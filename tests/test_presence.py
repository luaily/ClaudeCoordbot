import asyncio
import time

import discord

from coordbot.config import parse_config
from coordbot.daemon import ensure_online, gateway_alive
from coordbot.db import Database
from coordbot.discord_client import GatewaySession


class StubClient:
    def __init__(self):
        self.presences, self.closed = [], False

    async def change_presence(self, status=None, activity=None):
        self.presences.append(status)

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


def session(tmp_path, **cfg_extra):
    cfg = parse_config({"bot_token": "x", "channel_id": 1, "agent_name": "a", **cfg_extra}, tmp_path / "c.toml")
    db = Database(tmp_path / "a.db")
    s = GatewaySession(cfg, db)
    s.client = StubClient()
    s.ready.set()
    return s, db


def test_online_idle_offline_and_reset(tmp_path):
    async def go():
        s, db = session(tmp_path)
        assert await s.tick() and s._presence is discord.Status.online
        assert gateway_alive(db)
        s.last_activity = time.monotonic() - 6 * 60  # 6 quiet minutes -> idle
        assert await s.tick() and s._presence is discord.Status.idle
        assert s.client.presences[-1] is discord.Status.idle
        s.touch()  # any activity -> back online, timer restarted
        await asyncio.sleep(0)
        assert s._presence is discord.Status.online and s.client.presences[-1] is discord.Status.online
        db.set_meta("last_local_activity", str(time.time() + 1))  # a CLI command in another process
        s.last_activity = time.monotonic() - 19 * 60
        assert await s.tick() and s.idle_seconds() < 5  # the stamp restarted the timer
        s.last_activity = time.monotonic() - 21 * 60  # 21 quiet minutes -> offline
        assert not await s.tick()
        assert s.client.closed and not gateway_alive(db)
    asyncio.run(go())


def test_zero_disables(tmp_path):
    async def go():
        s, _ = session(tmp_path, away_minutes=0, idle_minutes=0)
        s.last_activity = time.monotonic() - 999 * 60
        assert await s.tick() and s._presence is discord.Status.online
    asyncio.run(go())


def test_autostart_rules(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr("coordbot.daemon.subprocess.Popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr("coordbot.daemon.HOME_DIR", tmp_path)
    monkeypatch.delenv("COORDBOT_NO_AUTOSTART", raising=False)
    cfg = parse_config({"bot_token": "x", "channel_id": 1, "agent_name": "a"}, tmp_path / "c.toml")
    db = Database(tmp_path / "a.db")
    assert ensure_online(cfg, db) and started[0][3:5] == ["run", "--config"]
    assert not ensure_online(cfg, db)  # still connecting: don't start twice
    db.set_meta("gateway_spawned", "0")
    db.set_meta("gateway_heartbeat", str(time.time()))
    assert not ensure_online(cfg, db)  # a live session (MCP or daemon) is already online
    off = parse_config({"bot_token": "x", "channel_id": 1, "agent_name": "a", "autostart": False}, tmp_path / "c.toml")
    db.set_meta("gateway_heartbeat", "0")
    assert not ensure_online(off, db)
