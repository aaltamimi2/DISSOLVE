"""P-3 r5de. Injected embedders. No gold needles in asserts."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import dense_r5de, engine_e2e, research, text_chunk_metrics
from dissolve.gold_ensemble import CONTAMINANT_SHA256
from dissolve.text_gold import TextGoldError

IDX = "aa" * 32
HOLD = "bb" * 32
FIRE_A = "tg-fire-a"
FIRE_B = "tg-fire-b"
FIRE_C = "tg-fire-c"
FIRE_D = "tg-fire-d"
HOLD_ID = "tg-hold-1"
NEEDLE_A = "ZXQALPHA"
NEEDLE_B = "ZXQBETA"
NEEDLE_C = "ZXQGAMA"
NEEDLE_D = "ZXQDELTA"
NEEDLE_HOLD = "ZXQHOLD"


def _k_map(value) -> dict[str, object]:
    return {str(k): value for k in text_chunk_metrics.RETRIEVAL_KS}


def _fact(fact_id: str, paper: str, status: str, token: str) -> dict:
    return {
        "fact_id": fact_id,
        "paper_sha256": paper,
        "paper_status": status,
        "query": f"query {token}",
        "needles": {"subject": token, "value": token},
    }


def _gold() -> dict:
    return {
        "papers": [
            {"paper_sha256": IDX, "paper_status": "indexed"},
            {"paper_sha256": HOLD, "paper_status": "held_out"},
        ],
        "facts": [
            _fact(FIRE_A, IDX, "indexed", NEEDLE_A),
            _fact(FIRE_B, IDX, "indexed", NEEDLE_B),
            _fact(FIRE_C, IDX, "indexed", NEEDLE_C),
            _fact(FIRE_D, IDX, "indexed", NEEDLE_D),
            _fact(HOLD_ID, HOLD, "held_out", NEEDLE_HOLD),
        ],
    }


def _chunk(chunk_id: str, token: str) -> dict:
    body = f"body {token} body"
    return {
        "chunk_id": chunk_id,
        "paper_sha256": IDX,
        "body": body,
        "text": body,
        "section": "methods",
        "section_origin": "parser_supplied",
        "char_start": 0,
        "char_end": len(body),
        "page": 1,
        "title": "Fixture",
    }


def _chunks() -> list[dict]:
    return [
        _chunk("c-a", NEEDLE_A),
        _chunk("c-b", NEEDLE_B),
        _chunk("c-c", NEEDLE_C),
        _chunk("c-d", NEEDLE_D),
    ]


def _store() -> dict:
    return {"indexed_paper_sha256": [IDX], "chunks": _chunks()}


def _minilm_index() -> dict:
    chunks = _chunks()
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": engine_e2e.KNOWLEDGEBASE_ID,
        "chunks": chunks,
        "dense": {
            "model": engine_e2e.MINILM_ID,
            "dim": 384,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [[1.0] + [0.0] * 383 for _ in chunks],
        },
    }


def _bge_index() -> dict:
    chunks = _chunks()
    recipe = dense_r5de.bge_recipe()
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": engine_e2e.KNOWLEDGEBASE_ID,
        "chunks": chunks,
        "dense": {
            "model": dense_r5de.BGE_ID,
            "dim": dense_r5de.BGE_DIM,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [[1.0] + [0.0] * 767 for _ in chunks],
            "query_instruction": recipe["query_instruction"],
            "passage_instruction": recipe["passage_instruction"],
            "recipe_source": recipe["recipe_source"],
        },
    }


def _ranker(hits: dict[str, list[str]]):
    gold_facts = _gold()["facts"]

    def rank(index, query: str) -> list:
        by_id = {chunk["chunk_id"]: chunk for chunk in index.get("chunks") or []}
        for fact in gold_facts:
            if str(fact.get("query") or "") == query:
                return [by_id[item] for item in hits.get(fact["fact_id"], []) if item in by_id]
        return []

    return rank


def _pins() -> dict[str, str]:
    return {
        "gold": text_chunk_metrics.GOLD_UNSEALED_SHA256,
        "census": "b60d9791eb1bc56a8e417df48c22e3b22faa9f3a44bf97022ec58d96495792f7",
        "store": engine_e2e.STORE_SHA256,
        "gzip": dense_r5de.GZIP_SHA256,
        "curves_v3": engine_e2e.CURVES_V3_SHA256,
        "curves_v4": dense_r5de.CURVES_V4_SHA256,
        "curves_d3": dense_r5de.CURVES_D3_SHA256,
        "error_analysis": engine_e2e.ERROR_ANALYSIS_SHA256,
    }


def test_recipe_pins_published_card_before_encode():
    recipe = dense_r5de.bge_recipe()
    assert recipe["query_instruction"] == dense_r5de.BGE_QUERY_INSTRUCTION
    assert recipe["passage_instruction"] == ""
    assert recipe["model"] == dense_r5de.BGE_ID
    assert recipe["dim"] == 768
    naive = dict(recipe)
    naive["query_instruction"] = ""
    with pytest.raises(TextGoldError) as error:
        dense_r5de.encode_with_recipe(["x"], naive, "query", embedder=lambda texts, model: (model, [[0.0] * 768]))
    assert error.value.code == "bge_recipe"


def test_encode_query_uses_instruction_passage_does_not():
    seen: list[str] = []

    def fake(texts, model_name=None):
        seen.extend(texts)
        return dense_r5de.BGE_ID, [[0.0] * 768 for _ in texts]

    recipe = dense_r5de.bge_recipe()
    dense_r5de.encode_with_recipe(["short q"], recipe, "query", embedder=fake)
    dense_r5de.encode_with_recipe(["passage text"], recipe, "passage", embedder=fake)
    assert seen[0] == dense_r5de.BGE_QUERY_INSTRUCTION + "short q"
    assert seen[1] == "passage text"


def test_small_and_e5_withdrawn():
    recipe = dense_r5de.bge_recipe()
    recipe["model"] = "BAAI/bge-small-en-v1.5"
    with pytest.raises(TextGoldError) as error:
        dense_r5de.require_bge_recipe(recipe)
    assert error.value.code == "bge_recipe"


def test_minilm_384_path_still_scores(monkeypatch):
    seen = []

    def fake(texts, model_name=None):
        seen.extend(texts)
        return engine_e2e.MINILM_ID, [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    rows = research._search_index(_minilm_index(), NEEDLE_A, 5, "dense")
    assert rows
    assert seen == [NEEDLE_A]


def test_mixed_768_query_on_384_index_unavailable(monkeypatch):
    def fake(texts, model_name=None):
        return engine_e2e.MINILM_ID, [[0.0] * 768 for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    with pytest.raises(ValueError) as error:
        research._search_index(_minilm_index(), NEEDLE_A, 5, "dense")
    assert str(error.value) == "dense_index_unavailable"


def test_bge_768_path_records_loaded_dim(monkeypatch):
    def fake(texts, model_name=None):
        assert model_name == dense_r5de.BGE_ID
        return dense_r5de.BGE_ID, [[1.0] + [0.0] * 767 for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    rows = research._search_index(_bge_index(), NEEDLE_A, 5, "dense")
    assert rows
    assert rows[0]["chunk_id"] == "c-a"


def test_bge_query_instruction_reaches_encoder(monkeypatch):
    seen = []

    def fake(texts, model_name=None):
        seen.extend(texts)
        return dense_r5de.BGE_ID, [[1.0] + [0.0] * 767 for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    research._search_index(_bge_index(), NEEDLE_A, 5, "dense")
    assert seen == [dense_r5de.BGE_QUERY_INSTRUCTION + NEEDLE_A]


def test_write_gzip_refuses_sealed_name(tmp_path):
    dest = tmp_path / "t5-indexed-unsealed.json.gz"
    with pytest.raises(TextGoldError) as error:
        dense_r5de._write_gzip(dest, {"chunks": []})
    assert error.value.code == "protected_persist"


def test_contaminant_never_indexed():
    store = _store()
    store["indexed_paper_sha256"] = [IDX, CONTAMINANT_SHA256]
    with pytest.raises(TextGoldError) as error:
        dense_r5de._refuse_contaminant(store, _minilm_index())
    assert error.value.code == "contaminant_indexed"


def test_artifact_stamps_lineage_not_c11():
    control_hits = {FIRE_A: ["c-a"], FIRE_B: ["c-b"], FIRE_C: [], FIRE_D: []}
    bge_hits = {FIRE_A: ["c-a"], FIRE_B: ["c-b"], FIRE_C: ["c-c"], FIRE_D: []}
    hold = {HOLD_ID: []}
    rankers = {
        "control_dense": _ranker(control_hits | hold),
        "control_hybrid": _ranker(control_hits | hold),
        "dense": _ranker(bge_hits | hold),
        "hybrid": _ranker(bge_hits | hold),
    }
    artifact = dense_r5de.build_r5de_artifact(
        gold=_gold(),
        minilm_index=_minilm_index(),
        bge_index=_bge_index(),
        pins=_pins(),
        recipe=dense_r5de.bge_recipe(),
        gzip_sha256="ab" * 32,
        rankers=rankers,
        require_control_counts=False,
    )
    assert artifact["schema"] == dense_r5de.CURVES_R5DE_SCHEMA
    assert artifact["is_c11"] is False
    assert artifact["is_c12"] is False
    assert artifact["c12_model_selection_made"] is False
    assert artifact["gold_sealed"] is False
    assert artifact["measurement_kind"] == "dense_retrieval_lineage"
    assert artifact["embedder"] == dense_r5de.BGE_ID
    assert artifact["dim"] == 768
    assert artifact["control_embedder"] == engine_e2e.MINILM_ID
    assert artifact["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    assert artifact["weights_retuned"] is False
    assert artifact["bars_moved"] is False
    assert artifact["recall_spec_sha256"]["v1"] == dense_r5de.RECALL_SPEC_V1_SHA256
    assert artifact["recall_spec_sha256"]["v1.1"] == dense_r5de.RECALL_SPEC_V11_SHA256
    assert artifact["recall_spec_sha256"]["v1.2"] == dense_r5de.RECALL_SPEC_V12_SHA256
    assert artifact["bge_recipe"]["query_instruction"] == dense_r5de.BGE_QUERY_INSTRUCTION
    paired = artifact["paired_at_k5"]["dense_vs_control_dense"]
    assert paired["fixed"] == [FIRE_C]
    assert paired["broken"] == []
    assert paired["net"] == 1
    blob = json.dumps(artifact)
    assert "needles" not in blob
    assert "evidence_quote" not in blob
    assert "canonical_text" not in blob
    assert "ZXQALPHA" not in blob
    for row in artifact["series"]:
        assert row["n_leaks_at_k"] == _k_map(0)
        assert row["n_must_refuse_at_k"] == _k_map(1)


def test_leak_fails_emit():
    leaked = _bge_index()
    leaked["chunks"][0]["body"] = f"body {NEEDLE_A} {NEEDLE_HOLD} body"
    leaked["chunks"][0]["text"] = leaked["chunks"][0]["body"]
    leak_hits = {
        FIRE_A: ["c-a"], FIRE_B: [], FIRE_C: [], FIRE_D: [],
        HOLD_ID: ["c-a"],
    }
    clean = {FIRE_A: ["c-a"], FIRE_B: [], FIRE_C: [], FIRE_D: [], HOLD_ID: []}
    rankers = {
        "control_dense": _ranker(clean),
        "control_hybrid": _ranker(clean),
        "dense": _ranker(leak_hits),
        "hybrid": _ranker(clean),
    }
    with pytest.raises(TextGoldError) as error:
        dense_r5de.build_r5de_artifact(
            gold=_gold(),
            minilm_index=_minilm_index(),
            bge_index=leaked,
            pins=_pins(),
            recipe=dense_r5de.bge_recipe(),
            gzip_sha256="ab" * 32,
            rankers=rankers,
            require_control_counts=False,
        )
    assert error.value.code == "refuse_leak"


def test_sidecar_gzip_roundtrip_new_name(tmp_path):
    dest = tmp_path / dense_r5de.R5DE_GZIP_NAME
    sha = dense_r5de._write_gzip(dest, _bge_index())
    assert dest.is_file()
    assert len(sha) == 64
    with gzip.open(dest, "rt", encoding="utf-8") as handle:
        loaded = json.load(handle)
    assert loaded["dense"]["dim"] == 768
    assert loaded["dense"]["model"] == dense_r5de.BGE_ID
