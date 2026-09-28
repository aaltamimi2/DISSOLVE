"""The web API: the CLI's agent, sessions and slash commands over HTTP. No model call leaves the machine."""

import json
import re
import threading
import time

import httpx
import pytest
import uvicorn

from dissolve import agent, cli, tea, web, web_tea


@pytest.fixture
def serve(tmp_path, monkeypatch):
    """Start the real server on an ephemeral port, in this process so the scripted model applies; env first."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
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
    assert (state["model"], state["mode"], state["contaminant"], state["literature"]) == ("openrouter-gemini-flash", "review", "off", "off")
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
    monkeypatch.delenv("OPENROUTER_API_KEY")
    events = _stream(client, session_id, "Which solvents dissolve PS near 100 C?")
    assert events[-1]["event"] == "error" and "OPENROUTER_API_KEY is required" in events[-1]["message"]
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
    assert http.get("/api/auth/config").json() == {"accounts": True, "access_code": True, "admin_reads": False}
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


# The TEA panel (web_tea.py): a plant TEA the model starts in a review-mode chat waits for the person.

def _plant():
    """The twelve design-point fields of a stored plant (LDPE in dodecane, C1), in the public vocabulary."""
    record = next(r for r in tea._records() if r["label"] == "ldpe-route-c1")
    public = dict(tea._DESIGN_POINT_PUBLIC_FIELDS)
    return {public[key]: value for key, value in record["config"].items() if key in public}


def _model(monkeypatch, calls, answer="Here are the plants you ran."):
    """A scripted model: one evaluate_process call per round, then the answer. seen[i] is what call i returned."""
    seen = []

    def complete(messages, tools, **kwargs):
        if messages[-1]["role"] == "tool":
            seen.append(json.loads(messages[-1]["content"]))
        if len(seen) < len(calls):
            return {"text": "", "tool_calls": [{"id": f"t{len(seen)}", "name": "evaluate_process",
                                                "args": calls[len(seen)]}]}
        return {"text": answer, "tool_calls": []}

    monkeypatch.setattr(agent, "complete", complete)
    return seen


def _engine(monkeypatch, gate=None):
    """A stub TEA engine for the panel's plant runs: MSP falls with capacity; each call records what it was sent and
    the confirmation it ran under. gate, when given, holds each plant until the test releases it."""
    runs = []

    def evaluate_process(**call):
        if gate is not None:
            gate.acquire(timeout=10)
        config = call["process_config"]
        runs.append({"call": call, "confirmation": tea.PROCESS_CONFIRMATION.get()})
        capacity = float(config["processing_capacity_mt_per_yr"])
        row = {"label": config.get("label"), "success": True, "target_polymer": config["target_polymer"],
               "solvent": config["solvent"], "processing_capacity_mt_per_yr": capacity, "engine_mode": "live",
               "msp_usd_per_kg": round(2.0 - capacity / 1e6, 4), "gwp_kg_co2e_per_kg": 1.5, "tci_usd": 1.0e7,
               "aoc_usd_per_yr": 2.0e6, "total_energy_mj_per_kg": 9.0}
        data = {"success": True, "tool_name": "evaluate_process", "comparison_rows": [row], "warnings": ["stub"],
                "process_confirmation": tea.PROCESS_CONFIRMATION.get()}
        return json.dumps({"display": None, "data": data})

    monkeypatch.setattr(tea, "evaluate_process", evaluate_process)
    monkeypatch.setattr(tea, "_live_tea_blocker", lambda: None)
    return runs


def _tea_turn(http, session_id, text, answer):
    """Run one turn; when the panel opens, answer(other, sheet) replies over a second connection, as the browser's
    panel does while the answer is still streaming."""
    events = []
    with httpx.Client(base_url=str(http.base_url), cookies=http.cookies, timeout=30) as other:
        with http.stream("POST", f"/api/sessions/{session_id}/turns", json={"text": text}) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line:
                    events.append(json.loads(line))
                    if events[-1]["event"] == "tea.sheet":
                        answer(other, events[-1]["sheet"])
    return events


def test_a_plant_tea_waits_for_the_panel_and_runs_what_the_person_confirmed(client, monkeypatch):
    """The web app ran the model's plant values unseen and unconfirmed, with no way to sweep (owner, 2026-09-26). In
    a review-mode chat the panel now shows every field the CLI's sheet shows; the person edits a field and turns
    another into a range, and each plant runs confirmed on the sheet."""
    seen = _model(monkeypatch, [{"mode": "evaluate", "process_config": _plant()}])
    runs = _engine(monkeypatch)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    answered = {}

    def answer(http, sheet):
        answered["sheet"] = sheet
        assert http.get(f"/api/sessions/{session_id}/tea-sheet").json()["sheet"]["id"] == sheet["id"]  # a reload
        ranges = {"processing_capacity_mt_per_yr": {"kind": "list", "values": [10000, 20000, 40000]}}
        report = http.post(f"/api/sessions/{session_id}/tea-sheet/check",
                           json={"sheet_id": sheet["id"], "values": {"irr": 0.15}, "ranges": ranges}).json()
        answered["check"] = report
        ran = http.post(f"/api/sessions/{session_id}/tea-sheet",
                        json={"sheet_id": sheet["id"], "action": "run", "values": {"irr": 0.15}, "ranges": ranges})
        assert ran.json() == {"ok": True, "action": "run", "plants": 3}
        again = http.post(f"/api/sessions/{session_id}/tea-sheet", json={"sheet_id": sheet["id"], "action": "run"})
        assert again.status_code == 409  # answered once

    events = _tea_turn(client, session_id, "What does LDPE recovery in dodecane cost?", answer)
    sheet = answered["sheet"]
    assert len(sheet["fields"]) == 46 and sheet["mode"] == "evaluate"
    assert sheet["values"]["solvent"] == "Dodecane" and sheet["origin"]["solvent"] == "model"
    assert (sheet["values"]["irr"], sheet["origin"]["irr"]) == (0.10, "default")
    irr = next(field for field in sheet["fields"] if field["name"] == "irr")
    assert (irr["unit"], irr["percent"], irr["rangeable"]) == ("%", True, True)
    report = answered["check"]
    assert (report["plants"], report["runnable"], report["invalid"]) == (3, True, [])
    assert report["stored"] + report["live"] == 3 and report["seconds"] == round(report["live"] * 15.13)
    kinds = [event["event"] for event in events]
    assert kinds[:2] == ["turn.started", "tea.sheet"] and kinds[-3:] == ["tea.result", "tool", "turn.completed"]
    assert kinds.count("tea.progress") == 6  # started and finished, per plant
    assert [run["confirmation"] for run in runs] == ["confirmed_on_sheet"] * 3
    assert [run["call"]["process_config"]["processing_capacity_mt_per_yr"] for run in runs] == [10000, 20000, 40000]
    assert {run["call"]["process_config"]["irr"] for run in runs} == {0.15}
    assert all(run["call"]["confirm_live_tea"] is True for run in runs)
    sent = runs[0]["call"]["process_config"]
    assert len(sent) == 46 and "lang_factor" not in sent  # every field with a value (lang_factor is empty) and a label
    result = next(event for event in events if event["event"] == "tea.result")
    assert (result["planned"], result["ran"], result["stopped"]) == (3, 3, False)
    assert [row["values"] for row in result["rows"]] == [{"processing_capacity_mt_per_yr": v} for v in (10000, 20000, 40000)]
    assert result["edited_fields"] == ["irr"] and result["handle"]
    tool = events[-2]
    assert (tool["name"], tool["ok"], tool["source_basis"]) == ("evaluate_process", True, "tea_live")
    (returned,) = seen  # what the model read: one evaluate result, through the handle path
    assert returned["available"] is True and returned["handle"] == result["handle"] and returned["total"] == 3
    data = returned["data"]
    assert data["process_confirmation"] == "confirmed_on_sheet" and data["completed"] == 3
    assert data["lowest_msp_scenario"] == "processing_capacity_mt_per_yr=40000"
    assert data["panel"]["edited_fields"] == {"irr": {"proposed": 0.1, "ran": 0.15}}
    assert data["panel"]["ranged_fields"] == {"processing_capacity_mt_per_yr": [10000, 20000, 40000]}
    assert "irr" not in data["panel"]["defaulted_fields"] and "income_tax" in data["panel"]["defaulted_fields"]
    state = client.get(f"/api/sessions/{session_id}/tea-sheet").json()
    assert state["sheet"] is None and state["progress"] is None and state["result"]["handle"] == result["handle"]


def test_closing_the_panel_tells_the_model_nothing_ran(client, monkeypatch):
    """Cancel is the CLI's abort: the model reads process_confirmation_aborted, nothing runs, and a retry in the same
    answer does not open the panel again."""
    seen = _model(monkeypatch, [{"mode": "evaluate", "process_config": _plant()}] * 2)
    runs = _engine(monkeypatch)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]

    def answer(http, sheet):
        assert http.post(f"/api/sessions/{session_id}/tea-sheet",
                         json={"sheet_id": sheet["id"], "action": "cancel"}).json()["action"] == "cancel"

    events = _tea_turn(client, session_id, "What does LDPE recovery cost?", answer)
    assert [event["event"] for event in events].count("tea.sheet") == 1 and runs == []
    assert [returned["refusal"] for returned in seen] == ["process_confirmation_aborted"] * 2
    tools = [event for event in events if event["event"] == "tool"]
    assert [(tool["ok"], tool["error_code"]) for tool in tools] == [(False, "process_confirmation_aborted")] * 2


def test_an_unanswered_panel_expires_as_a_cancel(client, monkeypatch):
    seen = _model(monkeypatch, [{"mode": "evaluate", "process_config": _plant()}])
    runs = _engine(monkeypatch)
    monkeypatch.setattr(web_tea, "WAIT_SECONDS", 0.3)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    events = _tea_turn(client, session_id, "What does LDPE recovery cost?", lambda http, sheet: None)
    assert runs == [] and seen[0]["refusal"] == "process_confirmation_aborted"
    assert next(event for event in events if event["event"] == "tea.closed")["reason"] == "expire"
    assert "waited 0 minutes" in seen[0]["data"]["error"]


def test_auto_mode_and_lookups_run_without_the_panel(client, monkeypatch):
    """Auto mode is the model's own assumptions, labelled; a lookup of stored records is no plant to confirm."""
    _model(monkeypatch, [{"mode": "lookup", "lookup_filter": {"target_polymer": "LDPE"}}])
    runs = _engine(monkeypatch)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    events = _tea_turn(client, session_id, "Which LDPE plants are stored?", lambda http, sheet: None)
    assert "tea.sheet" not in [event["event"] for event in events] and events[-1]["event"] == "turn.completed"
    _model(monkeypatch, [{"mode": "evaluate", "process_config": _plant()}])
    _stream(client, session_id, "/mode auto")
    events = _tea_turn(client, session_id, "What does LDPE recovery cost?", lambda http, sheet: None)
    assert "tea.sheet" not in [event["event"] for event in events] and runs == []  # the registry's own engine ran


