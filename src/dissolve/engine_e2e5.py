"""E2E-5 incremental ingest. Owner dispatch 2026-08-24.

Names the held E2E ADMIT sentence it moves: ENGINE_E2E_SPEC.v1 ``20957c2f…`` §6
"E2E-5 incremental ingest (owner adds papers; not this ADMIT)". That ADMIT is
not retracted for E2E-0..4. This module is the one-PDF growth path.

Does not retune 0.55/0.40. Does not overwrite pinned persist. Does not seal
gold. Does not reclassify held_out. A new SHA is census ``ingested``, not
``indexed``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import dense_d2, dense_d3, engine_e2e, research, text_chunk_metrics
from .gold_ensemble import CONTAMINANT_SHA256, file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError, load_canonical

INGESTED_STATUS = "ingested"
INCREMENTAL_STORE_PATH = DEFAULT_OUT_DIR / "CHUNKS.t5.incremental.unsealed.v1.json"
INCREMENTAL_CENSUS_PATH = DEFAULT_OUT_DIR / "CENSUS.incremental.v1.json"
_FORBIDDEN_PAYLOAD_KEYS = frozenset({"fact_id", "needles", "query", "evidence_quote"})


def _refuse_e2e5_dest(dest: Path) -> None:
    engine_e2e._refuse_protected_dest(dest)
    banned = {
        engine_e2e.CENSUS_PATH.resolve(),
        engine_e2e.STORE_PATH.resolve(),
        engine_e2e.MANIFEST_PATH.resolve(),
        text_chunk_metrics.GOLD_UNSEALED_PATH.resolve(),
        text_chunk_metrics.GOLD_V2_PATH.resolve(),
        text_chunk_metrics.CURVES_V3_PATH.resolve(),
        dense_d2.CURVES_V4_PATH.resolve(),
        dense_d3.CURVES_D3_PATH.resolve(),
        dense_d3.CURVES_D3_PNG_PATH.resolve(),
        dense_d2.INDEX_GZIP_PATH.resolve(),
    }
    if dest.resolve() in banned:
        raise TextGoldError("protected_persist", "E2E-5 must not overwrite published persist.")
    if dest.name in {
        "CENSUS.v3.json",
        "CHUNKS.t5.indexed.unsealed.v1.json",
        "CURVES.retrieval.d3.json",
        "CURVES.retrieval.d3.png",
        "t5-indexed-unsealed.json.gz",
    }:
        raise TextGoldError("protected_persist", "E2E-5 must not overwrite published persist.")


def paper_bytes_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def forbidden_shas(gold: Mapping[str, Any], census: Mapping[str, Any]) -> set[str]:
    gold_sets = engine_e2e.gold_status_sets(gold)
    census_sets = engine_e2e.census_status_sets(census)
    return (
        gold_sets["held_out"]
        | census_sets["held_out"]
        | census_sets["excluded"]
        | {CONTAMINANT_SHA256}
    )


def store_paper_shas(store: Mapping[str, Any]) -> set[str]:
    shas = {str(row.get("paper_sha256") or "") for row in (store.get("chunks") or [])}
    shas.update(str(sha) for sha in (store.get("indexed_paper_sha256") or []) if sha)
    shas.update(str(sha) for sha in (store.get("ingested_paper_sha256") or []) if sha)
    shas.discard("")
    return shas


def census_status_for(census: Mapping[str, Any], paper_sha256: str) -> str | None:
    for row in census.get("papers") or []:
        if str(row.get("sha256") or "") == paper_sha256:
            status = str(row.get("status") or "")
            return status or None
    return None


def chunk_one_paper(
    canonical: Mapping[str, Any],
    *,
    paper_sha256: str,
    forbidden: set[str],
) -> list[dict[str, Any]]:
    if paper_sha256 in forbidden:
        raise TextGoldError("held_out_in_store", "Refusing to chunk a non-indexed paper.")
    source = text_chunk_metrics._paper_sha(canonical)
    if source and source != paper_sha256:
        raise TextGoldError("paper_sha_mismatch", "Canonical source SHA is not the PDF bytes SHA.")
    if str(canonical.get("parser_backend") or "") != "docling":
        raise TextGoldError("parser_not_docling", "Ingest canonical is not docling.")
    packed = text_chunking_chunks(canonical)
    rows = []
    for ordinal, raw in enumerate(packed, start=1):
        row = engine_e2e._enrich_t5_chunk(
            raw, canonical=canonical, paper_sha256=paper_sha256, ordinal=ordinal,
        )
        if row["paper_sha256"] in forbidden:
            raise TextGoldError("held_out_in_store", "Store chunk carried a forbidden SHA.")
        rows.append(row)
    return rows


def text_chunking_chunks(canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
    from . import text_chunking
    return text_chunking.chunk_t5(canonical, target=text_chunking.T5_TARGET)


def dump_store(payload: Mapping[str, Any]) -> str:
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_PAYLOAD_KEYS)):
        raise TextGoldError("gold_quoted", "Ingest store must not carry gold identifiers.")
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_json(dest: Path, payload: Mapping[str, Any]) -> str:
    dest = Path(dest)
    _refuse_e2e5_dest(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = dump_store(payload) if dest.suffix == ".json" else json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    dest.write_text(text)
    return file_sha256(dest)


def searchable_index(
    store: Mapping[str, Any],
    census: Mapping[str, Any],
) -> dict[str, Any]:
    indexed = engine_e2e.census_status_sets(census)["indexed"]
    chunks = [
        dict(row) for row in (store.get("chunks") or [])
        if str(row.get("paper_sha256") or "") in indexed
    ]
    view = {
        "schema": store.get("schema") or engine_e2e.STORE_SCHEMA,
        "n_chunks": len(chunks),
        "indexed_paper_sha256": sorted(indexed),
        "chunks": chunks,
    }
    if set(view["indexed_paper_sha256"]) != {str(row.get("paper_sha256") or "") for row in chunks}:
        raise TextGoldError("index_sha_mismatch", "Searchable I is not the census indexed SHA set.")
    return engine_e2e.store_to_literature_index(view, knowledgebase=engine_e2e.KNOWLEDGEBASE_ID)


def holdout_n_leaks(
    index: Mapping[str, Any],
    gold: Mapping[str, Any],
    *,
    search=research._search_index,
) -> dict[str, Any]:
    """Must-refuse side after ingest. Counts only. Does not quote fact identifiers."""
    split = text_chunk_metrics.split_gold(gold)
    held = list(split["must_refuse"])
    chunks = list(index.get("chunks") or [])
    by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
    top = max(text_chunk_metrics.RETRIEVAL_KS)

    def ranker(idx: Mapping[str, Any], query: str):
        rows = search(idx, query, top, "sparse")
        return [
            by_id[str(row["chunk_id"])]
            for row in rows
            if str(row.get("chunk_id")) in by_id
        ]

    hits, _unused_ids = dense_d2._score_side(index, held, ranker)
    del _unused_ids
    n_leaks = {str(k): int(hits[str(k)]) for k in text_chunk_metrics.RETRIEVAL_KS}
    return {
        "n_held_out_facts": len(held),
        "n_leaks_at_k": n_leaks,
        "no_leaks_at_every_k": all(n_leaks[str(k)] == 0 for k in text_chunk_metrics.RETRIEVAL_KS),
    }


def _existing_vector_ids(store: Mapping[str, Any]) -> set[str]:
    pending = store.get("pending_dense") or {}
    return {str(item) for item in (pending.get("chunk_ids") or [])}


def _embed_new_chunks(
    chunks: Sequence[Mapping[str, Any]],
    *,
    existing_ids: set[str],
    embedder,
) -> tuple[list[str], list[list[float]], int]:
    fresh = [chunk for chunk in chunks if str(chunk["chunk_id"]) not in existing_ids]
    if not fresh:
        return [], [], 0
    texts = [research.chunk_sparse_corpus(chunk) for chunk in fresh]
    if embedder is None:
        model_id, vectors = research._dense_vectors(texts, engine_e2e.MINILM_ID)
    else:
        model_id, vectors = embedder(texts, engine_e2e.MINILM_ID)
    del model_id
    if len(vectors) != len(fresh):
        raise TextGoldError("embed_align", "Pending embed count is not the new chunk count.")
    return [str(chunk["chunk_id"]) for chunk in fresh], vectors, len(fresh)


def _merge_pending_dense(
    store: dict[str, Any],
    *,
    new_ids: Sequence[str],
    new_vectors: Sequence[Sequence[float]],
) -> None:
    pending = dict(store.get("pending_dense") or {
        "model": engine_e2e.MINILM_ID,
        "dim": engine_e2e.EXPECTED_DIM,
        "chunk_ids": [],
        "vectors": [],
    })
    ids = [str(item) for item in (pending.get("chunk_ids") or [])]
    vectors = [list(row) for row in (pending.get("vectors") or [])]
    known = set(ids)
    for chunk_id, vector in zip(new_ids, new_vectors):
        if chunk_id in known:
            continue
        ids.append(chunk_id)
        vectors.append(list(vector))
        known.add(chunk_id)
    pending["chunk_ids"] = ids
    pending["vectors"] = vectors
    pending["model"] = engine_e2e.MINILM_ID
    pending["dim"] = engine_e2e.EXPECTED_DIM
    store["pending_dense"] = pending


def _append_census_ingested(
    census: Mapping[str, Any],
    *,
    paper_sha256: str,
    filename: str,
    n_bytes: int,
) -> dict[str, Any]:
    working = json.loads(json.dumps(census))
    papers = list(working.get("papers") or [])
    for row in papers:
        if str(row.get("sha256") or "") == paper_sha256:
            return working
    papers.append({
        "filename": filename,
        "sha256": paper_sha256,
        "bytes": n_bytes,
        "status": INGESTED_STATUS,
    })
    working["papers"] = papers
    counts = dict(working.get("counts_by_status") or {})
    counts[INGESTED_STATUS] = int(counts.get(INGESTED_STATUS) or 0) + 1
    working["counts_by_status"] = counts
    return working


def _resolve_canonical(
    *,
    paper_sha256: str,
    canonical: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if canonical is not None:
        return canonical
    try:
        return load_canonical(paper_sha256)
    except (OSError, TextGoldError, FileNotFoundError) as error:
        raise TextGoldError(
            "canonical_missing",
            "E2E-5 needs a canonical for this SHA; it does not hunt PDFs.",
        ) from error


def ingest_one_paper(
    pdf_path: Path,
    *,
    gold: Mapping[str, Any],
    census: Mapping[str, Any],
    store: Mapping[str, Any],
    dest_store: Path,
    dest_census: Path,
    canonical: Mapping[str, Any] | None = None,
    embedder=None,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    source = Path(pdf_path)
    if not source.is_file():
        raise TextGoldError("missing_pdf", "E2E-5 ingest takes one existing file.")
    dest_store = Path(dest_store)
    dest_census = Path(dest_census)
    _refuse_e2e5_dest(dest_store)
    _refuse_e2e5_dest(dest_census)
    paper_sha = paper_bytes_sha256(source)
    blocked = forbidden_shas(gold, census)
    if paper_sha in blocked:
        raise TextGoldError("held_out_in_store", "Refusing to chunk a non-indexed paper.")
    if census_status_for(census, paper_sha) == "indexed" and paper_sha not in engine_e2e.gold_status_sets(gold)["indexed"]:
        raise TextGoldError("silent_index_refused", "Census indexed is an owner call, not ingest.")
    working_store = json.loads(json.dumps(store))
    working_census = json.loads(json.dumps(census))
    before = file_sha256(dest_store) if dest_store.is_file() else hashlib.sha256(dump_store(working_store).encode()).hexdigest()
    already = paper_sha in store_paper_shas(working_store)
    if already:
        if not dest_store.is_file():
            write_json(dest_store, working_store)
        if not dest_census.is_file():
            write_json(dest_census, working_census)
        after = file_sha256(dest_store)
        search = searchable_index(json.loads(dest_store.read_text()), json.loads(dest_census.read_text()) if dest_census.is_file() else working_census)
        return {
            "paper_sha256": paper_sha,
            "noop": True,
            "chunks_added": 0,
            "vectors_embedded": 0,
            "census_status": census_status_for(json.loads(dest_census.read_text()), paper_sha)
            or census_status_for(working_census, paper_sha),
            "store_sha256_before": before,
            "store_sha256_after": after,
            "search_n_chunks": len(search["chunks"]),
            "filename": source.name,
        }
    bound = _resolve_canonical(paper_sha256=paper_sha, canonical=canonical)
    new_chunks = chunk_one_paper(bound, paper_sha256=paper_sha, forbidden=blocked)
    existing_ids = _existing_vector_ids(working_store)
    existing_ids.update(str(row["chunk_id"]) for row in (working_store.get("chunks") or []) if str(row.get("paper_sha256") or "") in engine_e2e.census_status_sets(working_census)["indexed"])
    new_ids, new_vectors, n_embedded = _embed_new_chunks(
        new_chunks, existing_ids=existing_ids, embedder=embedder,
    )
    working_store.setdefault("chunks", [])
    working_store["chunks"].extend(new_chunks)
    ingested = {str(sha) for sha in (working_store.get("ingested_paper_sha256") or [])}
    ingested.add(paper_sha)
    working_store["ingested_paper_sha256"] = sorted(ingested)
    working_store["n_chunks"] = len(working_store["chunks"])
    _merge_pending_dense(working_store, new_ids=new_ids, new_vectors=new_vectors)
    working_census = _append_census_ingested(
        working_census,
        paper_sha256=paper_sha,
        filename=source.name,
        n_bytes=source.stat().st_size,
    )
    if census_status_for(working_census, paper_sha) == "indexed":
        raise TextGoldError("silent_index_refused", "Ingest must not mark a new SHA indexed.")
    write_json(dest_store, working_store)
    write_json(dest_census, working_census)
    after = file_sha256(dest_store)
    search = searchable_index(working_store, working_census)
    return {
        "paper_sha256": paper_sha,
        "noop": False,
        "chunks_added": len(new_chunks),
        "vectors_embedded": n_embedded,
        "census_status": INGESTED_STATUS,
        "store_sha256_before": before,
        "store_sha256_after": after,
        "search_n_chunks": len(search["chunks"]),
        "filename": source.name,
    }


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise TextGoldError("one_pdf", "E2E-5 takes exactly one PDF path.")
    gold = json.loads(text_chunk_metrics.GOLD_UNSEALED_PATH.read_text())
    census = json.loads(engine_e2e.CENSUS_PATH.read_text())
    store = json.loads(engine_e2e.STORE_PATH.read_text())
    dest_store = INCREMENTAL_STORE_PATH
    dest_census = INCREMENTAL_CENSUS_PATH
    if dest_store.is_file():
        store = json.loads(dest_store.read_text())
    if dest_census.is_file():
        census = json.loads(dest_census.read_text())
    result = ingest_one_paper(
        Path(args[0]),
        gold=gold,
        census=census,
        store=store,
        dest_store=dest_store,
        dest_census=dest_census,
    )
    print(json.dumps({
        "paper_sha256": result["paper_sha256"],
        "noop": result["noop"],
        "chunks_added": result["chunks_added"],
        "vectors_embedded": result["vectors_embedded"],
        "census_status": result["census_status"],
        "store_sha256_before": result["store_sha256_before"],
        "store_sha256_after": result["store_sha256_after"],
        "search_n_chunks": result["search_n_chunks"],
    }, indent=2))
    return result


if __name__ == "__main__":
    main()
