"""RTI-1 owner promote of one E2E-5 ingested paper into a sidecar searchable I.

ADMIT `4cfa76c7…`. Ingest stays off the agent. No URL fetch. No MiniLM rewrite
of the product 922. Floor is re-derived on the union, not typed by hand.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import abstention_a2a4, abstention_a3, dense_d2, engine_e2e, engine_e2e5, research, t5_corpus_graph, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "4cfa76c769ad3b8fae9919e900c6fa6237714c5f7ab0557cec093b20aa2fd346"
SIDECAR_STORE_NAME = "CHUNKS.t5.promoted.unsealed.v1.json"
SIDECAR_GZIP_NAME = "t5-promoted-unsealed.json.gz"
SIDECAR_INDEX_NAME = "INDEX.t5.promoted.v1.json"
SIDECAR_KNOWLEDGEBASE = "t5-promoted-unsealed"
PROMOTE_CURVES_NAME = "CURVES.retrieval.abstention.promote.v1.json"
PRODUCT_INDEX_NAME = "INDEX.t5.unsealed.v1.json"
INDEXED_STATUS = "indexed"
_FORBIDDEN_EMIT_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_NAMES = frozenset({
    "CENSUS.v3.json",
    "GOLD.text.v1.unsealed.json",
    "GOLD.v2.json",
    "CHUNKS.t5.indexed.unsealed.v1.json",
    "t5-indexed-unsealed.json.gz",
    "CURVES.retrieval.abstention.v1.json",
    "CURVES.retrieval.weights.v1.json",
    "CURVES.retrieval.v3.json",
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.d3.json",
    "OFFDOMAIN.queries.v1.json",
})


def _refuse_gold_v2() -> None:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    extra = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/GOLD.v2.json")
    if extra.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")


def _refuse_rti_dest(dest: Path) -> None:
    dest = Path(dest).expanduser().resolve()
    if dest.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "RTI-1 must not overwrite a pinned persist name.")
    banned = {
        engine_e2e.CENSUS_PATH.resolve(),
        engine_e2e.STORE_PATH.resolve(),
        text_chunk_metrics.GOLD_UNSEALED_PATH.resolve(),
        text_chunk_metrics.GOLD_V2_PATH.resolve(),
        dense_d2.INDEX_GZIP_PATH.resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json").resolve(),
        text_chunk_metrics.CURVES_V3_PATH.resolve(),
        dense_d2.CURVES_V4_PATH.resolve(),
        Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v3.json").resolve(),
    }
    if dest in banned:
        raise TextGoldError("protected_persist", "RTI-1 must not overwrite published persist.")


def _refuse_url(paper_sha256: str) -> None:
    folded = str(paper_sha256 or "").strip().casefold()
    if folded.startswith(("http://", "https://", "ftp://")):
        raise TextGoldError("url_fetch_refused", "Promote does not fetch URLs.")


def _write_json(dest: Path, payload: Mapping[str, Any]) -> str:
    dest = Path(dest)
    _refuse_rti_dest(dest)
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "RTI-1 dest must not carry gold identifiers.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return file_sha256(dest)


def _write_gzip(dest: Path, index: Mapping[str, Any]) -> str:
    dest = Path(dest)
    _refuse_rti_dest(dest)
    if dest.name == "t5-indexed-unsealed.json.gz":
        raise TextGoldError("protected_persist", "RTI-1 must not rewrite the product gzip.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    temporary = dest.with_name(dest.name + ".tmp")
    with temporary.open("wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            compressed.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(dest)
    return file_sha256(dest)


def _empty_sidecar_store() -> dict[str, Any]:
    return {
        "schema": engine_e2e.STORE_SCHEMA,
        "n_chunks": 0,
        "indexed_paper_sha256": [],
        "ingested_paper_sha256": [],
        "chunks": [],
    }


def _pending_vectors(store: Mapping[str, Any]) -> dict[str, list[float]]:
    pending = store.get("pending_dense") or {}
    ids = [str(item) for item in (pending.get("chunk_ids") or [])]
    vectors = [list(row) for row in (pending.get("vectors") or [])]
    return {chunk_id: vector for chunk_id, vector in zip(ids, vectors)}


def _mark_incremental_indexed(census: Mapping[str, Any], paper_sha256: str) -> dict[str, Any]:
    working = json.loads(json.dumps(census))
    found = False
    previous = None
    for row in working.get("papers") or []:
        if str(row.get("sha256") or "") != paper_sha256:
            continue
        previous = str(row.get("status") or "")
        if previous == INDEXED_STATUS:
            return working
        if previous != engine_e2e5.INGESTED_STATUS:
            raise TextGoldError("census_status", "Promote expects an ingested incremental row.")
        row["status"] = INDEXED_STATUS
        found = True
        break
    if not found:
        raise TextGoldError("ingested_missing", "Paper SHA is not in the incremental census.")
    counts = dict(working.get("counts_by_status") or {})
    ingested = max(0, int(counts.get(engine_e2e5.INGESTED_STATUS) or 0) - 1)
    counts[engine_e2e5.INGESTED_STATUS] = ingested
    counts[INDEXED_STATUS] = int(counts.get(INDEXED_STATUS) or 0) + 1
    working["counts_by_status"] = counts
    del previous
    return working


def _ensure_dest_index(dest_dir: Path) -> Path:
    dest_index = dest_dir / PRODUCT_INDEX_NAME
    product = engine_e2e.MANIFEST_PATH.resolve()
    if dest_index.resolve() != product and not dest_index.is_file():
        dest_index.write_bytes(engine_e2e.MANIFEST_PATH.read_bytes())
    return dest_index


def _update_product_index(
    dest_index: Path,
    *,
    promoted: Mapping[str, Any],
    floor: float,
) -> str:
    payload = json.loads(dest_index.read_text(encoding="utf-8"))
    before_keys = set(payload)
    indexed = list(payload.get("indexed_paper_sha256") or [])
    n_chunks = payload.get("n_chunks")
    payload["promoted"] = dict(promoted)
    block = dict(payload.get("abstention") or {})
    block["statistic"] = "query_idf_coverage"
    block["percentile"] = abstention_a2a4.SHIPPED_PERCENTILE
    block["floor"] = float(floor)
    block["calibrated_at"] = datetime.now(timezone.utc).isoformat()
    payload["abstention"] = block
    extra = set(payload) - before_keys
    if extra - {"promoted"}:
        raise TextGoldError("manifest_keys_moved", "INDEX.t5 may gain promoted only.")
    if list(payload.get("indexed_paper_sha256") or []) != indexed:
        raise TextGoldError("index_sha_mismatch", "Product indexed SHA list must not move.")
    if payload.get("n_chunks") != n_chunks:
        raise TextGoldError("n_chunks_mismatch", "Product INDEX n_chunks must not move.")
    return _write_json(dest_index, payload)


def _union_floor(
    *,
    sidecar_index: Mapping[str, Any],
    sidecar_store_sha256: str,
    sidecar_gzip_sha256: str,
    dest_curves: Path,
) -> dict[str, Any]:
    gold = json.loads(text_chunk_metrics.GOLD_UNSEALED_PATH.read_text(encoding="utf-8"))
    split = text_chunk_metrics.split_gold(gold)
    off_payload = json.loads(abstention_a3.OFFDOMAIN_PATH.read_text(encoding="utf-8"))
    fire = [str(fact.get("query") or "") for fact in split["must_fire"]]
    off = [str(row.get("query") or "") for row in (off_payload.get("queries") or [])]
    product = dense_d2.load_gzip_index()
    union = research._union_product_and_sidecar(product, sidecar_index)
    pins = {
        "gold_sha256": file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH),
        "census_sha256": file_sha256(engine_e2e.CENSUS_PATH),
        "store_sha256": file_sha256(engine_e2e.STORE_PATH),
        "gzip_sha256": file_sha256(dense_d2.INDEX_GZIP_PATH),
        "offdomain_sha256": file_sha256(abstention_a3.OFFDOMAIN_PATH),
        "sidecar_store_sha256": sidecar_store_sha256,
        "sidecar_gzip_sha256": sidecar_gzip_sha256,
        "set_digest": str(off_payload.get("set_digest") or abstention_a3.SET_DIGEST),
    }
    artifact = abstention_a2a4.build_abstention_artifact(
        index=union, fire_queries=fire, offdomain_queries=off, pins=pins,
    )
    _write_json(dest_curves, artifact)
    return artifact


def _header_from_dests(
    *,
    status: str,
    chunks_added: int,
    vectors_embedded: int,
    dest_dir: Path,
    floor: float | None,
    census_status: str | None,
) -> dict[str, Any]:
    sidecar_store = dest_dir / SIDECAR_STORE_NAME
    sidecar_gzip = dest_dir / "indexes" / SIDECAR_GZIP_NAME
    sidecar_index = dest_dir / SIDECAR_INDEX_NAME
    product_index = dest_dir / PRODUCT_INDEX_NAME
    graph = dest_dir / t5_corpus_graph.GRAPH_NAME
    curves = dest_dir / PROMOTE_CURVES_NAME
    n_papers = 0
    n_chunks = 0
    if sidecar_store.is_file():
        store = json.loads(sidecar_store.read_text(encoding="utf-8"))
        n_chunks = len(store.get("chunks") or [])
        n_papers = len({str(row.get("paper_sha256") or "") for row in (store.get("chunks") or []) if row.get("paper_sha256")})
    payload = {
        "status": status,
        "chunks_added": int(chunks_added),
        "vectors_embedded": int(vectors_embedded),
        "n_sidecar_papers": n_papers,
        "n_sidecar_chunks": n_chunks,
        "statistic": "query_idf_coverage",
        "percentile": abstention_a2a4.SHIPPED_PERCENTILE,
        "floor": floor,
        "census_status": census_status,
        "sidecar_store_sha256": file_sha256(sidecar_store) if sidecar_store.is_file() else None,
        "sidecar_gzip_sha256": file_sha256(sidecar_gzip) if sidecar_gzip.is_file() else None,
        "sidecar_index_sha256": file_sha256(sidecar_index) if sidecar_index.is_file() else None,
        "index_sha256": file_sha256(product_index) if product_index.is_file() else None,
        "graph_sha256": file_sha256(graph) if graph.is_file() else None,
        "promote_curves_sha256": file_sha256(curves) if curves.is_file() else None,
    }
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "RTI-1 return must not carry gold identifiers.")
    return payload


def promote_ingested_paper(
    *,
    paper_sha256: str,
    dest_dir: str | Path | None = None,
    embedder=None,
) -> dict[str, Any]:
    _refuse_gold_v2()
    _refuse_url(paper_sha256)
    dest = Path(dest_dir).expanduser().resolve() if dest_dir is not None else DEFAULT_OUT_DIR.resolve()
    incremental_census_path = dest / engine_e2e5.INCREMENTAL_CENSUS_PATH.name
    incremental_store_path = dest / engine_e2e5.INCREMENTAL_STORE_PATH.name
    sidecar_store_path = dest / SIDECAR_STORE_NAME
    sidecar_gzip_path = dest / "indexes" / SIDECAR_GZIP_NAME
    sidecar_index_path = dest / SIDECAR_INDEX_NAME
    dest_curves = dest / PROMOTE_CURVES_NAME
    graph_path = dest / t5_corpus_graph.GRAPH_NAME
    if not incremental_census_path.is_file():
        return _header_from_dests(
            status="ingested_missing",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=None,
            census_status=None,
        )
    census = json.loads(incremental_census_path.read_text(encoding="utf-8"))
    status = engine_e2e5.census_status_for(census, paper_sha256)
    if status is None:
        return _header_from_dests(
            status="ingested_missing",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=None,
            census_status=None,
        )
    gold = json.loads(text_chunk_metrics.GOLD_UNSEALED_PATH.read_text(encoding="utf-8"))
    product_census = json.loads(engine_e2e.CENSUS_PATH.read_text(encoding="utf-8"))
    blocked = engine_e2e5.forbidden_shas(gold, product_census) | engine_e2e5.forbidden_shas(gold, census)
    if paper_sha256 in blocked:
        raise TextGoldError("held_out_in_store", "Refusing to promote a non-indexed paper.")
    sidecar = json.loads(sidecar_store_path.read_text(encoding="utf-8")) if sidecar_store_path.is_file() else _empty_sidecar_store()
    if paper_sha256 in engine_e2e5.store_paper_shas(sidecar):
        floor = None
        dest_index = dest / PRODUCT_INDEX_NAME
        if dest_index.is_file():
            block = json.loads(dest_index.read_text(encoding="utf-8")).get("abstention") or {}
            if isinstance(block, Mapping) and block.get("floor") is not None:
                floor = float(block["floor"])
        return _header_from_dests(
            status="noop",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=floor,
            census_status=engine_e2e5.census_status_for(census, paper_sha256),
        )
    if status != engine_e2e5.INGESTED_STATUS:
        return _header_from_dests(
            status="ingested_missing",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=None,
            census_status=status,
        )
    if not incremental_store_path.is_file():
        raise TextGoldError("ingested_chunks_missing", "Incremental store is missing for this SHA.")
    incremental = json.loads(incremental_store_path.read_text(encoding="utf-8"))
    new_chunks = [
        dict(row) for row in (incremental.get("chunks") or [])
        if str(row.get("paper_sha256") or "") == paper_sha256
    ]
    if not new_chunks:
        raise TextGoldError("ingested_chunks_missing", "Incremental store has no chunks for this SHA.")
    product_ids = {
        str(row.get("chunk_id") or "")
        for row in (json.loads(engine_e2e.STORE_PATH.read_text(encoding="utf-8")).get("chunks") or [])
    }
    new_ids = {str(row["chunk_id"]) for row in new_chunks}
    if not new_ids.isdisjoint(product_ids):
        raise TextGoldError("sidecar_overlap", "Sidecar chunk_id set must be disjoint from the product 922.")
    known_sidecar = {str(row.get("chunk_id") or "") for row in (sidecar.get("chunks") or [])}
    if not new_ids.isdisjoint(known_sidecar):
        raise TextGoldError("sidecar_overlap", "Sidecar chunk_id set is not unique.")
    pending = _pending_vectors(incremental)
    id_to_vec: dict[str, list[float]] = {}
    if sidecar_gzip_path.is_file():
        previous = dense_d2.load_gzip_index(sidecar_gzip_path)
        prev_dense = previous.get("dense") or {}
        for chunk_id, vector in zip(prev_dense.get("chunk_ids") or [], prev_dense.get("vectors") or []):
            id_to_vec[str(chunk_id)] = list(vector)
    id_to_vec.update({chunk_id: pending[chunk_id] for chunk_id in new_ids if chunk_id in pending})
    missing_rows = [row for row in new_chunks if str(row["chunk_id"]) not in id_to_vec]
    n_embedded = 0
    if missing_rows:
        texts = [research.chunk_sparse_corpus(row) for row in missing_rows]
        if embedder is None:
            model_id, vectors = research._dense_vectors(texts, engine_e2e.MINILM_ID)
        else:
            model_id, vectors = embedder(texts, engine_e2e.MINILM_ID)
        del model_id
        if len(vectors) != len(missing_rows):
            raise TextGoldError("embed_align", "Promote embed count is not the missing chunk count.")
        for row, vector in zip(missing_rows, vectors):
            id_to_vec[str(row["chunk_id"])] = list(vector)
        n_embedded = len(missing_rows)
    working = json.loads(json.dumps(sidecar))
    working.setdefault("chunks", []).extend(new_chunks)
    papers = {str(sha) for sha in (working.get("indexed_paper_sha256") or []) if sha}
    papers.add(paper_sha256)
    working["indexed_paper_sha256"] = sorted(papers)
    working["n_chunks"] = len(working["chunks"])
    working["schema"] = engine_e2e.STORE_SCHEMA
    sidecar_store_sha = _write_json(sidecar_store_path, working)
    index = engine_e2e.store_to_literature_index(working, knowledgebase=SIDECAR_KNOWLEDGEBASE)
    dense_ids = [str(row["chunk_id"]) for row in (index.get("chunks") or [])]
    index["dense"] = {
        "model": engine_e2e.MINILM_ID,
        "dim": engine_e2e.EXPECTED_DIM,
        "chunk_ids": dense_ids,
        "vectors": [id_to_vec[chunk_id] for chunk_id in dense_ids],
    }
    sidecar_gzip_sha = _write_gzip(sidecar_gzip_path, index)
    sidecar_manifest = {
        "schema": engine_e2e.MANIFEST_SCHEMA,
        "knowledgebase": SIDECAR_KNOWLEDGEBASE,
        "index_path": str(sidecar_gzip_path),
        "n_indexed_papers": len(working["indexed_paper_sha256"]),
        "n_chunks": working["n_chunks"],
        "indexed_paper_sha256": list(working["indexed_paper_sha256"]),
        "store_sha256": sidecar_store_sha,
        "gzip_sha256": sidecar_gzip_sha,
        "embedder_in_index": True,
        "dense": {
            "model": engine_e2e.MINILM_ID,
            "dim": engine_e2e.EXPECTED_DIM,
            "n_vectors": len(dense_ids),
        },
    }
    _write_json(sidecar_index_path, sidecar_manifest)
    artifact = _union_floor(
        sidecar_index=index,
        sidecar_store_sha256=sidecar_store_sha,
        sidecar_gzip_sha256=sidecar_gzip_sha,
        dest_curves=dest_curves,
    )
    floor = float(artifact["shipped_floor"])
    dest_index = _ensure_dest_index(dest)
    promoted = {
        "knowledgebase": SIDECAR_KNOWLEDGEBASE,
        "index_path": str(sidecar_gzip_path),
        "n_papers": len(working["indexed_paper_sha256"]),
        "n_chunks": working["n_chunks"],
        "store_sha256": sidecar_store_sha,
        "gzip_sha256": sidecar_gzip_sha,
    }
    _update_product_index(dest_index, promoted=promoted, floor=floor)
    working_census = _mark_incremental_indexed(census, paper_sha256)
    _write_json(incremental_census_path, working_census)
    t5_corpus_graph.emit_t5_corpus_graph(
        dest=graph_path,
        store_path=engine_e2e.STORE_PATH,
        manifest_path=dest_index,
        sidecar_store_path=sidecar_store_path,
    )
    return _header_from_dests(
        status="ok",
        chunks_added=len(new_chunks),
        vectors_embedded=n_embedded,
        dest_dir=dest,
        floor=floor,
        census_status=INDEXED_STATUS,
    )


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise TextGoldError("one_sha", "RTI-1 takes exactly one paper SHA.")
    result = promote_ingested_paper(paper_sha256=args[0])
    print(json.dumps({
        "status": result["status"],
        "chunks_added": result["chunks_added"],
        "vectors_embedded": result["vectors_embedded"],
        "n_sidecar_papers": result["n_sidecar_papers"],
        "n_sidecar_chunks": result["n_sidecar_chunks"],
        "floor": result["floor"],
        "statistic": result["statistic"],
        "percentile": result["percentile"],
        "sidecar_store_sha256": result["sidecar_store_sha256"],
        "sidecar_gzip_sha256": result["sidecar_gzip_sha256"],
        "index_sha256": result["index_sha256"],
        "graph_sha256": result["graph_sha256"],
    }, indent=2))
    return result


if __name__ == "__main__":
    main()
