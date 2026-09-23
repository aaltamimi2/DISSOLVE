"""DISSOLVE in the browser: a small HTTP API over the agent, sessions and slash commands the CLI runs.

    dissolve web [--host 127.0.0.1] [--port 8765]          (./dissolve web in a checkout)

A turn streams the events `dissolve --once --stream-json` prints, one JSON object per line
(turn.started, tool, command.output, turn.completed, error). Slash commands go through the CLI's own
handler, and sessions are the CLI's session files, so a conversation can move between the two.

    GET  /api/health  /api/doctor  /api/models  /api/commands  /api/sessions
    POST /api/sessions                      {model?, mode?} -> a new session
    GET  /api/sessions/{id}                 its modes and transcript
    POST /api/sessions/{id}/turns {text}    the NDJSON stream of one turn or slash command
    POST /api/client-error                  a crash in someone's browser, printed to this server's log

Everything else serves the built UI in src/dissolve/ui/ (its source is web/).

A hosted copy sets DISSOLVE_WEB_PASSWORD (the whole site asks for it; /api/health stays open for the
platform's checks) and, on a small instance, DISSOLVE_WEB_DISABLE=literature,tea: the literature models
alone need 1.8 GB and a live TEA run 0.9 GB more, so a 1 GB host offers everything else.
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel
from rich.console import Console

from dissolve import RELEASE, cli
from dissolve.agent import ToolEvent

STATIC = Path(__file__).with_name("ui")
_DOCTOR_SECONDS = 120.0
_CLI_ONLY = {"/process": "The process sheet is an interactive CLI editor; describe the process in your message instead.",
             "/harness": None, "/quit": None, "/exit": None, "/q": None}


class NewSession(BaseModel):
    model: Optional[str] = None
    mode: Optional[str] = None


class Turn(BaseModel):
    text: str


def _options(rows: list[tuple[Any, str]]) -> list[dict[str, str]]:
    """The CLI picker rows ("2. leaching   wash against ...") as {value, description}."""
    return [
        {"value": str(key), "description": re.sub(r"^\d+\.\s+\S+\s*", "", label).replace("(current)", "").strip()}
        for key, label in rows if not str(label).rstrip().endswith("...")
    ]


def features() -> dict[str, bool]:
    """What this deployment offers; DISSOLVE_WEB_DISABLE lists what a small host switches off."""
    off = {item.strip().casefold() for item in os.getenv("DISSOLVE_WEB_DISABLE", "").split(",") if item.strip()}
    return {"literature": "literature" not in off, "tea": "tea" not in off}


def commands(offered: dict[str, bool] | None = None) -> list[dict[str, Any]]:
    """The CLI's slash commands; the mode options come from the CLI's own pickers."""
    rows = [
        {"command": "/contaminant", "state": "contaminant", "summary": "How contaminant removal enters separation plans",
         "options": _options(cli._contaminant_picker_options("")[0])
         + [{"value": "compare", "description": "compare leaching and STRAP removal for the last contaminant screen"}]},
        {"command": "/literature", "state": "literature", "summary": "Which literature tools the agent may use",
         "options": _options(cli._literature_picker_options("")[0])},
        {"command": "/solvents", "state": "solvents", "summary": "Which solvents screens draw from",
         "options": _options(cli._solvents_picker_options("")[0])},
        {"command": "/breadth", "state": "breadth", "summary": "Solvent candidates kept per separation stage",
         "options": _options(cli._breadth_picker_options(None)[0])
         + [{"value": "window 5", "description": "every candidate within 5% selectivity of the best"}]},
        {"command": "/mode", "state": "mode", "summary": "How the agent treats unstated process assumptions",
         "options": [{"value": key, "description": line} for key, line in cli._MODE_LINE.items()]},
        {"command": "/model", "state": "model", "summary": "The language model that drives the agent",
         "options": [{"value": alias, "description": f"{spec.label} · {spec.usage}"} for alias, spec in cli.MODELS.items()]},
        {"command": "/context", "summary": "Polymers, temperatures and result handles in play", "options": []},
        {"command": "/cost", "summary": "Tool rounds, tool calls and token usage of the last turn", "options": []},
        {"command": "/safety", "summary": "How the published hazard scores were built", "options": []},
        {"command": "/clear", "summary": "Clear this conversation's messages and handles", "options": []},
    ]
    return [row for row in rows if row["command"] != "/literature" or (offered or features())["literature"]]


def _state(app: cli.CliApp) -> dict[str, Any]:
    session = app.session
    breadth = session.get("planner_breadth") or {}
    return {
        "session_id": app.store.session_id,
        "model": app.model_alias,
        "model_label": app.model_spec.label,
        "model_ready": bool((os.getenv(app.model_spec.env_var) or "").strip()),
        "mode": app.mode,
        "contaminant": (session.get("contaminant_mode") or {}).get("mode") or "off",
        "literature": (session.get("literature_mode") or {}).get("mode") or "off",
        "solvents": (session.get("solvent_scope") or {}).get("scope") or "all",
        "breadth": (f"window {breadth.get('selectivity_window_pct')}" if breadth.get("branch_rule") == "window"
                    else str(breadth.get("breadth") or 1)),
    }


def _tool(event: ToolEvent) -> dict[str, Any]:
    """A tool call as the UI shows it; the full result stays in the session."""
    result = event.result if isinstance(event.result, dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    ok = bool(result.get("available", data.get("success", True))) and data.get("success") is not False
    return {
        "event": "tool", "name": event.name, "summary": cli._tool_event_summary(event), "ok": ok,
        "source_basis": result.get("source_basis") or data.get("source_basis"),
        "error_code": None if ok else (data.get("error_code") or result.get("refusal") or data.get("error_type")),
        "error": None if ok else str(data.get("error") or result.get("error") or "")[:400] or None,
    }


class Sessions:
    """One CliApp per session, as the CLI would hold it; a lock keeps one turn per session at a time."""

    def __init__(self, home: str | Path | None = None):
        self.home = home
        self.apps: dict[str, cli.CliApp] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()

    def root(self) -> Path:
        return Path(self.home or os.getenv("DISSOLVE_HOME") or Path.home() / ".dissolve").expanduser() / "sessions"

    def open(self, session_id: str, *, create: bool = False, model: str | None = None, mode: str | None = None):
        if not cli._SESSION_RE.fullmatch(session_id):
            raise HTTPException(404, "no such session")
        with self.guard:
            if session_id not in self.apps:
                if not create and not (self.root() / session_id / "session.json").is_file():
                    raise HTTPException(404, "no such session")
                console = Console(file=io.StringIO(), width=100, color_system=None, highlight=False, soft_wrap=True)
                try:
                    self.apps[session_id] = cli.CliApp(
                        session_id=session_id, model_alias=model, mode=mode, store_root=self.home,
                        console=console, quiet=True, require_key=False,
                    )
                except ValueError as error:
                    raise HTTPException(400, str(error)) from error
                self.locks[session_id] = threading.Lock()
            return self.apps[session_id], self.locks[session_id]

    def listing(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = []
        for path in self.root().glob("*/session.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            users = [m.get("content") for m in payload.get("messages") or [] if m.get("role") == "user"]
            rows.append({
                "session_id": path.parent.name, "updated_at": payload.get("updated_at"),
                "title": str(users[0])[:120] if users else None, "turns": len(users),
                "model": (payload.get("metadata") or {}).get("model"),
            })
        rows.sort(key=lambda row: str(row["updated_at"] or ""), reverse=True)
        return rows[:limit]


def transcript(app: cli.CliApp) -> list[dict[str, Any]]:
    """The session's turns: each user message, then the answer with the tool calls it made."""
    out: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    path = app.store.transcript_path
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = row.get("role")
        if role == "user":
            out.append({"role": "user", "text": row.get("content") or ""})
            tools = []
        elif role == "tool":
            try:
                result = json.loads(row.get("content") or "{}")
            except json.JSONDecodeError:
                result = {}
            tools.append(_tool(ToolEvent(name=row.get("name") or "", args=row.get("args") or {}, result=result)))
        elif role == "assistant":
            out.append({"role": "assistant", "text": row.get("content") or "", "status": row.get("status"), "tools": tools})
            tools = []
    return out


