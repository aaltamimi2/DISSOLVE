"""TEXT_CHUNKING_SPEC sweep metrics. BM25 only. No blended F1.

Per strategy × needle_span_chars bucket. T1 is the explicit control for T2
at matched size and overlap. None offsets are not a preserved span.

v1/v2 scored each paper as its own BM25 list, including hold-out papers.
v3 official ranking is BM25-body over index I (indexed papers only).
Must-refuse sits beside recall. Wilson 95% intervals decide ties.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import research, text_chunking
from .gold_ensemble import file_sha256
from .text_gold import (
    DEFAULT_OUT_DIR,
    SPAN_BUCKETS,
    TextGoldError,
    nonempty_needles,
    refuse_all_short,
    span_bucket,
    span_histogram,
)

SPEC_SHA256 = text_chunking.SPEC_SHA256
SPEC_V2_SHA256 = "2b6b776aec5a9146518051caee4b87e5dbd684eb1357776aa5655a0fa68eb8aa"
SPEC_V2_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/TEXT_CHUNKING_SPEC.v2.md")
SPEC_V3_SHA256 = "56f1eee782fce93125fd493ebf087deded3b9c17c245ef3e810f4394d76d4ac7"
SPEC_V3_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/TEXT_CHUNKING_SPEC.v3.md")
RETRIEVAL_KS = (1, 3, 5, 10, 20)
WILSON_Z = 1.959963984540054
CURVES_SCHEMA = "dissolve.text-chunk-curves.retrieval.v1"
CURVES_V3_SCHEMA = "dissolve.text-chunk-curves.retrieval.v3"
ERROR_ANALYSIS_SCHEMA = "dissolve.text-chunk-error-analysis.k10.v3"
CURVES_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v1.json"
CURVES_V3_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json"
CURVES_V1_PNG_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v1.png"
CURVES_V3_PNG_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v3.png"
ERROR_ANALYSIS_PATH = DEFAULT_OUT_DIR / "ERROR_ANALYSIS.k10.v3.json"
POINT_SWEEP_PATH = DEFAULT_OUT_DIR / "SWEEP.t0_t6.v1.json"
PROBE_SWEEP_PATH = DEFAULT_OUT_DIR / "SWEEP.t0_t6.probe.v1.json"
CURVE_SWEEP_PATH = DEFAULT_OUT_DIR / "SWEEP.t0_t6.curve.v1.json"
GOLD_UNSEALED_PATH = DEFAULT_OUT_DIR / "GOLD.text.v1.unsealed.json"
GOLD_V2_PATH = DEFAULT_OUT_DIR / "GOLD.v2.json"
POINT_SWEEP_SHA256 = "dfc4c4707e7b43ecd3238ac9e3182049da646bc8383abd2e5a282e7b5611afd6"
PROBE_SWEEP_SHA256 = "03ae2b2827a577baac3c3fab10edc78bae181c103e8c7e3a2885b9efd7351118"
CURVE_SWEEP_SHA256 = "cb5695ff7bced4f8bea564d3ef01cc01f581fa90efb5a314ff9ce318b35f0942"
CURVES_V1_SHA256 = "abe190c7b5d2608328b93f0c5311dd68ab1f9b1843835e0267bd65228ced62da"
CURVES_V1_PNG_SHA256 = "96cd15b152fa8a17bc570393fafb9cea648def7ba7e5c4b04c5014ba1193bb16"
GOLD_UNSEALED_SHA256 = "1c6df27b08dde99c675a01ecc8f99dbfb8dbcee250f298b3e1e352a56816c7d8"
SERIES_BUCKETS = ("all",) + SPAN_BUCKETS
NAMED_ERROR_ARMS = (
    ("T0", (("target", text_chunking.T0_TARGET), ("overlap", text_chunking.T0_OVERLAP))),
    ("T5", (("target", text_chunking.T5_TARGET),)),
    ("T6", (("percentile", 95),)),
    ("T1", (("size", 2000), ("overlap_frac", 0.25))),
)


def require_histogram_before_score(gold: Mapping[str, Any]) -> dict[str, Any]:
    """Scores are unreadable until the span histogram is published with the gold."""
    histogram = gold.get("span_histogram")
    if not isinstance(histogram, Mapping):
        raise TextGoldError(
            "histogram_missing",
            "Publish needle_span_chars with the gold before any score is read.",
        )
    refuse_all_short(histogram)
    return dict(histogram)


def _body(chunk: Mapping[str, Any]) -> str:
    return research._chunk_body_text(chunk)


def contain_bound_fact(body: str, needles: Mapping[str, Any]) -> bool:
    values = nonempty_needles(needles)
    if not values:
        return False
    folded = str(body or "").casefold()
    return all(value.casefold() in folded for value in values.values())


def needle_span_preserved(chunk: Mapping[str, Any], fact: Mapping[str, Any]) -> bool:
    """True only when usable offsets enclose [first needle, last needle].

    None offsets are false, never true (T0 hold-to).
    """
    if not text_chunking.offsets_usable(chunk):
        return False
    try:
        first = int(fact["needle_first_char"])
        last = int(fact["needle_last_char"])
        start = int(chunk["char_start"])
        end = int(chunk["char_end"])
    except (KeyError, TypeError, ValueError):
        return False
    return start <= first and last <= end


def _k_label(k: int) -> str:
    return str(int(k))


def _empty_k_counts() -> dict[str, int]:
    return {_k_label(k): 0 for k in RETRIEVAL_KS}


def _empty_k_sums() -> dict[str, float]:
    return {_k_label(k): 0.0 for k in RETRIEVAL_KS}


def _precision_means(sums: Mapping[str, float], n: int) -> dict[str, float]:
    return {key: (float(value) / n if n else 0.0) for key, value in sums.items()}


def _recall_rates(counts: Mapping[str, int], n: int) -> dict[str, float]:
    return {_k_label(int(key)): (int(value) / n if n else 0.0) for key, value in counts.items()}


def _k_int_map(src: Mapping[str, Any]) -> dict[str, int]:
    return {_k_label(k): int((src or {}).get(_k_label(k), 0)) for k in RETRIEVAL_KS}


def _k_float_map(src: Mapping[str, Any]) -> dict[str, float]:
    return {_k_label(k): float((src or {}).get(_k_label(k), 0.0)) for k in RETRIEVAL_KS}


def _add_k_map(
    dest: dict[str, float] | dict[str, int],
    src: Mapping[str, Any],
    *,
    as_float: bool = False,
) -> None:
    for key, value in src.items():
        label = _k_label(int(key))
        dest[label] = dest.get(label, 0.0 if as_float else 0) + (
            float(value) if as_float else int(value)
        )


def bm25_ranked(
    query: str,
    chunks: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Full BM25 ranking on chunk body. Same order as research._bm25_top5."""
    if not query or not chunks:
        return []
    rows = [{"text": _body(chunk)} for chunk in chunks]
    scores = research._bm25(research._tokens(query), rows)
    ranked = sorted(
        zip(scores, range(len(chunks)), chunks),
        key=lambda item: (-item[0], item[1]),
    )
    return [chunk for _, _, chunk in ranked]


def retrieval_at_ks(
    ranked: Sequence[Mapping[str, Any]],
    needles: Mapping[str, Any],
    ks: Sequence[int] = RETRIEVAL_KS,
) -> tuple[dict[str, bool], dict[str, float]]:
    """Recall@k is a hit if any top-k chunk binds. Precision@k is binds / k."""
    relevant = [contain_bound_fact(_body(chunk), needles) for chunk in ranked]
    retrievable: dict[str, bool] = {}
    precision: dict[str, float] = {}
    for k in ks:
        hits = sum(relevant[:k])
        retrievable[_k_label(k)] = bool(hits)
        precision[_k_label(k)] = (hits / k) if k else 0.0
    return retrievable, precision


