"""The Blocked reason follows the holds a task has: each route that clears a wait recomputes it in the same write, a note-only
update leaves it matching the state, and the one-time repair (migration 20261107140000) corrects stale reasons without
clearing, waking, messaging or touching anything else. Synthetic fixtures only, in the test's own database."""

import importlib

import pytest

from backend import hubdb as H, task_holds, task_relations as TR
from backend.tests.helpers.api import get, post  # noqa: F401  (fixture)
from backend.tests.test_api import api  # noqa: F401  (fixture)
from backend.tests.helpers.question_tasks import _answer, _work

ASK = "Pick the launch date? I'll book the venue after."
ROUTES = ["waiting_flag_cleared", "child_done", "question_answered", "question_withdrawn", "status_change"]


def _read(api, sql, args=()):
    with api.app.state.store.read() as c:
        return c.execute(sql, args).fetchall()


def _version(api, task_id, token="ana-test"):
    return get(api, f"tasks/{task_id}", token)["task"]["version"]


def _wait(api, task_id, token, on="ana", note=ASK):
    return post(
        api,
        f"tasks/{task_id}",
        {"version": _version(api, task_id, token), "status": "waiting", "waiting_on": on, "note": note},
        token,
    )


def _stale(api, task_id, on="Chris Hawkins"):
    """A hold with nothing behind it: waiting, a dependency reason naming a person, no open question, blocker or child."""
    with api.app.state.store.transaction() as c:
        H._set_status(c, H.KEEPER, H.task(c, task_id), "waiting", note=f"{on}: approve it?")
        task_holds.change(c, task_id, "blocked", cause="dependency", reason=f"Waiting on {on}: approve it?")


def _live(api, task_id):
    """Status, Blocked reason and open holds of a task, as the Blocked list reads them."""
    with api.app.state.store.read() as c:
        row = H.task(c, task_id)
        return row["status"], row.get("blocked_reason"), [(h["kind"], h["on"]) for h in task_holds.holds(c, row)]


def _task(api, title):
    """A second task of the bot's, with no run of its own (`_work` claims the bot's one run)."""
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Choose it with the team."})


def _clear(api, route, work, card, token):
    """One clear, by a route a keeper or owner uses."""
    if route == "waiting_flag_cleared":
        post(api, f"tasks/{work['id']}", {"version": _version(api, work["id"], token), "waiting_on": ""}, token)
    elif route == "child_done":
        post(api, f"tasks/{card}", {"version": _version(api, card), "status": "done", "note": "Thursday."}, "ana-test")
    elif route == "question_answered":
        _answer(api, card, {"answers": {}, "other": "Thursday the 12th."})
    elif route == "question_withdrawn":
        _answer(api, card, {"answers": {}, "other": None, "dismiss": True})
    elif route == "status_change":
        post(
            api,
            f"tasks/{work['id']}",
            {"version": _version(api, work["id"], token), "status": "declined", "note": "Not needed now."},
            token,
        )
    else:  # pragma: no cover
        raise AssertionError(route)


@pytest.mark.parametrize("route", ROUTES)
def test_each_clear_route_recomputes_the_reason_in_the_same_write(api, route):
    """A task waiting on a person through an open question. Each route clears that wait: the reason goes with the hold in
    the same write, and the task leaves the Blocked list."""
    work, attempt = _work(api)
    token = attempt["token"]
    _wait(api, work["id"], token)
    status, reason, held = _live(api, work["id"])
    assert (status, held) == ("waiting", [("child", "human:ana")]), route
    assert reason.startswith("Waiting on Ana"), reason
    with api.app.state.store.read() as c:
        [card] = TR.child_ids(c, work["id"])

    _clear(api, route, work, card, token)

    status, reason, held = _live(api, work["id"])
    assert held == [], route
    assert status != "waiting" and reason is None, (route, status, reason)


@pytest.mark.parametrize("route", ["waiting_flag_cleared", "status_change"])
def test_a_clear_on_a_stale_hold_recomputes_it_and_drops_the_task(api, route):
    """The stale state: waiting on a person with no question, blocker or child left. Clearing it recomputes the reason in
    the same write and the task leaves the Blocked list. Before the fix, a clear that wrote nothing else kept the old
    reason."""
    work, attempt = _work(api)
    _stale(api, work["id"])
    assert _live(api, work["id"])[1].startswith("Waiting on Chris Hawkins")

    _clear(api, route, work, None, attempt["token"])

    status, reason, held = _live(api, work["id"])
    assert (status != "waiting", reason, held) == (True, None, []), (route, status, reason)


