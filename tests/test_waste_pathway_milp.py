"""Waste-pathway MILP tests.

The numbers marked "notebook" come from the saved output of the published case study's Python run
(run_model.ipynb, scenario A, 8,000 t/yr of 60/20/10/10 PE/PET/N6/EVOH) and the Julia notebook's plot cells.
Only quantities that can be rebuilt from surviving data are asserted: the wash CAPEX and OPEX rows, prices,
transport rates, and the landfill and incineration coefficients. The wash GWP, energy, water and waste columns of the
original workbook are lost, so the washed pathways carry stand-in impacts and nothing here asserts their emissions.
"""
from __future__ import annotations

import math

import pytest

from dissolve import waste_pathway_milp as wp

FEED = wp.Feed(8000.0, {"PE": 0.6, "PET": 0.2, "N6": 0.1, "EVOH": 0.1})
# The published model drops the polymer a wash fails to recover instead of sending it downstream; the tests that reproduce its
# numbers use that convention, and the mass balance tests below use the default.
ECON = wp.Economics({"PE": 1173.0, "EVOH": 8100.0}, yield_loss_to_residual=False)
# The case study's upper bounds, so the all-landfill circularity can be compared with the notebook.
BOUNDS = wp.CircularityBounds(energy_mj=6.26e7, ghg_t=21303.35985408156, water_m3=14468.80855, waste_kg=1.92e6)

LANDFILL = wp.Technology(
    "lf", "Landfill", distance_mile=9.2, opex_usd_per_t=7.83599,
    impacts_per_t=wp.Impacts(energy_mj=453.74493269714236, disposal_t=1.0, gwp_t=0.0864563579568406),
)
INCINERATION = wp.Technology(
    "we", "Incineration", distance_mile=151.0, revenue_usd_per_t=110.0,
    impacts_per_t=wp.Impacts(energy_mj=418.193, renewable_mj=30.24, gwp_t=2.45971),
)
GASIFICATION = wp.Technology(
    "gas_er", "Gasification for energy recovery", distance_mile=0.0, revenue_usd_per_t=147.98008228,
    impacts_per_t=wp.Impacts(energy_mj=4976.45, renewable_mj=601.8, gwp_t=1.07),
    capex_ref_usd_yr=1.680302711e6, capex_ref_mass_t=2400.0,
)

PE_PCE = wp.WashStage("PE", "Tetrachloroethylene", 1376265.6805, 2154028.06, wp.Impacts(energy_mj=2.1e7, gwp_t=4500.0))
EVOH_GBL = wp.WashStage("EVOH", "gamma-butyrolactone", 650094.7785, 1520227.69, wp.Impacts(energy_mj=3.0e6, gwp_t=1926.76))
EVOH_PYR = wp.WashStage("EVOH", "Pyridazine", 667391.446, 1527148.15, wp.Impacts(energy_mj=3.0e6, gwp_t=1500.0))


def _candidate(stages, tech, bounds=BOUNDS):
    return wp.evaluate_candidate(wp.Pathway(tuple(stages)), tech, FEED, ECON, bounds)


def test_max_profit_route_reproduces_the_notebook_cost_breakdown():
    cand = _candidate([PE_PCE, EVOH_GBL], INCINERATION)
    assert cand.sales_usd == pytest.approx(12_011_088.0)  # 4,800 t PE and 800 t EVOH at 0.97, 2,400 t at $110
    assert cand.capex_usd == pytest.approx(2_026_360.46, abs=0.01)
    assert cand.opex_usd == pytest.approx(3_674_255.75, abs=0.01)
    assert cand.transport_usd == pytest.approx(66_304.0)  # 2,400 t over 151 mi, plus 8,000 t and 3,200 t into the washes
    assert cand.profit_usd == pytest.approx(6_244_167.79, abs=0.01)
    assert cand.residual_t == pytest.approx(2400.0)


