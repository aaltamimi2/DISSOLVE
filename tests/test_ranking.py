"""Ranking tests."""
from __future__ import annotations

import hashlib
import inspect
import json
import math
from pathlib import Path

import pytest

from dissolve import (
    safety,
    separation,
    tea,
    tea_ranking,
)
from dissolve.agent import (
    CONSUMERS,
    SYSTEM_PROMPT,
    dispatch,
    source_basis_for,
    tool_schemas,
)
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session, store_handle


@pytest.fixture(autouse=True)
def _live_tea_works(monkeypatch, tmp_path):
    """TEA answers only when live TEA works; these tests stand in a working engine (tests of the check itself
    override this) and never see this checkout's own live environment (.venv-tea, vendor/plastics)."""
    monkeypatch.setattr(tea, "_live_tea_blocker", lambda: None)
    monkeypatch.setattr(tea, "_REPO_TEA_PYTHON", tmp_path / "no-venv-tea" / "python")
    monkeypatch.setattr(tea.tea_polymer_parameters, "VENDORED_PLASTICS", tmp_path / "no-vendored-plastics")

# --- from test_rank_campaign_handle.py: A campaign lookup handle is a process_rows rank source. Not a live child.
_SEALED = tea_ranking.SHIPPED_CAMPAIGN


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


def _write_registry(tmp_path: Path, entries: dict) -> dict:
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


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("campaign-handle rank must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _count_jsonl(monkeypatch):
    reads = {"load_filtered_rows": 0}
    real = tea_ranking.load_filtered_rows

    def wrapped(*args, **kwargs):
        reads["load_filtered_rows"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tea_ranking, "load_filtered_rows", wrapped)
    return reads


def _record_by_label(label: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == label
    )


def _public_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def test_campaign_lookup_handle_ranks_without_rereading_jsonl(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
        ))
        assert lookup.get("success") is True
        assert len(lookup["comparison_rows"]) == 462
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        payload = _data(tea.rank_landscape(handle=handle))
    assert payload.get("success") is True
    assert payload["analysis_type"] == "campaign_process_rows_landscape"
    assert payload["n_usable"] == 408
    assert payload["n_groups"] == 10
    assert len(payload["grouped_fronts"]) == 10
    assert payload["ingested_into_admitted_cache"] is False
    assert payload["campaign_fingerprint"] == _CANONICAL
    assert payload["campaign_basis"]["n_pairs"] == 462
    assert reads["load_filtered_rows"] == 0
    assert len(tea._records()) == 24


def test_campaign_handle_held_mismatch_is_not_a_ranking(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        burned = _data(tea.rank_landscape(
            handle=handle,
            process_config={"burn_leftover_plastic": True},
        ))
        shifted = _data(tea.rank_landscape(
            handle=handle,
            process_config={
                "target_mass_percent": 60.0,
                "processing_capacity_mt_per_yr": 15_000.0,
            },
            energy_cases=["C2"],
        ))
    assert burned["error_code"] == "campaign_basis_mismatch"
    assert burned["mismatches"][0]["field"] == "burn_leftover_plastic"
    assert burned["mismatches"][0]["campaign_value"] is False
    assert burned["mismatches"][0]["requested_value"] is True
    assert "landscape_points" not in burned
    assert "grouped_fronts" not in burned
    assert shifted["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in shifted["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]
    assert shifted["live_rerun_quote"]["n_pairs"] == 462
    assert "landscape_points" not in shifted
    assert reads["load_filtered_rows"] == 0


def test_ldpe_campaign_lookup_handle_is_one_polymer_front(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
            target_polymer="LDPE",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        payload = _data(tea.rank_landscape(handle=handle))
    assert lookup["matching_row_count"] == 60
    assert payload.get("success") is True
    assert payload["n_usable"] == 54
    assert payload["n_landscape_points"] == 54
    assert payload["n_landscape_points"] == len(payload["landscape_points"])
    assert {point["target_polymer"] for point in payload["landscape_points"]} == {
        "LDPE",
    }
    assert "grouped_fronts" not in payload
    assert reads["load_filtered_rows"] == 0


def test_disagreeing_fingerprint_on_campaign_handle_mismatches(
    monkeypatch, tmp_path,
):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
            target_polymer="LDPE",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        payload = _data(tea.rank_landscape(
            handle=handle,
            campaign_fingerprint="ff" * 32,
        ))
    assert payload["error_code"] == "campaign_fingerprint_mismatch"
    assert "grouped_fronts" not in payload
    assert "landscape_points" not in payload


def test_evaluate_handle_still_ignores_campaign_held_constraints(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(c1), _public_from_record(c2)],
            engine_mode="auto",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data(tea.rank_landscape(
            handle=handle,
            process_config={"burn_leftover_plastic": True},
        ))
    assert payload.get("success") is True
    assert payload["n_usable"] == 2
    assert payload.get("error_code") != "campaign_basis_mismatch"
    assert payload["analysis_type"] == "process_rows_landscape"


def test_campaign_lookup_basis_is_not_cache_exact():
    campaign = {
        "success": True,
        "source": "campaign",
        "engine_mode": "campaign",
        "comparison_rows": [{"polymer": "LDPE"}, {"polymer": "PET"}],
    }
    cache = {
        "success": True,
        "engine_mode": "cache",
        "cache_match_status": "exact",
        "comparison_rows": [{"engine_mode": "cache"}],
    }
    assert source_basis_for(
        "evaluate_process", campaign, {},
    ) == "campaign_process_rows"
    assert source_basis_for(
        "evaluate_process", cache, {},
    ) == "tea_cache_exact"
    ranked = {
        "success": True,
        "source": "process_rows",
        "engine_mode": "campaign",
        "grouped_fronts": [{"target_polymer": "LDPE"}],
    }
    assert source_basis_for("rank_landscape", ranked, {}) == "campaign_process_rows"
    assert "campaign_process_rows" in SYSTEM_PROMPT
    assert "not tea_cache_exact" in SYSTEM_PROMPT


def test_dispatch_mints_a_campaign_lookup_handle(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={
                "source": "campaign",
                "campaign_fingerprint": _CANONICAL,
            },
        )
        assert lookup.get("available") is True
        assert lookup.get("refusal") != "no_honest_basis"
        assert lookup["source_basis"] == "campaign_process_rows"
        assert lookup["source_basis"] != "tea_cache_exact"
        handle = lookup.get("handle")
        assert handle
        assert lookup["total"] == 462
        ranked = dispatch("rank_landscape", handle=handle)
    assert ranked.get("available") is True
    assert ranked.get("refusal") != "no_honest_basis"
    assert ranked["source_basis"] == "campaign_process_rows"
    data = ranked.get("data") or {}
    assert data.get("n_usable") == 408
    assert data.get("n_groups") == 10
    assert data.get("ingested_into_admitted_cache") is False
    assert reads["load_filtered_rows"] == 0
    assert len(tea._records()) == 24


# --- from test_rank_handle_inherit.py: A rank_landscape handle can be a tornado inherit source. Not a live child.
def _route_c1() -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == "ldpe-route-c1"
    )


def _normalized(record: dict, **overrides) -> dict:
    cfg = dict(record["config"])
    cfg.update(overrides)
    return {key: cfg[key] for key in tea._CONFIG_FIELDS}


def _success_row(
    canonical, pair_id, polymer, solvent, msp, gwp, *, normalized=None,
):
    row = {
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
    if normalized is not None:
        row["config_normalized_twelve"] = dict(normalized)
    return row


def _write_registry_rank_handle_inherit(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "campaign_registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _minimal_definition(**overrides):
    definition = {
        "schema": tea_ranking.CAMPAIGN_DEFINITION_SCHEMA_V2,
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


def _mini(tmp_path, rows, definition=None):
    root = tmp_path / "mini_campaign"
    root.mkdir()
    run_definition = definition if definition is not None else _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(run_definition)
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


def _rank(monkeypatch, registry_path: Path, **kwargs):
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry_path))
    return _data(tea.rank_landscape(**kwargs))


def _forbid_live_rank_handle_inherit(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("rank inherit must not start live")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_compact_point_carries_the_executed_twelve():
    record = _route_c1()
    cfg = record["config"]
    point = tea_ranking.compact_process_row({
        "pair_id": "p1",
        "polymer": cfg["target_plastic"],
        "solvent_public_identity": "Dodecane",
        "campaign_fingerprint": "ab" * 32,
        "outcome": "success",
        "config_normalized_twelve": _normalized(record),
        "standing": {"process_parameter_status": {"msp_usd_per_kg": "x"}},
        "comparison_row": {
            "msp_usd_per_kg": 1.0,
            "gwp_kg_co2e_per_kg": 0.5,
            "lca_coverage": {"status": "partial"},
        },
    })
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert name in point
    assert point["target_polymer"] == cfg["target_plastic"]
    assert point["solvent"] == "Dodecane"
    assert point["labor_cost_usd_per_employee_yr"] == cfg["labor_cost"]
    assert "engine_envelope" not in point


def test_rank_points_without_normalized_twelve_are_not_an_inherit_source(
    monkeypatch, tmp_path,
):
    _forbid_live_rank_handle_inherit(monkeypatch)
    definition = _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(canonical, "a", "LDPE", "Toluene", 1.0, 0.4),
        _success_row(canonical, "b", "LDPE", "Xylene", 2.0, 0.8),
    ], definition=definition)
    registry = _write_registry_rank_handle_inherit(tmp_path, {canonical: entry})
    ranked = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    session = new_session()
    with bind_tool_session(session):
        handle = store_handle(
            session,
            tool="rank_landscape",
            source_basis="campaign_process_rows",
            data=ranked,
        )
        payload = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            row_id=1,
            engine_mode="auto",
        ))
    assert payload.get("error_code") == "not_economics_handle"


def test_tornado_inherits_from_a_ranked_cache_identity(monkeypatch, tmp_path):
    record = _route_c1()
    cfg = record["config"]
    _forbid_live_rank_handle_inherit(monkeypatch)
    definition = _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(
            canonical, "cache-hit", cfg["target_plastic"], "Dodecane",
            1.0, 0.4, normalized=_normalized(record),
        ),
        _success_row(
            canonical, "other", cfg["target_plastic"], "Xylene",
            2.0, 0.8, normalized=_normalized(record, solvent="Xylene"),
        ),
    ], definition=definition)
    registry = _write_registry_rank_handle_inherit(tmp_path, {canonical: entry})
    ranked = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert ranked.get("success") is True
    point = next(
        item for item in ranked["landscape_points"]
        if item["pair_id"] == "cache-hit"
    )
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert point[name] is not None
    session = new_session()
    with bind_tool_session(session):
        handle = store_handle(
            session,
            tool="rank_landscape",
            source_basis="campaign_process_rows",
            data=ranked,
        )
        missing = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            engine_mode="auto",
            analysis_mode="tornado",
        ))
        tornado = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            row_id="cache-hit",
            engine_mode="auto",
            analysis_mode="tornado",
        ))
    assert missing.get("error_code") == "ambiguous_handle_row"
    assert tornado.get("success") is True
    origin = tornado["field_origin"]
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert origin[name] == "inherited"
    assert tornado["polymer"] == cfg["target_plastic"]
    prices = {float(row["value"]) for row in tornado["sensitivity_rows"]}
    assert float(cfg["solvent_price"]) in prices
    assert len(prices) >= 2


def test_grouped_fronts_flatten_for_inherit(monkeypatch, tmp_path):
    record = _route_c1()
    cfg = record["config"]
    _forbid_live_rank_handle_inherit(monkeypatch)
    definition = _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(
            canonical, "cache-hit", "LDPE", "Dodecane",
            1.0, 0.4, normalized=_normalized(record),
        ),
        _success_row(
            canonical, "ldpe-b", "LDPE", "Xylene",
            2.0, 0.8, normalized=_normalized(record, solvent="Xylene"),
        ),
        _success_row(
            canonical, "hdpe-a", "HDPE", "Dodecane",
            1.1, 0.5, normalized=_normalized(
                record, target_plastic="HDPE",
            ),
        ),
        _success_row(
            canonical, "hdpe-b", "HDPE", "Xylene",
            2.1, 0.9, normalized=_normalized(
                record, target_plastic="HDPE", solvent="Xylene",
            ),
        ),
    ], definition=definition)
    registry = _write_registry_rank_handle_inherit(tmp_path, {canonical: entry})
    ranked = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert "grouped_fronts" in ranked
    assert "landscape_points" not in ranked
    session = new_session()
    with bind_tool_session(session):
        handle = store_handle(
            session,
            tool="rank_landscape",
            source_basis="campaign_process_rows",
            data=ranked,
        )
        missing = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            engine_mode="auto",
        ))
        tornado = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            row_id="cache-hit",
            engine_mode="auto",
            analysis_mode="tornado",
        ))
    assert missing.get("error_code") == "ambiguous_handle_row"
    assert missing.get("n_rows") == 4
    assert tornado.get("success") is True
    assert tornado["field_origin"]["target_polymer"] == "inherited"
    assert tornado["polymer"] == "LDPE"


# --- from test_rank_landscape.py: rank_landscape over campaign process_rows: usable projection, not a leftover-to-CHP ranking.
def _write_registry_rank_landscape(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "campaign_registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _success_row_rank_landscape(canonical, pair_id, polymer, solvent, msp, gwp):
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


def test_missing_fingerprint_is_first(monkeypatch, tmp_path):
    registry = _write_registry_rank_landscape(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(monkeypatch, registry)
    assert data["success"] is False
    assert data["error_code"] == "missing_campaign_fingerprint"


def test_held_mismatch_is_not_a_ranking(monkeypatch, tmp_path):
    registry = _write_registry_rank_landscape(tmp_path, {_CANONICAL: _sealed_entry()})
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
    registry = _write_registry_rank_landscape(tmp_path, {_CANONICAL: _sealed_entry()})
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
    registry = _write_registry_rank_landscape(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        exclude_safety_fail=True,
    )
    assert data["error_code"] == "unknown_process_field"
    assert data["extra_keys"] == ["exclude_safety_fail"]


def test_evaluated_safety_standing_is_carried_not_filtered(monkeypatch, tmp_path):
    definition = _minimal_definition(pair_definitions=[
        {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
        {"config_sent": {"target_plastic": "LDPE", "solvent": "xylene"}},
    ])
    canonical = tea_ranking.canonical_json_digest(definition)
    toluene = _success_row_rank_landscape(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4)
    toluene["safety_standing"] = {
        "status": "evaluated",
        "safety_profile": {"ghs_signal_word": "Danger"},
    }
    xylene = _success_row_rank_landscape(canonical, "p2", "LDPE", "Xylene", 2.0, 0.8)
    canonical, entry = _mini(
        tmp_path, [toluene, xylene], definition=definition,
    )
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert data["success"] is True
    assert data["n_usable"] == 2
    assert data["n_landscape_points"] == 2
    assert data["safety_standing_policy"] == "carried_not_filtered"
    by_id = {point["pair_id"]: point for point in data["landscape_points"]}
    assert by_id["p1"]["safety_standing"]["status"] == "evaluated"
    assert by_id["p1"]["safety_standing"]["safety_profile"] == {
        "ghs_signal_word": "Danger",
    }
    assert by_id["p2"]["safety_standing"] == {"status": "not_requested"}
    assert by_id["p1"]["safety_standing"] != by_id["p2"]["safety_standing"]
    assert data.get("excluded_count") == 0
    assert "GSK-fail" not in (data.get("excluded_by_error_type") or {})


def test_unavailable_safety_standing_is_carried(monkeypatch, tmp_path):
    definition = _minimal_definition(pair_definitions=[
        {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
        {"config_sent": {"target_plastic": "LDPE", "solvent": "xylene"}},
    ])
    canonical = tea_ranking.canonical_json_digest(definition)
    missing = _success_row_rank_landscape(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4)
    missing["safety_standing"] = {"status": "unavailable"}
    sibling = _success_row_rank_landscape(canonical, "p2", "LDPE", "Xylene", 2.0, 0.8)
    canonical, entry = _mini(
        tmp_path, [missing, sibling], definition=definition,
    )
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    by_id = {point["pair_id"]: point for point in data["landscape_points"]}
    assert by_id["p1"]["safety_standing"] == {"status": "unavailable"}
    assert by_id["p2"]["safety_standing"]["status"] == "not_requested"
    assert data["n_usable"] == 2


def test_illegal_safety_status_is_not_copied(monkeypatch, tmp_path):
    definition = _minimal_definition(pair_definitions=[
        {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
        {"config_sent": {"target_plastic": "LDPE", "solvent": "xylene"}},
    ])
    canonical = tea_ranking.canonical_json_digest(definition)
    bad = _success_row_rank_landscape(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4)
    bad["safety_standing"] = {"status": "fail", "excluded": True}
    sibling = _success_row_rank_landscape(canonical, "p2", "LDPE", "Xylene", 2.0, 0.8)
    canonical, entry = _mini(
        tmp_path, [bad, sibling], definition=definition,
    )
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    by_id = {point["pair_id"]: point for point in data["landscape_points"]}
    assert by_id["p1"]["safety_standing"] == {"status": "not_requested"}
    assert "excluded" not in by_id["p1"]["safety_standing"]
    assert data["n_usable"] == 2


def test_residual_route_campaign_fingerprint_is_not_applicable(monkeypatch, tmp_path):
    monkeypatch.delenv(tea_ranking.REGISTRY_ENV, raising=False)
    data = _data(tea.rank_landscape(
        source="residual_route", campaign_fingerprint=_CANONICAL,
    ))
    assert data["error_code"] == "not_applicable_in_source"
    assert data["source"] == "residual_route"
    assert data["inapplicable_fields"] == ["campaign_fingerprint"]
    assert data["error_code"] != "tool_not_wired"


def test_epsilon_not_applicable_on_process_rows(monkeypatch, tmp_path):
    registry = _write_registry_rank_landscape(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        operation="epsilon",
    )
    assert data["error_code"] == "not_applicable_in_source"


def test_one_usable_row_is_landscape_too_small(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row_rank_landscape(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4),
        _fail_row(canonical, "p2", "LDPE", "priced_solvent_unmodellable"),
    ], definition=definition)
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
    data = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    assert data["error_code"] == "landscape_too_small"
    assert data["n_usable"] == 1
    assert data["excluded_by_error_type"]["priced_solvent_unmodellable"] == 1
    assert data["ingested_into_admitted_cache"] is False


def test_two_point_total_order_serves_sparse_frontier(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row_rank_landscape(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4),
        _success_row_rank_landscape(canonical, "p2", "LDPE", "Xylene", 2.0, 0.8),
    ], definition=definition)
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
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
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row_rank_landscape(canonical, "a", "LDPE", "Toluene", 1.0, 1.0),
        _success_row_rank_landscape(canonical, "b", "LDPE", "Xylene", 2.0, 2.0),
        _success_row_rank_landscape(canonical, "c", "LDPE", "Heptane", 1.5, 0.5),
        _fail_row(canonical, "d", "LDPE", "lca_factor_basis_unavailable"),
    ], definition=definition)
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
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
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row_rank_landscape(canonical, "a", "LDPE", "Toluene", 1.0, 1.0),
        _success_row_rank_landscape(canonical, "b", "LDPE", "Xylene", 2.0, 0.5),
        _success_row_rank_landscape(canonical, "c", "HDPE", "Toluene", 1.1, 1.1),
        _success_row_rank_landscape(canonical, "d", "HDPE", "Xylene", 2.1, 0.4),
    ], definition=definition)
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
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
    canonical = tea_ranking.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row_rank_landscape(canonical, "b", "LDPE", "Xylene", 2.0, 0.8),
        _success_row_rank_landscape(canonical, "a", "LDPE", "Toluene", 1.0, 0.4),
    ], definition=definition)
    registry = _write_registry_rank_landscape(tmp_path, {canonical: entry})
    data = _rank(
        monkeypatch, registry, campaign_fingerprint=canonical, operation="sort",
    )
    assert data["n_returned"] == 2
    assert [point["pair_id"] for point in data["landscape_points"]] == ["a", "b"]
    assert "frontier_points" not in data
    assert "frontier_fraction" not in data


def test_ldpe_sealed_slice_usable_projection(monkeypatch, tmp_path):
    registry = _write_registry_rank_landscape(tmp_path, {_CANONICAL: _sealed_entry()})
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
    registry = _write_registry_rank_landscape(tmp_path, {_CANONICAL: _sealed_entry()})
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
    assert tea_ranking.hyndman_fan_type7([10.0], 0.05) == 10.0
    sample = [1.0, 2.0, 3.0, 4.0]
    assert tea_ranking.hyndman_fan_type7(sample, 0.0) == 1.0
    assert tea_ranking.hyndman_fan_type7(sample, 1.0) == 4.0
    assert tea_ranking.hyndman_fan_type7(sample, 0.5) == pytest.approx(2.5)


def test_cache_lookup_still_default(monkeypatch, tmp_path):
    monkeypatch.delenv(tea_ranking.REGISTRY_ENV, raising=False)
    data = _data(tea.lookup_admitted_process_records(target_polymer="LDPE"))
    assert data["success"] is True
    assert data["engine_mode"] == "cache"


