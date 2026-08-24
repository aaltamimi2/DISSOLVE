"""v3 §8 commit 1: /contaminant CLI skin, not a solvents bind.

Does not embed washes. Does not call tea._config_key. No BioSTEAM.
"""
from __future__ import annotations

import inspect
import io
import sys
from pathlib import Path

from rich.console import Console

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import contaminants, separation
from dissolve.cli import (
    CliApp,
    _format_contaminant_default,
    _parse_contaminant_slash,
)
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _app(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    app = CliApp(
        session_id="contaminant-cli",
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
        **kwargs,
    )
    return app, buf


def test_parse_and_format_skin():
    assert _parse_contaminant_slash([]) is None
    assert _parse_contaminant_slash(["off"]) == {"mode": "off"}
    assert _parse_contaminant_slash(["leaching"]) == {"mode": "leaching"}
    assert _parse_contaminant_slash(["strap"]) == {"mode": "strap"}
    assert _parse_contaminant_slash(["swing"]) == {"mode": "strap"}
    assert _parse_contaminant_slash(["compare"]) == {"compare": True}
    try:
        _parse_contaminant_slash(["bind"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "usage: /contaminant" in str(error)
    assert _format_contaminant_default(None, origin="built-in") == (
        "contaminant_mode=off  (built-in)"
    )
    assert _format_contaminant_default(
        {"mode": "strap"}, origin="session",
    ) == "contaminant_mode=strap  (session)"


def test_session_set_and_clear(tmp_path, monkeypatch):
    app, _buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant leaching") is False
    assert app.session.get("contaminant_mode") == {"mode": "leaching"}
    assert app.handle_command("/contaminant swing") is False
    assert app.session.get("contaminant_mode") == {"mode": "strap"}
    assert app.handle_command("/clear") is False
    assert "contaminant_mode" not in app.session
    assert "handle_command" not in inspect.getsource(app.ask)


def test_bare_non_tty_prints_status_and_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant") is False
    assert "contaminant_mode" not in app.session
    assert "contaminant_mode=off" in buf.getvalue()


def test_bare_picker_sets_strap(tmp_path, monkeypatch):
    from dissolve.cli import _contaminant_picker_options

    options, selected = _contaminant_picker_options("off")
    assert selected == 0
    assert [key for key, _label in options] == ["off", "leaching", "strap"]
    app, _buf = _app(tmp_path, monkeypatch)
    app._handle_contaminant_command([], picker_fn=lambda **_k: "strap")
    assert app.session.get("contaminant_mode") == {"mode": "strap"}
    app._handle_contaminant_command([], picker_fn=lambda **_k: None)
    assert app.session.get("contaminant_mode") == {"mode": "strap"}


def test_compare_without_prior_screen_prints_usage(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant compare") is False
    assert "contaminant_mode" not in app.session
    assert "prior contaminant screen" in buf.getvalue()


def test_compare_does_not_persist_a_mode(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.session["last_contaminant"] = {
        "target_polymer": "LDPE",
        "contaminants": ["di-(2-ethylhexyl) phthalate (DEHP)"],
        "other_polymers": ["EVOH"],
        "solvents": ["toluene"],
    }
    assert app.handle_command("/contaminant compare") is False
    assert "contaminant_mode" not in app.session
    assert "recommended_mode" in buf.getvalue()


def test_bad_token_does_not_write(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant solvents") is False
    assert "contaminant_mode" not in app.session
    assert "usage: /contaminant" in buf.getvalue()


def test_mode_key_does_not_change_planner_or_screens():
    def run(mode: str | None) -> tuple[str, str, str, str]:
        record = new_session()
        if mode is not None:
            record["contaminant_mode"] = {"mode": mode}
        with bind_tool_session(record):
            plan = separation.plan_multistage_separation(["LDPE", "PP"])
            leach = contaminants.screen_contaminant_leaching(
                "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
                solvents=["toluene"],
            )
            strap = contaminants.screen_contaminant_strap_removal(
                "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
                other_polymers=["EVOH"], solvents=["toluene"],
            )
            compare = contaminants.compare_contaminant_removal_modes(
                "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
                other_polymers=["EVOH"], solvents=["toluene"],
            )
        return plan, leach, strap, compare

    unset = run(None)
    assert unset == run("off") == run("leaching") == run("strap")
    assert _data(unset[1])["success"] is True
    assert "contaminants" in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
    assert "bind_query_solvent_scope" not in inspect.getsource(
        CliApp._handle_contaminant_command,
    )
    assert "bind_query_solvent_scope" not in inspect.getsource(
        CliApp._run_contaminant_compare,
    )


def test_logp_one_shot_parses_smiles_and_does_not_persist_a_mode(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.session["contaminant_mode"] = {"mode": "leaching"}
    assert app.handle_command(
        "/contaminant logp --smiles CCOC(=O)c1ccccc1C(=O)OCC"
    ) is False
    assert app.session.get("contaminant_mode") == {"mode": "leaching"}
    text = buf.getvalue()
    assert "FLKPEMZONWLCSK-UHFFFAOYSA-N" in text
    assert "dft=not_run" in text
    assert "n_rotatable_bonds=" in text
    assert "invalid_smiles" not in text


def test_logp_invalid_smiles_refuses_without_writing_mode(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant logp --smiles not_a_smiles") is False
    assert "contaminant_mode" not in app.session
    assert "invalid_smiles" in buf.getvalue()


def test_logp_is_not_a_persistent_mode_token():
    try:
        _parse_contaminant_slash(["logp"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "logp --smiles" in str(error)
    try:
        _parse_contaminant_slash(["banana"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "usage: /contaminant" in str(error)
        assert "logp --smiles" not in str(error)
