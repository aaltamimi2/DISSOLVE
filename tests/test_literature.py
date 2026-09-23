"""Literature tests."""
from __future__ import annotations

import ast
import copy
import gzip
import hashlib
import inspect
import json
import math
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from dissolve import corpus, literature_ingest, research
from dissolve.contracts import parse_tool_result

# --- from test_abstention_a1l.py: A-1L: surface sparse_raw_score; live hybrid default finds T5. No MiniLM. No gold needles.
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


# --- from test_abstention_a2a4.py: A-2A4 coverage + P5 floor. Fixtures only. No MiniLM. No gold needles.
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


# --- from test_canonical_document.py: C2: one-paper canonical document. Tiny fixtures. Not the probe PDF.
_HEADING_FILL = research._HEADING_FILL_KEY


_TEMP_RE = research._CANONICAL_TEMPERATURE_RE


_MEASURE_SCRIPTS = (
    (Path.home() / "dissolve-v12-audit/one_paper_experiment/measure_step2.py"),
    (Path.home() / "dissolve-v12-audit/one_paper_experiment/measure_step3_pypdf.py"),
    (Path.home() / "dissolve-v12-audit/one_paper_experiment/measure_parse.py"),
)


def _block(
    *,
    block_id: str,
    kind: str,
    text: str,
    page: int = 1,
    section_path: list[str] | None = None,
    heading_fill: str = "inherited_from_stack",
    bbox: dict | None = None,
    confidence: float = 1.0,
    footnote_refs: list | None = None,
    caption_ref=None,
    extra: dict | None = None,
) -> dict:
    row = {
        "block_id": block_id,
        "kind": kind,
        "reading_order": 0,
        "page": page,
        "bbox": bbox if bbox is not None else {
            "x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0, "coordinate_space": "page_points",
        },
        "section_path": list(section_path or []),
        "text": text,
        "confidence": confidence,
        "caption_ref": caption_ref,
        "footnote_refs": list(footnote_refs or []),
        _HEADING_FILL: heading_fill,
    }
    if extra:
        row.update(extra)
    return row


def _parsed(blocks: list[dict], tables: list[dict] | None = None, **extra) -> dict:
    payload = {
        "schema": "dissolve.parsed-document.v1",
        "library_id": "c2-fixture",
        "document_id": "DOC-C2",
        "source_sha256": "ab" * 32,
        "parser_backend": "docling",
        "parser_version": "2.121.0",
        "fallback_reason": None,
        "blocks": blocks,
        "tables": tables or [],
        "attachments": [],
        "quality_flags": [],
        "parsed_at": "2026-08-21T00:00:00+00:00",
        "parse_metrics": {"wall_s": 0.0, "peak_rss_bytes": 0},
    }
    payload.update(extra)
    return payload


def _glyph_counts(text: str) -> dict[str, int]:
    return {
        "degree": text.count("\u00b0"),
        "white_bullet": text.count("\u25e6"),
        "soft_hyphen": text.count("\u00ad"),
        "temperatures": len(_TEMP_RE.findall(text)),
    }


def _assert_1a(doc: dict) -> None:
    canonical = doc["canonical_text"]
    for block in doc["blocks"]:
        assert canonical[block["char_start"]:block["char_end"]] == block["text"]


def _repairs_precede_offsets(events: list[str]) -> bool:
    repairs = [i for i, name in enumerate(events) if name == "repair"]
    offsets = [i for i, name in enumerate(events) if name == "offset"]
    if not repairs or not offsets:
        return False
    return max(repairs) < min(offsets)


def _install_order_spies(monkeypatch) -> list[str]:
    events: list[str] = []
    original = research._m3_repair

    def repair(text: str) -> str:
        events.append("repair")
        return original(text)

    def mark(char_start: int, char_end: int) -> None:
        events.append("offset")
        del char_start, char_end

    monkeypatch.setattr(research, "_m3_repair", repair)
    monkeypatch.setattr(research, "_mark_offset_assignment", mark)
    return events


def test_1a_consistency_slice_equals_block_text():
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="alpha"),
        _block(block_id="B2", kind="paragraph", text="beta"),
    ])
    doc = research.build_canonical_document(parsed)
    _assert_1a(doc)
    assert doc["canonical_text"] == "alpha\n\nbeta"
    assert doc["blocks"][0]["char_start"] == 0
    assert doc["blocks"][0]["char_end"] == 5
    assert doc["blocks"][1]["char_start"] == 7
    assert doc["blocks"][1]["char_end"] == 11
    assert "\n\n" not in doc["blocks"][0]["text"]
    assert "\n\n" not in doc["blocks"][1]["text"]


def test_1b_every_repair_precedes_every_offset_assignment(monkeypatch):
    events = _install_order_spies(monkeypatch)
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="100 \u25e6 C"),
        _block(block_id="B2", kind="paragraph", text="S \u00b4 anchez"),
        _block(
            block_id="T1", kind="table", text="placeholder",
            extra={"page": 2},
        ),
    ], tables=[{
        "table_id": "T1", "page": 2, "caption_block_id": None,
        "row_count": 1, "column_count": 2, "grid_complete": True,
        "cells": [
            {"row": 0, "column": 0, "row_span": 1, "column_span": 1,
             "is_header": True, "text": "ex\u00adact", "block_refs": []},
            {"row": 0, "column": 1, "row_span": 1, "column_span": 1,
             "is_header": False, "text": "40 \u25e6 C", "block_refs": []},
        ],
    }])
    doc = research.build_canonical_document(parsed)
    assert events.count("repair") >= 1
    assert events.count("offset") == len(doc["blocks"])
    assert _repairs_precede_offsets(events)


def test_1c_identity_patch_turns_c25_red(monkeypatch):
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="Hold 100 \u25e6 C then 80 \u25e6 C."),
        _block(block_id="B2", kind="paragraph", text="re\u00adcycle"),
    ])
    honest = research.build_canonical_document(copy.deepcopy(parsed))
    honest_counts = _glyph_counts(honest["canonical_text"])
    assert honest_counts["degree"] > 0
    assert honest_counts["white_bullet"] == 0
    assert honest_counts["soft_hyphen"] == 0
    assert honest_counts["temperatures"] >= 2

    monkeypatch.setattr(research, "_m3_repair", lambda text: str(text))
    broken = research.build_canonical_document(copy.deepcopy(parsed))
    broken_counts = _glyph_counts(broken["canonical_text"])
    _assert_1a(broken)
    assert broken_counts["white_bullet"] > 0 or broken_counts["soft_hyphen"] > 0
    assert broken_counts["temperatures"] < honest_counts["temperatures"]
    assert "\u25e6" in broken["canonical_text"]
    assert "\u00ad" in broken["canonical_text"]


def test_cheat_a_length_preserving_post_pass_passes_1a_fails_1b():
    raw = "Hold 100 \u25e6 C."
    events = ["offset"]
    repaired = raw.replace("\u25e6", "\u00b0")
    events.append("repair")
    assert len(raw) == len(repaired)
    doc = {
        "canonical_text": repaired,
        "blocks": [{"text": repaired, "char_start": 0, "char_end": len(repaired)}],
    }
    _assert_1a(doc)
    assert not _repairs_precede_offsets(events)
    patched = raw  # identity "repair" after the same offsets
    cheat_disabled = {
        "canonical_text": patched.replace("\u25e6", "\u00b0"),
        "blocks": [{
            "text": patched.replace("\u25e6", "\u00b0"),
            "char_start": 0, "char_end": len(raw),
        }],
    }
    _assert_1a(cheat_disabled)
    # CHEAT A does not go red under an identity patch of _m3_repair: the glyph
    # rewrite lives in a different helper. That is why 1c exists on the named
    # function, and why this construction must not be the builder.
    assert _glyph_counts(cheat_disabled["canonical_text"])["white_bullet"] == 0


def test_cheat_b_post_pass_then_reslice_passes_1a_fails_1b():
    raw = "Hold 100 \u25e6 C."
    start, end = 0, len(raw)
    events = ["offset", "repair"]
    canonical = research._m3_repair(raw)
    sliced = canonical[start:end]
    doc = {
        "canonical_text": canonical,
        "blocks": [{"text": sliced, "char_start": start, "char_end": end}],
    }
    _assert_1a(doc)
    assert not _repairs_precede_offsets(events)


