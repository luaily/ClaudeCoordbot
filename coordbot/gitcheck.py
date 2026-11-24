"""Git preflight: what will happen if this agent pulls or pushes now, and is a conflict likely?

Read-only apart from `git fetch`. The agent must show the warnings to its user before it
pulls or pushes; coordbot never runs pull/push itself.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .protocol import any_overlap

Runner = Callable[[list[str], Path], tuple[int, str]]

BIG_CHANGE_LINES = 400  # suggest splitting commits above this many changed lines
BIG_CHANGE_FILES = 15


def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr if p.returncode else "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


@dataclass
class Preflight:
    ok: bool
    branch: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    dirty_files: list[str] = field(default_factory=list)
    incoming_files: list[str] = field(default_factory=list)
    outgoing_files: list[str] = field(default_factory=list)
    overlap_files: list[str] = field(default_factory=list)
    locked_by_others: list[str] = field(default_factory=list)
    changed_lines: int = 0
    warnings: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _lines(out: str) -> list[str]:
    return [line.strip() for line in out.splitlines() if line.strip()]


def preflight(repo: Path, others_locked: list[str] | None = None, run: Runner = run_git,
              fetch: bool = True) -> Preflight:
    """Inspect `repo` against its upstream. `others_locked` = paths other agents hold."""
    code, top = run(["rev-parse", "--show-toplevel"], repo)
    if code != 0:
        return Preflight(False, warnings=["This folder is not a git repository."])
    root = Path(top.strip())
    pf = Preflight(True)
    _, branch = run(["rev-parse", "--abbrev-ref", "HEAD"], root)
    pf.branch = branch.strip()
    if fetch:
        code, out = run(["fetch", "--quiet"], root)
        if code != 0:
            pf.warnings.append(f"`git fetch` failed, so remote changes are unknown: {out.strip()[:200]}")
    code, up = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root)
    pf.upstream = up.strip() if code == 0 else ""

    _, status = run(["status", "--porcelain"], root)
    pf.dirty_files = [line[3:].split(" -> ")[-1] for line in status.splitlines() if len(line) > 3]

    if pf.upstream:
        code, counts = run(["rev-list", "--left-right", "--count", "HEAD...@{u}"], root)
        if code == 0 and counts.split():
            ahead, behind = counts.split()[:2]
            pf.ahead, pf.behind = int(ahead), int(behind)
        if pf.behind:
            _, inc = run(["diff", "--name-only", "HEAD...@{u}"], root)
            pf.incoming_files = _lines(inc)
        if pf.ahead:
            _, outg = run(["diff", "--name-only", "@{u}...HEAD"], root)
            pf.outgoing_files = _lines(outg)
            _, stat = run(["diff", "--shortstat", "@{u}...HEAD"], root)
            nums = [int(w) for w in stat.replace(",", " ").split() if w.isdigit()]
            pf.changed_lines = sum(nums[1:]) if len(nums) > 1 else 0
    else:
        pf.warnings.append(f"Branch `{pf.branch}` has no upstream yet: the first push needs "
                           f"`git push -u origin {pf.branch}`.")

    mine = sorted(set(pf.dirty_files) | set(pf.outgoing_files))
    pf.overlap_files = sorted(f for f in pf.incoming_files if any_overlap([f], mine))
    if others_locked:
        pf.locked_by_others = sorted(f for f in mine if any_overlap([f], others_locked))

    # ----- warnings & advice (shown to the human before any pull/push) -----
    if pf.overlap_files:
        pf.warnings.append(f"CONFLICT RISK: {len(pf.overlap_files)} file(s) changed both here and on the remote: "
                           + ", ".join(pf.overlap_files[:10]))
        pf.advice.append("Stop and tell your user before pulling. Commit or stash your work first, then "
                         "`git pull --rebase` and resolve conflicts file by file with them.")
    if pf.locked_by_others:
        pf.warnings.append("You changed files another agent has locked: " + ", ".join(pf.locked_by_others[:10]))
        pf.advice.append("Do not push these. Tell your user; revert them or wait for the lock to be released.")
    if pf.behind and pf.ahead:
        pf.advice.append(f"Your branch and the remote have both moved ({pf.ahead} ahead, {pf.behind} behind): "
                         "`git pull --rebase` before pushing. Never force-push.")
    elif pf.behind:
        pf.advice.append(f"{pf.behind} new commit(s) on the remote: `git pull --rebase` before editing.")
    if pf.dirty_files and pf.behind:
        pf.advice.append("You have uncommitted changes: commit (or stash) before pulling.")
    if pf.changed_lines > BIG_CHANGE_LINES or len(pf.outgoing_files) > BIG_CHANGE_FILES:
        pf.warnings.append(f"Large push ({pf.changed_lines} lines, {len(pf.outgoing_files)} files).")
        pf.advice.append("Prefer smaller commits, or push to a branch and open a pull request so others can review.")
    if pf.branch in ("main", "master") and pf.ahead and len(pf.outgoing_files) > BIG_CHANGE_FILES:
        pf.advice.append("Big change straight to the main branch: consider a feature branch instead.")
    if not pf.warnings and not pf.advice:
        pf.advice.append("Clean: nothing risky found.")
    return pf
