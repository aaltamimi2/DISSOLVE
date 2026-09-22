"""Gated-hybrid top-20 rerank. Named cross-encoder or BLOCKED. No silent substitute."""

from __future__ import annotations

import hashlib
import importlib
import math
import os
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

CROSS_ENCODER_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"
WINDOW = 20
SHUFFLE_SEED = 20260830
PAIR_RERANK_MODE = "bge_pair"
MODES = frozenset({"off", "cross_encoder", "shuffle", PAIR_RERANK_MODE})
PAIR_RERANKER_ID = "BAAI/bge-reranker-base"
PAIR_RERANKER_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
PAIR_RERANKER_MODEL_TYPE = "xlm-roberta"
PAIR_RERANKER_NUM_LABELS = 1
PAIR_BATCH_SIZE = 8
PAIR_MAX_LENGTH = 512
PAIR_CPU_THREADS = 8
PAIR_INTEROP_THREADS = 1
RRF_K = 60
_ENV_BGE_RERANKER_DIR = "DISSOLVE_BGE_RERANKER_DIR"
PAIR_RERANKER_FILE_SHA256 = {
    "config.json": "289adf7ada1eb6b4afa7589a48a032d45a076cf2e46dcdb3b4cabc33be14f708",
    "model.safetensors": "ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd",
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    "special_tokens_map.json": "d5469a60db23249c7f8945013d78df30b44b6bf686c6bb4740f4223f77b1b535",
    "tokenizer.json": "9eb652ac4e40cc093272bbbe0f55d521cf67570060227109b5cdc20945a4489e",
    "tokenizer_config.json": "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
}

_MODEL: Any = None
_PAIR_BACKEND: Any = None
_PAIR_INTEROP_READY = False


class RerankBlocked(RuntimeError):
    """Named model could not be loaded. Never a bi-encoder fallback."""

    def __init__(self, message: str = "rerank_blocked") -> None:
        super().__init__(message)
        self.code = "rerank_blocked"


def _passage(chunk: Any) -> str:
    if chunk.get("body") is not None:
        return str(chunk.get("body") or "")
    return str(chunk.get("text") or "")


def _pair_passage(chunk: Any) -> str:
    from dissolve.research import chunk_sparse_corpus
    return chunk_sparse_corpus(chunk)


def _load_cross_encoder():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import CrossEncoder
        _MODEL = CrossEncoder(CROSS_ENCODER_ID)
    except Exception as error:
        raise RerankBlocked("rerank_blocked") from error
    return _MODEL


def _score_pairs(query: str, passages: Sequence[str]) -> list[float]:
    model = _load_cross_encoder()
    pairs = [(str(query or ""), str(passage or "")) for passage in passages]
    scores = model.predict(pairs, show_progress_bar=False)
    return [float(score) for score in scores]


def _hash_pair_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _pair_dir() -> Path:
    raw = os.getenv(_ENV_BGE_RERANKER_DIR)
    if not isinstance(raw, str) or not raw.strip():
        raise RerankBlocked("rerank_blocked")
    directory = Path(raw).expanduser()
    if not directory.is_dir():
        raise RerankBlocked("rerank_blocked")
    return directory


def _verify_pair_artifacts(directory: Path) -> str:
    identity = str(directory.resolve())
    for name, expected in PAIR_RERANKER_FILE_SHA256.items():
        path = directory / name
        if not path.is_file():
            raise RerankBlocked("rerank_blocked")
        actual = _hash_pair_file(path)
        if actual != expected:
            raise RerankBlocked("rerank_blocked")
    return identity


def _drop_pair_backend() -> None:
    global _PAIR_BACKEND
    _PAIR_BACKEND = None


def _cached_pair_backend(resolved: str):
    if _PAIR_BACKEND is None:
        return None
    _tokenizer, _model, _torch_mod, model_id, revision, cached_resolved = _PAIR_BACKEND
    if (
        model_id == PAIR_RERANKER_ID
        and revision == PAIR_RERANKER_REVISION
        and cached_resolved == resolved
    ):
        return _PAIR_BACKEND
    _drop_pair_backend()
    return None


def _configure_pair_threads(torch_mod: Any) -> None:
    global _PAIR_INTEROP_READY
    torch_mod.set_num_threads(PAIR_CPU_THREADS)
    if _PAIR_INTEROP_READY:
        current = int(torch_mod.get_num_interop_threads())
        if current != PAIR_INTEROP_THREADS:
            raise RerankBlocked("rerank_blocked")
        return
    try:
        torch_mod.set_num_interop_threads(PAIR_INTEROP_THREADS)
    except RuntimeError as error:
        current = int(torch_mod.get_num_interop_threads())
        if current != PAIR_INTEROP_THREADS:
            raise RerankBlocked("rerank_blocked") from error
    current = int(torch_mod.get_num_interop_threads())
    if current != PAIR_INTEROP_THREADS:
        raise RerankBlocked("rerank_blocked")
    _PAIR_INTEROP_READY = True


