"""Synthetic BGE pair-reranker and RRF60 tests. No real model or cache."""

from __future__ import annotations

import math
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from dissolve import research, rerank
from dissolve.contracts import parse_tool_result

PRODUCT_KB = "t5-indexed-unsealed"
INDEPENDENT_KB = "synth-user-lib"
QUERY = "zympoly"
UNCHANGED_FLOOR = 0.3698406656908355
MATCH_TEXT = "zympoly solventblend"
NOMATCH_TEXT = "solventblend only"
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BGE_QUERY_PREFIX = research._BGE_QUERY_INSTRUCTION
PINNED_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"


def _rrf(rank_before: int, rank_pair: int) -> float:
    return 1.0 / (60 + rank_before) + 1.0 / (60 + rank_pair)


def _chunk(
    chunk_id: str,
    *,
    title: str = "",
    section: str = "",
    text: str = MATCH_TEXT,
    body: str | None = None,
    caption: str = "",
    body_plus_rebound: str = "",
) -> dict:
    payload = {
        "chunk_id": chunk_id,
        "title": title,
        "section": section,
        "text": text,
        "body": MATCH_TEXT if body is None else body,
        "caption": caption,
        "source": "synthetic-local",
    }
    if body_plus_rebound:
        payload["body_plus_rebound"] = body_plus_rebound
    return payload


def _row(chunk_id: str, **chunk_fields) -> tuple:
    return (0.0, 0.0, 0.0, 0.0, _chunk(chunk_id, **chunk_fields), 0.0)


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


def _ranked_ids(ranked: list[tuple]) -> list[str]:
    return [item[4]["chunk_id"] for item in ranked]


class FakeScalar:
    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class FakeTensor:
    def __init__(self, rows, dtype):
        self._rows = rows
        self.shape = (len(rows), len(rows[0]) if rows else 0)
        self.dtype = dtype
        self.ndim = 2

    def __getitem__(self, idx):
        if not isinstance(idx, tuple) or len(idx) != 2:
            raise TypeError("expected pair index")
        return FakeScalar(self._rows[idx[0]][idx[1]])

    def to(self, device):
        return self


class FakeTokenizer:
    encode_calls: list

    def __init__(self, owner):
        self.owner = owner

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        cls.owner_ref.load_calls.append(("tokenizer", args, kwargs))
        return cls(cls.owner_ref)

    def __call__(self, queries, passages, **kwargs):
        type(self).encode_calls.append((list(queries), list(passages), dict(kwargs)))
        return {"input_ids": FakeTensor([[1] for _ in passages], self.owner.torch.float32)}


class FakeModel:
    def __init__(self, owner, *, model_type="xlm-roberta", num_labels=1):
        self.owner = owner
        self.config = SimpleNamespace(model_type=model_type, num_labels=num_labels)
        self.calls = []

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        cls.owner_ref.load_calls.append(("model", args, kwargs))
        spec = cls.owner_ref.model_spec
        return cls(
            cls.owner_ref,
            model_type=spec.get("model_type", "xlm-roberta"),
            num_labels=spec.get("num_labels", 1),
        )

    def to(self, device):
        self.calls.append(("to", device))
        return self

    def float(self):
        self.calls.append(("float",))
        return self

    def eval(self):
        self.calls.append(("eval",))
        return self

    def __call__(self, **kwargs):
        self.calls.append(("forward", kwargs))
        batch = 1
        if "input_ids" in kwargs:
            batch = kwargs["input_ids"].shape[0]
        rows = self.owner.next_logits(batch)
        return SimpleNamespace(logits=FakeTensor(rows, self.owner.torch.float32))


class FakeTorch:
    def __init__(self):
        self.float32 = object()
        self.thread_calls: list[int] = []
        self.interop_calls: list[int] = []
        self._interop = None
        self.inference_calls = 0

    def set_num_threads(self, count):
        self.thread_calls.append(int(count))

    def set_num_interop_threads(self, count):
        if self._interop is not None:
            raise RuntimeError("illegal interop reset")
        self._interop = int(count)
        self.interop_calls.append(int(count))

    def get_num_interop_threads(self):
        return 1 if self._interop is None else self._interop

    def inference_mode(self):
        self.inference_calls += 1
        return nullcontext()


