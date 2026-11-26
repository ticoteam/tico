"""One app open builds a person's Needs you once, not once per request (backend/needs_share.py; spec "Build Needs you
once per change", done-when 1-5). Live on 18 Nov one open built the same list eight times, about 1,000 statements each.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import events
from backend import hubdb as H
from backend import needs_share
from backend import task_engine as E
from backend import views as V
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import Identity, get, headers

ANA = Identity("human:ana", "owner", "ana@acme.example")


@pytest.fixture(autouse=True)
def _fresh():
    needs_share.clear()
    yield
    needs_share.clear()


def _ask(api, title, person="human:ana", owner="human:ana"):
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:finance", title, "x", "bot:finance", lint=False)
        X.ok_ask(c, made["id"], "bot:finance", person, f"OK {title}")
    return made


def _items(api, who=ANA, **how):
    auth = api.app.state.auth
    with api.app.state.store.read_transaction() as c:
        return V.needs_items(c, auth, who, **how)


def _alone(api, monkeypatch, who=ANA, **how):
    """The list as a build of its own gives it, sharing nothing (today's answer)."""
    with monkeypatch.context() as patched:
        patched.setattr(needs_share, "mark", lambda c: None)
        return _items(api, who, **how)


def test_one_open_builds_the_list_once_and_each_answer_is_todays(api, monkeypatch):
    """Done-when 1: needs-you twice, four live-stream starts, the company floor and two place reads arrive together, with
    no write in between: one build, held until the other eight wait on it, and each answer equals the same request
    answered with nothing shared (today's answer, built before the open)."""
    from concurrent.futures import Future

    for k in range(3):
        _ask(api, f"Payroll run {k}")
    auth = api.app.state.auth
    routes = ["needs-you", "needs-you?count=1", "company/floor", "places/dashboard", "places/person:me"]

    def baseline():
        with api.app.state.store.read_transaction() as c:
            return events.needs_baseline(c, auth, ANA, None)

    calls = (
        [lambda r=r: get(api, r) for r in routes[:2]] + [baseline] * 4 + [lambda r=r: get(api, r) for r in routes[2:]]
    )
    assert len(calls) == 9
    with monkeypatch.context() as alone:
        alone.setattr(needs_share, "mark", lambda c: None)
        today = [call() for call in calls]
    needs_share.clear()

    waiting = threading.Semaphore(0)

    class Counted(Future):
        def result(self, timeout=None):
            waiting.release()
            return super().result(timeout)

    real = V._needs_items

    def held(*args, **kwargs):
        for _ in range(len(calls) - 1):
            assert waiting.acquire(timeout=10), "a request of the open never waited on this build"
        return real(*args, **kwargs)

    monkeypatch.setattr(needs_share, "Future", Counted)
    monkeypatch.setattr(V, "_needs_items", held)
    before = needs_share.builds
    with ThreadPoolExecutor(len(calls)) as pool:
        answers = list(pool.map(lambda call: call(), calls))
    assert needs_share.builds - before == 1
    assert answers[1]["count"] == 3 and len(answers[0]["items"]) == 3
    for k, (got, want) in enumerate(zip(answers, today, strict=True)):
        assert got == want, f"request {k} of the open differs from its answer built alone"


def test_callers_that_ask_together_wait_on_one_build(api, monkeypatch):
    """Six callers at once: the first builds, and its build is held until the other five wait on it."""
    from concurrent.futures import Future

    _ask(api, "Pick a payroll vendor")
    callers = 6
    waiting = threading.Semaphore(0)

    class Counted(Future):
        def result(self, timeout=None):
            waiting.release()
            return super().result(timeout)

    real = V._needs_items

    def held(*args, **kwargs):
        for _ in range(callers - 1):
            assert waiting.acquire(timeout=10), "the other callers never waited on this build"
        return real(*args, **kwargs)

    monkeypatch.setattr(needs_share, "Future", Counted)
    monkeypatch.setattr(V, "_needs_items", held)
    before = needs_share.builds
    with ThreadPoolExecutor(callers) as pool:
        lists = list(pool.map(lambda _: _items(api), range(callers)))
    assert needs_share.builds - before == 1
    assert all(found == lists[0] for found in lists) and len(lists[0]) == 1
    # Each caller has a copy of its own: changing one changes no other's.
    monkeypatch.setattr(V, "_needs_items", real)
    lists[0][0]["line"] = "changed"
    assert _items(api)[0]["line"] != "changed"


def test_a_write_that_adds_an_ask_is_listed_on_the_next_request(api):
    """Done-when 2."""
    _ask(api, "Renew the domain")
    assert len(get(api, "needs-you")["items"]) == 1
    _ask(api, "Book the barn")
    assert sorted(i["task"]["title"] for i in get(api, "needs-you")["items"]) == ["Book the barn", "Renew the domain"]


def test_an_ask_whose_snooze_ends_is_listed_five_seconds_later(api, monkeypatch, time_machine):
    """Done-when 3: no write moves the list when a snooze ends by the clock; the kept list is at most TTL old."""
    from datetime import timedelta

    from backend import ask_snoozes

    time_machine.move_to(H.parse_ts(H.now()), tick=False)
    _ask(api, "Renew the domain")
    item = get(api, "needs-you")["items"][0]
    until = H.shift(H.now(), seconds=1)
    with api.app.state.store.transaction() as c:
        ask_snoozes.snooze(c, api.app.state.auth, ANA, item["id"], until)
    assert get(api, "needs-you")["items"] == []
    time_machine.move_to(H.parse_ts(until) + timedelta(seconds=1), tick=False)
    at = needs_share.clock()
    monkeypatch.setattr(needs_share, "clock", lambda: at + needs_share.TTL)
    assert [i["id"] for i in get(api, "needs-you")["items"]] == [item["id"]]


def test_two_people_each_get_their_own_list_and_a_lost_task_leaves_it(api):
    """Done-when 4: never one person's list for another, and access taken away shows on the next request."""
    from backend.tests.helpers.api import post

    _ask(api, "Ana's payroll OK")
    _ask(api, "Ben's payroll OK", person="human:ben")
    with ThreadPoolExecutor(4) as pool:
        ana, ben, ana2, ben2 = pool.map(
            lambda token: api.get("/api/v2/needs-you", headers=headers(token)).json(),
            ["ana-test", "ben-test", "ana-test", "ben-test"],
        )
    assert (
        [i["task"]["title"] for i in ana["items"]]
        == [i["task"]["title"] for i in ana2["items"]]
        == ["Ana's payroll OK"]
    )
    assert (
        [i["task"]["title"] for i in ben["items"]]
        == [i["task"]["title"] for i in ben2["items"]]
        == ["Ben's payroll OK"]
    )
    # Ben reads a private task he is asked on; once he is no longer one of its readers, his next list leaves it out.
    made = post(api, "tasks", {"title": "Ben's private plan", "body": "x", "owner": "human:ana", "private": True})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET readers=json_array('human:ben') WHERE id=?", (made["id"],))
        X.ok_ask(c, made["id"], "human:ana", "human:ben", "OK the plan")
    assert "Ben's private plan" in [i["task"]["title"] for i in get(api, "needs-you", "ben-test")["items"]]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET readers='[]' WHERE id=?", (made["id"],))
    assert "Ben's private plan" not in [i["task"]["title"] for i in get(api, "needs-you", "ben-test")["items"]]


def test_a_connection_that_wrote_builds_its_own(api):
    """Its own writes are not committed: no one else reads them, so its list is neither taken nor kept."""
    _ask(api, "Renew the domain")
    auth = api.app.state.auth
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:finance", "Inside the write", "x", "bot:finance", lint=False)
        X.ok_ask(c, made["id"], "bot:finance", "human:ana", "OK it")
        assert needs_share.mark(c) is None
        assert len(V.needs_items(c, auth, ANA)) == 2
    assert len(_items(api)) == 2


def test_one_build_reads_each_tasks_prs_metadata_and_reviews_together(api, monkeypatch):
    """Done-when 5, on the spec's fixture shaped like production (bench/needs-you-132-lookups.py): 132 candidate tasks
    asking the person's OK, each with a linked GitHub PR Tico has read, its saved metadata and a current QA page, and
    33 ❓. Their links, metadata, pages and reviews are read for all of them together: at most six statements in all,
    none per task (live, 18 Nov: about 400 a build; main on this fixture: 135), and the list is the one a build that
    reads each task's reviews on its own gives."""
    import importlib.util
    import pathlib

    from backend import item_metadata as IM
    from backend import task_pages as P
    from backend import task_requirements as R

    path = pathlib.Path(__file__).parents[2] / "bench" / "needs-you-132-lookups.py"
    spec = importlib.util.spec_from_file_location("needs_you_132_lookups", path)
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    store, auth = api.app.state.store, api.app.state.auth
    fixture.seed(store)
    items, seen = fixture.traced(store, auth)
    assert len([i for i in items if i["kind"] == "ask"]) == fixture.N == 132
    assert len([i for i in items if i["kind"] == "question"]) == 33
    one = {IM.OF_TASK_SQL, IM.GET_SQL, P.CURRENT_SQL, R.REVIEWED_SQL, R.NEW_REVIEWED_SQL}
    many = {IM.OF_TASKS_SQL, P.CURRENT_MANY_SQL, R.REVIEWED_MANY_SQL, R.NEW_REVIEWED_MANY_SQL}
    shapes = [_shape(s) for s in seen]
    assert not [s for s in shapes if s in {_shape(q) for q in one}], "a per-task lookup was not read together"
    together = {_shape(q) for q in many}
    assert sum(1 for s in shapes if s in together or s.startswith("SELECT m.* FROM json_each(?) p CROSS JOIN")) <= 6
    assert sum(fixture.families(seen).values()) <= 6
    # The same list as a build that reads each task's reviews on its own, sharing nothing.
    with monkeypatch.context() as alone:
        alone.setattr(needs_share, "mark", lambda c: None)
        alone.setattr(R, "prime_reviews", lambda conn, task_ids: None)
        assert fixture.traced(store, auth)[0] == items


def _shape(statement):
    """A statement with its values taken out: the trace gives the SQL with each value bound in place of its `?`."""
    import re

    return re.sub(r"'(?:[^']|'')*'|\b\d+\b|\?", "?", " ".join(statement.split()))
