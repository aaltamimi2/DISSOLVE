"""P-1 gated-top-20 rerank curves. New name only. Does not overwrite v4."""

from __future__ import annotations

import importlib.util
import json
import statistics
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import dense_d2, engine_e2e, research, rerank, text_chunk_metrics
from .gold_ensemble import CENSUS_V3_SHA256, file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

_ORIGINAL_DENSE_VECTORS = research._dense_vectors

CURVES_R5RR_SCHEMA = "dissolve.text-chunk-curves.retrieval.r5rr.v1"
CURVES_R5RR_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.r5rr.v1.json"
DENSE_SPEC_SHA256 = dense_d2.DENSE_SPEC_SHA256
CURVES_V4_SHA256 = "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2"
RECALL_SPEC_SHA256 = {
    "v1": "2c66a709667cb332b7cbc0bc31610ef68edda03956b53a0583d790a0472c43fd",
    "v1.1": "849953d7a5be4ec5c55de062e3aa32d4b2260cfeeae2f414d45bd0ae7f5e36f1",
    "v1.2": "1807c6476772cef4e481e8072455a2758cd68706dda670eb879900a7217a49b0",
}
RECALL_SPEC_PATHS = {
    "v1": Path("/home/aaltamimi2/dissolve-v12-audit/RECALL_PROGRAM_SPEC.v1.md"),
    "v1.1": Path("/home/aaltamimi2/dissolve-v12-audit/RECALL_PROGRAM_SPEC.v1.1.md"),
    "v1.2": Path("/home/aaltamimi2/dissolve-v12-audit/RECALL_PROGRAM_SPEC.v1.2.md"),
}
GOLD_VALUE_GUARD_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/instruments/gold_value_guard.py")
V4_HYBRID_N_RETRIEVABLE = {"1": 147, "3": 188, "5": 201, "10": 209, "20": 220}
BARS_AT_5 = {
    "fixed_min": 12,
    "broken_max": 2,
    "net_min": 10,
    "n_retrievable_at_5_min": 211,
    "must_refuse_every_k": 29,
}
ARMS = ("rerank", "no_rerank", "shuffled")
ARM_MODES = {
    "rerank": "cross_encoder",
    "no_rerank": "off",
    "shuffled": "shuffle",
}
_FORBIDDEN_R5RR_KEYS = frozenset({"needles", "query", "evidence_quote", "canonical_text"})
_PROTECTED_NAMES = dense_d2._PROTECTED_NAMES | {
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.v4.png",
    "CURVES.retrieval.d3.json",
    "CURVES.retrieval.d3.png",
    "CURVES.retrieval.abstention.r1.json",
    "CURVES.retrieval.abstention.r3.json",
}


def pairwise_ids(arm_ids: Sequence[str], control_ids: Sequence[str]) -> dict[str, Any]:
    arm_set = set(arm_ids)
    control_set = set(control_ids)
    fixed = sorted(arm_set - control_set)
    broken = sorted(control_set - arm_set)
    return {"fixed": fixed, "broken": broken, "net": len(fixed) - len(broken)}


def meets_bar_at_5(fixed: Sequence[str], broken: Sequence[str]) -> bool:
    net = len(fixed) - len(broken)
    return (
        len(fixed) >= int(BARS_AT_5["fixed_min"])
        and len(broken) <= int(BARS_AT_5["broken_max"])
        and net >= int(BARS_AT_5["net_min"])
    )


def _assert_no_rerank_ident(row: Mapping[str, Any], n_hold: int) -> None:
    got = {
        str(k): int((row.get("n_retrievable_at_k") or {}).get(str(k), 0))
        for k in text_chunk_metrics.RETRIEVAL_KS
    }
    if got != {str(k): int(V4_HYBRID_N_RETRIEVABLE[str(k)]) for k in text_chunk_metrics.RETRIEVAL_KS}:
        raise TextGoldError(
            "no_rerank_ident",
            f"no-rerank n_retrievable_at_k is not IDENT to v4 hybrid ({got}).",
        )
    refuse = {
        str(k): int((row.get("n_must_refuse_at_k") or {}).get(str(k), 0))
        for k in text_chunk_metrics.RETRIEVAL_KS
    }
    want_refuse = {str(k): int(n_hold) for k in text_chunk_metrics.RETRIEVAL_KS}
    if refuse != want_refuse or n_hold != int(BARS_AT_5["must_refuse_every_k"]):
        raise TextGoldError("no_rerank_refuse", "no-rerank must-refuse is not 29/29.")
    leaks = {
        str(k): int((row.get("n_leaks_at_k") or {}).get(str(k), 0))
        for k in text_chunk_metrics.RETRIEVAL_KS
    }
    if any(value > 0 for value in leaks.values()):
        raise TextGoldError("no_rerank_leak", "no-rerank leaked on must-refuse.")


