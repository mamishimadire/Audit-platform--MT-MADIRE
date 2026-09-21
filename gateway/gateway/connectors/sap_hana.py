from gateway.connectors import urls
from gateway.connectors.base import SqlAlchemyConnector


class SapHanaConnector(SqlAlchemyConnector):
    ping_sql = "SELECT 1 FROM DUMMY"

    def build_url(self):
        return urls.hana_url(self.config)

    def connect_args(self) -> dict:
        return urls.hana_connect_args(self.config)
