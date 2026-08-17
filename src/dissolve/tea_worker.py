"""Fresh-process BioSTEAM execution for the optional external STRAP model."""

from __future__ import annotations

import importlib
import json
import math
import os
import sys
import time
import traceback
from types import SimpleNamespace
from typing import Any, Callable, Optional

_ENERGY = {
    "C1": {"facilities": True, "turbogenerator": True},
    "C2": {"facilities": False, "turbogenerator": False},
    "C3": {"facilities": True, "turbogenerator": False},
}
_TARGET = {
    "LDPE": "PE", "HDPE": "PE", "EVOH": "EVOH", "PC": "PC",
}
_UNSUPPORTED_LIVE_TARGETS = frozenset({
    "PET", "PS", "PP", "PVC", "NYLON6", "NYLON66",
})
_TOXICITY_METRICS = (
    ("htc_ctuh_per_kg", "HTC", "htc"),
    ("htnc_ctuh_per_kg", "HTNC", "htnc"),
    ("etox_ctue_per_kg", "ETOX", "etox"),
)


class _NullBoiler:
    def __init__(self):
        self.ins = [None]
        self.natural_gas_price = 0.0
        self.design_results: dict[str, float] = {}
        self.blowdown_water = SimpleNamespace(imass={"Water": 0.0})


def _patch_process(process_class: Any) -> Any:
    if getattr(process_class, "_dissolve_worker_patched", False):
        return process_class
    original = process_class.create_model

    def create_model(instance: Any, *args: Any, **kwargs: Any) -> Any:
        if not instance.scenario.turbogenerator and not hasattr(instance, "B"):
            instance.B = getattr(instance, "BT", _NullBoiler())
        result = original(instance, *args, **kwargs)
        if not hasattr(instance, "BT") and hasattr(instance, "B"):
            instance.BT = instance.B
        return result

    process_class.create_model = create_model
    process_class._dissolve_worker_patched = True
    return process_class


def _safe(function: Callable[[], Any]) -> Any:
    try:
        value = function()
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    except Exception:
        return None


def _scalar_mapping(values: Any, limit: int = 8) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(values or {}).items():
        if len(result) >= limit:
            break
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            result[str(key)] = float(value)
        elif isinstance(value, str):
            result[str(key)] = value
    return result


def _set_characterization_factors(process: Any, factors: dict[str, float], facilities: bool) -> None:
    if not factors:
        return
    if facilities:
        for metric, key in (
            ("GWP", "natural_gas_gwp"), ("HTC", "natural_gas_htc"),
            ("HTNC", "natural_gas_htnc"), ("ETOX", "natural_gas_etox"),
        ):
            if factors.get(key) is not None:
                process.natural_gas.set_CF(metric, factors[key])
    for metric, key, offset in (
        ("GWP", "solvent_gwp", 0.1563), ("HTC", "solvent_htc", 3.56126e-10),
        ("HTNC", "solvent_htnc", 8.0464e-9), ("ETOX", "solvent_etox", 0.009),
    ):
        if factors.get(key) is not None:
            process.solvent.set_CF(metric, factors[key] + offset)
    for water_name in ("makeup_water", "cooling_tower_makeup_water"):
        stream = getattr(process, water_name, None)
        if stream is None:
            continue
        for metric, key, default in (
            ("GWP", "water_gwp", 0.000127), ("HTC", "water_htc", 1.40e-10),
            ("HTNC", "water_htnc", 7.96e-11), ("ETOX", "water_etox", 0.00538),
        ):
            stream.set_CF(metric, factors.get(key, default))
    electricity_factors = (
        ("GWP", "electricity_gwp"), ("HTC", "electricity_htc"),
        ("HTNC", "electricity_htnc"), ("ETOX", "electricity_etox"),
    )
    if any(factors.get(key) is not None for _metric, key in electricity_factors):
        biosteam = importlib.import_module("biosteam")
        for metric, key in electricity_factors:
            if factors.get(key) is not None:
                biosteam.settings.set_electricity_CF(
                    metric, factors[key], basis="MJ",
                )


def _required_toxicity_factors(
    metric_suffix: str, energy_case: str,
) -> tuple[str, ...]:
    """Return the process contributors needed for one toxicity indicator."""
    required = [f"solvent_{metric_suffix}"]
    if _ENERGY[energy_case]["facilities"]:
        required.append(f"natural_gas_{metric_suffix}")
    if energy_case in {"C2", "C3"}:
        required.append(f"electricity_{metric_suffix}")
    return tuple(required)


