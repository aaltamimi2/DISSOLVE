"""ENGINE_E2E_SPEC.v1 E2E-0 figure, E2E-1 store, E2E-2 index. No score. No MiniLM."""

from __future__ import annotations

import hashlib
import json
import math
import os
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
            chunk["body"] = f"{body} {token}"
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
