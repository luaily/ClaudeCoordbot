"""Link the human who runs this agent to their Discord account (OAuth2 + PKCE, local only).

The browser goes to Discord, the user clicks Authorize, and Discord redirects to a one-shot
server on http://127.0.0.1:<oauth_port>/callback. The code is exchanged with PKCE (no client
secret needed), /users/@me gives the user's id and name, and the token is thrown away.
Only the scope `identify` is requested: coordbot learns who you are, nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import threading
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .config import Member

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
ME_URL = "https://discord.com/api/v10/users/@me"
USER_AGENT = "coordbot (https://github.com/luaily/ClaudeCoordbot)"


class LinkProblem(Exception):
    pass


@dataclass(frozen=True)
class DiscordUser:
    id: int
    name: str


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def setup_hint(app_id: int, port: int) -> str:
    return (f"In the Discord Developer Portal, open your bot's application ({app_id}) -> OAuth2 -> "
            f"Redirects -> Add Redirect, paste exactly {redirect_uri(port)} and click Save Changes. "
            "Then try again.")


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(app_id: int, port: int, state: str, challenge: str) -> str:
    q = {"client_id": str(app_id), "response_type": "code", "redirect_uri": redirect_uri(port),
         "scope": "identify", "state": state, "prompt": "consent",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(q)


def _post_form(url: str, form: dict[str, str]) -> dict[str, object]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - fixed https URL
        return json.loads(r.read())


def exchange(app_id: int, port: int, code: str, verifier: str, client_secret: str = "") -> str:
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(port),
            "client_id": str(app_id), "code_verifier": verifier}
    try:
        data = _post_form(TOKEN_URL, form)
    except urllib.error.HTTPError as exc:
        if not client_secret:
            raise LinkProblem(f"Discord refused the login code (HTTP {exc.code}). If this keeps happening, "
                              "add client_secret (OAuth2 page -> Client Secret) to your personal "
                              "settings and try again.") from None
        try:
            data = _post_form(TOKEN_URL, {**form, "client_secret": client_secret})
        except urllib.error.HTTPError as exc2:
            raise LinkProblem(f"Discord refused the login code (HTTP {exc2.code}).") from None
    except OSError as exc:
        raise LinkProblem(f"Could not reach Discord: {exc}") from None
    token = data.get("access_token")
    if not isinstance(token, str):
        raise LinkProblem("Discord did not return an access token.")
    return token


def fetch_me(token: str) -> DiscordUser:
    req = urllib.request.Request(ME_URL, headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - fixed https URL
            me = json.loads(r.read())
    except (OSError, ValueError) as exc:
        raise LinkProblem(f"Could not read your Discord account: {exc}") from None
    return DiscordUser(int(me["id"]), str(me.get("global_name") or me.get("username") or ""))


_PAGE = ("<!doctype html><meta charset=utf-8><title>coordbot</title>"
         "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto'><h2>{title}</h2><p>{body}</p>")


def _callback_server(port: int, state: str) -> tuple[HTTPServer, dict[str, str], threading.Event]:
    """Listen on 127.0.0.1:<port> for Discord's redirect (before the user ever sees the login link)."""
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep stdout clean (MCP)
            pass

        def do_GET(self) -> None:  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            if url.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(url.query)
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                result["code"] = q["code"][0]
            else:
                result["error"] = q.get("error_description", q.get("error", ["login was not completed"]))[0]
            page = _PAGE.format(
                title="Linked. You can close this tab." if ok else "Not linked",
                body="Go back to your agent." if ok else html.escape(result.get("error", "")))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())
            done.set()

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise LinkProblem(f"Port {port} on 127.0.0.1 is busy ({exc.strerror}). Close what uses it, or set "
                          "oauth_port in your personal settings (and register the matching redirect).") from None
    server.timeout = 1
    return server, result, done


def _wait_for_code(server: HTTPServer, result: dict[str, str], done: threading.Event, timeout: float) -> str:
    try:
        while not done.is_set() and timeout > 0:
            server.handle_request()
            timeout -= 1
    finally:
        server.server_close()
    if "code" in result:
        return result["code"]
    raise LinkProblem(f"Discord login did not finish: {result.get('error', 'timed out')}.")


