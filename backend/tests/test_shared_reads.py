"""A person's identical reads are computed once, and reused for a short time (backend/shared_reads.py)."""

import asyncio
from types import SimpleNamespace

from starlette.responses import JSONResponse, StreamingResponse

from backend import shared_reads


def _request(method="GET", route_path="/api/v2/threads/rail", who="human:ana", role="human", query=b""):
    class Identity(SimpleNamespace):
        def __repr__(self):
            return f"Identity({self.actor}, {self.role})"

    identity = Identity(role=role, via=None, support=None, view=None, actor=who)
    return SimpleNamespace(
        method=method,
        state=SimpleNamespace(identity=identity, delegation=None),
        path_params={},
        headers={},
        url=SimpleNamespace(path=route_path),
        scope={"query_string": query},
    )


def _counting():
    calls = []

    async def compute():
        calls.append(1)
        await asyncio.sleep(0.01)
        return JSONResponse({"n": len(calls)})

    return calls, compute


def test_identical_reads_at_once_compute_once(monkeypatch):
    async def run():
        reads, (calls, compute) = shared_reads.SharedReads(), _counting()
        key = shared_reads.key_of(_request(), "/api/v2/threads/rail")
        answers = await asyncio.gather(*(reads.answer(key, 0, compute) for _ in range(10)))
        assert len(calls) == 1 and {a.body for a in answers} == {b'{"n":1}'}
        assert len({id(a) for a in answers}) == 10  # each its own copy

    asyncio.run(run())


def test_an_answer_serves_again_until_it_is_older_than_the_window(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(shared_reads, "clock", lambda: now[0])
    monkeypatch.setenv("TICO_READ_CACHE_SECONDS", "1")

    async def run():
        reads, (calls, compute) = shared_reads.SharedReads(), _counting()
        key = shared_reads.key_of(_request(), "/api/v2/threads/rail")
        await reads.answer(key, 0, compute)
        now[0] += 0.9
        await reads.answer(key, 0, compute)
        assert len(calls) == 1
        now[0] += 0.2
        await reads.answer(key, 0, compute)
        assert len(calls) == 2

    asyncio.run(run())


def test_who_and_what_decide_whether_a_read_is_shared():
    rail = "/api/v2/threads/rail"
    assert shared_reads.key_of(_request(who="human:ana"), rail) != shared_reads.key_of(_request(who="human:ben"), rail)
    assert shared_reads.key_of(_request(query=b"a=1"), rail) != shared_reads.key_of(_request(query=b"a=2"), rail)
    assert shared_reads.key_of(_request(method="POST"), rail) is None
    assert shared_reads.key_of(_request(role="runner"), rail) is None
    assert shared_reads.key_of(_request(), "/api/v2/events") is None
    assert shared_reads.key_of(_request(), "/healthz") is None
    assert shared_reads.key_of(_request(), "/api/v2/bots") is not None


def test_a_streamed_answer_is_never_handed_to_another_request():
    async def run():
        reads, calls = shared_reads.SharedReads(), []

        async def compute():
            calls.append(1)
            await asyncio.sleep(0.01)
            return StreamingResponse(iter([b"x"]))

        key = shared_reads.key_of(_request(), "/api/v2/threads/rail")
        await asyncio.gather(*(reads.answer(key, 0, compute) for _ in range(3)))
        assert len(calls) == 3

    asyncio.run(run())


def test_a_callers_own_write_drops_their_kept_answers():
    async def run():
        store = SimpleNamespace()
        reads, (calls, compute) = shared_reads.of(store), _counting()
        key = shared_reads.key_of(_request(), "/api/v2/threads/rail")
        await reads.answer(key, 0, compute)
        shared_reads.wrote(store, _request(method="POST", who="human:ben"))  # someone else's write: kept
        await reads.answer(key, 0, compute)
        assert len(calls) == 1
        shared_reads.wrote(store, _request(method="POST"))
        await reads.answer(key, 0, compute)
        assert len(calls) == 2

    asyncio.run(run())
