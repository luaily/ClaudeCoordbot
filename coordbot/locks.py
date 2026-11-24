"""Deriving locks from events, conflict checks and race resolution. Pure functions."""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import any_overlap, overlaps


@dataclass(frozen=True)
class EventRow:
    msg_id: int
    author_id: int
    agent: str
    type: str
    uid: str
    files: tuple[str, ...]
    text: str
    ttl: int | None
    all: bool
    ts: float


@dataclass
class Lock:
    msg_id: int
    file: str
    author_id: int
    agent: str
    uid: str
    reason: str
    ts: float
    expires_ts: float
    released_by: int | None = None

    def active(self, now: float) -> bool:
        return self.released_by is None and self.expires_ts > now


def derive_locks(events: list[EventRow], default_ttl: int = 120) -> list[Lock]:
    """A lock is active until a later release/done by the SAME author covers it, or its TTL passes."""
    locks: list[Lock] = []
    for ev in sorted(events, key=lambda e: e.msg_id):
        if ev.type == "lock":
            ttl = ev.ttl or default_ttl
            for f in ev.files:
                locks.append(Lock(ev.msg_id, f, ev.author_id, ev.agent, ev.uid, ev.text,
                                  ev.ts, ev.ts + ttl * 60))
        elif ev.type in ("release", "done"):
            for lk in locks:
                if lk.released_by is None and lk.author_id == ev.author_id and lk.msg_id < ev.msg_id:
                    if ev.all or any(overlaps_release(lk.file, rf) for rf in ev.files):
                        lk.released_by = ev.msg_id
    return locks


def overlaps_release(lock_file: str, release_file: str) -> bool:
    """A release of a path frees the same path or anything inside it."""
    return lock_file == release_file or (overlaps(lock_file, release_file) and lock_file.startswith(release_file))


def conflicts(files: list[str], active: list[Lock], own_id: int) -> list[Lock]:
    return [lk for lk in active if lk.author_id != own_id and any_overlap([lk.file], files)]


def lost_race(mine: list[tuple[int, list[str]]], active: list[Lock], own_id: int) -> list[Lock]:
    """mine: (our lock message id, files). Returns others' active locks that overlap and are EARLIER."""
    lost: list[Lock] = []
    for msg_id, files in mine:
        for lk in active:
            if lk.author_id != own_id and lk.msg_id < msg_id and any_overlap([lk.file], files):
                if lk not in lost:
                    lost.append(lk)
    return lost