def _assert_shuffled_fails_bar(pair: Mapping[str, Any]) -> None:
    fixed = list(pair.get("fixed") or [])
    broken = list(pair.get("broken") or [])
    if meets_bar_at_5(fixed, broken):
        raise TextGoldError(
            "shuffled_bar",
            f"shuffled-reranker control must FAIL the P-1 bar (fixed={len(fixed)} broken={len(broken)} net={len(fixed) - len(broken)}).",
        )


def _assert_rerank_meets_bar(pair: Mapping[str, Any], row: Mapping[str, Any], n_hold: int) -> None:
    fixed = list(pair.get("fixed") or [])
    broken = list(pair.get("broken") or [])
    if not meets_bar_at_5(fixed, broken):
        raise TextGoldError(
            "rerank_bar",
            f"rerank arm did not meet the frozen k=5 bar (fixed={len(fixed)} broken={len(broken)} net={len(fixed) - len(broken)} n5={int((row.get('n_retrievable_at_k') or {}).get('5', 0))}).",
        )
    refuse = {
        str(k): int((row.get("n_must_refuse_at_k") or {}).get(str(k), 0))
        for k in text_chunk_metrics.RETRIEVAL_KS
    }
    if any(value != int(n_hold) for value in refuse.values()):
        raise TextGoldError("rerank_refuse", "rerank must-refuse is not 29/29.")
    leaks = {
        str(k): int((row.get("n_leaks_at_k") or {}).get(str(k), 0))
        for k in text_chunk_metrics.RETRIEVAL_KS
    }
    if any(value > 0 for value in leaks.values()):
        raise TextGoldError("rerank_leak", "rerank leaked on must-refuse.")


def _assert_tg_ids(ids: Sequence[str], label: str) -> None:
    for item in ids:
        if not str(item).startswith("tg-"):
            raise TextGoldError("id_not_tg", f"{label} must be tg- ids only.")


def _latency_summary(samples: Sequence[float]) -> dict[str, float | int | None]:
    n = len(samples)
    if not n:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(float(value) for value in samples)
    def pct(p: float) -> float:
        if n == 1:
            return ordered[0]
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return ordered[idx]
    return {
        "n": n,
        "mean": statistics.fmean(ordered),
        "p50": pct(50),
        "p95": pct(95),
        "max": ordered[-1],
    }


def _rank_chunks(
    index: Mapping[str, Any],
    query: str,
    rerank_mode: str,
) -> list[Mapping[str, Any]]:
    chunks = list(index.get("chunks") or [])
    rows = research._search_index(
        index, query, max(1, len(chunks)), "hybrid", rerank_mode=rerank_mode,
    )
    by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
    return [by_id[str(row["chunk_id"])] for row in rows if str(row.get("chunk_id")) in by_id]


def _measured_rankers(index: Mapping[str, Any], queries: Sequence[str]) -> dict[str, Any]:
    model = str((index.get("dense") or {}).get("model") or engine_e2e.MINILM_ID)
    unique = [query for query in dict.fromkeys(str(item) for item in queries) if query]
    previous = research._dense_vectors
    research._dense_vectors = _ORIGINAL_DENSE_VECTORS
    try:
        loaded_id, vectors = research._dense_vectors(unique, model) if unique else (model, [])
    finally:
        research._dense_vectors = previous
    table = {query: vector for query, vector in zip(unique, vectors)}
    cache: dict[str, list[Mapping[str, Any]]] = {}

    def fake(texts, model_name=None):
        missing = [text for text in texts if text not in table]
        if missing:
            raise TextGoldError("query_embed_miss", "An r5rr query was not in the precomputed table.")
        return loaded_id, [table[text] for text in texts]

    def hybrid_off(idx: Mapping[str, Any], query: str) -> list[Mapping[str, Any]]:
        if query not in cache:
            cache[query] = _rank_chunks(idx, query, "off")
        return list(cache[query])

    previous = research._dense_vectors
    research._dense_vectors = fake
    try:
        for query in unique:
            hybrid_off(index, query)
    finally:
        research._dense_vectors = previous

    def make(mode: str):
        def rank(idx: Mapping[str, Any], query: str):
            previous = research._dense_vectors
            research._dense_vectors = fake
            try:
                chunks = hybrid_off(idx, query)
                if mode == "off":
                    return chunks
                return rerank.reorder_chunks(query, chunks, mode)
            finally:
                research._dense_vectors = previous

        return rank

    return {arm: make(ARM_MODES[arm]) for arm in ARMS}


