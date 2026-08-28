"""G-2-typed ParameterRecord overlay. No MiniLM. No gold needles. No paper-hash literals."""
from __future__ import annotations

import json
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
from dissolve import registry, research, t5_corpus_graph, t5_graph_lookup, t5_graph_records
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR, TextGoldError

GRAPH = t5_corpus_graph.GRAPH_PATH
STORE = t5_corpus_graph.STORE_PATH
MANIFEST = t5_corpus_graph.MANIFEST_PATH
GOLD = t5_corpus_graph.GOLD_PATH
CENSUS = t5_corpus_graph.CENSUS_PATH
GZIP = Path(json.loads(MANIFEST.read_text(encoding="utf-8"))["index_path"])
MODULE = Path(t5_graph_records.__file__).read_text(encoding="utf-8")
PIN_BEFORE = {
    GOLD: t5_corpus_graph.GOLD_SHA256,
    CENSUS: t5_corpus_graph.CENSUS_SHA256,
    STORE: t5_corpus_graph.STORE_SHA256,
    MANIFEST: t5_corpus_graph.MANIFEST_SHA256,
    GZIP: t5_corpus_graph.GZIP_SHA256,
    GRAPH: t5_graph_records.GRAPH_SHA256,
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": "0ee86b2682fa2aad8dee0c686fe6ae3cf565af872834c0b7626e9effd888a346",
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": "1a946df2e556e34e5c78491c99c208f20aec6ba674aa4b887f359947b226a398",
    DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json": "2b5be504c4fac12af07b1cd366d2b290dedad1e8efbf52f2218d0e649c7c6718",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json": "8090143a69106ff5ba5db8bab70f7fbc7980fe4f54574d427c7815dca7379152",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json": "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2",
    DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json": "1def352d26baa5bce481f8c5ca6b4b81ac41186471007b2976ee133b675610ad",
}
REC_TOKEN = "LOOKRECZXQ"
SIDECAR_TOKEN = "LOOKUPZXQTOKEN"


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
    assert not Path("/home/aaltamimi2/dissolve-v12-audit/corpus/GOLD.v2.json").is_file()


def _product_store() -> dict:
    return json.loads(STORE.read_text(encoding="utf-8"))


def _chi_record(row: dict, *, record_id: str = "T5-rec-looktest-0001") -> dict:
    return {
        "record_id": record_id,
        "record_class": "ChiParameter",
        "chunk_id": str(row["chunk_id"]),
        "paper_sha256": str(row["paper_sha256"]),
        "char_start": int(row["char_start"]),
        "char_end": int(row["char_end"]),
        "component_a": f"Polymer{REC_TOKEN}",
        "component_b": f"Solvent{REC_TOKEN}",
        "chi": 0.42,
    }


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
    assert overlay["schema"] == source["schema"]
    assert overlay["spec_sha256"] == source["spec_sha256"]
    assert overlay["knowledgebase"] == source["knowledgebase"]
    assert overlay["store_sha256"] == source["store_sha256"]
    assert overlay["gzip_sha256"] == source["gzip_sha256"]


def test_module_keep_out_has_no_muse_fetch_gold_or_ingest_join():
    lowered = MODULE.casefold()
    assert "urllib" not in lowered
    assert "requests" not in lowered
    assert "sentence_transformers" not in lowered
    assert "literature_ingest" not in MODULE
    assert "ingest_literature_graph" not in MODULE
    assert "merge_literature_graph" not in MODULE
    assert "literature_graph_path" not in MODULE
    assert "duckdb" not in lowered
    assert "scholarly |=" not in MODULE
    assert "mint_one_paper" not in MODULE
    assert "GOLD.text.v1.unsealed.json" not in MODULE
    assert "0.35126980440608535" not in Path(research.__file__).read_text(encoding="utf-8")


def test_hang_persist_graph_is_refused():
    row = _product_store()["chunks"][0]
    with pytest.raises(TextGoldError) as caught:
        t5_graph_records.hang_t5_parameter_records(
            records=[_chi_record(row)],
            dest=GRAPH,
        )
    assert caught.value.code == "persist_graph_refused"
    _assert_pins_unmoved()


def test_unknown_record_and_persist_dest_exist_false():
    before = GRAPH.read_bytes()
    missing = t5_graph_records.resolve_t5_graph_record(record_id="T5-rec-absent-0001")
    assert missing["exists"] is False
    assert GRAPH.read_bytes() == before
    assert file_sha256(GRAPH) == t5_graph_records.GRAPH_SHA256
    _assert_pins_unmoved()


def test_paper_and_chunk_node_are_not_a_record():
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    paper = next(node for node in graph["nodes"] if node["node_type"] == "paper")
    chunk = next(node for node in graph["nodes"] if node["node_type"] == "chunk")
    with pytest.raises(TextGoldError) as caught:
        t5_graph_records.resolve_t5_graph_record(node_id=str(paper["node_id"]))
    assert caught.value.code == "not_a_record"
    with pytest.raises(TextGoldError) as caught:
        t5_graph_records.resolve_t5_graph_record(node_id=str(chunk["node_id"]))
    assert caught.value.code == "not_a_record"
    _assert_pins_unmoved()


