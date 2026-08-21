"""`/solvents` toggle — first wiring slice against SOLVENTS_TOGGLE_SPEC.rev2.

No live BioSTEAM. No official-gate recapture. Built-in remains all.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve.cli import CliApp, _parse_solvents_slash
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session
from dissolve.tools import solubility_query
from dissolve import thermodynamics as thermo


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def test_freeze_set_is_closed():
    roster = thermo._available_solvents()
    assert len(thermo.COMMON_INTERP_KEYS) == 69
    assert thermo.COMMON_INTERP_KEYS <= roster
    assert "h2o" not in thermo.COMMON_INTERP_KEYS
    assert "h2o" in roster
    assert "triethylamine" in thermo.COMMON_INTERP_KEYS
    assert len(thermo.get_available_solvents()) == 990
    assert thermo.get_solvent_catalog_provenance()["fitted_solvent_count"] == 990


def test_common_universe_is_intersection_not_a_second_policy():
    with thermo.bind_query_solvent_scope("common"):
        active = thermo.get_available_solvents()
        assert active == set(thermo._available_solvents()) & thermo.COMMON_INTERP_KEYS
        assert len(active) == 69
        assert "h2o" not in active
        assert "toluene" in active
        assert thermo.get_solvent_catalog_provenance()["fitted_solvent_count"] == 990
        stamp = thermo.solvent_scope_stamp()
        assert stamp == {
            "solvent_scope": "common",
            "solvent_scope_origin": "query",
            "solvent_scope_n": 69,
        }
        polymer_set = thermo.get_available_solvents_for_polymer("LDPE")
        assert polymer_set <= active


def test_named_out_of_scope_refuses_not_unknown():
    with bind_tool_session(new_session()):
        with thermo.bind_query_solvent_scope("common"):
            payload = _data(solubility_query(
                polymers=["LDPE"], solvents=["n-methyl-2-pyrrolidinone"],
                temperatures=[80.0],
            ))
    assert payload["success"] is False
    assert payload["error_code"] == "solvent_not_in_scope"
    assert payload["solvent_scope"] == "common"
    assert payload["solvent_scope_origin"] == "query"
    water = _data(solubility_query(
        polymers=["LDPE"], solvents=["h2o"], temperatures=[80.0],
        solvent_scope="common",
    ))
    assert water["success"] is False
    assert water["error_code"] == "solvent_not_in_scope"
    unknown = _data(solubility_query(
        polymers=["LDPE"], solvents=["not-a-real-solvent-xx"], temperatures=[80.0],
        solvent_scope="common",
    ))
    assert unknown["success"] is False
    assert unknown["error_code"] == "unknown_solvents"


def test_surviving_solvent_number_identity():
    all_payload = _data(solubility_query(
        polymers=["LDPE"], solvents=["toluene"], temperatures=[80.0],
        solvent_scope="all",
    ))
    common_payload = _data(solubility_query(
        polymers=["LDPE"], solvents=["toluene"], temperatures=[80.0],
        solvent_scope="common",
    ))
    assert all_payload["success"] is True
    assert common_payload["success"] is True
    assert all_payload["results"][0]["solubility_pct"] == (
        common_payload["results"][0]["solubility_pct"]
    )
    assert common_payload["solvent_scope"] == "common"
    assert common_payload["solvent_scope_n"] == 69
    assert all_payload["solvent_scope"] == "all"
    assert all_payload["solvent_scope_n"] == 990


def test_omit_solvents_common_shrinks_axis_not_fitted_count():
    payload = _data(solubility_query(
        polymers=["LDPE"], temperatures=[80.0], solvent_scope="common",
    ))
    assert payload["success"] is True
    assert payload["selection"]["solvent_count"] == 69
    assert payload["solvent_scope_n"] == 69
    provenance = payload.get("solvent_catalog_provenance") or {}
    # query payload may not include provenance; fitted stays 990 on the API
    assert thermo.get_solvent_catalog_provenance()["fitted_solvent_count"] == 990


def test_under_coverage_denominator_is_scope_n():
    from dissolve.tools import screen_polymer_separation

    payload = _data(screen_polymer_separation(
        feed_polymers=["LDPE", "PP"],
        temperature_min_c=80.0,
        temperature_max_c=80.0,
        solvent_scope="common",
    ))
    assert payload["success"] is True
    assert payload["solvent_scope"] == "common"
    assert payload["solvent_scope_n"] == 69
    provenance = payload["solvent_catalog_provenance"]
    assert provenance["fitted_solvent_count"] == 990
    under = provenance.get("under_covered_polymers") or {}
    for count in under.values():
        assert count < 0.90 * 69


def test_session_default_and_clear(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    app = CliApp(
        session_id="solvents-session",
        store_root=tmp_path,
        console=Console(file=io.StringIO()),
    )
    assert _parse_solvents_slash([]) is None
    assert _parse_solvents_slash(["common"]) == {"scope": "common"}
    try:
        _parse_solvents_slash(["nope"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert app.handle_command("/solvents common") is False
    assert app.session.get("solvent_scope") == {"scope": "common"}
    session = dict(app.session)
    with bind_tool_session(session):
        payload = _data(solubility_query(polymers=["LDPE"], temperatures=[80.0]))
    assert payload["solvent_scope"] == "common"
    assert payload["solvent_scope_origin"] == "session_default"
    assert payload["selection"]["solvent_count"] == 69
    assert app.handle_command("/clear") is False
    assert "solvent_scope" not in app.session
    assert "handle_command" not in inspect.getsource(app.ask)


def test_bare_solvents_non_tty_prints_status(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-status",
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
    )
    assert app.handle_command("/solvents") is False
    assert "solvent_scope" not in app.session
    assert "solvent_scope=all" in buf.getvalue()


def test_bare_solvents_dumb_term_prints_status(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def boom(*_a, **_k):
        raise AssertionError("TUI must not open on a dumb terminal")

    monkeypatch.setattr("dissolve.cli._run_arrow_picker", boom)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-dumb",
        store_root=tmp_path,
        console=Console(file=buf),
    )
    assert app.handle_command("/solvents") is False
    assert "solvent_scope" not in app.session
    assert "solvent_scope=all" in buf.getvalue()


def test_bare_solvents_stdout_pipe_prints_status(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-stdout",
        store_root=tmp_path,
        console=Console(file=buf),
    )
    assert app.handle_command("/solvents") is False
    assert "solvent_scope" not in app.session


def test_bare_solvents_picker_sets_common(tmp_path, monkeypatch):
    import io
    from rich.console import Console
    from dissolve.cli import _solvents_picker_options

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    options, selected = _solvents_picker_options("all")
    assert selected == 1
    labels = " ".join(label for _value, label in options)
    assert "69" in labels
    assert "990" in labels
    assert "786" not in labels
    assert "(current)" in options[1][1]

    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-pick",
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
    )
    app._handle_solvents_command(
        [], picker_fn=lambda **_k: "common",
    )
    assert app.session.get("solvent_scope") == {"scope": "common"}
    app._handle_solvents_command([], picker_fn=lambda **_k: None)
    assert app.session.get("solvent_scope") == {"scope": "common"}


def test_bare_solvents_picker_rejects_unknown(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    app = CliApp(
        session_id="solvents-bad",
        store_root=tmp_path,
        console=Console(file=io.StringIO()),
    )
    app._handle_solvents_command([], picker_fn=lambda **_k: "nope")
    assert "solvent_scope" not in app.session


def test_bare_solvents_quiet_never_prompts(tmp_path, monkeypatch):
    import io
    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-quiet",
        store_root=tmp_path,
        console=Console(file=buf),
        quiet=True,
    )
    app._handle_solvents_command([], picker_fn=None)
    assert "solvent_scope" not in app.session


def test_query_overrides_session():
    session = new_session()
    session["solvent_scope"] = {"scope": "common"}
    with bind_tool_session(session):
        payload = _data(solubility_query(
            polymers=["LDPE"], temperatures=[80.0], solvent_scope="all",
        ))
    assert payload["solvent_scope"] == "all"
    assert payload["solvent_scope_origin"] == "query"
    assert payload["selection"]["solvent_count"] == 990


def test_invalid_scope_token():
    payload = _data(solubility_query(
        polymers=["LDPE"], temperatures=[80.0], solvent_scope="nope",
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_solvent_scope"


def test_resolve_solvent_stays_on_the_990():
    with thermo.bind_query_solvent_scope("common"):
        assert thermo.resolve_solvent("n-methyl-2-pyrrolidinone") == (
            "n-methyl-2-pyrrolidinone"
        )
        assert thermo.resolve_solvent("h2o") == "h2o"
        assert "n-methyl-2-pyrrolidinone" not in thermo.get_available_solvents()
