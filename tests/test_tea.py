"""Tea tests."""
from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from dissolve import (
    agent,
    separation,
    tea,
    tea_ranking,
    tea_worker,
    thermodynamics,
)
from dissolve import tea_polymer_parameters as params
from dissolve import tea_ranking as O
from dissolve.agent import CONSUMERS, UNWIRED, dispatch, tool_schemas
from dissolve.cli import EXPECTED_REGISTRY_NAMES, CliApp, doctor_report
from dissolve.contracts import parse_tool_result
from dissolve.session import (
    bind_tool_session,
    current_tool_session,
    handle_rows,
    load_handle,
    new_session,
    store_handle,
)


_LIVE_TEA_BLOCKER = tea._live_tea_blocker  # the real check, for the tests of it


@pytest.fixture(autouse=True)
def _live_tea_works(monkeypatch, tmp_path):
    """TEA answers only when live TEA works; these tests stand in a working engine (tests of the check itself
    override this) and never see this checkout's own live environment (.venv-tea, vendor/plastics)."""
    monkeypatch.setattr(tea, "_live_tea_blocker", lambda: None)
    monkeypatch.setattr(tea, "_REPO_TEA_PYTHON", tmp_path / "no-venv-tea" / "python")
    monkeypatch.setattr(tea.tea_polymer_parameters, "VENDORED_PLASTICS", tmp_path / "no-vendored-plastics")

# --- from test_evaluate_process.py: evaluate_process lookup, evaluate, sensitivity, and route.
_SEALED = tea_ranking.SHIPPED_CAMPAIGN


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


def _live_run_fails(monkeypatch, error_type="timeout"):
    """A confirmed live run that produces no result: its row fails and quotes nothing."""
    monkeypatch.setattr(tea, "_live", lambda config, timeout_seconds: {
        "success": False, "error_type": error_type, "error": "the live TEA run produced no result",
    })


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
    assert "evaluate_process" in agent.BY_NAME
    assert "evaluate_process" in EXPECTED_REGISTRY_NAMES
    assert "lookup_admitted_process_records" not in agent.BY_NAME
    assert "lookup_admitted_process_records" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.lookup_admitted_process_records)
    assert "evaluate_tea_lca_scenarios" not in agent.BY_NAME
    assert "evaluate_tea_lca_scenarios" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.evaluate_tea_lca_scenarios)
    assert "analyze_tea_sensitivity" not in agent.BY_NAME
    assert "analyze_tea_sensitivity" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.analyze_tea_sensitivity)
    assert "evaluate_stored_route_tea_lca" not in agent.BY_NAME
    assert "evaluate_stored_route_tea_lca" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea.evaluate_stored_route_tea_lca)
    assert "evaluate_stored_route_tea_lca" not in UNWIRED
    assert "optimize_stored_route" not in agent.BY_NAME
    assert "optimize_stored_route" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea_ranking.optimize_stored_route)
    assert "optimize_stored_route" not in UNWIRED
    assert "pareto_optimize_stored_route" not in agent.BY_NAME
    assert "pareto_optimize_stored_route" not in EXPECTED_REGISTRY_NAMES
    assert callable(tea_ranking.pareto_optimize_stored_route)
    assert "pareto_optimize_stored_route" not in UNWIRED
    assert UNWIRED == frozenset()
    assert "evaluate_process" not in UNWIRED
    assert "evaluate_process" in tea.PROCESS_CONFIRM_TOOLS
    assert "evaluate_tea_lca_scenarios" not in tea.PROCESS_CONFIRM_TOOLS
    assert "analyze_tea_sensitivity" not in tea.PROCESS_CONFIRM_TOOLS
    assert len(EXPECTED_REGISTRY_NAMES) == 31
    assert len(agent.REGISTRY) == 31
    assert "fetch_solvent_safety_by_cid" in EXPECTED_REGISTRY_NAMES
    assert "estimate_thermal_properties" not in EXPECTED_REGISTRY_NAMES
    assert "estimate_thermal_properties" not in agent.BY_NAME
    assert "fetch_solvent_safety_by_cid" in agent.BY_NAME
    retired = dispatch("evaluate_tea_lca_scenarios")
    assert retired.get("available") is False
    assert retired.get("refusal") == "unknown_tool"
    retired_sensitivity = dispatch("analyze_tea_sensitivity")
    assert retired_sensitivity.get("available") is False
    assert retired_sensitivity.get("refusal") == "unknown_tool"
    retired_route = dispatch("evaluate_stored_route_tea_lca")
    assert retired_route.get("available") is False
    assert retired_route.get("refusal") == "unknown_tool"
    retired_optimize = dispatch("optimize_stored_route")
    assert retired_optimize.get("available") is False
    assert retired_optimize.get("refusal") == "unknown_tool"
    retired_pareto = dispatch("pareto_optimize_stored_route")
    assert retired_pareto.get("available") is False
    assert retired_pareto.get("refusal") == "unknown_tool"


def test_schema_uses_two_typed_objects_not_top_level_polymer():
    props = _schema("evaluate_process")["parameters"]["properties"]
    assert "mode" in props
    assert "lookup_filter" in props
    assert "process_config" in props
    assert "process_configs" in props
    assert "screening_shortlist" in props
    assert "held_process_basis" in props
    assert "confirm_live_tea" in props
    assert props["lookup_filter"]["type"] == "object"
    assert props["process_config"]["type"] == "object"
    assert props["process_configs"]["type"] == "array"
    assert props["process_configs"]["items"]["type"] == "object"
    assert props["screening_shortlist"]["type"] == "object"
    assert props["held_process_basis"]["type"] == "object"
    assert props["confirm_live_tea"]["type"] == "boolean"
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
    assert "confirm_live_tea" not in required


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
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry_path))
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
    payload = _data(tea.evaluate_process(mode="evaluate", engine_mode="auto"))
    assert payload.get("success") is False
    assert payload.get("error_code") == "missing_scenarios"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"
    dispatched = dispatch("evaluate_process", mode="evaluate", engine_mode="auto")
    assert dispatched.get("available") is False
    assert dispatched.get("refusal") == "missing_scenarios"


def test_evaluate_mode_singular_config_matches_old_name(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    twelve = _public_from_record(record)
    direct = _data(tea.evaluate_tea_lca_scenarios(
        [twelve], engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="auto",
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
        engine_mode="auto",
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
    direct = _data(tea.evaluate_tea_lca_scenarios(batch, engine_mode="auto"))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=batch,
        engine_mode="auto",
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
        [partial], engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=partial,
        engine_mode="auto",
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
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])


_TRANSCRIPT_PROCESS_CONFIG = {
    "polymer": "LDPE",
    "solvent": "Dodecane",
    "temperature_c": 105.0,
    "solubility_pct": 14.54225715,
}


_FOUR_PUBLIC_NAMES = {
    "target_polymer": "LDPE",
    "solvent": "Dodecane",
    "dissolution_temperature_c": 105.0,
    "dissolution_capacity": 14.54225715,
}


def _ingest_must_not_run(*_args, **_kwargs):
    raise AssertionError("T1 must refuse before seed, default, or evaluate")


def test_evaluate_mode_transcript_dict_refuses_unknown_keys_at_ingest(monkeypatch):
    _forbid_live(monkeypatch)
    monkeypatch.setattr(tea, "seed_public_process_config", _ingest_must_not_run)
    monkeypatch.setattr(tea, "_scenario_config", _ingest_must_not_run)
    monkeypatch.setattr(tea, "evaluate_tea_lca_scenarios", _ingest_must_not_run)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=dict(_TRANSCRIPT_PROCESS_CONFIG),
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == [
        "polymer", "solubility_pct", "temperature_c",
    ]
    assert payload.get("tool_name") == "evaluate_process"
    assert "msp_usd_per_kg" not in payload
    assert "comparison_rows" not in payload


def test_evaluate_mode_process_configs_transcript_item_refuses(monkeypatch):
    _forbid_live(monkeypatch)
    monkeypatch.setattr(tea, "evaluate_tea_lca_scenarios", _ingest_must_not_run)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[dict(_TRANSCRIPT_PROCESS_CONFIG)],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == [
        "polymer", "solubility_pct", "temperature_c",
    ]


def test_evaluate_mode_real_public_names_do_not_refuse_on_field_names(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=dict(_FOUR_PUBLIC_NAMES),
        engine_mode="auto",
    ))
    assert payload.get("error_code") != "unknown_process_field"
    assert payload.get("error_code") == "incomplete_process_config"
    assert "solvent_price_usd_per_kg" in list(payload.get("missing") or [])


def test_seed_refuses_transcript_keys_instead_of_swallowing():
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea.seed_public_process_config(dict(_TRANSCRIPT_PROCESS_CONFIG))
    assert caught.value.error_code == "unknown_process_field"
    assert caught.value.details["extra_keys"] == [
        "polymer", "solubility_pct", "temperature_c",
    ]


def test_seed_accepts_real_public_and_internal_names():
    seeded = tea.seed_public_process_config(dict(_FOUR_PUBLIC_NAMES))
    assert seeded["target_polymer"] == "LDPE"
    assert seeded["dissolution_capacity"] == pytest.approx(14.54225715)
    internal = tea.seed_public_process_config({
        "target_plastic": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temp_c": 105.0,
        "dissolution_capacity": 14.54225715,
    })
    assert internal["target_polymer"] == "LDPE"
    assert internal["dissolution_temperature_c"] == pytest.approx(105.0)


def test_evaluate_mode_dual_key_conflict_refuses(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    monkeypatch.setattr(tea, "evaluate_tea_lca_scenarios", _ingest_must_not_run)
    config = _public_from_record(record, target_plastic="HDPE")
    config["target_polymer"] = "LDPE"
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=config,
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "conflicting_process_field"
    collisions = payload.get("collisions") or []
    assert collisions
    assert collisions[0]["public"] == "target_polymer"


def test_evaluate_mode_agreeing_dual_keys_are_not_a_field_name_refusal(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    config = _public_from_record(record)
    config["target_plastic"] = config["target_polymer"]
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=config,
        engine_mode="auto",
    ))
    assert payload.get("error_code") != "unknown_process_field"
    assert payload.get("error_code") != "conflicting_process_field"


def test_evaluate_mode_allowed_extras_are_not_unknown(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config={
            **_FOUR_PUBLIC_NAMES,
            "label": "probe",
            "lca_cfs": {"natural_gas_gwp": 1.0},
            "facilities": (),
            "turbogenerator": (),
        },
        engine_mode="auto",
    ))
    assert payload.get("error_code") != "unknown_process_field"
    assert payload.get("error_code") == "incomplete_process_config"


def test_evaluate_mode_screening_temperature_c_still_maps(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    shortlist = _shortlist(_item_from_record(record))
    item = dict(shortlist["items"][0])
    item["temperature_c"] = item.pop("dissolution_temperature_c")
    shortlist = _shortlist(item)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=shortlist,
        held_process_basis=_held_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("error_code") != "unknown_process_field"


def test_omitting_dissolution_capacity_lists_it_defaulted_at_3(monkeypatch):
    _forbid_live(monkeypatch)
    config = {
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 105.0,
    }
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=config,
        engine_mode="auto",
    ))
    assert payload.get("error_code") != "unknown_process_field"
    assert payload.get("silently_defaulted", {}).get("dissolution_capacity") == 3.0
    assert payload.get("field_origin", {}).get("dissolution_capacity") == "default"
    assert "msp_usd_per_kg" not in payload
    assert "comparison_rows" not in payload


def test_named_dissolution_capacity_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=dict(_FOUR_PUBLIC_NAMES),
        engine_mode="auto",
    ))
    silent = payload.get("silently_defaulted") or {}
    assert "dissolution_capacity" not in silent
    assert payload.get("field_origin", {}).get("dissolution_capacity") == "supplied"
    assert len(silent) == 40


def test_transcript_dict_does_not_reach_silent_defaults(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=dict(_TRANSCRIPT_PROCESS_CONFIG),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "silently_defaulted" not in payload


def test_complete_twelve_success_lists_unnamed_first_run_defaults(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    config = _public_from_record(record)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=config,
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    silent = payload.get("silently_defaulted") or {}
    origin = payload.get("field_origin") or {}
    assert "dissolution_capacity" not in silent
    assert origin.get("dissolution_capacity") == "supplied"
    assert origin.get("dissolution_capacity") != "default"
    assert silent.get("labor_burden") == tea.first_run_sheet_defaults()["labor_burden"]
    assert origin.get("labor_burden") == "default"
    assert len(silent) == 33


def test_complete_twelve_omitting_capacity_lists_default_3_without_msp(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    config = _public_from_record(record)
    config.pop("dissolution_capacity")
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=config,
        engine_mode="auto",
    ))
    assert payload.get("success") is not True
    assert payload.get("silently_defaulted", {}).get("dissolution_capacity") == 3.0
    assert payload.get("field_origin", {}).get("dissolution_capacity") == "default"
    assert "msp_usd_per_kg" not in payload


def test_capacity_alias_is_named_not_defaulted(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 105.0,
            "dissolution_capacity": 14.54225715,
        },
        engine_mode="auto",
    ))
    assert "dissolution_capacity" not in (payload.get("silently_defaulted") or {})


