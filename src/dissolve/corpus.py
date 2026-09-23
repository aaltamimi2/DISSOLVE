"""Build or grow a literature corpus the way the served DISSOLVE corpus was built.

Recipe: Docling 2.121.0 canonical document -> T5 chunks (whole parser blocks packed up to 1,400
characters, never split) -> BGE-base-en-v1.5 vectors at the pinned revision, one paper per batch.
The literature tools serve a corpus with BM25 and dense z-score fusion, the abstention gate and
the pinned bge-reranker-base pair rerank with reciprocal-rank fusion (the BGE10 configuration).

A release is a directory holding index.json.gz and manifest.json. The package ships the paper's
release (39 papers, 1,841 chunks) in data/corpus. The working release is DISSOLVE_CORPUS_DIR
(default ~/.dissolve/corpus); it starts as a copy of the shipped one, ``add`` grows it, and the
agent's ingest tool runs the same code. ``build`` starts a release from your papers alone.
``verify`` compares a release with a reference chunk by chunk (paper, offsets, text hash), so
anyone holding the same PDFs can confirm they reproduced it.

    python -m dissolve.corpus add PAPER.pdf ...          [--corpus DIR]
    python -m dissolve.corpus build PAPER.pdf ...        [--corpus DIR]
    python -m dissolve.corpus verify REFERENCE_DIR       [--corpus DIR]
    python -m dissolve.corpus prefetch                   (download the pinned models once; ./dissolve runs it)
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import resource
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import research
from .research import _local_acquisition

T5_TARGET = 1_400
BGE_ID = research._BGE_MODEL_ID
BASE_KB = research._PRODUCT_KNOWLEDGEBASE
RELEASE_INDEX = "index.json.gz"
RELEASE_MANIFEST = "manifest.json"
SHIPPED = Path(__file__).resolve().parent / "data" / "corpus"
#: The answer gate: 5th percentile of query_idf_coverage, calibrated 2026-09-09 against the
#: private benchmark. Nobody can recalibrate it without that benchmark, so it ships as is.
ABSTENTION = {"statistic": "query_idf_coverage", "percentile": 5, "floor": 0.3698406656908355}
#: The measured standing of the BGE10 configuration: better than MiniLM at every depth, and
#: still below the pre-registered served floor (196 of 228 against 206 at k=5).
BGE_STATUS = "exploratory-below-floor"
_METADATA_KEYS = ("title", "source", "url", "doi", "year")


def canonical_from_file(path: Path) -> dict[str, Any]:
    """Parse one document with Docling into the canonical document the chunker reads."""
    path = Path(path)
    sha = _sha256(path.read_bytes())
    started = time.perf_counter()
    parsed = research.parse_experiment_document(_local_acquisition(path, f"corpus-{sha[:12]}"), backend="docling")
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
    model: str = BGE_ID,
    metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Index ``papers`` (``(sha256, canonical)`` in order), appended to ``previous`` if given.

    Only chunks new to the index are embedded; existing vectors are carried over unchanged.
    ``metadata`` (by paper sha256) labels documents and their chunks for citation; it never
    changes the text that is ranked or embedded.
    """
    old = previous or {"chunks": [], "documents": [], "dense": None}
    known = {doc["sha256"] for doc in old["documents"]}
    labels = metadata or {}
    new = []
    for sha, canonical in papers:
        if sha in known:
            continue
        for row in chunk_records(canonical, sha):
            row.update({key: value for key, value in (labels.get(sha) or {}).items() if key in _METADATA_KEYS and value is not None})
            new.append(row)
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
    earlier = {doc["sha256"]: doc for doc in old["documents"]}
    documents = []
    for sha in shas:
        document = dict(earlier.get(sha) or {
            "document_id": f"D{sha[:16]}", "sha256": sha, "title": "", "source": "", "parser_backend": "docling",
        })
        if sha not in earlier:
            document.update({key: value for key, value in (labels.get(sha) or {}).items() if key in _METADATA_KEYS and value is not None})
        documents.append(document)
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": knowledgebase,
        "documents": documents,
        "chunks": chunks,
        "dense": research._attach_generated_recipe({
            "model": model,
            "dim": research._compatible_embedding_dim(model),
            "chunk_ids": [row["chunk_id"] for row in chunks],
            "vectors": vectors,
        }),
    }


# --- releases -------------------------------------------------------------------------------


def working_release(corpus: Path | None = None) -> Path:
    """The release ``add`` grows: DISSOLVE_CORPUS_DIR, seeded from the shipped release on first use."""
    directory = Path(corpus) if corpus is not None else research._corpus_dir()
    if not (directory / RELEASE_MANIFEST).is_file():
        directory.mkdir(parents=True, exist_ok=True)
        for name in (RELEASE_INDEX, RELEASE_MANIFEST):
            shutil.copyfile(SHIPPED / name, directory / name)
    return directory


