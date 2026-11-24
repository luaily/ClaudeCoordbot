import time

from coordbot.config import Member, Team, parse_config
from coordbot.db import Database
from coordbot.ingest import ACK, ingest, record_ack, should_ack
from coordbot.protocol import build_messages


def setup(tmp_path):
    team = Team(tmp_path / "t.toml", "p", 1, frozenset(), frozenset(), True, "https://x", None,
                (Member("a", 1, 101, "Alice"), Member("b", 2, 202, "Bob")))
    cfg = parse_config({"bot_token": "x", "agent_name": "a"}, tmp_path / "c.toml", team)
    return cfg, Database(tmp_path / "a.db")


def rec(i, author, content, age=0.0, **kw):
    return {"id": i, "author_id": author, "author_name": "n", "webhook_id": None, "content": content,
            "embeds": [], "ts": time.time() - age, **kw}


def coord(agent="b"):
    return build_messages("plan", agent, text="x")[0][0]


def test_should_ack_rules(tmp_path):
    cfg, db = setup(tmp_path)
    r = rec(10, 2, coord())
    ingest(db, cfg, r, 1)
    assert should_ack(db, cfg, r, 1)
    db.mark_ack_sent(10)
    assert not should_ack(db, cfg, r, 1)  # never twice
    assert not should_ack(db, cfg, rec(11, 1, coord("a")), 1)  # not our own
    assert not should_ack(db, cfg, rec(12, 999, coord()), 1)  # untrusted
    assert not should_ack(db, cfg, rec(13, 2, coord(), age=3600), 1)  # old history
    assert not should_ack(db, cfg, rec(14, 2, "just chat"), 1)  # plain chatter
    assert not should_ack(db, cfg, rec(15, 2, coord(), webhook_id=5), 1)
    human = rec(16, 202, "hey a", mentions=[1])  # Bob (human) talks to us: inbox -> ack
    ingest(db, cfg, human, 1)
    assert should_ack(db, cfg, human, 1)


def test_record_ack_only_on_our_posts(tmp_path):
    cfg, db = setup(tmp_path)
    ingest(db, cfg, rec(20, 1, coord("a")), 1)  # our post
    ingest(db, cfg, rec(21, 2, coord()), 1)  # theirs
    assert record_ack(db, cfg, 1, 20, 2, ACK)
    assert not record_ack(db, cfg, 1, 20, 2, ACK)  # duplicate
    assert record_ack(db, cfg, 1, 20, 202, ACK)  # a linked human counts too
    assert not record_ack(db, cfg, 1, 20, 999, ACK)  # untrusted
    assert not record_ack(db, cfg, 1, 20, 2, "👍")
    assert not record_ack(db, cfg, 1, 21, 2, ACK)  # not our message
    assert not record_ack(db, cfg, 1, 20, 1, ACK)  # ourselves
    assert db.acks_for([20]) == {20: ["b", "Bob"]}
    db.remove_ack(20, 2)
    assert db.acks_for([20]) == {20: ["Bob"]}
