"""A-2A4 coverage + P5 floor. Fixtures only. No MiniLM. No gold needles."""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import abstention_a2a4, dense_d2, engine_e2e, research, text_chunk_metrics
from dissolve.contracts import parse_tool_result
from dissolve.gold_ensemble import file_sha256

PAPER = "aa" * 32


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
            "model": engine_e2e.MINILM_ID,
            "dim": 384,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [_unit(1.0) for _ in chunks],
            "refuse_rule": "sparse_gated",
        },
    }


def _fake_minilm():
    def fake(texts, model_name=None):
        return engine_e2e.MINILM_ID, [_unit(1.0) for _ in texts]

    return fake


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


def test_a2a4_named_grid_and_interpolation():
    assert abstention_a2a4.NAMED_PERCENTILES == (1, 5, 10, 25)
    assert abstention_a2a4.SHIPPED_PERCENTILE == 5
    values = [0.0, 0.25, 0.5, 0.75, 1.0]
    assert abstention_a2a4.named_percentile(values, 50) == 0.5


def test_a2a4_emit_tmp_does_not_move_pins(tmp_path):
    index = _index([
        _chunk("c-a", "zxqtitania adsorption surface methods forcefield"),
        _chunk("c-b", "polymer solvent screening solubility"),
    ])
    manifest = tmp_path / "INDEX.t5.unsealed.v1.json"
    shutil.copy(engine_e2e.MANIFEST_PATH, manifest)
    off = tmp_path / "OFFDOMAIN.queries.v1.json"
    off.write_text(json.dumps({
        "schema": "dissolve.offdomain.queries.v1",
        "set_digest": abstention_a2a4.SPEC_SHA256,
        "queries": [
            {"id": "od-fixture0001-001", "query": "zxqtitania rutile grafting forcefield"},
            {"id": "od-fixture0001-002", "query": "zxqtitania adsorption on rutile surface"},
        ],
    }) + "\n")
    gzip_before = file_sha256(dense_d2.INDEX_GZIP_PATH)
    gold_before = file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH)
    live_manifest_before = file_sha256(engine_e2e.MANIFEST_PATH)
    result = abstention_a2a4.emit_a2a4_product(
        dest_dir=tmp_path,
        manifest_path=manifest,
        offdomain_path=off,
        index=index,
        fire_queries=[
            "polymer solvent screening solubility",
            "zxqtitania adsorption surface methods forcefield",
        ],
        offdomain_queries=[
            "zxqtitania rutile grafting forcefield",
            "zxqtitania adsorption on rutile surface",
        ],
    )
    curves = json.loads(Path(result["curves_path"]).read_text())
    assert curves["named_percentiles"] == [1, 5, 10, 25]
    assert curves["shipped_percentile"] == 5
    assert any(row["shipped"] and row["percentile"] == 5 for row in curves["grid"])
    assert "fn" in curves["grid"][0] and "fp" in curves["grid"][0]
    dumped = json.dumps(curves)
    assert '"query"' not in dumped
    assert "fact_id" not in dumped
    assert "needles" not in dumped
    patched = json.loads(manifest.read_text())
    assert patched["abstention"]["percentile"] == 5
    assert patched["abstention"]["statistic"] == "query_idf_coverage"
    assert patched["gold_sha256"] == text_chunk_metrics.GOLD_UNSEALED_SHA256
    assert file_sha256(dense_d2.INDEX_GZIP_PATH) == gzip_before == abstention_a2a4.GZIP_SHA256
    assert file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH) == gold_before
    assert file_sha256(engine_e2e.MANIFEST_PATH) == live_manifest_before
    source = Path(research.__file__).read_text()
    assert "_HYBRID_DENSE_WEIGHT = 0.55\n" in source
    assert "_HYBRID_SPARSE_WEIGHT = 0.40\n" in source
    assert "engine_e2e" not in source


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
