"""All Discord access.

* RestSession: short-lived, REST only (CLI commands).
* GatewaySession: a live connection that records the channel in real time. Used by
  `coordbot run` and by the MCP server, which brings it online when an agent session
  starts and takes it offline after `idle_minutes` with no activity.
Both expose the same small interface used by the commands: fetch, fetch_thread, send,
create_thread, archive_thread.
Diagnostics go to stderr (stdout belongs to the MCP protocol in `coordbot mcp`).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import webbrowser
from typing import Any, Callable

import discord

from .config import Config
from .db import Database
from .ingest import ingest, ingest_many
from .status import pick_status

logging.getLogger("discord").setLevel(logging.WARNING)


class DiscordProblem(Exception):
    """A Discord failure with a human-readable explanation."""


def say(*parts: Any) -> None:
    print(*parts, file=sys.stderr, flush=True)


# View Channel (1<<10) + Send Messages (1<<11) + Read Message History (1<<16)
# + Create Public Threads (1<<35) + Send Messages in Threads (1<<38)
INVITE_PERMISSIONS = (1 << 10) | (1 << 11) | (1 << 16) | (1 << 35) | (1 << 38)
THREAD_NAME_MAX = 90


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
    say(f"\n{line}\n{reason}\n\nAdd the bot to your Discord server with this link:\n\n  {url}\n")
    say("Pick the server, click Authorize, then make sure the bot can see the coordination channel.")
    say(f"{line}\n")
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
    in_thread = isinstance(m.channel, discord.Thread)
    return {
        "id": m.id,
        "author_id": m.author.id,
        "author_name": m.author.name,
        "webhook_id": m.webhook_id,
        "content": m.content or "",
        "embeds": [e.to_dict() for e in m.embeds],
        "ts": m.created_at.timestamp(),
        "channel_id": m.channel.id,
        "thread_id": m.channel.id if in_thread else None,  # posted inside this thread
    }


def explain(exc: BaseException) -> DiscordProblem:
    if isinstance(exc, DiscordProblem):
        return exc
    if isinstance(exc, discord.LoginFailure):
        return DiscordProblem("Discord rejected the bot token. Check bot_token in ~/.coordbot/config.toml.")
    if isinstance(exc, discord.Forbidden):
        return DiscordProblem("The bot is not allowed to do that in the channel. Give its role View Channel, "
                              "Send Messages, Read Message History, Create Public Threads and Send Messages "
                              "in Threads there.")
    if isinstance(exc, discord.NotFound):
        return DiscordProblem("Discord could not find that channel or message. Check channel_id.")
    if isinstance(exc, discord.PrivilegedIntentsRequired):
        return DiscordProblem("Message Content Intent is off. Enable it in the Developer Portal: your app -> Bot -> "
                              "Privileged Gateway Intents.")
    if isinstance(exc, asyncio.TimeoutError):
        return DiscordProblem("Timed out connecting to Discord. Check your internet connection.")
    if isinstance(exc, (discord.HTTPException, OSError)):
        return DiscordProblem(f"Could not talk to Discord ({type(exc).__name__}). Check your internet connection.")
    return DiscordProblem(f"Unexpected Discord problem: {type(exc).__name__}")


def thread_name(text: str) -> str:
    name = " ".join(text.split()) or "task"
    return name if len(name) <= THREAD_NAME_MAX else name[: THREAD_NAME_MAX - 1] + "…"


class _ChannelOps:
    """Shared message operations once `self.channel` and `self.client` are set."""

    client: discord.Client
    channel: Any

    async def fetch(self, after_id: int | None) -> list[dict[str, Any]]:
        return await self._history(self.channel, after_id)

    async def fetch_thread(self, thread_id: int, after_id: int | None = None) -> list[dict[str, Any]]:
        try:
            thread = self.client.get_channel(thread_id) or await self.client.fetch_channel(thread_id)
        except Exception:  # noqa: BLE001 - a missing/archived thread just has nothing new
            return []
        try:
            return await self._history(thread, after_id, limit=100)
        except DiscordProblem:
            return []

    async def _history(self, where: Any, after_id: int | None, limit: int = 200) -> list[dict[str, Any]]:
        try:
            if after_id:
                it = where.history(limit=1000, after=discord.Object(id=after_id), oldest_first=True)
            else:
                it = where.history(limit=limit)
            return [to_record(m) async for m in it]
        except Exception as exc:  # noqa: BLE001
            raise explain(exc) from None

    async def send(self, content: str, reply_to: int | None = None, thread_id: int | None = None) -> dict[str, Any]:
        try:
            where = self.channel
            if thread_id:
                where = self.client.get_channel(thread_id) or await self.client.fetch_channel(thread_id)
            kwargs: dict[str, Any] = {"allowed_mentions": discord.AllowedMentions.none()}
            if reply_to:
                kwargs["reference"] = discord.MessageReference(
                    message_id=reply_to, channel_id=self.channel.id, fail_if_not_exists=False)
            m = await where.send(content, **kwargs)
            return to_record(m)
        except Exception as exc:  # noqa: BLE001
            raise explain(exc) from None

    async def create_thread(self, message_id: int, name: str) -> int | None:
        """Open a public thread on one of our messages. None if not allowed (missing permission)."""
        try:
            msg = self.channel.get_partial_message(message_id)
            thread = await msg.create_thread(name=thread_name(name), auto_archive_duration=1440)
            return thread.id
        except Exception as exc:  # noqa: BLE001 - threads are a nicety; never block the protocol
            say(f"Could not open a thread ({type(exc).__name__}). Give the bot 'Create Public Threads' and "
                "'Send Messages in Threads' in the channel.")
            return None

    async def archive_thread(self, thread_id: int) -> None:
        try:
            thread = self.client.get_channel(thread_id) or await self.client.fetch_channel(thread_id)
            await thread.edit(archived=True)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - leaving a thread open is harmless
            pass


class RestSession(_ChannelOps):
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


class GatewaySession(_ChannelOps):
    """A live bot connection that records every trusted message in the channel and its threads.

    `touch()` marks activity; `idle_seconds()` says how long it has been quiet.
    """

    def __init__(self, cfg: Config, db: Database, on_message: Callable[[], None] | None = None) -> None:
        self.cfg, self.db = cfg, db
        self.client: discord.Client = self._make_client()
        self.channel: Any = None
        self.own_id = 0
        self.ready = asyncio.Event()
        self.problem: DiscordProblem | None = None
        self.last_activity = time.monotonic()
        self._task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._status: str | None = None
        self._on_message = on_message

    # ----- lifecycle -----
    def _make_client(self) -> discord.Client:
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:
            await self._on_ready()

        @client.event
        async def on_message(m: discord.Message) -> None:
            await self._handle(m)

        @client.event
        async def on_message_edit(_before: discord.Message, after: discord.Message) -> None:
            await self._handle(after)

        return client

    @property
    def online(self) -> bool:
        return self.ready.is_set() and not self.client.is_closed()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity

    async def start(self, timeout: float = 30) -> None:
        """Connect (if not already) and wait until the channel is ready."""
        self.touch()
        if self.online:
            return
        if self.client.is_closed():
            self.client = self._make_client()
            self.ready = asyncio.Event()
        self.problem = None
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())
        waiter = asyncio.ensure_future(self.ready.wait())
        done, _ = await asyncio.wait({waiter, self._task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            waiter.cancel()
            if self.problem is None:
                self.problem = DiscordProblem("Timed out connecting to Discord.")
            await self.stop()
            raise self.problem
        if self.problem is not None:
            raise self.problem

    async def _run(self) -> None:
        try:
            await self.client.start(self.cfg.bot_token)
        except Exception as exc:  # noqa: BLE001
            self.problem = explain(exc)

    async def stop(self) -> None:
        if self._status_task:
            self._status_task.cancel()
            self._status_task = None
        try:
            await self.client.close()
        except Exception:  # noqa: BLE001
            pass
        self.ready = asyncio.Event()

    async def wait_closed(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    # ----- events -----
    async def _on_ready(self) -> None:
        client = self.client
        assert client.user is not None
        self.own_id = client.user.id
        self.db.set_meta("own_id", str(self.own_id))
        app_id = await application_id_of(client)
        channel = client.get_channel(self.cfg.channel_id)
        if channel is None:
            say(f"Connected as {client.user}, but cannot see channel {self.cfg.channel_id}.")
            if app_id:
                show_invite(app_id, "The bot isn't in that channel's server yet (or channel_id is wrong).")
                self.problem = DiscordProblem(
                    f"The bot can't see channel {self.cfg.channel_id}. Invite it: {invite_url(app_id)}")
            else:
                self.problem = DiscordProblem(f"The bot can't see channel {self.cfg.channel_id}.")
            await self.stop()
            return
        self.channel = channel
        if app_id and not self.db.get_meta("invite_shown"):
            show_invite(app_id, "First run: here is your bot's invite link (save it for later).", open_browser=False)
        self.db.set_meta("invite_shown", "1")
        try:
            recs = [to_record(m) async for m in channel.history(limit=200)]  # type: ignore[union-attr]
            ingest_many(self.db, self.cfg, recs, self.own_id)
        except discord.HTTPException as exc:
            say(f"Could not read past messages: {explain(exc)}")
        self._start_status()
        say(f"Online as {client.user}, watching #{getattr(channel, 'name', self.cfg.channel_id)}.")
        self.ready.set()

    def _ours(self, m: discord.Message) -> bool:
        ch = m.channel
        return ch.id == self.cfg.channel_id or getattr(ch, "parent_id", None) == self.cfg.channel_id

    async def _handle(self, m: discord.Message) -> None:
        if not self._ours(m) or self.client.user is None:
            return
        rec = to_record(m)
        if rec["thread_id"]:
            key = f"thread_last:{rec['thread_id']}"
            if rec["id"] > int(self.db.get_meta(key) or 0):
                self.db.set_meta(key, str(rec["id"]))
        else:
            self.db.bump_last_seen(rec["id"])
        if ingest(self.db, self.cfg, rec, self.own_id):
            self.db.rebuild_locks(self.cfg.default_lock_ttl_minutes)
            if rec["author_id"] != self.own_id:
                self.touch()  # other agents talking counts as activity
                if self._on_message:
                    self._on_message()

    # ----- status rotation -----
    def _start_status(self) -> None:
        if self.cfg.statuses and (self._status_task is None or self._status_task.done()):
            self._status_task = asyncio.ensure_future(self._status_loop())

    async def _status_loop(self) -> None:
        custom = getattr(discord, "CustomActivity", None)
        try:
            while True:
                text = pick_status(self.cfg.statuses, self._status)
                if text is None:
                    return
                try:
                    activity = custom(name=text) if custom is not None else discord.Game(name=text)
                    await self.client.change_presence(activity=activity)
                    self._status = text
                except Exception as exc:  # noqa: BLE001 - a status is cosmetic, never crash the bot
                    say(f"Could not set the bot status ({type(exc).__name__}); will try again later.")
                if self.cfg.status_minutes <= 0:
                    return
                await asyncio.sleep(self.cfg.status_minutes * 60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            say(f"Status rotation stopped ({type(exc).__name__}).")


async def run_until_idle(cfg: Config, db: Database) -> None:
    """`coordbot run`: stay online while there is activity, go offline after idle_minutes."""
    session = GatewaySession(cfg, db)
    await session.start(timeout=60)
    idle = cfg.idle_minutes
    say("Leave this window open. Press Ctrl+C to stop."
        + (f" The bot goes offline by itself after {idle} minutes with no activity." if idle else ""))
    try:
        while session.online:
            await asyncio.sleep(15)
            # Agent commands (CLI/MCP) mark activity in the shared database.
            stamp = db.get_meta("last_local_activity")
            if stamp and time.time() - float(stamp) < 30:
                session.touch()
            if idle and session.idle_seconds() > idle * 60:
                say(f"No activity for {idle} minutes: going offline. Start the bot again when you need it.")
                break
    finally:
        await session.stop()
        await session.wait_closed()


def run_gateway(cfg: Config, db: Database) -> None:
    try:
        asyncio.run(run_until_idle(cfg, db))
    except (discord.LoginFailure, discord.PrivilegedIntentsRequired) as exc:
        raise explain(exc) from None
