"""Bounded statistics, Hansen screening, and thermal inference capabilities."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from . import thermodynamics as thermo
from .contracts import tool_error, tool_success

_ASSET = Path(__file__).with_name("data") / "analysis.json.gz"
_ASSET_SHA256 = "6fa37a21e4ff492654172883ca3a941f7820b704f899ad86839d1fe9e17ee9a0"
_HSP_ML_STEM = "corrected_Random_Forest_20251231_212903"
_HSP_ML_ASSETS = {
    f"{_HSP_ML_STEM}_model.pkl": "25b4331e2fb0bb3b6e6657b7705e61ccb4a23849fe4c282998c2e5f636d5cb6a",
    f"{_HSP_ML_STEM}_scaler.pkl": "3082771121a91cc1bf63fd5b3c5346c2380f7a3fe167009d081e991a555f9be1",
    f"{_HSP_ML_STEM}_metadata.json": "82b543f705eba9fa7f5cc2f8d96b67fe7019e8ed4fc966d19156a73e94234ca5",
}
_MAX_HSP_PAIRS = 400
_MAX_NUMERIC_SERIES = 20
_MAX_SAMPLES = 500

# Evidence keys deliberately preserve parenthetical content, signs, percentages,
# and quality marks. They case-fold, normalize whitespace/degree notation, and
# remove only other separators. Identity aliases are resolved centrally in
# thermodynamics; this key is only a collision-checked last resort for raw labels.
HSP_EVIDENCE_LABEL_NORMALIZER = (
    "casefold; degree->word; preserve (), +, -, %, and ?; "
    "other non-alphanumerics->space; collapse whitespace"
)


@lru_cache(maxsize=1)
def asset_payload() -> dict[str, Any]:
    if hashlib.sha256(_ASSET.read_bytes()).hexdigest() != _ASSET_SHA256:
        raise RuntimeError("Analysis evidence asset checksum mismatch")
    with gzip.open(_ASSET, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema") != "dissolve.analysis-asset.v2":
        raise RuntimeError("Unsupported analysis evidence schema")
    return payload


def _key(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("°", " degree ")
    text = re.sub(r"[^0-9a-z%?()+-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _category_key(value: Any) -> str:
    """Normalize controlled category vocabulary, never source identities."""
    text = str(value or "").strip().casefold().replace("°", " ")
    return re.sub(r"\s+", " ", re.sub(r"[_/\\-]+", " ", text)).strip(" .,;:")


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
        value = parsed
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    if not rows:
        return ""
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(str(value) for value in row) + " |" for row in rows),
    ])


def _entry_keys(entry: dict[str, Any]) -> set[str]:
    return {
        _key(value) for value in (
            entry.get("id"), entry.get("display_name"), entry.get("raw_hsp_name"),
            *(entry.get("aliases") or []),
        ) if value
    }


@lru_cache(maxsize=2)
def _source_lookup(kind: Literal["polymer", "solvent"]) -> dict[int, dict[str, Any]]:
    return {
        int(item["source_record_id"]): item
        for item in asset_payload()["hsp"][f"{kind}_source_records"]
    }


def _source_entry(
    record: dict[str, Any], kind: Literal["polymer", "solvent"], *,
    curated: dict[str, Any] | None = None,
    family_id: str | None = None,
    family_label: str | None = None,
    relationship: str | None = None,
    summary_tier: str | None = None,
) -> dict[str, Any]:
    source_id = int(record["source_record_id"])
    audit = record.get("audit") or {}
    review_status = str(audit.get("review_status") or "unreviewed")
    conflicted = list(audit.get("conflicting_source_records") or [])
    primary = curated.get("primary_source_id") if curated else None
    is_primary = primary == source_id
    display = str(curated.get("display_name") if curated else record["raw_label"])
    if conflicted and (curated is None or not is_primary):
        display = f"{display} [source {source_id}]"
    quality = str(
        curated.get("quality") if curated else
        f"reviewed_{relationship}" if relationship and review_status == "reviewed" else
        "unreviewed_raw"
    )
    if audit.get("numeric_status") == "nonphysical_fit":
        quality = "nonphysical_fit"
    elif not curated and any(
        audit.get(key) for key in (
            "percentage_condition", "time_condition", "temperature_condition",
            "pressure_condition", "solution_or_solubility", "fit_or_quality_text",
        )
    ):
        quality = "qualified_source"
    qualifiers = []
    if curated and curated.get("qualifier"):
        qualifiers.append(str(curated["qualifier"]))
    if conflicted:
        qualifiers.append(
            "The source label has conflicting parameter records: "
            + ", ".join(str(item) for item in conflicted) + "."
        )
    if audit.get("negative_components"):
        qualifiers.append(
            "Negative fitted Hansen component(s): "
            + ", ".join(audit["negative_components"])
            + "; retain as a visibly nonphysical/unconstrained raw fit."
        )
    if audit.get("question_mark"):
        qualifiers.append("The raw source label carries a question-mark quality flag.")
    warnings = list(curated.get("warnings") or []) if curated else []
    warnings.extend(qualifiers)
    hsp = {
        key: record[key] for key in (
            "dispersion", "polar", "hydrogen_bonding", "interaction_radius",
            "molar_volume",
        ) if key in record
    }
    headline = bool(
        (summary_tier == "headline" or (curated and curated.get("default_include")))
        and audit.get("numeric_status") != "nonphysical_fit"
    )
    return {
        "id": (
            str(curated["id"]) if curated and (is_primary or len(curated["source_record_ids"]) == 1)
            else f"{kind}:source:{source_id}"
        ),
        "kind": kind, "display_name": display,
        "canonical_display_name": str(
            curated.get("display_name") if curated else record["raw_label"]
        ),
        "raw_hsp_name": record["raw_label"],
        "raw_label": record["raw_label"],
        "aliases": list(curated.get("aliases") or []) if curated else [],
        "quality": quality, "qualifier": " ".join(qualifiers) or None,
        "review_status": review_status,
        "default_include": headline, "headline_eligible": headline,
        "warnings": list(dict.fromkeys(warnings)), "hsp": hsp,
        "source_record_id": source_id,
        "source_record_ids": list(curated.get("source_record_ids") or [source_id]) if curated else [source_id],
        "source_variant_ids": conflicted,
        "audit": audit, "family_id": family_id, "family_label": family_label,
        "relationship": relationship, "summary_tier": summary_tier,
    }


def _curated_source_entries(
    item: dict[str, Any], kind: Literal["polymer", "solvent"],
) -> list[dict[str, Any]]:
    lookup = _source_lookup(kind)
    ids = list(item.get("source_record_ids") or [])
    primary = item.get("primary_source_id")
    ids.sort(key=lambda value: (value != primary, value))
    return [_source_entry(lookup[int(source_id)], kind, curated=item) for source_id in ids]


def _family_entries(family_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    family = asset_payload()["hsp"]["families"].get(family_id)
    if not family:
        return [], {"reason": f"no reviewed HSP family view for {family_id}"}
    lookup = _source_lookup("polymer")
    entries = [
        _source_entry(
            lookup[int(edge["source_record_id"])], "polymer",
            family_id=family_id, family_label=family["label"],
            relationship=edge["relationship"], summary_tier=edge["summary_tier"],
        )
        for edge in family["edges"]
    ]
    return entries, {
        "family_id": family_id, "family_label": family["label"],
        "source_record_count": len(entries),
    }


def _resolve_hsp(
    query: str, kind: Literal["polymer", "solvent"], *, include_excluded: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    section = asset_payload()["hsp"]
    normalized = _key(query)
    category_key = _category_key(query)
    entries = section[f"curated_{kind}s"]
    unsupported_key = next((
        key for key in (str(query).strip().casefold(), category_key)
        if key in section["unsupported_solvent_aliases"]
    ), None) if kind == "solvent" else None
    if unsupported_key:
        return [], {
            "query": query,
            "reason": section["unsupported_solvent_aliases"][unsupported_key],
        }
    if kind == "polymer":
        family_id = thermo.hsp_family_for_polymer_query(query)
        if family_id:
            family_entries, context = _family_entries(family_id)
            return family_entries, {"query": query, **(context or {})}
    category_sets = []
    if kind == "polymer":
        category_sets.append(section["polymer_property_groups"])
    else:
        category_sets.extend([section["solvent_categories"], section["solvent_polarities"]])
    for categories in category_sets:
        if category_key in categories:
            category = categories[category_key]
            by_id = {entry["id"]: entry for entry in entries}
            members = [by_id[item] for item in category["members"]]
            selected = members if include_excluded else [
                item for item in members if item["default_include"]
            ]
            expanded = [entry for item in selected for entry in _curated_source_entries(item, kind)]
            excluded = [item["display_name"] for item in members if item not in selected]
            return expanded, {
                "query": query, "category_id": category["id"],
                "category_label": category["label"], "excluded": excluded,
            }
    query_keys = {normalized}
    if kind == "polymer":
        identity = thermo.resolve_polymer_identity(query)
        if identity:
            query_keys.update(_key(item) for item in thermo.polymer_identity_labels(identity))
    else:
        labels = thermo.solvent_identity_labels(query)
        query_keys.update(_key(item) for item in labels)
    matches = [entry for entry in entries if query_keys & _entry_keys(entry)]
    if matches:
        expanded = [entry for item in matches for entry in _curated_source_entries(item, kind)]
        return expanded, {
            "query": query,
            "curated_mappings": [item["id"] for item in matches],
            "source_record_count": len(expanded),
        }
    raw = section[f"{kind}_source_records"]
    exact_matches = [
        item for item in raw
        if str(item["raw_label"]).strip().casefold() == str(query).strip().casefold()
    ]
    if exact_matches:
        return [_source_entry(item, kind) for item in exact_matches], {
            "query": query, "source_record_count": len(exact_matches),
            "resolution": "exact_raw_label",
        }
    source_query_keys = query_keys if kind == "solvent" else {normalized}
    normalized_matches = [
        item for item in raw if _key(item["raw_label"]) in source_query_keys
    ]
    labels = {str(item["raw_label"]) for item in normalized_matches}
    if len(labels) == 1 and normalized_matches:
        return [_source_entry(item, kind) for item in normalized_matches], {
            "query": query, "source_record_count": len(normalized_matches),
            "resolution": "collision-checked_normalized_label",
        }
    if normalized_matches:
        return [], {
            "query": query, "reason": "normalized label is ambiguous",
            "matches": sorted(labels),
        }
    return [], {"query": query, "reason": f"no {kind} HSP record"}


def _resolve_many(
    values: Any, kind: Literal["polymer", "solvent"], include_excluded: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in _items(values):
        matches, issue = _resolve_hsp(value, kind, include_excluded=include_excluded)
        if issue and not matches:
            issues.append(issue)
        for item in matches:
            identity = f"{kind}:{item.get('source_record_id', item['id'])}"
            if identity not in seen:
                resolved.append(item)
                seen.add(identity)
    return resolved, issues


def _hsp_values(entry: dict[str, Any]) -> dict[str, float]:
    raw = entry["hsp"]
    return {
        "dispersion": float(raw["dispersion"]),
        "polar": float(raw["polar"]),
        "hydrogen_bonding": float(raw["hydrogen_bonding"]),
    }


def _parameter_ranges(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for key in ("dispersion", "polar", "hydrogen_bonding", "interaction_radius"):
        values = [float(item["hsp"][key]) for item in entries if item["hsp"].get(key) is not None]
        if not values:
            continue
        low, high = min(values), max(values)
        spread = high - low
        result[key] = {
            "minimum": round(low, 6), "maximum": round(high, 6),
            "spread": round(spread, 6),
            "numerically_narrow_under_2_mpa_sqrt_rule": spread <= 2.0,
        }
    return result


def _family_summaries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    family_ids = list(dict.fromkeys(
        str(item["family_id"]) for item in entries if item.get("family_id")
    ))
    for family_id in family_ids:
        members = [item for item in entries if item.get("family_id") == family_id]
        relationships = list(dict.fromkeys(str(item.get("relationship")) for item in members))
        groups = []
        for relationship in relationships:
            group = [item for item in members if item.get("relationship") == relationship]
            groups.append({
                "relationship": relationship,
                "source_record_count": len(group),
                "unique_label_count": len({item["raw_label"] for item in group}),
                "evidence_group_count": len({
                    (item.get("audit") or {}).get("evidence_group_id")
                    or f"source:{item['source_record_id']}"
                    for item in group
                }),
                "source_record_ids": [item["source_record_id"] for item in group],
                "parameter_ranges": _parameter_ranges(group),
            })
        headline = [item for item in members if item.get("headline_eligible")]
        conflicts = []
        for label in dict.fromkeys(
            str(item["raw_label"]) for item in members if item.get("source_variant_ids")
        ):
            variants = [item for item in members if item["raw_label"] == label]
            conflicts.append({
                "raw_label": label,
                "source_record_ids": sorted({
                    source_id for item in variants for source_id in item["source_variant_ids"]
                }),
                "parameter_ranges": _parameter_ranges(variants),
            })
        summaries.append({
            "family_id": family_id, "family_label": members[0].get("family_label"),
            "source_record_count": len(members),
            "unique_label_count": len({item["raw_label"] for item in members}),
            "headline_source_record_count": len(headline),
            "headline_parameter_ranges": _parameter_ranges(headline),
            "all_returned_parameter_ranges": _parameter_ranges(members),
            "groups": groups, "conflicting_labels": conflicts,
            "nonphysical_source_record_ids": [
                item["source_record_id"] for item in members
                if (item.get("audit") or {}).get("numeric_status") == "nonphysical_fit"
            ],
            "unreviewed_source_record_ids": [
                item["source_record_id"] for item in members
                if item.get("review_status") != "reviewed"
            ],
            "interpretation": (
                "Core controls the concise headline only; every returned tier remains available. "
                "Numerical-width flags are descriptive and are not experimental agreement."
            ),
        })
    return summaries


def _family_red_summaries(
    entries: list[dict[str, Any]], rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summaries = []
    for family in _family_summaries(entries):
        family_id = family["family_id"]
        source_ids = {
            int(item["source_record_id"])
            for item in entries if item.get("family_id") == family_id
        }
        family_rows = [row for row in rows if row.get("polymer_source_record_id") in source_ids]
        by_solvent: dict[str, list[dict[str, Any]]] = {}
        for row in family_rows:
            by_solvent.setdefault(str(row["solvent_canonical_label"]), []).append(row)
        solvent_summaries = []
        for solvent, solvent_rows in by_solvent.items():
            finite = [row for row in solvent_rows if isinstance(row.get("red"), (int, float))]
            if not finite:
                continue
            eligible = [
                row for row in finite
                if row.get("polymer_numeric_status") != "nonphysical_fit"
            ]
            ranked = eligible or finite
            closest = min(ranked, key=lambda item: float(item["red"]))
            tier_matches = []
            relationship_ranges = []
            for relationship in dict.fromkeys(
                str(row.get("polymer_relationship") or "unclassified")
                for row in ranked
            ):
                tier_rows = [
                    row for row in ranked
                    if str(row.get("polymer_relationship") or "unclassified") == relationship
                ]
                tier_closest = min(tier_rows, key=lambda item: float(item["red"]))
                relationship_ranges.append({
                    "relationship": relationship,
                    "source_record_count": len(tier_rows),
                    "red_minimum": min(float(item["red"]) for item in tier_rows),
                    "red_maximum": max(float(item["red"]) for item in tier_rows),
                    "inside_sphere_count": sum(
                        item.get("inside_hansen_sphere") is True for item in tier_rows
                    ),
                })
                tier_matches.append({
                    "relationship": relationship,
                    "polymer": tier_closest["polymer"],
                    "polymer_source_record_id": tier_closest["polymer_source_record_id"],
                    "solvent_source_record_id": tier_closest["solvent_source_record_id"],
                    "red": tier_closest["red"],
                })
            solvent_summaries.append({
                "solvent": solvent,
                "solvent_source_record_ids": sorted({
                    int(item["solvent_source_record_id"]) for item in finite
                }),
                "polymer_source_record_count": len(finite),
                "red_minimum": min(float(item["red"]) for item in finite),
                "red_maximum": max(float(item["red"]) for item in finite),
                "inside_sphere_count": sum(item.get("inside_hansen_sphere") is True for item in finite),
                "nonphysical_record_count": len(finite) - len(eligible),
                "closest_screened_member": {
                    "polymer": closest["polymer"],
                    "polymer_source_record_id": closest["polymer_source_record_id"],
                    "solvent_source_record_id": closest["solvent_source_record_id"],
                    "relationship": closest.get("polymer_relationship"),
                    "red": closest["red"],
                },
                "closest_screened_members_by_relationship": tier_matches,
                "relationship_red_ranges": relationship_ranges,
            })
        summaries.append({
            "family_id": family_id, "family_label": family["family_label"],
            "solvents": solvent_summaries,
            "interpretation": (
                "The closest screened member is solvent-specific and is not a family value or process recommendation."
            ),
        })
    return summaries


def _hsp_red_rows(
    polymers: list[dict[str, Any]], solvents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for polymer in polymers:
        p = _hsp_values(polymer)
        radius = float(polymer["hsp"]["interaction_radius"])
        for solvent in solvents:
            s = _hsp_values(solvent)
            ra = math.sqrt(
                4 * (p["dispersion"] - s["dispersion"]) ** 2
                + (p["polar"] - s["polar"]) ** 2
                + (p["hydrogen_bonding"] - s["hydrogen_bonding"]) ** 2
            )
            red = ra / radius if radius > 0 else None
            rows.append({
                "polymer": polymer["display_name"], "polymer_id": polymer["id"],
                "polymer_source_label": polymer["raw_label"],
                "polymer_source_record_id": polymer["source_record_id"],
                "polymer_source_variant_ids": polymer.get("source_variant_ids") or [],
                "polymer_family_id": polymer.get("family_id"),
                "polymer_relationship": polymer.get("relationship"),
                "polymer_summary_tier": polymer.get("summary_tier"),
                "solvent": solvent["display_name"], "solvent_id": solvent["id"],
                "solvent_canonical_label": solvent["canonical_display_name"],
                "solvent_source_label": solvent["raw_label"],
                "solvent_source_record_id": solvent["source_record_id"],
                "solvent_source_variant_ids": solvent.get("source_variant_ids") or [],
                "ra_mpa_sqrt": round(ra, 6), "red": None if red is None else round(red, 6),
                "inside_hansen_sphere": None if red is None else red <= 1.0,
                "polymer_hsp": p, "solvent_hsp": s, "interaction_radius": radius,
                "solvent_molar_volume": float(solvent["hsp"].get("molar_volume", 100.0)),
                "polymer_record_quality": polymer.get("quality", "canonical"),
                "polymer_review_status": polymer.get("review_status", "unreviewed"),
                "polymer_numeric_status": (polymer.get("audit") or {}).get("numeric_status"),
                "polymer_qualifier": polymer.get("qualifier"),
                "solvent_record_quality": solvent.get("quality", "canonical"),
                "solvent_review_status": solvent.get("review_status", "unreviewed"),
                "solvent_qualifier": solvent.get("qualifier"),
                "polymer_headline_eligible": polymer.get("headline_eligible", False),
                "warnings": [*(polymer.get("warnings") or []), *(solvent.get("warnings") or [])],
            })
    rows.sort(key=lambda row: (row["polymer"], float(row["red"] or math.inf), row["solvent"]))
    return rows


@lru_cache(maxsize=1)
def _hsp_ml_assets() -> tuple[Any, Any, dict[str, Any]]:
    """Load the checksummed v10 Hansen Random Forest and its exact scaler."""
    import joblib

    data_dir = Path(__file__).with_name("data")
    paths = {name: data_dir / name for name in _HSP_ML_ASSETS}
    for name, expected in _HSP_ML_ASSETS.items():
        if hashlib.sha256(paths[name].read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"HSP ML asset checksum mismatch: {name}")
    metadata = json.loads(paths[f"{_HSP_ML_STEM}_metadata.json"].read_text())
    if metadata.get("num_features") != 10 or metadata.get("target_definition") != "RED < 1.0 = Soluble":
        raise RuntimeError("Unsupported HSP ML metadata contract")
    return (
        joblib.load(paths[f"{_HSP_ML_STEM}_model.pkl"]),
        joblib.load(paths[f"{_HSP_ML_STEM}_scaler.pkl"]),
        metadata,
    )


def _hsp_ml_prediction(row: dict[str, Any]) -> dict[str, Any]:
    model, scaler, metadata = _hsp_ml_assets()
    polymer = row["polymer_hsp"]
    solvent = row["solvent_hsp"]
    features = [[
        polymer["dispersion"], polymer["polar"], polymer["hydrogen_bonding"],
        solvent["dispersion"], solvent["polar"], solvent["hydrogen_bonding"],
        row["solvent_molar_volume"], row["interaction_radius"],
        row["ra_mpa_sqrt"], row["red"],
    ]]
    probability = float(model.predict_proba(scaler.transform(features))[0, 1])
    threshold = float(metadata["classification_threshold"])
    test_metrics = {
        str(key): float(value)
        for key, value in (metadata.get("performance") or {}).items()
        if isinstance(value, (int, float))
    }
    perfect_test_metrics = bool(test_metrics) and all(
        value == 1.0 for value in test_metrics.values()
    )
    return {
        "predicted_class": "soluble" if probability >= threshold else "non-soluble",
        "probability_soluble": round(probability, 6),
        "probability_soluble_pct": round(probability * 100.0, 4),
        "classification_threshold": threshold,
        "decision_margin": round(abs(probability - threshold), 6),
        "model_type": metadata["model_type"],
        "training_strategy": metadata["training_strategy"],
        "target_definition": metadata["target_definition"],
        "reported_test_metrics": test_metrics,
        "perfect_test_metrics_reported": perfect_test_metrics,
        "validation_caveat": (
            "The bundled metadata reports perfect test metrics; these are not "
            "independent external validation and should be treated cautiously."
            if perfect_test_metrics else
            "Bundled test metrics are not independent external validation."
        ),
        "model_asset_sha256": _HSP_ML_ASSETS[f"{_HSP_ML_STEM}_model.pkl"],
    }


def _hsp_fallback_evidence_class(status: str, rows: list[Any]) -> str:
    """Name the forest only when it produced rows; otherwise name the refusal."""
    if rows:
        return "hsp_random_forest_fallback"
    if status == "thermodynamic_coverage_available":
        return "hsp_fallback_not_applicable"
    return "hsp_fallback_unavailable"


def hsp_fallback_evidence(
    polymer_name: str, solvent_names: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve bounded HSP evidence only after thermodynamic coverage is absent."""
    if thermo.expand_polymer_identity(polymer_name):
        status = "thermodynamic_coverage_available"
        rows: list[Any] = []
        return {
            "requested_polymer": polymer_name,
            "canonical_identity": thermo.resolve_polymer_identity(polymer_name),
            "status": status,
            "records": [], "rows": rows, "resolution_issues": [],
            "temperature_dependent": False,
            "evidence_class": _hsp_fallback_evidence_class(status, rows),
        }
    polymers, polymer_issue = _resolve_hsp(polymer_name, "polymer")
    identity = thermo.resolve_polymer_identity(polymer_name)
    result: dict[str, Any] = {
        "requested_polymer": polymer_name,
        "canonical_identity": identity,
        "temperature_dependent": False,
        "fallback_reason": "temperature-dependent thermodynamic coverage unavailable",
    }
    if not polymers:
        status = "no_hsp_record"
        rows = []
        return {
            **result, "status": status, "records": [], "rows": rows,
            "resolution_issues": [polymer_issue] if polymer_issue else [],
            "evidence_class": _hsp_fallback_evidence_class(status, rows),
        }
    result["records"] = [{
        "material": item["display_name"],
        "source_record_id": item["source_record_id"],
        "source_label": item["raw_label"],
        "quality": item.get("quality", "canonical"),
        "review_status": item.get("review_status", "unreviewed"),
        "numeric_status": (item.get("audit") or {}).get("numeric_status"),
        "qualifier": item.get("qualifier"),
        "family_id": item.get("family_id"),
        "relationship": item.get("relationship"),
        "warnings": item.get("warnings") or [],
    } for item in polymers]
    result["family_summaries"] = _family_summaries(polymers)
    requested_solvents = [str(item).strip() for item in solvent_names or [] if str(item).strip()]
    if not requested_solvents:
        status = "hsp_record_available"
        rows = []
        return {
            **result, "status": status, "rows": rows, "resolution_issues": [],
            "evidence_class": _hsp_fallback_evidence_class(status, rows),
        }
    solvents, solvent_issues = _resolve_many(requested_solvents, "solvent", False)
    raw_rows = _hsp_red_rows(polymers, solvents) if solvents else []
    rows = [{
        **{
            key: row[key] for key in (
            "polymer", "solvent", "ra_mpa_sqrt", "red", "inside_hansen_sphere",
            "polymer_record_quality", "polymer_review_status",
            "polymer_numeric_status", "polymer_qualifier",
            "solvent_record_quality", "solvent_review_status",
            "solvent_qualifier", "warnings",
            ) if row.get(key) is not None
        },
        "hsp_ml_prediction": _hsp_ml_prediction(row),
    } for row in raw_rows]
    status = "hsp_ml_prediction_available" if rows else "hsp_solvent_record_unavailable"
    return {
        **result,
        "status": status,
        "rows": rows, "resolution_issues": solvent_issues,
        "family_red_summaries": _family_red_summaries(polymers, raw_rows),
        "evidence_class": _hsp_fallback_evidence_class(status, rows),
    }