def retrievable_at_5(
    query: str,
    chunks: Sequence[Mapping[str, Any]],
    needles: Mapping[str, Any],
) -> bool:
    """BM25 top-5 on chunk body. The k=5 point on the curve. Not dense."""
    ranked = bm25_ranked(query, chunks)
    retrievable, _precision = retrieval_at_ks(ranked, needles)
    return retrievable[_k_label(5)]


def fact_has_cross_hit(
    chunks: Sequence[Mapping[str, Any]],
    fact: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
) -> bool:
    """True when a binding chunk for this fact also binds a different fact."""
    needles = nonempty_needles(fact.get("needles") or {})
    if not needles:
        return False
    bound = [chunk for chunk in chunks if contain_bound_fact(_body(chunk), needles)]
    if not bound:
        return False
    for other in facts:
        if other is fact:
            continue
        other_needles = nonempty_needles(other.get("needles") or {})
        if not other_needles:
            continue
        if any(contain_bound_fact(_body(chunk), other_needles) for chunk in bound):
            return True
    return False


def cross_fact_hits(
    chunks: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
) -> int:
    """A bound chunk for fact A that also binds a different fact B."""
    return sum(1 for fact in facts if fact_has_cross_hit(chunks, fact, facts))


def _empty_bucket() -> dict[str, Any]:
    return {
        "n_facts": 0,
        "n_contain_bound_fact": 0,
        "n_retrievable_at_5": 0,
        "n_retrievable_at_k": _empty_k_counts(),
        "recall_at_k": _empty_k_sums(),
        "precision_at_k_sum": _empty_k_sums(),
        "precision_at_k": _empty_k_sums(),
        "cross_fact_hits": 0,
        "n_needle_span_preserved": 0,
    }