def test_a_flag_clear_on_a_stale_hold_says_what_cleared_and_names_no_one(api):
    work, attempt = _work(api)
    _stale(api, work["id"])
    _clear(api, "waiting_flag_cleared", work, None, attempt["token"])
    with api.app.state.store.read() as c:
        note = H.task(c, work["id"])["note"]
    assert "wait on a person was cleared" in note
    assert "Chris" not in note


def test_a_note_only_update_leaves_the_reason_matching_the_state(api):
    work, attempt = _work(api)
    token = attempt["token"]
    _stale(api, work["id"])
    post(api, f"tasks/{work['id']}", {"version": _version(api, work["id"], token), "note": "Still looking."}, token)
    status, reason, held = _live(api, work["id"])
    assert (status, held, reason) == ("waiting", [], task_holds.NOTHING)

    live = _task(api, "Pick a venue")
    _wait(api, live["id"], token, on="ana", note="Which venue?")
    post(api, f"tasks/{live['id']}", {"version": _version(api, live["id"], token), "note": "Two venues."}, token)
    status, reason, held = _live(api, live["id"])
    assert status == "waiting" and held == [("child", "human:ana")]
    assert reason.startswith("Waiting on Ana")


def test_the_one_time_repair_changes_only_stale_reasons(api):
    """Migration 20261107140000 over four waiting tasks: a stale dependency hold gets the reason its holds give (none); a
    live hold keeps its reason; a hold a keeper set by hand that names a person gets the reason its holds give (none) and
    stays held, with its cause, clock and owner; a hand-set hold over a step keeps its reason. Nothing else is written."""
    migration = importlib.import_module("backend.migrations.20261107140000_blocked_reason_recompute")
    stale, attempt = _work(api, title="Stale wait")
    _stale(api, stale["id"])
    live = _task(api, "Live wait")
    _wait(api, live["id"], attempt["token"], on="ana", note="Which venue?")
    kept = _task(api, "Keeper kept it")
    stepped = _task(api, "Step hold")
    with api.app.state.store.transaction() as c:
        H._set_status(c, H.KEEPER, H.task(c, kept["id"]), "waiting", note="Keep it held.")
        task_holds.change(c, kept["id"], "blocked", cause="manual", reason="Waiting on Chris Hawkins: keep it held?")
        H._set_status(c, H.KEEPER, H.task(c, stepped["id"]), "waiting", note="Parked.")
        task_holds.change(c, stepped["id"], "blocked", cause="manual", reason="In Review 1")

    ids = (stale["id"], live["id"], kept["id"], stepped["id"])

    def snapshot():
        with api.app.state.store.read() as c:
            rows = {}
            for tid in ids:
                row = H.task(c, tid)
                rows[tid] = (
                    row["status"],
                    row.get("blocked_reason"),
                    row.get("blocked_kind"),
                    row.get("blocked_since"),
                    row.get("holds_cleared_at"),
                    row["version"],
                    row["updated_at"],
                    [
                        (h["kind"], h["cause"])
                        for h in c.execute(
                            "SELECT kind, cause FROM task_holds WHERE task_id=? AND released_at IS NULL", (tid,)
                        ).fetchall()
                    ],
                )
            counts = (
                c.execute("SELECT count(*) FROM messages").fetchone()[0],
                c.execute("SELECT count(*) FROM approvals").fetchone()[0],
            )
        return rows, counts

    before, counts_before = snapshot()
    with api.app.state.store.transaction() as c:
        migration.up(c)
    after, counts_after = snapshot()

    keep = (0, 2, 3, 4, 5, 6, 7)
    assert after[stale["id"]][1] == task_holds.NOTHING
    assert [after[stale["id"]][i] for i in keep] == [before[stale["id"]][i] for i in keep]
    assert after[live["id"]] == before[live["id"]]
    # The keeper's hold stays held with its owner, status, clock and cause; only its reason is corrected.
    assert after[kept["id"]][1] == task_holds.NOTHING
    assert [after[kept["id"]][i] for i in keep] == [before[kept["id"]][i] for i in keep]
    assert after[kept["id"]][0] == "waiting"
    assert after[stepped["id"]] == before[stepped["id"]]
    assert counts_after == counts_before
