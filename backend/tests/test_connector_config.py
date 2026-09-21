"""What a user may put in a file / SFTP connection's settings, and where each part is stored. Pure: no database."""
import json

import pytest

from app.services.connectors import config as cfg
from app.services.connectors.config import ConfigError, prepare

GOOD = {"host": "sftp.acme.example", "username": "audit", "remote_path": "/exports/payroll_*.csv", "auth": "password"}


def sftp(**changes):
    config = {**GOOD, **{k: v for k, v in changes.items() if k != "secrets"}}
    secrets = changes.get("secrets", {"password": "pw"})
    return prepare("sftp", {k: v for k, v in config.items() if v is not None}, secrets)


def test_a_valid_sftp_connection_is_split_into_columns_config_and_secrets():
    p = sftp(port=2222)
    assert (p.host, p.port, p.username) == ("sftp.acme.example", 2222, "audit")
    assert p.config == {"remote_path": "/exports/payroll_*.csv", "auth": "password"}  # host/user live in columns, not repeated
    assert p.secrets == {"password": "pw"}
    assert sftp().port == 22


@pytest.mark.parametrize(
    "host",
    ["https://sftp.acme.example", "sftp.acme.example:22", "sftp.acme.example/path", "acme example.com", "user@sftp.acme.example", "", "-bad.example.com", "a" * 300 + ".com"],
)
def test_a_host_must_be_a_bare_name_or_address(host):
    with pytest.raises(ConfigError):
        sftp(host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.1", "169.254.169.254", "::1", "fd00:ec2::254", "[::1]", "100.64.0.1"])
def test_a_private_or_internal_address_is_refused_at_creation(host):
    with pytest.raises(ConfigError, match="public internet"):
        sftp(host=host)


def test_a_public_address_and_a_dotted_name_are_accepted():
    assert sftp(host="93.184.216.34").host == "93.184.216.34"
    assert sftp(host="SFTP.Acme.Example.").host == "sftp.acme.example"  # case and a trailing dot are normalised


@pytest.mark.parametrize("port", [0, 65536, -1, "22", True, 22.5])
def test_the_port_must_be_a_real_port_number(port):
    with pytest.raises(ConfigError, match="Port"):
        sftp(port=port)


def test_required_settings_are_required():
    for missing in ("username", "remote_path"):
        with pytest.raises(ConfigError, match="required"):
            prepare("sftp", {k: v for k, v in GOOD.items() if k != missing}, {"password": "pw"})


def test_an_unknown_setting_is_refused_not_stored():
    with pytest.raises(ConfigError, match="unknown setting"):
        prepare("sftp", {**GOOD, "password": "smuggled"}, {"password": "pw"})  # a credential in config would be returned by the API
    with pytest.raises(ConfigError, match="unknown setting"):
        prepare("sftp", GOOD, {"password": "pw", "api_key": "x"})


def test_credentials_must_match_the_authentication_method():
    with pytest.raises(ConfigError, match="password is required"):
        sftp(secrets={})
    with pytest.raises(ConfigError, match="private key was supplied"):
        sftp(secrets={"password": "pw", "private_key": "-----BEGIN PRIVATE KEY-----"})
    with pytest.raises(ConfigError, match="PEM private key"):
        sftp(auth="private_key", secrets={"private_key": "not a key"})
    with pytest.raises(ConfigError, match="password was supplied"):
        sftp(auth="private_key", secrets={"private_key": "-----BEGIN OPENSSH PRIVATE KEY-----", "password": "x"})
    ok = sftp(auth="private_key", secrets={"private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc", "passphrase": "pp"})
    assert set(ok.secrets) == {"private_key", "passphrase"}


def test_an_oversized_secret_is_refused():
    with pytest.raises(ConfigError, match="too long"):
        sftp(secrets={"password": "x" * (cfg.MAX_SECRET_CHARS + 1)})


def test_a_host_key_fingerprint_must_be_in_the_form_ssh_prints():
    good = "SHA256:" + "A" * 43
    assert sftp(host_key_sha256=good).config["host_key_sha256"] == good
    for bad in ("A" * 43, "SHA256:short", "MD5:aa:bb", "SHA256:" + "A" * 44, 12):
        with pytest.raises(ConfigError, match="fingerprint"):
            sftp(host_key_sha256=bad)


@pytest.mark.parametrize("value", ["line\nbreak", "nul\x00byte", "x" * 600])
def test_control_characters_and_absurd_lengths_are_refused_in_paths(value):
    with pytest.raises(ConfigError):
        sftp(remote_path=value)


def test_a_file_connection_takes_nothing_and_unbuilt_families_are_refused():
    assert prepare("file_upload", {}, {}).config is None
    with pytest.raises(ConfigError):
        prepare("file_upload", {"a": 1}, {})
    with pytest.raises(ConfigError):
        prepare("file_upload", {}, {"password": "x"})
    with pytest.raises(ConfigError, match="not available"):
        prepare("something_else", {}, {})


def test_oversized_settings_are_refused():
    with pytest.raises(ConfigError, match="too large"):
        prepare("sftp", {**GOOD, "table_name": "x" * (cfg.MAX_CONFIG_BYTES + 10)}, {"password": "pw"})


def test_secrets_are_one_encrypted_blob_that_never_shows_the_plaintext():
    secrets = {"password": "hunter2-plaintext", "passphrase": "another-secret"}
    blob = cfg.pack_secrets(secrets)
    assert "hunter2" not in blob and "another-secret" not in blob and "password" not in blob
    assert cfg.unpack_secrets(blob) == secrets
    assert cfg.pack_secrets({}) is None and cfg.unpack_secrets(None) == {} and cfg.unpack_secrets("") == {}
    assert json.loads(json.dumps(cfg.unpack_secrets(blob))) == secrets
