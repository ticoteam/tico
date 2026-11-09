"""The company pause at the turn gate: no new turn starts, queued work waits with a held line on its task (once per
pause), a running turn is left alone, and Resume starts the held work in queue order.

Synthetic fixture team only (backend/tests/test_api.py); ops is the bot, its computer a fixture runner.
"""

from backend import hubdb as H
from backend import stranded_resume as SR
from backend.tests.helpers import stranded_resume as SRT
from backend.tests.test_api import api, claim, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.claim_refusals import forget_claims  # noqa: F401
from backend.tests.helpers.task_lanes import new_task, task_bot  # noqa: F401


def _held_lines(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM events WHERE action='company.held' AND target=?", (task_id,)).fetchone()[
            0
        ]


def _queued(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0]


def test_a_paused_company_starts_no_turn_and_the_queued_task_says_why(api):
    machine = task_bot(api, lanes=True)
    post(api, "company/pause", {})
    first = new_task(api, "first")
    assert claim(api, machine) is None
    assert _queued(api) == 1
    assert _held_lines(api, first["id"]) == 1
    with api.app.state.store.read() as c:
        status = c.execute("SELECT status FROM tasks WHERE id=?", (first["id"],)).fetchone()[0]
    assert status == "open"  # the task stays where it was: it is not moved to Doing


def test_the_held_line_is_written_once_per_task_per_pause(api):
    machine = task_bot(api, lanes=True)
    post(api, "company/pause", {})
    first = new_task(api, "first")
    post(api, "chat/ops", {"text": "Also this, while paused."})
    assert claim(api, machine) is None
    assert _held_lines(api, first["id"]) == 1
    post(api, "company/resume", {})
    post(api, "company/pause", {})
    assert _held_lines(api, first["id"]) == 2
    assert claim(api, machine) is None
    assert _held_lines(api, first["id"]) == 2


def test_resume_starts_the_held_work_in_queue_order_and_reports_the_count(api):
    machine = task_bot(api, lanes=True)
    post(api, "company/pause", {})
    first, second = new_task(api, "first"), new_task(api, "second")
    assert claim(api, machine) is None
    resumed = post(api, "company/resume", {})
    assert resumed["changed"] is True and resumed["held"] == 2
    one, two = claim(api, machine), claim(api, machine)
    assert [one["lane"], two["lane"]] == ["task:" + first["id"], "task:" + second["id"]]


def test_a_running_turn_finishes_and_nothing_new_starts_after_it(api):
    machine = task_bot(api, lanes=True)
    first = new_task(api, "first")
    running = claim(api, machine)
    assert running["lane"] == "task:" + first["id"]
    post(api, "company/pause", {})
    with api.app.state.store.read() as c:
        state = c.execute("SELECT state FROM attempts WHERE id=?", (running["id"],)).fetchone()[0]
    assert state in ("leased", "running")
    second = new_task(api, "second")
    assert claim(api, machine) is None
    assert _held_lines(api, second["id"]) == 1


def test_resume_with_nothing_held_reports_zero(api):
    post(api, "company/pause", {})
    resumed = post(api, "company/resume", {})
    assert resumed["changed"] is True and resumed["held"] == 0


def test_a_held_turn_is_not_a_failed_run_and_uses_no_automatic_resume(api):
    """T10: a stranded task left in progress gets no resume wake while the company is paused, however long the pause
    lasts, so no automatic resume is counted; the held turn makes no attempt and records no failed run; Resume starts it."""
    machine = SRT.online(api, "ops")
    tid = SRT.new_task(api, "Draft the notes")
    store = api.app.state.store
    with store.transaction() as c:
        SRT.strand(c, tid)
    post(api, "company/pause", {})
    for hours in (0, 1, 2, 3, 4):  # the sweep runs every few minutes through the pause, past the usual run times
        with store.transaction() as c:
            assert SR.run(c, H.shift(H.now(), hours=hours)) == {"resumed": 0, "stopped": 0}
    with store.read() as c:
        assert SRT.resumes(c, tid) == [], "no automatic resume is counted while paused"
        assert SRT.wakes(c, "ops", tid) == 0
        attempts = c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0]
        failed = c.execute("SELECT fails FROM run_backoff WHERE bot='ops'").fetchone()
    assert attempts == 0, "a held turn starts no run"
    assert failed is None or failed[0] == 0, "a held turn is not a failed run"
    post(api, "company/resume", {})
    with store.transaction() as c:
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
    assert claim(api, machine) is not None, "after Resume the work starts"
