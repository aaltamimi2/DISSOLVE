"""ENGINE_E2E_SPEC.v1 E2E-0..4. No published score. MiniLM default at E2E-3."""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import math
import os
import zlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import research, text_chunk_metrics, text_chunking
from .gold_ensemble import (
    CENSUS_V3_SHA256,
    CONTAMINANT_SHA256,
    file_sha256,
)
from .text_gold import DEFAULT_OUT_DIR, TextGoldError, load_canonical

SPEC_SHA256 = "20957c2f8efcb2661307d4bc07010190d029e69910baa61ac431e6849a8768c5"
SPEC_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/ENGINE_E2E_SPEC.v1.md")
CENSUS_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v3.json")
STORE_SCHEMA = "dissolve.t5-chunk-store.unsealed.v1"
STORE_PATH = DEFAULT_OUT_DIR / "CHUNKS.t5.indexed.unsealed.v1.json"
STORE_SHA256 = "91851dbf3b979450d087f86acca8c146205e0d0a8c41cc49c3aab1fc9cf73a40"
FIGURE_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.v3.strategies_spans.png"
MANIFEST_SCHEMA = "dissolve.t5-index-manifest.unsealed.v1"
MANIFEST_PATH = DEFAULT_OUT_DIR / "INDEX.t5.unsealed.v1.json"
KNOWLEDGEBASE_ID = "t5-indexed-unsealed"
INDEX_HOME = DEFAULT_OUT_DIR / "indexes"
PLANT_TOKEN = "E2E2PLANTZXQTOKEN"
NONSENSE_QUERY = "ZXQQQNONSENSEZXQ TOKENTHATISABSENTQZX"
SERVED_PROVENANCE_KEYS = (
    "paper_sha256",
    "page",
    "section",
    "section_origin",
    "char_start",
    "char_end",
    "chunk_id",
    "dense_score",
    "sparse_score",
    "section_boost",
    "final_score",
)
MINILM_ID = "sentence-transformers/all-MiniLM-L6-v2"
EXPECTED_DIM = 384
BGE_FROZEN_FLOOR = 0.3698406656908355
BGE_EXPLORATORY_STATUS = "exploratory-below-floor"
_MINILM_PROTECTED_BASENAMES = frozenset({
    f"{KNOWLEDGEBASE_ID}.json.gz",
    MANIFEST_PATH.name,
})
REBOUND_NOTE_TOKEN = "E2E3REBOUNDZXQNOTE"
CURVES_V3_SHA256 = "8090143a69106ff5ba5db8bab70f7fbc7980fe4f54574d427c7815dca7379152"
CURVES_V3_PNG_SHA256 = "be999b405544ac44a5e37291918f3c2b9b66196f6e9569df689ebb09d0d0cc4b"
ERROR_ANALYSIS_SHA256 = "4dbd695135ce7f877e4db86f6ed93ea855a75258b501aaf6d1391ee7ede3fdde"
CURVES_T5_LEFTOVER_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.T5.v1.json"
CURVES_T5_LEFTOVER_PNG_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.T5.v1.png"
CURVES_T5_LEFTOVER_SHA256 = "bbddc6ce69558ec8b7480bf3bf65511a9217e24f4c351792565edf441d73c0bb"
CURVES_T5_LEFTOVER_PNG_SHA256 = "9f94f666a1f6abdd5e69c39b3b3ea5164c22fe92f8cb0f127cff36532a2ba9fb"
EXPECTED_N_INDEXED = 19
EXPECTED_N_CHUNKS = 922
T5_RIGHT_TITLE = "T5 block-pack, target 1400"
STRATEGY_COLORS = {
    "T0": "#4C78A8",
    "T1": "#F58518",
    "T5": "#54A24B",
    "T6": "#E45756",
}
BUCKET_COLORS = {
    "<200": "#7B3294",
    "200-600": "#C51B7D",
    "600-1500": "#8C510A",
    ">1500": "#01665E",
}
_FORBIDDEN_STORE_KEYS = frozenset({"fact_id", "needles", "query", "evidence_quote"})


def figure_suptitle(artifact: Mapping[str, Any]) -> str:
    n_idx = int(artifact.get("n_indexed_papers") or 0)
    n_fire = int(artifact.get("n_indexed_facts") or 0)
    return f"{n_idx} indexed papers, {n_fire} must-fire facts"


def palettes_are_disjoint() -> bool:
    return set(STRATEGY_COLORS.values()).isdisjoint(BUCKET_COLORS.values())


def gold_status_sets(gold: Mapping[str, Any]) -> dict[str, set[str]]:
    indexed: set[str] = set()
    held: set[str] = set()
    for row in gold.get("papers") or []:
        sha = str(row.get("paper_sha256") or "")
        status = str(row.get("paper_status") or "")
        if not sha:
            continue
        if status == "indexed":
            indexed.add(sha)
        elif status == "held_out":
            held.add(sha)
    return {"indexed": indexed, "held_out": held}


def census_status_sets(census: Mapping[str, Any]) -> dict[str, set[str]]:
    grouped = {"indexed": set(), "held_out": set(), "excluded": set()}
    for row in census.get("papers") or []:
        sha = str(row.get("sha256") or "")
        status = str(row.get("status") or "")
        if sha and status in grouped:
            grouped[status].add(sha)
    return grouped


def t5_store_chunk_id(paper_sha256: str, ordinal: int) -> str:
    return f"T5-{paper_sha256[:12]}-{int(ordinal):04d}"


