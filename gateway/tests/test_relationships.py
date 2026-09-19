"""Run from gateway/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v"""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text

from gateway import main as gateway_main
from gateway import relationships
from gateway.config import ConnectionEntry, GatewaySettings
from gateway.connectors.base import ConnectorConfig, SqlAlchemyConnector


def _sqlite_connector(path: Path) -> SqlAlchemyConnector:
    class _Sqlite(SqlAlchemyConnector):
        def build_url(self) -> str:
            return f"sqlite:///{path}"

    return _Sqlite(ConnectorConfig(host="", port=0, database="", username="", password=""))


def _pair(child_entity, child_column, parent_entity, parent_column, child_type="TEXT", parent_type="TEXT"):
    return {
        "child_field_id": f"{child_entity}.{child_column}", "parent_field_id": f"{parent_entity}.{parent_column}",
        "child_entity": child_entity, "child_column": child_column, "child_type": child_type,
        "parent_entity": parent_entity, "parent_column": parent_column, "parent_type": parent_type,
    }


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "client.db"
        engine = create_engine(f"sqlite:///{self.path}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE users (user_id TEXT, secret_note TEXT)"))
            conn.execute(text("CREATE TABLE api_access (api_id TEXT, user_id TEXT)"))
            for i in range(1, 9):
                conn.execute(text("INSERT INTO users VALUES (:u, 'never-leaves-the-network')"), {"u": f"U{i}"})
            for i in range(1, 6):
                conn.execute(text("INSERT INTO api_access VALUES ('API1', :u)"), {"u": f"U{i}"})
            conn.execute(text("INSERT INTO api_access VALUES ('API1', 'GHOST')"))
        engine.dispose()
        self.connector = _sqlite_connector(self.path)

    def tearDown(self):
        self.connector.close()
        self.dir.cleanup()

    def test_counts_distinct_child_values_and_how_many_exist_on_the_parent_side(self):
        [m] = self.connector.measure_relationships([_pair("api_access", "user_id", "users", "user_id")])
        self.assertEqual((m["child_distinct"], m["matched_distinct"], m["parent_distinct"], m["parent_rows"]), (6, 5, 8, 8))

    def test_only_counts_and_the_platforms_own_ids_are_returned_never_a_value(self):
        [m] = self.connector.measure_relationships([_pair("api_access", "user_id", "users", "user_id")])
        self.assertEqual(
            set(m), {"child_field_id", "parent_field_id", "child_distinct", "matched_distinct", "parent_distinct", "parent_rows", "capped"}
        )
        self.assertNotIn("never-leaves-the-network", str(m))
        self.assertNotIn("GHOST", str(m))

    def test_one_pair_failing_does_not_stop_the_rest(self):
        results = self.connector.measure_relationships(
            [_pair("missing_table", "x", "users", "user_id"), _pair("api_access", "user_id", "users", "user_id")]
        )
        self.assertEqual(len(results), 1)

    def test_differently_typed_columns_are_compared_as_text(self):
        [m] = self.connector.measure_relationships([_pair("api_access", "user_id", "users", "user_id", child_type="VARCHAR(20)", parent_type="TEXT")])
        self.assertEqual(m["matched_distinct"], 5)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        test = self

        class FakeClient:
            def get_relationship_requests(self):
                return test.requests

            def report_relationship_measurements(self, connection_id, measurements):
                test.sent.append((connection_id, measurements))

        class FakeConnector:
            def test_connection(self):
                return test.connection_ok, None

            def measure_relationships(self, pairs):
                if test.raises:
                    raise RuntimeError("boom (password=hunter2)")
                return [{"child_field_id": p["child_field_id"], "parent_field_id": p["parent_field_id"], "child_distinct": 3, "matched_distinct": 3, "parent_distinct": 5, "parent_rows": 5, "capped": False} for p in pairs]

            def close(self):
                pass

        self.requests = [{"connection_id": "c1", "pairs": [_pair("a", "x", "b", "y")]}]
        self.connection_ok = True
        self.raises = False
        self.client = FakeClient()
        self._orig = gateway_main.build_connector
        gateway_main.build_connector = lambda source_type, config: FakeConnector()

    def tearDown(self):
        gateway_main.build_connector = self._orig

    def _settings(self, *, enabled=True, connection=True):
        entry = ConnectionEntry("c1", "postgresql", ConnectorConfig("h", 1, "d", "u", "p"), relationships=connection)
        return GatewaySettings("http://x", 300, [entry], relationships_enabled=enabled)

    def test_measures_the_requested_pairs_and_reports_counts(self):
        gateway_main.run_relationship_measurements(self._settings(), self.client)
        self.assertEqual(len(self.sent), 1)
        connection_id, measurements = self.sent[0]
        self.assertEqual((connection_id, measurements[0]["matched_distinct"]), ("c1", 3))

    def test_off_globally_or_per_connection_sends_nothing(self):
        gateway_main.run_relationship_measurements(self._settings(enabled=False), self.client)
        gateway_main.run_relationship_measurements(self._settings(connection=False), self.client)
        self.assertEqual(self.sent, [])

    def test_a_connection_this_gateway_does_not_have_is_ignored(self):
        self.requests = [{"connection_id": "someone-else", "pairs": [_pair("a", "x", "b", "y")]}]
        gateway_main.run_relationship_measurements(self._settings(), self.client)
        self.assertEqual(self.sent, [])

    def test_an_unreachable_database_or_a_failure_sends_nothing_and_never_crashes(self):
        self.connection_ok = False
        gateway_main.run_relationship_measurements(self._settings(), self.client)
        self.connection_ok, self.raises = True, True
        gateway_main.run_relationship_measurements(self._settings(), self.client)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
