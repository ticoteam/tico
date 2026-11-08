"""GET /api/v2/tasks/{tid} reads what a task holds (its comments and their runs' steps, its history, its child tasks) and
the company's bots in batches, so the statements it takes do not grow with how many of those there are."""
import contextlib

import pytest

from backend import task_privacy
from backend.store import H
from backend.tests import test_api as T
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401  (the api fixture)
from backend.tests.test_bot_list_reads import add_bots

# Tool calls each run streams. Every step names its own call id, so a run of N steps used to cost about N queries.
STEPS = 10


def build(api, comments=0, people_comments=0, events=0, children=0, bots=0):
    """A task owned by bot `ops` with `comments` bot replies, each from its own run that the runner claimed, streamed
    STEPS tool calls and completed; `people_comments` notes from Ben that each name a child task; `events` history
    rows; `children` child tasks; and `bots` more bots in the company. Ana is the requester."""
    add_bots(api, {f"x{n:02d}": {} for n in range(bots)})
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    tid = post(api, "tasks", {"owner": "ops", "title": "Grow the task", "body": "Do it."})["id"]
    with api.app.state.store.transaction() as c:
        kids = [H.task_create(c, "human:ana", f"Child {k}", "Part of it.", "bot:ops", parent_id=tid)["id"]
                for k in range(max(children, comments, people_comments))]
    for k in range(max(comments, 1)):
        if k:
            post(api, f"tasks/{tid}/comments", {"text": f"@ops step {k}"})
        aid = claim(api, machine)["id"]
        post(api, f"attempts/{aid}/started", {"thread_id": f"run {k}"}, machine["token"])
        rows = [{"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}}]
        for j in range(STEPS):
            call = {"tool": "Read", "item_id": f"r{k}-{j}"}
            rows += [{"seq": 2 + 2 * j, "kind": "tool", "payload": {**call, "status": "started"}},
                     {"seq": 3 + 2 * j, "kind": "tool", "payload": {**call, "status": "completed"}}]
        post(api, f"attempts/{aid}/events", {"events": rows}, machine["token"])
        post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": f"Reply {k}.", "last_seq": len(rows)},
             machine["token"])
    with api.app.state.store.transaction() as c:
        for k in range(people_comments):
            H.task_comment(c, "human:ben", tid, f"Ben note {k}", wake=False, extra_refs={"task": [kids[k]]})
        for k in range(events):
            c.execute("INSERT INTO task_events(id,task_id,created_at,actor,field,old,new,note) VALUES(?,?,?,?,?,?,?,?)",
                      (f"ev{k}", tid, H.now(), "human:ana", "status", "open", "doing", ""))
    return tid


def page_statements(api, monkeypatch, tid, token):
    """The task page as `token` gets it, and how many statements it ran. Each request starts from cold roster and row
    caches."""
    store, seen = api.app.state.store, []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()

    def traced(opened):
        @contextlib.contextmanager
        def borrowed(*args, **kwargs):
            with opened(*args, **kwargs) as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)
        return borrowed
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction", "transaction"):
            patched.setattr(store, name, traced(getattr(store, name)))
        response = api.get(f"/api/v2/tasks/{tid}", headers=T.headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


SCALES = [
    ("comments and their steps", {"comments": 2}, {"comments": 12}),
    ("people's comments naming child tasks", {"people_comments": 2}, {"people_comments": 12}),
    ("history events", {"events": 2}, {"events": 30}),
    ("child tasks", {"children": 2}, {"children": 12}),
    ("bots in the company", {"bots": 5}, {"bots": 40}),
]


@pytest.mark.parametrize("what,small,big", SCALES, ids=[s[0] for s in SCALES])
def test_the_task_page_takes_the_same_statements_however_many_of_each_it_holds(tmp_path, monkeypatch, what, small, big):
    counted = {}
    for name, scale in (("small", small), ("big", big)):
        (tmp_path / name).mkdir()
        install = T.api.__wrapped__(tmp_path / name, None)
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
            f"GET /api/v2/tasks/{{tid}} as {reader} takes {many} statements with {big} {what} against {few} with "
            f"{small}: a read runs once per item again instead of once for the whole page. Batch it with "
            "task_privacy.prime_references before the per-row checks (see backend/turns.py annotate).")


def test_priming_the_task_reads_does_not_change_the_page(api, monkeypatch):
    """The batched lookups only warm what the per-row checks read: the page is the same with them switched off."""
    tid = build(api, comments=3, people_comments=2, events=4, children=3)
    batched, _ = page_statements(api, monkeypatch, tid, "cara-test")
    with monkeypatch.context() as patched:
        patched.setattr(task_privacy, "prime_references", lambda c, values: None)
        unbatched, _ = page_statements(api, monkeypatch, tid, "cara-test")
    assert batched == unbatched
    assert len(batched["comments"]) >= 5 and any(m.get("run") for m in batched["comments"])
