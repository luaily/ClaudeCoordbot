import random

from coordbot.status import pick_status


def test_empty_gives_none():
    assert pick_status([]) is None
    assert pick_status([], "x") is None


def test_single_status_is_repeated():
    assert pick_status(["only"], "only") == "only"


def test_never_repeats_previous():
    rng = random.Random(1)
    options = ["a", "b", "c"]
    prev = None
    for _ in range(200):
        new = pick_status(options, prev, rng)
        assert new in options and new != prev
        prev = new


def test_two_options_alternate():
    assert pick_status(["a", "b"], "a") == "b"
    assert pick_status(["a", "b"], "b") == "a"


def test_unknown_previous_ok():
    assert pick_status(["a", "b"], "zzz", random.Random(0)) in ("a", "b")
