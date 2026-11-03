"""A helper job exits, with status 0 for the supervisor to restart it, when the checkout's revision changes."""
import threading

from runner import freshness
from runner.importers import service as importers_service


def watch(heads, supervised=True, lines=None):
    return freshness.CodeWatch("Tico test", every=0, head=lambda: heads[0], supervised=lambda: supervised,
                               say=(lines if lines is not None else []).append)


def test_a_new_revision_is_stale_once_and_says_so():
    heads, lines = ["a" * 40], []
    w = watch(heads, lines=lines)
    assert not w.stale()
    heads[0] = "b" * 40
    assert w.stale() and "aaaaaaa to bbbbbbb" in lines[0] and "restarts it" in lines[0]


def test_nothing_exits_without_a_supervisor_or_when_the_revision_is_unknown():
    heads = ["a" * 40]
    unsupervised = watch(heads, supervised=False)
    unknown = watch(heads)
    heads[0] = "b" * 40
    assert not unsupervised.stale()
    heads[0] = ""                                   # git failed: keep running
    assert not unknown.stale()


def test_wait_returns_early_on_a_change_and_never_after_a_stop():
    heads = ["a" * 40]
    w = watch(heads)
    stop = threading.Event()
    heads[0] = "b" * 40
    assert w.wait(stop, 30) is True
    stop.set()
    assert watch(["a" * 40]).wait(stop, 30) is False


def test_the_importers_loop_returns_when_the_checkout_moves(monkeypatch):
    heads = iter(["a" * 40] + ["b" * 40] * 20)
    monkeypatch.setattr(freshness, "checkout_head", lambda root=None, run=None: next(heads))
    monkeypatch.setattr(freshness, "process_supervised", lambda: True)
    monkeypatch.setattr(importers_service, "GRANULARITY", 0.01)
    service = object.__new__(importers_service.ImporterService)
    service.stop, ticks = threading.Event(), []
    service.tick = lambda: ticks.append(1)
    service.run()                                   # returns instead of looping: exit status 0
    assert ticks and not service.stop.is_set()
