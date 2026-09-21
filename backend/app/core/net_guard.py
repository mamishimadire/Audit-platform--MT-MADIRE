"""
Outbound-reach policy for everything the PLATFORM connects to on a user's behalf
(REST/SOAP APIs, SFTP servers).

The cloud platform runs inside a network of its own. If a user can type any address into a connection
form, they can make the platform call things only the platform can reach: its own loopback, the cloud
provider's metadata service (169.254.169.254, which hands out credentials), or other services on its
private network. That is server-side request forgery. Policy, decided with the product owner: the
platform reaches the PUBLIC internet only. A server inside a client's own network is reached through
the Gateway, which runs inside that network, never from the cloud.

How it is enforced:
  * the host name is resolved HERE, every address it resolves to (IPv4 and IPv6) must be public, and
    the request is then made to that vetted address, not to the name, so the address cannot change
    between the check and the connection (DNS rebinding);
  * whatever spelling was typed (`localhost`, `2130706433`, `0x7f.1`, `[::ffff:127.0.0.1]`, a name that
    resolves to a private address) is judged by what it RESOLVES to, never by how it looks;
  * "public" is Python's `ipaddress.is_global`: private, loopback, link-local (incl. the metadata
    address), carrier-grade NAT, documentation, reserved and unspecified ranges are all refused, and
    IPv4 addresses tunnelled inside IPv6 (mapped, NAT64, 6to4) are judged as the IPv4 they carry;
  * HTTPS only, redirects are never followed (a public server must not bounce the platform inward),
    hard time limits, and a response size cap.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
# The read timeout is per read, so a server that sends one byte every 29 seconds would never trip it. This is the
# ceiling on the WHOLE answer, which the platform's own scheduler loop is waiting on.
TOTAL_TIMEOUT_SECONDS = 60.0
_DEFAULT_PORT = {"https": 443}


class UnsafeDestination(ValueError):
    """The address is not one the platform may connect to. The message is safe to show the user."""


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:  # ::ffff:a.b.c.d
        return ip.ipv4_mapped
    packed = ip.packed
    if packed[:12] == bytes.fromhex("0064ff9b0000000000000000"):  # NAT64 64:ff9b::/96
        return ipaddress.IPv4Address(packed[12:])
    if packed[:2] == bytes.fromhex("2002"):  # 6to4 2002:AABB:CCDD::/48 carries an IPv4
        return ipaddress.IPv4Address(packed[2:6])
    return None


def is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_ipv4(ip)
        if inner is not None:
            return is_public_ip(inner)
    return ip.is_global and not ip.is_multicast


def _resolver(host: str, port: int):
    """The one place a name becomes addresses (a test can substitute it to simulate DNS)."""
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def resolve_public(host: str, port: int = 443) -> list[str]:
    """Every address `host` resolves to, IF all of them are public. Raises UnsafeDestination otherwise.
    A name with even one non-public address is refused (an attacker-controlled name can list a public
    address alongside 127.0.0.1 and hope the client picks the wrong one)."""
    host = (host or "").strip().strip("[]")
    if not host or any(c in host for c in " /\\@\x00") or len(host) > 253:
        raise UnsafeDestination("That is not a valid host name.")
    try:
        infos = _resolver(host, port)
    except socket.gaierror as exc:
        raise UnsafeDestination("That host name could not be resolved.") from exc
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if not is_public_ip(ip):
            raise UnsafeDestination(
                "That address is on a private, internal or otherwise non-public network. The platform only connects "
                "to the public internet; reach servers inside your network through a Gateway instead."
            )
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise UnsafeDestination("That host name did not resolve to any address.")
    return addresses


@dataclass(frozen=True)
class SafeResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


def _default_verify() -> ssl.SSLContext:
    return ssl.create_default_context()


def safe_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    content: bytes | None = None,
    json_body=None,
    max_bytes: int = MAX_RESPONSE_BYTES,
    _verify: ssl.SSLContext | bool | None = None,  # tests only; never reachable from a connection config
) -> SafeResponse:
    """One HTTPS request under the reach policy. The connection is made to the vetted IP, with the
    original host name kept for TLS (SNI + certificate check) and the Host header, so pinning the
    address does not weaken certificate validation."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeDestination("Only https:// addresses are allowed.")
    if parts.username or parts.password:
        raise UnsafeDestination("Credentials must not be embedded in the address.")
    host = parts.hostname or ""
    port = parts.port or _DEFAULT_PORT["https"]
    ip = resolve_public(host, port)[0]
    netloc_ip = f"[{ip}]" if ":" in ip else ip
    target = f"https://{netloc_ip}:{port}{parts.path or '/'}"
    if parts.query:
        target += f"?{parts.query}"

    request_headers = {**(headers or {}), "Host": host if port == 443 else f"{host}:{port}"}
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS, write=READ_TIMEOUT_SECONDS, pool=CONNECT_TIMEOUT_SECONDS)
    verify = _default_verify() if _verify is None else _verify
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, verify=verify, trust_env=False) as client:
            with client.stream(
                method, target, headers=request_headers, params=params, content=content, json=json_body,
                extensions={"sni_hostname": host},
            ) as response:
                if 300 <= response.status_code < 400:
                    raise UnsafeDestination("The server tried to redirect the request; redirects are not followed.")
                chunks: list[bytes] = []
                total = 0
                deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise UnsafeDestination("The server took too long to send its answer.")
                    total += len(chunk)
                    if total > max_bytes:
                        raise UnsafeDestination(f"The response is larger than the {max_bytes // (1024 * 1024)} MB limit.")
                    chunks.append(chunk)
                return SafeResponse(response.status_code, dict(response.headers), b"".join(chunks))
    except httpx.TimeoutException as exc:
        raise UnsafeDestination("The server took too long to respond.") from exc
    except httpx.HTTPError as exc:
        # The driver's text can include the resolved address; keep it out of what the user sees.
        raise UnsafeDestination("Could not connect to that server over HTTPS (check the address and its certificate).") from exc
