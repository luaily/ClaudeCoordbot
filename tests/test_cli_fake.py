"""Drive the CLI commands against an in-memory fake channel (no Discord)."""
import argparse
import asyncio
import time

from coordbot import cli
from coordbot.config import parse_config
from coordbot.db import Database


class Channel:
    def __init__(self):
        self.msgs, self.next = [], 1000

    def add(self, author, content, reply_to=None, thread_id=None):
        self.next += 1
        rec = {"id": self.next, "author_id": author, "author_name": "n", "webhook_id": None,
               "content": content, "embeds": [], "ts": time.time(), "reply_to": reply_to,
               "thread_id": thread_id}
        self.msgs.append(rec)
        return rec


class Fake:
    def __init__(self, ch, own_id, hook=None):
        self.ch, self.own_id, self.hook = ch, own_id, hook

        self.threads, self.archived = {}, []

    async def fetch(self, after):
        return [m for m in self.ch.msgs if m["id"] > (after or 0) and not m.get("thread_id")]

    async def fetch_thread(self, tid, after=None):
        return [m for m in self.ch.msgs if m.get("thread_id") == tid and m["id"] > (after or 0)]

    async def send(self, content, reply_to=None, thread_id=None):
        rec = self.ch.add(self.own_id, content, reply_to, thread_id)
        if self.hook:
            self.hook()
        return dict(rec)

    async def create_thread(self, message_id, name):
        tid = message_id  # Discord: thread id == starter message id
        self.threads[tid] = name
        return tid

    async def archive_thread(self, tid):
        self.archived.append(tid)


def make(tmp_path, name, own_id, ch, hook=None):
    cfg = parse_config({"bot_token": "x", "channel_id": 1, "agent_name": name, "trusted_user_ids": [1, 2]},
                       tmp_path / f"{name}.toml")
    db = Database(tmp_path / f"{name}.db")
    return cli.Ctx(cfg, db, Fake(ch, own_id, hook), own_id, True)


def args(**kw):
    return argparse.Namespace(race_wait=0, **kw)


def run(coro):
    return asyncio.run(coro)


def test_lock_conflict_then_release(tmp_path, capsys):
    ch = Channel()
    a, b = make(tmp_path, "a", 1, ch), make(tmp_path, "b", 2, ch)
    assert run(cli.cmd_lock(a, args(files=["src/x.luau"], reason="r", ttl=30))) == 0
    run(b.sync())
    assert run(cli.cmd_lock(b, args(files=["src"], reason="r", ttl=None))) == 2
    assert run(cli.cmd_check(b, args(files=["src/x.luau"]))) == 2
    assert run(cli.cmd_done(a, args(summary="ok", files=["src/x.luau"], commit="abc", all=False))) == 0
    run(b.sync())
    assert run(cli.cmd_check(b, args(files=["src/x.luau"]))) == 0
    assert run(cli.cmd_log(a, args(n=5))) == 0


def test_lost_race_posts_release(tmp_path):
    ch = Channel()
    a = make(tmp_path, "a", 1, ch)
    # a competing earlier lock appears only after the pre-check (simulate: id lower than ours is impossible
    # via fake counter, so insert one with a lower id directly when we post)
    from coordbot.protocol import build_messages
    [(content, _)] = build_messages("lock", "b", files=["src/x"], text="first", ttl=30)

    def hook():
        rec = {"id": ch.msgs[-1]["id"] - 1, "author_id": 2, "author_name": "b", "webhook_id": None,
               "content": content, "embeds": [], "ts": time.time()}
        ch.msgs.insert(0, rec)
        a.session.hook = None

    a.session.hook = hook
    assert run(cli.cmd_lock(a, args(files=["src/x"], reason="r", ttl=30))) == 2
    assert "release" in ch.msgs[-1]["content"]
    assert [l.author_id for l in a.db.active_locks()] == [2]


def test_threads_replies_and_context(tmp_path):
    ch = Channel()
    a, b = make(tmp_path, "a", 1, ch), make(tmp_path, "b", 2, ch)
    assert run(cli.cmd_plan(a, args(summary="build roof", files=["src/a"]))) == 0
    tid = a.task_thread()
    assert tid and tid in a.session.threads
    assert run(cli.cmd_lock(a, args(files=["src/a"], reason="roof", ttl=30))) == 0
    lock_msg = ch.msgs[-1]["id"]
    assert a.task_thread() == tid  # one thread per task
    assert run(cli.cmd_context(a, args(message="renamed Roof.luau", kind="context", files=[]))) == 0
    assert ch.msgs[-1]["thread_id"] == tid
    # another agent sees thread context through sync
    run(b.sync())
    assert run(cli.cmd_context(a, args(message="defended", kind="defense", files=[]))) == 0
    assert run(cli.cmd_done(a, args(summary="ok", files=["src/a"], commit="abc", all=False))) == 0
    assert ch.msgs[-1]["reply_to"] == lock_msg
    assert a.session.archived == [tid] and a.task_thread() is None
    run(b.sync())
    assert run(cli.cmd_check(b, args(files=["src/a"]))) == 0
    kinds = [r["kind"] for r in b.db.recent_events(("context",), 10)]
    assert "context" in kinds


def test_git_notice_and_status(tmp_path):
    ch = Channel()
    a = make(tmp_path, "a", 1, ch)
    assert run(cli.cmd_git(a, args(action="push", branch="main", commit="abc", summary="", files=[]))) == 0
    assert "push" in ch.msgs[-1]["content"]
    a.capture = True
    assert run(cli.cmd_status(a, args())) == 0
    assert a.result["recent"][0]["action"] == "push"