class PairBackend:
    def __init__(self, logits_by_passage=None, logit_batches=None, model_spec=None):
        self.torch = FakeTorch()
        self.load_calls: list[tuple] = []
        self.logits_by_passage = logits_by_passage or {}
        self.logit_batches = list(logit_batches or [])
        self.model_spec = model_spec or {}
        self._batch_i = 0
        FakeTokenizer.owner_ref = self
        FakeTokenizer.encode_calls = []
        FakeModel.owner_ref = self
        self.transformers = SimpleNamespace(
            AutoTokenizer=FakeTokenizer,
            AutoModelForSequenceClassification=FakeModel,
        )

    def next_logits(self, batch_count: int):
        if self.logit_batches:
            rows = self.logit_batches[self._batch_i]
            self._batch_i += 1
            return rows
        encoded = FakeTokenizer.encode_calls[-1]
        passages = encoded[1]
        rows = []
        for passage in passages:
            rows.append([float(self.logits_by_passage.get(passage, 0.0))])
        if len(rows) != batch_count:
            rows = [[0.0] for _ in range(batch_count)]
        return rows


def _reset_pair_state():
    rerank._PAIR_BACKEND = None
    rerank._PAIR_INTEROP_READY = False


def _touch_pair_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name in rerank.PAIR_RERANKER_FILE_SHA256:
        (path / name).write_bytes(b"synthetic-pair-artifact")
    return path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    _reset_pair_state()
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    monkeypatch.delenv("DISSOLVE_CORPUS_PROFILE", raising=False)
    monkeypatch.delenv("DISSOLVE_BGE10_MANIFEST", raising=False)
    monkeypatch.delenv("DISSOLVE_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("DISSOLVE_BGE_RERANKER_DIR", raising=False)
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
    _reset_pair_state()


def _pass_hashes(monkeypatch):
    monkeypatch.setattr(
        rerank,
        "_hash_pair_file",
        lambda path: rerank.PAIR_RERANKER_FILE_SHA256[Path(path).name],
    )


def _install_backend(monkeypatch, tmp_path, backend: PairBackend) -> PairBackend:
    pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
    monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
    _pass_hashes(monkeypatch)
    original = rerank.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            return backend.torch
        if name == "transformers":
            return backend.transformers
        return original(name, *args, **kwargs)

    monkeypatch.setattr(rerank.importlib, "import_module", fake_import)
    return backend


def _install_scores(monkeypatch, dense, sparse):
    dense_calls: list[str] = []
    sparse_calls: list[str] = []

    def fake_dense(index, chunks, query):
        dense_calls.append(query)
        return [float(value) for value in dense]

    def fake_sparse(query, rows):
        sparse_calls.append(query)
        return [float(value) for value in sparse]

    monkeypatch.setattr(research, "_dense_query_scores", fake_dense)
    monkeypatch.setattr(research, "_query_sparse_raw", fake_sparse)
    return dense_calls, sparse_calls


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


def test_rrf_empty_and_known_orders():
    assert rerank.fuse_rrf60([], []) == []
    tied = rerank.fuse_rrf60([_row("a"), _row("b")], [_row("b"), _row("a")])
    assert _ranked_ids(tied) == ["a", "b"]
    assert tied[0][0] == _rrf(1, 2)
    assert tied[1][0] == _rrf(2, 1)
    assert tied[0][0] == tied[1][0]
    flipped = rerank.fuse_rrf60(
        [_row("a"), _row("b"), _row("c")],
        [_row("c"), _row("b"), _row("a")],
    )
    assert _ranked_ids(flipped) == ["a", "c", "b"]
    assert flipped[0][0] == _rrf(1, 3)
    assert flipped[1][0] == _rrf(3, 1)
    assert flipped[2][0] == _rrf(2, 2)
    assert flipped[0][0] == flipped[1][0]
    assert flipped[0][0] > flipped[2][0]


def test_rrf_membership_mismatch_and_duplicates_fail():
    with pytest.raises(ValueError, match="rrf_membership"):
        rerank.fuse_rrf60([_row("a"), _row("b")], [_row("a")])
    with pytest.raises(ValueError, match="rrf_membership"):
        rerank.fuse_rrf60([_row("a"), _row("b")], [_row("a"), _row("c")])
    with pytest.raises(ValueError, match="rrf_duplicate"):
        rerank.fuse_rrf60([_row("a"), _row("a")], [_row("a"), _row("b")])
    with pytest.raises(ValueError, match="rrf_duplicate"):
        rerank.fuse_rrf60([_row("a"), _row("b")], [_row("b"), _row("b")])


def test_pair_equal_logits_keep_original_order(monkeypatch, tmp_path):
    backend = PairBackend(logits_by_passage={"zb": 0.5, "aa": 0.5})
    _install_backend(monkeypatch, tmp_path, backend)
    ranked = [_row("z", body="zb"), _row("a", body="aa")]
    out = rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert _ranked_ids(out) == ["z", "a"]


def test_pair_tiny_logit_delta_beats_rounding(monkeypatch, tmp_path):
    backend = PairBackend(logits_by_passage={"left": 1.0, "right": 1.0 + 1e-8})
    _install_backend(monkeypatch, tmp_path, backend)
    ranked = [_row("m", body="left"), _row("a", body="right")]
    out = rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert _ranked_ids(out) == ["a", "m"]
    assert out[0][0] - out[1][0] == pytest.approx(1e-8)


@pytest.mark.parametrize(
    "rows",
    [
        [[math.nan], [0.0]],
        [[math.inf], [0.0]],
        [[-math.inf], [0.0]],
    ],
)
def test_pair_nonfinite_logits_fail_closed(monkeypatch, tmp_path, rows):
    backend = PairBackend(logit_batches=[rows])
    _install_backend(monkeypatch, tmp_path, backend)
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, [_row("a"), _row("b")], rerank.PAIR_RERANK_MODE)


