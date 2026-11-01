"""What woke each run, its tokens and whether it acted, and the weekly table of them in hub sql
(backend/run_triggers.py; token saving 1/9)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import automations
from backend import github as G
from backend import repositories as R
from backend import routines
from backend import run_triggers as RT
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, as_member, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_sql import query


def newest(c, bot, words):
    """The newest message to `bot` whose text starts with `words`."""
    row = c.execute("SELECT id FROM messages WHERE recipient_id=? AND body LIKE ? ORDER BY rowid DESC LIMIT 1",
                    ("bot:" + bot, words + "%")).fetchone()
    assert row, f"no message to {bot} starting {words!r}"
    return H.message(c, row[0])


def quiet(c, bot="ops"):
    """The wakes so far ran: only what a test writes next is queued."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot_id=? AND state='queued'", (bot,))


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


USAGE = {"input_tokens": 200, "cached_tokens": 1000, "output_tokens": 30, "model": "fake"}


def finish(api, r, attempt, text="", tokens_in=1200, usage=USAGE):
    return post(api, f"attempts/{attempt['id']}/complete",
                {"outcome": "completed", "last_seq": 0, "text": text, "tokens_in": tokens_in, "tokens_out": 30,
                 "usage": usage}, r["token"])


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
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), seconds=-600), task["id"]))
        assert task["id"] in H.wake_stalled(c)["woke"] + H.wake_stalled(c)["checked"]
        stall = c.execute("SELECT id FROM messages WHERE recipient_id='bot:ops' AND json_extract(refs,'$.wake') "
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


@pytest.mark.parametrize("fired_by", ["cron", "event"])
def test_a_routines_new_task_is_woken_by_its_automation(api, fired_by):
    """A fresh occurrence of a routine (now its automation) opens a task; its wake names the automation, not the task
    handoff."""
    store = api.app.state.store
    fields = {"title": "Review the day", "text": "Review it", **(
        {"cron": "0 9 * * *", "timezone": "UTC"} if fired_by == "cron" else {"on": "meeting.ready"})}
    with store.transaction() as c:
        aid = routines.create(c, "human:ana", "ops", fields, key="morning")["automation"]
    if fired_by == "cron":
        automations.tick(store)                                   # sets its first due time
        automations.tick(store, datetime.now(timezone.utc) + timedelta(days=1, minutes=1))
    else:
        with store.transaction() as c:
            H.hook(c, "meeting.ready", "rec-1", lambda: {"title": "Planning"}, "human:ana")
        store.automations.drain()
    with store.read() as c:
        task = c.execute("SELECT id FROM tasks WHERE owner_id='bot:ops' AND title='Review the day'").fetchall()
        assert len(task) == 1
        wake = c.execute("SELECT id FROM messages WHERE recipient_id='bot:ops' AND json_extract(refs,'$.task')=? "
                         "ORDER BY rowid LIMIT 1", (task[0]["id"],)).fetchone()
        assert RT.of_message(H.message(c, wake[0])) == ("other", "automation:" + aid)


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


def test_a_run_gets_the_callers_purpose_else_the_mapped_default(api):
    r = machine(api)
    post(api, "chat/ops", {"text": "Summarize the week."})
    attempt = claim(api, r)
    assert turn(api, attempt)["purpose"] == "answer person message"
    start(api, r, attempt)
    finish(api, r, attempt)
    with api.app.state.store.transaction() as c:
        H.say(c, H.KEEPER, "bot:ops", "Sort the inbox.", kind="notice",
              refs=RT.stamp({}, "schedule", "morning", purpose="  sort   inbox "))
    assert turn(api, claim(api, r))["purpose"] == "sort inbox"
    assert RT.purpose_for("pr-reviewer", "pr_checks") == "handle PR checks"
    assert RT.purpose_for("pr-reviewer", "status_or_relation") == "react to task change"
    assert RT.purpose_for("pr-reviewer-sol", "pr_checks") == "review PR"
    assert RT.purpose_for("reviewer-2", "status_or_relation") == "review PR"
    assert RT.purpose_for("ops", "other") == RT.purpose_for("ops", None) == "unknown"


def test_backfill_fills_old_runs_from_the_map_and_leaves_unmapped_unknown(api):
    with api.app.state.store.transaction() as c:
        for i, (bot, kind) in enumerate([("ops", "comment"), ("pr-reviewer", "pr_checks"), ("ops", None),
                                         ("ops", "other")]):
            c.execute("INSERT INTO turns(id,bot,started_at,trigger,trigger_kind) VALUES(?,?,?,'task',?)",
                      (f"old{i}", bot, H.now(), kind))
        c.execute("INSERT INTO turns(id,bot,started_at,trigger,trigger_kind,purpose) VALUES('named','ops',?,'task',"
                  "'comment','mine')", (H.now(),))
        RT.backfill(c)
        got = dict(c.execute("SELECT id, purpose FROM turns WHERE id LIKE 'old%' OR id='named'").fetchall())
    assert got == {"old0": "answer comment", "old1": "handle PR checks", "old2": "unknown", "old3": "unknown",
                   "named": "mine"}
    rows = {row["purpose"] for row in weekly(api, where="bot='ops'")}
    assert {"answer comment", "unknown", "mine"} <= rows


WRITES = ["task_update", "comment", "message", "pr_link", "file_publish", "reply_to_person"]
NO_WRITES = {"no_writes": "no_change", "bookkeeping_only": "no_change", "task_finished": "nothing_to_do"}


@pytest.mark.parametrize("case", WRITES + list(NO_WRITES))
def test_did_nothing_and_outcome_come_from_what_the_run_wrote(api, case):
    """Each kind of write flips `did_nothing` off; reads and bookkeeping leave it on."""
    r = machine(api)
    if case == "reply_to_person":
        post(api, "chat/ops", {"text": "Anything new?"})
    else:
        task = post(api, "tasks", {"owner_id": "ops", "title": "Check the bank feed", "body": "Daily."})
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
    assert (row["cached_tokens"], row["input_tokens"], row["output_tokens"]) == (1000, 200, 30)


def test_an_answer_to_a_bots_ask_in_the_final_text_counts_once(api):
    r = machine(api)
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:finance", "bot:ops", "Is the bank feed in?", kind="ask")
    attempt = claim(api, r)
    start(api, r, attempt)
    finish(api, r, attempt, "Yes, it came in at six.")
    row = turn(api, attempt)
    assert (row["writes"], row["outcome"], row["did_nothing"]) == (1, "acted", 0)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE sender_id='bot:ops' AND recipient_id='bot:finance'"
                         ).fetchone()[0] == 1
    # The runner sends its completion again: the answer is not written or counted twice.
    api.post(f"/api/v2/attempts/{attempt['id']}/complete", headers=headers(r["token"]),
             json={"outcome": "completed", "last_seq": 0, "text": "Yes, it came in at six.", "usage": USAGE})
    assert turn(api, attempt)["writes"] == 1


