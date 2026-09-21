"""Run from gateway/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v"""
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text

from gateway.connectors.base import ConnectorConfig, SqlAlchemyConnector
from gateway.schema_metadata import derive_column_constraints, normalize_foreign_keys


def _sqlite_connector(path: Path) -> SqlAlchemyConnector:
    class _Sqlite(SqlAlchemyConnector):
        def build_url(self) -> str:
            return f"sqlite:///{path}"

    return _Sqlite(ConnectorConfig(host="", port=0, database="", username="", password=""))


class DiscoveryMetadataTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        path = Path(self.dir.name) / "client.db"
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE users (user_id TEXT PRIMARY KEY, email TEXT UNIQUE, status TEXT)"))
            conn.execute(text("CREATE TABLE api_access (api_id TEXT, user_id TEXT REFERENCES users(user_id))"))
            conn.execute(text("CREATE INDEX ix_api_user ON api_access(user_id)"))
        engine.dispose()
        self.connector = _sqlite_connector(path)

    def tearDown(self):
        self.connector.close()
        self.dir.cleanup()

    def test_discovery_reports_keys_uniqueness_indexes_and_foreign_keys(self):
        entities = {e["entity_name"]: e for e in self.connector.discover()}
        users = {f["field_name"]: f for f in entities["users"]["fields"]}
        self.assertTrue(users["user_id"]["is_primary_key"] and users["user_id"]["is_unique"])
        self.assertTrue(users["email"]["is_unique"])
        self.assertFalse(users["status"]["is_unique"])
        self.assertEqual(entities["users"]["foreign_keys"], [])  # reported: none exist

        api = {f["field_name"]: f for f in entities["api_access"]["fields"]}
        self.assertTrue(api["user_id"]["is_indexed"] and not api["user_id"]["is_unique"])
        self.assertEqual(
            [(fk["columns"], fk["referred_table"], fk["referred_columns"]) for fk in entities["api_access"]["foreign_keys"]],
            [(["user_id"], "users", ["user_id"])],
        )

    def test_a_failing_catalog_call_is_reported_as_not_reported_never_as_none_exist(self):
        from unittest import mock

        from sqlalchemy.engine.reflection import Inspector

        # The catalogue read fails both ways it is attempted: batched for the whole schema, and table by table.
        with mock.patch.object(Inspector, "get_multi_foreign_keys", side_effect=RuntimeError("no permission")), mock.patch.object(
            Inspector, "get_foreign_keys", side_effect=RuntimeError("no permission")
        ):
            entities = self.connector.discover()
        self.assertTrue(all("foreign_keys" not in e for e in entities))


class SharedLogicTests(unittest.TestCase):
    def test_constraint_derivation(self):
        facts = derive_column_constraints(
            ["id", "email", "tenant_id"],
            nullable_by_column={"id": True, "email": False, "tenant_id": True},
            pk_columns=["id"],
            unique_constraints=[{"column_names": ["email"]}, {"column_names": ["tenant_id", "email"]}],
            indexes=[],
        )
        self.assertEqual(facts["id"], {"is_nullable": False, "is_unique": True, "is_indexed": True})
        self.assertFalse(facts["tenant_id"]["is_unique"])
        self.assertTrue(facts["tenant_id"]["is_indexed"])

    def test_foreign_key_normalisation_keeps_none_as_not_reported(self):
        self.assertIsNone(normalize_foreign_keys(None))
        self.assertEqual(normalize_foreign_keys([]), [])


if __name__ == "__main__":
    unittest.main()
