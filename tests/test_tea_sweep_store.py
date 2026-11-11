"""The TEA sweep store (owner, 2026-11-11): the Euler core grid served one stored plant at a time, safely for 20-40
people on the 2 GB site. Each test fails on the code before the store existed (the module or the tea hook is new)."""
import copy
import datetime
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from dissolve import tea, tea_sweep_store as sweep

FINGERPRINT = {"process_model_sha256": "a" * 64, "worker_source_sha256": "b" * 64,
               "runtime_versions": {"biosteam": "2.52.17", "thermosteam": "0.52.16", "biorefineries": "2.34.10"},
               "cited_package_sha256": {"x.py": "c" * 64}}


def _config(**change):
    base = copy.deepcopy(next(r for r in tea._records() if r["label"] == "ldpe-route-c1")["config"])
    base.update(change)
    return base


def _result(msp, case="C1"):
    return {"success": True, "engine_mode": "live", "cache_match_status": "bypassed", "energy_case": case,
            "live_provenance": {"status": "verified", "note": "shared by every plant"},
            "tea": {"msp_usd_per_kg": msp, "tci_usd": msp * 1e7}, "lca": {"gwp_kg_co2e_per_kg": msp / 2},
            "process_details": {"equipment": [{"id": "D1", "cost": msp * 3}]}, "config": {"dropped": True}}


def _build(root: Path, plants, fingerprint=FINGERPRINT):
    """Write partitions and a manifest for (config, result) pairs, the way the repack job does."""
    by_pid = {}
    for config, result in plants:
        key = tea._config_key(config)
        by_pid.setdefault(sweep.partition_id(key), []).append((key, result))
    entries = {pid: sweep.write_partition(root / f"{pid}.sqlite", rows, fingerprint) for pid, rows in by_pid.items()}
    (root / "manifest.json").write_text(json.dumps({"schema": sweep.SCHEMA, "fingerprint": fingerprint,
                                                    "partitions": entries}))
    return entries


