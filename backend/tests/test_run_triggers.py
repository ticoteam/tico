"""What woke each run, its tokens and whether it acted, and the weekly table of them in hub sql
(backend/run_triggers.py; token saving 1/9)."""
from datetime import timedelta

import pytest

from backend import github as G
from backend import repositories as R
from backend import run_triggers as RT
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, as_member, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_sql import query


def newest(c, bot, words):
    """The newest message to `bot` whose text starts with `words`."""
    row = c.execute("SELECT id FROM messages WHERE to_actor=? AND body LIKE ? ORDER BY rowid DESC LIMIT 1",
                    ("bot:" + bot, words + "%")).fetchone()
    assert row, f"no message to {bot} starting {words!r}"
    return H.message(c, row[0])


def quiet(c, bot="ops"):
    """The wakes so far ran: only what a test writes next is queued."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot=? AND state='queued'", (bot,))


def turn(api, attempt):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM turns WHERE id=?", (attempt["id"],)).fetchone())


def machine(api, bot="ops"):
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    return r


def start(api, r, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "fixture-thread"}, r["token"])


def finish(api, r, attempt, text=""):
    return post(api, f"attempts/{attempt['id']}/complete",
                {"outcome": "completed", "last_seq": 0, "text": text, "tokens_in": 1200, "tokens_out": 30,
                 "usage": {"input_tokens": 200, "cached_tokens": 1000, "output_tokens": 30, "model": "fake"}}, r["token"])


def test_each_trigger_kind_is_stamped_where_the_wake_is_made(api):
    store = api.app.state.store
    with store.transaction() as c:
        # status_or_relation: a task handed to the bot.
        task = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:ops")
        assert RT.of_message(newest(c, "ops", "New task from")) == ("status_or_relation", None)
        # A subtask finishing names the subtask it was.
        child = H.task_create(c, "bot:ops", "Pull the bank file", "For the parent.", "bot:finance",
                              parent_id=task["id"])
        H.task_update(c, "bot:finance", child["id"], status="done", note="Pulled.")
        assert RT.of_message(newest(c, "ops", "")) == ("status_or_relation", child["id"])
        # comment: a bot writes on the task.
        H.task_comment(c, "bot:finance", task["id"], "The bank file is in.")
        said = newest(c, "ops", "The bank file is in.")
        assert RT.of_message(said) == ("comment", said["id"])
        # pr_checks: a pull request's burst of check results.
        R.save_metadata(c, "github-task-wake:" + task["id"],
                        {"due": H.shift(H.now(), seconds=-1), "items": ["Unit tests failed"]})
        assert G.flush_wakes(c) == [task["id"]]
        assert RT.of_message(newest(c, "ops", "Unit tests failed")) == ("pr_checks", None)
        # stall_check: Tico's look at a task nothing will move.
        quiet(c)
        c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.shift(H.now(), seconds=-600), task["id"]))
        assert task["id"] in H.wake_stalled(c)["woke"] + H.wake_stalled(c)["checked"]
        stall = c.execute("SELECT id FROM messages WHERE to_actor='bot:ops' AND json_extract(refs_json,'$.wake') "
                          "IN ('stalled','status_check') ORDER BY rowid DESC LIMIT 1").fetchone()
        assert RT.of_message(H.message(c, stall[0]))[0] == "stall_check"
        # other: a keeper message nothing stamped (a Slack digest).
        conv = H.conversation(c, task["conversation_id"])
        H.feed(c, "bot:ops", "Three new posts in #general.", conv)
        assert RT.of_message(newest(c, "ops", "Three new posts")) == ("other", None)
        # schedule: a snooze that ends.
        quiet(c)
        H.TS.snooze(c, "human:ana", task["id"], H.shift(H.now(), days=1))
    Scheduler(store, api.app.state.execution).tick(H.parse_ts(H.shift(H.now(), days=1)) + timedelta(seconds=1))
    with store.read() as c:
        assert RT.of_message(newest(c, "ops", "Snooze over")) == ("schedule", None)
    # person_message: a person writes to the bot.
    said = post(api, "chat/ops", {"text": "How is the cash warning?"})
    with store.read() as c:
        assert RT.of_message(H.message(c, said["id"])) == ("person_message", said["id"])


def test_the_claim_puts_the_wake_on_the_run(api):
    r = machine(api)
    said = post(api, "chat/ops", {"text": "Summarize the week."})
    attempt = claim(api, r)
    row = turn(api, attempt)
    assert (row["trigger_kind"], row["trigger_source"], row["writes"], row["outcome"]) == (
        "person_message", said["id"], 0, None)
    start(api, r, attempt)
    finish(api, r, attempt)
    with api.app.state.store.transaction() as c:
        quiet(c)
        H.task_create(c, H.human_actor("ana"), "Close the books", "By Friday.", "bot:ops")
    assert turn(api, claim(api, r))["trigger_kind"] == "status_or_relation"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM turns WHERE trigger_kind IS NULL").fetchone()[0] == 0


WRITES = ["task_update", "comment", "message", "pr_link", "file_publish", "reply_to_person"]
NO_WRITES = {"no_writes": "no_change", "bookkeeping_only": "no_change", "task_finished": "nothing_to_do"}


@pytest.mark.parametrize("case", WRITES + list(NO_WRITES))
def test_did_nothing_and_outcome_come_from_what_the_run_wrote(api, case):
    """Each kind of write flips `did_nothing` off; reads and bookkeeping leave it on."""
    r = machine(api)
    if case == "reply_to_person":
        post(api, "chat/ops", {"text": "Anything new?"})
    else:
        task = post(api, "tasks", {"owner": "ops", "title": "Check the bank feed", "body": "Daily."})
    attempt = claim(api, r)
    start(api, r, attempt)
    token = attempt["token"]
    if case == "bookkeeping_only":
        # Its status line, a look in hub sql and a conversation read: nobody else sees anything change.
        post(api, "bots/ops/status", {"state": "running", "focus": "Checking the bank feed"}, token)
        post(api, "sql", {"sql": "SELECT count(*) FROM tasks"}, token)
        get(api, f"conversations/{task['conversation_id']}/messages", token)
    elif case == "task_update":
        version = get(api, "tasks/" + task["id"], token)["task"]["version"]
        post(api, "tasks/" + task["id"], {"version": version, "status": "doing"}, token)
    elif case == "comment":
        post(api, f"tasks/{task['id']}/comments", {"text": "Feed checked: two new lines."}, token)
    elif case == "message":
        post(api, "messages", {"to": "human:ana", "text": "Two new lines in the feed.",
                               "conversation_id": task["conversation_id"]}, token)
    elif case == "pr_link":
        post(api, f"tasks/{task['id']}/links", {"url": "https://github.com/ticoteam/tico/pull/412"}, token)
    elif case == "file_publish":
        r_file = api.post("/api/v2/files/uploads", json={"name": "feed.md", "text": "# Feed"}, headers=headers(token))
        assert r_file.status_code == 200, r_file.text
    elif case == "task_finished":
        with api.app.state.store.transaction() as c:
            H.task_close(c, H.human_actor("ana"), task["id"], "Not needed.", quiet=True)
    finish(api, r, attempt, "Nothing new." if case == "reply_to_person" else "")
    row = turn(api, attempt)
    assert row["outcome"] == ("acted" if case in WRITES else NO_WRITES[case])
    assert row["did_nothing"] == (0 if case in WRITES else 1)
    assert (row["writes"] > 0) == (case in WRITES)
    assert (row["tokens_in"], row["cached_tokens"], row["input_tokens"], row["output_tokens"]) == (1200, 1000, 200, 30)


def test_a_restore_ends_open_runs_with_an_outcome(api):
    r = machine(api)
    post(api, "tasks", {"owner": "ops", "title": "Check the bank feed", "body": "Daily."})
    attempt = claim(api, r)
    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE turns SET finished=?,exit='interrupted',{RT.SETTLE_SQL} WHERE id=?",
                  (H.now(), attempt["id"]))
    assert (turn(api, attempt)["outcome"], turn(api, attempt)["did_nothing"]) == ("no_change", 1)


def weekly(api, token="ana-test", where="1"):
    out = query(api, f"SELECT * FROM run_weekly WHERE {where} ORDER BY week, bot, trigger", token)
    return [dict(zip(out["columns"], row)) for row in out["rows"]]


def test_the_weekly_table_counts_every_run_and_old_ones_read_unknown(api):
    r = machine(api)
    post(api, "chat/ops", {"text": "Anything new?"})
    attempt = claim(api, r)
    start(api, r, attempt)
    finish(api, r, attempt, "Nothing new.")
    with api.app.state.store.transaction() as c:
        # A run from before triggers were recorded, and one still going.
        c.execute("INSERT INTO turns(id,bot,started,finished,trigger,exit,tokens_in,tokens_out) "
                  "VALUES('old-run','ops',?,?,'task','completed',500,5)", (H.now(), H.now()))
        c.execute("INSERT INTO turns(id,bot,started,trigger,trigger_kind) VALUES('live-run','ops',?,'say','comment')",
                  (H.now(),))
    rows = {row["trigger"]: row for row in weekly(api, where="bot='ops'")}
    week = H.now()[:10]
    assert set(rows) == {"person_message", "unknown", "comment"}
    assert all(row["week"] <= week for row in rows.values())
    assert rows["person_message"] == {**rows["person_message"], "runs": 1, "input_tokens": 1200,
                                      "cached_input_tokens": 1000, "output_tokens": 30, "acted": 1, "no_change": 0,
                                      "nothing_to_do": 0, "unknown": 0, "acted_pct": 100.0}
    assert rows["unknown"] == {**rows["unknown"], "runs": 1, "input_tokens": 500, "output_tokens": 5,
                               "unknown": 1, "unknown_pct": 100.0, "acted": 0}
    assert rows["comment"]["unknown"] == 1        # still running: no outcome yet, still counted
    assert sum(row["runs"] for row in weekly(api)) == query(api, "SELECT count(*) FROM turns")["rows"][0][0]


def test_the_weekly_table_is_read_only_and_follows_who_may_read_the_bot(api):
    with api.app.state.store.transaction() as c:
        for bot in ("ops", "inbox"):
            c.execute("INSERT INTO turns(id,bot,started,trigger,trigger_kind,outcome) "
                      "VALUES(?,?,?,'task','schedule','no_change')", (bot + "-run", bot, H.now()))
    assert {row["bot"] for row in weekly(api)} == {"ops", "inbox"}
    as_member(api, "ben@acme.example")
    assert {row["bot"] for row in weekly(api, "ben-test")} == {"ops"}   # the inbox bot is Ana's alone
    for sql in ("DELETE FROM run_weekly", "INSERT INTO run_weekly(bot) VALUES('x')",
                "UPDATE run_weekly SET runs=0", "DROP VIEW run_weekly"):
        assert query(api, sql, expected=422)["error"]["code"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM turns WHERE id IN ('ops-run','inbox-run')").fetchone()[0] == 2


def test_open_task_states_match_the_board():
    assert set(RT.OPEN_TASK) == set(H.ACTIVE_STATUSES) | {"blocked"}
    with pytest.raises(ValueError):
        RT.stamp({}, "nudge")
    assert RT.counts("task.comment") and not RT.counts("status.set") and not RT.counts("attempt.started")
