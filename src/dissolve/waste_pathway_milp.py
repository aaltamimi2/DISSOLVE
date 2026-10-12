"""Waste-management pathway selection for a mixed plastic feed: profit, emissions and MICRON circularity.

A pathway is an ordered sequence of solvent washes (each removes one polymer, the rest moves on) followed by one
downstream technology for whatever is left: landfill, incineration, pyrolysis or a gasification route. The washes'
costs and impacts come from process simulation (BioSTEAM), and a wash's cost depends on what was removed before it,
so the candidate pathways are evaluated one by one and handed to this module as data.

The optimizer is then an exact selection MILP over those candidates: one binary per candidate, one candidate chosen,
every objective and indicator a precomputed coefficient. Each candidate's MICRON indicators are evaluated from its
own totals, so the model has no big-M constraints and no bilinear terms. Brute-force enumeration over the same
candidates is the reference, and every MILP answer is checked against it.

Circularity follows Munoz-Briones et al., "Decision support for circular economy waste management of mixed plastic
streams": five category scores weighted equally, each a function of the pathway's annual totals against an upper
bound. Sign conventions: profit is maximized; emissions (kg CO2-eq per year basis, tonnes CO2-eq) are minimized;
circularity (0 to 1) is maximized.
"""

from __future__ import annotations

import itertools
import math
import shutil
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

# MICRON category weights and the sub-weights inside the energy, water and waste categories.
CATEGORY_WEIGHTS = {"energy": 0.2, "ghg": 0.2, "water": 0.2, "waste": 0.2, "substitutability": 0.2}
ENERGY_SUBWEIGHTS = (0.6, 0.4)  # energy consumed, renewable share
HALF_AND_HALF = (0.5, 0.5)  # water withdrawn / recycled, waste generated / diverted
SUBSTITUTABILITY_NORM_USD = 1.61e7  # the published case study's highest sales; the default when no bounds are derived
DISPOSAL_ZERO_T = 1e-3  # waste to disposal at or below this counts as none
GASIFICATION_CAPEX_EXPONENT = 0.6  # the six-tenths rule; the published gasification capex values follow it exactly

OBJECTIVES = {"max_profit": ("profit", True), "min_emissions": ("emissions", False), "max_circularity": ("circularity", True)}
METRICS = ("profit", "emissions", "circularity")


@dataclass(frozen=True)
class Impacts:
    """Annual totals of the quantities the circularity indicators read."""

    energy_mj: float = 0.0
    renewable_mj: float = 0.0
    direct_ghg_t: float = 0.0
    indirect_ghg_t: float = 0.0
    water_withdrawn_m3: float = 0.0
    water_recycled_m3: float = 0.0
    waste_kg: float = 0.0
    disposal_t: float = 0.0
    gwp_t: float = 0.0  # tonnes CO2-eq per year; the emissions objective sums this
    ghg_circ_t: float | None = None  # set when impacts are added: the sum of each part's own circularity GHG

    @property
    def ghg_t(self) -> float:
        """Emissions the circularity GHG category reads.

        Each wash or technology contributes its direct plus indirect emissions, or its GWP when it does not split
        them. The choice is made part by part, so a pathway never counts one part's GWP and ignores another's.
        """
        if self.ghg_circ_t is not None:
            return self.ghg_circ_t
        split = self.direct_ghg_t + self.indirect_ghg_t
        return split if split > 0.0 else self.gwp_t

    def __add__(self, other: "Impacts") -> "Impacts":
        total = Impacts(*(a + b for a, b in zip(self._values(), other._values())))
        return replace(total, ghg_circ_t=self.ghg_t + other.ghg_t)

    def scaled(self, factor: float) -> "Impacts":
        return replace(Impacts(*(value * factor for value in self._values())), ghg_circ_t=self.ghg_t * factor)

    def _values(self) -> tuple[float, ...]:
        return (
            self.energy_mj, self.renewable_mj, self.direct_ghg_t, self.indirect_ghg_t, self.water_withdrawn_m3,
            self.water_recycled_m3, self.waste_kg, self.disposal_t, self.gwp_t,
        )


