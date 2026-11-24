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
    if ev is not None:
        db.insert_event(rec["id"], rec["author_id"], ev, rec["ts"])
        if rec["author_id"] == own_id:
            db.insert_own_action(rec["id"], ev.type, ev.text, list(ev.files), ev.commit, rec["ts"])
    return True


def ingest_many(db: Database, cfg: Config, recs: list[dict[str, Any]], own_id: int | None) -> int:
    kept = 0
    for rec in sorted(recs, key=lambda r: r["id"]):
        db.bump_last_seen(rec["id"])
        kept += ingest(db, cfg, rec, own_id)
    db.rebuild_locks(cfg.default_lock_ttl_minutes)
    return kept
