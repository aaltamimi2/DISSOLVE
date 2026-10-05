"""DISSOLVE in the browser: a small HTTP API over the agent, sessions and slash commands the CLI runs.

    dissolve web [--host 127.0.0.1] [--port 8765]          (./dissolve web in a checkout)

A turn streams the events `dissolve --once --stream-json` prints, one JSON object per line
(turn.started, tool, command.output, turn.completed, error). Slash commands go through the CLI's own
handler, and sessions are the CLI's session files, so a conversation can move between the two.

    GET  /api/health  /api/doctor  /api/models  /api/commands  /api/sessions
    GET  /api/contaminant-families          the families a question can name instead of their members
    GET  /api/structure.svg?smiles=...&size=small|large    an RDKit drawing, for answer tables with a SMILES column
    POST /api/sessions                      {model?, mode?} -> a new session
    GET  /api/sessions/{id}                 its modes and transcript
    POST /api/sessions/{id}/turns {text}    the NDJSON stream of one turn or slash command
    DELETE /api/sessions/{id}               remove a chat (with a database, only its owner can)
    GET  /api/sessions/{id}/tea-sheet       the TEA panel: a sheet waiting for an answer, a run, the last results
    POST /api/sessions/{id}/tea-sheet/check {sheet_id, values, ranges, drop} -> plants, stored, time, refused plants
    POST /api/sessions/{id}/tea-sheet       {sheet_id, action: run|cancel|stop, values, ranges, drop}
    POST /api/sessions/{id}/reports         {question, note}: report a problem with one of your answers
    GET  /api/admin/...                     the read-only admin view (web_admin.py; needs DISSOLVE_ADMIN_TOKEN)
    GET  /api/admin/events                  its questions, answers and reports as they happen (server-sent events)
    POST /api/client-error                  a crash in someone's browser, printed to this server's log
    GET  /api/auth/config  /api/auth/me     whether this server has accounts; who is signed in
    POST /api/auth/signup  /api/auth/login  /api/auth/logout

Everything else serves the built UI in src/dissolve/ui/ (its source is web/).

In a review-mode chat a plant TEA the model starts waits for the person in the TEA panel (web_tea.py): every field
the CLI's process sheet shows, with ranges, checked before anything runs.

With DATABASE_URL set, people have accounts (web_accounts.py): each signs up with a username and a password, sees
only their own chats, and keeps them across redeploys, because accounts and chats live in that database and not in
the container. Sign-up asks for DISSOLVE_SIGNUP_CODE (by default DISSOLVE_WEB_PASSWORD), or any comma-separated code
in DISSOLVE_SIGNUP_CODES, so that a stranger who finds the site cannot spend its model credits. Case, spaces and
hyphens in a code do not count, so a short code can be typed from a slide. Without a database, DISSOLVE_WEB_PASSWORD
makes the whole site ask for one shared password and sessions are files, as a local `dissolve web` keeps them.

On a small instance DISSOLVE_WEB_DISABLE=literature,tea: the literature models alone need 1.8 GB and a live TEA run
0.9 GB more, so a 1 GB host offers everything else. The hosted image serves both on 2 GB (see its Dockerfile), with
three limits from here: DISSOLVE_MAX_TURNS answers at once (TurnSlots), a memory limit on every DuckDB database
(DISSOLVE_DUCKDB_MEMORY_LIMIT, cap_duckdb) and at most _OPEN_CHATS chats held in memory (Sessions).
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from rich.console import Console
from starlette.concurrency import run_in_threadpool

from dissolve import RELEASE, cli, contaminants, web_accounts, web_admin, web_tea
from dissolve.agent import ToolEvent

STATIC = Path(__file__).with_name("ui")
_DOCTOR_SECONDS = 120.0
_CLI_ONLY = {"/process": "The TEA sheet needs live TEA, which this deployment switches off.",
             "/harness": None, "/quit": None, "/exit": None, "/q": None}


class NewSession(BaseModel):
    model: Optional[str] = None
    mode: Optional[str] = None


class Turn(BaseModel):
    text: str


class ReportBody(BaseModel):
    question: str = ""
    note: str = ""


class SheetAnswer(BaseModel):
    sheet_id: str
    action: str = "run"
    values: dict[str, Any] = {}
    ranges: dict[str, Any] = {}
    drop: list[int] = []


class Credentials(BaseModel):
    username: str = ""
    password: str = ""
    access_code: Optional[str] = None


COOKIE = "dissolve_login"
_ATTEMPTS, _ATTEMPT_WINDOW_S = 10, 600.0  # failed sign-ins per address and username before a pause
_OPEN_CHATS = 24  # chats held in memory; past it the least recently used idle one leaves (Sessions._make_room)
_IN_FLIGHT_PER_PLACE = 4  # answers in progress in any state (running, waiting for a place or a TEA panel) per place
_MAX_BODY = 2 * 2**20  # bytes in one request; a body is read whole into memory
_HELD_S = 60.0  # a chat used this recently stays in memory whatever the count
_KEPT_RESULTS = 32  # finished TEA tables kept for chats that left memory


def _code_key(code: str) -> str:
    """An access code as people type it: case, spaces and hyphens do not count (a phone capitalises the first letter)."""
    return re.sub(r"[\s\-]+", "", code or "").casefold()


def _signup_codes() -> list[str]:
    """Every code sign-up accepts: DISSOLVE_SIGNUP_CODE (by default DISSOLVE_WEB_PASSWORD), plus each comma-separated
    entry of DISSOLVE_SIGNUP_CODES, short codes for people who type them in, say from a slide after scanning a QR code."""
    codes = [os.getenv("DISSOLVE_SIGNUP_CODE", os.getenv("DISSOLVE_WEB_PASSWORD", ""))]
    codes += os.getenv("DISSOLVE_SIGNUP_CODES", "").split(",")
    return [key for key in map(_code_key, codes) if key]


def _code_accepted(typed: Optional[str], codes: list[str]) -> bool:
    key = _code_key(typed or "").encode()
    return any([secrets.compare_digest(key, code.encode()) for code in codes])  # a list: every code is compared


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
        {"command": "/process", "summary": "The TEA panel with every field: check the defaults, edit, run a plant",
         "options": []},
    ]
    offered = offered or features()
    return [row for row in rows if (row["command"] != "/literature" or offered["literature"])
            and (row["command"] != "/process" or offered["tea"])]


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
    """One CliApp per session, as the CLI would hold it; a lock keeps one turn per session at a time. With a database
    each session belongs to one account, and another account's session is "no such session"."""

    def __init__(self, home: str | Path | None = None, db: web_accounts.Database | None = None):
        self.home = home
        self.db = db
        self.apps: dict[str, cli.CliApp] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.panels: dict[str, web_tea.Panel] = {}
        self.owners: dict[str, str] = {}
        self.used: dict[str, float] = {}  # least recently used first
        self.guard = threading.Lock()

    def root(self) -> Path:
        return Path(self.home or os.getenv("DISSOLVE_HOME") or Path.home() / ".dissolve").expanduser() / "sessions"

    def open(self, session_id: str, *, user: str | None = None, create: bool = False, model: str | None = None,
             mode: str | None = None):
        if not cli._SESSION_RE.fullmatch(session_id):
            raise HTTPException(404, "no such session")
        owner = (user or "").casefold()
        with self.guard:
            if session_id in self.apps:
                if self.db is not None and self.owners[session_id] != owner:
                    raise HTTPException(404, "no such session")
                self.used.pop(session_id, None)
                self.used[session_id] = time.monotonic()
                return self.apps[session_id], self.locks[session_id]
            store = None
            if self.db is not None:
                kept = web_accounts.session_owner(self.db, session_id)
                if (kept is None and not create) or (kept is not None and kept != owner):
                    raise HTTPException(404, "no such session")
                store = web_accounts.DbStore(self.db, session_id, owner)
            elif not create and not (self.root() / session_id / "session.json").is_file():
                raise HTTPException(404, "no such session")
            self._make_room()
            console = Console(file=io.StringIO(), width=100, color_system=None, highlight=False, soft_wrap=True)
            try:
                self.apps[session_id] = cli.CliApp(
                    session_id=session_id, model_alias=model, mode=mode, store_root=self.home,
                    console=console, quiet=True, require_key=False, store=store,
                )
            except ValueError as error:
                raise HTTPException(400, str(error)) from error
            self.locks[session_id] = threading.Lock()
            self.panels.setdefault(session_id, web_tea.Panel())
            self.owners[session_id] = owner
            self.used[session_id] = time.monotonic()
            return self.apps[session_id], self.locks[session_id]

    def _make_room(self) -> None:
        """Chats past _OPEN_CHATS leave memory, least recently used first (called with the guard held). Each CliApp
        holds every message and result of its chat, and one per chat opened since the server started grew without
        bound. A chat with an answer running, a TEA panel waiting or running, or use in the last _HELD_S stays. One that
        left is read back from its store when it is opened again; its finished TEA table stays for the last
        _KEPT_RESULTS such chats."""
        now = time.monotonic()
        for session_id, used in list(self.used.items()):
            if len(self.apps) < _OPEN_CHATS or now - used < _HELD_S:
                break
            lock = self.locks[session_id]
            if not lock.acquire(blocking=False):
                continue
            try:
                panel = self.panels.get(session_id)
                if panel is not None and (panel.sheet is not None or panel.progress is not None):
                    continue
                for table in (self.apps, self.locks, self.owners, self.used):
                    table.pop(session_id, None)
                if panel is not None:
                    self.panels.pop(session_id)
                    if panel.result is not None:
                        self.panels[session_id] = panel  # the newest of the kept tables
            finally:
                lock.release()
        left = [session_id for session_id in self.panels if session_id not in self.apps]
        for session_id in left[:max(0, len(left) - _KEPT_RESULTS)]:
            del self.panels[session_id]

    def delete(self, session_id: str, *, user: str | None = None) -> None:
        """Remove a chat: its database rows, or its folder when there is no database. A chat another account owns is
        "no such session", and one with a turn running cannot go until the turn ends."""
        if not cli._SESSION_RE.fullmatch(session_id):
            raise HTTPException(404, "no such session")
        owner = (user or "").casefold()
        with self.guard:
            lock = self.locks.get(session_id)
            if lock is not None and self.db is not None and self.owners.get(session_id) != owner:
                raise HTTPException(404, "no such session")
            if lock is not None and not lock.acquire(blocking=False):
                raise HTTPException(409, "An answer is still running in this chat; delete it when it has finished.")
            try:
                if self.db is not None:
                    if not web_accounts.delete_session(self.db, session_id, owner):
                        raise HTTPException(404, "no such session")
                else:
                    folder = self.root() / session_id
                    if not (folder / "session.json").is_file():
                        raise HTTPException(404, "no such session")
                    shutil.rmtree(folder)
                for table in (self.apps, self.locks, self.panels, self.owners, self.used):
                    table.pop(session_id, None)
            finally:
                if lock is not None:
                    lock.release()

    def listing(self, limit: int = 50, user: str | None = None) -> list[dict[str, Any]]:
        if self.db is not None:
            return web_accounts.list_sessions(self.db, user or "", limit)
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


