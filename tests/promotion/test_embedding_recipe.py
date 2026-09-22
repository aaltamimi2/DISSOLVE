"""Synthetic encoder identity and in-memory embedding tests. No real model."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from dissolve import research

MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
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


def _unit(dim: int, axis: int) -> list[float]:
    row = [0.0] * dim
    row[axis] = 1.0
    return row


def _chunk(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "body": text,
        "sha256": f"sha-{chunk_id}",
        "document_id": "D-synth",
        "title": "Synthetic embed",
        "source": "synthetic-local",
    }


def _index() -> dict:
    return {
        "schema": "dissolve.literature-index.v1",
        "knowledgebase": KB,
        "documents": [{"document_id": "D-synth", "sha256": "doc-synth", "title": "Synthetic embed"}],
        "chunks": [_chunk(CHUNK_A, TEXT_A), _chunk(CHUNK_B, TEXT_B)],
        "dense": None,
    }


def _prepared() -> list[str]:
    return [research.chunk_sparse_corpus(chunk) for chunk in _index()["chunks"]]


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


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    monkeypatch.delenv("DISSOLVE_EMBEDDING_MODEL", raising=False)
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
    monkeypatch.setattr(
        research.rerank,
        "_load_cross_encoder",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("model forbidden")),
    )
    yield {"tmp_path": tmp_path}


def _init_calls(fake):
    return [item for item in fake.calls if item[0] == "init"]


def _encode_calls(fake):
    return [item for item in fake.calls if item[0] == "encode"]


def test_bge_constructor_pins_revision_and_cpu(monkeypatch):
    prepared = list(_prepared())
    fake = _install_fake_st(monkeypatch, output=[_unit(BGE_DIM, 0), _unit(BGE_DIM, 1)])
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
    assert vectors == [_unit(BGE_DIM, 0), _unit(BGE_DIM, 1)]
    assert inits[0][1][0] != MINILM_MODEL


def test_minilm_no_argument_selection(monkeypatch):
    prepared = list(_prepared())
    fake = _install_fake_st(monkeypatch, output=[_unit(MINILM_DIM, 0), _unit(MINILM_DIM, 1)])
    model_id, _vectors = research._dense_vectors(prepared)
    inits = _init_calls(fake)
    assert model_id == MINILM_MODEL
    assert inits[0][1] == (MINILM_MODEL,)
    assert inits[0][2] == {}
    assert inits[0][1][0] != BGE_MODEL


def test_explicit_minilm_ignores_bge_environment(monkeypatch):
    monkeypatch.setenv("DISSOLVE_EMBEDDING_MODEL", BGE_MODEL)
    prepared = list(_prepared())
    fake = _install_fake_st(monkeypatch, output=[_unit(MINILM_DIM, 0)])
    model_id, _vectors = research._dense_vectors([prepared[0]], MINILM_MODEL)
    inits = _init_calls(fake)
    assert model_id == MINILM_MODEL
    assert inits[0][1] == (MINILM_MODEL,)
    assert inits[0][2] == {}
    assert "revision" not in inits[0][2]


def test_output_rounds_to_eight_decimals(monkeypatch):
    raw = 0.123456789123
    _install_fake_st(monkeypatch, output=[[raw, 0.0]])
    _model_id, vectors = research._dense_vectors(["alpha"], MINILM_MODEL)
    assert vectors == [[round(raw, 8), 0.0]]
    assert vectors[0][0] == 0.12345679


def test_env_selected_bge_uses_pinned_constructor(monkeypatch):
    monkeypatch.setenv("DISSOLVE_EMBEDDING_MODEL", BGE_MODEL)
    fake = _install_fake_st(monkeypatch, output=[_unit(BGE_DIM, 0)])
    model_id, _vectors = research._dense_vectors(["alpha"])
    inits = _init_calls(fake)
    assert model_id == BGE_MODEL
    assert inits[0][1] == (BGE_MODEL,)
    assert inits[0][2] == {"revision": BGE_REVISION, "device": "cpu"}


def test_load_failure_does_not_retry_or_substitute(monkeypatch):
    fake = _install_fake_st(monkeypatch, output=[[1.0]], fail_init=True)
    with pytest.raises(RuntimeError, match="could not be loaded or evaluated"):
        research._dense_vectors(["alpha"], BGE_MODEL)
    assert len(_init_calls(fake)) == 1
    assert _encode_calls(fake) == []
    assert _init_calls(fake)[0][1] == (BGE_MODEL,)


def test_encode_failure_does_not_retry(monkeypatch):
    fake = _install_fake_st(monkeypatch, output=[[1.0]], fail_encode=True)
    with pytest.raises(RuntimeError, match="could not be loaded or evaluated"):
        research._dense_vectors(["alpha"], BGE_MODEL)
    assert len(_init_calls(fake)) == 1
    assert len(_encode_calls(fake)) == 1


def _fault_vectors(kind: str, dim: int) -> list[list[float]]:
    good = [_unit(dim, 0), _unit(dim, 1)]
    if kind == "too_few":
        return [good[0]]
    if kind == "too_many":
        return [good[0], good[1], _unit(dim, 2 % dim)]
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


def _run_ingest(monkeypatch, *, model_id, vectors, save_calls):
    payload = _index()
    monkeypatch.setattr(research, "_load_index", lambda knowledgebase: copy.deepcopy(payload))
    seen_texts: list[list[str]] = []

    def fake_dense(texts, model_name=None):
        seen_texts.append(list(texts))
        return model_id, copy.deepcopy(vectors)

    def fake_save(index):
        save_calls.append(copy.deepcopy(index))
        return Path("synth-not-written.json.gz")

    monkeypatch.setattr(research, "_dense_vectors", fake_dense)
    monkeypatch.setattr(research, "_save_index", fake_save)
    return research._ingest_inputs([], [], KB, False, 20, True), seen_texts


def test_ingest_bge_metadata_and_passage_inputs(monkeypatch):
    saves: list[dict] = []
    result, seen_texts = _run_ingest(
        monkeypatch,
        model_id=BGE_MODEL,
        vectors=[_unit(BGE_DIM, 0), _unit(BGE_DIM, 1)],
        save_calls=saves,
    )
    assert result["dense_index_built"] is True
    assert result["dense_model"] == BGE_MODEL
    assert seen_texts == [_prepared()]
    assert all(not text.startswith(BGE_QUERY) for text in seen_texts[0])
    assert len(saves) == 1
    dense = saves[0]["dense"]
    assert dense["model"] == BGE_MODEL
    assert dense["dim"] == BGE_DIM
    assert dense["chunk_ids"] == [CHUNK_A, CHUNK_B]
    assert dense["query_instruction"] == BGE_QUERY
    assert dense["passage_instruction"] == ""
    assert dense["encoder_revision"] == BGE_REVISION
    assert dense["refuse_rule"] == research._REFUSE_RULE_SPARSE_GATED


def test_ingest_minilm_stamps_actual_dim(monkeypatch):
    saves: list[dict] = []
    _result, _seen = _run_ingest(
        monkeypatch,
        model_id=MINILM_MODEL,
        vectors=[_unit(MINILM_DIM, 0), _unit(MINILM_DIM, 1)],
        save_calls=saves,
    )
    dense = saves[0]["dense"]
    assert dense["model"] == MINILM_MODEL
    assert dense["dim"] == MINILM_DIM
    assert "query_instruction" not in dense
    assert dense["refuse_rule"] == research._REFUSE_RULE_SPARSE_GATED


@pytest.mark.parametrize(
    "kind",
    ["too_few", "too_many", "ragged", "zero", "non_unit", "nan", "inf"],
)
def test_ingest_malformed_vectors_do_not_save(kind, monkeypatch):
    saves: list[dict] = []
    with pytest.raises(research.LiteratureContractError) as caught:
        _run_ingest(
            monkeypatch,
            model_id=BGE_MODEL,
            vectors=_fault_vectors(kind, BGE_DIM),
            save_calls=saves,
        )
    assert caught.value.code == "dense_vectors_invalid"
    assert saves == []
    assert TEXT_A not in str(caught.value)
    assert TEXT_B not in str(caught.value)
    assert "injected" not in str(caught.value)
