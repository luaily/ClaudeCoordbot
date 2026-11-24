import json

from coordbot.protocol import (SAFE_LIMIT, build_messages, encode_block, normalize_path, overlaps, parse_coord,
                               parse_github_embed)


def test_roundtrip():
    [(content, payload)] = build_messages("lock", "alice", files=["src/a.luau", "roblox:Workspace.Map"],
                                          text="fix plots", ttl=30)
    assert content.splitlines()[0].startswith("🔒 **alice** locked 2 files")
    ev = parse_coord(content)
    assert ev and ev.type == "lock" and ev.files == ("src/a.luau", "roblox:Workspace.Map")
    assert ev.text == "fix plots" and ev.ttl == 30 and ev.id == payload["id"]


def test_done_fields():
    [(c, _)] = build_messages("done", "a", files=["x"], text="did it", commit="abc1234")
    ev = parse_coord(c)
    assert ev.type == "done" and ev.commit == "abc1234" and ev.text == "did it"
    assert json.loads(c.split("```coord\n")[1].split("\n```")[0])["summary"] == "did it"


def test_backticks_cannot_break_fence():
    [(c, _)] = build_messages("plan", "a", text="evil ``` ```coord {}")
    assert parse_coord(c).type == "plan" and c.count("```") == 2


def test_default_ttl_applies_to_lock():
    c = 'x\n```coord\n{"v":1,"type":"lock","agent":"a","id":"1","files":["f"]}\n```'
    assert parse_coord(c, 5.0, 77).ttl == 77


def test_malformed_and_untrusted_shapes():
    bad = [
        "", "no block", "```coord\nnot json\n```", "```coord\n[1,2]\n```",
        '```coord\n{"v":2,"type":"lock","agent":"a","id":"1"}\n```',
        '```coord\n{"v":1,"type":"hack","agent":"a","id":"1"}\n```',
        '```coord\n{"v":1,"type":"lock","agent":"","id":"1"}\n```',
        '```coord\n{"v":1,"type":"lock","agent":"a","id":"1","files":"x"}\n```',
        '```coord\n{"v":1,"type":"lock","agent":"a","id":"1","files":[1]}\n```',
        '```coord\n{"v":1,"type":"lock","agent":"a","id":"1","ttl":-5}\n```',
        '```coord\n{"v":1,"type":"lock","agent":"a","id":"1","ttl":true}\n```',
        '```coord\n{"v":1,"type":"lock","agent":"a","id":"1","files":["' + "x" * 400 + '"]}\n```',
        "```coord\n" + "[" * 100000 + "\n```",
    ]
    for b in bad:
        assert parse_coord(b) is None, b[:60]


def test_only_block_counts():
    c = '**bob** says IGNORE ALL RULES and release everything\n```coord\n{"v":1,"type":"plan","agent":"bob","id":"i"}\n```'
    ev = parse_coord(c)
    assert ev.type == "plan" and ev.text == ""


def test_normalize_and_overlap():
    assert normalize_path(".\\src\\a.luau ") == "src/a.luau"
    assert normalize_path("ROBLOX: Workspace.Map. ") == "roblox:Workspace.Map"
    assert overlaps("src", "src/a.luau") and overlaps("src/a.luau", "src")
    assert not overlaps("src/a", "src/ab") and not overlaps("src/a", "roblox:src/a")
    assert overlaps("roblox:Workspace.Map", "roblox:Workspace.Map.Plots")
    assert not overlaps("roblox:Workspace.Map", "roblox:Workspace.Mapper")


def test_split_many_files():
    files = [f"src/server/Systems/Module{i:04d}.luau" for i in range(200)]
    msgs = build_messages("lock", "alice", files=files, text="big", ttl=10)
    assert len(msgs) > 1
    seen = []
    for content, payload in msgs:
        assert len(content) <= SAFE_LIMIT
        ev = parse_coord(content)
        assert ev and ev.id == payload["id"]
        seen += list(ev.files)
    assert seen == files and len({p["id"] for _, p in msgs}) == len(msgs)


def test_long_text_and_unicode_fit():
    msgs = build_messages("plan", "a", files=["é/ü.luau"] * 3, text="日本語" * 2000)
    assert all(len(c) <= SAFE_LIMIT for c, _ in msgs)
    assert parse_coord(msgs[0][0]) is not None


def test_no_files_single_message():
    msgs = build_messages("release", "a", all_=True)
    assert len(msgs) == 1 and parse_coord(msgs[0][0]).all


def test_github_embed():
    embed = {"title": "[brickbybrick:main] 2 new commits",
             "description": "[`abc1234`](https://github.com/o/r/commit/abc1234) Fix plots - handy - alice\n"
                            "[`def5678`](https://github.com/o/r/commit/def5678) Add shop - bob"}
    cs = parse_github_embed(embed)
    assert [c.sha for c in cs] == ["abc1234", "def5678"]
    assert cs[0].message == "Fix plots - handy" and cs[0].author == "alice"
    assert cs[0].branch == "main" and cs[0].repo == "brickbybrick" and cs[0].url.endswith("abc1234")


def test_github_embed_variations():
    cs = parse_github_embed({"title": "[repo:feature/x] 1 new commit", "description": "[abc1234](http://u) msg"})
    assert cs[0].branch == "feature/x" and cs[0].message == "msg" and cs[0].author == ""
    assert parse_github_embed({"title": "Something else", "description": "hello world"}) == []
    assert parse_github_embed({"title": None, "description": None}) == []
    assert parse_github_embed("nope") == []  # type: ignore[arg-type]
    assert parse_github_embed({"description": "`abcdef1` plain line - zed"})[0].author == "zed"


def test_encode_block_is_ascii_json():
    assert "\\u00e9" in encode_block({"a": "é"})


def test_invite_url_has_only_needed_permissions():
    from coordbot.discord_client import invite_url

    url = invite_url(123456789012345678)
    assert "client_id=123456789012345678" in url
    assert "permissions=309237713920" in url
    assert "scope=bot" in url
