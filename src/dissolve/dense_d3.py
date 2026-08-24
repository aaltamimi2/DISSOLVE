"""D-3 ablation: sparse / dense / hybrid. D-1 weights and refuse un-retuned. No gold seal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import dense_d2, engine_e2e, research, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

DENSE_SPEC_SHA256 = dense_d2.DENSE_SPEC_SHA256
CURVES_D3_SCHEMA = "dissolve.text-chunk-curves.retrieval.d3"
CURVES_D3_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json"
CURVES_D3_PNG_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.d3.png"
ARMS = ("sparse", "dense", "hybrid")
_FORBIDDEN_D3_KEYS = frozenset({"needles", "query", "evidence_quote", "canonical_text"})
_PROTECTED_NAMES = dense_d2._PROTECTED_NAMES | {
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.v4.png",
}


def rank_mode(
    index: Mapping[str, Any],
    query: str,
    mode: str,
    *,
    search=research._search_index,
) -> list[Mapping[str, Any]]:
    if mode not in ARMS:
        raise TextGoldError("unknown_arm", "D-3 arm must be sparse, dense, or hybrid.")
    chunks = list(index.get("chunks") or [])
    rows = search(index, query, max(1, len(chunks)), mode)
    by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
    return [by_id[str(row["chunk_id"])] for row in rows if str(row.get("chunk_id")) in by_id]


def _cached_mode_ranker(index: Mapping[str, Any], queries: list[str], mode: str):
    if mode == "sparse":
        def sparse_rank(idx: Mapping[str, Any], query: str):
            return rank_mode(idx, query, "sparse")

        return sparse_rank
    model = str((index.get("dense") or {}).get("model") or engine_e2e.MINILM_ID)
    unique = [query for query in dict.fromkeys(str(item) for item in queries) if query]
    loaded_id, vectors = research._dense_vectors(unique, model) if unique else (model, [])
    table = {query: vector for query, vector in zip(unique, vectors)}

    def fake(texts, model_name=None):
        missing = [text for text in texts if text not in table]
        if missing:
            raise TextGoldError("query_embed_miss", "A D-3 query was not in the precomputed table.")
        return loaded_id, [table[text] for text in texts]

    def rank(idx: Mapping[str, Any], query: str):
        previous = research._dense_vectors
        research._dense_vectors = fake
        try:
            return rank_mode(idx, query, mode)
        finally:
            research._dense_vectors = previous

    return rank


def _score_arm(
    *,
    gold: Mapping[str, Any],
    index: Mapping[str, Any],
    ranker,
) -> dict[str, Any]:
    split = text_chunk_metrics.split_gold(gold)
    fire_hits, _fire_at_10 = dense_d2._score_side(index, split["must_fire"], ranker)
    refuse_hits, _refuse_at_10 = dense_d2._score_side(index, split["must_refuse"], ranker)
    n_fire = len(split["must_fire"])
    n_hold = len(split["must_refuse"])
    n_retr = fire_hits
    n_refuse = {label: n_hold - int(refuse_hits[label]) for label in fire_hits}
    n_leaks = {label: int(refuse_hits[label]) for label in fire_hits}
    return {
        "n_facts": n_fire,
        "n_held_out_facts": n_hold,
        "n_chunks": len(index.get("chunks") or []),
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
    }


def _versus_arms(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in text_chunk_metrics.RETRIEVAL_KS:
        label = str(k)
        cell: dict[str, Any] = {}
        pairs = (("sparse", "dense"), ("sparse", "hybrid"), ("dense", "hybrid"))
        for left, right in pairs:
            left_recall = float((rows[left].get("recall_at_k") or {})[label])
            right_recall = float((rows[right].get("recall_at_k") or {})[label])
            delta = right_recall - left_recall
            tied = text_chunk_metrics.intervals_overlap(
                (rows[right].get("recall_ci_at_k") or {})[label],
                (rows[left].get("recall_ci_at_k") or {})[label],
            )
            cell[f"{right}_vs_{left}"] = {
                "delta": delta,
                "verdict": "TIED" if tied else ("UP" if delta > 0 else "DOWN"),
            }
        out[label] = cell
    return out


def build_d3_artifact(
    *,
    gold: Mapping[str, Any],
    index: Mapping[str, Any],
    pins: Mapping[str, str],
    rankers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    split = text_chunk_metrics.split_gold(gold)
    queries = [str(fact.get("query") or "") for fact in split["must_fire"] + split["must_refuse"]]
    used = dict(rankers or {})
    if "sparse" not in used or "dense" not in used or "hybrid" not in used:
        shared_queries = queries
        model = str((index.get("dense") or {}).get("model") or engine_e2e.MINILM_ID)
        unique = [query for query in dict.fromkeys(shared_queries) if query]
        loaded_id, vectors = research._dense_vectors(unique, model) if unique else (model, [])
        table = {query: vector for query, vector in zip(unique, vectors)}

        def fake(texts, model_name=None):
            return loaded_id, [table[text] for text in texts]

        def make(mode: str):
            if mode == "sparse":
                return lambda idx, query: rank_mode(idx, query, "sparse")

            def rank(idx: Mapping[str, Any], query: str):
                previous = research._dense_vectors
                research._dense_vectors = fake
                try:
                    return rank_mode(idx, query, mode)
                finally:
                    research._dense_vectors = previous

            return rank

        for mode in ARMS:
            used.setdefault(mode, make(mode))
    rows = {}
    for mode in ARMS:
        scored = _score_arm(gold=gold, index=index, ranker=used[mode])
        scored.update({
            "arm": mode,
            "strategy": "T5",
            "params": {"target": 1400},
            "bucket": "all",
            "ranker": mode,
            "embedder_in_retrieval": mode != "sparse",
            "refuse_rule": "sparse_gated",
            "hybrid_weights": {"dense": 0.55, "sparse": 0.40} if mode == "hybrid" else None,
        })
        rows[mode] = scored
    hybrid_leaks = rows["hybrid"]["n_leaks_at_k"]
    if any(int(hybrid_leaks[label]) > 0 for label in hybrid_leaks):
        raise TextGoldError("refuse_leak", "Hybrid leaked on the must-refuse split.")
    dense = index.get("dense") or {}
    artifact = {
        "schema": CURVES_D3_SCHEMA,
        "spec_sha256": DENSE_SPEC_SHA256,
        "index": "indexed_papers_only",
        "embedder": str(dense.get("model") or engine_e2e.MINILM_ID),
        "dim": int(dense.get("dim") or engine_e2e.EXPECTED_DIM),
        "refuse_rule": "sparse_gated",
        "refuse_gate": "sparse_raw_score > 0",
        "hybrid_weights": {"dense": 0.55, "sparse": 0.40},
        "weights_retuned": False,
        "retrieval_ks": list(text_chunk_metrics.RETRIEVAL_KS),
        "ci_method": "wilson_score",
        "ci_level": 0.95,
        "z": text_chunk_metrics.WILSON_Z,
        "gold_sha256": pins["gold"],
        "census_sha256": pins["census"],
        "store_sha256": pins["store"],
        "curves_v3_sha256": pins["curves_v3"],
        "curves_v4_sha256": pins.get("curves_v4"),
        "error_analysis_sha256": pins["error_analysis"],
        "n_indexed_papers": len(split["indexed_shas"]),
        "n_held_out_papers": len(split["held_shas"]),
        "n_indexed_facts": len(split["must_fire"]),
        "n_held_out_facts": len(split["must_refuse"]),
        "n_chunks": len(index.get("chunks") or []),
        "series": [rows[mode] for mode in ARMS],
        "versus_at_k": _versus_arms(rows),
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_D3_KEYS)):
        raise TextGoldError("gold_quoted", "D-3 must not carry needles, queries, or quotes.")
    return artifact


def render_d3_png(artifact: Mapping[str, Any], dest: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = list(artifact.get("series") or [])
    if len(series) != 3:
        raise TextGoldError("d3_series_missing", "D-3 needs three arms to plot.")
    ks = list(text_chunk_metrics.RETRIEVAL_KS)
    labels = [str(k) for k in ks]
    colors = {"sparse": "#4C78A8", "dense": "#F58518", "hybrid": "#54A24B"}
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharex=True)
    fig.suptitle("D-3 T5 ablation (sparse-gated; weights un-retuned)", fontsize=12)
    for row in series:
        arm = str(row.get("arm") or row.get("ranker") or "")
        color = colors.get(arm, "#9D755D")
        recall = [float((row.get("recall_at_k") or {})[label]) for label in labels]
        refuse = [float((row.get("must_refuse_at_k") or {})[label]) for label in labels]
        axes[0].plot(ks, recall, marker="o", color=color, label=arm)
        axes[1].plot(ks, refuse, marker="o", color=color, label=arm)
    axes[0].set_ylabel("recall@k")
    axes[1].set_ylabel("must_refuse@k")
    for ax in axes:
        ax.set_xlabel("k")
        ax.set_xticks(ks)
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.3)
        ax.legend()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(dest, dpi=120)
    plt.close(fig)


def emit_d3_product(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    store_path: Path | None = None,
    v3_path: Path | None = None,
    v4_path: Path | None = None,
    analysis_path: Path | None = None,
    index_path: Path | None = None,
    dest_dir: Path | None = None,
    rankers: Mapping[str, Any] | None = None,
    index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or engine_e2e.CENSUS_PATH)
    store_file = Path(store_path or engine_e2e.STORE_PATH)
    v3_file = Path(v3_path or text_chunk_metrics.CURVES_V3_PATH)
    v4_file = Path(v4_path or dense_d2.CURVES_V4_PATH)
    analysis_file = Path(analysis_path or dense_d2.ERROR_ANALYSIS_PATH)
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    curves_path = out_dir / "CURVES.retrieval.d3.json"
    png_path = out_dir / "CURVES.retrieval.d3.png"
    protected = {
        v3_file.resolve(),
        text_chunk_metrics.CURVES_V3_PNG_PATH.resolve(),
        v4_file.resolve(),
        dense_d2.CURVES_V4_PNG_PATH.resolve(),
    }
    for dest in (curves_path, png_path):
        if dest.name in _PROTECTED_NAMES or dest.resolve() in protected:
            raise TextGoldError("protected_persist", "D-3 must not overwrite a pinned persist name.")
    pins = dense_d2._pin_identity(
        gold_path=gold_file,
        census_path=census_file,
        store_path=store_file,
        v3_path=v3_file,
        analysis_path=analysis_file,
    )
    v3_before = pins["curves_v3"]
    v4_before = file_sha256(v4_file) if v4_file.is_file() else None
    pins["curves_v4"] = v4_before
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
    artifact = build_d3_artifact(gold=gold, index=loaded, pins=pins, rankers=rankers)
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    render_d3_png(artifact, png_path)
    if file_sha256(v3_file) != v3_before:
        raise TextGoldError("v3_mutated", "v3 digest moved during D-3 emit.")
    if v4_before is not None and file_sha256(v4_file) != v4_before:
        raise TextGoldError("v4_mutated", "v4 digest moved during D-3 emit.")
    by_arm = {str(row.get("arm")): row for row in artifact["series"]}
    return {
        "curves_path": str(curves_path),
        "png_path": str(png_path),
        "curves_v3_sha256": v3_before,
        "curves_v4_sha256": v4_before,
        "hybrid_n_leaks": 0,
        "n_retrievable_at_k": {
            arm: by_arm[arm]["n_retrievable_at_k"] for arm in ARMS
        },
        "n_leaks_at_k": {arm: by_arm[arm]["n_leaks_at_k"] for arm in ARMS},
        "versus_at_k": artifact["versus_at_k"],
    }
