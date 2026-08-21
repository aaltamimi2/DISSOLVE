"""C7b: footnote-and-caption re-attachment. Constructed fixtures. Gold v1 unmoved."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import research

GOLD = Path("/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/gold_facts.v1.json")
PERSIST = Path(
    "/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/parses/canonical_document.v1.json"
)
PYPDF = Path(
    "/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/parses/pypdf_parsed_document.v1.json"
)
C6 = Path(
    "/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/measurements/c6_one_paper_sweep.json"
)

TOKEN_V = "CELLVALUE"
TOKEN_BASIS = "BASISLINE"
TOKEN_NOTE = "FOOTNOTEZXQ"


def _block(block_id, kind, text, start, end, heading=None, caption_ref=None):
    return {
        "block_id": block_id, "kind": kind, "text": text,
        "char_start": start, "char_end": end, "page": 5,
        "nearest_preceding_heading": list(heading or ["3.2. Mixture"]),
        "caption_ref": caption_ref,
        "caption_ref_origin": "bound" if caption_ref else "unbound",
        "bbox": None, "confidence": 1.0, "footnote_refs": [],
    }


def _fixture():
    """Stub caption, orphan continuation, table with TOKEN_V, then following footnote."""
    parts = [
        ("C1", "caption", "Table 2"),
        ("B1", "paragraph", TOKEN_BASIS),
        ("T1", "table", f"[TABLE T1 page=5]\n| poly | {TOKEN_V} |\n[/TABLE]"),
        ("M1", "paragraph", "*"),
        ("F1", "footnote", TOKEN_NOTE),
        ("B2", "paragraph", "Unrelated following prose."),
    ]
    blocks = []
    texts = []
    cursor = 0
    heading = ["3.2. Mixture"]
    for block_id, kind, text in parts:
        if texts:
            cursor += 2
        start = cursor
        end = start + len(text)
        blocks.append(_block(block_id, kind, text, start, end, heading=heading))
        texts.append(text)
        cursor = end
    table_block = next(block for block in blocks if block["kind"] == "table")
    return {
        "schema": "dissolve.canonical-document.v1",
        "source_pdf_sha256": "ee" * 32,
        "parser_backend": "docling",
        "parser_version": "2.121.0",
        "fallback_reason": None,
        "canonical_text": "\n\n".join(texts),
        "blocks": blocks,
        "tables": [{
            "table_id": "T1", "page": 5,
            "char_start": table_block["char_start"], "char_end": table_block["char_end"],
            "caption_block_id": None, "row_count": 1, "column_count": 2,
            "cells": [], "grid_complete": True,
        }],
        "pages": 5,
    }


def _facts():
    return [{
        "fact_id": "T2-synthetic-rebind",
        "locus": "Table 2",
        "page": 5,
        "query": "synthetic table with TOKEN_V and TOKEN_NOTE",
        "needles": {"value": TOKEN_V, "basis": TOKEN_BASIS, "footnote": TOKEN_NOTE},
    }]


def test_rebind_does_not_invoke_docling_or_paragraph_chunks(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("C7b must not call Docling or production paragraph chunking")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(research, "parse_experiment_document", boom)
    monkeypatch.setattr(research, "_paragraph_chunks", boom)
    canonical = _fixture()
    chunks = research.apply_table_rebound(research.chunk_s2_block_pack(canonical), canonical)
    assert chunks
    research.score_chunks_against_facts(chunks, _facts(), canonical, strategy="S2_block_pack")


def test_atomicity_survives_rebind():
    canonical = _fixture()
    raw = research.chunk_s2_block_pack(canonical)
    rebound = research.apply_table_rebound(raw, canonical)
    table = canonical["tables"][0]
    assert research.table_atomic_fraction(rebound, canonical) == 1.0
    matched = [
        chunk for chunk in rebound
        if chunk.get("char_start") == table["char_start"] and chunk.get("char_end") == table["char_end"]
    ]
    assert matched
    body = matched[0]["body"]
    assert body == canonical["canonical_text"][table["char_start"]:table["char_end"]]
    assert TOKEN_V in body
    assert TOKEN_BASIS not in body
    assert TOKEN_NOTE not in body


def test_binding_is_not_atomicity():
    canonical = _fixture()
    rebound = research.apply_table_rebound(research.chunk_s2_block_pack(canonical), canonical)
    table = canonical["tables"][0]
    chunk = next(
        item for item in rebound
        if item.get("char_start") == table["char_start"] and item.get("char_end") == table["char_end"]
    )
    corpus = chunk["body_plus_rebound"]
    assert TOKEN_V in corpus and TOKEN_BASIS in corpus and TOKEN_NOTE in corpus
    scored = research.score_chunks_against_facts(
        rebound, _facts(), canonical, strategy="S2_block_pack",
    )
    row = scored["facts"][0]
    assert row["contain_bound_fact"] is False
    assert row["contain_bound_fact_rebound"] is True
    assert row["condition_severed_inside_table"] is False
    assert row["condition_severed_from_context"] is True


def test_1c_empty_rebound_makes_bind_go_red(monkeypatch):
    monkeypatch.setattr(research, "rebound_blocks_for_table", lambda *_args, **_kwargs: [])
    canonical = _fixture()
    rebound = research.apply_table_rebound(research.chunk_s2_block_pack(canonical), canonical)
    table = canonical["tables"][0]
    chunk = next(
        item for item in rebound
        if item.get("char_start") == table["char_start"] and item.get("char_end") == table["char_end"]
    )
    assert chunk["body_plus_rebound"] == chunk["body"]
    assert TOKEN_NOTE not in chunk["body_plus_rebound"]
    scored = research.score_chunks_against_facts(
        rebound, _facts(), canonical, strategy="S2_block_pack",
    )
    assert scored["facts"][0]["contain_bound_fact_rebound"] is False


def test_blob_size_is_not_binding():
    canonical = _fixture()
    blob = {
        "strategy": "S0_production_pypdf",
        "chunk_id": "flatten",
        "body": f"{TOKEN_BASIS} {TOKEN_V} {TOKEN_NOTE}",
        "header": "",
        "char_start": None,
        "char_end": None,
        "atomic_overflow": False,
    }
    assert TOKEN_V in blob["body"] and TOKEN_NOTE in blob["body"]
    assert research.table_atomic_fraction([blob], canonical) == 0.0
    scored = research.score_chunks_against_facts(
        [blob], _facts(), canonical, strategy="S0_production_pypdf",
    )
    assert scored["facts"][0]["contain_bound_fact"] is True
    assert scored["table_atomic"] is None


def test_s5_does_not_take_following_footnote():
    canonical = _fixture()
    s5 = research.chunk_s5_table_plus_neighbors(canonical)
    table_chunks = [chunk for chunk in s5 if TOKEN_V in chunk["body"]]
    assert table_chunks
    assert any(TOKEN_BASIS in chunk["body"] for chunk in table_chunks)
    assert all(TOKEN_NOTE not in chunk["body"] for chunk in table_chunks)
    assert research.table_atomic_fraction(s5, canonical) == 0.0
    scored = research.score_chunks_against_facts(
        s5, _facts(), canonical, strategy="S5_table_plus_neighbors",
    )
    row = scored["facts"][0]
    assert row["contain_bound_fact"] is False
    assert row["contain_bound_fact_rebound"] is False
    assert row["condition_severed_from_context"] is True


def test_gold_v1_digest_unmoved():
    digest = hashlib.sha256(GOLD.read_bytes()).hexdigest()
    assert digest == research._GOLD_FACTS_V1_SHA256


def test_inside_table_predicate_not_widened():
    """Whole-table missing extra-table needles is from_context, not inside_table."""
    canonical = _fixture()
    s2 = research.chunk_s2_block_pack(canonical)
    scored = research.score_chunks_against_facts(
        s2, _facts(), canonical, strategy="S2_block_pack",
    )
    assert scored["n_condition_severed_inside_table"] == 0
    assert scored["n_condition_severed_from_context"] == 1


@pytest.mark.skipif(not PERSIST.is_file(), reason="probe canonical persist missing")
def test_probe_table2_footnotes_rebound_not_spliced_into_span():
    canonical = json.loads(PERSIST.read_text(encoding="utf-8"))
    chunks = research._index_chunks_from_canonical(canonical)
    assert research.table_atomic_fraction(chunks, canonical) == 1.0
    hits = [chunk for chunk in chunks if "106.72" in chunk["text"]]
    assert hits
    hit = hits[0]
    assert hit["text"] == canonical["canonical_text"][hit["char_start"]:hit["char_end"]]
    assert "Solvent retention" not in hit["text"]
    assert "Solvent retention" in hit["body_plus_rebound"]
    assert "residue" in hit["body_plus_rebound"].casefold()
    assert "1 g for each polymer" in hit["body_plus_rebound"]


def _table_in_top(top, table) -> bool:
    return any(
        chunk.get("char_start") == table["char_start"]
        and chunk.get("char_end") == table["char_end"]
        for chunk in top
    )


def _table_sparse_score(query, chunks, corpus_of, table) -> float:
    rows = [{"text": corpus_of(chunk)} for chunk in chunks]
    scores = research._bm25(research._tokens(query), rows)
    for score, chunk in zip(scores, chunks):
        if chunk.get("char_start") == table["char_start"] and chunk.get("char_end") == table["char_end"]:
            return float(score)
    return 0.0


def test_c7c_token_note_query_returns_atomic_table():
    canonical = _fixture()
    rebound = research.apply_table_rebound(research.chunk_s2_block_pack(canonical), canonical)
    table = canonical["tables"][0]
    assert research.table_atomic_fraction(rebound, canonical) == 1.0
    assert _table_sparse_score(TOKEN_NOTE, rebound, research.chunk_sparse_corpus, table) > 0
    top = research._bm25_top5_on(TOKEN_NOTE, rebound, research.chunk_sparse_corpus)
    assert _table_in_top(top, table)


def test_c7c_1c_body_rank_misses_atomic_table(monkeypatch):
    monkeypatch.setattr(research, "chunk_sparse_corpus", research._chunk_body_text)
    canonical = _fixture()
    rebound = research.apply_table_rebound(research.chunk_s2_block_pack(canonical), canonical)
    table = canonical["tables"][0]
    assert _table_sparse_score(TOKEN_NOTE, rebound, research.chunk_sparse_corpus, table) == 0
    assert _table_sparse_score(TOKEN_NOTE, rebound, research._chunk_body_text, table) == 0


def test_c7c_search_index_ranks_rebound(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("C7c must not load MiniLM or call Docling")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    canonical = _fixture()
    path = tmp_path / "canonical_document.v1.json"
    path.write_text(json.dumps(canonical), encoding="utf-8")
    research._ingest_inputs([str(path)], [], "c7c-rank", True, 4, False)
    index = research._load_index("c7c-rank")
    table = canonical["tables"][0]
    rows = research._search_index(index, TOKEN_NOTE, 5, "sparse")
    assert any(
        row.get("page") == 5 and TOKEN_V in str(row.get("excerpt") or "")
        for row in rows
    )
    stored = [
        chunk for chunk in index["chunks"]
        if chunk.get("char_start") == table["char_start"]
        and chunk.get("char_end") == table["char_end"]
    ]
    assert stored
    assert stored[0]["text"] == canonical["canonical_text"][table["char_start"]:table["char_end"]]


def test_c7c_1c_search_index_body_rank_drops_table(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "chunk_sparse_corpus", research._chunk_body_text)
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    canonical = _fixture()
    path = tmp_path / "canonical_document.v1.json"
    path.write_text(json.dumps(canonical), encoding="utf-8")
    research._ingest_inputs([str(path)], [], "c7c-rank-1c", True, 4, False)
    index = research._load_index("c7c-rank-1c")
    rows = research._search_index(index, TOKEN_NOTE, 5, "sparse")
    assert not any(TOKEN_V in str(row.get("excerpt") or "") for row in rows)


@pytest.mark.skipif(not (PERSIST.is_file() and C6.is_file() and PYPDF.is_file()), reason="probe persist missing")
def test_c7c_official_s2_still_matches_c6_persist():
    canonical = json.loads(PERSIST.read_text(encoding="utf-8"))
    pages = research.pypdf_page_texts_from_persist(json.loads(PYPDF.read_text(encoding="utf-8")))
    record = research.sweep_one_paper_chunking(canonical, pages, GOLD)
    baseline = json.loads(C6.read_text(encoding="utf-8"))
    for name in research._C6_STRATEGY_IDS:
        got = record["strategies"][name]
        old = baseline["strategies"][name]
        assert got["n_contain_bound_fact"] == old["n_contain_bound_fact"]
        assert got["n_retrievable"] == old["n_retrievable"]
    s2 = record["strategies"]["S2_block_pack"]
    assert s2["n_contain_bound_fact_rebound"] == 10
    assert s2["n_retrievable"] == baseline["strategies"]["S2_block_pack"]["n_retrievable"]
