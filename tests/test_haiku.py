import json
import tomllib

import pytest

from coordbot import cli
from coordbot.config import ConfigError, haiku_notes, load_team, parse_config, parse_team
from coordbot.mcp_server import INSTRUCTIONS, build_instructions
from coordbot.oauth import add_trusted_user, set_team_bool

TEAM = '# header\nproject = "demo"\nchannel_id = 5\ntrusted_user_ids = [9]  # ids\nrequired = true\n'


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / ".coordbot" / "team.toml"
    p.parent.mkdir()
    p.write_text(TEAM)
    return tmp_path, p


def team_with(tmp_path, extra=""):
    return parse_team(tomllib.loads(TEAM + extra), tmp_path / ".coordbot" / "team.toml")


def test_parse_haiku(tmp_path):
    t = team_with(tmp_path)
    assert t.haiku_defense is None and t.haiku_preflight is None
    t = team_with(tmp_path, "haiku_defense = true\nhaiku_preflight = false\n")
    assert t.haiku_defense is True and t.haiku_preflight is False
    with pytest.raises(ConfigError, match="haiku_defense must be true or false"):
        team_with(tmp_path, 'haiku_defense = "yes"\n')


def test_notes_warn_when_missing(tmp_path):
    assert haiku_notes(team_with(tmp_path)) and "coordbot haiku" in haiku_notes(team_with(tmp_path))[0]
    assert haiku_notes(team_with(tmp_path, "haiku_defense = true\nhaiku_preflight = true\n")) == []
    cfg = parse_config({"bot_token": "x", "channel_id": 1, "agent_name": "a"}, tmp_path / "c.toml",
                           team_with(tmp_path))
    assert any("Haiku" in n for n in cli.setup_notes(cfg, 0))


def test_instructions_include_only_enabled_rules(tmp_path):
    off = build_instructions(team_with(tmp_path, "haiku_defense = false\nhaiku_preflight = false\n"))
    assert "Haiku" not in off
    assert "Haiku" not in build_instructions(None) and "{GIT_EXTRA}" not in off and "{DEFENSE_EXTRA}" not in off
    d = build_instructions(team_with(tmp_path, "haiku_defense = true\nhaiku_preflight = false\n"))
    assert cli.HAIKU_DEFENSE_RULE in d and cli.HAIKU_PREFLIGHT_RULE not in d
    p = build_instructions(team_with(tmp_path, "haiku_defense = false\nhaiku_preflight = true\n"))
    assert cli.HAIKU_PREFLIGHT_RULE in p and cli.HAIKU_DEFENSE_RULE not in p
    assert "Haiku" not in INSTRUCTIONS


def test_set_team_bool_adds_and_replaces_in_place(proj):
    root, path = proj
    assert set_team_bool(path, "haiku_defense", True)
    assert not set_team_bool(path, "haiku_defense", True)
    assert set_team_bool(path, "haiku_defense", False)
    assert set_team_bool(path, "haiku_preflight", True)
    text = path.read_text()
    assert text.startswith(TEAM) and text.count("haiku_defense") == 1
    data = tomllib.loads(text)
    assert data["haiku_defense"] is False and data["haiku_preflight"] is True
    t = load_team(root)
    assert t.haiku_defense is False and t.haiku_preflight is True


def test_set_team_bool_keeps_trailing_comment(proj):
    _, path = proj
    path.write_text(TEAM + "haiku_defense = true  # keep\n")
    set_team_bool(path, "haiku_defense", False)
    assert path.read_text().endswith("haiku_defense = false  # keep\n")


def test_add_trusted_user(proj):
    _, path = proj
    path.write_text(TEAM.replace("  # ids", ""))
    assert add_trusted_user(path, 7) and not add_trusted_user(path, 7)
    assert tomllib.loads(path.read_text())["trusted_user_ids"] == [9, 7]


def run_haiku(root, capsys, *argv):
    args = cli.build_parser().parse_args(["haiku", *argv, "--project", str(root), "--json"])
    code = cli.haiku_command(args, True)
    return code, json.loads(capsys.readouterr().out)


def test_haiku_command(proj, capsys):
    root, path = proj
    code, out = run_haiku(root, capsys)
    assert code == 0 and out["defense"] is None and out["warnings"]
    code, out = run_haiku(root, capsys, "preflight", "on")
    assert code == 0 and out["changed"] and "haiku_preflight = true" in path.read_text()
    code, out = run_haiku(root, capsys, "defense")
    assert code == 1 and not out["ok"]
    code, out = run_haiku(root, capsys)
    assert out["preflight"] is True and out["defense"] is None


def test_preflight_and_status_report_haiku(tmp_path):
    t = team_with(tmp_path, "haiku_preflight = true\n")
    state = cli.haiku_state(t)
    assert state["preflight"] is True and "preflight_rule" in state and "defense_rule" not in state
