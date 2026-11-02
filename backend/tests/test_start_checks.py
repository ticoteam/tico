"""A bot's start checks are read once for all its open tasks: counts() asks `waiting` only of the tasks a set
query says might be waiting, and reads each step name once. The numbers match the one-task-at-a-time checks."""
import contextlib

from backend import auto_tasks, hubdb as H
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_status_reads import setup


def employees(api, monkeypatch, token="ana-test"):
    """GET /api/employees as `token`, and the statements it took."""
    store, seen = api.app.state.store, []
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get("/api/employees", headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def open_tasks(store, slug="ops"):
    with store.read_transaction() as c:
        return H._rows(c.execute("SELECT * FROM tasks WHERE owner_id=? AND status IN ('open','doing') ORDER BY rowid",
                                 ("bot:" + slug,)))


def one_by_one(c, rows):
    """The counts as each task's own checks give them."""
    going = queued = 0
    for row in rows:
        if auto_tasks.waiting(c, row):
            continue
        if row["status"] == "doing":
            going += 1
        elif auto_tasks.startable(c, row):
            queued += 1
    return {"going": going, "waiting": queued}


def counted(store, rows):
    """counts() over `rows`, and the statements it took (the second run, once the connection has seen the tables)."""
    counted_once(store, rows)
    return counted_once(store, rows)


def counted_once(store, rows):
    with store.read_transaction() as c:
        seen = []
        c.set_trace_callback(seen.append)
        out = auto_tasks.counts(c, "ops", None, rows)
        c.set_trace_callback(None)
    return out, len(seen)


def test_batched_counts_match_each_tasks_own_checks(api, monkeypatch):
    setup(api, monkeypatch, tasks=2)
    for n in range(2, 16):
        post(api, "tasks", {"owner": "ops", "title": f"Review change {n}", "body": "Please review it."})
    store = api.app.state.store
    by_title = {r["title"]: r["id"] for r in open_tasks(store)}
    flagged_titles = ("Review change 2", "Review change 3", "Review change 4", "Review change 5",
                      "Review change 7", "Review change 8", "Review change 9", "Review change 10")
    with store.transaction() as c:
        t = {n: by_title[f"Review change {n}"] for n in range(16)}
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), days=2), t[2]))
        c.execute("UPDATE tasks SET next_run=1 WHERE id=?", (t[3],))
        c.execute("UPDATE tasks SET waiting_on='human:ben' WHERE id=?", (t[4],))
        c.execute("UPDATE tasks SET blocked_kind='manual', blocked_reason='Held for legal' WHERE id=?", (t[5],))
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (t[6],))
        c.execute("INSERT INTO approvals(id,kind,task_id,decision,created_at) VALUES('ap-1','deploy',?,NULL,?)",
                  (t[7], H.now()))
        blocker = H.task_create(c, "human:ana", "Blocker", "Blocks it.", "human:ben")
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)",
                  (blocker["id"], t[8], H.now()))
        H.task_ask(c, "bot:ops", t[9], "Which one?", park=False)
        H.task_create(c, "bot:ops", "Child", "Under it.", "human:ben", parent_id=t[10])
    rows = open_tasks(store)
    with store.read_transaction() as c:
        expected = one_by_one(c, rows)
        flagged = {r["title"] for r in rows if r["id"] in auto_tasks.maybe_waiting(c, rows)}
    got, _ = counted(store, rows)
    assert got == expected
    assert set(flagged_titles) <= flagged
    # A task with no wait, and no flag that could hold one, stays out of the per-task checks.
    assert "Review change 0" not in flagged and "Review change 15" not in flagged


def test_counts_read_a_fixed_number_of_statements_however_many_open_tasks(api, monkeypatch):
    setup(api, monkeypatch, tasks=4)
    store = api.app.state.store
    small = counted(store, open_tasks(store))[1]
    for n in range(40):
        post(api, "tasks", {"owner": "ops", "title": f"More {n}", "body": "Please review it."})
    rows = open_tasks(store)
    assert len(rows) == 44
    got, big = counted(store, rows)
    assert got["waiting"] == 44 and big == small
    # Control: one task at a time, each open task costs statements of its own.
    with store.read_transaction() as c:
        seen = []
        c.set_trace_callback(seen.append)
        one_by_one(c, rows)
        c.set_trace_callback(None)
    assert len(seen) > 10 * big


def test_employees_page_reads_no_more_statements_as_its_bots_open_tasks_grow(api, monkeypatch):
    setup(api, monkeypatch, tasks=4)
    employees(api, monkeypatch)                  # the first read warms the table checks each connection keeps
    _, small = employees(api, monkeypatch)
    for n in range(40):
        post(api, "tasks", {"owner": "ops", "title": f"More {n}", "body": "Please review it."})
    employees(api, monkeypatch)
    body, big = employees(api, monkeypatch)
    assert any(row.get("name") == "ops" for row in body if isinstance(row, dict))
    assert big == small