def test_handoff_from_screen_is_not_overwritten_by_default(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(_item_from_record(record)),
        held_process_basis=_held_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    origin = payload["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    assert "silently_defaulted" not in payload
    basis = payload.get("process_basis") or []
    assert len(basis) == 46
    top = payload.get("field_origin") or {}
    assert set(top) >= set(basis)
    assert top["target_polymer"] == "from_screen"
    assert top.get("lang_factor") == "default"


def test_complete_twelve_success_quotes_the_46_field_c1_basis(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    basis = payload.get("process_basis") or []
    origin = payload.get("field_origin") or {}
    assert basis == list(tea.evaluate_process_basis_names(energy_case="C1"))
    assert len(basis) == 46
    assert set(origin) >= set(basis)
    assert origin.get("lang_factor") == "default"
    assert origin.get("solvent_price_usd_per_kg") == "supplied"
    assert payload["comparison_rows"][0].get("msp_usd_per_kg") is not None


def test_c2_success_quotes_the_44_field_basis(monkeypatch):
    record = _record_with_energy("C2")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    basis = payload.get("process_basis") or []
    origin = payload.get("field_origin") or {}
    assert len(basis) == 44
    assert "natural_gas_price_usd_per_m3" not in basis
    assert set(origin) >= set(basis)
    assert payload["comparison_rows"][0].get("msp_usd_per_kg") is not None


def test_four_field_payload_has_no_msp(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=dict(_FOUR_PUBLIC_NAMES),
        engine_mode="auto",
    ))
    assert payload.get("success") is not True
    assert "msp_usd_per_kg" not in payload
    assert payload.get("lowest_msp_scenario") is None
    assert "solvent_price_usd_per_kg" not in (payload.get("field_origin") or {})
    assert len(payload.get("process_basis") or []) == 46


def test_confirmation_abort_shape_has_no_msp():
    abort = {
        "success": False,
        "error": "Process confirmation aborted; the model args did not run.",
        "error_code": "process_confirmation_aborted",
    }
    assert "msp_usd_per_kg" not in abort
    assert "comparison_rows" not in abort


def test_evaluate_mode_parameter_is_not_applicable(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        parameter="solvent_price",
        engine_mode="auto",
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
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "too_many_scenarios"
    assert payload.get("tool_name") == "evaluate_process"


def test_evaluate_mode_shortlist_without_held_lists_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    shortlist = _shortlist(_item_from_record(record))
    direct = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=shortlist, engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=shortlist,
        engine_mode="auto",
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
        engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=shortlist,
        held_process_basis=held,
        engine_mode="auto",
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
        engine_mode="auto",
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
        engine_mode="auto",
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
            engine_mode="auto",
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
        engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
        ))
        direct = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            engine_mode="auto",
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
            engine_mode="auto",
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
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["process_configs"]
    assert payload.get("applicable_mode") == "evaluate"


def test_sensitivity_mode_solvent_price_matches_old_name(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    twelve = _public_from_record(record)
    direct = _data(tea.analyze_tea_sensitivity(
        twelve, parameter="solvent_price", engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=twelve,
        parameter="solvent_price",
        engine_mode="auto",
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
        partial, parameter="solvent_price", engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=partial,
        parameter="solvent_price",
        engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
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
    result = _data(agent.BY_NAME["plan_multistage_separation"].fn(
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
            engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
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
        engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
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
            engine_mode="auto",
        ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "stage_identity_mismatch"
    assert payload.get("dissolved_polymer") == polymer
    assert payload.get("executed_target_polymer") == "PE"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "evaluate_process"


def _key_polymer(value):
    return " ".join(str(value or "").strip().casefold().split())


# --- from test_tea_design_point.py: Fast D-8 regression coverage; the external gate remains the admission bar.
TARGET_FRACTION = 0.55


EQUAL_FRACTION = 0.50


CAPACITY = 20_000.0


ENERGY_CASE = "C1"


PRECIPITATION_C = 25.0


@dataclass(frozen=True)
class RouteProbe:
    feed: tuple[str, str]
    composition: dict[str, float]
    route: dict


def _plan(feed: tuple[str, str], composition: dict[str, float]) -> list[dict]:
    result = _data(agent.BY_NAME["plan_multistage_separation"].fn(
        feed_polymers=list(feed),
        feed_mass_fractions=dict(composition),
        top_k_routes=10,
    ))
    assert result.get("success") is True
    routes = list(result.get("top_k_sequences") or [])
    assert routes, "public planner published no routes"
    return routes


def _route_key(route: dict) -> tuple:
    return (
        tuple(route.get("sequence") or []),
        tuple(
            (
                step.get("dissolved_polymer"),
                step.get("solvent"),
                float(step.get("temperature_c")),
            )
            for step in route.get("steps") or []
        ),
        route.get("final_residue"),
    )


def _stage_config(route: dict, target: str, fraction: float) -> dict:
    step = route["steps"][0]
    scenario = {
        "target_polymer": target,
        "solvent": step["solvent"],
        "target_mass_percent": 100.0 * fraction,
        "processing_capacity_mt_per_yr": CAPACITY,
        "energy_case": ENERGY_CASE,
        "dissolution_temp_c": step["temperature_c"],
        "precipitation_temp_c": PRECIPITATION_C,
    }
    scenario.update(tea._stored_route_named_remainder(str(step["solvent"])))
    return tea._scenario_config(scenario)


def _same_pair_records(config: dict) -> list[dict]:
    target = str(config["target_plastic"]).casefold()
    solvent = thermodynamics.resolve_solvent(str(config["solvent"]))
    return [
        record for record in tea._records()
        if str(record["config"]["target_plastic"]).casefold() == target
        and thermodynamics.resolve_solvent(
            str(record["config"]["solvent"])
        ) == solvent
    ]


@pytest.fixture(scope="module")
def exact_probe() -> tuple[RouteProbe, RouteProbe]:
    targets = sorted({
        str(record["config"]["target_plastic"])
        for record in tea._records()
        if math.isclose(
            float(record["config"]["target_plastic_percent"]),
            100.0 * TARGET_FRACTION,
            rel_tol=0,
            abs_tol=1e-9,
        )
        and math.isclose(
            float(record["config"]["processing_capacity"]),
            CAPACITY,
            rel_tol=0,
            abs_tol=1e-9,
        )
        and str(record["config"]["energy_case"]).upper() == ENERGY_CASE
        and math.isclose(
            float(record["config"]["precipitation_temperature_c"]),
            PRECIPITATION_C,
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
                target: TARGET_FRACTION,
                residue: 1.0 - TARGET_FRACTION,
            }
            route = _one_step_route(_plan(feed, composition), target, residue)
            if route is None:
                continue
            try:
                config = _stage_config(route, target, TARGET_FRACTION)
            except (TypeError, ValueError):
                continue
            if tea._cache_index().get(tea._config_key(config)) is None:
                continue

            equal = {target: EQUAL_FRACTION, residue: EQUAL_FRACTION}
            wanted = _route_key(route)
            equal_route = next((
                item for item in _plan(feed, equal)
                if _route_key(item) == wanted
            ), None)
            assert equal_route is not None, (
                "the exact route was not emitted by the public planner under "
                "the equal-weight counterfactual"
            )
            return (
                RouteProbe(feed, composition, copy.deepcopy(route)),
                RouteProbe(feed, equal, copy.deepcopy(equal_route)),
            )
    pytest.fail("no planner-emitted exact D-8 design-point route was reachable")


@pytest.fixture(scope="module")
def no_pair_probe() -> RouteProbe:
    recorded_targets = {
        str(record["config"]["target_plastic"])
        for record in tea._records()
    }
    polymers = sorted(thermodynamics.get_available_polymers())
    for target in (polymer for polymer in polymers if polymer not in recorded_targets):
        for residue in polymers:
            if residue == target:
                continue
            feed = tuple(sorted((target, residue)))
            composition = {
                target: TARGET_FRACTION,
                residue: 1.0 - TARGET_FRACTION,
            }
            route = _one_step_route(_plan(feed, composition), target, residue)
            if route is None:
                continue
            try:
                config = _stage_config(route, target, TARGET_FRACTION)
                key = tea._config_key(config)
            except (KeyError, TypeError, ValueError):
                continue
            if tea._cache_index().get(key) is not None:
                continue
            if _same_pair_records(config):
                continue
            return RouteProbe(feed, composition, copy.deepcopy(route))
    pytest.fail(
        "no planner-emitted complete-config miss without pair records was reachable"
    )


def _evaluate(monkeypatch, probe: RouteProbe, **overrides) -> dict:
    calls = 0

    def current_state():
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            last_route=copy.deepcopy(probe.route),
            feed_mass_fractions=dict(probe.composition),
        )

    monkeypatch.setattr(tea, "current_tool_session", current_state)
    result = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(probe.composition),
        processing_capacity_mt_per_yr=CAPACITY,
        energy_case=ENERGY_CASE,
        precipitation_temperature_c=PRECIPITATION_C,
        engine_mode="auto",
        allow_screening_estimate=False,
        **overrides,
    ))
    assert calls > 0, "stored-route TEA did not consume the supplied session route"
    return result


def test_d8_planner_route_exact_design_point_still_costs(
    monkeypatch, exact_probe,
):
    exact, _equal = exact_probe
    result = _evaluate(monkeypatch, exact)

    assert result.get("success") is True
    assert result.get("engine_mode") == "cache"
    assert result.get("cache_match_status") == "exact"
    stages = list(result.get("stage_results") or [])
    assert len(stages) == 1


def test_d8_equal_weight_route_refuses_reference_design_point_substitution(
    monkeypatch, exact_probe,
):
    _exact, equal = exact_probe
    unconfirmed = _evaluate(monkeypatch, equal)
    assert unconfirmed.get("error_code") == "live_tea_cost_confirmation_required"
    assert unconfirmed.get("n_live") == 1
    _live_run_fails(monkeypatch, "python_version")
    result = _evaluate(monkeypatch, equal, confirm_live_tea=True)

    assert result.get("success") is False
    assert result.get("error_code") == "route_stage_design_point_unavailable"
    assert result.get("can_cost_route") is False
    assert result.get("can_rank_route") is False
    assert (
        result.get("reference_evidence_role")
        == "context_only_not_route_cost_or_ranking"
    )
    requested = result.get("requested_stage_basis") or {}
    assert requested.get("target_mass_percent") == pytest.approx(50.0)
    assert requested.get("processing_capacity_mt_per_yr") == pytest.approx(CAPACITY)
    assert result.get("available_reference_design_points")
    differences = list(result.get("basis_differences") or [])
    assert differences
    assert any(
        "target_mass_percent" in (item.get("differing_fields") or [])
        for item in differences
    )


def test_d8_no_pair_route_keeps_generic_process_basis_refusal(
    monkeypatch, no_pair_probe,
):
    contract_results = []
    reference_contract = tea._reference_design_point_contract

    def traced_reference_contract(config):
        result = reference_contract(config)
        contract_results.append((config, result))
        return result

    monkeypatch.setattr(
        tea, "_reference_design_point_contract", traced_reference_contract,
    )
    _live_run_fails(monkeypatch, "python_version")
    result = _evaluate(monkeypatch, no_pair_probe, confirm_live_tea=True)

    assert len(contract_results) == 1, (
        "the no-pair probe did not reach the complete-config reference split"
    )
    contract_config, contract_result = contract_results[0]
    assert contract_result is None
    target = str(no_pair_probe.route["steps"][0]["dissolved_polymer"])
    config = _stage_config(no_pair_probe.route, target, TARGET_FRACTION)
    assert tea._config_key(contract_config) == tea._config_key(config)
    assert tea._cache_index().get(tea._config_key(config)) is None
    assert _same_pair_records(config) == []
    assert result.get("success") is False
    assert result.get("error_code") == "uncostable_route_stage"
    for field in (
        "can_cost_route",
        "can_rank_route",
        "reference_evidence_role",
        "requested_stage_basis",
        "available_reference_design_points",
        "basis_differences",
    ):
        assert field not in result


# --- from test_tea_exposed_coefficients.py: Unreached @parameter baselines join the serve key once they are public.
def _data_tea_exposed_coefficients(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _public_from_record_tea_exposed_coefficients(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temp_c": cfg["dissolution_temperature_c"],
        "precipitation_temp_c": cfg["precipitation_temperature_c"],
        "solvent_price": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def test_omitted_and_explicit_coefficient_defaults_share_the_serve_key():
    record = _record_with_energy("C1")
    twelve = dict(record["config"])
    explicit = {
        **twelve,
        "irr": 0.10,
        "income_tax": 0.21,
        "operating_days": 350.4,
        "labor_burden": 0.90,
        "finance_interest": 0.08,
        "finance_years": 10,
        "finance_fraction": 0.0,
        "startup_months": 3,
        "startup_FOCfrac": 1,
        "startup_VOCfrac": 0.75,
        "startup_salesfrac": 0.5,
        "WC_over_FCI": 0.05,
        "warehouse": 0.04,
        "site_development": 0.09,
        "additional_piping": 0.045,
        "proratable_costs": 0.10,
        "field_expenses": 0.10,
        "construction": 0.20,
        "contingency": 0.4,
        "other_indirect_costs": 0.10,
        "property_insurance": 0.007,
        "maintenance": 0.03,
        "duration": (2025, 2055),
        "depreciation": "MACRS7",
        "construction_schedule": (0.08, 0.60, 0.32),
        "lang_factor": None,
        "feedstock_price_usd_per_kg": 0.01,
        "centrifuged_plastic_solvent_content_pct": 50.0,
        "natural_gas_price_usd_per_m3": tea._NATURAL_GAS_PRICE_USD_PER_M3,
        "steam_power_depreciation": "MACRS20",
    }
    assert tea._config_key(twelve) == tea._config_key(explicit)
    reconstructed = tea._scenario_config(_public_from_record_tea_exposed_coefficients(record))
    assert reconstructed["irr"] == pytest.approx(0.10)
    assert reconstructed["income_tax"] == pytest.approx(0.21)
    assert reconstructed["operating_days"] == pytest.approx(350.4)
    assert reconstructed["labor_burden"] == pytest.approx(0.90)
    assert reconstructed["finance_interest"] == pytest.approx(0.08)
    assert reconstructed["finance_years"] == pytest.approx(10)
    assert reconstructed["finance_fraction"] == pytest.approx(0.0)
    assert reconstructed["startup_months"] == pytest.approx(3)
    assert reconstructed["startup_FOCfrac"] == pytest.approx(1)
    assert reconstructed["startup_VOCfrac"] == pytest.approx(0.75)
    assert reconstructed["startup_salesfrac"] == pytest.approx(0.5)
    assert reconstructed["WC_over_FCI"] == pytest.approx(0.05)
    assert reconstructed["warehouse"] == pytest.approx(0.04)
    assert reconstructed["site_development"] == pytest.approx(0.09)
    assert reconstructed["additional_piping"] == pytest.approx(0.045)
    assert reconstructed["proratable_costs"] == pytest.approx(0.10)
    assert reconstructed["field_expenses"] == pytest.approx(0.10)
    assert reconstructed["construction"] == pytest.approx(0.20)
    assert reconstructed["contingency"] == pytest.approx(0.4)
    assert reconstructed["other_indirect_costs"] == pytest.approx(0.10)
    assert reconstructed["property_insurance"] == pytest.approx(0.007)
    assert reconstructed["maintenance"] == pytest.approx(0.03)
    assert reconstructed["duration"] == (2025, 2055)
    assert reconstructed["depreciation"] == "MACRS7"
    assert reconstructed["construction_schedule"] == (0.08, 0.60, 0.32)
    assert reconstructed["lang_factor"] is None
    assert reconstructed["feedstock_price_usd_per_kg"] == pytest.approx(0.01)
    assert reconstructed["centrifuged_plastic_solvent_content_pct"] == pytest.approx(50.0)
    assert reconstructed["natural_gas_price_usd_per_m3"] == pytest.approx(
        tea._NATURAL_GAS_PRICE_USD_PER_M3
    )
    assert reconstructed["steam_power_depreciation"] == "MACRS20"
    hit = tea._cache_index().get(tea._config_key(reconstructed))
    assert hit is not None
    assert hit["label"] == record["label"]


def test_c2_does_not_carry_natural_gas_price():
    record = _record_with_energy("C2")
    reconstructed = tea._scenario_config(_public_from_record_tea_exposed_coefficients(record))
    assert "natural_gas_price_usd_per_m3" not in reconstructed
    assert "steam_power_depreciation" not in reconstructed
    assert reconstructed["lang_factor"] is None
    assert tea._cache_index().get(tea._config_key(reconstructed))["label"] == (
        record["label"]
    )


@pytest.mark.parametrize(
    "field, value",
    [
        pytest.param("irr", 0.15, id="irr_override_does_not_share_the_twelve_only"),
        pytest.param("income_tax", 0.3, id="income_tax_override_does_not_share_the_twelve_only"),
        pytest.param("operating_days", 300.0, id="operating_days_override_does_not_share_the_twelve_only"),
        pytest.param("labor_burden", 0.5, id="labor_burden_override_does_not_share_the_twelve_only"),
        pytest.param("finance_interest", 0.12, id="finance_interest_override_does_not_share_the_twelve_only"),
        pytest.param("finance_years", 15, id="finance_years_override_does_not_share_the_twelve_only"),
        pytest.param("finance_fraction", 0.4, id="finance_fraction_override_does_not_share_the_twelve_only"),
        pytest.param("startup_months", 6, id="startup_months_override_does_not_share_the_twelve_only"),
        pytest.param("startup_FOCfrac", 0.5, id="startup_FOCfrac_override_does_not_share_the_twelve_only"),
        pytest.param("startup_VOCfrac", 0.5, id="startup_VOCfrac_override_does_not_share_the_twelve_only"),
        pytest.param("startup_salesfrac", 0.8, id="startup_salesfrac_override_does_not_share_the_twelve_only"),
        pytest.param("WC_over_FCI", 0.1, id="wc_over_fci_override_does_not_share_the_twelve_only"),
        pytest.param("warehouse", 0.08, id="warehouse_override_does_not_share_the_twelve_only"),
        pytest.param("site_development", 0.18, id="site_development_override_does_not_share_the_twelve_only"),
        pytest.param("additional_piping", 0.09, id="additional_piping_override_does_not_share_the_twelve_only"),
        pytest.param("proratable_costs", 0.2, id="proratable_costs_override_does_not_share_the_twelve_only"),
        pytest.param("field_expenses", 0.2, id="field_expenses_override_does_not_share_the_twelve_only"),
        pytest.param("construction", 0.4, id="construction_override_does_not_share_the_twelve_only"),
        pytest.param("contingency", 0.8, id="contingency_override_does_not_share_the_twelve_only"),
        pytest.param("other_indirect_costs", 0.2, id="other_indirect_costs_override_does_not_share_the_twelve_only"),
        pytest.param("property_insurance", 0.014, id="property_insurance_override_does_not_share_the_twelve_only"),
        pytest.param("maintenance", 0.06, id="maintenance_override_does_not_share_the_twelve_only"),
        pytest.param("depreciation", "MACRS5", id="depreciation_override_does_not_share_the_twelve_only"),
        pytest.param("steam_power_depreciation", "MACRS7", id="steam_power_depreciation_override_does_not_share_the"),
        pytest.param("depreciation", "MACRS07", id="macrs7"),
        pytest.param("steam_power_depreciation", "MACRS07", id="macrs20_on_steam_power"),
    ],
)
def test_override_does_not_share_the_serve_key(field, value):
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), field: value}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_stored_record_is_not_served_for_irr_override_instead_of_the_other_plant(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("coefficient mismatch must not fall through to live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, irr=0.15)],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    irr = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "irr"
    )
    assert irr["recorded_value"] == pytest.approx(0.10)
    assert irr["requested_value"] == pytest.approx(0.15)


def test_stored_record_is_not_served_for_income_tax_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("income_tax mismatch must not fall through to live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, income_tax=0.30)],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    tax = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "income_tax"
    )
    assert tax["recorded_value"] == pytest.approx(0.21)
    assert tax["requested_value"] == pytest.approx(0.30)


def test_cache_evaluate_echoes_projected_income_tax(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("default income_tax must hit the cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record)],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    row = payload["comparison_rows"][0]
    assert row["income_tax"] == pytest.approx(0.21)
    assert row["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


@pytest.mark.parametrize(
    "message, recorded, requested, field, other_field",
    [
        pytest.param(
            "operating_days mismatch must not fall through to live",
            350.4,
            300.0,
            "operating_days",
            "income_tax",
            id="operating_days",
        ),
        pytest.param(
            "labor_burden mismatch must not fall through to live",
            0.9,
            0.5,
            "labor_burden",
            "operating_days",
            id="labor_burden",
        ),
        pytest.param(
            "finance_interest mismatch must not fall through to live",
            0.08,
            0.12,
            "finance_interest",
            "labor_burden",
            id="finance_interest",
        ),
        pytest.param(
            "finance_years mismatch must not fall through to live",
            10,
            15,
            "finance_years",
            "finance_interest",
            id="finance_years",
        ),
        pytest.param(
            "finance_fraction mismatch must not fall through to live",
            0.0,
            0.4,
            "finance_fraction",
            "finance_years",
            id="finance_fraction",
        ),
        pytest.param(
            "startup_months mismatch must not fall through to live",
            3,
            6,
            "startup_months",
            "finance_fraction",
            id="startup_months",
        ),
        pytest.param(
            "startup_FOCfrac mismatch must not fall through to live",
            1,
            0.5,
            "startup_FOCfrac",
            "startup_months",
            id="startup_FOCfrac",
        ),
        pytest.param(
            "startup_VOCfrac mismatch must not fall through to live",
            0.75,
            0.5,
            "startup_VOCfrac",
            "startup_FOCfrac",
            id="startup_VOCfrac",
        ),
        pytest.param(
            "startup_salesfrac mismatch must not fall through to live",
            0.5,
            0.8,
            "startup_salesfrac",
            "startup_VOCfrac",
            id="startup_salesfrac",
        ),
        pytest.param(
            "WC_over_FCI mismatch must not fall through to live",
            0.05,
            0.1,
            "WC_over_FCI",
            "startup_salesfrac",
            id="wc_over_fci",
        ),
        pytest.param(
            "warehouse mismatch must not fall through to live", 0.04, 0.08, "warehouse", "WC_over_FCI", id="warehouse"
        ),
        pytest.param(
            "site_development mismatch must not fall through to live",
            0.09,
            0.18,
            "site_development",
            "warehouse",
            id="site_development",
        ),
        pytest.param(
            "additional_piping mismatch must not fall through to live",
            0.045,
            0.09,
            "additional_piping",
            "site_development",
            id="additional_piping",
        ),
        pytest.param(
            "proratable_costs mismatch must not fall through to live",
            0.1,
            0.2,
            "proratable_costs",
            "additional_piping",
            id="proratable_costs",
        ),
        pytest.param(
            "field_expenses mismatch must not fall through to live",
            0.1,
            0.2,
            "field_expenses",
            "proratable_costs",
            id="field_expenses",
        ),
        pytest.param(
            "construction mismatch must not fall through to live",
            0.2,
            0.4,
            "construction",
            "field_expenses",
            id="construction",
        ),
        pytest.param(
            "contingency mismatch must not fall through to live",
            0.4,
            0.8,
            "contingency",
            "construction",
            id="contingency",
        ),
        pytest.param(
            "other_indirect_costs mismatch must not fall through to live",
            0.1,
            0.2,
            "other_indirect_costs",
            "contingency",
            id="other_indirect_costs",
        ),
        pytest.param(
            "property_insurance mismatch must not fall through to live",
            0.007,
            0.014,
            "property_insurance",
            "other_indirect_costs",
            id="property_insurance",
        ),
        pytest.param(
            "maintenance mismatch must not fall through to live",
            0.03,
            0.06,
            "maintenance",
            "property_insurance",
            id="maintenance",
        ),
    ],
)
def test_stored_record_is_not_served_for_override_instead_of_the_other_plant(
    monkeypatch, message, recorded, requested, field, other_field
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(message)

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(
        tea.evaluate_tea_lca_scenarios(
            [_public_from_record_tea_exposed_coefficients(record, **{field: requested})], engine_mode="auto"
        )
    )
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    delta = next((item for item in row["flowsheet_switch_deltas"] if item["field"] == field))
    assert delta["recorded_value"] == pytest.approx(recorded)
    assert delta["requested_value"] == pytest.approx(requested)
    assert not any((item["field"] == other_field for item in row["flowsheet_switch_deltas"]))


def test_cache_evaluate_echoes_projected_operating_days(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("default operating_days must hit the cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record)],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    row = payload["comparison_rows"][0]
    assert row["operating_days"] == pytest.approx(350.4)
    assert row["income_tax"] == pytest.approx(0.21)
    assert row["labor_burden"] == pytest.approx(0.90)
    assert row["finance_interest"] == pytest.approx(0.08)
    assert row["finance_years"] == pytest.approx(10)
    assert row["finance_fraction"] == pytest.approx(0.0)
    assert row["startup_months"] == pytest.approx(3)
    assert row["startup_FOCfrac"] == pytest.approx(1)
    assert row["startup_VOCfrac"] == pytest.approx(0.75)
    assert row["startup_salesfrac"] == pytest.approx(0.5)
    assert row["WC_over_FCI"] == pytest.approx(0.05)
    assert row["warehouse"] == pytest.approx(0.04)
    assert row["site_development"] == pytest.approx(0.09)
    assert row["additional_piping"] == pytest.approx(0.045)
    assert row["proratable_costs"] == pytest.approx(0.10)
    assert row["field_expenses"] == pytest.approx(0.10)
    assert row["construction"] == pytest.approx(0.20)
    assert row["contingency"] == pytest.approx(0.4)
    assert row["other_indirect_costs"] == pytest.approx(0.10)
    assert row["property_insurance"] == pytest.approx(0.007)
    assert row["maintenance"] == pytest.approx(0.03)
    assert row["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


@pytest.mark.parametrize(
    "energy_case, error_code, field, value",
    [
        pytest.param("C1", "invalid_scenario", "operating_days", 0, id="nonpositive_operating_days_is_refused"),
        pytest.param("C1", "invalid_scenario", "finance_interest", 8, id="percent_integer_finance_interest_is_refused"),
        pytest.param(
            "C1", "invalid_scenario", "finance_fraction", 40, id="percent_integer_finance_fraction_is_refused"
        ),
        pytest.param("C1", "invalid_scenario", "startup_months", 13, id="startup_months_above_one_year_is_refused"),
        pytest.param("C1", "invalid_scenario", "startup_FOCfrac", 50, id="percent_integer_startup_FOCfrac_is_refused"),
        pytest.param("C1", "invalid_scenario", "startup_VOCfrac", 75, id="percent_integer_startup_VOCfrac_is_refused"),
        pytest.param(
            "C1", "invalid_scenario", "startup_salesfrac", 50, id="percent_integer_startup_salesfrac_is_refused"
        ),
        pytest.param("C1", "invalid_scenario", "WC_over_FCI", 5, id="percent_integer_wc_over_fci_is_refused"),
        pytest.param("C1", "invalid_scenario", "warehouse", 4, id="percent_integer_warehouse_is_refused"),
        pytest.param("C1", "invalid_scenario", "site_development", 9, id="percent_integer_site_development_is_refused"),
        pytest.param(
            "C1", "invalid_scenario", "additional_piping", 5, id="percent_integer_additional_piping_is_refused"
        ),
        pytest.param(
            "C1", "invalid_scenario", "proratable_costs", 10, id="percent_integer_proratable_costs_is_refused"
        ),
        pytest.param("C1", "invalid_scenario", "field_expenses", 10, id="percent_integer_field_expenses_is_refused"),
        pytest.param("C1", "invalid_scenario", "construction", 20, id="percent_integer_construction_is_refused"),
        pytest.param("C1", "invalid_scenario", "contingency", 40, id="percent_integer_contingency_is_refused"),
        pytest.param(
            "C1", "invalid_scenario", "other_indirect_costs", 10, id="percent_integer_other_indirect_costs_is_refused"
        ),
        pytest.param(
            "C1", "invalid_scenario", "property_insurance", 7, id="percent_integer_property_insurance_is_refused"
        ),
        pytest.param("C1", "invalid_scenario", "maintenance", 3, id="percent_integer_maintenance_is_refused"),
        pytest.param("C1", "invalid_scenario", "duration", 30, id="years_scalar_is_not_duration"),
        pytest.param("C1", "invalid_scenario", "income_tax", 21, id="percent_integer_income_tax_is_refused"),
        pytest.param("C1", "invalid_scenario", "finance_years", 0, id="nonpositive"),
        pytest.param("C1", "invalid_scenario", "finance_years", 10.5, id="noninteger"),
        pytest.param("C1", "invalid_scenario", "depreciation", "macrs7", id="lowercase_depreciation_is"),
        pytest.param("C1", "invalid_scenario", "depreciation", 7, id="integer_depreciation_years_are"),
        pytest.param("C1", "invalid_scenario", "depreciation", "MACRS4", id="unimplemented_macrs_years_are"),
        pytest.param(
            "C1", "invalid_scenario", "construction_schedule", 3, id="scalar_years_is_not_construction_schedule"
        ),
        pytest.param(
            "C1",
            "invalid_scenario",
            "construction_schedule",
            "0.08, 0.60, 0.32",
            id="string_construction_schedule_is_refused",
        ),
        pytest.param(
            "C2",
            "energy_case_contract",
            "steam_power_depreciation",
            "MACRS20",
            id="c2_steam_power_depreciation_is_energy_case_contract",
        ),
        pytest.param(
            "C1",
            "invalid_scenario",
            "steam_power_depreciation",
            "macrs20",
            id="lowercase_steam_power_depreciation_is_refused",
        ),
        pytest.param(
            "C1", "invalid_scenario", "steam_power_depreciation", 20, id="integer_steam_power_depreciation_is_refused"
        ),
        pytest.param(
            "C1",
            "invalid_scenario",
            "steam_power_depreciation",
            "MACRS4",
            id="unimplemented_steam_power_macrs_years_are_refused",
        ),
        pytest.param("C1", "invalid_scenario", "lang_factor", "3.0", id="string"),
        pytest.param("C1", "invalid_scenario", "lang_factor", True, id="true"),
    ],
)
def test_invalid_scenario_override_is_refused(energy_case, error_code, field, value):
    record = _record_with_energy(energy_case)
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, **{field: value}))
    assert caught.value.error_code == error_code
    assert caught.value.details["field"] == field


@pytest.mark.parametrize(
    "field, magnitude",
    [
        pytest.param("labor_burden", 0.1, id="labor_burden"),
        pytest.param("finance_interest", 0.01, id="finance_interest"),
        pytest.param("finance_fraction", 0.1, id="finance_fraction"),
        pytest.param("startup_months", 1, id="startup_months"),
        pytest.param("startup_FOCfrac", 0.1, id="startup_FOCfrac"),
        pytest.param("startup_VOCfrac", 0.1, id="startup_VOCfrac"),
        pytest.param("startup_salesfrac", 0.1, id="startup_salesfrac"),
        pytest.param("WC_over_FCI", 0.1, id="wc_over_fci"),
        pytest.param("warehouse", 0.1, id="warehouse"),
        pytest.param("site_development", 0.1, id="site_development"),
        pytest.param("additional_piping", 0.1, id="additional_piping"),
        pytest.param("proratable_costs", 0.1, id="proratable_costs"),
        pytest.param("field_expenses", 0.1, id="field_expenses"),
        pytest.param("construction", 0.1, id="construction"),
        pytest.param("contingency", 0.1, id="contingency"),
        pytest.param("other_indirect_costs", 0.1, id="other_indirect_costs"),
        pytest.param("property_insurance", 0.1, id="property_insurance"),
        pytest.param("maintenance", 0.1, id="maintenance"),
    ],
)
def test_negative_override_is_refused(field, magnitude):
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, **{field: -magnitude}))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == field


@pytest.mark.parametrize(
    "message, requested, field",
    [
        pytest.param(
            "finance_fraction=1.0 is 100 percent debt, not invalid_scenario",
            1.0,
            "finance_fraction",
            id="boundary_override_reaches_the_flowsheet_check-full_debt_finance_fraction",
        ),
        pytest.param(
            "startup_months=0 is a legal override, not invalid_scenario",
            0,
            "startup_months",
            id="boundary_override_reaches_the_flowsheet_check-zero_startup_months",
        ),
        pytest.param(
            "startup_FOCfrac=0 is a legal override, not invalid_scenario",
            0,
            "startup_FOCfrac",
            id="boundary_override_reaches_the_flowsheet_check-zero_startup_FOCfrac",
        ),
        pytest.param(
            "startup_VOCfrac=1.0 is 100 percent VOC, not invalid_scenario",
            1.0,
            "startup_VOCfrac",
            id="boundary_override_reaches_the_flowsheet_check-full_startup_VOCfrac",
        ),
        pytest.param(
            "startup_salesfrac=1.0 is 100 percent sales, not invalid_scenario",
            1.0,
            "startup_salesfrac",
            id="boundary_override_reaches_the_flowsheet_check-full_startup_salesfrac",
        ),
        pytest.param(
            "WC_over_FCI=0 is no working capital, not invalid_scenario",
            0,
            "WC_over_FCI",
            id="wc_over_fci_is_a_legal_override-zero",
        ),
        pytest.param(
            "WC_over_FCI=1.0 is 100 percent of FCI, not invalid_scenario",
            1.0,
            "WC_over_FCI",
            id="wc_over_fci_is_a_legal_override-full",
        ),
        pytest.param(
            "warehouse=0 is no warehouse factor, not invalid_scenario",
            0,
            "warehouse",
            id="warehouse_is_a_legal_override-zero",
        ),
        pytest.param(
            "warehouse=1.0 is 100 percent of ISBL DPI, not invalid_scenario",
            1.0,
            "warehouse",
            id="warehouse_is_a_legal_override-full",
        ),
        pytest.param(
            "site_development=0 is no site factor, not invalid_scenario",
            0,
            "site_development",
            id="site_development_is_a_legal_override-zero",
        ),
        pytest.param(
            "site_development=1.0 is 100 percent of ISBL DPI, not invalid_scenario",
            1.0,
            "site_development",
            id="site_development_is_a_legal_override-full",
        ),
        pytest.param(
            "additional_piping=0 is no piping factor, not invalid_scenario",
            0,
            "additional_piping",
            id="additional_piping_is_a_legal_override-zero",
        ),
        pytest.param(
            "additional_piping=1.0 is 100 percent of ISBL DPI, not invalid_scenario",
            1.0,
            "additional_piping",
            id="additional_piping_is_a_legal_override-full",
        ),
        pytest.param(
            "proratable_costs=0 is no proratable factor, not invalid_scenario",
            0,
            "proratable_costs",
            id="proratable_costs_is_a_legal_override-zero",
        ),
        pytest.param(
            "proratable_costs=1.0 is 100 percent of DPI, not invalid_scenario",
            1.0,
            "proratable_costs",
            id="proratable_costs_is_a_legal_override-full",
        ),
        pytest.param(
            "field_expenses=0 is no field factor, not invalid_scenario",
            0,
            "field_expenses",
            id="field_expenses_is_a_legal_override-zero",
        ),
        pytest.param(
            "field_expenses=1.0 is 100 percent of DPI, not invalid_scenario",
            1.0,
            "field_expenses",
            id="field_expenses_is_a_legal_override-full",
        ),
        pytest.param(
            "construction=0 is no construction factor, not invalid_scenario",
            0,
            "construction",
            id="construction_is_a_legal_override-zero",
        ),
        pytest.param(
            "construction=1.0 is 100 percent of DPI, not invalid_scenario",
            1.0,
            "construction",
            id="construction_is_a_legal_override-full",
        ),
        pytest.param(
            "contingency=0 is no contingency factor, not invalid_scenario",
            0,
            "contingency",
            id="contingency_is_a_legal_override-zero",
        ),
        pytest.param(
            "contingency=1.0 is 100 percent of DPI, not invalid_scenario",
            1.0,
            "contingency",
            id="contingency_is_a_legal_override-full",
        ),
        pytest.param(
            "other_indirect_costs=0 is no other-indirect factor, not invalid_scenario",
            0,
            "other_indirect_costs",
            id="other_indirect_costs_is_a_legal_override-zero",
        ),
        pytest.param(
            "other_indirect_costs=1.0 is 100 percent of DPI, not invalid_scenario",
            1.0,
            "other_indirect_costs",
            id="other_indirect_costs_is_a_legal_override-full",
        ),
        pytest.param(
            "property_insurance=0 is no insurance factor, not invalid_scenario",
            0,
            "property_insurance",
            id="property_insurance_is_a_legal_override-zero",
        ),
        pytest.param(
            "property_insurance=1.0 is 100 percent of FCI, not invalid_scenario",
            1.0,
            "property_insurance",
            id="property_insurance_is_a_legal_override-full",
        ),
        pytest.param(
            "maintenance=0 is no maintenance factor, not invalid_scenario",
            0,
            "maintenance",
            id="maintenance_is_a_legal_override-zero",
        ),
        pytest.param(
            "maintenance=1.0 is 100 percent of ISBL DPI, not invalid_scenario",
            1.0,
            "maintenance",
            id="maintenance_is_a_legal_override-full",
        ),
    ],
)
def test_boundary_override_reaches_the_flowsheet_check(monkeypatch, message, requested, field):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(message)

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(
        tea.evaluate_tea_lca_scenarios(
            [_public_from_record_tea_exposed_coefficients(record, **{field: requested})], engine_mode="auto"
        )
    )
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    delta = next((item for item in row["flowsheet_switch_deltas"] if item["field"] == field))
    assert delta["requested_value"] == pytest.approx(requested)


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        pytest.param('depreciation', 'MACRS7', id='explicit_default_depreciation_shares_the_serve_key'),
        pytest.param('steam_power_depreciation', 'MACRS20', id='steam_power'),
    ],
)
def test_explicit_default_depreciation_shares_the_serve_key_2(key, value):
    record = _record_with_energy("C1")
    explicit = {**dict(record["config"]), key: value}
    assert tea._config_key(record["config"]) == tea._config_key(explicit)
    assert tea._cache_index().get(tea._config_key(explicit))["label"] == (
        record["label"]
    )


def test_stored_record_is_not_served_for_depreciation_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "depreciation mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, depreciation="MACRS5")],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    schedule = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "depreciation"
    )
    assert schedule["recorded_value"] == "MACRS7"
    assert schedule["requested_value"] == "MACRS5"
    assert not any(
        item["field"] == "maintenance"
        for item in row["flowsheet_switch_deltas"]
    )


def test_macrs20_is_plant_depreciation_not_steam_power(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "MACRS20 is plant TEA depreciation, not steam_power_depreciation"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, depreciation="MACRS20")],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    schedule = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "depreciation"
    )
    assert schedule["recorded_value"] == "MACRS7"
    assert schedule["requested_value"] == "MACRS20"
    assert not any(
        item["field"] == "steam_power_depreciation"
        for item in row["flowsheet_switch_deltas"]
    )


def test_maintenance_override_does_not_emit_a_depreciation_delta(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "maintenance mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, maintenance=0.06)],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    fraction = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "maintenance"
    )
    assert fraction["requested_value"] == pytest.approx(0.06)
    assert not any(
        item["field"] == "depreciation"
        for item in row["flowsheet_switch_deltas"]
    )


def test_array_depreciation_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(
            _public_from_record_tea_exposed_coefficients(record, depreciation=["MACRS7"]),
        )
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "depreciation"


def test_explicit_default_duration_shares_the_serve_key():
    record = _record_with_energy("C1")
    as_tuple = {**dict(record["config"]), "duration": (2025, 2055)}
    as_list = {**dict(record["config"]), "duration": [2025, 2055]}
    assert tea._config_key(record["config"]) == tea._config_key(as_tuple)
    assert tea._config_key(record["config"]) == tea._config_key(as_list)
    assert tea._cache_index().get(tea._config_key(as_list))["label"] == (
        record["label"]
    )


@pytest.mark.parametrize(
    ('key', 'value', 'value_2'),
    [
        pytest.param('duration', 2025, 2045, id='duration_override_does_not_share_the_twelve_only'),
        pytest.param('construction_schedule', 0.5, 0.5, id='construction_schedule_override_does_not_share_the'),
    ],
)
def test_serve_key_2(key, value, value_2):
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), key: (value, value_2)}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_stored_record_is_not_served_for_duration_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "duration mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, duration=[2025, 2045])],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    pair = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "duration"
    )
    assert pair["recorded_value"] == [2025, 2055]
    assert pair["requested_value"] == [2025, 2045]
    assert not any(
        item["field"] == "depreciation"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "construction_schedule"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "lang_factor"
        for item in row["flowsheet_switch_deltas"]
    )


def test_depreciation_override_does_not_emit_a_duration_delta(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "depreciation mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, depreciation="MACRS5")],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    schedule = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "depreciation"
    )
    assert schedule["requested_value"] == "MACRS5"
    assert not any(
        item["field"] == "duration"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "construction_schedule"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "lang_factor"
        for item in row["flowsheet_switch_deltas"]
    )


def test_c2_still_carries_duration():
    record = _record_with_energy("C2")
    reconstructed = tea._scenario_config(_public_from_record_tea_exposed_coefficients(record))
    assert reconstructed["duration"] == (2025, 2055)
    assert tea._cache_index().get(tea._config_key(reconstructed))["label"] == (
        record["label"]
    )


def test_three_year_duration_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(
            _public_from_record_tea_exposed_coefficients(record, duration=(2025, 2040, 2055)),
        )
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "duration"


def test_inverted_duration_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(
            _public_from_record_tea_exposed_coefficients(record, duration=(2055, 2025)),
        )
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "duration"


def test_explicit_default_construction_schedule_shares_the_serve_key():
    record = _record_with_energy("C1")
    as_tuple = {
        **dict(record["config"]),
        "construction_schedule": (0.08, 0.60, 0.32),
    }
    as_list = {
        **dict(record["config"]),
        "construction_schedule": [0.08, 0.60, 0.32],
    }
    assert tea._config_key(record["config"]) == tea._config_key(as_tuple)
    assert tea._config_key(record["config"]) == tea._config_key(as_list)
    assert tea._cache_index().get(tea._config_key(as_list))["label"] == (
        record["label"]
    )


def test_stored_record_is_not_served_for_construction_schedule_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "construction_schedule mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, construction_schedule=[0.5, 0.5])],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    pair = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "construction_schedule"
    )
    assert pair["recorded_value"] == [0.08, 0.60, 0.32]
    assert pair["requested_value"] == [0.5, 0.5]
    assert not any(
        item["field"] == "duration"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "depreciation"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "steam_power_depreciation"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "lang_factor"
        for item in row["flowsheet_switch_deltas"]
    )