def _score_facts(
    index: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    ranker,
) -> dict[str, Any]:
    hits = {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS}
    ids_at = {str(k): [] for k in text_chunk_metrics.RETRIEVAL_KS}
    mrr_sum = 0.0
    latencies: list[float] = []
    for fact in facts:
        needles = fact.get("needles") or {}
        t0 = time.perf_counter()
        ranked = ranker(index, str(fact.get("query") or ""))
        latencies.append(time.perf_counter() - t0)
        retrievable, _precision = text_chunk_metrics.retrieval_at_ks(ranked, needles)
        fact_id = str(fact.get("fact_id") or "")
        for k in text_chunk_metrics.RETRIEVAL_KS:
            label = str(k)
            if retrievable.get(label):
                hits[label] += 1
                if fact_id:
                    ids_at[label].append(fact_id)
        mrr_sum += text_chunk_metrics.mrr_at_k(ranked, needles, k=20)
    n = len(facts)
    return {
        "hits": hits,
        "ids_at": {label: sorted(values) for label, values in ids_at.items()},
        "mrr_at_20": (mrr_sum / n) if n else None,
        "latency_s": _latency_summary(latencies),
    }


def _series_row(
    *,
    arm: str,
    fire: Mapping[str, Any],
    refuse: Mapping[str, Any],
    n_fire: int,
    n_hold: int,
    n_chunks: int,
) -> dict[str, Any]:
    n_retr = fire["hits"]
    n_leaks = refuse["hits"]
    n_refuse = {label: n_hold - int(n_leaks[label]) for label in n_retr}
    return {
        "arm": arm,
        "strategy": "T5",
        "params": {"target": 1400},
        "bucket": "all",
        "ranker": "hybrid",
        "rerank_mode": ARM_MODES[arm],
        "shipped": arm == "rerank",
        "n_facts": n_fire,
        "n_held_out_facts": n_hold,
        "n_chunks": n_chunks,
        "n_retrievable_at_k": n_retr,
        "recall_at_k": {
            label: text_chunk_metrics._v3_rate(n_retr[label], n_fire) for label in n_retr
        },
        "recall_ci_at_k": {
            label: text_chunk_metrics.wilson_interval(n_retr[label], n_fire) for label in n_retr
        },
        "n_must_refuse_at_k": n_refuse,
        "must_refuse_at_k": {
            label: text_chunk_metrics._v3_rate(n_refuse[label], n_hold) for label in n_refuse
        },
        "must_refuse_ci_at_k": {
            label: text_chunk_metrics.wilson_interval(n_refuse[label], n_hold) for label in n_refuse
        },
        "n_leaks_at_k": n_leaks,
        "mrr_at_20": fire["mrr_at_20"],
        "hits_at_5_ids": list(fire["ids_at"]["5"]),
        "hits_at_1_ids": list(fire["ids_at"]["1"]),
        "latency_s": fire["latency_s"],
    }


