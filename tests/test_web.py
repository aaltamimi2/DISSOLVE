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


def test_answer_tables_get_structures_drawn_from_their_smiles(client):
    """"What's the SMILES of each of these?" should come with each structure (owner, 2026-09-25). The page asks the
    server for an RDKit drawing of every SMILES in a table's SMILES column."""
    erucamide = client.get("/api/structure.svg", params={"smiles": "CCCCCCCC/C=C\\CCCCCCCCCCCC(=O)N"})
    assert erucamide.status_code == 200 and erucamide.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in erucamide.text and "viewBox" in erucamide.text  # scales from thumbnail to full view
    assert "default-src 'none'" in erucamide.headers["content-security-policy"]
    assert client.get("/api/structure.svg", params={"smiles": "C1CC"}).status_code == 422  # an unclosed ring
    assert client.get("/api/structure.svg", params={"smiles": "C" * 700}).status_code == 422
    assert client.get("/api/structure.svg").status_code == 422
    small = client.get("/api/structure.svg", params={"smiles": "CCO", "size": "small"}).text
    assert "width='180px'" in small and "width='640px'" in client.get("/api/structure.svg", params={"smiles": "CCO"}).text
    assert client.get("/api/structure.svg", params={"smiles": "CCO", "size": "huge"}).status_code == 422


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


def _accounts(serve, tmp_path):
    """A server with accounts: a database (sqlite here, Postgres on the host) and the site password as the sign-up
    code. Calling it again starts a new server on the same database, as a redeploy does."""
    return serve(DATABASE_URL=f"sqlite:///{tmp_path / 'web.sqlite3'}", DISSOLVE_WEB_PASSWORD="s3cret")


def _sign_up(http, name, password="correct horse"):
    made = http.post("/api/auth/signup", json={"username": name, "password": password, "access_code": "s3cret"})
    assert made.status_code == 200, made.text
    return made


def test_people_sign_up_with_a_username_and_password(serve, tmp_path):
    """Everyone shared one site password, so everyone saw everyone's chats (owner, 2026-09-24). Each person now has
    an account: a username and a password, no email. Sign-up needs the site's access code, so a stranger who finds
    the site cannot spend its model credits."""
    http = _accounts(serve, tmp_path)
    assert http.get("/api/auth/config").json() == {"accounts": True, "access_code": True}
    assert http.get("/api/health").status_code == 200
    assert http.get("/api/sessions").status_code == 401
    assert http.get("/api/sessions", auth=("dissolve", "s3cret")).status_code == 401  # the shared password is not a way in
    details = lambda r: (r.status_code, r.json()["detail"])  # noqa: E731
    no_code = http.post("/api/auth/signup", json={"username": "Alice", "password": "correct horse"})
    assert details(no_code)[0] == 403
    short = http.post("/api/auth/signup", json={"username": "Alice", "password": "short", "access_code": "s3cret"})
    assert details(short) == (400, "A password needs at least 8 characters.")
    spaced = http.post("/api/auth/signup", json={"username": "a b", "password": "correct horse", "access_code": "s3cret"})
    assert details(spaced)[0] == 400
    made = _sign_up(http, "Alice")
    assert made.json()["username"] == "Alice"
    cookie = made.headers["set-cookie"]
    assert "dissolve_login=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert http.get("/api/auth/me").json()["username"] == "Alice"
    taken = http.post("/api/auth/signup", json={"username": "alice", "password": "another one", "access_code": "s3cret"})
    assert details(taken) == (409, "That username is taken.")
    assert http.post("/api/auth/logout").status_code == 200
    assert http.get("/api/auth/me").status_code == 401 and http.get("/api/sessions").status_code == 401
    wrong = http.post("/api/auth/login", json={"username": "alice", "password": "wrong horse"})
    assert details(wrong) == (401, "That username and password do not match an account.")
    assert http.post("/api/auth/login", json={"username": "ALICE", "password": "correct horse"}).json()["username"] == "Alice"
    with httpx.Client(base_url=str(http.base_url), timeout=30) as script:  # a script signs in with Basic credentials
        assert script.get("/api/models", auth=("alice", "correct horse")).status_code == 200
    for _ in range(10):
        http.post("/api/auth/login", json={"username": "alice", "password": "guess guess"})
    assert http.post("/api/auth/login", json={"username": "alice", "password": "correct horse"}).status_code == 429


