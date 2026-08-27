"""G-1 T5 corpus graph. Same 19-paper store as retrieval. No MiniLM. No billed extractor."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "1be3aa08f16e01d15be88890bb52920e74f8fa7d0d83ad8ecb71c6fd49bb8b26"
SCHEMA = "dissolve.t5-corpus-graph.unsealed.v1"
KNOWLEDGEBASE = "t5-indexed-unsealed"
GRAPH_NAME = "GRAPH.t5.unsealed.v1.json"
GRAPH_PATH = DEFAULT_OUT_DIR / GRAPH_NAME
STORE_PATH = DEFAULT_OUT_DIR / "CHUNKS.t5.indexed.unsealed.v1.json"
MANIFEST_PATH = DEFAULT_OUT_DIR / "INDEX.t5.unsealed.v1.json"
SIDECAR_STORE_NAME = "CHUNKS.t5.promoted.unsealed.v1.json"
SIDECAR_GZIP_NAME = "t5-promoted-unsealed.json.gz"
SIDECAR_KNOWLEDGEBASE = "t5-promoted-unsealed"
GOLD_PATH = DEFAULT_OUT_DIR / "GOLD.text.v1.unsealed.json"
CENSUS_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v3.json")

GOLD_SHA256 = "1c6df27b08dde99c675a01ecc8f99dbfb8dbcee250f298b3e1e352a56816c7d8"
CENSUS_SHA256 = "b60d9791eb1bc56a8e417df48c22e3b22faa9f3a44bf97022ec58d96495792f7"
STORE_SHA256 = "91851dbf3b979450d087f86acca8c146205e0d0a8c41cc49c3aab1fc9cf73a40"
MANIFEST_SHA256 = "61bbe29541cc3bb12a5257191433c510be0375ad984ae054f227dfe471ca6edd"
GZIP_SHA256 = "bf0bb9e213401868bac45590f364bc831976765fc9b258357523a981d2fe14f1"
EXPECTED_N_PAPERS = 19
EXPECTED_N_CHUNKS = 922

CHUNK_PAYLOAD_KEYS = (
    "chunk_id",
    "paper_sha256",
    "ordinal",
    "char_start",
    "char_end",
    "page",
    "section",
    "section_origin",
    "kind",
    "block_ids",
)
FORBIDDEN_KEYS = frozenset({
    "body",
    "body_plus_rebound",
    "fact_id",
    "needles",
    "query",
    "evidence_quote",
    "canonical_text",
    "abstention",
})


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _indexed_paper_shas(gold: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("paper_sha256") or "")
        for row in (gold.get("papers") or [])
        if str(row.get("paper_status") or "") == "indexed" and row.get("paper_sha256")
    }


def _census_indexed_shas(census: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("sha256") or "")
        for row in (census.get("papers") or [])
        if str(row.get("status") or "") == "indexed" and row.get("sha256")
    }


def _walk_forbidden(value: Any, *, trail: str = "$") -> None:
    if isinstance(value, Mapping):
        extra = FORBIDDEN_KEYS.intersection(value)
        if extra:
            raise TextGoldError(
                "t5_graph_forbidden_key",
                "T5 corpus graph must not carry store bodies or gold keys.",
                keys=sorted(extra),
                trail=trail,
            )
        for key, item in value.items():
            _walk_forbidden(item, trail=f"{trail}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_forbidden(item, trail=f"{trail}[{index}]")


def _chunk_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in CHUNK_PAYLOAD_KEYS if key not in row]
    if missing:
        raise TextGoldError(
            "t5_graph_chunk_keys",
            "Store chunk is missing G-1 coordinate keys.",
            missing=missing,
        )
    block_ids = row["block_ids"]
    if not isinstance(block_ids, list):
        raise TextGoldError("t5_graph_chunk_keys", "block_ids must be a list.")
    return {
        "chunk_id": str(row["chunk_id"]),
        "paper_sha256": str(row["paper_sha256"]),
        "ordinal": int(row["ordinal"]),
        "char_start": int(row["char_start"]),
        "char_end": int(row["char_end"]),
        "page": row["page"],
        "section": row["section"],
        "section_origin": row["section_origin"],
        "kind": str(row["kind"]),
        "block_ids": [str(item) for item in block_ids],
    }


def _header(
    *,
    gold_sha256: str,
    census_sha256: str,
    store_sha256: str,
    index_manifest_sha256: str,
    gzip_sha256: str,
    n_paper_nodes: int,
    n_chunk_nodes: int,
    n_edges: int,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    promoted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "spec_sha256": SPEC_SHA256,
        "gold_sha256": gold_sha256,
        "census_sha256": census_sha256,
        "store_sha256": store_sha256,
        "index_manifest_sha256": index_manifest_sha256,
        "gzip_sha256": gzip_sha256,
        "knowledgebase": KNOWLEDGEBASE,
        "n_paper_nodes": n_paper_nodes,
        "n_chunk_nodes": n_chunk_nodes,
        "n_edges": n_edges,
    }
    if promoted is not None:
        payload["promoted"] = dict(promoted)
    payload["nodes"] = nodes
    payload["edges"] = edges
    return payload


def _require_product_identity(
    store: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    gold_sha256: str,
    census_sha256: str,
    store_sha256: str,
    manifest_sha256: str,
    gzip_sha256: str,
    require_manifest_pin: bool = True,
) -> None:
    gold = _load_json(GOLD_PATH)
    census = _load_json(CENSUS_PATH)
    gold_set = _indexed_paper_shas(gold)
    census_set = _census_indexed_shas(census)
    index_set = {str(sha) for sha in (manifest.get("indexed_paper_sha256") or []) if sha}
    store_header = {str(sha) for sha in (store.get("indexed_paper_sha256") or []) if sha}
    chunk_papers = {str(row.get("paper_sha256") or "") for row in (store.get("chunks") or [])}
    chunk_ids = [str(row.get("chunk_id") or "") for row in (store.get("chunks") or [])]
    if not all(chunk_ids) or len(chunk_ids) != len(set(chunk_ids)):
        raise TextGoldError("t5_graph_identity", "Store chunk_id set is not unique.")
    if gold_set != census_set or gold_set != index_set or gold_set != store_header or gold_set != chunk_papers:
        raise TextGoldError("t5_graph_identity", "Paper SHA sets are not IDENT.")
    if len(gold_set) != EXPECTED_N_PAPERS:
        raise TextGoldError("t5_graph_identity", "Indexed paper set is not the pinned 19.")
    if len(chunk_ids) != EXPECTED_N_CHUNKS:
        raise TextGoldError("t5_graph_identity", "Chunk set is not the pinned 922.")
    if str(manifest.get("knowledgebase") or "") != KNOWLEDGEBASE:
        raise TextGoldError("t5_graph_identity", "Manifest knowledgebase is not the T5 product.")
    pins = {
        "gold_sha256": (gold_sha256, GOLD_SHA256),
        "census_sha256": (census_sha256, CENSUS_SHA256),
        "store_sha256": (store_sha256, STORE_SHA256),
        "gzip_sha256": (gzip_sha256, GZIP_SHA256),
    }
    if require_manifest_pin:
        pins["index_manifest_sha256"] = (manifest_sha256, MANIFEST_SHA256)
    mismatch = [name for name, (got, want) in pins.items() if got != want]
    if mismatch:
        raise TextGoldError("t5_graph_identity", "Header pin fields are not IDENT §1.", mismatch=mismatch)


def _resolve_sidecar_store(
    dest_path: Path,
    sidecar_store_path: str | Path | None,
) -> Path | None:
    if sidecar_store_path is not None:
        path = Path(sidecar_store_path).expanduser().resolve()
        if not path.is_file():
            raise TextGoldError("t5_graph_sidecar", "Sidecar store path is not a file.")
        return path
    candidate = dest_path.parent / SIDECAR_STORE_NAME
    return candidate.resolve() if candidate.is_file() else None


def _append_store_rows(
    store: Mapping[str, Any],
    *,
    papers: dict[str, dict[str, Any]],
    chunks: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    seen_chunks: set[str],
) -> None:
    for row in store.get("chunks") or []:
        if not isinstance(row, Mapping):
            raise TextGoldError("t5_graph_chunk_keys", "Store chunk is not an object.")
        payload = _chunk_payload(row)
        chunk_id = payload["chunk_id"]
        paper_sha = payload["paper_sha256"]
        if not chunk_id or not paper_sha:
            raise TextGoldError("t5_graph_identity", "Chunk identity is empty.")
        if chunk_id in seen_chunks:
            raise TextGoldError("t5_graph_identity", "Store chunk_id set is not unique.")
        seen_chunks.add(chunk_id)
        papers[paper_sha] = {
            "node_id": f"paper:{paper_sha}",
            "node_type": "paper",
            "canonical_key": paper_sha,
            "payload": {"paper_sha256": paper_sha},
        }
        chunks.append({
            "node_id": f"chunk:{chunk_id}",
            "node_type": "chunk",
            "canonical_key": chunk_id,
            "payload": payload,
        })
        edges.append({
            "edge_id": f"paper_has_chunk:{paper_sha}:{chunk_id}",
            "edge_type": "paper_has_chunk",
            "source_node_id": f"paper:{paper_sha}",
            "target_node_id": f"chunk:{chunk_id}",
        })


def emit_t5_corpus_graph(
    *,
    dest: str | Path | None = None,
    store_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    sidecar_store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write GRAPH.t5 from the store. Passage bodies stay in the store."""
    dest_path = Path(dest).expanduser().resolve() if dest is not None else GRAPH_PATH
    store_file = Path(store_path).expanduser().resolve() if store_path is not None else STORE_PATH
    manifest_file = (
        Path(manifest_path).expanduser().resolve() if manifest_path is not None else MANIFEST_PATH
    )
    store = _load_json(store_file)
    manifest = _load_json(manifest_file)
    gzip_file = Path(str(manifest.get("index_path") or "")).expanduser()
    if not gzip_file.is_file():
        raise TextGoldError("t5_graph_gzip", "Manifest index_path is not a file.")
    gold_sha = file_sha256(GOLD_PATH)
    census_sha = file_sha256(CENSUS_PATH)
    store_sha = file_sha256(store_file)
    manifest_sha = file_sha256(manifest_file)
    gzip_sha = file_sha256(gzip_file)
    sidecar_file = _resolve_sidecar_store(dest_path, sidecar_store_path)
    sidecar_store = _load_json(sidecar_file) if sidecar_file is not None else None
    if dest_path == GRAPH_PATH.resolve():
        if store_file != STORE_PATH.resolve() or store_sha != STORE_SHA256:
            raise TextGoldError(
                "t5_graph_product_store",
                "Product GRAPH dest requires the pinned T5 store.",
            )
        _require_product_identity(
            store,
            manifest,
            gold_sha256=gold_sha,
            census_sha256=census_sha,
            store_sha256=store_sha,
            manifest_sha256=manifest_sha,
            gzip_sha256=gzip_sha,
            require_manifest_pin=sidecar_store is None,
        )

    papers: dict[str, dict[str, Any]] = {}
    chunks: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    _append_store_rows(
        store, papers=papers, chunks=chunks, edges=edges, seen_chunks=seen_chunks,
    )
    promoted = None
    if sidecar_store is not None and sidecar_file is not None:
        sidecar_gzip = dest_path.parent / "indexes" / SIDECAR_GZIP_NAME
        _append_store_rows(
            sidecar_store, papers=papers, chunks=chunks, edges=edges, seen_chunks=seen_chunks,
        )
        sidecar_papers = {
            str(row.get("paper_sha256") or "")
            for row in (sidecar_store.get("chunks") or [])
            if row.get("paper_sha256")
        }
        promoted = {
            "knowledgebase": SIDECAR_KNOWLEDGEBASE,
            "index_path": str(sidecar_gzip) if sidecar_gzip.is_file() else "",
            "n_papers": len(sidecar_papers),
            "n_chunks": len(sidecar_store.get("chunks") or []),
            "store_sha256": file_sha256(sidecar_file),
            "gzip_sha256": file_sha256(sidecar_gzip) if sidecar_gzip.is_file() else "",
        }

    nodes = sorted(
        list(papers.values()) + chunks,
        key=lambda row: str(row["node_id"]),
    )
    edges = sorted(edges, key=lambda row: str(row["edge_id"]))
    graph = _header(
        gold_sha256=gold_sha,
        census_sha256=census_sha,
        store_sha256=store_sha,
        index_manifest_sha256=manifest_sha,
        gzip_sha256=gzip_sha,
        n_paper_nodes=len(papers),
        n_chunk_nodes=len(chunks),
        n_edges=len(edges),
        nodes=nodes,
        edges=edges,
        promoted=promoted,
    )
    _walk_forbidden(graph)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    text = _dump(graph)
    dest_path.write_text(text, encoding="utf-8")
    digest = file_sha256(dest_path)
    return {
        "schema": SCHEMA,
        "spec_sha256": SPEC_SHA256,
        "gold_sha256": gold_sha,
        "census_sha256": census_sha,
        "store_sha256": store_sha,
        "index_manifest_sha256": manifest_sha,
        "gzip_sha256": gzip_sha,
        "knowledgebase": KNOWLEDGEBASE,
        "n_paper_nodes": len(papers),
        "n_chunk_nodes": len(chunks),
        "n_edges": len(edges),
        "dest": str(dest_path),
        "dest_sha256": digest,
    }


