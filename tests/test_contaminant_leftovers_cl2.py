"""CL-2: omit a wash that cannot run; publish positions_considered with reasons.

A failed wash is not a reason to throw away the polymer route. Pass-case
insert stays the b7a9ef8 insert. k+1 is not changed. No BioSTEAM.
"""
from __future__ import annotations

from pathlib import Path


from dissolve import contaminants, separation
from dissolve.contracts import parse_tool_result


_DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"
_TRIPLE = ["LDPE", "PET", "EVOH"]

_ROUTE = {
    "complete": True,
    "final_residue": "PP",
    "unresolved_polymers": [],
    "steps": [{
        "step_kind": "dissolution",
        "dissolved_polymer": "LDPE",
        "solvent": "toluene",
        "temperature_c": 105.0,
        "selectivity_pct": 80.0,
        "target_solubility_pct": 20.0,
        "off_target_solubilities_pct": {"PP": 0.5},
    }],
}


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _empty_screen(*_args, **_kwargs) -> dict:
    return {
        "success": True,
        "recommended_solvents": [],
        "candidate_solvents": [{
            "solvent": "toluene",
            "passes": False,
        }],
    }


def test_empty_recommended_omits_wash_and_publishes_reasons():
    original = contaminants.evaluate_contaminant_at_feed_state
    contaminants.evaluate_contaminant_at_feed_state = _empty_screen
    try:
        payload = separation._embed_leaching_route(
            _ROUTE,
            names=["LDPE", "PP"],
            supported=[_DEHP],
            solvents=["toluene"],
            temperature_max_c=None,
            strict_maximum=False,
            step_c=5.0,
        )
    finally:
        contaminants.evaluate_contaminant_at_feed_state = original
    assert isinstance(payload, dict)
    steps = list(payload.get("steps") or [])
    considered = list(payload.get("positions_considered") or [])
    assert all(item.get("step_kind") != "wash" for item in steps)
    assert "wash" not in (payload.get("sequence") or [])
    assert all(not str(item).startswith("wash") for item in (payload.get("sequence") or []))
    assert len(considered) == 2
    assert all(item.get("reason") for item in considered)
    assert all(item.get("passing_count") == 0 for item in considered)
    assert payload.get("chosen_wash_position") is None
    assert not any(item.get("caveat") for item in steps)


def test_pass_case_still_inserts_wash_at_chosen_position():
    payload = _data(separation.plan_multistage_separation(
        _TRIPLE, contaminants=_DEHP, contaminant_mode="leaching",
        top_k_routes=1, breadth=1,
    ))
    assert payload["success"] is True
    steps = list(payload.get("steps") or [])
    dissolutions = [item for item in steps if item.get("step_kind") == "dissolution"]
    washes = [item for item in steps if item.get("step_kind") == "wash"]
    considered = list(payload.get("positions_considered") or [])
    assert len(washes) == 1
    assert washes[0].get("passes") is True
    assert washes[0].get("path") == "leaching"
    assert len(considered) == len(dissolutions) + 1
    assert payload.get("chosen_wash_position") in {item["index"] for item in considered}
    assert all(item.get("reason") for item in considered)
    assert "wash" in (payload.get("best_sequence") or [])
