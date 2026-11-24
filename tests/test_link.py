import base64
import hashlib
import tomllib

from coordbot.config import Member, load_team
from coordbot.oauth import authorize_url, pkce_pair, upsert_member

TEAM = """# header
project = "demo"
channel_id = 5
trusted_user_ids = [9]
required = true
"""


def test_pkce_and_url():
    v, c = pkce_pair()
    assert 43 <= len(v) <= 128
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    url = authorize_url(77, 53682, "st", c)
    assert "client_id=77" in url and "scope=identify" in url and "code_challenge_method=S256" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A53682%2Fcallback" in url


def test_upsert_member(tmp_path):
    p = tmp_path / ".coordbot" / "team.toml"
    p.parent.mkdir()
    p.write_text(TEAM)
    assert upsert_member(p, Member("alice", 1, 11, 'Al "the" ice'))
    assert upsert_member(p, Member("bob", 2, 22, "Bob"))
    assert not upsert_member(p, Member("bob", 2, 22, "Bob"))  # unchanged
    assert upsert_member(p, Member("alice", 1, 12, "Alice"))  # replaced
    text = p.read_text()
    assert text.startswith("# header") and text.count("members = [") == 1
    data = tomllib.loads(text)
    assert [(m["agent"], m["user_id"]) for m in data["members"]] == [("alice", 12), ("bob", 22)]
    team = load_team(tmp_path)
    assert {m.agent for m in team.members} == {"alice", "bob"}


def test_members_are_trusted(tmp_path):
    from coordbot.config import parse_config

    p = tmp_path / ".coordbot" / "team.toml"
    p.parent.mkdir()
    p.write_text(TEAM)
    upsert_member(p, Member("bob", 2, 22, "Bob"))
    cfg = parse_config({"bot_token": "t", "agent_name": "me"}, tmp_path / "c.toml", load_team(tmp_path))
    assert {2, 22, 9} <= cfg.trusted_user_ids
    assert cfg.member_by_agent("BOB").user_id == 22 and cfg.member_humans() == frozenset({22})
