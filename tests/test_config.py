from pathlib import Path

import pytest

from coordbot.config import ConfigError, load_config, parse_config

GOOD = 'bot_token = "abc.def"\nchannel_id = 123\nagent_name = "alice-agent"\ntrusted_user_ids = [1, 2]\n'


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_valid(tmp_path):
    cfg = load_config(write(tmp_path, GOOD + "trusted_webhook_ids=[9]\n"))
    assert cfg.channel_id == 123 and cfg.trusted_user_ids == {1, 2} and cfg.trusted_webhook_ids == {9}
    assert cfg.db_path == tmp_path / "coordbot.db" and cfg.default_lock_ttl_minutes == 120
    assert "abc.def" not in repr(cfg)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Could not find"):
        load_config(tmp_path / "nope.toml")


def test_bad_toml(tmp_path):
    with pytest.raises(ConfigError, match="not valid"):
        load_config(write(tmp_path, "bot_token = oops"))


def test_missing_fields_listed_without_token(tmp_path):
    with pytest.raises(ConfigError) as e:
        parse_config({"bot_token": "PASTE_YOUR_BOT_TOKEN_HERE", "channel_id": 0}, tmp_path / "c.toml")
    msg = str(e.value)
    assert "channel_id is missing" in msg and "step 6" in msg and "placeholder" in msg and "agent_name" in msg


def test_bad_values(tmp_path):
    with pytest.raises(ConfigError) as e:
        parse_config({"bot_token": "x", "channel_id": "abc", "agent_name": "bad name!",
                      "trusted_user_ids": ["x", 5], "default_lock_ttl_minutes": 0}, tmp_path / "c.toml")
    msg = str(e.value)
    for needle in ("channel_id must", "agent_name may", "trusted_user_ids contains", "default_lock_ttl_minutes"):
        assert needle in msg


def test_string_ids_accepted(tmp_path):
    cfg = parse_config({"bot_token": "x", "channel_id": "123", "agent_name": "a", "trusted_user_ids": ["7"]},
                       tmp_path / "c.toml")
    assert cfg.channel_id == 123 and cfg.trusted_user_ids == {7}


BASE = {"bot_token": "x", "channel_id": 1, "agent_name": "a"}


def test_status_defaults_when_missing(tmp_path):
    cfg = parse_config(dict(BASE), tmp_path / "c.toml")
    assert cfg.statuses == () and cfg.status_minutes == 15


def test_status_valid(tmp_path):
    cfg = parse_config({**BASE, "statuses": ["  Hi  ", "There"], "status_minutes": 0}, tmp_path / "c.toml")
    assert cfg.statuses == ("Hi", "There") and cfg.status_minutes == 0
    assert parse_config({**BASE, "status_minutes": 1440}, tmp_path / "c.toml").status_minutes == 1440


@pytest.mark.parametrize("statuses", ["Hi", [""], ["  "], [5], ["x" * 129], ["ok"] * 101])
def test_status_invalid_list(tmp_path, statuses):
    with pytest.raises(ConfigError) as e:
        parse_config({**BASE, "statuses": statuses}, tmp_path / "c.toml")
    assert "status" in str(e.value)


def test_status_limits_inclusive(tmp_path):
    cfg = parse_config({**BASE, "statuses": ["x" * 128] * 100}, tmp_path / "c.toml")
    assert len(cfg.statuses) == 100


@pytest.mark.parametrize("minutes", [-1, 1441, "5", 1.5, True])
def test_status_minutes_invalid(tmp_path, minutes):
    with pytest.raises(ConfigError, match="status_minutes must be"):
        parse_config({**BASE, "status_minutes": minutes}, tmp_path / "c.toml")


def test_example_config_parses(tmp_path):
    import shutil
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "config.example.toml"
    dst = tmp_path / "config.toml"
    shutil.copy(src, dst)
    text = dst.read_text().replace("PASTE_YOUR_BOT_TOKEN_HERE", "tok").replace("channel_id = 0", "channel_id = 5")
    dst.write_text(text)
    cfg = load_config(dst)
    assert 8 <= len(cfg.statuses) <= 12 and cfg.status_minutes == 15