def _run(app: cli.CliApp, text: str, events: "queue.Queue[dict[str, Any] | None]", offered: dict[str, bool]) -> None:
    started = time.monotonic()
    events.put({"event": "turn.started", "session_id": app.store.session_id, "text": text})
    try:
        if text.startswith("/"):
            command, *rest = text.split()
            command = command.casefold()
            if command in _CLI_ONLY:
                output = _CLI_ONLY[command] or f"{command} has no effect in the web app."
            elif command == "/literature" and not offered["literature"] and rest and rest[0].casefold() != "off":
                output = ("Literature search is off on this deployment: its models need more memory than this server "
                          "has. Run DISSOLVE locally (./dissolve web) to use it.")
            else:
                buffer = app.console.file
                start = buffer.tell()
                app.handle_command(text)
                output = buffer.getvalue()[start:].strip()
            events.put({"event": "command.output", "command": command, "text": output, "state": _state(app)})
            return
        if not (os.getenv(app.model_spec.env_var) or "").strip():
            events.put({"event": "error", "message": (
                f"{app.model_spec.env_var} is required for {app.model_spec.label}. Set it where the server runs, "
                "or pick a model whose key is set."
            )})
            return

        def sink(event: dict[str, Any]) -> None:
            if event.get("event") == "turn.completed":
                events.put({**event, "elapsed_s": round(time.monotonic() - started, 2), "usage": app.last_usage,
                            "tool_rounds": app.last_tool_rounds, "state": _state(app)})

        def on_tool(event: ToolEvent) -> None:
            original(event)
            events.put(_tool(event))

        original = app._print_tool_event
        app.event_sink = sink
        app._print_tool_event = on_tool  # the CLI's own recorder, then the stream
        try:
            app.ask(text)
        finally:
            app.event_sink = None
            del app._print_tool_event
    except (RuntimeError, ValueError) as error:
        events.put({"event": "error", "message": str(error)})
    except Exception as error:  # the stream must end with a reason, whatever failed
        events.put({"event": "error", "message": f"{type(error).__name__}: {error}"})
    finally:
        events.put(None)


