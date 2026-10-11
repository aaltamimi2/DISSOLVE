"""Served hybrid search (r3): BM25, lexicon, int8 BGE query encoder, z-score fusion.

The arithmetic follows dissolve.research at commit 2c072815 with
DISSOLVE_PAIR_RERANK=off and DISSOLVE_BGE_ONNX_INT8 set. Rankings match
observe_search_ranking (the abstention gate is not applied). One intra-op
thread is fixed so a harness process cannot fan out.
"""

from __future__ import annotations

import os

import hashlib
import json
import math
import re
import statistics
import threading
from pathlib import Path

import numpy

LEXICON_PATH = Path(__file__).resolve().parent / "data" / "lexicon.v1.json"
ONNX_PATH = Path(os.environ["DISSOLVE_BGE_ONNX_INT8"]).expanduser()
TOKENIZER_PATH = Path(__import__("huggingface_hub").hf_hub_download(
    "BAAI/bge-base-en-v1.5", "tokenizer.json", revision="a5beb1e3e68b9ab74eb54cfd186867f64f240e1a",
    local_files_only=True))
ONNX_SHA256 = "520f3e21f4be0c8683013523608b22396648827e5d3e7b721149c9fdb8d8068d"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
DENSE_W = 0.55
SPARSE_W = 0.40
DIM = 768
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "what", "which", "with",
})
_EXPANSION_STOP = frozenset({
    "low", "high", "linear", "density", "vinyl", "alcohol", "solvent",
    "methyl", "ethyl", "ether", "acid", "targeted", "recovery",
    "precipitation", "poly", "ethylene", "common", "name",
})
_LEXICON_KINDS = frozenset({
    "general_chemistry", "iupac_nomenclature", "engine_data_table",
})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def tokens(value: str) -> list[str]:
    return [
        token for token in _TOKEN_RE.findall(str(value or "").casefold())
        if len(token) > 1 and token not in _STOPWORDS
    ]


def load_lexicon(path: Path | None = None) -> list[dict]:
    target = Path(path) if path is not None else LEXICON_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    entries = []
    for item in payload.get("entries") or []:
        token = str(item.get("token") or "").strip().casefold()
        expansions = [
            str(phrase).strip() for phrase in (item.get("expansions") or [])
            if str(phrase).strip()
        ]
        provenance = item.get("provenance") or {}
        kind = str(provenance.get("kind") or "").strip()
        if not token or not expansions or kind not in _LEXICON_KINDS:
            raise ValueError("lexicon_entry_incomplete")
        entries.append({"token": token, "expansions": expansions})
    if not entries:
        raise ValueError("lexicon_empty")
    return entries


def bm25_query_tokens(query: str, lexicon: list[dict]) -> list[str]:
    """Query-side tokens. Document tokenization is tokens() and is not expanded."""
    query_tokens = list(tokens(query))
    present = set(query_tokens)
    extra: list[str] = []
    seen = set(present)
    folded_query = str(query or "").casefold()
    for entry in lexicon:
        key = str(entry.get("token") or "").strip().casefold()
        if not key:
            continue
        expansion_lists = [tokens(str(phrase)) for phrase in (entry.get("expansions") or [])]
        hit_key = key in present
        hit_expansion = False
        for phrase, phrase_tokens in zip(entry.get("expansions") or [], expansion_lists):
            phrase_folded = str(phrase).casefold()
            if phrase_folded and phrase_folded in folded_query:
                hit_expansion = True
                break
            if phrase_tokens and len(phrase_tokens) >= 2 and set(phrase_tokens) <= present:
                hit_expansion = True
                break
        if not hit_key and not hit_expansion:
            continue
        if hit_key:
            for phrase_tokens in expansion_lists:
                for item in phrase_tokens:
                    if item in seen:
                        continue
                    if len(item) < 6 or item in _EXPANSION_STOP:
                        continue
                    extra.append(item)
                    seen.add(item)
        if hit_expansion and key not in seen:
            extra.append(key)
            seen.add(key)
    return query_tokens + extra