def test_the_panel_checks_plants_before_anything_runs(client, monkeypatch):
    """A refused plant is named before the run (irr 110 % is a percent typed as a fraction), and a grid past 200 plants
    asks for a narrower range; neither starts a plant."""
    _model(monkeypatch, [{"mode": "evaluate", "process_config": _plant()}])
    runs = _engine(monkeypatch)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    seen = {}

    def answer(http, sheet):
        url = f"/api/sessions/{session_id}/tea-sheet"
        bad = {"sheet_id": sheet["id"], "ranges": {"irr": {"kind": "list", "values": [0.08, 1.1]}}}
        seen["bad"] = http.post(url + "/check", json=bad).json()
        seen["bad_run"] = http.post(url, json={**bad, "action": "run"})
        wide = {"sheet_id": sheet["id"], "ranges": {
            "processing_capacity_mt_per_yr": {"kind": "linear", "from": 100, "to": 1_000_000, "step": 10_000},
            "energy_case": {"kind": "list", "values": ["C1", "C2", "C3"]}}}
        seen["wide"] = http.post(url + "/check", json=wide).json()
        feed = {"sheet_id": sheet["id"], "ranges": {
            "processing_capacity_mt_per_yr": {"kind": "linear", "from": 100, "to": 1_000_000, "step": 10_000}}}
        seen["feed"] = http.post(url + "/check", json=feed).json()
        http.post(url, json={"sheet_id": sheet["id"], "action": "cancel"})

    _tea_turn(client, session_id, "What does LDPE recovery cost?", answer)
    assert (seen["bad"]["runnable"], seen["bad"]["invalid_count"]) == (False, 1)
    assert seen["bad"]["invalid"][0]["label"] == "irr=1.1" and "fraction" in seen["bad"]["invalid"][0]["error"]
    assert seen["bad_run"].status_code == 422 and runs == []
    assert (seen["wide"]["runnable"], seen["wide"]["plants"]) == (False, 300) and "at most 200" in seen["wide"]["error"]
    assert (seen["feed"]["plants"], seen["feed"]["runnable"], seen["feed"]["confirm"]) == (100, True, True)


