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
    """Done-when 1: needs-you twice, four live-stream starts, the company floor and two place reads, with no write in
    between: one build, and each answer equals the answer of a build of its own."""
    for k in range(3):
        _ask(api, f"Payroll run {k}")
    whole = get(api, "needs-you")
    count = get(api, "needs-you?count=1")
    floor = get(api, "company/floor")
    place = get(api, "places/dashboard")
    auth = api.app.state.auth
    baselines = []
    for _ in range(4):
        with api.app.state.store.read_transaction() as c:
            baselines.append(events.needs_baseline(c, auth, ANA, None))
    first = needs_share.builds
    needs_share.clear()

    def opened():
        answers = [get(api, "needs-you"), get(api, "needs-you?count=1")]
        for _ in range(4):
            with api.app.state.store.read_transaction() as c:
                answers.append(events.needs_baseline(c, auth, ANA, None))
        answers += [get(api, "company/floor"), get(api, "places/dashboard")]
        return answers

    before = needs_share.builds
    answers = opened()
    assert needs_share.builds - before == 1
    assert answers[0]["items"] == whole["items"] and answers[1]["count"] == count["count"] == 3
    assert answers[2:6] == baselines
    assert answers[6] == floor and answers[7] == place
    assert first >= 1
    # Today's answer, built alone, is the same list.
    assert _items(api) == _alone(api, monkeypatch)
    assert len(_items(api, count_only=True)) == len(_alone(api, monkeypatch, count_only=True))


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


def test_one_build_reads_each_tasks_prs_metadata_and_reviews_together(api):
    """Done-when 5, on a small fixture shaped like production: tasks asking the person's OK, each with a linked GitHub
    PR Tico has read. Their links, saved metadata, current pages and reviews are read for all of them together: at most
    six statements in all, none per task (live, 18 Nov: about 400 a build)."""
    from backend import item_metadata as IM
    from backend import task_pages as P
    from backend import task_requirements as R
    from backend.item_sources import github as GS

    from backend.blobs import Blobs
    from backend.tests.helpers.page_requirement import _page, _say

    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    stage = {
        "name": "QA",
        "owner": "assignee",
        "requirements": [{"kind": "page", "line": "QA page", "person": "human:ana"}],
    }
    n = 12
    with store.transaction() as c:
        X.bot(c, "writer")
        X.pipeline(c, "eng", "Engineering", (stage,), manager="bot:writer")
    for k in range(n):
        with store.transaction() as c:
            made = X.new_task(c, "human:ana", f"Ship change {k}", "eng", assignee="bot:writer")
            H.task_link(c, "bot:writer", made["id"], f"https://github.com/acme/app/pull/{100 + k}")
            GS.save_pr(c, "acme/app", 100 + k, {"title": f"Change {k}", "state": "open", "head_sha": "a" * 40}, 1)
            fid, _ = _page(c, store, f"<h1>QA {k}</h1>")
            row = H.task(c, made["id"])
            _say(c, P.attach(c, made["id"], "QA page", "page:" + fid, "bot:writer", seen_version=row["version"]), "met")
    one = {IM.OF_TASK_SQL, IM.GET_SQL, P.CURRENT_SQL, R.REVIEWED_SQL, R.NEW_REVIEWED_SQL}
    many = {IM.OF_TASKS_SQL, P.CURRENT_MANY_SQL, R.REVIEWED_MANY_SQL, R.NEW_REVIEWED_MANY_SQL}
    seen: list[str] = []
    with api.app.state.store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        items = V.needs_items(c, api.app.state.auth, ANA)
        c.set_trace_callback(None)
    assert len([i for i in items if i["kind"] == "ask"]) == n, items
    shapes = [_shape(s) for s in seen]
    assert not [s for s in shapes if s in {_shape(q) for q in one}], "a per-task lookup was not read together"
    together = {_shape(q) for q in many}
    assert sum(1 for s in shapes if s in together or s.startswith("SELECT m.* FROM json_each(?) p CROSS JOIN")) <= 6


def _shape(statement):
    """A statement with its values taken out: the trace gives the SQL with each value bound in place of its `?`."""
    import re

    return re.sub(r"'(?:[^']|'')*'|\b\d+\b|\?", "?", " ".join(statement.split()))
