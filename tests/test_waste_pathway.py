"""optimize_waste_pathway tool and its data layer. The thermodynamic screen and the process simulation are stubbed."""
from __future__ import annotations

import json

import pytest

from dissolve import agent, waste_pathway as tool, waste_pathway_data as dl, waste_pathway_milp as wp
from dissolve.cli import EXPECTED_REGISTRY_NAMES

FEED = wp.Feed(8000.0, {"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1})
ROW = {
    "tci_usd": 46_000_000.0, "aoc_usd_per_yr": 1_200_000.0, "gwp_kg_co2e_per_kg": 0.5, "total_energy_mj_per_kg": 5.0,
    "water_consumed_m3_per_yr": 1000.0, "water_circulated_m3_per_yr": 80_000.0, "waste_generated_kg_per_yr": 12_000.0,
    "finance_interest": 0.08, "finance_years": 10.0, "engine_mode": "live",
}


def _spec(polymer="LDPE", solvent="p-Xylene", removed=frozenset(), mass_in=8000.0, percent=60.0, plant=None):
    return dl.StageSpec(removed, polymer, solvent, 100.0, mass_in, percent, plant_capacity_t=plant)


def test_capital_recovery_factor():
    assert dl.capital_recovery_factor(0.08, 10) == pytest.approx(0.1490295, rel=1e-6)
    assert dl.capital_recovery_factor(0.0, 10) == pytest.approx(0.1)
    with pytest.raises(ValueError):
        dl.capital_recovery_factor(0.08, 0)


def test_wash_stage_scales_per_kilogram_results_by_the_recovered_polymer():
    stage, source = dl.wash_stage_from_row(_spec(), ROW)
    assert source == "assumed"  # the row carries no recovered resin mass: the 97% yield stands in
    product_kg = 8000.0 * 0.6 * 0.97 * 1000.0
    assert stage.recovered_fraction == 0.97
    assert stage.capex_usd_yr == pytest.approx(46_000_000.0 * 0.1490295, rel=1e-5)
    assert stage.opex_usd_yr == 1_200_000.0
    assert stage.impacts.gwp_t == pytest.approx(0.5 * product_kg / 1000.0)
    assert stage.impacts.energy_mj == pytest.approx(5.0 * product_kg)
    assert stage.impacts.renewable_mj == pytest.approx(0.12 * 5.0 * product_kg)
    assert stage.impacts.disposal_t == pytest.approx(12.0) and stage.impacts.waste_kg == 12_000.0
    assert stage.impacts.water_recycled_m3 == 80_000.0


def test_a_wash_uses_the_resin_mass_the_simulation_reports():
    simulated = dict(ROW, waste_diverted_kg_per_yr=4_800_000.0 * 0.98)   # 98% of the 4,800 t of LDPE the plant receives
    stage, source = dl.wash_stage_from_row(_spec(), simulated)
    assert source == "simulated" and stage.recovered_fraction == pytest.approx(0.98)
    assert stage.impacts.gwp_t == pytest.approx(0.5 * 4_800_000.0 * 0.98 / 1000.0)
    shared, _ = dl.wash_stage_from_row(_spec(plant=20_000.0), dict(ROW, waste_diverted_kg_per_yr=12_000_000.0 * 0.98))
    assert shared.recovered_fraction == pytest.approx(0.98)   # the plant's output is compared with the plant's target mass
    for odd in (4_800_000.0 * 0.3, 4_800_000.0 * 1.4):        # implausible: fall back to the assumed yield
        assert dl.wash_stage_from_row(_spec(), dict(ROW, waste_diverted_kg_per_yr=odd))[1] == "assumed"


def test_an_interest_rate_of_zero_is_not_replaced_by_the_default():
    stage, _ = dl.wash_stage_from_row(_spec(), dict(ROW, finance_interest=0.0))
    assert stage.capex_usd_yr == pytest.approx(46_000_000.0 / 10.0)


def test_shared_plant_charges_a_throughput_share_of_annual_totals_only():
    dedicated, _ = dl.wash_stage_from_row(_spec(), ROW)
    shared, _ = dl.wash_stage_from_row(_spec(plant=20_000.0), ROW)
    assert _spec(plant=20_000.0).share == pytest.approx(0.4) and _spec().share == 1.0
    assert shared.capex_usd_yr == pytest.approx(0.4 * dedicated.capex_usd_yr)
    assert shared.opex_usd_yr == pytest.approx(0.4 * dedicated.opex_usd_yr)
    assert shared.impacts.water_recycled_m3 == pytest.approx(0.4 * dedicated.impacts.water_recycled_m3)
    assert shared.impacts.gwp_t == pytest.approx(dedicated.impacts.gwp_t)  # per-kilogram results follow the stream


def test_a_shared_plant_smaller_than_the_feed_is_refused_not_clamped():
    assert _spec(plant=1000.0).share == pytest.approx(8.0)  # a share above one is visible, never hidden
    with pytest.raises(ValueError, match="at least as large"):
        dl.build_stage_table(FEED, plant_capacity_t=1000.0)


def test_process_config_carries_the_plant_capacity_and_stream_composition():
    spec = _spec("EVOH", "Ethylene glycol", frozenset({"LDPE"}), mass_in=3200.0, percent=25.0, plant=20_000.0)
    config = dl.process_config(spec)
    assert config["processing_capacity_mt_per_yr"] == 20_000.0 and config["target_mass_percent"] == 25.0
    assert config["target_polymer"] == "EVOH" and config["dissolution_temperature_c"] == 100.0
    assert config["solvent_price_usd_per_kg"] > 0
    with pytest.raises(dl.StageUnavailable, match="solvent_price_unavailable"):
        dl.process_config(_spec(solvent="Tetrachloroethylene"))


def _screen_stub(by_temperature):
    """A stand-in for thermodynamics.screen_polymer_separation: rows keyed by the single screened temperature."""
    def fake(**kwargs):
        temperature = kwargs["temperature_min_c"]
        rows = [dict(row, temperature_c=temperature) for row in by_temperature.get(temperature, [])]
        return json.dumps({"data": {"success": True, "ranked_candidates": rows}})
    return fake


def _row(solvent, target, retained, margin=50.0, gap=None):
    return {"solvent": solvent, "target_solubility_pct": target, "max_off_target_solubility_pct": retained,
            "boiling_point_margin_c": margin, "selectivity_pct": gap if gap is not None else target - retained,
            "limiting_off_target_polymer": "PET"}


def test_stage_options_keep_each_solvents_lowest_qualifying_temperature(monkeypatch):
    table = {
        90.0: [_row("ethylene glycol", 8.0, 0.5), _row("toluene", 11.0, 1.0)],  # glycol below the 10 wt% target
        100.0: [_row("ethylene glycol", 10.6, 0.94), _row("toluene", 20.0, 2.0)],
        120.0: [_row("ethylene glycol", 30.0, 2.0), _row("propylene glycol", 12.4, 3.23),  # over the retention limit
                _row("flashing solvent", 40.0, 0.1, margin=4.0)],  # within 10 C of boiling
    }
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub(table))
    specs = dl.stage_options(FEED, frozenset(), "EVOH", ["ethylene glycol", "toluene"], max_options=5,
                             temperature_min_c=90.0, temperature_max_c=120.0, temperature_step_c=10.0)
    found = {s.solvent: s for s in specs}
    assert set(found) == {"ethylene glycol", "toluene"}
    assert found["ethylene glycol"].temperature_c == 100.0 and found["toluene"].temperature_c == 90.0
    assert found["toluene"].thermo["target_solubility_pct"] == 11.0
    assert specs[0].solvent == "toluene"  # ranked by the target-to-retained gap
    assert found["toluene"].mass_in_t == 8000.0 and found["toluene"].target_mass_percent == pytest.approx(10.0)


def test_stage_options_for_the_last_polymer_or_an_absent_one_are_empty(monkeypatch):
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub({}))
    assert dl.stage_options(FEED, frozenset({"LDPE", "PET", "NYLON6"}), "EVOH", ["toluene"], max_options=3) == []
    assert dl.stage_options(FEED, frozenset({"LDPE"}), "LDPE", ["toluene"], max_options=3) == []


