"""CLI-direct process sheet: the submitted dict is the tool dict."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import cli, tea
from dissolve.cli import CliApp
from agent_harness import ToolEvent, TurnResult
from rich.console import Console
import io


def _console():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=True, width=80, color_system=None), buf


def _app(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    console, buf = _console()
    app = CliApp(
        session_id="test-session",
        store_root=tmp_path,
        console=console,
        **kwargs,
    )
    return app, buf


def _ok_turn(query, *, session, model, messages, on_event, api_base, api_key_env):
    if messages is not None:
        messages.append({"role": "user", "content": query})
        messages.append({"role": "assistant", "content": "done"})
    ev = ToolEvent("screen_polymer_separation", {"feed": "LDPE"}, {"ok": True})
    if on_event:
        on_event(ev)
    return TurnResult(
        answer="ok", status="ok", tool_trace=[ev],
        turn_record="turn-1", tool_rounds=1,
    )


def _accept_defaults(message, default=""):
    if str(message).startswith("edit"):
        return "run"
    return default


def test_first_run_precipitation_is_factory_35_not_scenario_25():
    defaults = tea.first_run_sheet_defaults()
    assert defaults["precipitation_temperature_c"] == pytest.approx(35.0)
    seeded = tea.seed_public_process_config({"target_polymer": "LDPE"})
    assert seeded["precipitation_temperature_c"] == pytest.approx(35.0)
    seeded_from_model = tea.seed_public_process_config({
        "precipitation_temperature_c": 25.0,
    })
    assert seeded_from_model["precipitation_temperature_c"] == pytest.approx(25.0)


def test_public_price_and_labor_aliases_reach_scenario_config():
    record = next(
        item for item in tea._records()
        if str(item["config"].get("energy_case")) == "C1"
    )
    cfg = record["config"]
    normalized = tea._scenario_config({
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
    })
    assert normalized["solvent_price"] == pytest.approx(float(cfg["solvent_price"]))
    assert normalized["labor_cost"] == pytest.approx(float(cfg["labor_cost"]))
    assert tea._cache_index().get(tea._config_key(normalized))["label"] == (
        record["label"]
    )


def test_ask_never_opens_the_process_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)

    def forbidden(*args, **kwargs):
        raise AssertionError("CliApp.ask must not prompt the process sheet")

    monkeypatch.setattr(cli.Prompt, "ask", forbidden)
    app, _buf = _app(tmp_path, monkeypatch)
    result = app.ask("screen LDPE")
    assert result.status == "ok"
    assert app._confirmation_sheet_submitted is False


def test_clear_drops_the_process_buffer_and_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _buf = _app(tmp_path, monkeypatch)
    app._process_buffer = tea.seed_public_process_config()
    app._confirmation_sheet_submitted = True
    app.handle_command("/clear")
    assert app._process_buffer is None
    assert app._confirmation_sheet_submitted is False


def test_cli_direct_dispatch_uses_the_same_sheet_object(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {"scenarios": [{"target_polymer": "LDPE", "solvent": "toluene"}]},
    )
    assert result == {"success": True}
    assert captured["name"] == "evaluate_tea_lca_scenarios"
    dispatched = captured["kwargs"]["scenarios"][0]
    assert dispatched is sheet
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_abort_does_not_set_the_bit_or_run_model_args(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: None)
    called = []

    def original(name, **kwargs):
        called.append(name)
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {"scenarios": [{"target_polymer": "LDPE"}]},
    )
    assert called == []
    assert result["error_code"] == "process_confirmation_aborted"
    assert app._confirmation_sheet_submitted is False


def test_non_tty_skips_the_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    app, _buf = _app(tmp_path, monkeypatch)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("non-TTY must not prompt")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    model_args = {"scenarios": [{"target_polymer": "LDPE"}]}
    app._cli_direct_dispatch(
        original, "evaluate_tea_lca_scenarios", model_args,
    )
    assert captured["kwargs"]["scenarios"] == model_args["scenarios"]
    assert app._confirmation_sheet_submitted is False


def test_first_old_evaluate_name_shortlist_seeds_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "engine_mode": "cache",
        },
    )
    assert result == {"success": True}
    assert seen[0]["target_polymer"] == "LDPE"
    assert seen[0]["solvent"] == "Dodecane"
    assert seen[0]["dissolution_temperature_c"] == 145.0
    assert seen[0]["energy_case"] == "C1"
    assert seen[0]["target_mass_percent"] == 55.0
    assert captured["name"] == "evaluate_tea_lca_scenarios"
    assert captured["kwargs"]["scenarios"][0] is sheet
    assert captured["kwargs"]["engine_mode"] == "cache"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_old_evaluate_name_scenarios_still_win_the_seed(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "HDPE",
        "solvent": "Toluene",
        "dissolution_temperature_c": 90.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "scenarios": [{"target_polymer": "HDPE", "solvent": "Toluene"}],
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    assert seen[0]["target_polymer"] == "HDPE"
    assert seen[0]["solvent"] == "Toluene"
    assert seen[0]["target_polymer"] != "LDPE"
    assert captured["kwargs"]["scenarios"][0] is sheet
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]


def test_first_old_sensitivity_name_shortlist_seeds_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "analyze_tea_sensitivity",
        {
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "parameter": "solvent_price",
            "engine_mode": "cache",
        },
    )
    assert result == {"success": True}
    assert seen[0]["target_polymer"] == "LDPE"
    assert seen[0]["solvent"] == "Dodecane"
    assert seen[0]["dissolution_temperature_c"] == 145.0
    assert seen[0]["energy_case"] == "C1"
    assert seen[0]["target_mass_percent"] == 55.0
    assert captured["name"] == "analyze_tea_sensitivity"
    assert captured["kwargs"]["scenario"] is sheet
    assert captured["kwargs"]["parameter"] == "solvent_price"
    assert captured["kwargs"]["engine_mode"] == "cache"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_old_sensitivity_name_scenario_still_wins_the_seed(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "HDPE",
        "solvent": "Toluene",
        "dissolution_temperature_c": 90.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "analyze_tea_sensitivity",
        {
            "scenario": {"target_polymer": "HDPE", "solvent": "Toluene"},
            "parameter": "solvent_price",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    assert seen[0]["target_polymer"] == "HDPE"
    assert seen[0]["solvent"] == "Toluene"
    assert seen[0]["target_polymer"] != "LDPE"
    assert captured["kwargs"]["scenario"] is sheet
    assert captured["kwargs"]["parameter"] == "solvent_price"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]


def test_ask_path_does_not_arm_cli_direct(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    assert app._cli_direct_active is False
    app.ask("hello")
    assert app._cli_direct_active is False


def test_sheet_accept_defaults_keeps_the_buffer_object(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 130.0,
    })
    submitted = app._edit_process_sheet(seed, prompt_fn=_accept_defaults)
    assert submitted is seed
    shown = buf.getvalue()
    assert "10%" in shown
    assert "expert_surface_only" in shown
    assert tea.missing_public_process_fields(submitted) == []


def test_later_evaluate_runs_the_confirmed_buffer_not_model_args(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.10,
    })
    prompts = []

    def record_prompt(seed, **kwargs):
        prompts.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = []

    def original(name, **kwargs):
        captured.append({"name": name, "kwargs": kwargs})
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {"scenarios": [{"target_polymer": "LDPE", "irr": 0.10}]},
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "engine_mode": "cache",
            "scenarios": [{"target_polymer": "LDPE", "irr": 0.15}],
        },
    )
    assert len(prompts) == 1
    assert len(captured) == 2
    second = captured[1]["kwargs"]
    assert second["scenarios"][0] is sheet
    assert second["scenarios"][0]["irr"] == pytest.approx(0.10)
    assert second["engine_mode"] == "cache"
    assert sheet["irr"] != 0.15
    app._cli_direct_dispatch(
        original,
        "analyze_tea_sensitivity",
        {"scenario": {"irr": 0.20}, "parameter": "solvent_price"},
    )
    assert len(prompts) == 1
    third = captured[2]["kwargs"]
    assert third["scenario"] is sheet
    assert third["parameter"] == "solvent_price"


def test_later_evaluate_drops_screening_shortlist_from_model_args(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = []

    def original(name, **kwargs):
        captured.append(kwargs)
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {"scenarios": [{"target_polymer": "LDPE"}]},
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "screening_shortlist": {
                "source": "explicit",
                "items": [{"target_polymer": "LDPE", "solvent": "toluene"}],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    assert "screening_shortlist" not in captured[1]
    assert "held_process_basis" not in captured[1]
    assert captured[1]["scenarios"][0] is sheet


def test_process_buffer_is_the_first_confirm_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    edited = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.12,
    })
    app._process_buffer = edited
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return seed

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {"scenarios": [{"target_polymer": "HDPE", "irr": 0.10}]},
    )
    assert seen[0] is edited
    assert captured["kwargs"]["scenarios"][0] is edited
    assert captured["kwargs"]["scenarios"][0]["irr"] == pytest.approx(0.12)


def test_evaluate_process_evaluate_mode_uses_the_same_sheet_object(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {"target_polymer": "LDPE", "solvent": "toluene"},
        },
    )
    assert result == {"success": True}
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["process_config"] is sheet
    assert "process_configs" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_first_evaluate_process_shortlist_pops_handoff_and_seeds_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "engine_mode": "cache",
        },
    )
    assert result == {"success": True}
    assert seen[0]["target_polymer"] == "LDPE"
    assert seen[0]["solvent"] == "Dodecane"
    assert seen[0]["dissolution_temperature_c"] == 145.0
    assert seen[0]["energy_case"] == "C1"
    assert seen[0]["target_mass_percent"] == 55.0
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["process_config"] is sheet
    assert captured["kwargs"]["engine_mode"] == "cache"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    assert "process_configs" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_non_tty_evaluate_process_keeps_the_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    app, _buf = _app(tmp_path, monkeypatch)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("non-TTY must not prompt")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    model_args = {
        "mode": "evaluate",
        "screening_shortlist": {
            "source": "explicit",
            "items": [{"target_polymer": "LDPE", "solvent": "Dodecane"}],
        },
        "held_process_basis": {"energy_case": "C1"},
        "engine_mode": "cache",
    }
    app._cli_direct_dispatch(original, "evaluate_process", model_args)
    assert captured["kwargs"] == model_args
    assert app._confirmation_sheet_submitted is False


def test_evaluate_process_lookup_mode_does_not_open_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("lookup mode must not prompt the process sheet")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    model_args = {
        "mode": "lookup",
        "lookup_filter": {"target_polymer": "LDPE"},
    }
    app._cli_direct_dispatch(original, "evaluate_process", model_args)
    assert captured["kwargs"] == model_args
    assert app._confirmation_sheet_submitted is False


def test_later_evaluate_process_runs_the_confirmed_buffer(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.10,
    })
    prompts = []

    def record_prompt(seed, **kwargs):
        prompts.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = []

    def original(name, **kwargs):
        captured.append({"name": name, "kwargs": kwargs})
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_configs": [{"target_polymer": "LDPE", "irr": 0.10}],
        },
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "engine_mode": "cache",
            "process_config": {"target_polymer": "LDPE", "irr": 0.15},
        },
    )
    assert len(prompts) == 1
    assert len(captured) == 2
    second = captured[1]["kwargs"]
    assert second["process_config"] is sheet
    assert second["process_config"]["irr"] == pytest.approx(0.10)
    assert second["engine_mode"] == "cache"
    assert "process_configs" not in second
    assert sheet["irr"] != 0.15


def test_evaluate_process_sensitivity_mode_uses_the_same_sheet_object(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "sensitivity",
            "process_config": {"target_polymer": "LDPE"},
            "parameter": "solvent_price",
        },
    )
    assert result == {"success": True}
    assert captured["kwargs"]["process_config"] is sheet
    assert captured["kwargs"]["parameter"] == "solvent_price"
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_later_sensitivity_keeps_parameter_and_uses_confirmed_buffer(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.10,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = []

    def original(name, **kwargs):
        captured.append(kwargs)
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "sensitivity",
            "parameter": "solvent_price",
            "engine_mode": "cache",
            "process_config": {"irr": 0.20},
        },
    )
    assert captured[1]["process_config"] is sheet
    assert captured[1]["parameter"] == "solvent_price"
    assert captured[1]["engine_mode"] == "cache"
    assert sheet["irr"] != 0.20


def test_evaluate_process_route_mode_opens_the_sheet_without_process_config(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "processing_capacity_mt_per_yr": 20_000.0,
        "energy_case": "C1",
        "precipitation_temperature_c": 25.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "route", "handle": "plan-1", "engine_mode": "cache"},
    )
    assert result == {"success": True}
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["handle"] == "plan-1"
    assert captured["kwargs"]["engine_mode"] == "cache"
    assert "process_config" not in captured["kwargs"]
    assert "process_configs" not in captured["kwargs"]
    assert captured["kwargs"]["processing_capacity_mt_per_yr"] == 20_000.0
    assert captured["kwargs"]["energy_case"] == "C1"
    assert captured["kwargs"]["precipitation_temperature_c"] == 25.0
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_later_route_keeps_handle_and_does_not_inject_process_config(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "processing_capacity_mt_per_yr": 20_000.0,
        "energy_case": "C1",
        "precipitation_temperature_c": 25.0,
        "irr": 0.10,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = []

    def original(name, **kwargs):
        captured.append(kwargs)
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "route", "handle": "plan-1", "engine_mode": "cache"},
    )
    assert captured[0]["process_config"] is sheet
    assert captured[1]["handle"] == "plan-1"
    assert "process_config" not in captured[1]
    assert captured[1]["engine_mode"] == "cache"
    assert captured[1]["processing_capacity_mt_per_yr"] == 20_000.0


def test_confirmation_sheet_origin_helper_from_screen_and_default():
    held = {"energy_case": "C1", "target_mass_percent": 55.0}
    submitted = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        **held,
    })
    origin = tea.confirmation_sheet_field_origin(
        submitted,
        snapshot=submitted,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "temperature_c": 145.0,
        },
        held_keys=list(held),
    )
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    assert origin["energy_case"] == "supplied"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["precipitation_temperature_c"] == "default"
    assert origin["processing_capacity_mt_per_yr"] == "default"
    assert "from_screen" not in {
        origin[name] for name in tea._NINE_HELD_PUBLIC_FIELDS
        if name in origin
    }


def test_confirmation_sheet_origin_helper_edit_is_supplied():
    seed = tea.seed_public_process_config({"energy_case": "C1"})
    submitted = dict(seed)
    submitted["target_mass_percent"] = 55.0
    origin = tea.confirmation_sheet_field_origin(
        submitted, snapshot=seed,
    )
    assert origin["target_mass_percent"] == "supplied"
    assert origin["processing_capacity_mt_per_yr"] == "default"


def test_flatten_shortlist_mints_from_screen_on_the_envelope(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["scenarios"][0])],
        }

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "engine_mode": "cache",
        },
    )
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    assert origin["energy_case"] == "supplied"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["precipitation_temperature_c"] == "default"
    assert result["field_origin"]["solvent"] == "from_screen"


def test_flatten_scenarios_do_not_mint_from_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "HDPE",
        "solvent": "Toluene",
        "dissolution_temperature_c": 90.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["scenarios"][0])],
        }

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "scenarios": [{"target_polymer": "HDPE", "solvent": "Toluene"}],
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["target_polymer"] != "from_screen"
    assert origin["precipitation_temperature_c"] == "default"


def test_non_tty_flatten_does_not_mint_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    app, _buf = _app(tmp_path, monkeypatch)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [{"target_polymer": "LDPE"}],
        }

    def forbidden(*args, **kwargs):
        raise AssertionError("non-TTY must not prompt")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original, "evaluate_tea_lca_scenarios",
        {"scenarios": [{"target_polymer": "LDPE"}]},
    )
    assert "field_origin" not in result
    assert "field_origin" not in result["comparison_rows"][0]


def test_flatten_evaluate_process_mints_from_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["process_config"])],
        }

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
        },
    )
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["precipitation_temperature_c"] == "default"


def test_process_edit_refreshes_sheet_field_origin():
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    previous = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    submitted = dict(seed)
    submitted["precipitation_temperature_c"] = 25.0
    origin = tea.confirmation_sheet_field_origin_after_edit(
        submitted, snapshot=seed, previous=previous,
    )
    assert origin["precipitation_temperature_c"] == "supplied"
    assert origin["precipitation_temperature_c"] != "default"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["processing_capacity_mt_per_yr"] == "default"
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["energy_case"] == "supplied"


def test_process_command_edit_is_supplied_on_later_stamp(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        if kwargs.get("scenarios"):
            row = dict(kwargs["scenarios"][0])
        else:
            row = dict(kwargs.get("process_config") or {})
        return {"success": True, "comparison_rows": [row]}

    app._cli_direct_active = True
    first = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
        },
    )
    assert first["comparison_rows"][0]["field_origin"]["target_mass_percent"] == (
        "supplied"
    )
    assert first["comparison_rows"][0]["field_origin"][
        "precipitation_temperature_c"
    ] == "default"

    def edit_precip(seed, **kwargs):
        seed["precipitation_temperature_c"] = 25.0
        return seed

    monkeypatch.setattr(app, "_edit_process_sheet", edit_precip)
    app.handle_command("/process")
    later = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {"scenarios": [{"target_polymer": "LDPE"}]},
    )
    origin = later["comparison_rows"][0]["field_origin"]
    assert origin["precipitation_temperature_c"] == "supplied"
    assert origin["precipitation_temperature_c"] != "default"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["target_polymer"] == "from_screen"
    assert later["comparison_rows"][0]["precipitation_temperature_c"] == (
        pytest.approx(25.0)
    )


def test_process_abort_does_not_refresh_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["scenarios"][0])],
        }

    app._cli_direct_active = True
    first = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {"scenarios": [{
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        }]},
    )
    before = dict(app._sheet_field_origin)
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: None)
    app.handle_command("/process")
    assert app._sheet_field_origin == before
    assert first["comparison_rows"][0]["field_origin"][
        "precipitation_temperature_c"
    ] == "default"


def test_confirmation_sheet_row_origin_recorded_map_wins():
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    origin = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    assert tea.confirmation_sheet_row_origin(
        "target_polymer", seed, origin=origin,
    ) == "from_screen"
    assert tea.confirmation_sheet_row_origin(
        "precipitation_temperature_c", seed, origin=origin,
    ) == "default"
    assert tea.confirmation_sheet_row_origin(
        "target_mass_percent", seed, origin=origin,
    ) == "supplied"
    assert tea.confirmation_sheet_row_origin("target_polymer", seed) == (
        "supplied"
    )
    assert tea.confirmation_sheet_row_origin("target_polymer", seed) != (
        "from_screen"
    )
    assert tea.confirmation_sheet_row_origin(
        "precipitation_temperature_c", seed,
    ) == "default"
    empty = dict(seed)
    empty.pop("target_polymer")
    assert tea.confirmation_sheet_row_origin("target_polymer", empty) == (
        "missing"
    )
    assert tea.confirmation_sheet_row_origin(
        "facilities", seed, origin=origin,
    ) == "missing"
    assert tea.confirmation_sheet_row_origin(
        "solvent", seed, origin={"solvent": "fail"},
    ) == "missing"
    assert tea.confirmation_sheet_row_origin(
        "solvent", seed, origin={"solvent": "inherited"},
    ) == "inherited"


def test_sheet_print_shows_origin_column(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    origin = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    app._print_process_sheet(seed, origin=origin)
    shown = buf.getvalue()
    assert "origin" in shown
    assert "from_screen" in shown
    assert "default" in shown
    assert "supplied" in shown
    assert "inherited" not in shown


def test_sheet_print_without_map_does_not_mint_from_screen(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    app._print_process_sheet(seed)
    shown = buf.getvalue()
    assert "from_screen" not in shown
    assert "inherited" not in shown
    assert "default" in shown
    assert "supplied" in shown


def test_first_run_sheet_print_shows_from_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", _accept_defaults)
    app, buf = _app(tmp_path, monkeypatch)

    def original(name, **kwargs):
        if kwargs.get("scenarios"):
            row = dict(kwargs["scenarios"][0])
        else:
            row = dict(kwargs.get("process_config") or {})
        return {"success": True, "comparison_rows": [row]}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_tea_lca_scenarios",
        {
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
        },
    )
    shown = buf.getvalue()
    assert "from_screen" in shown
    assert "default" in shown
    assert "supplied" in shown
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["precipitation_temperature_c"] == "default"
    assert origin["target_mass_percent"] == "supplied"


def test_confirmation_sheet_row_units_bind_per_field():
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    assert tea.confirmation_sheet_row_units(
        "precipitation_temperature_c", seed,
    ) == "°C"
    assert tea.confirmation_sheet_row_units(
        "dissolution_temperature_c", seed,
    ) == "°C"
    assert tea.confirmation_sheet_row_units("target_polymer", seed) == ""
    assert tea.confirmation_sheet_row_units("target_polymer", seed) != "°C"
    assert tea.confirmation_sheet_row_units("irr", seed) == "fraction"
    assert tea.confirmation_sheet_row_units("irr", seed) != "%"
    assert tea.confirmation_sheet_row_units("irr", seed) != "°C"
    assert tea.confirmation_sheet_row_units(
        "processing_capacity_mt_per_yr", seed,
    ) == "MT/yr"
    assert tea.confirmation_sheet_row_units(
        "target_mass_percent", seed,
    ) == "wt%"
    assert tea.confirmation_sheet_row_units(
        "solvent_price_usd_per_kg", seed,
    ) == "USD/kg"
    assert tea.confirmation_sheet_row_units(
        "labor_cost_usd_per_employee_yr", seed,
    ) == "USD/employee/yr"
    assert tea.confirmation_sheet_row_units("finance_years", seed) == "years"
    assert tea.confirmation_sheet_row_units("startup_months", seed) == (
        "months"
    )
    assert tea.confirmation_sheet_row_units("finance_years", seed) != (
        tea.confirmation_sheet_row_units("startup_months", seed)
    )
    assert tea.confirmation_sheet_row_units(
        "natural_gas_price_usd_per_m3", seed,
    ) == "USD/m3"
    c2 = dict(seed)
    c2["energy_case"] = "C2"
    assert tea.confirmation_sheet_row_units(
        "natural_gas_price_usd_per_m3", c2,
    ) == ""
    assert tea.confirmation_sheet_row_units("facilities", seed) == ""
    assert tea.confirmation_sheet_row_units("sell_leftover_plastic", seed) == (
        ""
    )


def test_sheet_print_shows_units_column(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    origin = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    app._print_process_sheet(seed, origin=origin)
    shown = buf.getvalue()
    assert "units" in shown
    assert "°C" in shown
    assert "wt%" in shown
    assert "MT/yr" in shown
    assert "fraction" in shown
    assert "from_screen" in shown
    assert "origin" in shown


def test_c2_not_on_this_instance_lists_ng_and_steam_power():
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    c3 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C3",
    })
    c2 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C2",
    })
    assert tea.confirmation_sheet_not_on_this_instance(c1) == ()
    assert tea.confirmation_sheet_not_on_this_instance(c3) == ()
    gated = tea.confirmation_sheet_not_on_this_instance(c2)
    assert gated == (
        "natural_gas_price_usd_per_m3",
        "steam_power_depreciation",
    )
    assert "facilities" not in gated
    assert "natural_gas_price_usd_per_m3" not in tea.public_process_field_names(
        energy_case="C2",
    )
    assert "natural_gas_price_usd_per_m3" not in tea.missing_public_process_fields(
        c2,
    )
    assert tea.confirmation_sheet_not_on_this_instance_label(c2) == (
        "not on this instance (energy_case=C2)"
    )
    assert "C1" not in tea.confirmation_sheet_not_on_this_instance_label(c2)


def test_c2_sheet_print_states_not_on_this_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    c2 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C2",
    })
    app._print_process_sheet(c2)
    shown = buf.getvalue()
    assert "not on this" in shown
    assert "energy_case=C2" in shown
    assert "USD/m3" not in shown
    assert "energy_case=C1" not in shown
    assert re.search(r"^natural_gas_price_usd_per_m3\b", shown, re.M)
    assert re.search(r"^steam_power_depreciation\b", shown, re.M)
    ng = next(
        line for line in shown.splitlines()
        if line.startswith("natural_gas_price_usd_per_m3")
    )
    steam = next(
        line for line in shown.splitlines()
        if line.startswith("steam_power_depreciation")
    )
    phrase = tea.confirmation_sheet_not_on_this_instance_label(c2)
    assert phrase in ng
    assert ng != ng.rstrip()
    assert steam != steam.rstrip()
    assert len(ng) > ng.find(phrase) + len(phrase)
    fac = next(
        line for line in shown.splitlines()
        if line.startswith("facilities")
    )
    assert len(ng) == len(fac)
    assert len(steam) == len(fac)


def test_c1_sheet_print_does_not_state_not_on_this_instance(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    app._print_process_sheet(c1)
    shown = buf.getvalue()
    assert "not on this instance" not in shown
    assert "USD/m3" in shown


def test_derived_energy_case_rows_bind_c1_c2_c3():
    c1 = tea.seed_public_process_config({"energy_case": "C1"})
    c2 = tea.seed_public_process_config({"energy_case": "C2"})
    c3 = tea.seed_public_process_config({"energy_case": "C3"})
    assert tea.confirmation_sheet_derived_energy_case_rows(c1) == (
        ("facilities", True),
        ("turbogenerator", True),
    )
    assert tea.confirmation_sheet_derived_energy_case_rows(c2) == (
        ("facilities", False),
        ("turbogenerator", False),
    )
    assert tea.confirmation_sheet_derived_energy_case_rows(c3) == (
        ("facilities", True),
        ("turbogenerator", False),
    )
    assert tea.confirmation_sheet_derived_energy_case_rows(c1) != (
        tea.confirmation_sheet_derived_energy_case_rows(c2)
    )
    assert tea.confirmation_sheet_derived_energy_case_label() == (
        "derived from energy_case"
    )
    names = tea.public_process_field_names(energy_case="C1")
    assert "facilities" not in names
    assert "turbogenerator" not in names
    gated = tea.confirmation_sheet_not_on_this_instance(c2)
    assert "facilities" not in gated
    assert "turbogenerator" not in gated


def test_c1_sheet_print_shows_derived_facilities_true(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    app._print_process_sheet(c1)
    shown = buf.getvalue()
    assert re.search(r"^facilities\s+true\b", shown, re.M)
    assert re.search(r"^turbogenerator\s+true\b", shown, re.M)
    assert re.search(r"^facilities\s+false\b", shown, re.M) is None


def test_c2_sheet_print_shows_derived_facilities_false(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    c2 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C2",
    })
    app._print_process_sheet(c2)
    shown = buf.getvalue()
    assert re.search(r"^facilities\s+false\b", shown, re.M)
    assert re.search(r"^turbogenerator\s+false\b", shown, re.M)
    assert re.search(r"^facilities\s+true\b", shown, re.M) is None
    assert "not on this" in shown
    assert "energy_case=C2" in shown


def test_sheet_print_keeps_long_public_names_contiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    app._print_process_sheet(c1)
    shown = buf.getvalue()
    for name in (
        "precipitation_temperature_format",
        "labor_cost_usd_per_employee_yr",
        "natural_gas_price_usd_per_m3",
        "processing_capacity_mt_per_yr",
        "centrifuged_plastic_solvent_content_pct",
    ):
        assert re.search(rf"^{name}\b", shown, re.M)
        assert f"{name}…" not in shown
    for name in tea.public_process_field_names(energy_case="C1"):
        assert re.search(rf"^{re.escape(name)}\b", shown, re.M)
    assert "…" not in shown
    assert re.search(r"^facilities\s+true\b", shown, re.M)


def test_confirmation_sheet_format_row_aligns_columns():
    rows = (
        ("target_polymer", "LDPE", "", "supplied"),
        ("solvent", "Dodecane", "", "supplied"),
        ("precipitation_temperature_c", "35", "°C", "default"),
        ("dissolution_temperature_c", "145", "°C", "supplied"),
        ("facilities", "true", "", "derived from energy_case"),
        (
            "natural_gas_price_usd_per_m3",
            "not on this instance (energy_case=C2)",
            "",
            "",
        ),
    )
    widths = tea.confirmation_sheet_column_widths(rows)
    lines = [
        tea.confirmation_sheet_format_row(*row, widths) for row in rows
    ]
    assert lines[0].find("LDPE") == lines[1].find("Dodecane")
    assert lines[0].find("LDPE") == lines[4].find("true")
    assert lines[2].find("°C") == lines[3].find("°C")
    assert lines[0].find("supplied") == lines[4].find(
        "derived from energy_case"
    )
    assert lines[4].find("derived from energy_case") != lines[2].find("°C")
    assert "not on this instance (energy_case=C2)" in lines[5]
    assert len(widths) == 4
    field_width, value_width, units_width, origin_width = widths
    four_slot = (
        field_width + 2 + value_width + 2 + units_width + 2 + origin_width
    )
    assert len(lines[0]) == four_slot
    assert len(lines[4]) == four_slot
    assert len(lines[5]) == four_slot
    assert lines[5] != lines[5].rstrip()
    assert "derived from energy_case" not in tea._SHEET_ORIGIN_TOKENS
    assert "…" not in "".join(lines)


def test_sheet_print_aligns_value_units_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    origin = tea.confirmation_sheet_field_origin(
        c1,
        snapshot=c1,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case"],
    )
    app._print_process_sheet(c1, origin=origin)
    shown = buf.getvalue()
    lines = shown.splitlines()
    poly = next(line for line in lines if line.startswith("target_polymer"))
    solv = next(line for line in lines if line.startswith("solvent"))
    precip = next(
        line for line in lines
        if line.startswith("precipitation_temperature_c ")
    )
    diss = next(
        line for line in lines
        if line.startswith("dissolution_temperature_c ")
    )
    fac = next(line for line in lines if line.startswith("facilities"))
    assert poly.find("LDPE") == solv.find("Dodecane")
    assert precip.find("°C") == diss.find("°C")
    assert fac.find("true") == poly.find("LDPE")
    assert fac.find("derived from energy_case") == poly.find("from_screen")
    assert fac.find("derived from energy_case") != precip.find("°C")
    assert len(poly) == len(fac)
    assert len(poly) == len(solv)
    assert poly != poly.rstrip()
    assert "…" not in shown
    assert re.search(r"^labor_cost_usd_per_employee_yr\b", shown, re.M)



