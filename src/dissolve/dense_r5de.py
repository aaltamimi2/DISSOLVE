"""P-3 DENSE-lineage measurement. BGE-base 768-d beside sealed MiniLM. Not C11."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import dense_d2, dense_d3, engine_e2e, research, text_chunk_metrics
from .gold_ensemble import CENSUS_V3_SHA256, CONTAMINANT_SHA256, file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

DENSE_SPEC_SHA256 = dense_d2.DENSE_SPEC_SHA256
RECALL_SPEC_V1_SHA256 = "2c66a709667cb332b7cbc0bc31610ef68edda03956b53a0583d790a0472c43fd"
RECALL_SPEC_V11_SHA256 = "849953d7a5be4ec5c55de062e3aa32d4b2260cfeeae2f414d45bd0ae7f5e36f1"
RECALL_SPEC_V12_SHA256 = "1807c6476772cef4e481e8072455a2758cd68706dda670eb879900a7217a49b0"
CURVES_V4_SHA256 = "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2"
CURVES_D3_SHA256 = dense_d3.CURVES_D3_SHA256
GZIP_SHA256 = "bf0bb9e213401868bac45590f364bc831976765fc9b258357523a981d2fe14f1"
MANIFEST_SHA256 = "61bbe29541cc3bb12a5257191433c510be0375ad984ae054f227dfe471ca6edd"
CURVES_R5DE_SCHEMA = "dissolve.text-chunk-curves.retrieval.r5de.v1"
CURVES_R5DE_NAME = "CURVES.retrieval.r5de.v1.json"
R5DE_GZIP_NAME = "t5-indexed-unsealed.r5de.v1.json.gz"
CURVES_R5DE_PATH = DEFAULT_OUT_DIR / CURVES_R5DE_NAME
R5DE_GZIP_PATH = DEFAULT_OUT_DIR / "indexes" / R5DE_GZIP_NAME
BGE_ID = "BAAI/bge-base-en-v1.5"
BGE_DIM = 768
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
BGE_PASSAGE_INSTRUCTION = ""
BGE_RECIPE_SOURCE = "https://huggingface.co/BAAI/bge-base-en-v1.5"
CONTROL_DENSE_K5 = 138
CONTROL_HYBRID_K5 = 201
DENSE_NET_BAR = 25
HYBRID_NET_BAR = 5
HYBRID_BROKEN_BAR = 2
_FORBIDDEN = frozenset({"needles", "query", "evidence_quote", "canonical_text"})
_PROTECTED_NAMES = frozenset({
    "CURVES.retrieval.v1.json",
    "CURVES.retrieval.v1.png",
    "CURVES.retrieval.v3.json",
    "CURVES.retrieval.v3.png",
    "CURVES.retrieval.T5.v1.json",
    "CURVES.retrieval.T5.v1.png",
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.v4.png",
    "CURVES.retrieval.d3.json",
    "CURVES.retrieval.d3.png",
    "CURVES.retrieval.d3.finding.json",
    "CURVES.retrieval.abstention.r1.json",
    "CURVES.retrieval.abstention.r3.json",
    "GOLD.v2.json",
    "GOLD.text.v1.unsealed.json",
    "CHUNKS.t5.indexed.unsealed.v1.json",
    "t5-indexed-unsealed.json.gz",
    "INDEX.t5.unsealed.v1.json",
    "ERROR_ANALYSIS.k10.v3.json",
})
Ranker = Callable[[Mapping[str, Any], str], list[Mapping[str, Any]]]
Embedder = Callable[[list[str], str | None], tuple[str, list[list[float]]]]


def bge_recipe() -> dict[str, Any]:
    """Published-card recipe. Pin this before the first BGE vector is written."""
    return {
        "model": BGE_ID,
        "dim": BGE_DIM,
        "query_instruction": BGE_QUERY_INSTRUCTION,
        "passage_instruction": BGE_PASSAGE_INSTRUCTION,
        "recipe_source": BGE_RECIPE_SOURCE,
        "passage_recipe": "no_instruction",
    }


def require_bge_recipe(recipe: Mapping[str, Any]) -> dict[str, Any]:
    pinned = bge_recipe()
    if str(recipe.get("model") or "") != pinned["model"]:
        raise TextGoldError("bge_recipe", "P-3 model must be BAAI/bge-base-en-v1.5.")
    if int(recipe.get("dim") or 0) != pinned["dim"]:
        raise TextGoldError("bge_recipe", "P-3 dim must be 768.")
    if str(recipe.get("query_instruction") or "") != pinned["query_instruction"]:
        raise TextGoldError("bge_recipe", "BGE query instruction must match the published card.")
    if str(recipe.get("passage_instruction") or "") != pinned["passage_instruction"]:
        raise TextGoldError("bge_recipe", "BGE passages must carry no instruction.")
    if str(recipe.get("model") or "") in {"BAAI/bge-small-en-v1.5", "intfloat/e5-small-v2"}:
        raise TextGoldError("bge_recipe", "e5 and bge-small are withdrawn.")
    return pinned


def encode_with_recipe(
    texts: Sequence[str],
    recipe: Mapping[str, Any],
    role: str,
    *,
    embedder: Embedder | None = None,
) -> tuple[str, list[list[float]]]:
    """Refuse naive MiniLM-style BGE encoding. Recipe must already be pinned."""
    pinned = require_bge_recipe(recipe)
    if role == "query":
        prefix = str(pinned["query_instruction"])
    elif role == "passage":
        prefix = str(pinned["passage_instruction"])
    else:
        raise TextGoldError("bge_role", "Encode role must be query or passage.")
    prepared = [prefix + str(text) for text in texts]
    encode = embedder or research._dense_vectors
    try:
        model_id, vectors = encode(prepared, pinned["model"])
    except Exception as error:
        raise TextGoldError(
            "bge_blocked",
            "BAAI/bge-base-en-v1.5 could not be loaded or evaluated. BLOCKED. No substitute.",
        ) from error
    if model_id != pinned["model"]:
        raise TextGoldError("bge_blocked", "Embedder returned a substitute model id.")
    if any(len(row) != pinned["dim"] for row in vectors):
        raise TextGoldError("bge_dim", "A BGE vector is not 768-d.")
    return model_id, vectors


def _write_gzip(dest: Path, index: Mapping[str, Any]) -> str:
    dest = Path(dest)
    if dest.name in _PROTECTED_NAMES or dest.name == "t5-indexed-unsealed.json.gz":
        raise TextGoldError("protected_persist", "P-3 must not overwrite the sealed MiniLM gzip.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode()
    temporary = dest.with_name(dest.name + ".tmp")
    with temporary.open("wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            compressed.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(dest)
    return file_sha256(dest)


def _paper_shas(index: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for chunk in index.get("chunks") or []:
        sha = str(chunk.get("paper_sha256") or "")
        if sha:
            out.add(sha)
    for document in index.get("documents") or []:
        sha = str(document.get("sha256") or "")
        if sha:
            out.add(sha)
    return out


def _refuse_contaminant(store: Mapping[str, Any], index: Mapping[str, Any]) -> None:
    store_shas = {str(sha) for sha in (store.get("indexed_paper_sha256") or [])}
    if CONTAMINANT_SHA256 in store_shas:
        raise TextGoldError("contaminant_indexed", "contaminant.pdf must never be indexed.")
    if CONTAMINANT_SHA256 in _paper_shas(index):
        raise TextGoldError("contaminant_indexed", "contaminant.pdf must never enter the gzip.")
    for chunk in store.get("chunks") or []:
        if str(chunk.get("paper_sha256") or "") == CONTAMINANT_SHA256:
            raise TextGoldError("contaminant_indexed", "contaminant.pdf must never be a store chunk.")


def _ranker_from_table(mode: str, loaded_id: str, table: Mapping[str, list[float]]) -> Ranker:
    if mode == "sparse":
        return lambda idx, query: dense_d3.rank_mode(idx, query, "sparse")

    def fake(texts, model_name=None):
        missing = [text for text in texts if text not in table]
        if missing:
            raise TextGoldError("query_embed_miss", "A P-3 query was not in the precomputed table.")
        return loaded_id, [table[text] for text in texts]

    def rank(idx: Mapping[str, Any], query: str):
        previous = research._dense_vectors
        research._dense_vectors = fake
        try:
            return dense_d3.rank_mode(idx, query, mode)
        finally:
            research._dense_vectors = previous

    return rank


def _query_table(
    index: Mapping[str, Any],
    queries: Sequence[str],
    *,
    embedder: Embedder | None = None,
) -> tuple[str, dict[str, list[float]]]:
    dense = index.get("dense") or {}
    model = str(dense.get("model") or "")
    prefix = str(dense.get("query_instruction") or "")
    unique = [query for query in dict.fromkeys(str(item) for item in queries) if query]
    prepared = [prefix + query for query in unique]
    encode = embedder or research._dense_vectors
    loaded_id, vectors = encode(prepared, model) if prepared else (model, [])
    return loaded_id, {text: vector for text, vector in zip(prepared, vectors)}


def _cached_ranker(index: Mapping[str, Any], queries: Sequence[str], mode: str, *, embedder: Embedder | None = None) -> Ranker:
    if mode == "sparse":
        return _ranker_from_table(mode, "", {})
    loaded_id, table = _query_table(index, queries, embedder=embedder)
    return _ranker_from_table(mode, loaded_id, table)


def _hit_ids_at_k(
    index: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    ranker: Ranker,
    k: int = 5,
) -> list[str]:
    ids: list[str] = []
    label = str(k)
    for fact in facts:
        ranked = ranker(index, str(fact.get("query") or ""))
        retrievable, _precision = text_chunk_metrics.retrieval_at_ks(
            ranked, fact.get("needles") or {},
        )
        if retrievable.get(label):
            fact_id = str(fact.get("fact_id") or "")
            if fact_id:
                ids.append(fact_id)
    return sorted(ids)


def _mrr_at(
    index: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    ranker: Ranker,
    k: int = 20,
) -> float:
    if not facts:
        return 0.0
    total = 0.0
    for fact in facts:
        ranked = ranker(index, str(fact.get("query") or ""))
        needles = fact.get("needles") or {}
        rr = 0.0
        for rank, chunk in enumerate(ranked[:k], 1):
            if text_chunk_metrics.contain_bound_fact(text_chunk_metrics._body(chunk), needles):
                rr = 1.0 / rank
                break
        total += rr
    return total / len(facts)


def _pair(new_ids: Sequence[str], base_ids: Sequence[str]) -> dict[str, Any]:
    new_set, base_set = set(new_ids), set(base_ids)
    fixed = sorted(new_set - base_set)
    broken = sorted(base_set - new_set)
    return {
        "fixed": fixed,
        "broken": broken,
        "net": len(fixed) - len(broken),
        "n_fixed": len(fixed),
        "n_broken": len(broken),
        "n_baseline": len(base_set),
        "n_new": len(new_set),
    }


def _arm_row(
    scored: Mapping[str, Any],
    *,
    arm: str,
    embedder: str,
    dim: int,
    mrr20: float,
) -> dict[str, Any]:
    n_retr = scored["n_retrievable_at_k"]
    row = dict(scored)
    row.update({
        "arm": arm,
        "strategy": "T5",
        "params": {"target": 1400},
        "bucket": "all",
        "ranker": "dense" if "dense" in arm else ("hybrid" if "hybrid" in arm else arm),
        "embedder": embedder,
        "dim": dim,
        "embedder_in_retrieval": "sparse" not in arm,
        "refuse_rule": "sparse_gated",
        "hybrid_weights": {"dense": 0.55, "sparse": 0.40} if "hybrid" in arm else None,
        "r_at_1": n_retr.get("1"),
        "r_at_3": n_retr.get("3"),
        "mrr_at_20": mrr20,
    })
    return row


def _snapshot_sealed() -> dict[str, str]:
    return {
        "store": file_sha256(engine_e2e.STORE_PATH),
        "gzip": file_sha256(dense_d2.INDEX_GZIP_PATH),
        "manifest": file_sha256(engine_e2e.MANIFEST_PATH),
        "gold": file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH),
        "v4": file_sha256(dense_d2.CURVES_V4_PATH),
        "d3": file_sha256(dense_d3.CURVES_D3_PATH),
    }


def _assert_sealed_unmoved(before: Mapping[str, str]) -> None:
    after = _snapshot_sealed()
    names = {
        "store": "T5 store",
        "gzip": "MiniLM gzip",
        "manifest": "INDEX.t5",
        "gold": "unsealed gold",
        "v4": "v4 curves",
        "d3": "d3 curves",
    }
    for key, label in names.items():
        if before.get(key) != after.get(key):
            raise TextGoldError("sealed_moved", f"{label} moved during P-3 emit.")


def build_bge_index(
    minilm_index: Mapping[str, Any],
    store: Mapping[str, Any],
    recipe: Mapping[str, Any],
    *,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    require_bge_recipe(recipe)
    dense_d2._assert_index_matches_store(store, minilm_index)
    _refuse_contaminant(store, minilm_index)
    chunks = list(minilm_index.get("chunks") or [])
    texts = [research.chunk_sparse_corpus(chunk) for chunk in chunks]
    model_id, vectors = encode_with_recipe(texts, recipe, "passage", embedder=embedder)
    out = dict(minilm_index)
    out["dense"] = {
        "model": model_id,
        "dim": int(recipe["dim"]),
        "chunk_ids": [str(chunk["chunk_id"]) for chunk in chunks],
        "vectors": vectors,
        "query_instruction": recipe["query_instruction"],
        "passage_instruction": recipe["passage_instruction"],
        "recipe_source": recipe["recipe_source"],
        "refuse_rule": "sparse_gated",
    }
    dense_d2._assert_index_matches_store(store, out)
    if int(out["dense"]["dim"]) == research._MINILM_DIM:
        raise TextGoldError("bge_dim", "BGE index must not record MiniLM dim 384.")
    return out


def build_r5de_artifact(
    *,
    gold: Mapping[str, Any],
    minilm_index: Mapping[str, Any],
    bge_index: Mapping[str, Any],
    pins: Mapping[str, str],
    recipe: Mapping[str, Any],
    gzip_sha256: str,
    rankers: Mapping[str, Ranker] | None = None,
    embedder: Embedder | None = None,
    require_control_counts: bool = False,
) -> dict[str, Any]:
    require_bge_recipe(recipe)
    split = text_chunk_metrics.split_gold(gold)
    queries = [str(fact.get("query") or "") for fact in split["must_fire"] + split["must_refuse"]]
    used = dict(rankers or {})
    if not used:
        minilm_id, minilm_table = _query_table(minilm_index, queries, embedder=embedder)
        bge_id, bge_table = _query_table(bge_index, queries, embedder=embedder)
        used["control_dense"] = _ranker_from_table("dense", minilm_id, minilm_table)
        used["control_hybrid"] = _ranker_from_table("hybrid", minilm_id, minilm_table)
        used["dense"] = _ranker_from_table("dense", bge_id, bge_table)
        used["hybrid"] = _ranker_from_table("hybrid", bge_id, bge_table)
    used.setdefault("control_dense", _cached_ranker(minilm_index, queries, "dense", embedder=embedder))
    used.setdefault("control_hybrid", _cached_ranker(minilm_index, queries, "hybrid", embedder=embedder))
    used.setdefault("dense", _cached_ranker(bge_index, queries, "dense", embedder=embedder))
    used.setdefault("hybrid", _cached_ranker(bge_index, queries, "hybrid", embedder=embedder))
    control_dense = dense_d3._score_arm(gold=gold, index=minilm_index, ranker=used["control_dense"])
    control_hybrid = dense_d3._score_arm(gold=gold, index=minilm_index, ranker=used["control_hybrid"])
    bge_dense = dense_d3._score_arm(gold=gold, index=bge_index, ranker=used["dense"])
    bge_hybrid = dense_d3._score_arm(gold=gold, index=bge_index, ranker=used["hybrid"])
    for name, scored in (
        ("control_dense", control_dense),
        ("control_hybrid", control_hybrid),
        ("dense", bge_dense),
        ("hybrid", bge_hybrid),
    ):
        leaks = scored["n_leaks_at_k"]
        if any(int(leaks[label]) > 0 for label in leaks):
            raise TextGoldError("refuse_leak", f"P-3 arm {name} leaked on the must-refuse split.")
        refuse = scored["n_must_refuse_at_k"]
        n_hold = int(scored["n_held_out_facts"])
        if any(int(refuse[label]) != n_hold for label in refuse):
            raise TextGoldError("refuse_leak", f"P-3 arm {name} must-refuse is not complete at every k.")
    if require_control_counts:
        if int(control_dense["n_retrievable_at_k"]["5"]) != CONTROL_DENSE_K5:
            raise TextGoldError("control_138", "Identical-embedder dense control did not reproduce 138.")
        if int(control_hybrid["n_retrievable_at_k"]["5"]) != CONTROL_HYBRID_K5:
            raise TextGoldError("control_201", "MiniLM hybrid control did not IDENT 201.")
    control_dense_ids = _hit_ids_at_k(minilm_index, split["must_fire"], used["control_dense"], 5)
    control_hybrid_ids = _hit_ids_at_k(minilm_index, split["must_fire"], used["control_hybrid"], 5)
    bge_dense_ids = _hit_ids_at_k(bge_index, split["must_fire"], used["dense"], 5)
    bge_hybrid_ids = _hit_ids_at_k(bge_index, split["must_fire"], used["hybrid"], 5)
    dense_pair = _pair(bge_dense_ids, control_dense_ids)
    hybrid_pair = _pair(bge_hybrid_ids, control_hybrid_ids)
    dense_bar_met = dense_pair["net"] >= DENSE_NET_BAR and dense_pair["n_new"] >= CONTROL_DENSE_K5 + DENSE_NET_BAR
    hybrid_bar_met = hybrid_pair["net"] >= HYBRID_NET_BAR and hybrid_pair["n_broken"] <= HYBRID_BROKEN_BAR
    minilm_dense = minilm_index.get("dense") or {}
    bge_dense_block = bge_index.get("dense") or {}
    if int(minilm_dense.get("dim") or 0) != engine_e2e.EXPECTED_DIM:
        raise TextGoldError("control_dim", "MiniLM control index is not 384-d.")
    if int(bge_dense_block.get("dim") or 0) != BGE_DIM:
        raise TextGoldError("bge_dim", "BGE index is not 768-d.")
    if str(minilm_dense.get("model") or "") == str(bge_dense_block.get("model") or ""):
        raise TextGoldError("mixed_embedder", "Control and BGE indexes share a model id.")
    rows = [
        _arm_row(
            control_dense, arm="control_dense", embedder=str(minilm_dense.get("model")),
            dim=int(minilm_dense.get("dim")),
            mrr20=_mrr_at(minilm_index, split["must_fire"], used["control_dense"]),
        ),
        _arm_row(
            control_hybrid, arm="control_hybrid", embedder=str(minilm_dense.get("model")),
            dim=int(minilm_dense.get("dim")),
            mrr20=_mrr_at(minilm_index, split["must_fire"], used["control_hybrid"]),
        ),
        _arm_row(
            bge_dense, arm="dense", embedder=BGE_ID, dim=BGE_DIM,
            mrr20=_mrr_at(bge_index, split["must_fire"], used["dense"]),
        ),
        _arm_row(
            bge_hybrid, arm="hybrid", embedder=BGE_ID, dim=BGE_DIM,
            mrr20=_mrr_at(bge_index, split["must_fire"], used["hybrid"]),
        ),
    ]
    artifact = {
        "schema": CURVES_R5DE_SCHEMA,
        "measurement_kind": "dense_retrieval_lineage",
        "is_c8": False,
        "is_c11": False,
        "is_c12": False,
        "c12_model_selection_made": False,
        "gold_sealed": False,
        "spec_sha256": DENSE_SPEC_SHA256,
        "recall_spec_sha256": {
            "v1": RECALL_SPEC_V1_SHA256,
            "v1.1": RECALL_SPEC_V11_SHA256,
            "v1.2": RECALL_SPEC_V12_SHA256,
        },
        "index": "indexed_papers_only",
        "embedder": BGE_ID,
        "dim": BGE_DIM,
        "control_embedder": engine_e2e.MINILM_ID,
        "control_dim": engine_e2e.EXPECTED_DIM,
        "bge_recipe": dict(recipe),
        "refuse_rule": "sparse_gated",
        "refuse_gate": "sparse_raw_score > 0",
        "hybrid_weights": {"dense": 0.55, "sparse": 0.40},
        "weights_retuned": False,
        "bars_moved": False,
        "retrieval_ks": list(text_chunk_metrics.RETRIEVAL_KS),
        "ci_method": "wilson_score",
        "ci_level": 0.95,
        "z": text_chunk_metrics.WILSON_Z,
        "gold_sha256": pins["gold"],
        "census_sha256": pins["census"],
        "store_sha256": pins["store"],
        "gzip_sha256": pins["gzip"],
        "r5de_gzip_sha256": gzip_sha256,
        "r5de_gzip_name": R5DE_GZIP_NAME,
        "curves_v3_sha256": pins["curves_v3"],
        "curves_v4_sha256": pins["curves_v4"],
        "curves_d3_sha256": pins["curves_d3"],
        "error_analysis_sha256": pins["error_analysis"],
        "n_indexed_papers": len(split["indexed_shas"]),
        "n_held_out_papers": len(split["held_shas"]),
        "n_indexed_facts": len(split["must_fire"]),
        "n_held_out_facts": len(split["must_refuse"]),
        "n_chunks": len(bge_index.get("chunks") or []),
        "series": rows,
        "paired_at_k5": {
            "dense_vs_control_dense": dense_pair,
            "hybrid_vs_control_hybrid": hybrid_pair,
        },
        "bars": {
            "k": 5,
            "dense_only_net": DENSE_NET_BAR,
            "dense_only_floor": CONTROL_DENSE_K5 + DENSE_NET_BAR,
            "hybrid_net": HYBRID_NET_BAR,
            "hybrid_broken_max": HYBRID_BROKEN_BAR,
            "dense_only_met": dense_bar_met,
            "hybrid_met": hybrid_bar_met,
        },
        "finding": {
            "dense_only_net": dense_pair["net"],
            "hybrid_net": hybrid_pair["net"],
            "hybrid_broken": hybrid_pair["n_broken"],
            "weights_retuned": False,
            "bars_moved": False,
            "hybrid_unsatisfied_is_finding": (not hybrid_bar_met),
            "note": (
                "Hybrid bar miss is a score-scale finding for a later named spec. "
                "Weights stay 0.55/0.40. Bars are not moved."
                if not hybrid_bar_met else
                "Bars recorded as pre-registered. Weights were not retuned."
            ),
        },
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN)):
        raise TextGoldError("gold_quoted", "r5de must not carry needles, queries, or quotes.")
    return artifact


def emit_r5de_product(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    store_path: Path | None = None,
    v3_path: Path | None = None,
    v4_path: Path | None = None,
    d3_path: Path | None = None,
    analysis_path: Path | None = None,
    minilm_gzip_path: Path | None = None,
    dest_dir: Path | None = None,
    embedder: Embedder | None = None,
    rankers: Mapping[str, Ranker] | None = None,
    index: Mapping[str, Any] | None = None,
    bge_index: Mapping[str, Any] | None = None,
    require_control_counts: bool = True,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    recipe = bge_recipe()
    require_bge_recipe(recipe)
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or engine_e2e.CENSUS_PATH)
    store_file = Path(store_path or engine_e2e.STORE_PATH)
    v3_file = Path(v3_path or text_chunk_metrics.CURVES_V3_PATH)
    v4_file = Path(v4_path or dense_d2.CURVES_V4_PATH)
    d3_file = Path(d3_path or dense_d3.CURVES_D3_PATH)
    analysis_file = Path(analysis_path or dense_d2.ERROR_ANALYSIS_PATH)
    gzip_file = Path(minilm_gzip_path or dense_d2.INDEX_GZIP_PATH)
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    curves_path = out_dir / CURVES_R5DE_NAME
    sidecar_gzip = out_dir / "indexes" / R5DE_GZIP_NAME
    if curves_path.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "r5de dest name is protected.")
    if sidecar_gzip.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "P-3 gzip name collides with a sealed dest.")
    if sidecar_gzip.resolve() == dense_d2.INDEX_GZIP_PATH.resolve():
        raise TextGoldError("protected_persist", "P-3 must not overwrite the sealed MiniLM gzip.")
    if store_file.resolve() == engine_e2e.STORE_PATH.resolve() and file_sha256(store_file) != engine_e2e.STORE_SHA256:
        raise TextGoldError("store_sha_moved", "T5 store moved; do not emit r5de.")
    if gzip_file.resolve() == dense_d2.INDEX_GZIP_PATH.resolve() and file_sha256(gzip_file) != GZIP_SHA256:
        raise TextGoldError("gzip_moved", "MiniLM gzip moved; do not emit r5de.")
    pins = dense_d2._pin_identity(
        gold_path=gold_file,
        census_path=census_file,
        store_path=store_file,
        v3_path=v3_file,
        analysis_path=analysis_file,
    )
    if file_sha256(v4_file) != CURVES_V4_SHA256:
        raise TextGoldError("v4_sha_moved", "v4 curves moved; do not emit r5de.")
    if file_sha256(d3_file) != CURVES_D3_SHA256:
        raise TextGoldError("d3_sha_moved", "d3 curves moved; do not emit r5de.")
    pins["curves_v4"] = CURVES_V4_SHA256
    pins["curves_d3"] = CURVES_D3_SHA256
    pins["gzip"] = file_sha256(gzip_file)
    gold = json.loads(gold_file.read_text())
    census = json.loads(census_file.read_text())
    store = json.loads(store_file.read_text())
    gold_sets = engine_e2e.gold_status_sets(gold)
    census_sets = engine_e2e.census_status_sets(census)
    store_shas = {str(sha) for sha in (store.get("indexed_paper_sha256") or [])}
    if gold_sets["indexed"] != census_sets["indexed"] or gold_sets["indexed"] != store_shas:
        raise TextGoldError("index_sha_mismatch", "Gold / census / store indexed SHA sets differ.")
    minilm_loaded = dict(index) if index is not None else dense_d2.load_gzip_index(gzip_file)
    minilm_dense = minilm_loaded.get("dense") or {}
    if str(minilm_dense.get("model") or "") != engine_e2e.MINILM_ID:
        raise TextGoldError("control_model", "Control gzip is not MiniLM-L6-v2.")
    if int(minilm_dense.get("dim") or 0) != engine_e2e.EXPECTED_DIM:
        raise TextGoldError("control_dim", "Control gzip is not 384-d.")
    dense_d2._assert_index_matches_store(store, minilm_loaded)
    _refuse_contaminant(store, minilm_loaded)
    sealed_before = None
    live = out_dir.resolve() == DEFAULT_OUT_DIR.resolve()
    if live:
        sealed_before = _snapshot_sealed()
        if sealed_before["manifest"] != MANIFEST_SHA256:
            raise TextGoldError("manifest_moved", "INDEX.t5 moved; do not emit r5de.")
    built = dict(bge_index) if bge_index is not None else build_bge_index(
        minilm_loaded, store, recipe, embedder=embedder,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    gzip_sha = _write_gzip(sidecar_gzip, built)
    artifact = build_r5de_artifact(
        gold=gold,
        minilm_index=minilm_loaded,
        bge_index=built,
        pins=pins,
        recipe=recipe,
        gzip_sha256=gzip_sha,
        rankers=rankers,
        embedder=embedder,
        require_control_counts=require_control_counts,
    )
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    if file_sha256(store_file) != pins["store"]:
        raise TextGoldError("store_mutated", "T5 store moved during r5de emit.")
    if file_sha256(gzip_file) != pins["gzip"]:
        raise TextGoldError("gzip_mutated", "MiniLM gzip moved during r5de emit.")
    if live and sealed_before is not None:
        _assert_sealed_unmoved(sealed_before)
    return {
        "curves_path": str(curves_path),
        "gzip_path": str(sidecar_gzip),
        "curves_sha256": file_sha256(curves_path),
        "gzip_sha256": gzip_sha,
        "control_dense_k5": int((artifact["series"][0]["n_retrievable_at_k"] or {})["5"]),
        "dense_k5": int((artifact["series"][2]["n_retrievable_at_k"] or {})["5"]),
        "hybrid_k5": int((artifact["series"][3]["n_retrievable_at_k"] or {})["5"]),
        "dense_net": artifact["paired_at_k5"]["dense_vs_control_dense"]["net"],
        "hybrid_net": artifact["paired_at_k5"]["hybrid_vs_control_hybrid"]["net"],
        "bars": artifact["bars"],
        "finding": artifact["finding"],
    }
