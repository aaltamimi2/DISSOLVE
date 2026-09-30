"""The web app's TEA panel. When the model starts a plant TEA in a review-mode chat (evaluate_process in evaluate,
sensitivity or route mode), the turn waits while the person sees every field the CLI's process sheet shows, keeps the
model's values and the first-run defaults or changes them, turns numeric fields into ranges, and presses Run.

    GET  /api/sessions/{id}/tea-sheet         the sheet waiting for an answer, a run in progress, the last results
    POST /api/sessions/{id}/tea-sheet/check   {sheet_id, values, ranges, drop} -> plants, stored, live, time, invalid
    POST /api/sessions/{id}/tea-sheet         {sheet_id, action: run | cancel | stop, values, ranges, drop}

web.py installs one wrapper around agent.dispatch, the seam the CLI's own sheet uses. A context variable carries the
turn's panel, so every other tool call, every chat in auto mode and every caller outside a web turn runs as before.
Run expands the values and ranges into plants (a full grid, at most MAX_PLANTS), runs them one at a time through
tea.evaluate_process with confirm_live_tea and process_confirmation confirmed_on_sheet, streams a tea.progress event
per plant, and hands the model one evaluate result through the agent's own handle path. Cancel, or no answer within
WAIT_SECONDS, returns process_confirmation_aborted, as the CLI's sheet does; Stop keeps the plants that finished.
"""

from __future__ import annotations

import itertools
import json
import math
import queue
import threading
import time
import uuid
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Mapping

from dissolve import agent, session, tea, tea_polymer_parameters

MAX_PLANTS = 200  # a grid past this runs for hours; the footer asks to narrow a range instead
CONFIRM_ABOVE = 20  # the engine's own ceiling for one evaluate call; above it the panel asks once more
WAIT_SECONDS = 1800.0  # an unanswered sheet ends as a cancel, and the chat's turn with it
KEEPALIVE_SECONDS = 25.0  # a quiet stream is closed by some proxies; the browser ignores tea.waiting
PLANT_MODES = ("evaluate", "sensitivity", "route")
REFERENCE_PLANT = "ldpe-route-c1"  # what the TEA sheet button opens with in a chat that has run no plant
_ENGINE = threading.Lock()  # one panel's plant at a time: the TEA worker runs one at a time anyway, and a plant waiting
                            # here holds no process
ROUTE_FIELDS = ("processing_capacity_mt_per_yr", "energy_case", "precipitation_temperature_c")
C1_C3_ONLY = ("natural_gas_price_usd_per_m3", "steam_power_depreciation")
GROUPS = ("Plant", "Solvent and feed", "Flowsheet", "Labor and operation", "Finance", "Startup", "Capital factors",
          "Utilities")
_DEPRECIATION = ("MACRS3", "MACRS5", "MACRS7", "MACRS10", "MACRS15", "MACRS20", "SL", "DDB", "SYD")
_ROW_METRICS = ("msp_usd_per_kg", "tci_usd", "aoc_usd_per_yr", "gwp_kg_co2e_per_kg", "electricity_mj_per_kg",
                "heating_mj_per_kg", "cooling_mj_per_kg", "total_energy_mj_per_kg")

# name, label, group, kind, hint. The order is the panel's; units come from the CLI sheet's own table.
_FIELDS = (
    ("target_polymer", "Polymer", "Plant", "text", ""),
    ("solvent", "Solvent", "Plant", "text", ""),
    ("target_mass_percent", "Target polymer in the feed", "Plant", "number", ""),
    ("processing_capacity_mt_per_yr", "Feed capacity", "Plant", "number", ""),
    ("energy_case", "Energy case", "Plant", "choice", ""),
    ("dissolution_temperature_c", "Dissolution temperature", "Plant", "number", ""),
    ("precipitation_temperature_c", "Precipitation temperature", "Plant", "number", ""),
    ("dissolution_capacity", "Dissolution capacity", "Plant", "number", ""),
    ("solvent_price_usd_per_kg", "Solvent price", "Solvent and feed", "number", ""),
    ("solvent_loss_pct", "Solvent loss", "Solvent and feed", "number", "In percent: 0.01 is 0.01 %"),
    ("feedstock_distance_km", "Feedstock transport distance", "Solvent and feed", "number", ""),
    ("feedstock_price_usd_per_kg", "Feedstock price", "Solvent and feed", "number", ""),
    ("centrifuged_plastic_solvent_content_pct", "Solvent left in the centrifuged plastic", "Solvent and feed",
     "number", ""),
    ("sell_leftover_plastic", "Sell the leftover plastic", "Flowsheet", "bool", ""),
    ("burn_leftover_plastic", "Burn the leftover plastic", "Flowsheet", "bool", ""),
    ("precipitation_configuration", "Precipitation configuration", "Flowsheet", "choice", ""),
    ("precipitation_temperature_format", "Precipitation temperature format", "Flowsheet", "choice",
     "Only a constant precipitation temperature runs here"),
    ("labor_cost_usd_per_employee_yr", "Labor cost", "Labor and operation", "number", ""),
    ("labor_burden", "Labor burden", "Labor and operation", "number", ""),
    ("operating_days", "Operating days", "Labor and operation", "number", "At most 366"),
    ("irr", "Internal rate of return", "Finance", "number", ""),
    ("income_tax", "Income tax", "Finance", "number", ""),
    ("finance_interest", "Loan interest", "Finance", "number", ""),
    ("finance_years", "Loan term", "Finance", "number", "Whole years"),
    ("finance_fraction", "Share paid with the loan", "Finance", "number", ""),
    ("depreciation", "Depreciation schedule", "Finance", "choice", ""),
    ("duration", "Plant life", "Finance", "pair", "First and last year of operation"),
    ("construction_schedule", "Construction schedule", "Finance", "list",
     "Share of the investment spent in each construction year; the shares sum to 100 %"),
    ("startup_months", "Startup period", "Startup", "number", "At most 12"),
    ("startup_FOCfrac", "Fixed operating cost during startup", "Startup", "number", ""),
    ("startup_VOCfrac", "Variable operating cost during startup", "Startup", "number", ""),
    ("startup_salesfrac", "Sales during startup", "Startup", "number", ""),
    ("WC_over_FCI", "Working capital", "Capital factors", "number", "Share of the fixed capital investment"),
    ("warehouse", "Warehouse", "Capital factors", "number", ""),
    ("site_development", "Site development", "Capital factors", "number", ""),
    ("additional_piping", "Additional piping", "Capital factors", "number", ""),
    ("proratable_costs", "Proratable costs", "Capital factors", "number", ""),
    ("field_expenses", "Field expenses", "Capital factors", "number", ""),
    ("construction", "Construction", "Capital factors", "number", ""),
    ("contingency", "Contingency", "Capital factors", "number", ""),
    ("other_indirect_costs", "Other indirect costs", "Capital factors", "number", ""),
    ("property_insurance", "Property insurance", "Capital factors", "number", ""),
    ("maintenance", "Maintenance", "Capital factors", "number", ""),
    ("lang_factor", "Lang factor", "Capital factors", "number",
     "Empty: installed costs use each unit's bare-module factors"),
    ("natural_gas_price_usd_per_m3", "Natural gas price", "Utilities", "number", ""),
    ("steam_power_depreciation", "Steam and power plant depreciation", "Utilities", "choice", ""),
)
SHEET_NAMES = tuple(row[0] for row in _FIELDS)
_KIND = {row[0]: row[3] for row in _FIELDS}
_LABEL = {row[0]: row[1] for row in _FIELDS}
_TWELVE = frozenset(tea._PUBLIC_REQUIRED_FIELDS)
# The model's origin tokens as the panel shows them; the engine's are supplied, inherited and from_screen.
_ORIGIN = {"supplied": "model", "inherited": "inherited", "from_screen": "screen"}
_MODE_ARGS = {
    "evaluate": frozenset({"mode", "process_config", "process_configs", "screening_shortlist", "held_process_basis",
                           "screen_to_economics_order", "confirm_live_tea", "engine_mode", "handle", "row_id",
                           "timeout_seconds"}),
    "sensitivity": frozenset({"mode", "process_config", "parameter", "values", "analysis_mode", "metric",
                              "engine_mode", "handle", "row_id", "timeout_seconds", "confirm_live_tea",
                              "screen_to_economics_order"}),
    "route": frozenset({"mode", "confirm_live_tea", "screen_to_economics_order", *tea._ROUTE_MODE_FORWARD}),
}
_PLANT_SCALARS = ("engine_mode", "timeout_seconds", "screen_to_economics_order")

