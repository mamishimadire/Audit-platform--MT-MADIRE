"""
The file-upload connector: CSV / TSV / Excel files a user uploads to a data source.

Every current file is a table (an Excel sheet is a table each). A table is keyed by its NAME, so
uploading a newer copy of `payroll.csv` replaces the data behind the same table and every mapping made
against it keeps working; the old version stays on record with its sha256.
"""
from __future__ import annotations

import hashlib
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, defer

from app.models.data_source import DataConnection, DataFile
from app.schemas.data_source import DiscoveredEntity, DiscoveredField
from app.services.connectors import ConnectorError, EntityNotHere, register
from app.services.connectors.file_parsing import FileParseError, ParsedTable, file_kind, parse_file, table_stem

MAX_VERSIONS_PER_FILE = 10
MAX_FILES_PER_CONNECTION = 50  # every current file is parsed for discovery, so this bounds that work


def _session() -> Session:
    from app.db.session import SessionLocal

    return SessionLocal()


def current_files(db: Session, connection_id: uuid.UUID) -> list[DataFile]:
    """The current version of each file, WITHOUT its bytes: a scheduled run asks this every time, and
    pulling up to 25 MB per file over the network to then find the parse cache already has it is waste.
    tables_for() fetches the bytes only when it has to parse."""
    return list(
        db.scalars(
            select(DataFile)
            .options(defer(DataFile.file_data))
            .where(DataFile.connection_id == connection_id, DataFile.is_current)
            .order_by(DataFile.file_name)
        )
    )


_PARSE_CACHE: "OrderedDict[tuple[uuid.UUID, str], tuple[ParsedTable, ...]]" = OrderedDict()
_PARSE_CACHE_MAX = 2  # parsed tables are large; two is enough for discovery + profiling + a scheduled run
# ...and they are bounded by SIZE, not just count: at ~70 bytes a cell, 750,000 cells is ~50 MB.
_PARSE_CACHE_MAX_CELLS = 750_000
_parse_cache_lock = threading.Lock()


def _cells(tables: tuple[ParsedTable, ...]) -> int:
    return sum(len(t.rows) * len(t.headers) for t in tables)


def _cache_put(file_id: uuid.UUID, sha256: str, tables: tuple[ParsedTable, ...]) -> None:
    with _parse_cache_lock:
        _PARSE_CACHE[(file_id, sha256)] = tables
        # Oldest out until it fits (the newest is always kept, even if it alone is over budget).
        while len(_PARSE_CACHE) > 1 and (len(_PARSE_CACHE) > _PARSE_CACHE_MAX or sum(_cells(v) for v in _PARSE_CACHE.values()) > _PARSE_CACHE_MAX_CELLS):
            _PARSE_CACHE.popitem(last=False)


def tables_for(db: Session, file: DataFile) -> tuple[ParsedTable, ...]:
    """Parsing is the expensive part and a version's bytes never change, so a file read for discovery,
    profiling and several scheduled tests in a row is parsed once. Keyed by (version, content hash),
    never by the bytes themselves, and bounded: three parsed files at most."""
    key = (file.file_id, file.sha256)
    with _parse_cache_lock:
        cached = _PARSE_CACHE.get(key)
        if cached is not None:
            _PARSE_CACHE.move_to_end(key)
            return cached
    data = db.scalar(select(DataFile.file_data).where(DataFile.file_id == file.file_id))
    if data is None:
        raise ConnectorError(f"The file '{file.file_name}' is no longer stored.")
    tables = tuple(parse_file(file.file_name, bytes(data)))
    _cache_put(file.file_id, file.sha256, tables)
    return tables


def store_upload(
    db: Session, *, connection: DataConnection, file_name: str, content_type: str | None, data: bytes, uploaded_by: uuid.UUID | None
) -> DataFile:
    """Validates the file by actually parsing it (a file that cannot be read as a table is refused
    here, not discovered later), then records it as the new current version of that file name."""
    file_kind(file_name)  # raises FileParseError for anything that is not a table file
    tables = parse_file(file_name, data)
    if not any(t.headers for t in tables):
        raise FileParseError("The file has no header row.")
    name = file_name.replace("\\", "/").split("/")[-1][:255]
    known_names = set(db.scalars(select(DataFile.file_name).where(DataFile.connection_id == connection.connection_id, DataFile.is_current)))
    if name not in known_names and len(known_names) >= MAX_FILES_PER_CONNECTION:
        raise FileParseError(f"A connection can hold at most {MAX_FILES_PER_CONNECTION} files. Remove one first, or use another connection.")
    db.execute(
        update(DataFile).where(DataFile.connection_id == connection.connection_id, DataFile.file_name == name, DataFile.is_current).values(is_current=False)
    )
    stored = DataFile(
        connection_id=connection.connection_id, file_name=name, content_type=content_type, sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data), file_data=data, is_current=True, uploaded_by=uploaded_by,
        uploaded_at=datetime.now(timezone.utc),  # not now(): inside one transaction that is a single instant, and "newest" must be well-defined
    )
    db.add(stored)
    db.flush()
    # Ids only: loading the old versions to delete them would pull up to ten 25 MB files into memory.
    version_ids = list(
        db.scalars(select(DataFile.file_id).where(DataFile.connection_id == connection.connection_id, DataFile.file_name == name).order_by(DataFile.uploaded_at.desc()))
    )
    if len(version_ids) > MAX_VERSIONS_PER_FILE:
        db.execute(delete(DataFile).where(DataFile.file_id.in_(version_ids[MAX_VERSIONS_PER_FILE:])))
    # The file was just parsed to validate it: keep that, rather than fetching the bytes back to parse them again.
    _cache_put(stored.file_id, stored.sha256, tuple(tables))
    return stored


