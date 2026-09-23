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
import shutil
import statistics
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from dissolve import corpus, research
from dissolve.contracts import parse_tool_result

# --- from test_literature.py: Literature tests.
# --- from test_abstention_a1l.py: A-1L: surface sparse_raw_score; live hybrid default finds T5. No MiniLM. No gold needles.
PAPER = "aa" * 32


IDENT_FIELDS = (
    "chunk_id", "sparse_score", "dense_score", "sparse_raw_score",
    "section_boost", "final_score",
)


#: A stand-in, non-BGE encoder for the generic ranking tests, and the library they search.
FIXTURE_ENCODER = "fixture/encoder"
FIXTURE_KB = "fixture-lib"


def _no_reranker_snapshot():
    """Tests never download the reranker; a test that needs it installs one."""
    raise OSError("no reranker snapshot in tests")


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
        "knowledgebase": FIXTURE_KB,
        "documents": [{"document_id": "d1", "sha256": PAPER, "title": "Fixture"}],
        "chunks": chunks,
        "dense": {
            "model": FIXTURE_ENCODER,
            "dim": 384,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [_unit(1.0) for _ in chunks],
            "refuse_rule": "sparse_gated",
        },
    }


def _fake_encoder():
    def fake(texts, model_name=None):
        return FIXTURE_ENCODER, [_unit(1.0) for _ in texts]

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
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder())
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
    payload = parse_tool_result(research.search_literature_corpus(query, knowledgebase=FIXTURE_KB, top_k=top_k))
    assert payload["data"]["success"] is True
    assert payload["data"]["retrieval_mode"] == "hybrid"
    assert payload["data"]["knowledgebase"] == FIXTURE_KB
    live = payload["data"]["results"]
    assert _projection(live) == _projection(direct)
    assert [row["chunk_id"] for row in live] == [row["chunk_id"] for row in direct]


def test_a1l_sparse_mode_ident_and_refuse_skips_encoder(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("A-1L sparse refuse must not load the encoder")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    index = _index([_chunk("c-a", "alpha methods solvent")])
    research._save_index(index)
    empty = parse_tool_result(research.search_literature_corpus(
        "xylophone quokka zzzyx", knowledgebase=FIXTURE_KB, retrieval_mode="sparse",
    ))
    assert empty["data"]["success"] is True
    assert empty["data"]["result_count"] == 0
    assert empty["data"]["results"] == []
    direct = research._search_index(index, "xylophone quokka zzzyx", 5, "sparse")
    assert direct == []
    assert research._search_index(index, "xylophone quokka zzzyx", 5, "hybrid") == []


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


def test_a2a4_floor_refuses_before_encoder_in_all_three_modes(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("A-2A4 floor must refuse before the encoder")

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


def test_a2a4_no_floor_skips_encoder_on_invented_tokens(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("invented tokens must not load the encoder")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    index = _index([_chunk("c-a", "alpha methods solvent")])
    assert research._search_index(index, "xylophone quokka zzzyx", 5, "hybrid") == []
    assert research.coverage_star(index, "xylophone quokka zzzyx") == 0.0


def test_a2a4_payload_coverage_star_on_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder())
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    index = _index([_chunk("c-a", "alpha methods solvent")])
    research._save_index(index)
    payload = parse_tool_result(research.search_literature_corpus("alpha", knowledgebase=FIXTURE_KB, retrieval_mode="sparse"))
    assert payload["data"]["success"] is True
    assert "coverage_star" in payload["data"]
    assert "floor" in payload["data"]
    assert payload["data"]["results"][0]["query_idf_coverage"] >= 0.0
    assert "floor" in payload["data"]["results"][0]


def test_a2a4_payload_echoes_index_floor_on_every_result(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder())
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    index = _index([_chunk("c-a", "alpha methods solvent")])
    index["abstention"] = {
        "statistic": "query_idf_coverage",
        "percentile": 5,
        "floor": 0.1,
    }
    research._save_index(index)
    payload = parse_tool_result(research.search_literature_corpus("alpha", knowledgebase=FIXTURE_KB, retrieval_mode="sparse"))
    assert payload["data"]["success"] is True
    assert payload["data"]["floor"] == 0.1
    assert payload["data"]["results"]
    for row in payload["data"]["results"]:
        assert row["floor"] == 0.1
        assert row["query_idf_coverage"] >= 0.1


# --- from test_canonical_document.py: C2: one-paper canonical document. Tiny fixtures. Not the probe PDF.
_HEADING_FILL = research._HEADING_FILL_KEY


_TEMP_RE = research._CANONICAL_TEMPERATURE_RE


# The corpus audit tree records how the shipped corpus was measured. It is not published, so its tests skip elsewhere.
AUDIT = Path.home() / "dissolve-v12-audit"
needs_audit = pytest.mark.skipif(not AUDIT.is_dir(), reason=f"reads the unpublished corpus audit tree {AUDIT}")
_MEASURE_SCRIPTS = tuple(
    AUDIT / "one_paper_experiment" / name for name in ("measure_step2.py", "measure_step3_pypdf.py", "measure_parse.py")
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


@needs_audit
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
    # the chunk plumbing is under test, not the encoder: a stand-in that answers as the pinned BGE model
    monkeypatch.setattr(research, "_dense_vectors", lambda texts, model_name=None: (
        BGE_MODEL, [_unit_embedding_recipe(BGE_DIM, 0) for _ in texts]))
    path = tmp_path / "canonical_document.v1.json"
    path.write_text(json.dumps(canonical), encoding="utf-8")
    return research._ingest_inputs([str(path)], [], knowledgebase, True, 4)


def _search_rows(payload: dict) -> list[dict]:
    data = payload.get("data") or payload
    value = data.get("results")
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value
    return []


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


def test_build_index_refuses_a_substituted_model(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", lambda texts, model_name=None: ("other/model", [[1.0] * 384 for _ in texts]))
    with pytest.raises(research.LiteratureContractError):
        corpus.build_index([(SHA_A, _paper(SHA_A, "alphaword"))], corpus.BASE_KB)


def test_build_index_refuses_misshapen_vectors(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", lambda texts, model_name=None: (model_name, [[1.0] * 3 for _ in texts]))
    with pytest.raises(research.LiteratureContractError):
        corpus.build_index([(SHA_A, _paper(SHA_A, "alphaword"))], corpus.BASE_KB)


@pytest.fixture
def pdfs(tmp_path, monkeypatch):
    """Three papers on disk; Docling is replaced by their canonical documents."""
    docs, paths = {}, []
    for token in ("alphaword", "betaword", "gammaword"):
        path = tmp_path / "papers" / f"{token}.pdf"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(token.encode())
        docs[str(path)] = _paper(hashlib.sha256(token.encode()).hexdigest(), token)
        paths.append(path)
    monkeypatch.setattr(corpus, "canonical_from_file", lambda path: docs[str(path)])
    return paths


def _release_shas(release: Path) -> set[str]:
    return {doc["sha256"] for doc in corpus.read_release(release)[1]["documents"]}


def _canonical_source(tmp_path: Path, sha: str, token: str) -> Path:
    path = tmp_path / f"{token}.v1.json"
    path.write_text(json.dumps({**_paper(sha, token), "schema": research._CANONICAL_DOCUMENT_SCHEMA}), encoding="utf-8")
    return path


def test_build_writes_a_release_the_agent_serves(tmp_path, monkeypatch, embeds, pdfs):
    manifest = corpus.build(pdfs[:2], tmp_path / "release")
    assert len(embeds) == 2, "one embedding batch per paper"
    assert (manifest["n_documents"], manifest["dense"]["model"]) == (2, research._BGE_MODEL_ID)
    assert manifest["abstention"] == {"floor": corpus.ABSTENTION["floor"]}
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(tmp_path / "release" / "manifest.json"))
    index = research._load_index(corpus.BASE_KB)
    assert {row["paper_sha256"] for row in index["chunks"]} == _release_shas(tmp_path / "release")
    for token in ("alphaword", "betaword"):
        rows = research._search_index(index, token, 3, "sparse")
        assert rows and token in rows[0]["excerpt"]


def test_add_seeds_the_working_release_and_skips_papers_it_holds(tmp_path, monkeypatch, embeds, pdfs):
    shipped = tmp_path / "shipped"
    corpus.build(pdfs[:1], shipped)
    monkeypatch.setattr(corpus, "SHIPPED", shipped)
    embeds.clear()
    manifest = corpus.add(pdfs, tmp_path / "working")
    assert len(embeds) == 2, "the paper it already holds is skipped; each new paper is its own batch"
    assert manifest["n_documents"] == 3
    assert len(_release_shas(shipped)) == 1, "the shipped release is never written"


def test_verify_compares_releases_chunk_by_chunk(tmp_path, embeds, pdfs):
    corpus.build(pdfs[:2], tmp_path / "ours")
    corpus.build(pdfs[:2], tmp_path / "theirs")
    assert corpus.verify(tmp_path / "ours", tmp_path / "theirs")["differing"] == []
    _manifest, index = corpus.read_release(tmp_path / "theirs")
    index["chunks"][0]["sha256"] = "0" * 64
    corpus.write_release(tmp_path / "theirs", index)
    assert corpus.verify(tmp_path / "ours", tmp_path / "theirs")["differing"] == [index["chunks"][0]["chunk_id"]]


def test_ingest_builds_a_library_with_the_corpus_recipe(tmp_path, monkeypatch, embeds):
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path / "home"))
    source = _canonical_source(tmp_path, SHA_A, "alphaword")
    data = parse_tool_result(research.ingest_literature_documents(paths=[str(source)], knowledgebase="mine"))["data"]
    assert data["success"] is True and data["chunker"] == f"T5 block pack, target {corpus.T5_TARGET}"
    assert len(embeds) == 1
    index = research._load_index("mine")
    expected = corpus.chunk_records(_paper(SHA_A, "alphaword"), SHA_A)
    assert [corpus._public(row) for row in index["chunks"]] == [corpus._public(row) for row in expected]
    assert index["documents"][0]["title"] == index["chunks"][0]["title"] == "alphaword.v1"
    assert index["abstention"] == {"floor": corpus.ABSTENTION["floor"]}


def test_ingest_into_the_product_grows_the_working_release(tmp_path, monkeypatch, embeds):
    shipped = tmp_path / "shipped"
    corpus.write_release(shipped, corpus.build_index([(SHA_A, _paper(SHA_A, "alphaword"))], corpus.BASE_KB))
    monkeypatch.setattr(corpus, "SHIPPED", shipped)
    monkeypatch.setattr(research, "_SHIPPED_CORPUS_DIR", shipped)
    monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path / "working"))
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    result = research._ingest_inputs([str(_canonical_source(tmp_path, SHA_B, "betaword"))], [], corpus.BASE_KB, False, 5)
    assert (result["documents_added"], result["document_count"]) == (1, 2)
    assert research._declared_bge10_manifest_path() == tmp_path / "working" / "manifest.json"
    assert {doc["sha256"] for doc in research._load_index(corpus.BASE_KB)["documents"]} == {SHA_A, SHA_B}
    assert _release_shas(shipped) == {SHA_A}, "the shipped release is never written"


def test_ingest_refuses_a_pinned_release_and_an_older_recipe(tmp_path, monkeypatch, embeds):
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(tmp_path / "pinned" / "manifest.json"))
    with pytest.raises(research.LiteratureContractError) as pinned:
        research._ingest_inputs([], [], corpus.BASE_KB, False, 5)
    assert pinned.value.code == "protected_serving_index"
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path / "home"))
    research._save_index({
        "schema": research._INDEX_SCHEMA, "knowledgebase": "old-lib", "documents": [{"sha256": SHA_C}],
        "chunks": [{"chunk_id": "K1", "sha256": "0" * 64, "text": "legacy"}], "dense": None,
    })
    with pytest.raises(research.LiteratureContractError) as older:
        research._ingest_inputs([], [], "old-lib", False, 5)
    assert older.value.code == "ingest_recipe_mismatch"


def test_ingest_reports_an_unsupported_file_and_keeps_going(tmp_path, monkeypatch, embeds):
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path / "home"))
    unsupported = tmp_path / "notes.docx"
    unsupported.write_bytes(b"x")
    result = research._ingest_inputs(
        [str(_canonical_source(tmp_path, SHA_A, "alphaword")), str(unsupported)], [], "mix", False, 5,
    )
    assert result["documents_added"] == 1
    assert result["failures"] == ["notes.docx: unsupported document type .docx"]