def test_stage_mass_balance_follows_what_was_removed():
    mass, masses = dl.stream_after(FEED, frozenset({"LDPE"}))
    assert mass == pytest.approx(3200.0) and set(masses) == {"PET", "NYLON6", "EVOH"}


def _stub_table(pending=False):
    stage = wp.WashStage("EVOH", "Ethylene glycol", 2.0e6, 0.6e6, wp.Impacts(energy_mj=1e6, gwp_t=500.0), 120.0)
    table = dl.StageTable()
    spec = _spec("EVOH", "Ethylene glycol", mass_in=8000.0, percent=10.0)
    if pending:
        table.pending.append(spec)
    else:
        table.stages[spec.key] = stage
        table.specs[spec.key] = spec
        table.engine_modes[spec.key] = "live"
    return table


def _call(monkeypatch, table, **kwargs):
    monkeypatch.setattr(dl, "build_stage_table", lambda *a, **k: table)
    base = {"feed_mass_fractions": {"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1}, "feed_tonnes_per_year": 8000}
    return json.loads(tool.optimize_waste_pathway(**{**base, **kwargs}))


def test_the_tool_stops_and_asks_before_simulating(monkeypatch):
    out = _call(monkeypatch, _stub_table(pending=True))
    assert out["data"]["success"] is False and out["data"]["error_code"] == "live_tea_cost_confirmation_required"
    pending = out["data"]["pending_stages"]
    assert pending[0]["solvent"] == "Ethylene glycol" and pending[0]["polymer"] == "EVOH"
    assert "confirm_live_tea=true" in out["display"]


def test_the_tool_returns_the_optimum_with_its_basis_and_checks(monkeypatch):
    out = _call(monkeypatch, _stub_table(), objective="max_profit", shared_plant_capacity_mt_per_yr=20000)
    data = out["data"]
    assert data["success"] is True and data["analysis_type"] == "waste_pathway_optimum"
    assert data["basis"] == "tea_live" and data["milp_agrees_with_enumeration"] is True
    assert data["selected"]["pathway"].startswith("EVOH/Ethylene glycol")  # 800 t of EVOH at $8,100 pays for the wash
    assert data["selected"]["washes"][0]["temperature_c"] == 120.0
    assert set(data["selected"]["circularity_scores"]) == {"energy", "ghg", "water", "waste", "substitutability"}
    assert set(data["optima_by_objective"]) == {"max_profit", "min_emissions", "max_circularity"}
    assert data["optima_by_objective"]["min_emissions"]["pathway"] == "no wash + lf"
    assert data["wash_plant"]["shared_capacity_mt_per_yr"] == 20000.0
    assert "downstream_data_notes" in data and "assumptions" in data
    assert agent.source_basis_for("optimize_waste_pathway", data, {}) == "tea_live"


def test_the_tool_traces_a_pareto_front(monkeypatch):
    out = _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit")
    data = out["data"]
    assert data["success"] is True and data["analysis_type"] == "waste_pathway_pareto"
    emissions = [p["emissions_t_co2e_per_yr"] for p in data["frontier_points"]]
    assert emissions == sorted(emissions) and data["epsilon_sweep_agrees_with_enumeration"] is True


@pytest.mark.parametrize("kwargs, fragment", [
    ({"feed_mass_fractions": {"LDPE": 0.7, "PET": 0.1}}, "sum to 1"),
    ({"feed_mass_fractions": {"unobtainium": 1.0}}, "not in the thermodynamic grid"),
    ({"feed_tonnes_per_year": 0}, "positive"),
    ({"max_washes": 9}, "max_washes"),
    ({"max_washes": -1}, "max_washes"),
])
def test_the_tool_refuses_bad_input(monkeypatch, kwargs, fragment):
    out = _call(monkeypatch, _stub_table(), **kwargs)
    assert out["data"]["success"] is False and out["data"]["error_code"] == "invalid_input"
    assert fragment in out["data"]["error"]


def test_percentages_are_accepted_and_resolved_to_grid_names():
    fractions, labels = tool._feed({"ldpe": 60, "PET": 20, "nylon 6": 10, "EVOH": 10})
    assert fractions["EVOH"] == pytest.approx(0.1) and sum(fractions.values()) == pytest.approx(1.0)
    assert set(labels.values()) == {"ldpe", "PET", "nylon 6", "EVOH"}


def test_the_tool_is_registered_with_a_schema_and_a_declared_name():
    assert "optimize_waste_pathway" in agent.BY_NAME and "optimize_waste_pathway" in EXPECTED_REGISTRY_NAMES
    schema = next(s for s in agent.tool_schemas({}) if s["name"] == "optimize_waste_pathway")
    props = schema["parameters"]["properties"]
    assert props["objective"]["enum"] == ["max_profit", "min_emissions", "max_circularity"]
    assert "confirm_live_tea" in props and "shared_plant_capacity_mt_per_yr" in props
    assert set(schema["parameters"]["required"]) == {"feed_mass_fractions", "feed_tonnes_per_year"}


def test_a_refused_screen_is_reported_and_never_read_as_no_solvent(monkeypatch):
    refusal = json.dumps({"data": {"success": False, "error": "Solvent(s) not in the active solvent scope: pyridine"}})
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", lambda **kwargs: refusal)
    with pytest.raises(dl.StageUnavailable, match="thermodynamic_screen_failed"):
        dl.stage_options(FEED, frozenset(), "EVOH", ["toluene"], max_options=3)
    table = dl.build_stage_table(FEED, max_washes=1, solvents=["toluene"])
    assert not table.stages and not table.no_option
    assert {item["reason"] for item in table.failed} == {"thermodynamic_screen_failed"}


def test_the_tool_reports_a_failed_screen_as_an_error(monkeypatch):
    table = dl.StageTable(failed=[{"polymer": "EVOH", "removed_before": [], "reason": "thermodynamic_screen_failed",
                                   "detail": "not in the active solvent scope"}])
    out = _call(monkeypatch, table)
    assert out["data"]["success"] is False and out["data"]["error_code"] == "thermodynamic_screen_failed"


def test_costable_solvents_follow_the_active_solvent_scope(monkeypatch):
    everything = dl.admitted_solvents()
    assert everything and dl.solvents_outside_scope() == []
    keep = {dl.thermodynamics.resolve_solvent("toluene"), dl.thermodynamics.resolve_solvent("glycol")}
    monkeypatch.setattr(dl.thermodynamics, "get_available_solvents", lambda: keep)
    inside = dl.admitted_solvents()
    assert set(inside) < set(everything) and 1 <= len(inside) <= 2
    assert set(dl.solvents_outside_scope()) == set(everything) - set(inside)
    assert set(dl.admitted_solvents(in_scope_only=False)) == set(everything)


def test_the_same_wash_is_simulated_once_per_session(monkeypatch):
    dl.clear_stage_rows()
    calls = []

    def fake_evaluate(**kwargs):
        calls.append(kwargs["process_config"]["solvent"])
        return json.dumps({"data": {"engine_mode": "live", "comparison_rows": [dict(ROW, success=True)]}})

    monkeypatch.setattr(dl.tea, "evaluate_process", fake_evaluate)
    spec = _spec("EVOH", "Ethylene glycol", mass_in=8000.0, percent=10.0)
    first = dl.run_stage(spec, confirm_live_tea=True)
    second = dl.run_stage(spec, confirm_live_tea=False)  # a repeat needs no new confirmation: nothing is simulated
    assert calls == ["Ethylene glycol"] and second == {**first, "session_reused": True} and "session_reused" not in first
    dl.run_stage(_spec("EVOH", "Ethylene glycol", mass_in=8000.0, percent=10.0, plant=20_000.0), confirm_live_tea=True)
    assert len(calls) == 2  # a different plant is a different simulation
    dl.clear_stage_rows()


def test_caller_supplied_circularity_bounds_fix_the_scale(monkeypatch):
    table = _stub_table()
    first = _call(monkeypatch, table)["data"]
    assert first["circularity_bounds_origin"].startswith("1.5 times")
    reported = first["circularity_upper_bounds"]
    again = _call(monkeypatch, table, circularity_upper_bounds=reported)["data"]
    assert again["circularity_bounds_origin"] == "supplied by the caller"
    assert again["selected"]["circularity_index"] == pytest.approx(first["selected"]["circularity_index"])
    looser = {k: 10 * v for k, v in reported.items()}
    wide = _call(monkeypatch, table, circularity_upper_bounds=looser)["data"]
    assert wide["selected"]["circularity_scores"]["ghg"] > first["selected"]["circularity_scores"]["ghg"] or \
        first["selected"]["circularity_scores"]["ghg"] == 1.0
    assert {"sales_low_usd", "sales_high_usd"} <= set(reported)
    bad = _call(monkeypatch, table, circularity_upper_bounds={"energy_mj": 1.0})
    assert bad["data"]["success"] is False and bad["data"]["error_code"] == "invalid_input"


def test_the_agent_receives_the_pathway_not_a_refusal(monkeypatch):
    """The harness refuses a result over 8 KB that has no primary row list; this tool's must reach the model."""
    from dissolve.session import bind_tool_session
    monkeypatch.setattr(dl, "build_stage_table", lambda *a, **k: _stub_table())
    base = {"feed_mass_fractions": {"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1}, "feed_tonnes_per_year": 8000}
    with bind_tool_session({}):
        optimum = agent.dispatch("optimize_waste_pathway", **base)
        front = agent.dispatch("optimize_waste_pathway", pareto="emissions_vs_profit", **base)
    assert optimum["available"] is True and optimum["source_basis"] == "tea_live"
    data = optimum["data"]
    assert data["selected"]["pathway"].startswith("EVOH/Ethylene glycol")
    assert set(data["optima_by_objective"]) == {"max_profit", "min_emissions", "max_circularity"}
    shown = optimum.get("top") or data["ranked_candidates"]
    assert shown[0]["rank"] == 1 and shown[0]["pathway"] == data["selected"]["pathway"]
    assert front["available"] is True
    rows = front.get("top") or front["data"]["frontier_points"]
    assert rows and rows[0]["pathway"] == "no wash + lf"


def test_the_user_can_state_distances_instead_of_a_location_scenario(monkeypatch):
    table = _stub_table()
    default = _call(monkeypatch, table)["data"]
    assert default["downstream_distances_mile"]["we"] == 151.0 and default["downstream_distances_mile"]["py"] == 1034.0
    own = _call(monkeypatch, table, downstream_distances_mile={"Incinerator": 20, "gasification hydrogen": 75})["data"]
    assert own["downstream_distances_mile"]["we"] == 20.0 and own["downstream_distances_mile"]["gas_h2"] == 75.0
    assert own["downstream_distances_mile"]["lf"] == 9.2  # not given: keeps the default
    assert "caller gave" in own["downstream_sites_origin"] and "incinerator 20 miles" in own["downstream_sites_in_words"]
    assert "published study" in default["downstream_sites_origin"] and "location_scenario" not in default
    coded = _call(monkeypatch, table, downstream_distances_mile={"we": 20, "py": 50})["data"]   # the tool's own codes work too
    assert coded["downstream_distances_mile"]["we"] == 20.0 and coded["downstream_distances_mile"]["py"] == 50.0
    assert default["selected"]["downstream"]["key"] == own["selected"]["downstream"]["key"] == "we"
    saved = default["selected"]["transport_usd_per_yr"] - own["selected"]["transport_usd_per_yr"]
    assert saved == pytest.approx(7224.0 * (151.0 - 20.0) * 0.07)  # the 7,224 t residual (7,200 t plus 24 t not recovered) hauls 131 miles less
    assert own["selected"]["profit_usd_per_yr"] == pytest.approx(default["selected"]["profit_usd_per_yr"] + saved)
    for bad, fragment in (({"airport": 5}, "unknown downstream site"), ({"landfill": -1}, "negative")):
        out = _call(monkeypatch, table, downstream_distances_mile=bad)
        assert out["data"]["success"] is False and fragment in out["data"]["error"]


def _solubility_stub(by_solvent):
    def fake(**kwargs):
        rows = [{"solvent_name": name, "solubility_pct": pct, "temperature_c": kwargs["temperatures"][0]}
                for name, pct in by_solvent.items() if name in kwargs["solvents"]]
        return json.dumps({"data": {"success": True, "results": rows, "has_more": False}})
    return fake


def test_a_wash_must_precipitate_its_polymer_when_it_cools(monkeypatch):
    table = {
        25.0: [_row("dmso", 11.0, 1.0), _row("glycol", 10.5, 0.9)],   # both dissolve the target already at 25 C
        75.0: [_row("dmso", 12.0, 1.0), _row("glycol", 12.0, 0.9)],
        120.0: [_row("glycol", 15.0, 1.0)],
    }
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub(table))
    # at the 35 C cooling step DMSO keeps 2.1 wt% in solution (above the 1 wt% limit), glycol 0.01 wt%
    monkeypatch.setattr(dl.thermodynamics, "solubility_query", _solubility_stub({"dmso": 2.1, "glycol": 0.01}))
    specs = dl.stage_options(FEED, frozenset(), "EVOH", ["dmso", "glycol"], max_options=5, temperature_min_c=25.0,
                             temperature_max_c=120.0, temperature_step_c=25.0, precipitation_temperature_c=35.0)
    found = {s.solvent: s for s in specs}
    assert set(found) == {"glycol"}                       # DMSO would leave the polymer dissolved after cooling
    assert found["glycol"].temperature_c == 75.0           # 25 C is not above the cooling temperature, so the next one
    assert found["glycol"].thermo["target_left_dissolved_pct_after_cooling"] == 0.01
    # without a precipitation temperature the old behaviour (lowest qualifying temperature) holds
    plain = dl.stage_options(FEED, frozenset(), "EVOH", ["dmso", "glycol"], max_options=5, temperature_min_c=25.0,
                             temperature_max_c=120.0, temperature_step_c=25.0)
    assert {s.solvent: s.temperature_c for s in plain} == {"dmso": 25.0, "glycol": 25.0}


def test_a_missing_precipitation_temperature_in_the_grid_is_reported(monkeypatch):
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub({25.0: [_row("glycol", 12.0, 1.0)]}))
    monkeypatch.setattr(dl.thermodynamics, "solubility_query", _solubility_stub({}))
    with pytest.raises(dl.StageUnavailable, match="precipitation_temperature_not_on_grid"):
        dl.stage_options(FEED, frozenset(), "EVOH", ["glycol"], max_options=3, temperature_min_c=25.0, temperature_max_c=25.0,
                         precipitation_temperature_c=33.0)


def test_the_retained_pickup_bound_is_reported_not_enforced(monkeypatch):
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub({100.0: [_row("toluene", 12.7, 1.44)]}))
    spec = dl.stage_options(FEED, frozenset(), "LDPE", ["toluene"], max_options=1, temperature_min_c=100.0,
                            temperature_max_c=100.0)[0]
    bound = spec.thermo["retained_pickup_upper_bound_pct_of_product"]
    assert bound == pytest.approx(100.0 * min(3200.0, 1.44 / 98.56 * 4800.0 * (100.0 / 3.0 - 1.0)) / 4800.0)
    assert bound > 10.0  # the 3 wt% retention limit does not by itself guarantee purity


def test_prices_are_resolved_to_the_feed_polymers_and_missing_ones_are_named():
    economics = dl.default_economics(FEED, {"ldpe": 900, "nylon 6": 3000})
    assert economics.polymer_price_usd_per_t["LDPE"] == 900.0 and economics.polymer_price_usd_per_t["NYLON6"] == 3000.0
    assert dl.polymers_without_price(wp.Feed(100.0, {"LDPE": 0.5, "PS": 0.5}), dl.default_economics(
        wp.Feed(100.0, {"LDPE": 0.5, "PS": 0.5}))) == ["PS"]
    with pytest.raises(ValueError, match="not a polymer in the feed"):
        dl.default_economics(FEED, {"PVC": 900})
    with pytest.raises(ValueError, match="negative"):
        dl.default_economics(FEED, {"LDPE": -1})


def test_the_tool_refuses_a_price_for_a_polymer_not_in_the_feed_and_a_too_small_shared_plant(monkeypatch):
    out = _call(monkeypatch, _stub_table(), polymer_prices_usd_per_t={"PVC": 100})
    assert out["data"]["success"] is False and out["data"]["error_code"] == "invalid_input"
    monkeypatch.undo()
    real = json.loads(tool.optimize_waste_pathway(
        feed_mass_fractions={"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1}, feed_tonnes_per_year=8000,
        shared_plant_capacity_mt_per_yr=1000))
    assert real["data"]["error_code"] == "invalid_input" and "at least as large" in real["data"]["error"]


def test_the_basis_says_what_the_answer_rests_on():
    assert tool._basis(dl.StageTable()) == "cosmo_rs_grid"        # no wash was costed: the screen and the technology table
    key = (frozenset(), "EVOH", "glycol")
    table = dl.StageTable(stages={key: wp.WashStage("EVOH", "glycol", 1.0, 1.0)}, engine_modes={key: "cache"},
                          match_status={key: "exact"})
    assert tool._basis(table) == "tea_cache_exact"
    table.engine_modes[key] = "live"
    assert tool._basis(table) == "tea_live"
    table.match_status[key] = "surrogate"
    assert tool._basis(table) == "tea_screening_analog"            # an analog is never passed off as the exact simulation


def test_warnings_name_unpriced_polymers_and_a_pure_residual(monkeypatch):
    stub = _stub_table()
    out = _call(monkeypatch, stub)["data"]
    assert "warnings" in out and isinstance(out["warnings"], list)
    only = {"feed_mass_fractions": {"EVOH": 0.5, "PS": 0.5}}
    unpriced = _call(monkeypatch, stub, **only)["data"]
    assert any("PS" in w and "no price" in w for w in unpriced["warnings"])


def test_inputs_are_validated_before_the_user_is_asked_to_confirm_simulations(monkeypatch):
    """A typo must not cost minutes of simulation before it is reported."""
    calls = []
    monkeypatch.setattr(dl, "build_stage_table", lambda *a, **k: calls.append(1) or _stub_table(pending=True))
    base = {"feed_mass_fractions": {"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1}, "feed_tonnes_per_year": 8000}
    for bad in ({"polymer_prices_usd_per_t": {"PVC": 900}}, {"downstream_distances_mile": {"airport": 5}},
                {"circularity_upper_bounds": {"energy_mj": 1.0}}):
        out = json.loads(tool.optimize_waste_pathway(**base, **bad))
        assert out["data"]["error_code"] == "invalid_input", bad
    assert calls == []  # no screening or simulation was even set up


def test_a_generic_polymer_label_is_reported_as_a_reading(monkeypatch):
    out = _call(monkeypatch, _stub_table(), feed_mass_fractions={"polyethylene": 0.7, "EVOH": 0.3})["data"]
    assert any("read the polymer 'polyethylene' as" in w for w in out["warnings"])
    exact = _call(monkeypatch, _stub_table(), feed_mass_fractions={"ldpe": 0.7, "EVOH": 0.3})["data"]
    assert not any("read the polymer" in w for w in exact["warnings"])   # case alone is not a guess


def test_a_single_polymer_feed_says_there_is_nothing_to_separate(monkeypatch):
    out = _call(monkeypatch, dl.StageTable(), feed_mass_fractions={"LDPE": 1.0})["data"]
    assert out["success"] is True and out["selected"]["washes"] == []
    assert any("single polymer" in w for w in out["warnings"])


def test_solvent_names_are_matched_in_any_common_spelling_and_unmatched_ones_are_reported(monkeypatch):
    seen = {}
    monkeypatch.setattr(dl, "build_stage_table", lambda feed, **k: seen.setdefault("pool", k["solvents"]) and _stub_table())
    base = {"feed_mass_fractions": {"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1}, "feed_tonnes_per_year": 8000}
    out = json.loads(tool.optimize_waste_pathway(**base, solvents=["toluene", "ethylene glycol", "Xylene", "unobtainium acid"]))["data"]
    resolved = {dl.thermodynamics.resolve_solvent(n) for n in seen["pool"]}
    assert resolved == {dl.thermodynamics.resolve_solvent(n) for n in ("toluene", "ethylene glycol", "xylene")}
    assert any("'unobtainium acid' is not recognized" in w for w in out["warnings"])
    gone = json.loads(tool.optimize_waste_pathway(**base, solvents=["tetrachloroethylene"]))["data"]
    assert gone["error_code"] == "no_costable_solvents" and gone["notes"]


def test_washes_that_could_not_be_simulated_are_named_in_the_warnings(monkeypatch):
    table = _stub_table()
    table.failed.append({"polymer": "PS", "removed_before": [], "reason": "failed",
                         "detail": "PS maps to process-model ID PS, which is not in the package chemical outline"})
    out = _call(monkeypatch, table)["data"]
    assert any("washes of PS could not be simulated" in w and "left in the residual" in w for w in out["warnings"])


def test_the_agent_is_told_never_to_invent_a_feed():
    from dissolve import agent as agent_module
    assert "Never invent or assume a feed" in agent_module.SYSTEM_PROMPT
    schema = next(s for s in agent_module.tool_schemas({}) if s["name"] == "optimize_waste_pathway")
    assert "Never assume or invent a feed" in schema["description"] and "ask" in schema["description"]


def test_a_solvent_that_would_freeze_on_cooling_is_excluded(monkeypatch):
    table = {75.0: [_row("camphene", 12.0, 1.0), _row("toluene", 12.0, 1.0), _row("mystery solvent", 12.0, 1.0)]}
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub(table))
    monkeypatch.setattr(dl.thermodynamics, "solubility_query", _solubility_stub({"camphene": 0.1, "toluene": 0.1, "mystery solvent": 0.1}))
    ident = dl.thermodynamics.resolve_solvent
    monkeypatch.setattr(dl, "solvent_melting_points_c", lambda: {ident("camphene"): 46.0, ident("toluene"): -95.0})
    monkeypatch.setattr(dl.thermodynamics, "resolve_solvent", lambda n, known=None: {"camphene": "camphene", "toluene": "toluene"}.get(n, n))
    specs = dl.stage_options(FEED, frozenset(), "LDPE", ["camphene", "toluene", "mystery solvent"], max_options=5,
                             temperature_min_c=75.0, temperature_max_c=75.0, precipitation_temperature_c=35.0)
    found = {s.solvent: s for s in specs}
    assert set(found) == {"toluene", "mystery solvent"}             # camphene (mp 46 C) is solid at the 35 C cooling step
    assert found["toluene"].thermo["solvent_melting_point_c"] == -95.0
    assert found["mystery solvent"].thermo["solvent_melting_point_c"] is None   # unchecked, not assumed liquid


def test_the_melting_point_table_comes_from_pubchem_and_holds_the_known_solids():
    table = dl.solvent_melting_points_c()
    ident = dl.thermodynamics.resolve_solvent
    assert table[ident("resorcinol")] == pytest.approx(110.0, abs=2)
    assert table[ident("naphthalene")] > 75 and table[ident("camphene")] > 40
    assert table[ident("toluene")] < -90 and table[ident("ethanol")] < -100
    import json as _json
    meta = _json.loads(dl._MELTING_POINTS_FILE.read_text())
    assert "PubChem" in meta["source"] and meta["retrieved"]


def test_the_warning_names_a_solvent_whose_melting_point_was_not_checked(monkeypatch):
    stub = _stub_table()
    key = next(iter(stub.specs))
    stub.specs[key] = dl.StageSpec(key[0], key[1], key[2], 120.0, 8000.0, 10.0, thermo={"solvent_melting_point_c": None})
    out = _call(monkeypatch, stub)["data"]
    assert any("no melting point is on file for" in w for w in out["warnings"])


def test_zero_washes_means_choose_only_the_downstream_destination(monkeypatch):
    seen = {}
    monkeypatch.setattr(dl, "build_stage_table", lambda feed, **k: seen.setdefault("max", k["max_washes"]) and dl.StageTable() or dl.StageTable())
    out = _call(monkeypatch, dl.StageTable(), max_washes=0)["data"]
    assert out["success"] is True and out["selected"]["washes"] == [] and out["candidates_considered"] > 0


def test_only_a_label_that_names_several_grades_is_flagged_as_a_guess(monkeypatch):
    for label, flagged in (("polyethylene", True), ("PE", True), ("polystyrene", False), ("ldpe", False), ("PET", False)):
        data = _call(monkeypatch, _stub_table(), feed_mass_fractions={label: 0.7, "EVOH": 0.3})["data"]
        assert data["success"] is True, label
        assert any("read the polymer" in w for w in data["warnings"]) is flagged, label


def test_a_family_label_with_several_grades_and_no_default_asks_which(monkeypatch):
    out = _call(monkeypatch, _stub_table(), feed_mass_fractions={"nylon": 0.7, "EVOH": 0.3})["data"]
    assert out["success"] is False and out["error_code"] == "invalid_input"
    assert "NYLON6" in out["error"] and "NYLON66" in out["error"] and "say which" in out["error"]


_BINARY = {"feed_mass_fractions": {"LDPE": 0.9, "EVOH": 0.1}, "feed_tonnes_per_year": 8000}


def test_the_result_lists_what_each_downstream_technology_earns(monkeypatch):
    rows = {r["key"]: r for r in _call(monkeypatch, _stub_table())["data"]["downstream_technologies"]}
    assert rows["we"]["revenue_usd_per_t_of_residual"] > 0 and rows["lf"]["revenue_usd_per_t_of_residual"] == 0
    assert rows["py"]["cannot_take"] == ["PET"] and set(rows) >= {"lf", "we", "py", "gas_er", "gas_h2", "gas_h2cc"}


def test_the_unwashed_feed_is_shown_at_every_destination_for_a_burn_or_recycle_question(monkeypatch):
    data = _call(monkeypatch, _stub_table())["data"]
    keys = [o["key"] for o in data["unwashed_feed_options"]]
    assert "we" in keys and "lf" in keys and "py" not in keys  # pyrolysis cannot take PET
    profits = [o["profit_usd_per_yr"] for o in data["unwashed_feed_options"]]
    assert profits == sorted(profits, reverse=True)


def test_a_selected_resale_reports_how_much_profit_depends_on_it(monkeypatch):
    data = _call(monkeypatch, _stub_table(), **_BINARY)["data"]
    assert data["selected"]["pathway"].endswith("resale")
    dep = data["resale_dependence"]
    assert 0 < dep["resale_share_of_sales_pct"] < 100
    by_fraction = {r["residual_sold_at_share_of_resin_price"]: r for r in dep["sensitivity_max_profit"]}
    assert set(by_fraction) == {1.0, 0.5, 0.0}
    assert by_fraction[1.0]["profit_usd_per_yr"] > by_fraction[0.5]["profit_usd_per_yr"] > by_fraction[0.0]["profit_usd_per_yr"]
    assert "resale_dependence" in " ".join(data["warnings"])


def test_the_resale_price_fraction_lowers_the_profit_and_can_remove_the_resale(monkeypatch):
    full = _call(monkeypatch, _stub_table(), **_BINARY)["data"]["selected"]["profit_usd_per_yr"]
    half = _call(monkeypatch, _stub_table(), residual_resale_price_fraction=0.5, **_BINARY)["data"]["selected"]["profit_usd_per_yr"]
    none = _call(monkeypatch, _stub_table(), residual_resale_price_fraction=0.0, **_BINARY)["data"]
    assert full > half and "resale" not in none["selected"]["pathway"].split("+")[-1]
    assert _call(monkeypatch, _stub_table(), residual_resale_price_fraction=-0.1, **_BINARY)["data"]["success"] is False


def test_a_pathway_without_resale_has_no_resale_dependence(monkeypatch):
    assert _call(monkeypatch, _stub_table(), objective="min_emissions")["data"]["resale_dependence"] is None


def test_selected_washes_carry_solvent_hazard_and_a_danger_signal_is_warned(monkeypatch):
    seen = []
    monkeypatch.setattr(dl, "solvent_hazard", lambda name, t: seen.append((name, t)) or
                        {"ghs_signal_word": "Danger", "heating_risk": "high", "heating_flags": ["above flash point"]})
    data = _call(monkeypatch, _stub_table(), **_BINARY)["data"]
    assert ("Ethylene glycol", 120.0) in seen
    assert data["selected_wash_screen"][0]["safety"]["ghs_signal_word"] == "Danger"
    text = " ".join(data["warnings"])
    assert "Danger" in text and "avoid_danger_solvents" in text and "above flash point" in text


def test_a_safe_solvent_raises_no_hazard_warning_and_an_unreadable_card_is_not_a_hazard(monkeypatch):
    monkeypatch.setattr(dl, "solvent_hazard", lambda name, t: {"ghs_signal_word": "Warning", "heating_risk": "low"})
    assert not any("Danger" in w or "heating risk" in w for w in _call(monkeypatch, _stub_table(), **_BINARY)["data"]["warnings"])
    monkeypatch.setattr(dl, "solvent_hazard", lambda name, t: {})
    assert not any("Danger" in w for w in _call(monkeypatch, _stub_table(), **_BINARY)["data"]["warnings"])


def test_avoid_danger_solvents_is_passed_to_the_stage_builder(monkeypatch):
    seen = {}
    monkeypatch.setattr(dl, "build_stage_table", lambda *a, **k: seen.update(k) or dl.StageTable())
    tool.optimize_waste_pathway(avoid_danger_solvents=True, **_BINARY)
    assert seen["avoid_danger_solvents"] is True


def test_the_agent_is_told_not_to_cite_tea_records_for_a_downstream_only_answer():
    prompt = agent.SYSTEM_PROMPT if hasattr(agent, "SYSTEM_PROMPT") else ""
    assert "never say incineration earns nothing" in prompt and "resale_dependence" in prompt
    assert "not cite stored TEA/LCA records" in prompt


def _indicators(solvent, **row):
    spec = _spec("LDPE", solvent)
    stage, _ = dl.wash_stage_from_row(spec, {**ROW, "solvent_makeup_kg_per_yr": 600.0, "solvent_loss_pct": 0.01, **row})
    return dl.wash_indicators(spec, {**ROW, "solvent_makeup_kg_per_yr": 600.0, "solvent_loss_pct": 0.01, **row}, stage), stage


def test_wash_indicators_are_per_kilogram_of_recovered_resin():
    ind, stage = _indicators("Toluene")
    resin_t = 8000.0 * 0.6 * 0.97  # no simulated output in the row: the assumed 97% yield
    assert ind["energy_mj_per_kg_resin"] == 5.0 and ind["gwp_kg_co2e_per_kg_resin"] == 0.5
    assert ind["solvent_makeup_kg_per_t_resin"] == pytest.approx(600.0 / resin_t)
    assert ind["wash_cost_usd_per_kg_resin"] == pytest.approx((stage.capex_usd_yr + stage.opex_usd_yr) / (resin_t * 1000.0))
    assert ind["boiling_margin_c"] == pytest.approx(ind["boiling_point_c"] - 100.0)


def test_a_shared_plant_charges_solvent_make_up_by_throughput_share():
    spec = _spec("LDPE", "Toluene", plant=16000.0)
    stage, _ = dl.wash_stage_from_row(spec, {**ROW, "solvent_makeup_kg_per_yr": 600.0})
    ind = dl.wash_indicators(spec, {**ROW, "solvent_makeup_kg_per_yr": 600.0}, stage)
    assert ind["solvent_makeup_kg_per_t_resin"] == pytest.approx(0.5 * 600.0 / (8000.0 * 0.6 * 0.97))


def test_a_row_without_solvent_make_up_gives_none_and_not_zero():
    assert dl.wash_indicators(_spec(), ROW, dl.wash_stage_from_row(_spec(), ROW)[0])["solvent_makeup_kg_per_t_resin"] is None


def test_solvents_differ_in_their_life_cycle_factor_and_its_quality_is_reported():
    toluene, ethylene_glycol, acetic = (dl.solvent_lca_factor(n) for n in ("Toluene", "Ethylene glycol", "Acetic acid"))
    assert toluene["gwp_kg_co2e_per_kg_solvent"] != ethylene_glycol["gwp_kg_co2e_per_kg_solvent"]
    assert acetic["source_tier"] == "generator_class_average" and toluene["source_tier"] != acetic["source_tier"]
    assert dl.solvent_lca_factor("not a solvent") == {}


def test_different_solvents_give_different_indicators_for_one_resin():
    cheap, _ = _indicators("Toluene", total_energy_mj_per_kg=4.0, gwp_kg_co2e_per_kg=0.4)
    costly, _ = _indicators("Diphenyl ether", total_energy_mj_per_kg=6.0, gwp_kg_co2e_per_kg=0.7)
    assert cheap["energy_mj_per_kg_resin"] < costly["energy_mj_per_kg_resin"]
    assert cheap["gwp_kg_co2e_per_kg_resin"] < costly["gwp_kg_co2e_per_kg_resin"]
    assert cheap["boiling_point_c"] != costly["boiling_point_c"]


def test_the_result_compares_every_costed_wash_with_its_hazard(monkeypatch):
    table = _stub_table()
    key = next(iter(table.stages))
    table.indicators[key] = {"energy_mj_per_kg_resin": 6.1, "gwp_kg_co2e_per_kg_resin": 0.9, "solvent_makeup_kg_per_t_resin": 0.02}
    monkeypatch.setattr(dl, "solvent_hazard", lambda name, t: {"ghs_signal_word": "Warning", "heating_risk": "low"})
    data = _call(monkeypatch, table)["data"]
    (row,) = data["wash_comparison"]
    assert row["polymer"] == "EVOH" and row["solvent"] == "Ethylene glycol" and row["energy_mj_per_kg_resin"] == 6.1
    assert row["safety"]["ghs_signal_word"] == "Warning" and row["stream_without"] == []
    assert "circularity_and_solvents" in data["assumptions"]


def test_the_agent_is_told_to_compare_solvents_on_the_indicators_not_the_index():
    assert "wash_comparison" in tool.optimize_waste_pathway.__doc__.split("\n\n")[0]
    assert "barely changes with the solvent" in agent.SYSTEM_PROMPT and "wash_comparison" in agent.SYSTEM_PROMPT


@pytest.mark.parametrize("pareto", ["emissions_vs_profit", "circularity_vs_profit", "emissions_vs_circularity"])
def test_the_tool_traces_every_offered_front_and_its_sweep_agrees_with_enumeration(monkeypatch, pareto):
    data = _call(monkeypatch, _stub_table(), pareto=pareto)["data"]
    assert data["success"] is True and data["analysis_type"] == "waste_pathway_pareto"
    assert data["epsilon_sweep_agrees_with_enumeration"] is True and len(data["frontier_points"]) >= 2
    keys = {"emissions": "emissions_t_co2e_per_yr", "circularity": "circularity_index", "profit": "profit_usd_per_yr"}
    x, y = pareto.split("_vs_")
    xs, ys = [p[keys[x]] for p in data["frontier_points"]], [p[keys[y]] for p in data["frontier_points"]]
    assert xs == sorted(xs)
    # a front is a trade-off: along x (ascending) the better y must get strictly worse when x gets better, and strictly
    # better when x gets worse (emissions rising is worse)
    y_good = [-v if y == "emissions" else v for v in ys]
    for i in range(len(xs) - 1):
        if x == "emissions":
            assert y_good[i + 1] > y_good[i]
        else:
            assert y_good[i + 1] < y_good[i]


def _saved(tmp_path):
    return json.loads((tmp_path / "pathway_results" / "latest.json").read_text())


def test_every_answer_saves_all_candidates_and_the_three_exact_fronts(monkeypatch, tmp_path):
    out = _call(monkeypatch, _stub_table(), objective="max_profit", shared_plant_capacity_mt_per_yr=20000)["data"]
    saved = _saved(tmp_path)
    assert out["figure_data_file"] == str(tmp_path / "pathway_results" / "latest.json")
    assert saved["schema"] == "dissolve.waste-pathway-figure-data.v1" and len(saved["candidates"]) == out["candidates_considered"]
    assert saved["selected"] == out["selected"]["pathway"]
    assert set(saved["fronts"]) == {"emissions_vs_profit", "circularity_vs_profit", "emissions_vs_circularity"}
    labels = {c["label"] for c in saved["candidates"]}
    assert all(set(front) <= labels and front for front in saved["fronts"].values())
    assert saved["plant"]["shared_capacity_mt_per_yr"] == 20000.0 and saved["optima"]["max_profit"] == saved["selected"]


def test_a_pareto_request_saves_the_same_data_without_a_chosen_pathway(monkeypatch, tmp_path):
    out = _call(monkeypatch, _stub_table(), pareto="circularity_vs_profit")["data"]
    saved = _saved(tmp_path)
    assert out["figure_data_file"] and saved["selected"] is None and saved["request"] == "pareto=circularity_vs_profit"
    assert [p["pathway"] for p in out["frontier_points"]] == saved["fronts"]["circularity_vs_profit"]


def test_the_saved_fronts_are_the_toolss_own_exact_fronts(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "build_stage_table", lambda *a, **k: _stub_table())
    data = _call(monkeypatch, _stub_table())["data"]
    saved = _saved(tmp_path)
    by_label = {c["label"]: c for c in saved["candidates"]}
    for name, (x, y) in {"emissions_vs_profit": ("emissions", "profit"), "circularity_vs_profit": ("circularity", "profit"),
                         "emissions_vs_circularity": ("emissions", "circularity")}.items():
        key = {"emissions": "emissions_t_co2e_per_yr", "profit": "profit_usd_per_yr", "circularity": "circularity_index"}
        sign = {"emissions": -1.0, "profit": 1.0, "circularity": 1.0}
        front = [by_label[label] for label in saved["fronts"][name]]
        for member in front:  # nothing in the saved candidates dominates a saved front member
            assert not any(sign[x] * o[key[x]] >= sign[x] * member[key[x]] and sign[y] * o[key[y]] >= sign[y] * member[key[y]]
                           and (o[key[x]], o[key[y]]) != (member[key[x]], member[key[y]]) for o in saved["candidates"]), (name, member["label"])
    assert data["figure_data_file"]


def test_an_unwritable_results_folder_never_fails_the_optimization(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setenv("DISSOLVE_PATHWAY_RESULTS_DIR", str(blocker / "inside"))
    data = _call(monkeypatch, _stub_table())["data"]
    assert data["success"] is True and data["figure_data_file"] is None


def test_the_agent_is_told_to_draw_the_plot_itself():
    first = tool.optimize_waste_pathway.__doc__.split("\n\n")[0]
    assert "draw_plot" in first and "figure_file" in first and "figure_note" in first


def test_the_system_prompt_tells_the_agent_to_point_to_the_plot_script():
    assert "draw_plot true" in agent.SYSTEM_PROMPT and "scripts/plot_pareto.py" in agent.SYSTEM_PROMPT
    assert "do not claim a plot" in agent.SYSTEM_PROMPT


def _matplotlib_python():
    return tool._python_with_matplotlib.__wrapped__()


def test_no_plot_is_drawn_unless_asked(monkeypatch):
    data = _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit")["data"]
    assert "figure_file" not in data and data["figure_data_file"]


def test_draw_plot_writes_the_figure_and_its_table_without_opening_a_window(monkeypatch):
    if _matplotlib_python() is None:
        pytest.skip("no Python with matplotlib on this machine")
    data = _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit", draw_plot=True)["data"]
    from pathlib import Path
    figure = Path(data["figure_file"])
    assert figure.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and figure.stat().st_size > 10_000
    assert Path(data["figure_table_file"]).read_text().startswith("# Pareto fronts:")
    assert data["figure_opened"] is False  # the tests set DISSOLVE_NO_OPEN
    assert "Plot:" in _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit", draw_plot=True)["display"]


def test_draw_plot_also_works_for_a_single_objective_answer(monkeypatch):
    if _matplotlib_python() is None:
        pytest.skip("no Python with matplotlib on this machine")
    data = _call(monkeypatch, _stub_table(), objective="max_profit", draw_plot=True)["data"]
    assert data["success"] is True and data["figure_file"] and data["selected"]


def test_without_matplotlib_anywhere_the_tool_says_so_and_claims_no_plot(monkeypatch):
    monkeypatch.setattr(tool, "_python_with_matplotlib", lambda: None)
    data = _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit", draw_plot=True)["data"]
    assert data["success"] is True and data["figure_file"] is None and "matplotlib" in data["figure_note"]
    assert "figure_opened" not in data


def test_a_drawing_failure_never_fails_the_optimization(monkeypatch):
    import sys
    monkeypatch.setattr(tool, "_python_with_matplotlib", lambda: sys.executable)
    monkeypatch.setattr(tool.plotting, "write", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    data = _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit", draw_plot=True)["data"]
    assert data["success"] is True and data["figure_file"] is None and "boom" in data["figure_note"]


def test_the_figure_is_opened_with_the_platform_viewer_and_a_failure_is_reported(monkeypatch, tmp_path):
    seen = []

    def fake_run(command, **kwargs):
        seen.append(command)
        return type("done", (), {"returncode": 0})()

    monkeypatch.delenv("DISSOLVE_NO_OPEN")
    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    assert tool._open_file(tmp_path / "f.png") is True and seen == [["open", str(tmp_path / "f.png")]]
    monkeypatch.setattr(tool.sys, "platform", "linux")
    assert tool._open_file(tmp_path / "f.png") is True and seen[-1][0] == "xdg-open"
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no viewer")))
    assert tool._open_file(tmp_path / "f.png") is False


def test_the_viewer_is_not_launched_when_opening_is_switched_off(monkeypatch, tmp_path):
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: pytest.fail("a viewer was launched"))
    assert tool._open_file(tmp_path / "f.png") is False  # conftest sets DISSOLVE_NO_OPEN


def test_a_screen_reports_how_it_narrowed_the_solvent_pool(monkeypatch):
    table = {
        100.0: [_row("ethylene glycol", 10.6, 0.94), _row("toluene", 20.0, 2.0), _row("flashing solvent", 40.0, 0.1, margin=4.0),
                _row("propylene glycol", 12.4, 3.23)],  # over the retention limit
    }
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub(table))
    options = dl.stage_options(FEED, frozenset(), "EVOH", ["ethylene glycol", "toluene", "flashing solvent", "propylene glycol"],
                               max_options=1, temperature_min_c=100.0, temperature_max_c=100.0)
    assert options.screened and options.pool == 4
    assert options.passed_solubility == 2 and options.passed == 2  # two clear the dissolution tests, and both pass cooling
    assert len(options) == 1  # the cap of one costed option
    assert dl.stage_options(FEED, frozenset({"LDPE", "PET", "NYLON6"}), "EVOH", ["toluene"], max_options=3).screened is False


def test_the_stage_table_logs_each_screen_and_marks_reused_washes(monkeypatch):
    dl.clear_stage_rows()
    screens = {120.0: [_row("ethylene glycol", 12.0, 1.0), _row("toluene", 30.0, 2.0)]}
    monkeypatch.setattr(dl.thermodynamics, "screen_polymer_separation", _screen_stub(screens))
    monkeypatch.setattr(dl, "precipitated_pct", lambda polymer, solvents, temperature: {name: 0.1 for name in solvents})
    monkeypatch.setattr(dl.tea, "evaluate_process", lambda **k: json.dumps(
        {"data": {"engine_mode": "live", "comparison_rows": [dict(ROW, success=True)]}}))
    feed = wp.Feed(8000.0, {"LDPE": 0.9, "EVOH": 0.1})
    first = dl.build_stage_table(feed, max_washes=1, max_options=3, confirm_live_tea=True, solvents=["ethylene glycol", "toluene"],
                                 temperature_min_c=120.0, temperature_max_c=120.0)
    assert first.session_reused == set() and first.screen_log
    again = dl.build_stage_table(feed, max_washes=1, max_options=3, confirm_live_tea=False, solvents=["ethylene glycol", "toluene"],
                                 temperature_min_c=120.0, temperature_max_c=120.0)
    assert again.session_reused == set(again.stages) and len(again.stages) == len(first.stages)  # nothing simulated again
    log = {(x["polymer"], tuple(x["removed_before"])): x for x in again.screen_log}
    assert all(set(x) >= {"pool", "passed_solubility", "passed", "costed"} for x in log.values())
    dl.clear_stage_rows()


def test_the_result_carries_a_work_summary_with_counts_that_add_up(monkeypatch, tmp_path):
    table = _stub_table()
    table.screen_log.append({"removed_before": [], "polymer": "EVOH", "pool": 59, "passed_solubility": 4, "passed": 2, "costed": 2})
    data = _call(monkeypatch, table, pareto="emissions_vs_profit")["data"]
    w = data["work_summary"]
    assert w["simulations"] == {"washes_costed": 1, "live_simulations": 1, "simulated_in_this_call": 1, "stored_records": 0,
                                "reused_from_earlier_call": 0, "failed": 0, "simulation_seconds": 0.0}
    assert w["candidates"]["total"] == data["candidates_considered"] == sum(w["candidates"]["by_destination"].values())
    assert w["routes"]["total"] == sum(w["routes"]["by_number_of_washes"].values())
    assert set(w["fronts"]) == {"emissions_vs_profit", "circularity_vs_profit", "emissions_vs_circularity"}
    assert w["screens"][0]["passed"] == 2 and w["solvent_pool"]["costable_in_scope"] > 0
    assert _saved(tmp_path)["work_summary"] == w  # the saved request carries it for the figure


def test_a_reused_wash_is_counted_as_reused_not_as_simulated(monkeypatch):
    table = _stub_table()
    table.session_reused.add(next(iter(table.stages)))
    w = _call(monkeypatch, table)["data"]["work_summary"]
    sims = w["simulations"]
    assert sims["simulated_in_this_call"] == 0 and sims["reused_from_earlier_call"] == 1
    assert sims["live_simulations"] == 1  # the repeat call must not understate the simulations behind the answer


def test_draw_summary_writes_the_figure_only_when_asked(monkeypatch):
    plain = _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit")["data"]
    assert "summary_figure_file" not in plain
    if _matplotlib_python() is None:
        pytest.skip("no Python with matplotlib on this machine")
    from pathlib import Path
    data = _call(monkeypatch, _stub_table(), pareto="emissions_vs_profit", draw_summary=True)["data"]
    figure = Path(data["summary_figure_file"])
    assert figure.name.endswith("_work.png") and figure.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and data["summary_figure_opened"] is False
    assert "figure_file" not in data  # the fronts were not asked for
    both = _call(monkeypatch, _stub_table(), objective="max_profit", draw_plot=True, draw_summary=True)["data"]
    assert both["figure_file"] and both["summary_figure_file"] and both["figure_file"] != both["summary_figure_file"]


def test_without_matplotlib_the_summary_says_so_and_claims_no_figure(monkeypatch):
    monkeypatch.setattr(tool, "_python_with_matplotlib", lambda: None)
    data = _call(monkeypatch, _stub_table(), draw_summary=True)["data"]
    assert data["success"] is True and data["summary_figure_file"] is None and "matplotlib" in data["summary_figure_note"]


def test_the_agent_is_told_to_answer_what_was_done_from_the_work_summary():
    first = tool.optimize_waste_pathway.__doc__.split("\n\n")[0]
    assert "work_summary" in first and "draw_summary" in first and "summary_figure_file" in first
    assert "work_summary" in agent.SYSTEM_PROMPT and "draw_summary true" in agent.SYSTEM_PROMPT
    assert "not results" in agent.SYSTEM_PROMPT


def test_a_reused_wash_keeps_the_run_time_of_its_own_simulation(monkeypatch):
    dl.clear_stage_rows()
    clock = iter([100.0, 112.5])  # the one live run took 12.5 s
    monkeypatch.setattr(dl.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(dl.tea, "evaluate_process", lambda **k: json.dumps(
        {"data": {"engine_mode": "live", "comparison_rows": [dict(ROW, success=True)]}}))
    spec = _spec("EVOH", "Ethylene glycol", mass_in=8000.0, percent=10.0)
    first = dl.run_stage(spec, confirm_live_tea=True)
    again = dl.run_stage(spec, confirm_live_tea=False)
    assert first["wall_seconds"] == pytest.approx(12.5) and again["wall_seconds"] == pytest.approx(12.5) and again["session_reused"]
    dl.clear_stage_rows()


def test_the_result_says_which_downstream_technologies_have_a_recorded_capital_cost(monkeypatch):
    data = _call(monkeypatch, _stub_table())["data"]
    recorded = {r["key"]: r["capital_cost_recorded"] for r in data["downstream_technologies"]}
    assert recorded["gas_er"] is True and not any(recorded[k] for k in recorded if k != "gas_er")
    assert "hydrogen routes" in data["assumptions"]["downstream_capital"] and "favors" in data["assumptions"]["downstream_capital"]