def test_c2_still_carries_construction_schedule():
    record = _record_with_energy("C2")
    reconstructed = tea._scenario_config(_public_from_record_tea_exposed_coefficients(record))
    assert reconstructed["construction_schedule"] == (0.08, 0.60, 0.32)
    assert reconstructed["lang_factor"] is None
    assert tea._cache_index().get(tea._config_key(reconstructed))["label"] == (
        record["label"]
    )


def test_empty_construction_schedule_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(
            _public_from_record_tea_exposed_coefficients(record, construction_schedule=[]),
        )
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "construction_schedule"


def test_negative_construction_schedule_item_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(
            _public_from_record_tea_exposed_coefficients(record, construction_schedule=(0.08, -0.60, 0.32)),
        )
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "construction_schedule"


def test_stored_record_is_not_served_for_steam_power_depreciation_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "steam_power_depreciation mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, steam_power_depreciation="MACRS7")],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    row = payload["stored_plant_differs"][0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    schedule = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "steam_power_depreciation"
    )
    assert schedule["recorded_value"] == "MACRS20"
    assert schedule["requested_value"] == "MACRS7"
    assert not any(
        item["field"] == "depreciation"
        for item in row["flowsheet_switch_deltas"]
    )
    assert not any(
        item["field"] == "lang_factor"
        for item in row["flowsheet_switch_deltas"]
    )


def test_array_steam_power_depreciation_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(
            record, steam_power_depreciation=["MACRS20"],
        ))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "steam_power_depreciation"


def test_explicit_none_lang_factor_shares_the_serve_key():
    record = _record_with_energy("C1")
    omitted = dict(record["config"])
    as_none = {**omitted, "lang_factor": None}
    as_empty = {**omitted, "lang_factor": ""}
    assert tea._config_key(omitted) == tea._config_key(as_none)
    assert tea._config_key(omitted) == tea._config_key(as_empty)
    reconstructed = tea._scenario_config(
        _public_from_record_tea_exposed_coefficients(record, lang_factor=None),
    )
    assert reconstructed["lang_factor"] is None
    assert tea._cache_index().get(tea._config_key(reconstructed))["label"] == (
        record["label"]
    )
    empty = tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, lang_factor=""))
    assert empty["lang_factor"] is None
    assert tea._config_key(reconstructed) == tea._config_key(empty)


def test_c2_still_carries_lang_factor():
    record = _record_with_energy("C2")
    reconstructed = tea._scenario_config(_public_from_record_tea_exposed_coefficients(record))
    assert reconstructed["lang_factor"] is None
    assert tea._cache_index().get(tea._config_key(reconstructed))["label"] == (
        record["label"]
    )


def test_lang_factor_present_is_invalid_scenario_not_a_live_plant(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "a Lang factor must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, lang_factor=3.0))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "lang_factor"
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, lang_factor=3.0)],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "invalid_scenario"
    assert payload.get("field") == "lang_factor"
    assert payload.get("error_code") != "unknown_process_field"
    assert payload.get("error_code") != "cache_flowsheet_mismatch"
    assert "comparison_rows" not in payload or not payload.get("comparison_rows")


def test_zero_is_not_the_production_lang_factor(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("lang_factor=0 is not production None")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, lang_factor=0))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "lang_factor"


def test_array_lang_factor_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, lang_factor=[3.0]))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "lang_factor"


def test_omitted_evaluate_echoes_production_lang_factor(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("omitted lang_factor must hit the cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record)],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    row = (payload.get("comparison_rows") or [])[0]
    assert row.get("lang_factor") is None
    assert row.get("msp_usd_per_kg") == pytest.approx(recorded_msp)


def test_other_g_constructor_coefficients_are_not_dumped():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, lang_factor=3.0))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "lang_factor"
    assert "lang_factor" in tea._COEFFICIENT_DEFAULTS
    assert "lang_factor" in tea.public_process_field_names()
    assert "lang_factor" in tea.public_process_field_names(energy_case="C2")
    assert "steam_power_depreciation" not in tea._COEFFICIENT_DEFAULTS
    assert "steam_power_depreciation" in tea.public_process_field_names()
    assert "steam_power_depreciation" not in tea.public_process_field_names(
        energy_case="C2",
    )
    assert "construction_schedule" in tea._COEFFICIENT_DEFAULTS
    assert "construction_schedule" in tea.public_process_field_names()
    assert "depreciation" in tea._COEFFICIENT_DEFAULTS
    assert "depreciation" in tea.public_process_field_names()
    assert "duration" in tea._COEFFICIENT_DEFAULTS
    assert "duration" in tea.public_process_field_names()
    assert "maintenance" in tea._COEFFICIENT_DEFAULTS
    assert "maintenance" in tea.public_process_field_names()
    assert "property_insurance" in tea._COEFFICIENT_DEFAULTS
    assert "property_insurance" in tea.public_process_field_names()
    assert "other_indirect_costs" in tea._COEFFICIENT_DEFAULTS
    assert "other_indirect_costs" in tea.public_process_field_names()
    assert "contingency" in tea._COEFFICIENT_DEFAULTS
    assert "contingency" in tea.public_process_field_names()
    assert "construction" in tea._COEFFICIENT_DEFAULTS
    assert "construction" in tea.public_process_field_names()
    assert "field_expenses" in tea._COEFFICIENT_DEFAULTS
    assert "field_expenses" in tea.public_process_field_names()
    assert "proratable_costs" in tea._COEFFICIENT_DEFAULTS
    assert "proratable_costs" in tea.public_process_field_names()
    assert "additional_piping" in tea._COEFFICIENT_DEFAULTS
    assert "additional_piping" in tea.public_process_field_names()
    assert "site_development" in tea._COEFFICIENT_DEFAULTS
    assert "site_development" in tea.public_process_field_names()
    assert "warehouse" in tea._COEFFICIENT_DEFAULTS
    assert "warehouse" in tea.public_process_field_names()
    assert "WC_over_FCI" in tea._COEFFICIENT_DEFAULTS
    assert "WC_over_FCI" in tea.public_process_field_names()
    assert "startup_salesfrac" in tea._COEFFICIENT_DEFAULTS
    assert "startup_salesfrac" in tea.public_process_field_names()
    assert "startup_VOCfrac" in tea._COEFFICIENT_DEFAULTS
    assert "startup_VOCfrac" in tea.public_process_field_names()
    assert "startup_FOCfrac" in tea._COEFFICIENT_DEFAULTS
    assert "startup_FOCfrac" in tea.public_process_field_names()
    assert "startup_months" in tea._COEFFICIENT_DEFAULTS
    assert "startup_months" in tea.public_process_field_names()
    assert "finance_fraction" in tea._COEFFICIENT_DEFAULTS
    assert "finance_fraction" in tea.public_process_field_names()
    assert "finance_years" in tea._COEFFICIENT_DEFAULTS
    assert "finance_years" in tea.public_process_field_names()
    assert "finance_interest" in tea._COEFFICIENT_DEFAULTS
    assert "finance_interest" in tea.public_process_field_names()
    assert "labor_burden" in tea.public_process_field_names()
    assert "operating_days" in tea.public_process_field_names()


def test_natural_gas_price_on_c2_is_energy_case_contract():
    record = _record_with_energy("C2")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(
            record, natural_gas_price_usd_per_m3=0.20,
        ))
    assert caught.value.error_code == "energy_case_contract"


def test_percent_integer_irr_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, irr=10))
    assert caught.value.error_code == "invalid_scenario"


def test_polymer_ratio_is_not_on_single_step():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, polymer_ratio=0.5))
    assert caught.value.error_code == "field_not_on_this_instance"


def test_recovery_is_not_in_the_model():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, recovery=0.5))
    assert caught.value.error_code == "field_not_in_model"
    payload = _data_tea_exposed_coefficients(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_exposed_coefficients(record, recovery=0.9)],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "field_not_in_model"


def test_commented_out_setter_is_not_adjustable():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(
            record, set_boiling_point=400,
        ))
    assert caught.value.error_code == "field_not_adjustable"


def test_tau_is_expert_surface_only():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_exposed_coefficients(record, tau_h=4.0))
    assert caught.value.error_code == "expert_surface_only"
    assert "tea_polymer_parameters.py" in str(
        caught.value.details.get("expert_surface") or caught.value
    )


def test_d8_overlay_still_excludes_coefficients():
    for name in (
        "irr",
        "income_tax",
        "operating_days",
        "labor_burden",
        "finance_interest",
        "finance_years",
        "finance_fraction",
        "startup_months",
        "startup_FOCfrac",
        "startup_VOCfrac",
        "startup_salesfrac",
        "WC_over_FCI",
        "warehouse",
        "site_development",
        "additional_piping",
        "proratable_costs",
        "field_expenses",
        "construction",
        "contingency",
        "other_indirect_costs",
        "property_insurance",
        "maintenance",
        "duration",
        "depreciation",
        "construction_schedule",
        "lang_factor",
        "feedstock_price_usd_per_kg",
        "centrifuged_plastic_solvent_content_pct",
        "natural_gas_price_usd_per_m3",
        "steam_power_depreciation",
    ):
        assert name not in tea._CONFIG_FIELDS
        assert name not in {
            public for _internal, public in tea._DESIGN_POINT_PUBLIC_FIELDS
        }


def test_worker_calls_the_unreached_setters():
    source = Path(tea_worker.__file__).read_text(encoding="utf-8")
    assert "set_IRR(" in source
    assert "process.tea.income_tax" in source
    assert "process.tea.operating_days" in source
    assert "process.tea.labor_burden" in source
    assert "process.tea.finance_interest" in source
    assert "process.tea.finance_years" in source
    assert "process.tea.finance_fraction" in source
    assert "process.tea.startup_months" in source
    assert "process.tea.startup_FOCfrac" in source
    assert "process.tea.startup_VOCfrac" in source
    assert "process.tea.startup_salesfrac" in source
    assert "process.tea.WC_over_FCI" in source
    assert "process.tea.warehouse" in source
    assert "process.tea.site_development" in source
    assert "process.tea.additional_piping" in source
    assert "process.tea.proratable_costs" in source
    assert "process.tea.field_expenses" in source
    assert "process.tea.construction" in source
    assert "process.tea.contingency" in source
    assert "process.tea.other_indirect_costs" in source
    assert "process.tea.property_insurance" in source
    assert "process.tea.maintenance" in source
    assert "process.tea.depreciation" in source
    assert "process.tea.duration" in source
    assert "process.tea.construction_schedule" in source
    assert "process.tea.steam_power_depreciation" in source
    assert "process.tea.lang_factor" in source
    assert "set_feedstock_price(" in source
    assert "set_centrifuged_plastic_solvent_content(" in source
    assert "set_natural_gas_price(" in source
    assert "if energy[\"facilities\"]:" in source
    assert "set_polymer_mass_fraction(" not in source


# --- from test_tea_flowsheet_switches.py: Flowsheet switches belong in the serve key. The twelve-only cache is a trap.
def _public_from_record_tea_flowsheet_switches(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temp_c": cfg["dissolution_temperature_c"],
        "precipitation_temp_c": cfg["precipitation_temperature_c"],
        "solvent_price": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def test_cache_index_stays_unique_after_switch_projection():
    records = tea._records()
    index = tea._cache_index()
    assert len(records) == 24
    assert len(index) == len(records)


def test_omitted_and_explicit_production_defaults_share_the_serve_key():
    record = _record_with_energy("C1")
    twelve = dict(record["config"])
    explicit = {
        **twelve,
        "sell_leftover_plastic": False,
        "burn_leftover_plastic": False,
        "precipitation_temperature_format": "constant",
        "precipitation_configuration": "integrated heat transfer",
    }
    assert tea._config_key(twelve) == tea._config_key(explicit)
    hit = tea._cache_index().get(tea._config_key(twelve))
    assert hit is not None
    assert hit["label"] == record["label"]
    reconstructed = tea._scenario_config(_public_from_record_tea_flowsheet_switches(record))
    reconstructed_hit = tea._cache_index().get(tea._config_key(reconstructed))
    assert reconstructed_hit is not None
    assert reconstructed_hit["label"] == record["label"]
    assert reconstructed["sell_leftover_plastic"] is False
    assert reconstructed["burn_leftover_plastic"] is False
    assert reconstructed["precipitation_temperature_format"] == "constant"
    assert reconstructed["precipitation_configuration"] == (
        "integrated heat transfer"
    )


def test_burn_true_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    burned = {**dict(record["config"]), "burn_leftover_plastic": True}
    assert tea._config_key(record["config"]) != tea._config_key(burned)
    assert tea._cache_index().get(tea._config_key(burned)) is None
    analog = tea._record_for_design_point(burned)
    assert analog is not None
    assert analog["label"] == record["label"]


def test_stored_record_is_not_served_for_flowsheet_mismatch_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = (record["result"].get("tea") or {}).get("msp_usd_per_kg")
    assert recorded_msp is not None

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("cache mismatch must not fall through to live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_flowsheet_switches(record, burn_leftover_plastic=True)],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    failures = list(payload.get("stored_plant_differs") or [])
    assert len(failures) == 1
    row = failures[0]
    assert row["cache_record_label"]
    assert row.get("msp_usd_per_kg") is None
    assert recorded_msp not in (row.get("msp_usd_per_kg"), row.get("tea"))
    deltas = list(row.get("flowsheet_switch_deltas") or [])
    burn = next(item for item in deltas if item["field"] == "burn_leftover_plastic")
    assert burn["recorded_value"] is False
    assert burn["requested_value"] is True


def test_existing_twelve_lookup_still_hits_a_real_record(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("exact production-default lookup must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_flowsheet_switches(record)],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert payload.get("engine_mode") == "cache"
    assert payload.get("cache_match_status") == "exact"
    rows = list(payload.get("comparison_rows") or [])
    assert len(rows) == 1
    assert rows[0]["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"])
    )
    assert rows[0]["burn_leftover_plastic"] is False


def test_leftover_disposition_conflict_refuses_both_true():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_flowsheet_switches(
            record,
            sell_leftover_plastic=True,
            burn_leftover_plastic=True,
        ))
    assert caught.value.error_code == "leftover_disposition_conflict"
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_flowsheet_switches(
            record,
            sell_leftover_plastic=True,
            burn_leftover_plastic=True,
        )],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "leftover_disposition_conflict"


def test_burn_requires_facilities_on_c2():
    record = _record_with_energy("C2")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_flowsheet_switches(
            record, burn_leftover_plastic=True,
        ))
    assert caught.value.error_code == "burn_requires_facilities"
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_tea_flowsheet_switches(record, burn_leftover_plastic=True)],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "burn_requires_facilities"


def test_independent_facilities_knob_is_energy_case_contract():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_flowsheet_switches(record, facilities=False))
    assert caught.value.error_code == "energy_case_contract"


def test_drop_precipitation_format_is_not_on_this_instance():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record_tea_flowsheet_switches(
            record, precipitation_temperature_format="drop",
        ))
    assert caught.value.error_code == "field_not_on_this_instance"


def test_d8_overlay_vocabulary_stays_the_twelve():
    public = [name for _internal, name in tea._DESIGN_POINT_PUBLIC_FIELDS]
    assert len(tea._CONFIG_FIELDS) == 12
    assert len(public) == 12
    for name in tea._FLOWSHEET_SWITCH_FIELDS:
        assert name not in tea._CONFIG_FIELDS
        assert name not in public


def test_worker_scenario_reads_switches_from_config():
    source = Path(tea_worker.__file__).read_text(encoding="utf-8")
    assert "sell_leftover_plastic=False, burn_leftover_plastic=False" not in source
    tree = ast.parse(source)
    keywords = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Scenario"
        ):
            keywords = {kw.arg: kw.value for kw in node.keywords}
            break
    assert keywords is not None
    for name in (
        "sell_leftover_plastic",
        "burn_leftover_plastic",
        "precipitation_configuration",
    ):
        value = keywords[name]
        assert isinstance(value, ast.Call)
        dumped = ast.dump(value)
        assert "config" in dumped
    fmt = keywords["precipitation_temperature_format"]
    assert isinstance(fmt, ast.Name)
    assert fmt.id == "precipitation_format"


# --- from test_tea_polymer_parameters.py: Live TEA admission, parameter surface, and standing — no BioSTEAM.
# This checkout's live-TEA environment (both gitignored until plastics is published), read before the autouse fixture
# hides them from every other test.
_REAL_PLASTICS_PARENT = tea.tea_polymer_parameters.VENDORED_PLASTICS
_CHECKOUT_TEA_PYTHON = tea._REPO_TEA_PYTHON


_FIXTURE_OUTLINE = '''
STRAP_chemicals_outline = bst.ChemicalsOutline([
    "Toluene",
    bst.ChemicalDraft("PE", aliases=set(["Polyethylene"]), formula="C2H4"),
    bst.ChemicalDraft("PEoligomer", search_ID="1-Hexene"),
    bst.ChemicalDraft("PES", formula="C12H8O3S"),
    bst.ChemicalDraft("PESoligomer", search_ID="Diphenyl sulfone"),
    bst.ChemicalDraft("PET", formula="C10H8O4"),
    bst.ChemicalDraft("Nylon6", formula="C6H11NO"),
    bst.ChemicalDraft("Nylon6oligomer", search_ID="Caprolactam"),
    "Water",
])
'''


def _committed_pe_toluene_config(**overrides):
    committed = params.COMMITTED_PAIR_STEPS[("PE", "toluene")]
    config = {
        "solvent": "toluene",
        "target_plastic": "LDPE",
        "target_plastic_percent": 60.0,
        "processing_capacity": 20_000.0,
        "energy_case": "C1",
        **params.process_config_from_assumptions(committed),
        "solvent_price": 1.312,
        "solvent_loss_pct": 0.01,
        "feedstock_distance_km": 0.0,
        "labor_cost": 120_000.0,
    }
    config.update(overrides)
    return config


# Synthetic standing-shape fixtures. Not live endpoints. The PE/Toluene
# MSP 1.13492 belongs to a 55% target-plastic run, not this 60% config;
# do not reattach that number here.
STANDING_SHAPE_TOLUENE = {
    "msp_usd_per_kg": 9.0,
    "tci_usd": 1.0e6,
    "aoc_usd_per_yr": 2.0e5,
    "gwp_kg_co2e_per_kg": 0.5,
}


LDPE_DODECANE_C1 = {
    "solvent": "Dodecane",
    "target_plastic": "LDPE",
    "target_plastic_percent": 55.0,
    "processing_capacity": 20_000.0,
    "energy_case": "C1",
    "dissolution_temperature_c": 145.0,
    "precipitation_temperature_c": 25.0,
    "solvent_price": 4.08,
    "solvent_loss_pct": 0.01,
    "feedstock_distance_km": 0.0,
    "dissolution_capacity": 3.0,
    "labor_cost": 120_000.0,
}


STANDING_SHAPE_DODECANE_MSP = 8.0


def _write_matching_pe_package(
    root: Path, *, pe_rho: str | None = "0.5 * (880 + 960)",
) -> Path:
    strap = root / "plastics" / "strap"
    strap.mkdir(parents=True)
    rho_line = f"        rho={pe_rho},\n" if pe_rho is not None else ""
    (strap / "property_package.py").write_text(
        f'''
STRAP_chemicals_outline = bst.ChemicalsOutline([
    bst.ChemicalDraft(
        "PE",
        formula="C2H4",
{rho_line}        Cp=0.5 * (1.330 + 2.400),
        Tm=0.5 * (115 + 135) + 273.15,
    ),
    bst.ChemicalDraft("PEoligomer", search_ID="1-Hexene"),
])
''',
        encoding="utf-8",
    )
    (strap / "dissolution_steps.py").write_text(
        """
def PE_Toluene_dissolution():
    return DissolutionStep(
        'PE', 'PEoligomer', 'Toluene', None, 0.03,
        0.5, 368.15, 0.5
    )
""",
        encoding="utf-8",
    )
    (strap / "precipitation_steps.py").write_text(
        """
def PE_Toluene_precipitation():
    return PrecipitationStep(
        'Toluene', 'PE', 'PEoligomer',
        0, 0.8, 0.4, 308.15, 0.5,
        None,
    )
""",
        encoding="utf-8",
    )
    return root


def test_expert_surface_lists_every_grid_polymer_including_refusals():
    assert set(params.POLYMERS) >= {
        "LDPE", "HDPE", "EVOH", "PC", "PET", "PP", "PS", "PVC",
        "NYLON6", "NYLON66", "PES", "PU",
    }
    assert params.POLYMERS["PU"].admission == params.ADMISSION_REFUSE
    assert params.POLYMERS["PES"].admission == params.ADMISSION_LIVE
    assert params.POLYMERS["PVC"].chemist_signoff_required is True
    assert "dissolution" in params.POLYMERS["PVC"].chemist_signoff_note.lower()


def test_nylon_maps_to_package_id_not_raw_grid_name():
    assert params.POLYMERS["NYLON6"].identity_in_model == "Nylon6"
    assert params.POLYMERS["NYLON66"].identity_in_model == "Nylon66"
    admitted = params.admit_live_target("NYLON6")
    assert admitted.admitted is True
    assert admitted.identity_in_model == "Nylon6"


def test_pet_oligomer_is_provisional_and_injectable():
    row = params.POLYMERS["PET"]
    assert row.oligomer is not None
    assert row.oligomer.in_package_outline is False
    assert row.oligomer.inject_if_missing is True
    assert row.oligomer.standing == params.PROVISIONAL


def test_admission_is_not_a_raw_name_fallthrough():
    unknown = params.admit_live_target("ABS")
    assert unknown.admitted is False
    assert unknown.error_type == "unsupported_live_target"
    assert "PET-clone" in unknown.error or "parameter surface" in unknown.error

    pu = params.admit_live_target("PU")
    assert pu.admitted is False
    assert "PET-clone" in pu.error

    pes = params.admit_live_target("PES")
    assert pes.admitted is True
    assert pes.identity_in_model == "PES"


def test_package_predicate_refuses_pet_clone_when_id_missing():
    ids = frozenset({"PE", "PEoligomer", "EVOH", "EVOHoligomer"})
    missing = params.admit_live_target("PES", package_chemical_ids=ids)
    assert missing.admitted is False
    assert missing.package_chemical_present is False
    assert "PET-clone" in missing.error

    nylon_raw = params.admit_live_target(
        "NYLON6", package_chemical_ids=frozenset({"NYLON6"}),
    )
    # Outline has the raw grid name only: that is NOT Nylon6, so refuse.
    assert nylon_raw.admitted is False

    nylon_ok = params.admit_live_target(
        "NYLON6",
        package_chemical_ids=frozenset({"Nylon6", "Nylon6oligomer"}),
    )
    assert nylon_ok.admitted is True
    assert nylon_ok.identity_in_model == "Nylon6"


def test_pet_missing_oligomer_requires_injection_not_clone():
    ids = frozenset({"PET"})  # chemical yes, oligomer no
    admission = params.admit_live_target("PET", package_chemical_ids=ids)
    assert admission.admitted is True
    assert admission.oligomer_injection_required is True
    assert admission.package_oligomer_present is False


def test_parser_reads_outline_ids_not_aliases_or_search_ids(tmp_path):
    source = tmp_path / "property_package.py"
    source.write_text(_FIXTURE_OUTLINE, encoding="utf-8")
    ids = params.package_chemical_ids_from_source(source)
    assert "PE" in ids
    assert "PEoligomer" in ids
    assert "PES" in ids
    assert "PET" in ids
    assert "Toluene" in ids
    assert "Polyethylene" not in ids
    assert "1-Hexene" not in ids
    assert "Diphenyl sulfone" not in ids
    assert "PU" not in ids
    assert "PEToligomer" not in ids