def create_app(home: str | Path | None = None) -> FastAPI:
    api = FastAPI(title="DISSOLVE", version=RELEASE, docs_url="/api/docs", openapi_url="/api/openapi.json")
    sessions = api.state.sessions = Sessions(home)
    doctor_cache: dict[str, Any] = {}
    offered = features()

    if password := os.getenv("DISSOLVE_WEB_PASSWORD"):
        expected = ("Basic " + base64.b64encode(f"{os.getenv('DISSOLVE_WEB_USER', 'dissolve')}:{password}".encode()).decode()).encode()

        @api.middleware("http")
        async def require_password(request: Request, call_next):
            if request.url.path == "/api/health" or secrets.compare_digest(request.headers.get("authorization", "").encode(), expected):
                return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="DISSOLVE"'})

    @api.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "release": RELEASE, "ui_built": (STATIC / "index.html").is_file(), "features": offered}

    @api.get("/api/doctor")
    def doctor(refresh: bool = False) -> dict[str, Any]:
        if refresh or time.monotonic() - doctor_cache.get("at", -1e9) > _DOCTOR_SECONDS:
            report = cli.doctor_report(home)
            checks = [{key: check.get(key) for key in ("name", "status", "detail")} for check in report["checks"]]
            for check in checks:
                if check["name"] == "Live TEA" and not offered["tea"]:  # off by design here, not broken
                    check.update(status="not_required", detail="Live TEA is switched off on this deployment.")
            doctor_cache.update(at=time.monotonic(), report={
                "ready": not any(check["status"] == "fail" for check in checks), "checks": checks,
            })
        return doctor_cache["report"]

    @api.get("/api/models")
    def models() -> list[dict[str, Any]]:
        return [{"alias": alias, "label": spec.label, "usage": spec.usage, "key": spec.env_var,
                 "ready": bool((os.getenv(spec.env_var) or "").strip()), "default": alias == cli.DEFAULT_MODEL}
                for alias, spec in cli.MODELS.items()]

    @api.get("/api/commands")
    def command_list() -> list[dict[str, Any]]:
        return commands(offered)

    @api.get("/api/sessions")
    def session_list() -> list[dict[str, Any]]:
        return sessions.listing()

    @api.post("/api/sessions")
    def session_new(body: NewSession) -> dict[str, Any]:
        if body.model is not None and cli.MODEL_ALIASES.get(body.model, body.model) not in cli.MODELS:
            raise HTTPException(400, f"Unknown model alias: {body.model}")
        app, _lock = sessions.open(uuid.uuid4().hex[:12], create=True, model=body.model, mode=body.mode)
        return _state(app)

    @api.get("/api/sessions/{session_id}")
    def session_get(session_id: str) -> dict[str, Any]:
        app, _lock = sessions.open(session_id)
        return {**_state(app), "messages": transcript(app)}

    @api.post("/api/sessions/{session_id}/turns")
    def session_turn(session_id: str, body: Turn) -> StreamingResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty message")
        app, lock = sessions.open(session_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "a turn is already running in this session")
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def work() -> None:
            try:
                _run(app, text, events, offered)
            finally:
                lock.release()

        threading.Thread(target=work, daemon=True, name=f"dissolve-turn-{session_id}").start()

        def stream() -> Iterator[str]:
            while (event := events.get()) is not None:
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @api.post("/api/client-error", status_code=204)
    async def client_error(request: Request) -> Response:
        print(f"browser error: {(await request.body())[:4000].decode('utf-8', 'replace')}", flush=True)
        return Response(status_code=204)

    @api.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "no such endpoint")
        target = (STATIC / path).resolve()
        if path and target.is_file() and STATIC.resolve() in target.parents:
            return FileResponse(target)
        if (STATIC / "index.html").is_file():
            return FileResponse(STATIC / "index.html")
        return HTMLResponse("<p>The DISSOLVE web UI is not built: run <code>npm --prefix web ci && npm --prefix web run "
                            "build</code>. The API is at <a href='/api/docs'>/api/docs</a>.</p>", status_code=503)

    return api


def serve(host: str = "127.0.0.1", port: int = 8765, home: str | Path | None = None) -> int:
    import uvicorn

    print(f"DISSOLVE web: http://{host}:{port}  (API docs at /api/docs; Ctrl+C stops)", flush=True)
    uvicorn.run(create_app(home), host=host, port=port, log_level="warning", ws="none")
    return 0
