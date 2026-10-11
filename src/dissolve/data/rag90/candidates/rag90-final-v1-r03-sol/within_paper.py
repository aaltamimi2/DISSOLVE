"""Order the chunks of one paper. Read-only. No logs and no writes.

order_in_paper(query, paper_sha256) returns that paper's chunk ids, best first.
The method is the silver leave-one-cluster-out winner recorded in within.json.
Chunk scores come from enrich-slack-v2. Question max-sim reads enrich-mv-v1's
question vectors and does not load a second encoder.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
sys.dont_write_bytecode = True

import numpy

_ROOT = Path(__file__).resolve().parent
_LIB = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "lib")
_ENRICH = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "candidates/enrich-slack-v2-r03-sol/search.py")
_MV = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "candidates/enrich-mv-v1-r03-sol/index")
_NOTES = (
    (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "enrich/batch-q-r03-sol/notes.jsonl"),
)
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from rag90search.r3 import bm25_query_tokens, load_lexicon, tokens

FEATURES = (
    "chunk_score",
    "question_maxsim",
    "kind_table",
    "kind_caption",
    "kind_text",
    "sec_reference",
    "sec_abstract",
    "sec_intro",
    "sec_method",
    "sec_results",
    "sec_conclusion",
    "sec_other",
    "distance",
)
_SECTIONS = (
    "reference",
    "abstract",
    "intro",
    "method",
    "results",
    "conclusion",
    "other",
)
_ENGINE = None
_CATALOG = None
_QVECS = None
_QOFF = None
_LEXICON = None
_CONFIG = None


def _purge() -> dict:
    saved = {}
    for key in list(sys.modules):
        if key == "rag90search" or key.startswith("rag90search.") or key == "rag90coverage" or key.startswith("rag90coverage."):
            saved[key] = sys.modules.pop(key)
    return saved


def _engine():
    global _ENGINE
    if _ENGINE is None:
        saved = _purge()
        sys.path.insert(0, str(_ENRICH.parent / "vendor"))
        spec = importlib.util.spec_from_file_location("within_enrich_slack_v2", _ENRICH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        filename = Path(module.Searcher.__init__.__code__.co_filename).as_posix()
        if "enrich-slack-v2" not in filename:
            raise RuntimeError(f"enrich_encoder {filename}")
        _ENGINE = module._engine()
        sys.modules.update(saved)
    return _ENGINE


def _lexicon():
    global _LEXICON
    if _LEXICON is None:
        _LEXICON = load_lexicon()
    return _LEXICON


def _questions():
    global _QVECS, _QOFF
    if _QVECS is None:
        _QVECS = numpy.load(_MV / "question_vectors.npy", mmap_mode="r")
        _QOFF = numpy.load(_MV / "question_offsets.npy", mmap_mode="r")
    return _QVECS, _QOFF


def coarse_section(section: str) -> str:
    folded = str(section or "").casefold()
    if "reference" in folded or "bibliograph" in folded:
        return "reference"
    if "abstract" in folded:
        return "abstract"
    if "intro" in folded:
        return "intro"
    if "method" in folded or "material" in folded or "experimental" in folded:
        return "method"
    if "result" in folded or "discussion" in folded:
        return "results"
    if "conclu" in folded:
        return "conclusion"
    return "other"


def kind_bucket(kind: str) -> str:
    if kind == "table":
        return "table"
    if kind == "caption":
        return "caption"
    return "text"


def note_text(note: dict) -> str:
    parts = [note.get("context") or "", note.get("section") or ""]
    parts.extend(str(item) for item in (note.get("questions") or []) if str(item).strip())
    entities = [str(item) for item in (note.get("entities") or []) if str(item).strip()]
    if entities:
        parts.append(" ".join(entities))
    for item in note.get("quantities") or []:
        parts.append(" ".join(str(item.get(key) or "") for key in ("number", "unit", "what")))
    return "\n".join(part for part in parts if str(part).strip())


def catalog() -> dict:
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    notes = {}
    for path in _NOTES:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            note = json.loads(line)
            if note["id"] in notes:
                raise RuntimeError(f"duplicate_note {note['id']}")
            notes[note["id"]] = note
    mv_ids = json.loads((_MV / "ids.json").read_text(encoding="utf-8"))
    row_of = {passage_id: index for index, passage_id in enumerate(mv_ids)}
    passages = []
    with (_ENRICH.parent / "passages.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            note = notes.get(row["id"])
            if note is None:
                raise RuntimeError(f"missing_note {row['id']}")
            body = note_text(note) + "\n\n" + row["text"]
            counted = tokens(body)
            passages.append(
                {
                    "id": row["id"],
                    "paper": row["paper_sha256"],
                    "char_start": int(row["char_start"]),
                    "text": row["text"],
                    "length": len(row["text"]),
                    "kind": kind_bucket(row.get("kind") or ""),
                    "section": coarse_section(note.get("section") or ""),
                    "qrow": row_of[row["id"]],
                    "tf": Counter(counted),
                    "doc_len": len(counted),
                }
            )
    if len(passages) != len(mv_ids) or set(row_of) != {row["id"] for row in passages}:
        raise RuntimeError("id_mismatch")
    by_paper: dict[str, list[dict]] = {}
    for row in passages:
        by_paper.setdefault(row["paper"], []).append(row)
    for chunks in by_paper.values():
        chunks.sort(key=lambda item: (item["char_start"], item["id"]))
        for index, chunk in enumerate(chunks):
            chunk["doc_index"] = index
    _CATALOG = {"by_paper": by_paper, "by_id": {row["id"]: row for row in passages}}
    return _CATALOG


def query_state(query: str) -> dict:
    folded = " ".join(str(query or "").split())
    if not folded:
        return {"folded": "", "scores": {}, "order": [], "vector": None}
    rows = _engine().rank_rows(folded)
    return {
        "folded": folded,
        "scores": {passage_id: float(score) for score, _title, passage_id in rows},
        "order": [passage_id for _score, _title, passage_id in rows],
        "vector": _engine().last_query_vector,
    }


def maxsim_by_row(query_vector) -> numpy.ndarray:
    ids = json.loads((_MV / "ids.json").read_text(encoding="utf-8"))
    out = numpy.zeros(len(ids), dtype=numpy.float64)
    if query_vector is None:
        return out
    vecs, offsets = _questions()
    dots = vecs @ numpy.asarray(query_vector, dtype=numpy.float64)
    for index in range(len(ids)):
        start = int(offsets[index])
        end = int(offsets[index + 1])
        if start == end:
            continue
        value = float(dots[start:end].max())
        if value > 0.0:
            out[index] = value
    return out


def _bm25_paper(chunks: list[dict], query_tokens: list[str]) -> dict[str, float]:
    scores = {chunk["id"]: 0.0 for chunk in chunks}
    if not query_tokens or not chunks:
        return scores
    df = Counter()
    for chunk in chunks:
        for term in chunk["tf"]:
            df[term] += 1
    count = len(chunks)
    average = sum(chunk["doc_len"] for chunk in chunks) / count
    scale = max(average, 1.0)
    for chunk in chunks:
        score = 0.0
        denom = 1.2 * (0.25 + 0.75 * chunk["doc_len"] / scale)
        for term in query_tokens:
            freq = chunk["tf"].get(term, 0)
            if not freq:
                continue
            seen = df[term]
            idf = math.log(1.0 + (count - seen + 0.5) / (seen + 0.5))
            score += idf * freq * 2.2 / (freq + denom)
        scores[chunk["id"]] = score
    return scores


def bm25_scores(query: str, paper: str) -> dict[str, float]:
    chunks = catalog()["by_paper"].get(paper) or []
    folded = " ".join(str(query or "").split())
    return _bm25_paper(chunks, bm25_query_tokens(folded, _lexicon()))


def order_enrich(paper: str, state: dict) -> list[str]:
    chunks = catalog()["by_paper"].get(paper) or []
    rank = {passage_id: index for index, passage_id in enumerate(state["order"])}
    ranked = sorted(chunks, key=lambda chunk: (rank.get(chunk["id"], 10**9), chunk["doc_index"]))
    return [chunk["id"] for chunk in ranked]


def order_maxsim(paper: str, maxsim: numpy.ndarray) -> list[str]:
    chunks = catalog()["by_paper"].get(paper) or []
    ranked = sorted(chunks, key=lambda chunk: (-float(maxsim[chunk["qrow"]]), chunk["doc_index"]))
    return [chunk["id"] for chunk in ranked]


def order_bm25(query: str, paper: str) -> list[str]:
    chunks = catalog()["by_paper"].get(paper) or []
    scores = bm25_scores(query, paper)
    ranked = sorted(chunks, key=lambda chunk: (-scores[chunk["id"]], chunk["doc_index"]))
    return [chunk["id"] for chunk in ranked]


def _chunk_score(chunk: dict, scores: dict[str, float], floor: float) -> float:
    if chunk["id"] in scores:
        return scores[chunk["id"]]
    return floor


def feature_rows(paper: str, scores: dict[str, float], maxsim: numpy.ndarray) -> tuple[list[str], numpy.ndarray]:
    chunks = catalog()["by_paper"].get(paper) or []
    if not chunks:
        return [], numpy.zeros((0, len(FEATURES)), dtype=numpy.float64)
    present = [scores[chunk["id"]] for chunk in chunks if chunk["id"] in scores]
    floor = (min(present) - 1.0) if present else 0.0
    top = min(chunks, key=lambda chunk: (-_chunk_score(chunk, scores, floor), chunk["doc_index"]))
    width = max(len(chunks) - 1, 1)
    matrix = numpy.zeros((len(chunks), len(FEATURES)), dtype=numpy.float64)
    section_at = {name: index for index, name in enumerate(_SECTIONS)}
    for row_index, chunk in enumerate(chunks):
        matrix[row_index, 0] = _chunk_score(chunk, scores, floor)
        matrix[row_index, 1] = float(maxsim[chunk["qrow"]])
        kind_at = {"table": 2, "caption": 3, "text": 4}[chunk["kind"]]
        matrix[row_index, kind_at] = 1.0
        matrix[row_index, 5 + section_at[chunk["section"]]] = 1.0
        matrix[row_index, 12] = abs(chunk["doc_index"] - top["doc_index"]) / width
    return [chunk["id"] for chunk in chunks], matrix


def zscore(matrix: numpy.ndarray) -> numpy.ndarray:
    if matrix.size == 0:
        return matrix
    center = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    flat = scale < 1e-8
    safe = numpy.where(flat, 1.0, scale)
    shifted = (matrix - center) / safe
    shifted[:, flat] = 0.0
    return shifted


def order_learned(paper: str, scores: dict[str, float], maxsim: numpy.ndarray, weights: numpy.ndarray) -> list[str]:
    ids, raw = feature_rows(paper, scores, maxsim)
    if not ids:
        return []
    scored = zscore(raw) @ weights
    chunks = catalog()["by_paper"][paper]
    order = sorted(range(len(ids)), key=lambda index: (-float(scored[index]), chunks[index]["doc_index"]))
    return [ids[index] for index in order]


def _config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = json.loads((_ROOT / "within.json").read_text(encoding="utf-8"))
    return _CONFIG


def order_in_paper(query: str, paper_sha256: str) -> list[str]:
    if not str(query or "").strip():
        return []
    paper = str(paper_sha256 or "")
    if paper not in catalog()["by_paper"]:
        return []
    config = _config()
    method = config["method"]
    if method == "bm25-notes":
        return order_bm25(query, paper)
    state = query_state(query)
    if method == "enrich-slack-v2":
        return order_enrich(paper, state)
    maxsim = maxsim_by_row(state["vector"])
    if method == "question-maxsim":
        return order_maxsim(paper, maxsim)
    if method == "learned":
        weights = numpy.asarray(config["weights"], dtype=numpy.float64)
        return order_learned(paper, state["scores"], maxsim, weights)
    raise RuntimeError(f"unknown_method {method}")


def chars_to_cover(order: list[str], spans: list[str]) -> int | None:
    rows = catalog()["by_id"]
    pending = list(spans)
    used = 0
    parts: list[str] = []
    for passage_id in order:
        parts.append(rows[passage_id]["text"])
        used += rows[passage_id]["length"]
        blob = "\n".join(parts)
        pending = [span for span in pending if span not in blob]
        if not pending:
            return used
    return None
