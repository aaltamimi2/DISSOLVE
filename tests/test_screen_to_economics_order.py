"""Closed screen_to_economics_order. Default independent. No router.

Ranking does not wait on safety. Not a GSK-fail filter. Not lang_factor.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import dispatch, tool_schemas
from dissolve import campaign_basis, campaign_consume, safety, tea

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
_LEGAL = (
    "independent",
    "thermo_then_economics",
    "safety_then_economics",
)


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _forbid_live(monkeypatch):
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


def _two_row_mini(tmp_path):
    definition = _minimal_definition(pair_definitions=[
        {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
        {"config_sent": {"target_plastic": "LDPE", "solvent": "xylene"}},
    ])
    canonical = campaign_consume.canonical_json_digest(definition)
    toluene = _success_row(canonical, "p1", "LDPE", "Toluene", 1.0, 0.4)
    toluene["safety_standing"] = {
        "status": "evaluated",
        "safety_profile": {"ghs_signal_word": "Danger"},
    }
    xylene = _success_row(canonical, "p2", "LDPE", "Xylene", 2.0, 0.8)
    return _mini(tmp_path, [toluene, xylene], definition=definition)


def test_schema_exposes_the_closed_choice():
    schemas = {item["name"]: item for item in tool_schemas()}
    for name in ("evaluate_process", "rank_landscape"):
        props = schemas[name]["parameters"]["properties"]
        required = schemas[name]["parameters"].get("required") or []
        assert "screen_to_economics_order" in props
        assert "screen_to_economics_order" not in required
        assert "exclude_safety_fail" not in props
    old = schemas["evaluate_tea_lca_scenarios"]["parameters"]["properties"]
    assert "screen_to_economics_order" not in old


def test_omitted_and_explicit_independent_match(monkeypatch, tmp_path):
    _forbid_live(monkeypatch)
    _forbid_safety(monkeypatch)
    canonical, entry = _two_row_mini(tmp_path)
    registry = _write_registry(tmp_path, {canonical: entry})
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
    _forbid_live(monkeypatch)
    _forbid_safety(monkeypatch)
    canonical, entry = _two_row_mini(tmp_path)
    registry = _write_registry(tmp_path, {canonical: entry})
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
    _forbid_live(monkeypatch)
    _forbid_safety(monkeypatch)
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
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
    _forbid_live(monkeypatch)
    _forbid_safety(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    omitted = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="cache",
    ))
    thermo = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="cache",
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
        engine_mode="cache",
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
    _forbid_live(monkeypatch)
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    ranked = _rank(
        monkeypatch,
        registry,
        campaign_fingerprint=_CANONICAL,
        screen_to_economics_order="pipeline",
    )
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(_record_by_label("ldpe-route-c1")),
        engine_mode="cache",
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
    _forbid_live(monkeypatch)
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
        engine_mode="cache",
        screen_to_economics_order="independent",
    ))
    assert old.get("error_code") == "unknown_process_field"
    assert old.get("extra_keys") == ["screen_to_economics_order"]
    misspelled = _data(tea.rank_landscape(
        screen_to_economic_order="independent",
    ))
    assert misspelled.get("error_code") == "unknown_process_field"
    assert misspelled.get("extra_keys") == ["screen_to_economic_order"]


def test_exclude_safety_fail_stays_unknown_extra(monkeypatch, tmp_path):
    _forbid_live(monkeypatch)
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    _forbid_safety(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    omitted = dispatch(
        "evaluate_process",
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="cache",
    )
    thermo = dispatch(
        "evaluate_process",
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="cache",
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
        engine_mode="cache",
        screen_to_economics_order="pipeline",
    )
    assert refused.get("available") is False
    assert refused.get("refusal") == "invalid_admitted_record_query"


def test_lang_factor_stays_unbound(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1, lang_factor=3.0),
        engine_mode="cache",
        screen_to_economics_order="independent",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "lang_factor" in list(payload.get("extra_keys") or [])
    assert "depreciation" not in tea._COEFFICIENT_DEFAULTS
    assert "lang_factor" not in tea._COEFFICIENT_DEFAULTS
