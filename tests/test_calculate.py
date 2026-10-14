"""The calculate tool: arithmetic on tool values, a number or a whole stored column. Each test states the behaviour
it protects; the stored-column tests run on a real solubility result, not a fixture."""
import inspect
import json

import pytest

from dissolve import agent
from dissolve.agent import SYSTEM_PROMPT, dispatch, tool_schemas
from dissolve.calculate import calculate
from dissolve.session import bind_tool_session, new_session


def _data(**kwargs):
    return json.loads(calculate(**kwargs))["data"]


def test_two_numbers_give_the_difference_and_the_ratio_without_float_noise():
    assert _data(operation="difference", a=[189.0], b=[25.0])["result"] == 164.0
    assert _data(operation="difference", a=[0.3], b=[0.1])["result"] == 0.2          # not 0.19999999999999998
    assert _data(operation="ratio", a=[1.0], b=[4.0])["result"] == 0.25
    assert _data(operation="percent_change", a=[110.0], b=[100.0])["result"] == 10.0


def test_a_column_keeps_its_labels_and_a_single_b_applies_to_every_value():
    data = _data(operation="difference", a=[10.0, 12.5, 20.0], b=[5.0], labels=["25", "30", "35"])
    assert [(r["label"], r["value"]) for r in data["rows"]] == [("25", 5.0), ("30", 7.5), ("35", 15.0)]
    assert "result" not in data


def test_min_and_max_say_where_and_report_ties():
    data = _data(operation="max", a=[1.0, 5.0, 5.0], labels=["x", "y", "z"])
    assert data["result"] == 5.0 and data["at_labels"] == ["y", "z"] and data["tied"] is True
    assert _data(operation="min", a=[3.0, 1.0], labels=["x", "y"])["at_labels"] == ["y"]


def test_threshold_counts_respect_inclusive_and_exclusive_bounds():
    values, labels = [4.999, 5.0, 7.0], ["a", "b", "c"]
    assert _data(operation="keep_at_least", a=values, labels=labels, threshold=5)["kept_labels"] == ["b", "c"]
    assert _data(operation="keep_above", a=values, labels=labels, threshold=5)["kept_count"] == 1
    assert _data(operation="keep_between", a=values, labels=labels, threshold=5, upper=7)["kept_count"] == 2
    assert _data(operation="keep_below", a=values, labels=labels, threshold=1)["kept_count"] == 0


def test_step_rounding_goes_the_way_it_is_asked_also_below_zero():
    assert _data(operation="round_down_to_step", a=[145.538], step=5)["result"] == 145.0
    assert _data(operation="round_up_to_step", a=[145.538], step=5)["result"] == 150.0
    assert _data(operation="round_down_to_step", a=[-24.6], step=5)["result"] == -25.0
    assert _data(operation="round_to_step", a=[0.35], step=0.1)["result"] == 0.4      # decimal, not binary, halves
    assert _data(operation="round", a=[0.297696172], decimals=3)["result"] == 0.298


@pytest.mark.parametrize("kwargs, code", [
    (dict(operation="ratio", a=[1.0], b=[0.0]), "division_by_zero"),
    (dict(operation="difference", a=[1.0, 2.0], b=[1.0, 2.0, 3.0]), "length_mismatch"),
    (dict(operation="difference", a=[1.0]), "missing_input"),
    (dict(operation="max", a=[1.0], b=[2.0]), "unused_input"),
    (dict(operation="keep_above", a=[1.0]), "missing_input"),
    (dict(operation="round_down_to_step", a=[1.0], step=0), "missing_input"),
    (dict(operation="sum", a=[1.0, float("nan")], b=[1.0]), "non_numeric_value"),
    (dict(operation="sum", a=[True], b=[1.0]), "non_numeric_value"),
    (dict(operation="interpolate", a=[1.0], b=[2.0]), "unknown_operation"),
    (dict(operation="mean", a=[1.0, 2.0]), "unknown_operation"),          # averaging grid nodes stays out
    (dict(operation="difference", a=[1.0], a_from="h:x", b=[1.0]), "two_sources"),
    (dict(operation="difference", a_from="no-such-handle:x", b=[1.0]), "unknown_handle"),
])
def test_what_it_cannot_do_is_refused_with_a_reason(kwargs, code):
    data = _data(**kwargs)
    assert data["success"] is False and data["error_code"] == code