def _lca_payload(
    process: Any,
    factors: dict[str, float],
    energy_case: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Emit toxicity only when every contributing CF for that metric exists."""
    lca = {"gwp_kg_co2e_per_kg": _safe(process.GWP)}
    emitted: list[str] = []
    omitted: list[str] = []
    missing_by_metric: dict[str, list[str]] = {}
    calculation_unavailable: list[str] = []
    for output_field, process_method, suffix in _TOXICITY_METRICS:
        required = _required_toxicity_factors(suffix, energy_case)
        missing = [key for key in required if factors.get(key) is None]
        if missing:
            omitted.append(output_field)
            missing_by_metric[output_field] = missing
            continue
        value = _safe(getattr(process, process_method))
        if value is None:
            omitted.append(output_field)
            calculation_unavailable.append(output_field)
            continue
        lca[output_field] = value
        emitted.append(output_field)

    status = (
        "available"
        if len(emitted) == len(_TOXICITY_METRICS)
        else "partial" if emitted else "unavailable"
    )
    coverage: dict[str, Any] = {
        "toxicity_metrics_status": status,
        "toxicity_metrics_emitted": emitted,
        "toxicity_metrics_omitted": omitted,
        "characterization_factor_source": (
            "config.lca_cfs" if factors else "not_supplied"
        ),
    }
    if missing_by_metric:
        coverage["reason"] = "missing_characterization_factors"
        coverage["missing_characterization_factors"] = missing_by_metric
    elif calculation_unavailable:
        coverage["reason"] = "toxicity_calculation_unavailable"
    if calculation_unavailable:
        coverage["calculation_unavailable_metrics"] = calculation_unavailable
    return lca, coverage


def run(config: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    original_target = str(config["target_plastic"]).upper()
    if original_target in _UNSUPPORTED_LIVE_TARGETS:
        return {
            "success": False,
            "error": (
                "Live TEA has no target-specific process model for "
                f"{original_target}; refusing to substitute the PE process."
            ),
            "error_type": "unsupported_live_target",
            "target_plastic": original_target,
            "supported_live_targets": sorted(_TARGET),
        }
    if sys.version_info < (3, 12):
        raise RuntimeError("Live BioSTEAM execution requires Python 3.12 or newer")
    if path := str(os.getenv("DISSOLVE_PLASTICS_PATH") or "").strip():
        sys.path.insert(0, os.path.abspath(os.path.expanduser(path)))
    strap_package = importlib.import_module("plastics.strap")
    process_class = _patch_process(getattr(strap_package, "BaselineSTRAPProcess"))
    try:
        strap_package.STRAP_chemicals_outline.append("HCl")
    except Exception:
        pass

    target = _TARGET.get(original_target, original_target)
    energy_case = str(config.get("energy_case") or "C1").upper()
    energy = _ENERGY.get(energy_case)
    if energy is None:
        raise ValueError("energy_case must be C1, C2, or C3")
    scenario = process_class.Scenario(
        solvent=config["solvent"], target_plastic=target,
        target_plastic_percent=config["target_plastic_percent"],
        processing_capacity=config["processing_capacity"],
        sell_leftover_plastic=False, burn_leftover_plastic=False,
        facilities=energy["facilities"], turbogenerator=energy["turbogenerator"],
        precipitation_temperature_format="constant",
    )
    process = process_class(scenario=scenario)
    try:
        process.T1.disconnect(join_ends=True)
        process.U1.disconnect(join_ends=True)
        process.system.update_configuration(
            units=[unit for unit in process.system.units if unit not in (process.T1, process.U1)]
        )
    except Exception:
        pass
    process.tea.labor_cost = config.get("labor_cost", 120_000)
    process.set_solvent_price(config["solvent_price"])
    process.set_feedstock_distance(config.get("feedstock_distance_km", 0))
    process.set_solvent_loss(config.get("solvent_loss_pct", 0.01) / 100.0)
    process.set_dissolution_temperature(config["dissolution_temperature_c"] + 273.15)
    process.set_precipitation_temperature(config["precipitation_temperature_c"] + 273.15)
    process.set_dissolution_capacity(config.get("dissolution_capacity", 3))
    lca_cfs = dict(config.get("lca_cfs") or {})
    _set_characterization_factors(process, lca_cfs, bool(energy["facilities"]))
    process.system.simulate()
    lca, lca_coverage = _lca_payload(process, lca_cfs, energy_case)

    resin = None
    for name in (f"{target}_resin", "PE_resin", "resin"):
        candidate = getattr(process, name, None)
        if candidate is not None and getattr(candidate, "F_mass", 0) > 0:
            resin = candidate
            break
    mass = getattr(resin, "F_mass", None)
    annual_product_mass = mass * process.tea.operating_hours if mass else None
    electricity = _safe(
        lambda: process.system.get_electricity_consumption() * 3.6 / annual_product_mass
    ) if annual_product_mass else None
    heating = _safe(
        lambda: process.system.get_heating_duty() * 0.001 / annual_product_mass
    ) if annual_product_mass else None
    cooling = _safe(
        lambda: process.system.get_cooling_duty() * 0.001 / annual_product_mass
    ) if annual_product_mass else None
    total_energy = (
        electricity + heating + cooling
        if None not in (electricity, heating, cooling) else None
    )
    water_consumed = water_circulated = None
    try:
        cooling_tower = process.CT
        consumed = (
            cooling_tower.blowdown_water.imass["Water"]
            + cooling_tower.evaporation_water.imass["Water"]
        ) / 1000.0
        circulated = cooling_tower.cooling_water.imass["Water"] / 1000.0
        if energy["facilities"]:
            consumed += process.BT.blowdown_water.imass["Water"] / 1000.0
            circulated += process.BT.design_results["Flow rate"] / 1000.0
        annual = 24 * process.tea.operating_days
        water_consumed, water_circulated = consumed * annual, circulated * annual
    except Exception:
        pass
    equipment = []
    for unit in list(process.system.units):
        equipment.append({
            "unit_id": str(getattr(unit, "ID", "")),
            "unit_type": type(unit).__name__,
            "installed_cost_usd": _safe(lambda unit=unit: unit.installed_cost),
            "design_results": _scalar_mapping(getattr(unit, "design_results", {})),
        })
    streams = sorted(
        list(process.system.streams),
        key=lambda stream: float(getattr(stream, "F_mass", 0.0) or 0.0),
        reverse=True,
    )
    stream_balance = [{
        "stream_id": str(getattr(stream, "ID", "")),
        "mass_flow_kg_per_hr": _safe(lambda stream=stream: stream.F_mass),
        "phase": str(getattr(stream, "phase", "")),
    } for stream in streams[:20]]
    feed_mass = sum(float(getattr(stream, "F_mass", 0.0) or 0.0) for stream in process.system.feeds)
    product_mass = sum(float(getattr(stream, "F_mass", 0.0) or 0.0) for stream in process.system.products)
    return {
        "success": True, "solvent": config["solvent"],
        "target_plastic": original_target, "simulated_as": target,
        "energy_case": energy_case,
        "tea": {
            "msp_usd_per_kg": _safe(process.MSP),
            "tci_usd": _safe(lambda: process.tea.TCI),
            "aoc_usd_per_yr": _safe(lambda: process.tea.AOC),
        },
        "lca": lca,
        "lca_coverage": lca_coverage,
        "operations": {
            "water_consumed_m3_yr": water_consumed,
            "water_circulated_m3_yr": water_circulated,
            "electricity_consumed_mj_per_kg": electricity,
            "heating_duty_mj_per_kg": heating,
            "cooling_duty_mj_per_kg": cooling,
            "total_energy_mj_per_kg": total_energy,
            "waste_generated_kg_yr": _safe(lambda: process.spent_activated_carbon.F_mass * process.tea.operating_hours),
            "waste_diverted_kg_yr": _safe(lambda: resin.F_mass * process.tea.operating_hours) if resin else None,
            "unit_operations": _safe(lambda: len(process.system.units)),
        },
        "process_details": {
            "feed_mass_flow_kg_per_hr": feed_mass,
            "product_mass_flow_kg_per_hr": product_mass,
            "mass_balance_closure_kg_per_hr": feed_mass - product_mass,
            "stream_mass_balance": stream_balance,
            "equipment": equipment,
        },
        "energy_normalization": {
            "status": "annual_energy_over_annual_product_mass",
            "operating_hours_per_year": process.tea.operating_hours,
            "basis": "BioSTEAM annual kWh-or-kJ divided by annual resin kg",
        },
        "runtime_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    config: dict[str, Any] = {}
    try:
        if len(sys.argv) != 2:
            raise ValueError("Pass one JSON configuration argument")
        config = json.loads(sys.argv[1])
        result = run(config)
        code = 0 if result.get("success") is True else 1
    except Exception as error:
        result = {
            "success": False, "error": str(error),
            "error_type": type(error).__name__,
            "solvent": config.get("solvent"),
            "target_plastic": config.get("target_plastic"),
            "energy_case": config.get("energy_case"),
        }
        print(traceback.format_exc(), file=sys.stderr)
        code = 1
    print(json.dumps(result, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