def _cpu_value(value: Any) -> Any:
    if hasattr(value, "to"):
        return value.to("cpu")
    return value


def _assert_pair_config(model: Any) -> None:
    config = getattr(model, "config", None)
    if config is None:
        raise RerankBlocked("rerank_blocked")
    if getattr(config, "model_type", None) != PAIR_RERANKER_MODEL_TYPE:
        raise RerankBlocked("rerank_blocked")
    if getattr(config, "num_labels", None) != PAIR_RERANKER_NUM_LABELS:
        raise RerankBlocked("rerank_blocked")


def _load_pair_reranker():
    global _PAIR_BACKEND
    try:
        directory = _pair_dir()
    except RerankBlocked:
        _drop_pair_backend()
        raise
    resolved = str(directory.resolve())
    cached = _cached_pair_backend(resolved)
    if cached is not None:
        return cached
    resolved = _verify_pair_artifacts(directory)
    try:
        torch_mod = importlib.import_module("torch")
        transformers_mod = importlib.import_module("transformers")
    except ImportError as error:
        _drop_pair_backend()
        raise RerankBlocked("rerank_blocked") from error
    _configure_pair_threads(torch_mod)
    tokenizer_cls = getattr(transformers_mod, "AutoTokenizer", None)
    model_cls = getattr(transformers_mod, "AutoModelForSequenceClassification", None)
    if tokenizer_cls is None or model_cls is None:
        _drop_pair_backend()
        raise RerankBlocked("rerank_blocked")
    try:
        tokenizer = tokenizer_cls.from_pretrained(
            resolved,
            revision=PAIR_RERANKER_REVISION,
            local_files_only=True,
            use_fast=True,
            truncation_side="right",
        )
        model = model_cls.from_pretrained(
            resolved,
            revision=PAIR_RERANKER_REVISION,
            local_files_only=True,
            torch_dtype=torch_mod.float32,
        )
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        _drop_pair_backend()
        raise RerankBlocked("rerank_blocked") from error
    try:
        _assert_pair_config(model)
        model = _cpu_value(model)
        if hasattr(model, "float"):
            model = model.float()
        if not hasattr(model, "eval"):
            raise RerankBlocked("rerank_blocked")
        model = model.eval()
    except RerankBlocked:
        _drop_pair_backend()
        raise
    _PAIR_BACKEND = (tokenizer, model, torch_mod, PAIR_RERANKER_ID, PAIR_RERANKER_REVISION, resolved)
    return _PAIR_BACKEND


def _logits_shape(logits: Any) -> tuple[int, ...]:
    try:
        return tuple(int(dim) for dim in logits.shape)
    except (TypeError, ValueError, AttributeError) as error:
        raise RerankBlocked("rerank_blocked") from error


def _assert_pair_logits(logits: Any, batch_count: int, torch_mod: Any) -> None:
    if logits is None:
        raise RerankBlocked("rerank_blocked")
    shape = _logits_shape(logits)
    if shape != (batch_count, 1):
        raise RerankBlocked("rerank_blocked")
    dtype = getattr(logits, "dtype", None)
    expected = getattr(torch_mod, "float32", None)
    if expected is not None and dtype != expected:
        raise RerankBlocked("rerank_blocked")


def _logit_score(logits: Any, index: int) -> float:
    try:
        value = logits[index, 0]
    except (TypeError, ValueError, IndexError, KeyError) as error:
        raise RerankBlocked("rerank_blocked") from error
    if hasattr(value, "item"):
        value = value.item()
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise RerankBlocked("rerank_blocked") from error
    if not math.isfinite(score):
        raise RerankBlocked("rerank_blocked")
    return score


def _encode_pair_batch(tokenizer: Any, queries: list[str], passages: list[str]) -> Mapping[str, Any]:
    encoded = tokenizer(
        queries,
        passages,
        padding=True,
        truncation="longest_first",
        max_length=PAIR_MAX_LENGTH,
        return_tensors="pt",
    )
    if hasattr(encoded, "items"):
        payload = {key: _cpu_value(value) for key, value in encoded.items()}
    elif isinstance(encoded, Mapping):
        payload = {key: _cpu_value(value) for key, value in encoded.items()}
    else:
        raise RerankBlocked("rerank_blocked")
    return payload