def _hsp_polymer_library_identities() -> tuple[str, ...]:
    """Return recognised identities that resolve to at least one HSP record."""
    library = []
    for identity in thermo.POLYMER_IDENTITIES:
        matches, _issue = _resolve_hsp(identity, "polymer")
        if matches:
            library.append(identity)
    return tuple(library)


def lookup_hansen_parameters(
    material_names: list[str], material_type: Literal["polymer", "solvent"],
    include_qualified_records: bool = False,
) -> str:
    """Look up curated Hansen parameters without predicting temperature behavior.

    An empty material_names list with material_type=polymer returns the HSP
    polymer library roster. That set is not the thermodynamic grid polymer
    roster; report both when both are relevant and never merge them.
    """
    tool = "lookup_hansen_parameters"
    kind = str(material_type or "").casefold()
    if kind not in {"polymer", "solvent"}:
        return tool_error(tool, "material_type must be polymer or solvent.")
    requested = _items(material_names)
    if not requested:
        if kind != "polymer":
            return tool_error(
                tool, "No unambiguous HSP identities were resolved.",
                error_code="hsp_resolution_failed",
            )
        library = _hsp_polymer_library_identities()
        thermodynamic = tuple(sorted(thermo.get_available_polymers()))
        thermo_set = set(thermodynamic)
        hsp_only = tuple(item for item in library if item not in thermo_set)
        no_hsp = tuple(
            item for item in thermo.POLYMER_IDENTITIES if item not in set(library)
        )
        return tool_success(
            tool,
            display=_table(
                ("HSP polymer library",),
                [(item,) for item in library],
            ),
            analysis_type="hsp_polymer_library",
            material_type=kind,
            hsp_polymer_library=list(library),
            hsp_polymer_library_count=len(library),
            available_thermodynamic_polymers=list(thermodynamic),
            thermodynamic_polymer_count=len(thermodynamic),
            polymers_in_hsp_not_thermodynamic=list(hsp_only),
            no_hsp_record_polymers=list(no_hsp),
            source_record_count=len(library),
            unique_label_count=len(library),
            temperature_dependent=False,
            evidence_class="qualitative_hansen_parameters",
            provenance=asset_payload()["provenance"],
            warnings=[
                "The HSP polymer library and the thermodynamic grid polymer set are different collections; do not merge them under one heading.",
                "Hansen parameters are temperature-independent qualitative screening evidence, not wt% solubility.",
            ],
        )
    entries, issues = _resolve_many(material_names, kind, include_qualified_records)
    if not entries:
        return tool_error(
            tool, "No unambiguous HSP identities were resolved.",
            error_code="hsp_resolution_failed", resolution_issues=issues,
        )
    rows = [{
        "material": entry["display_name"], "material_type": kind,
        "source_label": entry["raw_label"],
        "source_record_id": entry["source_record_id"],
        **_hsp_values(entry),
        "interaction_radius": (
            float(entry["hsp"]["interaction_radius"]) if kind == "polymer" else None
        ),
        "quality": entry.get("quality", "canonical"),
        "review_status": entry.get("review_status", "unreviewed"),
        "numeric_status": (entry.get("audit") or {}).get("numeric_status"),
        "qualifier": entry.get("qualifier"), "warnings": entry.get("warnings") or [],
        "family_id": entry.get("family_id"),
        "family_label": entry.get("family_label"),
        "relationship": entry.get("relationship"),
        "summary_tier": entry.get("summary_tier"),
        "headline_eligible": entry.get("headline_eligible", False),
        "conflicting_source_records": entry.get("source_variant_ids") or [],
    } for entry in entries]
    family_summaries = _family_summaries(entries) if kind == "polymer" else []
    return tool_success(
        tool,
        display=_table(
            ("Material", "dD", "dP", "dH", "R0", "Record"),
            [(row["material"], row["dispersion"], row["polar"],
              row["hydrogen_bonding"], row["interaction_radius"] or "—",
              row["quality"]) for row in rows],
        ),
        analysis_type="hsp_family_lookup" if family_summaries else "hsp_lookup",
        material_type=kind, rows=rows, family_summaries=family_summaries,
        source_record_count=len(rows), unique_label_count=len({row["source_label"] for row in rows}),
        resolution_issues=issues,
        hsp_unit="MPa^0.5", temperature_dependent=False,
        evidence_class="qualitative_hansen_parameters",
        provenance=asset_payload()["provenance"],
        warnings=[
            "Hansen parameters are temperature-independent qualitative screening evidence, not wt% solubility.",
            "Qualified, proxy, conditioned, or raw records may not represent a generic polymer grade.",
        ],
    )


