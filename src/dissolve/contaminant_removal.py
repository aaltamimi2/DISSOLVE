"""Contaminant removal for a chosen separation plan, with no setting to switch it on (owner, 2026-10-05).

When a separation question names contaminants, the planner's best route is assessed both ways at every stage: STRAP
(the stage's own dissolution, at its own temperature, keeps the contaminant in solution while the polymer precipitates
on cooling) and a wash (a leaching wash against every polymer still present at a position). One stated rule picks:
STRAP when every dissolution stage passes; otherwise the safest eligible wash; otherwise no removal, with reasons. A
route the user asks for is applied only if it passes; it is never swapped for the other. The separation itself is
never changed to make removal possible.

These are screening proxies (workbook miscibility and logD, stored-grid solubility), not removal efficiency, purity or
process-safety clearance. There is no contaminant inventory: nothing here knows which polymer carries a contaminant, so
a wash is chosen only where it reaches every product not already cleared, and the undissolved residue is cleared only
by a wash that reaches it."""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from . import contaminants as screens
from . import thermodynamics as thermo

RULE_ID = "contaminant-removal/1"
RULE_TEXT = (
    "STRAP when every dissolution stage of the chosen separation keeps every assessed contaminant in solution at the "
    "stage's own temperature and on cooling; otherwise the safest eligible wash, placed where it reaches every product "
    "not already cleared; otherwise no removal."
)
ROUTES = ("auto", "strap", "wash")
SUMMARY_BYTES = 3072  # the summary stays whole in the model's view of a paged plan
_LIST_CAP = 8


def _capped(items: Sequence[Any], cap: int = _LIST_CAP) -> list[Any]:
    return list(items[:cap])


def _plastchem_state(name: str) -> str:
    """Whether the PlastChem release can answer for a name the curated removal data lacks: available, not_computed,
    not_found, ambiguous, or unavailable (no release). A match is not partition data until the release computed it."""
    con = screens._plastchem()
    if con is None:
        return "unavailable"
    try:
        chosen, families, unknown, ambiguous = screens._resolve_plastchem(con, [name])
    except Exception:  # noqa: BLE001 - a lookup failure must not cost the separation plan
        return "unavailable"
    if ambiguous:
        return "ambiguous"
    if unknown and not chosen:
        return "not_found"
    if not chosen:
        return "not_computed" if families else "not_found"
    computed = con.execute(
        "SELECT count(*) FROM contaminants WHERE id IN (SELECT unnest(?)) AND computed", [list(chosen)],
    ).fetchone()[0]
    return "available" if computed else "not_computed"


def resolve_request(requested: Sequence[str]) -> tuple[list[str], dict[str, Any]]:
    """The curated contaminants a removal assessment can use, and what happened to every requested name. Unsupported
    names never stop the separation; they are reported with where else they can be looked up."""
    supported, unsupported, families, uncovered = screens._expand(list(requested))
    unsupported_rows = [
        {
            "name": name,
            "reason": "outside the curated removal data (PFAS and phthalates)",
            "plastchem": _plastchem_state(name),
        }
        for name in unsupported
    ]
    return supported, {
        "requested": list(requested),
        "supported": supported,
        "families": families,
        "unsupported": unsupported_rows,
        "uncovered_families": uncovered,
    }


def _wash_pool(supported: Sequence[str]) -> list[str]:
    """Curated wash solvents for these contaminants that the chat's solvent scope admits. An empty intersection stays
    empty; it never falls back to another list."""
    universe = {str(item).casefold() for item in thermo.active_solvent_universe()}
    chosen: list[str] = []
    for raw in screens._solvent_names(supported):
        resolved = thermo.resolve_solvent(raw) or raw
        if str(resolved).casefold() in universe or str(raw).casefold() in universe:
            chosen.append(str(raw))
    return list(dict.fromkeys(chosen))


def _dissolutions(route: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item) for item in route.get("steps") or []
        if item.get("step_kind") != "wash" and (item.get("step_kind") == "dissolution" or item.get("dissolved_polymer"))
    ]


