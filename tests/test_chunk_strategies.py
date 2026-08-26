"""C6: S0–S5 chunkers and the one-paper scorer. Tiny fixtures. Not C3."""
from __future__ import annotations

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


def _block(block_id, kind, text, start, end, heading=None, caption_ref=None, page=1):
    return {
        "block_id": block_id, "kind": kind, "text": text,
        "char_start": start, "char_end": end, "page": page,
        "nearest_preceding_heading": list(heading or []),
        "caption_ref": caption_ref, "caption_ref_origin": "bound" if caption_ref else "unbound",
        "bbox": None, "confidence": 1.0, "footnote_refs": [],
    }


def _canon(*, parts: list[tuple[str, str, str]], tables=None, extra_text=""):
    """parts: (block_id, kind, text). Join with newlines like the canonical builder."""
    blocks = []
    cursor = 0
    texts = []
    heading: list[str] = []
    for block_id, kind, text in parts:
        if texts:
            cursor += 2
        start = cursor
        end = start + len(text)
        if kind == "heading":
            heading = [text]
        blocks.append(_block(
            block_id, kind, text, start, end, heading=heading,
            page=4 if kind == "table" else 1,
        ))
        texts.append(text)
        cursor = end
    body = "\n\n".join(texts) + extra_text
    table_rows = []
    for block in blocks:
        if block["kind"] == "table":
            table_rows.append({
                "table_id": block["block_id"], "page": 4,
                "char_start": block["char_start"], "char_end": block["char_end"],
                "caption_block_id": None, "row_count": 1, "column_count": 1,
                "cells": [], "grid_complete": True,
            })
    return {
        "schema": "dissolve.canonical-document.v1",
        "source_pdf_sha256": "ab" * 32,
        "canonical_text": body,
        "blocks": blocks,
        "tables": tables if tables is not None else table_rows,
        "pages": 4,
        "parser_backend": "docling",
        "parser_version": "2.121.0",
        "fallback_reason": None,
    }


def _pad(char: str, n: int) -> str:
    return char * n


