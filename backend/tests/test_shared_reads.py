"""A person's identical reads are computed once (backend/shared_reads.py): ten open apps re-reading the rail on one live
event cost one rail build, not ten. Never across callers, and never older than a logged write."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from backend import shared_reads as SR
from backend import thread_rail
from backend.tests.helpers.api import headers, post


def _answers(body):
    """A compute whose calls are counted and that answers `body` once `gate` opens."""
    state = {"calls": 0, "gate": asyncio.Event()}

    async def compute():
        state["calls"] += 1
        await state["gate"].wait()
        return JSONResponse(body if not callable(body) else body(state["calls"]))

    return compute, state


def test_requests_while_one_computes_share_it_and_a_newer_change_waits_for_the_next():
    async def go():
        shared = SR.SharedReads()
        compute, state = _answers(lambda n: {"n": n})
        key = ("route", "/path", b"", "who", "", "")
        first = [asyncio.ensure_future(shared.answer(key, 5, compute)) for _ in range(10)]
        await asyncio.sleep(0)
        # Asked after change 6 was logged: the computation from change 5 may have read before it, so this waits.
        later = [
            asyncio.ensure_future(shared.answer(key, 6, compute)),
            asyncio.ensure_future(shared.answer(key, 7, compute)),
        ]
        await asyncio.sleep(0)
        assert state["calls"] == 1
        state["gate"].set()
        done = await asyncio.gather(*first, *later)
        assert [r.body for r in done[:10]] == [b'{"n":1}'] * 10
        assert [r.body for r in done[10:]] == [b'{"n":2}'] * 2
        assert state["calls"] == 2
        # Kept, read at or after change 7: the same request again with no newer change is not computed.
        again = await shared.answer(key, 7, compute)
        assert again.body == b'{"n":2}' and state["calls"] == 2
        assert (await shared.answer(key, 8, compute)).body == b'{"n":3}'
        assert (shared.computed, shared.reused) == (3, 1)

    asyncio.run(go())


def test_a_kept_answer_expires_and_each_request_gets_its_own_copy(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(SR, "clock", lambda: now[0])

    async def go():
        shared = SR.SharedReads()
        compute, state = _answers(lambda n: {"n": n})
        state["gate"].set()
        key = ("k",)
        one = await shared.answer(key, 1, compute)
        one.headers["ETag"] = '"mine"'
        one.body = b"changed"
        two = await shared.answer(key, 1, compute)
        assert two.body == b'{"n":1}' and "etag" not in two.headers
        now[0] += SR.FRESH_SECONDS + 0.1
        assert (await shared.answer(key, 1, compute)).body == b'{"n":2}'

    asyncio.run(go())


def test_a_failed_or_unshareable_computation_leaves_each_request_its_own():
    async def go():
        shared = SR.SharedReads()
        calls = {"n": 0}
        gate = asyncio.Event()

        async def failing():
            calls["n"] += 1
            await gate.wait()
            if calls["n"] == 1:
                raise RuntimeError("the first read failed")
            return JSONResponse({"n": calls["n"]})

        tasks = [asyncio.ensure_future(shared.answer(("k",), 1, failing)) for _ in range(3)]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[0], RuntimeError)
        assert sorted(r.body for r in results[1:]) == [b'{"n":2}', b'{"n":3}']

        streamed = {"n": 0}

        async def stream():
            streamed["n"] += 1
            await asyncio.sleep(0)
            return StreamingResponse(iter([b"x"]))

        both = await asyncio.gather(shared.answer(("s",), 1, stream), shared.answer(("s",), 1, stream))
        assert streamed["n"] == 2 and both[0] is not both[1]

    asyncio.run(go())


def test_ten_identical_reads_build_the_rail_once_and_callers_never_share(api, monkeypatch):
    real = thread_rail.rail
    builds = []
    release = threading.Event()

    def counted(c, v, **kwargs):
        builds.append(v)
        release.wait(30)
        return real(c, v, **kwargs)

    monkeypatch.setattr(thread_rail, "rail", counted)
    # Held still, so no background change splits the burst; Changes has its own test.
    monkeypatch.setattr(SR.Changes, "__call__", lambda self: (1, 1))
    shared = SR.of(api.app.state.store)
    arrived = threading.Event()
    real_join = SR.SharedReads._join

    async def join(self, task, compute, owner):
        if self.computed + self.joined + 1 == 12:
            arrived.set()
        return await real_join(self, task, compute, owner)

    monkeypatch.setattr(SR.SharedReads, "_join", join)
    before = shared.computed
    tokens = ["ana-test"] * 10 + ["ben-test"] * 2
    with ThreadPoolExecutor(len(tokens)) as pool:
        waiting = [pool.submit(api.get, "/api/v2/threads/rail", headers=headers(t)) for t in tokens]
        assert arrived.wait(10), "the twelve reads never all arrived"
        release.set()
        answers = [w.result(timeout=30) for w in waiting]
    assert [a.status_code for a in answers] == [200] * len(tokens)
    assert len(builds) == 2 and shared.computed - before == 2
    ana, ben = {a.content for a in answers[:10]}, {a.content for a in answers[10:]}
    assert len(ana) == 1 and len(ben) == 1
    solo = api.get("/api/v2/threads/rail", headers=headers("ana-test"))
    assert solo.json()["threads"] == answers[0].json()["threads"]


def test_a_read_after_a_write_is_never_answered_from_before_it(api):
    path = "/api/v2/tasks?limit=100"
    first = api.get(path, headers=headers())
    assert first.status_code == 200
    again = api.get(path, headers=headers())
    assert again.content == first.content
    task = post(api, "tasks", {"owner": "ops", "title": "Review the sample report"})
    after = api.get(path, headers=headers())
    assert task["id"] in {row["id"] for row in after.json()["tasks"]}


def test_a_logged_change_moves_the_number_and_a_read_does_not(company):
    number = SR.changes(company)
    first = number()
    assert first is not None and number() == first
    with company.read() as c:
        c.execute("SELECT count(*) FROM tasks").fetchone()
    assert number() == first
    with company.transaction() as c:
        c.execute("INSERT INTO changes(topic,kind,created_at) VALUES('setup','sample','2026-11-19T00:00:00Z')")
    second = number()
    assert second is not None and second > first and number() == second


def test_a_queued_computation_every_waiter_left_never_runs():
    async def go():
        shared = SR.SharedReads()
        compute, state = _answers(lambda n: {"n": n})
        first = asyncio.ensure_future(shared.answer(("k",), 1, compute))
        await asyncio.sleep(0)
        queued = asyncio.ensure_future(shared.answer(("k",), 2, compute))
        await asyncio.sleep(0)
        queued.cancel()
        await asyncio.sleep(0)
        state["gate"].set()
        assert (await first).body == b'{"n":1}'
        await asyncio.sleep(0)
        assert state["calls"] == 1
        assert queued.cancelled()

    asyncio.run(go())
