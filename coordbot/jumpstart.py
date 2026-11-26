"""`coordbot jumpstart`: a guided first run. Safe to run again: finished steps are skipped, so a
second run goes straight to adding a working directory.

Order: create the bot, paste its token, verify the user (Discord OAuth2), then the project folder,
its .coordbot/ folder, and only then the invite (before it has a channel the bot can't do anything).
Never prints the bot token. The personal config is written with mode 600.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import links, ui
from .config import (DEFAULT_OAUTH_PORT, PLACEHOLDER_TOKEN, TEAM_DIR, TEAM_FILE, ConfigError, Member, _NAME_RE,
                     default_config_path, find_team_file, load_team)
from .discord_client import invite_url
from .oauth import (ME_URL, USER_AGENT, DiscordUser, LinkProblem, _toml_str, add_trusted_user, link_user,
                    redirect_uri, set_team_bool, upsert_member)
from .setup import doctor_report, render_report, write_config

PORTAL = "https://discord.com/developers/applications"
APP_URL = "https://discord.com/api/v10/applications/@me"
# Scrolls the OAuth2 page to its "Add Redirect" button (a text fragment; harmless if the text changes).
ADD_REDIRECT_FRAGMENT = "#:~:text=you%20enter%20here.-,Add%20Redirect,-OAuth2%20URL%20Generator"
MESSAGE_CONTENT_FLAGS = (1 << 18) | (1 << 19)  # GATEWAY_MESSAGE_CONTENT (+ _LIMITED)
TOTAL = 6
MCP_ENTRY = {"command": "uvx", "args": ["--from", "git+https://github.com/luaily/ClaudeCoordbot", "coordbot", "mcp"]}


class JumpstartProblem(Exception):
    pass


@dataclass(frozen=True)
class App:
    id: int
    redirect_uris: tuple[str, ...]
    flags: int


# ---------- Discord ----------

def _get(url: str, token: str) -> dict[str, object]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - fixed https URL
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise JumpstartProblem("Discord rejected that token. Reset it in the Developer Portal (Bot page) "
                                   "and copy the new one.") from None
        raise JumpstartProblem(f"Discord answered with an error (HTTP {exc.code}).") from None
    except (OSError, ValueError) as exc:
        raise JumpstartProblem(f"Could not reach Discord: {exc}") from None


def check_token(token: str) -> tuple[int, str]:
    """Ask Discord who this token belongs to. Returns (bot id, bot name)."""
    me = _get(ME_URL, token)
    return int(me["id"]), str(me.get("username") or "")  # type: ignore[arg-type]


def app_info(token: str) -> App:
    """The bot's application: its id (the CLIENT_ID), registered OAuth2 redirects and intent flags."""
    app = _get(APP_URL, token)
    uris = app.get("redirect_uris") or []
    return App(int(app["id"]), tuple(str(u) for u in uris if isinstance(u, str)),  # type: ignore[arg-type]
               int(app.get("flags") or 0))  # type: ignore[arg-type]


def login(app_id: int, port: int, secret: str, open_browser: bool) -> DiscordUser:
    """The Discord OAuth2 login (identify only), served back to 127.0.0.1."""
    present = links.offer_link if (open_browser and links.interactive()) else None
    return link_user(app_id, port, secret, 300, open_browser and present is None, print, present)


# ---------- files ----------

def clean_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")[:40]
    return n if _NAME_RE.match(n or "-") and n else "my-agent"


def project_name(folder: Path) -> str:
    n = re.sub(r"[^A-Za-z0-9_.-]+", "-", folder.name).strip("-")[:40]
    return n if n and _NAME_RE.match(n) else "project"


def set_line(text: str, key: str, rendered: str) -> str:
    """Replace the `key = ...` line (or add it), leaving every comment alone."""
    pattern = re.compile(rf"(?m)^{re.escape(key)}[ \t]*=.*$")
    line = f"{key} = {rendered}"
    return pattern.sub(lambda _m: line, text, count=1) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"


