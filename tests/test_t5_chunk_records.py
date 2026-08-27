"""G-2-reverse chunk → hung ParameterRecord. No MiniLM. No gold needles. No paper-hash literals."""
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
from dissolve import registry, research, t5_chunk_records, t5_corpus_graph, t5_graph_records
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR, TextGoldError

GRAPH = t5_corpus_graph.GRAPH_PATH
STORE = t5_corpus_graph.STORE_PATH
MANIFEST = t5_corpus_graph.MANIFEST_PATH
GOLD = t5_corpus_graph.GOLD_PATH
CENSUS = t5_corpus_graph.CENSUS_PATH
GZIP = Path(json.loads(MANIFEST.read_text(encoding="utf-8"))["index_path"])
MODULE = Path(t5_chunk_records.__file__).read_text(encoding="utf-8")
PIN_BEFORE = {
    GOLD: t5_corpus_graph.GOLD_SHA256,
    CENSUS: t5_corpus_graph.CENSUS_SHA256,
    STORE: t5_corpus_graph.STORE_SHA256,
    MANIFEST: t5_corpus_graph.MANIFEST_SHA256,
    GZIP: t5_corpus_graph.GZIP_SHA256,
    GRAPH: t5_chunk_records.GRAPH_SHA256,
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": "0ee86b2682fa2aad8dee0c686fe6ae3cf565af872834c0b7626e9effd888a346",
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": "1a946df2e556e34e5c78491c99c208f20aec6ba674aa4b887f359947b226a398",
    DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json": "2b5be504c4fac12af07b1cd366d2b290dedad1e8efbf52f2218d0e649c7c6718",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json": "8090143a69106ff5ba5db8bab70f7fbc7980fe4f54574d427c7815dca7379152",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json": "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2",
    DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json": "1def352d26baa5bce481f8c5ca6b4b81ac41186471007b2976ee133b675610ad",
}
REC_TOKEN = "LOOKRECZXQ"


def _literature_names(session=None) -> set[str]:
    return {item["name"] for item in tool_schemas(session)} & set(LITERATURE_AGENT_TOOLS)


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


def test_module_keep_out_has_no_muse_fetch_search_or_hang():
    lowered = MODULE.casefold()
    assert "urllib" not in lowered
    assert "requests" not in lowered
    assert "sentence_transformers" not in lowered
    assert "search_literature_corpus" not in MODULE
    assert "hang_t5_parameter_records" not in MODULE
    assert "ingest_literature_graph" not in MODULE
    assert "literature_graph_path" not in MODULE
    assert "duckdb" not in lowered
    assert "scholarly |=" not in MODULE
    assert "mint_one_paper" not in MODULE
    assert "GOLD.text.v1.unsealed.json" not in MODULE
    assert "0.35126980440608535" not in Path(research.__file__).read_text(encoding="utf-8")


def test_persist_product_chunk_has_empty_records():
    store = _product_store()
    row = store["chunks"][0]
    chunk_id = str(row["chunk_id"])
    before = GRAPH.read_bytes()
    got = t5_chunk_records.resolve_t5_chunk_records(chunk_id=chunk_id)
    again = t5_chunk_records.resolve_t5_chunk_records(node_id=f"chunk:{chunk_id}")
    assert got == again
    assert got["exists"] is True
    assert got["chunk_id"] == chunk_id
    assert got["records"] == []
    assert got["body"] == row["body"]
    assert GRAPH.read_bytes() == before
    assert file_sha256(GRAPH) == t5_chunk_records.GRAPH_SHA256
    _assert_pins_unmoved()


def test_unknown_chunk_exists_false():
    before = GRAPH.read_bytes()
    got = t5_chunk_records.resolve_t5_chunk_records(chunk_id="T5-absent-0001")
    assert got["exists"] is False
    assert GRAPH.read_bytes() == before
    _assert_pins_unmoved()


def test_paper_and_record_node_are_not_a_chunk():
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    paper = next(node for node in graph["nodes"] if node["node_type"] == "paper")
    with pytest.raises(TextGoldError) as caught:
        t5_chunk_records.resolve_t5_chunk_records(node_id=str(paper["node_id"]))
    assert caught.value.code == "not_a_chunk"
    with pytest.raises(TextGoldError) as caught:
        t5_chunk_records.resolve_t5_chunk_records(node_id="record:T5-rec-absent-0001")
    assert caught.value.code == "not_a_chunk"
    _assert_pins_unmoved()


def test_url_refused():
    with pytest.raises(TextGoldError) as caught:
        t5_chunk_records.resolve_t5_chunk_records(chunk_id="https://example.invalid/chunk")
    assert caught.value.code == "url_fetch_refused"
    _assert_pins_unmoved()


def test_overlay_returns_hung_record_and_other_chunk_empty(tmp_path):
    store = _product_store()
    row = store["chunks"][0]
    other = store["chunks"][1]
    record = _chi_record(row)
    dest = tmp_path / "overlay-graph.json"
    before = GRAPH.read_bytes()
    t5_graph_records.hang_t5_parameter_records(records=[record], dest=dest)
    hung_node = next(
        node
        for node in json.loads(dest.read_text(encoding="utf-8"))["nodes"]
        if node["node_type"] == "ParameterRecord"
    )
    got = t5_chunk_records.resolve_t5_chunk_records(chunk_id=str(row["chunk_id"]), dest=dest)
    assert got["exists"] is True
    assert got["body"] == row["body"]
    assert len(got["records"]) == 1
    payload = got["records"][0]
    assert payload == hung_node["payload"]
    assert "body" not in payload
    assert payload["record_id"] == record["record_id"]
    assert payload["component_a"] == record["component_a"]
    other_got = t5_chunk_records.resolve_t5_chunk_records(
        chunk_id=str(other["chunk_id"]), dest=dest,
    )
    assert other_got["exists"] is True
    assert other_got["records"] == []
    assert other_got["body"] == other["body"]
    assert GRAPH.read_bytes() == before
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
        assert "resolve_t5_chunk_records" not in offered
        assert "hang_t5_parameter_records" not in offered
        assert "resolve_t5_graph_record" not in offered
        assert "resolve_t5_graph_passage" not in offered
        assert "promote_ingested_paper" not in offered
        assert "emit_t5_corpus_graph" not in offered
        assert "inspect_t5_corpus_graph" not in offered
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert "resolve_t5_chunk_records" not in registry.BY_NAME
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    thermo = file_sha256(_ROOT / "src" / "dissolve" / "thermo.py")
    leftover = file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py")
    assert thermo == "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    assert leftover == "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
    _assert_pins_unmoved()
