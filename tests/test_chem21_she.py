"""CHEM21 SH&E scorer and G-score-alternative rerank (spec v2)."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve.agent_tools import tool_schemas
from dissolve import safety, tea
from dissolve.contracts import parse_tool_result

_GREEN_IDENT_QUERY = dict(
    feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3,
)
_GREEN_IDENT_SHA256 = (
    "30fe3b378cb1082d64fbf926492c2c3e65770e5309e194532314fe2cef644876"
)
_GAP_FLASH_4 = (398, 6568, 7762, 7964)
_GAP_FLASH_5 = (12021,)

_PAYLOAD_KEYS = safety._CHEM21_PAYLOAD_KEYS


def _inputs(**fields):
    base = {
        "boiling_point_c": 100.0,
        "flash_point_c": None,
        "autoignition_c": None,
        "statements": [],
        "cas_number": None,
        "admission_signal_word": None,
        "snapshot_signal_word": None,
        "n_ghs_statements": None,
        "resistivity_ohm_m": None,
    }
    base.update(fields)
    return base


def test_recipe_water_is_one_and_recommended():
    out = safety.score_chem21_she("water", inputs=_inputs(
        flash_point_c=100.0, boiling_point_c=100.0, cas_number="7732-18-5",
    ))
    assert out["chem21_safety_score"] == 1
    assert out["chem21_health_score"] == 1
    assert out["chem21_environment_score"] == 1
    assert out["chem21_default_ranking"] == "recommended"
    for key in _PAYLOAD_KEYS:
        assert key in out


def test_recipe_diethyl_ether_is_ten_when_extras_supplied():
    out = safety.score_chem21_she("diethyl ether", inputs=_inputs(
        flash_point_c=-45.0,
        autoignition_c=160.0,
        resistivity_ohm_m=3.0e11,
        statements=["EUH019 May form explosive peroxides."],
        boiling_point_c=34.6,
        cas_number="60-29-7",
    ))
    assert out["chem21_safety_score"] == 10
    assert out["chem21_adjustments"]["ait_adjustment_applied"] is True
    assert out["chem21_adjustments"]["euh019_adjustment_applied"] is True
    assert out["chem21_adjustments"]["resistivity_adjustment_applied"] is True
    assert out["chem21_adjustments"]["decomposition_override"] == "not_assessed"


def test_recipe_acetone_safety_five_default_problematic():
    out = safety.score_chem21_she("acetone", inputs=_inputs(
        flash_point_c=-20.0,
        boiling_point_c=56.05,
        autoignition_c=465.0,
        statements=["H319 Causes serious eye irritation."],
        cas_number="67-64-1",
    ))
    assert out["chem21_safety_score"] == 5
    assert out["chem21_health_score"] == 3
    assert out["chem21_default_ranking"] == "problematic"


def test_recipe_benzene_safety_six_health_ten():
    out = safety.score_chem21_she("benzene", inputs=_inputs(
        flash_point_c=-11.0,
        boiling_point_c=80.1,
        resistivity_ohm_m=1.0e11,
        statements=["H340", "H350"],
        cas_number="71-43-2",
    ))
    assert out["chem21_safety_score"] == 6
    assert out["chem21_health_score"] == 10
    assert out["chem21_default_ranking"] == "hazardous"


def test_recipe_dmf_safety_three_health_nine():
    out = safety.score_chem21_she("DMF", inputs=_inputs(
        flash_point_c=58.0,
        boiling_point_c=153.0,
        statements=["H360 May damage fertility or the unborn child."],
        cas_number="68-12-2",
    ))
    assert out["chem21_safety_score"] == 3
    assert out["chem21_health_score"] == 9
    assert out["chem21_adjustments"]["health_bp_below_85_applied"] is False
    assert out["chem21_default_ranking"] == "hazardous"


def test_recipe_n_butyl_acetate_safety_four():
    out = safety.score_chem21_she("n-butyl acetate", inputs=_inputs(
        flash_point_c=22.0,
        boiling_point_c=126.1,
        cas_number="123-86-4",
    ))
    assert out["chem21_safety_score"] == 4


def test_dcm_missing_flash_is_null_not_one():
    out = safety.score_chem21_she("DCM", inputs=_inputs(
        flash_point_c=None, boiling_point_c=39.6, cas_number="75-09-2",
    ))
    assert out["chem21_safety_score"] is None
    assert out["chem21_unavailable_reason"] == "flash_point_missing"
    assert out["chem21_default_ranking"] is None


def test_nitromethane_excluded_decomposition_not_in_v1():
    out = safety.score_chem21_she("nitromethane")
    assert out["chem21_adjustments"]["decomposition_override"] == "not_assessed"
    assert out.get("chem21_safety_score") != "hard-coded-10"


def test_health_bp_bump_applied_at_most_once():
    default = safety.score_chem21_she("unknown-solvent", inputs=_inputs(
        flash_point_c=80.0, boiling_point_c=70.0, statements=[],
    ))
    assert default["chem21_health_score"] == 6
    assert default["chem21_adjustments"]["health_bp_below_85_applied"] is True
    h3xx = safety.score_chem21_she("unknown-solvent", inputs=_inputs(
        flash_point_c=80.0, boiling_point_c=70.0, statements=["H319"],
    ))
    assert h3xx["chem21_health_score"] == 3
    missing_bp = safety.score_chem21_she("unknown-solvent", inputs=_inputs(
        flash_point_c=80.0, boiling_point_c=None, statements=[],
    ))
    assert missing_bp["chem21_health_score"] == 5


def test_snapshot_data_gap_payload_has_every_key():
    out = safety.score_chem21_she("toluene")
    for key in _PAYLOAD_KEYS:
        assert key in out
    if out["chem21_safety_score"] is None:
        assert out["chem21_unavailable_reason"] == "flash_point_missing"
    else:
        assert 1 <= out["chem21_safety_score"] <= 10
        assert out["chem21_safety_score"] == safety._chem21_safety_basic(
            out["chem21_inputs"]["flash_point_c"]
        ) + int(out["chem21_adjustments"]["ait_adjustment_applied"]) + int(
            out["chem21_adjustments"]["euh019_adjustment_applied"]
        ) + int(out["chem21_adjustments"]["resistivity_adjustment_applied"])
    assert out["chem21_ghs_source"] == "pubchem_safety_snapshot"
    assert out["chem21_inputs"]["reach_registration"] == "unknown"
    assert out["chem21_inputs"]["resistivity_ohm_m"] is None


def test_g_score_and_chem21_are_not_aliases():
    profile = safety.build_safety_profile("toluene", include_pubchem=True)
    assert "g_score" in (profile.get("gscore") or {}) or profile.get("gscore")
    g_value = (profile.get("gscore") or {}).get("g_score")
    chem21 = profile["chem21_safety_score"]
    assert "chem21_safety_score" in profile
    assert chem21 != g_value
    assert profile.get("g_score") is None or profile["g_score"] == g_value
    assert "chem21_g_score" not in profile
    assert profile["chem21_recipe"] == "Prat2016_CHEM21_GreenChem_18_288"


def test_signal_word_disagreement_still_scores():
    out = safety.score_chem21_she("1,4-dimethylbenzene")
    assert out["chem21_signal_word_agreement"] is False
    out = safety.score_chem21_she("1-butanol")
    assert out["chem21_signal_word_agreement"] is True
    assert out["chem21_safety_score"] is None or 1 <= out["chem21_safety_score"] <= 10


def test_r1_gap_cids_use_repaired_ladder():
    for cid in _GAP_FLASH_4:
        pub = safety._snapshot_pubchem(cid)
        flash = float(pub["flash_point_c"])
        assert 23.8 < flash < 24.0
        out = safety.score_chem21_she(f"cid-{cid}", inputs=_inputs(flash_point_c=flash))
        assert out["chem21_safety_score"] == 4
    for cid in _GAP_FLASH_5:
        pub = safety._snapshot_pubchem(cid)
        flash = float(pub["flash_point_c"])
        assert -1.0 < flash < 0.0
        out = safety.score_chem21_she(f"cid-{cid}", inputs=_inputs(flash_point_c=flash))
        assert out["chem21_safety_score"] == 5


@pytest.mark.parametrize(
    ("flash", "basic"),
    [
        (-20.0, 5),
        (-20.01, 7),
        (0.0, 4),
        (-0.01, 5),
        (23.999, 4),
        (24.0, 3),
        (60.0, 3),
        (60.01, 1),
    ],
)
def test_flash_bin_boundaries(flash, basic):
    out = safety.score_chem21_she("bin", inputs=_inputs(flash_point_c=flash))
    assert out["chem21_safety_score"] == basic


def test_no_flash_in_range_returns_none_for_a_bin():
    for step in range(-1000, 1001, 7):
        out = safety.score_chem21_she("bin", inputs=_inputs(flash_point_c=float(step)))
        assert out["chem21_safety_score"] in {1, 3, 4, 5, 7}
        assert out["chem21_unavailable_reason"] not in {
            "flash_point_missing", "safety_score_missing",
        }
    missing = safety.score_chem21_she("bin", inputs=_inputs(flash_point_c=None))
    assert missing["chem21_safety_score"] is None
    assert missing["chem21_unavailable_reason"] == "flash_point_missing"


def _chem21_sort_ids(routes, direction: str) -> list[str]:
    def sort_key(route):
        value = tea._planner_route_metric(route, "max_stage_chem21_safety")
        missing = value is None
        original = int(route.get("rank") or 0)
        if direction == "max":
            return (missing, -(value or 0.0), original)
        return (missing, value if value is not None else math.inf, original)

    return [route["id"] for route in sorted(routes, key=sort_key)]


def test_planner_chem21_direction_must_fire():
    routes = [
        {
            "id": "A", "rank": 1,
            "steps": [
                {"chem21_safety_score": 2},
                {"chem21_safety_score": 3},
            ],
        },
        {
            "id": "B", "rank": 2,
            "steps": [
                {"chem21_safety_score": 7},
                {"chem21_safety_score": 8},
            ],
        },
    ]
    assert tea._PLANNER_SORT_OBJECTIVES["max_stage_chem21_safety"] == "min"
    assert tea._PLANNER_SORT_OBJECTIVES["max_stage_chem21_worst"] == "min"
    assert "min_stage_chem21_safety" not in tea._PLANNER_SORT_OBJECTIVES
    registered = tea._PLANNER_SORT_OBJECTIVES["max_stage_chem21_safety"]
    assert _chem21_sort_ids(routes, registered) == ["A", "B"]
    inverted = _chem21_sort_ids(routes, "max")
    assert inverted == ["B", "A"]
    assert inverted != _chem21_sort_ids(routes, registered)


def test_green_screen_g_score_default_and_explicit_are_ident():
    omitted = parse_tool_result(safety.screen_green_solvent_candidates(
        **_GREEN_IDENT_QUERY,
    ))
    explicit = parse_tool_result(safety.screen_green_solvent_candidates(
        **_GREEN_IDENT_QUERY, metric="g_score",
    ))
    omitted_blob = json.dumps(omitted, sort_keys=True, default=str).encode()
    explicit_blob = json.dumps(explicit, sort_keys=True, default=str).encode()
    assert hashlib.sha256(omitted_blob).hexdigest() == _GREEN_IDENT_SHA256
    assert hashlib.sha256(explicit_blob).hexdigest() == _GREEN_IDENT_SHA256
    assert "metric" not in omitted["data"]
    assert "chem21_safety_score" not in json.dumps(omitted["data"])


def test_green_screen_chem21_metric_stamps_eligible_set():
    payload = parse_tool_result(safety.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3,
        metric="chem21_safety",
    ))["data"]
    assert payload["success"] is True
    assert payload["metric"] == "chem21_safety"
    assert "eligible_candidate_count" in payload
    assert payload["eligible_candidate_count"] >= 1
    assert "maximum_chem21_score" in payload
    ranked = payload["ranked_candidates"]
    scores = [row["chem21_safety_score"] for row in ranked]
    assert scores == sorted(scores)
    assert all(row.get("g_score") is None or row["g_score"] != row["chem21_safety_score"]
               for row in ranked)


def test_schema_count_stays_24():
    assert len(tool_schemas()) == 24
    names = {item["name"] for item in tool_schemas()}
    assert "score_chem21_she" not in names
    assert "estimate_thermal_properties" not in names


_SUBSTITUTION_IDENT_QUERY = dict(
    feed_polymers=["LDPE", "PP"],
    route_steps=[{"dissolved_polymer": "LDPE", "solvent": "Toluene", "temperature_c": 80}],
)
_SUBSTITUTION_IDENT_SHA256 = (
    "a3ae9073b265c57e7c990c084e1d6686ded77b74334f54de6a9829f576909ee6"
)


def test_packed_table6_114_vs_144_must_fire():
    recommended = {
        "chem21_safety_score": 1,
        "chem21_health_score": 1,
        "chem21_environment_score": 4,
    }
    problematic = {
        "chem21_safety_score": 1,
        "chem21_health_score": 4,
        "chem21_environment_score": 4,
    }
    assert safety._chem21_ranking(1, 1, 4)[0] == "recommended"
    assert safety._chem21_ranking(1, 4, 4)[0] == "problematic"
    assert safety._chem21_ranking(7, 1, 1)[0] == "problematic"
    assert safety._chem21_ranking(6, 6, 1)[0] == "problematic"
    assert safety._chem21_worst(recommended) == 4
    assert safety._chem21_worst(problematic) == 14


def _chem21_worst_sort_ids(routes):
    def sort_key(route):
        value = tea._planner_route_metric(route, "max_stage_chem21_worst")
        missing = value is None
        original = int(route.get("rank") or 0)
        return (missing, value if value is not None else math.inf, original)
    return [route["id"] for route in sorted(routes, key=sort_key)]


def test_packed_swapped_original_rank_must_fire():
    def make(rank_a: int, rank_b: int):
        return [
            {
                "id": "A", "rank": rank_a,
                "steps": [{
                    "chem21_safety_score": 1,
                    "chem21_health_score": 1,
                    "chem21_environment_score": 4,
                }],
            },
            {
                "id": "B", "rank": rank_b,
                "steps": [{
                    "chem21_safety_score": 1,
                    "chem21_health_score": 4,
                    "chem21_environment_score": 4,
                }],
            },
        ]
    assert _chem21_worst_sort_ids(make(1, 2)) == ["A", "B"]
    assert _chem21_worst_sort_ids(make(2, 1)) == ["A", "B"]


def test_table6_disagrees_with_max_on_127_of_1000():
    n = 0
    for s in range(1, 11):
        for h in range(1, 11):
            for e in range(1, 11):
                word, _ = safety._chem21_ranking(s, h, e)
                by_max, _ = safety._chem21_ranking(max(s, h, e), 1, 1)
                if word != by_max:
                    n += 1
    assert n == 127


def test_substitution_default_and_g_score_are_ident():
    omitted = parse_tool_result(safety.screen_route_solvent_substitutions(
        **_SUBSTITUTION_IDENT_QUERY,
    ))
    explicit = parse_tool_result(safety.screen_route_solvent_substitutions(
        **_SUBSTITUTION_IDENT_QUERY, metric="g_score",
    ))
    omitted_blob = json.dumps(omitted, sort_keys=True, default=str).encode()
    explicit_blob = json.dumps(explicit, sort_keys=True, default=str).encode()
    assert hashlib.sha256(omitted_blob).hexdigest() == _SUBSTITUTION_IDENT_SHA256
    assert hashlib.sha256(explicit_blob).hexdigest() == _SUBSTITUTION_IDENT_SHA256
    assert "metric" not in omitted["data"]


def test_planner_route_point_stamps_table6_rule():
    route = {
        "sequence": ["PS"],
        "solvent_mapping": {},
        "complete": True,
        "bottleneck_selectivity_pct": 1.0,
        "peak_temperature_c": 80.0,
        "steps": [{
            "chem21_safety_score": 1,
            "chem21_health_score": 1,
            "chem21_environment_score": 4,
        }],
    }
    point = tea._planner_route_point(
        route, handle="h", original_rank=1, new_rank=1,
        objective="max_stage_chem21_worst", x_metric=None, y_metric=None,
    )
    assert point["chem21_ranking_rule"] == "table6_band_then_max"
    assert point["max_stage_chem21_worst"] == 4


_SEVEN = [
    "H301: synthetic statement one",   "H302: synthetic statement two",
    "H311: synthetic statement three", "H312: synthetic statement four",
    "H315: synthetic statement five",  "H319: synthetic statement six",
    "H331: synthetic statement seven",
]


def test_v2_schema_statements_never_truncated():
    from dissolve import thermodynamics as thermo
    for key in thermo._solvent_admission_rows():
        out = safety.score_chem21_she(key)
        assert out["chem21_inputs"]["statements_truncated"] is False


def test_truncation_schema_must_fire_two_sided():
    v1_shaped = safety.score_chem21_she(
        "schema-probe",
        inputs=_inputs(flash_point_c=20.0, statements=_SEVEN),
    )
    assert v1_shaped["chem21_inputs"]["statements_truncated"] is True
    v2_shaped = safety.score_chem21_she(
        "schema-probe",
        inputs=_inputs(
            flash_point_c=20.0, statements=_SEVEN, ghs_basis="clp_echa",
        ),
    )
    assert v2_shaped["chem21_inputs"]["statements_truncated"] is False


def test_ghs_basis_distribution_production():
    from collections import Counter
    from dissolve import thermodynamics as thermo
    counts = Counter()
    for key in thermo._solvent_admission_rows():
        out = safety.score_chem21_she(key)
        counts[out["chem21_inputs"].get("ghs_basis")] += 1
    assert counts["clp_echa"] == 749
    assert counts["none"] == 27
    assert counts["NITE-CMC"] == 7
    assert counts["Hazardous Substances Data Bank (HSDB)"] == 3
    assert sum(counts.values()) == 786


def test_nonbasis_severe_count_production():
    from dissolve import thermodynamics as thermo
    n = 0
    for key in thermo._solvent_admission_rows():
        out = safety.score_chem21_she(key)
        if out["chem21_inputs"].get("nonbasis_severe"):
            n += 1
    assert n == 392


def test_ethanol_nonbasis_disclosure():
    out = safety.score_chem21_she("ethanol")
    rows = out["chem21_inputs"]["nonbasis_severe"]
    codes = [item["code"] if isinstance(item, dict) else item for item in rows]
    assert codes == ["H340", "H350", "H360", "H372"]
    for item in rows:
        sources = item["sources"] if isinstance(item, dict) else []
        assert sources == ["NITE-CMC"]


# --- worst-of-three is the DEFAULT CHEM21 metric (owner 2026-08-30) ---

def _sha(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def test_chem21_alias_is_worst_of_three_on_both_screens():
    q = dict(feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3)
    alias = parse_tool_result(safety.screen_green_solvent_candidates(**q, metric="chem21"))["data"]
    worst = parse_tool_result(safety.screen_green_solvent_candidates(**q, metric="chem21_worst"))["data"]
    assert alias["success"] is True and alias["metric"] == "chem21_worst"
    assert _sha(alias) == _sha(worst)
    saf = parse_tool_result(safety.screen_green_solvent_candidates(**q, metric="chem21_safety"))["data"]
    assert _sha(alias) != _sha(saf)          # counter-case: the alias is NOT the Safety sub-score


def test_chem21_alias_does_not_touch_the_g_score_default():
    omitted = parse_tool_result(safety.screen_green_solvent_candidates(**_GREEN_IDENT_QUERY))
    blob = json.dumps(omitted, sort_keys=True, default=str).encode()
    assert hashlib.sha256(blob).hexdigest() == _GREEN_IDENT_SHA256


def test_planner_chem21_alias_resolves_to_worst_not_a_third_objective():
    assert "max_stage_chem21" not in tea._PLANNER_SORT_OBJECTIVES
    assert tea._PLANNER_OBJECTIVE_ALIASES["max_stage_chem21"] == "max_stage_chem21_worst"
    routes = [
        {"id": "A", "rank": 1, "steps": [{"chem21_safety_score": 1, "chem21_health_score": 1, "chem21_environment_score": 4}]},
        {"id": "B", "rank": 2, "steps": [{"chem21_safety_score": 1, "chem21_health_score": 4, "chem21_environment_score": 4}]},
    ]
    a = tea._planner_route_metric(routes[0], "max_stage_chem21_worst")
    b = tea._planner_route_metric(routes[1], "max_stage_chem21_worst")
    assert (a, b) == (4.0, 14.0)


def test_agent_surface_names_worst_of_three_as_the_default_chem21_metric():
    from dissolve.agent_tools import tool_schemas
    schemas = {t["name"]: t for t in tool_schemas()}
    assert len(schemas) == 24
    for name in ("screen_green_solvent_candidates", "screen_route_solvent_substitutions"):
        enum = schemas[name]["parameters"]["properties"]["metric"].get("enum")
        assert enum == ["g_score", "chem21", "chem21_safety", "chem21_worst"], (name, enum)
        assert "worst of Safety/Health/Environment" in schemas[name]["description"]
    assert "max_stage_chem21 = CHEM21 worst" in schemas["rank_landscape"]["description"]


def test_unknown_metric_still_refuses():
    out = parse_tool_result(safety.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3, metric="chem21_mean"))["data"]
    assert out["success"] is False and out["error_code"] == "invalid_metric"
