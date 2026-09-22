"""D-1 hybrid wiring. Constructed queries only. No gold rates."""
from __future__ import annotations

import json
from pathlib import Path


from dissolve import research

NONSENSE_QUERY = "ZXQQQNONSENSEZXQ TOKENTHATISABSENTQZX"
from dissolve.contracts import parse_tool_result

PLANT = "PLANTZXQTOKEN"
ABSENT_SUBJECT = "ZXQKRYPTONITE ZXUNOBTAINIUM ZXREGOLITHQ"
PAPER = "aa" * 32


def _unit(first: float) -> list[float]:
    return [first] + [0.0] * 383


def _index(*, chunk_ids: list[str] | None = None, dense_ids: list[str] | None = None) -> dict:
    ids = chunk_ids or ["c-0001", "c-0002"]
    recorded = dense_ids if dense_ids is not None else list(ids)
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": research._PRODUCT_KNOWLEDGEBASE,
        "documents": [{"document_id": "d1", "sha256": PAPER, "title": "Fixture"}],
        "chunks": [
            {
                "chunk_id": ids[0],
                "document_id": "d1",
                "paper_sha256": PAPER,
                "title": "Fixture",
                "body": f"alpha {PLANT} methods",
                "text": f"alpha {PLANT} methods",
                "section": "methods",
                "section_origin": "parser_supplied",
                "char_start": 0,
                "char_end": 20,
                "page": 1,
            },
            {
                "chunk_id": ids[1],
                "document_id": "d1",
                "paper_sha256": PAPER,
                "title": "Fixture",
                "body": "beta other token",
                "text": "beta other token",
                "section": "intro",
                "section_origin": "parser_supplied",
                "char_start": 20,
                "char_end": 36,
                "page": 1,
            },
        ],
        "dense": {
            "model": research._MINILM_MODEL_ID,
            "dim": 384,
            "chunk_ids": recorded,
            "vectors": [_unit(1.0), _unit(0.0)],
            "refuse_rule": "sparse_gated",
        },
    }


def _fake_minilm(seen, model_id=research._MINILM_MODEL_ID):
    def fake(texts, model_name=None):
        seen.extend(list(texts))
        return model_id, [_unit(1.0) for _ in texts]

    return fake


def test_d1_hybrid_does_not_raise_dense_unavailable(monkeypatch):
    seen = []
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm(seen))
    rows = research._search_index(_index(), PLANT, 5, "hybrid")
    assert rows
    assert rows[0]["chunk_id"] == "c-0001"
    assert seen == [PLANT]


def test_d1_chunk_id_set_identity_not_count(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm([]))
    broken = _index(dense_ids=["c-0001", "c-OTHER"])
    try:
        research._search_index(broken, PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("count-matched wrong chunk_id set must fail")


def test_d1_loaded_model_must_match_index(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm([], model_id="not-the-index-model"))
    try:
        research._search_index(_index(), PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("model id mismatch must fail")


def test_d1_served_components_sum_to_final(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm([]))
    rows = research._search_index(_index(), PLANT, 5, "hybrid")
    hit = rows[0]
    parts = hit["dense_score"] * 0.55 + hit["sparse_score"] * 0.40 + hit["section_boost"]
    assert abs(parts - hit["final_score"]) < 1e-6
    blob = json.dumps(rows)
    assert "fact_id" not in blob
    assert "recall" not in blob
    assert "needles" not in blob


def test_d1_constructed_refuse_does_not_load_minilm(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("refuse path must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    index = _index()
    assert research._search_index(index, NONSENSE_QUERY, 5, "hybrid") == []
    assert research._search_index(index, ABSENT_SUBJECT, 5, "hybrid") == []


def test_d1_tool_records_sparse_gated_refuse(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm([]))
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    research._save_index(_index())
    payload = parse_tool_result(research.search_literature_corpus(
        PLANT, knowledgebase=research._PRODUCT_KNOWLEDGEBASE, retrieval_mode="hybrid",
    ))
    assert payload["data"]["success"] is True
    assert payload["data"].get("error_code") != "dense_index_unavailable"
    assert payload["data"]["refuse_rule"] == "sparse_gated"
    assert payload["data"]["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    assert "recall" not in json.dumps(payload)
    empty = parse_tool_result(research.search_literature_corpus(
        ABSENT_SUBJECT, knowledgebase=research._PRODUCT_KNOWLEDGEBASE, retrieval_mode="hybrid",
    ))
    assert empty["data"]["success"] is True
    assert empty["data"]["result_count"] == 0
    assert empty["data"]["results"] == []
    assert empty["data"]["refuse_rule"] == "sparse_gated"


def test_d1_hybrid_does_not_admit_zero_sparse_cosine_hit(monkeypatch):
    """Dense may reorder BM25 admits. It must not admit a BM25 reject."""
    aligned = [0.0, 1.0] + [0.0] * 382

    def fake(texts, model_name=None):
        return research._MINILM_MODEL_ID, [aligned for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    index = _index()
    index["dense"]["vectors"] = [_unit(1.0), aligned]
    rows = research._search_index(index, PLANT, 5, "hybrid")
    assert [row["chunk_id"] for row in rows] == ["c-0001"]
    assert rows[0]["sparse_score"] > 0
    dense_rows = research._search_index(index, PLANT, 5, "dense")
    assert [row["chunk_id"] for row in dense_rows] == ["c-0001"]


def test_d1_missing_chunk_ids_is_unavailable(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm([]))
    index = _index()
    del index["dense"]["chunk_ids"]
    try:
        research._search_index(index, PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("missing chunk_ids must not fall back to position")
