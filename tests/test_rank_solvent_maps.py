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
    assert len(EXPECTED_REGISTRY_NAMES) == 34
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
    assert "evaluate_process" not in EXPECTED_REGISTRY_NAMES


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


def test_handle_does_not_ingest_the_remnant_table(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        feed_mass_fractions={"LDPE": 0.55, "EVOH": 0.45},
        process_config={"processing_capacity_mt_per_yr": 20_000},
        handle="not-a-remnant-table",
    ))
    assert payload.get("error_code") == "incomplete_stage_basis_grid"
    assert payload.get("error_code") != "unknown_handle"
    assert "landscape_points" not in payload
    assert payload.get("n_usable") is None
    assert len(payload["missing_keys"]) == 4


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

