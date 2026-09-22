"""P-2 query-time BM25 lexicon. Injected ranker. No gold needles in asserts."""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import dense_d3, recall_r5lx, research, text_chunk_metrics
from dissolve.text_gold import TextGoldError

IDX = "aa" * 32
HOLD = "bb" * 32
FIRE_HIT = "tg-fixture-hit"
FIRE_MISS = "tg-fixture-lex"
HOLD_ID = "tg-fixture-hold"
NEEDLE_A = "ZXQALPHA"
NEEDLE_B = "ZXQBETA"
NEEDLE_HOLD = "ZXQHOLD"


def _entry(token: str, expansions: list[str]) -> dict:
    return {
        "token": token,
        "expansions": expansions,
        "provenance": {
            "kind": "general_chemistry",
            "source": "constructed fixture table",
        },
    }


def _pins() -> dict[str, str]:
    return {
        "gold": "g",
        "census": "c",
        "store": "s",
        "curves_v4": "v",
        "lexicon": "l",
    }


def test_tokens_ignores_lexicon():
    text = "low density polyethylene ldpe"
    with research.bm25_lexicon([]):
        empty = research._tokens(text)
    with research.bm25_lexicon([_entry("ldpe", ["low density polyethylene"])]):
        loaded = research._tokens(text)
    assert empty == loaded


def test_empty_lexicon_matches_raw_tokens():
    query = "ldpe dissolution thf"
    with research.bm25_lexicon([]):
        assert research._bm25_query_tokens(query) == research._tokens(query)


def test_helper_appends_expansion_tokens():
    query = "ldpe dissolution"
    with research.bm25_lexicon([_entry("ldpe", ["low density polyethylene"])]):
        tokens = research._bm25_query_tokens(query)
    raw = research._tokens(query)
    assert tokens[: len(raw)] == raw
    assert "polyethylene" in tokens
    assert "density" not in tokens
    assert query == "ldpe dissolution"


def test_bidirectional_adds_abbreviation():
    query = "low density polyethylene"
    with research.bm25_lexicon([_entry("ldpe", ["low density polyethylene"])]):
        tokens = research._bm25_query_tokens(query)
    assert "ldpe" in tokens


def test_bm25_top5_coverage_star_and_expanded_query_unwired():
    top5 = inspect.getsource(research._bm25_top5_on)
    top5_alias = inspect.getsource(research._bm25_top5)
    star = inspect.getsource(research.coverage_star)
    hybrid = inspect.getsource(research._hybrid_passage_parts)
    search = inspect.getsource(research._search_index)
    helper = inspect.getsource(research._query_sparse_raw)
    expanded = inspect.getsource(research._expanded_query)
    inspect_src = inspect.getsource(research.inspect_literature_corpus)
    assert "_bm25(_tokens(query)" in top5
    assert "_bm25_query_tokens" not in top5
    assert "_bm25_query_tokens" not in top5_alias
    assert "_bm25(_tokens(query)" in star
    assert "_bm25_query_tokens" not in star
    assert "_query_sparse_raw(query" in hybrid
    assert "_query_sparse_raw(query" in search
    assert "_bm25_query_tokens(query)" in helper
    assert "ldpe" in expanded
    assert "_bm25_query_tokens" not in expanded
    assert "_expanded_query(" in inspect_src


def test_dense_sees_original_query_string(monkeypatch):
    seen: list[str] = []

    def fake_dense(index, chunks, query):
        seen.append(query)
        return [0.1] * len(chunks)

    monkeypatch.setattr(research, "_dense_query_scores", fake_dense)
    chunk = {
        "chunk_id": "c1",
        "title": "t",
        "text": "low density polyethylene body",
        "body": "low density polyethylene body",
    }
    index = {"chunks": [chunk]}
    query = "ldpe"
    with research.bm25_lexicon([_entry("ldpe", ["low density polyethylene"])]):
        parts = research._hybrid_passage_parts(index, query)
    assert seen == [query]
    assert parts


def test_raw_hits_keep_unexpanded_scores():
    chunk = {
        "chunk_id": "c1",
        "title": "t",
        "text": "ldpe body only",
        "body": "ldpe body only",
    }
    index = {"chunks": [chunk]}
    with research.bm25_lexicon([]):
        empty = research._search_index(index, "ldpe", 5, "sparse")
    with research.bm25_lexicon([_entry("ldpe", ["low density polyethylene"])]):
        expanded = research._search_index(index, "ldpe", 5, "sparse")
    assert empty and expanded
    assert empty[0]["sparse_raw_score"] == expanded[0]["sparse_raw_score"]


def test_expansion_opens_zero_overlap_chunk():
    chunk = {
        "chunk_id": "c1",
        "title": "t",
        "text": "low density polyethylene body",
        "body": "low density polyethylene body",
    }
    index = {"chunks": [chunk]}
    with research.bm25_lexicon([]):
        empty = research._search_index(index, "ldpe", 5, "sparse")
    with research.bm25_lexicon([_entry("ldpe", ["low density polyethylene"])]):
        expanded = research._search_index(index, "ldpe", 5, "sparse")
    assert empty == []
    assert expanded and expanded[0]["chunk_id"] == "c1"
    assert expanded[0]["sparse_raw_score"] > 0


