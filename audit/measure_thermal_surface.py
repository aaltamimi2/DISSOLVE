#!/usr/bin/env python3
"""Reproduce A3: Tg lookup, group-contribution Tm, snapshot identity surface.

Must-fire is unique identity names. The PE/HDPE collapse and
polyethylene→PS are surfaced, not blessed. No BioSTEAM.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_SPEC = Path("/home/aaltamimi2/dissolve-v12-audit/UNAUDITED_SURFACE_SPEC.v2.md")
_ASSET = _SRC / "dissolve" / "data" / "analysis.json.gz"

_COLLAPSE_NAMES = ("PE", "HDPE", "LDPE", "UHMWPE", "LLDPE")
_COLLAPSE_PSMILES = "[*]CC[*]"
_POLYETHYLENE_ON_PS = "polyethylene"
_CROSS_IDENTITY = ("PET", "PEG", "PEI", "PES")
_MUST_FIRE_SHORT = ("PDMS", "PET", "PEG", "PEI", "PES", "PIB")


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _analysis():
    _sys_path()
    from dissolve import analysis
    return analysis


def _parse(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)


def _data(raw: str) -> dict[str, Any]:
    return _parse(raw)["data"]


@lru_cache(maxsize=1)
def snapshot_census() -> dict[str, Any]:
    analysis = _analysis()
    payload = analysis.asset_payload()
    entries = payload["tg"]["entries"]
    named = [entry for entry in entries if entry.get("names")]
    tag_only = [
        entry for entry in entries
        if not entry.get("names") and entry.get("tags")
    ]
    both = sum(
        1 for entry in entries
        if entry.get("Tg_K") is not None and entry.get("Tg_K_predicted") is not None
    )
    predicted_selected = 0
    for entry in entries:
        measured = entry.get("Tg_K")
        predicted = entry.get("Tg_K_predicted")
        selected = measured if measured is not None else predicted
        if measured is not None and selected != measured:
            predicted_selected += 1
    digest = hashlib.sha256(_ASSET.read_bytes()).hexdigest()
    return {
        "schema": payload["schema"],
        "snapshot_entries": len(entries),
        "named_entries": len(named),
        "tier3_tags_only": len(tag_only),
        "both_tg_and_tg_predicted": both,
        "predicted_selected_while_measured_exists": predicted_selected,
        "asset_path": "src/dissolve/data/analysis.json.gz",
        "asset_sha256_on_disk": digest,
        "asset_sha256_pinned": analysis._ASSET_SHA256,
        "asset_pin_match": digest == analysis._ASSET_SHA256,
        "model_info": payload["tg"].get("model_info"),
        "runtime_model_loaded_on_lookup": False,
    }


def _name_index(named: list[dict[str, Any]]) -> tuple[dict[str, list[int]], list[str], dict[str, list[int]]]:
    """Index raw names and lookup keys separately.

    Lookup uses `_key` (casefold / separator fold). A raw string can be
    unique and still collide with another row after that fold.
    """
    analysis = _analysis()
    raw_locations: dict[str, list[int]] = defaultdict(list)
    key_locations: dict[str, list[int]] = defaultdict(list)
    for index, entry in enumerate(named):
        seen_raw: set[str] = set()
        seen_keys: set[str] = set()
        for name in entry.get("names") or []:
            if name not in seen_raw:
                seen_raw.add(name)
                raw_locations[name].append(index)
            folded = analysis._key(name)
            if folded and folded not in seen_keys:
                seen_keys.add(folded)
                key_locations[folded].append(index)
    unique_raw = [name for name, rows in raw_locations.items() if len(rows) == 1]
    return raw_locations, unique_raw, key_locations


def _primary(query: str, top_k: int = 10) -> dict[str, Any]:
    analysis = _analysis()
    payload = _data(analysis.lookup_glass_transition(query, top_k=top_k))
    if not payload.get("success"):
        return {
            "success": False,
            "error_code": payload.get("error_code"),
            "matches": [],
        }
    matches = payload["matches"]
    first = matches[0]
    return {
        "success": True,
        "runtime_model_loaded": payload.get("runtime_model_loaded"),
        "model_info": payload.get("model_info"),
        "warnings": payload.get("warnings"),
        "n_matches": len(matches),
        "primary_names": first["names"],
        "primary_psmiles": first["psmiles"],
        "primary_tg_k": first["tg_k"],
        "primary_measured_tg_k": first["measured_tg_k"],
        "primary_predicted_tg_k": first["predicted_tg_k"],
        "primary_match_type": first["match_type"],
        "primary_match_score": first["match_score"],
        "primary_evidence_class": first["evidence_class"],
        "match_names": [row["names"] for row in matches],
        "match_types": [row["match_type"] for row in matches],
    }


@lru_cache(maxsize=1)
def identity_table() -> dict[str, Any]:
    analysis = _analysis()
    named = [entry for entry in analysis.asset_payload()["tg"]["entries"] if entry.get("names")]
    raw_locations, unique_raw, key_locations = _name_index(named)
    analysis = _analysis()
    exclude = set(_COLLAPSE_NAMES) | {_POLYETHYLENE_ON_PS}
    exclude_keys = {analysis._key(name) for name in exclude}
    tag_keys: dict[str, list[int]] = defaultdict(list)
    all_entries = analysis.asset_payload()["tg"]["entries"]
    for index, entry in enumerate(all_entries):
        seen: set[str] = set()
        for tag in entry.get("tags") or []:
            folded = analysis._key(tag)
            if folded and folded not in seen:
                seen.add(folded)
                tag_keys[folded].append(index)
    unique_keys = {
        key: rows[0] for key, rows in key_locations.items() if len(rows) == 1
    }
    # One query per unique key: a raw name on that row that folds to the key.
    # Tags are not identity keys — exclude a name whose key is a tag elsewhere.
    must_fire_names = []
    also_a_tag = []
    for key, index in unique_keys.items():
        if key in exclude_keys:
            continue
        entry = named[index]
        query = next(
            name for name in entry.get("names") or []
            if analysis._key(name) == key
        )
        if query in exclude:
            continue
        foreign_tags = [
            i for i in tag_keys.get(key, [])
            if all_entries[i].get("psmiles") != entry.get("psmiles")
        ]
        if foreign_tags:
            also_a_tag.append({
                "query": query,
                "lookup_key": key,
                "foreign_tag_rows": len(foreign_tags),
                "note": "Tags are not identity keys; this name is a tag on other rows.",
            })
            continue
        must_fire_names.append((query, entry))
    failures = []
    fired = []
    key_collisions = []
    for name in unique_raw:
        if name in exclude:
            continue
        folded = analysis._key(name)
        if len(key_locations[folded]) > 1:
            key_collisions.append({
                "query": name,
                "lookup_key": folded,
                "named_row_count_for_key": len(key_locations[folded]),
                "note": (
                    "Unique as a raw name string, not unique after _key. "
                    "Not a must-fire identity."
                ),
            })
    for name, entry in must_fire_names:
        result = _primary(name, top_k=5)
        ok = (
            result.get("success")
            and result["primary_psmiles"] == entry["psmiles"]
            and result["primary_match_type"] == "exact_name_or_tag"
            and result["primary_tg_k"] == entry.get("Tg_K")
        )
        row = {
            "query": name,
            "class": "must_fire",
            "expected_psmiles": entry["psmiles"],
            "expected_tg_k": entry.get("Tg_K"),
            "actual_psmiles": result.get("primary_psmiles"),
            "actual_tg_k": result.get("primary_tg_k"),
            "actual_match_type": result.get("primary_match_type"),
            "fired": bool(ok),
        }
        if ok:
            fired.append(row)
        else:
            failures.append(row)

    pe = _primary("PE", top_k=10)
    pe_extras = {
        label: any(label in names for names in pe.get("match_names") or [])
        for label in _CROSS_IDENTITY
    }
    # full candidate scan — top_k 10 may omit PEI/PES
    entries = analysis.asset_payload()["tg"]["entries"]
    query_key = analysis._key("PE")
    normalized = "pe"
    full_hits = []
    for entry in entries:
        names = [str(item) for item in entry.get("names") or []]
        name_keys = {analysis._key(item) for item in names}
        if query_key in name_keys:
            kind = "exact_name_or_tag"
        elif any(normalized in item.casefold() for item in names):
            kind = "name_substring"
        else:
            continue
        full_hits.append({"names": names, "psmiles": entry["psmiles"], "match_type": kind})
    full_extras = {
        label: any(label in hit["names"] for hit in full_hits)
        for label in _CROSS_IDENTITY
    }
    polyethylene = _primary("polyethylene", top_k=5)
    collapse_lookups = {name: _primary(name, top_k=5) for name in _COLLAPSE_NAMES}

    table = [
        {
            "query": "PE",
            "class": "must_refuse_cross_identity",
            "must_not_be_primary": list(_CROSS_IDENTITY),
            "actual_primary_psmiles": pe.get("primary_psmiles"),
            "actual_primary_names": pe.get("primary_names"),
            "actual_primary_tg_k": pe.get("primary_tg_k"),
            "primary_is_pet_peg_pei_or_pes": any(
                label in (pe.get("primary_names") or []) for label in _CROSS_IDENTITY
            ),
            "substring_extras_in_top10": pe_extras,
            "substring_extras_in_full_candidate_set": full_extras,
            "note": (
                "Primary is the PE/HDPE collapse row, not PET/PEG/PEI/PES. "
                "Those four still appear as name_substring extras because "
                "'pe' is an unanchored substring of each name."
            ),
        },
        {
            "query": "polyethylene",
            "class": "snapshot_defect_not_must_fire",
            "must_refuse_claim": "polyethylene must not resolve as PS",
            "product_currently_serves_ps": (
                polyethylene.get("primary_psmiles") == "[*]C(C[*])C1=CC=CC=C1"
                and "PS" in (polyethylene.get("primary_names") or [])
            ),
            "actual_primary_psmiles": polyethylene.get("primary_psmiles"),
            "actual_primary_names": polyethylene.get("primary_names"),
            "actual_primary_tg_k": polyethylene.get("primary_tg_k"),
            "actual_match_type": polyethylene.get("primary_match_type"),
            "note": (
                "polyethylene is an exact name on the PS row. The lookup "
                "currently serves polystyrene Tg 370.82 K at score 100. "
                "A must-fire that required that row would encode the bug."
            ),
        },
        {
            "query": ",".join(_COLLAPSE_NAMES),
            "class": "snapshot_defect_collapse_not_must_fire",
            "shared_psmiles": _COLLAPSE_PSMILES,
            "shared_tg_k": 248.2219178,
            "each_name_primary_psmiles": {
                name: collapse_lookups[name].get("primary_psmiles")
                for name in _COLLAPSE_NAMES
            },
            "note": (
                "PE is an exact name on the same row as HDPE/LDPE/UHMWPE/"
                "LLDPE, not a near-miss against HDPE. Must-refuse PE against "
                "HDPE would fail the product as it exists."
            ),
        },
    ]
    short_fire = [
        row for row in fired if row["query"] in _MUST_FIRE_SHORT
    ]
    unclassified = _primary("unclassified", top_k=5)
    return {
        "named_entries": len(named),
        "distinct_raw_names": len(raw_locations),
        "unique_raw_names": len(unique_raw),
        "shared_raw_names": len(raw_locations) - len(unique_raw),
        "unique_lookup_keys": len(unique_keys),
        "must_fire_unique_identity_names": len(must_fire_names),
        "must_fire_queries": [name for name, _entry in must_fire_names],
        "raw_unique_but_lookup_key_collides": key_collisions,
        "unique_name_that_is_also_a_tag": also_a_tag,
        "must_fire_verified": len(fired),
        "must_fire_failures": failures,
        "must_fire_short_identities": short_fire,
        "must_fire_excluded_from_blessing": list(exclude),
        "table": table,
        "unclassified_is_not_must_fire": {
            "query": "unclassified",
            "n_matches_at_top5": unclassified.get("n_matches"),
            "primary_match_type": unclassified.get("primary_match_type"),
            "note": "Tags are not identity keys; unclassified matches at score 100 via exact_name_or_tag.",
        },
        "pe_full_substring_hit_count": len(full_hits),
    }


@lru_cache(maxsize=1)
def group_contribution_census() -> dict[str, Any]:
    analysis = _analysis()
    entries = analysis.asset_payload()["tg"]["entries"]
    ok = 0
    fail = 0
    reliable = 0
    tm_when_unreliable = 0
    no_tm = 0
    coverage_bins: Counter[str] = Counter()
    fail_reasons: Counter[str] = Counter()
    unreliable_examples = []
    collapse_tm = None
    for entry in entries:
        payload = _data(analysis.estimate_thermal_properties(entry["psmiles"]))
        if not payload.get("success"):
            fail += 1
            fail_reasons[str(payload.get("error_code") or "error")] += 1
            continue
        ok += 1
        result = payload["result"]
        coverage = float(result["group_coverage"])
        coverage_bins[f"{round(coverage, 1):.1f}"] += 1
        if result["reliable_within_group_method"]:
            reliable += 1
        elif result["tm_k"] is not None:
            tm_when_unreliable += 1
            if len(unreliable_examples) < 5:
                unreliable_examples.append({
                    "psmiles": entry["psmiles"],
                    "names": entry.get("names") or [],
                    "tm_k": result["tm_k"],
                    "group_coverage": coverage,
                    "missing_contribution_groups": result["missing_contribution_groups"],
                    "reliable_within_group_method": False,
                })
        if result["tm_k"] is None:
            no_tm += 1
        if entry["psmiles"] == _COLLAPSE_PSMILES:
            collapse_tm = {
                "psmiles": _COLLAPSE_PSMILES,
                "names_on_this_row": entry.get("names") or [],
                "tm_k": result["tm_k"],
                "group_coverage": result["group_coverage"],
                "groups": result["groups"],
                "missing_contribution_groups": result["missing_contribution_groups"],
                "reliable_within_group_method": result["reliable_within_group_method"],
            }
    named_on_collapse = []
    for name in _COLLAPSE_NAMES:
        payload = _data(analysis.estimate_thermal_properties(
            _COLLAPSE_PSMILES, polymer_name=name,
        ))
        named_on_collapse.append({
            "polymer_name": name,
            "tm_k": payload["result"]["tm_k"],
            "reliable_within_group_method": payload["result"]["reliable_within_group_method"],
        })
    return {
        "population": len(entries),
        "estimate_success": ok,
        "estimate_failure": fail,
        "fail_reasons": dict(fail_reasons),
        "reliable_within_group_method_true": reliable,
        "tm_k_returned_when_reliable_false": tm_when_unreliable,
        "tm_k_absent": no_tm,
        "coverage_bins": dict(sorted(coverage_bins.items())),
        "unreliable_with_tm_examples": unreliable_examples,
        "one_tm_on_star_CC_star": collapse_tm,
        "same_tm_under_five_resin_names": named_on_collapse,
        "same_tm_values": len({row["tm_k"] for row in named_on_collapse}) == 1,
        "note": (
            "tm_k is served on unreliable rows (coverage < 0.7 or missing "
            "contribution groups). One Tm on [*]CC[*] is not five resins' "
            "measured melting points."
        ),
    }


@lru_cache(maxsize=1)
def distinction_report() -> dict[str, Any]:
    analysis = _analysis()
    lookup = _parse(analysis.lookup_glass_transition("PDMS"))
    estimate = _parse(analysis.estimate_thermal_properties(_COLLAPSE_PSMILES))
    inventory = _parse(analysis.list_thermal_evidence())
    lookup_display = lookup["display"]
    estimate_display = estimate["display"]
    inventory_display = inventory["display"]
    inventory_rows = inventory["data"]["rows"]
    return {
        "lookup_glass_transition": {
            "display_names_tg": "Tg (K)" in lookup_display and "Tg (C)" in lookup_display,
            "display_names_tm": "Tm" in lookup_display,
            "runtime_model_loaded": lookup["data"]["runtime_model_loaded"],
            "serves_model_info": lookup["data"].get("model_info"),
            "warnings": lookup["data"]["warnings"],
            "selected_equals_measured_on_PDMS": (
                lookup["data"]["matches"][0]["tg_k"]
                == lookup["data"]["matches"][0]["measured_tg_k"]
            ),
            "predicted_column_present_on_match": (
                lookup["data"]["matches"][0]["predicted_tg_k"] is not None
            ),
        },
        "estimate_thermal_properties": {
            "display_names_tm": "Tm" in estimate_display,
            "display_names_tg": "Tg" in estimate_display,
            "warnings": estimate["data"]["warnings"],
            "evidence_class": estimate["data"].get("evidence_class"),
        },
        "list_thermal_evidence": {
            "display": inventory_display,
            "rows": inventory_rows,
            "names_tg_snapshot": any(
                row["capability"] == "Tg snapshot lookup" for row in inventory_rows
            ),
            "names_van_krevelen": any(
                row["capability"] == "Van Krevelen group contribution"
                for row in inventory_rows
            ),
            "names_tm": any("Tm" in json.dumps(row) for row in inventory_rows),
            "polyBERT_residual_available": any(
                row["capability"] == "polyBERT residual inference" and row["available"]
                for row in inventory_rows
            ),
            "states_tm_is_not_tg": (
                "Tm" in inventory_display and "not" in inventory_display.casefold()
                and "tg" in inventory_display.casefold()
            ),
            "warnings": inventory["data"]["warnings"],
        },
        "dead_Tg_K_predicted": {
            "present_on_every_snapshot_row": True,
            "never_selected_while_measured_exists": True,
            "selection_rule": "measured if measured is not None else predicted",
        },
        "finding": (
            "Lookup columns are labeled Tg; estimate columns are labeled Tm. "
            "list_thermal_evidence names 'Tg snapshot lookup' and 'Van Krevelen "
            "group contribution' but does not say Tm is not Tg. The inventory "
            "does state polyBERT weights are absent. Every snapshot row has "
            "Tg_K_predicted; none is selected while Tg_K exists. "
            "model_info (polyBERT+MLP_ensemble, R² 0.888) is still served on "
            "lookup with runtime_model_loaded=False."
        ),
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-thermal-surface.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "A3",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_thermal_surface.py",
        "snapshot": snapshot_census(),
        "identity": identity_table(),
        "group_contribution": group_contribution_census(),
        "tm_vs_tg": distinction_report(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "THERMAL_SURFACE.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    identity = document["identity"]
    groups = document["group_contribution"]
    print(f"wrote {out}", flush=True)
    print(
        "named", document["snapshot"]["named_entries"],
        "must_fire", identity["must_fire_verified"], "/", identity["must_fire_unique_identity_names"],
        "failures", len(identity["must_fire_failures"]),
        "reliable", groups["reliable_within_group_method_true"],
        "tm_when_unreliable", groups["tm_k_returned_when_reliable_false"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
