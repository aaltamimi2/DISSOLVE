"""Specific volume (1/rho) as a planner rerank axis: a TEA *proxy*, never a cost.
Every must-fire here is shown able to fail."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_tools import tool_schemas
from dissolve import separation, tea
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session, store_handle

KEY = "max_stage_specific_volume_l_per_kg"


def _data(raw):
    return parse_tool_result(raw)["data"]


def test_objective_registered_lower_is_better_and_alias_resolves():
    assert tea._PLANNER_SORT_OBJECTIVES[KEY] == "min"
    assert tea._PLANNER_OBJECTIVE_ALIASES["max_stage_specific_volume"] == KEY
    assert "max_stage_specific_volume" not in tea._PLANNER_SORT_OBJECTIVES   # alias, not a second key
    assert len(tool_schemas()) == 24


def test_table_loads_and_digest_is_checked_not_trusted(monkeypatch):
    tea._density_table.cache_clear()
    table = tea._density_table()
    assert table["digest"] == tea._DENSITY_TABLE_CONTENT_DIGEST
    assert table["by_key"]["toluene"]["rho_effective_kg_m3"] == pytest.approx(862.3, abs=1.0)
    # MUST-FIRE: a wrong pinned constant must refuse, even though the file is unchanged
    monkeypatch.setattr(tea, "_DENSITY_TABLE_CONTENT_DIGEST", "0" * 64)
    tea._density_table.cache_clear()
    with pytest.raises(tea.DensityTableRefuse) as err:
        tea._density_table()
    assert err.value.error_code == "density_table_digest_mismatch"
    tea._density_table.cache_clear()


def test_aggregator_takes_worst_stage_and_refuses_unsupported_solvents():
    tea._density_table.cache_clear()
    v_tol = tea._planner_route_metric({"steps": [{"solvent": "toluene"}]}, KEY)
    v_ccl4 = tea._planner_route_metric({"steps": [{"solvent": "ccl4"}]}, KEY)
    assert v_ccl4 == pytest.approx(1000 / 1583.7, rel=1e-3)
    assert v_tol > v_ccl4                                                    # lighter solvent = larger specific volume = worse
    both = tea._planner_route_metric({"steps": [{"solvent": "ccl4"}, {"solvent": "toluene"}]}, KEY)
    assert both == pytest.approx(v_tol)                                      # max over stages
    assert tea._planner_route_metric({"steps": [{"solvent": "naphthalene"}]}, KEY) is None     # solid at 25 C
    assert tea._planner_route_metric({"steps": [{"solvent": "not-a-solvent-xyz"}]}, KEY) is None
    assert tea._planner_route_metric({"steps": [{"specific_volume_l_per_kg": 1.5}]}, KEY) == 1.5


def test_unsupported_route_sorts_last_and_is_listed(monkeypatch):
    """MUST-FIRE: without the None-sorts-last contract a solid-only route would lead."""
    tea._density_table.cache_clear()
    routes = [
        {"rank": 1, "steps": [{"solvent": "naphthalene"}]},   # solid at 25 C -> None
        {"rank": 2, "steps": [{"solvent": "toluene"}]},
        {"rank": 3, "steps": [{"solvent": "ccl4"}]},
    ]
    excl = tea._planner_specific_volume_exclusions(routes)
    assert excl == [{"route_rank": 1, "solvent": "naphthalene", "reason": "density_unresolved:solid_at_25C"}]
    vals = [tea._planner_route_metric(r, KEY) for r in routes]
    order = sorted(range(3), key=lambda i: (vals[i] is None, vals[i] if vals[i] is not None else float("inf"), routes[i]["rank"]))
    assert [routes[i]["rank"] for i in order] == [3, 2, 1]                   # ccl4, toluene, then the unresolved route last
    # counter-case: if None were treated as 0 the solid route would lead
    naive = sorted(range(3), key=lambda i: (vals[i] or 0.0))
    assert [routes[i]["rank"] for i in naive][0] == 1


def test_live_sort_stamps_proxy_disclosure_and_still_refuses_economics():
    tea._density_table.cache_clear()
    session = new_session()
    with bind_tool_session(session):
        plan = _data(separation.plan_multistage_separation(
            feed_polymers=["PS", "HDPE", "PET"], top_k_routes=6, breadth=4))
        handle = store_handle(session, tool="plan_multistage_separation", source_basis="planner", data=plan)
        out = _data(tea.rank_landscape(source="planner_routes", handle=handle,
                                       operation="sort", objective="max_stage_specific_volume"))
        assert out["success"] is True and out["objective"] == KEY and out["objective_direction"] == "min"
        axis = out["specific_volume_axis"]
        assert axis["is_cost_metric"] is False
        assert "NOT MEASURED" not in axis["second_plant"]
        assert "86.5%" in axis["second_plant"] and "BELOW the pre-registered 90% bar" in axis["second_plant"]
        assert axis["evidence_n"] == 51 and axis["evidence_spearman_msp"] == pytest.approx(0.9925)
        # the coverage stamp is DERIVED: it must match a recount from the table
        import duckdb as _duckdb
        _con = _duckdb.connect(str(tea._DENSITY_TABLE_DEFAULT), read_only=True)
        _counts = dict(_con.execute(
            "select verdict, count(*) from density_validated group by 1").fetchall())
        _con.close()
        assert axis["table_coverage"].startswith("630 of 786")
        for name, count in _counts.items():
            if name not in ("validated", "predicted"):
                assert f"{count} {name}" in axis["table_coverage"], (name, axis["table_coverage"])
        assert "out-of-band" not in axis["table_coverage"]      # the falsified literal is gone
        assert "specific_volume_excluded_routes" in out
        pts = out["landscape_points"]
        vals = [p[KEY] for p in pts]
        finite = [v for v in vals if v is not None]
        assert finite == sorted(finite) and vals[:len(finite)] == finite     # ascending, None last
        assert all(p["specific_volume_rule"] == "max_stage_1000_over_rho_25C" for p in pts)
        assert all(isinstance(p["specific_volume_unresolved_stages"], int) for p in pts)
        assert sum(p["specific_volume_unresolved_stages"] for p in pts) == len(out["specific_volume_excluded_routes"])
        bad = _data(tea.rank_landscape(source="planner_routes", handle=handle,
                                       operation="sort", objective="msp_usd_per_kg"))
        assert bad["error_code"] == "not_applicable_in_source"
        g = _data(tea.rank_landscape(source="planner_routes", handle=handle,
                                     operation="sort", objective="min_stage_g_score"))
        assert g["success"] is True and "specific_volume_axis" not in g       # stamps only on this objective
