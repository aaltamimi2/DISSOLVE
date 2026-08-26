"""Docling bridge must put tables on the text item list, not only in tables[]."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import research


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
