"""55 adversarial counter-cases; every row is active with a nonzero demonstration."""

from __future__ import annotations

import unittest

from answer_eval.adversarial import CASES, run_adversarial, run_case
from answer_eval.evaluate import evaluate_fixture
from answer_eval.tables import load_json


class TestAdversarial(unittest.TestCase):
    def test_row_count_all_active(self) -> None:
        self.assertEqual(len(CASES), 55)
        self.assertEqual([c["id"] for c in CASES], list(range(1, 56)))
        self.assertFalse(any(c.get("withdrawn") for c in CASES))
        self.assertTrue(all(c.get("mutation") and c.get("fixtures") for c in CASES))

    def test_all_rows(self) -> None:
        report = run_adversarial()
        self.assertEqual(report["n"], 55)
        self.assertEqual(report["n_active"], 55)
        self.assertEqual(report.get("zero_demonstration_ids"), [])
        fails = [r["id"] for r in report["rows"] if not r["ok"] or r["demonstrations"] < 1]
        self.assertEqual(report["n_ok"], 55, fails)
        self.assertEqual(report["n_active_ok"], 55, fails)
        for r in report["rows"]:
            self.assertGreaterEqual(r["demonstrations"], 1, r["id"])
            self.assertTrue(r["ok"], r["id"])

    def test_row35_string_interval_endpoints(self) -> None:
        data = load_json("fixtures/FIXTURES.eval.v9.json")
        fx = next(f for f in data["fixtures"] if f["fixture_id"] == "F-RS-1")
        baseline = evaluate_fixture(fx)
        mutant = evaluate_fixture(fx, mutation="string_interval_endpoints")
        base_status = baseline["public_row_write_result"]
        mut_status = mutant["public_row_write_result"]
        self.assertEqual(base_status, "accepted")
        self.assertNotEqual(mut_status, "accepted")
        self.assertTrue(str(mut_status).startswith("refused"))
        base_iv = baseline["public_row_example"]["interval"]
        mut_iv = mutant["public_row_example"]["interval"]
        self.assertTrue(base_iv and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in base_iv))
        self.assertTrue(mut_iv and all(isinstance(x, str) for x in mut_iv))
        demo = run_case(next(c for c in CASES if c["id"] == 35), {fx["fixture_id"]: fx})
        self.assertGreaterEqual(demo["demonstrations"], 1)
        self.assertTrue(demo["ok"])


if __name__ == "__main__":
    unittest.main()
