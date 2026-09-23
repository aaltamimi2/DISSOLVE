"""rank_landscape source=process_rows: usable projection, landscape AND frontier.

safety_standing is a carried axis: copy a legal status from the source row.
Do not filter usable on it. Default not_requested when nothing was bound.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import tea
from . import thermodynamics as thermo

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
    if not raw:
        return {"entries": {}, "n_registered": 0, "aliases": {}}
    registry_path = Path(raw).expanduser()
    try:
        text = registry_path.read_text(encoding="utf-8")
        parsed = json.loads(text, object_pairs_hook=_duplicate_object)
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