def test_stop_keeps_the_plants_that_finished(client, monkeypatch):
    seen = _model(monkeypatch, [{"mode": "evaluate", "process_config": _plant()}])
    gate = threading.Semaphore(0)
    runs = _engine(monkeypatch, gate)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]

    def answer(http, sheet):
        url = f"/api/sessions/{session_id}/tea-sheet"
        ranges = {"processing_capacity_mt_per_yr": {"kind": "list", "values": [10000, 20000, 40000]}}
        http.post(url, json={"sheet_id": sheet["id"], "action": "run", "ranges": ranges})
        gate.release()  # the first plant finishes
        deadline = time.monotonic() + 10
        while not runs and time.monotonic() < deadline:
            time.sleep(0.02)
        assert http.post(url, json={"sheet_id": sheet["id"], "action": "stop"}).json()["action"] == "stop"
        gate.release()  # the second plant was already running; it finishes, the third never starts

    events = _tea_turn(client, session_id, "What does LDPE recovery cost?", answer)
    result = next(event for event in events if event["event"] == "tea.result")
    assert (result["planned"], result["ran"], result["stopped"]) == (3, 2, True) and len(runs) == 2
    assert seen[0]["data"]["panel"]["stopped_early"] is True and seen[0]["data"]["completed"] == 2


