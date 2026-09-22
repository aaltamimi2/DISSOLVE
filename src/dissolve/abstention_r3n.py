"""R-3N: measure rank-1 coverage localization. No floor bind. No R-2."""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import abstention_a2a4, abstention_a3, abstention_r1, abstention_r3, dense_d2, engine_e2e, research, text_chunk_metrics
from .abstention_a2a4 import _wilson, five_number
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "7d925d2cf9956078b6dc34a86ed6290bd68d262b9a649b03b1ac7729ddc841aa"
SCHEMA = "dissolve.retrieval.abstention.r3n"
CURVES_NAME = "CURVES.retrieval.abstention.r3n.json"
FIGURE_NAME = "FIGURES.abstention.r3n.png"
CURVES_PATH = DEFAULT_OUT_DIR / CURVES_NAME
FIGURE_PATH = DEFAULT_OUT_DIR / FIGURE_NAME
GZIP_SHA256 = abstention_r1.GZIP_SHA256
MANIFEST_SHA256 = abstention_r1.MANIFEST_SHA256
OFFDOMAIN_SHA256 = abstention_r1.OFFDOMAIN_SHA256
ABSTENTION_V1_SHA256 = abstention_r1.ABSTENTION_V1_SHA256
R3_CURVES_SHA256 = "e573f769259b5777a769c0c08d5eb2344e36f40cda11b5e9888242001bd15b6a"
R3_FIGURE_SHA256 = "8e03c09e4c7892e0ed2306ff3e3f3b00064a2499d5d513b49f979a8de71e6b4c"
EXPECTED_N_FIRE = abstention_r1.EXPECTED_N_FIRE
EXPECTED_N_HELD = abstention_r1.EXPECTED_N_HELD
EXPECTED_N_OFF = abstention_r1.EXPECTED_N_OFF
EXPECTED_N_CHUNKS = abstention_r1.EXPECTED_N_CHUNKS
SHIPPED_FLOOR = abstention_r1.SHIPPED_FLOOR
R1_KEEP_X = 216
R1_HELD_ABS_X = 9
R1_OFF_ABS_X = 20
BINDABLE = ("rank1_coverage", "rank1_coverage_ratio", "rank1_rare_coverage")
CONTROL = "rare_star"
NAMED_STATISTICS = BINDABLE + (CONTROL,)
R3M_ARM_NAMES = frozenset(abstention_r3.NAMED_STATISTICS)
STAR_RULE = "value < floor"
FINDING_RULE = (
    "For each named statistic S, S_eligible iff a sweep row has "
    "held_out_abstention.x > 9 and must_fire_keep.x >= 216. "
    "If any of rank1_coverage, rank1_coverage_ratio, rank1_rare_coverage "
    "is eligible: next_work=owner_operating_point. Else next_work=live_floor_stands. "
    "rare_star is a control and does not authorize a bind. "
    "R-3N must not pick F. No floor was shipped. query_idf_coverage stays live. "
    "m5_established is false. sparse_raw_top stays unbound."
)
_FORBIDDEN_EMIT_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_NAMES = frozenset(abstention_r1._PROTECTED_NAMES) | {
    "CURVES.retrieval.abstention.r1.json",
    "FIGURES.abstention.r1.png",
    "CURVES.retrieval.abstention.r3.json",
    "FIGURES.abstention.r3.png",
}


def strip_floor(index: Mapping[str, Any]) -> dict[str, Any]:
    working = dict(index)
    working.pop("abstention", None)
    return working


def _full_rows(index: Mapping[str, Any], query: str, mode: str) -> list[Mapping[str, Any]]:
    n_chunks = max(1, len(index.get("chunks") or []))
    return research._search_index(index, query, n_chunks, mode)


def _chunk_by_id(index: Mapping[str, Any], chunk_id: str) -> Mapping[str, Any] | None:
    for chunk in index.get("chunks") or []:
        if str(chunk.get("chunk_id") or "") == str(chunk_id):
            return chunk
    return None


