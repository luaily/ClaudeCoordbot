"""`coordbot update`: compare with the latest released version and upgrade through uv."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, ui

REMOTE_INIT = "https://raw.githubusercontent.com/luaily/ClaudeCoordbot/main/coordbot/__init__.py"
INSTALL_SPEC = "git+https://github.com/luaily/ClaudeCoordbot"
USER_AGENT = "coordbot (https://github.com/luaily/ClaudeCoordbot)"
_VERSION_RE = re.compile(r"""(?m)^__version__\s*=\s*["']([^"']+)["']""")


class UpdateProblem(Exception):
    pass


def parse_version(v: str) -> tuple[int, ...]:
    parts = []
    for p in v.strip().split("."):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def latest_version(timeout: float = 10) -> str:
    req = urllib.request.Request(REMOTE_INIT, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
            text = r.read().decode("utf-8", "replace")
    except (OSError, ValueError) as exc:  # URLError and timeouts are OSErrors
        raise UpdateProblem(f"Could not reach GitHub to check for updates ({getattr(exc, 'reason', exc)}). "
                            "Check your internet connection and try again.") from None
    m = _VERSION_RE.search(text)
    if not m:
        raise UpdateProblem("Could not read the latest version from GitHub.")
    return m.group(1)


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    return str(fallback) if fallback.is_file() else None


def is_uv_tool_install(uv: str) -> bool:
    """True when `uv tool list` shows coordbot (false for a git clone or `uvx`)."""
    try:
        out = subprocess.run([uv, "tool", "list"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[:1] == ["coordbot"] for line in out.stdout.splitlines())


def _emit(as_json: bool, data: dict[str, object], text: str, code: int) -> int:
    print(json.dumps(data) if as_json else text)
    return code


def run_update(check: bool = False, force: bool = False, as_json: bool = False) -> int:
    try:
        latest = latest_version()
    except UpdateProblem as exc:
        return _emit(as_json, {"ok": False, "error": str(exc)}, ui.err(str(exc)), 1)
    newer = parse_version(latest) > parse_version(__version__)
    base = {"installed": __version__, "latest": latest, "update_available": newer}
    if check:
        text = (ui.warn(f"Update available: {ui.value(__version__)} -> {ui.value(latest)}. Run `coordbot update`.")
                if newer else ui.ok(f"coordbot {ui.value(__version__)} is up to date."))
        return _emit(as_json, {"ok": True, **base}, text, 0)
    if not newer and not force:
        return _emit(as_json, {"ok": True, **base, "updated": False},
                     ui.ok(f"coordbot {ui.value(__version__)} is up to date."), 0)
    uv = find_uv()
    if uv is None:
        msg = ("uv was not found. Install it from https://docs.astral.sh/uv/getting-started/installation/, "
               f"then run: uv tool install --force {INSTALL_SPEC}")
        return _emit(as_json, {"ok": False, **base, "error": msg}, ui.err(msg), 1)
    if not is_uv_tool_install(uv):
        msg = ("coordbot was not installed with `uv tool install`, so it can't update itself. "
               f"If you run it from a clone, use `git pull` there. Otherwise run: uv tool install --force {INSTALL_SPEC}")
        return _emit(as_json, {"ok": False, **base, "error": msg}, ui.warn(msg), 1)
    cmd = [uv, "tool", "install", "--force", INSTALL_SPEC] if force else [uv, "tool", "upgrade", "coordbot"]
    if not as_json:
        print(ui.info(f"Updating {ui.value(__version__)} -> {ui.value(latest)} ..."), flush=True)
    try:
        done = subprocess.run(cmd, capture_output=as_json, text=True, check=False)  # noqa: S603
        code = done.returncode
    except OSError as exc:
        return _emit(as_json, {"ok": False, **base, "error": str(exc)}, ui.err(f"Could not run uv: {exc}"), 1)
    if code != 0:
        hint = f"Run this yourself in a fresh terminal: uv tool install --force {INSTALL_SPEC}"
        if os.name == "nt":
            hint = ("Windows can't replace coordbot.exe while it runs. Open a fresh PowerShell window and run: "
                    f"uv tool install --force {INSTALL_SPEC}")
        return _emit(as_json, {"ok": False, **base, "error": f"uv exited with code {code}", "hint": hint},
                     ui.err(f"uv exited with code {code}.") + "\n" + ui.info(hint), 1)
    return _emit(as_json, {"ok": True, **base, "updated": True},
                 ui.ok(f"coordbot is now version {ui.value(latest)}."), 0)
