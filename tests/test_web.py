"""The web API: the CLI's agent, sessions and slash commands over HTTP. No model call leaves the machine."""

import json
import re
import threading
import time

import httpx
import pytest
import uvicorn

from dissolve import agent, cli, web


@pytest.fixture
def serve(tmp_path, monkeypatch):
    """Start the real server on an ephemeral port, in this process so the scripted model applies; env first."""
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr(web, "STATIC", tmp_path / "ui")
    running = []

    def start(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        app = web.create_app(tmp_path / "home")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", ws="none"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        http = httpx.Client(base_url=f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}", timeout=30)
        http.app = app
        running.append((server, thread, http))
        return http

    yield start
    for server, thread, http in running:
        http.close()
        server.should_exit = True
        thread.join(10)


@pytest.fixture
def client(serve):
    return serve()


def _script(monkeypatch, steps):
    calls = {"n": 0}

    def complete(messages, tools, **kwargs):
        calls["n"] += 1
        return steps[calls["n"] - 1]

    monkeypatch.setattr(agent, "complete", complete)
    return calls


def _stream(client, session_id, text):
    with client.stream("POST", f"/api/sessions/{session_id}/turns", json={"text": text}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in response.iter_lines() if line]


def test_the_api_is_the_cli_surface(client):
    assert client.get("/api/health").json()["ok"] is True
    assert [row["alias"] for row in client.get("/api/models").json()] == list(cli.MODELS)
    commands = {row["command"]: row for row in client.get("/api/commands").json()}
    assert [o["value"] for o in commands["/contaminant"]["options"]] == ["off", "leaching", "strap", "compare"]
    assert [o["value"] for o in commands["/literature"]["options"]] == ["off", "corpus", "scholarly"]
    assert [o["value"] for o in commands["/breadth"]["options"]][:5] == ["1", "3", "5", "10", "all"]
    assert commands["/literature"]["options"][1]["description"] == "local pinned index, offline"


def test_the_family_picker_lists_what_each_family_screens(client):
    """In contaminant mode the composer offers the contaminant families, since nobody knows 5,830 names (owner,
    2026-09-24). Each carries the term a question uses and the members the screens evaluate."""
    families = {row["name"]: row for row in client.get("/api/contaminant-families").json()}
    assert families["Bisphenols"]["term"] == "bisphenols" and families["Bisphenols"]["count"] == 24
    assert "Bisphenol A" in families["Bisphenols"]["members"]
    assert families["UV stabilizers"]["examples"] == ["UV-328", "Tinuvin P", "Octabenzone"]
    assert families["PFAS"]["source"] == "workbook"


def test_a_turn_streams_its_tool_calls_then_the_answer(client, monkeypatch):
    _script(monkeypatch, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "lookup_hansen_parameters",
                                     "args": {"material_names": ["toluene"], "material_type": "solvent"}}]},
        {"text": "Toluene: δD 18.0, δP 1.4, δH 2.0 MPa½.", "tool_calls": []},
    ])
    state = client.post("/api/sessions", json={}).json()
    assert (state["model"], state["mode"], state["contaminant"], state["literature"]) == ("muse-spark", "review", "off", "off")
    events = _stream(client, state["session_id"], "Hansen parameters of toluene?")
    assert [e["event"] for e in events] == ["turn.started", "tool", "turn.completed"]
    tool = events[1]
    assert (tool["name"], tool["ok"], tool["source_basis"]) == ("lookup_hansen_parameters", True, "qualitative_hansen_parameters")
    assert tool["summary"].startswith("lookup_hansen_parameters(material_names=[toluene]")
    done = events[2]
    assert (done["status"], done["answer"], done["tool_calls"]) == ("ok", "Toluene: δD 18.0, δP 1.4, δH 2.0 MPa½.", 1)
    assert done["elapsed_s"] >= 0 and done["state"]["session_id"] == state["session_id"]
    stored = client.get(f"/api/sessions/{state['session_id']}").json()
    assert [(m["role"], m["text"][:8]) for m in stored["messages"]] == [("user", "Hansen p"), ("assistant", "Toluene:")]
    assert [t["name"] for t in stored["messages"][1]["tools"]] == ["lookup_hansen_parameters"]
    assert client.get("/api/sessions").json()[0]["title"] == "Hansen parameters of toluene?"


