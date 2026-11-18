"""Opening the app reads every open task's standing several times over: Needs you, Health, the Floor and the live
stream's first read each ask what every open task waits on. On Tico Team (2026-11-18 14:13 UTC, task 13b92931) one
app open sent about 25 such requests, each ~1,000 statements, ~140 of them a task's PRs, their metadata and its pages
read one task at a time; together they held the server's CPU at 95% and each answer took 5 to 7 seconds.

These tests pin the statements of each app-open read to the same count however many open QA tasks, each with a PR and
a linked QA report, the company has. A read that goes back to one statement per task fails here, naming the route.
And the batched reads must give each task exactly the standing it gets read on its own.

Synthetic rows only: the fictional company (backend/tests/helpers/engine.py) and its role bots.
"""

import json

from backend import events
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.health_queries import counted
from backend.tests.helpers.page_requirement import _page
from backend.tests.helpers.product_engineering_presets import _answers, _team
from backend.tests.helpers.stage_inputs import _attach, _link_pr, _push
from backend.tests.helpers.statement_scope import measuring, recorder
from backend.tests.helpers.thread_events import who

ANA = X.ANA
# The app-open reads that each ask every open task's standing (Needs you's list and count, Health's plates, the Floor).
ROUTES = ("/api/v2/needs-you", "/api/v2/needs-you?count=1", "/api/v2/health", "/api/v2/company/floor")


def _company(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    X.github_installed(c)
    X.bot(c, "writer")  # the pages' writer (page_requirement._page)


def _tasks(api, first, last):
    """Product tasks first..last-1 as production holds most of them: in QA, each with an open PR and its QA report
    linked and attached. Every third PR is merged, every other page has the decision model's verdict, and every
    fourth task has a second PR, so each kind of read the standing makes is in the list."""
    store = api.app.state.store
    with store.transaction() as c:
        for k in range(first, last):
            row = H.task_create(c, ANA, f"Feature {k}", "Shoppers lose carts. Keep them.", "bot:pm", pipeline="product", lint=False)
            _link_pr(c, row["id"], 100 + k, f"{k:040x}")
            if k % 4 == 0:
                _link_pr(c, row["id"], 500 + k, f"{k + 7:040x}")
            if k % 3 == 0:
                _push(c, 100 + k, f"{k:040x}", state="merged", merged_sha=f"{k + 1:040x}")
            E.move(c, row["id"], "product-qa", ANA, "On for this test")
            fid, version = _page(c, store, f"<h1>QA {k}</h1><p>The cart is kept.</p>")
            c.execute("UPDATE bot_files SET scope=?, task_id=? WHERE id=?", ("task:" + row["id"], row["id"], fid))
            c.execute("INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)", (fid, H.now(), H.now()))
            c.execute(
                "INSERT INTO page_links VALUES(?,?,?,'QA report','bot:pm',?)", (H.new_id(), fid, "task:" + row["id"], H.now())
            )
            _attach(c, row["id"], fid, "bot:pm", version)
            if k % 2 == 0:
                c.execute(
                    "INSERT INTO page_verdicts (id, file_id, version, rubric_hash, verdict, reasons, created_at, "
                    "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (H.new_id(), fid, version, "", "met", json.dumps([]), H.now(), H.now()),
                )


def _statements(api, read):
    store, seen = api.app.state.store, []
    with store.read_transaction() as c:
        c.set_trace_callback(recorder(seen))
        with measuring(seen):
            read(c)
        c.set_trace_callback(None)
    return len(seen)


def test_each_app_open_read_takes_the_same_statements_however_many_open_tasks_it_reads(api, monkeypatch):
    with api.app.state.store.transaction() as c:
        _company(c)
    _tasks(api, 0, 4)
    for path in ROUTES:
        counted(api, monkeypatch, path)  # warm: the table checks each connection keeps
    few = {path: counted(api, monkeypatch, path)[1] for path in ROUTES}
    auth, ana = api.app.state.auth, who(api, "ana-test")
    few["stream start"] = _statements(api, lambda c: events.needs_baseline(c, auth, ana, None))
    _tasks(api, 4, 20)
    many = {path: counted(api, monkeypatch, path)[1] for path in ROUTES}
    many["stream start"] = _statements(api, lambda c: events.needs_baseline(c, auth, ana, None))
    grown = {path: (few[path], many[path]) for path in few if many[path] - few[path] > 2}
    # 16 more QA tasks, each with a PR, its metadata, a linked QA report and a verdict. Two statements of slack: a
    # cache that fills on its first read. Before 13b92931 Needs you grew by 6 a task (111 -> 183 here).
    assert not grown, (
        f"{grown}: (statements with 4 open QA tasks, with 20). A read of each task's PRs, PR metadata, pages, linked "
        "documents or page verdicts runs once a task again instead of once for the whole list: prime it in "
        "waiting_on.prime (task_pages.prime, item_metadata.prime_of_tasks, page_verdicts.prime), and read it inside "
        "store.read_transaction(), where the snapshot cache those primes fill exists."
    )


def test_a_batched_read_gives_each_task_the_standing_it_gets_read_alone(api):
    with api.app.state.store.transaction() as c:
        _company(c)
    _tasks(api, 0, 12)
    store = api.app.state.store
    with store.read() as c:
        ids = [str(r[0]) for r in c.execute("SELECT id FROM tasks WHERE pipeline_id='product' AND status='open'")]
        alone = {task_id: W.waiting_on(c, task_id) for task_id in ids}  # no snapshot: nothing primed
    with store.read_transaction() as c:
        batched = W.waiting_on_many(c, ids)
    assert len(ids) == 12 and batched == alone