@dataclass(frozen=True)
class WashStage:
    """One solvent wash, as simulated in the pathway it belongs to. Money is per year; mass is tonnes per year."""

    polymer: str
    solvent: str
    capex_usd_yr: float
    opex_usd_yr: float
    impacts: Impacts = field(default_factory=Impacts)
    temperature_c: float | None = None
    recovered_fraction: float | None = None  # share of the target polymer the simulation recovers; None: the economics' yield


@dataclass(frozen=True)
class Technology:
    """A downstream technology for the residual stream. Coefficients are per tonne of residual."""

    key: str
    label: str
    distance_mile: float = 0.0
    opex_usd_per_t: float = 0.0
    revenue_usd_per_t: float = 0.0
    impacts_per_t: Impacts = field(default_factory=Impacts)
    capex_ref_usd_yr: float = 0.0  # capex at capex_ref_mass_t of residual; scales by the six-tenths rule
    capex_ref_mass_t: float = 0.0
    excluded_polymers: frozenset[str] = frozenset()  # polymers the technology cannot take, such as PET in pyrolysis

    def capex_usd_yr(self, residual_t: float) -> float:
        if self.capex_ref_usd_yr <= 0.0 or self.capex_ref_mass_t <= 0.0 or residual_t <= 0.0:
            return 0.0
        return self.capex_ref_usd_yr * (residual_t / self.capex_ref_mass_t) ** GASIFICATION_CAPEX_EXPONENT


@dataclass(frozen=True)
class Feed:
    tonnes_per_year: float
    fractions: Mapping[str, float]  # polymer -> mass fraction; the fractions sum to one

    def __post_init__(self) -> None:
        total = sum(self.fractions.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"feed fractions sum to {total}, not 1")
        if self.tonnes_per_year <= 0:
            raise ValueError("feed tonnes_per_year must be positive")

    def mass_t(self, polymer: str) -> float:
        return self.tonnes_per_year * self.fractions.get(polymer, 0.0)


@dataclass(frozen=True)
class Economics:
    """Prices and rates shared by every candidate."""

    polymer_price_usd_per_t: Mapping[str, float]  # recovered-resin value; a polymer without a price earns nothing
    recovery_yield: float = 0.97
    transport_fixed_usd_per_t: float = 3.01
    transport_usd_per_t_mile: float = 0.07
    strap_distance_mile: float = 0.0  # wash plant to feed source
    # The polymer a wash fails to recover stays in the waste stream and goes downstream with the residual. The published
    # model drops it instead (its residual subtracts the whole removed mass), which leaves part of the feed unaccounted.
    yield_loss_to_residual: bool = True
    # What a clean residual sells for, as a share of the recovered-resin price. The solid left after a wash has not been through the
    # precipitation and drying a recovered resin has, so a buyer may pay less; 1.0 is the recovered-resin price.
    residual_resale_fraction: float = 1.0


@dataclass(frozen=True)
class CircularityBounds:
    """What the category scores are measured against.

    A total at or above its upper bound scores zero. Substitutability follows the published rule: the highest sales
    observed across the pathways score one, the lowest score zero, and sales in between score linearly.
    """

    energy_mj: float
    ghg_t: float
    water_m3: float
    waste_kg: float
    sales_low_usd: float = 0.0
    sales_high_usd: float = SUBSTITUTABILITY_NORM_USD


@dataclass(frozen=True)
class Pathway:
    """An ordered wash sequence. The empty sequence sends the whole feed to the downstream technology."""

    stages: tuple[WashStage, ...] = ()

    @property
    def label(self) -> str:
        return " > ".join(f"{s.polymer}/{s.solvent}" for s in self.stages) or "no wash"


@dataclass(frozen=True)
class Candidate:
    pathway: Pathway
    technology: Technology
    profit_usd: float
    emissions_t: float
    sales_usd: float
    capex_usd: float
    opex_usd: float
    transport_usd: float
    residual_t: float
    totals: Impacts
    scores: Mapping[str, float]
    circularity: float

    @property
    def label(self) -> str:
        return f"{self.pathway.label} + {self.technology.key}"

    def metric(self, name: str) -> float:
        return {"profit": self.profit_usd, "emissions": self.emissions_t, "circularity": self.circularity}[name]


def _unit(value: float) -> float:
    """A category score is held to 0 to 1: a total past its bound scores zero, and a negative total (an avoided burden)
    cannot score above one."""
    return min(1.0, max(0.0, value))