def screen_hansen_compatibility(
    polymer_names: list[str], solvent_names: list[str],
    include_qualified_records: bool = False,
    temperature_c: Optional[float] = None,
) -> str:
    """Compute RED, optionally joined to thermodynamics at one temperature."""
    tool = "screen_hansen_compatibility"
    fitted_temperature = None
    if temperature_c is not None:
        try:
            fitted_temperature = float(temperature_c)
        except (TypeError, ValueError):
            return tool_error(
                tool, "temperature_c must be finite when supplied.",
                error_code="invalid_temperature",
            )
        if (
            not math.isfinite(fitted_temperature)
            or fitted_temperature < thermo.FITTED_TEMP_MIN_C
            or fitted_temperature > thermo.SENSITIVITY_EXTRAPOLATION_MAX_C
        ):
            return tool_error(
                tool,
                f"temperature_c must be between {thermo.FITTED_TEMP_MIN_C:g} and "
                f"{thermo.SENSITIVITY_EXTRAPOLATION_MAX_C:g} C for the admitted thermodynamic comparison.",
                error_code="invalid_temperature",
            )
    polymers, polymer_issues = _resolve_many(
        polymer_names, "polymer", include_qualified_records,
    )
    solvents, solvent_issues = _resolve_many(
        solvent_names, "solvent", include_qualified_records,
    )
    issues = [*polymer_issues, *solvent_issues]
    if not polymers or not solvents:
        return tool_error(
            tool, "At least one unambiguous polymer and solvent are required.",
            error_code="hsp_resolution_failed", resolution_issues=issues,
        )
    # H8: this bounded pair contract replaces the pre-bridge entries[:20]
    # lookup slice at analysis.py:336; no resolved source record is silently lost.
    requested_pairs = len(polymers) * len(solvents)
    if requested_pairs > _MAX_HSP_PAIRS:
        return tool_error(
            tool,
            f"The resolved HSP request contains {requested_pairs} source-record pairs, "
            f"above the explicit {_MAX_HSP_PAIRS}-pair budget. Narrow or page the request; no records were silently omitted.",
            error_code="hsp_pair_budget_exceeded",
            resolved_polymer_source_records=len(polymers),
            resolved_solvent_source_records=len(solvents),
            requested_pair_count=requested_pairs, maximum_pair_count=_MAX_HSP_PAIRS,
            omitted_pair_count=requested_pairs,
        )
    rows = _hsp_red_rows(polymers, solvents)
    joined_rows = []
    if fitted_temperature is not None:
        for row in rows:
            thermodynamic_polymer = thermo.resolve_polymer(str(row["polymer_id"]))
            thermodynamic_solvent = thermo.resolve_solvent(str(row["solvent"]))
            thermodynamic_result = (
                thermo.get_solubility_result(
                    thermodynamic_polymer, thermodynamic_solvent,
                    fitted_temperature,
                )
                if thermodynamic_polymer and thermodynamic_solvent else {}
            )
            fitted = thermodynamic_result.get("solubility_pct")
            joined_rows.append({
                **row,
                "thermodynamic_polymer": thermodynamic_polymer,
                "thermodynamic_solvent": (
                    thermo.canonical_solvent_name(thermodynamic_solvent)
                    if thermodynamic_solvent else None
                ),
                "temperature_c": fitted_temperature,
                "fitted_solubility_wt_pct": fitted,
                "fitted_evidence_available": fitted is not None,
                "source_grid_temperature_range_c": thermodynamic_result.get(
                    "source_grid_temperature_range_c",
                ),
            })
    family_summaries = _family_summaries(polymers)
    family_red_summaries = _family_red_summaries(polymers, rows)
    leading_matches = []
    if family_summaries:
        for family in family_red_summaries:
            for solvent in family["solvents"]:
                for closest in solvent["closest_screened_members_by_relationship"]:
                    match = next(
                        row for row in rows
                        if row["polymer_source_record_id"] == closest["polymer_source_record_id"]
                        and row["solvent_source_record_id"] == closest["solvent_source_record_id"]
                    )
                    key = (
                        match["polymer_source_record_id"],
                        match["solvent_source_record_id"],
                    )
                    if key not in {
                        (item["polymer_source_record_id"], item["solvent_source_record_id"])
                        for item in leading_matches
                    }:
                        leading_matches.append(match)
    else:
        for polymer in polymers:
            leading_matches.extend([
                row for row in rows if row["polymer_source_record_id"] == polymer["source_record_id"]
            ][:3])
    return tool_success(
        tool,
        display=_table(
            (
                ("Polymer", "Solvent", "RED", f"Thermo wt% @ {fitted_temperature:g} C")
                if fitted_temperature is not None
                else ("Polymer", "Solvent", "Ra", "RED", "Hansen sphere")
            ),
            (
                [(row["polymer"], row["solvent"],
                  f"{row['red']:.4g}" if row["red"] is not None else "—",
                  f"{row['fitted_solubility_wt_pct']:.5g}"
                  if row["fitted_solubility_wt_pct"] is not None else "unavailable")
                 for row in joined_rows]
                if fitted_temperature is not None
                else [(row["polymer"], row["solvent"], f"{row['ra_mpa_sqrt']:.4g}",
                       f"{row['red']:.4g}" if row["red"] is not None else "—",
                       "inside" if row["inside_hansen_sphere"] else "outside") for row in rows]
            ),
        ),
        analysis_type=(
            "hsp_thermodynamic_comparison"
            if fitted_temperature is not None else "hsp_red_matrix"
        ), polymers=[item["display_name"] for item in polymers],
        solvents=[item["display_name"] for item in solvents], rows=rows,
        joined_rows=joined_rows,
        joined_pair_count=len(joined_rows),
        fitted_temperature_c=fitted_temperature,
        fitted_evidence_class=(
            "temperature_dependent_stored_grid_thermodynamics"
            if fitted_temperature is not None else None
        ),
        polymer_source_record_ids=[item["source_record_id"] for item in polymers],
        solvent_source_record_ids=[item["source_record_id"] for item in solvents],
        family_summaries=family_summaries,
        family_red_summaries=family_red_summaries,
        leading_matches=leading_matches,
        leading_match_policy=(
            "closest eligible source record per relationship and solvent"
            if family_summaries else "up to three closest solvents per source record"
        ),
        leading_matches_total=len(leading_matches),
        n_pairs=len(rows), requested_pair_count=requested_pairs,
        omitted_pair_count=0, resolution_issues=issues,
        artifact={
            "kind": (
                "hsp_thermodynamic_comparison"
                if fitted_temperature is not None else "hsp_red_matrix"
            ),
            "format": "text",
            "title": (
                "Hansen RED and stored-grid solubility comparison"
                if fitted_temperature is not None else "Hansen RED matrix"
            ),
        },
        red_threshold=1.0, temperature_dependent=False,
        evidence_class="qualitative_hansen_red_screen",
        provenance=asset_payload()["provenance"],
        warnings=[
            "RED is a temperature-independent qualitative compatibility screen, not wt% solubility or recovery.",
            "Inside/outside the Hansen sphere is not proof of solubility or insolubility.",
            "RED does not model crystallinity, kinetics, molecular weight, concentration, or process temperature.",
            "Use the temperature-dependent stored-grid thermodynamics engine for process-condition decisions.",
            *(
                ["Joined rows preserve both evidence types; neither validates or replaces the other."]
                if fitted_temperature is not None else []
            ),
        ],
    )


