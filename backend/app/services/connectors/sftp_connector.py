"""
The SFTP connection: the newest file matching a path or pattern on a client's SFTP server, read as a table.

A connection is ONE remote path: a file (`/exports/payroll.csv`) or a pattern in the last segment
(`/exports/payroll_*.csv`, newest by modification time wins). The table's name is stable across drops
(default: the pattern with its wildcards and extension removed, or the configured `table_name`), so a
new monthly file replaces the data behind the same table and every mapping made against it keeps working.

What makes this safe to point at an address a user typed:
  * the host is resolved by net_guard and EVERY address must be public; the connection is made to that
    vetted address, so a name that changes its answer between check and connect cannot redirect it
  * the server's host key is pinned: the first successful TEST records its SHA-256 fingerprint (or the
    user supplies one up front) and any later connection to a different key is refused
  * only the connection's own credentials are ever sent, downloads are size-capped, and nothing raw from
    paramiko or the socket layer (which can echo addresses) is passed on to the user
"""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
import io
import posixpath
import re
import socket
import stat as stat_mode
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass

import paramiko

from app.core.net_guard import CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS, UnsafeDestination, resolve_public
from app.models.data_source import DataConnection
from app.schemas.data_source import DiscoveredEntity, DiscoveredField
from app.services.connectors import ConnectorError, EntityNotHere, register
from app.services.connectors.config import unpack_secrets
from app.services.connectors.file_parsing import MAX_FILE_BYTES, FileParseError, ParsedTable, parse_file, table_stem

_HANDSHAKE_TIMEOUT_SECONDS = 15
_SNAPSHOT_TTL_SECONDS = 60  # the file is looked at again at most this often; scheduled runs in between reuse it
_SNAPSHOT_MAX = 3
_SNAPSHOT_MAX_CELLS = 750_000  # ~50 MB of parsed tables at ~70 bytes a cell; the newest is always kept
_MAX_LISTED_ENTRIES = 20_000
_DOWNLOAD_CHUNK_BYTES = 256 * 1024
_DOWNLOAD_DEADLINE_SECONDS = 120  # the scheduler loop that asked is waiting; a slow drip must not hold it indefinitely
_GLOB_CHARS = "*?["


@dataclass
class _Snapshot:
    signature: tuple
    path: str
    mtime: int
    size: int
    sha256: str
    file_name: str
    tables: tuple[ParsedTable, ...]
    fetched_at: float


_SNAPSHOTS: "OrderedDict[uuid.UUID, _Snapshot]" = OrderedDict()
_snapshot_lock = threading.Lock()


