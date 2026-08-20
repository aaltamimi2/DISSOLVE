"""evaluate_process lookup, evaluate, sensitivity, and route.

lookup_admitted_process_records, evaluate_tea_lca_scenarios,
analyze_tea_sensitivity, and evaluate_stored_route_tea_lca stay as
Python engines. Those registry names are retired.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import UNWIRED, dispatch, tool_schemas
from dissolve import campaign_consume, registry, tea, thermodynamics
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.session import bind_tool_session, load_handle, new_session, store_handle

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
_LEGAL_MODES = ("lookup", "evaluate", "sensitivity", "route")


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("evaluate_process lookup must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _schema(name: str) -> dict:
    return next(item for item in tool_schemas() if item["name"] == name)


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


def _parity_keys(payload: dict) -> dict:
    rows = payload.get("comparison_rows") or []
    return {
        "success": payload.get("success"),
        "error_code": payload.get("error_code"),
        "analysis_type": payload.get("analysis_type"),
        "record_count": payload.get("record_count"),
        "n_rows_consumed": payload.get("n_rows_consumed"),
        "labels": [str(row.get("label") or "") for row in rows],
        "row_count": len(rows) if isinstance(rows, list) else None,
    }


def test_evaluate_process_is_registered_lookup_engine_stays():
    assert "evaluate_process" in registry.BY_NAME
    assert "evaluate_process" in EXPECTED_REGISTRY_NAMES
    assert "lookup_admitted_process_records" not in registry.BY_NAME
    assert "lookup_admitted_process_records" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.lookup_admitted_process_records)
    assert "evaluate_tea_lca_scenarios" not in registry.BY_NAME
    assert "evaluate_tea_lca_scenarios" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.evaluate_tea_lca_scenarios)
    assert "analyze_tea_sensitivity" not in registry.BY_NAME
    assert "analyze_tea_sensitivity" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.analyze_tea_sensitivity)
    assert "evaluate_stored_route_tea_lca" not in registry.BY_NAME
    assert "evaluate_stored_route_tea_lca" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.evaluate_stored_route_tea_lca)
    assert "evaluate_stored_route_tea_lca" not in UNWIRED
    assert "evaluate_process" not in UNWIRED
    assert "evaluate_process" in tea.PROCESS_CONFIRM_TOOLS
    assert "evaluate_tea_lca_scenarios" not in tea.PROCESS_CONFIRM_TOOLS
    assert "analyze_tea_sensitivity" not in tea.PROCESS_CONFIRM_TOOLS
    assert len(EXPECTED_REGISTRY_NAMES) == 31
    assert len(registry.REGISTRY) == 31
    retired = dispatch("evaluate_tea_lca_scenarios")
    assert retired.get("available") is False
    assert retired.get("refusal") == "unknown_tool"
    retired_sensitivity = dispatch("analyze_tea_sensitivity")
    assert retired_sensitivity.get("available") is False
    assert retired_sensitivity.get("refusal") == "unknown_tool"
    retired_route = dispatch("evaluate_stored_route_tea_lca")
    assert retired_route.get("available") is False
    assert retired_route.get("refusal") == "unknown_tool"


def test_schema_uses_two_typed_objects_not_top_level_polymer():
    props = _schema("evaluate_process")["parameters"]["properties"]
    assert "mode" in props
    assert "lookup_filter" in props
    assert "process_config" in props
    assert "process_configs" in props
    assert "screening_shortlist" in props
    assert "held_process_basis" in props
    assert props["lookup_filter"]["type"] == "object"
    assert props["process_config"]["type"] == "object"
    assert props["process_configs"]["type"] == "array"
    assert props["process_configs"]["items"]["type"] == "object"
    assert props["screening_shortlist"]["type"] == "object"
    assert props["held_process_basis"]["type"] == "object"
    assert "target_polymer" not in props
    assert "solvent" not in props
    assert "parameter" not in props
    assert "scenarios" not in props
    assert "feed_mass_fractions" not in props
    required = _schema("evaluate_process")["parameters"].get("required") or []
    assert "mode" not in required
    assert "lookup_filter" not in required
    assert "process_config" not in required
    assert "process_configs" not in required
    assert "screening_shortlist" not in required
    assert "held_process_basis" not in required


def test_missing_mode_is_named_envelope(monkeypatch):
    _forbid_live(monkeypatch)
    omitted = _data(tea.evaluate_process())
    blank = _data(tea.evaluate_process(mode=""))
    whitespace = _data(tea.evaluate_process(mode="  "))
    for payload in (omitted, blank, whitespace):
        assert payload.get("success") is False
        assert payload.get("error_code") == "missing_mode"
        assert payload.get("legal_modes") == list(_LEGAL_MODES)
        assert payload.get("tool_name") == "evaluate_process"
        assert "comparison_rows" not in payload
    dispatched = dispatch("evaluate_process")
    assert dispatched.get("available") is False
    assert dispatched.get("refusal") == "missing_mode"


def test_unknown_mode_lists_closed_set(monkeypatch):
    _forbid_live(monkeypatch)
    for token in ("optimization", "pareto", "sort", "waste"):
        payload = _data(tea.evaluate_process(mode=token))
        assert payload.get("success") is False
        assert payload.get("error_code") == "invalid_admitted_record_query"
        assert payload.get("error_code") != "missing_mode"
        assert payload.get("legal_modes") == list(_LEGAL_MODES)
        assert payload.get("mode") == token


def test_top_level_polymer_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        target_polymer="LDPE",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["target_polymer"]
    assert payload.get("error_code") != "missing_target_polymer"


def test_process_config_on_lookup_is_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        process_config={"target_polymer": "LDPE"},
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["process_config"]
    empty = _data(tea.evaluate_process(
        mode="lookup",
        process_config={},
    ))
    assert empty.get("error_code") == "not_applicable_in_mode"
    batch = _data(tea.evaluate_process(
        mode="lookup",
        process_configs=[{"target_polymer": "LDPE"}],
    ))
    assert batch.get("error_code") == "not_applicable_in_mode"
    assert batch.get("inapplicable_fields") == ["process_configs"]


def test_screening_handoff_on_lookup_is_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        screening_shortlist={"source": "explicit", "items": []},
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["screening_shortlist"]
    assert payload.get("applicable_mode") == "evaluate"
    held = _data(tea.evaluate_process(
        mode="lookup",
        held_process_basis={"energy_case": "C1"},
    ))
    assert held.get("error_code") == "not_applicable_in_mode"
    assert held.get("inapplicable_fields") == ["held_process_basis"]


def test_lookup_filter_extra_keys_are_unknown(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE", "msp_usd_per_kg": 1},
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["msp_usd_per_kg"]


def test_lookup_filter_must_be_an_object(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter=["LDPE"],
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("field") == "lookup_filter"


def test_parameter_on_lookup_is_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        parameter="solvent_price",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["parameter"]


def test_admitted_cache_lookup_parity_via_filter(monkeypatch):
    _forbid_live(monkeypatch)
    cache_n = len(tea._records())
    assert cache_n > 0
    direct = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
        },
    ))
    assert wrapped.get("tool_name") == "evaluate_process"
    assert direct.get("tool_name") == "lookup_admitted_process_records"
    assert _parity_keys(wrapped) == _parity_keys(direct)
    assert wrapped.get("success") is True
    omitted = _data(tea.evaluate_process(mode="lookup"))
    assert omitted.get("success") is False
    assert omitted.get("error_code") == "missing_target_polymer"
    assert omitted.get("tool_name") == "evaluate_process"
    assert "comparison_rows" not in omitted
    assert omitted.get("record_count") is None
    assert len(tea._records()) == cache_n


def test_sensitivity_selector_stays_on_lookup_filter(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "sensitivity_axes": ["solvent_price"],
        },
    ))
    assert payload.get("success") is True
    assert payload["analysis_type"] == "admitted_process_sensitivity_records"
    labels = {str(row.get("label") or "") for row in payload["comparison_rows"]}
    assert "ldpe-route-c1" in {item.casefold() for item in labels}


def test_route_missing_handle_is_named_refuse(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(mode="route"))
    assert payload.get("success") is False
    assert payload.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("error_code") != "missing_stored_route"
    assert payload.get("tool_name") == "evaluate_process"
    blank = _data(tea.evaluate_process(mode="route", handle="  "))
    assert blank.get("error_code") == "unknown_handle"
    assert blank.get("error_code") != "tool_not_wired"
    filtered = _data(tea.evaluate_process(
        mode="route",
        lookup_filter={"target_polymer": "LDPE"},
    ))
    assert filtered.get("error_code") == "not_applicable_in_mode"
    assert filtered.get("inapplicable_fields") == ["lookup_filter"]
    batch = _data(tea.evaluate_process(
        mode="route",
        process_configs=[{"target_polymer": "LDPE"}],
    ))
    assert batch.get("error_code") == "not_applicable_in_mode"
    assert batch.get("inapplicable_fields") == ["process_configs"]
    config = _data(tea.evaluate_process(
        mode="route",
        process_config={"target_polymer": "LDPE"},
        handle="h1",
    ))
    assert config.get("error_code") == "not_applicable_in_mode"
    assert config.get("inapplicable_fields") == ["process_config"]
    assert config.get("applicable_mode") == "evaluate"
    dispatched = dispatch("evaluate_process", mode="route")
    assert dispatched.get("available") is False
    assert dispatched.get("refusal") == "unknown_handle"
    old = dispatch("evaluate_stored_route_tea_lca")
    assert old.get("available") is False
    assert old.get("refusal") == "unknown_tool"
    assert "evaluate_stored_route_tea_lca" not in UNWIRED
    assert "evaluate_process" not in UNWIRED
    assert _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
    )).get("success") is True


def test_campaign_lookup_via_filter(monkeypatch, tmp_path):
    _forbid_live(monkeypatch)
    registry_path = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry_path))
    direct = _data(tea.lookup_admitted_process_records(
        source="campaign",
        campaign_fingerprint=_CANONICAL,
    ))
    wrapped = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "source": "campaign",
            "campaign_fingerprint": _CANONICAL,
        },
    ))
    assert wrapped.get("success") is True
    assert wrapped.get("tool_name") == "evaluate_process"
    assert wrapped.get("ingested_into_admitted_cache") is False
    assert _parity_keys(wrapped) == _parity_keys(direct)


def test_dispatch_lookup_mode_issues_handle(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        out = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={
                "target_polymer": "LDPE",
                "solvent": "Dodecane",
            },
        )
        assert out.get("available") is True
        assert out.get("handle")
        assert out.get("source_basis") == "tea_cache_exact"
        stored = load_handle(session, out["handle"])
        assert stored.get("tool") == "evaluate_process"
        assert "comparison_rows" in stored["exact"]
        assert "top" not in out


def _record_with_energy(energy_case: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record["config"].get("energy_case") or "").upper() == energy_case
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


def _evaluate_parity(payload: dict) -> dict:
    rows = payload.get("comparison_rows") or payload.get("failures") or []
    return {
        "success": payload.get("success"),
        "error_code": payload.get("error_code"),
        "cache_match_status": payload.get("cache_match_status"),
        "missing": payload.get("missing"),
        "extra_keys": payload.get("extra_keys"),
        "row_count": len(rows) if isinstance(rows, list) else None,
        "polymers": [row.get("target_polymer") for row in rows if isinstance(row, dict)],
        "energy_cases": [row.get("energy_case") for row in rows if isinstance(row, dict)],
        "origins": [row.get("field_origin") for row in rows if isinstance(row, dict)],
        "msps": [row.get("msp_usd_per_kg") for row in rows if isinstance(row, dict)],
    }


def test_evaluate_mode_without_config_is_missing_scenarios(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(mode="evaluate", engine_mode="cache"))
    assert payload.get("success") is False
    assert payload.get("error_code") == "missing_scenarios"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"
    dispatched = dispatch("evaluate_process", mode="evaluate", engine_mode="cache")
    assert dispatched.get("available") is False
    assert dispatched.get("refusal") == "missing_scenarios"


def test_evaluate_mode_singular_config_matches_old_name(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    twelve = _public_from_record(record)
    direct = _data(tea.evaluate_tea_lca_scenarios(
        [twelve], engine_mode="cache",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="cache",
    ))
    assert wrapped.get("tool_name") == "evaluate_process"
    assert direct.get("tool_name") == "evaluate_tea_lca_scenarios"
    assert wrapped.get("success") is True
    assert _evaluate_parity(wrapped) == _evaluate_parity(direct)
    sibling = next(
        item for item in tea._records()
        if str(item["config"].get("energy_case") or "").upper() == "C1"
        and item["config"]["target_plastic"] != record["config"]["target_plastic"]
    )
    other = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(sibling),
        engine_mode="cache",
    ))
    assert other.get("success") is True
    assert _evaluate_parity(other)["polymers"] != _evaluate_parity(wrapped)["polymers"]
    assert _evaluate_parity(other)["msps"] != _evaluate_parity(wrapped)["msps"]


def test_evaluate_mode_batch_process_configs_matches_old_name(monkeypatch):
    c1 = _record_with_energy("C1")
    c2 = next(
        item for item in tea._records()
        if str(item["config"].get("energy_case") or "").upper() == "C2"
        and item["config"]["target_plastic"] == c1["config"]["target_plastic"]
        and item["config"]["solvent"] == c1["config"]["solvent"]
    )
    _forbid_live(monkeypatch)
    batch = [_public_from_record(c1), _public_from_record(c2)]
    direct = _data(tea.evaluate_tea_lca_scenarios(batch, engine_mode="cache"))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=batch,
        engine_mode="cache",
    ))
    assert wrapped.get("success") is True
    assert wrapped.get("tool_name") == "evaluate_process"
    assert _evaluate_parity(wrapped) == _evaluate_parity(direct)
    assert len(_evaluate_parity(wrapped)["energy_cases"]) == 2
    assert _evaluate_parity(wrapped)["msps"][0] != _evaluate_parity(wrapped)["msps"][1]


def test_evaluate_mode_incomplete_lists_missing_public_fields(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_live(monkeypatch)
    partial = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
    }
    direct = _data(tea.evaluate_tea_lca_scenarios(
        [partial], engine_mode="cache",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=partial,
        engine_mode="cache",
    ))
    assert wrapped.get("error_code") == "incomplete_process_config"
    assert wrapped.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert wrapped.get("tool_name") == "evaluate_process"
    assert "comparison_rows" not in wrapped
    assert _evaluate_parity(wrapped) == _evaluate_parity(direct)


def test_evaluate_mode_temperature_c_is_unknown_extra(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(
            record,
            temperature_c=record["config"]["dissolution_temperature_c"],
        ),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])


def test_evaluate_mode_parameter_is_not_applicable(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["parameter"]
    assert payload.get("error_code") != "tool_not_wired"


def test_evaluate_mode_too_many_process_configs(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    twelve = _public_from_record(record)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[twelve] * 21,
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "too_many_scenarios"
    assert payload.get("tool_name") == "evaluate_process"


def test_evaluate_mode_shortlist_without_held_lists_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    shortlist = _shortlist(_item_from_record(record))
    direct = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=shortlist, engine_mode="cache",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=shortlist,
        engine_mode="cache",
    ))
    assert wrapped.get("error_code") == "screening_basis_incomplete"
    assert wrapped.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert wrapped.get("tool_name") == "evaluate_process"
    assert wrapped.get("error_code") != "unknown_process_field"
    assert "comparison_rows" not in wrapped
    assert _evaluate_parity(wrapped) == _evaluate_parity(direct)


def test_evaluate_mode_shortlist_plus_held_matches_old_name(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    shortlist = _shortlist(_item_from_record(record))
    held = _held_from_record(record)
    direct = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=shortlist,
        held_process_basis=held,
        engine_mode="cache",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=shortlist,
        held_process_basis=held,
        engine_mode="cache",
    ))
    assert wrapped.get("tool_name") == "evaluate_process"
    assert direct.get("tool_name") == "evaluate_tea_lca_scenarios"
    assert wrapped.get("success") is True
    origin = wrapped["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "supplied"
        assert origin[name] != "from_screen"
        assert origin[name] != "inherited"
    assert _evaluate_parity(wrapped) == _evaluate_parity(direct)


def test_evaluate_mode_process_config_and_shortlist_conflict(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        screening_shortlist=_shortlist(_item_from_record(record)),
        held_process_basis=_held_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "conflicting_evaluate_composition"
    assert payload.get("tool_name") == "evaluate_process"


def test_screening_handoff_on_sensitivity_and_route_is_not_applicable(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    shortlist = _shortlist(_item_from_record(record))
    sensitivity = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=_public_from_record(record),
        screening_shortlist=shortlist,
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert sensitivity.get("error_code") == "not_applicable_in_mode"
    assert sensitivity.get("inapplicable_fields") == ["screening_shortlist"]
    assert sensitivity.get("applicable_mode") == "evaluate"
    route = _data(tea.evaluate_process(
        mode="route",
        screening_shortlist=shortlist,
        handle="h1",
    ))
    assert route.get("error_code") == "not_applicable_in_mode"
    assert route.get("inapplicable_fields") == ["screening_shortlist"]
    assert route.get("applicable_mode") == "evaluate"


def test_dispatch_evaluate_mode_shortlist_issues_handle(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        out = dispatch(
            "evaluate_process",
            mode="evaluate",
            screening_shortlist=_shortlist(_item_from_record(record)),
            held_process_basis=_held_from_record(record),
            engine_mode="cache",
        )
        assert out.get("available") is True
        assert out.get("handle")
        stored = load_handle(session, out["handle"])
        assert stored.get("tool") == "evaluate_process"
        assert stored["exact"]["tool_name"] == "evaluate_process"
        origin = stored["exact"]["comparison_rows"][0]["field_origin"]
        assert origin["target_polymer"] == "from_screen"
        for name in tea._NINE_HELD_PUBLIC_FIELDS:
            assert origin[name] == "supplied"


def test_evaluate_mode_process_configs_must_be_a_list(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=_public_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("field") == "process_configs"


def test_evaluate_mode_inherits_omitted_fields_from_handle(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_process(
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        wrapped = _data(tea.evaluate_process(
            mode="evaluate",
            process_config={
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            },
            handle=handle,
            engine_mode="cache",
        ))
        direct = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            engine_mode="cache",
        ))
    assert wrapped.get("success") is True
    origin = wrapped["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["dissolution_temperature_c"] == "supplied"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"
    assert _evaluate_parity(wrapped) == _evaluate_parity(direct)


def test_dispatch_evaluate_mode_issues_handle(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        out = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="cache",
        )
        assert out.get("available") is True
        assert out.get("handle")
        assert out.get("source_basis") == "tea_cache_exact"
        stored = load_handle(session, out["handle"])
        assert stored.get("tool") == "evaluate_process"
        assert "comparison_rows" in stored["exact"]
        assert "top" not in out
        assert stored["exact"]["tool_name"] == "evaluate_process"


def _route_c1() -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == "ldpe-route-c1"
    )


def _sensitivity_parity(payload: dict) -> dict:
    rows = payload.get("sensitivity_rows") or []
    return {
        "success": payload.get("success"),
        "error_code": payload.get("error_code"),
        "analysis_type": payload.get("analysis_type"),
        "parameter": payload.get("parameter"),
        "missing": payload.get("missing"),
        "row_count": len(rows) if isinstance(rows, list) else None,
        "row_parameters": [row.get("parameter") for row in rows if isinstance(row, dict)],
        "row_success": [row.get("success") for row in rows if isinstance(row, dict)],
        "row_values": [row.get("value") for row in rows if isinstance(row, dict)],
        "metric_values": [row.get("metric_value") for row in rows if isinstance(row, dict)],
        "origins": [row.get("field_origin") for row in rows if isinstance(row, dict)],
    }


def test_sensitivity_mode_without_parameter_is_unsupported(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(mode="sensitivity"))
    assert payload.get("success") is False
    assert payload.get("error_code") == "unsupported_parameter"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"


def test_sensitivity_mode_process_configs_is_not_applicable(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="sensitivity",
        process_configs=[_public_from_record(record)],
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["process_configs"]
    assert payload.get("applicable_mode") == "evaluate"


def test_sensitivity_mode_solvent_price_matches_old_name(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    twelve = _public_from_record(record)
    direct = _data(tea.analyze_tea_sensitivity(
        twelve, parameter="solvent_price", engine_mode="cache",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=twelve,
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert wrapped.get("tool_name") == "evaluate_process"
    assert direct.get("tool_name") == "analyze_tea_sensitivity"
    assert wrapped.get("success") is True
    assert len(wrapped["sensitivity_rows"]) >= 2
    assert _sensitivity_parity(wrapped) == _sensitivity_parity(direct)


def test_sensitivity_mode_incomplete_lists_missing_public_fields(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_live(monkeypatch)
    partial = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
    }
    direct = _data(tea.analyze_tea_sensitivity(
        partial, parameter="solvent_price", engine_mode="cache",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=partial,
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert wrapped.get("error_code") == "incomplete_process_config"
    assert wrapped.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert wrapped.get("tool_name") == "evaluate_process"
    assert "sensitivity_rows" not in wrapped
    assert _sensitivity_parity(wrapped) == _sensitivity_parity(direct)


def test_sensitivity_mode_inherits_baseline_from_evaluate_handle(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_process(
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        wrapped = _data(tea.evaluate_process(
            mode="sensitivity",
            process_config={
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            },
            parameter="solvent_price",
            handle=handle,
            engine_mode="cache",
            analysis_mode="tornado",
        ))
        direct = _data(tea.analyze_tea_sensitivity(
            {
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            },
            parameter="solvent_price",
            handle=handle,
            engine_mode="cache",
            analysis_mode="tornado",
        ))
    assert wrapped.get("success") is True
    origin = wrapped["sensitivity_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["dissolution_temperature_c"] == "supplied"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"
    assert _sensitivity_parity(wrapped) == _sensitivity_parity(direct)


def test_dispatch_sensitivity_mode_issues_handle(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        out = dispatch(
            "evaluate_process",
            mode="sensitivity",
            process_config=_public_from_record(record),
            parameter="solvent_price",
            engine_mode="cache",
        )
        assert out.get("available") is True
        assert out.get("handle")
        assert out.get("source_basis") == "tea_cache_exact"
        stored = load_handle(session, out["handle"])
        assert stored.get("tool") == "evaluate_process"
        assert "sensitivity_rows" in stored["exact"]
        assert stored["exact"]["tool_name"] == "evaluate_process"


_ROUTE_CAPACITY = 20_000.0
_ROUTE_ENERGY = "C1"
_ROUTE_PRECIP = 25.0
_ROUTE_FRACTION = 0.55
_EXACT_PLANNER_ROUTE: tuple[dict, dict] | None = None


def _plan_routes(feed: tuple[str, str], composition: dict[str, float]) -> list[dict]:
    result = _data(registry.BY_NAME["plan_multistage_separation"].fn(
        feed_polymers=list(feed),
        feed_mass_fractions=dict(composition),
        top_k_routes=10,
    ))
    assert result.get("success") is True
    return list(result.get("top_k_sequences") or [])


def _one_step_route(routes: list[dict], target: str, residue: str) -> dict | None:
    return next((
        route for route in routes
        if route.get("complete") is True
        and len(route.get("steps") or []) == 1
        and route["steps"][0].get("dissolved_polymer") == target
        and route.get("final_residue") == residue
    ), None)


def _exact_planner_route() -> tuple[dict, dict]:
    global _EXACT_PLANNER_ROUTE
    if _EXACT_PLANNER_ROUTE is not None:
        return (
            copy.deepcopy(_EXACT_PLANNER_ROUTE[0]),
            copy.deepcopy(_EXACT_PLANNER_ROUTE[1]),
        )
    targets = sorted({
        str(record["config"]["target_plastic"])
        for record in tea._records()
        if math.isclose(
            float(record["config"]["target_plastic_percent"]),
            100.0 * _ROUTE_FRACTION,
            rel_tol=0,
            abs_tol=1e-9,
        )
        and math.isclose(
            float(record["config"]["processing_capacity"]),
            _ROUTE_CAPACITY,
            rel_tol=0,
            abs_tol=1e-9,
        )
        and str(record["config"]["energy_case"]).upper() == _ROUTE_ENERGY
        and math.isclose(
            float(record["config"]["precipitation_temperature_c"]),
            _ROUTE_PRECIP,
            rel_tol=0,
            abs_tol=1e-9,
        )
    })
    for target in targets:
        for residue in sorted(thermodynamics.get_available_polymers()):
            if residue == target:
                continue
            feed = tuple(sorted((target, residue)))
            composition = {
                target: _ROUTE_FRACTION,
                residue: 1.0 - _ROUTE_FRACTION,
            }
            route = _one_step_route(_plan_routes(feed, composition), target, residue)
            if route is None:
                continue
            try:
                step = route["steps"][0]
                scenario = {
                    "target_polymer": target,
                    "solvent": step["solvent"],
                    "target_mass_percent": 100.0 * _ROUTE_FRACTION,
                    "processing_capacity_mt_per_yr": _ROUTE_CAPACITY,
                    "energy_case": _ROUTE_ENERGY,
                    "dissolution_temp_c": step["temperature_c"],
                    "precipitation_temp_c": _ROUTE_PRECIP,
                }
                scenario.update(tea._stored_route_named_remainder(str(step["solvent"])))
                config = tea._scenario_config(scenario)
            except (TypeError, ValueError):
                continue
            if tea._cache_index().get(tea._config_key(config)) is None:
                continue
            _EXACT_PLANNER_ROUTE = (composition, copy.deepcopy(route))
            return copy.deepcopy(composition), copy.deepcopy(route)
    raise AssertionError("no planner-emitted exact D-8 design-point route was reachable")


def _store_planner_route_handle(session, composition: dict, route: dict) -> str:
    return store_handle(
        session,
        tool="plan_multistage_separation",
        source_basis="cosmo_rs_grid",
        data={
            "success": True,
            "complete": bool(route.get("complete")),
            "steps": copy.deepcopy(route.get("steps") or []),
            "final_residue": route.get("final_residue"),
            "best_sequence": copy.deepcopy(route.get("sequence") or []),
            "feed_mass_fractions": dict(composition),
            "top_k_sequences": [copy.deepcopy(route)],
        },
    )


def _route_parity(payload: dict) -> dict:
    stages = payload.get("stage_results") or []
    return {
        "success": payload.get("success"),
        "error_code": payload.get("error_code"),
        "analysis_type": payload.get("analysis_type"),
        "engine_mode": payload.get("engine_mode"),
        "cache_match_status": payload.get("cache_match_status"),
        "stage_count": len(stages) if isinstance(stages, list) else None,
        "stage_labels": [
            str(row.get("label") or "") for row in stages if isinstance(row, dict)
        ],
        "stage_success": [
            row.get("success") for row in stages if isinstance(row, dict)
        ],
        "polymers": [
            str(row.get("target_plastic") or row.get("polymer") or "")
            for row in stages if isinstance(row, dict)
        ],
    }


def test_route_unknown_handle_is_named(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        payload = _data(tea.evaluate_process(
            mode="route",
            handle="no-such-handle",
            engine_mode="cache",
        ))
    assert payload.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"


def test_route_evaluate_handle_is_not_a_route(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_process(
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            engine_mode="cache",
        ))
    assert payload.get("error_code") == "not_route_handle"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"


def test_route_mode_matches_old_name_on_planner_route(monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        wrapped = _data(tea.evaluate_process(
            mode=" ROUTE ",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        ))
        assert session.get("last_route") is None
        assert getattr(session, "last_route", None) is None
    monkeypatch.setattr(
        tea,
        "current_tool_session",
        lambda: SimpleNamespace(
            last_route=copy.deepcopy(route),
            feed_mass_fractions=dict(composition),
        ),
    )
    direct = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(composition),
        processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
        energy_case=_ROUTE_ENERGY,
        precipitation_temperature_c=_ROUTE_PRECIP,
        engine_mode="cache",
    ))
    assert wrapped.get("tool_name") == "evaluate_process"
    assert direct.get("tool_name") == "evaluate_stored_route_tea_lca"
    assert wrapped.get("success") is True
    assert wrapped.get("error_code") != "tool_not_wired"
    assert wrapped.get("route_source") == "typed_session_state"
    assert len(wrapped.get("comparison_rows") or []) == len(wrapped.get("stage_results") or [])
    assert _route_parity(wrapped) == _route_parity(direct)


def test_dispatch_route_mode_issues_handle(monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        out = dispatch(
            "evaluate_process",
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        )
        assert out.get("available") is True
        assert out.get("handle")
        assert out.get("handle") != handle
        stored = load_handle(session, out["handle"])
        assert stored.get("tool") == "evaluate_process"
        assert stored["exact"]["tool_name"] == "evaluate_process"
        assert "stage_results" in stored["exact"]
        assert "comparison_rows" in stored["exact"]
        assert len(stored["exact"]["comparison_rows"]) == len(
            stored["exact"]["stage_results"]
        )
        old = dispatch("evaluate_stored_route_tea_lca")
        assert old.get("available") is False
        assert old.get("refusal") == "unknown_tool"


def test_route_mode_row_id_costs_a_real_handle(monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = store_handle(
            session,
            tool="plan_multistage_separation",
            source_basis="cosmo_rs_grid",
            data={
                "success": True,
                "complete": bool(route.get("complete")),
                "steps": copy.deepcopy(route.get("steps") or []),
                "final_residue": route.get("final_residue"),
                "best_sequence": copy.deepcopy(route.get("sequence") or []),
                "feed_mass_fractions": dict(composition),
                "top_k_sequences": [
                    copy.deepcopy(route),
                    copy.deepcopy(route),
                ],
            },
        )
        missing = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        ))
        assert missing.get("error_code") == "ambiguous_handle_row"
        assert missing.get("error_code") != "tool_not_wired"
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            row_id=1,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        ))
        assert session.get("last_route") is None
        assert getattr(session, "last_route", None) is None
    assert payload.get("success") is True
    assert payload.get("error_code") != "unknown_handle"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"
    stages = payload.get("stage_results") or []
    assert stages
    step = route["steps"][0]
    assert _key_polymer(stages[0].get("target_polymer") or stages[0].get("polymer")) == (
        _key_polymer(step["dissolved_polymer"])
    )
    assert len(payload.get("comparison_rows") or []) == len(stages)


def test_route_mode_stage_identity_mismatch(monkeypatch):
    composition, route = _exact_planner_route()
    polymer = str(route["steps"][0]["dissolved_polymer"])
    _forbid_live(monkeypatch)
    real_run = tea._run

    def planted(config, engine_mode, timeout_seconds):
        result = real_run(config, engine_mode, timeout_seconds)
        if result.get("success") is not True:
            return result
        planted_result = dict(result)
        planted_config = dict(result.get("config") or {})
        planted_config["target_plastic"] = "PE"
        planted_result["config"] = planted_config
        planted_result["target_plastic"] = "PE"
        planted_result["target_polymer"] = "PE"
        return planted_result

    monkeypatch.setattr(tea, "_run", planted)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            row_id=1,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "stage_identity_mismatch"
    assert payload.get("dissolved_polymer") == polymer
    assert payload.get("executed_target_polymer") == "PE"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"


def _key_polymer(value):
    return " ".join(str(value or "").strip().casefold().split())
