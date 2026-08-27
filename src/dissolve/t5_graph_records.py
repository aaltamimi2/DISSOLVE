"""G-2-typed: caller-supplied ParameterRecord overlay. No Muse. Persist GRAPH is read-only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import t5_corpus_graph, t5_graph_lookup, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "abc79a1459b22513467b5de67870ecfc824281f1dc711eb538db136b8a9e6212"
GRAPH_SHA256 = "3e7914dc0fe42b7e3a779e9a24ce2942b91f807645f74dee2e62197bebaf0c25"
_FORBIDDEN_RETURN_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_CLASS_FIELDS = {
    "ChiParameter": ("component_a", "component_b", "chi"),
    "SolubilityPoint": ("polymer", "solvent", "temperature", "solubility"),
}
_BASE_FIELDS = (
    "record_id",
    "record_class",
    "chunk_id",
    "paper_sha256",
    "char_start",
    "char_end",
)


def _refuse_gold_v2() -> None:
    t5_graph_lookup._refuse_gold_v2()


def _refuse_url(value: str) -> None:
    t5_graph_lookup._refuse_url(value)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _protected_dests() -> set[Path]:
    protected = {
        t5_corpus_graph.GRAPH_PATH.resolve(),
        t5_corpus_graph.STORE_PATH.resolve(),
        t5_corpus_graph.MANIFEST_PATH.resolve(),
        t5_corpus_graph.GOLD_PATH.resolve(),
        t5_corpus_graph.CENSUS_PATH.resolve(),
        (DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json").resolve(),
    }
    manifest = t5_corpus_graph.MANIFEST_PATH
    if manifest.is_file():
        index_path = json.loads(manifest.read_text(encoding="utf-8")).get("index_path")
        if index_path:
            protected.add(Path(str(index_path)).expanduser().resolve())
    return protected


def _refuse_pin_dest(dest: Path) -> None:
    if dest == t5_corpus_graph.GRAPH_PATH.resolve():
        raise TextGoldError("persist_graph_refused", "G-2-typed does not rewrite persist GRAPH.")
    if dest in _protected_dests() or dest.parent == t5_corpus_graph.GRAPH_PATH.resolve().parent:
        raise TextGoldError("persist_graph_refused", "G-2-typed does not mint persist dests.")


def _chunk_node(graph: Mapping[str, Any], chunk_id: str) -> Mapping[str, Any] | None:
    want = f"chunk:{chunk_id}"
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_id") or "") != want:
            continue
        if str(node.get("node_type") or "") != "chunk":
            raise TextGoldError("join_miss", "GRAPH chunk node is not a chunk.")
        payload = node.get("payload")
        if not isinstance(payload, Mapping):
            raise TextGoldError("t5_graph_chunk_keys", "Chunk node payload is not an object.")
        return payload
    return None


def _has_paper_node(graph: Mapping[str, Any], paper_sha256: str) -> bool:
    want = f"paper:{paper_sha256}"
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_id") or "") == want and str(node.get("node_type") or "") == "paper":
            return True
    return False


def _record_node(graph: Mapping[str, Any], record_id: str) -> Mapping[str, Any] | None:
    want = f"record:{record_id}"
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_id") or "") != want:
            continue
        if str(node.get("node_type") or "") != "ParameterRecord":
            raise TextGoldError("not_a_record", "GRAPH node is not a ParameterRecord.")
        payload = node.get("payload")
        if not isinstance(payload, Mapping):
            raise TextGoldError("identity_required", "ParameterRecord payload is not an object.")
        return payload
    return None


def _payload_from_record(row: Mapping[str, Any], graph: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in _BASE_FIELDS if key not in row]
    if missing:
        raise TextGoldError(
            "identity_required",
            "Typed record is missing required keys.",
            missing=missing,
        )
    record_id = str(row["record_id"])
    record_class = str(row["record_class"])
    chunk_id = str(row["chunk_id"])
    paper_sha256 = str(row["paper_sha256"])
    _refuse_url(record_id)
    _refuse_url(chunk_id)
    if not record_id or not chunk_id or not paper_sha256:
        raise TextGoldError("identity_required", "Typed record identity is empty.")
    fields = _CLASS_FIELDS.get(record_class)
    if fields is None:
        raise TextGoldError(
            "literature_record_class_unsupported",
            "G-2-typed accepts only ChiParameter and SolubilityPoint.",
        )
    class_missing = [key for key in fields if key not in row]
    if class_missing:
        raise TextGoldError(
            "identity_required",
            "Typed record is missing class fields.",
            missing=class_missing,
        )
    chunk = _chunk_node(graph, chunk_id)
    if chunk is None:
        raise TextGoldError("join_miss", "Record chunk_id is not on the source GRAPH.")
    if str(chunk.get("paper_sha256") or "") != paper_sha256:
        raise TextGoldError("join_mismatch", "Record paper_sha256 is not IDENT the chunk.")
    if not _has_paper_node(graph, paper_sha256):
        raise TextGoldError("join_miss", "Record paper node is not on the source GRAPH.")
    char_start = int(row["char_start"])
    char_end = int(row["char_end"])
    chunk_start = int(chunk["char_start"])
    chunk_end = int(chunk["char_end"])
    if char_start > char_end or char_start < chunk_start or char_end > chunk_end:
        raise TextGoldError("join_mismatch", "Record span is not inside the chunk span.")
    payload = {
        "record_id": record_id,
        "record_class": record_class,
        "chunk_id": chunk_id,
        "paper_sha256": paper_sha256,
        "char_start": char_start,
        "char_end": char_end,
    }
    for key in fields:
        payload[key] = row[key]
    if text_chunk_metrics._contains_forbidden_keys(payload, set(t5_corpus_graph.FORBIDDEN_KEYS)):
        raise TextGoldError("gold_quoted", "Typed record must not carry gold identifiers.")
    return payload


def _overlay_graph(
    source: Mapping[str, Any],
    *,
    records_block: Mapping[str, Any],
    extra_nodes: list[dict[str, Any]],
    extra_edges: list[dict[str, Any]],
) -> dict[str, Any]:
    overlay: dict[str, Any] = {}
    inserted = False
    for key, value in source.items():
        if key == "records":
            continue
        if key == "n_edges":
            overlay[key] = value
            overlay["records"] = dict(records_block)
            inserted = True
            continue
        if key == "nodes":
            overlay["nodes"] = list(value) + extra_nodes
            continue
        if key == "edges":
            overlay["edges"] = list(value) + extra_edges
            continue
        overlay[key] = value
    if not inserted:
        overlay["records"] = dict(records_block)
    overlay.setdefault("nodes", list(source.get("nodes") or []) + extra_nodes)
    overlay.setdefault("edges", list(source.get("edges") or []) + extra_edges)
    return overlay


def _missing(*, record_id: str, graph_path: Path) -> dict[str, Any]:
    payload = {
        "exists": False,
        "record_id": record_id,
        "graph_path": str(graph_path),
    }
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_RETURN_KEYS)):
        raise TextGoldError("gold_quoted", "Record resolve must not carry gold identifiers.")
    return payload


def hang_t5_parameter_records(
    *,
    records: Sequence[Mapping[str, Any]],
    dest: str | Path,
    graph_path: str | Path | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Hang caller-supplied records onto an overlay GRAPH dest. Does not write persist GRAPH."""
    _refuse_gold_v2()
    del store_path
    dest_path = Path(dest).expanduser().resolve()
    _refuse_pin_dest(dest_path)
    source_path = (
        Path(graph_path).expanduser().resolve()
        if graph_path is not None
        else t5_corpus_graph.GRAPH_PATH
    )
    if dest_path == source_path:
        raise TextGoldError("persist_graph_refused", "Overlay dest must not be the source GRAPH.")
    if not source_path.is_file():
        raise TextGoldError("store_missing", "Source GRAPH is not a file.")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise TextGoldError("identity_required", "Hang records must be a sequence.")
    rows = [dict(row) for row in records]
    if not rows:
        raise TextGoldError("identity_required", "Hang records must not be empty.")
    seen: set[str] = set()
    source_digest = file_sha256(source_path)
    source = _clone(_load_json(source_path))
    extra_nodes: list[dict[str, Any]] = []
    extra_edges: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise TextGoldError("identity_required", "Each hang record must be an object.")
        payload = _payload_from_record(row, source)
        record_id = payload["record_id"]
        if record_id in seen:
            raise TextGoldError("identity_required", "Hang record_id set must be unique.")
        seen.add(record_id)
        paper_sha256 = payload["paper_sha256"]
        extra_nodes.append({
            "node_id": f"record:{record_id}",
            "node_type": "ParameterRecord",
            "canonical_key": record_id,
            "payload": payload,
        })
        extra_edges.append({
            "edge_id": f"reports:{paper_sha256}:{record_id}",
            "edge_type": "REPORTS",
            "source_node_id": f"paper:{paper_sha256}",
            "target_node_id": f"record:{record_id}",
        })
    extra_nodes.sort(key=lambda item: str(item["node_id"]))
    extra_edges.sort(key=lambda item: str(item["edge_id"]))
    records_block = {
        "n_record_nodes": len(extra_nodes),
        "n_reports_edges": len(extra_edges),
        "source_graph_sha256": source_digest,
    }
    overlay = _overlay_graph(
        source,
        records_block=records_block,
        extra_nodes=extra_nodes,
        extra_edges=extra_edges,
    )
    t5_corpus_graph._walk_forbidden(overlay)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(t5_corpus_graph._dump(overlay), encoding="utf-8")
    result = {
        "dest": str(dest_path),
        "dest_sha256": file_sha256(dest_path),
        "n_record_nodes": records_block["n_record_nodes"],
        "n_reports_edges": records_block["n_reports_edges"],
    }
    if text_chunk_metrics._contains_forbidden_keys(result, set(_FORBIDDEN_RETURN_KEYS)):
        raise TextGoldError("gold_quoted", "Hang return must not carry gold identifiers.")
    return result


