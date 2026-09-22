"""rank_landscape ranks a tool-1 handle. Not a fingerprint and not a live child."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from dissolve.agent_tools import CONSUMERS, dispatch, tool_schemas
from dissolve import tea
from dissolve.session import bind_tool_session, new_session, store_handle


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


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


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("rank from a handle must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(
        tea.tea_worker, "run", forbidden,
    )


def _store_evaluate(session, scenarios):
    first = _data(tea.evaluate_tea_lca_scenarios(
        scenarios, engine_mode="cache",
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
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        payload = _data(tea.rank_landscape(handle=handle))
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
        assert point["lca_coverage"]
        assert point["lca_coverage"].get("lca_metric_status")
        assert point["safety_standing"]["status"] == "not_requested"
        assert "engine_envelope" not in point
    assert payload["n_frontier_points"] >= 1
    assert payload["sparse_frontier"] is True or payload["n_frontier_points"] >= 1


def test_evaluate_handle_carries_bound_safety_standing(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(c1), _public_from_record(c2)],
            engine_mode="cache",
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
        payload = _data(tea.rank_landscape(handle=handle))
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
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        payload = _data(tea.rank_landscape(handle=handle))
    assert payload.get("success") is True
    assert payload.get("error_code") != "mixed_campaign_basis"
    cases = {point["energy_case"] for point in payload["landscape_points"]}
    assert cases == {"C1", "C2"}


def test_one_row_evaluate_handle_is_landscape_too_small(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate(session, [_public_from_record(c1)])
        payload = _data(tea.rank_landscape(handle=handle))
    assert payload.get("error_code") == "landscape_too_small"
    assert payload["n_usable"] == 1
    assert payload["ingested_into_admitted_cache"] is False
    assert "landscape_points" not in payload
    assert "frontier_points" not in payload


def test_admitted_lookup_handle_ranks_energy_cases(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
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
        payload = _data(tea.rank_landscape(handle=handle))
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
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        wrapped = dispatch(
            "evaluate_process",
            mode="sensitivity",
            process_config=_public_from_record(record),
            parameter="solvent_price",
            engine_mode="cache",
        )
        retired = dispatch(
            "analyze_tea_sensitivity",
            scenario=_public_from_record(record),
            parameter="solvent_price",
            engine_mode="cache",
        )
        assert wrapped.get("available") is True
        assert retired.get("available") is False
        assert retired.get("refusal") == "unknown_tool"
        engine = _data(tea.analyze_tea_sensitivity(
            _public_from_record(record),
            parameter="solvent_price",
            engine_mode="cache",
        ))
        engine_handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=engine,
        )
        wrapped_rank = _data(tea.rank_landscape(handle=wrapped["handle"]))
        engine_rank = _data(tea.rank_landscape(handle=engine_handle))
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
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        assert screen.get("handle")
        payload = _data(tea.rank_landscape(handle=screen["handle"]))
    assert payload.get("error_code") == "not_economics_handle"
    assert "landscape_points" not in payload


def test_rank_handle_cannot_be_ranked_as_tool1(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        ranked = _data(tea.rank_landscape(handle=handle))
        rank_handle = store_handle(
            session,
            tool="rank_landscape",
            source_basis="tea_cache_exact",
            data=ranked,
        )
        payload = _data(tea.rank_landscape(handle=rank_handle))
    assert first.get("success") is True
    assert payload.get("error_code") == "not_economics_handle"


def test_unknown_handle_is_not_a_fingerprint_lookup(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        payload = _data(tea.rank_landscape(handle="no-such-handle"))
        empty = _data(tea.rank_landscape(handle=""))
    assert payload.get("error_code") == "unknown_handle"
    assert empty.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "missing_campaign_fingerprint"


def test_extra_fingerprint_does_not_locate_a_campaign(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate(
            session,
            [_public_from_record(c1), _public_from_record(c2)],
        )
        payload = _data(tea.rank_landscape(
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
    _forbid_live(monkeypatch)
    row_a = _public_from_record(c1)
    row_a.update({
        "msp_usd_per_kg": 1.0,
        "gwp_kg_co2e_per_kg": 0.4,
        "success": True,
        "engine_mode": "cache",
        "lca_metric_status": {
            "gwp_kg_co2e_per_kg": "cache_gwp_natural_gas_combustion_double_count",
        },
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
        payload = _data(tea.rank_landscape(handle=handle))
    assert payload.get("error_code") == "mixed_campaign_basis"
    assert "landscape_points" not in payload


def test_handle_is_native_not_a_consumer(monkeypatch):
    _forbid_live(monkeypatch)
    assert "rank_landscape" not in CONSUMERS
    schemas = {item["name"]: item for item in tool_schemas()}
    props = schemas["rank_landscape"]["parameters"]["properties"]
    assert "handle" in props
    assert "default" not in props["handle"]
    assert "handle" not in (schemas["rank_landscape"]["parameters"].get("required") or [])


def test_dispatch_ranks_the_evaluate_handle(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        evaluated = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_configs=[_public_from_record(c1), _public_from_record(c2)],
            engine_mode="cache",
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
