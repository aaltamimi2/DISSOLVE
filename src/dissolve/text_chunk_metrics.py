"""TEXT_CHUNKING_SPEC.v1 sweep metrics. BM25 only. No blended F1.

Per strategy × needle_span_chars bucket. T1 is the explicit control for T2
at matched size and overlap. None offsets are not a preserved span.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import research, text_chunking
from .text_gold import (
    SPAN_BUCKETS,
    TextGoldError,
    nonempty_needles,
    refuse_all_short,
    span_bucket,
)

SPEC_SHA256 = text_chunking.SPEC_SHA256


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


def retrievable_at_5(
    query: str,
    chunks: Sequence[Mapping[str, Any]],
    needles: Mapping[str, Any],
) -> bool:
    """BM25 top-5 on chunk body. Does not call _dense_vectors."""
    if not query or not chunks:
        return False
    top5 = research._bm25_top5(query, chunks)
    return any(contain_bound_fact(_body(chunk), needles) for chunk in top5)


def cross_fact_hits(
    chunks: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
) -> int:
    """A bound chunk for fact A that also binds a different fact B."""
    hits = 0
    for fact in facts:
        needles = nonempty_needles(fact.get("needles") or {})
        bound = [chunk for chunk in chunks if contain_bound_fact(_body(chunk), needles)]
        if not bound:
            continue
        for other in facts:
            if other is fact:
                continue
            other_needles = nonempty_needles(other.get("needles") or {})
            if not other_needles:
                continue
            if any(contain_bound_fact(_body(chunk), other_needles) for chunk in bound):
                hits += 1
                break
    return hits


def _empty_bucket() -> dict[str, int]:
    return {
        "n_facts": 0,
        "n_contain_bound_fact": 0,
        "n_retrievable_at_5": 0,
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
        retrieved = retrievable_at_5(str(fact.get("query") or ""), chunks, needles)
        by_bucket[bucket]["n_facts"] += 1
        by_bucket[bucket]["n_contain_bound_fact"] += int(contained)
        by_bucket[bucket]["n_retrievable_at_5"] += int(retrieved)
        by_bucket[bucket]["n_needle_span_preserved"] += int(preserved)
        per_fact.append({
            "fact_id": fact.get("fact_id"),
            "bucket": bucket,
            "needle_span_chars": span,
            "contain_bound_fact": contained,
            "retrievable@5": retrieved,
            "needle_span_preserved": preserved,
        })
    return {
        "strategy": strategy,
        "params": dict(params or {}),
        "n_chunks": len(chunks),
        "token_count_min": min(token_lens) if token_lens else 0,
        "token_count_max": max(token_lens) if token_lens else 0,
        "token_count_mean": (sum(token_lens) / len(token_lens)) if token_lens else 0.0,
        "cross_fact_hits": cross_fact_hits(chunks, facts),
        "by_bucket": by_bucket,
        "facts": per_fact,
        # Headline totals are diagnostic only. The reported result is by_bucket.
        "n_contain_bound_fact": sum(row["n_contain_bound_fact"] for row in by_bucket.values()),
        "n_retrievable_at_5": sum(row["n_retrievable_at_5"] for row in by_bucket.values()),
        "n_needle_span_preserved": sum(row["n_needle_span_preserved"] for row in by_bucket.values()),
        "n_facts": len(facts),
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
    paper_facts = [
        fact for fact in facts
        if not fact.get("paper_sha256") or fact.get("paper_sha256") == paper_id
    ]
    if not paper_facts:
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
                "n_needle_span_preserved": control["n_needle_span_preserved"],
                "cross_fact_hits": control["cross_fact_hits"],
                "n_chunks": control["n_chunks"],
                "by_bucket": control["by_bucket"],
            },
            "t2": {
                "n_contain_bound_fact": row["n_contain_bound_fact"],
                "n_retrievable_at_5": row["n_retrievable_at_5"],
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


def _merge_buckets(into: dict[str, dict[str, int]], src: Mapping[str, Mapping[str, int]]) -> None:
    for bucket, row in src.items():
        dest = into.setdefault(bucket, _empty_bucket())
        for key, value in row.items():
            dest[key] = dest.get(key, 0) + int(value)


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
            "n_needle_span_preserved": 0,
            "n_facts": 0,
            "n_offsets_none": 0,
            "by_bucket": {bucket: _empty_bucket() for bucket in SPAN_BUCKETS},
            "f1": None,
        })
        slot["n_chunks"] += row["n_chunks"]
        slot["cross_fact_hits"] += row["cross_fact_hits"]
        slot["n_contain_bound_fact"] += row["n_contain_bound_fact"]
        slot["n_retrievable_at_5"] += row["n_retrievable_at_5"]
        slot["n_needle_span_preserved"] += row["n_needle_span_preserved"]
        slot["n_facts"] += row["n_facts"]
        slot["n_offsets_none"] += row["n_offsets_none"]
        _merge_buckets(slot["by_bucket"], row["by_bucket"])
    results = list(pooled.values())
    report = {
        "schema": "dissolve.text-chunk-sweep.v1",
        "spec_sha256": SPEC_SHA256,
        "span_histogram": histogram,
        "n_papers": len(canonicals),
        "n_facts": len(facts),
        "arms": results,
        "t1_vs_t2": t1_t2_control_rows(results),
        "f1": None,
        "per_paper_arms": per_arm,
    }
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report
