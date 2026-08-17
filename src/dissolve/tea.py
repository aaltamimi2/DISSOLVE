"""Route-aware TEA/LCA over cached or subprocess-isolated BioSTEAM evidence."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from . import tea_contracts, tea_worker
from . import thermodynamics as thermo
from .contracts import parse_tool_result, tool_error, tool_success
from .session import (
    candidate_evidence, current_tool_session,
    resolve_candidate_argument,
)

_ASSET = Path(str(files("dissolve").joinpath("data/tea_cache.json.gz")))
_ASSET_SHA256 = "f95dff48c68d57543e472ece51b59f4d807326cee432f1a1138029efcee12173"
_CONFIG_FIELDS = (
    "solvent", "target_plastic", "target_plastic_percent",
    "processing_capacity", "energy_case", "dissolution_temperature_c",
    "precipitation_temperature_c", "solvent_price", "solvent_loss_pct",
    "feedstock_distance_km", "dissolution_capacity", "labor_cost",
)
_NUMERIC_FIELDS = set(_CONFIG_FIELDS) - {"solvent", "target_plastic", "energy_case"}
_TEA_WORKER_PYTHON_ENV = "DISSOLVE_TEA_PYTHON"
_ENERGY_CASES = {
    "C1": "on-site boiler and turbogenerator (CHP)",
    "C2": "grid electricity with no on-site utilities",
    "C3": "grid electricity plus on-site boiler",
}
_REQUESTED_METRIC_ALIASES = {
    "minimum_selling_price": "msp", "capital_cost": "tci",
    "total_capital_investment": "tci", "capex": "tci",
    "operating_cost": "aoc", "annual_operating_cost": "aoc", "opex": "aoc",
    "cost_per_kg": "msp", "capital": "tci",
}
_LEGACY_OPERATING_HOURS = 350.4 * 24.0
_METRICS = {
    "msp_usd_per_kg": ("tea", "msp_usd_per_kg", "USD/kg product"),
    "tci_usd": ("tea", "tci_usd", "USD"),
    "aoc_usd_per_yr": ("tea", "aoc_usd_per_yr", "USD/yr"),
    "gwp_kg_co2e_per_kg": ("lca", "gwp_kg_co2e_per_kg", "kg CO2e/kg product"),
    "total_energy_mj_per_kg": ("operations", "total_energy_mj_per_kg", "MJ/kg product"),
}
_ADMITTED_RECORD_METRICS = {
    "msp": ("economics", "msp_usd_per_kg", "USD/kg product"),
    "tci": ("economics", "tci_usd", "USD"),
    "aoc": ("economics", "aoc_usd_per_yr", "USD/yr"),
    "gwp": ("lca", "gwp_kg_co2e_per_kg", "kg CO2e/kg product"),
    "etox": ("lca", "etox_ctue_per_kg", "CTUe/kg product"),
    "htc": ("lca", "htc_ctuh_per_kg", "CTUh/kg product"),
    "htnc": ("lca", "htnc_ctuh_per_kg", "CTUh/kg product"),
    "electricity": (
        "operations", "electricity_consumed_mj_per_kg", "MJ/kg product",
    ),
    "heating": ("operations", "heating_duty_mj_per_kg", "MJ/kg product"),
    "cooling": ("operations", "cooling_duty_mj_per_kg", "MJ/kg product"),
    "energy": ("operations", "total_energy_mj_per_kg", "MJ/kg product"),
}
_ADMITTED_RECORD_METRIC_ALIASES = {
    **_REQUESTED_METRIC_ALIASES,
    "gwp_per_kg": "gwp",
    "global_warming": "gwp",
    "global_warming_potential": "gwp",
    "cost_per_kg": "msp",
    "capital": "tci",
    "ecotoxicity": "etox",
    "human_toxicity_carcinogenic": "htc",
    "human_toxicity_noncarcinogenic": "htnc",
    "total_energy": "energy",
    "energy_per_kg": "energy",
}
_SENSITIVITY_AXIS_ALIASES = {
    item.name: item.aliases
    for item in tea_contracts.TEA_SENSITIVITY_AXIS_REGISTRY
}
_SENSITIVITY_LABEL_TOKEN_BY_AXIS = dict(
    tea_contracts.TEA_SENSITIVITY_CACHE_TOKEN_BY_AXIS,
)
TeaSensitivityAxis = Literal.__getitem__(
    tea_contracts.CANONICAL_TEA_SENSITIVITY_AXES,
)
TeaSensitivityLevelSelector = Literal.__getitem__(
    tea_contracts.CANONICAL_TEA_SENSITIVITY_LEVEL_SELECTORS,
)
TeaRecordForm = Literal.__getitem__(
    tea_contracts.CANONICAL_TEA_RECORD_FORMS,
)


def _validate_tea_plan(
    calls: list[dict[str, Any]], _original_query: str,
    typed_context: dict[str, Any],
) -> tuple[str, ...]:
    """Keep specialist metric tokens aligned with the explicit user request."""
    recognized_calls = [
        item for item in calls
        if item.get("name") in {
            "evaluate_stored_route_tea_lca",
            "evaluate_tea_lca_scenarios",
            "analyze_tea_sensitivity",
            "lookup_admitted_process_records",
        }
    ]
    call = recognized_calls[0] if recognized_calls else None
    if call is None:
        return ()
    tool_name = str(call.get("name"))
    arguments = call.get("args") or {}
    if not isinstance(arguments, dict):
        return (f"{tool_name} arguments must be one JSON object.",)
    violations = []
    declared = typed_context.get("declared_deliverable")
    typed_deliverable = declared if isinstance(declared, dict) else {}
    boundary = (
        tea_contracts.resolve_tea_tool_boundary(
            typed_deliverable,
            typed_context,
            typed_context.get("declared_request_kind"),
            typed_context.get("declared_request_units") or (),
        )
        if typed_deliverable.get("kind") == "tea_lca_analysis"
        else None
    )
    if boundary is not None:
        if boundary.tool_name is None:
            violations.append(
                "component=specialist_plan.tool_name: no TEA tool is "
                "admissible until the root declaration resolves "
                + ", ".join(boundary.missing_input_fields)
                + "; return the named D4 clarification instead of a tool call."
            )
        elif tool_name != boundary.tool_name:
            violations.append(
                "component=specialist_plan.tool_name: replace "
                f"{tool_name} with {boundary.tool_name}; manifest mode "
                f"{boundary.mode} owns this deliverable."
            )
    asks_route_variant_comparison = bool(
        (boundary is None or boundary.mode == "route_integrated")
        and typed_context.get(
            "declared_request_kind"
        ) == "tea_route_variant_comparison"
    )
    if asks_route_variant_comparison and arguments.get("compare_route_variants") is not True:
        violations.append(
            "Set compare_route_variants=true to evaluate the stored original and "
            "substituted routes on one basis; do not reconstruct either route."
        )
    supplied = arguments.get("requested_metrics")
    if supplied is None:
        return tuple(violations)
    normalized = {
        _REQUESTED_METRIC_ALIASES.get(token, token)
        for value in supplied if (
            token := str(value).strip().casefold().replace("-", "_").replace(" ", "_")
        )
    }
    allowed = (
        set(_ADMITTED_RECORD_METRICS)
        if tool_name == "lookup_admitted_process_records"
        else {"msp", "tci", "aoc", "gwp", "energy"}
    )
    expected = {
        _REQUESTED_METRIC_ALIASES.get(token, token)
        for value in typed_context.get("declared_requested_metrics") or []
        if (
            token := str(value).strip().casefold()
            .replace("-", "_").replace(" ", "_")
        )
    }
    invalid = sorted(normalized - allowed)
    if invalid:
        violations.append(
            "requested_metrics contains unsupported values " + str(invalid)
            + "; use only " + ", ".join(sorted(allowed)) + "."
        )
    if expected and normalized != expected:
        violations.append(
            "Preserve exactly the explicitly requested metrics: "
            + str(sorted(expected)) + "."
        )
    current_polymers = list(dict.fromkeys((
        *(typed_deliverable.get("target_polymers") or ()),
        *(typed_deliverable.get("polymers") or ()),
        *(typed_deliverable.get("feed_polymers") or ()),
    ))) if boundary is not None else list(dict.fromkeys(
        identity
        for _, identity in thermo.find_polymer_mentions(_original_query)
    ))
    if tool_name == "lookup_admitted_process_records":
        supplied_targets = (
            arguments.get("target_polymer")
            if isinstance(arguments.get("target_polymer"), list)
            else [arguments.get("target_polymer")]
            if arguments.get("target_polymer")
            else []
        )
        missing_targets = [
            polymer for polymer in current_polymers
            if not any(
                _key(polymer) == _key(supplied)
                for supplied in supplied_targets
            )
        ]
        if missing_targets:
            violations.append(
                "component=specialist_plan.lookup_admitted_process_records."
                "target_polymer: preserve every selected target polymer "
                "exactly: " + ", ".join(missing_targets) + "."
            )
        selectors = (
            boundary.implicit_selectors
            if boundary is not None
            else tea_contracts.TeaImplicitRecordSelectors()
        )
        supplied_cases = tuple(dict.fromkeys(
            tea_contracts.canonical_tea_energy_case(value)
            for value in arguments.get("energy_cases") or ()
        ))
        if (
            selectors.energy_cases
            and supplied_cases != selectors.energy_cases
        ):
            violations.append(
                "component=specialist_plan.lookup_admitted_process_records."
                "energy_cases: preserve the selected admitted energy cases "
                "exactly: " + ", ".join(selectors.energy_cases) + "."
            )
        supplied_axes = tuple(dict.fromkeys(
            tea_contracts.canonical_tea_sensitivity_axis(value)
            for value in arguments.get("sensitivity_axes") or ()
        ))
        if (
            selectors.sensitivity_axes
            and supplied_axes != selectors.sensitivity_axes
        ):
            violations.append(
                "component=specialist_plan.lookup_admitted_process_records."
                "sensitivity_axes: preserve the selected admitted sensitivity "
                "axes exactly: "
                + ", ".join(selectors.sensitivity_axes) + "."
            )
        supplied_capacity = arguments.get(
            "processing_capacity_mt_per_yr",
        )
        if (
            selectors.processing_capacity_mt_per_yr is not None
            and (
                supplied_capacity is None
                or not math.isclose(
                    float(supplied_capacity),
                    selectors.processing_capacity_mt_per_yr,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            )
        ):
            violations.append(
                "component=specialist_plan.lookup_admitted_process_records."
                "processing_capacity_mt_per_yr: preserve the selected exact "
                "capacity "
                f"{selectors.processing_capacity_mt_per_yr:g}."
            )
        return tuple(violations)
    stored_route = typed_context.get("last_route") or {}
    stored_route_owned = bool(
        stored_route
        and typed_context.get("declared_request_kind")
        == "tea_stored_route"
    )
    if stored_route_owned and arguments.get("feed_polymers"):
        violations.append(
            "Remove feed_polymers for this stored-route request: the typed route "
            "owns polymer identity. Preserve newly supplied numeric composition "
            "only in feed_mass_fractions."
        )
    asks_feed_economics = (
        typed_context.get("declared_request_kind")
        == "tea_feed_economics"
    )
    if asks_feed_economics:
        supplied_feed = list(arguments.get("feed_polymers") or [])
        fraction_feed = list((arguments.get("feed_mass_fractions") or {}).keys())
        planned_feed = supplied_feed or fraction_feed
        missing = [
            polymer for polymer in current_polymers
            if not any(_key(polymer) == _key(item) for item in planned_feed)
        ]
        if missing:
            violations.append(
                "Preserve every polymer named in the current economics request in "
                "feed_polymers (or feed_mass_fractions when fractions were supplied): "
                + ", ".join(missing) + "."
            )
    return tuple(violations)


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


@lru_cache(maxsize=1)
def cache_payload() -> dict[str, Any]:
    raw = _ASSET.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _ASSET_SHA256:
        raise RuntimeError(f"TEA cache checksum mismatch: {digest}")
    return json.loads(gzip.decompress(raw))


def _records() -> list[dict[str, Any]]:
    return list(cache_payload().get("records") or [])


def _solvent_assumption(solvent: str) -> Optional[dict[str, Any]]:
    target = thermo.resolve_solvent(solvent)
    if target is None:
        return None
    for row in (cache_payload().get("solvent_assumptions") or {}).get("records") or []:
        for candidate in (row.get("name_cosmobase"), row.get("name_biosteam")):
            if candidate and thermo.resolve_solvent(str(candidate)) == target:
                return dict(row)
    return None


def _record_for_pair(polymer: str, solvent: str) -> Optional[dict[str, Any]]:
    matches = [
        item for item in _records()
        if _key(item["config"].get("target_plastic")) == _key(polymer)
        and _key(item["config"].get("solvent")) == _key(solvent)
        and str(item["config"].get("energy_case") or "").upper() == "C1"
    ]
    preferred = [item for item in matches if str(item.get("label", "")).endswith("route-c1")]
    return (preferred or matches or [None])[0]


def _resolve_polymer(value: Any) -> str:
    supplied = str(value or "").strip()
    expanded = thermo.expand_polymer_identity(supplied)
    if len(expanded) != 1:
        raise ValueError(f"Unsupported target polymer: {supplied or '(missing)'}")
    return expanded[0]


def _resolve_solvent(value: Any) -> str:
    supplied = str(value or "").strip()
    resolved = thermo.resolve_solvent(supplied)
    if resolved is None:
        raise ValueError(f"Unsupported solvent: {supplied or '(missing)'}")
    return thermo.canonical_solvent_name(resolved)


class _MissingScenarioBasis(ValueError):
    """A valid route condition lacks an admitted process input."""


def _scenario_config(scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scenario, dict):
        raise ValueError("Each scenario must be an object")
    polymer = _resolve_polymer(
        scenario.get("target_polymer") or scenario.get("target_plastic")
    )
    solvent = _resolve_solvent(scenario.get("solvent"))
    base_record = _record_for_pair(polymer, solvent)
    defaults = copy.deepcopy((base_record or {}).get("config") or {})
    aliases = {
        "target_mass_percent": "target_plastic_percent",
        "processing_capacity_mt_per_yr": "processing_capacity",
        "dissolution_temp_c": "dissolution_temperature_c",
        "precipitation_temp_c": "precipitation_temperature_c",
    }
    supplied = {aliases.get(key, key): value for key, value in scenario.items()}
    config = {
        **{
            "target_plastic_percent": 60.0,
            "processing_capacity": 20_000.0,
            "energy_case": "C1",
            "precipitation_temperature_c": 25.0,
            "solvent_loss_pct": 0.01,
            "feedstock_distance_km": 0.0,
            "dissolution_capacity": 3.0,
            "labor_cost": 120_000.0,
        },
        **defaults,
        **{key: supplied[key] for key in _CONFIG_FIELDS if key in supplied},
        "target_plastic": polymer,
        "solvent": solvent,
    }
    config["energy_case"] = str(config.get("energy_case") or "C1").upper()
    if config["energy_case"] not in _ENERGY_CASES:
        raise ValueError("energy_case must be C1, C2, or C3")
    if config.get("dissolution_temperature_c") is None:
        raise ValueError(
            "dissolution_temperature_c is required when no cached pair default exists"
        )
    if config.get("solvent_price") is None:
        assumption = _solvent_assumption(solvent)
        if assumption is not None:
            config["solvent_price"] = assumption["price_usd_per_kg"]
        else:
            raise _MissingScenarioBasis(
                f"No admitted solvent-price basis is available for {solvent}."
            )
    for field in _NUMERIC_FIELDS:
        config[field] = _finite(config.get(field), field)
    if not 0 < config["target_plastic_percent"] <= 100:
        raise ValueError("target_plastic_percent must be above 0 and at most 100")
    if config["processing_capacity"] <= 0 or config["dissolution_capacity"] <= 0:
        raise ValueError("processing and dissolution capacity must be positive")
    if config["solvent_price"] < 0 or config["solvent_loss_pct"] < 0:
        raise ValueError("solvent price and loss must be nonnegative")
    return {key: config[key] for key in _CONFIG_FIELDS}


def _config_key(config: dict[str, Any]) -> str:
    normalized = {
        key: (
            _key(config[key]) if key in {"solvent", "target_plastic"}
            else str(config[key]).upper() if key == "energy_case"
            else round(float(config[key]), 10)
        )
        for key in _CONFIG_FIELDS
    }
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=1)
def _cache_index() -> dict[str, dict[str, Any]]:
    return {_config_key(item["config"]): item for item in _records()}


def _tea_worker_python() -> str:
    """Resolve the isolated worker interpreter without changing the default."""
    configured = str(os.getenv(_TEA_WORKER_PYTHON_ENV) or "").strip()
    return os.path.expanduser(configured) if configured else sys.executable


def _tea_worker_environment() -> dict[str, str]:
    """Expose this source tree and the optional process model to the worker."""
    environment = os.environ.copy()
    python_paths = [str(Path(__file__).resolve().parents[1])]
    if path := str(os.getenv("DISSOLVE_PLASTICS_PATH") or "").strip():
        python_paths.append(str(Path(path).expanduser().resolve()))
    python_paths.extend(
        item for item in environment.get("PYTHONPATH", "").split(os.pathsep)
        if item
    )
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(python_paths))
    return environment


def live_engine_status() -> dict[str, Any]:
    path = str(os.getenv("DISSOLVE_PLASTICS_PATH") or "").strip()
    configured_python = bool(
        str(os.getenv(_TEA_WORKER_PYTHON_ENV) or "").strip()
    )
    worker_python = _tea_worker_python()
    if not configured_python and sys.version_info < (3, 12):
        return {
            "available": False, "reason": "python_version",
            "detail": "Live BioSTEAM execution requires Python 3.12 or newer.",
        }
    if configured_python and shutil.which(worker_python) is None:
        return {
            "available": False, "reason": "worker_interpreter_missing",
            "detail": (
                f"{_TEA_WORKER_PYTHON_ENV} must identify an executable "
                "Python interpreter."
            ),
        }
    if path and not Path(path).expanduser().exists():
        return {
            "available": False, "reason": "plastics_path_missing",
            "detail": f"DISSOLVE_PLASTICS_PATH does not exist: {path}",
        }
    try:
        import importlib.util

        spec = importlib.util.find_spec("plastics.strap")
    except (ImportError, ModuleNotFoundError, ValueError):
        spec = None
    if not path and spec is None:
        return {
            "available": False, "reason": "process_model_missing",
            "detail": "Install the compatible unpublished plastics 0.1.4 model or set DISSOLVE_PLASTICS_PATH.",
        }
    return {"available": True, "reason": None, "detail": "subprocess engine ready"}


def _live(config: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    status = live_engine_status()
    if not status["available"]:
        return {"success": False, "error": status["detail"], "error_type": status["reason"]}
    environment = _tea_worker_environment()
    try:
        completed = subprocess.run(
            [_tea_worker_python(), "-m", "strap.tea_worker", json.dumps(config)],
            capture_output=True, text=True, timeout=timeout_seconds,
            env=environment, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "BioSTEAM subprocess timed out", "error_type": "timeout"}
    except OSError as error:
        return {
            "success": False,
            "error": f"BioSTEAM worker interpreter could not start: {error}",
            "error_type": "worker_interpreter_unavailable",
        }
    stdout, stderr = (completed.stdout or "")[:10_000_000], (completed.stderr or "")[-2_000:]
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "success": False, "error": "BioSTEAM worker returned invalid JSON",
            "error_type": "invalid_worker_output", "stderr": stderr,
        }
    if completed.returncode and result.get("success") is not True:
        result.setdefault("stderr", stderr)
    return result


def _run(config: dict[str, Any], engine_mode: str, timeout_seconds: int) -> dict[str, Any]:
    mode = str(engine_mode or "auto").strip().casefold()
    if mode not in {"auto", "cache", "live"}:
        return {"success": False, "error": "engine_mode must be auto, cache, or live", "error_type": "invalid_engine_mode"}
    record = _cache_index().get(_config_key(config))
    if mode != "live" and record:
        result = copy.deepcopy(record["result"])
        operations = result.get("operations") or {}
        for field in (
            "electricity_consumed_mj_per_kg", "heating_duty_mj_per_kg",
            "cooling_duty_mj_per_kg", "total_energy_mj_per_kg",
            "electricity_intensity_mj_per_kg",
        ):
            if operations.get(field) is not None:
                operations[field] = float(operations[field]) / _LEGACY_OPERATING_HOURS
        result["energy_normalization"] = {
            "status": "corrected_legacy_annual_hour_basis",
            "operating_hours_per_year": _LEGACY_OPERATING_HOURS,
            "basis": "BioSTEAM annual kWh-or-kJ divided by annual resin kg",
        }
        result.update({
            "engine_mode": "cache", "cache_match_status": "exact",
            "cache_record_label": record["label"], "config": config,
        })
        return result
    if mode == "cache":
        return {
            "success": False, "error": "No exact cached simulation matches this configuration.",
            "error_type": "cache_miss", "engine_mode": "cache",
            "cache_match_status": "miss", "config": config,
        }
    result = _live(config, timeout_seconds)
    result.update({
        "engine_mode": "live", "cache_match_status": "bypassed" if mode == "live" else "miss",
        "config": config,
    })
    return result


def _same_config(
    left: dict[str, Any], right: dict[str, Any], varying: set[str],
) -> bool:
    for key in _CONFIG_FIELDS:
        if key in varying:
            continue
        if key in {"solvent", "target_plastic"}:
            if _key(left.get(key)) != _key(right.get(key)):
                return False
        elif key == "energy_case":
            if str(left.get(key)).upper() != str(right.get(key)).upper():
                return False
        elif not math.isclose(float(left.get(key)), float(right.get(key)), rel_tol=0, abs_tol=1e-9):
            return False
    return True


def _record_metric(record: dict[str, Any], section: str, field: str) -> float:
    value = float((record["result"].get(section) or {})[field])
    if section == "operations" and field in {
        "electricity_consumed_mj_per_kg", "heating_duty_mj_per_kg",
        "cooling_duty_mj_per_kg", "total_energy_mj_per_kg",
    }:
        value /= _LEGACY_OPERATING_HOURS
    return value


def _power_estimate(
    rows: list[dict[str, Any]], capacity: float, section: str, field: str,
) -> tuple[float, float]:
    points = [
        (math.log(float(row["config"]["processing_capacity"])),
         math.log(_record_metric(row, section, field)))
        for row in rows if _record_metric(row, section, field) > 0
    ]
    if len(points) < 2:
        raise ValueError("At least two cached scale points are required")
    x_mean = statistics.mean(point[0] for point in points)
    y_mean = statistics.mean(point[1] for point in points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    if denominator <= 0:
        raise ValueError("Cached scale points must use distinct capacities")
    exponent = sum((x - x_mean) * (y - y_mean) for x, y in points) / denominator
    estimate = math.exp(y_mean + exponent * (math.log(capacity) - x_mean))
    return estimate, exponent


def _linear_slope(
    rows: list[dict[str, Any]], config_field: str, section: str, metric: str,
) -> float:
    points = [
        (float(row["config"][config_field]), _record_metric(row, section, metric))
        for row in rows
    ]
    if len({point[0] for point in points}) < 2:
        return 0.0
    x_mean = statistics.mean(point[0] for point in points)
    y_mean = statistics.mean(point[1] for point in points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    return sum((x - x_mean) * (y - y_mean) for x, y in points) / denominator


def _screening_estimate(
    config: dict[str, Any], failed_evaluation: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    analogs = [
        row for row in _records()
        if _key(row["config"].get("target_plastic")) == _key(config["target_plastic"])
        and str(row["config"].get("energy_case") or "").upper() == config["energy_case"]
    ]
    if not analogs:
        return {
            "success": False, "error": "No cached polymer analog is available.",
            "error_type": "surrogate_unavailable", "config": config,
        }
    def support(row: dict[str, Any]) -> int:
        return sum(
            sum(_same_config(item["config"], row["config"], {field}) for item in analogs)
            for field in (
                "processing_capacity", "solvent_price", "dissolution_temperature_c",
            )
        )

    base = min(analogs, key=lambda row: (
        -support(row),
        abs(float(row["config"]["target_plastic_percent"]) - config["target_plastic_percent"]),
        abs(float(row["config"]["dissolution_temperature_c"]) - config["dissolution_temperature_c"]),
        abs(math.log(float(row["config"]["processing_capacity"]) / config["processing_capacity"])),
    ))
    base_config = base["config"]
    scale_rows = [
        row for row in analogs
        if _same_config(row["config"], base_config, {"processing_capacity"})
    ]
    price_rows = [
        row for row in analogs
        if _same_config(row["config"], base_config, {"solvent_price"})
    ]
    temperature_rows = [
        row for row in analogs
        if _same_config(row["config"], base_config, {"dissolution_temperature_c"})
    ]
    metrics = {
        "msp_usd_per_kg": ("tea", "msp_usd_per_kg"),
        "tci_usd": ("tea", "tci_usd"),
        "aoc_usd_per_yr": ("tea", "aoc_usd_per_yr"),
        "gwp_kg_co2e_per_kg": ("lca", "gwp_kg_co2e_per_kg"),
        "total_energy_mj_per_kg": ("operations", "total_energy_mj_per_kg"),
    }
    estimates: dict[str, float] = {}
    exponents: dict[str, float] = {}
    try:
        for name, (section, field) in metrics.items():
            estimate, exponent = _power_estimate(
                scale_rows, config["processing_capacity"], section, field,
            )
            if section == "tea":
                estimate += _linear_slope(price_rows, "solvent_price", section, field) * (
                    config["solvent_price"] - float(base_config["solvent_price"])
                )
                estimate += _linear_slope(
                    temperature_rows, "dissolution_temperature_c", section, field,
                ) * (
                    config["dissolution_temperature_c"]
                    - float(base_config["dissolution_temperature_c"])
                )
            estimates[name] = max(0.0, estimate)
            exponents[name] = exponent
    except (KeyError, ValueError) as error:
        return {
            "success": False, "error": str(error),
            "error_type": "insufficient_surrogate_evidence", "config": config,
        }
    capacities = sorted(float(row["config"]["processing_capacity"]) for row in scale_rows)
    requested_capacity = float(config["processing_capacity"])
    nearest_capacity_ratio = (
        requested_capacity / capacities[0]
        if requested_capacity < capacities[0]
        else requested_capacity / capacities[-1]
        if requested_capacity > capacities[-1]
        else 1.0
    )
    price_values = {float(row["config"]["solvent_price"]) for row in price_rows}
    temperature_values = {
        float(row["config"]["dissolution_temperature_c"])
        for row in temperature_rows
    }
    assumptions = cache_payload().get("solvent_assumptions") or {}
    return {
        "success": True,
        "tea": {key: estimates[key] for key in (
            "msp_usd_per_kg", "tci_usd", "aoc_usd_per_yr",
        )},
        "lca": {"gwp_kg_co2e_per_kg": estimates["gwp_kg_co2e_per_kg"]},
        "operations": {"total_energy_mj_per_kg": estimates["total_energy_mj_per_kg"]},
        "engine_mode": "screening_estimate", "cache_match_status": "surrogate",
        "config": config,
        "energy_normalization": {
            "status": "corrected_legacy_annual_hour_basis",
            "operating_hours_per_year": _LEGACY_OPERATING_HOURS,
            "basis": "cached analog annual energy divided by annual product mass",
        },
        "estimate_basis": {
            "quality": "screening_only", "analog_record": base["label"],
            "analog_solvent": base_config["solvent"],
            "scale_record_labels": [row["label"] for row in scale_rows],
            "cached_capacity_range_mt_per_yr": capacities,
            "capacity_extrapolated": not capacities[0] <= requested_capacity <= capacities[-1],
            "capacity_ratio_to_nearest_cached_bound": nearest_capacity_ratio,
            "metric_scale_exponents": exponents,
            "solvent_price_usd_per_kg": config["solvent_price"],
            "solvent_price_adjustment_available": len(price_values) > 1,
            "temperature_adjustment_available": len(temperature_values) > 1,
            "solvent_assumption_source": assumptions.get("source"),
            "solvent_assumption_source_sha256": assumptions.get("source_sha256"),
            "selected_solvent_simulated": False,
            "solvent_substitution_reflected_in_lca": False,
            "fallback_from": {
                key: (failed_evaluation or {}).get(key)
                for key in ("engine_mode", "cache_match_status", "error_type", "error")
                if (failed_evaluation or {}).get(key) is not None
            },
        },
    }


def _comparison_row(label: str, result: dict[str, Any]) -> dict[str, Any]:
    tea, lca, operations = result.get("tea") or {}, result.get("lca") or {}, result.get("operations") or {}
    config = result.get("config") or {}
    return {
        "label": label, "success": bool(result.get("success")),
        "polymer": config.get("target_plastic") or result.get("target_plastic"),
        "solvent": config.get("solvent") or result.get("solvent"),
        "energy_case": config.get("energy_case") or result.get("energy_case"),
        "target_mass_percent": config.get("target_plastic_percent"),
        "processing_capacity_mt_per_yr": config.get("processing_capacity"),
        "dissolution_temperature_c": config.get("dissolution_temperature_c"),
        "precipitation_temperature_c": config.get("precipitation_temperature_c"),
        "msp_usd_per_kg": tea.get("msp_usd_per_kg"),
        "tci_usd": tea.get("tci_usd"), "aoc_usd_per_yr": tea.get("aoc_usd_per_yr"),
        "gwp_kg_co2e_per_kg": lca.get("gwp_kg_co2e_per_kg"),
        "water_consumed_m3_per_yr": operations.get("water_consumed_m3_yr"),
        "water_circulated_m3_per_yr": operations.get("water_circulated_m3_yr"),
        "electricity_mj_per_kg": operations.get("electricity_consumed_mj_per_kg"),
        "heating_mj_per_kg": operations.get("heating_duty_mj_per_kg"),
        "cooling_mj_per_kg": operations.get("cooling_duty_mj_per_kg"),
        "total_energy_mj_per_kg": operations.get("total_energy_mj_per_kg"),
        "waste_generated_kg_per_yr": operations.get("waste_generated_kg_yr"),
        "waste_diverted_kg_per_yr": operations.get("waste_diverted_kg_yr"),
        "unit_operations": operations.get("unit_operations"),
        "energy_normalization_status": (result.get("energy_normalization") or {}).get("status"),
        "equipment_count": len((result.get("process_details") or {}).get("equipment") or []),
        "stream_count": len((result.get("process_details") or {}).get("stream_mass_balance") or []),
        "engine_mode": result.get("engine_mode"),
        "cache_record_label": result.get("cache_record_label"),
        "estimate_basis": result.get("estimate_basis"),
        **(
            {"lca_coverage": copy.deepcopy(result["lca_coverage"])}
            if result.get("lca_coverage") else {}
        ),
        **({"error": result.get("error")} if not result.get("success") else {}),
    }


def _live_toxicity_data_gaps(
    results: Sequence[dict[str, Any]],
) -> list[str]:
    """Disclose live toxicity omissions on the public tool payload."""
    missing = [
        result for result in results
        if (result.get("lca_coverage") or {}).get("toxicity_metrics_status")
        != "available"
        and result.get("engine_mode") == "live"
        and result.get("success") is True
    ]
    if not missing:
        return []
    return [
        "Live toxicity metrics were omitted because complete characterization "
        "factors were not supplied for every contributing process input; no "
        "zero or partial-impact toxicity value is reported."
    ]


def _provenance(modes: Sequence[str]) -> dict[str, Any]:
    generator = cache_payload().get("generator") or {}
    return {
        "engine_modes": sorted(set(modes)), "cache_asset_sha256": _ASSET_SHA256,
        "cache_records": len(_records()), "generation_v10_commit": generator.get("v10_commit"),
        "worker_sha256": generator.get("worker_sha256"),
        "process_model_sha256": generator.get("process_model_sha256"),
        "biosteam_version": generator.get("biosteam_version"),
        "thermosteam_version": generator.get("thermosteam_version"),
        "energy_normalization": "legacy annual/hour basis corrected at v11 cache boundary",
    }


def _process_details(label: str, result: dict[str, Any]) -> Optional[dict[str, Any]]:
    details = result.get("process_details") or {}
    if not details:
        return None
    return {
        "label": label,
        "feed_mass_flow_kg_per_hr": details.get("feed_mass_flow_kg_per_hr"),
        "product_mass_flow_kg_per_hr": details.get("product_mass_flow_kg_per_hr"),
        "mass_balance_closure_kg_per_hr": details.get("mass_balance_closure_kg_per_hr"),
        "stream_mass_balance": list(details.get("stream_mass_balance") or [])[:8],
        "equipment": list(details.get("equipment") or [])[:8],
    }


def _lookup_token(value: Any) -> str:
    return tea_contracts.normalize_tea_vocabulary(value)


def _sensitivity_axis_for_record(label: str) -> str | None:
    normalized = f"-{str(label).casefold().replace('_', '-')}-"
    return next((
        axis for axis, token in _SENSITIVITY_LABEL_TOKEN_BY_AXIS.items()
        if f"-{token}-" in normalized
    ), None)


def _normalize_sensitivity_labels(
    values: Sequence[str] | None,
    axes_values: Sequence[str] | None = None,
) -> tuple[set[str], set[str], list[str]]:
    axes: set[str] = set()
    exact_labels: set[str] = set()
    unknown: list[str] = []
    available_labels = {
        str(record.get("label") or "").casefold(): str(record.get("label") or "")
        for record in _records()
    }
    alias_to_axis = {
        _lookup_token(alias): axis
        for axis, aliases in _SENSITIVITY_AXIS_ALIASES.items()
        for alias in (axis, *aliases)
    }
    for supplied in (*tuple(values or ()), *tuple(axes_values or ())):
        text = str(supplied or "").strip()
        if not text:
            continue
        if text.casefold() in available_labels:
            exact_labels.add(available_labels[text.casefold()])
            continue
        normalized = _lookup_token(text)
        axis = alias_to_axis.get(normalized)
        if axis is None:
            axis = next((
                candidate_axis
                for alias, candidate_axis in alias_to_axis.items()
                if normalized in {
                    f"{alias}_low", f"{alias}_mid", f"{alias}_high",
                }
            ), None)
        if axis is None:
            unknown.append(text)
        else:
            axes.add(axis)
    return axes, exact_labels, unknown


def _normalized_admitted_record(record: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(record.get("config") or {})
    result = _run(config, "cache", 1)
    label = str(record.get("label") or "")
    axis = _sensitivity_axis_for_record(label)
    level = (
        label.rsplit("-", 1)[-1].casefold()
        if axis is not None and "-" in label else None
    )
    return {
        "record_id": label,
        "record_group": "sensitivity" if axis else "energy_case",
        **({"sensitivity_axis": axis, "sensitivity_level": level} if axis else {}),
        "config": config,
        "economics": copy.deepcopy(result.get("tea") or {}),
        "lca": copy.deepcopy(result.get("lca") or {}),
        "operations": copy.deepcopy(result.get("operations") or {}),
        "energy_normalization": copy.deepcopy(
            result.get("energy_normalization") or {},
        ),
    }


def _admitted_record_summary(record: dict[str, Any]) -> dict[str, Any]:
    config = record["config"]
    summary = {
        "label": record["record_id"],
        "record_group": record["record_group"],
        "polymer": config.get("target_plastic"),
        "solvent": config.get("solvent"),
        "energy_case": config.get("energy_case"),
        "target_mass_percent": config.get("target_plastic_percent"),
        "processing_capacity_mt_per_yr": config.get("processing_capacity"),
        "dissolution_temperature_c": config.get(
            "dissolution_temperature_c",
        ),
        "precipitation_temperature_c": config.get(
            "precipitation_temperature_c",
        ),
        "solvent_price_usd_per_kg": config.get("solvent_price"),
        "solvent_loss_pct": config.get("solvent_loss_pct"),
        "feedstock_distance_km": config.get("feedstock_distance_km"),
        "dissolution_capacity": config.get("dissolution_capacity"),
        "labor_cost_usd_per_employee_yr": config.get("labor_cost"),
        "energy_normalization_status": record[
            "energy_normalization"
        ].get("status"),
        "engine_mode": "cache",
        "cache_match_status": "exact",
    }
    if record.get("sensitivity_axis"):
        summary.update({
            "sensitivity_axis": record["sensitivity_axis"],
            "sensitivity_level": record.get("sensitivity_level"),
        })
    for _metric, (section, field, _unit) in (
        _ADMITTED_RECORD_METRICS.items()
    ):
        value = (record.get(section) or {}).get(field)
        if value is not None:
            summary[field] = value
    return summary


def lookup_admitted_process_records(
    target_polymer: str | list[str],
    solvent: Optional[str] = None,
    energy_cases: Optional[list[Literal["C1", "C2", "C3"]]] = None,
    sensitivity_labels: Optional[list[str]] = None,
    sensitivity_axes: Optional[list[TeaSensitivityAxis]] = None,
    sensitivity_level_selector: Optional[
        TeaSensitivityLevelSelector
    ] = None,
    record_form: TeaRecordForm = "per_record",
    dissolution_temperature_c: Optional[float] = None,
    processing_capacity_mt_per_yr: Optional[float] = None,
    requested_metrics: Optional[
        list[
            Literal[
                "msp", "tci", "aoc", "gwp", "etox", "htc", "htnc",
                "electricity", "heating", "cooling", "energy",
            ]
        ]
    ] = None,
) -> str:
    """Query exact admitted BioSTEAM records without requiring a stored route.

    The target polymer is required; solvent, energy cases, exact dissolution
    temperature, exact plant capacity, and sensitivity labels are optional
    filters. With no sensitivity labels, the canonical C1/C2/C3 energy-case
    records are returned. Sensitivity labels accept the seven admitted axes
    (solvent price, plant scale, solvent loss, dissolution temperature,
    precipitation temperature, feedstock distance, and feed composition) or an
    exact cache record label. Returned records retain their full configuration,
    economics, LCA, and normalized operations payloads. No interpolation,
    surrogate, or live provider is used.
    """
    tool = "lookup_admitted_process_records"
    try:
        supplied_polymers = (
            target_polymer
            if isinstance(target_polymer, list)
            else [target_polymer]
        )
        polymers = list(dict.fromkeys(
            _resolve_polymer(value) for value in supplied_polymers
        ))
        if not polymers:
            raise ValueError("At least one target polymer is required")
        resolved_solvent = _resolve_solvent(solvent) if solvent else None
        temperature = (
            _finite(dissolution_temperature_c, "dissolution_temperature_c")
            if dissolution_temperature_c is not None else None
        )
        capacity = (
            _finite(
                processing_capacity_mt_per_yr,
                "processing_capacity_mt_per_yr",
            )
            if processing_capacity_mt_per_yr is not None else None
        )
        if capacity is not None and capacity <= 0:
            raise ValueError("processing_capacity_mt_per_yr must be positive")
    except ValueError as error:
        return tool_error(
            tool, str(error), error_code="invalid_admitted_record_query",
        )

    metrics = list(dict.fromkeys(
        _ADMITTED_RECORD_METRIC_ALIASES.get(token, token)
        for value in requested_metrics or list(_ADMITTED_RECORD_METRICS)
        if (
            token := _lookup_token(value)
        )
    ))
    invalid_metrics = sorted(
        set(metrics) - set(_ADMITTED_RECORD_METRICS),
    )
    if invalid_metrics:
        return tool_error(
            tool,
            "Unsupported admitted-record metrics: "
            + ", ".join(invalid_metrics) + ".",
            error_code="invalid_admitted_record_metrics",
            available_metrics=list(_ADMITTED_RECORD_METRICS),
        )
    requested_cases = list(dict.fromkeys(
        tea_contracts.canonical_tea_energy_case(value)
        for value in energy_cases or []
        if str(value or "").strip()
    ))
    invalid_cases = sorted(set(requested_cases) - set(_ENERGY_CASES))
    if invalid_cases:
        return tool_error(
            tool,
            "energy_cases must contain only C1, C2, or C3.",
            error_code="invalid_admitted_energy_case",
            invalid_energy_cases=invalid_cases,
        )
    axes, exact_labels, unknown_labels = _normalize_sensitivity_labels(
        sensitivity_labels, sensitivity_axes,
    )
    if unknown_labels:
        return tool_error(
            tool,
            "Unknown sensitivity labels: " + ", ".join(unknown_labels) + ".",
            error_code="unknown_admitted_sensitivity_label",
            available_sensitivity_axes=list(_SENSITIVITY_AXIS_ALIASES),
            available_sensitivity_labels=sorted(
                str(item.get("label") or "")
                for item in _records()
                if _sensitivity_axis_for_record(
                    str(item.get("label") or ""),
                )
            ),
        )
    canonical_level_selector = (
        tea_contracts.canonical_tea_sensitivity_level_selector(
            sensitivity_level_selector,
        )
        if sensitivity_level_selector is not None else None
    )
    if (
        canonical_level_selector is not None
        and canonical_level_selector
        not in tea_contracts.CANONICAL_TEA_SENSITIVITY_LEVEL_SELECTORS
    ):
        return tool_error(
            tool,
            "Unknown sensitivity level selector.",
            error_code="unknown_sensitivity_level_selector",
            available_sensitivity_level_selectors=list(
                tea_contracts.CANONICAL_TEA_SENSITIVITY_LEVEL_SELECTORS,
            ),
        )
    canonical_record_form = tea_contracts.canonical_tea_record_form(
        record_form,
    )
    if canonical_record_form not in tea_contracts.CANONICAL_TEA_RECORD_FORMS:
        return tool_error(
            tool,
            "Unknown admitted-record output form.",
            error_code="unknown_admitted_record_form",
            available_record_forms=list(
                tea_contracts.CANONICAL_TEA_RECORD_FORMS,
            ),
        )

    pair_records = [
        record for record in _records()
        if any(
            _key((record.get("config") or {}).get("target_plastic"))
            == _key(polymer)
            for polymer in polymers
        )
        and (
            resolved_solvent is None
            or _key((record.get("config") or {}).get("solvent"))
            == _key(resolved_solvent)
        )
    ]
    sensitivity_requested = bool(sensitivity_labels or sensitivity_axes)
    if sensitivity_requested:
        selected = [
            record for record in pair_records
            if (
                str(record.get("label") or "") in exact_labels
                or _sensitivity_axis_for_record(
                    str(record.get("label") or ""),
                ) in axes
            )
        ]
        baseline = next((
            record for record in pair_records
            if str(record.get("label") or "").casefold().endswith(
                "-route-c1",
            )
        ), None)
        if baseline is not None and baseline not in selected:
            selected.insert(0, baseline)
    else:
        cases = requested_cases or list(_ENERGY_CASES)
        selected = [
            record for record in pair_records
            if str(record.get("label") or "").casefold().endswith(tuple(
                f"-route-{case.casefold()}" for case in cases
            ))
        ]
    selected_levels = tea_contracts.tea_sensitivity_levels(
        canonical_level_selector,
    )
    if sensitivity_requested and selected_levels:
        by_axis: dict[str, list[dict[str, Any]]] = {}
        ungrouped: list[dict[str, Any]] = []
        for record in selected:
            axis = _sensitivity_axis_for_record(
                str(record.get("label") or ""),
            )
            if axis is None:
                ungrouped.append(record)
            else:
                by_axis.setdefault(axis, []).append(record)
        selected = list(ungrouped)
        for axis in tea_contracts.CANONICAL_TEA_SENSITIVITY_AXES:
            records = by_axis.get(axis, [])
            endpoint_records = [
                record for record in records
                if str(record.get("label") or "").casefold().rsplit(
                    "-", 1,
                )[-1] in selected_levels
            ]
            # Some admitted axes use ``mid`` for the lower measured endpoint.
            # Select the stored endpoint pair without relabeling source data.
            selected.extend(
                endpoint_records
                if len(endpoint_records) >= 2
                else (
                    records
                    if len(records) <= 2
                    else [records[0], records[-1]]
                )
            )
    if temperature is not None:
        selected = [
            record for record in selected
            if math.isclose(
                float(record["config"]["dissolution_temperature_c"]),
                temperature,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
    if capacity is not None:
        selected = [
            record for record in selected
            if math.isclose(
                float(record["config"]["processing_capacity"]),
                capacity,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
    if not selected:
        available_pair_configs = [{
            "record_id": str(record.get("label") or ""),
            "solvent": (record.get("config") or {}).get("solvent"),
            "energy_case": (record.get("config") or {}).get("energy_case"),
            "processing_capacity_mt_per_yr": (
                record.get("config") or {}
            ).get("processing_capacity"),
            "dissolution_temperature_c": (
                record.get("config") or {}
            ).get("dissolution_temperature_c"),
        } for record in pair_records[:24]]
        return tool_error(
            tool,
            "No admitted process record matches the requested configuration.",
            error_code="admitted_process_record_gap",
            analysis_type="admitted_process_record_gap",
            target_polymer=(
                polymers[0] if len(polymers) == 1 else None
            ),
            target_polymers=polymers,
            solvent=resolved_solvent,
            requested_energy_cases=requested_cases,
            requested_sensitivity_labels=list(sensitivity_labels or []),
            dissolution_temperature_c=temperature,
            processing_capacity_mt_per_yr=capacity,
            requested_metrics=metrics,
            can_estimate_msp=False,
            can_estimate_gwp=False,
            exact_cache_basis_available=False,
            available_pair_configurations=available_pair_configs,
            cached_target_polymers=sorted({
                str((record.get("config") or {}).get("target_plastic"))
                for record in _records()
            }),
            missing_basis_codes=["admitted_configuration"],
            process_data_gaps=[
                "The admitted cache contains no record for this exact "
                "polymer, solvent, condition, and requested record group.",
            ],
            provenance=_provenance(["cache"]),
        )

    normalized_records = [
        _normalized_admitted_record(record) for record in selected
    ]
    comparison_rows = [
        _admitted_record_summary(record) for record in normalized_records
    ]
    units = {
        metric: _ADMITTED_RECORD_METRICS[metric][2]
        for metric in metrics
    }
    baseline = next((
        row for row in normalized_records
        if str(row["record_id"]).casefold().endswith("-route-c1")
    ), None)
    assumptions = [{
        "record_id": row["record_id"],
        **copy.deepcopy(row["config"]),
    } for row in normalized_records]
    return tool_success(
        tool,
        analysis_type=(
            "admitted_process_sensitivity_records"
            if sensitivity_requested
            else "admitted_process_energy_case_records"
        ),
        engine_mode="cache",
        cache_match_status="exact",
        target_polymer=(
            polymers[0] if len(polymers) == 1 else None
        ),
        target_polymers=polymers,
        solvent=resolved_solvent,
        requested_metrics=metrics,
        requested_energy_cases=requested_cases or list(_ENERGY_CASES),
        selected_sensitivity_labels=[
            axis
            for axis in tea_contracts.CANONICAL_TEA_SENSITIVITY_AXES
            if axis in axes
        ],
        sensitivity_level_selector=canonical_level_selector,
        record_form=canonical_record_form,
        record_count=len(normalized_records),
        baseline_record_id=(
            baseline["record_id"] if baseline is not None else None
        ),
        records=normalized_records,
        comparison_rows=comparison_rows,
        metric_units=units,
        record_assumptions=assumptions,
        record_basis=(
            "exact admitted BioSTEAM cache records; no interpolation, "
            "surrogate, or live execution"
        ),
        energy_normalization_status="corrected_legacy_annual_hour_basis",
        energy_case_descriptions=dict(_ENERGY_CASES),
        exact_cache_basis_available=True,
        provenance=_provenance(["cache"]),
        process_data_gaps=[
            "These are admitted single-process records, not a newly integrated "
            "multistage route or experimental recovery/purity result.",
        ],
        warnings=[
            "Energy intensities apply the recorded legacy annual-hour "
            "normalization correction at the cache boundary.",
            "Every metric uses the product basis and configuration stored with "
            "its record; records are not interpolated across conditions.",
        ],
    )


def evaluate_tea_lca_scenarios(
    scenarios: list[dict[str, Any]],
    engine_mode: str = "auto",
    timeout_seconds: int = 180,
) -> str:
    """Evaluate user-supplied complete independent scenarios, not current candidates.

    Each scenario requires solvent and target_polymer and may set energy_case,
    target_mass_percent, processing_capacity_mt_per_yr, dissolution_temp_c,
    precipitation_temp_c, solvent_price, solvent_loss_pct, and
    feedstock_distance_km. Auto mode uses only exact cache matches, otherwise a
    compatible isolated live engine; it never interpolates cached process results.
    Use evaluate_stored_route_tea_lca for an inherited route or candidate shortlist.
    """
    tool = "evaluate_tea_lca_scenarios"
    if not isinstance(scenarios, list) or not scenarios:
        return tool_error(tool, "scenarios must be a non-empty list", error_code="missing_scenarios")
    if len(scenarios) > 20:
        return tool_error(tool, "At most 20 scenarios may run per call", error_code="too_many_scenarios")
    try:
        timeout = max(1, min(int(timeout_seconds), 600))
        configs = [_scenario_config(item) for item in scenarios]
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="invalid_scenario")
    results = [_run(config, engine_mode, timeout) for config in configs]
    labels = [str(item.get("label") or f"scenario-{index}") for index, item in enumerate(scenarios, 1)]
    rows = [_comparison_row(label, result) for label, result in zip(labels, results)]
    successes = [row for row in rows if row["success"]]
    failures = [row for row in rows if not row["success"]]
    if not successes:
        return tool_error(
            tool, "No scenario could be evaluated.", error_code="no_simulation_result",
            engine_mode=engine_mode, cache_match_status="miss",
            failures=failures, live_engine=live_engine_status(),
            provenance=_provenance([str(result.get("engine_mode")) for result in results]),
        )
    by_msp = sorted(successes, key=lambda row: float(row["msp_usd_per_kg"]))
    by_gwp = sorted(successes, key=lambda row: float(row["gwp_kg_co2e_per_kg"]))
    modes = [str(row["engine_mode"]) for row in successes]
    process_details = [
        detail for label, result in zip(labels, results)
        if (detail := _process_details(label, result)) is not None
    ]
    return tool_success(
        tool, analysis_type="tea_lca_scenario_comparison",
        engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
        cache_match_status="exact" if set(modes) == {"cache"} else "mixed_or_live",
        scenarios_requested=len(scenarios), completed=len(successes), failed=len(failures),
        comparison_rows=rows, lowest_msp_scenario=by_msp[0]["label"],
        lowest_gwp_scenario=by_gwp[0]["label"], energy_cases=_ENERGY_CASES,
        metric_units={key: unit for key, (_, _, unit) in _METRICS.items()},
        process_details=process_details,
        process_data_gaps=(
            ["Cached corpus does not contain unit-level equipment sizes or full stream mass balances."]
            if set(modes) == {"cache"} else []
        ) + _live_toxicity_data_gaps(results),
        provenance=_provenance(modes),
        warnings=[
            "Cached results are exact prior subprocess simulations, never interpolated process economics.",
            "Cached energy intensities correct the legacy worker's annual-total/hourly-flow normalization error using 8,409.6 operating hours per year.",
            "MSP and GWP are per kilogram of modeled recovered product; TCI and AOC are scenario-level estimates.",
            "Process simulation does not establish polymer recovery, purity, kinetics, or integrated plant performance.",
        ],
    )


def _composition(
    values: dict[str, float], *, resolve_polymers: bool = True,
) -> dict[str, float]:
    if not isinstance(values, dict) or not values:
        raise ValueError("feed_mass_fractions must map each feed polymer to its mass fraction or percent")
    resolved: dict[str, float] = {}
    for polymer, value in values.items():
        name = (
            _resolve_polymer(polymer) if resolve_polymers
            else str(polymer or "").strip()
        )
        if not name:
            raise ValueError("every feed polymer must have a name")
        resolved[name] = resolved.get(name, 0.0) + _finite(value, f"feed fraction for {polymer}")
    total = sum(resolved.values())
    if total <= 0:
        raise ValueError("feed composition must have positive mass")
    if total > 1.000001:
        if abs(total - 100.0) > 0.01:
            raise ValueError("feed percentages must sum to 100")
        resolved = {key: value / 100.0 for key, value in resolved.items()}
    elif abs(total - 1.0) > 0.0001:
        raise ValueError("feed mass fractions must sum to 1")
    if any(value <= 0 for value in resolved.values()):
        raise ValueError("every feed mass fraction must be positive")
    return resolved


def _stored_candidate_screen(state: Any) -> Optional[dict[str, Any]]:
    """Recover a typed screening basis without promoting it to a process route."""
    compact = getattr(state, "last_result", None) if state else None
    if compact:
        try:
            observation = json.loads(compact)
        except (TypeError, json.JSONDecodeError):
            observation = {}
        if observation.get("contract") == "tool.observation.v1":
            facts = observation.get("relevant_facts") or {}
            source_tool = str(observation.get("tool") or facts.get("tool_name") or "")
            if source_tool == "screen_precipitation_order":
                rows = [
                    dict(row) for row in facts.get("candidate_solvents") or []
                    if isinstance(row, dict) and row.get("solvent")
                ]
                if not rows and isinstance(facts.get("recommended_condition"), dict):
                    rows = [dict(facts["recommended_condition"])]
                if rows:
                    return {
                        "source_tool": source_tool,
                        "target_product": facts.get("first_polymer"),
                        "other_polymers": [
                            value for value in (facts.get("second_polymer"),)
                            if value
                        ],
                        "temperature_min_c": facts.get("temperature_min_c"),
                        "temperature_max_c": facts.get("temperature_max_c"),
                        "strict_maximum": facts.get("strict_maximum"),
                        "precipitation_threshold_wt_pct": facts.get("precipitation_threshold_wt_pct"),
                        "min_dissolution_solubility_wt_pct": facts.get("min_dissolution_solubility_wt_pct"),
                        "candidate_conditions": rows,
                    }
    rows, candidate_source = candidate_evidence(
        state, {CANDIDATE_SHAPE_SCREEN, CANDIDATE_SHAPE_PRECIPITATION},
    )
    rows = [row for row in rows if row.get("solvent")]
    if (
        state and getattr(state, "last_candidates", None)
        and not rows and candidate_source
    ):
        return {
            "candidate_evidence_kind_mismatch": True,
            "candidate_evidence_source": candidate_source.get("source_tool"),
            "candidate_evidence_shape": candidate_source.get("shape"),
            "required_candidate_shape": CANDIDATE_SHAPE_SCREEN,
        }
    constraints = dict(getattr(state, "last_screen_constraints", None) or {})
    target = next((
        row.get("dissolved_polymer") or row.get("target_polymer")
        or row.get("first_polymer")
        for row in rows
        if row.get("dissolved_polymer") or row.get("target_polymer")
        or row.get("first_polymer")
    ), None) or constraints.get("target_polymer")
    others = list(dict.fromkeys(
        str(polymer)
        for row in rows for polymer in row.get("retained_polymers") or []
        if polymer
    )) or list(dict.fromkeys(
        str(row["second_polymer"])
        for row in rows if row.get("second_polymer")
    )) or list(constraints.get("other_polymers") or [])
    if rows and target:
        return {
            "source_tool": (candidate_source or {}).get("source_tool")
            or "screen_polymer_separation",
            "target_product": target,
            "other_polymers": others,
            "temperature_min_c": getattr(state, "temperature_min_c", None),
            "temperature_max_c": getattr(state, "temperature_max_c", None),
            "strict_maximum": getattr(state, "strict_maximum", None),
            "candidate_conditions": rows,
        }
    prior = dict(getattr(state, "last_tea", None) or {})
    rows = [
        dict(row) for row in prior.get("candidate_conditions") or []
        if isinstance(row, dict) and row.get("solvent")
    ]
    if prior.get("route_source") != "typed_session_candidate_screen" or not rows:
        return None
    return {
        "source_tool": "screen_precipitation_order",
        "target_product": prior.get("target_product"),
        "other_polymers": list(prior.get("other_polymers") or []),
        "temperature_min_c": getattr(state, "temperature_min_c", None),
        "temperature_max_c": getattr(state, "temperature_max_c", None),
        "strict_maximum": getattr(state, "strict_maximum", None),
        "candidate_conditions": rows,
    }


def _candidate_tea_basis_gap(
    state: Any,
    basis: dict[str, Any],
    *,
    processing_capacity_mt_per_yr: Optional[float],
    product_capacity_mt_per_yr: Optional[float],
    energy_case: Optional[str],
    product_quality_intent: Optional[str],
    requested_metrics: Sequence[str],
) -> str:
    """Describe why a scientific screen is not yet a costable process basis."""
    tool = "evaluate_stored_route_tea_lca"
    prior = dict(getattr(state, "last_tea", None) or {})
    if processing_capacity_mt_per_yr is None and product_capacity_mt_per_yr is None:
        prior_capacity = prior.get("requested_capacity_mt_per_yr")
        if prior.get("requested_capacity_basis") == "recovered_product":
            product_capacity_mt_per_yr = prior_capacity
        elif prior.get("requested_capacity_basis") == "total_feed":
            processing_capacity_mt_per_yr = prior_capacity
    if product_quality_intent is None:
        product_quality_intent = prior.get("product_quality_intent")
    if energy_case is None:
        energy_case = prior.get("energy_case")
    if processing_capacity_mt_per_yr is not None and product_capacity_mt_per_yr is not None:
        return tool_error(
            tool,
            "Supply either total-feed capacity or recovered-product capacity, not both.",
            error_code="conflicting_capacity_basis",
        )
    try:
        capacity = _finite(
            product_capacity_mt_per_yr
            if product_capacity_mt_per_yr is not None
            else processing_capacity_mt_per_yr,
            "requested_capacity_mt_per_yr",
        ) if (
            product_capacity_mt_per_yr is not None
            or processing_capacity_mt_per_yr is not None
        ) else None
        if capacity is not None and capacity <= 0:
            raise ValueError("requested capacity must be positive")
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_route_basis")

    quality_intent = str(product_quality_intent or "").strip() or None
    if quality_intent:
        quality_words = " ".join(filter(None, re.split(r"[_\s-]+", quality_intent)))
        quality_intent = (
            "food-grade intent"
            if quality_words.casefold() in {"food grade", "food grade intent"}
            else quality_words
        )
    if quality_intent and len(quality_intent) > 120:
        return tool_error(
            tool, "product_quality_intent must be at most 120 characters",
            error_code="invalid_route_basis",
        )
    target = str(basis.get("target_product") or "").strip() or None
    cached_targets = sorted({
        str(row["config"].get("target_plastic"))
        for row in _records() if row.get("config", {}).get("target_plastic")
    })
    target_records = [
        row for row in _records()
        if target and _key(row["config"].get("target_plastic")) == _key(target)
    ]
    candidate_rows = []
    missing_prices = []
    for row in basis.get("candidate_conditions") or []:
        solvent = str(row.get("solvent") or "")
        assumption = _solvent_assumption(solvent)
        pair_record = _record_for_pair(target, solvent) if target else None
        if assumption is None:
            missing_prices.append(solvent)
        candidate_rows.append({
            key: value for key, value in {
                "solvent": solvent,
                "dissolution_temperature_c": (
                    row.get("dissolution_temperature_c")
                    if row.get("dissolution_temperature_c") is not None
                    else row.get("temperature_c")
                ),
                "dissolution_solubilities_wt_pct": row.get("dissolution_solubilities_wt_pct"),
                "target_solubility_wt_pct": row.get("target_solubility_pct"),
                "max_off_target_solubility_wt_pct": row.get("max_off_target_solubility_pct"),
                "selectivity_percentage_points": row.get("selectivity_pct"),
                "first_polymer": row.get("first_polymer"),
                "first_precipitation_proxy_c": row.get("first_precipitation_proxy_c"),
                "second_polymer": row.get("second_polymer"),
                "second_precipitation_proxy_c": row.get("second_precipitation_proxy_c"),
                "crossing_temperatures_c": (
                    row.get("crossing_temperatures_c")
                    or row.get("cooling_window_c")
                ),
                "recovery_window_c": row.get("recovery_window_c"),
                "solvent_price_usd_per_kg": (
                    assumption.get("price_usd_per_kg") if assumption else None
                ),
                "exact_target_solvent_cache_record_available": bool(pair_record),
            }.items() if value is not None
        })

    if set(requested_metrics) == {"gwp"}:
        lca_candidates = [{
            key: row[key] for key in (
                "solvent", "dissolution_temperature_c",
                "exact_target_solvent_cache_record_available",
            ) if key in row
        } for row in candidate_rows]
        return tool_error(
            tool,
            "No defensible process-GWP ranking can be calculated from the stored candidate screen.",
            error_code="insufficient_candidate_lca_basis",
            analysis_type="candidate_lca_basis_gap",
            requested_metrics=list(requested_metrics),
            can_estimate_msp=False,
            can_estimate_gwp=False,
            can_rank_gwp=False,
            lowest_gwp_solvent=None,
            route_source="typed_session_candidate_screen",
            candidate_source_tool=basis.get("source_tool"),
            target_product=target,
            other_polymers=list(basis.get("other_polymers") or []),
            temperature_min_c=basis.get("temperature_min_c"),
            temperature_max_c=basis.get("temperature_max_c"),
            candidate_count=len(candidate_rows),
            candidate_conditions=lca_candidates,
            target_cache_record_count=len(target_records),
            cached_target_polymers=cached_targets,
            missing_basis_codes=[
                "comparative_functional_unit", "system_boundary",
                "solvent_production_lci", "solvent_recovery_disposal_lci",
                "solvent_loading", "solvent_makeup_recycle_loss",
                "process_energy_case", "target_process_evidence",
                "feed_recovery_product_basis",
            ],
            missing_process_inputs=[
                "Define one comparative functional unit and life-cycle system boundary.",
                "Supply candidate-specific solvent-production and recovery/disposal life-cycle inventories.",
                "Define solvent loading, makeup, recycle efficiency, and process losses for every candidate.",
                "Define the process energy case and exact feed, recovery, and product basis.",
                f"Generate exact {target or 'target-polymer'} process evidence for each candidate; do not substitute records for other polymer/solvent pairs.",
            ],
            process_data_gaps=[
                "The current evidence establishes thermodynamic and safety screening, not comparative process life-cycle impacts.",
                "The admitted process cache has no exact target/candidate records for this comparison.",
            ],
            warnings=[
                "No GWP value or lowest-GWP solvent was calculated.",
                "G-score, Hansen RED, flash/GHS/exposure evidence, and records for other polymer/solvent pairs cannot substitute for process LCA.",
            ],
        )

    contaminant = dict(getattr(state, "last_contaminant", None) or {})
    unsupported = list(contaminant.get("unsupported_contaminants") or [])
    material_basis = "/".join(filter(None, (
        target, *[str(item) for item in basis.get("other_polymers") or []]
    ))) or "target/off-target"
    missing = []
    missing_codes = []
    if len(candidate_rows) > 1:
        missing.append("Select one screened candidate solvent as the process basis.")
        missing_codes.append("candidate_selection")
    missing.append(
        "Define an exact collection/precipitation setpoint, solvent loading, residence time, and solvent recycle/loss; the screen supplies only loading-dependent proxy crossings."
    )
    missing_codes.extend((
        "exact_collection_setpoint", "solvent_loading", "residence_time",
        "solvent_recycle_loss",
    ))
    if not getattr(state, "feed_mass_fractions", None):
        missing.append(
            f"Supply the {material_basis} feed composition and total-feed throughput."
        )
        missing_codes.extend(("feed_composition", "total_feed_throughput"))
    if product_capacity_mt_per_yr is not None:
        missing.append(
            f"Supply a validated {target or 'target-product'} recovery yield to convert recovered-product capacity to total-feed throughput."
        )
        missing_codes.append("product_recovery_yield")
    if not energy_case:
        missing.append("Select an energy case for the process evaluation.")
        missing_codes.append("energy_case")
    if not target_records:
        missing.append(
            f"Generate target-specific process-simulation evidence; the admitted TEA cache has no {target or 'requested-polymer'} record."
        )
        missing_codes.append("target_process_evidence")
    if missing_prices:
        missing.append(
            "Supply an admitted solvent-price basis for: "
            + ", ".join(dict.fromkeys(missing_prices)) + "."
        )
        missing_codes.append("solvent_price")
    if unsupported:
        missing.append(
            "Define and cost contaminant-removal or wash unit operations for: "
            + ", ".join(unsupported) + "."
        )
        missing_codes.append("contaminant_removal_cost")
    if quality_intent:
        missing.append(
            f"Validate product purity and decontamination against the requested {quality_intent} specification."
        )
        missing_codes.append("product_quality_validation")
    return tool_error(
        tool,
        "No defensible MSP can be calculated from the stored candidate screen.",
        error_code="uncostable_candidate_screen",
        analysis_type="tea_route_basis_gap",
        can_estimate_msp=False,
        route_source="typed_session_candidate_screen",
        candidate_source_tool=basis.get("source_tool"),
        target_product=target,
        other_polymers=list(basis.get("other_polymers") or []),
        requested_capacity_mt_per_yr=capacity,
        requested_capacity_basis=(
            "recovered_product" if product_capacity_mt_per_yr is not None
            else "total_feed" if processing_capacity_mt_per_yr is not None
            else "unspecified"
        ),
        energy_case=str(energy_case).upper() if energy_case else None,
        product_quality_intent=quality_intent,
        product_quality_validation="not_modeled",
        temperature_min_c=basis.get("temperature_min_c"),
        temperature_max_c=basis.get("temperature_max_c"),
        strict_maximum=basis.get("strict_maximum"),
        precipitation_threshold_wt_pct=basis.get("precipitation_threshold_wt_pct"),
        min_dissolution_solubility_wt_pct=basis.get("min_dissolution_solubility_wt_pct"),
        candidate_count=len(candidate_rows),
        candidate_conditions=candidate_rows,
        target_cache_record_count=len(target_records),
        cached_target_polymers=cached_targets,
        unsupported_contaminants=unsupported,
        supported_families=list(contaminant.get("supported_families") or []),
        missing_basis_codes=missing_codes,
        missing_process_inputs=missing,
        process_data_gaps=[
            "The current scientific result is a precipitation-order screen, not a complete process route.",
            "The admitted process cache contains no target-specific simulation evidence for this product.",
            "Recovery, product purity, contaminant removal, and integrated-facility performance are not established.",
        ],
        warnings=[
            "No MSP, TCI, AOC, or GWP value was calculated.",
            "Do not substitute the direct-scenario defaults or a different target polymer as an economic analog.",
            *(
                ["The requested product-quality intent is an objective, not validated product performance."]
                if quality_intent else []
            ),
        ],
    )


def _live_process_model_targets() -> tuple[str, ...]:
    return tuple(sorted(tea_worker._TARGET))


def _classify_live_feed_polymers(
    names: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split feed labels into unrecognised vs recognised-but-unmodelled.

    Recognition is thermo.resolve_polymer_identity. Unmodelled is absence
    from tea_worker._TARGET after that lookup, not membership in a bad-name
    list. Generic PE expands to modelled LDPE/HDPE and is neither.
    """
    modelled = tea_worker._TARGET
    unrecognised: list[str] = []
    unmodelled: list[str] = []
    seen_unrecognised: set[str] = set()
    seen_unmodelled: set[str] = set()
    for name in names:
        token = str(name or "").strip()
        if not token:
            continue
        identity = thermo.resolve_polymer_identity(token)
        if identity is None:
            if token not in seen_unrecognised:
                seen_unrecognised.add(token)
                unrecognised.append(token)
            continue
        key = str(identity).upper()
        if key in modelled:
            continue
        if any(
            str(member).upper() in modelled
            for member in thermo.expand_polymer_identity(token)
        ):
            continue
        if key not in seen_unmodelled:
            seen_unmodelled.add(key)
            unmodelled.append(str(identity))
    return tuple(unrecognised), tuple(unmodelled)


