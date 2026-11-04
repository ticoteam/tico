"""The task roundup (backend/task_roundup.py): each run (three times a day by default, at 6am, noon and 6pm company time) gives
each active bot at most one wake, with one list of its tasks that have a missed update, have gone quiet, or have a follow-up
time that has come. A task waiting on a person or a live blocker with nothing new is not listed, a task waiting on nothing is
listed once quiet a day, Tico's own notices and check-backs are not news, a task with a run already queued is not listed, a
bot with nothing to list gets no message, a private task is named without its title or any detail of it, a list reaches the
bot alone, and a quiet task is listed again only after seven days. Synthetic fixtures only."""
import importlib
import json
from datetime import datetime, timezone

import pytest

from backend import scheduler, task_check_backs, task_roundup
from backend import task_relations as TR
from backend.store import H, encode
from backend.tests.test_api import api  # noqa: F401

DAY = "2026-11-03T14:00:00.000000Z"     # the 6am run (company time is Pacific, PST on this date); fixed for the tests
NOON = H.shift(DAY, hours=6)            # the noon run, 20:00 UTC


@pytest.fixture(autouse=True)
def build_clock(monkeypatch):
    """A list is built at its run's time unless a test moves the build clock, so the checkpoints the tests read are the runs'."""
    monkeypatch.setattr(task_roundup, "_built_at", lambda: DAY)


def ago(**kw):
    return H.shift(DAY, **{k: -v for k, v in kw.items()})


def task(c, title):
    return H.task_create(c, H.human_actor("ana"), title, f"{title}, for the roundup.", "bot:finance")


def settle(c, tid, slug, turned):
    """The task as it stood before its bot's last turn on it at `turned`: its own row, and its messages and history a day
    older, so nothing has happened on it since the turn."""
    conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (turned, tid))
    c.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (H.shift(turned, days=-1), conv))
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
              (H.shift(turned, days=-1), tid))
    c.execute("INSERT INTO turns(id,bot,thread_id,started_at,finished_at,trigger,message_id,task_id,exit,summary) "
              "VALUES(?,?,NULL,?,?,'schedule',NULL,?,'done','')", (H.new_id(), slug, turned, turned, tid))


def blocks(c, blocker, tid, when):
    c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'blocks','human:ana',?)",
              (blocker, tid, when))


