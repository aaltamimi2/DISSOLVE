"""G-3 overlay extract. No MiniLM. No gold needles. No paper-hash literals of the 19."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import (
    LITERATURE_AGENT_TOOLS,
    LITERATURE_CORPUS_TOOLS,
    LITERATURE_INGEST_TOOLS,
    LITERATURE_MODE_SURFACE,
    LITERATURE_SCHOLARLY_TOOLS,
    offered_tool_names,
    tool_schemas,
)
from dissolve import (
    contaminants,
    registry,
    research,
    t5_corpus_graph,
    t5_graph_extract,
    thermodynamics,
)
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR, TextGoldError

GRAPH = t5_corpus_graph.GRAPH_PATH
STORE = t5_corpus_graph.STORE_PATH
MANIFEST = t5_corpus_graph.MANIFEST_PATH
GOLD = t5_corpus_graph.GOLD_PATH
CENSUS = t5_corpus_graph.CENSUS_PATH
GZIP = Path(json.loads(MANIFEST.read_text(encoding="utf-8"))["index_path"])
MODULE = Path(t5_graph_extract.__file__).read_text(encoding="utf-8")
PIN_BEFORE = {
    GOLD: t5_corpus_graph.GOLD_SHA256,
    CENSUS: t5_corpus_graph.CENSUS_SHA256,
    STORE: t5_corpus_graph.STORE_SHA256,
    MANIFEST: t5_corpus_graph.MANIFEST_SHA256,
    GZIP: t5_corpus_graph.GZIP_SHA256,
    GRAPH: t5_graph_extract.GRAPH_SHA256,
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": "0ee86b2682fa2aad8dee0c686fe6ae3cf565af872834c0b7626e9effd888a346",
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": "1a946df2e556e34e5c78491c99c208f20aec6ba674aa4b887f359947b226a398",
    DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json": "2b5be504c4fac12af07b1cd366d2b290dedad1e8efbf52f2218d0e649c7c6718",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json": "8090143a69106ff5ba5db8bab70f7fbc7980fe4f54574d427c7815dca7379152",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json": "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2",
    DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json": "1def352d26baa5bce481f8c5ca6b4b81ac41186471007b2976ee133b675610ad",
}
PAPER_A = "ab" * 32
PAPER_B = "cd" * 32
PROMPT = "G3PAIRPROMPTZXQ"


def _literature_names(session=None) -> set[str]:
    return {item["name"] for item in tool_schemas(session)} & set(LITERATURE_AGENT_TOOLS)


def _walk_keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            _walk_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, found)


def _assert_pins_unmoved() -> None:
    for path, digest in PIN_BEFORE.items():
        assert file_sha256(path) == digest
    floor = json.loads(MANIFEST.read_text(encoding="utf-8"))["abstention"]["floor"]
    assert floor == 0.35126980440608535
    assert "promoted" not in json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "records" not in json.loads(GRAPH.read_text(encoding="utf-8"))
    assert not (DEFAULT_OUT_DIR / "GOLD.v2.json").is_file()


def _chunk(chunk_id: str, paper: str, body: str, ordinal: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_sha256": paper,
        "ordinal": ordinal,
        "body": body,
        "char_start": 0,
        "char_end": len(body),
        "page": 1,
        "section": "Methods",
        "section_origin": "parser_supplied",
        "kind": "paragraph",
        "block_ids": [f"b-{ordinal}"],
    }


def _mini_store() -> dict:
    return {
        "schema": "dissolve.t5-chunk-store.unsealed.v1",
        "n_chunks": 2,
        "indexed_paper_sha256": [PAPER_A, PAPER_B],
        "chunks": [
            _chunk("T5-g3-a", PAPER_A, "PET dissolves in toluene during the screen.", 1),
            _chunk("T5-g3-b", PAPER_B, "PET and toluene appear together again.", 1),
        ],
    }


def _write_mini(tmp_path: Path) -> tuple[Path, Path]:
    store = _mini_store()
    store_path = tmp_path / "store.json"
    store_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    graph_path = tmp_path / "graph.json"
    t5_corpus_graph.emit_t5_corpus_graph(
        dest=graph_path,
        store_path=store_path,
        manifest_path=MANIFEST,
    )
    return store_path, graph_path


def _g1_survivors(source: dict, overlay: dict) -> None:
    source_nodes = {node["node_id"]: node for node in source["nodes"]}
    overlay_nodes = {node["node_id"]: node for node in overlay["nodes"]}
    for node_id, node in source_nodes.items():
        assert overlay_nodes[node_id] == node
    source_edges = {edge["edge_id"]: edge for edge in source["edges"]}
    overlay_edges = {edge["edge_id"]: edge for edge in overlay["edges"]}
    for edge_id, edge in source_edges.items():
        assert overlay_edges[edge_id] == edge
    assert overlay["n_paper_nodes"] == source["n_paper_nodes"]
    assert overlay["n_chunk_nodes"] == source["n_chunk_nodes"]
    assert overlay["n_edges"] == source["n_edges"]


def test_extract_module_keep_out():
    lowered = MODULE.casefold()
    assert "urllib" not in lowered
    assert "requests" not in lowered
    assert "sentence_transformers" not in lowered
    assert "dense_d2" not in MODULE
    assert "text_chunk_metrics" not in MODULE
    assert "search_literature_corpus" not in MODULE
    assert "ingest_literature_graph" not in MODULE
    assert "merge_literature_graph" not in MODULE
    assert "literature_graph_path" not in MODULE
    assert "mint_one_paper" not in MODULE
    assert "scholarly |=" not in MODULE
    assert "GOLD.text.v1.unsealed.json" not in MODULE
    assert t5_graph_extract.SPEC_SHA256 == (
        "81b8ea013821e853b0b6a1a3337961f08fec4ec2421b00ca58a09feb15e839c3"
    )


def test_extract_persist_graph_is_refused():
    with pytest.raises(TextGoldError) as caught:
        t5_graph_extract.extract_t5_entity_graph(dest=GRAPH)
    assert caught.value.code == "persist_graph_refused"
    _assert_pins_unmoved()


def test_extract_tmp_overlay_mentions_and_same_entity(tmp_path):
    store_path, graph_path = _write_mini(tmp_path)
    dest = tmp_path / "overlay.json"
    source = json.loads(graph_path.read_text(encoding="utf-8"))
    before = GRAPH.read_bytes()
    first = t5_graph_extract.extract_t5_entity_graph(
        dest=dest, graph_path=graph_path, store_path=store_path,
    )
    overlay = json.loads(dest.read_text(encoding="utf-8"))
    _g1_survivors(source, overlay)
    assert overlay["schema"] == t5_graph_extract.SCHEMA
    assert overlay["spec_sha256"] == t5_graph_extract.SPEC_SHA256
    assert overlay["source_graph_sha256"] == file_sha256(graph_path)
    found: set[str] = set()
    _walk_keys(overlay, found)
    assert t5_corpus_graph.FORBIDDEN_KEYS.isdisjoint(found)
    entity_nodes = [node for node in overlay["nodes"] if node["node_type"] == "entity"]
    assert entity_nodes
    assert all(node["payload"]["registry"] in {"polymer", "solvent", "contaminant"} for node in entity_nodes)
    new_edges = [
        edge for edge in overlay["edges"]
        if edge["edge_type"] != "paper_has_chunk"
    ]
    types = {edge["edge_type"] for edge in new_edges}
    assert types <= t5_graph_extract.EDGE_TYPES
    for edge in new_edges:
        payload = edge["payload"]
        assert "chunk_id" in payload
        if edge["edge_type"] == "mentions":
            assert {"chunk_id", "char_start", "char_end", "registry", "canonical_key"} <= set(payload)
        elif edge["edge_type"] == "same_entity":
            assert {"chunk_id", "char_start", "char_end", "registry", "canonical_key", "provenance"} <= set(payload)
            assert len(payload["provenance"]) == 2
        elif edge["edge_type"] == "pair_mentioned":
            assert "chunk_id" in payload
            assert "polymer" in payload and "solvent" in payload
    assert first["n_mentions_edges"] >= 2
    assert first["n_same_entity_edges"] >= 1
    assert first["n_raw_mentions"] >= first["n_unresolved_mentions"]
    assert "unresolved_rate" in first
    if first["largest_same_entity_component"] == 1:
        assert first["corpus_connectivity"] == "disjoint"
    else:
        assert first["corpus_connectivity"] == "connected"
    for edge in new_edges:
        if edge["edge_type"] == "same_entity":
            assert edge["source_node_id"] < edge["target_node_id"]
    second = t5_graph_extract.extract_t5_entity_graph(
        dest=dest, graph_path=graph_path, store_path=store_path,
    )
    assert second["dest_sha256"] == first["dest_sha256"] == file_sha256(dest)
    assert GRAPH.read_bytes() == before
    _assert_pins_unmoved()


def test_product_extract_tmp_dest_leaves_persist_unmoved(tmp_path):
    dest = tmp_path / "product-overlay.json"
    before = GRAPH.read_bytes()
    header = t5_graph_extract.extract_t5_entity_graph(dest=dest)
    overlay = json.loads(dest.read_text(encoding="utf-8"))
    source = json.loads(before.decode("utf-8"))
    _g1_survivors(source, overlay)
    assert overlay["source_graph_sha256"] == t5_graph_extract.GRAPH_SHA256
    assert header["n_entity_nodes"] == len(
        [node for node in overlay["nodes"] if node["node_type"] == "entity"]
    )
    new_types = {
        edge["edge_type"]
        for edge in overlay["edges"]
        if edge["edge_type"] != "paper_has_chunk"
    }
    assert new_types <= t5_graph_extract.EDGE_TYPES
    found: set[str] = set()
    _walk_keys(overlay, found)
    assert t5_corpus_graph.FORBIDDEN_KEYS.isdisjoint(found)
    assert GRAPH.read_bytes() == before
    assert file_sha256(GRAPH) == t5_graph_extract.GRAPH_SHA256
    _assert_pins_unmoved()


def test_agent_list_and_registry_unmoved():
    names = _literature_names()
    assert names == set()
    corpus = {"literature_mode": {"mode": "corpus"}}
    scholarly = {"literature_mode": {"mode": "scholarly"}}
    assert _literature_names(corpus) == set(LITERATURE_CORPUS_TOOLS)
    assert _literature_names(scholarly) == set(LITERATURE_SCHOLARLY_TOOLS)
    blocked = {
        "extract_t5_entity_graph",
        "derive_t5_multihop_pairs",
        "score_t5_graph_rag",
        "render_t5_graph_rag_figures",
        "hang_t5_parameter_records",
        "resolve_t5_graph_record",
        "resolve_t5_chunk_records",
        "resolve_t5_graph_passage",
        "promote_ingested_paper",
        "emit_t5_corpus_graph",
        "inspect_t5_corpus_graph",
    }
    for mode in (None, corpus, scholarly, {"literature_mode": {"mode": "off"}}):
        offered = {item["name"] for item in tool_schemas(mode)}
        assert LITERATURE_INGEST_TOOLS.isdisjoint(offered)
        assert blocked.isdisjoint(offered)
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert blocked.isdisjoint(set(registry.BY_NAME))
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    thermo = file_sha256(_ROOT / "src" / "dissolve" / "thermo.py")
    leftover = file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py")
    assert thermo == "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    assert leftover == "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
    for rel in (
        "src/dissolve/thermodynamics.py",
        "src/dissolve/contaminants.py",
        "src/dissolve/cosmo_logp.py",
        "src/dissolve/thermo.py",
        "src/dissolve/t5_corpus_graph.py",
        "src/dissolve/t5_graph_lookup.py",
        "src/dissolve/t5_graph_records.py",
        "src/dissolve/t5_chunk_records.py",
        "agent_tools.py",
    ):
        assert not subprocess.check_output(["git", "diff", "HEAD", "--", rel], cwd=_ROOT)
    assert file_sha256(Path(thermodynamics._ASSET)) == (
        "4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc"
    )
    assert file_sha256(Path(contaminants._ASSET)) == (
        "866d769b6a140bf289c5036fd5c0d7d2b6f424cb7994e71c76e16a1a1d9a4c5f"
    )
    _assert_pins_unmoved()