def test_landfill_only_matches_the_notebook_profit_emissions_and_circularity():
    cand = _candidate([], LANDFILL)
    assert cand.profit_usd == pytest.approx(-91_919.92, abs=0.01)
    assert cand.emissions_t == pytest.approx(691.6508637, abs=1e-6)
    scores = cand.scores
    assert scores["energy"] == pytest.approx(0.5652080562788133, abs=1e-9)
    assert scores["ghg"] == pytest.approx(0.9675332497600274, abs=1e-9)
    assert scores["water"] == pytest.approx(1.0)
    assert scores["waste"] == pytest.approx(0.5)  # nothing generated, but every tonne goes to disposal
    assert scores["substitutability"] == 0.0
    assert cand.circularity == pytest.approx(0.606548261, abs=1e-8)


def test_gasification_capex_follows_the_six_tenths_rule_of_the_published_values():
    assert GASIFICATION.capex_usd_yr(2400.0) == pytest.approx(1.680302711e6)
    assert GASIFICATION.capex_usd_yr(3200.0) == pytest.approx(1.996874495e6, rel=1e-4)  # after PE is removed
    assert GASIFICATION.capex_usd_yr(7200.0) == pytest.approx(3.248331031e6, rel=1e-4)  # after EVOH is removed
    assert GASIFICATION.capex_usd_yr(0.0) == 0.0
    assert INCINERATION.capex_usd_yr(2400.0) == 0.0


def test_category_scores_at_the_edges():
    over = wp.Impacts(energy_mj=2e8, renewable_mj=1e8, direct_ghg_t=3e4, water_withdrawn_m3=2e4, water_recycled_m3=5e4,
                      waste_kg=3e6, disposal_t=0.0)
    scores = wp.category_scores(over, sales_usd=2e7, feed_t=8000.0, bounds=BOUNDS)
    assert scores["energy"] == pytest.approx(0.4 * 0.5)  # past the bound scores zero consumed; half is renewable
    assert scores["ghg"] == 0.0
    assert scores["water"] == pytest.approx(0.5 * 0.0 + 0.5 * 1.0)  # recycled share is capped at one
    assert scores["waste"] == pytest.approx(0.5 * 0.0 + 0.5 * 1.0)  # no disposal counts as fully diverted
    assert scores["substitutability"] == 1.0  # sales above the highest observed sales are held at one
    none = wp.category_scores(wp.Impacts(), 0.0, 8000.0, BOUNDS)
    assert none["energy"] == pytest.approx(0.6 + 0.4)  # no energy used: nothing consumed, renewable share one
    assert none["water"] == 1.0 and none["waste"] == 1.0


def test_wash_rules_reject_a_repeated_or_absent_polymer():
    with pytest.raises(ValueError, match="twice"):
        _candidate([PE_PCE, PE_PCE], LANDFILL)
    with pytest.raises(ValueError, match="not in the feed"):
        _candidate([wp.WashStage("PS", "toluene", 1.0, 1.0)], LANDFILL)
    with pytest.raises(ValueError, match="sum to"):
        wp.Feed(100.0, {"PE": 0.5})


def _problem():
    stage_table = {
        (1, "PE", "Tetrachloroethylene"): PE_PCE,
        (1, "EVOH", "Pyridazine"): EVOH_PYR,
        (2, "EVOH", "gamma-butyrolactone"): EVOH_GBL,
        (2, "EVOH", "Pyridazine"): EVOH_PYR,
        (2, "PE", "Tetrachloroethylene"): PE_PCE,
    }
    paths = wp.pathways_from_stage_table(stage_table, FEED)
    return paths, wp.PathwayProblem.build(paths, [LANDFILL, INCINERATION, GASIFICATION], FEED, ECON, bounds=BOUNDS)


def test_stage_table_expands_to_every_allowed_wash_sequence():
    paths, problem = _problem()
    labels = {p.label for p in paths}
    assert "no wash" in labels
    assert "PE/Tetrachloroethylene" in labels and "EVOH/Pyridazine" in labels
    assert "PE/Tetrachloroethylene > EVOH/gamma-butyrolactone" in labels
    assert "EVOH/Pyridazine > PE/Tetrachloroethylene" in labels
    assert not any(label.count("EVOH") > 1 or label.count("PE/") > 1 for label in labels)  # one removal per polymer
    assert len(problem.candidates) == len(paths) * 3


