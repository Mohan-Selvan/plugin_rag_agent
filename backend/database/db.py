"""Persistent SQLite store for chat sessions and message history."""
from __future__ import annotations

import time
from pathlib import Path

import aiosqlite
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from ..utils import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content     TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_session_time ON messages(session_id, created_at);
"""


class SessionStore:
    """Async SQLite store: per-session chat history with insert/touch/recent ops."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._init = False

    async def initialise(self) -> None:
        """Create tables on first use (idempotent)."""
        if self._init: return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(_SCHEMA); await db.commit()
        self._init = True
        log.info("Session store ready at %s", self.path)

    async def touch(self, sid: str) -> None:
        """Insert the session row or bump its last_seen timestamp."""
        now = int(time.time())
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO sessions (session_id, created_at, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET last_seen=excluded.last_seen",
                (sid, now, now))
            await db.commit()

    async def append(self, sid: str, role: str, content: str) -> None:
        """Persist one message turn (role must be 'user' or 'assistant')."""
        if role not in ("user", "assistant"): raise ValueError(f"bad role {role!r}")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (sid, role, content, int(time.time())))
            await db.commit()

    async def recent(self, sid: str, limit: int) -> list[BaseMessage]:
        """Return up to `limit` recent message pairs as BaseMessage list, oldest first."""
        if limit <= 0: return []
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (sid, limit * 2)) as cur:
                rows = await cur.fetchall()
        rows.reverse()
        return [HumanMessage(content=c) if r == "user" else AIMessage(content=c) for r, c in rows]

    async def purge_expired(self, ttl_seconds: int) -> int:
        """Delete sessions inactive longer than ttl_seconds (messages cascade); returns count."""
        if ttl_seconds <= 0: return 0
        cutoff = int(time.time()) - ttl_seconds
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            cur = await db.execute("DELETE FROM sessions WHERE last_seen < ?", (cutoff,))
            await db.commit()
            return cur.rowcount or 0