def _rare_query_tokens(
    chunks: Sequence[Mapping[str, Any]],
    query: str,
) -> tuple[set[str], float, int, Mapping[str, int], list[set[str]]]:
    n_docs, document_frequency, token_sets = research._idf_maps(chunks)
    query_tokens = set(research._tokens(query))
    if not query_tokens:
        return set(), 0.0, n_docs, document_frequency, token_sets
    cut = statistics.median(
        [research._idf_value(token, n_docs, document_frequency) for token in query_tokens]
    )
    rare = {
        token for token in query_tokens
        if research._idf_value(token, n_docs, document_frequency) >= cut
    }
    rare_mass = sum(research._idf_value(token, n_docs, document_frequency) for token in rare)
    return rare, float(rare_mass), n_docs, document_frequency, token_sets


def _rare_mass_on_tokens(
    rare: set[str],
    rare_mass: float,
    tokens: set[str],
    n_docs: int,
    document_frequency: Mapping[str, int],
) -> float:
    if rare_mass <= 0:
        return 0.0
    matched = sum(
        research._idf_value(token, n_docs, document_frequency)
        for token in rare
        if token in tokens
    )
    return float(matched) / rare_mass


def rank1_coverage(index: Mapping[str, Any], query: str) -> float:
    rows = _full_rows(index, query, "hybrid")
    if not rows:
        return 0.0
    return float(rows[0]["query_idf_coverage"])


def rank1_coverage_ratio(index: Mapping[str, Any], query: str) -> float:
    top = rank1_coverage(index, query)
    star = float(research.coverage_star(index, query))
    if star <= 0:
        return 0.0
    return top / star


def rank1_rare_coverage(index: Mapping[str, Any], query: str) -> float:
    rows = _full_rows(index, query, "hybrid")
    if not rows:
        return 0.0
    chunks = list(index.get("chunks") or [])
    rare, rare_mass, n_docs, document_frequency, _token_sets = _rare_query_tokens(chunks, query)
    if rare_mass <= 0:
        return 0.0
    chunk = _chunk_by_id(index, str(rows[0]["chunk_id"]))
    if chunk is None:
        return 0.0
    tokens = set(research._tokens(research.chunk_sparse_corpus(chunk)))
    return _rare_mass_on_tokens(rare, rare_mass, tokens, n_docs, document_frequency)


def rare_star(index: Mapping[str, Any], query: str) -> float:
    chunks = list(index.get("chunks") or [])
    rare, rare_mass, n_docs, document_frequency, token_sets = _rare_query_tokens(chunks, query)
    if rare_mass <= 0:
        return 0.0
    sparse_raw = research._bm25(
        research._tokens(query),
        [{"text": research.chunk_sparse_corpus(chunk)} for chunk in chunks],
    )
    gated: list[float] = []
    for tokens, raw in zip(token_sets, sparse_raw):
        if float(raw) <= 0:
            continue
        gated.append(_rare_mass_on_tokens(rare, rare_mass, tokens, n_docs, document_frequency))
    return float(max(gated)) if gated else 0.0


STATISTICS: dict[str, Callable[[Mapping[str, Any], str], float]] = {
    "rank1_coverage": rank1_coverage,
    "rank1_coverage_ratio": rank1_coverage_ratio,
    "rank1_rare_coverage": rank1_rare_coverage,
    "rare_star": rare_star,
}


def score_query(index: Mapping[str, Any], query: str) -> dict[str, float]:
    """One hybrid pass plus MiniLM-free star/rare. IDENT the named scorers."""
    rows = _full_rows(index, query, "hybrid")
    top = float(rows[0]["query_idf_coverage"]) if rows else 0.0
    star = float(research.coverage_star(index, query))
    ratio = (top / star) if star > 0 else 0.0
    chunks = list(index.get("chunks") or [])
    rare, rare_mass, n_docs, document_frequency, token_sets = _rare_query_tokens(chunks, query)
    if rare_mass <= 0 or not rows:
        rank1_rare = 0.0
    else:
        chunk = _chunk_by_id(index, str(rows[0]["chunk_id"]))
        if chunk is None:
            rank1_rare = 0.0
        else:
            tokens = set(research._tokens(research.chunk_sparse_corpus(chunk)))
            rank1_rare = _rare_mass_on_tokens(rare, rare_mass, tokens, n_docs, document_frequency)
    sparse_raw = research._bm25(
        research._tokens(query),
        [{"text": research.chunk_sparse_corpus(chunk)} for chunk in chunks],
    )
    gated: list[float] = []
    if rare_mass > 0:
        for tokens, raw in zip(token_sets, sparse_raw):
            if float(raw) <= 0:
                continue
            gated.append(_rare_mass_on_tokens(rare, rare_mass, tokens, n_docs, document_frequency))
    return {
        "rank1_coverage": top,
        "rank1_coverage_ratio": ratio,
        "rank1_rare_coverage": rank1_rare,
        "rare_star": float(max(gated)) if gated else 0.0,
    }


