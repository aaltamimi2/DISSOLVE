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


def test_gold_v1_digest_unmoved():
    digest = hashlib.sha256(GOLD.read_bytes()).hexdigest()
    assert digest == research._GOLD_FACTS_V1_SHA256


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


