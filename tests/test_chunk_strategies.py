"""C6: S0–S5 chunkers and the one-paper scorer. Tiny fixtures. Not C3."""
from __future__ import annotations

from pathlib import Path


from dissolve import research


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
