"""A computer that hands back a bot's runs on a task before starting them, each time for the same reason, does not loop
silently: BotOps gets it and the task says what it waits on (Acme, Nov 11: Reviewer 2's computer handed back every run
on its Review 2 task with "the bot's checkout's upstream could not be read", two minutes apart, and the task read
"Queued")."""

import json

from backend import execution, start_holds, task_wakes, waiting_on
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner  # noqa: F401 (fixture)
from backend.tests.helpers.execution_review import add_recovery_reviewer

UPSTREAM = (
    "the bot's checkout's upstream could not be read (fatal: ambiguous argument '@{{u}}': unknown revision or path not "
    "in the working tree.), so task {task} did not run in the bot's shared checkout"
)


def hand_back(api, machine, attempt, reason):
    """The runner's report of a run it could not set up: its reason as a diagnostic, then the interrupted completion.
    The hand-back's wait is then made to be over, so the next claim takes the job again."""
    events = []
    if reason:
        events = [{"seq": 1, "kind": "diagnostic", "payload": {"error_type": "RuntimeError", "runner_error": reason}}]
        post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "interrupted", "last_seq": len(events), "text": ""},
        machine["token"],
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempts SET finished_at=? WHERE id=?",
            (H.shift(H.now(), seconds=-execution.HAND_BACK_MAX - 1), attempt["id"]),
        )
    ready(api, machine, ["ops"])


def start(api):
    add_recovery_reviewer(api)
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    made = post(api, "tasks", {"owner": "ops", "title": "Review the negate PR", "body": "Review it."})
    task_id = made.get("task", made).get("id") or made["refs"]["task"]
    return machine, task_id, claim(api, machine)


def job_state(api, job_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]


def botops_tasks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,title,body,status FROM tasks WHERE owner_id='bot:botops'")]


def test_three_hand_backs_for_the_same_reason_go_to_botops_and_pause_the_task(api):
    machine, task_id, attempt = start(api)
    job_id = attempt["job_id"]
    reason = UPSTREAM.format(task=task_id)
    for n in range(start_holds.SAME):
        assert attempt and attempt["job_id"] == job_id
        hand_back(api, machine, attempt, reason)
        with api.app.state.store.read() as c:
            [detail] = [
                json.loads(r[0])
                for r in c.execute(
                    "SELECT detail FROM events WHERE action='attempt.returned' AND target=?", (attempt["id"],)
                )
            ]
        assert detail["cause"] == "handed_back"  # the category the server logs it with
        if n < start_holds.SAME - 1:
            assert job_state(api, job_id) == "queued"
            assert not botops_tasks(api)
            attempt = claim(api, machine)
    # The third in a row stops the retries, long before PRE_START_TRIES.
    assert start_holds.SAME < execution.PRE_START_TRIES
    assert job_state(api, job_id) == "failed"
    assert claim(api, machine) is None
    [review] = botops_tasks(api)
    assert review["title"] == "Find why ops's runs can't start" and review["status"] == "open"
    assert "upstream could not be read" in review["body"] and task_id in review["body"]
    assert "Review the negate PR" in review["body"] and H.BOT_REVIEW_ID.format(id="ops") in review["body"]
    # The run says why, and the bot's page asks no person.
    turn = next(t for t in get(api, "bots/ops/turns") if t["id"] == attempt["id"])
    assert "upstream could not be read" in turn["end_reason"]
    stop = get(api, "status?bot=ops")["status"]["last_stop"]
    assert stop["needs_person"] is False and stop["text"].endswith(start_holds.BOTOPS_HAS_IT)
    # The task waits on the bot, Paused with the cause: not "Queued", not a person's.
    with api.app.state.store.read() as c:
        assert task_wakes.task_cause(c, task_id, "bot:ops") == task_wakes.CANT_START
        [wait] = [w for w in waiting_on.waiting_on(c, task_id) if w.who == "bot:ops"]
        assert wait.state == waiting_on.PAUSED and wait.line.endswith(task_wakes.CANT_START)
        assert not [
            row
            for rows in waiting_on.needs_by_person(c).values()
            for row in rows
            if task_id in (row.get("tasks") or [row.get("task_id")])
        ]
    # BotOps fixed it and closed its task: the hold lifts, and the never-stuck check may wake the bot again.
    post(api, f"tasks/{review['id']}/done", {"note": "Unset the upstream it could not fetch."})
    with api.app.state.store.read() as c:
        assert task_wakes.task_cause(c, task_id, "bot:ops") is None


def test_hand_backs_for_different_reasons_or_none_keep_retrying(api):
    machine, task_id, attempt = start(api)
    job_id = attempt["job_id"]
    for reason in ("its worktree could not be made", "", UPSTREAM.format(task=task_id), "other"):
        hand_back(api, machine, attempt, reason)
        assert job_state(api, job_id) == "queued"
        attempt = claim(api, machine)
    assert not botops_tasks(api)


def test_a_second_task_of_the_same_bot_is_named_on_the_open_botops_task(api):
    machine, first, attempt = start(api)
    for _ in range(start_holds.SAME):
        hand_back(api, machine, attempt, UPSTREAM.format(task=first))
        attempt = claim(api, machine)
    assert attempt is None
    made = post(api, "tasks", {"owner": "ops", "title": "Review the multiply PR", "body": "Review it."})
    second = made.get("task", made).get("id") or made["refs"]["task"]
    attempt = claim(api, machine)
    for _ in range(start_holds.SAME):
        assert attempt
        hand_back(api, machine, attempt, UPSTREAM.format(task=second))
        attempt = claim(api, machine)
    [review] = botops_tasks(api)  # one open task per bot
    with api.app.state.store.read() as c:
        said = [r[0] for r in c.execute("SELECT body FROM messages WHERE refs LIKE ?", (f"%{review['id']}%",))]
        assert task_wakes.task_cause(c, second, "bot:ops") == task_wakes.CANT_START
    assert any("Review the multiply PR" in text for text in said)
