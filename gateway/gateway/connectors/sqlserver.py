from urllib.parse import quote_plus

from gateway.connectors.base import SqlAlchemyConnector


class SqlServerConnector(SqlAlchemyConnector):
    def build_url(self) -> str:
        c = self.config
        # pymssql — a pure-Python driver, chosen so a client machine never needs
        # a native ODBC driver installed just to run the gateway.
        return f"mssql+pymssql://{quote_plus(c.username)}:{quote_plus(c.password)}@{c.host}:{c.port}/{c.database}"
