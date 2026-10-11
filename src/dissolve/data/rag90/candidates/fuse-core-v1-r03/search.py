"""Fusion search. Reads fusion.json beside this file. No logs and no writes."""

from __future__ import annotations

import os

import json
import sys
from collections import Counter
from pathlib import Path

import numpy

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "vendor"))

from rag90coverage.aspects import decompose
from rag90coverage.pack import coverage_order, pack_until_fit, pool_from_ranks, rank_relevance
from rag90search.r3 import Searcher

_INDEX = (Path(os.environ["DISSOLVE_RAG90_ROOT"]) / "candidates/fuse-core-v1-r03/index")
_ABBREV = _ROOT / "vendor" / "rag90coverage" / "data" / "abbrev.v1.json"
_PASSAGES = _ROOT / "passages.jsonl"
_FUSION = _ROOT / "fusion.json"
_KEEP = 200
_KINDS = ("paragraph", "pack", "list_item", "caption", "table", "heading", "formula", "footnote")
_MAX_LEN = 256

_ENGINE = None
_EXPANSIONS = None
_META = None
_CFG = None
_CE = None


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Searcher.open(_INDEX)
    return _ENGINE


def _expansions():
    global _EXPANSIONS
    if _EXPANSIONS is None:
        payload = json.loads(_ABBREV.read_text(encoding="utf-8"))
        _EXPANSIONS = {row["abbr"]: row["expansion"] for row in payload["entries"]}
    return _EXPANSIONS


def _meta():
    global _META
    if _META is None:
        meta = {}
        for line in _PASSAGES.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            meta[row["id"]] = row
        _META = meta
    return _META


def _config():
    global _CFG
    if _CFG is None:
        payload = json.loads(_FUSION.read_text(encoding="utf-8"))
        beta = numpy.asarray(payload["beta"], dtype=numpy.float64)
        width = len(payload["features"])
        if beta.shape != (width + 1,):
            raise RuntimeError("beta_shape")
        head = int(payload["head"])
        if head == 0 and width != 16:
            raise RuntimeError("feature_width")
        if head > 0 and width != 18:
            raise RuntimeError("feature_width")
        payload["beta_arr"] = beta
        payload["head"] = head
        _CFG = payload
    return _CFG


class _CrossEncoder:
    def __init__(self, model_path: Path, tokenizer_path: Path):
        import onnxruntime
        from tokenizers import Tokenizer

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"],
        )
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=_MAX_LEN, strategy="longest_first")
        self.tokenizer.enable_padding(direction="right", pad_id=0, pad_type_id=0, pad_token="[PAD]")

    def score(self, query: str, passages: list[str]) -> list[float]:
        logits = []
        for passage in passages:
            encoded = self.tokenizer.encode(query, passage)
            feed = {
                "input_ids": numpy.asarray([encoded.ids], dtype=numpy.int64),
                "attention_mask": numpy.asarray([encoded.attention_mask], dtype=numpy.int64),
                "token_type_ids": numpy.asarray([encoded.type_ids], dtype=numpy.int64),
            }
            logits.append(float(self.session.run(None, feed)[0].reshape(-1)[0]))
        return logits


def _ce():
    global _CE
    if _CE is None:
        cfg = _config()
        model = Path(cfg["onnx"]["path"])
        tokenizer = Path(cfg["tokenizer"]["path"])
        if _sha256(model) != cfg["onnx"]["sha256"]:
            raise RuntimeError("ce_model_mismatch")
        if _sha256(tokenizer) != cfg["tokenizer"]["sha256"]:
            raise RuntimeError("ce_tokenizer_mismatch")
        _CE = _CrossEncoder(model, tokenizer)
    return _CE


def _zscore(values: numpy.ndarray) -> numpy.ndarray:
    if values.size == 0:
        return values
    scale = float(values.std())
    if scale < 1e-8:
        return numpy.zeros_like(values)
    return (values - float(values.mean())) / scale


