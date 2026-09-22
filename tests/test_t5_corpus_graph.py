"""G-1 T5 corpus graph. No MiniLM. No gold needles. No paper-hash literals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve.agent_tools import (
    LITERATURE_AGENT_TOOLS,
    LITERATURE_CORPUS_TOOLS,
    LITERATURE_INGEST_TOOLS,
    LITERATURE_MODE_SURFACE,
    LITERATURE_SCHOLARLY_TOOLS,
    offered_tool_names,
    tool_schemas,
)
from dissolve import registry, research, t5_corpus_graph
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR

GRAPH = t5_corpus_graph.GRAPH_PATH
STORE = t5_corpus_graph.STORE_PATH
MANIFEST = t5_corpus_graph.MANIFEST_PATH
GOLD = t5_corpus_graph.GOLD_PATH
CENSUS = t5_corpus_graph.CENSUS_PATH
GZIP = Path(json.loads(MANIFEST.read_text(encoding="utf-8"))["index_path"])
MODULE = Path(t5_corpus_graph.__file__).read_text(encoding="utf-8")
FORBIDDEN = t5_corpus_graph.FORBIDDEN_KEYS
PIN_BEFORE = {
    GOLD: t5_corpus_graph.GOLD_SHA256,
    CENSUS: t5_corpus_graph.CENSUS_SHA256,
    STORE: t5_corpus_graph.STORE_SHA256,
    MANIFEST: t5_corpus_graph.MANIFEST_SHA256,
    GZIP: t5_corpus_graph.GZIP_SHA256,
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": "0ee86b2682fa2aad8dee0c686fe6ae3cf565af872834c0b7626e9effd888a346",
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": "1a946df2e556e34e5c78491c99c208f20aec6ba674aa4b887f359947b226a398",
    DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json": "2b5be504c4fac12af07b1cd366d2b290dedad1e8efbf52f2218d0e649c7c6718",
}


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
    assert not (DEFAULT_OUT_DIR / "GOLD.v2.json").is_file()
    assert not Path("/home/aaltamimi2/dissolve-v12-audit/corpus/GOLD.v2.json").is_file()


def _emit_product():
    return t5_corpus_graph.emit_t5_corpus_graph()


def test_module_keep_out_has_no_muse_minilm_or_duckdb():
    lowered = MODULE.casefold()
    assert "sentence_transformers" not in lowered
    assert "literature_ingest" not in MODULE
    assert "ingest_literature_graph" not in MODULE
    assert "merge_literature_graph" not in MODULE
    assert "literature_graph_path" not in MODULE
    assert "duckdb" not in lowered
    assert "_default_extractor" not in MODULE


def test_inspect_missing_path_does_not_create(tmp_path):
    missing = tmp_path / "absent-graph.json"
    payload = t5_corpus_graph.inspect_t5_corpus_graph(dest=missing)
    assert payload["exists"] is False
    assert payload["counts"] == {
        "n_paper_nodes": 0, "n_chunk_nodes": 0, "n_edges": 0,
    }
    assert not missing.exists()
    _assert_pins_unmoved()


def test_emit_product_graph_set_identity_and_coordinates():
    _assert_pins_unmoved()
    first = _emit_product()
    second = _emit_product()
    assert first["dest_sha256"] == second["dest_sha256"]
    assert file_sha256(GRAPH) == first["dest_sha256"]
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    store = json.loads(STORE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    text = GRAPH.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "\r" not in text
    assert list(graph)[:11] == [
        "schema", "spec_sha256", "gold_sha256", "census_sha256", "store_sha256",
        "index_manifest_sha256", "gzip_sha256", "knowledgebase",
        "n_paper_nodes", "n_chunk_nodes", "n_edges",
    ]
    assert graph["schema"] == t5_corpus_graph.SCHEMA
    assert graph["knowledgebase"] == "t5-indexed-unsealed"
    assert graph["spec_sha256"] == t5_corpus_graph.SPEC_SHA256
    assert graph["gold_sha256"] == t5_corpus_graph.GOLD_SHA256
    assert graph["census_sha256"] == t5_corpus_graph.CENSUS_SHA256
    assert graph["store_sha256"] == t5_corpus_graph.STORE_SHA256
    assert graph["index_manifest_sha256"] == t5_corpus_graph.MANIFEST_SHA256
    assert graph["gzip_sha256"] == t5_corpus_graph.GZIP_SHA256
    papers = [row for row in graph["nodes"] if row["node_type"] == "paper"]
    chunks = [row for row in graph["nodes"] if row["node_type"] == "chunk"]
    paper_keys = {row["canonical_key"] for row in papers}
    chunk_keys = {row["canonical_key"] for row in chunks}
    gold_indexed = t5_corpus_graph._indexed_paper_shas(gold)
    census_indexed = t5_corpus_graph._census_indexed_shas(census)
    index_set = set(manifest["indexed_paper_sha256"])
    store_ids = {str(row["chunk_id"]) for row in store["chunks"]}
    assert paper_keys == gold_indexed == census_indexed == index_set
    assert len(paper_keys) == 19
    assert chunk_keys == store_ids
    assert len(chunk_keys) == 922
    assert graph["n_paper_nodes"] == 19
    assert graph["n_chunk_nodes"] == 922
    assert graph["n_edges"] == 922
    assert graph["n_edges"] == graph["n_chunk_nodes"]
    assert [row["node_id"] for row in graph["nodes"]] == sorted(row["node_id"] for row in graph["nodes"])
    assert [row["edge_id"] for row in graph["edges"]] == sorted(row["edge_id"] for row in graph["edges"])
    by_chunk = {str(row["chunk_id"]): row for row in store["chunks"]}
    for node in chunks:
        payload = node["payload"]
        store_row = by_chunk[payload["chunk_id"]]
        assert list(payload) == list(t5_corpus_graph.CHUNK_PAYLOAD_KEYS)
        for key in t5_corpus_graph.CHUNK_PAYLOAD_KEYS:
            if key == "block_ids":
                assert payload[key] == [str(item) for item in store_row[key]]
            elif key in {"ordinal", "char_start", "char_end"}:
                assert payload[key] == int(store_row[key])
            else:
                assert payload[key] == store_row[key]
        assert set(payload) == set(t5_corpus_graph.CHUNK_PAYLOAD_KEYS)
        paper_sha = payload["paper_sha256"]
        assert node["node_id"] == f"chunk:{payload['chunk_id']}"
        assert node["canonical_key"] == payload["chunk_id"]
        assert list(node["payload"]) == list(t5_corpus_graph.CHUNK_PAYLOAD_KEYS)
        assert any(
            edge["edge_type"] == "paper_has_chunk"
            and edge["source_node_id"] == f"paper:{paper_sha}"
            and edge["target_node_id"] == node["node_id"]
            and edge["edge_id"] == f"paper_has_chunk:{paper_sha}:{payload['chunk_id']}"
            for edge in graph["edges"]
        )
    assert {row["edge_type"] for row in graph["edges"]} == {"paper_has_chunk"}
    assert {row["node_type"] for row in graph["nodes"]} == {"paper", "chunk"}
    for paper in papers:
        assert paper["payload"] == {"paper_sha256": paper["canonical_key"]}
        assert paper["node_id"] == f"paper:{paper['canonical_key']}"
    found: set[str] = set()
    _walk_keys(graph, found)
    assert FORBIDDEN.isdisjoint(found)
    status = t5_corpus_graph.inspect_t5_corpus_graph()
    assert status["exists"] is True
    assert status["counts"]["n_paper_nodes"] == 19
    assert status["counts"]["n_chunk_nodes"] == 922
    assert status["counts"]["n_edges"] == 922
    assert status["store_sha256"] == t5_corpus_graph.STORE_SHA256
    assert status["spec_sha256"] == t5_corpus_graph.SPEC_SHA256
    _assert_pins_unmoved()


def test_growth_counterfactual_survivors_ident(tmp_path):
    _assert_pins_unmoved()
    store = json.loads(STORE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dropped = sorted(str(sha) for sha in manifest["indexed_paper_sha256"])[0]
    reduced_chunks = [
        row for row in store["chunks"] if str(row["paper_sha256"]) != dropped
    ]
    reduced = dict(store)
    reduced["chunks"] = reduced_chunks
    reduced["n_chunks"] = len(reduced_chunks)
    reduced["indexed_paper_sha256"] = sorted(
        {str(row["paper_sha256"]) for row in reduced_chunks}
    )
    reduced_path = tmp_path / "store-reduced.json"
    full_path = tmp_path / "store-full.json"
    reduced_path.write_text(json.dumps(reduced, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full_path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dest_a = tmp_path / "graph-a.json"
    dest_b = tmp_path / "graph-b.json"
    t5_corpus_graph.emit_t5_corpus_graph(dest=dest_a, store_path=reduced_path)
    t5_corpus_graph.emit_t5_corpus_graph(dest=dest_b, store_path=full_path)
    graph_a = json.loads(dest_a.read_text(encoding="utf-8"))
    graph_b = json.loads(dest_b.read_text(encoding="utf-8"))
    nodes_b = {row["node_id"]: row for row in graph_b["nodes"]}
    edges_b = {row["edge_id"]: row for row in graph_b["edges"]}
    for node in graph_a["nodes"]:
        assert nodes_b[node["node_id"]] == node
    for edge in graph_a["edges"]:
        assert edges_b[edge["edge_id"]] == edge
    extra_nodes = {row["node_id"] for row in graph_b["nodes"]} - {row["node_id"] for row in graph_a["nodes"]}
    extra_edges = {row["edge_id"] for row in graph_b["edges"]} - {row["edge_id"] for row in graph_a["edges"]}
    assert f"paper:{dropped}" in extra_nodes
    assert extra_nodes
    assert extra_edges
    assert graph_b["n_paper_nodes"] == graph_a["n_paper_nodes"] + 1
    _assert_pins_unmoved()


def test_agent_list_and_registry_unmoved():
    names = _literature_names()
    assert names == set()
    assert LITERATURE_INGEST_TOOLS.isdisjoint(offered_tool_names())
    corpus = {"literature_mode": {"mode": "corpus"}}
    scholarly = {"literature_mode": {"mode": "scholarly"}}
    assert _literature_names(corpus) == set(LITERATURE_CORPUS_TOOLS)
    assert _literature_names(scholarly) == set(LITERATURE_SCHOLARLY_TOOLS)
    for mode in (None, corpus, scholarly, {"literature_mode": {"mode": "off"}}):
        offered = {item["name"] for item in tool_schemas(mode)}
        assert LITERATURE_INGEST_TOOLS.isdisjoint(offered)
        assert "emit_t5_corpus_graph" not in offered
        assert "inspect_t5_corpus_graph" not in offered
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert "emit_t5_corpus_graph" not in registry.BY_NAME
    assert "inspect_t5_corpus_graph" not in registry.BY_NAME
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    thermo = file_sha256(_ROOT / "src" / "dissolve" / "thermo.py")
    leftover = file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py")
    assert thermo == "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    assert leftover == "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
