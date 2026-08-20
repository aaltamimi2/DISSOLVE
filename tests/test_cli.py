"""CLI surface: persistence, slash commands, doctor, and the run_turn seam."""
from __future__ import annotations

import inspect
import io
import json
import re
import sys
from pathlib import Path

import pytest
from rich.console import Console

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_harness
from agent_harness import ToolEvent, TurnResult, run_turn
from dissolve import cli
from dissolve.cli import CliApp, EXPECTED_REGISTRY_NAMES, doctor_report, main, resolve_model
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
        messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "call-1", "name": "screen_polymer_separation",
                            "args": {"feed": "LDPE"}}],
        })
        messages.append({
            "role": "tool", "tool_call_id": "call-1",
            "name": "screen_polymer_separation", "content": "{}",
        })
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
    return TurnResult(answer="screened forty solvents", status="ok", tool_trace=[ev],
                      turn_record="turn-1", tool_rounds=1)


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
    assert "true_alias" not in src
    assert "budget_profile" not in src
    assert "result.tool_rounds" in src
    assert "result.usage" in src
    assert "last_provider_tokens" not in src


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
    assert by_name["Tool registry"]["detail"] == "32 registered names"
    assert by_name["Tool registry"]["registered"] == len(EXPECTED_REGISTRY_NAMES)
    assert len(EXPECTED_REGISTRY_NAMES) == 32
    assert "normalize_feed_composition" not in EXPECTED_REGISTRY_NAMES
    assert by_name["Scientific assets"]["status"] == "pass"
    assert by_name["Model provider"]["status"] == "fail"
    assert report["ready"] is False
    assert "specialist" not in json.dumps(report).lower()


def test_doctor_fails_when_a_declared_tool_is_missing(tmp_path, monkeypatch):
    import dissolve.registry as registry

    shortened = tuple(t for t in registry.REGISTRY if t.name != "ingest_literature_graph")
    monkeypatch.setattr(registry, "REGISTRY", shortened)
    monkeypatch.setattr(registry, "BY_NAME", {t.name: t for t in shortened})
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Tool registry")
    assert check["status"] == "fail"
    assert "missing ingest_literature_graph" in check["detail"]
    assert "registered names" in check["detail"]


def test_doctor_registry_fails_on_duplicate_names(tmp_path, monkeypatch):
    import dissolve.registry as registry

    monkeypatch.setattr(registry, "REGISTRY", registry.REGISTRY + registry.REGISTRY[:1])
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Tool registry")
    assert check["status"] == "fail"
    assert "REGISTRY" in check["detail"] and "BY_NAME" in check["detail"]


