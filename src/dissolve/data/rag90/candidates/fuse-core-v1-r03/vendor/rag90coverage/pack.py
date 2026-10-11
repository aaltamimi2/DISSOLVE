"""Coverage packing and MMR. Pure numpy-free selection over precomputed scores.

The harness stops at the first passage that does not fit, so every returned
list fits in order. A passage that is too long for the remaining budget is
skipped rather than emitted.
"""

from __future__ import annotations

import math

# Fixed before looking at silver rates. The prior is linear in reciprocal
# rank so the full-query order still breaks ties. Coverage saturates.
PRIOR_WEIGHT = 0.35
BETA = 1.0
MMR_LAMBDA = 0.7
FULL_TOP = 60
ASPECT_TOP = 40


def reciprocal(rank: int) -> float:
    return 1.0 / (1.0 + int(rank))


def pack_until_fit(ids: list[str], lengths: dict[str, int], budget_chars: int) -> list[str]:
    """Take whole passages in order. Stop at the first one that does not fit."""
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


def pool_from_ranks(
    full_ids: list[str],
    aspect_ids: list[list[str]],
    full_top: int = FULL_TOP,
    aspect_top: int = ASPECT_TOP,
) -> list[str]:
    """Union of the full-query head and each aspect head, full query first."""
    seen: set[str] = set()
    ordered: list[str] = []
    for passage_id in list(full_ids[:full_top]) + [
        passage_id
        for ranked in aspect_ids
        for passage_id in ranked[:aspect_top]
    ]:
        if passage_id in seen:
            continue
        seen.add(passage_id)
        ordered.append(passage_id)
    return ordered


def rank_relevance(ranked_ids: list[str], limit: int | None = None) -> dict[str, float]:
    rel: dict[str, float] = {}
    cap = len(ranked_ids) if limit is None else min(limit, len(ranked_ids))
    for rank, passage_id in enumerate(ranked_ids[:cap]):
        rel[passage_id] = reciprocal(rank)
    return rel


def coverage_order(
    pool: list[str],
    lengths: dict[str, int],
    budget_chars: int,
    aspect_rel: list[dict[str, float]],
    prior: dict[str, float],
    prior_weight: float = PRIOR_WEIGHT,
    beta: float = BETA,
) -> list[str]:
    """Greedy submodular cover. Marginal gain is the rise in 1 - exp(-beta * sum rel)."""
    budget = int(budget_chars)
    if budget <= 0 or not pool:
        return []
    remaining = set(pool)
    sums = [0.0] * len(aspect_rel)
    chosen: list[str] = []
    used = 0
    while remaining:
        best_id = None
        best_key = None
        for passage_id in remaining:
            length = int(lengths[passage_id])
            if used + length > budget:
                continue
            gain = prior_weight * float(prior.get(passage_id, 0.0))
            for index, rel in enumerate(aspect_rel):
                weight = float(rel.get(passage_id, 0.0))
                if weight <= 0.0:
                    continue
                saturated = math.exp(-beta * sums[index])
                gain += saturated * (1.0 - math.exp(-beta * weight))
            key = (gain, float(prior.get(passage_id, 0.0)), passage_id)
            if best_key is None or key[0] > best_key[0] + 1e-12 or (
                abs(key[0] - best_key[0]) <= 1e-12 and (key[1] > best_key[1] or (key[1] == best_key[1] and key[2] < best_key[2]))
            ):
                best_key = key
                best_id = passage_id
        if best_id is None or best_key is None or best_key[0] <= 0.0:
            break
        chosen.append(best_id)
        remaining.remove(best_id)
        used += int(lengths[best_id])
        for index, rel in enumerate(aspect_rel):
            sums[index] += float(rel.get(best_id, 0.0))
    return chosen


