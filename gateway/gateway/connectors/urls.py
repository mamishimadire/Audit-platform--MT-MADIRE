"""
Connection URLs and driver arguments for the engines whose settings go beyond host / port / user / password:
Oracle, SAP HANA and Snowflake.

This file imports nothing from the Gateway (no pandas, no other module), on purpose. The platform connects to
the same three engines itself when a database is reachable from the internet (data_source_service.
_direct_engine_url / _direct_connect_args), and the two must build the same connection from the same
settings. The platform's own tests load THIS file by path and compare the two, so a change to one that is not
made to the other fails a test instead of surfacing at a client.

Every function takes any object with the ConnectorConfig attribute names (host, port, database, username,
password, schema, oracle_connection_type, sap_hana_encrypt, snowflake_*).
"""
from sqlalchemy.engine import URL

CONNECT_TIMEOUT_SECONDS = 8


def oracle_url(c) -> URL:
    # Oracle's Service Name is a connect-time query parameter, not a path segment; a SID takes the path like every
    # other engine's database name. URL.create (not an f-string) so '@', ':' or '%' in a password cannot break it.
    if (c.oracle_connection_type or "service_name") == "service_name":
        return URL.create(
            "oracle+oracledb", username=c.username, password=c.password, host=c.host, port=c.port, query={"service_name": c.database}
        )
    return URL.create("oracle+oracledb", username=c.username, password=c.password, host=c.host, port=c.port, database=c.database)


def hana_url(c) -> URL:
    # A tenant-database connection needs only host and port.
    return URL.create("hana+hdbcli", username=c.username, password=c.password, host=c.host, port=c.port)


def snowflake_url(c) -> URL:
    # `host` is the account identifier; the dialect resolves the real host name. snowflake-sqlalchemy splits
    # `database` on "/" into (database, schema). Key-pair authentication passes the key through connect args,
    # never the URL, so the password slot is left empty for that mode.
    query = {"warehouse": c.snowflake_warehouse}
    if c.snowflake_role:
        query["role"] = c.snowflake_role
    return URL.create(
        "snowflake", username=c.username, password=c.password if c.snowflake_auth_method == "password" else None,
        host=c.host, database=f"{c.database}/{c.schema}", query=query,
    )


def oracle_connect_args(c) -> dict:
    return {"tcp_connect_timeout": CONNECT_TIMEOUT_SECONDS}


def hana_connect_args(c) -> dict:
    # hdbcli's communicationTimeout is in MILLISECONDS, unlike every other driver here.
    return {
        "communicationTimeout": CONNECT_TIMEOUT_SECONDS * 1000,
        "encrypt": bool(c.sap_hana_encrypt),
        "sslValidateCertificate": bool(c.sap_hana_encrypt),
    }


def snowflake_connect_args(c) -> dict:
    args: dict = {"login_timeout": CONNECT_TIMEOUT_SECONDS}
    if c.snowflake_auth_method == "key_pair":
        from cryptography.hazmat.primitives import serialization

        passphrase = c.snowflake_key_passphrase.encode() if c.snowflake_key_passphrase else None
        key = serialization.load_pem_private_key((c.snowflake_private_key or "").encode(), password=passphrase)
        # snowflake-connector-python's `private_key` wants the DER / PKCS8 bytes.
        args["private_key"] = key.private_bytes(
            encoding=serialization.Encoding.DER, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()
        )
    return args
