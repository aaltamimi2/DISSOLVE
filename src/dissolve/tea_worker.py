"""Fresh-process BioSTEAM execution for the optional external STRAP model."""

from __future__ import annotations

import importlib
import importlib.util
import hashlib
import json
import math
import os
import platform
import re
import sys
import time
import traceback
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Optional

_ENERGY = {
    "C1": {"facilities": True, "turbogenerator": True},
    "C2": {"facilities": False, "turbogenerator": False},
    "C3": {"facilities": True, "turbogenerator": False},
}
# BaselineSTRAPProcess set_natural_gas_price @parameter baseline (USD/m3).
_NATURAL_GAS_PRICE_USD_PER_M3 = 4.73 * 35.3146667 / 1e3
_IRR_DEFAULT = 0.10
_INCOME_TAX_DEFAULT = 0.21
_OPERATING_DAYS_DEFAULT = 350.4
_FEEDSTOCK_PRICE_USD_PER_KG = 0.01
_CENTRIFUGED_PLASTIC_SOLVENT_CONTENT_PCT = 50.0


def _polymer_parameters() -> ModuleType:
    """Load the expert surface without importing the dissolve package.

    The live child is executed via runpy as ``__main__``. A package import
    of ``dissolve`` would run ``dissolve.__init__`` and pull the rest of
    the engine into the BioSTEAM worker. File-loading the sibling keeps
    the isolation the cache generator depends on.

    The loaded module MUST be registered in ``sys.modules`` before
    ``exec_module``. ``@dataclass`` looks up ``sys.modules[cls.__module__]``;
    an unregistered spec leaves that entry ``None`` and the child dies
    before it can write JSON.
    """
    if __package__:
        from . import tea_polymer_parameters as module
        return module
    name = "_dissolve_tea_polymer_parameters"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).resolve().parent / "tea_polymer_parameters.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load polymer parameter surface from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _live_identity_map() -> dict[str, str]:
    return dict(_polymer_parameters().live_identity_map())


# Derived from tea_polymer_parameters.POLYMERS. Not the admission
# authority — that is admit_live_target() over the table + package
# outline. Kept so tea.py can still ask "which grid names are live".
_TARGET = _live_identity_map()
_LCA_METRICS = (
    ("gwp_kg_co2e_per_kg", "GWP", "GWP"),
    ("htc_ctuh_per_kg", "HTC", "htc"),
    ("htnc_ctuh_per_kg", "HTNC", "htnc"),
    ("etox_ctue_per_kg", "ETOX", "etox"),
)
_TOXICITY_OUTPUT_FIELDS = tuple(row[0] for row in _LCA_METRICS[1:])
_ACTIVE_FLOW_EPSILON = 1e-12
_LIVE_RUNTIME_MODULES = ("biosteam", "thermosteam", "biorefineries")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CAS_NUMBER_RE = re.compile(r"^[0-9]{2,7}-[0-9]{2}-[0-9]$")


class _NullBoiler:
    def __init__(self):
        self.ins = [None]
        self.natural_gas_price = 0.0
        self.design_results: dict[str, float] = {}
        self.blowdown_water = SimpleNamespace(imass={"Water": 0.0})


class _LiveSolventModelError(RuntimeError):
    """One admitted solvent reached a failing external-model phase."""

    def __init__(
        self,
        phase: str,
        original: Exception,
        engine_identity: dict[str, Any],
    ) -> None:
        super().__init__(str(original))
        self.phase = phase
        self.original = original
        self.engine_identity = engine_identity


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


