"""A-2A4 coverage distributions and P5 abstention floor. No MiniLM rewrite. No gold needles."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import abstention_a3, dense_d2, dense_d3, engine_e2e, research, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "734f4a9c7b64ff700ab3c5f99b0844c00b8180adb016430f24ecd9637ae3e0da"
SCHEMA = "dissolve.retrieval.abstention.v1"
CURVES_NAME = "CURVES.retrieval.abstention.v1.json"
CURVES_PATH = DEFAULT_OUT_DIR / CURVES_NAME
GZIP_SHA256 = "bf0bb9e213401868bac45590f364bc831976765fc9b258357523a981d2fe14f1"
MANIFEST_SHA256_BEFORE = "a3422bcffca301952895f159eaae418656fcdb384d5e976283f3487064c31726"
NAMED_PERCENTILES = (1, 5, 10, 25)
SHIPPED_PERCENTILE = 5
EXPECTED_N_FIRE = 228
EXPECTED_N_REFUSE = 29
EXPECTED_N_CHUNKS = 922
_FORBIDDEN_EMIT_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_NAMES = frozenset({
    "GOLD.text.v1.unsealed.json",
    "GOLD.v2.json",
    "CENSUS.v3.json",
    "CHUNKS.t5.indexed.unsealed.v1.json",
    "t5-indexed-unsealed.json.gz",
    "CURVES.retrieval.v3.json",
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.d3.json",
    "CURVES.retrieval.d3.finding.json",
    "CURVES.retrieval.weights.v1.json",
    "OFFDOMAIN.queries.v1.json",
})


def named_percentile(values: Sequence[float], percentile: float) -> float:
    """Linear interpolation. IDENT text_chunking._percentile."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def five_number(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "p5": None, "median": None, "p95": None, "max": None}
    items = [float(value) for value in values]
    return {
        "n": len(items),
        "min": min(items),
        "p5": named_percentile(items, 5),
        "median": named_percentile(items, 50),
        "p95": named_percentile(items, 95),
        "max": max(items),
    }


def _stars(index: Mapping[str, Any], queries: Sequence[str]) -> list[float]:
    return [float(research.coverage_star(index, query)) for query in queries]


def _wilson(x: int, n: int) -> dict[str, Any]:
    interval = text_chunk_metrics.wilson_interval(x, n, z=text_chunk_metrics.WILSON_Z)
    return {
        "x": int(x),
        "n": int(n),
        "rate": (int(x) / int(n)) if n else None,
        "lo": interval["lo"],
        "hi": interval["hi"],
        "z": text_chunk_metrics.WILSON_Z,
    }


def build_abstention_artifact(
    *,
    index: Mapping[str, Any],
    fire_queries: Sequence[str],
    offdomain_queries: Sequence[str],
    pins: Mapping[str, str],
) -> dict[str, Any]:
    fire_stars = _stars(index, fire_queries)
    off_stars = _stars(index, offdomain_queries)
    floors = {int(p): named_percentile(fire_stars, p) for p in NAMED_PERCENTILES}
    shipped_floor = floors[SHIPPED_PERCENTILE]
    grid = []
    for percentile in NAMED_PERCENTILES:
        floor = floors[percentile]
        n_fn = sum(1 for value in fire_stars if value < floor)
        n_fp = sum(1 for value in off_stars if value >= floor)
        n_keep = len(fire_stars) - n_fn
        grid.append({
            "percentile": percentile,
            "floor": floor,
            "fn": _wilson(n_fn, len(fire_stars)),
            "fp": _wilson(n_fp, len(off_stars)),
            "must_fire_recall": _wilson(n_keep, len(fire_stars)),
            "shipped": percentile == SHIPPED_PERCENTILE,
        })
    n_overlap = sum(1 for value in off_stars if value >= floors[SHIPPED_PERCENTILE])
    artifact = {
        "schema": SCHEMA,
        "spec_sha256": SPEC_SHA256,
        "statistic": "query_idf_coverage",
        "percentile_interpolation": "linear_ident_text_chunking._percentile",
        "named_percentiles": list(NAMED_PERCENTILES),
        "shipped_percentile": SHIPPED_PERCENTILE,
        "shipped_floor": shipped_floor,
        "must_fire": five_number(fire_stars),
        "offdomain": five_number(off_stars),
        "overlap_a3_ge_must_fire_p5": _wilson(n_overlap, len(off_stars)),
        "grid": grid,
        "held_out_in_index": 0,
        "n_chunks": len(index.get("chunks") or []),
        **dict(pins),
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "A-2A4 emit must not carry needles, queries, or STRAP fact_ids.")
    return artifact


