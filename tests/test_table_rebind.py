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

TOKEN_V = "TOKEN_V"
TOKEN_BASIS = "TOKEN_BASIS"
TOKEN_NOTE = "TOKEN_NOTE"


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