# --- from test_rank_solvent_maps.py: planner_solvent_map / allowed_solvents are rank_landscape arguments.
def _forbid_live_rank_solvent_maps(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("superstructure maps must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


_PLAN_MAP = {"LDPE": "Toluene", "EVOH": "DMSO"}


_ALLOWED_MAP = {"LDPE": ["Toluene"], "EVOH": ["DMSO", "Toluene"]}


_FEED_55_45 = {"LDPE": 0.55, "EVOH": 0.45}


_CAP_20KT = {"processing_capacity_mt_per_yr": 20_000}


def test_sequence_without_map_is_missing_planner_solvent_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("formulation") == "sequence"
    assert payload.get("success") is False
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_empty_planner_map_is_missing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("feed") == ["LDPE", "EVOH"]
    assert payload.get("polymers") == ["LDPE", "EVOH"]
    assert "landscape_points" not in payload


def test_nested_temperature_value_is_not_a_planner_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={"LDPE": {"solvent": "Toluene", "temperature_c": 25}},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_feed_polymer_without_solvent_is_missing_planner_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={"LDPE": "Toluene"},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("polymers") == ["EVOH"]
    assert payload.get("feed") == ["LDPE", "EVOH"]
    assert "landscape_points" not in payload


def test_allowed_solvents_does_not_substitute_for_planner_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_complete_planner_map_is_incomplete_stage_basis_grid(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_type") == "incomplete_stage_basis_grid"
    assert payload.get("formulation") == "sequence"
    assert payload.get("source") == "superstructure"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None
    blockers = payload.get("pending_blockers")
    assert blockers == [
        {
            "error_type": "sequence_coupling_unproven",
            "gate": "gate_sequence_order_coupling",
            "status": "would_fire_if_remnant_grid_complete",
        },
    ]
    keys = payload.get("missing_keys")
    assert isinstance(keys, list) and keys
    by_polymer = {row["polymer"]: row for row in keys}
    assert set(by_polymer) == {"LDPE", "EVOH"}
    for row in keys:
        assert row["target_mass_percent"] is None
        assert row["processing_capacity_mt_per_yr"] is None
        assert row["solvent"]
        assert row["target_mass_percent"] != 55
        assert row["processing_capacity_mt_per_yr"] != 20_000


def test_map_hidden_in_process_config_is_still_missing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        process_config={"planner_solvent_map": _PLAN_MAP},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_solvent_without_allowed_set_is_missing_allowed_solvents(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("formulation") == "solvent"
    assert "landscape_points" not in payload


def test_empty_allowed_solvents_is_missing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents=[],
        target_polymer=["LDPE"],
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("feed") == ["LDPE"]
    assert "landscape_points" not in payload


def test_planner_map_does_not_substitute_for_allowed_solvents(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert "landscape_points" not in payload


def test_flat_allowed_list_is_incomplete_stage_basis_grid(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents=["Toluene", "DMSO"],
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("formulation") == "solvent"
    assert payload.get("error_code") != "missing_allowed_solvents"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload
    polymers = {row["polymer"] for row in payload["missing_keys"]}
    assert polymers == {"LDPE", "EVOH"}
    assert len(payload["missing_keys"]) == 4


def test_per_polymer_allowed_map_missing_a_stage_polymer(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents={"LDPE": ["Toluene"]},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("polymers") == ["EVOH"]
    assert payload.get("feed") == ["LDPE", "EVOH"]
    assert "landscape_points" not in payload


def test_complete_allowed_map_is_incomplete_stage_basis_grid(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload
    polymers = {row["polymer"] for row in payload["missing_keys"]}
    assert polymers == {"LDPE", "EVOH"}


def test_superstructure_without_formulation_is_missing_formulation(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(source="superstructure"))
    assert payload.get("error_code") == "missing_formulation"
    assert payload.get("source") == "superstructure"
    assert payload.get("legal_formulations") == [
        "sequence", "solvent", "sequence_solvent", "wash_train",
    ]
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "process_model_wash_train_unavailable"
    assert "landscape_points" not in payload


def test_residual_route_formulation_is_not_applicable_without_requiring_maps(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="residual_route",
        formulation="sequence",
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "residual_route"
    assert payload.get("inapplicable_fields") == ["formulation"]
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("error_code") != "missing_planner_solvent_map"


def test_maps_on_process_rows_are_not_applicable(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        planner_solvent_map=_PLAN_MAP,
        allowed_solvents=_ALLOWED_MAP,
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "process_rows"
    assert payload.get("inapplicable_fields") == [
        "planner_solvent_map",
        "allowed_solvents",
    ]
    assert "landscape_points" not in payload


def test_maps_on_residual_route_are_not_applicable(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="residual_route",
        planner_solvent_map=_PLAN_MAP,
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "residual_route"
    assert payload.get("inapplicable_fields") == ["planner_solvent_map"]


def test_top_k_sequences_is_unknown_extra_not_the_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        top_k_sequences=[{"steps": [{"dissolved_polymer": "LDPE", "solvent": "Toluene"}]}],
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["top_k_sequences"]
    assert payload.get("error_code") != "missing_planner_solvent_map"


def test_exclude_safety_fail_stays_unknown_extra(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        exclude_safety_fail=True,
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["exclude_safety_fail"]


def test_maps_are_optional_schema_args_not_a_35th_name(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    assert "rank_landscape" not in CONSUMERS
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert "fetch_solvent_safety_by_cid" in EXPECTED_REGISTRY_NAMES
    assert "estimate_thermal_properties" not in EXPECTED_REGISTRY_NAMES
    props = {
        item["name"]: item["parameters"]["properties"]
        for item in tool_schemas()
    }["rank_landscape"]
    assert "planner_solvent_map" in props
    assert "allowed_solvents" in props
    assert "feed_mass_fractions" in props
    assert "default" not in props["planner_solvent_map"]
    assert "default" not in props["allowed_solvents"]
    assert "default" not in props["feed_mass_fractions"]
    required = next(
        item["parameters"].get("required") or []
        for item in tool_schemas()
        if item["name"] == "rank_landscape"
    )
    assert "planner_solvent_map" not in required
    assert "allowed_solvents" not in required
    assert "feed_mass_fractions" not in required
    assert "formulation" not in required
    assert "evaluate_process" in EXPECTED_REGISTRY_NAMES


def test_dispatch_names_missing_planner_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    ranked = dispatch(
        "rank_landscape",
        source="superstructure",
        formulation="sequence",
    )
    assert ranked.get("available") is False
    assert ranked.get("refusal") == "missing_planner_solvent_map"
    assert ranked.get("handle") is None


def test_dispatch_of_complete_map_is_the_data_gate(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    ranked = dispatch(
        "rank_landscape",
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    )
    assert ranked.get("available") is False
    assert ranked.get("refusal") == "incomplete_stage_basis_grid"
    assert ranked.get("handle") is None
    assert ranked["data"]["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )


def test_complete_map_does_not_fill_from_admitted_cache(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)

    def forbidden_records():
        raise AssertionError("remnant grid must not pair-fill from cache")

    monkeypatch.setattr(tea, "_records", forbidden_records)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload


def test_campaign_fingerprint_does_not_fill_the_remnant_grid(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
        campaign_fingerprint=(
            "ef62efb3a708d782ce58cac3295cc9de71b0a7e8d076f6ca79f1ba5830a29636"
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None


def test_wash_train_is_process_model_unavailable(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    wash = _data(tea.rank_landscape(
        source="superstructure",
        formulation="wash_train",
    ))
    assert wash.get("error_code") == "process_model_wash_train_unavailable"
    assert wash.get("formulation") == "wash_train"
    assert wash.get("source") == "superstructure"
    assert "landscape_points" not in wash
    assert wash.get("error_code") != "incomplete_stage_basis_grid"
    aliased = _data(tea.rank_landscape(
        source="superstructure",
        formulation="wash_train",
        process_config={"solvent_loss_pct": 0.5},
        planner_solvent_map=_PLAN_MAP,
        allowed_solvents=_ALLOWED_MAP,
    ))
    assert aliased.get("error_code") == "process_model_wash_train_unavailable"
    assert aliased.get("error_code") != "missing_planner_solvent_map"
    assert aliased.get("error_code") != "incomplete_stage_basis_grid"


def test_sequence_solvent_lists_remnant_times_solvent_table(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    coupled = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence_solvent",
        planner_solvent_map=_PLAN_MAP,
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert coupled.get("error_code") == "incomplete_stage_basis_grid"
    assert coupled.get("formulation") == "sequence_solvent"
    assert coupled.get("error_code") != "tool_not_wired"
    assert coupled.get("error_code") != "missing_planner_solvent_map"
    assert coupled.get("error_code") != "missing_allowed_solvents"
    assert coupled.get("error_code") != "sequence_coupling_unproven"
    assert "pending_blockers" not in coupled
    assert "landscape_points" not in coupled
    polymers = {row["polymer"] for row in coupled["missing_keys"]}
    assert polymers == {"LDPE", "EVOH"}
    assert len(coupled["missing_keys"]) == 3
    for row in coupled["missing_keys"]:
        assert row["target_mass_percent"] is None
        assert row["processing_capacity_mt_per_yr"] is None
        assert row["solvent"]


def test_complete_map_does_not_infer_formulation(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        planner_solvent_map=_PLAN_MAP,
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_formulation"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_sequence_solvent_does_not_require_the_sequence_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence_solvent",
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("formulation") == "sequence_solvent"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert payload.get("error_code") != "missing_allowed_solvents"
    assert payload.get("error_code") != "tool_not_wired"
    assert "pending_blockers" not in payload
    keys = payload["missing_keys"]
    assert {row["polymer"] for row in keys} == {"LDPE", "EVOH"}
    assert all(row["solvent"] is None for row in keys)
    assert all(row["target_mass_percent"] is None for row in keys)


def test_sequence_solvent_does_not_fill_from_a_shortlist_kwarg(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence_solvent",
        screening_shortlist={"source": "explicit", "items": []},
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["screening_shortlist"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_sequence_solvent_empty_table_is_still_incomplete(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence_solvent",
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("missing_keys")
    assert payload.get("missing_keys") != []
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None


def test_sequence_solvent_planner_map_names_cells_not_a_fill(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence_solvent",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert len(payload["missing_keys"]) == 2
    assert {row["polymer"] for row in payload["missing_keys"]} == {"LDPE", "EVOH"}
    assert all(row["solvent"] for row in payload["missing_keys"])
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_unknown_formulation_is_invalid(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="recovery_fraction",
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("formulation") == "recovery_fraction"
    assert payload.get("legal_formulations") == [
        "sequence", "solvent", "sequence_solvent", "wash_train",
    ]
    assert payload.get("error_code") != "process_model_wash_train_unavailable"
    assert payload.get("error_code") != "missing_formulation"


def test_casefold_sequence_still_requires_the_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="Sequence",
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("formulation") == "sequence"


def test_dispatch_names_missing_formulation(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    ranked = dispatch("rank_landscape", source="superstructure")
    assert ranked.get("available") is False
    assert ranked.get("refusal") == "missing_formulation"
    assert ranked.get("handle") is None


def _d18_cell(row):
    return (
        row["polymer"],
        row["target_mass_percent"],
        row["processing_capacity_mt_per_yr"],
        row["solvent"],
    )


def _plant_row(polymer, solvent, mass_pct, capacity, *, success=True, **extra):
    row = {
        "target_polymer": polymer,
        "polymer": polymer,
        "solvent": solvent,
        "target_mass_percent": mass_pct,
        "processing_capacity_mt_per_yr": capacity,
        "success": success,
    }
    row.update(extra)
    return row


def _plant_handle(session, rows):
    return store_handle(
        session,
        tool="evaluate_process",
        source_basis="tea_cache_exact",
        data={"success": True, "comparison_rows": list(rows)},
    )


def _complete_d18_rows(**extra):
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    held = {key: value for key, value in extra.items() if value is not None}
    return [
        _plant_row("LDPE", toluene, 55.0, 20_000.0, **held),
        _plant_row("EVOH", dmso, 45.0, 20_000.0, **held),
        _plant_row("LDPE", toluene, 100.0, 11_000.0, **held),
        _plant_row("EVOH", dmso, 100.0, 9_000.0, **held),
    ]


def _sequence_kwargs(**extra):
    args = dict(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        feed_mass_fractions=_FEED_55_45,
        process_config=_CAP_20KT,
    )
    args.update(extra)
    return args


def test_sequence_feed_expands_d18_remnant_keys(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
        process_config={"processing_capacity_mt_per_yr": 20_000},
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    assert payload.get("n_usable") is None
    assert "landscape_points" not in payload
    assert payload.get("pending_blockers") == [
        {
            "error_type": "sequence_coupling_unproven",
            "gate": "gate_sequence_order_coupling",
            "status": "would_fire_if_remnant_grid_complete",
        },
    ]
    keys = payload["missing_keys"]
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    cells = {_d18_cell(row) for row in keys}
    assert cells == {
        ("LDPE", 55.0, 20_000.0, toluene),
        ("LDPE", 100.0, 11_000.0, toluene),
        ("EVOH", 45.0, 20_000.0, dmso),
        ("EVOH", 100.0, 9_000.0, dmso),
    }
    assert len(keys) == 4
    assert all(row["target_mass_percent"] != 66.667 for row in keys)
    assert all(row["processing_capacity_mt_per_yr"] != 18_000 for row in keys)


def test_three_polymer_feed_lists_the_subset_union(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={
            "LDPE": "Toluene", "EVOH": "DMSO", "PET": "Toluene",
        },
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.15, "PET": 0.30},
        process_config={"processing_capacity_mt_per_yr": 20_000},
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 12
    ldpe_caps = sorted(
        row["processing_capacity_mt_per_yr"]
        for row in keys if row["polymer"] == "LDPE"
    )
    assert ldpe_caps == [11_000.0, 14_000.0, 17_000.0, 20_000.0]
    assert all(row["processing_capacity_mt_per_yr"] != 18_000 for row in keys)


def test_d18_keys_follow_the_feed_in_hand(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        feed_mass_fractions={"LDPE": 0.60, "EVOH": 0.40},
        process_config={"processing_capacity_mt_per_yr": 15_000},
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    cells = {_d18_cell(row) for row in payload["missing_keys"]}
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    assert cells == {
        ("LDPE", 60.0, 15_000.0, toluene),
        ("LDPE", 100.0, 9_000.0, toluene),
        ("EVOH", 40.0, 15_000.0, dmso),
        ("EVOH", 100.0, 6_000.0, dmso),
    }
    assert all(row["target_mass_percent"] != 55 for row in payload["missing_keys"])
    assert all(
        row["processing_capacity_mt_per_yr"] != 20_000
        for row in payload["missing_keys"]
    )


def test_omitted_capacity_does_not_default_twenty_kt(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    percents = {row["target_mass_percent"] for row in keys}
    assert percents == {55.0, 45.0, 100.0}
    assert all(row["processing_capacity_mt_per_yr"] is None for row in keys)
    assert all(row["processing_capacity_mt_per_yr"] != 20_000 for row in keys)


def test_process_config_mass_percent_is_not_the_feed(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
        process_config={
            "target_mass_percent": 55,
            "processing_capacity_mt_per_yr": 20_000,
            "feed_mass_fractions": {"LDPE": 0.55, "EVOH": 0.45},
        },
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 2
    for row in keys:
        assert row["target_mass_percent"] is None
        assert row["processing_capacity_mt_per_yr"] is None


def test_composition_without_map_is_still_missing_planner_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
        process_config={"processing_capacity_mt_per_yr": 20_000},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_feed_mass_fractions_on_process_rows_is_not_applicable(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "process_rows"
    assert payload.get("inapplicable_fields") == ["feed_mass_fractions"]
    assert "landscape_points" not in payload


def test_unknown_handle_is_not_an_empty_remnant_table(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(handle="not-a-remnant-table"),
    ))
    assert payload.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload


def test_unknown_handle_does_not_outrank_missing_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config=_CAP_20KT,
        handle="not-a-remnant-table",
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "unknown_handle"
    assert "pending_blockers" not in payload


def test_omitted_map_inherits_pairs_from_economics_handle(monkeypatch):
    """Handle binds the map; it still does not fill remnant cells."""
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
        ])
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    cells = {_d18_cell(row) for row in payload["missing_keys"]}
    assert cells == {
        ("LDPE", 100.0, 11_000.0, toluene),
        ("EVOH", 100.0, 9_000.0, dmso),
    }
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None


def test_omitted_map_complete_handle_is_still_coupling_unproven(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None


def test_omitted_map_handle_missing_a_feed_polymer_is_missing_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
        ])
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("polymers") == ["EVOH"]
    assert payload.get("feed") == ["LDPE", "EVOH"]
    assert "landscape_points" not in payload


def test_explicit_planner_map_is_not_replaced_by_handle_pairs(monkeypatch):
    """The argument map names solvents; the handle only subtracts matching cells."""
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    xylene = tea._public_solvent_token("Xylene")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", xylene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", xylene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0),
        ])
        explicit = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
        inherited = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert explicit.get("error_code") == "incomplete_stage_basis_grid"
    cells = {_d18_cell(row) for row in explicit["missing_keys"]}
    assert ("LDPE", 55.0, 20_000.0, toluene) in cells
    assert ("LDPE", 100.0, 11_000.0, toluene) in cells
    assert ("EVOH", 45.0, 20_000.0, dmso) not in cells
    assert all(row[3] != xylene for row in cells)
    assert inherited.get("error_code") == "sequence_coupling_unproven"
    assert inherited.get("error_code") != explicit.get("error_code")


def test_empty_planner_map_does_not_inherit_from_handle(monkeypatch):
    """Present `{}` is not omitted. Inherit only when the map is absent."""
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
        ])
        empty = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map={}),
        ))
        omitted = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert empty.get("error_code") == "missing_planner_solvent_map"
    assert empty.get("error_code") != "incomplete_stage_basis_grid"
    assert "landscape_points" not in empty
    assert omitted.get("error_code") == "incomplete_stage_basis_grid"
    assert omitted.get("error_code") != "missing_planner_solvent_map"


def test_malformed_planner_map_does_not_inherit_from_handle(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
        ])
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                planner_solvent_map={
                    "LDPE": {"solvent": "Toluene", "temperature_c": 25},
                },
            ),
        ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload


def test_two_solvents_for_one_polymer_is_not_a_sequence_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    xylene = tea._public_solvent_token("Xylene")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("LDPE", xylene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
        ])
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("polymers") == ["LDPE"]
    assert "landscape_points" not in payload


def test_failed_row_does_not_bind_planner_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0, success=False),
        ])
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("polymers") == ["EVOH"]
    assert "landscape_points" not in payload


def test_plan_top_k_is_not_scanned_for_the_map(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = store_handle(
            session,
            tool="plan_multistage_separation",
            source_basis="cosmo_rs_grid",
            data={
                "success": True,
                "steps": [
                    {"dissolved_polymer": "LDPE", "solvent": "Toluene"},
                    {"dissolved_polymer": "EVOH", "solvent": "DMSO"},
                ],
                "top_k_sequences": [{
                    "steps": [
                        {"dissolved_polymer": "LDPE", "solvent": "Toluene"},
                        {"dissolved_polymer": "EVOH", "solvent": "DMSO"},
                    ],
                }],
            },
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(handle=handle, planner_solvent_map=None),
        ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "not_economics_handle"
    assert "landscape_points" not in payload


def test_omitted_allowed_solvents_does_not_inherit_from_handle(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
        ])
        payload = _data(tea.rank_landscape(
            source="superstructure",
            formulation="solvent",
            target_polymer=["LDPE", "EVOH"],
            handle=handle,
        ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload


def test_first_stage_handle_does_not_fill_remnant_cells(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
        ])
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    cells = {_d18_cell(row) for row in payload["missing_keys"]}
    assert cells == {
        ("LDPE", 100.0, 11_000.0, toluene),
        ("EVOH", 100.0, 9_000.0, dmso),
    }
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None


def test_planted_p2_is_not_a_remnant_hit(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", toluene, 66.667, 18_000.0),
            _plant_row("EVOH", dmso, 66.667, 18_000.0),
        ])
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    cells = {_d18_cell(row) for row in payload["missing_keys"]}
    assert ("LDPE", 100.0, 11_000.0, toluene) in cells
    assert ("EVOH", 100.0, 9_000.0, dmso) in cells
    assert all(row["target_mass_percent"] != 66.667 for row in payload["missing_keys"])


def test_failed_row_is_not_a_coefficient(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", toluene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0, success=False),
        ])
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    cells = {_d18_cell(row) for row in payload["missing_keys"]}
    assert cells == {("EVOH", 100.0, 9_000.0, dmso)}
    assert "pending_blockers" in payload


def test_complete_sequence_grid_is_d20_primary(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", toluene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0),
        ])
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert payload.get("error_type") == "sequence_coupling_unproven"
    assert payload.get("formulation") == "sequence"
    assert payload.get("source") == "superstructure"
    assert payload.get("gate") == "gate_sequence_order_coupling"
    assert "pending_blockers" not in payload
    assert payload.get("pending_blockers") in (None, [])
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None
    assert "missing_keys" not in payload
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_complete_grid_without_composition_cannot_match(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", toluene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0),
        ])
        payload = _data(tea.rank_landscape(
            source="superstructure",
            formulation="sequence",
            planner_solvent_map=_PLAN_MAP,
            target_polymer=["LDPE", "EVOH"],
            handle=handle,
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 2
    for row in keys:
        assert row["target_mass_percent"] is None
        assert row["processing_capacity_mt_per_yr"] is None
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )


def test_complete_sequence_solvent_is_not_a_ranking(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("LDPE", toluene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("EVOH", toluene, 45.0, 20_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0),
            _plant_row("EVOH", toluene, 100.0, 9_000.0),
        ])
        payload = _data(tea.rank_landscape(
            source="superstructure",
            formulation="sequence_solvent",
            planner_solvent_map=_PLAN_MAP,
            allowed_solvents=_ALLOWED_MAP,
            feed_mass_fractions=_FEED_55_45,
            process_config=_CAP_20KT,
            handle=handle,
        ))
    assert payload.get("error_code") == "tool_not_wired"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_dispatch_of_complete_grid_is_d20_primary(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", toluene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0),
        ])
        ranked = dispatch("rank_landscape", **_sequence_kwargs(handle=handle))
    assert ranked.get("available") is False
    assert ranked.get("refusal") == "sequence_coupling_unproven"
    assert ranked.get("handle") is None
    assert "pending_blockers" not in ranked.get("data", {})
    assert "landscape_points" not in ranked.get("data", {})


def test_campaign_fingerprint_does_not_complete_or_rank(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", toluene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0),
        ])
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                campaign_fingerprint=(
                    "ef62efb3a708d782ce58cac3295cc9de71b0a7e8d076f6ca79f1ba5830a29636"
                ),
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert payload.get("n_usable") is None
    assert "landscape_points" not in payload


def test_solvent_handle_does_not_invent_remnant_matches(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, [
            _plant_row("LDPE", toluene, 55.0, 20_000.0),
            _plant_row("EVOH", dmso, 45.0, 20_000.0),
            _plant_row("LDPE", toluene, 100.0, 11_000.0),
            _plant_row("EVOH", dmso, 100.0, 9_000.0),
        ])
        payload = _data(tea.rank_landscape(
            source="superstructure",
            formulation="solvent",
            allowed_solvents=_ALLOWED_MAP,
            target_polymer=["LDPE", "EVOH"],
            feed_mass_fractions=_FEED_55_45,
            process_config=_CAP_20KT,
            handle=handle,
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    keys = payload["missing_keys"]
    assert len(keys) == 3
    for row in keys:
        assert row["target_mass_percent"] is None
        assert row["processing_capacity_mt_per_yr"] is None


def test_d18_keys_do_not_pair_fill_from_cache(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)

    def forbidden_records():
        raise AssertionError("D-18 listing must not pair-fill from cache")

    monkeypatch.setattr(tea, "_records", forbidden_records)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
        process_config={"processing_capacity_mt_per_yr": 20_000},
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert len(payload["missing_keys"]) == 4


def test_sequence_solvent_expands_remnant_times_solvent(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence_solvent",
        planner_solvent_map=_PLAN_MAP,
        allowed_solvents=_ALLOWED_MAP,
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
        process_config={"processing_capacity_mt_per_yr": 20_000},
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert "pending_blockers" not in payload
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    toluene = tea._public_solvent_token("Toluene")
    dmso = tea._public_solvent_token("DMSO")
    cells = {_d18_cell(row) for row in keys}
    assert cells == {
        ("LDPE", 55.0, 20_000.0, toluene),
        ("LDPE", 100.0, 11_000.0, toluene),
        ("EVOH", 45.0, 20_000.0, dmso),
        ("EVOH", 45.0, 20_000.0, toluene),
        ("EVOH", 100.0, 9_000.0, dmso),
        ("EVOH", 100.0, 9_000.0, toluene),
    }
    assert len(keys) == 6
    assert all(row["target_mass_percent"] != 66.667 for row in keys)


def test_solvent_formulation_does_not_invent_an_order(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
        process_config={"processing_capacity_mt_per_yr": 20_000},
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert "pending_blockers" not in payload
    keys = payload["missing_keys"]
    assert len(keys) == 3
    for row in keys:
        assert row["target_mass_percent"] is None
        assert row["processing_capacity_mt_per_yr"] is None


def test_dispatch_of_d18_keys_is_still_the_data_gate(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    ranked = dispatch(
        "rank_landscape",
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
        process_config={"processing_capacity_mt_per_yr": 20_000},
    )
    assert ranked.get("available") is False
    assert ranked.get("refusal") == "incomplete_stage_basis_grid"
    assert ranked.get("handle") is None
    assert len(ranked["data"]["missing_keys"]) == 4
    assert ranked["data"]["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )


def test_omitted_energy_case_is_not_a_silent_c1(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert "energy_case" not in row or row.get("energy_case") is None
        assert row.get("energy_case") != "C1"


@pytest.mark.parametrize(
    "field, planted",
    [
        pytest.param(
            "energy_case",
            "C2",
            id="planted_rows_still_complete_when_the_field_is_omitted-c2_rows_still_complete_when_energy_case_is",
        ),
        pytest.param(
            "dissolution_temperature_c",
            80.0,
            id="planted_rows_still_complete_when_the_field_is_omitted-planted_t_still_completes_when_dissolution_t_is",
        ),
        pytest.param(
            "precipitation_temperature_c",
            35.0,
            id="planted_rows_still_complete_when_the_field_is_omitted-planted_t_still_completes_when_precipitation_t_is",
        ),
        pytest.param(
            "solvent_price_usd_per_kg",
            1.5,
            id="planted_rows_still_complete_when_the_field_is_omitted-planted_price_still_completes_when_solvent_price_is",
        ),
        pytest.param(
            "solvent_loss_pct",
            0.5,
            id="planted_rows_still_complete_when_the_field_is_omitted-planted_loss_still_completes_when_solvent_loss_is",
        ),
        pytest.param(
            "steam_power_depreciation",
            "MACRS20",
            id="planted_rows_still_complete_when_the_field_is_omitted-default_steam_power_rows_still_complete_when",
        ),
        pytest.param("feedstock_distance_km", 100.0, id="omitted_3-planted_distance_still_completes_when"),
        pytest.param("feedstock_distance_km", 0.0, id="omitted_3-zero_rows_still_complete_when_distance_is"),
        pytest.param("dissolution_capacity", 5.0, id="when_omitted-planted_capacity_still_completes"),
        pytest.param("dissolution_capacity", 3.0, id="when_omitted-default_capacity_rows_still_complete"),
        pytest.param("labor_cost_usd_per_employee_yr", 150000.0, id="when_omitted_2-planted_labor_still_completes"),
        pytest.param("labor_cost_usd_per_employee_yr", 120000.0, id="when_omitted_2-default_labor_rows_still_complete"),
        pytest.param("sell_leftover_plastic", False, id="rows_still_complete_when_sell_is_omitted-false"),
        pytest.param("sell_leftover_plastic", True, id="rows_still_complete_when_sell_is_omitted-true"),
        pytest.param("burn_leftover_plastic", False, id="burn_rows_still_complete_when_omitted-false"),
        pytest.param("burn_leftover_plastic", True, id="burn_rows_still_complete_when_omitted-true"),
        pytest.param(
            "precipitation_temperature_format", "constant", id="format_rows_still_complete_when_omitted-constant"
        ),
        pytest.param("precipitation_temperature_format", "drop", id="format_rows_still_complete_when_omitted-drop"),
        pytest.param("irr", 0.1, id="irr_rows_still_complete_when_omitted-default"),
        pytest.param("irr", 0.12, id="irr_rows_still_complete_when_omitted-named"),
        pytest.param("income_tax", 0.21, id="income_tax_rows_still_complete_when_omitted-default"),
        pytest.param("income_tax", 0.25, id="income_tax_rows_still_complete_when_omitted-named"),
        pytest.param("operating_days", 350.4, id="operating_days_rows_still_complete_when_omitted-default"),
        pytest.param("operating_days", 365.0, id="operating_days_rows_still_complete_when_omitted-named"),
        pytest.param("labor_burden", 0.9, id="labor_burden_rows_still_complete_when_omitted-default"),
        pytest.param("labor_burden", 1.5, id="labor_burden_rows_still_complete_when_omitted-above_one"),
        pytest.param("finance_interest", 0.08, id="finance_interest_rows_still_complete_when_omitted-default"),
        pytest.param("finance_interest", 0.12, id="finance_interest_rows_still_complete_when_omitted-named"),
        pytest.param("finance_years", 10, id="finance_years_rows_still_complete_when_omitted-default"),
        pytest.param("finance_years", 15, id="finance_years_rows_still_complete_when_omitted-named"),
        pytest.param("finance_fraction", 0.0, id="finance_fraction_rows_still_complete_when_omitted-default"),
        pytest.param("finance_fraction", 0.4, id="finance_fraction_rows_still_complete_when_omitted-named"),
        pytest.param("startup_months", 3, id="startup_months_rows_still_complete_when_omitted-default"),
        pytest.param("startup_months", 6, id="startup_months_rows_still_complete_when_omitted-named"),
        pytest.param("startup_FOCfrac", 1.0, id="startup_FOCfrac_rows_still_complete_when_omitted-default"),
        pytest.param("startup_FOCfrac", 0.5, id="startup_FOCfrac_rows_still_complete_when_omitted-named"),
        pytest.param("startup_VOCfrac", 0.75, id="startup_VOCfrac_rows_still_complete_when_omitted-default"),
        pytest.param("startup_VOCfrac", 0.5, id="startup_VOCfrac_rows_still_complete_when_omitted-named"),
        pytest.param("startup_salesfrac", 0.5, id="startup_salesfrac_rows_still_complete_when_omitted-default"),
        pytest.param("startup_salesfrac", 0.25, id="startup_salesfrac_rows_still_complete_when_omitted-named"),
        pytest.param("WC_over_FCI", 0.05, id="WC_over_FCI_rows_still_complete_when_omitted-default"),
        pytest.param("WC_over_FCI", 0.1, id="WC_over_FCI_rows_still_complete_when_omitted-named"),
        pytest.param("warehouse", 0.04, id="warehouse_rows_still_complete_when_omitted-default"),
        pytest.param("warehouse", 0.1, id="warehouse_rows_still_complete_when_omitted-named"),
        pytest.param("site_development", 0.09, id="site_development_rows_still_complete_when_omitted-default"),
        pytest.param("site_development", 0.1, id="site_development_rows_still_complete_when_omitted-named"),
        pytest.param("additional_piping", 0.045, id="additional_piping_rows_still_complete_when_omitted-default"),
        pytest.param("additional_piping", 0.1, id="additional_piping_rows_still_complete_when_omitted-named"),
        pytest.param("proratable_costs", 0.1, id="proratable_costs_rows_still_complete_when_omitted-default"),
        pytest.param("proratable_costs", 0.2, id="proratable_costs_rows_still_complete_when_omitted-named"),
        pytest.param("field_expenses", 0.1, id="field_expenses_rows_still_complete_when_omitted-default"),
        pytest.param("field_expenses", 0.2, id="field_expenses_rows_still_complete_when_omitted-named"),
        pytest.param("construction", 0.2, id="construction_rows_still_complete_when_omitted-default"),
        pytest.param("construction", 0.1, id="construction_rows_still_complete_when_omitted-named"),
        pytest.param("contingency", 0.4, id="contingency_rows_still_complete_when_omitted-default"),
        pytest.param("contingency", 0.1, id="contingency_rows_still_complete_when_omitted-named"),
        pytest.param("other_indirect_costs", 0.1, id="other_indirect_costs_rows_still_complete_when_omitted-default"),
        pytest.param("other_indirect_costs", 0.2, id="other_indirect_costs_rows_still_complete_when_omitted-named"),
        pytest.param("property_insurance", 0.007, id="property_insurance_rows_still_complete_when_omitted-default"),
        pytest.param("property_insurance", 0.1, id="property_insurance_rows_still_complete_when_omitted-named"),
        pytest.param("maintenance", 0.03, id="maintenance_rows_still_complete_when_omitted-default"),
        pytest.param("maintenance", 0.1, id="maintenance_rows_still_complete_when_omitted-named"),
        pytest.param("depreciation", "MACRS7", id="depreciation_rows_still_complete_when_omitted-default"),
        pytest.param("depreciation", "MACRS5", id="depreciation_rows_still_complete_when_omitted-named"),
    ],
)
def test_planted_rows_still_complete_when_the_field_is_omitted(monkeypatch, field, planted):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(**{field: planted}))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


@pytest.mark.parametrize(
    "missing, field, planted, requested",
    [
        pytest.param("C1", "energy_case", "C2", "C1", id="c1_slice_does_not_accept_c2_rows"),
        pytest.param(90.0, "dissolution_temperature_c", 80.0, 90.0, id="dissolution_t_does_not_accept_another_t"),
        pytest.param(40.0, "precipitation_temperature_c", 35.0, 40.0, id="precipitation_t_does_not_accept_another_t"),
        pytest.param(2.0, "solvent_price_usd_per_kg", 1.5, 2.0, id="solvent_price_does_not_accept_another_price"),
        pytest.param(3.0, "solvent_loss_pct", 0.5, 3.0, id="solvent_loss_does_not_accept_another_loss"),
        pytest.param(
            250.0, "feedstock_distance_km", 100.0, 250.0, id="feedstock_distance_does_not_accept_another_distance"
        ),
        pytest.param(5.0, "dissolution_capacity", 3.0, 5.0, id="dissolution_capacity_does_not_accept_another_capacity"),
        pytest.param(
            150000.0, "labor_cost_usd_per_employee_yr", 120000.0, 150000.0, id="labor_does_not_accept_another_labor"
        ),
        pytest.param(True, "sell_leftover_plastic", False, True, id="true_does_not_accept_false_rows"),
        pytest.param(True, "burn_leftover_plastic", False, True, id="burn_true_does_not_accept_false_rows"),
        pytest.param(
            "drop", "precipitation_temperature_format", "constant", "drop", id="drop_does_not_accept_constant_rows"
        ),
        pytest.param(0.12, "irr", 0.1, 0.12, id="irr_does_not_accept_default_rows"),
        pytest.param(0.25, "income_tax", 0.21, 0.25, id="income_tax_does_not_accept_default_rows"),
        pytest.param(365.0, "operating_days", 350.4, 365, id="operating_days_does_not_accept_default_rows"),
        pytest.param(1.5, "labor_burden", 0.9, 1.5, id="labor_burden_does_not_accept_default_rows"),
        pytest.param(0.12, "finance_interest", 0.08, 0.12, id="finance_interest_does_not_accept_default_rows"),
        pytest.param(15.0, "finance_years", 10, 15, id="finance_years_does_not_accept_default_rows"),
        pytest.param(0.4, "finance_fraction", 0.0, 0.4, id="finance_fraction_does_not_accept_default_rows"),
        pytest.param(6.0, "startup_months", 3, 6, id="startup_months_does_not_accept_default_rows"),
        pytest.param(0.5, "startup_FOCfrac", 1.0, 0.5, id="startup_FOCfrac_does_not_accept_default_rows"),
        pytest.param(0.5, "startup_VOCfrac", 0.75, 0.5, id="startup_VOCfrac_does_not_accept_default_rows"),
        pytest.param(0.25, "startup_salesfrac", 0.5, 0.25, id="startup_salesfrac_does_not_accept_default_rows"),
        pytest.param(0.1, "WC_over_FCI", 0.05, 0.1, id="WC_over_FCI_does_not_accept_default_rows"),
        pytest.param(0.1, "warehouse", 0.04, 0.1, id="warehouse_does_not_accept_default_rows"),
        pytest.param(0.1, "site_development", 0.09, 0.1, id="site_development_does_not_accept_default_rows"),
        pytest.param(0.1, "additional_piping", 0.045, 0.1, id="additional_piping_does_not_accept_default_rows"),
        pytest.param(0.2, "proratable_costs", 0.1, 0.2, id="proratable_costs_does_not_accept_default_rows"),
        pytest.param(0.2, "field_expenses", 0.1, 0.2, id="field_expenses_does_not_accept_default_rows"),
        pytest.param(0.1, "construction", 0.2, 0.1, id="construction_does_not_accept_default_rows"),
        pytest.param(0.1, "contingency", 0.4, 0.1, id="contingency_does_not_accept_default_rows"),
        pytest.param(0.2, "other_indirect_costs", 0.1, 0.2, id="other_indirect_costs_does_not_accept_default_rows"),
        pytest.param(0.1, "property_insurance", 0.007, 0.1, id="property_insurance_does_not_accept_default_rows"),
        pytest.param(0.1, "maintenance", 0.03, 0.1, id="maintenance_does_not_accept_default_rows"),
        pytest.param("MACRS5", "depreciation", "MACRS7", "MACRS5", id="depreciation_does_not_accept_default_rows"),
        pytest.param(
            "MACRS7", "steam_power_depreciation", "MACRS20", "MACRS7", id="steam_power_does_not_accept_default_rows"
        ),
    ],
)
def test_named_slice_does_not_accept_other_rows(monkeypatch, missing, field, planted, requested):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(**{field: planted}))
        payload = _data(
            tea.rank_landscape(**_sequence_kwargs(handle=handle, process_config={**_CAP_20KT, field: requested}))
        )
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[field] for row in keys} == {missing}
    assert payload["pending_blockers"][0]["error_type"] == "sequence_coupling_unproven"
    assert "landscape_points" not in payload


@pytest.mark.parametrize(
    "field, planted, requested",
    [
        pytest.param("energy_case", "C1", "C1", id="c1_slice_completes_on_c1"),
        pytest.param("dissolution_temperature_c", 90.0, 90.0, id="dissolution_t_completes_on_matching"),
        pytest.param("precipitation_temperature_c", 40.0, 40.0, id="precipitation_t_completes_on_matching"),
        pytest.param("solvent_price_usd_per_kg", 2.0, 2.0, id="solvent_price_completes_on_matching"),
        pytest.param("solvent_loss_pct", 3.0, 3.0, id="solvent_loss_completes_on_matching"),
        pytest.param("feedstock_distance_km", 250.0, 250.0, id="feedstock_distance_completes_on_matching"),
        pytest.param("dissolution_capacity", 5.0, 5.0, id="dissolution_capacity_completes_on_matching"),
        pytest.param("labor_cost_usd_per_employee_yr", 150000.0, 150000.0, id="labor_completes_on_matching"),
        pytest.param("sell_leftover_plastic", True, True, id="true_completes_on_matching"),
        pytest.param("burn_leftover_plastic", True, True, id="burn_true_completes_on_matching"),
        pytest.param("precipitation_temperature_format", "drop", "drop", id="drop_completes_on_matching"),
        pytest.param("irr", 0.12, 0.12, id="irr_completes_on_matching"),
        pytest.param("income_tax", 0.25, 0.25, id="income_tax_completes_on_matching"),
        pytest.param("operating_days", 365.0, 365, id="operating_days_completes_on_matching"),
        pytest.param("labor_burden", 1.5, 1.5, id="labor_burden_completes_on_matching"),
        pytest.param("finance_interest", 0.12, 0.12, id="finance_interest_completes_on_matching"),
        pytest.param("finance_years", 15, 15, id="finance_years_completes_on_matching"),
        pytest.param("finance_fraction", 0.4, 0.4, id="finance_fraction_completes_on_matching"),
        pytest.param("startup_months", 6, 6, id="startup_months_completes_on_matching"),
        pytest.param("startup_FOCfrac", 0.5, 0.5, id="startup_FOCfrac_completes_on_matching"),
        pytest.param("startup_VOCfrac", 0.5, 0.5, id="startup_VOCfrac_completes_on_matching"),
        pytest.param("startup_salesfrac", 0.25, 0.25, id="startup_salesfrac_completes_on_matching"),
        pytest.param("WC_over_FCI", 0.1, 0.1, id="WC_over_FCI_completes_on_matching"),
        pytest.param("warehouse", 0.1, 0.1, id="warehouse_completes_on_matching"),
        pytest.param("site_development", 0.1, 0.1, id="site_development_completes_on_matching"),
        pytest.param("additional_piping", 0.1, 0.1, id="additional_piping_completes_on_matching"),
        pytest.param("proratable_costs", 0.2, 0.2, id="proratable_costs_completes_on_matching"),
        pytest.param("field_expenses", 0.2, 0.2, id="field_expenses_completes_on_matching"),
        pytest.param("construction", 0.1, 0.1, id="construction_completes_on_matching"),
        pytest.param("contingency", 0.1, 0.1, id="contingency_completes_on_matching"),
        pytest.param("other_indirect_costs", 0.2, 0.2, id="other_indirect_costs_completes_on_matching"),
        pytest.param("property_insurance", 0.1, 0.1, id="property_insurance_completes_on_matching"),
        pytest.param("maintenance", 0.1, 0.1, id="maintenance_completes_on_matching"),
        pytest.param("depreciation", "MACRS5", "MACRS5", id="depreciation_completes_on_matching"),
        pytest.param("steam_power_depreciation", "MACRS7", "MACRS7", id="steam_power_completes_on_matching"),
    ],
)
def test_named_slice_completes_on_matching_rows(monkeypatch, field, planted, requested):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(**{field: planted}))
        payload = _data(
            tea.rank_landscape(**_sequence_kwargs(handle=handle, process_config={**_CAP_20KT, field: requested}))
        )
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_energy_case_is_listed_without_a_handle(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "energy_case": "c2"}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C2"}
    assert all(row["target_mass_percent"] is not None for row in keys)


def test_invalid_energy_case_is_not_a_listing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "energy_case": "C9"}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        pytest.param('energy_case', 'C9', id='energy_case'),
        pytest.param('dissolution_temperature_c', 'hot', id='dissolution_t'),
        pytest.param('precipitation_temperature_c', 'cold', id='precipitation_t'),
        pytest.param('solvent_price_usd_per_kg', 'cheap', id='solvent_price'),
        pytest.param('solvent_loss_pct', 'leaky', id='solvent_loss'),
        pytest.param('feedstock_distance_km', 'far', id='feedstock_distance'),
        pytest.param('dissolution_capacity', 'wide', id='dissolution_capacity'),
        pytest.param('labor_cost_usd_per_employee_yr', 'salary', id='labor'),
        pytest.param('sell_leftover_plastic', 'maybe', id='sell_leftover'),
        pytest.param('burn_leftover_plastic', 'maybe', id='burn_leftover'),
        pytest.param('precipitation_temperature_format', 'linear', id='precipitation_format'),
        pytest.param('precipitation_configuration', 'flash', id='precipitation_configuration'),
        pytest.param('irr', 'maybe', id='irr'),
        pytest.param('income_tax', 'maybe', id='income_tax'),
        pytest.param('operating_days', 'maybe', id='operating_days'),
        pytest.param('labor_burden', 'maybe', id='labor_burden'),
        pytest.param('finance_interest', 'maybe', id='finance_interest'),
        pytest.param('finance_years', 'maybe', id='finance_years'),
        pytest.param('finance_fraction', 'maybe', id='finance_fraction'),
        pytest.param('startup_months', 'maybe', id='startup_months'),
        pytest.param('startup_FOCfrac', 'maybe', id='startup_FOCfrac'),
        pytest.param('startup_VOCfrac', 'maybe', id='startup_VOCfrac'),
        pytest.param('startup_salesfrac', 'maybe', id='startup_salesfrac'),
        pytest.param('WC_over_FCI', 'maybe', id='WC_over_FCI'),
        pytest.param('warehouse', 'maybe', id='warehouse'),
        pytest.param('site_development', 'maybe', id='site_development'),
        pytest.param('additional_piping', 'maybe', id='additional_piping'),
        pytest.param('proratable_costs', 'maybe', id='proratable_costs'),
        pytest.param('field_expenses', 'maybe', id='field_expenses'),
        pytest.param('construction', 'maybe', id='construction'),
        pytest.param('contingency', 'maybe', id='contingency'),
        pytest.param('other_indirect_costs', 'maybe', id='other_indirect_costs'),
        pytest.param('property_insurance', 'maybe', id='property_insurance'),
        pytest.param('maintenance', 'maybe', id='maintenance'),
        pytest.param('depreciation', 'macrs7', id='depreciation'),
        pytest.param('duration', 30, id='duration'),
        pytest.param('construction_schedule', 3, id='construction_schedule'),
        pytest.param('steam_power_depreciation', 'macrs20', id='steam_power'),
        pytest.param('lang_factor', 3.0, id='lang_factor'),
    ],
)
def test_invalid_does_not_outrank_missing_map(monkeypatch, key, value):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, key: value},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


@pytest.mark.parametrize(
    'value',
    [
        pytest.param('dissolution_temperature_c', id='dissolution_t_is_not_a_silent_cache_temperature'),
        pytest.param('precipitation_temperature_c', id='precipitation_t_is_not_a_silent_cache_temperature'),
        pytest.param('solvent_price_usd_per_kg', id='solvent_price_is_not_a_silent_cache_price'),
    ],
)
def test_omitted(monkeypatch, value):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value) is None


@pytest.mark.parametrize(
    ('value', 'value_2', 'key', 'value_3'),
    [
        pytest.param(90.0, 'dissolution_temperature_c', 'dissolution_temp_c', 90, id='dissolution_temp_c_alias_stamps_the_public_name'),
        pytest.param(40.0, 'precipitation_temperature_c', 'precipitation_temp_c', 40, id='precipitation_temp_c_alias_stamps_the_public_name'),
        pytest.param(2.0, 'solvent_price_usd_per_kg', 'solvent_price', 2.0, id='solvent_price_alias_stamps_the_public_name'),
        pytest.param(0.0, 'feedstock_distance_km', 'feedstock_distance_km', 0.0, id='named_zero_distance_is_holdable'),
        pytest.param(3.0, 'dissolution_capacity', 'dissolution_capacity', 3.0, id='named_default_capacity_is_holdable'),
        pytest.param(120000.0, 'labor_cost_usd_per_employee_yr', 'labor_cost_usd_per_employee_yr', 120000.0, id='named_default_labor_is_holdable'),
        pytest.param(150000.0, 'labor_cost_usd_per_employee_yr', 'labor_cost', 150000, id='labor_cost_alias_stamps_the_public_name'),
        pytest.param(False, 'sell_leftover_plastic', 'sell_leftover_plastic', False, id='named_false_is_holdable'),
        pytest.param(True, 'sell_leftover_plastic', 'sell_leftover_plastic', 'true', id='true_string_stamps_boolean_true'),
        pytest.param(False, 'burn_leftover_plastic', 'burn_leftover_plastic', False, id='named_burn_false_is_holdable'),
        pytest.param('constant', 'precipitation_temperature_format', 'precipitation_temperature_format', 'constant', id='named_constant_is_holdable'),
        pytest.param(0.1, 'irr', 'irr', 0.1, id='named_default_irr_is_holdable'),
        pytest.param(0.21, 'income_tax', 'income_tax', 0.21, id='named_default_income_tax_is_holdable'),
        pytest.param(0.0, 'income_tax', 'income_tax', 0, id='named_zero_income_tax_is_holdable'),
        pytest.param(350.4, 'operating_days', 'operating_days', 350.4, id='named_default_operating_days_is_holdable'),
        pytest.param(1.0, 'operating_days', 'operating_days', 1, id='named_one_operating_day_is_holdable'),
        pytest.param(0.9, 'labor_burden', 'labor_burden', 0.9, id='named_default_labor_burden_is_holdable'),
        pytest.param(0.0, 'labor_burden', 'labor_burden', 0, id='named_zero_labor_burden_is_holdable'),
        pytest.param(0.08, 'finance_interest', 'finance_interest', 0.08, id='named_default_finance_interest_is_holdable'),
        pytest.param(0.0, 'finance_interest', 'finance_interest', 0, id='named_zero_finance_interest_is_holdable'),
        pytest.param(10.0, 'finance_years', 'finance_years', 10, id='named_default_finance_years_is_holdable'),
        pytest.param(1.0, 'finance_years', 'finance_years', 1, id='named_one_finance_year_is_holdable'),
        pytest.param(0.0, 'finance_fraction', 'finance_fraction', 0.0, id='named_default_finance_fraction_is_holdable'),
        pytest.param(1.0, 'finance_fraction', 'finance_fraction', 1.0, id='named_unity_finance_fraction_is_holdable'),
        pytest.param(3.0, 'startup_months', 'startup_months', 3, id='named_default_startup_months_is_holdable'),
        pytest.param(0.0, 'startup_months', 'startup_months', 0, id='named_zero_startup_months_is_holdable'),
        pytest.param(12.0, 'startup_months', 'startup_months', 12, id='named_twelve_startup_months_is_holdable'),
        pytest.param(1.0, 'startup_FOCfrac', 'startup_FOCfrac', 1.0, id='named_default_startup_FOCfrac_is_holdable'),
        pytest.param(0.0, 'startup_FOCfrac', 'startup_FOCfrac', 0.0, id='named_zero_startup_FOCfrac_is_holdable'),
        pytest.param(0.75, 'startup_VOCfrac', 'startup_VOCfrac', 0.75, id='named_default_startup_VOCfrac_is_holdable'),
        pytest.param(0.0, 'startup_VOCfrac', 'startup_VOCfrac', 0.0, id='named_zero_startup_VOCfrac_is_holdable'),
        pytest.param(1.0, 'startup_VOCfrac', 'startup_VOCfrac', 1.0, id='named_unity_startup_VOCfrac_is_holdable'),
        pytest.param(0.5, 'startup_salesfrac', 'startup_salesfrac', 0.5, id='named_default_startup_salesfrac_is_holdable'),
        pytest.param(0.0, 'startup_salesfrac', 'startup_salesfrac', 0.0, id='named_zero_startup_salesfrac_is_holdable'),
        pytest.param(1.0, 'startup_salesfrac', 'startup_salesfrac', 1.0, id='named_unity_startup_salesfrac_is_holdable'),
        pytest.param(0.05, 'WC_over_FCI', 'WC_over_FCI', 0.05, id='named_default_WC_over_FCI_is_holdable'),
        pytest.param(0.0, 'WC_over_FCI', 'WC_over_FCI', 0.0, id='named_zero_WC_over_FCI_is_holdable'),
        pytest.param(1.0, 'WC_over_FCI', 'WC_over_FCI', 1.0, id='named_unity_WC_over_FCI_is_holdable'),
        pytest.param(0.04, 'warehouse', 'warehouse', 0.04, id='named_default_warehouse_is_holdable'),
        pytest.param(0.0, 'warehouse', 'warehouse', 0.0, id='named_zero_warehouse_is_holdable'),
        pytest.param(1.0, 'warehouse', 'warehouse', 1.0, id='named_unity_warehouse_is_holdable'),
        pytest.param(0.09, 'site_development', 'site_development', 0.09, id='named_default_site_development_is_holdable'),
        pytest.param(0.0, 'site_development', 'site_development', 0.0, id='named_zero_site_development_is_holdable'),
        pytest.param(1.0, 'site_development', 'site_development', 1.0, id='named_unity_site_development_is_holdable'),
        pytest.param(0.045, 'additional_piping', 'additional_piping', 0.045, id='named_default_additional_piping_is_holdable'),
        pytest.param(0.0, 'additional_piping', 'additional_piping', 0.0, id='named_zero_additional_piping_is_holdable'),
        pytest.param(1.0, 'additional_piping', 'additional_piping', 1.0, id='named_unity_additional_piping_is_holdable'),
        pytest.param(0.1, 'proratable_costs', 'proratable_costs', 0.1, id='named_default_proratable_costs_is_holdable'),
        pytest.param(0.0, 'proratable_costs', 'proratable_costs', 0.0, id='named_zero_proratable_costs_is_holdable'),
        pytest.param(1.0, 'proratable_costs', 'proratable_costs', 1.0, id='named_unity_proratable_costs_is_holdable'),
        pytest.param(0.1, 'field_expenses', 'field_expenses', 0.1, id='named_default_field_expenses_is_holdable'),
        pytest.param(0.0, 'field_expenses', 'field_expenses', 0.0, id='named_zero_field_expenses_is_holdable'),
        pytest.param(1.0, 'field_expenses', 'field_expenses', 1.0, id='named_unity_field_expenses_is_holdable'),
        pytest.param(0.2, 'construction', 'construction', 0.2, id='named_default_construction_is_holdable'),
        pytest.param(0.0, 'construction', 'construction', 0.0, id='named_zero_construction_is_holdable'),
        pytest.param(1.0, 'construction', 'construction', 1.0, id='named_unity_construction_is_holdable'),
        pytest.param(0.4, 'contingency', 'contingency', 0.4, id='named_default_contingency_is_holdable'),
        pytest.param(0.0, 'contingency', 'contingency', 0.0, id='named_zero_contingency_is_holdable'),
        pytest.param(1.0, 'contingency', 'contingency', 1.0, id='named_unity_contingency_is_holdable'),
        pytest.param(0.1, 'other_indirect_costs', 'other_indirect_costs', 0.1, id='named_default_other_indirect_costs_is_holdable'),
        pytest.param(0.0, 'other_indirect_costs', 'other_indirect_costs', 0.0, id='named_zero_other_indirect_costs_is_holdable'),
        pytest.param(1.0, 'other_indirect_costs', 'other_indirect_costs', 1.0, id='named_unity_other_indirect_costs_is_holdable'),
        pytest.param(0.007, 'property_insurance', 'property_insurance', 0.007, id='named_default_property_insurance_is_holdable'),
        pytest.param(0.0, 'property_insurance', 'property_insurance', 0.0, id='named_zero_property_insurance_is_holdable'),
        pytest.param(1.0, 'property_insurance', 'property_insurance', 1.0, id='named_unity_property_insurance_is_holdable'),
        pytest.param(0.03, 'maintenance', 'maintenance', 0.03, id='named_default_maintenance_is_holdable'),
        pytest.param(0.0, 'maintenance', 'maintenance', 0.0, id='named_zero_maintenance_is_holdable'),
        pytest.param(1.0, 'maintenance', 'maintenance', 1.0, id='named_unity_maintenance_is_holdable'),
        pytest.param('MACRS7', 'depreciation', 'depreciation', 'MACRS7', id='named_default_depreciation_is_holdable'),
        pytest.param('MACRS20', 'steam_power_depreciation', 'steam_power_depreciation', 'MACRS20', id='named_default_steam_power_is_holdable'),
    ],
)
def test_dissolution_temp_c_alias_stamps_the_public_name_cases(monkeypatch, value, value_2, key, value_3):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, key: value_3}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[value_2] for row in keys} == {value}


def test_c1_at_wrong_t_does_not_fill_named_c1_and_t(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(energy_case="C1", dissolution_temperature_c=80.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "energy_case": "C1",
                    "dissolution_temperature_c": 90.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}


@pytest.mark.parametrize(
    "field, value",
    [
        pytest.param("dissolution_temperature_c", 90.0, id="dissolution_temperature_c"),
        pytest.param("precipitation_temperature_c", 40.0, id="precipitation_temperature_c"),
        pytest.param("precipitation_temp_c", 40.0, id="precipitation_temp_c"),
        pytest.param("solvent_price_usd_per_kg", 2.0, id="solvent_price_usd_per_kg"),
        pytest.param("solvent_price", 2.0, id="solvent_price"),
        pytest.param("solvent_loss_pct", 3.0, id="solvent_loss_pct"),
        pytest.param("feedstock_distance_km", 250.0, id="feedstock_distance_km"),
        pytest.param("dissolution_capacity", 5.0, id="dissolution_capacity"),
        pytest.param("labor_cost_usd_per_employee_yr", 150000.0, id="labor_cost_usd_per_employee_yr"),
        pytest.param("labor_cost", 150000.0, id="labor_cost"),
        pytest.param("sell_leftover_plastic", True, id="sell_leftover_plastic"),
        pytest.param("burn_leftover_plastic", True, id="burn_leftover_plastic"),
        pytest.param("precipitation_temperature_format", "drop", id="precipitation_temperature_format"),
        pytest.param("irr", 0.12, id="irr"),
        pytest.param("income_tax", 0.25, id="income_tax"),
        pytest.param("operating_days", 365, id="operating_days"),
        pytest.param("labor_burden", 1.5, id="labor_burden"),
        pytest.param("finance_interest", 0.12, id="finance_interest"),
        pytest.param("finance_years", 15, id="finance_years"),
        pytest.param("finance_fraction", 0.4, id="finance_fraction"),
        pytest.param("startup_months", 6, id="startup_months"),
        pytest.param("startup_FOCfrac", 0.5, id="startup_FOCfrac"),
        pytest.param("startup_VOCfrac", 0.5, id="startup_VOCfrac"),
        pytest.param("startup_salesfrac", 0.25, id="startup_salesfrac"),
        pytest.param("WC_over_FCI", 0.1, id="WC_over_FCI"),
        pytest.param("warehouse", 0.1, id="warehouse"),
        pytest.param("site_development", 0.1, id="site_development"),
        pytest.param("additional_piping", 0.1, id="additional_piping"),
        pytest.param("proratable_costs", 0.2, id="proratable_costs"),
        pytest.param("field_expenses", 0.2, id="field_expenses"),
        pytest.param("construction", 0.1, id="construction"),
        pytest.param("contingency", 0.1, id="contingency"),
        pytest.param("other_indirect_costs", 0.2, id="other_indirect_costs"),
        pytest.param("property_insurance", 0.1, id="property_insurance"),
        pytest.param("maintenance", 0.1, id="maintenance"),
        pytest.param("depreciation", "MACRS5", id="depreciation"),
        pytest.param("steam_power_depreciation", "MACRS7", id="steam_power"),
        pytest.param("lang_factor", 3.0, id="lang_factor"),
    ],
)
def test_process_field_kwarg_is_unknown_extra(monkeypatch, field, value):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs(**{field: value})))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == [field]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


@pytest.mark.parametrize(
    ('value', 'key', 'value_2'),
    [
        pytest.param('dissolution_temperature_c', 'dissolution_temperature_c', 90.0, id='dissolution_t'),
        pytest.param('precipitation_temperature_c', 'precipitation_temperature_c', 40.0, id='precipitation_t'),
        pytest.param('solvent_price_usd_per_kg', 'solvent_price_usd_per_kg', 2.0, id='solvent_price'),
        pytest.param('solvent_loss_pct', 'solvent_loss_pct', 3.0, id='solvent_loss'),
        pytest.param('feedstock_distance_km', 'feedstock_distance_km', 250.0, id='feedstock_distance'),
        pytest.param('dissolution_capacity', 'dissolution_capacity', 5.0, id='dissolution_capacity'),
        pytest.param('labor_cost_usd_per_employee_yr', 'labor_cost_usd_per_employee_yr', 150000.0, id='labor'),
        pytest.param('sell_leftover_plastic', 'sell_leftover_plastic', True, id='sell_leftover'),
        pytest.param('burn_leftover_plastic', 'burn_leftover_plastic', True, id='burn_leftover'),
        pytest.param('precipitation_temperature_format', 'precipitation_temperature_format', 'drop', id='precipitation_format'),
        pytest.param('irr', 'irr', 0.12, id='irr'),
        pytest.param('income_tax', 'income_tax', 0.25, id='income_tax'),
        pytest.param('operating_days', 'operating_days', 365, id='operating_days'),
        pytest.param('labor_burden', 'labor_burden', 1.5, id='labor_burden'),
        pytest.param('finance_interest', 'finance_interest', 0.12, id='finance_interest'),
        pytest.param('finance_years', 'finance_years', 15, id='finance_years'),
        pytest.param('finance_fraction', 'finance_fraction', 0.4, id='finance_fraction'),
        pytest.param('startup_months', 'startup_months', 6, id='startup_months'),
        pytest.param('startup_FOCfrac', 'startup_FOCfrac', 0.5, id='startup_FOCfrac'),
        pytest.param('startup_VOCfrac', 'startup_VOCfrac', 0.5, id='startup_VOCfrac'),
        pytest.param('startup_salesfrac', 'startup_salesfrac', 0.25, id='startup_salesfrac'),
        pytest.param('WC_over_FCI', 'WC_over_FCI', 0.1, id='WC_over_FCI'),
        pytest.param('warehouse', 'warehouse', 0.1, id='warehouse'),
        pytest.param('site_development', 'site_development', 0.1, id='site_development'),
        pytest.param('additional_piping', 'additional_piping', 0.1, id='additional_piping'),
        pytest.param('proratable_costs', 'proratable_costs', 0.2, id='proratable_costs'),
        pytest.param('field_expenses', 'field_expenses', 0.2, id='field_expenses'),
        pytest.param('construction', 'construction', 0.1, id='construction'),
        pytest.param('contingency', 'contingency', 0.1, id='contingency'),
        pytest.param('other_indirect_costs', 'other_indirect_costs', 0.2, id='other_indirect_costs'),
        pytest.param('property_insurance', 'property_insurance', 0.1, id='property_insurance'),
        pytest.param('maintenance', 'maintenance', 0.1, id='maintenance'),
        pytest.param('depreciation', 'depreciation', 'MACRS5', id='depreciation'),
        pytest.param('steam_power_depreciation', 'steam_power_depreciation', 'MACRS7', id='steam_power'),
        pytest.param('lang_factor', 'lang_factor', 3.0, id='lang_factor'),
    ],
)
def test_nested_does_not_count(monkeypatch, value, key, value_2):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {key: value_2},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value) is None


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        pytest.param('dissolution_temperature_c', 'hot', id='invalid_dissolution_t_is_not_a'),
        pytest.param('precipitation_temperature_c', 'cold', id='invalid_precipitation_t_is_not_a'),
        pytest.param('solvent_price_usd_per_kg', 'cheap', id='invalid_solvent_price_is_not_a'),
        pytest.param('solvent_loss_pct', 'leaky', id='invalid_solvent_loss_is_not_a'),
        pytest.param('feedstock_distance_km', 'far', id='invalid_feedstock_distance_is_not_a'),
        pytest.param('dissolution_capacity', 'wide', id='invalid_dissolution_capacity_is_not_a'),
        pytest.param('labor_cost_usd_per_employee_yr', 'salary', id='invalid_labor_is_not_a'),
        pytest.param('sell_leftover_plastic', 'maybe', id='invalid_sell_leftover_is_not_a'),
        pytest.param('burn_leftover_plastic', 'maybe', id='invalid_burn_leftover_is_not_a'),
        pytest.param('burn_leftover_plastic', 1, id='integer_one_is_not_a_burn'),
        pytest.param('precipitation_temperature_format', 1, id='integer_one_is_not_a_precipitation_format'),
        pytest.param('precipitation_configuration', 'SOLVENT MIXING', id='uppercase_mixing_is_not_a_configuration'),
        pytest.param('precipitation_configuration', 1, id='integer_one_is_not_a_precipitation_configuration'),
        pytest.param('irr', 'maybe', id='invalid_irr_is_not_a'),
        pytest.param('irr', 0, id='zero_irr_is_not_a_remnant'),
        pytest.param('income_tax', 'maybe', id='invalid_income_tax_is_not_a'),
        pytest.param('operating_days', 'maybe', id='invalid_operating_days_is_not_a'),
        pytest.param('labor_burden', 'maybe', id='invalid_labor_burden_is_not_a'),
        pytest.param('finance_interest', 'maybe', id='invalid_finance_interest_is_not_a'),
        pytest.param('finance_years', 'maybe', id='invalid_finance_years_is_not_a'),
        pytest.param('finance_years', 10.5, id='fractional_finance_years_is_not_a_remnant'),
        pytest.param('finance_fraction', 'maybe', id='invalid_finance_fraction_is_not_a'),
        pytest.param('startup_months', 'maybe', id='invalid_startup_months_is_not_a'),
        pytest.param('startup_months', 13, id='startup_months_above_one_year_is_not_a_remnant'),
        pytest.param('startup_FOCfrac', 'maybe', id='invalid_startup_FOCfrac_is_not_a'),
        pytest.param('startup_VOCfrac', 'maybe', id='invalid_startup_VOCfrac_is_not_a'),
        pytest.param('startup_salesfrac', 'maybe', id='invalid_startup_salesfrac_is_not_a'),
        pytest.param('WC_over_FCI', 'maybe', id='invalid_WC_over_FCI_is_not_a'),
        pytest.param('warehouse', 'maybe', id='invalid_warehouse_is_not_a'),
        pytest.param('site_development', 'maybe', id='invalid_site_development_is_not_a'),
        pytest.param('additional_piping', 'maybe', id='invalid_additional_piping_is_not_a'),
        pytest.param('proratable_costs', 'maybe', id='invalid_proratable_costs_is_not_a'),
        pytest.param('field_expenses', 'maybe', id='invalid_field_expenses_is_not_a'),
        pytest.param('construction', 'maybe', id='invalid_construction_is_not_a'),
        pytest.param('contingency', 'maybe', id='invalid_contingency_is_not_a'),
        pytest.param('other_indirect_costs', 'maybe', id='invalid_other_indirect_costs_is_not_a'),
        pytest.param('property_insurance', 'maybe', id='invalid_property_insurance_is_not_a'),
        pytest.param('maintenance', 'maybe', id='invalid_maintenance_is_not_a'),
        pytest.param('depreciation', 'macrs7', id='invalid_depreciation_is_not_a'),
        pytest.param('depreciation', 'MACRS4', id='unimplemented_macrs_is_not_a_remnant'),
    ],
)
def test_listing(monkeypatch, key, value):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, key: value},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


@pytest.mark.parametrize(
    ('value', 'key', 'value_2'),
    [
        pytest.param('precipitation_temperature_c', 'precipitation_temperature_c', '', id='empty_precipitation_t_is_the_omitted_grain'),
        pytest.param('solvent_price_usd_per_kg', 'solvent_price_usd_per_kg', '', id='empty_solvent_price_is_the_omitted_grain'),
        pytest.param('solvent_price_usd_per_kg', 'solvent_loss_pct', 3.0, id='solvent_loss_does_not_stamp_solvent_price'),
        pytest.param('solvent_loss_pct', 'solvent_loss_pct', '', id='empty_solvent_loss_is_the_omitted_grain'),
        pytest.param('solvent_loss_pct', 'feedstock_distance_km', 100.0, id='feedstock_distance_does_not_stamp_solvent_loss'),
        pytest.param('solvent_loss_pct', 'solvent_loss', 3.0, id='solvent_loss_without_pct_does_not_stamp'),
        pytest.param('feedstock_distance_km', 'feedstock_distance_km', '', id='empty_feedstock_distance_is_the_omitted_grain'),
        pytest.param('feedstock_distance_km', 'dissolution_capacity', 5.0, id='dissolution_capacity_does_not_stamp_feedstock_distance'),
        pytest.param('feedstock_distance_km', 'feedstock_distance', 250.0, id='feedstock_distance_without_km_does_not_stamp'),
        pytest.param('dissolution_capacity', 'dissolution_capacity', '', id='empty_dissolution_capacity_is_the_omitted_grain'),
        pytest.param('dissolution_capacity', 'labor_cost_usd_per_employee_yr', 150000.0, id='labor_does_not_stamp_dissolution_capacity'),
        pytest.param('labor_cost_usd_per_employee_yr', 'labor_cost_usd_per_employee_yr', '', id='empty_labor_is_the_omitted_grain'),
        pytest.param('labor_cost_usd_per_employee_yr', 'labor_burden', 0.9, id='labor_burden_does_not_stamp_labor'),
        pytest.param('labor_cost_usd_per_employee_yr', 'sell_leftover_plastic', True, id='sell_leftover_does_not_stamp_labor'),
        pytest.param('sell_leftover_plastic', 'sell_leftover_plastic', '', id='empty_sell_leftover_is_the_omitted_grain'),
        pytest.param('sell_leftover_plastic', 'burn_leftover_plastic', True, id='burn_leftover_does_not_stamp_sell'),
        pytest.param('burn_leftover_plastic', 'burn_leftover_plastic', '', id='empty_burn_leftover_is_the_omitted_grain'),
        pytest.param('burn_leftover_plastic', 'precipitation_temperature_format', 'constant', id='precipitation_format_does_not_stamp_burn'),
        pytest.param('precipitation_temperature_format', 'precipitation_temperature_format', '', id='empty_precipitation_format_is_the_omitted_grain'),
        pytest.param('precipitation_temperature_format', 'precipitation_configuration', 'solvent mixing', id='precipitation_configuration_does_not_stamp_format'),
        pytest.param('precipitation_configuration', 'precipitation_configuration', '', id='empty_precipitation_configuration_is_the_omitted_grain'),
        pytest.param('precipitation_configuration', 'irr', 0.12, id='irr_does_not_stamp_precipitation_configuration'),
        pytest.param('irr', 'irr', '', id='empty_irr_is_the_omitted_grain'),
        pytest.param('irr', 'income_tax', 0.25, id='income_tax_does_not_stamp_irr'),
        pytest.param('income_tax', 'income_tax', '', id='empty_income_tax_is_the_omitted_grain'),
        pytest.param('income_tax', 'operating_days', 365, id='operating_days_does_not_stamp_income_tax'),
        pytest.param('operating_days', 'operating_days', '', id='empty_operating_days_is_the_omitted_grain'),
        pytest.param('operating_days', 'labor_burden', 1.5, id='labor_burden_does_not_stamp_operating_days'),
        pytest.param('labor_burden', 'labor_burden', '', id='empty_labor_burden_is_the_omitted_grain'),
        pytest.param('labor_burden', 'finance_interest', 0.12, id='finance_interest_does_not_stamp_labor_burden'),
        pytest.param('finance_interest', 'finance_interest', '', id='empty_finance_interest_is_the_omitted_grain'),
        pytest.param('finance_interest', 'finance_years', 15, id='finance_years_does_not_stamp_finance_interest'),
        pytest.param('finance_years', 'finance_years', '', id='empty_finance_years_is_the_omitted_grain'),
        pytest.param('finance_years', 'finance_fraction', 0.4, id='finance_fraction_does_not_stamp_finance_years'),
        pytest.param('finance_fraction', 'finance_fraction', '', id='empty_finance_fraction_is_the_omitted_grain'),
        pytest.param('startup_months', 'startup_months', '', id='empty_startup_months_is_the_omitted_grain'),
        pytest.param('startup_FOCfrac', 'startup_FOCfrac', '', id='empty_startup_FOCfrac_is_the_omitted_grain'),
        pytest.param('startup_VOCfrac', 'startup_VOCfrac', '', id='empty_startup_VOCfrac_is_the_omitted_grain'),
        pytest.param('startup_salesfrac', 'startup_salesfrac', '', id='empty_startup_salesfrac_is_the_omitted_grain'),
        pytest.param('WC_over_FCI', 'WC_over_FCI', '', id='empty_WC_over_FCI_is_the_omitted_grain'),
        pytest.param('warehouse', 'warehouse', '', id='empty_warehouse_is_the_omitted_grain'),
        pytest.param('site_development', 'site_development', '', id='empty_site_development_is_the_omitted_grain'),
        pytest.param('additional_piping', 'additional_piping', '', id='empty_additional_piping_is_the_omitted_grain'),
        pytest.param('proratable_costs', 'proratable_costs', '', id='empty_proratable_costs_is_the_omitted_grain'),
        pytest.param('field_expenses', 'field_expenses', '', id='empty_field_expenses_is_the_omitted_grain'),
        pytest.param('construction', 'construction', '', id='empty_construction_is_the_omitted_grain'),
        pytest.param('contingency', 'contingency', '', id='empty_contingency_is_the_omitted_grain'),
        pytest.param('other_indirect_costs', 'other_indirect_costs', '', id='empty_other_indirect_costs_is_the_omitted_grain'),
        pytest.param('property_insurance', 'property_insurance', '', id='empty_property_insurance_is_the_omitted_grain'),
        pytest.param('maintenance', 'maintenance', '', id='empty_maintenance_is_the_omitted_grain'),
        pytest.param('depreciation', 'depreciation', '', id='empty_depreciation_is_the_omitted_grain'),
        pytest.param('duration', 'duration', '', id='empty_duration_is_the_omitted_grain'),
        pytest.param('construction_schedule', 'construction_schedule', '', id='empty_construction_schedule_is_the_omitted_grain'),
        pytest.param('steam_power_depreciation', 'steam_power_depreciation', '', id='empty_steam_power_depreciation_is_the_omitted_grain'),
        pytest.param('lang_factor', 'lang_factor', '', id='empty_lang_factor_is_the_omitted_grain'),
    ],
)
def test_empty_precipitation_t_is_the_omitted_grain_cases(monkeypatch, value, key, value_2):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, key: value_2},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value) is None


