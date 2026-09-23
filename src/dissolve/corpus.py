"""Build or grow a literature corpus exactly the way the served DISSOLVE corpus was built.

PDF -> Docling 2.121.0 canonical document -> T5 chunks (whole parser blocks packed up to
1,400 characters, never split) -> index records -> MiniLM vectors, written in the layout the
literature tools serve from ``DISSOLVE_CORPUS_DIR``. ``build`` writes the base index and
``add`` grows the promoted sidecar, which is how the served corpus went from 19 to 39
papers. ``bge`` writes the BGE10 artifact (one index over base and sidecar, BGE vectors).

The manifest lists every chunk's paper, offsets and sha256 but no text, so anyone holding
the same PDFs can check they reproduced these chunks exactly without the papers ever being
published: ``verify`` compares a corpus against such a manifest.

    python -m dissolve.corpus build PAPER.pdf ... [--corpus DIR]
    python -m dissolve.corpus add PAPER.pdf ...   [--corpus DIR]
    python -m dissolve.corpus bge OUT_DIR         [--corpus DIR] [--reuse INDEX.json.gz]
    python -m dissolve.corpus verify MANIFEST.json [--corpus DIR]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import resource
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import research
from .research import _local_acquisition

T5_TARGET = 1_400
MINILM_ID = research._MINILM_MODEL_ID
BASE_KB = research._PRODUCT_KNOWLEDGEBASE
SIDECAR_KB = research._SIDECAR_KNOWLEDGEBASE
MANIFEST_NAME = research._PRODUCT_MANIFEST_NAME
#: The answer gate: 5th percentile of query_idf_coverage, calibrated 2026-09-09 against the
#: private benchmark. Nobody can recalibrate it without that benchmark, so it ships as is.
ABSTENTION = {"statistic": "query_idf_coverage", "percentile": 5, "floor": 0.3698406656908355}
BGE_STATUS = "exploratory-below-floor"


def canonical_from_pdf(pdf: Path) -> dict[str, Any]:
    """Parse one PDF with Docling into the canonical document the chunker reads."""
    pdf = Path(pdf)
    sha = _sha256(pdf.read_bytes())
    started = time.perf_counter()
    parsed = research.parse_experiment_document(_local_acquisition(pdf, f"corpus-{sha[:12]}"), backend="docling")
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    return research.build_canonical_document(
        parsed, parse_metrics={"wall_s": time.perf_counter() - started, "peak_rss_bytes": peak},
    )


def t5_spans(canonical: Mapping[str, Any], target: int = T5_TARGET) -> list[tuple[int, int]]:
    """Pack whole blocks, in text order, into spans that stop before exceeding ``target``.

    A block is never split: a block longer than ``target`` becomes a span of its own.
    """
    spans: list[tuple[int, int]] = []
    start = end = None
    for block_start, block_end in _block_spans(canonical, nonempty=True):
        if start is not None and block_end - start > target:
            spans.append((start, end))
            start = None
        if start is None:
            start = block_start
        end = block_end
    if start is not None:
        spans.append((start, end))
    return spans


def chunk_records(canonical: Mapping[str, Any], paper_sha256: str) -> list[dict[str, Any]]:
    """Index records for one paper: T5 spans stamped with page, section, kind and table rebound."""
    text = str(canonical.get("canonical_text") or "")
    blocks = [block for block in canonical.get("blocks") or [] if _span(block)]
    rows = []
    for ordinal, (start, end) in enumerate(t5_spans(canonical), start=1):
        body = text[start:end]
        overlapping = sorted(
            (block for block in blocks if _span(block)[0] < end and start < _span(block)[1]),
            key=_span,
        )
        first = overlapping[0] if overlapping else {}
        origin = first.get("nearest_preceding_heading_origin") if first else None
        heading = list(first.get("nearest_preceding_heading") or [])
        pages = sorted(int(block["page"]) for block in overlapping if _is_int(block.get("page")))
        kinds = {str(block.get("kind")) for block in overlapping if block.get("kind")}
        row = {
            "chunk_id": f"T5-{paper_sha256[:12]}-{ordinal:04d}",
            "sha256": _sha256(body.encode("utf-8")),
            "document_id": f"D{paper_sha256[:16]}",
            "paper_sha256": paper_sha256,
            "title": "",
            "source": "",
            "page": None if not pages else pages[0] if pages[0] == pages[-1] else f"{pages[0]}-{pages[-1]}",
            "section": research._chunk_header(heading) if origin == "parser_supplied" else "",
            "section_origin": origin,
            "kind": None if not kinds else kinds.pop() if len(kinds) == 1 else "pack",
            "char_start": start,
            "char_end": end,
            "text": body,
            "body": body,
            "token_estimate": max(1, math.ceil(len(body) / 4)),
        }
        rebound = research.apply_table_rebound([{"body": body, "char_start": start, "char_end": end}], canonical)
        extra = str(rebound[0].get("body_plus_rebound") or "")
        if extra and extra != body:
            row["body_plus_rebound"] = extra
        rows.append(row)
    return rows


def build_index(
    papers: Sequence[tuple[str, Mapping[str, Any]]],
    knowledgebase: str,
    *,
    previous: Mapping[str, Any] | None = None,
    model: str = MINILM_ID,
) -> dict[str, Any]:
    """Index ``papers`` (``(pdf_sha256, canonical)`` in order), appended to ``previous`` if given.

    Only chunks new to the index are embedded; existing vectors are carried over unchanged.
    """
    old = previous or {"chunks": [], "documents": [], "dense": None}
    known = {doc["sha256"] for doc in old["documents"]}
    new = [row for sha, canonical in papers if sha not in known for row in chunk_records(canonical, sha)]
    chunks = list(old["chunks"]) + new
    shas = sorted(known | {sha for sha, _ in papers})
    vectors = list((old.get("dense") or {}).get("vectors") or [])
    if new:
        returned, fresh = research._dense_vectors([research.chunk_sparse_corpus(row) for row in new], model)
        if returned != model:
            raise research.LiteratureContractError("dense_model", "The embedder returned a different model.")
        research._assert_generated_dense_vectors(
            fresh, expected_count=len(new), model_name=model, expected_dim=research._compatible_embedding_dim(model),
        )
        vectors += fresh
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": knowledgebase,
        "documents": [
            {"document_id": f"D{sha[:16]}", "sha256": sha, "title": "", "source": "", "parser_backend": "docling"}
            for sha in shas
        ],
        "chunks": chunks,
        "dense": research._attach_generated_recipe({
            "model": model,
            "dim": research._compatible_embedding_dim(model),
            "chunk_ids": [row["chunk_id"] for row in chunks],
            "vectors": vectors,
        }),
    }


def build(pdfs: Sequence[Path], corpus: Path) -> dict[str, Any]:
    """Write the base index for ``pdfs`` and a fresh manifest; replaces any sidecar."""
    base = build_index([_paper(pdf) for pdf in pdfs], BASE_KB)
    return _write(corpus, base, None)


def add(pdfs: Sequence[Path], corpus: Path) -> dict[str, Any]:
    """Grow the promoted sidecar with ``pdfs``; the base index is left untouched.

    Papers are added one at a time, each paper's chunks embedded on their own, which is how
    the served sidecar grew (embedding batches pad differently, so the batching matters).
    """
    base = read_index(corpus / "indexes" / f"{BASE_KB}.json.gz")
    sidecar_path = corpus / "indexes" / f"{SIDECAR_KB}.json.gz"
    sidecar = read_index(sidecar_path) if sidecar_path.is_file() else None
    in_base = {doc["sha256"] for doc in base["documents"]}
    for paper in map(_paper, pdfs):
        if paper[0] not in in_base:
            sidecar = build_index([paper], SIDECAR_KB, previous=sidecar)
    return _write(corpus, base, sidecar)


def bge_artifact(corpus: Path, out: Path, *, reuse: Path | None = None) -> dict[str, Any]:
    """Write the BGE10 artifact: one index over base and sidecar with BGE vectors.

    Vectors for chunk ids present in ``reuse`` (an earlier BGE index) are carried over; only
    the rest are encoded.
    """
    manifest = json.loads((corpus / MANIFEST_NAME).read_text(encoding="utf-8"))
    base = read_index(corpus / "indexes" / f"{BASE_KB}.json.gz")
    sidecar_path = corpus / "indexes" / f"{SIDECAR_KB}.json.gz"
    sidecar = read_index(sidecar_path) if sidecar_path.is_file() else {"chunks": [], "documents": []}
    chunks = list(base["chunks"]) + list(sidecar["chunks"])
    old: dict[str, list[float]] = {}
    if reuse is not None:
        dense = read_index(reuse)["dense"]
        old = dict(zip(dense["chunk_ids"], dense["vectors"]))
    missing = [row for row in chunks if row["chunk_id"] not in old]
    if missing:
        _, fresh = research._dense_vectors([research.chunk_sparse_corpus(row) for row in missing], research._BGE_MODEL_ID)
        old.update(zip((row["chunk_id"] for row in missing), fresh))
    ids = [row["chunk_id"] for row in chunks]
    index = {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": BASE_KB,
        "abstention": {"floor": manifest.get("abstention", ABSTENTION)["floor"]},
        "documents": list(base["documents"]) + list(sidecar["documents"]),
        "chunks": chunks,
        "dense": research._attach_generated_recipe({
            "model": research._BGE_MODEL_ID, "dim": research._BGE_DIM, "chunk_ids": ids, "vectors": [old[i] for i in ids],
        }),
    }
    out.mkdir(parents=True, exist_ok=True)
    digest = _write_gzip(out / "index.json.gz", index)
    bge_manifest = {
        "knowledgebase": BASE_KB,
        "index_path": "index.json.gz",
        "gzip_sha256": digest,
        "dense": {
            key: index["dense"][key]
            for key in ("model", "dim", "query_instruction", "passage_instruction", "encoder_revision", "chunk_ids")
        },
        "abstention": dict(index["abstention"]),
        "n_vectors": len(ids),
        "n_chunks": len(chunks),
        "n_documents": len(index["documents"]),
        "status": BGE_STATUS,
    }
    (out / "manifest.json").write_text(json.dumps(bge_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return bge_manifest


def verify(corpus: Path, published: Mapping[str, Any]) -> dict[str, Any]:
    """Compare this corpus's chunks with a published manifest, chunk by chunk (no text needed)."""
    ours = {row["chunk_id"]: row for row in _chunk_list(corpus)}
    theirs = {row["chunk_id"]: row for row in published.get("chunks") or []}
    differing = sorted(cid for cid in ours.keys() & theirs.keys() if ours[cid] != theirs[cid])
    return {
        "matching": len(ours.keys() & theirs.keys()) - len(differing),
        "differing": differing,
        "only_here": sorted(ours.keys() - theirs.keys()),
        "only_published": sorted(theirs.keys() - ours.keys()),
    }


