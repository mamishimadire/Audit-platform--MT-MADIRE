from urllib.parse import quote_plus

from gateway.connectors.base import SqlAlchemyConnector


class MySqlConnector(SqlAlchemyConnector):
    def build_url(self) -> str:
        c = self.config
        return f"mysql+pymysql://{quote_plus(c.username)}:{quote_plus(c.password)}@{c.host}:{c.port}/{c.database}"
