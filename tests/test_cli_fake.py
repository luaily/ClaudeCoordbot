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

    def add(self, author, content):
        self.next += 1
        rec = {"id": self.next, "author_id": author, "author_name": "n", "webhook_id": None,
               "content": content, "embeds": [], "ts": time.time()}
        self.msgs.append(rec)
        return rec


class Fake:
    def __init__(self, ch, own_id, hook=None):
        self.ch, self.own_id, self.hook = ch, own_id, hook

    async def fetch(self, after):
        return [m for m in self.ch.msgs if m["id"] > (after or 0)]

    async def send(self, content):
        rec = self.ch.add(self.own_id, content)
        if self.hook:
            self.hook()
        return rec


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