def test_the_tool_is_offered_with_typed_lists_and_its_results_name_their_basis():
    schema = next(item for item in tool_schemas() if item["name"] == "calculate")
    props = schema["parameters"]["properties"]
    assert props["a"] == {"type": "array", "items": {"type": "number"}}      # Gemini needs typed array items
    assert props["a_where"] == {"type": "array", "items": {"type": "string"}}
    assert schema["parameters"]["required"] == ["operation"]
    out = dispatch("calculate", operation="difference", a=[189.0], b=[25.0])
    assert out["available"] and out["source_basis"] == "calculated_from_supplied_values"
    assert out["data"]["result"] == 164.0 and out["data"]["a"] == 189.0 and out["data"]["b"] == 25.0


def test_the_instructions_send_arithmetic_to_the_tool_and_no_longer_forbid_it():
    assert "you must not perform" not in SYSTEM_PROMPT and "do not have a calculator" not in SYSTEM_PROMPT
    assert "call\ncalculate and report its result" in SYSTEM_PROMPT
    assert "Do not interpolate. Do not average two grid nodes." in SYSTEM_PROMPT   # those bans stay


def _stored_curves():
    """PVC and PC in acetone at every stored temperature from 25 to 95 °C: more than one page, so it has a handle."""
    out = dispatch("solubility_query", polymers=["PVC", "PC"], solvents=["acetone"],
                   temperatures=[float(t) for t in range(25, 100, 5)], top_k=100)
    assert out["available"] and out.get("handle"), out
    return out["handle"], agent.handle_rows(agent.load_handle(agent.current_tool_session(), out["handle"]))


def test_two_stored_curves_are_subtracted_at_every_temperature_and_the_largest_gap_is_found_by_handle():
    with bind_tool_session(new_session()):
        handle, rows = _stored_curves()
        at = {(r["polymer"], r["temperature_c"]): r["solubility_pct"] for r in rows}
        temps = sorted({t for _, t in at})
        assert len(temps) >= 10
        gap = dispatch("calculate", operation="difference", a_from=f"{handle}:solubility_pct", a_where=["polymer=PVC"],
                       b_from=f"{handle}:solubility_pct", b_where=["polymer=PC"], labels_from="temperature_c")
        assert gap["available"] and gap.get("handle"), gap
        got = {float(r["label"]): r["value"] for r in agent.handle_rows(
            agent.load_handle(agent.current_tool_session(), gap["handle"]))}
        assert set(got) == set(temps)                                    # every stored node, none skipped
        assert all(got[t] == pytest.approx(at[("PVC", t)] - at[("PC", t)], abs=1e-9) for t in temps)
        best = dispatch("calculate", operation="max", a_from=f"{gap['handle']}:value", labels_from="label")
        want = max(temps, key=lambda t: at[("PVC", t)] - at[("PC", t)])
        assert best["data"]["result"] == pytest.approx(at[("PVC", want)] - at[("PC", want)], abs=1e-9)
        assert best["data"]["at_labels"] == [str(want)]
        kept = dispatch("calculate", operation="keep_at_least", a_from=f"{handle}:solubility_pct",
                        a_where=["polymer=PVC"], labels_from="temperature_c", threshold=20)
        assert kept["data"]["kept_count"] == sum(at[("PVC", t)] >= 20 for t in temps)


def test_stored_columns_are_paired_by_label_and_never_by_position():
    with bind_tool_session(new_session()):
        handle, rows = _stored_curves()
        short = dispatch("calculate", operation="difference", a_from=f"{handle}:solubility_pct",
                         a_where=["polymer=PVC"], b_from=f"{handle}:solubility_pct",
                         b_where=["polymer=PC", "temperature_c>=40"], labels_from="temperature_c")
        assert short["refusal"] == "labels_do_not_match" and "25.0" in short["data"]["only_in_a"]
        both = dispatch("calculate", operation="difference", a_from=f"{handle}:solubility_pct",
                        b_from=f"{handle}:solubility_pct", b_where=["polymer=PC"], labels_from="temperature_c")
        assert both["refusal"] == "labels_not_unique"                      # a holds both polymers at each temperature
        one = dispatch("calculate", operation="ratio", a_from=f"{handle}:solubility_pct",
                       a_where=["polymer=PC", "temperature_c=60"], b_from=f"{handle}:solubility_pct",
                       b_where=["polymer=PVC", "temperature_c=60"])
        at = {(r["polymer"], r["temperature_c"]): r["solubility_pct"] for r in rows}
        assert one["data"]["result"] == pytest.approx(at[("PC", 60.0)] / at[("PVC", 60.0)], rel=1e-11)
        assert dispatch("calculate", operation="max", a_from=f"{handle}:no_such_column")["refusal"] == "unknown_column"
        assert dispatch("calculate", operation="max", a_from=f"{handle}:solubility_pct",
                        a_where=["polymer=PTFE"])["refusal"] == "no_rows_match"