def test_pair_wrong_logit_shapes_fail_closed(monkeypatch, tmp_path):
    ranked = [_row("a"), _row("b")]

    class WideModel(FakeModel):
        def __call__(self, **kwargs):
            return SimpleNamespace(
                logits=FakeTensor([[0.0, 1.0], [0.0, 1.0]], self.owner.torch.float32)
            )

    backend = _install_backend(monkeypatch, tmp_path, PairBackend())
    backend.transformers.AutoModelForSequenceClassification = type(
        "Wide",
        (WideModel,),
        {"owner_ref": backend},
    )
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)

    _reset_pair_state()

    class FlatTensor:
        shape = (2,)
        dtype = backend.torch.float32

        def __getitem__(self, idx):
            raise AssertionError("1d logits must fail before scoring")

    class FlatModel(FakeModel):
        def __call__(self, **kwargs):
            return SimpleNamespace(logits=FlatTensor())

    backend.transformers.AutoModelForSequenceClassification = type(
        "Flat",
        (FlatModel,),
        {"owner_ref": backend},
    )
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)

    _reset_pair_state()

    class CubeTensor:
        shape = (2, 1, 1)
        dtype = backend.torch.float32

    class CubeModel(FakeModel):
        def __call__(self, **kwargs):
            return SimpleNamespace(logits=CubeTensor())

    backend.transformers.AutoModelForSequenceClassification = type(
        "Cube",
        (CubeModel,),
        {"owner_ref": backend},
    )
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)


def test_window_twenty_batches_and_rest_excluded(monkeypatch, tmp_path):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    chunks = [_chunk(f"c{i:02d}", body=f"p{i:02d}") for i in range(25)]
    index = _index(chunks)
    dense = [float(i) for i in range(25)]
    sparse = [1.0] * 25
    _install_scores(monkeypatch, dense, sparse)
    logits = {f"p{i:02d}": float(i) for i in range(25)}
    backend = PairBackend(logits_by_passage=logits)
    _install_backend(monkeypatch, tmp_path, backend)
    rrf_calls: list[tuple[list[str], list[str]]] = []
    real_fuse = rerank.fuse_rrf60

    def spy_fuse(before, pair):
        rrf_calls.append((_ranked_ids(list(before)), _ranked_ids(list(pair))))
        return real_fuse(before, pair)

    monkeypatch.setattr(rerank, "fuse_rrf60", spy_fuse)
    rows = research._search_index(
        index, QUERY, 5, "hybrid", rerank_mode=rerank.PAIR_RERANK_MODE,
    )
    scored_passages = [passage for _q, passages, _kw in FakeTokenizer.encode_calls for passage in passages]
    assert [len(call[1]) for call in FakeTokenizer.encode_calls] == [8, 8, 4]
    assert scored_passages == [f"p{i:02d}" for i in range(24, 4, -1)]
    assert "p04" not in scored_passages
    assert rrf_calls and len(rrf_calls[0][0]) == 20
    assert rrf_calls[0][0] == [f"c{i:02d}" for i in range(24, 4, -1)]
    assert set(rrf_calls[0][0]) == set(rrf_calls[0][1])
    assert "c04" not in rrf_calls[0][0]
    assert len(rows) == 5
    assert "c04" not in _ids(rows)
    assert "c00" not in _ids(rows)