@pytest.mark.parametrize("objective", ["max_profit", "min_emissions", "max_circularity"])
def test_milp_agrees_with_enumeration_for_each_objective(objective):
    _, problem = _problem()
    answer = wp.optimize(problem, objective)
    assert answer["agrees_with_enumeration"] is True
    assert answer["candidate"] is not None
    metric, maximize = wp.OBJECTIVES[objective]
    values = [c.metric(metric) for c in problem.candidates]
    assert answer["candidate"].metric(metric) == pytest.approx(max(values) if maximize else min(values))


def test_max_profit_washes_pe_then_evoh_and_min_emissions_landfills_everything():
    _, problem = _problem()
    assert wp.optimize(problem, "max_profit")["candidate"].label.endswith(("+ we", "+ gas_er"))
    assert "PE/Tetrachloroethylene" in wp.optimize(problem, "max_profit")["candidate"].label
    assert wp.optimize(problem, "min_emissions")["candidate"].label == "no wash + lf"


def test_limits_constrain_the_choice():
    _, problem = _problem()
    free = wp.optimize(problem, "max_profit")["candidate"]
    capped = wp.optimize(problem, "max_profit", {"emissions": ("<=", free.emissions_t * 0.5)})["candidate"]
    assert capped.emissions_t <= free.emissions_t * 0.5 + 1e-6
    assert capped.profit_usd <= free.profit_usd
    infeasible = wp.optimize(problem, "max_profit", {"emissions": ("<=", -1.0)})
    assert infeasible["candidate"] is None and infeasible["agrees_with_enumeration"] is True


def test_pareto_front_is_non_dominated_and_ordered():
    _, problem = _problem()
    front = wp.pareto_front(problem.candidates, "emissions", "profit")
    emissions = [c.emissions_t for c in front]
    profits = [c.profit_usd for c in front]
    assert emissions == sorted(emissions)
    assert profits == sorted(profits)  # more profit is only available at more emissions
    for cand in front:
        assert not any(
            o.emissions_t <= cand.emissions_t and o.profit_usd >= cand.profit_usd
            and (o.emissions_t < cand.emissions_t or o.profit_usd > cand.profit_usd)
            for o in problem.candidates
        )
    assert front[0].label == "no wash + lf" and front[-1].profit_usd == max(c.profit_usd for c in problem.candidates)
    with pytest.raises(ValueError):
        wp.pareto_front(problem.candidates, "profit", "profit")


def test_epsilon_sweep_lands_on_the_exact_front():
    _, problem = _problem()
    front_labels = {c.label for c in wp.pareto_front(problem.candidates, "emissions", "profit")}
    points = wp.epsilon_front(problem, primary="profit", bounded="emissions", steps=40)
    assert points and all(p["agrees_with_enumeration"] for p in points)
    assert {p["candidate"].label for p in points} <= front_labels
    assert {"no wash + lf"} <= {p["candidate"].label for p in points}


def test_location_scenario_changes_only_downstream_distances():
    far = wp.with_location([LANDFILL, INCINERATION], {"we": 76.1})
    assert [t.distance_mile for t in far] == [9.2, 76.1]
    near_cost = wp.evaluate_candidate(wp.Pathway((PE_PCE, EVOH_GBL)), far[1], FEED, ECON, BOUNDS)
    base = _candidate([PE_PCE, EVOH_GBL], INCINERATION)
    assert base.transport_usd - near_cost.transport_usd == pytest.approx(2400.0 * (151.0 - 76.1) * 0.07)
    assert near_cost.profit_usd > base.profit_usd