def test_the_model_sweep_arrives_as_a_range_and_several_plants_stay_paired(client, monkeypatch):
    """A sensitivity call opens the panel with its parameter ranged over the model's values; plants that differ in
    two fields together (not a grid) stay one row each."""
    plant = _plant()
    xylene = {**plant, "solvent": "toluene", "dissolution_temperature_c": 110.0, "solvent_price_usd_per_kg": 1.1}
    _model(monkeypatch, [{"mode": "sensitivity", "process_config": plant, "parameter": "solvent_price",
                          "values": [2.0, 6.0]},
                         {"mode": "evaluate", "process_configs": [plant, xylene]}])
    _engine(monkeypatch)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    sheets = []

    def answer(http, sheet):
        sheets.append(sheet)
        http.post(f"/api/sessions/{session_id}/tea-sheet", json={"sheet_id": sheet["id"], "action": "cancel"})

    _tea_turn(client, session_id, "How does the solvent price move the MSP?", answer)
    assert sheets[0]["mode"] == "sensitivity"
    assert sheets[0]["ranges"] == {"solvent_price_usd_per_kg": {"kind": "list", "values": [2, 4.08, 6]}}  # with the plant's own
    assert len(sheets) == 1  # the second call came after a cancel in the same answer
    _model(monkeypatch, [{"mode": "evaluate", "process_configs": [plant, xylene]}])
    _tea_turn(client, session_id, "Compare dodecane and toluene.", answer)
    paired = sheets[1]["paired"]
    assert paired["fields"] == ["solvent", "dissolution_temperature_c", "solvent_price_usd_per_kg"]
    assert paired["rows"] == [["Dodecane", 145.0, 4.08], ["toluene", 110.0, 1.1]]


def test_another_account_cannot_see_or_answer_the_panel(serve, tmp_path, monkeypatch):
    _model(monkeypatch, [])
    alice = _accounts(serve, tmp_path)
    _sign_up(alice, "alice")
    session_id = alice.post("/api/sessions", json={}).json()["session_id"]
    assert alice.get(f"/api/sessions/{session_id}/tea-sheet").json() == {"sheet": None, "progress": None, "result": None}
    with httpx.Client(base_url=str(alice.base_url), timeout=30) as bob:
        _sign_up(bob, "bob")
        assert bob.get(f"/api/sessions/{session_id}/tea-sheet").status_code == 404
        assert bob.post(f"/api/sessions/{session_id}/tea-sheet",
                        json={"sheet_id": "x", "action": "cancel"}).status_code == 404
    assert alice.post(f"/api/sessions/{session_id}/tea-sheet",
                      json={"sheet_id": "x", "action": "cancel"}).status_code == 409  # nothing is waiting