def test_an_empty_optional_argument_means_not_given():
    """One model writes every argument of a call and leaves the unused ones empty; 159 of its 184 calculate calls
    were refused for b=[] or a_from="" (2026-10-13). The first call below is one of them, as sent."""
    with bind_tool_session(new_session()):
        sent = dict(operation="round_down_to_step", a=[75.681328], b=[], labels=[], a_from="", b_from="",
                    labels_from="", a_where=[], b_where=[], threshold=0, upper=0, step=5, decimals=0)
        assert dispatch("calculate", **sent)["data"]["result"] == 75.0
        assert json.loads(calculate(**sent))["data"]["result"] == 75.0     # and when the function is called directly
        gap = dispatch("calculate", operation="difference", a=[95], b=[55], labels=[], a_from=" ", b_where=[])
        assert gap["data"]["result"] == 40.0
        # a required argument sent empty, and an input the operation does not take, are still refused
        assert dispatch("calculate", operation="", a=[1.0])["refusal"] == "unknown_operation"
        assert dispatch("calculate", operation="max", a=[], b=[])["refusal"] == "missing_input"
        assert dispatch("calculate", operation="max", a=[1.0, 2.0], b=[3.0])["refusal"] == "unused_input"


def test_dispatch_drops_empty_optional_arguments_for_every_tool():
    kept = agent._without_empty_optionals("calculate", {"operation": "", "a": [], "b": {}, "a_from": "  ",
                                                         "threshold": 0, "decimals": 0, "labels": ["x"]})
    assert kept == {"operation": "", "threshold": 0, "decimals": 0, "labels": ["x"]}
    for name, tool in agent.BY_NAME.items():
        optional = [p.name for p in inspect.signature(tool.fn).parameters.values()
                    if p.default is not inspect.Parameter.empty]
        required = [p.name for p in inspect.signature(tool.fn).parameters.values()
                    if p.default is inspect.Parameter.empty]
        sent = {**{k: "" for k in optional}, **{k: "" for k in required}}
        assert sorted(agent._without_empty_optionals(name, sent)) == sorted(required), name


def test_a_value_sent_as_its_json_text_is_decoded_and_other_text_is_left_alone():
    """One model writes every list argument as a string holding its JSON, e.g. polymers='["PVC"]' (2026-10-14);
    the tool refused each call until the reply cap. The JSON text of a list or object is decoded only for an
    argument that takes a list or an object and not a string; the counterfactual is the refusal without it."""
    sent = {"polymers": '["PVC"]', "solvents": '["acetone"]', "temperatures": "[25, 30, 35]"}
    assert agent._with_json_text_decoded("solubility_query", sent) == {
        "polymers": ["PVC"], "solvents": ["acetone"], "temperatures": [25, 30, 35]}
    # text that is not JSON, or is JSON of another kind, stays as sent and the tool refuses it as before
    for value in ("PVC", '"PVC"', "{not json", '{"a": 1}'):
        assert agent._with_json_text_decoded("solubility_query", {"polymers": value}) == {"polymers": value}
    # an argument that takes a string is never decoded
    for name, tool in agent.BY_NAME.items():
        props = agent.tool_schema_for(name)["parameters"]["properties"]
        for key, spec in props.items():
            kinds = {t for part in spec.get("anyOf") or [spec]
                     for t in (part.get("type") if isinstance(part.get("type"), list) else [part.get("type")])}
            if "string" in kinds:
                assert agent._with_json_text_decoded(name, {key: '["x"]'}) == {key: '["x"]'}, (name, key)
    # numbers and booleans sent as text, as the same model sent them; a word that is not that kind stays as sent
    assert agent._with_json_text_decoded("solubility_query", {"top_k": "100"}) == {"top_k": 100}
    assert agent._with_json_text_decoded("solubility_query", {"top_k": "1.5"}) == {"top_k": "1.5"}
    assert agent._with_json_text_decoded("solubility_query", {"top_k": "many"}) == {"top_k": "many"}
    assert agent._with_json_text_decoded("screen_contaminant_leaching", {"strict_maximum": "true",
        "max_temperature_c": "100"}) == {"strict_maximum": True, "max_temperature_c": 100}
    assert agent._with_json_text_decoded("screen_contaminant_leaching", {"strict_maximum": "yes"}) == {
        "strict_maximum": "yes"}
    with bind_tool_session(new_session()):
        assert dispatch("solubility_query", polymers='["PVC"]', top_k="5").get("refusal") is None
        out = dispatch("calculate", operation="max", a="[1.0, 4.0, 2.0]")
        assert out["data"]["result"] == 4.0
