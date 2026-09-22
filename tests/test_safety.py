"""Safety tests."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import pytest
from dissolve.agent_tools import tool_schemas
from dissolve import safety, tea
from dissolve.contracts import parse_tool_result
import inspect
import os
import urllib.parse
from dissolve.agent_tools import PUBCHEM, source_basis_for
from dissolve import registry, safety
from dissolve.cli import EXPECTED_REGISTRY_NAMES, doctor_report
from dissolve.session import bind_tool_session, new_session, store_handle
from dissolve import separation
import copy
import io
import re
from rich.console import Console
from dissolve.cli import (
    CliApp,
    PUBLISHED_HAZARD_METHODS_COPY,
    doctor_report,
    format_published_hazard_methods_copy,
    published_hazard_methods_doctor_check,
)


# --- from test_chem21_she.py: CHEM21 SH&E scorer and G-score-alternative rerank (spec v2).
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


# --- from test_pubchem_snapshot.py: PubChem snapshot cache and explicit CID fetch. Not a test_safety*.py inventory.
_SNAPSHOT = Path.home() / "dissolve-v12-audit/safety/pubchem_safety_snapshot.v2.duckdb"


_SNAPSHOT_SHA256 = (
    "9f4082e0844ab18698fd229986d215fc7403767961c53bc5ac548875daaae0f7"
)


_LIVE_HEADINGS = (
    "Flash Point", "Autoignition Temperature", "Vapor Pressure",
    "GHS Classification", "Non-Human Toxicity Values",
    "Environmental Biodegradation",
    "NIOSH Recommendations", "OSHA Standards",
)


_FETCHED_AT = "2026-08-28T21:29:23.217777+00:00"


_TOLUENE = "Toluene"


_TOLUENE_CID = 1140


_MISS_CID = 1


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _forbid_network(monkeypatch):
    def _raise(_url: str):
        raise RuntimeError("network forbidden")

    monkeypatch.setattr(safety, "_request_json", _raise)
    safety._heading.cache_clear()
    safety._pubchem.cache_clear()


def _empty_payload(_url: str) -> dict:
    parsed = urllib.parse.urlparse(_url)
    heading = urllib.parse.parse_qs(parsed.query).get("heading", [None])[0]
    if heading == "Biodegradation":
        raise AssertionError("CID path must not fetch heading Biodegradation")
    return {}


def test_registry_keeps_cid_tool_after_thermal_estimator_retirement():
    names = {tool.name for tool in registry.REGISTRY}
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert names == EXPECTED_REGISTRY_NAMES
    assert "fetch_solvent_safety_by_cid" in names
    assert "estimate_thermal_properties" not in names
    assert "fetch_solvent_safety_by_cid" not in PUBCHEM
    assert safety._HEADINGS == _LIVE_HEADINGS
    assert "Biodegradation" not in safety._HEADINGS


def test_cached_card_via_registry_is_snapshot_with_network_forbidden(monkeypatch):
    _forbid_network(monkeypatch)
    raw = registry.call(
        "get_solvent_safety_card",
        solvent_name=_TOLUENE,
        include_pubchem=True,
    )
    payload = _data(raw)
    assert payload["success"] is True
    profile = payload["safety_profile"]
    physical = profile["physical_properties"]
    assert physical["flash_point_c"] is not None
    assert profile["ghs"].get("signal_word")
    assert profile["occupational_exposure_limits"]
    assert profile["toxicity"]["biodegradation"]
    origin = payload["field_origin"]
    for key in (
        "flash_point_c", "ghs", "occupational_exposure_limits", "biodegradation",
    ):
        assert origin[key]["source"] == "snapshot"
        assert origin[key]["fetched_at"] == _FETCHED_AT
    assert profile["provenance"]["pubchem_source"] == "snapshot"
    assert "source: live" not in json.dumps(payload)
    assert source_basis_for(
        "get_solvent_safety_card", payload, {"include_pubchem": True},
    ) == "safety_local"


def test_screens_and_compare_complete_offline(monkeypatch):
    _forbid_network(monkeypatch)
    green = _data(registry.call(
        "screen_green_solvent_candidates",
        feed_polymers=["LDPE", "PP"],
        target_polymer="LDPE",
        limit=3,
    ))
    assert green["success"] is True
    compare = _data(registry.call(
        "compare_solvent_safety_at_conditions",
        candidates=[{"solvent_name": _TOLUENE, "operating_temp_c": 80}],
        include_pubchem=True,
    ))
    assert compare["success"] is True
    assert "source: live" not in json.dumps(compare)
    route = _data(registry.call(
        "screen_route_solvent_substitutions",
        feed_polymers=["LDPE", "PP"],
        route_steps=[{
            "dissolved_polymer": "LDPE",
            "solvent": _TOLUENE,
            "temperature_c": 80,
        }],
        include_pubchem=True,
    ))
    assert route["success"] is True
    assert "source: live" not in json.dumps(route)


def test_cid_outside_snapshot_is_named_miss(monkeypatch):
    _forbid_network(monkeypatch)
    monkeypatch.setattr(
        safety, "_local_properties",
        lambda query: {"name": query, "cid": _MISS_CID},
    )
    payload = _data(registry.call(
        "get_solvent_safety_card",
        solvent_name="not-in-snapshot",
        include_pubchem=True,
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "safety_snapshot_miss"
    assert payload["cid"] == _MISS_CID
    assert str(_MISS_CID) in payload.get("error", "")
    assert "safety_profile" not in payload


def test_decoy_same_size_file_is_digest_mismatch(tmp_path, monkeypatch):
    decoy = tmp_path / "decoy.duckdb"
    decoy.write_bytes(b"\x00" * _SNAPSHOT.stat().st_size)
    assert decoy.stat().st_size == _SNAPSHOT.stat().st_size
    assert hashlib.sha256(decoy.read_bytes()).hexdigest() != _SNAPSHOT_SHA256
    monkeypatch.setenv("DISSOLVE_SAFETY_SNAPSHOT", str(decoy))
    payload = _data(registry.call(
        "get_solvent_safety_card",
        solvent_name=_TOLUENE,
        include_pubchem=True,
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "snapshot_digest_mismatch"
    assert payload.get("digest") != _SNAPSHOT_SHA256


def test_snapshot_internal_mismatch_count_for_all_987():
    loaded = safety._load_snapshot()
    connection = loaded["connection"]
    typed_rows = connection.execute("SELECT * FROM pubchem_safety").fetchall()
    columns = loaded["columns"]
    mismatches = 0
    for values in typed_rows:
        record = dict(zip(columns, values))
        cid = int(record["cid"])
        served = safety._snapshot_pubchem(cid)
        typed_map = {
            "flash_point_c": record.get("flash_point_c"),
            "autoignition_c": record.get("autoignition_c"),
            "vapor_pressure_kpa": record.get("vapor_pressure_kpa"),
            "ghs_signal_word": record.get("ghs_signal_word"),
            "ghs_pictograms": safety._json_field(record.get("ghs_pictograms")) or [],
            "ghs_hazard_statements": safety._json_field(
                record.get("ghs_hazard_statements")
            ) or [],
            "occupational_exposure_limits": safety._json_field(
                record.get("occupational_exposure_limits")
            ) or [],
            "ld50_values": safety._json_field(record.get("ld50_values")) or [],
            "lc50_values": safety._json_field(record.get("lc50_values")) or [],
            "biodegradation": safety._json_field(record.get("biodegradation")) or [],
        }
        if isinstance(typed_map["biodegradation"], str):
            typed_map["biodegradation"] = [typed_map["biodegradation"]]
        pairs = (
            ("flash_point_c", served.get("flash_point_c"), typed_map["flash_point_c"]),
            ("autoignition_c", served.get("autoignition_c"), typed_map["autoignition_c"]),
            (
                "vapor_pressure_kpa",
                served.get("vapor_pressure_kpa"),
                typed_map["vapor_pressure_kpa"],
            ),
            (
                "ghs_signal_word",
                (served.get("ghs") or {}).get("signal_word"),
                typed_map["ghs_signal_word"],
            ),
            (
                "ghs_pictograms",
                (served.get("ghs") or {}).get("pictograms") or [],
                typed_map["ghs_pictograms"] if isinstance(typed_map["ghs_pictograms"], list) else [],
            ),
            (
                "ghs_hazard_statements",
                (served.get("ghs") or {}).get("hazard_statements") or [],
                typed_map["ghs_hazard_statements"] if isinstance(typed_map["ghs_hazard_statements"], list) else [],
            ),
            (
                "occupational_exposure_limits",
                served.get("occupational_exposure_limits") or [],
                typed_map["occupational_exposure_limits"] if isinstance(typed_map["occupational_exposure_limits"], list) else [],
            ),
            (
                "ld50_values",
                served.get("ld50_values") or [],
                typed_map["ld50_values"] if isinstance(typed_map["ld50_values"], list) else [],
            ),
            (
                "lc50_values",
                served.get("lc50_values") or [],
                typed_map["lc50_values"] if isinstance(typed_map["lc50_values"], list) else [],
            ),
            (
                "biodegradation",
                served.get("biodegradation") or [],
                typed_map["biodegradation"] if isinstance(typed_map["biodegradation"], list) else [],
            ),
        )
        for _name, got, want in pairs:
            if _empty(got) and _empty(want):
                continue
            if _empty(want) and not _empty(got):
                mismatches += 1
                continue
            if not _same(got, want):
                mismatches += 1
    assert mismatches == 0


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _same(got, want) -> bool:
    if isinstance(got, (int, float)) and isinstance(want, (int, float)):
        return math.isclose(float(got), float(want), rel_tol=1e-9, abs_tol=1e-9)
    return got == want


def test_biodegradation_after_rename_vs_broken_heading(monkeypatch):
    _forbid_network(monkeypatch)
    payload = _data(registry.call(
        "get_solvent_safety_card",
        solvent_name=_TOLUENE,
        include_pubchem=True,
    ))
    served = payload["safety_profile"]["toxicity"]["biodegradation"]
    assert served
    loaded = safety._load_snapshot()
    broken = loaded["connection"].execute(
        "SELECT biodegradation_broken_heading FROM pubchem_safety WHERE cid = ?",
        [_TOLUENE_CID],
    ).fetchone()[0]
    parsed = safety._json_field(broken) or []
    assert parsed == [] or _empty(parsed)


def test_cid_path_cassette_fetches_eight_names_never_biodegradation(monkeypatch):
    seen: list[str] = []

    def fake_request(url: str) -> dict:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        heading = qs.get("heading", [None])[0]
        seen.append(heading)
        assert heading != "Biodegradation"
        return _empty_payload(url)

    monkeypatch.setattr(safety, "_request_json", fake_request)
    safety._heading.cache_clear()
    safety._pubchem.cache_clear()
    before = hashlib.sha256(_SNAPSHOT.read_bytes()).hexdigest()
    payload = _data(registry.call("fetch_solvent_safety_by_cid", cid=_TOLUENE_CID))
    after = hashlib.sha256(_SNAPSHOT.read_bytes()).hexdigest()
    assert payload["success"] is True
    assert payload["persisted"] is False
    assert after == before == _SNAPSHOT_SHA256
    assert len(seen) == 8
    assert set(seen) == set(_LIVE_HEADINGS)
    assert "Biodegradation" not in seen
    assert payload["safety_profile"]["provenance"]["pubchem_source"] == "live"
    assert payload["safety_profile"]["provenance"]["pubchem_fetched_at"]
    assert payload["field_origin"] or payload["safety_profile"]["provenance"]["pubchem_source"] == "live"
    signature = inspect.signature(safety.fetch_solvent_safety_by_cid)
    assert list(signature.parameters) == ["cid"]
    missing = _data(registry.call("fetch_solvent_safety_by_cid"))
    assert missing["success"] is False
    assert missing["error_code"] == "missing_cid"
    invalid = _data(registry.call("fetch_solvent_safety_by_cid", cid="toluene"))
    assert invalid["success"] is False
    assert invalid["error_code"] == "invalid_cid"


def test_doctor_adds_snapshot_pin_after_registry_only(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    monkeypatch.delenv("DISSOLVE_SAFETY_SNAPSHOT", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    names = [c["name"] for c in report["checks"]]
    assets_at = names.index("Scientific assets")
    assert names[assets_at] == "Scientific assets"
    assert names[assets_at + 1] == "Published hazard Methods"
    assert names[assets_at + 2] == "Tool registry"
    assert names[assets_at + 3] == "PubChem safety snapshot"
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["Scientific assets"]["checked"] == 6
    assert by_name["Scientific assets"]["detail"] == "6 checksums verified"
    assert by_name["Tool registry"]["detail"] == "29 registered names"
    snap = by_name["PubChem safety snapshot"]
    assert snap["status"] == "pass"
    assert snap["digest"] == _SNAPSHOT_SHA256
    assert snap["expected"] == _SNAPSHOT_SHA256
    assert os.path.expanduser(snap["path"]) == str(_SNAPSHOT)
    methods = by_name["Published hazard Methods"]
    assert methods["detail"] == (
        "n=990 GSK=130 GreenSolventDB=840 neither=20 both=128 "
        "MAE=0.30 signed=+0.01 floor=6.0 unsourced"
    )
    assert methods["n"] == 990
    assert methods["served_gsk"] == 130
    assert methods["served_green"] == 840
    assert methods["served_neither"] == 20
    assert methods["both_table_hits"] == 128
    assert methods["mae_2dp"] == 0.3
    assert methods["default_minimum_g_score"] == 6.0


# --- from test_evaluate_safety_standing.py: evaluate / lookup comparison rows stamp safety_standing.
def _data_evaluate_safety_standing(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("safety standing stamp must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _record_by_label(label: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == label
    )


def _public_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def test_evaluate_comparison_rows_stamp_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data_evaluate_safety_standing(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(c1)], engine_mode="cache",
    ))
    assert payload.get("success") is True
    rows = payload["comparison_rows"]
    assert len(rows) == 1
    assert rows[0]["safety_standing"] == {"status": "not_requested"}
    assert rows[0]["safety_standing"]["status"] != "evaluated"


def test_evaluate_process_evaluate_stamps_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data_evaluate_safety_standing(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload["comparison_rows"][0]["safety_standing"] == {
        "status": "not_requested",
    }


def test_two_row_evaluate_stamps_every_comparison_row(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    payload = _data_evaluate_safety_standing(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(c1), _public_from_record(c2)],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    rows = payload["comparison_rows"]
    assert len(rows) == 2
    assert {row["energy_case"] for row in rows} == {"C1", "C2"}
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}
    assert rows[0]["energy_case"] != rows[1]["energy_case"]


def test_lookup_comparison_rows_stamp_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data_evaluate_safety_standing(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        energy_cases=["C1", "C2"],
    ))
    assert payload.get("success") is True
    rows = payload["comparison_rows"]
    assert len(rows) == 2
    cases = {row["energy_case"] for row in rows}
    assert cases == {"C1", "C2"}
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}


def test_evaluate_process_lookup_stamps_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data_evaluate_safety_standing(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "energy_cases": ["C1"],
        },
    ))
    assert payload.get("success") is True
    assert payload["comparison_rows"][0]["safety_standing"] == {
        "status": "not_requested",
    }


def test_rank_copies_producer_not_requested(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data_evaluate_safety_standing(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(c1), _public_from_record(c2)],
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        ranked = _data_evaluate_safety_standing(tea.rank_landscape(handle=handle))
    assert first["comparison_rows"][0]["safety_standing"]["status"] == (
        "not_requested"
    )
    assert ranked.get("success") is True
    for point in ranked["landscape_points"]:
        assert point["safety_standing"] == {"status": "not_requested"}


def test_illegal_bound_standing_is_not_copied():
    assert tea._comparison_safety_standing({
        "safety_standing": {"status": "fail", "excluded": True},
    }) == {"status": "not_requested"}
    evaluated = {
        "status": "evaluated",
        "safety_profile": {"ghs_signal_word": "Danger"},
    }
    assert tea._comparison_safety_standing(
        {"safety_standing": evaluated},
    ) == evaluated
    assert tea._comparison_safety_standing(None) == {
        "status": "not_requested",
    }


def test_sensitivity_rows_stamp_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data_evaluate_safety_standing(tea.analyze_tea_sensitivity(
        _public_from_record(c1),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    rows = payload["sensitivity_rows"]
    assert len(rows) >= 2
    assert "safety_standing" not in payload
    values = {row["value"] for row in rows}
    assert len(values) >= 2
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}
        assert row["safety_standing"]["status"] != "evaluated"


def test_evaluate_process_sensitivity_stamps_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data_evaluate_safety_standing(tea.evaluate_process(
        mode="sensitivity",
        process_config=_public_from_record(c1),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert "safety_standing" not in payload
    rows = payload["sensitivity_rows"]
    assert len(rows) >= 2
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}
    assert rows[0]["value"] != rows[1]["value"]


def test_incomplete_sensitivity_does_not_stamp_rows(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    cfg = c1["config"]
    payload = _data_evaluate_safety_standing(tea.evaluate_process(
        mode="sensitivity",
        process_config={
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        },
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert "sensitivity_rows" not in payload


def test_sensitivity_row_helper_copies_legal_standing():
    c1 = _record_by_label("ldpe-route-c1")
    fields = tea._sensitivity_row_process_fields(c1["config"])
    assert fields["safety_standing"] == {"status": "not_requested"}
    evaluated = {
        "status": "evaluated",
        "safety_profile": {"ghs_signal_word": "Danger"},
    }
    copied = tea._sensitivity_row_process_fields(
        c1["config"],
        {"safety_standing": evaluated},
    )
    assert copied["safety_standing"] == evaluated
    skipped = tea._sensitivity_row_process_fields(
        c1["config"],
        {"safety_standing": {"status": "fail", "excluded": True}},
    )
    assert skipped["safety_standing"] == {"status": "not_requested"}
    assert "excluded" not in skipped["safety_standing"]


# --- from test_finish_wash_safe.py: v3 §8 commit 2: finish() is wash-safe. Fixture plus omitted-contaminants inertness.
def _dissolution(polymer: str, solvent: str, temperature_c: float, **extra) -> dict:
    return {
        "dissolved_polymer": polymer,
        "solvent": solvent,
        "temperature_c": temperature_c,
        "selectivity_pct": extra.get("selectivity_pct", 80.0),
        "target_solubility_pct": extra.get("target_solubility_pct", 20.0),
        "off_target_solubilities_pct": extra.get(
            "off_target_solubilities_pct", {"PET": 0.5},
        ),
    }


def _wash(solvent: str, temperature_c: float) -> dict:
    return {
        "step_kind": "wash",
        "path": "leaching",
        "solvent": solvent,
        "temperature_c": temperature_c,
        "contaminants_targeted": ["di-(2-ethylhexyl) phthalate (DEHP)"],
        "passes": True,
        "feed_state_at_step": {
            "polymers": ["LDPE", "PET", "EVOH"],
            "contaminants": ["di-(2-ethylhexyl) phthalate (DEHP)"],
            "inventory_model": "none",
        },
    }


def test_wash_bearing_fixture_does_not_keyerror_or_drop():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PET",
            "unresolved_polymers": [],
            "steps": [
                _wash("toluene", 25.0),
                _dissolution("LDPE", "cyclohexanol", 135.0),
                _dissolution("EVOH", "water", 80.0, selectivity_pct=40.0),
            ],
        },
        feed_polymers=["LDPE", "PET", "EVOH"],
    )
    assert finished["sequence"] == ["wash", "LDPE", "EVOH", "PET"]
    assert "wash" not in finished["solvent_mapping"]
    assert finished["solvent_mapping"] == {
        "LDPE": "cyclohexanol",
        "EVOH": "water",
    }
    assert finished["bottleneck_selectivity_pct"] == 40.0
    assert finished["bottleneck_target_solubility_pct"] == 20.0
    assert finished["peak_temperature_c"] == 135.0
    assert finished["steps"][0]["step_kind"] == "wash"
    assert finished["steps"][1]["step_kind"] == "dissolution"
    assert finished["steps"][2]["step_kind"] == "dissolution"


def test_two_washes_are_numbered_in_step_order():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PET",
            "steps": [
                _wash("toluene", 25.0),
                _dissolution("LDPE", "cyclohexanol", 135.0),
                _wash("acetone", 30.0),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["sequence"] == ["wash 1", "LDPE", "wash 2", "PET"]
    assert list(finished["solvent_mapping"]) == ["LDPE"]


def test_peak_temperature_includes_wash():
    finished = separation.finish_route(
        {
            "complete": False,
            "final_residue": None,
            "steps": [
                _wash("toluene", 160.0),
                _dissolution("LDPE", "cyclohexanol", 105.0),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["peak_temperature_c"] == 160.0
    assert finished["sequence"] == ["wash", "LDPE"]


def test_dissolution_only_route_keeps_polymer_sequence():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PP",
            "steps": [
                _dissolution("LDPE", "toluene", 105.0),
            ],
        },
        feed_polymers=["LDPE", "PP"],
    )
    assert finished["sequence"] == ["LDPE", "PP"]
    assert finished["solvent_mapping"] == {"LDPE": "toluene"}
    assert finished["steps"][0]["step_kind"] == "dissolution"


def _render_tokens(tokens: list[str], *, complete: bool) -> str:
    parts: list[str] = []
    last = len(tokens) - 1
    for index, token in enumerate(tokens):
        if str(token).startswith("wash"):
            parts.append(str(token))
        elif complete and index == last:
            parts.append(f"residual {token}")
        else:
            parts.append(f"dissolve {token}")
    return " → ".join(parts)


def test_tokens_render_the_spec_sentence():
    finished = separation.finish_route(
        {
            "complete": True,
            "final_residue": "PET",
            "steps": [
                _wash("toluene", 25.0),
                _dissolution("LDPE", "cyclohexanol", 135.0),
                _dissolution("EVOH", "water", 80.0),
            ],
        },
        feed_polymers=["LDPE", "PET", "EVOH"],
    )
    assert _render_tokens(finished["sequence"], complete=True) == (
        "wash → dissolve LDPE → dissolve EVOH → residual PET"
    )


def test_wash_without_temperature_does_not_keyerror():
    finished = separation.finish_route(
        {
            "complete": False,
            "final_residue": None,
            "steps": [
                {"step_kind": "wash", "solvent": "toluene"},
                _dissolution("LDPE", "toluene", 105.0, selectivity_pct=70.0),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["sequence"] == ["wash", "LDPE"]
    assert finished["peak_temperature_c"] == 105.0
    assert finished["bottleneck_selectivity_pct"] == 70.0


def test_wash_selectivity_does_not_enter_bottlenecks():
    finished = separation.finish_route(
        {
            "complete": False,
            "steps": [
                {
                    "step_kind": "wash",
                    "solvent": "toluene",
                    "temperature_c": 25.0,
                    "selectivity_pct": 1.0,
                    "target_solubility_pct": 1.0,
                    "off_target_solubilities_pct": {"PET": 99.0},
                },
                _dissolution(
                    "LDPE",
                    "toluene",
                    105.0,
                    selectivity_pct=80.0,
                    target_solubility_pct=20.0,
                    off_target_solubilities_pct={"PET": 0.5},
                ),
            ],
        },
        feed_polymers=["LDPE", "PET"],
    )
    assert finished["bottleneck_selectivity_pct"] == 80.0
    assert finished["bottleneck_target_solubility_pct"] == 20.0
    assert finished["cumulative_off_target_burden_wt_pct_sum"] == 0.5
    assert "wash" not in finished["solvent_mapping"]


def test_planner_omitted_contaminants_still_emits_no_wash():
    payload = _data(separation.plan_multistage_separation(["LDPE", "PP"]))
    assert payload["success"] is True
    assert "contaminants" in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
    sequence = payload.get("best_sequence") or payload.get("sequence")
    assert sequence
    assert all(not str(item).startswith("wash") for item in sequence)
    mapping = payload.get("solvent_mapping") or {}
    assert "wash" not in mapping
    assert "positions_considered" not in payload
    assert all(
        item.get("path") is None and item.get("feed_state_at_step") is None
        for item in (payload.get("steps") or [])
        if isinstance(item, dict)
    )


# --- from test_cli_safety.py: HAZARD_METHODS_CLI_SPEC.v1: /safety print-only copy, not a score rewrite.
_ROOT = Path(__file__).resolve().parents[1]


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


_CENSUS_JSON = _ROOT / "src" / "dissolve" / "data" / "HAZARD_METHODS.v1.json"


_CENSUS_SHA256 = "8540e6fe2f3ee780989ea15beb1b9ce0fe2aac2c81a073e271fd073997ecb150"


_KEEP_OUTS = (
    # Card builder stamped CHEM21 SH&E (v2). Methods copy / duckdb / thermo stay.
    ("src/dissolve/data/safety.duckdb", "88ce0d09ac28de17045702a8a283de6610b5fe1ab33aa5f90bf6e98edfd75a74"),
    ("src/dissolve/data/HAZARD_METHODS.published.v1.md", "a3a74ccdfc47596bc8f335097056956224407e531d9a258cad0b2656f9e9b9f9"),
    ("src/dissolve/data/HAZARD_METHODS.v1.json", _CENSUS_SHA256),
)


_FENCE = (
    "safety  published Methods 5ec2521  measured 1d97a73\n"
    "enumerating_function=get_available_solvents  scope=all  origin=built_in  n=990\n"
    "served GSK=130  GreenSolventDB=840  neither=20\n"
    "both_table=128  leftover=1,2-dimethoxyethane (110-71-4), cis-decalin (493-01-6)\n"
    "average MAE=0.30  signed_mean=+0.01  green_lookup=LIMIT 1 no ORDER BY\n"
    "green_screen=screen_green_solvent_candidates  network=offline  floor=6.0 unsourced\n"
    "route=screen_route_solvent_substitutions  include_pubchem=True (default)\n"
    "card=get_solvent_safety_card  include_pubchem=True (default)\n"
)


def _strike_phrases() -> tuple[str, ...]:
    return (
        "562" + " screenable",
        "102 of the " + "562",
        "covers" + " 452",
        "8 have" + " neither",
        "100 solvents" + " where both",
        "differ by " + "0.28",
    )


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80, color_system=None)
    app = CliApp(
        session_id="safety-session",
        store_root=tmp_path,
        console=console,
        persist=False,
    )
    return app, buf


def _plain(buf: io.StringIO) -> str:
    return _ANSI.sub("", buf.getvalue())


def test_safety_prints_the_published_fence(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/safety") is False
    shown = _plain(buf)
    assert shown == _FENCE
    assert format_published_hazard_methods_copy() == _FENCE
    assert shown.count("\n") == 8
    assert shown.split("\n")[8] == ""


def test_safety_extra_token_is_usage(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/safety extra") is False
    shown = _plain(buf)
    assert "usage: /safety" in shown
    assert "enumerating_function=" not in shown
    assert "published Methods" not in shown


def test_safety_does_not_write_session(tmp_path, monkeypatch):
    app, _buf = _app(tmp_path, monkeypatch)
    before = copy.deepcopy(dict(app.session))
    app.handle_command("/safety")
    assert dict(app.session) == before
    assert "safety" not in app.session
    assert "g_score" not in app.session
    assert "include_pubchem" not in app.session


def test_frozen_copy_matches_census_json():
    raw = _CENSUS_JSON.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _CENSUS_SHA256
    payload = json.loads(raw)
    census = payload["census"]
    copy_row = PUBLISHED_HAZARD_METHODS_COPY
    assert copy_row["n"] == census["n"]
    assert copy_row["served_gsk"] == census["served_gsk"]
    assert copy_row["served_green"] == census["served_green"]
    assert copy_row["served_neither"] == census["served_neither"]
    assert copy_row["both_table_hits"] == census["both_table_hits"]
    assert copy_row["mae_2dp"] == census["mae_2dp"]
    assert copy_row["signed_mean_2dp"] == census["signed_mean_2dp"]
    assert copy_row["default_minimum_g_score"] == census["default_minimum_g_score"]
    assert copy_row["measured_on_builder_sha"] == payload["measured_on_builder_sha"]
    assert [key for key, _cas in copy_row["leftover"]] == [
        row["interp_key"] for row in census["gsk_only"]
    ]
    assert [cas for _key, cas in copy_row["leftover"]] == [
        row["cas"] for row in census["gsk_only"]
    ]


def test_fence_and_incoming_omit_strike_phrases():
    phrases = _strike_phrases()
    blob = _FENCE + Path(__file__).read_text(encoding="utf-8")
    cli_src = (_ROOT / "src" / "dissolve" / "cli.py").read_text(encoding="utf-8")
    for phrase in phrases:
        assert phrase not in _FENCE
        assert phrase not in blob
        assert phrase not in cli_src


def test_banner_names_safety_after_solvents(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.banner()
    shown = _plain(buf)
    assert "/safety" in shown
    solvents_at = shown.index("/solvents")
    safety_at = shown.index("/safety")
    contaminant_at = shown.index("/contaminant")
    assert solvents_at < safety_at < contaminant_at


def test_safety_helper_is_format_only():
    src = inspect.getsource(format_published_hazard_methods_copy)
    src += inspect.getsource(CliApp._handle_safety_command)
    assert "duckdb" not in src
    assert "_gscore" not in src
    assert "import " not in inspect.getsource(format_published_hazard_methods_copy)


def test_keep_outs_ident_parent_tree():
    for rel, digest in _KEEP_OUTS:
        path = _ROOT / rel
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


_DOCTOR_DETAIL = (
    "n=990 GSK=130 GreenSolventDB=840 neither=20 both=128 "
    "MAE=0.30 signed=+0.01 floor=6.0 unsourced"
)


def test_doctor_published_hazard_methods_after_assets(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    names = [c["name"] for c in report["checks"]]
    assets_at = names.index("Scientific assets")
    assert names[assets_at + 1] == "Published hazard Methods"
    assert names[assets_at + 2] == "Tool registry"
    assert names[:2] == ["Model provider", "Scientific assets"]


def test_doctor_published_hazard_methods_detail_and_facts(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Published hazard Methods")
    copy_row = PUBLISHED_HAZARD_METHODS_COPY
    assert check["status"] == "pass"
    assert check["detail"] == _DOCTOR_DETAIL
    assert check["n"] == copy_row["n"] == 990
    assert check["served_gsk"] == copy_row["served_gsk"] == 130
    assert check["served_green"] == copy_row["served_green"] == 840
    assert check["served_neither"] == copy_row["served_neither"] == 20
    assert check["both_table_hits"] == copy_row["both_table_hits"] == 128
    assert check["mae_2dp"] == copy_row["mae_2dp"] == 0.3
    assert check["signed_mean_2dp"] == copy_row["signed_mean_2dp"] == 0.01
    assert check["default_minimum_g_score"] == copy_row["default_minimum_g_score"] == 6.0
    assert check["measured_on_builder_sha"] == copy_row["measured_on_builder_sha"]
    assert check["census_json"] == _CENSUS_SHA256


def test_doctor_check_helper_is_format_only():
    src = inspect.getsource(published_hazard_methods_doctor_check)
    assert "connect" not in src
    assert "_gscore" not in src
    assert "dissolve.safety" not in src
    assert "import " not in src


def test_doctor_detail_omits_strike_phrases():
    for phrase in _strike_phrases():
        assert phrase not in _DOCTOR_DETAIL
