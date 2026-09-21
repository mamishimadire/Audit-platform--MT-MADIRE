from gateway.connectors import urls
from gateway.connectors.base import SqlAlchemyConnector


class OracleConnector(SqlAlchemyConnector):
    # python-oracledb in its default "thin" mode: pure Python, so the client machine needs no Oracle Client install.
    ping_sql = "SELECT 1 FROM DUAL"

    def build_url(self):
        return urls.oracle_url(self.config)

    def connect_args(self) -> dict:
        return urls.oracle_connect_args(self.config)