def apply_gold_value_guard(artifact: Mapping[str, Any]) -> dict[str, Any]:
    path = GOLD_VALUE_GUARD_PATH
    if not path.is_file():
        return {"landed": False, "violations": None}
    spec = importlib.util.spec_from_file_location("dissolve_gold_value_guard", path)
    if spec is None or spec.loader is None:
        raise TextGoldError("gold_value_guard_load", "gold_value_guard.py landed but could not load.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "violation_count", None) or getattr(module, "count_violations", None)
    if fn is None:
        raise TextGoldError("gold_value_guard_api", "gold_value_guard.py landed without a count API.")
    count = int(fn(artifact))
    if count:
        raise TextGoldError("gold_quoted_value", "artifact values contain gold text")
    return {"landed": True, "violations": count}


def build_r5rr_artifact(
    *,
    gold: Mapping[str, Any],
    index: Mapping[str, Any],
    pins: Mapping[str, str],
    rankers: Mapping[str, Any] | None = None,
    enforce_live_gates: bool = False,
) -> dict[str, Any]:
    split = text_chunk_metrics.split_gold(gold)
    n_fire = len(split["must_fire"])
    n_hold = len(split["must_refuse"])
    n_chunks = len(index.get("chunks") or [])
    queries = [str(fact.get("query") or "") for fact in split["must_fire"] + split["must_refuse"]]
    used = dict(rankers) if rankers is not None else _measured_rankers(index, queries)
    rows: dict[str, Any] = {}
    series: list[dict[str, Any]] = []
    for arm in ARMS:
        if arm not in used:
            raise TextGoldError("missing_arm_ranker", f"r5rr ranker missing for {arm}.")
        fire = _score_facts(index, split["must_fire"], used[arm])
        refuse = _score_facts(index, split["must_refuse"], used[arm])
        row = _series_row(
            arm=arm, fire=fire, refuse=refuse,
            n_fire=n_fire, n_hold=n_hold, n_chunks=n_chunks,
        )
        rows[arm] = row
        series.append(row)
    control_ids = list(rows["no_rerank"]["hits_at_5_ids"])
    versus = {
        "no_rerank": {"fixed": [], "broken": [], "net": 0},
        "rerank": pairwise_ids(rows["rerank"]["hits_at_5_ids"], control_ids),
        "shuffled": pairwise_ids(rows["shuffled"]["hits_at_5_ids"], control_ids),
    }
    versus["rerank"]["recall_at_1"] = pairwise_ids(
        rows["rerank"]["hits_at_1_ids"], rows["no_rerank"]["hits_at_1_ids"],
    )
    versus["rerank"]["mrr_at_20_delta"] = (
        None
        if rows["rerank"]["mrr_at_20"] is None or rows["no_rerank"]["mrr_at_20"] is None
        else float(rows["rerank"]["mrr_at_20"]) - float(rows["no_rerank"]["mrr_at_20"])
    )
    versus["shuffled"]["recall_at_1"] = pairwise_ids(
        rows["shuffled"]["hits_at_1_ids"], rows["no_rerank"]["hits_at_1_ids"],
    )
    versus["rerank"]["bar_met"] = meets_bar_at_5(versus["rerank"]["fixed"], versus["rerank"]["broken"])
    versus["shuffled"]["bar_met"] = meets_bar_at_5(versus["shuffled"]["fixed"], versus["shuffled"]["broken"])
    live = bool(enforce_live_gates)
    if live:
        _assert_no_rerank_ident(rows["no_rerank"], n_hold)
        _assert_shuffled_fails_bar(versus["shuffled"])
        for arm in ARMS:
            _assert_tg_ids(rows[arm]["hits_at_5_ids"], f"{arm}.hits_at_5")
            _assert_tg_ids(versus[arm]["fixed"], f"{arm}.fixed")
            _assert_tg_ids(versus[arm]["broken"], f"{arm}.broken")
        if n_hold != 29:
            raise TextGoldError("refuse_universe", "Refuse universe must stay 29 facts on 2 papers.")
        refuse_ok = all(
            int((rows[arm].get("n_must_refuse_at_k") or {}).get(str(k), 0)) == n_hold
            and int((rows[arm].get("n_leaks_at_k") or {}).get(str(k), 0)) == 0
            for arm in ARMS
            for k in text_chunk_metrics.RETRIEVAL_KS
        )
        if not refuse_ok:
            raise TextGoldError("refuse_leak", "An r5rr arm leaked or dropped must-refuse.")
    dense = index.get("dense") or {}
    artifact = {
        "schema": CURVES_R5RR_SCHEMA,
        "recall_spec_sha256": dict(RECALL_SPEC_SHA256),
        "spec_sha256": DENSE_SPEC_SHA256,
        "ranker": "hybrid_rerank_top20",
        "index": "indexed_papers_only",
        "embedder_in_retrieval": True,
        "embedder": str(dense.get("model") or engine_e2e.MINILM_ID),
        "dim": int(dense.get("dim") or engine_e2e.EXPECTED_DIM),
        "cross_encoder": rerank.CROSS_ENCODER_ID,
        "rerank_window": rerank.WINDOW,
        "shuffle_seed": rerank.SHUFFLE_SEED,
        "refuse_rule": "sparse_gated",
        "refuse_gate": "sparse_raw_score > 0",
        "hybrid_weights": {"dense": 0.55, "sparse": 0.40},
        "retrieval_ks": list(text_chunk_metrics.RETRIEVAL_KS),
        "ci_method": "wilson_score",
        "ci_level": 0.95,
        "z": text_chunk_metrics.WILSON_Z,
        "bars_at_5": dict(BARS_AT_5),
        "gold_sha256": pins["gold"],
        "census_sha256": pins["census"],
        "store_sha256": pins["store"],
        "curves_v4_sha256": pins["curves_v4"],
        "n_indexed_papers": len(split["indexed_shas"]),
        "n_held_out_papers": len(split["held_shas"]),
        "n_indexed_facts": n_fire,
        "n_held_out_facts": n_hold,
        "n_chunks": n_chunks,
        "series": series,
        "versus_no_rerank": versus,
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_R5RR_KEYS)):
        raise TextGoldError("gold_quoted", "r5rr must not carry needles, queries, or quotes.")
    artifact["gold_value_guard"] = apply_gold_value_guard(artifact)
    return artifact


