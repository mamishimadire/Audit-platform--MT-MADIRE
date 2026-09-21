"""The outbound-reach policy: what the platform may and may not connect to. No database needed."""
import datetime
import ipaddress
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.core import net_guard
from app.core.net_guard import UnsafeDestination, is_public_ip, resolve_public, safe_request


def _addr(*ips):
    """A stub for DNS: whatever the name, resolve to exactly these addresses."""
    def fake(host, port):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return fake


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "127.255.255.254", "10.0.0.1", "172.16.5.4", "172.31.255.255", "192.168.1.1", "169.254.169.254",  # incl. cloud metadata
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "192.0.2.1", "198.18.0.1", "255.255.255.255",
        "::1", "fe80::1", "fc00::1", "fd00:ec2::254", "::", "ff02::1",
        "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",  # IPv4 tunnelled inside IPv6
        "64:ff9b::7f00:1", "2002:7f00:0001::1",  # NAT64 and 6to4 carrying 127.0.0.1
    ],
)
def test_non_public_addresses_are_refused(monkeypatch, ip):
    monkeypatch.setattr(net_guard, "_resolver", _addr(ip))
    with pytest.raises(UnsafeDestination, match="public internet"):
        resolve_public("anything.example.com")


@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_allowed(monkeypatch, ip):
    monkeypatch.setattr(net_guard, "_resolver", _addr(ip))
    assert resolve_public("api.example.com") == [str(ipaddress.ip_address(ip))]


def test_one_bad_address_among_good_ones_refuses_the_whole_name(monkeypatch):
    """A hostile name can list a public address next to 127.0.0.1 and hope the client picks the wrong one."""
    monkeypatch.setattr(net_guard, "_resolver", _addr("8.8.8.8", "127.0.0.1"))
    with pytest.raises(UnsafeDestination):
        resolve_public("rebind.example.com")


@pytest.mark.parametrize("name", ["localhost", "2130706433", "0x7f.1", "0177.0.0.1", "127.1", "[::1]", "metadata.google.internal"])
def test_however_loopback_is_spelled_it_is_judged_by_what_it_resolves_to(name):
    """No stubbing: the real resolver turns these into loopback/metadata addresses (or fails to resolve)."""
    with pytest.raises(UnsafeDestination):
        resolve_public(name)


@pytest.mark.parametrize("bad", ["", "  ", "a b", "evil.com/../x", "user@host", "a\\b", "x" * 300, "nul\x00byte"])
def test_malformed_hosts_are_refused_before_any_lookup(monkeypatch, bad):
    monkeypatch.setattr(net_guard, "_resolver", lambda *a: pytest.fail("must not resolve a malformed host"))
    with pytest.raises(UnsafeDestination):
        resolve_public(bad)


def test_unresolvable_names_are_refused_with_a_safe_message(monkeypatch):
    def nxdomain(host, port):
        raise socket.gaierror("Name or service not known: internal-secret-host")

    monkeypatch.setattr(net_guard, "_resolver", nxdomain)
    with pytest.raises(UnsafeDestination) as info:
        resolve_public("nope.example.com")
    assert "internal-secret-host" not in str(info.value)


@pytest.mark.parametrize("url", ["http://api.example.com/x", "ftp://api.example.com/x", "file:///etc/passwd", "gopher://x/", "//api.example.com/x"])
def test_only_https_is_allowed(url):
    with pytest.raises(UnsafeDestination, match="https"):
        safe_request("GET", url)


def test_credentials_embedded_in_the_address_are_refused():
    with pytest.raises(UnsafeDestination, match="Credentials"):
        safe_request("GET", "https://user:pass@api.example.com/")


def test_a_private_target_is_refused_before_any_connection_is_attempted(monkeypatch):
    monkeypatch.setattr(net_guard, "_resolver", _addr("169.254.169.254"))
    monkeypatch.setattr(net_guard.httpx, "Client", lambda *a, **k: pytest.fail("no connection may be attempted"))
    with pytest.raises(UnsafeDestination):
        safe_request("GET", "https://metadata.example.com/latest/meta-data/")


# ---- real sockets: a local HTTPS server with a throwaway certificate --------------------------------------
def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "api.example.test")])
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("api.example.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


@pytest.fixture
def local_https(tmp_path, monkeypatch):
    cert, key = _self_signed(tmp_path)
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen["host"] = self.headers.get("Host")
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "https://169.254.169.254/latest/meta-data/")
                self.end_headers()
                return
            body = b"x" * 50_000 if self.path.startswith("/big") else b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client_ctx = ssl.create_default_context(cafile=str(cert))
    # The test may reach 127.0.0.1 (the policy itself is what the tests above prove); nothing else changes.
    monkeypatch.setattr(net_guard, "is_public_ip", lambda ip: True)
    monkeypatch.setattr(net_guard, "_resolver", _addr("127.0.0.1"))
    yield {"port": server.server_address[1], "ctx": client_ctx, "seen": seen}
    server.shutdown()


def test_a_request_connects_to_the_vetted_ip_but_keeps_the_host_name_for_tls_and_host_header(local_https):
    r = safe_request("GET", f"https://api.example.test:{local_https['port']}/v1/things", _verify=local_https["ctx"])
    assert r.status_code == 200 and r.body == b'{"ok": true}'
    assert local_https["seen"]["host"] == f"api.example.test:{local_https['port']}"  # certificate checked against the NAME


def test_a_certificate_for_another_name_is_rejected(local_https):
    with pytest.raises(UnsafeDestination, match="certificate"):
        safe_request("GET", f"https://wrong-name.example.test:{local_https['port']}/", _verify=local_https["ctx"])


def test_redirects_are_never_followed(local_https):
    with pytest.raises(UnsafeDestination, match="redirect"):
        safe_request("GET", f"https://api.example.test:{local_https['port']}/redirect", _verify=local_https["ctx"])


def test_an_oversized_response_is_cut_off(local_https):
    with pytest.raises(UnsafeDestination, match="larger than"):
        safe_request("GET", f"https://api.example.test:{local_https['port']}/big", max_bytes=10_000, _verify=local_https["ctx"])
    ok = safe_request("GET", f"https://api.example.test:{local_https['port']}/big", max_bytes=100_000, _verify=local_https["ctx"])
    assert len(ok.body) == 50_000


def test_is_public_ip_matches_the_documented_policy():
    assert is_public_ip(ipaddress.ip_address("8.8.8.8")) and not is_public_ip(ipaddress.ip_address("10.1.2.3"))
