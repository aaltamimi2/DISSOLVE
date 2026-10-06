"""Contaminants assessed whenever they are named (owner, 2026-10-05): no setting, both routes at every stage of the best
separation, one stated rule, the safest eligible wash, truthful states. Spec v2 of the 2026-10-05 audit."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from dissolve import agent, contaminant_removal, contaminants, separation
from dissolve import thermodynamics as thermo
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session

FIXTURE = Path(__file__).parent / "fixtures" / "contaminant_always_on" / "planner_no_contaminant.v1.json"
DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def test_plans_naming_no_contaminant_are_byte_identical_to_before():
    """Every plain separation question must come out exactly as before the change: 24 frozen payloads (four feeds, two
    solvent scopes, three argument sets) captured at main a9dfda16."""
    cases = json.loads(FIXTURE.read_text())["cases"]
    assert len(cases) == 24
    for case in cases:
        with thermo.bind_query_solvent_scope(case["scope"]):
            raw = separation.plan_multistage_separation(list(case["feed"]), **case["kwargs"])
        assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == case["sha256"], case["feed"]


def test_pvc_pet_dehp_gets_the_safest_eligible_wash_not_chloroform():
    """The live leaching run washed PVC/PET with chloroform at 60 °C, 1.2 °C below its boiling point, because washes
    were ordered by partitioning alone. The rule now washes with a recommended or problematic solvent at least 10 °C
    below boiling, before any dissolution (a later wash cannot reach products already recovered)."""
    with thermo.bind_query_solvent_scope("common"):
        data = _data(separation.plan_multistage_separation(["PVC", "PET"], contaminants=["DEHP"]))
    removal = data["contaminant_removal"]
    assert (removal["applied_route"], removal["status"], removal["selected_by"]) == ("wash", "applied_screen_pass", "rule")
    (stage,) = removal["strap"]["stages"]
    assert (stage["solvent"], stage["verdict"], stage["reason"]) == (
        "Diethylene Glycol", "not_checkable", "no_contaminant_data_for_solvent")
    chosen = removal["wash"]["chosen"]
    assert chosen["solvent"] != "chloroform" and chosen["position"] == 0
    assert chosen["chem21_band"] in contaminants.WASH_AUTO_BANDS and chosen["boiling_margin_c"] >= 10.0
    assert data["steps"][0]["step_kind"] == "wash" and data["best_sequence"][0] == "wash"
    assert removal["whole_feed_cleanup_validated"] is False


def test_wash_conditions_never_assume_a_boiling_point_or_invent_a_temperature(monkeypatch):
    nodes = [25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0]
    monkeypatch.setattr(thermo, "_grid_nodes", lambda: nodes)
    monkeypatch.setattr(contaminants, "_regime", lambda _s, t: "t_higher" if t is not None and t >= 40 else "rt")
    for missing in (None, float("nan"), float("inf")):
        monkeypatch.setattr(thermo, "get_boiling_point", lambda _s, value=missing: value)
        assert contaminants.wash_conditions("x", None)["unavailable"] == "boiling_margin_unverifiable"
    monkeypatch.setattr(thermo, "get_boiling_point", lambda _s: 60.0)  # cap exactly 50, a node: margin exactly 10
    exact = contaminants.wash_conditions("x", None)
    assert (exact["temperature_c"], exact["boiling_margin_c"], exact["unavailable"]) == (50.0, 10.0, None)
    monkeypatch.setattr(thermo, "get_boiling_point", lambda _s: 59.9)  # cap 49.9: never 50
    assert contaminants.wash_conditions("x", None)["temperature_c"] == 45.0
    monkeypatch.setattr(thermo, "get_boiling_point", lambda _s: 34.0)  # cap 24: no node, no off-grid fallback
    assert contaminants.wash_conditions("x", None)["unavailable"] == "no_admissible_wash_grid_node"
    monkeypatch.setattr(thermo, "get_boiling_point", lambda _s: 200.0)
    assert contaminants.wash_conditions("x", 42.0)["temperature_c"] == 40.0  # the requested ceiling still binds


def test_the_miscibility_regime_is_read_at_the_node_not_the_cap(monkeypatch):
    """A cap of 49.5 °C is hot (threshold 47.5), but the wash runs at the 45 °C node, which is not: the regime must be
    the node's (audit AB05). The old code kept the cap's regime."""
    monkeypatch.setattr(thermo, "_grid_nodes", lambda: [25.0, 30.0, 35.0, 40.0, 45.0, 50.0])
    monkeypatch.setattr(thermo, "get_boiling_point", lambda _s: 59.5)
    monkeypatch.setattr(contaminants, "_regime", lambda _s, t: "t_higher" if t is not None and t >= 47.5 else "rt")
    conditions = contaminants.wash_conditions("x", None)
    assert (conditions["temperature_c"], conditions["regime"]) == (45.0, "rt")


def test_one_total_wash_order_safety_first_and_independent_of_input_order():
    def row(solvent, band, worst, margin, logd):
        return {"solvent": solvent, "chem21_band": band, "chem21_max_subscore": worst, "boiling_margin_c": margin,
                "contaminant_logd_min": logd, "operating_temperature_c": 50.0}

    safe_weak = row("a", "recommended", 4, 12.0, 0.1)
    hazardous_strong = row("b", "hazardous", 9, 30.0, 9.0)
    unknown = row("c", None, None, 40.0, 5.0)
    unscored = row("d", "recommended", None, 40.0, 5.0)
    twin_1, twin_2 = row("e", "problematic", 6, 15.0, 1.0), row("f", "problematic", 6, 15.0, 1.0)
    rows = [hazardous_strong, unknown, twin_2, safe_weak, unscored, twin_1]
    ordered = [r["solvent"] for r in sorted(rows, key=contaminants.wash_order_key)]
    assert ordered == [r["solvent"] for r in sorted(reversed(rows), key=contaminants.wash_order_key)]
    assert ordered == ["a", "d", "e", "f", "b", "c"]  # band first; an unknown sub-score is worst, never zero


def test_the_screen_and_the_planner_pick_the_same_wash_for_the_same_polymers():
    """A wash at position 0 sees the whole feed, exactly what the standalone screen sees with the same polymers."""
    with thermo.bind_query_solvent_scope("all"):
        plan = _data(separation.plan_multistage_separation(["PVC", "PET"], contaminants=["DEHP"]))
        screen = _data(contaminants.screen_contaminant_leaching("PVC", ["DEHP"], other_polymers=["PET"]))
    chosen = plan["contaminant_removal"]["wash"]["chosen"]
    assert chosen["position"] == 0
    assert chosen["solvent"] == screen["auto_eligible_solvents"][0]


def test_a_large_comparison_pages_through_the_real_dispatcher_and_an_empty_one_needs_no_page():
    """compare_contaminant_removal_modes was refused live with unaddressable_result: two whole screens nested and no
    row list to page. Its rows are now the page."""
    with bind_tool_session(new_session()):
        large = agent.dispatch("compare_contaminant_removal_modes", target_polymer="PVC", contaminants=["DEHP"])
        empty = agent.dispatch("compare_contaminant_removal_modes", target_polymer="PVC", contaminants=["DEHP"],
                               solvents=["water"])
    assert large.get("refusal") != "unaddressable_result" and large.get("available") is True
    assert large["total"] > large["shown"] > 0 and large["top"][0]["route"] in {"strap", "wash"}
    assert large["data"]["context"] == "single_polymer_screen" and large["data"]["route"] in {"strap", "wash", "none"}
    assert empty.get("refusal") != "unaddressable_result"


def test_the_summary_stays_whole_within_its_budget_for_a_long_request():
    names = ["PFAS"] + [f"made-up contaminant {i}" for i in range(30)]
    data = _data(separation.plan_multistage_separation(["LDPE", "PET", "EVOH"], contaminants=names, breadth=1))
    removal = data["contaminant_removal"]
    assert len(json.dumps(removal, ensure_ascii=False).encode("utf-8")) <= contaminant_removal.SUMMARY_BYTES
    assert removal["contaminants"]["unsupported_total"] == 30 and len(removal["contaminants"]["unsupported"]) == 5
    assert removal["contaminants"]["assessed_total"] == 26
    assert removal["contaminants"]["coverage"] == "only the supported subset was assessed"
    assert all(stage["verdict"] in {"pass", "fail", "not_checkable"} for stage in removal["strap"]["stages"])


def test_the_agent_sees_contaminant_route_and_the_rule():
    (planner,) = [entry for entry in agent.tool_schemas() if entry["name"] == "plan_multistage_separation"]
    schema = json.dumps(planner)
    assert "contaminant_route" in schema and "contaminant_mode" not in schema
    assert all(token in schema for token in ('"auto"', '"strap"', '"wash"'))
    assert "pass it as\n  contaminants to plan_multistage_separation" in agent.SYSTEM_PROMPT
    first_line = (separation.plan_multistage_separation.__doc__ or "").strip().splitlines()[0]
    assert "contaminants" in first_line and "stated rule" in first_line


def test_strap_at_a_stage_reports_absent_evidence_as_not_checkable():
    unknown_solvent = contaminants.strap_at_stage("LDPE", "no-such-solvent", [], [DEHP], 105.0)
    assert (unknown_solvent["verdict"], unknown_solvent["reason"]) == ("not_checkable", "no_contaminant_data_for_solvent")
    cold = contaminants.strap_at_stage("LDPE", "toluene", ["EVOH"], [DEHP], 25.0)
    assert cold["verdict"] in {"fail", "not_checkable"} and cold["dissolution_c"] == 25.0
    assert not math.isnan(float(cold["dissolution_c"]))


@pytest.mark.parametrize("route", ["auto", "strap", "wash"])
def test_an_empty_or_partial_route_never_passes_strap_vacuously(route):
    partial = {"complete": False, "final_residue": None, "unresolved_polymers": ["LDPE", "PP"], "steps": []}
    steps, summary = contaminant_removal.assess(
        partial, feed=["LDPE", "PP"], supported=[DEHP], request={"supported": [DEHP]}, requested_route=route,
        maximum=None, strict_maximum=False, step_c=5.0,
    )
    assert summary["strap"]["all_stages_pass"] is False and summary["applied_route"] != "strap"
