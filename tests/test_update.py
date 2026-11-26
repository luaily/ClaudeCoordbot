import json
import subprocess
from types import SimpleNamespace

import pytest

from coordbot import update as up


def fake_run(calls, tool_list="coordbot v2.5.0\n", code=0):
    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1:3] == ["tool", "list"]:
            return SimpleNamespace(returncode=0, stdout=tool_list)
        return SimpleNamespace(returncode=code, stdout="")
    return run


@pytest.fixture
def uv(monkeypatch):
    monkeypatch.setattr(up, "find_uv", lambda: "/bin/uv")
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls))
    return calls


def test_parse_version():
    assert up.parse_version("2.10.0") > up.parse_version("2.9.1")


def test_up_to_date_runs_nothing(monkeypatch, uv, capsys):
    monkeypatch.setattr(up, "latest_version", lambda: up.__version__)
    assert up.run_update() == 0
    assert uv == [] and "up to date" in capsys.readouterr().out


def test_newer_runs_upgrade(monkeypatch, uv, capsys):
    monkeypatch.setattr(up, "latest_version", lambda: "99.0.0")
    assert up.run_update() == 0
    assert uv[-1] == ["/bin/uv", "tool", "upgrade", "coordbot"]


def test_check_only_reports(monkeypatch, uv, capsys):
    monkeypatch.setattr(up, "latest_version", lambda: "99.0.0")
    assert up.run_update(check=True, as_json=True) == 0
    assert uv == [] and json.loads(capsys.readouterr().out)["update_available"] is True


def test_force_reinstalls(monkeypatch, uv):
    monkeypatch.setattr(up, "latest_version", lambda: up.__version__)
    assert up.run_update(force=True) == 0
    assert uv[-1] == ["/bin/uv", "tool", "install", "--force", up.INSTALL_SPEC]


def test_not_a_uv_tool_install(monkeypatch, capsys):
    monkeypatch.setattr(up, "find_uv", lambda: "/bin/uv")
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls, tool_list="other v1\n"))
    monkeypatch.setattr(up, "latest_version", lambda: "99.0.0")
    assert up.run_update() == 1
    assert all(c[1:3] == ["tool", "list"] for c in calls) and "git pull" in capsys.readouterr().out


def test_unreachable(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(up.urllib.request, "urlopen", boom)
    assert up.run_update() == 1
    assert "internet" in capsys.readouterr().out