def worktree(api, task_id, attempt, path):
    """A worktree of `task_id` on the computer that runs `attempt`."""
    with api.app.state.store.transaction() as c:
        computer = c.execute("SELECT runner_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,repo,branch,path,computer_id) "
                  "VALUES(?,?,'worktree',?,'present','bot:ops',?,'acme/app','tico/x',?,?)",
                  (path, task_id, "worktree:" + path, H.now(), path, computer))


@pytest.mark.parametrize("case", ["own_task", "none", "other_task", "older_runner"])
def test_a_push_from_the_runs_own_task_worktree_is_acting(api, case):
    """Tico never sees a git push; the runner reports the ones from the run's task worktrees with its result."""
    r = machine(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Fix the export", "body": "Push the fix."})
    other = post(api, "tasks", {"owner_id": "ops", "title": "Tidy the docs", "body": "Later."})
    attempt = claim(api, r)
    start(api, r, attempt)
    worktree(api, task["id"], attempt, "tasks/fix/app")
    worktree(api, other["id"], attempt, "tasks/docs/app")
    pushes = {"own_task": [{"path": "tasks/fix/app", "count": 2}], "none": [],
              "other_task": [{"path": "tasks/docs/app", "count": 1}, {"path": "tasks/elsewhere/app", "count": 1}]}
    body = {"outcome": "completed", "last_seq": 0, "text": "", "usage": USAGE}
    if case != "older_runner":
        body["pushes"] = pushes[case]
    post(api, f"attempts/{attempt['id']}/complete", body, r["token"])
    row = turn(api, attempt)
    assert (row["writes"], row["outcome"], row["did_nothing"]) == (
        (2, "acted", 0) if case == "own_task" else (0, "no_change", 1))


@pytest.mark.parametrize("case", ["rotated", "resumed"])
def test_a_run_that_started_a_fresh_session_says_why_and_the_week_counts_it(api, case):
    """The runner reports a session left behind for its size (runner-rs/src/rotate.rs); the token table counts it."""
    r = machine(api)
    post(api, "tasks", {"owner_id": "ops", "title": "Watch the release", "body": "Each hour."})
    attempt = claim(api, r)
    start(api, r, attempt)
    body = {"outcome": "completed", "last_seq": 0, "text": "", "usage": USAGE}
    if case == "rotated":
        body["rotated_at"] = {"reason": "tokens", "tokens": 612_000, "turns": 4, "limit": 400_000}
    post(api, f"attempts/{attempt['id']}/complete", body, r["token"])
    row = turn(api, attempt)
    assert row["rotated_at"] == ("tokens" if case == "rotated" else None)
    rows = weekly(api, where="bot='ops'")
    assert {bucket["purpose"] for bucket in rows} == {row["purpose"]}
    assert sum(bucket["rotated_at"] for bucket in rows) == (1 if case == "rotated" else 0)


@pytest.mark.parametrize("session,said", [
    ({"kind": "resumed", "why": "compacted"}, None),
    ({"kind": "resumed"}, None),
    ({"kind": "new", "why": "none_saved"}, None),
    ({"kind": "new", "why": "not_found"}, "Started a new session: the old one wasn't on this computer"),
    ({"kind": "new", "why": "resume_failed", "detail": "exit 1"},
     "Started a new session: the old one could not be continued"),
    ({"kind": "new", "why": "fallback_side"},
     "Started a new session: the run used a fallback model, which keeps a session of its own"),
    ({"kind": "new", "why": "a_newer_reason"}, "Started a new session (a_newer_reason)"),
])
def test_a_run_says_whether_it_resumed_its_session_and_a_new_one_is_in_the_tasks_history(api, session, said):
    r = machine(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Watch the release", "body": "Each hour."})
    attempt = claim(api, r)
    start(api, r, attempt)
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "text": "", "usage": USAGE, "session": session}, r["token"])
    row = turn(api, attempt)
    assert (row["session_kind"], row["session_why"]) == (session["kind"], session.get("why"))
    assert row["rotated"] is None
    with api.app.state.store.read() as c:
        notes = [e["note"] for e in c.execute("SELECT note FROM task_events WHERE task_id=? AND field='session'",
                                              (task["id"],))]
    assert notes == ([said] if said else [])


