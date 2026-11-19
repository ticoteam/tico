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


def _counting(gate=None):
    """A read that counts its runs and, given `gate` (an asyncio.Event), holds each run until the gate opens."""
    calls = []

    async def compute():
        calls.append(1)
        if gate is not None:
            await gate.wait()
        return JSONResponse({"n": len(calls)})

    return calls, compute


async def _all_at_once(reads, requests, gate):
    """Start every request, let each reach its answer (computing or joined), then open the gate."""
    tasks = [asyncio.ensure_future(one) for one in requests]
    while reads.computed + reads.joined < len(tasks):
        await asyncio.sleep(0)
    gate.set()
    return await asyncio.gather(*tasks)


def test_identical_reads_at_once_compute_once(monkeypatch):
    async def run():
        gate = asyncio.Event()
        reads, (calls, compute) = shared_reads.SharedReads(), _counting(gate)
        key = shared_reads.key_of(_request(), "/api/v2/threads/rail")
        answers = await _all_at_once(reads, [reads.answer(key, 0, compute) for _ in range(10)], gate)
        assert len(calls) == 1 and {a.body for a in answers} == {b'{"n":1}'}
        assert len({id(a) for a in answers}) == 10  # each its own copy
        assert (reads.computed, reads.joined) == (1, 9)

    asyncio.run(run())


def test_below_zero_shares_nothing_and_zero_shares_only_while_computing(monkeypatch):
    """Tests run with TICO_READ_CACHE_SECONDS=-1 (conftest.py): concurrent identical reads each run. 0 shares a read
    only while it is being computed."""
    rail = "/api/v2/threads/rail"
    ticks = iter(range(1, 1_000_000))
    monkeypatch.setattr(shared_reads, "clock", lambda: float(next(ticks)))  # time moves on between reads

    async def run(seconds):
        monkeypatch.setenv("TICO_READ_CACHE_SECONDS", seconds)
        store, gate = SimpleNamespace(), asyncio.Event()
        calls, compute = _counting(gate)
        requests = [shared_reads.answer(store, _request(), rail, compute) for _ in range(4)]
        if seconds == "-1":
            gate.set()
            await asyncio.gather(*requests)
        else:
            await _all_at_once(shared_reads.of(store), requests, gate)
        await shared_reads.answer(store, _request(), rail, compute)  # after the computation ended
        return len(calls)

    assert asyncio.run(run("-1")) == 5
    assert asyncio.run(run("0")) == 2


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
        reads, calls, gate = shared_reads.SharedReads(), [], asyncio.Event()

        async def compute():
            calls.append(1)
            await gate.wait()
            return StreamingResponse(iter([b"x"]))

        def own():  # each request has its own compute, as backend/names.py makes one per request
            async def mine():
                return await compute()

            return mine

        key = shared_reads.key_of(_request(), "/api/v2/threads/rail")
        await _all_at_once(reads, [reads.answer(key, 0, own()) for _ in range(3)], gate)
        assert len(calls) == 3

    asyncio.run(run())


def test_a_callers_own_write_drops_their_kept_answers(monkeypatch):
    monkeypatch.setenv("TICO_READ_CACHE_SECONDS", "5")

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