def _numbers(values: Any, label: str) -> list[float]:
    if not isinstance(values, list) or not 2 <= len(values) <= _MAX_SAMPLES:
        raise ValueError(f"Series {label!r} must contain 2 to {_MAX_SAMPLES} values")
    result = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError(f"Series {label!r} contains a non-numeric value")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Series {label!r} contains a non-finite value")
        result.append(number)
    return result


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average = (index + 1 + end) / 2
        for position in range(index, end):
            ranks[ordered[position][0]] = average
        index = end
    return ranks


def _correlation(x: list[float], y: list[float]) -> float:
    x_mean, y_mean = statistics.fmean(x), statistics.fmean(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    denominator = math.sqrt(
        sum((a - x_mean) ** 2 for a in x) * sum((b - y_mean) ** 2 for b in y)
    )
    if denominator == 0:
        raise ValueError("Correlation is undefined for a constant series")
    return numerator / denominator


def _scipy_stats() -> Any | None:
    try:
        from scipy import stats
        return stats
    except ImportError:
        return None


def analyze_numeric_samples(
    series: dict[str, list[float]],
    analysis_type: Literal["summary", "correlation", "group_comparison", "regression"] = "summary",
    x_name: Optional[str] = None, y_name: Optional[str] = None,
    correlation_method: Literal["pearson", "spearman"] = "pearson",
    confidence_level: float = 0.95,
) -> str:
    """Analyze explicit numeric samples; never execute database names or SQL."""
    tool = "analyze_numeric_samples"
    if not isinstance(series, dict) or not 1 <= len(series) <= _MAX_NUMERIC_SERIES:
        return tool_error(tool, f"Provide 1 to {_MAX_NUMERIC_SERIES} named numeric series.")
    try:
        data = {str(name): _numbers(values, str(name)) for name, values in series.items()}
        confidence = float(confidence_level)
        if not 0.5 < confidence < 1.0:
            raise ValueError("confidence_level must be between 0.5 and 1.0")
        operation = str(analysis_type).casefold()
        if operation not in {"summary", "correlation", "group_comparison", "regression"}:
            raise ValueError("Unsupported analysis_type")
        selected = list(data)
        if operation != "summary":
            x_key, y_key = x_name or selected[0], y_name or (selected[1] if len(selected) > 1 else "")
            if x_key not in data or y_key not in data or x_key == y_key:
                raise ValueError("x_name and y_name must identify two different supplied series")
            x, y = data[x_key], data[y_key]
        else:
            x_key = y_key = ""
            x = y = []
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="invalid_statistical_input")

    scipy_stats = _scipy_stats()
    warnings = []
    summaries = []
    for name, values in data.items():
        mean = statistics.fmean(values)
        std = statistics.stdev(values)
        sem = std / math.sqrt(len(values))
        alpha = round(1.0 - confidence, 12)
        critical = (
            float(scipy_stats.t.ppf(1 - alpha / 2, len(values) - 1))
            if scipy_stats is not None else statistics.NormalDist().inv_cdf(1 - alpha / 2)
        )
        summaries.append({
            "name": name, "n": len(values), "mean": mean, "sample_std": std,
            "median": statistics.median(values), "min": min(values), "max": max(values),
            "confidence_level": confidence,
            "mean_confidence_interval": [mean - critical * sem, mean + critical * sem],
            "confidence_interval_method": "student_t" if scipy_stats is not None else "normal_approximation",
        })
    if scipy_stats is None:
        warnings.append(
            "SciPy is unavailable: confidence intervals use a normal approximation and hypothesis-test p-values are omitted. Install .[analysis] for the full contract."
        )
    result: dict[str, Any] = {"summaries": summaries}
    if operation == "correlation":
        if len(x) != len(y) or len(x) < 3:
            return tool_error(tool, "Correlation requires equal-length series with at least three paired values.", error_code="invalid_statistical_input")
        method = str(correlation_method).casefold()
        if method not in {"pearson", "spearman"}:
            return tool_error(tool, "correlation_method must be pearson or spearman.")
        transformed_x, transformed_y = (_rank(x), _rank(y)) if method == "spearman" else (x, y)
        try:
            coefficient = _correlation(transformed_x, transformed_y)
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_statistical_input")
        p_value = None
        if scipy_stats is not None:
            test = scipy_stats.spearmanr(x, y) if method == "spearman" else scipy_stats.pearsonr(x, y)
            p_value = float(test.pvalue)
        result["correlation"] = {
            "x_name": x_key, "y_name": y_key, "method": method,
            "coefficient": coefficient, "p_value": p_value, "n_pairs": len(x),
            "alpha": alpha, "statistically_significant": (
                None if p_value is None else p_value < alpha
            ),
        }
        if p_value is not None:
            result["inferential_assumptions"] = (
                "independent paired observations and bivariate normality for the Pearson p-value"
                if method == "pearson" else
                "independent paired observations; Spearman inference assumes exchangeable ranks"
            )
    elif operation == "group_comparison":
        x_mean, y_mean = statistics.fmean(x), statistics.fmean(y)
        pooled = math.sqrt(
            ((len(x) - 1) * statistics.variance(x) + (len(y) - 1) * statistics.variance(y))
            / (len(x) + len(y) - 2)
        )
        comparison = {
            "group_1": x_key, "group_2": y_key,
            "mean_difference": x_mean - y_mean,
            "cohens_d": (x_mean - y_mean) / pooled if pooled else None,
            "welch_t_test": None, "mann_whitney_u": None,
        }
        if scipy_stats is not None:
            t_test = scipy_stats.ttest_ind(x, y, equal_var=False)
            mann = scipy_stats.mannwhitneyu(x, y, alternative="two-sided")
            comparison["welch_t_test"] = {"statistic": float(t_test.statistic), "p_value": float(t_test.pvalue)}
            comparison["mann_whitney_u"] = {"statistic": float(mann.statistic), "p_value": float(mann.pvalue)}
            comparison["welch_t_test"]["statistically_significant"] = bool(t_test.pvalue < alpha)
            comparison["mann_whitney_u"]["statistically_significant"] = bool(mann.pvalue < alpha)
            result["inferential_assumptions"] = (
                "independent groups; Welch inference assumes approximately normal group means, while Mann-Whitney tests distributional rank separation"
            )
        result["group_comparison"] = comparison
    elif operation == "regression":
        if len(x) != len(y) or len(x) < 3:
            return tool_error(tool, "Regression requires equal-length series with at least three paired values.", error_code="invalid_statistical_input")
        x_mean, y_mean = statistics.fmean(x), statistics.fmean(y)
        denominator = sum((value - x_mean) ** 2 for value in x)
        if denominator == 0:
            return tool_error(tool, "Regression is undefined for a constant x series.", error_code="invalid_statistical_input")
        slope = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y)) / denominator
        intercept = y_mean - slope * x_mean
        fitted = [intercept + slope * value for value in x]
        residual_ss = sum((actual - predicted) ** 2 for actual, predicted in zip(y, fitted))
        total_ss = sum((actual - y_mean) ** 2 for actual in y)
        result["regression"] = {
            "x_name": x_key, "y_name": y_key, "model": "ordinary_least_squares_linear",
            "slope": slope, "intercept": intercept,
            "r_squared": 1 - residual_ss / total_ss if total_ss else None,
            "n_pairs": len(x),
        }
    display_rows = [(row["name"], row["n"], f"{row['mean']:.6g}", f"{row['sample_std']:.6g}", f"{row['median']:.6g}") for row in summaries]
    return tool_success(
        tool, display=_table(("Series", "n", "Mean", "Sample SD", "Median"), display_rows),
        analysis_type=operation, sample_source="explicit_user_supplied_numeric_series",
        result=result, series_names=list(data), sample_counts={name: len(values) for name, values in data.items()},
        provenance={"engine": "Python statistics; SciPy hypothesis tests when installed"},
        warnings=[
            *warnings,
            "Inferential p-values depend on the stated sampling/distribution assumptions; correlation does not establish causation."
            if operation in {"correlation", "group_comparison"} else
            "Descriptive or regression output does not by itself establish causation.",
        ],
    )


