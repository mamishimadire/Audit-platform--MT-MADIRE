"""
The two PUBLIC download endpoints (Gateway and Endpoint Agent). Each Windows download is a compiled executable of
tens of megabytes, and nobody has to log in to ask for it, so it must be streamed from disk: reading it whole into
memory per request would let a few concurrent (or hostile) requests exhaust a small server. No database needed.
"""
import io
import tarfile

import pytest
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.api.v1.routes import devices as devices_routes
from app.api.v1.routes import gateways as gateway_routes
from app.main import app
from app.services import endpoint_agent_download_service as agent_service
from app.services import gateway_download_service as gateway_service

client = TestClient(app, client=("127.0.0.1", 50000))
PAYLOAD = (bytes(range(256)) * 4096) * 3  # 3 MB with a pattern, so a truncated or shifted body would be noticed


@pytest.fixture
def fake_exes(tmp_path, monkeypatch):
    gateway_exe, agent_exe = tmp_path / "MadireGateway.exe", tmp_path / "MadireEndpointAgent.exe"
    gateway_exe.write_bytes(b"GW" + PAYLOAD)
    agent_exe.write_bytes(b"EA" + PAYLOAD)
    monkeypatch.setattr(gateway_service, "WINDOWS_EXE_PATH", gateway_exe)
    monkeypatch.setattr(agent_service, "WINDOWS_EXE_PATH", agent_exe)
    return gateway_exe, agent_exe


@pytest.mark.parametrize(
    "url, prefix, filename",
    [("/api/v1/gateways/download/windows", b"GW", "MadireGateway.exe"), ("/api/v1/endpoint-agent/download/windows", b"EA", "MadireEndpointAgent.exe")],
)
def test_a_windows_download_delivers_the_exact_file_with_the_right_headers(fake_exes, url, prefix, filename):
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == prefix + PAYLOAD  # every byte, in order
    assert response.headers["content-length"] == str(len(prefix + PAYLOAD))
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert response.headers["content-type"].startswith("application/vnd.microsoft.portable-executable")


def test_the_executables_are_streamed_from_disk_and_the_in_memory_builders_are_not_used_for_them(fake_exes, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("an executable must not be assembled in memory")

    monkeypatch.setattr(gateway_routes, "build_gateway_archive", forbidden)
    assert isinstance(gateway_routes.download("windows"), FileResponse)
    assert isinstance(devices_routes.download_endpoint_agent("windows"), FileResponse)
    with pytest.raises(AssertionError):  # ...while the small Linux tarball still goes through the builder
        gateway_routes.download("linux")


def test_the_service_hands_back_a_path_not_the_bytes(fake_exes):
    gateway_exe, agent_exe = fake_exes
    assert gateway_service.windows_exe()[0] == gateway_exe
    assert agent_service.windows_exe("windows")[0] == agent_exe
    with pytest.raises(ValueError):
        gateway_service.build_gateway_archive("windows")  # only the Linux source archive is built in memory now


def test_a_missing_executable_says_try_again_shortly_and_never_a_stack_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_service, "WINDOWS_EXE_PATH", tmp_path / "absent.exe")
    monkeypatch.setattr(agent_service, "WINDOWS_EXE_PATH", tmp_path / "absent2.exe")
    for url in ("/api/v1/gateways/download/windows", "/api/v1/endpoint-agent/download/windows"):
        response = client.get(url)
        assert response.status_code == 503 and "try again shortly" in response.json()["detail"]
        assert "absent" not in response.text  # not the server's file path


@pytest.mark.parametrize("url", ["/api/v1/gateways/download/macos", "/api/v1/gateways/download/..%2f..%2fetc%2fpasswd", "/api/v1/endpoint-agent/download/linux", "/api/v1/endpoint-agent/download/macos"])
def test_an_unsupported_platform_is_refused_and_never_reaches_a_file(fake_exes, url):
    assert client.get(url).status_code in (400, 404)


def test_the_linux_gateway_download_is_still_the_source_tarball_without_the_executable():
    response = client.get("/api/v1/gateways/download/linux")
    assert response.status_code == 200 and response.headers["content-disposition"].endswith('audit-data-gateway-linux.tar.gz"')
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        names = archive.getnames()
    assert any(n.endswith("gateway/connectors/oracle.py") for n in names)  # this build's new connectors ship in the source package too
    assert not any(n.endswith(".exe") or "/dist/" in n or "/.venv/" in n for n in names)
