"""Loading and validating settings. Never prints or returns the token in messages.

Two layers:
  * Personal config (secret, never committed): ~/.coordbot/config.toml
    bot_token, agent_name, statuses, idle_minutes, and optionally channel/trust lists.
  * Project team file (shared, committed): <project>/.coordbot/team.toml
    channel_id, trusted_user_ids, trusted_webhook_ids, required, install, lock TTL.
    Found automatically by walking up from the working directory, so a project
    only needs a .coordbot/ folder to plug every teammate's agent into one channel.
The team file's channel wins; trust lists are merged.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLACEHOLDER_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
HOME_DIR = Path.home() / ".coordbot"
HOME_CONFIG = HOME_DIR / "config.toml"
LEGACY_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
DEFAULT_CONFIG_PATH = HOME_CONFIG
TEAM_DIR = ".coordbot"
TEAM_FILE = "team.toml"
DEFAULT_INSTALL = "https://github.com/luaily/ClaudeCoordbot"
DEFAULT_OAUTH_PORT = 53682
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class ConfigError(Exception):
    """Raised with a human-readable, multi-line message."""


@dataclass(frozen=True)
class Config:
    bot_token: str = field(repr=False)
    channel_id: int
    agent_name: str
    trusted_user_ids: frozenset[int]
    trusted_webhook_ids: frozenset[int]
    db_path: Path
    default_lock_ttl_minutes: int
    config_path: Path
    statuses: tuple[str, ...] = ()
    status_minutes: int = 15
    idle_minutes: int = 20  # offline after this long with no activity (0 = never)
    team: "Team | None" = None
    oauth_port: int = DEFAULT_OAUTH_PORT
    client_secret: str = field(default="", repr=False)  # optional; PKCE normally needs none
    away_minutes: int = 5  # Discord "idle" (moon) after this long with no activity (0 = never)
    autostart: bool = True  # CLI commands start a background bot when none is online

    @property
    def members(self) -> tuple["Member", ...]:
        return self.team.members if self.team else ()

    def member_by_bot(self, bot_id: int) -> "Member | None":
        return next((m for m in self.members if m.bot_id == bot_id), None)

    def member_by_agent(self, agent: str) -> "Member | None":
        a = agent.strip().lower()
        return next((m for m in self.members if m.agent.lower() == a), None)

    def member_humans(self) -> frozenset[int]:
        return frozenset(m.user_id for m in self.members)


@dataclass(frozen=True)
class Member:
    """One teammate: their agent's bot and the human who runs it (linked with `coordbot link`)."""

    agent: str
    bot_id: int
    user_id: int
    name: str


@dataclass(frozen=True)
class Team:
    """Shared project settings from .coordbot/team.toml (no secrets)."""

    path: Path
    project: str
    channel_id: int
    trusted_user_ids: frozenset[int]
    trusted_webhook_ids: frozenset[int]
    required: bool
    install: str
    default_lock_ttl_minutes: int | None
    members: tuple[Member, ...] = ()

    @property
    def root(self) -> Path:
        """The project folder (the one holding .coordbot/)."""
        return self.path.parent.parent


def default_config_path() -> Path:
    env = os.environ.get("COORDBOT_CONFIG")
    if env:
        return Path(env)
    if not HOME_CONFIG.is_file() and LEGACY_CONFIG_PATH.is_file():
        return LEGACY_CONFIG_PATH  # older installs kept config.toml next to the tool
    return HOME_CONFIG


