"""Bounded exported-API regressions for A-1…A-4. Does not consult expected blocks as oracles."""

from __future__ import annotations

import copy
import json
import unittest

import answer_eval
from answer_eval.adversarial import CASES, run_case
from answer_eval.evaluate import evaluate_fixture
from answer_eval.reproduce import failing_keys
from answer_eval.tables import load_json


def _fixtures() -> dict[str, dict]:
    data = load_json("fixtures/FIXTURES.eval.v9.json")
    return {fx["fixture_id"]: fx for fx in data["fixtures"]}


class TestExportedScoreEmit(unittest.TestCase):
    def test_score_emits_metrics_without_invented_binding(self) -> None:
        fx = _fixtures()["F-CB-1"]
        scored = answer_eval.score([fx], arm="evidence_off")
        self.assertIn("metrics", scored)
        self.assertIsInstance(scored["metrics"], list)
        self.assertGreater(len(scored["metrics"]), 0)
        self.assertNotIn("backbone", scored)
        self.assertNotIn("subject_id", scored)
        for row in scored["metrics"]:
            self.assertNotEqual(row.get("backbone"), "FAM_A")
            self.assertNotEqual(row.get("partition"), "fixture")
            self.assertNotIn("cell", row)
            self.assertNotIn("primary_family", row)
        emitted = answer_eval.emit_public(scored)
        self.assertIn("write_results", emitted)
        self.assertNotEqual(emitted.get("write_results"), "accepted")
        self.assertTrue(str(emitted.get("write_results")).startswith("refused"))

    def test_score_preserves_caller_binding(self) -> None:
        fx = _fixtures()["F-CB-1"]
        scored = answer_eval.score(
            [fx],
            arm="evidence_off",
            backbone="bb1",
            partition="dev",
            subject_id="subj1",
        )
        self.assertEqual(scored["backbone"], "bb1")
        self.assertEqual(scored["subject_id"], "subj1")
        for row in scored["metrics"]:
            self.assertEqual(row.get("backbone"), "bb1")
            self.assertEqual(row.get("partition"), "dev")
            self.assertEqual(row.get("arm"), "off_run1")
            self.assertNotIn("cell", row)
        emitted = answer_eval.emit_public(scored)
        self.assertEqual(emitted.get("write_results"), "accepted")

    def test_empty_score_does_not_fabricate_denominator(self) -> None:
        scored = answer_eval.score([])
        self.assertIn("metrics", scored)
        ops = [r for r in scored["metrics"] if r.get("metric_id") == "operational_error_rate"]
        self.assertTrue(ops)
        self.assertEqual(ops[0].get("status"), "NA")
        self.assertNotIn("den", ops[0])
        self.assertNotIn("num", ops[0])

    def test_emit_public_refuses_on_arm_without_cell(self) -> None:
        obj = copy.deepcopy(_fixtures()["F-EMIT-7"]["prediction"]["attempts"][1]["object"])
        emitted = answer_eval.emit_public(obj)
        self.assertTrue(str(emitted.get("write_results")).startswith("refused"))


class TestPointerCustody(unittest.TestCase):
    def test_patterned_digest_key_is_sanitized(self) -> None:
        marker = "SYNTHETIC-PRIVATE-MARKER-7f3a"
        doc = copy.deepcopy(_fixtures()["F-EMIT-7"]["prediction"]["attempts"][0]["object"])
        doc["input_digests"] = {marker: "invalid"}
        emitted = answer_eval.emit_public(doc)
        records = emitted.get("error_log_records") or []
        blob = json.dumps(records)
        self.assertTrue(str(emitted.get("write_results")).startswith("refused"))
        self.assertNotIn(marker, blob)
        self.assertTrue(records)
        self.assertIn("<unknown_key#", records[0].get("pointer") or "")
        from pathlib import Path
        import importlib.util

        path = Path(__file__).resolve().parent / "synthetic_guard.py"
        spec = importlib.util.spec_from_file_location("synthetic_guard_mod", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(int(mod.count_violations(records)), 0)


class TestAdversarialIsolation(unittest.TestCase):
    def test_row26_unrelated_attempt_is_not_credited(self) -> None:
        from unittest.mock import patch

        fixtures = _fixtures()
        case26 = next(c for c in CASES if c["id"] == 26)
        fx26 = fixtures["F-EMIT-3"]
        wrong_target = copy.deepcopy(evaluate_fixture(fx26))
        original_target = wrong_target["write_results"][0]
        wrong_target["write_results"][1] = "accepted"
        with patch("answer_eval.adversarial.evaluate_fixture", return_value=wrong_target):
            check26 = run_case(case26, {fx26["fixture_id"]: fx26})
        self.assertEqual(wrong_target["write_results"][0], original_target)
        self.assertFalse(check26["ok"])


class TestLabelIndependence(unittest.TestCase):
    def test_identity_diagnostics_do_not_key_on_fixture_id(self) -> None:
        fixtures = _fixtures()
        fxid = copy.deepcopy(fixtures["F-ID-7"])
        fxid["fixture_id"] = "FX-LABEL-ONLY"
        got = evaluate_fixture(fxid)
        failed = failing_keys(fixtures["F-ID-7"]["expected"], got)
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