def test_hang_chi_on_tmp_overlay_and_resolve_store_body(tmp_path):
    store = _product_store()
    row = store["chunks"][0]
    record = _chi_record(row)
    dest = tmp_path / "overlay-graph.json"
    before = GRAPH.read_bytes()
    source = json.loads(before.decode("utf-8"))
    hung = t5_graph_records.hang_t5_parameter_records(records=[record], dest=dest)
    assert dest.resolve() != GRAPH.resolve()
    assert hung["n_record_nodes"] == 1
    assert hung["n_reports_edges"] == 1
    overlay = json.loads(dest.read_text(encoding="utf-8"))
    _g1_survivors(source, overlay)
    records = overlay["records"]
    assert records["n_record_nodes"] == 1
    assert records["n_reports_edges"] == 1
    assert records["source_graph_sha256"] == t5_graph_records.GRAPH_SHA256
    record_nodes = [node for node in overlay["nodes"] if node["node_type"] == "ParameterRecord"]
    report_edges = [edge for edge in overlay["edges"] if edge["edge_type"] == "REPORTS"]
    assert len(record_nodes) == 1
    assert len(report_edges) == 1
    assert record_nodes[0]["node_id"] == f"record:{record['record_id']}"
    assert report_edges[0]["source_node_id"] == f"paper:{row['paper_sha256']}"
    found: set[str] = set()
    _walk_keys(overlay, found)
    assert t5_corpus_graph.FORBIDDEN_KEYS.isdisjoint(found)
    got = t5_graph_records.resolve_t5_graph_record(record_id=record["record_id"], dest=dest)
    again = t5_graph_records.resolve_t5_graph_record(
        node_id=f"record:{record['record_id']}", dest=dest,
    )
    assert got == again
    assert got["exists"] is True
    assert got["record_id"] == record["record_id"]
    assert got["record_class"] == "ChiParameter"
    assert got["chunk_id"] == row["chunk_id"]
    assert got["char_start"] == record["char_start"]
    assert got["char_end"] == record["char_end"]
    assert got["body"] == row["body"]
    assert got["component_a"] == record["component_a"]
    assert REC_TOKEN in got["component_a"]
    assert GRAPH.read_bytes() == before
    again_hang = t5_graph_records.hang_t5_parameter_records(records=[record], dest=dest)
    assert again_hang["dest_sha256"] == hung["dest_sha256"] == file_sha256(dest)
    _assert_pins_unmoved()


def test_url_refused():
    with pytest.raises(TextGoldError) as caught:
        t5_graph_records.resolve_t5_graph_record(record_id="https://example.invalid/record")
    assert caught.value.code == "url_fetch_refused"
    _assert_pins_unmoved()


def test_tmp_union_sidecar_and_survivor_records(tmp_path):
    store = _product_store()
    survivor = store["chunks"][0]
    sidecar_sha = "cc" * 32
    sidecar_id = "T5-looktest-0001"
    sidecar = {
        "schema": "dissolve.t5-chunk-store.unsealed.v1",
        "n_chunks": 1,
        "indexed_paper_sha256": [sidecar_sha],
        "chunks": [{
            "chunk_id": sidecar_id,
            "paper_sha256": sidecar_sha,
            "ordinal": 1,
            "body": f"{SIDECAR_TOKEN} lives in one sidecar block.",
            "char_start": 0,
            "char_end": 40,
            "page": 1,
            "section": "Methods",
            "section_origin": "parser_supplied",
            "kind": "paragraph",
            "block_ids": ["b-lookup"],
        }],
    }
    sidecar_path = tmp_path / t5_corpus_graph.SIDECAR_STORE_NAME
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    union = tmp_path / t5_corpus_graph.GRAPH_NAME
    t5_corpus_graph.emit_t5_corpus_graph(
        dest=union,
        store_path=STORE,
        manifest_path=MANIFEST,
        sidecar_store_path=sidecar_path,
    )
    overlay = tmp_path / "overlay-graph.json"
    sidecar_row = sidecar["chunks"][0]
    hung = t5_graph_records.hang_t5_parameter_records(
        records=[
            _chi_record(survivor, record_id="T5-rec-surv-0001"),
            _chi_record(sidecar_row, record_id="T5-rec-side-0001"),
        ],
        dest=overlay,
        graph_path=union,
    )
    assert hung["n_record_nodes"] == 2
    sidecar_got = t5_graph_records.resolve_t5_graph_record(
        record_id="T5-rec-side-0001", dest=overlay, store_path=STORE,
    )
    assert sidecar_got["exists"] is True
    assert sidecar_got["chunk_id"] == sidecar_id
    assert SIDECAR_TOKEN in sidecar_got["body"]
    survivor_got = t5_graph_records.resolve_t5_graph_record(
        record_id="T5-rec-surv-0001", dest=overlay, store_path=STORE,
    )
    assert survivor_got["exists"] is True
    assert survivor_got["body"] == survivor["body"]
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
        assert "hang_t5_parameter_records" not in offered
        assert "resolve_t5_graph_record" not in offered
        assert "resolve_t5_graph_passage" not in offered
        assert "promote_ingested_paper" not in offered
        assert "emit_t5_corpus_graph" not in offered
        assert "inspect_t5_corpus_graph" not in offered
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert "hang_t5_parameter_records" not in registry.BY_NAME
    assert "resolve_t5_graph_record" not in registry.BY_NAME
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    thermo = file_sha256(_ROOT / "src" / "dissolve" / "thermo.py")
    leftover = file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py")
    assert thermo == "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    assert leftover == "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
    _assert_pins_unmoved()
