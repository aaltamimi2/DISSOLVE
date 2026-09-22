"""Reproduce all 101 fixtures from clauses (no expected-value lookup in the scorer)."""

from __future__ import annotations

import unittest

from answer_eval.reproduce import run_fixtures
from answer_eval.resample import PRODUCTION_B_CONSTANT
from answer_eval.tables import verify_all_pins


class TestFixtures(unittest.TestCase):
    def test_pins(self) -> None:
        pins = verify_all_pins()
        self.assertEqual(len(pins), 28)

    def test_all_fixtures(self) -> None:
        report = run_fixtures()
        self.assertEqual(report["n"], 101)
        self.assertEqual(report["n_ok"], 101, [r["fixture_id"] for r in report["rows"] if not r["ok"]])


class TestProductionB(unittest.TestCase):
    def test_constant(self) -> None:
        self.assertEqual(PRODUCTION_B_CONSTANT, 2000)


if __name__ == "__main__":
    unittest.main()