assert set(SHEET_NAMES) == set(tea.public_process_field_names(energy_case="C1")), "the panel lists every sheet field"


class PassThrough(Exception):
    """A call the panel cannot show (malformed, or naming what the sheet has no field for): the engine answers it,
    refusal included, exactly as without the panel."""


class SheetError(ValueError):
    """Values or ranges that cannot become plants; the message is for the person."""

    def __init__(self, message: str, **detail: Any):
        super().__init__(message)
        self.detail = detail


class Conflict(Exception):
    """The panel is not in the state the request expects (answered already, or nothing is running)."""


def _same(left: Any, right: Any) -> bool:
    return tea._sheet_values_match(left, right)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _round(value: float) -> float:
    """Twelve significant figures: 0.1 + 2 * 0.05 is 0.2, not 0.20000000000000004."""
    number = float(f"{value:.12g}")
    return int(number) if number.is_integer() and abs(number) < 1e15 else number


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if _finite(value):
        return str(_round(value)) if float(value).is_integer() else f"{value:.6g}"
    if isinstance(value, (list, tuple)):
        return "/".join(_fmt(item) for item in value)
    return str(value)


def _distinct(items: Any) -> list[Any]:
    """Items once each, in order, whatever their type (a warning is text, a gap may be an object)."""
    seen: set[str] = set()
    out = []
    for item in items:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _unique(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if not any(_same(value, kept) for kept in out):
            out.append(value)
    return out


def _choices(name: str, value: Any) -> list[dict[str, str]]:
    if name == "energy_case":
        return [{"value": case, "label": f"{case}: {text}"} for case, text in tea._ENERGY_CASES.items()]
    if name == "precipitation_configuration":
        return [{"value": item, "label": item} for item in sorted(tea._PRECIPITATION_CONFIGURATIONS)]
    if name == "precipitation_temperature_format":
        return [{"value": "constant", "label": "constant"}]
    names = list(_DEPRECIATION) + ([str(value)] if isinstance(value, str) and value not in _DEPRECIATION else [])
    return [{"value": item, "label": item} for item in names]


def field_specs(values: Mapping[str, Any], *, editable: frozenset[str] | set[str], ranges: bool) -> list[dict]:
    """Every sheet field as the panel draws it. A fraction is shown and typed as a percent (irr 0.10 is 10 %)."""
    specs = []
    for name, label, group, kind, hint in _FIELDS:
        unit = tea._SHEET_FIELD_UNITS.get(name, "")
        percent = unit == "fraction"
        spec: dict[str, Any] = {
            "name": name, "label": label, "group": group, "kind": kind, "unit": "%" if percent else unit,
            "percent": percent, "hint": hint, "required": name in _TWELVE, "only_c1_c3": name in C1_C3_ONLY,
            "editable": name in editable,
            "rangeable": ranges and name in editable and kind in {"number", "bool", "choice"}
            and name != "precipitation_temperature_format",
        }
        if kind == "choice":
            spec["options"] = _choices(name, values.get(name))
        specs.append(spec)
    return specs


def _public(scenario: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A scenario in the engine's mixed vocabulary as sheet fields, and whatever else it carries (lca_cfs, say)."""
    plant: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    for key, value in scenario.items():
        if value is None or str(key).startswith("_") or key == "label":
            continue
        public = tea._process_config_public_name(str(key))
        if public is None:
            continue
        if public == "energy_case":
            value = tea._energy_case_token(value) or value
        if public in SHEET_NAMES:
            plant[public] = value
        else:
            extras[public] = value
    return plant, extras


def _admitted_price(solvent: Any) -> float | None:
    if not isinstance(solvent, str) or not solvent.strip():
        return None
    try:
        return float(tea._stored_route_named_remainder(solvent)["solvent_price_usd_per_kg"])
    except (tea._ScenarioInputError, ValueError, TypeError, KeyError):
        return None


def _catalog() -> dict[str, Any]:
    """The polymers live TEA admits and the solvents with an admitted price, for the panel's suggestions."""
    polymers = sorted(name for name, row in tea_polymer_parameters.POLYMERS.items()
                      if row.admission == tea_polymer_parameters.ADMISSION_LIVE)
    solvents = []
    for row in (tea.cache_payload().get("solvent_assumptions") or {}).get("records") or []:
        name = row.get("name_biosteam") or row.get("name_cosmobase")
        if name and row.get("price_usd_per_kg") is not None:
            solvents.append({"name": str(name), "price_usd_per_kg": float(row["price_usd_per_kg"])})
    return {"polymers": polymers, "solvents": sorted(solvents, key=lambda row: row["name"].casefold())}


def _seed(base: Mapping[str, Any], origin: Mapping[str, str], previous: Mapping[str, Any]) -> tuple[dict, dict]:
    """The sheet's values: the model's plant over the person's earlier choices over the first-run defaults.

    previous holds the chat's last run: the values the model proposed and the values the person ran. The person's
    switches and coefficients carry over, and so does the rest of their plant when the next call names the same
    polymer and solvent. A value the model names wins, except where it repeats its own earlier proposal for a field
    the person corrected (a dissolution capacity of 0.1 the person set to 3): the correction stands. So a follow-up
    question keeps the person's plant, and a new solvent never keeps the old one's price."""
    defaults = tea.first_run_sheet_defaults(energy_case="C1")  # all 46; a C2 plant drops the two utility fields
    ran, model = previous.get("ran") or {}, previous.get("model") or {}
    values: dict[str, Any] = {name: defaults.get(name) for name in SHEET_NAMES}
    source = {name: ("default" if name in defaults else "missing") for name in SHEET_NAMES}
    same_plant = bool(ran) and all(
        name in base and tea._process_config_values_equivalent(name, base[name], ran.get(name))
        for name in ("target_polymer", "solvent")
    )
    for name, value in ran.items():
        if name not in SHEET_NAMES or value is None or (name not in _TWELVE and _same(value, defaults.get(name))):
            continue
        if name not in _TWELVE or same_plant:
            values[name], source[name] = value, "previous"
    for name, value in base.items():
        corrected = name in ran and name in model and not _same(ran[name], model[name])
        if corrected and _same(value, model[name]):
            values[name], source[name] = ran[name], "previous"
        else:
            values[name], source[name] = value, origin.get(name, "model")
    if values.get("solvent_price_usd_per_kg") is None and (price := _admitted_price(values.get("solvent"))) is not None:
        values["solvent_price_usd_per_kg"], source["solvent_price_usd_per_kg"] = price, "admitted"
    return values, source


def _sheet(mode: str, plants: list[dict], origins: list[dict], extras: dict, previous: Mapping[str, Any]) -> dict:
    base = plants[0]
    values, source = _seed(base, origins[0], previous)
    ranges: dict[str, Any] = {}
    paired = None
    plants = [plant for index, plant in enumerate(plants)
              if not any(all(_same(plant.get(n), other.get(n)) for n in {*plant, *other}) for other in plants[:index])]
    if len(plants) > 1:
        varying = [name for name in SHEET_NAMES
                   if any(not _same(plant.get(name, base.get(name)), base.get(name)) for plant in plants[1:])]
        columns = {name: [plant.get(name, base.get(name)) for plant in plants] for name in varying}
        rangeable = all(_KIND[name] in {"number", "bool", "choice"} for name in varying)
        grid = math.prod(len(_unique(columns[name])) for name in varying) == len(plants)
        if rangeable and grid:  # the plants are every combination of the varying values: ranges, editable as such
            ranges = {name: {"kind": "list", "values": _unique(columns[name])} for name in varying}
        else:  # values that vary together stay together, one row per proposed plant
            paired = {"fields": varying, "rows": [[plant.get(name, base.get(name)) for name in varying]
                                                  for plant in plants]}
        for name in varying:
            source[name] = "model"
    editable = frozenset(SHEET_NAMES)
    return {
        "id": uuid.uuid4().hex[:12], "mode": mode,
        "title": f"{values.get('target_polymer') or 'Polymer'} in {values.get('solvent') or 'solvent'}",
        "groups": list(GROUPS), "fields": field_specs(values, editable=editable, ranges=True),
        "values": values, "origin": source, "defaults": tea.first_run_sheet_defaults(energy_case="C1"),
        "ranges": ranges, "paired": paired, "extras": extras, "editable": sorted(editable),
        "route": None, "sensitivity": None, "model_values": dict(base),
    }


def _evaluate_sheet(kwargs: Mapping[str, Any], previous: Mapping[str, Any]) -> dict:
    config, configs = kwargs.get("process_config"), kwargs.get("process_configs")
    if config is not None and configs is not None:
        raise PassThrough
    if config is not None and not isinstance(config, dict):
        raise PassThrough
    if configs is not None and (not isinstance(configs, list) or not all(isinstance(item, dict) for item in configs)):
        raise PassThrough
    for item in [config, *(configs or [])]:
        if item is not None:
            tea._refuse_process_config_ingest(item)
    scenarios = [config] if config is not None else (list(configs) if configs else None)
    composed, origin, origins = tea._compose_evaluate_scenarios(
        scenarios, kwargs.get("screening_shortlist"), kwargs.get("held_process_basis"), kwargs.get("handle"),
        kwargs.get("row_id"),
    )
    if not composed or len(composed) > MAX_PLANTS:
        raise PassThrough
    plants, extras = [], {}
    for item in composed:
        plant, extra = _public(item)
        plants.append(plant)
        extras.update(extra)
    tokens = origins or [origin or {}] * len(plants)
    shown = [{name: _ORIGIN.get(str(token.get(name, "supplied")), "model") for name in plant}
             for plant, token in zip(plants, tokens)]
    return _sheet("evaluate", plants, shown, extras, previous)


def _stored_points(scenario: Mapping[str, Any], internal: str) -> list[float]:
    """The values stored design points hold for one field when every other serve-key field equals the baseline's:
    what the engine's sensitivity sweeps when no values are named."""
    try:
        baseline = tea._scenario_config(dict(scenario), require_complete_twelve=True)
    except (tea._ScenarioInputError, ValueError, TypeError):
        return []

    def without(config: Mapping[str, Any]) -> dict[str, Any]:
        key = json.loads(tea._config_key(dict(config)))
        key.pop(internal, None)
        return key

    held = without(baseline)
    found = sorted({float(record["config"][internal]) for record in tea._records()
                    if internal in record["config"] and without(record["config"]) == held})
    points = _unique([float(baseline[internal]), *found])
    return [_round(value) for value in points] if len(points) > 1 else []


def _sensitivity_sheet(kwargs: Mapping[str, Any], previous: Mapping[str, Any]) -> dict:
    config = kwargs.get("process_config")
    if config is not None and not isinstance(config, dict):
        raise PassThrough
    if config is not None:
        tea._refuse_process_config_ingest(config)
    parameter = str(kwargs.get("parameter") or "").strip()
    internal = tea._SCENARIO_ALIASES.get(parameter, parameter)
    if internal not in tea._NUMERIC_FIELDS:
        raise PassThrough  # the engine names the fields it can sweep
    field = dict(tea._DESIGN_POINT_PUBLIC_FIELDS).get(internal, internal)
    named = kwargs.get("values")
    if named is not None and (not isinstance(named, list) or not all(_finite(value) for value in named)):
        raise PassThrough
    scenario, origin = tea._compose_sensitivity_scenario(config, kwargs.get("handle"), kwargs.get("row_id"))
    plant, extras = _public(scenario)
    shown = {name: _ORIGIN.get(str((origin or {}).get(name, "supplied")), "model") for name in plant}
    sheet = _sheet("sensitivity", [plant], [shown], extras, previous)
    base = sheet["values"].get(field)
    if named:
        points = _unique(([base] if _finite(base) else []) + [float(value) for value in named])
    else:
        points = _stored_points(scenario, internal)
        if not points and _finite(base) and base:
            points = [_round(base * factor) for factor in (0.5, 0.75, 1.0, 1.25, 1.5)]
    sheet["ranges"] = {field: {"kind": "list", "values": sorted(_round(float(value)) for value in points)}}
    sheet["sensitivity"] = {"parameter": field, "values_named": bool(named)}
    return sheet


def _route_sheet(kwargs: Mapping[str, Any], previous: Mapping[str, Any]) -> dict:
    route, _exact = tea._load_stored_route_from_handle(kwargs.get("handle"), kwargs.get("row_id"))
    defaults = tea.first_run_sheet_defaults(energy_case="C1")
    values = {name: defaults.get(name) for name in SHEET_NAMES}
    source = {name: "default" for name in SHEET_NAMES}
    for name in ROUTE_FIELDS:
        if (previous.get("ran") or {}).get(name) is not None:
            values[name], source[name] = previous["ran"][name], "previous"
        if kwargs.get(name) is not None:
            value = kwargs[name]
            values[name] = (tea._energy_case_token(value) or value) if name == "energy_case" else value
            source[name] = "model"
    steps = []
    for step in route.get("steps") or []:
        if isinstance(step, dict):
            steps.append({
                "polymer": step.get("dissolved_polymer"), "solvent": step.get("solvent"),
                "temperature_c": next((step[key] for key in ("temperature_c", "dissolution_temperature_c", "T_C")
                                       if step.get(key) is not None), None),
            })
    editable = frozenset(ROUTE_FIELDS)
    return {
        "id": uuid.uuid4().hex[:12], "mode": "route",
        "title": "Route: " + ", then ".join(f"{s['polymer']} in {s['solvent']}" for s in steps) if steps else "Route",
        "groups": list(GROUPS), "fields": field_specs(values, editable=editable, ranges=False),
        "values": values, "origin": source, "defaults": defaults, "ranges": {}, "paired": None, "extras": {},
        "editable": sorted(editable), "route": {"handle": kwargs.get("handle"), "row_id": kwargs.get("row_id"),
                                                "steps": steps},
        "sensitivity": None, "model_values": {name: kwargs[name] for name in ROUTE_FIELDS if name in kwargs},
    }


def build_sheet(mode: str, kwargs: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> dict:
    """The sheet for one evaluate_process call, or PassThrough when the engine should answer it unchanged."""
    if mode not in PLANT_MODES or set(kwargs) - _MODE_ARGS[mode]:
        raise PassThrough
    try:
        if mode == "route":
            sheet = _route_sheet(kwargs, previous or {})
        elif mode == "sensitivity":
            sheet = _sensitivity_sheet(kwargs, previous or {})
        else:
            sheet = _evaluate_sheet(kwargs, previous or {})
    except Exception as error:  # whatever the sheet cannot show, the engine answers (and refuses) as before
        raise PassThrough from error
    sheet.update(_catalog())
    sheet["limits"] = {"max_plants": MAX_PLANTS, "confirm_above": CONFIRM_ABOVE,
                       "seconds_per_live_plant": round(tea._LIVE_TEA_SECONDS_PER_PAIR, 2)}
    sheet["expires_at"] = time.time() + WAIT_SECONDS
    return sheet


def range_values(spec: Mapping[str, Any], shape: Mapping[str, Any]) -> list[Any]:
    """One field's values from a range: linear (from, to, step), log (from, to, points) or a list."""
    label, kind = spec["label"], str(shape.get("kind") or "")
    if kind == "list":
        raw = shape.get("values")
        if not isinstance(raw, list) or not raw:
            raise SheetError(f"{label}: list at least one value.")
        if len(raw) > MAX_PLANTS:
            raise SheetError(f"{label}: at most {MAX_PLANTS} values.")
        if spec["kind"] == "bool":
            if not all(isinstance(value, bool) for value in raw):
                raise SheetError(f"{label}: the values are yes and no.")
        elif spec["kind"] == "choice":
            allowed = {option["value"] for option in spec.get("options") or []}
            if not all(value in allowed for value in raw):
                raise SheetError(f"{label}: choose from {', '.join(sorted(allowed))}.")
        elif not all(_finite(value) for value in raw):
            raise SheetError(f"{label}: every value must be a number.")
        return _unique([_round(value) if _finite(value) else value for value in raw])
    if spec["kind"] != "number":
        raise SheetError(f"{label}: pick the values from the list.")
    start, stop = shape.get("from"), shape.get("to")
    if not (_finite(start) and _finite(stop)):
        raise SheetError(f"{label}: give the range a start and an end.")
    if stop < start:
        raise SheetError(f"{label}: the end is below the start.")
    if kind == "linear":
        step = shape.get("step")
        if not _finite(step) or step <= 0:
            raise SheetError(f"{label}: the step must be above zero.")
        count = math.floor((stop - start) / step + 1e-9) + 1
        if count > MAX_PLANTS:
            raise SheetError(f"{label}: {count:,} values; at most {MAX_PLANTS} plants run at once.", count=count)
        return _unique([_round(start + index * step) for index in range(count)])
    if kind == "log":
        points = shape.get("points")
        if not isinstance(points, int) or isinstance(points, bool) or not 2 <= points <= MAX_PLANTS:
            raise SheetError(f"{label}: log spacing takes 2 to {MAX_PLANTS} points.")
        if start <= 0:
            raise SheetError(f"{label}: log spacing needs a start above zero.")
        return _unique([_round(start * (stop / start) ** (index / (points - 1))) for index in range(points)])
    raise SheetError(f"{label}: a range is linear, log or a list.")


def expand(sheet: Mapping[str, Any], values: Mapping[str, Any] | None, ranges: Mapping[str, Any] | None,
           drop: list[int] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """The plants a run would evaluate: the sheet's values with the person's edits, times every ranged value (a full
    grid), times the proposed plants still ticked. Each plant is labelled by what varies in it."""
    specs = {spec["name"]: spec for spec in sheet["fields"]}
    editable = set(sheet["editable"])
    base = dict(sheet["values"])
    for name, value in (values or {}).items():
        if name in editable:
            base[name] = value
    axes: list[tuple[str, list[Any]]] = []
    for name in SHEET_NAMES:
        shape = (ranges or {}).get(name)
        if shape is None:
            continue
        if not specs[name]["rangeable"]:
            raise SheetError(f"{specs[name]['label']} cannot take a range here.")
        if not isinstance(shape, Mapping):
            raise SheetError(f"{specs[name]['label']}: a range is linear, log or a list.")
        axes.append((name, range_values(specs[name], shape)))
    paired = sheet.get("paired")
    dropped = set(drop or ())
    rows: list[list[Any] | None] = [None]
    if paired:
        rows = [row for index, row in enumerate(paired["rows"]) if index not in dropped]
        if not rows:
            raise SheetError("Every proposed plant is unticked; tick one to run.")
    count = math.prod(len(points) for _name, points in axes) * len(rows)
    if count > MAX_PLANTS:
        raise SheetError(f"{count:,} plants; at most {MAX_PLANTS} run at once. Narrow a range.", count=count)
    varied = [name for name, _points in axes] + list(paired["fields"] if paired else [])
    plants: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        for combo in itertools.product(*(points for _name, points in axes)):
            plant = dict(base)
            for name, value in zip(paired["fields"] if paired and row else [], row or []):
                if value is not None:
                    plant[name] = value
            for (name, _points), value in zip(axes, combo):
                plant[name] = value
            if sheet["mode"] == "route":
                plant = {name: plant.get(name) for name in ROUTE_FIELDS}
            else:
                if (tea._energy_case_token(plant.get("energy_case") or "") or "") == "C2":
                    for name in C1_C3_ONLY:
                        plant.pop(name, None)
                plant = {name: value for name, value in plant.items() if value is not None or name in _TWELVE}
            label = ("; ".join(f"{name}={_fmt(plant.get(name))}" for name in varied) if varied
                     else sheet["title"])
            if not any(_same(plant, kept) for _label, kept in plants):
                plants.append((label, plant))
    return plants


def _engine_mode(kwargs: Mapping[str, Any]) -> str:
    return str(kwargs.get("engine_mode") or "auto").strip().casefold()


def check(sheet: Mapping[str, Any], plants: list[tuple[str, dict]], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """What a run would do, before anything runs: how many plants, how many are stored design points (instant),
    how long the rest take live, and which plants the engine would refuse, with its reason."""
    invalid: list[dict[str, Any]] = []
    stored = live = 0
    if sheet["mode"] == "route":
        for label, plant in plants:
            try:
                capacity = float(plant.get("processing_capacity_mt_per_yr"))
                precipitation = float(plant.get("precipitation_temperature_c"))
                if not (math.isfinite(capacity) and capacity > 0 and math.isfinite(precipitation)):
                    raise ValueError("capacity must be above zero and the temperature a number")
                if tea._energy_case_token(plant.get("energy_case") or "") not in tea._ENERGY_CASES:
                    raise ValueError("energy_case must be C1, C2, or C3")
            except (TypeError, ValueError) as error:
                invalid.append({"label": label, "error": str(error), "error_code": "invalid_scenario"})
        steps = len((sheet.get("route") or {}).get("steps") or []) or 1
        live = steps * (len(plants) - len(invalid))
    else:
        for label, plant in plants:
            try:
                config = tea._scenario_config(dict(plant), require_complete_twelve=True)
            except tea._ScenarioInputError as error:
                invalid.append({"label": label, "error": str(error), "error_code": error.error_code})
                continue
            except (TypeError, ValueError) as error:
                invalid.append({"label": label, "error": str(error),
                                "error_code": getattr(error, "code", "invalid_scenario")})
                continue
            if tea._would_start_live_child(config, _engine_mode(kwargs)):
                live += 1
            else:
                stored += 1
    return {
        "plants": len(plants), "stored": stored, "live": live,
        "seconds": round(live * tea._LIVE_TEA_SECONDS_PER_PAIR),
        "invalid": invalid[:25], "invalid_count": len(invalid),
        "confirm": len(plants) > CONFIRM_ABOVE, "runnable": bool(plants) and not invalid,
        "admitted_price": _admitted_price(plants[0][1].get("solvent")) if plants else None,
    }


def _usable(row: Mapping[str, Any]) -> bool:
    return tea._evaluate_row_usable(dict(row))


def _plant_rows(label: str, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The comparison rows one engine call returned, or one failed row saying why nothing came back."""
    rows = [dict(row) for row in data.get("comparison_rows") or [] if isinstance(row, dict)]
    if data.get("success") and rows:
        return rows
    failures = [dict(row) for row in data.get("failures") or [] if isinstance(row, dict)]
    if failures:
        return failures
    return [{"label": label, "success": False, "error": str(data.get("error") or "the engine returned no row"),
             "error_code": data.get("error_code") or data.get("error_type")}]


def _panel_row(index: int, row: Mapping[str, Any], plant: Mapping[str, Any], varied: list[str]) -> dict[str, Any]:
    """A result row as the panel shows it: the varied values, the metrics, stored or live, and any failure."""
    out: dict[str, Any] = {
        "index": index, "label": row.get("label"), "success": _usable(row),
        "values": {name: plant.get(name) for name in varied},
        "polymer": row.get("target_polymer") or row.get("polymer") or plant.get("target_polymer"),
        "solvent": row.get("solvent") or plant.get("solvent"),
        "engine_mode": row.get("engine_mode"), "stored_record": row.get("cache_record_label"),
        "can_cite_as_validated_process": row.get("can_cite_as_validated_process"),
        **{metric: row.get(metric) for metric in _ROW_METRICS},
    }
    if not out["success"]:
        out["error"] = str(row.get("error") or "no MSP or GWP came back")[:400]
        out["error_code"] = row.get("error_code") or row.get("error_type")
    return out


def _origin_for(plant: Mapping[str, Any], sheet: Mapping[str, Any]) -> dict[str, str]:
    """The CLI sheet's origin tokens for one plant the person ran: supplied, from_screen, inherited or default."""
    named = [name for name, source in sheet["origin"].items() if source in {"model", "admitted", "inherited"}]
    origin = tea.confirmation_sheet_field_origin(dict(plant), snapshot=dict(sheet["values"]), caller_keys=named)
    for name, source in sheet["origin"].items():
        if name in origin and source in {"screen", "inherited"} and _same(plant.get(name), sheet["values"].get(name)):
            origin[name] = "from_screen" if source == "screen" else "inherited"
    return origin


def combine(runs: list[tuple[str, dict, dict]], planned: int, sheet: Mapping[str, Any], note: dict[str, Any]) -> dict:
    """One evaluate result for every plant the panel ran, in the shape a multi-plant evaluate call returns, so the
    model pages it with result_read and hands it to rank_landscape as it would its own call's."""
    rows: list[dict[str, Any]] = []
    for label, plant, data in runs:
        for row in _plant_rows(label, data):
            row["field_origin"] = _origin_for(plant, sheet)
            rows.append(row)
    usable = [row for row in rows if _usable(row)]
    datas = [data for _label, _plant, data in runs]
    if not usable:  # nothing to compare: the model sees the engine's own refusal of the first plant, and why
        first = dict(datas[0]) if datas else {"success": False, "error": "No plant ran.", "error_code": "no_plant_ran"}
        return {**first, "comparison_rows": rows, "panel": note, "process_confirmation": "confirmed_on_sheet"}
    template = next(data for data in datas if data.get("success"))
    modes = [str(row.get("engine_mode")) for row in usable]
    by_msp = sorted(usable, key=lambda row: float(row["msp_usd_per_kg"]))
    by_gwp = sorted(usable, key=lambda row: float(row["gwp_kg_co2e_per_kg"]))
    base_origin = _origin_for(runs[0][1], sheet)
    return {
        **template,
        "scenarios_requested": planned, "completed": len(usable), "failed": len(rows) - len(usable),
        "comparison_rows": rows,
        "engine_mode": modes[0] if len(set(modes)) == 1 else "mixed",
        "cache_match_status": "exact" if set(modes) == {"cache"} else "mixed_or_live",
        "lowest_msp_scenario": by_msp[0]["label"], "lowest_gwp_scenario": by_gwp[0]["label"],
        "lowest_msp_tied_scenarios": tea._tied_lowest(by_msp, "msp_usd_per_kg"),
        "lowest_gwp_tied_scenarios": tea._tied_lowest(by_gwp, "gwp_kg_co2e_per_kg"),
        "metric_units": tea._comparison_metric_units(rows),
        "process_details": [item for data in datas for item in data.get("process_details") or []],
        "process_data_gaps": _distinct(gap for data in datas for gap in data.get("process_data_gaps") or []),
        "warnings": _distinct(item for data in datas for item in data.get("warnings") or []),
        "provenance": tea._provenance(modes),
        "field_origin": base_origin, "silently_defaulted": {},
        "process_confirmation": "confirmed_on_sheet",
        "panel": note,
    }


def issue(name: str, kwargs: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """The tail of agent.dispatch for a result the panel assembled: the same contract, source basis, handle and
    session record a call the model made itself gets."""
    record = agent.current_tool_session()
    parsed = {**parsed, "data": agent._bound_logs(parsed["data"])}
    data, display = parsed["data"], parsed.get("display")
    if not data.get("success"):
        return agent._emit(name, kwargs, agent.to_contract(parsed, None), parsed, display=display)
    basis = agent.source_basis_for(name, data, kwargs)
    if not basis:
        out = agent._refuse("no_honest_basis", tool=name)
        return agent._emit(name, kwargs, out, parsed, display=display)
    payload = agent._issue_handle(record, name, basis, data, agent.to_contract(parsed, basis), display)
    handle = payload.get("handle") if payload.get("available") else None
    if handle:
        return agent._emit(name, kwargs, payload, None, handle, None, basis)
    return agent._emit(name, kwargs, payload, parsed, None, display, basis)


def _aborted(detail: str) -> dict[str, Any]:
    """What the CLI's sheet returns when the person aborts, in the contract the web app's tool rows read."""
    data = {"success": False, "error": "Process confirmation aborted; the model args did not run. " + detail,
            "error_code": "process_confirmation_aborted"}
    return {"available": False, "refusal": "process_confirmation_aborted", "data": data}


class Panel:
    """One chat's TEA panel: the sheet waiting for the person, a run in progress, and the last results. The turn's
    thread waits on it; the panel's endpoints answer it from request threads."""

    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.sheet: dict[str, Any] | None = None
        self.kwargs: dict[str, Any] = {}
        self.answers: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stop = threading.Event()
        self.progress: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None
        self.previous: dict[str, Any] = {}

    def state(self) -> dict[str, Any]:
        with self.guard:
            return {"sheet": self.sheet, "progress": self.progress, "result": self.result}

    def _pending(self, sheet_id: str) -> dict[str, Any]:
        if self.sheet is None or self.sheet["id"] != sheet_id:
            raise Conflict("This TEA panel is no longer waiting for an answer.")
        return self.sheet

    def check(self, sheet_id: str, values: Mapping[str, Any], ranges: Mapping[str, Any], drop: list[int]) -> dict:
        with self.guard:
            sheet, kwargs = self._pending(sheet_id), dict(self.kwargs)
        try:
            plants = expand(sheet, values, ranges, drop)
        except SheetError as error:
            return {"plants": error.detail.get("count", 0), "stored": 0, "live": 0, "seconds": 0, "invalid": [],
                    "invalid_count": 0, "confirm": False, "runnable": False, "error": str(error)}
        return check(sheet, plants, kwargs)

    def respond(self, sheet_id: str, action: str, values: Mapping[str, Any], ranges: Mapping[str, Any],
                drop: list[int]) -> dict[str, Any]:
        if action == "stop":
            with self.guard:
                if self.progress is None or self.progress["sheet_id"] != sheet_id:
                    raise Conflict("No TEA run is in progress.")
                self.stop.set()
            return {"ok": True, "action": "stop"}
        if action == "cancel":
            with self.guard:
                self._pending(sheet_id)
                self.sheet = None
            self.answers.put({"action": "cancel"})
            return {"ok": True, "action": "cancel"}
        if action != "run":
            raise SheetError("action is run, cancel or stop.")
        with self.guard:
            sheet, kwargs = self._pending(sheet_id), dict(self.kwargs)
        plants = expand(sheet, values, ranges, drop)
        report = check(sheet, plants, kwargs)
        if not report["runnable"]:
            raise SheetError("Some plants would be refused; fix them before running.", report=report)
        with self.guard:
            self._pending(sheet_id)
            self.sheet = None
            self.stop.clear()
            self.progress = {"sheet_id": sheet_id, "done": 0, "total": len(plants), "rows": [], "running": None}
        base = {**sheet["values"], **{k: v for k, v in (values or {}).items() if k in set(sheet["editable"])}}
        self.answers.put({"action": "run", "plants": plants, "base": base, "ranges": dict(ranges or {}),
                          "drop": list(drop or [])})
        return {"ok": True, "action": "run", "plants": len(plants)}

    def ask(self, turn: "Turn", sheet: dict[str, Any], name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Show the sheet and wait (in the turn's thread) for Run, Cancel or the time limit."""
        while True:
            try:
                self.answers.get_nowait()  # an answer to an earlier sheet cannot answer this one
            except queue.Empty:
                break
        with self.guard:
            self.sheet, self.kwargs, self.progress = sheet, dict(kwargs), None
        turn.send({"event": "tea.sheet", "sheet": sheet})
        with turn.idle():  # the person may take minutes, and the plants run in the TEA worker, under its own limit
            deadline = time.monotonic() + WAIT_SECONDS
            answer: dict[str, Any] = {"action": "expire"}
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    answer = self.answers.get(timeout=min(KEEPALIVE_SECONDS, remaining))
                    break
                except queue.Empty:
                    turn.send({"event": "tea.waiting", "sheet_id": sheet["id"]})
            with self.guard:
                if self.sheet is not None and self.sheet["id"] == sheet["id"]:
                    self.sheet = None
            if answer["action"] != "run":
                turn.declined = True
                turn.send({"event": "tea.closed", "sheet_id": sheet["id"], "reason": answer["action"]})
                return _aborted("The person closed the TEA panel without running it." if answer["action"] == "cancel"
                                else f"The TEA panel waited {WAIT_SECONDS / 60:.0f} minutes without an answer.")
            try:
                return self._run(turn, sheet, answer, name, kwargs)
            except Exception as error:  # the answer goes on with the reason, and the panel does not spin forever
                message = f"{type(error).__name__}: {error}"
                with self.guard:
                    self.progress = None
                turn.send({"event": "tea.closed", "sheet_id": sheet["id"], "reason": "error", "message": message})
                return agent._refuse("tool_exception", error=message)

    def _run(self, turn: "Turn", sheet: dict[str, Any], answer: dict[str, Any], name: str,
             kwargs: dict[str, Any]) -> dict[str, Any]:
        plants: list[tuple[str, dict]] = answer["plants"]
        axes = [key for key in SHEET_NAMES if key in answer["ranges"]]
        paired = list((sheet.get("paired") or {}).get("fields") or [])
        varied = axes + paired
        runs: list[tuple[str, dict, dict]] = []
        rows: list[dict[str, Any]] = []
        token = tea.PROCESS_CONFIRMATION.set("confirmed_on_sheet")
        try:
            for index, (label, plant) in enumerate(plants):
                with _ENGINE:
                    if self.stop.is_set():
                        break
                    with self.guard:
                        self.progress["running"] = label
                    turn.send({"event": "tea.progress", "sheet_id": sheet["id"], "done": index, "total": len(plants),
                               "running": label})
                    data = _call(sheet, kwargs, label, plant)
                runs.append((label, plant, data))
                new = [_panel_row(len(rows) + offset, row, plant, varied)
                       for offset, row in enumerate(_plant_rows(label, data))]
                rows.extend(new)
                with self.guard:
                    self.progress.update(done=index + 1, rows=list(rows), running=None)
                turn.send({"event": "tea.progress", "sheet_id": sheet["id"], "done": index + 1, "total": len(plants),
                           "rows": new})
        finally:
            tea.PROCESS_CONFIRMATION.reset(token)
        stopped = len(runs) < len(plants)
        edited = {key: {"proposed": sheet["values"].get(key), "ran": answer["base"].get(key)}
                  for key in sheet["editable"] if not _same(answer["base"].get(key), sheet["values"].get(key))}
        note = {
            "note": ("The person reviewed this plant in the web app's TEA panel and ran it: each row is a plant they "
                     "confirmed. Report the rows as run and name the fields they edited."),
            "requested_mode": sheet["mode"], "plants_planned": len(plants), "plants_run": len(runs),
            "stopped_early": stopped,
            "ranged_fields": {key: _unique([plant.get(key) for _label, plant in plants]) for key in axes},
            "paired_fields": paired, "edited_fields": edited,
            "defaulted_fields": sorted(key for key, source in sheet["origin"].items()
                                       if source == "default" and key in set(sheet["editable"]) and key not in edited),
        }
        if sheet["mode"] == "route":
            data = dict(runs[0][2]) if runs else {"success": False, "error": "Stopped before the route ran.",
                                                   "error_code": "process_confirmation_aborted"}
            data["panel"] = note
        else:
            data = combine(runs, len(plants), sheet, note)
        payload = issue(name, _recorded(sheet, answer, kwargs), {"display": None, "data": data})
        result = {
            "event": "tea.result", "sheet_id": sheet["id"], "mode": sheet["mode"], "title": sheet["title"],
            "rows": rows, "axes": [{"name": key, "values": note["ranged_fields"].get(key, [])} for key in axes],
            "paired_fields": note["paired_fields"], "planned": len(plants), "ran": len(runs), "stopped": stopped,
            "handle": payload.get("handle"), "source_basis": payload.get("source_basis"),
            "edited_fields": sorted(edited), "defaulted_fields": note["defaulted_fields"],
            "fields": [{key: spec[key] for key in ("name", "label", "unit", "percent", "kind")}
                       for spec in sheet["fields"] if spec["name"] in set(varied)],
            "labels": {spec["name"]: spec["label"] for spec in sheet["fields"]},
        }
        with self.guard:
            self.progress = None
            self.result = result
            self.previous = {"ran": dict(answer["base"]), "model": dict(sheet.get("model_values") or {})}
        turn.send(result)
        return payload


def _recorded(sheet: Mapping[str, Any], answer: Mapping[str, Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """The call as the session records it: the plant the person confirmed and the ranges they ran. Every plant ran
    as an evaluate call, so a sensitivity sheet is recorded as evaluate, with the mode the model asked for."""
    if sheet["mode"] == "route":
        call = {"mode": "route", **{key: kwargs[key] for key in ("handle", "row_id") if key in kwargs},
                **{key: answer["base"].get(key) for key in ROUTE_FIELDS}}
    else:
        call = {"mode": "evaluate", "requested_mode": sheet["mode"],
                "process_config": {key: value for key, value in answer["base"].items() if value is not None}}
    if answer["ranges"]:
        call["ranges"] = dict(answer["ranges"])
    return call


def _call(sheet: Mapping[str, Any], kwargs: Mapping[str, Any], label: str, plant: Mapping[str, Any]) -> dict:
    """One engine call for one plant (or the route), confirmed; an exception becomes that plant's failed row."""
    if sheet["mode"] == "route":
        call = {key: value for key, value in kwargs.items() if key not in {"mode", "confirm_live_tea", *ROUTE_FIELDS}}
        call.update(mode="route", confirm_live_tea=True, **dict(plant))
    else:
        call = {"mode": "evaluate", "process_config": {**plant, "label": label}, "confirm_live_tea": True,
                **{key: kwargs[key] for key in _PLANT_SCALARS if key in kwargs}}
    try:
        envelope = json.loads(tea.evaluate_process(**call))
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if not isinstance(data, dict):
            raise ValueError("the engine returned no data")
        return data
    except Exception as error:  # one plant's failure is its row, not the run's end
        return {"success": False, "error": f"{type(error).__name__}: {error}", "error_code": "tool_exception"}


@dataclass
class Turn:
    """The web turn a dispatch belongs to: its chat's panel, the chat (whose mode decides), and the turn's stream.
    idle() gives the answer's place among those running at once (web.TurnSlots) back until it ends."""

    panel: Panel
    app: Any
    send: Callable[[dict[str, Any]], None]
    declined: bool = False
    idle: Callable[[], ContextManager[None]] = nullcontext


TURN: ContextVar[Turn | None] = ContextVar("dissolve_web_tea_turn", default=None)


def _reference_plant() -> dict[str, Any]:
    """The stored reference plant (LDPE in dodecane, C1) in the public vocabulary: its twelve design-point fields."""
    record = next(r for r in tea._records() if r.get("label") == REFERENCE_PLANT)
    public = dict(tea._DESIGN_POINT_PUBLIC_FIELDS)
    return {public[key]: value for key, value in record["config"].items() if key in public}


def process_sheet(turn: Turn) -> str:
    """/process in the web app, the TEA sheet button: the panel without the model, to check the defaults, edit a
    plant and run it. It opens with the plant last run in this chat, or else the stored reference plant, and runs as a
    panel the model opened runs; the result is issued into the chat's session as the model's own would be. The command
    answers with what ran."""
    if agent.tea_switched_off():
        return "Live TEA is switched off on this deployment, so there is no TEA panel."
    if (blocker := tea._live_tea_blocker()) is not None:
        return str(blocker.get("error") or "Live TEA is not available here.")
    ran = {name: value for name, value in (turn.panel.previous.get("ran") or {}).items() if value is not None}
    base, label = (ran, "previous") if ran else (_reference_plant(), "reference")
    kwargs = {"mode": "evaluate", "process_config": base}
    try:
        sheet = build_sheet("evaluate", kwargs, turn.panel.previous)
    except PassThrough:
        return "The TEA panel could not open with that plant."
    sheet["origin"] = {name: label if source == "model" else source for name, source in sheet["origin"].items()}
    sheet["opened"] = label  # the panel says where the plant came from: no model proposed it
    with session.bind_tool_session(turn.app.session):
        payload = turn.panel.ask(turn, sheet, "evaluate_process", kwargs)
    turn.app._save()
    data = payload.get("data") or {}
    if payload.get("refusal") == "process_confirmation_aborted":
        return "Closed the TEA panel without running a plant."
    if "panel" not in data:
        return f"The TEA run stopped: {data.get('error') or payload.get('refusal') or 'no result came back'}."
    note = data["panel"]
    handle = f" (result {payload['handle']})" if payload.get("handle") else ""
    stopped = ", stopped early" if note.get("stopped_early") else ""
    return (f"Ran {note.get('plants_run')} of {note.get('plants_planned')} plants in the TEA panel{stopped}; the rows "
            f"are in the panel{handle}.")


def _applies(turn: Turn | None, name: str, kwargs: Mapping[str, Any]) -> bool:
    if turn is None or name != "evaluate_process" or getattr(turn.app, "mode", "review") != "review":
        return False
    if str(kwargs.get("mode") or "").strip().casefold() not in PLANT_MODES:
        return False
    return not agent.tea_switched_off() and tea._live_tea_blocker() is None  # else the engine refuses, as before


def panel_dispatch(original: Callable[..., Any], name: str, kwargs: dict[str, Any]) -> Any:
    turn = TURN.get()
    if not _applies(turn, name, kwargs):
        return original(name, **kwargs)
    if turn.declined:
        return _aborted("The person already closed a TEA panel in this answer; answer without new TEA numbers.")
    mode = str(kwargs.get("mode")).strip().casefold()
    try:
        sheet = build_sheet(mode, {**kwargs, "mode": mode}, turn.panel.previous)
    except PassThrough:
        return original(name, **kwargs)
    return turn.panel.ask(turn, sheet, name, kwargs)


def install() -> None:
    """Wrap agent.dispatch once per process; outside a web turn with a panel the wrapper is the original call."""
    if getattr(agent.dispatch, "_web_tea", False):
        return
    original = agent.dispatch

    def dispatch(name: str, **kwargs: Any) -> Any:
        return panel_dispatch(original, name, kwargs)

    dispatch._web_tea = True  # type: ignore[attr-defined]
    dispatch.__wrapped__ = original  # type: ignore[attr-defined]
    agent.dispatch = dispatch
