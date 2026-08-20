"""planner_solvent_map / allowed_solvents are rank_landscape arguments.

A bound map still has no remnant table: incomplete_stage_basis_grid,
not a ranking, not a cache fill, and not a scan of top_k_sequences.
feed_mass_fractions expands listed remnant keys by D-18; it is not ingest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import CONSUMERS, dispatch, tool_schemas
from dissolve import tea
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.session import bind_tool_session, new_session, store_handle


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _forbid_live(monkeypatch):
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={"LDPE": {"solvent": "Toluene", "temperature_c": 25}},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_feed_polymer_without_solvent_is_missing_planner_map(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_complete_planner_map_is_incomplete_stage_basis_grid(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        process_config={"planner_solvent_map": _PLAN_MAP},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_solvent_without_allowed_set_is_missing_allowed_solvents(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("formulation") == "solvent"
    assert "landscape_points" not in payload


def test_empty_allowed_solvents_is_missing(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert "landscape_points" not in payload


def test_flat_allowed_list_is_incomplete_stage_basis_grid(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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


def test_residual_route_stays_unwired_without_requiring_maps(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="residual_route",
        formulation="sequence",
    ))
    assert payload.get("error_code") == "tool_not_wired"
    assert payload.get("source") == "residual_route"


def test_maps_on_process_rows_are_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="residual_route",
        planner_solvent_map=_PLAN_MAP,
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "residual_route"
    assert payload.get("inapplicable_fields") == ["planner_solvent_map"]


def test_top_k_sequences_is_unknown_extra_not_the_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        top_k_sequences=[{"steps": [{"dissolved_polymer": "LDPE", "solvent": "Toluene"}]}],
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["top_k_sequences"]
    assert payload.get("error_code") != "missing_planner_solvent_map"


def test_exclude_safety_fail_stays_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        exclude_safety_fail=True,
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["exclude_safety_fail"]


def test_maps_are_optional_schema_args_not_a_35th_name(monkeypatch):
    _forbid_live(monkeypatch)
    assert "rank_landscape" not in CONSUMERS
    assert len(EXPECTED_REGISTRY_NAMES) == 35
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
    _forbid_live(monkeypatch)
    ranked = dispatch(
        "rank_landscape",
        source="superstructure",
        formulation="sequence",
    )
    assert ranked.get("available") is False
    assert ranked.get("refusal") == "missing_planner_solvent_map"
    assert ranked.get("handle") is None


def test_dispatch_of_complete_map_is_the_data_gate(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)

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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence_solvent",
        screening_shortlist={"source": "explicit", "items": []},
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["screening_shortlist"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_sequence_solvent_empty_table_is_still_incomplete(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="Sequence",
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("formulation") == "sequence"


def test_dispatch_names_missing_formulation(monkeypatch):
    _forbid_live(monkeypatch)
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
        tool="evaluate_tea_lca_scenarios",
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "process_rows"
    assert payload.get("inapplicable_fields") == ["feed_mass_fractions"]
    assert "landscape_points" not in payload


def test_unknown_handle_is_not_an_empty_remnant_table(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(handle="not-a-remnant-table"),
    ))
    assert payload.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload


def test_unknown_handle_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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


def test_malformed_planner_map_does_not_inherit_from_handle(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)

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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert "energy_case" not in row or row.get("energy_case") is None
        assert row.get("energy_case") != "C1"


def test_c2_rows_still_complete_when_energy_case_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(energy_case="C2"))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_c1_slice_does_not_accept_c2_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(energy_case="C2"))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "energy_case": "C1"},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_c1_slice_completes_on_c1_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(energy_case="C1"))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "energy_case": "C1"},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_energy_case_is_listed_without_a_handle(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "energy_case": "c2"}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C2"}
    assert all(row["target_mass_percent"] is not None for row in keys)


def test_invalid_energy_case_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "energy_case": "C9"}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert "landscape_points" not in payload


def test_invalid_energy_case_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "energy_case": "C9"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_dissolution_t_is_not_a_silent_cache_temperature(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_temperature_c") is None


def test_planted_t_still_completes_when_dissolution_t_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_temperature_c=80.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_dissolution_t_does_not_accept_another_t(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_temperature_c=80.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_temperature_c": 90.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_dissolution_t_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_temperature_c=90.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_temperature_c": 90.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_dissolution_temp_c_alias_stamps_the_public_name(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "dissolution_temp_c": 90}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}


def test_c1_at_wrong_t_does_not_fill_named_c1_and_t(monkeypatch):
    _forbid_live(monkeypatch)
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


def test_dissolution_temperature_c_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(dissolution_temperature_c=90.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["dissolution_temperature_c"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_dissolution_t_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"dissolution_temperature_c": 90.0},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_temperature_c") is None


def test_invalid_dissolution_t_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_temperature_c": "hot"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_dissolution_t_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "dissolution_temperature_c": "hot"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_precipitation_t_is_not_a_silent_cache_temperature(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_c") is None


def test_empty_precipitation_t_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_temperature_c": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_c") is None


def test_planted_t_still_completes_when_precipitation_t_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_temperature_c=35.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_precipitation_t_does_not_accept_another_t(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_temperature_c=35.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_temperature_c": 40.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_precipitation_t_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_temperature_c=40.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_temperature_c": 40.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_precipitation_temp_c_alias_stamps_the_public_name(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "precipitation_temp_c": 40}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}


def test_public_precipitation_t_wins_over_alias(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_temperature_c": 40.0,
                "precipitation_temp_c": 35.0,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}


def test_dissolution_t_does_not_stamp_precipitation_t(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_temperature_c": 90.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_temperature_c") == 90.0
        assert row.get("precipitation_temperature_c") is None


def test_precipitation_t_does_not_stamp_dissolution_t(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_temperature_c": 40.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_c") == 40.0
        assert row.get("dissolution_temperature_c") is None


def test_c1_and_dissolution_t_at_wrong_precip_do_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
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


def test_mixed_precip_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(precipitation_temperature_c=40.0)
    rows[-1]["precipitation_temperature_c"] = 35.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_temperature_c": 40.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["precipitation_temperature_c"] == 40.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_alias_only_row_does_not_fill_named_precipitation_t(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows()
    for row in rows:
        row["precipitation_temp_c"] = 40.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "precipitation_temperature_c": 40.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}


def test_precipitation_temperature_c_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(precipitation_temperature_c=40.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["precipitation_temperature_c"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_precipitation_temp_c_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(precipitation_temp_c=40.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["precipitation_temp_c"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_precipitation_t_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"precipitation_temperature_c": 40.0},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_c") is None


def test_invalid_precipitation_t_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_temperature_c": "cold"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_precipitation_t_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "precipitation_temperature_c": "cold"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_solvent_price_is_not_a_silent_cache_price(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_price_usd_per_kg") is None


def test_empty_solvent_price_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_price_usd_per_kg": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_price_usd_per_kg") is None


def test_planted_price_still_completes_when_solvent_price_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(solvent_price_usd_per_kg=1.5),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_solvent_price_does_not_accept_another_price(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(solvent_price_usd_per_kg=1.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_price_usd_per_kg": 2.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_solvent_price_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(solvent_price_usd_per_kg=2.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_price_usd_per_kg": 2.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_solvent_price_alias_stamps_the_public_name(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "solvent_price": 2.0}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}


def test_public_solvent_price_wins_over_alias(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "solvent_price_usd_per_kg": 2.0,
                "solvent_price": 1.5,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}


def test_precipitation_t_does_not_stamp_solvent_price(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_temperature_c": 40.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_c") == 40.0
        assert row.get("solvent_price_usd_per_kg") is None


def test_solvent_price_does_not_stamp_precipitation_t(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_price_usd_per_kg": 2.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_price_usd_per_kg") == 2.0
        assert row.get("precipitation_temperature_c") is None


def test_solvent_loss_does_not_stamp_solvent_price(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_loss_pct": 3.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_price_usd_per_kg") is None


def test_named_slice_at_wrong_price_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=1.5,
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
                    "solvent_price_usd_per_kg": 2.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}


def test_mixed_price_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(solvent_price_usd_per_kg=2.0)
    rows[-1]["solvent_price_usd_per_kg"] = 1.5
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_price_usd_per_kg": 2.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["solvent_price_usd_per_kg"] == 2.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_alias_only_row_does_not_fill_named_solvent_price(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows()
    for row in rows:
        row["solvent_price"] = 2.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_price_usd_per_kg": 2.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}


def test_solvent_price_usd_per_kg_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(solvent_price_usd_per_kg=2.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["solvent_price_usd_per_kg"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_solvent_price_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(solvent_price=2.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["solvent_price"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_solvent_price_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"solvent_price_usd_per_kg": 2.0},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_price_usd_per_kg") is None


def test_invalid_solvent_price_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_price_usd_per_kg": "cheap"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_solvent_price_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "solvent_price_usd_per_kg": "cheap"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_solvent_loss_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_loss_pct") is None
        assert row.get("solvent_loss_pct") != 0.01


def test_empty_solvent_loss_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_loss_pct": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_loss_pct") is None


def test_planted_loss_still_completes_when_solvent_loss_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(solvent_loss_pct=0.5),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_solvent_loss_does_not_accept_another_loss(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(solvent_loss_pct=0.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_loss_pct": 3.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["solvent_loss_pct"] for row in keys} == {3.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_solvent_loss_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(solvent_loss_pct=3.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_loss_pct": 3.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_solvent_price_does_not_stamp_solvent_loss(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_price_usd_per_kg": 2.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_price_usd_per_kg") == 2.0
        assert row.get("solvent_loss_pct") is None


def test_solvent_loss_does_not_stamp_feedstock_distance(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_loss_pct": 3.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_loss_pct") == 3.0
        assert row.get("feedstock_distance_km") is None


def test_feedstock_distance_does_not_stamp_solvent_loss(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "feedstock_distance_km": 100.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_loss_pct") is None


def test_solvent_loss_without_pct_does_not_stamp(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_loss": 3.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_loss_pct") is None


def test_named_slice_at_wrong_loss_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=0.5,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}
    assert {row["solvent_loss_pct"] for row in keys} == {3.0}


def test_mixed_loss_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(solvent_loss_pct=3.0)
    rows[-1]["solvent_loss_pct"] = 0.5
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_loss_pct": 3.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["solvent_loss_pct"] == 3.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_loss_do_not_fill_named_solvent_loss(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "solvent_loss_pct": 3.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["solvent_loss_pct"] for row in keys} == {3.0}


def test_solvent_loss_pct_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(solvent_loss_pct=3.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["solvent_loss_pct"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_solvent_loss_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"solvent_loss_pct": 3.0},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_loss_pct") is None


def test_invalid_solvent_loss_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_loss_pct": "leaky"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_solvent_loss_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "solvent_loss_pct": "leaky"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_feedstock_distance_is_not_a_silent_zero(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("feedstock_distance_km") is None
        assert row.get("feedstock_distance_km") != 0.0


def test_empty_feedstock_distance_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "feedstock_distance_km": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("feedstock_distance_km") is None


def test_planted_distance_still_completes_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(feedstock_distance_km=100.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_zero_rows_still_complete_when_distance_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(feedstock_distance_km=0.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_feedstock_distance_does_not_accept_another_distance(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(feedstock_distance_km=100.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "feedstock_distance_km": 250.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["feedstock_distance_km"] for row in keys} == {250.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_zero_does_not_accept_another_distance(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(feedstock_distance_km=100.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "feedstock_distance_km": 0.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["feedstock_distance_km"] for row in keys} == {0.0}


def test_named_feedstock_distance_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(feedstock_distance_km=250.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "feedstock_distance_km": 250.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_zero_distance_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "feedstock_distance_km": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["feedstock_distance_km"] for row in keys} == {0.0}


def test_named_zero_completes_on_matching_zero_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(feedstock_distance_km=0.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "feedstock_distance_km": 0.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_solvent_loss_does_not_stamp_distance_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "solvent_loss_pct": 3.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("solvent_loss_pct") == 3.0
        assert row.get("feedstock_distance_km") is None


def test_feedstock_distance_does_not_stamp_dissolution_capacity(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "feedstock_distance_km": 250.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("feedstock_distance_km") == 250.0
        assert row.get("dissolution_capacity") is None


def test_dissolution_capacity_does_not_stamp_feedstock_distance(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_capacity": 5.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("feedstock_distance_km") is None


def test_feedstock_distance_without_km_does_not_stamp(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "feedstock_distance": 250.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("feedstock_distance_km") is None


def test_named_slice_at_wrong_distance_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=100.0,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}
    assert {row["solvent_loss_pct"] for row in keys} == {3.0}
    assert {row["feedstock_distance_km"] for row in keys} == {250.0}


def test_mixed_distance_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(feedstock_distance_km=250.0)
    rows[-1]["feedstock_distance_km"] = 100.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "feedstock_distance_km": 250.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["feedstock_distance_km"] == 250.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_distance_do_not_fill_named_distance(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "feedstock_distance_km": 250.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["feedstock_distance_km"] for row in keys} == {250.0}


def test_feedstock_distance_km_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(feedstock_distance_km=250.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["feedstock_distance_km"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_feedstock_distance_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"feedstock_distance_km": 250.0},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("feedstock_distance_km") is None


def test_invalid_feedstock_distance_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "feedstock_distance_km": "far"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_feedstock_distance_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "feedstock_distance_km": "far"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_dissolution_capacity_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_capacity") is None
        assert row.get("dissolution_capacity") != 3.0


def test_empty_dissolution_capacity_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_capacity": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_capacity") is None


def test_planted_capacity_still_completes_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_capacity=5.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_default_capacity_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_capacity=3.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_dissolution_capacity_does_not_accept_another_capacity(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_capacity=3.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_capacity": 5.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["dissolution_capacity"] for row in keys} == {5.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_capacity_does_not_accept_another_capacity(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_capacity=5.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_capacity": 3.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["dissolution_capacity"] for row in keys} == {3.0}


def test_named_dissolution_capacity_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_capacity=5.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_capacity": 5.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_capacity_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_capacity": 3.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["dissolution_capacity"] for row in keys} == {3.0}


def test_named_default_completes_on_matching_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(dissolution_capacity=3.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_capacity": 3.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_feedstock_distance_does_not_stamp_dissolution_capacity_on_this_slice(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "feedstock_distance_km": 250.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("feedstock_distance_km") == 250.0
        assert row.get("dissolution_capacity") is None


def test_dissolution_capacity_does_not_stamp_labor(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_capacity": 5.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_capacity") == 5.0
        assert row.get("labor_cost_usd_per_employee_yr") is None


def test_labor_does_not_stamp_dissolution_capacity(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "labor_cost_usd_per_employee_yr": 150_000.0,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_capacity") is None


def test_plant_capacity_is_not_dissolution_capacity(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("processing_capacity_mt_per_yr") is not None
        assert row.get("dissolution_capacity") is None


def test_named_slice_at_wrong_dissolution_capacity_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=3.0,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}
    assert {row["solvent_loss_pct"] for row in keys} == {3.0}
    assert {row["feedstock_distance_km"] for row in keys} == {250.0}
    assert {row["dissolution_capacity"] for row in keys} == {5.0}


def test_mixed_capacity_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(dissolution_capacity=5.0)
    rows[-1]["dissolution_capacity"] = 3.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_capacity": 5.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["dissolution_capacity"] == 5.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_capacity_do_not_fill_named_dissolution_capacity(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "dissolution_capacity": 5.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["dissolution_capacity"] for row in keys} == {5.0}


def test_dissolution_capacity_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(dissolution_capacity=5.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["dissolution_capacity"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_dissolution_capacity_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"dissolution_capacity": 5.0},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_capacity") is None


def test_invalid_dissolution_capacity_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_capacity": "wide"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_dissolution_capacity_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "dissolution_capacity": "wide"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_labor_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") is None
        assert row.get("labor_cost_usd_per_employee_yr") != 120_000.0


def test_empty_labor_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "labor_cost_usd_per_employee_yr": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") is None


def test_planted_labor_still_completes_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(labor_cost_usd_per_employee_yr=150_000.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_default_labor_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(labor_cost_usd_per_employee_yr=120_000.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_labor_does_not_accept_another_labor(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(labor_cost_usd_per_employee_yr=120_000.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {150_000.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_labor_does_not_accept_another_labor(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(labor_cost_usd_per_employee_yr=150_000.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "labor_cost_usd_per_employee_yr": 120_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {120_000.0}


def test_named_labor_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(labor_cost_usd_per_employee_yr=150_000.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_labor_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "labor_cost_usd_per_employee_yr": 120_000.0,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {120_000.0}


def test_named_default_labor_completes_on_matching_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(labor_cost_usd_per_employee_yr=120_000.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "labor_cost_usd_per_employee_yr": 120_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_labor_cost_alias_stamps_the_public_name(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_cost": 150_000}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {150_000.0}


def test_public_labor_wins_over_alias(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "labor_cost_usd_per_employee_yr": 150_000.0,
                "labor_cost": 120_000.0,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {150_000.0}


def test_dissolution_capacity_does_not_stamp_labor_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "dissolution_capacity": 5.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("dissolution_capacity") == 5.0
        assert row.get("labor_cost_usd_per_employee_yr") is None


def test_labor_does_not_stamp_labor_burden(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "labor_cost_usd_per_employee_yr": 150_000.0,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") == 150_000.0
        assert row.get("labor_burden") is None


def test_labor_burden_does_not_stamp_labor(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "labor_burden": 0.9},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") is None


def test_sell_leftover_does_not_stamp_labor(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "sell_leftover_plastic": True},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") is None


def test_named_slice_at_wrong_labor_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=120_000.0,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["dissolution_temperature_c"] for row in keys} == {90.0}
    assert {row["precipitation_temperature_c"] for row in keys} == {40.0}
    assert {row["solvent_price_usd_per_kg"] for row in keys} == {2.0}
    assert {row["solvent_loss_pct"] for row in keys} == {3.0}
    assert {row["feedstock_distance_km"] for row in keys} == {250.0}
    assert {row["dissolution_capacity"] for row in keys} == {5.0}
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {150_000.0}


def test_mixed_labor_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(labor_cost_usd_per_employee_yr=150_000.0)
    rows[-1]["labor_cost_usd_per_employee_yr"] = 120_000.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["labor_cost_usd_per_employee_yr"] == 150_000.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_labor_do_not_fill_named_labor(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {150_000.0}


def test_alias_only_row_does_not_fill_named_labor(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows()
    for row in rows:
        row["labor_cost"] = 150_000.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {150_000.0}


def test_labor_cost_usd_per_employee_yr_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(labor_cost_usd_per_employee_yr=150_000.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["labor_cost_usd_per_employee_yr"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_labor_cost_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(labor_cost=150_000.0),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["labor_cost"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_labor_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"labor_cost_usd_per_employee_yr": 150_000.0},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") is None


def test_invalid_labor_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "labor_cost_usd_per_employee_yr": "salary",
            },
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_labor_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={
            **_CAP_20KT,
            "labor_cost_usd_per_employee_yr": "salary",
        },
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_sell_leftover_is_not_a_silent_false(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("sell_leftover_plastic") is None
        assert row.get("sell_leftover_plastic") is not False


def test_empty_sell_leftover_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "sell_leftover_plastic": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("sell_leftover_plastic") is None


def test_false_rows_still_complete_when_sell_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(sell_leftover_plastic=False),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_true_rows_still_complete_when_sell_is_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(sell_leftover_plastic=True),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_true_does_not_accept_false_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(sell_leftover_plastic=False),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "sell_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["sell_leftover_plastic"] for row in keys} == {True}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_false_does_not_accept_true_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(sell_leftover_plastic=True),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "sell_leftover_plastic": False},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["sell_leftover_plastic"] for row in keys} == {False}


def test_named_true_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(sell_leftover_plastic=True),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "sell_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_false_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "sell_leftover_plastic": False},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["sell_leftover_plastic"] for row in keys} == {False}


def test_named_false_completes_on_matching_false_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(sell_leftover_plastic=False),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "sell_leftover_plastic": False},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_true_string_stamps_boolean_true(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "sell_leftover_plastic": "true"},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["sell_leftover_plastic"] for row in keys} == {True}


def test_labor_does_not_stamp_sell_leftover(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "labor_cost_usd_per_employee_yr": 150_000.0,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") == 150_000.0
        assert row.get("sell_leftover_plastic") is None


def test_sell_leftover_does_not_stamp_burn(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "sell_leftover_plastic": True},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("sell_leftover_plastic") is True
        assert row.get("burn_leftover_plastic") is None


def test_burn_leftover_does_not_stamp_sell(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "burn_leftover_plastic": True},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("sell_leftover_plastic") is None


def test_named_slice_at_wrong_sell_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=False,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C1"}
    assert {row["labor_cost_usd_per_employee_yr"] for row in keys} == {150_000.0}
    assert {row["sell_leftover_plastic"] for row in keys} == {True}


def test_mixed_sell_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(sell_leftover_plastic=True)
    rows[-1]["sell_leftover_plastic"] = False
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "sell_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["sell_leftover_plastic"] is True
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_sell_do_not_fill_named_true(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "sell_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["sell_leftover_plastic"] for row in keys} == {True}


def test_sell_leftover_plastic_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(sell_leftover_plastic=True),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["sell_leftover_plastic"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_sell_leftover_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"sell_leftover_plastic": True},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("sell_leftover_plastic") is None


def test_invalid_sell_leftover_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "sell_leftover_plastic": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_sell_leftover_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "sell_leftover_plastic": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_burn_leftover_is_not_a_silent_false(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("burn_leftover_plastic") is None
        assert row.get("burn_leftover_plastic") is not False


def test_empty_burn_leftover_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "burn_leftover_plastic": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("burn_leftover_plastic") is None


def test_false_burn_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(burn_leftover_plastic=False),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_true_burn_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(burn_leftover_plastic=True),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_burn_true_does_not_accept_false_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(burn_leftover_plastic=False),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "burn_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["burn_leftover_plastic"] for row in keys} == {True}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_burn_false_does_not_accept_true_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(burn_leftover_plastic=True),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "burn_leftover_plastic": False},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["burn_leftover_plastic"] for row in keys} == {False}


def test_named_burn_true_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(burn_leftover_plastic=True),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "burn_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_burn_false_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "burn_leftover_plastic": False},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["burn_leftover_plastic"] for row in keys} == {False}


def test_named_burn_false_completes_on_matching_false_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(burn_leftover_plastic=False),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "burn_leftover_plastic": False},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_sell_leftover_does_not_stamp_burn_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "sell_leftover_plastic": True},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("sell_leftover_plastic") is True
        assert row.get("burn_leftover_plastic") is None


def test_burn_leftover_does_not_stamp_precipitation_format(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "burn_leftover_plastic": True},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("burn_leftover_plastic") is True
        assert row.get("precipitation_temperature_format") is None


def test_precipitation_format_does_not_stamp_burn(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_temperature_format": "constant",
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("burn_leftover_plastic") is None


def test_named_sell_and_burn_true_does_not_fire_disposition_conflict(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "sell_leftover_plastic": True,
                "burn_leftover_plastic": True,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "leftover_disposition_conflict"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["sell_leftover_plastic"] for row in keys} == {True}
    assert {row["burn_leftover_plastic"] for row in keys} == {True}


def test_named_burn_true_on_c2_does_not_fire_burn_requires_facilities(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "energy_case": "C2",
                "burn_leftover_plastic": True,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "burn_requires_facilities"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["energy_case"] for row in keys} == {"C2"}
    assert {row["burn_leftover_plastic"] for row in keys} == {True}


def test_named_slice_at_wrong_burn_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=False,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["sell_leftover_plastic"] for row in keys} == {True}
    assert {row["burn_leftover_plastic"] for row in keys} == {True}


def test_mixed_burn_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(burn_leftover_plastic=True)
    rows[-1]["burn_leftover_plastic"] = False
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "burn_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["burn_leftover_plastic"] is True
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_burn_do_not_fill_named_true(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "burn_leftover_plastic": True},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["burn_leftover_plastic"] for row in keys} == {True}


def test_burn_leftover_plastic_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(burn_leftover_plastic=True),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["burn_leftover_plastic"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_burn_leftover_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"burn_leftover_plastic": True},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("burn_leftover_plastic") is None


def test_invalid_burn_leftover_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "burn_leftover_plastic": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_integer_one_is_not_a_burn_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "burn_leftover_plastic": 1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_burn_leftover_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "burn_leftover_plastic": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_precipitation_format_is_not_a_silent_constant(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_format") is None
        assert row.get("precipitation_temperature_format") != "constant"


def test_empty_precipitation_format_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_temperature_format": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_format") is None


def test_constant_format_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(precipitation_temperature_format="constant"),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_drop_format_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(precipitation_temperature_format="drop"),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_drop_does_not_accept_constant_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(precipitation_temperature_format="constant"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "precipitation_temperature_format": "drop",
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_format"] for row in keys} == {"drop"}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_constant_does_not_accept_drop_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(precipitation_temperature_format="drop"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "precipitation_temperature_format": "constant",
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_format"] for row in keys} == {
        "constant",
    }


def test_named_drop_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(precipitation_temperature_format="drop"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "precipitation_temperature_format": "drop",
                },
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_constant_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_temperature_format": "constant",
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_format"] for row in keys} == {
        "constant",
    }


def test_named_constant_completes_on_matching_constant_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(precipitation_temperature_format="constant"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "precipitation_temperature_format": "constant",
                },
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_burn_leftover_does_not_stamp_format_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "burn_leftover_plastic": True},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("burn_leftover_plastic") is True
        assert row.get("precipitation_temperature_format") is None


def test_precipitation_format_does_not_stamp_configuration(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_temperature_format": "drop",
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_format") == "drop"
        assert row.get("precipitation_configuration") is None
        assert row.get("precipitation_temperature_drop_pct") is None


def test_precipitation_configuration_does_not_stamp_format(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_configuration": "solvent mixing",
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_format") is None


def test_named_drop_does_not_fire_field_not_on_this_instance(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_temperature_format": "drop",
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "field_not_on_this_instance"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_format"] for row in keys} == {"drop"}


def test_named_slice_at_wrong_format_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="constant",
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["burn_leftover_plastic"] for row in keys} == {True}
    assert {row["precipitation_temperature_format"] for row in keys} == {"drop"}


def test_mixed_format_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(precipitation_temperature_format="drop")
    rows[-1]["precipitation_temperature_format"] = "constant"
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "precipitation_temperature_format": "drop",
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["precipitation_temperature_format"] == "drop"
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_format_do_not_fill_named_drop(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={
                    **_CAP_20KT,
                    "precipitation_temperature_format": "drop",
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_format"] for row in keys} == {"drop"}


def test_precipitation_temperature_format_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(precipitation_temperature_format="drop"),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["precipitation_temperature_format"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_precipitation_format_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"precipitation_temperature_format": "drop"},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_format") is None


def test_invalid_precipitation_format_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_temperature_format": "linear",
            },
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "field_not_on_this_instance"


def test_integer_one_is_not_a_precipitation_format_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_temperature_format": 1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_precipitation_format_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={
            **_CAP_20KT,
            "precipitation_temperature_format": "linear",
        },
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


_IHT = "integrated heat transfer"
_MIX = "solvent mixing"


def test_omitted_precipitation_configuration_is_not_a_silent_integrated(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") is None
        assert row.get("precipitation_configuration") != _IHT


def test_empty_precipitation_configuration_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") is None


def test_integrated_config_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_configuration=_IHT),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_mixing_config_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(precipitation_configuration=_MIX),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_mixing_does_not_accept_integrated_rows(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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


def test_precipitation_format_does_not_stamp_configuration_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_temperature_format": "drop",
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_temperature_format") == "drop"
        assert row.get("precipitation_configuration") is None


def test_precipitation_configuration_does_not_stamp_irr(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") == _MIX
        assert row.get("irr") is None


def test_irr_does_not_stamp_precipitation_configuration(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "irr": 0.12},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") is None


def test_named_slice_at_wrong_configuration_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_IHT,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_temperature_format"] for row in keys} == {"drop"}
    assert {row["precipitation_configuration"] for row in keys} == {_MIX}


def test_mixed_configuration_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(precipitation_configuration=_MIX),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["precipitation_configuration"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_precipitation_configuration_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
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


def test_invalid_precipitation_configuration_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": "flash"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_uppercase_mixing_is_not_a_configuration_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "precipitation_configuration": "SOLVENT MIXING",
            },
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_integer_one_is_not_a_precipitation_configuration_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": 1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_precipitation_configuration_does_not_outrank_missing_map(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "precipitation_configuration": "flash"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_irr_is_not_a_silent_tenth(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("irr") is None
        assert row.get("irr") != 0.10


def test_empty_irr_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "irr": ""}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("irr") is None


def test_default_irr_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(irr=0.10))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_irr_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(irr=0.12))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_irr_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(irr=0.10))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "irr": 0.12},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["irr"] for row in keys} == {0.12}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_irr_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(irr=0.12))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "irr": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["irr"] for row in keys} == {0.10}


def test_named_irr_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(irr=0.12))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "irr": 0.12},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_irr_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "irr": 0.10}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["irr"] for row in keys} == {0.10}


def test_named_default_irr_completes_on_matching_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(irr=0.10))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "irr": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_precipitation_configuration_does_not_stamp_irr_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "precipitation_configuration": _MIX},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("precipitation_configuration") == _MIX
        assert row.get("irr") is None


def test_irr_does_not_stamp_income_tax(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "irr": 0.12}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("irr") == 0.12
        assert row.get("income_tax") is None


def test_income_tax_does_not_stamp_irr(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": 0.25}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("irr") is None


def test_named_slice_at_wrong_irr_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.10,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["precipitation_configuration"] for row in keys} == {_MIX}
    assert {row["irr"] for row in keys} == {0.12}


def test_mixed_irr_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(irr=0.12)
    rows[-1]["irr"] = 0.10
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "irr": 0.12},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["irr"] == 0.12
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_irr_do_not_fill_named_irr(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "irr": 0.12},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["irr"] for row in keys} == {0.12}


def test_irr_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs(irr=0.12)))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["irr"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_irr_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"irr": 0.12}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("irr") is None


def test_invalid_irr_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "irr": "maybe"}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_irr_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "irr": 10}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_zero_irr_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "irr": 0}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_irr_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "irr": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_income_tax_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("income_tax") is None
        assert row.get("income_tax") != 0.21


def test_empty_income_tax_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": ""}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("income_tax") is None


def test_default_income_tax_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(income_tax=0.21))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_income_tax_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(income_tax=0.25))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_income_tax_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(income_tax=0.21))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "income_tax": 0.25},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["income_tax"] for row in keys} == {0.25}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_income_tax_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(income_tax=0.25))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "income_tax": 0.21},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["income_tax"] for row in keys} == {0.21}


def test_named_income_tax_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(income_tax=0.25))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "income_tax": 0.25},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_income_tax_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": 0.21}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["income_tax"] for row in keys} == {0.21}


def test_named_zero_income_tax_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": 0}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["income_tax"] for row in keys} == {0.0}


def test_named_default_income_tax_completes_on_matching_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(income_tax=0.21))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "income_tax": 0.21},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_irr_does_not_stamp_income_tax_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "irr": 0.12}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("irr") == 0.12
        assert row.get("income_tax") is None


def test_income_tax_does_not_stamp_operating_days(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": 0.25}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("income_tax") == 0.25
        assert row.get("operating_days") is None
        assert row.get("lang_factor") is None


def test_operating_days_does_not_stamp_income_tax(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": 365}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("income_tax") is None


def test_named_slice_at_wrong_income_tax_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.21,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["irr"] for row in keys} == {0.12}
    assert {row["income_tax"] for row in keys} == {0.25}


def test_mixed_income_tax_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(income_tax=0.25)
    rows[-1]["income_tax"] = 0.21
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "income_tax": 0.25},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["income_tax"] == 0.25
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_income_tax_do_not_fill_named_tax(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "income_tax": 0.25},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["income_tax"] for row in keys} == {0.25}


def test_income_tax_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs(income_tax=0.25)))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["income_tax"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_income_tax_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"income_tax": 0.25}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("income_tax") is None


def test_invalid_income_tax_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": "maybe"}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_income_tax_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": 21}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_income_tax_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": -0.01}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_income_tax_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "income_tax": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_operating_days_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("operating_days") is None
        assert row.get("operating_days") != 350.4


def test_empty_operating_days_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": ""}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("operating_days") is None


def test_default_operating_days_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(operating_days=350.4))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_operating_days_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(operating_days=365.0))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_operating_days_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(operating_days=350.4))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "operating_days": 365},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["operating_days"] for row in keys} == {365.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_operating_days_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(operating_days=365.0))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "operating_days": 350.4},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["operating_days"] for row in keys} == {350.4}


def test_named_operating_days_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(operating_days=365.0))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "operating_days": 365},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_operating_days_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": 350.4}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["operating_days"] for row in keys} == {350.4}


def test_named_one_operating_day_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": 1}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["operating_days"] for row in keys} == {1.0}


def test_named_default_operating_days_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(operating_days=350.4))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "operating_days": 350.4},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_income_tax_does_not_stamp_operating_days_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "income_tax": 0.25}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("income_tax") == 0.25
        assert row.get("operating_days") is None


def test_operating_days_does_not_stamp_labor_burden(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": 365}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("operating_days") == 365.0
        assert row.get("labor_burden") is None
        assert row.get("lang_factor") is None


def test_labor_burden_does_not_stamp_operating_days(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": 1.5}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("operating_days") is None


def test_named_slice_at_wrong_operating_days_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=350.4,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["income_tax"] for row in keys} == {0.25}
    assert {row["operating_days"] for row in keys} == {365.0}


def test_mixed_operating_days_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(operating_days=365.0)
    rows[-1]["operating_days"] = 350.4
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "operating_days": 365},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["operating_days"] == 365.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_operating_days_do_not_fill_named_days(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "operating_days": 365},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["operating_days"] for row in keys} == {365.0}


def test_operating_days_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs(operating_days=365)))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["operating_days"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_operating_days_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"operating_days": 365}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("operating_days") is None


def test_invalid_operating_days_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "operating_days": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_zero_operating_days_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": 0}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_operating_days_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": -1}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_operating_days_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "operating_days": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_labor_burden_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_burden") is None
        assert row.get("labor_burden") != 0.90


def test_empty_labor_burden_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": ""}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_burden") is None


def test_default_labor_burden_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(labor_burden=0.90))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_above_one_labor_burden_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(labor_burden=1.5))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_labor_burden_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(labor_burden=0.90))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "labor_burden": 1.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_burden"] for row in keys} == {1.5}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_labor_burden_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(labor_burden=1.5))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "labor_burden": 0.90},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_burden"] for row in keys} == {0.90}


def test_named_labor_burden_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(labor_burden=1.5))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "labor_burden": 1.5},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_labor_burden_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": 0.90}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_burden"] for row in keys} == {0.90}


def test_named_zero_labor_burden_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": 0}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_burden"] for row in keys} == {0.0}


def test_named_above_one_labor_burden_is_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": 1.5}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_admitted_record_query"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_burden"] for row in keys} == {1.5}


def test_named_default_labor_burden_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(labor_burden=0.90))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "labor_burden": 0.90},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_operating_days_does_not_stamp_labor_burden_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "operating_days": 365}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("operating_days") == 365.0
        assert row.get("labor_burden") is None


def test_labor_burden_does_not_stamp_finance_interest(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": 1.5}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_burden") == 1.5
        assert row.get("finance_interest") is None
        assert row.get("lang_factor") is None


def test_finance_interest_does_not_stamp_labor_burden(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_interest": 0.12}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_burden") is None


def test_labor_cost_does_not_stamp_labor_burden(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "labor_cost_usd_per_employee_yr": 150_000.0,
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_cost_usd_per_employee_yr") == 150_000.0
        assert row.get("labor_burden") is None


def test_named_slice_at_wrong_labor_burden_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=0.90,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["operating_days"] for row in keys} == {365.0}
    assert {row["labor_burden"] for row in keys} == {1.5}


def test_mixed_labor_burden_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(labor_burden=1.5)
    rows[-1]["labor_burden"] = 0.90
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "labor_burden": 1.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["labor_burden"] == 1.5
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_labor_burden_do_not_fill_named_burden(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "labor_burden": 1.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_burden"] for row in keys} == {1.5}


def test_labor_burden_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs(labor_burden=1.5)))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["labor_burden"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_labor_burden_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"labor_burden": 1.5}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_burden") is None


def test_invalid_labor_burden_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": "maybe"}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_negative_labor_burden_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": -0.1}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_invalid_labor_burden_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "labor_burden": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_finance_interest_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_interest") is None
        assert row.get("finance_interest") != 0.08


def test_empty_finance_interest_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_interest": ""}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_interest") is None


def test_default_finance_interest_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_interest=0.08),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_finance_interest_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_interest=0.12),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_finance_interest_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_interest=0.08),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_interest": 0.12},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_interest"] for row in keys} == {0.12}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_finance_interest_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_interest=0.12),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_interest": 0.08},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_interest"] for row in keys} == {0.08}


def test_named_finance_interest_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_interest=0.12),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_interest": 0.12},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_finance_interest_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_interest": 0.08},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_interest"] for row in keys} == {0.08}


def test_named_zero_finance_interest_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_interest": 0}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_interest"] for row in keys} == {0.0}


def test_named_default_finance_interest_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_interest=0.08),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_interest": 0.08},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_labor_burden_does_not_stamp_finance_interest_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "labor_burden": 1.5}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("labor_burden") == 1.5
        assert row.get("finance_interest") is None


def test_finance_interest_does_not_stamp_finance_years_or_irr(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_interest": 0.12},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_interest") == 0.12
        assert row.get("finance_years") is None
        assert row.get("irr") is None
        assert row.get("lang_factor") is None


def test_finance_years_does_not_stamp_finance_interest(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": 15}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_interest") is None


def test_named_slice_at_wrong_finance_interest_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.08,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["labor_burden"] for row in keys} == {1.5}
    assert {row["finance_interest"] for row in keys} == {0.12}


def test_mixed_finance_interest_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(finance_interest=0.12)
    rows[-1]["finance_interest"] = 0.08
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_interest": 0.12},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["finance_interest"] == 0.12
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_finance_interest_do_not_fill_named_rate(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_interest": 0.12},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_interest"] for row in keys} == {0.12}


def test_finance_interest_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(finance_interest=0.12),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["finance_interest"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_finance_interest_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"finance_interest": 0.12}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_interest") is None


def test_invalid_finance_interest_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_interest": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_finance_interest_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_interest": 8}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_finance_interest_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_interest": -0.01},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_finance_interest_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "finance_interest": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_finance_years_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_years") is None
        assert row.get("finance_years") != 10


def test_empty_finance_years_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": ""}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_years") is None


def test_default_finance_years_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(finance_years=10))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_finance_years_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(finance_years=15))
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_finance_years_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(finance_years=10))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_years": 15},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_years"] for row in keys} == {15.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_finance_years_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(finance_years=15))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_years": 10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_years"] for row in keys} == {10.0}


def test_named_finance_years_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(finance_years=15))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_years": 15},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_finance_years_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": 10}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_years"] for row in keys} == {10.0}


def test_named_one_finance_year_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": 1}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_years"] for row in keys} == {1.0}


def test_named_default_finance_years_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows(finance_years=10))
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_years": 10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_finance_interest_does_not_stamp_finance_years_on_this_slice(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_interest": 0.12},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_interest") == 0.12
        assert row.get("finance_years") is None


def test_finance_years_does_not_stamp_finance_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": 15}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_years") == 15.0
        assert row.get("finance_fraction") is None
        assert row.get("lang_factor") is None


def test_finance_fraction_does_not_stamp_finance_years(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_fraction": 0.4}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_years") is None


def test_named_slice_at_wrong_finance_years_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=10,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_interest"] for row in keys} == {0.12}
    assert {row["finance_years"] for row in keys} == {15.0}


def test_mixed_finance_years_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(finance_years=15)
    rows[-1]["finance_years"] = 10
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_years": 15},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["finance_years"] == 15.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_finance_years_do_not_fill_named_years(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_years": 15},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_years"] for row in keys} == {15.0}


def test_finance_years_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs(finance_years=15)))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["finance_years"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_finance_years_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"finance_years": 15}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_years") is None


def test_invalid_finance_years_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": "maybe"}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_zero_finance_years_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": 0}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_fractional_finance_years_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": 10.5}),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_finance_years_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "finance_years": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_finance_fraction_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_fraction") is None
        assert row.get("finance_fraction") != 0.0


def test_empty_finance_fraction_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_fraction") is None


def test_default_finance_fraction_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_fraction=0.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_finance_fraction_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_fraction=0.4),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_finance_fraction_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_fraction=0.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_fraction": 0.4},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_fraction"] for row in keys} == {0.4}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_finance_fraction_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_fraction=0.4),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_fraction": 0.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_fraction"] for row in keys} == {0.0}


def test_named_finance_fraction_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_fraction=0.4),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_fraction": 0.4},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_finance_fraction_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_fraction"] for row in keys} == {0.0}


def test_named_unity_finance_fraction_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_fraction"] for row in keys} == {1.0}


def test_named_default_finance_fraction_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(finance_fraction=0.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_fraction": 0.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_finance_years_does_not_stamp_finance_fraction_on_this_slice(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "finance_years": 15}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_years") == 15.0
        assert row.get("finance_fraction") is None


def test_finance_fraction_does_not_stamp_startup_months(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": 0.4},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_fraction") == 0.4
        assert row.get("startup_months") is None
        assert row.get("lang_factor") is None
        assert row.get("finance_years") is None


def test_named_slice_at_wrong_finance_fraction_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.0,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_years"] for row in keys} == {15.0}
    assert {row["finance_fraction"] for row in keys} == {0.4}


def test_mixed_finance_fraction_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(finance_fraction=0.4)
    rows[-1]["finance_fraction"] = 0.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_fraction": 0.4},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["finance_fraction"] == 0.4
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_finance_fraction_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "finance_fraction": 0.4},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_fraction"] for row in keys} == {0.4}


def test_finance_fraction_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(finance_fraction=0.4),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["finance_fraction"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_finance_fraction_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"finance_fraction": 0.4}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_fraction") is None


def test_invalid_finance_fraction_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_finance_fraction_is_not_a_remnant_listing(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": 40},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_finance_fraction_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_finance_fraction_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "finance_fraction": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_startup_months_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_months") is None
        assert row.get("startup_months") != 3


def test_empty_startup_months_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_months") is None


def test_default_startup_months_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_months=3),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_months_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_months=6),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_months_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_months=3),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_months": 6},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_months"] for row in keys} == {6.0}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_startup_months_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_months=6),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_months": 3},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_months"] for row in keys} == {3.0}


def test_named_startup_months_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_months=6),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_months": 6},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_startup_months_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": 3},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_months"] for row in keys} == {3.0}


def test_named_zero_startup_months_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": 0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_months"] for row in keys} == {0.0}


def test_named_twelve_startup_months_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": 12},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_months"] for row in keys} == {12.0}


def test_named_default_startup_months_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_months=3),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_months": 3},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_finance_fraction_does_not_stamp_startup_months_on_this_slice(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "finance_fraction": 0.4},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("finance_fraction") == 0.4
        assert row.get("startup_months") is None


def test_startup_months_does_not_stamp_startup_FOCfrac(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": 6},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_months") == 6.0
        assert row.get("startup_FOCfrac") is None
        assert row.get("lang_factor") is None
        assert row.get("finance_fraction") is None


def test_named_slice_at_wrong_startup_months_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=3,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["finance_fraction"] for row in keys} == {0.4}
    assert {row["startup_months"] for row in keys} == {6.0}


def test_mixed_startup_months_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(startup_months=6)
    rows[-1]["startup_months"] = 3
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_months": 6},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["startup_months"] == 6.0
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_startup_months_do_not_fill_named_months(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_months": 6},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_months"] for row in keys} == {6.0}


def test_startup_months_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(startup_months=6),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["startup_months"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_startup_months_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"startup_months": 6}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_months") is None


def test_invalid_startup_months_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_negative_startup_months_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": -1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_startup_months_above_one_year_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_months": 13},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_startup_months_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "startup_months": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_startup_FOCfrac_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_FOCfrac") is None
        assert row.get("startup_FOCfrac") != 1


def test_empty_startup_FOCfrac_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_FOCfrac") is None


def test_default_startup_FOCfrac_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_FOCfrac=1.0),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_FOCfrac_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_FOCfrac=0.5),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_FOCfrac_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_FOCfrac=1.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_FOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_FOCfrac"] for row in keys} == {0.5}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_startup_FOCfrac_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_FOCfrac=0.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_FOCfrac": 1.0},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_FOCfrac"] for row in keys} == {1.0}


def test_named_startup_FOCfrac_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_FOCfrac=0.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_FOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_startup_FOCfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_FOCfrac"] for row in keys} == {1.0}


def test_named_zero_startup_FOCfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_FOCfrac"] for row in keys} == {0.0}


def test_named_default_startup_FOCfrac_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_FOCfrac=1.0),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_FOCfrac": 1.0},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_startup_months_does_not_stamp_startup_FOCfrac_on_this_slice(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(process_config={**_CAP_20KT, "startup_months": 6}),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_months") == 6.0
        assert row.get("startup_FOCfrac") is None


def test_startup_FOCfrac_does_not_stamp_startup_VOCfrac(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": 0.5},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_FOCfrac") == 0.5
        assert row.get("startup_VOCfrac") is None
        assert row.get("lang_factor") is None
        assert row.get("startup_months") is None


def test_named_slice_at_wrong_startup_FOCfrac_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=1.0,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_months"] for row in keys} == {6.0}
    assert {row["startup_FOCfrac"] for row in keys} == {0.5}


def test_mixed_startup_FOCfrac_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(startup_FOCfrac=0.5)
    rows[-1]["startup_FOCfrac"] = 1.0
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_FOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["startup_FOCfrac"] == 0.5
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_startup_FOCfrac_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_FOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_FOCfrac"] for row in keys} == {0.5}


def test_startup_FOCfrac_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(startup_FOCfrac=0.5),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["startup_FOCfrac"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_startup_FOCfrac_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"startup_FOCfrac": 0.5}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_FOCfrac") is None


def test_invalid_startup_FOCfrac_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_startup_FOCfrac_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": 50},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_startup_FOCfrac_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_startup_FOCfrac_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "startup_FOCfrac": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_startup_VOCfrac_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_VOCfrac") is None
        assert row.get("startup_VOCfrac") != 0.75


def test_empty_startup_VOCfrac_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_VOCfrac") is None


def test_default_startup_VOCfrac_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_VOCfrac=0.75),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_VOCfrac_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_VOCfrac=0.5),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_VOCfrac_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_VOCfrac=0.75),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_VOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_VOCfrac"] for row in keys} == {0.5}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_startup_VOCfrac_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_VOCfrac=0.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_VOCfrac": 0.75},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_VOCfrac"] for row in keys} == {0.75}


def test_named_startup_VOCfrac_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_VOCfrac=0.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_VOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_startup_VOCfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": 0.75},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_VOCfrac"] for row in keys} == {0.75}


def test_named_zero_startup_VOCfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_VOCfrac"] for row in keys} == {0.0}


def test_named_unity_startup_VOCfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_VOCfrac"] for row in keys} == {1.0}


def test_named_default_startup_VOCfrac_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_VOCfrac=0.75),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_VOCfrac": 0.75},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_startup_FOCfrac_does_not_stamp_startup_VOCfrac_on_this_slice(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_FOCfrac": 0.5},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_FOCfrac") == 0.5
        assert row.get("startup_VOCfrac") is None


def test_startup_VOCfrac_does_not_stamp_startup_salesfrac(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": 0.5},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_VOCfrac") == 0.5
        assert row.get("startup_salesfrac") is None
        assert row.get("lang_factor") is None
        assert row.get("startup_FOCfrac") is None


def test_named_slice_at_wrong_startup_VOCfrac_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.75,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_FOCfrac"] for row in keys} == {0.5}
    assert {row["startup_VOCfrac"] for row in keys} == {0.5}


def test_mixed_startup_VOCfrac_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(startup_VOCfrac=0.5)
    rows[-1]["startup_VOCfrac"] = 0.75
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_VOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["startup_VOCfrac"] == 0.5
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_startup_VOCfrac_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_VOCfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_VOCfrac"] for row in keys} == {0.5}


def test_startup_VOCfrac_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(startup_VOCfrac=0.5),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["startup_VOCfrac"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_startup_VOCfrac_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"startup_VOCfrac": 0.5}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_VOCfrac") is None


def test_invalid_startup_VOCfrac_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_startup_VOCfrac_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": 75},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_startup_VOCfrac_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_startup_VOCfrac_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "startup_VOCfrac": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_startup_salesfrac_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_salesfrac") is None
        assert row.get("startup_salesfrac") != 0.5


def test_empty_startup_salesfrac_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_salesfrac") is None


def test_default_startup_salesfrac_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_salesfrac=0.5),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_salesfrac_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_salesfrac=0.25),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_startup_salesfrac_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_salesfrac=0.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_salesfrac": 0.25},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_salesfrac"] for row in keys} == {0.25}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_startup_salesfrac_does_not_accept_other_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_salesfrac=0.25),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_salesfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_salesfrac"] for row in keys} == {0.5}


def test_named_startup_salesfrac_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_salesfrac=0.25),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_salesfrac": 0.25},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_startup_salesfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": 0.5},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_salesfrac"] for row in keys} == {0.5}


def test_named_zero_startup_salesfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_salesfrac"] for row in keys} == {0.0}


def test_named_unity_startup_salesfrac_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_salesfrac"] for row in keys} == {1.0}


def test_named_default_startup_salesfrac_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(startup_salesfrac=0.5),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_salesfrac": 0.5},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_startup_VOCfrac_does_not_stamp_startup_salesfrac_on_this_slice(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_VOCfrac": 0.5},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_VOCfrac") == 0.5
        assert row.get("startup_salesfrac") is None


def test_startup_salesfrac_does_not_stamp_WC_over_FCI(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": 0.25},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_salesfrac") == 0.25
        assert row.get("WC_over_FCI") is None
        assert row.get("lang_factor") is None
        assert row.get("startup_VOCfrac") is None


def test_named_slice_at_wrong_startup_salesfrac_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.5,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_VOCfrac"] for row in keys} == {0.5}
    assert {row["startup_salesfrac"] for row in keys} == {0.25}


def test_mixed_startup_salesfrac_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(startup_salesfrac=0.25)
    rows[-1]["startup_salesfrac"] = 0.5
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_salesfrac": 0.25},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["startup_salesfrac"] == 0.25
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_startup_salesfrac_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "startup_salesfrac": 0.25},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_salesfrac"] for row in keys} == {0.25}


def test_startup_salesfrac_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(startup_salesfrac=0.25),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["startup_salesfrac"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_startup_salesfrac_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"startup_salesfrac": 0.25}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("startup_salesfrac") is None


def test_invalid_startup_salesfrac_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_startup_salesfrac_is_not_a_remnant_listing(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": 50},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_startup_salesfrac_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "startup_salesfrac": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_startup_salesfrac_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "startup_salesfrac": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_WC_over_FCI_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("WC_over_FCI") is None
        assert row.get("WC_over_FCI") != 0.05


def test_empty_WC_over_FCI_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("WC_over_FCI") is None


def test_default_WC_over_FCI_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(WC_over_FCI=0.05),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_WC_over_FCI_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(WC_over_FCI=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_WC_over_FCI_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(WC_over_FCI=0.05),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "WC_over_FCI": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["WC_over_FCI"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_WC_over_FCI_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(WC_over_FCI=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "WC_over_FCI": 0.05},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["WC_over_FCI"] for row in keys} == {0.05}


def test_named_WC_over_FCI_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(WC_over_FCI=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "WC_over_FCI": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_WC_over_FCI_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": 0.05},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["WC_over_FCI"] for row in keys} == {0.05}


def test_named_zero_WC_over_FCI_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["WC_over_FCI"] for row in keys} == {0.0}


def test_named_unity_WC_over_FCI_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["WC_over_FCI"] for row in keys} == {1.0}


def test_named_default_WC_over_FCI_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(WC_over_FCI=0.05),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "WC_over_FCI": 0.05},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_WC_over_FCI_does_not_stamp_warehouse(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("WC_over_FCI") == 0.10
        assert row.get("warehouse") is None
        assert row.get("lang_factor") is None
        assert row.get("startup_salesfrac") is None


def test_named_slice_at_wrong_WC_over_FCI_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.05,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["startup_salesfrac"] for row in keys} == {0.25}
    assert {row["WC_over_FCI"] for row in keys} == {0.10}


def test_mixed_WC_over_FCI_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(WC_over_FCI=0.10)
    rows[-1]["WC_over_FCI"] = 0.05
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "WC_over_FCI": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["WC_over_FCI"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_WC_over_FCI_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "WC_over_FCI": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["WC_over_FCI"] for row in keys} == {0.10}


def test_WC_over_FCI_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(WC_over_FCI=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["WC_over_FCI"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_WC_over_FCI_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"WC_over_FCI": 0.10}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("WC_over_FCI") is None


def test_invalid_WC_over_FCI_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_WC_over_FCI_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": 5},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_WC_over_FCI_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "WC_over_FCI": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_WC_over_FCI_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "WC_over_FCI": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_warehouse_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("warehouse") is None
        assert row.get("warehouse") != 0.04


def test_empty_warehouse_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("warehouse") is None


def test_default_warehouse_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(warehouse=0.04),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_warehouse_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(warehouse=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_warehouse_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(warehouse=0.04),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "warehouse": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["warehouse"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_warehouse_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(warehouse=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "warehouse": 0.04},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["warehouse"] for row in keys} == {0.04}


def test_named_warehouse_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(warehouse=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "warehouse": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_warehouse_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": 0.04},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["warehouse"] for row in keys} == {0.04}


def test_named_zero_warehouse_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["warehouse"] for row in keys} == {0.0}


def test_named_unity_warehouse_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["warehouse"] for row in keys} == {1.0}


def test_named_default_warehouse_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(warehouse=0.04),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "warehouse": 0.04},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_warehouse_does_not_stamp_site_development(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("warehouse") == 0.10
        assert row.get("site_development") is None
        assert row.get("lang_factor") is None
        assert row.get("WC_over_FCI") is None


def test_named_slice_at_wrong_warehouse_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.04,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["WC_over_FCI"] for row in keys} == {0.10}
    assert {row["warehouse"] for row in keys} == {0.10}


def test_mixed_warehouse_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(warehouse=0.10)
    rows[-1]["warehouse"] = 0.04
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "warehouse": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["warehouse"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_warehouse_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "warehouse": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["warehouse"] for row in keys} == {0.10}


def test_warehouse_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(warehouse=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["warehouse"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_warehouse_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"warehouse": 0.10}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("warehouse") is None


def test_invalid_warehouse_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_warehouse_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": 4},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_warehouse_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "warehouse": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_warehouse_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "warehouse": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_site_development_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("site_development") is None
        assert row.get("site_development") != 0.09


def test_empty_site_development_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("site_development") is None


def test_default_site_development_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(site_development=0.09),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_site_development_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(site_development=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_site_development_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(site_development=0.09),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "site_development": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["site_development"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_site_development_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(site_development=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "site_development": 0.09},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["site_development"] for row in keys} == {0.09}


def test_named_site_development_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(site_development=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "site_development": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_site_development_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": 0.09},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["site_development"] for row in keys} == {0.09}


def test_named_zero_site_development_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["site_development"] for row in keys} == {0.0}


def test_named_unity_site_development_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["site_development"] for row in keys} == {1.0}


def test_named_default_site_development_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(site_development=0.09),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "site_development": 0.09},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_site_development_does_not_stamp_additional_piping(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("site_development") == 0.10
        assert row.get("additional_piping") is None
        assert row.get("lang_factor") is None
        assert row.get("warehouse") is None


def test_named_slice_at_wrong_site_development_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.09,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["warehouse"] for row in keys} == {0.10}
    assert {row["site_development"] for row in keys} == {0.10}


def test_mixed_site_development_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(site_development=0.10)
    rows[-1]["site_development"] = 0.09
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "site_development": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["site_development"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_site_development_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "site_development": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["site_development"] for row in keys} == {0.10}


def test_site_development_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(site_development=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["site_development"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_site_development_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"site_development": 0.10}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("site_development") is None


def test_invalid_site_development_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_site_development_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": 9},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_site_development_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "site_development": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_site_development_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "site_development": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_additional_piping_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("additional_piping") is None
        assert row.get("additional_piping") != 0.045


def test_empty_additional_piping_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("additional_piping") is None


def test_default_additional_piping_rows_still_complete_when_omitted(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(additional_piping=0.045),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_additional_piping_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(additional_piping=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_additional_piping_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(additional_piping=0.045),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "additional_piping": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["additional_piping"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_additional_piping_does_not_accept_other_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(additional_piping=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "additional_piping": 0.045},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["additional_piping"] for row in keys} == {0.045}


def test_named_additional_piping_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(additional_piping=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "additional_piping": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_additional_piping_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": 0.045},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["additional_piping"] for row in keys} == {0.045}


def test_named_zero_additional_piping_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["additional_piping"] for row in keys} == {0.0}


def test_named_unity_additional_piping_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["additional_piping"] for row in keys} == {1.0}


def test_named_default_additional_piping_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(additional_piping=0.045),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "additional_piping": 0.045},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_additional_piping_does_not_stamp_proratable_costs(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("additional_piping") == 0.10
        assert row.get("proratable_costs") is None
        assert row.get("lang_factor") is None
        assert row.get("site_development") is None


def test_named_slice_at_wrong_additional_piping_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.045,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["site_development"] for row in keys} == {0.10}
    assert {row["additional_piping"] for row in keys} == {0.10}


def test_mixed_additional_piping_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(additional_piping=0.10)
    rows[-1]["additional_piping"] = 0.045
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "additional_piping": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["additional_piping"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_additional_piping_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "additional_piping": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["additional_piping"] for row in keys} == {0.10}


def test_additional_piping_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(additional_piping=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["additional_piping"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_additional_piping_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"additional_piping": 0.10}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("additional_piping") is None


def test_invalid_additional_piping_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_additional_piping_is_not_a_remnant_listing(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": 5},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_additional_piping_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "additional_piping": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_additional_piping_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "additional_piping": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_proratable_costs_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("proratable_costs") is None
        assert row.get("proratable_costs") != 0.10


def test_empty_proratable_costs_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("proratable_costs") is None


def test_default_proratable_costs_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(proratable_costs=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_proratable_costs_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(proratable_costs=0.20),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_proratable_costs_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(proratable_costs=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "proratable_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["proratable_costs"] for row in keys} == {0.20}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_proratable_costs_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(proratable_costs=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "proratable_costs": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["proratable_costs"] for row in keys} == {0.10}


def test_named_proratable_costs_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(proratable_costs=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "proratable_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_proratable_costs_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["proratable_costs"] for row in keys} == {0.10}


def test_named_zero_proratable_costs_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["proratable_costs"] for row in keys} == {0.0}


def test_named_unity_proratable_costs_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["proratable_costs"] for row in keys} == {1.0}


def test_named_default_proratable_costs_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(proratable_costs=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "proratable_costs": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_proratable_costs_does_not_stamp_field_expenses(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": 0.20},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("proratable_costs") == 0.20
        assert row.get("field_expenses") is None
        assert row.get("lang_factor") is None
        assert row.get("additional_piping") is None


def test_named_slice_at_wrong_proratable_costs_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.10,
                proratable_costs=0.10,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                    "proratable_costs": 0.20,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["additional_piping"] for row in keys} == {0.10}
    assert {row["proratable_costs"] for row in keys} == {0.20}


def test_mixed_proratable_costs_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(proratable_costs=0.20)
    rows[-1]["proratable_costs"] = 0.10
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "proratable_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["proratable_costs"] == 0.20
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_proratable_costs_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "proratable_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["proratable_costs"] for row in keys} == {0.20}


def test_proratable_costs_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(proratable_costs=0.20),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["proratable_costs"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_proratable_costs_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"proratable_costs": 0.20}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("proratable_costs") is None


def test_invalid_proratable_costs_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_proratable_costs_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": 10},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_proratable_costs_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "proratable_costs": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_proratable_costs_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "proratable_costs": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_field_expenses_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("field_expenses") is None
        assert row.get("field_expenses") != 0.10


def test_empty_field_expenses_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("field_expenses") is None


def test_default_field_expenses_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(field_expenses=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_field_expenses_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(field_expenses=0.20),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_field_expenses_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(field_expenses=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "field_expenses": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["field_expenses"] for row in keys} == {0.20}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_field_expenses_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(field_expenses=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "field_expenses": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["field_expenses"] for row in keys} == {0.10}


def test_named_field_expenses_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(field_expenses=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "field_expenses": 0.20},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_field_expenses_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["field_expenses"] for row in keys} == {0.10}


def test_named_zero_field_expenses_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["field_expenses"] for row in keys} == {0.0}


def test_named_unity_field_expenses_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["field_expenses"] for row in keys} == {1.0}


def test_named_default_field_expenses_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(field_expenses=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "field_expenses": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_field_expenses_does_not_stamp_construction(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": 0.20},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("field_expenses") == 0.20
        assert row.get("construction") is None
        assert row.get("lang_factor") is None
        assert row.get("proratable_costs") is None


def test_named_slice_at_wrong_field_expenses_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.10,
                proratable_costs=0.20,
                field_expenses=0.10,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                    "proratable_costs": 0.20,
                    "field_expenses": 0.20,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["proratable_costs"] for row in keys} == {0.20}
    assert {row["field_expenses"] for row in keys} == {0.20}


def test_mixed_field_expenses_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(field_expenses=0.20)
    rows[-1]["field_expenses"] = 0.10
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "field_expenses": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["field_expenses"] == 0.20
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_field_expenses_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "field_expenses": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["field_expenses"] for row in keys} == {0.20}


def test_field_expenses_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(field_expenses=0.20),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["field_expenses"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_field_expenses_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"field_expenses": 0.20}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("field_expenses") is None


def test_invalid_field_expenses_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_field_expenses_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": 10},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_field_expenses_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "field_expenses": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_field_expenses_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "field_expenses": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_construction_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction") is None
        assert row.get("construction") != 0.20


def test_empty_construction_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction") is None


def test_default_construction_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction=0.20),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_construction_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_construction_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["construction"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_construction_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["construction"] for row in keys} == {0.20}


def test_named_construction_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_construction_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": 0.20},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["construction"] for row in keys} == {0.20}


def test_named_zero_construction_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["construction"] for row in keys} == {0.0}


def test_named_unity_construction_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["construction"] for row in keys} == {1.0}


def test_named_default_construction_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction": 0.20},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_construction_does_not_stamp_contingency(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction") == 0.10
        assert row.get("contingency") is None
        assert row.get("lang_factor") is None
        assert row.get("field_expenses") is None


def test_named_slice_at_wrong_construction_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.10,
                proratable_costs=0.20,
                field_expenses=0.20,
                construction=0.20,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                    "proratable_costs": 0.20,
                    "field_expenses": 0.20,
                    "construction": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["field_expenses"] for row in keys} == {0.20}
    assert {row["construction"] for row in keys} == {0.10}


def test_mixed_construction_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(construction=0.10)
    rows[-1]["construction"] = 0.20
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["construction"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_construction_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["construction"] for row in keys} == {0.10}


def test_construction_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(construction=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["construction"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_construction_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"construction": 0.10}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction") is None


def test_invalid_construction_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_construction_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": 20},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_construction_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_construction_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "construction": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_contingency_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("contingency") is None
        assert row.get("contingency") != 0.4


def test_empty_contingency_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("contingency") is None


def test_default_contingency_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(contingency=0.4),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_contingency_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(contingency=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_contingency_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(contingency=0.4),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "contingency": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["contingency"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_contingency_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(contingency=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "contingency": 0.4},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["contingency"] for row in keys} == {0.4}


def test_named_contingency_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(contingency=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "contingency": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_contingency_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": 0.4},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["contingency"] for row in keys} == {0.4}


def test_named_zero_contingency_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["contingency"] for row in keys} == {0.0}


def test_named_unity_contingency_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["contingency"] for row in keys} == {1.0}


def test_named_default_contingency_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(contingency=0.4),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "contingency": 0.4},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_contingency_does_not_stamp_other_indirect_costs(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("contingency") == 0.10
        assert row.get("other_indirect_costs") is None
        assert row.get("lang_factor") is None
        assert row.get("construction") is None


def test_named_slice_at_wrong_contingency_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.10,
                proratable_costs=0.20,
                field_expenses=0.20,
                construction=0.10,
                contingency=0.4,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                    "proratable_costs": 0.20,
                    "field_expenses": 0.20,
                    "construction": 0.10,
                    "contingency": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["construction"] for row in keys} == {0.10}
    assert {row["contingency"] for row in keys} == {0.10}


def test_mixed_contingency_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(contingency=0.10)
    rows[-1]["contingency"] = 0.4
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "contingency": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["contingency"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_contingency_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "contingency": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["contingency"] for row in keys} == {0.10}


def test_contingency_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(contingency=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["contingency"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_contingency_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"contingency": 0.10}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("contingency") is None


def test_invalid_contingency_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_contingency_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": 40},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_contingency_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "contingency": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_contingency_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "contingency": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_other_indirect_costs_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("other_indirect_costs") is None
        assert row.get("other_indirect_costs") != 0.10


def test_empty_other_indirect_costs_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("other_indirect_costs") is None


def test_default_other_indirect_costs_rows_still_complete_when_omitted(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(other_indirect_costs=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_other_indirect_costs_rows_still_complete_when_omitted(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(other_indirect_costs=0.20),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_other_indirect_costs_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(other_indirect_costs=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "other_indirect_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["other_indirect_costs"] for row in keys} == {0.20}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_other_indirect_costs_does_not_accept_other_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(other_indirect_costs=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "other_indirect_costs": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["other_indirect_costs"] for row in keys} == {0.10}


def test_named_other_indirect_costs_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(other_indirect_costs=0.20),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "other_indirect_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_other_indirect_costs_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["other_indirect_costs"] for row in keys} == {0.10}


def test_named_zero_other_indirect_costs_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["other_indirect_costs"] for row in keys} == {0.0}


def test_named_unity_other_indirect_costs_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["other_indirect_costs"] for row in keys} == {1.0}


def test_named_default_other_indirect_costs_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(other_indirect_costs=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "other_indirect_costs": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_other_indirect_costs_does_not_stamp_property_insurance(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": 0.20},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("other_indirect_costs") == 0.20
        assert row.get("property_insurance") is None
        assert row.get("lang_factor") is None
        assert row.get("contingency") is None


def test_named_slice_at_wrong_other_indirect_costs_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.10,
                proratable_costs=0.20,
                field_expenses=0.20,
                construction=0.10,
                contingency=0.10,
                other_indirect_costs=0.10,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                    "proratable_costs": 0.20,
                    "field_expenses": 0.20,
                    "construction": 0.10,
                    "contingency": 0.10,
                    "other_indirect_costs": 0.20,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["contingency"] for row in keys} == {0.10}
    assert {row["other_indirect_costs"] for row in keys} == {0.20}


def test_mixed_other_indirect_costs_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(other_indirect_costs=0.20)
    rows[-1]["other_indirect_costs"] = 0.10
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "other_indirect_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["other_indirect_costs"] == 0.20
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_other_indirect_costs_do_not_fill_named_fraction(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "other_indirect_costs": 0.20},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["other_indirect_costs"] for row in keys} == {0.20}


def test_other_indirect_costs_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(other_indirect_costs=0.20),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["other_indirect_costs"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_other_indirect_costs_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT, "held": {"other_indirect_costs": 0.20},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("other_indirect_costs") is None


def test_invalid_other_indirect_costs_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_other_indirect_costs_is_not_a_remnant_listing(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": 10},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_other_indirect_costs_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "other_indirect_costs": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_other_indirect_costs_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "other_indirect_costs": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_property_insurance_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("property_insurance") is None
        assert row.get("property_insurance") != 0.007


def test_empty_property_insurance_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("property_insurance") is None


def test_default_property_insurance_rows_still_complete_when_omitted(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(property_insurance=0.007),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_property_insurance_rows_still_complete_when_omitted(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(property_insurance=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_property_insurance_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(property_insurance=0.007),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "property_insurance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["property_insurance"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_property_insurance_does_not_accept_other_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(property_insurance=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "property_insurance": 0.007},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["property_insurance"] for row in keys} == {0.007}


def test_named_property_insurance_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(property_insurance=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "property_insurance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_property_insurance_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": 0.007},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["property_insurance"] for row in keys} == {0.007}


def test_named_zero_property_insurance_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["property_insurance"] for row in keys} == {0.0}


def test_named_unity_property_insurance_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["property_insurance"] for row in keys} == {1.0}


def test_named_default_property_insurance_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(property_insurance=0.007),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "property_insurance": 0.007},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_property_insurance_does_not_stamp_maintenance(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("property_insurance") == 0.10
        assert row.get("maintenance") is None
        assert row.get("lang_factor") is None
        assert row.get("other_indirect_costs") is None


def test_named_slice_at_wrong_property_insurance_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.10,
                proratable_costs=0.20,
                field_expenses=0.20,
                construction=0.10,
                contingency=0.10,
                other_indirect_costs=0.20,
                property_insurance=0.007,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                    "proratable_costs": 0.20,
                    "field_expenses": 0.20,
                    "construction": 0.10,
                    "contingency": 0.10,
                    "other_indirect_costs": 0.20,
                    "property_insurance": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["other_indirect_costs"] for row in keys} == {0.20}
    assert {row["property_insurance"] for row in keys} == {0.10}


def test_mixed_property_insurance_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(property_insurance=0.10)
    rows[-1]["property_insurance"] = 0.007
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "property_insurance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["property_insurance"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_property_insurance_do_not_fill_named_fraction(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "property_insurance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["property_insurance"] for row in keys} == {0.10}


def test_property_insurance_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(property_insurance=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["property_insurance"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_property_insurance_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT, "held": {"property_insurance": 0.10},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("property_insurance") is None


def test_invalid_property_insurance_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_property_insurance_is_not_a_remnant_listing(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": 7},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_property_insurance_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "property_insurance": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_property_insurance_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "property_insurance": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_maintenance_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("maintenance") is None
        assert row.get("maintenance") != 0.03


def test_empty_maintenance_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("maintenance") is None


def test_default_maintenance_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(maintenance=0.03),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_maintenance_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(maintenance=0.10),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_maintenance_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(maintenance=0.03),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "maintenance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["maintenance"] for row in keys} == {0.10}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_maintenance_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(maintenance=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "maintenance": 0.03},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["maintenance"] for row in keys} == {0.03}


def test_named_maintenance_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(maintenance=0.10),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "maintenance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_maintenance_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": 0.03},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["maintenance"] for row in keys} == {0.03}


def test_named_zero_maintenance_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": 0.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["maintenance"] for row in keys} == {0.0}


def test_named_unity_maintenance_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": 1.0},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["maintenance"] for row in keys} == {1.0}


def test_named_default_maintenance_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(maintenance=0.03),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "maintenance": 0.03},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_maintenance_does_not_stamp_lang_factor(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": 0.10},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("maintenance") == 0.10
        assert row.get("lang_factor") is None
        assert row.get("property_insurance") is None


def test_named_slice_at_wrong_maintenance_does_not_fill(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session,
            _complete_d18_rows(
                energy_case="C1",
                dissolution_temperature_c=90.0,
                precipitation_temperature_c=40.0,
                solvent_price_usd_per_kg=2.0,
                solvent_loss_pct=3.0,
                feedstock_distance_km=250.0,
                dissolution_capacity=5.0,
                labor_cost_usd_per_employee_yr=150_000.0,
                sell_leftover_plastic=True,
                burn_leftover_plastic=True,
                precipitation_temperature_format="drop",
                precipitation_configuration=_MIX,
                irr=0.12,
                income_tax=0.25,
                operating_days=365.0,
                labor_burden=1.5,
                finance_interest=0.12,
                finance_years=15,
                finance_fraction=0.4,
                startup_months=6,
                startup_FOCfrac=0.5,
                startup_VOCfrac=0.5,
                startup_salesfrac=0.25,
                WC_over_FCI=0.10,
                warehouse=0.10,
                site_development=0.10,
                additional_piping=0.10,
                proratable_costs=0.20,
                field_expenses=0.20,
                construction=0.10,
                contingency=0.10,
                other_indirect_costs=0.20,
                property_insurance=0.10,
                maintenance=0.03,
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
                    "solvent_price_usd_per_kg": 2.0,
                    "solvent_loss_pct": 3.0,
                    "feedstock_distance_km": 250.0,
                    "dissolution_capacity": 5.0,
                    "labor_cost_usd_per_employee_yr": 150_000.0,
                    "sell_leftover_plastic": True,
                    "burn_leftover_plastic": True,
                    "precipitation_temperature_format": "drop",
                    "precipitation_configuration": _MIX,
                    "irr": 0.12,
                    "income_tax": 0.25,
                    "operating_days": 365,
                    "labor_burden": 1.5,
                    "finance_interest": 0.12,
                    "finance_years": 15,
                    "finance_fraction": 0.4,
                    "startup_months": 6,
                    "startup_FOCfrac": 0.5,
                    "startup_VOCfrac": 0.5,
                    "startup_salesfrac": 0.25,
                    "WC_over_FCI": 0.10,
                    "warehouse": 0.10,
                    "site_development": 0.10,
                    "additional_piping": 0.10,
                    "proratable_costs": 0.20,
                    "field_expenses": 0.20,
                    "construction": 0.10,
                    "contingency": 0.10,
                    "other_indirect_costs": 0.20,
                    "property_insurance": 0.10,
                    "maintenance": 0.10,
                },
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["property_insurance"] for row in keys} == {0.10}
    assert {row["maintenance"] for row in keys} == {0.10}


def test_mixed_maintenance_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(maintenance=0.10)
    rows[-1]["maintenance"] = 0.03
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "maintenance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["maintenance"] == 0.10
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_maintenance_do_not_fill_named_fraction(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "maintenance": 0.10},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["maintenance"] for row in keys} == {0.10}


def test_maintenance_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(maintenance=0.10),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["maintenance"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_maintenance_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"maintenance": 0.10}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("maintenance") is None


def test_invalid_maintenance_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": "maybe"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_percent_integer_maintenance_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": 3},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_negative_maintenance_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "maintenance": -0.1},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_maintenance_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "maintenance": "maybe"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_depreciation_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("depreciation") is None
        assert row.get("depreciation") != "MACRS7"


def test_empty_depreciation_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("depreciation") is None


def test_default_depreciation_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(depreciation="MACRS7"),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_depreciation_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(depreciation="MACRS5"),
        )
        payload = _data(tea.rank_landscape(**_sequence_kwargs(handle=handle)))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "landscape_points" not in payload


def test_named_depreciation_does_not_accept_default_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(depreciation="MACRS7"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "depreciation": "MACRS5"},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "sequence_coupling_unproven"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["depreciation"] for row in keys} == {"MACRS5"}
    assert payload["pending_blockers"][0]["error_type"] == (
        "sequence_coupling_unproven"
    )
    assert "landscape_points" not in payload


def test_named_default_depreciation_does_not_accept_other_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(depreciation="MACRS5"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "depreciation": "MACRS7"},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["depreciation"] for row in keys} == {"MACRS7"}


def test_named_depreciation_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(depreciation="MACRS5"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "depreciation": "MACRS5"},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_depreciation_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": "MACRS7"},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["depreciation"] for row in keys} == {"MACRS7"}


def test_named_default_depreciation_completes_on_matching_default_rows(
    monkeypatch,
):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(depreciation="MACRS7"),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "depreciation": "MACRS7"},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload


def test_depreciation_does_not_stamp_unbound_g_fields(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": "MACRS5"},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("depreciation") == "MACRS5"
        assert row.get("lang_factor") is None
        assert row.get("steam_power_depreciation") is None
        assert row.get("construction_schedule") is None
        assert row.get("maintenance") is None


def test_mixed_depreciation_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
    rows = _complete_d18_rows(depreciation="MACRS5")
    rows[-1]["depreciation"] = "MACRS7"
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, rows)
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "depreciation": "MACRS5"},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 1
    assert keys[0]["depreciation"] == "MACRS5"
    assert keys[0]["polymer"] == "EVOH"
    assert keys[0]["target_mass_percent"] == 100.0


def test_rows_without_depreciation_do_not_fill_named_schedule(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "depreciation": "MACRS5"},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {row["depreciation"] for row in keys} == {"MACRS5"}


def test_depreciation_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(depreciation="MACRS5"),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["depreciation"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_depreciation_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"depreciation": "MACRS5"}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("depreciation") is None


def test_invalid_depreciation_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": "macrs7"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_integer_depreciation_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": 7},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_array_depreciation_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": ["MACRS7"]},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_unimplemented_macrs_is_not_a_remnant_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "depreciation": "MACRS4"},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_depreciation_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "depreciation": "macrs7"},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_duration_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("duration") is None
        assert row.get("duration") != (2025, 2055)
        assert row.get("duration") != [2025, 2055]


def test_empty_duration_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "duration": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("duration") is None


def test_default_duration_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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


def test_named_duration_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(duration=[2025, 2045]),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "duration": (2025, 2045)},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_duration_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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


def test_duration_does_not_stamp_unbound_g_fields(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "duration": [2025, 2045]},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert tuple(row["duration"]) == (2025, 2045)
        assert row.get("lang_factor") is None
        assert row.get("steam_power_depreciation") is None
        assert row.get("construction_schedule") is None
        assert row.get("depreciation") is None


def test_mixed_duration_handle_leaves_the_unmatched_remnant(monkeypatch):
    _forbid_live(monkeypatch)
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


def test_rows_without_duration_do_not_fill_named_pair(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "duration": [2025, 2045]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["duration"]) for row in keys} == {(2025, 2045)}


def test_duration_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(duration=[2025, 2045]),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["duration"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_duration_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "held": {"duration": [2025, 2045]}},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("duration") is None


def test_years_scalar_duration_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "duration": 30},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_inverted_duration_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "duration": [2055, 2025]},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_invalid_duration_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "duration": 30},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"


def test_omitted_construction_schedule_is_not_a_silent_default(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(**_sequence_kwargs()))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction_schedule") is None
        assert row.get("construction_schedule") != (0.08, 0.60, 0.32)
        assert row.get("construction_schedule") != [0.08, 0.60, 0.32]


def test_empty_construction_schedule_is_the_omitted_grain(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction_schedule": ""},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction_schedule") is None


def test_default_construction_schedule_rows_still_complete_when_omitted(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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


def test_named_construction_schedule_completes_on_matching_rows(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(
            session, _complete_d18_rows(construction_schedule=[0.5, 0.5]),
        )
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction_schedule": (0.5, 0.5)},
            ),
        ))
    assert payload.get("error_code") == "sequence_coupling_unproven"
    assert "pending_blockers" not in payload
    assert "missing_keys" not in payload
    assert "landscape_points" not in payload


def test_named_default_construction_schedule_is_holdable(monkeypatch):
    _forbid_live(monkeypatch)
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
    _forbid_live(monkeypatch)
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


def test_construction_schedule_does_not_stamp_unbound_g_fields(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction_schedule": [0.5, 0.5]},
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert tuple(row["construction_schedule"]) == (0.5, 0.5)
        assert row.get("lang_factor") is None
        assert row.get("steam_power_depreciation") is None
        assert row.get("duration") is None
        assert row.get("depreciation") is None


def test_mixed_construction_schedule_handle_leaves_the_unmatched_remnant(
    monkeypatch,
):
    _forbid_live(monkeypatch)
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


def test_rows_without_construction_schedule_do_not_fill_named(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _plant_handle(session, _complete_d18_rows())
        payload = _data(tea.rank_landscape(
            **_sequence_kwargs(
                handle=handle,
                process_config={**_CAP_20KT, "construction_schedule": [0.5, 0.5]},
            ),
        ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    keys = payload["missing_keys"]
    assert len(keys) == 4
    assert {tuple(row["construction_schedule"]) for row in keys} == {(0.5, 0.5)}


def test_construction_schedule_kwarg_is_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(construction_schedule=[0.5, 0.5]),
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["construction_schedule"]
    assert payload.get("error_code") != "incomplete_stage_basis_grid"


def test_nested_construction_schedule_does_not_count(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={
                **_CAP_20KT,
                "held": {"construction_schedule": [0.5, 0.5]},
            },
        ),
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    for row in payload["missing_keys"]:
        assert row.get("construction_schedule") is None


def test_scalar_construction_schedule_is_not_a_listing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        **_sequence_kwargs(
            process_config={**_CAP_20KT, "construction_schedule": 3},
        ),
    ))
    assert payload.get("error_code") == "invalid_admitted_record_query"
    assert payload.get("error_code") != "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "invalid_scenario"


def test_invalid_construction_schedule_does_not_outrank_missing_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        feed_mass_fractions=_FEED_55_45,
        process_config={**_CAP_20KT, "construction_schedule": 3},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("error_code") != "invalid_admitted_record_query"