def _interpolate(value: float, low: float, high: float) -> float:
    """Zero at ``low``, one at ``high``, linear between, and held to that range."""
    if high <= low:
        return 0.0
    return min(1.0, max(0.0, (value - low) / (high - low)))


def category_scores(totals: Impacts, sales_usd: float, feed_t: float, bounds: CircularityBounds) -> dict[str, float]:
    """The five MICRON category scores for one pathway's annual totals."""
    consumed = _unit(1.0 - totals.energy_mj / bounds.energy_mj)
    renewable = 1.0 if totals.energy_mj <= 0.0 else min(1.0, totals.renewable_mj / totals.energy_mj)
    ghg = _unit(1.0 - totals.ghg_t / bounds.ghg_t)
    withdrawn = _unit(1.0 - totals.water_withdrawn_m3 / bounds.water_m3)
    recycled = 1.0 if totals.water_withdrawn_m3 <= 0.0 else min(1.0, totals.water_recycled_m3 / totals.water_withdrawn_m3)
    generated = _unit(1.0 - totals.waste_kg / bounds.waste_kg)
    diverted = 1.0 if totals.disposal_t <= DISPOSAL_ZERO_T else _unit(1.0 - totals.disposal_t / feed_t)
    return {
        "energy": ENERGY_SUBWEIGHTS[0] * consumed + ENERGY_SUBWEIGHTS[1] * renewable,
        "ghg": ghg,
        "water": HALF_AND_HALF[0] * withdrawn + HALF_AND_HALF[1] * recycled,
        "waste": HALF_AND_HALF[0] * generated + HALF_AND_HALF[1] * diverted,
        "substitutability": _interpolate(sales_usd, bounds.sales_low_usd, bounds.sales_high_usd),
    }


def evaluate_candidate(
    pathway: Pathway, technology: Technology, feed: Feed, economics: Economics, bounds: CircularityBounds,
    weights: Mapping[str, float] = CATEGORY_WEIGHTS,
) -> Candidate:
    """Profit, emissions and circularity of one pathway followed by one downstream technology."""
    seen: set[str] = set()
    mass_in = feed.tonnes_per_year
    sales = capex = opex = 0.0
    transport = 0.0
    lost = 0.0
    totals = Impacts()
    haul = economics.strap_distance_mile * economics.transport_usd_per_t_mile + economics.transport_fixed_usd_per_t
    for stage in pathway.stages:
        if stage.polymer in seen:
            raise ValueError(f"{stage.polymer} is removed twice in {pathway.label}")
        if stage.polymer not in feed.fractions:
            raise ValueError(f"{stage.polymer} is not in the feed")
        seen.add(stage.polymer)
        removed = feed.mass_t(stage.polymer)
        fraction = economics.recovery_yield if stage.recovered_fraction is None else stage.recovered_fraction
        recovered = removed * fraction
        lost += removed - recovered
        sales += recovered * economics.polymer_price_usd_per_t.get(stage.polymer, 0.0)
        capex += stage.capex_usd_yr
        opex += stage.opex_usd_yr
        transport += mass_in * haul
        totals = totals + stage.impacts
        mass_in -= removed
    residual = max(0.0, mass_in) + (lost if economics.yield_loss_to_residual else 0.0)
    tech = technology
    sales += residual * tech.revenue_usd_per_t
    capex += tech.capex_usd_yr(residual)
    opex += residual * tech.opex_usd_per_t
    transport += residual * (tech.distance_mile * economics.transport_usd_per_t_mile + economics.transport_fixed_usd_per_t)
    totals = totals + tech.impacts_per_t.scaled(residual)
    scores = category_scores(totals, sales, feed.tonnes_per_year, bounds)
    circularity = sum(weights[name] * scores[name] for name in weights)
    return Candidate(
        pathway=pathway, technology=tech, profit_usd=sales - capex - opex - transport, emissions_t=totals.gwp_t,
        sales_usd=sales, capex_usd=capex, opex_usd=opex, transport_usd=transport, residual_t=residual, totals=totals,
        scores=scores, circularity=circularity,
    )