def read_index(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _write(corpus: Path, base: Mapping[str, Any], sidecar: Mapping[str, Any] | None) -> dict[str, Any]:
    indexes = corpus / "indexes"
    indexes.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "dissolve.corpus-manifest.v1",
        "recipe": {"parser": "docling 2.121.0", "chunker": f"T5 block pack, target {T5_TARGET}", "embedder": MINILM_ID},
        "knowledgebase": BASE_KB,
        "index_path": f"indexes/{BASE_KB}.json.gz",
        "gzip_sha256": _write_gzip(indexes / f"{BASE_KB}.json.gz", base),
        "n_papers": len(base["documents"]),
        "n_chunks": len(base["chunks"]),
        "abstention": dict(ABSTENTION),
    }
    stale = indexes / f"{SIDECAR_KB}.json.gz"
    if sidecar is not None:
        manifest["promoted"] = {
            "knowledgebase": SIDECAR_KB,
            "index_path": f"indexes/{SIDECAR_KB}.json.gz",
            "gzip_sha256": _write_gzip(stale, sidecar),
            "n_papers": len(sidecar["documents"]),
            "n_chunks": len(sidecar["chunks"]),
        }
    elif stale.exists():
        stale.unlink()
    manifest["chunks"] = [_public(row) for index in (base, sidecar) if index for row in index["chunks"]]
    (corpus / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def _chunk_list(corpus: Path) -> list[dict[str, Any]]:
    rows = list(read_index(corpus / "indexes" / f"{BASE_KB}.json.gz")["chunks"])
    sidecar = corpus / "indexes" / f"{SIDECAR_KB}.json.gz"
    if sidecar.is_file():
        rows += read_index(sidecar)["chunks"]
    return [_public(row) for row in rows]


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("chunk_id", "paper_sha256", "char_start", "char_end", "sha256")
    return {key: row[key] for key in keys}


def _paper(pdf: Path) -> tuple[str, dict[str, Any]]:
    canonical = canonical_from_pdf(Path(pdf))
    return str(canonical["source_pdf_sha256"]), canonical


def _write_gzip(path: Path, payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        compressed.write(body)
    data = buffer.getvalue()
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return _sha256(data)


def _block_spans(canonical: Mapping[str, Any], *, nonempty: bool) -> list[tuple[int, int]]:
    spans = [_span(block) for block in canonical.get("blocks") or []]
    return sorted(span for span in spans if span and (span[1] > span[0] or not nonempty))


def _span(block: Mapping[str, Any]) -> tuple[int, int] | tuple[()]:
    try:
        return int(block["char_start"]), int(block["char_end"])
    except (KeyError, TypeError, ValueError):
        return ()


def _is_int(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dissolve.corpus", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("build", "add", "bge", "verify"))
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--corpus", type=Path, default=None, help="corpus directory (default: DISSOLVE_CORPUS_DIR)")
    parser.add_argument("--reuse", type=Path, default=None, help="bge: earlier BGE index whose vectors to carry over")
    args = parser.parse_args(argv)
    corpus = args.corpus or research._corpus_dir()
    if args.command == "build":
        result = build(args.paths, corpus)
    elif args.command == "add":
        result = add(args.paths, corpus)
    elif args.command == "bge":
        result = bge_artifact(corpus, args.paths[0], reuse=args.reuse)
    else:
        result = verify(corpus, json.loads(args.paths[0].read_text(encoding="utf-8")))
    summary = {key: value for key, value in result.items() if key != "chunks"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if args.command == "verify" and (result["differing"] or result["only_published"]) else 0


if __name__ == "__main__":
    sys.exit(main())