@pytest.mark.skipif(
    not (_REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py").is_file(),
    reason="unpublished plastics 0.1.4 is not on this machine",
)
def test_real_package_outline_has_pes_nylon_not_pu_not_petoligomer():
    ids = params.package_chemical_ids(_REAL_PLASTICS_PARENT)
    assert ids is not None
    assert "PES" in ids and "PESoligomer" in ids
    assert "Nylon6" in ids and "Nylon6oligomer" in ids
    assert "PET" in ids
    assert "PEToligomer" not in ids
    assert "PU" not in ids
    pes = params.admit_live_target("PES", package_chemical_ids=ids)
    assert pes.admitted is True
    pu = params.admit_live_target("PU", package_chemical_ids=ids)
    assert pu.admitted is False
    pet = params.admit_live_target("PET", package_chemical_ids=ids)
    assert pet.admitted is True
    assert pet.oligomer_injection_required is True


def test_provisional_payload_labels_every_costed_metric():
    row = params.POLYMERS["PES"]
    payload = params.live_parameter_standing_payload(
        row, solvent="toluene", present_metrics=("msp_usd_per_kg", "tci_usd"),
    )
    assert payload["can_cite_as_validated_process"] is False
    assert payload["process_parameter_status"]["msp_usd_per_kg"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    assert payload["process_parameter_status"]["tci_usd"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    definition = payload["process_parameter_status_definitions"][
        params.PROCESS_PARAMETER_STATUS_CODE
    ]
    assert definition["can_cite_as_validated_process"] is False
    assert definition["current_scientific_use"] == (
        "not_admitted_as_validated_process"
    )
    assert payload["live_parameter_standing"]["chemist_signoff_required"] is False


def test_pe_toluene_committed_pair_is_validated_other_solvents_are_not():
    row = params.POLYMERS["LDPE"]
    exact = _committed_pe_toluene_config()
    toluene = params.live_parameter_standing_payload(
        row, solvent="Toluene", config=exact, package_disagreements=(),
    )
    assert toluene["can_cite_as_validated_process"] is True
    assert "process_parameter_status" not in toluene
    unnamed = params.live_parameter_standing_payload(
        row, solvent="Toluene", package_disagreements=(),
    )
    assert unnamed["can_cite_as_validated_process"] is False
    hexane = params.live_parameter_standing_payload(
        row, solvent="hexane", config=exact, package_disagreements=(),
    )
    assert hexane["can_cite_as_validated_process"] is False
    assert "msp_usd_per_kg" in hexane["process_parameter_status"]


def test_pvc_is_provisional_because_chemist_has_not_signed_off():
    row = params.POLYMERS["PVC"]
    payload = params.live_parameter_standing_payload(row, solvent="thf")
    assert payload["can_cite_as_validated_process"] is False
    assert payload["live_parameter_standing"]["chemist_signoff_required"] is True
    assert "chemist_signoff_required" in payload["live_parameter_standing"][
        "provisional_parameters"
    ]


def test_worker_refuses_pu_and_unknown_before_python_version(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    pu = tea_worker.run({"target_plastic": "PU"})
    assert pu["success"] is False
    assert pu["error_type"] == "unsupported_live_target"
    assert "PET-clone" in pu["error"]
    unknown = tea_worker.run({"target_plastic": "ABS"})
    assert unknown["success"] is False
    assert unknown["error_type"] == "unsupported_live_target"
    assert tea_worker._TARGET.get("PES") == "PES"
    assert "PU" not in tea_worker._TARGET
    assert tea_worker._TARGET.get("NYLON6") == "Nylon6"
    # No raw-name default: missing keys stay missing.
    assert tea_worker._TARGET.get("ABS") is None


def test_worker_no_longer_has_a_denylist_guarding_a_default():
    source = Path(tea_worker.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    text = ast.dump(tree)
    assert "_UNSUPPORTED_LIVE_TARGETS" not in source
    assert "original_target, original_target" not in source
    # Admission must call the predicate, not a remembered six-name set.
    assert "admit_live_target" in source
    assert "PET-clone" in source or "PET-clone" in text


def test_refuse_reason_is_populated_for_every_refused_polymer():
    refused = [
        row for row in params.POLYMERS.values()
        if row.admission == params.ADMISSION_REFUSE
    ]
    assert refused, "the surface must list refused polymers, not omit them"
    for row in refused:
        assert row.refuse_reason.strip(), (
            f"{row.grid_name} is refused with an empty refuse_reason"
        )


def test_unlabelled_live_msp_is_a_standing_defect():
    bare = {
        "success": True,
        "tea": {"msp_usd_per_kg": 1.25},
    }
    defects = params.live_number_standing_defects(bare)
    assert defects
    assert any("can_cite_as_validated_process is absent" in item for item in defects)


def test_run_binds_standing_on_injected_live_success(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    unlabelled = {
        "success": True,
        "solvent": "toluene",
        "target_plastic": "PES",
        "tea": {
            "msp_usd_per_kg": 1.25,
            "tci_usd": 1.0,
            "aoc_usd_per_yr": 1.0,
        },
        "lca": {"gwp_kg_co2e_per_kg": 0.8},
        "operations": {},
    }
    monkeypatch.setattr(tea, "_live", lambda config, timeout: dict(unlabelled))
    config = {
        "solvent": "toluene",
        "target_plastic": "PES",
        "target_plastic_percent": 60.0,
        "processing_capacity": 20_000.0,
        "energy_case": "C1",
        "dissolution_temperature_c": 130.0,
        "precipitation_temperature_c": 25.0,
        "solvent_price": 2.17,
        "solvent_loss_pct": 0.01,
        "feedstock_distance_km": 0.0,
        "dissolution_capacity": 3.0,
        "labor_cost": 120_000.0,
    }
    pes = tea._run(dict(config), "live", 1)
    assert pes["success"] is True
    assert not params.live_number_standing_defects(pes)
    assert pes["tea"]["can_cite_as_validated_process"] is False
    assert pes["tea"]["process_parameter_status"]["msp_usd_per_kg"]
    row = tea._comparison_row("pes", pes)
    assert row["process_parameter_status"]["msp_usd_per_kg"]
    assert row["can_cite_as_validated_process"] is False

    config["target_plastic"] = "LDPE"
    config["dissolution_temperature_c"] = 95.0
    unlabelled["target_plastic"] = "LDPE"
    ldpe_wrong_precip = tea._run(dict(config), "live", 1)
    assert ldpe_wrong_precip["success"] is True
    assert not params.live_number_standing_defects(ldpe_wrong_precip)
    assert ldpe_wrong_precip["tea"]["can_cite_as_validated_process"] is False
    assert "precipitation_temperature_c" in ldpe_wrong_precip[
        "live_parameter_standing"
    ]["committed_setpoint_mismatches"]

    config["precipitation_temperature_c"] = 35.0
    ldpe = tea._run(dict(config), "live", 1)
    assert ldpe["success"] is True
    assert not params.live_number_standing_defects(ldpe)
    assert ldpe["tea"]["can_cite_as_validated_process"] is True
    assert params.live_standing_signature(pes) != params.live_standing_signature(ldpe)
    assert params.live_standing_signature(ldpe_wrong_precip) != (
        params.live_standing_signature(ldpe)
    )


def test_standing_shape_keeps_validated_apart_from_correct(monkeypatch):
    """Standing-shape unit test: injected metrics, not a live oracle.

    Patched ``_live`` proves validated vs provisional follows executed
    setpoints and committed-pair membership. It does not bind MSP/TCI/AOC/GWP
    to BioSTEAM. The production-bound child handshake is
    ``test_runpy_child_returns_named_json_for_refused_target``.
    """
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)

    def fake_live(config, timeout):
        solvent = str(config.get("solvent") or "").casefold()
        plastic = str(config.get("target_plastic") or "").upper()
        if plastic == "LDPE" and solvent == "toluene":
            return {
                "success": True,
                "solvent": "toluene",
                "target_plastic": "LDPE",
                "tea": {
                    "msp_usd_per_kg": STANDING_SHAPE_TOLUENE["msp_usd_per_kg"],
                    "tci_usd": STANDING_SHAPE_TOLUENE["tci_usd"],
                    "aoc_usd_per_yr": STANDING_SHAPE_TOLUENE["aoc_usd_per_yr"],
                },
                "lca": {"gwp_kg_co2e_per_kg": STANDING_SHAPE_TOLUENE["gwp_kg_co2e_per_kg"]},
                "operations": {},
            }
        if plastic == "LDPE" and solvent == "dodecane":
            return {
                "success": True,
                "solvent": "Dodecane",
                "target_plastic": "LDPE",
                "tea": {
                    "msp_usd_per_kg": STANDING_SHAPE_DODECANE_MSP,
                    "tci_usd": 87102535.73687321,
                    "aoc_usd_per_yr": 2356462.05524089,
                },
                "lca": {"gwp_kg_co2e_per_kg": 0.6207154872703069},
                "operations": {},
            }
        raise AssertionError(f"unexpected live config {config!r}")

    monkeypatch.setattr(tea, "_live", fake_live)
    committed = tea._run(_committed_pe_toluene_config(), "live", 1)
    moved = tea._run(
        _committed_pe_toluene_config(precipitation_temperature_c=25.0),
        "live",
        1,
    )
    dodecane = tea._run(dict(LDPE_DODECANE_C1), "live", 1)

    assert committed["tea"]["msp_usd_per_kg"] == STANDING_SHAPE_TOLUENE["msp_usd_per_kg"]
    assert committed["can_cite_as_validated_process"] is True
    assert not params.live_number_standing_defects(committed)

    assert moved["tea"]["msp_usd_per_kg"] == STANDING_SHAPE_TOLUENE["msp_usd_per_kg"]
    assert moved["can_cite_as_validated_process"] is False
    assert "precipitation_temperature_c" in moved["live_parameter_standing"][
        "committed_setpoint_mismatches"
    ]
    assert moved["process_parameter_status"]["msp_usd_per_kg"]

    assert dodecane["tea"]["msp_usd_per_kg"] == STANDING_SHAPE_DODECANE_MSP
    assert dodecane["can_cite_as_validated_process"] is False
    assert dodecane["live_parameter_standing"]["committed_pair"] is None
    assert dodecane["process_parameter_status"]["msp_usd_per_kg"]
    assert params.live_standing_signature(committed) != (
        params.live_standing_signature(dodecane)
    )
    assert params.live_standing_signature(committed) != (
        params.live_standing_signature(moved)
    )


def test_parent_refuses_unlabelled_provisional_live_result():
    unlabelled = {
        "success": True,
        "target_plastic": "PES",
        "solvent": "toluene",
        "tea": {"msp_usd_per_kg": 1.23, "tci_usd": 4.0, "aoc_usd_per_yr": 5.0},
        "lca": {"gwp_kg_co2e_per_kg": 0.9},
        "operations": {},
    }
    guarded = tea._require_live_parameter_standing(
        dict(unlabelled), {"target_plastic": "PES", "solvent": "toluene"},
    )
    assert guarded["success"] is True
    assert guarded["can_cite_as_validated_process"] is False
    assert guarded["tea"]["can_cite_as_validated_process"] is False
    assert guarded["process_parameter_status"]["msp_usd_per_kg"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    assert guarded["tea"]["process_parameter_status"]["msp_usd_per_kg"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    stray = tea._require_live_parameter_standing(
        {
            "success": True,
            "target_plastic": "ABS",
            "tea": {"msp_usd_per_kg": 9.9},
        },
        {"target_plastic": "ABS"},
    )
    assert stray["success"] is False
    assert stray["error_type"] == "live_parameter_standing_missing"
    assert "tea" not in stray


def test_classify_live_feed_uses_parameter_surface_not_old_three_names():
    unrecognised, unmodelled = tea._classify_live_feed_polymers(
        ["LDPE", "PES", "PET", "PU", "not-a-polymer"],
    )
    assert "not-a-polymer" in unrecognised
    # "PU" resolves to the POLYURETHANES family; its one grid member is
    # refused on the parameter surface, so the family is unmodelled.
    assert "POLYURETHANES" in unmodelled
    assert "PU" not in params.live_identity_map()
    assert "PES" not in unmodelled
    assert "PET" not in unmodelled
    assert "LDPE" not in unmodelled


def test_doctor_fails_when_live_tea_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Live TEA")
    assert check["status"] == "fail"  # TEA answers nothing without live TEA, so this is not optional
    assert check["parameter_surface"].endswith("tea_polymer_parameters.py")
    assert "PES" in check["admitted_live_targets"]
    assert "PU" in check["refused_grid_targets"]
    assert "unavailable because" in check["detail"]
    assert "DISSOLVE_PLASTICS_PATH is unset" in check["detail"]
    assert "3.12" in check["detail"]
    assert "subprocess" in check["detail"]
    assert "duckdb" in check["detail"]
    assert "tea_worker" in check["detail"]
    assert check.get("execution_path")
    assert "ModuleNotFoundError: duckdb" in check["execution_path"]
    assert check.get("why_unavailable")


# The stored results are live results: each record's LCA came from a live run of its configuration (2026-09-22).
_LIVE_GWP = {"ldpe-route-c1": 0.620715487270307, "ldpe-route-c2": 0.9825799139489072, "ldpe-route-c3": 0.9652122846784537}


def test_stored_lca_is_the_live_result_with_its_coverage(monkeypatch):
    _forbid_live(monkeypatch)
    generator = tea.cache_payload()["generator"]
    regenerated = generator["lca_regenerated_live"]
    assert regenerated["process_model_sha256"] == generator["process_model_sha256"]
    assert {key: regenerated["runtime_versions"][key] for key in ("biosteam", "thermosteam", "biorefineries")} == (
        tea._expected_live_runtime_versions()
    )
    for label, gwp in _LIVE_GWP.items():
        record = _record_by_label(label)
        served = tea._run(record["config"], "auto", 60)
        assert (served["engine_mode"], served["lca"]["gwp_kg_co2e_per_kg"]) == ("cache", gwp)
        assert served["lca_coverage"]["lca_metrics_status"] == "partial"
        assert "lca_metric_status" not in served and "electricity_intensity_mj_per_kg" not in served["operations"]
    rows = _data(tea.evaluate_tea_lca_scenarios([_public_from_record(_record_by_label(label)) for label in _LIVE_GWP]))
    assert rows["lowest_gwp_scenario"] == "scenario-1"  # C1 is lowest once the natural-gas double count is gone
    assert any("Uncharacterized active contributors" in gap for gap in rows["process_data_gaps"])


@pytest.mark.skipif(not os.getenv("DISSOLVE_LIVE_TEA_KNOWN_ANSWER"), reason="set DISSOLVE_LIVE_TEA_KNOWN_ANSWER=1 to run live TEA")
def test_known_answer_one_stored_record_per_energy_case_reproduces_live(monkeypatch):
    """Runs BioSTEAM three times (about 40 s); hold /tmp/dissolve-tea-worker.lock if other live TEA work may run."""
    monkeypatch.setattr(tea, "_live_tea_blocker", _LIVE_TEA_BLOCKER)
    monkeypatch.setattr(tea, "_REPO_TEA_PYTHON", _CHECKOUT_TEA_PYTHON)
    monkeypatch.setattr(tea.tea_polymer_parameters, "VENDORED_PLASTICS", _REAL_PLASTICS_PARENT)
    for label in _LIVE_GWP:
        record = _record_by_label(label)
        live = tea._live(record["config"], 600)
        for block in ("tea", "lca"):
            for key, stored in record["result"][block].items():
                assert live[block][key] == pytest.approx(stored, rel=1e-12), (label, key)


def _live_tea_down(monkeypatch):
    monkeypatch.setattr(tea, "_live_tea_blocker", _LIVE_TEA_BLOCKER)
    monkeypatch.setattr(tea, "live_engine_status", lambda: {
        "available": False, "reason": "plastics_path_unset", "detail": "DISSOLVE_PLASTICS_PATH is unset",
    })


def test_no_tea_answer_without_live_tea_not_even_a_stored_one(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    public = _public_from_record(record)
    polymer = record["config"]["target_plastic"]
    _live_tea_down(monkeypatch)
    calls = {
        "lookup_admitted_process_records": lambda: tea.lookup_admitted_process_records(target_polymer=polymer),
        "evaluate_process": lambda: tea.evaluate_process(mode="evaluate", process_config=public),
        "rank_landscape": lambda: tea.rank_landscape(target_polymer=polymer),
        "evaluate_tea_lca_scenarios": lambda: tea.evaluate_tea_lca_scenarios([public]),
        "evaluate_stored_route_tea_lca": lambda: tea.evaluate_stored_route_tea_lca(feed_mass_fractions={polymer: 1.0}),
        "analyze_tea_sensitivity": lambda: tea.analyze_tea_sensitivity(public, parameter="solvent_price"),
    }
    for name, call in calls.items():
        payload = _data(call())
        assert (payload["tool_name"], payload["success"], payload["error_code"]) == (name, False, "live_tea_unavailable")
        assert "DISSOLVE_PLASTICS_PATH is unset" in payload["error"]
        assert payload["remediation"]
        assert repr(recorded_msp) not in json.dumps(payload)
    stored = tea._run(record["config"], "auto", 60)
    assert (stored["error_type"], stored["cache_match_status"]) == ("live_tea_unavailable", "not_consulted")


def test_a_stored_result_serves_once_live_tea_works(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    stored = tea._run(record["config"], "auto", 60)
    assert (stored["success"], stored["engine_mode"], stored["cache_match_status"]) == (True, "cache", "exact")
    assert stored["tea"]["msp_usd_per_kg"] == pytest.approx(record["result"]["tea"]["msp_usd_per_kg"])


def test_cache_is_not_an_engine_mode(monkeypatch):
    record = _record_with_energy("C1")
    public = _public_from_record(record)
    _forbid_live(monkeypatch)
    for raw in (
        tea.evaluate_tea_lca_scenarios([public], engine_mode="cache"),
        tea.evaluate_process(mode="evaluate", process_config=public, engine_mode="cache"),
        tea.analyze_tea_sensitivity(public, parameter="solvent_price", engine_mode="cache"),
    ):
        payload = _data(raw)
        assert (payload["error_code"], payload["engine_mode"]) == ("invalid_engine_mode", "cache")
    assert tea._run(record["config"], "cache", 60)["error_type"] == "invalid_engine_mode"


def test_public_scenario_overrides_cannot_cite_committed_pair_names(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    captured = {}
    unlabelled = {
        "success": True,
        "solvent": "toluene",
        "target_plastic": "LDPE",
        "tea": {
            "msp_usd_per_kg": 7.25,
            "tci_usd": 1.0,
            "aoc_usd_per_yr": 1.0,
        },
        "lca": {"gwp_kg_co2e_per_kg": 0.8},
        "operations": {},
    }

    def fake_live(config, timeout):
        captured.update(config)
        return dict(unlabelled)

    monkeypatch.setattr(tea, "_live", fake_live)
    raw = tea.evaluate_tea_lca_scenarios(
        scenarios=[{
            "target_polymer": "LDPE",
            "solvent": "toluene",
            "target_mass_percent": 60.0,
            "processing_capacity_mt_per_yr": 20_000.0,
            "energy_case": "C1",
            "dissolution_temp_c": 200.0,
            "precipitation_temp_c": 10.0,
            "dissolution_capacity": 9.0,
            "solvent_price": 2.17,
            "solvent_loss_pct": 0.01,
            "feedstock_distance_km": 0.0,
            "labor_cost": 120_000.0,
        }],
        engine_mode="live",
        confirm_live_tea=True,
    )
    payload = json.loads(raw)
    assert captured["dissolution_temperature_c"] == 200.0
    assert captured["precipitation_temperature_c"] == 10.0
    assert captured["dissolution_capacity"] == 9.0
    rows = payload["data"]["comparison_rows"]
    assert rows[0]["success"] is True
    assert rows[0]["msp_usd_per_kg"] == 7.25
    assert rows[0]["can_cite_as_validated_process"] is False
    assert rows[0]["process_parameter_status"]["msp_usd_per_kg"]
    mismatches = rows[0]["live_parameter_standing"]["committed_setpoint_mismatches"]
    assert "dissolution_temperature_c" in mismatches
    assert "precipitation_temperature_c" in mismatches
    assert "dissolution_capacity" in mismatches


def test_mutating_committed_precip_makes_validation_fail(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    original = params.COMMITTED_PAIR_STEPS[("PE", "toluene")]
    exact = _committed_pe_toluene_config()
    before = params.live_parameter_standing_payload(
        params.POLYMERS["LDPE"],
        solvent="toluene",
        config=exact,
        package_disagreements=(),
    )
    assert before["can_cite_as_validated_process"] is True
    mutated = replace(original, precipitation_temperature_c=40.0)
    monkeypatch.setitem(params.COMMITTED_PAIR_STEPS, ("PE", "toluene"), mutated)
    after = params.live_parameter_standing_payload(
        params.POLYMERS["LDPE"],
        solvent="toluene",
        config=exact,
        package_disagreements=(),
    )
    assert after["can_cite_as_validated_process"] is False
    assert "precipitation_temperature_c" in after["live_parameter_standing"][
        "committed_setpoint_mismatches"
    ]
    # The table does not configure the plant. Mutating the claim cannot
    # change the request config that would be sent to the worker.
    cfg = tea._scenario_config({
        "target_polymer": "LDPE",
        "solvent": "toluene",
        "target_mass_percent": 60.0,
        "processing_capacity_mt_per_yr": 20_000.0,
        "energy_case": "C1",
        "dissolution_temperature_c": 95.0,
        "precipitation_temperature_c": 35.0,
        "solvent_price": 2.17,
        "solvent_loss_pct": 0.01,
        "feedstock_distance_km": 0.0,
        "dissolution_capacity": 3.0,
        "labor_cost": 120_000.0,
    })
    assert cfg["precipitation_temperature_c"] != 40.0


def test_package_chemical_mutation_makes_validation_fail(tmp_path):
    _write_matching_pe_package(tmp_path)
    row = params.POLYMERS["LDPE"]
    exact = _committed_pe_toluene_config()
    matching = params.surface_package_disagreements(row, "toluene", tmp_path)
    assert matching == ()
    validated = params.live_parameter_standing_payload(
        row, solvent="toluene", config=exact, plastics_root=tmp_path,
    )
    assert validated["can_cite_as_validated_process"] is True
    mutated_row = replace(row, chemical=replace(row.chemical, rho_kg_m3=1.0))
    disagreements = params.surface_package_disagreements(
        mutated_row, "toluene", tmp_path,
    )
    assert "rho_kg_m3" in disagreements
    payload = params.live_parameter_standing_payload(
        mutated_row,
        solvent="toluene",
        config=exact,
        plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False


def test_doctor_malformed_property_package_is_named_fail(tmp_path, monkeypatch):
    root = tmp_path / "plastics-root"
    strap = root / "plastics" / "strap"
    strap.mkdir(parents=True)
    (strap / "property_package.py").write_text("def (\n", encoding="utf-8")
    (strap / "process_model.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = tea.live_environment_report()
    assert report["reason"] == "property_package_unreadable"
    assert report["check_status"] == "fail"
    assert str(strap / "property_package.py") in (report["why_unavailable"] or "")
    assert "Traceback" not in (report["why_unavailable"] or "")
    doctor = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in doctor["checks"] if c["name"] == "Live TEA")
    assert check["status"] == "fail"
    assert check["reason"] == "property_package_unreadable"
    assert str(strap / "property_package.py") in (check["detail"] or "")


def test_worker_malformed_package_is_named_refusal(tmp_path, monkeypatch):
    root = tmp_path / "plastics-root"
    strap = root / "plastics" / "strap"
    strap.mkdir(parents=True)
    (strap / "property_package.py").write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    result = tea_worker.run({"target_plastic": "LDPE"})
    assert result["success"] is False
    assert result["error_type"] == "property_package_unreadable"
    assert str(strap / "property_package.py") in result["error"]


def test_cited_package_hashes_seal_the_files_the_table_cites(tmp_path):
    _write_matching_pe_package(tmp_path)
    first = params.cited_strap_source_provenance(tmp_path)
    assert first["missing"] == ()
    assert set(first["sources"]) == set(params.CITED_STRAP_SOURCE_NAMES)
    for name, row in first["sources"].items():
        assert row["sha256"]
        assert Path(row["path"]).is_file()
    precip = Path(first["sources"]["precipitation_steps"]["path"])
    precip.write_text(precip.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    drifted = params.cited_strap_source_provenance(tmp_path)
    assert drifted["sources"]["precipitation_steps"]["sha256"] != (
        first["sources"]["precipitation_steps"]["sha256"]
    )
    mismatches = params.cited_package_hash_mismatches(
        params.cited_package_sha256_map(first), drifted,
    )
    assert "precipitation_steps" in mismatches
    assert "property_package" not in mismatches


def test_runpy_child_returns_named_json_for_refused_target(monkeypatch):
    """Production child handshake: exact runpy argv, cheap refused target.

    On sealed 5c0f4be this child wrote no JSON (invalid_worker_output).
    The producer is the documented worker, not an expected-value fixture.
    When the isolated 3.12 interpreter and unpublished package are on
    this machine, use them — that is the path that was dead.
    """
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    live_python = _CHECKOUT_TEA_PYTHON
    if live_python.is_file():
        monkeypatch.setenv("DISSOLVE_TEA_PYTHON", str(live_python))
    else:
        monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    if (_REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py").is_file():
        monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(_REAL_PLASTICS_PARENT))
    else:
        monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    argv = tea.live_worker_runpy_argv({"target_plastic": "PU"})
    assert argv[1] == "-c"
    assert argv[2] == tea.LIVE_WORKER_RUNPY_BOOTSTRAP
    assert "runpy.run_path" in argv[2]
    provenance, worker_error = tea._tea_worker_source_provenance()
    assert worker_error is None
    result = tea._launch_live_worker(
        {"target_plastic": "PU"},
        timeout_seconds=30,
        environment=tea._tea_worker_environment(),
        provenance=provenance,
    )
    assert result.get("error_type") != "invalid_worker_output"
    assert result["success"] is False
    assert result["error_type"] == "unsupported_live_target"
    assert "PET-clone" in (result.get("error") or "")
    assert result.get("target_plastic") == "PU"


def test_live_engine_status_exercises_runpy_child(monkeypatch):
    """Readiness must start the documented child, not only hash and probe."""
    live_python = _CHECKOUT_TEA_PYTHON
    plastics_ok = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py"
    ).is_file()
    if not live_python.is_file() or not plastics_ok:
        pytest.skip("isolated live interpreter or unpublished plastics missing")
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", str(live_python))
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(_REAL_PLASTICS_PARENT))
    status = tea.live_engine_status()
    assert status["available"] is True
    provenance = status["live_provenance"]
    assert provenance["child_handshake_ok"] is True
    assert provenance["child_handshake_error_type"] == "unsupported_live_target"
    assert provenance["child_handshake_target"] == "PU"
    assert "child handshake" in (status.get("detail") or "")


def test_readiness_fails_closed_when_child_writes_no_json(monkeypatch):
    live_python = _CHECKOUT_TEA_PYTHON
    plastics_ok = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py"
    ).is_file()
    if not live_python.is_file() or not plastics_ok:
        pytest.skip("isolated live interpreter or unpublished plastics missing")
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", str(live_python))
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(_REAL_PLASTICS_PARENT))

    def dead_child(*_args, **_kwargs):
        return {
            "success": False,
            "error": "BioSTEAM worker returned invalid JSON",
            "error_type": "invalid_worker_output",
        }

    monkeypatch.setattr(tea, "_launch_live_worker", dead_child)
    try:
        status = tea.live_engine_status()
        assert status["available"] is False
        assert status["reason"] == "invalid_worker_output"
        assert status["live_provenance"]["child_handshake_ok"] is False
    finally:
        tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()


def test_uninspectable_chemical_rho_forces_provisional(tmp_path):
    _write_matching_pe_package(tmp_path, pe_rho="external_rho()")
    row = params.POLYMERS["LDPE"]
    exact = _committed_pe_toluene_config()
    disagreements = params.surface_package_disagreements(row, "toluene", tmp_path)
    assert "rho_kg_m3_uninspectable" in disagreements
    payload = params.live_parameter_standing_payload(
        row, solvent="toluene", config=exact, plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False
    assert payload["live_parameter_standing"]["package_disagreements"] == (
        list(disagreements)
    )


def test_absent_chemical_rho_forces_provisional(tmp_path):
    _write_matching_pe_package(tmp_path, pe_rho=None)
    row = params.POLYMERS["LDPE"]
    disagreements = params.surface_package_disagreements(
        row, "toluene", tmp_path,
    )
    assert "rho_kg_m3_uninspectable" in disagreements
    payload = params.live_parameter_standing_payload(
        row,
        solvent="toluene",
        config=_committed_pe_toluene_config(),
        plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False


def test_missing_oligomer_draft_is_named_disagreement(tmp_path):
    _write_matching_pe_package(tmp_path)
    strap = tmp_path / "plastics" / "strap"
    (strap / "property_package.py").write_text(
        '''
STRAP_chemicals_outline = bst.ChemicalsOutline([
    bst.ChemicalDraft(
        "PE",
        formula="C2H4",
        rho=0.5 * (880 + 960),
        Cp=0.5 * (1.330 + 2.400),
        Tm=0.5 * (115 + 135) + 273.15,
    ),
])
''',
        encoding="utf-8",
    )
    row = params.POLYMERS["LDPE"]
    disagreements = params.surface_package_disagreements(row, "toluene", tmp_path)
    assert "oligomer_not_in_package_source" in disagreements
    payload = params.live_parameter_standing_payload(
        row,
        solvent="toluene",
        config=_committed_pe_toluene_config(),
        plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False


@pytest.mark.parametrize(
    "filename",
    ("dissolution_steps.py", "precipitation_steps.py"),
)
def test_doctor_malformed_cited_step_is_named_fail(
    tmp_path, monkeypatch, filename,
):
    root = tmp_path / "plastics-root"
    _write_matching_pe_package(root)
    strap = root / "plastics" / "strap"
    (strap / filename).write_text("def (\n", encoding="utf-8")
    (strap / "process_model.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = tea.live_environment_report()
    assert report["reason"] == "cited_package_unreadable"
    assert report["check_status"] == "fail"
    assert str(strap / filename) in (report["why_unavailable"] or "")
    assert "Traceback" not in (report["why_unavailable"] or "")
    doctor = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in doctor["checks"] if c["name"] == "Live TEA")
    assert check["status"] == "fail"
    assert check["reason"] == "cited_package_unreadable"
    assert str(strap / filename) in (check["detail"] or "")


@pytest.mark.parametrize(
    "filename",
    ("dissolution_steps.py", "precipitation_steps.py"),
)
def test_worker_malformed_cited_step_is_named_refusal(
    tmp_path, monkeypatch, filename,
):
    root = tmp_path / "plastics-root"
    _write_matching_pe_package(root)
    strap = root / "plastics" / "strap"
    (strap / filename).write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    result = tea_worker.run({"target_plastic": "LDPE"})
    assert result["success"] is False
    assert result["error_type"] == "cited_package_unreadable"
    assert str(strap / filename) in result["error"]


def test_live_engine_status_parses_cited_steps_before_ready(tmp_path, monkeypatch):
    real_model = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "process_model.py"
    )
    if not real_model.is_file():
        pytest.skip("unpublished process_model.py is not on this machine")
    _write_matching_pe_package(tmp_path)
    strap = tmp_path / "plastics" / "strap"
    shutil.copy2(real_model, strap / "process_model.py")
    (strap / "dissolution_steps.py").write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", sys.executable)
    status = tea.live_engine_status()
    assert status["available"] is False
    assert status["reason"] == "cited_package_unreadable"
    assert "dissolution_steps.py" in (status.get("detail") or "")


def test_worker_admitted_target_is_named_python_version(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    monkeypatch.setattr(tea_worker.sys, "version_info", (3, 11, 14))
    monkeypatch.setattr(
        tea_worker.platform, "python_version", lambda: "3.11.14",
    )
    result = tea_worker.run({"target_plastic": "LDPE"})
    assert result["success"] is False
    assert result["error_type"] == "python_version"
    assert "3.11.14" in result["error"]
    assert "Traceback" not in result["error"]


def test_worker_python_guard_does_not_fire_on_3_12_predicate(monkeypatch):
    """The 3.12 predicate must pass the guard without depending on plastics.

    Passing must not be an ambient ModuleNotFoundError. Patch the first
    post-guard seam to a sentinel and assert that result.
    """
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    monkeypatch.setattr(tea_worker.sys, "version_info", (3, 12, 13))
    monkeypatch.setattr(
        tea_worker.platform, "python_version", lambda: "3.12.13",
    )
    real_import = tea_worker.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "plastics.strap":
            return SimpleNamespace(STRAP_chemicals_outline=[])
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(tea_worker.importlib, "import_module", fake_import)
    monkeypatch.setattr(
        tea_worker,
        "_verify_loaded_live_provenance",
        lambda _expectations: (
            {"status": "sentinel"},
            "post_guard_sentinel",
            "python version guard did not fire",
        ),
    )
    result = tea_worker.run({"target_plastic": "LDPE"})
    assert result["success"] is False
    assert result["error_type"] == "post_guard_sentinel"
    assert result["error_type"] != "python_version"
    assert result["error"] == "python version guard did not fire"


def test_readiness_rejects_resolved_python_below_3_12(tmp_path, monkeypatch):
    real_model = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "process_model.py"
    )
    if not real_model.is_file():
        pytest.skip("unpublished process_model.py is not on this machine")
    _write_matching_pe_package(tmp_path)
    shutil.copy2(real_model, tmp_path / "plastics" / "strap" / "process_model.py")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", sys.executable)
    expected = tea._expected_live_runtime_versions()

    def fake_probe(_python):
        return {
            "python": "3.11.14",
            **expected,
        }, None

    monkeypatch.setattr(tea, "_probe_live_runtime_versions", fake_probe)
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    status = tea.live_engine_status()
    assert status["available"] is False
    assert status["reason"] == "python_version"
    assert "3.11.14" in (status.get("detail") or "")
    assert not (status.get("live_provenance") or {}).get("child_handshake_ok")
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = tea.live_environment_report()
    assert report["available"] is False
    assert report["reason"] == "python_version"
    assert report["check_status"] == "fail"


def test_group_meeting_interpreter_is_not_ready(monkeypatch):
    interpreter = (Path.home() / "anaconda3/envs/group-meeting/bin/python")  # Python 3.11 with the TEA packages
    plastics = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py"
    )
    if not interpreter.is_file() or not plastics.is_file():
        pytest.skip("group-meeting interpreter or unpublished plastics missing")
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", str(interpreter))
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(_REAL_PLASTICS_PARENT))
    status = tea.live_engine_status()
    assert status["available"] is False
    assert status["reason"] == "python_version"
    resolved = (status.get("live_provenance") or {}).get(
        "resolved_runtime_versions"
    ) or {}
    assert str(resolved.get("python") or "").startswith("3.11")


def test_unreadable_cited_source_is_named_not_traceback(tmp_path, monkeypatch):
    root = tmp_path / "plastics-root"
    _write_matching_pe_package(root)
    strap = root / "plastics" / "strap"
    (strap / "process_model.py").write_text("# stub\n", encoding="utf-8")
    target = strap / "dissolution_steps.py"
    original = Path.read_bytes

    def boom(self):
        if Path(self).resolve() == target.resolve():
            raise PermissionError("injected")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = tea.live_environment_report()
    assert report["reason"] == "cited_package_unreadable"
    assert report["check_status"] == "fail"
    assert str(target) in (report["why_unavailable"] or "")
    assert "Traceback" not in (report["why_unavailable"] or "")
    result = tea_worker.run({"target_plastic": "LDPE"})
    assert result["success"] is False
    assert result["error_type"] == "cited_package_unreadable"
    assert str(target) in result["error"]
    assert "Traceback" not in result["error"]
    real_model = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "process_model.py"
    )
    if real_model.is_file():
        original_copy = original
        shutil.copy2(real_model, strap / "process_model.py")

        def boom_status(self):
            if Path(self).resolve() == target.resolve():
                raise PermissionError("injected")
            return original_copy(self)

        monkeypatch.setattr(Path, "read_bytes", boom_status)
        monkeypatch.setenv("DISSOLVE_TEA_PYTHON", sys.executable)
        status = tea.live_engine_status()
        assert status["available"] is False
        assert status["reason"] == "cited_package_unreadable"


def test_unreadable_process_model_is_unverifiable_not_mismatch(tmp_path, monkeypatch):
    real_model = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "process_model.py"
    )
    if not real_model.is_file():
        pytest.skip("unpublished process_model.py is not on this machine")
    _write_matching_pe_package(tmp_path)
    dest = tmp_path / "plastics" / "strap" / "process_model.py"
    shutil.copy2(real_model, dest)
    original = Path.read_bytes

    def boom(self):
        if Path(self).resolve() == dest.resolve():
            raise PermissionError("injected")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", sys.executable)
    status = tea.live_engine_status()
    assert status["available"] is False
    assert status["reason"] == "process_model_unreadable"
    assert status["live_provenance"]["status"] == "unverifiable"
    assert status["live_provenance"]["unreadable_source"] == str(dest.resolve())


def test_unreadable_worker_source_is_named_parent_and_public(tmp_path, monkeypatch):
    real_model = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "process_model.py"
    )
    if not real_model.is_file():
        pytest.skip("unpublished process_model.py is not on this machine")
    _write_matching_pe_package(tmp_path)
    shutil.copy2(real_model, tmp_path / "plastics" / "strap" / "process_model.py")
    worker = Path(tea_worker.__file__).resolve()
    original = Path.read_bytes

    def boom(self):
        if Path(self).resolve() == worker:
            raise PermissionError("injected")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", sys.executable)
    expected = tea._expected_live_runtime_versions()
    monkeypatch.setattr(
        tea,
        "_probe_live_runtime_versions",
        lambda _python: ({"python": "3.12.13", **expected}, None),
    )
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    status = tea.live_engine_status()
    assert status["available"] is False
    assert status["reason"] == "worker_source_unreadable"
    assert status["live_provenance"]["status"] == "unverifiable"
    assert str(worker) in (status.get("detail") or "")
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = tea.live_environment_report()
    assert report["reason"] == "worker_source_unreadable"
    assert report["check_status"] == "fail"
    raw = tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": "PU",
            "solvent": "toluene",
            "target_mass_percent": 60.0,
            "processing_capacity_mt_per_yr": 20_000.0,
            "energy_case": "C1",
            "dissolution_temp_c": 95.0,
            "precipitation_temp_c": 35.0,
            "solvent_price": 1.312,
            "solvent_loss_pct": 0.01,
            "feedstock_distance_km": 0.0,
            "dissolution_capacity": 3.0,
            "labor_cost": 120_000.0,
        }],
        engine_mode="live",
        confirm_live_tea=True,
        timeout_seconds=30,
    )
    parsed = json.loads(raw)
    data = parsed["data"]
    assert data["success"] is False
    failures = data.get("failures") or []
    assert any(
        row.get("error_type") == "worker_source_unreadable" for row in failures
    )
    assert "Traceback" not in json.dumps(parsed)


def test_loaded_worker_source_read_failure_is_named(monkeypatch):
    worker = Path(tea_worker.__file__).resolve()
    original = Path.read_bytes

    def boom(self):
        if Path(self).resolve() == worker:
            raise PermissionError("injected")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    provenance, error_type, error = tea_worker._verify_loaded_live_provenance({
        "runtime_versions": {
            name: "0" for name in tea_worker._LIVE_RUNTIME_MODULES
        },
        "process_model_sha256": "abc",
        "worker_source_path": str(worker),
        "worker_source_sha256": "def",
        "cited_package_sha256": {
            name: "x" for name in params.CITED_STRAP_SOURCE_NAMES
        },
    })
    assert error_type == "worker_source_unreadable"
    assert provenance["status"] == "unverifiable"
    assert provenance["unreadable_source"] == str(worker)
    assert "Traceback" not in (error or "")


# --- from test_stage1_tea.py: Stage-1 TEA: shortlist of m first-step items, quote, then economics rank.
_SECONDS = 15.132821729521634


_PLAN = "plan_multistage_separation"


_TRIPLE = ["LDPE", "PP", "PS"]


_PAIR = ["LDPE", "PP"]


def _record_by_label(label: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == label
    )


def _admitted_ldpe_trio():
    return (
        _record_by_label("ldpe-dissolution-low"),
        _record_by_label("ldpe-route-c1"),
        _record_by_label("ldpe-dissolution-high"),
    )


def test_registry_stays_two_public_tea_names():
    names = [item["name"] for item in tool_schemas() if item["name"] != "result_read"]
    assert "evaluate_process" in names
    assert "rank_landscape" in names
    assert "plan_then_tea" not in names
    assert names.count("evaluate_process") == 1
    assert len(agent.REGISTRY) == 31
    assert "fetch_solvent_safety_by_cid" in agent.BY_NAME
    assert "estimate_thermal_properties" not in agent.BY_NAME
    assert UNWIRED == frozenset()


def test_shortlist_of_three_without_held_names_the_nine(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(
            _item_from_record(low),
            _item_from_record(mid),
            _item_from_record(high),
        ),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "screening_basis_incomplete"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert payload.get("error_code") != "tool_not_wired"
    assert "comparison_rows" not in payload
    assert payload.get("tool_name") == "evaluate_process"


def test_feed_basis_cache_serves_admitted_and_fails_unadmitted(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    _live_run_fails(monkeypatch)
    held = _held_from_record(mid)
    miss = _item_from_record(mid, dissolution_temperature_c=999.0)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(
            _item_from_record(low, thermo_rank=3),
            _item_from_record(mid, thermo_rank=2),
            miss,
        ),
        held_process_basis=held,
        engine_mode="auto",
        confirm_live_tea=True,
    ))
    rows = list(payload.get("comparison_rows") or payload.get("failures") or [])
    assert len(rows) == 3
    assert payload.get("error_code") != "tool_not_wired"
    served = [row for row in rows if row.get("success") is True]
    failed = [row for row in rows if row.get("success") is not True]
    assert len(served) == 2
    assert len(failed) == 1
    assert failed[0].get("error_type") == "timeout"
    assert failed[0].get("msp_usd_per_kg") is None
    origins = served[0]["field_origin"]
    assert origins["target_polymer"] == "from_screen"
    assert origins["solvent"] == "from_screen"
    assert origins["dissolution_temperature_c"] == "from_screen"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origins[name] == "supplied"
        assert origins[name] != "from_screen"
    by_t = {row["dissolution_temperature_c"]: row for row in served}
    assert by_t[low["config"]["dissolution_temperature_c"]]["original_thermo_rank"] == 3
    assert by_t[mid["config"]["dissolution_temperature_c"]]["original_thermo_rank"] == 2


def test_unadmitted_auto_without_confirm_quotes_and_starts_no_child(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    calls = []

    def forbidden(config, timeout_seconds):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    miss_a = _item_from_record(mid, dissolution_temperature_c=998.0, thermo_rank=1)
    miss_b = _item_from_record(high, dissolution_temperature_c=999.0, thermo_rank=2)
    hit = _item_from_record(low, thermo_rank=3)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(miss_a, miss_b, hit),
        held_process_basis=_held_from_record(mid),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "auto"
    assert payload.get("n_live") == 2
    assert payload.get("n_cache") == 1
    assert payload.get("seconds_per_pair") == pytest.approx(_SECONDS)
    assert payload.get("estimated_wall_seconds") == pytest.approx(2 * _SECONDS)
    assert payload.get("per_stage_per_ordering") is True
    assert payload.get("tool_name") == "evaluate_process"
    assert calls == []
    omitted = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(miss_a, miss_b, hit),
        held_process_basis=_held_from_record(mid),
        engine_mode="auto",
        confirm_live_tea=False,
    ))
    assert omitted.get("error_code") == "live_tea_cost_confirmation_required"
    assert omitted.get("n_live") == 2
    assert calls == []


def test_confirm_live_tea_runs_children_one_at_a_time(monkeypatch):
    _mid = _record_by_label("ldpe-route-c1")
    order = []

    def planted(config, timeout_seconds):
        order.append(config.get("dissolution_temperature_c"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    items = [
        _item_from_record(_mid, dissolution_temperature_c=997.0, thermo_rank=1),
        _item_from_record(_mid, dissolution_temperature_c=998.0, thermo_rank=2),
        _item_from_record(_mid, dissolution_temperature_c=999.0, thermo_rank=3),
    ]
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(*items),
        held_process_basis=_held_from_record(_mid),
        engine_mode="auto",
        confirm_live_tea=True,
    ))
    assert order == [997.0, 998.0, 999.0]
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"
    assert payload.get("error_code") == "no_simulation_result" or payload.get(
        "failed"
    ) == 3


def test_confirm_live_tea_on_process_configs_quotes_live_without_starting(
    monkeypatch,
):
    record = _record_by_label("ldpe-route-c1")
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    twelve = _public_from_record(record)
    quoted = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert quoted.get("error_code") == "live_tea_cost_confirmation_required"
    assert quoted.get("engine_mode") == "live"
    assert quoted.get("n_live") == 1
    assert quoted.get("n_cache") == 0
    assert quoted.get("per_stage_per_ordering") is True
    assert calls == []
    omitted = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="live",
    ))
    assert omitted.get("error_code") == "live_tea_cost_confirmation_required"
    assert omitted.get("engine_mode") == "live"
    assert calls == []
    cache_path = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="auto",
        confirm_live_tea=True,
    ))
    assert cache_path.get("success") is True
    assert cache_path.get("error_code") != "live_tea_cost_confirmation_required"
    assert cache_path.get("error_code") != "not_applicable_in_mode"