def test_chunkers_do_not_invoke_docling(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("Docling must not run during C6")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(research, "parse_experiment_document", boom)
    canonical = _canon(parts=[
        ("B1", "paragraph", _pad("a", 100)),
        ("T1", "table", "[TABLE T1 page=4]\n| polymer | val |\n[/TABLE]"),
        ("B2", "paragraph", _pad("b", 100)),
    ])
    research.chunk_s1_naive_char(canonical)
    research.chunk_s2_block_pack(canonical)
    research.chunk_s3_section_pack(canonical)
    research.chunk_s4_sentence_pack(canonical)
    research.chunk_s5_table_plus_neighbors(canonical)
    research.chunk_s0_production_pypdf(["a short page of pypdf text. " * 20])


def test_s1_splits_tables_s2_and_s5_do_not():
    prefix = _pad("x", 1300)
    table = "[TABLE T1 page=4]\n| cell |\n" + _pad("T", 380) + "\n[/TABLE]"
    suffix = _pad("y", 800)
    canonical = _canon(parts=[
        ("B1", "paragraph", prefix),
        ("T1", "table", table),
        ("B2", "paragraph", suffix),
    ])
    s1 = research.chunk_s1_naive_char(canonical)
    s2 = research.chunk_s2_block_pack(canonical)
    s5 = research.chunk_s5_table_plus_neighbors(canonical)
    assert research.chunk_splits_atomic(s1, canonical, {"table"}) > 0
    assert research.chunk_splits_atomic(s2, canonical, {"table"}) == 0
    assert research.chunk_splits_atomic(s5, canonical, {"table"}) == 0
    assert research.chunk_splits_atomic(s2, canonical, {"formula", "caption"}) == 0
    assert research.table_atomic_fraction(s2, canonical) == 1.0


def test_s1_s5_round_trip_into_canonical_text():
    canonical = _canon(parts=[
        ("H1", "heading", "Methods"),
        ("B1", "paragraph", "First sentence. Second sentence is longer than the first one."),
        ("T1", "table", "[TABLE T1 page=4]\n| a | b |\n[/TABLE]"),
        ("C1", "caption", "Table 1 synthetic grid"),
    ])
    for chunks in (
        research.chunk_s1_naive_char(canonical),
        research.chunk_s2_block_pack(canonical),
        research.chunk_s3_section_pack(canonical),
        research.chunk_s4_sentence_pack(canonical),
        research.chunk_s5_table_plus_neighbors(canonical),
    ):
        assert chunks
        for chunk in chunks:
            assert research._canonical_slice_ok(canonical, chunk)


def test_s0_has_no_canonical_offsets():
    chunks = research.chunk_s0_production_pypdf(["Intro text.\n\nMore pypdf page text here."])
    assert chunks
    for chunk in chunks:
        assert chunk["strategy"] == "S0_production_pypdf"
        assert chunk["char_start"] is None
        assert chunk["char_end"] is None


def test_s3_heading_starts_a_new_chunk():
    canonical = _canon(parts=[
        ("H1", "heading", "One"),
        ("B1", "paragraph", "aaaa"),
        ("H2", "heading", "Two"),
        ("B2", "paragraph", "bbbb"),
    ])
    chunks = research.chunk_s3_section_pack(canonical)
    heads = [c["header"] for c in chunks]
    assert any(h == "One" for h in heads)
    assert any(h == "Two" for h in heads)
    assert not any("One" in c["body"] and "Two" in c["body"] for c in chunks)


def test_s5_table_chunk_covers_predecessor_and_caption():
    canonical = _canon(parts=[
        ("B0", "paragraph", "Neighbor prose names the step."),
        ("T1", "table", "[TABLE T1 page=4]\n| ZZ9 |  |\n[/TABLE]"),
        ("C1", "caption", "Table 1 synthetic grid"),
    ])
    canonical["tables"][0]["caption_block_id"] = "C1"
    canonical["blocks"][1]["caption_ref"] = "C1"
    chunks = research.chunk_s5_table_plus_neighbors(canonical)
    table_chunks = [c for c in chunks if "TABLE T1" in c["body"]]
    assert len(table_chunks) == 1
    body = table_chunks[0]["body"]
    assert "Neighbor prose" in body
    assert "Table 1 synthetic grid" in body
    text = canonical["canonical_text"]
    assert text[table_chunks[0]["char_start"]:table_chunks[0]["char_end"]] == body


def test_gold_digest_mismatch_aborts(tmp_path):
    path = tmp_path / "gold_facts.v1.json"
    path.write_bytes(b'{"facts": []}\n')
    with pytest.raises(research.LiteratureContractError) as caught:
        research.load_sealed_gold_facts(path)
    assert caught.value.code == "gold_digest_mismatch"


def test_f4_is_parse_miss_without_page6_table():
    canonical = _canon(parts=[("B1", "paragraph", "no figure table here")])
    facts = [{
        "fact_id": "F4-synthetic",
        "locus": "Fig. 4 embedded table",
        "page": 6,
        "query": "synthetic figure query",
        "needles": {"polymer": "QQ", "value": "zz9"},
    }]
    chunks = research.chunk_s2_block_pack(canonical)
    scored = research.score_chunks_against_facts(
        chunks, facts, canonical, strategy="S2_block_pack",
    )
    assert scored["facts"][0]["parse_miss"] is True
    assert scored["facts"][0]["contain_bound_fact"] is None
    assert scored["facts"][0]["retrievable"] is None
    assert scored["n_parse_miss"] == 1


def test_condition_severed_only_on_proper_subset_of_table():
    table = "[TABLE T1 page=4]\n| ZZ9 | keep |\n| other | row |\n[/TABLE]"
    canonical = _canon(parts=[
        ("C1", "caption", "Table 1 synthetic"),
        ("T1", "table", table),
    ])
    canonical["tables"][0]["caption_block_id"] = "C1"
    facts = [{
        "fact_id": "T1-synthetic",
        "locus": "Table 1",
        "page": 4,
        "query": "synthetic table query",
        "needles": {"polymer": "keep", "value": "ZZ9"},
    }]
    whole = research.chunk_s2_block_pack(canonical)
    whole_score = research.score_chunks_against_facts(
        whole, facts, canonical, strategy="S2_block_pack",
    )
    assert whole_score["facts"][0]["condition_severed_inside_table"] is False

    ts, te = canonical["tables"][0]["char_start"], canonical["tables"][0]["char_end"]
    body = canonical["canonical_text"]
    zz = body.find("ZZ9")
    assert ts <= zz < te
    subset = [{
        "strategy": "S1_naive_char", "chunk_id": "subset",
        "body": body[zz:zz + 3],
        "header": "", "char_start": zz, "char_end": zz + 3, "atomic_overflow": False,
    }]
    assert "ZZ9" in subset[0]["body"]
    assert "keep" not in subset[0]["body"]
    subset_score = research.score_chunks_against_facts(
        subset, facts, canonical, strategy="S1_naive_char",
    )
    assert subset_score["facts"][0]["condition_severed_inside_table"] is True
    assert subset_score["facts"][0]["contain_bound_fact"] is False


def test_sweep_names_all_six_and_does_not_prune():
    assert GOLD.is_file()
    canonical = _canon(parts=[
        ("H1", "heading", "Methods"),
        ("B1", "paragraph", "Prose."),
        ("C1", "caption", "Table 1 synthetic"),
        ("T1", "table", "[TABLE T1 page=4]\n| a |\n[/TABLE]"),
    ])
    record = research.sweep_one_paper_chunking(canonical, ["pypdf page"], GOLD)
    assert record["strategies_named"] == list(research._C6_STRATEGY_IDS)
    assert set(record["strategies"]) == set(research._C6_STRATEGY_IDS)
    blob = json.dumps(record)
    assert "will not be run at C9" not in blob
    assert record["c9_all_six_still_run"] is True
    assert "f1" not in {key.casefold() for key in record}
    assert record["did_not_invoke_docling"] is True
    assert "figure_artifacts_missing" not in blob


def test_c26_still_absent_from_chunk_code():
    source = Path(research.__file__).read_text(encoding="utf-8")
    assert "figure_artifacts_missing" not in source


def test_s2_atomic_overflow_keeps_long_table_whole():
    table = "[TABLE T1 page=4]\n" + _pad("Z", 1600) + "\n[/TABLE]"
    canonical = _canon(parts=[
        ("B1", "paragraph", "before"),
        ("T1", "table", table),
        ("B2", "paragraph", "after"),
    ])
    chunks = research.chunk_s2_block_pack(canonical)
    table_chunks = [c for c in chunks if c["atomic_overflow"]]
    assert table_chunks
    assert all(c["body"] == table for c in table_chunks)
    assert research.chunk_splits_atomic(chunks, canonical, {"table"}) == 0
