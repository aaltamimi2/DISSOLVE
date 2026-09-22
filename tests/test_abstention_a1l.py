"""A-1L: surface sparse_raw_score; live hybrid default finds T5. No MiniLM. No gold needles."""
from __future__ import annotations

import inspect
import json
from pathlib import Path


from dissolve import research
from dissolve.contracts import parse_tool_result

PAPER = "aa" * 32
IDENT_FIELDS = (
    "chunk_id", "sparse_score", "dense_score", "sparse_raw_score",
    "section_boost", "final_score",
)


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


def _projection(rows: list[dict]) -> list[dict]:
    return [{key: row[key] for key in IDENT_FIELDS} for row in rows]


def test_a1l_production_weights_ident():
    source = Path(research.__file__).read_text()
    assert "_HYBRID_DENSE_WEIGHT = 0.55\n" in source
    assert "_HYBRID_SPARSE_WEIGHT = 0.40\n" in source
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    assert "engine_e2e" not in source
    assert "low_retrieval_confidence=not rows or top_score < 0.15" in source
    assert "_ABSTENTION_FLOOR" not in source
    assert "_COVERAGE_FLOOR" not in source


def test_a1l_search_defaults_and_ingest_home():
    search = inspect.signature(research.search_literature_corpus)
    assert search.parameters["knowledgebase"].default == "t5-indexed-unsealed"
    assert search.parameters["retrieval_mode"].default == "hybrid"
    ingest = inspect.signature(research.ingest_literature_documents)
    assert ingest.parameters["knowledgebase"].default == "user-library"
    inspect_tool = inspect.signature(research.inspect_literature_corpus)
    assert inspect_tool.parameters["knowledgebase"].default == "user-library"


def test_a1l_unset_home_finds_canonical_gzip(monkeypatch, tmp_path):
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path))
    expected = tmp_path / "indexes" / "t5-indexed-unsealed.json.gz"
    expected.parent.mkdir()
    expected.write_bytes(b"")
    assert research._index_path("t5-indexed-unsealed") == expected


def test_a1l_env_set_index_path_ident(monkeypatch, tmp_path):
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    got = research._index_path("t5-indexed-unsealed")
    assert got == (tmp_path / "t5-indexed-unsealed.json.gz").resolve()
    assert got != research._corpus_dir() / "indexes" / "t5-indexed-unsealed.json.gz"


def test_a1l_two_serving_queries_share_unit_sparse_and_differ_in_raw():
    index = _index([
        _chunk("c-neural", "neural network solvent screening"),
        _chunk("c-polymer", "polymer " * 24 + "solubility"),
    ])
    neural = research._search_index(index, "neural", 3, "sparse")
    polymer = research._search_index(index, "polymer", 3, "sparse")
    assert neural and polymer
    assert neural[0]["sparse_score"] == 1.0
    assert polymer[0]["sparse_score"] == 1.0
    assert neural[0]["sparse_raw_score"] != polymer[0]["sparse_raw_score"]
    assert "sparse_raw_score" in neural[0]


def test_a1l_live_rows_ident_search_index(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    index = _index([
        _chunk("c-a", "alpha methods solvent"),
        _chunk("c-b", "beta other token"),
    ])
    research._save_index(index)
    query = "alpha"
    top_k = 3
    mode = "hybrid"
    direct = research._search_index(index, query, top_k, mode)
    payload = parse_tool_result(research.search_literature_corpus(query, top_k=top_k))
    assert payload["data"]["success"] is True
    assert payload["data"]["retrieval_mode"] == "hybrid"
    assert payload["data"]["knowledgebase"] == "t5-indexed-unsealed"
    live = payload["data"]["results"]
    assert _projection(live) == _projection(direct)
    assert [row["chunk_id"] for row in live] == [row["chunk_id"] for row in direct]


def test_a1l_sparse_mode_ident_and_refuse_skips_minilm(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("A-1L sparse refuse must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    index = _index([_chunk("c-a", "alpha methods solvent")])
    research._save_index(index)
    empty = parse_tool_result(research.search_literature_corpus(
        "xylophone quokka zzzyx", retrieval_mode="sparse",
    ))
    assert empty["data"]["success"] is True
    assert empty["data"]["result_count"] == 0
    assert empty["data"]["results"] == []
    direct = research._search_index(index, "xylophone quokka zzzyx", 5, "sparse")
    assert direct == []
    assert research._search_index(index, "xylophone quokka zzzyx", 5, "hybrid") == []


def test_a1l_save_does_not_clobber_canonical_gzip(monkeypatch, tmp_path):
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path))
    served = tmp_path / "indexes" / "t5-indexed-unsealed.json.gz"
    served.parent.mkdir()
    served.write_bytes(b"served")
    index = _index([_chunk("c-a", "alpha methods solvent")])
    try:
        research._save_index(index)
    except research.LiteratureContractError as error:
        assert error.code == "protected_serving_index"
    else:
        raise AssertionError("unset-home save must not overwrite the T5 gzip")
    assert served.read_bytes() == b"served"
