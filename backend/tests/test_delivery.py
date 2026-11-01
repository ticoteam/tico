"""Steer, queue, defer (backend/delivery.py): a deferred item's deadline starts one run per session, once, and only
for an active bot; a defer rides along only on a turn in its own session; new tasks, chat messages and notes take the
same words; the older names still work."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import delivery
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_mcp import call
from backend.tests.test_tasks_board import bot_token
from clients import hubcli, hubtools


def setup(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    return machine


def rows(api, **where):
    sql = " AND ".join(f"{k}=?" for k in where) or "1"
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM deferrals WHERE {sql} ORDER BY created_at",
                                           tuple(where.values()))]


def job_of(api, message_id):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT id,state FROM jobs WHERE message_id=?", (message_id,)).fetchone()
    return tuple(row) if row else None


def tick(api, minutes):
    """One scheduler pass `minutes` from now, by a new Scheduler, as after a restart."""
    Scheduler(api.app.state.store, api.app.state.execution).tick(datetime.now(timezone.utc) + timedelta(minutes=minutes))


def fired(api):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT target FROM events WHERE action=?", (delivery.EVENT,))]


def test_a_deadline_starts_one_run_for_its_session_once(api):
    machine = setup(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Plan the launch", "body": "For the team.",
                               "delivery": "defer", "defer_for": "never"})
    path = f"tasks/{task['id']}/comments"
    first = post(api, path, {"text": "Billing first.", "delivery": "defer", "defer_for": "30m"})["comment"]
    second = post(api, path, {"text": "Then the docs.", "delivery": "defer", "defer_for": "2h"})["comment"]
    assert job_of(api, first["id"]) is None and job_of(api, second["id"]) is None

    tick(api, 20)
    assert fired(api) == []
    # The earliest deadline came: one run for the room, on its newest waiting comment, carrying both.
    tick(api, 31)
    assert job_of(api, second["id"])[1] == "queued" and job_of(api, first["id"]) is None
    assert {r["fired_job"] for r in rows(api, kind="comment")} == {second["id"]}
    # A restarted scheduler, and the later deadline, start nothing more.
    tick(api, 31)
    tick(api, 130)
    assert fired(api) == [second["id"]]
    # The run is an ordinary turn on the task: it carries both comments and settles the task's own wait.
    attempt = post(api, "jobs/claim", {"next_run": True}, token=machine["token"])["attempt"]
    assert attempt["message"]["id"] == second["id"]
    assert attempt["deferred"].split("\n")[0] == "Deferred since your last turn: 2 comments"
    assert "Billing first." in attempt["deferred"] and "Then the docs." not in attempt["deferred"]
    assert {r["how"] for r in rows(api, bot="ops")} == {"carried"}


def test_deferred_tasks_batch_into_one_run_that_carries_them_all(api):
    machine = setup(api)
    tasks = [post(api, "tasks", {"owner_id": "ops", "title": title, "body": "Check on your next run.",
                                 "delivery": "defer", "defer_for": "1h"}) for title in ("Refunds", "Signups")]
    assert all(t["next_run"] and t["next_run_waiting"] for t in tasks)
    assert [r["kind"] for r in rows(api, bot="ops")] == ["task", "task"]

    tick(api, 61)
    tick(api, 62)
    assert len(fired(api)) == 1
    attempt = post(api, "jobs/claim", {"next_run": True}, token=machine["token"])["attempt"]
    assert sorted(t["id"] for t in attempt["next_run"]) == sorted(t["id"] for t in tasks)
    assert attempt["deferred"] == "Deferred since your last turn: 2 tasks"
    assert {r["how"] for r in rows(api, bot="ops")} == {"carried"}


def test_the_stalled_task_check_leaves_a_deferred_task_to_its_deadline(api):
    setup(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Watch refunds", "body": "x", "delivery": "defer"})
    with api.app.state.store.read() as c:
        assert task["id"] not in {t["id"] for t in H.stalled_tasks(c, at=H.shift(H.now(), hours=2))}
        c.execute("UPDATE deferrals SET settled_at=?, how='carried' WHERE item=?", (H.now(), task["id"]))
        assert task["id"] in {t["id"] for t in H.stalled_tasks(c, at=H.shift(H.now(), hours=2))}


def test_a_paused_bot_is_not_woken_until_it_is_active_again(api):
    setup(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Plan the launch", "body": "For the team."})
    held = post(api, f"tasks/{task['id']}/comments", {"text": "Billing first.", "delivery": "defer",
                                                       "defer_for": "30m"})["comment"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
        assert delivery.next_due(c) is None, "a paused bot's deadline keeps no server awake"
    tick(api, 31)
    assert job_of(api, held["id"]) is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='ops'")
        assert delivery.next_due(c) == rows(api, item=held["id"])[0]["due_at"]
    tick(api, 32)
    assert job_of(api, held["id"])[1] == "queued"


def test_a_deferred_item_already_gone_starts_nothing(api):
    setup(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Plan the launch", "body": "For the team."})
    held = post(api, f"tasks/{task['id']}/comments", {"text": "Billing first.", "delivery": "defer",
                                                       "defer_for": "30m"})["comment"]
    post(api, f"tasks/{task['id']}/comments/{held['id']}/delete", {})
    tick(api, 31)
    assert fired(api) == [] and rows(api, item=held["id"])[0]["how"] == "moot"


def test_a_defer_rides_along_only_on_a_turn_in_its_own_session(api):
    machine = setup(api)
    working = post(api, "tasks", {"owner_id": "ops", "title": "Plan the launch", "body": "For the team."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "plan"}, machine["token"])
    other = post(api, "tasks", {"owner_id": "ops", "title": "Draft the FAQ", "body": "For the site."})

    # The bot is at work on another task: this waits for a turn on its own.
    elsewhere = post(api, f"tasks/{other['id']}/comments", {"text": "When you can.", "delivery": "defer"})
    assert elsewhere["sent_as"]["delivery"] == "defer" and elsewhere["woke"] is False
    assert job_of(api, elsewhere["comment"]["id"]) is None
    # On the task it is working, it goes right after this turn.
    here = post(api, f"tasks/{working['id']}/comments", {"text": "And the API page.", "delivery": "defer"})
    assert here["sent_as"] == {"delivery": "queue", "until": None} and here["woke"] is True
    assert rows(api, item=here["comment"]["id"]) == []


def test_on_a_task_a_bot_filed_a_comment_defers_a_day_and_an_at_mention_queues(api):
    token = bot_token(api, "ops")
    task = post(api, "tasks", {"owner_id": "ben", "title": "Fix the sign-in check", "body": "x"}, token=token)
    before = H.now()
    plain = post(api, f"tasks/{task['id']}/comments", {"text": "On it today."}, token="ben-test")
    assert plain["woke"] is False and plain["sent_as"]["delivery"] == "defer"
    assert H.shift(before, hours=24) <= plain["sent_as"]["until"] <= H.shift(H.now(), hours=24)
    named = post(api, f"tasks/{task['id']}/comments", {"text": "@ops which app is it?"}, token="ben-test")
    assert named["woke"] is True and named["sent_as"]["delivery"] == "queue"
    # Named, it still goes by the sender's choice.
    chosen = post(api, f"tasks/{task['id']}/comments", {"text": "@ops later is fine.", "delivery": "defer",
                                                         "defer_for": "never"}, token="ben-test")
    assert chosen["woke"] is False and chosen["sent_as"] == {"delivery": "defer", "until": None}


def test_a_new_task_takes_queue_or_defer_but_not_steer(api):
    setup(api)
    r = api.post("/api/v2/tasks", json={"owner_id": "ops", "title": "Plan", "body": "x", "delivery": "steer"},
                 headers=headers())
    assert r.status_code == 422, r.text
    r = api.post("/api/v2/tasks", json={"owner_id": "ops", "title": "Plan", "body": "x", "delivery": "queue",
                                        "next_run": True}, headers=headers())
    assert r.status_code == 422, r.text
    queued = post(api, "tasks", {"owner_id": "ops", "title": "Plan", "body": "x", "delivery": "queue"})
    assert queued["next_run"] is False
    # The older flag is a deferred task with the default day.
    older = post(api, "tasks", {"owner_id": "ops", "title": "Watch refunds", "body": "x", "next_run": True})
    assert older["next_run"] is True and rows(api, item=older["id"])[0]["due_at"] > H.shift(H.now(), hours=23)


def test_a_chat_message_steers_by_default_queues_when_asked_and_defers_with_no_deadline(api):
    machine = setup(api)
    first = post(api, "chat/ops", {"text": "Morning numbers?"})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "chat"}, machine["token"])
    queued = post(api, "chat/ops", {"text": "And the refunds.", "delivery": "queue"})
    steered = post(api, "chat/ops", {"text": "Skip the charts."})
    assert queued["sent_as"]["delivery"] == "queue" and steered["sent_as"]["delivery"] == "steer"
    taken = [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]
    assert steered["id"] in taken and queued["id"] not in taken
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Done.", "last_seq": 0},
         machine["token"])
    assert first["conversation_id"] == queued["conversation_id"]

    held = post(api, "chat/ops", {"text": "Some day: the yearly view.", "delivery": "defer"})
    assert job_of(api, held["id"]) is None
    [row] = rows(api, item=held["id"])
    assert row["kind"] == "message" and row["due_at"] is None and row["conversation_id"] == held["conversation_id"]


def test_notes_take_a_deadline_and_the_old_names_still_work(api):
    setup(api)
    note = post(api, "notes", {"to": "ops", "text": "Check the refunds.", "defer_for": "4h"})["note"]
    assert rows(api, item=note["id"])[0]["kind"] == "note"
    plain = post(api, "notes", {"to": "ops", "text": "Check the signups."})["note"]
    assert rows(api, item=plain["id"])[0]["due_at"] is None

    parse = hubcli.parser().parse_args
    for flag in ("--next-run", "--quiet"):
        assert parse(["task", "create", "--owner", "ops", "--title", "Watch refunds", flag]).next_run is True
    assert hubcli.delivery_of(parse(["task", "create", "--owner", "ops", "--title", "Watch refunds", "--defer", "4h"])) == {
        "delivery": "defer", "defer_for": "4h"}
    assert parse(["task", "defer", "T1", "--next-run"]).fn == parse(["task", "later", "T1", "--next-run"]).fn
    assert hubtools.CALL_ALIASES["hub_task_defer"] == "hub_task_later" and "hub_task_defer" not in hubtools.BY_NAME
    task = post(api, "tasks", {"owner_id": "ops", "title": "Watch refunds", "body": "x", "next_run": True})
    for route in ("later", "defer"):
        post(api, f"tasks/{task['id']}/{route}", {"next_run": True})
    is_error, payload = call(api, "hub_task_defer", {"id": task["id"], "next_run": True})
    assert is_error is False, payload


@pytest.mark.parametrize("body", [
    {"text": "x", "delivery": "steer", "defer_for": "4h"},
    {"text": "x", "delivery": "defer", "defer_for": "0m"},
    {"text": "x", "delivery": "defer", "defer_for": "731d"},
    {"text": "x", "delivery": "later"},
])
def test_a_chat_delivery_that_cannot_apply_is_refused(api, body):
    setup(api)
    r = api.post("/api/v2/chat/ops", json=body, headers=headers())
    assert r.status_code == 422, r.text


def test_someone_off_the_task_cannot_wake_its_bot_with_a_deadline(api):
    setup(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Plan the launch", "body": "For the team."})
    path = f"tasks/{task['id']}/comments"
    # Ben is not on the task: a deadline would wake the bot as a steer would.
    r = api.post(f"/api/v2/{path}", json={"text": "Mine first.", "delivery": "defer", "defer_for": "1m"},
                 headers=headers("ben-test"))
    assert r.status_code == 403 and "delivery" in r.text, r.text
    # A defer with no deadline named takes none from him (a person on the task gets a day).
    held = post(api, path, {"text": "When you can.", "delivery": "defer"}, token="ben-test")
    assert held["sent_as"] == {"delivery": "defer", "until": None}
    never = post(api, path, {"text": "Whenever.", "delivery": "defer", "defer_for": "never"}, token="ben-test")
    assert never["sent_as"] == {"delivery": "defer", "until": None}
    tick(api, 2 * 24 * 60)
    assert fired(api) == [] and job_of(api, held["comment"]["id"]) is None
    # Ana, on the task, still sets one.
    assert post(api, path, {"text": "Billing first.", "delivery": "defer", "defer_for": "1m"})["sent_as"]["until"]


def test_deferred_comments_older_than_the_history_page_are_written_out_not_dropped(api, monkeypatch):
    machine = setup(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Plan the launch", "body": "For the team.",
                               "delivery": "defer", "defer_for": "never"})
    path = f"tasks/{task['id']}/comments"
    old = post(api, path, {"text": "Billing first.", "delivery": "defer", "defer_for": "30m"})["comment"]
    # The room fills past one history page before the bot's next turn.
    for n in range(60):
        post(api, path, {"text": f"Note {n}.", "delivery": "defer", "defer_for": "never"})
    monkeypatch.setattr(delivery, "ITEMS", 5, raising=False)
    trigger = post(api, path, {"text": "Start now.", "delivery": "steer"})["comment"]
    attempt = post(api, "jobs/claim", {"next_run": True}, token=machine["token"])["attempt"]
    assert attempt["message"]["id"] == trigger["id"]
    assert old["id"] not in {m["id"] for m in attempt["history"]}, "the page does not reach it"
    # Written out in the prompt, so its deadline is met.
    assert f"[{old['id']}]" in attempt["deferred"] and "Billing first." in attempt["deferred"]
    assert rows(api, item=old["id"])[0]["how"] == "carried"
    # What did not fit waits for the next turn, unsettled, and says so.
    assert "56 more deferred here wait for your next turn" in attempt["deferred"]
    assert len([r for r in rows(api, bot="ops", kind="comment") if r["settled"] is None]) == 56


def test_deadlines_in_one_session_share_one_run_and_another_turn_cancels_it(api):
    machine = setup(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Plan the launch", "body": "For the team.",
                               "delivery": "defer", "defer_for": "never"})
    path = f"tasks/{task['id']}/comments"
    first = post(api, path, {"text": "Billing first.", "delivery": "defer", "defer_for": "30m"})["comment"]
    tick(api, 31)
    assert job_of(api, first["id"])[1] == "queued"
    # A later item comes due while that run is still queued: it joins the run rather than starting a second.
    second = post(api, path, {"text": "Then the docs.", "delivery": "defer", "defer_for": "1m"})["comment"]
    tick(api, 32)
    assert fired(api) == [first["id"]] and job_of(api, second["id"]) is None
    assert {r["fired_job"] for r in rows(api, kind="comment")} == {first["id"]}

    # A turn there in another way carries both (the run's job is stamped at the deadline, so this one goes first):
    # the deadline run is no longer needed.
    trigger = post(api, path, {"text": "Start now.", "delivery": "steer"})["comment"]
    attempt = post(api, "jobs/claim", {"next_run": True}, token=machine["token"])["attempt"]
    assert attempt["message"]["id"] == trigger["id"]
    assert "Billing first." in attempt["deferred"] and "Then the docs." in attempt["deferred"]
    assert {r["how"] for r in rows(api, kind="comment")} == {"carried"}
    assert job_of(api, first["id"])[1] == "canceled"


# The statements the deferral hot paths run on every claim, scheduler pass and stalled-task check (backend/delivery.py).
# Settled rows are never deleted, so each must reach its rows through an index, not a scan of the whole table.
HOT_DEFERRAL_QUERIES = (
    "SELECT DISTINCT d.fired_job FROM deferrals d JOIN jobs j ON j.id=d.fired_job WHERE d.attempt=? "
    "AND j.state='queued' AND d.fired_job IS NOT ? AND NOT EXISTS(SELECT 1 FROM deferrals x "
    "WHERE x.fired_job=d.fired_job AND x.settled_at IS NULL)",
    "UPDATE deferrals SET settled_at=?,how='fired' WHERE settled_at IS NULL AND fired_job IS NOT NULL AND "
    "NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=deferrals.fired_job "
    "AND j.state IN ('queued','leased','running','input'))",
    "SELECT 1 FROM deferrals WHERE kind='task' AND item=? AND settled_at IS NULL LIMIT 1",
    "UPDATE deferrals SET settled_at=?,how='carried',attempt=? WHERE bot=? AND kind=? AND "
    "settled_at IS NULL AND item IN (?,?)",
)


def test_the_deferral_hot_paths_reach_their_rows_by_index_not_scan(api):
    with api.app.state.store.read() as c:
        for sql in HOT_DEFERRAL_QUERIES:
            params = [None] * sql.count("?")
            plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, params)]
            scans = [step for step in plan if step.startswith("SCAN") and step.split()[1] in ("deferrals", "d", "x")]
            assert scans == [], (sql, plan)
