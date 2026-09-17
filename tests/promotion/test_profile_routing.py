"""Synthetic profile-routing and compatible-dense-union tests. No real corpus."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path

import pytest

from dissolve import research
from dissolve.contracts import parse_tool_result

_OMIT = object()
PRODUCT_KB = "t5-indexed-unsealed"
SIDECAR_KB = "t5-promoted-unsealed"
INDEPENDENT_KB = "synth-user-lib"
INDEX_SCHEMA = "dissolve.literature-index.v1"
QUERY = "zympoly"
PRODUCT_CHUNK = "synth-product-chunk"
SIDECAR_CHUNK = "synth-sidecar-chunk"
INDEPENDENT_CHUNK = "synth-independent-chunk"
BGE_CHUNK_A = "synth-bge-chunk-a"
BGE_CHUNK_B = "synth-bge-chunk-b"
PRODUCT_TEXT = "zympoly product solventblend"
SIDECAR_TEXT = "helioxane sidecar solventblend"
INDEPENDENT_TEXT = "zympoly independent solventblend"
BGE_TEXT_A = "zympoly bge solventblend"
BGE_TEXT_B = "helioxane bge solventblend"
UNION_FLOOR = 0.25
BGE_FLOOR = 0.3698406656908355
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_DIM = 384
BGE_MODEL = research._BGE_MODEL_ID
BGE_DIM = research._BGE_DIM
BGE_QUERY_INSTRUCTION = research._BGE_QUERY_INSTRUCTION
BGE_PASSAGE_INSTRUCTION = research._BGE_PASSAGE_INSTRUCTION
BGE_REVISION = research._BGE_ENCODER_REVISION
LEAK_MARKERS = (
    "JSONDecodeError",
    "OSError",
    "PermissionError",
    "BadGzipFile",
    "UnicodeDecodeError",
    "OverflowError",
    "zlib.error",
    "Expecting value",
    "Not a gzipped file",
    "codec can't decode",
    "invalid start byte",
    "injected",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(paths: list[Path]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in paths:
        key = str(path)
        if path.is_file():
            out[key] = _sha256(path)
        elif path.exists():
            out[key] = "exists"
        else:
            out[key] = None
    return out


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_gzip_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _unit(dim: int, axis: int) -> list[float]:
    row = [0.0] * dim
    row[axis] = 1.0
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


def _make_index(
    knowledgebase: str,
    chunks: list[tuple[str, str, str]],
    *,
    dense: dict | None = None,
    abstention: object = _OMIT,
) -> dict:
    payload: dict = {
        "schema": INDEX_SCHEMA,
        "knowledgebase": knowledgebase,
        "documents": [_doc(doc_key) for _, _, doc_key in chunks],
        "chunks": [_chunk(chunk_id, text, doc_key) for chunk_id, text, doc_key in chunks],
        "dense": dense,
    }
    if abstention is not _OMIT:
        payload["abstention"] = abstention
    return payload


def _dense(
    chunk_ids: list[str],
    vectors: list[list[float]],
    *,
    model: str | None = MINILM_MODEL,
    dim: int | None = MINILM_DIM,
    **meta: object,
) -> dict:
    block: dict = {
        "chunk_ids": list(chunk_ids),
        "vectors": vectors,
    }
    if model is not None:
        block["model"] = model
    if dim is not None:
        block["dim"] = dim
    block.update(meta)
    return block


def _minilm_dense(chunk_ids: list[str], axes: list[int], **meta: object) -> dict:
    return _dense(
        chunk_ids,
        [_unit(MINILM_DIM, axis) for axis in axes],
        model=MINILM_MODEL,
        dim=MINILM_DIM,
        **meta,
    )


def _bge_dense(chunk_ids: list[str], axes: list[int], **meta: object) -> dict:
    meta.setdefault("query_instruction", BGE_QUERY_INSTRUCTION)
    meta.setdefault("passage_instruction", BGE_PASSAGE_INSTRUCTION)
    meta.setdefault("encoder_revision", BGE_REVISION)
    return _dense(
        chunk_ids,
        [_unit(BGE_DIM, axis) for axis in axes],
        model=BGE_MODEL,
        dim=BGE_DIM,
        **meta,
    )


def _abstention(floor: float) -> dict:
    return {
        "statistic": "query_idf_coverage",
        "percentile": 5,
        "floor": floor,
        "calibrated_at": "2020-01-01T00:00:00+00:00",
        "note": "synthetic-gate",
    }


def _public_search(query: str = QUERY, knowledgebase: str = PRODUCT_KB) -> dict:
    return parse_tool_result(
        research.search_literature_corpus(
            query,
            knowledgebase=knowledgebase,
            top_k=5,
            retrieval_mode="sparse",
        )
    )


def _chunk_ids(index: dict) -> list[str]:
    return [str(chunk.get("chunk_id") or "") for chunk in (index.get("chunks") or [])]


def _assert_no_leak(text: str, query: str, paths: list[Path]) -> None:
    assert query not in text
    lowered = text.casefold()
    for marker in LEAK_MARKERS:
        assert marker.casefold() not in lowered
    for path in paths:
        assert str(path) not in text
        assert path.name not in text


def _assert_public_failure(parsed: dict, query: str, paths: list[Path]) -> None:
    data = parsed["data"]
    assert data["success"] is False
    assert data["error_code"] == "corpus_read_failed"
    assert data["tool_name"] == "search_literature_corpus"
    assert "results" not in data
    _assert_no_leak(parsed["display"], query, paths)
    _assert_no_leak(str(data.get("error") or ""), query, paths)


def _assert_helper_refusal(knowledgebase: str, query: str, paths: list[Path]) -> None:
    with pytest.raises(research.LiteratureContractError) as caught:
        research._load_index(knowledgebase)
    _assert_no_leak(str(caught.value), query, paths)


def _assert_union_refusal(left: dict, right: dict) -> None:
    original_left = json.dumps(left, sort_keys=True)
    original_right = json.dumps(right, sort_keys=True)
    with pytest.raises(research.LiteratureContractError) as caught:
        research._union_product_and_sidecar(left, right)
    assert caught.value.code == "dense_union_incompatible"
    assert json.dumps(left, sort_keys=True) == original_left
    assert json.dumps(right, sort_keys=True) == original_right


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
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
    index_path = tmp_path / "canonical" / "product.json.gz"
    manifest_path = tmp_path / "canonical" / "manifest.json"
    monkeypatch.setattr(research, "_canonical_product_index_path", lambda: index_path)
    monkeypatch.setattr(research, "_product_manifest_path", lambda: manifest_path)
    yield {
        "index_path": index_path,
        "manifest_path": manifest_path,
        "tmp_path": tmp_path,
    }


@pytest.fixture
def forbid_ranking(monkeypatch):
    monkeypatch.setattr(
        research,
        "_search_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ranking reached on failure path")
        ),
    )


def _seed_product(paths: dict, *, floor: float | None = UNION_FLOOR, promoted: object = _OMIT, dense=None) -> None:
    payload = _make_index(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=dense,
    )
    _write_gzip_json(paths["index_path"], payload)
    manifest: dict = {"schema": "synthetic.product-manifest.v1", "knowledgebase": PRODUCT_KB}
    if floor is not None:
        manifest["abstention"] = _abstention(floor)
    if promoted is not _OMIT:
        manifest["promoted"] = promoted
    _write_json(paths["manifest_path"], manifest)


def _seed_sidecar(tmp_path: Path, *, dense=None, extra: dict | None = None) -> Path:
    sidecar_path = tmp_path / "sidecar.json.gz"
    payload = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=dense,
    )
    if extra:
        payload.update(extra)
    _write_gzip_json(sidecar_path, payload)
    return sidecar_path


def _seed_minilm_pair(paths: dict, *, product_dense=None, sidecar_dense=None) -> Path:
    sidecar_path = _seed_sidecar(paths["tmp_path"], dense=sidecar_dense)
    _seed_product(
        paths,
        promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        dense=product_dense,
    )
    return sidecar_path


def _bge_index_payload() -> dict:
    return _make_index(
        PRODUCT_KB,
        [
            (BGE_CHUNK_A, BGE_TEXT_A, "bge-a"),
            (BGE_CHUNK_B, BGE_TEXT_B, "bge-b"),
        ],
        dense=_bge_dense([BGE_CHUNK_A, BGE_CHUNK_B], [0, 1]),
    )


def _seed_bge(paths: dict, *, relative: bool = False, mutate_manifest=None, mutate_index=None) -> dict:
    root = paths["tmp_path"] / "bge"
    index_path = root / "indexes" / "bge.json.gz"
    payload = _bge_index_payload()
    if mutate_index is not None:
        payload = mutate_index(payload)
    _write_gzip_json(index_path, payload)
    manifest_path = root / "BGE10.manifest.json"
    declared_index = "indexes/bge.json.gz" if relative else str(index_path)
    manifest = {
        "knowledgebase": PRODUCT_KB,
        "index_path": declared_index,
        "gzip_sha256": _sha256(index_path),
        "dense": {
            "model": BGE_MODEL,
            "dim": BGE_DIM,
            "query_instruction": BGE_QUERY_INSTRUCTION,
            "passage_instruction": BGE_PASSAGE_INSTRUCTION,
            "encoder_revision": BGE_REVISION,
            "chunk_ids": [BGE_CHUNK_A, BGE_CHUNK_B],
        },
        "abstention": _abstention(BGE_FLOOR),
    }
    if mutate_manifest is not None:
        manifest = mutate_manifest(manifest, index_path)
    _write_json(manifest_path, manifest)
    return {
        "manifest_path": manifest_path,
        "index_path": index_path,
        "payload": payload,
    }


def _tracked(paths: dict, extra: list[Path] | None = None) -> list[Path]:
    items = [paths["index_path"], paths["manifest_path"]]
    if extra:
        items.extend(extra)
    return items


def _spy_paths(monkeypatch) -> list[Path]:
    seen: list[Path] = []

    def remember(path: Path) -> None:
        try:
            seen.append(path.resolve())
        except OSError:
            seen.append(path)

    real_open = research.gzip.open

    def fake_open(path, *args, **kwargs):
        remember(Path(path))
        return real_open(path, *args, **kwargs)

    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def fake_read_text(self, *args, **kwargs):
        remember(self)
        return real_read_text(self, *args, **kwargs)

    def fake_read_bytes(self, *args, **kwargs):
        remember(self)
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(research.gzip, "open", fake_open)
    monkeypatch.setattr(Path, "read_text", fake_read_text)
    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    return seen


def test_unset_and_explicit_minilm_select_product_sidecar(_isolate, monkeypatch):
    sidecar_path = _seed_minilm_pair(_isolate)
    tracked = _tracked(_isolate, [sidecar_path])
    before = _snapshot(tracked)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    unset_loaded = research._load_index(PRODUCT_KB)
    unset_search = _public_search()
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "minilm")
    explicit_loaded = research._load_index(PRODUCT_KB)
    explicit_search = _public_search()
    assert _chunk_ids(unset_loaded) == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    assert _chunk_ids(explicit_loaded) == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    assert unset_loaded["abstention"] == _abstention(UNION_FLOOR)
    assert explicit_loaded["abstention"] == _abstention(UNION_FLOOR)
    assert unset_search["data"]["success"] is True
    assert explicit_search["data"]["success"] is True
    assert unset_search["data"]["floor"] == UNION_FLOOR
    assert explicit_search["data"]["floor"] == UNION_FLOOR
    assert {row["chunk_id"] for row in unset_search["data"]["results"]} == {PRODUCT_CHUNK}
    assert {row["chunk_id"] for row in _public_search("helioxane")["data"]["results"]} == {SIDECAR_CHUNK}
    assert _snapshot(tracked) == before


def test_explicit_bge10_selects_manifest_only(_isolate, monkeypatch):
    sidecar_path = _seed_minilm_pair(_isolate)
    bge = _seed_bge(_isolate, relative=True)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    real_open = research.gzip.open

    def fake_open(path, *args, **kwargs):
        resolved = Path(path).resolve()
        if resolved in {_isolate["index_path"].resolve(), sidecar_path.resolve()}:
            raise AssertionError("legacy path read")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(research.gzip, "open", fake_open)
    tracked = _tracked(_isolate, [sidecar_path, bge["index_path"], bge["manifest_path"]])
    before = _snapshot(tracked)
    loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
    assert loaded["dense"]["model"] == BGE_MODEL
    assert loaded["dense"]["dim"] == BGE_DIM
    assert loaded["dense"]["encoder_revision"] == BGE_REVISION
    assert loaded["abstention"]["floor"] == BGE_FLOOR
    parsed = _public_search()
    assert parsed["data"]["success"] is True
    assert parsed["data"]["floor"] == round(BGE_FLOOR, 6)
    assert {row["chunk_id"] for row in parsed["data"]["results"]} == {BGE_CHUNK_A}
    assert _snapshot(tracked) == before


def test_unknown_profile_refuses_before_ranking(_isolate, forbid_ranking, monkeypatch):
    _seed_minilm_pair(_isolate)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "other")
    tracked = _tracked(_isolate)
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_helper_refusal(INDEPENDENT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


@pytest.mark.parametrize(
    "case",
    [
        "missing_env",
        "empty_env",
        "missing_file",
        "malformed",
        "unreadable",
        "wrong_digest",
        "wrong_knowledgebase",
        "missing_floor",
        "model_mismatch",
        "dimension_mismatch",
        "revision_mismatch",
        "instruction_mismatch",
        "index_model_mismatch",
        "index_dimension_mismatch",
        "index_revision_mismatch",
        "index_instruction_mismatch",
    ],
)
def test_bge10_manifest_and_recipe_faults(case, _isolate, forbid_ranking, monkeypatch):
    def mutate_manifest(manifest, index_path):
        if case == "wrong_digest":
            manifest["gzip_sha256"] = "0" * 64
            return manifest
        if case == "wrong_knowledgebase":
            manifest["knowledgebase"] = SIDECAR_KB
        elif case == "missing_floor":
            del manifest["abstention"]
        elif case == "model_mismatch":
            manifest["dense"]["model"] = MINILM_MODEL
        elif case == "dimension_mismatch":
            manifest["dense"]["dim"] = MINILM_DIM
        elif case == "revision_mismatch":
            manifest["dense"]["encoder_revision"] = "deadbeef" * 8
        elif case == "instruction_mismatch":
            manifest["dense"]["query_instruction"] = "other instruction: "
        return manifest

    def mutate_index(payload):
        if case == "index_model_mismatch":
            payload["dense"]["model"] = MINILM_MODEL
        elif case == "index_dimension_mismatch":
            payload["dense"]["dim"] = MINILM_DIM
        elif case == "index_revision_mismatch":
            payload["dense"]["encoder_revision"] = "c" * 40
        elif case == "index_instruction_mismatch":
            payload["dense"]["query_instruction"] = "other instruction: "
        return payload

    index_cases = {
        "index_model_mismatch",
        "index_dimension_mismatch",
        "index_revision_mismatch",
        "index_instruction_mismatch",
    }
    skip_manifest = {
        "missing_env",
        "empty_env",
        "missing_file",
        "malformed",
        "unreadable",
        *index_cases,
    }
    bge = _seed_bge(
        _isolate,
        relative=True,
        mutate_manifest=None if case in skip_manifest else mutate_manifest,
        mutate_index=mutate_index if case in index_cases else None,
    )
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    if case == "missing_env":
        monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
        tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
    elif case == "empty_env":
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", "  ")
        tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
    elif case == "missing_file":
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"].with_name("absent.json")))
        tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
    elif case == "malformed":
        bge["manifest_path"].write_text("{", encoding="utf-8")
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
    elif case == "unreadable":
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        real = Path.read_text

        def fake_read(self, *args, **kwargs):
            if self.resolve() == bge["manifest_path"].resolve():
                raise OSError("injected")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read)
        tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
    else:
        monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
        tracked = _tracked(_isolate, [bge["index_path"], bge["manifest_path"]])
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


def test_relative_index_path_ignores_cwd(_isolate, monkeypatch):
    bge = _seed_bge(_isolate, relative=True)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    cwd = _isolate["tmp_path"] / "other-cwd"
    decoy = cwd / "indexes" / "bge.json.gz"
    _write_gzip_json(
        decoy,
        _make_index(PRODUCT_KB, [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "decoy")]),
    )
    monkeypatch.chdir(cwd)
    tracked = [bge["index_path"], bge["manifest_path"], decoy]
    before = _snapshot(tracked)
    loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
    assert INDEPENDENT_CHUNK not in _chunk_ids(loaded)
    assert _snapshot(tracked) == before


def test_alias_save_protection_selected_and_original(_isolate, monkeypatch):
    bge = _seed_bge(_isolate)
    _seed_product(_isolate)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    alias_dir = _isolate["tmp_path"] / "aliases"
    alias_dir.mkdir()
    selected_alias = alias_dir / "selected.json.gz"
    original_alias = alias_dir / "original.json.gz"
    selected_alias.symlink_to(bge["index_path"].resolve())
    original_alias.symlink_to(_isolate["index_path"].resolve())
    payload = _make_index(INDEPENDENT_KB, [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "independent")])
    tracked = [bge["index_path"], _isolate["index_path"], selected_alias, original_alias]
    before = _snapshot(tracked)
    monkeypatch.setattr(research, "_index_path", lambda knowledgebase: selected_alias)
    with pytest.raises(research.LiteratureContractError) as selected:
        research._save_index(payload)
    assert selected.value.code == "protected_serving_index"
    monkeypatch.setattr(research, "_index_path", lambda knowledgebase: original_alias)
    with pytest.raises(research.LiteratureContractError) as original:
        research._save_index(payload)
    assert original.value.code == "protected_serving_index"
    assert _snapshot(tracked) == before


def test_noncanonical_independent_of_profile(_isolate, monkeypatch):
    home = _isolate["tmp_path"] / "research-home"
    independent = home / f"{INDEPENDENT_KB}.json.gz"
    _write_gzip_json(
        independent,
        _make_index(INDEPENDENT_KB, [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "independent")]),
    )
    bge = _seed_bge(_isolate)
    _isolate["manifest_path"].parent.mkdir(parents=True, exist_ok=True)
    _isolate["manifest_path"].write_text("{", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(home))
    tracked = _tracked(_isolate, [independent, bge["index_path"], bge["manifest_path"]])
    before = _snapshot(tracked)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    unset = research._load_index(INDEPENDENT_KB)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "minilm")
    minilm = research._load_index(INDEPENDENT_KB)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    bge10 = research._load_index(INDEPENDENT_KB)
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    bge10_without_manifest = research._load_index(INDEPENDENT_KB)
    for loaded in (unset, minilm, bge10, bge10_without_manifest):
        assert _chunk_ids(loaded) == [INDEPENDENT_CHUNK]
    parsed = _public_search(knowledgebase=INDEPENDENT_KB)
    assert parsed["data"]["success"] is True
    assert {row["chunk_id"] for row in parsed["data"]["results"]} == {INDEPENDENT_CHUNK}
    assert _snapshot(tracked) == before


def test_bge10_does_not_fallback_to_research_home(_isolate, monkeypatch):
    home = _isolate["tmp_path"] / "research-home"
    decoy = home / f"{PRODUCT_KB}.json.gz"
    _write_gzip_json(
        decoy,
        _make_index(PRODUCT_KB, [(INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "decoy")]),
    )
    bge = _seed_bge(_isolate)
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(home))
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
    assert INDEPENDENT_CHUNK not in _chunk_ids(loaded)


def test_minilm_ignores_bge_manifest(_isolate, monkeypatch):
    sidecar_path = _seed_minilm_pair(_isolate)
    bge = _seed_bge(_isolate)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "minilm")
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    real_open = research.gzip.open

    def fake_open(path, *args, **kwargs):
        if Path(path).resolve() == bge["index_path"].resolve():
            raise AssertionError("bge index read on minilm profile")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(research.gzip, "open", fake_open)
    loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(loaded) == [PRODUCT_CHUNK, SIDECAR_CHUNK]


def test_compatible_minilm_union_aligns_by_id(_isolate):
    product = _make_index(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=_minilm_dense([PRODUCT_CHUNK], [1], query_instruction="", encoder_revision=""),
        abstention=_abstention(UNION_FLOOR),
    )
    sidecar = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_minilm_dense([SIDECAR_CHUNK], [0], query_instruction="", encoder_revision=""),
    )
    sidecar["dense"]["chunk_ids"] = [SIDECAR_CHUNK]
    sidecar["dense"]["vectors"] = [_unit(MINILM_DIM, 0)]
    product["dense"]["chunk_ids"] = [PRODUCT_CHUNK]
    product["dense"]["vectors"] = [_unit(MINILM_DIM, 1)]
    left = json.dumps(product, sort_keys=True)
    right = json.dumps(sidecar, sort_keys=True)
    union = research._union_product_and_sidecar(product, sidecar)
    assert _chunk_ids(union) == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    assert union["dense"]["chunk_ids"] == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    assert union["dense"]["vectors"][0] == _unit(MINILM_DIM, 1)
    assert union["dense"]["vectors"][1] == _unit(MINILM_DIM, 0)
    assert union["dense"]["model"] == MINILM_MODEL
    assert union["dense"]["dim"] == MINILM_DIM
    assert "query_instruction" not in union["dense"]
    assert "encoder_revision" not in union["dense"]
    assert union["abstention"] == _abstention(UNION_FLOOR)
    assert json.dumps(product, sort_keys=True) == left
    assert json.dumps(sidecar, sort_keys=True) == right


def test_compatible_minilm_union_reordered_ids():
    product = _make_index(
        PRODUCT_KB,
        [
            (PRODUCT_CHUNK, PRODUCT_TEXT, "product"),
            ("synth-product-chunk-2", "second product solventblend", "product-2"),
        ],
        dense=_minilm_dense([PRODUCT_CHUNK, "synth-product-chunk-2"], [0, 1]),
    )
    product["dense"]["chunk_ids"] = ["synth-product-chunk-2", PRODUCT_CHUNK]
    product["dense"]["vectors"] = [_unit(MINILM_DIM, 1), _unit(MINILM_DIM, 0)]
    sidecar = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_minilm_dense([SIDECAR_CHUNK], [2]),
    )
    union = research._union_product_and_sidecar(product, sidecar)
    assert union["dense"]["chunk_ids"] == [PRODUCT_CHUNK, "synth-product-chunk-2", SIDECAR_CHUNK]
    assert union["dense"]["vectors"][0] == _unit(MINILM_DIM, 0)
    assert union["dense"]["vectors"][1] == _unit(MINILM_DIM, 1)
    assert union["dense"]["vectors"][2] == _unit(MINILM_DIM, 2)


def test_compatible_bge_union_preserves_recipe():
    product = _make_index(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=_bge_dense([PRODUCT_CHUNK], [0]),
        abstention=_abstention(BGE_FLOOR),
    )
    sidecar = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_bge_dense([SIDECAR_CHUNK], [3]),
    )
    sidecar["dense"]["chunk_ids"] = [SIDECAR_CHUNK]
    sidecar["dense"]["vectors"] = [_unit(BGE_DIM, 3)]
    union = research._union_product_and_sidecar(product, sidecar)
    assert _chunk_ids(union) == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    assert union["dense"]["model"] == BGE_MODEL
    assert union["dense"]["dim"] == BGE_DIM
    assert union["dense"]["query_instruction"] == BGE_QUERY_INSTRUCTION
    assert union["dense"]["passage_instruction"] == BGE_PASSAGE_INSTRUCTION
    assert union["dense"]["encoder_revision"] == BGE_REVISION
    assert union["dense"]["vectors"][0] == _unit(BGE_DIM, 0)
    assert union["dense"]["vectors"][1] == _unit(BGE_DIM, 3)
    assert union["abstention"]["floor"] == BGE_FLOOR


def test_both_sparse_only_union_remains_valid():
    product = _make_index(PRODUCT_KB, [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")])
    sidecar = _make_index(SIDECAR_KB, [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")])
    union = research._union_product_and_sidecar(product, sidecar)
    assert _chunk_ids(union) == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    assert union.get("dense") in (None, {})


def test_legacy_optional_metadata_omission_compatible():
    product = _make_index(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=_minilm_dense([PRODUCT_CHUNK], [0]),
    )
    sidecar = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_minilm_dense([SIDECAR_CHUNK], [1]),
    )
    product["dense"].pop("query_instruction", None)
    sidecar["dense"].pop("query_instruction", None)
    product["dense"].pop("encoder_revision", None)
    sidecar["dense"].pop("encoder_revision", None)
    union = research._union_product_and_sidecar(product, sidecar)
    assert union["dense"]["model"] == MINILM_MODEL
    assert "encoder_revision" not in union["dense"]
    assert "query_instruction" not in union["dense"]


@pytest.mark.parametrize(
    "case",
    [
        "mixed_dim",
        "different_model_same_dim",
        "different_instruction",
        "conflicting_revision",
        "missing_one_dense",
        "duplicate_ids",
        "missing_id",
        "extra_id",
        "ragged",
        "nan",
        "inf",
        "bool_component",
    ],
)
def test_incompatible_union_refuses_and_leaves_inputs(case):
    product = _make_index(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=_minilm_dense([PRODUCT_CHUNK], [0], query_instruction="q", encoder_revision="aaa"),
    )
    sidecar = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_minilm_dense([SIDECAR_CHUNK], [1], query_instruction="q", encoder_revision="aaa"),
    )
    if case == "mixed_dim":
        sidecar["dense"] = _dense(
            [SIDECAR_CHUNK],
            [_unit(BGE_DIM, 0)],
            model=MINILM_MODEL,
            dim=BGE_DIM,
        )
    elif case == "different_model_same_dim":
        sidecar["dense"]["model"] = "sentence-transformers/other-384"
    elif case == "different_instruction":
        sidecar["dense"]["query_instruction"] = "other: "
    elif case == "conflicting_revision":
        sidecar["dense"]["encoder_revision"] = "bbb"
    elif case == "missing_one_dense":
        sidecar["dense"] = None
    elif case == "duplicate_ids":
        sidecar["dense"]["chunk_ids"] = [SIDECAR_CHUNK, SIDECAR_CHUNK]
        sidecar["dense"]["vectors"] = [_unit(MINILM_DIM, 1), _unit(MINILM_DIM, 2)]
        sidecar["chunks"].append(_chunk(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar"))
    elif case == "missing_id":
        sidecar["dense"]["chunk_ids"] = ["other-chunk"]
        sidecar["dense"]["vectors"] = [_unit(MINILM_DIM, 1)]
    elif case == "extra_id":
        sidecar["dense"]["chunk_ids"] = [SIDECAR_CHUNK, "extra-chunk"]
        sidecar["dense"]["vectors"] = [_unit(MINILM_DIM, 1), _unit(MINILM_DIM, 2)]
    elif case == "ragged":
        sidecar["dense"]["vectors"] = [_unit(MINILM_DIM, 1)[:-1]]
    elif case == "nan":
        sidecar["dense"]["vectors"][0][0] = math.nan
    elif case == "inf":
        sidecar["dense"]["vectors"][0][0] = math.inf
    else:
        sidecar["dense"]["vectors"][0][0] = True
    _assert_union_refusal(product, sidecar)


def test_mixed_dimension_union_is_base_counterfactual(_isolate, monkeypatch, forbid_ranking):
    sidecar_path = _seed_minilm_pair(
        _isolate,
        product_dense=_minilm_dense([PRODUCT_CHUNK], [0]),
        sidecar_dense=_dense(
            [SIDECAR_CHUNK],
            [_unit(BGE_DIM, 0)],
            model=BGE_MODEL,
            dim=BGE_DIM,
            query_instruction=BGE_QUERY_INSTRUCTION,
            passage_instruction=BGE_PASSAGE_INSTRUCTION,
            encoder_revision=BGE_REVISION,
        ),
    )
    tracked = _tracked(_isolate, [sidecar_path])
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


def test_bge_invalid_norm_and_zero_vector_refuse():
    product = _make_index(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=_bge_dense([PRODUCT_CHUNK], [0]),
    )
    sidecar_norm = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_bge_dense([SIDECAR_CHUNK], [1]),
    )
    sidecar_norm["dense"]["vectors"][0][0] = 0.5
    _assert_union_refusal(product, sidecar_norm)
    sidecar_zero = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_bge_dense([SIDECAR_CHUNK], [1]),
    )
    sidecar_zero["dense"]["vectors"][0] = [0.0] * BGE_DIM
    _assert_union_refusal(product, sidecar_zero)


def test_bge_known_unit_vectors_pass():
    product = _make_index(
        PRODUCT_KB,
        [(PRODUCT_CHUNK, PRODUCT_TEXT, "product")],
        dense=_bge_dense([PRODUCT_CHUNK], [0]),
    )
    sidecar = _make_index(
        SIDECAR_KB,
        [(SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")],
        dense=_bge_dense([SIDECAR_CHUNK], [1]),
    )
    union = research._union_product_and_sidecar(product, sidecar)
    assert union["dense"]["vectors"][0] == _unit(BGE_DIM, 0)
    assert union["dense"]["vectors"][1] == _unit(BGE_DIM, 1)


def test_profile_switch_has_no_stale_cache_or_writes(_isolate, monkeypatch):
    sidecar_path = _seed_minilm_pair(
        _isolate,
        product_dense=_minilm_dense([PRODUCT_CHUNK], [0]),
        sidecar_dense=_minilm_dense([SIDECAR_CHUNK], [1]),
    )
    bge = _seed_bge(_isolate, relative=True)
    tracked = _tracked(_isolate, [sidecar_path, bge["index_path"], bge["manifest_path"]])
    before = _snapshot(tracked)
    seen = _spy_paths(monkeypatch)
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    bge_loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(bge_loaded) == [BGE_CHUNK_A, BGE_CHUNK_B]
    assert bge_loaded["dense"]["model"] == BGE_MODEL
    assert bge_loaded["dense"]["encoder_revision"] == BGE_REVISION
    bge_seen = set(seen)
    assert bge["index_path"].resolve() in bge_seen
    assert _isolate["index_path"].resolve() not in bge_seen
    assert sidecar_path.resolve() not in bge_seen
    seen.clear()
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "minilm")
    minilm_loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(minilm_loaded) == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    assert minilm_loaded["dense"]["model"] == MINILM_MODEL
    assert minilm_loaded["dense"].get("encoder_revision") is None
    assert minilm_loaded["dense"]["dim"] == MINILM_DIM
    minilm_seen = set(seen)
    assert _isolate["index_path"].resolve() in minilm_seen
    assert bge["index_path"].resolve() not in minilm_seen
    seen.clear()
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    again = research._load_index(PRODUCT_KB)
    assert _chunk_ids(again) == [BGE_CHUNK_A, BGE_CHUNK_B]
    assert again["dense"]["model"] == BGE_MODEL
    assert again["dense"]["encoder_revision"] == BGE_REVISION
    assert again["dense"]["dim"] == BGE_DIM
    assert _snapshot(tracked) == before
    parsed = _public_search()
    assert parsed["data"]["success"] is True
    assert parsed["data"]["floor"] == round(BGE_FLOOR, 6)


def test_unsupported_routing_counterfactual_without_profile_env(_isolate, monkeypatch):
    bge = _seed_bge(_isolate)
    _seed_minilm_pair(_isolate)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    monkeypatch.setenv("DISSOLVE_BGE10_MANIFEST", str(bge["manifest_path"]))
    loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(loaded) == [PRODUCT_CHUNK, SIDECAR_CHUNK]
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    routed = research._load_index(PRODUCT_KB)
    assert _chunk_ids(routed) == [BGE_CHUNK_A, BGE_CHUNK_B]
