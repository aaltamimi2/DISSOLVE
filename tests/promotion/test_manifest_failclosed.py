"""Synthetic fail-closed tests for canonical product manifest and sidecar load."""

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
PRODUCT_TEXT = "zympoly product solventblend"
SIDECAR_TEXT = "helioxane sidecar solventblend"
INDEPENDENT_TEXT = "zympoly independent solventblend"
UNION_FLOOR = 0.25
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


def _make_index(knowledgebase: str, chunk_id: str, text: str, doc_key: str) -> dict:
    return {
        "schema": INDEX_SCHEMA,
        "knowledgebase": knowledgebase,
        "documents": [{
            "document_id": f"D-{doc_key}",
            "sha256": hashlib.sha256(doc_key.encode("utf-8")).hexdigest(),
            "title": f"Synthetic {doc_key}",
            "source": "synthetic-local",
        }],
        "chunks": [{
            "chunk_id": chunk_id,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "document_id": f"D-{doc_key}",
            "title": f"Synthetic {doc_key}",
            "source": "synthetic-local",
            "text": text,
            "body": text,
        }],
        "dense": None,
    }


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


def _chunk_ids(index: dict) -> set[str]:
    return {str(chunk.get("chunk_id") or "") for chunk in (index.get("chunks") or [])}


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
    message = str(caught.value)
    _assert_no_leak(message, query, paths)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
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


def _seed_product(paths: dict, *, floor: float | None = UNION_FLOOR, promoted: object = _OMIT) -> None:
    _write_gzip_json(
        paths["index_path"],
        _make_index(PRODUCT_KB, PRODUCT_CHUNK, PRODUCT_TEXT, "product"),
    )
    manifest: dict = {"schema": "synthetic.product-manifest.v1", "knowledgebase": PRODUCT_KB}
    if floor is not None:
        manifest["abstention"] = _abstention(floor)
    if promoted is not _OMIT:
        manifest["promoted"] = promoted
    _write_json(paths["manifest_path"], manifest)


def _seed_sidecar(tmp_path: Path, *, knowledgebase: str = SIDECAR_KB, extra: dict | None = None) -> Path:
    sidecar_path = tmp_path / "sidecar.json.gz"
    payload = _make_index(knowledgebase, SIDECAR_CHUNK, SIDECAR_TEXT, "sidecar")
    if extra:
        payload.update(extra)
    _write_gzip_json(sidecar_path, payload)
    return sidecar_path


def _tracked(paths: dict, extra: list[Path] | None = None) -> list[Path]:
    items = [paths["index_path"], paths["manifest_path"]]
    if extra:
        items.extend(extra)
    return items


def test_valid_product_sidecar_union(_isolate):
    sidecar_path = _seed_sidecar(_isolate["tmp_path"])
    _seed_product(
        _isolate,
        promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
    )
    tracked = _tracked(_isolate, [sidecar_path])
    before = _snapshot(tracked)
    loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(loaded) == {PRODUCT_CHUNK, SIDECAR_CHUNK}
    assert loaded["abstention"] == _abstention(UNION_FLOOR)
    parsed = _public_search()
    data = parsed["data"]
    assert data["success"] is True
    assert data["floor"] == UNION_FLOOR
    returned = {row["chunk_id"] for row in data["results"]}
    assert PRODUCT_CHUNK in returned
    sidecar_hit = _public_search("helioxane")
    assert sidecar_hit["data"]["success"] is True
    assert SIDECAR_CHUNK in {row["chunk_id"] for row in sidecar_hit["data"]["results"]}
    assert _snapshot(tracked) == before


def test_valid_product_only_manifest(_isolate):
    _seed_product(_isolate, floor=None)
    sidecar_path = _seed_sidecar(_isolate["tmp_path"])
    tracked = _tracked(_isolate, [sidecar_path])
    before = _snapshot(tracked)
    loaded = research._load_index(PRODUCT_KB)
    assert _chunk_ids(loaded) == {PRODUCT_CHUNK}
    assert "abstention" not in loaded
    assert "promoted" not in json.loads(_isolate["manifest_path"].read_text(encoding="utf-8"))
    parsed = _public_search()
    assert parsed["data"]["success"] is True
    assert parsed["data"]["floor"] is None
    assert {row["chunk_id"] for row in parsed["data"]["results"]} == {PRODUCT_CHUNK}
    assert _snapshot(tracked) == before


def test_valid_zero_floor(_isolate):
    _seed_product(_isolate, floor=0.0)
    tracked = _tracked(_isolate)
    before = _snapshot(tracked)
    loaded = research._load_index(PRODUCT_KB)
    assert loaded["abstention"]["floor"] == 0.0
    parsed = _public_search()
    data = parsed["data"]
    assert data["success"] is True
    assert data["floor"] == 0.0
    assert data["result_count"] >= 1
    assert _snapshot(tracked) == before


def test_noncanonical_independent_index(_isolate, monkeypatch):
    home = _isolate["tmp_path"] / "research-home"
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(home))
    independent = home / f"{INDEPENDENT_KB}.json.gz"
    _write_gzip_json(
        independent,
        _make_index(INDEPENDENT_KB, INDEPENDENT_CHUNK, INDEPENDENT_TEXT, "independent"),
    )
    _isolate["manifest_path"].parent.mkdir(parents=True, exist_ok=True)
    _isolate["manifest_path"].write_text("{", encoding="utf-8")
    tracked = _tracked(_isolate, [independent])
    before = _snapshot(tracked)
    loaded = research._load_index(INDEPENDENT_KB)
    assert _chunk_ids(loaded) == {INDEPENDENT_CHUNK}
    parsed = _public_search(knowledgebase=INDEPENDENT_KB)
    assert parsed["data"]["success"] is True
    assert {row["chunk_id"] for row in parsed["data"]["results"]} == {INDEPENDENT_CHUNK}
    assert _snapshot(tracked) == before


