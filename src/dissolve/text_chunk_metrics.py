"""TEXT_CHUNKING_SPEC.v1 sweep metrics. BM25 only. No blended F1.

Per strategy × needle_span_chars bucket. T1 is the explicit control for T2
at matched size and overlap. None offsets are not a preserved span.

retrievable@5 is one operating point. The curve is recall@k and precision@k
at k ∈ {1, 3, 5, 10, 20} from one BM25 ranking per fact.
"""

from __future__ import annotations

import json
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
)

SPEC_SHA256 = text_chunking.SPEC_SHA256
SPEC_V2_SHA256 = "2b6b776aec5a9146518051caee4b87e5dbd684eb1357776aa5655a0fa68eb8aa"
SPEC_V2_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/TEXT_CHUNKING_SPEC.v2.md")
RETRIEVAL_KS = (1, 3, 5, 10, 20)
CURVES_SCHEMA = "dissolve.text-chunk-curves.retrieval.v1"
CURVES_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v1.json"
POINT_SWEEP_PATH = DEFAULT_OUT_DIR / "SWEEP.t0_t6.v1.json"
PROBE_SWEEP_PATH = DEFAULT_OUT_DIR / "SWEEP.t0_t6.probe.v1.json"
GOLD_UNSEALED_PATH = DEFAULT_OUT_DIR / "GOLD.text.v1.unsealed.json"
GOLD_V2_PATH = DEFAULT_OUT_DIR / "GOLD.v2.json"
POINT_SWEEP_SHA256 = "dfc4c4707e7b43ecd3238ac9e3182049da646bc8383abd2e5a282e7b5611afd6"
SERIES_BUCKETS = ("all",) + SPAN_BUCKETS


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
