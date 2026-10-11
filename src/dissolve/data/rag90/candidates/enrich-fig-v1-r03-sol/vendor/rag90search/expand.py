"""Budget packing on top of an r3 ranking.

Neighbour expansion inserts adjacent passages of the same paper after each hit.
Paper-first spends a fraction of the budget inside the top papers, then the rest
of the global list. Focus-or-slack spends the whole budget on one paper when
that paper dominates the top hits, and otherwise keeps global order plus slack
neighbours. None of these functions changes sparse or dense scores.
"""

from __future__ import annotations


def pack(ids: list[str], lengths: dict[str, int], budget_chars: int) -> list[str]:
    """Take whole passages in order until the next one does not fit."""
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


def _walk(start: str, link: dict[str, str | None], steps: int) -> str | None:
    current = start
    for _ in range(steps):
        current = link.get(current)
        if not current:
            return None
    return current


def expand_hit_first(
    ranked_ids: list[str],
    prev_of: dict[str, str | None],
    next_of: dict[str, str | None],
    radius: int,
) -> list[str]:
    """Each new hit is followed by its neighbors, nearer first, next before previous.

    The hit stays ahead of its neighbors so a neighbor that does not fit cannot
    push the hit out of the budget.
    """
    if radius < 0:
        raise ValueError("radius")
    seen: set[str] = set()
    ordered: list[str] = []
    for hit in ranked_ids:
        if hit in seen:
            continue
        block = [hit]
        for distance in range(1, radius + 1):
            nxt = _walk(hit, next_of, distance)
            prv = _walk(hit, prev_of, distance)
            if nxt:
                block.append(nxt)
            if prv:
                block.append(prv)
        for passage_id in block:
            if passage_id in seen:
                continue
            seen.add(passage_id)
            ordered.append(passage_id)
    return ordered


def paper_first(
    ranked: list[tuple[float, str]],
    paper_of: dict[str, str],
    lengths: dict[str, int],
    budget_chars: int,
    top_papers: int,
    paper_fraction: float,
) -> list[str]:
    """Fill paper_fraction of the budget from the top papers, then the global list."""
    if top_papers < 1:
        raise ValueError("top_papers")
    if not 0 < paper_fraction <= 1:
        raise ValueError("paper_fraction")
    best: dict[str, float] = {}
    for score, passage_id in ranked:
        paper = paper_of[passage_id]
        if paper not in best or score > best[paper]:
            best[paper] = score
    chosen_papers = sorted(best, key=lambda paper: (-best[paper], paper))[:top_papers]
    paper_set = set(chosen_papers)
    primary = [passage_id for _score, passage_id in ranked if paper_of[passage_id] in paper_set]
    rest = [passage_id for _score, passage_id in ranked if paper_of[passage_id] not in paper_set]
    paper_budget = int(int(budget_chars) * paper_fraction)
    head = pack(primary, lengths, paper_budget)
    seen = set(head)
    used = sum(int(lengths[passage_id]) for passage_id in head)
    remainder = [passage_id for _score, passage_id in ranked if passage_id not in seen]
    tail = pack(remainder, lengths, int(budget_chars) - used)
    return head + tail


def pack_expand(
    ranked_ids: list[str],
    prev_of: dict[str, str | None],
    next_of: dict[str, str | None],
    radius: int,
    lengths: dict[str, int],
    budget_chars: int,
    expand_limit: int | None = None,
) -> list[str]:
    """Place each hit, then any neighbor that still fits. Skip an unfit neighbor.

    Stop only when a new hit does not fit. The returned ids fit in order, so a
    harness that stops at the first unfit passage keeps the same list.
    """
    if radius < 0:
        raise ValueError("radius")
    budget = int(budget_chars)
    if budget <= 0:
        return []
    chosen: list[str] = []
    seen: set[str] = set()
    used = 0
    expanded = 0

    def try_add(passage_id: str) -> bool:
        nonlocal used
        if passage_id in seen:
            return True
        length = int(lengths[passage_id])
        if used + length > budget:
            return False
        chosen.append(passage_id)
        seen.add(passage_id)
        used += length
        return True

    for hit in ranked_ids:
        if hit in seen:
            continue
        if not try_add(hit):
            break
        if expand_limit is not None and expanded >= expand_limit:
            continue
        expanded += 1
        for distance in range(1, radius + 1):
            for link in (next_of, prev_of):
                neighbor = _walk(hit, link, distance)
                if neighbor:
                    try_add(neighbor)
    return chosen


