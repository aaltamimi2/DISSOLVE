"""P-2 lexical bridging curves. Query-time BM25 expansion. No gold seal."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import dense_d2, engine_e2e, research, text_chunk_metrics
from .gold_ensemble import CENSUS_V3_SHA256, file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

CURVES_R5LX_SCHEMA = "dissolve.text-chunk-curves.retrieval.r5lx.v1"
CURVES_R5LX_PATH = DEFAULT_OUT_DIR / "CURVES.retrieval.r5lx.v1.json"
RECALL_SPEC_V1_SHA256 = "2c66a709667cb332b7cbc0bc31610ef68edda03956b53a0583d790a0472c43fd"
RECALL_SPEC_V1_1_SHA256 = "849953d7a5be4ec5c55de062e3aa32d4b2260cfeeae2f414d45bd0ae7f5e36f1"
RECALL_SPEC_V1_2_SHA256 = "1807c6476772cef4e481e8072455a2758cd68706dda670eb879900a7217a49b0"
CURVES_V4_SHA256 = "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2"
V4_RETRIEVABLE = {"1": 147, "3": 188, "5": 201, "10": 209, "20": 220}
POISON_TOKEN = "zzzxqlexiconprobe"
POISON_ENTRY = {
    "token": POISON_TOKEN,
    "expansions": ["polyethylene"],
    "provenance": {
        "kind": "general_chemistry",
        "source": "constructed poisoned-entry control",
    },
}
_FORBIDDEN = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_NAMES = dense_d2._PROTECTED_NAMES | {
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.v4.png",
    "CURVES.retrieval.d3.json",
    "CURVES.retrieval.abstention.r1.json",
    "CURVES.retrieval.abstention.r3.json",
}
CONSTRUCTED_PROBE = "zzzxqlexiconprobe"


def _kmap(hits: Mapping[str, int]) -> dict[str, int]:
    return {str(k): int(hits[str(k)]) for k in text_chunk_metrics.RETRIEVAL_KS}


def _score_facts(
    index: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    ranker,
) -> dict[str, Any]:
    hits = {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS}
    ids_at: dict[str, set[str]] = {str(k): set() for k in text_chunk_metrics.RETRIEVAL_KS}
    rr_sum = 0.0
    for fact in facts:
        ranked = ranker(index, str(fact.get("query") or ""))
        needles = fact.get("needles") or {}
        retrievable, _precision = text_chunk_metrics.retrieval_at_ks(ranked, needles)
        fact_id = str(fact.get("fact_id") or "")
        first = None
        for index_number, chunk in enumerate(ranked[:20], 1):
            if text_chunk_metrics.contain_bound_fact(
                text_chunk_metrics._body(chunk), needles,
            ):
                first = index_number
                break
        if first is not None:
            rr_sum += 1.0 / float(first)
        for k in text_chunk_metrics.RETRIEVAL_KS:
            label = str(k)
            if retrievable.get(label):
                hits[label] += 1
                if fact_id:
                    ids_at[label].add(fact_id)
    n = len(facts)
    return {
        "n_retrievable_at_k": hits,
        "ids_at_k": {label: set(values) for label, values in ids_at.items()},
        "mrr_at_20": (rr_sum / n) if n else 0.0,
    }


def _arm_metrics(
    *,
    fire: dict[str, Any],
    refuse: dict[str, Any],
    n_fire: int,
    n_hold: int,
    n_chunks: int,
) -> dict[str, Any]:
    n_retr = fire["n_retrievable_at_k"]
    n_leaks = refuse["n_retrievable_at_k"]
    n_refuse = {label: n_hold - int(n_leaks[label]) for label in n_retr}
    recall = {
        label: text_chunk_metrics._v3_rate(n_retr[label], n_fire) for label in n_retr
    }
    return {
        "n_facts": n_fire,
        "n_held_out_facts": n_hold,
        "n_chunks": n_chunks,
        "n_retrievable_at_k": n_retr,
        "recall_at_k": recall,
        "recall_ci_at_k": {
            label: text_chunk_metrics.wilson_interval(n_retr[label], n_fire)
            for label in n_retr
        },
        "n_must_refuse_at_k": n_refuse,
        "must_refuse_at_k": {
            label: text_chunk_metrics._v3_rate(n_refuse[label], n_hold)
            for label in n_refuse
        },
        "n_leaks_at_k": n_leaks,
        "mrr_at_20": fire["mrr_at_20"],
    }


def _max_sparse_raw(index: Mapping[str, Any], probe: str) -> float:
    rows = research._search_index(index, probe, 50, "sparse")
    if not rows:
        return 0.0
    return max(float(row.get("sparse_raw_score") or 0.0) for row in rows)


def _ids_tg(values: Sequence[str], *, require_tg: bool) -> list[str]:
    raw = sorted({str(item) for item in values if item})
    if not require_tg:
        return raw
    if any(not item.startswith("tg-") for item in raw):
        raise TextGoldError("ids_not_tg", "Paired lists must be tg- ids.")
    return raw


def build_r5lx_artifact(
    *,
    gold: Mapping[str, Any],
    index: Mapping[str, Any],
    pins: Mapping[str, str],
    ranker=None,
    require_v4_ident: bool = True,
) -> dict[str, Any]:
    split = text_chunk_metrics.split_gold(gold)
    fire_facts = split["must_fire"]
    refuse_facts = split["must_refuse"]
    queries = [str(fact.get("query") or "") for fact in fire_facts + refuse_facts]
    used = ranker
    if used is None:
        used = dense_d2._cached_query_ranker(index, queries)
    n_fire = len(fire_facts)
    n_hold = len(refuse_facts)
    n_chunks = len(index.get("chunks") or [])
    committed = list(research.load_lexicon_entries())

    print("r5lx empty-lexicon arm", file=sys.stderr, flush=True)
    with research.bm25_lexicon([]):
        empty_fire = _score_facts(index, fire_facts, used)
        empty_refuse = _score_facts(index, refuse_facts, used)
        empty_probe = _max_sparse_raw(index, CONSTRUCTED_PROBE)
    print("r5lx expansion arm", file=sys.stderr, flush=True)
    with research.bm25_lexicon(committed):
        exp_fire = _score_facts(index, fire_facts, used)
        exp_refuse = _score_facts(index, refuse_facts, used)
    poisoned = list(committed) + [POISON_ENTRY]
    print("r5lx poisoned-entry arm", file=sys.stderr, flush=True)
    with research.bm25_lexicon(poisoned):
        poison_refuse = _score_facts(index, refuse_facts, used)
        poison_probe = _max_sparse_raw(index, CONSTRUCTED_PROBE)

    empty_retr = _kmap(empty_fire["n_retrievable_at_k"])
    if research._HYBRID_DENSE_WEIGHT != 0.55 or research._HYBRID_SPARSE_WEIGHT != 0.40:
        raise TextGoldError("weights_retuned", "Hybrid weights moved off 0.55/0.40.")
    if require_v4_ident and empty_retr != V4_RETRIEVABLE:
        raise TextGoldError("empty_lexicon_not_ident", "Empty-lexicon control is not IDENT to v4.")
    if require_v4_ident and n_fire != 228:
        raise TextGoldError("fire_count_moved", "Indexed fact count is not 228.")
    if require_v4_ident and n_hold != 29:
        raise TextGoldError("must_refuse_short", "Must-refuse is not 29/29.")
    for side in (empty_refuse, exp_refuse, poison_refuse):
        leaks = side["n_retrievable_at_k"]
        if any(int(leaks[str(k)]) > 0 for k in text_chunk_metrics.RETRIEVAL_KS):
            raise TextGoldError("refuse_leak", "A r5lx arm leaked on must-refuse.")

    empty_ids = set(empty_fire["ids_at_k"]["5"])
    exp_ids = set(exp_fire["ids_at_k"]["5"])
    fixed = _ids_tg(sorted(exp_ids - empty_ids), require_tg=require_v4_ident)
    broken = _ids_tg(sorted(empty_ids - exp_ids), require_tg=require_v4_ident)
    net = len(fixed) - len(broken)
    exp_at_5 = int(exp_fire["n_retrievable_at_k"]["5"])
    empty_at_5 = int(empty_fire["n_retrievable_at_k"]["5"])
    if exp_at_5 != empty_at_5 + net:
        raise TextGoldError("paired_count_mismatch", "Paired net does not match k=5 hit counts.")
    if not poison_probe > empty_probe:
        raise TextGoldError("poison_did_not_move", "Poisoned-entry control did not move sparse_raw.")
    if require_v4_ident and (net < 4 or len(broken) > 1):
        raise TextGoldError(
            "bars_missed",
            "Expansion arm missed pre-registered k=5 bars.",
            net=net,
            n_fixed=len(fixed),
            n_broken=len(broken),
            empty_at_5=empty_at_5,
            expansion_at_5=exp_at_5,
        )

    empty_metrics = _arm_metrics(
        fire=empty_fire, refuse=empty_refuse, n_fire=n_fire, n_hold=n_hold,
        n_chunks=n_chunks,
    )
    expansion_metrics = _arm_metrics(
        fire=exp_fire, refuse=exp_refuse, n_fire=n_fire, n_hold=n_hold,
        n_chunks=n_chunks,
    )
    poison_refuse_n = {
        label: n_hold - int(poison_refuse["n_retrievable_at_k"][label])
        for label in poison_refuse["n_retrievable_at_k"]
    }
    dense = index.get("dense") or {}
    artifact = {
        "schema": CURVES_R5LX_SCHEMA,
        "spec_sha256": dense_d2.DENSE_SPEC_SHA256,
        "recall_spec_sha256": RECALL_SPEC_V1_SHA256,
        "recall_spec_v1_1_sha256": RECALL_SPEC_V1_1_SHA256,
        "recall_spec_v1_2_sha256": RECALL_SPEC_V1_2_SHA256,
        "gold_sha256": pins["gold"],
        "census_sha256": pins["census"],
        "store_sha256": pins["store"],
        "curves_v4_sha256": pins["curves_v4"],
        "lexicon_sha256": pins["lexicon"],
        "ranker": "hybrid",
        "refuse_rule": "sparse_gated",
        "refuse_gate": "sparse_raw_score > 0",
        "hybrid_weights": {"dense": 0.55, "sparse": 0.40},
        "embedder": str(dense.get("model") or engine_e2e.MINILM_ID),
        "dim": int(dense.get("dim") or engine_e2e.EXPECTED_DIM),
        "retrieval_ks": list(text_chunk_metrics.RETRIEVAL_KS),
        "ci_method": "wilson_score",
        "ci_level": 0.95,
        "n_indexed_facts": n_fire,
        "n_held_out_facts": n_hold,
        "n_chunks": n_chunks,
        "arms": {
            "empty_lexicon": empty_metrics,
            "expansion": expansion_metrics,
            "poisoned_entry": {
                "n_held_out_facts": n_hold,
                "n_must_refuse_at_k": poison_refuse_n,
                "n_leaks_at_k": poison_refuse["n_retrievable_at_k"],
                "constructed_probe": True,
                "sparse_raw_empty": empty_probe,
                "sparse_raw_poisoned": poison_probe,
                "sparse_raw_moved": poison_probe > empty_probe,
            },
        },
        "fixed": fixed,
        "broken": broken,
        "net": net,
        "bars": {
            "k": 5,
            "net_min": 4,
            "broken_max": 1,
            "leaks_max": 0,
        },
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN)):
        raise TextGoldError("gold_quoted", "r5lx must not carry gold keys.")
    return artifact


def _pin_identity(
    *,
    gold_path: Path,
    census_path: Path,
    store_path: Path,
    v4_path: Path,
    lexicon_path: Path,
) -> dict[str, str]:
    pins = {
        "gold": file_sha256(gold_path),
        "census": file_sha256(census_path),
        "store": file_sha256(store_path),
        "curves_v4": file_sha256(v4_path),
        "lexicon": file_sha256(lexicon_path),
    }
    if pins["gold"] != text_chunk_metrics.GOLD_UNSEALED_SHA256:
        raise TextGoldError("gold_sha_moved", "Unsealed gold moved.")
    if pins["census"] != CENSUS_V3_SHA256:
        raise TextGoldError("census_sha_moved", "CENSUS.v3 moved.")
    if pins["store"] != engine_e2e.STORE_SHA256:
        raise TextGoldError("store_sha_moved", "T5 store moved.")
    if pins["curves_v4"] != CURVES_V4_SHA256:
        raise TextGoldError("v4_sha_moved", "v4 curves moved.")
    return pins


def emit_r5lx(
    *,
    gold_path: Path | None = None,
    census_path: Path | None = None,
    store_path: Path | None = None,
    v4_path: Path | None = None,
    index_path: Path | None = None,
    dest_dir: Path | None = None,
    ranker=None,
    index: Mapping[str, Any] | None = None,
    require_v4_ident: bool = True,
) -> dict[str, Any]:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    gold_file = Path(gold_path or text_chunk_metrics.GOLD_UNSEALED_PATH)
    census_file = Path(census_path or engine_e2e.CENSUS_PATH)
    store_file = Path(store_path or engine_e2e.STORE_PATH)
    v4_file = Path(v4_path or dense_d2.CURVES_V4_PATH)
    lexicon_file = research._LEXICON_PATH
    out_dir = Path(dest_dir or DEFAULT_OUT_DIR)
    curves_path = out_dir / "CURVES.retrieval.r5lx.v1.json"
    if curves_path.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "r5lx must not overwrite a sealed name.")
    r1 = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.r1.json"
    r3 = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.r3.json"
    r1_before = file_sha256(r1) if r1.is_file() else ""
    r3_before = file_sha256(r3) if r3.is_file() else ""
    v4_before = file_sha256(v4_file) if v4_file.is_file() else ""
    pins = _pin_identity(
        gold_path=gold_file,
        census_path=census_file,
        store_path=store_file,
        v4_path=v4_file,
        lexicon_path=lexicon_file,
    )
    gold = json.loads(gold_file.read_text())
    store = json.loads(store_file.read_text())
    loaded = dict(index) if index is not None else dense_d2.load_gzip_index(index_path)
    dense_d2._assert_index_matches_store(store, loaded)
    artifact = build_r5lx_artifact(
        gold=gold, index=loaded, pins=pins, ranker=ranker,
        require_v4_ident=require_v4_ident,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    curves_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    if r1.is_file() and file_sha256(r1) != r1_before:
        raise TextGoldError("r1_mutated", "Emit must not touch abstention r1.")
    if r3.is_file() and file_sha256(r3) != r3_before:
        raise TextGoldError("r3_mutated", "Emit must not touch abstention r3.")
    if v4_file.is_file() and file_sha256(v4_file) != v4_before:
        raise TextGoldError("v4_mutated", "Emit must not touch v4.")
    return {
        "curves_path": str(curves_path),
        "net": artifact["net"],
        "n_fixed": len(artifact["fixed"]),
        "n_broken": len(artifact["broken"]),
        "broken": artifact["broken"],
        "fixed": artifact["fixed"],
    }


def main(argv: list[str] | None = None) -> dict[str, Any]:
    del argv
    return emit_r5lx()


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, ensure_ascii=False))