@pytest.mark.parametrize(
    ('value', 'value_2', 'key', 'key_2', 'value_3', 'value_4'),
    [
        pytest.param(40.0, 'precipitation_temperature_c', 'precipitation_temperature_c', 'precipitation_temp_c', 40.0, 35.0, id='precipitation_t'),
        pytest.param(2.0, 'solvent_price_usd_per_kg', 'solvent_price_usd_per_kg', 'solvent_price', 2.0, 1.5, id='solvent_price'),
        pytest.param(150000.0, 'labor_cost_usd_per_employee_yr', 'labor_cost_usd_per_employee_yr', 'labor_cost', 150000.0, 120000.0, id='labor'),
    ],
)
def test_public_wins_over_alias(monkeypatch, value, value_2, key, key_2, value_3, value_4):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                key: value_3,
                key_2: value_4,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[value_2] for row in keys} == {value}


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'key', 'value_4'),
    [
        pytest.param(90.0, 'dissolution_temperature_c', 'precipitation_temperature_c', 'dissolution_temperature_c', 90.0, id='dissolution_t_does_not_stamp_precipitation_t'),
        pytest.param(40.0, 'precipitation_temperature_c', 'dissolution_temperature_c', 'precipitation_temperature_c', 40.0, id='precipitation_t_does_not_stamp_dissolution_t'),
        pytest.param(40.0, 'precipitation_temperature_c', 'solvent_price_usd_per_kg', 'precipitation_temperature_c', 40.0, id='precipitation_t_does_not_stamp_solvent_price'),
        pytest.param(2.0, 'solvent_price_usd_per_kg', 'precipitation_temperature_c', 'solvent_price_usd_per_kg', 2.0, id='solvent_price_does_not_stamp_precipitation_t'),
        pytest.param(2.0, 'solvent_price_usd_per_kg', 'solvent_loss_pct', 'solvent_price_usd_per_kg', 2.0, id='solvent_price_does_not_stamp_solvent_loss'),
        pytest.param(3.0, 'solvent_loss_pct', 'feedstock_distance_km', 'solvent_loss_pct', 3.0, id='solvent_loss_does_not_stamp_feedstock_distance'),
        pytest.param(3.0, 'solvent_loss_pct', 'feedstock_distance_km', 'solvent_loss_pct', 3.0, id='solvent_loss_does_not_stamp_distance_on_this_slice'),
        pytest.param(250.0, 'feedstock_distance_km', 'dissolution_capacity', 'feedstock_distance_km', 250.0, id='feedstock_distance_does_not_stamp_dissolution_capacity'),
        pytest.param(250.0, 'feedstock_distance_km', 'dissolution_capacity', 'feedstock_distance_km', 250.0, id='feedstock_distance_does_not_stamp_dissolution_capacity_on_this_slice'),
        pytest.param(5.0, 'dissolution_capacity', 'labor_cost_usd_per_employee_yr', 'dissolution_capacity', 5.0, id='dissolution_capacity_does_not_stamp_labor'),
        pytest.param(5.0, 'dissolution_capacity', 'labor_cost_usd_per_employee_yr', 'dissolution_capacity', 5.0, id='dissolution_capacity_does_not_stamp_labor_on_this_slice'),
        pytest.param(150000.0, 'labor_cost_usd_per_employee_yr', 'labor_burden', 'labor_cost_usd_per_employee_yr', 150000.0, id='labor_does_not_stamp_labor_burden'),
        pytest.param(150000.0, 'labor_cost_usd_per_employee_yr', 'sell_leftover_plastic', 'labor_cost_usd_per_employee_yr', 150000.0, id='labor_does_not_stamp_sell_leftover'),
        pytest.param('drop', 'precipitation_temperature_format', 'precipitation_configuration', 'precipitation_temperature_format', 'drop', id='precipitation_format_does_not_stamp_configuration_on_this_slice'),
        pytest.param(0.12, 'irr', 'income_tax', 'irr', 0.12, id='irr_does_not_stamp_income_tax'),
        pytest.param(0.12, 'irr', 'income_tax', 'irr', 0.12, id='irr_does_not_stamp_income_tax_on_this_slice'),
        pytest.param(0.25, 'income_tax', 'operating_days', 'income_tax', 0.25, id='income_tax_does_not_stamp_operating_days_on_this_slice'),
        pytest.param(365.0, 'operating_days', 'labor_burden', 'operating_days', 365, id='operating_days_does_not_stamp_labor_burden_on_this_slice'),
        pytest.param(150000.0, 'labor_cost_usd_per_employee_yr', 'labor_burden', 'labor_cost_usd_per_employee_yr', 150000.0, id='labor_cost_does_not_stamp_labor_burden'),
        pytest.param(1.5, 'labor_burden', 'finance_interest', 'labor_burden', 1.5, id='labor_burden_does_not_stamp_finance_interest_on_this_slice'),
        pytest.param(0.12, 'finance_interest', 'finance_years', 'finance_interest', 0.12, id='finance_interest_does_not_stamp_finance_years_on_this_slice'),
        pytest.param(15.0, 'finance_years', 'finance_fraction', 'finance_years', 15, id='finance_years_does_not_stamp_finance_fraction_on_this_slice'),
        pytest.param(0.4, 'finance_fraction', 'startup_months', 'finance_fraction', 0.4, id='finance_fraction_does_not_stamp_startup_months_on_this_slice'),
        pytest.param(6.0, 'startup_months', 'startup_FOCfrac', 'startup_months', 6, id='startup_months_does_not_stamp_startup_FOCfrac_on_this_slice'),
        pytest.param(0.5, 'startup_FOCfrac', 'startup_VOCfrac', 'startup_FOCfrac', 0.5, id='startup_FOCfrac_does_not_stamp_startup_VOCfrac_on_this_slice'),
        pytest.param(0.5, 'startup_VOCfrac', 'startup_salesfrac', 'startup_VOCfrac', 0.5, id='startup_VOCfrac_does_not_stamp_startup_salesfrac_on_this_slice'),
    ],
)
def test_dissolution_t_does_not_stamp_precipitation_t_cases(monkeypatch, value, value_2, value_3, key, value_4):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, key: value_4},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value_2) == value
        assert row.get(value_3) is None


