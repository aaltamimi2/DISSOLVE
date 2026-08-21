"""C7: canonical documents reach the portable index. Not C3. Not C8."""
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
from dissolve.contracts import parse_tool_result

PERSIST = Path(
    "/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/parses/canonical_document.v1.json"
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
