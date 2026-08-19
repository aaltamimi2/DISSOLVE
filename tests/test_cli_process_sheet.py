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
