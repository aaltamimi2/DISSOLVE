"""CLI-direct process sheet: the submitted dict is the tool dict."""
from __future__ import annotations

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