def test_c1_and_dissolution_t_at_wrong_precip_do_not_fill(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=35.0,
            ),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "energy_case": "C1",
                    "dissolution_temperature_c": 90.0,
                    "precipitation_temperature_c": 40.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}


@pytest.mark.parametrize(
    "remnant, field, missing, planted, requested",
    [
        pytest.param(35.0, "precipitation_temperature_c", 40.0, 40.0, 40.0, id="precip"),
        pytest.param(1.5, "solvent_price_usd_per_kg", 2.0, 2.0, 2.0, id="price"),
        pytest.param(0.5, "solvent_loss_pct", 3.0, 3.0, 3.0, id="loss"),
        pytest.param(100.0, "feedstock_distance_km", 250.0, 250.0, 250.0, id="distance"),
        pytest.param(3.0, "dissolution_capacity", 5.0, 5.0, 5.0, id="capacity"),
        pytest.param(120000.0, "labor_cost_usd_per_employee_yr", 150000.0, 150000.0, 150000.0, id="labor"),
        pytest.param("constant", "precipitation_temperature_format", "drop", "drop", "drop", id="format"),
        pytest.param(0.1, "irr", 0.12, 0.12, 0.12, id="irr"),
        pytest.param(0.21, "income_tax", 0.25, 0.25, 0.25, id="income_tax"),
        pytest.param(350.4, "operating_days", 365.0, 365.0, 365, id="operating_days"),
        pytest.param(0.9, "labor_burden", 1.5, 1.5, 1.5, id="labor_burden"),
        pytest.param(0.08, "finance_interest", 0.12, 0.12, 0.12, id="finance_interest"),
        pytest.param(10, "finance_years", 15.0, 15, 15, id="finance_years"),
        pytest.param(0.0, "finance_fraction", 0.4, 0.4, 0.4, id="finance_fraction"),
        pytest.param(3, "startup_months", 6.0, 6, 6, id="startup_months"),
        pytest.param(1.0, "startup_FOCfrac", 0.5, 0.5, 0.5, id="startup_FOCfrac"),
        pytest.param(0.75, "startup_VOCfrac", 0.5, 0.5, 0.5, id="startup_VOCfrac"),
        pytest.param(0.5, "startup_salesfrac", 0.25, 0.25, 0.25, id="startup_salesfrac"),
        pytest.param(0.05, "WC_over_FCI", 0.1, 0.1, 0.1, id="WC_over_FCI"),
        pytest.param(0.04, "warehouse", 0.1, 0.1, 0.1, id="warehouse"),
        pytest.param(0.09, "site_development", 0.1, 0.1, 0.1, id="site_development"),
        pytest.param(0.045, "additional_piping", 0.1, 0.1, 0.1, id="additional_piping"),
        pytest.param(0.1, "proratable_costs", 0.2, 0.2, 0.2, id="proratable_costs"),
        pytest.param(0.1, "field_expenses", 0.2, 0.2, 0.2, id="field_expenses"),
        pytest.param(0.2, "construction", 0.1, 0.1, 0.1, id="construction"),
        pytest.param(0.4, "contingency", 0.1, 0.1, 0.1, id="contingency"),
        pytest.param(0.1, "other_indirect_costs", 0.2, 0.2, 0.2, id="other_indirect_costs"),
        pytest.param(0.007, "property_insurance", 0.1, 0.1, 0.1, id="property_insurance"),
        pytest.param(0.03, "maintenance", 0.1, 0.1, 0.1, id="maintenance"),
        pytest.param("MACRS7", "depreciation", "MACRS5", "MACRS5", "MACRS5", id="depreciation"),
        pytest.param("MACRS20", "steam_power_depreciation", "MACRS7", "MACRS7", "MACRS7", id="steam_power"),
    ],
)
def test_mixed_handle_leaves_the_unmatched_remnant(monkeypatch, remnant, field, missing, planted, requested):
    _forbid_live_rank_solvent_maps(monkeypatch)
    rows = _complete_d18_rows(**{field: planted})
    rows[-1][field] = remnant
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(
            tea.rank_landscape(**_sequence_kwargs(handle=handle, process_config={**_CAP_20KT, field: requested}))
        )
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0][field] == missing
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'value_4', 'key', 'value_5'),
    [
        pytest.param(40.0, 'precipitation_temp_c', 40.0, 'precipitation_temperature_c', 'precipitation_temperature_c', 40.0, id='precipitation_t'),
        pytest.param(2.0, 'solvent_price', 2.0, 'solvent_price_usd_per_kg', 'solvent_price_usd_per_kg', 2.0, id='solvent_price'),
        pytest.param(150000.0, 'labor_cost', 150000.0, 'labor_cost_usd_per_employee_yr', 'labor_cost_usd_per_employee_yr', 150000.0, id='labor'),
    ],
)
def test_alias_only_row_does_not_fill_named(monkeypatch, value, value_2, value_3, value_4, key, value_5):
    _forbid_live_rank_solvent_maps(monkeypatch)
    rows = _complete_d18_rows()
    for row in rows:
        row[value_2] = value
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, key: value_5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[value_4] for row in keys} == {value_3}


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3'),
    [
        pytest.param(0.01, 'solvent_loss_pct', 'solvent_loss_pct', id='solvent_loss_is_not_a_silent_default'),
        pytest.param(0.0, 'feedstock_distance_km', 'feedstock_distance_km', id='feedstock_distance_is_not_a_silent_zero'),
        pytest.param(3.0, 'dissolution_capacity', 'dissolution_capacity', id='dissolution_capacity_is_not_a_silent_default'),
        pytest.param(120000.0, 'labor_cost_usd_per_employee_yr', 'labor_cost_usd_per_employee_yr', id='labor_is_not_a_silent_default'),
        pytest.param('constant', 'precipitation_temperature_format', 'precipitation_temperature_format', id='precipitation_format_is_not_a_silent_constant'),
        pytest.param(0.1, 'irr', 'irr', id='irr_is_not_a_silent_tenth'),
        pytest.param(0.21, 'income_tax', 'income_tax', id='income_tax_is_not_a_silent_default'),
        pytest.param(350.4, 'operating_days', 'operating_days', id='operating_days_is_not_a_silent_default'),
        pytest.param(0.9, 'labor_burden', 'labor_burden', id='labor_burden_is_not_a_silent_default'),
        pytest.param(0.08, 'finance_interest', 'finance_interest', id='finance_interest_is_not_a_silent_default'),
        pytest.param(10, 'finance_years', 'finance_years', id='finance_years_is_not_a_silent_default'),
        pytest.param(0.0, 'finance_fraction', 'finance_fraction', id='finance_fraction_is_not_a_silent_default'),
        pytest.param(3, 'startup_months', 'startup_months', id='startup_months_is_not_a_silent_default'),
        pytest.param(1, 'startup_FOCfrac', 'startup_FOCfrac', id='startup_FOCfrac_is_not_a_silent_default'),
        pytest.param(0.75, 'startup_VOCfrac', 'startup_VOCfrac', id='startup_VOCfrac_is_not_a_silent_default'),
        pytest.param(0.5, 'startup_salesfrac', 'startup_salesfrac', id='startup_salesfrac_is_not_a_silent_default'),
        pytest.param(0.05, 'WC_over_FCI', 'WC_over_FCI', id='WC_over_FCI_is_not_a_silent_default'),
        pytest.param(0.04, 'warehouse', 'warehouse', id='warehouse_is_not_a_silent_default'),
        pytest.param(0.09, 'site_development', 'site_development', id='site_development_is_not_a_silent_default'),
        pytest.param(0.045, 'additional_piping', 'additional_piping', id='additional_piping_is_not_a_silent_default'),
        pytest.param(0.1, 'proratable_costs', 'proratable_costs', id='proratable_costs_is_not_a_silent_default'),
        pytest.param(0.1, 'field_expenses', 'field_expenses', id='field_expenses_is_not_a_silent_default'),
        pytest.param(0.2, 'construction', 'construction', id='construction_is_not_a_silent_default'),
        pytest.param(0.4, 'contingency', 'contingency', id='contingency_is_not_a_silent_default'),
        pytest.param(0.1, 'other_indirect_costs', 'other_indirect_costs', id='other_indirect_costs_is_not_a_silent_default'),
        pytest.param(0.007, 'property_insurance', 'property_insurance', id='property_insurance_is_not_a_silent_default'),
        pytest.param(0.03, 'maintenance', 'maintenance', id='maintenance_is_not_a_silent_default'),
        pytest.param('MACRS7', 'depreciation', 'depreciation', id='depreciation_is_not_a_silent_default'),
        pytest.param('MACRS20', 'steam_power_depreciation', 'steam_power_depreciation', id='steam_power_depreciation_is_not_a_silent_default'),
        pytest.param(3.0, 'lang_factor', 'lang_factor', id='lang_factor_is_not_a_silent_default'),
    ],
)
def test_omitted_2(monkeypatch, value, value_2, value_3):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value_2) is None
        assert row.get(value_3) != value