def find_team_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` (or $COORDBOT_PROJECT, or the cwd) to find .coordbot/team.toml."""
    env = os.environ.get("COORDBOT_PROJECT")
    here = (start or (Path(env) if env else Path.cwd())).expanduser().resolve()
    for folder in (here, *here.parents):
        candidate = folder / TEAM_DIR / TEAM_FILE
        if candidate.is_file():
            return candidate
    return None


def _read_toml(path: Path, what: str) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{what} {path} is not valid: {exc}\n"
            "  Check that text values are in quotes and ids are plain numbers."
        ) from None
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc.strerror or exc}") from None


def parse_team(data: dict[str, Any], path: Path) -> Team:
    errors: list[str] = []
    channel_id = _as_id(data.get("channel_id"))
    if channel_id is None:
        errors.append("channel_id must be the coordination channel's id number")
        channel_id = 0
    users = _id_list(data, "trusted_user_ids", errors)
    hooks = _id_list(data, "trusted_webhook_ids", errors)
    project = data.get("project", path.parent.parent.name)
    if not isinstance(project, str) or not _NAME_RE.match(project.strip() or "-"):
        errors.append("project must be a short name (letters, digits, - _ .)")
        project = "project"
    required = data.get("required", False)
    if not isinstance(required, bool):
        errors.append("required must be true or false")
        required = False
    install = data.get("install", DEFAULT_INSTALL)
    if not isinstance(install, str) or not install.startswith("https://"):
        errors.append("install must be an https:// link to coordbot")
        install = DEFAULT_INSTALL
    ttl = data.get("default_lock_ttl_minutes")
    if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 10080):
        errors.append("default_lock_ttl_minutes must be between 1 and 10080")
        ttl = None
    members = _members(data, errors)
    if errors:
        raise ConfigError(f"Problems in the team file {path}:\n" + "\n".join(f"  - {e}" for e in errors))
    return Team(path, project.strip(), channel_id, users, hooks, required, install, ttl, members)


def _members(data: dict[str, Any], errors: list[str]) -> tuple[Member, ...]:
    raw = data.get("members", [])
    if not isinstance(raw, list):
        errors.append("members must be a list (written by `coordbot link`)")
        return ()
    out: list[Member] = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            errors.append(f"members entry {i + 1} is not a table")
            continue
        bot, user = _as_id(m.get("bot_id")), _as_id(m.get("user_id"))
        agent, name = m.get("agent", ""), m.get("name", "")
        if bot is None or user is None or not isinstance(agent, str) or not _NAME_RE.match(agent or "-"):
            errors.append(f"members entry {i + 1} needs agent, bot_id and user_id (run `coordbot link` again)")
            continue
        out.append(Member(agent, bot, user, name if isinstance(name, str) else ""))
    return tuple(out)


def load_team(start: Path | None = None) -> Team | None:
    path = find_team_file(start)
    return parse_team(_read_toml(path, "Team file"), path) if path else None


def _as_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


def _id_list(data: dict[str, Any], key: str, errors: list[str]) -> frozenset[int]:
    raw = data.get(key, [])
    if not isinstance(raw, list):
        errors.append(f'{key} must be a list of numbers, like {key} = [123456789012345678] (see README step 7)')
        return frozenset()
    out: set[int] = set()
    for item in raw:
        n = _as_id(item)
        if n is None:
            errors.append(f"{key} contains something that is not a Discord id number: {item!r}")
        else:
            out.add(n)
    return frozenset(out)


MAX_STATUSES = 100
MAX_STATUS_LENGTH = 128


def _status_list(data: dict[str, Any], errors: list[str]) -> tuple[str, ...]:
    raw = data.get("statuses", [])
    if not isinstance(raw, list):
        errors.append('statuses must be a list of texts in quotes, like statuses = ["Stacking bricks", "Reading commits"]')
        return ()
    if len(raw) > MAX_STATUSES:
        errors.append(f"statuses has {len(raw)} entries; the most allowed is {MAX_STATUSES}")
        return ()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"statuses may only contain non-empty texts in quotes, not {item!r}")
        elif len(item.strip()) > MAX_STATUS_LENGTH:
            errors.append(f"a status is too long (max {MAX_STATUS_LENGTH} characters): {item.strip()[:30]!r}...")
        else:
            out.append(item.strip())
    return tuple(out)


def parse_config(data: dict[str, Any], config_path: Path, team: Team | None = None) -> Config:
    errors: list[str] = []
    data = dict(data)
    if team is not None:
        # The project's shared settings: its channel wins, trust lists are merged.
        data["channel_id"] = team.channel_id
        # Linked members are trusted: their agent's bot and the human who runs it.
        member_ids = frozenset(i for m in team.members for i in (m.bot_id, m.user_id))
        for key, ids in (("trusted_user_ids", team.trusted_user_ids | member_ids),
                         ("trusted_webhook_ids", team.trusted_webhook_ids)):
            own = data.get(key, [])
            if isinstance(own, list):  # a bad value is left alone so validation reports it
                data[key] = [*own, *sorted(ids)]
        if team.default_lock_ttl_minutes and "default_lock_ttl_minutes" not in data:
            data["default_lock_ttl_minutes"] = team.default_lock_ttl_minutes
        if "db_path" not in data:
            data["db_path"] = str(HOME_DIR / f"{team.project}-{team.channel_id}.db")

    token = data.get("bot_token")
    if not isinstance(token, str) or not token.strip():
        errors.append("bot_token is missing: paste your bot's token between the quotes (see README step 5)")
        token = ""
    elif token.strip() == PLACEHOLDER_TOKEN:
        errors.append("bot_token still has the placeholder text: paste your real bot token (see README step 5)")
    token = token.strip()

    channel_raw = data.get("channel_id")
    channel_id = _as_id(channel_raw)
    if channel_raw is None or channel_raw == 0:
        errors.append("channel_id is missing: copy the channel's id from Discord (see README step 6), "
                      "or work inside a project that has a .coordbot/team.toml")
        channel_id = 0
    elif channel_id is None:
        errors.append("channel_id must be a number like 123456789012345678 (see README step 6)")
        channel_id = 0

    agent = data.get("agent_name")
    if not isinstance(agent, str) or not agent.strip():
        errors.append('agent_name is missing: pick a name like "alice-agent"')
        agent = ""
    elif not _NAME_RE.match(agent.strip()):
        errors.append("agent_name may only use letters, digits, - _ . (max 40 characters)")
    agent = agent.strip()

    users = _id_list(data, "trusted_user_ids", errors)
    hooks = _id_list(data, "trusted_webhook_ids", errors)

    db_raw = data.get("db_path", "coordbot.db")
    if not isinstance(db_raw, str) or not db_raw.strip():
        errors.append("db_path must be a file name in quotes, like db_path = \"coordbot.db\"")
        db_raw = "coordbot.db"
    db_path = Path(db_raw).expanduser()
    if not db_path.is_absolute():
        db_path = config_path.parent / db_path

    ttl = data.get("default_lock_ttl_minutes", 120)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not (1 <= ttl <= 10080):
        errors.append("default_lock_ttl_minutes must be a whole number of minutes between 1 and 10080")
        ttl = 120

    statuses = _status_list(data, errors)

    minutes = data.get("status_minutes", 15)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not (0 <= minutes <= 1440):
        errors.append("status_minutes must be a whole number of minutes between 0 and 1440 (0 = pick once at startup)")
        minutes = 15

    idle = data.get("idle_minutes", 20)
    if isinstance(idle, bool) or not isinstance(idle, int) or not (0 <= idle <= 1440):
        errors.append("idle_minutes must be a whole number of minutes between 0 and 1440 (0 = never go offline)")
        idle = 20
    away = data.get("away_minutes", 5)
    if isinstance(away, bool) or not isinstance(away, int) or not (0 <= away <= 1440):
        errors.append("away_minutes must be a whole number of minutes between 0 and 1440 (0 = never show idle)")
        away = 5
    autostart = data.get("autostart", True)
    if not isinstance(autostart, bool):
        errors.append("autostart must be true or false")
        autostart = True

    port = data.get("oauth_port", DEFAULT_OAUTH_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not (1024 <= port <= 65535):
        errors.append("oauth_port must be a port number between 1024 and 65535")
        port = DEFAULT_OAUTH_PORT
    secret = data.get("client_secret", "")
    if not isinstance(secret, str):
        errors.append("client_secret must be text in quotes (or leave it out)")
        secret = ""

    if errors:
        raise ConfigError(
            f"Problems in {config_path}:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    return Config(token, channel_id, agent, users, hooks, db_path, ttl, config_path, statuses, minutes,
                  idle, team, port, secret.strip(), away, autostart)


def load_config(path: Path | None = None, project: Path | None = None) -> Config:
    path = (path or default_config_path()).expanduser()
    if not path.is_file():
        raise ConfigError(
            f"Could not find your personal coordbot settings at {path}.\n"
            "  Run `coordbot setup` (or the setup script for your computer) first."
        )
    data = _read_toml(path, "Config")
    return parse_config(data, path, load_team(project))
