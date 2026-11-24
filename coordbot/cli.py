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
from .gitcheck import preflight
from .ingest import ingest, ingest_many
from .locks import Lock, conflicts, lost_race
from .protocol import CONTEXT_KINDS, GIT_ACTIONS, MAX_PATH_LEN, build_messages, normalize_path

OK, ERR, CONFLICT = 0, 1, 2
POSTING = {"plan", "lock", "done", "release", "context", "git"}
LOCAL_ONLY = {"preflight"}  # commands that work without Discord


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
    """Everything a command needs. With capture=True (MCP), output is collected instead of printed."""

    def __init__(self, cfg: Config, db: Database, session: Any, own_id: int, as_json: bool,
                 capture: bool = False) -> None:
        self.cfg, self.db, self.session, self.own_id, self.as_json = cfg, db, session, own_id, as_json
        self.capture = capture
        self.result: dict[str, Any] = {}
        self.text = ""
        db.set_meta("last_local_activity", str(time.time()))  # keeps a running `coordbot run` online

    async def sync(self) -> None:
        if self.session is None:
            return
        recs = await self.session.fetch(self.db.last_seen_id())
        # Threads opened on lock/plan messages carry context and defenses: read new replies too.
        fetch_thread = getattr(self.session, "fetch_thread", None)
        if fetch_thread is not None:
            for tid in self.db.open_thread_ids():
                key = f"thread_last:{tid}"
                last = self.db.get_meta(key)
                got = await fetch_thread(tid, int(last) if last else None)
                if got:
                    recs.extend(got)
                    self.db.set_meta(key, str(max(r["id"] for r in got)))
        ingest_many(self.db, self.cfg, recs, self.own_id)

    async def _send(self, content: str, reply_to: int | None, thread_id: int | None) -> dict[str, Any]:
        if reply_to or thread_id:
            return await self.session.send(content, reply_to=reply_to, thread_id=thread_id)
        return await self.session.send(content)

    async def post(self, type_: str, *, reply_to: int | None = None, thread_id: int | None = None,
                   **kw: Any) -> list[dict[str, Any]]:
        if self.session is None:
            raise DiscordProblem("Cannot post right now: not connected to Discord.")
        posted: list[dict[str, Any]] = []
        for content, payload in build_messages(type_, self.cfg.agent_name, **kw):
            rec = await self._send(content, reply_to, thread_id)
            rec.setdefault("channel_id", thread_id or self.cfg.channel_id)
            ingest(self.db, self.cfg, rec, self.own_id)
            posted.append({"message_id": rec["id"], "files": payload["files"], "id": payload["id"]})
        self.db.rebuild_locks(self.cfg.default_lock_ttl_minutes)
        return posted

    # ----- the task thread: one per piece of work, opened on the first plan/lock -----
    def task_thread(self) -> int | None:
        v = self.db.get_meta("task_thread")
        return int(v) if v else None

    async def open_task_thread(self, root_msg_id: int, title: str) -> int | None:
        if self.task_thread():
            return self.task_thread()
        create = getattr(self.session, "create_thread", None)
        if create is None:
            return None
        tid = await create(root_msg_id, f"{self.cfg.agent_name}: {title}")
        if tid:
            self.db.set_thread(root_msg_id, tid, self.own_id)
            self.db.set_meta("task_thread", str(tid))
        return tid

    async def close_task_thread(self) -> None:
        tid = self.task_thread()
        if not tid:
            return
        archive = getattr(self.session, "archive_thread", None)
        if archive is not None:
            await archive(tid)
        self.db.close_thread(tid)
        self.db.set_meta("task_thread", "")

    def emit(self, data: dict[str, Any], text: str) -> None:
        if self.capture:
            self.result, self.text = data, text
            return
        print(json.dumps(data, ensure_ascii=False) if self.as_json else text)

    def others_conflicts(self, files: list[str]) -> list[Lock]:
        return conflicts(files, self.db.active_locks(), self.own_id)


# ---------- commands ----------

async def cmd_sync(ctx: Ctx, args: argparse.Namespace) -> int:
    ctx.emit({"ok": True, "synced": ctx.session is not None}, "Synced.")
    return OK