def _set_characterization_factors(
    process: Any,
    factors: dict[str, float],
    facilities: bool,
    factor_bases: dict[str, str],
    solvent_transport_offsets: Optional[dict[str, float]] = None,
) -> None:
    if not factors:
        return
    expected_bases = {
        "natural_gas": "kg", "water": "kg",
        "solvent": "kg", "electricity": "MJ",
    }
    active_contributors = {
        key.split("_", 1)[0]
        for key in factors
        if key.startswith(("water_", "solvent_", "electricity_"))
    }
    if any(key.startswith("natural_gas_") for key in factors):
        active_contributors.add("natural_gas")
    mismatches = {
        contributor: factor_bases.get(contributor)
        for contributor in active_contributors
        if factor_bases.get(contributor) != expected_bases[contributor]
    }
    if mismatches:
        raise ValueError(
            "Unsupported LCA factor application basis: "
            + ", ".join(
                f"{name}={basis or '(missing)'}"
                for name, basis in sorted(mismatches.items())
            )
        )
    if facilities:
        for metric, key in (
            ("GWP", "natural_gas_gwp"), ("HTC", "natural_gas_htc"),
            ("HTNC", "natural_gas_htnc"), ("ETOX", "natural_gas_etox"),
        ):
            if factors.get(key) is not None:
                process.natural_gas.set_CF(
                    metric, factors[key], basis=factor_bases["natural_gas"],
                )
    for metric, key in (
        ("GWP", "solvent_gwp"), ("HTC", "solvent_htc"),
        ("HTNC", "solvent_htnc"), ("ETOX", "solvent_etox"),
    ):
        if factors.get(key) is not None:
            suffix = key.removeprefix("solvent_")
            offset = float((solvent_transport_offsets or {}).get(suffix, 0.0))
            process.solvent.set_CF(
                metric, factors[key] + offset,
                basis=factor_bases["solvent"],
            )
    seen_water_streams: set[int] = set()
    for water_name in ("makeup_water", "cooling_tower_makeup_water"):
        stream = getattr(process, water_name, None)
        if stream is None or id(stream) in seen_water_streams:
            continue
        seen_water_streams.add(id(stream))
        for metric, key in (
            ("GWP", "water_gwp"), ("HTC", "water_htc"),
            ("HTNC", "water_htnc"), ("ETOX", "water_etox"),
        ):
            if factors.get(key) is not None:
                stream.set_CF(
                    metric, factors[key], basis=factor_bases["water"],
                )
    electricity_factors = (
        ("GWP", "electricity_gwp"), ("HTC", "electricity_htc"),
        ("HTNC", "electricity_htnc"), ("ETOX", "electricity_etox"),
    )
    if any(factors.get(key) is not None for _metric, key in electricity_factors):
        biosteam = importlib.import_module("biosteam")
        for metric, key in electricity_factors:
            if factors.get(key) is not None:
                biosteam.settings.set_electricity_CF(
                    metric, factors[key], basis=factor_bases["electricity"],
                )


def _is_internal_utility_circulation(stream: Any) -> bool:
    """Return whether a system feed is an internal utility return carrier.

    BioSTEAM represents some cooling-loop returns as ``system.feeds`` because
    their facility source is abstract rather than a process unit. They are not
    purchased inputs and must not be mistaken for uncharacterized inventory.
    Utility-agent facilities receive that return as their first inlet; later
    inlets remain ordinary purchased makeup or chemical feeds. The decision is
    therefore made from graph role and the registered utility agent, never from
    observed stream names or facility classes.
    """
    sink = getattr(stream, "sink", None)
    if sink is None:
        return False
    agent = getattr(sink, "agent", None)
    if agent is None or not callable(getattr(agent, "to_stream", None)):
        return False
    try:
        inlets = list(getattr(sink, "ins", ()) or ())
    except Exception:
        return False
    return bool(inlets and inlets[0] is stream)


def _active_lca_contributors(process: Any) -> list[dict[str, Any]]:
    """Enumerate active external contributors and their actual CF registries."""
    system = process.system
    contributors: list[dict[str, Any]] = []
    for index, stream in enumerate(list(getattr(system, "feeds", ()) or ())):
        mass = float(getattr(stream, "F_mass", 0.0) or 0.0)
        if mass <= _ACTIVE_FLOW_EPSILON or _is_internal_utility_circulation(stream):
            continue
        name = str(getattr(stream, "ID", "") or "").strip()
        contributors.append({
            "kind": "feed",
            "name": name or f"unnamed_feed_{index + 1}",
            "characterization_factors": (
                getattr(stream, "characterization_factors", {}) or {}
            ),
        })

    for utility in list(getattr(system, "heat_utilities", ()) or ()):
        flow = float(getattr(utility, "flow", 0.0) or 0.0)
        agent = getattr(utility, "agent", None)
        if flow <= _ACTIVE_FLOW_EPSILON or not bool(
            getattr(agent, "isheating_agent", False)
        ):
            continue
        agent_id = str(getattr(agent, "ID", "") or "").strip()
        registry = getattr(utility, "characterization_factors", {}) or {}
        contributors.append({
            "kind": "purchased_heat",
            "name": agent_id or "unnamed_heat_utility",
            "characterization_factors": {
                metric: registry[agent_id, metric]
                for _field, metric, _suffix in _LCA_METRICS
                if (agent_id, metric) in registry
            },
        })

    power = getattr(system, "power_utility", None)
    rate = float(getattr(power, "rate", 0.0) or 0.0) if power is not None else 0.0
    if rate > _ACTIVE_FLOW_EPSILON:
        contributors.append({
            "kind": "purchased_electricity",
            "name": "electricity",
            "characterization_factors": (
                getattr(power, "characterization_factors", {}) or {}
            ),
        })
    return contributors