def _unsupported_live_feed_polymers(names: Sequence[str]) -> tuple[str, ...]:
    """Return recognised feed identities that have no live TEA process model."""
    _unrecognised, unmodelled = _classify_live_feed_polymers(names)
    return unmodelled


def _join_polymer_names(names: Sequence[str]) -> str:
    items = [str(name) for name in names]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _unsupported_live_target_feed_error(
    *,
    unrecognised: Sequence[str] = (),
    unsupported: Sequence[str] = (),
    feed_names: Sequence[str],
    feed: dict[str, float],
    requested_metrics: Sequence[str],
    processing_capacity_mt_per_yr: Optional[float],
    product_capacity_mt_per_yr: Optional[float],
    capacity: Optional[float],
    energy_case: Optional[str],
) -> str:
    """Refuse a feed whose labels are unrecognised or have no live process model.

    Unrecognised names must not be worded as missing a process model.
    Recognised-but-unmodelled names keep the modelled-set refusal.
    """
    unrecognised = tuple(unrecognised)
    unsupported = tuple(unsupported)
    sentences: list[str] = []
    process_data_gaps: list[str] = []
    missing_codes: list[str] = []
    if unrecognised:
        named = _join_polymer_names(unrecognised)
        copula = "is" if len(unrecognised) == 1 else "are"
        noun = "identity" if len(unrecognised) == 1 else "identities"
        sentences.append(f"{named} {copula} not a recognised polymer {noun}.")
        process_data_gaps.append(
            f"No TEA/LCA process-model claim applies to {named}."
        )
        missing_codes.append("recognised_polymer_identity")
    if unsupported:
        named = _join_polymer_names(unsupported)
        modelled = _join_polymer_names(_live_process_model_targets())
        verb = "has" if len(unsupported) == 1 else "have"
        sentences.append(
            f"{named} {verb} no live TEA process model, so completing a "
            "separation route and plant basis cannot produce TEA/LCA for this "
            f"feed. Live process models exist only for {modelled}; LDPE and "
            "HDPE share the PE process."
        )
        process_data_gaps.append(
            f"No additional process inputs can create a TEA/LCA model for {named}."
        )
        missing_codes.append("live_process_model")
    warnings = [
        "No MSP, TCI, AOC, or GWP value was calculated.",
    ]
    if unrecognised:
        warnings.append(
            "Do not treat an unrecognised name as a polymer this system cannot cost."
        )
    if unsupported:
        warnings.append(
            "Do not collect a route or plant basis as if that would enable this TEA."
        )
    cached_targets = sorted({
        str(row["config"].get("target_plastic"))
        for row in _records() if row.get("config", {}).get("target_plastic")
    })
    selected_energy_case = str(energy_case).upper() if energy_case else None
    return tool_error(
        "evaluate_stored_route_tea_lca",
        " ".join(sentences),
        error_code="uncostable_initial_feed_basis",
        analysis_type=(
            "tea_unrecognised_feed_polymer" if unrecognised
            else "tea_unsupported_live_target"
        ),
        can_estimate_msp=False,
        can_estimate_gwp=False,
        estimate_quality="not_calculated",
        exact_cache_basis_available=False,
        route_source="requested_feed_without_route",
        requested_feed_polymers=list(feed_names),
        requested_feed_mass_fractions=dict(feed),
        requested_capacity_mt_per_yr=capacity,
        requested_capacity_basis=(
            "recovered_product" if product_capacity_mt_per_yr is not None
            else "total_feed" if processing_capacity_mt_per_yr is not None
            else "unspecified"
        ),
        requested_metrics=list(requested_metrics),
        energy_case=selected_energy_case,
        cached_target_polymers=cached_targets,
        unrecognised_feed_polymers=list(unrecognised),
        unsupported_live_targets=list(unsupported),
        supported_live_targets=list(_live_process_model_targets()),
        missing_basis_codes=missing_codes,
        missing_process_inputs=[],
        process_data_gaps=process_data_gaps,
        warnings=warnings,
    )


