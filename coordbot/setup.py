"""`coordbot setup`: create the personal config (~/.coordbot/config.toml) and open it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from . import ui
from .config import ConfigError, default_config_path, load_config


class Line(str):
    """A plain text line that also carries a status tag (ok / warn / err / info) for the CLI to color."""

    tag: str = "info"

    def __new__(cls, text: str, tag: str = "info") -> "Line":
        obj = super().__new__(cls, text)
        obj.tag = tag
        return obj


def _template() -> str:
    try:
        return resources.files("coordbot").joinpath("config.example.toml").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return (Path(__file__).resolve().parent.parent / "config.example.toml").read_text(encoding="utf-8")


def _open_in_editor(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-t", str(path)], check=False)
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        elif shutil.which("xdg-open"):
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


def write_config(path: Path) -> None:
    """Create the personal config from the template, readable only by you."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_template(), encoding="utf-8")
    try:
        path.chmod(0o600)  # only you can read the token
    except OSError:
        pass


def run_setup(path: Path | None = None) -> int:
    path = (path or default_config_path()).expanduser()
    if path.is_file():
        print(ui.info(f"Your settings already exist: {ui.value(path)}"))
        try:
            cfg = load_config(path)
            print(ui.ok(f"They look fine (agent name: {ui.value(cfg.agent_name)})."))
            return 0
        except ConfigError as exc:
            print(ui.err(str(exc)))
    else:
        write_config(path)
        print(ui.ok(f"Created {ui.value(path)}"))
    print("Opening it now. Paste your bot token and pick an agent_name, then save.")
    print(ui.warn("NEVER share or commit this file: it holds your bot's secret token."))
    _open_in_editor(path)
    return 0


async def doctor_report(config_path: Path | None = None, project: Path | None = None,
                        own_id: int | None = None) -> tuple[bool, list[Line]]:
    """Check the whole setup. Returns (ready, lines). Never includes the token."""
    from .config import find_team_file, load_team
    from .discord_client import DiscordProblem, RestSession, invite_url

    lines: list[Line] = []
    ready = True
    path = (config_path or default_config_path()).expanduser()
    try:
        team = load_team(project)
    except ConfigError as exc:
        return False, [Line(str(exc), "err")]
    if team is None:
        lines.append(Line("No .coordbot/team.toml found in this project (or its parents): this project doesn't "
                          "use coordbot, or you are in the wrong folder.", "warn"))
    else:
        lines.append(Line(f"Team file: {team.path} (project {team.project}, channel {team.channel_id}, "
                          f"required={str(team.required).lower()}).", "ok"))
    if not path.is_file():
        install = team.install if team else "https://github.com/luaily/ClaudeCoordbot"
        return False, lines + [Line(f"No personal settings at {path}. Run `coordbot setup`, paste your bot token "
                                    f"and agent_name. Install guide: {install}", "err")]
    try:
        cfg = load_config(path, project)
    except ConfigError as exc:
        return False, lines + [Line(str(exc), "err")]
    lines.append(Line(f"Personal settings: {path} (agent {cfg.agent_name}, idle_minutes {cfg.idle_minutes}).", "ok"))
    if own_id is None:
        session = RestSession(cfg)
        try:
            await session.open()
            own_id = session.own_id
            lines.append(Line("Discord: connected, and the bot can see the channel.", "ok"))
        except DiscordProblem as exc:
            ready = False
            lines.append(Line(f"Discord: {exc}", "err"))
        finally:
            await session.close()
    if own_id:
        lines.append(Line(f"Your bot's id: {own_id}. Invite link (includes thread permissions): {invite_url(own_id)}"))
        if team is not None and own_id not in team.trusted_user_ids:
            ready = False
            lines.append(Line(f"Your bot id {own_id} is NOT in trusted_user_ids in {team.path}: teammates ignore your "
                              "posts. Add it there, commit and push it (tell your user first).", "err"))
    lines.append(Line("The bot's role needs: View Channel, Send Messages, Read Message History, "
                      "Add Reactions, Create Public Threads, Send Messages in Threads."))
    return ready, lines

def render_report(ready: bool, lines: list[Line]) -> str:
    """The doctor report as terminal text (colored for humans, compact for --agent)."""
    tail = ui.ok("Ready.") if ready else ui.err("Not ready yet: fix the items above.")
    return ui.lines([ui.header("coordbot doctor"), *(ui.tagged(getattr(l, "tag", "info"), l) for l in lines),
                     ui.blank(), tail])
