"""tea_tornado: the one-at-a-time TEA/LCA sensitivity of a solvent wash, ranked as a tornado."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dissolve import agent, tea_tornado as tt, tea_tornado_plot as plot, waste_pathway_data as dl


def _toy_row(cfg, engine_mode="live"):
    """A costed row whose results depend on the config in a known way: MSP rises with irr and falls with capacity, GWP falls with
    polymer loading and ignores everything else."""
    return {"msp_usd_per_kg": 1.0 + 4.0 * cfg["irr"] - cfg["processing_capacity_mt_per_yr"] / 1e5,
            "gwp_kg_co2e_per_kg": 0.5 - 0.02 * cfg["dissolution_capacity"], "tci_usd": 1e7, "aoc_usd_per_yr": 1e6,
            "total_energy_mj_per_kg": 5.0, "engine_mode": engine_mode}


@pytest.fixture
def toy(monkeypatch):
    calls = []

    def fake(cfg, *, confirm_live_tea):
        calls.append((dict(cfg), confirm_live_tea))
        return _toy_row(cfg)

    monkeypatch.setattr(dl, "run_process_config", fake)
    return calls


def _call(**kwargs):
    base = {"polymer": "LDPE", "solvent": "p-Xylene", "confirm_live_tea": True}
    return json.loads(tt.tea_tornado(**{**base, **kwargs}))["data"]


def test_default_ranges_follow_each_parameters_own_rule():
    assert tt.default_range("irr", 0.10, None) == pytest.approx((0.07, 0.13))
    assert tt.default_range("dissolution_temperature_c", 120.0, None) == (110.0, 130.0)
    assert tt.default_range("solvent_loss_pct", 0.01, None) == (0.01, 1.0)  # the process model's own range, one-sided at the base
    assert tt.default_range("feedstock_distance_km", 0.0, None) == (0.0, 500.0)
    assert tt.default_range("labor_cost_usd_per_employee_yr", 1e5, 0.1) == pytest.approx((9e4, 1.1e5))  # a caller fraction wins
    assert tt.default_range("maintenance", 0.0, 0.2) is None  # a relative range of zero is no range
    assert tt.default_range("lang_factor", 3.0, None) is None  # no rule: the caller must give a range


def test_it_refuses_input_it_cannot_run(toy):
    cases = [({"polymer": "unobtainium"}, "invalid_input"), ({"solvent": "not a solvent"}, "no_costable_solvent"),
             ({"metrics": ["profit"]}, "invalid_input"), ({"metrics": ["msp_usd_per_kg", "msp_usd_per_kg"]}, "invalid_input"),
             ({"parameters": ["colour"]}, "unsupported_parameter"), ({"parameters": ["solvent"]}, "unsupported_parameter"),
             ({"parameters": ["irr", "irr"]}, "invalid_input"), ({"parameters": ["irr"], "ranges": {"irr": [0.1]}}, "invalid_input"),
             ({"parameters": ["irr"], "ranges": {"irr": [0.1, 0.1]}}, "invalid_input"),
             ({"parameters": ["feedstock_distance_km"], "relative_range": 0.2}, "range_required"),
             ({"parameters": ["irr"], "relative_range": 1.5}, "invalid_input"), ({"target_mass_percent": 150}, "invalid_input"),
             ({"processing_capacity_mt_per_yr": 0}, "invalid_input"),
             ({"parameters": [f"maintenance"] * 2}, "invalid_input")]
    for kwargs, code in cases:
        out = _call(**kwargs)
        assert out["success"] is False and out["error_code"] == code, (kwargs, out.get("error_code"), out.get("error"))
    assert not toy  # nothing was simulated for a request that could not run


def test_a_parameter_with_no_default_range_runs_when_the_user_gives_one(toy):
    out = _call(parameters=["maintenance"], ranges={"maintenance": [0.02, 0.05]})
    assert out["success"] is True and out["sensitivity_rows"][0]["low_value"] == 0.02 and out["sensitivity_rows"][0]["high_value"] == 0.05


def test_it_asks_before_simulating_and_counts_each_distinct_run(monkeypatch):
    def unconfirmed(cfg, *, confirm_live_tea):
        if not confirm_live_tea:
            raise dl.StageUnavailable("live_tea_cost_confirmation_required", "x")
        return _toy_row(cfg)

    monkeypatch.setattr(dl, "run_process_config", unconfirmed)
    out = _call(parameters=["irr", "solvent_loss_pct"], confirm_live_tea=False)
    # the base, irr low and high, and solvent loss high: solvent loss low equals the base, so it is not run twice
    assert out["error_code"] == "live_tea_cost_confirmation_required" and out["simulations_needed"] == 4
    assert "4 live BioSTEAM simulations" in out["error"] and "confirm_live_tea=true" in out["error"]
    default = _call(confirm_live_tea=False)
    assert default["simulations_needed"] == 16 and len(default["parameters_requested"]) == 8  # 8 parameters, one lies on the base


def test_the_tornado_ranks_parameters_by_how_far_they_move_each_metric(toy):
    out = _call(parameters=["irr", "processing_capacity_mt_per_yr", "dissolution_capacity", "operating_days"],
                ranges={"processing_capacity_mt_per_yr": [10000, 30000], "dissolution_capacity": [1.5, 4.5]})
    assert out["success"] is True and out["analysis_type"] == "tea_tornado" and out["basis"] == "tea_live"
    rows = {r["parameter"]: r for r in out["sensitivity_rows"]}
    msp = rows["irr"]["metrics"]["msp_usd_per_kg"]
    assert msp["at_low"] == pytest.approx(1.0 + 4 * 0.07 - 0.2) and msp["at_high"] == pytest.approx(1.0 + 4 * 0.13 - 0.2)
    assert msp["swing"] == pytest.approx(4 * 0.06) and msp["swing_pct_of_base"] == pytest.approx(100 * 0.24 / msp["base"])
    assert out["ranking"]["msp_usd_per_kg"][0] == "irr" and out["ranking"]["gwp_kg_co2e_per_kg"][0] == "dissolution_capacity"
    assert rows["operating_days"]["metrics"]["msp_usd_per_kg"]["swing"] == 0.0  # the toy model ignores it
    assert out["simulations"] == {"distinct_runs": 9, "live": 9, "stored_or_reused": 0, "failed": 0}
    keys = {json.dumps(c, sort_keys=True, default=str) for c, _ in toy}
    assert len(toy) == 9 and len(keys) == 9  # nine distinct simulations, none repeated


def test_every_run_moves_only_the_one_parameter_from_the_same_base(toy):
    _call(parameters=["irr", "dissolution_capacity"])
    configs = [c for c, _ in toy]
    base = next(c for c in configs if c["irr"] == 0.1 and c["dissolution_capacity"] == 3.0)
    for cfg in configs:
        differing = [k for k in base if cfg.get(k) != base[k]]
        assert len(differing) <= 1 and set(differing) <= {"irr", "dissolution_capacity"}
    assert base["target_mass_percent"] == 60.0 and base["processing_capacity_mt_per_yr"] == 20000.0 and base["target_polymer"] == "LDPE"


def test_the_base_and_what_was_assumed_are_reported(toy):
    out = _call(parameters=["irr"], target_mass_percent=45, dissolution_temperature_c=110)
    assert out["base"]["target_mass_percent"] == 45.0 and out["base"]["dissolution_temperature_c"] == 110.0
    assert out["base"]["assumed_by_default"] == ["processing_capacity_mt_per_yr"]
    assert out["base"]["solvent"] == "p-Xylene"  # the name the user wrote
    default = _call(parameters=["irr"])
    assert set(default["base"]["assumed_by_default"]) == {"target_mass_percent", "processing_capacity_mt_per_yr", "dissolution_temperature_c"}


def test_default_ranges_are_stated_and_user_ranges_are_not_called_defaults(toy):
    out = _call(parameters=["irr", "dissolution_capacity"], ranges={"irr": [0.08, 0.12]})
    text = " ".join(out["warnings"])
    assert "default ranges" in text and "dissolution_capacity" in text and "irr" not in text.split("default ranges")[1]
    assert "default ranges" not in " ".join(_call(parameters=["irr"], relative_range=0.2)["warnings"])


def test_a_parameter_with_no_effect_is_named_not_hidden(toy):
    out = _call(parameters=["irr", "operating_days"])
    assert any("No measurable change" in w and "Operating days per year" in w for w in out["warnings"])


def test_a_failed_side_keeps_the_other_side_and_is_reported(monkeypatch):
    def fake(cfg, *, confirm_live_tea):
        if cfg["dissolution_temperature_c"] == 130.0:
            raise dl.StageUnavailable("simulation_failed", "above the boiling margin")
        return _toy_row(cfg)

    monkeypatch.setattr(dl, "run_process_config", fake)
    out = _call(parameters=["dissolution_temperature_c", "irr"])
    row = next(r for r in out["sensitivity_rows"] if r["parameter"] == "dissolution_temperature_c")
    assert row["failed_runs"] == {"high": "simulation_failed: above the boiling margin"}
    assert row["metrics"]["msp_usd_per_kg"]["at_high"] is None and row["metrics"]["msp_usd_per_kg"]["at_low"] is not None
    assert out["simulations"]["failed"] == 1 and any("failed" in w for w in out["warnings"])


def test_a_base_that_cannot_be_simulated_is_an_error(monkeypatch):
    def fake(cfg, *, confirm_live_tea):
        raise dl.StageUnavailable("simulation_failed", "no")

    monkeypatch.setattr(dl, "run_process_config", fake)
    assert _call(parameters=["irr"])["error_code"] == "base_run_failed"


def test_stored_records_give_a_cache_basis(monkeypatch):
    monkeypatch.setattr(dl, "run_process_config", lambda cfg, *, confirm_live_tea: _toy_row(cfg, "cache"))
    out = _call(parameters=["irr"])
    assert out["basis"] == "tea_cache_exact" and out["simulations"]["live"] == 0 and agent.source_basis_for("tea_tornado", out, {}) == "tea_cache_exact"


def test_the_result_is_saved_for_the_figure(toy, tmp_path):
    out = _call(parameters=["irr", "dissolution_capacity"])
    saved = json.loads((tmp_path / "pathway_results" / "latest_tornado.json").read_text())
    assert out["figure_data_file"] == str(tmp_path / "pathway_results" / "latest_tornado.json")
    assert saved["schema"] == plot.SCHEMA and [r["parameter"] for r in saved["rows"]] == ["irr", "dissolution_capacity"]
    assert "figure_file" not in out  # nothing is drawn unless asked


def test_an_unwritable_results_folder_never_fails_the_analysis(toy, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("DISSOLVE_PATHWAY_RESULTS_DIR", str(blocker / "inside"))
    out = _call(parameters=["irr"], draw_plot=True)
    assert out["success"] is True and out["figure_data_file"] is None and out["figure_file"] is None


def _matplotlib_python():
    from dissolve import figure_support
    return figure_support._python_with_matplotlib.__wrapped__()


def test_draw_plot_writes_the_figure_and_its_table_without_opening_a_window(toy):
    if _matplotlib_python() is None:
        pytest.skip("no Python with matplotlib on this machine")
    out = _call(parameters=["irr", "dissolution_capacity", "operating_days"], draw_plot=True)
    figure = Path(out["figure_file"])
    assert figure.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and figure.stat().st_size > 10_000 and out["figure_opened"] is False
    assert Path(out["figure_table_file"]).read_text().startswith("# Tornado: LDPE washed in p-Xylene")


def test_without_matplotlib_anywhere_the_tool_says_so_and_claims_no_plot(toy, monkeypatch):
    from dissolve import figure_support
    monkeypatch.setattr(figure_support, "_python_with_matplotlib", lambda: None)
    out = _call(parameters=["irr"], draw_plot=True)
    assert out["success"] is True and out["figure_file"] is None and "matplotlib" in out["figure_note"] and "figure_opened" not in out


def test_the_agent_is_told_when_and_how_to_use_the_tornado():
    first = tt.tea_tornado.__doc__.split("\n\n")[0]
    for word in ("tornado", "optimize_waste_pathway", "analyze_tea_sensitivity", "relative_range", "confirm_live_tea", "draw_plot", "figure_note"):
        assert word in first, word
    for word in ("tea_tornado", "ranges win", "default ranges", "figure_note", "not a pathway's profit"):
        assert word in agent.SYSTEM_PROMPT, word
    assert "tea_tornado" in {t.name for t in agent.REGISTRY}


# --- the plotting module: data handling runs here, drawing in whichever Python has matplotlib ---

def _data():
    def cell(base, lo, hi):
        reach = [v for v in (lo, hi) if v is not None]
        swing = max(reach + [base]) - min(reach + [base])
        return {"base": base, "at_low": lo, "at_high": hi, "swing": swing, "swing_pct_of_base": swing / base * 100}

    rows = [
        {"parameter": "irr", "label": "Internal rate of return", "unit": "fraction", "group": "economic", "base_value": 0.1, "low_value": 0.07,
         "high_value": 0.13, "metrics": {"msp_usd_per_kg": cell(1.1, 0.87, 1.4), "gwp_kg_co2e_per_kg": cell(0.47, 0.47, 0.47)}, "failed_runs": {}},
        {"parameter": "dissolution_capacity", "label": "Polymer loading in solvent", "unit": "wt/vol %", "group": "process", "base_value": 3.0,
         "low_value": 1.5, "high_value": 4.5, "metrics": {"msp_usd_per_kg": cell(1.1, 1.48, 0.99), "gwp_kg_co2e_per_kg": cell(0.47, 0.54, 0.45)},
         "failed_runs": {}},
    ]
    return {"schema": plot.SCHEMA, "polymer": "LDPE", "solvent": "p-Xylene", "basis": "tea_live",
            "base": {"target_mass_percent": 60.0, "processing_capacity_mt_per_yr": 20000.0, "dissolution_temperature_c": 120.0,
                     "solvent_price_usd_per_kg": 1.11, "assumed_by_default": ["target_mass_percent"],
                     "metrics": {"msp_usd_per_kg": 1.1, "gwp_kg_co2e_per_kg": 0.47}},
            "metrics": [{"metric": "msp_usd_per_kg", "label": "Minimum selling price", "unit": "$/kg"},
                        {"metric": "gwp_kg_co2e_per_kg", "label": "Global warming potential", "unit": "kg CO2e/kg"}],
            "rows": rows, "ranking": {}}


def test_each_panel_orders_parameters_by_its_own_swing_and_labels_the_range():
    data = _data()
    assert [r["parameter"] for r in plot.ordered_rows(data, "msp_usd_per_kg")] == ["irr", "dissolution_capacity"]
    assert [r["parameter"] for r in plot.ordered_rows(data, "gwp_kg_co2e_per_kg")] == ["dissolution_capacity", "irr"]
    assert plot.range_label(data["rows"][1]) == "Polymer loading in solvent\n1.5 – 4.5 wt/vol %"
    assert plot.fmt(None) == "n/a" and plot.fmt(20000.0) == "20,000" and plot.fmt(0.4716, 3) == "0.472" and plot.fmt(2.5e-5) == "2.50e-05"


def test_the_table_gives_every_parameter_with_its_values_and_swing():
    text = plot.tornado_markdown(_data())
    assert text.startswith("# Tornado: LDPE washed in p-Xylene") and "Assumed by default (not given): target_mass_percent." in text
    assert "## Minimum selling price ($/kg); base 1.1" in text and "## Global warming potential (kg CO2e/kg); base 0.47" in text
    assert "| Internal rate of return (fraction) | 0.07 | 0.13 | 0.87 | 1.4 | 0.53 | 48.2% |" in text


def test_a_file_that_is_not_a_saved_tornado_is_refused(tmp_path):
    other = tmp_path / "x.json"
    other.write_text(json.dumps({"schema": "nope"}))
    with pytest.raises(SystemExit):
        plot.load(other)
    assert plot.main([str(tmp_path / "missing.json")]) == 1


def test_the_tornado_is_drawn_from_the_command_line(tmp_path):
    exe = next((shutil.which(n) for n in ("python3", "python")
                if shutil.which(n) and subprocess.run([shutil.which(n), "-c", "import matplotlib"], capture_output=True).returncode == 0), None)
    if exe is None:
        pytest.skip("no Python with matplotlib on this machine")
    saved = tmp_path / "latest_tornado.json"
    saved.write_text(json.dumps(_data()))
    out = tmp_path / "t.png"
    script = Path(__file__).resolve().parents[1] / "scripts" / "plot_tornado.py"
    done = subprocess.run([exe, str(script), str(saved), "--out", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and (tmp_path / "t.md").exists()