@pytest.mark.parametrize(
    ('value', 'value_2', 'key', 'value_3'),
    [
        pytest.param(3.0, 'solvent_loss_pct', 'solvent_loss_pct', 3.0, id='loss_do_not_fill_named_solvent_loss'),
        pytest.param(250.0, 'feedstock_distance_km', 'feedstock_distance_km', 250.0, id='distance_do_not_fill_named_distance'),
        pytest.param(5.0, 'dissolution_capacity', 'dissolution_capacity', 5.0, id='capacity_do_not_fill_named_dissolution_capacity'),
        pytest.param(150000.0, 'labor_cost_usd_per_employee_yr', 'labor_cost_usd_per_employee_yr', 150000.0, id='labor_do_not_fill_named_labor'),
        pytest.param(True, 'sell_leftover_plastic', 'sell_leftover_plastic', True, id='sell_do_not_fill_named_true'),
        pytest.param(True, 'burn_leftover_plastic', 'burn_leftover_plastic', True, id='burn_do_not_fill_named_true'),
        pytest.param('drop', 'precipitation_temperature_format', 'precipitation_temperature_format', 'drop', id='format_do_not_fill_named_drop'),
        pytest.param(0.12, 'irr', 'irr', 0.12, id='irr_do_not_fill_named_irr'),
        pytest.param(0.25, 'income_tax', 'income_tax', 0.25, id='income_tax_do_not_fill_named_tax'),
        pytest.param(365.0, 'operating_days', 'operating_days', 365, id='operating_days_do_not_fill_named_days'),
        pytest.param(1.5, 'labor_burden', 'labor_burden', 1.5, id='labor_burden_do_not_fill_named_burden'),
        pytest.param(0.12, 'finance_interest', 'finance_interest', 0.12, id='finance_interest_do_not_fill_named_rate'),
        pytest.param(15.0, 'finance_years', 'finance_years', 15, id='finance_years_do_not_fill_named_years'),
        pytest.param(0.4, 'finance_fraction', 'finance_fraction', 0.4, id='finance_fraction_do_not_fill_named_fraction'),
        pytest.param(6.0, 'startup_months', 'startup_months', 6, id='startup_months_do_not_fill_named_months'),
        pytest.param(0.5, 'startup_FOCfrac', 'startup_FOCfrac', 0.5, id='startup_FOCfrac_do_not_fill_named_fraction'),
        pytest.param(0.5, 'startup_VOCfrac', 'startup_VOCfrac', 0.5, id='startup_VOCfrac_do_not_fill_named_fraction'),
        pytest.param(0.25, 'startup_salesfrac', 'startup_salesfrac', 0.25, id='startup_salesfrac_do_not_fill_named_fraction'),
        pytest.param(0.1, 'WC_over_FCI', 'WC_over_FCI', 0.1, id='WC_over_FCI_do_not_fill_named_fraction'),
        pytest.param(0.1, 'warehouse', 'warehouse', 0.1, id='warehouse_do_not_fill_named_fraction'),
        pytest.param(0.1, 'site_development', 'site_development', 0.1, id='site_development_do_not_fill_named_fraction'),
        pytest.param(0.1, 'additional_piping', 'additional_piping', 0.1, id='additional_piping_do_not_fill_named_fraction'),
        pytest.param(0.2, 'proratable_costs', 'proratable_costs', 0.2, id='proratable_costs_do_not_fill_named_fraction'),
        pytest.param(0.2, 'field_expenses', 'field_expenses', 0.2, id='field_expenses_do_not_fill_named_fraction'),
        pytest.param(0.1, 'construction', 'construction', 0.1, id='construction_do_not_fill_named_fraction'),
        pytest.param(0.1, 'contingency', 'contingency', 0.1, id='contingency_do_not_fill_named_fraction'),
        pytest.param(0.2, 'other_indirect_costs', 'other_indirect_costs', 0.2, id='other_indirect_costs_do_not_fill_named_fraction'),
        pytest.param(0.1, 'property_insurance', 'property_insurance', 0.1, id='property_insurance_do_not_fill_named_fraction'),
        pytest.param(0.1, 'maintenance', 'maintenance', 0.1, id='maintenance_do_not_fill_named_fraction'),
        pytest.param('MACRS5', 'depreciation', 'depreciation', 'MACRS5', id='depreciation_do_not_fill_named_schedule'),
        pytest.param('MACRS7', 'steam_power_depreciation', 'steam_power_depreciation', 'MACRS7', id='steam_power_do_not_fill_named'),
    ],
)
def test_rows_without(monkeypatch, value, value_2, key, value_3):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, key: value_3},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[value_2] for row in keys} == {value}


