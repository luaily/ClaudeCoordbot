"""Message format: human line + fenced ```coord JSON block. Parsing, building, splitting, GitHub embeds."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
DISCORD_LIMIT = 2000
SAFE_LIMIT = 1900
RESERVE = 500  # room kept free for files in every chunk
MAX_PATH_LEN = 240
MAX_FILES = 500
MAX_TEXT = 600
TYPES = ("plan", "lock", "release", "done")
ROBLOX_PREFIX = "roblox:"

_BLOCK_RE = re.compile(r"```coord[ \t]*\r?\n(.*?)\r?\n?```", re.DOTALL)


@dataclass(frozen=True)
class CoordEvent:
    type: str
    agent: str
    id: str
    files: tuple[str, ...]
    text: str
    ttl: int | None
    commit: str | None
    all: bool
    ts: float


# ---------- paths ----------

def normalize_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    if p.lower().startswith(ROBLOX_PREFIX):
        rest = p[len(ROBLOX_PREFIX):].strip().strip(".")
        return ROBLOX_PREFIX + rest
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/") if len(p) > 1 else p


def overlaps(a: str, b: str) -> bool:
    """True if paths are equal or one contains the other (dir / roblox parent)."""
    a_rbx, b_rbx = a.startswith(ROBLOX_PREFIX), b.startswith(ROBLOX_PREFIX)
    if a_rbx != b_rbx:
        return False
    sep = "." if a_rbx else "/"
    return a == b or a.startswith(b + sep) or b.startswith(a + sep)


def any_overlap(xs: list[str] | tuple[str, ...], ys: list[str] | tuple[str, ...]) -> bool:
    return any(overlaps(x, y) for x in xs for y in ys)


# ---------- encode ----------

def _clean(s: str) -> str:
    return s.replace("```", "'''")


def _one_line(s: str, limit: int) -> str:
    s = " ".join(_clean(s).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def encode_block(payload: dict[str, Any]) -> str:
    return "```coord\n" + json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n```"


def _payload(type_: str, agent: str, files: list[str], text: str, ttl: int | None,
             commit: str | None, all_: bool, ts: float) -> dict[str, Any]:
    d: dict[str, Any] = {
        "v": PROTOCOL_VERSION, "type": type_, "agent": agent, "id": str(uuid.uuid4()),
        "files": [_clean(f) for f in files],
    }
    d["reason" if type_ in ("lock", "release") else "summary"] = _clean(text)
    if ttl is not None:
        d["ttl"] = ttl
    if commit:
        d["commit"] = _clean(commit)
    if all_:
        d["all"] = True
    d["ts"] = int(ts)
    return d


_ICONS = {"plan": "📝", "lock": "🔒", "release": "🔓", "done": "✅"}


def _human(type_: str, agent: str, files: list[str], text: str, commit: str | None, all_: bool) -> str:
    shown = ", ".join(f"`{_one_line(f, 60)}`" for f in files[:4])
    if len(files) > 4:
        shown += f", +{len(files) - 4} more"
    n = len(files)
    t = _one_line(text, 200)
    a = _one_line(agent, 40)
    if type_ == "lock":
        line = f"{_ICONS[type_]} **{a}** locked {n} file{'s' if n != 1 else ''}: {shown}" + (f" — {t}" if t else "")
    elif type_ == "release":
        what = "all its locks" if all_ and not files else f"{n} file{'s' if n != 1 else ''}: {shown}"
        line = f"{_ICONS[type_]} **{a}** released {what}" + (f" — {t}" if t else "")
    elif type_ == "done":
        line = f"{_ICONS[type_]} **{a}** done: {t}"
        if files:
            line += f" (files: {shown})"
        if all_ and not files:
            line += " (released all locks)"
        if commit:
            line += f" commit `{_one_line(commit, 40)}`"
    else:
        line = f"{_ICONS['plan']} **{a}** plans: {t}" + (f" (files: {shown})" if files else "")
    return line


def build_messages(type_: str, agent: str, *, files: list[str] | None = None, text: str = "",
                   ttl: int | None = None, commit: str | None = None, all_: bool = False,
                   ts: float | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Return one or more (content, payload) pairs, each under the safe size limit."""
    if type_ not in TYPES:
        raise ValueError(f"unknown type {type_!r}")
    files = list(files or [])
    ts = time.time() if ts is None else ts
    text = " ".join(text.split())[:MAX_TEXT] if text else ""

    def render(chunk: list[str]) -> tuple[str, dict[str, Any]]:
        payload = _payload(type_, agent, chunk, text, ttl, commit, all_, ts)
        return _human(type_, agent, chunk, text, commit, all_) + "\n" + encode_block(payload), payload

    while text and len(render([])[0]) > SAFE_LIMIT - RESERVE:
        text = text[: int(len(text) * 0.8)]

    chunks: list[list[str]] = [[]]
    for f in files:
        trial = chunks[-1] + [f]
        if chunks[-1] and len(render(trial)[0]) > SAFE_LIMIT:
            chunks.append([f])
        else:
            chunks[-1] = trial
    return [render(c) for c in chunks]