def test_a_plant_the_engine_fails_stays_in_the_results_with_its_reason(client, monkeypatch):
    """One plant's failure is its row, not the run's end: the others complete, the model reads which failed and why,
    and the lowest MSP comes from the plants that ran."""
    seen = _model(monkeypatch, [{"mode": "evaluate", "process_config": _plant()}])
    runs = _engine(monkeypatch)
    working = tea.evaluate_process

    def evaluate_process(**call):
        if call["process_config"]["processing_capacity_mt_per_yr"] == 40000:
            raise RuntimeError("BioSTEAM did not converge")
        return working(**call)

    monkeypatch.setattr(tea, "evaluate_process", evaluate_process)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]

    def answer(http, sheet):
        ranges = {"processing_capacity_mt_per_yr": {"kind": "list", "values": [10000, 20000, 40000]}}
        http.post(f"/api/sessions/{session_id}/tea-sheet", json={"sheet_id": sheet["id"], "action": "run", "ranges": ranges})

    events = _tea_turn(client, session_id, "What does LDPE recovery cost?", answer)
    result = next(event for event in events if event["event"] == "tea.result")
    assert [row["success"] for row in result["rows"]] == [True, True, False] and len(runs) == 2
    assert result["rows"][2]["error"] == "RuntimeError: BioSTEAM did not converge"
    data = seen[0]["data"]
    assert (data["completed"], data["failed"]) == (2, 1)
    assert data["lowest_msp_scenario"] == "processing_capacity_mt_per_yr=20000"


def test_a_follow_up_keeps_the_plant_the_person_corrected(client, monkeypatch):
    """The model proposed a dissolution capacity of 0.1 wt/vol %, the person ran 3, and the model's follow-up repeated
    0.1 (live, 2026-09-26): the panel showed the guess again. The correction now stands while the model repeats its
    guess, the rest of the person's plant carries to a question about the same polymer and solvent, and a value the
    model actually changes still comes through."""
    guess = {**_plant(), "dissolution_capacity": 0.1}
    _model(monkeypatch, [{"mode": "evaluate", "process_config": guess}])
    _engine(monkeypatch)
    session_id = client.post("/api/sessions", json={}).json()["session_id"]
    sheets = []

    def run_with(values):
        def answer(http, sheet):
            sheets.append(sheet)
            http.post(f"/api/sessions/{session_id}/tea-sheet",
                      json={"sheet_id": sheet["id"], "action": "run", "values": values})
        return answer

    _tea_turn(client, session_id, "What does LDPE recovery cost?", run_with({"dissolution_capacity": 3.0,
                                                                              "feedstock_distance_km": 50.0, "irr": 0.12}))
    for turn, call in enumerate([guess, {**guess, "dissolution_capacity": 5.0},
                                 {**_plant(), "solvent": "toluene", "dissolution_temperature_c": 110.0}]):
        _model(monkeypatch, [{"mode": "evaluate", "process_config": {k: v for k, v in call.items()
                                                                       if k != "feedstock_distance_km"}}])
        _tea_turn(client, session_id, f"Follow-up {turn}", run_with({}))
    shown = [(s["values"]["dissolution_capacity"], s["origin"]["dissolution_capacity"],
              s["values"]["feedstock_distance_km"], s["origin"]["feedstock_distance_km"], s["values"]["irr"])
             for s in sheets]
    assert shown[0] == (0.1, "model", 0.0, "model", 0.1)
    assert shown[1] == (3.0, "previous", 50.0, "previous", 0.12)  # the guess again: the person's plant stands
    assert shown[2] == (5.0, "model", 50.0, "previous", 0.12)  # a new value from the model comes through
    assert shown[3][2:] == (0.0, "default", 0.12)  # another solvent: the plant starts over, the coefficients stay


# The admin view (web_admin.py): the owner reads every account's turns to troubleshoot and audit them.

ADMIN = "admin-token-" + "x" * 40


