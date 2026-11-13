"""Wake presets (backend/wake_presets.py): what a preset holds at enqueue time, what it never holds, and what it never
changes. Times are set on the jobs directly and the planner is run at a chosen moment, so nothing here sleeps."""

import json

import pytest

from backend import github as G
from backend import never_stuck
from backend import wake_presets as W
from backend.store import H
from backend.tests.helpers.api import claim, get, headers, post, ready, setup_attempt  # noqa: F401  (api is a fixture)
from backend.tests.helpers.botops_parity import act
from backend.tests.helpers.member_bots import botops, turn  # noqa: F401  (botops is a fixture)
from backend.tests.helpers.task_lanes import new_task, task_bot
from backend import bot_settings as BS

PR = "https://github.com/ticoteam/tico/pull/412"


def notice(api, task, body, to="ops", **refs):
    with api.app.state.store.transaction() as c:
        return H._wake(c, H.task(c, task["id"]), "bot:" + to, body, refs or None)


def jobs(api, bot="ops", since=""):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT j.id,j.state,j.held,j.wake_class,j.hold_until,m.body FROM jobs j "
                "JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND m.created_at>? "
                "ORDER BY m.rowid",
                (bot, since),
            )
        ]


def at(api, job_id, when):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE id=?", (when, job_id))


def tick(api, when):
    with api.app.state.store.transaction() as c:
        W.tick(c, when)


