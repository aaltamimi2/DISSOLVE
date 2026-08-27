"""G-3 derived pairs and hybrid vs one-entity-hop score. Walk depth frozen at 1."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import (
    abstention_a3,
    dense_d2,
    t5_corpus_graph,
    t5_graph_extract,
    t5_graph_lookup,
    text_chunk_metrics,
)
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = t5_graph_extract.SPEC_SHA256
GRAPH_SHA256 = t5_graph_extract.GRAPH_SHA256
ENTITY_HOP_DEPTH = 1
CURVES_NAME = "CURVES.graph-rag.v1.json"
CURVES_PATH = DEFAULT_OUT_DIR / CURVES_NAME
_FORBIDDEN = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
Ranker = Callable[[Mapping[str, Any], str], list[Mapping[str, Any]]]


def _refuse_gold_v2() -> None:
    t5_graph_lookup._refuse_gold_v2()


def _refuse_url(value: str) -> None:
    t5_graph_lookup._refuse_url(value)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _refuse_overlay_dest(dest: Path) -> None:
    if dest == t5_corpus_graph.GRAPH_PATH.resolve():
        raise TextGoldError("persist_graph_refused", "G-3 does not score persist GRAPH as overlay.")


def _curves_path(dest: Path) -> Path:
    if dest == t5_graph_extract.OVERLAY_PATH.resolve():
        return CURVES_PATH
    return dest.with_name(CURVES_NAME)


def _chunk_rows(store: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    by_paper: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in store.get("chunks") or []:
        if not isinstance(row, Mapping):
            continue
        paper = str(row.get("paper_sha256") or "")
        if paper:
            by_paper[paper].append(row)
    return by_paper


def _join_chunk(
    by_paper: Mapping[str, list[Mapping[str, Any]]],
    fact: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    paper = str(fact.get("paper_sha256") or "")
    try:
        start = int(fact["char_start"])
        end = int(fact["char_end"])
    except (KeyError, TypeError, ValueError):
        return None
    hits = [
        row
        for row in by_paper.get(paper, [])
        if int(row["char_start"]) <= start and end <= int(row["char_end"])
    ]
    if len(hits) != 1:
        return None
    return hits[0]


def _mention_index(graph: Mapping[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    chunk_entities: dict[str, set[str]] = defaultdict(set)
    entity_chunks: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges") or []:
        if not isinstance(edge, Mapping):
            continue
        if str(edge.get("edge_type") or "") != "mentions":
            continue
        payload = edge.get("payload")
        if not isinstance(payload, Mapping):
            continue
        chunk_id = str(payload.get("chunk_id") or "")
        key = str(payload.get("canonical_key") or "")
        if not chunk_id or not key:
            continue
        chunk_entities[chunk_id].add(key)
        entity_chunks[key].add(chunk_id)
    return chunk_entities, entity_chunks


def one_entity_hop(
    seed_ids: Sequence[str],
    *,
    chunk_entities: Mapping[str, set[str]],
    entity_chunks: Mapping[str, set[str]],
    depth: int = ENTITY_HOP_DEPTH,
) -> list[str]:
    if depth != 1:
        raise TextGoldError("walk_depth", "G-3 walk depth is frozen at 1.")
    seeds = [str(item) for item in seed_ids if item]
    if not seeds:
        return []
    entities: set[str] = set()
    for chunk_id in seeds:
        entities |= set(chunk_entities.get(chunk_id) or ())
    extra: list[str] = []
    seen = set(seeds)
    for key in sorted(entities):
        for chunk_id in sorted(entity_chunks.get(key) or ()):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            extra.append(chunk_id)
    return list(seeds) + extra


def _derive_pairs_internal(
    *,
    dest_path: Path,
    gold_file: Path,
    store_file: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    graph = _load_json(dest_path)
    gold = _load_json(gold_file)
    store = _load_json(store_file)
    by_paper = _chunk_rows(store)
    chunk_entities, _entity_chunks = _mention_index(graph)
    split = text_chunk_metrics.split_gold(gold)
    joined: list[dict[str, Any]] = []
    n_miss = 0
    n_zero = 0
    for fact in split["must_fire"]:
        row = _join_chunk(by_paper, fact)
        if row is None:
            n_miss += 1
            continue
        chunk_id = str(row["chunk_id"])
        entities = set(chunk_entities.get(chunk_id) or ())
        if not entities:
            n_zero += 1
        joined.append({
            "chunk_id": chunk_id,
            "paper_sha256": str(fact.get("paper_sha256") or ""),
            "char_start": int(fact["char_start"]),
            "char_end": int(fact["char_end"]),
            "entities": entities,
            "prompt_fact": fact,
        })
    for fact in split["must_refuse"]:
        if _join_chunk(by_paper, fact) is None:
            n_miss += 1
    pairs: list[dict[str, Any]] = []
    n_cross = 0
    n_same = 0
    for index, left in enumerate(joined):
        for right in joined[index + 1:]:
            if left["chunk_id"] == right["chunk_id"]:
                continue
            if not (left["entities"] & right["entities"]):
                continue
            ordered = sorted(
                (left, right),
                key=lambda item: (
                    str(item["paper_sha256"]),
                    int(item["char_start"]),
                    int(item["char_end"]),
                ),
            )
            cross = str(left["paper_sha256"]) != str(right["paper_sha256"])
            if cross:
                n_cross += 1
            else:
                n_same += 1
            pairs.append({
                "chunk_ids": (ordered[0]["chunk_id"], ordered[1]["chunk_id"]),
                "cross_paper": cross,
                "prompt_fact": ordered[0]["prompt_fact"],
            })
    payload = {
        "n_pairs": len(pairs),
        "n_cross_paper_pairs": n_cross,
        "n_same_paper_pairs": n_same,
        "n_facts_with_zero_entities": n_zero,
        "n_join_miss": n_miss,
        "n_joined": len(joined),
        "spec_sha256": SPEC_SHA256,
    }
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN)):
        raise TextGoldError("gold_quoted", "Derived-pair return must not carry gold identifiers.")
    return payload, pairs


def derive_t5_multihop_pairs(
    *,
    dest: str | Path,
    gold_path: str | Path | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Join gold coordinates to overlay identities. Counts only. No quote-class keys."""
    _refuse_gold_v2()
    dest_path = Path(dest).expanduser().resolve()
    _refuse_url(str(dest_path))
    _refuse_overlay_dest(dest_path)
    gold_file = (
        Path(gold_path).expanduser().resolve()
        if gold_path is not None
        else t5_corpus_graph.GOLD_PATH.resolve()
    )
    store_file = (
        Path(store_path).expanduser().resolve()
        if store_path is not None
        else t5_corpus_graph.STORE_PATH.resolve()
    )
    payload, _pairs = _derive_pairs_internal(
        dest_path=dest_path, gold_file=gold_file, store_file=store_file,
    )
    return payload


