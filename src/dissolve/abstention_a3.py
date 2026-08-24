"""A-3 off-domain query mint. Unpaid pdftotext only. No gold needles. No MiniLM."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import dense_d2, dense_d3, engine_e2e, gold_ensemble, research, text_chunk_metrics
from .gold_ensemble import GoldEnsembleError, file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "734f4a9c7b64ff700ab3c5f99b0844c00b8180adb016430f24ecd9637ae3e0da"
SET_DIGEST = "7081eedb65f97f2e8e4714fe0337b6ac47d6b1f1a678a199d222bb8c4fffeb90"
SCHEMA = "dissolve.offdomain.queries.v1"
A3_ROOT = Path.home() / "titania-adsorption-new"
OFFDOMAIN_NAME = "OFFDOMAIN.queries.v1.json"
OFFDOMAIN_PATH = DEFAULT_OUT_DIR / OFFDOMAIN_NAME
MAX_QUERIES_PER_PDF = 6
WINDOW_LEN = 12
WINDOW_COUNT = 5
MIN_WINDOW_TOKENS = 6
TITLE_MIN_CHARS = 8

A3_PDFS: tuple[tuple[str, str], ...] = (
    (
        "references/Luan2015_TiO2-LJ-forcefield_JCP-142-234102.pdf",
        "1a37dc42f9596f84381fdeb3c2eedda0889836f58857d4cceb14dbe1ca84b18d",
    ),
    (
        "references/Monti-Walsh-2010-PMF-aminoacid-analogues-aqueous-titania-JPCC.pdf",
        "88e8ea49bc469a47d1c89478346472e3060831d0fa6da23c5f15339a52c264b4",
    ),
    (
        "references/Predota2004_rutile110-EDL_JPCB-108-12049.pdf",
        "13062fa04850ed6e823d313b29cd10de703579bbe2fb1da62bb78a338242c472",
    ),
    (
        "references/titania-grafting-1.pdf",
        "b0942ab15bb1e40af22556c6a5b20258b6e12db863cf4074a93c6d38b33ede1b",
    ),
)

_PROTECTED_NAMES = frozenset({
    "GOLD.text.v1.unsealed.json",
    "GOLD.v2.json",
    "CENSUS.v3.json",
    "CHUNKS.t5.indexed.unsealed.v1.json",
    "INDEX.t5.unsealed.v1.json",
    "t5-indexed-unsealed.json.gz",
    "CURVES.retrieval.v3.json",
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.d3.json",
    "CURVES.retrieval.d3.finding.json",
    "CURVES.retrieval.weights.v1.json",
})
_FORBIDDEN_BATTERY_KEYS = frozenset({
    "needles", "evidence_quote", "canonical_text", "fact_id",
})


def set_manifest_text(rows: Sequence[tuple[str, str]] | None = None) -> str:
    items = list(rows or A3_PDFS)
    return "".join(sorted(f"{relpath} {digest}\n" for relpath, digest in items))


def set_digest(rows: Sequence[tuple[str, str]] | None = None) -> str:
    return hashlib.sha256(set_manifest_text(rows).encode("utf-8")).hexdigest()


def _title_query(text: str) -> str | None:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if len(stripped) >= TITLE_MIN_CHARS:
            return stripped
    return None


def mint_queries_from_text(
    text: str,
    *,
    paper_sha256: str,
    relpath: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    title = _title_query(text)
    if title is not None:
        rows.append({
            "id": f"od-{paper_sha256[:12]}-{len(rows) + 1:03d}",
            "paper_sha256": paper_sha256,
            "relpath": relpath,
            "kind": "title_query",
            "window_k": None,
            "query": title,
        })
    tokens = research._tokens(text)
    for k in range(WINDOW_COUNT):
        window = tokens[WINDOW_LEN * k: WINDOW_LEN * k + WINDOW_LEN]
        if len(window) < MIN_WINDOW_TOKENS:
            continue
        rows.append({
            "id": f"od-{paper_sha256[:12]}-{len(rows) + 1:03d}",
            "paper_sha256": paper_sha256,
            "relpath": relpath,
            "kind": "body_window",
            "window_k": k,
            "query": " ".join(window),
        })
        if len(rows) >= MAX_QUERIES_PER_PDF:
            break
    return rows[:MAX_QUERIES_PER_PDF]


def _extract(
    pdf: Path,
    extractor: Callable[[Path], Mapping[str, Any]] | None = None,
) -> str:
    reader = extractor or gold_ensemble.channel_b_pdftotext
    try:
        payload = reader(pdf)
    except GoldEnsembleError as error:
        raise TextGoldError(
            "pdftotext_failed",
            f"pdftotext -layout failed for {pdf.name}.",
            relpath=str(pdf),
            cause=error.code,
        ) from error
    return str((payload or {}).get("text") or "")


def build_offdomain_artifact(
    *,
    root: Path | None = None,
    rows: Sequence[tuple[str, str]] | None = None,
    extractor: Callable[[Path], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    table = list(rows or A3_PDFS)
    digest = set_digest(table)
    if digest != SET_DIGEST and table == list(A3_PDFS):
        raise TextGoldError("a3_set_digest", "A-3 PDF set digest is not the freeze.")
    base = Path(root or A3_ROOT)
    if base.resolve() == A3_ROOT.resolve() and (
        table != list(A3_PDFS) or digest != SET_DIGEST
    ):
        raise TextGoldError("a3_set_extra", "A-3 mints the pinned four only.")
    papers: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    for relpath, digest_want in sorted(table, key=lambda item: item[0]):
        pdf = base / relpath
        if not pdf.is_file():
            raise TextGoldError("a3_pdf_missing", "A pinned A-3 PDF is missing.", relpath=relpath)
        got = file_sha256(pdf)
        if got != digest_want:
            raise TextGoldError("a3_pdf_sha_mismatch", "A pinned A-3 PDF sha256 moved.", relpath=relpath)
        text = _extract(pdf, extractor)
        minted = mint_queries_from_text(text, paper_sha256=got, relpath=relpath)
        if not minted:
            raise TextGoldError("a3_no_query", "A pinned A-3 PDF minted zero queries.", relpath=relpath)
        papers.append({
            "relpath": relpath,
            "paper_sha256": got,
            "n_queries": len(minted),
        })
        queries.extend(minted)
    artifact = {
        "schema": SCHEMA,
        "spec_sha256": SPEC_SHA256,
        "set_digest": digest,
        "root": str(base),
        "extractor": "gold_ensemble.channel_b_pdftotext",
        "n_pdfs": len(papers),
        "n_queries": len(queries),
        "pdfs": papers,
        "queries": queries,
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN_BATTERY_KEYS)):
        raise TextGoldError("gold_quoted", "A-3 must not carry needles, quotes, or STRAP fact_ids.")
    ids = [str(row.get("id") or "") for row in queries]
    if len(ids) != len(set(ids)):
        raise TextGoldError("od_id_collision", "Off-domain ids must be unique.")
    if any(not item.startswith("od-") for item in ids):
        raise TextGoldError("od_id_prefix", "Off-domain ids must use the od- prefix.")
    return artifact


def emit_a3_product(
    *,
    root: Path | None = None,
    dest_dir: Path | None = None,
    rows: Sequence[tuple[str, str]] | None = None,
    extractor: Callable[[Path], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    dest = out_dir / OFFDOMAIN_NAME
    protected = {
        text_chunk_metrics.GOLD_UNSEALED_PATH.resolve(),
        text_chunk_metrics.GOLD_V2_PATH.resolve(),
        text_chunk_metrics.CURVES_V3_PATH.resolve(),
        dense_d2.CURVES_V4_PATH.resolve(),
        dense_d3.CURVES_D3_PATH.resolve(),
        dense_d3.CURVES_D3_FINDING_PATH.resolve(),
        engine_e2e.CENSUS_PATH.resolve(),
        engine_e2e.STORE_PATH.resolve(),
        engine_e2e.MANIFEST_PATH.resolve(),
        dense_d2.INDEX_GZIP_PATH.resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json").resolve(),
    }
    if dest.name in _PROTECTED_NAMES or dest.resolve() in protected:
        raise TextGoldError("protected_persist", "A-3 must not overwrite a pinned persist name.")
    gold_before = file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH)
    if gold_before != text_chunk_metrics.GOLD_UNSEALED_SHA256:
        raise TextGoldError("gold_moved", "Unsealed gold digest moved; do not emit A-3.")
    if dest.resolve() == OFFDOMAIN_PATH.resolve() and set_digest(rows or A3_PDFS) != SET_DIGEST:
        raise TextGoldError("a3_set_extra", "Production OFFDOMAIN must be the pinned four.")
    artifact = build_offdomain_artifact(root=root, rows=rows, extractor=extractor)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    if file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH) != gold_before:
        raise TextGoldError("gold_mutated", "Unsealed gold moved during A-3 emit.")
    return {
        "offdomain_path": str(dest),
        "set_digest": artifact["set_digest"],
        "n_pdfs": artifact["n_pdfs"],
        "n_queries": artifact["n_queries"],
        "gold_sha256": gold_before,
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    return emit_a3_product()