def inspect_t5_corpus_graph(*, dest: str | Path | None = None) -> dict[str, Any]:
    """Return bounded counts and pins. Does not create the dest. No MiniLM."""
    path = Path(dest).expanduser().resolve() if dest is not None else GRAPH_PATH
    empty = {"n_paper_nodes": 0, "n_chunk_nodes": 0, "n_edges": 0}
    if not path.is_file():
        return {
            "schema": SCHEMA,
            "exists": False,
            "graph_path": str(path),
            "counts": empty,
            "spec_sha256": None,
            "gold_sha256": None,
            "census_sha256": None,
            "store_sha256": None,
            "index_manifest_sha256": None,
            "gzip_sha256": None,
            "knowledgebase": None,
        }
    graph = _load_json(path)
    _walk_forbidden(graph)
    return {
        "schema": str(graph.get("schema") or SCHEMA),
        "exists": True,
        "graph_path": str(path),
        "counts": {
            "n_paper_nodes": int(graph.get("n_paper_nodes") or 0),
            "n_chunk_nodes": int(graph.get("n_chunk_nodes") or 0),
            "n_edges": int(graph.get("n_edges") or 0),
        },
        "spec_sha256": graph.get("spec_sha256"),
        "gold_sha256": graph.get("gold_sha256"),
        "census_sha256": graph.get("census_sha256"),
        "store_sha256": graph.get("store_sha256"),
        "index_manifest_sha256": graph.get("index_manifest_sha256"),
        "gzip_sha256": graph.get("gzip_sha256"),
        "knowledgebase": graph.get("knowledgebase"),
    }
