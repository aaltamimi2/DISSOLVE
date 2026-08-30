"""Gated-hybrid top-20 rerank. Named cross-encoder or BLOCKED. No silent substitute."""

from __future__ import annotations

import random
from typing import Any, Sequence

CROSS_ENCODER_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"
WINDOW = 20
SHUFFLE_SEED = 20260830
MODES = frozenset({"off", "cross_encoder", "shuffle"})

_MODEL: Any = None


class RerankBlocked(RuntimeError):
    """Named model could not be loaded. Never a bi-encoder fallback."""

    def __init__(self, message: str = "rerank_blocked") -> None:
        super().__init__(message)
        self.code = "rerank_blocked"


def _passage(chunk: Any) -> str:
    if chunk.get("body") is not None:
        return str(chunk.get("body") or "")
    return str(chunk.get("text") or "")


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
    passages = [_passage(item[4]) for item in window]
    scores = _score_pairs(query, passages)
    order = sorted(range(len(window)), key=lambda i: (-float(scores[i]), i))
    reordered: list[tuple[Any, ...]] = []
    for i in order:
        item = window[i]
        reordered.append((
            float(scores[i]), item[1], item[2], item[3], item[4], item[5],
        ))
    return reordered + rest


def reorder_chunks(
    query: str,
    chunks: Sequence[Any],
    rerank_mode: str = "off",
) -> list[Any]:
    ranked = [(0.0, 0.0, 0.0, 0.0, chunk, 0.0) for chunk in chunks]
    return [item[4] for item in reorder_window(query, ranked, rerank_mode)]
