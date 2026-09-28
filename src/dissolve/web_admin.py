"""A read-only admin view of the hosted app, so that problems are diagnosed from what was actually asked and answered
instead of from screenshots: every account's chats, each turn (the question, every tool call, the answer), the turns
running now, and the problems people report from an answer.

    GET  /api/admin/accounts                         usernames, when each joined, chats, turns, last activity
    GET  /api/admin/chats?owner=&since=&limit=       chats across accounts, newest first, with a running flag
    GET  /api/admin/chats/{id}?full=1                one chat's turns; full=1 adds each tool call's whole result
    GET  /api/admin/turns?owner=&since=&limit=       recent turns across accounts, newest first, for auditing
    GET  /api/admin/live                             turns running now, with the tool calls made so far
    GET  /api/admin/reports?since=&limit=            problems people reported, each with the turn it is about
    POST /api/sessions/{id}/reports {question, note}  a person reports a problem with one of their own answers

The admin routes need DISSOLVE_ADMIN_TOKEN (at least 32 characters) on a server with an accounts database, sent as
`Authorization: Bearer <token>`. Without both they are "no such endpoint". Nothing here reads a password hash or a
sign-in token, the token cannot act as any account, and the only write is a person's report on their own chat. Each
admin request prints one line (method, path, query; never content) to the server log, and repeated wrong tokens from
one address are paused like wrong passwords.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dissolve import web_accounts

MIN_TOKEN = 32
MAX_NOTE = 2000


def admin_token() -> str | None:
    """The configured admin token, or None when the admin view is off (unset, or too short to be safe)."""
    token = os.getenv("DISSOLVE_ADMIN_TOKEN", "").strip()
    if not token:
        return None
    if len(token) < MIN_TOKEN:
        print(f"admin: DISSOLVE_ADMIN_TOKEN is shorter than {MIN_TOKEN} characters; the admin view stays off",
              flush=True)
        return None
    return token


def token_matches(header: str, token: str) -> bool:
    given = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return bool(given) and secrets.compare_digest(given.encode(), token.encode())


def since_cutoff(value: str | None) -> str | None:
    """An ISO timestamp from `since`: an ISO time as given, or a span back from now such as 30m, 6h, 2d."""
    text = (value or "").strip()
    if not text:
        return None
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if text[:-1].isdigit() and text[-1:] in units:
        span = timedelta(**{units[text[-1]]: int(text[:-1])})
        return (datetime.now(timezone.utc) - span).isoformat()
    return text


def _json(text: Any) -> dict[str, Any]:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _seconds(start: str | None, end: str | None) -> float | None:
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def tool_call(event: dict[str, Any], full: bool = False) -> dict[str, Any]:
    """One recorded tool call as the admin reads it: what the model sent, and whether and how the tool answered."""
    from dissolve.agent import ToolEvent
    from dissolve.web import _tool

    result = _json(event.get("content"))
    shown = _tool(ToolEvent(name=event.get("name") or "", args=event.get("args") or {}, result=result))
    call = {
        "at": event.get("timestamp"), "name": event.get("name"), "args": event.get("args") or {},
        "ok": shown["ok"], "source_basis": shown["source_basis"], "refusal": shown["error_code"],
        "error": shown["error"],
    }
    if isinstance(result.get("handle"), str):
        call["handle"], call["total"] = result["handle"], result.get("total")
    if full:
        call["result"] = result
    return call


def turns(events: list[dict[str, Any]], full: bool = False) -> list[dict[str, Any]]:
    """A chat's events as turns: the question, each tool call in order, the answer and its status. A turn with no
    answer yet is still running (or stopped before one)."""
    out: list[dict[str, Any]] = []
    for event in events:
        role = event.get("role")
        if role == "user":
            out.append({
                "turn": len(out) + 1, "asked_at": event.get("timestamp"), "question": event.get("content") or "",
                "mode": event.get("mode"), "model": event.get("model"), "tools": [], "answer": None, "status": None,
                "answered_at": None, "elapsed_s": None,
            })
        elif role == "tool" and out:
            out[-1]["tools"].append(tool_call(event, full))
        elif role == "assistant" and out:
            turn = out[-1]
            turn.update(answer=event.get("content") or "", status=event.get("status"),
                        answered_at=event.get("timestamp"))
            turn["elapsed_s"] = _seconds(turn["asked_at"], turn["answered_at"])
    return out


def _summary(turn: dict[str, Any], owner: str | None, session_id: str) -> dict[str, Any]:
    """A turn in a list: enough to judge it at a glance, with the chat to open for the rest."""
    refused = [call["name"] for call in turn["tools"] if not call["ok"]]
    return {
        "session_id": session_id, "owner": owner, "turn": turn["turn"], "asked_at": turn["asked_at"],
        "question": turn["question"], "status": turn["status"] or "running", "elapsed_s": turn["elapsed_s"],
        "tool_calls": len(turn["tools"]), "refused": refused, "model": turn["model"],
        "answer": turn["answer"],
    }


class AdminView:
    """The admin queries over the accounts database and the server's own running turns."""

    def __init__(self, db: web_accounts.Database, sessions: Any):
        self.db = db
        self.sessions = sessions

    def _events(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.db.run("SELECT event FROM transcript WHERE session_id = ? ORDER BY seq", (session_id,),
                           fetch="all")
        return [_json(event) for (event,) in rows]

    def _first_question(self, session_id: str) -> str | None:
        """The title of a chat whose first turn is still running: its row has none until that turn ends."""
        asked = next((e for e in self._events(session_id) if e.get("role") == "user"), {})
        return str(asked.get("content") or "")[:120] or None

    def _running(self) -> dict[str, str]:
        """Chats with a turn running on this server now, and whose they are."""
        with self.sessions.guard:
            return {sid: self.sessions.owners.get(sid, "") for sid, lock in self.sessions.locks.items()
                    if lock.locked()}

    def accounts(self) -> list[dict[str, Any]]:
        users = self.db.run("SELECT username, display, created_at FROM users ORDER BY created_at", fetch="all")
        counts = {owner: (chats, total, last) for owner, chats, total, last in self.db.run(
            "SELECT owner, COUNT(*), COALESCE(SUM(turns), 0), MAX(updated_at) FROM sessions GROUP BY owner",
            fetch="all")}
        return [{"username": display, "joined": created, "chats": counts.get(name, (0, 0, None))[0],
                 "turns": counts.get(name, (0, 0, None))[1], "last_activity": counts.get(name, (0, 0, None))[2]}
                for name, display, created in users]

    def chats(self, owner: str | None = None, since: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql, params = "SELECT session_id, owner, title, turns, model, updated_at FROM sessions WHERE 1 = 1", []
        if owner:
            sql += " AND owner = ?"
            params.append(owner.casefold())
        if since:
            sql += " AND updated_at >= ?"
            params.append(since)
        rows = self.db.run(sql + " ORDER BY updated_at DESC LIMIT ?", (*params, limit), fetch="all")
        running = self._running()
        out = [{"session_id": sid, "owner": who, "title": title or self._first_question(sid), "turns": count,
                "model": model, "updated_at": updated, "running": sid in running}
               for sid, who, title, count, model, updated in rows]
        known = {row["session_id"] for row in out}
        for sid, who in running.items():  # a new chat's first turn: its row is saved when the turn ends
            if sid not in known and (not owner or who == owner.casefold()):
                asked = next((e for e in self._events(sid) if e.get("role") == "user"), {})
                out.insert(0, {"session_id": sid, "owner": who, "title": self._first_question(sid), "turns": 0,
                               "model": asked.get("model"), "updated_at": asked.get("timestamp"), "running": True})
        return out

    def chat(self, session_id: str, full: bool = False) -> dict[str, Any] | None:
        row = self.db.run("SELECT owner, title, turns, model, updated_at FROM sessions WHERE session_id = ?",
                          (session_id,), fetch="one")
        events = self._events(session_id)
        running = self._running()
        if row is None and not events:
            return None
        owner = row[0] if row else running.get(session_id) or self.sessions.owners.get(session_id)
        return {"session_id": session_id, "owner": owner, "title": row[1] if row else None,
                "model": row[3] if row else None, "updated_at": row[4] if row else None,
                "running": session_id in running, "turns": turns(events, full)}

    def recent_turns(self, owner: str | None = None, since: str | None = None, limit: int = 50) -> list[dict]:
        out = []
        for chat in self.chats(owner=owner, since=since, limit=max(limit, 50)):
            for turn in turns(self._events(chat["session_id"])):
                if not since or str(turn["asked_at"] or "") >= since:
                    out.append(_summary(turn, chat["owner"], chat["session_id"]))
        out.sort(key=lambda turn: str(turn["asked_at"] or ""), reverse=True)
        return out[:limit]

    def live(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        out = []
        for sid, who in self._running().items():
            chat_turns = turns(self._events(sid))
            if chat_turns and chat_turns[-1]["answer"] is None:
                turn = chat_turns[-1]
                out.append({**_summary(turn, who, sid), "running_s": _seconds(turn["asked_at"], now),
                            "tools": turn["tools"]})
        return out

    def reports(self, since: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql, params = "SELECT report_id, session_id, owner, created_at, note, question, question_seq FROM reports", []
        if since:
            sql += " WHERE created_at >= ?"
            params.append(since)
        rows = self.db.run(sql + " ORDER BY created_at DESC LIMIT ?", (*params, limit), fetch="all")
        out = []
        for report_id, sid, who, created, note, question, seq in rows:
            turn = None
            if seq is not None:
                events = self.db.run("SELECT event FROM transcript WHERE session_id = ? AND seq >= ? ORDER BY seq",
                                     (sid, seq), fetch="all")
                found = turns([_json(event) for (event,) in events])
                turn = found[0] if found else None
            out.append({"report_id": report_id, "created_at": created, "owner": who, "session_id": sid, "note": note,
                        "question": question, "turn": turn})
        return out


def save_report(db: web_accounts.Database | None, app: Any, owner: str, question: str, note: str) -> dict[str, Any]:
    """A person's report on one of their answers: which question it is about, and what they say went wrong. With a
    database it is a row the admin view reads; a local server keeps it next to the chat's transcript."""
    question, note = (question or "").strip(), (note or "").strip()[:MAX_NOTE]
    report = {"report_id": uuid.uuid4().hex[:12], "session_id": app.store.session_id, "owner": owner.casefold(),
              "created_at": web_accounts._now(), "note": note, "question": question[:4000]}
    if db is None:
        with Path(app.store.transcript_path).with_name("reports.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False) + "\n")
        return report
    seq = None
    for row_seq, raw in db.run("SELECT seq, event FROM transcript WHERE session_id = ? ORDER BY seq",
                               (report["session_id"],), fetch="all"):
        event = _json(raw)
        if event.get("role") == "user" and str(event.get("content") or "").strip() == question:
            seq = row_seq  # the latest asking of that question
    db.run("INSERT INTO reports (report_id, session_id, owner, created_at, note, question, question_seq) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)",
           (report["report_id"], report["session_id"], report["owner"], report["created_at"], note,
            report["question"], seq))
    return {**report, "question_seq": seq}
