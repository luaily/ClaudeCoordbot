from pathlib import Path

from coordbot.gitcheck import preflight


def fake(responses):
    def run(args, cwd):
        key = " ".join(args)
        for k, v in responses.items():
            if key.startswith(k):
                return v
        return 0, ""
    return run


BASE = {"rev-parse --show-toplevel": (0, "/repo\n"), "rev-parse --abbrev-ref HEAD": (0, "main\n"),
        "rev-parse --abbrev-ref --symbolic-full-name": (0, "origin/main\n")}


def test_clean():
    pf = preflight(Path("."), run=fake({**BASE, "rev-list": (0, "0\t0\n")}))
    assert pf.ok and not pf.warnings and pf.advice == ["Clean: nothing risky found."]


def test_conflict_risk_and_locked():
    pf = preflight(Path("."), ["src/b.luau"], run=fake({
        **BASE, "rev-list": (0, "1\t2\n"), "status --porcelain": (0, " M src/a.luau\n M src/b.luau\n"),
        "diff --name-only HEAD...@{u}": (0, "src/a.luau\n"), "diff --name-only @{u}...HEAD": (0, "src/c.luau\n"),
        "diff --shortstat": (0, " 1 file changed, 3 insertions(+)\n")}))
    assert pf.overlap_files == ["src/a.luau"]
    assert pf.locked_by_others == ["src/b.luau"]
    assert any("CONFLICT RISK" in w for w in pf.warnings)
    assert any("pull --rebase" in a for a in pf.advice)


def test_not_a_repo():
    pf = preflight(Path("."), run=fake({"rev-parse --show-toplevel": (128, "fatal")}))
    assert not pf.ok


def test_no_upstream():
    pf = preflight(Path("."), run=fake({**BASE, "rev-parse --abbrev-ref --symbolic-full-name": (128, "")}))
    assert any("no upstream" in w for w in pf.warnings)
