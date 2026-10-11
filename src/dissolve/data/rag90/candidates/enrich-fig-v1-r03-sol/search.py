"""Served chunks, enrichment indexed only, slack-neighbour packing. No logs and no writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "vendor"))

from rag90search.expand import pack_expand, pack_slack
from rag90search.r3 import Searcher

_THRESHOLD = 12200
_ENGINE = None
_PREV = None
_NEXT = None
_LENGTHS = None


def configure(**params):
    if params:
        raise ValueError("no_parameters")


def _maps():
    global _PREV, _NEXT, _LENGTHS
    if _LENGTHS is None:
        prev_of = {}
        next_of = {}
        lengths = {}
        with (_ROOT / "passages.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                prev_of[row["id"]] = row.get("prev")
                next_of[row["id"]] = row.get("next")
                lengths[row["id"]] = len(row["text"])
        _PREV, _NEXT, _LENGTHS = prev_of, next_of, lengths
    return _PREV, _NEXT, _LENGTHS


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Searcher.open(_ROOT / "index")
    return _ENGINE


def search(query: str, budget_chars: int) -> list[str]:
    if not str(query or "").strip():
        return []
    budget = int(budget_chars)
    if budget <= 0:
        return []
    ranked = _engine().rank_ids(query, k=None)
    prev_of, next_of, lengths = _maps()
    if budget < _THRESHOLD:
        return pack_slack(ranked, prev_of, next_of, 1, lengths, budget)
    return pack_expand(ranked, prev_of, next_of, 1, lengths, budget, expand_limit=2)
