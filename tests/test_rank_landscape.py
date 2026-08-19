"""rank_landscape over campaign process_rows: usable projection, not a leftover-to-CHP ranking."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import campaign_basis, campaign_consume, landscape, tea

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


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
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


def _rank(monkeypatch, registry_path: Path, **kwargs):
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry_path))
    return _data(tea.rank_landscape(**kwargs))


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
            "dissolution_temperature_c": "lowest stored grid node",
        },
        "pair_definitions": [
            {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
        ],
        "runtime_engine_versions": {"python": "3.12.0"},
    }
    definition.update(overrides)
    return definition


def _success_row(canonical, pair_id, polymer, solvent, msp, gwp):
    return {
        "campaign_fingerprint": canonical,
        "outcome": "success",
        "error_type": None,
        "pair_id": pair_id,
        "polymer": polymer,
        "solvent_public_identity": solvent,
        "standing": {
            "can_cite_as_validated_process": False,
            "process_parameter_status": {
                "msp_usd_per_kg": "provisional_live_process_parameters",
            },
        },
        "comparison_row": {
            "msp_usd_per_kg": msp,
            "gwp_kg_co2e_per_kg": gwp,
            "lca_coverage": {
                "status": "partial",
                "lca_metrics_status": "partial",
            },
        },
    }


def _fail_row(canonical, pair_id, polymer, error_type):
    return {
        "campaign_fingerprint": canonical,
        "outcome": "failure",
        "error_type": error_type,
        "pair_id": pair_id,
        "polymer": polymer,
        "solvent_public_identity": "Toluene",
        "standing": {},
        "comparison_row": {
            "msp_usd_per_kg": None,
            "gwp_kg_co2e_per_kg": None,
        },
    }


def _mini(tmp_path, rows, definition=None):
    root = tmp_path / "mini_campaign"
    root.mkdir()
    run_definition = definition if definition is not None else _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(run_definition)
    run_path = root / "run_definition.json"
    run_path.write_text(json.dumps(run_definition), encoding="utf-8")
    rows_path = root / "process_rows.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    (root / "results.jsonl").write_text("{}\n", encoding="utf-8")
    manifest = {
        "campaign_fingerprint": canonical,
        "append_log_fingerprint": "cc" * 32,
        "complete": True,
        "counts": {"attempted": len(rows)},
        "census": {"pair_count": len(run_definition["pair_definitions"])},
        "aggregate_pair_wall_seconds": 10.0,
        "artifact_sha256": {
            "run_definition_json": hashlib.sha256(run_path.read_bytes()).hexdigest(),
            "process_rows_jsonl": hashlib.sha256(rows_path.read_bytes()).hexdigest(),
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    entry = {
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "append_log_aliases": [],
    }
    return canonical, entry


def test_missing_fingerprint_is_first(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(monkeypatch, registry)
    assert data["success"] is False
    assert data["error_code"] == "missing_campaign_fingerprint"


def test_held_mismatch_is_not_a_ranking(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        process_config={"burn_leftover_plastic": True},
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    assert data["mismatches"][0]["field"] == "burn_leftover_plastic"
    assert "landscape_points" not in data
    assert "frontier_points" not in data


def test_sixty_fifteen_c2_is_not_a_ranking(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        process_config={
            "target_mass_percent": 60.0,
            "processing_capacity_mt_per_yr": 15_000.0,
        },
        energy_cases=["C2"],
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in data["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]
    assert "landscape_points" not in data


def test_exclude_safety_fail_is_unknown_extra(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        exclude_safety_fail=True,
    )
    assert data["error_code"] == "unknown_process_field"
    assert data["extra_keys"] == ["exclude_safety_fail"]


def test_residual_route_is_unwired(monkeypatch, tmp_path):
    monkeypatch.delenv(campaign_consume.REGISTRY_ENV, raising=False)
    data = _data(tea.rank_landscape(
        source="residual_route", campaign_fingerprint=_CANONICAL,
    ))
    assert data["error_code"] == "tool_not_wired"


def test_epsilon_not_applicable_on_process_rows(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        operation="epsilon",
    )
    assert data["error_code"] == "not_applicable_in_source"


def test_one_usable_row_is_landscape_too_small(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4),
        _fail_row(canonical, "p2", "LDPE", "priced_solvent_unmodellable"),
    ], definition=definition)
    registry = _write_registry(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert data["error_code"] == "landscape_too_small"
    assert data["n_usable"] == 1
    assert data["excluded_by_error_type"]["priced_solvent_unmodellable"] == 1
    assert data["ingested_into_admitted_cache"] is False


def test_two_point_total_order_serves_sparse_frontier(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4),
        _success_row(canonical, "p2", "LDPE", "Xylene", 2.0, 0.8),
    ], definition=definition)
    registry = _write_registry(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert data["success"] is True
    assert data["n_landscape_points"] == 2
    assert data["n_landscape_points"] == len(data["landscape_points"])
    assert data["n_frontier_points"] == 1
    assert data["n_frontier_points"] == len(data["frontier_points"])
    assert data["sparse_frontier"] is True
    assert data["cheapest_equals_lowest_y"] is True
    assert data["knee_status"] == "endpoint_only_no_interior_knee"
    assert data["frontier_tradeoff"] is None
    assert data["frontier_fraction"] == pytest.approx(0.5)
    for point in data["landscape_points"]:
        assert point["safety_standing"]["status"] == "not_requested"
        assert point["standing"]
        assert point["lca_coverage"]
    assert "engine_envelope" not in data["landscape_points"][0]
    assert len(tea._records()) == 24


def test_pareto_returns_landscape_and_frontier(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(canonical, "a", "LDPE", "Toluene", 1.0, 1.0),
        _success_row(canonical, "b", "LDPE", "Xylene", 2.0, 2.0),
        _success_row(canonical, "c", "LDPE", "Heptane", 1.5, 0.5),
        _fail_row(canonical, "d", "LDPE", "lca_factor_basis_unavailable"),
    ], definition=definition)
    registry = _write_registry(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert data["success"] is True
    assert data["n_landscape_points"] == 3
    assert data["n_frontier_points"] == 2
    assert data["excluded_count"] == 1
    assert data["excluded_by_error_type"] == {
        "lca_factor_basis_unavailable": 1,
    }
    ids = {point["pair_id"] for point in data["frontier_points"]}
    assert ids == {"a", "c"}
    assert data["cheapest_equals_lowest_y"] is False
    assert data["frontier_tradeoff"]["delta_x"] == pytest.approx(0.5)
    assert data["ingested_into_admitted_cache"] is False


def test_default_grouping_does_not_mix_polymers(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(canonical, "a", "LDPE", "Toluene", 1.0, 1.0),
        _success_row(canonical, "b", "LDPE", "Xylene", 2.0, 0.5),
        _success_row(canonical, "c", "HDPE", "Toluene", 1.1, 1.1),
        _success_row(canonical, "d", "HDPE", "Xylene", 2.1, 0.4),
    ], definition=definition)
    registry = _write_registry(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert data["success"] is True
    assert "grouped_fronts" in data
    assert data["n_groups"] == 2
    assert "frontier_points" not in data
    polymers = [block["target_polymer"] for block in data["grouped_fronts"]]
    assert polymers == ["LDPE", "HDPE"]
    for block in data["grouped_fronts"]:
        assert block["n_landscape_points"] == 2
        assert block["n_landscape_points"] == len(block["landscape_points"])
        assert block["n_frontier_points"] == len(block["frontier_points"])
        names = {point["target_polymer"] for point in block["landscape_points"]}
        assert names == {block["target_polymer"]}
    mixed = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=canonical,
        polymer_grouping="mixed_polymer",
    )
    assert mixed["n_landscape_points"] == 4
    assert "grouped_fronts" not in mixed


def test_sort_has_no_frontier_array(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(canonical, "b", "LDPE", "Xylene", 2.0, 0.8),
        _success_row(canonical, "a", "LDPE", "Toluene", 1.0, 0.4),
    ], definition=definition)
    registry = _write_registry(tmp_path, {canonical: entry})
    data = _rank(
        monkeypatch, registry, campaign_fingerprint=canonical, operation="sort",
    )
    assert data["n_returned"] == 2
    assert [point["pair_id"] for point in data["landscape_points"]] == ["a", "b"]
    assert "frontier_points" not in data
    assert "frontier_fraction" not in data


def test_ldpe_sealed_slice_usable_projection(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        target_polymer="LDPE",
    )
    assert data["success"] is True
    assert data["campaign_fingerprint"] == _CANONICAL
    assert data["n_landscape_points"] == 54
    assert data["n_landscape_points"] == len(data["landscape_points"])
    assert data["n_frontier_points"] == len(data["frontier_points"])
    assert data["n_frontier_points"] >= 1
    assert data["excluded_count"] == 6
    assert data["excluded_by_error_type"] == {
        "priced_solvent_unmodellable": 4,
        "lca_factor_basis_unavailable": 2,
    }
    assert data["ingested_into_admitted_cache"] is False
    assert len(tea._records()) == 24
    for point in data["landscape_points"]:
        assert point["target_polymer"] == "LDPE"
        assert point["safety_standing"]["status"] == "not_requested"
        assert point["standing"]
        assert math.isfinite(float(point["msp_usd_per_kg"]))
        assert math.isfinite(float(point["gwp_kg_co2e_per_kg"]))
    if data["n_frontier_points"] == 1 or data["cheapest_equals_lowest_y"]:
        assert data["sparse_frontier"] is True
        assert data["frontier_tradeoff"] is None


def test_mixed_polymer_sealed_counts_bind(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        polymer_grouping="mixed_polymer",
    )
    assert data["n_landscape_points"] == 408
    assert data["n_landscape_points"] == len(data["landscape_points"])
    assert data["n_frontier_points"] == len(data["frontier_points"])
    assert data["n_frontier_points"] == 3
    assert data["excluded_count"] == 54
    assert data["excluded_by_error_type"] == {
        "priced_solvent_unmodellable": 39,
        "lca_factor_basis_unavailable": 15,
    }
    assert data["frontier_fraction"] == pytest.approx(3 / 408)
    assert len(tea._records()) == 24


def test_quantile_type7_matches_the_spec():
    assert landscape.hyndman_fan_type7([10.0], 0.05) == 10.0
    sample = [1.0, 2.0, 3.0, 4.0]
    assert landscape.hyndman_fan_type7(sample, 0.0) == 1.0
    assert landscape.hyndman_fan_type7(sample, 1.0) == 4.0
    assert landscape.hyndman_fan_type7(sample, 0.5) == pytest.approx(2.5)


def test_cache_lookup_still_default(monkeypatch, tmp_path):
    monkeypatch.delenv(campaign_consume.REGISTRY_ENV, raising=False)
    data = _data(tea.lookup_admitted_process_records(target_polymer="LDPE"))
    assert data["success"] is True
    assert data["engine_mode"] == "cache"