def settled(api):
    """Mark the bot's queued jobs (a new task's assignment) handled, so the next claim takes the job under test."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops' AND state='queued'")


def start(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["id"]}, machine["token"])


def finish(api, machine, attempt, started=False, **tokens):
    if not started:
        start(api, machine, attempt)
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done.", **tokens},
        machine["token"],
    )


def employee(api, bot):
    """The row Settings and the branch editor read (ui/app/settings.ts loads /employees)."""
    return {e["name"]: e for e in api.get("/api/employees", headers=headers()).json()}[bot]


def test_immediate_wakes_are_never_held_and_a_duplicate_wakes_once(api):
    task = new_task(api, "assigned")  # an assignment
    post(api, "chat/ops", {"text": "Can you look at this?"})  # a person's question
    post(api, "chat/ops", {"text": "Can you look at this?"})  # the same, sent twice
    # An awaited result, a blocker and a reopened task wake.
    for body, refs in (
        ("All subtasks done: assigned\nThe migration is finished.", {"subtask": task["id"]}),
        ("Your task for the deploy key is waiting on you", {}),
        ("Open: assigned", {}),
    ):
        notice(api, task, body, **refs)
        notice(api, task, body, **refs)  # a duplicated event
    rows = jobs(api)
    assert all(not j["held"] for j in rows)
    live = [j["body"] for j in rows if j["state"] == "queued"]
    assert len(live) == len(set(live)) == 5
    assert sum(j["wake_class"] == "duplicate" for j in rows) == 4


def test_a_repeated_send_while_its_turn_runs_shows_that_turn_not_a_dismissal(api):
    # The duplicate trigger dismisses a send repeated while its turn runs; the chat keeps showing the turn still going.
    # A send dismissed for any other reason still reads "Delivery dismissed".
    machine, first, attempt = setup_attempt(api, "ops")
    start(api, machine, attempt)
    text = "Please summarize the current work."
    again = post(api, "chat/ops", {"text": text})  # the same words, sent again while it runs
    assert again["conversation_id"] == first["conversation_id"]
    [run, duplicate] = [j for j in jobs(api) if j["body"] == text]
    assert (run["state"], run["wake_class"]) == ("running", None)
    assert (duplicate["state"], duplicate["wake_class"]) == ("canceled", "duplicate")
    snapshot = f"conversations/{first['conversation_id']}/snapshot?limit=1"
    execution = get(api, snapshot)["execution"]
    assert (execution["state"], execution["label"]) == ("running", "Working")
    assert execution["message_id"] == first["id"]
    other = post(api, "chat/ops", {"text": "And one more thing."})
    with api.app.state.store.transaction() as c:  # dismissed for another reason
        c.execute("UPDATE jobs SET state='canceled' WHERE message_id=?", (other["id"],))
    assert get(api, snapshot)["execution"]["label"] == "Delivery dismissed"


def test_an_unchanged_webhook_queues_nothing_and_the_notice_wait_is_its_only_timer(api):
    window = W.NOTICE_SECONDS
    task = post(api, "tasks", {"owner": "ops", "title": "Repair checks", "body": "x", "links": [PR]})
    failing = {
        "action": "created",
        "repository": {"full_name": "ticoteam/tico"},
        "pull_request": {"html_url": PR},
        "comment": {"id": 1, "user": {"login": "sam"}},
    }
    with api.app.state.store.transaction() as c:
        G.pr_signal(c, "pull_request_review_comment", failing)
        G.pr_signal(c, "pull_request_review_comment", failing)
        assert G.flush_wakes(c) == [task["id"]]  # the next flush, not three minutes on
        G.pr_signal(c, "pull_request_review_comment", failing)  # the same comment: nothing new
        assert G.flush_wakes(c) == []
    [ci] = [j for j in jobs(api) if j["wake_class"] == "review" and j["state"] == "queued"]
    assert ci["held"] == 1  # a person's review comment nobody waits on is batched
    tick(api, H.now())
    [ci] = [j for j in jobs(api) if j["id"] == ci["id"]]
    assert H.parse_ts(ci["hold_until"]) <= H.parse_ts(H.shift(H.now(), seconds=window))
    with api.app.state.store.transaction() as c:
        G.queue_wake(c, H.task(c, task["id"]), "The release you armed a wait for is out.")
        assert G.flush_wakes(c) == [task["id"]]  # an awaited result goes at once too
    # It takes the held review wake with it (one turn per task) and is not held for it.
    [armed] = [j for j in jobs(api) if "The release you armed" in j["body"]]
    assert (armed["state"], armed["held"]) == ("queued", 0) and "review comment" in armed["body"]
    assert [j["state"] for j in jobs(api) if j["id"] == ci["id"]] == ["canceled"]


def test_a_subordinates_edits_do_not_wake_its_manager_and_its_end_wakes_it_unheld(api):
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "bot:assistant", "Plan the launch", "Plan it.", "bot:assistant")
        task = H.task_create(
            c, "bot:assistant", "Draft the plan", "Please draft it.", "bot:ops", parent_id=parent["id"]
        )
        before = c.execute("SELECT count(*) FROM jobs WHERE bot_id='assistant'").fetchone()[0]
        H.task_update(c, "bot:ops", task["id"], note="Started on the outline")
        H.task_update(c, "bot:ops", task["id"], note="Half way")
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='assistant'").fetchone()[0] == before
        H.task_update(
            c, "bot:ops", task["id"], status="done", note="Drafted", seen_version=H.task(c, task["id"])["version"]
        )
        # Pipelines v3: no "done" notice goes to the parent (D45). The parent's turn comes back to its owner, and the
        # never-stuck check wakes it then, at once: nothing holds that wake.
        never_stuck.check(c)
        woken = c.execute(
            "SELECT j.held FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.bot_id='assistant' AND j.state='queued' AND json_extract(m.refs,'$.task')=?",
            (parent["id"],),
        ).fetchall()
        assert woken and all(row["held"] == 0 for row in woken)


def test_one_tasks_notices_inside_the_five_minute_wait_give_one_wake(api):
    task = new_task(api, "fix")
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    for n, body in enumerate(("1 review comments on tico#412", "2 review comments on tico#412")):
        notice(api, task, body, wake_batch="review")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=20 * n))
    tick(api, H.shift(t0, seconds=W.NOTICE_SECONDS - 1))
    assert all(j["held"] for j in jobs(api, since=since))
    tick(api, H.shift(t0, seconds=W.NOTICE_SECONDS + 1))
    assert [(j["state"], j["held"]) for j in jobs(api, since=since)] == [("completed", 0), ("queued", 0)]


def test_a_usage_limit_with_a_known_reset_waits_for_it(api, monkeypatch):
    from backend.execution import cooling_off
    from backend.scheduler import Scheduler

    machine, _, attempt = setup_attempt(api)
    reset = H.shift(H.now(), minutes=40)[:19] + "+00:00"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "reset"}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "failed", "last_seq": 0, "limited": True, "limit_resets_at": reset, "text": ""},
        machine["token"],
    )
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    for minutes in (-30, -10, -1):  # no retry before the reset
        monkeypatch.setattr(H, "now", lambda m=minutes: H.shift(reset, minutes=m))
        ready(api, machine, ["ops"])
        scheduler.tick()
        assert claim(api, machine) is None
    monkeypatch.setattr(H, "now", lambda: H.shift(reset, seconds=2))
    scheduler.tick()
    with api.app.state.store.read() as c:
        assert not cooling_off(c, "ops")
        assert c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (attempt["job_id"],)).fetchone()[0] == 1
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "queued"


def test_a_duplicate_is_one_event_through_its_turn_and_a_new_event_is_not(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "awaited")
    settled(api)
    body = "Unblocked: awaited\nThe migration is finished."
    states = lambda: [(j["state"], j["wake_class"]) for j in jobs(api) if j["body"] == body]
    notice(api, task, body, subtask="s1")
    notice(api, task, body, subtask="s1")  # before the claim
    attempt = claim(api, machine)
    notice(api, task, body, subtask="s1")  # while its turn runs
    with api.app.state.store.transaction() as c:  # its turn ran for ten minutes
        c.execute(
            "UPDATE messages SET created_at=? WHERE id=(SELECT message_id FROM jobs WHERE attempt_id=?)",
            (H.shift(H.now(), minutes=-10), attempt["id"]),
        )
        c.execute("UPDATE attempts SET created_at=? WHERE id=?", (H.shift(H.now(), minutes=-10), attempt["id"]))
    finish(api, machine, attempt)
    notice(api, task, body, subtask="s1")  # just after it ended
    assert states() == [("completed", None)] + [("canceled", "duplicate")] * 3
    notice(api, task, body, subtask="s2")  # the same words, another dependency
    with api.app.state.store.transaction() as c:  # the same event, long after the turn ended
        c.execute(
            "UPDATE attempts SET finished_at=? WHERE id=?",
            (H.shift(H.now(), seconds=-W.DUPLICATE_SECONDS - 60), attempt["id"]),
        )
    notice(api, task, body, subtask="s1")
    assert states()[-2:] == [("queued", None), ("queued", None)]
    # A person saying the same thing again after the bot answered is a new request.
    said = post(api, "chat/ops", {"text": "Yes"})
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:ops", "human:ana", "Ship the other one as well?", conversation_id=said["conversation_id"])
    post(api, "chat/ops", {"text": "Yes"})
    assert [j["state"] for j in jobs(api) if j["body"] == "Yes"] == ["queued", "queued"]


@pytest.mark.parametrize("rooms", [1, 3])
def test_a_batched_event_redelivered_after_its_batch_went_out_wakes_once_until_its_turn_is_over(api, rooms):
    machine = task_bot(api, lanes=True)
    tasks = [new_task(api, "fix")] * 3 if rooms == 1 else [new_task(api, f"report {n}") for n in range(3)]
    if rooms > 1:
        lead(api)  # several rooms go out as one digest only for a Lead; any other bot gets one wake per room
    settled(api)
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    sent = [(task, f"Finished: part {n}") for n, task in enumerate(tasks)]
    for n, (task, body) in enumerate(sent):
        notice(api, task, body, wake_batch="completion")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=20 * n))
    tick(api, H.now())
    tick(api, H.shift(t0, days=2))  # one room: the newest carries; else a digest
    released = len(jobs(api, since=since))
    redeliver = lambda: [notice(api, task, body, wake_batch="completion") for task, body in sent]
    redeliver()  # while the carrier is queued
    attempt = claim(api, machine)
    start(api, machine, attempt)
    assert "running" in [j["state"] for j in jobs(api, since=since)]
    redeliver()  # while it runs
    assert [(j["state"], j["wake_class"]) for j in jobs(api, since=since)[released:]] == [("canceled", "duplicate")] * 6
    finish(api, machine, attempt, started=True)
    redeliver()  # just after it ended
    assert [(j["state"], j["wake_class"]) for j in jobs(api, since=since)[released:]] == [("canceled", "duplicate")] * 9
    with api.app.state.store.transaction() as c:  # long after the turn ended: a new event
        c.execute(
            "UPDATE attempts SET finished_at=? WHERE id=?",
            (H.shift(H.now(), seconds=-W.DUPLICATE_SECONDS - 60), attempt["id"]),
        )
    redeliver()
    assert all(j["state"] == "queued" for j in jobs(api, since=since)[-3:])


def test_a_batch_bigger_than_the_room_page_goes_out_as_a_digest_that_names_it(api):
    from backend.execution import HISTORY_PAGE

    task = new_task(api, "noisy")
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    count = HISTORY_PAGE // 2 + 5
    for n in range(count):
        notice(api, task, f"{n} review comments on tico#412", wake_batch="review")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=n))
    tick(api, H.shift(t0, minutes=5))
    rows = jobs(api, since=since)
    assert [j["state"] for j in rows] == ["canceled"] * count + ["queued"]
    digest = rows[-1]["body"]
    assert f"{count} updates" in digest and f"and {count - 1} more" in digest and task["conversation_id"] in digest
    with api.app.state.store.read() as c:  # nothing is marked read on the bot's behalf
        assert (
            c.execute(
                "SELECT count(*) FROM messages m JOIN jobs j ON j.message_id=m.id WHERE j.wake_class='review' "
                "AND m.delivered_at IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


def lead(api, bot="ops"):
    """A Lead: its notices wait for its daily digest hour, one digest for the bot (backend/profiles.py)."""
    with api.app.state.store.transaction() as c:
        BS.update(c, bot, {"profile": "lead"})


def test_a_digest_over_many_rooms_names_the_newest_and_counts_the_rest(api):
    from backend import wake_presets as WP

    lead(api)
    tasks = [new_task(api, f"room {n}") for n in range(WP.DIGEST_ROOMS + 5)]
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    for n, task in enumerate(tasks):
        notice(api, task, f"Finished: part {n}", wake_batch="completion")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=n))
    tick(api, H.now())
    tick(api, H.shift(t0, days=2))
    digest = jobs(api, since=since)[-1]["body"]
    assert "and updates in 5 more rooms" in digest
    assert tasks[-1]["conversation_id"] in digest and tasks[0]["conversation_id"] not in digest  # newest first


def test_a_digest_turn_is_measured_from_each_event_and_split_over_its_tickets(api):
    """`measures` reads the preset a past job recorded: history only, since new jobs record none."""
    machine = task_bot(api, lanes=True)
    lead(api)
    tasks = [new_task(api, f"report {n}") for n in range(3)]
    settled(api)
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    for n, task in enumerate(tasks):
        notice(api, task, f"Finished: part {n}", wake_batch="completion")
        job = jobs(api, since=since)[-1]
        at(api, job["id"], H.shift(t0, minutes=10 * n))
        with api.app.state.store.transaction() as c:
            c.execute(
                "UPDATE messages SET created_at=? WHERE id=(SELECT message_id FROM jobs WHERE id=?)",
                (H.shift(t0, minutes=10 * n), job["id"]),
            )
            c.execute("UPDATE jobs SET wake_preset='token_saver' WHERE id=?", (job["id"],))  # as a past job did
    tick(api, H.now())
    tick(api, H.shift(t0, days=2))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET wake_preset='token_saver' WHERE wake_class='digest' AND bot_id='ops'")
    attempt = claim(api, machine)
    finish(api, machine, attempt, usage={"input_tokens": 900, "cached_tokens": 0, "output_tokens": 0, "model": "fake"})
    with api.app.state.store.transaction() as c:
        for task in tasks:
            H.task_update(
                c, "bot:ops", task["id"], status="done", note="Done", seen_version=H.task(c, task["id"])["version"]
            )
    [token_saver] = [
        p
        for p in get(api, "wake-presets/measures?since=" + H.shift(t0, minutes=-1))["presets"]
        if p["preset"] == "token_saver"
    ]
    assert token_saver["turns"] == 1 and token_saver["tokens"] == 900 and token_saver["accepted_tickets"] == 3
    assert token_saver["tokens_per_ticket"] == 300 and token_saver["turns_per_ticket"] == 0.33
    assert token_saver["pickup_seconds"]["held_p50"] >= 50 * 60  # from each event, not from the digest


def stale_comments(api, task, n):
    """Tico's reminders left as comments on the task: news no rule wakes for, so a note that starts no job.
    A stale check that flags the owner's task wakes it at once instead (Dev 543, in test_urgent_news.py), so
    these reminders carry no stale-check flag."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            H.task_comment(c, H.KEEPER, task["id"], f"Still waiting on review {i}.")