def _admin_site(serve, tmp_path, name="web.sqlite3"):
    return serve(DATABASE_URL=f"sqlite:///{tmp_path / name}", DISSOLVE_WEB_PASSWORD="s3cret", DISSOLVE_ADMIN_TOKEN=ADMIN)


def test_the_admin_reads_every_account_without_their_passwords(serve, tmp_path, monkeypatch):
    """People sent screenshots of problems (owner, 2026-09-28). An admin token now reads every account's chats: the
    question, each tool call and the answer. It never returns a password hash or a sign-in token, it is not an account,
    and a signed-in person cannot use the admin view."""
    _script(monkeypatch, [
        {"text": "", "tool_calls": [{"id": "t1", "name": "lookup_hansen_parameters",
                                     "args": {"material_names": ["toluene"], "material_type": "solvent"}}]},
        {"text": "Toluene: δD 18.0, δP 1.4, δH 2.0 MPa½.", "tool_calls": []},
        {"text": "PS dissolves in toluene.", "tool_calls": []},
    ])
    site = _admin_site(serve, tmp_path)
    assert site.get("/api/auth/config").json()["admin_reads"] is True  # the sign-up page shows the notice
    _sign_up(site, "alice")
    alice_chat = site.post("/api/sessions", json={}).json()["session_id"]
    _stream(site, alice_chat, "Hansen parameters of toluene?")
    with httpx.Client(base_url=str(site.base_url), timeout=30) as bob:
        _sign_up(bob, "bob")
        bob_chat = bob.post("/api/sessions", json={}).json()["session_id"]
        _stream(bob, bob_chat, "Which solvents dissolve PS?")
        assert bob.get("/api/admin/accounts").status_code == 401  # a signed-in person is not the admin
    admin = {"Authorization": f"Bearer {ADMIN}"}
    with httpx.Client(base_url=str(site.base_url), timeout=30) as http:  # no cookie: only the token
        assert http.get("/api/admin/accounts").status_code == 401
        assert http.get("/api/admin/accounts", headers={"Authorization": f"Bearer {ADMIN}x"}).status_code == 401
        accounts = http.get("/api/admin/accounts", headers=admin)
        rows = {row["username"]: row for row in accounts.json()}
        assert set(rows) == {"alice", "bob"} and (rows["alice"]["chats"], rows["alice"]["turns"]) == (1, 1)
        assert not any(word in accounts.text for word in ("scrypt", "password", "token_hash"))
        chats = http.get("/api/admin/chats", headers=admin).json()
        assert {(chat["owner"], chat["session_id"]) for chat in chats} == {("alice", alice_chat), ("bob", bob_chat)}
        (turn,) = http.get(f"/api/admin/chats/{alice_chat}", headers=admin).json()["turns"]
        assert (turn["question"], turn["status"]) == ("Hansen parameters of toluene?", "ok")
        assert turn["answer"].startswith("Toluene: δD 18.0") and turn["elapsed_s"] >= 0
        (call,) = turn["tools"]
        assert (call["name"], call["ok"], call["source_basis"]) == ("lookup_hansen_parameters", True,
                                                                   "qualitative_hansen_parameters")
        assert call["args"] == {"material_names": ["toluene"], "material_type": "solvent"} and "result" not in call
        full = http.get(f"/api/admin/chats/{alice_chat}?full=1", headers=admin).json()
        assert full["turns"][0]["tools"][0]["result"]["available"] is True
        recent = http.get("/api/admin/turns?since=1h", headers=admin).json()
        assert [(t["owner"], t["question"]) for t in recent] == [("bob", "Which solvents dissolve PS?"),
                                                                 ("alice", "Hansen parameters of toluene?")]
        assert http.get("/api/sessions", headers=admin).status_code == 401  # the token is no account
        assert http.get("/api/admin/chats/nosuchchat000", headers=admin).status_code == 404