class TurnSlots:
    """At most `limit` answers run at once (DISSOLVE_MAX_TURNS; 0 is no limit), so answers that a burst of people start
    together cannot outgrow the host; the rest wait for a place, and their stream says so every KEEPALIVE_SECONDS. A
    TEA panel gives its place back while it waits for the person and while its plants run (web_tea.Turn.idle): the
    TEA worker holds that memory, under its own limit."""

    def __init__(self, limit: int):
        self.places = threading.BoundedSemaphore(limit) if limit > 0 else None
        self.most = limit * _IN_FLIGHT_PER_PLACE  # answers in progress at all, each holding its chat in memory
        self.in_flight = 0
        self.guard = threading.Lock()

    def enter(self) -> bool:
        """Count an answer in; False when _IN_FLIGHT_PER_PLACE answers per place are already in progress."""
        with self.guard:
            if self.places is not None and self.in_flight >= self.most:
                return False
            self.in_flight += 1
            return True

    def leave(self) -> None:
        with self.guard:
            self.in_flight -= 1

    def take(self, send: Callable[[dict[str, Any]], None]) -> None:
        if self.places is None or self.places.acquire(blocking=False):
            return
        send({"event": "turn.queued"})
        self.places.acquire()

    def give(self) -> None:
        if self.places is not None:
            self.places.release()

    @contextmanager
    def idle(self, send: Callable[[dict[str, Any]], None]) -> Iterator[None]:
        self.give()
        try:
            yield
        finally:
            self.take(send)


