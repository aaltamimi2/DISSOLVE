"""Locate and integrity-bind a registered campaign. Do not ingest JSONL into cache."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from . import campaign_basis, tea
from . import thermodynamics as thermo

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
        projected = campaign_basis.project_campaign_basis_v1(run_definition)
    except campaign_basis.CampaignBasisIncomplete as error:
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


def consume_process_rows(
    path: Path,
    canonical: str,
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
) -> tuple[int, int | None]:
    wanted_polymers = {_key(item) for item in (polymers or []) if item}
    wanted_solvent = str(solvent or "").strip() or None
    filter_active = bool(wanted_polymers or wanted_solvent)
    n_rows = 0
    matched = 0
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
            if not filter_active:
                continue
            polymer = str(row.get("polymer") or "").strip()
            if wanted_polymers and _key(polymer) not in wanted_polymers:
                continue
            public_solvent = str(row.get("solvent_public_identity") or "")
            if wanted_solvent is not None and not _solvents_match(
                wanted_solvent, public_solvent,
            ):
                continue
            matched += 1
    return n_rows, (matched if filter_active else None)


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _held_mismatch_payload(
    projected: Mapping[str, Any],
    public_requested: Mapping[str, Any],
    *,
    run_definition: Mapping[str, Any],
    seconds_per_pair: float | None,
) -> dict[str, Any] | None:
    mismatch = campaign_basis.held_field_mismatches(
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


def consume_campaign_lookup(
    *,
    fingerprint: str | None,
    requested: Mapping[str, Any] | None = None,
    polymers: list[str] | None = None,
    solvent: str | None = None,
    registry_path: str | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
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
    n_rows, matching = consume_process_rows(
        bound["process_rows_path"],
        bound["canonical"],
        polymers=polymers,
        solvent=solvent,
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
    payload = {
        "source": "campaign",
        "campaign_fingerprint": bound["canonical"],
        "append_log_fingerprint": bound["append_log_fingerprint"],
        "campaign_definition_schema": campaign_basis.CAMPAIGN_DEFINITION_SCHEMA_V2,
        **bound["projected"],
        "n_rows_consumed": n_rows,
        "ingested_into_admitted_cache": False,
    }
    if matching is not None:
        payload["matching_row_count"] = matching
    return payload
