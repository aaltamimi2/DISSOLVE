"""Literature search with the RAG-90 designs, packaged from their frozen candidates.

The 89-paper Sol engine rag90-final-v1-r03-sol is the design research serves. ensemble-v4-r03-sol is its base without the focus packing,
packaged for the paper's ablation and as the simpler design to serve. search(query, budget_chars, design) returns the
ids of the served corpus chunks (T5-...) to show the model: whole passages in rank order while they fit the character
budget (7,000, 14,000 and 28,000 are the benchmark's B5, B10 and B20).

The frozen runtime tree lives in data/rag90 with its original layout. Three kinds of line were rewritten, nothing else:
absolute paths now read DISSOLVE_RAG90_ROOT, the int8 BGE encoder and its tokenizer resolve to DISSOLVE's pinned and
hash-checked files, and the vendored package coverage is named rag90coverage (the original would shadow coverage.py
and is purged from sys.modules on load). ensemble-v4's passages.jsonl is the same file as final-v1's, as it is in the
frozen candidates. The served-equivalence check compares this module's lists with each frozen candidate's, question
by question.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).with_name("data") / "rag90"
DESIGNS = {"rag90-final-v1": "dissolve_rag90_final_v1", "ensemble-v4": "dissolve_rag90_ensemble_v4"}
DEFAULT_DESIGN = "rag90-final-v1"
RUNTIME_DESIGNS = {"rag90-final-v1": "rag90-final-v1-r03-sol", "ensemble-v4": "ensemble-v4-r03-sol"}
_LOCK = threading.Lock()
_MODULES: dict[str, object] = {}
_CHUNK_IDS: frozenset[str] | None = None


def chunk_ids() -> frozenset[str]:
    """The 4,432 chunk ids of the served 89-paper corpus; research refuses any other chunk set."""
    global _CHUNK_IDS
    if _CHUNK_IDS is None:
        passages = ROOT / "candidates" / RUNTIME_DESIGNS[DEFAULT_DESIGN] / "passages.jsonl"
        with passages.open(encoding="utf-8") as handle:
            ids = frozenset(json.loads(line)["id"] for line in handle if line.strip())
        if len(ids) != 4432:
            raise RuntimeError("rag90_incomplete_corpus")
        _CHUNK_IDS = ids
    return _CHUNK_IDS


def _module(design: str):
    if design not in DESIGNS:
        raise ValueError(f"unknown rag90 design: {design}")
    module = _MODULES.get(design)
    if module is None:
        if not (os.getenv("DISSOLVE_BGE_ONNX_INT8") or "").strip():
            raise RuntimeError("rag90 search needs DISSOLVE_BGE_ONNX_INT8, the pinned int8 BGE encoder")
        os.environ["DISSOLVE_RAG90_ROOT"] = str(ROOT)
        spec = importlib.util.spec_from_file_location(DESIGNS[design], ROOT / "candidates" / RUNTIME_DESIGNS[design] / "search.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _MODULES[design] = module
    return module


def search(query: str, budget_chars: int, design: str = DEFAULT_DESIGN) -> list[str]:
    """Packed chunk ids for one query. Calls are serialised: the frozen code fills module-level caches lazily.
    Each design loads its own encoder sessions, so a server should use one design."""
    with _LOCK:
        return list(_module(design).search(query, int(budget_chars)))