def fingerprint(key: paramiko.PKey) -> str:
    """The server key's identity in the form `ssh-keygen -lf` prints, so a user can compare it."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def _load_private_key(pem: str, passphrase: str | None) -> paramiko.PKey:
    needs_passphrase = False
    for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return key_class.from_private_key(io.StringIO(pem), password=passphrase or None)
        except paramiko.PasswordRequiredException:
            needs_passphrase = True
        except (paramiko.SSHException, ValueError):
            continue
    if needs_passphrase and not passphrase:
        raise ConnectorError("The private key is protected by a passphrase, which was not supplied.")
    raise ConnectorError("The private key could not be read: it is an unsupported type, damaged, or the passphrase is wrong.")


def _dial(host: str, port: int) -> socket.socket:
    try:
        addresses = resolve_public(host, port)
    except UnsafeDestination as exc:
        raise ConnectorError(str(exc)) from exc
    for address in addresses:  # already vetted: the socket is opened to the address, never to the name again
        try:
            return socket.create_connection((address, port), timeout=CONNECT_TIMEOUT_SECONDS)
        except OSError:
            continue
    raise ConnectorError("Could not connect to the server — check the host and port, and that the server is reachable from the internet.")


@contextmanager
def _open_sftp(connection: DataConnection, *, may_pin: bool):
    """Yields (sftp client, the server's fingerprint). Every way this can go wrong ends as a ConnectorError
    whose text is safe to show; the transport is always closed."""
    config = connection.connector_config or {}
    secrets = unpack_secrets(connection.encrypted_password)
    sock = _dial(connection.host or "", connection.port or 22)
    sock.settimeout(READ_TIMEOUT_SECONDS)
    transport = paramiko.Transport(sock)
    transport.banner_timeout = _HANDSHAKE_TIMEOUT_SECONDS
    transport.auth_timeout = _HANDSHAKE_TIMEOUT_SECONDS
    sftp = None
    try:
        transport.start_client(timeout=_HANDSHAKE_TIMEOUT_SECONDS)
        seen = fingerprint(transport.get_remote_server_key())
        pinned = config.get("host_key_sha256")
        if pinned:
            if not hmac.compare_digest(pinned, seen):
                raise ConnectorError(
                    "The server's host key is not the one recorded for this connection. Either the server was re-keyed or "
                    "something is impersonating it. Confirm the change with the server's owner before updating the recorded key."
                )
        elif not may_pin:
            raise ConnectorError("Test this connection first, so the server's host key can be recorded.")
        if config.get("auth") == "private_key":
            transport.auth_publickey(connection.username or "", _load_private_key(secrets.get("private_key", ""), secrets.get("passphrase")))
        else:
            transport.auth_password(connection.username or "", secrets.get("password", ""))
        if not transport.is_authenticated():
            raise ConnectorError("The server rejected the username or credentials.")
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise ConnectorError("The server does not offer SFTP.")
        yield sftp, seen
    except ConnectorError:
        raise
    except paramiko.AuthenticationException as exc:
        raise ConnectorError("The server rejected the username or credentials.") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ConnectorError("The server did not respond in time.") from exc
    except (paramiko.SSHException, EOFError, OSError) as exc:
        raise ConnectorError("The SFTP session failed — the server may not support SFTP or closed the connection.") from exc
    finally:
        try:
            if sftp is not None:
                sftp.close()
        finally:
            transport.close()


def _pick_file(sftp: paramiko.SFTPClient, remote_path: str) -> tuple[str, paramiko.SFTPAttributes]:
    directory, _, pattern = remote_path.rpartition("/")
    directory = directory or ("/" if remote_path.startswith("/") else ".")
    try:
        if any(ch in pattern for ch in _GLOB_CHARS):
            listing = sftp.listdir_attr(directory)
            if len(listing) > _MAX_LISTED_ENTRIES:
                raise ConnectorError(f"The folder holds more than {_MAX_LISTED_ENTRIES:,} entries; point the connection at a smaller folder.")
            matches = [a for a in listing if stat_mode.S_ISREG(a.st_mode or 0) and fnmatch.fnmatchcase(a.filename, pattern)]
            if not matches:
                raise ConnectorError(f"No file matching '{pattern}' was found in that folder.")
            newest = max(matches, key=lambda a: (a.st_mtime or 0, a.filename))
            return posixpath.join(directory, newest.filename), newest
        attrs = sftp.stat(remote_path)
    except IOError as exc:  # paramiko raises IOError/FileNotFoundError for a missing or unreadable path
        raise ConnectorError("The remote path could not be read — check that it exists and that the user may read it.") from exc
    if stat_mode.S_ISDIR(attrs.st_mode or 0):
        raise ConnectorError("The remote path is a folder. Give a file, or a pattern such as /exports/*.csv.")
    return remote_path, attrs


def _download(sftp: paramiko.SFTPClient, path: str, size: int) -> bytes:
    if size > MAX_FILE_BYTES:
        raise ConnectorError(f"The file is {size / (1024 * 1024):.0f} MB; the limit is {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE_SECONDS
    try:
        with sftp.open(path, "rb") as handle:
            while True:
                chunk = handle.read(_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FILE_BYTES:  # the stated size is only the server's word
                    raise ConnectorError(f"The file is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB limit.")
                if time.monotonic() > deadline:
                    raise ConnectorError("The download took too long; the server is too slow to fetch this file within the time allowed.")
                chunks.append(chunk)
    except IOError as exc:
        raise ConnectorError("The file could not be read — check that the user may read it.") from exc
    return b"".join(chunks)


def stable_table_name(config: dict) -> str:
    """The table's name, unchanged from one drop to the next: the configured name, else the last path
    segment without its extension and with wildcards turned into separators and trimmed away."""
    configured = (config.get("table_name") or "").strip()
    if configured:
        return configured[:150]
    stem = table_stem(posixpath.basename(config.get("remote_path") or ""))
    stem = re.sub(r"[*?\[\]]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_-. ")
    return (stem or "sftp_file")[:150]


def _rename(tables: tuple[ParsedTable, ...], file_name: str, stable: str) -> tuple[ParsedTable, ...]:
    stem = table_stem(file_name)
    renamed = []
    for table in tables:
        name = stable if table.name == stem else stable + table.name[len(stem):] if table.name.startswith(stem) else table.name
        renamed.append(ParsedTable(name=name[:150], headers=table.headers, rows=table.rows, types=table.types, truncated=table.truncated))
    return tuple(renamed)


def _signature(connection: DataConnection) -> tuple:
    config = connection.connector_config or {}
    return (connection.host, connection.port, connection.username, config.get("remote_path"), config.get("table_name"), config.get("auth"))


def _pin_host_key(connection_id: uuid.UUID, seen: str) -> None:
    """Records the fingerprint the first successful test saw. Its own session: the caller's transaction is not ours to commit."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(DataConnection, connection_id)
        if row is not None and not (row.connector_config or {}).get("host_key_sha256"):
            row.connector_config = {**(row.connector_config or {}), "host_key_sha256": seen}
            db.commit()
    finally:
        db.close()


def _snapshot(connection: DataConnection, *, may_pin: bool = False, force: bool = False) -> _Snapshot:
    signature = _signature(connection)
    with _snapshot_lock:
        cached = _SNAPSHOTS.get(connection.connection_id)
    if cached is not None and cached.signature == signature and not force and time.monotonic() - cached.fetched_at < _SNAPSHOT_TTL_SECONDS:
        return cached

    remote_path = (connection.connector_config or {}).get("remote_path") or ""
    with _open_sftp(connection, may_pin=may_pin) as (sftp, seen):
        path, attrs = _pick_file(sftp, remote_path)
        mtime, size = int(attrs.st_mtime or 0), int(attrs.st_size or 0)
        if cached is not None and cached.signature == signature and (cached.path, cached.mtime, cached.size) == (path, mtime, size):
            snapshot = cached  # the same file as last time: no download, no parse
            snapshot.fetched_at = time.monotonic()
        else:
            data = _download(sftp, path, size)
            file_name = posixpath.basename(path)
            try:
                tables = _rename(tuple(parse_file(file_name, data)), file_name, stable_table_name(connection.connector_config or {}))
            except FileParseError as exc:
                raise ConnectorError(f"The file '{file_name}' could not be read as a table: {exc}") from exc
            snapshot = _Snapshot(signature, path, mtime, size, hashlib.sha256(data).hexdigest(), file_name, tables, time.monotonic())
    if may_pin and not (connection.connector_config or {}).get("host_key_sha256"):
        _pin_host_key(connection.connection_id, seen)
    _remember(connection.connection_id, snapshot)
    return snapshot


def _remember(connection_id: uuid.UUID, snapshot: _Snapshot) -> None:
    """Keeps the snapshot, evicting the oldest until the cache fits its budget in COUNT and in SIZE (parsed cells).
    The newest is always kept, even if it alone is over budget."""
    with _snapshot_lock:
        _SNAPSHOTS[connection_id] = snapshot
        _SNAPSHOTS.move_to_end(connection_id)
        while len(_SNAPSHOTS) > 1 and (
            len(_SNAPSHOTS) > _SNAPSHOT_MAX or sum(len(t.rows) * len(t.headers) for v in _SNAPSHOTS.values() for t in v.tables) > _SNAPSHOT_MAX_CELLS
        ):
            _SNAPSHOTS.popitem(last=False)


def forget(connection_id: uuid.UUID) -> None:
    with _snapshot_lock:
        _SNAPSHOTS.pop(connection_id, None)


class SftpConnector:
    def test(self, connection: DataConnection) -> tuple[bool, str]:
        snapshot = _snapshot(connection, may_pin=True, force=True)
        rows = sum(len(t.rows) for t in snapshot.tables)
        cut = " Only the first rows are read: the file is larger than the platform reads." if any(t.truncated for t in snapshot.tables) else ""
        return True, f"Connected. Latest file: {snapshot.file_name} ({snapshot.size / 1024:.0f} KB, {rows} rows).{cut}"

    def discover(self, connection: DataConnection) -> list[DiscoveredEntity]:
        snapshot = _snapshot(connection, force=True)
        entities = []
        for table in snapshot.tables:
            note = f" First {len(table.rows)} rows only." if table.truncated else ""
            entities.append(
                DiscoveredEntity(
                    entity_name=table.name, entity_type="file",
                    description=f"Newest match on the SFTP server: {snapshot.file_name} (sha256 {snapshot.sha256[:12]}…, {len(table.rows)} rows).{note}",
                    fields=[DiscoveredField(field_name=h, data_type=table.types.get(h, "string")) for h in table.headers],
                )
            )
        return entities

    def fetch_records(self, connection: DataConnection, *, entity_name: str, field_names: list[str], limit: int) -> list[dict]:
        # Cheap to tell a table that is not this connection's from the name alone, before any network use.
        stable = stable_table_name(connection.connector_config or {})
        if entity_name != stable and not entity_name.startswith(stable + " / "):
            raise EntityNotHere(f"The table '{entity_name}' is not supplied by this SFTP connection.")
        snapshot = _snapshot(connection)
        for table in snapshot.tables:
            if table.name == entity_name:
                return [{f: row.get(f) for f in field_names if f in row} for row in table.rows[:limit]]
        raise EntityNotHere(f"The table '{entity_name}' is not in the latest file.")

    def truncated(self, connection: DataConnection, entity_name: str) -> bool:
        snapshot = _snapshot(connection)
        return any(t.truncated for t in snapshot.tables if t.name == entity_name)


register("sftp", SftpConnector())
