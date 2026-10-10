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
    SYSTEM_PROMPT,
    dispatch,
    source_basis_for,
    tool_schemas,
)
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


def test_rank_landscape_surface_is_process_rows_and_planner_routes():
    """The residual-route optimizer and the superstructure data-gate are gone; the retired arguments are unknown."""
    schema = {item["name"]: item for item in tool_schemas()}["rank_landscape"]["parameters"]["properties"]
    assert set(schema["source"]["enum"]) == {"process_rows", "planner_routes"}
    assert set(schema["operation"]["enum"]) == {"sort", "pareto_dominance"}
    for retired in (
        "formulation", "planner_solvent_map", "allowed_solvents", "feed_mass_fractions", "scenario",
        "recovery_yield", "polymer_market_values_usd_per_mt", "solver_name", "composition_slices",
    ):
        assert retired not in schema
        payload = _data(tea.rank_landscape(**{retired: "x"}))
        assert payload["error_code"] == "unknown_process_field"
        assert payload["extra_keys"] == [retired]
    for source in ("residual_route", "superstructure"):
        payload = _data(tea.rank_landscape(source=source))
        assert payload["error_code"] == "invalid_admitted_record_query"
        assert not hasattr(tea_ranking, "rank_residual_route")


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
    assert len(tool_schemas()) == 29  # find_plastchem_contaminants 2026-10-05, optimize_waste_pathway 2026-10-03, tea_tornado 2026-10-07


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


def _plan_handle(session, routes, breadth=2):
    return store_handle(session, tool="plan_multistage_separation", source_basis="planner",
                        data={"success": True, "top_k_sequences": routes, "breadth": breadth, "branch_rule": "count"})


def test_route_safety_rank_puts_unscored_routes_last_and_marks_ties():
    """MUST-FIRE: route 1's second stage has no CHEM21 score. Aggregating over the scored stages only gave it a
    worst of 7 and led it ahead of the routes scored 17; a missing score never counts in an option's favour."""
    session = new_session()
    routes = [
        {"rank": 1, "steps": [{"chem21_worst": 7}, {"chem21_safety_score": None}]},
        {"rank": 2, "steps": [{"chem21_worst": 17}]},
        {"rank": 3, "steps": [{"chem21_worst": 17}]},
        {"rank": 4, "steps": [{"chem21_worst": 5}]},
    ]
    with bind_tool_session(session):
        out = json.loads(tea.rank_landscape(source="planner_routes", handle=_plan_handle(session, routes),
                                            operation="sort", objective="max_stage_chem21"))["data"]
    points = out["landscape_points"]
    assert [p["original_thermo_rank"] for p in points] == [4, 2, 3, 1]
    assert [p["unresolved_stages"] for p in points] == [0, 0, 0, 1]
    assert points[1]["tied_with_ranks"] == [3] and points[2]["tied_with_ranks"] == [2]
    assert points[0]["tied_with_ranks"] == [] and points[3]["tied_with_ranks"] == []


def test_route_safety_rank_needs_no_tea_but_economics_still_does(monkeypatch):
    """The hosted site runs without the TEA worker. Ranking a plan's routes by CHEM21 or G score uses no TEA, yet
    the ranker refused with live_tea_unavailable and the model ranked routes by hand, wrongly (validation,
    2026-09-25)."""
    monkeypatch.setattr(tea, "_live_tea_blocker", lambda: {
        "error": "Live TEA is not available", "live_tea_reason": "test", "remediation": "run dissolve doctor"})
    session = new_session()
    routes = [{"rank": 1, "steps": [{"g_score": 5.4}, {"g_score": 7.4}]}, {"rank": 2, "steps": [{"g_score": 6.1}]}]
    with bind_tool_session(session):
        out = json.loads(tea.rank_landscape(source="planner_routes", handle=_plan_handle(session, routes),
                                            operation="sort", objective="min_stage_g_score"))["data"]
    assert out["success"] is True
    assert [p["original_thermo_rank"] for p in out["landscape_points"]] == [2, 1]
    refused = json.loads(tea.rank_landscape(target_polymer="LDPE"))["data"]
    assert refused["error_code"] == "live_tea_unavailable"