def test_cheat_c_post_pass_then_rebuild_offsets_byte_equal_fails_1b(monkeypatch):
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="Hold 100 \u25e6 C."),
    ])
    honest = research.build_canonical_document(copy.deepcopy(parsed))
    raw_text = parsed["blocks"][0]["text"]
    events = ["offset"]
    _ = (0, len(raw_text))
    repaired = research._m3_repair(raw_text)
    events.append("repair")
    rebuilt = {"canonical_text": repaired, "blocks": [{
        "text": repaired, "char_start": 0, "char_end": len(repaired),
    }]}
    events.append("offset")
    _assert_1a(rebuilt)
    assert rebuilt["canonical_text"] == honest["canonical_text"]
    assert rebuilt["blocks"][0]["text"] == honest["blocks"][0]["text"]
    assert not _repairs_precede_offsets(events)


def test_honest_builder_is_not_cheat_c(monkeypatch):
    events = _install_order_spies(monkeypatch)
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="Hold 100 \u25e6 C."),
        _block(block_id="B2", kind="paragraph", text="S \u00b4 anchez"),
    ])
    doc = research.build_canonical_document(parsed)
    assert _repairs_precede_offsets(events)
    assert "Sánchez" in doc["canonical_text"]
    assert "\u25e6" not in doc["canonical_text"]
    assert "\u00ad" not in doc["canonical_text"]


def test_c22_table_span_is_the_table_block_not_the_whole_document():
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="Before the grid."),
        _block(block_id="T1", kind="table", text="ignored"),
        _block(block_id="B2", kind="paragraph", text="After the grid."),
    ], tables=[{
        "table_id": "T1", "page": 1, "caption_block_id": None,
        "row_count": 2, "column_count": 2, "grid_complete": True,
        "cells": [
            {"row": 0, "column": 0, "row_span": 1, "column_span": 1,
             "is_header": True, "text": "polymer", "block_refs": []},
            {"row": 0, "column": 1, "row_span": 1, "column_span": 1,
             "is_header": True, "text": "wt", "block_refs": []},
            {"row": 1, "column": 0, "row_span": 1, "column_span": 1,
             "is_header": False, "text": "PX", "block_refs": []},
            {"row": 1, "column": 1, "row_span": 1, "column_span": 1,
             "is_header": False, "text": "99.01", "block_refs": []},
        ],
    }])
    doc = research.build_canonical_document(parsed)
    table_blocks = [b for b in doc["blocks"] if b["kind"] == "table"]
    assert len(table_blocks) == 1
    table = doc["tables"][0]
    assert table["char_start"] == table_blocks[0]["char_start"]
    assert table["char_end"] == table_blocks[0]["char_end"]
    span = doc["canonical_text"][table["char_start"]:table["char_end"]]
    assert span == table_blocks[0]["text"]
    assert span != doc["canonical_text"]
    assert (table["char_start"], table["char_end"]) != (0, len(doc["canonical_text"]))
    for cell in ("polymer", "wt", "PX", "99.01"):
        assert cell in span
    assert span.startswith("[TABLE T1 page=1]")
    assert span.endswith("[/TABLE]")


def test_c23_bbox_confidence_footnote_refs_copied_without_allowlist():
    parsed = _parsed([
        _block(
            block_id="B1", kind="paragraph", text="body",
            bbox={"x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0, "coordinate_space": "page_points"},
            confidence=0.87,
            footnote_refs=["#/footnotes/0"],
            extra={"parser_extra": "keep-me"},
        ),
    ])
    doc = research.build_canonical_document(parsed)
    block = doc["blocks"][0]
    assert block["bbox"] == {
        "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0, "coordinate_space": "page_points",
    }
    assert block["confidence"] == 0.87
    assert block["footnote_refs"] == ["#/footnotes/0"]
    assert block["parser_extra"] == "keep-me"
    assert "section_path" not in block


def test_c24_heading_origin_stamped_section_path_not_outward():
    parsed = _parsed([
        _block(
            block_id="H1", kind="heading", text="Methods",
            section_path=["Methods"], heading_fill="parser_supplied",
        ),
        _block(
            block_id="B1", kind="paragraph", text="body",
            section_path=["Methods"], heading_fill="inherited_from_stack",
            caption_ref=None,
        ),
        _block(
            block_id="C1", kind="caption", text="Fig. 1",
            section_path=["Methods"], heading_fill="inherited_from_stack",
            caption_ref="#/pictures/0",
        ),
    ])
    doc = research.build_canonical_document(parsed)
    heading, body, caption = doc["blocks"]
    assert heading["nearest_preceding_heading"] == ["Methods"]
    assert heading["nearest_preceding_heading_origin"] == "parser_supplied"
    assert body["nearest_preceding_heading_origin"] == "inherited_from_stack"
    assert caption["caption_ref_origin"] == "bound"
    assert body["caption_ref_origin"] == "unbound"
    for block in doc["blocks"]:
        assert "section_path" not in block
        assert block["nearest_preceding_heading_origin"] in {
            "parser_supplied", "inherited_from_stack",
        }
        assert block["nearest_preceding_heading_origin"] != "numbering_inferred"
        assert block["caption_ref_origin"] != "reconstructed"
    assert "section_path_is_provenance" not in doc
    assert doc["normalization"]["applied_during_build"] is True
    assert doc["normalization"]["post_pass"] is False


def test_c25_glyphs_and_elsevier_acute_on_fixture():
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="Hold 100 \u25e6 C for 2 h."),
        _block(block_id="B2", kind="paragraph", text="S \u00b4 anchez"),
        _block(block_id="B3", kind="paragraph", text="re\u00adprecipitation"),
    ])
    doc = research.build_canonical_document(parsed)
    text = doc["canonical_text"]
    counts = _glyph_counts(text)
    assert counts["degree"] > 0
    assert counts["white_bullet"] == 0
    assert counts["soft_hyphen"] == 0
    assert counts["temperatures"] >= 1
    assert "Sánchez" in text
    assert "reprecipitation" in text
    assert "°C" in text


def test_c26_figure_artifacts_missing_absent_from_code_and_payload():
    source = inspect.getsource(research.build_canonical_document)
    source += inspect.getsource(research._m3_repair)
    source += Path(research.__file__).read_text(encoding="utf-8")
    assert "figure_artifacts_missing" not in source
    parsed = _parsed([_block(block_id="B1", kind="paragraph", text="plain")])
    doc = research.build_canonical_document(parsed)
    assert "figure_artifacts_missing" not in doc
    assert "figure_artifacts_missing" not in (doc.get("quality_flags") or [])


def test_measurement_scripts_deleted_the_block_key_allowlist():
    needle = re.compile(
        r"""["']block_id["']\s*,\s*["']kind["']\s*,\s*["']reading_order["']"""
    )
    for path in _MEASURE_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        assert needle.search(text) is None, path.name
        assert "figure_artifacts_missing" not in text


def test_canonical_writer_has_no_key_allowlist_tuple():
    tree = ast.parse(inspect.getsource(research.build_canonical_document))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) >= 6:
            names = []
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.append(elt.value)
            if {"block_id", "kind", "reading_order", "page", "text"} <= set(names):
                pytest.fail(f"allowlist of block keys in writer: {names}")


def test_picture_and_claim_store_as_other_not_figure():
    parsed = _parsed([
        _block(block_id="P1", kind="figure", text="a picture caption-like"),
        _block(block_id="C1", kind="claim", text="a claim"),
    ])
    doc = research.build_canonical_document(parsed)
    kinds = [b["kind"] for b in doc["blocks"]]
    assert kinds == ["other", "other"]
    assert "figure" not in kinds