def test_default_bounds_are_one_and_a_half_times_the_candidate_mean():
    paths = [wp.Pathway(), wp.Pathway((PE_PCE,))]
    bounds = wp.default_bounds(paths, [LANDFILL], FEED, ECON)
    energies = [
        wp.evaluate_candidate(p, LANDFILL, FEED, ECON, wp.CircularityBounds(1, 1, 1, 1)).totals.energy_mj for p in paths
    ]
    assert bounds.energy_mj == pytest.approx(1.5 * sum(energies) / 2)
    assert math.isfinite(bounds.ghg_t) and bounds.water_m3 > 0 and bounds.waste_kg > 0


def test_a_technology_refuses_residuals_it_cannot_take():
    pyrolysis = wp.Technology("py", "Pyrolysis", distance_mile=1034.0, opex_usd_per_t=153.4067,
                              impacts_per_t=wp.Impacts(gwp_t=0.682266), excluded_polymers=frozenset({"PET"}))
    paths = [wp.Pathway(), wp.Pathway((PE_PCE, EVOH_GBL))]
    problem = wp.PathwayProblem.build(paths, [LANDFILL, pyrolysis], FEED, ECON, bounds=BOUNDS)
    assert {c.technology.key for c in problem.candidates} == {"lf"}  # PET stays in every residual here
    no_pet = wp.Feed(1000.0, {"PE": 0.6, "EVOH": 0.4})
    ok = wp.PathwayProblem.build([wp.Pathway((PE_PCE, EVOH_GBL))], [pyrolysis], no_pet, ECON, bounds=BOUNDS)
    assert [c.technology.key for c in ok.candidates] == ["py"]


def test_ties_break_toward_lower_emissions_then_higher_circularity():
    clean = wp.Technology("gas_h2cc", "Gasification with carbon capture", distance_mile=76.1, revenue_usd_per_t=110.0,
                          impacts_per_t=wp.Impacts(energy_mj=9665.56, renewable_mj=906.9, gwp_t=2.55583))
    dirty = wp.Technology("gas_h2", "Gasification", distance_mile=76.1, revenue_usd_per_t=110.0,
                          impacts_per_t=wp.Impacts(energy_mj=8290.26, renewable_mj=741.8, gwp_t=5.42838))
    paths = [wp.Pathway((PE_PCE, EVOH_GBL))]
    problem = wp.PathwayProblem.build(paths, [dirty, clean], FEED, ECON, bounds=BOUNDS)
    a, b = problem.candidates
    assert a.profit_usd == pytest.approx(b.profit_usd) and a.emissions_t > b.emissions_t  # a genuine profit tie
    for order in (problem.candidates, list(reversed(problem.candidates))):
        assert wp.enumerate_optimum(order, "max_profit").technology.key == "gas_h2cc"  # independent of input order
    answer = wp.optimize(problem, "max_profit")
    assert answer["candidate"].technology.key == "gas_h2cc" and answer["agrees_with_enumeration"] is True
    # an emissions tie goes to the more profitable, then the more circular pathway
    same = [wp.Candidate(**{**vars(a), "emissions_t": 5.0, "profit_usd": 1.0, "circularity": 0.2}),
            wp.Candidate(**{**vars(a), "emissions_t": 5.0, "profit_usd": 2.0, "circularity": 0.1})]
    assert wp.enumerate_optimum(same, "min_emissions").profit_usd == 2.0


def test_substitutability_runs_from_the_lowest_to_the_highest_sales_observed():
    zero = wp.CircularityBounds(1e9, 1e9, 1e9, 1e9, sales_low_usd=1_000_000.0, sales_high_usd=5_000_000.0)
    score = lambda sales: wp.category_scores(wp.Impacts(), sales, 8000.0, zero)["substitutability"]  # noqa: E731
    assert score(1_000_000.0) == 0.0 and score(5_000_000.0) == 1.0 and score(3_000_000.0) == pytest.approx(0.5)
    assert score(0.0) == 0.0 and score(9e9) == 1.0
    flat = wp.CircularityBounds(1e9, 1e9, 1e9, 1e9, sales_low_usd=2.0, sales_high_usd=2.0)
    assert wp.category_scores(wp.Impacts(), 2.0, 8000.0, flat)["substitutability"] == 0.0