def lookup_glass_transition(polymer_query: str, top_k: int = 5) -> str:
    """Search the checksummed Tg snapshot while preserving row provenance."""
    tool = "lookup_glass_transition"
    query = str(polymer_query or "").strip()
    if not query:
        return tool_error(tool, "A polymer name, tag, or PSMILES is required.")
    limit = max(1, min(int(top_k), 10))
    entries = asset_payload()["tg"]["entries"]
    normalized = query.casefold()
    query_key = _key(query)
    qualified_query = re.sub(r"\s+\([^()]*\)\s*$", "", query)
    qualified_query_key = _key(qualified_query) if qualified_query != query else ""
    candidates = []
    for index, entry in enumerate(entries):
        names = [str(name) for name in entry.get("names") or []]
        tags = [str(tag) for tag in entry.get("tags") or []]
        name_keys = {_key(name) for name in names}
        tag_keys = {_key(tag) for tag in tags}
        if query == entry.get("psmiles"):
            score, match = 100, "exact_psmiles"
        elif query_key in name_keys or query_key in tag_keys:
            score, match = 100, "exact_name_or_tag"
        elif qualified_query_key and qualified_query_key in name_keys:
            score, match = 90, "qualified_name"
        elif any(normalized in name.casefold() for name in names):
            score, match = 85, "name_substring"
        elif any(normalized in tag.casefold() for tag in tags):
            score, match = 60, "tag_substring"
        else:
            continue
        candidates.append((score, index, match, entry))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    matches = []
    for score, _, match, entry in candidates[:limit]:
        measured = entry.get("Tg_K")
        predicted = entry.get("Tg_K_predicted")
        selected = measured if measured is not None else predicted
        matches.append({
            "psmiles": entry["psmiles"], "names": entry.get("names") or [],
            "tags": entry.get("tags") or [], "tg_k": selected,
            "tg_c": None if selected is None else float(selected) - 273.15,
            "measured_tg_k": measured, "predicted_tg_k": predicted,
            "prediction_std_k": entry.get("Tg_std"), "source": entry.get("source"),
            "evidence_class": "measured_snapshot" if measured is not None else "precomputed_model_prediction",
            "match_type": match, "match_score": score,
        })
    if not matches:
        return tool_error(
            tool, f"No Tg snapshot match for {query!r}.", error_code="polymer_not_found",
            snapshot_entries=len(entries),
        )
    return tool_success(
        tool,
        display=_table(
            ("Polymer", "Tg (K)", "Tg (C)", "Evidence", "Source"),
            [("; ".join(row["names"][:2]) or row["psmiles"], f"{row['tg_k']:.6g}",
              f"{row['tg_c']:.6g}", row["evidence_class"], row["source"] or "—") for row in matches],
        ),
        analysis_type="tg_lookup", polymer_query=query, matches=matches,
        snapshot_entries=len(entries), model_info=asset_payload()["tg"].get("model_info"),
        runtime_model_loaded=False, provenance=asset_payload()["provenance"],
        warnings=[
            "Each row is labeled measured snapshot or precomputed model prediction; no thermal model weights are loaded at runtime.",
            "A snapshot lookup is not a fitted polymer-solvent thermodynamic record.",
        ],
    )