def _score_bge_pairs(query: str, passages: Sequence[str]) -> list[float]:
    tokenizer, model, torch_mod, _model_id, _revision, _source = _load_pair_reranker()
    query_text = str(query or "")
    scores: list[float] = []
    with torch_mod.inference_mode():
        for start in range(0, len(passages), PAIR_BATCH_SIZE):
            batch = [str(passage or "") for passage in passages[start:start + PAIR_BATCH_SIZE]]
            queries = [query_text] * len(batch)
            payload = _encode_pair_batch(tokenizer, queries, batch)
            try:
                outputs = model(**payload)
            except (TypeError, ValueError, RuntimeError) as error:
                raise RerankBlocked("rerank_blocked") from error
            logits = getattr(outputs, "logits", None)
            _assert_pair_logits(logits, len(batch), torch_mod)
            for index in range(len(batch)):
                scores.append(_logit_score(logits, index))
    if len(scores) != len(passages):
        raise RerankBlocked("rerank_blocked")
    return scores


def _candidate_id(item: tuple[Any, ...]) -> str:
    try:
        chunk = item[4]
        chunk_id = chunk["chunk_id"]
    except (TypeError, KeyError, IndexError) as error:
        raise ValueError("rrf_membership") from error
    return str(chunk_id)


def _unique_ids(ranked: Sequence[tuple[Any, ...]]) -> list[str]:
    ids = [_candidate_id(item) for item in ranked]
    if len(ids) != len(set(ids)):
        raise ValueError("rrf_duplicate")
    return ids


def fuse_rrf60(
    before: Sequence[tuple[Any, ...]],
    pair: Sequence[tuple[Any, ...]],
) -> list[tuple[Any, ...]]:
    """Closed RRF60 over the same candidate set. Ranks are one-based."""
    if not before and not pair:
        return []
    before_ids = _unique_ids(before)
    pair_ids = _unique_ids(pair)
    if len(before_ids) != len(pair_ids) or set(before_ids) != set(pair_ids):
        raise ValueError("rrf_membership")
    rank_before = {chunk_id: index + 1 for index, chunk_id in enumerate(before_ids)}
    rank_pair = {chunk_id: index + 1 for index, chunk_id in enumerate(pair_ids)}
    fused: list[tuple[Any, ...]] = []
    for item in before:
        chunk_id = _candidate_id(item)
        score = 1.0 / (RRF_K + rank_before[chunk_id]) + 1.0 / (RRF_K + rank_pair[chunk_id])
        fused.append((score, item[1], item[2], item[3], item[4], item[5]))
    fused.sort(key=lambda item: (-item[0], rank_before[_candidate_id(item)]))
    return fused


def _normalize_mode(rerank_mode: str) -> str:
    mode = str(rerank_mode or "off").strip().casefold()
    if mode in {"", "off", "none", "identity"}:
        return "off"
    if mode not in MODES:
        raise ValueError("unknown_rerank_mode")
    return mode


def reorder_window(
    query: str,
    ranked: list[tuple[Any, ...]],
    rerank_mode: str = "off",
) -> list[tuple[Any, ...]]:
    """Reorder ranked[:20] only. Identity when off. Shuffle is not a shipped arm."""
    mode = _normalize_mode(rerank_mode)
    if not ranked or mode == "off":
        return ranked
    window = list(ranked[:WINDOW])
    rest = list(ranked[WINDOW:])
    if not window:
        return ranked
    if mode == "shuffle":
        rng = random.Random(SHUFFLE_SEED)
        rng.shuffle(window)
        return window + rest
    if mode == PAIR_RERANK_MODE:
        _unique_ids(window)
        passages = [_pair_passage(item[4]) for item in window]
        scores = _score_bge_pairs(query, passages)
        if len(scores) != len(window):
            raise RerankBlocked("rerank_blocked")
        order = sorted(range(len(window)), key=lambda i: (-float(scores[i]), i))
        reordered: list[tuple[Any, ...]] = []
        for i in order:
            item = window[i]
            reordered.append((
                float(scores[i]), item[1], item[2], item[3], item[4], item[5],
            ))
        return reordered + rest
    passages = [_passage(item[4]) for item in window]
    scores = _score_pairs(query, passages)
    order = sorted(range(len(window)), key=lambda i: (-float(scores[i]), i))
    reordered = []
    for i in order:
        item = window[i]
        reordered.append((
            float(scores[i]), item[1], item[2], item[3], item[4], item[5],
        ))
    return reordered + rest