async def cmd_plan(ctx: Ctx, args: argparse.Namespace) -> int:
    posted = await ctx.post("plan", files=clean_files(args.files), text=args.summary)
    thread = await ctx.open_task_thread(posted[0]["message_id"], args.summary)
    ctx.emit({"ok": True, "posted": posted, "thread_id": thread},
             f"Plan posted ({len(posted)} message{'s' if len(posted) != 1 else ''})."
             + (" Opened a thread for this task." if thread else ""))
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
    thread = await ctx.open_task_thread(posted[0]["message_id"], args.reason)
    ctx.emit({"ok": True, "locked": files, "ttl": ttl, "posted": posted, "thread_id": thread},
             f"Locked {len(files)} file{'s' if len(files) != 1 else ''} for {ttl} min.")
    return OK


async def cmd_check(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    found = ctx.others_conflicts(files)
    if found:
        return _blocked(ctx, found, "LOCKED by someone else:")
    ctx.emit({"ok": True, "free": True}, "Free.")
    return OK


def _my_locks_by_message(ctx: Ctx, files: list[str], all_: bool) -> dict[int, list[str]]:
    """Our active locks that this release covers, grouped by the lock message they came from."""
    from .locks import overlaps_release

    groups: dict[int, list[str]] = {}
    for lk in ctx.db.active_locks():
        if lk.author_id == ctx.own_id and (all_ or any(overlaps_release(lk.file, f) for f in files)):
            groups.setdefault(lk.msg_id, []).append(lk.file)
    return groups


async def _release_as_replies(ctx: Ctx, kind: str, files: list[str], all_: bool, text: str,
                              commit: str | None = None) -> list[dict[str, Any]]:
    """Post the unlock as a reply to each lock message it releases (first reply carries the summary)."""
    groups = _my_locks_by_message(ctx, files, all_)
    if not groups:
        return await ctx.post(kind, files=files, text=text, commit=commit, all_=all_)
    posted: list[dict[str, Any]] = []
    for i, (lock_msg, lock_files) in enumerate(sorted(groups.items())):
        if i == 0:
            posted += await ctx.post(kind, reply_to=lock_msg, files=lock_files, text=text, commit=commit)
        else:
            posted += await ctx.post("release", reply_to=lock_msg, files=lock_files, text=f"released with: {text}"[:200])
    return posted


async def cmd_done(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    posted = await _release_as_replies(ctx, "done", files, args.all, args.summary, args.commit)
    if args.all or not any(lk.author_id == ctx.own_id for lk in ctx.db.active_locks()):
        await ctx.close_task_thread()
    ctx.emit({"ok": True, "posted": posted}, "Done posted as replies to your locks; locks released.")
    return OK


async def cmd_release(ctx: Ctx, args: argparse.Namespace) -> int:
    files = clean_files(args.files)
    if not files and not args.all:
        raise UsageProblem("Say which files to release, or use --all.")
    posted = await _release_as_replies(ctx, "release", files, args.all, args.reason or "")
    if args.all:
        await ctx.close_task_thread()
    ctx.emit({"ok": True, "posted": posted}, "Released.")
    return OK


async def cmd_context(ctx: Ctx, args: argparse.Namespace) -> int:
    """Context for other agents (or a defense of choices, or a question), in the task thread."""
    if args.kind not in CONTEXT_KINDS:
        raise UsageProblem(f"--kind must be one of: {', '.join(CONTEXT_KINDS)}")
    thread = ctx.task_thread()
    posted = await ctx.post("context", thread_id=thread, files=clean_files(args.files), text=args.message,
                            kind=args.kind)
    where = "the task thread" if thread else "the channel"
    ctx.emit({"ok": True, "posted": posted, "thread_id": thread}, f"Posted {args.kind} to {where}.")
    return OK


async def cmd_git(ctx: Ctx, args: argparse.Namespace) -> int:
    """Tell everyone you are pulling/pushing/switching branch/merging."""
    if args.action not in GIT_ACTIONS:
        raise UsageProblem(f"action must be one of: {', '.join(GIT_ACTIONS)}")
    posted = await ctx.post("git", action=args.action, branch=args.branch, commit=args.commit,
                            text=args.summary or "", files=clean_files(args.files))
    ctx.emit({"ok": True, "posted": posted}, f"Posted git {args.action} notice for {args.branch}.")
    return OK


def project_root(ctx: Ctx) -> Path:
    if ctx.cfg.team is not None:
        return ctx.cfg.team.root
    return Path(getattr(ctx, "cwd", None) or Path.cwd())


async def cmd_preflight(ctx: Ctx, args: argparse.Namespace) -> int:
    """Check for conflict risk before pulling or pushing. Show the result to your user."""
    others = [lk.file for lk in ctx.db.active_locks() if lk.author_id != ctx.own_id]
    pf = preflight(project_root(ctx), others, fetch=not args.no_fetch)
    lines = [f"Branch {pf.branch or '?'} -> {pf.upstream or 'no upstream'}: {pf.ahead} ahead, {pf.behind} behind."]
    lines += [f"WARNING: {w}" for w in pf.warnings] + [f"- {a}" for a in pf.advice]
    ctx.emit({"ok": pf.ok and not pf.overlap_files and not pf.locked_by_others, **pf.to_dict()}, "\n".join(lines))
    return CONFLICT if (pf.overlap_files or pf.locked_by_others) else (OK if pf.ok else ERR)


async def cmd_status(ctx: Ctx, args: argparse.Namespace) -> int:
    now = time.time()
    locks = ctx.db.active_locks(now)
    evs = ctx.db.recent_events(("plan", "done", "context", "git"), 8)
    commits = ctx.db.recent_commits(5)
    data = {
        "ok": True, "agent": ctx.cfg.agent_name,
        "locks": [lock_dict(l) | {"mine": l.author_id == ctx.own_id} for l in locks],
        "recent": [{"type": e["type"], "kind": e["kind"], "action": e["action"], "branch": e["branch"],
                    "agent": e["agent"], "text": e["text"], "files": json.loads(e["files"]),
                    "commit": e["commit_sha"], "ts": int(e["ts"])} for e in evs],
        "task_thread": ctx.task_thread(),
        "setup": setup_notes(ctx.cfg, ctx.own_id),
        "commits": [c.__dict__ for c in ctx.db.recent_commits(5)],
    }
    lines = [f"Active locks ({len(locks)}):"] + [
        "  " + lock_line(l, now) + (" [yours]" if l.author_id == ctx.own_id else "") for l in locks] or []
    if not locks:
        lines[0] = "Active locks: none"
    lines.append("Recent activity:" if evs else "Recent activity: none")
    for e in evs:
        files = json.loads(e["files"])
        extra = f" ({', '.join(files[:3])}{'...' if len(files) > 3 else ''})" if files else ""
        label = e["kind"] if e["type"] == "context" and e["kind"] else e["type"]
        if e["type"] == "git":
            label = f"git {e['action']} {e['branch'] or ''}".strip()
        lines.append(f"  [{label}] {e['agent']} {ago(e['ts'], now)}: {e['text']}{extra}")
    lines.append("Latest commits:" if commits else "Latest commits: none seen")
    for c in commits:
        lines.append(f"  {c.sha[:7]} {c.message} - {c.author} ({c.branch}) {ago(c.ts, now)}")
    notes = setup_notes(ctx.cfg, ctx.own_id)
    lines += [f"Setup: {n}" for n in notes]
    ctx.emit(data, "\n".join(lines))
    return OK


def setup_notes(cfg: Config, own_id: int) -> list[str]:
    """Things the user should fix so teammates can see this bot's posts."""
    notes: list[str] = []
    team = cfg.team
    if team is None:
        return notes
    if own_id and own_id not in team.trusted_user_ids:
        notes.append(f"your bot id {own_id} is not in {team.path.name}'s trusted_user_ids: teammates ignore "
                     f"your posts until it is added there and committed.")
    if not team.trusted_webhook_ids:
        notes.append("no GitHub webhook is trusted in the team file, so commits aren't tracked.")
    return notes


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
            "release": cmd_release, "status": cmd_status, "commits": cmd_commits, "wait": cmd_wait, "log": cmd_log,
            "context": cmd_context, "git": cmd_git, "preflight": cmd_preflight}


# ---------- parser ----------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable JSON output")
    common.add_argument("--config", type=Path, help="personal config (default: ~/.coordbot/config.toml)")
    common.add_argument("--project", type=Path, help="project folder with .coordbot/team.toml (default: here)")

    p = argparse.ArgumentParser(prog="coordbot", description="Coordinate AI coding agents through one Discord channel.")
    p.add_argument("--version", action="version", version=f"coordbot {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_, description=help_)

    add("setup", "create your personal config (~/.coordbot/config.toml) and open it")
    add("doctor", "check your setup: settings, team file, bot id, permissions")
    add("mcp", "run as an MCP server for AI agents (stdio)")
    add("run", "keep the bot online and recording; goes offline after idle_minutes with no activity")
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
    s = add("context", "post context for other agents in your task thread")
    s.add_argument("message")
    s.add_argument("--kind", default="context", choices=CONTEXT_KINDS)
    s.add_argument("--files", nargs="*", default=[], metavar="FILE")
    s = add("git", "announce a git pull/push/branch/merge")
    s.add_argument("action", choices=GIT_ACTIONS)
    s.add_argument("branch")
    s.add_argument("--commit")
    s.add_argument("--summary", default="")
    s.add_argument("--files", nargs="*", default=[], metavar="FILE")
    s = add("preflight", "before pull/push: ahead/behind, conflict risk, locked files (exit 2 = risky)")
    s.add_argument("--no-fetch", action="store_true", help="don't run git fetch first")
    s = add("log", "show your own recorded actions")
    s.add_argument("-n", type=int, default=20)
    return p


# ---------- main ----------

async def execute(cfg: Config, db: Database, args: argparse.Namespace, session: Any = None,
                  capture: bool = False) -> tuple[int, Ctx]:
    """Run one command. With `session` given (the MCP server's live gateway), it is reused and not closed."""
    owned = session is None
    own_id = int(getattr(session, "own_id", 0) or 0)
    warn = (lambda m: None) if capture else (lambda m: print(m, file=sys.stderr))
    needs_discord = args.command in POSTING or args.command == "sync"
    try:
        if args.command in LOCAL_ONLY:
            session = None if owned else session
            own_id = own_id or int(db.get_meta("own_id") or 0)
        elif owned:
            session = RestSession(cfg)
            try:
                await session.open()
                own_id = session.own_id
                db.set_meta("own_id", str(own_id))
            except DiscordProblem as exc:
                await session.close()
                session = None
                cached = db.get_meta("own_id")
                if needs_discord or not cached:
                    raise
                warn(f"Warning: {exc} Showing saved data.")
                own_id = int(cached)
        ctx = Ctx(cfg, db, session, own_id, bool(getattr(args, "json", False)), capture=capture)
        try:
            await ctx.sync()
        except DiscordProblem as exc:
            if needs_discord:
                raise
            warn(f"Warning: {exc} Showing saved data.")
            ctx.session = None
        code = await HANDLERS[args.command](ctx, args)
        return code, ctx
    finally:
        if owned and session is not None:
            await session.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_json = bool(getattr(args, "json", False))

    def fail(msg: str) -> int:
        print(json.dumps({"ok": False, "error": msg}) if as_json else f"Error: {msg}", file=sys.stderr if not as_json else sys.stdout)
        return ERR

    if args.command == "setup":
        from .setup import run_setup

        return run_setup(args.config)
    if args.command == "doctor":
        from .setup import doctor_report

        ready, lines = asyncio.run(doctor_report(args.config, args.project))
        if as_json:
            print(json.dumps({"ok": ready, "lines": lines}))
        else:
            print("\n".join(lines) + ("\nReady." if ready else "\nNot ready yet: fix the items above."))
        return OK if ready else ERR
    if args.command == "mcp":
        from .mcp_server import serve

        serve(args.config, args.project)
        return OK
    try:
        cfg = load_config(args.config, args.project)
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
        return asyncio.run(execute(cfg, db, args))[0]
    except UsageProblem as exc:
        return fail(str(exc))
    except DiscordProblem as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        print("Stopped.")
        return OK
    finally:
        db.close()