def _clashes(dissolutions: Sequence[dict[str, Any]], position: int, solvent: str, temperature: float,
             step_c: float) -> bool:
    """A wash next to a dissolution in the same solvent must run within temperature_step_c of it."""
    for index in (position - 1, position):
        if 0 <= index < len(dissolutions):
            other = dissolutions[index]
            if screens._key(other.get("solvent")) == screens._key(solvent) and other.get("temperature_c") is not None:
                if abs(float(other["temperature_c"]) - float(temperature)) > float(step_c):
                    return True
    return False


def assess(
    route: dict[str, Any],
    *,
    feed: Sequence[str],
    supported: Sequence[str],
    request: dict[str, Any],
    requested_route: str,
    maximum: Optional[float],
    strict_maximum: bool,
    step_c: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The route's steps with removal applied (or unchanged), and the compact summary of both assessments."""
    dissolutions = _dissolutions(route)
    names = list(feed)
    dissolved = [str(step.get("dissolved_polymer") or step.get("polymer")) for step in dissolutions]

    stages = []
    for index, step in enumerate(dissolutions):
        present = [name for name in names if name not in dissolved[:index]]
        target = dissolved[index]
        others = [name for name in present if name != target]
        if step.get("solvent") is None or step.get("temperature_c") is None:
            stages.append({"stage": index + 1, "polymer": target, "solvent": step.get("solvent"),
                           "temperature_c": step.get("temperature_c"), "verdict": "not_checkable",
                           "reason": "stage_without_solvent_or_temperature", "precipitation_c": None})
            continue
        result = screens.strap_at_stage(target, str(step["solvent"]), others, supported, float(step["temperature_c"]))
        stages.append({"stage": index + 1, "polymer": target, "solvent": step["solvent"],
                       "temperature_c": float(step["temperature_c"]), "verdict": result["verdict"],
                       "reason": result["reason"], "precipitation_c": result.get("precipitation_c")})

    pool = _wash_pool(supported)
    candidates, not_checkable, failing = [], {}, 0
    for position in range(len(dissolutions) + 1):
        present = [name for name in names if name not in dissolved[:position]]
        if not present or not pool:
            continue
        screen = screens.evaluate_contaminant_at_feed_state(
            "leaching", present[0], present[1:], list(supported), solvents=pool,
            temperature_max_c=maximum, strict_maximum=strict_maximum,
        )
        for row in screen.get("candidate_solvents") or []:
            if row.get("verdict") == "not_checkable":
                not_checkable[row.get("reason") or "unknown"] = not_checkable.get(row.get("reason") or "unknown", 0) + 1
                continue
            if not row.get("passes"):
                failing += 1
                continue
            candidates.append({**row, "position": position, "polymers_present": present})

    covered = [all(stage["verdict"] == "pass" for stage in stages[:position]) for position in range(len(stages) + 1)]
    excluded = {"hazardous_or_unscored_band": 0, "prior_product_not_covered": 0, "adjacent_temperature_clash": 0}
    eligible = []
    for row in candidates:
        if not row.get("safety_eligible"):
            excluded["hazardous_or_unscored_band"] += 1
        elif not covered[row["position"]]:
            excluded["prior_product_not_covered"] += 1
        elif _clashes(dissolutions, row["position"], row["solvent"], row["operating_temperature_c"], step_c):
            excluded["adjacent_temperature_clash"] += 1
        else:
            eligible.append(row)
    eligible.sort(key=lambda row: screens.wash_order_key(row, row["position"]))
    wash = eligible[0] if eligible else None

    complete = bool(route.get("complete")) and bool(dissolutions)
    strap_passes = complete and all(stage["verdict"] == "pass" for stage in stages)
    if requested_route == "strap":
        applied = "strap" if strap_passes else "none"
    elif requested_route == "wash":
        applied = "wash" if wash is not None else "none"
    else:
        applied = "strap" if strap_passes else "wash" if wash is not None else "none"
    if applied != "none":
        status = "applied_screen_pass"
    elif requested_route != "auto":
        status = "forced_route_unavailable"
    elif not complete:
        status = "partial_plan"
    elif not candidates and all(stage["verdict"] == "not_checkable" for stage in stages):
        status = "not_checkable"
    else:
        status = "no_screen_pass"

    steps = [dict(item) for item in route.get("steps") or []]
    if applied == "strap":
        by_stage = iter(stages)
        steps = [
            {**item, "path": "strap", "contaminant_removal": "strap",
             "precipitation_temperature_c": next(by_stage)["precipitation_c"]}
            if item.get("step_kind") != "wash" and (item.get("step_kind") == "dissolution" or item.get("dissolved_polymer"))
            else item
            for item in steps
        ]
    elif applied == "wash":
        steps = [dict(item) for item in dissolutions]
        steps.insert(wash["position"], {
            "step_kind": "wash", "path": "leaching", "solvent": wash["solvent"],
            "temperature_c": wash["operating_temperature_c"], "contaminants_targeted": list(supported),
            "position_index": wash["position"], "polymers_present": wash["polymers_present"],
            "boiling_margin_c": wash.get("boiling_margin_c"), "chem21_band": wash.get("chem21_band"),
        })

    residue = route.get("final_residue")
    summary = _summary(
        request=request, requested_route=requested_route, applied=applied, status=status, stages=stages,
        strap_passes=strap_passes, wash=wash, eligible=eligible, candidates=candidates, excluded=excluded,
        not_checkable=not_checkable, failing=failing, pool=pool,
        residue_note=(
            "covered by the wash" if applied == "wash" and residue in (wash or {}).get("polymers_present", [])
            else "not assessed: there is no contaminant inventory or residue removal model"
        ),
    )
    return steps, summary


def unsupported_summary(request: dict[str, Any], *, requested_route: str) -> dict[str, Any]:
    """No named contaminant is in the curated removal data: the separation stands and says so."""
    return _summary(
        request=request, requested_route=requested_route, applied="none", status="unsupported_request", stages=[],
        strap_passes=False, wash=None, eligible=[], candidates=[], excluded={}, not_checkable={}, failing=0, pool=[],
        residue_note="not assessed: no requested contaminant is in the curated removal data",
    )


def _summary(*, request, requested_route, applied, status, stages, strap_passes, wash, eligible, candidates, excluded,
             not_checkable, failing, pool, residue_note) -> dict[str, Any]:
    unsupported = request.get("unsupported") or []
    supported = request.get("supported") or []
    summary = {
        "requested_route": requested_route,
        "selected_by": "rule" if requested_route == "auto" else "user",
        "applied_route": applied,
        "status": status,
        "rule": {"id": RULE_ID, "text": RULE_TEXT, "wash_margin_c": screens._WASH_MARGIN_C,
                 "auto_selectable_chem21_bands": sorted(screens.WASH_AUTO_BANDS)},
        "contaminants": {
            "assessed": _capped(supported), "assessed_total": len(supported),
            "unsupported": _capped(unsupported, 5), "unsupported_total": len(unsupported),
            "coverage": ("every requested contaminant was assessed" if not unsupported
                         else "only the supported subset was assessed"),
        },
        "strap": {"all_stages_pass": bool(strap_passes), "stages": stages},
        "wash": {
            "chosen": None if wash is None else {
                "solvent": wash["solvent"], "position": wash["position"],
                "temperature_c": wash["operating_temperature_c"], "boiling_margin_c": wash.get("boiling_margin_c"),
                "chem21_band": wash.get("chem21_band"), "chem21_max_subscore": wash.get("chem21_max_subscore"),
                "contaminant_logd_min": wash.get("contaminant_logd_min"),
            },
            "next_options": [
                {"solvent": row["solvent"], "position": row["position"], "chem21_band": row.get("chem21_band")}
                for row in eligible[1:4]
            ],
            "candidate_solvents": len(pool), "passing_pairs": len(candidates), "eligible_pairs": len(eligible),
            "failing_pairs": failing, "excluded": excluded, "not_checkable": not_checkable,
        },
        "final_residue": residue_note,
        "assessed_route": "the first-ranked route only; other ranked routes carry no contaminant assessment",
        "whole_feed_cleanup_validated": False,
        "basis": ("screening proxies (workbook miscibility and logD, stored-grid solubility), not removal efficiency, "
                  "purity or process-safety clearance"),
    }
    while len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) > SUMMARY_BYTES and summary["wash"]["next_options"]:
        summary["wash"]["next_options"].pop()
    return summary
