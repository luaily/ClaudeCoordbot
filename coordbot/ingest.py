"""Trust filtering and storing of messages. Message content is data only; nothing here acts on it."""

from __future__ import annotations

from typing import Any

from .config import Config
from .db import Database
from .protocol import parse_coord, parse_github_embed


def is_trusted(cfg: Config, rec: dict[str, Any], own_id: int | None) -> bool:
    webhook_id = rec.get("webhook_id")
    if webhook_id:
        return webhook_id in cfg.trusted_webhook_ids
    author = rec["author_id"]
    return author == own_id or author in cfg.trusted_user_ids


def ingest(db: Database, cfg: Config, rec: dict[str, Any], own_id: int | None) -> bool:
    """Store one message if trusted. Returns True when it was kept."""
    if not is_trusted(cfg, rec, own_id):
        return False
    db.insert_message(rec)
    if rec.get("webhook_id"):
        for embed in rec.get("embeds", []):
            for c in parse_github_embed(embed):
                db.insert_commit(c, rec["ts"], rec["id"], embed)
        return True
    ev = parse_coord(rec.get("content", ""), rec["ts"], cfg.default_lock_ttl_minutes)
    if rec["author_id"] != own_id:
        _maybe_inbox(db, cfg, rec, ev, own_id)
    if ev is not None:
        db.insert_event(rec["id"], rec["author_id"], ev, rec["ts"], rec.get("channel_id"))
        if not rec.get("thread_id") and rec["author_id"] != own_id:
            if ev.type in ("plan", "lock"):
                # Their task thread (if any) has this message's id: watch it for context.
                db.set_thread_if_new(rec["id"], rec["id"], rec["author_id"], rec["ts"])
            elif ev.type in ("done", "release") and ev.all:
                db.close_threads_of(rec["author_id"])
        if rec["author_id"] == own_id:
            db.insert_own_action(rec["id"], ev.type, ev.text, list(ev.files), ev.commit, rec["ts"])
    return True


def _maybe_inbox(db: Database, cfg: Config, rec: dict[str, Any], ev: Any, own_id: int | None) -> None:
    """File contacts addressed to us (or messages from linked humans aimed at us) in the inbox."""
    me = cfg.member_by_bot(own_id or 0)
    my_human = me.user_id if me else None
    sender = cfg.member_by_bot(rec["author_id"])
    base = {"msg_id": rec["id"], "from_id": rec["author_id"], "thread_id": rec.get("thread_id"), "ts": rec["ts"]}
    if ev is not None and ev.type == "contact":
        to = (ev.to or "").strip().lower()
        if to and to not in (cfg.agent_name.lower(), "all"):
            return
        human = sender.name if sender else ""
        if not human and rec["author_id"] in cfg.member_humans():
            human = rec.get("author_name", "")
        db.add_inbox({**base, "from_agent": ev.agent, "from_human": human, "kind": ev.kind or "info",
                      "text": ev.text, "ref": ev.ref, "uid": ev.id, "decision": ev.decision,
                      "files": list(ev.files)})
        return
    if ev is not None or rec["author_id"] not in cfg.member_humans() or rec["author_id"] == my_human:
        return
    # A linked human wrote plain text: is it for us (mentions us, or replies to one of our messages)?
    mentions = set(rec.get("mentions") or [])
    replied = db.message(rec["reply_to"]) if rec.get("reply_to") else None
    if (own_id and own_id in mentions) or (my_human and my_human in mentions) or \
            (replied is not None and replied["author_id"] == own_id):
        db.add_inbox({**base, "from_agent": None, "from_human": rec.get("author_name", ""), "kind": "human",
                      "text": " ".join((rec.get("content") or "").split())[:1500]})


def ingest_many(db: Database, cfg: Config, recs: list[dict[str, Any]], own_id: int | None) -> int:
    kept = 0
    for rec in sorted(recs, key=lambda r: r["id"]):
        if not rec.get("thread_id"):  # thread replies have their own cursor (thread_last:<id>)
            db.bump_last_seen(rec["id"])
        kept += ingest(db, cfg, rec, own_id)
    db.rebuild_locks(cfg.default_lock_ttl_minutes)
    return kept