def test_loader_records_and_single_init(monkeypatch, tmp_path):
    pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
    backend = PairBackend(logits_by_passage={MATCH_TEXT: 1.0})
    _install_backend(monkeypatch, tmp_path, backend)
    hash_calls: list[str] = []

    def counted_hash(path):
        hash_calls.append(Path(path).name)
        return rerank.PAIR_RERANKER_FILE_SHA256[Path(path).name]

    monkeypatch.setattr(rerank, "_hash_pair_file", counted_hash)
    ranked = [_row("c0"), _row("c1")]
    rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    tokenizer_loads = [call for call in backend.load_calls if call[0] == "tokenizer"]
    model_loads = [call for call in backend.load_calls if call[0] == "model"]
    assert len(tokenizer_loads) == 1
    assert len(model_loads) == 1
    for _kind, args, kwargs in backend.load_calls:
        assert args[0] == str(pair_dir.resolve())
        assert kwargs["revision"] == PINNED_REVISION
        assert kwargs["local_files_only"] is True
    assert tokenizer_loads[0][2]["use_fast"] is True
    assert tokenizer_loads[0][2]["truncation_side"] == "right"
    assert model_loads[0][2]["torch_dtype"] is backend.torch.float32
    assert backend.torch.thread_calls == [8]
    assert backend.torch.interop_calls == [1]
    encode_kwargs = FakeTokenizer.encode_calls[0][2]
    assert encode_kwargs["padding"] is True
    assert encode_kwargs["truncation"] == "longest_first"
    assert encode_kwargs["max_length"] == 512
    assert encode_kwargs["return_tensors"] == "pt"
    assert hash_calls == list(rerank.PAIR_RERANKER_FILE_SHA256)
    model = rerank._PAIR_BACKEND[1]
    assert model.calls[0] == ("to", "cpu")
    assert ("float",) in model.calls
    assert ("eval",) in model.calls


def test_failed_init_retry_rehashes_changed_digest(monkeypatch, tmp_path):
    pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
    monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
    hash_calls: list[str] = []
    allow = {"ok": True}
    import_calls: list[str] = []

    def counted_hash(path):
        hash_calls.append(Path(path).name)
        if not allow["ok"]:
            return "0" * 64
        return rerank.PAIR_RERANKER_FILE_SHA256[Path(path).name]

    def fail_import(name, *args, **kwargs):
        import_calls.append(name)
        raise ImportError("synthetic import failure")

    monkeypatch.setattr(rerank, "_hash_pair_file", counted_hash)
    monkeypatch.setattr(rerank.importlib, "import_module", fail_import)
    ranked = [_row("a")]
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert rerank._PAIR_BACKEND is None
    assert hash_calls == list(rerank.PAIR_RERANKER_FILE_SHA256)
    assert import_calls == ["torch"]
    allow["ok"] = False
    hash_calls.clear()
    import_calls.clear()
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert hash_calls == ["config.json"]
    assert import_calls == []
    assert rerank._PAIR_BACKEND is None


