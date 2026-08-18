"""CLI surface: persistence, slash commands, doctor, and the run_turn seam."""
from __future__ import annotations

import inspect
import io
import json
import sys
from pathlib import Path

import pytest
from rich.console import Console

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_harness
from agent_harness import ToolEvent, TurnResult
from dissolve import cli
from dissolve.cli import CliApp, doctor_report, main, resolve_model
from dissolve.session import new_session


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
    session.setdefault("handles", {})
    session["handles"]["calm-blue-cat"] = {
        "tool": "screen_polymer_separation",
        "source_basis": "cosmo_rs_grid",
        "exact": {"ranked_candidates": [{"solvent": "dodecane"} for _ in range(40)]},
    }
    session.setdefault("polymers_in_play", []).append("LDPE")
    session.setdefault("temperatures_in_play", []).append(140.0)
    ev = ToolEvent("screen_polymer_separation", {"feed": "LDPE"}, {"handle": "calm-blue-cat", "total": 40})
    if on_event:
        on_event(ev)
    return TurnResult(answer="screened forty solvents", status="ok", tool_trace=[ev], turn_record="turn-1")


def test_resolve_model_aliases_and_default():
    alias, spec = resolve_model("muse")
    assert alias == "muse-spark"
    assert spec.model == "openai:muse-spark-1.2"
    assert spec.env_var == "META_MUSE_API_KEY"
    assert spec.base_url == "https://api.meta.ai/v1"
    with pytest.raises(ValueError, match="Unknown model alias"):
        resolve_model("not-a-model")


def test_cli_source_cuts_langchain_ingest_and_registry_call():
    src = inspect.getsource(cli)
    assert "langchain" not in src
    assert "ingest-graph" not in src
    assert "compare-rag" not in src
    assert "registry.call" not in src
    assert "AgentHarness" not in src
    assert "SessionState" not in src
    assert "candidate_evidence" not in src


def test_main_rejects_ingest_verbs():
    with pytest.raises(SystemExit):
        main(["ingest-graph", "--paths", "x"])
    with pytest.raises(SystemExit):
        main(["compare-rag", "--paths", "x"])


def test_doctor_checks_key_assets_registry_duckdb(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    names = [c["name"] for c in report["checks"]]
    assert names[:2] == ["Model provider", "Scientific assets"]
    assert "Tool registry" in names
    assert "duckdb" in names
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["Tool registry"]["status"] == "pass"
    assert by_name["Tool registry"]["registered"] == 34
    assert by_name["Scientific assets"]["status"] == "pass"
    assert by_name["Model provider"]["status"] == "fail"
    assert report["ready"] is False
    assert "specialist" not in json.dumps(report).lower()


def test_persist_roundtrip_messages_and_handle(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    path = tmp_path / "sessions" / "test-session" / "session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["release"] == "dissolve-v12-0.1"
    assert payload["metadata"]["mode"] == "review"
    assert payload["metadata"]["model"] == "muse-spark"
    assert any(m.get("role") == "user" and m.get("content") == "screen LDPE" for m in payload["messages"])
    stored = payload["session"]["handles"]["calm-blue-cat"]
    assert stored["tool"] == "screen_polymer_separation"
    assert len(stored["exact"]["ranked_candidates"]) == 40
    trans = (tmp_path / "sessions" / "test-session" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    roles = [json.loads(line)["role"] for line in trans]
    assert "user" in roles and "assistant" in roles and "tool" in roles
    app2, _ = _app(tmp_path, monkeypatch)
    assert "calm-blue-cat" in app2.session["handles"]
    assert len(app2.session["handles"]["calm-blue-cat"]["exact"]["ranked_candidates"]) == 40
    assert any(m.get("content") == "screen LDPE" for m in app2.messages)


def test_clear_drops_messages_and_handles_keeps_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    assert app.session["handles"]
    assert app.handle_command("/clear") is False
    assert app.store.session_id == "test-session"
    assert app.session["handles"] == {}
    assert [m["role"] for m in app.messages] == ["system"]
    payload = json.loads((tmp_path / "sessions" / "test-session" / "session.json").read_text())
    assert payload["session"]["handles"] == {}
    assert payload["session_id"] == "test-session"


def test_mode_rewrites_system_prompt(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert "Ask before making consequential process assumptions" in app.messages[0]["content"]
    app.handle_command("/mode auto")
    assert app.mode == "auto"
    assert "Make reasonable process assumptions when needed" in app.messages[0]["content"]
    assert "Ask before making consequential" not in app.messages[0]["content"]
    assert "auto" in buf.getvalue()


def test_context_lists_polymers_temps_and_handle_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    app.handle_command("/context")
    shown = buf.getvalue()
    assert "LDPE" in shown
    assert "140" in shown
    assert "calm-blue-cat" in shown
    assert "40" in shown


def test_harness_and_cost_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    app.handle_command("/harness")
    assert "flat loop, no specialists" in buf.getvalue()
    app.ask("q")
    app.handle_command("/cost")
    out = buf.getvalue()
    assert "1 tool call" in out
    assert "status ok" in out


def test_compaction_error_uses_notice_panel(tmp_path, monkeypatch):
    def fake(query, **kwargs):
        kwargs["messages"].append({"role": "user", "content": query})
        return TurnResult(
            answer="compaction cannot meet window-reserve target=0",
            status="compaction_error", tool_trace=[], turn_record="turn-1",
        )

    monkeypatch.setattr(cli, "run_turn", fake)
    app, buf = _app(tmp_path, monkeypatch)
    result = app.ask("q")
    assert result.status == "compaction_error"
    assert "Session budget stopped" in buf.getvalue()
    assert "compaction cannot meet" in buf.getvalue()


def test_startup_requires_named_key(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="META_MUSE_API_KEY"):
        CliApp(session_id="key-check", store_root=tmp_path, persist=False, require_key=True)


def test_harness_without_query_launches_cli(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent_harness.py"])
    monkeypatch.setattr(cli, "main", lambda argv=None: 17)
    with pytest.raises(SystemExit) as exc:
        agent_harness._main()
    assert exc.value.code == 17


def test_cli_does_not_call_registry(monkeypatch, tmp_path):
    import dissolve.registry as registry

    def boom(*a, **k):
        raise AssertionError("CLI must not call registry.call")

    monkeypatch.setattr(registry, "call", boom)
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("q")
    app.handle_command("/context")
    app.handle_command("/harness")
    app.handle_command("/clear")


def test_new_session_shape_unchanged():
    rec = new_session()
    assert set(rec) >= {"handles", "reported", "turn_records"}
