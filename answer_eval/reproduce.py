"""Reproduce 101 fixtures and write diagnostic artifacts."""

from __future__ import annotations

import json
import resource
import time
from pathlib import Path
from typing import Any

from answer_eval.evaluate import evaluate_fixture, is_note_key
from answer_eval.ids import PRODUCTION_HEX_WIDTH
from answer_eval.resample import PRODUCTION_B_CONSTANT
from answer_eval.tables import (
    DEFAULT_SEED,
    DISPATCH_SHA256,
    HANDOFF_SHA256,
    PACKAGE_IDENTITY,
    PINS,
    PRODUCT_COMMIT,
    PRODUCTION_B,
    PYTHON_EXECUTABLE_SHA256,
    load_json,
    verify_all_pins,
)

ROOT = Path(__file__).resolve().parent


def _compare(expected: Any, observed: Any) -> bool:
    if expected == observed:
        return True
    if isinstance(expected, dict) and isinstance(observed, dict):
        for k, v in expected.items():
            if is_note_key(k):
                continue
            if k == "interval" and isinstance(v, list) and "interval_serialized" in expected:
                continue
            if k not in observed:
                return False
            if not _compare(v, observed[k]):
                return False
        return True
    if isinstance(expected, list) and isinstance(observed, list):
        if len(expected) != len(observed):
            return False
        return all(_compare(a, b) for a, b in zip(expected, observed))
    return False


def failing_keys(expected: dict, observed: dict, prefix: str = "") -> list[str]:
    fails = []
    for k, v in expected.items():
        if is_note_key(k):
            continue
        if k == "interval" and "interval_serialized" in expected:
            continue
        key = f"{prefix}.{k}" if prefix else k
        if k not in observed:
            fails.append(key)
            continue
        ov = observed[k]
        if isinstance(v, dict) and isinstance(ov, dict):
            fails.extend(failing_keys(v, ov, key))
        elif not _compare(v, ov):
            fails.append(key)
    return fails


def run_fixtures(family: str | None = None, fixture_id: str | None = None) -> dict[str, Any]:
    data = load_json("fixtures/FIXTURES.eval.v9.json")
    rows = []
    n_ok = 0
    for fx in data["fixtures"]:
        if family and fx["family"] != family:
            continue
        if fixture_id and fx["fixture_id"] != fixture_id:
            continue
        obs = evaluate_fixture(fx)
        fails = failing_keys(fx["expected"], obs)
        ok = not fails
        if ok:
            n_ok += 1
        rows.append(
            {
                "fixture_id": fx["fixture_id"],
                "family": fx["family"],
                "ok": ok,
                "failing_keys": fails,
                "halt": obs.get("halt"),
            }
        )
    return {"n": len(rows), "n_ok": n_ok, "rows": rows}


def write_artifacts(report: dict[str, Any]) -> None:
    diagnostic = {
        "schema": "RESULTS.fixtures.v1",
        "fixture_count": report["n"],
        "accepted_count": report["n_ok"],
        "rows": [
            {
                "fixture_id": r["fixture_id"],
                "family": r["family"],
                "ok": r["ok"],
                "failing_keys": r["failing_keys"],
                "halt": r["halt"],
            }
            for r in report["rows"]
        ],
    }
    (ROOT / "RESULTS.fixtures.v1.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "package_identity": PACKAGE_IDENTITY,
        "product_commit": PRODUCT_COMMIT,
        "handoff_sha256": HANDOFF_SHA256,
        "dispatch_sha256": DISPATCH_SHA256,
        "input_digests": PINS,
        "runtime": {
            "python": "3.11.9",
            "executable": "/home/aaltamimi2/anaconda3/bin/python",
            "executable_sha256": PYTHON_EXECUTABLE_SHA256,
            "numpy": "2.4.6",
            "jsonschema": "4.19.2",
            "erratum": "HANDOFF.wp1.v8 §8 Python 3.12 phrase replaced for this dispatch only",
        },
        "production_B": PRODUCTION_B,
        "id_hex_width": PRODUCTION_HEX_WIDTH,
        "default_seed": DEFAULT_SEED,
    }
    (ROOT / "MANIFEST.v1.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main() -> int:
    t0 = time.perf_counter()
    verify_all_pins()
    assert PRODUCTION_B_CONSTANT == 2000
    report = run_fixtures()
    write_artifacts(report)
    elapsed = time.perf_counter() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"fixtures {report['n_ok']}/{report['n']}")
    fails = [r for r in report["rows"] if not r["ok"]]
    print(f"failing {len(fails)}")
    for r in fails[:40]:
        print(r["fixture_id"], ",".join(r["failing_keys"][:8]))
    print(f"wall_s {elapsed:.3f} rss_kb {rss}")
    return 0 if report["n_ok"] == report["n"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
