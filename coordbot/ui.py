"""Terminal styling. Build text with these helpers; never write ANSI codes elsewhere.

Color is on only for a human at a real terminal: stdout is a TTY, NO_COLOR is unset, and the run
is neither --agent (or COORDBOT_AGENT=1) nor --json. --agent output is compact: no color, no rules,
no blank lines, no decorative glyphs. The MCP server never gets color.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterable, Iterator

_RESET = "\033[0m"
_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "magenta": "35", "cyan": "36"}
_state = {"color": False, "agent": False}


def agent_env() -> bool:
    return os.environ.get("COORDBOT_AGENT", "").strip().lower() in {"1", "true", "yes", "on"}


def _enable_windows_ansi() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001
        return False


def configure(agent: bool = False, json: bool = False) -> None:
    """Choose the output mode for this run."""
    agent = agent or agent_env()
    color = False
    if not agent and not json and not os.environ.get("NO_COLOR"):
        try:
            color = sys.stdout.isatty()
        except (AttributeError, ValueError):
            color = False
        if color:
            color = _enable_windows_ansi()
    _state["agent"], _state["color"] = agent, color


@contextlib.contextmanager
def plain() -> Iterator[None]:
    """Compact, colorless output (used for text that goes to MCP clients)."""
    before = dict(_state)
    _state["agent"], _state["color"] = True, False
    try:
        yield
    finally:
        _state.update(before)


def is_agent() -> bool:
    return bool(_state["agent"])


def use_color() -> bool:
    return bool(_state["color"])


def _glyph(fancy: str, plain_: str) -> str:
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        fancy.encode(enc)
        return fancy
    except (UnicodeEncodeError, LookupError):
        return plain_


def style(text: str, *names: str) -> str:
    if not _state["color"] or not names:
        return text
    return "\033[" + ";".join(_CODES[n] for n in names) + "m" + text + _RESET


def value(x: object) -> str:
    """Something the user set up: agent name, ids, paths, URLs."""
    return style(str(x), "magenta")


def dim(text: str) -> str:
    return style(text, "dim")


def bold(text: str) -> str:
    return style(text, "bold")


def ok(msg: str) -> str:
    return msg if _state["agent"] else f"{style(_glyph('✓', '+'), 'green')} {msg}"


def warn(msg: str) -> str:
    return f"warning: {msg}" if _state["agent"] else f"{style('!', 'yellow')} {style(msg, 'yellow')}"


def err(msg: str) -> str:
    return f"error: {msg}" if _state["agent"] else f"{style(_glyph('✗', 'x'), 'red')} {style(msg, 'red')}"


def info(msg: str) -> str:
    return msg if _state["agent"] else f"{style(_glyph('•', '-'), 'dim')} {msg}"


def tagged(tag: str, msg: str) -> str:
    """Render a line tagged ok / warn / err / info (see setup.doctor_report)."""
    return {"ok": ok, "warn": warn, "err": err}.get(tag, info)(msg)


def kv(label: str, val: object) -> str:
    return f"{label}: {val}" if _state["agent"] else f"{style(label + ':', 'dim')} {value(val)}"


def rule(width: int = 48) -> str | None:
    return None if _state["agent"] else style(_glyph("─", "-") * width, "dim")


def blank() -> str | None:
    return None if _state["agent"] else ""


def header(title: str) -> str | None:
    """Bold cyan title with a rule under it (nothing in --agent mode)."""
    if _state["agent"]:
        return None
    return style(title, "bold", "cyan") + "\n" + (rule(max(len(title), 12)) or "")


def step(n: int, total: int, title: str) -> str:
    return f"step {n}/{total}: {title}" if _state["agent"] else style(f"Step {n}/{total}", "bold", "cyan") + f"  {style(title, 'bold')}"


def lines(items: Iterable[str | None]) -> str:
    """Join lines, dropping the None that rule()/blank()/header() return in --agent mode."""
    return "\n".join(i for i in items if i is not None)


def section(title: str, rows: list[str], empty: str = "none", count: bool = False) -> str:
    """A titled block of rows. Compact `Title: none` / `Title:` in --agent mode."""
    n = f" ({len(rows)})" if count and rows else ""
    if _state["agent"]:
        return f"{title}{n}: {empty}" if not rows else lines([f"{title}{n}:", *rows])
    return lines([header(title + n), *(rows or [dim(empty)])])


def blocks(*parts: str) -> str:
    """Stack blocks; a blank line between them for humans, none in --agent mode."""
    return ("\n" if _state["agent"] else "\n\n").join(p for p in parts if p)


def say(msg: str = "") -> None:
    """Print to stderr (keeps stdout clean for JSON and MCP)."""
    if msg == "" and _state["agent"]:
        return
    print(msg, file=sys.stderr, flush=True)
