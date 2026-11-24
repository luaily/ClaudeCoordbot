"""Command-line interface: `python -m coordbot <command>`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, ConfigError, load_config
from .db import Database
from .discord_client import DiscordProblem, RestSession, run_gateway
from .ingest import ingest, ingest_many
from .locks import Lock, conflicts, lost_race
from .protocol import MAX_PATH_LEN, build_messages, normalize_path

OK, ERR, CONFLICT = 0, 1, 2
POSTING = {"plan", "lock", "done", "release"}


class UsageProblem(Exception):
    pass


# ---------- helpers ----------

def ago(ts: float, now: float | None = None) -> str:
    s = int((now or time.time()) - ts)
    if s < 90:
        return f"{max(s, 0)}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago" if s < 172800 else f"{s // 86400}d ago"


def until(ts: float, now: float | None = None) -> str:
    s = int(ts - (now or time.time()))
    return "now" if s <= 0 else (f"in {max(s // 60, 1)}m" if s < 5400 else f"in {s // 3600}h")


def clean_files(files: list[str] | None) -> list[str]:
    out: list[str] = []
    for f in files or []:
        n = normalize_path(f)
        if not n or n == "roblox:":
            raise UsageProblem(f"Empty or invalid path: {f!r}")
        if len(n) > MAX_PATH_LEN:
            raise UsageProblem(f"Path is too long (max {MAX_PATH_LEN} characters): {n[:60]}...")
        if n not in out:
            out.append(n)
    return out


def lock_dict(lk: Lock) -> dict[str, Any]:
    return {"file": lk.file, "agent": lk.agent, "author_id": lk.author_id, "reason": lk.reason,
            "since": int(lk.ts), "expires": int(lk.expires_ts), "message_id": lk.msg_id}


def lock_line(lk: Lock, now: float) -> str:
    why = f' "{lk.reason}"' if lk.reason else ""
    return f"{lk.file} - {lk.agent}{why} - since {ago(lk.ts, now)}, expires {until(lk.expires_ts, now)}"


class Ctx:
    def __init__(self, cfg: Config, db: Database, session: RestSession | None, own_id: int, as_json: bool) -> None:
        self.cfg, self.db, self.session, self.own_id, self.as_json = cfg, db, session, own_id, as_json

    async def sync(self) -> None:
        if self.session is None:
            return
        recs = await self.session.fetch(self.db.last_seen_id())
        ingest_many(self.db, self.cfg, recs, self.own_id)

    async def post(self, kind: str, **kw: Any) -> list[dict[str, Any]]:
        if self.session is None:
            raise DiscordProblem("Cannot post right now: not connected to Discord.")
        posted: list[dict[str, Any]] = []
        for content, payload in build_messages(kind, self.cfg.agent_name, **kw):
            rec = await self.session.send(content)
            ingest(self.db, self.cfg, rec, self.own_id)
            posted.append({"message_id": rec["id"], "files": payload["files"], "id": payload["id"]})
        self.db.rebuild_locks(self.cfg.default_lock_ttl_minutes)
        return posted

    def emit(self, data: dict[str, Any], text: str) -> None:
        print(json.dumps(data, ensure_ascii=False) if self.as_json else text)

    def others_conflicts(self, files: list[str]) -> list[Lock]:
        return conflicts(files, self.db.active_locks(), self.own_id)


# ---------- commands ----------

async def cmd_sync(ctx: Ctx, args: argparse.Namespace) -> int:
    ctx.emit({"ok": True, "synced": ctx.session is not None}, "Synced.")
    return OK


async def cmd_plan(ctx: Ctx, args: argparse.Namespace) -> int:
    posted = await ctx.post("plan", files=clean_files(args.files), text=args.summary)
    ctx.emit({"ok": True, "posted": posted}, f"Plan posted ({len(posted)} message{'s' if len(posted) != 1 else ''}).")
    return OK


def _blocked(ctx: Ctx, found: list[Lock], title: str) -> int:
    now = time.time()
    ctx.emit({"ok": False, "conflicts": [lock_dict(l) for l in found]},
             title + "\n" + "\n".join("  " + lock_line(l, now) for l in found))
    return CONFLICT


async def cmd_lock(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    ttl = args.ttl or ctx.cfg.default_lock_ttl_minutes
    if not 1 <= ttl <= 10080:
        raise UsageProblem("--ttl must be between 1 and 10080 minutes")
    found = ctx.others_conflicts(files)
    if found:
        return _blocked(ctx, found, "BLOCKED: someone else holds these. Do not touch them; run `coord wait` or do something else.")
    posted = await ctx.post("lock", files=files, text=args.reason, ttl=ttl)
    await asyncio.sleep(args.race_wait)
    await ctx.sync()
    mine = [(p["message_id"], p["files"]) for p in posted]
    lost = lost_race(mine, ctx.db.active_locks(), ctx.own_id)
    if lost:
        await ctx.post("release", files=files, text="lost the race")
        return _blocked(ctx, lost, "LOST THE RACE: someone locked first. Your lock was released. Do not touch these files.")
    ctx.emit({"ok": True, "locked": files, "ttl": ttl, "posted": posted},
             f"Locked {len(files)} file{'s' if len(files) != 1 else ''} for {ttl} min.")
    return OK


async def cmd_check(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    found = ctx.others_conflicts(files)
    if found:
        return _blocked(ctx, found, "LOCKED by someone else:")
    ctx.emit({"ok": True, "free": True}, "Free.")
    return OK


async def cmd_done(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    posted = await ctx.post("done", files=files, text=args.summary, commit=args.commit, all_=args.all)
    ctx.emit({"ok": True, "posted": posted}, "Done posted; locks released.")
    return OK


async def cmd_release(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    if not files and not args.all:
        raise UsageProblem("Say which files to release, or use --all.")
    posted = await ctx.post("release", files=files, text=args.reason or "", all_=args.all)
    ctx.emit({"ok": True, "posted": posted}, "Released.")
    return OK


async def cmd_status(ctx: Ctx, args: argparse.Namespace) -> int:
    now = time.time()
    locks = ctx.db.active_locks(now)
    evs = ctx.db.recent_events(("plan", "done"), 5)
    commits = ctx.db.recent_commits(5)
    data = {
        "ok": True, "agent": ctx.cfg.agent_name,
        "locks": [lock_dict(l) | {"mine": l.author_id == ctx.own_id} for l in locks],
        "recent": [{"type": e["type"], "agent": e["agent"], "text": e["text"], "files": json.loads(e["files"]),
                    "commit": e["commit_sha"], "ts": int(e["ts"])} for e in evs],
        "commits": [c.__dict__ for c in ctx.db.recent_commits(5)],
    }
    lines = [f"Active locks ({len(locks)}):"] + [
        "  " + lock_line(l, now) + (" [yours]" if l.author_id == ctx.own_id else "") for l in locks] or []
    if not locks:
        lines[0] = "Active locks: none"
    lines.append("Recent plans/done:" if evs else "Recent plans/done: none")
    for e in evs:
        files = json.loads(e["files"])
        extra = f" ({', '.join(files[:3])}{'...' if len(files) > 3 else ''})" if files else ""
        lines.append(f"  [{e['type']}] {e['agent']} {ago(e['ts'], now)}: {e['text']}{extra}")
    lines.append("Latest commits:" if commits else "Latest commits: none seen")
    for c in commits:
        lines.append(f"  {c.sha[:7]} {c.message} - {c.author} ({c.branch}) {ago(c.ts, now)}")
    ctx.emit(data, "\n".join(lines))
    return OK


async def cmd_commits(ctx: Ctx, args: argparse.Namespace) -> int:
    now = time.time()
    commits = ctx.db.recent_commits(args.n)
    text = "\n".join(f"{c.sha[:7]} {c.message} - {c.author} ({c.repo}:{c.branch}) {ago(c.ts, now)}"
                     for c in commits) or "No GitHub commits seen yet."
    ctx.emit({"ok": True, "commits": [c.__dict__ for c in commits]}, text)
    return OK


async def cmd_wait(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    deadline = time.time() + args.timeout
    while True:
        found = ctx.others_conflicts(files)
        if not found:
            ctx.emit({"ok": True, "free": True}, "Free.")
            return OK
        if time.time() >= deadline:
            return _blocked(ctx, found, "TIMEOUT: still locked by someone else:")
        await asyncio.sleep(args.interval)
        await ctx.sync()
        ctx.db.rebuild_locks(ctx.cfg.default_lock_ttl_minutes)


async def cmd_log(ctx: Ctx, args: argparse.Namespace) -> int:
    rows = ctx.db.own_actions(args.n)
    data = [{"type": r["type"], "summary": r["summary"], "files": json.loads(r["files"]),
             "commit": r["commit_sha"], "ts": int(r["ts"]), "message_id": r["msg_id"]} for r in rows]
    now = time.time()
    text = "\n".join(f"{ago(d['ts'], now)} [{d['type']}] {d['summary']} {', '.join(d['files'][:3])}".rstrip()
                     for d in data) or "No actions recorded yet."
    ctx.emit({"ok": True, "actions": data}, text)
    return OK


HANDLERS = {"sync": cmd_sync, "plan": cmd_plan, "lock": cmd_lock, "check": cmd_check, "done": cmd_done,
            "release": cmd_release, "status": cmd_status, "commits": cmd_commits, "wait": cmd_wait, "log": cmd_log}


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")
    common.add_argument("--config", type=Path, help="path to config.toml (default: next to the tool)")

    p = argparse.ArgumentParser(prog="coordbot", description="Coordinate AI coding agents through one Discord channel.")
    p.add_argument("--version", action="version", version=f"coordbot {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_, description=help_)

    add("run", "start the always-on bot that records the channel")
    add("sync", "fetch recent channel history into the local database")
    s = add("plan", "post what you are about to change")
    s.add_argument("summary")
    s.add_argument("--files", nargs="*", default=[], metavar="FILE")
    s = add("lock", "claim files (exit 2 if someone else has them)")
    s.add_argument("files", nargs="+", metavar="FILE", help="repo path, or roblox:Workspace.Some.Path")
    s.add_argument("--reason", required=True)
    s.add_argument("--ttl", type=int, help="minutes until the lock expires (default from config)")
    s.add_argument("--race-wait", type=float, default=5.0, help=argparse.SUPPRESS)
    s = add("check", "exit 0 if files are free, 2 if locked by someone else")
    s.add_argument("files", nargs="+", metavar="FILE")
    s = add("done", "post what you changed and release your locks")
    s.add_argument("summary")
    s.add_argument("--files", nargs="*", default=[], metavar="FILE")
    s.add_argument("--commit", help="commit sha")
    s.add_argument("--all", action="store_true", help="release ALL of your locks")
    s = add("release", "release locks without a summary")
    s.add_argument("files", nargs="*", metavar="FILE")
    s.add_argument("--all", action="store_true", help="release ALL of your locks")
    s.add_argument("--reason", default="")
    add("status", "show active locks, recent plans/done and latest commits")
    s = add("commits", "show recent GitHub commits")
    s.add_argument("-n", type=int, default=10)
    s = add("wait", "wait until files are free")
    s.add_argument("files", nargs="+", metavar="FILE")
    s.add_argument("--timeout", type=float, default=600, help="seconds (default 600)")
    s.add_argument("--interval", type=float, default=5, help=argparse.SUPPRESS)
    s = add("log", "show your own recorded actions")
    s.add_argument("-n", type=int, default=20)
    return p


# ---------- main ----------

async def execute(cfg: Config, db: Database, args: argparse.Namespace) -> int:
    session: RestSession | None = RestSession(cfg)
    own_id = 0
    try:
        try:
            assert session is not None
            await session.open()
            own_id = session.own_id
            db.set_meta("own_id", str(own_id))
        except DiscordProblem as exc:
            if args.command in POSTING or args.command == "sync":
                raise
            cached = db.get_meta("own_id")
            if not cached:
                raise
            print(f"Warning: {exc} Showing saved data.", file=sys.stderr)
            session, own_id = None, int(cached)
        ctx = Ctx(cfg, db, session, own_id, args.json)
        try:
            await ctx.sync()
        except DiscordProblem as exc:
            if args.command in POSTING or args.command == "sync":
                raise
            print(f"Warning: {exc} Showing saved data.", file=sys.stderr)
            ctx.session = None
        return await HANDLERS[args.command](ctx, args)
    finally:
        if session is not None:
            await session.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_json = bool(getattr(args, "json", False))

    def fail(msg: str) -> int:
        print(json.dumps({"ok": False, "error": msg}) if as_json else f"Error: {msg}", file=sys.stderr if not as_json else sys.stdout)
        return ERR

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        return fail(str(exc))
    try:
        db = Database(cfg.db_path)
    except Exception as exc:  # noqa: BLE001
        return fail(f"Could not open the database at {cfg.db_path}: {exc}")
    try:
        if args.command == "run":
            run_gateway(cfg, db)
            return OK
        return asyncio.run(execute(cfg, db, args))
    except UsageProblem as exc:
        return fail(str(exc))
    except DiscordProblem as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        print("Stopped.")
        return OK
    finally:
        db.close()