_GROUPS: tuple[tuple[str, str, Optional[float], Optional[float], Optional[float]], ...] = (
    ("[c]1[c][c][c]([c][c]1)-[c]2[c][c][c][c][c]2", "biphenyl", 12000, None, None),
    ("C(=O)[NH]", "amide", 12000, 20, 22), ("C(=O)[OX2]", "ester", 10000, 18, 18),
    ("[#16](=O)(=O)", "sulfonyl", 7000, None, None),
    ("C(C(F)(F)F)(C(F)(F)F)", "hexafluoroisopropylidene", 8000, None, None),
    ("[Si]([CH3])([CH3])[OX2]", "siloxane", 2000, None, None),
    ("[c]1[c][c][c][c][c]1", "phenylene", 6000, 25, 5),
    ("[CX3](=O)([#6])[#6]", "ketone", 5000, None, None),
    ("[OX2H]", "hydroxyl", 8500, 12, None),
    ("[OX2]([#6])[#6]", "ether", 4500, 8, 5),
    ("[#16X2]([#6])[#6]", "thioether", 3500, None, None),
    ("[NX3H]([#6])[#6]", "secondary_amine", 6000, None, None),
    ("[CX4](Cl)(Cl)", "dichloromethylene", 4500, None, None),
    ("[CX4H](Cl)", "chloromethine", 4000, 14, None),
    ("[CX4](F)(F)", "difluoromethylene", 5000, 12, 10),
    ("[#6]/[CH]=[CH]/[#6]", "trans_vinylene", 9000, None, None),
    ("[CX4]([CH3])([CH3])", "gem_dimethyl", 6500, 25, None),
    ("[CX4H]([CH3])", "methyl_methine", 7500, 18, 14),
    ("[CH2]", "methylene", 4000, 10.5, 9.9),
)


