"""TEA ranking over stored evidence: registered campaigns, rank_landscape over their process rows, and route-constrained optimization."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence

from . import tea
from . import thermodynamics as thermo
from .contracts import tool_error, tool_success
from .session import candidate_evidence, current_tool_session
from .tea import route_evidence_signature

# --- landscape: rank_landscape source=process_rows: usable projection, landscape AND frontier.
#
# safety_standing is a carried axis: copy a legal status from the source row.
# Do not filter usable on it. Default not_requested when nothing was bound.


# --- campaign_basis: campaign_basis.v1 — project a v2 run definition onto public held roles.
#
# A v2 campaign has no field_role object. Absence of leftover/precipitation
# switches from JSONL is not incompleteness: those plants ran at the worker
# production hardcodes. Ranking burn=True against that slice is a held-field
# mismatch, not a leftover-to-CHP landscape.

CAMPAIGN_BASIS_PROJECTION = "campaign_basis.v1"
CAMPAIGN_DEFINITION_SCHEMA_V2 = "dissolve.process_campaign_definition.v2"
_DERIVED_PUBLIC = frozenset({
    "dissolution_temperature_c", "solvent_price_usd_per_kg",
})
_IDENTITY_PUBLIC = frozenset({"target_polymer", "solvent"})
_DERIVED_RULES = {
    "dissolution_temperature_c": (
        "setpoint_rule: lowest stored 25-160 C grid node with "
        "solubility >= 5 wt%"
    ),
    "solvent_price_usd_per_kg": (
        "solvent census price_usd_per_kg for that pair's solvent identity"
    ),
}


class CampaignBasisIncomplete(Exception):
    """The run definition cannot support campaign_basis.v1."""

    error_code = "campaign_basis_incomplete"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


def _twelve_public_names() -> tuple[str, ...]:
    return tuple(public for _internal, public in tea._DESIGN_POINT_PUBLIC_FIELDS)


def _held_switch_or_coefficient(
    fixed_fields: Mapping[str, Any], field: str, production_value: Any,
) -> dict[str, Any]:
    """Declared fixed_fields win. JSONL-absent switches stay worker production."""
    if field in fixed_fields:
        return {
            "role": "held",
            "value": fixed_fields[field],
            "projection": "fixed_fields",
        }
    return {
        "role": "held",
        "value": production_value,
        "projection": "worker_production",
    }


def _held_values_equal(field: str, campaign: Any, requested: Any) -> bool:
    if campaign is requested:
        return True
    if field == "energy_case":
        campaign_s = str(campaign or "").upper()
        if isinstance(requested, (list, tuple)):
            return bool(requested) and all(
                str(item or "").upper() == campaign_s for item in requested
            )
        return campaign_s == str(requested or "").upper()
    if field == "lang_factor":
        return campaign in (None, "") and requested in (None, "")
    if isinstance(campaign, (list, tuple)) or isinstance(requested, (list, tuple)):
        try:
            left = tuple(campaign)
            right = tuple(requested)
        except TypeError:
            return False
        if len(left) != len(right):
            return False
        try:
            if all(
                isinstance(item, int) and not isinstance(item, bool)
                for item in (*left, *right)
            ):
                return left == right
            return all(
                round(float(a), 10) == round(float(b), 10)
                for a, b in zip(left, right)
            )
        except (TypeError, ValueError):
            return False
    if isinstance(campaign, bool) or isinstance(requested, bool):
        return bool(campaign) is bool(requested)
    if isinstance(campaign, (int, float)) and not isinstance(campaign, bool):
        try:
            return round(float(campaign), 10) == round(float(requested), 10)
        except (TypeError, ValueError):
            return False
    return campaign == requested


def _delta(field: str, campaign: Any, requested: Any) -> dict[str, Any]:
    numeric = isinstance(campaign, (int, float)) and not isinstance(campaign, bool)
    try:
        requested_number = float(requested)
        numeric = numeric or not isinstance(requested, bool)
    except (TypeError, ValueError):
        requested_number = None
        numeric = False
    payload: dict[str, Any] = {
        "field": field,
        "campaign_value": campaign,
        "requested_value": requested,
    }
    if numeric and requested_number is not None:
        try:
            payload["delta"] = requested_number - float(campaign)
        except (TypeError, ValueError):
            payload["delta"] = None
    else:
        payload["delta"] = {
            "campaign": campaign,
            "requested": requested,
        }
    return payload


def project_campaign_basis_v1(run_definition: Mapping[str, Any]) -> dict[str, Any]:
    """Project dissolve.process_campaign_definition.v2 onto campaign_basis.v1.

    D-8 overlay names stay the twelve. Public extras that change the plant
    (flowsheet switches and B coefficients) join as held at the production
    values the v12 worker actually ran, even when JSONL omitted them.
    """
    if not isinstance(run_definition, Mapping):
        raise CampaignBasisIncomplete(
            "run definition must be an object",
            missing=["schema", "fixed_fields", "setpoint_rule", "pair_definitions"],
        )
    missing: list[str] = []
    schema = run_definition.get("schema")
    if schema != CAMPAIGN_DEFINITION_SCHEMA_V2:
        missing.append("schema")
    fixed_fields = run_definition.get("fixed_fields")
    if not isinstance(fixed_fields, Mapping):
        missing.append("fixed_fields")
    setpoint_rule = run_definition.get("setpoint_rule")
    if not isinstance(setpoint_rule, Mapping):
        missing.append("setpoint_rule")
    pair_definitions = run_definition.get("pair_definitions")
    if not isinstance(pair_definitions, list) or not pair_definitions:
        missing.append("pair_definitions")
    if missing:
        raise CampaignBasisIncomplete(
            "run definition cannot support campaign_basis.v1",
            missing=missing,
            schema=schema,
        )

    field_role: dict[str, Any] = {}
    unassigned: list[str] = []
    for public in _twelve_public_names():
        worker = next(
            internal for internal, name in tea._DESIGN_POINT_PUBLIC_FIELDS
            if name == public
        )
        if worker in fixed_fields:
            field_role[public] = {
                "role": "held",
                "value": fixed_fields[worker],
                "projection": "fixed_fields",
            }
        elif public in _DERIVED_PUBLIC:
            rule = setpoint_rule.get(public) or _DERIVED_RULES[public]
            field_role[public] = {
                "role": "derived",
                "rule": rule,
                "values_vary": True,
            }
        elif public in _IDENTITY_PUBLIC:
            field_role[public] = {"role": "varied", "axis": "identity"}
        else:
            unassigned.append(public)
    if unassigned:
        raise CampaignBasisIncomplete(
            "twelve public fields could not each be assigned a closed role",
            missing=unassigned,
        )

    for field, value in tea._FLOWSHEET_SWITCH_DEFAULTS.items():
        field_role[field] = _held_switch_or_coefficient(fixed_fields, field, value)
    energy = str(
        (field_role.get("energy_case") or {}).get("value") or "C1"
    ).upper()
    for field, value in tea._COEFFICIENT_DEFAULTS.items():
        field_role[field] = _held_switch_or_coefficient(fixed_fields, field, value)
    if energy in {"C1", "C3"}:
        field_role["natural_gas_price_usd_per_m3"] = _held_switch_or_coefficient(
            fixed_fields,
            "natural_gas_price_usd_per_m3",
            tea._NATURAL_GAS_PRICE_USD_PER_M3,
        )
        field_role["steam_power_depreciation"] = _held_switch_or_coefficient(
            fixed_fields,
            "steam_power_depreciation",
            tea._STEAM_POWER_DEPRECIATION_DEFAULT,
        )

    return {
        "campaign_basis_projection": CAMPAIGN_BASIS_PROJECTION,
        "campaign_definition_schema": CAMPAIGN_DEFINITION_SCHEMA_V2,
        "campaign_basis": {
            "field_role": field_role,
            "n_pairs": len(pair_definitions),
            "complete": True,
        },
    }


def held_field_mismatches(
    basis: Mapping[str, Any],
    requested: Mapping[str, Any] | None,
    *,
    seconds_per_pair: float | None = None,
) -> dict[str, Any]:
    """Compare requested public fields against projected HELD roles only.

    Absent keys are unconstrained. Varied and derived fields are not a
    held mismatch. 55 / 20000 / C1 is not hardcoded here.
    """
    campaign_basis = dict(basis.get("campaign_basis") or basis)
    field_role = dict(campaign_basis.get("field_role") or {})
    supplied = dict(requested or {})
    mismatches: list[dict[str, Any]] = []
    for field, spec in field_role.items():
        if spec.get("role") != "held":
            continue
        if field not in supplied or supplied[field] is None:
            continue
        campaign_value = spec.get("value")
        if _held_values_equal(field, campaign_value, supplied[field]):
            continue
        mismatches.append(_delta(field, campaign_value, supplied[field]))
    payload: dict[str, Any] = {
        "mismatches": mismatches,
        "n_held_mismatches": len(mismatches),
    }
    if mismatches:
        payload["error_code"] = "campaign_basis_mismatch"
        n_pairs = campaign_basis.get("n_pairs")
        if seconds_per_pair is not None and n_pairs is not None:
            payload["live_rerun_quote"] = {
                "n_pairs": n_pairs,
                "seconds_per_pair": seconds_per_pair,
                "estimated_wall_seconds": float(n_pairs) * float(seconds_per_pair),
            }
    return payload


# --- campaign_consume: Locate and integrity-bind a registered campaign. Do not ingest JSONL into cache.

REGISTRY_ENV = "DISSOLVE_CAMPAIGN_REGISTRY"
#: The sealed polymer-solvent TEA/LCA campaign (462 rows) ships with the package and is the registry
#: when DISSOLVE_CAMPAIGN_REGISTRY is unset. Its manifest pins the two consumed artifacts by sha256.
SHIPPED_CAMPAIGN = Path(str(files("dissolve").joinpath("data/campaign")))
_SHIPPED_REGISTRY = {
    "ef62efb3a708d782ce58cac3295cc9de71b0a7e8d076f6ca79f1ba5830a29636": {
        "manifest_path": str(SHIPPED_CAMPAIGN / "manifest.json"),
        "manifest_sha256": "763c3b52306bda21653749b2378d4b17da9ba394b6683d1055a838e1fa5ec347",
        "append_log_aliases": ["8b11ee68ce9948418872d892076bf275cb8b0dc1f95e72b0af80bbd2ac1eee3a"],
    },
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FILES = (
    ("run_definition.json", "run_definition_json"),
    ("process_rows.jsonl", "process_rows_jsonl"),
)


class CampaignConsumeError(Exception):
    def __init__(self, message: str, *, error_code: str, **details: Any) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details


def canonical_json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise CampaignConsumeError(
                "campaign registry has a duplicate key",
                error_code="ambiguous_campaign_registry",
                duplicate_key=key,
            )
        seen[str(key)] = value
    return seen


def load_campaign_registry(path: str | None = None) -> dict[str, Any]:
    raw = str(
        path if path is not None else os.getenv(REGISTRY_ENV) or ""
    ).strip()
    registry_path = Path(raw).expanduser()
    try:
        parsed = (
            json.loads(registry_path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_object)
            if raw else _SHIPPED_REGISTRY
        )
    except CampaignConsumeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignConsumeError(
            "campaign registry is unreadable",
            error_code="campaign_registry_unreadable",
            path=str(registry_path),
            reason=type(error).__name__,
        ) from error
    if not isinstance(parsed, dict):
        raise CampaignConsumeError(
            "campaign registry must be a JSON object",
            error_code="ambiguous_campaign_registry",
        )
    aliases: dict[str, str] = {}
    entries: dict[str, Any] = {}
    for canonical, entry in parsed.items():
        token = str(canonical).strip().casefold()
        if not _HEX64.match(token) or not isinstance(entry, Mapping):
            raise CampaignConsumeError(
                "campaign registry entry is not a canonical digest object",
                error_code="ambiguous_campaign_registry",
                canonical=canonical,
            )
        if token in entries:
            raise CampaignConsumeError(
                "campaign registry maps one canonical digest twice",
                error_code="ambiguous_campaign_registry",
                canonical=token,
            )
        entries[token] = dict(entry)
        for alias in entry.get("append_log_aliases") or []:
            alias_token = str(alias).strip().casefold()
            if not _HEX64.match(alias_token):
                continue
            owner = aliases.get(alias_token)
            if owner is not None and owner != token:
                raise CampaignConsumeError(
                    "one append-log alias is claimed by two canonicals",
                    error_code="ambiguous_campaign_registry",
                    alias=alias_token,
                    canonicals=[owner, token],
                )
            aliases[alias_token] = token
    return {
        "entries": entries,
        "n_registered": len(entries),
        "aliases": aliases,
    }


def classify_campaign_fingerprint(
    fingerprint: str | None, registry: Mapping[str, Any],
) -> dict[str, Any]:
    supplied = str(fingerprint or "").strip().casefold()
    if not supplied:
        raise CampaignConsumeError(
            "source=campaign requires campaign_fingerprint",
            error_code="missing_campaign_fingerprint",
        )
    n_registered = int(registry.get("n_registered") or 0)
    if not _HEX64.match(supplied):
        raise CampaignConsumeError(
            "campaign_fingerprint must be a 64-hex digest",
            error_code="campaign_not_registered",
            supplied=supplied,
            n_registered=n_registered,
        )
    entries = dict(registry.get("entries") or {})
    aliases = dict(registry.get("aliases") or {})
    if supplied in aliases:
        canonical = aliases[supplied]
        raise CampaignConsumeError(
            "campaign_fingerprint is append-log provenance, not consume identity",
            error_code="campaign_fingerprint_mismatch",
            supplied=supplied,
            canonical=canonical,
            note="append-log",
        )
    if supplied not in entries:
        raise CampaignConsumeError(
            "campaign_fingerprint is not registered",
            error_code="campaign_not_registered",
            supplied=supplied,
            n_registered=n_registered,
        )
    return {"canonical": supplied, "entry": dict(entries[supplied])}


def _seconds_per_pair(manifest: Mapping[str, Any]) -> float | None:
    wall = manifest.get("aggregate_pair_wall_seconds")
    attempted = (manifest.get("counts") or {}).get("attempted")
    try:
        if wall is None or not attempted:
            return None
        return float(wall) / float(attempted)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def bind_registered_campaign(
    canonical: str,
    entry: Mapping[str, Any],
    *,
    allow_partial: bool = False,
) -> dict[str, Any]:
    manifest_path = Path(str(entry.get("manifest_path") or "")).expanduser()
    expected_manifest = str(entry.get("manifest_sha256") or "").strip().casefold()
    if not manifest_path.is_file():
        raise CampaignConsumeError(
            "registered campaign manifest is missing or unreadable",
            error_code="campaign_manifest_unreadable",
            manifest_path=str(manifest_path),
        )
    try:
        observed_manifest = file_sha256(manifest_path)
    except OSError as error:
        raise CampaignConsumeError(
            "registered campaign manifest is missing or unreadable",
            error_code="campaign_manifest_unreadable",
            manifest_path=str(manifest_path),
        ) from error
    if not expected_manifest or observed_manifest != expected_manifest:
        raise CampaignConsumeError(
            "on-disk manifest.json does not match the registry digest",
            error_code="campaign_manifest_integrity_mismatch",
            expected=expected_manifest or None,
            observed=observed_manifest,
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CampaignConsumeError(
            "registered campaign manifest is missing or unreadable",
            error_code="campaign_manifest_unreadable",
            manifest_path=str(manifest_path),
        ) from error
    if not isinstance(manifest, dict):
        raise CampaignConsumeError(
            "registered campaign manifest is missing or unreadable",
            error_code="campaign_manifest_unreadable",
            manifest_path=str(manifest_path),
        )
    root = manifest_path.parent
    artifacts = dict(manifest.get("artifact_sha256") or {})
    bound: dict[str, Path] = {}
    for filename, artifact_key in _ARTIFACT_FILES:
        path = root / filename
        if not path.is_file():
            raise CampaignConsumeError(
                "campaign artifact is missing",
                error_code="campaign_artifact_integrity_mismatch",
                artifact=filename,
                expected=artifacts.get(artifact_key),
                observed=None,
            )
        observed = file_sha256(path)
        expected = str(artifacts.get(artifact_key) or "").strip().casefold()
        if not expected or observed != expected:
            raise CampaignConsumeError(
                "on-disk campaign artifact does not match the manifest digest",
                error_code="campaign_artifact_integrity_mismatch",
                artifact=filename,
                expected=expected or None,
                observed=observed,
            )
        bound[filename] = path
    run_definition = json.loads(
        bound["run_definition.json"].read_text(encoding="utf-8"),
    )
    computed = canonical_json_digest(run_definition)
    manifest_fingerprint = str(
        manifest.get("campaign_fingerprint") or ""
    ).strip().casefold()
    if computed != canonical or (
        manifest_fingerprint and computed != manifest_fingerprint
    ):
        raise CampaignConsumeError(
            "recomputed run-definition digest disagrees with the consume identity",
            error_code="campaign_fingerprint_mismatch",
            supplied=canonical,
            computed=computed,
            manifest=manifest_fingerprint or None,
        )
    if manifest.get("complete") is not True and not allow_partial:
        counts = dict(manifest.get("counts") or {})
        raise CampaignConsumeError(
            "campaign is not complete",
            error_code="campaign_incomplete",
            recorded_pairs=counts.get("attempted"),
            expected_pairs=(
                (manifest.get("census") or {}).get("pair_count")
            ),
        )
    try:
        projected = project_campaign_basis_v1(run_definition)
    except CampaignBasisIncomplete as error:
        raise CampaignConsumeError(
            str(error),
            error_code=error.error_code,
            **error.details,
        ) from error
    return {
        "canonical": canonical,
        "append_log_fingerprint": manifest.get("append_log_fingerprint"),
        "manifest": manifest,
        "run_definition": run_definition,
        "process_rows_path": bound["process_rows.jsonl"],
        "projected": projected,
        "seconds_per_pair": _seconds_per_pair(manifest),
    }


def publicize_requested_fields(requested: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map worker and alias names onto public campaign_basis fields."""
    out: dict[str, Any] = {}
    internal_to_public = dict(tea._DESIGN_POINT_PUBLIC_FIELDS)
    aliases = dict(tea._SCENARIO_ALIASES)
    for key, value in dict(requested or {}).items():
        if value is None:
            continue
        if key in internal_to_public:
            out[internal_to_public[key]] = value
        elif key in aliases:
            internal = aliases[key]
            out[internal_to_public.get(internal, key)] = value
        else:
            out[key] = value
    cases = out.pop("energy_cases", None)
    if cases is not None:
        if isinstance(cases, (list, tuple)):
            cleaned = [
                str(item).strip().upper()
                for item in cases
                if str(item or "").strip()
            ]
            if len(cleaned) == 1:
                out["energy_case"] = cleaned[0]
            elif cleaned:
                out["energy_case"] = cleaned
        else:
            out["energy_case"] = cases
    return out