def test_signature_matches_the_aws_worked_example():
    """AWS's documented GET Object example (Signature Version 4): our signer must reproduce its signature."""
    headers = sweep.sigv4_headers(
        "GET", "https://examplebucket.s3.amazonaws.com/test.txt", access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1",
        payload_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        now=datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc), extra_headers={"Range": "bytes=0-9"})
    assert headers["Authorization"].endswith(
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in headers["Authorization"]


def test_a_stored_plant_is_served_only_for_its_exact_key_and_engine(tmp_path):
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    plants = [(_config(irr=round(0.10 + i / 100, 2)), _result(1.0 + i)) for i in range(5)]
    _build(remote, plants)
    store = sweep.SweepStore(remote.as_uri(), local, 50 * 1024 * 1024)
    for config, result in plants:
        served = store.lookup(tea._config_key(config), FINGERPRINT)
        expected = {k: v for k, v in result.items() if k != "config"}
        assert served == expected
    assert store.lookup(tea._config_key(_config(irr=0.5)), FINGERPRINT) is None          # a field that differs
    assert store.lookup(tea._config_key(plants[0][0]), {**FINGERPRINT, "worker_source_sha256": "d" * 64}) is None
    assert store.stats["hit"] == 5 and store.stats["miss"] == 1 and store.stats["engine_mismatch"] == 1


def test_a_file_whose_checksum_differs_is_refused_and_not_kept(tmp_path):
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    config = _config()
    entries = _build(remote, [(config, _result(2.0))])
    manifest = json.loads((remote / "manifest.json").read_text())
    pid = next(iter(entries))
    manifest["partitions"][pid]["sha256"] = "0" * 64
    (remote / "manifest.json").write_text(json.dumps(manifest))
    store = sweep.SweepStore(remote.as_uri(), local, 50 * 1024 * 1024)
    assert store.lookup(tea._config_key(config), FINGERPRINT) is None
    assert not list(local.glob("*.sqlite")) and store.stats["download_error"] == 1


def test_the_local_directory_stays_under_its_cap_least_recently_used_first(tmp_path):
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    cases = ["C1", "C2", "C3"]
    plants = [(_config(energy_case=case, irr=round(0.1 + j / 100, 2)), _result(1.0 + j, case)) for case in cases
              for j in range(40)]
    entries = _build(remote, plants)
    biggest = max(e["bytes"] for e in entries.values())
    store = sweep.SweepStore(remote.as_uri(), local, 2 * biggest + 10)   # room for two files, not three
    for case in cases:                                                     # C1, C2, then C3 pushes C1 out
        assert store.lookup(tea._config_key(_config(energy_case=case, irr=0.1)), FINGERPRINT) is not None
    on_disk = list(local.glob("*.sqlite"))
    assert len(on_disk) == 2 and sum(p.stat().st_size for p in on_disk) <= store.max_bytes
    c1 = sweep.partition_id(tea._config_key(_config(energy_case="C1")))
    assert not (local / f"{c1}.sqlite").exists() and store.stats["evicted"] == 1


def test_forty_people_at_once_download_each_file_once_and_all_get_their_plant(tmp_path):
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    plants = [(_config(irr=round(0.05 + i / 100, 2)), _result(1.0 + i / 10)) for i in range(40)]
    _build(remote, plants)
    gets = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            gets.append(self.path)
            return super().do_GET()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(remote)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        store = sweep.SweepStore(f"http://127.0.0.1:{server.server_address[1]}/", local, 50 * 1024 * 1024)
        results = [None] * len(plants)

        def ask(i):
            results[i] = store.lookup(tea._config_key(plants[i][0]), FINGERPRINT)

        threads = [threading.Thread(target=ask, args=(i,)) for i in range(len(plants))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    finally:
        server.shutdown()
    assert all(r is not None and r["tea"]["msp_usd_per_kg"] == p[1]["tea"]["msp_usd_per_kg"]
               for r, p in zip(results, plants))
    assert sorted(set(gets)) == sorted(gets) and len([g for g in gets if g.endswith(".sqlite")]) == 1


def test_an_unreachable_store_answers_nothing_and_raises_nothing(tmp_path):
    store = sweep.SweepStore((tmp_path / "nowhere").as_uri(), tmp_path / "local", 10 * 1024 * 1024)
    assert store.lookup(tea._config_key(_config()), FINGERPRINT) is None
    assert store.stats["manifest_error"] == 1


def test_the_tea_path_serves_a_grid_plant_without_a_live_child(tmp_path, monkeypatch):
    """With a store configured, an exact grid plant comes back as a stored result, labelled as the core grid, and the
    panel's check counts it as stored; a different plant still needs the live engine."""
    fingerprint = tea._sweep_engine_fingerprint()
    if fingerprint is None:
        pytest.skip("this checkout has no live engine files to fingerprint")
    remote = tmp_path / "remote"
    remote.mkdir()
    config = _config(irr=0.123)
    _build(remote, [(config, _result(3.21))], fingerprint)
    monkeypatch.setenv(sweep.URL_ENV, remote.as_uri())
    monkeypatch.setenv(sweep.DIR_ENV, str(tmp_path / "local"))
    monkeypatch.delenv("DISSOLVE_TEA_STORED_RESULTS", raising=False)
    sweep.reset()
    try:
        served = tea._cached_result(dict(config))
        assert served is not None and served["engine_mode"] == "cache" and served["cache_match_status"] == "exact"
        assert served["cache_record_label"].startswith("core-grid:") and served["tea"]["msp_usd_per_kg"] == 3.21
        assert served["config"] == config
        assert tea._would_start_live_child(dict(config), "auto") is False
        assert tea._would_start_live_child(dict(config), "live") is True
        assert tea._would_start_live_child(_config(irr=0.124), "auto") is True
    finally:
        sweep.reset()
