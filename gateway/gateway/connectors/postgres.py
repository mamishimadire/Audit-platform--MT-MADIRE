from urllib.parse import quote_plus

from gateway.connectors.base import SqlAlchemyConnector


class PostgresConnector(SqlAlchemyConnector):
    def build_url(self) -> str:
        c = self.config
        return (
            f"postgresql+psycopg://{quote_plus(c.username)}:{quote_plus(c.password)}"
            f"@{c.host}:{c.port}/{c.database}?sslmode=prefer"
        )