def _derived_temperature_mismatch(
    run_definition: Mapping[str, Any], requested_t: Any,
) -> dict[str, Any] | None:
    try:
        want = round(float(requested_t), 10)
    except (TypeError, ValueError):
        return {
            "field": "dissolution_temperature_c",
            "campaign_value": "setpoint_rule",
            "requested_value": requested_t,
            "delta": {"campaign": "setpoint_rule", "requested": requested_t},
        }
    produced: set[float] = set()
    for pair in run_definition.get("pair_definitions") or []:
        config = dict(pair.get("config_sent") or {})
        if "dissolution_temperature_c" not in config:
            continue
        try:
            produced.add(round(float(config["dissolution_temperature_c"]), 10))
        except (TypeError, ValueError):
            continue
    if want in produced:
        return None
    return {
        "field": "dissolution_temperature_c",
        "campaign_value": "setpoint_rule",
        "requested_value": requested_t,
        "delta": {"campaign": "setpoint_rule", "requested": requested_t},
    }


def _solvent_identities(name: str) -> set[str]:
    labels = thermo.solvent_identity_labels(name)
    if labels:
        return {str(item) for item in labels if item}
    canonical = str(thermo.canonical_solvent_name(name) or "").strip()
    supplied = str(name or "").strip()
    return {item for item in (canonical, supplied) if item}


def _solvents_match(requested: str, public_identity: str) -> bool:
    return bool(
        _solvent_identities(requested) & _solvent_identities(public_identity)
    )