def test_production_bridge_is_not_widened_with_canonical_fields():
    payload = {
        "backend": "docling",
        "version": "2.121.0",
        "items": [{
            "id": "#/texts/0", "label": "paragraph", "order": 0, "page": 1,
            "text": "production path", "confidence": 1.0,
        }],
        "tables": [],
        "quality_flags": [],
        "fallback_reason": None,
    }
    acquisition = {
        "schema": "dissolve.acquire.v1",
        "library_id": "bridge-probe",
        "document": {
            "document_id": "DOC-BRIDGE",
            "library_id": "bridge-probe",
            "content_sha256": "ab" * 32,
            "document_kind": "paper",
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": "ab" * 32,
            "packed_path": "/tmp/bridge-probe.pdf",
        }],
    }
    parsed = research.parse_document_structure(
        acquisition, parser_payload=payload, parsed_at="2026-08-21T00:00:00+00:00",
    )
    assert parsed["schema"] == "dissolve.parsed-document.v1"
    assert _HEADING_FILL not in parsed["blocks"][0]
    assert "nearest_preceding_heading" not in parsed["blocks"][0]
    with pytest.raises(research.LiteratureContractError) as caught:
        research.build_canonical_document(
            {**parsed, "parse_metrics": {"wall_s": 0.0, "peak_rss_bytes": 0}},
        )
    assert caught.value.code == "missing_heading_fill_stamp"


def test_envelope_schema_and_separators_unowned():
    parsed = _parsed([
        _block(block_id="B1", kind="paragraph", text="one"),
        _block(block_id="B2", kind="paragraph", text="two"),
    ])
    doc = research.build_canonical_document(parsed)
    assert doc["schema"] == "dissolve.canonical-document.v1"
    assert doc["source_pdf_sha256"] == "ab" * 32
    assert doc["attachments"] == []
    assert doc["canonical_text"][3:5] == "\n\n"
    assert doc["blocks"][0]["char_end"] == 3
    assert doc["blocks"][1]["char_start"] == 5


# --- from test_canonical_ingest.py: C7: canonical documents reach the portable index. Not C3. Not C8.
PERSIST = Path(
    f"{Path.home()}/dissolve-v12-audit/one_paper_experiment/parses/canonical_document.v1.json"
)


def _canon() -> dict:
    caption = "Table 1 synthetic grid"
    basis = "1 g for each polymer and 30 g of the corresponding solvent"
    table = "[TABLE T1 page=4]\n| ZZ9 | keep |\n[/TABLE]"
    heading = "Methods"
    parts = [
        ("H1", "heading", heading, "parser_supplied"),
        ("B0", "paragraph", basis, "inherited_from_stack"),
        ("C1", "caption", caption, "inherited_from_stack"),
        ("T1", "table", table, "inherited_from_stack"),
        ("B1", "paragraph", "Following prose.", "inherited_from_stack"),
    ]
    blocks = []
    texts = []
    cursor = 0
    heading_path = []
    for block_id, kind, text, origin in parts:
        if texts:
            cursor += 2
        start = cursor
        end = start + len(text)
        if kind == "heading":
            heading_path = [text]
        blocks.append({
            "block_id": block_id, "kind": kind, "text": text,
            "char_start": start, "char_end": end, "page": 5 if kind == "table" else 1,
            "nearest_preceding_heading": list(heading_path),
            "nearest_preceding_heading_origin": origin,
            "caption_ref": "C1" if block_id == "T1" else None,
        })
        texts.append(text)
        cursor = end
    body = "\n\n".join(texts)
    table_block = next(b for b in blocks if b["kind"] == "table")
    return {
        "schema": "dissolve.canonical-document.v1",
        "source_pdf_sha256": "cd" * 32,
        "parser_backend": "docling",
        "parser_version": "2.121.0",
        "fallback_reason": None,
        "canonical_text": body,
        "blocks": blocks,
        "tables": [{
            "table_id": "T1", "page": 5,
            "char_start": table_block["char_start"], "char_end": table_block["char_end"],
            "caption_block_id": "C1",
        }],
        "pages": 5,
    }


def _ingest_canonical(monkeypatch, tmp_path, canonical: dict, knowledgebase: str = "c7-join"):
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    path = tmp_path / "canonical_document.v1.json"
    path.write_text(json.dumps(canonical), encoding="utf-8")
    return research._ingest_inputs(
        [str(path)], [], knowledgebase, True, 4, False,
    )


def _search_rows(payload: dict) -> list[dict]:
    data = payload.get("data") or payload
    value = data.get("results")
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value
    return []


