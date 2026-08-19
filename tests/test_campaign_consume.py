"""Bind a registered campaign to lookup without ingesting JSONL into cache."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import campaign_basis, campaign_consume, tea

_SEALED = Path(
    "/home/aaltamimi2/dissolve-v12-campaign/"
    "polymer-solvent-tea-lca-20260818"
)
_CANONICAL = (
    "ef62efb3a708d782ce58cac3295cc9de71b0a7e8d076f6ca79f1ba5830a29636"
)
_APPEND_LOG = (
    "8b11ee68ce9948418872d892076bf275cb8b0dc1f95e72b0af80bbd2ac1eee3a"
)
_OTHER = "ab" * 32


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _write_registry(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "campaign_registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _sealed_entry() -> dict:
    manifest = _SEALED / "manifest.json"
    return {
        "manifest_path": str(manifest),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "append_log_aliases": [_APPEND_LOG],
    }


def _lookup(monkeypatch, registry_path: Path, **kwargs):
    monkeypatch.setenv(
        campaign_consume.REGISTRY_ENV, str(registry_path),
    )
    return _data(tea.lookup_admitted_process_records(**kwargs))


def _minimal_definition(**overrides):
    definition = {
        "schema": campaign_basis.CAMPAIGN_DEFINITION_SCHEMA_V2,
        "fixed_fields": {
            "target_plastic_percent": 55.0,
            "processing_capacity": 20_000.0,
            "energy_case": "C1",
            "precipitation_temperature_c": 35.0,
            "solvent_loss_pct": 0.01,
            "feedstock_distance_km": 0.0,
            "dissolution_capacity": 3.0,
            "labor_cost": 120_000.0,
        },
        "setpoint_rule": {
            "dissolution_temperature_c": (
                "lowest stored grid node from 25 through 160 C"
            ),
        },
        "pair_definitions": [
            {
                "config_sent": {
                    "target_plastic": "LDPE",
                    "solvent": "toluene",
                    "dissolution_temperature_c": 95.0,
                },
            },
        ],
        "runtime_engine_versions": {"python": "3.12.0"},
    }
    definition.update(overrides)
    return definition


def _mini_campaign(
    tmp_path: Path,
    *,
    definition=None,
    rows=None,
    complete: bool = True,
    include_results: bool = True,
):
    root = tmp_path / "mini_campaign"
    root.mkdir()
    run_definition = definition if definition is not None else _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(run_definition)
    run_path = root / "run_definition.json"
    run_path.write_text(json.dumps(run_definition), encoding="utf-8")
    if rows is None:
        rows = [
            {
                "campaign_fingerprint": canonical,
                "polymer": "LDPE",
                "solvent_public_identity": "2,4-Pentanedione",
                "pair_id": "p1",
                "config_sent": {"solvent": "toluene"},
            },
        ]
    rows_path = root / "process_rows.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    if include_results:
        (root / "results.jsonl").write_text(
            json.dumps({
                "campaign_fingerprint": "ff" * 32,
                "should_not": "be_consumed",
            }) + "\n",
            encoding="utf-8",
        )
    manifest = {
        "campaign_fingerprint": canonical,
        "append_log_fingerprint": "cc" * 32,
        "complete": complete,
        "counts": {"attempted": len(rows)},
        "census": {"pair_count": len(run_definition["pair_definitions"])},
        "aggregate_pair_wall_seconds": 10.0 * max(len(rows), 1),
        "artifact_sha256": {
            "run_definition_json": hashlib.sha256(
                run_path.read_bytes()
            ).hexdigest(),
            "process_rows_jsonl": hashlib.sha256(
                rows_path.read_bytes()
            ).hexdigest(),
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    entry = {
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "append_log_aliases": [],
    }
    return {
        "root": root,
        "canonical": canonical,
        "entry": entry,
        "run_path": run_path,
        "rows_path": rows_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "definition": run_definition,
    }


def test_empty_registry_is_unregistered(monkeypatch, tmp_path):
    monkeypatch.delenv(campaign_consume.REGISTRY_ENV, raising=False)
    data = _data(tea.lookup_admitted_process_records(
        source="campaign",
        campaign_fingerprint=_OTHER,
    ))
    assert data["success"] is False
    assert data["error_code"] == "campaign_not_registered"
    assert data["n_registered"] == 0
    assert data["supplied"] == _OTHER


def test_missing_fingerprint_is_first(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch, registry, source="campaign", target_polymer="LDPE",
    )
    assert data["success"] is False
    assert data["error_code"] == "missing_campaign_fingerprint"
    assert "n_registered" not in data


def test_append_log_alias_is_mismatch_not_unregistered(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_APPEND_LOG,
    )
    assert data["success"] is False
    assert data["error_code"] == "campaign_fingerprint_mismatch"
    assert data["supplied"] == _APPEND_LOG
    assert data["canonical"] == _CANONICAL
    assert data["note"] == "append-log"


def test_unknown_digest_is_unregistered_when_another_is_registered(
    monkeypatch, tmp_path,
):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch, registry, source="campaign", campaign_fingerprint=_OTHER,
    )
    assert data["error_code"] == "campaign_not_registered"
    assert data["n_registered"] == 1
    assert data["supplied"] == _OTHER


def test_legal_sealed_bind_does_not_ingest_cache(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    before = len(tea._records())
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
    )
    assert data["success"] is True
    assert data["source"] == "campaign"
    assert data["campaign_fingerprint"] == _CANONICAL
    assert data["append_log_fingerprint"] == _APPEND_LOG
    assert data["campaign_basis_projection"] == "campaign_basis.v1"
    assert data["campaign_basis"]["complete"] is True
    assert data["campaign_basis"]["n_pairs"] == 462
    assert data["n_rows_consumed"] == 462
    assert data["ingested_into_admitted_cache"] is False
    assert "records" not in data
    roles = data["campaign_basis"]["field_role"]
    assert roles["target_mass_percent"]["value"] == pytest.approx(55)
    assert roles["burn_leftover_plastic"]["value"] is False
    assert len(tea._records()) == before == 24
    assert "matching_row_count" not in data


def test_polymer_filter_counts_without_dumping_rows(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        target_polymer="LDPE",
    )
    assert data["success"] is True
    assert data["matching_row_count"] == 60
    assert "records" not in data
    assert len(tea._records()) == 24


def test_sixty_fifteen_c2_is_held_mismatch_not_a_ranking(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        process_config={
            "target_mass_percent": 60.0,
            "processing_capacity_mt_per_yr": 15_000.0,
        },
        energy_cases=["C2"],
    )
    assert data["success"] is False
    assert data["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in data["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]
    assert data["n_held_mismatches"] == 3
    quote = data["live_rerun_quote"]
    assert quote["n_pairs"] == 462
    assert quote["estimated_wall_seconds"] == pytest.approx(6991.363639038995)


def test_worker_names_bind_on_consume(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        process_config={
            "target_plastic_percent": 60.0,
            "processing_capacity": 15_000.0,
            "energy_case": "C2",
        },
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in data["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]


def test_burn_true_is_switch_mismatch_on_lookup(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        process_config={"burn_leftover_plastic": True},
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    assert data["mismatches"][0]["field"] == "burn_leftover_plastic"
    assert data["mismatches"][0]["campaign_value"] is False
    assert data["mismatches"][0]["requested_value"] is True


def test_flipped_jsonl_byte_is_artifact_integrity_mismatch(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path)
    payload = mini["rows_path"].read_bytes()
    mini["rows_path"].write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_artifact_integrity_mismatch"
    assert data["artifact"] == "process_rows.jsonl"


def test_run_definition_field_change_is_artifact_integrity_mismatch(
    monkeypatch, tmp_path,
):
    mini = _mini_campaign(tmp_path)
    definition = json.loads(mini["run_path"].read_text(encoding="utf-8"))
    definition["runtime_engine_versions"]["python"] = "0.0.0"
    mini["run_path"].write_text(json.dumps(definition), encoding="utf-8")
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_artifact_integrity_mismatch"
    assert data["artifact"] == "run_definition.json"


def test_lockstep_artifact_and_manifest_without_registry_is_manifest_mismatch(
    monkeypatch, tmp_path,
):
    mini = _mini_campaign(tmp_path)
    payload = mini["rows_path"].read_bytes()
    mini["rows_path"].write_bytes(payload + b"\n")
    manifest = json.loads(mini["manifest_path"].read_text(encoding="utf-8"))
    manifest["artifact_sha256"]["process_rows_jsonl"] = hashlib.sha256(
        mini["rows_path"].read_bytes()
    ).hexdigest()
    mini["manifest_path"].write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8",
    )
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_manifest_integrity_mismatch"


def test_stale_canonical_after_python_change_is_fingerprint_mismatch(
    monkeypatch, tmp_path,
):
    mini = _mini_campaign(tmp_path)
    stale = mini["canonical"]
    definition = json.loads(mini["run_path"].read_text(encoding="utf-8"))
    definition["runtime_engine_versions"]["python"] = "3.11.0"
    mini["run_path"].write_text(json.dumps(definition), encoding="utf-8")
    new_digest = campaign_consume.canonical_json_digest(definition)
    assert new_digest != stale
    manifest = json.loads(mini["manifest_path"].read_text(encoding="utf-8"))
    manifest["artifact_sha256"]["run_definition_json"] = hashlib.sha256(
        mini["run_path"].read_bytes()
    ).hexdigest()
    manifest["campaign_fingerprint"] = new_digest
    mini["manifest_path"].write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8",
    )
    entry = dict(mini["entry"])
    entry["manifest_sha256"] = hashlib.sha256(
        mini["manifest_path"].read_bytes()
    ).hexdigest()
    registry = _write_registry(tmp_path, {stale: entry})
    data = _lookup(
        monkeypatch, registry, source="campaign", campaign_fingerprint=stale,
    )
    assert data["error_code"] == "campaign_fingerprint_mismatch"
    assert data["supplied"] == stale
    assert data["computed"] == new_digest
    assert data["manifest"] == new_digest


def test_duplicate_registry_key_is_ambiguous(monkeypatch, tmp_path):
    digest = "aa" * 32
    path = tmp_path / "dup.json"
    path.write_text(
        "{"
        f'"{digest}": {{"manifest_path": "/x", "manifest_sha256": "{"0" * 64}"}}, '
        f'"{digest}": {{"manifest_path": "/y", "manifest_sha256": "{"1" * 64}"}}'
        "}",
        encoding="utf-8",
    )
    data = _lookup(
        monkeypatch, path, source="campaign", campaign_fingerprint=digest,
    )
    assert data["error_code"] == "ambiguous_campaign_registry"


def test_alias_claimed_by_two_canonicals_is_ambiguous(monkeypatch, tmp_path):
    alias = "dd" * 32
    entries = {
        "aa" * 32: {
            "manifest_path": "/a",
            "manifest_sha256": "0" * 64,
            "append_log_aliases": [alias],
        },
        "bb" * 32: {
            "manifest_path": "/b",
            "manifest_sha256": "1" * 64,
            "append_log_aliases": [alias],
        },
    }
    registry = _write_registry(tmp_path, entries)
    data = _lookup(
        monkeypatch, registry, source="campaign", campaign_fingerprint="aa" * 32,
    )
    assert data["error_code"] == "ambiguous_campaign_registry"


def test_default_lookup_stays_on_the_admitted_cache(monkeypatch, tmp_path):
    monkeypatch.delenv(campaign_consume.REGISTRY_ENV, raising=False)
    data = _data(tea.lookup_admitted_process_records(target_polymer="LDPE"))
    assert data["success"] is True
    assert data["engine_mode"] == "cache"
    assert data["record_count"] >= 1
    assert len(tea._records()) == 24


def test_public_solvent_identity_matches_aliases(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        solvent="acetylacetone",
    )
    assert data["success"] is True
    assert data["matching_row_count"] == 1
    lowercase_only = (
        "acetylacetone".casefold() == "2,4-Pentanedione".casefold()
    )
    assert lowercase_only is False


def test_results_jsonl_is_not_consumed(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path, include_results=True)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["success"] is True
    assert data["n_rows_consumed"] == 1


def test_mixed_row_fingerprint_is_mismatch(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
    mini = _mini_campaign(
        tmp_path,
        definition=definition,
        rows=[
            {
                "campaign_fingerprint": canonical,
                "polymer": "LDPE",
                "solvent_public_identity": "Toluene",
                "pair_id": "p1",
            },
            {
                "campaign_fingerprint": "ee" * 32,
                "polymer": "HDPE",
                "solvent_public_identity": "Toluene",
                "pair_id": "p2",
            },
        ],
    )
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_fingerprint_mismatch"
    assert data["pair_id"] == "p2"


def test_incomplete_campaign_refuses_unless_named(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path, complete=False)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_incomplete"
    allowed = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        allow_partial_campaign=True,
    )
    assert allowed["success"] is True


def test_derived_temperature_not_produced_is_mismatch(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        dissolution_temperature_c=1.0,
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    assert data["mismatches"][-1]["field"] == "dissolution_temperature_c"
    matching = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        dissolution_temperature_c=95.0,
    )
    assert matching["success"] is True
