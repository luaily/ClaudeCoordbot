"""Loading and validating config.toml. Never prints or returns the token in messages."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLACEHOLDER_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
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


def default_config_path() -> Path:
    env = os.environ.get("COORDBOT_CONFIG")
    return Path(env) if env else DEFAULT_CONFIG_PATH


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


def parse_config(data: dict[str, Any], config_path: Path) -> Config:
    errors: list[str] = []

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
        errors.append("channel_id is missing: copy the channel's id from Discord (see README step 6)")
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

    if errors:
        raise ConfigError(
            f"Problems in {config_path}:\n" + "\n".join(f"  - {e}" for e in errors)
        )
    return Config(token, channel_id, agent, users, hooks, db_path, ttl, config_path)


def load_config(path: Path | None = None) -> Config:
    path = (path or default_config_path()).expanduser()
    if not path.is_file():
        raise ConfigError(
            f"Could not find {path}.\n"
            "  Run setup-mac.command (macOS) or setup-windows.bat (Windows) first, "
            "or copy config.example.toml to config.toml and fill it in."
        )
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{path} is not valid: {exc}\n"
            "  Check that text values are in quotes and ids are plain numbers."
        ) from None
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc.strerror or exc}") from None
    return parse_config(data, path)