def test_default_bounds_scale_substitutability_to_the_feed_not_to_the_published_constant():
    prices = {"PE": 1173.0, "PET": 1380.0, "N6": 2800.0, "EVOH": 8100.0}
    published = wp.Economics(prices)
    assert wp.full_recovery_sales_usd(FEED, published) == pytest.approx(wp.SUBSTITUTABILITY_NORM_USD, rel=0.003)
    big = wp.Feed(80_000.0, {"PE": 0.6, "PET": 0.2, "N6": 0.1, "EVOH": 0.1})  # ten times the published feed
    paths = [wp.Pathway(), wp.Pathway((PE_PCE,)), wp.Pathway((PE_PCE, EVOH_GBL))]
    problem = wp.PathwayProblem.build(paths, [LANDFILL, INCINERATION], big, published)
    assert problem.bounds.sales_low_usd == 0.0
    assert problem.bounds.sales_high_usd == pytest.approx(10 * wp.full_recovery_sales_usd(FEED, published))
    assert all(0.0 <= c.scores["substitutability"] <= 1.0 and 0.0 <= c.circularity <= 1.0 for c in problem.candidates)
    best = max(problem.candidates, key=lambda c: c.sales_usd)
    assert best.scores["substitutability"] == pytest.approx(best.sales_usd / problem.bounds.sales_high_usd)


def test_substitutability_does_not_depend_on_which_pathways_are_enumerated():
    prices = {"PE": 1173.0, "EVOH": 8100.0}
    only_landfill = wp.PathwayProblem.build([wp.Pathway()], [LANDFILL, INCINERATION], FEED, wp.Economics(prices))
    with_washes = wp.PathwayProblem.build([wp.Pathway(), wp.Pathway((PE_PCE,))], [LANDFILL, INCINERATION], FEED,
                                          wp.Economics(prices))
    assert only_landfill.bounds.sales_high_usd == with_washes.bounds.sales_high_usd
    incinerate = lambda p: next(c for c in p.candidates if c.pathway.label == "no wash" and c.technology.key == "we")  # noqa: E731
    assert incinerate(only_landfill).scores["substitutability"] == pytest.approx(incinerate(with_washes).scores["substitutability"])
    assert incinerate(only_landfill).scores["substitutability"] < 0.1  # $0.88 million of a possible $12 million, not "the best"



def test_circularity_ghg_falls_back_to_gwp_part_by_part():
    split = wp.Impacts(direct_ghg_t=3.0, indirect_ghg_t=1.0, gwp_t=9.0)   # a part that reports its own split
    gwp_only = wp.Impacts(gwp_t=5.0)                                      # a part that reports GWP alone
    assert split.ghg_t == 4.0 and gwp_only.ghg_t == 5.0
    assert (split + gwp_only).ghg_t == 9.0 and (gwp_only + split).ghg_t == 9.0   # order does not matter
    assert (split + gwp_only + gwp_only).ghg_t == 14.0
    assert gwp_only.scaled(3.0).ghg_t == 15.0 and (split + gwp_only).scaled(2.0).ghg_t == 18.0
    assert (split + gwp_only).gwp_t == 14.0  # the emissions objective still sums GWP
    assert (wp.Impacts() + wp.Impacts()).ghg_t == 0.0


def test_a_downstream_technology_without_split_emissions_still_counts_against_a_washed_pathway():
    split_wash = wp.WashStage("PE", "x", 1.0, 1.0, wp.Impacts(direct_ghg_t=1000.0, gwp_t=1000.0))
    tech = wp.Technology("t", "t", impacts_per_t=wp.Impacts(gwp_t=2.0))   # GWP only
    washed = wp.evaluate_candidate(wp.Pathway((split_wash,)), tech, FEED, ECON, BOUNDS)
    bare = wp.evaluate_candidate(wp.Pathway(), tech, FEED, ECON, BOUNDS)
    assert washed.totals.ghg_t == pytest.approx(1000.0 + 2.0 * 3200.0)   # the 3,200 t residual counts
    assert bare.totals.ghg_t == pytest.approx(2.0 * 8000.0)


