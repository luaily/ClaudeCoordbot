"""All Discord access: a short-lived REST session for agent commands and the gateway bot for `run`."""

from __future__ import annotations

import logging
import webbrowser
from typing import Any

import discord

from .config import Config
from .db import Database
from .ingest import ingest, ingest_many


class DiscordProblem(Exception):
    """A Discord failure with a human-readable explanation."""


# View Channel (1024) + Send Messages (2048) + Read Message History (65536)
INVITE_PERMISSIONS = 68608


def invite_url(application_id: int) -> str:
    """Link that adds this bot to a server with only the permissions it needs."""
    return (
        f"https://discord.com/oauth2/authorize?client_id={application_id}"
        f"&permissions={INVITE_PERMISSIONS}&scope=bot"
    )


def show_invite(application_id: int, reason: str, open_browser: bool = True) -> None:
    """Print the invite link in a box the user can't miss, and try to open it."""
    url = invite_url(application_id)
    line = "=" * 72
    print(f"\n{line}\n{reason}\n\nAdd the bot to your Discord server with this link:\n\n  {url}\n")
    print("Pick the server, click Authorize, then make sure the bot can see the coordination channel.")
    print(f"{line}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - printing the link is enough
            pass


async def application_id_of(client: discord.Client) -> int:
    """The app id for invite links (same as the bot user id for normal bot apps)."""
    if client.application_id:
        return client.application_id
    try:
        return (await client.application_info()).id
    except Exception:  # noqa: BLE001
        return client.user.id if client.user else 0


def to_record(m: discord.Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "author_id": m.author.id,
        "author_name": m.author.name,
        "webhook_id": m.webhook_id,
        "content": m.content or "",
        "embeds": [e.to_dict() for e in m.embeds],
        "ts": m.created_at.timestamp(),
    }


def explain(exc: BaseException) -> DiscordProblem:
    if isinstance(exc, discord.LoginFailure):
        return DiscordProblem("Discord rejected the bot token. Check bot_token in config.toml (README step 5).")
    if isinstance(exc, discord.Forbidden):
        return DiscordProblem("The bot is not allowed to see that channel. Invite the bot to the server and give it "
                              "View Channel, Send Messages and Read Message History there (README step 4).")
    if isinstance(exc, discord.NotFound):
        return DiscordProblem("Discord could not find that channel. Check channel_id in config.toml (README step 6).")
    if isinstance(exc, discord.PrivilegedIntentsRequired):
        return DiscordProblem("Message Content Intent is off. Enable it in the Developer Portal: your app -> Bot -> "
                              "Privileged Gateway Intents (README step 3).")
    if isinstance(exc, (discord.HTTPException, OSError)):
        return DiscordProblem(f"Could not talk to Discord ({type(exc).__name__}). Check your internet connection.")
    return DiscordProblem(f"Unexpected Discord problem: {type(exc).__name__}")


class RestSession:
    """Login + fetch channel + send/history over REST. No gateway connection."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = discord.Client(intents=discord.Intents.none())
        self.channel: Any = None
        self.own_id: int = 0

    async def open(self) -> None:
        try:
            await self.client.login(self.cfg.bot_token)
            assert self.client.user is not None
            self.own_id = self.client.user.id
            self.channel = await self.client.fetch_channel(self.cfg.channel_id)
        except DiscordProblem:
            raise
        except Exception as exc:  # noqa: BLE001 - converted to friendly message
            problem = explain(exc)
            if isinstance(exc, (discord.Forbidden, discord.NotFound)) and self.client.user is not None:
                app_id = await application_id_of(self.client)
                if app_id:
                    problem = DiscordProblem(f"{problem} Invite link: {invite_url(app_id)}")
            await self.close()
            raise problem from None

    async def close(self) -> None:
        try:
            await self.client.close()
        except Exception:  # noqa: BLE001
            pass

    async def fetch(self, after_id: int | None) -> list[dict[str, Any]]:
        try:
            if after_id:
                it = self.channel.history(limit=1000, after=discord.Object(id=after_id), oldest_first=True)
            else:
                it = self.channel.history(limit=200)
            return [to_record(m) async for m in it]
        except Exception as exc:  # noqa: BLE001
            raise explain(exc) from None

    async def send(self, content: str) -> dict[str, Any]:
        try:
            m = await self.channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            return to_record(m)
        except Exception as exc:  # noqa: BLE001
            raise explain(exc) from None


def run_gateway(cfg: Config, db: Database) -> None:
    """Long-running bot: record every trusted message in real time."""
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        assert client.user is not None
        db.set_meta("own_id", str(client.user.id))
        app_id = await application_id_of(client)
        channel = client.get_channel(cfg.channel_id)
        if channel is None:
            print(f"Connected as {client.user}, but cannot see channel {cfg.channel_id}.")
            if app_id:
                show_invite(app_id, "The bot isn't in that channel's server yet (or channel_id is wrong).")
            print("After inviting it, close this window and start the bot again.")
            return
        if app_id and not db.get_meta("invite_shown"):
            # First successful run: show the link once so it can be shared or reused.
            show_invite(app_id, "First run: here is your bot's invite link (save it for later).", open_browser=False)
        db.set_meta("invite_shown", "1")
        print(f"Connected as {client.user}, watching #{getattr(channel, 'name', cfg.channel_id)}. "
              "Leave this window open. Press Ctrl+C to stop.")
        try:
            recs = [to_record(m) async for m in channel.history(limit=200)]  # type: ignore[union-attr]
            ingest_many(db, cfg, recs, client.user.id)
        except discord.HTTPException as exc:
            print(f"Could not read past messages: {explain(exc)}")

    async def handle(m: discord.Message) -> None:
        if m.channel.id != cfg.channel_id or client.user is None:
            return
        rec = to_record(m)
        db.bump_last_seen(rec["id"])
        if ingest(db, cfg, rec, client.user.id):
            db.rebuild_locks(cfg.default_lock_ttl_minutes)

    @client.event
    async def on_message(m: discord.Message) -> None:
        await handle(m)

    @client.event
    async def on_message_edit(_before: discord.Message, after: discord.Message) -> None:
        await handle(after)

    try:
        client.run(cfg.bot_token, log_level=logging.WARNING)
    except (discord.LoginFailure, discord.PrivilegedIntentsRequired) as exc:
        raise explain(exc) from None