def test_confirm_live_tea_false_on_empty_scenarios_is_not_inapplicable():
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [], confirm_live_tea=False,
    ))
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") != ["confirm_live_tea"]


def test_scenarios_live_without_confirm_quotes_and_starts_no_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "live"
    assert payload.get("n_live") == 1
    assert payload.get("n_cache") == 0
    assert payload.get("seconds_per_pair") == pytest.approx(_SECONDS)
    assert calls == []


def test_scenarios_confirm_live_tea_runs_children_one_at_a_time(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    order = []

    def planted(config, timeout_seconds=None, **_kwargs):
        order.append(config.get("dissolution_temperature_c"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    first = _public_from_record(record, dissolution_temperature_c=997.0)
    second = _public_from_record(record, dissolution_temperature_c=998.0)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [first, second],
        engine_mode="live",
        confirm_live_tea=True,
    ))
    assert order == [997.0, 998.0]
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"


def _one_step_route_from_record(record: dict) -> tuple[dict, dict]:
    cfg = record["config"]
    polymer = cfg["target_plastic"]
    composition = {polymer: 1.0}
    route = {
        "complete": True,
        "final_residue": None,
        "steps": [{
            "dissolved_polymer": polymer,
            "solvent": cfg["solvent"],
            "temperature_c": cfg["dissolution_temperature_c"],
        }],
    }
    return composition, route


def test_stored_route_live_without_confirm_quotes_and_starts_no_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    cfg = record["config"]
    composition, route = _one_step_route_from_record(record)
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    monkeypatch.setattr(
        tea,
        "current_tool_session",
        lambda: SimpleNamespace(
            last_route=copy.deepcopy(route),
            feed_mass_fractions=dict(composition),
        ),
    )
    payload = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(composition),
        processing_capacity_mt_per_yr=cfg["processing_capacity"],
        energy_case=cfg["energy_case"],
        precipitation_temperature_c=cfg["precipitation_temperature_c"],
        engine_mode="live",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "live"
    assert payload.get("n_live") == 1
    assert payload.get("n_cache") == 0
    assert payload.get("per_stage_per_ordering") is True
    assert calls == []
    omitted = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(composition),
        processing_capacity_mt_per_yr=cfg["processing_capacity"],
        energy_case=cfg["energy_case"],
        precipitation_temperature_c=cfg["precipitation_temperature_c"],
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert omitted.get("error_code") == "live_tea_cost_confirmation_required"
    assert omitted.get("inapplicable_fields") != ["confirm_live_tea"]
    assert calls == []


def test_stored_route_confirm_live_tea_proceeds_without_a_real_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    cfg = record["config"]
    composition, route = _one_step_route_from_record(record)
    order = []

    def planted(config, timeout_seconds=None, **_kwargs):
        order.append(config.get("dissolution_temperature_c"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    monkeypatch.setattr(
        tea,
        "current_tool_session",
        lambda: SimpleNamespace(
            last_route=copy.deepcopy(route),
            feed_mass_fractions=dict(composition),
        ),
    )
    payload = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(composition),
        processing_capacity_mt_per_yr=cfg["processing_capacity"],
        energy_case=cfg["energy_case"],
        precipitation_temperature_c=cfg["precipitation_temperature_c"],
        engine_mode="live",
        confirm_live_tea=True,
    ))
    assert order == [cfg["dissolution_temperature_c"]]
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"


def test_sensitivity_live_without_confirm_quotes_and_starts_no_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    baseline_price = float(record["config"]["solvent_price"])
    requested = [0.5, 1.5]
    expected_values = list(dict.fromkeys([baseline_price, *requested]))
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        values=requested,
        engine_mode="live",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "live"
    assert payload.get("n_live") == len(expected_values)
    assert payload.get("n_cache") == 0
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") != ["confirm_live_tea"]
    assert calls == []
    accepted = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        values=requested,
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert accepted.get("error_code") == "live_tea_cost_confirmation_required"
    assert accepted.get("inapplicable_fields") != ["confirm_live_tea"]
    assert calls == []


def test_sensitivity_confirm_live_tea_proceeds_one_at_a_time(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    baseline_price = float(record["config"]["solvent_price"])
    requested = [0.5, 1.5]
    expected_values = list(dict.fromkeys([baseline_price, *requested]))
    order = []

    def planted(config, timeout_seconds=None, **_kwargs):
        order.append(config.get("solvent_price"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        values=requested,
        engine_mode="live",
        confirm_live_tea=True,
    ))
    assert order == expected_values
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"


def test_process_rows_rank_keeps_thermo_rank_and_stamps_disagreement(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist=_shortlist(
                _item_from_record(high, thermo_rank=1),
                _item_from_record(low, thermo_rank=2),
                _item_from_record(mid, thermo_rank=3),
            ),
            held_process_basis=_held_from_record(mid),
            engine_mode="auto",
        ))
        assert first.get("success") is True
        assert [row.get("original_thermo_rank") for row in first["comparison_rows"]] == [
            1, 2, 3,
        ]
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data(tea.rank_landscape(
            source="process_rows",
            operation="pareto_dominance",
            handle=handle,
        ))
        sorted_payload = _data(tea.rank_landscape(
            source="process_rows",
            operation="sort",
            objective="msp_usd_per_kg",
            handle=handle,
        ))
    assert payload.get("success") is True
    points = payload["landscape_points"]
    frontier = payload["frontier_points"]
    assert payload["n_landscape_points"] == len(points) == 3
    assert payload["n_frontier_points"] == len(frontier)
    assert payload["n_frontier_points"] >= 1
    assert {point["original_thermo_rank"] for point in points} == {1, 2, 3}
    assert {point["rank"] for point in points} == {1, 2, 3}
    thermo_best = next(point for point in points if point["original_thermo_rank"] == 1)
    assert thermo_best["rank"] != 1
    assert thermo_best["thermo_econ_rank_disagreement"] is True
    for point in points:
        assert point["thermo_econ_rank_disagreement"] is (
            point["original_thermo_rank"] != point["rank"]
        )
    cheap = min(points, key=lambda point: float(point["msp_usd_per_kg"]))
    assert cheap["rank"] == 1
    assert cheap["original_thermo_rank"] == 2
    sort_points = sorted_payload["landscape_points"]
    assert [point["rank"] for point in sort_points] == [1, 2, 3]
    assert sort_points[0]["msp_usd_per_kg"] <= sort_points[1]["msp_usd_per_kg"]


def test_missing_thermo_rank_is_null_and_cannot_claim_disagreement(monkeypatch):
    low, mid, _high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist=_shortlist(
                _item_from_record(low),
                _item_from_record(mid),
            ),
            held_process_basis=_held_from_record(mid),
            engine_mode="auto",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data(tea.rank_landscape(
            source="process_rows",
            operation="sort",
            handle=handle,
        ))
    points = payload["landscape_points"]
    assert all(point.get("original_thermo_rank") is None for point in points)
    assert all(point.get("thermo_econ_rank_disagreement") is False for point in points)


def test_planner_names_stage1_shortlists_from_keep_set_not_beam(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner must not start TEA")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "evaluate_process", forbidden)
    monkeypatch.setattr(tea, "evaluate_tea_lca_scenarios", forbidden)
    session = new_session()
    with bind_tool_session(session):
        out = dispatch(
            _PLAN,
            feed_polymers=_TRIPLE,
            breadth=5,
        )
        stored = load_handle(session, out["handle"])
        exact = stored["exact"]
    shortlists = exact.get("stage1_shortlists")
    assert isinstance(shortlists, list)
    assert shortlists
    max_keep = 0
    for block in shortlists:
        items = block.get("items") or []
        assert items
        ranks = [item.get("thermo_rank") for item in items]
        assert ranks == list(range(1, len(items) + 1))
        for item in items:
            assert item.get("target_polymer") == block.get("target_polymer")
            assert item.get("solvent")
            assert item.get("dissolution_temperature_c") is not None
        max_keep = max(max_keep, len(items))
    published = set()
    for route in exact.get("top_k_sequences") or []:
        steps = route.get("steps") or []
        if not steps:
            continue
        step = steps[0]
        published.add((
            step.get("dissolved_polymer"),
            step.get("solvent"),
            step.get("temperature_c"),
        ))
    assert max_keep > 1
    visible = out.get("data") or {}
    assert "stage1_shortlists" in visible
    assert "top_k_sequences" not in visible
    assert exact.get("top_k_sequences")


def test_residue_polymer_that_is_not_a_first_stage_is_residue_was_costed(
    monkeypatch,
):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        planned = dispatch(_PLAN, feed_polymers=_PAIR, breadth=5)
        handle = planned["handle"]
        exact = load_handle(session, handle)["exact"]
        residue = exact["final_residue"]
        first = {
            (item.get("target_polymer"), item.get("solvent"), item.get("dissolution_temperature_c"))
            for block in exact.get("stage1_shortlists") or []
            for item in (block.get("items") or [])
        }
        payload = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist={
                "source": "plan_multistage_separation",
                "handle": handle,
                "items": [{
                    "target_polymer": residue,
                    "solvent": record["config"]["solvent"],
                    "dissolution_temperature_c": record["config"][
                        "dissolution_temperature_c"
                    ],
                    "thermo_rank": 1,
                }],
            },
            held_process_basis=_held_from_record(record),
            engine_mode="auto",
        ))
    assert (residue, record["config"]["solvent"], record["config"][
        "dissolution_temperature_c"
    ]) not in first
    assert payload.get("error_code") == "residue_was_costed"
    assert payload.get("polymer") == residue
    assert payload.get("final_residue") == residue
    assert "comparison_rows" not in payload


def test_later_stage_identity_at_feed_basis_refuses(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        planned = dispatch(_PLAN, feed_polymers=_TRIPLE, breadth=1)
        handle = planned["handle"]
        exact = load_handle(session, handle)["exact"]
        residue = tea._key(exact.get("final_residue"))
        first = set()
        for block in exact.get("stage1_shortlists") or []:
            for item in block.get("items") or []:
                first.add((
                    item.get("target_polymer"),
                    item.get("solvent"),
                    item.get("dissolution_temperature_c"),
                ))
        later_item = None
        for route in exact.get("top_k_sequences") or []:
            for step in (route.get("steps") or [])[1:]:
                ident = (
                    step.get("dissolved_polymer"),
                    step.get("solvent"),
                    step.get("temperature_c"),
                )
                if ident in first:
                    continue
                if tea._key(ident[0]) == residue:
                    continue
                later_item = ident
                break
            if later_item:
                break
        assert later_item is not None
        payload = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist={
                "source": "plan_multistage_separation",
                "handle": handle,
                "items": [{
                    "target_polymer": later_item[0],
                    "solvent": later_item[1],
                    "dissolution_temperature_c": later_item[2],
                    "thermo_rank": 1,
                }],
            },
            held_process_basis=_held_from_record(record),
            engine_mode="auto",
        ))
    assert payload.get("error_code") == "stage_basis_not_derived"
    assert payload.get("named_blocker") == "incomplete_stage_basis_grid"
    assert "comparison_rows" not in payload