def test_the_admin_view_is_off_without_a_long_token_and_accounts(serve, tmp_path, monkeypatch):
    site = serve(DATABASE_URL=f"sqlite:///{tmp_path / 'web.sqlite3'}", DISSOLVE_WEB_PASSWORD="s3cret")
    bearer = {"Authorization": f"Bearer {ADMIN}"}
    assert site.get("/api/admin/accounts", headers=bearer).status_code == 404
    assert site.get("/api/auth/config").json()["admin_reads"] is False
    short = serve(DISSOLVE_ADMIN_TOKEN="too-short")
    assert short.get("/api/admin/accounts", headers={"Authorization": "Bearer too-short"}).status_code == 404
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("DISSOLVE_WEB_PASSWORD")
    local = serve(DISSOLVE_ADMIN_TOKEN=ADMIN)  # session files: one person, no accounts to read
    assert local.get("/api/admin/accounts", headers=bearer).status_code == 404


def test_a_report_reaches_the_admin_with_the_turn_it_is_about(serve, tmp_path, monkeypatch):
    """A person reports a problem from the answer itself; the admin reads their note beside the question, the tool
    calls and the answer. Nobody reports on another account's chat."""
    _script(monkeypatch, [{"text": "PS dissolves in toluene.", "tool_calls": []}])
    site = _admin_site(serve, tmp_path)
    _sign_up(site, "alice")
    chat = site.post("/api/sessions", json={}).json()["session_id"]
    _stream(site, chat, "Which solvents dissolve PS?")
    report = site.post(f"/api/sessions/{chat}/reports",
                       json={"question": "Which solvents dissolve PS? ", "note": "It skipped the safety data."})
    assert report.status_code == 200 and report.json()["saved"] is True
    assert site.post(f"/api/sessions/{chat}/reports", json={"question": " ", "note": "x"}).status_code == 400
    with httpx.Client(base_url=str(site.base_url), timeout=30) as bob:
        _sign_up(bob, "bob")
        spam = bob.post(f"/api/sessions/{chat}/reports", json={"question": "Which solvents dissolve PS?", "note": "x"})
        assert spam.status_code == 404
    with httpx.Client(base_url=str(site.base_url), timeout=30) as http:
        (row,) = http.get("/api/admin/reports?since=1h", headers={"Authorization": f"Bearer {ADMIN}"}).json()
    assert (row["owner"], row["session_id"], row["note"]) == ("alice", chat, "It skipped the safety data.")
    assert row["question"] == "Which solvents dissolve PS?"
    assert (row["turn"]["question"], row["turn"]["answer"]) == ("Which solvents dissolve PS?", "PS dissolves in toluene.")


def test_the_admin_sees_a_turn_while_it_runs(serve, tmp_path, monkeypatch):
    """Live troubleshooting: while an answer is still being worked out, the admin sees the question and each tool
    call so far, even for a chat whose first turn has not finished (its row is saved when the turn ends)."""
    release = threading.Event()
    calls = {"n": 0}

    def complete(messages, tools, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "tool_calls": [{"id": "t1", "name": "lookup_hansen_parameters",
                                                "args": {"material_names": ["toluene"], "material_type": "solvent"}}]}
        release.wait(20)
        return {"text": "Toluene: δD 18.0.", "tool_calls": []}

    monkeypatch.setattr(agent, "complete", complete)
    site = _admin_site(serve, tmp_path)
    _sign_up(site, "alice")
    chat = site.post("/api/sessions", json={}).json()["session_id"]
    finished = {}
    worker = threading.Thread(target=lambda: finished.update(events=_stream(site, chat, "Hansen parameters of toluene?")))
    worker.start()
    admin = {"Authorization": f"Bearer {ADMIN}"}
    with httpx.Client(base_url=str(site.base_url), timeout=30) as http:
        deadline, live = time.monotonic() + 20, []
        while time.monotonic() < deadline and not (live and live[0]["tool_calls"] == 1):
            live = http.get("/api/admin/live", headers=admin).json()
            time.sleep(0.05)
        (turn,) = live
        assert (turn["owner"], turn["session_id"], turn["question"]) == ("alice", chat, "Hansen parameters of toluene?")
        assert turn["status"] == "running" and turn["running_s"] >= 0
        assert [call["name"] for call in turn["tools"]] == ["lookup_hansen_parameters"]
        running = http.get("/api/admin/chats", headers=admin).json()
        assert (running[0]["session_id"], running[0]["running"], running[0]["title"]) == (
            chat, True, "Hansen parameters of toluene?")
        release.set()
        worker.join(20)
        assert finished["events"][-1]["event"] == "turn.completed"
        assert http.get("/api/admin/live", headers=admin).json() == []