def cap_duckdb(limit: str) -> None:
    """Every DuckDB database this process opens gets `limit` (DISSOLVE_DUCKDB_MEMORY_LIMIT, e.g. 256MB), one thread,
    and a temporary directory it can spill to. By default DuckDB lets one database take 80 % of the host's memory and
    a thread per host CPU, and the hosted site budgets its 2 GB. Every connect call in the package goes through
    duckdb.connect, so every connection to a file shares one configuration, as DuckDB requires."""
    import duckdb

    original = getattr(duckdb.connect, "__wrapped__", duckdb.connect)
    caps = {"memory_limit": limit, "threads": 1, "temp_directory": str(Path(tempfile.gettempdir()) / "dissolve-duckdb")}

    def connect(database: Any = ":memory:", read_only: bool = False, config: Any = None, **kwargs: Any) -> Any:
        return original(database, read_only=read_only, config={**(config or {}), **caps}, **kwargs)

    connect.__wrapped__ = original  # type: ignore[attr-defined]
    duckdb.connect = connect


class BodyLimit:
    """Requests with a body past `limit` bytes are refused (413) before they are read: the app reads a body whole into
    memory, and nothing else bounded its size."""

    def __init__(self, app: Any, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        detail = f"A request can carry at most {self.limit // 2**20} MB."
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.limit:
            return await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)
        seen = 0

        async def counted() -> dict[str, Any]:  # a body sent in chunks, without a length
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b"")) if message["type"] == "http.request" else 0
            if seen > self.limit:
                raise HTTPException(413, detail)  # FastAPI answers any other error while reading a body with a 400
            return message

        await self.app(scope, counted, send)