def pack_slack(
    ranked_ids: list[str],
    prev_of: dict[str, str | None],
    next_of: dict[str, str | None],
    radius: int,
    lengths: dict[str, int],
    budget_chars: int,
) -> list[str]:
    """Keep the plain packed hits, then add neighbors that fit in the leftover budget."""
    chosen = pack(ranked_ids, lengths, budget_chars)
    seen = set(chosen)
    used = sum(int(lengths[passage_id]) for passage_id in chosen)
    budget = int(budget_chars)
    for hit in chosen:
        for distance in range(1, radius + 1):
            for link in (next_of, prev_of):
                neighbor = _walk(hit, link, distance)
                if not neighbor or neighbor in seen:
                    continue
                length = int(lengths[neighbor])
                if used + length <= budget:
                    chosen.append(neighbor)
                    seen.add(neighbor)
                    used += length
    return chosen


def paper_concentration(ranked_ids: list[str], paper_of: dict[str, str], k: int) -> tuple[int, int, str | None]:
    """Return (hits from the leading paper, head size, that paper) inside the top k.

    Ties go to the paper that appears first.
    """
    if k < 1:
        raise ValueError("k")
    head = ranked_ids[:k]
    if not head:
        return 0, 0, None
    counts: dict[str, int] = {}
    for passage_id in head:
        paper = paper_of[passage_id]
        counts[paper] = counts.get(paper, 0) + 1
    best = max(counts.values())
    top = next(paper_of[passage_id] for passage_id in head if counts[paper_of[passage_id]] == best)
    return best, len(head), top


def allocate(
    ranked_ids: list[str],
    paper_of: dict[str, str],
    prev_of: dict[str, str | None],
    next_of: dict[str, str | None],
    lengths: dict[str, int],
    budget_chars: int,
    k: int,
    min_count: int,
) -> list[str]:
    """Focused packing when the top paper dominates the top k, otherwise plain order.

    The focused slice gets (count / head) of the budget, filled with that paper's hits
    and radius-1 neighbours that fit. The rest of the budget follows the global order.
    min_count is the switch, chosen by cross-validation, not by this function.
    """
    if min_count < 1:
        raise ValueError("min_count")
    count, head, top = paper_concentration(ranked_ids, paper_of, k)
    if top is None or count < min_count:
        return pack(ranked_ids, lengths, budget_chars)
    focus_budget = int(int(budget_chars) * count / head)
    paper_hits = [passage_id for passage_id in ranked_ids if paper_of[passage_id] == top]
    focused = pack_expand(paper_hits, prev_of, next_of, 1, lengths, focus_budget)
    seen = set(focused)
    used = sum(int(lengths[passage_id]) for passage_id in focused)
    rest = [passage_id for passage_id in ranked_ids if passage_id not in seen]
    return focused + pack(rest, lengths, int(budget_chars) - used)


def focus_or_slack(
    ranked_ids: list[str],
    paper_of: dict[str, str],
    prev_of: dict[str, str | None],
    next_of: dict[str, str | None],
    lengths: dict[str, int],
    budget_chars: int,
    k: int,
    min_count: int,
) -> list[str]:
    """Whole-budget focus when one paper dominates the top k; otherwise slack neighbours.

    Dominance is at least min_count hits of one paper inside the top k. That branch
    spends the entire budget on that paper's hits, in rank order, and their radius-1
    neighbours. The other branch keeps plain rank order and adds only neighbours that
    fit in the leftover budget. min_count is the switch, chosen by cross-validation.
    """
    if min_count < 1:
        raise ValueError("min_count")
    count, _head, top = paper_concentration(ranked_ids, paper_of, k)
    if top is None or count < min_count:
        return pack_slack(ranked_ids, prev_of, next_of, 1, lengths, budget_chars)
    paper_hits = [passage_id for passage_id in ranked_ids if paper_of[passage_id] == top]
    return pack_expand(paper_hits, prev_of, next_of, 1, lengths, budget_chars)