@pytest.mark.parametrize(
    "case",
    ["missing", "unreadable", "malformed", "non_object"],
    ids=["missing", "unreadable", "malformed", "non_object"],
)
def test_canonical_manifest_faults(case, _isolate, monkeypatch, forbid_ranking):
    _write_gzip_json(
        _isolate["index_path"],
        _make_index(PRODUCT_KB, PRODUCT_CHUNK, PRODUCT_TEXT, "product"),
    )
    manifest_path = _isolate["manifest_path"]
    if case == "missing":
        if manifest_path.exists():
            manifest_path.unlink()
    elif case == "unreadable":
        _write_json(manifest_path, {"abstention": _abstention(UNION_FLOOR)})
        real = Path.read_text

        def fake_read(self, *args, **kwargs):
            if self.resolve() == manifest_path.resolve():
                raise OSError("injected")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read)
    elif case == "malformed":
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{", encoding="utf-8")
    else:
        _write_json(manifest_path, ["not-an-object"])
    tracked = _tracked(_isolate)
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


@pytest.mark.parametrize(
    "promoted",
    [None, ["not-mapping"], {}, {"index_path": ""}, {"index_path": "   "}],
    ids=["null", "list", "empty_mapping", "empty_path", "whitespace_path"],
)
def test_invalid_promoted_declaration(promoted, _isolate, forbid_ranking):
    _seed_product(_isolate, promoted=promoted)
    tracked = _tracked(_isolate)
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "directory",
        "unreadable",
        "bad_gzip",
        "bad_json",
        "non_object",
        "wrong_schema",
        "mismatched_knowledgebase",
    ],
)
def test_advertised_sidecar_faults(case, _isolate, monkeypatch, forbid_ranking):
    tmp_path = _isolate["tmp_path"]
    sidecar_path = tmp_path / "sidecar.json.gz"
    extra: list[Path] = [sidecar_path]
    if case == "missing":
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
    elif case == "directory":
        sidecar_path.mkdir(parents=True)
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
    elif case == "unreadable":
        sidecar_path = _seed_sidecar(tmp_path)
        extra = [sidecar_path]
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
        real = research.gzip.open

        def fake_open(path, *args, **kwargs):
            if Path(path).resolve() == sidecar_path.resolve():
                raise OSError("injected")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(research.gzip, "open", fake_open)
    elif case == "bad_gzip":
        sidecar_path.write_bytes(b"not-gzip-bytes")
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
    elif case == "bad_json":
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(sidecar_path, "wt", encoding="utf-8") as handle:
            handle.write("{")
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
    elif case == "non_object":
        _write_gzip_json(sidecar_path, ["not-an-object"])
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
    elif case == "wrong_schema":
        sidecar_path = _seed_sidecar(tmp_path, extra={"schema": "dissolve.literature-index.v0"})
        extra = [sidecar_path]
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
    else:
        sidecar_path = _seed_sidecar(tmp_path, knowledgebase=PRODUCT_KB)
        extra = [sidecar_path]
        _seed_product(
            _isolate,
            promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
        )
    tracked = _tracked(_isolate, extra)
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


@pytest.mark.parametrize(
    "block",
    [
        ["not-mapping"],
        {"statistic": "query_idf_coverage"},
        {"floor": None},
        {"floor": True},
        {"floor": False},
        {"floor": math.nan},
        {"floor": math.inf},
        {"floor": -math.inf},
    ],
    ids=[
        "list",
        "missing_floor",
        "null_floor",
        "bool_true",
        "bool_false",
        "nan",
        "inf",
        "ninf",
    ],
)
def test_malformed_present_abstention(block, _isolate, forbid_ranking):
    _write_gzip_json(
        _isolate["index_path"],
        _make_index(PRODUCT_KB, PRODUCT_CHUNK, PRODUCT_TEXT, "product"),
    )
    _write_json(_isolate["manifest_path"], {"knowledgebase": PRODUCT_KB, "abstention": block})
    tracked = _tracked(_isolate)
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


def test_canonical_manifest_undecodable_bytes(_isolate, forbid_ranking):
    _write_gzip_json(
        _isolate["index_path"],
        _make_index(PRODUCT_KB, PRODUCT_CHUNK, PRODUCT_TEXT, "product"),
    )
    manifest_path = _isolate["manifest_path"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(b"\xff")
    tracked = _tracked(_isolate)
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


def test_abstention_floor_overflow(_isolate, forbid_ranking):
    _write_gzip_json(
        _isolate["index_path"],
        _make_index(PRODUCT_KB, PRODUCT_CHUNK, PRODUCT_TEXT, "product"),
    )
    _write_json(
        _isolate["manifest_path"],
        {"knowledgebase": PRODUCT_KB, "abstention": {"floor": 10 ** 400}},
    )
    tracked = _tracked(_isolate)
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before


def test_advertised_sidecar_truncated_gzip(_isolate, forbid_ranking):
    sidecar_path = _isolate["tmp_path"] / "sidecar.json.gz"
    sidecar_path.write_bytes(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x07" + b"\x00" * 16)
    _seed_product(
        _isolate,
        promoted={"knowledgebase": SIDECAR_KB, "index_path": str(sidecar_path)},
    )
    tracked = _tracked(_isolate, [sidecar_path])
    before = _snapshot(tracked)
    _assert_helper_refusal(PRODUCT_KB, QUERY, tracked)
    _assert_public_failure(_public_search(), QUERY, tracked)
    assert _snapshot(tracked) == before