def _features(searcher, meta, query, expansion, cache):
    def ranked(text):
        key = " ".join(text.split())
        if key not in cache:
            rows = searcher.rank_rows(key)
            cache[key] = [item[2] for item in rows[:_KEEP]]
            vector = searcher.last_query_vector
            cache[key + "\0vec"] = None if vector is None else numpy.asarray(vector, dtype=numpy.float64)
            cache[key + "\0sparse"] = numpy.asarray(searcher.sparse_scores(key), dtype=numpy.float64)
        return cache[key]

    folded = " ".join(query.split())
    full = ranked(folded)
    aspect_ids = []
    for item in decompose(folded, expansion):
        text = " ".join(item["text"].split())
        aspect_ids.append(full if text == folded else ranked(text))
    pool = pool_from_ranks(full, aspect_ids)
    if not pool:
        return [], numpy.zeros((0, 16)), full, aspect_ids
    id_to_row = {pid: index for index, pid in enumerate(searcher.ids)}
    prior = rank_relevance(full, _KEEP)
    aspect_rels = [rank_relevance(ids, 40) for ids in aspect_ids] or [prior]
    qvec = cache[folded + "\0vec"]
    sparse = cache[folded + "\0sparse"]
    top10 = full[:10]
    paper_counts = Counter(meta[pid]["paper_sha256"] for pid in top10 if pid in meta)
    top_n = max(len(top10), 1)
    dense_raw = []
    sparse_raw = []
    for pid in pool:
        row_index = id_to_row[pid]
        dense_raw.append(0.0 if qvec is None else float(searcher.vectors[row_index] @ qvec))
        sparse_raw.append(float(sparse[row_index]))
    dense_z = _zscore(numpy.asarray(dense_raw, dtype=numpy.float64))
    sparse_z = _zscore(numpy.asarray(sparse_raw, dtype=numpy.float64))
    rows_feat = []
    for index, pid in enumerate(pool):
        info = meta[pid]
        best_aspect = 0.0
        hits = 0
        for rel in aspect_rels:
            value = float(rel.get(pid, 0.0))
            if value > 0:
                hits += 1
            if value > best_aspect:
                best_aspect = value
        paper_frac = paper_counts.get(info["paper_sha256"], 0) / top_n
        neighbour = 0.0
        for other in (info.get("prev"), info.get("next")):
            if other:
                neighbour = max(neighbour, float(prior.get(other, 0.0)))
        kind = [1.0 if info.get("kind") == name else 0.0 for name in _KINDS]
        rows_feat.append([
            float(prior.get(pid, 0.0)),
            best_aspect,
            hits / max(len(aspect_rels), 1),
            float(sparse_z[index]),
            float(dense_z[index]),
            paper_frac,
            neighbour,
            float(numpy.log1p(len(info["text"]))),
            *kind,
        ])
    return pool, numpy.asarray(rows_feat, dtype=numpy.float64), full, aspect_ids


def _ce_columns(pool, ce_ids, ce_logits) -> numpy.ndarray:
    logits = numpy.asarray(ce_logits, dtype=numpy.float64)
    scored = {}
    if logits.size:
        scale = float(logits.std())
        if scale < 1e-8:
            scored = {pid: 0.0 for pid in ce_ids}
        else:
            center = float(logits.mean())
            scored = {pid: (float(value) - center) / scale for pid, value in zip(ce_ids, logits)}
    columns = numpy.zeros((len(pool), 2), dtype=numpy.float64)
    for index, pid in enumerate(pool):
        if pid in scored:
            columns[index, 0] = scored[pid]
            columns[index, 1] = 1.0
    return columns


def _probabilities(beta, features):
    if features.size == 0:
        return numpy.zeros(0)
    design = numpy.concatenate([numpy.ones((features.shape[0], 1)), features], axis=1)
    linear = design @ beta
    return 1.0 / (1.0 + numpy.exp(-numpy.clip(linear, -30, 30)))


def _pack_scored(pool, scores, lengths, budget):
    order = sorted(range(len(pool)), key=lambda index: (-float(scores[index]), pool[index]))
    chosen = []
    used = 0
    for index in order:
        pid = pool[index]
        length = int(lengths[pid])
        if used + length <= budget:
            chosen.append(pid)
            used += length
    return chosen


def _preference_rank(pool, scores):
    order = sorted(range(len(pool)), key=lambda index: (-float(scores[index]), pool[index]))
    return [pool[index] for index in order]


