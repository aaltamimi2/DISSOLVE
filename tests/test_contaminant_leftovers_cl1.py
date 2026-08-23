"""CL-1: validate contaminant_mode before the empty-contaminants short-circuit.

Session / off stay no-ops. Accept test 2 is unchanged. No BioSTEAM.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import contaminants, separation
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session


_DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _plan(**kwargs) -> dict:
    return _data(separation.plan_multistage_separation(
        ["LDPE", "PP"], top_k_routes=1, breadth=1, **kwargs,
    ))


def _candidate(payload: dict, solvent: str) -> dict:
    wanted = solvent.casefold()
    for row in payload.get("candidate_solvents") or []:
        if str(row.get("solvent") or "").casefold() == wanted:
            return row
    return {}


def _regimes(row: dict) -> list[str]:
    hot = [item.get("miscibility_regime") for item in (row.get("contaminants") or [])]
    cold = [
        item.get("miscibility_regime")
        for item in (row.get("precipitation_regime_contaminants") or [])
    ]
    return [str(item) for item in hot + cold if item is not None]


def test_argument_junk_refuses_even_without_contaminants():
    payload = _plan(contaminant_mode="banana")
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_contaminant_mode"
    assert payload["error_code"] != "unsupported_contaminants"


def test_argument_strap_or_leaching_without_contaminants_is_a_new_code():
    strap = _plan(contaminant_mode="strap")
    leach = _plan(contaminant_mode="leaching")
    swing = _plan(contaminant_mode="swing")
    for payload in (strap, leach, swing):
        assert payload["success"] is False
        assert payload["error_code"] == "contaminant_mode_without_contaminants"
        assert payload["error_code"] != "unsupported_contaminants"
        assert payload["error_code"] != "invalid_contaminant_mode"
        assert payload.get("contaminant_mode_origin") == "argument"


def test_argument_off_without_contaminants_is_ident_to_unset():
    unset = _plan()
    off = _plan(contaminant_mode="off")
    assert unset["success"] is True
    assert off == unset
    assert "wash" not in (off.get("best_sequence") or [])


def test_session_mode_without_contaminants_stays_a_silent_noop():
    def run(mode: str | None) -> dict:
        record = new_session()
        if mode is not None:
            record["contaminant_mode"] = {"mode": mode}
        with bind_tool_session(record):
            return _plan()

    unset = run(None)
    assert unset["success"] is True
    assert unset == run("off") == run("leaching") == run("strap") == run("banana")


def test_helper_distinguishes_argument_from_session():
    mode, requested, origin = separation._planner_contaminant_mode(None, "strap")
    assert origin == "argument"
    assert mode == "without_contaminants"
    assert requested == []
    junk, _requested, junk_origin = separation._planner_contaminant_mode(
        None, "banana",
    )
    assert junk == "invalid"
    assert junk_origin == "argument"
    record = new_session()
    record["contaminant_mode"] = {"mode": "strap"}
    with bind_tool_session(record):
        session_mode, session_requested, session_origin = (
            separation._planner_contaminant_mode(None, None)
        )
    assert session_origin == "session"
    assert session_mode is None
    assert session_requested == []


def test_argument_junk_with_dehp_still_invalid():
    payload = _plan(contaminants=_DEHP, contaminant_mode="banana")
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_contaminant_mode"


def test_accept_test_2_unchanged():
    fail = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", ["PET", "EVOH"], [_DEHP], solvents=["toluene"],
    )
    passed = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", ["EVOH"], [_DEHP], solvents=["toluene"],
    )
    fail_row = _candidate(fail, "toluene")
    pass_row = _candidate(passed, "toluene")
    assert fail["success"] is True
    assert fail_row.get("passes") is False
    assert passed["success"] is True
    assert pass_row.get("passes") is True
    assert _regimes(pass_row) == ["rt", "rt"]