@pytest.mark.parametrize(
    "missing, field, planted, requested",
    [
        pytest.param(0.0, "feedstock_distance_km", 100.0, 0.0, id="zero_does_not_accept_another_distance"),
        pytest.param(3.0, "dissolution_capacity", 5.0, 3.0, id="default_capacity_does_not_accept_another_capacity"),
        pytest.param(
            120000.0,
            "labor_cost_usd_per_employee_yr",
            150000.0,
            120000.0,
            id="default_labor_does_not_accept_another_labor",
        ),
        pytest.param(False, "sell_leftover_plastic", True, False, id="false_does_not_accept_true_rows"),
        pytest.param(False, "burn_leftover_plastic", True, False, id="burn_false_does_not_accept_true_rows"),
        pytest.param(
            "constant", "precipitation_temperature_format", "drop", "constant", id="constant_does_not_accept_drop_rows"
        ),
        pytest.param(0.1, "irr", 0.12, 0.1, id="default_irr_does_not_accept_other_rows"),
        pytest.param(0.21, "income_tax", 0.25, 0.21, id="default_income_tax_does_not_accept_other_rows"),
        pytest.param(350.4, "operating_days", 365.0, 350.4, id="default_operating_days_does_not_accept_other_rows"),
        pytest.param(0.9, "labor_burden", 1.5, 0.9, id="default_labor_burden_does_not_accept_other_rows"),
        pytest.param(0.08, "finance_interest", 0.12, 0.08, id="default_finance_interest_does_not_accept_other_rows"),
        pytest.param(10.0, "finance_years", 15, 10, id="default_finance_years_does_not_accept_other_rows"),
        pytest.param(0.0, "finance_fraction", 0.4, 0.0, id="default_finance_fraction_does_not_accept_other_rows"),
        pytest.param(3.0, "startup_months", 6, 3, id="default_startup_months_does_not_accept_other_rows"),
        pytest.param(1.0, "startup_FOCfrac", 0.5, 1.0, id="default_startup_FOCfrac_does_not_accept_other_rows"),
        pytest.param(0.75, "startup_VOCfrac", 0.5, 0.75, id="default_startup_VOCfrac_does_not_accept_other_rows"),
        pytest.param(0.5, "startup_salesfrac", 0.25, 0.5, id="default_startup_salesfrac_does_not_accept_other_rows"),
        pytest.param(0.05, "WC_over_FCI", 0.1, 0.05, id="default_WC_over_FCI_does_not_accept_other_rows"),
        pytest.param(0.04, "warehouse", 0.1, 0.04, id="default_warehouse_does_not_accept_other_rows"),
        pytest.param(0.09, "site_development", 0.1, 0.09, id="default_site_development_does_not_accept_other_rows"),
        pytest.param(0.045, "additional_piping", 0.1, 0.045, id="default_additional_piping_does_not_accept_other_rows"),
        pytest.param(0.1, "proratable_costs", 0.2, 0.1, id="default_proratable_costs_does_not_accept_other_rows"),
        pytest.param(0.1, "field_expenses", 0.2, 0.1, id="default_field_expenses_does_not_accept_other_rows"),
        pytest.param(0.2, "construction", 0.1, 0.2, id="default_construction_does_not_accept_other_rows"),
        pytest.param(0.4, "contingency", 0.1, 0.4, id="default_contingency_does_not_accept_other_rows"),
        pytest.param(
            0.1, "other_indirect_costs", 0.2, 0.1, id="default_other_indirect_costs_does_not_accept_other_rows"
        ),
        pytest.param(
            0.007, "property_insurance", 0.1, 0.007, id="default_property_insurance_does_not_accept_other_rows"
        ),
        pytest.param(0.03, "maintenance", 0.1, 0.03, id="default_maintenance_does_not_accept_other_rows"),
        pytest.param(
            "MACRS7", "depreciation", "MACRS5", "MACRS7", id="default_depreciation_does_not_accept_other_rows"
        ),
        pytest.param(
            "MACRS20",
            "steam_power_depreciation",
            "MACRS7",
            "MACRS20",
            id="default_steam_power_does_not_accept_other_rows",
        ),
    ],
)
def test_named_default_slice_does_not_accept_other_rows(monkeypatch, missing, field, planted, requested):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(**{field: planted}))
        payload = _data(
            tea.rank_landscape(**_sequence_kwargs(handle=handle, process_config={**_CAP_20KT, field: requested}))
        )
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[field] for row in keys} == {missing}


@pytest.mark.parametrize(
    "field, value",
    [
        pytest.param("feedstock_distance_km", 0.0, id="zero_completes_on_matching_zero"),
        pytest.param("dissolution_capacity", 3.0, id="default_completes_on_matching_default"),
        pytest.param("labor_cost_usd_per_employee_yr", 120000.0, id="default_labor_completes_on_matching_default"),
        pytest.param("sell_leftover_plastic", False, id="false_completes_on_matching_false"),
        pytest.param("burn_leftover_plastic", False, id="burn_false_completes_on_matching_false"),
        pytest.param("precipitation_temperature_format", "constant", id="constant_completes_on_matching_constant"),
        pytest.param("irr", 0.1, id="default_irr_completes_on_matching_default"),
        pytest.param("income_tax", 0.21, id="default_income_tax_completes_on_matching_default"),
        pytest.param("operating_days", 350.4, id="default_operating_days_completes_on_matching_default"),
        pytest.param("labor_burden", 0.9, id="default_labor_burden_completes_on_matching_default"),
        pytest.param("finance_interest", 0.08, id="default_finance_interest_completes_on_matching_default"),
        pytest.param("finance_years", 10, id="default_finance_years_completes_on_matching_default"),
        pytest.param("finance_fraction", 0.0, id="default_finance_fraction_completes_on_matching_default"),
        pytest.param("startup_months", 3, id="default_startup_months_completes_on_matching_default"),
        pytest.param("startup_FOCfrac", 1.0, id="default_startup_FOCfrac_completes_on_matching_default"),
        pytest.param("startup_VOCfrac", 0.75, id="default_startup_VOCfrac_completes_on_matching_default"),
        pytest.param("startup_salesfrac", 0.5, id="default_startup_salesfrac_completes_on_matching_default"),
        pytest.param("WC_over_FCI", 0.05, id="default_WC_over_FCI_completes_on_matching_default"),
        pytest.param("warehouse", 0.04, id="default_warehouse_completes_on_matching_default"),
        pytest.param("site_development", 0.09, id="default_site_development_completes_on_matching_default"),
        pytest.param("additional_piping", 0.045, id="default_additional_piping_completes_on_matching_default"),
        pytest.param("proratable_costs", 0.1, id="default_proratable_costs_completes_on_matching_default"),
        pytest.param("field_expenses", 0.1, id="default_field_expenses_completes_on_matching_default"),
        pytest.param("construction", 0.2, id="default_construction_completes_on_matching_default"),
        pytest.param("contingency", 0.4, id="default_contingency_completes_on_matching_default"),
        pytest.param("other_indirect_costs", 0.1, id="default_other_indirect_costs_completes_on_matching_default"),
        pytest.param("property_insurance", 0.007, id="default_property_insurance_completes_on_matching_default"),
        pytest.param("maintenance", 0.03, id="default_maintenance_completes_on_matching_default"),
        pytest.param("depreciation", "MACRS7", id="default_depreciation_completes_on_matching_default"),
        pytest.param("steam_power_depreciation", "MACRS20", id="default_steam_power_completes_on_matching_default"),
    ],
)
def test_named_default_slice_completes_on_matching_rows(monkeypatch, field, value):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(**{field: value}))
        payload = _data(
            tea.rank_landscape(**_sequence_kwargs(handle=handle, process_config={**_CAP_20KT, field: value}))
        )
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_plant_capacity_is_not_dissolution_capacity(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("processing_capacity_mt_per_yr") is not None
        assert row.get("dissolution_capacity") is None


@pytest.mark.parametrize(
    ('value', 'value_2'),
    [
        pytest.param('sell_leftover_plastic', 'sell_leftover_plastic', id='sell'),
        pytest.param('burn_leftover_plastic', 'burn_leftover_plastic', id='burn'),
    ],
)
def test_omitted_leftover_is_not_a_silent_false(monkeypatch, value, value_2):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value) is None
        assert row.get(value_2) is not False


@pytest.mark.parametrize(
    ('value', 'value_2', 'key'),
    [
        pytest.param('sell_leftover_plastic', 'burn_leftover_plastic', 'sell_leftover_plastic', id='sell_leftover_does_not_stamp_burn'),
        pytest.param('sell_leftover_plastic', 'burn_leftover_plastic', 'sell_leftover_plastic', id='sell_leftover_does_not_stamp_burn_on_this_slice'),
        pytest.param('burn_leftover_plastic', 'precipitation_temperature_format', 'burn_leftover_plastic', id='burn_leftover_does_not_stamp_precipitation_format'),
        pytest.param('burn_leftover_plastic', 'precipitation_temperature_format', 'burn_leftover_plastic', id='burn_leftover_does_not_stamp_format_on_this_slice'),
    ],
)
def test_sell_leftover_does_not_stamp_burn_cases(monkeypatch, value, value_2, key):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, key: True},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value) is True
        assert row.get(value_2) is None


@pytest.mark.parametrize(
    "field", [pytest.param("sell_leftover_plastic", id="sell"), pytest.param("burn_leftover_plastic", id="burn")]
)
def test_mixed_leftover_handle_leaves_the_unmatched_remnant(monkeypatch, field):
    _forbid_live_rank_solvent_maps(monkeypatch)
    rows = _complete_d18_rows(**{field: True})
    rows[-1][field] = False
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(
            tea.rank_landscape(**_sequence_kwargs(handle=handle, process_config={**_CAP_20KT, field: True}))
        )
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0][field] is True
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'key', 'value_4'),
    [
        pytest.param('leftover_disposition_conflict', True, 'sell_leftover_plastic', 'sell_leftover_plastic', True, id='sell_and_burn_true_does_not_fire_disposition_conflict'),
        pytest.param('burn_requires_facilities', 'C2', 'energy_case', 'energy_case', 'C2', id='burn_true_on_c2_does_not_fire_burn_requires_facilities'),
    ],
)
def test_named(monkeypatch, value, value_2, value_3, key, value_4):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                key: value_4,
                "burn_leftover_plastic": True,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != value
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[value_3] for row in keys} == {value_2}
    assert {row["burn_leftover_plastic"] for row in keys} == {True}


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'value_4', 'key', 'value_5'),
    [
        pytest.param('drop', 'precipitation_temperature_format', 'precipitation_configuration', 'precipitation_temperature_drop_pct', 'precipitation_temperature_format', 'drop', id='precipitation_format_does_not_stamp_configuration'),
        pytest.param(0.25, 'income_tax', 'operating_days', 'lang_factor', 'income_tax', 0.25, id='income_tax_does_not_stamp_operating_days'),
        pytest.param(365.0, 'operating_days', 'labor_burden', 'lang_factor', 'operating_days', 365, id='operating_days_does_not_stamp_labor_burden'),
        pytest.param(1.5, 'labor_burden', 'finance_interest', 'lang_factor', 'labor_burden', 1.5, id='labor_burden_does_not_stamp_finance_interest'),
        pytest.param(15.0, 'finance_years', 'finance_fraction', 'lang_factor', 'finance_years', 15, id='finance_years_does_not_stamp_finance_fraction'),
        pytest.param(0.1, 'maintenance', 'lang_factor', 'property_insurance', 'maintenance', 0.1, id='maintenance_does_not_stamp_lang_factor'),
    ],
)
def test_precipitation_format_does_not_stamp_configuration_cases(monkeypatch, value, value_2, value_3, value_4, key, value_5):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                key: value_5,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value_2) == value
        assert row.get(value_3) is None
        assert row.get(value_4) is None


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'key', 'value_4'),
    [
        pytest.param('field_not_on_this_instance', 'drop', 'precipitation_temperature_format', 'precipitation_temperature_format', 'drop', id='drop_does_not_fire_field_not_on_this_instance'),
        pytest.param('invalid_admitted_record_query', 1.5, 'labor_burden', 'labor_burden', 1.5, id='above_one_labor_burden_is_a_listing'),
    ],
)
def test_named_2(monkeypatch, value, value_2, value_3, key, value_4):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                key: value_4,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != value
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row[value_3] for row in keys} == {value_2}


@pytest.mark.parametrize(
    ('value', 'key', 'value_2'),
    [
        pytest.param('field_not_on_this_instance', 'precipitation_temperature_format', 'linear', id='invalid_precipitation_format_is_not_a'),
        pytest.param('invalid_scenario', 'precipitation_configuration', 'flash', id='invalid_precipitation_configuration_is_not_a'),
        pytest.param('invalid_scenario', 'irr', 10, id='percent_integer_irr_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'income_tax', 21, id='percent_integer_income_tax_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'operating_days', 0, id='zero_operating_days_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'finance_interest', 8, id='percent_integer_finance_interest_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'finance_years', 0, id='zero_finance_years_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'finance_fraction', 40, id='percent_integer_finance_fraction_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'startup_FOCfrac', 50, id='percent_integer_startup_FOCfrac_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'startup_VOCfrac', 75, id='percent_integer_startup_VOCfrac_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'startup_salesfrac', 50, id='percent_integer_startup_salesfrac_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'WC_over_FCI', 5, id='percent_integer_WC_over_FCI_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'warehouse', 4, id='percent_integer_warehouse_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'site_development', 9, id='percent_integer_site_development_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'additional_piping', 5, id='percent_integer_additional_piping_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'proratable_costs', 10, id='percent_integer_proratable_costs_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'field_expenses', 10, id='percent_integer_field_expenses_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'construction', 20, id='percent_integer_construction_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'contingency', 40, id='percent_integer_contingency_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'other_indirect_costs', 10, id='percent_integer_other_indirect_costs_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'property_insurance', 7, id='percent_integer_property_insurance_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'maintenance', 3, id='percent_integer_maintenance_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'depreciation', 7, id='integer_depreciation_is_not_a_remnant'),
        pytest.param('invalid_scenario', 'duration', 30, id='years_scalar_duration_is_not_a'),
        pytest.param('invalid_scenario', 'construction_schedule', 3, id='scalar_construction_schedule_is_not_a'),
        pytest.param('invalid_scenario', 'steam_power_depreciation', 'macrs20', id='lowercase_steam_power_is_not_a'),
    ],
)
def test_listing_2(monkeypatch, value, key, value_2):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                key: value_2,
            },
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != value


