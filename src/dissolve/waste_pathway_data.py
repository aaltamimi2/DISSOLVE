"""Candidate pathways for waste_pathway_milp: thermodynamic screen, then process simulation, then the optimizer.

For a feed this module asks the thermodynamic screen which solvents and temperatures take each polymer out of the
stream that is left at that point of a wash sequence (a polymer is washed out only when every polymer still in the
stream stays below the retention limit), costs each wash with the TEA/LCA engine, and turns the results into
WashStage objects. The wash's cost depends on what was removed before it (the plant is sized for the stream it
receives), so stages are keyed by the set of polymers already removed.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from . import safety, tea, thermodynamics
from .waste_pathway_milp import Economics, Feed, Impacts, Pathway, Technology, WashStage

# Defaults of the published case study (Munoz-Briones et al.), kept as named values so a caller can override them.
DEFAULT_POLYMER_PRICES_USD_PER_T = {"LDPE": 1173.0, "HDPE": 1173.0, "PET": 1380.0, "NYLON6": 2800.0, "EVOH": 8100.0}
RECOVERY_YIELD = 0.97  # STRAP wash yield in the case study; recovered mass is feed x fraction x yield
RENEWABLE_ENERGY_SHARE = 0.12  # the case study's washes carry exactly 12% renewable energy (grid mix)
MIN_SOLUBILITY_PCT = 10.0  # target must reach this wt% in the solvent
MAX_RETAINED_PCT = 3.0  # every polymer left in the stream must stay at or below this wt%
MAX_PRECIPITATED_PCT = 1.0  # after cooling the target may stay at most this wt% in solution: DISSOLVE's precipitation level
DISSOLUTION_LOADING_PCT = 3.0  # the process model dissolves polymer at this wt% in solution (dissolution_capacity default)
BOILING_MARGIN_C = 10.0  # a wash runs at least this far below the solvent's boiling point, as the safety card flags

# Downstream technologies. Coefficients are per tonne of residual. They are recovered from the published case study's
# notebook outputs (GWP and energy for all six; renewable energy for all six) and from the optimization asset
# (waste and water); the workbook they came from no longer holds cached values, so water, waste and the opex of
# incineration and gasification are partial and noted as such.
DOWNSTREAM_NOTES = (
    "Downstream coefficients are recovered from the published case study's notebook outputs (GWP, energy, "
    "renewable energy) and from DISSOLVE's earlier optimization asset (waste, water, landfill and pyrolysis opex). "
    "The original workbook sheet is not available, so incineration and gasification opex are zero and pyrolysis "
    "earns no product revenue."
)
DOWNSTREAM_TECHNOLOGIES: tuple[Technology, ...] = (
    Technology("lf", "Landfill", distance_mile=9.2, opex_usd_per_t=7.83599,
               impacts_per_t=Impacts(energy_mj=453.74493269714236, disposal_t=1.0, gwp_t=0.0864563579568406)),
    Technology("we", "Incineration with energy recovery", distance_mile=151.0, revenue_usd_per_t=110.0,
               impacts_per_t=Impacts(energy_mj=418.193, renewable_mj=30.24, water_withdrawn_m3=1.81,
                                     water_recycled_m3=1.81, waste_kg=0.1, gwp_t=2.45971)),
    Technology("py", "Pyrolysis", distance_mile=1034.0, opex_usd_per_t=153.4067,
               impacts_per_t=Impacts(energy_mj=3054.52, renewable_mj=601.8, waste_kg=250.0, gwp_t=0.682266),
               excluded_polymers=frozenset({"PET"})),  # PET degrades the process, as in the case study
    Technology("gas_er", "Gasification for energy recovery", distance_mile=0.0, revenue_usd_per_t=147.98008228,
               impacts_per_t=Impacts(energy_mj=4976.45, renewable_mj=601.8, waste_kg=387.663, gwp_t=1.07),
               capex_ref_usd_yr=1.680302711e6, capex_ref_mass_t=2400.0),
    Technology("gas_h2", "Gasification for hydrogen", distance_mile=2036.0, revenue_usd_per_t=110.0,
               impacts_per_t=Impacts(energy_mj=8290.26, renewable_mj=741.8, waste_kg=820.915, gwp_t=5.42838)),
    Technology("gas_h2cc", "Gasification for hydrogen with carbon capture", distance_mile=2036.0,
               revenue_usd_per_t=110.0,
               impacts_per_t=Impacts(energy_mj=9665.56, renewable_mj=906.9, waste_kg=1046.405, gwp_t=2.55583)),
)

# Distance (miles) of each downstream technology in the case study's location scenarios.
LOCATION_SCENARIOS: dict[str, dict[str, float]] = {
    "A": {"lf": 9.2, "we": 151.0, "py": 1034.0, "gas_er": 0.0, "gas_h2": 2036.0, "gas_h2cc": 2036.0},
    "B": {"lf": 9.2, "we": 151.0, "py": 76.1, "gas_er": 0.0, "gas_h2": 76.1, "gas_h2cc": 76.1},
}


_STAGE_ROWS: dict[str, dict[str, Any]] = {}  # costed washes of this process, by their full process configuration


def clear_stage_rows() -> None:
    _STAGE_ROWS.clear()


# Names a user (or the agent) can give for each downstream site, mapped to the technology keys.
DISTANCE_KEYS = {
    "landfill": "lf", "incineration": "we", "incinerator": "we", "waste_to_energy": "we", "pyrolysis": "py",
    "gasification_energy": "gas_er", "gasification_for_energy": "gas_er", "gasification_hydrogen": "gas_h2",
    "gasification_for_hydrogen": "gas_h2", "hydrogen_gasification": "gas_h2",
    "gasification_hydrogen_ccs": "gas_h2cc", "gasification_hydrogen_cc": "gas_h2cc",
    "hydrogen_gasification_with_carbon_capture": "gas_h2cc",
    # the technology keys the tool reports, accepted back as they are
    "lf": "lf", "we": "we", "py": "py", "gas_er": "gas_er", "gas_h2": "gas_h2", "gas_h2cc": "gas_h2cc",
}


def describe_distances(distances: Mapping[str, float]) -> str:
    """The downstream distances in plain words, for an answer that should not name a scenario label."""
    names = {"lf": "landfill", "we": "incinerator", "py": "pyrolysis plant", "gas_er": "energy-recovery gasifier",
             "gas_h2": "hydrogen gasifier", "gas_h2cc": "hydrogen gasifier with carbon capture"}
    return "; ".join(f"{names[k]} {distances[k]:g} miles" for k in names if k in distances)


class StageUnavailable(Exception):
    """A wash could not be costed; the reason is stated, never guessed."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class StageSpec:
    """One wash to cost: which polymer leaves which stream, with which solvent at which temperature."""

    removed_before: frozenset[str]
    polymer: str
    solvent: str
    temperature_c: float
    mass_in_t: float
    target_mass_percent: float
    plant_capacity_t: float | None = None  # a shared plant is sized for this; the wash pays its throughput share
    thermo: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def share(self) -> float:
        """Fraction of the plant's annual cost and impacts this wash carries."""
        return 1.0 if not self.plant_capacity_t else self.mass_in_t / self.plant_capacity_t

    @property
    def key(self) -> tuple[frozenset[str], str, str]:
        return (self.removed_before, self.polymer, self.solvent)


