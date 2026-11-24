"""Picking the bot's Discord status text. Pure logic, no Discord imports."""

from __future__ import annotations

import random
from collections.abc import Sequence


def pick_status(statuses: Sequence[str], previous: str | None = None, rng: random.Random | None = None) -> str | None:
    """Pick a random status. With two or more choices, never repeat `previous`.

    Returns None when the list is empty.
    """
    if not statuses:
        return None
    pool = [s for s in statuses if s != previous] or list(statuses)
    return (rng or random).choice(pool)