def _pin_identity(
    *,
    gold_path: Path,
    census_path: Path,
    store_path: Path,
    v4_path: Path,
) -> dict[str, str]:
    pins = {
        "gold": file_sha256(gold_path),
        "census": file_sha256(census_path),
        "store": file_sha256(store_path),
        "curves_v4": file_sha256(v4_path),
    }
    if pins["gold"] != text_chunk_metrics.GOLD_UNSEALED_SHA256:
        raise TextGoldError("gold_sha_moved", "Unsealed gold moved; do not emit r5rr.")
    if pins["census"] != CENSUS_V3_SHA256:
        raise TextGoldError("census_sha_moved", "CENSUS.v3 moved; do not emit r5rr.")
    if pins["store"] != engine_e2e.STORE_SHA256:
        raise TextGoldError("store_sha_moved", "T5 store moved; do not emit r5rr.")
    if pins["curves_v4"] != CURVES_V4_SHA256:
        raise TextGoldError("v4_sha_moved", "v4 curves moved; r5rr must sit beside the pinned file.")
    for label, expected in RECALL_SPEC_SHA256.items():
        path = RECALL_SPEC_PATHS[label]
        digest = file_sha256(path)
        if digest != expected:
            raise TextGoldError("recall_spec_moved", f"RECALL spec {label} moved; do not emit r5rr.")
        pins[f"recall_{label}"] = digest
    if dense_d2.DENSE_SPEC_PATH.is_file() and file_sha256(dense_d2.DENSE_SPEC_PATH) != DENSE_SPEC_SHA256:
        raise TextGoldError("dense_spec_moved", "DENSE_RETRIEVAL_SPEC.v1 moved; do not emit r5rr.")
    return pins


def emit_r5rr_product(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    store_path: Path | None = None,
    v4_path: Path | None = None,
    index_path: Path | None = None,
    dest_dir: Path | None = None,
    rankers: Mapping[str, Any] | None = None,
    index: Mapping[str, Any] | None = None,
    enforce_live_gates: bool | None = None,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or engine_e2e.CENSUS_PATH)
    store_file = Path(store_path or engine_e2e.STORE_PATH)
    v4_file = Path(v4_path or dense_d2.CURVES_V4_PATH)
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    curves_path = out_dir / "CURVES.retrieval.r5rr.v1.json"
    if curves_path.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "r5rr must not overwrite a pinned persist name.")
    if curves_path.resolve() == v4_file.resolve():
        raise TextGoldError("v4_overwrite", "r5rr must not overwrite v4.")
    pins = _pin_identity(
        gold_path=gold_file,
        census_path=census_file,
        store_path=store_file,
        v4_path=v4_file,
    )
    gold = json.loads(gold_file.read_text())
    census = json.loads(census_file.read_text())
    store = json.loads(store_file.read_text())
    gold_sets = engine_e2e.gold_status_sets(gold)
    census_sets = engine_e2e.census_status_sets(census)
    store_shas = {str(sha) for sha in (store.get("indexed_paper_sha256") or [])}
    if gold_sets["indexed"] != census_sets["indexed"] or gold_sets["indexed"] != store_shas:
        raise TextGoldError("index_sha_mismatch", "Gold / census / store indexed SHA sets differ.")
    loaded = dict(index) if index is not None else dense_d2.load_gzip_index(index_path)
    dense_d2._assert_index_matches_store(store, loaded)
    v4_before = pins["curves_v4"]
    live = pins["gold"] == text_chunk_metrics.GOLD_UNSEALED_SHA256 if enforce_live_gates is None else bool(enforce_live_gates)
    artifact = build_r5rr_artifact(
        gold=gold,
        index=loaded,
        pins=pins,
        rankers=rankers,
        enforce_live_gates=live,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    if file_sha256(v4_file) != v4_before:
        raise TextGoldError("v4_mutated", "v4 digest moved during r5rr emit.")
    if file_sha256(gold_file) != pins["gold"]:
        raise TextGoldError("gold_mutated", "Gold digest moved during r5rr emit.")
    return {
        "curves_path": str(curves_path),
        "curves_v4_sha256": v4_before,
        "n_retrievable_at_k": (artifact["series"][0].get("n_retrievable_at_k") if artifact["series"] else {}),
        "versus_no_rerank": artifact["versus_no_rerank"],
        "gold_value_guard": artifact["gold_value_guard"],
    }