def test_coverage_star_stays_on_raw_tokens():
    chunk = {
        "chunk_id": "c1",
        "title": "t",
        "text": "low density polyethylene body",
        "body": "low density polyethylene body",
    }
    index = {"chunks": [chunk]}
    with research.bm25_lexicon([_entry("ldpe", ["low density polyethylene"])]):
        star = research.coverage_star(index, "ldpe")
        rows = research._search_index(index, "ldpe", 5, "sparse")
    assert star == 0.0
    assert rows and rows[0]["chunk_id"] == "c1"


def test_lexicon_rejects_gold_kind(tmp_path):
    path = tmp_path / "lex.json"
    path.write_text(json.dumps({
        "entries": [{
            "token": "ldpe",
            "expansions": ["low density polyethylene"],
            "provenance": {"kind": "gold", "source": "somewhere"},
        }],
    }))
    with pytest.raises(ValueError, match="lexicon_kind_forbidden"):
        research.load_lexicon_entries(path)


def test_committed_lexicon_kinds_are_closed():
    entries = research.load_lexicon_entries()
    assert entries
    kinds = {item["provenance"]["kind"] for item in entries}
    assert kinds <= research._LEXICON_KINDS
    for item in entries:
        source = item["provenance"]["source"].casefold()
        assert "needle" not in source
        assert "error_analysis" not in source
        assert "error-analysis" not in source
        assert "gold" not in source
        token_parts = research._tokens(item["token"])
        assert token_parts == [item["token"]]


def _gold() -> dict:
    return {
        "papers": [
            {"paper_sha256": IDX, "paper_status": "indexed"},
            {"paper_sha256": HOLD, "paper_status": "held_out"},
        ],
        "facts": [
            {
                "fact_id": FIRE_HIT,
                "paper_sha256": IDX,
                "paper_status": "indexed",
                "query": f"query {NEEDLE_A}",
                "needles": {"subject": NEEDLE_A, "value": NEEDLE_A},
            },
            {
                "fact_id": FIRE_MISS,
                "paper_sha256": IDX,
                "paper_status": "indexed",
                "query": "ldpe",
                "needles": {"subject": NEEDLE_B, "value": NEEDLE_B},
            },
            {
                "fact_id": HOLD_ID,
                "paper_sha256": HOLD,
                "paper_status": "held_out",
                "query": f"query {NEEDLE_HOLD}",
                "needles": {"subject": NEEDLE_HOLD, "value": NEEDLE_HOLD},
            },
        ],
    }


def _index() -> dict:
    return {
        "chunks": [
            {
                "chunk_id": "c-hit",
                "title": "a",
                "paper_sha256": IDX,
                "text": f"body {NEEDLE_A} polyethylene body",
                "body": f"body {NEEDLE_A} polyethylene body",
            },
            {
                "chunk_id": "c-lex",
                "title": "b",
                "paper_sha256": IDX,
                "text": f"low density polyethylene {NEEDLE_B}",
                "body": f"low density polyethylene {NEEDLE_B}",
            },
        ],
        "dense": {"model": "fixture", "dim": 384},
    }


def _rank(idx, query):
    return dense_d3.rank_mode(idx, query, "sparse")


def test_build_r5lx_fixture_has_no_forbidden_keys():
    artifact = recall_r5lx.build_r5lx_artifact(
        gold=_gold(),
        index=_index(),
        pins=_pins(),
        ranker=_rank,
        require_v4_ident=False,
    )
    assert artifact["net"] >= 1
    assert FIRE_MISS in artifact["fixed"]
    assert artifact["broken"] == []
    assert artifact["arms"]["poisoned_entry"]["sparse_raw_moved"] is True
    assert not text_chunk_metrics._contains_forbidden_keys(
        artifact, set(recall_r5lx._FORBIDDEN),
    )
    leaks = artifact["arms"]["expansion"]["n_leaks_at_k"]
    assert all(int(leaks[str(k)]) == 0 for k in (1, 3, 5, 10, 20))
    assert artifact["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}


def test_empty_lexicon_ident_must_fire():
    with pytest.raises(TextGoldError) as caught:
        recall_r5lx.build_r5lx_artifact(
            gold=_gold(),
            index=_index(),
            pins=_pins(),
            ranker=_rank,
            require_v4_ident=True,
        )
    assert caught.value.code == "empty_lexicon_not_ident"


def test_poison_control_fails_when_score_does_not_move(monkeypatch):
    monkeypatch.setattr(recall_r5lx, "CONSTRUCTED_PROBE", "no-such-token-zzz")
    monkeypatch.setattr(
        recall_r5lx, "POISON_ENTRY",
        _entry("no-such-token-zzz", ["also-absent-zzz"]),
    )
    with pytest.raises(TextGoldError) as caught:
        recall_r5lx.build_r5lx_artifact(
            gold=_gold(),
            index=_index(),
            pins=_pins(),
            ranker=_rank,
            require_v4_ident=False,
        )
    assert caught.value.code == "poison_did_not_move"