def cover_from_ranks(
    full_ids: list[str],
    aspect_ids: list[list[str]],
    lengths: dict[str, int],
    budget_chars: int,
    prior_weight: float = PRIOR_WEIGHT,
    beta: float = BETA,
) -> list[str]:
    """Pack one question from full-query and per-aspect rank lists."""
    pool = pool_from_ranks(full_ids, aspect_ids, FULL_TOP, ASPECT_TOP)
    prior = rank_relevance(full_ids, 200)
    if aspect_ids:
        aspect_rel = [rank_relevance(ranked, ASPECT_TOP) for ranked in aspect_ids]
    else:
        aspect_rel = [rank_relevance(full_ids, ASPECT_TOP)]
    return coverage_order(pool, lengths, budget_chars, aspect_rel, prior, prior_weight, beta)


def mmr_order(
    pool: list[str],
    lengths: dict[str, int],
    budget_chars: int,
    relevance: dict[str, float],
    similarity,
    lam: float = MMR_LAMBDA,
) -> list[str]:
    """MMR. similarity(left_id, right_id) returns a cosine in [-1, 1]."""
    budget = int(budget_chars)
    if budget <= 0 or not pool:
        return []
    if not 0.0 <= lam <= 1.0:
        raise ValueError("lambda")
    remaining = set(pool)
    chosen: list[str] = []
    used = 0
    while remaining:
        best_id = None
        best_key = None
        for passage_id in remaining:
            length = int(lengths[passage_id])
            if used + length > budget:
                continue
            rel = float(relevance.get(passage_id, 0.0))
            if chosen:
                redundancy = max(similarity(passage_id, other) for other in chosen)
            else:
                redundancy = 0.0
            if redundancy < 0.0:
                redundancy = 0.0
            score = lam * rel - (1.0 - lam) * redundancy
            key = (score, rel, passage_id)
            if best_key is None or key[0] > best_key[0] + 1e-12 or (
                abs(key[0] - best_key[0]) <= 1e-12 and key[2] < best_key[2]
            ):
                best_key = key
                best_id = passage_id
        if best_id is None:
            break
        chosen.append(best_id)
        remaining.remove(best_id)
        used += int(lengths[best_id])
    return chosen


def r3_core_then_aspects(
    full_ids: list[str],
    aspect_ids: list[list[str]],
    lengths: dict[str, int],
    budget_chars: int,
    protect_rank: int | None = None,
    prior_weight: float = PRIOR_WEIGHT,
    beta: float = BETA,
) -> list[str]:
    """Keep an r3 prefix. Aspects fill only what that prefix does not use.

    protect_rank None packs the r3 list until the first passage that does not
    fit, then appends aspect passages that fit in the leftover. The r3 passages
    stay in front, so this cannot drop a passage the served list would return.

    protect_rank K keeps only the first K fitting r3 passages. Aspect passages
    take the rest of the budget ahead of the r3 tail, and any r3 tail that still
    fits follows them. K is a sweep parameter, not chosen inside this function.
    """
    budget = int(budget_chars)
    if budget <= 0:
        return []
    if protect_rank is not None and int(protect_rank) < 1:
        raise ValueError("protect_rank")
    core: list[str] = []
    used = 0
    for passage_id in full_ids:
        if protect_rank is not None and len(core) >= int(protect_rank):
            break
        length = int(lengths[passage_id])
        if used + length > budget:
            break
        core.append(passage_id)
        used += length
    pool = pool_from_ranks(full_ids, aspect_ids, FULL_TOP, ASPECT_TOP)
    prior = rank_relevance(full_ids, 200)
    if aspect_ids:
        aspect_rel = [rank_relevance(ranked, ASPECT_TOP) for ranked in aspect_ids]
    else:
        aspect_rel = [rank_relevance(full_ids, ASPECT_TOP)]
    unit = {passage_id: 1 for passage_id in pool}
    preference = coverage_order(
        pool, unit, max(len(pool), 1), aspect_rel, prior, prior_weight, beta
    )
    seen = set(core)
    chosen = list(core)
    for passage_id in preference:
        if passage_id in seen:
            continue
        length = int(lengths[passage_id])
        if used + length <= budget:
            chosen.append(passage_id)
            seen.add(passage_id)
            used += length
    if protect_rank is None:
        return chosen
    for passage_id in full_ids:
        if passage_id in seen:
            continue
        length = int(lengths[passage_id])
        if used + length <= budget:
            chosen.append(passage_id)
            seen.add(passage_id)
            used += length
        else:
            break
    return chosen
