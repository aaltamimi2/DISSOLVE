"""W-1 hybrid-weight sweep. Measure named pairs. Do not bind production weights."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import dense_d2, dense_d3, engine_e2e, research, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SWEEP_SPEC_SHA256 = "93accdb79dca33262621e60a8e3836c708c00fc493d838d5d0b0ba90b36b6c73"
DENSE_SPEC_SHA256 = dense_d2.DENSE_SPEC_SHA256
CURVES_WEIGHTS_SCHEMA = "dissolve.text-chunk-curves.retrieval.weights.v1"
CURVES_WEIGHTS_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json"
CURVES_WEIGHTS_PNG_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.png"
CURVES_V4_SHA256 = "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2"
CURVES_D3_SHA256 = dense_d3.CURVES_D3_SHA256
CURVES_D3_FINDING_SHA256 = "a4785d3c79a95ad8bea5217d7825a66793320bff6cecf62d694d07485b531819"
D3_HYBRID_N_RETRIEVABLE = {"1": 147, "3": 188, "5": 201, "10": 209, "20": 220}
NAMED_ARMS: tuple[tuple[str, float, float], ...] = (
    ("w_prod", 0.55, 0.40),
    ("w_swap", 0.40, 0.55),
    ("w_sparse_heavy", 0.25, 0.70),
    ("w_sparse_dom", 0.10, 0.85),
    ("w_dense_heavy", 0.70, 0.25),
)
PRODUCTION_ID = "w_prod"
_FORBIDDEN_W1_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_NAMES = dense_d3._PROTECTED_NAMES | {
    "CURVES.retrieval.d3.json",
    "CURVES.retrieval.d3.png",
    "CURVES.retrieval.d3.finding.json",
}


def named_grid() -> tuple[tuple[str, float, float], ...]:
    return NAMED_ARMS


def rank_hybrid_parts(
    parts: list[tuple[float, float, float, Mapping[str, Any]]],
    w_dense: float,
    w_sparse: float,
) -> list[Mapping[str, Any]]:
    ranked = [
        (w_dense * dense_score + w_sparse * sparse_score + boost, chunk)
        for sparse_score, dense_score, boost, chunk in parts
    ]
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("title")), item[1]["chunk_id"]))
    return [chunk for _score, chunk in ranked]


def _arm_failure(row: Mapping[str, Any], n_hold: int) -> tuple[bool, str | None, list[str]]:
    leak_ks: list[str] = []
    refuse_ks: list[str] = []
    for k in text_chunk_metrics.RETRIEVAL_KS:
        label = str(k)
        if int((row.get("n_leaks_at_k") or {}).get(label) or 0) > 0:
            leak_ks.append(label)
        n_refuse = int((row.get("n_must_refuse_at_k") or {}).get(label) or 0)
        refuse_rate = (row.get("must_refuse_at_k") or {}).get(label)
        if n_refuse != int(n_hold) or float(refuse_rate) != 1.0:
            refuse_ks.append(label)
    if leak_ks:
        return True, "leak", leak_ks
    if refuse_ks:
        return True, "refuse_dropped", refuse_ks
    return False, None, []


def _versus_w_prod(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    production = rows[PRODUCTION_ID]
    out: dict[str, Any] = {}
    for k in text_chunk_metrics.RETRIEVAL_KS:
        label = str(k)
        cell: dict[str, Any] = {}
        prod_recall = float((production.get("recall_at_k") or {})[label])
        prod_ci = (production.get("recall_ci_at_k") or {})[label]
        for arm_id, _, _ in NAMED_ARMS:
            if arm_id == PRODUCTION_ID:
                continue
            right = rows[arm_id]
            right_recall = float((right.get("recall_at_k") or {})[label])
            delta = right_recall - prod_recall
            tied = text_chunk_metrics.intervals_overlap(
                (right.get("recall_ci_at_k") or {})[label],
                prod_ci,
            )
            cell[arm_id] = {
                "delta": delta,
                "verdict": "TIED" if tied else ("UP" if delta > 0 else "DOWN"),
            }
        out[label] = cell
    return out


def sweep_finding(versus_at_k: Mapping[str, Any], series: list[Mapping[str, Any]], *, k: int = 5) -> dict[str, Any]:
    cell = dict((versus_at_k or {}).get(str(k)) or {})
    failed = {str(row.get("arm")) for row in series if row.get("arm_failed")}
    up_ids = [
        arm_id for arm_id, row in cell.items()
        if str((row or {}).get("verdict") or "") == "UP" and arm_id not in failed
    ]
    down_ids = [
        arm_id for arm_id, row in cell.items()
        if str((row or {}).get("verdict") or "") == "DOWN" and arm_id not in failed
    ]
    tied_ids = [
        arm_id for arm_id, row in cell.items()
        if str((row or {}).get("verdict") or "") == "TIED" and arm_id not in failed
    ]
    note = (
        f"At k={k}, versus w_prod: UP={up_ids or 'none'}; TIED={tied_ids or 'none'}; "
        f"DOWN={down_ids or 'none'}. Failed arms are not candidates. "
        "Weights were not bound as a new default."
    )
    return {
        "k": int(k),
        "up_vs_w_prod": up_ids,
        "tied_vs_w_prod": tied_ids,
        "down_vs_w_prod": down_ids,
        "weights_retuned": False,
        "note": note,
    }


def _assert_w_prod_ident(row: Mapping[str, Any], expected: Mapping[str, Any] | None) -> None:
    if expected is None:
        return
    got = {
        str(k): int((row.get("n_retrievable_at_k") or {}).get(str(k), 0))
        for k in text_chunk_metrics.RETRIEVAL_KS
    }
    want = {str(k): int(expected[str(k)]) for k in text_chunk_metrics.RETRIEVAL_KS}
    if got != want:
        raise TextGoldError("w_prod_ident", "w_prod n_retrievable_at_k is not IDENT to D-3 hybrid.")


def _assert_grid(series: list[Mapping[str, Any]]) -> None:
    got_ids = [str(row.get("arm")) for row in series]
    want_ids = [arm_id for arm_id, _, _ in NAMED_ARMS]
    if got_ids != want_ids:
        raise TextGoldError("grid_ids", "W-1 arm ids must match the frozen five-pair grid.")
    for row, (arm_id, w_dense, w_sparse) in zip(series, NAMED_ARMS):
        weights = row.get("hybrid_weights") or {}
        if round(float(weights.get("dense")), 2) != round(w_dense, 2):
            raise TextGoldError("grid_coeff", f"{arm_id} dense coefficient drifted.")
        if round(float(weights.get("sparse")), 2) != round(w_sparse, 2):
            raise TextGoldError("grid_coeff", f"{arm_id} sparse coefficient drifted.")
        if abs(float(weights.get("dense")) + float(weights.get("sparse")) - 0.95) > 1e-9:
            raise TextGoldError("grid_mass", f"{arm_id} weights must sum to 0.95.")


def _lookup_ranker(rankers: Mapping[str, Any], arm_id: str):
    if arm_id in rankers:
        return rankers[arm_id]
    raise TextGoldError("missing_arm_ranker", f"W-1 ranker missing for {arm_id}.")


def _measured_rankers(index: Mapping[str, Any], queries: list[str]) -> dict[str, Any]:
    model = str((index.get("dense") or {}).get("model") or engine_e2e.MINILM_ID)
    unique = [query for query in dict.fromkeys(str(item) for item in queries) if query]
    loaded_id, vectors = research._dense_vectors(unique, model) if unique else (model, [])
    table = {query: vector for query, vector in zip(unique, vectors)}

    def fake(texts, model_name=None):
        missing = [text for text in texts if text not in table]
        if missing:
            raise TextGoldError("query_embed_miss", "A W-1 query was not in the precomputed table.")
        return loaded_id, [table[text] for text in texts]

    previous = research._dense_vectors
    research._dense_vectors = fake
    try:
        parts_by_query = {query: research._hybrid_passage_parts(index, query) for query in unique}
    finally:
        research._dense_vectors = previous

    def make(w_dense: float, w_sparse: float):
        def rank(idx: Mapping[str, Any], query: str):
            return rank_hybrid_parts(parts_by_query.get(query) or [], w_dense, w_sparse)

        return rank

    return {arm_id: make(w_dense, w_sparse) for arm_id, w_dense, w_sparse in NAMED_ARMS}


def build_w1_artifact(
    *,
    gold: Mapping[str, Any],
    index: Mapping[str, Any],
    pins: Mapping[str, str],
    rankers: Mapping[str, Any] | None = None,
    w_prod_expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    split = text_chunk_metrics.split_gold(gold)
    n_hold = len(split["must_refuse"])
    queries = [str(fact.get("query") or "") for fact in split["must_fire"] + split["must_refuse"]]
    used = dict(rankers) if rankers is not None else _measured_rankers(index, queries)
    rows: dict[str, Any] = {}
    series: list[dict[str, Any]] = []
    for arm_id, w_dense, w_sparse in NAMED_ARMS:
        scored = dense_d3._score_arm(gold=gold, index=index, ranker=_lookup_ranker(used, arm_id))
        failed, reason, fail_ks = _arm_failure(scored, n_hold)
        scored.update({
            "arm": arm_id,
            "strategy": "T5",
            "params": {"target": 1400},
            "bucket": "all",
            "ranker": "hybrid",
            "embedder_in_retrieval": True,
            "refuse_rule": "sparse_gated",
            "hybrid_weights": {"dense": w_dense, "sparse": w_sparse},
            "arm_failed": failed,
            "fail_reason": reason,
            "fail_ks": fail_ks,
        })
        rows[arm_id] = scored
        series.append(scored)
    production = rows[PRODUCTION_ID]
    if production["arm_failed"]:
        raise TextGoldError("w_prod_failed", "w_prod leaked or dropped must-refuse; W-1 SHA fails.")
    _assert_w_prod_ident(production, w_prod_expected)
    _assert_grid(series)
    versus = _versus_w_prod(rows)
    dense = index.get("dense") or {}
    artifact = {
        "schema": CURVES_WEIGHTS_SCHEMA,
        "spec_sha256": SWEEP_SPEC_SHA256,
        "dense_spec_sha256": DENSE_SPEC_SHA256,
        "index": "indexed_papers_only",
        "embedder": str(dense.get("model") or engine_e2e.MINILM_ID),
        "dim": int(dense.get("dim") or engine_e2e.EXPECTED_DIM),
        "refuse_rule": "sparse_gated",
        "refuse_gate": "sparse_raw_score > 0",
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
        "curves_d3_sha256": pins.get("curves_d3"),
        "error_analysis_sha256": pins["error_analysis"],
        "n_indexed_papers": len(split["indexed_shas"]),
        "n_held_out_papers": len(split["held_shas"]),
        "n_indexed_facts": len(split["must_fire"]),
        "n_held_out_facts": len(split["must_refuse"]),
        "n_chunks": len(index.get("chunks") or []),
        "series": series,
        "versus_w_prod_at_k": versus,
    }
    artifact["finding"] = sweep_finding(versus, series)
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_W1_KEYS)):
        raise TextGoldError("gold_quoted", "W-1 must not carry needles, queries, quotes, or fact ids.")
    return artifact


def render_w1_png(artifact: Mapping[str, Any], dest: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    series = list(artifact.get("series") or [])
    if len(series) != len(NAMED_ARMS):
        raise TextGoldError("w1_series_missing", "W-1 needs the five named arms to plot.")
    ks = list(text_chunk_metrics.RETRIEVAL_KS)
    labels = [str(k) for k in ks]
    colors = {
        "w_prod": "#54A24B",
        "w_swap": "#4C78A8",
        "w_sparse_heavy": "#72B7B2",
        "w_sparse_dom": "#F58518",
        "w_dense_heavy": "#E45756",
    }
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), sharex=True)
    fig.suptitle("W-1 hybrid weight sweep (sparse-gated; production un-bound)", fontsize=12)
    for row in series:
        arm = str(row.get("arm") or "")
        color = colors.get(arm, "#9D755D")
        width = 2.4 if arm == PRODUCTION_ID else 1.4
        recall = [float((row.get("recall_at_k") or {})[label]) for label in labels]
        refuse = [float((row.get("must_refuse_at_k") or {})[label]) for label in labels]
        axes[0].plot(ks, recall, marker="o", color=color, linewidth=width, label=arm)
        axes[1].plot(ks, refuse, marker="o", color=color, linewidth=width, label=arm)
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


def _live_pin(path: Path, published: Path, digest: str, code: str, message: str) -> None:
    if path.resolve() == published.resolve() and file_sha256(path) != digest:
        raise TextGoldError(code, message)


def emit_w1_product(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    store_path: Path | None = None,
    v3_path: Path | None = None,
    v4_path: Path | None = None,
    d3_path: Path | None = None,
    finding_path: Path | None = None,
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
    d3_file = Path(d3_path or dense_d3.CURVES_D3_PATH)
    d3_finding_file = Path(finding_path or dense_d3.CURVES_D3_FINDING_PATH)
    analysis_file = Path(analysis_path or dense_d2.ERROR_ANALYSIS_PATH)
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    curves_path = out_dir / "CURVES.retrieval.weights.v1.json"
    png_path = out_dir / "CURVES.retrieval.weights.v1.png"
    protected = {
        v3_file.resolve(),
        text_chunk_metrics.CURVES_V3_PNG_PATH.resolve(),
        v4_file.resolve(),
        dense_d2.CURVES_V4_PNG_PATH.resolve(),
        d3_file.resolve(),
        dense_d3.CURVES_D3_PNG_PATH.resolve(),
        d3_finding_file.resolve(),
    }
    for dest in (curves_path, png_path):
        if dest.name in _PROTECTED_NAMES or dest.resolve() in protected:
            raise TextGoldError("protected_persist", "W-1 must not overwrite a pinned persist name.")
    pins = dense_d2._pin_identity(
        gold_path=gold_file,
        census_path=census_file,
        store_path=store_file,
        v3_path=v3_file,
        analysis_path=analysis_file,
    )
    v3_before = pins["curves_v3"]
    v4_before = file_sha256(v4_file) if v4_file.is_file() else None
    d3_before = file_sha256(d3_file) if d3_file.is_file() else None
    finding_before = file_sha256(d3_finding_file) if d3_finding_file.is_file() else None
    pins["curves_v4"] = v4_before
    pins["curves_d3"] = d3_before
    _live_pin(v4_file, dense_d2.CURVES_V4_PATH, CURVES_V4_SHA256, "v4_moved", "v4 digest moved; do not emit W-1.")
    _live_pin(d3_file, dense_d3.CURVES_D3_PATH, CURVES_D3_SHA256, "d3_moved", "d3 digest moved; do not emit W-1.")
    _live_pin(
        d3_finding_file,
        dense_d3.CURVES_D3_FINDING_PATH,
        CURVES_D3_FINDING_SHA256,
        "d3_finding_moved",
        "D-3 finding sidecar moved; do not emit W-1.",
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
    expected = None
    if d3_file.is_file() and d3_before == CURVES_D3_SHA256:
        expected = D3_HYBRID_N_RETRIEVABLE
        d3_artifact = json.loads(d3_file.read_text())
        hybrid = next(
            (row for row in (d3_artifact.get("series") or []) if row.get("arm") == "hybrid"),
            None,
        )
        if hybrid is not None:
            expected = {
                str(k): int((hybrid.get("n_retrievable_at_k") or {}).get(str(k), 0))
                for k in text_chunk_metrics.RETRIEVAL_KS
            }
    artifact = build_w1_artifact(
        gold=gold,
        index=loaded,
        pins=pins,
        rankers=rankers,
        w_prod_expected=expected,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    render_w1_png(artifact, png_path)
    if file_sha256(v3_file) != v3_before:
        raise TextGoldError("v3_mutated", "v3 digest moved during W-1 emit.")
    if v4_before is not None and file_sha256(v4_file) != v4_before:
        raise TextGoldError("v4_mutated", "v4 digest moved during W-1 emit.")
    if d3_before is not None and file_sha256(d3_file) != d3_before:
        raise TextGoldError("d3_mutated", "d3 digest moved during W-1 emit.")
    if finding_before is not None and file_sha256(d3_finding_file) != finding_before:
        raise TextGoldError("d3_finding_mutated", "D-3 finding sidecar moved during W-1 emit.")
    by_arm = {str(row.get("arm")): row for row in artifact["series"]}
    return {
        "curves_path": str(curves_path),
        "png_path": str(png_path),
        "curves_v3_sha256": v3_before,
        "curves_v4_sha256": v4_before,
        "curves_d3_sha256": d3_before,
        "w_prod_n_retrievable_at_k": by_arm[PRODUCTION_ID]["n_retrievable_at_k"],
        "w_prod_n_leaks_at_k": by_arm[PRODUCTION_ID]["n_leaks_at_k"],
        "versus_w_prod_at_k": artifact["versus_w_prod_at_k"],
        "weights_retuned": False,
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    return emit_w1_product()


if __name__ == "__main__":
    main()