# ---------- parse ----------

def _str(d: dict[str, Any], key: str, limit: int, required: bool = False) -> str | None:
    v = d.get(key)
    if v is None:
        return None if not required else ""
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError(key)
    return v


def parse_coord(content: str, default_ts: float = 0.0, default_ttl: int | None = None) -> CoordEvent | None:
    """Parse the first ```coord block. Returns None for anything missing or malformed."""
    if not isinstance(content, str):
        return None
    m = _BLOCK_RE.search(content)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
        if not isinstance(d, dict) or d.get("v") != PROTOCOL_VERSION:
            return None
        type_ = d.get("type")
        if type_ not in TYPES:
            return None
        agent = _str(d, "agent", 64, True)
        uid = _str(d, "id", 64, True)
        if not agent or not uid:
            return None
        files_raw = d.get("files", [])
        if not isinstance(files_raw, list) or len(files_raw) > MAX_FILES:
            return None
        files: list[str] = []
        for f in files_raw:
            if not isinstance(f, str) or not f.strip() or len(f) > MAX_PATH_LEN + 20:
                return None
            files.append(normalize_path(f))
        text = _str(d, "reason" if type_ in ("lock", "release") else "summary", 2000) or ""
        ttl = d.get("ttl")
        if ttl is not None:
            if isinstance(ttl, bool) or not isinstance(ttl, int) or not (1 <= ttl <= 10080):
                return None
        elif type_ == "lock":
            ttl = default_ttl
        commit = _str(d, "commit", 64)
        all_ = d.get("all", False)
        if not isinstance(all_, bool):
            return None
        ts = d.get("ts", default_ts)
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            ts = default_ts
        return CoordEvent(type_, agent, uid, tuple(files), text, ttl, commit, all_, float(ts))
    except (ValueError, TypeError, RecursionError):
        return None


# ---------- GitHub ----------

@dataclass(frozen=True)
class GitCommit:
    sha: str
    message: str
    author: str
    branch: str
    repo: str
    url: str


_TITLE_RE = re.compile(r"^\s*\[([^\]]+)\]")
_LINE_RE = re.compile(
    r"^\[?`?(?P<sha>[0-9a-fA-F]{7,40})`?\]?(?:\((?P<url>[^)\s]+)\))?[:\s]+(?P<rest>\S.*)$"
)


def parse_github_embed(embed: dict[str, Any]) -> list[GitCommit]:
    """Tolerant parser for the Discord GitHub 'push' embed. Returns [] if it isn't one."""
    if not isinstance(embed, dict):
        return []
    title = embed.get("title") or ""
    desc = embed.get("description") or ""
    if not isinstance(title, str) or not isinstance(desc, str):
        return []
    repo, branch = "", ""
    tm = _TITLE_RE.match(title)
    if tm:
        inner = tm.group(1)
        repo, _, branch = inner.partition(":")
    commits: list[GitCommit] = []
    for line in desc.splitlines():
        lm = _LINE_RE.match(line.strip())
        if not lm:
            continue
        rest = lm.group("rest").strip()
        message, author = rest, ""
        if " - " in rest:
            message, author = (s.strip() for s in rest.rsplit(" - ", 1))
        commits.append(GitCommit(lm.group("sha").lower(), message, author, branch.strip(),
                                 repo.strip(), lm.group("url") or ""))
    return commits
