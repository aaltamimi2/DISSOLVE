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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_local(name: str):
    spec = importlib.util.spec_from_file_location(f"aa_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"aa_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_harness = _load_local("harness")
_adv = _load_local("adversarial")
_corr = _load_local("corrections")
compare_fixture = _harness.compare_fixture
load_fixture_file = _harness.load_fixture_file
run_fixture = _harness.run_fixture
demonstrate_all = _adv.demonstrate_all
scan_text = _adv.scan_text

from answer_adapter import new_ledger, present_result
from answer_adapter.halt import AdapterHalt
import answer_adapter

AUDIT = Path("/home/aaltamimi2/dissolve-v12-audit")
PYTHON = Path("/home/aaltamimi2/anaconda3/bin/python")
GUARD = AUDIT / "fixtures" / "synthetic_guard.py"
ADAPTER = ROOT / "answer_adapter"
WALL_LIMIT_S = 60.0
RSS_LIMIT_KB = 524288
PUBLIC_RAW_KEYS = ("raw_response", "raw_draft")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_count_violations():
    spec = importlib.util.spec_from_file_location("aa_synthetic_guard_pin", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.count_violations


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
    row = {
        "chunk_id": "syn-purity",
        "excerpt": "synthetic purity excerpt",
        "title": "t",
        "year": 1,
        "doi": None,
        "page": 1,
        "section": "s",
        "section_origin": "own",
        "paper_sha256": None,
        "char_start": 0,
        "char_end": 1,
    }
    handle = {
        "available": True,
        "source_basis": "provider_metadata",
        "handle": "h",
        "top": [row],
        "data": {"query": "synthetic", "result_count": 1},
    }
    handle_ledger = new_ledger()
    row_id = id(row)
    handle_out = present_result(handle, handle_ledger, {"tool": "inspect_literature_corpus", "call_ordinal": 1})
    handle_mutated = handle["top"][0] is not row or id(row) != row_id or handle_ledger.get("executed_calls", 0) != 0
    isolated = present_result(
        {"available": True, "source_basis": "provider_metadata", "data": {"results": []}},
        new_ledger(),
        {"tool": "search_literature_corpus", "call_ordinal": 1},
    )
    return {
        "input_mutated": mutated,
        "new_ledger_returned": new_ledger_ok,
        "call_tool_missing": missing_tool,
        "call_ordinal_mismatch": ordinal,
        "executed_calls": out["ledger"]["executed_calls"],
        "entries": len(out["ledger"]["entries"]),
        "handle_top_entries": len(handle_out["ledger"]["entries"]),
        "handle_top_mutated": handle_mutated,
        "isolated_token_restart": isolated["ledger"]["executed_calls"] == 1,
    }


def key_material_non_echo(bundle) -> dict:
    from answer_adapter import config_echo

    fx = next(
        f
        for f in bundle["fixtures"]
        if f["family"] == "F-CFG" and f["expected"].get("halt") == "key_material_in_config"
    )
    secret = (fx["input"].get("config") or {}).get("api_key")
    try:
        config_echo(fx["input"]["arm"], fx["input"]["config"])
        return {"halt": None, "value_echoed": None, "secret_in_payload": None}
    except AdapterHalt as exc:
        payload = exc.as_dict()
        blob = json.dumps(payload)
        return {
            "halt": exc.halt,
            "value_echoed": payload.get("value_echoed"),
            "secret_in_payload": bool(secret) and secret in blob,
        }


def static_credential_scan() -> dict:
    hits = []
    for path in sorted(ADAPTER.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for hit in scan_text(text):
            hits.append({"path": str(path.relative_to(ROOT)), **hit})
    return {"hits": len(hits), "items": hits}


def adversarial_by_path(rows: list[dict]) -> dict:
    out = {}
    for row in rows:
        path = f"{row['row']}:{row['fixture_id']}:{row['key']}"
        out[path] = bool(row["failed"])
    return out


def raw_keys_in(value: object) -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PUBLIC_RAW_KEYS:
                found.append(str(key))
            found.extend(raw_keys_in(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(raw_keys_in(item))
    return found


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


def candidate_identity() -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def checkout_identity() -> dict:
    adapter_file = Path(answer_adapter.__file__).resolve()
    adapter_root = adapter_file.parent
    results_path = (ADAPTER / "RESULTS.adapter.fixtures.v2.json").resolve()
    ok = adapter_root == ADAPTER.resolve() and adapter_root.is_relative_to(ROOT) and results_path.parent == ADAPTER.resolve()
    return {
        "ok": ok,
        "import_root": str(ROOT),
        "adapter_file": str(adapter_file),
        "adapter_root": str(adapter_root),
    }


def guard_outputs(count_violations) -> dict:
    items = {}
    results = json.loads((ADAPTER / "RESULTS.adapter.fixtures.v2.json").read_text(encoding="utf-8"))
    items["RESULTS.adapter.fixtures.v2.json"] = count_violations(results)
    items["MANIFEST.v1.json"] = count_violations(
        json.loads((ADAPTER / "MANIFEST.v1.json").read_text(encoding="utf-8"))
    )
    items["README.md"] = count_violations({"text": (ADAPTER / "README.md").read_text(encoding="utf-8")})
    items["PROMPT.rag_alone.v1"] = count_violations(
        {"text": (ADAPTER / "prompts" / "PROMPT.rag_alone.v1.txt").read_text(encoding="utf-8")}
    )
    items["PROMPT.closed_book.v1"] = count_violations(
        {"text": (ADAPTER / "prompts" / "PROMPT.closed_book.v1.txt").read_text(encoding="utf-8")}
    )
    return items


def main() -> int:
    started = time.perf_counter()
    command = [str(PYTHON), str(Path(__file__).resolve())]
    inputs = rehash_inputs()
    count_violations = _load_count_violations()
    bundle = load_fixture_file()
    fixtures = fixture_reproduction(bundle)
    adversarial = demonstrate_all(bundle)
    corrections = _corr.run_all(bundle)
    purity = purity_and_halts()
    key_echo = key_material_non_echo(bundle)
    scan = static_credential_scan()
    identity = checkout_identity()
    git_head = candidate_identity()
    path_keyed = adversarial_by_path(adversarial)
    results_payload = {
        "schema": "RESULTS.adapter.fixtures.v2",
        "command": command,
        "import_root": str(ROOT),
        "candidate": git_head,
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
        "adversarial_rows": len({a["row"] for a in adversarial}),
        "adversarial_by_path": path_keyed,
        "corrections": {
            "n": corrections["n"],
            "passed": corrections["passed"],
            "failed_ids": corrections["failed_ids"],
        },
        "purity": {
            "input_mutated": purity["input_mutated"],
            "call_tool_missing": purity["call_tool_missing"],
            "call_ordinal_mismatch": purity["call_ordinal_mismatch"],
            "handle_top_entries": purity["handle_top_entries"],
            "handle_top_mutated": purity["handle_top_mutated"],
            "isolated_token_restart": purity["isolated_token_restart"],
        },
        "key_material": {
            "halt": key_echo["halt"],
            "value_echoed": key_echo["value_echoed"],
            "secret_in_payload": key_echo["secret_in_payload"],
        },
        "static_hits": scan["hits"],
        "identity_ok": identity["ok"],
    }
    results_path = write_results(results_payload)
    runtime = runtime_identity()
    output_digests = {
        "RESULTS.adapter.fixtures.v2.json": _sha(results_path),
        "PROMPT.rag_alone.v1": _sha(ADAPTER / "prompts" / "PROMPT.rag_alone.v1.txt"),
        "PROMPT.closed_book.v1": _sha(ADAPTER / "prompts" / "PROMPT.closed_book.v1.txt"),
        "README.md": _sha(ADAPTER / "README.md"),
    }
    write_manifest(inputs, output_digests, runtime)
    guards = guard_outputs(count_violations)
    elapsed = time.perf_counter() - started
    rss_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    resources = {
        "wall_s": elapsed,
        "rss_kb": rss_kb,
        "wall_limit_s": WALL_LIMIT_S,
        "rss_limit_kb": RSS_LIMIT_KB,
    }
    results_payload["resources"] = {
        "wall_s": round(elapsed, 6),
        "rss_kb": rss_kb,
    }
    results_payload["guard"] = guards
    results_path = write_results(results_payload)
    output_digests["RESULTS.adapter.fixtures.v2.json"] = _sha(results_path)
    write_manifest(inputs, output_digests, runtime)
    guards = guard_outputs(count_violations)
    public_raw = raw_keys_in(json.loads(results_path.read_text(encoding="utf-8")))
    failed_adv = [a for a in adversarial if not a["failed"]]
    guard_hits = sum(int(v) for v in guards.values())
    resources_ok = elapsed <= WALL_LIMIT_S and rss_kb <= RSS_LIMIT_KB
    purity_ok = (
        not purity["input_mutated"]
        and not purity["handle_top_mutated"]
        and purity["handle_top_entries"] == 1
        and purity["isolated_token_restart"]
        and purity["call_tool_missing"] == "call_tool_missing"
        and purity["call_ordinal_mismatch"] == "call_ordinal_mismatch"
    )
    key_ok = (
        key_echo["halt"] == "key_material_in_config"
        and key_echo["value_echoed"] is False
        and not key_echo["secret_in_payload"]
    )
    ok = (
        fixtures["matched"] == fixtures["fixture_count"]
        and not failed_adv
        and len({a["row"] for a in adversarial}) == 29
        and purity_ok
        and key_ok
        and scan["hits"] == 0
        and guard_hits == 0
        and identity["ok"]
        and not public_raw
        and corrections["passed"] == corrections["n"]
        and resources_ok
    )
    print("state=corrected_checkpoint_ready" if ok else "state=correction_failed")
    print(f"command={command[0]} {command[1]}")
    print(f"import_root={ROOT}")
    print(f"candidate={git_head}")
    print(f"fixtures={fixtures['fixture_count']}")
    print(f"matched={fixtures['matched']}")
    print(f"misses={len(fixtures['misses'])}")
    if fixtures["misses"]:
        print("miss_ids=" + ",".join(m["fixture_id"] for m in fixtures["misses"]))
        for m in fixtures["misses"]:
            print(f"miss_keys.{m['fixture_id']}={','.join(m['missed_keys'])}")
    print(f"adversarial_demos={len(adversarial)}")
    print(f"adversarial_rows={len(set(a['row'] for a in adversarial))}")
    print(f"adversarial_active={sum(1 for a in adversarial if a['failed'])}")
    print(f"adversarial_inactive={len(failed_adv)}")
    if failed_adv:
        print("adversarial_inactive_rows=" + ",".join(f"{a['row']}:{a['fixture_id']}:{a['key']}" for a in failed_adv))
    print(f"corrections_n={corrections['n']}")
    print(f"corrections_passed={corrections['passed']}")
    if corrections["failed_ids"]:
        print("corrections_failed=" + ",".join(corrections["failed_ids"]))
    print(f"purity_mutated={int(purity['input_mutated'])}")
    print(f"purity.handle_top_entries={purity['handle_top_entries']}")
    print(f"purity.handle_top_mutated={int(purity['handle_top_mutated'])}")
    print(f"purity.isolated_token_restart={int(purity['isolated_token_restart'])}")
    print(f"halt.call_tool_missing={purity['call_tool_missing']}")
    print(f"halt.call_ordinal_mismatch={purity['call_ordinal_mismatch']}")
    print(f"key_material.halt={key_echo['halt']}")
    print(f"key_material.value_echoed={key_echo['value_echoed']}")
    print(f"key_material.secret_in_payload={int(bool(key_echo['secret_in_payload']))}")
    print(f"static_hits={scan['hits']}")
    print(f"guard_hits={guard_hits}")
    for name, value in guards.items():
        print(f"guard.{name}={value}")
    print(f"identity_ok={int(identity['ok'])}")
    print(f"public_raw_keys={len(public_raw)}")
    print(f"wall_s={elapsed:.3f}")
    print(f"rss_kb={rss_kb}")
    print(f"resources_ok={int(resources_ok)}")
    print(f"results_sha256={_sha(results_path)}")
    print(f"manifest_sha256={_sha(ADAPTER / 'MANIFEST.v1.json')}")
    print(f"runtime_sha256={runtime['executable_sha256']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
