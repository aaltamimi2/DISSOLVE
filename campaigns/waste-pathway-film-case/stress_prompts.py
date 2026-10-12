"""Unscripted prompt test: questions a user could ask that the case study was not built around.

    .venv/bin/python campaigns/waste-pathway-film-case/stress_prompts.py [--model vertex-pro]

Every prompt is a fresh session. If the agent stops to ask permission to simulate, the next turn answers "Yes, go ahead and run
the simulations." The tool calls, the pathway returned and the answer go to stress_prompts_<model>.json for review. The prompts
include traps on purpose: no composition, units per day, an unsupported polymer, percentages that sum to 110, and questions that
should not use the pathway tool at all.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from dissolve import agent
from dissolve.cli import resolve_model

HERE = Path(__file__).resolve().parent

# (id, prompt, what a correct agent does)
PROMPTS = [
    ("P01_per_day", "I have about 3 tonnes a day of LDPE/EVOH laminate scrap, roughly 70/30. Is it worth recycling it with solvents or should I just burn it?",
     "converts 3 t/day to about 1,095 t/yr and states it; uses the pathway tool"),
    ("P02_no_composition", "What's the best way to handle a mixed film waste?",
     "asks for the composition and amount instead of inventing them; no pathway result"),
    ("P03_solvent_question", "Which solvent dissolves LDPE most cheaply?",
     "does not use the pathway tool (a screening or cost question)"),
    ("P04_unsupported_polymer", "We have 10,000 t/yr of 50% HDPE and 50% polypropylene. Maximize circularity.",
     "reports that PP cannot be simulated/washed here or that nothing separates HDPE from PP, instead of crashing or inventing"),
    ("P05_own_distance_generic_name", "We process 20,000 tonnes per year of film that is 80% PE, 10% EVOH and 10% nylon. We care most about low emissions, and our nearest landfill is 40 miles away.",
     "passes the landfill distance; says which PE grade it assumed"),
    ("P06_price", "Our PET/PE laminate is 50/50 and we have 4,000 t/yr. Would washing it pay if recycled PET sells for 1,500 dollars a tonne?",
     "uses the PET price; reports honestly if no wash is admissible"),
    ("P07_single_polymer", "Compare pyrolysis and incineration for 6,000 tonnes a year of polystyrene packaging.",
     "treats it as a single-polymer feed (nothing to separate) and reports downstream options; no invented washing"),
    ("P08_shared_plant_pareto", "Show me profit versus circularity for a 60/40 PE/EVOH film, 2,000 t/yr, if we share a 15,000 tonne per year recycling plant with others.",
     "shared capacity 15,000 and a circularity-profit front"),
    ("P09_tiny_scale", "I run a small recycler with 500 tonnes per year of film scrap, 90% LDPE and 10% EVOH. Does solvent washing make sense at that scale?",
     "answers from the tool at 500 t/yr and says what it found, including any scale caveat"),
    ("P10_vague", "How green is solvent-based recycling for multilayer packaging?",
     "does not invent a feed or numbers; asks, or answers generally without a pathway result"),
    ("P11_bad_percentages", "I have 8,000 tonnes of film: 60% LDPE and 50% EVOH. What should I do with it?",
     "notices the percentages sum to 110% and asks, instead of silently normalizing"),
    ("P12_solvent_restriction", "For 8,000 t/yr of 60% LDPE, 20% PET, 10% nylon 6 and 10% EVOH, what maximizes profit if we may only use toluene and ethylene glycol?",
     "passes the solvent restriction and answers within it"),
]
CONSENT = "Yes, go ahead and run the simulations."


def run_one(spec, prompt):
    session, messages = {}, []
    turns, calls = [], []

    def ask(text):
        events = []
        started = time.monotonic()
        result = agent.run_turn(text, session=session, model=spec.model, messages=messages, api_key_env=spec.env_var,
                                api_base=spec.base_url, on_event=events.append)
        for event in events:
            data = (getattr(event, "result", None) or {}).get("data") or {}
            call = {"tool": event.name, "args": {k: v for k, v in event.args.items() if k != "feed_mass_fractions"},
                    "feed": event.args.get("feed_mass_fractions")}
            if event.name == "optimize_waste_pathway":
                sel = data.get("selected") or {}
                call["result"] = {"success": data.get("success"), "error_code": data.get("error_code"), "error": str(data.get("error") or "")[:200],
                                  "pathway": sel.get("pathway"), "profit": sel.get("profit_usd_per_yr"),
                                  "no_admissible_wash": data.get("polymers_with_no_admissible_wash"),
                                  "failed_washes": [f"{f.get('polymer')}:{f.get('reason')}" for f in data.get("washes_that_failed") or []],
                                  "warnings": data.get("warnings")}
            calls.append(call)
        turns.append({"asked": text, "status": result.status, "seconds": round(time.monotonic() - started, 1), "answer": result.answer})
        return result, events

    result, events = ask(prompt)
    needs_consent = any(e.name == "optimize_waste_pathway" and (getattr(e, "result", None) or {}).get("refusal") == "live_tea_cost_confirmation_required"
                        for e in events) or "confirm" in result.answer.lower() and "simulat" in result.answer.lower()
    if needs_consent:
        ask(CONSENT)
    return {"turns": turns, "tool_calls": calls}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="vertex-pro")
    parser.add_argument("--only", default="", help="comma-separated prompt ids")
    args = parser.parse_args()
    alias, spec = resolve_model(args.model)
    wanted = {x for x in args.only.split(",") if x}
    out = {"model": spec.label, "prompts": {}}
    path = HERE / f"stress_prompts_{alias}.json"
    for pid, prompt, expectation in PROMPTS:
        if wanted and pid not in wanted:
            continue
        try:
            record = run_one(spec, prompt)
        except Exception as error:  # a crash is a finding
            record = {"crashed": f"{type(error).__name__}: {error}"}
        record.update({"prompt": prompt, "expected": expectation})
        out["prompts"][pid] = record
        tools = [c["tool"] for c in record.get("tool_calls", [])]
        status = record.get("crashed") or "/".join(t["status"] for t in record.get("turns", []))
        print(f"{pid:30s} {status:14s} tools={tools}", flush=True)
        path.write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