def read_release(directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((Path(directory) / RELEASE_MANIFEST).read_text(encoding="utf-8"))
    return manifest, read_index(Path(directory) / str(manifest.get("index_path") or RELEASE_INDEX))


def add(sources: Sequence[Path], corpus: Path | None = None) -> dict[str, Any]:
    """Grow the working release with ``sources``, one paper per embedding batch; papers already in it are skipped."""
    directory = working_release(corpus)
    _, index = read_release(directory)
    present = {doc["sha256"] for doc in index["documents"]}
    for sha, canonical in _papers(sources, present):
        index = build_index([(sha, canonical)], index["knowledgebase"], previous=index, model=BGE_ID)
    return write_release(directory, index)


def build(sources: Sequence[Path], corpus: Path) -> dict[str, Any]:
    """Write a new release holding only ``sources``, one paper per embedding batch."""
    index: dict[str, Any] | None = None
    for sha, canonical in _papers(sources, set()):
        index = build_index([(sha, canonical)], BASE_KB, previous=index, model=BGE_ID)
    if index is None:
        raise ValueError("no document was indexed")
    return write_release(Path(corpus), index)


def write_release(directory: Path, index: Mapping[str, Any]) -> dict[str, Any]:
    """Write ``index`` and the manifest that binds it; the manifest lands last, so a torn write fails closed."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    index = dict(index, abstention={"floor": ABSTENTION["floor"]})
    dense = index["dense"]
    manifest = {
        "knowledgebase": index["knowledgebase"],
        "index_path": RELEASE_INDEX,
        "gzip_sha256": _write_gzip(directory / RELEASE_INDEX, index),
        "dense": {key: dense[key] for key in ("model", "dim", "query_instruction", "passage_instruction", "encoder_revision", "chunk_ids")},
        "abstention": {"floor": ABSTENTION["floor"]},
        "n_vectors": len(dense["vectors"]),
        "n_chunks": len(index["chunks"]),
        "n_documents": len(index["documents"]),
        "status": BGE_STATUS,
    }
    temporary = directory / (RELEASE_MANIFEST + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(directory / RELEASE_MANIFEST)
    return manifest


def verify(corpus: Path, reference: Path = SHIPPED) -> dict[str, Any]:
    """Compare a release's chunks with a reference release, chunk by chunk (paper, offsets, text hash)."""
    ours = {row["chunk_id"]: _public(row) for row in read_release(Path(corpus))[1]["chunks"]}
    theirs = {row["chunk_id"]: _public(row) for row in read_release(Path(reference))[1]["chunks"]}
    differing = sorted(cid for cid in ours.keys() & theirs.keys() if ours[cid] != theirs[cid])
    return {
        "matching": len(ours.keys() & theirs.keys()) - len(differing),
        "differing": differing,
        "only_here": sorted(ours.keys() - theirs.keys()),
        "only_reference": sorted(theirs.keys() - ours.keys()),
    }


def prefetch() -> dict[str, Any]:
    """Download the pinned models once, so a first search or ingest does not stall on them: the BGE encoder,
    the pair reranker (hash-checked) and Docling's PDF layout, table and OCR models."""
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter

    research._dense_vectors(["prefetch"])
    reranker = research._verify_pair_artifacts(research._pair_dir())
    DocumentConverter().initialize_pipeline(InputFormat.PDF)
    return {"encoder": BGE_ID, "reranker": reranker, "docling": "PDF pipeline ready"}


def read_index(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _papers(sources: Iterable[Path], present: set[str]) -> Iterable[tuple[str, dict[str, Any]]]:
    """(sha256, canonical) for each source not already ``present``; a canonical-document JSON is used as is."""
    for source in sources:
        source = Path(source)
        if source.suffix.casefold() == ".json":
            canonical = json.loads(source.read_text(encoding="utf-8"))
            sha = str(canonical.get("source_pdf_sha256") or "")
            if canonical.get("schema") != research._CANONICAL_DOCUMENT_SCHEMA or not sha:
                raise ValueError(f"{source.name}: a JSON source must be a canonical document")
        else:
            sha = _sha256(source.read_bytes())
            if sha in present:
                continue
            canonical = canonical_from_file(source)
            sha = str(canonical["source_pdf_sha256"])
        if sha not in present:
            present.add(sha)
            yield sha, canonical


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("chunk_id", "paper_sha256", "char_start", "char_end", "sha256")
    return {key: row[key] for key in keys}


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
    parser.add_argument("command", choices=("add", "build", "verify", "prefetch"))
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--corpus", type=Path, default=None, help="release directory (default: DISSOLVE_CORPUS_DIR)")
    args = parser.parse_args(argv)
    if (args.command == "prefetch") != (not args.paths):
        parser.error("prefetch takes no paths; add, build and verify need at least one")
    corpus = args.corpus or research._corpus_dir()
    if args.command == "prefetch":
        result = prefetch()
    elif args.command == "add":
        result = add(args.paths, corpus)
    elif args.command == "build":
        result = build(args.paths, corpus)
    else:
        result = verify(corpus, args.paths[0])
    summary = {key: value for key, value in result.items() if key not in ("chunks", "dense")}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 1 if args.command == "verify" and (result["differing"] or result["only_reference"]) else 0


if __name__ == "__main__":
    sys.exit(main())