def test_a_completion_from_an_older_runner_has_no_session_and_a_session_that_is_too_long_is_refused(api):
    r = machine(api)
    post(api, "tasks", {"owner_id": "ops", "title": "Watch the release", "body": "Each hour."})
    attempt = claim(api, r)
    start(api, r, attempt)
    body = {"outcome": "completed", "last_seq": 0, "text": "", "usage": USAGE}
    post(api, f"attempts/{attempt['id']}/complete", {**body, "session": {"kind": "new", "why": "x" * 61}}, r["token"],
         expected=422)
    post(api, f"attempts/{attempt['id']}/complete",
         {**body, "rotated": {"reason": "turns", "tokens": 10, "turns": 9, "limit": 8}}, r["token"])
    row = turn(api, attempt)
    assert (row["rotated_at"], row["session_kind"], row["session_why"]) == ("turns", None, None)


def test_a_persons_clear_and_a_topic_change_in_a_task_room_are_in_the_tasks_history(api):
    from backend import views
    machine(api)
    task = post(api, "tasks", {"owner_id": "ops", "title": "Watch the release", "body": "Each hour."})
    with api.app.state.store.transaction() as c:
        room = c.execute("SELECT id FROM conversations WHERE task_id=?", (task["id"],)).fetchone()[0]
        views.clear_chat_session(c, api.app.state.store.settings, "human:ana", "ops", room)
        views.clear_chat_session(c, api.app.state.store.settings, "human:ana", "ops", room, {"reason": "topic_change"})
        notes = [e["note"] for e in c.execute("SELECT note FROM task_events WHERE task_id=? AND field='session' "
                                              "ORDER BY rowid", (task["id"],))]
    assert notes == ["Started a new session: a person cleared it",
                     "Started a new session: the conversation moved to a new topic"]