def population_zscores(values: list[float]) -> list[float]:
    sequence = [float(value) for value in values]
    if len(sequence) < 2:
        return [0.0] * len(sequence)
    variance = statistics.pvariance(sequence)
    if variance <= 0.0:
        return [0.0] * len(sequence)
    mean = statistics.fmean(sequence)
    scale = math.sqrt(variance)
    return [(value - mean) / scale for value in sequence]


class QueryEncoder:
    """BGE-base query encoder: tokenizer truncation 512, CLS, L2 norm, round to 8 decimals."""

    def __init__(self, model_path: Path, tokenizer_path: Path):
        import onnxruntime
        from tokenizers import Tokenizer

        if sha256_file(model_path) != ONNX_SHA256:
            raise RuntimeError("bge_onnx_model_mismatch")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"],
        )
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(512)
        self.tokenizer.enable_padding()
        self.lock = threading.Lock()

    def encode_query(self, query: str) -> list[float]:
        text = QUERY_INSTRUCTION + query
        batch = self.tokenizer.encode_batch([text])
        feed = {
            "input_ids": numpy.array([item.ids for item in batch], dtype=numpy.int64),
            "attention_mask": numpy.array([item.attention_mask for item in batch], dtype=numpy.int64),
        }
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = numpy.array([item.type_ids for item in batch], dtype=numpy.int64)
        with self.lock:
            cls = self.session.run(None, feed)[0][:, 0]
        row = (cls / numpy.linalg.norm(cls, axis=1, keepdims=True)).tolist()[0]
        return [round(float(value), 8) for value in row]