def resolve_t5_graph_record(
    *,
    record_id: str | None = None,
    node_id: str | None = None,
    dest: str | Path | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Join a ParameterRecord node to the store passage. Does not write GRAPH."""
    _refuse_gold_v2()
    has_record = record_id is not None
    has_node = node_id is not None
    if has_record == has_node:
        raise TextGoldError(
            "identity_required",
            "Record resolve takes exactly one of record_id or node_id.",
        )
    if has_node:
        _refuse_url(str(node_id))
        token = str(node_id)
        if token.startswith("paper:") or token.startswith("chunk:"):
            raise TextGoldError("not_a_record", "A paper or chunk node is not a ParameterRecord.")
        if not token.startswith("record:"):
            raise TextGoldError("not_a_record", "Record node_id must be a ParameterRecord node.")
        record_id = token[len("record:"):]
    else:
        record_id = str(record_id)
        _refuse_url(record_id)
    if not record_id:
        raise TextGoldError("identity_required", "Record resolve record_id is empty.")
    graph_path = (
        Path(dest).expanduser().resolve() if dest is not None else t5_corpus_graph.GRAPH_PATH
    )
    if not graph_path.is_file():
        return _missing(record_id=record_id, graph_path=graph_path)
    before = file_sha256(graph_path)
    graph = _load_json(graph_path)
    payload = _record_node(graph, record_id)
    if payload is None:
        if file_sha256(graph_path) != before:
            raise TextGoldError("graph_mutated", "Record resolve must not rewrite GRAPH.")
        return _missing(record_id=record_id, graph_path=graph_path)
    passage = t5_graph_lookup.resolve_t5_graph_passage(
        chunk_id=str(payload.get("chunk_id") or ""),
        dest=graph_path,
        store_path=store_path,
    )
    if not passage.get("exists"):
        raise TextGoldError("join_miss", "Record chunk_id does not join a store passage.")
    result = {
        "exists": True,
        "record_id": str(payload.get("record_id") or record_id),
        "record_class": str(payload.get("record_class") or ""),
        "chunk_id": str(payload.get("chunk_id") or ""),
        "paper_sha256": str(payload.get("paper_sha256") or ""),
        "char_start": int(payload["char_start"]),
        "char_end": int(payload["char_end"]),
        "body": passage["body"],
    }
    fields = _CLASS_FIELDS.get(result["record_class"]) or ()
    for key in fields:
        result[key] = payload[key]
    if text_chunk_metrics._contains_forbidden_keys(result, set(_FORBIDDEN_RETURN_KEYS)):
        raise TextGoldError("gold_quoted", "Record resolve must not carry gold identifiers.")
    after = file_sha256(graph_path)
    if after != before:
        raise TextGoldError("graph_mutated", "Record resolve must not rewrite GRAPH.")
    return result
