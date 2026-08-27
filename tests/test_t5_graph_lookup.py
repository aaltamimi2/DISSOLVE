"""G-2-join GRAPH → store passage. No MiniLM. No gold needles. No paper-hash literals."""
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
from dissolve import registry, research, t5_corpus_graph, t5_graph_lookup
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR, TextGoldError

GRAPH = t5_corpus_graph.GRAPH_PATH
STORE = t5_corpus_graph.STORE_PATH
MANIFEST = t5_corpus_graph.MANIFEST_PATH
GOLD = t5_corpus_graph.GOLD_PATH
CENSUS = t5_corpus_graph.CENSUS_PATH
GZIP = Path(json.loads(MANIFEST.read_text(encoding="utf-8"))["index_path"])
MODULE = Path(t5_graph_lookup.__file__).read_text(encoding="utf-8")
PIN_BEFORE = {
    GOLD: t5_corpus_graph.GOLD_SHA256,
    CENSUS: t5_corpus_graph.CENSUS_SHA256,
    STORE: t5_corpus_graph.STORE_SHA256,
    MANIFEST: t5_corpus_graph.MANIFEST_SHA256,
    GZIP: t5_corpus_graph.GZIP_SHA256,
    GRAPH: t5_graph_lookup.GRAPH_SHA256,
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": "0ee86b2682fa2aad8dee0c686fe6ae3cf565af872834c0b7626e9effd888a346",
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": "1a946df2e556e34e5c78491c99c208f20aec6ba674aa4b887f359947b226a398",
    DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json": "2b5be504c4fac12af07b1cd366d2b290dedad1e8efbf52f2218d0e649c7c6718",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json": "8090143a69106ff5ba5db8bab70f7fbc7980fe4f54574d427c7815dca7379152",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json": "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2",
    DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json": "1def352d26baa5bce481f8c5ca6b4b81ac41186471007b2976ee133b675610ad",
}
LOOKUP_TOKEN = "LOOKUPZXQTOKEN"


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
    assert not (DEFAULT_OUT_DIR / "GOLD.v2.json").is_file()
    assert not Path("/home/aaltamimi2/dissolve-v12-audit/corpus/GOLD.v2.json").is_file()


def _product_store() -> dict:
    return json.loads(STORE.read_text(encoding="utf-8"))


def test_module_keep_out_has_no_muse_fetch_or_ingest_join():
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
    assert "ParameterRecord" not in MODULE
    assert "0.35126980440608535" not in Path(research.__file__).read_text(encoding="utf-8")


def test_missing_graph_does_not_create_dest(tmp_path):
    missing = tmp_path / "absent-graph.json"
    payload = t5_graph_lookup.resolve_t5_graph_passage(chunk_id="T5-absent-0001", dest=missing)
    assert payload["exists"] is False
    assert not missing.exists()
    _assert_pins_unmoved()


def test_unknown_chunk_exists_false_and_graph_unmoved():
    before = file_sha256(GRAPH)
    payload = t5_graph_lookup.resolve_t5_graph_passage(chunk_id="T5-absent-0001")
    assert payload["exists"] is False
    assert file_sha256(GRAPH) == before == t5_graph_lookup.GRAPH_SHA256
    _assert_pins_unmoved()


def test_url_refused():
    with pytest.raises(TextGoldError) as caught:
        t5_graph_lookup.resolve_t5_graph_passage(chunk_id="https://example.invalid/paper.pdf")
    assert caught.value.code == "url_fetch_refused"
    _assert_pins_unmoved()


def test_product_chunk_join_is_store_body_and_graph_read_only():
    store = _product_store()
    row = store["chunks"][0]
    chunk_id = str(row["chunk_id"])
    before = GRAPH.read_bytes()
    got = t5_graph_lookup.resolve_t5_graph_passage(chunk_id=chunk_id)
    again = t5_graph_lookup.resolve_t5_graph_passage(node_id=f"chunk:{chunk_id}")
    assert got == again
    assert got["exists"] is True
    assert got["chunk_id"] == chunk_id
    assert got["body"] == row["body"]
    coords = t5_corpus_graph._chunk_payload(row)
    for key, value in coords.items():
        assert got[key] == value
    if "body_plus_rebound" in row and str(row["body_plus_rebound"]) != str(row["body"]):
        assert got["body_plus_rebound"] == row["body_plus_rebound"]
    else:
        assert "body_plus_rebound" not in got
    assert GRAPH.read_bytes() == before
    assert file_sha256(GRAPH) == t5_graph_lookup.GRAPH_SHA256
    rebound = next(
        (item for item in store["chunks"] if item.get("body_plus_rebound") and item["body_plus_rebound"] != item.get("body")),
        None,
    )
    if rebound is not None:
        joined = t5_graph_lookup.resolve_t5_graph_passage(chunk_id=str(rebound["chunk_id"]))
        assert joined["body_plus_rebound"] == rebound["body_plus_rebound"]
    found: set[str] = set()
    _walk_keys(json.loads(GRAPH.read_text(encoding="utf-8")), found)
    assert t5_corpus_graph.FORBIDDEN_KEYS.isdisjoint(found)
    _assert_pins_unmoved()


def test_paper_node_is_not_a_chunk():
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    paper = next(node for node in graph["nodes"] if node["node_type"] == "paper")
    with pytest.raises(TextGoldError) as caught:
        t5_graph_lookup.resolve_t5_graph_passage(node_id=str(paper["node_id"]))
    assert caught.value.code == "not_a_chunk"
    _assert_pins_unmoved()


def test_tmp_union_sidecar_and_survivor_join(tmp_path):
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
            "body": f"{LOOKUP_TOKEN} lives in one sidecar block.",
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
    dest = tmp_path / t5_corpus_graph.GRAPH_NAME
    t5_corpus_graph.emit_t5_corpus_graph(
        dest=dest,
        store_path=STORE,
        manifest_path=MANIFEST,
        sidecar_store_path=sidecar_path,
    )
    assert dest.resolve() != GRAPH.resolve()
    sidecar_got = t5_graph_lookup.resolve_t5_graph_passage(
        chunk_id=sidecar_id, dest=dest, store_path=STORE,
    )
    assert sidecar_got["exists"] is True
    assert sidecar_got["chunk_id"] == sidecar_id
    assert LOOKUP_TOKEN in sidecar_got["body"]
    survivor_got = t5_graph_lookup.resolve_t5_graph_passage(
        chunk_id=str(survivor["chunk_id"]), dest=dest, store_path=STORE,
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
        assert "resolve_t5_graph_passage" not in offered
        assert "promote_ingested_paper" not in offered
        assert "emit_t5_corpus_graph" not in offered
        assert "inspect_t5_corpus_graph" not in offered
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert "resolve_t5_graph_passage" not in registry.BY_NAME
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    thermo = file_sha256(_ROOT / "src" / "dissolve" / "thermo.py")
    leftover = file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py")
    assert thermo == "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    assert leftover == "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
    _assert_pins_unmoved()