def link_user(app_id: int, port: int, client_secret: str = "", timeout: float = 300,
              open_browser: bool = True, show: Callable[[str], None] = print,
              present: Callable[[str], object] | None = None) -> DiscordUser:
    """Run the whole browser login. Blocks until done or `timeout` seconds pass.
    `present` (an interactive terminal) shows the login link itself instead of `show` + auto-open."""
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    url = authorize_url(app_id, port, state, challenge)
    server, result, done = _callback_server(port, state)  # listening before the link is shown
    try:
        if present is not None:
            present(url)
        else:
            show(f"Opening Discord to link your account. If no browser opens, visit:\n{url}")
            if open_browser:
                try:
                    webbrowser.open(url)
                except Exception:  # noqa: BLE001
                    pass
    except BaseException:
        server.server_close()
        raise
    code = _wait_for_code(server, result, done, timeout)
    return fetch_me(exchange(app_id, port, code, verifier, client_secret))


# ---------- writing members into .coordbot/team.toml ----------

_BLOCK_RE = re.compile(r"(?ms)^members[ \t]*=[ \t]*\[.*?^\][ \t]*$")


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def render_members(members: list[Member]) -> str:
    lines = ["members = ["]
    for m in sorted(members, key=lambda m: m.agent.lower()):
        lines.append(f"  {{ agent = {_toml_str(m.agent)}, bot_id = {m.bot_id}, user_id = {m.user_id}, "
                     f"name = {_toml_str(m.name)} }},")
    lines.append("]")
    return "\n".join(lines)


def upsert_member(team_path: Path, new: Member) -> bool:
    """Add or update one member (matched by bot id or agent name). Returns True if the file changed."""
    text = team_path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    current = [Member(str(m.get("agent", "")), int(m.get("bot_id", 0)), int(m.get("user_id", 0)),
                      str(m.get("name", ""))) for m in data.get("members", []) if isinstance(m, dict)]
    kept = [m for m in current if m.bot_id != new.bot_id and m.agent.lower() != new.agent.lower()]
    if new in current and len(kept) == len(current) - 1:
        return False
    block = render_members([*kept, new])
    if _BLOCK_RE.search(text):
        text = _BLOCK_RE.sub(lambda _m: block, text, count=1)
    elif re.search(r"(?m)^members\s*=", text):
        raise LinkProblem(f"Could not update `members` in {team_path}: write it as a multi-line list.")
    else:
        text = text.rstrip("\n") + (
            "\n\n# Teammates linked with `coordbot link`: their agent's bot and their own Discord account.\n"
            "# Both are trusted, and agents @mention the human when they need them.\n" + block + "\n")
    tomllib.loads(text)  # never write a broken file
    team_path.write_text(text, encoding="utf-8")
    return True


# ---------- other small edits to .coordbot/team.toml (comments and the rest stay as they are) ----------

def _check_and_write(team_path: Path, text: str) -> None:
    tomllib.loads(text)  # never write a broken file
    team_path.write_text(text, encoding="utf-8")


def set_team_bool(team_path: Path, key: str, val: bool) -> bool:
    """Set `key = true|false` in the team file: replace the line in place, or add it. True if changed."""
    text = team_path.read_text(encoding="utf-8")
    new = "true" if val else "false"
    line_re = re.compile(rf"(?m)^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)[^#\r\n]*?([ \t]*(?:#.*)?)$")
    if line_re.search(text):
        out = line_re.sub(lambda m: f"{m.group(1)}{new}{m.group(2)}", text, count=1)
    else:
        table = re.search(r"(?m)^\[", text)  # keep top-level keys above any [table]
        entry = f"{key} = {new}\n"
        if table:
            out = text[:table.start()] + entry + "\n" + text[table.start():]
        else:
            out = text.rstrip("\n") + "\n" + entry
    if out == text:
        return False
    _check_and_write(team_path, out)
    return True


def add_trusted_user(team_path: Path, user_id: int) -> bool:
    """Add an id to trusted_user_ids in the team file. True if the file changed."""
    text = team_path.read_text(encoding="utf-8")
    current = [int(i) for i in tomllib.loads(text).get("trusted_user_ids", []) if str(i).isdigit()]
    if user_id in current:
        return False
    ids = ", ".join(map(str, [*current, user_id]))
    list_re = re.compile(r"(?ms)^trusted_user_ids[ \t]*=[ \t]*\[(.*?)\]")
    found = list_re.search(text)
    if found and "#" in found.group(1):
        raise LinkProblem(f"Could not update trusted_user_ids in {team_path} (it has comments inside): "
                          f"add {user_id} by hand.")
    if found:
        out = list_re.sub(lambda _m: f"trusted_user_ids = [{ids}]", text, count=1)
    elif re.search(r"(?m)^trusted_user_ids\s*=", text):
        raise LinkProblem(f"Could not update trusted_user_ids in {team_path}: add {user_id} by hand.")
    else:
        out = text.rstrip("\n") + f"\ntrusted_user_ids = [{ids}]\n"
    _check_and_write(team_path, out)
    return True
