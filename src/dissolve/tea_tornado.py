"""tea_tornado: one-at-a-time sensitivity of a solvent wash's TEA/LCA results, drawn as a tornado plot.

For one polymer washed in one solvent, each chosen parameter is moved to a low and to a high value while the others stay at
the base, the process is simulated at every point (BioSTEAM, with the project's life-cycle factors), and the change in the
minimum selling price, global warming potential and the other TEA/LCA metrics is ranked by size. The agent or the user picks
the parameters and may give their ranges; otherwise a standard set is moved over default ranges that the result states.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import tea, tea_tornado_plot as plotting, thermodynamics, waste_pathway_data as data_layer
from .contracts import tool_error, tool_success
from .figure_support import _open_file, _results_dir, run_plot

SCHEMA = plotting.SCHEMA
_SECONDS_PER_LIVE_RUN = 12  # measured: a BioSTEAM wash simulation takes about 11 to 15 s

METRICS = {  # key in a costed row: (label, unit)
    "msp_usd_per_kg": ("Minimum selling price", "$/kg of recovered polymer"),
    "gwp_kg_co2e_per_kg": ("Global warming potential", "kg CO2e/kg of recovered polymer"),
    "tci_usd": ("Total capital investment", "$"),
    "aoc_usd_per_yr": ("Annual operating cost", "$/yr"),
    "total_energy_mj_per_kg": ("Energy", "MJ/kg of recovered polymer"),
}
DEFAULT_METRICS = ("msp_usd_per_kg", "gwp_kg_co2e_per_kg")


@dataclass(frozen=True)
class Parameter:
    label: str
    unit: str
    group: str  # process or economic
    rule: tuple  # ("rel", fraction) | ("abs", delta) | ("span", low, high): the default range when none is given


PARAMETERS: dict[str, Parameter] = {
    "solvent_price_usd_per_kg": Parameter("Solvent price", "$/kg", "economic", ("rel", 0.3)),
    "solvent_loss_pct": Parameter("Solvent loss per pass", "%", "process", ("span", 0.01, 1.0)),  # the process model's range
    "dissolution_capacity": Parameter("Polymer loading in solvent", "wt/vol %", "process", ("rel", 0.5)),
    "processing_capacity_mt_per_yr": Parameter("Plant capacity", "t/yr", "process", ("rel", 0.5)),
    "dissolution_temperature_c": Parameter("Dissolution temperature", "°C", "process", ("abs", 10.0)),
    "precipitation_temperature_c": Parameter("Precipitation temperature", "°C", "process", ("abs", 5.0)),
    "feedstock_distance_km": Parameter("Feedstock transport distance", "km", "process", ("span", 0.0, 500.0)),
    "centrifuged_plastic_solvent_content_pct": Parameter("Solvent left in centrifuged polymer", "wt%", "process", ("rel", 0.3)),
    "target_mass_percent": Parameter("Target polymer share of the feed", "wt%", "process", ("rel", 0.2)),
    "labor_cost_usd_per_employee_yr": Parameter("Labor cost", "$/employee/yr", "economic", ("rel", 0.2)),
    "irr": Parameter("Internal rate of return", "fraction", "economic", ("rel", 0.3)),
    "operating_days": Parameter("Operating days per year", "days", "economic", ("rel", 0.1)),
    "feedstock_price_usd_per_kg": Parameter("Feedstock price", "$/kg", "economic", ("span", 0.0, 0.1)),
    "natural_gas_price_usd_per_m3": Parameter("Natural gas price", "$/m3", "economic", ("rel", 0.3)),
    "maintenance": Parameter("Maintenance (share of capital)", "fraction", "economic", ("rel", 0.3)),
    "contingency": Parameter("Contingency (share of capital)", "fraction", "economic", ("rel", 0.25)),
    "income_tax": Parameter("Income tax rate", "fraction", "economic", ("abs", 0.05)),
}
DEFAULT_PARAMETERS = ("solvent_price_usd_per_kg", "solvent_loss_pct", "dissolution_capacity", "processing_capacity_mt_per_yr",
                      "dissolution_temperature_c", "precipitation_temperature_c", "labor_cost_usd_per_employee_yr", "irr")
_IDENTITY_FIELDS = {"target_polymer", "solvent", "energy_case"}


def _numeric_public_defaults(energy_case: str) -> dict[str, float]:
    defaults = tea.first_run_sheet_defaults(energy_case=energy_case)
    return {name: float(defaults[name]) for name in tea.public_process_field_names(energy_case=energy_case)
            if isinstance(defaults.get(name), (int, float)) and not isinstance(defaults.get(name), bool)}


def default_range(name: str, base: float, relative_range: float | None) -> tuple[float, float] | None:
    """Low and high values of a parameter: a fraction of the base when the caller set one, else the parameter's own default rule
    (None when no sensible default exists, so the caller must give a range)."""
    if relative_range is not None:
        return (base * (1.0 - relative_range), base * (1.0 + relative_range)) if base else None
    rule = PARAMETERS[name].rule if name in PARAMETERS else None
    if rule is None:
        return None
    if rule[0] == "rel":
        return (base * (1.0 - rule[1]), base * (1.0 + rule[1])) if base else None
    if rule[0] == "abs":
        return (base - rule[1], base + rule[1])
    return (min(rule[1], base), max(rule[2], base))


def _base_config(polymer: str, solvent: str, *, target_mass_percent: float, capacity: float, temperature: float,
                 price: float, energy_case: str, overrides: dict[str, float]) -> dict[str, Any]:
    spec = data_layer.StageSpec(frozenset(), polymer, solvent, float(temperature), float(capacity), float(target_mass_percent))
    # one wash plant of the given size and target share, with every other field at the project's first-run default
    config = data_layer.process_config(spec, energy_case=energy_case)
    config.update({"processing_capacity_mt_per_yr": float(capacity), "target_mass_percent": float(target_mass_percent),
                   "solvent_price_usd_per_kg": float(price)})
    config.update({k: float(v) for k, v in overrides.items()})
    return config


def _metric_values(row: dict[str, Any] | None, metrics: tuple[str, ...]) -> dict[str, float | None]:
    return {m: (None if not row or row.get(m) is None else float(row[m])) for m in metrics}


def _sensitivity_row(name: str, label: str, unit: str, group: str, base_value: float, low: float, high: float,
                     base: dict[str, float | None], at_low: dict[str, float | None] | None, at_high: dict[str, float | None] | None,
                     errors: dict[str, str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for m, base_m in base.items():
        lo = None if at_low is None else at_low.get(m)
        hi = None if at_high is None else at_high.get(m)
        reach = [v for v in (lo, hi) if v is not None]
        metrics[m] = {
            "base": base_m, "at_low": lo, "at_high": hi,
            "swing": (max(reach + [base_m]) - min(reach + [base_m])) if reach and base_m is not None else None,
            "swing_pct_of_base": ((max(reach + [base_m]) - min(reach + [base_m])) / abs(base_m) * 100.0
                                  if reach and base_m else None),
        }
    return {"parameter": name, "label": label, "unit": unit, "group": group, "base_value": base_value, "low_value": low,
            "high_value": high, "metrics": metrics, "failed_runs": errors}


def tea_tornado(
    polymer: str,
    solvent: str,
    parameters: Optional[list[str]] = None,
    ranges: Optional[dict[str, list[float]]] = None,
    relative_range: Optional[float] = None,
    metrics: Optional[list[str]] = None,
    target_mass_percent: Optional[float] = None,
    processing_capacity_mt_per_yr: Optional[float] = None,
    dissolution_temperature_c: Optional[float] = None,
    energy_case: str = "C1",
    process_overrides: Optional[dict[str, float]] = None,
    confirm_live_tea: bool = False,
    draw_plot: bool = False,
) -> str:
    """Rank which process and economic parameters move the TEA/LCA results of one solvent wash most, as a tornado plot. Use it when the user asks for a sensitivity or tornado analysis of the cost, capital, operating cost, energy or global warming potential of washing one polymer with one solvent, or which parameters matter most; for a mixed feed or a choice of pathway use optimize_waste_pathway instead, and for a single parameter swept over many values use analyze_tea_sensitivity. polymer and solvent name the wash (LDPE, HDPE, PET, PC or EVOH, and a solvent with a price and a life-cycle record); target_mass_percent (default 60 when omitted) and processing_capacity_mt_per_yr (default 20,000 when omitted) size the base plant and dissolution_temperature_c defaults to the solvent's recorded process temperature, so state the base you used. parameters lists what to vary: solvent_price_usd_per_kg, solvent_loss_pct, dissolution_capacity, processing_capacity_mt_per_yr, dissolution_temperature_c, precipitation_temperature_c, feedstock_distance_km, centrifuged_plastic_solvent_content_pct, target_mass_percent, labor_cost_usd_per_employee_yr, irr, operating_days, feedstock_price_usd_per_kg, natural_gas_price_usd_per_m3, maintenance, contingency or income_tax (omit it for the default set: solvent price, solvent loss, polymer loading, plant capacity, both temperatures, labor cost and irr), or any other numeric process field given a range. ranges maps a parameter to [low, high] in its own units and relative_range (for example 0.2) moves every parameter by plus and minus that fraction of its base; without either, each parameter takes the default range the result reports, so ask the user for ranges they care about. metrics picks what to rank: msp_usd_per_kg, gwp_kg_co2e_per_kg, tci_usd, aoc_usd_per_yr and total_energy_mj_per_kg (default the first two). process_overrides sets other base fields. Every parameter needs two simulations (about 12 seconds each, run only when confirm_live_tea is true; otherwise the tool says how many it needs and stops), and one parameter is moved at a time, so interactions are not shown. Set draw_plot to true to draw the tornado figure with a table of the values and open it in the user's image viewer (figure_file; if figure_note says it could not be drawn, say why). The results describe one simulated wash plant, gate to gate, not a pathway's profit and not a plant measurement.
    """
    tool = "tea_tornado"
    started = time.monotonic()
    try:
        polymer_id = thermodynamics.resolve_polymer(str(polymer))
    except Exception:
        polymer_id = None
    if polymer_id is None:
        return tool_error(tool, f"polymer {polymer!r} is not in the thermodynamic grid", error_code="invalid_input")
    assumption = tea._solvent_assumption(str(solvent))
    if assumption is None or assumption.get("price_usd_per_kg") is None:
        return tool_error(tool, f"{solvent!r} has no price and life-cycle record, so it cannot be costed", error_code="no_costable_solvent")
    solvent_name = str(solvent).strip()  # as the user wrote it; the process model resolves any common spelling
    chosen_metrics = tuple(metrics) if metrics else DEFAULT_METRICS
    bad_metrics = [m for m in chosen_metrics if m not in METRICS]
    if bad_metrics or len(set(chosen_metrics)) != len(chosen_metrics):
        return tool_error(tool, f"metrics must be distinct names from {sorted(METRICS)}; got {list(chosen_metrics)}",
                          error_code="invalid_input", supported_metrics=sorted(METRICS))
    temperature = dissolution_temperature_c if dissolution_temperature_c is not None else assumption.get("hot_process_temperature_c")
    if temperature is None:
        return tool_error(tool, f"{solvent_name} has no recorded process temperature: give dissolution_temperature_c",
                          error_code="invalid_input")
    share = 60.0 if target_mass_percent is None else target_mass_percent
    capacity = 20000.0 if processing_capacity_mt_per_yr is None else processing_capacity_mt_per_yr
    try:
        if not float(share) > 0 or float(share) > 100 or not float(capacity) > 0:
            raise ValueError
        if relative_range is not None and not 0 < float(relative_range) < 1:
            raise ValueError("relative_range must be between 0 and 1")
        base = _base_config(polymer_id, solvent_name, target_mass_percent=float(share),
                            capacity=float(capacity), temperature=float(temperature),
                            price=float(assumption["price_usd_per_kg"]), energy_case=energy_case,
                            overrides=dict(process_overrides or {}))
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error) or "target_mass_percent must be in (0, 100] and the capacity positive", error_code="invalid_input")
    numeric = _numeric_public_defaults(energy_case)
    names = list(parameters) if parameters else list(DEFAULT_PARAMETERS)
    if len(set(names)) != len(names):
        return tool_error(tool, "a parameter is listed twice", error_code="invalid_input")
    allowed = set(PARAMETERS) | set(numeric)
    unknown = [n for n in names if n not in allowed or n in _IDENTITY_FIELDS]
    if unknown:
        return tool_error(tool, f"unknown or non-numeric parameters: {unknown}", error_code="unsupported_parameter",
                          supported_parameters=sorted(allowed - _IDENTITY_FIELDS))
    if len(names) > 12:
        return tool_error(tool, "at most 12 parameters per tornado (each needs two simulations)", error_code="too_many_parameters")
    plan: list[tuple[str, float, float, float]] = []  # name, base, low, high
    for name in names:
        base_value = base.get(name, numeric.get(name))
        if base_value is None:
            return tool_error(tool, f"{name} has no base value: set it in process_overrides", error_code="invalid_input")
        explicit = (ranges or {}).get(name)
        if explicit is not None:
            if not isinstance(explicit, (list, tuple)) or len(explicit) != 2 or not all(isinstance(v, (int, float)) for v in explicit):
                return tool_error(tool, f"ranges[{name!r}] must be [low, high] in the parameter's own units", error_code="invalid_input")
            low, high = sorted(float(v) for v in explicit)
        else:
            found = default_range(name, float(base_value), relative_range)
            if found is None:
                return tool_error(tool, f"{name} needs a range (its base is {base_value}, so no default range applies): give ranges[{name!r}]",
                                  error_code="range_required")
            low, high = found
        if low == high or (low == float(base_value) and high == float(base_value)):
            return tool_error(tool, f"the range of {name} is empty", error_code="invalid_input")
        plan.append((name, float(base_value), float(low), float(high)))
    # every distinct simulation: the base plant and each parameter moved to its low and high value
    configs = {json.dumps(base, sort_keys=True, default=str): base}
    for name, base_value, low, high in plan:
        for value in (low, high):
            if value != base_value:
                cfg = {**base, name: value}
                configs[json.dumps(cfg, sort_keys=True, default=str)] = cfg
    rows: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    pending: list[tuple[str, dict[str, Any]]] = []
    for key, cfg in configs.items():
        try:
            rows[key] = data_layer.run_process_config(cfg, confirm_live_tea=False)
        except data_layer.StageUnavailable as error:
            if error.kind == "live_tea_cost_confirmation_required":
                pending.append((key, cfg))
            else:
                failures[key] = f"{error.kind}: {error.detail}"
    if pending and not confirm_live_tea:
        seconds = len(pending) * _SECONDS_PER_LIVE_RUN
        return tool_error(
            tool, f"this tornado needs {len(pending)} live BioSTEAM simulations (about {_SECONDS_PER_LIVE_RUN} s each, roughly "
            f"{seconds // 60} min {seconds % 60} s); say so and call again with confirm_live_tea=true once the user agrees",
            error_code="live_tea_cost_confirmation_required", simulations_needed=len(pending),
            parameters_requested=names, base={"polymer": polymer_id, "solvent": solvent_name})
    for key, cfg in pending:
        try:
            rows[key] = data_layer.run_process_config(cfg, confirm_live_tea=True)
        except data_layer.StageUnavailable as error:
            failures[key] = f"{error.kind}: {error.detail}"
    base_key = json.dumps(base, sort_keys=True, default=str)
    if base_key not in rows:
        return tool_error(tool, f"the base wash could not be simulated: {failures.get(base_key)}", error_code="base_run_failed",
                          failed_runs=list(failures.values()))
    base_values = _metric_values(rows[base_key], chosen_metrics)
    if any(v is None for v in base_values.values()):
        return tool_error(tool, "the base wash returned no value for some requested metric", error_code="base_run_failed",
                          base_metrics=base_values)
    sens_rows = []
    for name, base_value, low, high in plan:
        sides: dict[str, dict[str, float | None] | None] = {}
        errors: dict[str, str] = {}
        for side, value in (("low", low), ("high", high)):
            cfg = base if value == base_value else {**base, name: value}
            key = json.dumps(cfg, sort_keys=True, default=str)
            if key in rows:
                sides[side] = _metric_values(rows[key], chosen_metrics)
            else:
                sides[side] = None
                errors[side] = failures.get(key, "not run")
        meta = PARAMETERS.get(name)
        sens_rows.append(_sensitivity_row(name, meta.label if meta else name, meta.unit if meta else "", meta.group if meta else "process",
                                          base_value, low, high, base_values, sides["low"], sides["high"], errors))
    primary = chosen_metrics[0]
    ranking = {m: [r["parameter"] for r in sorted(sens_rows, key=lambda r: -(r["metrics"][m]["swing"] or 0.0))] for m in chosen_metrics}
    live = sum(1 for r in rows.values() if str(r.get("engine_mode")) == "live")
    basis = "tea_live" if live else "tea_cache_exact"
    default_ranges = [n for n, *_ in plan if not (ranges or {}).get(n) and relative_range is None]
    warnings = [
        "One parameter is moved at a time, so interactions between parameters are not shown, and the ranking depends on the ranges "
        "chosen (the ranges used are in each row).",
        "Results describe one simulated wash plant, gate to gate, per kg of recovered polymer: they are not a pathway's profit and "
        "not a plant measurement.",
    ]
    if default_ranges:
        warnings.append("These parameters used the tool's default ranges, not ranges the user gave: " + ", ".join(default_ranges))
    flat = [r["label"] for r in sens_rows if (r["metrics"][primary]["swing_pct_of_base"] or 0.0) < 0.05
            and r["metrics"][primary]["swing"] is not None]
    if flat:
        warnings.append(f"No measurable change in {METRICS[primary][0].lower()} from: {', '.join(flat)}. That is a property of this base "
                        "plant, not a missing input (for example solvent price acts only through make-up solvent, which is tiny at "
                        "the default 0.01% solvent loss), so a different base or range could change it.")
    if failures:
        warnings.append(f"{len(failures)} simulation(s) failed (a parameter value outside what the process model accepts); those "
                        "bars show only the side that ran: " + "; ".join(sorted(set(failures.values()))))
    top = [f"{r['label']} ({r['metrics'][primary]['swing_pct_of_base']:.0f}%)" for r in
           sorted(sens_rows, key=lambda r: -(r['metrics'][primary]['swing'] or 0.0))[:3] if r["metrics"][primary]["swing_pct_of_base"] is not None]
    base_echo = {
        "polymer": polymer_id, "solvent": solvent_name, "energy_case": energy_case, "target_mass_percent": base["target_mass_percent"],
        "processing_capacity_mt_per_yr": base["processing_capacity_mt_per_yr"],
        "dissolution_temperature_c": base["dissolution_temperature_c"], "solvent_price_usd_per_kg": base["solvent_price_usd_per_kg"],
        "assumed_by_default": [n for n, given in (("target_mass_percent", target_mass_percent),
                                                  ("processing_capacity_mt_per_yr", processing_capacity_mt_per_yr),
                                                  ("dissolution_temperature_c", dissolution_temperature_c)) if given is None],
        "metrics": base_values,
    }
    saved = _save(polymer_id, solvent_name, base_echo, chosen_metrics, sens_rows, ranking, basis)
    extra: dict[str, Any] = {"figure_data_file": saved}
    if draw_plot:
        extra.update(_render(saved))
    return tool_success(
        tool, display=(f"Tornado for {polymer_id} washed in {solvent_name}: largest drivers of {METRICS[primary][0].lower()}: "
                       + ", ".join(top) + "." + (f" Plot: {extra['figure_file']}" if extra.get("figure_file") else "")),
        analysis_type="tea_tornado", basis=basis, base=base_echo, metrics=[{"metric": m, "label": METRICS[m][0], "unit": METRICS[m][1]}
                                                                           for m in chosen_metrics],
        sensitivity_rows=sens_rows, ranking=ranking,
        simulations={"distinct_runs": len(configs), "live": live, "stored_or_reused": len(rows) - live, "failed": len(failures)},
        warnings=warnings, seconds=round(time.monotonic() - started, 1), **extra,
    )


def _save(polymer: str, solvent: str, base: dict[str, Any], metrics: tuple[str, ...], rows: list[dict[str, Any]],
          ranking: dict[str, list[str]], basis: str) -> str | None:
    payload = {"schema": SCHEMA, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "polymer": polymer,
               "solvent": solvent, "basis": basis, "base": base,
               "metrics": [{"metric": m, "label": METRICS[m][0], "unit": METRICS[m][1]} for m in metrics],
               "rows": rows, "ranking": ranking}
    try:
        directory = _results_dir()
        directory.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=1, default=str)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (directory / f"tornado_{stamp}.json").write_text(text)
        (directory / "latest_tornado.json").write_text(text)
        return str(directory / "latest_tornado.json")
    except (OSError, ValueError):
        return None


def _render(data_file: str | None) -> dict[str, Any]:
    """Draw the tornado of the saved result, write its table, and open the figure for the user."""
    if not data_file:
        return {"figure_file": None, "figure_note": "the results folder could not be written, so nothing was drawn"}
    source = Path(data_file)
    figure = source.with_name(f"{source.stem}.png")
    note = run_plot(lambda: plotting.write(source, figure), str(Path(plotting.__file__)), [str(source), "--out", str(figure)])
    if note:
        return {"figure_file": None, "figure_note": note}
    return {"figure_file": str(figure), "figure_table_file": str(figure.with_name(f"{figure.stem}.md")),
            "figure_opened": _open_file(figure)}