def _overlapping_blocks(
    canonical: Mapping[str, Any], start: int, end: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in canonical.get("blocks") or []:
        try:
            b_start, b_end = int(block["char_start"]), int(block["char_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b_start < end and start < b_end:
            rows.append(dict(block))
    rows.sort(key=lambda item: (int(item["char_start"]), int(item["char_end"])))
    return rows


def _page_stamp(blocks: Sequence[Mapping[str, Any]]) -> int | str | None:
    pages: list[int] = []
    for block in blocks:
        page = block.get("page")
        if page is None:
            continue
        try:
            pages.append(int(page))
        except (TypeError, ValueError):
            continue
    if not pages:
        return None
    lo, hi = min(pages), max(pages)
    if lo == hi:
        return lo
    return f"{lo}-{hi}"


def _section_stamp(block: Mapping[str, Any] | None) -> tuple[str, Any]:
    if block is None:
        return "", None
    origin = block.get("nearest_preceding_heading_origin")
    heading = list(block.get("nearest_preceding_heading") or [])
    section = research._chunk_header(heading) if origin == "parser_supplied" else ""
    return section, origin


def _kind_stamp(blocks: Sequence[Mapping[str, Any]]) -> str | None:
    kinds = [str(block.get("kind") or "") for block in blocks]
    kinds = [kind for kind in kinds if kind]
    if not kinds:
        return None
    if len(set(kinds)) == 1:
        return kinds[0]
    return "pack"


def _t5_n_chunks_from_curves(artifact: Mapping[str, Any]) -> int:
    for row in artifact.get("series") or []:
        if (
            row.get("strategy") == "T5"
            and dict(row.get("params") or {}) == {"target": text_chunking.T5_TARGET}
            and row.get("bucket") == "all"
        ):
            return int(row.get("n_chunks") or 0)
    raise TextGoldError("t5_series_missing", "Official I T5 bucket=all series is missing.")


def named_strategy_series(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in artifact.get("series") or []:
        if row.get("bucket") != "all" or int(row.get("n_facts") or 0) <= 0:
            continue
        if any(
            text_chunk_metrics._arm_matches_named(row["strategy"], row["params"], named)
            for named in text_chunk_metrics.NAMED_ERROR_ARMS
        ):
            rows.append(dict(row))
    return rows


def t5_bucket_series(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in artifact.get("series") or []:
        if row.get("strategy") != "T5":
            continue
        if dict(row.get("params") or {}) != {"target": text_chunking.T5_TARGET}:
            continue
        if row.get("bucket") in {None, "all"}:
            continue
        if int(row.get("n_facts") or 0) <= 0:
            continue
        rows.append(dict(row))
    return rows


def _ys(row: Mapping[str, Any]) -> list[float]:
    rates = row.get("recall_at_k") or {}
    return [float(rates.get(str(k)) or 0.0) for k in text_chunk_metrics.RETRIEVAL_KS]


def _yerr(row: Mapping[str, Any]) -> list[list[float]]:
    rates = row.get("recall_at_k") or {}
    cis = row.get("recall_ci_at_k") or {}
    down, up = [], []
    for k in text_chunk_metrics.RETRIEVAL_KS:
        rate = rates.get(str(k))
        ci = cis.get(str(k)) or {}
        if rate is None or ci.get("lo") is None or ci.get("hi") is None:
            down.append(0.0)
            up.append(0.0)
        else:
            down.append(max(0.0, float(rate) - float(ci["lo"])))
            up.append(max(0.0, float(ci["hi"]) - float(rate)))
    return [down, up]


def _strategy_label(row: Mapping[str, Any]) -> str:
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
    return strategy


def _protected_e2e_pins() -> dict[Path, str]:
    pins = {
        text_chunk_metrics.POINT_SWEEP_PATH: text_chunk_metrics.POINT_SWEEP_SHA256,
        text_chunk_metrics.PROBE_SWEEP_PATH: text_chunk_metrics.PROBE_SWEEP_SHA256,
        text_chunk_metrics.CURVE_SWEEP_PATH: text_chunk_metrics.CURVE_SWEEP_SHA256,
        text_chunk_metrics.CURVES_PATH: text_chunk_metrics.CURVES_V1_SHA256,
        text_chunk_metrics.CURVES_V1_PNG_PATH: text_chunk_metrics.CURVES_V1_PNG_SHA256,
        text_chunk_metrics.GOLD_UNSEALED_PATH: text_chunk_metrics.GOLD_UNSEALED_SHA256,
        text_chunk_metrics.CURVES_V3_PATH: CURVES_V3_SHA256,
        text_chunk_metrics.CURVES_V3_PNG_PATH: CURVES_V3_PNG_SHA256,
        text_chunk_metrics.ERROR_ANALYSIS_PATH: ERROR_ANALYSIS_SHA256,
        CURVES_T5_LEFTOVER_PATH: CURVES_T5_LEFTOVER_SHA256,
        CURVES_T5_LEFTOVER_PNG_PATH: CURVES_T5_LEFTOVER_PNG_SHA256,
        STORE_PATH: STORE_SHA256,
        FIGURE_PATH: "5d3ce85a4163edfae5a7ab9450451ca1b6e95fb148604e5672f1ec9ed5ea65ec",
    }
    return {path.resolve(): digest for path, digest in pins.items() if path.is_file()}


def _refuse_protected_dest(dest: Path) -> None:
    protected = set(_protected_e2e_pins())
    if dest.resolve() in protected:
        raise TextGoldError("protected_persist", "E2E emit must not overwrite published persist.")
    banned_names = {
        "CURVES.retrieval.v1.json",
        "CURVES.retrieval.v1.png",
        "CURVES.retrieval.v3.json",
        "CURVES.retrieval.v3.png",
        "CURVES.retrieval.T5.v1.json",
        "CURVES.retrieval.T5.v1.png",
        "CHUNKS.engine.v1.json",
        "ENGINE_CURVES.v1.json",
        "GOLD.v2.json",
        "GOLD.text.v1.unsealed.json",
        "user-library.json.gz",
    }
    if dest.name in banned_names:
        raise TextGoldError("protected_persist", "E2E emit must not overwrite published persist.")


def render_strategies_spans_png(artifact: Mapping[str, Any], dest: Path) -> None:
    """Left: named strategies. Right: T5 span buckets. Distinct palettes. Wilson bars."""
    if not palettes_are_disjoint():
        raise TextGoldError("palette_collision", "Strategy and bucket palettes must not share a colour.")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dest = Path(dest)
    _refuse_protected_dest(dest)
    strategies = named_strategy_series(artifact)
    buckets = t5_bucket_series(artifact)
    if not strategies or not buckets:
        raise TextGoldError("figure_series_missing", "Need named strategy series and T5 buckets.")
    ks = list(text_chunk_metrics.RETRIEVAL_KS)
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 5.0), sharex=True)
    fig.suptitle(figure_suptitle(artifact), fontsize=12)
    ax0, ax1 = axes
    for row in strategies:
        color = STRATEGY_COLORS[str(row["strategy"])]
        ax0.errorbar(
            ks, _ys(row), yerr=_yerr(row),
            fmt="o-", color=color, linewidth=2, markersize=5, capsize=3,
            label=_strategy_label(row),
        )
    ax0.set_title("Recall@k by strategy (index I)")
    ax0.set_ylabel("recall@k")
    ax0.text(
        0.02, 0.04,
        "Wilson 95% CI. Overlapping intervals are tied — not a ranking.",
        transform=ax0.transAxes, fontsize=7, va="bottom",
    )
    for row in buckets:
        bucket = str(row["bucket"])
        ax1.errorbar(
            ks, _ys(row), yerr=_yerr(row),
            fmt="s-", color=BUCKET_COLORS[bucket], linewidth=2, markersize=5,
            capsize=3, label=bucket,
        )
    ax1.set_title(f"{T5_RIGHT_TITLE} — recall@k by span bucket")
    ax1.set_ylabel("recall@k")
    for ax in axes:
        ax.set_xlabel("k (retrieved chunks)")
        ax.set_xticks(ks)
        ax.set_ylim(-0.02, 1.05)
        ax.grid(True, alpha=0.3)
    ax0.legend(loc="lower right", fontsize=8, frameon=False)
    ax1.legend(loc="lower right", fontsize=8, frameon=False, title="span bucket")
    fig.tight_layout()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, dpi=140, facecolor="white")
    plt.close(fig)


def _enrich_t5_chunk(
    chunk: Mapping[str, Any],
    *,
    canonical: Mapping[str, Any],
    paper_sha256: str,
    ordinal: int,
) -> dict[str, Any]:
    start, end = int(chunk["char_start"]), int(chunk["char_end"])
    text = str(canonical.get("canonical_text") or "")
    body = text[start:end]
    if body != str(chunk.get("body") or ""):
        raise TextGoldError("body_slice_mismatch", "chunk_t5 body is not the canonical slice.")
    blocks = _overlapping_blocks(canonical, start, end)
    section, origin = _section_stamp(blocks[0] if blocks else None)
    row = {
        "chunk_id": t5_store_chunk_id(paper_sha256, ordinal),
        "paper_sha256": paper_sha256,
        "ordinal": ordinal,
        "body": body,
        "char_start": start,
        "char_end": end,
        "page": _page_stamp(blocks),
        "section": section,
        "section_origin": origin,
        "block_ids": [
            str(block.get("block_id"))
            for block in blocks
            if block.get("block_id")
        ],
        "kind": _kind_stamp(blocks),
    }
    rebound = research.apply_table_rebound(
        [{"body": body, "char_start": start, "char_end": end}],
        canonical,
    )[0]
    sidecar = str(rebound.get("body_plus_rebound") or "")
    if sidecar and sidecar != body:
        row["body_plus_rebound"] = sidecar
    return row


def build_t5_store_chunks(
    canonicals: Sequence[Mapping[str, Any]],
    *,
    indexed_shas: set[str],
    paper_order: Sequence[str],
    forbidden_shas: set[str],
) -> list[dict[str, Any]]:
    by_sha = {text_chunk_metrics._paper_sha(row): row for row in canonicals}
    extra = set(by_sha) - indexed_shas
    if extra:
        raise TextGoldError("unexpected_paper", "Canonical list included a SHA that is not gold-indexed.")
    missing = indexed_shas - set(by_sha)
    if missing:
        raise TextGoldError("canonical_missing", "An indexed paper has no canonical.")
    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()
    order = [sha for sha in paper_order if sha in indexed_shas]
    for sha in list(order) + sorted(indexed_shas - set(order)):
        if sha in seen:
            continue
        seen.add(sha)
        if sha in forbidden_shas:
            raise TextGoldError("held_out_in_store", "Refusing to chunk a non-indexed paper.")
        canonical = by_sha[sha]
        if str(canonical.get("parser_backend") or "") != "docling":
            raise TextGoldError("parser_not_docling", "Indexed canonical is not docling.")
        packed = text_chunking.chunk_t5(canonical, target=text_chunking.T5_TARGET)
        for ordinal, raw in enumerate(packed, start=1):
            row = _enrich_t5_chunk(
                raw, canonical=canonical, paper_sha256=sha, ordinal=ordinal,
            )
            if row["paper_sha256"] in forbidden_shas:
                raise TextGoldError("held_out_in_store", "Store chunk carried a forbidden SHA.")
            chunks.append(row)
    return chunks


def emit_e2e_0_figure(
    *,
    artifact: Mapping[str, Any] | None = None,
    curves_path: Path | None = None,
    dest: Path | None = None,
    skip_pin_check: bool = False,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    dest = Path(dest or FIGURE_PATH)
    _refuse_protected_dest(dest)
    pins = {} if skip_pin_check else _protected_e2e_pins()
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="before E2E-0")
    source = Path(curves_path or text_chunk_metrics.CURVES_V3_PATH)
    if artifact is None:
        digest = file_sha256(source)
        if digest != CURVES_V3_SHA256 and source.resolve() == text_chunk_metrics.CURVES_V3_PATH.resolve():
            raise TextGoldError("curves_v3_moved", "Figure must be drawn from official I.")
        artifact = json.loads(source.read_text())
    render_strategies_spans_png(artifact, dest)
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="after E2E-0")
    return {"figure_path": str(dest), "suptitle": figure_suptitle(artifact)}


def emit_e2e_1_store(
    *,
    gold: Mapping[str, Any],
    gold_path: Path,
    census: Mapping[str, Any],
    census_path: Path,
    curves: Mapping[str, Any],
    dest: Path | None = None,
    canonicals: Sequence[Mapping[str, Any]] | None = None,
    expected_n_indexed: int = EXPECTED_N_INDEXED,
    expected_n_chunks: int = EXPECTED_N_CHUNKS,
    spec_path: Path | None = None,
    skip_pin_check: bool = False,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    dest = Path(dest or STORE_PATH)
    _refuse_protected_dest(dest)
    spec_file = Path(spec_path or SPEC_PATH)
    spec_digest = file_sha256(spec_file)
    if spec_file.resolve() == SPEC_PATH.resolve() and spec_digest != SPEC_SHA256:
        raise TextGoldError("spec_e2e_moved", "Emit only against the ADMITTED E2E bytes.")
    gold_digest = file_sha256(Path(gold_path))
    census_digest = file_sha256(Path(census_path))
    if (
        Path(gold_path).resolve() == text_chunk_metrics.GOLD_UNSEALED_PATH.resolve()
        and gold_digest != text_chunk_metrics.GOLD_UNSEALED_SHA256
    ):
        raise TextGoldError("gold_sha_moved", "Unsealed gold moved; do not emit against new bytes.")
    if Path(census_path).resolve() == CENSUS_PATH.resolve() and census_digest != CENSUS_V3_SHA256:
        raise TextGoldError("census_sha_moved", "CENSUS.v3 moved; do not emit against new bytes.")
    gold_sets = gold_status_sets(gold)
    census_sets = census_status_sets(census)
    if gold_sets["indexed"] != census_sets["indexed"]:
        raise TextGoldError("index_sha_mismatch", "Gold indexed SHAs are not the census indexed set.")
    if len(gold_sets["indexed"]) != expected_n_indexed:
        raise TextGoldError("index_sha_mismatch", "Indexed SHA count is not the accept integer.")
    forbidden = (
        gold_sets["held_out"]
        | census_sets["held_out"]
        | census_sets["excluded"]
        | {CONTAMINANT_SHA256}
    )
    if gold_sets["indexed"] & forbidden:
        raise TextGoldError("index_sha_mismatch", "An indexed SHA is also held-out or excluded.")
    paper_order = [
        str(row.get("paper_sha256") or "")
        for row in (gold.get("papers") or [])
        if str(row.get("paper_status") or "") == "indexed" and row.get("paper_sha256")
    ]
    if canonicals is None:
        loaded = []
        for sha in sorted(gold_sets["indexed"]):
            loaded.append(load_canonical(sha))
        canonicals = loaded
    pins = {} if skip_pin_check else _protected_e2e_pins()
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="before E2E-1")
    chunks = build_t5_store_chunks(
        canonicals,
        indexed_shas=gold_sets["indexed"],
        paper_order=paper_order,
        forbidden_shas=forbidden,
    )
    curves_n = _t5_n_chunks_from_curves(curves)
    if len(chunks) != expected_n_chunks or len(chunks) != curves_n:
        raise TextGoldError("n_chunks_mismatch", "T5 |I| is not the official 922 / curves n_chunks.")
    by_sha = {text_chunk_metrics._paper_sha(row): row for row in canonicals}
    for row in chunks:
        text = str((by_sha.get(row["paper_sha256"]) or {}).get("canonical_text") or "")
        sliced = text[int(row["char_start"]):int(row["char_end"])]
        if sliced != row["body"]:
            raise TextGoldError("body_slice_mismatch", "Store body is not the canonical slice.")
        if row["paper_sha256"] in forbidden:
            raise TextGoldError("held_out_in_store", "A store chunk came from a forbidden SHA.")
    payload = {
        "schema": STORE_SCHEMA,
        "spec_sha256": spec_digest,
        "gold_sha256": gold_digest,
        "census_sha256": census_digest,
        "source_curves_sha256": CURVES_V3_SHA256,
        "chunker": {"strategy": "T5", "params": {"target": text_chunking.T5_TARGET}},
        "n_indexed_papers": len(gold_sets["indexed"]),
        "n_chunks": len(chunks),
        "indexed_paper_sha256": sorted(gold_sets["indexed"]),
        "chunks": chunks,
    }
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_STORE_KEYS)):
        raise TextGoldError("gold_quoted", "Chunk store must not carry gold fact identifiers.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="after E2E-1")
    return {
        "store_path": str(dest),
        "store_sha256": file_sha256(dest),
        "n_chunks": len(chunks),
        "n_indexed_papers": len(gold_sets["indexed"]),
    }


def emit_e2e_0_and_1(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    curves_path: Path | None = None,
    figure_dest: Path | None = None,
    store_dest: Path | None = None,
    canonicals: Sequence[Mapping[str, Any]] | None = None,
    expected_n_indexed: int = EXPECTED_N_INDEXED,
    expected_n_chunks: int = EXPECTED_N_CHUNKS,
    spec_path: Path | None = None,
    skip_pin_check: bool = False,
) -> dict[str, Any]:
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or CENSUS_PATH)
    curves_file = Path(curves_path or text_chunk_metrics.CURVES_V3_PATH)
    gold = json.loads(gold_file.read_text())
    census = json.loads(census_file.read_text())
    curves = json.loads(curves_file.read_text())
    if (
        curves_file.resolve() == text_chunk_metrics.CURVES_V3_PATH.resolve()
        and file_sha256(curves_file) != CURVES_V3_SHA256
    ):
        raise TextGoldError("curves_v3_moved", "Figure and store must pin official I.")
    figure = emit_e2e_0_figure(
        artifact=curves,
        curves_path=curves_file,
        dest=figure_dest,
        skip_pin_check=skip_pin_check,
    )
    store = emit_e2e_1_store(
        gold=gold,
        gold_path=gold_file,
        census=census,
        census_path=census_file,
        curves=curves,
        dest=store_dest,
        canonicals=canonicals,
        expected_n_indexed=expected_n_indexed,
        expected_n_chunks=expected_n_chunks,
        spec_path=spec_path,
        skip_pin_check=skip_pin_check,
    )
    return {**figure, **store}


