"""TEXT_CHUNKING_SPEC.v1 T0–T6. Tiny fixtures. No Docling, no gold v1."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import research, text_chunking

TOKEN_A = "PEZXQ"
TOKEN_B = "SOLZXQ"
TOKEN_C = "81.25"


def _t6_encoder_one_jump(sentences):
    """First half [1,0], second half [0,1]. One large distance in the middle."""
    mid = max(1, len(sentences) // 2)
    return [[1.0, 0.0] if index < mid else [0.0, 1.0] for index, _ in enumerate(sentences)]


def _block(kind, text, start, end, heading=None):
    return {
        "block_id": f"{kind}-{start}",
        "kind": kind,
        "text": text,
        "char_start": start,
        "char_end": end,
        "nearest_preceding_heading": list(heading or []),
    }


def _canon_from_parts(parts: list[tuple[str, str, tuple[str, ...]]]) -> dict:
    """parts: (kind, text, heading). Join with blank lines."""
    blocks = []
    texts = []
    cursor = 0
    for kind, text, heading in parts:
        if texts:
            cursor += 2
        start = cursor
        end = start + len(text)
        blocks.append(_block(kind, text, start, end, heading))
        texts.append(text)
        cursor = end
    body = "\n\n".join(texts)
    return {
        "schema": "dissolve.canonical-document.v1",
        "canonical_text": body,
        "blocks": blocks,
    }


def _long_prose() -> dict:
    para_a = f"{TOKEN_A} first sentence. " + ("word " * 80) + f"End {TOKEN_A}."
    para_b = f"{TOKEN_B} second sentence. " + ("item " * 80) + f"End {TOKEN_B}."
    para_c = f"{TOKEN_C} third sentence. " + ("note " * 80) + f"End {TOKEN_C}."
    return _canon_from_parts([
        ("heading", "Section one", ()),
        ("paragraph", para_a, ("Section one",)),
        ("paragraph", para_b, ("Section one",)),
        ("heading", "Section two", ()),
        ("paragraph", para_c, ("Section two",)),
    ])


def test_t0_constants_match_production():
    assert text_chunking.T0_TARGET == research._CHUNK_TARGET == 1400
    assert text_chunking.T0_OVERLAP == research._CHUNK_OVERLAP == 180


def test_t0_bodies_are_canonical_slices():
    canon = _long_prose()
    chunks = text_chunking.chunk_t0(canon)
    assert chunks
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)


def test_t0_recovers_offsets_when_production_collapses_whitespace():
    """Auditor hold-to: None offsets must not be scored as span-preserved."""
    raw = (
        f"{TOKEN_A} first sentence.\n\n"
        + ("word  " * 40)
        + f"\nEnd {TOKEN_A}.\n\n"
        + f"{TOKEN_B} second sentence.\n"
        + ("item\t" * 40)
        + f"End {TOKEN_B}."
    )
    canon = {"canonical_text": raw, "blocks": []}
    chunks = text_chunking.chunk_t0(canon)
    assert chunks
    for chunk in chunks:
        assert chunk["char_start"] is not None
        assert chunk["char_end"] is not None
        start, end = int(chunk["char_start"]), int(chunk["char_end"])
        covering = canon["canonical_text"][start:end]
        collapsed_cover = "".join(ch for ch in covering if not ch.isspace())
        collapsed_body = "".join(ch for ch in chunk["body"] if not ch.isspace())
        assert collapsed_cover == collapsed_body
        # needle_span_preserved must see real offsets, never None-as-true
        assert start < end
        assert text_chunking.offsets_usable(chunk)
    assert text_chunking.offsets_usable({"char_start": None, "char_end": None}) is False


def test_t1_grid_and_nonoverlap():
    assert len(text_chunking.t1_grid()) == 12
    canon = _long_prose()
    chunks = text_chunking.chunk_t1(canon, size=400, overlap_frac=0.0)
    assert chunks
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)
        assert (chunk["char_end"] - chunk["char_start"]) <= 400
    starts = [chunk["char_start"] for chunk in chunks]
    assert starts == sorted(starts)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt["char_start"] == prev["char_end"]


def test_t2_respects_size_and_is_slice():
    canon = _long_prose()
    chunks = text_chunking.chunk_t2(canon, size=800, overlap_frac=0.0)
    assert chunks
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)
        assert (chunk["char_end"] - chunk["char_start"]) <= 800


def test_t1_is_control_for_t2_at_matched_pairs():
    pairs = text_chunking.t1_control_pairs_for_t2()
    assert (800, 0.0) in pairs
    assert (1400, 0.0) in pairs
    assert (800, 0.15) in pairs
    assert (1400, 0.15) in pairs
    canon = _long_prose()
    t1 = text_chunking.chunk_t1(canon, size=800, overlap_frac=0.0)
    t2 = text_chunking.chunk_t2(canon, size=800, overlap_frac=0.0)
    assert t1 and t2
    assert t1[0]["strategy"] == "T1"
    assert t2[0]["strategy"] == "T2"


def test_t3_sentence_window_stride():
    text = f"{TOKEN_A} one. {TOKEN_B} two. {TOKEN_C} three. Four. Five."
    canon = {"canonical_text": text, "blocks": []}
    chunks = text_chunking.chunk_t3(canon, n_sentences=3, stride=2)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)
    assert chunks[1]["char_start"] > chunks[0]["char_start"]


def test_t4_does_not_cross_heading():
    canon = _long_prose()
    chunks = text_chunking.chunk_t4(canon)
    assert chunks
    section_one = "Section one"
    section_two = "Section two"
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)
        body = chunk["body"]
        if section_one in body and section_two in body:
            raise AssertionError("T4 crossed a heading boundary")


def test_t5_never_splits_a_block():
    canon = _long_prose()
    chunks = text_chunking.chunk_t5(canon, target=200)
    assert chunks
    block_spans = {(int(b["char_start"]), int(b["char_end"])) for b in canon["blocks"]}
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)
        start, end = int(chunk["char_start"]), int(chunk["char_end"])
        covered = [span for span in block_spans if span[0] >= start and span[1] <= end]
        assert covered
        for span in covered:
            assert span[0] >= start and span[1] <= end
        # no block is only partially inside
        for span in block_spans:
            mid = span[0] < end and start < span[1]
            whole = span[0] >= start and span[1] <= end
            assert (not mid) or whole


def test_chunkers_do_not_invoke_docling_or_dense(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("Docling / dense must not run")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(research, "_dense_vectors", boom)
    canon = _long_prose()
    text_chunking.chunk_t0(canon)
    text_chunking.chunk_t1(canon, size=400, overlap_frac=0.1)
    text_chunking.chunk_t2(canon, size=800, overlap_frac=0.15)
    text_chunking.chunk_t3(canon, n_sentences=3, stride=1)
    text_chunking.chunk_t4(canon)
    text_chunking.chunk_t5(canon)
    text_chunking.chunk_t6(canon, percentile=80, encoder=_t6_encoder_one_jump)


def test_empty_text_is_empty_chunks():
    canon = {"canonical_text": "", "blocks": []}
    assert text_chunking.chunk_t0(canon) == []
    assert text_chunking.chunk_t1(canon, size=400, overlap_frac=0.0) == []
    assert text_chunking.chunk_t2(canon, size=800, overlap_frac=0.0) == []
    assert text_chunking.chunk_t3(canon, n_sentences=3, stride=2) == []
    assert text_chunking.chunk_t5(canon) == []
    assert text_chunking.chunk_t6(canon, percentile=80, encoder=_t6_encoder_one_jump) == []


def test_t6_pin_and_segmentation_only():
    assert text_chunking.T6_EMBEDDER_ID == "sentence-transformers/all-MiniLM-L6-v2"
    assert text_chunking.T6_EMBEDDER_ROLE == "segmentation_only"
    assert text_chunking.t6_grid() == [80, 95]


def test_t6_percentile_cuts_and_no_vectors_on_chunks():
    sentences = [
        f"{TOKEN_A} one.",
        "Two stays close.",
        "Three stays close.",
        f"{TOKEN_B} topic change.",
        "Five stays close.",
        f"{TOKEN_C} six.",
    ]
    text = " ".join(sentences)
    canon = {"canonical_text": text, "blocks": []}
    chunks = text_chunking.chunk_t6(canon, percentile=80, encoder=_t6_encoder_one_jump)
    assert len(chunks) == 2
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)
        assert chunk["strategy"] == "T6"
        assert chunk["params"]["embedder"] == text_chunking.T6_EMBEDDER_ID
        assert chunk["params"]["embedder_role"] == text_chunking.T6_EMBEDDER_ROLE
        assert "embedding" not in chunk
        assert "vector" not in chunk
        assert "vectors" not in chunk
    assert TOKEN_A in chunks[0]["body"]
    assert TOKEN_B in chunks[1]["body"]
    assert TOKEN_A not in chunks[1]["body"]


def test_t6_default_path_does_not_call_dense(monkeypatch):
    class FakeModel:
        def __init__(self, name):
            assert name == text_chunking.T6_EMBEDDER_ID

        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
            return _t6_encoder_one_jump(list(texts))

    def boom(*_args, **_kwargs):
        raise AssertionError("retrieval _dense_vectors must not run for T6")

    monkeypatch.setattr(text_chunking, "_t6_sentence_transformer", lambda: FakeModel)
    monkeypatch.setattr(research, "_dense_vectors", boom)
    canon = _long_prose()
    chunks = text_chunking.chunk_t6(canon, percentile=95)
    assert chunks
    for chunk in chunks:
        assert text_chunking.slice_ok(canon, chunk)
        assert "vector" not in chunk
