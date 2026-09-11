"""§5.7 RESAMPLE.v1."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

import numpy as np

from answer_eval.tables import DEFAULT_SEED, PRODUCTION_B

PRODUCTION_B_CONSTANT = PRODUCTION_B


def serialize_endpoint(x: float) -> str:
    d = Decimal(repr(float(x))).quantize(Decimal("1e-12"), rounding=ROUND_HALF_EVEN)
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def public_interval_from_serialized(ends: list[str]) -> list[float]:
    return [float(Decimal(s)) for s in ends]


def roundtrip_ok(wire: list[float], serialized: list[str]) -> bool:
    return [serialize_endpoint(x) for x in wire] == list(serialized)


def _percentile_linear(values: list[float]) -> tuple[float, float] | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    lo, hi = np.percentile(arr, [2.5, 97.5], method="linear")
    return float(lo), float(hi)


def _ratio(num: int, den: int) -> float | None:
    if den == 0:
        return None
    return num / den


def resample(
    scores: Any,
    metric: str,
    B: int,
    seed: int,
    frame: str,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    cutoff = int(0.10 * B)
    if cutoff == 0 and B:
        cutoff = 0
    inst_cut = 5 if B == 50 else 200
    if B == PRODUCTION_B:
        inst_cut = 200
    else:
        inst_cut = max(1, int(0.10 * B) if (0.10 * B) == int(0.10 * B) else int(0.10 * B))
        if B == 50:
            inst_cut = 5

    if frame in {"one_stage", "family_clustered_primary"} or metric == "synthetic_one_stage_ratio":
        return _one_stage(scores, B, rng, inst_cut)
    if frame in {"two_stage"} or metric == "synthetic_two_stage_ratio":
        return _two_stage(scores, B, rng, inst_cut)
    if frame in {"paired_question"} or metric == "M-3t_on_minus_off":
        return _paired_two_stage(scores, B, rng, inst_cut)
    if frame == "coordinated_shared_family":
        return _coordinated(scores, B, rng, inst_cut)
    if frame == "independent_disjoint":
        return _independent(scores, B, rng, inst_cut)
    raise ValueError(f"unknown frame {frame}")


def _one_stage(clusters: dict[str, dict[str, int]], B: int, rng, inst_cut: int) -> dict[str, Any]:
    ids = sorted(clusters)
    n = len(ids)
    point_n = sum(clusters[i]["num"] for i in ids)
    point_d = sum(clusters[i]["den"] for i in ids)
    if n == 1:
        return {
            "point": {"num": point_n, "den": point_d},
            "interval_label": "degenerate_single_cluster",
            "replicates_used": 0,
            "dropped_replicates": 0,
            "dropped_reason": "none",
            "instability_cutoff": inst_cut,
            "percentile_method": "linear",
        }
    if n == 0:
        return {
            "point": {"num": 0, "den": 0},
            "interval_label": "NA",
            "replicates_used": 0,
            "dropped_replicates": 0,
            "dropped_reason": "none",
            "instability_cutoff": inst_cut,
        }
    used: list[float] = []
    dropped = 0
    for _ in range(B):
        draw = rng.integers(0, n, n)
        num = sum(clusters[ids[int(i)]]["num"] for i in draw)
        den = sum(clusters[ids[int(i)]]["den"] for i in draw)
        r = _ratio(num, den)
        if r is None:
            dropped += 1
        else:
            used.append(r)
    return _finish(used, dropped, inst_cut, "family_clustered_primary", {"num": point_n, "den": point_d}, B)


def _two_stage(clusters: dict[str, list[dict[str, int]]], B: int, rng, inst_cut: int) -> dict[str, Any]:
    ids = sorted(clusters)
    n = len(ids)
    point_n = sum(q["num"] for fam in ids for q in clusters[fam])
    point_d = sum(q["den"] for fam in ids for q in clusters[fam])
    used: list[float] = []
    dropped = 0
    for _ in range(B):
        fam_draw = rng.integers(0, n, n)
        num = den = 0
        for fi in fam_draw:
            fam = ids[int(fi)]
            qs = clusters[fam]
            m = len(qs)
            from answer_eval.mutation import get as mut

            if mut() == "omit_inner_draw":
                for q in qs:
                    num += q["num"]
                    den += q["den"]
                continue
            q_draw = rng.integers(0, m, m)
            for qi in q_draw:
                num += qs[int(qi)]["num"]
                den += qs[int(qi)]["den"]
        r = _ratio(num, den)
        if r is None:
            dropped += 1
        else:
            used.append(r)
    return _finish(used, dropped, inst_cut, "two_stage", {"num": point_n, "den": point_d}, B)


def _paired_two_stage(clusters: dict[str, list[dict[str, int]]], B: int, rng, inst_cut: int) -> dict[str, Any]:
    ids = sorted(clusters)
    n = len(ids)
    on_n = sum(q["on"] for fam in ids for q in clusters[fam])
    off_n = sum(q["off"] for fam in ids for q in clusters[fam])
    den = sum(1 for fam in ids for _ in clusters[fam])
    used: list[float] = []
    dropped = 0
    for _ in range(B):
        fam_draw = rng.integers(0, n, n)
        on = off = count = 0
        for fi in fam_draw:
            fam = ids[int(fi)]
            qs = clusters[fam]
            m = len(qs)
            from answer_eval.mutation import get as mut

            if mut() == "omit_inner_draw":
                for q in qs:
                    on += q["on"]
                    off += q["off"]
                    count += 1
                continue
            q_draw = rng.integers(0, m, m)
            for qi in q_draw:
                q = qs[int(qi)]
                on += q["on"]
                off += q["off"]
                count += 1
        if count == 0:
            dropped += 1
            continue
        used.append((on - off) / count)
    return _finish(
        used,
        dropped,
        inst_cut,
        "two_stage",
        {"num": on_n - off_n, "den": den},
        B,
        extra={"frame": "paired_question"},
    )


def _coordinated(payload: dict[str, Any], B: int, rng, inst_cut: int) -> dict[str, Any]:
    test = payload["test"]
    fresh = payload["fresh"]
    union = sorted(set(test) | set(fresh))
    n = len(union)
    used: list[float] = []
    dropped = 0
    for _ in range(B):
        fam_draw = rng.integers(0, n, n)
        t_n = t_d = f_n = f_d = 0
        for fi in fam_draw:
            fam = union[int(fi)]
            tqs = list(test.get(fam) or [])
            fqs = list(fresh.get(fam) or [])
            if tqs:
                m = len(tqs)
                for qi in rng.integers(0, m, m):
                    t_n += int(tqs[int(qi)])
                    t_d += 1
            if fqs:
                m = len(fqs)
                for qi in rng.integers(0, m, m):
                    f_n += int(fqs[int(qi)])
                    f_d += 1
        if t_d == 0 or f_d == 0:
            dropped += 1
            continue
        used.append((f_n / f_d) - (t_n / t_d))
    t_n = sum(sum(v) for v in test.values())
    t_d = sum(len(v) for v in test.values())
    f_n = sum(sum(v) for v in fresh.values())
    f_d = sum(len(v) for v in fresh.values())
    return _finish(
        used,
        dropped,
        inst_cut,
        "coordinated_shared_family",
        None,
        B,
        extra={
            "frame": "coordinated_shared_family",
            "point_test": {"num": t_n, "den": t_d},
            "point_fresh": {"num": f_n, "den": f_d},
            "shared_families": sorted(set(test) & set(fresh)),
        },
    )


def _independent(payload: dict[str, Any], B: int, rng, inst_cut: int) -> dict[str, Any]:
    test = payload["test"]
    fresh = payload["fresh"]
    t_ids = sorted(test)
    f_ids = sorted(fresh)
    used: list[float] = []
    dropped = 0
    for _ in range(B):
        t_n = t_d = f_n = f_d = 0
        if t_ids:
            n_t = len(t_ids)
            for fi in rng.integers(0, n_t, n_t):
                fam = t_ids[int(fi)]
                qs = test[fam]
                m = len(qs)
                for qi in rng.integers(0, m, m):
                    t_n += int(qs[int(qi)])
                    t_d += 1
        if f_ids:
            n_f = len(f_ids)
            for fi in rng.integers(0, n_f, n_f):
                fam = f_ids[int(fi)]
                qs = fresh[fam]
                m = len(qs)
                for qi in rng.integers(0, m, m):
                    f_n += int(qs[int(qi)])
                    f_d += 1
        if t_d == 0 or f_d == 0:
            dropped += 1
            continue
        used.append((f_n / f_d) - (t_n / t_d))
    t_n = sum(sum(v) for v in test.values())
    t_d = sum(len(v) for v in test.values())
    f_n = sum(sum(v) for v in fresh.values())
    f_d = sum(len(v) for v in fresh.values())
    return _finish(
        used,
        dropped,
        inst_cut,
        "independent_disjoint",
        None,
        B,
        extra={
            "frame": "independent_disjoint",
            "point_test": {"num": t_n, "den": t_d},
            "point_fresh": {"num": f_n, "den": f_d},
            "shared_families": sorted(set(test) & set(fresh)),
        },
    )


def _finish(used, dropped, inst_cut, label, point, B, extra=None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "B": B,
        "replicates_used": len(used),
        "dropped_replicates": dropped,
        "dropped_reason": "zero_denominator" if dropped else "none",
        "instability_cutoff": inst_cut,
        "percentile_method": "linear",
        "rng": f"numpy.random.default_rng({DEFAULT_SEED})",
    }
    if point is not None:
        out["point"] = point
    if extra:
        out.update(extra)
    if dropped > inst_cut:
        out["interval_label"] = "unstable"
        return out
    if not used:
        out["interval_label"] = label
        return out
    ends = _percentile_linear(used)
    if ends is None:
        out["interval_label"] = label
        return out
    ser = [serialize_endpoint(ends[0]), serialize_endpoint(ends[1])]
    out["interval_label"] = label
    out["interval_serialized"] = ser
    out["interval"] = [float(Decimal(ser[0])), float(Decimal(ser[1]))]
    return out
