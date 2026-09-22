"""A-2A4 coverage + P5 floor. Fixtures only. No MiniLM. No gold needles."""
from __future__ import annotations

import math
from pathlib import Path


from dissolve import research
from dissolve.contracts import parse_tool_result

PAPER = "aa" * 32


def _unit(first: float) -> list[float]:
    return [first] + [0.0] * 383


def _chunk(chunk_id: str, body: str, *, section: str = "methods") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "d1",
        "paper_sha256": PAPER,
        "title": "Fixture",
        "body": body,
        "text": body,
        "section": section,
        "section_origin": "parser_supplied",
        "char_start": 0,
        "char_end": len(body),
        "page": 1,
    }


def _index(chunks: list[dict]) -> dict:
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": research._PRODUCT_KNOWLEDGEBASE,
        "documents": [{"document_id": "d1", "sha256": PAPER, "title": "Fixture"}],
        "chunks": chunks,
        "dense": {
            "model": research._MINILM_MODEL_ID,
            "dim": 384,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [_unit(1.0) for _ in chunks],
            "refuse_rule": "sparse_gated",
        },
    }


def _fake_minilm():
    def fake(texts, model_name=None):
        return research._MINILM_MODEL_ID, [_unit(1.0) for _ in texts]

    return fake


def test_a2a4_idf_is_ident_bm25_formula():
    n_docs = 2
    df = {"zxqalpha": 1}
    got = research._idf_value("zxqalpha", n_docs, df)
    want = math.log(1.0 + (n_docs - 1 + 0.5) / (1 + 0.5))
    assert got == want
    missing = research._idf_value("zxqabsent", n_docs, df)
    assert missing == math.log(1.0 + (n_docs + 0.5) / 0.5)


def test_a2a4_coverage_star_is_max_over_sparse_gated():
    index = _index([
        _chunk("c-a", "zxqalpha zxqalpha methods"),
        _chunk("c-b", "zxqbeta other token"),
    ])
    n_docs, df, _tokensets = research._idf_maps(index["chunks"])
    paired = research.query_idf_coverage(
        "zxqalpha zxqbeta", "zxqalpha zxqalpha methods", n_docs=n_docs, document_frequency=df,
    )
    star = research.coverage_star(index, "zxqalpha zxqbeta")
    assert 0.0 < paired <= 1.0
    assert star >= paired
    rows = research._search_index(index, "zxqalpha", 3, "sparse")
    assert rows
    assert "query_idf_coverage" in rows[0]
    assert rows[0]["query_idf_coverage"] == 1.0


def test_a2a4_floor_refuses_before_minilm_in_all_three_modes(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("A-2A4 floor must refuse before MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    index = _index([
        _chunk("c-neural", "neural network solvent screening"),
        _chunk("c-other", "polymer solubility methods"),
    ])
    index["abstention"] = {
        "statistic": "query_idf_coverage",
        "percentile": 5,
        "floor": 1.1,
    }
    for mode in ("sparse", "dense", "hybrid"):
        assert research._search_index(index, "neural", 5, mode) == []
        assert research.coverage_star(index, "neural") < 1.1


def test_a2a4_no_floor_skips_minilm_on_invented_tokens(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("invented tokens must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    index = _index([_chunk("c-a", "alpha methods solvent")])
    assert research._search_index(index, "xylophone quokka zzzyx", 5, "hybrid") == []
    assert research.coverage_star(index, "xylophone quokka zzzyx") == 0.0


def test_a2a4_payload_coverage_star_on_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    index = _index([_chunk("c-a", "alpha methods solvent")])
    research._save_index(index)
    payload = parse_tool_result(research.search_literature_corpus("alpha", retrieval_mode="sparse"))
    assert payload["data"]["success"] is True
    assert "coverage_star" in payload["data"]
    assert "floor" in payload["data"]
    assert payload["data"]["results"][0]["query_idf_coverage"] >= 0.0
    assert "floor" in payload["data"]["results"][0]


def test_a2a4_payload_echoes_index_floor_on_every_result(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    index = _index([_chunk("c-a", "alpha methods solvent")])
    index["abstention"] = {
        "statistic": "query_idf_coverage",
        "percentile": 5,
        "floor": 0.1,
    }
    research._save_index(index)
    payload = parse_tool_result(research.search_literature_corpus("alpha", retrieval_mode="sparse"))
    assert payload["data"]["success"] is True
    assert payload["data"]["floor"] == 0.1
    assert payload["data"]["results"]
    for row in payload["data"]["results"]:
        assert row["floor"] == 0.1
        assert row["query_idf_coverage"] >= 0.1