@pytest.mark.skipif(not os.getenv("DISSOLVE_CORPUS_REFERENCE"), reason="set DISSOLVE_CORPUS_REFERENCE to a directory with canonical/")
def test_known_answer_every_shipped_chunk_reproduces_from_its_canonical_document():
    """DISSOLVE_CORPUS_REFERENCE/canonical/<sha>.v1.json for each shipped paper. No encoder is needed."""
    canonical_dir = Path(os.environ["DISSOLVE_CORPUS_REFERENCE"]) / "canonical"
    shipped = corpus.read_release(corpus.SHIPPED)[1]
    theirs = {row["chunk_id"]: corpus._public(row) for row in shipped["chunks"]}
    ours = {}
    for sha in {row["paper_sha256"] for row in shipped["chunks"]}:
        canonical = json.loads((canonical_dir / f"{sha}.v1.json").read_text(encoding="utf-8"))
        ours.update({row["chunk_id"]: corpus._public(row) for row in corpus.chunk_records(canonical, sha)})
    assert len(theirs) == 1841
    assert ours == theirs


# --- from test_corpus_canonical.py: C3: 21 canonical documents. Not C8. Loads persist; does not call Docling.
CENSUS = AUDIT / "corpus/CENSUS.v1.json"
MANIFEST = AUDIT / "corpus/CANONICAL_MANIFEST.v1.json"
CANON_DIR = AUDIT / "corpus/canonical"
PARSED_DIR = AUDIT / "corpus/parsed"


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


@needs_audit
def test_manifest_sha_set_equals_census_indexed_union_held_out():
    manifest = _manifest()
    shas = [row["pdf_sha256"] for row in manifest["documents"]]
    assert manifest["n_documents"] == 21
    assert len(shas) == 21
    assert len(set(shas)) == 21
    assert set(shas) == _census_in_scope()
    assert PATENT_SHA not in set(shas)


@needs_audit
def test_every_row_is_docling_with_null_fallback():
    for row in _manifest()["documents"]:
        assert row["parser_backend"] == "docling"
        assert row["fallback_reason"] is None
        assert row["peak_rss_bytes"] > 0
        assert row["wall_s"] > 0
        assert row["mem_available_before_bytes"] >= (
            research.PEAK_RSS_CEILING_BYTES + research.PEAK_RSS_HEADROOM_BYTES
        )


@needs_audit
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


@needs_audit
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


@needs_audit
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


@needs_audit
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
        "knowledgebase": FIXTURE_KB,
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
            "model": FIXTURE_ENCODER,
            "dim": 384,
            "chunk_ids": recorded,
            "vectors": [_unit(1.0), _unit(0.0)],
            "refuse_rule": "sparse_gated",
        },
    }


def _fake_encoder_dense_retrieval_d1(seen, model_id=FIXTURE_ENCODER):
    def fake(texts, model_name=None):
        seen.extend(list(texts))
        return model_id, [_unit(1.0) for _ in texts]

    return fake


def test_d1_hybrid_does_not_raise_dense_unavailable(monkeypatch):
    seen = []
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder_dense_retrieval_d1(seen))
    rows = research._search_index(_index_dense_retrieval_d1(), PLANT, 5, "hybrid")
    assert rows
    assert rows[0]["chunk_id"] == "c-0001"
    assert seen == [PLANT]