def pathways_from_stage_table(
    stage_table: Mapping[tuple[int, str, str], WashStage], feed: Feed, max_washes: int | None = None,
) -> list[Pathway]:
    """Every wash sequence a stage table allows, including the empty one.

    The table is keyed by (wash number from 1, polymer, solvent) and holds a wash's cost and impacts when they do not
    depend on what was removed earlier. A polymer is removed at most once and only polymers in the feed are washed.
    Pathways whose costs do depend on the history are built directly as Pathway objects instead.
    """
    polymers = sorted({polymer for (_, polymer, _) in stage_table if polymer in feed.fractions})
    longest = min(max_washes if max_washes is not None else len(polymers), len(polymers))
    found = [Pathway()]
    for length in range(1, longest + 1):
        for order in itertools.permutations(polymers, length):
            options = [
                [stage_table[(wash, polymer, solvent)] for (w, p, solvent) in stage_table if w == wash and p == polymer]
                for wash, polymer in enumerate(order, start=1)
            ]
            if any(not choice for choice in options):
                continue
            found.extend(Pathway(stages=tuple(combo)) for combo in itertools.product(*options))
    return found


def full_recovery_sales_usd(feed: Feed, economics: Economics) -> float:
    """What the feed would earn if every polymer were recovered and sold: the substitutability reference.

    The published case study's constant, 1/1.61 x 10^7, is this value for its feed (0.97 x the resin value of all four
    polymers at $1,173, $1,380, $2,800 and $8,100 per tonne), so it generalizes to any feed without depending on which
    pathways happen to be enumerated. A polymer without a price adds nothing.
    """
    return sum(feed.mass_t(p) * economics.recovery_yield * economics.polymer_price_usd_per_t.get(p, 0.0)
               for p in feed.fractions)


def default_bounds(
    pathways: Iterable[Pathway], technologies: Iterable[Technology], feed: Feed, economics: Economics,
    factor: float = 1.5,
) -> CircularityBounds:
    """Bounds as the published case study set them.

    Energy, emissions, water and waste are bounded by 1.5 times their mean over all candidates; substitutability runs from
    zero sales to the sales of recovering every polymer in the feed (``full_recovery_sales_usd``).
    """
    probe = CircularityBounds(1.0, 1.0, 1.0, 1.0)
    technologies = list(technologies)
    totals = [
        evaluate_candidate(pathway, tech, feed, economics, probe).totals for pathway in pathways
        for tech in applicable_technologies(pathway, technologies, feed, economics)
    ]
    if not totals:
        raise ValueError("no candidates to derive bounds from")

    def bound(values: Sequence[float]) -> float:
        return max(factor * sum(values) / len(values), 1e-9)

    top = full_recovery_sales_usd(feed, economics)
    return CircularityBounds(
        energy_mj=bound([t.energy_mj for t in totals]),
        ghg_t=bound([t.ghg_t for t in totals]),
        water_m3=bound([t.water_withdrawn_m3 for t in totals]),
        waste_kg=bound([t.waste_kg for t in totals]),
        sales_low_usd=0.0,
        sales_high_usd=top if top > 0 else SUBSTITUTABILITY_NORM_USD,
    )


def residual_polymers(pathway: Pathway, feed: Feed) -> frozenset[str]:
    """Polymers still in the stream after the pathway's washes."""
    return frozenset(feed.fractions) - {stage.polymer for stage in pathway.stages}


def applicable_technologies(
    pathway: Pathway, technologies: Sequence[Technology], feed: Feed, economics: Economics,
) -> list[Technology]:
    """The downstream technologies a pathway's residual can go to.

    A technology that cannot take one of the residual's polymers (pyrolysis and PET) is left out. After at least one wash, a
    residual that is a single priced polymer can also be sold as that resin: the washed solid is valued at the recovered-resin
    price with no further processing cost, which the published model, whose residual was always a mixture, did not need. An
    unwashed feed is never resold, because untreated scrap is not clean resin.
    """
    out = [t for t in technologies if not (t.excluded_polymers & residual_polymers(pathway, feed))]
    left = residual_polymers(pathway, feed)
    if pathway.stages and len(left) == 1:
        polymer = next(iter(left))
        price = economics.polymer_price_usd_per_t.get(polymer, 0.0) * economics.residual_resale_fraction
        if price > 0:
            out.append(Technology("resale", f"sold as {polymer} resin", revenue_usd_per_t=price))
    return out


