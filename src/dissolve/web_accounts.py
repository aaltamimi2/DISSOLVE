"""Accounts and chats for the hosted web app, kept in a database so that a redeploy loses neither.

DATABASE_URL names the database: postgresql://... on the host (DigitalOcean attaches it to the app), sqlite:///path
for local trials and the tests. Without one, `dissolve web` keeps session files and at most one site password.

A person signs up with a username and a password, nothing else. A sign-in sets an HttpOnly cookie, and scripts may send
the same username and password as HTTP Basic credentials instead. A chat belongs to the account that started it: the
list shows only your own, and another account's chat is "no such session". Accounts and chats stay until someone
deletes them on purpose: its owner, from the chat list, or whoever runs the database.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Any

from dissolve import RELEASE
from dissolve.cli import _is_v12_session
from dissolve.contracts import normalize_json

USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,31}")
MIN_PASSWORD = 8
LOGIN_DAYS = 30
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, display TEXT NOT NULL, password_hash TEXT NOT NULL, "
    "created_at TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS logins (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, created_at TEXT NOT NULL, "
    "expires_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, "
    "title TEXT, turns INTEGER NOT NULL DEFAULT 0, model TEXT, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS sessions_by_owner ON sessions (owner, updated_at)",
    "CREATE TABLE IF NOT EXISTS transcript (session_id TEXT NOT NULL, seq INTEGER NOT NULL, event TEXT NOT NULL, "
    "PRIMARY KEY (session_id, seq))",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """One connection per call, which survives a database restart: sqlite3 for sqlite:///path, psycopg for
    postgresql:// (only the hosted image installs it). Queries are written with ? placeholders."""

    def __init__(self, url: str):
        self.url = url.strip()
        self.sqlite = self.url.startswith("sqlite:///")
        if not self.sqlite and not self.url.startswith(("postgres://", "postgresql://")):
            raise ValueError("DATABASE_URL must be postgresql://... or sqlite:///path")
        self._writer = threading.Lock()  # sqlite allows one writer; its callers wait here instead of erroring
        for statement in SCHEMA:
            self.run(statement)

    def _connect(self) -> Any:
        if self.sqlite:
            return sqlite3.connect(self.url.removeprefix("sqlite:///"), timeout=30)
        import psycopg

        return psycopg.connect(self.url)

    def run(self, sql: str, params: tuple = (), fetch: str | None = None) -> Any:
        with self._writer if self.sqlite else nullcontext():
            connection = self._connect()
            try:
                cursor = connection.cursor()
                cursor.execute(sql if self.sqlite else sql.replace("?", "%s"), params)
                rows = cursor.fetchall() if fetch == "all" else cursor.fetchone() if fetch == "one" else None
                connection.commit()
                return rows
            finally:
                connection.close()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


