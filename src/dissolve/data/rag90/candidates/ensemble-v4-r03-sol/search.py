"""Round-robin of enrich-fig-v1-r03-sol then fuse-core-v1-r03, then one plain pack.

enrich-fig-v1-r03-sol is the primary list: its r3 order over enrichment index_text,
including the figure-content block on the chunks that carry one.
fuse-core-v1-r03 contributes the order from its frozen core-rank fusion at a
budget large enough to return that order. Duplicates are dropped. Packing
keeps whole passages and stops at the first that does not fit. No weights
and no logs. Frozen candidates are not modified.
"""

from __future__ import annotations

import os

import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

_ROOT = Path(__file__).resolve().parent
_CANDIDATES = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "candidates")
_ARMS = ("enrich-fig-v1-r03-sol", "fuse-core-v1-r03")
_RANKERS = None
_LENGTHS = None
_FUSE_BUDGET = 10**12


def configure(**params):
    if params:
        raise ValueError("no_parameters")


def _purge() -> None:
    for key in list(sys.modules):
        if key == "rag90search" or key.startswith("rag90search.") or key == "rag90coverage" or key.startswith("rag90coverage."):
            del sys.modules[key]


def _load(name: str):
    path = _CANDIDATES / name / "search.py"
    _purge()
    sys.path.insert(0, str(path.parent / "vendor"))
    spec = importlib.util.spec_from_file_location("ensemble4_" + name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rankers() -> dict:
    global _RANKERS
    if _RANKERS is None:
        loaded = {name: _load(name) for name in _ARMS}
        enrich_file = loaded["enrich-fig-v1-r03-sol"].search.__code__.co_filename
        if "enrich-fig-v1-r03-sol" not in enrich_file:
            raise RuntimeError("enrich_mismatch")
        fuse_file = loaded["fuse-core-v1-r03"].search.__code__.co_filename
        if "fuse-core-v1-r03" not in fuse_file:
            raise RuntimeError("fuse_mismatch")
        _RANKERS = loaded
    return _RANKERS


def _lengths() -> dict[str, int]:
    global _LENGTHS
    if _LENGTHS is None:
        lengths = {}
        with (_ROOT / "passages.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                lengths[row["id"]] = len(row["text"])
        _LENGTHS = lengths
    return _LENGTHS


def _enrich_ids(folded: str) -> list[str]:
    return _rankers()["enrich-fig-v1-r03-sol"]._engine().rank_ids(folded, k=None)


def _fuse_ids(folded: str) -> list[str]:
    return _rankers()["fuse-core-v1-r03"].search(folded, _FUSE_BUDGET)


def _round_robin(lists: list[list[str]]) -> list[str]:
    seen = set()
    merged = []
    longest = max((len(item) for item in lists), default=0)
    for index in range(longest):
        for ranked in lists:
            if index >= len(ranked):
                continue
            passage_id = ranked[index]
            if passage_id in seen:
                continue
            seen.add(passage_id)
            merged.append(passage_id)
    return merged


def ordered(query: str) -> list[str]:
    folded = " ".join(str(query).split())
    return _round_robin([_enrich_ids(folded), _fuse_ids(folded)])


def _pack(ids: list[str], lengths: dict[str, int], budget_chars: int) -> list[str]:
    budget = int(budget_chars)
    if budget <= 0:
        return []
    chosen = []
    used = 0
    for passage_id in ids:
        length = int(lengths[passage_id])
        if used + length <= budget:
            chosen.append(passage_id)
            used += length
        else:
            break
    return chosen


def search(query: str, budget_chars: int) -> list[str]:
    if not str(query or "").strip():
        return []
    budget = int(budget_chars)
    if budget <= 0:
        return []
    return _pack(ordered(query), _lengths(), budget)