def store_to_literature_index(
    store: Mapping[str, Any],
    *,
    knowledgebase: str = KNOWLEDGEBASE_ID,
) -> dict[str, Any]:
    """Address the T5 store as a live literature index. Dense stays null."""
    slug = research._slug(knowledgebase)
    if slug == "user-library":
        raise TextGoldError("protected_persist", "E2E-2 must not write user-library.")
    indexed = [str(sha) for sha in (store.get("indexed_paper_sha256") or [])]
    if set(indexed) != {str(row.get("paper_sha256") or "") for row in (store.get("chunks") or [])}:
        raise TextGoldError("index_sha_mismatch", "Store header SHAs do not match chunk paper_sha256.")
    documents = []
    for sha in indexed:
        documents.append({
            "document_id": f"D{sha[:16]}",
            "sha256": sha,
            "title": "",
            "source": "",
            "parser_backend": "docling",
        })
    doc_ids = {row["sha256"]: row["document_id"] for row in documents}
    chunks = []
    for row in store.get("chunks") or []:
        body = str(row.get("body") or "")
        item = {
            "chunk_id": str(row["chunk_id"]),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "document_id": doc_ids[str(row["paper_sha256"])],
            "paper_sha256": str(row["paper_sha256"]),
            "title": "",
            "source": "",
            "page": row.get("page"),
            "section": row.get("section") or "",
            "section_origin": row.get("section_origin"),
            "kind": row.get("kind"),
            "char_start": row.get("char_start"),
            "char_end": row.get("char_end"),
            "text": body,
            "body": body,
            "token_estimate": max(1, math.ceil(len(body) / 4)),
        }
        sidecar = row.get("body_plus_rebound")
        if sidecar and str(sidecar) != body:
            item["body_plus_rebound"] = sidecar
        chunks.append(item)
    if len(chunks) != int(store.get("n_chunks") or 0):
        raise TextGoldError("n_chunks_mismatch", "Index chunk count does not match the store.")
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": slug,
        "documents": documents,
        "chunks": chunks,
        "dense": None,
    }