def _arm_values(
    index: Mapping[str, Any],
    queries: Sequence[str],
) -> dict[str, list[float]]:
    by_name = {name: [] for name in NAMED_STATISTICS}
    for query in queries:
        scored = score_query(index, query)
        for name in NAMED_STATISTICS:
            by_name[name].append(float(scored[name]))
    return by_name


def _rate_block(n_event: int, n: int) -> dict[str, Any]:
    return _wilson(n_event, n)


def candidate_floors(*groups: Sequence[float]) -> list[float]:
    values = {0.0}
    for group in groups:
        values.update(float(item) for item in group)
    return sorted(values)


def sweep_row(
    *,
    floor: float,
    fire_values: Sequence[float],
    held_values: Sequence[float],
    off_values: Sequence[float],
) -> dict[str, Any]:
    n_fire_keep = sum(1 for value in fire_values if value >= floor)
    n_held_abs = sum(1 for value in held_values if value < floor)
    n_off_abs = sum(1 for value in off_values if value < floor)
    return {
        "floor": float(floor),
        "star_rule": STAR_RULE,
        "must_fire_keep": _rate_block(n_fire_keep, len(fire_values)),
        "held_out_abstention": _rate_block(n_held_abs, len(held_values)),
        "offdomain_abstention": _rate_block(n_off_abs, len(off_values)),
    }


def statistic_eligible(rows: Sequence[Mapping[str, Any]]) -> bool:
    for row in rows:
        held_x = int(row["held_out_abstention"]["x"])
        keep_x = int(row["must_fire_keep"]["x"])
        if held_x > R1_HELD_ABS_X and keep_x >= R1_KEEP_X:
            return True
    return False


def next_work_from_eligibility(eligible: Mapping[str, bool]) -> str:
    if any(bool(eligible.get(name)) for name in BINDABLE):
        return "owner_operating_point"
    return "live_floor_stands"


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    items = [float(value) for value in values]
    summary = five_number(items)
    summary["values"] = sorted(items)
    return summary


def _r1_baseline() -> dict[str, Any]:
    return {
        "statistic": "query_idf_coverage",
        "star_rule": "star < floor",
        "floor": SHIPPED_FLOOR,
        "must_fire_keep": _rate_block(R1_KEEP_X, EXPECTED_N_FIRE),
        "held_out_abstention": _rate_block(R1_HELD_ABS_X, EXPECTED_N_HELD),
        "offdomain_abstention": _rate_block(R1_OFF_ABS_X, EXPECTED_N_OFF),
        "not_a_rank1_coverage_candidate": True,
    }


def _assert_r1_not_overwritten(path: Path, *, before: bytes | None, live: bool) -> None:
    if before is None:
        if path.is_file() and path.name in _PROTECTED_NAMES:
            raise TextGoldError("abstention_r1_mutated", "R-3N must not create the R-1 persist.")
        return
    if not path.is_file():
        raise TextGoldError("abstention_r1_mutated", "R-1 curves must stay on disk.")
    if not live:
        if path.read_bytes() != before:
            raise TextGoldError("abstention_r1_mutated", "R-3N must not overwrite the R-1 persist.")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("schema") or "") != "dissolve.retrieval.abstention.r1":
        raise TextGoldError("abstention_r1_mutated", "R-1 curves must stay unmoved.")
    shipped = payload.get("at_shipped") or {}
    keep_x = int((shipped.get("must_fire_keep") or {}).get("x", -1))
    held_x = int((shipped.get("held_out_abstention") or {}).get("x", -1))
    off_x = int((shipped.get("offdomain_abstention") or {}).get("x", -1))
    if keep_x != R1_KEEP_X or held_x != R1_HELD_ABS_X or off_x != R1_OFF_ABS_X:
        raise TextGoldError("abstention_r1_counts", "R-1 at_shipped counts must stay 216/9/20.")
    if str(payload.get("next_work") or "") != "R-3":
        raise TextGoldError("abstention_r1_next_work", "R-1 next_work must stay R-3.")