class Searcher:
    """Read-only r3 index. search() packs whole passages until the budget stops."""

    def __init__(self, index_dir: Path):
        index_dir = Path(index_dir)
        meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
        if meta.get("mode") != "r3" or int(meta.get("dim") or 0) != DIM:
            raise ValueError("index_mode")
        if meta.get("query_instruction") != QUERY_INSTRUCTION:
            raise ValueError("index_instruction")
        self.ids = json.loads((index_dir / "ids.json").read_text(encoding="utf-8"))
        self.titles = json.loads((index_dir / "titles.json").read_text(encoding="utf-8"))
        self.text_len = numpy.load(index_dir / "text_len.npy").tolist()
        doc_len = [int(value) for value in numpy.load(index_dir / "doc_len.npy").tolist()]
        if not (len(self.ids) == len(self.titles) == len(self.text_len) == len(doc_len)):
            raise ValueError("index_alignment")
        if len(self.ids) != len(set(self.ids)):
            raise ValueError("duplicate_id")
        self.n = len(self.ids)
        self.len_of = {pid: int(n) for pid, n in zip(self.ids, self.text_len)}
        vocab = (index_dir / "vocab.txt").read_text(encoding="utf-8").splitlines()
        self.vocab_index = {token: i for i, token in enumerate(vocab)}
        pack = numpy.load(index_dir / "postings.npz")
        self.offsets = pack["offsets"]
        self.post_docs = pack["docs"]
        self.post_tfs = pack["tfs"]
        if int(self.offsets.shape[0]) != len(vocab) + 1:
            raise ValueError("postings_vocab")
        self.vectors = numpy.load(index_dir / "vectors.npy")
        if self.vectors.shape != (self.n, DIM):
            raise ValueError("vector_shape")
        if self.vectors.dtype != numpy.float64:
            raise ValueError("vector_dtype")
        self.vectors.setflags(write=False)
        self.last_query_vector = None
        average = statistics.fmean(doc_len) if doc_len else 1.0
        scale = max(average, 1.0)
        self.denom = [1.2 * (0.25 + 0.75 * length / scale) for length in doc_len]
        self.idf = []
        for term in range(len(vocab)):
            df = int(self.offsets[term + 1] - self.offsets[term])
            self.idf.append(math.log(1.0 + (self.n - df + 0.5) / (df + 0.5)))
        lexicon_sha = sha256_file(LEXICON_PATH)
        if lexicon_sha != meta.get("lexicon_sha256"):
            raise ValueError("lexicon_mismatch")
        tokenizer_sha = sha256_file(TOKENIZER_PATH)
        if tokenizer_sha != meta.get("tokenizer_sha256"):
            raise ValueError("tokenizer_mismatch")
        self.lexicon = load_lexicon(LEXICON_PATH)
        self.encoder = None
        self._checked_dot = False

    @classmethod
    def open(cls, index_dir: Path) -> "Searcher":
        return cls(index_dir)

    def _encoder(self) -> QueryEncoder:
        if self.encoder is None:
            self.encoder = QueryEncoder(ONNX_PATH, TOKENIZER_PATH)
        return self.encoder

    def bm25(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.n
        if not query_tokens:
            return scores
        vocab_index = self.vocab_index
        offsets = self.offsets
        docs = self.post_docs
        tfs = self.post_tfs
        idf = self.idf
        denom = self.denom
        for token in set(query_tokens):
            term = vocab_index.get(token)
            if term is None:
                continue
            weight = idf[term]
            start = int(offsets[term])
            end = int(offsets[term + 1])
            for cursor in range(start, end):
                doc = int(docs[cursor])
                count = int(tfs[cursor])
                scores[doc] += weight * count * 2.2 / (count + denom[doc])
        return scores

    def sparse_scores(self, query: str) -> list[float]:
        expanded = bm25_query_tokens(query, self.lexicon)
        raw_tokens = tokens(query)
        raw_scores = self.bm25(raw_tokens)
        if expanded == raw_tokens:
            return raw_scores
        expanded_scores = self.bm25(expanded)
        raw_max = max(raw_scores, default=0.0)
        fused = []
        for raw, expanded_score in zip(raw_scores, expanded_scores):
            if raw > 0:
                fused.append(raw)
            elif expanded_score > 0:
                fused.append(min(expanded_score, raw_max) if raw_max > 0 else expanded_score)
            else:
                fused.append(0.0)
        return fused

    def _dots(self, query_vector: list[float], eligible: list[int]) -> list[float]:
        """Left-to-right float64 sum, the same association as sum(a * b for a, b in zip(...))."""
        sub = self.vectors[numpy.asarray(eligible, dtype=numpy.int64)]
        acc = numpy.zeros(sub.shape[0], dtype=numpy.float64)
        query_arr = numpy.asarray(query_vector, dtype=numpy.float64)
        for dim in range(sub.shape[1]):
            acc += sub[:, dim] * query_arr[dim]
        return acc.tolist()

    def _python_dot(self, query_vector: list[float], row_index: int) -> float:
        score = 0.0
        row = self.vectors[row_index]
        for left, right in zip(query_vector, row):
            score += left * float(right)
        return score

    def rank_rows(self, query: str) -> list[tuple[float, str, str]]:
        fused = self.sparse_scores(query)
        eligible = [i for i, raw in enumerate(fused) if raw > 0.0]
        if not eligible:
            self.last_query_vector = None
            return []
        query_vector = self._encoder().encode_query(query)
        self.last_query_vector = query_vector
        if not self._checked_dot:
            fast = self._dots(query_vector, [eligible[0]])[0]
            slow = self._python_dot(query_vector, eligible[0])
            if fast != slow:
                raise RuntimeError("dot_mismatch")
            self._checked_dot = True
        dots = self._dots(query_vector, eligible)
        clipped = [0.0 if value < 0.0 else value for value in dots]
        z_dense = population_zscores(clipped)
        z_sparse = population_zscores([fused[i] for i in eligible])
        ranked = []
        for index, dense_z, sparse_z in zip(eligible, z_dense, z_sparse):
            score = DENSE_W * dense_z + SPARSE_W * sparse_z
            ranked.append((score, self.titles[index], self.ids[index]))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        return ranked

    def rank_ids(self, query: str, k: int | None = 50) -> list[str]:
        rows = self.rank_rows(query)
        if k is None:
            return [item[2] for item in rows]
        return [item[2] for item in rows[:k]]

    def search(self, query: str, budget_chars: int) -> list[str]:
        if not str(query or "").strip():
            return []
        budget = int(budget_chars)
        if budget <= 0:
            return []
        chosen = []
        used = 0
        for passage_id in self.rank_ids(query, k=None):
            length = self.len_of[passage_id]
            if used + length <= budget:
                chosen.append(passage_id)
                used += length
            else:
                break
        return chosen
