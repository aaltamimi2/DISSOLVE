"""RTI-1 promote into sidecar I. Fixtures. No MiniLM. No gold needles in asserts."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src"), str(_ROOT / "tests")):
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
from dissolve import dense_d2, engine_e2e, engine_e2e5, registry, research, t5_corpus_graph, t5_promote
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.contracts import parse_tool_result
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR, TextGoldError
import test_engine_e2e5 as e2e5

NEW_PLANT = e2e5.NEW_PLANT
_boom = e2e5._boom
_canonical = e2e5._canonical
_census = e2e5._census
_fake_embedder = e2e5._fake_embedder
_gold = e2e5._gold
_store = e2e5._store
_write_pdf = e2e5._write_pdf

GOLD = t5_corpus_graph.GOLD_PATH
CENSUS = t5_corpus_graph.CENSUS_PATH
STORE = t5_corpus_graph.STORE_PATH
MANIFEST = t5_corpus_graph.MANIFEST_PATH
GRAPH = t5_corpus_graph.GRAPH_PATH
GZIP = dense_d2.INDEX_GZIP_PATH
MODULE = Path(t5_promote.__file__).read_text(encoding="utf-8")
RESEARCH_SRC = Path(research.__file__).read_text(encoding="utf-8")
PIN_BEFORE = {
    GOLD: t5_corpus_graph.GOLD_SHA256,
    CENSUS: t5_corpus_graph.CENSUS_SHA256,
    STORE: t5_corpus_graph.STORE_SHA256,
    MANIFEST: t5_corpus_graph.MANIFEST_SHA256,
    GZIP: t5_corpus_graph.GZIP_SHA256,
    GRAPH: "3e7914dc0fe42b7e3a779e9a24ce2942b91f807645f74dee2e62197bebaf0c25",
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": "0ee86b2682fa2aad8dee0c686fe6ae3cf565af872834c0b7626e9effd888a346",
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": "1a946df2e556e34e5c78491c99c208f20aec6ba674aa4b887f359947b226a398",
    DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json": "2b5be504c4fac12af07b1cd366d2b290dedad1e8efbf52f2218d0e649c7c6718",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json": "8090143a69106ff5ba5db8bab70f7fbc7980fe4f54574d427c7815dca7379152",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json": "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2",
    DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json": "1def352d26baa5bce481f8c5ca6b4b81ac41186471007b2976ee133b675610ad",
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
    assert "promoted" not in json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert not (DEFAULT_OUT_DIR / "GOLD.v2.json").is_file()
    assert not Path("/home/aaltamimi2/dissolve-v12-audit/corpus/GOLD.v2.json").is_file()


def _ingest_constructed(dest: Path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    seen: list[str] = []
    pdf = _write_pdf(dest / "paper.pdf", "rti-one")
    sha = engine_e2e5.paper_bytes_sha256(pdf)
    result = engine_e2e5.ingest_one_paper(
        pdf,
        gold=_gold(),
        census=_census(),
        store=_store(),
        dest_store=dest / engine_e2e5.INCREMENTAL_STORE_PATH.name,
        dest_census=dest / engine_e2e5.INCREMENTAL_CENSUS_PATH.name,
        canonical=_canonical(sha, NEW_PLANT),
        embedder=_fake_embedder(seen),
    )
    return sha, seen, result


def test_module_keep_out_has_no_fetch_muse_or_ingest_join():
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
    assert "0.35126980440608535" not in MODULE
    assert "0.35126980440608535" not in RESEARCH_SRC


def test_ingested_missing_does_not_create_dest(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    dest = tmp_path / "rti"
    dest.mkdir()
    result = t5_promote.promote_ingested_paper(paper_sha256="00" * 32, dest_dir=dest)
    assert result["status"] == "ingested_missing"
    assert result["chunks_added"] == 0
    assert not (dest / t5_promote.SIDECAR_STORE_NAME).exists()
    assert not (dest / t5_promote.PRODUCT_INDEX_NAME).exists()
    _assert_pins_unmoved()


def test_url_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    dest = tmp_path / "rti"
    dest.mkdir()
    with pytest.raises(TextGoldError) as caught:
        t5_promote.promote_ingested_paper(
            paper_sha256="https://example.invalid/paper.pdf",
            dest_dir=dest,
        )
    assert caught.value.code == "url_fetch_refused"
    assert not (dest / t5_promote.SIDECAR_STORE_NAME).exists()
    _assert_pins_unmoved()


def test_held_out_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    dest = tmp_path / "rti"
    dest.mkdir()
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    held = next(
        str(row["paper_sha256"])
        for row in (gold.get("papers") or [])
        if str(row.get("paper_status") or "") == "held_out" and row.get("paper_sha256")
    )
    census = {
        "papers": [{"filename": "held.pdf", "sha256": held, "status": engine_e2e5.INGESTED_STATUS}],
        "counts_by_status": {engine_e2e5.INGESTED_STATUS: 1},
    }
    (dest / engine_e2e5.INCREMENTAL_CENSUS_PATH.name).write_text(
        json.dumps(census, indent=2) + "\n", encoding="utf-8",
    )
    with pytest.raises(TextGoldError) as caught:
        t5_promote.promote_ingested_paper(paper_sha256=held, dest_dir=dest)
    assert caught.value.code == "held_out_in_store"
    assert not (dest / t5_promote.SIDECAR_STORE_NAME).exists()
    _assert_pins_unmoved()


def test_promote_sidecar_union_search_graph_and_holdout(tmp_path, monkeypatch):
    dest = tmp_path / "rti"
    dest.mkdir()
    sha, seen, ingested = _ingest_constructed(dest, monkeypatch)
    assert ingested["census_status"] == engine_e2e5.INGESTED_STATUS
    first = t5_promote.promote_ingested_paper(
        paper_sha256=sha, dest_dir=dest, embedder=_fake_embedder(seen),
    )
    assert first["status"] == "ok"
    assert first["chunks_added"] > 0
    assert first["vectors_embedded"] == 0
    sidecar_store = dest / t5_promote.SIDECAR_STORE_NAME
    sidecar_gzip = dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME
    dest_index = dest / t5_promote.PRODUCT_INDEX_NAME
    dest_graph = dest / t5_corpus_graph.GRAPH_NAME
    curves = dest / t5_promote.PROMOTE_CURVES_NAME
    assert sidecar_store.is_file()
    assert sidecar_gzip.is_file()
    assert dest_index.is_file()
    assert dest_graph.is_file()
    assert curves.is_file()
    store = json.loads(sidecar_store.read_text(encoding="utf-8"))
    product_ids = {str(row["chunk_id"]) for row in json.loads(STORE.read_text(encoding="utf-8"))["chunks"]}
    sidecar_ids = {str(row["chunk_id"]) for row in store["chunks"]}
    assert sidecar_ids
    assert sidecar_ids.isdisjoint(product_ids)
    assert len(product_ids) == 922
    with gzip.open(GZIP, "rt", encoding="utf-8") as handle:
        product_index = json.load(handle)
    assert len(product_index["dense"]["chunk_ids"]) == 922
    assert file_sha256(GZIP) == t5_corpus_graph.GZIP_SHA256
    census = json.loads((dest / engine_e2e5.INCREMENTAL_CENSUS_PATH.name).read_text(encoding="utf-8"))
    assert engine_e2e5.census_status_for(census, sha) == "indexed"
    manifest = json.loads(dest_index.read_text(encoding="utf-8"))
    artifact = json.loads(curves.read_text(encoding="utf-8"))
    assert manifest["n_chunks"] == 922
    assert len(manifest["indexed_paper_sha256"]) == 19
    assert manifest["promoted"]["knowledgebase"] == t5_promote.SIDECAR_KNOWLEDGEBASE
    assert manifest["promoted"]["n_chunks"] == len(sidecar_ids)
    assert manifest["abstention"]["statistic"] == "query_idf_coverage"
    assert manifest["abstention"]["percentile"] == 5
    assert manifest["abstention"]["floor"] == artifact["shipped_floor"]
    digest = file_sha256(sidecar_store)
    gzip_digest = file_sha256(sidecar_gzip)
    second = t5_promote.promote_ingested_paper(
        paper_sha256=sha, dest_dir=dest, embedder=_fake_embedder(seen),
    )
    assert second["status"] == "noop"
    assert second["chunks_added"] == 0
    assert second["vectors_embedded"] == 0
    assert file_sha256(sidecar_store) == digest
    assert file_sha256(sidecar_gzip) == gzip_digest

    monkeypatch.setattr(research, "_product_manifest_path", lambda: dest_index)
    payload = parse_tool_result(
        research.search_literature_corpus(NEW_PLANT, retrieval_mode="sparse", top_k=5),
    )
    assert payload["data"]["success"] is True
    served = [str(row["chunk_id"]) for row in payload["data"]["results"]]
    assert served
    assert set(served).issubset(sidecar_ids)
    assert set(served).isdisjoint(product_ids)
    empty = parse_tool_result(
        research.search_literature_corpus(
            engine_e2e.NONSENSE_QUERY, retrieval_mode="sparse", top_k=5,
        ),
    )
    assert empty["data"]["success"] is True
    assert empty["data"]["results"] == []
    union = research._load_index("t5-indexed-unsealed")
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    leaks = engine_e2e5.holdout_n_leaks(union, gold)
    assert leaks["n_held_out_facts"] == 29
    assert leaks["no_leaks_at_every_k"] is True

    base_dir = tmp_path / "base-graph"
    base_dir.mkdir()
    t5_corpus_graph.emit_t5_corpus_graph(dest=base_dir / "graph.json")
    base = json.loads((base_dir / "graph.json").read_text(encoding="utf-8"))
    union_graph = json.loads(dest_graph.read_text(encoding="utf-8"))
    nodes_u = {row["node_id"]: row for row in union_graph["nodes"]}
    edges_u = {row["edge_id"]: row for row in union_graph["edges"]}
    for node in base["nodes"]:
        assert nodes_u[node["node_id"]] == node
    for edge in base["edges"]:
        assert edges_u[edge["edge_id"]] == edge
    assert union_graph["store_sha256"] == t5_corpus_graph.STORE_SHA256
    assert union_graph["gzip_sha256"] == t5_corpus_graph.GZIP_SHA256
    assert union_graph["n_paper_nodes"] == 19 + 1
    assert union_graph["n_chunk_nodes"] == 922 + len(sidecar_ids)
    assert f"paper:{sha}" in nodes_u
    found: set[str] = set()
    _walk_keys(union_graph, found)
    assert t5_corpus_graph.FORBIDDEN_KEYS.isdisjoint(found)
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
        assert "promote_ingested_paper" not in offered
        assert "emit_t5_corpus_graph" not in offered
        assert "inspect_t5_corpus_graph" not in offered
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert "promote_ingested_paper" not in registry.BY_NAME
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    thermo = file_sha256(_ROOT / "src" / "dissolve" / "thermo.py")
    leftover = file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py")
    assert thermo == "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    assert leftover == "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
    _assert_pins_unmoved()


def test_emptying_ingest_tools_does_not_put_ingest_on_scholarly(monkeypatch):
    import agent_tools as tools
    monkeypatch.setattr(tools, "LITERATURE_INGEST_TOOLS", frozenset())
    scholarly = {"literature_mode": {"mode": "scholarly"}}
    offered = {item["name"] for item in tools.tool_schemas(scholarly)}
    assert "ingest_literature_documents" not in offered
    assert "ingest_literature_graph" not in offered
    assert "promote_ingested_paper" not in offered
    assert tools.LITERATURE_MODE_SURFACE["scholarly"] == tools.LITERATURE_SCHOLARLY_TOOLS
