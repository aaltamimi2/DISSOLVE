from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dissolve import rag90_search, research
from dissolve.contracts import parse_tool_result

FIXTURE = Path(__file__).with_name("fixtures") / "rag90_known_answer.v1.json"
PAPER = "a" * 64


def _chunk(chunk_id: str, body: str) -> dict:
    return {
        "chunk_id": chunk_id, "document_id": "d1", "paper_sha256": PAPER, "title": "Fixture", "body": body,
        "text": body, "section": "methods", "section_origin": "parser_supplied", "char_start": 0,
        "char_end": len(body), "page": 1,
    }


def _index(ids: list[str]) -> dict:
    return {
        "schema": research._INDEX_SCHEMA, "knowledgebase": research._PRODUCT_KNOWLEDGEBASE,
        "documents": [{"document_id": "d1", "sha256": PAPER, "title": "Fixture"}],
        "chunks": [_chunk(chunk_id, f"passage {chunk_id}") for chunk_id in ids],
    }


def _search(monkeypatch, index: dict, *, top_k: int = 5, mode: str = "hybrid") -> dict:
    monkeypatch.setattr(research, "_load_index", lambda kb: index)
    return parse_tool_result(research.search_literature_corpus("solvents for polystyrene", top_k=top_k,
                                                                retrieval_mode=mode))


def test_the_served_ranking_stays_unless_the_rag90_engine_is_selected(monkeypatch):
    monkeypatch.delenv("DISSOLVE_LITERATURE_ENGINE", raising=False)
    assert research.literature_engine() == "served"
    monkeypatch.setenv("DISSOLVE_LITERATURE_ENGINE", "rag90")
    assert research.literature_engine() == "rag90"


def test_rag90_rows_keep_the_designs_order_and_ask_for_equal_text(monkeypatch):
    ids = ["T5-c", "T5-a", "T5-d", "T5-b"]
    asked = []
    monkeypatch.setenv("DISSOLVE_LITERATURE_ENGINE", "rag90")
    monkeypatch.setattr(rag90_search, "chunk_ids", lambda: frozenset(ids))
    monkeypatch.setattr(rag90_search, "search", lambda query, budget: asked.append(budget) or ["T5-d", "T5-a"])
    result = _search(monkeypatch, _index(ids), top_k=5)
    rows = result["data"]["results"]
    assert [row["chunk_id"] for row in rows] == ["T5-d", "T5-a"]
    assert [row["citation_id"] for row in rows] == ["C1", "C2"]
    assert asked == [5 * research.RAG90_CHARS_PER_PASSAGE]
    assert result["data"]["literature_engine"] == research.RAG90_ENGINE_ID


def test_rag90_refuses_a_corpus_it_was_not_built_on(monkeypatch):
    monkeypatch.setenv("DISSOLVE_LITERATURE_ENGINE", "rag90")
    monkeypatch.setattr(rag90_search, "chunk_ids", lambda: frozenset({"T5-a", "T5-b"}))
    monkeypatch.setattr(rag90_search, "search", lambda query, budget: pytest.fail("ranked a foreign corpus"))
    result = _search(monkeypatch, _index(["T5-a", "T5-b", "T5-extra"]))
    assert result["data"]["error_code"] == "retrieval_failed"
    assert "rag90_corpus_mismatch" in result["data"]["error"]


def test_sparse_and_dense_requests_keep_the_served_ranking(monkeypatch):
    monkeypatch.setenv("DISSOLVE_LITERATURE_ENGINE", "rag90")
    monkeypatch.setattr(rag90_search, "search", lambda query, budget: pytest.fail("rag90 answered a sparse request"))
    result = _search(monkeypatch, _index(["T5-a", "T5-b"]), mode="sparse")
    assert "literature_engine" not in result["data"]


def test_the_packaged_tree_has_no_absolute_paths_and_no_coverage_package():
    sources = list(rag90_search.ROOT.rglob("*.py"))
    assert sources
    assert not [str(path) for path in sources if "/home/" in path.read_text(encoding="utf-8")]
    assert not list(rag90_search.ROOT.rglob("coverage/__init__.py"))


def test_both_designs_rank_the_same_passages_and_unknown_designs_are_refused():
    passages = [rag90_search.ROOT / "candidates" / design / "passages.jsonl" for design in rag90_search.RUNTIME_DESIGNS.values()]
    assert len({path.read_bytes() for path in passages}) == 1
    with pytest.raises(ValueError, match="unknown rag90 design"):
        rag90_search.search("solvents for polystyrene", 7_000, design="combo-v3")


def test_rag90_chunk_set_matches_the_complete_shipped_89_paper_release():
    from dissolve import corpus
    release = corpus.read_release(corpus.SHIPPED)[1]
    assert len(release["documents"]) == 89
    assert len(rag90_search.chunk_ids()) == 4432
    assert rag90_search.chunk_ids() == frozenset(row["chunk_id"] for row in release["chunks"])


@pytest.mark.skipif(not os.getenv("DISSOLVE_BGE_ONNX_INT8"), reason="needs the pinned int8 BGE encoder")
@pytest.mark.parametrize(("design", "fixture"), [
    ("rag90-final-v1", FIXTURE),
    ("ensemble-v4", FIXTURE.with_name("rag90_ensemble_v4_known_answer.v1.json")),
])
def test_the_package_reproduces_the_frozen_candidates_lists(design, fixture):
    fixture = json.loads(fixture.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        for budget in fixture["budgets"]:
            assert rag90_search.search(case["query"], budget, design=design) == case["lists"][str(budget)], (
                case["query"], budget)
