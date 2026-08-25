"""G-2-join: GRAPH chunk node → store passage. No Muse. No MiniLM. GRAPH is read-only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import t5_corpus_graph, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import TextGoldError

SPEC_SHA256 = "9b4ea4c68273dbea88d223bf346c03e988483cc83d439a9b98706e7661221ca8"
GRAPH_SHA256 = "3e7914dc0fe42b7e3a779e9a24ce2942b91f807645f74dee2e62197bebaf0c25"
_FORBIDDEN_RETURN_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})


def _refuse_gold_v2() -> None:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    extra = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/GOLD.v2.json")
    if extra.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")


def _refuse_url(value: str) -> None:
    folded = str(value or "").strip().casefold()
    if folded.startswith(("http://", "https://", "ftp://")):
        raise TextGoldError("url_fetch_refused", "Lookup does not fetch URLs.")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _chunk_index(store: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for row in store.get("chunks") or []:
        if not isinstance(row, Mapping):
            continue
        chunk_id = str(row.get("chunk_id") or "")
        if not chunk_id:
            continue
        index[chunk_id] = row
    return index


def _graph_chunk_payload(graph: Mapping[str, Any], chunk_id: str) -> Mapping[str, Any] | None:
    want = f"chunk:{chunk_id}"
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_id") or "") != want:
            continue
        if str(node.get("node_type") or "") != "chunk":
            raise TextGoldError("not_a_chunk", "GRAPH node is not a chunk passage.")
        payload = node.get("payload")
        if not isinstance(payload, Mapping):
            raise TextGoldError("t5_graph_chunk_keys", "Chunk node payload is not an object.")
        return payload
    return None


def _coordinates(payload: Mapping[str, Any]) -> dict[str, Any]:
    return t5_corpus_graph._chunk_payload(payload)


def _coordinates_ident(graph_payload: Mapping[str, Any], store_row: Mapping[str, Any]) -> None:
    graph_coords = _coordinates(graph_payload)
    store_coords = _coordinates(store_row)
    if graph_coords != store_coords:
        raise TextGoldError("join_mismatch", "GRAPH coordinates are not IDENT the store row.")


def _sidecar_store_path(graph_path: Path, graph: Mapping[str, Any]) -> Path | None:
    if not isinstance(graph.get("promoted"), Mapping):
        return None
    candidate = graph_path.parent / t5_corpus_graph.SIDECAR_STORE_NAME
    return candidate if candidate.is_file() else None


def _missing(*, chunk_id: str, graph_path: Path) -> dict[str, Any]:
    payload = {
        "exists": False,
        "chunk_id": chunk_id,
        "graph_path": str(graph_path),
    }
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_RETURN_KEYS)):
        raise TextGoldError("gold_quoted", "Lookup return must not carry gold identifiers.")
    return payload


def resolve_t5_graph_passage(
    *,
    chunk_id: str | None = None,
    node_id: str | None = None,
    dest: str | Path | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Join a GRAPH chunk node to the store passage. Does not write GRAPH."""
    _refuse_gold_v2()
    has_chunk = chunk_id is not None
    has_node = node_id is not None
    if has_chunk == has_node:
        raise TextGoldError(
            "identity_required",
            "Lookup takes exactly one of chunk_id or node_id.",
        )
    if has_node:
        _refuse_url(str(node_id))
        token = str(node_id)
        if token.startswith("paper:"):
            raise TextGoldError("not_a_chunk", "A paper node is not a passage.")
        if not token.startswith("chunk:"):
            raise TextGoldError("not_a_chunk", "Lookup node_id must be a chunk node.")
        chunk_id = token[len("chunk:"):]
    else:
        chunk_id = str(chunk_id)
        _refuse_url(chunk_id)
    if not chunk_id:
        raise TextGoldError("identity_required", "Lookup chunk_id is empty.")
    graph_path = (
        Path(dest).expanduser().resolve() if dest is not None else t5_corpus_graph.GRAPH_PATH
    )
    store_file = (
        Path(store_path).expanduser().resolve() if store_path is not None else t5_corpus_graph.STORE_PATH
    )
    if not graph_path.is_file():
        return _missing(chunk_id=chunk_id, graph_path=graph_path)
    before = file_sha256(graph_path)
    graph = _load_json(graph_path)
    t5_corpus_graph._walk_forbidden(graph)
    payload = _graph_chunk_payload(graph, chunk_id)
    if payload is None:
        if file_sha256(graph_path) != before:
            raise TextGoldError("graph_mutated", "Lookup must not rewrite GRAPH.")
        return _missing(chunk_id=chunk_id, graph_path=graph_path)
    if not store_file.is_file():
        raise TextGoldError("store_missing", "Product store is not a file.")
    product_index = _chunk_index(_load_json(store_file))
    sidecar_file = _sidecar_store_path(graph_path, graph)
    sidecar_index = _chunk_index(_load_json(sidecar_file)) if sidecar_file is not None else {}
    if chunk_id in product_index and chunk_id in sidecar_index:
        raise TextGoldError("sidecar_overlap", "Sidecar chunk_id set must be disjoint from the product store.")
    store_row = product_index.get(chunk_id) or sidecar_index.get(chunk_id)
    if store_row is None:
        raise TextGoldError("join_miss", "GRAPH chunk is not in the joined store.")
    _coordinates_ident(payload, store_row)
    coords = _coordinates(payload)
    result = {"exists": True, **coords, "body": str(store_row.get("body") or "")}
    rebound = store_row.get("body_plus_rebound")
    if rebound and str(rebound) != result["body"]:
        result["body_plus_rebound"] = str(rebound)
    if text_chunk_metrics._contains_forbidden_keys(result, set(_FORBIDDEN_RETURN_KEYS)):
        raise TextGoldError("gold_quoted", "Lookup return must not carry gold identifiers.")
    after = file_sha256(graph_path)
    if after != before:
        raise TextGoldError("graph_mutated", "Lookup must not rewrite GRAPH.")
    return result