def write_settings(path: Path, **values: object) -> None:
    """Set top-level keys in the personal config (created from the template if missing). Mode 600."""
    if not path.is_file():
        write_config(path)
    text = path.read_text(encoding="utf-8")
    for key, val in values.items():
        text = set_line(text, key, _toml_str(val) if isinstance(val, str) else str(val))
    tomllib.loads(text)  # never write a broken file
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read(path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def clean_path(raw: str) -> Path:
    """A pasted or dragged-in folder path: drop surrounding quotes and (macOS/Linux) backslash escapes."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    if os.name != "nt":
        s = re.sub(r"\\(.)", r"\1", s)  # Terminal drag and drop writes "My\ Project"
    return Path(s).expanduser()


def _team_template() -> str:
    from importlib import resources

    try:
        return resources.files("coordbot").joinpath("team.example.toml").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return (Path(__file__).resolve().parent.parent / "team.example.toml").read_text(encoding="utf-8")


def create_team(folder: Path, channel: int, required: bool, bot_id: int) -> Path:
    """Write <folder>/.coordbot/team.toml from the template, trusting this bot."""
    team_path = folder / TEAM_DIR / TEAM_FILE
    team_path.parent.mkdir(parents=True, exist_ok=True)
    text = _team_template()
    text = set_line(text, "project", _toml_str(project_name(folder)))
    text = set_line(text, "channel_id", str(channel))
    text = set_line(text, "required", "true" if required else "false")
    text = set_line(text, "trusted_user_ids", f"[{bot_id}]")
    tomllib.loads(text)
    team_path.write_text(text, encoding="utf-8")
    return team_path


def ensure_mcp_json(folder: Path) -> bool:
    """Offer to add coordbot to <folder>/.mcp.json, so the agent starts it whenever it opens the
    project. True if the file changed."""
    path = folder / ".mcp.json"
    data: object = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(ui.warn(f"Could not read {path}: add the coordbot entry by hand (see the README)."))
            return False
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(data, dict) or (servers is not None and not isinstance(servers, dict)):
            print(ui.warn(f"{path} has an unexpected shape: add the coordbot entry by hand (see the README)."))
            return False
        if isinstance(servers, dict) and "coordbot" in servers:
            return False
    if not confirm(f"Add coordbot to {path.name}, so your agent starts it every time it opens this project?"):
        return False
    assert isinstance(data, dict)
    data.setdefault("mcpServers", {})["coordbot"] = MCP_ENTRY
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(ui.ok(f"Added coordbot to {ui.value(path)}."))
    return True


# ---------- prompts ----------

def ask(prompt: str, default: str = "") -> str:
    tail = f" [{default}]" if default else ""
    return input(f"{prompt}{tail}: ").strip() or default


def confirm(prompt: str, default: bool = True) -> bool:
    answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def wait(msg: str = "Press Enter when you are done") -> None:
    input(ui.dim(f"{msg}... "))


def show_link(url: str, open_browser: bool) -> None:
    """Interactive terminal: C copies, Enter opens. Otherwise the link is just printed."""
    if open_browser:
        links.offer_link(url)
    else:
        print("  " + ui.value(url))


def show_copy(text: str, done: str) -> None:
    """Interactive terminal: C copies, Enter continues. Otherwise print it and wait for Enter."""
    if links.interactive():
        links.offer_copy(text, done)
    else:
        print("  " + ui.value(text))
        wait(f"Press Enter to {done}")


def ask_channel() -> int:
    print("  In Discord: User Settings > Advanced > Developer Mode ON, then right-click the coordination "
          "channel > Copy Channel ID.")
    while True:
        raw = ask("Coordination channel id")
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        print(ui.warn("The channel id is a number like 123456789012345678."))


def ask_folder(default: Path | None) -> Path:
    print("Paste the folder where your code lives: the git repo you already run your AI agent in.")
    print(ui.dim("Tip: you can drag the folder into this window."))
    suggestion = str((default or Path.cwd()).expanduser().resolve())
    while True:
        folder = clean_path(ask("Working directory", suggestion))
        if not folder.is_dir():
            print(ui.warn(f"{folder} is not a folder. Check the path and try again."))
        elif not (folder / ".git").exists():
            print(ui.warn(f"{folder} has no .git folder. Pick the top folder of your git repository."))
        else:
            return folder.resolve()


def head(n: int, title: str) -> None:
    print(ui.lines([ui.blank(), ui.step(n, TOTAL, title)]))


# ---------- the wizard ----------

def run_jumpstart(config_path: Path | None = None, project: Path | None = None, open_browser: bool = True) -> int:
    try:
        return _run((config_path or default_config_path()).expanduser(), project, open_browser)
    except (KeyboardInterrupt, EOFError):
        print("\n" + ui.info("Stopped. Run `coordbot jumpstart` again to continue where you left off."))
        return 0


def _saved_bot(path: Path) -> tuple[int, str] | None:
    """The bot behind the saved token, if a real token is saved and Discord accepts it."""
    token = _read(path).get("bot_token")
    if not isinstance(token, str) or not token.strip() or token.strip() == PLACEHOLDER_TOKEN:
        return None
    try:
        return check_token(token.strip())
    except JumpstartProblem:
        return None


def _saved_user(path: Path) -> DiscordUser | None:
    data = _read(path)
    uid, name = data.get("linked_user_id"), data.get("linked_user_name")
    return DiscordUser(uid, name if isinstance(name, str) else "") if isinstance(uid, int) and uid > 0 else None


def _run(path: Path, project: Path | None, open_browser: bool) -> int:
    bot, user = _saved_bot(path), _saved_user(path)
    print(ui.header("coordbot jumpstart"))
    if bot and user:
        print("Welcome back. Your bot and your Discord account are set up, so let's add a working directory.")
    else:
        print("Let's get your agent connected. You can stop any time: running this again picks up where you left off.")

    # 1: create the bot
    head(1, "Create your Discord bot")
    if bot:
        print(ui.ok(f"Already done: bot {ui.value(bot[1])} (id {ui.value(bot[0])})."))
    else:
        print("In the Discord Developer Portal:")
        print("  1. Click New Application and give it a name.")
        print("  2. Open Bot, switch Message Content Intent ON, and click Save Changes.")
        print("  3. On the same page, click Reset Token, then Copy.")
        show_link(PORTAL, open_browser)

    # 2: the token and the agent name
    head(2, "Paste your bot token")
    if bot:
        print(ui.ok("Already done: your token is saved and works."))
    else:
        for _ in range(3):
            token = getpass.getpass("Bot token (hidden, nothing shows as you paste): ").strip()
            if not token:
                print(ui.warn("No token entered."))
                return 1
            try:
                bot = check_token(token)
                break
            except JumpstartProblem as exc:
                print(ui.err(str(exc)))
        else:
            print(ui.err("Giving up after 3 tries. Run `coordbot jumpstart` again."))
            return 1
        print(ui.ok(f"Token works: bot {ui.value(bot[1])} (id {ui.value(bot[0])})."))
        write_settings(path, bot_token=token)
    bot_id, bot_name = bot
    existing = _read(path).get("agent_name")
    if isinstance(existing, str) and _NAME_RE.match(existing) and existing != "my-agent":
        agent = existing
        print(ui.ok(f"Agent name: {ui.value(agent)}"))
    else:
        while True:
            agent = ask("Name for your agent (letters, digits, - _ .)", clean_name(bot_name))
            if _NAME_RE.match(agent):
                break
            print(ui.warn("Use only letters, digits, - _ . (max 40 characters)."))
        write_settings(path, agent_name=agent)
        print(ui.ok(f"Saved {ui.value(path)} (only you can read it)."))
        print(ui.warn("Never share or commit that file: it holds your bot's secret token."))
    token = str(_read(path).get("bot_token", "")).strip()
    try:
        app: App | None = app_info(token)
    except JumpstartProblem:
        app = None
    while app is not None and not app.flags & MESSAGE_CONTENT_FLAGS:
        print(ui.warn("Message Content Intent is off, so your bot can't read the channel. On the Bot page, "
                      "switch it ON and click Save Changes:"))
        show_link(f"{PORTAL}/{app.id}/bot", open_browser)
        wait("Press Enter once it is saved")
        try:
            app = app_info(token)
        except JumpstartProblem:
            app = None
        if app is not None and not app.flags & MESSAGE_CONTENT_FLAGS and not confirm("Still off. Check again?"):
            break
    app_id = app.id if app is not None else bot_id

    # 3: verify the user (Discord OAuth2)
    head(3, "Verify you (link your Discord account)")
    if user:
        print(ui.ok(f"Already done: linked as {ui.value(user.name or user.id)}."))
    else:
        data = _read(path)
        port = data.get("oauth_port") if isinstance(data.get("oauth_port"), int) else DEFAULT_OAUTH_PORT
        secret = data.get("client_secret") if isinstance(data.get("client_secret"), str) else ""
        uri = redirect_uri(port)  # type: ignore[arg-type]
        if app is not None and uri in app.redirect_uris:
            print(ui.ok("The login redirect is already registered."))
        else:
            print("Discord needs to know where to send you back after you log in.")
            print("On your bot's OAuth2 page, click Add Redirect:")
            show_link(f"{PORTAL}/{app_id}/oauth2{ADD_REDIRECT_FRAGMENT}", open_browser)
            print("Paste this address there, then click Save Changes:")
            show_copy(uri, "continue once it is saved")
            while app is not None:
                try:
                    app = app_info(token)
                except JumpstartProblem:
                    break  # can't check: the login below tells us
                if uri in app.redirect_uris:
                    print(ui.ok("Discord has the redirect."))
                    break
                print(ui.warn("Discord doesn't have that redirect yet. Did you click Save Changes?"))
                show_copy(uri, "check again")
        print("Now log in: Discord asks you to let coordbot know who you are (identify only). Click Authorize.")
        while True:
            try:
                user = login(app_id, port, secret, open_browser)  # type: ignore[arg-type]
                break
            except LinkProblem as exc:
                print(ui.err(str(exc)))
                if not confirm("Linking didn't finish. Try again?"):
                    print(ui.info("Run `coordbot jumpstart` again to continue from here."))
                    return 1
        write_settings(path, linked_user_id=user.id, linked_user_name=user.name)
        print(ui.ok(f"Verified: you are {ui.value(user.name or user.id)} on Discord."))

    # 4: the working directory
    head(4, "Your working directory")
    print("  1. Specify a working directory now (it must contain a git repository)")
    print("  2. Add a working directory later (run `coordbot jumpstart` again)")
    while (choice := ask("Choose 1 or 2", "1")) not in ("1", "2"):
        print(ui.warn("Type 1 or 2."))
    if choice == "2":
        _summary(agent, bot_name, bot_id, user, path, None, None, [])
        print(ui.info("Run `coordbot jumpstart` again when you want to add a working directory."))
        return 0
    folder = ask_folder(project)
    changed: list[Path] = []
    created = False
    found = find_team_file(folder)
    if found is not None:
        where = "" if found.parent.parent == folder else f" (in {ui.value(found.parent.parent)})"
        print(ui.ok(f"Found {ui.value(found)}{where}."))
    else:
        has_dir = (folder / TEAM_DIR).is_dir()
        print(ui.warn(f"{folder / TEAM_DIR} " + ("has no team.toml." if has_dir else "doesn't exist.")
                      + " This project isn't set up for coordbot yet."))
        if confirm("Create it now? (Say no if a teammate already set it up: pull their changes first.)"):
            channel = ask_channel()
            required = confirm("Make coordbot required here? (agents without it stop and ask their user to "
                               "set it up)")
            changed.append(create_team(folder, channel, required, bot_id))
            created = True
            print(ui.ok(f"Created {ui.value(changed[-1])} (channel {ui.value(channel)}, "
                        f"required {str(required).lower()}, your bot trusted)."))
    try:
        team = load_team(folder)
    except ConfigError as exc:
        print(ui.err(str(exc)))
        return 1
    if team is not None:
        print(ui.ok(f"Project {ui.value(team.project)} coordinates in channel {ui.value(team.channel_id)}."))
        try:
            if upsert_member(team.path, Member(agent, bot_id, user.id, user.name)):
                changed.append(team.path)
                print(ui.ok(f"Added you ({ui.value(user.name or user.id)}, agent {ui.value(agent)}) to the team."))
        except LinkProblem as exc:
            print(ui.err(str(exc)))
        if ensure_mcp_json(team.root):
            changed.append(team.root / ".mcp.json")
    else:
        current = _read(path).get("channel_id")
        if isinstance(current, int) and current > 0:
            print(ui.ok(f"Using the channel from your settings: {ui.value(current)}."))
        else:
            print(ui.info("Without a .coordbot folder, coordbot uses a channel from your personal settings."))
            write_settings(path, channel_id=ask_channel())

    # 5: invite the bot (only now: before it has a channel it can't do anything)
    head(5, "Invite your bot to the server")
    while True:
        ready, lines = asyncio.run(doctor_report(path, folder, own_id=None))
        if not any(getattr(l, "tag", "") == "err" and l.startswith("Discord:") for l in lines):
            print(ui.ok("Your bot can see the channel."))
            break
        print("Open this link, pick your server and click Authorize:")
        show_link(invite_url(app_id), open_browser)
        wait("Press Enter once the bot is invited")
        ready, lines = asyncio.run(doctor_report(path, folder, own_id=None))
        if not any(getattr(l, "tag", "") == "err" and l.startswith("Discord:") for l in lines):
            print(ui.ok("Your bot can see the channel."))
            break
        print(render_report(ready, lines))
        if not confirm("The bot still can't see the channel. Try again?"):
            break

    # 6: finish
    head(6, "Finish")
    try:
        team = load_team(folder)
    except ConfigError:
        team = None
    if team is not None:
        if bot_id not in team.trusted_user_ids and confirm(
                f"Add your bot id {bot_id} to trusted_user_ids in {team.path.name}?"):
            try:
                if add_trusted_user(team.path, bot_id):
                    changed.append(team.path)
            except LinkProblem as exc:
                print(ui.err(str(exc)))
        for key, label in (("haiku_defense", "a Haiku subagent defends your choices at the end of each task"),
                           ("haiku_preflight", "a Haiku subagent checks lint and merge problems before a push")):
            if created or getattr(team, key) is None:
                on = confirm(f"Turn on {key}? ({label})", default=False)
                if set_team_bool(team.path, key, on) or created:
                    changed.append(team.path)
    _summary(agent, bot_name, bot_id, user, path, folder, team, changed)
    print(ui.ok("All set. Open this folder with your AI agent and allow the coordbot MCP server."))
    return 0


def _summary(agent: str, bot_name: str, bot_id: int, user: DiscordUser, path: Path, folder: Path | None,
             team: object, changed: list[Path]) -> None:
    print(ui.kv("agent", agent))
    print(ui.kv("bot", f"{bot_name} ({bot_id})"))
    print(ui.kv("you", f"{user.name or '?'} ({user.id})"))
    print(ui.kv("settings", path))
    if folder is not None:
        print(ui.kv("working directory", folder))
    root = getattr(team, "root", None)
    if root is not None:
        print(ui.kv("team file", team.path))  # type: ignore[attr-defined]
        if changed:
            names = sorted({str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in changed})
            print(ui.warn(f"Commit and push {', '.join(names)} so teammates get these changes."))