def _events(app: cli.CliApp) -> list[dict[str, Any]]:
    """The session's recorded events, from the database store or the transcript file."""
    if hasattr(app.store, "events"):
        return app.store.events()
    path = app.store.transcript_path
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def transcript(app: cli.CliApp) -> list[dict[str, Any]]:
    """The session's turns: each user message, then the answer with the tool calls it made."""
    out: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for row in _events(app):
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


def _run(app: cli.CliApp, text: str, events: "queue.Queue[dict[str, Any] | None]", offered: dict[str, bool],
         panel: web_tea.Panel | None = None, idle: Callable[[], ContextManager[None]] | None = None) -> None:
    started = time.monotonic()
    events.put({"event": "turn.started", "session_id": app.store.session_id, "text": text})
    try:
        if text.startswith("/"):
            command, *rest = text.split()
            command = command.casefold()
            if command == "/process" and offered["tea"] and panel is not None:  # the TEA sheet button
                output = web_tea.process_sheet(web_tea.Turn(panel, app, events.put, idle=idle or nullcontext))
            elif command in _CLI_ONLY:
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
        turn = web_tea.TURN.set(web_tea.Turn(panel, app, events.put, idle=idle or nullcontext)
                                if panel is not None and offered["tea"] else None)
        try:
            app.ask(text)
        finally:
            web_tea.TURN.reset(turn)
            app.event_sink = None
            del app._print_tool_event
    except (RuntimeError, ValueError) as error:
        events.put({"event": "error", "message": str(error)})
    except Exception as error:  # the stream must end with a reason, whatever failed
        events.put({"event": "error", "message": f"{type(error).__name__}: {error}"})
    finally:
        events.put(None)


_DRAWING_SIZES = {"small": (180, 120), "large": (640, 440)}  # a table thumbnail; the view a click opens


@lru_cache(maxsize=1024)  # at most about 30 MB: a drawing is 2 to 28 kB
def depict(smiles: str, size: str = "large") -> str | None:
    """An RDKit drawing of a molecule as SVG, on a white card that reads in either theme; None when RDKit cannot read
    the SMILES. Each size is drawn for itself: one drawing shrunk into a table cell left its bonds hair-thin."""
    if not smiles or len(smiles) > 600 or size not in _DRAWING_SIZES:
        return None
    from rdkit import Chem, RDLogger
    from rdkit.Chem.Draw import rdMolDraw2D

    RDLogger.DisableLog("rdApp.*")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    drawer = rdMolDraw2D.MolDraw2DSVG(*_DRAWING_SIZES[size])
    options = drawer.drawOptions()
    options.clearBackground = True
    options.padding = 0.06
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def _basic(request: Request) -> tuple[str, str] | None:
    """HTTP Basic credentials, for scripts that call the API without a browser's sign-in cookie."""
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        name, _, secret = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    return name, secret


