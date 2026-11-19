"""One person's identical reads run at most once a second, and never answer from before they arrived
(backend/shared_reads.py)."""

import asyncio
from types import SimpleNamespace

from starlette.responses import JSONResponse, StreamingResponse

from backend import shared_reads

RAIL = "/api/v2/threads/rail"


def _request(method="GET", route_path=RAIL, who="human:ana", role="human", query=b""):
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


class Reads:
    """A read that stays open until `release()`, counting how many ran; `own()` is one request's compute, as
    backend/names.py makes one per request."""

    def __init__(self, stream=False):
        self.ran = 0
        self.gate = asyncio.Event()
        self.stream = stream

    def release(self):
        self.gate.set()

    def own(self):
        async def compute():
            self.ran += 1
            n = self.ran
            await self.gate.wait()
            return StreamingResponse(iter([b"x"])) if self.stream else JSONResponse({"n": n})

        return compute


def _clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(shared_reads, "clock", lambda: now[0])
    return now


async def _arrive(reads, n, key, limiter, seconds=1.0):
    """n identical requests, each with its own compute; returns their tasks once all have arrived."""
    tasks = [asyncio.create_task(limiter.answer(key, reads.own(), seconds)) for _ in range(n)]
    for _ in range(5):
        await asyncio.sleep(0)
    return tasks


def test_ten_identical_reads_at_once_run_twice_at_most(monkeypatch):
    now = _clock(monkeypatch)

    async def run():
        limiter, reads = shared_reads.SharedReads(), Reads()
        key = shared_reads.key_of(_request(), RAIL)
        first = await _arrive(reads, 1, key, limiter)
        rest = await _arrive(reads, 9, key, limiter)  # they arrive while the first runs: they wait for the next read
        assert reads.ran == 1
        reads.release()
        assert (await first[0]).body == b'{"n":1}'
        now[0] += 1.0  # the queued read begins no sooner than a second after the first
        answers = await asyncio.gather(*rest)
        assert reads.ran == 2 and {a.body for a in answers} == {b'{"n":2}'}
        assert len({id(a) for a in answers}) == 9  # each its own copy

    asyncio.run(run())


def test_a_read_never_answers_with_a_read_begun_before_it_arrived(monkeypatch):
    _clock(monkeypatch)

    async def run():
        limiter, reads = shared_reads.SharedReads(), Reads()
        key = shared_reads.key_of(_request(), RAIL)
        first = await _arrive(reads, 1, key, limiter)
        late = await _arrive(reads, 1, key, limiter)
        reads.release()
        await first[0]
        assert reads.ran == 1 and not late[0].done()  # it does not take the first read's answer

    asyncio.run(run())


def test_a_read_a_second_later_runs_at_once(monkeypatch):
    now = _clock(monkeypatch)

    async def run():
        limiter, reads = shared_reads.SharedReads(), Reads()
        reads.release()
        key = shared_reads.key_of(_request(), RAIL)
        await limiter.answer(key, reads.own(), 1.0)
        now[0] += 1.0
        await limiter.answer(key, reads.own(), 1.0)
        assert reads.ran == 2 and limiter.joined == 0

    asyncio.run(run())


def test_who_and_what_decide_whether_reads_are_limited_together():
    assert shared_reads.key_of(_request(who="human:ana"), RAIL) != shared_reads.key_of(_request(who="human:ben"), RAIL)
    assert shared_reads.key_of(_request(query=b"a=1"), RAIL) != shared_reads.key_of(_request(query=b"a=2"), RAIL)
    assert shared_reads.key_of(_request(method="POST"), RAIL) is None
    assert shared_reads.key_of(_request(role="runner"), RAIL) is None
    assert shared_reads.key_of(_request(), "/api/v2/events") is None
    assert shared_reads.key_of(_request(), "/healthz") is None
    assert shared_reads.key_of(_request(), "/api/v2/bots") is not None


def test_a_streamed_answer_is_never_handed_to_another_request(monkeypatch):
    now = _clock(monkeypatch)

    async def run():
        limiter, reads = shared_reads.SharedReads(), Reads(stream=True)
        key = shared_reads.key_of(_request(), RAIL)
        first = await _arrive(reads, 1, key, limiter)
        rest = await _arrive(reads, 2, key, limiter)
        reads.release()
        await first[0]
        now[0] += 1.0
        await asyncio.gather(*rest)
        assert reads.ran == 3  # the queued read's creator, then the other request on its own

    asyncio.run(run())


def test_the_limit_is_off_below_zero(monkeypatch):
    monkeypatch.setenv("TICO_READ_RATE_SECONDS", "-1")
    calls = []

    async def compute():
        calls.append(1)
        return JSONResponse({})

    async def run():
        store = SimpleNamespace()
        request = SimpleNamespace(**vars(_request()))
        await asyncio.gather(*(shared_reads.answer(store, request, RAIL, compute) for _ in range(3)))
        assert len(calls) == 3

    asyncio.run(run())
