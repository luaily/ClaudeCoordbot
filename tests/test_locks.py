import time

from coordbot.config import parse_config
from coordbot.db import Database
from coordbot.ingest import ingest, ingest_many, is_trusted
from coordbot.locks import EventRow, conflicts, derive_locks, lost_race
from coordbot.protocol import build_messages

NOW = 1_000_000.0


def ev(msg_id, author, type_, files=(), ttl=60, all_=False, ts=NOW):
    return EventRow(msg_id, author, f"agent{author}", type_, str(msg_id), tuple(files), "why", ttl, all_, ts)


def active(events, now=NOW + 1):
    return [l for l in derive_locks(events) if l.active(now)]


def test_lock_active_until_release():
    evs = [ev(1, 10, "lock", ["a", "b"])]
    assert {l.file for l in active(evs)} == {"a", "b"}
    evs.append(ev(2, 10, "release", ["a"]))
    assert {l.file for l in active(evs)} == {"b"}
    evs.append(ev(3, 10, "done", all_=True))
    assert active(evs) == []


def test_release_only_by_same_author():
    evs = [ev(1, 10, "lock", ["a"]), ev(2, 11, "release", ["a"]), ev(3, 11, "done", all_=True)]
    assert len(active(evs)) == 1


def test_release_does_not_free_later_lock():
    evs = [ev(1, 10, "lock", ["a"]), ev(2, 10, "release", ["a"]), ev(3, 10, "lock", ["a"])]
    assert [l.msg_id for l in active(evs)] == [3]


def test_directory_release_and_no_reverse():
    evs = [ev(1, 10, "lock", ["src/x.luau"]), ev(2, 10, "release", ["src"])]
    assert active(evs) == []
    evs = [ev(1, 10, "lock", ["src"]), ev(2, 10, "release", ["src/x.luau"])]
    assert len(active(evs)) == 1


def test_expiry():
    evs = [ev(1, 10, "lock", ["a"], ttl=60)]
    assert len(active(evs, NOW + 3599)) == 1
    assert active(evs, NOW + 3601) == []


def test_conflicts_exclude_own():
    evs = [ev(1, 10, "lock", ["src/a"]), ev(2, 11, "lock", ["src/b"])]
    act = active(evs)
    assert [l.author_id for l in conflicts(["src"], act, own_id=10)] == [11]
    assert conflicts(["src/c"], act, own_id=10) == []


def test_race_resolution_by_snowflake():
    act = active([ev(5, 10, "lock", ["a"]), ev(9, 11, "lock", ["a"])])
    assert lost_race([(9, ["a"])], act, own_id=11)[0].author_id == 10   # 11 lost
    assert lost_race([(5, ["a"])], act, own_id=10) == []               # 10 won
    assert lost_race([(9, ["zzz"])], act, own_id=11) == []


# ---- trust + ingest end-to-end ----

def cfg(tmp_path):
    return parse_config({"bot_token": "x", "channel_id": 1, "agent_name": "me", "trusted_user_ids": [20],
                         "trusted_webhook_ids": [99]}, tmp_path / "c.toml")


def rec(id_, author, content="", webhook=None, embeds=None, ts=None):
    return {"id": id_, "author_id": author, "author_name": "n", "webhook_id": webhook, "content": content,
            "embeds": embeds or [], "ts": ts or time.time()}


def test_untrusted_ignored_entirely(tmp_path):
    c, db = cfg(tmp_path), Database(tmp_path / "t.db")
    [(content, _)] = build_messages("lock", "mallory", files=["src/a"], text="x", ttl=60)
    assert not ingest(db, c, rec(1, 666, content), own_id=10)
    assert db.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_trusted_flow_and_own_actions(tmp_path):
    c, db = cfg(tmp_path), Database(tmp_path / "t.db")
    [(lock, _)] = build_messages("lock", "bob", files=["src/a"], text="x", ttl=60)
    [(mine, _)] = build_messages("plan", "me", files=[], text="hi")
    ingest_many(db, c, [rec(5, 20, lock), rec(6, 10, mine), rec(7, 666, lock)], own_id=10)
    assert [l.agent for l in db.active_locks()] == ["bob"]
    assert db.last_seen_id() == 7
    assert [r["type"] for r in db.own_actions(5)] == ["plan"]
    [(rel, _)] = build_messages("release", "bob", all_=True)
    ingest_many(db, c, [rec(8, 20, rel)], own_id=10)
    assert db.active_locks() == []


def test_webhook_rules(tmp_path):
    c, db = cfg(tmp_path), Database(tmp_path / "t.db")
    embed = {"title": "[r:main] 1 new commit", "description": "[`abc1234`](http://u) hi - al"}
    assert not is_trusted(c, rec(1, 98, webhook=98), None)
    ingest(db, c, rec(1, 98, webhook=98, embeds=[embed]), None)
    assert db.recent_commits(5) == []
    # a trusted webhook cannot create lock events
    [(lock, _)] = build_messages("lock", "x", files=["a"], text="", ttl=5)
    ingest(db, c, rec(2, 99, lock, webhook=99, embeds=[embed]), None)
    ingest(db, c, rec(2, 99, lock, webhook=99, embeds=[embed]), None)  # idempotent
    cs = db.recent_commits(5)
    assert len(cs) == 1 and cs[0].sha == "abc1234" and cs[0].branch == "main"
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
