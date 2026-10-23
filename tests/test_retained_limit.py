"""The planner's no-co-dissolution limit: a stage qualifies only when every polymer left behind dissolves at or below
max_retained_pct (default 3 wt%), and a solvent's best temperature is one that meets that limit if any does."""
import json

from dissolve import separation, thermodynamics as thermo


def _plan(feed, **kwargs):
    with thermo.bind_query_solvent_scope("common"):
        return json.loads(separation.plan_multistage_separation(
            feed_polymers=feed, temperature_max_c=90, **kwargs))["data"]


def _worst_retained(step):
    return max((step.get("off_target_solubilities_pct") or {}).values())


def test_default_limit_keeps_every_retained_polymer_at_or_below_3_wt_pct():
    data = _plan(["PVC", "PS", "HDPE"])
    assert data["max_retained_pct"] == 3.0
    assert "every polymer left behind <= 3 wt%" in data["stage_rule"]
    assert data["complete"] is True
    for route in data["top_k_sequences"]:
        for step in route["steps"]:
            assert _worst_retained(step) <= 3.0


def test_without_the_limit_the_gap_alone_lets_a_retained_polymer_dissolve_past_it():
    # Counterfactual: the gap rule alone takes PS in toluene at 90 °C with PVC at about 8.5 wt%.
    data = _plan(["PVC", "PS"], max_retained_pct=None)
    assert data["max_retained_pct"] is None
    assert _worst_retained(data["steps"][0]) > 3.0
    capped = _plan(["PVC", "PS"])
    assert _worst_retained(capped["steps"][0]) <= 3.0
    assert capped["steps"][0]["selectivity_pct"] >= 5.0


def test_a_negative_limit_is_refused():
    with thermo.bind_query_solvent_scope("common"):
        out = json.loads(separation.plan_multistage_separation(
            feed_polymers=["PVC", "PS"], max_retained_pct=-1))
    assert out["data"]["success"] is False
    assert out["data"]["error_code"] == "invalid_max_retained_pct"


def test_the_agent_can_set_another_limit_and_null_keeps_the_default():
    from dissolve.agent import bind_tool_session, dispatch, tool_schema_for
    from dissolve.session import new_session

    prop = tool_schema_for("plan_multistage_separation")["parameters"]["properties"]["max_retained_pct"]
    assert prop["default"] == 3.0
    with bind_tool_session(new_session()), thermo.bind_query_solvent_scope("common"):
        loose = dispatch("plan_multistage_separation", feed_polymers=["PVC", "PS"], temperature_max_c=90,
                         max_retained_pct=5)
        default = dispatch("plan_multistage_separation", feed_polymers=["PVC", "PS"], temperature_max_c=90,
                           max_retained_pct=None)
    assert loose["available"] is True and loose["data"]["max_retained_pct"] == 5.0
    assert default["data"]["max_retained_pct"] == 3.0
