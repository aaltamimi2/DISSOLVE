"""G-2-reverse: chunk node → hung ParameterRecord payloads. No Muse. GRAPH is read-only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import t5_corpus_graph, t5_graph_lookup, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import TextGoldError

SPEC_SHA256 = "4b0d5e7103971fd954f1eb841715fd6a1bf124d0beaa9bf3499b3b3d607c2c44"
GRAPH_SHA256 = "3e7914dc0fe42b7e3a779e9a24ce2942b91f807645f74dee2e62197bebaf0c25"
_FORBIDDEN_RETURN_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_STRIP_FROM_RECORD = frozenset(t5_corpus_graph.FORBIDDEN_KEYS) | {"body", "body_plus_rebound"}


def _refuse_gold_v2() -> None:
    t5_graph_lookup._refuse_gold_v2()


def _refuse_url(value: str) -> None:
    t5_graph_lookup._refuse_url(value)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _missing(*, chunk_id: str, graph_path: Path) -> dict[str, Any]:
    payload = {
        "exists": False,
        "chunk_id": chunk_id,
        "graph_path": str(graph_path),
    }
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_RETURN_KEYS)):
        raise TextGoldError("gold_quoted", "Chunk-records return must not carry gold identifiers.")
    return payload


def _hung_records(graph: Mapping[str, Any], chunk_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_type") or "") != "ParameterRecord":
            continue
        payload = node.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if str(payload.get("chunk_id") or "") != chunk_id:
            continue
        row = {
            key: value
            for key, value in payload.items()
            if key not in _STRIP_FROM_RECORD
        }
        found.append(row)
    found.sort(key=lambda item: str(item.get("record_id") or ""))
    return found


def resolve_t5_chunk_records(
    *,
    chunk_id: str | None = None,
    node_id: str | None = None,
    dest: str | Path | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return ParameterRecord payloads hung on a GRAPH chunk. Does not write GRAPH."""
    _refuse_gold_v2()
    has_chunk = chunk_id is not None
    has_node = node_id is not None
    if has_chunk == has_node:
        raise TextGoldError(
            "identity_required",
            "Chunk-records takes exactly one of chunk_id or node_id.",
        )
    if has_node:
        _refuse_url(str(node_id))
        token = str(node_id)
        if token.startswith("paper:") or token.startswith("record:"):
            raise TextGoldError("not_a_chunk", "A paper or record node is not a chunk passage.")
        if not token.startswith("chunk:"):
            raise TextGoldError("not_a_chunk", "Chunk-records node_id must be a chunk node.")
        chunk_id = token[len("chunk:"):]
    else:
        chunk_id = str(chunk_id)
        _refuse_url(chunk_id)
    if not chunk_id:
        raise TextGoldError("identity_required", "Chunk-records chunk_id is empty.")
    graph_path = (
        Path(dest).expanduser().resolve() if dest is not None else t5_corpus_graph.GRAPH_PATH
    )
    if not graph_path.is_file():
        return _missing(chunk_id=chunk_id, graph_path=graph_path)
    before = file_sha256(graph_path)
    passage = t5_graph_lookup.resolve_t5_graph_passage(
        chunk_id=chunk_id,
        dest=graph_path,
        store_path=store_path,
    )
    if not passage.get("exists"):
        if file_sha256(graph_path) != before:
            raise TextGoldError("graph_mutated", "Chunk-records must not rewrite GRAPH.")
        return _missing(chunk_id=chunk_id, graph_path=graph_path)
    graph = _load_json(graph_path)
    records = _hung_records(graph, chunk_id)
    result = {
        "exists": True,
        "chunk_id": chunk_id,
        "body": passage["body"],
        "records": records,
    }
    if text_chunk_metrics._contains_forbidden_keys(result, set(_FORBIDDEN_RETURN_KEYS)):
        raise TextGoldError("gold_quoted", "Chunk-records return must not carry gold identifiers.")
    after = file_sha256(graph_path)
    if after != before:
        raise TextGoldError("graph_mutated", "Chunk-records must not rewrite GRAPH.")
    return result