def _write_manifest_abstention(manifest_path: Path, block: Mapping[str, Any]) -> None:
    payload = json.loads(manifest_path.read_text())
    before_keys = set(payload)
    payload["abstention"] = dict(block)
    if set(payload) - before_keys != {"abstention"} and "abstention" not in before_keys:
        raise TextGoldError("manifest_keys_moved", "Manifest may gain abstention only.")
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def emit_a2a4_product(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    store_path: Path | None = None,
    offdomain_path: Path | None = None,
    index_path: Path | None = None,
    dest_dir: Path | None = None,
    manifest_path: Path | None = None,
    index: Mapping[str, Any] | None = None,
    fire_queries: Sequence[str] | None = None,
    offdomain_queries: Sequence[str] | None = None,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or engine_e2e.CENSUS_PATH)
    store_file = Path(store_path or engine_e2e.STORE_PATH)
    off_file = Path(offdomain_path or abstention_a3.OFFDOMAIN_PATH)
    gzip_file = Path(index_path or dense_d2.INDEX_GZIP_PATH)
    man_file = Path(manifest_path or engine_e2e.MANIFEST_PATH)
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    curves_path = out_dir / CURVES_NAME
    if curves_path.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "A-2A4 must not overwrite a pinned persist name.")
    gzip_before = file_sha256(gzip_file)
    if gzip_file.resolve() == dense_d2.INDEX_GZIP_PATH.resolve() and gzip_before != GZIP_SHA256:
        raise TextGoldError("gzip_moved", "T5 gzip moved; do not emit A-2A4.")
    gold_before = file_sha256(gold_file)
    if gold_file.resolve() == text_chunk_metrics.GOLD_UNSEALED_PATH.resolve() and gold_before != text_chunk_metrics.GOLD_UNSEALED_SHA256:
        raise TextGoldError("gold_moved", "Unsealed gold digest moved; do not emit A-2A4.")
    gold = json.loads(gold_file.read_text())
    split = text_chunk_metrics.split_gold(gold)
    if gold_file.resolve() == text_chunk_metrics.GOLD_UNSEALED_PATH.resolve():
        if len(split["must_fire"]) != EXPECTED_N_FIRE or len(split["must_refuse"]) != EXPECTED_N_REFUSE:
            raise TextGoldError("gold_split_moved", "Must-fire / must-refuse counts are not the pinned sets.")
    loaded = index if index is not None else dense_d2.load_gzip_index(gzip_file)
    working = dict(loaded)
    working.pop("abstention", None)
    if index is None and gzip_file.resolve() == dense_d2.INDEX_GZIP_PATH.resolve() and len(working.get("chunks") or []) != EXPECTED_N_CHUNKS:
        raise TextGoldError("n_chunks_mismatch", "Live n_chunks is not 922.")
    index_shas = {str(row.get("sha256") or row.get("paper_sha256") or "") for row in (working.get("documents") or [])}
    if index_shas & set(split["held_shas"]):
        raise TextGoldError("held_out_in_index", "Held-out papers must not enter the T5 index.")
    off_payload = json.loads(off_file.read_text()) if off_file.is_file() else None
    fire = list(fire_queries) if fire_queries is not None else [
        str(fact.get("query") or "") for fact in split["must_fire"]
    ]
    off = list(offdomain_queries) if offdomain_queries is not None else [
        str(row.get("query") or "") for row in (off_payload or {}).get("queries") or []
    ]
    if not fire or not off:
        raise TextGoldError("a2a4_empty_battery", "A-2A4 needs must-fire queries and the A-3 battery.")
    gold_ids = {str(fact.get("fact_id") or "") for fact in split["facts"]}
    od_ids = {str(row.get("id") or "") for row in ((off_payload or {}).get("queries") or [])}
    if gold_ids & od_ids:
        raise TextGoldError("od_id_collision", "Off-domain ids collided with gold fact_ids.")
    pins = {
        "gold_sha256": gold_before,
        "census_sha256": file_sha256(census_file),
        "store_sha256": file_sha256(store_file),
        "gzip_sha256": gzip_before,
        "offdomain_sha256": file_sha256(off_file) if off_file.is_file() else "",
        "set_digest": str((off_payload or {}).get("set_digest") or abstention_a3.SET_DIGEST),
    }
    artifact = build_abstention_artifact(
        index=working, fire_queries=fire, offdomain_queries=off, pins=pins,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    calibrated_at = datetime.now(timezone.utc).isoformat()
    block = {
        "statistic": "query_idf_coverage",
        "percentile": SHIPPED_PERCENTILE,
        "floor": artifact["shipped_floor"],
        "calibrated_at": calibrated_at,
        "gold_sha256": pins["gold_sha256"],
        "census_sha256": pins["census_sha256"],
        "store_sha256": pins["store_sha256"],
        "offdomain_sha256": pins["offdomain_sha256"],
    }
    if text_chunk_metrics._contains_forbidden_keys(block, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "Manifest abstention must not carry gold text.")
    _write_manifest_abstention(man_file, block)
    if file_sha256(gzip_file) != gzip_before:
        raise TextGoldError("gzip_mutated", "T5 gzip moved during A-2A4 emit.")
    if file_sha256(gold_file) != gold_before:
        raise TextGoldError("gold_mutated", "Unsealed gold moved during A-2A4 emit.")
    return {
        "curves_path": str(curves_path),
        "manifest_path": str(man_file),
        "shipped_floor": artifact["shipped_floor"],
        "shipped_percentile": SHIPPED_PERCENTILE,
        "gzip_sha256": gzip_before,
        "gold_sha256": gold_before,
        "n_must_fire": artifact["must_fire"]["n"],
        "n_offdomain": artifact["offdomain"]["n"],
        "fn": next(row["fn"] for row in artifact["grid"] if row["shipped"]),
        "fp": next(row["fp"] for row in artifact["grid"] if row["shipped"]),
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    return emit_a2a4_product()