@dataclass
class StageTable:
    stages: dict[tuple[frozenset[str], str, str], WashStage] = field(default_factory=dict)
    specs: dict[tuple[frozenset[str], str, str], StageSpec] = field(default_factory=dict)
    engine_modes: dict[tuple[frozenset[str], str, str], str] = field(default_factory=dict)  # live, cache or campaign
    match_status: dict[tuple[frozenset[str], str, str], str] = field(default_factory=dict)  # exact, surrogate, ...
    recovery_sources: dict[tuple[frozenset[str], str, str], str] = field(default_factory=dict)  # simulated or assumed
    indicators: dict[tuple[frozenset[str], str, str], dict[str, Any]] = field(default_factory=dict)  # per-kg solvent indicators
    screen_log: list[dict[str, Any]] = field(default_factory=list)  # what each screen did to the solvent pool
    session_reused: set = field(default_factory=set)  # washes whose row came from an earlier call in this session
    wall_seconds: dict[tuple[frozenset[str], str, str], float] = field(default_factory=dict)  # each wash's own run time
    pending: list[StageSpec] = field(default_factory=list)  # waiting for live-simulation confirmation
    failed: list[dict[str, Any]] = field(default_factory=list)
    no_option: list[dict[str, Any]] = field(default_factory=list)  # (stream, polymer) pairs with no admissible solvent