_IHT = "integrated heat transfer"


_MIX = "solvent mixing"


def test_omitted_precipitation_configuration_is_not_a_silent_integrated(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") is None
        assert row.get("precipitation_configuration") != _IHT


@pytest.mark.parametrize("configuration", [pytest.param(_IHT, id="integrated"), pytest.param(_MIX, id="mixing")])
def test_config_rows_still_complete_when_omitted(monkeypatch, configuration):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(precipitation_configuration=configuration))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_mixing_does_not_accept_integrated_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_configuration=_IHT),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_configuration"] for row in keys} == {_MIX}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_integrated_does_not_accept_mixing_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_configuration=_MIX),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_configuration": _IHT},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_configuration"] for row in keys} == {_IHT}


def test_named_mixing_completes_on_matching_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_configuration=_MIX),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_integrated_is_holdable(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": _IHT},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_configuration"] for row in keys} == {_IHT}


def test_named_integrated_completes_on_matching_integrated_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_configuration=_IHT),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_configuration": _IHT},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_precipitation_configuration_does_not_stamp_irr(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") == _MIX
        assert row.get("irr") is None


def test_mixed_configuration_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    rows = _complete_d18_rows(precipitation_configuration=_MIX)
    rows[-1]["precipitation_configuration"] = _IHT
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["precipitation_configuration"] == _MIX
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_configuration_do_not_fill_named_mixing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_configuration"] for row in keys} == {_MIX}


def test_precipitation_configuration_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(precipitation_configuration=_MIX),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["precipitation_configuration"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_precipitation_configuration_does_not_count(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"precipitation_configuration": _MIX},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") is None


def test_precipitation_configuration_does_not_stamp_irr_on_this_slice(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") == _MIX
        assert row.get("irr") is None


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        pytest.param('income_tax', 0.01, id='income_tax'),
        pytest.param('operating_days', 1, id='operating_days'),
        pytest.param('finance_interest', 0.01, id='finance_interest'),
        pytest.param('finance_fraction', 0.1, id='finance_fraction'),
        pytest.param('startup_FOCfrac', 0.1, id='startup_FOCfrac'),
        pytest.param('startup_VOCfrac', 0.1, id='startup_VOCfrac'),
        pytest.param('startup_salesfrac', 0.1, id='startup_salesfrac'),
        pytest.param('WC_over_FCI', 0.1, id='WC_over_FCI'),
        pytest.param('warehouse', 0.1, id='warehouse'),
        pytest.param('site_development', 0.1, id='site_development'),
        pytest.param('additional_piping', 0.1, id='additional_piping'),
        pytest.param('proratable_costs', 0.1, id='proratable_costs'),
        pytest.param('field_expenses', 0.1, id='field_expenses'),
        pytest.param('construction', 0.1, id='construction'),
        pytest.param('contingency', 0.1, id='contingency'),
        pytest.param('other_indirect_costs', 0.1, id='other_indirect_costs'),
        pytest.param('property_insurance', 0.1, id='property_insurance'),
        pytest.param('maintenance', 0.1, id='maintenance'),
    ],
)
def test_negative_is_not_a_remnant_listing(monkeypatch, key, value):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, key: -value}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        pytest.param('labor_burden', 0.1, id='labor_burden'),
        pytest.param('startup_months', 1, id='startup_months'),
    ],
)
def test_negative_is_not_a_remnant_listing_2(monkeypatch, key, value):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, key: -value}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'value_4', 'value_5', 'key', 'value_6'),
    [
        pytest.param(0.12, 'finance_interest', 'finance_years', 'irr', 'lang_factor', 'finance_interest', 0.12, id='finance_interest_does_not_stamp_finance_years_or_irr'),
        pytest.param(0.4, 'finance_fraction', 'startup_months', 'lang_factor', 'finance_years', 'finance_fraction', 0.4, id='finance_fraction_does_not_stamp_startup_months'),
        pytest.param(6.0, 'startup_months', 'startup_FOCfrac', 'lang_factor', 'finance_fraction', 'startup_months', 6, id='startup_months_does_not_stamp_startup_FOCfrac'),
        pytest.param(0.5, 'startup_FOCfrac', 'startup_VOCfrac', 'lang_factor', 'startup_months', 'startup_FOCfrac', 0.5, id='startup_FOCfrac_does_not_stamp_startup_VOCfrac'),
        pytest.param(0.5, 'startup_VOCfrac', 'startup_salesfrac', 'lang_factor', 'startup_FOCfrac', 'startup_VOCfrac', 0.5, id='startup_VOCfrac_does_not_stamp_startup_salesfrac'),
        pytest.param(0.25, 'startup_salesfrac', 'WC_over_FCI', 'lang_factor', 'startup_VOCfrac', 'startup_salesfrac', 0.25, id='startup_salesfrac_does_not_stamp_WC_over_FCI'),
        pytest.param(0.1, 'WC_over_FCI', 'warehouse', 'lang_factor', 'startup_salesfrac', 'WC_over_FCI', 0.1, id='WC_over_FCI_does_not_stamp_warehouse'),
        pytest.param(0.1, 'warehouse', 'site_development', 'lang_factor', 'WC_over_FCI', 'warehouse', 0.1, id='warehouse_does_not_stamp_site_development'),
        pytest.param(0.1, 'site_development', 'additional_piping', 'lang_factor', 'warehouse', 'site_development', 0.1, id='site_development_does_not_stamp_additional_piping'),
        pytest.param(0.1, 'additional_piping', 'proratable_costs', 'lang_factor', 'site_development', 'additional_piping', 0.1, id='additional_piping_does_not_stamp_proratable_costs'),
        pytest.param(0.2, 'proratable_costs', 'field_expenses', 'lang_factor', 'additional_piping', 'proratable_costs', 0.2, id='proratable_costs_does_not_stamp_field_expenses'),
        pytest.param(0.2, 'field_expenses', 'construction', 'lang_factor', 'proratable_costs', 'field_expenses', 0.2, id='field_expenses_does_not_stamp_construction'),
        pytest.param(0.1, 'construction', 'contingency', 'lang_factor', 'field_expenses', 'construction', 0.1, id='construction_does_not_stamp_contingency'),
        pytest.param(0.1, 'contingency', 'other_indirect_costs', 'lang_factor', 'construction', 'contingency', 0.1, id='contingency_does_not_stamp_other_indirect_costs'),
        pytest.param(0.2, 'other_indirect_costs', 'property_insurance', 'lang_factor', 'contingency', 'other_indirect_costs', 0.2, id='other_indirect_costs_does_not_stamp_property_insurance'),
        pytest.param(0.1, 'property_insurance', 'maintenance', 'lang_factor', 'other_indirect_costs', 'property_insurance', 0.1, id='property_insurance_does_not_stamp_maintenance'),
    ],
)
def test_finance_interest_does_not_stamp_finance_years_or_irr_cases(monkeypatch, value, value_2, value_3, value_4, value_5, key, value_6):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, key: value_6},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value_2) == value
        assert row.get(value_3) is None
        assert row.get(value_4) is None
        assert row.get(value_5) is None


# Fields a named TEA slice must match, in the order the cases below add them: the value planted in the
# rows when the field is the wrong one, the value planted once it matches, and the value requested.
_NAMED_SLICE_FIELDS = (
    ("solvent_price_usd_per_kg", 1.5, 2.0, 2.0),
    ("solvent_loss_pct", 0.5, 3.0, 3.0),
    ("feedstock_distance_km", 100.0, 250.0, 250.0),
    ("dissolution_capacity", 3.0, 5.0, 5.0),
    ("labor_cost_usd_per_employee_yr", 120000.0, 150000.0, 150000.0),
    ("sell_leftover_plastic", False, True, True),
    ("burn_leftover_plastic", False, True, True),
    ("precipitation_temperature_format", "constant", "drop", "drop"),
    ("precipitation_configuration", _IHT, _MIX, _MIX),
    ("irr", 0.1, 0.12, 0.12),
    ("income_tax", 0.21, 0.25, 0.25),
    ("operating_days", 350.4, 365.0, 365),
    ("labor_burden", 0.9, 1.5, 1.5),
    ("finance_interest", 0.08, 0.12, 0.12),
    ("finance_years", 10, 15, 15),
    ("finance_fraction", 0.0, 0.4, 0.4),
    ("startup_months", 3, 6, 6),
    ("startup_FOCfrac", 1.0, 0.5, 0.5),
    ("startup_VOCfrac", 0.75, 0.5, 0.5),
    ("startup_salesfrac", 0.5, 0.25, 0.25),
    ("WC_over_FCI", 0.05, 0.1, 0.1),
    ("warehouse", 0.04, 0.1, 0.1),
    ("site_development", 0.09, 0.1, 0.1),
    ("additional_piping", 0.045, 0.1, 0.1),
    ("proratable_costs", 0.1, 0.2, 0.2),
    ("field_expenses", 0.1, 0.2, 0.2),
    ("construction", 0.2, 0.1, 0.1),
    ("contingency", 0.4, 0.1, 0.1),
    ("other_indirect_costs", 0.1, 0.2, 0.2),
    ("property_insurance", 0.007, 0.1, 0.1),
    ("maintenance", 0.03, 0.1, 0.1),
)


@pytest.mark.parametrize(
    "wrong, checks",
    [
        pytest.param(
            0,
            {
                "energy_case": "C1",
                "dissolution_temperature_c": 90.0,
                "precipitation_temperature_c": 40.0,
                "solvent_price_usd_per_kg": 2.0,
            },
            id="price",
        ),
        pytest.param(
            1,
            {
                "energy_case": "C1",
                "dissolution_temperature_c": 90.0,
                "precipitation_temperature_c": 40.0,
                "solvent_price_usd_per_kg": 2.0,
                "solvent_loss_pct": 3.0,
            },
            id="loss",
        ),
        pytest.param(
            2,
            {
                "energy_case": "C1",
                "dissolution_temperature_c": 90.0,
                "precipitation_temperature_c": 40.0,
                "solvent_price_usd_per_kg": 2.0,
                "solvent_loss_pct": 3.0,
                "feedstock_distance_km": 250.0,
            },
            id="distance",
        ),
        pytest.param(
            3,
            {
                "energy_case": "C1",
                "dissolution_temperature_c": 90.0,
                "precipitation_temperature_c": 40.0,
                "solvent_price_usd_per_kg": 2.0,
                "solvent_loss_pct": 3.0,
                "feedstock_distance_km": 250.0,
                "dissolution_capacity": 5.0,
            },
            id="dissolution_capacity",
        ),
        pytest.param(
            4,
            {
                "energy_case": "C1",
                "dissolution_temperature_c": 90.0,
                "precipitation_temperature_c": 40.0,
                "solvent_price_usd_per_kg": 2.0,
                "solvent_loss_pct": 3.0,
                "feedstock_distance_km": 250.0,
                "dissolution_capacity": 5.0,
                "labor_cost_usd_per_employee_yr": 150000.0,
            },
            id="labor",
        ),
        pytest.param(
            5,
            {"energy_case": "C1", "labor_cost_usd_per_employee_yr": 150000.0, "sell_leftover_plastic": True},
            id="sell",
        ),
        pytest.param(6, {"sell_leftover_plastic": True, "burn_leftover_plastic": True}, id="burn"),
        pytest.param(7, {"burn_leftover_plastic": True, "precipitation_temperature_format": "drop"}, id="format"),
        pytest.param(
            8, {"precipitation_temperature_format": "drop", "precipitation_configuration": _MIX}, id="configuration"
        ),
        pytest.param(9, {"precipitation_configuration": _MIX, "irr": 0.12}, id="irr"),
        pytest.param(10, {"irr": 0.12, "income_tax": 0.25}, id="income_tax"),
        pytest.param(11, {"income_tax": 0.25, "operating_days": 365.0}, id="operating_days"),
        pytest.param(12, {"operating_days": 365.0, "labor_burden": 1.5}, id="labor_burden"),
        pytest.param(13, {"labor_burden": 1.5, "finance_interest": 0.12}, id="finance_interest"),
        pytest.param(14, {"finance_interest": 0.12, "finance_years": 15.0}, id="finance_years"),
        pytest.param(15, {"finance_years": 15.0, "finance_fraction": 0.4}, id="finance_fraction"),
        pytest.param(16, {"finance_fraction": 0.4, "startup_months": 6.0}, id="startup_months"),
        pytest.param(17, {"startup_months": 6.0, "startup_FOCfrac": 0.5}, id="startup_FOCfrac"),
        pytest.param(18, {"startup_FOCfrac": 0.5, "startup_VOCfrac": 0.5}, id="startup_VOCfrac"),
        pytest.param(19, {"startup_VOCfrac": 0.5, "startup_salesfrac": 0.25}, id="startup_salesfrac"),
        pytest.param(20, {"startup_salesfrac": 0.25, "WC_over_FCI": 0.1}, id="WC_over_FCI"),
        pytest.param(21, {"WC_over_FCI": 0.1, "warehouse": 0.1}, id="warehouse"),
        pytest.param(22, {"warehouse": 0.1, "site_development": 0.1}, id="site_development"),
        pytest.param(23, {"site_development": 0.1, "additional_piping": 0.1}, id="additional_piping"),
        pytest.param(24, {"additional_piping": 0.1, "proratable_costs": 0.2}, id="proratable_costs"),
        pytest.param(25, {"proratable_costs": 0.2, "field_expenses": 0.2}, id="field_expenses"),
        pytest.param(26, {"field_expenses": 0.2, "construction": 0.1}, id="construction"),
        pytest.param(27, {"construction": 0.1, "contingency": 0.1}, id="contingency"),
        pytest.param(28, {"contingency": 0.1, "other_indirect_costs": 0.2}, id="other_indirect_costs"),
        pytest.param(29, {"other_indirect_costs": 0.2, "property_insurance": 0.1}, id="property_insurance"),
        pytest.param(30, {"property_insurance": 0.1, "maintenance": 0.1}, id="maintenance"),
    ],
)
def test_named_slice_at_wrong_field_does_not_fill(monkeypatch, wrong, checks):
    field, stored, _, requested = _NAMED_SLICE_FIELDS[wrong]
    earlier = _NAMED_SLICE_FIELDS[:wrong]
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                **{name: value for name, _, value, _ in earlier},
                **{field: stored},
            ),
        )
        payload = _data(
            tea.rank_landscape(
                **_sequence_kwargs(
                    handle=handle,
                    process_config={
                        **_CAP_20KT,
                        "energy_case": "C1",
                        "dissolution_temperature_c": 90.0,
                        "precipitation_temperature_c": 40.0,
                        **{name: value for name, _, _, value in earlier},
                        field: requested,
                    },
                )
            )
        )
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    for key, expected in checks.items():
        assert {row[key] for row in keys} == {expected}


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'value_4', 'value_5', 'key', 'value_6'),
    [
        pytest.param('MACRS5', 'depreciation', 'steam_power_depreciation', 'construction_schedule', 'maintenance', 'depreciation', 'MACRS5', id='depreciation'),
        pytest.param('MACRS7', 'steam_power_depreciation', 'depreciation', 'duration', 'construction_schedule', 'steam_power_depreciation', 'MACRS7', id='steam_power'),
    ],
)
def test_does_not_stamp_unbound_g_fields(monkeypatch, value, value_2, value_3, value_4, value_5, key, value_6):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, key: value_6},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value_2) == value
        assert row.get("lang_factor") is None
        assert row.get(value_3) is None
        assert row.get(value_4) is None
        assert row.get(value_5) is None


def test_array_depreciation_is_not_a_remnant_listing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": ["MACRS7"]},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_omitted_duration_is_not_a_silent_default(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("duration") is None
        assert row.get("duration") != (2025, 2055)
        assert row.get("duration") != [2025, 2055]


def test_default_duration_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(duration=(2025, 2055)),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_duration_does_not_accept_default_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(duration=(2025, 2055)),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "duration": [2025, 2045]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["duration"]) for row in keys} == {(2025, 2045)}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_duration_does_not_accept_other_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(duration=(2025, 2045)),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "duration": [2025, 2055]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["duration"]) for row in keys} == {(2025, 2055)}


@pytest.mark.parametrize(
    "field, first, second",
    [
        pytest.param("duration", 2025, 2045, id="duration"),
        pytest.param("construction_schedule", 0.5, 0.5, id="construction_schedule"),
    ],
)
def test_named_pair_completes_on_matching_rows(monkeypatch, field, first, second):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(**{field: [first, second]}))
        payload = _data(
            tea.rank_landscape(**_sequence_kwargs(handle=handle, process_config={**_CAP_20KT, field: (first, second)}))
        )
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_duration_is_holdable(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "duration": [2025, 2055]},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["duration"]) for row in keys} == {(2025, 2055)}


def test_named_default_duration_completes_on_matching_default_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(duration=(2025, 2055)),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "duration": [2025, 2055]},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'value_4', 'key', 'value_5', 'value_6'),
    [
        pytest.param(2025, 2045, 'construction_schedule', 'duration', 'duration', 2025, 2045, id='duration'),
        pytest.param(0.5, 0.5, 'duration', 'construction_schedule', 'construction_schedule', 0.5, 0.5, id='construction_schedule'),
    ],
)
def test_does_not_stamp_unbound_g_fields_2(monkeypatch, value, value_2, value_3, value_4, key, value_5, value_6):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, key: [value_5, value_6]},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert tuple(row[value_4]) == (value, value_2)
        assert row.get("lang_factor") is None
        assert row.get("steam_power_depreciation") is None
        assert row.get(value_3) is None
        assert row.get("depreciation") is None


def test_mixed_duration_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    rows = _complete_d18_rows(duration=(2025, 2045))
    rows[-1]["duration"] = (2025, 2055)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "duration": [2025, 2045]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert tuple(keys[0]["duration"]) == (2025, 2045)
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'key', 'value_4', 'value_5'),
    [
        pytest.param(2025, 2045, 'duration', 'duration', 2025, 2045, id='duration_do_not_fill_named_pair'),
        pytest.param(0.5, 0.5, 'construction_schedule', 'construction_schedule', 0.5, 0.5, id='construction_schedule_do_not_fill_named'),
    ],
)
def test_rows_without_2(monkeypatch, value, value_2, value_3, key, value_4, value_5):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, key: [value_4, value_5]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row[value_3]) for row in keys} == {(value, value_2)}


def test_duration_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(duration=[2025, 2045]),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["duration"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


@pytest.mark.parametrize(
    ('value', 'key', 'value_2', 'value_3'),
    [
        pytest.param('duration', 'duration', 2025, 2045, id='duration'),
        pytest.param('construction_schedule', 'construction_schedule', 0.5, 0.5, id='construction_schedule'),
    ],
)
def test_nested_does_not_count_2(monkeypatch, value, key, value_2, value_3):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {key: [value_2, value_3]}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get(value) is None


def test_inverted_duration_is_not_a_listing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "duration": [2055, 2025]},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_omitted_construction_schedule_is_not_a_silent_default(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction_schedule") is None
        assert row.get("construction_schedule") != (0.08, 0.60, 0.32)
        assert row.get("construction_schedule") != [0.08, 0.60, 0.32]


def test_default_construction_schedule_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction_schedule=(0.08, 0.60, 0.32)),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_construction_schedule_does_not_accept_default_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction_schedule=(0.08, 0.60, 0.32)),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction_schedule": [0.5, 0.5]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["construction_schedule"]) for row in keys} == {(0.5, 0.5)}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_construction_schedule_does_not_accept_other_rows(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction_schedule=(0.5, 0.5)),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "construction_schedule": [0.08, 0.60, 0.32],
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["construction_schedule"]) for row in keys} == {
        (0.08, 0.60, 0.32),
    }


def test_named_default_construction_schedule_is_holdable(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "construction_schedule": [0.08, 0.60, 0.32],
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["construction_schedule"]) for row in keys} == {
        (0.08, 0.60, 0.32),
    }


def test_named_default_construction_schedule_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live_rank_solvent_maps(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction_schedule=(0.08, 0.60, 0.32)),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "construction_schedule": [0.08, 0.60, 0.32],
                },
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_mixed_construction_schedule_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live_rank_solvent_maps(monkeypatch)
    rows = _complete_d18_rows(construction_schedule=(0.5, 0.5))
    rows[-1]["construction_schedule"] = (0.08, 0.60, 0.32)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction_schedule": [0.5, 0.5]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert tuple(keys[0]["construction_schedule"]) == (0.5, 0.5)
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_construction_schedule_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(construction_schedule=[0.5, 0.5]),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["construction_schedule"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_c2_named_steam_power_is_energy_case_contract(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "energy_case": "C2",
                "steam_power_depreciation": "MACRS20",
            },
        ),
    ))
    assert payload.get("error_code") == "energy_case_contract"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_named_lang_factor_is_not_a_listing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "lang_factor": 3.0},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("field") == "lang_factor"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"
    assert "missing_keys" not in payload


def test_zero_lang_factor_is_not_a_listing(monkeypatch):
    _forbid_live_rank_solvent_maps(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "lang_factor": 0},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("field") == "lang_factor"


