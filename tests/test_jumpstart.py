import json
import socket
import stat
import tomllib

import pytest

from coordbot import jumpstart as js
from coordbot import links, oauth
from coordbot.oauth import DiscordUser
from coordbot.setup import Line

TEAM = 'project = "demo"\nchannel_id = 5\ntrusted_user_ids = [9]\nrequired = true\n'
TOKEN = "secret.token.value"
URI = oauth.redirect_uri(53682)
INTENT = 1 << 18


class Script:
    """Feeds scripted answers to input() and records every prompt."""

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt}")
        a = self.answers.pop(0)
        if a is KeyboardInterrupt:
            raise KeyboardInterrupt
        return a


@pytest.fixture
def env(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    team = proj / ".coordbot" / "team.toml"
    team.parent.mkdir()
    team.write_text(TEAM)
    cfg = tmp_path / "home" / "config.toml"
    state = {"apps": [], "logins": 0, "shown": []}

    async def doctor(path, project, own_id=None):
        return True, [Line("Discord: connected, and the bot can see the channel.", "ok")]

    def app_info(token):
        apps = state["apps"]
        return apps.pop(0) if len(apps) > 1 else apps[0]

    def login(app_id, port, secret, open_browser):
        state["logins"] += 1
        return DiscordUser(7, "Alice")

    monkeypatch.setattr(js, "doctor_report", doctor)
    monkeypatch.setattr(js, "check_token", lambda t: (42, "Botty") if t == TOKEN else (_ for _ in ()).throw(
        js.JumpstartProblem("Discord rejected that token.")))
    monkeypatch.setattr(js, "app_info", app_info)
    monkeypatch.setattr(js, "login", login)
    state["apps"] = [js.App(42, (URI,), INTENT)]
    return proj, cfg, team, state


def test_full_run_links_before_folder(env, monkeypatch, capsys):
    proj, cfg, team, state = env
    state["apps"] = [js.App(42, (), 0), js.App(42, (), INTENT), js.App(42, (URI,), INTENT)]
    # agent name, Enter (intent saved), Enter (redirect saved), choice 1, folder, .mcp.json? no,
    # trust bot? yes, haiku x2: no
    script = Script(["alice-agent", "", "", "1", str(proj), "n", "y", "n", "n"])
    monkeypatch.setattr("builtins.input", script)
    monkeypatch.setattr(js.getpass, "getpass", lambda prompt="": TOKEN)
    assert js.run_jumpstart(cfg, None, open_browser=False) == 0
    data = tomllib.loads(cfg.read_text())
    assert data["bot_token"] == TOKEN and data["agent_name"] == "alice-agent"
    assert data["linked_user_id"] == 7 and data["linked_user_name"] == "Alice"
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert "/42/bot" in out.out and "/42/oauth2#:~:text=" in out.out and URI in out.out
    # the login happened before the working directory was asked for
    assert state["logins"] == 1
    assert script.prompts.index(next(p for p in script.prompts if "Choose 1 or 2" in p)) == 3
    t = tomllib.loads(team.read_text())
    assert 42 in t["trusted_user_ids"] and t["haiku_defense"] is False and t["haiku_preflight"] is False
    assert {"agent": "alice-agent", "bot_id": 42, "user_id": 7, "name": "Alice"} in t["members"]


def test_add_folder_later_then_resume(env, monkeypatch, capsys):
    proj, cfg, team, state = env
    monkeypatch.setattr(js.getpass, "getpass", lambda prompt="": TOKEN)
    monkeypatch.setattr("builtins.input", Script(["alice-agent", "2"]))
    assert js.run_jumpstart(cfg, None, open_browser=False) == 0
    assert "add a working directory" in capsys.readouterr().out
    assert tomllib.loads(cfg.read_text())["linked_user_id"] == 7
    # second run: token and link are remembered, it goes straight to the working directory
    monkeypatch.setattr(js.getpass, "getpass", lambda prompt="": pytest.fail("token asked again"))
    script = Script(["1", str(proj), "n", "y", "n", "n"])
    monkeypatch.setattr("builtins.input", script)
    assert js.run_jumpstart(cfg, None, open_browser=False) == 0
    out = capsys.readouterr().out
    assert "Welcome back" in out and "linked as" in out and state["logins"] == 1
    assert "Choose 1 or 2" in script.prompts[0]


def test_creates_coordbot_folder_then_invites(tmp_path, monkeypatch, capsys):
    """No .coordbot/: offer to create it, ask channel + required, and show the invite only after that."""
    proj, cfg = tmp_path / "My Game", tmp_path / "home" / "config.toml"
    (proj / ".git").mkdir(parents=True)
    js.write_settings(cfg, bot_token=TOKEN, agent_name="alice-agent", linked_user_id=7, linked_user_name="Alice")
    monkeypatch.setattr(js, "check_token", lambda t: (42, "Botty"))
    monkeypatch.setattr(js, "app_info", lambda t: js.App(42, (URI,), INTENT))
    seen = {"n": 0}

    async def doctor(path, project, own_id=None):
        seen["n"] += 1
        if seen["n"] == 1:
            return False, [Line("Discord: the bot is not allowed to see that channel.", "err")]
        return True, [Line("Discord: connected, and the bot can see the channel.", "ok")]

    monkeypatch.setattr(js, "doctor_report", doctor)
    # choice 1, folder (dragged in, escaped), create? yes, channel, required? yes, .mcp.json? yes,
    # Enter (invited), haiku x2: yes / no
    script = Script(["1", str(proj).replace(" ", "\\ "), "y", "777", "y", "y", "", "y", "n"])
    monkeypatch.setattr("builtins.input", script)
    monkeypatch.setattr(js, "show_link", lambda url, ob: script.prompts.append(f"OPEN {url}"))
    assert js.run_jumpstart(cfg, None, open_browser=False) == 0
    team = tomllib.loads((proj / ".coordbot" / "team.toml").read_text())
    assert team["project"] == "My-Game" and team["channel_id"] == 777 and team["required"] is True
    assert team["trusted_user_ids"] == [42] and team["haiku_defense"] is True and team["haiku_preflight"] is False
    assert team["members"] == [{"agent": "alice-agent", "bot_id": 42, "user_id": 7, "name": "Alice"}]
    assert json.loads((proj / ".mcp.json").read_text())["mcpServers"]["coordbot"] == js.MCP_ENTRY
    invite = next(i for i, p in enumerate(script.prompts) if p.startswith("OPEN") and "oauth2/authorize" in p)
    channel = next(i for i, p in enumerate(script.prompts) if "channel id" in p)
    assert channel < invite
    out = capsys.readouterr().out
    assert "doesn't exist" in out and ".coordbot/team.toml" in out and ".mcp.json" in out


def test_declining_folder_uses_personal_channel(env, monkeypatch, capsys):
    _, cfg, _, _ = env
    solo = cfg.parent.parent / "solo"
    (solo / ".git").mkdir(parents=True)
    js.write_settings(cfg, bot_token=TOKEN, agent_name="alice-agent", linked_user_id=7)
    monkeypatch.setattr("builtins.input", Script(["1", str(solo), "n", "555"]))
    assert js.run_jumpstart(cfg, None, open_browser=False) == 0
    assert not (solo / ".coordbot").exists()
    assert tomllib.loads(cfg.read_text())["channel_id"] == 555


def test_folder_must_contain_git(tmp_path, monkeypatch, capsys):
    plain, repo = tmp_path / "plain", tmp_path / "repo"
    plain.mkdir()
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr("builtins.input", Script([str(tmp_path / "missing"), str(plain), str(repo)]))
    assert js.ask_folder(None) == repo.resolve()
    out = capsys.readouterr().out
    assert "is not a folder" in out and "has no .git folder" in out


def test_ctrl_c_exits_cleanly(env, monkeypatch, capsys):
    _, cfg, _, _ = env

    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(js.getpass, "getpass", interrupt)
    assert js.run_jumpstart(cfg, None, open_browser=False) == 0
    assert "Stopped" in capsys.readouterr().out
    assert not cfg.exists()


def test_set_line_keeps_comments():
    text = '# c\nbot_token = "x"  \nagent_name = "y"\n'
    out = js.set_line(js.set_line(text, "bot_token", '"a\\"b"'), "agent_name", '"z"')
    assert out.startswith("# c\n") and tomllib.loads(out) == {"bot_token": 'a"b', "agent_name": "z"}


def test_clean_name():
    assert js.clean_name("My Bot!") == "My-Bot" and js.clean_name("!!!") == "my-agent"


def test_clean_path_handles_quotes_and_escapes():
    assert str(js.clean_path('"/a b/c"')) == "/a b/c"
    assert str(js.clean_path("'/x/y'")) == "/x/y"
    if js.os.name != "nt":
        assert str(js.clean_path("/My\\ Game")) == "/My Game"


# ---------- the C / Enter link prompt ----------

def keys(monkeypatch, seq):
    it = iter(seq)
    monkeypatch.setattr(links, "interactive", lambda: True)
    monkeypatch.setattr(links, "read_key", lambda: next(it))


def test_offer_link_copies_then_opens(monkeypatch, capsys):
    copied, opened = [], []
    keys(monkeypatch, ["x", "C", "\r"])
    monkeypatch.setattr(links, "copy", lambda t: copied.append(t) or True)
    monkeypatch.setattr(links.webbrowser, "open", lambda u: opened.append(u))
    assert links.offer_link("https://example.com/a") is True
    assert copied == opened == ["https://example.com/a"]
    out = capsys.readouterr().out
    assert "[C] copy" in out and "Copied." in out


def test_offer_link_skip_and_no_clipboard(monkeypatch, capsys):
    keys(monkeypatch, ["c", "s"])
    monkeypatch.setattr(links, "copy", lambda t: False)
    monkeypatch.setattr(links.webbrowser, "open", lambda u: pytest.fail("opened"))
    assert links.offer_link("https://example.com/b") is False
    assert "select the text above" in capsys.readouterr().out


def test_offer_link_not_interactive_only_prints(monkeypatch, capsys):
    monkeypatch.setattr(links, "interactive", lambda: False)
    monkeypatch.setattr(links, "read_key", lambda: pytest.fail("read a key"))
    assert links.offer_link("https://example.com/c") is False
    assert "https://example.com/c" in capsys.readouterr().out


def test_offer_copy_waits_for_enter(monkeypatch):
    copied = []
    keys(monkeypatch, ["c", "\n"])
    monkeypatch.setattr(links, "copy", lambda t: copied.append(t) or True)
    links.offer_copy(URI)
    assert copied == [URI]


def test_login_server_listens_before_link_is_shown():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    reached = []

    def present(url):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        reached.append(url)
        raise KeyboardInterrupt  # the user stops: the server must be closed again

    with pytest.raises(KeyboardInterrupt):
        oauth.link_user(1, port, present=present)
    assert reached and "redirect_uri=" in reached[0]
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))  # free again