def create_app(home: str | Path | None = None) -> FastAPI:
    api = FastAPI(title="DISSOLVE", version=RELEASE, docs_url="/api/docs", openapi_url="/api/openapi.json")
    api.add_middleware(BodyLimit, limit=_MAX_BODY)
    database = os.getenv("DATABASE_URL", "").strip()
    db = web_accounts.Database(database) if database else None
    accounts = web_accounts.Accounts(db) if db is not None else None
    sessions = api.state.sessions = Sessions(home, db)
    doctor_cache: dict[str, Any] = {}
    family_cache: dict[str, Any] = {}
    failures: dict[tuple[str, str], list[float]] = {}
    api.state.failures = failures
    offered = features()
    slots = api.state.slots = TurnSlots(int(os.getenv("DISSOLVE_MAX_TURNS") or 0))
    if limit := os.getenv("DISSOLVE_DUCKDB_MEMORY_LIMIT", "").strip():
        cap_duckdb(limit)
    signup_codes = _signup_codes()
    admin_key = web_admin.admin_token() if accounts is not None else None
    admin = web_admin.AdminView(db, sessions) if admin_key else None
    admin_events = web_admin.AdminEvents() if admin is not None else None
    if offered["tea"]:
        web_tea.install()  # a plant TEA in a review-mode chat waits for the person in the TEA panel

    if accounts is not None:
        @api.middleware("http")
        async def require_account(request: Request, call_next):
            path = request.url.path
            if not path.startswith("/api/") or path == "/api/health" or path.startswith(("/api/auth/", "/api/admin/")):
                return await call_next(request)  # the UI, signing in, and the admin view (which checks its own token)
            user = await run_in_threadpool(accounts.user_for, request.cookies.get(COOKIE))
            if user is None and (basic := _basic(request)):
                user = await run_in_threadpool(accounts.check, *basic)
            if user is None:
                return JSONResponse({"detail": "Sign in to use DISSOLVE."}, status_code=401)
            request.state.user = user
            return await call_next(request)

    elif password := os.getenv("DISSOLVE_WEB_PASSWORD"):
        expected = ("Basic " + base64.b64encode(f"{os.getenv('DISSOLVE_WEB_USER', 'dissolve')}:{password}".encode()).decode()).encode()

        @api.middleware("http")
        async def require_password(request: Request, call_next):
            if request.url.path == "/api/health" or secrets.compare_digest(request.headers.get("authorization", "").encode(), expected):
                return await call_next(request)
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="DISSOLVE"'})

    def user_of(request: Request) -> str | None:
        return getattr(request.state, "user", None)

    def signed_in(response: Response, request: Request, name: str) -> dict[str, Any]:
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(COOKIE, accounts.sign_in(name), max_age=web_accounts.LOGIN_DAYS * 86400, httponly=True,
                            samesite="lax", secure=secure, path="/")
        return accounts.profile(name)

    def throttled(request: Request, username: str) -> tuple[str, str]:
        key = (request.client.host if request.client else "", username.strip().casefold())
        now = time.monotonic()
        if len(failures) > 1000:  # every username tried leaves a key; the ones without a recent failure go
            for stale in [k for k, times in failures.items() if not times or now - times[-1] >= _ATTEMPT_WINDOW_S]:
                del failures[stale]
        recent = [t for t in failures.get(key, []) if now - t < _ATTEMPT_WINDOW_S]
        failures[key] = recent
        if len(recent) >= _ATTEMPTS:
            raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
        return key

    @api.get("/api/auth/config")
    def auth_config() -> dict[str, Any]:
        return {"accounts": accounts is not None, "access_code": accounts is not None and bool(signup_codes),
                "admin_reads": admin is not None}

    @api.post("/api/auth/signup")
    def signup(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
        if accounts is None:
            raise HTTPException(404, "This server has no accounts.")
        key = throttled(request, "signup")
        if signup_codes and not _code_accepted(body.access_code, signup_codes):
            failures[key].append(time.monotonic())
            raise HTTPException(403, "That access code is not right. Ask whoever gave you this site for it.")
        try:
            name = accounts.create(body.username, body.password)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except LookupError as error:
            raise HTTPException(409, str(error)) from error
        return signed_in(response, request, name)

    @api.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
        if accounts is None:
            raise HTTPException(404, "This server has no accounts.")
        key = throttled(request, body.username)
        name = accounts.check(body.username, body.password)
        if name is None:
            failures[key].append(time.monotonic())
            raise HTTPException(401, "That username and password do not match an account.")
        return signed_in(response, request, name)

    @api.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        if accounts is not None:
            accounts.sign_out(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @api.get("/api/auth/me")
    def me(request: Request) -> dict[str, Any]:
        if accounts is None:
            return {"username": None}
        name = accounts.user_for(request.cookies.get(COOKIE))
        if name is None and (basic := _basic(request)):
            name = accounts.check(*basic)
        if name is None:
            raise HTTPException(401, "Sign in to use DISSOLVE.")
        return accounts.profile(name)

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

    @api.get("/api/contaminant-families")
    def contaminant_families() -> list[dict[str, Any]]:
        if "families" not in family_cache:  # read once: the assets do not change while the server runs
            family_cache["families"] = contaminants.contaminant_families()
        return family_cache["families"]

    @api.get("/api/structure.svg", include_in_schema=False)
    def structure(smiles: str = "", size: str = "large") -> Response:
        svg = depict(smiles.strip(), size)
        if svg is None:
            raise HTTPException(422, "RDKit cannot draw that SMILES.")
        return Response(svg, media_type="image/svg+xml", headers={
            "Cache-Control": "private, max-age=604800",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",  # an image, never a page
        })

    @api.get("/api/sessions")
    def session_list(request: Request) -> list[dict[str, Any]]:
        return sessions.listing(user=user_of(request))

    @api.post("/api/sessions")
    def session_new(body: NewSession, request: Request) -> dict[str, Any]:
        if body.model is not None and cli.MODEL_ALIASES.get(body.model, body.model) not in cli.MODELS:
            raise HTTPException(400, f"Unknown model alias: {body.model}")
        app, _lock = sessions.open(uuid.uuid4().hex[:12], user=user_of(request), create=True, model=body.model,
                                   mode=body.mode)
        return _state(app)

    @api.get("/api/sessions/{session_id}")
    def session_get(session_id: str, request: Request) -> dict[str, Any]:
        app, _lock = sessions.open(session_id, user=user_of(request))
        return {**_state(app), "messages": transcript(app)}

    @api.delete("/api/sessions/{session_id}")
    def session_delete(session_id: str, request: Request) -> dict[str, Any]:
        sessions.delete(session_id, user=user_of(request))
        return {"deleted": session_id}

    @api.post("/api/sessions/{session_id}/turns")
    def session_turn(session_id: str, body: Turn, request: Request) -> StreamingResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty message")
        app, lock = sessions.open(session_id, user=user_of(request))
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "a turn is already running in this session")
        if not slots.enter():
            lock.release()
            raise HTTPException(503, "DISSOLVE is answering as many questions as it can hold right now. Try again in a "
                                     "minute.")
        events: queue.Queue[dict[str, Any] | None] = queue.Queue()

        panel = sessions.panels.get(session_id)
        sink = events if admin_events is None else web_admin.TurnTee(
            events, admin_events, session_id, user_of(request) or "local", text)

        def work() -> None:
            try:
                slots.take(sink.put)
                try:
                    _run(app, text, sink, offered, panel, lambda: slots.idle(sink.put))
                finally:
                    slots.give()
            finally:
                slots.leave()
                lock.release()

        threading.Thread(target=work, daemon=True, name=f"dissolve-turn-{session_id}").start()

        def stream() -> Iterator[str]:
            """The turn's events. A quiet stream (a long tool, a place or the TEA worker still busy) carries a
            keepalive every KEEPALIVE_SECONDS, which the browser ignores, so no proxy closes it as idle."""
            while True:
                try:
                    event = events.get(timeout=web_tea.KEEPALIVE_SECONDS)
                except queue.Empty:
                    yield '{"event": "keepalive"}\n'
                    continue
                if event is None:
                    return
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    def panel_of(session_id: str, request: Request) -> web_tea.Panel:
        sessions.open(session_id, user=user_of(request))  # another account's chat is "no such session"
        return sessions.panels[session_id]

    @api.get("/api/sessions/{session_id}/tea-sheet")
    def tea_sheet(session_id: str, request: Request) -> dict[str, Any]:
        return panel_of(session_id, request).state()

    @api.post("/api/sessions/{session_id}/tea-sheet/check")
    def tea_sheet_check(session_id: str, body: SheetAnswer, request: Request) -> dict[str, Any]:
        try:
            return panel_of(session_id, request).check(body.sheet_id, body.values, body.ranges, body.drop)
        except web_tea.Conflict as error:
            raise HTTPException(409, str(error)) from error

    @api.post("/api/sessions/{session_id}/tea-sheet")
    def tea_sheet_answer(session_id: str, body: SheetAnswer, request: Request) -> dict[str, Any]:
        try:
            return panel_of(session_id, request).respond(body.sheet_id, body.action, body.values, body.ranges,
                                                         body.drop)
        except web_tea.Conflict as error:
            raise HTTPException(409, str(error)) from error
        except web_tea.SheetError as error:
            raise HTTPException(422, {"message": str(error), **error.detail}) from error

    @api.post("/api/sessions/{session_id}/reports")
    def session_report(session_id: str, body: ReportBody, request: Request) -> dict[str, Any]:
        if not body.question.strip():
            raise HTTPException(400, "Say which question the report is about.")
        app, _lock = sessions.open(session_id, user=user_of(request))  # only the chat's owner reports on it
        report = web_admin.save_report(db, app, user_of(request) or "local", body.question, body.note)
        print(f"report: {report['report_id']} on chat {session_id}", flush=True)
        if admin_events is not None:
            admin_events.publish("report", session_id=session_id, owner=report["owner"], report_id=report["report_id"],
                                 note=report["note"], question=report["question"][:300])
        return {"report_id": report["report_id"], "saved": True}

    def require_admin(request: Request) -> web_admin.AdminView:
        if admin is None:
            raise HTTPException(404, "no such endpoint")
        key = throttled(request, "admin")
        if not web_admin.token_matches(request.headers.get("authorization", ""), admin_key):
            failures[key].append(time.monotonic())
            print(f"admin: refused {request.method} {request.url.path} from {key[0] or 'unknown'}", flush=True)
            raise HTTPException(401, "admin token required")
        query = f"?{request.url.query}" if request.url.query else ""
        print(f"admin: {request.method} {request.url.path}{query}", flush=True)
        return admin

    def bounded(limit: int) -> int:
        return max(1, min(int(limit), 500))

    @api.get("/api/admin/accounts")
    def admin_accounts(request: Request) -> list[dict[str, Any]]:
        return require_admin(request).accounts()

    @api.get("/api/admin/chats")
    def admin_chats(request: Request, owner: str = "", since: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return require_admin(request).chats(owner or None, web_admin.since_cutoff(since), bounded(limit))

    @api.get("/api/admin/chats/{session_id}")
    def admin_chat(session_id: str, request: Request, full: bool = False) -> dict[str, Any]:
        chat = require_admin(request).chat(session_id, full)
        if chat is None:
            raise HTTPException(404, "no such session")
        return chat

    @api.get("/api/admin/turns")
    def admin_turns(request: Request, owner: str = "", since: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return require_admin(request).recent_turns(owner or None, web_admin.since_cutoff(since), bounded(limit))

    @api.get("/api/admin/live")
    def admin_live(request: Request) -> list[dict[str, Any]]:
        return require_admin(request).live()

    @api.get("/api/admin/reports")
    def admin_reports(request: Request, since: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return require_admin(request).reports(web_admin.since_cutoff(since), bounded(limit))

    @api.get("/api/admin/events")
    async def admin_event_stream(request: Request, after: int = 0, follow: bool = True,
                                 boot: str = "") -> StreamingResponse:
        """Questions, answers and reports as they happen. A listener that reconnects sends Last-Event-ID and the boot
        it saw; after a restart the ids start again, so a stale boot gets everything this boot still holds."""
        require_admin(request)
        last = request.headers.get("last-event-id", "").strip()
        start = int(last) if last.isdigit() else max(0, after)
        if boot and boot != admin_events.boot:
            start = 0
        return StreamingResponse(admin_events.stream(start, request, follow), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