# --- from test_screen_to_economics_order.py: Closed screen_to_economics_order. Default independent. No router.
_LEGAL = (
    "independent",
    "thermo_then_economics",
    "safety_then_economics",
)


def _forbid_live_screen_to_economics_order(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("screen_to_economics_order must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _forbid_safety(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("screen_to_economics_order must not call a safety tool")

    monkeypatch.setattr(safety, "get_solvent_safety_card", boom)
    monkeypatch.setattr(safety, "compare_solvent_safety_at_conditions", boom)


def _write_registry_screen_to_economics_order(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "campaign_registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _success_row_screen_to_economics_order(canonical, pair_id, polymer, solvent, msp, gwp):
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


def _two_row_mini(tmp_path):
    definition = _minimal_definition(pair_definitions=[
        {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
        {"config_sent": {"target_plastic": "LDPE", "solvent": "xylene"}},
    ])
    canonical = tea_ranking.canonical_json_digest(definition)
    toluene = _success_row_screen_to_economics_order(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4)
    toluene["safety_standing"] = {
        "status": "evaluated",
        "safety_profile": {"ghs_signal_word": "Danger"},
    }
    xylene = _success_row_screen_to_economics_order(canonical, "p2", "LDPE", "Xylene", 2.0, 0.8)
    return _mini(tmp_path, [toluene, xylene], definition=definition)


def test_schema_exposes_the_closed_choice():
    schemas = {item["name"]: item for item in tool_schemas()}
    expected = [
        "independent",
        "thermo_then_economics",
        "safety_then_economics",
    ]
    for name in ("evaluate_process", "rank_landscape"):
        props = schemas[name]["parameters"]["properties"]
        required = schemas[name]["parameters"].get("required") or []
        assert "screen_to_economics_order" in props
        assert "screen_to_economics_order" not in required
        assert props["screen_to_economics_order"]["type"] == "string"
        assert props["screen_to_economics_order"]["enum"] == expected
        assert "default" not in props["screen_to_economics_order"]
        assert "pipeline" not in props["screen_to_economics_order"]["enum"]
        assert "exclude_safety_fail" not in props
    old = schemas["evaluate_process"]["parameters"]["properties"]
    assert "screen_to_economics_order" in old
    assert "evaluate_tea_lca_scenarios" not in schemas
    assert "lookup_admitted_process_records" not in schemas
    assert "analyze_tea_sensitivity" not in schemas
    assert "evaluate_stored_route_tea_lca" not in schemas
    assert "optimize_stored_route" not in schemas
    assert "pareto_optimize_stored_route" not in schemas
    engine_params = inspect.signature(tea.evaluate_tea_lca_scenarios).parameters
    assert "screen_to_economics_order" not in engine_params
    assert "screen_to_economics_order" not in inspect.signature(
        tea.analyze_tea_sensitivity,
    ).parameters


def test_omitted_and_explicit_independent_match(monkeypatch, tmp_path):
    _forbid_live_screen_to_economics_order(monkeypatch)
    _forbid_safety(monkeypatch)
    canonical, entry = _two_row_mini(tmp_path)
    registry = _write_registry_screen_to_economics_order(tmp_path, {canonical: entry})
    omitted = _rank(monkeypatch, registry, campaign_fingerprint=canonical)
    explicit = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=canonical,
        screen_to_economics_order="independent",
    )
    blank = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=canonical,
        screen_to_economics_order="",
    )
    folded = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=canonical,
        screen_to_economics_order="Independent",
    )
    assert omitted.get("success") is True
    assert omitted["n_usable"] == 2
    for payload in (omitted, explicit, blank, folded):
        assert payload["screen_to_economics_order"] == "independent"
        assert payload["n_usable"] == omitted["n_usable"]
        assert payload["safety_standing_policy"] == "carried_not_filtered"
        for point in payload["landscape_points"]:
            assert "screen_to_economics_order" not in point


def test_thermo_and_safety_orders_echo_without_filtering(monkeypatch, tmp_path):
    _forbid_live_screen_to_economics_order(monkeypatch)
    _forbid_safety(monkeypatch)
    canonical, entry = _two_row_mini(tmp_path)
    registry = _write_registry_screen_to_economics_order(tmp_path, {canonical: entry})
    independent = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=canonical,
        screen_to_economics_order="independent",
    )
    thermo = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=canonical,
        screen_to_economics_order="thermo_then_economics",
    )
    safety_first = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=canonical,
        screen_to_economics_order="safety_then_economics",
    )
    assert independent["screen_to_economics_order"] == "independent"
    assert thermo["screen_to_economics_order"] == "thermo_then_economics"
    assert safety_first["screen_to_economics_order"] == "safety_then_economics"
    assert thermo["n_usable"] == independent["n_usable"] == 2
    assert safety_first["n_usable"] == independent["n_usable"]
    assert thermo["screen_to_economics_order"] != independent["screen_to_economics_order"]
    by_id = {point["pair_id"]: point for point in safety_first["landscape_points"]}
    assert by_id["p1"]["safety_standing"]["status"] == "evaluated"
    assert by_id["p2"]["safety_standing"] == {"status": "not_requested"}
    assert safety_first["safety_standing_policy"] == "carried_not_filtered"


def test_sealed_campaign_omitted_order_is_independent(monkeypatch, tmp_path):
    _forbid_live_screen_to_economics_order(monkeypatch)
    _forbid_safety(monkeypatch)
    registry = _write_registry_screen_to_economics_order(tmp_path, {_CANONICAL: _sealed_entry()})
    omitted = _rank(monkeypatch, registry, campaign_fingerprint=_CANONICAL)
    safety_first = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        screen_to_economics_order="safety_then_economics",
    )
    assert omitted.get("success") is True
    assert omitted["screen_to_economics_order"] == "independent"
    assert omitted["n_usable"] == 408
    assert omitted["n_groups"] == 10
    assert safety_first["screen_to_economics_order"] == "safety_then_economics"
    assert safety_first["n_usable"] == omitted["n_usable"]
    assert safety_first["n_groups"] == omitted["n_groups"]


def test_evaluate_process_stamps_the_payload_not_the_rows(monkeypatch):
    _forbid_live_screen_to_economics_order(monkeypatch)
    _forbid_safety(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    omitted = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="auto",
    ))
    thermo = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="auto",
        screen_to_economics_order="thermo_then_economics",
    ))
    lookup = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "energy_cases": ["C1"],
        },
        screen_to_economics_order="safety_then_economics",
    ))
    sensitivity = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=_public_from_record(c1),
        parameter="solvent_price",
        engine_mode="auto",
        screen_to_economics_order="independent",
    ))
    assert omitted.get("success") is True
    assert omitted["screen_to_economics_order"] == "independent"
    assert thermo["screen_to_economics_order"] == "thermo_then_economics"
    assert omitted["screen_to_economics_order"] != thermo["screen_to_economics_order"]
    assert "screen_to_economics_order" not in omitted["comparison_rows"][0]
    assert lookup.get("success") is True
    assert lookup["screen_to_economics_order"] == "safety_then_economics"
    assert "screen_to_economics_order" not in lookup["comparison_rows"][0]
    assert sensitivity.get("success") is True
    assert sensitivity["screen_to_economics_order"] == "independent"
    assert "screen_to_economics_order" not in sensitivity["sensitivity_rows"][0]


def test_invalid_order_is_named(monkeypatch, tmp_path):
    _forbid_live_screen_to_economics_order(monkeypatch)
    registry = _write_registry_screen_to_economics_order(tmp_path, {_CANONICAL: _sealed_entry()})
    ranked = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        screen_to_economics_order="pipeline",
    )
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(_record_by_label("ldpe-route-c1")),
        engine_mode="auto",
        screen_to_economics_order="parallel",
    ))
    missing_mode = _data(tea.evaluate_process(
        screen_to_economics_order="pipeline",
    ))
    number = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        screen_to_economics_order=1,
    ))
    for payload, supplied in (
        (ranked, "pipeline"),
        (wrapped, "parallel"),
        (missing_mode, "pipeline"),
        (number, 1),
    ):
        assert payload.get("success") is False
        assert payload.get("error_code") == "invalid_admitted_record_query"
        assert payload.get("field") == "screen_to_economics_order"
        assert payload.get("legal_orders") == list(_LEGAL)
        assert payload.get("supplied") == supplied
        assert payload.get("error_code") != "missing_mode"
        assert "landscape_points" not in payload
        assert "comparison_rows" not in payload


def test_leftover_extra_still_wins_before_invalid_order(monkeypatch):
    _forbid_live_screen_to_economics_order(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        leftover_xyz=1,
        screen_to_economics_order="pipeline",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["leftover_xyz"]
    assert "screen_to_economics_order" not in payload
    old = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(_record_by_label("ldpe-route-c1"))],
        engine_mode="auto",
        screen_to_economics_order="independent",
    ))
    assert old.get("error_code") == "unknown_process_field"
    assert old.get("extra_keys") == ["screen_to_economics_order"]
    misspelled = _data(tea.rank_landscape(
        screen_to_economic_order="independent",
    ))
    assert misspelled.get("error_code") == "unknown_process_field"
    assert misspelled.get("extra_keys") == ["screen_to_economic_order"]


def test_exclude_safety_fail_stays_unknown_extra__screen_to_economics_order(monkeypatch, tmp_path):
    _forbid_live_screen_to_economics_order(monkeypatch)
    registry = _write_registry_screen_to_economics_order(tmp_path, {_CANONICAL: _sealed_entry()})
    payload = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        screen_to_economics_order="safety_then_economics",
        exclude_safety_fail=True,
    )
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["exclude_safety_fail"]
    assert "screen_to_economics_order" not in payload


def test_superstructure_missing_map_is_not_a_ranking(monkeypatch):
    _forbid_live_screen_to_economics_order(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        screen_to_economics_order="thermo_then_economics",
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("screen_to_economics_order") == "thermo_then_economics"
    assert "landscape_points" not in payload
    assert payload.get("error_code") != "sequence_coupling_unproven"


def test_dispatch_evaluate_process_binds_the_token(monkeypatch):
    _forbid_live_screen_to_economics_order(monkeypatch)
    _forbid_safety(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    omitted = dispatch(
        "evaluate_process",
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="auto",
    )
    thermo = dispatch(
        "evaluate_process",
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="auto",
        screen_to_economics_order="thermo_then_economics",
    )
    assert omitted.get("available") is True
    assert omitted["data"]["screen_to_economics_order"] == "independent"
    assert thermo.get("available") is True
    assert thermo["data"]["screen_to_economics_order"] == "thermo_then_economics"
    refused = dispatch(
        "evaluate_process",
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="auto",
        screen_to_economics_order="pipeline",
    )
    assert refused.get("available") is False
    assert refused.get("refusal") == "invalid_admitted_record_query"


def test_lang_factor_present_is_invalid_scenario(monkeypatch):
    _forbid_live_screen_to_economics_order(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1, lang_factor=3.0),
        engine_mode="auto",
        screen_to_economics_order="independent",
    ))
    assert payload.get("error_code") == "invalid_scenario"
    assert payload.get("field") == "lang_factor"
    assert payload.get("error_code") != "unknown_process_field"
    assert "lang_factor" in tea._COEFFICIENT_DEFAULTS


# --- from test_screening_handoff.py: Screening-to-economics handoff: shortlist plus held nine, not a cache fill.
def _data_screening_handoff(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _record_with_energy(energy_case: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record["config"].get("energy_case") or "").upper() == energy_case
    )


def _held_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    held = {
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    }
    held.update(overrides)
    return held


def _item_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    item = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
    }
    item.update(overrides)
    return item


def _shortlist(*items, source="explicit"):
    return {"source": source, "items": list(items)}


def _forbid_pair_fill(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("handoff must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_shortlist_without_held_basis_lists_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(_item_from_record(record)),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "screening_basis_incomplete"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert "comparison_rows" not in payload


def test_held_basis_missing_one_field_names_only_that_field(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    held = _held_from_record(record)
    held.pop("energy_case")
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(_item_from_record(record)),
        held_process_basis=held,
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "screening_basis_incomplete"
    assert payload.get("missing") == ["energy_case"]


def test_shortlist_item_missing_t_is_screening_shortlist_incomplete(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    item = _item_from_record(record)
    item.pop("dissolution_temperature_c")
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(item),
        held_process_basis=_held_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "screening_shortlist_incomplete"
    assert payload.get("item_index") == 0
    assert payload.get("missing") == ["dissolution_temperature_c"]


def test_temperature_c_on_shortlist_item_maps_and_hits_cache(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("mapped handoff T must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden_live)
    item = _item_from_record(record)
    t = item.pop("dissolution_temperature_c")
    item["temperature_c"] = t
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(item),
        held_process_basis=_held_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert payload.get("cache_match_status") == "exact"
    row = payload["comparison_rows"][0]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert row["dissolution_temperature_c"] == pytest.approx(t)
    origin = row["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "supplied"
        assert origin[name] != "from_screen"


def test_temperature_c_on_process_config_still_refuses(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    cfg = record["config"]
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            "temperature_c": cfg["dissolution_temperature_c"],
            **_held_from_record(record),
        }],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])


def test_screening_shortlist_inside_process_config_is_unknown(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        [{
            **_item_from_record(record),
            **_held_from_record(record),
            "screening_shortlist": {"source": "explicit", "items": []},
        }],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "screening_shortlist" in list(payload.get("extra_keys") or [])


def test_scenarios_and_shortlist_together_conflict(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        [{**_item_from_record(record), **_held_from_record(record)}],
        screening_shortlist=_shortlist(_item_from_record(record)),
        held_process_basis=_held_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "conflicting_evaluate_composition"


def test_expansion_cap_matches_evaluate(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    items = [_item_from_record(record)] * 21
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(*items),
        held_process_basis=_held_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "too_many_scenarios"


def test_two_item_expansion_is_two_rows_not_one_fill(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("an unconfirmed expansion must not start live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    arguments = dict(
        screening_shortlist=_shortlist(
            _item_from_record(record),
            _item_from_record(record, dissolution_temperature_c=999.0),
        ),
        held_process_basis=_held_from_record(record),
        engine_mode="auto",
    )
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(**arguments))
    # the stored row is one cache row, the unstored one a live child: two rows, not one row filled twice
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert (payload.get("n_live"), payload.get("n_cache")) == (1, 1)
    monkeypatch.setattr(tea, "_live", lambda config, timeout_seconds: {
        "success": False, "error_type": "timeout", "error": "the live TEA run produced no result",
    })
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(**arguments, confirm_live_tea=True))
    assert payload.get("success") is True
    assert payload.get("completed") == 1
    assert payload.get("failed") == 1
    assert len(payload["comparison_rows"]) == 2
    assert payload["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert payload["comparison_rows"][1]["msp_usd_per_kg"] is None


def test_polymer_solvent_t_as_process_config_is_still_incomplete(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data_screening_handoff(tea.evaluate_tea_lca_scenarios(
        [_item_from_record(record)],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)


# --- from test_specific_volume_rerank.py: Specific volume (1/rho) as a planner rerank axis: a TEA *proxy*, never a cost.
KEY = "max_stage_specific_volume_l_per_kg"


def _data_specific_volume_rerank(raw):
    return parse_tool_result(raw)["data"]


def test_objective_registered_lower_is_better_and_alias_resolves():
    assert tea._PLANNER_SORT_OBJECTIVES[KEY] == "min"
    assert tea._PLANNER_OBJECTIVE_ALIASES["max_stage_specific_volume"] == KEY
    assert "max_stage_specific_volume" not in tea._PLANNER_SORT_OBJECTIVES   # alias, not a second key
    assert len(tool_schemas()) == 24


def test_table_loads_and_digest_is_checked_not_trusted(monkeypatch):
    tea._density_table.cache_clear()
    table = tea._density_table()
    assert table["digest"] == tea._DENSITY_TABLE_CONTENT_DIGEST
    assert table["by_key"]["toluene"]["rho_effective_kg_m3"] == pytest.approx(862.3, abs=1.0)
    # MUST-FIRE: a wrong pinned constant must refuse, even though the file is unchanged
    monkeypatch.setattr(tea, "_DENSITY_TABLE_CONTENT_DIGEST", "0" * 64)
    tea._density_table.cache_clear()
    with pytest.raises(tea.DensityTableRefuse) as err:
        tea._density_table()
    assert err.value.error_code == "density_table_digest_mismatch"
    tea._density_table.cache_clear()


def test_aggregator_takes_worst_stage_and_refuses_unsupported_solvents():
    tea._density_table.cache_clear()
    v_tol = tea._planner_route_metric({"steps": [{"solvent": "toluene"}]}, KEY)
    v_ccl4 = tea._planner_route_metric({"steps": [{"solvent": "ccl4"}]}, KEY)
    assert v_ccl4 == pytest.approx(1000 / 1583.7, rel=1e-3)
    assert v_tol > v_ccl4                                                    # lighter solvent = larger specific volume = worse
    both = tea._planner_route_metric({"steps": [{"solvent": "ccl4"}, {"solvent": "toluene"}]}, KEY)
    assert both == pytest.approx(v_tol)                                      # max over stages
    assert tea._planner_route_metric({"steps": [{"solvent": "naphthalene"}]}, KEY) is None     # solid at 25 C
    assert tea._planner_route_metric({"steps": [{"solvent": "not-a-solvent-xyz"}]}, KEY) is None
    assert tea._planner_route_metric({"steps": [{"specific_volume_l_per_kg": 1.5}]}, KEY) == 1.5


def test_unsupported_route_sorts_last_and_is_listed(monkeypatch):
    """MUST-FIRE: without the None-sorts-last contract a solid-only route would lead."""
    tea._density_table.cache_clear()
    routes = [
        {"rank": 1, "steps": [{"solvent": "naphthalene"}]},   # solid at 25 C -> None
        {"rank": 2, "steps": [{"solvent": "toluene"}]},
        {"rank": 3, "steps": [{"solvent": "ccl4"}]},
    ]
    excl = tea._planner_specific_volume_exclusions(routes)
    assert excl == [{"route_rank": 1, "solvent": "naphthalene", "reason": "density_unresolved:solid_at_25C"}]
    vals = [tea._planner_route_metric(r, KEY) for r in routes]
    order = sorted(range(3), key=lambda i: (vals[i] is None, vals[i] if vals[i] is not None else float("inf"), routes[i]["rank"]))
    assert [routes[i]["rank"] for i in order] == [3, 2, 1]                   # ccl4, toluene, then the unresolved route last
    # counter-case: if None were treated as 0 the solid route would lead
    naive = sorted(range(3), key=lambda i: (vals[i] or 0.0))
    assert [routes[i]["rank"] for i in naive][0] == 1


def test_live_sort_stamps_proxy_disclosure_and_still_refuses_economics():
    tea._density_table.cache_clear()
    session = new_session()
    with bind_tool_session(session):
        plan = _data_specific_volume_rerank(separation.plan_multistage_separation(
            feed_polymers=["PS", "HDPE", "PET"], top_k_routes=6, breadth=4))
        handle = store_handle(session, tool="plan_multistage_separation", source_basis="planner", data=plan)
        out = _data_specific_volume_rerank(tea.rank_landscape(source="planner_routes", handle=handle,
                                       operation="sort", objective="max_stage_specific_volume"))
        assert out["success"] is True and out["objective"] == KEY and out["objective_direction"] == "min"
        axis = out["specific_volume_axis"]
        assert axis["is_cost_metric"] is False
        assert "NOT MEASURED" not in axis["second_plant"]
        assert "86.5%" in axis["second_plant"] and "BELOW the pre-registered 90% bar" in axis["second_plant"]
        assert axis["evidence_n"] == 51 and axis["evidence_spearman_msp"] == pytest.approx(0.9925)
        # the coverage stamp is DERIVED: it must match a recount from the table
        import duckdb as _duckdb
        _con = _duckdb.connect(str(tea._DENSITY_TABLE_DEFAULT), read_only=True)
        _counts = dict(_con.execute(
            "select verdict, count(*) from density_validated group by 1").fetchall())
        _con.close()
        assert axis["table_coverage"].startswith("630 of 786")
        for name, count in _counts.items():
            if name not in ("validated", "predicted"):
                assert f"{count} {name}" in axis["table_coverage"], (name, axis["table_coverage"])
        assert "out-of-band" not in axis["table_coverage"]      # the falsified literal is gone
        assert "specific_volume_excluded_routes" in out
        pts = out["landscape_points"]
        vals = [p[KEY] for p in pts]
        finite = [v for v in vals if v is not None]
        assert finite == sorted(finite) and vals[:len(finite)] == finite     # ascending, None last
        assert all(p["specific_volume_rule"] == "max_stage_1000_over_rho_25C" for p in pts)
        assert all(isinstance(p["specific_volume_unresolved_stages"], int) for p in pts)
        assert sum(p["specific_volume_unresolved_stages"] for p in pts) == len(out["specific_volume_excluded_routes"])
        bad = _data_specific_volume_rerank(tea.rank_landscape(source="planner_routes", handle=handle,
                                       operation="sort", objective="msp_usd_per_kg"))
        assert bad["error_code"] == "not_applicable_in_source"
        g = _data_specific_volume_rerank(tea.rank_landscape(source="planner_routes", handle=handle,
                                     operation="sort", objective="min_stage_g_score"))
        assert g["success"] is True and "specific_volume_axis" not in g       # stamps only on this objective