@dataclass
class PathwayProblem:
    feed: Feed
    economics: Economics
    bounds: CircularityBounds
    candidates: list[Candidate]

    @classmethod
    def build(
        cls, pathways: Sequence[Pathway], technologies: Sequence[Technology], feed: Feed, economics: Economics,
        bounds: CircularityBounds | None = None, weights: Mapping[str, float] = CATEGORY_WEIGHTS,
    ) -> "PathwayProblem":
        bounds = bounds or default_bounds(pathways, technologies, feed, economics)
        technologies = list(technologies)
        candidates = [
            evaluate_candidate(pathway, tech, feed, economics, bounds, weights)
            for pathway in pathways for tech in applicable_technologies(pathway, technologies, feed, economics)
        ]
        return cls(feed=feed, economics=economics, bounds=bounds, candidates=candidates)


def _better(a: float, b: float, maximize: bool) -> bool:
    return a > b if maximize else a < b


# When two pathways tie on the objective, the next metrics decide, in this order, so a tie never hides a better pathway:
# the profit objective prefers lower emissions then higher circularity, the emissions objective prefers more profit
# then higher circularity, and the circularity objective prefers more profit then lower emissions.
_TIE_BREAK = {
    "profit": (("emissions", False), ("circularity", True)),
    "emissions": (("profit", True), ("circularity", True)),
    "circularity": (("profit", True), ("emissions", False)),
}


def _rank_key(cand: Candidate, metric: str, maximize: bool) -> tuple[float, ...]:
    """Sort key where larger is better: the objective first, then the tie-break metrics."""
    sign = 1.0 if maximize else -1.0
    key = [sign * cand.metric(metric)]
    key += [(1.0 if up else -1.0) * cand.metric(m) for m, up in _TIE_BREAK[metric]]
    return tuple(key)


def enumerate_optimum(
    candidates: Sequence[Candidate], objective: str, limits: Mapping[str, tuple[str, float]] | None = None,
) -> Candidate | None:
    """Reference answer: scan every candidate. limits maps a metric to ("<=" or ">=", bound).

    Candidates within a relative 1e-9 of the best objective value tie, and the tie-break metrics choose among them.
    """
    metric, maximize = OBJECTIVES[objective]
    allowed = [c for c in candidates if _within(c, limits)]
    if not allowed:
        return None
    best = max(c.metric(metric) if maximize else -c.metric(metric) for c in allowed)
    tied = [c for c in allowed
            if math.isclose(c.metric(metric), best if maximize else -best, rel_tol=1e-9, abs_tol=1e-6)]
    return max(tied, key=lambda c: _rank_key(c, metric, maximize))


def _within(cand: Candidate, limits: Mapping[str, tuple[str, float]] | None) -> bool:
    for metric, (sense, bound) in (limits or {}).items():
        value = cand.metric(metric)
        if sense == "<=" and value > bound + 1e-9 * max(1.0, abs(bound)):
            return False
        if sense == ">=" and value < bound - 1e-9 * max(1.0, abs(bound)):
            return False
    return True


def solver_name() -> str | None:
    """The MILP solver on this machine, or None when only enumeration is available."""
    try:
        import pyomo.environ as pyo  # noqa: F401
    except ImportError:
        return None
    for name in ("scip", "highs", "cbc", "glpk"):
        if shutil.which(name):
            return name
    return None


def solve_milp(
    candidates: Sequence[Candidate], objective: str, limits: Mapping[str, tuple[str, float]] | None = None,
    solver: str | None = None,
) -> tuple[Candidate | None, str]:
    """Select one candidate by MILP. Returns (candidate, solver used); the solver is "enumeration" without Pyomo."""
    name = solver or solver_name()
    if name is None:
        return enumerate_optimum(candidates, objective, limits), "enumeration"
    import pyomo.environ as pyo

    metric, maximize = OBJECTIVES[objective]
    # Objectives are scaled to order one so the solver's tolerances mean the same thing for dollars and tonnes.
    scale = {m: max(1.0, max(abs(c.metric(m)) for c in candidates)) for m in METRICS}
    model = pyo.ConcreteModel()
    model.pick = pyo.Var(range(len(candidates)), domain=pyo.Binary)
    model.one = pyo.Constraint(expr=sum(model.pick[i] for i in model.pick) == 1)
    model.limits = pyo.ConstraintList()
    for m, (sense, bound) in (limits or {}).items():
        lhs = sum(candidates[i].metric(m) / scale[m] * model.pick[i] for i in model.pick)
        rhs = bound / scale[m]
        model.limits.add(lhs <= rhs + 1e-9 if sense == "<=" else lhs >= rhs - 1e-9)
    value = sum(candidates[i].metric(metric) / scale[metric] * model.pick[i] for i in model.pick)
    model.obj = pyo.Objective(expr=value, sense=pyo.maximize if maximize else pyo.minimize)
    result = pyo.SolverFactory(name).solve(model, tee=False)
    status = str(result.solver.termination_condition)
    if status not in ("optimal", "TerminationCondition.optimal"):
        return None, name
    chosen = [i for i in model.pick if pyo.value(model.pick[i]) > 0.5]
    return (candidates[chosen[0]] if len(chosen) == 1 else None), name