def finished_requests(api, task, n):
    """Nonblocking news the notice wait batches: requests the bot asked for, finished (refs.wake_batch)."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            H._wake(c, H.task(c, task["id"]), "bot:ops", f"Finished: request {i}", {"wake_batch": "completion"})


def test_comments_start_no_turn_and_two_finished_requests_inside_the_window_give_one(api):
    task = new_task(api, "tidy")
    settled(api)
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    stale_comments(api, task, 2)
    assert jobs(api, since=since) == []  # notes for the next run (rules.default_note)
    finished_requests(api, task, 2)
    for n, job in enumerate(jobs(api, since=since)):
        at(api, job["id"], H.shift(t0, seconds=20 * n))
    tick(api, H.shift(t0, seconds=W.NOTICE_SECONDS - 1))
    assert [(j["held"], j["wake_class"]) for j in jobs(api, since=since)] == [(1, "completion")] * 2
    tick(api, H.shift(t0, seconds=W.NOTICE_SECONDS + 1))
    assert [(j["state"], j["held"]) for j in jobs(api, since=since)] == [("completed", 0), ("queued", 0)]


def test_a_persons_message_is_not_delayed_by_a_held_batch(api):
    task = new_task(api, "tidy")
    settled(api)
    since = H.now()
    finished_requests(api, task, 1)
    tick(api, H.now())
    [held] = jobs(api, since=since)
    assert held["held"] == 1 and held["hold_until"] > H.now()
    post(api, f"tasks/{task['id']}/comments", {"text": "Please also tidy the README."})  # the person who filed it
    post(api, "chat/ops", {"text": "And send me the link when it is up."})
    tick(api, H.now())
    assert [(j["state"], j["held"], j["hold_until"]) for j in jobs(api, since=since)[1:]] == [("queued", 0, None)] * 2


def test_a_persons_done_through_botops_wakes_no_bot_about_the_done_task(api, botops):
    """BotOps marking done, for the person, a task a bot asked them for: the person's act, as the same act from the app
    is. Pipelines v3 wakes no bot about a Done task (docs/pipelines-v3.md, check rule 7), so nothing is queued or held."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Approve the vendor list", "Please check it.", "human:ana")
    attempt = turn(api, botops, person="ana-test", text="Mark the vendor list done")
    since = H.now()
    version = get(api, "tasks/" + task["id"])["task"]["version"]
    done = act(api, attempt, "POST", f"tasks/{task['id']}/done", {"version": version})
    assert done.status_code == 200, done.text
    assert jobs(api, since=since) == []
    assert get(api, "tasks/" + task["id"])["task"]["status"] == "done"


# A held notice is queued but starts no run: every immediate wake that checks for a run already waiting still goes out.


def held_notice(api, task):
    finished_requests(api, task, 1)
    [held] = [j for j in jobs(api) if j["wake_class"] == "completion"]
    assert held["held"] == 1 and held["state"] == "queued"
    return held


def test_run_now_wakes_the_bot_while_a_notice_on_the_task_is_held(api):
    task = new_task(api, "tidy")
    settled(api)
    held_notice(api, task)
    assert post(api, f"tasks/{task['id']}/run-now", {})["queued"] is True
    [run] = [j for j in jobs(api) if j["body"].startswith("Run now:")]
    assert (run["state"], run["held"]) == ("queued", 0)
