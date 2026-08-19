"""A rank_landscape handle can be a tornado inherit source. Not a live child."""
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

from dissolve import campaign_basis, campaign_consume, landscape, tea
from dissolve.session import bind_tool_session, new_session, store_handle


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


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


def _write_registry(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "campaign_registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


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


def _rank(monkeypatch, registry_path: Path, **kwargs):
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry_path))
    return _data(tea.rank_landscape(**kwargs))


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("rank inherit must not start live")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_compact_point_carries_the_executed_twelve():
    record = _route_c1()
    cfg = record["config"]
    point = landscape.compact_process_row({
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
    _forbid_live(monkeypatch)
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
    canonical, entry = _mini(tmp_path, [
        _success_row(canonical, "a", "LDPE", "Toluene", 1.0, 0.4),
        _success_row(canonical, "b", "LDPE", "Xylene", 2.0, 0.8),
    ], definition=definition)
    registry = _write_registry(tmp_path, {canonical: entry})
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
            engine_mode="cache",
        ))
    assert payload.get("error_code") == "not_economics_handle"


def test_tornado_inherits_from_a_ranked_cache_identity(monkeypatch, tmp_path):
    record = _route_c1()
    cfg = record["config"]
    _forbid_live(monkeypatch)
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
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
    registry = _write_registry(tmp_path, {canonical: entry})
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
            engine_mode="cache",
            analysis_mode="tornado",
        ))
        tornado = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            row_id="cache-hit",
            engine_mode="cache",
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
    _forbid_live(monkeypatch)
    definition = _minimal_definition()
    canonical = campaign_consume.canonical_json_digest(definition)
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
    registry = _write_registry(tmp_path, {canonical: entry})
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
            engine_mode="cache",
        ))
        tornado = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            row_id="cache-hit",
            engine_mode="cache",
            analysis_mode="tornado",
        ))
    assert missing.get("error_code") == "ambiguous_handle_row"
    assert missing.get("n_rows") == 4
    assert tornado.get("success") is True
    assert tornado["field_origin"]["target_polymer"] == "inherited"
    assert tornado["polymer"] == "LDPE"