def test_persist_roundtrip_messages_and_handle(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    path = tmp_path / "sessions" / "test-session" / "session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
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
    assert "1 tool round" in out
    assert "status ok" in out
    assert "usage null" in out
    assert "token(s)" not in out
    assert "did not return" not in out.lower()


def test_cost_prints_provider_tokens_when_returned(tmp_path, monkeypatch):
    def fake(query, **kwargs):
        kwargs["messages"].append({"role": "user", "content": query})
        kwargs["messages"].append({"role": "assistant", "content": "done"})
        return TurnResult("done", "ok", [], "turn-1", 1, {"total_tokens": 18})

    monkeypatch.setattr(cli, "run_turn", fake)
    app, buf = _app(tmp_path, monkeypatch)
    app.ask("q")
    app.handle_command("/cost")
    out = buf.getvalue()
    assert '"total_tokens": 18' in out
    assert "usage null" not in out
    assert "did not return" not in out.lower()
    assert "Provider did not return a token count" not in out


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


def test_banner_subtitle_is_v12_release(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.banner()
    shown = buf.getvalue()
    assert "dissolve-v12-0.1" in shown
    assert "v0.4" not in shown


def test_interactive_path_draws_banner_models_and_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    console = Console(file=buf, width=96, force_terminal=False)
    prompts = []
    replies = iter(["/model", "quit"])

    def fake_ask(prompt, **kwargs):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(cli.Prompt, "ask", fake_ask)
    app = CliApp(
        session_id="look-session",
        store_root=tmp_path,
        persist=False,
        console=console,
    )
    app.run()
    shown = buf.getvalue()
    assert "Advanced Recycling Agent" in shown
    assert "dissolve-v12-0.1" in shown
    assert "D I S S O L V E" in shown
    assert "v0.4" not in shown
    assert "Models" in shown
    assert "muse-spark" in shown
    assert "gemini-flash" in shown
    assert prompts == ["\n[bold cyan]>[/]", "\n[bold cyan]>[/]"]


def test_ask_prints_turnresult_answer_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    result = app.ask("screen LDPE")
    shown = buf.getvalue()
    assert result.answer in shown
    assert shown.count(result.answer) >= 1
    assert "registry.call" not in shown


def test_load_strips_stale_turn_and_incomplete_tool_round(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    root = tmp_path / "sessions" / "test-session"
    root.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "session_id": "test-session",
        "release": "dissolve-v12-0.1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call-1", "name": "no_such_tool", "args": {}},
                {"id": "call-2", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "name": "no_such_tool", "content": "{}"},
        ],
        "session": {
            "handles": {}, "reported": [],
            "_turn": "turn-1",
            "turn_records": {"turn-1": [{"tool": "old"}]},
            "polymers_in_play": [], "temperatures_in_play": [],
        },
        "metadata": {"model": "muse-spark", "mode": "review"},
    }
    (root / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    app, _ = _app(tmp_path, monkeypatch)
    assert "_turn" not in app.session
    assert [m.get("role") for m in app.messages] == ["system"]
    assert app.session["turn_records"]["turn-1"] == [{"tool": "old"}]


def test_resume_missing_handle_is_named_refusal_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    n = {"i": 0}

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {"text": "", "tool_calls": [
                {"id": "1", "name": "result_read", "args": {"handle": "ghost-handle"}},
            ]}
        return {"text": "the handle is gone", "tool_calls": []}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    path = tmp_path / "sessions" / "test-session" / "session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session"]["handles"] = {}
    payload["session"]["_turn"] = "turn-1"
    payload["session"]["turn_records"] = {"turn-1": [{"tool": "old"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cli, "run_turn", agent_harness.run_turn)
    app2, _ = _app(tmp_path, monkeypatch)
    assert app2.session.get("handles") == {}
    assert "_turn" not in app2.session
    result = app2.ask("read that shortlist")
    assert result.status == "ok"
    assert result.tool_trace[0].result.get("refusal") == "unknown_handle"
    assert result.turn_record != "turn-1"
    assert app2.session["turn_records"]["turn-1"] == [{"tool": "old"}]


def test_incomplete_round_drops_the_user_group(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    seen = []

    def fake_complete(messages, tools, **kwargs):
        seen.append([m.get("content") for m in messages if m.get("role") == "user"])
        return {"text": "new", "tool_calls": []}

    root = tmp_path / "sessions" / "test-session"
    root.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "session_id": "test-session",
        "release": "dissolve-v12-0.1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "OLD MUTATING REQUEST"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call-1", "name": "no_such_tool", "args": {}},
                {"id": "call-2", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "name": "no_such_tool", "content": "{}"},
        ],
        "session": {"handles": {}, "reported": [], "turn_records": {},
                    "polymers_in_play": [], "temperatures_in_play": []},
        "metadata": {"model": "muse-spark", "mode": "review"},
    }
    (root / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    app, _ = _app(tmp_path, monkeypatch)
    assert not any(m.get("content") == "OLD MUTATING REQUEST" for m in app.messages)
    app.ask("NEW REQUEST")
    assert seen
    assert "OLD MUTATING REQUEST" not in seen[0]
    assert any("NEW REQUEST" in str(c) for c in seen[0])


def test_resume_keeps_complete_empty_id_google_round(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    root = tmp_path / "sessions" / "test-session"
    root.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "session_id": "test-session",
        "release": "dissolve-v12-0.1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": "", "name": "no_such_tool", "content": "{}"},
            {"role": "assistant", "content": "done"},
        ],
        "session": {"handles": {}, "reported": [], "turn_records": {},
                    "polymers_in_play": [], "temperatures_in_play": []},
        "metadata": {"model": "muse-spark", "mode": "review"},
    }
    (root / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    app, _ = _app(tmp_path, monkeypatch)
    assert [m.get("role") for m in app.messages] == [
        "system", "user", "assistant", "tool", "assistant",
    ]
    assert app.messages[-1]["content"] == "done"


def test_v11_session_file_is_refused_and_left_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    root = tmp_path / "sessions" / "legacy-sess"
    root.mkdir(parents=True)
    path = root / "session.json"
    v11 = {
        "schema_version": 1,
        "session_id": "legacy-sess",
        "state": {"handles": {"old": True}},
        "context": {"turns": ["keep-me"]},
        "metadata": {"model": "muse-spark"},
    }
    path.write_text(json.dumps(v11), encoding="utf-8")
    before = path.read_bytes()
    console, _ = _console()
    with pytest.raises(ValueError, match="incompatible"):
        CliApp(session_id="legacy-sess", store_root=tmp_path, persist=True, console=console)
    assert path.read_bytes() == before


def test_context_uses_canonical_handle_total(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.session["handles"]["route-sub"] = {
        "tool": "screen_route_solvent_substitutions",
        "source_basis": "safety_local",
        "exact": {
            "route_stage_assessments": [{"stage": 1}],
            "candidate_substitutions": [{"solvent": f"s{i}"} for i in range(11)],
            "comparison_rows": [{"solvent": "a"}, {"solvent": "b"}],
            "candidate_conditions": [{"c": i} for i in range(12)],
        },
    }
    app.handle_command("/context")
    shown = buf.getvalue()
    assert "route-sub" in shown
    assert re.search(r'"total":\s*11\b', shown)
    assert not re.search(r'"total":\s*1\b', shown)


def test_cost_survives_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("q")
    app2, buf = _app(tmp_path, monkeypatch)
    app2.handle_command("/cost")
    out = buf.getvalue()
    assert "No model usage in this process yet" not in out
    assert "status ok" in out
    assert "1 tool round" in out


def test_cost_rounds_count_current_group_after_compaction(tmp_path, monkeypatch):
    def compacting_turn(query, *, messages, on_event, **kwargs):
        start = next(i for i, msg in enumerate(messages) if msg.get("role") == "user")
        end = next(
            i for i in range(start + 1, len(messages)) if messages[i].get("role") == "user"
        )
        del messages[start:end]
        messages.append({"role": "user", "content": query})
        messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "now", "name": "no_such_tool", "args": {}}],
        })
        ev = ToolEvent("no_such_tool", {}, {"ok": True})
        if on_event:
            on_event(ev)
        messages.append({
            "role": "tool", "tool_call_id": "now", "name": "no_such_tool", "content": "{}",
        })
        messages.append({"role": "assistant", "content": "done"})
        return TurnResult("done", "ok", [ev], "turn-now", 1)

    monkeypatch.setattr(cli, "run_turn", compacting_turn)
    app, buf = _app(tmp_path, monkeypatch)
    for i in range(6):
        app.messages.extend([
            {"role": "user", "content": f"old-{i}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"old-{i}", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": f"old-{i}", "name": "no_such_tool", "content": "{}"},
            {"role": "assistant", "content": f"ans-{i}"},
        ])
    before = sum(1 for m in app.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert before == 6
    app.ask("NEW REQUEST")
    after = sum(1 for m in app.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert after == 6
    assert app.last_tool_rounds == 1
    assert app.last_tool_calls == 1
    app.handle_command("/cost")
    out = buf.getvalue()
    assert "1 tool round" in out
    assert "0 tool round" not in out


def test_oneshot_resolves_through_cli_table(monkeypatch, tmp_path):
    seen = {}

    def fake_run_turn(query, **kwargs):
        seen.update(kwargs)
        seen["query"] = query
        return TurnResult("ok", "ok", [], "turn-1")

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setenv("DISSOLVE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["agent_harness.py", "hello"])
    monkeypatch.setattr(cli, "run_turn", fake_run_turn)
    with pytest.raises(SystemExit) as exc:
        agent_harness._main()
    assert exc.value.code == 0
    assert seen["query"] == "hello"
    assert seen["model"] == "openai:muse-spark-1.2"
    assert seen["api_base"] == "https://api.meta.ai/v1"
    assert seen["api_key_env"] == "META_MUSE_API_KEY"


def test_positional_oneshot_persists_exact_tool_result(tmp_path, monkeypatch, capsys):
    home = tmp_path / "dissolve-home"
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setenv("DISSOLVE_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["agent_harness.py", "what is the safety of dodecane?"])
    monkeypatch.setattr(
        agent_harness, "complete",
        _complete_one_tool(
            "get_solvent_safety_card",
            {"solvent_name": "dodecane", "include_pubchem": False},
        ),
    )
    try:
        agent_harness._main()
    except SystemExit as exc:
        assert exc.code == 0
    sessions = list(home.glob("sessions/*/session.json"))
    transcripts = list(home.glob("sessions/*/transcript.jsonl"))
    assert sessions and transcripts
    session_text = sessions[0].read_text(encoding="utf-8")
    transcript_text = transcripts[0].read_text(encoding="utf-8")
    assert "physical_properties" in session_text
    assert "112-40-3" in session_text
    assert "physical_properties" in transcript_text
    assert "112-40-3" in transcript_text
    shown = capsys.readouterr().out
    assert "physical_properties" not in shown
    assert "112-40-3" not in shown
    assert "tool  get_solvent_safety_card" in shown
    assert "status=ok" in shown
    assert "Advanced Recycling Agent" not in shown


def test_positional_oneshot_missing_key_is_provider_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    monkeypatch.setenv("DISSOLVE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", ["agent_harness.py", "hello"])
    try:
        agent_harness._main()
    except SystemExit:
        pass
    except RuntimeError as exc:
        pytest.fail(f"RuntimeError escaped the positional entrypoint: {exc}")
    out, err = capsys.readouterr()
    assert "Traceback" not in err
    assert "RuntimeError" not in err
    assert "missing environment variable META_MUSE_API_KEY" in out
    assert "status=provider_error" in out
    assert "Traceback" not in out


def test_usage_is_display_only_except_the_cost_line(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 18, 6, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 1000.0)

    injected = [
        None,
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        {"total_tokens": 18},
    ]
    snapshots = []
    cost_lines = []
    for i, usage in enumerate(injected):
        def fake_complete(messages, tools, *, _usage=usage, **kwargs):
            return {"text": "same-answer", "tool_calls": [], "usage": _usage}

        monkeypatch.setattr(agent_harness, "complete", fake_complete)
        console, buf = _console()
        app = CliApp(
            session_id=f"usage-{i}",
            store_root=tmp_path,
            console=console,
            persist=True,
        )
        result = app.ask("same query")
        ask_out = buf.getvalue()
        app.handle_command("/cost")
        cost_lines.append(buf.getvalue()[len(ask_out):])
        state = json.loads(app.store.state_path.read_text(encoding="utf-8"))
        state.pop("session_id", None)
        state.get("metadata", {}).pop("last_usage", None)
        snapshots.append({
            "answer": result.answer,
            "status": result.status,
            "tool_trace": [(e.name, e.args, e.result) for e in result.tool_trace],
            "messages": json.dumps(app.messages, sort_keys=True),
            "turn_records": json.dumps(app.session.get("turn_records"), sort_keys=True),
            "archive": json.dumps(state, sort_keys=True),
            "transcript": app.store.transcript_path.read_text(encoding="utf-8"),
            "ask": ask_out,
        })
    assert snapshots[1] == snapshots[0]
    assert snapshots[2] == snapshots[0]
    assert cost_lines[0] != cost_lines[1]
    assert cost_lines[0] != cost_lines[2]
    assert cost_lines[1] != cost_lines[2]
    assert "usage null" in cost_lines[0]
    assert '"input_tokens": 0' in cost_lines[1]
    assert '"output_tokens": 0' in cost_lines[1]
    assert '"total_tokens": 0' in cost_lines[1]
    assert '"total_tokens": 18' in cost_lines[2]
    assert "No model usage in this process yet" not in "".join(cost_lines)


_SAFETY_PAYLOAD = {
    "identity": {"name": "dodecane"},
    "physical_properties": {"bp_c": 216},
    "gscore": 4.1,
    "ghs": {"pictograms": ["flame"]},
    "toxicity": {"ld50": "none"},
    "occupational_exposure_limits": {"twa": "none"},
    "peroxide_risk": {"class": "none"},
    "process_temperature_assessment": {"ok": True},
    "data_gaps": [],
    "provenance": {"source_basis": "safety_local"},
}


def _safety_turn(query, *, session, model, messages, on_event, api_base, api_key_env):
    if messages is not None:
        messages.append({"role": "user", "content": query})
        messages.append({"role": "assistant", "content": "dodecane is a hydrocarbon."})
    ev = ToolEvent(
        "get_solvent_safety_card",
        {"solvent_name": "dodecane", "include_pubchem": False},
        _SAFETY_PAYLOAD,
    )
    if on_event:
        on_event(ev)
    return TurnResult(
        "dodecane is a hydrocarbon.", "ok", [ev], "turn-1", 1, None,
    )


def test_tool_event_print_is_one_line_without_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _safety_turn)
    app, buf = _app(tmp_path, monkeypatch)
    result = app.ask("what is the safety of dodecane?")
    shown = buf.getvalue()
    blob = json.dumps(_SAFETY_PAYLOAD, ensure_ascii=False)
    size = len(blob.encode("utf-8"))
    summary = cli._tool_event_summary(result.tool_trace[0])
    assert summary in shown
    assert "get_solvent_safety_card(solvent_name=dodecane, include_pubchem=False" in summary
    assert f"-> {size} B" in summary
    tool_lines = [ln for ln in shown.splitlines() if "get_solvent_safety_card" in ln]
    assert len(tool_lines) == 1
    assert "\n" not in summary
    assert shown.count("get_solvent_safety_card") == 1
    for key in (
        "physical_properties", "gscore", "occupational_exposure_limits",
        "peroxide_risk", "process_temperature_assessment", "data_gaps",
    ):
        assert key not in shown
    assert result.tool_trace[0].result == _SAFETY_PAYLOAD
    rows = [
        json.loads(line)
        for line in app.store.transcript_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tool_row = next(row for row in rows if row.get("role") == "tool")
    assert json.loads(tool_row["content"]) == _SAFETY_PAYLOAD


def test_stream_json_emits_complete_tool_result(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _safety_turn)
    events = []
    console, buf = _console()
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    app = CliApp(
        session_id="stream-json",
        store_root=tmp_path,
        console=console,
        persist=False,
        event_sink=events.append,
        quiet=True,
    )
    app.ask("what is the safety of dodecane?")
    shown = buf.getvalue()
    tool_events = [ev for ev in events if ev.get("event") == "tool"]
    assert len(tool_events) == 1
    assert tool_events[0]["name"] == "get_solvent_safety_card"
    assert tool_events[0]["result"] == _SAFETY_PAYLOAD
    assert tool_events[0]["result"]["gscore"] == 4.1
    assert "gscore" not in shown
    assert "physical_properties" not in shown
    assert "get_solvent_safety_card" not in shown


def test_tool_event_line_escapes_multiline_query(tmp_path, monkeypatch):
    event = ToolEvent(
        "search_scholarly_literature",
        {"query": "alpha\nRAW SECOND LINE"},
        {"hits": 0},
    )

    def fake_turn(query, *, session, model, messages, on_event, api_base, api_key_env):
        if messages is not None:
            messages.append({"role": "user", "content": query})
            messages.append({"role": "assistant", "content": "none"})
        if on_event:
            on_event(event)
        return TurnResult("none", "ok", [event], "turn-1", 1, None)

    monkeypatch.setattr(cli, "run_turn", fake_turn)
    app, buf = _app(tmp_path, monkeypatch)
    app.ask("literature")
    summary = cli._tool_event_summary(event)
    shown = buf.getvalue()
    assert "\n" not in summary
    assert "\\n" in summary
    assert "RAW SECOND LINE" not in shown.splitlines()
    assert sum(1 for ln in shown.splitlines() if "search_scholarly_literature" in ln) == 1


def test_tool_event_line_bounds_long_string():
    event = ToolEvent(
        "search_scholarly_literature",
        {"query": "Q" * 5000},
        {"hits": 0},
    )
    summary = cli._tool_event_summary(event)
    assert "\n" not in summary
    assert len(summary) <= 48 + 160 + 80
    assert ("Q" * 5000) not in summary
    assert summary.count("Q") <= cli._ARG_ITEM_MAX


def test_tool_event_line_keeps_container_identity():
    result = {"ok": True}
    left = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["LDPE"], "solvent_names": ["dodecane", "xylene"]},
        result,
    ))
    right = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["HDPE"], "solvent_names": ["hexane", "toluene"]},
        result,
    ))
    assert left != right
    assert "LDPE" in left and "HDPE" in right
    assert "dodecane" in left and "hexane" in right
    assert "<1>" not in left and "<2>" not in left


