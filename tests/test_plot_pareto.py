"""scripts/plot_pareto.py: the data handling runs in the project's environment; drawing needs matplotlib, which only some
Pythons have, so that part runs in a subprocess with whichever Python has it and is skipped when none does."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "plot_pareto.py"
from dissolve import waste_pathway_plot as plot


def _cand(label, washes, key, profit, emissions, circ):
    return {"label": label, "washes": washes, "downstream": key, "downstream_key": key, "profit_usd_per_yr": profit,
            "emissions_t_co2e_per_yr": emissions, "circularity_index": circ}


DATA = {
    "schema": "dissolve.waste-pathway-figure-data.v1", "feed": {"tonnes_per_year": 8000.0, "mass_fractions": {"LDPE": 0.7, "EVOH": 0.3}},
    "plant": {"cost_basis": "plant dedicated to this feed"}, "basis": "tea_live", "downstream_sites": "published sites",
    "candidates": [
        _cand("no wash + lf", [], "lf", -100.0, 700.0, 0.60),
        _cand("EVOH/Ethylene glycol + we", [{"polymer": "EVOH", "solvent": "Ethylene glycol", "temperature_c": 120}], "we", 2.6e6, 18000.0, 0.49),
        _cand("LDPE/Toluene > EVOH/Propylene glycol + lf", [{"polymer": "LDPE", "solvent": "Toluene", "temperature_c": 100},
                                                            {"polymer": "EVOH", "solvent": "Propylene glycol", "temperature_c": 120}], "lf", 4.4e5, 2870.0, 0.71),
        _cand("no wash + we", [], "we", 7.7e5, 20000.0, 0.44),
    ],
    "fronts": {"emissions_vs_profit": ["no wash + lf", "EVOH/Ethylene glycol + we"],
               "circularity_vs_profit": ["EVOH/Ethylene glycol + we", "LDPE/Toluene > EVOH/Propylene glycol + lf"],
               "emissions_vs_circularity": ["no wash + lf", "LDPE/Toluene > EVOH/Propylene glycol + lf"]},
    "optima": {}, "selected": "EVOH/Ethylene glycol + we",
}


def test_short_labels_name_the_washes_with_short_solvents_and_the_destination():
    assert plot.short_label(DATA["candidates"][0]) == "no wash → landfill"
    assert plot.short_label(DATA["candidates"][2]) == "LDPE (toluene) › EVOH (PG) → landfill"


def test_a_front_is_read_back_in_the_saved_order():
    assert [p["label"] for p in plot.front_points(DATA, "circularity_vs_profit")][0] == "EVOH/Ethylene glycol + we"


def test_the_table_lists_every_front_member_with_its_number():
    text = plot.fronts_markdown(DATA)
    for heading in ("profit against emissions", "profit against circularity", "circularity against emissions"):
        assert f"## {heading}" in text
    assert "| 2 | EVOH (EG) → incineration | 2,600,000 | 18,000 | 0.490 |" in text and "4 candidate pathways" in text


def test_a_file_that_is_not_a_saved_request_is_refused(tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"schema": "something else"}))
    with pytest.raises(SystemExit):
        plot.load(other)


def test_with_no_saved_request_the_script_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DISSOLVE_PATHWAY_RESULTS_DIR", str(tmp_path / "nothing"))
    assert plot.main([]) == 1 and "ask optimize_waste_pathway" in capsys.readouterr().err


def _python_with_matplotlib():
    for name in ("python3", "python"):
        exe = shutil.which(name)
        if exe and subprocess.run([exe, "-c", "import matplotlib"], capture_output=True).returncode == 0:
            return exe
    return None


def test_the_figure_and_table_are_written(tmp_path):
    exe = _python_with_matplotlib()
    if exe is None:
        pytest.skip("no Python with matplotlib on this machine")
    saved = tmp_path / "latest.json"
    saved.write_text(json.dumps(DATA))
    out = tmp_path / "fronts.png"
    done = subprocess.run([exe, str(SCRIPT), str(saved), "--out", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert out.stat().st_size > 10_000 and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "fronts_fronts.md").read_text().startswith("# Pareto fronts: 8,000 t/yr")


def test_the_route_list_numbers_every_front_member_and_wraps_long_routes():
    lines = plot.route_list(DATA, "circularity_vs_profit")
    assert lines[0] == " 1  EVOH (EG) → incineration"
    assert lines[1].startswith(" 2  LDPE (toluene) › EVOH (PG) → landfill") and all(len(line) <= plot.LIST_WIDTH + 2 for line in lines)
    long = {**DATA["candidates"][2], "washes": DATA["candidates"][2]["washes"] * 3}
    wrapped = plot.route_list({**DATA, "candidates": [DATA["candidates"][0], long], "fronts": {"x": ["no wash + lf", long["label"]]}}, "x")
    assert len(wrapped) > 2 and wrapped[2].startswith("      ")  # continuation lines hang under the route, not the number


def test_solvent_names_are_shortened_whatever_their_capitalization():
    assert [plot.short_solvent(n) for n in ("Propylene Glycol", "propylene glycol", "Ethylene glycol", "Toluene")] == ["PG", "PG", "EG", "toluene"]
    assert plot.short_solvent("Tetrachloroethylene") == "tetrachloroethylene"  # a long single word is kept whole
    assert plot.short_solvent("Some very long solvent name") == "some"
    assert plot.short_solvent("Propylene Carbonate") == "propylene carbonate"  # not confused with propylene glycol


SUMMARY = {
    "solvent_pool": {"scope": "common", "costable_in_scope": 34, "costable_outside_scope": 25},
    "screens": [
        {"removed_before": [], "polymer": "LDPE", "pool": 34, "passed_solubility": 9, "passed": 7, "costed": 3},
        {"removed_before": [], "polymer": "PET", "pool": 34, "passed_solubility": 0, "passed": 0, "costed": 0},
        {"removed_before": ["LDPE"], "polymer": "EVOH", "pool": 34, "passed_solubility": 10, "passed": 8, "costed": 3},
    ],
    "polymers_without_wash": ["PET"],
    "simulations": {"washes_costed": 6, "live_simulations": 5, "simulated_in_this_call": 4, "stored_records": 1,
                    "reused_from_earlier_call": 1, "failed": 0, "simulation_seconds": 61.5},
    "routes": {"total": 5, "by_number_of_washes": {"0": 1, "1": 2, "2": 2}},
    "candidates": {"total": 28, "by_destination": {"lf": 5, "we": 5}},
    "fronts": {"emissions_vs_profit": 5, "circularity_vs_profit": 1, "emissions_vs_circularity": 3},
    "seconds": 123.4,
}


def test_the_summary_stages_follow_the_work_in_order_with_counts_from_the_request():
    rows = plot.summary_rows({**DATA, "work_summary": SUMMARY})
    assert [r[0] for r in rows] == ["Solvents in scope", "Clear the thermodynamic screen", "Costed (best 3 per polymer and stream)",
                                    "BioSTEAM process runs", "Wash routes", "Candidate pathways", "Non-dominated pathways"]
    assert [r[2] for r in rows] == ["34", "15", "6", "6", "5", "28", "5 \u00b7 1 \u00b7 3"]
    assert "25 lie outside the \u201ccommon\u201d scope" in rows[0][3]
    assert "5 live simulations (4 run in this call, 1 reused from an earlier call), 1 from stored records, 0 failed" in rows[3][3]
    assert "1 with 0 washes, 2 with 1 wash, 2 with 2 washes" in rows[4][3]


def test_the_screen_table_names_each_stream_and_flags_a_polymer_nothing_dissolves():
    lines = plot.screen_lines({**DATA, "work_summary": SUMMARY})
    assert lines[1].startswith("LDPE (whole stream)") and lines[3].startswith("EVOH (after LDPE removed)")
    assert lines[2].rstrip().endswith("none") and not lines[1].rstrip().endswith("none")


def test_the_effort_lines_state_what_ran_and_how_long():
    text = " | ".join(plot.effort_lines({**DATA, "work_summary": SUMMARY}))
    assert "5 live BioSTEAM simulations, 62 s of simulation in all (1 reused from an earlier call), 1 from stored records" in text
    assert "28 candidate pathways" in text and "emissions\u2013profit 5" in text and "this call took 123 s" in text


def test_a_request_saved_before_work_summaries_existed_is_refused_with_a_reason():
    with pytest.raises(SystemExit) as stop:
        plot.summary_rows(DATA)
    assert "no work summary" in str(stop.value)


def test_the_summary_figure_is_drawn_from_the_command_line(tmp_path):
    exe = _python_with_matplotlib()
    if exe is None:
        pytest.skip("no Python with matplotlib on this machine")
    saved = tmp_path / "latest.json"
    saved.write_text(json.dumps({**DATA, "work_summary": SUMMARY}))
    out = tmp_path / "work.png"
    done = subprocess.run([exe, str(SCRIPT), str(saved), "--summary", "--out", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and out.stat().st_size > 10_000