def _feed_tea_basis_gap(
    feed_mass_fractions: Optional[dict[str, float]],
    feed_polymers: Optional[list[str]],
    *,
    processing_capacity_mt_per_yr: Optional[float],
    product_capacity_mt_per_yr: Optional[float],
    comparison_capacities_mt_per_yr: Optional[list[float]],
    energy_case: Optional[str],
    requested_metrics: Sequence[str],
) -> str:
    """Preserve an initial feed request without silently choosing its route."""
    tool = "evaluate_stored_route_tea_lca"
    if processing_capacity_mt_per_yr is not None and product_capacity_mt_per_yr is not None:
        return tool_error(
            tool,
            "Supply either total-feed capacity or recovered-product capacity, not both.",
            error_code="conflicting_capacity_basis",
        )
    try:
        if feed_mass_fractions:
            feed = _composition(feed_mass_fractions, resolve_polymers=False)
            feed_names = list(feed)
        elif isinstance(feed_polymers, list) and feed_polymers:
            feed = {}
            feed_names = list(dict.fromkeys(
                thermo.resolve_polymer_identity(str(item)) or str(item).strip()
                for item in feed_polymers if str(item).strip()
            ))
            if not feed_names:
                raise ValueError("feed_polymers must contain at least one material label")
        else:
            raise ValueError(
                "Supply feed_polymers or feed_mass_fractions for a new feed-level request"
            )
        capacity_value = product_capacity_mt_per_yr if (
            product_capacity_mt_per_yr is not None
        ) else processing_capacity_mt_per_yr
        capacity = _finite(capacity_value, "requested_capacity_mt_per_yr") if (
            capacity_value is not None
        ) else None
        if capacity is not None and capacity <= 0:
            raise ValueError("requested capacity must be positive")
        scale_capacities = None
        if comparison_capacities_mt_per_yr is not None:
            if not feed:
                raise ValueError(
                    "scale comparison requires feed mass fractions, not material labels alone"
                )
            if not isinstance(comparison_capacities_mt_per_yr, list) or capacity is None:
                raise ValueError(
                    "scale comparison requires a baseline total-feed capacity and a list of comparison capacities"
                )
            scale_capacities = list(dict.fromkeys([
                capacity,
                *(
                    _finite(value, "comparison_capacities_mt_per_yr")
                    for value in comparison_capacities_mt_per_yr
                ),
            ]))
            if (
                len(scale_capacities) < 2 or len(scale_capacities) > 6
                or any(value <= 0 for value in scale_capacities)
            ):
                raise ValueError(
                    "scale comparison requires two to six distinct positive capacities"
                )
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_route_basis")

    unrecognised, unmodelled = _classify_live_feed_polymers(feed_names)
    if unrecognised or unmodelled:
        return _unsupported_live_target_feed_error(
            unrecognised=unrecognised,
            unsupported=unmodelled,
            feed_names=feed_names,
            feed=feed,
            requested_metrics=requested_metrics,
            processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
            product_capacity_mt_per_yr=product_capacity_mt_per_yr,
            capacity=capacity,
            energy_case=energy_case,
        )

    unresolved = [name for name in feed_names if name.casefold() == "pe"]
    interpretations = {name: ["LDPE", "HDPE"] for name in unresolved}
    missing_codes = ["complete_separation_route", "solvent_selection",
                     "dissolution_collection_setpoints", "recovery_product_basis"]
    missing = [
        "Define a complete separation route and identify which feed polymer is dissolved at each stage.",
        "Select the solvent and exact dissolution and collection setpoints for each stage.",
        "Define recovery yields and the product basis needed to convert feed throughput into product economics.",
    ]
    if not feed:
        missing_codes.insert(0, "feed_composition")
        missing.insert(
            0,
            "Supply the mass fraction of every named feed polymer; no composition was inferred.",
        )
    if unresolved:
        missing_codes.insert(0, "polymer_grade")
        missing.insert(
            0,
            "Clarify whether PE means LDPE or HDPE; those grades do not share one dissolution route.",
        )
    if capacity is None:
        missing_codes.append("plant_capacity")
        missing.append("Supply the plant capacity and state whether it is total feed or recovered product.")
    if not energy_case:
        missing_codes.append("energy_case")
        missing.append("Select the process energy case.")

    cached_targets = sorted({
        str(row["config"].get("target_plastic"))
        for row in _records() if row.get("config", {}).get("target_plastic")
    })
    cache_lca_fields = {
        str(key).casefold() for row in _records()
        for key in (row.get("result", {}).get("lca") or {})
    }
    cache_energy_case_coverage = {
        case: sorted({
            str(row["config"].get("target_plastic"))
            for row in _records()
            if str(row.get("config", {}).get("energy_case") or "").upper() == case
            and row.get("config", {}).get("target_plastic")
        })
        for case in _ENERGY_CASES
    }
    scope_available = all(any(
        re.search(rf"scope[_ -]?{number}\b", key) for key in cache_lca_fields
    ) for number in (1, 2))
    selected_energy_case = str(energy_case).upper() if energy_case else None
    if scale_capacities:
        candidate_targets = sorted(
            (set(feed_names) - set(unresolved))
            | {item for values in interpretations.values() for item in values}
        )
        capacity_ranges = {
            polymer: sorted({
                float(row["config"]["processing_capacity"])
                for row in _records()
                if _key(row["config"].get("target_plastic")) == _key(polymer)
                and (
                    not selected_energy_case
                    or str(row["config"].get("energy_case") or "").upper()
                    == selected_energy_case
                )
            })
            for polymer in candidate_targets
        }
        stage_capacities = {
            f"{total:g}": {
                polymer: total * fraction for polymer, fraction in feed.items()
            }
            for total in scale_capacities
        }
        coverage = {}
        for total, stages in stage_capacities.items():
            coverage[total] = {}
            for polymer in candidate_targets:
                ranges = capacity_ranges[polymer]
                stage = stages.get(polymer, stages.get("PE"))
                coverage[total][polymer] = (
                    "no_record" if not ranges else
                    "below" if stage < ranges[0] else
                    "above" if stage > ranges[-1] else "within"
                )
        return tool_error(
            tool,
            "The requested scales can be checked against stage-cache coverage, but no integrated economies-of-scale or emissions-intensity result can be calculated without a complete route.",
            error_code="uncostable_feed_scale_basis",
            analysis_type="tea_feed_scale_basis_gap",
            can_estimate_msp=False,
            can_estimate_gwp=False,
            can_compare_scale=False,
            economies_of_scale_calculated=False,
            emissions_intensity_change_calculated=False,
            estimate_quality="not_calculated",
            exact_cache_basis_available=False,
            route_source="requested_feed_without_route",
            requested_feed_polymers=feed_names,
            requested_feed_mass_fractions=feed,
            unresolved_polymer_identities=unresolved,
            supported_interpretations=interpretations,
            requested_capacity_mt_per_yr=capacity,
            requested_capacity_basis="total_feed",
            requested_scale_capacities_mt_per_yr=scale_capacities,
            conditional_stage_capacities_mt_per_yr=stage_capacities,
            cache_capacity_ranges_mt_per_yr=capacity_ranges,
            conditional_cache_coverage=coverage,
            requested_metrics=list(requested_metrics),
            energy_case=selected_energy_case,
            energy_case_description=_ENERGY_CASES.get(selected_energy_case),
            available_cache_energy_cases=list(_ENERGY_CASES),
            energy_case_descriptions=dict(_ENERGY_CASES),
            can_compare_energy_cases=False,
            missing_basis_codes=[*missing_codes, "integrated_scale_process_evidence"],
            missing_process_inputs=missing,
            scale_comparison_basis="conditional stage-cache coverage only",
            process_data_gaps=[
                "The stage capacities are feed-fraction arithmetic, not recovered-product throughput or an integrated process mass balance.",
                "Being inside a cached stage-capacity range does not establish an exact record for the requested grade, route, solvent, setpoints, recovery, or product basis.",
                "No MSP, GWP intensity, or total annual emissions value was calculated at either scale.",
            ],
            warnings=[
                "Do not infer economies of scale or an emissions-intensity trend from cache-range coverage.",
                *(
                    ["Do not silently map generic PE to LDPE or HDPE or combine unmatched stage records."]
                    if unresolved else
                    ["The requested polymer identities are explicit; do not introduce a polymer-grade ambiguity."]
                ),
            ],
        )
    return tool_error(
        tool,
        "No defensible MSP, capital cost, or operating cost can be calculated from this feed request alone.",
        error_code="uncostable_initial_feed_basis",
        analysis_type="tea_feed_basis_gap",
        can_estimate_msp=False,
        estimate_quality="not_calculated",
        exact_cache_basis_available=False,
        route_source="requested_feed_without_route",
        requested_feed_polymers=feed_names,
        requested_feed_mass_fractions=feed,
        unresolved_polymer_identities=unresolved,
        supported_interpretations=interpretations,
        requested_capacity_mt_per_yr=capacity,
        requested_capacity_basis=(
            "recovered_product" if product_capacity_mt_per_yr is not None
            else "total_feed" if processing_capacity_mt_per_yr is not None
            else "unspecified"
        ),
        requested_metrics=list(requested_metrics),
        energy_case=selected_energy_case,
        cached_target_polymers=cached_targets,
        available_cache_energy_cases=list(_ENERGY_CASES),
        energy_case_descriptions=dict(_ENERGY_CASES),
        cache_energy_case_coverage=cache_energy_case_coverage,
        can_compare_energy_cases=False,
        cache_total_gwp_available="gwp_kg_co2e_per_kg" in cache_lca_fields,
        scope_1_2_attribution_available=scope_available,
        scope_attribution_data_gap=(
            None if scope_available else
            "The admitted cache has no sourced fields separating direct/on-site fuel emissions from purchased electricity."
        ),
        missing_basis_codes=missing_codes,
        missing_process_inputs=missing,
        process_data_gaps=[
            "The admitted cache contains stage records for selected polymers, solvents, capacities, and energy cases; it contains no exact integrated record for this requested feed and capacity.",
            "Any estimate based on a different polymer grade, route, composition, or scale would be a screening analog rather than an exact process result.",
        ],
        warnings=[
            "No MSP, TCI, AOC, or GWP value was calculated.",
            *(
                ["Do not silently map generic PE to LDPE or HDPE or combine unmatched stage records."]
                if unresolved else
                ["The requested polymer identities are explicit; do not introduce a polymer-grade ambiguity."]
            ),
        ],
    )


