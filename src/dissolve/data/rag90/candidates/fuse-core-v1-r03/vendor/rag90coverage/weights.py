"""Reweight silver rates to the published benchmark mix.

Silver kinds are exclusive. The benchmark rates are not: table or caption
evidence overlaps the passage-count split, and cross-paper questions sit
inside the multi-passage share. The weights below make an exclusive mixture
that matches those margins, then scale the non-table strata so the five
weights sum to 1.

Sources of the margins:
- one passage supplies every claim: 266/376
- two or more passages are needed: 110/376
- evidence spans several papers: 0.07 (the assignment's "about 7%"; no exact count was published)
- table or caption evidence: 28/376

Same-paper multi-passage mass is split equally across pair_adjacent and
pair_far. The benchmark aggregate does not separate those two shapes.
"""

from __future__ import annotations

BENCH_N = 376
ONE_PASSAGE = 266
MULTI_PASSAGE = 110
TABLE_OR_CAPTION = 28
CROSS_PAPER_SHARE = 0.07

_ONE = ONE_PASSAGE / BENCH_N
_MULTI = MULTI_PASSAGE / BENCH_N
_TABLE = TABLE_OR_CAPTION / BENCH_N
_SAME = _MULTI - CROSS_PAPER_SHARE
_REMAIN = 1.0 - _TABLE

WEIGHTS: dict[str, float] = {
    "single": _ONE * _REMAIN,
    "pair_adjacent": (_SAME / 2.0) * _REMAIN,
    "pair_far": (_SAME / 2.0) * _REMAIN,
    "cross_paper": CROSS_PAPER_SHARE * _REMAIN,
    "table": _TABLE,
}

_WEIGHT_SUM = sum(WEIGHTS.values())
if abs(_WEIGHT_SUM - 1.0) > 1e-9:
    raise RuntimeError(f"weights_sum {_WEIGHT_SUM}")
if _SAME <= 0:
    raise RuntimeError("cross_paper_share_exceeds_multi")


def reweighted(rate_by_kind: dict[str, float]) -> float:
    """Stratum-weighted mean. Missing kinds raise."""
    total = 0.0
    for kind, weight in WEIGHTS.items():
        if kind not in rate_by_kind:
            raise KeyError(kind)
        total += weight * float(rate_by_kind[kind])
    return total


def rates_from_hits(hits_by_kind: dict[str, tuple[int, int]]) -> dict[str, float]:
    out = {}
    for kind in WEIGHTS:
        hits, count = hits_by_kind[kind]
        if count <= 0:
            raise ValueError(kind)
        out[kind] = hits / count
    return out
