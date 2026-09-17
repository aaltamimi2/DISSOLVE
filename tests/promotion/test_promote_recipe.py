"""Synthetic promote recipe tests. No real model, corpus, gold, or graph."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from dissolve import abstention_a2a4, dense_d2, engine_e2e, research, t5_corpus_graph, t5_promote, text_chunk_metrics
from dissolve.text_gold import TextGoldError

MINILM_MODEL = engine_e2e.MINILM_ID
MINILM_DIM = engine_e2e.EXPECTED_DIM
BGE_MODEL = research._BGE_MODEL_ID
BGE_DIM = research._BGE_DIM
BGE_REVISION = research._BGE_ENCODER_REVISION
BGE_QUERY = research._BGE_QUERY_INSTRUCTION
BGE_PASSAGE = research._BGE_PASSAGE_INSTRUCTION
SYNTH_FLOOR = 0.125
PAPER = "aa" * 32
PRIOR_PAPER = "bb" * 32
OTHER_PAPER = "dd" * 32
PRODUCT_PAPER = "cc" * 32
PRODUCT_CHUNK = "synth-product-chunk"
PRIOR_CHUNK = "synth-prior-chunk"
OTHER_CHUNK = "synth-other-chunk"
CHUNK_A = "synth-new-a"
CHUNK_B = "synth-new-b"
TEXT_PRODUCT = "zympoly product solventblend"
TEXT_PRIOR = "helioxane prior solventblend"
TEXT_OTHER = "polyflux other solventblend"
TEXT_A = "zympoly passage solventblend"
TEXT_B = "helioxane passage solventblend"
PRODUCT_KB = "t5-indexed-unsealed"
SIDECAR_KB = "t5-promoted-unsealed"


def _unit(dim: int, axis: int) -> list[float]:
    row = [0.0] * dim
    row[axis % dim] = 1.0
    return row


def _store_chunk(chunk_id: str, paper_sha256: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_sha256": paper_sha256,
        "body": text,
        "text": text,
        "page": 1,
        "section": "intro",
        "section_origin": "intro",
        "kind": "paragraph",
        "char_start": 0,
        "char_end": len(text),
    }


def _store(*, chunks: list[dict], indexed: list[str] | None = None, ingested: list[str] | None = None, pending=None) -> dict:
    payload = {
        "schema": engine_e2e.STORE_SCHEMA,
        "n_chunks": len(chunks),
        "indexed_paper_sha256": list(indexed or []),
        "ingested_paper_sha256": list(ingested or []),
        "chunks": chunks,
    }
    if pending is not None:
        payload["pending_dense"] = pending
    return payload


def _census(rows: list[tuple[str, str]]) -> dict:
    counts: dict[str, int] = {}
    papers = []
    for sha, status in rows:
        papers.append({"filename": f"{sha[:8]}.pdf", "sha256": sha, "bytes": 8, "status": status})
        counts[status] = int(counts.get(status) or 0) + 1
    return {"papers": papers, "counts_by_status": counts}


def _gold() -> dict:
    return {"papers": []}


def _minilm_dense(chunk_ids: list[str], axes: list[int]) -> dict:
    return {
        "model": MINILM_MODEL,
        "dim": MINILM_DIM,
        "chunk_ids": list(chunk_ids),
        "vectors": [_unit(MINILM_DIM, axis) for axis in axes],
    }


def _bge_dense(chunk_ids: list[str], axes: list[int], **meta) -> dict:
    block = {
        "model": BGE_MODEL,
        "dim": BGE_DIM,
        "chunk_ids": list(chunk_ids),
        "vectors": [_unit(BGE_DIM, axis) for axis in axes],
        "query_instruction": BGE_QUERY,
        "passage_instruction": BGE_PASSAGE,
        "encoder_revision": BGE_REVISION,
    }
    block.update(meta)
    return block


def _pending(model: str, dim: int, chunk_ids: list[str], vectors: list[list[float]], **meta) -> dict:
    block = {
        "model": model,
        "dim": dim,
        "chunk_ids": list(chunk_ids),
        "vectors": copy.deepcopy(vectors),
    }
    if model == BGE_MODEL:
        block["query_instruction"] = BGE_QUERY
        block["passage_instruction"] = BGE_PASSAGE
        block["encoder_revision"] = BGE_REVISION
    block.update(meta)
    return block


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


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_gzip_index(path: Path, index: Mapping_alias := dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = engine_e2e._gzip_index_bytes(index)
    path.write_bytes(raw)
    return raw


def _product_index(*, model: str, dim: int) -> dict:
    chunk = {
        "chunk_id": PRODUCT_CHUNK,
        "sha256": hashlib.sha256(TEXT_PRODUCT.encode("utf-8")).hexdigest(),
        "document_id": f"D{PRODUCT_PAPER[:16]}",
        "paper_sha256": PRODUCT_PAPER,
        "title": "",
        "source": "",
        "page": 1,
        "section": "intro",
        "section_origin": "intro",
        "kind": "paragraph",
        "char_start": 0,
        "char_end": len(TEXT_PRODUCT),
        "text": TEXT_PRODUCT,
        "body": TEXT_PRODUCT,
        "token_estimate": 8,
    }
    dense = _bge_dense([PRODUCT_CHUNK], [0]) if model == BGE_MODEL else _minilm_dense([PRODUCT_CHUNK], [0])
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": PRODUCT_KB,
        "documents": [{
            "document_id": f"D{PRODUCT_PAPER[:16]}",
            "sha256": PRODUCT_PAPER,
            "title": "",
            "source": "",
            "parser_backend": "docling",
        }],
        "chunks": [chunk],
        "dense": dense,
    }


def _product_manifest(index_path: Path, digest: str, *, model: str, dim: int) -> dict:
    dense = {
        "model": model,
        "dim": dim,
        "n_vectors": 1,
    }
    if model == BGE_MODEL:
        dense.update(research._bge_generated_recipe_fields())
        dense["chunk_ids"] = [PRODUCT_CHUNK]
    return {
        "schema": engine_e2e.MANIFEST_SCHEMA,
        "knowledgebase": PRODUCT_KB,
        "index_path": index_path.name,
        "n_indexed_papers": 1,
        "n_chunks": 1,
        "indexed_paper_sha256": [PRODUCT_PAPER],
        "store_sha256": "0" * 64,
        "gzip_sha256": digest,
        "embedder_in_index": True,
        "dense": dense,
        "abstention": {
            "statistic": "query_idf_coverage",
            "percentile": 5,
            "floor": engine_e2e.BGE_FROZEN_FLOOR,
        },
    }


def _write_product_pair(directory: Path, *, model: str, dim: int) -> tuple[Path, Path, bytes, str]:
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / "t5-indexed-unsealed.json.gz"
    manifest_path = directory / "INDEX.t5.unsealed.v1.json"
    raw = _write_gzip_index(index_path, _product_index(model=model, dim=dim))
    digest = hashlib.sha256(raw).hexdigest()
    _write_json(manifest_path, _product_manifest(index_path, digest, model=model, dim=dim))
    return index_path, manifest_path, raw, digest


def _sidecar_index_from_store(store: dict, *, model: str, dim: int, dense: dict) -> dict:
    index = engine_e2e.store_to_literature_index(store, knowledgebase=SIDECAR_KB)
    index["dense"] = research._attach_generated_recipe({
        "model": model,
        "dim": dim,
        "chunk_ids": [str(row["chunk_id"]) for row in index["chunks"]],
        "vectors": list(dense["vectors"]),
    })
    if model == BGE_MODEL:
        index["dense"].update({
            "query_instruction": dense.get("query_instruction", BGE_QUERY),
            "passage_instruction": dense.get("passage_instruction", BGE_PASSAGE),
            "encoder_revision": dense.get("encoder_revision", BGE_REVISION),
        })
    return index


def fail(*args, **kwargs):
    raise AssertionError("protected helper called")


@pytest.fixture
def promote_env(monkeypatch, tmp_path):
    writes = {"json": [], "gzip": []}
    union_calls: list[dict] = []
    graph_calls: list[dict] = []
    ensure_calls: list = []
    protected = tmp_path / "protected"
    dest = tmp_path / "sidecar-dest"
    product_dir = tmp_path / "product"
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    monkeypatch.delenv("DISSOLVE_EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(t5_promote, "_refuse_gold_v2", lambda: None)
    monkeypatch.setattr(engine_e2e, "CENSUS_PATH", protected / "CENSUS.v3.json")
    monkeypatch.setattr(engine_e2e, "STORE_PATH", protected / "CHUNKS.t5.indexed.unsealed.v1.json")
    monkeypatch.setattr(engine_e2e, "MANIFEST_PATH", protected / "INDEX.t5.unsealed.v1.json")
    monkeypatch.setattr(dense_d2, "INDEX_GZIP_PATH", protected / "indexes" / "t5-indexed-unsealed.json.gz")
    monkeypatch.setattr(text_chunk_metrics, "GOLD_UNSEALED_PATH", protected / "GOLD.text.v1.unsealed.json")
    monkeypatch.setattr(text_chunk_metrics, "GOLD_V2_PATH", protected / "absent-GOLD.v2.json")
    monkeypatch.setattr(
        research,
        "_canonical_product_index_path",
        lambda: protected / "indexes" / "t5-indexed-unsealed.json.gz",
    )
    monkeypatch.setattr(
        research,
        "_product_manifest_path",
        lambda: protected / "INDEX.t5.unsealed.v1.json",
    )
    monkeypatch.setattr(dense_d2, "load_gzip_index", fail)
    monkeypatch.setattr(abstention_a2a4, "build_abstention_artifact", fail)
    monkeypatch.setattr(text_chunk_metrics, "split_gold", fail)
    monkeypatch.setattr(research, "_dense_vectors", fail)
    monkeypatch.setattr(research.rerank, "_load_cross_encoder", fail)
    real_json = t5_promote._write_json
    real_gzip = t5_promote._write_gzip
    real_ensure = t5_promote._ensure_dest_index

    def wrap_json(dest_path, payload):
        writes["json"].append(Path(dest_path).resolve())
        return real_json(dest_path, payload)

    def wrap_gzip(dest_path, index):
        writes["gzip"].append(Path(dest_path).resolve())
        return real_gzip(dest_path, index)

    def wrap_ensure(dest_dir, *, product_manifest_path=None):
        ensure_calls.append(None if product_manifest_path is None else Path(product_manifest_path).resolve())
        return real_ensure(dest_dir, product_manifest_path=product_manifest_path)

    def fake_union(**kwargs):
        union_calls.append(kwargs)
        dest_curves = Path(kwargs["dest_curves"])
        dest_curves.parent.mkdir(parents=True, exist_ok=True)
        dest_curves.write_text(json.dumps({"shipped_floor": SYNTH_FLOOR, "spy": True}) + "\n", encoding="utf-8")
        return {"shipped_floor": SYNTH_FLOOR}

    def fake_graph(**kwargs):
        graph_calls.append(kwargs)
        dest_graph = Path(kwargs["dest"])
        dest_graph.parent.mkdir(parents=True, exist_ok=True)
        dest_graph.write_text("{}\n", encoding="utf-8")
        return {"graph": str(dest_graph)}

    monkeypatch.setattr(t5_promote, "_write_json", wrap_json)
    monkeypatch.setattr(t5_promote, "_write_gzip", wrap_gzip)
    monkeypatch.setattr(t5_promote, "_ensure_dest_index", wrap_ensure)
    monkeypatch.setattr(t5_promote, "_union_floor", fake_union)
    monkeypatch.setattr(t5_corpus_graph, "emit_t5_corpus_graph", fake_graph)
    _write_json(engine_e2e.CENSUS_PATH, _census([(PRODUCT_PAPER, "indexed")]))
    _write_json(text_chunk_metrics.GOLD_UNSEALED_PATH, _gold())
    _write_json(
        engine_e2e.STORE_PATH,
        _store(chunks=[_store_chunk(PRODUCT_CHUNK, PRODUCT_PAPER, TEXT_PRODUCT)], indexed=[PRODUCT_PAPER]),
    )
    index_path, manifest_path, raw, digest = _write_product_pair(protected / "minilm-product", model=MINILM_MODEL, dim=MINILM_DIM)
    _write_json(engine_e2e.MANIFEST_PATH, _product_manifest(index_path, digest, model=MINILM_MODEL, dim=MINILM_DIM))
    return SimpleNamespace(
        tmp_path=tmp_path,
        dest=dest,
        protected=protected,
        product_dir=product_dir,
        writes=writes,
        union_calls=union_calls,
        graph_calls=graph_calls,
        ensure_calls=ensure_calls,
        minilm_index=index_path,
        minilm_manifest=manifest_path,
        minilm_bytes=raw,
        minilm_digest=digest,
    )


def _incremental(dest: Path, *, chunks: list[dict], pending=None, papers: list[str] | None = None) -> None:
    papers = papers or [PAPER]
    _write_json(dest / engine_e2e5_store_name(), _store(chunks=chunks, ingested=papers, pending=pending))
    _write_json(dest / engine_e2e5_census_name(), _census([(sha, "ingested") for sha in papers]))


def engine_e2e5_store_name() -> str:
    from dissolve import engine_e2e5
    return engine_e2e5.INCREMENTAL_STORE_PATH.name


def engine_e2e5_census_name() -> str:
    from dissolve import engine_e2e5
    return engine_e2e5.INCREMENTAL_CENSUS_PATH.name


def _seed_current_paper(dest: Path, *, pending=None, extra_chunks: list[dict] | None = None, extra_papers: list[str] | None = None) -> list[dict]:
    chunks = list(extra_chunks or [])
    current = [
        _store_chunk(CHUNK_A, PAPER, TEXT_A),
        _store_chunk(CHUNK_B, PAPER, TEXT_B),
    ]
    chunks.extend(current)
    papers = list(extra_papers or [])
    if PAPER not in papers:
        papers.append(PAPER)
    _incremental(dest, chunks=chunks, pending=pending, papers=papers)
    return current


def _promote_minilm(env, **kwargs):
    params = {
        "paper_sha256": PAPER,
        "dest_dir": env.dest,
    }
    params.update(kwargs)
    return t5_promote.promote_ingested_paper(**params)


def _promote_bge(env, *, product_index_path, product_manifest_path, **kwargs):
    params = {
        "paper_sha256": PAPER,
        "dest_dir": env.dest,
        "model_name": BGE_MODEL,
        "expected_dim": BGE_DIM,
        "product_index_path": product_index_path,
        "product_manifest_path": product_manifest_path,
    }
    params.update(kwargs)
    return t5_promote.promote_ingested_paper(**params)


def _snapshot_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_default_minilm_encodes_passages_and_keeps_legacy_metadata(promote_env):
    env = promote_env
    _seed_current_paper(env.dest)
    calls: list = []
    vectors = [_unit(MINILM_DIM, 1), _unit(MINILM_DIM, 2)]
    result = _promote_minilm(
        env,
        embedder=_embedder(MINILM_MODEL, vectors, calls=calls),
    )
    assert result["status"] == "ok"
    assert result["chunks_added"] == 2
    assert result["vectors_embedded"] == 2
    assert result["floor"] == SYNTH_FLOOR
    assert result["census_status"] == t5_promote.INDEXED_STATUS
    expected_texts = [research.chunk_sparse_corpus(_store_chunk(CHUNK_A, PAPER, TEXT_A)), research.chunk_sparse_corpus(_store_chunk(CHUNK_B, PAPER, TEXT_B))]
    assert calls == [(expected_texts, MINILM_MODEL)]
    assert all(not text.startswith(BGE_QUERY) for text in calls[0][0])
    gzip_path = env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME
    decoded = research._read_gzip_json_bytes(gzip_path.read_bytes())
    assert decoded["dense"]["model"] == MINILM_MODEL
    assert decoded["dense"]["dim"] == MINILM_DIM
    assert decoded["dense"]["chunk_ids"] == [CHUNK_A, CHUNK_B]
    assert decoded["dense"]["vectors"] == vectors
    assert "query_instruction" not in decoded["dense"]
    assert "encoder_revision" not in decoded["dense"]
    manifest = json.loads((env.dest / t5_promote.SIDECAR_INDEX_NAME).read_text(encoding="utf-8"))
    assert manifest["dense"] == {"model": MINILM_MODEL, "dim": MINILM_DIM, "n_vectors": 2}
    assert env.ensure_calls == [None]
    assert env.union_calls[0]["product_index"] is None
    assert env.union_calls[0]["product_gzip_path"] is None
    assert env.graph_calls


def test_default_legacy_noop_preserved(promote_env):
    env = promote_env
    prior_store = _store(
        chunks=[_store_chunk(CHUNK_A, PAPER, TEXT_A)],
        indexed=[PAPER],
    )
    _write_json(env.dest / t5_promote.SIDECAR_STORE_NAME, prior_store)
    _write_json(env.dest / engine_e2e5_census_name(), _census([(PAPER, "ingested")]))
    calls: list = []
    result = _promote_minilm(env, embedder=_forbidden_embedder(calls))
    assert result["status"] == "noop"
    assert result["chunks_added"] == 0
    assert result["vectors_embedded"] == 0
    assert calls == []
    assert env.writes["json"] == []
    assert env.writes["gzip"] == []
    assert env.union_calls == []
    assert env.graph_calls == []
    assert env.ensure_calls == []


def test_bge_one_pending_one_missing_encodes_once_and_stamps_recipe(promote_env):
    env = promote_env
    index_path, manifest_path, raw, digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    source_index = _snapshot_bytes(index_path)
    source_manifest = _snapshot_bytes(manifest_path)
    extra = [_store_chunk(OTHER_CHUNK, OTHER_PAPER, TEXT_OTHER)]
    pending = _pending(
        BGE_MODEL,
        BGE_DIM,
        [OTHER_CHUNK, CHUNK_A],
        [_unit(BGE_DIM, 3), _unit(BGE_DIM, 4)],
    )
    _seed_current_paper(env.dest, pending=pending, extra_chunks=extra, extra_papers=[OTHER_PAPER, PAPER])
    calls: list = []
    result = _promote_bge(
        env,
        product_index_path=index_path,
        product_manifest_path=manifest_path,
        embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 5)], calls=calls),
    )
    assert result["status"] == "ok"
    assert result["chunks_added"] == 2
    assert result["vectors_embedded"] == 1
    assert result["floor"] == SYNTH_FLOOR
    assert calls == [([research.chunk_sparse_corpus(_store_chunk(CHUNK_B, PAPER, TEXT_B))], BGE_MODEL)]
    assert not calls[0][0][0].startswith(BGE_QUERY)
    gzip_path = env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME
    decoded = research._read_gzip_json_bytes(gzip_path.read_bytes())
    assert decoded["dense"]["model"] == BGE_MODEL
    assert decoded["dense"]["dim"] == BGE_DIM
    assert decoded["dense"]["chunk_ids"] == [CHUNK_A, CHUNK_B]
    assert decoded["dense"]["vectors"] == [_unit(BGE_DIM, 4), _unit(BGE_DIM, 5)]
    assert decoded["dense"]["query_instruction"] == BGE_QUERY
    assert decoded["dense"]["passage_instruction"] == BGE_PASSAGE
    assert decoded["dense"]["encoder_revision"] == BGE_REVISION
    manifest = json.loads((env.dest / t5_promote.SIDECAR_INDEX_NAME).read_text(encoding="utf-8"))
    assert manifest["dense"]["model"] == BGE_MODEL
    assert manifest["dense"]["dim"] == BGE_DIM
    assert manifest["dense"]["n_vectors"] == 2
    assert manifest["dense"]["query_instruction"] == BGE_QUERY
    assert manifest["dense"]["passage_instruction"] == BGE_PASSAGE
    assert manifest["dense"]["encoder_revision"] == BGE_REVISION
    assert env.union_calls[0]["product_gzip_path"] == index_path
    assert env.union_calls[0]["product_gzip_sha256"] == digest
    assert env.union_calls[0]["product_index"]["dense"]["model"] == BGE_MODEL
    assert env.ensure_calls == [manifest_path.resolve()]
    assert _snapshot_bytes(index_path) == source_index == raw
    assert _snapshot_bytes(manifest_path) == source_manifest
    dest_index = json.loads((env.dest / t5_promote.PRODUCT_INDEX_NAME).read_text(encoding="utf-8"))
    assert dest_index["gzip_sha256"] == digest
    assert dest_index["dense"]["model"] == BGE_MODEL


def test_all_valid_pending_zero_encode(promote_env):
    env = promote_env
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    pending = _pending(
        BGE_MODEL,
        BGE_DIM,
        [CHUNK_A, CHUNK_B],
        [_unit(BGE_DIM, 1), _unit(BGE_DIM, 2)],
    )
    _seed_current_paper(env.dest, pending=pending)
    calls: list = []
    result = _promote_bge(
        env,
        product_index_path=index_path,
        product_manifest_path=manifest_path,
        embedder=_forbidden_embedder(calls),
    )
    assert result["status"] == "ok"
    assert result["vectors_embedded"] == 0
    assert calls == []
    decoded = research._read_gzip_json_bytes((env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME).read_bytes())
    assert decoded["dense"]["vectors"] == [_unit(BGE_DIM, 1), _unit(BGE_DIM, 2)]


def test_prior_compatible_sidecar_retained_unchanged(promote_env):
    env = promote_env
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    prior_store = _store(
        chunks=[_store_chunk(PRIOR_CHUNK, PRIOR_PAPER, TEXT_PRIOR)],
        indexed=[PRIOR_PAPER],
    )
    prior_dense = _bge_dense([PRIOR_CHUNK], [9])
    prior_index = _sidecar_index_from_store(prior_store, model=BGE_MODEL, dim=BGE_DIM, dense=prior_dense)
    _write_json(env.dest / t5_promote.SIDECAR_STORE_NAME, prior_store)
    _write_gzip_index(env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME, prior_index)
    pending = _pending(BGE_MODEL, BGE_DIM, [CHUNK_A, CHUNK_B], [_unit(BGE_DIM, 1), _unit(BGE_DIM, 2)])
    _seed_current_paper(env.dest, pending=pending)
    result = _promote_bge(
        env,
        product_index_path=index_path,
        product_manifest_path=manifest_path,
        embedder=_forbidden_embedder([]),
    )
    assert result["status"] == "ok"
    decoded = research._read_gzip_json_bytes((env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME).read_bytes())
    assert decoded["dense"]["chunk_ids"] == [PRIOR_CHUNK, CHUNK_A, CHUNK_B]
    assert decoded["dense"]["vectors"][0] == _unit(BGE_DIM, 9)
    assert decoded["dense"]["vectors"][1:] == [_unit(BGE_DIM, 1), _unit(BGE_DIM, 2)]


def test_explicit_minilm_product_pair_is_not_ignored(promote_env):
    env = promote_env
    index_path, manifest_path, raw, digest = _write_product_pair(env.product_dir, model=MINILM_MODEL, dim=MINILM_DIM)
    source = _snapshot_bytes(index_path)
    _seed_current_paper(env.dest)
    calls: list = []
    result = _promote_minilm(
        env,
        embedder=_embedder(MINILM_MODEL, [_unit(MINILM_DIM, 1), _unit(MINILM_DIM, 2)], calls=calls),
        product_index_path=index_path,
        product_manifest_path=manifest_path,
    )
    assert result["status"] == "ok"
    assert env.union_calls[0]["product_gzip_path"] == index_path
    assert env.union_calls[0]["product_gzip_sha256"] == digest
    assert env.ensure_calls == [manifest_path.resolve()]
    assert _snapshot_bytes(index_path) == source == raw
    assert env.union_calls[0]["product_index"] is not None


def _refuse_kwargs(env, **overrides):
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    pending = _pending(BGE_MODEL, BGE_DIM, [CHUNK_A], [_unit(BGE_DIM, 1)])
    _seed_current_paper(env.dest, pending=pending)
    params = {
        "product_index_path": index_path,
        "product_manifest_path": manifest_path,
        "embedder": _embedder(BGE_MODEL, [_unit(BGE_DIM, 2)], calls=[]),
    }
    params.update(overrides)
    return params


def _assert_zero_side_effects(env, before_dest: dict[str, str | None] | None = None):
    assert env.writes["json"] == []
    assert env.writes["gzip"] == []
    assert env.union_calls == []
    assert env.graph_calls == []
    assert env.ensure_calls == []


def test_missing_explicit_bge_product_pair_zero_writes(promote_env):
    env = promote_env
    _seed_current_paper(env.dest)
    with pytest.raises(TextGoldError) as caught:
        t5_promote.promote_ingested_paper(
            paper_sha256=PAPER,
            dest_dir=env.dest,
            model_name=BGE_MODEL,
            expected_dim=BGE_DIM,
            embedder=_forbidden_embedder([]),
        )
    assert caught.value.code == "bge_product_pair"
    _assert_zero_side_effects(env)


def test_partial_product_pair_zero_writes(promote_env):
    env = promote_env
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    _seed_current_paper(env.dest)
    with pytest.raises(TextGoldError) as caught:
        t5_promote.promote_ingested_paper(
            paper_sha256=PAPER,
            dest_dir=env.dest,
            model_name=BGE_MODEL,
            expected_dim=BGE_DIM,
            product_index_path=index_path,
            embedder=_forbidden_embedder([]),
        )
    assert caught.value.code == "bge_product_pair"
    _assert_zero_side_effects(env)
    with pytest.raises(TextGoldError) as caught:
        _promote_minilm(env, product_manifest_path=manifest_path, embedder=_forbidden_embedder([]))
    assert caught.value.code == "bge_product_pair"
    _assert_zero_side_effects(env)


def test_incompatible_minilm_product_under_bge_zero_writes(promote_env):
    env = promote_env
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=MINILM_MODEL, dim=MINILM_DIM)
    _seed_current_paper(env.dest, pending=_pending(BGE_MODEL, BGE_DIM, [CHUNK_A], [_unit(BGE_DIM, 1)]))
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(
            env,
            product_index_path=index_path,
            product_manifest_path=manifest_path,
            embedder=_forbidden_embedder([]),
        )
    assert caught.value.code == "dense_model"
    _assert_zero_side_effects(env)


def test_wrong_source_digest_zero_writes(promote_env):
    env = promote_env
    index_path, manifest_path, _raw, digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    flipped = "0" if digest[0] != "0" else "1"
    payload["gzip_sha256"] = flipped + digest[1:]
    _write_json(manifest_path, payload)
    _seed_current_paper(env.dest, pending=_pending(BGE_MODEL, BGE_DIM, [CHUNK_A], [_unit(BGE_DIM, 1)]))
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(
            env,
            product_index_path=index_path,
            product_manifest_path=manifest_path,
            embedder=_forbidden_embedder([]),
        )
    assert caught.value.code == "bge10_index_digest"
    _assert_zero_side_effects(env)


def test_wrong_selected_dimension_zero_writes(promote_env):
    env = promote_env
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    _seed_current_paper(env.dest)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(
            env,
            product_index_path=index_path,
            product_manifest_path=manifest_path,
            expected_dim=MINILM_DIM,
            embedder=_forbidden_embedder([]),
        )
    assert caught.value.code == "dense_dim"
    _assert_zero_side_effects(env)


def test_substitute_returned_model_zero_writes(promote_env):
    env = promote_env
    params = _refuse_kwargs(env)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(
            env,
            embedder=_embedder(MINILM_MODEL, [_unit(BGE_DIM, 2)], calls=[]),
            **{k: v for k, v in params.items() if k != "embedder"},
        )
    assert caught.value.code == "dense_model"
    _assert_zero_side_effects(env)


def test_duplicate_pending_ids_zero_writes(promote_env):
    env = promote_env
    params = _refuse_kwargs(
        env,
        # replaced below
    )
    pending = _pending(
        BGE_MODEL,
        BGE_DIM,
        [CHUNK_A, CHUNK_A],
        [_unit(BGE_DIM, 1), _unit(BGE_DIM, 2)],
    )
    _seed_current_paper(env.dest, pending=pending)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(env, **params)
    assert caught.value.code == "bge10_chunk_membership"
    _assert_zero_side_effects(env)


def test_unequal_pending_id_vector_counts_zip_truncation(promote_env):
    env = promote_env
    params = _refuse_kwargs(env)
    pending = _pending(BGE_MODEL, BGE_DIM, [CHUNK_A, CHUNK_B], [_unit(BGE_DIM, 1)])
    _seed_current_paper(env.dest, pending=pending)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(env, **params)
    assert caught.value.code == "n_chunks_mismatch"
    _assert_zero_side_effects(env)


def test_unknown_pending_id_zero_writes(promote_env):
    env = promote_env
    params = _refuse_kwargs(env)
    pending = _pending(BGE_MODEL, BGE_DIM, ["synth-unknown"], [_unit(BGE_DIM, 1)])
    _seed_current_paper(env.dest, pending=pending)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(env, **params)
    assert caught.value.code == "bge10_chunk_membership"
    _assert_zero_side_effects(env)


@pytest.mark.parametrize(
    ("pending_meta", "code"),
    [
        ({"model": MINILM_MODEL}, "dense_model"),
        ({"dim": MINILM_DIM}, "dense_dim"),
        ({"encoder_revision": "0" * 40}, "bge10_recipe_mismatch"),
    ],
)
def test_wrong_pending_model_revision_dimension_zero_writes(promote_env, pending_meta, code):
    env = promote_env
    params = _refuse_kwargs(env)
    pending = _pending(BGE_MODEL, BGE_DIM, [CHUNK_A], [_unit(BGE_DIM, 1)], **pending_meta)
    _seed_current_paper(env.dest, pending=pending)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(env, **params)
    assert caught.value.code == code
    _assert_zero_side_effects(env)


def test_wrong_prior_model_zero_writes(promote_env):
    env = promote_env
    params = _refuse_kwargs(env)
    prior_store = _store(chunks=[_store_chunk(PRIOR_CHUNK, PRIOR_PAPER, TEXT_PRIOR)], indexed=[PRIOR_PAPER])
    prior_index = _sidecar_index_from_store(
        prior_store,
        model=MINILM_MODEL,
        dim=MINILM_DIM,
        dense=_minilm_dense([PRIOR_CHUNK], [1]),
    )
    _write_json(env.dest / t5_promote.SIDECAR_STORE_NAME, prior_store)
    _write_gzip_index(env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME, prior_index)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(env, **params)
    assert caught.value.code == "dense_model"
    _assert_zero_side_effects(env)


def test_wrong_prior_revision_zero_writes(promote_env):
    env = promote_env
    params = _refuse_kwargs(env)
    prior_store = _store(chunks=[_store_chunk(PRIOR_CHUNK, PRIOR_PAPER, TEXT_PRIOR)], indexed=[PRIOR_PAPER])
    prior_dense = _bge_dense([PRIOR_CHUNK], [1], encoder_revision="0" * 40)
    prior_index = _sidecar_index_from_store(prior_store, model=BGE_MODEL, dim=BGE_DIM, dense=prior_dense)
    prior_index["dense"]["encoder_revision"] = "0" * 40
    _write_json(env.dest / t5_promote.SIDECAR_STORE_NAME, prior_store)
    _write_gzip_index(env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME, prior_index)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(env, **params)
    assert caught.value.code == "bge10_recipe_mismatch"
    _assert_zero_side_effects(env)


def test_conflicting_stored_membership_zero_writes(promote_env):
    env = promote_env
    params = _refuse_kwargs(env)
    prior_store = _store(chunks=[_store_chunk(PRIOR_CHUNK, PRIOR_PAPER, TEXT_PRIOR)], indexed=[PRIOR_PAPER])
    other_store = _store(chunks=[_store_chunk(OTHER_CHUNK, PRIOR_PAPER, TEXT_OTHER)], indexed=[PRIOR_PAPER])
    other_dense = _bge_dense([OTHER_CHUNK], [1])
    prior_index = _sidecar_index_from_store(other_store, model=BGE_MODEL, dim=BGE_DIM, dense=other_dense)
    _write_json(env.dest / t5_promote.SIDECAR_STORE_NAME, prior_store)
    _write_gzip_index(env.dest / "indexes" / t5_promote.SIDECAR_GZIP_NAME, prior_index)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(env, **params)
    assert caught.value.code == "dense_union_incompatible"
    _assert_zero_side_effects(env)


def _fault_vector(kind: str) -> list[float]:
    row = _unit(BGE_DIM, 2)
    if kind == "nan":
        row[0] = math.nan
    elif kind == "inf":
        row[0] = math.inf
    elif kind == "zero":
        row = [0.0] * BGE_DIM
    elif kind == "non_unit":
        row[0] = 2.0
    else:
        raise AssertionError(kind)
    return row


@pytest.mark.parametrize("kind", ["nan", "inf", "zero", "non_unit"])
def test_malformed_new_vectors_zero_writes(promote_env, kind):
    env = promote_env
    params = _refuse_kwargs(env)
    with pytest.raises(TextGoldError) as caught:
        _promote_bge(
            env,
            embedder=_embedder(BGE_MODEL, [_fault_vector(kind)], calls=[]),
            **{k: v for k, v in params.items() if k != "embedder"},
        )
    assert caught.value.code == "dense_dim"
    _assert_zero_side_effects(env)
    assert TEXT_A not in str(caught.value)
    assert TEXT_B not in str(caught.value)


def test_bge_canonical_destination_zero_writes(promote_env, monkeypatch):
    env = promote_env
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    with pytest.raises(TextGoldError) as caught:
        t5_promote.promote_ingested_paper(
            paper_sha256=PAPER,
            model_name=BGE_MODEL,
            expected_dim=BGE_DIM,
            product_index_path=index_path,
            product_manifest_path=manifest_path,
            embedder=_forbidden_embedder([]),
        )
    assert caught.value.code == "protected_persist"
    _assert_zero_side_effects(env)


def test_empty_absent_pending_encodes_both(promote_env):
    env = promote_env
    index_path, manifest_path, _raw, _digest = _write_product_pair(env.product_dir, model=BGE_MODEL, dim=BGE_DIM)
    _seed_current_paper(env.dest, pending=None)
    calls: list = []
    result = _promote_bge(
        env,
        product_index_path=index_path,
        product_manifest_path=manifest_path,
        embedder=_embedder(BGE_MODEL, [_unit(BGE_DIM, 1), _unit(BGE_DIM, 2)], calls=calls),
    )
    assert result["vectors_embedded"] == 2
    assert len(calls[0][0]) == 2