def _preference_prior(pool, scores, full, aspect_ids):
    prior = {pid: float(score) for pid, score in zip(pool, scores)}
    aspect_rel = [rank_relevance(ids, 40) for ids in aspect_ids] or [rank_relevance(full, 40)]
    unit = {pid: 1 for pid in pool}
    ordered = coverage_order(pool, unit, max(len(pool), 1), aspect_rel, prior, 1.0, 1.0)
    seen = set(ordered)
    ordered.extend(pid for pid in _preference_rank(pool, scores) if pid not in seen)
    return ordered


def _core_then_list(full, preference, lengths, budget, protect_rank):
    budget = int(budget)
    if budget <= 0:
        return []
    limit = None if int(protect_rank) == 0 else int(protect_rank)
    core = []
    used = 0
    for pid in full:
        if limit is not None and len(core) >= limit:
            break
        length = int(lengths[pid])
        if used + length > budget:
            break
        core.append(pid)
        used += length
    seen = set(core)
    chosen = list(core)
    for pid in preference:
        if pid in seen:
            continue
        length = int(lengths[pid])
        if used + length <= budget:
            chosen.append(pid)
            seen.add(pid)
            used += length
    if limit is None:
        return chosen
    for pid in full:
        if pid in seen:
            continue
        length = int(lengths[pid])
        if used + length <= budget:
            chosen.append(pid)
            seen.add(pid)
            used += length
        else:
            break
    return chosen


def _ce_tail(full, ce_ids, ce_logits, lengths, budget, protect_rank):
    score = {pid: float(value) for pid, value in zip(ce_ids, ce_logits)}
    head_ids = [pid for pid in full[: len(ce_ids)] if pid in score]
    limit = int(protect_rank)
    core = []
    used = 0
    if limit > 0:
        for pid in full:
            if len(core) >= limit:
                break
            length = int(lengths[pid])
            if used + length > budget:
                break
            core.append(pid)
            used += length
    order = list(core)
    middle = [pid for pid in head_ids if pid not in set(core)]
    middle.sort(key=lambda pid: (-score[pid], pid))
    order.extend(middle)
    seen = set(order)
    for pid in full:
        if pid not in seen:
            order.append(pid)
    return pack_until_fit(order, lengths, budget)


def search(query: str, budget_chars: int) -> list[str]:
    if not str(query or "").strip():
        return []
    budget = int(budget_chars)
    if budget <= 0:
        return []
    folded = " ".join(str(query).split())
    cfg = _config()
    meta = _meta()
    pool, feats, full, aspect_ids = _features(_engine(), meta, folded, _expansions(), {})
    lengths = {pid: len(meta[pid]["text"]) for pid in meta}
    ce_ids = []
    ce_logits = []
    head = int(cfg["head"])
    if head > 0:
        ce_ids = list(full[:head])
        ce_logits = _ce().score(folded, [meta[pid]["text"] for pid in ce_ids])
        if feats.size:
            feats = numpy.concatenate([feats, _ce_columns(pool, ce_ids, ce_logits)], axis=1)
    elif feats.size == 0:
        feats = numpy.zeros((0, 16))
    scores = _probabilities(cfg["beta_arr"], feats)
    pack = cfg["pack"]
    protect = int(cfg["protect_rank"])
    if pack == "rank":
        return _pack_scored(pool, scores, lengths, budget)
    if pack == "prior":
        aspect_rel = [rank_relevance(ids, 40) for ids in aspect_ids] or [rank_relevance(full, 40)]
        prior = {pid: float(score) for pid, score in zip(pool, scores)}
        return coverage_order(pool, lengths, budget, aspect_rel, prior, 1.0, 1.0)
    if pack == "core_rank":
        return _core_then_list(full, _preference_rank(pool, scores), lengths, budget, protect)
    if pack == "core_prior":
        return _core_then_list(
            full, _preference_prior(pool, scores, full, aspect_ids), lengths, budget, protect,
        )
    if pack == "ce_tail":
        return _ce_tail(full, ce_ids, ce_logits, lengths, budget, protect)
    raise RuntimeError("pack")