def lost(c, bot, why, n, kind="new", hours=1):
    for i in range(n):
        c.execute("INSERT INTO turns(id,bot,started_at,session_kind,session_why) VALUES(?,?,?,?,?)",
                  (f"{bot}-{why}-{hours}-{i}", bot, H.shift(H.now(), hours=-hours), kind, why))


def session_findings(api):
    from backend import health_monitor
    with api.app.state.store.read() as c:
        return {f["key"]: f for f in health_monitor._session_checks(c)}


def test_health_names_a_bot_that_lost_its_session_more_than_twice_in_a_day(api):
    with api.app.state.store.transaction() as c:
        lost(c, "ops", "not_found", 2)
        lost(c, "cmo", "not_found", 1)
        lost(c, "cmo", "resume_failed", 1)
        lost(c, "cmo", "prompt_too_long_after_compact", 1)
        lost(c, "cfo", "not_found", 5, hours=25)             # not in the last day
    found = session_findings(api)
    assert list(found) == ["new_sessions:cmo"]                # two for ops is not more than two
    assert "3 new ones in a day" in found["new_sessions:cmo"]["title"]
    with api.app.state.store.transaction() as c:
        lost(c, "ops", "interrupted", 1)
    assert set(session_findings(api)) == {"new_sessions:cmo", "new_sessions:ops"}


def test_health_does_not_count_a_first_session_a_cleared_one_or_a_topic_change(api):
    with api.app.state.store.transaction() as c:
        lost(c, "ops", "none_saved", 10)
        lost(c, "ops", "fallback_side", 10)
        lost(c, "ops", "person_clear", 10)
        lost(c, "ops", "topic_change", 10)
        lost(c, "ops", "not_found", 10, kind="resumed")
    assert session_findings(api) == {}


