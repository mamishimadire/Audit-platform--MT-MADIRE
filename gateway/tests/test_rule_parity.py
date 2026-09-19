"""
The Gateway's pandas rule engine must give exactly the answers the platform's
pure-Python engine gives. Both run the SAME cases, in
backend/tests/fixtures/rule_parity.json (the backend's test_bridge_rule.py runs
the other side).

Run from gateway/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import json
import unittest
from pathlib import Path

import pandas as pd

from gateway import rule_engine

FIXTURES = Path(__file__).resolve().parents[2] / "backend" / "tests" / "fixtures" / "rule_parity.json"


class RuleParityTests(unittest.TestCase):
    def test_gateway_engine_matches_the_shared_fixture(self):
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 4)
        for case in cases:
            with self.subTest(case=case["name"]):
                frames = {name: pd.DataFrame(rows) for name, rows in case["records"].items()}
                result = rule_engine.evaluate(case["rule"], frames)
                self.assertEqual(result.records_analyzed, case["expected"]["records_analyzed"])
                flagged = sorted(e["exception_data"]["api_id"] for e in result.exceptions)
                self.assertEqual(flagged, case["expected"]["flagged"])

    def test_a_missing_bridge_column_is_a_key_error_never_a_wrong_answer(self):
        case = json.loads(FIXTURES.read_text(encoding="utf-8"))[0]
        frames = {name: pd.DataFrame(rows) for name, rows in case["records"].items()}
        frames["user_roles"] = frames["user_roles"].drop(columns=["user_id"])
        with self.assertRaises(KeyError):
            rule_engine.evaluate(case["rule"], frames)  # main.py reports this as mapping_required


if __name__ == "__main__":
    unittest.main()
