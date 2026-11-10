"""The company pause at the turn gate: no new turn starts, queued work waits with a held line on its task (once per
pause), a running turn is left alone, and Resume starts the held work in queue order.

Synthetic fixture team only (backend/tests/test_api.py); ops is the bot, its computer a fixture runner.
"""

from backend import hubdb as H
from backend import never_stuck as NS
from backend.tests.helpers.api import claim, post  # noqa: F401
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


def test_a_held_turn_is_not_a_failed_run_and_the_never_stuck_check_wakes_no_one_while_paused(api):
    """T10: a task whose bot is held by the pause gets no wake from the never-stuck check, however long the pause lasts:
    no try is counted, no run starts and none is recorded as failed. After Resume the next pass wakes the bot."""
    machine = task_bot(api, lanes=True)
    tid = new_task(api, "Draft the notes")["id"]
    run = claim(api, machine)  # its first wake has had its run, which stopped without saying done
    post(api, f"attempts/{run['id']}/started", {"thread_id": "first"}, machine["token"])
    post(
        api,
        f"attempts/{run['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Started."},
        machine["token"],
    )
    post(api, "company/pause", {})
    store = api.app.state.store
    with store.read() as c:
        before = c.execute("SELECT count(*) FROM messages WHERE wake_key LIKE ?", (tid + ":%",)).fetchone()[0]
    for hours in (0, 1, 2, 3, 4, 30):  # the check runs every few seconds through the pause, past the expected time
        with store.transaction() as c:
            done = NS.check(c, H.shift(H.now(), hours=hours))
            assert not done.get("woken") and not done.get("to_manager"), done
    with store.read() as c:
        wakes = c.execute("SELECT count(*) FROM messages WHERE wake_key LIKE ?", (tid + ":%",)).fetchone()[0]
        tries = c.execute("SELECT tries FROM visits WHERE task_id=? AND left_at IS NULL", (tid,)).fetchone()[0]
        attempts = c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0]
        failed = c.execute("SELECT fails FROM run_backoff WHERE bot='ops'").fetchone()
        waits = H.task_view(c, tid)["waits"]
    assert wakes == before, "no wake is written while paused"
    assert tries == 0, "no try is counted while paused"
    assert attempts == 1, "a held turn starts no run"
    assert failed is None or failed[0] == 0, "a held turn is not a failed run"
    assert waits[0]["state"] == "paused", waits
    post(api, "company/resume", {})
    with store.transaction() as c:
        assert NS.check(c, H.shift(H.now(), hours=31))["woken"] == 1, "after Resume the next pass wakes the bot"
    assert claim(api, machine) is not None, "and the work starts"
