"""Explore branch (2026-10-15): two tool outputs that agents misread on the agent benchmark."""
import json

from dissolve import thermodynamics as t


def _data(raw):
    return json.loads(raw)["data"]


def test_pair_screen_says_which_pressure_it_assumed():
    # An echoed require_atmospheric null (the 1 atm default) was read as "pressure allowed".
    default = _data(t.screen_pairwise_solubility_overlap(["PC", "PS", "PVC"], 25, 95))
    assert default["require_atmospheric"] is None
    assert default["pressure_basis"].startswith("1 atm (the default)")
    assert "send require_atmospheric false" in default["pressure_basis"]
    assert default["excluded_for_boiling_point"] > 0
    allowed = _data(t.screen_pairwise_solubility_overlap(["PC", "PS", "PVC"], 25, 95, require_atmospheric=False))
    assert allowed["pressure_basis"].startswith("Pressure allowed")
    assert allowed["excluded_for_boiling_point"] == 0
    assert _data(t.screen_polymer_separation(["PC", "PS"], 25, 95, require_atmospheric=True))[
        "pressure_basis"].startswith("1 atm, strict")


def test_separation_gap_splits_its_qualifying_count_by_direction():
    # separation_gap is unsigned; its qualifying total was read as "solvents that dissolve the target away".
    raw = t.screen_polymer_separation(["LDPE", "PVC"], 25, 145, target_polymers=["LDPE"],
                                      ranking_mode="separation_gap", require_atmospheric=True, top_k=60)
    data = _data(raw)
    direction = data["screened_directions"][0]
    split = direction["qualifying_by_gap_direction"]
    assert sum(split.values()) == direction["qualifying_total"]
    # the same split sits at the top level, beside qualifying_total_by_target, so the compact view shows it
    assert data["qualifying_by_gap_direction_by_target"]["LDPE"] == split
    assert split["target_less_soluble_than_all_others"] > 0
    assert any(w.startswith("separation_gap counts a gap in either direction") and "target_dissolution" in w
               for w in data["warnings"])
    assert "either direction" in data["qualifying_rule"]
    # the table shows the signed gap, so a row where the other polymer dissolves more reads as negative
    display = json.loads(raw)["display"]
    assert "Gap, target minus closest other" in display
    signed = [row["signed_target_minus_limiting_off_target_pct"] for row in data["ranked_candidates"]]
    assert all(f"{value:+.6g}" in display for value in signed[:5])


def test_target_dissolution_is_unchanged_by_the_split():
    data = _data(t.screen_polymer_separation(["LDPE", "PVC"], 25, 145, target_polymers=["LDPE"],
                                             require_atmospheric=True))
    assert "qualifying_by_gap_direction" not in data["screened_directions"][0]
    assert "qualifying_by_gap_direction_by_target" not in data
    assert not any(w.startswith("separation_gap counts") for w in data["warnings"])


def test_the_agent_sees_the_direction_split_and_the_pressure_sentence():
    # The split first lived only in screened_directions, which the compact view of a large result leaves out; the
    # rule text pointed at a field the agent never received (team 2, fix8 regrade, 2026-10-15).
    from dissolve.agent import dispatch
    from dissolve.session import bind_tool_session, new_session
    with bind_tool_session(new_session()):
        out = json.dumps(dispatch("screen_polymer_separation", feed_polymers=["LDPE", "PVC"], temperature_min_c=25,
                                  temperature_max_c=145, target_polymers=["LDPE"], ranking_mode="separation_gap",
                                  require_atmospheric=True, top_k=60))
    assert "qualifying_by_gap_direction_by_target" in out
    assert "counts a gap in either direction" in out
    assert "pressure_basis" in out
