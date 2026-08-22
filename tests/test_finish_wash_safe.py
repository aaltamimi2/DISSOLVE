"""v3 §8 commit 2: finish() is wash-safe. Fixture only; no planner embed.

Omitted contaminants still emit no wash. No BioSTEAM.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import separation
from dissolve.contracts import parse_tool_result


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _dissolution(polymer: str, solvent: str, temperature_c: float, **extra) -> dict:
    return {
        "dissolved_polymer": polymer,
        "solvent": solvent,
        "temperature_c": temperature_c,
        "selectivity_pct": extra.get("selectivity_pct", 80.0),
        "target_solubility_pct": extra.get("target_solubility_pct", 20.0),
        "off_target_solubilities_pct": extra.get(
            "off_target_solubilities_pct", {"PET": 0.5},
        ),
    }


def _wash(solvent: str, temperature_c: float) -> dict:
    return {
        "step_kind": "wash",
        "path": "leaching",
        "solvent": solvent,
        "temperature_c": temperature_c,
        "contaminants_targeted": ["di-(2-ethylhexyl) phthalate (DEHP)"],
        "passes": True,
        "feed_state_at_step": {
            "polymers": ["LDPE", "PET", "EVOH"],
            "contaminants": ["di-(2-ethylhexyl) phthalate (DEHP)"],
            "inventory_model": "none",
        },
    }


def test_wash_bearing_fixture_does_not_keyerror_or_drop():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PET",
            "unresolved_polymers": [],
            "steps": [
                _wash("toluene", 25.0),
                _dissolution("LDPE", "cyclohexanol", 135.0),
                _dissolution("EVOH", "water", 80.0, selectivity_pct=40.0),
            ],
        },
        feed_polymers=["LDPE", "PET", "EVOH"],
    )
    assert finished["sequence"] == ["wash", "LDPE", "EVOH", "PET"]
    assert "wash" not in finished["solvent_mapping"]
    assert finished["solvent_mapping"] == {
        "LDPE": "cyclohexanol",
        "EVOH": "water",
    }
    assert finished["bottleneck_selectivity_pct"] == 40.0
    assert finished["bottleneck_target_solubility_pct"] == 20.0
    assert finished["peak_temperature_c"] == 135.0
    assert finished["steps"][0]["step_kind"] == "wash"
    assert finished["steps"][1]["step_kind"] == "dissolution"
    assert finished["steps"][2]["step_kind"] == "dissolution"


def test_two_washes_are_numbered_in_step_order():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PET",
            "steps": [
                _wash("toluene", 25.0),
                _dissolution("LDPE", "cyclohexanol", 135.0),
                _wash("acetone", 30.0),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["sequence"] == ["wash 1", "LDPE", "wash 2", "PET"]
    assert list(finished["solvent_mapping"]) == ["LDPE"]


def test_peak_temperature_includes_wash():
    finished = separation.finish_route(
        {
            "complete": False,
            "final_residue": None,
            "steps": [
                _wash("toluene", 160.0),
                _dissolution("LDPE", "cyclohexanol", 105.0),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["peak_temperature_c"] == 160.0
    assert finished["sequence"] == ["wash", "LDPE"]


def test_dissolution_only_route_keeps_polymer_sequence():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PP",
            "steps": [
                _dissolution("LDPE", "toluene", 105.0),
            ],
        },
        feed_polymers=["LDPE", "PP"],
    )
    assert finished["sequence"] == ["LDPE", "PP"]
    assert finished["solvent_mapping"] == {"LDPE": "toluene"}
    assert finished["steps"][0]["step_kind"] == "dissolution"


def _render_tokens(tokens: list[str], *, complete: bool) -> str:
    parts: list[str] = []
    last = len(tokens) - 1
    for index, token in enumerate(tokens):
        if str(token).startswith("wash"):
            parts.append(str(token))
        elif complete and index == last:
            parts.append(f"residual {token}")
        else:
            parts.append(f"dissolve {token}")
    return " → ".join(parts)


def test_tokens_render_the_spec_sentence():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PET",
            "steps": [
                _wash("toluene", 25.0),
                _dissolution("LDPE", "cyclohexanol", 135.0),
                _dissolution("EVOH", "water", 80.0),
            ],
        },
        feed_polymers=["LDPE", "PET", "EVOH"],
    )
    assert _render_tokens(finished["sequence"], complete=True) == (
        "wash → dissolve LDPE → dissolve EVOH → residual PET"
    )


def test_wash_without_temperature_does_not_keyerror():
    finished = separation.finish_route(
        {
            "complete": False,
            "final_residue": None,
            "steps": [
                {"step_kind": "wash", "solvent": "toluene"},
                _dissolution("LDPE", "toluene", 105.0, selectivity_pct=70.0),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["sequence"] == ["wash", "LDPE"]
    assert finished["peak_temperature_c"] == 105.0
    assert finished["bottleneck_selectivity_pct"] == 70.0


def test_wash_selectivity_does_not_enter_bottlenecks():
    finished = separation.finish_route(
        {
            "complete": False,
            "steps": [
                {
                    "step_kind": "wash",
                    "solvent": "toluene",
                    "temperature_c": 25.0,
                    "selectivity_pct": 1.0,
                    "target_solubility_pct": 1.0,
                    "off_target_solubilities_pct": {"PET": 99.0},
                },
                _dissolution(
                    "LDPE",
                    "toluene",
                    105.0,
                    selectivity_pct=80.0,
                    target_solubility_pct=20.0,
                    off_target_solubilities_pct={"PET": 0.5},
                ),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["bottleneck_selectivity_pct"] == 80.0
    assert finished["bottleneck_target_solubility_pct"] == 20.0
    assert finished["cumulative_off_target_burden_wt_pct_sum"] == 0.5
    assert "wash" not in finished["solvent_mapping"]


def test_planner_omitted_contaminants_still_emits_no_wash():
    payload = _data(separation.plan_multistage_separation(["LDPE", "PP"]))
    assert payload["success"] is True
    assert "contaminants" in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
    sequence = payload.get("best_sequence") or payload.get("sequence")
    assert sequence
    assert all(not str(item).startswith("wash") for item in sequence)
    mapping = payload.get("solvent_mapping") or {}
    assert "wash" not in mapping
    assert "positions_considered" not in payload
    assert all(
        item.get("path") is None and item.get("feed_state_at_step") is None
        for item in (payload.get("steps") or [])
        if isinstance(item, dict)
    )