def test_unadmitted_rows_quote_nothing_until_live_is_confirmed(monkeypatch):
    mid = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(
            _item_from_record(mid, dissolution_temperature_c=999.0, thermo_rank=1),
            _item_from_record(mid, dissolution_temperature_c=998.0, thermo_rank=2),
        ),
        held_process_basis=_held_from_record(mid),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert (payload.get("n_live"), payload.get("n_cache")) == (2, 0)
    assert "msp_usd_per_kg" not in json.dumps(payload)


# --- from test_lca_cf_gap.py: C-1: describe the two CF gaps; do not admit rows or move the C1/C2/C3 refuse.
_ROOT = Path(__file__).resolve().parents[1]


_OLD_LCA_FACTORS_SHA256 = (
    "adba150af8192c57ccbd1eb3fe550206598ad7eda1f1e4abb49d07d636ea2adf"
)


_LCA_FACTORS_SHA256 = (
    "825e9e02a9366250ef0357407cc26bea1ef4ee04aa12720c3892e9eb44d8a083"
)


_ASSET_RELATIVE = "src/dissolve/data/tea_lca_characterization_factors.json"


_UNMAPPED_SOLVENTS = ("Acetaldehyde", "Triethylamine")


_ENERGY_CASES = ("C1", "C2", "C3")


_ADMITTED_SOLVENT_KEYS = frozenset({
    "1,3-Benzenediol",
    "1,4-Dioxane",
    "1-Butanol",
    "1-Propanol",
    "2,3-Dihydropyran",
    "2,4-Pentanedione",
    "2-Propanol",
    "Acetic Acid",
    "Acetic Anhydride",
    "Acetone",
    "Acetonitrile",
    "Benzaldehyde",
    "Benzene",
    "Benzonitrile",
    "Camphene",
    "Carbon Disulfide (0 Dipole Moment)",
    "Carbon Tetrachloride (0 Dipole Moment)",
    "Chlorobenzene",
    "Chloroform",
    "Cyclohexane",
    "Cyclohexanol",
    "Di-(2-Methoxyethyl) Ether",
    "Dichloromethane",
    "Diethyl Ether",
    "Diethylene Glycol",
    "Dimethyl Cellosolve",
    "Dimethyl sulfoxide",
    "Dipentene (Dl-Limonene)",
    "Diphenyl ether",
    "Dodecane",
    "Ethanol",
    "Ethyl Aceto Acetate (Keto)",
    "Ethyl acetate",
    "Ethylene Dichloride",
    "Ethylene glycol",
    "Heptane",
    "Hexamethylphosphoramide",
    "Hexane",
    "Isophorone",
    "Isopropylamine",
    "Methanol",
    "Methyl acetate",
    "Methyl ethyl ketone",
    "Methyl-t-Butyl Ether",
    "N,N-Dimethylformamide",
    "N-Methyl-2-Pyrrolidone (NMP)",
    "Naphthalene",
    "Nitromethane",
    "Propylene Carbonate",
    "Propylene glycol",
    "Pyridine",
    "Pyrrole",
    "Styrene",
    "Tetrahydrofuran (THF)",
    "Tetrahydropyran",
    "Toluene",
    "Water",
    "p-Xylene",
    "tert-Butanol",
})


_MEASURED_UNMAPPED = [
    [
        "triethylamine",
        "Triethylamine",
        "source row carries GWP only, no HTC/HTNC/ETOX, lca_confidence low",
    ],
    [
        "acetaldehyde",
        "acetaldehyde",
        "no row in the pinned CF source solvent-econ-lca-summary.csv "
        "under name, synonym ethanal, or CAS 75-07-0",
    ],
]


def _committed_factors() -> dict:
    raw = subprocess.check_output(
        ["git", "show", f"HEAD:{_ASSET_RELATIVE}"],
        cwd=_ROOT,
    )
    return json.loads(raw)


def test_lca_factor_asset_digest_matches_the_c1_pin():
    digest = hashlib.sha256(tea._LCA_FACTORS_ASSET.read_bytes()).hexdigest()
    assert digest == _LCA_FACTORS_SHA256
    assert tea._LCA_FACTORS_ASSET_SHA256 == _LCA_FACTORS_SHA256
    assert digest != _OLD_LCA_FACTORS_SHA256
    tea.lca_factor_payload.cache_clear()
    payload = tea.lca_factor_payload()
    assert payload["unmapped_source_rows"] == _MEASURED_UNMAPPED


@pytest.mark.parametrize("solvent", _UNMAPPED_SOLVENTS)
@pytest.mark.parametrize("energy_case", _ENERGY_CASES)
def test_a3_unmapped_solvents_still_refuse_at_c1_c2_c3(
    monkeypatch, solvent, energy_case,
):
    config = {"solvent": solvent, "energy_case": energy_case}
    with pytest.raises(ValueError, match="No governed cache-generator LCA factors"):
        tea._default_live_lca_cfs(config)
    with pytest.raises(ValueError, match="No governed cache-generator LCA factors"):
        tea._governed_live_lca_application(config)

    monkeypatch.setattr(tea, "live_engine_status", lambda: {
        "available": True,
        "live_provenance": {"status": "test-stub"},
    })
    result = tea._live(config, timeout_seconds=1)
    assert result["success"] is False
    assert result["error_type"] == "lca_factor_basis_unavailable"


def test_a3_cf_lookup_stays_outside_the_c1_branch():
    source = inspect.getsource(tea._governed_live_lca_application)
    lookup_at = source.find("recorded = _default_live_lca_cfs(config)")
    c1_at = source.find('if energy_case == "C1":')
    assert lookup_at != -1
    assert c1_at != -1
    assert lookup_at < c1_at


def test_a4_cf_solvent_key_set_is_identity_unchanged():
    tea.lca_factor_payload.cache_clear()
    payload = tea.lca_factor_payload()
    served = set(payload["solvents"])
    committed = set(_committed_factors()["solvents"])
    assert served - committed == set()
    assert committed - served == set()
    assert served == committed == _ADMITTED_SOLVENT_KEYS
    assert "Acetaldehyde" not in served
    assert "Triethylamine" not in served
    assert "acetaldehyde" not in served
    assert "triethylamine" not in served
    assert len(served) == 59
    tiers = Counter(row[1] for row in payload["solvents"].values())
    assert tiers == {
        "generator_validated_table": 27,
        "generator_class_average": 23,
        "generator_curated_with_class_fallbacks": 9,
    }
    assert sum(tiers.values()) == 59


# --- from test_nonfinite_served_tea.py: Refuse a non-finite served TEA metric instead of success-with-null.
def _plant_success_with_null_msp(
    monkeypatch,
    *,
    error=None,
    poison=None,
    msp=None,
):
    real_run = tea._run

    def planted(config, engine_mode, timeout_seconds):
        result = copy.deepcopy(real_run(config, engine_mode, timeout_seconds))
        if poison is not None and not poison(config, result):
            return result
        planted_result = dict(result)
        tea_block = dict(result.get("tea") or {})
        tea_block["msp_usd_per_kg"] = msp
        planted_result["tea"] = tea_block
        planted_result["success"] = True
        planted_result.pop("error", None)
        planted_result.pop("error_type", None)
        planted_result.pop("nonfinite_served_metrics", None)
        planted_result.pop("underlying_errors", None)
        if error is not None:
            planted_result["error"] = error
        return planted_result

    monkeypatch.setattr(tea, "_run", planted)


def _plant_success_with_null_gwp(monkeypatch, *, poison=None):
    real_run = tea._run

    def planted(config, engine_mode, timeout_seconds):
        result = copy.deepcopy(real_run(config, engine_mode, timeout_seconds))
        if poison is not None and not poison(config, result):
            return result
        planted_result = dict(result)
        lca_block = dict(result.get("lca") or {})
        lca_block["gwp_kg_co2e_per_kg"] = None
        planted_result["lca"] = lca_block
        planted_result["success"] = True
        planted_result.pop("error", None)
        planted_result.pop("error_type", None)
        planted_result.pop("nonfinite_served_metrics", None)
        planted_result.pop("underlying_errors", None)
        return planted_result

    monkeypatch.setattr(tea, "_run", planted)


def _two_c1_records():
    c1 = _record_with_energy("C1")
    sibling = next(
        item for item in tea._records()
        if str(item["config"].get("energy_case") or "").upper() == "C1"
        and item["config"]["target_plastic"] != c1["config"]["target_plastic"]
    )
    return c1, sibling


def test_served_tea_value_keeps_cashflow_runtime_error():
    def boom():
        raise RuntimeError("nan encountered in cashflow array")

    value, exc_name, context = tea_worker._served_tea_value(boom)
    assert value is None
    assert exc_name == "RuntimeError"
    assert "nan encountered in cashflow array" in context
    refusal = tea_worker._nonfinite_served_tea_refusal(
        ["msp_usd_per_kg"],
        [f"{exc_name}: {context}"],
    )
    assert refusal["error_type"] == "tea_cashflow_undefined"
    assert "nan encountered in cashflow array" in refusal["error"]
    assert refusal["nonfinite_served_metrics"] == ["msp_usd_per_kg"]


def test_served_tea_value_rejects_missing_and_nonfinite():
    for raw in (None, float("nan"), float("inf"), float("-inf"), "not-a-number"):
        value, _exc_name, context = tea_worker._served_tea_value(raw)
        assert value is None
        assert context
    finite, exc_name, context = tea_worker._served_tea_value(1.25)
    assert finite == 1.25
    assert exc_name is None
    assert context is None


def test_safe_still_swallows_for_equipment():
    assert tea_worker._safe(lambda: 1.5) == 1.5
    assert tea_worker._safe(lambda: float("nan")) is None
    assert tea_worker._safe(
        lambda: (_ for _ in ()).throw(RuntimeError("equipment blew up")),
    ) is None


def test_nonfinite_tci_without_msp_is_tea_cashflow_undefined():
    refusal = tea_worker._nonfinite_served_tea_refusal(
        ["tci_usd", "aoc_usd_per_yr"],
        ["served metric was None"],
    )
    assert refusal["error_type"] == "tea_cashflow_undefined"


def test_coerce_binds_to_the_metric_not_the_input_mass_percent():
    missing = tea._coerce_nonfinite_served_tea({
        "success": True,
        "tea": {
            "msp_usd_per_kg": None,
            "tci_usd": 1.0,
            "aoc_usd_per_yr": 1.0,
        },
        "config": {"target_plastic_percent": 55.0},
    })
    assert missing["success"] is False
    assert missing["error_type"] == "no_finite_msp"
    assert missing["nonfinite_served_metrics"] == ["msp_usd_per_kg"]

    cashflow = tea._coerce_nonfinite_served_tea({
        "success": True,
        "error": "RuntimeError: nan encountered in cashflow array",
        "tea": {
            "msp_usd_per_kg": None,
            "tci_usd": None,
            "aoc_usd_per_yr": None,
        },
        "config": {"target_plastic_percent": 55.0},
    })
    assert cashflow["success"] is False
    assert cashflow["error_type"] == "tea_cashflow_undefined"
    assert "nan encountered in cashflow array" in cashflow["error"]

    finite_at_hundred = tea._coerce_nonfinite_served_tea({
        "success": True,
        "tea": {
            "msp_usd_per_kg": 1.2,
            "tci_usd": 3.0,
            "aoc_usd_per_yr": 4.0,
        },
        "config": {"target_plastic_percent": 100.0},
    })
    assert finite_at_hundred["success"] is True
    already = {"success": False, "error_type": "timeout"}
    assert tea._coerce_nonfinite_served_tea(already) is already
    assert tea._coerce_nonfinite_served_gwp(already) is already

    gwp_missing = tea._coerce_nonfinite_served_gwp({
        "success": True,
        "tea": {
            "msp_usd_per_kg": 1.2,
            "tci_usd": 3.0,
            "aoc_usd_per_yr": 4.0,
        },
        "lca": {"gwp_kg_co2e_per_kg": None},
    })
    assert gwp_missing["success"] is False
    assert gwp_missing["error_type"] == "no_finite_gwp"
    assert gwp_missing["nonfinite_served_metrics"] == ["gwp_kg_co2e_per_kg"]

    tea_first = tea._coerce_nonfinite_served_gwp(
        tea._coerce_nonfinite_served_tea({
            "success": True,
            "tea": {
                "msp_usd_per_kg": None,
                "tci_usd": 1.0,
                "aoc_usd_per_yr": 1.0,
            },
            "lca": {"gwp_kg_co2e_per_kg": None},
        })
    )
    assert tea_first["error_type"] == "no_finite_msp"


def test_all_fail_error_code_promotes_only_homogeneous_classified():
    assert tea._all_fail_error_code(
        [{"error_type": "tea_cashflow_undefined"}]
    ) == "tea_cashflow_undefined"
    assert tea._all_fail_error_code(
        [{"error_type": "no_finite_msp"}]
    ) == "no_finite_msp"
    assert tea._all_fail_error_code(
        [{"error_type": "no_finite_gwp"}]
    ) == "no_finite_gwp"
    assert tea._all_fail_error_code(
        [{"error_type": "priced_solvent_unmodellable"}]
    ) == "priced_solvent_unmodellable"
    assert tea._all_fail_error_code(
        [{"error_type": "timeout"}]
    ) == "no_simulation_result"
    assert tea._all_fail_error_code([
        {"error_type": "no_finite_msp"},
        {"error_type": "tea_cashflow_undefined"},
    ]) == "no_simulation_result"
    assert tea._all_fail_error_code(
        [{"error_type": ""}]
    ) == "no_simulation_result"


def test_complete_twelve_cache_still_serves_finite_msp(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    row = (payload.get("comparison_rows") or [None])[0]
    assert row is not None
    assert math.isfinite(float(row["msp_usd_per_kg"]))
    assert math.isfinite(float(row["tci_usd"]))
    assert math.isfinite(float(row["aoc_usd_per_yr"]))


def test_evaluate_injected_null_msp_is_typed_not_typeerror(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
    )
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "tea_cashflow_undefined"
    assert payload.get("error_code") != "no_simulation_result"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "tea_cashflow_undefined"
    assert "nan encountered in cashflow array" in str(failures[0].get("error") or "")
    assert failures[0].get("nonfinite_served_metrics") == ["msp_usd_per_kg"]


def test_evaluate_injected_null_msp_without_cashflow_promotes_no_finite_msp(
    monkeypatch,
):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_finite_msp"
    assert payload.get("error_code") != "no_simulation_result"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "no_finite_msp"


def test_evaluate_injected_inf_msp_promotes_no_finite_msp(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(monkeypatch, msp=float("inf"))
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_finite_msp"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "no_finite_msp"


def test_evaluate_mixed_d23_types_stay_no_simulation_result(monkeypatch):
    c1, sibling = _two_c1_records()
    cashflow_polymer = c1["config"]["target_plastic"]
    _forbid_live(monkeypatch)
    real_run = tea._run

    def planted(config, engine_mode, timeout_seconds):
        result = copy.deepcopy(real_run(config, engine_mode, timeout_seconds))
        planted_result = dict(result)
        tea_block = dict(result.get("tea") or {})
        tea_block["msp_usd_per_kg"] = None
        planted_result["tea"] = tea_block
        planted_result["success"] = True
        planted_result.pop("error", None)
        planted_result.pop("error_type", None)
        planted_result.pop("nonfinite_served_metrics", None)
        planted_result.pop("underlying_errors", None)
        if config.get("target_plastic") == cashflow_polymer:
            planted_result["error"] = (
                "RuntimeError: nan encountered in cashflow array"
            )
        return planted_result

    monkeypatch.setattr(tea, "_run", planted)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[
            _public_from_record(c1),
            _public_from_record(sibling),
        ],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_simulation_result"
    types = {
        str(row.get("error_type") or "")
        for row in (payload.get("failures") or [])
    }
    assert types == {"tea_cashflow_undefined", "no_finite_msp"}


def test_evaluate_injected_null_gwp_is_typed_not_untyped_drop(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_gwp(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_finite_gwp"
    assert payload.get("error_code") != "no_simulation_result"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "no_finite_gwp"
    assert failures[0].get("nonfinite_served_metrics") == ["gwp_kg_co2e_per_kg"]
    assert math.isfinite(float(
        (record["result"].get("tea") or {}).get("msp_usd_per_kg")
    ))


def test_evaluate_mixed_null_gwp_keeps_the_finite_sibling(monkeypatch):
    c1, sibling = _two_c1_records()
    poisoned = c1["config"]["target_plastic"]
    _forbid_live(monkeypatch)
    _plant_success_with_null_gwp(
        monkeypatch,
        poison=lambda config, _result: config.get("target_plastic") == poisoned,
    )
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[
            _public_from_record(c1),
            _public_from_record(sibling),
        ],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert payload.get("completed") == 1
    assert payload.get("failed") == 1
    failed = [
        row for row in (payload.get("comparison_rows") or [])
        if row.get("success") is not True
    ]
    assert failed[0].get("error_type") == "no_finite_gwp"


def test_evaluate_mixed_batch_keeps_the_finite_sibling(monkeypatch):
    c1, sibling = _two_c1_records()
    poisoned = c1["config"]["target_plastic"]
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
        poison=lambda config, _result: config.get("target_plastic") == poisoned,
    )
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[
            _public_from_record(c1),
            _public_from_record(sibling),
        ],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert payload.get("completed") == 1
    assert payload.get("failed") == 1
    rows = payload.get("comparison_rows") or []
    assert len(rows) == 2
    usable = [row for row in rows if row.get("success") is True]
    failed = [row for row in rows if row.get("success") is not True]
    assert len(usable) == 1
    assert math.isfinite(float(usable[0]["msp_usd_per_kg"]))
    assert failed[0].get("error_type") == "tea_cashflow_undefined"


def test_route_injected_null_msp_is_route_stage_failed(monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
    )
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="auto",
        ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "route_stage_failed"
    failed = payload.get("failed_stage") or {}
    assert failed.get("error_type") == "tea_cashflow_undefined"
    assert failed.get("success") is False
    assert "nan encountered in cashflow array" in str(failed.get("error") or "")


def test_route_stage_live_cannot_run_reaches_uncostable_or_design_point(
    monkeypatch,
):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)

    def planted(config, engine_mode, timeout_seconds):
        return {
            "success": False,
            "error": "The TEA worker is not Python 3.12.",
            "error_type": "python_version",
            "engine_mode": "live",
            "cache_match_status": "miss",
            "config": config,
        }

    monkeypatch.setattr(tea, "_run", planted)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="auto",
        ))
    assert payload.get("success") is False
    assert payload.get("error_code") in {
        "uncostable_route_stage",
        "route_stage_design_point_unavailable",
    }
    assert payload.get("error_code") != "route_stage_failed"


def test_route_nonfinite_metric_is_not_papered_by_screening_estimate(
    monkeypatch,
):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
    )
    called = {"n": 0}
    real_estimate = tea._screening_estimate

    def traced(config, result):
        called["n"] += 1
        return real_estimate(config, result)

    monkeypatch.setattr(tea, "_screening_estimate", traced)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="auto",
            allow_screening_estimate=True,
        ))
    assert called["n"] == 0
    assert payload.get("error_code") == "route_stage_failed"


def test_json_roundtrip_of_worker_refusal_allows_null_metrics():
    payload = {
        "success": False,
        "error_type": "tea_cashflow_undefined",
        "error": "RuntimeError: nan encountered in cashflow array",
        "tea": {
            "msp_usd_per_kg": None,
            "tci_usd": None,
            "aoc_usd_per_yr": None,
        },
        "lca": {"gwp_kg_co2e_per_kg": 1.0},
    }
    encoded = json.dumps(payload, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["success"] is False
    assert decoded["tea"]["msp_usd_per_kg"] is None
    assert decoded["lca"]["gwp_kg_co2e_per_kg"] == 1.0


# --- from test_economics_handle_inherit.py: Fill omitted process fields from an economics handle, not a screen or pair.
def _forbid_pair_fill(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("inherit must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def _store_evaluate_handle(session, record):
    first = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)], engine_mode="auto",
    ))
    assert first.get("success") is True
    return store_handle(
        session,
        tool="evaluate_process",
        source_basis="tea_cache_exact",
        data=first,
    ), first


def test_evaluate_comparison_rows_carry_the_lookup_twelve(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)], engine_mode="auto",
    ))
    row = payload["comparison_rows"][0]
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert name in row
        assert row[name] is not None
    assert row["target_polymer"] == row["polymer"] == record["config"]["target_plastic"]
    assert "field_origin" not in row


def test_first_incomplete_evaluate_still_refuses_without_handle(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        }],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert "comparison_rows" not in payload


def test_omitted_nine_inherit_from_evaluate_handle(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            engine_mode="auto",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert payload.get("success") is True
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["dissolution_temperature_c"] == "supplied"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"
    assert first["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(
        recorded_msp,
    )


def test_override_is_supplied_and_rest_inherited(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    _live_run_fails(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
                "precipitation_temperature_c": 999.0,
            }],
            handle=handle,
            engine_mode="auto",
            confirm_live_tea=True,
        ))
    row = (payload.get("comparison_rows") or payload.get("failures") or [None])[0]
    assert row is not None
    origin = row["field_origin"]
    assert origin["precipitation_temperature_c"] == "supplied"
    assert origin["target_mass_percent"] == "inherited"
    assert row["precipitation_temperature_c"] == pytest.approx(999.0)
    assert row.get("success") is not True


def test_handle_only_reruns_the_executed_twelve(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            handle=handle, engine_mode="auto",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert origin[name] == "inherited"


def test_complete_scenarios_win_over_leftover_handle(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(record)],
            handle=handle,
            engine_mode="auto",
        ))
    row = payload["comparison_rows"][0]
    assert "field_origin" not in row
    assert row["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


def test_shortlist_without_held_inherits_nine_from_handle(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            screening_shortlist={
                "source": "explicit",
                "items": [_item_from_record(record)],
            },
            handle=handle,
            engine_mode="auto",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"


def test_held_basis_beats_handle_for_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            screening_shortlist={
                "source": "explicit",
                "items": [_item_from_record(record)],
            },
            held_process_basis=_held_from_record(record),
            handle=handle,
            engine_mode="auto",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "supplied"
    assert origin["target_polymer"] == "from_screen"


def test_screening_handle_cannot_be_the_inherit_source(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        assert screen.get("handle")
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=screen["handle"],
            engine_mode="auto",
        ))
    assert payload.get("error_code") == "not_economics_handle"
    assert "comparison_rows" not in payload


def test_multi_row_handle_without_row_id_is_ambiguous(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    _live_run_fails(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_tea_lca_scenarios(
            [
                _public_from_record(record),
                _public_from_record(record, dissolution_temperature_c=999.0),
            ],
            engine_mode="auto",
            confirm_live_tea=True,
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        missing = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
            }],
            handle=handle,
            engine_mode="auto",
        ))
        selected = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            row_id=1,
            engine_mode="auto",
        ))
    assert missing.get("error_code") == "ambiguous_handle_row"
    assert missing.get("n_rows") == 2
    row = selected["comparison_rows"][0]
    assert row["dissolution_temperature_c"] == pytest.approx(
        cfg["dissolution_temperature_c"],
    )
    assert row["field_origin"]["dissolution_temperature_c"] == "supplied"
    assert row["field_origin"]["target_mass_percent"] == "inherited"


def test_unknown_handle_refuses_incomplete_follow_up(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
        }],
        handle="ghost-white-fox",
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_handle"


def test_lookup_row_is_an_economics_inherit_source(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            target_polymer=cfg["target_plastic"],
            solvent=cfg["solvent"],
            energy_cases=[cfg["energy_case"]],
            dissolution_temperature_c=cfg["dissolution_temperature_c"],
            processing_capacity_mt_per_yr=cfg["processing_capacity"],
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        rows = handle_rows(load_handle(session, handle))
        assert len(rows) >= 1
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            row_id=1 if len(rows) > 1 else None,
            engine_mode="auto",
        ))
    row = payload["comparison_rows"][0]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert row["field_origin"][name] == "inherited"


def test_dispatch_one_scenario_evaluate_issues_handle_and_keeps_rows(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session) as rec:
        out = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="auto",
        )
        assert out["available"] is True
        assert out.get("handle")
        assert "comparison_rows" in (out.get("data") or {})
        assert "top" not in out
        stored = load_handle(rec, out["handle"])
        assert stored["tool"] == "evaluate_process"
        assert stored["exact"]["comparison_rows"][0]["target_polymer"]
        follow = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config={
                "target_polymer": record["config"]["target_plastic"],
                "solvent": record["config"]["solvent"],
                "dissolution_temperature_c": record["config"][
                    "dissolution_temperature_c"
                ],
            },
            handle=out["handle"],
            engine_mode="auto",
        )
    assert follow["available"] is True
    origin = follow["data"]["comparison_rows"][0]["field_origin"]
    assert origin["target_mass_percent"] == "inherited"
    assert origin["target_polymer"] == "supplied"


def test_evaluate_schema_names_handle_and_row_id():
    schemas = {spec["name"]: spec for spec in tool_schemas()}
    assert "evaluate_tea_lca_scenarios" not in schemas
    params = inspect.signature(tea.evaluate_tea_lca_scenarios).parameters
    assert "handle" in params
    assert "row_id" in params
    wrap = schemas["evaluate_process"]["parameters"]["properties"]
    assert wrap["process_config"]["type"] == "object"
    assert wrap["process_configs"]["type"] == "array"
    assert "handle" not in schemas["solubility_query"]["parameters"]["properties"]


# --- from test_incomplete_process_config.py: Headless evaluate refuses an incomplete twelve instead of filling it.
def _public_from_record_incomplete_process_config(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temp_c": cfg["dissolution_temperature_c"],
        "precipitation_temp_c": cfg["precipitation_temperature_c"],
        "solvent_price": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def _forbid_pair_fill_incomplete_process_config(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("headless evaluate must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_polymer_and_solvent_alone_list_missing_public_fields(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
        }],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    missing = list(payload.get("missing") or [])
    assert "dissolution_temperature_c" in missing
    assert missing == [
        name for name in tea._PUBLIC_REQUIRED_FIELDS
        if name not in {"target_polymer", "solvent"}
    ]
    assert payload.get("msp_usd_per_kg") is None
    assert "comparison_rows" not in payload


def test_polymer_solvent_and_t_lists_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
    }
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(scenario)
    assert caught.value.error_code == "incomplete_process_config"
    assert caught.value.details["missing"] == list(tea._NINE_HELD_PUBLIC_FIELDS)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [scenario], engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert payload.get("comparison_rows") is None or "comparison_rows" not in payload