def admitted_solvents(*, in_scope_only: bool = True) -> list[str]:
    """Solvents the TEA/LCA engine can cost (a price record and a life-cycle record) that the active solvent scope allows.

    The session's solvent scope (all 990 grid solvents, or the pinned common set) decides which of them a screen may
    use; a solvent outside it is left out here and named by ``solvents_outside_scope`` rather than refused later.
    """
    factor_ids = {thermodynamics.resolve_solvent(name) for name in (tea.lca_factor_payload().get("solvents") or {})}
    active = thermodynamics.get_available_solvents() if in_scope_only else None
    names: list[str] = []
    for row in (tea.cache_payload().get("solvent_assumptions") or {}).get("records") or []:
        name = row.get("name_cosmobase") or row.get("name_biosteam")
        identity = thermodynamics.resolve_solvent(str(name)) if name else None
        if identity is None or row.get("price_usd_per_kg") is None or identity not in factor_ids:
            continue
        if active is not None and identity not in active:
            continue
        names.append(str(name))
    return sorted(set(names))


def solvents_outside_scope() -> list[str]:
    """Costable solvents the active solvent scope excludes."""
    return sorted(set(admitted_solvents(in_scope_only=False)) - set(admitted_solvents()))


def stream_after(feed: Feed, removed: frozenset[str]) -> tuple[float, dict[str, float]]:
    """Tonnes per year and polymer masses of the stream left after the polymers in ``removed`` are washed out."""
    masses = {p: feed.mass_t(p) for p in feed.fractions if p not in removed}
    return sum(masses.values()), masses


_MELTING_POINTS_FILE = Path(__file__).with_name("data") / "solvent_melting_points.json"


@lru_cache(maxsize=1)
def solvent_melting_points_c() -> dict[str, float]:
    """Melting point (C) of each costable solvent, keyed by its stored-grid identity.

    The values were read from PubChem (campaigns/waste-pathway-film-case/fetch_melting_points.py records how). A solvent absent
    from the table is unchecked, not liquid.
    """
    try:
        raw = json.loads(_MELTING_POINTS_FILE.read_text()).get("solvents", {})
    except (OSError, ValueError):
        return {}
    table: dict[str, float] = {}
    for name, record in raw.items():
        identity = thermodynamics.resolve_solvent(name)
        if identity is not None and record.get("melting_point_c") is not None:
            table[identity] = float(record["melting_point_c"])
    return table


@lru_cache(maxsize=1024)
def solvent_hazard(name: str, temperature_c: float) -> dict[str, Any]:
    """GHS signal word, pictograms, CHEM21 ranking and heating risk of a solvent at an operating temperature, from DISSOLVE's local
    safety store and its cached PubChem snapshot. An empty dict means no card could be read, not that the solvent is safe."""
    try:
        data = json.loads(safety.get_solvent_safety_card(
            solvent_name=name, operating_temp_c=float(temperature_c), include_pubchem=True)).get("data") or {}
    except Exception:  # a solvent the store does not know has no card
        return {}
    if not data.get("success"):
        return {}
    profile = data.get("safety_profile") or {}
    ghs = profile.get("ghs") or {}
    assessment = profile.get("process_temperature_assessment") or {}
    return {
        "ghs_signal_word": ghs.get("signal_word"), "pictograms": ghs.get("pictograms"),
        "chem21_ranking": profile.get("chem21_default_ranking"), "heating_risk": assessment.get("risk_level"),
        "heating_flags": assessment.get("flags"), "flash_point_c": (profile.get("chem21_inputs") or {}).get("flash_point_c"),
    }


@lru_cache(maxsize=None)
def solvent_lca_factor(name: str) -> dict[str, Any]:
    """Cradle-to-gate GWP per kg of solvent from the project's life-cycle record, and how good that record is.

    ``source_tier`` says whether the factor was curated for the solvent or is a class average. Empty when there is no record.
    """
    payload = tea.lca_factor_payload()
    fields = list(payload.get("solvent_entry_fields") or [])
    identity = thermodynamics.resolve_solvent(name)
    for key, entry in (payload.get("solvents") or {}).items():
        if thermodynamics.resolve_solvent(key) == identity:
            record = dict(zip(fields, entry)) if isinstance(entry, (list, tuple)) else dict(entry)
            return {"gwp_kg_co2e_per_kg_solvent": record.get("gwp_per_kg_solvent"), "source_tier": record.get("source_tier")}
    return {}


