"""D-2 hybrid curves beside v3. Sparse-gated MiniLM. No gold seal. No v3 overwrite."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import engine_e2e, research, text_chunk_metrics
from .gold_ensemble import CENSUS_V3_SHA256, file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

DENSE_SPEC_SHA256 = "20ccd8e054251a1f8f8b80b204a46bb201b4768d8c5d39b9188d0413ca7bcee6"
DENSE_SPEC_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/DENSE_RETRIEVAL_SPEC.v1.md")
CURVES_V4_SCHEMA = "dissolve.text-chunk-curves.retrieval.v4"
CURVES_V4_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json"
CURVES_V4_PNG_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v4.png"
ERROR_ANALYSIS_PATH = text_chunk_metrics.ERROR_ANALYSIS_PATH
INDEX_GZIP_PATH = DEFAULT_OUT_DIR / "indexes" / "t5-indexed-unsealed.json.gz"
_FORBIDDEN_V4_KEYS = frozenset({"needles", "query", "evidence_quote", "canonical_text"})
_PROTECTED_NAMES = frozenset({
    "CURVES.retrieval.v1.json",
    "CURVES.retrieval.v1.png",
    "CURVES.retrieval.v3.json",
    "CURVES.retrieval.v3.png",
    "CURVES.retrieval.T5.v1.json",
    "CURVES.retrieval.T5.v1.png",
    "GOLD.v2.json",
    "GOLD.text.v1.unsealed.json",
    "CHUNKS.t5.indexed.unsealed.v1.json",
    "ERROR_ANALYSIS.k10.v3.json",
})
Ranker = Callable[[Mapping[str, Any], str], list[Mapping[str, Any]]]


def load_gzip_index(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or INDEX_GZIP_PATH)
    with gzip.open(target, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _miss_bucket(klass: str) -> str | None:
    folded = str(klass or "").replace("-", "_").casefold()
    if "lexical" in folded and "mismatch" in folded:
        return "lexical_mismatch"
    if "needle" in folded and "split" in folded:
        return "needles_split"
    return None


def t5_miss_sets(analysis: Mapping[str, Any]) -> dict[str, set[str]]:
    arm = next(
        (
            row
            for row in (analysis.get("arms") or [])
            if row.get("strategy") == "T5" and (row.get("params") or {}).get("target") == 1400
        ),
        None,
    )
    if arm is None:
        raise TextGoldError("t5_arm_missing", "ERROR_ANALYSIS has no T5 target-1400 arm.")
    lexical: set[str] = set()
    split: set[str] = set()
    for miss in arm.get("misses") or []:
        fact_id = str(miss.get("fact_id") or "")
        bucket = _miss_bucket(str(miss.get("class") or ""))
        if not fact_id or bucket is None:
            continue
        if bucket == "lexical_mismatch":
            lexical.add(fact_id)
        else:
            split.add(fact_id)
    return {"lexical_mismatch": lexical, "needles_split": split}


def v3_t5_all_row(artifact: Mapping[str, Any]) -> dict[str, Any]:
    for row in artifact.get("series") or []:
        params = row.get("params") or {}
        if row.get("strategy") == "T5" and row.get("bucket") == "all" and params.get("target") == 1400:
            return dict(row)
    raise TextGoldError("v3_t5_row_missing", "v3 has no T5 all-bucket series.")


def hybrid_rank(
    index: Mapping[str, Any],
    query: str,
    *,
    search=research._search_index,
) -> list[Mapping[str, Any]]:
    chunks = list(index.get("chunks") or [])
    rows = search(index, query, max(1, len(chunks)), "hybrid")
    by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
    return [by_id[str(row["chunk_id"])] for row in rows if str(row.get("chunk_id")) in by_id]


def _cached_query_ranker(index: Mapping[str, Any], queries: Sequence[str]) -> Ranker:
    """One MiniLM encode of unique queries. Chunk vectors stay on the index."""
    model = str((index.get("dense") or {}).get("model") or engine_e2e.MINILM_ID)
    unique = list(dict.fromkeys(str(query) for query in queries))
    unique = [query for query in unique if query]
    loaded_id, vectors = research._dense_vectors(unique, model) if unique else (model, [])
    table = {query: vector for query, vector in zip(unique, vectors)}

    def fake(texts, model_name=None):
        missing = [text for text in texts if text not in table]
        if missing:
            raise TextGoldError("query_embed_miss", "A D-2 query was not in the precomputed table.")
        return loaded_id, [table[text] for text in texts]

    def rank(idx: Mapping[str, Any], query: str) -> list[Mapping[str, Any]]:
        previous = research._dense_vectors
        research._dense_vectors = fake
        try:
            return hybrid_rank(idx, query)
        finally:
            research._dense_vectors = previous

    return rank


def _score_side(
    index: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    ranker: Ranker,
) -> tuple[dict[str, int], set[str]]:
    hits = {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS}
    at_10: set[str] = set()
    for fact in facts:
        ranked = ranker(index, str(fact.get("query") or ""))
        retrievable, _precision = text_chunk_metrics.retrieval_at_ks(
            ranked, fact.get("needles") or {},
        )
        fact_id = str(fact.get("fact_id") or "")
        for k in text_chunk_metrics.RETRIEVAL_KS:
            label = str(k)
            if retrievable.get(label):
                hits[label] += 1
                if k == 10:
                    at_10.add(fact_id)
    return hits, at_10


def _compare_k(
    v3_row: Mapping[str, Any],
    hybrid_recall: Mapping[str, Any],
    hybrid_ci: Mapping[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    v3_recall = v3_row.get("recall_at_k") or {}
    v3_ci = v3_row.get("recall_ci_at_k") or {}
    for k in text_chunk_metrics.RETRIEVAL_KS:
        label = str(k)
        delta = float(hybrid_recall[label]) - float(v3_recall[label])
        tied = text_chunk_metrics.intervals_overlap(hybrid_ci[label], v3_ci.get(label) or {})
        out[label] = {
            "delta": delta,
            "verdict": "TIED" if tied else ("UP" if delta > 0 else "DOWN"),
        }
    return out


def build_v4_artifact(
    *,
    gold: Mapping[str, Any],
    index: Mapping[str, Any],
    v3: Mapping[str, Any],
    analysis: Mapping[str, Any],
    pins: Mapping[str, str],
    ranker: Ranker | None = None,
) -> dict[str, Any]:
    split = text_chunk_metrics.split_gold(gold)
    used = ranker
    if used is None:
        queries = [str(fact.get("query") or "") for fact in split["must_fire"] + split["must_refuse"]]
        used = _cached_query_ranker(index, queries)
    fire_hits, fire_at_10 = _score_side(index, split["must_fire"], used)
    refuse_hits, _refuse_at_10 = _score_side(index, split["must_refuse"], used)
    n_fire = len(split["must_fire"])
    n_hold = len(split["must_refuse"])
    n_retr = fire_hits
    n_refuse = {label: n_hold - int(refuse_hits[label]) for label in fire_hits}
    n_leaks = {label: int(refuse_hits[label]) for label in fire_hits}
    if any(n_leaks[label] > 0 for label in n_leaks):
        raise TextGoldError("refuse_leak", "Hybrid leaked on the must-refuse split.")
    recall = {
        label: text_chunk_metrics._v3_rate(n_retr[label], n_fire) for label in n_retr
    }
    recall_ci = {
        label: text_chunk_metrics.wilson_interval(n_retr[label], n_fire) for label in n_retr
    }
    refuse = {
        label: text_chunk_metrics._v3_rate(n_refuse[label], n_hold) for label in n_refuse
    }
    refuse_ci = {
        label: text_chunk_metrics.wilson_interval(n_refuse[label], n_hold) for label in n_refuse
    }
    v3_row = v3_t5_all_row(v3)
    miss_sets = t5_miss_sets(analysis)
    lexical = miss_sets["lexical_mismatch"]
    split_ids = miss_sets["needles_split"]
    recovered_lexical = sorted(lexical & fire_at_10)
    recovered_split = sorted(split_ids & fire_at_10)
    dense = index.get("dense") or {}
    artifact = {
        "schema": CURVES_V4_SCHEMA,
        "spec_sha256": DENSE_SPEC_SHA256,
        "ranker": "hybrid",
        "index": "indexed_papers_only",
        "embedder_in_retrieval": True,
        "embedder": str(dense.get("model") or engine_e2e.MINILM_ID),
        "dim": int(dense.get("dim") or engine_e2e.EXPECTED_DIM),
        "refuse_rule": "sparse_gated",
        "refuse_gate": "sparse_raw_score > 0",
        "hybrid_weights": {"dense": 0.55, "sparse": 0.40},
        "retrieval_ks": list(text_chunk_metrics.RETRIEVAL_KS),
        "ci_method": "wilson_score",
        "ci_level": 0.95,
        "z": text_chunk_metrics.WILSON_Z,
        "gold_sha256": pins["gold"],
        "census_sha256": pins["census"],
        "store_sha256": pins["store"],
        "curves_v3_sha256": pins["curves_v3"],
        "error_analysis_sha256": pins["error_analysis"],
        "n_indexed_papers": len(split["indexed_shas"]),
        "n_held_out_papers": len(split["held_shas"]),
        "n_indexed_facts": n_fire,
        "n_held_out_facts": n_hold,
        "n_chunks": len(index.get("chunks") or []),
        "series": [
            {
                "strategy": "T5",
                "params": {"target": 1400},
                "bucket": "all",
                "ranker": "hybrid",
                "n_facts": n_fire,
                "n_held_out_facts": n_hold,
                "n_chunks": len(index.get("chunks") or []),
                "n_retrievable_at_k": n_retr,
                "recall_at_k": recall,
                "recall_ci_at_k": recall_ci,
                "n_must_refuse_at_k": n_refuse,
                "must_refuse_at_k": refuse,
                "must_refuse_ci_at_k": refuse_ci,
                "n_leaks_at_k": n_leaks,
            }
        ],
        "versus_v3_t5": _compare_k(v3_row, recall, recall_ci),
        "recovery_at_10": {
            "lexical_mismatch_recovered_ids": recovered_lexical,
            "lexical_mismatch_recovered_count": len(recovered_lexical),
            "lexical_mismatch_set_size": len(lexical),
            "needles_split_recovered_ids": recovered_split,
            "needles_split_recovered_count": len(recovered_split),
            "needles_split_set_size": len(split_ids),
        },
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_V4_KEYS)):
        raise TextGoldError("gold_quoted", "v4 must not carry needles, queries, or quotes.")
    return artifact


def render_v4_png(artifact: Mapping[str, Any], dest: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    row = (artifact.get("series") or [None])[0]
    if not row:
        raise TextGoldError("v4_series_empty", "v4 has no series to plot.")
    ks = list(text_chunk_metrics.RETRIEVAL_KS)
    labels = [str(k) for k in ks]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharex=True)
    fig.suptitle("T5 hybrid (sparse-gated) vs BM25 v3", fontsize=12)
    recall = [float((row.get("recall_at_k") or {})[label]) for label in labels]
    refuse = [float((row.get("must_refuse_at_k") or {})[label]) for label in labels]
    axes[0].plot(ks, recall, marker="o", color="#54A24B", label="hybrid")
    axes[0].set_ylabel("recall@k")
    axes[1].plot(ks, refuse, marker="o", color="#E45756", label="hybrid")
    axes[1].set_ylabel("must_refuse@k")
    for ax in axes:
        ax.set_xlabel("k")
        ax.set_xticks(ks)
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.3)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(dest, dpi=120)
    plt.close(fig)


def _pin_identity(
    *,
    gold_path: Path,
    census_path: Path,
    store_path: Path,
    v3_path: Path,
    analysis_path: Path,
) -> dict[str, str]:
    pins = {
        "gold": file_sha256(gold_path),
        "census": file_sha256(census_path),
        "store": file_sha256(store_path),
        "curves_v3": file_sha256(v3_path),
        "error_analysis": file_sha256(analysis_path),
    }
    if pins["gold"] != text_chunk_metrics.GOLD_UNSEALED_SHA256:
        raise TextGoldError("gold_sha_moved", "Unsealed gold moved; do not emit D-2.")
    if pins["census"] != CENSUS_V3_SHA256:
        raise TextGoldError("census_sha_moved", "CENSUS.v3 moved; do not emit D-2.")
    if pins["store"] != engine_e2e.STORE_SHA256:
        raise TextGoldError("store_sha_moved", "T5 store moved; do not emit D-2.")
    if pins["curves_v3"] != engine_e2e.CURVES_V3_SHA256:
        raise TextGoldError("v3_sha_moved", "v3 curves moved; D-2 must sit beside the pinned file.")
    if pins["error_analysis"] != engine_e2e.ERROR_ANALYSIS_SHA256:
        raise TextGoldError("error_analysis_moved", "ERROR_ANALYSIS.k10.v3.json moved.")
    return pins


def _assert_index_matches_store(store: Mapping[str, Any], index: Mapping[str, Any]) -> None:
    store_ids = {str(chunk["chunk_id"]) for chunk in (store.get("chunks") or [])}
    index_ids = {str(chunk["chunk_id"]) for chunk in (index.get("chunks") or [])}
    dense_ids = {str(item) for item in ((index.get("dense") or {}).get("chunk_ids") or [])}
    if not store_ids or store_ids != index_ids or store_ids != dense_ids:
        raise TextGoldError("chunk_id_set_mismatch", "Gzip dense chunk_id set is not the store set.")


def emit_v4_product(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    store_path: Path | None = None,
    v3_path: Path | None = None,
    analysis_path: Path | None = None,
    index_path: Path | None = None,
    dest_dir: Path | None = None,
    ranker: Ranker | None = None,
    index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or engine_e2e.CENSUS_PATH)
    store_file = Path(store_path or engine_e2e.STORE_PATH)
    v3_file = Path(v3_path or text_chunk_metrics.CURVES_V3_PATH)
    analysis_file = Path(analysis_path or ERROR_ANALYSIS_PATH)
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    curves_path = out_dir / "CURVES.retrieval.v4.json"
    png_path = out_dir / "CURVES.retrieval.v4.png"
    for dest in (curves_path, png_path):
        if dest.name in _PROTECTED_NAMES:
            raise TextGoldError("protected_persist", "D-2 must not overwrite a pinned persist name.")
        if dest.resolve() == v3_file.resolve() or dest.resolve() == text_chunk_metrics.CURVES_V3_PNG_PATH.resolve():
            raise TextGoldError("v3_overwrite", "D-2 must not overwrite v3.")
    pins = _pin_identity(
        gold_path=gold_file,
        census_path=census_file,
        store_path=store_file,
        v3_path=v3_file,
        analysis_path=analysis_file,
    )
    gold = json.loads(gold_file.read_text())
    census = json.loads(census_file.read_text())
    store = json.loads(store_file.read_text())
    v3 = json.loads(v3_file.read_text())
    analysis = json.loads(analysis_file.read_text())
    gold_sets = engine_e2e.gold_status_sets(gold)
    census_sets = engine_e2e.census_status_sets(census)
    store_shas = {str(sha) for sha in (store.get("indexed_paper_sha256") or [])}
    if gold_sets["indexed"] != census_sets["indexed"] or gold_sets["indexed"] != store_shas:
        raise TextGoldError("index_sha_mismatch", "Gold / census / store indexed SHA sets differ.")
    loaded = dict(index) if index is not None else load_gzip_index(index_path)
    _assert_index_matches_store(store, loaded)
    v3_before = pins["curves_v3"]
    artifact = build_v4_artifact(
        gold=gold, index=loaded, v3=v3, analysis=analysis, pins=pins, ranker=ranker,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    render_v4_png(artifact, png_path)
    if file_sha256(v3_file) != v3_before:
        raise TextGoldError("v3_mutated", "v3 digest moved during D-2 emit.")
    recovery = artifact["recovery_at_10"]
    return {
        "curves_path": str(curves_path),
        "png_path": str(png_path),
        "curves_v3_sha256": v3_before,
        "lexical_mismatch_recovered_count": recovery["lexical_mismatch_recovered_count"],
        "lexical_mismatch_set_size": recovery["lexical_mismatch_set_size"],
        "needles_split_recovered_count": recovery["needles_split_recovered_count"],
        "needles_split_set_size": recovery["needles_split_set_size"],
        "n_leaks": 0,
        "versus_v3_t5": artifact["versus_v3_t5"],
    }
