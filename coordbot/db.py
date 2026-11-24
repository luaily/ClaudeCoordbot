"""SQLite storage. Only trusted messages ever reach this module."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .locks import EventRow, Lock, derive_locks

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, author_id INTEGER, author_name TEXT, webhook_id INTEGER,
  content TEXT, embeds TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS events (
  msg_id INTEGER PRIMARY KEY, author_id INTEGER, agent TEXT, type TEXT, uid TEXT,
  files TEXT, text TEXT, ttl INTEGER, commit_sha TEXT, all_flag INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS locks (
  msg_id INTEGER, file TEXT, author_id INTEGER, agent TEXT, uid TEXT, reason TEXT,
  ts REAL, expires_ts REAL, released_by INTEGER);
CREATE TABLE IF NOT EXISTS commits (
  sha TEXT, branch TEXT, repo TEXT, message TEXT, author TEXT, url TEXT, ts REAL,
  msg_id INTEGER, raw TEXT, PRIMARY KEY (sha, branch));
CREATE TABLE IF NOT EXISTS own_actions (
  msg_id INTEGER PRIMARY KEY, type TEXT, summary TEXT, files TEXT, commit_sha TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS threads (
  root_msg_id INTEGER PRIMARY KEY, thread_id INTEGER, author_id INTEGER, ts REAL, closed INTEGER DEFAULT 0);
"""

# Columns added after the first release; added to older databases on open.
MIGRATIONS = [
    ("events", "kind", "TEXT"),
    ("events", "action", "TEXT"),
    ("events", "branch", "TEXT"),
    ("events", "channel_id", "INTEGER"),
    ("messages", "channel_id", "INTEGER"),
    ("messages", "thread_id", "INTEGER"),
]


