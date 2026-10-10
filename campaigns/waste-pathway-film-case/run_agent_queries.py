"""Ask the DISSOLVE agent the case-study questions in plain English and record what it called and answered.

    .venv/bin/python campaigns/waste-pathway-film-case/run_agent_queries.py [--model vertex]

The questions are one session, in a user's words (no study labels, no tool names; Q1b answers the agent's request for
permission to simulate): the agent keeps the conversation and simulated washes between them, so a follow-up that
changes only the downstream location or a price costs no new simulation. The tool trace (every tool the agent called,
its arguments, and the pathway it returned) and each answer go to agent_transcript.json beside this file (agent_transcript_<model>.json for any model but the default `vertex`).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from dissolve import agent
from dissolve.cli import MODELS, resolve_model

HERE = Path(__file__).resolve().parent

QUESTIONS = [
    ("Q1", "We make about 8,000 tonnes a year of post-industrial multilayer film scrap: 60% LDPE, 20% PET, 10% nylon 6 "
           "and 10% EVOH by mass. What is the best way to process it to make money, and how would the answer change if "
           "we cared most about circularity? We would build a solvent-based recycling plant just for this scrap."),
    ("Q1b", "Yes, go ahead and run whatever simulations you need."),
    ("Q2", "Show the trade-off between profit and emissions."),
    ("Q3", "What if we shared a 20,000 tonne per year recycling plant with other producers instead of building our own?"),
    ("Q4", "In that shared plant, would recovering the LDPE pay if we could sell it for 1,300, 1,500 or 1,800 dollars "
           "per tonne?"),
    ("Q5", "Our landfill is 9 miles away and the incinerator 150 miles, but there are pyrolysis and hydrogen "
           "gasification plants only 76 miles away, and an energy-recovery gasifier could sit on our site. Does the "
           "best pathway change?"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="vertex")
    args = parser.parse_args()
    alias, spec = resolve_model(args.model)
    out_name = "agent_transcript.json" if alias == "vertex-gemini-flash" else f"agent_transcript_{alias}.json"
    session: dict = {}
    messages: list = []
    transcript = {"model": spec.label, "model_id": spec.model, "turns": []}
    for tag, question in QUESTIONS:
        events: list = []
        started = time.monotonic()
        result = agent.run_turn(question, session=session, model=spec.model, messages=messages,
                                api_key_env=spec.env_var, api_base=spec.base_url, on_event=events.append)
        turn = {"id": tag, "question": question, "status": result.status, "seconds": round(time.monotonic() - started, 1),
                "tool_calls": [], "answer": result.answer}
        for event in events:
            call = {"tool": event.name, "args": event.args}
            data = (getattr(event, "result", None) or {}).get("data") or {}
            if event.name == "optimize_waste_pathway":
                selected = data.get("selected") or {}
                call["result"] = {
                    "success": data.get("success"), "error_code": data.get("error_code"),
                    "pathway": selected.get("pathway"), "profit_usd_per_yr": selected.get("profit_usd_per_yr"),
                    "emissions_t_co2e_per_yr": selected.get("emissions_t_co2e_per_yr"),
                    "circularity_index": selected.get("circularity_index"), "basis": data.get("basis"),
                    "pending_stages": len(data.get("pending_stages") or []),
                }
            turn["tool_calls"].append(call)
        transcript["turns"].append(turn)
        print(f"\n=== {tag} ({turn['seconds']} s, {result.status}) — tools: {[c['tool'] for c in turn['tool_calls']]}")
        print(result.answer[:1800])
        (HERE / out_name).write_text(json.dumps(transcript, indent=1, default=str))


if __name__ == "__main__":
    main()
