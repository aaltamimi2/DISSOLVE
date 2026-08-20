"""evaluate_process lookup and evaluate. Old TEA names stay. Not sensitivity/route."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import UNWIRED, dispatch, tool_schemas
from dissolve import campaign_consume, registry, tea
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


def test_evaluate_process_is_registered_lookup_stays():
    assert "evaluate_process" in registry.BY_NAME
    assert "evaluate_process" in EXPECTED_REGISTRY_NAMES
    assert "lookup_admitted_process_records" in registry.BY_NAME
    assert "evaluate_process" not in UNWIRED
    assert "evaluate_process" in tea.PROCESS_CONFIRM_TOOLS
    assert len(EXPECTED_REGISTRY_NAMES) == 35
    assert len(registry.REGISTRY) == 35


def test_schema_uses_two_typed_objects_not_top_level_polymer():
    props = _schema("evaluate_process")["parameters"]["properties"]
    assert "mode" in props
    assert "lookup_filter" in props
    assert "process_config" in props
    assert "process_configs" in props
    assert props["lookup_filter"]["type"] == "object"
    assert props["process_config"]["type"] == "object"
    assert props["process_configs"]["type"] == "array"
    assert props["process_configs"]["items"]["type"] == "object"
    assert "target_polymer" not in props
    assert "solvent" not in props
    assert "parameter" not in props
    assert "scenarios" not in props
    required = _schema("evaluate_process")["parameters"].get("required") or []
    assert "mode" not in required
    assert "lookup_filter" not in required
    assert "process_config" not in required
    assert "process_configs" not in required


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


def test_sensitivity_and_route_stay_unwired_on_this_name(monkeypatch):
    _forbid_live(monkeypatch)
    for token in ("sensitivity", "route"):
        payload = _data(tea.evaluate_process(mode=token))
        assert payload.get("success") is False
        assert payload.get("error_code") == "tool_not_wired"
        assert payload.get("mode") == token
        filtered = _data(tea.evaluate_process(
            mode=token,
            lookup_filter={"target_polymer": "LDPE"},
        ))
        assert filtered.get("error_code") == "not_applicable_in_mode"
        assert filtered.get("inapplicable_fields") == ["lookup_filter"]
    batch = _data(tea.evaluate_process(
        mode="sensitivity",
        process_configs=[{"target_polymer": "LDPE"}],
    ))
    assert batch.get("error_code") == "not_applicable_in_mode"
    assert batch.get("inapplicable_fields") == ["process_configs"]
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
