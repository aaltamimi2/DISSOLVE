"""Synthetic BGE10 fusion and public retrieval-diagnostic tests. No real corpus."""

from __future__ import annotations

import math
import statistics

import pytest

from dissolve import research
from dissolve.contracts import parse_tool_result

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


def _chunk(
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


def _index(
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


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    monkeypatch.delenv("DISSOLVE_EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(research, "_research_root", lambda: tmp_path / "research-home")
    monkeypatch.setattr(
        research,
        "_canonical_product_index_path",
        lambda: tmp_path / "canonical" / "product.json.gz",
    )
    monkeypatch.setattr(
        research,
        "_product_manifest_path",
        lambda: tmp_path / "canonical" / "manifest.json",
    )
    monkeypatch.setattr(research, "_committed_lexicon", lambda: [])
    monkeypatch.setattr(
        research,
        "_dense_vectors",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model forbidden")),
    )
    monkeypatch.setattr(
        research.rerank,
        "_load_cross_encoder",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model forbidden")),
    )
    yield tmp_path


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
    monkeypatch.setattr(research.rerank, "reorder_window", fake_reorder)
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
        chunks.append(_chunk(chunk_id, title=titles[i], section=section, text=texts[i]))
    return chunks


def test_section_removal_uses_zscore_not_boost(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three(abstract=True))
    rows, counters = _search(index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
    assert _ids(rows) == ["c2", "c1", "c0"]
    assert [row["section_boost"] for row in rows] == [0.0, 0.0, 0.0]
    z_dense = _zscores([0.10, 0.11, 0.12])
    assert [row["dense_score"] for row in rows] == [round(z_dense[i], 6) for i in (2, 1, 0)]
    assert [row["sparse_score"] for row in rows] == [0.0, 0.0, 0.0]
    assert counters[0] and counters[2]


def test_zscore_vs_max_normalization_order(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three())
    rows, _counters = _search(index, monkeypatch, [0.1, 0.2, 0.3], [3.0, 2.0, 1.0])
    assert _ids(rows) == ["c2", "c1", "c0"]
    assert rows[2]["final_score"] < 0.0
    assert set(_ids(rows)) == {"c0", "c1", "c2"}


def test_population_excludes_ineligible_sparse_zero(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    chunks = _three() + [_chunk("c3")]
    index = _index(chunks)
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


def test_constant_channels_lexicographic_chunk_id(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three(ids=["c2", "c1", "c0"]))
    rows, _counters = _search(index, monkeypatch, [0.4, 0.4, 0.4], [2.0, 2.0, 2.0])
    assert _ids(rows) == ["c0", "c1", "c2"]
    assert [row["dense_score"] for row in rows] == [0.0, 0.0, 0.0]
    assert [row["sparse_score"] for row in rows] == [0.0, 0.0, 0.0]


def test_gate_first_below_unchanged_floor(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(
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


def test_singleton_zero_standardized_components(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index([_chunk("c0")])
    rows, _counters = _search(index, monkeypatch, [0.77], [4.2], top_k=5)
    assert _ids(rows) == ["c0"]
    assert rows[0]["dense_score"] == 0.0
    assert rows[0]["sparse_score"] == 0.0
    assert rows[0]["section_boost"] == 0.0
    assert rows[0]["final_score"] == 0.0


def test_empty_corpus_reason_without_model_calls(monkeypatch):
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


def test_no_sparse_match_without_floor(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three())
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


def test_gate_first_when_floor_and_no_sparse_coincide(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three(), floor=UNCHANGED_FLOOR)
    _install_scores(monkeypatch, [0.9, 0.8, 0.7], [0.0, 0.0, 0.0])
    parsed = _public(monkeypatch, index)
    assert parsed["data"]["reason"] == "abstained_below_floor"
    assert parsed["data"]["result_count"] == 0


def test_public_depths_five_ten_twenty(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    chunks = [_chunk(f"c{i:02d}") for i in range(25)]
    index = _index(chunks)
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


def test_minilm_rollback_section_and_max_norm(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "minilm")
    section_index = _index(_three(abstract=True), identity="minilm")
    section_rows, _c1 = _search(
        section_index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0],
    )
    assert _ids(section_rows) == ["c0", "c2", "c1"]
    assert section_rows[0]["section_boost"] == 0.05
    max_index = _index(_three(), identity="minilm")
    max_rows, _c2 = _search(max_index, monkeypatch, [0.1, 0.2, 0.3], [3.0, 2.0, 1.0])
    assert _ids(max_rows) == ["c0", "c1", "c2"]
    bge_identity = _index(_three(abstract=True), identity="bge")
    rollback_bge, _c3 = _search(
        bge_identity, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0],
    )
    assert _ids(rollback_bge) == ["c0", "c2", "c1"]
    assert rollback_bge[0]["section_boost"] == 0.05


def test_dense_clip_before_standardization_changes_order(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three())
    rows, _counters = _search(index, monkeypatch, [-1.0, 0.0, 0.2], [1.0, 4.0, 1.0])
    assert _ids(rows) == ["c2", "c1", "c0"]
    clipped = _zscores([0.0, 0.0, 0.2])
    assert rows[0]["dense_score"] == round(clipped[2], 6)


def test_title_secondary_key_on_tied_scores(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three(titles=["m", "a", "z"]))
    rows, _counters = _search(index, monkeypatch, [0.5, 0.5, 0.5], [1.0, 1.0, 1.0])
    assert _ids(rows) == ["c1", "c0", "c2"]


def test_zero_variance_and_negative_fused_tail_kept(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three())
    rows, _counters = _search(index, monkeypatch, [0.1, 0.2, 0.3], [3.0, 2.0, 1.0], top_k=3)
    assert _ids(rows) == ["c2", "c1", "c0"]
    assert rows[-1]["final_score"] < 0.0
    assert len(rows) == 3


def test_population_includes_outlier_outside_top_twenty(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    chunks = [_chunk(f"c{i:02d}") for i in range(21)]
    index = _index(chunks)
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


def test_gate_equality_is_not_abstention(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    equal = _index(_three(), floor=1.0)
    rows, counters = _search(equal, monkeypatch, [0.3, 0.2, 0.1], [1.0, 1.0, 1.0])
    assert _ids(rows) == ["c0", "c1", "c2"]
    assert counters[0]
    strict = _index(_three(), floor=1.0000001)
    empty, counters_strict = _search(
        strict, monkeypatch, [0.3, 0.2, 0.1], [1.0, 1.0, 1.0],
    )
    assert empty == []
    assert counters_strict[0] == []


def test_nonproduct_keeps_max_norm_under_bge_profile(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three(abstract=True), knowledgebase=INDEPENDENT_KB)
    rows, _counters = _search(index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
    assert _ids(rows) == ["c0", "c2", "c1"]
    assert rows[0]["section_boost"] == 0.05


def test_product_minilm_identity_not_silently_substituted(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three(abstract=True), identity="minilm")
    rows, _counters = _search(index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
    assert _ids(rows) == ["c0", "c2", "c1"]
    assert rows[0]["section_boost"] == 0.05


def test_legacy_sparse_mode_keeps_section_boost_on_bge_identity(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    chunks = [_chunk("c0"), _chunk("c1"), _chunk("c2", section="abstract")]
    index = _index(chunks)
    rows, counters = _search(
        index, monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0], mode="sparse",
    )
    assert _ids(rows) == ["c2", "c0", "c1"]
    assert rows[0]["section_boost"] == 0.05
    assert counters[0] == []


def test_explicit_weights_still_apply_on_bge_path(monkeypatch):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    index = _index(_three())
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
