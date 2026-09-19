"""
Run from the gateway/ directory with the gateway's own environment:

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

(unittest, not pytest: the gateway's requirements deliberately carry no test
framework, since they are also what the Windows exe is built from.)
"""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text

from gateway import main as gateway_main
from gateway import profiling
from gateway.config import ConnectionEntry, GatewaySettings
from gateway.connectors.base import ConnectorConfig, SqlAlchemyConnector


def _sqlite_connector(path: Path) -> SqlAlchemyConnector:
    class _Sqlite(SqlAlchemyConnector):
        def build_url(self) -> str:
            return f"sqlite:///{path}"

    return _Sqlite(ConnectorConfig(host="", port=0, database="", username="", password=""))


class SampleRowsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "client.db"
        engine = create_engine(f"sqlite:///{self.path}")
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE "weird table" (id INTEGER, "user status" TEXT, photo BLOB)'))
            conn.execute(text("INSERT INTO \"weird table\" VALUES (1, 'active', x'00'), (2, 'locked', x'01'), (3, 'active', x'02')"))
        engine.dispose()
        self.connector = _sqlite_connector(self.path)

    def tearDown(self):
        self.connector.close()
        self.dir.cleanup()

    def test_reads_only_the_wanted_columns_up_to_the_limit_and_quotes_identifiers(self):
        rows = self.connector.sample_rows("weird table", [("id", "INTEGER"), ("user status", "TEXT")], 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), {"id", "user status"})

    def test_binary_columns_are_never_requested_by_attach_profiles(self):
        entities = self.connector.discover()
        wanted = [name for name, _ in profiling.wanted_columns(entities[0]["fields"])]
        self.assertEqual(wanted, ["id", "user status"])

        profiled = profiling.attach_profiles(self.connector, entities, sample_rows=500)
        self.assertEqual(profiled, 1)
        by_name = {f["field_name"]: f for f in entities[0]["fields"]}
        self.assertNotIn("profile", by_name["photo"])
        self.assertEqual(by_name["user status"]["profile"]["top_values"], ["active", "locked"])
        self.assertEqual(by_name["id"]["profile"]["value_kind"], "numeric")


class ProfileBuildTests(unittest.TestCase):
    def test_enum_like_column_reports_its_lowercased_values(self):
        profile = profiling.build_column_profile("status", ["Active", "LOCKED", "active", None, "Active"])
        self.assertEqual(profile["top_values"], ["active", "locked"])
        self.assertEqual((profile["sample_size"], profile["null_ratio"]), (5, 0.2))

    def test_sensitive_or_personal_columns_never_report_values(self):
        for name in ("password_hash", "email", "salary", "first_name", "api_token", "iban"):
            self.assertIsNone(profiling.build_column_profile(name, ["a", "b"])["top_values"], name)
        self.assertIsNone(profiling.build_column_profile("status", ["a", "b"], is_sensitive=True)["top_values"])

    def test_identifiers_dates_and_high_cardinality_never_report_values(self):
        self.assertIsNone(profiling.build_column_profile("code", [f"v{i}" for i in range(25)])["top_values"])
        self.assertIsNone(profiling.build_column_profile("seen", ["2024-01-01", "2024-02-01"])["top_values"])
        self.assertIsNone(profiling.build_column_profile("note", ["x" * 41, "y"])["top_values"])

    def test_empty_sample_has_no_profile(self):
        self.assertIsNone(profiling.build_column_profile("status", []))

    def test_one_table_failing_does_not_stop_the_rest(self):
        class Flaky:
            def sample_rows(self, entity_name, columns, limit):
                if entity_name == "broken":
                    raise RuntimeError("permission denied for table broken (password=hunter2)")
                return [{"status": "active"}, {"status": "locked"}]

        entities = [
            {"entity_name": "broken", "fields": [{"field_name": "status", "data_type": "TEXT"}]},
            {"entity_name": "fine", "fields": [{"field_name": "status", "data_type": "TEXT"}]},
        ]
        self.assertEqual(profiling.attach_profiles(Flaky(), entities, sample_rows=500), 1)
        self.assertNotIn("profile", entities[0]["fields"][0])
        self.assertIn("profile", entities[1]["fields"][0])


class RunProfilingTests(unittest.TestCase):
    def setUp(self):
        gateway_main._next_profile_due.clear()
        self.reports = []
        self.connects = 0
        test = self

        class FakeClient:
            def report_discovery(self, connection_id, entities):
                test.reports.append((connection_id, entities))

        class FakeConnector:
            def test_connection(self):
                test.connects += 1
                return test.connection_ok, None

            def discover(self):
                return [{"entity_name": "users", "fields": [{"field_name": "status", "data_type": "TEXT"}]}]

            def sample_rows(self, entity_name, columns, limit):
                if test.sample_raises:
                    raise RuntimeError("boom")
                return [{"status": "active"}, {"status": "locked"}]

            def close(self):
                pass

        self.connection_ok = True
        self.sample_raises = False
        self.client = FakeClient()
        self._orig_build = gateway_main.build_connector
        gateway_main.build_connector = lambda source_type, config: FakeConnector()

    def tearDown(self):
        gateway_main.build_connector = self._orig_build
        gateway_main._next_profile_due.clear()

    def _settings(self, *, enabled=True, connection_profiling=True):
        entry = ConnectionEntry("c1", "postgresql", ConnectorConfig("h", 1, "d", "u", "p"), profiling=connection_profiling)
        return GatewaySettings("http://x", 300, [entry], profiling_enabled=enabled)

    def test_profiles_once_then_waits_for_the_interval(self):
        settings = self._settings()
        gateway_main.run_profiling(settings, self.client)
        gateway_main.run_profiling(settings, self.client)
        self.assertEqual(len(self.reports), 1)
        sent_field = self.reports[0][1][0]["fields"][0]
        self.assertEqual(sent_field["profile"]["top_values"], ["active", "locked"])

    def test_disabled_globally_or_per_connection_sends_nothing_and_never_connects(self):
        gateway_main.run_profiling(self._settings(enabled=False), self.client)
        gateway_main.run_profiling(self._settings(connection_profiling=False), self.client)
        self.assertEqual((self.reports, self.connects), ([], 0))

    def test_unreachable_database_retries_sooner_than_a_full_interval(self):
        self.connection_ok = False
        settings = self._settings()
        gateway_main.run_profiling(settings, self.client)
        self.assertEqual(self.reports, [])
        due_in = gateway_main._next_profile_due["c1"] - __import__("time").monotonic()
        self.assertLess(due_in, settings.profile_interval_hours * 3600)
        self.assertLessEqual(due_in, gateway_main._PROFILE_RETRY_SECONDS)

    def test_nothing_sampled_sends_no_discovery(self):
        self.sample_raises = True
        gateway_main.run_profiling(self._settings(), self.client)
        self.assertEqual(self.reports, [])


if __name__ == "__main__":
    unittest.main()