def optimize(
    problem: PathwayProblem, objective: str = "max_profit", limits: Mapping[str, tuple[str, float]] | None = None,
) -> dict[str, Any]:
    """Best pathway for one objective, with the MILP's answer checked against enumeration."""
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {sorted(OBJECTIVES)}")
    reference = enumerate_optimum(problem.candidates, objective, limits)
    chosen, used = solve_milp(problem.candidates, objective, limits)
    metric = OBJECTIVES[objective][0]
    agrees = (
        (chosen is None and reference is None)
        or (chosen is not None and reference is not None
            and math.isclose(chosen.metric(metric), reference.metric(metric), rel_tol=1e-6, abs_tol=1e-6))
    )
    return {
        "objective": objective, "solver": used, "agrees_with_enumeration": agrees,
        "candidate": reference,  # the tie-broken answer; the MILP is checked on the objective value alone
        "candidates_considered": len(problem.candidates),
    }


def pareto_front(candidates: Sequence[Candidate], x: str, y: str) -> list[Candidate]:
    """Non-dominated candidates for two metrics, ordered along x. Each metric keeps its own direction."""
    if x == y or x not in METRICS or y not in METRICS:
        raise ValueError(f"x and y must be two different metrics from {METRICS}")
    sign = {"profit": 1.0, "circularity": 1.0, "emissions": -1.0}  # larger is better after the sign
    keyed = sorted(candidates, key=lambda c: (-sign[x] * c.metric(x), -sign[y] * c.metric(y)))
    front: list[Candidate] = []
    best_y: float | None = None
    for cand in keyed:
        value = sign[y] * cand.metric(y)
        if best_y is None or value > best_y + 1e-9 * max(1.0, abs(best_y)):
            front.append(cand)
            best_y = value
    return sorted(front, key=lambda c: c.metric(x))


def epsilon_front(problem: PathwayProblem, primary: str, bounded: str, steps: int = 20) -> list[dict[str, Any]]:
    """Epsilon-constraint sweep: optimize the primary objective while the bounded metric is held to a moving limit.

    The bounded metric sweeps from its worst to its best value over the candidates. Repeated answers collapse to one.
    """
    if primary == bounded or primary not in METRICS or bounded not in METRICS:
        raise ValueError(f"primary and bounded must be two different metrics from {METRICS}")
    objective = {"profit": "max_profit", "emissions": "min_emissions", "circularity": "max_circularity"}[primary]
    values = [c.metric(bounded) for c in problem.candidates]
    sense = "<=" if bounded == "emissions" else ">="
    start, stop = (max(values), min(values)) if bounded == "emissions" else (min(values), max(values))
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k in range(max(2, steps)):
        epsilon = start + (stop - start) * k / (max(2, steps) - 1)
        answer = optimize(problem, objective, {bounded: (sense, epsilon)})
        cand = answer["candidate"]
        if cand is None or cand.label in seen:
            continue
        seen.add(cand.label)
        points.append({"epsilon": epsilon, "candidate": cand, "agrees_with_enumeration": answer["agrees_with_enumeration"]})
    return points


def with_location(technologies: Sequence[Technology], distances_mile: Mapping[str, float]) -> list[Technology]:
    """The same technologies with the distances of one location scenario; a key not named keeps its distance."""
    return [replace(t, distance_mile=float(distances_mile.get(t.key, t.distance_mile))) for t in technologies]