def build_r3n_artifact(
    *,
    index: Mapping[str, Any],
    fire_queries: Sequence[str],
    held_queries: Sequence[str],
    offdomain_queries: Sequence[str],
    pins: Mapping[str, str],
) -> dict[str, Any]:
    if len(held_queries) == len(offdomain_queries) and len(held_queries) != EXPECTED_N_HELD:
        raise TextGoldError(
            "held_out_substituted",
            "Held-out must not reuse the titania set or any other equal-n substitute.",
        )
    stripped = strip_floor(index)
    fire_arms = _arm_values(stripped, fire_queries)
    held_arms = _arm_values(stripped, held_queries)
    off_arms = _arm_values(stripped, offdomain_queries)
    arms: dict[str, Any] = {}
    eligible: dict[str, bool] = {}
    for name in NAMED_STATISTICS:
        fire_values = fire_arms[name]
        held_values = held_arms[name]
        off_values = off_arms[name]
        if len(held_values) != len(held_queries):
            raise TextGoldError("held_out_prefix", "Held-out values must cover every held-out query.")
        floors = candidate_floors(fire_values, held_values, off_values)
        rows = [
            sweep_row(
                floor=floor,
                fire_values=fire_values,
                held_values=held_values,
                off_values=off_values,
            )
            for floor in floors
        ]
        is_eligible = statistic_eligible(rows)
        eligible[name] = is_eligible
        arms[name] = {
            "name": name,
            "bindable": name in BINDABLE,
            "eligible": is_eligible,
            "must_fire": _distribution(fire_values),
            "held_out": _distribution(held_values),
            "offdomain": _distribution(off_values),
            "sweep": rows,
        }
        if "sparse_score" in json.dumps(arms[name]):
            raise TextGoldError("sparse_score_sweep", "R-3N must not threshold normalised sparse_score.")
    if set(arms) & R3M_ARM_NAMES:
        raise TextGoldError("r3m_arms_reused", "R-3N must not reuse R-3M arm names as its bindable set.")
    next_work = next_work_from_eligibility(eligible)
    artifact = {
        "schema": SCHEMA,
        "spec_sha256": SPEC_SHA256,
        "star_rule": STAR_RULE,
        "finding_rule": FINDING_RULE,
        "next_work": next_work,
        "r3_bound": False,
        "r3n_bound": False,
        "new_floor_shipped": False,
        "r2_ran": False,
        "query_idf_coverage_retired": False,
        "m5_established": False,
        "weights_retuned": False,
        "operating_point_is_owners": True,
        "seal_forbidden": True,
        "held_out_in_index": 0,
        "v4_must_refuse_is_not_abstention": True,
        "shipped_floor": SHIPPED_FLOOR,
        "shipped_floor_source": "INDEX.t5.unsealed.v1.json abstention.floor",
        "r1_at_shipped": _r1_baseline(),
        "statistics": arms,
        "eligible": {name: bool(eligible[name]) for name in NAMED_STATISTICS},
        "n_chunks": len(stripped.get("chunks") or []),
        **dict(pins),
    }
    held_ns = {int(arms[name]["held_out"]["n"]) for name in NAMED_STATISTICS}
    if held_ns != {len(held_queries)}:
        raise TextGoldError("held_out_prefix", "Held-out n must be identical on every arm.")
    if 8 in held_ns:
        raise TextGoldError("held_out_prefix", "Held-out n=8 is the prefix error; emit all 29.")
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "R-3N emit must not carry needles, queries, or STRAP fact_ids.")
    dumped = json.dumps(artifact)
    if '"sparse_score"' in dumped:
        raise TextGoldError("sparse_score_sweep", "R-3N must not emit a sparse_score sweep key.")
    return artifact


def _style(ax) -> None:
    abstention_r1._style(ax)


def _five_bar(ax, y: float, summary: Mapping[str, Any], color: str) -> None:
    abstention_r1._five_bar(ax, y, summary, color)


def render_r3n_figure(artifact: Mapping[str, Any], dest: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.2))
    for ax, name in zip(axes.ravel(), NAMED_STATISTICS):
        arm = artifact["statistics"][name]
        rows = (
            (2.0, arm["must_fire"], "#4C78A8", f"must-fire n={arm['must_fire']['n']}"),
            (1.0, arm["held_out"], "#F58518", f"held-out n={arm['held_out']['n']}"),
            (0.0, arm["offdomain"], "#54A24B", f"titania n={arm['offdomain']['n']}"),
        )
        labels = []
        ys = []
        for y, summary, color, label in rows:
            _five_bar(ax, y, summary, color)
            labels.append(label)
            ys.append(y)
        ax.set_yticks(ys)
        ax.set_yticklabels(labels)
        ax.set_xlabel(name)
        ax.set_title(name, fontsize=10, color="black")
        _style(ax)
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=120, facecolor="white")
    plt.close(fig)
    return dest