def _complete_one_tool(name, args):
    def fake_complete(messages, tools, **kwargs):
        if any(m.get("role") == "tool" for m in messages):
            return {"text": "done", "tool_calls": []}
        return {
            "text": "",
            "tool_calls": [{"id": "c1", "name": name, "args": args}],
        }
    return fake_complete


def _run_one_tool(monkeypatch, name, args):
    monkeypatch.setattr(agent_harness, "complete", _complete_one_tool(name, args))
    events = []
    result = run_turn(
        "q", session=new_session(), model="openai:x", on_event=events.append,
    )
    return result, events


def _summary_args_body(summary: str) -> str:
    inner = summary.split("(", 1)[1].rsplit(") -> ", 1)[0]
    return re.sub(r" #[0-9a-f]+$", "", inner)


def test_tool_event_line_escapes_name_via_unknown_tool_refusal(monkeypatch):
    result, events = _run_one_tool(monkeypatch, "unknown\nTOOL", {})
    assert events and events[0].result.get("refusal") == "unknown_tool"
    assert result.tool_trace[0].result.get("refusal") == "unknown_tool"
    summary = cli._tool_event_summary(events[0])
    assert "\n" not in summary
    assert "\\n" in summary
    assert "unknown\\nTOOL" in summary


def test_tool_event_line_escapes_key_via_tool_exception_refusal(monkeypatch):
    result, events = _run_one_tool(
        monkeypatch, "solubility_query", {"bad\nKEY": "x"},
    )
    assert events and events[0].result.get("refusal") == "tool_exception"
    assert result.tool_trace[0].name == "solubility_query"
    summary = cli._tool_event_summary(events[0])
    assert "\n" not in summary
    assert "\\n" in summary
    assert "bad\\nKEY" in summary


