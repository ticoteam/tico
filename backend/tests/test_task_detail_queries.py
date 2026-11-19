"""GET /api/v2/tasks/{tid} reads what a task holds (its comments and their runs' steps, its history, its child tasks) and
the company's bots in batches, so the statements it takes do not grow with how many of those there are."""

import contextlib

import pytest

from backend import task_privacy
from backend.tests.helpers import api as T
from backend.tests.helpers.api_app import api_client
from backend.tests.helpers.api import assign, claim, post, ready, runner  # noqa: F401  (the api fixture)
from backend.tests.helpers.participants_audience import on_roster
from backend.tests.helpers.task_detail_queries import build


def page_statements(api, monkeypatch, tid, token):
    """The task page as `token` gets it, and how many statements it ran. Each request starts from cold roster and row
    caches."""
    from backend.tests.helpers.statement_scope import measuring, recorder

    store, seen = api.app.state.store, []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()

    def traced(opened):
        @contextlib.contextmanager
        def borrowed(*args, **kwargs):
            with opened(*args, **kwargs) as c:
                c.set_trace_callback(recorder(seen))
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction", "transaction"):
            patched.setattr(store, name, traced(getattr(store, name)))
        with measuring(seen):
            response = api.get(f"/api/v2/tasks/{tid}", headers=T.headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def test_the_task_page_takes_the_same_statements_however_many_of_each_it_holds(tmp_path, monkeypatch):
    """One small and one big install, every dimension growing at once: comments and their steps, people's comments
    naming child tasks, history events, child tasks and bots in the company."""
    small = {"comments": 2, "people_comments": 2, "events": 2, "children": 2, "bots": 5}
    big = {"comments": 12, "people_comments": 12, "events": 30, "children": 12, "bots": 40}
    counted = {}
    for name, scale in (("small", small), ("big", big)):
        (tmp_path / name).mkdir()
        install = api_client(tmp_path / name, None)
        api = next(install)
        try:
            tid = build(api, **scale)
            for reader in ("cara-test", "ana-test"):
                _, counted[(name, reader)] = page_statements(api, monkeypatch, tid, reader)
        finally:
            install.close()
    for reader in ("cara-test", "ana-test"):
        few, many = counted[("small", reader)], counted[("big", reader)]
        assert few > 0 and many > 0, (reader, few, many)
        assert many - few <= 4, (
            f"GET /api/v2/tasks/{{tid}} as {reader} takes {many} statements with {big} against {few} with "
            f"{small}: a read runs once per item again instead of once for the whole page. Batch it with "
            "task_privacy.prime_references before the per-row checks (see backend/turns.py annotate)."
        )


def test_priming_the_task_reads_does_not_change_the_page(api, monkeypatch):
    """The batched lookups only warm what the per-row checks read: the page is the same with them switched off."""
    tid = build(api, comments=3, people_comments=2, events=4, children=3)
    batched, _ = page_statements(api, monkeypatch, tid, "cara-test")
    with monkeypatch.context() as patched:
        patched.setattr(task_privacy, "prime_references", lambda c, values: None)
        unbatched, _ = page_statements(api, monkeypatch, tid, "cara-test")
    assert batched == unbatched
    assert len(batched["comments"]) >= 5 and any(m.get("run") for m in batched["comments"])


def test_the_run_line_alone_is_the_task_pages_own(api):
    """GET /api/v2/tasks/{tid}/execution, which the task page reads as a run's steps come in, answers the page's own
    `execution` while the run works and once it ends, to whoever may read the task, and nothing to anyone else."""
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid = post(api, "tasks", {"owner": "ops", "title": "Watch the run", "body": "Do it."})["id"]
    aid = claim(api, machine)["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t1"}, machine["token"])
    steps = [{"seq": 1, "kind": "tool", "payload": {"tool": "Read", "item_id": "r1", "status": "started"}}]
    steps.append({"seq": 2, "kind": "message", "payload": {"text": "Reading the spec.", "final": False}})
    post(api, f"attempts/{aid}/events", {"events": steps}, machine["token"])

    def both(token):
        page = api.get(f"/api/v2/tasks/{tid}", headers=T.headers(token))
        alone = api.get(f"/api/v2/tasks/{tid}/execution", headers=T.headers(token))
        return page, alone

    page, alone = both("ana-test")
    assert page.status_code == alone.status_code == 200, (page.text, alone.text)
    working = page.json()["execution"]
    assert working and working["state"] == "running" and working["parts"], working
    assert alone.json() == {"execution": working}
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": "Done.", "last_seq": 2}, machine["token"])
    page, alone = both("ana-test")
    assert alone.json() == {"execution": page.json()["execution"]}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (tid,))
    on_roster(api, "eve")
    page, alone = both("eve-test")
    assert page.status_code == alone.status_code and page.status_code in (403, 404), (page.text, alone.text)