def wash_indicators(spec: StageSpec, row: Mapping[str, Any], stage: WashStage) -> dict[str, Any]:
    """Solvent-specific figures of one wash, per kg of the resin it recovers.

    The MICRON circularity index scales waste, emissions and energy by whole-pathway bounds, so it barely moves with the
    solvent. These figures are reported beside it so that solvents and resins can be compared on what differs between them.
    Solvent make-up is the simulation's purchased-solvent stream at the process model's solvent-loss setting.
    """
    resin_t = spec.mass_in_t * spec.target_mass_percent / 100.0 * float(stage.recovered_fraction or 0.0)
    makeup = row.get("solvent_makeup_kg_per_yr")
    assumption = tea._solvent_assumption(spec.solvent) or {}
    boiling = assumption.get("boiling_point_c")
    water = row.get("water_circulated_m3_per_yr")

    def per_t(value: float | None) -> float | None:
        return None if value is None or resin_t <= 0 else float(value) * spec.share / resin_t

    return {
        "energy_mj_per_kg_resin": row.get("total_energy_mj_per_kg"),
        "gwp_kg_co2e_per_kg_resin": row.get("gwp_kg_co2e_per_kg"),
        "wash_cost_usd_per_kg_resin": None if resin_t <= 0 else (stage.capex_usd_yr + stage.opex_usd_yr) / (resin_t * 1000.0),
        "solvent_makeup_kg_per_t_resin": per_t(makeup),
        "solvent_loss_pct_assumed": row.get("solvent_loss_pct"),
        "water_circulated_m3_per_t_resin": per_t(water),
        "solvent_price_usd_per_kg": assumption.get("price_usd_per_kg"),
        "boiling_point_c": boiling,
        "boiling_margin_c": None if boiling is None else float(boiling) - spec.temperature_c,
        **{f"solvent_{k}": v for k, v in solvent_lca_factor(spec.solvent).items()},
    }


def default_precipitation_temperature_c(energy_case: str = "C1") -> float:
    """The temperature the process model cools to when the caller does not set one."""
    return float(tea.first_run_sheet_defaults(energy_case=energy_case)["precipitation_temperature_c"])


def precipitated_pct(polymer: str, solvents: Sequence[str], temperature_c: float) -> dict[str, float]:
    """Solubility (wt%) of ``polymer`` in each solvent at ``temperature_c``, from the stored grid.

    A wash only recovers its polymer if the polymer falls out of solution when the process cools, so this is the wt% that
    stays dissolved at the precipitation temperature. A solvent with no value there is simply absent.
    """
    found: dict[str, float] = {}
    offset = 0
    while True:
        payload = json.loads(thermodynamics.solubility_query(
            polymers=[polymer], solvents=list(solvents), temperatures=[float(temperature_c)],
            top_k=50, offset=offset,
        ))
        data = payload.get("data") or {}
        if not data.get("success"):
            raise StageUnavailable("thermodynamic_screen_failed", str(data.get("error") or "the solubility query refused"))
        for row in data.get("results") or []:
            if row.get("solubility_pct") is not None:
                found[str(row.get("solvent_name"))] = float(row["solubility_pct"])
        if not data.get("has_more"):
            return found
        offset = int(data.get("next_offset") or offset + 50)


class _Options(list):
    """The wash options of one screen, with what the screen did to the solvent pool.

    ``pool`` solvents were looked at, ``passed_solubility`` cleared the dissolution tests (target solubility, retained polymers,
    boiling margin) at some grid temperature, and ``passed`` also cleared the cooling, freezing and hazard filters. The list holds
    the best ``max_options`` of those. ``screened`` is False for a stream with nothing to wash.
    """

    def __init__(self, items=(), *, screened: bool = False, pool: int = 0, passed_solubility: int = 0, passed: int = 0):
        super().__init__(items)
        self.screened, self.pool, self.passed_solubility, self.passed = screened, pool, passed_solubility, passed