def test_temperature_c_is_not_an_alias_and_does_not_fill(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_incomplete_process_config(
            record,
            temperature_c=record["config"]["dissolution_temperature_c"],
        )],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])
    assert payload.get("cache_match_status") is None


def test_temperature_c_rename_does_not_supply_dissolution_t(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    scenario = _public_from_record_incomplete_process_config(record)
    renamed = scenario.pop("dissolution_temp_c")
    scenario["temperature_c"] = renamed
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [scenario], engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])
    assert payload.get("error_code") != "incomplete_process_config"


def test_unknown_extra_key_refuses_a_complete_twelve(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_incomplete_process_config(record, not_a_process_field=1)],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "not_a_process_field" in list(payload.get("extra_keys") or [])


def test_complete_twelve_still_hits_the_cache(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("complete twelve must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_incomplete_process_config(record)],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert payload.get("cache_match_status") == "exact"
    assert payload["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(
        recorded_msp
    )


def test_complete_twelve_at_unmatched_t_is_a_miss_not_a_fill(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("an unconfirmed miss must not start live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_incomplete_process_config(record, dissolution_temp_c=999.0)],
        engine_mode="auto",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert (payload.get("n_live"), payload.get("n_cache")) == (1, 0)
    assert repr(recorded_msp) not in json.dumps(payload)


def test_omitted_switches_on_a_complete_twelve_keep_production(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("production defaults must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    reconstructed = tea._scenario_config(_public_from_record_incomplete_process_config(record))
    assert reconstructed["sell_leftover_plastic"] is False
    assert reconstructed["burn_leftover_plastic"] is False
    assert reconstructed["irr"] == pytest.approx(0.10)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record_incomplete_process_config(record)],
        engine_mode="auto",
    ))
    assert payload.get("cache_match_status") == "exact"


def test_sensitivity_refuses_the_same_incomplete_nine(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        {
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        },
        parameter="target_mass_percent",
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)


def test_worker_internal_names_satisfy_the_public_twelve(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("internal names of a complete twelve stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_plastic": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "target_plastic_percent": cfg["target_plastic_percent"],
            "processing_capacity": cfg["processing_capacity"],
            "energy_case": cfg["energy_case"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            "precipitation_temperature_c": cfg["precipitation_temperature_c"],
            "solvent_price": cfg["solvent_price"],
            "solvent_loss_pct": cfg["solvent_loss_pct"],
            "feedstock_distance_km": cfg["feedstock_distance_km"],
            "dissolution_capacity": cfg["dissolution_capacity"],
            "labor_cost": cfg["labor_cost"],
        }],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert payload.get("cache_match_status") == "exact"


def _d8_overlay_from_record(record: dict) -> dict:
    cfg = record["config"]
    return {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temp_c": cfg["dissolution_temperature_c"],
        "precipitation_temp_c": cfg["precipitation_temperature_c"],
    }


def test_d8_overlay_scenario_config_names_the_remainder(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    overlay = _d8_overlay_from_record(record)
    config = tea._scenario_config(overlay)
    assert tea._cache_index().get(tea._config_key(config)) is not None
    assert config["solvent_loss_pct"] == pytest.approx(0.01)
    assert config["feedstock_distance_km"] == pytest.approx(0.0)
    assert config["dissolution_capacity"] == pytest.approx(3.0)
    assert config["labor_cost"] == pytest.approx(120_000.0)


def test_evaluate_still_refuses_a_d8_overlay_without_the_twelve(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_d8_overlay_from_record(record)],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._D8_REMAINDER_PUBLIC_FIELDS)
    assert payload.get("comparison_rows") is None or "comparison_rows" not in payload
    assert payload.get("cache_match_status") is None


def test_sensitivity_still_refuses_a_d8_overlay_without_the_twelve(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill_incomplete_process_config(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _d8_overlay_from_record(record),
        parameter="target_mass_percent",
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._D8_REMAINDER_PUBLIC_FIELDS)


# --- from test_sensitivity_capacity_labor.py: Sensitivity sweeps dissolution_capacity and labor_cost. Not a live child.
def _forbid_live_sensitivity_capacity_labor(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("capacity/labour sensitivity must not start live")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_unknown_parameter_lists_capacity_and_labour(monkeypatch):
    record = _route_c1()
    _forbid_live_sensitivity_capacity_labor(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="recovery",
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unsupported_parameter"
    supported = list(payload.get("supported_parameters") or [])
    assert supported == sorted(tea._NUMERIC_FIELDS)
    assert "dissolution_capacity" in supported
    assert "labor_cost" in supported


def test_energy_case_is_still_unsupported(monkeypatch):
    record = _route_c1()
    _forbid_live_sensitivity_capacity_labor(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="energy_case",
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unsupported_parameter"


def test_dissolution_capacity_is_no_longer_unsupported(monkeypatch):
    record = _route_c1()
    _forbid_live_sensitivity_capacity_labor(monkeypatch)
    discovered = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="dissolution_capacity",
        engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=_public_from_record(record),
        parameter="dissolution_capacity",
        engine_mode="auto",
    ))
    assert discovered.get("error_code") != "unsupported_parameter"
    assert discovered.get("error_code") == "insufficient_sensitivity_values"
    assert wrapped.get("error_code") == "insufficient_sensitivity_values"
    assert wrapped.get("tool_name") == "evaluate_process"
    _live_run_fails(monkeypatch)
    swept = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="dissolution_capacity",
        values=[
            float(record["config"]["dissolution_capacity"]),
            float(record["config"]["dissolution_capacity"]) + 1.0,
        ],
        engine_mode="auto",
        confirm_live_tea=True,
    ))
    assert swept.get("error_code") != "unsupported_parameter"
    assert swept.get("error_code") == "insufficient_sensitivity_results"
    rows = list(swept.get("sensitivity_rows") or [])
    assert len(rows) == 2
    assert {row["parameter"] for row in rows} == {"dissolution_capacity"}
    successes = [row for row in rows if row.get("success")]
    failures = [row for row in rows if not row.get("success")]
    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0]["dissolution_capacity"] == float(
        record["config"]["dissolution_capacity"],
    )
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert name in successes[0]


def test_labor_cost_public_alias_is_no_longer_unsupported(monkeypatch):
    record = _route_c1()
    _forbid_live_sensitivity_capacity_labor(monkeypatch)
    discovered = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="labor_cost_usd_per_employee_yr",
        engine_mode="auto",
    ))
    wrapped = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=_public_from_record(record),
        parameter="labor_cost",
        engine_mode="auto",
    ))
    assert discovered.get("error_code") != "unsupported_parameter"
    assert discovered.get("error_code") == "insufficient_sensitivity_values"
    assert wrapped.get("error_code") == "insufficient_sensitivity_values"
    assert wrapped.get("tool_name") == "evaluate_process"
    _live_run_fails(monkeypatch)
    swept = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="labor_cost",
        values=[
            float(record["config"]["labor_cost"]),
            float(record["config"]["labor_cost"]) + 1000.0,
        ],
        engine_mode="auto",
        confirm_live_tea=True,
    ))
    assert swept.get("error_code") != "unsupported_parameter"
    assert swept.get("error_code") == "insufficient_sensitivity_results"
    rows = list(swept.get("sensitivity_rows") or [])
    assert {row["parameter"] for row in rows} == {"labor_cost"}
    assert any(
        row.get("labor_cost_usd_per_employee_yr")
        == float(record["config"]["labor_cost"])
        and row.get("success")
        for row in rows
    )


def test_solvent_price_sweep_still_serves(monkeypatch):
    record = _route_c1()
    _forbid_live_sensitivity_capacity_labor(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert len(payload["sensitivity_rows"]) >= 2


# --- from test_sensitivity_handle_inherit.py: Sensitivity inherits its baseline from an economics handle, not a screen.
def _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("sensitivity inherit must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_sensitivity_rows_carry_the_executed_twelve(monkeypatch):
    record = _route_c1()
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    rows = payload["sensitivity_rows"]
    assert len(rows) >= 2
    for row in rows:
        for name in tea._PUBLIC_REQUIRED_FIELDS:
            assert name in row
            assert row[name] is not None
        assert row["target_polymer"] == record["config"]["target_plastic"]
        assert "field_origin" not in row


def test_first_incomplete_sensitivity_still_refuses_without_handle(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        {
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        },
        parameter="solvent_price",
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert "sensitivity_rows" not in payload


def test_sensitivity_inherits_baseline_from_evaluate_handle(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.analyze_tea_sensitivity(
            {
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            },
            parameter="solvent_price",
            handle=handle,
            engine_mode="auto",
            analysis_mode="tornado",
        ))
    assert payload.get("success") is True
    origin = payload["field_origin"]
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["dissolution_temperature_c"] == "supplied"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"
    row = payload["sensitivity_rows"][0]
    assert row["field_origin"]["target_mass_percent"] == "inherited"
    prices = {float(item["value"]) for item in payload["sensitivity_rows"]}
    assert float(cfg["solvent_price"]) in prices
    assert len(prices) >= 2


def test_handle_only_sensitivity_inherits_the_executed_twelve(monkeypatch):
    record = _route_c1()
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            engine_mode="auto",
            analysis_mode="tornado",
        ))
    assert payload.get("success") is True
    origin = payload["field_origin"]
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert origin[name] == "inherited"
    assert payload["polymer"] == record["config"]["target_plastic"]


def test_complete_sensitivity_scenario_wins_over_leftover_handle(monkeypatch):
    record = _route_c1()
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.analyze_tea_sensitivity(
            _public_from_record(record),
            parameter="solvent_price",
            handle=handle,
            engine_mode="auto",
        ))
    assert payload.get("success") is True
    assert "field_origin" not in payload
    assert "field_origin" not in payload["sensitivity_rows"][0]


def test_screening_handle_cannot_be_sensitivity_baseline(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        payload = _data(tea.analyze_tea_sensitivity(
            {
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            },
            parameter="solvent_price",
            handle=screen["handle"],
            engine_mode="auto",
        ))
    assert payload.get("error_code") == "not_economics_handle"


def test_evaluate_can_inherit_from_a_sensitivity_row(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        sweep = _data(tea.analyze_tea_sensitivity(
            _public_from_record(record),
            parameter="solvent_price",
            engine_mode="auto",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=sweep,
        )
        missing = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            engine_mode="auto",
        ))
        baseline_index = next(
            index
            for index, row in enumerate(sweep["sensitivity_rows"], 1)
            if abs(float(row["value"]) - float(cfg["solvent_price"])) < 1e-9
        )
        follow = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            row_id=baseline_index,
            engine_mode="auto",
        ))
    assert missing.get("error_code") == "ambiguous_handle_row"
    row = follow["comparison_rows"][0]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert row["field_origin"]["solvent_price_usd_per_kg"] == "inherited"


def test_dispatch_tornado_from_evaluate_handle(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill_sensitivity_handle_inherit(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        evaluated = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="auto",
        )
        tornado = dispatch(
            "evaluate_process",
            mode="sensitivity",
            parameter="solvent_price",
            analysis_mode="tornado",
            handle=evaluated["handle"],
            engine_mode="auto",
        )
    assert tornado["available"] is True
    assert tornado.get("handle")
    assert "sensitivity_rows" in (tornado.get("data") or {})
    origin = tornado["data"]["field_origin"]
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert origin[name] == "inherited"
    assert tornado["data"]["polymer"] == cfg["target_plastic"]


def test_sensitivity_schema_names_handle_and_optional_scenario():
    schemas = {spec["name"]: spec for spec in tool_schemas()}
    assert "analyze_tea_sensitivity" not in schemas
    params = inspect.signature(tea.analyze_tea_sensitivity).parameters
    assert "handle" in params
    assert "row_id" in params
    assert "scenario" in params
    wrap = schemas["evaluate_process"]["parameters"]["properties"]
    assert wrap["process_config"]["type"] == "object"


# --- from test_sheet_standing_preview.py: Sheet standing preview must pass the resolved plastics root.
def _pe_toluene_sheet(**overrides):
    committed = params.COMMITTED_PAIR_STEPS[("PE", "toluene")]
    buffer = {
        "target_polymer": "LDPE",
        "solvent": "Toluene",
        **params.process_config_from_assumptions(committed),
    }
    buffer.update(overrides)
    return buffer


def test_thin_payload_call_is_validated_sheet_without_root_is_not(
    monkeypatch,
):
    sheet = _pe_toluene_sheet()
    thin = params.live_parameter_standing_payload(
        params.POLYMERS["LDPE"],
        solvent="Toluene",
        config={
            "dissolution_temperature_c": sheet["dissolution_temperature_c"],
            "precipitation_temperature_c": sheet["precipitation_temperature_c"],
            "dissolution_capacity": sheet["dissolution_capacity"],
        },
    )
    assert thin["can_cite_as_validated_process"] is True
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    preview = tea.sheet_standing_preview(sheet)
    assert preview["can_cite_as_validated_process"] is False
    assert preview["badge"] == "unavailable"
    assert preview["reason"] == "missing_plastics_root"
    assert preview["passed_plastics_root"] is False


def test_matching_package_pe_toluene_95_35_3_is_validated(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["passed_plastics_root"] is True
    assert preview["plastics_root"] == str(tmp_path.resolve())
    assert preview["badge"] == "validated"
    assert preview["can_cite_as_validated_process"] is True


def test_package_disagreement_flips_the_same_config_to_provisional(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path, pe_rho="1.0")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["passed_plastics_root"] is True
    assert preview["badge"] == "provisional"
    assert preview["can_cite_as_validated_process"] is False
    assert "rho_kg_m3" in preview["provisional_parameters"]


def test_preview_passes_resolved_plastics_root_into_payload(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    seen = {}
    original = params.live_parameter_standing_payload

    def spy(*args, **kwargs):
        seen["kwargs"] = kwargs
        seen["args"] = args
        return original(*args, **kwargs)

    monkeypatch.setattr(params, "live_parameter_standing_payload", spy)
    tea.sheet_standing_preview(_pe_toluene_sheet())
    assert seen["kwargs"]["plastics_root"] == tmp_path.resolve()
    assert seen["kwargs"]["package_disagreements"] is not None


def test_unrecognised_plastics_root_is_missing_package_not_validated(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["badge"] == "unavailable"
    assert preview["reason"] == "missing_package"
    assert preview["can_cite_as_validated_process"] is False


def test_malformed_package_is_unavailable(tmp_path, monkeypatch):
    strap = tmp_path / "plastics" / "strap"
    strap.mkdir(parents=True)
    (strap / "property_package.py").write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["badge"] == "unavailable"
    assert preview["reason"] == "property_package_unreadable"
    assert preview["can_cite_as_validated_process"] is False


def test_generic_first_run_setpoints_are_not_validated_pe_toluene(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    seeded = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Toluene",
    })
    preview = tea.sheet_standing_preview(seeded)
    assert preview["can_cite_as_validated_process"] is False
    assert preview["badge"] == "provisional"
    assert "dissolution_temperature_c" in preview["provisional_parameters"]


def test_missing_polymer_or_solvent_is_incomplete(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    preview = tea.sheet_standing_preview({"target_polymer": "LDPE"})
    assert preview["badge"] == "incomplete"
    assert preview["missing"] == ["solvent"]
    assert preview["passed_plastics_root"] is False


def test_preview_does_not_call_live(tmp_path, monkeypatch):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))

    def boom(*args, **kwargs):
        raise AssertionError("standing preview must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", boom)
    tea.sheet_standing_preview(_pe_toluene_sheet())


def test_sheet_print_shows_standing_badge(tmp_path, monkeypatch):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    app = CliApp(
        session_id="test-session",
        store_root=tmp_path / "session",
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
    )
    app._print_process_sheet(_pe_toluene_sheet())
    shown = buf.getvalue()
    assert "VALIDATED-process" in shown
    assert "msp_usd_per_kg" not in shown


# --- from test_optimization_basis_gap.py: Feed-scale ranking gaps must be reachable and name complete design points.
def _data_optimization_basis_gap(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _plant_feed_scale_gap(session) -> None:
    session["last_tea"] = {
        "analysis_type": "tea_feed_scale_basis_gap",
        "missing_basis_codes": ["probe"],
        "requested_feed_mass_fractions": {"LDPE": 1.0},
    }


def _gap_payload() -> dict:
    session = new_session()
    _plant_feed_scale_gap(session)
    with bind_tool_session(session):
        return _data_optimization_basis_gap(O.pareto_optimize_stored_route(
            x_metric="total_cost", y_metric="circularity",
        ))


def test_insufficient_feed_optimization_basis_is_unreachable_without_session():
    assert current_tool_session() is None
    data = _data_optimization_basis_gap(O.pareto_optimize_stored_route(
        x_metric="total_cost", y_metric="circularity",
    ))
    assert data.get("error_code") != "insufficient_feed_optimization_basis"
    assert data.get("error_code") != "insufficient_optimization_basis"


def test_insufficient_feed_optimization_basis_is_reachable_from_last_tea():
    data = _gap_payload()
    assert data["error_code"] == "insufficient_feed_optimization_basis"
    assert data["success"] is False


def test_insufficient_feed_optimization_basis_names_complete_design_points():
    data = _gap_payload()
    points = data.get("proposed_design_points")
    assert isinstance(points, list) and points
    for item in points:
        tea._scenario_config(dict(item), require_complete_twelve=True)
    broken = dict(points[0])
    broken.pop(sorted(broken)[0])
    with pytest.raises(Exception):
        tea._scenario_config(broken, require_complete_twelve=True)


def test_evaluate_feed_scale_gap_binds_last_tea_for_the_next_optimizer():
    session = new_session()
    with bind_tool_session(session) as bound:
        raw = tea.evaluate_stored_route_tea_lca(
            feed_mass_fractions={"LDPE": 1.0},
            processing_capacity_mt_per_yr=20_000.0,
            comparison_capacities_mt_per_yr=[10_000.0, 40_000.0],
            energy_case="C1",
        )
        planted = _data_optimization_basis_gap(raw)
        assert planted["analysis_type"] == "tea_feed_scale_basis_gap"
        assert bound.last_tea["analysis_type"] == "tea_feed_scale_basis_gap"
        assert bound["last_tea"] is bound.last_tea
        gap = _data_optimization_basis_gap(O.pareto_optimize_stored_route(
            x_metric="total_cost", y_metric="circularity",
        ))
    assert gap["error_code"] == "insufficient_feed_optimization_basis"
    assert gap.get("proposed_design_points")


def test_insufficient_optimization_basis_is_reachable_from_candidate_gap():
    session = new_session()
    session["last_tea"] = {
        "analysis_type": "candidate_lca_basis_gap",
        "missing_basis_codes": ["probe"],
        "missing_process_inputs": [],
        "target_product": "LDPE",
        "other_polymers": ["HDPE"],
    }
    session["last_screen_constraints"] = {"minimum_selectivity_points": 5.0}
    session["last_candidates"] = [
        {"solvent": "Toluene", "selectivity_pct": 12.0, "temperature_c": 80.0},
    ]
    session["last_safety"] = [
        {"solvent": "Toluene", "boiling_point_c": 110.6},
    ]
    with bind_tool_session(session):
        data = _data_optimization_basis_gap(O.pareto_optimize_stored_route(
            x_metric="emissions", y_metric="selectivity",
        ))
    assert data["error_code"] == "insufficient_optimization_basis"


def test_rank_landscape_does_not_advertise_epsilon():
    annotation = inspect.signature(tea.rank_landscape).parameters[
        "operation"
    ].annotation
    assert "epsilon" not in str(annotation)


# --- from test_rank_evaluate_handle.py: rank_landscape ranks a tool-1 handle. Not a fingerprint and not a live child.
def _data_rank_evaluate_handle(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _forbid_live_rank_evaluate_handle(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("rank from a handle must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(
        tea.tea_worker, "run", forbidden,
    )


def _store_evaluate(session, scenarios):
    first = _data_rank_evaluate_handle(tea.evaluate_tea_lca_scenarios(
        scenarios, engine_mode="auto",
    ))
    assert first.get("success") is True
    return store_handle(
        session,
        tool="evaluate_process",
        source_basis="tea_cache_exact",
        data=first,
    ), first


def test_evaluate_batch_handle_ranks_without_fingerprint(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=handle))
    assert payload.get("success") is True
    assert payload.get("error_code") is None
    assert payload["analysis_type"] == "process_rows_landscape"
    assert payload["source"] == "process_rows"
    assert payload["n_landscape_points"] == 2
    assert payload["n_landscape_points"] == len(payload["landscape_points"])
    assert payload["n_frontier_points"] == len(payload["frontier_points"])
    assert payload["n_usable"] == 2
    assert payload["ingested_into_admitted_cache"] is False
    assert "campaign_fingerprint" not in payload
    msps = {float(row["msp_usd_per_kg"]) for row in first["comparison_rows"]}
    ranked = {float(point["msp_usd_per_kg"]) for point in payload["landscape_points"]}
    assert ranked == msps
    for point in payload["landscape_points"]:
        for name in tea._PUBLIC_REQUIRED_FIELDS:
            assert point.get(name) is not None
        assert point["target_polymer"] == "LDPE"
        assert point["lca_coverage"]["status"] == "partial"  # the live run's own coverage, stored with its values
        assert point["safety_standing"]["status"] == "not_requested"
        assert "engine_envelope" not in point
    assert payload["n_frontier_points"] >= 1
    assert payload["sparse_frontier"] is True or payload["n_frontier_points"] >= 1


def test_evaluate_handle_carries_bound_safety_standing(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data_rank_evaluate_handle(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(c1), _public_from_record(c2)],
            engine_mode="auto",
        ))
        assert first.get("success") is True
        rows = list(first["comparison_rows"])
        rows[0] = dict(rows[0])
        rows[0]["safety_standing"] = {
            "status": "evaluated",
            "safety_profile": {"ghs_signal_word": "Danger"},
        }
        first = dict(first)
        first["comparison_rows"] = rows
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=handle))
    assert payload.get("success") is True
    assert payload["n_usable"] == 2
    assert payload["safety_standing_policy"] == "carried_not_filtered"
    points = payload["landscape_points"]
    statuses = {point["safety_standing"]["status"] for point in points}
    assert statuses == {"evaluated", "not_requested"}
    evaluated = next(
        point for point in points
        if point["safety_standing"]["status"] == "evaluated"
    )
    skipped = next(
        point for point in points
        if point["safety_standing"]["status"] == "not_requested"
    )
    assert evaluated["safety_standing"]["safety_profile"] == {
        "ghs_signal_word": "Danger",
    }
    assert skipped["safety_standing"] == {"status": "not_requested"}
    assert evaluated["energy_case"] != skipped["energy_case"]


def test_c1_and_c2_evaluate_batch_is_not_mixed_campaign_basis(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=handle))
    assert payload.get("success") is True
    assert payload.get("error_code") != "mixed_campaign_basis"
    cases = {point["energy_case"] for point in payload["landscape_points"]}
    assert cases == {"C1", "C2"}


def test_one_row_evaluate_handle_is_landscape_too_small(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate(session, [_public_from_record(c1)])
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=handle))
    assert payload.get("error_code") == "landscape_too_small"
    assert payload["n_usable"] == 1
    assert payload["ingested_into_admitted_cache"] is False
    assert "landscape_points" not in payload
    assert "frontier_points" not in payload


def test_admitted_lookup_handle_ranks_energy_cases(monkeypatch):
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data_rank_evaluate_handle(tea.lookup_admitted_process_records(
            target_polymer="LDPE",
            solvent="Dodecane",
            energy_cases=["C1", "C2", "C3"],
        ))
        assert lookup.get("success") is True
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=handle))
    assert payload.get("success") is True
    assert payload["n_landscape_points"] == 3
    assert payload["n_landscape_points"] == len(payload["landscape_points"])
    assert payload["n_frontier_points"] == len(payload["frontier_points"])
    cases = {point["energy_case"] for point in payload["landscape_points"]}
    assert cases == {"C1", "C2", "C3"}
    for point in payload["landscape_points"]:
        for name in tea._PUBLIC_REQUIRED_FIELDS:
            assert point.get(name) is not None
        assert point["lca_coverage"]
        assert point["safety_standing"]["status"] == "not_requested"


def test_evaluate_process_sensitivity_handle_ranks(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        wrapped = dispatch(
            "evaluate_process",
            mode="sensitivity",
            process_config=_public_from_record(record),
            parameter="solvent_price",
            engine_mode="auto",
        )
        retired = dispatch(
            "analyze_tea_sensitivity",
            scenario=_public_from_record(record),
            parameter="solvent_price",
            engine_mode="auto",
        )
        assert wrapped.get("available") is True
        assert retired.get("available") is False
        assert retired.get("refusal") == "unknown_tool"
        engine = _data_rank_evaluate_handle(tea.analyze_tea_sensitivity(
            _public_from_record(record),
            parameter="solvent_price",
            engine_mode="auto",
        ))
        engine_handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=engine,
        )
        wrapped_rank = _data_rank_evaluate_handle(tea.rank_landscape(handle=wrapped["handle"]))
        engine_rank = _data_rank_evaluate_handle(tea.rank_landscape(handle=engine_handle))
    rows = [
        row for row in wrapped["data"]["sensitivity_rows"]
        if row.get("success") and row.get("msp_usd_per_kg") is not None
    ]
    assert len(rows) >= 2
    for payload in (wrapped_rank, engine_rank):
        assert payload.get("success") is True
        assert payload.get("error_code") is None
        assert payload["analysis_type"] == "process_rows_landscape"
        assert payload["analysis_type"] != "tea_tornado"
        assert payload["source"] == "process_rows"
        assert payload["n_usable"] == len(rows)
        assert payload["n_landscape_points"] == len(payload["landscape_points"])
        assert payload["n_frontier_points"] == len(payload["frontier_points"])
        assert payload["n_usable"] >= 2
        assert payload["ingested_into_admitted_cache"] is False
        prices = {
            float(row["solvent_price_usd_per_kg"]) for row in rows
        }
        ranked_prices = {
            float(point["solvent_price_usd_per_kg"])
            for point in payload["landscape_points"]
        }
        assert ranked_prices == prices
        pair_ids = [point["pair_id"] for point in payload["landscape_points"]]
        assert len(set(pair_ids)) == len(pair_ids)
        for point in payload["landscape_points"]:
            for name in tea._PUBLIC_REQUIRED_FIELDS:
                assert point.get(name) is not None
            assert point["lca_coverage"]
            assert point["safety_standing"]["status"] == "not_requested"


def test_screening_handle_cannot_be_ranked(monkeypatch):
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        assert screen.get("handle")
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=screen["handle"]))
    assert payload.get("error_code") == "not_economics_handle"
    assert "landscape_points" not in payload


def test_rank_handle_cannot_be_ranked_as_tool1(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        ranked = _data_rank_evaluate_handle(tea.rank_landscape(handle=handle))
        rank_handle = store_handle(
            session,
            tool="rank_landscape",
            source_basis="tea_cache_exact",
            data=ranked,
        )
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=rank_handle))
    assert first.get("success") is True
    assert payload.get("error_code") == "not_economics_handle"


