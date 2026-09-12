"""Bounded synthetic reproduction for the WP-2b adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/aaltamimi2/dissolve-v12-wp2b")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def _load_local(name: str):
    spec = importlib.util.spec_from_file_location(f"aa_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"aa_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_harness = _load_local("harness")
_adv = _load_local("adversarial")
compare_fixture = _harness.compare_fixture
load_fixture_file = _harness.load_fixture_file
run_fixture = _harness.run_fixture
demonstrate_all = _adv.demonstrate_all

from answer_adapter import new_ledger, present_result
from answer_adapter.halt import AdapterHalt

AUDIT = Path("/home/aaltamimi2/dissolve-v12-audit")
PYTHON = Path("/home/aaltamimi2/anaconda3/bin/python")
GUARD = AUDIT / "fixtures" / "synthetic_guard.py"
ADAPTER = ROOT / "answer_adapter"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rehash_inputs() -> dict:
    pins = {
        "HANDOFF.wp2b.v3.md": ("13d8b4e6cf66cc0c871060b472766daade2394fcadc026079a679dc231caa717", AUDIT / "HANDOFF.wp2b.v3.md"),
        "RAG_AUDIT_SPEC.v2.5.2.md": ("97aa6f15613a8733c9d509cd2f93fb7fd45e46f2ec812bfcb3ff702d4ef19797", AUDIT / "RAG_AUDIT_SPEC.v2.5.2.md"),
        "RAG_AUDIT_SPEC.v2.5.1.md": ("55003624c2e26f129981998db006ec90d69acc5d4042a0bd081cc6fe1a46f82b", AUDIT / "RAG_AUDIT_SPEC.v2.5.1.md"),
        "RAG_AUDIT_SPEC.v2.5.md": ("cf60ae08690b64b61d80bcf98c4c54fb7b08ed51008aefb815afde0b2c5bbc48", AUDIT / "RAG_AUDIT_SPEC.v2.5.md"),
        "RAG_AUDIT_SPEC.v2.1.md": ("bf262ff6cada3285a6e4613bc96c56afc196b1a2f870e224bf03ab118ab89980", AUDIT / "RAG_AUDIT_SPEC.v2.1.md"),
        "FIXTURES.adapter.v3.json": ("9a5636e11b9ff5f85cee3a2d5ddbd8bc5b56eb4049d6ec24b364741810e2c347", AUDIT / "fixtures" / "FIXTURES.adapter.v3.json"),
        "FIXTURES.adapter.v2.json": ("f4cdf274ae647660c3f3954209fc525e2a65ca78a315babbbe30e974a645ef20", AUDIT / "fixtures" / "FIXTURES.adapter.v2.json"),
        "synthetic_guard.py": ("8f434541004c67e61d79ec5356bd98adafae5e0949fc8f6747c55d7e1aa9f365", GUARD),
        "gold_value_guard.py": ("961ef0aeb8842d785e7806d8b57da7c0a62dcba393b282ec9490f04f578847a9", AUDIT / "instruments" / "gold_value_guard.py"),
        "build_fixtures_adapter_v3.py": ("d0a969394e297017b7845090c9ccd0fbf85f6faa735965c3a1f656ab95cf4fea", AUDIT / "instruments" / "build_fixtures_adapter_v3.py"),
    }
    observed = {}
    for name, (expected, path) in pins.items():
        digest = _sha(path)
        if digest != expected:
            raise SystemExit(f"input_digest_mismatch {name}")
        observed[name] = digest
    return observed


def fixture_reproduction(bundle) -> dict:
    by_family = {}
    reports = []
    misses = []
    for fixture in bundle["fixtures"]:
        actual = run_fixture(fixture)
        report = compare_fixture(fixture, actual)
        reports.append(report)
        by_family.setdefault(fixture["family"], {"n": 0, "matched": 0})
        by_family[fixture["family"]]["n"] += 1
        if report["matched"]:
            by_family[fixture["family"]]["matched"] += 1
        else:
            misses.append({"fixture_id": report["fixture_id"], "missed_keys": report["missed_keys"]})
    return {
        "fixture_count": len(bundle["fixtures"]),
        "matched": sum(1 for r in reports if r["matched"]),
        "misses": misses,
        "by_family": by_family,
        "reports": [
            {
                "fixture_id": r["fixture_id"],
                "family": r["family"],
                "matched_keys": r["matched_keys"],
                "graded_keys": r["graded_keys"],
                "missed_n": len(r["missed_keys"]),
            }
            for r in reports
        ],
    }


def purity_and_halts() -> dict:
    ledger = new_ledger()
    result = {
        "available": True,
        "source_basis": "provider_metadata",
        "data": {"results": []},
    }
    call = {"tool": "inspect_literature_corpus", "call_ordinal": 1}
    snapshot_ledger = json.loads(json.dumps(ledger))
    snapshot_result = json.loads(json.dumps(result))
    out = present_result(result, ledger, call)
    mutated = ledger != snapshot_ledger or result != snapshot_result
    new_ledger_ok = out["ledger"] is not ledger
    missing_tool = None
    try:
        present_result(result, new_ledger(), {"call_ordinal": 1})
    except AdapterHalt as exc:
        missing_tool = exc.halt
    ordinal = None
    try:
        present_result(result, new_ledger(), {"tool": "inspect_literature_corpus", "call_ordinal": 2})
    except AdapterHalt as exc:
        ordinal = exc.halt
    return {
        "input_mutated": mutated,
        "new_ledger_returned": new_ledger_ok,
        "call_tool_missing": missing_tool,
        "call_ordinal_mismatch": ordinal,
        "executed_calls": out["ledger"]["executed_calls"],
        "entries": len(out["ledger"]["entries"]),
    }


def static_credential_scan() -> dict:
    hits = []
    for path in sorted(ADAPTER.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in ("os.environ", "os.getenv", "getenv(", "environ["):
            if needle in text:
                hits.append({"path": str(path.relative_to(ROOT)), "needle": needle})
        if "META_MUSE_API_KEY" in text:
            for line in text.splitlines():
                stripped = line.strip()
                if "META_MUSE_API_KEY" in stripped and any(
                    tok in stripped for tok in ("environ", "getenv", "open(", "Path(")
                ) and "API_KEY_ENV_NAME" not in stripped:
                    hits.append({"path": str(path.relative_to(ROOT)), "needle": "key_value_read"})
    return {"hits": len(hits), "items": hits}


def run_guard(path: Path) -> dict:
    proc = subprocess.run(
        [str(PYTHON), str(GUARD), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    return {"exit": proc.returncode, "stdout": proc.stdout.strip()}


def write_results(payload: dict) -> Path:
    path = ADAPTER / "RESULTS.adapter.fixtures.v2.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_manifest(input_digests: dict, output_digests: dict, runtime: dict) -> Path:
    path = ADAPTER / "MANIFEST.v1.json"
    payload = {
        "schema": "answer_adapter.MANIFEST.v1",
        "package": "cf950a384de54f700789c3e1d5634cc7d15679e8",
        "handoff": {
            "path": "HANDOFF.wp2b.v3.md",
            "commit": "a6cf298",
            "sha256": "13d8b4e6cf66cc0c871060b472766daade2394fcadc026079a679dc231caa717",
        },
        "dispatch": {
            "path": "DISPATCH.wp2b.v1.md",
            "sha256": "b3e59ebb8061ed8e37115c243009119865c9adc5a9d8edebee3a81d8df3dbb3f",
        },
        "product_pin": "849ecdccb4d68204d0f68e56362adb90d4ef5296",
        "worktree": str(ROOT),
        "branch": "wp2b-adapter",
        "runtime": runtime,
        "inputs": input_digests,
        "outputs": output_digests,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def runtime_identity() -> dict:
    import numpy
    from importlib.metadata import version as pkg_version

    return {
        "executable": str(PYTHON),
        "executable_sha256": _sha(PYTHON),
        "python": "3.11.9",
        "numpy": numpy.__version__,
        "jsonschema": pkg_version("jsonschema"),
    }


def main() -> int:
    started = time.perf_counter()
    inputs = rehash_inputs()
    bundle = load_fixture_file()
    fixtures = fixture_reproduction(bundle)
    adversarial = demonstrate_all(bundle)
    purity = purity_and_halts()
    scan = static_credential_scan()
    results_payload = {
        "schema": "RESULTS.adapter.fixtures.v2",
        "fixture_count": fixtures["fixture_count"],
        "matched": fixtures["matched"],
        "missed_n": len(fixtures["misses"]),
        "families": {k: v for k, v in fixtures["by_family"].items()},
        "by_id": {
            r["fixture_id"]: {
                "family": r["family"],
                "matched_keys": r["matched_keys"],
                "graded_keys": r["graded_keys"],
                "missed_n": r["missed_n"],
            }
            for r in fixtures["reports"]
        },
        "adversarial_n": len(adversarial),
        "adversarial_failed": sum(1 for a in adversarial if a["failed"]),
        "purity": {
            "input_mutated": purity["input_mutated"],
            "call_tool_missing": purity["call_tool_missing"],
            "call_ordinal_mismatch": purity["call_ordinal_mismatch"],
        },
        "static_hits": scan["hits"],
    }
    results_path = write_results(results_payload)
    runtime = runtime_identity()
    # write manifest without results/manifest hashes first
    output_digests = {
        "RESULTS.adapter.fixtures.v2.json": _sha(results_path),
        "PROMPT.rag_alone.v1": _sha(ADAPTER / "prompts" / "PROMPT.rag_alone.v1.txt"),
        "PROMPT.closed_book.v1": _sha(ADAPTER / "prompts" / "PROMPT.closed_book.v1.txt"),
    }
    manifest_path = write_manifest(inputs, output_digests, runtime)
    output_digests["MANIFEST.v1.json"] = _sha(manifest_path)
    write_manifest(inputs, output_digests, runtime)
    guards = {
        "RESULTS": run_guard(results_path),
        "MANIFEST": run_guard(ADAPTER / "MANIFEST.v1.json"),
    }
    elapsed = time.perf_counter() - started
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print("state=checkpoint_ready")
    print(f"fixtures={fixtures['fixture_count']}")
    print(f"matched={fixtures['matched']}")
    print(f"misses={len(fixtures['misses'])}")
    if fixtures["misses"]:
        print("miss_ids=" + ",".join(m["fixture_id"] for m in fixtures["misses"]))
        for m in fixtures["misses"]:
            print(f"miss_keys.{m['fixture_id']}={','.join(m['missed_keys'])}")
    failed_adv = [a for a in adversarial if not a["failed"]]
    print(f"adversarial_demos={len(adversarial)}")
    print(f"adversarial_rows={len(set(a['row'] for a in adversarial))}")
    print(f"adversarial_active={sum(1 for a in adversarial if a['failed'])}")
    print(f"adversarial_inactive={len(failed_adv)}")
    if failed_adv:
        print("adversarial_inactive_rows=" + ",".join(f"{a['row']}:{a['fixture_id']}:{a['key']}" for a in failed_adv))
    print(f"purity_mutated={int(purity['input_mutated'])}")
    print(f"halt.call_tool_missing={purity['call_tool_missing']}")
    print(f"halt.call_ordinal_mismatch={purity['call_ordinal_mismatch']}")
    print(f"static_hits={scan['hits']}")
    print(f"guard.RESULTS={guards['RESULTS']['stdout']}")
    print(f"guard.MANIFEST={guards['MANIFEST']['stdout']}")
    print(f"wall_s={elapsed:.3f}")
    print(f"rss_kb={rss_kb}")
    print(f"results_sha256={_sha(results_path)}")
    print(f"manifest_sha256={_sha(ADAPTER / 'MANIFEST.v1.json')}")
    print(f"runtime_sha256={runtime['executable_sha256']}")
    return 0 if fixtures["matched"] == fixtures["fixture_count"] and not failed_adv else 1


if __name__ == "__main__":
    raise SystemExit(main())