def test_tool_event_line_bounds_unknown_tool_name_via_run_turn(monkeypatch):
    name = "U" * 5000
    result, events = _run_one_tool(monkeypatch, name, {})
    assert events and events[0].result.get("refusal") == "unknown_tool"
    summary = cli._tool_event_summary(events[0])
    assert "\n" not in summary
    assert len(summary) <= 48 + 160 + 80
    assert name not in summary
    assert summary.count("U") <= cli._ARG_ITEM_MAX
    assert result.tool_trace[0].name == name


def test_tool_event_line_distinguishes_unsampled_container_tail():
    result = {"ok": True}
    left = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["LDPE"], "solvent_names": ["dodecane", "xylene", "hexane"]},
        result,
    ))
    right = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["LDPE"], "solvent_names": ["dodecane", "xylene", "toluene"]},
        result,
    ))
    assert _summary_args_body(left) == _summary_args_body(right)
    assert "hexane" not in left and "toluene" not in right
    assert left != right
    fps = re.findall(
        rf"(?<=#)[0-9a-f]{{{cli._FP_HEX}}}", left + " " + right,
    )
    assert len(set(fps)) == 2


def test_tool_event_line_distinguishes_args_past_global_truncation():
    padding = {f"arg{i:02d}": "xxxx" for i in range(30)}
    left = cli._tool_event_summary(ToolEvent("probe", {**padding, "zz": "one"}, {}))
    right = cli._tool_event_summary(ToolEvent("probe", {**padding, "zz": "two"}, {}))
    assert _summary_args_body(left) == _summary_args_body(right)
    assert "zz=" not in _summary_args_body(left)
    assert left != right