def stage_options(
    feed: Feed, removed: frozenset[str], polymer: str, solvents: Sequence[str], *, max_options: int,
    temperature_min_c: float = 25.0, temperature_max_c: float = 160.0, temperature_step_c: float = 5.0,
    min_solubility_pct: float = MIN_SOLUBILITY_PCT, max_retained_pct: float = MAX_RETAINED_PCT,
    boiling_margin_c: float = BOILING_MARGIN_C, precipitation_temperature_c: float | None = None,
    max_precipitated_pct: float = MAX_PRECIPITATED_PCT, avoid_danger_solvents: bool = False,
) -> _Options:
    """Solvents and temperatures that take ``polymer`` out of the stream left after ``removed``.

    The screen reports each solvent only at its single best-gap temperature, which can fall outside the retention
    limit although a lower temperature clears it, so every grid temperature is screened. A condition within
    ``boiling_margin_c`` of the solvent's boiling point is dropped. When ``precipitation_temperature_c`` is given the
    wash must be a dissolve-then-precipitate cycle: it dissolves above that temperature and the polymer stays at or
    below ``max_precipitated_pct`` wt% in solution once cooled to it; each solvent then keeps the lowest dissolution
    temperature that clears every test (the cheapest to heat). Solvents are ranked by the gap between the target and the
    most soluble retained polymer.
    """
    mass_in, masses = stream_after(feed, removed)
    if polymer not in masses or len(masses) < 2:
        return _Options()  # the last polymer is the residue, not a wash
    fractions = {p: m / mass_in for p, m in masses.items()}
    hits: dict[str, list[dict[str, Any]]] = {}
    temperature = float(temperature_min_c)
    while temperature <= float(temperature_max_c) + 1e-9:
        payload = json.loads(thermodynamics.screen_polymer_separation(
            feed_polymers=sorted(masses), target_polymers=[polymer], solvents=list(solvents),
            temperature_min_c=temperature, temperature_max_c=temperature, strict_maximum=False,
            ranking_mode="separation_gap", require_atmospheric=True, top_k=max(len(solvents), 10),
            min_solubility_pct=min_solubility_pct, max_retained_pct=max_retained_pct, feed_mass_fractions=fractions,
        ))
        screened = payload.get("data") or {}
        if not screened.get("success"):
            raise StageUnavailable("thermodynamic_screen_failed", str(screened.get("error") or "the screen refused"))
        for item in screened.get("ranked_candidates") or []:
            margin = item.get("boiling_point_margin_c")
            target, retained = item.get("target_solubility_pct"), item.get("max_off_target_solubility_pct")
            if target is None or retained is None or float(target) < min_solubility_pct or float(retained) > max_retained_pct:
                continue  # the screen's own retained_intact flag uses the engine's 1 wt% level, not the caller's limit
            if margin is not None and float(margin) < boiling_margin_c:
                continue
            hits.setdefault(str(item["solvent"]), []).append({**item, "temperature_c": float(item["temperature_c"])})
        temperature += float(temperature_step_c)
    left_dissolved: dict[str, float] = {}
    if precipitation_temperature_c is not None and hits:
        left_dissolved = precipitated_pct(polymer, sorted(hits), precipitation_temperature_c)
        if not left_dissolved:
            raise StageUnavailable("precipitation_temperature_not_on_grid",
                                   f"no stored solubility for {polymer} at {precipitation_temperature_c:g} C")
    best: dict[str, dict[str, Any]] = {}
    melting = solvent_melting_points_c()
    for name, found in hits.items():  # temperatures ascend within each solvent's hits
        if precipitation_temperature_c is not None:
            if left_dissolved.get(name) is None or left_dissolved[name] > max_precipitated_pct:
                continue  # the polymer would stay in solution on cooling: the wash would recover little of it
            mp = melting.get(thermodynamics.resolve_solvent(name) or "")
            if mp is not None and mp > precipitation_temperature_c:
                continue  # the solvent would freeze when the process cools, so it cannot carry a precipitate out
            found = [it for it in found if it["temperature_c"] > precipitation_temperature_c]
        if found and avoid_danger_solvents:
            if (solvent_hazard(name, found[0]["temperature_c"]) or {}).get("ghs_signal_word") == "Danger":
                continue  # the caller asked to keep out solvents with the GHS Danger signal word
        if found:
            best[name] = {**found[0], "precipitated_pct": left_dissolved.get(name),
                          "melting_point_c": melting.get(thermodynamics.resolve_solvent(name) or "")}
    ranked = sorted(best.values(), key=lambda it: (-float(it.get("selectivity_pct") or 0.0), it["temperature_c"], it["solvent"]))
    target_t = masses[polymer]
    solvent_t = target_t * (100.0 / DISSOLUTION_LOADING_PCT - 1.0)

    def pickup_pct_of_product(retained_pct: float | None) -> float | None:
        """Most retained polymer the solvent could take up, as a share of the product: the solvent saturated at the
        retained solubility, limited by the retained polymer present. An upper bound, not a prediction."""
        if retained_pct is None or retained_pct >= 100.0:
            return None
        return 100.0 * min(mass_in - target_t, retained_pct / (100.0 - retained_pct) * solvent_t) / target_t

    return _Options([
        StageSpec(
            removed_before=removed, polymer=polymer, solvent=str(item["solvent"]), temperature_c=item["temperature_c"],
            mass_in_t=mass_in, target_mass_percent=100.0 * masses[polymer] / mass_in,
            thermo={
                "target_solubility_pct": item.get("target_solubility_pct"),
                "max_retained_pct": item.get("max_off_target_solubility_pct"),
                "limiting_retained_polymer": item.get("limiting_off_target_polymer"),
                "selectivity_pct": item.get("selectivity_pct"),
                "boiling_point_margin_c": item.get("boiling_point_margin_c"),
                "target_left_dissolved_pct_after_cooling": item.get("precipitated_pct"),
                "solvent_melting_point_c": item.get("melting_point_c"),
                "retained_pickup_upper_bound_pct_of_product": pickup_pct_of_product(
                    item.get("max_off_target_solubility_pct")),
            },
        )
        for item in ranked[:max_options]
    ], screened=True, pool=len(solvents), passed_solubility=len(hits), passed=len(best))