def planted_sparse_top5(
    index: Mapping[str, Any],
    *,
    chunk_id: str,
    token: str = PLANT_TOKEN,
) -> list[str]:
    """Constructed query. Plants a fixture token in one body. Not a gold needle."""
    planted = json.loads(json.dumps(index))
    hit = False
    for chunk in planted["chunks"]:
        if chunk["chunk_id"] == chunk_id:
            body = str(chunk.get("body") or chunk.get("text") or "")
            chunk["body"] = f"{token} {body}"
            chunk["text"] = chunk["body"]
            chunk.pop("body_plus_rebound", None)
            hit = True
            break
    if not hit:
        raise TextGoldError("plant_miss", "Constructed plant target chunk_id is missing.")
    rows = research._search_index(planted, token, 5, "sparse")
    return [str(row["chunk_id"]) for row in rows]


def emit_e2e_2(
    *,
    store_path: Path | None = None,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    dest: Path | None = None,
    index_home: Path | None = None,
    expected_n_indexed: int = EXPECTED_N_INDEXED,
    expected_n_chunks: int = EXPECTED_N_CHUNKS,
    spec_path: Path | None = None,
    skip_pin_check: bool = False,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    dest = Path(dest or MANIFEST_PATH)
    _refuse_protected_dest(dest)
    store_file = Path(store_path or STORE_PATH)
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or CENSUS_PATH)
    spec_file = Path(spec_path or SPEC_PATH)
    if store_file.resolve() == STORE_PATH.resolve() and file_sha256(store_file) != STORE_SHA256:
        raise TextGoldError("store_moved", "E2E-2 must address the PASSed T5 store.")
    if spec_file.resolve() == SPEC_PATH.resolve() and file_sha256(spec_file) != SPEC_SHA256:
        raise TextGoldError("spec_e2e_moved", "Emit only against the ADMITTED E2E bytes.")
    gold = json.loads(gold_file.read_text())
    census = json.loads(census_file.read_text())
    store = json.loads(store_file.read_text())
    gold_sets = gold_status_sets(gold)
    census_sets = census_status_sets(census)
    store_shas = set(store.get("indexed_paper_sha256") or [])
    if store_shas != gold_sets["indexed"] or store_shas != census_sets["indexed"]:
        raise TextGoldError("index_sha_mismatch", "Store SHA set is not gold/census indexed I.")
    if len(store_shas) != expected_n_indexed or int(store.get("n_chunks") or 0) != expected_n_chunks:
        raise TextGoldError("n_chunks_mismatch", "Store counts are not the E2E-1 accept integers.")
    pins = {} if skip_pin_check else _protected_e2e_pins()
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="before E2E-2")
    index = store_to_literature_index(store, knowledgebase=KNOWLEDGEBASE_ID)
    live_shas = {str(row.get("sha256") or "") for row in index["documents"]}
    if live_shas != gold_sets["indexed"]:
        raise TextGoldError("index_sha_mismatch", "Live document SHAs are not gold indexed I.")
    if len(index["chunks"]) != expected_n_chunks:
        raise TextGoldError("n_chunks_mismatch", "Live n_chunks is not the store.")
    home = Path(index_home or INDEX_HOME)
    home.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("DISSOLVE_RESEARCH_HOME")
    os.environ["DISSOLVE_RESEARCH_HOME"] = str(home)
    try:
        index_path = research._save_index(index)
        loaded = research._load_index(KNOWLEDGEBASE_ID)
    finally:
        if previous is None:
            os.environ.pop("DISSOLVE_RESEARCH_HOME", None)
        else:
            os.environ["DISSOLVE_RESEARCH_HOME"] = previous
    loaded_shas = {str(row.get("sha256") or "") for row in loaded["documents"]}
    if loaded_shas != gold_sets["indexed"] or len(loaded["chunks"]) != expected_n_chunks:
        raise TextGoldError("index_sha_mismatch", "Reloaded index is not I.")
    if loaded.get("dense") is not None:
        raise TextGoldError("dense_present", "E2E-2 must not persist dense vectors.")
    plant_ids = planted_sparse_top5(loaded, chunk_id=str(loaded["chunks"][0]["chunk_id"]))
    if loaded["chunks"][0]["chunk_id"] not in plant_ids:
        raise TextGoldError("plant_not_retrieved", "Constructed plant token missed top-5 sparse.")
    payload = {
        "schema": MANIFEST_SCHEMA,
        "spec_sha256": file_sha256(spec_file),
        "gold_sha256": file_sha256(gold_file),
        "census_sha256": file_sha256(census_file),
        "chunk_store_sha256": file_sha256(store_file),
        "knowledgebase": KNOWLEDGEBASE_ID,
        "index_path": str(index_path),
        "n_indexed_papers": len(live_shas),
        "n_chunks": len(loaded["chunks"]),
        "indexed_paper_sha256": sorted(live_shas),
        "embedder_in_index": False,
        "dense": None,
    }
    if text_chunk_metrics._contains_forbidden_keys(
        payload, set(_FORBIDDEN_STORE_KEYS) | {"recall_at_k", "retr@k", "f1", "provisional"},
    ):
        raise TextGoldError("gold_quoted", "Index manifest must not carry gold text or a score.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="after E2E-2")
    return {
        "manifest_path": str(dest),
        "manifest_sha256": file_sha256(dest),
        "index_path": str(index_path),
        "n_chunks": len(loaded["chunks"]),
        "n_indexed_papers": len(live_shas),
        "knowledgebase": KNOWLEDGEBASE_ID,
        "planted_in_top5": True,
    }


def embed_inputs_for_index(index: Mapping[str, Any]) -> list[str]:
    """C7d selector. Not body-only when rebound is present."""
    return [research.chunk_sparse_corpus(chunk) for chunk in (index.get("chunks") or [])]


def embed_t5_index(
    index: Mapping[str, Any],
    *,
    embedder=None,
    model_name: str | None = None,
    expected_dim: int | None = None,
) -> dict[str, Any]:
    """Attach dense vectors aligned to chunks. Legacy default MiniLM. No gold score."""
    slug = str(index.get("knowledgebase") or "")
    if slug == "user-library":
        raise TextGoldError("protected_persist", "E2E-3 must not write user-library.")
    chunks = list(index.get("chunks") or [])
    if not chunks:
        raise TextGoldError("empty_index", "Cannot embed an empty index.")
    texts = embed_inputs_for_index(index)
    if len(texts) != len(chunks):
        raise TextGoldError("embed_align", "Embed inputs are not aligned to chunks.")
    selected = MINILM_ID if model_name is None else str(model_name)
    compatible_dim = research._compatible_embedding_dim(selected)
    if expected_dim is not None and expected_dim != compatible_dim:
        raise TextGoldError("dense_dim", "Embedding dim conflicts with the selected model.")
    if embedder is None:
        model_id, vectors = research._dense_vectors(texts, selected)
    else:
        model_id, vectors = embedder(texts, selected)
    if str(model_id) != selected:
        raise TextGoldError("dense_model", "Embedder returned a substitute model id.")
    try:
        count = len(vectors)
    except TypeError:
        count = -1
    if count != len(chunks):
        raise TextGoldError("n_chunks_mismatch", "Vector count is not n_chunks.")
    try:
        dim = research._assert_generated_dense_vectors(
            vectors,
            expected_count=len(chunks),
            model_name=str(model_id),
            expected_dim=compatible_dim,
        )
    except research.LiteratureContractError as error:
        raise TextGoldError("dense_dim", "Generated dense vectors are invalid.") from error
    out = dict(index)
    out["dense"] = research._attach_generated_recipe({
        "model": str(model_id),
        "dim": dim,
        "chunk_ids": [str(chunk["chunk_id"]) for chunk in chunks],
        "vectors": vectors,
    })
    return out


def _plant_copy(
    index: Mapping[str, Any],
    *,
    chunk_id: str,
    token: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    planted = {
        "schema": index.get("schema"),
        "knowledgebase": index.get("knowledgebase"),
        "documents": json.loads(json.dumps(index.get("documents") or [])),
        "chunks": json.loads(json.dumps(index.get("chunks") or [])),
        "dense": None,
    }
    hit = None
    for chunk in planted["chunks"]:
        if str(chunk.get("chunk_id")) == chunk_id:
            body = str(chunk.get("body") or chunk.get("text") or "")
            chunk["body"] = f"{token} {body}"
            chunk["text"] = chunk["body"]
            chunk.pop("body_plus_rebound", None)
            hit = chunk
            break
    if hit is None:
        raise TextGoldError("plant_miss", "Constructed plant target chunk_id is missing.")
    return planted, hit


def served_row_has_provenance(row: Mapping[str, Any]) -> bool:
    return all(key in row for key in SERVED_PROVENANCE_KEYS)


def prove_e2e4_contract(
    index: Mapping[str, Any],
    *,
    chunk_id: str | None = None,
    token: str = PLANT_TOKEN,
    mutate_plant: bool = True,
) -> dict[str, Any]:
    """Constructed plant + nonsense. Counts only. No gold rate."""
    chunks = list(index.get("chunks") or [])
    if not chunks:
        raise TextGoldError("empty_index", "Cannot prove provenance on an empty index.")
    target_id = chunk_id or str(chunks[0]["chunk_id"])
    store_row = next((chunk for chunk in chunks if str(chunk.get("chunk_id")) == target_id), None)
    if store_row is None:
        raise TextGoldError("plant_miss", "Constructed plant target chunk_id is missing.")
    if mutate_plant:
        working, planted_row = _plant_copy(index, chunk_id=target_id, token=token)
    else:
        working, planted_row = index, store_row
    rows = research._search_index(working, token, 5, "sparse")
    match = next((row for row in rows if str(row.get("chunk_id")) == target_id), None)
    expected_sha = research._chunk_paper_sha256(index, store_row)
    slice_has_token = token in str(planted_row.get("body") or planted_row.get("text") or "")
    planted_ok = bool(
        match
        and served_row_has_provenance(match)
        and match.get("paper_sha256") == expected_sha
        and match.get("paper_sha256") != planted_row.get("sha256")
        and match.get("char_start") == store_row.get("char_start")
        and match.get("char_end") == store_row.get("char_end")
        and match.get("section_origin") == store_row.get("section_origin")
        and token in str(match.get("excerpt") or "")
        and slice_has_token
    )
    nonsense = research._search_index(index, NONSENSE_QUERY, 5, "sparse")
    empty_ok = nonsense == []
    if index.get("dense"):
        empty_ok = empty_ok and research._search_index(index, NONSENSE_QUERY, 5, "hybrid") == []
        empty_ok = empty_ok and research._search_index(index, NONSENSE_QUERY, 5, "dense") == []
    return {
        "planted_match": planted_ok,
        "empty_on_no_match": empty_ok,
        "n_plant_hits": len(rows),
        "n_nonsense": len(nonsense),
    }


def hybrid_envelope_ok(raw: str) -> bool:
    from .contracts import parse_tool_result
    parsed = parse_tool_result(raw)
    data = parsed["data"]
    if data.get("error_code") == "dense_index_unavailable":
        return False
    return bool(data.get("success"))


def _wrap_contract(error: BaseException) -> None:
    if isinstance(error, research.LiteratureContractError):
        raise TextGoldError(error.code, str(error)) from error
    raise error


def _gzip_index_bytes(index: Mapping[str, Any]) -> bytes:
    body = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        compressed.write(body)
    return buffer.getvalue()


def _exclusive_create_write(path: Path, data: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(destination, flags, 0o644)
    except FileExistsError as error:
        raise TextGoldError("dest_exists", "Artifact destination already exists.") from error
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise TextGoldError("artifact_write_failed", "Artifact write failed.") from error


def _explicit_bge_writer_requested(
    *,
    model_name: str | None,
    source_index: Mapping[str, Any] | None,
    reuse_index: Mapping[str, Any] | None,
    output_index_path: Path | str | None,
    frozen_floor: Any,
) -> bool:
    if model_name is not None and str(model_name) == research._BGE_MODEL_ID:
        return True
    return (
        source_index is not None
        or reuse_index is not None
        or output_index_path is not None
        or frozen_floor is not None
    )


def _require_product_identity(index: Mapping[str, Any]) -> None:
    kb = index.get("knowledgebase")
    try:
        if not isinstance(kb, str) or research._slug(kb) != research._PRODUCT_KNOWLEDGEBASE:
            raise TextGoldError(
                "bge10_index_knowledgebase",
                "BGE serving knowledgebase is mismatched.",
            )
    except ValueError as error:
        raise TextGoldError(
            "bge10_index_knowledgebase",
            "BGE serving knowledgebase is mismatched.",
        ) from error
    if index.get("schema") != research._INDEX_SCHEMA:
        raise TextGoldError("bge10_index_schema", "BGE serving index schema is incompatible.")


def _require_frozen_floor(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TextGoldError("bge_floor_invalid", "Supplied frozen floor is invalid.")
    try:
        numeric = float(value)
    except OverflowError as error:
        raise TextGoldError("bge_floor_invalid", "Supplied frozen floor is invalid.") from error
    if not math.isfinite(numeric) or numeric != BGE_FROZEN_FLOOR:
        raise TextGoldError("bge_floor_invalid", "Supplied frozen floor is invalid.")
    return value


def _require_new_artifact_path(path: Path | str, *, peer: Path | None = None) -> Path:
    dest = Path(path).expanduser()
    if dest.is_symlink():
        raise TextGoldError("dest_symlink", "Artifact destination must not be a symlink.")
    if dest.exists():
        raise TextGoldError("dest_exists", "Artifact destination already exists.")
    if dest.name in _MINILM_PROTECTED_BASENAMES:
        raise TextGoldError("protected_persist", "E2E emit must not overwrite published persist.")
    _refuse_protected_dest(dest)
    resolved = dest.resolve()
    protected_targets = [research._canonical_product_index_path(), research._product_manifest_path()]
    protected_targets.extend(research._protected_index_targets())
    for protected in protected_targets:
        if resolved == Path(protected).expanduser().resolve():
            raise TextGoldError("protected_persist", "E2E emit must not overwrite published persist.")
    if peer is not None and resolved == Path(peer).expanduser().resolve():
        raise TextGoldError("dest_not_distinct", "Index and manifest destinations must be distinct.")
    return resolved


def _declared_index_path(index_dest: Path, manifest_dest: Path) -> str:
    index_resolved = Path(index_dest).expanduser().resolve()
    manifest_parent = Path(manifest_dest).expanduser().resolve().parent
    try:
        relative = index_resolved.relative_to(manifest_parent)
    except ValueError:
        return str(index_resolved)
    return relative.as_posix()


def _reuse_vectors_by_id(reuse_index: Mapping[str, Any]) -> dict[str, list[float]]:
    chunks = list(reuse_index.get("chunks") or [])
    try:
        dense = research._as_dense_mapping(reuse_index)
        recipe = research._require_bge_recipe_fields(dense, "bge10_recipe_mismatch")
        validated = research._validated_dense_side(reuse_index, chunks)
    except research.LiteratureContractError as error:
        _wrap_contract(error)
    if (
        validated["model"] != recipe["model"]
        or validated["dim"] != recipe["dim"]
        or validated["query_instruction"] != recipe["query_instruction"]
        or validated["passage_instruction"] != recipe["passage_instruction"]
        or validated["encoder_revision"] != recipe["encoder_revision"]
    ):
        raise TextGoldError("bge10_recipe_mismatch", "BGE serving recipe is incompatible.")
    return {chunk_id: list(vector) for chunk_id, vector in validated["by_id"].items()}


def _verify_written_bge_index(
    path: Path,
    *,
    expected_digest: str,
    expected_index: Mapping[str, Any],
) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_digest:
        raise TextGoldError("bge10_index_digest", "BGE serving index digest is invalid.")
    try:
        decoded = research._read_gzip_json_bytes(raw)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        gzip.BadGzipFile,
        EOFError,
        zlib.error,
    ) as error:
        raise TextGoldError("bge10_index_malformed", "BGE serving index is malformed.") from error
    if not isinstance(decoded, dict):
        raise TextGoldError("bge10_index_invalid", "BGE serving index is not an object.")
    _require_product_identity(decoded)
    if decoded.get("chunks") != expected_index.get("chunks"):
        raise TextGoldError("reuse_content", "Written chunk records do not match the source.")
    if decoded.get("documents") != expected_index.get("documents"):
        raise TextGoldError("reuse_content", "Written document records do not match the source.")
    dense = decoded.get("dense")
    if not isinstance(dense, Mapping):
        raise TextGoldError("bge10_recipe_mismatch", "BGE serving recipe is incompatible.")
    try:
        research._require_bge_recipe_fields(dense, "bge10_recipe_mismatch")
        ordered_ids = [str(chunk["chunk_id"]) for chunk in decoded.get("chunks") or []]
        if list(dense.get("chunk_ids") or []) != ordered_ids:
            raise TextGoldError("bge10_chunk_membership", "BGE serving chunk membership is incompatible.")
        expected_dense = expected_index.get("dense") or {}
        if list(dense.get("chunk_ids") or []) != list(expected_dense.get("chunk_ids") or []):
            raise TextGoldError("bge10_chunk_membership", "BGE serving chunk membership is incompatible.")
        if list(dense.get("vectors") or []) != list(expected_dense.get("vectors") or []):
            raise TextGoldError("dense_dim", "Generated dense vectors are invalid.")
        research._validate_dense_rows(
            ordered_ids,
            dense.get("vectors"),
            dim=research._BGE_DIM,
            require_bge_geometry=True,
        )
        research._assert_generated_dense_vectors(
            dense.get("vectors"),
            expected_count=len(ordered_ids),
            model_name=research._BGE_MODEL_ID,
            expected_dim=research._BGE_DIM,
        )
    except research.LiteratureContractError as error:
        _wrap_contract(error)
    return decoded


def _emit_bge_artifact(
    *,
    manifest_path: Path | None,
    expected_n_chunks: int,
    embedder,
    model_name: str | None,
    expected_dim: int | None,
    source_index: Mapping[str, Any] | None,
    reuse_index: Mapping[str, Any] | None,
    output_index_path: Path | str | None,
    frozen_floor: Any,
) -> dict[str, Any]:
    if (
        manifest_path is None
        or source_index is None
        or reuse_index is None
        or output_index_path is None
        or frozen_floor is None
        or model_name is None
        or expected_dim is None
    ):
        raise TextGoldError("bge_writer_incomplete", "BGE artifact writer requires explicit inputs.")
    selected = str(model_name)
    if selected != research._BGE_MODEL_ID:
        raise TextGoldError("dense_model", "BGE artifact writer requires the pinned BGE model.")
    compatible_dim = research._compatible_embedding_dim(selected)
    if expected_dim != compatible_dim or expected_dim != research._BGE_DIM:
        raise TextGoldError("dense_dim", "Embedding dim conflicts with the selected model.")
    if not isinstance(source_index, Mapping) or not isinstance(reuse_index, Mapping):
        raise TextGoldError("bge_writer_incomplete", "BGE artifact writer requires explicit indexes.")
    index_dest = _require_new_artifact_path(Path(output_index_path))
    dest = _require_new_artifact_path(Path(manifest_path), peer=index_dest)
    _require_new_artifact_path(index_dest, peer=dest)
    pinned_floor = _require_frozen_floor(frozen_floor)
    _require_product_identity(source_index)
    _require_product_identity(reuse_index)
    source_chunks = list(source_index.get("chunks") or [])
    reuse_chunks = list(reuse_index.get("chunks") or [])
    try:
        source_ids = research._side_chunk_ids(source_chunks)
    except research.LiteratureContractError as error:
        _wrap_contract(error)
    if len(source_ids) != expected_n_chunks:
        raise TextGoldError("n_chunks_mismatch", "Source cardinality is not the expected total.")
    reuse_by_id = _reuse_vectors_by_id(reuse_index)
    reuse_ids = list(reuse_by_id)
    if len(reuse_ids) != len(set(reuse_ids)):
        raise TextGoldError("bge10_chunk_membership", "BGE serving chunk membership is incompatible.")
    source_chunk_by_id = {str(chunk["chunk_id"]): chunk for chunk in source_chunks}
    reuse_chunk_by_id = {str(chunk["chunk_id"]): chunk for chunk in reuse_chunks}
    for chunk_id in reuse_ids:
        if chunk_id not in source_chunk_by_id:
            raise TextGoldError("bge10_chunk_membership", "BGE serving chunk membership is incompatible.")
        if source_chunk_by_id[chunk_id] != reuse_chunk_by_id.get(chunk_id):
            raise TextGoldError("reuse_content", "Reused chunk records must equal the source records.")
    missing_chunks = [chunk for chunk in source_chunks if str(chunk["chunk_id"]) not in reuse_by_id]
    new_by_id: dict[str, list[float]] = {}
    if missing_chunks:
        partial = {
            "schema": source_index.get("schema"),
            "knowledgebase": source_index.get("knowledgebase"),
            "documents": list(source_index.get("documents") or []),
            "chunks": missing_chunks,
            "dense": None,
        }
        encoded = embed_t5_index(
            partial,
            embedder=embedder,
            model_name=selected,
            expected_dim=expected_dim,
        )
        encoded_dense = encoded.get("dense") or {}
        encoded_ids = [str(item) for item in (encoded_dense.get("chunk_ids") or [])]
        if encoded_ids != [str(chunk["chunk_id"]) for chunk in missing_chunks]:
            raise TextGoldError("embed_align", "Encoded chunk_ids are not the missing source order.")
        new_by_id = {
            chunk_id: list(vector)
            for chunk_id, vector in zip(encoded_ids, encoded_dense.get("vectors") or [])
        }
        if set(new_by_id) != {str(chunk["chunk_id"]) for chunk in missing_chunks}:
            raise TextGoldError("embed_align", "Encoded vectors are not aligned to missing chunks.")
    elif embedder is not None:
        # All-reuse: the encoder must not be consulted.
        pass
    ordered_vectors: list[list[float]] = []
    for chunk in source_chunks:
        chunk_id = str(chunk["chunk_id"])
        if chunk_id in reuse_by_id:
            ordered_vectors.append(list(reuse_by_id[chunk_id]))
        else:
            if chunk_id not in new_by_id:
                raise TextGoldError("embed_align", "Missing source IDs were not encoded.")
            ordered_vectors.append(list(new_by_id[chunk_id]))
    try:
        dim = research._assert_generated_dense_vectors(
            ordered_vectors,
            expected_count=len(source_chunks),
            model_name=selected,
            expected_dim=expected_dim,
        )
        research._validate_dense_rows(
            source_ids,
            ordered_vectors,
            dim=research._BGE_DIM,
            require_bge_geometry=True,
        )
    except research.LiteratureContractError as error:
        _wrap_contract(error)
    output = copy.deepcopy(dict(source_index))
    output["schema"] = research._INDEX_SCHEMA
    output["knowledgebase"] = research._PRODUCT_KNOWLEDGEBASE
    output["documents"] = copy.deepcopy(list(source_index.get("documents") or []))
    output["chunks"] = copy.deepcopy(source_chunks)
    output["dense"] = research._attach_generated_recipe({
        "model": selected,
        "dim": dim,
        "chunk_ids": list(source_ids),
        "vectors": ordered_vectors,
    })
    gzip_bytes = _gzip_index_bytes(output)
    digest = hashlib.sha256(gzip_bytes).hexdigest()
    declared_index = _declared_index_path(index_dest, dest)
    _exclusive_create_write(index_dest, gzip_bytes)
    _verify_written_bge_index(index_dest, expected_digest=digest, expected_index=output)
    manifest = {
        "knowledgebase": research._PRODUCT_KNOWLEDGEBASE,
        "index_path": declared_index,
        "gzip_sha256": digest,
        "dense": {
            "model": selected,
            "dim": dim,
            "query_instruction": research._BGE_QUERY_INSTRUCTION,
            "passage_instruction": research._BGE_PASSAGE_INSTRUCTION,
            "encoder_revision": research._BGE_ENCODER_REVISION,
            "chunk_ids": list(source_ids),
        },
        "abstention": {"floor": pinned_floor},
        "n_vectors": len(ordered_vectors),
        "n_chunks": len(source_chunks),
        "n_documents": len(output["documents"]),
        "status": BGE_EXPLORATORY_STATUS,
    }
    if text_chunk_metrics._contains_forbidden_keys(
        manifest,
        set(_FORBIDDEN_STORE_KEYS) | {"recall_at_k", "retr@k", "f1", "provisional", "delta"},
    ):
        raise TextGoldError("gold_quoted", "E2E-3 must not persist a gold score.")
    _exclusive_create_write(
        dest,
        (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return {
        "manifest_path": str(dest),
        "manifest_sha256": file_sha256(dest),
        "index_path": str(index_dest),
        "gzip_sha256": digest,
        "n_vectors": len(ordered_vectors),
        "n_chunks": len(source_chunks),
        "n_documents": len(output["documents"]),
        "dim": int(dim),
        "model": selected,
        "query_instruction": research._BGE_QUERY_INSTRUCTION,
        "passage_instruction": research._BGE_PASSAGE_INSTRUCTION,
        "encoder_revision": research._BGE_ENCODER_REVISION,
        "status": BGE_EXPLORATORY_STATUS,
        "construction": "built",
    }


def emit_e2e_3(
    *,
    index_home: Path | None = None,
    manifest_path: Path | None = None,
    expected_n_chunks: int = EXPECTED_N_CHUNKS,
    embedder=None,
    skip_pin_check: bool = False,
    model_name: str | None = None,
    expected_dim: int | None = None,
    source_index: Mapping[str, Any] | None = None,
    reuse_index: Mapping[str, Any] | None = None,
    output_index_path: Path | str | None = None,
    frozen_floor: Any = None,
) -> dict[str, Any]:
    if _explicit_bge_writer_requested(
        model_name=model_name,
        source_index=source_index,
        reuse_index=reuse_index,
        output_index_path=output_index_path,
        frozen_floor=frozen_floor,
    ):
        return _emit_bge_artifact(
            manifest_path=manifest_path,
            expected_n_chunks=expected_n_chunks,
            embedder=embedder,
            model_name=model_name,
            expected_dim=expected_dim,
            source_index=source_index,
            reuse_index=reuse_index,
            output_index_path=output_index_path,
            frozen_floor=frozen_floor,
        )
    selected = MINILM_ID if model_name is None else str(model_name)
    compatible_dim = research._compatible_embedding_dim(selected)
    dim_expected = EXPECTED_DIM if expected_dim is None else expected_dim
    if dim_expected != compatible_dim:
        raise TextGoldError("dense_dim", "Embedding dim conflicts with the selected model.")
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    dest = Path(manifest_path or MANIFEST_PATH)
    home = Path(index_home or INDEX_HOME)
    pins = {} if skip_pin_check else _protected_e2e_pins()
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="before E2E-3")
    previous = os.environ.get("DISSOLVE_RESEARCH_HOME")
    os.environ["DISSOLVE_RESEARCH_HOME"] = str(home)
    try:
        loaded = research._load_index(KNOWLEDGEBASE_ID)
        if len(loaded.get("chunks") or []) != expected_n_chunks:
            raise TextGoldError("n_chunks_mismatch", "Live index is not the E2E-2 store.")
        embedded = embed_t5_index(
            loaded,
            embedder=embedder,
            model_name=selected,
            expected_dim=dim_expected,
        )
        index_path = research._save_index(embedded)
        again = research._load_index(KNOWLEDGEBASE_ID)
        dense = again.get("dense") or {}
        if len(dense.get("vectors") or []) != expected_n_chunks:
            raise TextGoldError("n_chunks_mismatch", "Saved dense length is not n_chunks.")
        if int(dense.get("dim") or 0) != dim_expected:
            raise TextGoldError("dense_dim", "Saved dim is not the selected model width.")
        if list(dense.get("chunk_ids") or []) != [str(c["chunk_id"]) for c in again["chunks"]]:
            raise TextGoldError("embed_align", "Dense chunk_ids are not keyed to the store.")
        raw_hybrid = research.search_literature_corpus(
            "E2E3HYBRIDPROBE", knowledgebase=KNOWLEDGEBASE_ID, retrieval_mode="hybrid",
        )
        raw_dense = research.search_literature_corpus(
            "E2E3DENSEPROBE", knowledgebase=KNOWLEDGEBASE_ID, retrieval_mode="dense",
        )
    finally:
        if previous is None:
            os.environ.pop("DISSOLVE_RESEARCH_HOME", None)
        else:
            os.environ["DISSOLVE_RESEARCH_HOME"] = previous
    if not hybrid_envelope_ok(raw_hybrid) or not hybrid_envelope_ok(raw_dense):
        raise TextGoldError("dense_index_unavailable", "hybrid/dense still raise dense_index_unavailable.")
    payload = json.loads(dest.read_text()) if dest.is_file() else {
        "schema": MANIFEST_SCHEMA,
        "knowledgebase": KNOWLEDGEBASE_ID,
    }
    payload["embedder_in_index"] = True
    payload["dense"] = {
        "model": dense["model"],
        "dim": int(dense["dim"]),
        "n_vectors": len(dense["vectors"]),
    }
    payload["index_path"] = str(index_path)
    if text_chunk_metrics._contains_forbidden_keys(
        payload,
        set(_FORBIDDEN_STORE_KEYS) | {"recall_at_k", "retr@k", "f1", "provisional", "delta"},
    ):
        raise TextGoldError("gold_quoted", "E2E-3 must not persist a gold score.")
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    if pins:
        text_chunk_metrics._assert_protected_unmoved(pins, when="after E2E-3")
    return {
        "manifest_path": str(dest),
        "manifest_sha256": file_sha256(dest),
        "index_path": str(index_path),
        "n_vectors": len(dense["vectors"]),
        "dim": int(dense["dim"]),
        "model": dense["model"],
        "hybrid_ok": True,
        "dense_ok": True,
    }


def main() -> None:
    result = emit_e2e_0_and_1()
    print(json.dumps({
        "figure_path": result["figure_path"],
        "store_path": result["store_path"],
        "store_sha256": result["store_sha256"],
        "n_chunks": result["n_chunks"],
        "n_indexed_papers": result["n_indexed_papers"],
        "suptitle": result["suptitle"],
    }, indent=2))


if __name__ == "__main__":
    main()