def _evaluate_lca_metrics(
    process: Any,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Evaluate impacts in the live order that reproduces cached economics."""
    lca: dict[str, Any] = {}
    emitted: list[str] = []
    calculation_unavailable: list[str] = []
    for output_field, process_method, _suffix in _LCA_METRICS:
        value = _safe(getattr(process, process_method))
        if value is None:
            calculation_unavailable.append(output_field)
        else:
            lca[output_field] = value
            emitted.append(output_field)
    return lca, emitted, calculation_unavailable


def _lca_payload(
    process: Any,
    factors: dict[str, float],
    energy_case: str,
    factor_provenance: Optional[dict[str, Any]] = None,
    evaluated_metrics: Optional[
        tuple[dict[str, Any], list[str], list[str]]
    ] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Emit cache-comparable values and separately disclose graph coverage."""
    del energy_case  # Coverage follows the simulated graph, never a case label.
    if evaluated_metrics is None:
        evaluated_metrics = _evaluate_lca_metrics(process)
    lca, emitted, calculation_unavailable = evaluated_metrics

    contributors = _active_lca_contributors(process)
    missing_by_metric: dict[str, list[dict[str, str]]] = {}
    grouped_missing: dict[tuple[str, str], set[str]] = {}
    metric_status: dict[str, str] = {}
    for output_field, process_method, _suffix in _LCA_METRICS:
        missing = [
            {"kind": item["kind"], "name": item["name"]}
            for item in contributors
            if process_method not in item["characterization_factors"]
        ]
        if missing:
            missing_by_metric[output_field] = missing
            for item in missing:
                grouped_missing.setdefault(
                    (item["kind"], item["name"]), set(),
                ).add(output_field)
        if output_field not in lca:
            metric_status[output_field] = "unavailable"
            continue
        metric_status[output_field] = "partial" if missing else "available"

    toxicity_emitted = [
        field for field in _TOXICITY_OUTPUT_FIELDS if field in emitted
    ]
    toxicity_omitted = [
        field for field in _TOXICITY_OUTPUT_FIELDS if field not in emitted
    ]
    toxicity_statuses = {
        metric_status.get(field, "unavailable")
        for field in _TOXICITY_OUTPUT_FIELDS
    }
    toxicity_status = (
        "unavailable" if toxicity_statuses == {"unavailable"}
        else "partial" if "partial" in toxicity_statuses or "unavailable" in toxicity_statuses
        else "available"
    )
    all_statuses = set(metric_status.values())
    overall_status = (
        "unavailable" if all_statuses == {"unavailable"}
        else "partial" if "partial" in all_statuses or "unavailable" in all_statuses
        else "available"
    )
    source = str((factor_provenance or {}).get("source") or (
        "config.lca_cfs" if factors else "not_supplied"
    ))
    coverage: dict[str, Any] = {
        "status": overall_status,
        "lca_metrics_status": overall_status,
        "metric_coverage_status": metric_status,
        "toxicity_metrics_status": toxicity_status,
        "toxicity_metrics_emitted": toxicity_emitted,
        "toxicity_metrics_omitted": toxicity_omitted,
        "characterization_factor_source": source,
    }
    if missing_by_metric:
        coverage["reason"] = "active_contributors_lack_characterization_factors"
        coverage["uncharacterized_active_contributors"] = [
            {
                "kind": kind,
                "name": name,
                "missing_metrics": sorted(fields),
            }
            for (kind, name), fields in sorted(grouped_missing.items())
        ]
    elif calculation_unavailable:
        coverage["reason"] = "lca_calculation_unavailable"
    if calculation_unavailable:
        coverage["calculation_unavailable_metrics"] = calculation_unavailable
    for key, value in (factor_provenance or {}).items():
        if key != "source":
            coverage[key] = value
    return lca, coverage


def _verify_loaded_live_provenance(
    expectations: dict[str, Any],
) -> tuple[dict[str, Any], Optional[str], Optional[str]]:
    """Bind comparability to imported modules and the loaded physics source."""
    expected_versions = dict(expectations.get("runtime_versions") or {})
    expected_model_sha256 = str(
        expectations.get("process_model_sha256") or ""
    ).strip().casefold()
    expected_worker_path = str(
        expectations.get("worker_source_path") or ""
    ).strip()
    expected_worker_sha256 = str(
        expectations.get("worker_source_sha256") or ""
    ).strip().casefold()
    params = _polymer_parameters()
    expected_cited_raw = expectations.get("cited_package_sha256") or {}
    expected_cited_complete = (
        isinstance(expected_cited_raw, dict)
        and set(expected_cited_raw) == set(params.CITED_STRAP_SOURCE_NAMES)
        and all(
            str(expected_cited_raw.get(name) or "").strip()
            for name in params.CITED_STRAP_SOURCE_NAMES
        )
    )
    if set(expected_versions) != set(_LIVE_RUNTIME_MODULES) or not (
        expected_model_sha256 and expected_worker_path and expected_worker_sha256
        and expected_cited_complete
    ):
        return (
            {"status": "unverifiable"},
            "live_provenance_contract_missing",
            "Live worker requires complete governed runtime, process-model, "
            "cited package-source, and worker-source provenance.",
        )

    worker_path = Path(__file__).resolve()
    try:
        worker_sha256 = hashlib.sha256(
            params._read_strap_bytes(worker_path)
        ).hexdigest()
    except params.PackageInspectionError as error:
        return (
            {
                "status": "unverifiable",
                "worker_source_path": str(worker_path),
                "unreadable_source": str(error.path),
            },
            error.diagnostic_reason,
            str(error),
        )
    worker_provenance = {
        "worker_source_path": str(worker_path),
        "worker_source_sha256": worker_sha256,
        "expected_worker_source_path": expected_worker_path,
        "expected_worker_source_sha256": expected_worker_sha256,
    }
    if str(worker_path) != expected_worker_path or worker_sha256 != (
        expected_worker_sha256
    ):
        return (
            {"status": "mismatch", **worker_provenance},
            "worker_source_mismatch",
            "Loaded live worker does not match the parent worker source: "
            f"expected {expected_worker_path} ({expected_worker_sha256}), "
            f"resolved {worker_path} ({worker_sha256}).",
        )

    # ``plastics.strap`` imports this stack as part of loading the governed
    # model. Inspect those exact loaded module objects instead of importing the
    # distributions again inside the provenance check: this keeps the check
    # bound to what will execute without adding a second import envelope before
    # process construction.
    loaded_runtime_modules = {
        name: sys.modules.get(name) for name in _LIVE_RUNTIME_MODULES
    }
    unresolved_modules = [
        name for name, module in loaded_runtime_modules.items()
        if module is None
    ]
    if unresolved_modules:
        return (
            {
                "status": "unverifiable",
                **worker_provenance,
                "expected_runtime_versions": expected_versions,
            },
            "worker_environment_probe_failed",
            "Governed process-model import did not load dependencies: "
            + ", ".join(unresolved_modules) + ".",
        )
    resolved_versions = {
        "python": platform.python_version(),
        **{
            name: str(getattr(module, "__version__", "")).strip()
            for name, module in loaded_runtime_modules.items()
        },
    }
    incomplete = [
        name for name in _LIVE_RUNTIME_MODULES
        if not resolved_versions[name]
    ]
    if incomplete:
        return (
            {
                "status": "unverifiable",
                **worker_provenance,
                "expected_runtime_versions": expected_versions,
                "resolved_runtime_versions": resolved_versions,
            },
            "worker_environment_probe_failed",
            "Imported live dependencies do not expose versions for: "
            + ", ".join(incomplete) + ".",
        )
    runtime_mismatches = {
        name: {
            "expected": str(expected_versions[name]),
            "resolved": resolved_versions[name],
        }
        for name in _LIVE_RUNTIME_MODULES
        if resolved_versions[name] != str(expected_versions[name])
    }
    provenance: dict[str, Any] = {
        "status": "mismatch" if runtime_mismatches else "verified",
        **worker_provenance,
        "expected_process_model_sha256": expected_model_sha256,
        "expected_runtime_versions": expected_versions,
        "resolved_runtime_versions": resolved_versions,
    }
    if runtime_mismatches:
        return (
            provenance,
            "runtime_version_mismatch",
            "Imported live dependency versions do not match the admitted TEA cache: "
            + "; ".join(
                f"{name} expected {values['expected']}, resolved "
                f"{values['resolved']}"
                for name, values in runtime_mismatches.items()
            ) + ".",
        )

    model_module = sys.modules.get("plastics.strap.process_model")
    if model_module is None:
        provenance["status"] = "unverifiable"
        return (
            provenance,
            "process_model_missing",
            "Governed plastics package did not load plastics.strap.process_model.",
        )
    model_path = Path(str(getattr(model_module, "__file__", ""))).resolve()
    provenance["process_model_path"] = str(model_path)
    if not model_path.is_file():
        provenance["status"] = "unverifiable"
        return (
            provenance,
            "process_model_missing",
            f"Imported process model has no readable source file: {model_path}",
        )
    try:
        actual_model_sha256 = hashlib.sha256(
            params._read_strap_bytes(model_path)
        ).hexdigest()
    except params.PackageInspectionError as error:
        provenance["status"] = "unverifiable"
        provenance["unreadable_source"] = str(error.path)
        return provenance, error.diagnostic_reason, str(error)
    provenance["process_model_sha256"] = actual_model_sha256
    if actual_model_sha256 != expected_model_sha256:
        provenance["status"] = "mismatch"
        return (
            provenance,
            "process_model_checksum_mismatch",
            "Imported process model does not match the admitted TEA cache: "
            f"expected {expected_model_sha256}, resolved {actual_model_sha256} "
            f"at {model_path}.",
        )
    try:
        loaded_cited = params.loaded_cited_strap_source_provenance()
    except params.PackageInspectionError as error:
        provenance["status"] = "unverifiable"
        provenance["unreadable_source"] = str(error.path)
        return provenance, error.diagnostic_reason, str(error)
    provenance["cited_package_sources"] = loaded_cited["sources"]
    expected_cited = {
        name: str(expected_cited_raw.get(name) or "").strip().casefold()
        for name in params.CITED_STRAP_SOURCE_NAMES
    }
    cited_mismatches = params.cited_package_hash_mismatches(
        expected_cited, loaded_cited,
    )
    if cited_mismatches:
        provenance["status"] = "mismatch"
        return (
            provenance,
            "cited_package_checksum_mismatch",
            "Imported package sources do not match the parent seal: "
            + ", ".join(cited_mismatches)
            + " (property_package.py, dissolution_steps.py, and "
            "precipitation_steps.py must match the expert-table claim).",
        )
    return provenance, None, None


def _prepare_engine_solvent(
    strap_package: Any,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Return a process-safe ID for the identity selected by the parent.

    The external process registers ``<solvent>_loss`` as a ThermoSTEAM alias,
    whose first character must be alphabetic. On an admitted-identity retry,
    a letter-leading BioSTEAM label is used unchanged and a digit-leading one
    gets a letter-leading internal ID backed by the same CAS. No solvent-name
    synonym table participates.
    """
    engine_name = str(
        config.get("_engine_solvent") or config.get("solvent") or ""
    ).strip()
    if not engine_name:
        raise ValueError("Live TEA requires a resolved BioSTEAM solvent identity")
    identity = {
        "strategy": (
            "admitted_biosteam_identity"
            if config.get("_solvent_identity_retry") is True
            else "requested_canonical_identity"
        ),
        "engine_solvent": engine_name,
    }
    if engine_name[0].isalpha():
        return engine_name, identity
    if config.get("_allow_cas_safe_alias") is not True:
        return engine_name, identity

    cas_number = str(config.get("_engine_solvent_cas") or "").strip()
    if not _CAS_NUMBER_RE.fullmatch(cas_number):
        raise ValueError(
            "A digit-leading BioSTEAM solvent identity requires a valid CAS "
            "number for a process-safe internal alias"
        )
    thermosteam = sys.modules.get("thermosteam")
    chemical_draft = getattr(thermosteam, "ChemicalDraft", None)
    if chemical_draft is None:
        raise RuntimeError(
            "Loaded ThermoSTEAM does not expose ChemicalDraft for a "
            "CAS-backed process alias"
        )
    safe_id = "DissolveSolvent_" + cas_number.replace("-", "_")
    strap_package.STRAP_chemicals_outline.append(
        chemical_draft(safe_id, search_ID=cas_number)
    )
    return safe_id, {
        **identity,
        "strategy": "cas_backed_letter_safe_alias",
        "cas_number": cas_number,
        "model_solvent_identifier": safe_id,
    }


def _exception_chain(error: Exception) -> list[Exception]:
    chain: list[Exception] = []
    seen: set[int] = set()
    current: Optional[BaseException] = error
    while isinstance(current, Exception) and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _clean_diagnostic(value: Any) -> str:
    return " ".join(
        _ANSI_ESCAPE_RE.sub("", str(value or "")).strip().split()
    )


def _solvent_model_gap(
    error: Exception,
    *,
    phase: str,
    engine_identity: dict[str, Any],
) -> dict[str, Any]:
    """Classify external model requirements from the actual exception chain."""
    chain = _exception_chain(error)
    diagnostics = [_clean_diagnostic(item) for item in chain]
    combined = " | ".join(item for item in diagnostics if item)
    folded = combined.casefold()
    missing_properties: list[str] = []
    property_patterns = (
        ("Psat", (r"\bpsat\b", r"vapo(?:u)?r pressure")),
        ("Tb", (r"\btb\b", r"boiling point")),
        ("Hvap", (r"\bhvap\b", r"enthalpy of vapori[sz]ation")),
    )
    for label, patterns in property_patterns:
        if any(re.search(pattern, folded) for pattern in patterns):
            missing_properties.append(label)

    engine_name = str(engine_identity.get("engine_solvent") or "")
    model_identifier = str(
        engine_identity.get("model_solvent_identifier") or engine_name
    )
    missing_process_chemicals: list[str] = []
    registry_identity_missing = False
    for item in chain:
        candidate = ""
        if isinstance(item, KeyError) and item.args:
            candidate = _clean_diagnostic(item.args[0]).strip("'\"")
        elif type(item).__name__ == "UndefinedChemicalAlias":
            match = re.search(r"['\"]([^'\"]+)['\"]", str(item))
            candidate = _clean_diagnostic(match.group(1) if match else "")
        if not candidate:
            continue
        if candidate.casefold() in {
            engine_name.casefold(), model_identifier.casefold(),
        }:
            registry_identity_missing = True
        elif candidate not in missing_process_chemicals:
            missing_process_chemicals.append(candidate)

    invalid_engine_alias = "alias must start with a letter" in folded
    engine_identity_not_recognized = bool(re.search(
        r"chemical\s+['\"].+?['\"]\s+not recognized",
        combined,
        flags=re.IGNORECASE,
    ))
    if invalid_engine_alias:
        kind = "invalid_engine_alias"
    elif engine_identity_not_recognized:
        kind = "engine_identity_not_recognized"
    elif missing_properties:
        kind = "missing_thermophysical_property"
    elif registry_identity_missing:
        kind = "process_model_solvent_registry_gap"
    elif missing_process_chemicals:
        kind = "missing_process_chemical"
    else:
        kind = "process_model_failure"
    gap: dict[str, Any] = {
        "kind": kind,
        "phase": phase,
        "exception_type": type(error).__name__,
        "engine_diagnostic": combined[:1_000],
    }
    if missing_properties:
        gap["missing_properties"] = missing_properties
    if missing_process_chemicals:
        gap["missing_process_chemicals"] = missing_process_chemicals
    if registry_identity_missing:
        gap["missing_registry_identity"] = model_identifier
    if invalid_engine_alias:
        gap["invalid_engine_alias"] = model_identifier
    if engine_identity_not_recognized:
        gap["unrecognized_engine_identity"] = model_identifier
    return gap


def _unmodellable_solvent_result(
    config: dict[str, Any],
    failure: _LiveSolventModelError,
    engine_identity: dict[str, Any],
) -> dict[str, Any]:
    requested = str(
        config.get("_requested_solvent") or config.get("solvent") or ""
    )
    canonical = str(config.get("solvent") or requested)
    gap = _solvent_model_gap(
        failure.original,
        phase=failure.phase,
        engine_identity=engine_identity,
    )
    if gap.get("missing_properties"):
        reason = "missing thermophysical properties " + ", ".join(
            gap["missing_properties"]
        )
    elif gap.get("missing_process_chemicals"):
        reason = "missing required process chemicals " + ", ".join(
            gap["missing_process_chemicals"]
        )
    elif gap.get("missing_registry_identity"):
        reason = (
            "the process-model chemical registry could not bind the admitted "
            "solvent identity"
        )
    else:
        reason = f"the process model failed during {failure.phase}"
    return {
        "success": False,
        "error": (
            f"Live TEA cannot model requested solvent '{requested}' "
            f"(recognized and priced as {canonical}): {reason}. "
            "Choose another solvent; no TEA/LCA number was emitted."
        ),
        "error_type": "priced_solvent_unmodellable",
        "requested_solvent": requested,
        "solvent": canonical,
        "canonical_solvent": canonical,
        "engine_solvent": engine_identity.get("engine_solvent"),
        "solvent_support_status": "priced_but_unmodellable",
        "solvent_model_gap": gap,
    }


def _create_and_simulate_process(
    strap_package: Any,
    process_class: Any,
    config: dict[str, Any],
    *,
    target: str,
    energy: dict[str, bool],
) -> tuple[Any, dict[str, Any]]:
    """Construct and simulate once, preserving the failing model phase."""
    phase = "solvent_identity_preparation"
    engine_identity: dict[str, Any] = {
        "engine_solvent": str(
            config.get("_engine_solvent") or config.get("solvent") or ""
        )
    }
    try:
        model_solvent, engine_identity = _prepare_engine_solvent(
            strap_package, config,
        )
        phase = "process_construction"
        precipitation_format = str(
            config.get("precipitation_temperature_format") or "constant"
        )
        if precipitation_format != "constant":
            raise ValueError(
                "precipitation_temperature_format must be 'constant'; "
                "'drop' registers a different setter than this worker calls."
            )
        scenario = process_class.Scenario(
            solvent=model_solvent, target_plastic=target,
            target_plastic_percent=config["target_plastic_percent"],
            processing_capacity=config["processing_capacity"],
            sell_leftover_plastic=bool(
                config.get("sell_leftover_plastic", False)
            ),
            burn_leftover_plastic=bool(
                config.get("burn_leftover_plastic", False)
            ),
            facilities=energy["facilities"],
            turbogenerator=energy["turbogenerator"],
            precipitation_temperature_format=precipitation_format,
            precipitation_configuration=str(
                config.get("precipitation_configuration")
                or "integrated heat transfer"
            ),
        )
        process = process_class(scenario=scenario)
        try:
            process.T1.disconnect(join_ends=True)
            process.U1.disconnect(join_ends=True)
            process.system.update_configuration(
                units=[
                    unit for unit in process.system.units
                    if unit not in (process.T1, process.U1)
                ]
            )
        except Exception:
            pass
        phase = "process_configuration"
        process.tea.labor_cost = config.get("labor_cost", 120_000)
        process.tea.income_tax = float(
            config.get("income_tax", _INCOME_TAX_DEFAULT)
        )
        process.tea.operating_days = float(
            config.get("operating_days", _OPERATING_DAYS_DEFAULT)
        )
        process.set_solvent_price(config["solvent_price"])
        process.set_feedstock_distance(config.get("feedstock_distance_km", 0))
        process.set_solvent_loss(config.get("solvent_loss_pct", 0.01) / 100.0)
        process.set_dissolution_temperature(
            config["dissolution_temperature_c"] + 273.15
        )
        process.set_precipitation_temperature(
            config["precipitation_temperature_c"] + 273.15
        )
        process.set_dissolution_capacity(config.get("dissolution_capacity", 3))
        process.set_IRR(float(config.get("irr", _IRR_DEFAULT)))
        process.set_feedstock_price(float(
            config.get("feedstock_price_usd_per_kg", _FEEDSTOCK_PRICE_USD_PER_KG)
        ))
        process.set_centrifuged_plastic_solvent_content(float(
            config.get(
                "centrifuged_plastic_solvent_content_pct",
                _CENTRIFUGED_PLASTIC_SOLVENT_CONTENT_PCT,
            )
        ))
        if energy["facilities"]:
            process.set_natural_gas_price(float(
                config.get(
                    "natural_gas_price_usd_per_m3",
                    _NATURAL_GAS_PRICE_USD_PER_M3,
                )
            ))
        phase = "characterization_factor_application"
        _set_characterization_factors(
            process,
            dict(config.get("lca_cfs") or {}),
            bool(energy["facilities"]),
            dict(config.get("_lca_factor_bases") or {}),
            dict(config.get("_lca_solvent_transport_offsets") or {}),
        )
        phase = "process_simulation"
        process.system.simulate()
    except Exception as error:
        raise _LiveSolventModelError(
            phase, error, engine_identity,
        ) from error
    return process, engine_identity


def _outline_chemical_ids(outline: Any) -> frozenset[str]:
    """IDs the loaded ChemicalsOutline will not PET-clone."""
    collected: list[str] = []
    ids_attr = getattr(outline, "IDs", None)
    if ids_attr:
        collected.extend(str(item) for item in ids_attr)
    chemicals = getattr(outline, "chemicals", None) or getattr(outline, "data", None)
    if chemicals:
        for item in chemicals:
            ident = getattr(item, "ID", None)
            if ident:
                collected.append(str(ident))
    if collected:
        return frozenset(collected)
    # Fallback: membership is the API process_model uses (`plastic not in outline`).
    params = _polymer_parameters()
    candidates = {
        row.identity_in_model
        for row in params.POLYMERS.values()
        if row.identity_in_model
    }
    candidates.update(
        row.oligomer.chemical_id
        for row in params.POLYMERS.values()
        if row.oligomer is not None
    )
    return frozenset(name for name in candidates if name in outline)


def _inject_oligomer(strap_package: Any, oligomer: Any) -> None:
    """Append a missing oligomer draft from our surface. Does not edit the package."""
    outline = strap_package.STRAP_chemicals_outline
    if oligomer.chemical_id in outline:
        return
    import biosteam as bst
    draft = bst.ChemicalDraft(
        oligomer.chemical_id,
        search_ID=oligomer.search_id,
    )
    outline.append(draft)


def _unsupported_target_result(
    original_target: str, admission: Any,
) -> dict[str, Any]:
    params = _polymer_parameters()
    return {
        "success": False,
        "error": admission.error,
        "error_type": admission.error_type or "unsupported_live_target",
        "target_plastic": original_target,
        "identity_in_model": admission.identity_in_model,
        "supported_live_targets": list(params.live_grid_targets()),
        "package_chemical_present": admission.package_chemical_present,
        "package_oligomer_present": admission.package_oligomer_present,
        "parameter_surface": "dissolve.tea_polymer_parameters",
    }


def run(config: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    params = _polymer_parameters()
    original_target = str(config["target_plastic"]).upper()
    plastics_root = params.resolve_plastics_path()
    try:
        source_ids = params.package_chemical_ids(plastics_root)
        if plastics_root is not None:
            params.inspect_cited_strap_sources(plastics_root)
    except params.PackageInspectionError as error:
        return {
            "success": False,
            "error": str(error),
            "error_type": error.diagnostic_reason,
            "target_plastic": original_target,
            "parameter_surface": "dissolve.tea_polymer_parameters",
        }
    admission = params.admit_live_target(
        original_target, package_chemical_ids=source_ids,
    )
    if not admission.admitted:
        return _unsupported_target_result(original_target, admission)
    if sys.version_info < (3, 12):
        return {
            "success": False,
            "error": (
                "Live BioSTEAM execution requires Python 3.12 or newer. "
                f"This worker is Python {platform.python_version()}."
            ),
            "error_type": "python_version",
            "target_plastic": original_target,
            "parameter_surface": "dissolve.tea_polymer_parameters",
        }
    if path := str(os.getenv("DISSOLVE_PLASTICS_PATH") or "").strip():
        sys.path.insert(0, os.path.abspath(os.path.expanduser(path)))
    # Preserve the cache generator's import order. Importing process_model (or
    # its BioSTEAM dependencies) before the package changes the last bit of
    # several allocated-impact results even on the same admitted stack.
    strap_package = importlib.import_module("plastics.strap")
    provenance, provenance_error_type, provenance_error = (
        _verify_loaded_live_provenance(
            dict(config.get("_live_provenance_expectations") or {}),
        )
    )
    if provenance_error:
        return {
            "success": False,
            "error": provenance_error,
            "error_type": provenance_error_type,
            "target_plastic": original_target,
            "live_provenance": provenance,
        }
    loaded_ids = _outline_chemical_ids(strap_package.STRAP_chemicals_outline)
    admission = params.admit_live_target(
        original_target, package_chemical_ids=loaded_ids,
    )
    if not admission.admitted:
        return _unsupported_target_result(original_target, admission)
    if (
        admission.oligomer_injection_required
        and admission.row is not None
        and admission.row.oligomer is not None
    ):
        try:
            _inject_oligomer(strap_package, admission.row.oligomer)
        except Exception as error:
            return {
                "success": False,
                "error": (
                    f"Could not inject oligomer "
                    f"{admission.row.oligomer.chemical_id} from the parameter "
                    f"surface: {error}"
                ),
                "error_type": "oligomer_injection_failed",
                "target_plastic": original_target,
                "live_provenance": provenance,
            }
    process_class = _patch_process(getattr(strap_package, "BaselineSTRAPProcess"))
    try:
        strap_package.STRAP_chemicals_outline.append("HCl")
    except Exception:
        pass

    target = admission.identity_in_model
    if not target:
        return _unsupported_target_result(original_target, admission)
    energy_case = str(config.get("energy_case") or "C1").upper()
    energy = _ENERGY.get(energy_case)
    if energy is None:
        raise ValueError("energy_case must be C1, C2, or C3")
    try:
        process, engine_identity = _create_and_simulate_process(
            strap_package,
            process_class,
            config,
            target=target,
            energy=energy,
        )
    except _LiveSolventModelError as failure:
        return {
            **_unmodellable_solvent_result(
                config,
                failure,
                failure.engine_identity,
            ),
            "target_plastic": original_target,
            "energy_case": energy_case,
            "live_provenance": provenance,
        }
    lca_cfs = dict(config.get("lca_cfs") or {})
    # Preserve the cache-comparable order on the admitted live stack.
    # BioSTEAM's lazy properties make the last bits order-sensitive: impact
    # metrics and process details are evaluated before economics. Coverage
    # walks the graph only after economics so it cannot perturb those values.
    evaluated_lca = _evaluate_lca_metrics(process)

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
    msp = _safe(process.MSP)
    tci = _safe(lambda: process.tea.TCI)
    aoc = _safe(lambda: process.tea.AOC)
    lca, lca_coverage = _lca_payload(
        process,
        lca_cfs,
        energy_case,
        dict(config.get("_lca_factor_provenance") or {}),
        evaluated_lca,
    )
    result = {
        "success": True, "solvent": config["solvent"],
        "engine_solvent": engine_identity.get("engine_solvent"),
        "solvent_identity_resolution": engine_identity,
        "target_plastic": original_target, "simulated_as": target,
        "energy_case": energy_case,
        "tea": {
            "msp_usd_per_kg": msp,
            "tci_usd": tci,
            "aoc_usd_per_yr": aoc,
        },
        "lca": lca,
        "lca_coverage": lca_coverage,
        "live_provenance": provenance,
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
    if admission.row is not None:
        result = params.attach_live_parameter_standing(
            result,
            admission.row,
            solvent=engine_identity.get("engine_solvent") or config.get("solvent"),
            config=config,
            plastics_root=plastics_root,
        )
    elif result.get("success") is True:
        return {
            "success": False,
            "error": (
                "Live TEA produced a number without parameter standing; "
                "refusing to serve an unlabelled result."
            ),
            "error_type": "live_parameter_standing_missing",
            "target_plastic": original_target,
        }
    return result


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
            "requested_solvent": (
                config.get("_requested_solvent") or config.get("solvent")
            ),
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