def process_config(spec: StageSpec, *, energy_case: str = "C1", overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The public process_config for one wash. Anything not set here keeps its first-run default."""
    assumption = tea._solvent_assumption(spec.solvent)
    if assumption is None or assumption.get("price_usd_per_kg") is None:
        raise StageUnavailable("solvent_price_unavailable", f"{spec.solvent} has no price record")
    config = {
        key: value for key, value in tea.first_run_sheet_defaults(energy_case=energy_case).items()
        if key in tea.public_process_field_names(energy_case=energy_case)
    }
    config.update({
        "target_polymer": spec.polymer, "solvent": spec.solvent, "energy_case": energy_case,
        "target_mass_percent": round(spec.target_mass_percent, 6),
        "processing_capacity_mt_per_yr": round(spec.plant_capacity_t or spec.mass_in_t, 6),
        "dissolution_temperature_c": spec.temperature_c,
        "solvent_price_usd_per_kg": float(assumption["price_usd_per_kg"]),
    })
    config.update(dict(overrides or {}))
    return config


def run_stage(spec: StageSpec, *, confirm_live_tea: bool, energy_case: str = "C1",
              overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Costed row for one wash, from the stored records or, once confirmed, a live simulation."""
    return run_process_config(process_config(spec, energy_case=energy_case, overrides=overrides),
                              confirm_live_tea=confirm_live_tea)


def run_process_config(config: Mapping[str, Any], *, confirm_live_tea: bool) -> dict[str, Any]:
    """Costed row for any complete process_config: this session's earlier run of it, a stored record, or (once confirmed) a
    live simulation. Raises StageUnavailable when a live simulation would be needed and has not been confirmed."""
    key = json.dumps(config, sort_keys=True, default=str)
    if key in _STAGE_ROWS:  # the same wash asked again in this session: no second simulation
        return {**_STAGE_ROWS[key], "session_reused": True}
    started = time.monotonic()
    payload = json.loads(tea.evaluate_process(
        mode="evaluate", process_config=config, confirm_live_tea=bool(confirm_live_tea),
    ))
    elapsed = time.monotonic() - started
    data = payload.get("data") or {}
    rows = data.get("comparison_rows") or []
    if rows and rows[0].get("success"):
        row = {**rows[0], "engine_mode": rows[0].get("engine_mode") or data.get("engine_mode"),
               "cache_match_status": rows[0].get("cache_match_status") or data.get("cache_match_status"),
               "wall_seconds": elapsed}  # kept with the row, so a reused wash still reports what its simulation took
        _STAGE_ROWS[key] = row
        return dict(row)
    error = str(data.get("error_type") or data.get("error") or (rows[0].get("error_type") if rows else "") or "failed")
    if "confirmation_required" in error or "live_tea_cost_confirmation" in json.dumps(payload)[:4000]:
        raise StageUnavailable("live_tea_cost_confirmation_required", str(config.get("solvent")))
    raise StageUnavailable(error, str(data.get("message") or (rows[0].get("error") if rows else "") or error))


def capital_recovery_factor(interest: float, years: float) -> float:
    if years <= 0:
        raise ValueError("years must be positive")
    if interest <= 0:
        return 1.0 / years
    growth = (1.0 + interest) ** years
    return interest * growth / (growth - 1.0)


def wash_stage_from_row(
    spec: StageSpec, row: Mapping[str, Any], *, recovery_yield: float = RECOVERY_YIELD,
    renewable_share: float = RENEWABLE_ENERGY_SHARE,
) -> tuple[WashStage, str]:
    """Annual capex, opex and impacts of one simulated wash, and where its recovery came from.

    Capital is the total capital investment annualized at the plant's own finance rate and term. The recovered share of the
    target polymer is the simulation's own annual resin output over the target mass the plant receives (``simulated``);
    when the row lacks it or it is implausible, the assumed yield is used (``assumed``). Per-kilogram energy and
    emissions scale by the resin this wash recovers from the feed's stream.
    """
    plant_target_kg = (spec.plant_capacity_t or spec.mass_in_t) * spec.target_mass_percent / 100.0 * 1000.0
    simulated_kg = row.get("waste_diverted_kg_per_yr")  # the process model's annual resin output
    fraction = float(simulated_kg) / plant_target_kg if simulated_kg and plant_target_kg > 0 else None
    if fraction is not None and 0.5 <= fraction <= 1.0:
        source = "simulated"
    else:
        fraction, source = recovery_yield, "assumed"
    product_kg = spec.mass_in_t * spec.target_mass_percent / 100.0 * fraction * 1000.0
    energy = float(row["total_energy_mj_per_kg"]) * product_kg
    waste_kg = float(row.get("waste_generated_kg_per_yr") or 0.0)
    interest = row.get("finance_interest")
    years = row.get("finance_years")
    crf = capital_recovery_factor(0.08 if interest is None else float(interest), 10.0 if years is None else float(years))
    share = spec.share  # annual plant totals (cost, water, waste) are split by throughput; per-kilogram results are not
    return WashStage(
        polymer=spec.polymer, solvent=spec.solvent,
        capex_usd_yr=float(row["tci_usd"]) * crf * share, opex_usd_yr=float(row["aoc_usd_per_yr"]) * share,
        temperature_c=spec.temperature_c, recovered_fraction=fraction,
        impacts=Impacts(
            energy_mj=energy, renewable_mj=renewable_share * energy,
            water_withdrawn_m3=float(row.get("water_consumed_m3_per_yr") or 0.0) * share,
            water_recycled_m3=float(row.get("water_circulated_m3_per_yr") or 0.0) * share,
            waste_kg=waste_kg * share, disposal_t=waste_kg * share / 1000.0,
            gwp_t=float(row["gwp_kg_co2e_per_kg"]) * product_kg / 1000.0,
        ),
    ), source


def build_stage_table(
    feed: Feed, *, max_washes: int = 2, max_options: int = 3, solvents: Sequence[str] | None = None,
    confirm_live_tea: bool = False, energy_case: str = "C1", overrides: Mapping[str, Any] | None = None,
    recovery_yield: float = RECOVERY_YIELD, renewable_share: float = RENEWABLE_ENERGY_SHARE,
    on_stage: Callable[[StageSpec], None] | None = None, plant_capacity_t: float | None = None,
    max_precipitated_pct: float = MAX_PRECIPITATED_PCT, avoid_danger_solvents: bool = False, **thermo_limits: float,
) -> StageTable:
    """Screen and cost every wash a sequence of up to ``max_washes`` could contain."""
    if plant_capacity_t is not None and plant_capacity_t < feed.tonnes_per_year:
        raise ValueError(f"a shared plant must be at least as large as the feed it washes ({feed.tonnes_per_year:g} t/yr)")
    pool = list(solvents) if solvents is not None else admitted_solvents()
    cooling_c = float((overrides or {}).get("precipitation_temperature_c", default_precipitation_temperature_c(energy_case)))
    table = StageTable()

    def explore(removed: frozenset[str]) -> None:
        if len(removed) >= max_washes:
            return
        _, masses = stream_after(feed, removed)
        for polymer in sorted(masses):
            try:
                options = stage_options(feed, removed, polymer, pool, max_options=max_options,
                                        precipitation_temperature_c=cooling_c, max_precipitated_pct=max_precipitated_pct,
                                        avoid_danger_solvents=avoid_danger_solvents, **thermo_limits)
                specs = [replace(spec, plant_capacity_t=plant_capacity_t) for spec in options]
                if options.screened:
                    table.screen_log.append({"removed_before": sorted(removed), "polymer": polymer, "pool": options.pool,
                                             "passed_solubility": options.passed_solubility, "passed": options.passed,
                                             "costed": len(specs)})
            except StageUnavailable as error:
                table.failed.append({"polymer": polymer, "removed_before": sorted(removed), "reason": error.kind,
                                     "detail": error.detail})
                continue
            if not specs:
                if len(masses) > 1:
                    table.no_option.append({"removed_before": sorted(removed), "polymer": polymer})
                continue
            costed = False
            for spec in specs:
                if spec.key in table.stages:
                    costed = True
                    continue
                try:
                    if on_stage:
                        on_stage(spec)
                    row = run_stage(spec, confirm_live_tea=confirm_live_tea, energy_case=energy_case, overrides=overrides)
                except StageUnavailable as error:
                    if error.kind == "live_tea_cost_confirmation_required":
                        if spec.key not in {item.key for item in table.pending}:
                            table.pending.append(spec)
                        costed = True  # keep exploring so the caller sees every simulation the tree needs
                    else:
                        table.failed.append({"polymer": spec.polymer, "solvent": spec.solvent,
                                             "removed_before": sorted(removed), "reason": error.kind, "detail": error.detail})
                    continue
                table.stages[spec.key], table.recovery_sources[spec.key] = wash_stage_from_row(
                    spec, row, recovery_yield=recovery_yield, renewable_share=renewable_share)
                table.specs[spec.key] = spec
                if row.get("session_reused"):
                    table.session_reused.add(spec.key)
                table.wall_seconds[spec.key] = float(row.get("wall_seconds") or 0.0)
                table.indicators[spec.key] = wash_indicators(spec, row, table.stages[spec.key])
                table.engine_modes[spec.key] = str(row.get("engine_mode") or "unknown")
                table.match_status[spec.key] = str(row.get("cache_match_status") or "unknown")
                costed = True
            if costed:
                explore(removed | {polymer})

    explore(frozenset())
    return table


def pathways_from_table(table: StageTable, feed: Feed, max_washes: int = 2) -> list[Pathway]:
    """Every wash sequence whose stages are all costed, including the empty sequence."""
    found = [Pathway()]

    def extend(removed: frozenset[str], stages: tuple[WashStage, ...]) -> None:
        if len(removed) >= max_washes:
            return
        for (before, polymer, _solvent), stage in table.stages.items():
            if before != removed:
                continue
            path = stages + (stage,)
            found.append(Pathway(path))
            extend(removed | {polymer}, path)

    extend(frozenset(), ())
    return found


def default_economics(feed: Feed, prices: Mapping[str, float] | None = None, *, recovery_yield: float = RECOVERY_YIELD,
                      strap_distance_mile: float = 0.0, residual_resale_fraction: float = 1.0) -> Economics:
    """Prices for the feed's polymers: the case study's defaults, overridden by the caller's (keyed by any polymer label)."""
    merged = {p: DEFAULT_POLYMER_PRICES_USD_PER_T.get(p, 0.0) for p in feed.fractions}
    for name, value in (prices or {}).items():
        identity = thermodynamics.resolve_polymer(str(name)) or str(name)
        if identity not in feed.fractions:
            raise ValueError(f"a price was given for {name!r}, which is not a polymer in the feed")
        if float(value) < 0:
            raise ValueError("polymer prices cannot be negative")
        merged[identity] = float(value)
    if not 0.0 <= float(residual_resale_fraction) <= 1.0:
        raise ValueError("residual_resale_price_fraction must be between 0 and 1")
    return Economics(polymer_price_usd_per_t=merged, recovery_yield=recovery_yield, strap_distance_mile=strap_distance_mile,
                     residual_resale_fraction=float(residual_resale_fraction))


def polymers_without_price(feed: Feed, economics: Economics) -> list[str]:
    """Feed polymers the economics values at nothing: recovering one earns no revenue."""
    return [p for p in feed.fractions if economics.polymer_price_usd_per_t.get(p, 0.0) <= 0.0]
