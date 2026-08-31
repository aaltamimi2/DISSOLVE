#!/usr/bin/env python3
"""Deterministic G-1 scaling battery and dilution dry-run.

This is a measurement instrument only.  It does not ingest papers and never
writes the sealed gold, store, index, curve, or abstention artifacts.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dissolve import research, text_chunk_metrics  # noqa: E402
from dissolve.text_gold import TextGoldError  # noqa: E402


AUDIT = Path.home() / "dissolve-v12-audit"
CORPUS = AUDIT / "corpus" / "text_chunking"
GOLD_PATH = CORPUS / "GOLD.text.v1.unsealed.json"
STORE_PATH = CORPUS / "CHUNKS.t5.indexed.unsealed.v1.json"
CENSUS_PATH = AUDIT / "corpus" / "CENSUS.v3.json"
DENSE_SPEC_PATH = AUDIT / "DENSE_RETRIEVAL_SPEC.v1.md"
CURVES_V4_PATH = CORPUS / "CURVES.retrieval.v4.json"
INDEX_PATH = CORPUS / "indexes" / "t5-indexed-unsealed.json.gz"
SCORER_PATH = SRC / "dissolve" / "text_chunk_metrics.py"
VALUE_GUARD_PATH = AUDIT / "instruments" / "gold_value_guard.py"

EXPECTED_PINS = {
    "gold_sha256": "1c6df27b08dde99c675a01ecc8f99dbfb8dbcee250f298b3e1e352a56816c7d8",
    "store_sha256": "91851dbf3b979450d087f86acca8c146205e0d0a8c41cc49c3aab1fc9cf73a40",
    "census_sha256": "b60d9791eb1bc56a8e417df48c22e3b22faa9f3a44bf97022ec58d96495792f7",
    "dense_spec_sha256": "20ccd8e054251a1f8f8b80b204a46bb201b4768d8c5d39b9188d0413ca7bcee6",
    "curves_v4_sha256": "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2",
    "dense_index_sha256": "bf0bb9e213401868bac45590f364bc831976765fc9b258357523a981d2fe14f1",
    "scorer_sha256": "d9edcf1d1fb3468217342dba759523cff46c21e6483d0c18c96dc298b4a16b59",
}
RECALL_SPEC_SHA256 = {
    "v1": "2c66a709667cb332b7cbc0bc31610ef68edda03956b53a0583d790a0472c43fd",
    "v1.1": "849953d7a5be4ec5c55de062e3aa32d4b2260cfeeae2f414d45bd0ae7f5e36f1",
    "v1.2": "1807c6476772cef4e481e8072455a2758cd68706dda670eb879900a7217a49b0",
    "v1.3": "7acbd065a99976c55abf6c9f7c9da0004e721ec420f035d5aac1495a7f65e2b0",
    "v1.4": "8e2677b564b848a032723d6fd9762e13a8f5da16990b02b5eb118a7db3584ddf",
}
RECALL_SPEC_PATHS = {
    version: AUDIT / f"RECALL_PROGRAM_SPEC.{version}.md"
    for version in RECALL_SPEC_SHA256
}
RETRIEVAL_KS = (1, 3, 5, 10, 20)
BASE_CHUNKS = 922
X2_ADDED = 922
X4_ADDED = 2_766
N_FACTS = 257
N_MUST_FIRE = 228
N_MUST_REFUSE = 29
DEFAULT_SEED = 20_260_830
CONTAMINANT_PREFIX = "7419fa5c"
FORBIDDEN_ARTIFACT_KEYS = {"needles", "query", "evidence_quote", "canonical_text"}
FROZEN_BARS = {
    "dilution_x2_added_chunks": 922,
    "dilution_x4_added_chunks": 2766,
    "dilution_x4_max_r5_drop_facts": 4,
    "growth_batch_papers": 10,
    "growth_max_r5_drop_facts_per_batch": 2,
    "growth_min_r5_recall": 0.9,
    "max_leaks_per_k": 0,
    "must_refuse_required": 29,
    "retrieval_ks": [1, 3, 5, 10, 20],
    "span_verification_max_positives": 0,
}
FROZEN_BARS_SHA256 = "1dd9af3a115a9c06b5b76ee794fa011c2892e93d428ee66d7223230d4920f403"
SCHEMA_DILUTION = "dissolve.recall-growth.dilution.v1"
SCHEMA_SCALE = "dissolve.recall-growth.scale.v1"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_DIGIT = re.compile(r"\d")
_ENTITY = re.compile(r"\b(?:[A-Z][A-Za-z-]{2,}|[A-Z]{2,}[A-Za-z0-9-]*)\b")
_WORD = re.compile(r"[A-Za-z0-9]+")


class GrowthGateError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha_value(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise GrowthGateError("json_load") from error


def _load_index(path: Path) -> dict[str, Any]:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                value = json.load(handle)
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise GrowthGateError("index_load") from error
    if not isinstance(value, dict):
        raise GrowthGateError("index_schema")
    return value


def _current_pins() -> dict[str, str]:
    paths = {
        "gold_sha256": GOLD_PATH,
        "store_sha256": STORE_PATH,
        "census_sha256": CENSUS_PATH,
        "dense_spec_sha256": DENSE_SPEC_PATH,
        "curves_v4_sha256": CURVES_V4_PATH,
        "dense_index_sha256": INDEX_PATH,
        "scorer_sha256": SCORER_PATH,
    }
    observed = {name: _sha_file(path) for name, path in paths.items()}
    if observed != EXPECTED_PINS:
        raise GrowthGateError("source_pin")
    observed_specs = {
        version: _sha_file(path) for version, path in RECALL_SPEC_PATHS.items()
    }
    if observed_specs != RECALL_SPEC_SHA256:
        raise GrowthGateError("recall_spec_pin")
    if not VALUE_GUARD_PATH.is_file():
        raise GrowthGateError("value_guard_missing")
    return {
        **observed,
        "gold_value_guard_sha256": _sha_file(VALUE_GUARD_PATH),
        "growth_gate_sha256": _sha_file(Path(__file__).resolve()),
    }


def _census_sets(census: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    indexed: set[str] = set()
    forbidden: set[str] = set()
    for row in census.get("papers") or []:
        sha = str(row.get("sha256") or "")
        status = str(row.get("status") or "")
        if not sha:
            continue
        if status == "indexed":
            indexed.add(sha)
        else:
            forbidden.add(sha)
    return indexed, forbidden


def validate_source_universe(
    store: Mapping[str, Any],
    index: Mapping[str, Any],
    census: Mapping[str, Any],
    *,
    permit_growth: bool = False,
) -> None:
    indexed, forbidden = _census_sets(census)
    store_set = {str(item) for item in store.get("indexed_paper_sha256") or []}
    if len(indexed) != 19 or store_set != indexed:
        raise GrowthGateError("source_set")
    if any(item.startswith(CONTAMINANT_PREFIX) for item in store_set):
        raise GrowthGateError("contaminant_source")
    store_chunk_set = {
        str(row.get("paper_sha256") or "") for row in store.get("chunks") or []
    }
    if store_chunk_set != indexed or store_chunk_set & forbidden:
        raise GrowthGateError("store_exclusion")
    index_doc_set = {
        str(row.get("sha256") or "") for row in index.get("documents") or []
    }
    if not indexed.issubset(index_doc_set):
        raise GrowthGateError("index_baseline_missing")
    if index_doc_set & forbidden:
        raise GrowthGateError("index_exclusion")
    if not permit_growth and index_doc_set != indexed:
        raise GrowthGateError("index_source_set")
    for chunk in index.get("chunks") or []:
        sha = str(chunk.get("paper_sha256") or "")
        if sha and (sha in forbidden or sha.startswith(CONTAMINANT_PREFIX)):
            raise GrowthGateError("chunk_exclusion")
    dense = index.get("dense") or {}
    chunk_ids = [str(row.get("chunk_id") or "") for row in index.get("chunks") or []]
    if (
        len(chunk_ids) != len(set(chunk_ids))
        or chunk_ids != [str(item) for item in dense.get("chunk_ids") or []]
        or len(chunk_ids) != len(dense.get("vectors") or [])
    ):
        raise GrowthGateError("dense_alignment")


def _sentences(body: str) -> list[str]:
    normalized = " ".join(str(body or "").split())
    pieces = [item.strip() for item in _SENTENCE_SPLIT.split(normalized) if item.strip()]
    useful = [item for item in pieces if len(item) >= 24]
    return useful or ([normalized] if normalized else [])


def _scrub(value: str) -> str:
    masked = _DIGIT.sub("#", value)
    masked = _ENTITY.sub("[ENTITY]", masked)
    return " ".join(masked.split())


def _aggressive_scrub(value: str) -> str:
    masked = _WORD.sub(lambda match: "x" * max(3, len(match.group(0))), value)
    return " ".join(masked.split()) or "[masked]"


def _pick(sequence: Sequence[Any], material: str) -> Any:
    if not sequence:
        raise GrowthGateError("distractor_source_empty")
    number = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
    return sequence[number % len(sequence)]


def _contains_any_fact(body: str, facts: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        text_chunk_metrics.contain_bound_fact(body, fact.get("needles") or {})
        for fact in facts
    )


def build_distractors(
    store: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    *,
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    by_paper: dict[str, list[dict[str, Any]]] = {}
    for raw in store.get("chunks") or []:
        row = dict(raw)
        paper = str(row.get("paper_sha256") or "")
        if paper:
            by_paper.setdefault(paper, []).append(row)
    papers = sorted(by_paper)
    if len(papers) != 19:
        raise GrowthGateError("distractor_paper_count")
    rng = random.Random(seed)
    shuffled = list(papers)
    rng.shuffle(shuffled)
    rows: list[dict[str, Any]] = []
    for ordinal in range(count):
        paper_a = shuffled[ordinal % len(shuffled)]
        offset = 1 + ((ordinal // len(shuffled)) % (len(shuffled) - 1))
        paper_b = shuffled[(ordinal + offset) % len(shuffled)]
        if paper_a == paper_b:
            raise GrowthGateError("cross_paper_shuffle")
        chunk_a = _pick(by_paper[paper_a], f"{seed}:{ordinal}:a")
        chunk_b = _pick(by_paper[paper_b], f"{seed}:{ordinal}:b")
        sentence_a = _pick(
            _sentences(str(chunk_a.get("body") or "")),
            f"{seed}:{ordinal}:sa",
        )
        sentence_b = _pick(
            _sentences(str(chunk_b.get("body") or "")),
            f"{seed}:{ordinal}:sb",
        )
        pair = [sentence_a, sentence_b]
        if int(hashlib.sha256(f"{seed}:{ordinal}:order".encode()).hexdigest(), 16) % 2:
            pair.reverse()
        body = _scrub(" ".join(pair))
        if _contains_any_fact(body, facts):
            body = _aggressive_scrub(body)
        if _contains_any_fact(body, facts):
            raise GrowthGateError("distractor_span")
        rows.append(
            {
                "chunk_id": f"dx-{seed:08d}-{ordinal + 1:05d}",
                "body": body,
                "text": body,
                "paper_sha256": "dx-synthetic",
                "title": "dilution-control",
                "source": "",
                "section": "",
                "section_origin": None,
                "token_estimate": max(1, math.ceil(len(body) / 4)),
            }
        )
    return rows


def verify_span_free(
    distractors: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    positives = 0
    for chunk in distractors:
        body = str(chunk.get("body") or chunk.get("text") or "")
        positives += sum(
            text_chunk_metrics.contain_bound_fact(body, fact.get("needles") or {})
            for fact in facts
        )
    return {
        "n_facts": len(facts),
        "n_distractors": len(distractors),
        "n_comparisons": len(facts) * len(distractors),
        "n_positives": positives,
    }


def _require_span_free(report: Mapping[str, Any]) -> None:
    if int(report.get("n_positives") or 0) != 0:
        raise GrowthGateError("span_positive")


def _embed_distractors(
    base_index: Mapping[str, Any], distractors: Sequence[Mapping[str, Any]]
) -> list[list[float]]:
    dense = base_index.get("dense") or {}
    model = str(dense.get("model") or "")
    if not model:
        raise GrowthGateError("embedder_missing")
    loaded, vectors = research._dense_vectors(
        [research.chunk_sparse_corpus(row) for row in distractors], model
    )
    if loaded != model or len(vectors) != len(distractors):
        raise GrowthGateError("distractor_embedding")
    dim = int(dense.get("dim") or 0)
    if dim < 1 or any(len(vector) != dim for vector in vectors):
        raise GrowthGateError("distractor_dimension")
    return vectors


def _augment_index(
    base: Mapping[str, Any],
    distractors: Sequence[Mapping[str, Any]],
    vectors: Sequence[Sequence[float]],
) -> dict[str, Any]:
    if len(distractors) != len(vectors):
        raise GrowthGateError("augment_alignment")
    out = dict(base)
    out["chunks"] = list(base.get("chunks") or []) + [dict(row) for row in distractors]
    dense = dict(base.get("dense") or {})
    dense["chunk_ids"] = list(dense.get("chunk_ids") or []) + [
        str(row["chunk_id"]) for row in distractors
    ]
    dense["vectors"] = list(dense.get("vectors") or []) + [list(row) for row in vectors]
    out["dense"] = dense
    return out


def _query_ranker(facts: Sequence[Mapping[str, Any]], model: str):
    queries = list(dict.fromkeys(str(fact.get("query") or "") for fact in facts))
    queries = [item for item in queries if item]
    loaded, vectors = research._dense_vectors(queries, model)
    if loaded != model or len(vectors) != len(queries):
        raise GrowthGateError("query_embedding")
    table = dict(zip(queries, vectors))

    def ranked(index: Mapping[str, Any], query: str) -> list[Mapping[str, Any]]:
        def cached(texts, model_name=None):
            if model_name and model_name != loaded:
                raise GrowthGateError("query_model")
            try:
                return loaded, [table[item] for item in texts]
            except KeyError as error:
                raise GrowthGateError("query_cache") from error

        previous = research._dense_vectors
        research._dense_vectors = cached
        try:
            result = research._search_index(dict(index), query, 20, "hybrid")
        finally:
            research._dense_vectors = previous
        by_id = {
            str(chunk.get("chunk_id") or ""): chunk for chunk in index.get("chunks") or []
        }
        return [
            by_id[str(row.get("chunk_id") or "")]
            for row in result
            if str(row.get("chunk_id") or "") in by_id
        ]

    return ranked


def _score_arm(
    index: Mapping[str, Any],
    split: Mapping[str, Any],
    ranker: Callable[[Mapping[str, Any], str], list[Mapping[str, Any]]],
) -> dict[str, Any]:
    fire_hits = {str(k): set() for k in RETRIEVAL_KS}
    leaks = {str(k): set() for k in RETRIEVAL_KS}
    reciprocal_sum = 0.0
    for side, target in ((split["must_fire"], fire_hits), (split["must_refuse"], leaks)):
        for fact in side:
            ranked = ranker(index, str(fact.get("query") or ""))
            relevant = [
                text_chunk_metrics.contain_bound_fact(
                    text_chunk_metrics._body(chunk), fact.get("needles") or {}
                )
                for chunk in ranked
            ]
            fact_id = str(fact.get("fact_id") or "")
            for k in RETRIEVAL_KS:
                if any(relevant[:k]):
                    target[str(k)].add(fact_id)
            if side is split["must_fire"]:
                first = next((i for i, hit in enumerate(relevant[:20], 1) if hit), None)
                reciprocal_sum += 0.0 if first is None else 1.0 / first
    n_fire = len(split["must_fire"])
    n_hold = len(split["must_refuse"])
    return {
        "n_must_fire": n_fire,
        "n_must_refuse": n_hold,
        "n_retrievable_at_k": {key: len(value) for key, value in fire_hits.items()},
        "recall_at_k": {
            key: round(len(value) / n_fire, 12) for key, value in fire_hits.items()
        },
        "mrr_at_20": round(reciprocal_sum / n_fire, 12),
        "n_must_refuse_at_k": {
            key: n_hold - len(value) for key, value in leaks.items()
        },
        "n_leaks_at_k": {key: len(value) for key, value in leaks.items()},
        "_fire_ids": fire_hits,
    }


def _paired(
    baseline: Mapping[str, set[str]], current: Mapping[str, set[str]]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in RETRIEVAL_KS:
        key = str(k)
        fixed = sorted(current[key] - baseline[key])
        broken = sorted(baseline[key] - current[key])
        out[key] = {
            "fixed_ids": fixed,
            "broken_ids": broken,
            "net": len(fixed) - len(broken),
        }
    return out


def _public_arm(
    *,
    name: str,
    score: dict[str, Any],
    baseline_ids: Mapping[str, set[str]],
    total_chunks: int,
    added_chunks: int,
    baseline_tokens: int,
    added_tokens: int,
    span_report: Mapping[str, Any],
) -> dict[str, Any]:
    fire_ids = score.pop("_fire_ids")
    return {
        "arm": name,
        "total_chunks": total_chunks,
        "added_distractor_chunks": added_chunks,
        "token_mass": {
            "baseline": baseline_tokens,
            "added": added_tokens,
            "total": baseline_tokens + added_tokens,
        },
        "span_verification": dict(span_report),
        "metrics": score,
        "paired_vs_baseline_at_k": _paired(baseline_ids, fire_ids),
    }


def _t5_v4_counts(curves: Mapping[str, Any]) -> dict[str, int]:
    for row in curves.get("series") or []:
        if row.get("strategy") == "T5" and row.get("bucket") == "all":
            return {
                str(k): int((row.get("n_retrievable_at_k") or {}).get(str(k), -1))
                for k in RETRIEVAL_KS
            }
    raise GrowthGateError("curves_row")


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("recall_gold_value_guard", VALUE_GUARD_PATH)
    if spec is None or spec.loader is None:
        raise GrowthGateError("value_guard_import")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _guard_artifact(artifact: Mapping[str, Any]) -> None:
    if text_chunk_metrics._contains_forbidden_keys(artifact, FORBIDDEN_ARTIFACT_KEYS):
        raise GrowthGateError("forbidden_key")
    guard = _load_guard_module()
    if int(guard.count_violations(artifact)) != 0:
        raise GrowthGateError("forbidden_value")


def _validate_common(artifact: Mapping[str, Any], *, verify_files: bool = True) -> None:
    if artifact.get("recall_spec_sha256") != RECALL_SPEC_SHA256:
        raise GrowthGateError("artifact_spec_pin")
    if artifact.get("bars") != FROZEN_BARS:
        raise GrowthGateError("artifact_bars")
    if artifact.get("bars_sha256") != FROZEN_BARS_SHA256:
        raise GrowthGateError("artifact_bars_pin")
    if _sha_value(FROZEN_BARS) != FROZEN_BARS_SHA256:
        raise GrowthGateError("compiled_bars_pin")
    pins = artifact.get("pins") or {}
    if any(pins.get(key) != value for key, value in EXPECTED_PINS.items()):
        raise GrowthGateError("artifact_source_pin")
    if verify_files:
        current = _current_pins()
        if any(pins.get(key) != value for key, value in current.items()):
            raise GrowthGateError("artifact_live_pin")
    _guard_artifact(artifact)


def validate_dilution_artifact(
    artifact: Mapping[str, Any], *, verify_files: bool = True
) -> None:
    _validate_common(artifact, verify_files=verify_files)
    if artifact.get("schema") != SCHEMA_DILUTION:
        raise GrowthGateError("artifact_schema")
    arms = {str(row.get("arm") or ""): row for row in artifact.get("arms") or []}
    if set(arms) != {"baseline", "x2", "x4"}:
        raise GrowthGateError("artifact_arms")
    expected_added = {"baseline": 0, "x2": X2_ADDED, "x4": X4_ADDED}
    expected_comparisons = {
        "baseline": 0,
        "x2": N_FACTS * X2_ADDED,
        "x4": N_FACTS * X4_ADDED,
    }
    curves = _load_json(CURVES_V4_PATH)
    baseline_counts = _t5_v4_counts(curves)
    for name, row in arms.items():
        added = expected_added[name]
        if (
            int(row.get("added_distractor_chunks") or 0) != added
            or int(row.get("total_chunks") or 0) != BASE_CHUNKS + added
        ):
            raise GrowthGateError("artifact_mass")
        span = row.get("span_verification") or {}
        if (
            int(span.get("n_distractors") or 0) != added
            or int(span.get("n_comparisons") or 0) != expected_comparisons[name]
            or int(span.get("n_positives") or 0) != 0
        ):
            raise GrowthGateError("artifact_span")
        metrics = row.get("metrics") or {}
        if int(metrics.get("n_must_fire") or 0) != N_MUST_FIRE:
            raise GrowthGateError("artifact_fire_count")
        if int(metrics.get("n_must_refuse") or 0) != N_MUST_REFUSE:
            raise GrowthGateError("artifact_refuse_count")
        for k in RETRIEVAL_KS:
            key = str(k)
            if (
                int((metrics.get("n_must_refuse_at_k") or {}).get(key, -1))
                != N_MUST_REFUSE
                or int((metrics.get("n_leaks_at_k") or {}).get(key, -1)) != 0
            ):
                raise GrowthGateError("artifact_leak")
            paired = (row.get("paired_vs_baseline_at_k") or {}).get(key) or {}
            fixed = list(paired.get("fixed_ids") or [])
            broken = list(paired.get("broken_ids") or [])
            if (
                any(not str(item).startswith("tg-") for item in fixed + broken)
                or set(fixed) & set(broken)
                or int(paired.get("net") or 0) != len(fixed) - len(broken)
            ):
                raise GrowthGateError("artifact_pairing")
    if (arms["baseline"].get("metrics") or {}).get("n_retrievable_at_k") != baseline_counts:
        raise GrowthGateError("artifact_baseline")
    baseline_r5 = baseline_counts["5"]
    x4_r5 = int(
        ((arms["x4"].get("metrics") or {}).get("n_retrievable_at_k") or {}).get("5", -1)
    )
    x4_drop = baseline_r5 - x4_r5
    span_pass = all(
        int((row.get("span_verification") or {}).get("n_positives") or 0) == 0
        for row in arms.values()
    )
    refusal_pass = all(
        int(value) == N_MUST_REFUSE
        for row in arms.values()
        for value in ((row.get("metrics") or {}).get("n_must_refuse_at_k") or {}).values()
    ) and all(
        int(value) == 0
        for row in arms.values()
        for value in ((row.get("metrics") or {}).get("n_leaks_at_k") or {}).values()
    )
    x4_pass = x4_drop <= FROZEN_BARS["dilution_x4_max_r5_drop_facts"]
    expected_acceptance = {
        "span_free_pass": span_pass,
        "must_refuse_pass": refusal_pass,
        "x4_r5_drop_facts": x4_drop,
        "x4_r5_drop_max_facts": FROZEN_BARS["dilution_x4_max_r5_drop_facts"],
        "x4_r5_drop_pass": x4_pass,
        "overall_pass": span_pass and refusal_pass and x4_pass,
    }
    if artifact.get("acceptance") != expected_acceptance:
        raise GrowthGateError("artifact_acceptance")


def validate_scale_artifact(
    artifact: Mapping[str, Any], *, verify_files: bool = True
) -> None:
    _validate_common(artifact, verify_files=verify_files)
    if artifact.get("schema") != SCHEMA_SCALE:
        raise GrowthGateError("scale_schema")
    batch = int(artifact.get("batch") or 0)
    if artifact.get("artifact_name") != _scale_name(batch):
        raise GrowthGateError("scale_name")
    if int(artifact.get("papers_added") or 0) != FROZEN_BARS["growth_batch_papers"]:
        raise GrowthGateError("scale_batch_mass")
    if int(artifact.get("n_indexed_papers") or 0) != 19 + (
        batch * FROZEN_BARS["growth_batch_papers"]
    ):
        raise GrowthGateError("scale_cumulative_mass")
    metrics = artifact.get("metrics") or {}
    if (
        int(metrics.get("n_must_fire") or 0) != N_MUST_FIRE
        or int(metrics.get("n_must_refuse") or 0) != N_MUST_REFUSE
    ):
        raise GrowthGateError("scale_fact_counts")
    refusal_pass = all(
        int((metrics.get("n_must_refuse_at_k") or {}).get(str(k), -1))
        == N_MUST_REFUSE
        and int((metrics.get("n_leaks_at_k") or {}).get(str(k), -1)) == 0
        for k in RETRIEVAL_KS
    )
    retrieved = artifact.get("retrieved_ids_at_k") or {}
    paired = artifact.get("paired_vs_previous_at_k") or {}
    for k in RETRIEVAL_KS:
        key = str(k)
        ids = list(retrieved.get(key) or [])
        if len(ids) != len(set(ids)) or any(not str(item).startswith("tg-") for item in ids):
            raise GrowthGateError("scale_retrieved_ids")
        cell = paired.get(key) or {}
        fixed = list(cell.get("fixed_ids") or [])
        broken = list(cell.get("broken_ids") or [])
        if (
            any(not str(item).startswith("tg-") for item in fixed + broken)
            or set(fixed) & set(broken)
            or int(cell.get("net") or 0) != len(fixed) - len(broken)
        ):
            raise GrowthGateError("scale_pairing")
    r5 = int((metrics.get("n_retrievable_at_k") or {}).get("5", -1))
    r5_drop = int(artifact.get("r5_drop_from_previous") or 0)
    floor_pass = r5 / N_MUST_FIRE >= FROZEN_BARS["growth_min_r5_recall"]
    drop_pass = r5_drop <= FROZEN_BARS["growth_max_r5_drop_facts_per_batch"]
    expected_acceptance = {
        "must_refuse_pass": refusal_pass,
        "r5_floor": FROZEN_BARS["growth_min_r5_recall"],
        "r5_floor_pass": floor_pass,
        "r5_drop_facts": r5_drop,
        "r5_drop_max_facts": FROZEN_BARS["growth_max_r5_drop_facts_per_batch"],
        "r5_drop_pass": drop_pass,
        "overall_pass": refusal_pass and floor_pass and drop_pass,
    }
    if artifact.get("acceptance") != expected_acceptance:
        raise GrowthGateError("scale_acceptance")


def build_dilution_artifact(*, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    pins = _current_pins()
    gold = _load_json(GOLD_PATH)
    store = _load_json(STORE_PATH)
    census = _load_json(CENSUS_PATH)
    curves = _load_json(CURVES_V4_PATH)
    base_index = _load_index(INDEX_PATH)
    split = text_chunk_metrics.split_gold(gold)
    facts = list(split["facts"])
    if (
        len(facts) != N_FACTS
        or len(split["must_fire"]) != N_MUST_FIRE
        or len(split["must_refuse"]) != N_MUST_REFUSE
    ):
        raise GrowthGateError("gold_counts")
    if len(store.get("chunks") or []) != BASE_CHUNKS:
        raise GrowthGateError("store_mass")
    validate_source_universe(store, base_index, census)
    distractors = build_distractors(store, facts, count=X4_ADDED, seed=seed)
    x2_distractors = distractors[:X2_ADDED]
    span_x2 = verify_span_free(x2_distractors, facts)
    span_x4 = verify_span_free(distractors, facts)
    _require_span_free(span_x2)
    _require_span_free(span_x4)
    vectors = _embed_distractors(base_index, distractors)
    x2_index = _augment_index(base_index, x2_distractors, vectors[:X2_ADDED])
    x4_index = _augment_index(base_index, distractors, vectors)
    model = str((base_index.get("dense") or {}).get("model") or "")
    ranker = _query_ranker(facts, model)
    baseline_score = _score_arm(base_index, split, ranker)
    baseline_ids = baseline_score["_fire_ids"]
    x2_score = _score_arm(x2_index, split, ranker)
    x4_score = _score_arm(x4_index, split, ranker)
    baseline_tokens = sum(
        int(row.get("token_estimate") or max(1, math.ceil(len(str(row.get("text") or "")) / 4)))
        for row in base_index.get("chunks") or []
    )
    x2_tokens = sum(int(row["token_estimate"]) for row in x2_distractors)
    x4_tokens = sum(int(row["token_estimate"]) for row in distractors)
    zero_span = {
        "n_facts": N_FACTS,
        "n_distractors": 0,
        "n_comparisons": 0,
        "n_positives": 0,
    }
    arms = [
        _public_arm(
            name="baseline",
            score=baseline_score,
            baseline_ids=baseline_ids,
            total_chunks=BASE_CHUNKS,
            added_chunks=0,
            baseline_tokens=baseline_tokens,
            added_tokens=0,
            span_report=zero_span,
        ),
        _public_arm(
            name="x2",
            score=x2_score,
            baseline_ids=baseline_ids,
            total_chunks=BASE_CHUNKS + X2_ADDED,
            added_chunks=X2_ADDED,
            baseline_tokens=baseline_tokens,
            added_tokens=x2_tokens,
            span_report=span_x2,
        ),
        _public_arm(
            name="x4",
            score=x4_score,
            baseline_ids=baseline_ids,
            total_chunks=BASE_CHUNKS + X4_ADDED,
            added_chunks=X4_ADDED,
            baseline_tokens=baseline_tokens,
            added_tokens=x4_tokens,
            span_report=span_x4,
        ),
    ]
    by_arm = {row["arm"]: row for row in arms}
    baseline_r5 = int(by_arm["baseline"]["metrics"]["n_retrievable_at_k"]["5"])
    x4_r5 = int(by_arm["x4"]["metrics"]["n_retrievable_at_k"]["5"])
    x4_drop = baseline_r5 - x4_r5
    refusal_pass = all(
        int(value) == N_MUST_REFUSE
        for row in arms
        for value in row["metrics"]["n_must_refuse_at_k"].values()
    ) and all(
        int(value) == 0
        for row in arms
        for value in row["metrics"]["n_leaks_at_k"].values()
    )
    x4_pass = x4_drop <= FROZEN_BARS["dilution_x4_max_r5_drop_facts"]
    artifact = {
        "schema": SCHEMA_DILUTION,
        "artifact_name": "GROWTH.dilution.v1.json",
        "recall_spec_sha256": dict(RECALL_SPEC_SHA256),
        "pins": pins,
        "bars": copy.deepcopy(FROZEN_BARS),
        "bars_sha256": FROZEN_BARS_SHA256,
        "ranker": "hybrid_sparse_gated",
        "retrieval_ks": list(RETRIEVAL_KS),
        "generator": {
            "seed": seed,
            "algorithm": "cross_paper_sentence_shuffle_digit_entity_mask_v1",
            "namespace": "dx-",
            "source_papers": 19,
            "source_chunks": BASE_CHUNKS,
            "source_set_sha256": _sha_value(sorted(store.get("indexed_paper_sha256") or [])),
            "distractor_digest": _sha_value(
                [{"chunk_id": row["chunk_id"], "body": row["body"]} for row in distractors]
            ),
        },
        "span_scorer": {
            "name": "contain_bound_fact",
            "semantics": "body_casefold_all_nonempty_leaves",
            "sha256": EXPECTED_PINS["scorer_sha256"],
        },
        "arms": arms,
        "baseline_source_counts": _t5_v4_counts(curves),
        "acceptance": {
            "span_free_pass": True,
            "must_refuse_pass": refusal_pass,
            "x4_r5_drop_facts": x4_drop,
            "x4_r5_drop_max_facts": FROZEN_BARS["dilution_x4_max_r5_drop_facts"],
            "x4_r5_drop_pass": x4_pass,
            "overall_pass": refusal_pass and x4_pass,
        },
    }
    validate_dilution_artifact(artifact)
    return artifact


def _write_artifact(artifact: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _scale_name(batch: int) -> str:
    if batch < 1 or batch > 999:
        raise GrowthGateError("batch_number")
    return f"GROWTH.scale.b{batch:03d}.v1.json"


def build_scale_artifact(
    *,
    index_path: Path,
    batch: int,
    papers_added: int,
    previous_path: Path | None,
) -> dict[str, Any]:
    if papers_added != FROZEN_BARS["growth_batch_papers"]:
        raise GrowthGateError("growth_batch_mass")
    pins = _current_pins()
    gold = _load_json(GOLD_PATH)
    store = _load_json(STORE_PATH)
    census = _load_json(CENSUS_PATH)
    index = _load_index(index_path)
    validate_source_universe(store, index, census, permit_growth=True)
    n_indexed_papers = len(index.get("documents") or [])
    if n_indexed_papers != 19 + (batch * FROZEN_BARS["growth_batch_papers"]):
        raise GrowthGateError("growth_index_paper_mass")
    split = text_chunk_metrics.split_gold(gold)
    facts = list(split["facts"])
    model = str((index.get("dense") or {}).get("model") or "")
    score = _score_arm(index, split, _query_ranker(facts, model))
    fire_ids = score.pop("_fire_ids")
    if batch == 1:
        base_index = _load_index(INDEX_PATH)
        base_model = str((base_index.get("dense") or {}).get("model") or "")
        baseline_score = _score_arm(
            base_index, split, _query_ranker(facts, base_model)
        )
        reference_ids = baseline_score.pop("_fire_ids")
        reference_r5 = int(baseline_score["n_retrievable_at_k"]["5"])
        previous_digest = None
    else:
        if previous_path is None or previous_path.name != _scale_name(batch - 1):
            raise GrowthGateError("previous_batch")
        previous = _load_json(previous_path)
        validate_scale_artifact(previous)
        reference_ids = {
            str(k): set((previous.get("retrieved_ids_at_k") or {}).get(str(k)) or [])
            for k in RETRIEVAL_KS
        }
        reference_r5 = int(
            ((previous.get("metrics") or {}).get("n_retrievable_at_k") or {}).get("5", -1)
        )
        previous_digest = _sha_file(previous_path)
    current_r5 = int(score["n_retrievable_at_k"]["5"])
    refusal_pass = all(
        int(score["n_must_refuse_at_k"][str(k)]) == N_MUST_REFUSE
        and int(score["n_leaks_at_k"][str(k)]) == 0
        for k in RETRIEVAL_KS
    )
    floor_pass = current_r5 / N_MUST_FIRE >= FROZEN_BARS["growth_min_r5_recall"]
    drop = reference_r5 - current_r5
    drop_pass = drop <= FROZEN_BARS["growth_max_r5_drop_facts_per_batch"]
    token_mass = sum(
        int(
            row.get("token_estimate")
            or max(1, math.ceil(len(str(row.get("text") or row.get("body") or "")) / 4))
        )
        for row in index.get("chunks") or []
    )
    artifact = {
        "schema": SCHEMA_SCALE,
        "artifact_name": _scale_name(batch),
        "batch": batch,
        "papers_added": papers_added,
        "n_indexed_papers": n_indexed_papers,
        "n_chunks": len(index.get("chunks") or []),
        "token_mass": token_mass,
        "previous_artifact_sha256": previous_digest,
        "recall_spec_sha256": dict(RECALL_SPEC_SHA256),
        "pins": {**pins, "measured_index_sha256": _sha_file(index_path)},
        "bars": copy.deepcopy(FROZEN_BARS),
        "bars_sha256": FROZEN_BARS_SHA256,
        "retrieval_ks": list(RETRIEVAL_KS),
        "metrics": score,
        "retrieved_ids_at_k": {key: sorted(value) for key, value in fire_ids.items()},
        "paired_vs_previous_at_k": _paired(reference_ids, fire_ids),
        "r5_drop_from_previous": drop,
        "acceptance": {
            "must_refuse_pass": refusal_pass,
            "r5_floor": FROZEN_BARS["growth_min_r5_recall"],
            "r5_floor_pass": floor_pass,
            "r5_drop_facts": drop,
            "r5_drop_max_facts": FROZEN_BARS["growth_max_r5_drop_facts_per_batch"],
            "r5_drop_pass": drop_pass,
            "overall_pass": refusal_pass and floor_pass and drop_pass,
        },
    }
    validate_scale_artifact(artifact)
    return artifact


def _one_span_distractor(gold: Mapping[str, Any]) -> dict[str, Any]:
    facts = list(text_chunk_metrics.split_gold(gold)["facts"])
    for fact in facts:
        values = text_chunk_metrics.nonempty_needles(fact.get("needles") or {})
        body = " ".join(values.values())
        if body and sum(
            text_chunk_metrics.contain_bound_fact(body, row.get("needles") or {})
            for row in facts
        ) == 1:
            return {"chunk_id": "dx-mf-00001", "body": body}
    raise GrowthGateError("single_span_control")


def _bad_control(case: int, good: Mapping[str, Any], context: Mapping[str, Any]) -> Any:
    if case == 1:
        index = copy.deepcopy(context["index"])
        forbidden_sha = sorted(context["forbidden"])[0]
        index["documents"].append({"sha256": forbidden_sha, "document_id": "mf"})
        return index
    if case == 2:
        report = verify_span_free(
            [_one_span_distractor(context["gold"])], context["facts"]
        )
        if int(report.get("n_positives") or 0) != 1:
            raise GrowthGateError("single_span_count")
        return report
    if case == 3:
        artifact = copy.deepcopy(good)
        artifact["arms"][1]["added_distractor_chunks"] += 1
        return artifact
    if case == 4:
        artifact = copy.deepcopy(good)
        artifact["bars"]["dilution_x4_max_r5_drop_facts"] += 1
        return artifact
    if case == 5:
        artifact = copy.deepcopy(good)
        artifact["recall_spec_sha256"]["v1.4"] = "0" * 64
        return artifact
    if case == 6:
        guard = _load_guard_module()
        corpus = guard.ProtectedCorpus(guard.load_gold())
        value = next(item for item in sorted(corpus.leaves) if len(item) >= 8)
        return [value]
    raise GrowthGateError("control_number")


def _validate_control(case: int, value: Any, context: Mapping[str, Any]) -> None:
    if case == 1:
        validate_source_universe(context["store"], value, context["census"])
    elif case == 2:
        _require_span_free(value)
    elif case in {3, 4, 5}:
        validate_dilution_artifact(value, verify_files=False)
    elif case == 6:
        guard = _load_guard_module()
        if int(guard.count_violations(value)) != 0:
            raise GrowthGateError("forbidden_value")
    else:
        raise GrowthGateError("control_number")


def self_test(artifact_path: Path) -> int:
    try:
        good = _load_json(artifact_path)
        validate_dilution_artifact(good)
        gold = _load_json(GOLD_PATH)
        store = _load_json(STORE_PATH)
        census = _load_json(CENSUS_PATH)
        index = _load_index(INDEX_PATH)
        _indexed, forbidden = _census_sets(census)
        context = {
            "gold": gold,
            "facts": list(text_chunk_metrics.split_gold(gold)["facts"]),
            "store": store,
            "census": census,
            "index": index,
            "forbidden": forbidden,
        }
        controls = [_bad_control(case, good, context) for case in range(1, 7)]

        def exercise(validator) -> list[bool]:
            observed: list[bool] = []
            for case, bad in enumerate(controls, 1):
                try:
                    validator(case, bad, context)
                except (GrowthGateError, TextGoldError):
                    observed.append(True)
                else:
                    observed.append(False)
            return observed

        fired = exercise(_validate_control)
        for case in range(1, 7):
            value = fired[case - 1]
            print(f"m{case}={int(value)} {'PASS' if value else 'FAIL'}")
        accept_all_fired = exercise(lambda _case, _value, _context: None)
        m7 = all(fired) and not any(accept_all_fired)
        print(f"m7={int(m7)} {'PASS' if m7 else 'FAIL'}")
        passed = all(fired) and m7
        print("SELFTEST=" + ("PASS" if passed else "FAIL"))
        return 0 if passed else 1
    except (GrowthGateError, TextGoldError):
        print("SELFTEST=ERROR")
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth_gate.py")
    sub = parser.add_subparsers(dest="command", required=True)
    dilution = sub.add_parser("dilution")
    dilution.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "GROWTH.dilution.v1.json",
    )
    dilution.add_argument("--seed", type=int, default=DEFAULT_SEED)
    validate = sub.add_parser("validate")
    validate.add_argument("artifact", type=Path)
    test = sub.add_parser("self-test")
    test.add_argument(
        "artifact",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parent / "GROWTH.dilution.v1.json",
    )
    scale = sub.add_parser("scale")
    scale.add_argument("--index", type=Path, required=True)
    scale.add_argument("--batch", type=int, required=True)
    scale.add_argument("--papers-added", type=int, required=True)
    scale.add_argument("--previous", type=Path)
    scale.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "dilution":
            artifact = build_dilution_artifact(seed=args.seed)
            if args.output.name != "GROWTH.dilution.v1.json":
                raise GrowthGateError("artifact_name")
            _write_artifact(artifact, args.output)
            print(f"artifact_sha256={_sha_file(args.output)}")
            return 0 if (artifact.get("acceptance") or {}).get("overall_pass") else 2
        if args.command == "validate":
            artifact = _load_json(args.artifact)
            if artifact.get("schema") == SCHEMA_DILUTION:
                validate_dilution_artifact(artifact)
            elif artifact.get("schema") == SCHEMA_SCALE:
                validate_scale_artifact(artifact)
            else:
                raise GrowthGateError("artifact_schema")
            print("VALIDATE=PASS")
            return 0
        if args.command == "self-test":
            return self_test(args.artifact)
        if args.command == "scale":
            name = _scale_name(args.batch)
            artifact = build_scale_artifact(
                index_path=args.index,
                batch=args.batch,
                papers_added=args.papers_added,
                previous_path=args.previous,
            )
            output = args.output_dir / name
            _write_artifact(artifact, output)
            print(f"artifact_sha256={_sha_file(output)}")
            return 0 if (artifact.get("acceptance") or {}).get("overall_pass") else 2
    except (GrowthGateError, TextGoldError) as error:
        code = error.code if isinstance(error, GrowthGateError) else "text_gold"
        print(f"GATE=FAIL code={code}")
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