def test_each_account_sees_only_its_chats_and_they_outlive_a_redeploy(serve, tmp_path, monkeypatch):
    """Chats were files in the container, and every redeploy replaced the container (owner, 2026-09-24). They are
    now rows in the database, each owned by one account; a new server on the same database finds them."""
    _script(monkeypatch, [{"text": "Toluene: δD 18.0, δP 1.4, δH 2.0 MPa½.", "tool_calls": []}])
    alice = _accounts(serve, tmp_path)
    _sign_up(alice, "alice")
    session_id = alice.post("/api/sessions", json={}).json()["session_id"]
    assert _stream(alice, session_id, "/contaminant leaching")[1]["state"]["contaminant"] == "leaching"
    assert _stream(alice, session_id, "Hansen parameters of toluene?")[-1]["event"] == "turn.completed"
    with httpx.Client(base_url=str(alice.base_url), timeout=30) as bob:
        _sign_up(bob, "bob")
        assert bob.get("/api/sessions").json() == []
        assert bob.get(f"/api/sessions/{session_id}").status_code == 404
        assert bob.post(f"/api/sessions/{session_id}/turns", json={"text": "/context"}).status_code == 404
    assert not (tmp_path / "home" / "sessions").exists()  # nothing in the container to lose
    again = _accounts(serve, tmp_path)  # the redeploy: a new server, nothing carried over but the database
    assert again.get("/api/sessions").status_code == 401
    assert again.post("/api/auth/login", json={"username": "alice", "password": "correct horse"}).status_code == 200
    (row,) = again.get("/api/sessions").json()
    assert (row["session_id"], row["title"], row["turns"]) == (session_id, "Hansen parameters of toluene?", 1)
    kept = again.get(f"/api/sessions/{session_id}").json()
    assert kept["contaminant"] == "leaching"
    assert [(m["role"], m["text"][:8]) for m in kept["messages"]] == [("user", "Hansen p"), ("assistant", "Toluene:")]


def test_a_chat_can_be_deleted_by_its_owner_only(serve, tmp_path, client):
    """There was no way to delete a chat (owner, 2026-09-25). Its owner now can; nobody else can, and an answer that
    is still running keeps it until it finishes."""
    session_id = client.post("/api/sessions", json={}).json()["session_id"]  # no database: session files
    _stream(client, session_id, "/contaminant leaching")
    folder = tmp_path / "home" / "sessions" / session_id
    assert folder.is_dir()
    held = client.app.state.sessions.locks[session_id]
    held.acquire()  # as a running turn holds it
    assert client.delete(f"/api/sessions/{session_id}").status_code == 409
    held.release()
    assert client.delete(f"/api/sessions/{session_id}").json() == {"deleted": session_id}
    assert not folder.exists() and client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.delete(f"/api/sessions/{session_id}").status_code == 404
    assert session_id not in [row["session_id"] for row in client.get("/api/sessions").json()]

    alice = _accounts(serve, tmp_path)  # with a database: only the owner
    _sign_up(alice, "alice")
    kept = alice.post("/api/sessions", json={}).json()["session_id"]
    _stream(alice, kept, "/contaminant leaching")
    with httpx.Client(base_url=str(alice.base_url), timeout=30) as bob:
        _sign_up(bob, "bob")
        assert bob.delete(f"/api/sessions/{kept}").status_code == 404
    assert alice.get(f"/api/sessions/{kept}").status_code == 200
    assert alice.delete(f"/api/sessions/{kept}").status_code == 200
    assert alice.get("/api/sessions").json() == []
    again = _accounts(serve, tmp_path)  # the deletion outlives a redeploy too
    again.post("/api/auth/login", json={"username": "alice", "password": "correct horse"})
    assert again.get("/api/sessions").json() == [] and again.get(f"/api/sessions/{kept}").status_code == 404