def complete_jobs(c, slug="finance"):
    """The creation wakes ran: nothing is queued for the bot."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot_id=?", (slug,))


def note(c, tid, sender, when):
    """A message in the task's conversation from `sender` (None for no sender), as Tico's wake notices land."""
    conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
              "VALUES(?,?,?,?,'notice','Check on it.',NULL,?)",
              (H.new_id(), conv, sender, H.bot_actor("finance"), when))


def lists(c, slug="finance"):
    return [row[0] for row in c.execute("SELECT body FROM messages WHERE recipient_id=? AND refs LIKE '%task_roundup%' "
                                        "ORDER BY created_at", (H.bot_actor(slug),)).fetchall()]


def only_list(c, slug="finance"):
    found = lists(c, slug)
    assert len(found) == 1, found
    return found[0]


def named_in(body):
    """How many task lines a list names (not the 'and K more' line)."""
    return len([line for line in body.split("\n") if line.startswith("- ") and not line.startswith("- and ")])


def test_a_task_blocked_on_a_person_with_nothing_new_is_not_listed_until_the_blocker_is_closed_before_the_turn(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Sign the lease", "Ana signs it.", "human:ana")
        mine = task(c, "Hold the warehouse for the lease")
        blocks(c, blocker["id"], mine["id"], ago(days=3))
        settle(c, mine["id"], "finance", ago(days=3))
        complete_jobs(c)
        # The blocker is still open: the bot waits on Ana, and nothing has changed since its turn.
        assert "finance" not in task_roundup.run(c, DAY)["woke"]
        assert lists(c) == []
        # Closed a day before the bot's turn: nothing is missed, but the task is quiet and nothing waits on anyone.
        c.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (ago(days=4), blocker["id"]))
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert f"{mine['id'][:8]} Hold the warehouse for the lease: Quiet for 3 days" in only_list(c)


def test_a_blocker_finished_and_taken_off_after_the_bots_last_turn_is_listed_as_a_missed_update(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Sign the lease", "Ana signs it.", "human:ana")
        mine = task(c, "Hold the warehouse for the lease")
        blocks(c, blocker["id"], mine["id"], ago(days=3))
        settle(c, mine["id"], "finance", ago(days=3))
        complete_jobs(c)
        c.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (ago(hours=2), blocker["id"]))
        TR.remove_blocker(c, H.KEEPER, mine["id"], blocker["id"], keeper_note="unblocked: Sign the lease")
        # The removal is the news: its row is the latest, and the rows the removal's side effects wrote are older.
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? AND kind='blocked_by'",
                  (ago(hours=1), mine["id"]))
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? AND kind<>'blocked_by'",
                  (ago(hours=3), mine["id"]))
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{mine['id'][:8]} Hold the warehouse for the lease: Update you may have missed: a blocker is done" in body


def test_a_blocker_taken_off_by_a_person_after_the_turn_names_them_as_news(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Sign the lease", "Ana signs it.", "human:ana")
        mine = task(c, "Hold the warehouse for the lease")
        blocks(c, blocker["id"], mine["id"], ago(days=3))
        settle(c, mine["id"], "finance", ago(days=3))
        complete_jobs(c)
        H._task_event(c, mine["id"], H.human_actor("ana"), "blocked_by", blocker["id"], None, "Ana took it off")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_id=? AND kind='blocked_by'", (ago(hours=1), mine["id"]))
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert "Update you may have missed: a blocker was taken off by " in only_list(c)


def test_a_subtask_finished_by_someone_else_is_news_and_one_the_bot_finished_is_not(api):
    store = api.app.state.store
    with store.transaction() as c:
        parent = task(c, "Ship the kiosk")
        settle(c, parent["id"], "finance", ago(days=2))
        theirs, own = task(c, "Wire the kiosk"), task(c, "Test the kiosk")
        for child in (theirs, own):
            c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'parent','human:ana',?)",
                      (child["id"], parent["id"], ago(days=2)))
            c.execute("UPDATE tasks SET status='done' WHERE id=?", (child["id"],))
        H.history_event(c, "task", theirs["id"], H.human_actor("ana"), "status", "doing", "done", "")
        H.history_event(c, "task", own["id"], H.bot_actor("finance"), "status", "doing", "done", "")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?,?) AND new='done'",
                  (ago(hours=1), theirs["id"], own["id"]))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert (f"{parent['id'][:8]} Ship the kiosk: Update you may have missed: a subtask is done "
                "(1 since you last worked on it)") in only_list(c)


def test_an_idle_open_task_untouched_two_days_is_listed_as_quiet_and_a_recent_one_is_not(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        recent = task(c, "Book the dentist")
        settle(c, recent["id"], "finance", ago(hours=12))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{idle['id'][:8]} Renew the parking permit: Quiet for 2 days, nothing waiting on anyone" in body
        assert recent["id"][:8] not in body


def test_a_task_with_a_run_already_queued_is_not_listed(api):
    store = api.app.state.store
    with store.transaction() as c:
        queued = task(c, "Chase the invoice")        # its creation wake is still queued
        settle(c, queued["id"], "finance", ago(days=2))
        idle = task(c, "Plan the offsite")
        settle(c, idle["id"], "finance", ago(days=2))
        complete_jobs(c)
        c.execute("UPDATE jobs SET state='queued' WHERE message_id IN (SELECT id FROM messages WHERE conversation_id=?)",
                  (c.execute("SELECT conversation_id FROM tasks WHERE id=?", (queued["id"],)).fetchone()[0],))
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{idle['id'][:8]} Plan the offsite" in body
        assert queued["id"][:8] not in body


def test_a_bot_with_nothing_to_list_gets_no_message(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('idlebot','idlebot','Idle Bot','active')")
        complete_jobs(c)
        out = task_roundup.run(c, DAY)
        assert "idlebot" not in out["woke"] and lists(c, "idlebot") == []
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key='task_roundup:idlebot'").fetchone()[0] == 0


def test_a_list_is_one_queued_wake_for_its_bot_and_its_run_and_tasks_are_written_only_with_it(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        queued = c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='finance' "
                           "AND j.state='queued' AND m.refs LIKE '%task_roundup%'").fetchone()[0]
        assert queued == 1
        assert c.execute("SELECT value FROM registry_metadata WHERE key='task_roundup:finance'").fetchone()[0] == DAY
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key=?",
                         (f"task_roundup:task:{idle['id']}",)).fetchone()[0] == 1
        assert "finance" not in task_roundup.run(c, H.shift(DAY, hours=3))["woke"]
        assert c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='finance' "
                         "AND m.refs LIKE '%task_roundup%'").fetchone()[0] == 1


def test_a_list_no_rule_queued_is_not_marked_sent_or_its_tasks_listed(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        complete_jobs(c)
        monkeypatch.setattr(task_roundup, "_queued", lambda c, message_id: False)
        assert "finance" not in task_roundup.run(c, DAY)["woke"]
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'task_roundup:%'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM events WHERE action='task_roundup.sent'").fetchone()[0] == 0
        monkeypatch.undo()
        assert "finance" in task_roundup.run(c, DAY)["woke"]


def test_a_repeat_run_gives_no_second_list_and_a_list_names_at_most_25_tasks(api):
    store = api.app.state.store
    with store.transaction() as c:
        for n in range(30):
            settle(c, task(c, f"Old errand {n:02d}")["id"], "finance", ago(days=2))
        complete_jobs(c)
        first = task_roundup.run(c, DAY)
        again = task_roundup.run(c, DAY)                           # the same run's time: nothing more
        assert "finance" in first["woke"] and "finance" not in again["woke"]
        body = only_list(c)
        lines = body.split("\n")
        named = [line for line in lines if line.startswith("- ") and not line.startswith("- and ")]
        assert len(named) == 25 and lines[-1] == "- and 5 more"
        assert lines[0] == task_roundup.HEADING
        assert c.execute("SELECT count(*) FROM events WHERE action='task_roundup.sent'").fetchone()[0] == 1
        # The noon run lists only the five not named: the 25 named are not listed again for a week.
        assert "finance" in task_roundup.run(c, NOON)["woke"]
        assert named_in(lists(c)[-1]) == 5 and "- and " not in lists(c)[-1]
        # Seven days after the first list the 25 are quiet and listed again.
        assert "finance" in task_roundup.run(c, H.shift(DAY, days=7))["woke"]
        assert named_in(lists(c)[-1]) == 25


def test_tico_notices_and_unsent_messages_after_the_turn_are_not_news_and_do_not_reset_quiet(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Renew the car insurance")
        settle(c, mine["id"], "finance", ago(days=3))
        note(c, mine["id"], H.KEEPER, ago(days=1))      # Tico's wake notice, a day after the turn
        note(c, mine["id"], None, ago(days=1))          # a message with no sender
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{mine['id'][:8]} Renew the car insurance: Quiet for 3 days, nothing waiting on anyone" in body
        assert "Update you may have missed" not in body


def test_a_check_back_row_is_not_news_but_a_status_change_by_tico_is(api):
    store = api.app.state.store
    with store.transaction() as c:
        plants = task(c, "Water the office plants")
        settle(c, plants["id"], "finance", ago(days=3))
        H._task_event(c, plants["id"], H.KEEPER, "check_back_note", "a", "b", "Look again tomorrow")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_id=? AND kind='check_back_note'",
                  (ago(days=1), plants["id"]))
        moved = task(c, "File the expense report")
        settle(c, moved["id"], "finance", ago(days=3))
        H.history_event(c, "task", moved["id"], H.KEEPER, "status", "open", "waiting", "Moved by the hub")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_id=? AND kind='status' AND actor_id=?",
                  (ago(days=1), moved["id"], H.KEEPER))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{plants['id'][:8]} Water the office plants: Quiet for 3 days, nothing waiting on anyone" in body
        assert (f"{moved['id'][:8]} File the expense report: Update you may have missed: status changed by Tico "
                "(1 since you last worked on it)") in body


def test_a_due_date_set_by_someone_else_is_news_even_when_it_had_no_date_before(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Renew the car tax")
        settle(c, mine["id"], "finance", ago(days=2))
        H._task_event(c, mine["id"], H.human_actor("ana"), "due", None, "2026-11-10", "Set the due date")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_id=? AND kind='due'", (ago(hours=1), mine["id"]))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert f"{mine['id'][:8]} Renew the car tax: Update you may have missed: due changed by " in only_list(c)


def test_a_creation_row_is_not_news_even_when_the_bot_never_worked_on_the_task(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Plan the hike")               # its creation rows are its status and step, with no old value
        conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (mine["id"],)).fetchone()[0]
        for sql in ("UPDATE tasks SET updated_at=? WHERE id=?", "UPDATE history_events SET created_at=? WHERE subject_id=?",
                    "UPDATE messages SET created_at=? WHERE conversation_id=?"):
            c.execute(sql, (ago(days=3), mine["id"] if "tasks" in sql or "subject_id" in sql else conv))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{mine['id'][:8]} Plan the hike: Quiet for 3 days, nothing waiting on anyone" in body
        assert "Update you may have missed" not in body


def test_a_message_edited_by_someone_else_after_the_turn_is_news(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Check the signage quote")
        settle(c, mine["id"], "finance", ago(days=2))
        note(c, mine["id"], H.human_actor("ana"), ago(days=3))
        conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (mine["id"],)).fetchone()[0]
        c.execute("UPDATE messages SET edited_at=?, body='Corrected.' WHERE conversation_id=? AND sender_id=?",
                  (ago(hours=1), conv, H.human_actor("ana")))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert f"{mine['id'][:8]} Check the signage quote: Update you may have missed: a message from " in only_list(c)


def test_a_comment_changed_by_someone_else_after_the_turn_is_news(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Check the signage quote")
        settle(c, mine["id"], "finance", ago(days=2))
        H._task_event(c, mine["id"], H.human_actor("ana"), "comment", "Old wording", "New wording", "Reworded")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_id=? AND kind='comment'", (ago(hours=1), mine["id"]))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert "Update you may have missed: a comment changed by " in only_list(c)


def test_a_listed_task_is_news_only_after_its_listing_and_a_quiet_one_is_listed_again_after_seven_days(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        note(c, idle["id"], H.human_actor("ana"), ago(hours=1))     # news after the turn, before the first list
        complete_jobs(c)
        assert task_roundup.run(c, DAY)["woke"] == ["finance"]
        assert "Update you may have missed: a message from" in only_list(c)
        assert c.execute("SELECT value FROM registry_metadata WHERE key=?",
                         (f"task_roundup:task:{idle['id']}",)).fetchone()[0] == DAY
        # The next day nothing is new, and the task was listed a day ago: no list.
        assert "finance" not in task_roundup.run(c, H.shift(DAY, days=1))["woke"]
        # A message after the listing is news again.
        note(c, idle["id"], H.human_actor("ana"), H.shift(DAY, days=2))
        assert "finance" in task_roundup.run(c, H.shift(DAY, days=2))["woke"]
        assert "Update you may have missed: a message from" in lists(c)[-1]
        # Listed two days ago and quiet since: not listed again before seven days have passed since the listing...
        assert "finance" not in task_roundup.run(c, H.shift(DAY, days=7))["woke"]
        # ...and listed as quiet once they have.
        assert "finance" in task_roundup.run(c, H.shift(DAY, days=9))["woke"]
        assert "Quiet for 7 days, nothing waiting on anyone" in lists(c)[-1]


def test_a_quiet_task_with_no_listing_yet_is_listed_at_once(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        c.execute("INSERT INTO registry_metadata VALUES(?,?)", (f"task_roundup:task:{idle['id']}", ago(days=8)))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert "Quiet for 2 days" in only_list(c)


def test_a_waiting_task_that_waits_on_nothing_is_listed_after_two_days(api):
    store = api.app.state.store
    with store.transaction() as c:
        stranded = task(c, "Confirm the delivery date")
        settle(c, stranded["id"], "finance", ago(days=2))
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (stranded["id"],))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert f"{stranded['id'][:8]} Confirm the delivery date: Waiting on nothing for 2 days" in only_list(c)


def test_a_waiting_task_held_by_a_live_blocker_or_by_a_person_is_not_listed(api):
    store = api.app.state.store
    with store.transaction() as c:
        quote = H.task_create(c, H.human_actor("ana"), "Get the quote", "Ana gets it.", "human:ana")
        van = task(c, "Book the van")
        blocks(c, quote["id"], van["id"], ago(days=2))
        settle(c, van["id"], "finance", ago(days=2))
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (van["id"],))
        landlord = task(c, "Ask the landlord")
        settle(c, landlord["id"], "finance", ago(days=2))
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana' WHERE id=?", (landlord["id"],))
        complete_jobs(c)
        assert "finance" not in task_roundup.run(c, DAY)["woke"]
        assert lists(c) == []


def test_a_private_task_is_listed_by_its_id_without_its_title(api):
    store = api.app.state.store
    with store.transaction() as c:
        private = H.task_create(c, H.human_actor("ana"), "Confidential merger plan", "Keep it quiet.", "bot:finance",
                                lint=False, private=True)
        settle(c, private["id"], "finance", ago(days=2))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"- a private task {private['id'][:8]}: Quiet for 2 days, nothing waiting on anyone" in body
        assert "Confidential merger plan" not in body


def test_the_scheduler_reads_every_list_then_sends_each_bot_in_its_own_transaction_that_rechecks_the_run(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        complete_jobs(c)
    at = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)      # 06:00 company time: the 6am run, DAY
    with store.transaction() as c:
        [plan] = [p for p in task_roundup.discover(c, DAY) if p["slug"] == "finance"]
    with store.transaction() as c:
        assert task_roundup.send(c, plan) == 1                   # the list names one task
    with store.transaction() as c:
        assert task_roundup.send(c, plan) == 0                   # the run was written by the first send: a stale plan sends nothing
    scheduler.run_task_roundup(store, None, at)                  # the same run, through the scheduler: nothing again
    with store.read_transaction() as c:
        assert len(lists(c)) == 1


def test_a_list_is_rebuilt_from_the_live_database_when_it_is_sent(api):
    store = api.app.state.store
    with store.transaction() as c:
        gone = task(c, "Renew the parking permit")
        settle(c, gone["id"], "finance", ago(days=2))
        complete_jobs(c)
        [plan] = [p for p in task_roundup.discover(c, DAY) if p["slug"] == "finance"]
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (gone["id"],))      # finished after the plan was made
        fresh = task(c, "Book the dentist")
        settle(c, fresh["id"], "finance", ago(days=2))
        complete_jobs(c)
        assert task_roundup.send(c, plan) == 1
        body = only_list(c)
        assert fresh["id"][:8] in body and gone["id"][:8] not in body


def test_a_bot_that_asked_for_a_task_is_woken_once_when_the_bot_that_owns_it_finishes_it(api):
    store = api.app.state.store
    with store.transaction() as c:
        asked = H.task_create(c, H.bot_actor("finance"), "Fix the kiosk", "Build.", "bot:ops")
        before = c.execute("SELECT count(*) FROM jobs WHERE bot_id='finance' AND state='queued'").fetchone()[0]
        H.task_update(c, H.bot_actor("ops"), asked["id"], status="done", note="Kiosk is fixed.")
        after = c.execute("SELECT count(*) FROM jobs WHERE bot_id='finance' AND state='queued'").fetchone()[0]
        assert after - before == 1


def test_a_step_set_later_on_a_task_that_had_none_is_news_but_its_creation_row_is_not(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Plan the hike")
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (ago(days=3), mine["id"]))   # created before the step below
        settle(c, mine["id"], "finance", ago(days=2))
        H._task_event(c, mine["id"], H.human_actor("ana"), "step", None, "Draft the route", "Set the step")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_id=? AND kind='step' AND new='Draft the route'",
                  (ago(hours=1), mine["id"]))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert f"{mine['id'][:8]} Plan the hike: Update you may have missed: step changed by " in only_list(c)


def test_a_retained_blocker_that_someone_else_finished_after_the_turn_counts_as_news(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Sign the lease", "Ana signs it.", "human:ana")
        mine = task(c, "Hold the warehouse for the lease")
        blocks(c, blocker["id"], mine["id"], ago(days=3))
        settle(c, mine["id"], "finance", ago(days=3))
        complete_jobs(c)
        c.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (ago(hours=2), blocker["id"]))
        H.history_event(c, "task", blocker["id"], H.human_actor("ana"), "status", "doing", "done", "")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? AND kind='status'",
                  (ago(hours=2), blocker["id"]))
        # Nobody took the blocker off, so the relation is still there: the roundup counts it on the task it blocks.
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert (f"{mine['id'][:8]} Hold the warehouse for the lease: Update you may have missed: a blocker is done "
                "(1 since you last worked on it)") in only_list(c)


def test_a_bot_reached_only_through_an_external_inbox_is_sent_its_list_once_with_no_job(api):
    store = api.app.state.store
    with store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='finance'").fetchone()[0])
        config["harness"] = "hermes"                  # an external agent with no registered computer to deliver its jobs
        c.execute("UPDATE bot_config SET config=? WHERE bot='finance'", (encode(config),))
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert "Quiet for 2 days" in only_list(c)
        assert c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='finance' "
                         "AND m.refs LIKE '%task_roundup%'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key='task_roundup:finance'").fetchone()[0] == 1
        assert "finance" not in task_roundup.run(c, NOON)["woke"]


def test_an_explicit_follow_up_that_has_come_is_listed_first_once_and_then_cleared(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Chase the quote")
        settle(c, mine["id"], "finance", ago(days=3))
        task_check_backs.set_time(c, H.task(c, mine["id"]), ago(hours=1), "Check whether the quote came in",
                                  actor=H.human_actor("ana"), at=ago(hours=3))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        lines = only_list(c).split("\n")
        assert lines[1].startswith(f"- {mine['id'][:8]} Chase the quote: Follow-up time set by ")
        assert lines[1].endswith("has come: Check whether the quote came in")
        assert H.task(c, mine["id"])["check_back_at"] is None           # cleared once it is listed
        assert "finance" not in task_roundup.run(c, NOON)["woke"]        # listed once: not again


def test_a_follow_up_still_to_come_is_not_listed_and_wakes_nothing_on_its_own(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Chase the quote")
        settle(c, mine["id"], "finance", ago(days=3))
        later = H.shift(DAY, hours=2)
        task_check_backs.set_time(c, H.task(c, mine["id"]), later, "Check the quote", actor=H.human_actor("ana"), at=DAY)
        complete_jobs(c)
        assert "finance" not in task_roundup.run(c, DAY)["woke"]         # not due, and it holds the task: not quiet either
        assert lists(c) == []
        task_check_backs.sweep(c, later)                                  # the time has come: Tico queues nothing for it
        assert c.execute("SELECT count(*) FROM messages WHERE refs LIKE '%check_back%'").fetchone()[0] == 0
        assert H.task(c, mine["id"])["check_back_at"] == later


def test_migration_0065_clears_a_follow_up_nobody_set_and_keeps_one_that_was(api):
    store = api.app.state.store
    migration = importlib.import_module("backend.migrations.0071_explicit_follow_ups")
    with store.transaction() as c:
        system_set = task(c, "Renew the car tax")
        person_set = task(c, "Chase the quote")
        stamp = H.shift(DAY, hours=5)
        for tid in (system_set["id"], person_set["id"]):
            c.execute("UPDATE tasks SET check_back_at=?, check_back_note=? WHERE id=?", (stamp, "Look again", tid))
        H._task_event(c, person_set["id"], H.human_actor("ana"), "check_back_explicit", None, stamp, "Look again")
        migration.up(c)
        assert H.task(c, system_set["id"])["check_back_at"] is None
        assert H.task(c, system_set["id"])["check_back_note"] is None
        assert H.task(c, person_set["id"])["check_back_at"] == stamp
        assert H.task(c, person_set["id"])["check_back_note"] == "Look again"


def test_a_bot_with_no_live_task_is_skipped_before_any_per_task_query(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        finished = task(c, "File the receipts")
        settle(c, finished["id"], "finance", ago(days=2))
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (finished["id"],))
        complete_jobs(c)
        asked = []
        visit = task_roundup._list
        monkeypatch.setattr(task_roundup, "_list", lambda c, slug, at: asked.append(slug) or visit(c, slug, at))
        assert "finance" not in task_roundup.run(c, DAY)["woke"]
        assert "finance" not in asked                                     # no live task: no per-task query at all
        live = task(c, "Plan the offsite")
        settle(c, live["id"], "finance", ago(days=2))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert "finance" in asked


def test_a_bot_whose_tasks_need_no_look_gets_no_message_and_no_sent_mark(api):
    store = api.app.state.store
    with store.transaction() as c:
        fresh = task(c, "Draft the budget")
        settle(c, fresh["id"], "finance", ago(hours=3))          # worked on and changed three hours ago: not quiet, nothing new
        complete_jobs(c)
        assert "finance" not in task_roundup.run(c, DAY)["woke"]
        assert task_roundup.send(c, {"slug": "finance", "at": DAY}) == 0
        assert lists(c) == []
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'task_roundup:%'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM events WHERE action='task_roundup.sent'").fetchone()[0] == 0


def test_a_private_task_names_neither_its_follow_up_who_or_note_nor_what_changed(api):
    store = api.app.state.store
    with store.transaction() as c:
        due = H.task_create(c, H.human_actor("ana"), "Confidential merger plan", "Keep it quiet.", "bot:finance",
                            lint=False, private=True)
        settle(c, due["id"], "finance", ago(days=3))
        task_check_backs.set_time(c, H.task(c, due["id"]), ago(hours=1), "Ask Ben about the merger terms",
                                  actor=H.human_actor("ana"), at=ago(hours=3))
        moved = H.task_create(c, H.human_actor("ana"), "Second confidential errand", "Keep it quiet too.", "bot:finance",
                              lint=False, private=True)
        settle(c, moved["id"], "finance", ago(days=2))
        note(c, moved["id"], H.human_actor("ben"), ago(hours=1))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"- a private task {due['id'][:8]}: Follow-up time has come." in body
        assert f"- a private task {moved['id'][:8]}: Update you may have missed (" in body
        for secret in ("merger", "Ask Ben", "Ana", "Ben", "a message from", "Confidential", "Second confidential"):
            assert secret not in body, secret


def test_an_update_after_the_run_but_before_its_list_is_built_is_listed_once(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        note(c, idle["id"], H.human_actor("ana"), H.shift(DAY, minutes=2))      # after the run's time, before the build
        complete_jobs(c)
        built = H.shift(DAY, minutes=5)
        monkeypatch.setattr(task_roundup, "_built_at", lambda: built)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert "Update you may have missed: a message from" in only_list(c)
        stored = c.execute("SELECT value FROM registry_metadata WHERE key=?", (f"task_roundup:task:{idle['id']}",)).fetchone()
        assert stored[0] == built, "the checkpoint is the build's time, not the run's"
        assert "finance" not in task_roundup.run(c, NOON)["woke"], "the update is in the list already: not news again"
        assert len(lists(c)) == 1


def test_a_list_is_written_to_the_bot_alone_so_no_person_gets_a_delivery_for_it(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")       # finance is Ana's bot: her personal room is where a list used to go
        settle(c, idle["id"], "finance", ago(days=2))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        message_id, conversation_id = c.execute("SELECT id, conversation_id FROM messages WHERE recipient_id=? "
                                                "AND refs LIKE '%task_roundup%'", (H.bot_actor("finance"),)).fetchone()
        assert c.execute("SELECT count(*) FROM conversations WHERE id=? AND participants LIKE '%human:%'",
                         (conversation_id,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM message_deliveries WHERE message_id=? AND recipient LIKE 'human:%'",
                         (message_id,)).fetchone()[0] == 0


def test_a_date_a_snooze_left_behind_holds_nothing_while_a_snooze_in_force_or_an_explicit_follow_up_does(api):
    store = api.app.state.store
    with store.transaction() as c:
        left = task(c, "Renew the parking permit")
        task_check_backs.set_time(c, H.task(c, left["id"]), H.shift(DAY, hours=2), "Look again", at=DAY, system=True)
        settle(c, left["id"], "finance", ago(days=2))            # a date nobody set, as a snooze leaves it: it holds nothing
        snoozed = task(c, "Plan the offsite")
        c.execute("UPDATE tasks SET snoozed_until=?, snoozed_by_id='human:ana' WHERE id=?",
                  (H.shift(DAY, hours=2), snoozed["id"]))
        settle(c, snoozed["id"], "finance", ago(days=2))         # a snooze in force hides the task
        explicit = task(c, "Chase the quote")
        task_check_backs.set_time(c, H.task(c, explicit["id"]), H.shift(DAY, hours=2), "Check the quote",
                                  actor=H.human_actor("ana"), at=DAY)
        settle(c, explicit["id"], "finance", ago(days=2))        # an explicit follow-up still to come holds it
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{left['id'][:8]} Renew the parking permit: Quiet for 2 days, nothing waiting on anyone" in body
        assert snoozed["id"][:8] not in body
        assert explicit["id"][:8] not in body


def test_a_private_subtask_or_blocker_its_bot_cannot_read_is_neither_counted_nor_described(api):
    store = api.app.state.store
    with store.transaction() as c:
        parent = task(c, "Ship the kiosk")                      # public, owned by finance
        settle(c, parent["id"], "finance", ago(days=2))
        child = H.task_create(c, H.human_actor("ben"), "Confidential merger plan", "Keep it quiet.", H.human_actor("ben"),
                              lint=False, private=True)
        blocker = H.task_create(c, H.human_actor("ben"), "Confidential term sheet", "Keep it quiet too.",
                                H.human_actor("ben"), lint=False, private=True)
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'parent','human:ben',?)",
                  (child["id"], parent["id"], ago(days=2)))
        blocks(c, blocker["id"], parent["id"], ago(days=2))
        for hidden in (child, blocker):
            c.execute("UPDATE tasks SET status='done' WHERE id=?", (hidden["id"],))
            H.history_event(c, "task", hidden["id"], H.human_actor("ben"), "status", "doing", "done", "")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?,?) AND new='done'",
                  (ago(hours=1), child["id"], blocker["id"]))
        # A private blocker taken off the public parent by Ben: the removal row names the blocker's id, which Finance cannot read.
        taken = H.task_create(c, H.human_actor("ben"), "Confidential budget memo", "Keep it quiet three.", H.human_actor("ben"),
                              lint=False, private=True)
        H.history_event(c, "task", parent["id"], H.human_actor("ben"), "blocked_by", taken["id"], None, "")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? AND kind='blocked_by'",
                  (ago(hours=1), parent["id"]))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"- {parent['id'][:8]} Ship the kiosk: Quiet for 2 days, nothing waiting on anyone" in body
        for secret in ("subtask", "blocker", "Confidential", "merger", "term sheet", "budget", "taken off", "Ben", "Keep it quiet"):
            assert secret not in body, secret


def test_a_title_that_names_a_person_makes_no_mention_in_the_roundup_so_no_person_gets_a_delivery_for_it(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = H.task_create(c, H.human_actor("ana"), "Ask @ana about the parking permit", "Ask Ana about it.",
                             "bot:finance", lint=False)
        settle(c, idle["id"], "finance", ago(days=2))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert "Ask @ana about the parking permit" in only_list(c)
        message_id = c.execute("SELECT id FROM messages WHERE recipient_id=? AND refs LIKE '%task_roundup%'",
                               (H.bot_actor("finance"),)).fetchone()[0]
        assert c.execute("SELECT count(*) FROM message_mentions WHERE message_id=?", (message_id,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM message_deliveries WHERE message_id=? AND recipient LIKE 'human:%'",
                         (message_id,)).fetchone()[0] == 0