def evaluate_stored_route_tea_lca(
    feed_mass_fractions: Optional[dict[str, float]] = None,
    processing_capacity_mt_per_yr: Optional[float] = None,
    product_capacity_mt_per_yr: Optional[float] = None,
    comparison_capacities_mt_per_yr: Optional[list[float]] = None,
    energy_case: Optional[str] = None,
    precipitation_temperature_c: Optional[float] = None,
    product_quality_intent: Optional[str] = None,
    engine_mode: str = "auto",
    timeout_seconds: int = 180,
    allow_screening_estimate: bool = False,
    requested_metrics: Optional[list[str]] = None,
    requested_product_count: Optional[int] = None,
    product_selection_basis: Optional[str] = None,
    feed_polymers: Optional[list[str]] = None,
    compare_route_variants: bool = False,
) -> str:
    """Evaluate every dissolution stage from the exact route in typed session state.

    The route or current candidate screen is deliberately not a model argument:
    this tool consumes typed session state, preventing a follow-up from silently
    re-deriving solvents and setpoints. It returns a bounded basis gap when a
    candidate comparison lacks process evidence. An initial feed without a
    route returns an unresolved process-basis gap. Supply one or more
    `comparison_capacities_mt_per_yr` to compare the same route across scales;
    the first processing capacity remains the baseline and the last comparison
    becomes the durable basis for downstream optimization. A request to choose
    products by value returns a typed portfolio-basis gap until the product
    identities and value basis are supplied; the model never makes that choice.
    """
    tool = "evaluate_stored_route_tea_lca"
    metrics = list(dict.fromkeys(
        _REQUESTED_METRIC_ALIASES.get(token, token)
        for metric in requested_metrics or []
        if (token := str(metric).strip().casefold())
    ))
    allowed_metrics = {"msp", "tci", "aoc", "gwp", "energy"}
    if not metrics or any(metric not in allowed_metrics for metric in metrics):
        if requested_metrics is not None:
            return tool_error(
                tool,
                "requested_metrics must contain one or more of: msp, tci, aoc, gwp, energy.",
                error_code="invalid_requested_metrics",
            )
    state = current_tool_session()
    route = copy.deepcopy(getattr(state, "last_route", None)) if state else None
    candidate_basis = _stored_candidate_screen(state) if state and not route else None
    candidate_feed_matches = False
    if candidate_basis and not candidate_basis.get("candidate_evidence_kind_mismatch"):
        candidate_labels = [
            str(candidate_basis.get("target_product") or ""),
            *(str(item) for item in candidate_basis.get("other_polymers") or []),
        ]
        try:
            candidate_identities = {
                _resolve_polymer(item) for item in candidate_labels if item
            }
            supplied_identities = {
                _resolve_polymer(item) for item in feed_polymers or []
            }
            candidate_feed_matches = bool(
                candidate_identities
                and (not supplied_identities or supplied_identities == candidate_identities)
            )
        except ValueError:
            candidate_feed_matches = False
    if compare_route_variants:
        basis = copy.deepcopy(
            getattr(state, "route_substitution_basis", None)
        ) if state else None
        if not basis:
            return tool_error(
                tool,
                "No stored original/substituted route pair is available for comparison.",
                error_code="missing_route_variant_basis",
                analysis_type="route_variant_tea_basis_gap",
                can_estimate_msp=False, can_estimate_gwp=False,
            )
        original_route = basis.get("original_route")
        substituted_route = basis.get("recommended_route")
        if not original_route or not substituted_route:
            return tool_error(
                tool, "The stored route-variant basis is incomplete.",
                error_code="invalid_route_variant_basis",
                analysis_type="route_variant_tea_basis_gap",
                can_estimate_msp=False, can_estimate_gwp=False,
            )
        original_state_route = state.last_route
        variant_results = []
        try:
            for label, variant_route in (
                ("original", original_route),
                ("substituted", substituted_route),
            ):
                state.last_route = copy.deepcopy(variant_route)
                variant = parse_tool_result(evaluate_stored_route_tea_lca(
                    feed_mass_fractions=feed_mass_fractions,
                    processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                    product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                    comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
                    energy_case=energy_case,
                    precipitation_temperature_c=precipitation_temperature_c,
                    product_quality_intent=product_quality_intent,
                    engine_mode=engine_mode,
                    timeout_seconds=timeout_seconds,
                    allow_screening_estimate=allow_screening_estimate,
                    requested_metrics=requested_metrics,
                    requested_product_count=requested_product_count,
                    product_selection_basis=product_selection_basis,
                    feed_polymers=feed_polymers,
                    compare_route_variants=False,
                ))["data"]
                variant_results.append((label, variant_route, variant))
        finally:
            state.last_route = original_state_route

        rows = []
        for label, variant_route, variant in variant_results:
            row = {
                "label": label.title(), "route_variant": label,
                "success": variant.get("success"),
                "route_signature": variant.get("route_signature"),
                "engine_mode": variant.get("engine_mode"),
                "cache_match_status": variant.get("cache_match_status"),
                "processing_capacity_mt_per_yr": variant.get(
                    "processing_capacity_mt_per_yr"
                ),
                "energy_case": variant.get("energy_case"),
                "steps": list(variant_route.get("steps") or []),
                "mass_weighted_recovered_msp_usd_per_kg": variant.get(
                    "mass_weighted_recovered_msp_usd_per_kg"
                ),
                "mass_weighted_recovered_gwp_kg_co2e_per_kg": variant.get(
                    "mass_weighted_recovered_gwp_kg_co2e_per_kg"
                ),
                "total_stage_tci_usd": variant.get("total_stage_tci_usd"),
                "total_stage_aoc_usd_per_yr": variant.get(
                    "total_stage_aoc_usd_per_yr"
                ),
                "estimate_quality": variant.get("estimate_quality"),
                "estimate_limitations": list(
                    variant.get("estimate_limitations") or []
                ),
                "error_code": variant.get("error_code"),
                "missing_basis_codes": list(
                    variant.get("missing_basis_codes") or []
                ),
            }
            rows.append({key: value for key, value in row.items() if value is not None})
        failed = [row for row in rows if row.get("success") is not True]
        if failed:
            return tool_error(
                tool,
                "The original and substituted routes do not share a complete admitted TEA/LCA basis.",
                error_code="route_variant_tea_basis_gap",
                analysis_type="route_variant_tea_basis_gap",
                can_estimate_msp=False, can_estimate_gwp=False,
                route_source="typed_session_state",
                requested_metrics=metrics,
                comparison_rows=rows,
                missing_basis_codes=sorted({
                    code for row in failed
                    for code in row.get("missing_basis_codes") or [
                        "route_variant_process_evidence"
                    ]
                }),
                warnings=[
                    "No cross-route MSP or GWP difference was calculated.",
                    "Thermodynamic selectivity and safety scores cannot substitute for a common TEA/LCA basis.",
                ],
            )
        original, substituted = rows
        msp_delta = (
            float(substituted["mass_weighted_recovered_msp_usd_per_kg"])
            - float(original["mass_weighted_recovered_msp_usd_per_kg"])
            if "msp" in metrics else None
        )
        gwp_delta = (
            float(substituted["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
            - float(original["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
            if "gwp" in metrics else None
        )
        modes = {str(row.get("engine_mode")) for row in rows}
        evidence_class = (
            "exact_cache" if modes == {"cache"}
            else "screening_estimate" if modes == {"screening_estimate"}
            else "mixed_exact_and_screening"
        )
        return tool_success(
            tool,
            analysis_type="route_substitution_tea_lca_comparison",
            route_source="typed_session_state",
            engine_mode=(next(iter(modes)) if len(modes) == 1 else "mixed"),
            processing_capacity_mt_per_yr=original.get(
                "processing_capacity_mt_per_yr"
            ),
            energy_case=original.get("energy_case"),
            requested_metrics=metrics,
            comparison_rows=rows,
            common_route_variant_basis={
                "feed_mass_fractions": dict(
                    feed_mass_fractions
                    or getattr(state, "feed_mass_fractions", {}) or {}
                ),
                "processing_capacity_mt_per_yr": original.get(
                    "processing_capacity_mt_per_yr"
                ),
                "energy_case": original.get("energy_case"),
                "precipitation_temperature_c": (
                    25.0 if precipitation_temperature_c is None
                    else float(precipitation_temperature_c)
                ),
            },
            comparison_evidence_class=evidence_class,
            msp_difference_substituted_minus_original_usd_per_kg=msp_delta,
            gwp_difference_substituted_minus_original_kg_co2e_per_kg=gwp_delta,
            provenance={
                "original_route_signature": original.get("route_signature"),
                "substituted_route_signature": substituted.get("route_signature"),
            },
            warnings=[
                "The original and substituted routes share feed, capacity, energy case, and metric definitions, but their evidence classes remain distinct.",
                "A screening-estimate difference is not an experimentally validated cost or carbon premium.",
            ],
        )
    if feed_polymers and route:
        route_labels = [
            str(step.get("dissolved_polymer"))
            for step in route.get("steps") or []
            if step.get("dissolved_polymer")
        ] + ([str(route["final_residue"])] if route.get("final_residue") else [])
        try:
            supplied_identities = {_resolve_polymer(item) for item in feed_polymers}
            route_identities = {_resolve_polymer(item) for item in route_labels}
        except ValueError:
            supplied_identities = route_identities = set()
        if not route_identities or supplied_identities != route_identities:
            return _feed_tea_basis_gap(
                feed_mass_fractions,
                feed_polymers,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
                energy_case=energy_case,
                requested_metrics=metrics,
            )
    elif (
        feed_polymers or (feed_mass_fractions and not route)
    ) and not candidate_feed_matches:
        return _feed_tea_basis_gap(
            feed_mass_fractions,
            feed_polymers,
            processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
            product_capacity_mt_per_yr=product_capacity_mt_per_yr,
            comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
            energy_case=energy_case,
            requested_metrics=metrics,
        )
    if not route:
        if candidate_basis and candidate_feed_matches:
            if candidate_basis.get("candidate_evidence_kind_mismatch"):
                return tool_error(
                    tool,
                    "The stored candidate evidence is a route, not a solvent-screen "
                    "shortlist; ask to cost the stored route or rerun the screen.",
                    error_code="candidate_evidence_kind_mismatch",
                    analysis_type="candidate_evidence_basis_gap",
                    requested_analysis_type="tea_lca",
                    can_estimate_msp=False, can_estimate_gwp=False,
                    **candidate_basis,
                    missing_basis_codes=["screen_shortlist_or_complete_route"],
                    warnings=["No MSP, TCI, AOC, or GWP value was calculated."],
                )
            return _candidate_tea_basis_gap(
                state,
                candidate_basis,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                energy_case=energy_case,
                product_quality_intent=product_quality_intent,
                requested_metrics=metrics,
            )
        prior_tea = dict(getattr(state, "last_tea", None) or {}) if state else {}
        if (
            comparison_capacities_mt_per_yr is not None
            and not feed_mass_fractions
            and prior_tea.get("analysis_type") in {
                "tea_feed_basis_gap", "tea_feed_scale_basis_gap",
            }
        ):
            feed_mass_fractions = dict(
                prior_tea.get("requested_feed_mass_fractions") or {}
            )
            feed_polymers = list(prior_tea.get("requested_feed_polymers") or [])
            if processing_capacity_mt_per_yr is None:
                processing_capacity_mt_per_yr = prior_tea.get(
                    "requested_capacity_mt_per_yr"
                )
            if energy_case is None:
                energy_case = prior_tea.get("energy_case")
        if feed_mass_fractions or feed_polymers:
            return _feed_tea_basis_gap(
                feed_mass_fractions,
                feed_polymers,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
                energy_case=energy_case,
                requested_metrics=metrics,
            )
        candidate_basis = candidate_basis or _stored_candidate_screen(state)
        if candidate_basis:
            if candidate_basis.get("candidate_evidence_kind_mismatch"):
                return tool_error(
                    tool,
                    "The stored candidate evidence is a route, not a solvent-screen "
                    "shortlist; ask to cost the stored route or rerun the screen.",
                    error_code="candidate_evidence_kind_mismatch",
                    analysis_type="candidate_evidence_basis_gap",
                    requested_analysis_type="tea_lca",
                    can_estimate_msp=False, can_estimate_gwp=False,
                    **candidate_basis,
                    missing_basis_codes=["screen_shortlist_or_complete_route"],
                    warnings=["No MSP, TCI, AOC, or GWP value was calculated."],
                )
            return _candidate_tea_basis_gap(
                state,
                candidate_basis,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                energy_case=energy_case,
                product_quality_intent=product_quality_intent,
                requested_metrics=metrics,
            )
        return tool_error(
            tool, "No stored separation route is available in this session.",
            error_code="missing_stored_route",
        )
    if not route.get("complete"):
        return tool_error(
            tool, "The stored separation route is incomplete and cannot define every TEA stage.",
            error_code="incomplete_stored_route", consumed_route=route,
        )
    if processing_capacity_mt_per_yr is not None and product_capacity_mt_per_yr is not None:
        return tool_error(
            tool,
            "Supply either total-feed capacity or recovered-product capacity, not both.",
            error_code="conflicting_capacity_basis",
        )
    selection_basis = str(product_selection_basis or "").strip().casefold()
    if requested_product_count is not None or selection_basis:
        route_products = [
            str(step["dissolved_polymer"]) for step in route.get("steps") or []
            if step.get("dissolved_polymer")
        ] + ([str(route["final_residue"])] if route.get("final_residue") else [])
        if (
            isinstance(requested_product_count, bool)
            or not isinstance(requested_product_count, int)
            or requested_product_count < 1
            or requested_product_count > len(route_products)
            or selection_basis != "highest_value"
        ):
            return tool_error(
                tool,
                "Product selection requires a valid requested_product_count and product_selection_basis='highest_value'.",
                error_code="invalid_product_selection_basis",
            )
        capacity_value = (
            product_capacity_mt_per_yr
            if product_capacity_mt_per_yr is not None
            else processing_capacity_mt_per_yr
        )
        try:
            capacity = (
                _finite(capacity_value, "requested_capacity_mt_per_yr")
                if capacity_value is not None else None
            )
            if capacity is not None and capacity <= 0:
                raise ValueError("requested capacity must be positive")
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_route_basis")
        missing = [
            "Name the requested product polymers and define the comparable product-value or market-price basis; the thermodynamic route does not rank products by value.",
            "Supply the full feed composition and per-polymer recovery yields needed to convert feed into recovered products.",
            "State whether the requested recovered-product capacity is combined across products or applies to each product, then allocate that capacity.",
            "Define route-stage collection, product specifications, solvent loading/recycle/loss, and the process energy case.",
            "Generate route- and product-specific process evidence on one MSP and cradle-to-gate GWP basis.",
        ]
        return tool_error(
            tool,
            "No defensible product-value ranking, MSP, or GWP estimate can be calculated from the stored thermodynamic route.",
            error_code="uncostable_product_portfolio",
            analysis_type="tea_route_portfolio_basis_gap",
            can_estimate_msp=False,
            can_estimate_gwp=False,
            can_rank_product_value=False,
            route_source="typed_session_state",
            route_products=route_products,
            requested_product_count=requested_product_count,
            product_selection_basis=selection_basis,
            requested_metrics=metrics,
            requested_capacity_mt_per_yr=capacity,
            requested_capacity_basis=(
                "recovered_product" if product_capacity_mt_per_yr is not None
                else "total_feed" if processing_capacity_mt_per_yr is not None
                else "unspecified"
            ),
            feed_composition_available=bool(getattr(state, "feed_mass_fractions", None)),
            target_products=[],
            value_ranking_available=False,
            exact_route_process_basis_available=False,
            missing_basis_codes=[
                "target_product_identity", "product_value_basis",
                "feed_composition", "product_recovery_yields",
                "product_capacity_allocation", "collection_product_basis",
                "solvent_recycle_loss", "energy_case",
                "route_product_process_evidence",
            ],
            missing_process_inputs=missing,
            process_data_gaps=[
                "The stored route is a thermodynamic sequence, not an integrated process or product-value ranking.",
                "No common product-specific MSP or cradle-to-gate GWP basis was evaluated.",
            ],
            warnings=[
                "No polymers were selected as highest value.",
                "No MSP, TCI, AOC, GWP, recovery, or landfill allocation was calculated.",
            ],
        )
    if product_capacity_mt_per_yr is not None:
        try:
            product_capacity = _finite(product_capacity_mt_per_yr, "product_capacity_mt_per_yr")
            if product_capacity <= 0:
                raise ValueError("product_capacity_mt_per_yr must be positive")
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_route_basis")
        return tool_error(
            tool,
            "Recovered-product capacity cannot be converted to route feed throughput without a validated recovery basis.",
            error_code="product_capacity_requires_recovery_basis",
            analysis_type="tea_route_basis_gap", can_estimate_msp=False,
            route_source="typed_session_state",
            requested_capacity_mt_per_yr=product_capacity,
            requested_capacity_basis="recovered_product",
            product_quality_intent=str(product_quality_intent or "").strip() or None,
            product_quality_validation="not_modeled",
            steps=list(route.get("steps") or []),
            final_residue=route.get("final_residue"),
            missing_process_inputs=[
                "Supply a validated recovery yield or state total-feed throughput instead."
            ],
            warnings=["No MSP, TCI, AOC, or GWP value was calculated."],
        )
    feed_capacity = 20_000.0 if processing_capacity_mt_per_yr is None else processing_capacity_mt_per_yr
    selected_energy_case = str(energy_case or "C1").upper()
    selected_precipitation_c = 25.0 if precipitation_temperature_c is None else precipitation_temperature_c
    if comparison_capacities_mt_per_yr is not None:
        if not isinstance(comparison_capacities_mt_per_yr, list):
            return tool_error(
                tool, "comparison_capacities_mt_per_yr must be a list",
                error_code="invalid_scale_comparison",
            )
        try:
            requested = [
                _finite(feed_capacity, "processing_capacity_mt_per_yr"),
                *[
                    _finite(value, "comparison_capacities_mt_per_yr")
                    for value in comparison_capacities_mt_per_yr
                ],
            ]
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_scale_comparison")
        capacities = list(dict.fromkeys(requested))
        if len(capacities) < 2 or len(capacities) > 6 or any(value <= 0 for value in capacities):
            return tool_error(
                tool,
                "Scale comparison requires two to six distinct positive capacities.",
                error_code="invalid_scale_comparison",
            )
        scale_results = []
        for capacity in capacities:
            result = parse_tool_result(evaluate_stored_route_tea_lca(
                feed_mass_fractions=feed_mass_fractions,
                processing_capacity_mt_per_yr=capacity,
                product_capacity_mt_per_yr=None,
                comparison_capacities_mt_per_yr=None,
                energy_case=selected_energy_case,
                precipitation_temperature_c=selected_precipitation_c,
                product_quality_intent=product_quality_intent,
                engine_mode=engine_mode,
                timeout_seconds=timeout_seconds,
                allow_screening_estimate=allow_screening_estimate,
            ))["data"]
            if result.get("success") is not True:
                return tool_error(
                    tool, f"Stored-route scale evaluation failed at {capacity:g} mt/yr.",
                    error_code="route_scale_failed", failed_capacity_mt_per_yr=capacity,
                    cause={
                        key: result.get(key) for key in (
                            "error", "error_code", "failed_stage", "live_engine",
                        ) if result.get(key) is not None
                    },
                )
            scale_results.append(result)
        baseline = scale_results[0]
        baseline_msp = float(baseline["mass_weighted_recovered_msp_usd_per_kg"])
        baseline_gwp = float(baseline["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
        baseline_annual_gwp = float(baseline["modeled_dissolution_stage_gwp_t_co2e_per_yr"])
        if min(baseline_msp, baseline_gwp, baseline_annual_gwp) <= 0:
            return tool_error(
                tool, "Scale-comparison baseline metrics must be positive.",
                error_code="invalid_scale_baseline",
            )
        scale_rows = []
        for capacity, result in zip(capacities, scale_results):
            msp = float(result["mass_weighted_recovered_msp_usd_per_kg"])
            gwp = float(result["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
            annual_gwp = float(result["modeled_dissolution_stage_gwp_t_co2e_per_yr"])
            scale_rows.append({
                "label": f"{capacity:g}-mt-per-yr",
                "processing_capacity_mt_per_yr": capacity,
                "engine_mode": result.get("engine_mode"),
                "cache_match_status": result.get("cache_match_status"),
                "estimate_quality": result.get("estimate_quality"),
                "provenance_class": (
                    "exact_cached_simulation"
                    if result.get("engine_mode") == "cache"
                    else "cache_derived_screening_estimate"
                    if result.get("engine_mode") == "screening_estimate"
                    else "mixed_exact_and_screening"
                ),
                "capacity_factor_from_baseline": capacity / capacities[0],
                "msp_usd_per_kg": msp,
                "msp_change_usd_per_kg_from_baseline": msp - baseline_msp,
                "msp_change_percent_from_baseline": 100.0 * (msp / baseline_msp - 1.0),
                "gwp_kg_co2e_per_kg": gwp,
                "gwp_t_co2e_per_mt": gwp,
                "gwp_change_t_co2e_per_mt_from_baseline": gwp - baseline_gwp,
                "gwp_change_percent_from_baseline": 100.0 * (gwp / baseline_gwp - 1.0),
                "modeled_dissolution_stage_gwp_t_co2e_per_yr": annual_gwp,
                "annual_gwp_factor_from_baseline": annual_gwp / baseline_annual_gwp,
            })
        selected = scale_results[-1]
        analog_solvents = sorted({
            str(basis["analog_solvent"])
            for result in scale_results for row in result.get("stage_results") or []
            if (basis := row.get("estimate_basis") or {}).get("analog_solvent")
        })
        extrapolated = [
            f"{capacity:g}:{row['label']}"
            for capacity, result in zip(capacities, scale_results)
            for row in result.get("stage_results") or []
            if (row.get("estimate_basis") or {}).get("capacity_extrapolated")
        ]
        missing_temperature = sorted({
            str(row["label"])
            for result in scale_results for row in result.get("stage_results") or []
            if row.get("estimate_basis")
            and not row["estimate_basis"].get("temperature_adjustment_available")
        })
        modes = [str(result.get("engine_mode")) for result in scale_results]
        mode_set = set(modes)
        estimate = any(result.get("estimate_quality") == "screening_only" for result in scale_results)
        process_gaps = list(dict.fromkeys(
            str(gap) for result in scale_results for gap in result.get("process_data_gaps") or []
        ))
        return tool_success(
            tool, analysis_type="multistage_route_scale_comparison",
            engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
            cache_match_status=(
                "exact" if mode_set == {"cache"}
                else "surrogate" if mode_set == {"screening_estimate"}
                else "mixed_exact_and_surrogate" if "screening_estimate" in mode_set
                else "mixed_or_live"
            ),
            route_source="typed_session_state",
            feed_composition_source=selected.get("feed_composition_source"),
            requested_metrics=metrics,
            route_signature=selected.get("route_signature"),
            consumed_route=selected.get("consumed_route"),
            feed_mass_fractions=selected.get("feed_mass_fractions"),
            processing_capacity_mt_per_yr=capacities[-1],
            scale_baseline_capacity_mt_per_yr=capacities[0],
            scale_selected_capacity_mt_per_yr=capacities[-1],
            energy_case=selected.get("energy_case"),
            energy_case_description=selected.get("energy_case_description"),
            stage_results=selected.get("stage_results"),
            scale_comparison_rows=scale_rows,
            scale_comparison_basis=(
                "same stored route, typed feed composition, energy case, dissolution setpoints, "
                "and precipitation setpoint at every capacity"
            ),
            final_residue=selected.get("final_residue"),
            designated_residue_feed_fraction=selected.get("designated_residue_feed_fraction"),
            gwp_system_boundary=selected.get("gwp_system_boundary"),
            metric_units={
                **selected.get("metric_units", {}),
                "gwp_t_co2e_per_mt": "t CO2e/metric ton modeled dissolution-stage product",
            },
            provenance=selected.get("provenance"),
            process_data_gaps=process_gaps,
            estimate_quality="screening_only" if estimate else "exact_simulation_evidence",
            estimate_limitations=(
                {
                    "selected_route_solvents_directly_simulated": False,
                    "analog_solvents": analog_solvents,
                    "capacity_extrapolated_stages": extrapolated,
                    "uncertainty_interval_available": False,
                    "solvent_substitution_reflected_in_gwp": False,
                    "missing_temperature_adjustment_stages": missing_temperature,
                }
                if estimate else {}
            ),
            warnings=[
                "Scale changes use cache-derived capacity exponents and analog-solvent evidence; they are not measured scale-up data.",
                "kg CO2e/kg product is numerically equal to t CO2e/metric ton product; total annual emissions can rise while this intensity falls.",
                "The stored route does not establish recovery or an integrated-facility design, and designated-residue impacts are excluded.",
            ],
        )
    stored_composition = getattr(state, "feed_mass_fractions", None) if state else None
    composition_source = "typed_session_state" if stored_composition else "tool_argument"
    supplied_composition = stored_composition or feed_mass_fractions
    if not supplied_composition:
        return tool_error(
            tool, "No stored feed composition is available for this route.",
            error_code="missing_feed_composition", consumed_route=route,
        )
    try:
        composition = _composition(supplied_composition)
        if stored_composition and feed_mass_fractions:
            echoed = _composition(feed_mass_fractions)
            if echoed != composition:
                raise ValueError(
                    "feed_mass_fractions conflicts with the stored route composition"
                )
        capacity = _finite(feed_capacity, "processing_capacity_mt_per_yr")
        precipitation = _finite(selected_precipitation_c, "precipitation_temperature_c")
        if capacity <= 0:
            raise ValueError("processing_capacity_mt_per_yr must be positive")
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_route_basis")
    expected = {
        str(item.get("dissolved_polymer")) for item in route.get("steps") or []
        if item.get("dissolved_polymer")
    }
    if route.get("final_residue"):
        expected.add(str(route["final_residue"]))
    if set(composition) != expected:
        return tool_error(
            tool, "Feed composition must cover exactly the polymers in the stored route.",
            error_code="route_feed_mismatch", route_polymers=sorted(expected),
            supplied_polymers=sorted(composition), consumed_route=route,
        )
    remaining = dict(composition)
    results, rows = [], []
    for index, step in enumerate(route.get("steps") or [], 1):
        polymer = str(step["dissolved_polymer"])
        entering_fraction = sum(remaining.values())
        target_fraction = remaining[polymer]
        stage_capacity = round(capacity * entering_fraction, 10)
        scenario = {
            "solvent": step["solvent"], "target_polymer": polymer,
            "target_mass_percent": round(100.0 * target_fraction / entering_fraction, 10),
            "processing_capacity_mt_per_yr": stage_capacity,
            "energy_case": selected_energy_case,
            "dissolution_temp_c": step["temperature_c"],
            "precipitation_temp_c": precipitation,
        }
        try:
            config = _scenario_config(scenario)
        except _MissingScenarioBasis as error:
            result = {
                "success": False,
                "error_type": "insufficient_surrogate_evidence",
                "error": str(error),
                "config": {
                    "target_plastic": polymer,
                    "solvent": thermo.canonical_solvent_name(str(step["solvent"])),
                    "energy_case": selected_energy_case,
                    "target_plastic_percent": scenario["target_mass_percent"],
                    "processing_capacity": stage_capacity,
                    "dissolution_temperature_c": step["temperature_c"],
                    "precipitation_temperature_c": precipitation,
                },
            }
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_route_stage", stage=index)
        else:
            result = _run(config, engine_mode, max(1, min(int(timeout_seconds), 600)))
            if (
                result.get("success") is not True
                and allow_screening_estimate
                and result.get("error_type") != "invalid_engine_mode"
            ):
                result = _screening_estimate(config, result)
        label = f"stage-{index}-{polymer}"
        row = _comparison_row(label, result)
        row.update({
            "stage": index, "original_feed_mass_fraction": target_fraction,
            "entering_feed_mass_fraction": entering_fraction,
        })
        results.append(result)
        rows.append(row)
        if not result.get("success"):
            if result.get("error_type") in {
                "cache_miss", "python_version", "surrogate_unavailable",
                "insufficient_surrogate_evidence",
            }:
                return tool_error(
                    tool,
                    f"No defensible process basis is available for route stage {index} ({polymer}).",
                    error_code="uncostable_route_stage",
                    analysis_type="tea_route_basis_gap",
                    can_estimate_msp=False,
                    can_estimate_gwp=False,
                    route_source="typed_session_state",
                    route_signature=route_evidence_signature(route),
                    consumed_route=route,
                    feed_mass_fractions=composition,
                    processing_capacity_mt_per_yr=capacity,
                    requested_capacity_basis="total_feed",
                    energy_case=selected_energy_case,
                    requested_metrics=metrics,
                    failed_stage=row,
                    completed_stage_results=rows[:-1],
                    missing_basis_codes=[
                        "route_stage_process_evidence",
                        "exact_or_admitted_analog_process_basis",
                    ],
                    missing_process_inputs=[
                        f"Generate an admitted process record or validated analog basis for {polymer} in {step['solvent']} at {step['temperature_c']:g} C.",
                        "Define stage solvent loading, collection and recovery, recycle loss, and product specification on the same route basis.",
                    ],
                    process_data_gaps=[
                        "The thermodynamic route is complete, but a complete separation route is not itself a TEA/LCA process basis.",
                        f"The admitted cache has no process analog for {polymer}; records for other polymers are not substituted.",
                    ],
                    warnings=[
                        "No MSP, TCI, AOC, GWP, or energy value was calculated for the full route.",
                        "Completed earlier stage rows, if any, do not establish integrated-route economics.",
                    ],
                )
            return tool_error(
                tool, f"TEA stage {index} ({polymer}) could not be evaluated.",
                error_code="route_stage_failed", failed_stage=row,
                completed_stage_results=rows[:-1], route_source="typed_session_state",
                consumed_route=route, live_engine=live_engine_status(),
            )
        row["modeled_stage_product_mt_per_yr"] = round(capacity * target_fraction, 10)
        row["modeled_stage_gwp_t_co2e_per_yr"] = (
            row["modeled_stage_product_mt_per_yr"]
            * float(row["gwp_kg_co2e_per_kg"])
        )
        remaining.pop(polymer)
    dissolution_stage_fraction = sum(
        float(row["original_feed_mass_fraction"]) for row in rows
    )

    def weighted(field: str) -> float:
        return sum(
            float(row[field]) * float(row["original_feed_mass_fraction"])
            for row in rows
        ) / dissolution_stage_fraction

    modes = [str(row["engine_mode"]) for row in rows]
    uses_estimate = "screening_estimate" in modes
    all_estimated = set(modes) == {"screening_estimate"}
    analog_solvents = sorted({
        str(basis["analog_solvent"])
        for row in rows
        if (basis := row.get("estimate_basis") or {}).get("analog_solvent")
    })
    extrapolated_stages = [
        row["label"] for row in rows
        if (row.get("estimate_basis") or {}).get("capacity_extrapolated")
    ]
    missing_temperature_adjustment = [
        row["label"] for row in rows
        if row.get("estimate_basis")
        and not row["estimate_basis"].get("temperature_adjustment_available")
    ]
    total_stage_gwp = sum(float(row["modeled_stage_gwp_t_co2e_per_yr"]) for row in rows)
    for row in rows:
        row["modeled_stage_gwp_contribution_fraction"] = (
            float(row["modeled_stage_gwp_t_co2e_per_yr"]) / total_stage_gwp
            if total_stage_gwp > 0 else 0.0
        )
    dominant_gwp = max(rows, key=lambda row: float(row["modeled_stage_gwp_t_co2e_per_yr"]))
    return tool_success(
        tool, analysis_type="multistage_route_tea_lca",
        engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
        cache_match_status=(
            "exact" if set(modes) == {"cache"}
            else "surrogate" if all_estimated
            else "mixed_exact_and_surrogate" if uses_estimate
            else "mixed_or_live"
        ),
        route_source="typed_session_state",
        feed_composition_source=composition_source,
        requested_metrics=metrics,
        route_signature=route_evidence_signature(route),
        consumed_route=route, feed_mass_fractions=composition,
        processing_capacity_mt_per_yr=capacity, energy_case=selected_energy_case,
        energy_case_description=_ENERGY_CASES.get(selected_energy_case),
        stage_results=rows,
        dissolution_stage_feed_fraction=dissolution_stage_fraction,
        final_residue=route.get("final_residue"),
        designated_residue_feed_fraction=sum(remaining.values()),
        total_stage_tci_usd=sum(float(row["tci_usd"]) for row in rows),
        total_stage_aoc_usd_per_yr=sum(float(row["aoc_usd_per_yr"]) for row in rows),
        mass_weighted_recovered_msp_usd_per_kg=weighted("msp_usd_per_kg"),
        mass_weighted_recovered_gwp_kg_co2e_per_kg=weighted("gwp_kg_co2e_per_kg"),
        modeled_dissolution_stage_gwp_t_co2e_per_yr=total_stage_gwp,
        dominant_gwp_stage={
            key: dominant_gwp[key] for key in (
                "stage", "polymer", "solvent", "modeled_stage_product_mt_per_yr",
                "gwp_kg_co2e_per_kg", "modeled_stage_gwp_t_co2e_per_yr",
                "modeled_stage_gwp_contribution_fraction",
            )
        },
        gwp_contribution_basis=(
            "stage product mass from typed feed composition multiplied by stage GWP intensity; "
            "excludes designated-residue impacts and does not establish recovery"
        ),
        gwp_system_boundary=(
            "cached process-model cradle-to-gate screen per modeled dissolution-stage product; "
            "not a full product life cycle"
        ),
        metric_units={key: unit for key, (_, _, unit) in _METRICS.items()},
        provenance=_provenance(modes),
        process_details=[
            detail for row, result in zip(rows, results)
            if (detail := _process_details(str(row["label"]), result)) is not None
        ],
        process_data_gaps=(
            ["Cached corpus does not contain unit-level equipment sizes or full stream mass balances."]
            if set(modes) == {"cache"} or uses_estimate else []
        ) + ([
            "Stage economics are cache-derived analog estimates, not simulations of the selected solvents or an integrated plant.",
            "The screening estimator does not provide uncertainty or confidence intervals.",
            "Solvent prices are adjusted from the V12-0 TEA assumptions table; solvent substitution is not reflected in estimated GWP.",
        ] + ([
            "Requested capacities are outside the cached scale range for: "
            + ", ".join(extrapolated_stages) + "."
        ] if extrapolated_stages else []) + ([
            "No cached temperature sensitivity was available for: "
            + ", ".join(missing_temperature_adjustment) + "."
        ] if missing_temperature_adjustment else []) if uses_estimate else [])
        + _live_toxicity_data_gaps(results),
        estimate_quality="screening_only" if uses_estimate else "exact_simulation_evidence",
        estimate_limitations=(
            {
                "selected_route_solvents_directly_simulated": False,
                "analog_solvents": analog_solvents,
                "capacity_extrapolated_stages": extrapolated_stages,
                "uncertainty_interval_available": False,
                "solvent_substitution_reflected_in_gwp": False,
                "missing_temperature_adjustment_stages": missing_temperature_adjustment,
            }
            if uses_estimate else {}
        ),
        warnings=[
            "The stored thermodynamic route supplies solvents and setpoints; it does not establish stage recovery or purity.",
            "Summed TCI/AOC assume separate stage simulations and are not an integrated-facility design or cash flow.",
            "Mass-weighted MSP/GWP cover modeled dissolved-stage products only; the final residue is not economically credited.",
            (
                "Screening estimates fit cache-derived capacity exponents and apply source-table solvent-price and available temperature adjustments."
                if uses_estimate else
                "Cached results are exact prior subprocess simulations, never interpolated process economics."
            ),
            "Cached energy intensities correct the legacy worker's annual-total/hourly-flow normalization error using 8,409.6 operating hours per year.",
        ],
    )


def _metric(row: dict[str, Any], metric: str) -> Optional[float]:
    section, key, _ = _METRICS[metric]
    value = (row.get(section) or {}).get(key)
    return None if value is None else float(value)


def analyze_tea_sensitivity(
    scenario: dict[str, Any],
    parameter: str,
    values: Optional[list[float]] = None,
    metric: str = "msp_usd_per_kg",
    analysis_mode: str = "sweep",
    engine_mode: str = "auto",
    timeout_seconds: int = 180,
) -> str:
    """Run a deterministic parameter sweep/tornado slice or sampled uncertainty summary.

    With values omitted, exact cached one-at-a-time variants are discovered
    around the supplied baseline. Supported parameter names are the numeric
    scenario fields. `uncertainty` describes only the supplied/discovered
    scenario sample; it is not a probabilistic Monte Carlo claim.
    """
    tool = "analyze_tea_sensitivity"
    aliases = {
        "target_mass_percent": "target_plastic_percent",
        "processing_capacity_mt_per_yr": "processing_capacity",
        "dissolution_temp_c": "dissolution_temperature_c",
        "precipitation_temp_c": "precipitation_temperature_c",
    }
    field = aliases.get(str(parameter or "").strip(), str(parameter or "").strip())
    mode = str(analysis_mode or "sweep").casefold()
    if field not in _NUMERIC_FIELDS or field in {"dissolution_capacity", "labor_cost"}:
        return tool_error(tool, "Unsupported sensitivity parameter.", error_code="unsupported_parameter", supported_parameters=sorted(_NUMERIC_FIELDS - {"dissolution_capacity", "labor_cost"}))
    if metric not in _METRICS:
        return tool_error(tool, "Unsupported sensitivity metric.", error_code="unsupported_metric", supported_metrics=sorted(_METRICS))
    if mode not in {"sweep", "tornado", "uncertainty"}:
        return tool_error(tool, "analysis_mode must be sweep, tornado, or uncertainty", error_code="invalid_analysis_mode")
    try:
        baseline = _scenario_config(scenario)
        requested_values = [] if values is None else [_finite(value, field) for value in values]
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_sensitivity_basis")
    if not requested_values:
        comparison_key = {key: value for key, value in baseline.items() if key != field}
        requested_values = sorted({
            float(record["config"][field]) for record in _records()
            if all(
                _key(record["config"][key]) == _key(value)
                if key in {"solvent", "target_plastic"}
                else str(record["config"][key]).upper() == str(value).upper()
                if key == "energy_case"
                else abs(float(record["config"][key]) - float(value)) < 1e-9
                for key, value in comparison_key.items()
            )
        })
    requested_values = list(dict.fromkeys([float(baseline[field]), *requested_values]))
    if len(requested_values) < 2:
        return tool_error(tool, "At least two exact sensitivity values are required.", error_code="insufficient_sensitivity_values")
    if len(requested_values) > 20:
        return tool_error(tool, "At most 20 sensitivity values may run per call.", error_code="too_many_sensitivity_values")
    results = [_run({**baseline, field: value}, engine_mode, max(1, min(int(timeout_seconds), 600))) for value in requested_values]
    rows = []
    for value, result in zip(requested_values, results):
        measured = _metric(result, metric) if result.get("success") else None
        rows.append({
            "parameter": field, "value": value, "metric": metric,
            "metric_value": measured, "success": bool(result.get("success")),
            "engine_mode": result.get("engine_mode"),
            "cache_record_label": result.get("cache_record_label"),
            **({"error": result.get("error")} if not result.get("success") else {}),
        })
    successes = [row for row in rows if row["success"] and row["metric_value"] is not None]
    if len(successes) < 2:
        return tool_error(tool, "Fewer than two sensitivity scenarios completed.", error_code="insufficient_sensitivity_results", sensitivity_rows=rows, live_engine=live_engine_status())
    values_out = [float(row["metric_value"]) for row in successes]
    baseline_row = min(successes, key=lambda row: abs(float(row["value"]) - float(baseline[field])))
    modes = [str(row["engine_mode"]) for row in successes]
    return tool_success(
        tool, analysis_type=f"tea_{mode}", engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
        polymer=baseline["target_plastic"], solvent=baseline["solvent"],
        energy_case=baseline["energy_case"],
        processing_capacity_mt_per_yr=baseline["processing_capacity"],
        temperature_c=baseline["dissolution_temperature_c"],
        parameter=field, metric=metric, metric_unit=_METRICS[metric][2],
        baseline_parameter_value=baseline[field], baseline_metric_value=baseline_row["metric_value"],
        sensitivity_rows=rows,
        minimum_metric_value=min(values_out), maximum_metric_value=max(values_out),
        metric_span=max(values_out) - min(values_out),
        sample_median_metric_value=statistics.median(values_out),
        sample_count=len(successes), provenance=_provenance(modes),
        warnings=[
            "This is a one-parameter scenario analysis; it does not establish causal sensitivity outside the evaluated values.",
            "An uncertainty-mode summary describes the finite scenario sample, not a fitted probability distribution or Monte Carlo confidence interval."
            if mode == "uncertainty" else
            "Cached sensitivity points are exact prior simulations and are not interpolated between evaluated values.",
        ],
    )


BIOSTEAM_PROMPT = """You are DISSOLVE's BioSTEAM TEA/LCA specialist. Call
exactly one scoped tool and return no prose. Use lookup_admitted_process_records
when the user explicitly asks for admitted or cached process records by polymer,
solvent, energy case, or sensitivity label; this exact-record path does not
require a stored route. Preserve the stated polymer, solvent, dissolution
temperature, plant capacity, energy cases, sensitivity axes, and requested
metrics. An uncached combination must remain the tool's bounded data gap.
Use evaluate_stored_route_tea_lca
when a prior separation route should be costed: the tool itself reads the exact
typed route and stored feed composition, so never reconstruct either as model
arguments. Also use that tool when the current typed result is only a
precipitation/selectivity candidate screen and the user says "this route"; the
tool will return the bounded missing-basis assessment rather than promoting the
screen into a process route. Never use evaluate_tea_lca_scenarios to fill that
gap. If the user requests a recovered-product capacity, pass it as
product_capacity_mt_per_yr; use processing_capacity_mt_per_yr only for explicit
total-feed capacity. Preserve a named product-quality intent in
product_quality_intent. Obey typed_session_context.declared_request_kind. An
explicit admitted-record request without a stored route takes precedence over
route-variant and generic sensitivity tools. For tea_feed_economics, always pass
every current-question label in feed_polymers. Also pass
feed_mass_fractions only when the user supplied exact fractions, plus the
total-feed capacity and every requested metric. Current-question feed labels
take precedence over stale candidate screens. Never resolve generic PE to LDPE
or HDPE; the tool will return the bounded clarification and process-basis gap.
For tea_stored_route, never pass feed_polymers:
the typed route owns those identities. When the user supplies exact fractions
and typed_session_context.feed_mass_fractions is empty, pass only
feed_mass_fractions. When typed_session_context.feed_mass_fractions is already
nonempty, omit both feed arguments.
For tea_route_variant_comparison, set compare_route_variants=true; the tool
evaluates the stored original and substituted routes on one feed,
capacity, energy-case, and metric basis and preserves exact-versus-screening
provenance. Do not reconstruct either route in arguments.
Preserve the user's plant capacity,
energy case, and precipitation assumption. When the user explicitly requests
an estimate and the selected route
lacks exact cache/live evidence, set allow_screening_estimate=true; this permits
only a clearly labeled cache-analog screening estimate. Also preserve that opt-in
for a direct follow-up when typed_session_context.last_tea.estimate_quality is
screening_only. For a scale comparison of the stored route, use its prior
processing capacity as `processing_capacity_mt_per_yr` and put every new scale
in `comparison_capacities_mt_per_yr`; do not reconstruct independent scenarios.
For a scale follow-up to an unresolved feed-level basis, use that same tool,
omit the feed argument so it consumes the preserved typed feed, pass the prior
total-feed capacity as the baseline, put new scales in
`comparison_capacities_mt_per_yr`, preserve the named energy case, and request
only the asked-for metrics. The bounded result reports conditional cache
coverage; never promote it into economies of scale or an emissions trend. Do
not use analyze_tea_sensitivity until a complete route basis exists.
For a process life-cycle GWP comparison of the current solvent shortlist, call
evaluate_stored_route_tea_lca with requested_metrics=["gwp"]. The tool consumes
the exact typed candidates even after an intervening safety turn. Never infer
GWP from G-score, Hansen RED, flash/GHS, or exposure-limit evidence.
Use evaluate_tea_lca_scenarios only for complete independent process scenarios
supplied by the user, never for "those" or current/prior candidates. Use
analyze_tea_sensitivity for scale, price, loss, temperature,
distance, sweep, uncertainty-sample, or tornado questions. Default to
engine_mode=auto, which accepts only exact cache records or an available live
subprocess unless the explicit screening-estimate opt-in above applies. Do not
invent feed fractions, metrics, recovery, purity, integration, or economic
assumptions. When the user asks for an unnamed count of highest-value products,
use the stored-route tool with `requested_product_count`,
`product_selection_basis="highest_value"`, the exact requested metrics, and the
stated recovered-product capacity. Do not name target products: the tool must
return the missing product-value and process basis. Pass exactly
typed_session_context.declared_requested_metrics as requested_metrics. MSP plus
GWP is ["msp", "gwp"]; introductory wording such as "cost and carbon" adds no
separate tokens. TEA plus LCA without a narrower declared subset is
["msp", "tci", "aoc", "gwp"]."""
