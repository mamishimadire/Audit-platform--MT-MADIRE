from gateway.connectors import urls
from gateway.connectors.base import SqlAlchemyConnector


class SnowflakeConnector(SqlAlchemyConnector):
    def build_url(self):
        return urls.snowflake_url(self.config)

    def connect_args(self) -> dict:
        return urls.snowflake_connect_args(self.config)
