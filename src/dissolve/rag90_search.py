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

The frozen rankers each open the same int8 encoder file. While this module loads or runs them, a second request for
a file with the same session settings gets the session already open: one copy of the model in memory (about 160 MB
less on the 89-paper engine) and, since a session holds no state between runs, the same lists.
"""

from __future__ import annotations

import contextlib
import ctypes
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
_SESSIONS: dict[tuple, object] = {}


@contextlib.contextmanager
def _shared_sessions():
    """Inside the block, onnxruntime.InferenceSession(path, options, providers=...) returns one session per model file
    and thread settings. Any other call shape gets a session of its own. Callers hold _LOCK."""
    import onnxruntime

    real = onnxruntime.InferenceSession

    def shared(path, options=None, *args, providers=None, **kwargs):
        if options is None or args or kwargs:
            return real(path, options, *args, providers=providers, **kwargs)
        key = (str(path), options.intra_op_num_threads, options.inter_op_num_threads, tuple(providers or ()))
        if key not in _SESSIONS:
            _SESSIONS[key] = real(path, options, providers=providers)
        return _SESSIONS[key]

    onnxruntime.InferenceSession = shared
    try:
        yield
    finally:
        onnxruntime.InferenceSession = real


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


def _release_freed_memory() -> None:
    """Hand the C allocator's freed pages back to the system (glibc only; a no-op elsewhere). Loading the passages
    and each search leave tens of MB freed but still resident, which the hosted site's TEA worker needs."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def search(query: str, budget_chars: int, design: str = DEFAULT_DESIGN) -> list[str]:
    """Packed chunk ids for one query. Calls are serialised: the frozen code fills module-level caches lazily,
    and opens its encoder sessions on first use."""
    with _LOCK, _shared_sessions():
        try:
            return list(_module(design).search(query, int(budget_chars)))
        finally:
            _release_freed_memory()