def test_a_restore_ends_open_runs_with_an_outcome(api):
    r = machine(api)
    post(api, "tasks", {"owner_id": "ops", "title": "Check the bank feed", "body": "Daily."})
    attempt = claim(api, r)
    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE turns SET finished_at=?,exit='interrupted',{RT.SETTLE_SQL} WHERE id=?",
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
    # The runner's one-number counter says 9000 (a resumed thread's whole total); its measured usage, a primary
    # and a fallback segment, says 1200. The table reads the measured usage.
    finish(api, r, attempt, "Nothing new.", tokens_in=9000, usage={**USAGE, "segments": [
        {"input_tokens": 50, "cached_tokens": 250, "output_tokens": 10, "model": "fake"},
        {"input_tokens": 150, "cached_tokens": 750, "output_tokens": 20, "model": "fake-fallback"}]})
    with api.app.state.store.transaction() as c:
        # A run from before triggers were recorded, and one still going.
        c.execute("INSERT INTO turns(id,bot,started_at,finished_at,trigger,exit,input_tokens,output_tokens) "
                  "VALUES('old-run','ops',?,?,'task','completed',500,5)", (H.now(), H.now()))
        c.execute("INSERT INTO turns(id,bot,started_at,trigger,trigger_kind) VALUES('live-run','ops',?,'say','comment')",
                  (H.now(),))
    # An attempt that failed with no turn row at all (as before turns were written at the claim).
    post(api, "chat/ops", {"text": "And the bank feed?"})
    lost = claim(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM turns WHERE id=?", (lost["id"],))
        c.execute("UPDATE attempts SET state='failed' WHERE id=?", (lost["id"],))
    rows = {row["trigger"]: row for row in weekly(api, where="bot='ops'")}
    week = H.now()[:10]
    assert set(rows) == {"person_message", "unknown", "comment"}
    assert all(row["week"] <= week for row in rows.values())
    assert rows["person_message"] == {**rows["person_message"], "runs": 1, "input_tokens": 1200,
                                      "cached_input_tokens": 1000, "output_tokens": 30, "acted": 1, "no_change": 0,
                                      "nothing_to_do": 0, "unknown": 0, "acted_pct": 100.0}
    assert rows["unknown"] == {**rows["unknown"], "runs": 2, "input_tokens": 500, "output_tokens": 5,
                               "unknown": 2, "unknown_pct": 100.0, "acted": 0, "did_nothing": 0}
    assert rows["comment"]["unknown"] == 1        # still running: no outcome yet, still counted
    every = query(api, "SELECT (SELECT count(*) FROM turns) + (SELECT count(*) FROM attempts a "
                       "WHERE NOT EXISTS (SELECT 1 FROM turns t WHERE t.id = a.id))")["rows"][0][0]
    assert sum(row["runs"] for row in weekly(api)) == every == 4


def test_the_weekly_table_is_read_only_and_follows_who_may_read_the_bot(api):
    with api.app.state.store.transaction() as c:
        for bot in ("ops", "inbox"):
            c.execute("INSERT INTO turns(id,bot,started_at,trigger,trigger_kind,outcome) "
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


def test_the_weekly_table_adds_up_only_runs_the_reader_may_read(api):
    """A bucket of one run would tell its reader that run happened, what it cost and whether it acted."""
    as_member(api, "ben@acme.example")
    r = machine(api)
    store = api.app.state.store

    def ran(start_with):
        start_with()
        attempt = claim(api, r)
        start(api, r, attempt)
        finish(api, r, attempt)
        return attempt

    ran(lambda: post(api, "chat/ops", {"text": "My own numbers, please."}))      # Ana's room: its message and inputs
    shared = post(api, "tasks", {"owner_id": "ops", "title": "Check the bank feed", "body": "Daily."})
    ran(lambda: None)                                                             # Ben may read this task, for now
    secret = post(api, "tasks", {"owner_id": "ops", "title": "Plan the reorg", "body": "Ana only."})
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (secret["id"],))
    ran(lambda: None)                                                             # a run on Ana's private task
    # A run Ben may read the attempt of, but not its turn (the turn names Ana's private task): not a turnless
    # attempt, so not counted at all.
    post(api, "tasks", {"owner_id": "ops", "title": "Check the card feed", "body": "Daily."})
    hidden_turn = ran(lambda: None)
    with store.transaction() as c:
        c.execute("UPDATE turns SET task_id=? WHERE id=?", (secret["id"], hidden_turn["id"]))
    post(api, "chat/ops", {"text": "And the rest?"})                              # Ana's room, failed with no turn
    lost = claim(api, r)
    with store.transaction() as c:
        c.execute("DELETE FROM turns WHERE id=?", (lost["id"],))
        c.execute("UPDATE attempts SET state='failed' WHERE id=?", (lost["id"],))

    def runs(token):
        return {row["trigger"]: row["runs"] for row in weekly(api, token)}

    def readable(token, table):
        return query(api, f"SELECT count(*) FROM {table}", token)["rows"][0][0]

    assert runs("ana-test") == {"person_message": 1, "status_or_relation": 3, "unknown": 1}
    assert runs("ben-test") == {"status_or_relation": 1} and readable("ben-test", "turns") == 1
    assert readable("ben-test", "attempts") == 2                  # the control's attempt is his to read
    # Ben's access to the shared task ends after its run: its totals leave his table with it.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (shared["id"],))
    assert runs("ben-test") == {} and readable("ben-test", "turns") == 0
    assert sum(runs("ana-test").values()) == 5


def test_a_run_whose_inputs_came_from_a_private_task_leaves_the_weekly_table(api):
    """A run on a task Ben may read, turn or turnless, that read a message of Ana's private task among its inputs."""
    as_member(api, "ben@acme.example")
    r = machine(api)
    store = api.app.state.store
    private = post(api, "tasks", {"owner_id": "ops", "title": "Plan the reorg", "body": "Ana only.", "private": True})
    secret = claim(api, r)
    start(api, r, secret)
    finish(api, r, secret)
    post(api, "tasks", {"owner_id": "ops", "title": "Check the bank feed", "body": "Daily."})
    tainted = claim(api, r)
    start(api, r, tainted)
    finish(api, r, tainted)
    post(api, "tasks", {"owner_id": "ops", "title": "Check the card feed", "body": "Daily."})
    lost = claim(api, r)
    with store.transaction() as c:
        c.execute("DELETE FROM turns WHERE id=?", (lost["id"],))
        c.execute("UPDATE attempts SET state='failed' WHERE id=?", (lost["id"],))

    def runs(token):
        return {row["trigger"]: row["runs"] for row in weekly(api, token)}

    assert runs("ben-test") == {"status_or_relation": 1, "unknown": 1}
    with store.transaction() as c:
        # Each run read a different message of the private task (a message is one run's input).
        H.task_comment(c, "human:ana", private["id"], "And the second draft.")
        later = c.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1",
                          (private["conversation_id"],)).fetchone()[0]
        quiet(c)
        first = c.execute("SELECT message_id FROM jobs WHERE id=?", (secret["job_id"],)).fetchone()[0]
        for attempt, message in ((tainted, first), (lost, later)):
            c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (attempt["id"], message))
    assert runs("ben-test") == {}
    assert runs("ana-test") == {"status_or_relation": 2, "unknown": 1}