def test_canonical_ingest_does_not_call_paragraph_chunks(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("_paragraph_chunks must not run on a Docling canonical document")

    monkeypatch.setattr(research, "_paragraph_chunks", boom)
    result = _ingest_canonical(monkeypatch, tmp_path, _canon())
    assert result["documents_added"] == 1
    assert result["chunks_added"] > 0


def test_table_atomic_gt_zero_on_join(monkeypatch, tmp_path):
    canonical = _canon()
    _ingest_canonical(monkeypatch, tmp_path, canonical)
    index = research._load_index("c7-join")
    assert research.table_atomic_fraction(index["chunks"], canonical) > 0
    table = canonical["tables"][0]
    matches = [
        c for c in index["chunks"]
        if c.get("char_start") == table["char_start"] and c.get("char_end") == table["char_end"]
    ]
    assert matches
    assert "ZZ9" in matches[0]["text"]
    assert matches[0]["kind"] == "table"


def test_inherited_heading_is_not_a_served_section_claim(monkeypatch, tmp_path):
    _ingest_canonical(monkeypatch, tmp_path, _canon())
    raw = research.search_literature_corpus(
        "ZZ9 keep", knowledgebase="c7-join", top_k=5, retrieval_mode="sparse",
    )
    rows = _search_rows(parse_tool_result(raw))
    assert rows
    table_row = next(
        row for row in rows
        if "ZZ9" in str(row.get("excerpt") or "")
    )
    assert table_row.get("section") in (None, "")
    assert table_row.get("section_origin") == "inherited_from_stack"
    assert table_row.get("section") != "Methods"


def test_served_table_carries_caption_and_basis(monkeypatch, tmp_path):
    _ingest_canonical(monkeypatch, tmp_path, _canon())
    raw = research.search_literature_corpus(
        "ZZ9", knowledgebase="c7-join", top_k=5, retrieval_mode="sparse",
    )
    blob = json.dumps(parse_tool_result(raw))
    assert "Table 1 synthetic grid" in blob
    assert "1 g for each polymer and 30 g of the corresponding solvent" in blob


def test_parser_supplied_section_may_be_served(monkeypatch, tmp_path):
    canonical = _canon()
    heading = next(b for b in canonical["blocks"] if b["kind"] == "heading")
    _ingest_canonical(monkeypatch, tmp_path, canonical)
    index = research._load_index("c7-join")
    heading_chunks = [c for c in index["chunks"] if c.get("section_origin") == "parser_supplied"]
    assert heading_chunks
    assert heading_chunks[0]["section"] == heading["text"]


@pytest.mark.skipif(not PERSIST.is_file(), reason="probe canonical persist missing")
def test_probe_table2_join_carries_basis_alongside():
    canonical = json.loads(PERSIST.read_text(encoding="utf-8"))
    chunks = research._index_chunks_from_canonical(canonical)
    assert research.table_atomic_fraction(chunks, canonical) > 0
    hits = [c for c in chunks if "106.72" in c["text"]]
    assert hits
    carried = " ".join(
        part for part in (hits[0].get("caption"), hits[0].get("basis"), hits[0]["text"]) if part
    )
    assert "1 g for each polymer and 30 g of the corresponding solvent" in carried
    text = canonical["canonical_text"]
    assert text[hits[0]["char_start"]:hits[0]["char_end"]] == hits[0]["text"]


# --- from test_chunk_strategies.py: C6: S0–S5 chunkers and the one-paper scorer. Tiny fixtures. Not C3.
def _block_chunk_strategies(block_id, kind, text, start, end, heading=None, caption_ref=None, page=1):
    return {
        "block_id": block_id, "kind": kind, "text": text,
        "char_start": start, "char_end": end, "page": page,
        "nearest_preceding_heading": list(heading or []),
        "caption_ref": caption_ref, "caption_ref_origin": "bound" if caption_ref else "unbound",
        "bbox": None, "confidence": 1.0, "footnote_refs": [],
    }


def _canon_chunk_strategies(*, parts: list[tuple[str, str, str]], tables=None, extra_text=""):
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
        blocks.append(_block_chunk_strategies(
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


def test_s2_atomic_overflow_keeps_long_table_whole():
    table = "[TABLE T1 page=4]\n" + _pad("Z", 1600) + "\n[/TABLE]"
    canonical = _canon_chunk_strategies(parts=[
        ("B1", "paragraph", "before"),
        ("T1", "table", table),
        ("B2", "paragraph", "after"),
    ])
    chunks = research.chunk_s2_block_pack(canonical)
    table_chunks = [c for c in chunks if c["atomic_overflow"]]
    assert table_chunks
    assert all(c["body"] == table for c in table_chunks)
    assert research.chunk_splits_atomic(chunks, canonical, {"table"}) == 0


# --- from test_corpus.py: corpus.py: the T5 build-and-grow path, on synthetic canonical documents (no papers needed).
SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


def _canonical(sha: str, blocks: list[tuple[str, dict]]) -> dict:
    """Blocks joined by blank lines, with offsets into the joined text (as canonical documents are)."""
    text, rows = "", []
    for index, (body, extra) in enumerate(blocks):
        if text:
            text += "\n\n"
        rows.append({"block_id": f"b{index}", "char_start": len(text), "char_end": len(text) + len(body), **extra})
        text += body
    return {"source_pdf_sha256": sha, "canonical_text": text, "blocks": rows, "tables": []}


def _paper(sha: str, token: str) -> dict:
    return _canonical(sha, [
        (f"{token} opening paragraph " + "x" * 580, {"page": 1, "kind": "text"}),
        ("second paragraph " + "y" * 583, {"page": 2, "kind": "text", "nearest_preceding_heading": ["Methods"],
                                           "nearest_preceding_heading_origin": "parser_supplied"}),
        ("third paragraph " + "z" * 584, {"page": 2, "kind": "table"}),
    ])


@pytest.fixture
def embeds(monkeypatch):
    """Stand-in embedder: records each call's batch and returns unit vectors of the model's dimension."""
    calls: list[list[str]] = []

    def fake(texts, model_name=None):
        calls.append(list(texts))
        dim = research._compatible_embedding_dim(model_name)
        return model_name, [[1.0] + [0.0] * (dim - 1) for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    return calls


@pytest.fixture
def papers(monkeypatch):
    docs = {SHA_A: _paper(SHA_A, "alphaword"), SHA_B: _paper(SHA_B, "betaword"), SHA_C: _paper(SHA_C, "gammaword")}
    monkeypatch.setattr(corpus, "_paper", lambda pdf: (str(pdf), docs[str(pdf)]))
    return docs


def test_t5_packs_whole_blocks_up_to_the_target():
    doc = _paper(SHA_A, "alphaword")
    first, second, third = [(b["char_start"], b["char_end"]) for b in doc["blocks"]]
    assert corpus.t5_spans(doc) == [(first[0], second[1]), third]
    assert all(end - start <= corpus.T5_TARGET for start, end in corpus.t5_spans(doc))


def test_t5_never_splits_a_block():
    doc = _canonical(SHA_A, [("short", {}), ("w" * 2000, {}), ("tail", {})])
    spans = corpus.t5_spans(doc)
    assert [doc["canonical_text"][s:e] for s, e in spans] == ["short", "w" * 2000, "tail"]


def test_chunk_records_stamp_page_section_kind_and_identity():
    first, second = corpus.chunk_records(_paper(SHA_A, "alphaword"), SHA_A)
    assert first["chunk_id"] == f"T5-{SHA_A[:12]}-0001" and second["chunk_id"] == f"T5-{SHA_A[:12]}-0002"
    assert first["document_id"] == f"D{SHA_A[:16]}"
    assert first["page"] == "1-2" and second["page"] == 2
    assert first["kind"] == "text" and second["kind"] == "table"
    assert first["section"] == "" and first["section_origin"] is None
    assert first["text"] == first["body"] and first["token_estimate"] == -(-len(first["body"]) // 4)
    assert first["sha256"] == hashlib.sha256(first["body"].encode()).hexdigest()


def test_section_comes_from_a_parser_supplied_heading():
    doc = _canonical(SHA_A, [("body text", {"nearest_preceding_heading": ["Results"],
                                             "nearest_preceding_heading_origin": "parser_supplied"})])
    (row,) = corpus.chunk_records(doc, SHA_A)
    assert row["section"] == research._chunk_header(["Results"]) and row["section_origin"] == "parser_supplied"


def test_build_then_add_one_paper_at_a_time(tmp_path, embeds, papers):
    corpus.build([Path(SHA_A)], tmp_path)
    assert len(embeds) == 1
    manifest = corpus.add([Path(SHA_B), Path(SHA_C), Path(SHA_A)], tmp_path)
    assert len(embeds) == 3, "each added paper is embedded on its own; papers already in the base are skipped"
    base = corpus.read_index(tmp_path / "indexes" / f"{corpus.BASE_KB}.json.gz")
    sidecar = corpus.read_index(tmp_path / "indexes" / f"{corpus.SIDECAR_KB}.json.gz")
    assert [d["sha256"] for d in base["documents"]] == [SHA_A]
    assert [d["sha256"] for d in sidecar["documents"]] == [SHA_B, SHA_C]
    assert sidecar["dense"]["chunk_ids"] == [row["chunk_id"] for row in sidecar["chunks"]]
    assert manifest["promoted"]["n_papers"] == 2 and manifest["n_papers"] == 1
    assert manifest["abstention"]["floor"] == corpus.ABSTENTION["floor"]


def test_manifest_chunk_list_is_text_free_and_verifies(tmp_path, embeds, papers):
    manifest = corpus.build([Path(SHA_A), Path(SHA_B)], tmp_path)
    assert all(set(row) == {"chunk_id", "paper_sha256", "char_start", "char_end", "sha256"} for row in manifest["chunks"])
    assert corpus.verify(tmp_path, manifest)["matching"] == len(manifest["chunks"])
    tampered = json.loads(json.dumps(manifest))
    tampered["chunks"][0]["sha256"] = "0" * 64
    assert corpus.verify(tmp_path, tampered)["differing"] == [manifest["chunks"][0]["chunk_id"]]


def test_build_index_refuses_a_substituted_model(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", lambda texts, model_name=None: ("other/model", [[1.0] * 384 for _ in texts]))
    with pytest.raises(research.LiteratureContractError):
        corpus.build_index([(SHA_A, _paper(SHA_A, "alphaword"))], corpus.BASE_KB)


def test_build_index_refuses_misshapen_vectors(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", lambda texts, model_name=None: (model_name, [[1.0] * 3 for _ in texts]))
    with pytest.raises(research.LiteratureContractError):
        corpus.build_index([(SHA_A, _paper(SHA_A, "alphaword"))], corpus.BASE_KB)


def test_the_agent_serves_a_built_corpus_with_its_sidecar(tmp_path, monkeypatch, embeds, papers):
    corpus.build([Path(SHA_A)], tmp_path)
    corpus.add([Path(SHA_B)], tmp_path)
    monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path))
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    index = research._load_index(corpus.BASE_KB)
    assert {row["paper_sha256"] for row in index["chunks"]} == {SHA_A, SHA_B}
    assert index["abstention"]["floor"] == corpus.ABSTENTION["floor"]
    for token, sha in (("alphaword", SHA_A), ("betaword", SHA_B)):
        rows = research._search_index(index, token, 3, "sparse")
        assert rows and rows[0]["paper_sha256"] == sha


def test_bge_artifact_reuses_known_vectors_and_encodes_the_rest(tmp_path, embeds, papers):
    corpus.build([Path(SHA_A)], tmp_path)
    corpus.add([Path(SHA_B)], tmp_path)
    base = corpus.read_index(tmp_path / "indexes" / f"{corpus.BASE_KB}.json.gz")
    reuse = tmp_path / "reuse.json.gz"
    known = [row["chunk_id"] for row in base["chunks"]]
    reuse.write_bytes(gzip.compress(json.dumps({"dense": {"chunk_ids": known, "vectors": [[0.5] * 768 for _ in known]}}).encode()))
    embeds.clear()
    manifest = corpus.bge_artifact(tmp_path, tmp_path / "bge", reuse=reuse)
    assert len(embeds) == 1 and len(embeds[0]) == manifest["n_chunks"] - len(known)
    index = corpus.read_index(tmp_path / "bge" / "index.json.gz")
    assert index["dense"]["model"] == research._BGE_MODEL_ID and index["dense"]["vectors"][0] == [0.5] * 768
    assert manifest["n_documents"] == 2 and manifest["abstention"] == {"floor": corpus.ABSTENTION["floor"]}


@pytest.mark.skipif(not os.getenv("DISSOLVE_CORPUS_REFERENCE"), reason="set DISSOLVE_CORPUS_REFERENCE to a served corpus")
def test_known_answer_rebuild_matches_the_reference_corpus(tmp_path):
    """Rebuild a served corpus from its canonical documents; every index must match byte for byte.

    DISSOLVE_CORPUS_REFERENCE is a directory holding canonical/<sha>.v1.json and the served
    indexes/ (the base index and, if present, the promoted sidecar).
    """
    ref = Path(os.environ["DISSOLVE_CORPUS_REFERENCE"])

    def papers_of(name):
        served = corpus.read_index(ref / "indexes" / name)
        order = list(dict.fromkeys(row["paper_sha256"] for row in served["chunks"]))
        return [(sha, json.loads((ref / "canonical" / f"{sha}.v1.json").read_text())) for sha in order]

    base = corpus.build_index(papers_of(f"{corpus.BASE_KB}.json.gz"), corpus.BASE_KB)
    sidecar = None
    if (ref / "indexes" / f"{corpus.SIDECAR_KB}.json.gz").is_file():
        for paper in papers_of(f"{corpus.SIDECAR_KB}.json.gz"):
            sidecar = corpus.build_index([paper], corpus.SIDECAR_KB, previous=sidecar)
    corpus._write(tmp_path, base, sidecar)
    for kb in (corpus.BASE_KB, corpus.SIDECAR_KB):
        if (ref / "indexes" / f"{kb}.json.gz").is_file():
            ours = gzip.decompress((tmp_path / "indexes" / f"{kb}.json.gz").read_bytes())
            assert ours == gzip.decompress((ref / "indexes" / f"{kb}.json.gz").read_bytes())


# --- from test_corpus_canonical.py: C3: 21 canonical documents. Not C8. Loads persist; does not call Docling.
CENSUS = (Path.home() / "dissolve-v12-audit/corpus/CENSUS.v1.json")


MANIFEST = (Path.home() / "dissolve-v12-audit/corpus/CANONICAL_MANIFEST.v1.json")


CANON_DIR = (Path.home() / "dissolve-v12-audit/corpus/canonical")


PARSED_DIR = (Path.home() / "dissolve-v12-audit/corpus/parsed")


PROBE_SHA = "1af857ee2e8299d6d0a586216ead5109a9b4293505585edf76da3bca9772ad21"


PATENT_SHA = "b95603201907ce4de4f4a62671d0ba8c20879b723da7b15fee5d8af2fa2f199f"


SCREENING_SHA = "74a97bb76eb805256d49c3ce07b42cca2357415b634e8f1d70873d9c92c05462"


C1_CEILING_BYTES = 3_501_953_024


TEMP_RE = research._CANONICAL_TEMPERATURE_RE


def _census_in_scope() -> set[str]:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    return {
        row["sha256"]
        for row in census["papers"]
        if row["status"] in {"indexed", "held_out"}
    }


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _canonical_corpus_canonical(sha: str) -> dict:
    return json.loads((CANON_DIR / f"{sha}.v1.json").read_text(encoding="utf-8"))


def _parsed_corpus_canonical(sha: str) -> dict:
    return json.loads((PARSED_DIR / f"{sha}.v1.json").read_text(encoding="utf-8"))


def test_manifest_sha_set_equals_census_indexed_union_held_out():
    manifest = _manifest()
    shas = [row["pdf_sha256"] for row in manifest["documents"]]
    assert manifest["n_documents"] == 21
    assert len(shas) == 21
    assert len(set(shas)) == 21
    assert set(shas) == _census_in_scope()
    assert PATENT_SHA not in set(shas)


def test_every_row_is_docling_with_null_fallback():
    for row in _manifest()["documents"]:
        assert row["parser_backend"] == "docling"
        assert row["fallback_reason"] is None
        assert row["peak_rss_bytes"] > 0
        assert row["wall_s"] > 0
        assert row["mem_available_before_bytes"] >= (
            research.PEAK_RSS_CEILING_BYTES + research.PEAK_RSS_HEADROOM_BYTES
        )


def test_1a_on_all_21_persisted_canonicals():
    for row in _manifest()["documents"]:
        doc = _canonical_corpus_canonical(row["pdf_sha256"])
        _assert_1a(doc)
        assert doc["source_pdf_sha256"] == row["pdf_sha256"]
        assert doc["parser_backend"] == "docling"
        assert doc["fallback_reason"] is None
        assert "figure_artifacts_missing" not in json.dumps(doc)
        for block in doc["blocks"]:
            assert "section_path" not in block
            origin = block.get("nearest_preceding_heading_origin")
            assert origin in {"parser_supplied", "inherited_from_stack"}
        for table in doc["tables"]:
            matches = [
                b for b in doc["blocks"]
                if b.get("kind") == "table" and b.get("block_id") == table.get("table_id")
            ]
            assert len(matches) == 1
            assert (matches[0]["char_start"], matches[0]["char_end"]) == (
                table["char_start"], table["char_end"],
            )


def test_1b_rebuild_from_saved_parsed_on_all_21(monkeypatch):
    """1b is a property of build_canonical_document. Re-run it; do not re-Docling."""
    events = _install_order_spies(monkeypatch)
    for row in _manifest()["documents"]:
        events.clear()
        parsed = _parsed_corpus_canonical(row["pdf_sha256"])
        metrics = {
            "wall_s": row["wall_s"],
            "peak_rss_bytes": row["peak_rss_bytes"],
        }
        doc = research.build_canonical_document(parsed, parse_metrics=metrics)
        assert events.count("repair") >= 1
        assert events.count("offset") == len(doc["blocks"])
        assert _repairs_precede_offsets(events)
        _assert_1a(doc)


def test_c25_glyphs_on_all_21_and_report_temperatures():
    counts = []
    for row in _manifest()["documents"]:
        doc = _canonical_corpus_canonical(row["pdf_sha256"])
        text = doc["canonical_text"]
        degree = text.count("\u00b0")
        white = text.count("\u25e6")
        hyphen = text.count("\u00ad")
        temps = len(TEMP_RE.findall(text))
        assert white == 0
        assert hyphen == 0
        assert temps == row["temperature_count"]
        counts.append({
            "pdf_sha256": row["pdf_sha256"],
            "temperature_count": temps,
            "degree_count": degree,
        })
    probe = next(item for item in counts if item["pdf_sha256"] == PROBE_SHA)
    assert probe["temperature_count"] >= 18
    assert probe["degree_count"] > 0
    assert len(counts) == 21


def test_c35_start_guard_and_recorded_breaches():
    """C3.5 after e1388ba OBJECT: start-guard held; C1 peak not silently raised; breaches listed."""
    assert research.PEAK_RSS_CEILING_BYTES == C1_CEILING_BYTES
    assert research.PEAK_RSS_HEADROOM_BYTES == 512 * 1024 * 1024
    manifest = _manifest()
    ceiling = research.PEAK_RSS_CEILING_BYTES
    guard = ceiling + research.PEAK_RSS_HEADROOM_BYTES
    shas = {row["pdf_sha256"] for row in manifest["documents"]}
    over = [row for row in manifest["documents"] if row["peak_rss_bytes"] > ceiling]
    listed = {item["pdf_sha256"] for item in manifest.get("ceiling_breaches") or []}
    assert listed == {row["pdf_sha256"] for row in over}
    assert listed <= shas
    assert SCREENING_SHA in shas
    for row in manifest["documents"]:
        assert row["wall_s"] > 0
        assert row["peak_rss_bytes"] > 0
        assert row["mem_available_before_bytes"] >= guard
    for row in over:
        assert row.get("exceeded_c1_ceiling") is True
        assert row["pdf_sha256"] in listed


# --- from test_dense_retrieval_d1.py: D-1 hybrid wiring. Constructed queries only. No gold rates.
NONSENSE_QUERY = "ZXQQQNONSENSEZXQ TOKENTHATISABSENTQZX"


PLANT = "PLANTZXQTOKEN"


ABSENT_SUBJECT = "ZXQKRYPTONITE ZXUNOBTAINIUM ZXREGOLITHQ"


def _index_dense_retrieval_d1(*, chunk_ids: list[str] | None = None, dense_ids: list[str] | None = None) -> dict:
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


def _fake_minilm_dense_retrieval_d1(seen, model_id=research._MINILM_MODEL_ID):
    def fake(texts, model_name=None):
        seen.extend(list(texts))
        return model_id, [_unit(1.0) for _ in texts]

    return fake


def test_d1_hybrid_does_not_raise_dense_unavailable(monkeypatch):
    seen = []
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm_dense_retrieval_d1(seen))
    rows = research._search_index(_index_dense_retrieval_d1(), PLANT, 5, "hybrid")
    assert rows
    assert rows[0]["chunk_id"] == "c-0001"
    assert seen == [PLANT]


def test_d1_chunk_id_set_identity_not_count(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm_dense_retrieval_d1([]))
    broken = _index_dense_retrieval_d1(dense_ids=["c-0001", "c-OTHER"])
    try:
        research._search_index(broken, PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("count-matched wrong chunk_id set must fail")


def test_d1_loaded_model_must_match_index(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm_dense_retrieval_d1([], model_id="not-the-index-model"))
    try:
        research._search_index(_index_dense_retrieval_d1(), PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("model id mismatch must fail")


def test_d1_served_components_sum_to_final(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm_dense_retrieval_d1([]))
    rows = research._search_index(_index_dense_retrieval_d1(), PLANT, 5, "hybrid")
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
    index = _index_dense_retrieval_d1()
    assert research._search_index(index, NONSENSE_QUERY, 5, "hybrid") == []
    assert research._search_index(index, ABSENT_SUBJECT, 5, "hybrid") == []


def test_d1_tool_records_sparse_gated_refuse(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm_dense_retrieval_d1([]))
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    research._save_index(_index_dense_retrieval_d1())
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
    index = _index_dense_retrieval_d1()
    index["dense"]["vectors"] = [_unit(1.0), aligned]
    rows = research._search_index(index, PLANT, 5, "hybrid")
    assert [row["chunk_id"] for row in rows] == ["c-0001"]
    assert rows[0]["sparse_score"] > 0
    dense_rows = research._search_index(index, PLANT, 5, "dense")
    assert [row["chunk_id"] for row in dense_rows] == ["c-0001"]


def test_d1_missing_chunk_ids_is_unavailable(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm_dense_retrieval_d1([]))
    index = _index_dense_retrieval_d1()
    del index["dense"]["chunk_ids"]
    try:
        research._search_index(index, PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("missing chunk_ids must not fall back to position")


# --- from test_docling_bridge.py: Docling bridge must put tables on the text item list, not only in tables[].
def _cell(row: int, column: int, text: str, **extra) -> SimpleNamespace:
    return SimpleNamespace(
        start_row_offset_idx=row, start_col_offset_idx=column,
        end_row_offset_idx=row + 1, end_col_offset_idx=column + 1,
        row_span=1, col_span=1, column_header=False, row_header=False,
        row_section=False, text=text, **extra,
    )


def _table_item(*, table_id: str, page: int, cells: list, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        label="table",
        self_ref=table_id,
        id=table_id,
        page=page,
        bbox=None,
        confidence=1.0,
        captions=[],
        footnotes=[],
        caption_ref=None,
        text=text,
        orig=text,
        data=SimpleNamespace(
            table_cells=cells, num_rows=1 + max(c.start_row_offset_idx for c in cells),
            num_cols=1 + max(c.start_col_offset_idx for c in cells),
        ),
    )


def _paragraph(*, block_id: str, page: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        label="paragraph",
        self_ref=block_id,
        id=block_id,
        page=page,
        bbox=None,
        confidence=1.0,
        captions=[],
        footnotes=[],
        caption_ref=None,
        text=text,
        orig=text,
        data=None,
    )


class _Document:
    def __init__(self, items: list) -> None:
        self._items = items

    def iterate_items(self):
        for item in self._items:
            yield item, 0


def _bridge(items: list) -> dict:
    return research._docling_bridge(_Document(items), version="2.121.0")


def _acquire(sha: str = "ab" * 32) -> dict:
    return {
        "schema": "dissolve.acquire.v1",
        "library_id": "bridge-probe",
        "document": {
            "document_id": "DOC-BRIDGE",
            "library_id": "bridge-probe",
            "content_sha256": sha,
            "document_kind": "paper",
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": sha,
            "packed_path": "/tmp/bridge-probe.pdf",
        }],
    }


def test_table_with_empty_item_text_still_enters_items():
    table = _table_item(
        table_id="#/tables/0",
        page=4,
        cells=[
            _cell(0, 0, "polymer"), _cell(0, 1, "wt_pct"),
            _cell(1, 0, "PX"), _cell(1, 1, "99.01"),
        ],
        text="",
    )
    bridge = _bridge([table])
    assert len(bridge["tables"]) == 1
    assert bridge["tables"][0]["row_count"] == 2
    assert bridge["tables"][0]["column_count"] == 2
    assert [c["text"] for c in bridge["tables"][0]["cells"]] == [
        "polymer", "wt_pct", "PX", "99.01",
    ]
    assert len(bridge["items"]) == 1
    item = bridge["items"][0]
    assert item["label"] == "table"
    assert item["id"] == "#/tables/0"
    assert item["page"] == 4
    assert "99.01" in item["text"]
    assert "PX" in item["text"]
    assert item["text"].startswith("[TABLE #/tables/0 page=4]")
    assert item["text"].endswith("[/TABLE]")


def test_table_and_paragraph_keep_reading_order():
    table = _table_item(
        table_id="#/tables/1",
        page=1,
        cells=[_cell(0, 0, "hdr"), _cell(1, 0, "val")],
    )
    paragraph = _paragraph(block_id="#/texts/0", page=1, text="Following the grid.")
    bridge = _bridge([table, paragraph])
    assert [row["id"] for row in bridge["items"]] == ["#/tables/1", "#/texts/0"]
    assert [row["label"] for row in bridge["items"]] == ["table", "paragraph"]
    assert bridge["items"][1]["text"] == "Following the grid."


def test_empty_cell_serializes_as_empty_not_a_dash():
    table = _table_item(
        table_id="#/tables/2",
        page=2,
        cells=[
            _cell(0, 0, "A"), _cell(0, 1, ""),
            _cell(1, 0, "B"), _cell(1, 1, "1"),
        ],
    )
    text = _bridge([table])["items"][0]["text"]
    assert "—" not in text
    assert "NaN" not in text
    assert "| A |  |" in text or "| A | |" in text


def test_paragraph_only_document_does_not_invent_a_table():
    paragraph = _paragraph(block_id="#/texts/9", page=1, text="No grid here.")
    bridge = _bridge([paragraph])
    assert bridge["tables"] == []
    assert "figures" not in bridge
    assert [row["label"] for row in bridge["items"]] == ["paragraph"]


def test_picture_item_with_text_is_other_not_paragraph():
    picture = SimpleNamespace(
        label="picture",
        self_ref="#/pictures/0",
        id="#/pictures/0",
        page=6,
        bbox=None,
        confidence=1.0,
        captions=[],
        footnotes=[],
        caption_ref=None,
        text="solubility curve",
        orig="solubility curve",
        data=None,
    )
    parsed = research.parse_document_structure(
        _acquire(), parser_payload=_bridge([picture]),
        parsed_at="2026-08-20T00:00:00+00:00",
    )
    kinds = [block["kind"] for block in parsed["blocks"]]
    assert kinds == ["other"]
    assert "paragraph" not in kinds
    assert "figure" not in kinds
    assert parsed["blocks"][0]["text"] == "solubility curve"


def test_normalize_keeps_table_kind_not_other():
    table = _table_item(
        table_id="#/tables/3",
        page=3,
        cells=[_cell(0, 0, "col"), _cell(1, 0, "42")],
    )
    parsed = research.parse_document_structure(
        _acquire(), parser_payload=_bridge([table]), parsed_at="2026-08-20T00:00:00+00:00",
    )
    kinds = [block["kind"] for block in parsed["blocks"]]
    assert "table" in kinds
    assert "other" not in kinds
    table_block = next(block for block in parsed["blocks"] if block["kind"] == "table")
    assert "42" in table_block["text"]
    assert parsed["tables"][0]["row_count"] == 2
    assert parsed["parser_backend"] == "docling"
    assert parsed["fallback_reason"] is None


# --- from test_experiment_parse.py: Experiment parses name a backend. Docling failure must raise, not pypdf.
def _acquire_experiment_parse(packed_path: Path, sha: str = "ab" * 32) -> dict:
    return {
        "schema": "dissolve.acquire.v1",
        "library_id": "bridge-probe",
        "document": {
            "document_id": "DOC-BRIDGE",
            "library_id": "bridge-probe",
            "content_sha256": sha,
            "document_kind": "paper",
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": sha,
            "packed_path": str(packed_path),
        }],
    }


def _pypdf_bridge_stub(path, fallback_reason):
    return {
        "backend": "pypdf",
        "version": "6.6.0",
        "items": [{
            "id": "PDF-P0001-B00001", "label": "paragraph", "order": 0,
            "page": 1, "text": "control-arm prose", "confidence": 0.75,
        }],
        "tables": [],
        "quality_flags": ["layout_degraded"],
        "fallback_reason": fallback_reason,
    }


def test_docling_failure_raises_instead_of_returning_pypdf(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 forced-failure")
    calls = {"pypdf": 0, "cascade": 0}

    def boom(path):
        raise research.LiteratureContractError(
            "parser_backend_failed", "forced Docling failure", backend="docling",
        )

    def count_pypdf(*args, **kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a Docling experiment parse")


    def count_cascade(*args, **kwargs):
        calls["cascade"] += 1
        raise AssertionError("production _parse cascade must not run")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)
    monkeypatch.setattr(literature_ingest, "_parse", count_cascade)

    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_experiment_document(_acquire_experiment_parse(pdf), backend="docling")
    assert caught.value.code == "parser_backend_failed"
    assert calls == {"pypdf": 0, "cascade": 0}


def test_pypdf_control_arm_is_named_not_a_docling_fallback(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 control-arm")

    def boom(path):
        raise AssertionError("Docling must not run on the named pypdf control arm")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", _pypdf_bridge_stub)

    parsed = research.parse_experiment_document(
        _acquire_experiment_parse(pdf), backend="pypdf", parsed_at="2026-08-20T00:00:00+00:00",
    )
    assert parsed["parser_backend"] == "pypdf"
    assert parsed["parser_version"] == "6.6.0"
    assert parsed["fallback_reason"] == "explicit_control_arm"
    assert parsed["blocks"][0]["text"] == "control-arm prose"


def test_unknown_experiment_backend_raises(tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_experiment_document(_acquire_experiment_parse(pdf), backend="deepdoc")
    assert caught.value.code == "unknown_experiment_backend"


def test_docling_payload_with_fallback_reason_is_a_lie():
    payload = {
        "backend": "docling",
        "version": "2.121.0",
        "items": [{
            "id": "#/texts/0", "label": "paragraph", "order": 0, "page": 1,
            "text": "looks like Docling", "confidence": 1.0,
        }],
        "tables": [],
        "quality_flags": [],
        "fallback_reason": "docling_parser_backend_failed;deepdoc_parser_backend_unavailable",
    }
    pdf_sha = "ab" * 32
    acquisition = {
        "schema": "dissolve.acquire.v1",
        "library_id": "bridge-probe",
        "document": {
            "document_id": "DOC-BRIDGE",
            "library_id": "bridge-probe",
            "content_sha256": pdf_sha,
            "document_kind": "paper",
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": pdf_sha,
            "packed_path": "/tmp/bridge-probe.pdf",
        }],
    }
    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_document_structure(
            acquisition, parser_payload=payload, parsed_at="2026-08-20T00:00:00+00:00",
        )
    assert caught.value.code == "parser_identity_lie"


# --- from test_production_parse.py: C4: production parse raises on Docling failure. DeepDoc and pypdf are not a fallback.
def _acquire_production_parse(packed_path: Path, sha: str = "ab" * 32) -> dict:
    return {
        "schema": "dissolve.acquire.v1",
        "library_id": "bridge-probe",
        "document": {
            "document_id": "DOC-BRIDGE",
            "library_id": "bridge-probe",
            "content_sha256": sha,
            "document_kind": "paper",
            "acquired_at": "2026-08-21T00:00:00+00:00",
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": sha,
            "packed_path": str(packed_path),
        }],
    }


def _docling_bridge():
    return {
        "backend": "docling",
        "version": "2.121.0",
        "items": [{
            "id": "#/texts/0", "label": "paragraph", "order": 0, "page": 1,
            "text": "production docling prose", "confidence": 1.0,
        }],
        "tables": [],
        "quality_flags": [],
        "fallback_reason": None,
    }


def test_production_parse_raises_when_docling_fails_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 forced-failure")
    calls = {"pypdf": 0}

    def boom(path):
        raise research.LiteratureContractError(
            "parser_backend_failed", "forced Docling failure", backend="docling",
        )

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a production Docling failure")


    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(pdf))
    assert caught.value.code == "parser_backend_failed"
    assert caught.value.details.get("backend") == "docling"
    assert calls == {"pypdf": 0}


def test_production_structure_parse_does_not_call_deepdoc_on_docling_failure(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 forced-failure")
    calls = {"pypdf": 0}

    def boom(path):
        raise research.LiteratureContractError(
            "parser_backend_failed", "forced Docling failure", backend="docling",
        )


    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_document_structure(_acquire_production_parse(pdf))
    assert caught.value.code == "parser_backend_failed"
    assert calls == {"pypdf": 0}


def test_corrupt_pdf_failure_is_loud_and_names_docling(monkeypatch, tmp_path):
    pdf = tmp_path / "corrupt.pdf"
    pdf.write_bytes(b"not-a-pdf")
    calls = {"pypdf": 0}

    class _FailingConverter:
        def convert(self, path):
            raise RuntimeError("invalid PDF header")

    orig_import = research.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "docling.document_converter":
            return SimpleNamespace(DocumentConverter=_FailingConverter)
        if name == "docling":
            return SimpleNamespace(__version__="2.121.0")
        return orig_import(name, *args, **kwargs)

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a corrupt PDF")


    monkeypatch.setattr(research.importlib, "import_module", fake_import)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(pdf))
    assert caught.value.code == "parser_backend_failed"
    assert caught.value.details.get("backend") == "docling"
    assert calls == {"pypdf": 0}


def test_production_parse_refuses_a_non_docling_bridge(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    calls = {"pypdf": 0}

    def lie(_path):
        return {
            "backend": "pypdf",
            "version": "6.6.0",
            "items": [{
                "id": "PDF-P0001-B00001", "label": "paragraph", "order": 0,
                "page": 1, "text": "fallback prose", "confidence": 0.75,
            }],
            "tables": [],
            "quality_flags": ["layout_degraded"],
            "fallback_reason": "docling_parser_backend_failed",
        }

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf bridge must not be the production fallback")


    monkeypatch.setattr(research, "_run_docling", lie)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(pdf))
    assert caught.value.code in {"parser_identity_lie", "undisclosed_parser_fallback"}
    assert calls["pypdf"] == 0


def test_production_parse_keeps_a_named_docling_success(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 ok")
    calls = {"pypdf": 0}

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a Docling success")


    monkeypatch.setattr(research, "_run_docling", lambda _path: _docling_bridge())
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    parsed = literature_ingest._parse(_acquire_production_parse(pdf))
    assert parsed["parser_backend"] == "docling"
    assert parsed["fallback_reason"] is None
    assert parsed["blocks"][0]["text"] == "production docling prose"
    assert calls == {"pypdf": 0}


def _suffix_fallback_spies(monkeypatch):
    calls = {"docling": 0, "pypdf": 0}

    def count_docling(*_args, **_kwargs):
        calls["docling"] += 1
        raise AssertionError("Docling must not run on a JATS/local-text suffix")

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a JATS/local-text suffix")


    monkeypatch.setattr(research, "_run_docling", count_docling)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)
    return calls


def test_production_parse_refuses_xml_jats_suffix_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    xml = tmp_path / "probe.xml"
    xml.write_text(
        "<article><article-title>Suffix probe</article-title>"
        "<p>jats body text for the identity check</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(xml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "jats"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_shtml_as_local_text_not_docling(monkeypatch, tmp_path):
    """87cc348 residual: unlisted suffixes must not reach Docling. .shtml is the named leftover."""
    shtml = tmp_path / "probe.shtml"
    shtml.write_text("<p>shtml body text for the identity check</p>\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(shtml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_unlisted_suffix_without_allowlist_membership(monkeypatch, tmp_path):
    """1c: a new allowlist row for .shtml would stay green. The PDF-only predicate must bind."""
    odd = tmp_path / "probe.notajats"
    odd.write_text("odd suffix body for the identity check\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(odd))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_htm_as_local_text_not_docling(monkeypatch, tmp_path):
    """11efc46 residual: .htm must stamp local_text, not fall through to Docling or jats."""
    htm = tmp_path / "probe.htm"
    htm.write_text("<p>htm body text for the identity check</p>\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(htm))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_html_as_local_text_not_docling(monkeypatch, tmp_path):
    """a5a43c0 residual: .html must stamp local_text, not fall through to Docling or jats."""
    html = tmp_path / "probe.html"
    html.write_text("<p>html body text for the identity check</p>\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(html))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_xhtml_as_jats_not_docling(monkeypatch, tmp_path):
    """08b2f0f residual: .xhtml must stamp jats, not fall through to Docling."""
    xhtml = tmp_path / "probe.xhtml"
    xhtml.write_text(
        "<article><article-title>XHTML probe</article-title>"
        "<p>xhtml body text for the identity check</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(xhtml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "jats"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_nxml_as_jats_not_local_text(monkeypatch, tmp_path):
    """b45a1b4 residual: .nxml must stamp jats, not fall through to Docling or local_text."""
    nxml = tmp_path / "probe.nxml"
    nxml.write_text(
        "<article><article-title>NXML probe</article-title>"
        "<p>nxml body text for the identity check</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(nxml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "jats"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_txt_local_text_suffix_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    txt = tmp_path / "probe.txt"
    txt.write_text("local text body for the identity check\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(txt))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_refuses_md_local_text_suffix_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    md = tmp_path / "probe.md"
    md.write_text("markdown body for the identity check\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(md))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0}


def test_production_parse_suffix_refuse_names_the_stamped_backend(monkeypatch, tmp_path):
    """1c: a hardcoded jats refuse from the suffix would stay green. The stamp must bind."""
    xml = tmp_path / "probe.xml"
    xml.write_text(
        "<article><p>suffix stamp body</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)
    jats_calls = {"n": 0}

    def lie_bridge(_path):
        jats_calls["n"] += 1
        return {
            "backend": "pypdf",
            "version": "6.6.0",
            "items": [{
                "id": "PDF-P0001-B00001", "label": "paragraph", "order": 0,
                "page": 1, "text": "forced suffix stamp", "confidence": 0.75,
            }],
            "tables": [],
            "quality_flags": ["layout_degraded"],
            "fallback_reason": "forced_suffix_stamp",
        }

    monkeypatch.setattr(literature_ingest, "_jats_bridge", lie_bridge)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire_production_parse(xml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "pypdf"
    assert caught.value.details.get("fallback_reason") == "forced_suffix_stamp"
    assert jats_calls["n"] == 1
    assert calls == {"docling": 0, "pypdf": 0}


# --- from test_table_rebind.py: C7b: footnote-and-caption re-attachment. Constructed fixtures. Gold v1 unmoved.
TOKEN_V = "CELLVALUE"


TOKEN_BASIS = "BASISLINE"


TOKEN_NOTE = "FOOTNOTEZXQ"


def _block_table_rebind(block_id, kind, text, start, end, heading=None, caption_ref=None):
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
        blocks.append(_block_table_rebind(block_id, kind, text, start, end, heading=heading))
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


def _table_sparse_score(query, chunks, corpus_of, table) -> float:
    rows = [{"text": corpus_of(chunk)} for chunk in chunks]
    scores = research._bm25(research._tokens(query), rows)
    for score, chunk in zip(scores, chunks):
        if chunk.get("char_start") == table["char_start"] and chunk.get("char_end") == table["char_end"]:
            return float(score)
    return 0.0


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


def _patched_dense(seen):
    def fake(texts, model_name=None):
        seen.extend(list(texts))
        return "patched-no-minilm", [[0.0, 1.0] for _ in texts]
    return fake


def _c7d_ingest_spies(monkeypatch, tmp_path, seen, corpus=None):
    def boom(*_args, **_kwargs):
        raise AssertionError("C7d must not call Docling, paragraph chunks, or MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", _patched_dense(seen))
    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(research, "parse_experiment_document", boom)
    monkeypatch.setattr(research, "_paragraph_chunks", boom)
    if corpus is not None:
        monkeypatch.setattr(research, "chunk_sparse_corpus", corpus)
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    canonical = _fixture()
    path = tmp_path / "canonical_document.v1.json"
    path.write_text(json.dumps(canonical), encoding="utf-8")
    research._ingest_inputs([str(path)], [], "c7d-embed", True, 4, True)
    return canonical, research._load_index("c7d-embed")


def test_c7d_atomicity_holds():
    canonical = _fixture()
    rebound = research.apply_table_rebound(research.chunk_s2_block_pack(canonical), canonical)
    table = canonical["tables"][0]
    assert research.table_atomic_fraction(rebound, canonical) > 0
    matched = [
        chunk for chunk in rebound
        if chunk.get("char_start") == table["char_start"] and chunk.get("char_end") == table["char_end"]
    ]
    assert matched
    body = matched[0]["body"]
    assert TOKEN_V in body
    assert TOKEN_NOTE not in body
    assert body == canonical["canonical_text"][table["char_start"]:table["char_end"]]


def _table_embed_texts(index, seen, table):
    assert len(seen) == len(index["chunks"])
    return [
        text for chunk, text in zip(index["chunks"], seen)
        if chunk.get("char_start") == table["char_start"]
        and chunk.get("char_end") == table["char_end"]
    ]


def test_c7d_dense_embed_receives_rebound_not_body(monkeypatch, tmp_path):
    seen = []
    canonical, index = _c7d_ingest_spies(monkeypatch, tmp_path, seen)
    table = canonical["tables"][0]
    embeds = _table_embed_texts(index, seen, table)
    assert embeds
    assert TOKEN_NOTE in embeds[0]
    assert TOKEN_V in embeds[0]
    stored = [
        chunk for chunk in index["chunks"]
        if chunk.get("char_start") == table["char_start"]
        and chunk.get("char_end") == table["char_end"]
    ]
    assert stored
    assert TOKEN_NOTE not in stored[0]["text"]
    assert stored[0]["text"] == canonical["canonical_text"][table["char_start"]:table["char_end"]]
    assert len(index["documents"]) == 1


def test_c7d_1c_force_body_embed_drops_note(monkeypatch, tmp_path):
    seen = []
    canonical, index = _c7d_ingest_spies(
        monkeypatch, tmp_path, seen, corpus=research._chunk_body_text,
    )
    embeds = _table_embed_texts(index, seen, canonical["tables"][0])
    assert embeds
    assert TOKEN_NOTE not in embeds[0]
    assert TOKEN_V in embeds[0]
