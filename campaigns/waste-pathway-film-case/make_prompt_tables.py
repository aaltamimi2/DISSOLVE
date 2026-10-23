"""Markdown tables of the agent sessions (the supplementary prompts and tool traces), built from the saved transcripts.

    python3 campaigns/waste-pathway-film-case/make_prompt_tables.py [agent_transcript.json ...]

With no arguments it reads agent_transcript.json (Gemini Flash) and agent_transcript_vertex-gemini-pro.json (Gemini Pro) beside
this file and prints one table per model. Every number comes from what the agent's tool calls returned.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TECH = {"lf": "landfill", "we": "incineration", "gas_er": "gasification (energy)", "gas_h2": "gasification (H2)",
        "gas_h2cc": "gasification (H2+CC)", "py": "pyrolysis", "resale": "sold as resin"}


def describe_call(call: dict) -> str:
    """One pathway-tool call: what was asked and the pathway it returned."""
    args, result = call["args"], call.get("result") or {}
    asked = []
    if args.get("pareto"):
        asked.append(f"pareto={args['pareto']}")
    elif args.get("objective"):
        asked.append(args["objective"])
    if args.get("shared_plant_capacity_mt_per_yr"):
        asked.append(f"shared {args['shared_plant_capacity_mt_per_yr']:,.0f} t/yr")
    prices = args.get("polymer_prices_usd_per_t") or {}
    if prices:
        asked.append("prices " + ", ".join(f"{k} ${v:,.0f}" for k, v in sorted(prices.items())))
    if args.get("downstream_distances_mile"):
        asked.append("own distances")
    if args.get("draw_plot"):
        asked.append("draw_plot")
    if args.get("draw_summary"):
        asked.append("draw_summary")
    head = ", ".join(asked) or "call"
    if result.get("error_code"):
        return f"{head} → refused: {result['error_code']}"
    if result.get("pathway") is None:
        return f"{head} → front returned"
    return (f"{head} → {result['pathway']} (${result['profit_usd_per_yr'] / 1e6:.2f}M, "
            f"{result['emissions_t_co2e_per_yr']:,.0f} t, {result['circularity_index']:.2f})")


def table(transcript: dict) -> str:
    lines = ["| Id | Question | Tool calls | Calls to the pathway tool and the pathway returned | Time (s) |", "|---|---|---|---|---|"]
    for turn in transcript["turns"]:
        calls = turn["tool_calls"]
        pathway_calls = [c for c in calls if c["tool"] == "optimize_waste_pathway"]
        others = sorted({c["tool"] for c in calls if c["tool"] != "optimize_waste_pathway"})
        cell = "<br>".join(describe_call(c) for c in pathway_calls) or ("no tool call" if not calls else "")
        if others:
            cell = f"(also {', '.join(others)})<br>{cell}"
        question = turn["question"].replace("|", "/")
        lines.append(f"| {turn['id']} | {question} | {len(calls)} | {cell} | {turn['seconds']:.0f} |")
    return "\n".join(lines)


def main(paths: list[str]) -> None:
    targets = [Path(p) for p in paths] or [HERE / "agent_transcript.json", HERE / "agent_transcript_vertex-gemini-pro.json"]
    for path in targets:
        transcript = json.loads(path.read_text())
        print(f"**{transcript['model']}** ({path.name})\n")
        print(table(transcript))
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