# --- Cursor code review, ranking findings (2026-09-26): each test fails on 32a755de.

def test_a_success_without_lca_coverage_is_excluded_by_name():
    """B05-R1, B10-R2: every live and cached result carries lca_coverage (regenerated 2026-09-22); a row without it
    (a screening estimate) was excluded as a generic not_usable."""
    rows = [_success_row("c", "p1", "LDPE", "toluene", 1.2, 2.0), _success_row("c", "p2", "LDPE", "xylene", 1.5, 2.4)]
    rows[1]["comparison_row"].pop("lca_coverage")
    usable, excluded, by_type = tea_ranking.project_usable(rows, canonical="c")
    assert [point["pair_id"] for point in usable] == ["p1"]
    assert excluded[0]["error_type"] == "missing_lca_coverage"
    assert by_type == {"missing_lca_coverage": 1}


def test_rows_sharing_a_pair_id_stay_two_points():
    """B05-R2, B10-R2: frontier flags and rank joins keyed on pair_id, so two unlabeled LDPE|toluene|C1 rows at 80
    and 120 C were both marked frontier and one dropped out of the ranks."""
    rows = [
        _success_row("c", "LDPE|toluene|C1", "LDPE", "toluene", 1.2, 2.0),
        _success_row("c", "LDPE|toluene|C1", "LDPE", "toluene", 1.5, 2.4),
    ]
    usable, _excluded, _by_type = tea_ranking.project_usable(rows, canonical="c")
    assert [point["pair_id"] for point in usable] == ["LDPE|toluene|C1", "LDPE|toluene|C1#2"]
    block = tea_ranking.quality_block(usable, grouping={})
    assert [point["is_frontier"] for point in block["landscape_points"]] == [True, False]


def test_a_polymer_with_one_usable_design_is_named_in_a_grouped_frontier():
    """B05-R3, B10-R2: PVC with one usable row vanished from grouped_fronts and from every excluded list."""
    rows = [
        _success_row("c", f"pet-{index}", "PET", solvent, msp, gwp)
        for index, (solvent, msp, gwp) in enumerate((("gvl", 1.2, 2.5), ("nmp", 1.6, 2.0), ("dmso", 1.4, 2.2)))
    ] + [_success_row("c", "pvc-0", "PVC", "thf", 1.1, 3.0)]
    usable, excluded, by_type = tea_ranking.project_usable(rows, canonical="c")
    payload = tea_ranking._rank_usable_population(
        usable, excluded, by_type, n_rows_read=len(rows), polymer_grouping="per_target_polymer",
        operation="pareto_dominance",
    )
    assert [group["target_polymer"] for group in payload["grouped_fronts"]] == ["PET"]
    single = payload["polymers_with_one_usable_design"]
    assert [item["target_polymer"] for item in single] == ["PVC"]
    assert single[0]["point"]["pair_id"] == "pvc-0"


def test_two_names_for_one_requested_campaign_field_that_disagree_are_refused():
    """B05-R6: target_mass_percent 60 and target_plastic_percent 80 became one field holding 80."""
    with pytest.raises(tea_ranking.CampaignConsumeError) as error:
        tea_ranking.publicize_requested_fields({"target_mass_percent": 60, "target_plastic_percent": 80})
    assert error.value.error_code == "conflicting_process_field"
    assert tea_ranking.publicize_requested_fields(
        {"target_mass_percent": 60, "target_plastic_percent": 60},
    )["target_mass_percent"] == 60


def test_evaluate_refuses_precipitation_format_drop_on_this_instance():
    """B03-R6, B10-R3: evaluate on this instance runs precipitation_temperature_format='constant' only, so 'drop'
    refuses with field_not_on_this_instance."""
    with pytest.raises(tea._ScenarioInputError) as refused:
        tea._validated_flowsheet_switches({"precipitation_temperature_format": "drop"}, energy_case="C1")
    assert refused.value.error_code == "field_not_on_this_instance"