def test_slash_commands_run_through_the_cli_handler(client, monkeypatch):
    calls = _script(monkeypatch, [])
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    events = _stream(client, session_id, "/contaminant leaching")
    assert [e["event"] for e in events] == ["turn.started", "command.output"]
    assert "contaminant_mode=leaching" in events[1]["text"]
    assert events[1]["state"]["contaminant"] == "leaching"
    assert _stream(client, session_id, "/literature corpus")[1]["state"]["literature"] == "corpus"
    assert "interactive CLI editor" in _stream(client, session_id, "/process")[1]["text"]
    assert _stream(client, session_id, "/model nope")[1] == {"event": "error", "message": "Unknown model alias: nope"}
    assert client.get(f"/api/sessions/{session_id}").json()["contaminant"] == "leaching"  # the CLI's session file
    assert calls["n"] == 0


def test_a_missing_model_key_is_an_error_not_a_call(client, monkeypatch):
    calls = _script(monkeypatch, [])
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    monkeypatch.delenv("META_MUSE_API_KEY")
    events = _stream(client, session_id, "Which solvents dissolve PS near 100 C?")
    assert events[-1]["event"] == "error" and "META_MUSE_API_KEY is required" in events[-1]["message"]
    assert calls["n"] == 0


def test_one_turn_at_a_time_and_unknown_sessions(client):
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    assert client.get("/api/sessions/nosuchsession").status_code == 404
    assert client.post("/api/sessions", json={"model": "nope"}).status_code == 400
    _app, lock = client.app.state.sessions.open(session_id)
    with lock:
        assert client.post(f"/api/sessions/{session_id}/turns", json={"text": "hi"}).status_code == 409
    assert client.post(f"/api/sessions/{session_id}/turns", json={"text": "  "}).status_code == 400


def test_the_ui_is_served_and_the_api_is_not_shadowed(client, tmp_path):
    assert client.get("/").status_code == 503  # not built
    (tmp_path / "ui" / "assets").mkdir(parents=True)
    (tmp_path / "ui" / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (tmp_path / "ui" / "assets" / "app.js").write_text("ok", encoding="utf-8")
    assert client.get("/").text == "<div id=root></div>"
    assert client.get("/sessions/abc").text == "<div id=root></div>"  # the UI's own routes
    assert client.get("/assets/app.js").text == "ok"
    assert client.get("/api/nope").status_code == 404
    assert client.get("/..%2Fpyproject.toml").text == "<div id=root></div>"


def test_a_hosted_copy_asks_for_its_password(serve):
    http = serve(DISSOLVE_WEB_PASSWORD="s3cret")
    assert http.get("/api/health").status_code == 200  # the platform's health check stays open
    denied = http.get("/api/models")
    assert (denied.status_code, denied.headers["www-authenticate"]) == (401, 'Basic realm="DISSOLVE"')
    assert http.get("/", auth=("dissolve", "wrong")).status_code == 401
    assert http.get("/api/models", auth=("dissolve", "s3cret")).status_code == 200


def test_a_browser_crash_reaches_the_server_log(serve, capfd):
    http = serve(DISSOLVE_WEB_PASSWORD="s3cret")
    report = {"message": "Failed to execute 'insertBefore' on 'Node'", "agent": "a test browser"}
    assert http.post("/api/client-error", json=report).status_code == 401  # not a way past the password
    assert http.post("/api/client-error", json=report, auth=("dissolve", "s3cret")).status_code == 204
    logged = capfd.readouterr().out
    assert "browser error:" in logged and "insertBefore" in logged and "a test browser" in logged


def test_a_small_host_switches_literature_and_tea_off(serve):
    http = serve(DISSOLVE_WEB_DISABLE="literature,tea")
    assert http.get("/api/health").json()["features"] == {"literature": False, "tea": False}
    assert "/literature" not in [row["command"] for row in http.get("/api/commands").json()]
    session_id = http.post("/api/sessions", json={}).json()["session_id"]
    refused = _stream(http, session_id, "/literature corpus")[1]
    assert "Literature search is off on this deployment" in refused["text"]
    assert refused["state"]["literature"] == "off"
    assert _stream(http, session_id, "/literature off")[1]["state"]["literature"] == "off"


def test_the_built_ui_ships_with_the_package():
    index = (web.STATIC / "index.html").read_text(encoding="utf-8")
    assets = re.findall(r'(?:src|href)="/(assets/[^"]+)"', index)
    assert {asset.rsplit(".", 1)[-1] for asset in assets} == {"js", "css"}
    assert all((web.STATIC / asset).is_file() for asset in assets)


def test_dissolve_web_starts_the_server(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(web, "serve", lambda **kwargs: seen.update(kwargs) or 0)
    assert cli.main(["web", "--port", "9999", "--home", str(tmp_path)]) == 0
    assert seen == {"host": "127.0.0.1", "port": 9999, "home": str(tmp_path)}
