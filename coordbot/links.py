"""Show a link or a value at an interactive terminal: C copies it, Enter opens it in the browser.

Only for a human at a real terminal (stdin and stdout are TTYs, not --agent). Never used by the
MCP server: its stdin is the MCP channel.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser

from . import ui


def interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty() and not ui.is_agent()
    except (AttributeError, ValueError):
        return False


def read_key() -> str:
    """One key press, without Enter. Ctrl+C still raises KeyboardInterrupt."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # arrows and function keys come as two codes
            msvcrt.getwch()
            return ""
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # keeps Ctrl+C working
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def copy(text: str) -> bool:
    """Put text on the clipboard. False when no clipboard tool is available."""
    if sys.platform == "darwin":
        cmds = [["pbcopy"]]
    elif os.name == "nt":
        cmds = [["clip"]]
    else:
        cmds = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for cmd in cmds:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=True, timeout=5)  # noqa: S603
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def _copied(text: str) -> None:
    if copy(text):
        print(ui.ok("Copied."))
    else:
        print(ui.warn("Couldn't reach the clipboard here: select the text above and copy it."))


def offer_link(url: str) -> bool:
    """Print the link, then wait: C copies, Enter opens it in the browser, S skips. True if opened."""
    print("  " + ui.value(url))
    if not interactive():
        return False
    print(ui.dim("  [C] copy   [Enter] open in browser   [S] skip"))
    while True:
        key = read_key().lower()
        if key == "c":
            _copied(url)
        elif key in ("\r", "\n"):
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001 - the link is printed anyway
                pass
            print(ui.ok("Opened in your browser."))
            return True
        elif key in ("s", "\x1b"):
            return False


def offer_copy(text: str, done: str = "continue") -> None:
    """Print a value to paste somewhere, then wait: C copies it, Enter continues."""
    print("  " + ui.value(text))
    if not interactive():
        return
    print(ui.dim(f"  [C] copy   [Enter] {done}"))
    while True:
        key = read_key().lower()
        if key == "c":
            _copied(text)
        elif key in ("\r", "\n"):
            return