def _served_index(gzip_path: Path) -> dict[str, Any]:
    """Gzip chunks plus persist INDEX.t5 floor when the gzip has no abstention block."""
    index = dense_d2.load_gzip_index(gzip_path)
    if isinstance(index.get("abstention"), Mapping) and index["abstention"].get("floor") is not None:
        return index
    manifest = t5_corpus_graph.MANIFEST_PATH
    if not manifest.is_file():
        return index
    block = json.loads(manifest.read_text(encoding="utf-8")).get("abstention")
    if not isinstance(block, Mapping) or block.get("floor") is None:
        return index
    out = dict(index)
    out["abstention"] = {"floor": float(block["floor"])}
    return out


def _chunk_map(index: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["chunk_id"]): row
        for row in (index.get("chunks") or [])
        if isinstance(row, Mapping) and row.get("chunk_id")
    }


def _union_chunks(
    ranked: Sequence[Mapping[str, Any]],
    k: int,
    *,
    chunk_entities: Mapping[str, set[str]],
    entity_chunks: Mapping[str, set[str]],
    by_id: Mapping[str, Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    seeds = [str(row["chunk_id"]) for row in ranked[:k] if row.get("chunk_id")]
    if not seeds:
        return []
    union_ids = one_entity_hop(
        seeds,
        chunk_entities=chunk_entities,
        entity_chunks=entity_chunks,
        depth=ENTITY_HOP_DEPTH,
    )
    ranked_by_id = {
        str(row["chunk_id"]): row for row in ranked if row.get("chunk_id")
    }
    out: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for chunk_id in union_ids:
        if chunk_id in seen:
            continue
        row = ranked_by_id.get(chunk_id) or by_id.get(chunk_id)
        if row is None:
            continue
        seen.add(chunk_id)
        out.append(row)
    return out


def _truncate_union_by_rank(
    union: Sequence[Mapping[str, Any]],
    ranked: Sequence[Mapping[str, Any]],
    k: int,
) -> list[Mapping[str, Any]]:
    """Keep at most k union members, ordered by hybrid rank. Extras sort last."""
    pos: dict[str, int] = {}
    for index, row in enumerate(ranked):
        chunk_id = str(row.get("chunk_id") or "")
        if chunk_id and chunk_id not in pos:
            pos[chunk_id] = index
    ordered = sorted(
        union,
        key=lambda row: (pos.get(str(row.get("chunk_id") or ""), 10**9), str(row.get("chunk_id") or "")),
    )
    return list(ordered[:k])


def _size_summary(sizes: Sequence[int]) -> dict[str, Any]:
    if not sizes:
        return {"mean": None, "median": None, "max": None, "n": 0}
    return {
        "mean": float(statistics.fmean(sizes)),
        "median": float(statistics.median(sizes)),
        "max": int(max(sizes)),
        "n": len(sizes),
    }


def _verdict(left_ci: Mapping[str, Any], right_ci: Mapping[str, Any], delta: float) -> str:
    if text_chunk_metrics.intervals_overlap(left_ci, right_ci):
        return "TIED"
    return "UP" if delta > 0 else "DOWN"


def _contest(versus: Mapping[str, Mapping[str, Any]], ks: Sequence[int], n_pairs: int) -> str:
    if n_pairs == 0:
        return "NO_PAIRS"
    verdicts = [str(versus[str(k)]["verdict"]) for k in ks]
    if all(item == "TIED" for item in verdicts):
        return "TIED"
    if versus["5"]["verdict"] == "TIED" and versus["10"]["verdict"] == "TIED":
        return "TIED"
    if all(item == "UP" for item in (versus["5"]["verdict"], versus["10"]["verdict"])):
        return "UP"
    if all(item == "DOWN" for item in (versus["5"]["verdict"], versus["10"]["verdict"])):
        return "DOWN"
    return "MIXED"


def _budget_finding(matched: str, hybrid_at_mean: str) -> str:
    if matched == "NO_PAIRS":
        return "NO_PAIRS"
    wins = {"UP"}
    loses = {"TIED", "DOWN", "MIXED"}
    if matched in wins and hybrid_at_mean in wins:
        return "graph_wins_both_matched_budgets"
    if matched in loses and hybrid_at_mean in loses:
        return "effect_was_budget"
    return f"split:matched={matched}:hybrid_at_mean={hybrid_at_mean}"


def score_t5_graph_rag(
    *,
    dest: str | Path,
    gold_path: str | Path | None = None,
    index_path: str | Path | None = None,
    ranker: Ranker | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Hybrid vs matched-budget hop vs unbounded union. Walk depth frozen at 1."""
    _refuse_gold_v2()
    dest_path = Path(dest).expanduser().resolve()
    _refuse_url(str(dest_path))
    _refuse_overlay_dest(dest_path)
    gold_file = (
        Path(gold_path).expanduser().resolve()
        if gold_path is not None
        else t5_corpus_graph.GOLD_PATH.resolve()
    )
    store_file = (
        Path(store_path).expanduser().resolve()
        if store_path is not None
        else t5_corpus_graph.STORE_PATH.resolve()
    )
    derived, pairs = _derive_pairs_internal(
        dest_path=dest_path, gold_file=gold_file, store_file=store_file,
    )
    gold = _load_json(gold_file)
    split = text_chunk_metrics.split_gold(gold)
    graph = _load_json(dest_path)
    chunk_entities, entity_chunks = _mention_index(graph)
    gzip_path = (
        Path(index_path).expanduser().resolve()
        if index_path is not None
        else Path(json.loads(t5_corpus_graph.MANIFEST_PATH.read_text(encoding="utf-8"))["index_path"])
    )
    index = _served_index(gzip_path)
    by_id = _chunk_map(index)
    used = ranker
    if used is None:
        prompts = [
            str(item["prompt_fact"].get("query") or "")
            for item in pairs
        ]
        prompts.extend(str(fact.get("query") or "") for fact in split["must_refuse"])
        off = _load_json(abstention_a3.OFFDOMAIN_PATH)
        prompts.extend(str(row.get("query") or "") for row in (off.get("queries") or []))
        used = dense_d3_hybrid_ranker(index, prompts)

    ranked_by_query: dict[str, list[Mapping[str, Any]]] = {}

    def _rank(query: str) -> list[Mapping[str, Any]]:
        key = str(query or "")
        cached = ranked_by_query.get(key)
        if cached is None:
            cached = used(index, key)
            ranked_by_query[key] = cached
        return cached

    n_pairs = int(derived["n_pairs"])
    ks = list(text_chunk_metrics.RETRIEVAL_KS)
    hybrid_hits = {str(k): 0 for k in ks}
    matched_hits = {str(k): 0 for k in ks}
    unbounded_hits = {str(k): 0 for k in ks}
    hybrid_sizes = {str(k): [] for k in ks}
    matched_sizes = {str(k): [] for k in ks}
    unbounded_sizes = {str(k): [] for k in ks}
    matched_differs = {str(k): 0 for k in ks}
    if n_pairs:
        for item in pairs:
            need = set(item["chunk_ids"])
            prompt = str(item["prompt_fact"].get("query") or "")
            ranked = _rank(prompt)
            for k in ks:
                label = str(k)
                hybrid_rows = list(ranked[:k])
                hybrid_ids = {
                    str(row["chunk_id"]) for row in hybrid_rows if row.get("chunk_id")
                }
                hybrid_sizes[label].append(len(hybrid_ids))
                if need <= hybrid_ids:
                    hybrid_hits[label] += 1
                unbounded = _union_chunks(
                    ranked,
                    k,
                    chunk_entities=chunk_entities,
                    entity_chunks=entity_chunks,
                    by_id=by_id,
                )
                unbounded_ids = {str(row["chunk_id"]) for row in unbounded}
                unbounded_sizes[label].append(len(unbounded_ids))
                if need <= unbounded_ids:
                    unbounded_hits[label] += 1
                matched = _truncate_union_by_rank(unbounded, ranked, k)
                matched_ids = {str(row["chunk_id"]) for row in matched}
                matched_sizes[label].append(len(matched_ids))
                if matched_ids != hybrid_ids:
                    matched_differs[label] += 1
                if need <= matched_ids:
                    matched_hits[label] += 1

    unbounded_union = {label: _size_summary(unbounded_sizes[label]) for label in unbounded_sizes}
    matched_union = {label: _size_summary(matched_sizes[label]) for label in matched_sizes}
    hybrid_union = {label: _size_summary(hybrid_sizes[label]) for label in hybrid_sizes}
    k_eff_at_k = {}
    hybrid_at_mean_hits = {str(k): 0 for k in ks}
    hybrid_at_mean_sizes = {str(k): [] for k in ks}
    if n_pairs:
        for k in ks:
            label = str(k)
            mean = unbounded_union[label]["mean"]
            k_eff = max(1, int(round(float(mean)))) if mean is not None else k
            k_eff_at_k[label] = k_eff
        for item in pairs:
            need = set(item["chunk_ids"])
            ranked = _rank(str(item["prompt_fact"].get("query") or ""))
            for k in ks:
                label = str(k)
                k_eff = int(k_eff_at_k[label])
                hybrid_ids = {
                    str(row["chunk_id"]) for row in ranked[:k_eff] if row.get("chunk_id")
                }
                hybrid_at_mean_sizes[label].append(len(hybrid_ids))
                if need <= hybrid_ids:
                    hybrid_at_mean_hits[label] += 1
    else:
        for k in ks:
            k_eff_at_k[str(k)] = k

    n_hold = len(split["must_refuse"])
    hybrid_leaks = {str(k): 0 for k in ks}
    matched_leaks = {str(k): 0 for k in ks}
    unbounded_leaks = {str(k): 0 for k in ks}
    hybrid_at_mean_leaks = {str(k): 0 for k in ks}
    for fact in split["must_refuse"]:
        ranked = _rank(str(fact.get("query") or ""))
        needles = fact.get("needles") or {}
        hybrid_retr, _precision = text_chunk_metrics.retrieval_at_ks(ranked, needles)
        for k in ks:
            label = str(k)
            if hybrid_retr[label]:
                hybrid_leaks[label] += 1
            unbounded = _union_chunks(
                ranked,
                k,
                chunk_entities=chunk_entities,
                entity_chunks=entity_chunks,
                by_id=by_id,
            )
            if any(
                text_chunk_metrics.contain_bound_fact(text_chunk_metrics._body(row), needles)
                for row in unbounded
            ):
                unbounded_leaks[label] += 1
            matched = _truncate_union_by_rank(unbounded, ranked, k)
            if any(
                text_chunk_metrics.contain_bound_fact(text_chunk_metrics._body(row), needles)
                for row in matched
            ):
                matched_leaks[label] += 1
            k_eff = int(k_eff_at_k[label])
            if any(
                text_chunk_metrics.contain_bound_fact(text_chunk_metrics._body(row), needles)
                for row in ranked[:k_eff]
            ):
                hybrid_at_mean_leaks[label] += 1

    fire_chunks = set()
    by_paper = _chunk_rows(_load_json(store_file))
    for fact in split["must_fire"]:
        row = _join_chunk(by_paper, fact)
        if row is not None:
            fire_chunks.add(str(row["chunk_id"]))
    off = _load_json(abstention_a3.OFFDOMAIN_PATH)
    off_intro_unbounded = {str(k): 0 for k in ks}
    off_intro_matched = {str(k): 0 for k in ks}
    for row in off.get("queries") or []:
        ranked = _rank(str(row.get("query") or ""))
        for k in ks:
            label = str(k)
            hybrid_ids = {
                str(item["chunk_id"])
                for item in ranked[:k]
                if item.get("chunk_id")
            }
            unbounded = _union_chunks(
                ranked,
                k,
                chunk_entities=chunk_entities,
                entity_chunks=entity_chunks,
                by_id=by_id,
            )
            unbounded_ids = {str(item["chunk_id"]) for item in unbounded}
            if (unbounded_ids - hybrid_ids) & fire_chunks:
                off_intro_unbounded[label] += 1
            matched = _truncate_union_by_rank(unbounded, ranked, k)
            matched_ids = {str(item["chunk_id"]) for item in matched}
            if (matched_ids - hybrid_ids) & fire_chunks:
                off_intro_matched[label] += 1

    def _arm(
        hits: dict[str, int],
        leaks: dict[str, int],
        name: str,
        *,
        budget: str,
        union_size: dict[str, Any],
        walk: int,
    ) -> dict[str, Any]:
        n_refuse = {label: n_hold - int(leaks[label]) for label in leaks}
        recall = {
            label: text_chunk_metrics._v3_rate(int(hits[label]), n_pairs) if n_pairs else None
            for label in hits
        }
        recall_ci = {
            label: (
                text_chunk_metrics.wilson_interval(int(hits[label]), n_pairs)
                if n_pairs
                else {"lo": None, "hi": None}
            )
            for label in hits
        }
        return {
            "arm": name,
            "budget": budget,
            "n_pairs": n_pairs,
            "n_held_out_facts": n_hold,
            "n_retrievable_at_k": dict(hits) if n_pairs else {str(k): 0 for k in ks},
            "recall_at_k": recall,
            "recall_ci_at_k": recall_ci,
            "n_must_refuse_at_k": n_refuse,
            "must_refuse_at_k": {
                label: text_chunk_metrics._v3_rate(n_refuse[label], n_hold) for label in n_refuse
            },
            "must_refuse_ci_at_k": {
                label: text_chunk_metrics.wilson_interval(n_refuse[label], n_hold)
                for label in n_refuse
            },
            "n_leaks_at_k": dict(leaks),
            "union_size_at_k": union_size,
            "n_returned_mean_at_k": {
                label: (union_size.get(label) or {}).get("mean") for label in hits
            },
            "n_returned_median_at_k": {
                label: (union_size.get(label) or {}).get("median") for label in hits
            },
            "n_returned_max_at_k": {
                label: (union_size.get(label) or {}).get("max") for label in hits
            },
            "walk_depth": walk,
        }

    hybrid_row = _arm(
        hybrid_hits, hybrid_leaks, "hybrid",
        budget="k", union_size=hybrid_union, walk=0,
    )
    matched_row = _arm(
        matched_hits, matched_leaks, "graph_matched",
        budget="k", union_size=matched_union, walk=ENTITY_HOP_DEPTH,
    )
    unbounded_row = _arm(
        unbounded_hits, unbounded_leaks, "graph_unbounded",
        budget="unbounded", union_size=unbounded_union, walk=ENTITY_HOP_DEPTH,
    )
    hybrid_mean_union = {label: _size_summary(hybrid_at_mean_sizes[label]) for label in hybrid_at_mean_sizes}
    hybrid_at_mean_row = _arm(
        hybrid_at_mean_hits, hybrid_at_mean_leaks, "hybrid_at_union_mean",
        budget="graph_unbounded_mean", union_size=hybrid_mean_union, walk=0,
    )
    hybrid_at_mean_row["k_eff_at_k"] = dict(k_eff_at_k)

    def _versus(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k in ks:
            label = str(k)
            if n_pairs == 0:
                out[label] = {"delta": None, "verdict": "NO_PAIRS"}
                continue
            l_rec = float(left["recall_at_k"][label] or 0.0)
            r_rec = float(right["recall_at_k"][label] or 0.0)
            out[label] = {
                "delta": l_rec - r_rec,
                "verdict": _verdict(
                    left["recall_ci_at_k"][label],
                    right["recall_ci_at_k"][label],
                    l_rec - r_rec,
                ),
            }
        return out

    versus_matched = _versus(matched_row, hybrid_row)
    versus_unbounded = _versus(unbounded_row, hybrid_row)
    versus_hybrid_at_mean: dict[str, Any] = {}
    hybrid_at_mean_recall = {}
    hybrid_at_mean_ci = {}
    for k in ks:
        label = str(k)
        k_eff = int(k_eff_at_k[label])
        if n_pairs == 0:
            hybrid_at_mean_recall[label] = None
            hybrid_at_mean_ci[label] = {"lo": None, "hi": None}
            versus_hybrid_at_mean[label] = {
                "k_eff": k_eff,
                "delta": None,
                "verdict": "NO_PAIRS",
            }
            continue
        hits = int(hybrid_at_mean_hits[label])
        rec = text_chunk_metrics._v3_rate(hits, n_pairs)
        ci = text_chunk_metrics.wilson_interval(hits, n_pairs)
        hybrid_at_mean_recall[label] = rec
        hybrid_at_mean_ci[label] = ci
        g_rec = float(unbounded_row["recall_at_k"][label] or 0.0)
        versus_hybrid_at_mean[label] = {
            "k_eff": k_eff,
            "hybrid_n_retrievable": hits,
            "hybrid_recall": rec,
            "hybrid_recall_ci": ci,
            "graph_unbounded_recall": g_rec,
            "delta": g_rec - float(rec or 0.0),
            "verdict": _verdict(
                unbounded_row["recall_ci_at_k"][label],
                ci,
                g_rec - float(rec or 0.0),
            ),
        }

    contest_matched = _contest(versus_matched, ks, n_pairs)
    contest_unbounded = _contest(versus_unbounded, ks, n_pairs)
    contest_mean = _contest(versus_hybrid_at_mean, ks, n_pairs)
    finding = _budget_finding(contest_matched, contest_mean)

    artifact = {
        "schema": "dissolve.text-chunk-curves.graph-rag.v2",
        "spec_sha256": SPEC_SHA256,
        "source_graph_sha256": str(graph.get("source_graph_sha256") or ""),
        "walk_depth": ENTITY_HOP_DEPTH,
        "n_pairs": n_pairs,
        "n_cross_paper_pairs": derived["n_cross_paper_pairs"],
        "n_same_paper_pairs": derived["n_same_paper_pairs"],
        "n_join_miss": derived["n_join_miss"],
        "n_facts_with_zero_entities": derived["n_facts_with_zero_entities"],
        "pair_contest": contest_matched,
        "pair_contest_matched": contest_matched,
        "pair_contest_unbounded": contest_unbounded,
        "pair_contest_hybrid_at_mean": contest_mean,
        "budget_finding": finding,
        "retrieval_ks": ks,
        "ci_method": "wilson_score",
        "ci_level": 0.95,
        "z": text_chunk_metrics.WILSON_Z,
        "hybrid_weights": {"dense": 0.55, "sparse": 0.40},
        "weights_retuned": False,
        "series": [hybrid_row, matched_row, unbounded_row, hybrid_at_mean_row],
        "versus_matched_at_k": versus_matched,
        "versus_unbounded_at_k": versus_unbounded,
        "hybrid_at_graph_mean_union": versus_hybrid_at_mean,
        "n_offdomain_introduced_at_k": off_intro_unbounded,
        "n_offdomain_introduced_matched_at_k": off_intro_matched,
        "n_matched_set_differs_from_hybrid_at_k": matched_differs,
        "matched_k_method": "truncate_union_by_hybrid_rank",
    }
    if text_chunk_metrics._contains_forbidden_keys(artifact, set(_FORBIDDEN)):
        raise TextGoldError("gold_quoted", "Graph-RAG curves must not carry gold identifiers.")
    out = _curves_path(dest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_dump(artifact), encoding="utf-8")
    header = {
        "curves_path": str(out),
        "curves_sha256": file_sha256(out),
        "pair_contest": contest_matched,
        "pair_contest_matched": contest_matched,
        "pair_contest_unbounded": contest_unbounded,
        "pair_contest_hybrid_at_mean": contest_mean,
        "budget_finding": finding,
        "n_pairs": n_pairs,
        "walk_depth": ENTITY_HOP_DEPTH,
        "spec_sha256": SPEC_SHA256,
    }
    if text_chunk_metrics._contains_forbidden_keys(header, set(_FORBIDDEN)):
        raise TextGoldError("gold_quoted", "Score return must not carry gold identifiers.")
    if any(int(unbounded_leaks[str(k)]) > 0 for k in ks):
        raise TextGoldError("refuse_leak", "Graph arm dropped must-refuse below 1.000.")
    if any(int(off_intro_unbounded[str(k)]) > 0 for k in ks):
        raise TextGoldError(
            "offdomain_leak",
            "Graph arm introduced a must_fire chunk on off-domain.",
        )
    return header


def dense_d3_hybrid_ranker(index: Mapping[str, Any], queries: Sequence[str]) -> Ranker:
    return dense_d2._cached_query_ranker(index, queries)
