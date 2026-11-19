import asyncio
import gc

import pytest

from backend import server as serving


class _Base:
    def __init__(self, *, stop=False, fail=None):
        self.should_exit, self.stop, self.fail, self.sockets = False, stop, fail, "unset"

    async def startup(self, sockets=None):
        self.sockets = sockets
        if self.fail:
            raise self.fail
        if self.stop:
            self.should_exit = True


class _Server(serving._FreezeAfterStartup, _Base):
    pass


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(gc, "collect", lambda *a: seen.append("collect") or 0)
    monkeypatch.setattr(gc, "freeze", lambda: seen.append("freeze"))
    return seen


def test_a_started_server_collects_then_freezes_its_heap_once(calls):
    started = _Server()
    asyncio.run(started.startup(sockets=["sock"]))
    assert started.sockets == ["sock"] and calls == ["collect", "freeze"]


def test_a_stop_asked_for_while_starting_freezes_nothing(calls):
    asyncio.run(_Server(stop=True).startup())
    assert calls == []


def test_a_failed_startup_raises_and_freezes_nothing(calls):
    with pytest.raises(SystemExit):
        asyncio.run(_Server(fail=SystemExit(3)).startup())
    assert calls == []
