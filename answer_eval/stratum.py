"""§4.9 closed-book stratum label."""

from __future__ import annotations

from typing import Any


def stratum_label(runs: list[Any]) -> dict[str, Any]:
    labels = []
    flags = {}
    for run in runs:
        if isinstance(run, str):
            if run == "operational":
                labels.append("none")
                flags["operational"] = True
            else:
                labels.append(run)
            continue
        if isinstance(run, dict):
            lab = run.get("label") or run.get("closed_book")
            if run.get("operational") or lab == "operational":
                labels.append("none")
                flags["operational"] = True
            else:
                labels.append(lab or "none")
            continue
        labels.append(str(run))
    if any(x == "NA" for x in labels) or runs == []:
        return {"closed_book": "NA"}
    if all(x == "full" for x in labels):
        result = "full"
    elif all(x == "none" for x in labels):
        result = "none"
    else:
        result = "partial"
    out: dict[str, Any] = {"closed_book": result, "paired_run_index": 1, "no_mode_used": True}
    if flags:
        out["flags"] = flags
    counts = {x: labels.count(x) for x in ("full", "partial", "none")}
    majority = max(("full", "partial", "none"), key=lambda k: (counts[k], k))
    out["majority_would_give"] = majority
    out["discriminates_majority"] = majority != result
    return out
