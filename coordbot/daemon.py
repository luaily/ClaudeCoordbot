"""Keep the bot visibly online for agents that use the CLI instead of the MCP server.

A CLI command is a one-off REST call, which doesn't show the bot online. After a command,
if no live connection is running (no fresh heartbeat in the database), start `coordbot run`
in the background. It shows idle after away_minutes and goes offline after idle_minutes;
every later command restarts the timer through the shared database.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from .config import HOME_DIR, Config
from .db import Database

HEARTBEAT_FRESH_S = 45  # a live session writes a heartbeat about every 15 s
SPAWN_GRACE_S = 90  # don't start a second one while the first is still connecting


def gateway_alive(db: Database) -> bool:
    beat = float(db.get_meta("gateway_heartbeat") or 0)
    return time.time() - beat < HEARTBEAT_FRESH_S


def ensure_online(cfg: Config, db: Database) -> bool:
    """Start the background bot if nothing is online. Returns True if one was started."""
    if not cfg.autostart or os.environ.get("COORDBOT_NO_AUTOSTART") or gateway_alive(db):
        return False
    if time.time() - float(db.get_meta("gateway_spawned") or 0) < SPAWN_GRACE_S:
        return False
    db.set_meta("gateway_spawned", str(time.time()))
    cmd = [sys.executable, "-m", "coordbot", "run", "--config", str(cfg.config_path)]
    if cfg.team is not None:
        cmd += ["--project", str(cfg.team.root)]
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    log = open(HOME_DIR / "coordbot-run.log", "a", encoding="utf-8")  # noqa: SIM115 - handed to the child
    kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
                                 "cwd": str(cfg.team.root if cfg.team else os.getcwd())}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)  # noqa: S603 - our own module with our own config
        return True
    except OSError:
        return False
    finally:
        log.close()
