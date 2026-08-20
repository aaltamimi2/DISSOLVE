"""The flat tool registry. Every tool, one list, no routing.

v11 put these behind ten specialists, each with its own prompt, policy, context
projector, plan validator and completion requirements — so reaching a tool meant
a routing decision, and a routing decision is a second thing that can be wrong.
Here they are flat. The model sees all of them and picks.

Nothing in this file decides anything. It imports functions and lists them. If
you find yourself adding a condition here, that is routing coming back.

PROVENANCE: every tool returns the v11 envelope from `contracts.py` —
`{"display": str, "data": {...}}` on success, with `success: false` and an
`error_code` on refusal. The `basis` hook the flat harness will want is not
uniformly present yet: some tools state their source inside `data`
(`source_table`, `cache_match_status`, `provenance`, `evidence_class`) and some
do not. That is deliberate for now — the tools are ported raw and unedited
beyond decoupling, so what they claim about their own sources is exactly what
v11 claimed. Normalising that into one declared `basis` enum is a later pass,
and doing it now would mean inventing provenance for tools that never stated it.
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple

from . import (
    analysis, contaminants, optimization, research, safety,
    separation, tea, tools,
)


class Tool(NamedTuple):
    name: str
    fn: Callable[..., str]
    engine: str
    summary: str


def _s(fn: Callable[..., Any]) -> str:
    doc = (fn.__doc__ or "").strip().splitlines()
    return doc[0] if doc else ""


def _t(fn: Callable[..., Any], engine: str) -> Tool:
    return Tool(fn.__name__, fn, engine, _s(fn))


REGISTRY: tuple[Tool, ...] = tuple([
    # --- thermodynamics: grid query and separation screens ---
    _t(tools.solubility_query, "thermodynamics"),
    _t(tools.screen_polymer_separation, "thermodynamics"),
    _t(tools.screen_pairwise_solubility_overlap, "thermodynamics"),
    # --- separation: routes, precipitation protocol, membership ---
    _t(separation.resolve_polymer_data_scope, "separation"),
    _t(separation.lookup_material_database_membership, "separation"),
    _t(separation.plan_multistage_separation, "separation"),
    _t(separation.screen_precipitation_order, "separation"),
    _t(separation.screen_cool_then_reheat_getter, "separation"),
    # --- safety ---
    _t(safety.get_solvent_safety_card, "safety"),
    _t(safety.compare_solvent_safety_at_conditions, "safety"),
    _t(safety.screen_green_solvent_candidates, "safety"),
    _t(safety.screen_route_solvent_substitutions, "safety"),
    # --- TEA / LCA ---
    _t(tea.evaluate_process, "tea"),
    _t(tea.evaluate_tea_lca_scenarios, "tea"),
    _t(tea.evaluate_stored_route_tea_lca, "tea"),
    _t(tea.analyze_tea_sensitivity, "tea"),
    _t(tea.rank_landscape, "tea"),
    # --- optimization ---
    _t(optimization.optimize_stored_route, "optimization"),
    _t(optimization.pareto_optimize_stored_route, "optimization"),
    # --- Hansen parameters, thermal properties, numeric analysis ---
    _t(analysis.lookup_hansen_parameters, "analysis"),
    _t(analysis.screen_hansen_compatibility, "analysis"),
    _t(analysis.lookup_glass_transition, "analysis"),
    _t(analysis.estimate_thermal_properties, "analysis"),
    _t(analysis.list_thermal_evidence, "analysis"),
    _t(analysis.analyze_numeric_samples, "analysis"),
    # --- contaminant removal ---
    _t(contaminants.screen_contaminant_leaching, "contaminants"),
    _t(contaminants.screen_contaminant_strap_removal, "contaminants"),
    _t(contaminants.compare_contaminant_removal_modes, "contaminants"),
    # --- retrieval-augmented literature ---
    _t(research.search_scholarly_literature, "research"),
    _t(research.search_patent_literature, "research"),
    _t(research.ingest_literature_documents, "research"),
    _t(research.search_literature_corpus, "research"),
    _t(research.inspect_literature_corpus, "research"),
    _t(research.ingest_literature_graph, "research"),
])

BY_NAME: dict[str, Tool] = {t.name: t for t in REGISTRY}


def call(name: str, /, **kwargs: Any) -> str:
    """Invoke a registered tool by name. Unknown name raises — it never guesses."""
    if name not in BY_NAME:
        raise KeyError(f"no such tool: {name!r}")
    return BY_NAME[name].fn(**kwargs)