def test_d1_chunk_id_set_identity_not_count(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder_dense_retrieval_d1([]))
    broken = _index_dense_retrieval_d1(dense_ids=["c-0001", "c-OTHER"])
    try:
        research._search_index(broken, PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("count-matched wrong chunk_id set must fail")


def test_d1_loaded_model_must_match_index(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder_dense_retrieval_d1([], model_id="not-the-index-model"))
    try:
        research._search_index(_index_dense_retrieval_d1(), PLANT, 5, "hybrid")
    except ValueError as error:
        assert str(error) == "dense_index_unavailable"
    else:
        raise AssertionError("model id mismatch must fail")


def test_d1_served_components_sum_to_final(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder_dense_retrieval_d1([]))
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
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder_dense_retrieval_d1([]))
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    research._save_index(_index_dense_retrieval_d1())
    payload = parse_tool_result(research.search_literature_corpus(
        PLANT, knowledgebase=FIXTURE_KB, retrieval_mode="hybrid",
    ))
    assert payload["data"]["success"] is True
    assert payload["data"].get("error_code") != "dense_index_unavailable"
    assert payload["data"]["refuse_rule"] == "sparse_gated"
    assert payload["data"]["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    assert "recall" not in json.dumps(payload)
    empty = parse_tool_result(research.search_literature_corpus(
        ABSENT_SUBJECT, knowledgebase=FIXTURE_KB, retrieval_mode="hybrid",
    ))
    assert empty["data"]["success"] is True
    assert empty["data"]["result_count"] == 0
    assert empty["data"]["results"] == []
    assert empty["data"]["refuse_rule"] == "sparse_gated"


def test_d1_hybrid_does_not_admit_zero_sparse_cosine_hit(monkeypatch):
    """Dense may reorder BM25 admits. It must not admit a BM25 reject."""
    aligned = [0.0, 1.0] + [0.0] * 382

    def fake(texts, model_name=None):
        return FIXTURE_ENCODER, [aligned for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    index = _index_dense_retrieval_d1()
    index["dense"]["vectors"] = [_unit(1.0), aligned]
    rows = research._search_index(index, PLANT, 5, "hybrid")
    assert [row["chunk_id"] for row in rows] == ["c-0001"]
    assert rows[0]["sparse_score"] > 0
    dense_rows = research._search_index(index, PLANT, 5, "dense")
    assert [row["chunk_id"] for row in dense_rows] == ["c-0001"]


def test_d1_missing_chunk_ids_is_unavailable(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_encoder_dense_retrieval_d1([]))
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
    monkeypatch.setattr(research, "_pypdf_bridge", count_pypdf)
    monkeypatch.setattr(research, "_parse", count_cascade)

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
    monkeypatch.setattr(research, "_pypdf_bridge", _pypdf_bridge_stub)

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
    monkeypatch.setattr(research, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        research._parse(_acquire_production_parse(pdf))
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
    monkeypatch.setattr(research, "_pypdf_bridge", count_pypdf)

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
    monkeypatch.setattr(research, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        research._parse(_acquire_production_parse(pdf))
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
    monkeypatch.setattr(research, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        research._parse(_acquire_production_parse(pdf))
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
    monkeypatch.setattr(research, "_pypdf_bridge", count_pypdf)

    parsed = research._parse(_acquire_production_parse(pdf))
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
    monkeypatch.setattr(research, "_pypdf_bridge", count_pypdf)
    return calls


@pytest.mark.parametrize(
    "filename, content, backend",
    [
        pytest.param(
            "probe.xml",
            "<article><article-title>Suffix probe</article-title><p>jats body text for the identity check</p></article>",
            "jats",
            id="xml_jats_suffix_and_does_not_call_fallbacks",
        ),
        pytest.param(
            "probe.shtml",
            "<p>shtml body text for the identity check</p>\n",
            "local_text",
            id="shtml_as_local_text_not_docling",
        ),
        pytest.param(
            "probe.notajats",
            "odd suffix body for the identity check\n",
            "local_text",
            id="unlisted_suffix_without_allowlist_membership",
        ),
        pytest.param(
            "probe.htm",
            "<p>htm body text for the identity check</p>\n",
            "local_text",
            id="htm_as_local_text_not_docling",
        ),
        pytest.param(
            "probe.html",
            "<p>html body text for the identity check</p>\n",
            "local_text",
            id="html_as_local_text_not_docling",
        ),
        pytest.param(
            "probe.xhtml",
            "<article><article-title>XHTML probe</article-title><p>xhtml body text for the identity check</p></article>",
            "jats",
            id="xhtml_as_jats_not_docling",
        ),
        pytest.param(
            "probe.nxml",
            "<article><article-title>NXML probe</article-title><p>nxml body text for the identity check</p></article>",
            "jats",
            id="nxml_as_jats_not_local_text",
        ),
        pytest.param(
            "probe.txt",
            "local text body for the identity check\n",
            "local_text",
            id="txt_local_text_suffix_and_does_not_call_fallbacks",
        ),
        pytest.param(
            "probe.md",
            "markdown body for the identity check\n",
            "local_text",
            id="md_local_text_suffix_and_does_not_call_fallbacks",
        ),
    ],
)
def test_production_parse_refuses_non_docling_suffix(monkeypatch, tmp_path, filename, content, backend):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)
    with pytest.raises(research.LiteratureContractError) as caught:
        research._parse(_acquire_production_parse(path))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == backend
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

    monkeypatch.setattr(research, "_jats_bridge", lie_bridge)

    with pytest.raises(research.LiteratureContractError) as caught:
        research._parse(_acquire_production_parse(xml))
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


def _t5_table_fixture() -> dict:
    """The table fixture with a table longer than the T5 target, so it is a chunk of its own; only such
    chunks carry rebound in the served recipe (9 of the 1,841 shipped chunks, all tables)."""
    canonical = _fixture()
    table = next(block for block in canonical["blocks"] if block["kind"] == "table")
    pad = corpus.T5_TARGET + 100 - (table["char_end"] - table["char_start"])
    old_text = canonical["canonical_text"][table["char_start"]:table["char_end"]]
    new_text = old_text.replace("[/TABLE]", "| x | y |\n" * (pad // 10) + "[/TABLE]")
    shift = len(new_text) - len(old_text)
    canonical["canonical_text"] = canonical["canonical_text"].replace(old_text, new_text, 1)
    for block in canonical["blocks"]:
        if block["char_start"] > table["char_start"]:
            block["char_start"] += shift
            block["char_end"] += shift
    table["char_end"] += shift
    record = canonical["tables"][0]
    record["char_end"] = table["char_end"]
    return canonical


def _ingest_t5_fixture(monkeypatch, tmp_path, seen: list[str], sparse_text=None) -> tuple[dict, dict]:
    def fake(texts, model_name=None):
        seen.extend(texts)
        return model_name, [[1.0] + [0.0] * (research._BGE_DIM - 1) for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    if sparse_text is not None:
        monkeypatch.setattr(research, "chunk_sparse_corpus", sparse_text)
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    canonical = _t5_table_fixture()
    path = tmp_path / "canonical_document.v1.json"
    path.write_text(json.dumps(canonical), encoding="utf-8")
    research._ingest_inputs([str(path)], [], "rebound", True, 4)
    return canonical, research._load_index("rebound")


def _table_chunk(index: dict, canonical: dict) -> dict:
    table = canonical["tables"][0]
    (chunk,) = [c for c in index["chunks"] if c["char_start"] <= table["char_start"] and table["char_end"] <= c["char_end"]]
    return chunk


def test_t5_table_chunk_keeps_its_span_and_rebinds_the_footnote(monkeypatch, tmp_path):
    canonical, index = _ingest_t5_fixture(monkeypatch, tmp_path, [])
    chunk = _table_chunk(index, canonical)
    assert chunk["body"] == canonical["canonical_text"][chunk["char_start"]:chunk["char_end"]]
    assert TOKEN_V in chunk["body"] and TOKEN_NOTE not in chunk["body"]
    assert TOKEN_NOTE in chunk["body_plus_rebound"]


def test_t5_ranks_and_embeds_a_table_on_its_rebound(monkeypatch, tmp_path):
    seen: list[str] = []
    canonical, index = _ingest_t5_fixture(monkeypatch, tmp_path, seen)
    rows = research._search_index(index, TOKEN_NOTE, 5, "sparse")
    assert any(TOKEN_V in str(row.get("excerpt") or "") for row in rows)
    position = index["chunks"].index(_table_chunk(index, canonical))
    assert TOKEN_NOTE in seen[position]


def test_t5_body_only_text_drops_the_rebound(monkeypatch, tmp_path):
    seen: list[str] = []
    canonical, index = _ingest_t5_fixture(monkeypatch, tmp_path, seen, sparse_text=research._chunk_body_text)
    rows = research._search_index(index, TOKEN_NOTE, 5, "sparse")
    assert not any(TOKEN_V in str(row.get("excerpt") or "") for row in rows)
    position = index["chunks"].index(_table_chunk(index, canonical))
    assert TOKEN_NOTE not in seen[position]


def _table_sparse_score(query, chunks, corpus_of, table) -> float:
    rows = [{"text": corpus_of(chunk)} for chunk in chunks]
    scores = research._bm25(research._tokens(query), rows)
    for score, chunk in zip(scores, chunks):
        if chunk.get("char_start") == table["char_start"] and chunk.get("char_end") == table["char_end"]:
            return float(score)
    return 0.0


# --- from test_bge_fusion.py: Synthetic BGE10 fusion and public retrieval-diagnostic tests. No real corpus.
PRODUCT_KB = "t5-indexed-unsealed"


INDEPENDENT_KB = "synth-user-lib"


QUERY = "zympoly"


UNCHANGED_FLOOR = 0.3698406656908355


MATCH_TEXT = "zympoly solventblend"


NOMATCH_TEXT = "solventblend only"


MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _zscores(values: list[float]) -> list[float]:
    sequence = [float(value) for value in values]
    if len(sequence) < 2:
        return [0.0] * len(sequence)
    variance = statistics.pvariance(sequence)
    if variance <= 0.0:
        return [0.0] * len(sequence)
    mean = statistics.fmean(sequence)
    scale = math.sqrt(variance)
    return [(value - mean) / scale for value in sequence]


def _chunk_bge_fusion(
    chunk_id: str,
    *,
    title: str = "",
    section: str = "",
    text: str = MATCH_TEXT,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "title": title,
        "section": section,
        "text": text,
        "body": text,
        "source": "synthetic-local",
    }


def _bge_dense(chunk_ids: list[str]) -> dict:
    return {
        "model": research._BGE_MODEL_ID,
        "dim": research._BGE_DIM,
        "query_instruction": research._BGE_QUERY_INSTRUCTION,
        "passage_instruction": research._BGE_PASSAGE_INSTRUCTION,
        "encoder_revision": research._BGE_ENCODER_REVISION,
        "chunk_ids": list(chunk_ids),
        "vectors": [[0.0] * research._BGE_DIM for _ in chunk_ids],
    }


def _minilm_dense(chunk_ids: list[str]) -> dict:
    return {
        "model": MINILM_MODEL,
        "dim": 384,
        "chunk_ids": list(chunk_ids),
        "vectors": [[0.0] * 384 for _ in chunk_ids],
    }


def _index_bge_fusion(
    chunks: list[dict],
    *,
    knowledgebase: str = PRODUCT_KB,
    identity: str = "bge",
    floor: float | None = None,
) -> dict:
    ids = [str(chunk["chunk_id"]) for chunk in chunks]
    payload = {
        "schema": "dissolve.literature-index.v1",
        "knowledgebase": knowledgebase,
        "documents": [],
        "chunks": chunks,
        "dense": _bge_dense(ids) if identity == "bge" else _minilm_dense(ids),
    }
    if floor is not None:
        payload["abstention"] = {
            "statistic": "query_idf_coverage",
            "percentile": 5,
            "floor": floor,
            "calibrated_at": "2020-01-01T00:00:00+00:00",
            "note": "synthetic-gate",
        }
    return payload


def _ids(rows: list[dict]) -> list[str]:
    return [str(row["chunk_id"]) for row in rows]


def _install_scores(monkeypatch, dense, sparse):
    dense_calls: list[str] = []
    sparse_calls: list[str] = []
    rerank_captures: list[list[str]] = []

    def fake_dense(index, chunks, query):
        dense_calls.append(query)
        return [float(value) for value in dense]

    def fake_sparse(query, rows):
        sparse_calls.append(query)
        return [float(value) for value in sparse]

    def fake_reorder(query, ranked, rerank_mode="off"):
        rerank_captures.append([item[4]["chunk_id"] for item in ranked])
        return ranked

    monkeypatch.setattr(research, "_dense_query_scores", fake_dense)
    monkeypatch.setattr(research, "_query_sparse_raw", fake_sparse)
    monkeypatch.setattr(research, "reorder_window", fake_reorder)
    return dense_calls, sparse_calls, rerank_captures


def _search(index, monkeypatch, dense, sparse, *, top_k=10, mode="hybrid", **kwargs):
    counters = _install_scores(monkeypatch, dense, sparse)
    rows = research._search_index(index, QUERY, top_k, mode, **kwargs)
    return rows, counters


def _public(monkeypatch, index, *, top_k=5, mode="hybrid", knowledgebase=PRODUCT_KB):
    monkeypatch.setattr(research, "_load_index", lambda kb: index)
    return parse_tool_result(
        research.search_literature_corpus(
            QUERY,
            knowledgebase=knowledgebase,
            top_k=top_k,
            retrieval_mode=mode,
        )
    )


def _three(*, abstract=False, ids=None, titles=None, texts=None):
    ids = ids or ["c0", "c1", "c2"]
    titles = titles or ["", "", ""]
    texts = texts or [MATCH_TEXT, MATCH_TEXT, MATCH_TEXT]
    chunks = []
    for i, chunk_id in enumerate(ids):
        section = "abstract" if abstract and i == 0 else ""
        chunks.append(_chunk_bge_fusion(chunk_id, title=titles[i], section=section, text=texts[i]))
    return chunks


class TestBgeFusion:
    """Synthetic BGE10 fusion and public retrieval-diagnostic tests. No real corpus."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path / "working-corpus"))
        monkeypatch.setattr(research, "_pinned_reranker_snapshot", _no_reranker_snapshot)
        monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        monkeypatch.setattr(research, "_research_root", lambda: tmp_path / "research-home")
        monkeypatch.setattr(research, "_committed_lexicon", lambda: [])
        monkeypatch.setattr(
            research,
            "_dense_vectors",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model forbidden")),
        )
        yield tmp_path

    def test_section_removal_uses_zscore_not_boost(self, monkeypatch):
        index = _index_bge_fusion(_three(abstract=True))
        rows, counters = _search(index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
        assert _ids(rows) == ["c2", "c1", "c0"]
        assert [row["section_boost"] for row in rows] == [0.0, 0.0, 0.0]
        z_dense = _zscores([0.10, 0.11, 0.12])
        assert [row["dense_score"] for row in rows] == [round(z_dense[i], 6) for i in (2, 1, 0)]
        assert [row["sparse_score"] for row in rows] == [0.0, 0.0, 0.0]
        assert counters[0] and counters[2]

    def test_zscore_vs_max_normalization_order(self, monkeypatch):
        index = _index_bge_fusion(_three())
        rows, _counters = _search(index, monkeypatch, [0.1, 0.2, 0.3], [3.0, 2.0, 1.0])
        assert _ids(rows) == ["c2", "c1", "c0"]
        assert rows[2]["final_score"] < 0.0
        assert set(_ids(rows)) == {"c0", "c1", "c2"}

    def test_population_excludes_ineligible_sparse_zero(self, monkeypatch):
        chunks = _three() + [_chunk_bge_fusion("c3")]
        index = _index_bge_fusion(chunks)
        rows, _counters = _search(
            index,
            monkeypatch,
            [0.9, 0.2, 0.1, 999.0],
            [1.0, 2.0, 3.0, 0.0],
        )
        assert _ids(rows) == ["c0", "c2", "c1"]
        assert "c3" not in _ids(rows)
        eligible_dense = _zscores([0.9, 0.2, 0.1])
        eligible_sparse = _zscores([1.0, 2.0, 3.0])
        assert rows[0]["dense_score"] == round(eligible_dense[0], 6)
        assert rows[0]["sparse_score"] == round(eligible_sparse[0], 6)

    def test_constant_channels_lexicographic_chunk_id(self, monkeypatch):
        index = _index_bge_fusion(_three(ids=["c2", "c1", "c0"]))
        rows, _counters = _search(index, monkeypatch, [0.4, 0.4, 0.4], [2.0, 2.0, 2.0])
        assert _ids(rows) == ["c0", "c1", "c2"]
        assert [row["dense_score"] for row in rows] == [0.0, 0.0, 0.0]
        assert [row["sparse_score"] for row in rows] == [0.0, 0.0, 0.0]

    def test_gate_first_below_unchanged_floor(self, monkeypatch):
        index = _index_bge_fusion(
            _three(texts=[NOMATCH_TEXT, NOMATCH_TEXT, NOMATCH_TEXT]),
            floor=UNCHANGED_FLOOR,
        )
        rows, counters = _search(index, monkeypatch, [0.9, 0.8, 0.7], [1.0, 1.0, 1.0])
        assert rows == []
        assert counters[0] == []
        assert counters[2] == []
        parsed = _public(monkeypatch, index, mode="hybrid")
        data = parsed["data"]
        assert data["success"] is True
        assert data["result_count"] == 0
        assert data["reason"] == "abstained_below_floor"
        assert data["floor"] == round(UNCHANGED_FLOOR, 6)

    def test_singleton_zero_standardized_components(self, monkeypatch):
        index = _index_bge_fusion([_chunk_bge_fusion("c0")])
        rows, _counters = _search(index, monkeypatch, [0.77], [4.2], top_k=5)
        assert _ids(rows) == ["c0"]
        assert rows[0]["dense_score"] == 0.0
        assert rows[0]["sparse_score"] == 0.0
        assert rows[0]["section_boost"] == 0.0
        assert rows[0]["final_score"] == 0.0

    def test_empty_corpus_reason_without_model_calls(self, monkeypatch):
        empty = {
            "schema": "dissolve.literature-index.v1",
            "knowledgebase": PRODUCT_KB,
            "documents": [],
            "chunks": [],
            "dense": None,
        }
        dense_calls, sparse_calls, rerank_captures = _install_scores(monkeypatch, [], [])
        parsed = _public(monkeypatch, empty)
        data = parsed["data"]
        assert data["success"] is False
        assert data["error_code"] == "empty_corpus"
        assert data["reason"] == "empty_corpus"
        assert dense_calls == []
        assert rerank_captures == []
        assert sparse_calls == []

    def test_no_sparse_match_without_floor(self, monkeypatch):
        index = _index_bge_fusion(_three())
        rows, counters = _search(index, monkeypatch, [0.9, 0.8, 0.7], [0.0, 0.0, 0.0])
        assert rows == []
        assert counters[0] == []
        assert counters[2] == []
        parsed = _public(monkeypatch, index)
        data = parsed["data"]
        assert data["success"] is True
        assert data["result_count"] == 0
        assert data["reason"] == "no_sparse_match"
        assert "results" in data
        assert data["results"] == []

    def test_gate_first_when_floor_and_no_sparse_coincide(self, monkeypatch):
        index = _index_bge_fusion(_three(), floor=UNCHANGED_FLOOR)
        _install_scores(monkeypatch, [0.9, 0.8, 0.7], [0.0, 0.0, 0.0])
        parsed = _public(monkeypatch, index)
        assert parsed["data"]["reason"] == "abstained_below_floor"
        assert parsed["data"]["result_count"] == 0

    def test_public_depths_five_ten_twenty(self, monkeypatch):
        chunks = [_chunk_bge_fusion(f"c{i:02d}") for i in range(25)]
        index = _index_bge_fusion(chunks)
        dense = [float(i) for i in range(25)]
        sparse = [1.0] * 25
        _install_scores(monkeypatch, dense, sparse)
        for depth in (5, 10, 20):
            parsed = _public(monkeypatch, index, top_k=depth)
            data = parsed["data"]
            assert data["success"] is True
            assert data["result_count"] == depth
            assert len(data["results"]) == depth
            assert "reason" not in data

    def test_dense_clip_before_standardization_changes_order(self, monkeypatch):
        index = _index_bge_fusion(_three())
        rows, _counters = _search(index, monkeypatch, [-1.0, 0.0, 0.2], [1.0, 4.0, 1.0])
        assert _ids(rows) == ["c2", "c1", "c0"]
        clipped = _zscores([0.0, 0.0, 0.2])
        assert rows[0]["dense_score"] == round(clipped[2], 6)

    def test_title_secondary_key_on_tied_scores(self, monkeypatch):
        index = _index_bge_fusion(_three(titles=["m", "a", "z"]))
        rows, _counters = _search(index, monkeypatch, [0.5, 0.5, 0.5], [1.0, 1.0, 1.0])
        assert _ids(rows) == ["c1", "c0", "c2"]

    def test_zero_variance_and_negative_fused_tail_kept(self, monkeypatch):
        index = _index_bge_fusion(_three())
        rows, _counters = _search(index, monkeypatch, [0.1, 0.2, 0.3], [3.0, 2.0, 1.0], top_k=3)
        assert _ids(rows) == ["c2", "c1", "c0"]
        assert rows[-1]["final_score"] < 0.0
        assert len(rows) == 3

    def test_population_includes_outlier_outside_top_twenty(self, monkeypatch):
        chunks = [_chunk_bge_fusion(f"c{i:02d}") for i in range(21)]
        index = _index_bge_fusion(chunks)
        dense = [float(i) for i in range(21)]
        sparse = [1.0] * 21
        rows, counters = _search(index, monkeypatch, dense, sparse, top_k=20)
        assert len(rows) == 20
        assert "c00" not in _ids(rows)
        assert rows[0]["chunk_id"] == "c20"
        expected = _zscores(dense)
        assert rows[0]["dense_score"] == round(expected[20], 6)
        assert rows[0]["dense_score"] != round(_zscores(dense[1:])[-1], 6)
        assert counters[2][0] == [f"c{i:02d}" for i in range(20, -1, -1)]

    def test_gate_equality_is_not_abstention(self, monkeypatch):
        equal = _index_bge_fusion(_three(), floor=1.0)
        rows, counters = _search(equal, monkeypatch, [0.3, 0.2, 0.1], [1.0, 1.0, 1.0])
        assert _ids(rows) == ["c0", "c1", "c2"]
        assert counters[0]
        strict = _index_bge_fusion(_three(), floor=1.0000001)
        empty, counters_strict = _search(
            strict, monkeypatch, [0.3, 0.2, 0.1], [1.0, 1.0, 1.0],
        )
        assert empty == []
        assert counters_strict[0] == []

    def test_bge_recipe_library_is_fused_like_the_product(self, monkeypatch):
        product = _index_bge_fusion(_three(abstract=True))
        library = _index_bge_fusion(_three(abstract=True), knowledgebase=INDEPENDENT_KB)
        fused, _counters = _search(product, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
        rows, _counters = _search(library, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
        assert _ids(rows) == _ids(fused)
        assert [row["final_score"] for row in rows] == [row["final_score"] for row in fused]
        assert rows[0]["section_boost"] == 0.0

    def test_non_bge_vectors_keep_the_generic_ranker(self, monkeypatch):
        index = _index_bge_fusion(_three(abstract=True), identity="minilm")
        rows, _counters = _search(index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
        assert _ids(rows) == ["c0", "c2", "c1"]
        assert rows[0]["section_boost"] == 0.05

    def test_legacy_sparse_mode_keeps_section_boost_on_bge_identity(self, monkeypatch):
        chunks = [_chunk_bge_fusion("c0"), _chunk_bge_fusion("c1"), _chunk_bge_fusion("c2", section="abstract")]
        index = _index_bge_fusion(chunks)
        rows, counters = _search(
            index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0], mode="sparse",
        )
        assert _ids(rows) == ["c2", "c0", "c1"]
        assert rows[0]["section_boost"] == 0.05
        assert counters[0] == []

    def test_explicit_weights_still_apply_on_bge_path(self, monkeypatch):
        index = _index_bge_fusion(_three())
        rows, _counters = _search(
            index,
            monkeypatch,
            [0.1, 0.2, 0.3],
            [3.0, 2.0, 1.0],
            w_dense=0.0,
            w_sparse=1.0,
        )
        assert _ids(rows) == ["c0", "c1", "c2"]
        z_sparse = _zscores([3.0, 2.0, 1.0])
        assert rows[0]["sparse_score"] == round(z_sparse[0], 6)
        assert rows[0]["section_boost"] == 0.0


# --- from test_bge_reranker.py: Synthetic BGE pair-reranker and RRF60 tests. No real model or cache.
BGE_QUERY_PREFIX = research._BGE_QUERY_INSTRUCTION


PINNED_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"


def _rrf(rank_before: int, rank_pair: int) -> float:
    return 1.0 / (60 + rank_before) + 1.0 / (60 + rank_pair)


def _chunk_bge_reranker(
    chunk_id: str,
    *,
    title: str = "",
    section: str = "",
    text: str = MATCH_TEXT,
    body: str | None = None,
    caption: str = "",
    body_plus_rebound: str = "",
) -> dict:
    payload = {
        "chunk_id": chunk_id,
        "title": title,
        "section": section,
        "text": text,
        "body": MATCH_TEXT if body is None else body,
        "caption": caption,
        "source": "synthetic-local",
    }
    if body_plus_rebound:
        payload["body_plus_rebound"] = body_plus_rebound
    return payload


def _row(chunk_id: str, **chunk_fields) -> tuple:
    return (0.0, 0.0, 0.0, 0.0, _chunk_bge_reranker(chunk_id, **chunk_fields), 0.0)


def _index_bge_reranker(
    chunks: list[dict],
    *,
    knowledgebase: str = PRODUCT_KB,
    identity: str = "bge",
    floor: float | None = None,
) -> dict:
    ids = [str(chunk["chunk_id"]) for chunk in chunks]
    payload = {
        "schema": "dissolve.literature-index.v1",
        "knowledgebase": knowledgebase,
        "documents": [],
        "chunks": chunks,
        "dense": _bge_dense(ids) if identity == "bge" else _minilm_dense(ids),
    }
    if floor is not None:
        payload["abstention"] = {
            "statistic": "query_idf_coverage",
            "percentile": 5,
            "floor": floor,
            "calibrated_at": "2020-01-01T00:00:00+00:00",
            "note": "synthetic-gate",
        }
    return payload


def _ranked_ids(ranked: list[tuple]) -> list[str]:
    return [item[4]["chunk_id"] for item in ranked]


class FakeScalar:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class FakeTensor:
    def __init__(self, rows, dtype):
        self._rows = rows
        self.shape = (len(rows), len(rows[0]) if rows else 0)
        self.dtype = dtype
        self.ndim = 2

    def __getitem__(self, idx):
        if not isinstance(idx, tuple) or len(idx) != 2:
            raise TypeError("expected pair index")
        return FakeScalar(self._rows[idx[0]][idx[1]])

    def to(self, device):
        return self


class FakeTokenizer:
    encode_calls: list

    def __init__(self, owner):
        self.owner = owner

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        cls.owner_ref.load_calls.append(("tokenizer", args, kwargs))
        return cls(cls.owner_ref)

    def __call__(self, queries, passages, **kwargs):
        type(self).encode_calls.append((list(queries), list(passages), dict(kwargs)))
        return {"input_ids": FakeTensor([[1] for _ in passages], self.owner.torch.float32)}


class FakeModel:
    def __init__(self, owner, *, model_type="xlm-roberta", num_labels=1):
        self.owner = owner
        self.config = SimpleNamespace(model_type=model_type, num_labels=num_labels)
        self.calls = []

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        cls.owner_ref.load_calls.append(("model", args, kwargs))
        spec = cls.owner_ref.model_spec
        return cls(
            cls.owner_ref,
            model_type=spec.get("model_type", "xlm-roberta"),
            num_labels=spec.get("num_labels", 1),
        )

    def to(self, device):
        self.calls.append(("to", device))
        return self

    def float(self):
        self.calls.append(("float",))
        return self

    def eval(self):
        self.calls.append(("eval",))
        return self

    def __call__(self, **kwargs):
        self.calls.append(("forward", kwargs))
        batch = 1
        if "input_ids" in kwargs:
            batch = kwargs["input_ids"].shape[0]
        rows = self.owner.next_logits(batch)
        return SimpleNamespace(logits=FakeTensor(rows, self.owner.torch.float32))


class FakeTorch:
    def __init__(self):
        self.float32 = object()
        self.thread_calls: list[int] = []
        self.interop_calls: list[int] = []
        self._interop = None
        self.inference_calls = 0

    def set_num_threads(self, count):
        self.thread_calls.append(int(count))

    def set_num_interop_threads(self, count):
        if self._interop is not None:
            raise RuntimeError("illegal interop reset")
        self._interop = int(count)
        self.interop_calls.append(int(count))

    def get_num_interop_threads(self):
        return 1 if self._interop is None else self._interop

    def inference_mode(self):
        self.inference_calls += 1
        return nullcontext()


class PairBackend:
    def __init__(self, logits_by_passage=None, logit_batches=None, model_spec=None):
        self.torch = FakeTorch()
        self.load_calls: list[tuple] = []
        self.logits_by_passage = logits_by_passage or {}
        self.logit_batches = list(logit_batches or [])
        self.model_spec = model_spec or {}
        self._batch_i = 0
        FakeTokenizer.owner_ref = self
        FakeTokenizer.encode_calls = []
        FakeModel.owner_ref = self
        self.transformers = SimpleNamespace(
            AutoTokenizer=FakeTokenizer,
            AutoModelForSequenceClassification=FakeModel,
        )

    def next_logits(self, batch_count: int):
        if self.logit_batches:
            rows = self.logit_batches[self._batch_i]
            self._batch_i += 1
            return rows
        encoded = FakeTokenizer.encode_calls[-1]
        passages = encoded[1]
        rows = []
        for passage in passages:
            rows.append([float(self.logits_by_passage.get(passage, 0.0))])
        if len(rows) != batch_count:
            rows = [[0.0] for _ in range(batch_count)]
        return rows


def _reset_pair_state():
    research._PAIR_BACKEND = None
    research._PAIR_INTEROP_READY = False


def _touch_pair_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name in research.PAIR_RERANKER_FILE_SHA256:
        (path / name).write_bytes(b"synthetic-pair-artifact")
    return path


def _pass_hashes(monkeypatch):
    monkeypatch.setattr(
        research,
        "_hash_pair_file",
        lambda path: research.PAIR_RERANKER_FILE_SHA256[Path(path).name],
    )


def _install_backend(monkeypatch, tmp_path, backend: PairBackend) -> PairBackend:
    pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
    monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
    _pass_hashes(monkeypatch)
    original = research.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            return backend.torch
        if name == "transformers":
            return backend.transformers
        return original(name, *args, **kwargs)

    monkeypatch.setattr(research.importlib, "import_module", fake_import)
    return backend


def _install_scores_bge_reranker(monkeypatch, dense, sparse):
    dense_calls: list[str] = []
    sparse_calls: list[str] = []

    def fake_dense(index, chunks, query):
        dense_calls.append(query)
        return [float(value) for value in dense]

    def fake_sparse(query, rows):
        sparse_calls.append(query)
        return [float(value) for value in sparse]

    monkeypatch.setattr(research, "_dense_query_scores", fake_dense)
    monkeypatch.setattr(research, "_query_sparse_raw", fake_sparse)
    return dense_calls, sparse_calls


class TestBgeReranker:
    """Synthetic BGE pair-reranker and RRF60 tests. No real model or cache."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path / "working-corpus"))
        monkeypatch.setattr(research, "_pinned_reranker_snapshot", _no_reranker_snapshot)
        _reset_pair_state()
        monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        monkeypatch.delenv("DISSOLVE_BGE_RERANKER_DIR", raising=False)
        monkeypatch.setattr(research, "_research_root", lambda: tmp_path / "research-home")
        monkeypatch.setattr(research, "_committed_lexicon", lambda: [])
        monkeypatch.setattr(
            research,
            "_dense_vectors",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model forbidden")),
        )
        yield tmp_path
        _reset_pair_state()

    def test_rrf_empty_and_known_orders(self):
        assert research.fuse_rrf60([], []) == []
        tied = research.fuse_rrf60([_row("a"), _row("b")], [_row("b"), _row("a")])
        assert _ranked_ids(tied) == ["a", "b"]
        assert tied[0][0] == _rrf(1, 2)
        assert tied[1][0] == _rrf(2, 1)
        assert tied[0][0] == tied[1][0]
        flipped = research.fuse_rrf60(
            [_row("a"), _row("b"), _row("c")],
            [_row("c"), _row("b"), _row("a")],
        )
        assert _ranked_ids(flipped) == ["a", "c", "b"]
        assert flipped[0][0] == _rrf(1, 3)
        assert flipped[1][0] == _rrf(3, 1)
        assert flipped[2][0] == _rrf(2, 2)
        assert flipped[0][0] == flipped[1][0]
        assert flipped[0][0] > flipped[2][0]

    def test_rrf_membership_mismatch_and_duplicates_fail(self):
        with pytest.raises(ValueError, match="rrf_membership"):
            research.fuse_rrf60([_row("a"), _row("b")], [_row("a")])
        with pytest.raises(ValueError, match="rrf_membership"):
            research.fuse_rrf60([_row("a"), _row("b")], [_row("a"), _row("c")])
        with pytest.raises(ValueError, match="rrf_duplicate"):
            research.fuse_rrf60([_row("a"), _row("a")], [_row("a"), _row("b")])
        with pytest.raises(ValueError, match="rrf_duplicate"):
            research.fuse_rrf60([_row("a"), _row("b")], [_row("b"), _row("b")])

    def test_pair_equal_logits_keep_original_order(self, monkeypatch, tmp_path):
        backend = PairBackend(logits_by_passage={"zb": 0.5, "aa": 0.5})
        _install_backend(monkeypatch, tmp_path, backend)
        ranked = [_row("z", body="zb"), _row("a", body="aa")]
        out = research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert _ranked_ids(out) == ["z", "a"]

    def test_pair_tiny_logit_delta_beats_rounding(self, monkeypatch, tmp_path):
        backend = PairBackend(logits_by_passage={"left": 1.0, "right": 1.0 + 1e-8})
        _install_backend(monkeypatch, tmp_path, backend)
        ranked = [_row("m", body="left"), _row("a", body="right")]
        out = research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert _ranked_ids(out) == ["a", "m"]
        assert out[0][0] - out[1][0] == pytest.approx(1e-8)

    @pytest.mark.parametrize(
        "rows",
        [
            [[math.nan], [0.0]],
            [[math.inf], [0.0]],
            [[-math.inf], [0.0]],
        ],
    )
    def test_pair_nonfinite_logits_fail_closed(self, monkeypatch, tmp_path, rows):
        backend = PairBackend(logit_batches=[rows])
        _install_backend(monkeypatch, tmp_path, backend)
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, [_row("a"), _row("b")], research.PAIR_RERANK_MODE)

    def test_pair_wrong_logit_shapes_fail_closed(self, monkeypatch, tmp_path):
        ranked = [_row("a"), _row("b")]

        class WideModel(FakeModel):
            def __call__(self, **kwargs):
                return SimpleNamespace(
                    logits=FakeTensor([[0.0, 1.0], [0.0, 1.0]], self.owner.torch.float32)
                )

        backend = _install_backend(monkeypatch, tmp_path, PairBackend())
        backend.transformers.AutoModelForSequenceClassification = type(
            "Wide",
            (WideModel,),
            {"owner_ref": backend},
        )
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)

        _reset_pair_state()

        class FlatTensor:
            shape = (2,)
            dtype = backend.torch.float32

            def __getitem__(self, idx):
                raise AssertionError("1d logits must fail before scoring")

        class FlatModel(FakeModel):
            def __call__(self, **kwargs):
                return SimpleNamespace(logits=FlatTensor())

        backend.transformers.AutoModelForSequenceClassification = type(
            "Flat",
            (FlatModel,),
            {"owner_ref": backend},
        )
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)

        _reset_pair_state()

        class CubeTensor:
            shape = (2, 1, 1)
            dtype = backend.torch.float32

        class CubeModel(FakeModel):
            def __call__(self, **kwargs):
                return SimpleNamespace(logits=CubeTensor())

        backend.transformers.AutoModelForSequenceClassification = type(
            "Cube",
            (CubeModel,),
            {"owner_ref": backend},
        )
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)

    def test_window_twenty_batches_and_rest_excluded(self, monkeypatch, tmp_path):
        chunks = [_chunk_bge_reranker(f"c{i:02d}", body=f"p{i:02d}") for i in range(25)]
        index = _index_bge_reranker(chunks)
        dense = [float(i) for i in range(25)]
        sparse = [1.0] * 25
        _install_scores_bge_reranker(monkeypatch, dense, sparse)
        logits = {f"p{i:02d}": float(i) for i in range(25)}
        backend = PairBackend(logits_by_passage=logits)
        _install_backend(monkeypatch, tmp_path, backend)
        rrf_calls: list[tuple[list[str], list[str]]] = []
        real_fuse = research.fuse_rrf60

        def spy_fuse(before, pair):
            rrf_calls.append((_ranked_ids(list(before)), _ranked_ids(list(pair))))
            return real_fuse(before, pair)

        monkeypatch.setattr(research, "fuse_rrf60", spy_fuse)
        rows = research._search_index(
            index, QUERY, 5, "hybrid", rerank_mode=research.PAIR_RERANK_MODE,
        )
        scored_passages = [passage for _q, passages, _kw in FakeTokenizer.encode_calls for passage in passages]
        assert [len(call[1]) for call in FakeTokenizer.encode_calls] == [8, 8, 4]
        assert scored_passages == [f"p{i:02d}" for i in range(24, 4, -1)]
        assert "p04" not in scored_passages
        assert rrf_calls and len(rrf_calls[0][0]) == 20
        assert rrf_calls[0][0] == [f"c{i:02d}" for i in range(24, 4, -1)]
        assert set(rrf_calls[0][0]) == set(rrf_calls[0][1])
        assert "c04" not in rrf_calls[0][0]
        assert len(rows) == 5
        assert "c04" not in _ids(rows)
        assert "c00" not in _ids(rows)

    def test_loader_records_and_single_init(self, monkeypatch, tmp_path):
        pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
        backend = PairBackend(logits_by_passage={MATCH_TEXT: 1.0})
        _install_backend(monkeypatch, tmp_path, backend)
        hash_calls: list[str] = []

        def counted_hash(path):
            hash_calls.append(Path(path).name)
            return research.PAIR_RERANKER_FILE_SHA256[Path(path).name]

        monkeypatch.setattr(research, "_hash_pair_file", counted_hash)
        ranked = [_row("c0"), _row("c1")]
        research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        tokenizer_loads = [call for call in backend.load_calls if call[0] == "tokenizer"]
        model_loads = [call for call in backend.load_calls if call[0] == "model"]
        assert len(tokenizer_loads) == 1
        assert len(model_loads) == 1
        for _kind, args, kwargs in backend.load_calls:
            assert args[0] == str(pair_dir.resolve())
            assert kwargs["revision"] == PINNED_REVISION
            assert kwargs["local_files_only"] is True
        assert tokenizer_loads[0][2]["use_fast"] is True
        assert tokenizer_loads[0][2]["truncation_side"] == "right"
        assert model_loads[0][2]["torch_dtype"] is backend.torch.float32
        assert backend.torch.thread_calls == [8]
        assert backend.torch.interop_calls == [1]
        encode_kwargs = FakeTokenizer.encode_calls[0][2]
        assert encode_kwargs["padding"] is True
        assert encode_kwargs["truncation"] == "longest_first"
        assert encode_kwargs["max_length"] == 512
        assert encode_kwargs["return_tensors"] == "pt"
        assert hash_calls == list(research.PAIR_RERANKER_FILE_SHA256)
        model = research._PAIR_BACKEND[1]
        assert model.calls[0] == ("to", "cpu")
        assert ("float",) in model.calls
        assert ("eval",) in model.calls

    def test_failed_init_retry_rehashes_changed_digest(self, monkeypatch, tmp_path):
        pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
        monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
        hash_calls: list[str] = []
        allow = {"ok": True}
        import_calls: list[str] = []

        def counted_hash(path):
            hash_calls.append(Path(path).name)
            if not allow["ok"]:
                return "0" * 64
            return research.PAIR_RERANKER_FILE_SHA256[Path(path).name]

        def fail_import(name, *args, **kwargs):
            import_calls.append(name)
            raise ImportError("synthetic import failure")

        monkeypatch.setattr(research, "_hash_pair_file", counted_hash)
        monkeypatch.setattr(research.importlib, "import_module", fail_import)
        ranked = [_row("a")]
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert research._PAIR_BACKEND is None
        assert hash_calls == list(research.PAIR_RERANKER_FILE_SHA256)
        assert import_calls == ["torch"]
        allow["ok"] = False
        hash_calls.clear()
        import_calls.clear()
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert hash_calls == ["config.json"]
        assert import_calls == []
        assert research._PAIR_BACKEND is None

    def test_cached_backend_rejects_changed_or_missing_directory(self, monkeypatch, tmp_path):
        backend = PairBackend(logits_by_passage={MATCH_TEXT: 1.0})
        pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
        _install_backend(monkeypatch, tmp_path, backend)
        ranked = [_row("c0")]
        research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert research._PAIR_BACKEND is not None
        loads = len(backend.load_calls)
        encodes = len(FakeTokenizer.encode_calls)
        other = _touch_pair_dir(tmp_path / "other-pair")
        hashed_roots: list[str] = []

        def selective_hash(path):
            hashed_roots.append(str(Path(path).resolve().parent))
            if Path(path).resolve().parent == other.resolve():
                return "0" * 64
            return research.PAIR_RERANKER_FILE_SHA256[Path(path).name]

        monkeypatch.setattr(research, "_hash_pair_file", selective_hash)
        monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(other))
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert hashed_roots and hashed_roots[0] == str(other.resolve())
        assert len(backend.load_calls) == loads
        assert len(FakeTokenizer.encode_calls) == encodes
        assert research._PAIR_BACKEND is None

        monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
        research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert research._PAIR_BACKEND is not None
        recached_loads = len(backend.load_calls)
        recached_encodes = len(FakeTokenizer.encode_calls)
        assert recached_loads > loads
        monkeypatch.delenv("DISSOLVE_BGE_RERANKER_DIR", raising=False)
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, ranked, research.PAIR_RERANK_MODE)
        assert len(backend.load_calls) == recached_loads
        assert len(FakeTokenizer.encode_calls) == recached_encodes
        assert research._PAIR_BACKEND is None

    def test_missing_and_mismatched_artifacts_fail_closed(self, monkeypatch, tmp_path):
        missing = tmp_path / "missing"
        missing.mkdir()
        monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(missing))
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, [_row("a")], research.PAIR_RERANK_MODE)
        pair_dir = _touch_pair_dir(tmp_path / "bad-hash")
        monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, [_row("a")], research.PAIR_RERANK_MODE)
        monkeypatch.delenv("DISSOLVE_BGE_RERANKER_DIR", raising=False)
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, [_row("a")], research.PAIR_RERANK_MODE)

    def test_incompatible_config_blocks_and_no_other_reranker_exists(self, monkeypatch, tmp_path):
        backend = PairBackend(model_spec={"model_type": "bert", "num_labels": 1})
        _install_backend(monkeypatch, tmp_path, backend)
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, [_row("a")], research.PAIR_RERANK_MODE)
        assert research._PAIR_BACKEND is None
        _reset_pair_state()
        backend = PairBackend(model_spec={"model_type": "xlm-roberta", "num_labels": 2})
        _install_backend(monkeypatch, tmp_path, backend)
        with pytest.raises(research.RerankBlocked):
            research.reorder_window(QUERY, [_row("a")], research.PAIR_RERANK_MODE)
        with pytest.raises(ValueError, match="unknown_rerank_mode"):
            research.reorder_window(QUERY, [_row("a")], "cross_encoder")

    def test_public_product_invokes_pair_and_rrf(self, monkeypatch, tmp_path):
        chunks = [
            _chunk_bge_reranker("a", body="pa"),
            _chunk_bge_reranker("b", body="pb"),
            _chunk_bge_reranker("c", body="pc"),
        ]
        index = _index_bge_reranker(chunks)
        _install_scores_bge_reranker(monkeypatch, [0.3, 0.2, 0.1], [1.0, 1.0, 1.0])
        backend = PairBackend(logits_by_passage={"pa": 0.0, "pb": 1.0, "pc": 2.0})
        _install_backend(monkeypatch, tmp_path, backend)
        rrf_calls: list[tuple[list[str], list[str]]] = []
        real_fuse = research.fuse_rrf60

        def spy_fuse(before, pair):
            rrf_calls.append((_ranked_ids(list(before)), _ranked_ids(list(pair))))
            return real_fuse(before, pair)

        monkeypatch.setattr(research, "fuse_rrf60", spy_fuse)
        parsed = _public(monkeypatch, index, top_k=3)
        data = parsed["data"]
        assert data["success"] is True
        assert _ids(data["results"]) == ["a", "c", "b"]
        assert rrf_calls[0][0] == ["a", "b", "c"]
        assert rrf_calls[0][1] == ["c", "b", "a"]
        expected = _rrf(1, 3)
        assert data["results"][0]["final_score"] == round(expected, 6)
        assert data["results"][0]["final_score"] == data["results"][1]["final_score"]
        assert FakeTokenizer.encode_calls
        assert backend.load_calls

    def test_bge_recipe_library_invokes_pair_and_rrf(self, monkeypatch, tmp_path):
        chunks = [
            _chunk_bge_reranker("a", body="pa"),
            _chunk_bge_reranker("b", body="pb"),
            _chunk_bge_reranker("c", body="pc"),
        ]
        index = _index_bge_reranker(chunks, knowledgebase=INDEPENDENT_KB)
        _install_scores_bge_reranker(monkeypatch, [0.3, 0.2, 0.1], [1.0, 1.0, 1.0])
        backend = PairBackend(logits_by_passage={"pa": 0.0, "pb": 1.0, "pc": 2.0})
        _install_backend(monkeypatch, tmp_path, backend)
        rrf_calls: list[tuple[list[str], list[str]]] = []
        real_fuse = research.fuse_rrf60

        def spy_fuse(before, pair):
            rrf_calls.append((_ranked_ids(list(before)), _ranked_ids(list(pair))))
            return real_fuse(before, pair)

        monkeypatch.setattr(research, "fuse_rrf60", spy_fuse)
        parsed = _public(monkeypatch, index, top_k=3, knowledgebase=INDEPENDENT_KB)
        data = parsed["data"]
        assert data["success"] is True
        assert _ids(data["results"]) == ["a", "c", "b"]
        assert rrf_calls[0][0] == ["a", "b", "c"]
        assert rrf_calls[0][1] == ["c", "b", "a"]
        expected = _rrf(1, 3)
        assert data["results"][0]["final_score"] == round(expected, 6)
        assert data["results"][0]["final_score"] == data["results"][1]["final_score"]
        assert FakeTokenizer.encode_calls
        assert backend.load_calls

    def test_default_reranker_is_the_pinned_snapshot(self, monkeypatch, tmp_path):
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        monkeypatch.delenv("DISSOLVE_BGE_RERANKER_DIR", raising=False)
        monkeypatch.setattr(research, "_pinned_reranker_snapshot", lambda: snapshot)
        assert research._pair_dir() == snapshot
        monkeypatch.setattr(research, "_pinned_reranker_snapshot", _no_reranker_snapshot)
        with pytest.raises(research.RerankBlocked):
            research._pair_dir()
        monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", " ")
        with pytest.raises(research.RerankBlocked):
            research._pair_dir()

    def test_gate_and_non_bge_library_skip_pair(self, monkeypatch, tmp_path):
        backend = PairBackend(logits_by_passage={MATCH_TEXT: 9.0})
        _install_backend(monkeypatch, tmp_path, backend)
        rrf_calls: list = []
        monkeypatch.setattr(
            research,
            "fuse_rrf60",
            lambda *args, **kwargs: rrf_calls.append(args) or (_ for _ in ()).throw(AssertionError("rrf")),
        )

        gated = _index_bge_reranker(
            [_chunk_bge_reranker("c0", text=NOMATCH_TEXT, body=NOMATCH_TEXT),
             _chunk_bge_reranker("c1", text=NOMATCH_TEXT, body=NOMATCH_TEXT),
             _chunk_bge_reranker("c2", text=NOMATCH_TEXT, body=NOMATCH_TEXT)],
            floor=UNCHANGED_FLOOR,
        )
        _install_scores_bge_reranker(monkeypatch, [0.9, 0.8, 0.7], [1.0, 1.0, 1.0])
        parsed = _public(monkeypatch, gated)
        assert parsed["data"]["reason"] == "abstained_below_floor"
        assert parsed["data"]["result_count"] == 0
        assert FakeTokenizer.encode_calls == []
        assert rrf_calls == []
        assert backend.load_calls == []

        other = _index_bge_reranker([_chunk_bge_reranker("c0"), _chunk_bge_reranker("c1"), _chunk_bge_reranker("c2")], knowledgebase=INDEPENDENT_KB, identity="minilm")
        _install_scores_bge_reranker(monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
        parsed = _public(monkeypatch, other, top_k=3, knowledgebase=INDEPENDENT_KB)
        assert parsed["data"]["success"] is True
        assert FakeTokenizer.encode_calls == []
        assert rrf_calls == []

    def test_public_depths_and_no_sparse_reason(self, monkeypatch, tmp_path):
        chunks = [_chunk_bge_reranker(f"c{i:02d}", body=f"p{i:02d}") for i in range(25)]
        index = _index_bge_reranker(chunks)
        dense = [float(i) for i in range(25)]
        sparse = [1.0] * 25
        _install_scores_bge_reranker(monkeypatch, dense, sparse)
        backend = PairBackend(logits_by_passage={f"p{i:02d}": 0.0 for i in range(25)})
        _install_backend(monkeypatch, tmp_path, backend)
        for depth in (5, 10, 20):
            FakeTokenizer.encode_calls.clear()
            parsed = _public(monkeypatch, index, top_k=depth)
            data = parsed["data"]
            assert data["success"] is True
            assert data["result_count"] == depth
            assert len(data["results"]) == depth
            assert "reason" not in data
            scored = [p for _q, passages, _kw in FakeTokenizer.encode_calls for p in passages]
            assert len(scored) == 20
        empty_sparse = _index_bge_reranker([_chunk_bge_reranker("c0"), _chunk_bge_reranker("c1"), _chunk_bge_reranker("c2")])
        _install_scores_bge_reranker(monkeypatch, [0.9, 0.8, 0.7], [0.0, 0.0, 0.0])
        FakeTokenizer.encode_calls.clear()
        parsed = _public(monkeypatch, empty_sparse)
        assert parsed["data"]["reason"] == "no_sparse_match"
        assert FakeTokenizer.encode_calls == []

    def test_canonical_passage_and_query_has_no_prefix(self, monkeypatch, tmp_path):
        chunk = _chunk_bge_reranker(
            "disc",
            text="TEXT-ONLY",
            body="BODY-ONLY",
            caption="CAPTION-ONLY",
            body_plus_rebound="REBOUND-ONLY",
        )
        expected = research.chunk_sparse_corpus(chunk)
        assert expected == "REBOUND-ONLY"
        assert expected != "BODY-ONLY"
        backend = PairBackend(logits_by_passage={expected: 1.5, "BODY-ONLY": 9.0, "TEXT-ONLY": 8.0})
        _install_backend(monkeypatch, tmp_path, backend)
        out = research.reorder_window(QUERY, [(0.0, 0.0, 0.0, 0.0, chunk, 0.0)], research.PAIR_RERANK_MODE)
        assert FakeTokenizer.encode_calls
        queries, passages, _kwargs = FakeTokenizer.encode_calls[0]
        assert passages == [expected]
        assert queries == [QUERY]
        assert not any(text.startswith(BGE_QUERY_PREFIX) for text in queries)
        assert BGE_QUERY_PREFIX not in queries[0]
        assert out[0][0] == 1.5


# --- from test_embedding_recipe.py: Synthetic encoder identity and in-memory embedding tests. No real model.
MINILM_DIM = 384


BGE_MODEL = research._BGE_MODEL_ID


BGE_DIM = research._BGE_DIM


BGE_REVISION = research._BGE_ENCODER_REVISION


BGE_QUERY = research._BGE_QUERY_INSTRUCTION


CHUNK_A = "synth-embed-a"


CHUNK_B = "synth-embed-b"


TEXT_A = "zympoly passage solventblend"


TEXT_B = "helioxane passage solventblend"


KB = "synth-embed-lib"


def _unit_embedding_recipe(dim: int, axis: int) -> list[float]:
    row = [0.0] * dim
    row[axis] = 1.0
    return row


def _chunk_embedding_recipe(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "body": text,
        "sha256": f"sha-{chunk_id}",
        "document_id": "D-synth",
        "title": "Synthetic embed",
        "source": "synthetic-local",
    }


def _index_embedding_recipe() -> dict:
    return {
        "schema": "dissolve.literature-index.v1",
        "knowledgebase": KB,
        "documents": [{"document_id": "D-synth", "sha256": "doc-synth", "title": "Synthetic embed"}],
        "chunks": [_chunk_embedding_recipe(CHUNK_A, TEXT_A), _chunk_embedding_recipe(CHUNK_B, TEXT_B)],
        "dense": None,
    }


def _prepared() -> list[str]:
    return [research.chunk_sparse_corpus(chunk) for chunk in _index_embedding_recipe()["chunks"]]


class FakeSentenceTransformer:
    calls: list[tuple[str, tuple, dict]]

    def __init__(self, *args, **kwargs):
        type(self).calls.append(("init", args, kwargs))
        fail = getattr(type(self), "fail_init", False)
        if fail:
            raise RuntimeError("synthetic load failure")

    def encode(self, texts, **kwargs):
        type(self).calls.append(("encode", (texts,), kwargs))
        if getattr(type(self), "fail_encode", False):
            raise RuntimeError("synthetic encode failure")
        output = getattr(type(self), "output")
        if callable(output):
            return output(texts)
        return output


def _install_fake_st(monkeypatch, *, output, fail_init=False, fail_encode=False):
    FakeSentenceTransformer.calls = []
    FakeSentenceTransformer.fail_init = fail_init
    FakeSentenceTransformer.fail_encode = fail_encode
    FakeSentenceTransformer.output = output
    module = SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
    original = research.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            return module
        return original(name, *args, **kwargs)

    monkeypatch.setattr(research.importlib, "import_module", fake_import)
    return FakeSentenceTransformer


def _init_calls(fake):
    return [item for item in fake.calls if item[0] == "init"]


def _encode_calls(fake):
    return [item for item in fake.calls if item[0] == "encode"]


PASSAGE_A = TEXT_A + " " + "a" * 800
PASSAGE_B = TEXT_B + " " + "b" * 800


def _two_chunk_canonical() -> dict:
    """Two blocks that T5 cannot pack into one chunk."""
    text = PASSAGE_A + "\n\n" + PASSAGE_B
    return {
        "schema": research._CANONICAL_DOCUMENT_SCHEMA,
        "source_pdf_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "canonical_text": text,
        "blocks": [
            {"block_id": "b0", "char_start": 0, "char_end": len(PASSAGE_A), "kind": "text", "page": 1},
            {"block_id": "b1", "char_start": len(PASSAGE_A) + 2, "char_end": len(text), "kind": "text", "page": 2},
        ],
        "tables": [],
    }


def _run_ingest(monkeypatch, tmp_path, *, model_id, vectors, save_calls):
    """Ingest one two-chunk canonical document into a fresh library with a stand-in encoder."""
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path / "research-home"))
    canonical = _two_chunk_canonical()
    source = tmp_path / "paper.v1.json"
    source.write_text(json.dumps(canonical), encoding="utf-8")
    seen_texts: list[list[str]] = []

    def fake_dense(texts, model_name=None):
        seen_texts.append(list(texts))
        return model_id, copy.deepcopy(vectors)

    def fake_save(index):
        save_calls.append(copy.deepcopy(index))
        return tmp_path / "not-written.json.gz"

    monkeypatch.setattr(research, "_dense_vectors", fake_dense)
    monkeypatch.setattr(research, "_save_index", fake_save)
    return research._ingest_inputs([str(source)], [], KB, False, 20), seen_texts, canonical