@dataclass(frozen=True)
class CommitRow:
    sha: str
    branch: str
    repo: str
    message: str
    author: str
    url: str
    ts: float


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self.conn.executescript(SCHEMA)
        for table, column, kind in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                with self.conn:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def close(self) -> None:
        self.conn.close()

    # meta
    def get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def last_seen_id(self) -> int | None:
        v = self.get_meta("last_seen_id")
        return int(v) if v else None

    def bump_last_seen(self, msg_id: int) -> None:
        cur = self.last_seen_id() or 0
        if msg_id > cur:
            self.set_meta("last_seen_id", str(msg_id))

    # writes
    def insert_message(self, rec: dict[str, Any]) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO messages (id, author_id, author_name, webhook_id, content, embeds, ts,"
                " channel_id, thread_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (rec["id"], rec["author_id"], rec["author_name"], rec.get("webhook_id"),
                 rec.get("content", ""), json.dumps(rec.get("embeds", [])), rec["ts"],
                 rec.get("channel_id"), rec.get("thread_id")))
            if rec.get("thread_id"):
                # A thread started from a message has that message's id.
                self.conn.execute(
                    "INSERT OR IGNORE INTO threads (root_msg_id, thread_id, author_id, ts) VALUES (?,?,?,?)",
                    (rec["thread_id"], rec["thread_id"], rec["author_id"], rec["ts"]))
        return cur.rowcount > 0

    def insert_event(self, msg_id: int, author_id: int, ev: Any, ts: float,
                     channel_id: int | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO events (msg_id, author_id, agent, type, uid, files, text, ttl,"
                " commit_sha, all_flag, ts, kind, action, branch, channel_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (msg_id, author_id, ev.agent, ev.type, ev.id, json.dumps(list(ev.files)), ev.text,
                 ev.ttl, ev.commit, int(ev.all), ts, getattr(ev, "kind", None),
                 getattr(ev, "action", None), getattr(ev, "branch", None), channel_id))

    def insert_commit(self, c: Any, ts: float, msg_id: int, raw: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO commits VALUES (?,?,?,?,?,?,?,?,?)",
                (c.sha, c.branch, c.repo, c.message, c.author, c.url, ts, msg_id, json.dumps(raw)))

    def insert_own_action(self, msg_id: int, type_: str, summary: str, files: list[str],
                          commit: str | None, ts: float) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO own_actions VALUES (?,?,?,?,?,?)",
                              (msg_id, type_, summary, json.dumps(files), commit, ts))

    # locks
    def event_rows(self) -> list[EventRow]:
        rows = self.conn.execute("SELECT * FROM events ORDER BY msg_id").fetchall()
        return [EventRow(r["msg_id"], r["author_id"], r["agent"], r["type"], r["uid"],
                         tuple(json.loads(r["files"])), r["text"] or "", r["ttl"],
                         bool(r["all_flag"]), r["ts"]) for r in rows]

    def rebuild_locks(self, default_ttl: int = 120) -> None:
        locks = derive_locks(self.event_rows(), default_ttl)
        with self.conn:
            self.conn.execute("DELETE FROM locks")
            self.conn.executemany(
                "INSERT INTO locks VALUES (?,?,?,?,?,?,?,?,?)",
                [(l.msg_id, l.file, l.author_id, l.agent, l.uid, l.reason, l.ts, l.expires_ts,
                  l.released_by) for l in locks])

    def active_locks(self, now: float | None = None) -> list[Lock]:
        now = time.time() if now is None else now
        rows = self.conn.execute(
            "SELECT * FROM locks WHERE released_by IS NULL AND expires_ts > ? ORDER BY msg_id, file",
            (now,)).fetchall()
        return [Lock(r["msg_id"], r["file"], r["author_id"], r["agent"], r["uid"], r["reason"] or "",
                     r["ts"], r["expires_ts"], None) for r in rows]

    # reads
    def recent_events(self, types: tuple[str, ...], limit: int) -> list[sqlite3.Row]:
        q = ",".join("?" * len(types))
        return self.conn.execute(
            f"SELECT * FROM events WHERE type IN ({q}) ORDER BY msg_id DESC LIMIT ?",
            (*types, limit)).fetchall()

    def recent_commits(self, n: int) -> list[CommitRow]:
        rows = self.conn.execute("SELECT * FROM commits ORDER BY msg_id DESC, ts DESC LIMIT ?", (n,)).fetchall()
        return [CommitRow(r["sha"], r["branch"], r["repo"], r["message"], r["author"], r["url"], r["ts"])
                for r in rows]

    # threads
    def set_thread(self, root_msg_id: int, thread_id: int, author_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO threads (root_msg_id, thread_id, author_id, ts, closed)"
                " VALUES (?,?,?,?,0)", (root_msg_id, thread_id, author_id, time.time()))

    def set_thread_if_new(self, root_msg_id: int, thread_id: int, author_id: int, ts: float) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO threads (root_msg_id, thread_id, author_id, ts, closed)"
                " VALUES (?,?,?,?,0)", (root_msg_id, thread_id, author_id, ts))

    def thread_for(self, root_msg_id: int) -> int | None:
        r = self.conn.execute("SELECT thread_id FROM threads WHERE root_msg_id=?", (root_msg_id,)).fetchone()
        return int(r["thread_id"]) if r and r["thread_id"] else None

    def close_thread(self, thread_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE threads SET closed=1 WHERE thread_id=?", (thread_id,))

    def close_threads_of(self, author_id: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE threads SET closed=1 WHERE author_id=?", (author_id,))

    def open_thread_ids(self, max_age_s: float = 86400, limit: int = 20) -> list[int]:
        rows = self.conn.execute("SELECT thread_id FROM threads WHERE closed=0 AND ts > ?"
                                 " ORDER BY ts DESC LIMIT ?", (time.time() - max_age_s, limit)).fetchall()
        return [int(r["thread_id"]) for r in rows if r["thread_id"]]

    def own_actions(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM own_actions ORDER BY msg_id DESC LIMIT ?", (limit,)).fetchall()
