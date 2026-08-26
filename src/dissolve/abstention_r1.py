"""R-1: measure query_idf_coverage on must-fire, held-out 29, titania 24.

Does not ship a floor. Does not run R-2. Does not load MiniLM.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import abstention_a2a4, abstention_a3, dense_d2, engine_e2e, research, text_chunk_metrics
from .abstention_a2a4 import _wilson, five_number
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "35f1385019a182700bbb4ba844d95c7ff3bf095b5cf839b819de99b241135fa1"
SCHEMA = "dissolve.retrieval.abstention.r1"
CURVES_NAME = "CURVES.retrieval.abstention.r1.json"
FIGURE_NAME = "FIGURES.abstention.r1.png"
CURVES_PATH = DEFAULT_OUT_DIR / CURVES_NAME
FIGURE_PATH = DEFAULT_OUT_DIR / FIGURE_NAME
GZIP_SHA256 = abstention_a2a4.GZIP_SHA256
MANIFEST_SHA256 = "61bbe29541cc3bb12a5257191433c510be0375ad984ae054f227dfe471ca6edd"
OFFDOMAIN_SHA256 = "0ee86b2682fa2aad8dee0c686fe6ae3cf565af872834c0b7626e9effd888a346"
ABSTENTION_V1_SHA256 = "1a946df2e556e34e5c78491c99c208f20aec6ba674aa4b887f359947b226a398"
EXPECTED_N_FIRE = 228
EXPECTED_N_HELD = 29
EXPECTED_N_OFF = 24
EXPECTED_N_CHUNKS = 922
SHIPPED_FLOOR = 0.35126980440608535
FINDING_RULE = (
    "R-3 iff no candidate floor has held_out_abstention.x > at_shipped.held_out_abstention.x "
    "and must_fire_keep.x >= at_shipped.must_fire_keep.x. Else R-2_eligible. "
    "R-2 did not run. No floor was shipped. Operating point is the owner's. "
    "m5_established is false."
)
STAR_RULE = "star < floor"
_FORBIDDEN_EMIT_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_NAMES = frozenset({
    "GOLD.text.v1.unsealed.json",
    "GOLD.v2.json",
    "CENSUS.v3.json",
    "CHUNKS.t5.indexed.unsealed.v1.json",
    "INDEX.t5.unsealed.v1.json",
    "t5-indexed-unsealed.json.gz",
    "CURVES.retrieval.v3.json",
    "CURVES.retrieval.v3.png",
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.v4.png",
    "CURVES.retrieval.d3.json",
    "CURVES.retrieval.d3.png",
    "CURVES.retrieval.d3.finding.json",
    "CURVES.retrieval.weights.v1.json",
    "CURVES.retrieval.weights.v1.png",
    "CURVES.retrieval.abstention.v1.json",
    "OFFDOMAIN.queries.v1.json",
    "GRAPH.t5.unsealed.v1.json",
})


def _stars(index: Mapping[str, Any], queries: Sequence[str]) -> list[float]:
    return [float(research.coverage_star(index, query)) for query in queries]


def _rate_block(n_event: int, n: int) -> dict[str, Any]:
    return _wilson(n_event, n)


def sweep_row(
    *,
    floor: float,
    fire_stars: Sequence[float],
    held_stars: Sequence[float],
    off_stars: Sequence[float],
    shipped_floor: float,
) -> dict[str, Any]:
    n_fire = len(fire_stars)
    n_held = len(held_stars)
    n_off = len(off_stars)
    n_fire_keep = sum(1 for value in fire_stars if value >= floor)
    n_held_abs = sum(1 for value in held_stars if value < floor)
    n_off_abs = sum(1 for value in off_stars if value < floor)
    return {
        "floor": float(floor),
        "star_rule": STAR_RULE,
        "shipped": float(floor) == float(shipped_floor),
        "must_fire_keep": _rate_block(n_fire_keep, n_fire),
        "held_out_abstention": _rate_block(n_held_abs, n_held),
        "offdomain_abstention": _rate_block(n_off_abs, n_off),
    }


def candidate_floors(
    fire_stars: Sequence[float],
    held_stars: Sequence[float],
    off_stars: Sequence[float],
    shipped_floor: float,
) -> list[float]:
    values = {float(item) for item in fire_stars}
    values.update(float(item) for item in held_stars)
    values.update(float(item) for item in off_stars)
    values.add(float(shipped_floor))
    values.add(0.0)
    values.add(1.0)
    return sorted(values)


def next_work_from_sweep(rows: Sequence[Mapping[str, Any]]) -> str:
    shipped = next((row for row in rows if row.get("shipped")), None)
    if shipped is None:
        raise TextGoldError("shipped_row_missing", "R-1 sweep must include the shipped floor.")
    shipped_keep = int(shipped["must_fire_keep"]["x"])
    shipped_held = int(shipped["held_out_abstention"]["x"])
    for row in rows:
        held_x = int(row["held_out_abstention"]["x"])
        keep_x = int(row["must_fire_keep"]["x"])
        if held_x > shipped_held and keep_x >= shipped_keep:
            return "R-2_eligible"
    return "R-3"


def _distribution(stars: Sequence[float]) -> dict[str, Any]:
    items = [float(value) for value in stars]
    summary = five_number(items)
    summary["stars"] = sorted(items)
    return summary


def _range_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return text_chunk_metrics.intervals_overlap(
        {"lo": left.get("min"), "hi": left.get("max")},
        {"lo": right.get("min"), "hi": right.get("max")},
    )


def _central_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return text_chunk_metrics.intervals_overlap(
        {"lo": left.get("p5"), "hi": left.get("p95")},
        {"lo": right.get("p5"), "hi": right.get("p95")},
    )


def build_r1_artifact(
    *,
    index: Mapping[str, Any],
    fire_queries: Sequence[str],
    held_queries: Sequence[str],
    offdomain_queries: Sequence[str],
    shipped_floor: float,
    pins: Mapping[str, str],
) -> dict[str, Any]:
    if len(held_queries) == len(offdomain_queries) and len(held_queries) != EXPECTED_N_HELD:
        raise TextGoldError(
            "held_out_substituted",
            "Held-out must not reuse the titania set or any other equal-n substitute.",
        )
    fire_stars = _stars(index, fire_queries)
    held_stars = _stars(index, held_queries)
    off_stars = _stars(index, offdomain_queries)
    if len(held_stars) != len(held_queries):
        raise TextGoldError("held_out_prefix", "Held-out stars must cover every held-out query.")
    floors = candidate_floors(fire_stars, held_stars, off_stars, shipped_floor)
    rows = [
        sweep_row(
            floor=floor,
            fire_stars=fire_stars,
            held_stars=held_stars,
            off_stars=off_stars,
            shipped_floor=shipped_floor,
        )
        for floor in floors
    ]
    fire_dist = _distribution(fire_stars)
    held_dist = _distribution(held_stars)
    off_dist = _distribution(off_stars)
    next_work = next_work_from_sweep(rows)
    shipped_row = next(row for row in rows if row["shipped"])
    artifact = {
        "schema": SCHEMA,
        "spec_sha256": SPEC_SHA256,
        "statistic": "query_idf_coverage",
        "star_rule": STAR_RULE,
        "finding_rule": FINDING_RULE,
        "next_work": next_work,
        "r2_ran": False,
        "new_floor_shipped": False,
        "weights_retuned": False,
        "m5_established": False,
        "operating_point_is_owners": True,
        "seal_forbidden": True,
        "held_out_in_index": 0,
        "v4_must_refuse_is_not_abstention": True,
        "index_hygiene_is_not_held_out_abstention": True,
        "shipped_floor": float(shipped_floor),
        "shipped_floor_source": "INDEX.t5.unsealed.v1.json abstention.floor",
        "must_fire": fire_dist,
        "held_out": held_dist,
        "offdomain": off_dist,
        "must_fire_held_out_range_overlap": _range_overlap(fire_dist, held_dist),
        "must_fire_held_out_p5_p95_overlap": _central_overlap(fire_dist, held_dist),
        "at_shipped": dict(shipped_row),
        "sweep": rows,
        "n_chunks": len(index.get("chunks") or []),
        **dict(pins),
    }
    if int(artifact["held_out"]["n"]) == 8:
        raise TextGoldError("held_out_prefix", "Held-out n=8 is the prefix error; emit all 29.")
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "R-1 emit must not carry needles, queries, or STRAP fact_ids.")
    return artifact


def _style(ax) -> None:
    ax.tick_params(labelsize=10, colors="black")
    ax.xaxis.label.set_size(10)
    ax.yaxis.label.set_size(10)
    ax.xaxis.label.set_color("black")
    ax.yaxis.label.set_color("black")
    ax.title.set_size(10)
    ax.title.set_color("black")
    for spine in ax.spines.values():
        spine.set_color("black")


def _five_bar(ax, y: float, summary: Mapping[str, Any], color: str) -> None:
    lo = float(summary["min"])
    hi = float(summary["max"])
    p5 = float(summary["p5"])
    p95 = float(summary["p95"])
    median = float(summary["median"])
    ax.hlines(y, lo, hi, colors="black", linewidth=1.0, zorder=1)
    ax.plot([p5, p95], [y, y], color=color, linewidth=6, solid_capstyle="butt", zorder=2)
    ax.plot(median, y, "o", color="black", markersize=7, zorder=3)


def render_r1_figure(artifact: Mapping[str, Any], dest: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.2))
    rows = (
        (2.0, artifact["must_fire"], "#4C78A8", f"must-fire n={artifact['must_fire']['n']}"),
        (1.0, artifact["held_out"], "#F58518", f"held-out n={artifact['held_out']['n']}"),
        (0.0, artifact["offdomain"], "#54A24B", f"titania n={artifact['offdomain']['n']}"),
    )
    labels = []
    ys = []
    for y, summary, color, label in rows:
        _five_bar(ax1, y, summary, color)
        labels.append(label)
        ys.append(y)
    ax1.set_yticks(ys)
    ax1.set_yticklabels(labels)
    ax1.set_xlabel("query_idf_coverage (coverage_star)")
    ax1.set_xlim(-0.02, 1.02)
    ax1.set_title("Five-number summaries, one axis")
    _style(ax1)

    keep = [float(row["must_fire_keep"]["rate"]) for row in artifact["sweep"]]
    held = [float(row["held_out_abstention"]["rate"]) for row in artifact["sweep"]]
    ax2.plot(keep, held, "o", color="#4C78A8", markersize=3, alpha=0.7)
    shipped = artifact["at_shipped"]
    ax2.plot(
        float(shipped["must_fire_keep"]["rate"]),
        float(shipped["held_out_abstention"]["rate"]),
        "s",
        color="black",
        markersize=8,
        zorder=3,
        label="shipped floor",
    )
    ax2.set_xlabel("must-fire keep rate")
    ax2.set_ylabel("held-out abstention rate")
    ax2.set_xlim(-0.02, 1.02)
    ax2.set_ylim(-0.02, 1.02)
    ax2.set_title("Sweep (star < floor)")
    ax2.legend(fontsize=10, loc="lower left")
    _style(ax2)
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=120, facecolor="white")
    plt.close(fig)
    return dest


def _queries_from_facts(facts: Sequence[Mapping[str, Any]]) -> list[str]:
    queries = [str(fact.get("query") or "") for fact in facts]
    if any(not item for item in queries):
        raise TextGoldError("empty_query", "R-1 refuses an empty query string.")
    return queries


def _shipped_floor(manifest: Mapping[str, Any]) -> float:
    block = manifest.get("abstention")
    if not isinstance(block, Mapping) or block.get("floor") is None:
        raise TextGoldError("floor_missing", "R-1 reads the shipped floor from INDEX.t5.")
    return float(block["floor"])


def emit_r1_product(
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
        raise TextGoldError("protected_persist", "R-1 must not overwrite a pinned persist name.")
    gzip_before = file_sha256(gzip_file)
    man_before = file_sha256(man_file)
    gold_before = file_sha256(gold_file)
    off_before = file_sha256(off_file) if off_file.is_file() else ""
    live_gold = gold_file.resolve() == text_chunk_metrics.GOLD_UNSEALED_PATH.resolve()
    live_gzip = gzip_file.resolve() == dense_d2.INDEX_GZIP_PATH.resolve()
    live_man = man_file.resolve() == engine_e2e.MANIFEST_PATH.resolve()
    live_off = off_file.resolve() == abstention_a3.OFFDOMAIN_PATH.resolve()
    if live_gzip and gzip_before != GZIP_SHA256:
        raise TextGoldError("gzip_moved", "T5 gzip moved; do not emit R-1.")
    if live_gold and gold_before != text_chunk_metrics.GOLD_UNSEALED_SHA256:
        raise TextGoldError("gold_moved", "Unsealed gold digest moved; do not emit R-1.")
    if live_man and man_before != MANIFEST_SHA256:
        raise TextGoldError("manifest_moved", "INDEX.t5 digest moved; do not emit R-1.")
    if live_off and off_before != OFFDOMAIN_SHA256:
        raise TextGoldError("offdomain_moved", "Titania battery digest moved; do not emit R-1.")
    gold = json.loads(gold_file.read_text())
    split = text_chunk_metrics.split_gold(gold)
    if live_gold:
        if len(split["must_fire"]) != EXPECTED_N_FIRE or len(split["must_refuse"]) != EXPECTED_N_HELD:
            raise TextGoldError("gold_split_moved", "Must-fire / must-refuse counts are not the pinned sets.")
    loaded = index if index is not None else dense_d2.load_gzip_index(gzip_file)
    working = dict(loaded)
    working.pop("abstention", None)
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
        raise TextGoldError("held_out_prefix", "R-1 must emit must-fire 228 and held-out 29, not a prefix.")
    if live_off and len(off) != EXPECTED_N_OFF:
        raise TextGoldError("offdomain_n", "Titania battery n is not 24.")
    if len(held) == EXPECTED_N_OFF and live_gold:
        raise TextGoldError("held_out_substituted", "Held-out n must be 29, not the titania 24.")
    if not fire or not held or not off:
        raise TextGoldError("r1_empty_battery", "R-1 needs must-fire, held-out, and titania queries.")
    gold_ids = {str(fact.get("fact_id") or "") for fact in split["facts"]}
    od_ids = {str(row.get("id") or "") for row in ((off_payload or {}).get("queries") or [])}
    if gold_ids & od_ids:
        raise TextGoldError("od_id_collision", "Off-domain ids collided with gold fact_ids.")
    manifest = json.loads(man_file.read_text())
    shipped_floor = _shipped_floor(manifest)
    if live_man and shipped_floor != SHIPPED_FLOOR:
        raise TextGoldError("floor_moved", "Shipped floor moved; R-1 does not retune it.")
    pins = {
        "gold_sha256": gold_before,
        "census_sha256": file_sha256(census_file),
        "store_sha256": file_sha256(store_file),
        "gzip_sha256": gzip_before,
        "manifest_sha256": man_before,
        "offdomain_sha256": off_before,
        "set_digest": str((off_payload or {}).get("set_digest") or abstention_a3.SET_DIGEST),
    }
    artifact = build_r1_artifact(
        index=working,
        fire_queries=fire,
        held_queries=held,
        offdomain_queries=off,
        shipped_floor=shipped_floor,
        pins=pins,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    render_r1_figure(artifact, figure_path)
    if file_sha256(gzip_file) != gzip_before:
        raise TextGoldError("gzip_mutated", "T5 gzip moved during R-1 emit.")
    if file_sha256(gold_file) != gold_before:
        raise TextGoldError("gold_mutated", "Unsealed gold moved during R-1 emit.")
    if file_sha256(man_file) != man_before:
        raise TextGoldError("manifest_mutated", "INDEX.t5 must not change during R-1.")
    if off_file.is_file() and file_sha256(off_file) != off_before:
        raise TextGoldError("offdomain_mutated", "Titania battery must not change during R-1.")
    v1_path = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json"
    if v1_path.is_file() and file_sha256(v1_path) != ABSTENTION_V1_SHA256:
        raise TextGoldError("abstention_v1_mutated", "A-2A4 curves must stay pinned.")
    return {
        "curves_path": str(curves_path),
        "figure_path": str(figure_path),
        "next_work": artifact["next_work"],
        "r2_ran": False,
        "new_floor_shipped": False,
        "shipped_floor": shipped_floor,
        "n_must_fire": artifact["must_fire"]["n"],
        "n_held_out": artifact["held_out"]["n"],
        "n_offdomain": artifact["offdomain"]["n"],
        "gzip_sha256": gzip_before,
        "gold_sha256": gold_before,
        "manifest_sha256": man_before,
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    del argv
    return emit_r1_product()