def list_versions(db: Session, connection_id: uuid.UUID) -> list[DataFile]:
    """Every stored version, newest first within each file name. The bytes are not loaded."""
    return list(
        db.scalars(
            select(DataFile)
            .options(defer(DataFile.file_data))
            .where(DataFile.connection_id == connection_id)
            .order_by(DataFile.file_name, DataFile.uploaded_at.desc())
        )
    )


def delete_version(db: Session, *, connection_id: uuid.UUID, file_id: uuid.UUID) -> bool:
    """Removes one version. If it was the current one, the newest remaining version of the same file
    name takes over, so a table never silently disappears because its latest upload was withdrawn."""
    target = db.execute(
        select(DataFile.is_current, DataFile.file_name).where(DataFile.file_id == file_id, DataFile.connection_id == connection_id)
    ).one_or_none()
    if target is None:
        return False
    was_current, name = target
    db.execute(delete(DataFile).where(DataFile.file_id == file_id, DataFile.connection_id == connection_id))
    if was_current:
        newest_id = db.scalar(
            select(DataFile.file_id).where(DataFile.connection_id == connection_id, DataFile.file_name == name).order_by(DataFile.uploaded_at.desc()).limit(1)
        )
        if newest_id is not None:
            db.execute(update(DataFile).where(DataFile.file_id == newest_id).values(is_current=True))
    return True


def truncation_notices(tables: tuple[ParsedTable, ...]) -> list[str]:
    """Plain-language notes for tables that have more rows than the platform reads (information, not a problem)."""
    from app.services.connectors.file_parsing import MAX_CELLS

    notices = []
    for t in tables:
        if not t.truncated:
            continue
        if not t.rows:
            notices.append(
                f"The rows of '{t.name}' were not read: the other sheets of this file already used the platform's limit of {MAX_CELLS:,} cells "
                "(rows x columns) for one file."
            )
        else:
            notices.append(
                f"Only the first {len(t.rows):,} rows of '{t.name}' are used: the platform reads at most {MAX_CELLS:,} cells (rows x columns) "
                "of a file, and each control test looks at the first 5,000 rows of a table."
            )
    return notices


def schema_changes(before: tuple[ParsedTable, ...], after: tuple[ParsedTable, ...]) -> list[str]:
    """What a new version of a file breaks for anything already mapped to it: a table or column that is
    gone, or a column whose type changed. Additions are harmless and not reported."""
    changes: list[str] = []
    new_by_name = {t.name: t for t in after}
    for old in before:
        new = new_by_name.get(old.name)
        if new is None:
            changes.append(f"The table '{old.name}' is no longer in the file.")
            continue
        gone = [h for h in old.headers if h not in new.headers]
        if gone:
            changes.append(f"'{old.name}' no longer has the column(s): {', '.join(gone)}.")
        retyped = [h for h in old.headers if h in new.headers and old.types.get(h) != new.types.get(h)]
        if retyped:
            changes.append(f"'{old.name}': the type of {', '.join(retyped)} changed.")
    return changes


def _named_tables(db: Session, connection_id: uuid.UUID, *, for_entity: str | None = None) -> list[tuple[str, DataFile, ParsedTable]]:
    """(table name, its file, the parsed table) for every current file. Two files that yield the same table
    name keep both, numbered in file-name order, so a name means one table every time it is asked for.
    With `for_entity`, only files whose name could produce that table are parsed (every table name starts
    with its file's stem), so fetching one table does not re-parse every other file."""
    named: list[tuple[str, DataFile, ParsedTable]] = []
    used: set[str] = set()
    for file in current_files(db, connection_id):
        if for_entity is not None and not for_entity.startswith(table_stem(file.file_name)[:140]):
            continue
        for table in tables_for(db, file):
            name, n = table.name[:150], 2
            while name.lower() in used:
                name, n = f"{table.name[:140]} ({n})", n + 1
            used.add(name.lower())
            named.append((name, file, table))
    return named


class FileUploadConnector:
    def test(self, connection: DataConnection) -> tuple[bool, str]:
        db = _session()
        try:
            files = current_files(db, connection.connection_id)
        finally:
            db.close()
        if not files:
            return False, "No file has been uploaded to this connection yet."
        return True, f"{len(files)} file(s) available."

    def discover(self, connection: DataConnection) -> list[DiscoveredEntity]:
        db = _session()
        try:
            entities: list[DiscoveredEntity] = []
            for name, file, table in _named_tables(db, connection.connection_id):
                note = f" First {len(table.rows)} rows only." if table.truncated else ""
                entities.append(
                    DiscoveredEntity(
                        entity_name=name, entity_type="file",
                        description=f"From {file.file_name} (sha256 {file.sha256[:12]}…, {len(table.rows)} rows).{note}",
                        fields=[DiscoveredField(field_name=h, data_type=table.types.get(h, "string")) for h in table.headers],
                    )
                )
            return entities
        finally:
            db.close()

    def fetch_records(self, connection: DataConnection, *, entity_name: str, field_names: list[str], limit: int) -> list[dict]:
        db = _session()
        try:
            for name, _file, table in _named_tables(db, connection.connection_id, for_entity=entity_name):
                if name == entity_name:
                    return [{f: row.get(f) for f in field_names if f in row} for row in table.rows[:limit]]
        finally:
            db.close()
        raise EntityNotHere(f"The table '{entity_name}' is not in the uploaded files.")

    def truncated(self, connection: DataConnection, entity_name: str) -> bool:
        """True when this table has more rows than the platform reads (see file_parsing.MAX_CELLS)."""
        db = _session()
        try:
            for name, _file, table in _named_tables(db, connection.connection_id, for_entity=entity_name):
                if name == entity_name:
                    return table.truncated
        finally:
            db.close()
        return False


register("file_upload", FileUploadConnector())