def score_strategy(
    chunks: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    *,
    strategy: str,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-bucket metrics. Does not emit an F1."""
    by_bucket = {bucket: _empty_bucket() for bucket in SPAN_BUCKETS}
    per_fact: list[dict[str, Any]] = []
    token_lens = [len(_body(chunk).split()) for chunk in chunks]
    for fact in facts:
        needles = nonempty_needles(fact.get("needles") or {})
        span = int(fact.get("needle_span_chars") or 0)
        bucket = span_bucket(span)
        contained = any(contain_bound_fact(_body(chunk), needles) for chunk in chunks)
        preserved = any(needle_span_preserved(chunk, fact) for chunk in chunks)
        ranked = bm25_ranked(str(fact.get("query") or ""), chunks)
        retrievable, precision = retrieval_at_ks(ranked, needles)
        retrieved = retrievable[_k_label(5)]
        crossed = fact_has_cross_hit(chunks, fact, facts)
        by_bucket[bucket]["n_facts"] += 1
        by_bucket[bucket]["n_contain_bound_fact"] += int(contained)
        by_bucket[bucket]["n_retrievable_at_5"] += int(retrieved)
        by_bucket[bucket]["n_needle_span_preserved"] += int(preserved)
        by_bucket[bucket]["cross_fact_hits"] += int(crossed)
        _add_k_map(by_bucket[bucket]["n_retrievable_at_k"], {
            key: int(hit) for key, hit in retrievable.items()
        })
        _add_k_map(by_bucket[bucket]["precision_at_k_sum"], precision, as_float=True)
        per_fact.append({
            "fact_id": fact.get("fact_id"),
            "bucket": bucket,
            "needle_span_chars": span,
            "contain_bound_fact": contained,
            "retrievable@5": retrieved,
            "retrievable_at_k": retrievable,
            "precision_at_k": precision,
            "needle_span_preserved": preserved,
        })
    n_facts = len(facts)
    retr_at_k = _empty_k_counts()
    prec_sums = _empty_k_sums()
    for row in by_bucket.values():
        n_bucket = int(row["n_facts"])
        row["precision_at_k"] = _precision_means(row["precision_at_k_sum"], n_bucket)
        row["recall_at_k"] = _recall_rates(row["n_retrievable_at_k"], n_bucket)
        _add_k_map(retr_at_k, row["n_retrievable_at_k"])
        _add_k_map(prec_sums, row["precision_at_k_sum"], as_float=True)
    return {
        "strategy": strategy,
        "params": dict(params or {}),
        "n_chunks": len(chunks),
        "token_count_min": min(token_lens) if token_lens else 0,
        "token_count_max": max(token_lens) if token_lens else 0,
        "token_count_mean": (sum(token_lens) / len(token_lens)) if token_lens else 0.0,
        "cross_fact_hits": sum(int(row["cross_fact_hits"]) for row in by_bucket.values()),
        "by_bucket": by_bucket,
        "facts": per_fact,
        "retrieval_ks": list(RETRIEVAL_KS),
        # Headline totals are diagnostic only. The reported result is by_bucket.
        "n_contain_bound_fact": sum(row["n_contain_bound_fact"] for row in by_bucket.values()),
        "n_retrievable_at_5": retr_at_k[_k_label(5)],
        "n_retrievable_at_k": retr_at_k,
        "recall_at_k": _recall_rates(retr_at_k, n_facts),
        "precision_at_k_sum": prec_sums,
        "precision_at_k": _precision_means(prec_sums, n_facts),
        "n_needle_span_preserved": sum(row["n_needle_span_preserved"] for row in by_bucket.values()),
        "n_facts": n_facts,
        "f1": None,
    }


def run_arm(
    canonical: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    *,
    strategy: str,
    params: Mapping[str, Any],
    chunks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    paper_id = (
        canonical.get("source_pdf_sha256")
        or canonical.get("pdf_sha256")
        or canonical.get("paper_sha256")
    )
    tagged = [fact for fact in facts if fact.get("paper_sha256")]
    if tagged:
        # A paper with zero kept facts scores nothing. Do not fall back to
        # the rest of the gold — that doubles pooled n_facts.
        paper_facts = [fact for fact in tagged if fact.get("paper_sha256") == paper_id]
    else:
        paper_facts = list(facts)
    scored = score_strategy(chunks, paper_facts, strategy=strategy, params=params)
    scored["n_offsets_usable"] = sum(1 for chunk in chunks if text_chunking.offsets_usable(chunk))
    scored["n_offsets_none"] = sum(
        1 for chunk in chunks
        if chunk.get("char_start") is None or chunk.get("char_end") is None
    )
    return scored


def t1_t2_control_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """T2 vs T1 at matched size and overlap. A tie means T2 bought nothing."""
    t1 = {
        (row["params"].get("size"), row["params"].get("overlap_frac")): row
        for row in results
        if row.get("strategy") == "T1"
    }
    pairs = []
    for row in results:
        if row.get("strategy") != "T2":
            continue
        key = (row["params"].get("size"), row["params"].get("overlap_frac"))
        control = t1.get(key)
        if control is None:
            continue
        pairs.append({
            "size": key[0],
            "overlap_frac": key[1],
            "t1": {
                "n_contain_bound_fact": control["n_contain_bound_fact"],
                "n_retrievable_at_5": control["n_retrievable_at_5"],
                "n_retrievable_at_k": control["n_retrievable_at_k"],
                "precision_at_k": control["precision_at_k"],
                "n_needle_span_preserved": control["n_needle_span_preserved"],
                "cross_fact_hits": control["cross_fact_hits"],
                "n_chunks": control["n_chunks"],
                "by_bucket": control["by_bucket"],
            },
            "t2": {
                "n_contain_bound_fact": row["n_contain_bound_fact"],
                "n_retrievable_at_5": row["n_retrievable_at_5"],
                "n_retrievable_at_k": row["n_retrievable_at_k"],
                "precision_at_k": row["precision_at_k"],
                "n_needle_span_preserved": row["n_needle_span_preserved"],
                "cross_fact_hits": row["cross_fact_hits"],
                "n_chunks": row["n_chunks"],
                "by_bucket": row["by_bucket"],
            },
            "t2_beats_t1_contain": row["n_contain_bound_fact"] > control["n_contain_bound_fact"],
            "tied_contain": row["n_contain_bound_fact"] == control["n_contain_bound_fact"],
        })
    return pairs


def enumerate_arms(canonical: Mapping[str, Any]) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    """All T0–T6 arms. T6 encoder is the pinned production path."""
    arms: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    arms.append(("T0", {"target": text_chunking.T0_TARGET, "overlap": text_chunking.T0_OVERLAP},
                 text_chunking.chunk_t0(canonical)))
    for size, frac in text_chunking.t1_grid():
        arms.append(("T1", {"size": size, "overlap_frac": frac},
                     text_chunking.chunk_t1(canonical, size=size, overlap_frac=frac)))
    for size, frac in text_chunking.t1_control_pairs_for_t2():
        if (size, frac) not in text_chunking.t1_grid():
            arms.append(("T1", {"size": size, "overlap_frac": frac, "role": "t2_control"},
                         text_chunking.chunk_t1(canonical, size=size, overlap_frac=frac)))
    for size, frac in text_chunking.t2_grid():
        arms.append(("T2", {"size": size, "overlap_frac": frac},
                     text_chunking.chunk_t2(canonical, size=size, overlap_frac=frac)))
    for n_sentences in text_chunking.T3_SENTENCE_COUNTS:
        for stride in (n_sentences - 1, n_sentences - 2):
            if stride < 1:
                continue
            arms.append(("T3", {"n_sentences": n_sentences, "stride": stride},
                         text_chunking.chunk_t3(canonical, n_sentences=n_sentences, stride=stride)))
    arms.append(("T4", {"max_size": text_chunking.T4_MAX_SIZE}, text_chunking.chunk_t4(canonical)))
    arms.append(("T5", {"target": text_chunking.T5_TARGET}, text_chunking.chunk_t5(canonical)))
    for percentile in text_chunking.t6_grid():
        arms.append(("T6", {
            "percentile": percentile,
            "embedder": text_chunking.T6_EMBEDDER_ID,
            "embedder_role": text_chunking.T6_EMBEDDER_ROLE,
        }, text_chunking.chunk_t6(canonical, percentile=percentile)))
    return arms


def _merge_buckets(into: dict[str, dict[str, Any]], src: Mapping[str, Mapping[str, Any]]) -> None:
    for bucket, row in src.items():
        dest = into.setdefault(bucket, _empty_bucket())
        dest["n_facts"] += int(row.get("n_facts") or 0)
        dest["n_contain_bound_fact"] += int(row.get("n_contain_bound_fact") or 0)
        dest["n_retrievable_at_5"] += int(row.get("n_retrievable_at_5") or 0)
        dest["n_needle_span_preserved"] += int(row.get("n_needle_span_preserved") or 0)
        dest["cross_fact_hits"] += int(row.get("cross_fact_hits") or 0)
        _add_k_map(dest["n_retrievable_at_k"], row.get("n_retrievable_at_k") or {})
        _add_k_map(
            dest["precision_at_k_sum"],
            row.get("precision_at_k_sum") or {},
            as_float=True,
        )
        dest["precision_at_k"] = _precision_means(dest["precision_at_k_sum"], dest["n_facts"])
        dest["recall_at_k"] = _recall_rates(dest["n_retrievable_at_k"], dest["n_facts"])


def run_sweep(
    *,
    gold: Mapping[str, Any],
    canonicals: Sequence[Mapping[str, Any]],
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Score every T0–T6 arm. Histogram must already be on the gold."""
    histogram = require_histogram_before_score(gold)
    facts = list(gold.get("facts") or [])
    per_arm: list[dict[str, Any]] = []
    for canonical in canonicals:
        for strategy, params, chunks in enumerate_arms(canonical):
            scored = run_arm(canonical, facts, strategy=strategy, params=params, chunks=chunks)
            scored["paper_sha256"] = (
                canonical.get("source_pdf_sha256") or canonical.get("pdf_sha256")
            )
            per_arm.append(scored)
    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    for row in per_arm:
        key = (row["strategy"], json.dumps(row["params"], sort_keys=True))
        slot = pooled.setdefault(key, {
            "strategy": row["strategy"],
            "params": row["params"],
            "n_chunks": 0,
            "cross_fact_hits": 0,
            "n_contain_bound_fact": 0,
            "n_retrievable_at_5": 0,
            "n_retrievable_at_k": _empty_k_counts(),
            "recall_at_k": _empty_k_sums(),
            "precision_at_k_sum": _empty_k_sums(),
            "precision_at_k": _empty_k_sums(),
            "n_needle_span_preserved": 0,
            "n_facts": 0,
            "n_offsets_none": 0,
            "retrieval_ks": list(RETRIEVAL_KS),
            "by_bucket": {bucket: _empty_bucket() for bucket in SPAN_BUCKETS},
            "f1": None,
        })
        slot["n_chunks"] += row["n_chunks"]
        slot["cross_fact_hits"] += row["cross_fact_hits"]
        slot["n_contain_bound_fact"] += row["n_contain_bound_fact"]
        slot["n_needle_span_preserved"] += row["n_needle_span_preserved"]
        slot["n_facts"] += row["n_facts"]
        slot["n_offsets_none"] += row["n_offsets_none"]
        _add_k_map(slot["n_retrievable_at_k"], row.get("n_retrievable_at_k") or {})
        _add_k_map(
            slot["precision_at_k_sum"],
            row.get("precision_at_k_sum") or {},
            as_float=True,
        )
        slot["n_retrievable_at_5"] = slot["n_retrievable_at_k"][_k_label(5)]
        slot["precision_at_k"] = _precision_means(slot["precision_at_k_sum"], slot["n_facts"])
        slot["recall_at_k"] = _recall_rates(slot["n_retrievable_at_k"], slot["n_facts"])
        _merge_buckets(slot["by_bucket"], row["by_bucket"])
    results = list(pooled.values())
    report = {
        "schema": "dissolve.text-chunk-sweep.v1",
        "spec_sha256": SPEC_SHA256,
        "span_histogram": histogram,
        "n_papers": len(canonicals),
        "n_facts": len(facts),
        "retrieval_ks": list(RETRIEVAL_KS),
        "arms": results,
        "t1_vs_t2": t1_t2_control_rows(results),
        "f1": None,
        "per_paper_arms": per_arm,
    }
    if out_path is not None:
        path = Path(out_path)
        if path.resolve() in {
            POINT_SWEEP_PATH.resolve(),
            PROBE_SWEEP_PATH.resolve(),
            GOLD_UNSEALED_PATH.resolve(),
        }:
            raise TextGoldError(
                "protected_persist",
                "Do not overwrite the point sweep, probe, or unsealed gold.",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


def _series_from_arm(arm: Mapping[str, Any], bucket: str) -> dict[str, Any]:
    if bucket == "all":
        n_facts = int(arm.get("n_facts") or 0)
        n_retr = _k_int_map(arm.get("n_retrievable_at_k") or {})
        precision = _k_float_map(arm.get("precision_at_k") or {})
        contain = int(arm.get("n_contain_bound_fact") or 0)
        preserved = int(arm.get("n_needle_span_preserved") or 0)
        cross = int(arm.get("cross_fact_hits") or 0)
    else:
        row = (arm.get("by_bucket") or {}).get(bucket) or _empty_bucket()
        n_facts = int(row.get("n_facts") or 0)
        n_retr = _k_int_map(row.get("n_retrievable_at_k") or {})
        precision = _k_float_map(row.get("precision_at_k") or {})
        contain = int(row.get("n_contain_bound_fact") or 0)
        preserved = int(row.get("n_needle_span_preserved") or 0)
        cross = int(row.get("cross_fact_hits") or 0)
    return {
        "strategy": arm["strategy"],
        "params": dict(arm.get("params") or {}),
        "bucket": bucket,
        "n_facts": n_facts,
        "n_chunks": int(arm.get("n_chunks") or 0),
        "n_contain_bound_fact": contain,
        "n_needle_span_preserved": preserved,
        "n_retrievable_at_k": n_retr,
        "recall_at_k": _recall_rates(n_retr, n_facts),
        "precision_at_k": precision,
        "cross_fact_hits": cross,
    }


def t1_t2_curve_rows(arms: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """T2 vs T1 at each k and each span bucket, including all."""
    t1 = {
        (row["params"].get("size"), row["params"].get("overlap_frac")): row
        for row in arms
        if row.get("strategy") == "T1"
    }
    pairs = []
    for row in arms:
        if row.get("strategy") != "T2":
            continue
        key = (row["params"].get("size"), row["params"].get("overlap_frac"))
        control = t1.get(key)
        if control is None:
            continue
        for bucket in SERIES_BUCKETS:
            left = _series_from_arm(control, bucket)
            right = _series_from_arm(row, bucket)
            beats = {
                _k_label(k): right["n_retrievable_at_k"][_k_label(k)]
                > left["n_retrievable_at_k"][_k_label(k)]
                for k in RETRIEVAL_KS
            }
            pairs.append({
                "size": key[0],
                "overlap_frac": key[1],
                "bucket": bucket,
                "t1": {
                    "n_facts": left["n_facts"],
                    "n_chunks": left["n_chunks"],
                    "n_contain_bound_fact": left["n_contain_bound_fact"],
                    "n_needle_span_preserved": left["n_needle_span_preserved"],
                    "n_retrievable_at_k": left["n_retrievable_at_k"],
                    "recall_at_k": left["recall_at_k"],
                    "precision_at_k": left["precision_at_k"],
                    "cross_fact_hits": left["cross_fact_hits"],
                },
                "t2": {
                    "n_facts": right["n_facts"],
                    "n_chunks": right["n_chunks"],
                    "n_contain_bound_fact": right["n_contain_bound_fact"],
                    "n_needle_span_preserved": right["n_needle_span_preserved"],
                    "n_retrievable_at_k": right["n_retrievable_at_k"],
                    "recall_at_k": right["recall_at_k"],
                    "precision_at_k": right["precision_at_k"],
                    "cross_fact_hits": right["cross_fact_hits"],
                },
                "t2_beats_t1_recall_at_k": beats,
            })
    return pairs


def build_retrieval_curves(
    *,
    gold: Mapping[str, Any],
    arms: Sequence[Mapping[str, Any]],
    gold_sha256: str,
    spec_sha256: str = SPEC_V2_SHA256,
) -> dict[str, Any]:
    """Addressable curve artifact. Counts and rates only. Not a sweep dump."""
    histogram = require_histogram_before_score(gold)
    series = [
        _series_from_arm(arm, bucket)
        for arm in arms
        for bucket in SERIES_BUCKETS
    ]
    return {
        "schema": CURVES_SCHEMA,
        "spec_sha256": spec_sha256,
        "ranker": "bm25_body",
        "embedder_in_retrieval": False,
        "retrieval_ks": list(RETRIEVAL_KS),
        "f1": None,
        "n_papers": int(gold.get("n_papers") or 0),
        "n_facts": int(gold.get("n_facts") or len(gold.get("facts") or [])),
        "gold_sha256": gold_sha256,
        "span_histogram": histogram,
        "series": series,
        "t1_vs_t2": t1_t2_curve_rows(arms),
    }


def emit_retrieval_curves(
    *,
    gold: Mapping[str, Any],
    canonicals: Sequence[Mapping[str, Any]],
    gold_path: Path,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Write CURVES.retrieval.v1.json. Does not overwrite the point sweep."""
    if GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    dest = Path(out_path or CURVES_PATH)
    protected = {
        POINT_SWEEP_PATH.resolve(),
        PROBE_SWEEP_PATH.resolve(),
        GOLD_UNSEALED_PATH.resolve(),
        CURVES_PATH.resolve(),
        CURVES_V1_PNG_PATH.resolve(),
        Path(gold_path).resolve(),
    }
    if dest.resolve() in protected:
        raise TextGoldError(
            "protected_persist",
            "CURVES.retrieval.v1.json is a new persist. Do not overwrite the point sweep.",
        )
    spec_digest = file_sha256(SPEC_V2_PATH)
    if spec_digest != SPEC_V2_SHA256:
        raise TextGoldError("spec_v2_moved", "Emit only against the ADMITTED v2 bytes.")
    gold_digest = file_sha256(Path(gold_path))
    before_point = file_sha256(POINT_SWEEP_PATH) if POINT_SWEEP_PATH.is_file() else ""
    before_probe = file_sha256(PROBE_SWEEP_PATH) if PROBE_SWEEP_PATH.is_file() else ""
    before_gold = gold_digest
    if before_point and before_point != POINT_SWEEP_SHA256:
        raise TextGoldError("point_sweep_moved", "Point sweep digest is not the v2 pin.")
    report = run_sweep(gold=gold, canonicals=canonicals, out_path=None)
    artifact = build_retrieval_curves(
        gold=gold,
        arms=report["arms"],
        gold_sha256=gold_digest,
        spec_sha256=spec_digest,
    )
    artifact["n_papers"] = len(canonicals)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    if POINT_SWEEP_PATH.is_file() and file_sha256(POINT_SWEEP_PATH) != before_point:
        raise TextGoldError("point_sweep_mutated", "Emit must not touch the point sweep.")
    if PROBE_SWEEP_PATH.is_file() and file_sha256(PROBE_SWEEP_PATH) != before_probe:
        raise TextGoldError("probe_sweep_mutated", "Emit must not touch the probe sweep.")
    if file_sha256(Path(gold_path)) != before_gold:
        raise TextGoldError("gold_mutated", "Emit must not touch the unsealed gold.")
    return artifact


def wilson_interval(x: int, n: int, z: float = WILSON_Z) -> dict[str, float | None]:
    """Wilson score interval. n==0 is null, not a fake 0.0."""
    if n <= 0:
        return {"lo": None, "hi": None}
    successes = int(x)
    trials = int(n)
    if successes < 0 or successes > trials:
        raise TextGoldError("wilson_x_out_of_range", "Wilson x must sit in 0..n.")
    phat = successes / trials
    z2 = float(z) * float(z)
    denom = 1.0 + z2 / trials
    center = (phat + z2 / (2.0 * trials)) / denom
    margin = (
        float(z)
        * math.sqrt(phat * (1.0 - phat) / trials + z2 / (4.0 * trials * trials))
        / denom
    )
    return {"lo": max(0.0, center - margin), "hi": min(1.0, center + margin)}


def intervals_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    lo_a, hi_a = left.get("lo"), left.get("hi")
    lo_b, hi_b = right.get("lo"), right.get("hi")
    if lo_a is None or hi_a is None or lo_b is None or hi_b is None:
        return False
    return float(lo_a) <= float(hi_b) and float(lo_b) <= float(hi_a)


def interval_separates_above(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    lo_a, hi_b = left.get("lo"), right.get("hi")
    if lo_a is None or hi_b is None:
        return False
    return float(lo_a) > float(hi_b)


def _v3_rate(count: int, n: int) -> float | None:
    if n <= 0:
        return None
    return int(count) / n


def _v3_rate_map(counts: Mapping[str, Any], n: int) -> dict[str, float | None]:
    return {_k_label(k): _v3_rate(int(counts.get(_k_label(k), 0)), n) for k in RETRIEVAL_KS}


def _v3_ci_map(counts: Mapping[str, Any], n: int) -> dict[str, dict[str, float | None]]:
    return {
        _k_label(k): wilson_interval(int(counts.get(_k_label(k), 0)), n)
        for k in RETRIEVAL_KS
    }


def _empty_k_null_rates() -> dict[str, float | None]:
    return {_k_label(k): None for k in RETRIEVAL_KS}


def _empty_k_null_cis() -> dict[str, dict[str, float | None]]:
    return {_k_label(k): {"lo": None, "hi": None} for k in RETRIEVAL_KS}


def iter_gold_facts(gold: Mapping[str, Any]) -> list[dict[str, Any]]:
    facts = [dict(row) for row in (gold.get("facts") or [])]
    if facts:
        return facts
    out: list[dict[str, Any]] = []
    for paper in gold.get("papers") or []:
        out.extend(dict(row) for row in (paper.get("facts") or []))
    return out


def split_gold(gold: Mapping[str, Any]) -> dict[str, Any]:
    """Index membership is gold paper_status + paper_sha256, never filename."""
    paper_status = {
        str(row.get("paper_sha256") or ""): str(row.get("paper_status") or "")
        for row in (gold.get("papers") or [])
        if row.get("paper_sha256")
    }
    indexed_shas = {sha for sha, status in paper_status.items() if status == "indexed"}
    held_shas = {sha for sha, status in paper_status.items() if status == "held_out"}
    facts = iter_gold_facts(gold)

    def _status(fact: Mapping[str, Any]) -> str:
        return str(fact.get("paper_status") or paper_status.get(str(fact.get("paper_sha256") or ""), ""))

    fire = [fact for fact in facts if _status(fact) == "indexed"]
    refuse = [fact for fact in facts if _status(fact) == "held_out"]
    return {
        "paper_status": paper_status,
        "indexed_shas": indexed_shas,
        "held_shas": held_shas,
        "facts": facts,
        "must_fire": fire,
        "must_refuse": refuse,
    }


def _paper_sha(canonical: Mapping[str, Any]) -> str:
    return str(
        canonical.get("source_pdf_sha256")
        or canonical.get("pdf_sha256")
        or canonical.get("paper_sha256")
        or ""
    )


def tag_chunks(
    chunks: Sequence[Mapping[str, Any]],
    paper_sha256: str,
) -> list[dict[str, Any]]:
    return [{**dict(chunk), "paper_sha256": paper_sha256} for chunk in chunks]


def build_index_i(
    chunks_by_paper: Mapping[str, Sequence[Mapping[str, Any]]],
    indexed_shas: Sequence[str] | set[str],
    paper_order: Sequence[str],
) -> list[dict[str, Any]]:
    """Concatenate indexed-paper chunks only. Hold-out SHAs never enter I."""
    allowed = set(indexed_shas)
    index: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sha in paper_order:
        if sha not in allowed or sha in seen:
            continue
        seen.add(sha)
        index.extend(tag_chunks(chunks_by_paper.get(sha) or [], sha))
    for sha in sorted(allowed - seen):
        index.extend(tag_chunks(chunks_by_paper.get(sha) or [], sha))
    if any(str(chunk.get("paper_sha256") or "") not in allowed for chunk in index):
        raise TextGoldError("held_out_in_index", "Index I contained a non-indexed paper_sha256.")
    return index


def count_value_collisions(
    held_out_facts: Sequence[Mapping[str, Any]],
    indexed_canonicals: Sequence[Mapping[str, Any]],
) -> tuple[int, bool]:
    """§6.5 diagnostic. Does not remint. Does not quote values."""
    blob = "\n".join(
        str(row.get("canonical_text") or "") for row in indexed_canonicals
    ).casefold()
    collisions = 0
    by_paper: dict[str, list[bool]] = {}
    for fact in held_out_facts:
        value = nonempty_needles(fact.get("needles") or {}).get("value", "")
        hit = bool(value) and value.casefold() in blob
        collisions += int(hit)
        by_paper.setdefault(str(fact.get("paper_sha256") or ""), []).append(hit)
    too_thin = False
    for flags in by_paper.values():
        if (len(flags) - sum(flags)) < 5:
            too_thin = True
    return collisions, too_thin


def _needles_in_text(text: str, needles: Mapping[str, Any]) -> bool:
    values = nonempty_needles(needles)
    if not values:
        return False
    folded = str(text or "").casefold()
    return all(value.casefold() in folded for value in values.values())


def _needles_in_recorded_window(text: str, fact: Mapping[str, Any]) -> bool:
    values = nonempty_needles(fact.get("needles") or {})
    if not values:
        return False
    try:
        first = int(fact["needle_first_char"])
        last = int(fact["needle_last_char"])
    except (KeyError, TypeError, ValueError):
        return False
    if last < first:
        return False
    span = int(fact.get("needle_span_chars") or 0)
    if span == (last - first + 1):
        window = str(text or "")[first:last + 1]
    else:
        window = str(text or "")[first:last]
    folded = window.casefold()
    return all(value.casefold() in folded for value in values.values())


def classify_k10_miss(
    fact: Mapping[str, Any],
    *,
    canonical_text: str,
    own_chunks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Exactly one class. Counts only in the persist; needles stay here."""
    needles = fact.get("needles") or {}
    span = int(fact.get("needle_span_chars") or 0)
    max_chunk = max((len(_body(chunk)) for chunk in own_chunks), default=0)
    contained = any(contain_bound_fact(_body(chunk), needles) for chunk in own_chunks)
    if not _needles_in_text(canonical_text, needles) or not _needles_in_recorded_window(
        canonical_text, fact,
    ):
        klass = "ambiguous_gold"
    elif span > max_chunk:
        klass = "span_exceeds_chunk"
    elif not contained:
        klass = "needles_split"
    else:
        klass = "lexical_mismatch"
    return {
        "fact_id": fact.get("fact_id"),
        "class": klass,
        "bucket": span_bucket(span),
        "needle_span_chars": span,
        "max_chunk_chars": max_chunk,
    }


def _arm_key(strategy: str, params: Mapping[str, Any]) -> tuple[str, str]:
    return (str(strategy), json.dumps(dict(params or {}), sort_keys=True))


def _arm_matches_named(strategy: str, params: Mapping[str, Any], named: tuple[str, tuple]) -> bool:
    if strategy != named[0]:
        return False
    want = dict(named[1])
    have = dict(params or {})
    return all(have.get(key) == value for key, value in want.items())


def _empty_v3_bucket() -> dict[str, Any]:
    return {
        "n_facts": 0,
        "n_held_out_facts": 0,
        "n_contain_bound_fact": 0,
        "n_needle_span_preserved": 0,
        "n_retrievable_at_k": _empty_k_counts(),
        "precision_at_k_sum": _empty_k_sums(),
        "n_must_refuse_at_k": _empty_k_counts(),
        "n_leaks_at_k": _empty_k_counts(),
        "cross_fact_hits": 0,
    }


def _finalize_v3_bucket(row: Mapping[str, Any], n_chunks: int) -> dict[str, Any]:
    n_fire = int(row.get("n_facts") or 0)
    n_hold = int(row.get("n_held_out_facts") or 0)
    retr = _k_int_map(row.get("n_retrievable_at_k") or {})
    refuse = _k_int_map(row.get("n_must_refuse_at_k") or {})
    leaks = _k_int_map(row.get("n_leaks_at_k") or {})
    recall_ci = _v3_ci_map(retr, n_fire)
    refuse_ci = _v3_ci_map(refuse, n_hold)
    hi5 = (recall_ci["5"] or {}).get("hi")
    null_retriever = (
        n_hold > 0
        and int(refuse.get("5") or 0) == n_hold
        and hi5 is not None
        and float(hi5) < 0.05
    )
    return {
        "n_facts": n_fire,
        "n_held_out_facts": n_hold,
        "n_chunks": int(n_chunks),
        "n_contain_bound_fact": int(row.get("n_contain_bound_fact") or 0),
        "n_needle_span_preserved": int(row.get("n_needle_span_preserved") or 0),
        "n_retrievable_at_k": retr,
        "recall_at_k": _v3_rate_map(retr, n_fire),
        "recall_ci_at_k": recall_ci,
        "precision_at_k": (
            _precision_means(row.get("precision_at_k_sum") or _empty_k_sums(), n_fire)
            if n_fire else _empty_k_null_rates()
        ),
        "n_must_refuse_at_k": refuse,
        "must_refuse_at_k": _v3_rate_map(refuse, n_hold),
        "must_refuse_ci_at_k": refuse_ci,
        "n_leaks_at_k": leaks,
        "cross_fact_hits": int(row.get("cross_fact_hits") or 0),
        "null_retriever": bool(null_retriever),
    }


def score_index_arm(
    *,
    strategy: str,
    params: Mapping[str, Any],
    index: Sequence[Mapping[str, Any]],
    chunks_by_paper: Mapping[str, Sequence[Mapping[str, Any]]],
    must_fire: Sequence[Mapping[str, Any]],
    must_refuse: Sequence[Mapping[str, Any]],
    all_facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Official v3 scores: one BM25 ranking over I for both sides."""
    held_shas = {str(fact.get("paper_sha256") or "") for fact in must_refuse}
    if any(str(chunk.get("paper_sha256") or "") in held_shas for chunk in index):
        raise TextGoldError("held_out_in_index", "A held-out paper_sha256 entered I.")
    fire_ids = {id(fact) for fact in must_fire}

    by_bucket = {bucket: _empty_v3_bucket() for bucket in SPAN_BUCKETS}
    fire_hits: dict[str, dict[str, bool]] = {}
    refuse_hits: dict[str, dict[str, bool]] = {}
    own_contain: dict[str, bool] = {}

    for fact in list(must_fire) + list(must_refuse):
        fact_id = str(fact.get("fact_id") or "")
        needles = nonempty_needles(fact.get("needles") or {})
        own = chunks_by_paper.get(str(fact.get("paper_sha256") or ""), [])
        own_contain[fact_id] = any(contain_bound_fact(_body(chunk), needles) for chunk in own)
        preserved = any(needle_span_preserved(chunk, fact) for chunk in own)
        ranked = bm25_ranked(str(fact.get("query") or ""), index)
        retrievable, precision = retrieval_at_ks(ranked, needles)
        bucket = span_bucket(int(fact.get("needle_span_chars") or 0))
        slot = by_bucket[bucket]
        if id(fact) in fire_ids:
            crossed = fact_has_cross_hit(index, fact, all_facts)
            slot["n_facts"] += 1
            slot["n_contain_bound_fact"] += int(own_contain[fact_id])
            slot["n_needle_span_preserved"] += int(preserved)
            slot["cross_fact_hits"] += int(crossed)
            _add_k_map(slot["n_retrievable_at_k"], {
                key: int(hit) for key, hit in retrievable.items()
            })
            _add_k_map(slot["precision_at_k_sum"], precision, as_float=True)
            fire_hits[fact_id] = retrievable
        else:
            slot["n_held_out_facts"] += 1
            refused = {key: (not bool(hit)) for key, hit in retrievable.items()}
            _add_k_map(slot["n_must_refuse_at_k"], {
                key: int(hit) for key, hit in refused.items()
            })
            _add_k_map(slot["n_leaks_at_k"], {
                key: int(not hit) for key, hit in refused.items()
            })
            refuse_hits[fact_id] = refused

    all_row = _empty_v3_bucket()
    for row in by_bucket.values():
        all_row["n_facts"] += row["n_facts"]
        all_row["n_held_out_facts"] += row["n_held_out_facts"]
        all_row["n_contain_bound_fact"] += row["n_contain_bound_fact"]
        all_row["n_needle_span_preserved"] += row["n_needle_span_preserved"]
        all_row["cross_fact_hits"] += row["cross_fact_hits"]
        _add_k_map(all_row["n_retrievable_at_k"], row["n_retrievable_at_k"])
        _add_k_map(all_row["precision_at_k_sum"], row["precision_at_k_sum"], as_float=True)
        _add_k_map(all_row["n_must_refuse_at_k"], row["n_must_refuse_at_k"])
        _add_k_map(all_row["n_leaks_at_k"], row["n_leaks_at_k"])

    n_chunks = len(index)
    series = []
    finalized_all = _finalize_v3_bucket(all_row, n_chunks)
    finalized_all.update({"strategy": strategy, "params": dict(params or {}), "bucket": "all"})
    series.append(finalized_all)
    for bucket in SPAN_BUCKETS:
        finalized = _finalize_v3_bucket(by_bucket[bucket], n_chunks)
        finalized.update({"strategy": strategy, "params": dict(params or {}), "bucket": bucket})
        series.append(finalized)
    return {
        "strategy": strategy,
        "params": dict(params or {}),
        "n_chunks": n_chunks,
        "series": series,
        "fire_hits": fire_hits,
        "refuse_hits": refuse_hits,
        "own_contain": own_contain,
    }


def _tie_groups_for_series(series: Sequence[Mapping[str, Any]], k: int) -> dict[str, Any]:
    rows = [row for row in series if row.get("bucket") == "all" and int(row.get("n_facts") or 0) > 0]
    labels = []
    cis = []
    for row in rows:
        labels.append({"strategy": row["strategy"], "params": dict(row.get("params") or {})})
        cis.append((row.get("recall_ci_at_k") or {}).get(_k_label(k)) or {"lo": None, "hi": None})
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if intervals_overlap(cis[i], cis[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[dict[str, Any]]] = {}
    for i, label in enumerate(labels):
        groups.setdefault(find(i), []).append(label)
    return {"bucket": "all", "side": "recall", "groups": list(groups.values())}


def t1_t2_v3_rows(series: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    t1 = {
        (
            row["params"].get("size"),
            row["params"].get("overlap_frac"),
            row.get("bucket"),
        ): row
        for row in series
        if row.get("strategy") == "T1"
    }
    pairs = []
    for row in series:
        if row.get("strategy") != "T2":
            continue
        key = (row["params"].get("size"), row["params"].get("overlap_frac"), row.get("bucket"))
        control = t1.get(key)
        if control is None:
            continue
        beats = {}
        tied = {}
        for k in RETRIEVAL_KS:
            label = _k_label(k)
            left = (control.get("recall_ci_at_k") or {}).get(label) or {"lo": None, "hi": None}
            right = (row.get("recall_ci_at_k") or {}).get(label) or {"lo": None, "hi": None}
            beats[label] = interval_separates_above(right, left)
            tied[label] = intervals_overlap(left, right)
        pairs.append({
            "size": key[0],
            "overlap_frac": key[1],
            "bucket": row.get("bucket"),
            "t1": {
                "n_facts": control.get("n_facts"),
                "n_held_out_facts": control.get("n_held_out_facts"),
                "n_chunks": control.get("n_chunks"),
                "n_retrievable_at_k": control.get("n_retrievable_at_k"),
                "recall_at_k": control.get("recall_at_k"),
                "recall_ci_at_k": control.get("recall_ci_at_k"),
                "must_refuse_at_k": control.get("must_refuse_at_k"),
                "must_refuse_ci_at_k": control.get("must_refuse_ci_at_k"),
                "cross_fact_hits": control.get("cross_fact_hits"),
            },
            "t2": {
                "n_facts": row.get("n_facts"),
                "n_held_out_facts": row.get("n_held_out_facts"),
                "n_chunks": row.get("n_chunks"),
                "n_retrievable_at_k": row.get("n_retrievable_at_k"),
                "recall_at_k": row.get("recall_at_k"),
                "recall_ci_at_k": row.get("recall_ci_at_k"),
                "must_refuse_at_k": row.get("must_refuse_at_k"),
                "must_refuse_ci_at_k": row.get("must_refuse_ci_at_k"),
                "cross_fact_hits": row.get("cross_fact_hits"),
            },
            "t2_beats_t1_recall_at_k": beats,
            "tied_recall_at_k": tied,
        })
    return pairs


def run_v3_sweep(
    *,
    gold: Mapping[str, Any],
    canonicals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    split = split_gold(gold)
    indexed_hist = span_histogram(split["must_fire"])
    if split["must_fire"]:
        refuse_all_short(indexed_hist)
    held_hist = span_histogram(split["must_refuse"])
    all_hist = require_histogram_before_score(gold)

    paper_order = [
        str(row.get("paper_sha256") or "")
        for row in (gold.get("papers") or [])
        if str(row.get("paper_status") or "") == "indexed"
    ]
    canonical_by_sha = {_paper_sha(row): row for row in canonicals}
    indexed_canonicals = [
        canonical_by_sha[sha] for sha in paper_order if sha in canonical_by_sha
    ]
    collisions, too_thin = count_value_collisions(split["must_refuse"], indexed_canonicals)

    # arm_key -> paper_sha -> chunks
    per_arm_chunks: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    arm_params: dict[tuple[str, str], dict[str, Any]] = {}
    for canonical in canonicals:
        sha = _paper_sha(canonical)
        for strategy, params, chunks in enumerate_arms(canonical):
            key = _arm_key(strategy, params)
            arm_params[key] = dict(params)
            per_arm_chunks.setdefault(key, {})[sha] = list(chunks)

    scored_arms = []
    series: list[dict[str, Any]] = []
    for key, by_paper in per_arm_chunks.items():
        strategy, _params_json = key
        params = arm_params[key]
        index = build_index_i(by_paper, split["indexed_shas"], paper_order)
        scored = score_index_arm(
            strategy=strategy,
            params=params,
            index=index,
            chunks_by_paper=by_paper,
            must_fire=split["must_fire"],
            must_refuse=split["must_refuse"],
            all_facts=split["facts"],
        )
        scored_arms.append(scored)
        series.extend(scored["series"])

    tie_groups = {_k_label(k): _tie_groups_for_series(series, k) for k in RETRIEVAL_KS}
    return {
        "split": split,
        "indexed_hist": indexed_hist,
        "held_hist": held_hist,
        "all_hist": all_hist,
        "n_value_collisions": collisions,
        "holdout_too_thin": too_thin,
        "scored_arms": scored_arms,
        "series": series,
        "tie_groups_at_k": tie_groups,
        "t1_vs_t2": t1_t2_v3_rows(series),
        "canonical_by_sha": canonical_by_sha,
        "per_arm_chunks": per_arm_chunks,
        "paper_order": paper_order,
    }


def build_retrieval_curves_v3(
    *,
    gold: Mapping[str, Any],
    report: Mapping[str, Any],
    gold_sha256: str,
    spec_sha256: str = SPEC_V3_SHA256,
) -> dict[str, Any]:
    split = report["split"]
    return {
        "schema": CURVES_V3_SCHEMA,
        "spec_sha256": spec_sha256,
        "ranker": "bm25_body",
        "index": "indexed_papers_only",
        "embedder_in_retrieval": False,
        "retrieval_ks": list(RETRIEVAL_KS),
        "ci_method": "wilson_score",
        "ci_level": 0.95,
        "z": WILSON_Z,
        "f1": None,
        "n_papers": int(gold.get("n_papers") or len(gold.get("papers") or [])),
        "n_indexed_papers": len(split["indexed_shas"]),
        "n_held_out_papers": len(split["held_shas"]),
        "n_facts": int(gold.get("n_facts") or len(split["facts"])),
        "n_indexed_facts": len(split["must_fire"]),
        "n_held_out_facts": len(split["must_refuse"]),
        "n_value_collisions": int(report["n_value_collisions"]),
        "holdout_too_thin": bool(report["holdout_too_thin"]),
        "gold_sha256": gold_sha256,
        "span_histogram": report["all_hist"],
        "span_histogram_indexed": report["indexed_hist"],
        "span_histogram_held_out": report["held_hist"],
        "series": report["series"],
        "tie_groups_at_k": report["tie_groups_at_k"],
        "t1_vs_t2": report["t1_vs_t2"],
    }


def _protected_v3_paths(gold_path: Path) -> dict[Path, str]:
    pins = {
        POINT_SWEEP_PATH: POINT_SWEEP_SHA256,
        PROBE_SWEEP_PATH: PROBE_SWEEP_SHA256,
        CURVE_SWEEP_PATH: CURVE_SWEEP_SHA256,
        CURVES_PATH: CURVES_V1_SHA256,
        CURVES_V1_PNG_PATH: CURVES_V1_PNG_SHA256,
        GOLD_UNSEALED_PATH: GOLD_UNSEALED_SHA256,
    }
    resolved = Path(gold_path).resolve()
    if resolved == GOLD_UNSEALED_PATH.resolve():
        pins[GOLD_UNSEALED_PATH] = GOLD_UNSEALED_SHA256
    return {path.resolve(): digest for path, digest in pins.items() if path.is_file()}


def _assert_protected_unmoved(pins: Mapping[Path, str], *, when: str) -> None:
    for path, digest in pins.items():
        if file_sha256(path) != digest:
            raise TextGoldError(
                "protected_persist_moved",
                f"Protected persist moved {when}.",
                path=str(path),
            )


def render_retrieval_png_v3(artifact: Mapping[str, Any], dest: Path) -> None:
    """Two panels: recall and must-refuse. Wilson bars. No leaked 21/257 title."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = [
        row for row in (artifact.get("series") or [])
        if row.get("bucket") == "all" and int(row.get("n_facts") or 0) > 0
    ]
    ks = list(RETRIEVAL_KS)
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharex=True)
    n_idx = int(artifact.get("n_indexed_papers") or 0)
    n_fire = int(artifact.get("n_indexed_facts") or 0)
    n_hold = int(artifact.get("n_held_out_facts") or 0)
    fig.suptitle(
        f"{n_idx} indexed papers, {n_fire} must-fire facts, {n_hold} must-refuse facts",
        fontsize=12,
    )
    highlight = []
    faint = []
    for row in series:
        if any(_arm_matches_named(row["strategy"], row["params"], named) for named in NAMED_ERROR_ARMS):
            highlight.append(row)
        else:
            faint.append(row)

    def _ys(row: Mapping[str, Any], key: str) -> list[float]:
        rates = row.get(key) or {}
        return [float(rates.get(_k_label(k)) or 0.0) for k in ks]

    def _yerr(row: Mapping[str, Any], ci_key: str, rate_key: str) -> list[list[float]]:
        rates = row.get(rate_key) or {}
        cis = row.get(ci_key) or {}
        down, up = [], []
        for k in ks:
            rate = rates.get(_k_label(k))
            ci = cis.get(_k_label(k)) or {}
            if rate is None or ci.get("lo") is None or ci.get("hi") is None:
                down.append(0.0)
                up.append(0.0)
            else:
                down.append(max(0.0, float(rate) - float(ci["lo"])))
                up.append(max(0.0, float(ci["hi"]) - float(rate)))
        return [down, up]

    def _label(row: Mapping[str, Any]) -> str:
        strategy = str(row.get("strategy"))
        params = dict(row.get("params") or {})
        if strategy == "T5":
            return "T5 block-pack"
        if strategy == "T6":
            return f"T6 p{params.get('percentile')}"
        if strategy == "T0":
            return "T0 production"
        if strategy == "T1":
            return f"T1 {params.get('size')} ov {params.get('overlap_frac')}"
        if strategy == "T2":
            return f"T2 {params.get('size')} ov {params.get('overlap_frac')}"
        return strategy

    ax0, ax1 = axes
    for row in faint:
        ax0.errorbar(
            ks, _ys(row, "recall_at_k"), yerr=_yerr(row, "recall_ci_at_k", "recall_at_k"),
            fmt="o-", color="0.75", alpha=0.45, linewidth=0.8, markersize=3, capsize=2,
        )
        ax1.errorbar(
            ks, _ys(row, "must_refuse_at_k"),
            yerr=_yerr(row, "must_refuse_ci_at_k", "must_refuse_at_k"),
            fmt="o-", color="0.75", alpha=0.45, linewidth=0.8, markersize=3, capsize=2,
        )
    for row in highlight:
        ax0.errorbar(
            ks, _ys(row, "recall_at_k"), yerr=_yerr(row, "recall_ci_at_k", "recall_at_k"),
            fmt="o-", linewidth=2, markersize=5, capsize=3, label=_label(row),
        )
        ax1.errorbar(
            ks, _ys(row, "must_refuse_at_k"),
            yerr=_yerr(row, "must_refuse_ci_at_k", "must_refuse_at_k"),
            fmt="o-", linewidth=2, markersize=5, capsize=3, label=_label(row),
        )
    ax0.set_title("Recall@k (must-fire, index I)")
    ax1.set_title("Must-refuse@k (held-out, same I)")
    ax0.set_ylabel("recall@k")
    ax1.set_ylabel("must_refuse@k")
    for ax in axes:
        ax.set_xlabel("k (retrieved chunks)")
        ax.set_xticks(ks)
        ax.set_ylim(-0.02, 1.05)
        ax.grid(True, alpha=0.3)
    ax0.legend(loc="lower right", fontsize=8, frameon=False)
    fig.tight_layout()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=140, facecolor="white")
    plt.close(fig)


def build_error_analysis_k10(
    *,
    artifact: Mapping[str, Any],
    report: Mapping[str, Any],
    spec_sha256: str,
    curves_sha256: str,
) -> dict[str, Any]:
    split = report["split"]
    canonical_by_sha = report["canonical_by_sha"]
    per_arm_chunks = report["per_arm_chunks"]
    fire_by_id = {str(fact.get("fact_id") or ""): fact for fact in split["must_fire"]}
    hold_by_id = {str(fact.get("fact_id") or ""): fact for fact in split["must_refuse"]}
    arms_out = []
    for scored in report["scored_arms"]:
        strategy = scored["strategy"]
        params = scored["params"]
        named = any(_arm_matches_named(strategy, params, named) for named in NAMED_ERROR_ARMS)
        if not named:
            continue
        all_row = next(row for row in scored["series"] if row["bucket"] == "all")
        misses = []
        for fact_id, retr in (scored.get("fire_hits") or {}).items():
            if retr.get("10"):
                continue
            fact = fire_by_id.get(fact_id)
            if fact is None:
                continue
            sha = str(fact.get("paper_sha256") or "")
            text = str((canonical_by_sha.get(sha) or {}).get("canonical_text") or "")
            own = (per_arm_chunks.get(_arm_key(strategy, params)) or {}).get(sha) or []
            misses.append(classify_k10_miss(fact, canonical_text=text, own_chunks=own))
        class_counts = {
            "ambiguous_gold": 0,
            "span_exceeds_chunk": 0,
            "needles_split": 0,
            "lexical_mismatch": 0,
        }
        for row in misses:
            class_counts[str(row["class"])] += 1
        leaks = [
            fact_id
            for fact_id, refused in (scored.get("refuse_hits") or {}).items()
            if not refused.get("10") and fact_id in hold_by_id
        ]
        arms_out.append({
            "strategy": strategy,
            "params": dict(params),
            "n_miss_at_10": len(misses),
            "class_counts": class_counts,
            "misses": misses,
            "refuse_leaks_at_10": leaks,
        })
    return {
        "schema": ERROR_ANALYSIS_SCHEMA,
        "spec_sha256": spec_sha256,
        "curves_sha256": curves_sha256,
        "k": 10,
        "n_value_collisions": int(report["n_value_collisions"]),
        "holdout_too_thin": bool(report["holdout_too_thin"]),
        "arms": arms_out,
    }


def emit_v3_product(
    *,
    gold: Mapping[str, Any],
    canonicals: Sequence[Mapping[str, Any]],
    gold_path: Path,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Write the three §6 files. Does not overwrite the leaked v2 persist."""
    if GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    dest_dir = Path(out_dir or DEFAULT_OUT_DIR)
    curves_path = dest_dir / "CURVES.retrieval.v3.json" if out_dir else CURVES_V3_PATH
    png_path = dest_dir / "CURVES.retrieval.v3.png" if out_dir else CURVES_V3_PNG_PATH
    err_path = dest_dir / "ERROR_ANALYSIS.k10.v3.json" if out_dir else ERROR_ANALYSIS_PATH
    protected = {
        POINT_SWEEP_PATH.resolve(),
        PROBE_SWEEP_PATH.resolve(),
        CURVE_SWEEP_PATH.resolve(),
        CURVES_PATH.resolve(),
        CURVES_V1_PNG_PATH.resolve(),
        GOLD_UNSEALED_PATH.resolve(),
        Path(gold_path).resolve(),
    }
    for dest in (curves_path, png_path, err_path):
        if dest.resolve() in protected:
            raise TextGoldError("protected_persist", "v3 emit must not overwrite the leaked persist.")
    spec_digest = file_sha256(SPEC_V3_PATH)
    if spec_digest != SPEC_V3_SHA256:
        raise TextGoldError("spec_v3_moved", "Emit only against the ADMITTED v3 bytes.")
    gold_digest = file_sha256(Path(gold_path))
    pins = _protected_v3_paths(Path(gold_path))
    _assert_protected_unmoved(pins, when="before emit")
    report = run_v3_sweep(gold=gold, canonicals=canonicals)
    artifact = build_retrieval_curves_v3(
        gold=gold,
        report=report,
        gold_sha256=gold_digest,
        spec_sha256=spec_digest,
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    render_retrieval_png_v3(artifact, png_path)
    curves_digest = file_sha256(curves_path)
    analysis = build_error_analysis_k10(
        artifact=artifact,
        report=report,
        spec_sha256=spec_digest,
        curves_sha256=curves_digest,
    )
    err_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False) + "\n")
    _assert_protected_unmoved(pins, when="after emit")
    if _contains_forbidden_keys(artifact, {"needles", "canonical_text", "evidence_quote", "query", "fact_id"}):
        raise TextGoldError("gold_quoted", "Curve persist must not carry gold text fields.")
    if _contains_forbidden_keys(analysis, {"needles", "canonical_text", "evidence_quote", "query"}):
        raise TextGoldError("gold_quoted", "Error analysis must not carry needles, queries, or quotes.")
    return {"curves": artifact, "error_analysis": analysis, "curves_path": str(curves_path)}


def _contains_forbidden_keys(obj: Any, forbidden: set[str]) -> bool:
    if isinstance(obj, Mapping):
        return any(key in forbidden or _contains_forbidden_keys(value, forbidden) for key, value in obj.items())
    if isinstance(obj, list):
        return any(_contains_forbidden_keys(item, forbidden) for item in obj)
    return False