def _row_without_engine_envelope(row: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(row)
    copied.pop("engine_envelope", None)
    return copied


def consume_process_rows(
    path: Path,
    canonical: str,
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
) -> tuple[int, int | None, list[dict[str, Any]]]:
    wanted_polymers = {_key(item) for item in (polymers or []) if item}
    wanted_solvent = str(solvent or "").strip() or None
    filter_active = bool(wanted_polymers or wanted_solvent)
    n_rows = 0
    matched = 0
    dumped: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            n_rows += 1
            row_fp = str(row.get("campaign_fingerprint") or "").strip().casefold()
            if row_fp != canonical:
                raise CampaignConsumeError(
                    "process_rows.jsonl campaign_fingerprint disagrees",
                    error_code="campaign_fingerprint_mismatch",
                    supplied=canonical,
                    computed=row_fp or None,
                    pair_id=row.get("pair_id"),
                )
            polymer = str(row.get("polymer") or "").strip()
            if wanted_polymers and _key(polymer) not in wanted_polymers:
                continue
            public_solvent = str(row.get("solvent_public_identity") or "")
            if wanted_solvent is not None and not _solvents_match(
                wanted_solvent, public_solvent,
            ):
                continue
            dumped.append(_row_without_engine_envelope(row))
            if filter_active:
                matched += 1
    return n_rows, (matched if filter_active else None), dumped


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _held_mismatch_payload(
    projected: Mapping[str, Any],
    public_requested: Mapping[str, Any],
    *,
    run_definition: Mapping[str, Any],
    seconds_per_pair: float | None,
) -> dict[str, Any] | None:
    mismatch = held_field_mismatches(
        projected,
        public_requested,
        seconds_per_pair=seconds_per_pair,
    )
    derived_t = public_requested.get("dissolution_temperature_c")
    if derived_t is not None:
        derived = _derived_temperature_mismatch(run_definition, derived_t)
        if derived is not None:
            mismatch.setdefault("mismatches", []).append(derived)
            mismatch["n_held_mismatches"] = len(mismatch["mismatches"])
            mismatch["error_code"] = "campaign_basis_mismatch"
            if (
                seconds_per_pair is not None
                and "live_rerun_quote" not in mismatch
            ):
                n_pairs = projected.get("campaign_basis", {}).get("n_pairs")
                if n_pairs is not None:
                    mismatch["live_rerun_quote"] = {
                        "n_pairs": n_pairs,
                        "seconds_per_pair": seconds_per_pair,
                        "estimated_wall_seconds": (
                            float(n_pairs) * float(seconds_per_pair)
                        ),
                    }
    if mismatch.get("error_code") != "campaign_basis_mismatch":
        return None
    return mismatch


def prepare_registered_campaign(
    *,
    fingerprint: str | None,
    requested: Mapping[str, Any] | None = None,
    registry_path: str | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Locate, integrity-bind, and refuse held mismatch. Does not stream rows."""
    if not str(fingerprint or "").strip():
        raise CampaignConsumeError(
            "source=campaign requires campaign_fingerprint",
            error_code="missing_campaign_fingerprint",
        )
    registry = load_campaign_registry(registry_path)
    located = classify_campaign_fingerprint(fingerprint, registry)
    bound = bind_registered_campaign(
        located["canonical"],
        located["entry"],
        allow_partial=allow_partial,
    )
    public_requested = publicize_requested_fields(requested)
    mismatch = _held_mismatch_payload(
        bound["projected"],
        public_requested,
        run_definition=bound["run_definition"],
        seconds_per_pair=bound["seconds_per_pair"],
    )
    if mismatch is not None:
        raise CampaignConsumeError(
            "requested held fields disagree with the campaign basis",
            error_code="campaign_basis_mismatch",
            mismatches=mismatch["mismatches"],
            n_held_mismatches=mismatch["n_held_mismatches"],
            **(
                {"live_rerun_quote": mismatch["live_rerun_quote"]}
                if mismatch.get("live_rerun_quote") else {}
            ),
        )
    return bound


def consume_campaign_lookup(
    *,
    fingerprint: str | None,
    requested: Mapping[str, Any] | None = None,
    polymers: list[str] | None = None,
    solvent: str | None = None,
    registry_path: str | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
    bound = prepare_registered_campaign(
        fingerprint=fingerprint,
        requested=requested,
        registry_path=registry_path,
        allow_partial=allow_partial,
    )
    n_rows, matching, dumped = consume_process_rows(
        bound["process_rows_path"],
        bound["canonical"],
        polymers=polymers,
        solvent=solvent,
    )
    payload = {
        "source": "campaign",
        "campaign_fingerprint": bound["canonical"],
        "append_log_fingerprint": bound["append_log_fingerprint"],
        "campaign_definition_schema": CAMPAIGN_DEFINITION_SCHEMA_V2,
        **bound["projected"],
        "n_rows_consumed": n_rows,
        "comparison_rows": dumped,
        "ingested_into_admitted_cache": False,
    }
    if matching is not None:
        payload["matching_row_count"] = matching
    return payload


X_METRIC = "msp_usd_per_kg"
Y_METRIC = "gwp_kg_co2e_per_kg"
X_UNITS = "USD/kg"
Y_UNITS = "kg CO2e/kg"
X_DIRECTION = "min"
Y_DIRECTION = "min"
_SAFETY_STANDING_STATUSES = frozenset({
    "evaluated", "not_requested", "unavailable",
})


def _finite_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def hyndman_fan_type7(sample: Sequence[float], p: float) -> float:
    """Quantile at probability p, Hyndman–Fan type 7 (numpy / R default)."""
    ordered = sorted(float(item) for item in sample)
    n = len(ordered)
    if n == 0:
        raise ValueError("quantile of an empty sample")
    if n == 1:
        return ordered[0]
    h = (n - 1) * float(p)
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])


def axis_span(values: Sequence[float]) -> dict[str, float]:
    ordered = [float(item) for item in values]
    return {
        "min": min(ordered),
        "p05": hyndman_fan_type7(ordered, 0.05),
        "p95": hyndman_fan_type7(ordered, 0.95),
        "max": max(ordered),
    }


def _dominates(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> bool:
    cx, cy = float(candidate[X_METRIC]), float(candidate[Y_METRIC])
    bx, by = float(baseline[X_METRIC]), float(baseline[Y_METRIC])
    return (cx <= bx and cy <= by) and (cx < bx or cy < by)


def pareto_frontier(landscape: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier = [
        point for point in landscape
        if not any(
            other is not point and _dominates(other, point)
            for other in landscape
        )
    ]
    frontier.sort(key=lambda point: float(point[X_METRIC]))
    out: list[dict[str, Any]] = []
    for index, point in enumerate(frontier, 1):
        copied = dict(point)
        copied.update({
            "point_id": index,
            "point_status": "frontier",
            "is_frontier": True,
        })
        out.append(copied)
    return out


def _lca_standing(comparison: Mapping[str, Any]) -> dict[str, Any] | None:
    coverage = comparison.get("lca_coverage")
    if isinstance(coverage, dict) and coverage:
        if any(
            key in coverage
            for key in ("status", "lca_metrics_status", "metric_coverage_status")
        ):
            standing = {
                "status": coverage.get("status"),
                "lca_metrics_status": coverage.get("lca_metrics_status"),
                "metric_coverage_status": coverage.get("metric_coverage_status"),
            }
            metric_status = coverage.get("lca_metric_status")
            if isinstance(metric_status, dict) and metric_status:
                standing["lca_metric_status"] = dict(metric_status)
            return standing
        return dict(coverage)
    metric_status = comparison.get("lca_metric_status")
    if isinstance(metric_status, dict) and metric_status:
        return {"lca_metric_status": dict(metric_status)}
    return None


def _public_twelve_from_process_row(
    row: Mapping[str, Any], comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Executed D-8 twelve on a compact landscape point. Public solvent."""
    from . import tea

    normalized = dict(row.get("config_normalized_twelve") or {})
    public: dict[str, Any] = {}
    for internal, public_name in tea._DESIGN_POINT_PUBLIC_FIELDS:
        if public_name in comparison and comparison.get(public_name) is not None:
            value = comparison[public_name]
        elif internal in normalized:
            value = normalized[internal]
        elif public_name in normalized:
            value = normalized[public_name]
        else:
            value = None
        if public_name == "target_polymer":
            value = (
                row.get("polymer") or comparison.get("polymer") or value
            )
        if public_name == "solvent":
            value = (
                row.get("solvent_public_identity")
                or comparison.get("solvent")
                or value
            )
        if value is not None and value != "":
            public[public_name] = value
    if public.get("target_polymer"):
        public["polymer"] = public["target_polymer"]
    return public


def _carried_safety_standing(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a legal safety_standing object. Not a GSK-fail predicate."""
    nested = row.get("comparison_row")
    sources = (row.get("safety_standing"),)
    if isinstance(nested, dict):
        sources = (row.get("safety_standing"), nested.get("safety_standing"))
    for source in sources:
        if not isinstance(source, dict):
            continue
        status = str(source.get("status") or "").strip()
        if status in _SAFETY_STANDING_STATUSES:
            return dict(source)
    return {"status": "not_requested"}


def compact_process_row(row: Mapping[str, Any]) -> dict[str, Any]:
    comparison = dict(row.get("comparison_row") or {})
    standing = dict(row.get("standing") or {})
    payload = {
        "pair_id": row.get("pair_id"),
        "target_polymer": row.get("polymer"),
        "solvent": row.get("solvent_public_identity"),
        "campaign_fingerprint": row.get("campaign_fingerprint"),
        "outcome": row.get("outcome"),
        "error_type": row.get("error_type"),
        X_METRIC: comparison.get(X_METRIC),
        Y_METRIC: comparison.get(Y_METRIC),
        "standing": standing,
        "process_parameter_status": standing.get("process_parameter_status"),
        "can_cite_as_validated_process": standing.get(
            "can_cite_as_validated_process",
        ),
        "lca_coverage": _lca_standing(comparison),
        "safety_standing": _carried_safety_standing(row),
    }
    payload.update(_public_twelve_from_process_row(row, comparison))
    if "original_thermo_rank" in row:
        payload["original_thermo_rank"] = row.get("original_thermo_rank")
    elif "original_thermo_rank" in comparison:
        payload["original_thermo_rank"] = comparison.get("original_thermo_rank")
    return payload


def row_is_usable(
    point: Mapping[str, Any],
    *,
    canonical: str | None,
    skip_campaign_identity: bool = False,
) -> bool:
    if not skip_campaign_identity:
        fingerprint = str(point.get("campaign_fingerprint") or "").strip().casefold()
        if fingerprint != str(canonical or "").casefold():
            return False
    if str(point.get("outcome") or "") != "success":
        return False
    if not _finite_number(point.get(X_METRIC)):
        return False
    if not _finite_number(point.get(Y_METRIC)):
        return False
    coverage = point.get("lca_coverage")
    if not isinstance(coverage, dict) or not coverage:
        return False
    if skip_campaign_identity:
        from . import tea

        return tea._public_twelve_from_row(dict(point)) is not None
    standing = point.get("standing")
    if not isinstance(standing, dict) or not standing:
        return False
    if not standing.get("process_parameter_status"):
        return False
    return True


def _exclusion_token(point: Mapping[str, Any]) -> str:
    token = str(point.get("error_type") or "").strip()
    if token:
        return token
    if str(point.get("outcome") or "") != "success":
        return "failure"
    return "not_usable"


def frontier_tradeoff(
    frontier: Sequence[Mapping[str, Any]],
    *,
    cheapest_equals_lowest_y: bool,
) -> dict[str, Any] | None:
    if len(frontier) < 2 or cheapest_equals_lowest_y:
        return None
    cheapest = min(frontier, key=lambda point: float(point[X_METRIC]))
    best_y = min(frontier, key=lambda point: float(point[Y_METRIC]))
    x_at_cheapest = float(cheapest[X_METRIC])
    y_at_cheapest = float(cheapest[Y_METRIC])
    x_at_best_y = float(best_y[X_METRIC])
    y_at_best_y = float(best_y[Y_METRIC])
    delta_y = y_at_best_y - y_at_cheapest
    payload = {
        "x_metric": X_METRIC,
        "y_metric": Y_METRIC,
        "x_direction": X_DIRECTION,
        "y_direction": Y_DIRECTION,
        "x_units": X_UNITS,
        "y_units": Y_UNITS,
        "x_at_cheapest": x_at_cheapest,
        "y_at_cheapest": y_at_cheapest,
        "x_at_best_y": x_at_best_y,
        "y_at_best_y": y_at_best_y,
        "delta_x": x_at_best_y - x_at_cheapest,
        "delta_y": delta_y,
        "delta_y_percent": (
            100.0 * delta_y / y_at_cheapest if y_at_cheapest else None
        ),
    }
    if x_at_cheapest > 0:
        payload["x_ratio"] = x_at_best_y / x_at_cheapest
    return payload


def quality_block(
    landscape: Sequence[dict[str, Any]],
    *,
    grouping: dict[str, Any],
) -> dict[str, Any]:
    frontier = pareto_frontier(landscape)
    cheapest = min(frontier, key=lambda point: float(point[X_METRIC]))
    lowest_y = min(frontier, key=lambda point: float(point[Y_METRIC]))
    cheapest_equals_lowest_y = cheapest is lowest_y or (
        float(cheapest[X_METRIC]) == float(lowest_y[X_METRIC])
        and float(cheapest[Y_METRIC]) == float(lowest_y[Y_METRIC])
    )
    n_landscape = len(landscape)
    n_frontier = len(frontier)
    sparse = n_frontier == 1 or cheapest_equals_lowest_y
    if n_frontier == 1:
        knee_status = "endpoint_only_no_interior_knee"
    elif cheapest_equals_lowest_y:
        knee_status = "endpoint_only_no_interior_knee"
    elif n_frontier > 2:
        knee_status = "interior_tradeoff"
    else:
        knee_status = "endpoint_only_no_interior_knee"
    marked = []
    frontier_pairs = {point.get("pair_id") for point in frontier}
    for point in landscape:
        copied = dict(point)
        copied["is_frontier"] = copied.get("pair_id") in frontier_pairs
        marked.append(copied)
    return {
        "landscape_points": marked,
        "frontier_points": frontier,
        "n_landscape_points": n_landscape,
        "n_frontier_points": n_frontier,
        "frontier_fraction": (
            n_frontier / n_landscape if n_landscape else None
        ),
        "axis_spans": {
            X_METRIC: axis_span([float(p[X_METRIC]) for p in landscape]),
            Y_METRIC: axis_span([float(p[Y_METRIC]) for p in landscape]),
        },
        "cheapest_point": dict(cheapest),
        "frontier_tradeoff": frontier_tradeoff(
            frontier, cheapest_equals_lowest_y=cheapest_equals_lowest_y,
        ),
        "knee_status": knee_status,
        "cheapest_equals_lowest_y": cheapest_equals_lowest_y,
        "sparse_frontier": sparse,
        "grouping": grouping,
        "metric_units": {X_METRIC: X_UNITS, Y_METRIC: Y_UNITS},
        "x_metric": X_METRIC,
        "y_metric": Y_METRIC,
    }


def project_usable(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonical: str | None,
    skip_campaign_identity: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    usable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    by_type: Counter[str] = Counter()
    for row in rows:
        point = compact_process_row(row)
        if row_is_usable(
            point,
            canonical=canonical,
            skip_campaign_identity=skip_campaign_identity,
        ):
            usable.append(point)
            continue
        token = _exclusion_token(point)
        by_type[token] += 1
        excluded.append({
            "pair_id": point.get("pair_id"),
            "target_polymer": point.get("target_polymer"),
            "solvent": point.get("solvent"),
            "error_type": token,
            "outcome": point.get("outcome"),
        })
    return usable, excluded, dict(by_type)


def load_filtered_rows(
    path: Path,
    canonical: str,
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
) -> list[dict[str, Any]]:
    wanted_polymers = {
        _key(item) for item in (polymers or []) if item
    }
    wanted_solvent = str(solvent or "").strip() or None
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            row_fp = str(row.get("campaign_fingerprint") or "").strip().casefold()
            if row_fp != canonical:
                raise CampaignConsumeError(
                    "process_rows.jsonl campaign_fingerprint disagrees",
                    error_code="campaign_fingerprint_mismatch",
                    supplied=canonical,
                    computed=row_fp or None,
                    pair_id=row.get("pair_id"),
                )
            polymer = str(row.get("polymer") or "").strip()
            if wanted_polymers and _key(polymer) not in wanted_polymers:
                continue
            public_solvent = str(row.get("solvent_public_identity") or "")
            if wanted_solvent is not None and not _solvents_match(
                wanted_solvent, public_solvent,
            ):
                continue
            rows.append(row)
    return rows


def _filter_process_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
) -> list[Mapping[str, Any]]:
    wanted_polymers = {
        _key(item) for item in (polymers or []) if item
    }
    wanted_solvent = str(solvent or "").strip() or None
    filtered: list[Mapping[str, Any]] = []
    for row in rows:
        polymer = str(
            row.get("polymer") or row.get("target_polymer") or "",
        ).strip()
        if wanted_polymers and _key(polymer) not in wanted_polymers:
            continue
        public_solvent = str(
            row.get("solvent_public_identity") or row.get("solvent") or "",
        )
        if wanted_solvent is not None and not _solvents_match(
            wanted_solvent, public_solvent,
        ):
            continue
        filtered.append(row)
    return filtered


def _pair_id_for_economics_row(row: Mapping[str, Any], index: int) -> str:
    for key in ("pair_id", "label", "record_id"):
        text = str(row.get(key) or "").strip()
        if text:
            return text
    polymer = str(row.get("target_polymer") or row.get("polymer") or "").strip()
    solvent = str(row.get("solvent") or "").strip()
    case = str(row.get("energy_case") or "").strip()
    parts = [part for part in (polymer, solvent, case) if part]
    parameter = str(row.get("parameter") or "").strip()
    if parameter:
        parts.append(parameter)
        value = row.get("value")
        if value not in (None, ""):
            parts.append(str(value))
    token = "|".join(parts)
    return token or f"handle-row-{index + 1}"


def _outcome_for_economics_row(row: Mapping[str, Any]) -> str:
    if row.get("success") is True:
        return "success"
    if row.get("success") is False:
        return "failure"
    outcome = str(row.get("outcome") or "").strip()
    if outcome:
        return outcome
    if _finite_number(row.get(X_METRIC)):
        return "success"
    return "failure"


def economics_row_as_process_row(
    row: Mapping[str, Any],
    *,
    index: int = 0,
) -> dict[str, Any]:
    """JSONL-shaped process row from an evaluate, lookup, or sensitivity row."""
    from . import tea

    if isinstance(row.get("comparison_row"), dict) and (
        row.get("polymer") is not None
        or row.get("solvent_public_identity") is not None
    ):
        copied = dict(row)
        if not copied.get("pair_id"):
            copied["pair_id"] = _pair_id_for_economics_row(row, index)
        return copied
    public = tea._public_twelve_from_row(dict(row)) or {}
    worker = {
        internal: public[public_name]
        for internal, public_name in tea._DESIGN_POINT_PUBLIC_FIELDS
        if public_name in public
    }
    standing = dict(row.get("standing") or {})
    process_status = row.get("process_parameter_status")
    if isinstance(process_status, dict) and process_status:
        standing.setdefault("process_parameter_status", process_status)
    if "can_cite_as_validated_process" in row:
        standing.setdefault(
            "can_cite_as_validated_process",
            row["can_cite_as_validated_process"],
        )
    comparison = {
        X_METRIC: row.get(X_METRIC),
        Y_METRIC: row.get(Y_METRIC),
        **public,
    }
    coverage = row.get("lca_coverage")
    if isinstance(coverage, dict) and coverage:
        comparison["lca_coverage"] = coverage
    metric_status = row.get("lca_metric_status")
    if isinstance(metric_status, dict) and metric_status:
        comparison["lca_metric_status"] = dict(metric_status)
    payload = {
        "pair_id": _pair_id_for_economics_row(row, index),
        "polymer": public.get("target_polymer") or row.get("polymer"),
        "solvent_public_identity": public.get("solvent") or row.get("solvent"),
        "campaign_fingerprint": row.get("campaign_fingerprint"),
        "outcome": _outcome_for_economics_row(row),
        "error_type": row.get("error_type"),
        "standing": standing,
        "comparison_row": comparison,
        "config_normalized_twelve": worker,
    }
    safety = row.get("safety_standing")
    if isinstance(safety, dict):
        payload["safety_standing"] = dict(safety)
    if row.get("engine_mode"):
        payload["engine_mode"] = row["engine_mode"]
    if "original_thermo_rank" in row:
        payload["original_thermo_rank"] = row.get("original_thermo_rank")
        comparison["original_thermo_rank"] = row.get("original_thermo_rank")
    elif isinstance(row.get("comparison_row"), dict) and (
        "original_thermo_rank" in row["comparison_row"]
    ):
        payload["original_thermo_rank"] = row["comparison_row"].get(
            "original_thermo_rank"
        )
    return payload


def _optional_thermo_rank(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        number = int(value)
        return number if number >= 1 else None
    return None


def _stamp_thermo_econ_ranks(
    points: Sequence[dict[str, Any]],
    *,
    rank_metric: str,
) -> list[dict[str, Any]]:
    """Stamp economics rank 1..n and disagreement with original_thermo_rank."""
    indexed = list(enumerate(points))

    def sort_key(item: tuple[int, Mapping[str, Any]]) -> tuple[float, int]:
        index, point = item
        try:
            value = float(point[rank_metric])
        except (TypeError, ValueError, KeyError):
            return (math.inf, index)
        if not math.isfinite(value):
            return (math.inf, index)
        return (value, index)

    order = sorted(indexed, key=sort_key)
    ranks = {index: rank for rank, (index, _point) in enumerate(order, 1)}
    stamped: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        copied = dict(point)
        thermo = _optional_thermo_rank(copied.get("original_thermo_rank"))
        copied["original_thermo_rank"] = thermo
        copied["rank"] = ranks[index]
        copied["thermo_econ_rank_disagreement"] = (
            thermo is not None and thermo != ranks[index]
        )
        stamped.append(copied)
    return stamped


def _apply_thermo_econ_ranks(
    block: dict[str, Any],
    *,
    rank_metric: str,
) -> dict[str, Any]:
    landscape = list(block.get("landscape_points") or [])
    stamped = _stamp_thermo_econ_ranks(landscape, rank_metric=rank_metric)
    by_id = {point.get("pair_id"): point for point in stamped}
    block = dict(block)
    block["landscape_points"] = stamped
    frontier = [
        dict(by_id.get(point.get("pair_id"), point))
        for point in (block.get("frontier_points") or [])
        if isinstance(point, dict)
    ]
    if "frontier_points" in block:
        block["frontier_points"] = frontier
    cheapest = block.get("cheapest_point")
    if isinstance(cheapest, dict) and cheapest.get("pair_id") in by_id:
        block["cheapest_point"] = dict(by_id[cheapest["pair_id"]])
    return block


def _rank_usable_population(
    usable: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    by_type: dict[str, int],
    *,
    n_rows_read: int,
    polymer_grouping: str,
    operation: str,
    extra_census: Mapping[str, Any] | None = None,
    sort_metric: str | None = None,
) -> dict[str, Any]:
    census: dict[str, Any] = {
        "n_rows_read": n_rows_read,
        "excluded_count": len(excluded),
        "excluded_by_error_type": by_type,
        "excluded_records": excluded,
        "n_usable": len(usable),
        "ingested_into_admitted_cache": False,
        "safety_standing_policy": "carried_not_filtered",
        "view_family": "two_of_five",
        **dict(extra_census or {}),
    }
    if len(usable) < 2:
        raise CampaignConsumeError(
            "fewer than two usable rows after the projection",
            error_code="landscape_too_small",
            **census,
        )
    grouping_token = str(polymer_grouping or "per_target_polymer").strip()
    polymers_present = list(dict.fromkeys(
        str(point.get("target_polymer") or "") for point in usable
    ))
    rank_metric = str(sort_metric or X_METRIC).strip() or X_METRIC
    units = {X_METRIC: X_UNITS, Y_METRIC: Y_UNITS}
    if operation == "sort":
        ranked = sorted(usable, key=lambda point: float(point[rank_metric]))
        ranked = _stamp_thermo_econ_ranks(ranked, rank_metric=rank_metric)
        return {
            **census,
            "operation": "sort",
            "landscape_points": ranked,
            "n_landscape_points": len(ranked),
            "n_returned": len(ranked),
            "grouping": {
                "polymer_grouping": grouping_token,
                "target_polymers": polymers_present,
            },
            "metric_units": {rank_metric: units[rank_metric]},
            "x_metric": rank_metric,
        }
    if grouping_token == "per_target_polymer" and len(polymers_present) > 1:
        grouped = []
        for polymer in polymers_present:
            subset = [
                point for point in usable
                if point.get("target_polymer") == polymer
            ]
            if len(subset) < 2:
                continue
            grouped.append({
                "target_polymer": polymer,
                **_apply_thermo_econ_ranks(
                    quality_block(
                        subset,
                        grouping={
                            "polymer_grouping": "per_target_polymer",
                            "target_polymer": polymer,
                        },
                    ),
                    rank_metric=X_METRIC,
                ),
            })
        if len(grouped) < 1:
            raise CampaignConsumeError(
                "fewer than two usable rows after the projection",
                error_code="landscape_too_small",
                **census,
            )
        return {
            **census,
            "operation": "pareto_dominance",
            "polymer_grouping": "per_target_polymer",
            "grouped_fronts": grouped,
            "n_groups": len(grouped),
        }
    grouping = {
        "polymer_grouping": grouping_token,
        "target_polymers": polymers_present,
    }
    block = _apply_thermo_econ_ranks(
        quality_block(usable, grouping=grouping),
        rank_metric=X_METRIC,
    )
    n_land, n_front = block["n_landscape_points"], block["n_frontier_points"]
    if n_land != len(block["landscape_points"]) or n_front != len(
        block["frontier_points"]
    ):
        raise RuntimeError("landscape counts disagree with arrays")
    return {
        **census,
        "operation": "pareto_dominance",
        **block,
    }


def rank_process_rows(
    bound: Mapping[str, Any],
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
    polymer_grouping: str = "per_target_polymer",
    operation: str = "pareto_dominance",
    sort_metric: str | None = None,
) -> dict[str, Any]:
    canonical = str(bound["canonical"])
    rows = load_filtered_rows(
        bound["process_rows_path"],
        canonical,
        polymers=polymers,
        solvent=solvent,
    )
    usable, excluded, by_type = project_usable(rows, canonical=canonical)
    return _rank_usable_population(
        usable,
        excluded,
        by_type,
        n_rows_read=len(rows),
        polymer_grouping=polymer_grouping,
        operation=operation,
        extra_census={
            "campaign_fingerprint": canonical,
            "append_log_fingerprint": bound.get("append_log_fingerprint"),
            **dict(bound.get("projected") or {}),
        },
        sort_metric=sort_metric,
    )


def rank_handle_process_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
    polymer_grouping: str = "per_target_polymer",
    operation: str = "pareto_dominance",
    skip_campaign_identity: bool = True,
    canonical: str | None = None,
    extra_census: Mapping[str, Any] | None = None,
    sort_metric: str | None = None,
) -> dict[str, Any]:
    """Rank tool-1 rows already in a handle. No JSONL, no BioSTEAM."""
    converted = [
        economics_row_as_process_row(row, index=index)
        for index, row in enumerate(rows)
    ]
    fingerprints = {
        str(row.get("campaign_fingerprint") or "").strip().casefold()
        for row in converted
        if str(row.get("campaign_fingerprint") or "").strip()
    }
    if len(fingerprints) > 1:
        raise CampaignConsumeError(
            "handle mixes more than one campaign fingerprint",
            error_code="mixed_campaign_basis",
            fingerprints=sorted(fingerprints),
            n_rows_read=len(converted),
            ingested_into_admitted_cache=False,
        )
    filtered = _filter_process_rows(
        converted, polymers=polymers, solvent=solvent,
    )
    usable, excluded, by_type = project_usable(
        filtered,
        canonical=canonical,
        skip_campaign_identity=skip_campaign_identity,
    )
    extra: dict[str, Any] = dict(extra_census or {})
    if "campaign_fingerprint" not in extra and len(fingerprints) == 1:
        extra["campaign_fingerprint"] = next(iter(fingerprints))
    return _rank_usable_population(
        usable,
        excluded,
        by_type,
        n_rows_read=len(filtered),
        polymer_grouping=polymer_grouping,
        operation=operation,
        extra_census=extra,
        sort_metric=sort_metric,
    )


# --- optimization: Route-constrained pathway optimization over sourced process evidence.


_ASSET = Path(str(files("dissolve").joinpath("data/optimization.json.gz")))
_ASSET_SHA256 = "ffa6141a23ef0364f9d8e7734399922a71fc421e3a581105b7b4da75370b1258"
_DIRECTIONS = {
    "total_cost": "min", "emissions": "min", "profit": "max",
    "circularity": "max",
}
_TRADEOFF_UNITS = {
    "total_cost": "USD/yr",
    "emissions": "t CO2e/yr",
    "profit": "USD/yr",
    "circularity": "dimensionless",
}
_OBJECTIVES = {
    "min_cost": ("total_cost", "min"),
    "min_emissions": ("emissions", "min"),
    "max_profit": ("profit", "max"),
    "max_circularity": ("circularity", "max"),
}
_TECHNOLOGY_LABELS = {
    "lf": "Landfill", "we": "Waste-to-energy", "py": "Pyrolysis",
    "gas_er": "Gasification with energy recovery",
    "gas_h2": "Gasification to hydrogen",
    "gas_h2cc": "Gasification to hydrogen with carbon capture and storage",
}
Objective = Literal["max_profit", "min_emissions", "min_cost", "max_circularity"]
Metric = Literal["total_cost", "emissions", "profit", "circularity", "selectivity"]
Scenario = Literal["A", "B"]
SolverName = Literal["scip", "appsi_highs", "highs"]


@lru_cache(maxsize=1)
def asset_payload() -> dict[str, Any]:
    raw = _ASSET.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _ASSET_SHA256:
        raise RuntimeError(f"Optimization asset checksum mismatch: {digest}")
    return json.loads(gzip.decompress(raw))


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _scenario_key(value: Any) -> str:
    token = " ".join(str(value or "B").strip().casefold().replace("_", " ").split())
    aliases = {"a": "A", "scenario a": "A", "b": "B", "scenario b": "B"}
    if token not in aliases:
        raise ValueError("scenario must be A or B")
    return aliases[token]


def _metric_key(value: Any) -> str:
    token = " ".join(str(value or "").strip().casefold().replace("_", " ").split())
    aliases = {
        "cost": "total_cost", "total cost": "total_cost",
        "annual cost": "total_cost", "total annual cost": "total_cost",
        "emission": "emissions", "emissions": "emissions",
        "profit": "profit", "annual profit": "profit",
        "circularity": "circularity", "circularity proxy": "circularity",
        "selectivity": "selectivity", "selectivity margin": "selectivity",
    }
    if token not in aliases:
        raise ValueError(
            "Metric must be total_cost, emissions, profit, circularity, or selectivity"
        )
    return aliases[token]


def _objective_key(value: Any) -> str:
    token = " ".join(str(value or "max_profit").strip().casefold().replace("_", " ").split())
    aliases = {
        "max profit": "max_profit", "maximize profit": "max_profit",
        "min emissions": "min_emissions", "minimize emissions": "min_emissions",
        "min cost": "min_cost", "minimize cost": "min_cost",
        "min total cost": "min_cost", "max circularity": "max_circularity",
        "maximize circularity": "max_circularity",
    }
    if token not in aliases:
        raise ValueError("Unsupported objective")
    return aliases[token]


def _solver_key(value: Any) -> str:
    token = str(value or "scip").strip().casefold().replace("-", "_")
    aliases = {
        "scip": "scip", "highs": "highs", "appsi_highs": "appsi_highs",
    }
    if token not in aliases:
        raise ValueError("solver_name must be scip, highs, or appsi_highs")
    return aliases[token]


def _composition(values: dict[str, Any]) -> dict[str, float]:
    if not isinstance(values, dict) or not values:
        raise ValueError("A feed composition is required")
    result = {str(key): _number(value, f"fraction for {key}") for key, value in values.items()}
    total = sum(result.values())
    if total > 1.000001:
        if abs(total - 100.0) > 0.01:
            raise ValueError("Feed percentages must sum to 100")
        result = {key: value / 100.0 for key, value in result.items()}
    elif abs(total - 1.0) > 0.0001:
        raise ValueError("Feed mass fractions must sum to 1")
    if any(value <= 0 for value in result.values()):
        raise ValueError("Every feed mass fraction must be positive")
    return result


def _source_from_route_and_tea(
    route: Any,
    tea: Any,
) -> dict[str, Any]:
    """F source from an explicit costed-route payload. Never getattr."""
    if not route or not route.get("complete"):
        raise ValueError("A complete stored separation route is required")
    if not tea or tea.get("route_source") != "typed_session_state":
        raise ValueError("A route-backed stored TEA/LCA result is required")
    rows = list(tea.get("comparison_rows") or [])
    if not rows or any(row.get("stage") is None for row in rows):
        raise ValueError("Stored TEA/LCA state lacks stage metrics")
    expected_signature = route_evidence_signature(route)
    if tea.get("route_signature") != expected_signature:
        raise ValueError("Stored TEA/LCA state is stale for the current route")
    route_steps = list(route.get("steps") or [])
    ordered_rows = sorted(rows, key=lambda row: int(row["stage"]))
    if len(route_steps) != len(ordered_rows) or any(
        (
            str(step.get("dissolved_polymer")), str(step.get("solvent")),
            float(step.get("temperature_c")),
        ) != (
            str(row.get("polymer")), str(row.get("solvent")),
            float(row.get("dissolution_temperature_c")),
        )
        for step, row in zip(route_steps, ordered_rows)
    ):
        raise ValueError("Stored TEA/LCA stages no longer match the current route")
    composition = _composition(tea.get("feed_mass_fractions") or {})
    route_polymers = {
        str(step.get("dissolved_polymer")) for step in route.get("steps") or []
    } | {str(route.get("final_residue"))}
    if set(composition) != route_polymers:
        raise ValueError("Stored TEA composition no longer matches the stored route")
    return {
        "route": route, "tea": tea, "stages": ordered_rows,
        "composition": composition,
        "feed_mt_per_yr": _number(tea.get("processing_capacity_mt_per_yr"), "processing capacity"),
    }


def _source_state() -> dict[str, Any]:
    state = current_tool_session()
    route = copy.deepcopy(getattr(state, "last_route", None)) if state else None
    tea = copy.deepcopy(getattr(state, "last_tea", None)) if state else None
    return _source_from_route_and_tea(route, tea)


def _proposed_design_points_from_feed_gap(
    prior: dict[str, Any],
) -> list[dict[str, Any]]:
    """Name admitted twelve-field points that would complete the feed gap.

    Uses the same public vocabulary as `_public_design_point` / remnant
    keys. Values come from pinned cache records; nothing is invented.
    """
    from . import tea

    names = [
        *list(prior.get("requested_feed_mass_fractions") or {}),
        *list(prior.get("requested_feed_polymers") or []),
    ]
    wanted = {tea._key(name) for name in names if str(name or "").strip()}
    if not wanted:
        return []
    points: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for record in tea._records():
        config = dict(record.get("config") or {})
        if tea._key(config.get("target_plastic")) not in wanted:
            continue
        point = tea._public_design_point(config)
        ident = tuple(
            (public, point.get(public))
            for _internal, public in tea._DESIGN_POINT_PUBLIC_FIELDS
        )
        if ident in seen:
            continue
        seen.add(ident)
        points.append(point)
    return points


def _stored_optimization_gap(x_metric: str, y_metric: str) -> str | None:
    state = current_tool_session()
    prior = dict(getattr(state, "last_tea", None) or {}) if state else {}
    if prior.get("analysis_type") == "tea_feed_scale_basis_gap":
        tool = "pareto_optimize_stored_route"
        if {x_metric, y_metric} != {"total_cost", "circularity"}:
            return tool_error(
                tool,
                "The unresolved feed-scale basis supports only a cost-versus-circularity basis-gap assessment.",
                error_code="invalid_pareto_basis",
            )
        return tool_error(
            tool,
            "No cost-versus-circularity frontier or knee can be calculated without comparable route-backed designs.",
            error_code="insufficient_feed_optimization_basis",
            analysis_type="feed_optimization_basis_gap",
            can_optimize=False, can_build_frontier=False,
            requested_objective="cost_vs_circularity",
            x_metric=x_metric, y_metric=y_metric,
            proposed_design_points=_proposed_design_points_from_feed_gap(prior),
            requested_feed_mass_fractions=dict(
                prior.get("requested_feed_mass_fractions") or {}
            ),
            unresolved_polymer_identities=list(
                prior.get("unresolved_polymer_identities") or []
            ),
            supported_interpretations=dict(prior.get("supported_interpretations") or {}),
            requested_scale_capacities_mt_per_yr=list(
                prior.get("requested_scale_capacities_mt_per_yr") or []
            ),
            available_cache_energy_cases=list(
                prior.get("available_cache_energy_cases") or []
            ),
            energy_case_descriptions=dict(prior.get("energy_case_descriptions") or {}),
            conditional_cache_coverage=dict(prior.get("conditional_cache_coverage") or {}),
            cache_capacity_ranges_mt_per_yr=dict(
                prior.get("cache_capacity_ranges_mt_per_yr") or {}
            ),
            n_frontier_points=0, knee_status="not_calculated_no_comparable_designs",
            missing_basis_codes=[
                *list(prior.get("missing_basis_codes") or []),
                "route_backed_scale_energy_landscape", "circularity_basis",
                "recovery_residual_pathway_basis",
            ],
            missing_process_inputs=[
                "Resolve the PE grade and define a complete solvent/setpoint separation route.",
                "Evaluate one common route-backed cost basis at every capacity and energy case.",
                "Define recovery, product basis, circularity metric, and residual pathway assumptions.",
            ],
            warnings=[
                "Conditional stage-cache coverage is not a cost or circularity design point.",
                "No optimum, Pareto point, knee, solvent selection, or frontier artifact was calculated.",
            ],
        )
    if prior.get("analysis_type") != "candidate_lca_basis_gap":
        return None
    tool = "pareto_optimize_stored_route"
    if {x_metric, y_metric} != {"emissions", "selectivity"}:
        return tool_error(tool, "The stored candidate screen supports only an emissions-versus-selectivity basis-gap assessment.", error_code="invalid_pareto_basis")
    floor = (getattr(state, "last_screen_constraints", None) or {}).get("minimum_selectivity_points")
    # Shape selection belongs to the harness. The optimization engine accepts
    # bound rows only when the selectivity fields below establish its basis.
    candidates, _ = candidate_evidence(state)
    if floor is None or not candidates or any(row.get("selectivity_pct") is None for row in candidates):
        return tool_error(tool, "The stored screen lacks a selectivity floor or candidate values.", error_code="invalid_pareto_basis")
    safety = {
        str(row.get("solvent") or "").casefold(): row
        for row in getattr(state, "last_safety", None) or []
    }
    rows = []
    for candidate in candidates:
        solvent, temperature = str(candidate.get("solvent") or ""), candidate.get("temperature_c")
        boiling, selectivity = safety.get(solvent.casefold(), {}).get("boiling_point_c"), float(candidate["selectivity_pct"])
        rows.append({"solvent": solvent, "temperature_c": temperature,
            "selectivity_percentage_points": selectivity, "meets_selectivity_floor": selectivity >= float(floor),
            "boiling_point_c": boiling, "atmospheric_feasible": (
                float(temperature) < float(boiling) if temperature is not None and boiling is not None else None)})
    qualifying = [row["solvent"] for row in rows if row["meets_selectivity_floor"]]
    atmospheric = sum(row["meets_selectivity_floor"] and row["atmospheric_feasible"] is True for row in rows)
    return tool_error(
        tool,
        "No defensible emissions optimization or Pareto frontier can be calculated from the stored candidate screen.",
        error_code="insufficient_optimization_basis",
        analysis_type="optimization_basis_gap", can_optimize=False,
        can_build_frontier=False, requested_objective="min_emissions",
        x_metric=x_metric, y_metric=y_metric, constraint_metric="selectivity",
        minimum_selectivity_points=float(floor), constraint_unit="percentage_points",
        target_product=prior.get("target_product"), other_polymers=list(prior.get("other_polymers") or []),
        temperature_min_c=getattr(state, "temperature_min_c", None), temperature_max_c=getattr(state, "temperature_max_c", None),
        candidate_conditions=rows, qualifying_candidates=qualifying,
        atmospheric_qualifying_candidate_count=atmospheric,
        missing_basis_codes=[
            *list(prior.get("missing_basis_codes") or []),
            "comparable_candidate_gwp", "complete_process_route",
            "feed_composition", "process_capacity", "decision_variables",
        ],
        missing_process_inputs=list(prior.get("missing_process_inputs") or []) +
        ["Define a complete process route, feed composition, capacity, recovery, and decision variables."],
        warnings=["No candidate GWP, emissions winner, Pareto point, or frontier artifact was calculated.",
                  "The selectivity floor is a screening heuristic; modeled solution concentrations are not recovery or purity."],
    )


def _stored_point_optimization_gap(objective: str) -> str | None:
    """Preserve an uncostable product-portfolio decision without defaulting it."""
    state = current_tool_session()
    prior = dict(getattr(state, "last_tea", None) or {}) if state else {}
    if prior.get("analysis_type") != "tea_route_portfolio_basis_gap":
        return None
    missing_codes = list(dict.fromkeys([
        *list(prior.get("missing_basis_codes") or []),
        "route_backed_product_economics", "polymer_market_values",
        "recovery_residual_pathway_basis", "landfill_cost_emissions_basis",
    ]))
    return tool_error(
        "optimize_stored_route",
        "No max-profit recovery or landfill allocation can be calculated from the uncosted product-portfolio basis.",
        error_code="insufficient_portfolio_optimization_basis",
        analysis_type="portfolio_optimization_basis_gap",
        can_optimize=False, can_select_recovery_portfolio=False,
        can_assign_landfill=False, profit_calculated=False,
        requested_objective=objective,
        requested_product_count=prior.get("requested_product_count"),
        product_selection_basis=prior.get("product_selection_basis"),
        route_products=list(prior.get("route_products") or []),
        target_products=list(prior.get("target_products") or []),
        requested_capacity_mt_per_yr=prior.get("requested_capacity_mt_per_yr"),
        requested_capacity_basis=prior.get("requested_capacity_basis"),
        selected_recovered_polymers=[], selected_landfilled_polymers=[],
        n_landscape_points=0, missing_basis_codes=missing_codes,
        missing_process_inputs=[
            *list(prior.get("missing_process_inputs") or []),
            "Define polymer-specific residual or landfill costs, emissions, and constraints on a common route-backed basis.",
        ],
        warnings=[
            "Thermodynamic route order and solubility do not establish product value, profit, recovery, or landfill allocation.",
            "No optimum, recovered-polymer set, landfilled-polymer set, profit, or design point was calculated.",
        ],
    )


def _technology_rows(scenario: str) -> dict[str, dict[str, float]]:
    key = _scenario_key(scenario)
    rows = (asset_payload().get("technology_scenarios") or {}).get(key)
    if not rows:
        raise ValueError("scenario must be A or B")
    return rows


def _usable_economics(point: dict[str, Any]) -> bool:
    """P1.3: exclude zero-capital anchors from an executable landscape."""
    return (
        float(point.get("capital_cost") or 0.0) > 0.0
        and (
            float(point.get("operational_cost") or 0.0) > 0.0
            or float(point.get("emissions") or 0.0) > 0.0
        )
    )


def _market_values(
    overrides: Optional[dict[str, float]], polymers: Sequence[str] = (),
) -> dict[str, float]:
    defaults = dict((asset_payload().get("policy") or {}).get("default_market_values_usd_per_mt") or {})
    for polymer, value in (overrides or {}).items():
        number = _number(value, f"market value for {polymer}")
        if number < 0:
            raise ValueError("Polymer market values must be nonnegative")
        defaults[str(polymer)] = number
    return {
        polymer: float(defaults.get(polymer, 0.0)) for polymer in polymers
    } if polymers else defaults


def _landscape(
    source: dict[str, Any], *, scenario: str, recovery_yield: float,
    market_values: dict[str, float], composition: Optional[dict[str, float]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    composition = composition or source["composition"]
    feed = source["feed_mt_per_yr"]
    stages = source["stages"]
    technologies = _technology_rows(scenario)
    policy = asset_payload().get("policy") or {}
    distances = policy.get("distances_mile") or {}
    diversion = policy.get("residual_diversion_factors") or {}
    fixed_transport = float(policy.get("transport_fixed_usd_per_mt") or 0.0)
    variable_transport = float(policy.get("transport_variable_usd_per_mt_mile") or 0.0)
    all_points: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for active_count in range(len(stages) + 1):
        active = stages[:active_count]
        for technology, tech in sorted(technologies.items()):
            recovered_by_polymer = {
                str(stage["polymer"]): feed * composition[str(stage["polymer"])] * recovery_yield
                for stage in active
            }
            recovered = sum(recovered_by_polymer.values())
            residual = max(0.0, feed - recovered)
            stage_tci = sum(float(stage["tci_usd"]) for stage in active)
            allocation_years = int(policy["strap_capital_allocation_years"])
            # The workbook's residual-technology CAPEX coefficients are already
            # USD/yr. BioSTEAM TCI is installed USD and follows the v10 adapter's
            # explicit straight-line allocation before entering an annual objective.
            annual_capital = stage_tci / allocation_years + float(tech["capex"])
            operating = sum(float(stage["aoc_usd_per_yr"]) for stage in active) + residual * float(tech["opex"])
            transport = residual * (
                fixed_transport + variable_transport * float(distances.get(technology, 0.0))
            )
            emissions = (
                sum(
                    float(stage["gwp_kg_co2e_per_kg"]) * recovered_by_polymer[str(stage["polymer"])]
                    for stage in active
                )
                + residual * float(tech["gwp"])
            )
            energy = (
                sum(
                    float(stage.get("total_energy_mj_per_kg") or 0.0)
                    * recovered_by_polymer[str(stage["polymer"])] * 1000.0
                    for stage in active
                )
                + residual * float(tech["total_energy"])
            )
            revenue = sum(
                mass * float(market_values.get(polymer, 0.0))
                for polymer, mass in recovered_by_polymer.items()
            )
            total_cost = annual_capital + operating + transport
            circularity = (recovered + residual * float(diversion.get(technology, 0.0))) / feed
            selected_polymers = [str(stage["polymer"]) for stage in active]
            point = {
                "design_id": f"r{active_count}-{technology}",
                "active_recovery_stages": active_count,
                "recovered_polymers": selected_polymers,
                "recovered_mass_mt_per_yr_by_polymer": recovered_by_polymer,
                "residual_polymers": [name for name in composition if name not in selected_polymers],
                "residual_mass_mt_per_yr": residual,
                "residual_technology": technology,
                "residual_technology_label": _TECHNOLOGY_LABELS[technology],
                "stage_conditions": [{
                    "stage": stage["stage"], "polymer": stage["polymer"],
                    "solvent": stage["solvent"],
                    "temperature_c": stage["dissolution_temperature_c"],
                } for stage in active],
                "stage_tci_usd": stage_tci,
                "capital_cost": annual_capital, "operational_cost": operating,
                "transportation_cost": transport, "total_cost": total_cost,
                "emissions": emissions, "energy_mj_per_yr": energy,
                "revenue": revenue, "profit": revenue - total_cost,
                "circularity": circularity,
                "economics_usable": False,
            }
            point["economics_usable"] = _usable_economics(point)
            (all_points if point["economics_usable"] else rejected).append(point)
    for index, point in enumerate(all_points, 1):
        point["landscape_point_id"] = index
    return all_points, rejected


def _dominates_on(candidate: dict[str, Any], baseline: dict[str, Any], x: str, y: str) -> bool:
    comparisons = []
    strict = False
    for metric in (x, y):
        first, second = float(candidate[metric]), float(baseline[metric])
        better = first <= second if _DIRECTIONS[metric] == "min" else first >= second
        comparisons.append(better)
        strict = strict or (first < second if _DIRECTIONS[metric] == "min" else first > second)
    return all(comparisons) and strict


def _pareto_sweep(
    landscape: Sequence[dict[str, Any]], x_metric: str, y_metric: str,
) -> list[dict[str, Any]]:
    """One dominance template for all supported objective pairs (P3.2)."""
    if x_metric not in _DIRECTIONS or y_metric not in _DIRECTIONS or x_metric == y_metric:
        raise ValueError("Choose two distinct metrics from total_cost, emissions, profit, circularity")
    frontier = [
        point for point in landscape
        if not any(
            other is not point and _dominates_on(other, point, x_metric, y_metric)
            for other in landscape
        )
    ]
    reverse = _DIRECTIONS[x_metric] == "max"
    frontier.sort(key=lambda point: float(point[x_metric]), reverse=reverse)
    result = []
    for index, point in enumerate(frontier, 1):
        copied = copy.deepcopy(point)
        copied.update({"point_id": index, "point_status": "frontier", "is_frontier": True})
        result.append(copied)
    return result


def _knee(frontier: Sequence[dict[str, Any]], x: str, y: str) -> Optional[dict[str, Any]]:
    if not frontier:
        return None
    if len(frontier) == 1:
        return copy.deepcopy(frontier[0])
    values = {metric: [float(point[metric]) for point in frontier] for metric in (x, y)}

    def loss(metric: str, value: float) -> float:
        low, high = min(values[metric]), max(values[metric])
        if high - low <= 1e-12:
            return 0.0
        return (
            (value - low) / (high - low)
            if _DIRECTIONS[metric] == "min" else (high - value) / (high - low)
        )

    return copy.deepcopy(min(
        frontier,
        key=lambda point: math.hypot(loss(x, float(point[x])), loss(y, float(point[y]))),
    ))


def _cost_emissions_tradeoff(
    frontier: Sequence[dict[str, Any]], x_metric: str, y_metric: str,
) -> Optional[dict[str, Any]]:
    """Return decision arithmetic for a cost/emissions frontier without choosing a preference."""
    if {x_metric, y_metric} != {"total_cost", "emissions"} or len(frontier) < 2:
        return None
    cheapest = min(frontier, key=lambda point: float(point["total_cost"]))
    lower_emissions = min(frontier, key=lambda point: float(point["emissions"]))
    cost_increase = float(lower_emissions["total_cost"]) - float(cheapest["total_cost"])
    emissions_reduction = float(cheapest["emissions"]) - float(lower_emissions["emissions"])
    if cheapest is lower_emissions or cost_increase <= 0 or emissions_reduction <= 0:
        return None
    return {
        "cheapest_design_id": cheapest["design_id"],
        "lower_emissions_design_id": lower_emissions["design_id"],
        "incremental_annual_cost_usd": cost_increase,
        "annual_emissions_reduction_t_co2e": emissions_reduction,
        "emissions_reduction_percent": (
            100.0 * emissions_reduction / float(cheapest["emissions"])
        ),
        "incremental_cost_usd_per_t_co2e_avoided": cost_increase / emissions_reduction,
        "selected_to_cheapest_cost_ratio": (
            float(lower_emissions["total_cost"]) / float(cheapest["total_cost"])
        ),
    }


def _stamp_residual_point(point: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Carry safety_standing on an F point. Not a GSK-fail filter."""
    if not isinstance(point, dict):
        return point
    copied = dict(point)
    copied["safety_standing"] = _carried_safety_standing(copied)
    return copied


def _stamp_residual_points(
    points: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [_stamp_residual_point(point) for point in points]


def _residual_grouping(source: Mapping[str, Any]) -> dict[str, Any]:
    """Held identity of one costed route. Not process_rows polymer_grouping."""
    return {
        "source_route_signature": source["tea"].get("route_signature"),
        "feed_mass_fractions": dict(source["composition"]),
        "feed_mt_per_yr": source["feed_mt_per_yr"],
    }


def _axis_extreme(
    points: Sequence[dict[str, Any]], key: str, direction: str,
) -> dict[str, Any]:
    if direction == "max":
        return max(points, key=lambda point: float(point[key]))
    return min(points, key=lambda point: float(point[key]))


def _residual_axis_spans(
    landscape: Sequence[dict[str, Any]], x_key: str, y_key: str,
) -> dict[str, dict[str, float]]:
    """Hyndman–Fan type 7 spans of the usable landscape, not the frontier."""
    if not landscape:
        return {}
    return {
        x_key: axis_span([float(point[x_key]) for point in landscape]),
        y_key: axis_span([float(point[y_key]) for point in landscape]),
    }


def _residual_pareto_quality(
    landscape: Sequence[dict[str, Any]],
    frontier: Sequence[dict[str, Any]],
    x_key: str,
    y_key: str,
) -> dict[str, Any]:
    """F quality fields on residual pareto: fraction, sparse, equality, spans."""
    n_landscape = len(landscape)
    n_frontier = len(frontier)
    fraction = (n_frontier / n_landscape) if n_landscape else None
    spans = _residual_axis_spans(landscape, x_key, y_key)
    if not frontier:
        return {
            "frontier_fraction": fraction,
            "sparse_frontier": False,
            "cheapest_equals_lowest_y": False,
            "axis_spans": spans,
        }
    cheapest = _axis_extreme(frontier, x_key, _DIRECTIONS[x_key])
    best_y = _axis_extreme(frontier, y_key, _DIRECTIONS[y_key])
    equals = cheapest is best_y or (
        float(cheapest[x_key]) == float(best_y[x_key])
        and float(cheapest[y_key]) == float(best_y[y_key])
    )
    return {
        "frontier_fraction": fraction,
        "sparse_frontier": n_frontier == 1 or equals,
        "cheapest_equals_lowest_y": equals,
        "axis_spans": spans,
    }


def _metric_generic_tradeoff(
    frontier: Sequence[dict[str, Any]],
    x_key: str,
    y_key: str,
    *,
    cheapest_equals_lowest_y: bool,
) -> dict[str, Any] | None:
    """§9.2.2 metric-generic tradeoff. Null on a star. F units, not F leaf names."""
    if len(frontier) < 2 or cheapest_equals_lowest_y:
        return None
    cheapest = _axis_extreme(frontier, x_key, _DIRECTIONS[x_key])
    best_y = _axis_extreme(frontier, y_key, _DIRECTIONS[y_key])
    x_at_cheapest = float(cheapest[x_key])
    y_at_cheapest = float(cheapest[y_key])
    x_at_best_y = float(best_y[x_key])
    y_at_best_y = float(best_y[y_key])
    delta_y = y_at_best_y - y_at_cheapest
    payload: dict[str, Any] = {
        "x_metric": x_key,
        "y_metric": y_key,
        "x_direction": _DIRECTIONS[x_key],
        "y_direction": _DIRECTIONS[y_key],
        "x_units": _TRADEOFF_UNITS[x_key],
        "y_units": _TRADEOFF_UNITS[y_key],
        "x_at_cheapest": x_at_cheapest,
        "y_at_cheapest": y_at_cheapest,
        "x_at_best_y": x_at_best_y,
        "y_at_best_y": y_at_best_y,
        "delta_x": x_at_best_y - x_at_cheapest,
        "delta_y": delta_y,
        "delta_y_percent": (
            100.0 * delta_y / y_at_cheapest if y_at_cheapest else None
        ),
    }
    if x_at_cheapest > 0:
        payload["x_ratio"] = x_at_best_y / x_at_cheapest
    return payload


def _solver_version(name: str) -> Optional[str]:
    if name in {"appsi_highs", "highs"}:
        try:
            return f"highspy {package_version('highspy')}"
        except PackageNotFoundError:
            return None
    executable = shutil.which(name)
    if not executable:
        return None
    try:
        line = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            timeout=10, check=False,
        ).stdout.splitlines()[0]
        return line.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _verify_point(
    landscape: Sequence[dict[str, Any]], objective: str, solver_name: str,
) -> dict[str, Any]:
    version = _solver_version(solver_name)
    try:
        import pyomo.environ as pyo
    except ImportError:
        return {"status": "skipped", "reason": "pyomo_not_installed", "solver": solver_name, "version": version}
    solver = pyo.SolverFactory(solver_name)
    if not solver.available(False):
        return {"status": "skipped", "reason": "solver_unavailable", "solver": solver_name, "version": version}
    metric, direction = _OBJECTIVES[objective]
    model = pyo.ConcreteModel("DISSOLVE_route_landscape")
    model.designs = pyo.RangeSet(0, len(landscape) - 1)
    model.choose = pyo.Var(model.designs, domain=pyo.Binary)
    model.one = pyo.Constraint(expr=sum(model.choose[index] for index in model.designs) == 1)
    expression = sum(float(landscape[index][metric]) * model.choose[index] for index in model.designs)
    model.objective = pyo.Objective(
        expr=expression, sense=pyo.minimize if direction == "min" else pyo.maximize,
    )
    result = solver.solve(model, tee=False)
    selected = [index for index in model.designs if pyo.value(model.choose[index]) > 0.5]
    termination = str(result.solver.termination_condition)
    return {
        "status": "verified" if len(selected) == 1 else "failed",
        "reason": None if len(selected) == 1 else "no_unique_design",
        "solver": solver_name, "version": version,
        "termination_condition": termination,
        "selected_design_id": landscape[selected[0]]["design_id"] if len(selected) == 1 else None,
    }


def _deterministic_optimum(landscape: Sequence[dict[str, Any]], objective: str) -> dict[str, Any]:
    metric, direction = _OBJECTIVES[objective]
    return copy.deepcopy(
        min(landscape, key=lambda point: float(point[metric]))
        if direction == "min" else max(landscape, key=lambda point: float(point[metric]))
    )


def _payload_path(kind: str, payload: dict[str, Any]) -> str:
    root = Path(os.getenv("DISSOLVE_OUTPUT_DIR") or Path.cwd() / "plots") / "optimization_payloads"
    root.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    path = (root / f"{kind}_{hashlib.sha256(canonical.encode()).hexdigest()[:12]}.json").resolve()
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return str(path)


def rank_residual_route(
    source: dict[str, Any],
    *,
    operation: str,
    objective: Objective = "max_profit",
    scenario: Scenario = "B",
    recovery_yield: Optional[float] = None,
    polymer_market_values_usd_per_mt: Optional[dict[str, float]] = None,
    solver_name: SolverName = "scip",
    x_metric: Metric = "total_cost",
    y_metric: Metric = "emissions",
    composition_slices: Optional[list[dict[str, float]]] = None,
) -> str:
    """Prefix × leftover-tech ranking of one costed route. Handle-fed source."""
    tool = "rank_landscape"
    operation_token = str(operation or "").strip().casefold()
    shared = {
        "source": "residual_route",
        "source_route_signature": source["tea"].get("route_signature"),
        "source_tea_engine_mode": source["tea"].get("engine_mode"),
        "engine_mode": source["tea"].get("engine_mode"),
        "feed_mass_fractions": source["composition"],
        "feed_mt_per_yr": source["feed_mt_per_yr"],
    }
    try:
        scenario_key = _scenario_key(scenario)
        recovery = _number(
            recovery_yield if recovery_yield is not None else
            (asset_payload().get("policy") or {}).get("default_recovery_yield"),
            "recovery_yield",
        )
        if not 0 < recovery <= 1:
            raise ValueError("recovery_yield must be above 0 and at most 1")
        values = _market_values(
            polymer_market_values_usd_per_mt, tuple(source["composition"]),
        )
        if operation_token == "optimum":
            objective_key = _objective_key(objective)
            solver_key = _solver_key(solver_name)
            landscape, rejected = _landscape(
                source, scenario=scenario_key, recovery_yield=recovery,
                market_values=values,
            )
        elif operation_token == "pareto_dominance":
            x_key = _metric_key(x_metric)
            y_key = _metric_key(y_metric)
            slices = composition_slices or [source["composition"]]
            normalized_slices = [_composition(item) for item in slices]
            if any(
                set(item) != set(source["composition"])
                for item in normalized_slices
            ):
                raise ValueError(
                    "Every composition slice must cover the stored-route polymers"
                )
        else:
            raise ValueError("operation must be optimum or pareto_dominance")
    except ValueError as error:
        message = str(error)
        if message == "Unsupported objective":
            return tool_error(
                tool, "Unsupported objective.",
                error_code="unsupported_objective",
                supported_objectives=sorted(_OBJECTIVES),
                **shared,
            )
        code = (
            "invalid_pareto_basis"
            if operation_token == "pareto_dominance"
            else "invalid_optimization_basis"
        )
        return tool_error(tool, message, error_code=code, **shared)
    if operation_token == "optimum":
        if not landscape:
            return tool_error(
                tool, "No design has usable economics.",
                error_code="no_usable_designs", **shared,
            )
        selected = _deterministic_optimum(landscape, objective_key)
        verification = _verify_point(landscape, objective_key, solver_key)
        verification["agrees_with_deterministic_optimum"] = (
            verification.get("selected_design_id") == selected["design_id"]
            if verification.get("status") == "verified" else None
        )
        marked = _stamp_residual_points(landscape)
        return tool_success(
            tool, analysis_type="point_optimum", operation="optimum",
            objective=objective_key, scenario=scenario_key,
            recovery_yield=recovery,
            polymer_market_values_usd_per_mt=values,
            capital_allocation_years=int(
                (asset_payload().get("policy") or {})["strap_capital_allocation_years"]
            ),
            residual_product_revenue_included=False,
            selected_point=_stamp_residual_point(selected),
            cheapest_point=_stamp_residual_point(
                min(landscape, key=lambda point: float(point["total_cost"]))
            ),
            landscape_points=marked,
            n_landscape_points=len(marked),
            n_rejected_phantom_designs=len(rejected),
            solver=verification,
            economics_guard="annualized CAPEX>0 AND (OPEX>0 OR GWP>0)",
            metric_units={
                "capital_cost": "USD/yr (BioSTEAM TCI allocated over 10 years)",
                "operational_cost": "USD/yr", "transportation_cost": "USD/yr",
                "total_cost": "USD/yr", "revenue": "USD/yr", "profit": "USD/yr",
                "emissions": "t CO2e/yr", "energy_mj_per_yr": "MJ/yr",
                "circularity": "mass-diversion fraction",
            },
            circularity_basis=(asset_payload().get("policy") or {}).get(
                "circularity_note"
            ),
            provenance={
                "asset_sha256": _ASSET_SHA256,
                **(asset_payload().get("provenance") or {}),
            },
            **shared,
        )
    slice_payloads = []
    for index, composition in enumerate(normalized_slices, 1):
        landscape, rejected = _landscape(
            source, scenario=scenario_key, recovery_yield=recovery,
            market_values=values, composition=composition,
        )
        frontier = _pareto_sweep(landscape, x_key, y_key)
        if not frontier:
            continue
        cheapest = copy.deepcopy(
            min(frontier, key=lambda point: float(point["total_cost"]))
        )
        knee = _knee(frontier, x_key, y_key)
        knee_status = (
            "interior_tradeoff"
            if len(frontier) > 2 and knee and knee.get("design_id") not in {
                frontier[0].get("design_id"), frontier[-1].get("design_id"),
            }
            else "endpoint_only_no_interior_knee"
        )
        if not landscape:
            knee_status = "not_calculated_no_comparable_designs"
        frontier_ids = {point.get("design_id") for point in frontier}
        marked = []
        for point in landscape:
            copied = dict(point)
            copied["is_frontier"] = copied.get("design_id") in frontier_ids
            marked.append(copied)
        marked = _stamp_residual_points(marked)
        frontier = _stamp_residual_points(frontier)
        cheapest = _stamp_residual_point(cheapest)
        quality = _residual_pareto_quality(marked, frontier, x_key, y_key)
        grouping = _residual_grouping(source)
        slice_payloads.append({
            "slice_id": f"slice-{index}",
            "feed_mass_fractions": composition,
            "landscape_points": marked,
            "frontier_points": frontier,
            "points": frontier,
            "n_landscape_points": len(marked),
            "n_frontier_points": len(frontier),
            "n_rejected_phantom_designs": len(rejected),
            "knee_point": _stamp_residual_point(knee), "knee_status": knee_status,
            "cheapest_point": cheapest,
            "frontier_tradeoff": _metric_generic_tradeoff(
                frontier,
                x_key,
                y_key,
                cheapest_equals_lowest_y=quality["cheapest_equals_lowest_y"],
            ),
            "grouping": grouping,
            **quality,
        })
    if not slice_payloads:
        return tool_error(
            tool, "No feasible Pareto frontier was found.",
            error_code="no_pareto_points", **shared,
        )
    primary = slice_payloads[0]
    n_land = primary["n_landscape_points"]
    n_front = primary["n_frontier_points"]
    return tool_success(
        tool,
        analysis_type=(
            "pareto_slices" if len(slice_payloads) > 1 else "pareto_front"
        ),
        operation="pareto_dominance",
        x_metric=x_key, y_metric=y_key, scenario=scenario_key,
        recovery_yield=recovery,
        polymer_market_values_usd_per_mt=values,
        capital_allocation_years=int(
            (asset_payload().get("policy") or {})["strap_capital_allocation_years"]
        ),
        residual_product_revenue_included=False,
        n_slices_requested=len(normalized_slices),
        n_slices_solved=len(slice_payloads),
        landscape_points=primary["landscape_points"],
        frontier_points=primary["frontier_points"],
        points=primary["points"],
        n_landscape_points=n_land,
        n_frontier_points=n_front,
        frontier_fraction=primary["frontier_fraction"],
        sparse_frontier=primary["sparse_frontier"],
        cheapest_equals_lowest_y=primary["cheapest_equals_lowest_y"],
        axis_spans=primary["axis_spans"],
        knee_point=primary["knee_point"],
        knee_status=primary["knee_status"],
        cheapest_point=primary["cheapest_point"],
        frontier_tradeoff=primary["frontier_tradeoff"],
        grouping=primary["grouping"],
        slices=[{
            key: item[key] for key in (
                "slice_id", "feed_mass_fractions", "n_landscape_points",
                "n_frontier_points", "frontier_fraction", "sparse_frontier",
                "cheapest_equals_lowest_y", "axis_spans", "knee_point", "cheapest_point",
                "knee_status", "frontier_tradeoff", "grouping",
            )
        } for item in slice_payloads],
        points_are_subset_of_landscape=True,
        economics_guard="annualized CAPEX>0 AND (OPEX>0 OR GWP>0)",
        metric_units={
            "capital_cost": "USD/yr (BioSTEAM TCI allocated over 10 years)",
            "operational_cost": "USD/yr", "transportation_cost": "USD/yr",
            "total_cost": "USD/yr", "revenue": "USD/yr", "profit": "USD/yr",
            "emissions": "t CO2e/yr", "energy_mj_per_yr": "MJ/yr",
            "circularity": "mass-diversion fraction",
        },
        circularity_basis=(asset_payload().get("policy") or {}).get(
            "circularity_note"
        ),
        provenance={
            "asset_sha256": _ASSET_SHA256,
            **(asset_payload().get("provenance") or {}),
        },
        **shared,
    )


def optimize_stored_route(
    objective: Objective = "max_profit",
    scenario: Scenario = "B",
    recovery_yield: Optional[float] = None,
    polymer_market_values_usd_per_mt: Optional[dict[str, float]] = None,
    solver_name: SolverName = "scip",
) -> str:
    """Select one route/residual design for cost, emissions, profit, or circularity."""
    tool = "optimize_stored_route"
    try:
        objective = _objective_key(objective)
    except ValueError:
        return tool_error(tool, "Unsupported objective.", error_code="unsupported_objective", supported_objectives=sorted(_OBJECTIVES))
    inherited_gap = _stored_point_optimization_gap(objective)
    if inherited_gap is not None:
        return inherited_gap
    try:
        scenario_key = _scenario_key(scenario)
        solver_key = _solver_key(solver_name)
        source = _source_state()
        recovery = _number(
            recovery_yield if recovery_yield is not None else
            (asset_payload().get("policy") or {}).get("default_recovery_yield"),
            "recovery_yield",
        )
        if not 0 < recovery <= 1:
            raise ValueError("recovery_yield must be above 0 and at most 1")
        values = _market_values(
            polymer_market_values_usd_per_mt, tuple(source["composition"]),
        )
        landscape, rejected = _landscape(
            source, scenario=scenario_key, recovery_yield=recovery, market_values=values,
        )
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_optimization_basis")
    if not landscape:
        return tool_error(tool, "No design has usable economics.", error_code="no_usable_designs")
    selected = _deterministic_optimum(landscape, objective)
    verification = _verify_point(landscape, objective, solver_key)
    verification["agrees_with_deterministic_optimum"] = (
        verification.get("selected_design_id") == selected["design_id"]
        if verification.get("status") == "verified" else None
    )
    return tool_success(
        tool, analysis_type="point_optimum", objective=objective,
        source_route="typed_session_state", source_route_signature=source["tea"].get("route_signature"),
        source_tea_engine_mode=source["tea"].get("engine_mode"),
        feed_mass_fractions=source["composition"], feed_mt_per_yr=source["feed_mt_per_yr"],
        scenario=scenario_key, recovery_yield=recovery,
        polymer_market_values_usd_per_mt=values,
        capital_allocation_years=int((asset_payload().get("policy") or {})["strap_capital_allocation_years"]),
        residual_product_revenue_included=False,
        selected_point=selected, n_landscape_points=len(landscape),
        n_rejected_phantom_designs=len(rejected), solver=verification,
        economics_guard="annualized CAPEX>0 AND (OPEX>0 OR GWP>0)",
        metric_units={
            "capital_cost": "USD/yr (BioSTEAM TCI allocated over 10 years)",
            "operational_cost": "USD/yr", "transportation_cost": "USD/yr",
            "total_cost": "USD/yr", "revenue": "USD/yr", "profit": "USD/yr",
            "emissions": "t CO2e/yr", "energy_mj_per_yr": "MJ/yr",
            "circularity": "mass-diversion fraction",
        },
        circularity_basis=(asset_payload().get("policy") or {}).get("circularity_note"),
        provenance={
            "asset_sha256": _ASSET_SHA256,
            **(asset_payload().get("provenance") or {}),
        },
        warnings=[
            "This is a route-constrained superstructure screen, not a final integrated-facility design.",
            "Recovery yield and market values are explicit optimization assumptions, not measured route performance.",
            "BioSTEAM TCI is allocated over 10 years without discounting; residual-technology CAPEX is sourced in USD/yr.",
            "Residual-technology product revenue is not credited in this screen.",
            "Circularity is a mass-diversion screening proxy, not a validated circularity assessment.",
        ],
    )


def pareto_optimize_stored_route(
    x_metric: Metric = "total_cost",
    y_metric: Metric = "emissions",
    scenario: Scenario = "B",
    recovery_yield: Optional[float] = None,
    polymer_market_values_usd_per_mt: Optional[dict[str, float]] = None,
    composition_slices: Optional[list[dict[str, float]]] = None,
    solver_name: SolverName = "scip",
) -> str:
    """Build a route Pareto frontier or expose an inherited candidate-basis gap."""
    tool = "pareto_optimize_stored_route"
    try:
        x_metric, y_metric = _metric_key(x_metric), _metric_key(y_metric)
        candidate_gap = _stored_optimization_gap(x_metric, y_metric)
        if candidate_gap is not None:
            return candidate_gap
        if "selectivity" in {x_metric, y_metric}:
            raise ValueError("selectivity is not a route-landscape metric")
        scenario_key = _scenario_key(scenario)
        solver_key = _solver_key(solver_name)
        source = _source_state()
        recovery = _number(
            recovery_yield if recovery_yield is not None else
            (asset_payload().get("policy") or {}).get("default_recovery_yield"),
            "recovery_yield",
        )
        if not 0 < recovery <= 1:
            raise ValueError("recovery_yield must be above 0 and at most 1")
        values = _market_values(
            polymer_market_values_usd_per_mt, tuple(source["composition"]),
        )
        slices = composition_slices or [source["composition"]]
        normalized_slices = [_composition(item) for item in slices]
        if any(set(item) != set(source["composition"]) for item in normalized_slices):
            raise ValueError("Every composition slice must cover the stored-route polymers")
        slice_payloads = []
        for index, composition in enumerate(normalized_slices, 1):
            landscape, rejected = _landscape(
                source, scenario=scenario_key, recovery_yield=recovery,
                market_values=values, composition=composition,
            )
            frontier = _pareto_sweep(landscape, x_metric, y_metric)
            if not frontier:
                continue
            cheapest = copy.deepcopy(min(frontier, key=lambda point: float(point["total_cost"])))
            knee = _knee(frontier, x_metric, y_metric)
            knee_status = (
                "interior_tradeoff"
                if len(frontier) > 2 and knee and knee.get("design_id") not in {
                    frontier[0].get("design_id"), frontier[-1].get("design_id"),
                }
                else "endpoint_only_no_interior_knee"
            )
            slice_payloads.append({
                "slice_id": f"slice-{index}", "feed_mass_fractions": composition,
                "landscape_points": landscape, "points": frontier,
                "n_landscape_points": len(landscape), "n_frontier_points": len(frontier),
                "n_rejected_phantom_designs": len(rejected),
                "knee_point": knee, "knee_status": knee_status,
                "cheapest_point": cheapest,
                "frontier_tradeoff": _cost_emissions_tradeoff(
                    frontier, x_metric, y_metric,
                ),
            })
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_pareto_basis")
    if not slice_payloads:
        return tool_error(tool, "No feasible Pareto frontier was found.", error_code="no_pareto_points")
    full = {
        "schema": "dissolve.optimization-payload.v1", "analysis_type": "pareto_slices" if len(slice_payloads) > 1 else "pareto_front",
        "x_metric": x_metric, "y_metric": y_metric, "scenario": scenario_key,
        "source_route_signature": source["tea"].get("route_signature"),
        "slices": slice_payloads,
    }
    primary = slice_payloads[0]
    objective_for_verification = {
        "total_cost": "min_cost", "emissions": "min_emissions",
        "profit": "max_profit", "circularity": "max_circularity",
    }[x_metric]
    verification = _verify_point(
        primary["landscape_points"], objective_for_verification, solver_key,
    )
    full["solver"] = verification
    full["assumptions"] = {
        "recovery_yield": recovery,
        "polymer_market_values_usd_per_mt": values,
        "capital_allocation_years": int(
            (asset_payload().get("policy") or {})["strap_capital_allocation_years"]
        ),
        "residual_product_revenue_included": False,
    }
    path = _payload_path("pareto", full)
    points = primary["points"]
    return tool_success(
        tool, display=f"Full route-constrained Pareto payload: {path}",
        artifact={"kind": "optimization_frontier", "format": "json", "title": "Route-constrained Pareto payload", "path": path},
        analysis_type=full["analysis_type"], x_metric=x_metric, y_metric=y_metric,
        source_route="typed_session_state", source_route_signature=source["tea"].get("route_signature"),
        source_tea_engine_mode=source["tea"].get("engine_mode"),
        feed_mt_per_yr=source["feed_mt_per_yr"], scenario=scenario_key,
        recovery_yield=recovery, n_slices_requested=len(normalized_slices),
        polymer_market_values_usd_per_mt=values,
        capital_allocation_years=int((asset_payload().get("policy") or {})["strap_capital_allocation_years"]),
        residual_product_revenue_included=False,
        n_slices_solved=len(slice_payloads), n_landscape_points=primary["n_landscape_points"],
        n_frontier_points=primary["n_frontier_points"], points=points,
        knee_point=primary["knee_point"], knee_status=primary["knee_status"],
        cheapest_point=primary["cheapest_point"],
        frontier_tradeoff=primary["frontier_tradeoff"],
        slices=[{
            key: item[key] for key in (
                "slice_id", "feed_mass_fractions", "n_landscape_points",
                "n_frontier_points", "knee_point", "cheapest_point",
                "knee_status", "frontier_tradeoff",
            )
        } for item in slice_payloads],
        pareto_payload_path=path, points_are_subset_of_landscape=True,
        economics_guard="annualized CAPEX>0 AND (OPEX>0 OR GWP>0)",
        metric_units={
            "capital_cost": "USD/yr (BioSTEAM TCI allocated over 10 years)",
            "operational_cost": "USD/yr", "transportation_cost": "USD/yr",
            "total_cost": "USD/yr", "revenue": "USD/yr", "profit": "USD/yr",
            "emissions": "t CO2e/yr", "energy_mj_per_yr": "MJ/yr",
            "circularity": "mass-diversion fraction",
        },
        solver=verification,
        circularity_basis=(asset_payload().get("policy") or {}).get("circularity_note"),
        sensitivity_basis=(
            "fixed stored-TEA stage economics/intensities across composition slices"
            if len(normalized_slices) > 1 else None
        ),
        provenance={"asset_sha256": _ASSET_SHA256, **(asset_payload().get("provenance") or {})},
        warnings=[
            "Frontier points are dominance-filtered from the feasible landscape; zero-capital phantom anchors are excluded.",
            "This is a route-constrained superstructure screen, not a final integrated-facility design.",
            "Recovery yield and market values are explicit assumptions, not measured route performance.",
            "BioSTEAM TCI is allocated over 10 years without discounting; residual-technology CAPEX is sourced in USD/yr.",
            "Residual-technology product revenue is not credited in this screen.",
            "Composition slices hold stored TEA stage economics/intensities fixed and are sensitivity slices, not re-simulations."
            if len(normalized_slices) > 1 else
            "Circularity is a mass-diversion screening proxy, not a validated circularity assessment.",
        ],
    )
