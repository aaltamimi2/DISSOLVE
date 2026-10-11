"""Gate-E candidate. focus-base-v4 packing with one fixed point. No logs and no writes.

The point is focus-base-v4 grid index 0: f1=0.5, s=0, margin=0.25. Family-cluster
leave-one-cluster-out selected it on 21 of 21 folds. s=0 makes the margin unused.
Rank-1 gets half the budget in within-paper BM25 order, and a chunk that does not
fit is skipped. The rest of the budget walks the ensemble-v4 order and stops at
the first chunk that does not fit. There is no parameter grid.
"""

from __future__ import annotations

import os

import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.dont_write_bytecode = True

_ROOT = Path(__file__).resolve().parent
_CANDIDATES = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "candidates")
_PAPERS = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "corpus/served-index-r03/papers.json")
_WITHIN_PATH = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "candidates/rag90-final-v1-r03-sol/within_paper.py")
_F1 = 0.5
_S = 0.0
_MARGIN = 0.25
_BASE = None
_WITHIN = None
_META = None
_TITLES = None


def configure(**params):
    if params:
        raise ValueError("no_parameters")


def _purge() -> None:
    for key in list(sys.modules):
        if key == "rag90search" or key.startswith("rag90search.") or key == "rag90coverage" or key.startswith("rag90coverage."):
            del sys.modules[key]


def _ensemble():
    global _BASE
    if _BASE is None:
        path = _CANDIDATES / "ensemble-v4-r03-sol" / "search.py"
        _purge()
        sys.path.insert(0, str(path.parent / "vendor"))
        spec = importlib.util.spec_from_file_location("rag90_final_v1_ensemble", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _BASE = module
    return _BASE


def _within():
    global _WITHIN
    if _WITHIN is None:
        spec = importlib.util.spec_from_file_location("rag90_final_v1_within", _WITHIN_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _WITHIN = module
    return _WITHIN


def _meta():
    global _META
    if _META is None:
        paper_of = {}
        lengths = {}
        with (_ROOT / "passages.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                paper_of[row["id"]] = row["paper_sha256"]
                lengths[row["id"]] = len(row["text"])
        _META = (paper_of, lengths)
    return _META


def _titles():
    global _TITLES
    if _TITLES is None:
        payload = json.loads(_PAPERS.read_text(encoding="utf-8"))
        found = []
        for row in payload["papers"].values():
            title = " ".join(str(row.get("title") or "").split()).casefold()
            if len(title) >= 24:
                found.append(title)
        _TITLES = tuple(found)
    return _TITLES


def looks_cross(query: str) -> bool:
    lowered = " ".join(str(query).split()).casefold()
    hits = 0
    for title in _titles():
        if title in lowered:
            hits += 1
            if hits >= 2:
                return True
    return False


def base_order(query: str) -> list[str]:
    folded = " ".join(str(query).split())
    return list(_ensemble().ordered(folded))


def _soft3(groups: dict) -> dict:
    scored = {}
    for paper, values in groups.items():
        top = sorted(values, reverse=True)[:3]
        peak = max(top)
        weights = [math.exp(value - peak) for value in top]
        normal = sum(weights)
        scored[paper] = sum(weight * value for weight, value in zip(weights, top)) / normal
    return scored


def rank_papers(order, paper_of):
    groups = defaultdict(list)
    first = []
    found = set()
    for index, pid in enumerate(order):
        paper = paper_of.get(pid)
        if not paper:
            continue
        groups[paper].append(1.0 / (index + 1))
        if paper not in found:
            found.add(paper)
            first.append(paper)
    scored = _soft3(groups) if groups else {}
    position = {paper: index for index, paper in enumerate(first)}
    ranked = sorted(first, key=lambda paper: (-scored.get(paper, float("-inf")), position[paper]))
    return ranked, scored


def bm25_for(query: str, paper: str, lengths: dict):
    module = _within()
    scores = module.bm25_scores(query, paper)
    order = [pid for pid in module.order_bm25(query, paper) if pid in lengths]
    return order, scores


def focus_pack(order, paper_of, lengths, budget, f1, second, margin, cross, dense_for) -> list[str]:
    budget = int(budget)
    if budget <= 0:
        return []
    chosen = []
    seen = set()
    used = 0

    def take(pid, length):
        nonlocal used
        chosen.append(pid)
        seen.add(pid)
        used += length

    def fill(paper: str, cap: int) -> None:
        spent = 0
        for pid in dense_for(paper):
            if pid in seen or paper_of.get(pid) != paper:
                continue
            length = lengths.get(pid)
            if length is None:
                continue
            if spent + length > cap or used + length > budget:
                continue
            take(pid, length)
            spent += length

    if not cross and (float(f1) > 0.0 or float(second) > 0.0):
        ranked, scored = rank_papers(order, paper_of)
        if ranked and float(f1) > 0.0:
            fill(ranked[0], int(round(float(f1) * budget)))
        if ranked and len(ranked) > 1 and float(second) > 0.0:
            primary = scored.get(ranked[0], 0.0)
            other = scored.get(ranked[1], 0.0)
            gap = (primary - other) / abs(primary) if primary else 0.0
            if gap < float(margin):
                fill(ranked[1], int(round(float(second) * budget)))
    for pid in order:
        if pid in seen:
            continue
        length = lengths.get(pid)
        if length is None:
            continue
        if used + length <= budget:
            take(pid, length)
        else:
            break
    return chosen


def _dense_for(query: str, lengths: dict):
    cached = {}

    def lookup(paper: str):
        if paper not in cached:
            order, _scores = bm25_for(query, paper, lengths)
            cached[paper] = order
        return cached[paper]

    return lookup


def search(query: str, budget_chars: int) -> list[str]:
    if not str(query or "").strip():
        return []
    budget = int(budget_chars)
    if budget <= 0:
        return []
    paper_of, lengths = _meta()
    order = base_order(query)
    return focus_pack(
        order,
        paper_of,
        lengths,
        budget,
        _F1,
        _S,
        _MARGIN,
        looks_cross(query),
        _dense_for(query, lengths),
    )
