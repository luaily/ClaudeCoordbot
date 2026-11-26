import re

import pytest

from coordbot import ui

ANSI = re.compile(r"\x1b\[")


class Tty:
    def isatty(self):
        return True

    def write(self, s):
        return len(s)

    encoding = "utf-8"


@pytest.fixture(autouse=True)
def reset():
    yield
    ui.configure(False, True)


def test_color_only_for_humans_on_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COORDBOT_AGENT", raising=False)
    monkeypatch.setattr("sys.stdout", Tty())
    ui.configure(False, False)
    assert ui.use_color() and ANSI.search(ui.ok("hi"))
    ui.configure(True, False)  # --agent
    assert not ui.use_color() and not ANSI.search(ui.ok("hi"))
    ui.configure(False, True)  # --json
    assert not ui.use_color()
    monkeypatch.setenv("NO_COLOR", "1")
    ui.configure(False, False)
    assert not ui.use_color()
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("COORDBOT_AGENT", "1")
    ui.configure(False, False)
    assert ui.is_agent() and not ui.use_color()


def test_no_color_when_not_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COORDBOT_AGENT", raising=False)
    ui.configure(False, False)  # pytest's stdout is not a tty
    assert not ui.use_color() and not ANSI.search(ui.header("T") + ui.warn("x") + ui.value("v"))


def test_agent_output_is_compact(monkeypatch):
    monkeypatch.delenv("COORDBOT_AGENT", raising=False)
    ui.configure(True, False)
    assert ui.rule() is None and ui.blank() is None and ui.header("Title") is None
    assert ui.warn("w") == "warning: w" and ui.err("e") == "error: e" and ui.ok("o") == "o"
    assert ui.kv("a", 1) == "a: 1"
    text = ui.blocks(ui.section("Locks", [], "none", count=True), ui.section("Posts", ["  a", "  b"]))
    assert text == "Locks: none\nPosts:\n  a\n  b"
    assert "─" not in text and "\n\n" not in text


def test_human_output_has_rules(monkeypatch):
    monkeypatch.delenv("COORDBOT_AGENT", raising=False)
    ui.configure(False, False)
    text = ui.blocks(ui.section("Locks", [], "none"), ui.section("Posts", ["a"]))
    assert "─" in text and "\n\n" in text


def test_plain_context_restores():
    ui.configure(False, True)
    with ui.plain():
        assert ui.is_agent()
    assert not ui.is_agent()