_UNKNOWN_USER = hash_password(secrets.token_hex(8))  # checked for a name that does not exist, so both take as long


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Accounts:
    """Usernames, password hashes and sign-in tokens. A username is its own key, compared without case."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, username: str, password: str) -> str:
        name = (username or "").strip()
        if not USERNAME.fullmatch(name):
            raise ValueError("A username is 3 to 32 letters, digits, dots, hyphens or underscores, starting with a "
                             "letter or digit.")
        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"A password needs at least {MIN_PASSWORD} characters.")
        if self.db.run("SELECT 1 FROM users WHERE username = ?", (name.casefold(),), fetch="one"):
            raise LookupError("That username is taken.")
        self.db.run("INSERT INTO users (username, display, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (name.casefold(), name, hash_password(password), _now()))
        return name

    def check(self, username: str, password: str) -> str | None:
        """The account's display name when the password is right, else None."""
        row = self.db.run("SELECT display, password_hash FROM users WHERE username = ?",
                          ((username or "").strip().casefold(),), fetch="one")
        if row is None:
            verify_password(password or "", _UNKNOWN_USER)
            return None
        return row[0] if verify_password(password or "", row[1]) else None

    def sign_in(self, username: str) -> str:
        """A new sign-in token for the account; the cookie carries it and the database keeps only its hash."""
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=LOGIN_DAYS)
        self.db.run("DELETE FROM logins WHERE expires_at < ?", (_now(),))
        self.db.run("INSERT INTO logins (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    (_token_hash(token), username.casefold(), _now(), expires.isoformat()))
        return token

    def user_for(self, token: str | None) -> str | None:
        if not token:
            return None
        row = self.db.run("SELECT u.display, l.expires_at FROM logins l JOIN users u ON u.username = l.username "
                          "WHERE l.token_hash = ?", (_token_hash(token),), fetch="one")
        return row[0] if row is not None and row[1] > _now() else None

    def sign_out(self, token: str | None) -> None:
        if token:
            self.db.run("DELETE FROM logins WHERE token_hash = ?", (_token_hash(token),))

    def profile(self, username: str) -> dict[str, Any]:
        row = self.db.run("SELECT display, created_at FROM users WHERE username = ?", (username.casefold(),),
                          fetch="one")
        return {"username": row[0], "created_at": row[1]} if row else {}


class DbStore:
    """CliApp's session store in the database: the state is one row owned by one account, and the transcript is its
    numbered events. The two paths only name them for messages; nothing is written to disk."""

    def __init__(self, db: Database, session_id: str, owner: str):
        self.db, self.session_id, self.owner = db, session_id, owner.casefold()
        self.state_path = f"database: sessions/{session_id}"
        self.transcript_path = f"database: transcript/{session_id}"

    def load(self) -> dict[str, Any] | None:
        row = self.db.run("SELECT state FROM sessions WHERE session_id = ? AND owner = ?",
                          (self.session_id, self.owner), fetch="one")
        if row is None:
            return None
        payload = json.loads(row[0])
        if not _is_v12_session(payload):
            raise ValueError("incompatible DISSOLVE session; not overwritten")
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        body = normalize_json({**payload, "schema_version": 2, "session_id": self.session_id, "release": RELEASE,
                               "updated_at": _now()})
        asked = [m.get("content") for m in body.get("messages") or [] if m.get("role") == "user"]
        self.db.run(
            "INSERT INTO sessions (session_id, owner, state, title, turns, model, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (session_id) DO UPDATE SET state = excluded.state, "
            "title = excluded.title, turns = excluded.turns, model = excluded.model, updated_at = excluded.updated_at "
            "WHERE sessions.owner = excluded.owner",
            (self.session_id, self.owner, json.dumps(body, ensure_ascii=False), str(asked[0])[:120] if asked else None,
             len(asked), (body.get("metadata") or {}).get("model"), body["updated_at"]),
        )

    def append(self, role: str, content: str, **metadata: Any) -> None:
        event = normalize_json({"timestamp": _now(), "role": role, "content": content, **metadata})
        self.db.run("INSERT INTO transcript (session_id, seq, event) SELECT ?, COALESCE(MAX(seq), 0) + 1, ? "
                    "FROM transcript WHERE session_id = ?",
                    (self.session_id, json.dumps(event, ensure_ascii=False), self.session_id))

    def events(self) -> list[dict[str, Any]]:
        rows = self.db.run("SELECT event FROM transcript WHERE session_id = ? ORDER BY seq", (self.session_id,),
                           fetch="all")
        return [json.loads(event) for (event,) in rows]


def session_owner(db: Database, session_id: str) -> str | None:
    row = db.run("SELECT owner FROM sessions WHERE session_id = ?", (session_id,), fetch="one")
    return row[0] if row else None


def delete_session(db: Database, session_id: str, owner: str) -> bool:
    """Remove one of the owner's chats with its transcript. Another account's chat is left alone (False)."""
    if session_owner(db, session_id) != owner.casefold():
        return False
    db.run("DELETE FROM transcript WHERE session_id = ?", (session_id,))
    db.run("DELETE FROM sessions WHERE session_id = ? AND owner = ?", (session_id, owner.casefold()))
    return True


def list_sessions(db: Database, owner: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.run("SELECT session_id, updated_at, title, turns, model FROM sessions WHERE owner = ? "
                  "ORDER BY updated_at DESC LIMIT ?", (owner.casefold(), limit), fetch="all")
    return [{"session_id": sid, "updated_at": updated, "title": title, "turns": turns, "model": model}
            for sid, updated, title, turns, model in rows]