def test_mass_is_conserved_when_a_wash_recovers_less_than_all_of_its_polymer():
    kept = wp.Economics({"PE": 1173.0, "EVOH": 8100.0})  # default: the unrecovered polymer goes downstream
    published = wp.Economics({"PE": 1173.0, "EVOH": 8100.0}, yield_loss_to_residual=False)
    pathway = wp.Pathway((PE_PCE,))
    a = wp.evaluate_candidate(pathway, LANDFILL, FEED, kept, BOUNDS)
    b = wp.evaluate_candidate(pathway, LANDFILL, FEED, published, BOUNDS)
    recovered = 4800.0 * 0.97
    assert a.residual_t == pytest.approx(8000.0 - recovered)           # nothing disappears
    assert b.residual_t == pytest.approx(8000.0 - 4800.0)              # the published convention loses 144 t
    assert a.sales_usd == pytest.approx(b.sales_usd) == pytest.approx(recovered * 1173.0)
    assert a.totals.disposal_t == pytest.approx(a.residual_t)           # and the extra tonnes are landfilled


def test_a_wash_that_reports_its_own_recovery_overrides_the_assumed_yield():
    stage = wp.WashStage("PE", "x", 0.0, 0.0, recovered_fraction=0.80)
    cand = wp.evaluate_candidate(wp.Pathway((stage,)), LANDFILL, FEED, wp.Economics({"PE": 1000.0}), BOUNDS)
    assert cand.sales_usd == pytest.approx(4800.0 * 0.80 * 1000.0)
    assert cand.residual_t == pytest.approx(3200.0 + 4800.0 * 0.20)


def test_scores_stay_within_zero_and_one_even_for_a_negative_burden():
    credit = wp.Technology("c", "credit", impacts_per_t=wp.Impacts(energy_mj=-1e5, gwp_t=-2.0, waste_kg=-50.0))
    cand = wp.evaluate_candidate(wp.Pathway(), credit, FEED, ECON, BOUNDS)
    assert all(0.0 <= value <= 1.0 for value in cand.scores.values()) and 0.0 <= cand.circularity <= 1.0


def _binary():
    feed = wp.Feed(1000.0, {"PE": 0.7, "EVOH": 0.3})
    econ = wp.Economics({"PE": 1173.0, "EVOH": 8100.0}, yield_loss_to_residual=False)
    return feed, econ


def test_a_pure_residual_after_a_wash_can_be_sold_as_resin():
    feed, econ = _binary()
    paths = [wp.Pathway(), wp.Pathway((EVOH_GBL,)), wp.Pathway((PE_PCE,))]
    problem = wp.PathwayProblem.build(paths, [LANDFILL, INCINERATION], feed, econ, bounds=BOUNDS)
    sold = [c for c in problem.candidates if c.technology.key == "resale"]
    assert {c.pathway.label for c in sold} == {"EVOH/gamma-butyrolactone", "PE/Tetrachloroethylene"}   # never the unwashed feed
    pe_sold = next(c for c in sold if c.pathway.label.startswith("EVOH"))   # EVOH washed out, 700 t of clean PE left
    assert pe_sold.residual_t == pytest.approx(700.0 + 0.0)
    assert pe_sold.sales_usd == pytest.approx(300.0 * 0.97 * 8100.0 + 700.0 * 1173.0)   # recovered EVOH plus the PE sold
    assert pe_sold.totals.disposal_t == 0.0 and pe_sold.technology.label == "sold as PE resin"
    landfilled = next(c for c in problem.candidates if c.pathway.label.startswith("EVOH") and c.technology.key == "lf")
    assert pe_sold.profit_usd > landfilled.profit_usd   # selling clean PE beats landfilling it


