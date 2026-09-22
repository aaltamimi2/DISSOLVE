"""Synthetic BGE artifact writer tests. No real model or corpus."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
from pathlib import Path

import pytest

from dissolve import engine_e2e, research, text_chunk_metrics
from dissolve.contracts import tool_success
from dissolve.text_gold import TextGoldError

MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_DIM = 384
BGE_MODEL = research._BGE_MODEL_ID
BGE_DIM = research._BGE_DIM
BGE_REVISION = research._BGE_ENCODER_REVISION
BGE_QUERY = research._BGE_QUERY_INSTRUCTION
BGE_PASSAGE = research._BGE_PASSAGE_INSTRUCTION
FROZEN = engine_e2e.BGE_FROZEN_FLOOR
PRODUCT_KB = "t5-indexed-unsealed"
INDEX_SCHEMA = "dissolve.literature-index.v1"
CHUNK_A = "synth-writer-a"
CHUNK_B = "synth-writer-b"
CHUNK_C = "synth-writer-c"
TEXT_A = "zympoly writer solventblend"
TEXT_B = "helioxane writer solventblend"
TEXT_C = "polyflux writer solventblend"
SENTINEL = b"SENTINEL-UNCHANGED"

REFUSAL = (TextGoldError, research.LiteratureContractError)


def _unit(dim: int, axis: int) -> list[float]:
    row = [0.0] * dim
    row[axis % dim] = 1.0
    return row


def _doc(doc_key: str) -> dict:
    return {
        "document_id": f"D-{doc_key}",
        "sha256": hashlib.sha256(doc_key.encode("utf-8")).hexdigest(),
        "title": f"Synthetic {doc_key}",
        "source": "synthetic-local",
    }


def _chunk(chunk_id: str, text: str, doc_key: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "document_id": f"D-{doc_key}",
        "title": f"Synthetic {doc_key}",
        "source": "synthetic-local",
        "text": text,
        "body": text,
    }


def _make_index(chunks: list[tuple[str, str, str]], *, dense=None, knowledgebase=PRODUCT_KB) -> dict:
    return {
        "schema": INDEX_SCHEMA,
        "knowledgebase": knowledgebase,
        "documents": [_doc(doc_key) for _, _, doc_key in chunks],
        "chunks": [_chunk(chunk_id, text, doc_key) for chunk_id, text, doc_key in chunks],
        "dense": dense,
    }


def _bge_dense(chunk_ids: list[str], axes: list[int], **meta) -> dict:
    block = {
        "chunk_ids": list(chunk_ids),
        "vectors": [_unit(BGE_DIM, axis) for axis in axes],
        "model": BGE_MODEL,
        "dim": BGE_DIM,
        "query_instruction": BGE_QUERY,
        "passage_instruction": BGE_PASSAGE,
        "encoder_revision": BGE_REVISION,
    }
    block.update(meta)
    return block


def _three_source() -> dict:
    return _make_index([
        (CHUNK_A, TEXT_A, "a"),
        (CHUNK_B, TEXT_B, "b"),
        (CHUNK_C, TEXT_C, "c"),
    ])


def _reuse_ac() -> dict:
    return _make_index(
        [(CHUNK_A, TEXT_A, "a"), (CHUNK_C, TEXT_C, "c")],
        dense=_bge_dense([CHUNK_A, CHUNK_C], [0, 2]),
    )


def _empty_reuse() -> dict:
    return {
        "schema": INDEX_SCHEMA,
        "knowledgebase": PRODUCT_KB,
        "documents": [],
        "chunks": [],
        "dense": _bge_dense([], []),
    }


def _all_reuse() -> dict:
    return _make_index(
        [(CHUNK_A, TEXT_A, "a"), (CHUNK_B, TEXT_B, "b"), (CHUNK_C, TEXT_C, "c")],
        dense=_bge_dense([CHUNK_A, CHUNK_B, CHUNK_C], [0, 1, 2]),
    )


def _prepared(chunks: list[dict]) -> list[str]:
    return [research.chunk_sparse_corpus(chunk) for chunk in chunks]


def _embedder(model_id, vectors, *, calls):
    def fake(texts, selected=None):
        calls.append((list(texts), selected))
        if callable(vectors):
            return model_id, vectors(texts)
        return model_id, copy.deepcopy(vectors)
    return fake


def _forbidden_embedder(calls):
    def fake(texts, selected=None):
        calls.append((list(texts), selected))
        raise AssertionError("encoder called")
    return fake


def _snapshot(payload) -> str:
    return json.dumps(payload, sort_keys=True)


def _write_gzip_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _owned_paths(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "owned"
    return root / "bge.json.gz", root / "BGE10.manifest.json"


def _write(tmp_path: Path, *, source, reuse, embedder, **overrides):
    index_path, manifest_path = _owned_paths(tmp_path)
    kwargs = {
        "manifest_path": manifest_path,
        "expected_n_chunks": len(source.get("chunks") or []),
        "embedder": embedder,
        "model_name": BGE_MODEL,
        "expected_dim": BGE_DIM,
        "source_index": source,
        "reuse_index": reuse,
        "output_index_path": index_path,
        "frozen_floor": FROZEN,
    }
    kwargs.update(overrides)
    return engine_e2e.emit_e2e_3(**kwargs), Path(kwargs["output_index_path"]), Path(kwargs["manifest_path"])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    monkeypatch.delenv("DISSOLVE_EMBEDDING_MODEL", raising=False)
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
    monkeypatch.setattr(
        research,
        "_canonical_product_index_path",
        lambda: tmp_path / "canonical" / f"{PRODUCT_KB}.json.gz",
    )
    monkeypatch.setattr(
        research,
        "_product_manifest_path",
        lambda: tmp_path / "canonical" / "INDEX.t5.unsealed.v1.json",
    )
    monkeypatch.setattr(text_chunk_metrics, "GOLD_V2_PATH", tmp_path / "absent-GOLD.v2.json")
    def fail(*args, **kwargs):
        raise AssertionError("forbidden helper called")
    monkeypatch.setattr(research, "_union_floor", fail, raising=False)
    yield {"tmp_path": tmp_path}


@pytest.fixture
def bge_spies(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("legacy helper called")
    monkeypatch.setattr(research, "_load_index", fail)
    monkeypatch.setattr(research, "_save_index", fail)
    monkeypatch.setattr(research, "search_literature_corpus", fail)
    monkeypatch.setattr(research, "_union_product_and_sidecar", fail)
    monkeypatch.setattr(research, "merge_literature_graph", fail)
    monkeypatch.setattr(engine_e2e, "hybrid_envelope_ok", fail)
    return fail


def test_three_chunk_two_reuse_encodes_missing_in_source_order(tmp_path, bge_spies):
    source = _three_source()
    reuse = _reuse_ac()
    source_snap = _snapshot(source)
    reuse_snap = _snapshot(reuse)
    calls: list = []
    result, index_path, manifest_path = _write(
        tmp_path,
        source=source,
        reuse=reuse,
        embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
    )
    assert _snapshot(source) == source_snap
    assert _snapshot(reuse) == reuse_snap
    assert calls == [([research.chunk_sparse_corpus(source["chunks"][1])], BGE_MODEL)]
    assert result["n_vectors"] == 3
    assert result["n_chunks"] == 3
    assert result["n_documents"] == 3
    assert result["model"] == BGE_MODEL
    assert result["dim"] == BGE_DIM
    assert result["status"] == engine_e2e.BGE_EXPLORATORY_STATUS
    assert result["gzip_sha256"] == hashlib.sha256(index_path.read_bytes()).hexdigest()
    decoded = research._read_gzip_json_bytes(index_path.read_bytes())
    assert decoded["chunks"] == source["chunks"]
    assert decoded["documents"] == source["documents"]
    assert decoded["dense"]["vectors"][0] == _unit(BGE_DIM, 0)
    assert decoded["dense"]["vectors"][1] == _unit(BGE_DIM, 1)
    assert decoded["dense"]["vectors"][2] == _unit(BGE_DIM, 2)
    assert decoded["dense"]["chunk_ids"] == [CHUNK_A, CHUNK_B, CHUNK_C]
    assert decoded["dense"]["encoder_revision"] == BGE_REVISION
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["abstention"]["floor"] == FROZEN
    assert manifest["gzip_sha256"] == result["gzip_sha256"]
    assert manifest["dense"]["chunk_ids"] == [CHUNK_A, CHUNK_B, CHUNK_C]
    assert manifest["status"] == engine_e2e.BGE_EXPLORATORY_STATUS


def test_all_reused_zero_encoder_calls(tmp_path, bge_spies):
    calls: list = []
    result, index_path, _manifest = _write(
        tmp_path,
        source=_three_source(),
        reuse=_all_reuse(),
        embedder=_forbidden_embedder(calls),
    )
    assert calls == []
    decoded = research._read_gzip_json_bytes(index_path.read_bytes())
    assert decoded["dense"]["vectors"] == [_unit(BGE_DIM, 0), _unit(BGE_DIM, 1), _unit(BGE_DIM, 2)]
    assert result["n_vectors"] == 3


def test_empty_reuse_encodes_all_source_passages(tmp_path, bge_spies):
    source = _three_source()
    calls: list = []
    vectors = [_unit(BGE_DIM, 0), _unit(BGE_DIM, 1), _unit(BGE_DIM, 2)]
    _result, index_path, _manifest = _write(
        tmp_path,
        source=source,
        reuse=_empty_reuse(),
        embedder=_embedder(BGE_MODEL, vectors, calls=calls),
    )
    assert calls == [(_prepared(source["chunks"]), BGE_MODEL)]
    decoded = research._read_gzip_json_bytes(index_path.read_bytes())
    assert decoded["dense"]["vectors"] == vectors


def test_invalid_empty_reuse_is_not_discarded(tmp_path, bge_spies):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    invalid = {
        "schema": INDEX_SCHEMA,
        "knowledgebase": PRODUCT_KB,
        "documents": [],
        "chunks": [],
        "dense": None,
    }
    with pytest.raises(REFUSAL):
        _write(
            tmp_path,
            source=_three_source(),
            reuse=invalid,
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 0)], calls=calls),
        )
    assert calls == []
    assert not index_path.exists()
    assert not manifest_path.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        "minilm_recipe",
        "wrong_revision",
        "wrong_query",
        "wrong_dim",
        "zero_geometry",
        "foreign_id",
        "duplicate_reuse",
        "changed_content",
        "membership",
        "duplicate_source",
        "wrong_kb",
        "wrong_count",
    ],
)
def test_identity_recipe_geometry_membership_refuse_without_io(tmp_path, bge_spies, mutate):
    source = _three_source()
    reuse = _reuse_ac()
    expected = 3
    if mutate == "minilm_recipe":
        reuse["dense"]["model"] = MINILM_MODEL
        reuse["dense"]["dim"] = MINILM_DIM
        reuse["dense"]["vectors"] = [_unit(MINILM_DIM, 0), _unit(MINILM_DIM, 1)]
    elif mutate == "wrong_revision":
        reuse["dense"]["encoder_revision"] = "0" * 40
    elif mutate == "wrong_query":
        reuse["dense"]["query_instruction"] = "not-the-pinned-query"
    elif mutate == "wrong_dim":
        reuse["dense"]["dim"] = MINILM_DIM
        reuse["dense"]["vectors"] = [_unit(MINILM_DIM, 0), _unit(MINILM_DIM, 2)]
    elif mutate == "zero_geometry":
        reuse["dense"]["vectors"][0] = [0.0] * BGE_DIM
    elif mutate == "foreign_id":
        reuse = _make_index(
            [(CHUNK_A, TEXT_A, "a"), ("synth-foreign", TEXT_C, "c")],
            dense=_bge_dense([CHUNK_A, "synth-foreign"], [0, 2]),
        )
    elif mutate == "duplicate_reuse":
        reuse["dense"]["chunk_ids"] = [CHUNK_A, CHUNK_A]
        reuse["chunks"] = [reuse["chunks"][0], copy.deepcopy(reuse["chunks"][0])]
    elif mutate == "changed_content":
        reuse["chunks"][0] = dict(reuse["chunks"][0], body="altered reused body", text="altered reused body")
    elif mutate == "membership":
        reuse["dense"]["vectors"] = [reuse["dense"]["vectors"][0]]
    elif mutate == "duplicate_source":
        source["chunks"] = [source["chunks"][0], copy.deepcopy(source["chunks"][0]), source["chunks"][2]]
    elif mutate == "wrong_kb":
        source["knowledgebase"] = "synth-user-lib"
    elif mutate == "wrong_count":
        expected = 4
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    with pytest.raises(REFUSAL):
        _write(
            tmp_path,
            source=source,
            reuse=reuse,
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
            expected_n_chunks=expected,
        )
    assert calls == []
    assert not index_path.exists()
    assert not manifest_path.exists()


def test_wrong_returned_encoder_model_zero_writes(tmp_path, bge_spies):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(MINILM_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
        )
    assert caught.value.code == "dense_model"
    assert calls == [([research.chunk_sparse_corpus(_three_source()["chunks"][1])], BGE_MODEL)]
    assert not index_path.exists()
    assert not manifest_path.exists()


@pytest.mark.parametrize("kind", ["zero", "non_unit", "nan", "inf", "ragged"])
def test_malformed_new_vectors_zero_writes(tmp_path, bge_spies, kind):
    good = _unit(BGE_DIM, 1)
    if kind == "zero":
        vectors = [[0.0] * BGE_DIM]
    elif kind == "non_unit":
        row = list(good)
        row[1] = 2.0
        vectors = [row]
    elif kind == "nan":
        row = list(good)
        row[0] = math.nan
        vectors = [row]
    elif kind == "inf":
        row = list(good)
        row[0] = math.inf
        vectors = [row]
    else:
        vectors = [good[:-1]]
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    with pytest.raises(REFUSAL):
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, vectors, calls=calls),
        )
    assert calls
    assert not index_path.exists()
    assert not manifest_path.exists()


def test_exact_frozen_floor_preserved(tmp_path, bge_spies):
    calls: list = []
    _result, _index_path, manifest_path = _write(
        tmp_path,
        source=_three_source(),
        reuse=_reuse_ac(),
        embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["abstention"]["floor"] == 0.3698406656908355
    assert payload["abstention"]["floor"] == FROZEN


@pytest.mark.parametrize("value", [True, False, "0.3698406656908355", 0.37, math.nan, math.inf, -math.inf, 0, None])
def test_invalid_floor_variants_refuse(tmp_path, bge_spies, value):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    kwargs = {}
    if value is None:
        kwargs["frozen_floor"] = None
    else:
        kwargs["frozen_floor"] = value
    with pytest.raises(REFUSAL):
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
            **kwargs,
        )
    assert calls == []
    assert not index_path.exists()
    assert not manifest_path.exists()


def test_incomplete_bge_inputs_refuse(tmp_path, bge_spies):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    with pytest.raises(TextGoldError) as caught:
        engine_e2e.emit_e2e_3(
            source_index=_three_source(),
            reuse_index=_reuse_ac(),
            frozen_floor=FROZEN,
            model_name=BGE_MODEL,
            expected_dim=BGE_DIM,
            expected_n_chunks=3,
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
            manifest_path=manifest_path,
        )
    assert caught.value.code == "bge_writer_incomplete"
    assert calls == []
    assert not index_path.exists()
    assert not manifest_path.exists()


def test_minilm_model_with_bge_inputs_refuses(tmp_path, bge_spies):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
            model_name=MINILM_MODEL,
            expected_dim=MINILM_DIM,
        )
    assert caught.value.code == "dense_model"
    assert calls == []
    assert not index_path.exists()
    assert not manifest_path.exists()


def test_distinct_new_paths_required(tmp_path, bge_spies):
    calls: list = []
    index_path, _manifest_path = _owned_paths(tmp_path)
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
            output_index_path=index_path,
            manifest_path=index_path,
        )
    assert caught.value.code == "dest_not_distinct"
    assert calls == []
    assert not index_path.exists()


@pytest.mark.parametrize("name", ["t5-indexed-unsealed.json.gz", "INDEX.t5.unsealed.v1.json"])
def test_protected_minilm_basenames_refused(tmp_path, bge_spies, name):
    calls: list = []
    dest = tmp_path / "elsewhere" / name
    other = tmp_path / "owned" / "ok-manifest.json" if name.endswith(".gz") else tmp_path / "owned" / "ok.json.gz"
    kwargs = {"output_index_path": dest, "manifest_path": other} if name.endswith(".gz") else {
        "output_index_path": other,
        "manifest_path": dest,
    }
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
            **kwargs,
        )
    assert caught.value.code == "protected_persist"
    assert calls == []
    assert not dest.exists()
    assert not other.exists()


def test_existing_target_sentinel_unchanged(tmp_path, bge_spies):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(SENTINEL)
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
        )
    assert caught.value.code == "dest_exists"
    assert index_path.read_bytes() == SENTINEL
    assert not manifest_path.exists()
    assert calls == []


def test_symlink_target_sentinel_unchanged(tmp_path, bge_spies):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    target = tmp_path / "sentinel.bin"
    target.write_bytes(SENTINEL)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.symlink_to(target)
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
        )
    assert caught.value.code == "dest_symlink"
    assert target.read_bytes() == SENTINEL
    assert index_path.is_symlink()
    assert not manifest_path.exists()
    assert calls == []


def test_exclusive_create_collision_does_not_overwrite(tmp_path, bge_spies, monkeypatch):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(SENTINEL)
    original_exists = Path.exists
    original_symlink = Path.is_symlink

    def fake_exists(self):
        if Path(self).name == index_path.name and Path(self).parent.resolve() == index_path.parent.resolve():
            return False
        return original_exists(self)

    def fake_symlink(self):
        if Path(self).name == index_path.name and Path(self).parent.resolve() == index_path.parent.resolve():
            return False
        return original_symlink(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "is_symlink", fake_symlink)
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
        )
    assert caught.value.code == "dest_exists"
    assert index_path.read_bytes() == SENTINEL
    assert not manifest_path.exists()


def test_injected_manifest_write_failure_leaves_index(tmp_path, bge_spies, monkeypatch):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)
    real = engine_e2e._exclusive_create_write

    def boom(path, data):
        if Path(path).resolve() == manifest_path.resolve():
            raise OSError("injected manifest failure")
        return real(path, data)

    monkeypatch.setattr(engine_e2e, "_exclusive_create_write", boom)
    with pytest.raises(OSError, match="injected manifest failure"):
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
        )
    assert index_path.is_file()
    assert not manifest_path.exists()
    assert calls


def test_read_back_refusal_skips_manifest(tmp_path, bge_spies, monkeypatch):
    calls: list = []
    index_path, manifest_path = _owned_paths(tmp_path)

    def fake_verify(*args, **kwargs):
        raise TextGoldError("bge10_index_digest", "injected read-back refusal")

    monkeypatch.setattr(engine_e2e, "_verify_written_bge_index", fake_verify)
    with pytest.raises(TextGoldError) as caught:
        _write(
            tmp_path,
            source=_three_source(),
            reuse=_reuse_ac(),
            embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
        )
    assert caught.value.code == "bge10_index_digest"
    assert index_path.is_file()
    assert not manifest_path.exists()


def test_p2_loader_round_trip_exact_floor_and_membership(tmp_path, bge_spies, monkeypatch):
    calls: list = []
    result, index_path, manifest_path = _write(
        tmp_path,
        source=_three_source(),
        reuse=_reuse_ac(),
        embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
    )
    previous = None
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(manifest_path))
    try:
        loaded = research._load_bge10_product_index()
    finally:
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        if previous is not None:
            monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", previous)
    assert loaded["abstention"]["floor"] == FROZEN
    assert loaded["dense"]["chunk_ids"] == [CHUNK_A, CHUNK_B, CHUNK_C]
    assert loaded["dense"]["model"] == BGE_MODEL
    assert loaded["dense"]["encoder_revision"] == BGE_REVISION
    assert loaded["knowledgebase"] == PRODUCT_KB
    assert result["status"] == engine_e2e.BGE_EXPLORATORY_STATUS
    assert "hybrid_ok" not in result
    loaded_path = research._resolve_against(
        Path(manifest_path).resolve().parent,
        json.loads(manifest_path.read_text(encoding="utf-8"))["index_path"],
    )
    assert loaded_path == index_path.resolve()
    assert loaded_path.is_file()


def test_relative_destinations_p2_load_after_cwd_change(tmp_path, bge_spies, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    calls: list = []
    result = engine_e2e.emit_e2e_3(
        manifest_path="artifacts/bge.json",
        expected_n_chunks=3,
        embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1)], calls=calls),
        model_name=BGE_MODEL,
        expected_dim=BGE_DIM,
        source_index=_three_source(),
        reuse_index=_reuse_ac(),
        output_index_path="artifacts/bge.gz",
        frozen_floor=FROZEN,
    )
    written_index = (work / "artifacts" / "bge.gz").resolve()
    written_manifest = (work / "artifacts" / "bge.json").resolve()
    assert written_index.is_file()
    assert written_manifest.is_file()
    payload = json.loads(written_manifest.read_text(encoding="utf-8"))
    assert payload["index_path"] != "artifacts/bge.gz"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(written_manifest))
    try:
        loaded = research._load_bge10_product_index()
        resolved = research._resolve_against(written_manifest.parent, payload["index_path"])
    finally:
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    assert resolved == written_index
    assert not (elsewhere / "artifacts" / "bge.gz").exists()
    assert loaded["abstention"]["floor"] == FROZEN
    assert loaded["dense"]["chunk_ids"] == [CHUNK_A, CHUNK_B, CHUNK_C]
    assert loaded["dense"]["model"] == BGE_MODEL
    assert result["n_vectors"] == 3


def test_minilm_path_owned_fixtures_keeps_384(tmp_path, monkeypatch):
    source = _make_index(
        [(CHUNK_A, TEXT_A, "a"), (CHUNK_B, TEXT_B, "b")],
        knowledgebase=PRODUCT_KB,
    )
    home = tmp_path / "minilm-home"
    planted = home / f"{PRODUCT_KB}.json.gz"
    _write_gzip_json(planted, source)
    calls: list = []
    monkeypatch.setattr(
        research,
        "search_literature_corpus",
        lambda *args, **kwargs: tool_success("search_literature_corpus"),
    )
    dest = tmp_path / "owned" / "minilm-manifest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = engine_e2e.emit_e2e_3(
        index_home=home,
        manifest_path=dest,
        expected_n_chunks=2,
        embedder=_embedder(MINILM_MODEL, [_unit(MINILM_DIM, 0), _unit(MINILM_DIM, 1)], calls=calls),
        skip_pin_check=True,
    )
    assert result["dim"] == MINILM_DIM
    assert result["model"] == MINILM_MODEL
    assert result["hybrid_ok"] is True
    assert result["dense_ok"] is True
    assert calls == [(_prepared(source["chunks"]), MINILM_MODEL)]
    assert json.loads(dest.read_text(encoding="utf-8"))["dense"]["dim"] == 384


def test_minilm_path_rejects_conflicting_dimension(tmp_path, monkeypatch):
    source = _make_index(
        [(CHUNK_A, TEXT_A, "a"), (CHUNK_B, TEXT_B, "b")],
        knowledgebase=PRODUCT_KB,
    )
    home = tmp_path / "minilm-home"
    _write_gzip_json(home / f"{PRODUCT_KB}.json.gz", source)
    calls: list = []
    with pytest.raises(TextGoldError) as caught:
        engine_e2e.emit_e2e_3(
            index_home=home,
            manifest_path=tmp_path / "owned" / "minilm-manifest.json",
            expected_n_chunks=2,
            embedder=_embedder(MINILM_MODEL, [_unit(MINILM_DIM, 0), _unit(MINILM_DIM, 1)], calls=calls),
            skip_pin_check=True,
            expected_dim=BGE_DIM,
        )
    assert caught.value.code == "dense_dim"
    assert calls == []