def _fault_vectors(kind: str, dim: int) -> list[list[float]]:
    good = [_unit_embedding_recipe(dim, 0), _unit_embedding_recipe(dim, 1)]
    if kind == "too_few":
        return [good[0]]
    if kind == "too_many":
        return [good[0], good[1], _unit_embedding_recipe(dim, 2 % dim)]
    if kind == "ragged":
        return [good[0], good[1][:-1]]
    if kind == "zero":
        return [good[0], [0.0] * dim]
    if kind == "non_unit":
        row = list(good[1])
        row[1] = 2.0
        return [good[0], row]
    if kind == "nan":
        row = list(good[1])
        row[0] = math.nan
        return [good[0], row]
    if kind == "inf":
        row = list(good[1])
        row[0] = math.inf
        return [good[0], row]
    raise AssertionError(kind)


class TestEmbeddingRecipe:
    """Synthetic encoder identity and in-memory embedding tests. No real model."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path / "working-corpus"))
        monkeypatch.setattr(research, "_pinned_reranker_snapshot", _no_reranker_snapshot)
        monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        yield {"tmp_path": tmp_path}

    def test_bge_constructor_pins_revision_and_cpu(self, monkeypatch):
        prepared = list(_prepared())
        fake = _install_fake_st(monkeypatch, output=[_unit_embedding_recipe(BGE_DIM, 0), _unit_embedding_recipe(BGE_DIM, 1)])
        model_id, vectors = research._dense_vectors(prepared, BGE_MODEL)
        inits = _init_calls(fake)
        encodes = _encode_calls(fake)
        assert model_id == BGE_MODEL
        assert len(inits) == 1
        assert inits[0][1] == (BGE_MODEL,)
        assert inits[0][2] == {"revision": BGE_REVISION, "device": "cpu"}
        assert len(encodes) == 1
        received = encodes[0][1][0]
        assert [item.encode("utf-8") for item in received] == [item.encode("utf-8") for item in prepared]
        assert encodes[0][2] == {"normalize_embeddings": True, "show_progress_bar": False}
        assert all(not item.startswith(BGE_QUERY) for item in received)
        assert vectors == [_unit_embedding_recipe(BGE_DIM, 0), _unit_embedding_recipe(BGE_DIM, 1)]
        assert inits[0][1][0] != MINILM_MODEL

    def test_output_rounds_to_eight_decimals(self, monkeypatch):
        raw = 0.123456789123
        _install_fake_st(monkeypatch, output=[[raw, 0.0]])
        _model_id, vectors = research._dense_vectors(["alpha"], MINILM_MODEL)
        assert vectors == [[round(raw, 8), 0.0]]
        assert vectors[0][0] == 0.12345679

    def test_no_argument_selects_the_pinned_bge_encoder(self, monkeypatch):
        fake = _install_fake_st(monkeypatch, output=[_unit_embedding_recipe(BGE_DIM, 0)])
        model_id, _vectors = research._dense_vectors(["alpha"])
        inits = _init_calls(fake)
        assert model_id == BGE_MODEL
        assert inits[0][1] == (BGE_MODEL,)
        assert inits[0][2] == {"revision": BGE_REVISION, "device": "cpu"}

    def test_load_failure_does_not_retry_or_substitute(self, monkeypatch):
        fake = _install_fake_st(monkeypatch, output=[[1.0]], fail_init=True)
        with pytest.raises(RuntimeError, match="could not be loaded or evaluated"):
            research._dense_vectors(["alpha"], BGE_MODEL)
        assert len(_init_calls(fake)) == 1
        assert _encode_calls(fake) == []
        assert _init_calls(fake)[0][1] == (BGE_MODEL,)

    def test_encode_failure_does_not_retry(self, monkeypatch):
        fake = _install_fake_st(monkeypatch, output=[[1.0]], fail_encode=True)
        with pytest.raises(RuntimeError, match="could not be loaded or evaluated"):
            research._dense_vectors(["alpha"], BGE_MODEL)
        assert len(_init_calls(fake)) == 1
        assert len(_encode_calls(fake)) == 1

    def test_ingest_embeds_one_paper_with_the_pinned_bge_recipe(self, monkeypatch, tmp_path):
        saves: list[dict] = []
        result, seen_texts, canonical = _run_ingest(
            monkeypatch, tmp_path, model_id=BGE_MODEL,
            vectors=[_unit_embedding_recipe(BGE_DIM, 0), _unit_embedding_recipe(BGE_DIM, 1)], save_calls=saves,
        )
        assert result["encoder"] == BGE_MODEL and result["documents_added"] == 1
        assert seen_texts == [[PASSAGE_A, PASSAGE_B]], "one batch per paper; passages carry no query instruction"
        (saved,) = saves
        sha = canonical["source_pdf_sha256"]
        dense = saved["dense"]
        assert (dense["model"], dense["dim"], dense["encoder_revision"]) == (BGE_MODEL, BGE_DIM, BGE_REVISION)
        assert dense["query_instruction"] == BGE_QUERY and dense["passage_instruction"] == ""
        assert dense["chunk_ids"] == [f"T5-{sha[:12]}-0001", f"T5-{sha[:12]}-0002"]
        assert saved["abstention"] == {"floor": corpus.ABSTENTION["floor"]}

    @pytest.mark.parametrize(
        "kind",
        ["too_few", "too_many", "ragged", "zero", "non_unit", "nan", "inf"],
    )
    def test_ingest_malformed_vectors_do_not_save(self, kind, monkeypatch, tmp_path):
        saves: list[dict] = []
        result, _seen, _canonical = _run_ingest(
            monkeypatch, tmp_path, model_id=BGE_MODEL, vectors=_fault_vectors(kind, BGE_DIM), save_calls=saves,
        )
        assert result["documents_added"] == 0 and saves == []
        (failure,) = result["failures"]
        for leaked in (TEXT_A, TEXT_B, "injected"):
            assert leaked not in failure


# --- from test_manifest_failclosed.py: Synthetic fail-closed tests for canonical product manifest and sidecar load.
_OMIT = object()


SIDECAR_KB = "t5-promoted-unsealed"


INDEX_SCHEMA = "dissolve.literature-index.v1"


PRODUCT_CHUNK = "synth-product-chunk"


SIDECAR_CHUNK = "synth-sidecar-chunk"


INDEPENDENT_CHUNK = "synth-independent-chunk"


PRODUCT_TEXT = "zympoly product solventblend"


SIDECAR_TEXT = "helioxane sidecar solventblend"


INDEPENDENT_TEXT = "zympoly independent solventblend"


UNION_FLOOR = 0.25


LEAK_MARKERS = (
    "JSONDecodeError",
    "OSError",
    "PermissionError",
    "BadGzipFile",
    "UnicodeDecodeError",
    "OverflowError",
    "zlib.error",
    "Expecting value",
    "Not a gzipped file",
    "codec can't decode",
    "invalid start byte",
    "injected",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(paths: list[Path]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in paths:
        key = str(path)
        if path.is_file():
            out[key] = _sha256(path)
        elif path.exists():
            out[key] = "exists"
        else:
            out[key] = None
    return out


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_gzip_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _make_index(knowledgebase: str, chunk_id: str, text: str, doc_key: str) -> dict:
    return {
        "schema": INDEX_SCHEMA,
        "knowledgebase": knowledgebase,
        "documents": [{
            "document_id": f"D-{doc_key}",
            "sha256": hashlib.sha256(doc_key.encode("utf-8")).hexdigest(),
            "title": f"Synthetic {doc_key}",
            "source": "synthetic-local",
        }],
        "chunks": [{
            "chunk_id": chunk_id,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "document_id": f"D-{doc_key}",
            "title": f"Synthetic {doc_key}",
            "source": "synthetic-local",
            "text": text,
            "body": text,
        }],
        "dense": None,
    }


def _abstention(floor: float) -> dict:
    return {
        "statistic": "query_idf_coverage",
        "percentile": 5,
        "floor": floor,
        "calibrated_at": "2020-01-01T00:00:00+00:00",
        "note": "synthetic-gate",
    }


def _public_search(query: str = QUERY, knowledgebase: str = PRODUCT_KB) -> dict:
    return parse_tool_result(
        research.search_literature_corpus(
            query,
            knowledgebase=knowledgebase,
            top_k=5,
            retrieval_mode="sparse",
        )
    )


def _chunk_ids(index: dict) -> set[str]:
    return {str(chunk.get("chunk_id") or "") for chunk in (index.get("chunks") or [])}


def _assert_no_leak(text: str, query: str, paths: list[Path]) -> None:
    assert query not in text
    lowered = text.casefold()
    for marker in LEAK_MARKERS:
        assert marker.casefold() not in lowered
    for path in paths:
        assert str(path) not in text
        assert path.name not in text


def _assert_public_failure(parsed: dict, query: str, paths: list[Path]) -> None:
    data = parsed["data"]
    assert data["success"] is False
    assert data["error_code"] == "corpus_read_failed"
    assert data["tool_name"] == "search_literature_corpus"
    assert "results" not in data
    _assert_no_leak(parsed["display"], query, paths)
    _assert_no_leak(str(data.get("error") or ""), query, paths)


def _assert_helper_refusal(knowledgebase: str, query: str, paths: list[Path]) -> None:
    with pytest.raises(research.LiteratureContractError) as caught:
        research._load_index(knowledgebase)
    message = str(caught.value)
    _assert_no_leak(message, query, paths)


@pytest.fixture
def forbid_ranking(monkeypatch):
    monkeypatch.setattr(
        research,
        "_search_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ranking reached on failure path")
        ),
    )


def _seed_product(paths: dict, *, floor: float | None = UNION_FLOOR, promoted: object = _OMIT) -> None:
    _write_gzip_json(
        paths["index_path"],
        _make_index(PRODUCT_KB, PRODUCT_CHUNK, PRODUCT_TEXT, "product"),
    )
    manifest: dict = {"schema": "synthetic.product-manifest.v1", "knowledgebase": PRODUCT_KB}
    if floor is not None:
        manifest["abstention"] = _abstention(floor)
    if promoted is not _OMIT:
        manifest["promoted"] = promoted
    _write_json(paths["manifest_path"], manifest)


def _tracked(paths: dict, extra: list[Path] | None = None) -> list[Path]:
    items = [paths["index_path"], paths["manifest_path"]]
    if extra:
        items.extend(extra)
    return items


# --- from test_profile_routing.py: Synthetic profile-routing and compatible-dense-union tests. No real corpus.
BGE_CHUNK_A = "synth-bge-chunk-a"


BGE_CHUNK_B = "synth-bge-chunk-b"


BGE_TEXT_A = "zympoly bge solventblend"


BGE_TEXT_B = "helioxane bge solventblend"


BGE_FLOOR = 0.3698406656908355


BGE_QUERY_INSTRUCTION = research._BGE_QUERY_INSTRUCTION


BGE_PASSAGE_INSTRUCTION = research._BGE_PASSAGE_INSTRUCTION


def _unit_profile_routing(dim: int, axis: int) -> list[float]:
    row = [0.0] * dim
    row[axis] = 1.0
    return row


def _doc(doc_key: str) -> dict:
    return {
        "document_id": f"D-{doc_key}",
        "sha256": hashlib.sha256(doc_key.encode("utf-8")).hexdigest(),
        "title": f"Synthetic {doc_key}",
        "source": "synthetic-local",
    }


def _chunk_profile_routing(chunk_id: str, text: str, doc_key: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "document_id": f"D-{doc_key}",
        "title": f"Synthetic {doc_key}",
        "source": "synthetic-local",
        "text": text,
        "body": text,
    }


def _make_index_profile_routing(
    knowledgebase: str,
    chunks: list[tuple[str, str, str]],
    *,
    dense: dict | None = None,
    abstention: object = _OMIT,
) -> dict:
    payload: dict = {
        "schema": INDEX_SCHEMA,
        "knowledgebase": knowledgebase,
        "documents": [_doc(doc_key) for _, _, doc_key in chunks],
        "chunks": [_chunk_profile_routing(chunk_id, text, doc_key) for chunk_id, text, doc_key in chunks],
        "dense": dense,
    }
    if abstention is not _OMIT:
        payload["abstention"] = abstention
    return payload


def _dense(
    chunk_ids: list[str],
    vectors: list[list[float]],
    *,
    model: str | None = MINILM_MODEL,
    dim: int | None = MINILM_DIM,
    **meta: object,
) -> dict:
    block: dict = {
        "chunk_ids": list(chunk_ids),
        "vectors": vectors,
    }
    if model is not None:
        block["model"] = model
    if dim is not None:
        block["dim"] = dim
    block.update(meta)
    return block


def _minilm_dense_profile_routing(chunk_ids: list[str], axes: list[int], **meta: object) -> dict:
    return _dense(
        chunk_ids,
        [_unit_profile_routing(MINILM_DIM, axis) for axis in axes],
        model=MINILM_MODEL,
        dim=MINILM_DIM,
        **meta,
    )


def _bge_dense_profile_routing(chunk_ids: list[str], axes: list[int], **meta: object) -> dict:
    meta.setdefault("query_instruction", BGE_QUERY_INSTRUCTION)
    meta.setdefault("passage_instruction", BGE_PASSAGE_INSTRUCTION)
    meta.setdefault("encoder_revision", BGE_REVISION)
    return _dense(
        chunk_ids,
        [_unit_profile_routing(BGE_DIM, axis) for axis in axes],
        model=BGE_MODEL,
        dim=BGE_DIM,
        **meta,
    )


def _chunk_ids_profile_routing(index: dict) -> list[str]:
    return [str(chunk.get("chunk_id") or "") for chunk in (index.get("chunks") or [])]


def _assert_helper_refusal_profile_routing(knowledgebase: str, query: str, paths: list[Path]) -> None:
    with pytest.raises(research.LiteratureContractError) as caught:
        research._load_index(knowledgebase)
    _assert_no_leak(str(caught.value), query, paths)


def _assert_union_refusal(left: dict, right: dict) -> None:
    original_left = json.dumps(left, sort_keys=True)
    original_right = json.dumps(right, sort_keys=True)
    with pytest.raises(research.LiteratureContractError) as caught:
        research._union_product_and_sidecar(left, right)
    assert caught.value.code == "dense_union_incompatible"
    assert json.dumps(left, sort_keys=True) == original_left
    assert json.dumps(right, sort_keys=True) == original_right


def _seed_product_profile_routing(paths: dict, *, floor: float | None = UNION_FLOOR, promoted: object = _OMIT, dense=None) -> None:
    payload = _make_index_profile_routing(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=dense,
    )
    _write_gzip_json(paths["index_path"], payload)
    manifest: dict = {"schema": "synthetic.product-manifest.v1", "knowledgebase": PRODUCT_KB}
    if floor is not None:
        manifest["abstention"] = _abstention(floor)
    if promoted is not _OMIT:
        manifest["promoted"] = promoted
    _write_json(paths["manifest_path"], manifest)


def _seed_sidecar_profile_routing(tmp_path: Path, *, dense=None, extra: dict | None = None) -> Path:
    sidecar_path = tmp_path / "sidecar.json.gz"
    payload = _make_index_profile_routing(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=dense,
    )
    if extra:
        payload.update(extra)
    _write_gzip_json(sidecar_path, payload)
    return sidecar_path


def _seed_minilm_pair(paths: dict, *, product_dense=None, sidecar_dense=None) -> Path:
    sidecar_path = _seed_sidecar_profile_routing(paths["tmp_path"], dense=sidecar_dense)
    _seed_product_profile_routing(
        paths,
        promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        dense=product_dense,
    )
    return sidecar_path


def _bge_index_payload() -> dict:
    return _make_index_profile_routing(
        PRODUCT_KB,
        [
            (BGE_CHUNK_A, BGE_TEXT_A, "bge-a"),
            (BGE_CHUNK_B, BGE_TEXT_B, "bge-b"),
        ],
        dense=_bge_dense_profile_routing([BGE_CHUNK_A, BGE_CHUNK_B], [0, 1]),
    )


def _seed_bge(paths: dict, *, relative: bool = False, mutate_manifest=None, mutate_index=None) -> dict:
    root = paths["tmp_path"] / "bge"
    index_path = root / "indexes" / "bge.json.gz"
    payload = _bge_index_payload()
    if mutate_index is not None:
        payload = mutate_index(payload)
    _write_gzip_json(index_path, payload)
    manifest_path = root / "BGE10.manifest.json"
    declared_index = "indexes/bge.json.gz" if relative else str(index_path)
    manifest = {
        "knowledgebase": PRODUCT_KB,
        "index_path": declared_index,
        "gzip_sha256": _sha256(index_path),
        "dense": {
            "model": BGE_MODEL,
            "dim": BGE_DIM,
            "query_instruction": BGE_QUERY_INSTRUCTION,
            "passage_instruction": BGE_PASSAGE_INSTRUCTION,
            "encoder_revision": BGE_REVISION,
            "chunk_ids": [BGE_CHUNK_A, BGE_CHUNK_B],
        },
        "abstention": _abstention(BGE_FLOOR),
    }
    if mutate_manifest is not None:
        manifest = mutate_manifest(manifest, index_path)
    _write_json(manifest_path, manifest)
    return {
        "manifest_path": manifest_path,
        "index_path": index_path,
        "payload": payload,
    }


def _spy_paths(monkeypatch) -> list[Path]:
    seen: list[Path] = []

    def remember(path: Path) -> None:
        try:
            seen.append(path.resolve())
        except OSError:
            seen.append(path)

    real_open = research.gzip.open

    def fake_open(path, *args, **kwargs):
        remember(Path(path))
        return real_open(path, *args, **kwargs)

    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def fake_read_text(self, *args, **kwargs):
        remember(self)
        return real_read_text(self, *args, **kwargs)

    def fake_read_bytes(self, *args, **kwargs):
        remember(self)
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(research.gzip, "open", fake_open)
    monkeypatch.setattr(Path, "read_text", fake_read_text)
    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    return seen


class TestProfileRouting:
    """Synthetic profile-routing and compatible-dense-union tests. No real corpus."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path / "working-corpus"))
        monkeypatch.setattr(research, "_pinned_reranker_snapshot", _no_reranker_snapshot)
        monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        monkeypatch.setattr(
            research,
            "_dense_vectors",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model forbidden")),
        )
        index_path = tmp_path / "canonical" / "product.json.gz"
        manifest_path = tmp_path / "canonical" / "manifest.json"
        yield {
            "index_path": index_path,
            "manifest_path": manifest_path,
            "tmp_path": tmp_path,
        }

    def test_explicit_bge10_selects_manifest_only(self, _isolate, monkeypatch):
        sidecar_path = _seed_minilm_pair(_isolate)
        bge = _seed_bge(_isolate, relative=True)
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        real_open = research.gzip.open

        def fake_open(path, *args, **kwargs):
            resolved = Path(path).resolve()
            if resolved in {_isolate["index_path"].resolve(), sidecar_path.resolve()}:
                raise AssertionError("legacy path read")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(research.gzip, "open", fake_open)
        tracked = _tracked(_isolate, [sidecar_path, bge["index_path"], bge["manifest_path"]])
        before = _snapshot(tracked)
        loaded = research._load_index(PRODUCT_KB)
        assert _chunk_ids_profile_routing(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
        assert loaded["dense"]["model"] == BGE_MODEL
        assert loaded["dense"]["dim"] == BGE_DIM
        assert loaded["dense"]["encoder_revision"] == BGE_REVISION
        assert loaded["abstention"]["floor"] == BGE_FLOOR
        parsed = _public_search()
        assert parsed["data"]["success"] is True
        assert parsed["data"]["floor"] == round(BGE_FLOOR, 6)
        assert {row["chunk_id"] for row in parsed["data"]["results"]} == {BGE_CHUNK_A}
        assert _snapshot(tracked) == before

    @pytest.mark.parametrize(
        "case",
        [
            "empty_env",
            "missing_file",
            "malformed",
            "unreadable",
            "wrong_digest",
            "wrong_knowledgebase",
            "missing_floor",
            "model_mismatch",
            "dimension_mismatch",
            "revision_mismatch",
            "instruction_mismatch",
            "index_model_mismatch",
            "index_dimension_mismatch",
            "index_revision_mismatch",
            "index_instruction_mismatch",
        ],
    )
    def test_bge10_manifest_and_recipe_faults(self, case, _isolate, forbid_ranking, monkeypatch):
        def mutate_manifest(manifest, index_path):
            if case == "wrong_digest":
                manifest["gzip_sha256"] = "0" * 64
                return manifest
            if case == "wrong_knowledgebase":
                manifest["knowledgebase"] = SIDECAR_KB
            elif case == "missing_floor":
                del manifest["abstention"]
            elif case == "model_mismatch":
                manifest["dense"]["model"] = MINILM_MODEL
            elif case == "dimension_mismatch":
                manifest["dense"]["dim"] = MINILM_DIM
            elif case == "revision_mismatch":
                manifest["dense"]["encoder_revision"] = "deadbeef" * 8
            elif case == "instruction_mismatch":
                manifest["dense"]["query_instruction"] = "other instruction: "
            return manifest

        def mutate_index(payload):
            if case == "index_model_mismatch":
                payload["dense"]["model"] = MINILM_MODEL
            elif case == "index_dimension_mismatch":
                payload["dense"]["dim"] = MINILM_DIM
            elif case == "index_revision_mismatch":
                payload["dense"]["encoder_revision"] = "c" * 40
            elif case == "index_instruction_mismatch":
                payload["dense"]["query_instruction"] = "other instruction: "
            return payload

        index_cases = {
            "index_model_mismatch",
            "index_dimension_mismatch",
            "index_revision_mismatch",
            "index_instruction_mismatch",
        }
        skip_manifest = {
            "empty_env",
            "missing_file",
            "malformed",
            "unreadable",
            *index_cases,
        }
        bge = _seed_bge(
            _isolate,
            relative=True,
            mutate_manifest=None if case in skip_manifest else mutate_manifest,
            mutate_index=mutate_index if case in index_cases else None,
        )
        if case == "missing_env":
            monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
            tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
        elif case == "empty_env":
            monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", "  ")
            tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
        elif case == "missing_file":
            monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"].with_name("absent.json")))
            tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
        elif case == "malformed":
            bge["manifest_path"].write_text("{", encoding="utf-8")
            monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
            tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
        elif case == "unreadable":
            monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
            real = Path.read_text

            def fake_read(self, *args, **kwargs):
                if self.resolve() == bge["manifest_path"].resolve():
                    raise OSError("injected")
                return real(self, *args, **kwargs)

            monkeypatch.setattr(Path, "read_text", fake_read)
            tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
        else:
            monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
            tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
        before = _snapshot(tracked)
        _assert_helper_refusal_profile_routing(PRODUCT_KB, QUERY, tracked)
        _assert_public_failure(_public_search(), QUERY, tracked)
        assert _snapshot(tracked) == before

    def test_relative_index_path_ignores_cwd(self, _isolate, monkeypatch):
        bge = _seed_bge(_isolate, relative=True)
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        cwd = _isolate["tmp_path"] / "other-cwd"
        decoy = cwd / "indexes" / "bge.json.gz"
        _write_gzip_json(
            decoy,
            _make_index_profile_routing(PRODUCT_KB, [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "decoy")]),
        )
        monkeypatch.chdir(cwd)
        tracked = [bge["index_path"], bge["manifest_path"], decoy]
        before = _snapshot(tracked)
        loaded = research._load_index(PRODUCT_KB)
        assert _chunk_ids_profile_routing(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
        assert INDEPENDENT_CHUNK not in _chunk_ids_profile_routing(loaded)
        assert _snapshot(tracked) == before

    def test_alias_save_protection_on_the_served_release(self, _isolate, monkeypatch):
        bge = _seed_bge(_isolate)
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        alias_dir = _isolate["tmp_path"] / "aliases"
        alias_dir.mkdir()
        selected_alias = alias_dir / "selected.json.gz"
        selected_alias.symlink_to(bge["index_path"].resolve())
        payload = _make_index_profile_routing(INDEPENDENT_KB, [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "independent")])
        tracked = [bge["index_path"], selected_alias]
        before = _snapshot(tracked)
        monkeypatch.setattr(research, "_index_path", lambda knowledgebase: selected_alias)
        with pytest.raises(research.LiteratureContractError) as selected:
            research._save_index(payload)
        assert selected.value.code == "protected_serving_index"
        assert _snapshot(tracked) == before

    def test_unset_manifest_serves_the_working_release_else_the_shipped_one(self, _isolate, monkeypatch):
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        assert research._declared_bge10_manifest_path() == research._SHIPPED_CORPUS_DIR / "manifest.json"
        bge = _seed_bge(_isolate, relative=True)
        working = _isolate["tmp_path"] / "working-corpus"
        shutil.copytree(bge["manifest_path"].parent, working)
        (working / bge["manifest_path"].name).rename(working / "manifest.json")
        assert research._declared_bge10_manifest_path() == working / "manifest.json"
        assert _chunk_ids_profile_routing(research._load_index(PRODUCT_KB)) == [BGE_CHUNK_A, BGE_CHUNK_B]

    def test_the_shipped_release_loads_and_validates(self, monkeypatch):
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        index = research._load_index(PRODUCT_KB)
        assert (len(index["documents"]), len(index["chunks"])) == (39, 1841)
        assert research._bge10_recipe_identity(index)
        assert index["abstention"]["floor"] == corpus.ABSTENTION["floor"]

    def test_bge10_does_not_fallback_to_research_home(self, _isolate, monkeypatch):
        home = _isolate["tmp_path"] / "research-home"
        decoy = home / f"{PRODUCT_KB}.json.gz"
        _write_gzip_json(
            decoy,
            _make_index_profile_routing(PRODUCT_KB, [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "decoy")]),
        )
        bge = _seed_bge(_isolate)
        monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(home))
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        loaded = research._load_index(PRODUCT_KB)
        assert _chunk_ids_profile_routing(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
        assert INDEPENDENT_CHUNK not in _chunk_ids_profile_routing(loaded)

    def test_bge10_verified_bytes_load_unchanged_positive(self, _isolate, monkeypatch):
        bge = _seed_bge(_isolate)
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        tracked = [bge["index_path"], bge["manifest_path"]]
        before = _snapshot(tracked)
        loaded = research._load_index(PRODUCT_KB)
        assert _chunk_ids_profile_routing(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
        assert loaded["dense"]["chunk_ids"] == [BGE_CHUNK_A, BGE_CHUNK_B]
        assert loaded["dense"]["model"] == BGE_MODEL
        assert loaded["dense"]["encoder_revision"] == BGE_REVISION
        assert _snapshot(tracked) == before

    def test_bge10_digest_read_swap_never_returns_replacement(self, _isolate, monkeypatch):
        bge = _seed_bge(_isolate)
        replacement = _make_index_profile_routing(
            PRODUCT_KB,
            [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "swap")],
            dense=_bge_dense_profile_routing([INDEPENDENT_CHUNK], [5]),
        )
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        real_read = Path.read_bytes
        swapped = {"done": False}

        def fake_read(self, *args, **kwargs):
            data = real_read(self, *args, **kwargs)
            if self.resolve() == bge["index_path"].resolve() and not swapped["done"]:
                swapped["done"] = True
                _write_gzip_json(bge["index_path"], replacement)
            return data

        monkeypatch.setattr(Path, "read_bytes", fake_read)
        try:
            loaded = research._load_index(PRODUCT_KB)
        except research.LiteratureContractError:
            return
        assert _chunk_ids_profile_routing(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
        assert INDEPENDENT_CHUNK not in _chunk_ids_profile_routing(loaded)
        assert loaded["dense"]["chunk_ids"] == [BGE_CHUNK_A, BGE_CHUNK_B]

    def test_bge10_corrupt_compression_refuses(self, _isolate, forbid_ranking, monkeypatch):
        bge = _seed_bge(_isolate)
        bge["index_path"].write_bytes(b"not-gzip")
        manifest = json.loads(bge["manifest_path"].read_text(encoding="utf-8"))
        manifest["gzip_sha256"] = _sha256(bge["index_path"])
        _write_json(bge["manifest_path"], manifest)
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        tracked = [bge["index_path"], bge["manifest_path"]]
        before = _snapshot(tracked)
        _assert_helper_refusal_profile_routing(PRODUCT_KB, QUERY, tracked)
        _assert_public_failure(_public_search(), QUERY, tracked)
        assert _snapshot(tracked) == before

    def test_bge10_manifest_ordered_chunk_ids_accepted(self, _isolate, monkeypatch):
        bge = _seed_bge(_isolate)
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        loaded = research._load_index(PRODUCT_KB)
        assert _chunk_ids_profile_routing(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
        assert loaded["dense"]["chunk_ids"] == [BGE_CHUNK_A, BGE_CHUNK_B]

    @pytest.mark.parametrize(
        "case",
        [
            "missing",
            "null",
            "wrong_type",
            "duplicate",
            "missing_member",
            "extra_member",
            "reordered",
        ],
    )
    def test_bge10_manifest_chunk_ids_required_against_index(self, case, _isolate, forbid_ranking, monkeypatch):
        def mutate_manifest(manifest, index_path):
            if case == "missing":
                del manifest["dense"]["chunk_ids"]
            elif case == "null":
                manifest["dense"]["chunk_ids"] = None
            elif case == "wrong_type":
                manifest["dense"]["chunk_ids"] = BGE_CHUNK_A
            elif case == "duplicate":
                manifest["dense"]["chunk_ids"] = [BGE_CHUNK_A, BGE_CHUNK_B, BGE_CHUNK_A]
            elif case == "missing_member":
                manifest["dense"]["chunk_ids"] = [BGE_CHUNK_A]
            elif case == "extra_member":
                manifest["dense"]["chunk_ids"] = [BGE_CHUNK_A, BGE_CHUNK_B, INDEPENDENT_CHUNK]
            else:
                manifest["dense"]["chunk_ids"] = [BGE_CHUNK_B, BGE_CHUNK_A]
            return manifest

        bge = _seed_bge(_isolate, mutate_manifest=mutate_manifest)
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        tracked = [bge["index_path"], bge["manifest_path"]]
        before = _snapshot(tracked)
        _assert_helper_refusal_profile_routing(PRODUCT_KB, QUERY, tracked)
        _assert_public_failure(_public_search(), QUERY, tracked)
        assert _snapshot(tracked) == before