def test_cached_backend_rejects_changed_or_missing_directory(monkeypatch, tmp_path):
    backend = PairBackend(logits_by_passage={MATCH_TEXT: 1.0})
    pair_dir = _touch_pair_dir(tmp_path / "pair-reranker")
    _install_backend(monkeypatch, tmp_path, backend)
    ranked = [_row("c0")]
    rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert rerank._PAIR_BACKEND is not None
    loads = len(backend.load_calls)
    encodes = len(FakeTokenizer.encode_calls)
    other = _touch_pair_dir(tmp_path / "other-pair")
    hashed_roots: list[str] = []

    def selective_hash(path):
        hashed_roots.append(str(Path(path).resolve().parent))
        if Path(path).resolve().parent == other.resolve():
            return "0" * 64
        return rerank.PAIR_RERANKER_FILE_SHA256[Path(path).name]

    monkeypatch.setattr(rerank, "_hash_pair_file", selective_hash)
    monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(other))
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert hashed_roots and hashed_roots[0] == str(other.resolve())
    assert len(backend.load_calls) == loads
    assert len(FakeTokenizer.encode_calls) == encodes
    assert rerank._PAIR_BACKEND is None

    monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
    rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert rerank._PAIR_BACKEND is not None
    recached_loads = len(backend.load_calls)
    recached_encodes = len(FakeTokenizer.encode_calls)
    assert recached_loads > loads
    monkeypatch.delenv("DISSOLVE_BGE_RERANKER_DIR", raising=False)
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, ranked, rerank.PAIR_RERANK_MODE)
    assert len(backend.load_calls) == recached_loads
    assert len(FakeTokenizer.encode_calls) == recached_encodes
    assert rerank._PAIR_BACKEND is None


def test_missing_and_mismatched_artifacts_fail_closed(monkeypatch, tmp_path):
    missing = tmp_path / "missing"
    missing.mkdir()
    monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(missing))
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, [_row("a")], rerank.PAIR_RERANK_MODE)
    pair_dir = _touch_pair_dir(tmp_path / "bad-hash")
    monkeypatch.setenv("DISSOLVE_BGE_RERANKER_DIR", str(pair_dir))
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, [_row("a")], rerank.PAIR_RERANK_MODE)
    monkeypatch.delenv("DISSOLVE_BGE_RERANKER_DIR", raising=False)
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, [_row("a")], rerank.PAIR_RERANK_MODE)


def test_incompatible_config_and_cross_encoder_not_substituted(monkeypatch, tmp_path):
    backend = PairBackend(model_spec={"model_type": "bert", "num_labels": 1})
    _install_backend(monkeypatch, tmp_path, backend)
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, [_row("a")], rerank.PAIR_RERANK_MODE)
    assert rerank._PAIR_BACKEND is None
    _reset_pair_state()
    backend = PairBackend(model_spec={"model_type": "xlm-roberta", "num_labels": 2})
    _install_backend(monkeypatch, tmp_path, backend)
    with pytest.raises(rerank.RerankBlocked):
        rerank.reorder_window(QUERY, [_row("a")], rerank.PAIR_RERANK_MODE)
    with pytest.raises(RuntimeError, match="model forbidden"):
        rerank.reorder_window(QUERY, [_row("a")], "cross_encoder")


def test_public_product_invokes_pair_and_rrf(monkeypatch, tmp_path):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    chunks = [
        _chunk("a", body="pa"),
        _chunk("b", body="pb"),
        _chunk("c", body="pc"),
    ]
    index = _index(chunks)
    _install_scores(monkeypatch, [0.3, 0.2, 0.1], [1.0, 1.0, 1.0])
    backend = PairBackend(logits_by_passage={"pa": 0.0, "pb": 1.0, "pc": 2.0})
    _install_backend(monkeypatch, tmp_path, backend)
    rrf_calls: list[tuple[list[str], list[str]]] = []
    real_fuse = rerank.fuse_rrf60

    def spy_fuse(before, pair):
        rrf_calls.append((_ranked_ids(list(before)), _ranked_ids(list(pair))))
        return real_fuse(before, pair)

    monkeypatch.setattr(rerank, "fuse_rrf60", spy_fuse)
    parsed = _public(monkeypatch, index, top_k=3)
    data = parsed["data"]
    assert data["success"] is True
    assert _ids(data["results"]) == ["a", "c", "b"]
    assert rrf_calls[0][0] == ["a", "b", "c"]
    assert rrf_calls[0][1] == ["c", "b", "a"]
    expected = _rrf(1, 3)
    assert data["results"][0]["final_score"] == round(expected, 6)
    assert data["results"][0]["final_score"] == data["results"][1]["final_score"]
    assert FakeTokenizer.encode_calls
    assert backend.load_calls