def test_a_bots_result_accepted_edited_or_overridden_gives_its_run_one_outcome(api):
    ana = H.human_actor("ana")
    with api.app.state.store.transaction() as c:
        old = H.turn_start(c, "bot:ops", "ops")              # a run with no result, as runs before this were
        before = dict(c.execute("SELECT * FROM turns WHERE id=?", (old["id"],)).fetchone())
        runs = {}
        for name in ("accepted", "edited", "overridden", "own"):
            task = H.task_create(c, ana, f"Recheck the {name} cash warning", "Before the deadline.", "bot:ops")
            runs[name] = (task["id"], H.turn_start(c, "bot:ops", "ops", task_id=task["id"])["id"])
            H.task_update(c, "bot:ops", task["id"], status="done", note="Rechecked.")
        H.task_close(c, ana, runs["accepted"][0])
        H.task_update(c, ana, runs["edited"][0], body="Before the deadline, with the March file.")
        H.task_close(c, ana, runs["edited"][0])                       # after its edit: still one, `edited`
        H.task_update(c, ana, runs["overridden"][0], status="open", note="Not yet: the March file is missing.")
        H.task_update(c, "bot:ops", runs["own"][0], status="doing")    # the bot moving its own result is no verdict
        got = {r[0]: (r[1], json.loads(r[2])) for r in c.execute(
            "SELECT target, actor, detail FROM events WHERE action='turn.outcome'")}
        assert c.execute("SELECT count(*) FROM events WHERE action='turn.outcome'").fetchone()[0] == len(got) == 3
        for name, to in (("accepted", "closed"), ("edited", "done"), ("overridden", "open")):
            task_id, turn_id = runs[name]
            assert got[turn_id] == (ana, {"task_id": task_id, "outcome": name, "from": "done", "to": to})
        assert dict(c.execute("SELECT * FROM turns WHERE id=?", (old["id"],)).fetchone()) == before