def _queries_from_facts(facts: Sequence[Mapping[str, Any]]) -> list[str]:
    queries = [str(fact.get("query") or "") for fact in facts]
    if any(not item for item in queries):
        raise TextGoldError("empty_query", "R-3N refuses an empty query string.")
    return queries


def emit_r3n_product(
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
    held_queries: Sequence[str] | None = None,
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
    figure_path = out_dir / FIGURE_NAME
    if curves_path.name in _PROTECTED_NAMES or figure_path.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "R-3N must not overwrite a pinned persist name.")
    gzip_before = file_sha256(gzip_file)
    man_before = file_sha256(man_file)
    gold_before = file_sha256(gold_file)
    off_before = file_sha256(off_file) if off_file.is_file() else ""
    r1_path = out_dir / "CURVES.retrieval.abstention.r1.json"
    r3_path = out_dir / "CURVES.retrieval.abstention.r3.json"
    live_out = out_dir.resolve() == DEFAULT_OUT_DIR.resolve()
    r1_before = r1_path.read_bytes() if r1_path.is_file() else None
    r3_before = r3_path.read_bytes() if r3_path.is_file() else None
    r3_fig_path = out_dir / "FIGURES.abstention.r3.png"
    r3_fig_before = r3_fig_path.read_bytes() if r3_fig_path.is_file() else None
    live_gold = gold_file.resolve() == text_chunk_metrics.GOLD_UNSEALED_PATH.resolve()
    live_gzip = gzip_file.resolve() == dense_d2.INDEX_GZIP_PATH.resolve()
    live_man = man_file.resolve() == engine_e2e.MANIFEST_PATH.resolve()
    live_off = off_file.resolve() == abstention_a3.OFFDOMAIN_PATH.resolve()
    if live_gzip and gzip_before != GZIP_SHA256:
        raise TextGoldError("gzip_moved", "T5 gzip moved; do not emit R-3N.")
    if live_gold and gold_before != text_chunk_metrics.GOLD_UNSEALED_SHA256:
        raise TextGoldError("gold_moved", "Unsealed gold digest moved; do not emit R-3N.")
    if live_man and man_before != MANIFEST_SHA256:
        raise TextGoldError("manifest_moved", "INDEX.t5 digest moved; do not emit R-3N.")
    if live_off and off_before != OFFDOMAIN_SHA256:
        raise TextGoldError("offdomain_moved", "Titania battery digest moved; do not emit R-3N.")
    live_r3 = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.r3.json"
    if live_out and live_r3.is_file():
        r3_payload = json.loads(live_r3.read_text(encoding="utf-8"))
        if str(r3_payload.get("schema") or "") != "dissolve.retrieval.abstention.r3":
            raise TextGoldError("r3_dest_moved", "R-3 dest schema must stay r3.")
        if str(r3_payload.get("next_work") or "") != "R-3_next_candidate":
            raise TextGoldError("r3_dest_next_work", "R-3 dest next_work must stay R-3_next_candidate.")
    gold = json.loads(gold_file.read_text())
    split = text_chunk_metrics.split_gold(gold)
    if live_gold:
        if len(split["must_fire"]) != EXPECTED_N_FIRE or len(split["must_refuse"]) != EXPECTED_N_HELD:
            raise TextGoldError("gold_split_moved", "Must-fire / must-refuse counts are not the pinned sets.")
    loaded = index if index is not None else dense_d2.load_gzip_index(gzip_file)
    working = strip_floor(loaded)
    if index is None and live_gzip and len(working.get("chunks") or []) != EXPECTED_N_CHUNKS:
        raise TextGoldError("n_chunks_mismatch", "Live n_chunks is not 922.")
    index_shas = {
        str(row.get("sha256") or row.get("paper_sha256") or "")
        for row in (working.get("documents") or [])
    }
    if index_shas & set(split["held_shas"]):
        raise TextGoldError("held_out_in_index", "Held-out papers must not enter the T5 index.")
    off_payload = json.loads(off_file.read_text()) if off_file.is_file() else None
    fire = list(fire_queries) if fire_queries is not None else _queries_from_facts(split["must_fire"])
    held = list(held_queries) if held_queries is not None else _queries_from_facts(split["must_refuse"])
    off = list(offdomain_queries) if offdomain_queries is not None else [
        str(row.get("query") or "") for row in (off_payload or {}).get("queries") or []
    ]
    if live_gold and (len(fire) != EXPECTED_N_FIRE or len(held) != EXPECTED_N_HELD):
        raise TextGoldError("held_out_prefix", "R-3N must emit must-fire 228 and held-out 29, not a prefix.")
    if live_off and len(off) != EXPECTED_N_OFF:
        raise TextGoldError("offdomain_n", "Titania battery n is not 24.")
    if len(held) == EXPECTED_N_OFF and live_gold:
        raise TextGoldError("held_out_substituted", "Held-out n must be 29, not the titania 24.")
    if not fire or not held or not off:
        raise TextGoldError("r3n_empty_battery", "R-3N needs must-fire, held-out, and titania queries.")
    gold_ids = {str(fact.get("fact_id") or "") for fact in split["facts"]}
    od_ids = {str(row.get("id") or "") for row in ((off_payload or {}).get("queries") or [])}
    if gold_ids & od_ids:
        raise TextGoldError("od_id_collision", "Off-domain ids collided with gold fact_ids.")
    manifest = json.loads(man_file.read_text())
    shipped = float((manifest.get("abstention") or {}).get("floor"))
    if live_man and shipped != SHIPPED_FLOOR:
        raise TextGoldError("floor_moved", "Shipped floor moved; R-3N does not retune it.")
    pins = {
        "gold_sha256": gold_before,
        "census_sha256": file_sha256(census_file),
        "store_sha256": file_sha256(store_file),
        "gzip_sha256": gzip_before,
        "manifest_sha256": man_before,
        "offdomain_sha256": off_before,
        "set_digest": str((off_payload or {}).get("set_digest") or abstention_a3.SET_DIGEST),
        "r3_curves_sha256": file_sha256(r3_path) if r3_path.is_file() else (R3_CURVES_SHA256 if live_out else ""),
    }
    artifact = build_r3n_artifact(
        index=working,
        fire_queries=fire,
        held_queries=held,
        offdomain_queries=off,
        pins=pins,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    render_r3n_figure(artifact, figure_path)
    if file_sha256(gzip_file) != gzip_before:
        raise TextGoldError("gzip_mutated", "T5 gzip moved during R-3N emit.")
    if file_sha256(gold_file) != gold_before:
        raise TextGoldError("gold_mutated", "Unsealed gold moved during R-3N emit.")
    if file_sha256(man_file) != man_before:
        raise TextGoldError("manifest_mutated", "INDEX.t5 must not change during R-3N.")
    if off_file.is_file() and file_sha256(off_file) != off_before:
        raise TextGoldError("offdomain_mutated", "Titania battery must not change during R-3N.")
    v1_path = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json"
    if v1_path.is_file() and file_sha256(v1_path) != ABSTENTION_V1_SHA256:
        raise TextGoldError("abstention_v1_mutated", "A-2A4 curves must stay pinned.")
    _assert_r1_not_overwritten(r1_path, before=r1_before, live=live_out)
    if r3_before is not None:
        if not r3_path.is_file() or r3_path.read_bytes() != r3_before:
            raise TextGoldError("r3_dest_mutated", "R-3 dest must stay unmoved.")
    if r3_fig_before is not None:
        if not r3_fig_path.is_file() or r3_fig_path.read_bytes() != r3_fig_before:
            raise TextGoldError("r3_figure_mutated", "R-3 figure must stay unmoved.")
    return {
        "curves_path": str(curves_path),
        "figure_path": str(figure_path),
        "next_work": artifact["next_work"],
        "r3_bound": False,
        "r3n_bound": False,
        "new_floor_shipped": False,
        "r2_ran": False,
        "query_idf_coverage_retired": False,
        "n_must_fire": EXPECTED_N_FIRE if live_gold else len(fire),
        "n_held_out": len(held),
        "n_offdomain": len(off),
        "gzip_sha256": gzip_before,
        "gold_sha256": gold_before,
        "manifest_sha256": man_before,
    }