def test_gate_minilm_and_nonproduct_skip_pair(monkeypatch, tmp_path):
    backend = PairBackend(logits_by_passage={MATCH_TEXT: 9.0})
    _install_backend(monkeypatch, tmp_path, backend)
    rrf_calls: list = []
    monkeypatch.setattr(
        rerank,
        "fuse_rrf60",
        lambda *args, **kwargs: rrf_calls.append(args) or (_ for _ in ()).throw(AssertionError("rrf")),
    )

    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    gated = _index(
        [_chunk("c0", text=NOMATCH_TEXT, body=NOMATCH_TEXT),
         _chunk("c1", text=NOMATCH_TEXT, body=NOMATCH_TEXT),
         _chunk("c2", text=NOMATCH_TEXT, body=NOMATCH_TEXT)],
        floor=UNCHANGED_FLOOR,
    )
    _install_scores(monkeypatch, [0.9, 0.8, 0.7], [1.0, 1.0, 1.0])
    parsed = _public(monkeypatch, gated)
    assert parsed["data"]["reason"] == "abstained_below_floor"
    assert parsed["data"]["result_count"] == 0
    assert FakeTokenizer.encode_calls == []
    assert rrf_calls == []
    assert backend.load_calls == []

    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "minilm")
    minilm_index = _index([_chunk("c0"), _chunk("c1"), _chunk("c2")], identity="minilm")
    _install_scores(monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
    parsed = _public(monkeypatch, minilm_index, top_k=3)
    assert parsed["data"]["success"] is True
    assert FakeTokenizer.encode_calls == []
    assert rrf_calls == []

    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    other = _index([_chunk("c0"), _chunk("c1"), _chunk("c2")], knowledgebase=INDEPENDENT_KB)
    _install_scores(monkeypatch, [0.10, 0.11, 0.12], [1.0, 1.0, 1.0])
    parsed = _public(monkeypatch, other, top_k=3, knowledgebase=INDEPENDENT_KB)
    assert parsed["data"]["success"] is True
    assert FakeTokenizer.encode_calls == []
    assert rrf_calls == []


def test_public_depths_and_no_sparse_reason(monkeypatch, tmp_path):
    monkeypatch.setenv("DISSOLVE_CORPUS_PROFILE", "bge10")
    chunks = [_chunk(f"c{i:02d}", body=f"p{i:02d}") for i in range(25)]
    index = _index(chunks)
    dense = [float(i) for i in range(25)]
    sparse = [1.0] * 25
    _install_scores(monkeypatch, dense, sparse)
    backend = PairBackend(logits_by_passage={f"p{i:02d}": 0.0 for i in range(25)})
    _install_backend(monkeypatch, tmp_path, backend)
    for depth in (5, 10, 20):
        FakeTokenizer.encode_calls.clear()
        parsed = _public(monkeypatch, index, top_k=depth)
        data = parsed["data"]
        assert data["success"] is True
        assert data["result_count"] == depth
        assert len(data["results"]) == depth
        assert "reason" not in data
        scored = [p for _q, passages, _kw in FakeTokenizer.encode_calls for p in passages]
        assert len(scored) == 20
    empty_sparse = _index([_chunk("c0"), _chunk("c1"), _chunk("c2")])
    _install_scores(monkeypatch, [0.9, 0.8, 0.7], [0.0, 0.0, 0.0])
    FakeTokenizer.encode_calls.clear()
    parsed = _public(monkeypatch, empty_sparse)
    assert parsed["data"]["reason"] == "no_sparse_match"
    assert FakeTokenizer.encode_calls == []


def test_canonical_passage_and_query_has_no_prefix(monkeypatch, tmp_path):
    chunk = _chunk(
        "disc",
        text="TEXT-ONLY",
        body="BODY-ONLY",
        caption="CAPTION-ONLY",
        body_plus_rebound="REBOUND-ONLY",
    )
    expected = research.chunk_sparse_corpus(chunk)
    assert expected == "REBOUND-ONLY"
    assert expected != "BODY-ONLY"
    backend = PairBackend(logits_by_passage={expected: 1.5, "BODY-ONLY": 9.0, "TEXT-ONLY": 8.0})
    _install_backend(monkeypatch, tmp_path, backend)
    out = rerank.reorder_window(QUERY, [(0.0, 0.0, 0.0, 0.0, chunk, 0.0)], rerank.PAIR_RERANK_MODE)
    assert FakeTokenizer.encode_calls
    queries, passages, _kwargs = FakeTokenizer.encode_calls[0]
    assert passages == [expected]
    assert queries == [QUERY]
    assert not any(text.startswith(BGE_QUERY_PREFIX) for text in queries)
    assert BGE_QUERY_PREFIX not in queries[0]
    assert out[0][0] == 1.5