def _thermal_groups(psmiles: str) -> dict[str, Any]:
    try:
        from rdkit import Chem
    except ImportError as error:
        raise RuntimeError("RDKit is required; install DISSOLVE with .[thermal].") from error
    sanitized = re.sub(r"\[\*\]|\[\*:\d+\]|\*", "[Xe]", psmiles.strip())
    molecule = Chem.MolFromSmiles(sanitized)
    if molecule is None:
        raise ValueError("PSMILES could not be parsed")
    heavy = sum(atom.GetAtomicNum() != 54 for atom in molecule.GetAtoms())
    if not heavy:
        raise ValueError("PSMILES has no non-attachment heavy atoms")
    consumed: set[int] = set()
    groups: dict[str, int] = {}
    for smarts, name, *_ in _GROUPS:
        pattern = Chem.MolFromSmarts(smarts)
        count = 0
        for match in molecule.GetSubstructMatches(pattern, uniquify=True):
            real = {index for index in match if molecule.GetAtomWithIdx(index).GetAtomicNum() != 54}
            if real - consumed:
                count += 1
                consumed.update(real)
        if count:
            groups[name] = count
    return {"groups": groups, "coverage": len(consumed) / heavy, "heavy_atoms": heavy, "matched_atoms": len(consumed)}


def estimate_thermal_properties(
    polymer_psmiles: str,
    polymer_name: Optional[str] = None,
) -> str:
    """Estimate Tm, fusion enthalpy, and heat-capacity change by group contribution."""
    tool = "estimate_thermal_properties"
    try:
        parsed = _thermal_groups(str(polymer_psmiles or ""))
    except (RuntimeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="thermal_inference_unavailable")
    groups = parsed["groups"]
    totals = []
    missing: dict[str, list[str]] = {"delta_hf": [], "delta_cp": [], "delta_sf": []}
    for value_index, label in ((2, "delta_hf"), (3, "delta_cp"), (4, "delta_sf")):
        total = 0.0
        used = 0
        for row in _GROUPS:
            count = groups.get(row[1], 0)
            if not count:
                continue
            if row[value_index] is None:
                missing[label].append(row[1])
            else:
                total += float(row[value_index]) * count
                used += count
        totals.append(total if used else None)
    delta_hf, delta_cp, delta_sf = totals
    tm_k = delta_hf / delta_sf if delta_hf is not None and delta_sf else None
    reliable = parsed["coverage"] >= 0.7 and not any(missing.values()) and tm_k is not None
    result = {
        # No invented identity: an unsupplied name stays None instead of
        # the fabricated "Unknown" polymer label.
        "polymer_name": (
            str(polymer_name).strip() if polymer_name is not None else None
        ),
        "polymer_psmiles": polymer_psmiles,
        "tm_k": tm_k, "tm_c": None if tm_k is None else tm_k - 273.15,
        "delta_hf_j_per_mol": delta_hf, "delta_cp_j_per_mol_k": delta_cp,
        "delta_sf_j_per_mol_k": delta_sf, "group_coverage": parsed["coverage"],
        "groups": groups, "missing_contribution_groups": missing,
        "reliable_within_group_method": reliable,
    }
    return tool_success(
        tool,
        display=_table(
            ("Property", "Estimate", "Unit"),
            [("Tm", f"{tm_k:.6g}" if tm_k is not None else "—", "K"),
             ("Delta Hf", f"{delta_hf:.6g}" if delta_hf is not None else "—", "J/mol"),
             ("Delta Cp", f"{delta_cp:.6g}" if delta_cp is not None else "—", "J/(mol K)"),
             ("Coverage", f"{parsed['coverage']:.1%}", "matched heavy atoms")],
        ),
        analysis_type="thermal_group_contribution", result=result,
        polymer_name=polymer_name, polymer_psmiles=polymer_psmiles,
        method="Van Krevelen group contribution", runtime_model_loaded=False,
        evidence_class="predictive_extension_not_fitted_record",
        provenance={**asset_payload()["provenance"], "method_source": "Properties of Polymers, 4th ed."},
        warnings=[
            "These are group-contribution estimates and approximate solubility inputs, not experimental values or fitted polymer-solvent records.",
            "No polyBERT thermal weights are present or loaded; no ML residual correction was applied.",
            "Coverage and missing contribution groups must be considered before use.",
        ],
    )


def list_thermal_evidence() -> str:
    """Describe exactly which thermal evidence modes are and are not deployed."""
    payload = asset_payload()
    rows = [
        {"capability": "Tg snapshot lookup", "available": True, "basis": f"{len(payload['tg']['entries'])} precomputed rows"},
        {"capability": "Van Krevelen group contribution", "available": True, "basis": "runtime inference with .[thermal]"},
        {"capability": "polyBERT residual inference", "available": False, "basis": "weights absent at v10 baseline"},
        {"capability": "dynamic fitted solubility records", "available": False, "basis": "refused: ideal-SLE promotion is not fitted evidence"},
    ]
    return tool_success(
        "list_thermal_evidence",
        display=_table(("Capability", "Available", "Basis"), [(row["capability"], row["available"], row["basis"]) for row in rows]),
        analysis_type="thermal_evidence_inventory", rows=rows,
        provenance=payload["provenance"],
        warnings=["Generated predictive extensions are never equivalent to fitted thermodynamic records."],
    )

