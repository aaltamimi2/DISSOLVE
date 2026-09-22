"""C2: one-paper canonical document. Tiny fixtures. Not the probe PDF."""
from __future__ import annotations

import ast
import copy
import inspect
import re
from pathlib import Path

import pytest


from dissolve import research


_HEADING_FILL = research._HEADING_FILL_KEY
_TEMP_RE = research._CANONICAL_TEMPERATURE_RE
_MEASURE_SCRIPTS = (
    Path("/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/measure_step2.py"),
    Path("/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/measure_step3_pypdf.py"),
    Path("/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/measure_parse.py"),
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