def test_resale_needs_a_single_priced_polymer_left():
    feed = wp.Feed(1000.0, {"PE": 0.5, "PET": 0.3, "EVOH": 0.2})
    econ = wp.Economics({"PE": 1173.0, "PET": 1380.0, "EVOH": 8100.0})
    mixed = wp.applicable_technologies(wp.Pathway((EVOH_GBL,)), [LANDFILL], feed, econ)
    assert [t.key for t in mixed] == ["lf"]                      # PE and PET left: no single resin to sell
    two = wp.applicable_technologies(wp.Pathway((EVOH_GBL, PE_PCE)), [LANDFILL], feed, econ)
    assert [t.key for t in two] == ["lf", "resale"] and two[-1].revenue_usd_per_t == 1380.0
    unpriced = wp.applicable_technologies(wp.Pathway((EVOH_GBL, PE_PCE)), [LANDFILL], feed, wp.Economics({"PE": 1.0, "EVOH": 1.0}))
    assert [t.key for t in unpriced] == ["lf"]                   # PET has no price: nothing to sell it for


def test_the_binary_film_answer_sells_the_clean_polymer_rather_than_burning_it():
    feed, econ = _binary()
    paths = [wp.Pathway(), wp.Pathway((EVOH_GBL,))]
    problem = wp.PathwayProblem.build(paths, [LANDFILL, INCINERATION], feed, econ, bounds=BOUNDS)
    best = wp.optimize(problem, "max_profit")["candidate"]
    assert best.technology.key == "resale" and best.pathway.label == "EVOH/gamma-butyrolactone"


_OBJECTIVE = {"profit": "max_profit", "emissions": "min_emissions", "circularity": "max_circularity"}
_PAIRS = [("emissions", "profit"), ("circularity", "profit"), ("emissions", "circularity")]


def _dominates(a, b, x, y):
    sign = {"profit": 1.0, "circularity": 1.0, "emissions": -1.0}
    ax, ay, bx, by = (sign[x] * a.metric(x), sign[y] * a.metric(y), sign[x] * b.metric(x), sign[y] * b.metric(y))
    return ax >= bx and ay >= by and (ax > bx or ay > by)


@pytest.mark.parametrize("x, y", _PAIRS)
def test_every_pair_of_metrics_gives_a_non_dominated_front(x, y):
    _, problem = _problem()
    front = wp.pareto_front(problem.candidates, x, y)
    assert len(front) >= 2
    for cand in front:
        assert not any(_dominates(other, cand, x, y) for other in problem.candidates)
    # every candidate off the front is dominated by, or equal to, a front member
    for cand in problem.candidates:
        assert any(f is cand or _dominates(f, cand, x, y) or (f.metric(x), f.metric(y)) == (cand.metric(x), cand.metric(y))
                   for f in front)


@pytest.mark.parametrize("bounded, primary", [(a, b) for a, b in _PAIRS] + [(b, a) for a, b in _PAIRS])
def test_epsilon_sweep_in_either_direction_agrees_with_enumeration_and_stays_on_the_front(bounded, primary):
    _, problem = _problem()
    points = wp.epsilon_front(problem, primary=primary, bounded=bounded, steps=40)
    assert points and all(p["agrees_with_enumeration"] for p in points)
    front = wp.pareto_front(problem.candidates, bounded, primary)
    keys = {(round(f.metric(bounded), 6), round(f.metric(primary), 6)) for f in front}
    for p in points:  # a point the sweep reports is non-dominated for this pair
        assert not any(_dominates(o, p["candidate"], bounded, primary) for o in problem.candidates), p["candidate"].label


@pytest.mark.parametrize("x, y", _PAIRS)
def test_the_epsilon_constraint_reaches_every_member_of_the_exact_front(x, y):
    _, problem = _problem()
    for member in wp.pareto_front(problem.candidates, x, y):
        limit = member.metric(x)
        slack = 1e-9 * max(1.0, abs(limit))
        constraint = ("<=", limit + slack) if x == "emissions" else (">=", limit - slack)
        answer = wp.optimize(problem, _OBJECTIVE[y], {x: constraint})
        assert answer["agrees_with_enumeration"] is True
        assert answer["candidate"].metric(y) == pytest.approx(member.metric(y), rel=1e-9, abs=1e-6), member.label