def test_unknown_handle_is_not_a_fingerprint_lookup(monkeypatch):
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle="no-such-handle"))
        empty = _data_rank_evaluate_handle(tea.rank_landscape(handle=""))
    assert payload.get("error_code") == "unknown_handle"
    assert empty.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "missing_campaign_fingerprint"


def test_extra_fingerprint_does_not_locate_a_campaign(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        payload = _data_rank_evaluate_handle(tea.rank_landscape(
            handle=handle,
            campaign_fingerprint="ff" * 32,
        ))
    assert payload.get("success") is True
    assert payload.get("error_code") not in {
        "campaign_not_registered", "missing_campaign_fingerprint",
    }
    assert payload["n_landscape_points"] == 2


def test_mixed_fingerprints_on_one_handle_refuse(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    row_a = _public_from_record(c1)
    row_a.update({
        "msp_usd_per_kg": 1.0,
        "gwp_kg_co2e_per_kg": 0.4,
        "success": True,
        "engine_mode": "cache",
        "campaign_fingerprint": "aa" * 32,
        "label": "row-a",
    })
    row_b = dict(row_a)
    row_b["msp_usd_per_kg"] = 2.0
    row_b["energy_case"] = "C2"
    row_b["campaign_fingerprint"] = "bb" * 32
    row_b["label"] = "row-b"
    session = new_session()
    with bind_tool_session(session):
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data={
                "success": True,
                "comparison_rows": [row_a, row_b],
            },
        )
        payload = _data_rank_evaluate_handle(tea.rank_landscape(handle=handle))
    assert payload.get("error_code") == "mixed_campaign_basis"
    assert "landscape_points" not in payload


def test_handle_is_native_not_a_consumer(monkeypatch):
    _forbid_live_rank_evaluate_handle(monkeypatch)
    assert "rank_landscape" not in CONSUMERS
    schemas = {item["name"]: item for item in tool_schemas()}
    props = schemas["rank_landscape"]["parameters"]["properties"]
    assert "handle" in props
    assert "default" not in props["handle"]
    assert "handle" not in (schemas["rank_landscape"]["parameters"].get("required") or [])


def test_dispatch_ranks_the_evaluate_handle(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live_rank_evaluate_handle(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        evaluated = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_configs=[_public_from_record(c1), _public_from_record(c2)],
            engine_mode="auto",
        )
        assert evaluated.get("available") is True
        handle = evaluated.get("handle")
        assert handle
        ranked = dispatch("rank_landscape", handle=handle)
    assert ranked.get("available") is True
    assert ranked.get("source_basis") == "tea_cache_exact"
    data = ranked["data"]
    assert data["n_landscape_points"] == 2
    assert data["n_landscape_points"] == len(data["landscape_points"])
    assert data["ingested_into_admitted_cache"] is False


# --- from test_residual_route.py: rank_landscape source=residual_route from a mode=route handle. Never last_route.
def _forbid_live_residual_route(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("residual_route must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _costed_route_handle(session, monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live_residual_route(monkeypatch)
    plan = _store_planner_route_handle(session, composition, route)
    payload = _data(tea.evaluate_process(
        mode="route",
        handle=plan,
        processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
        energy_case=_ROUTE_ENERGY,
        precipitation_temperature_c=_ROUTE_PRECIP,
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    return store_handle(
        session,
        tool="evaluate_process",
        source_basis="tea_cache_exact",
        data=payload,
    ), payload


def test_residual_route_omitted_handle_is_unknown_handle(monkeypatch):
    _forbid_live_residual_route(monkeypatch)
    payload = _data(tea.rank_landscape(source="residual_route", operation="optimum"))
    assert payload.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "rank_landscape"


def test_residual_route_planner_handle_is_not_the_costed_route(monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live_residual_route(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        plan = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="optimum",
            handle=plan,
        ))
    assert payload.get("error_code") == "not_route_handle"
    assert payload.get("error_code") != "tool_not_wired"


def test_residual_route_mixed_polymer_grouping_is_not_applicable(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="pareto_dominance",
            handle=handle,
            polymer_grouping="mixed_polymer",
        ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("inapplicable_fields") == ["polymer_grouping"]
    assert payload.get("source") == "residual_route"


def test_residual_route_sort_is_not_applicable(monkeypatch):
    _forbid_live_residual_route(monkeypatch)
    payload = _data(tea.rank_landscape(source="residual_route", operation="sort"))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("operation") == "sort"
    assert payload.get("error_code") != "tool_not_wired"


def test_residual_route_does_not_read_getattr_source_state(monkeypatch):
    monkeypatch.setattr(
        tea_ranking,
        "_source_state",
        lambda: (_ for _ in ()).throw(
            AssertionError("residual_route must not getattr last_route")
        ),
    )
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        assert session.get("last_route") is None
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="optimum",
            handle=handle,
        ))
    assert payload.get("success") is True
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("source") == "residual_route"
    assert payload.get("operation") == "optimum"
    assert payload.get("analysis_type") == "point_optimum"
    assert payload.get("selected_point")
    landscape = payload.get("landscape_points") or []
    assert len(landscape) == payload.get("n_landscape_points")
    assert payload.get("n_landscape_points") >= 2
    assert payload.get("cheapest_point")
    assert payload.get("solver")
    for point in landscape:
        assert point["safety_standing"]["status"] in {
            "evaluated", "not_requested", "unavailable",
        }
    assert payload["selected_point"]["safety_standing"]["status"] in {
        "evaluated", "not_requested", "unavailable",
    }
    assert payload["cheapest_point"]["safety_standing"]["status"] in {
        "evaluated", "not_requested", "unavailable",
    }


def test_residual_route_pareto_returns_landscape_and_frontier(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="pareto_dominance",
            handle=handle,
        ))
    assert payload.get("success") is True
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("source") == "residual_route"
    assert payload.get("operation") == "pareto_dominance"
    landscape = payload.get("landscape_points") or []
    frontier = payload.get("frontier_points") or []
    assert landscape
    assert frontier
    assert payload.get("n_landscape_points") == len(landscape)
    assert payload.get("n_frontier_points") == len(frontier)
    assert payload.get("n_frontier_points") <= payload.get("n_landscape_points")
    assert payload.get("points") == frontier
    assert payload.get("knee_status") in {
        "endpoint_only_no_interior_knee",
        "interior_tradeoff",
        "not_calculated_no_comparable_designs",
    }
    assert payload.get("cheapest_point")
    assert "frontier_tradeoff" in payload
    n_land = payload["n_landscape_points"]
    n_front = payload["n_frontier_points"]
    assert payload.get("frontier_fraction") == n_front / n_land
    assert payload.get("sparse_frontier") is (
        n_front == 1 or payload.get("cheapest_equals_lowest_y") is True
    )
    assert isinstance(payload.get("cheapest_equals_lowest_y"), bool)
    assert payload.get("sparse_frontier") is not None
    for point in landscape + frontier + [payload["cheapest_point"]]:
        assert point["safety_standing"]["status"] in {
            "evaluated", "not_requested", "unavailable",
        }
    assert all(
        point["safety_standing"]["status"] == "not_requested"
        for point in landscape
    )
    x_key = payload.get("x_metric") or "total_cost"
    y_key = payload.get("y_metric") or "emissions"
    spans = payload.get("axis_spans") or {}
    assert set(spans) == {x_key, y_key}
    costs = [float(point[x_key]) for point in landscape]
    emissions = [float(point[y_key]) for point in landscape]
    assert spans[x_key]["min"] == min(costs)
    assert spans[x_key]["max"] == max(costs)
    assert spans[y_key]["min"] == min(emissions)
    assert spans[y_key]["max"] == max(emissions)
    for axis, values in ((x_key, costs), (y_key, emissions)):
        span = spans[axis]
        assert span["min"] <= span["p05"] <= span["p95"] <= span["max"]
        assert span == tea_ranking.axis_span(values)
    slice0 = (payload.get("slices") or [{}])[0]
    assert slice0.get("axis_spans") == spans
    grouping = payload.get("grouping") or {}
    assert "polymer_grouping" not in grouping
    assert grouping.get("source_route_signature") == payload.get(
        "source_route_signature",
    )
    assert grouping.get("feed_mass_fractions") == payload.get("feed_mass_fractions")
    assert grouping.get("feed_mt_per_yr") == payload.get("feed_mt_per_yr")
    assert slice0.get("grouping") == grouping
    tradeoff = payload.get("frontier_tradeoff")
    if payload.get("cheapest_equals_lowest_y") or n_front < 2:
        assert tradeoff is None
    else:
        assert tradeoff is not None
        assert tradeoff["x_metric"] == x_key
        assert tradeoff["y_metric"] == y_key
        assert tradeoff["x_direction"] == "min"
        assert tradeoff["y_direction"] == "min"
        assert tradeoff["x_units"] == "USD/yr"
        assert tradeoff["y_units"] == "t CO2e/yr"
        assert "incremental_annual_cost_usd" not in tradeoff
        cheapest_x = min(float(point[x_key]) for point in frontier)
        best_y = min(float(point[y_key]) for point in frontier)
        assert tradeoff["x_at_cheapest"] == cheapest_x
        assert tradeoff["y_at_best_y"] == best_y
        assert tradeoff["delta_x"] == tradeoff["x_at_best_y"] - tradeoff["x_at_cheapest"]
        assert tradeoff["delta_y"] == tradeoff["y_at_best_y"] - tradeoff["y_at_cheapest"]
        assert slice0.get("frontier_tradeoff") == tradeoff


def test_pareto_name_retired_both_successors_serve(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        route_handle, _costed = _costed_route_handle(session, monkeypatch)
        residual = dispatch(
            "rank_landscape",
            source="residual_route",
            operation="pareto_dominance",
            handle=route_handle,
        )
        assert residual.get("available") is True
        assert residual["data"]["source"] == "residual_route"
        assert residual["data"]["operation"] == "pareto_dominance"
        assert residual["data"].get("n_frontier_points") >= 1
        c1 = _record_by_label("ldpe-route-c1")
        c2 = _record_by_label("ldpe-route-c2")
        batch = _data(tea.evaluate_process(
            mode="evaluate",
            process_configs=[
                _public_from_record(c1),
                _public_from_record(c2),
            ],
            engine_mode="auto",
        ))
        assert batch.get("success") is True
        batch_handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=batch,
        )
        process_rows = dispatch(
            "rank_landscape",
            source="process_rows",
            operation="pareto_dominance",
            handle=batch_handle,
        )
        assert process_rows.get("available") is True
        assert process_rows["data"]["source"] == "process_rows"
        assert process_rows["data"]["operation"] == "pareto_dominance"
        assert process_rows["data"].get("n_frontier_points") >= 1
        old = dispatch("pareto_optimize_stored_route")
        assert old.get("available") is False
        assert old.get("refusal") == "unknown_tool"
    assert callable(tea_ranking.pareto_optimize_stored_route)
    engine = _data(tea_ranking.pareto_optimize_stored_route())
    assert engine.get("error_code") == "invalid_pareto_basis"
    assert engine.get("tool_name") == "pareto_optimize_stored_route"


def test_dispatch_residual_route_issues_handle(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        out = dispatch(
            "rank_landscape",
            source="residual_route",
            operation="optimum",
            handle=handle,
        )
        assert out.get("available") is True
        assert out.get("handle")
        assert out.get("handle") != handle
        stored = load_handle(session, out["handle"])
        assert stored.get("tool") == "rank_landscape"
        assert stored["exact"]["source"] == "residual_route"
        old = dispatch("optimize_stored_route")
        assert old.get("available") is False
        assert old.get("refusal") == "unknown_tool"
        assert callable(tea_ranking.optimize_stored_route)
        engine = _data(tea_ranking.optimize_stored_route())
        assert engine.get("error_code") == "invalid_optimization_basis"
        assert engine.get("tool_name") == "optimize_stored_route"


def test_objective_on_process_rows_is_not_applicable(monkeypatch):
    _forbid_live_residual_route(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="process_rows",
        operation="pareto_dominance",
        objective="min_cost",
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("inapplicable_fields") == ["objective"]
    assert payload.get("source") == "process_rows"


def test_residual_pareto_quality_matches_fraction_and_sparse_definition():
    one = {"total_cost": 1.0, "emissions": 2.0}
    two = {"total_cost": 3.0, "emissions": 1.0}
    star = tea_ranking._residual_pareto_quality(
        [one, two], [one], "total_cost", "emissions",
    )
    assert star["frontier_fraction"] == 0.5
    assert star["sparse_frontier"] is True
    assert star["cheapest_equals_lowest_y"] is True
    tradeoff = tea_ranking._residual_pareto_quality(
        [one, two], [one, two], "total_cost", "emissions",
    )
    assert tradeoff["frontier_fraction"] == 1.0
    assert tradeoff["cheapest_equals_lowest_y"] is False
    assert tradeoff["sparse_frontier"] is False
    spans = star["axis_spans"]
    assert spans["total_cost"]["min"] == 1.0
    assert spans["total_cost"]["max"] == 3.0
    assert spans["emissions"]["min"] == 1.0
    assert spans["emissions"]["max"] == 2.0
    singleton = tea_ranking._residual_pareto_quality(
        [one], [one], "total_cost", "emissions",
    )
    cost_span = singleton["axis_spans"]["total_cost"]
    assert cost_span["min"] == cost_span["p05"] == cost_span["p95"] == cost_span["max"] == 1.0
    generic = tea_ranking._metric_generic_tradeoff(
        [one, two], "total_cost", "emissions", cheapest_equals_lowest_y=False,
    )
    assert generic is not None
    assert generic["x_metric"] == "total_cost"
    assert generic["y_metric"] == "emissions"
    assert generic["x_at_cheapest"] == 1.0
    assert generic["y_at_cheapest"] == 2.0
    assert generic["x_at_best_y"] == 3.0
    assert generic["y_at_best_y"] == 1.0
    assert generic["delta_x"] == 2.0
    assert generic["delta_y"] == -1.0
    assert generic["x_ratio"] == 3.0
    assert "incremental_annual_cost_usd" not in generic
    assert tea_ranking._metric_generic_tradeoff(
        [one], "total_cost", "emissions", cheapest_equals_lowest_y=False,
    ) is None
    assert tea_ranking._metric_generic_tradeoff(
        [one, two], "total_cost", "emissions", cheapest_equals_lowest_y=True,
    ) is None


def test_residual_route_optimum_omits_frontier_fraction(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="optimum",
            handle=handle,
        ))
    assert payload.get("success") is True
    assert "frontier_fraction" not in payload
    assert "sparse_frontier" not in payload
    assert "axis_spans" not in payload
    assert "grouping" not in payload
    landscape = payload.get("landscape_points") or []
    assert landscape
    assert all(
        point["safety_standing"]["status"] == "not_requested"
        for point in landscape
    )


# --- from test_contaminant_leftovers_cl4.py: CL-4: a wash is visible to TEA and is not silently free.
_DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"


def _data_contaminant_leftovers_cl4(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _wash(**kwargs) -> dict:
    step = {
        "step_kind": "wash",
        "path": "leaching",
        "solvent": "acetone",
        "temperature_c": 25.0,
        "contaminants_targeted": [_DEHP],
    }
    step.update(kwargs)
    return step


def _cost(monkeypatch, composition: dict, route: dict) -> dict:
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        return _data_contaminant_leftovers_cl4(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="auto",
        ))


def test_wash_identity_is_step_kind_not_a_polymer_sentinel():
    ident = tea._stage_identity(None, "acetone", 25.0, "wash")
    assert ident[3] == "wash"
    assert ident[0] == ""
    assert tea._stage_visible(ident) is True
    dissolution = tea._stage_identity("LDPE", "toluene", 145.0)
    assert dissolution[3] == "dissolution"
    assert dissolution[0] == "ldpe"


def test_later_position_wash_refuses_incomplete_stage_basis_grid(monkeypatch):
    composition, route = _exact_planner_route()
    later = copy.deepcopy(route)
    later["steps"] = [dict(item) for item in later["steps"]] + [_wash()]
    payload = _cost(monkeypatch, composition, later)
    assert payload["success"] is False
    assert payload["error_code"] == "stage_basis_not_derived"
    assert payload["named_blocker"] == "incomplete_stage_basis_grid"
    assert payload.get("step_kind") == "wash"
    assert payload.get("wash_position") == 1
    assert payload.get("recovered_polymer_kg") == 0.0
    assert payload.get("disposal_cost") == "not_costed"
    assert not isinstance(payload.get("disposal_cost"), (int, float))
    assert payload.get("per_stage_usd_per_kg") is None


def test_position_zero_wash_refuses_rather_than_invent_a_basis(monkeypatch):
    composition, route = _exact_planner_route()
    first = copy.deepcopy(route)
    first["steps"] = [_wash()] + [dict(item) for item in first["steps"]]
    payload = _cost(monkeypatch, composition, first)
    assert payload["success"] is False
    assert payload["error_code"] == "stage_basis_not_derived"
    assert payload["named_blocker"] == "incomplete_stage_basis_grid"
    assert payload.get("wash_position") == 0
    assert set(payload.get("missing_wash_basis") or []) >= {
        "solvent_charge", "vessel", "residence_time", "waste_mass",
    }
    assert payload.get("recovered_polymer_kg") == 0.0
    assert payload.get("disposal_cost") == "not_costed"
    assert payload.get("per_stage_usd_per_kg") is None


def test_wash_does_not_keyerror_or_count_as_polymer(monkeypatch):
    composition, route = _exact_planner_route()
    with_wash = copy.deepcopy(route)
    with_wash["steps"] = [_wash()] + [dict(item) for item in with_wash["steps"]]
    payload = _cost(monkeypatch, composition, with_wash)
    assert payload.get("error_code") != "route_feed_mismatch"
    assert "KeyError" not in str(payload.get("error") or "")
    consumed = payload.get("consumed_route") or {}
    for item in consumed.get("steps") or []:
        if item.get("step_kind") == "wash":
            assert item.get("dissolved_polymer") not in {"wash", "Wash"}


def test_formulation_wash_train_stays_unavailable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data_contaminant_leftovers_cl4(tea.rank_landscape(
        source="superstructure",
        formulation="wash_train",
    ))
    assert payload["error_code"] == "process_model_wash_train_unavailable"
    assert payload.get("formulation") == "wash_train"


# --- from test_contaminant_leftovers_cl6.py: CL-6: the contaminant-step TEA routing table, as measured.
_REFUSE = "stage_basis_not_derived"


_BLOCKER = "incomplete_stage_basis_grid"


_WASH_BASIS = ("solvent_charge", "vessel", "residence_time", "waste_mass")


# Measured on fff5c2c. The spec draft said position 0 would be costed.
# Hold-to 5 refused that number; both wash rows refuse. Write it that way.
ROUTING_TABLE = (
    {
        "step_kind": "leaching wash, position 0",
        "reaches_tea": True,
        "identity": "step_kind=wash",
        "recovers": 0.0,
        "before_cl4": "free — dropped by ident[0]",
        "after_cl4": "refuse incomplete_stage_basis_grid",
    },
    {
        "step_kind": "leaching wash, later position",
        "reaches_tea": True,
        "identity": "step_kind=wash",
        "recovers": 0.0,
        "before_cl4": "free — dropped by ident[0]",
        "after_cl4": "refuse incomplete_stage_basis_grid",
    },
    {
        "step_kind": "STRAP dissolution w/ contaminants",
        "reaches_tea": True,
        "identity": "dissolution (polymer, solvent, T)",
        "recovers": "the polymer",
        "before_cl4": "costed",
        "after_cl4": "unchanged — stamp inert",
    },
)


def _data_contaminant_leftovers_cl6(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _refuse_fields(payload: dict) -> None:
    assert payload["success"] is False
    assert payload["error_code"] == _REFUSE
    assert payload["named_blocker"] == _BLOCKER
    assert payload.get("step_kind") == "wash"
    assert payload.get("recovered_polymer_kg") == 0.0
    assert payload.get("disposal_cost") == "not_costed"
    assert not isinstance(payload.get("disposal_cost"), (int, float))
    assert payload.get("per_stage_usd_per_kg") is None


def test_routing_table_names_three_contaminant_step_kinds():
    kinds = [row["step_kind"] for row in ROUTING_TABLE]
    assert kinds == [
        "leaching wash, position 0",
        "leaching wash, later position",
        "STRAP dissolution w/ contaminants",
    ]
    assert all(row["reaches_tea"] is True for row in ROUTING_TABLE)
    assert ROUTING_TABLE[0]["after_cl4"] == ROUTING_TABLE[1]["after_cl4"] == (
        "refuse incomplete_stage_basis_grid"
    )
    assert "costed" not in ROUTING_TABLE[0]["after_cl4"]
    assert ROUTING_TABLE[2]["after_cl4"].startswith("unchanged")


def test_leaching_wash_position_0_reaches_tea_and_refuses(monkeypatch):
    ident = tea._stage_identity(None, "acetone", 25.0, "wash")
    assert ident[3] == "wash"
    assert ident[0] == ""
    assert tea._stage_visible(ident) is True
    composition, route = _exact_planner_route()
    first = copy.deepcopy(route)
    first["steps"] = [_wash()] + [dict(item) for item in first["steps"]]
    payload = _cost(monkeypatch, composition, first)
    _refuse_fields(payload)
    assert payload.get("wash_position") == 0
    assert set(payload.get("missing_wash_basis") or []) >= set(_WASH_BASIS)


def test_leaching_wash_later_position_reaches_tea_and_refuses(monkeypatch):
    composition, route = _exact_planner_route()
    later = copy.deepcopy(route)
    later["steps"] = [dict(item) for item in later["steps"]] + [_wash()]
    payload = _cost(monkeypatch, composition, later)
    _refuse_fields(payload)
    assert payload.get("wash_position") == 1


def test_cited_wash_basis_still_has_no_cost_path(monkeypatch):
    composition, route = _exact_planner_route()
    cited = _wash(
        solvent_charge=1,
        vessel=1,
        residence_time=1,
        waste_mass=1,
        field_origin={name: "constructed" for name in _WASH_BASIS},
    )
    first = copy.deepcopy(route)
    first["steps"] = [cited] + [dict(item) for item in first["steps"]]
    payload = _cost(monkeypatch, composition, first)
    _refuse_fields(payload)
    assert payload.get("missing_wash_basis") == []
    assert set(payload.get("cited_wash_basis") or []) >= set(_WASH_BASIS)


def test_strap_dissolution_is_ordinary_and_stamp_inert(monkeypatch):
    composition, unset_route = _exact_planner_route()
    stamped_plan = _data_contaminant_leftovers_cl6(separation.plan_multistage_separation(
        list(composition),
        feed_mass_fractions=dict(composition),
        contaminants=_DEHP,
        contaminant_mode="strap",
        top_k_routes=10,
        breadth=1,
    ))
    assert stamped_plan["success"] is True
    unset_dissolutions = [
        (
            item.get("dissolved_polymer") or item.get("polymer"),
            item.get("solvent"),
            item.get("temperature_c"),
        )
        for item in unset_route["steps"]
        if item.get("step_kind") != "wash"
        and (item.get("dissolved_polymer") or item.get("polymer"))
    ]
    stamped_route = next(
        route
        for route in (stamped_plan.get("top_k_sequences") or [])
        if [
            (
                item.get("dissolved_polymer") or item.get("polymer"),
                item.get("solvent"),
                item.get("temperature_c"),
            )
            for item in route.get("steps") or []
            if item.get("step_kind") != "wash"
            and (item.get("dissolved_polymer") or item.get("polymer"))
        ] == unset_dissolutions
    )
    assert all(item.get("step_kind") != "wash" for item in stamped_route["steps"])
    unset_cost = _cost(monkeypatch, composition, unset_route)
    strap_cost = _cost(monkeypatch, composition, stamped_route)
    assert unset_cost["success"] is True
    assert strap_cost["success"] is True
    unset_msp = unset_cost["mass_weighted_recovered_msp_usd_per_kg"]
    strap_msp = strap_cost["mass_weighted_recovered_msp_usd_per_kg"]
    assert struct.pack(">d", float(strap_msp)) == struct.pack(">d", float(unset_msp))


def test_wash_train_formulation_stays_unavailable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data_contaminant_leftovers_cl6(tea.rank_landscape(
        source="superstructure",
        formulation="wash_train",
    ))
    assert payload["error_code"] == "process_model_wash_train_unavailable"


# --- from test_contaminant_tea_counterfactual.py: CL-5: the STRAP stamp is inert to costing. Test-only if MSPs already match.
def _data_contaminant_tea_counterfactual(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _dissolutions(route: dict) -> list[tuple[str, str, float | None]]:
    out = []
    for item in route.get("steps") or []:
        if item.get("step_kind") == "wash":
            continue
        polymer = item.get("dissolved_polymer") or item.get("polymer")
        if not polymer:
            continue
        out.append((
            str(polymer),
            str(item.get("solvent") or ""),
            item.get("temperature_c"),
        ))
    return out


def _identities(route: dict) -> list[tuple[str, str, float | None]]:
    return [
        tea._stage_identity(polymer, solvent, temperature)
        for polymer, solvent, temperature in _dissolutions(route)
    ]


def _recovered_mt(payload: dict) -> float:
    return sum(
        float(row["modeled_stage_product_mt_per_yr"])
        for row in (payload.get("stage_results") or [])
        if row.get("modeled_stage_product_mt_per_yr") is not None
    )


def _bits(value: float) -> bytes:
    return struct.pack(">d", float(value))


def test_strap_stamp_is_inert_to_cached_msp(monkeypatch):
    composition, unset_route = _exact_planner_route()
    feed = list(composition)
    stamped_plan = _data_contaminant_tea_counterfactual(separation.plan_multistage_separation(
        feed,
        feed_mass_fractions=dict(composition),
        contaminants=_DEHP,
        contaminant_mode="strap",
        top_k_routes=10,
        breadth=1,
    ))
    assert stamped_plan["success"] is True
    assert stamped_plan["contaminant_mode"] == "strap"
    stamped_route = next(
        (
            route for route in (stamped_plan.get("top_k_sequences") or [])
            if _dissolutions(route) == _dissolutions(unset_route)
        ),
        None,
    )
    assert stamped_route is not None
    assert any(item.get("path") == "strap" for item in stamped_route["steps"])
    assert all(item.get("step_kind") != "wash" for item in stamped_route["steps"])
    assert _identities(stamped_route) == _identities(unset_route)

    unset_cost = _cost(monkeypatch, composition, unset_route)
    strap_cost = _cost(monkeypatch, composition, stamped_route)
    assert unset_cost["success"] is True
    assert strap_cost["success"] is True
    unset_msp = unset_cost["mass_weighted_recovered_msp_usd_per_kg"]
    strap_msp = strap_cost["mass_weighted_recovered_msp_usd_per_kg"]
    assert unset_msp is not None and strap_msp is not None
    assert _bits(strap_msp) == _bits(unset_msp)
    assert _recovered_mt(strap_cost) == _recovered_mt(unset_cost)
    recovered_names = json.dumps(strap_cost.get("stage_results") or [])
    assert "phthalate" not in recovered_names.casefold()
    assert "dehp" not in recovered_names.casefold()
    assert "contaminant" not in [
        str(row.get("polymer") or row.get("target_plastic") or "").casefold()
        for row in (strap_cost.get("stage_results") or [])
    ]
