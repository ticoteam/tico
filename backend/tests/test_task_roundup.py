"""The task roundup (backend/task_roundup.py): each run (three times a day by default, at 6am, noon and 6pm company time) gives
each active bot at most one wake, with one list of its tasks that have a missed update, have gone quiet, have a follow-up
time that has come, or need a look for a late ticket, a backlog, a question or a restate. A task waiting on a person or a live blocker with nothing new is not listed, a task waiting on nothing is
listed once quiet a day, Tico's own notices and check-backs are not news, a task with a run already queued is not listed, a
bot with nothing to list gets no message, a private task is named without its title or any detail of it, a list reaches the
bot alone, and a quiet task is listed again only after seven days. Synthetic fixtures only."""
import importlib
import json
from datetime import datetime, timezone

import pytest

from backend import bot_settings as BS
from backend.enqueue import enqueue_stored
from backend import scheduler, still_needed, task_check_backs, task_roundup, team_rules
from backend import step_backlog as SB
from backend import task_holds as TH
from backend import task_relations as TR
from backend.store import H
from backend.tests.hold_rows import set_holds
from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_dev_ticket_rules import dev_type
from backend.tests.test_step_backlog import board as backlog_board, tickets as backlog_tickets

DAY = "2026-11-03T14:00:00.000000Z"     # the 6am run (company time is Pacific, PST on this date); fixed for the tests
NOON = H.shift(DAY, hours=6)            # the noon run, 20:00 UTC


@pytest.fixture(autouse=True)
def build_clock(monkeypatch):
    """A list is built at its run's time unless a test moves the build clock, so the checkpoints the tests read are the runs'."""
    monkeypatch.setattr(task_roundup, "_built_at", lambda: DAY)


def ago(**kw):
    return H.shift(DAY, **{k: -v for k, v in kw.items()})


def task(c, title):
    """Created at the fixed DAY clock, so the runs' cutoffs do not depend on the real date."""
    made = H.task_create(c, H.human_actor("ana"), title, f"{title}, for the roundup.", "bot:finance")
    c.execute("UPDATE tasks SET created_at=? WHERE id=?", (DAY, made["id"]))
    return made


def settle(c, tid, slug, turned):
    """The task as it stood before its bot's last turn on it at `turned`: its own row, and its messages and history a day
    older, so nothing has happened on it since the turn."""
    conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (turned, tid))
    c.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (H.shift(turned, days=-1), conv))
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
              (H.shift(turned, days=-1), tid))
    c.execute("INSERT INTO attempts(id,bot_id,created_at,settled_at,trigger,message_id,task_id,exit,summary) "
              "VALUES(?,?,?,?,'schedule',NULL,?,'done','')", (H.new_id(), slug, turned, turned, tid))
    started(c, tid, slug, turned)                  # the turn was a run of the bot on the task


def started(c, tid, slug, when):
    """A run of the bot that carried the task at `when`: its attempt makes the task started, so it is not an assignment the
    bot has yet to start (backend/assigned_start.py). The message that started the run is written before the turn."""
    c.execute("INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r-roundup','box','ana','h',?)",
              (when,))
    conv = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    # Created a day before the turn, as settle dates its history: the task's assignment floor is its creation.
    c.execute("UPDATE tasks SET created_at=? WHERE id=?", (H.shift(when, days=-1), tid))
    mid = H.new_id()
    c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
              "VALUES(?,?,?,?,'task','',?,?)",
              (mid, conv, H.human_actor("ana"), H.bot_actor(slug), json.dumps({"task": tid}), H.shift(when, days=-1)))
    enqueue_stored(c, mid)
    if not c.execute("SELECT 1 FROM jobs WHERE message_id=?", (mid,)).fetchone():
        # An external inbox gets no job (backend/enqueue.py): its run still needs one to attach to.
        c.execute("INSERT INTO jobs(id,message_id,bot_id,state,created_at) VALUES(?,?,?,'completed',?)",
                  (mid, mid, slug, when))
    job = c.execute("SELECT id FROM jobs WHERE message_id=?", (mid,)).fetchone()[0]
    c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job,))
    c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
              "VALUES(?,?,?,'r-roundup',1,?,'completed',?,?)", (H.new_id(), job, slug, H.new_id(), when, when))


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
        # A waiting task on a person with a plain ask is held by that person: a note with no plain ask is a restate line instead
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?",
                  ("Send the landlord the lease copy.", landlord["id"]))
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
        BS.update(c, "finance", {"harness": "hermes"})   # an external agent with no registered computer to deliver its jobs
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
        assert "finance" in task_roundup.run(c, later)["woke"]           # the time has come: the roundup lists it, once
        assert only_list(c).split("\n")[1].startswith(f"- {mine['id'][:8]} Chase the quote: Follow-up time set by ")
        assert c.execute("SELECT count(*) FROM messages WHERE refs LIKE '%check_back%'").fetchone()[0] == 0
        assert H.task(c, mine["id"])["check_back_at"] is None           # consumed by its listing


def test_migration_0071_clears_a_follow_up_nobody_set_and_keeps_one_that_was(api):
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
        monkeypatch.setattr(task_roundup, "_list", lambda c, slug, at, shared: asked.append(slug) or visit(c, slug, at, shared))
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
        set_holds(c, snoozed["id"], snoozed_until=H.shift(DAY, hours=2), snoozed_by_id="human:ana")
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


def test_a_late_ticket_is_one_line_on_its_managers_list_with_no_wake_of_its_own_and_is_relisted_after_seven_days(api):
    store = api.app.state.store
    with store.transaction() as c:
        typ = dev_type(c)
        c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
        row = H.task_create(c, H.human_actor("ana"), "Fix the connection", "Build.", "bot:cpo", type=typ["id"],
                            step="In Progress", lint=False)
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (ago(hours=5), row["id"]))
        c.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE id=?", (ago(hours=5), ago(hours=5), row["id"]))
        assert "coo" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c, "coo")
        assert named_in(body) == 1 and f"- {row['id'][:8]} Fix the connection: Late in In Progress: 5 h" in body
        assert c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:coo' AND json_extract(refs,'$.task')=?",
                         (row["id"],)).fetchone()[0] == 0, "no wake of its own: the list is its notice"
        assert "coo" not in task_roundup.run(c, NOON)["woke"], "not listed again before seven days"
        assert "coo" in task_roundup.run(c, H.shift(DAY, days=7))["woke"]
        assert "Late in In Progress: 7 days" in lists(c, "coo")[-1]


def queued_roundups(c, slug):
    return c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND j.state='queued' "
                     "AND json_extract(m.refs,'$.wake')='task_roundup'", (slug,)).fetchone()[0]


def late_manager_ticket(c):
    typ = dev_type(c)
    c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
    row = H.task_create(c, H.human_actor("ana"), "Fix the connection", "Build.", "bot:cpo", type=typ["id"],
                        step="In Progress", lint=False)
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (ago(hours=5), row["id"]))
    c.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE id=?", (ago(hours=5), ago(hours=5), row["id"]))
    return row


def test_a_manager_whose_list_has_only_a_late_line_gets_one_queued_list_per_run(api):
    with api.app.state.store.transaction() as c:
        late_manager_ticket(c)
        assert "coo" in task_roundup.run(c, DAY)["woke"]
        assert queued_roundups(c, "coo") == 1 and len(lists(c, "coo")) == 1, "one list, one queued wake"
        assert "coo" not in task_roundup.run(c, NOON)["woke"], "the late line is not listed again"
        assert len(lists(c, "coo")) == 1


def test_a_list_held_as_a_note_is_not_sent_and_its_lines_are_listed_again_next_run(api, monkeypatch):
    from backend import rules
    real = rules.judge

    def held(conn, actor, target, kind, refs, body, conv, in_reply_to=None, decided=None):
        if (refs or {}).get("wake") == "task_roundup":
            return rules.Decision(rules.NOTE, rules.MANAGER_NOTE, rules.HOLD_ROW)
        return real(conn, actor, target, kind, refs, body, conv, in_reply_to, decided)

    with api.app.state.store.transaction() as c:
        late_manager_ticket(c)
        monkeypatch.setattr(rules, "judge", held)
        assert "coo" not in task_roundup.run(c, DAY)["woke"], "a held note is not a sent list"
        monkeypatch.setattr(rules, "judge", real)
        assert "coo" in task_roundup.run(c, NOON)["woke"], "its line was not consumed: it is listed again next run"
        assert "Late in In Progress" in lists(c, "coo")[-1]


def test_a_late_ticket_its_manager_cannot_read_is_not_listed(api):
    store = api.app.state.store
    with store.transaction() as c:
        typ = dev_type(c)
        c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
        row = H.task_create(c, H.human_actor("ana"), "Fix the private connection", "Build.", "bot:cpo", type=typ["id"],
                            step="In Progress", lint=False, private=True)
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (ago(hours=5), row["id"]))
        c.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE id=?", (ago(hours=5), ago(hours=5), row["id"]))
        assert not H.task_private_readable(c, "bot:coo", H.task(c, row["id"]))
        assert "coo" not in task_roundup.run(c, DAY)["woke"]
        assert lists(c, "coo") == []


def test_a_manager_with_a_late_ticket_and_an_over_backlog_gets_one_list_with_both_lines_in_order(api):
    store = api.app.state.store
    with store.transaction() as c:
        typ = dev_type(c)
        c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
        late = H.task_create(c, H.human_actor("ana"), "Fix the connection", "Build.", "bot:cpo", type=typ["id"],
                             step="In Progress", lint=False)
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (ago(hours=5), late["id"]))
        c.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE id=?", (ago(hours=5), ago(hours=5), late["id"]))
        backlog_tickets(c, backlog_board(c, {"over": 3, "notify": ["manager"]}), "bot:ops", 4)
        assert "coo" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c, "coo")
        assert named_in(body) == 2
        assert body.index("Late in In Progress: 5 h") < body.index("Backlog: 4 tickets wait at Inbox")


def test_a_step_over_its_backlog_is_one_line_on_its_oldest_ticket_for_its_manager_once_per_every_hours(api):
    store = api.app.state.store
    with store.transaction() as c:
        typ = backlog_board(c, {"over": 3, "notify": ["manager"], "every_hours": 4})
        backlog_tickets(c, typ, "bot:ops", 4)
        first = SB.warnings(c)[0]["first"]
        assert "coo" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c, "coo")
        assert f"- {first['id'][:8]} " in body and "Backlog: 4 tickets wait at Inbox (over 3; ops 4)." in body
        assert "coo" not in task_roundup.run(c, H.shift(DAY, hours=3))["woke"], "not again before every_hours"
        assert "coo" in task_roundup.run(c, NOON)["woke"], "again after every_hours"
        assert sum("Backlog: 4 tickets" in found for found in lists(c, "coo")) == 2


def test_a_backlog_named_to_a_person_reaches_no_person_through_the_roundup(api):
    store = api.app.state.store
    with store.transaction() as c:
        typ = backlog_board(c, {"over": 3, "notify": ["manager", "human:ana"]})
        backlog_tickets(c, typ, "bot:ops", 4)
        assert "coo" in task_roundup.run(c, DAY)["woke"]
        assert named_in(only_list(c, "coo")) == 1
        assert c.execute("SELECT count(*) FROM messages WHERE recipient_id='human:ana' AND body LIKE '%Backlog:%'"
                         ).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM message_deliveries WHERE recipient LIKE 'human:%' AND message_id IN "
                         "(SELECT id FROM messages WHERE refs LIKE '%task_roundup%')").fetchone()[0] == 0


def test_a_backlog_of_snoozed_tickets_lists_nothing_for_its_owner_and_queues_no_roundup_job(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: DAY)
    store = api.app.state.store
    with store.transaction() as c:
        typ = backlog_board(c, {"over": 3, "per": "owner", "notify": ["owner"]})
        for ticket in backlog_tickets(c, typ, "bot:ops", 4):
            H.TS.snooze(c, "human:ana", ticket["id"], H.shift(DAY, days=2))
        assert task_roundup.run(c, DAY) == {"woke": [], "listed": 0}
        assert lists(c, "ops") == []
        assert c.execute("SELECT count(*) FROM jobs WHERE message_id IN (SELECT id FROM messages WHERE refs LIKE "
                         "'%task_roundup%')").fetchone()[0] == 0


def test_a_backlog_of_snoozed_tickets_still_lists_for_its_manager(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: DAY)
    store = api.app.state.store
    with store.transaction() as c:
        typ = backlog_board(c, {"over": 3, "per": "owner", "notify": ["manager"]})
        for ticket in backlog_tickets(c, typ, "bot:ops", 4):
            H.TS.snooze(c, "human:ana", ticket["id"], H.shift(DAY, days=2))
        assert "coo" in task_roundup.run(c, DAY)["woke"]
        assert "Backlog: 4 tickets wait at Inbox" in only_list(c, "coo")
        assert lists(c, "ops") == []


def test_a_waiting_task_whose_ask_to_a_person_is_not_a_plain_ask_is_restated_to_its_bot_alone(api):
    store = api.app.state.store
    long_ask = "Please send " + "the signed lease copy " * 12 + "today."
    with store.transaction() as c:
        mine = task(c, "Renew the lease")
        settle(c, mine["id"], "finance", ago(days=2))
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?", (long_ask, mine["id"]))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert (f"- {mine['id'][:8]} Renew the lease: Waiting on ana with no plain ask. "
                "Restate it in one or two plain sentences") in only_list(c)
        assert c.execute("SELECT count(*) FROM message_deliveries WHERE recipient LIKE 'human:%' AND message_id IN "
                         "(SELECT id FROM messages WHERE refs LIKE '%task_roundup%')").fetchone()[0] == 0
        assert "finance" not in task_roundup.run(c, NOON)["woke"], "not relisted before seven days"
        assert "finance" in task_roundup.run(c, H.shift(DAY, days=7))["woke"]
        assert "Waiting on ana with no plain ask" in lists(c)[-1]


def test_a_private_waiting_task_restated_names_no_person_and_no_title(api):
    store = api.app.state.store
    long_ask = "Please send " + "the signed lease copy " * 12 + "today."
    with store.transaction() as c:
        private = H.task_create(c, H.human_actor("ana"), "Confidential merger plan", "Keep it quiet.", "bot:finance",
                                lint=False, private=True)
        settle(c, private["id"], "finance", ago(days=2))
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?", (long_ask, private["id"]))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        body = only_list(c)
        assert (f"- a private task {private['id'][:8]}: Waiting on a person with no plain ask. Restate it, at most "
                f"{H.LINT_ASK_WORDS} words, with: hub task update {private['id']} --status waiting --on <person>") in body
        for secret in ("merger", "Confidential", "Ana", "human:ana", "Keep it quiet", "signed lease"):
            assert secret not in body, secret


def test_an_unanswered_question_goes_back_to_the_bot_that_asked_as_one_roundup_line_with_no_wake_and_is_not_resent(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Renew the parking permit")
        settle(c, mine["id"], "finance", ago(days=8))
        question = H.say(c, "bot:finance", "human:ana", "Which permit number do I use?", kind="ask",
                         conversation_id=H.task(c, mine["id"])["conversation_id"], refs={"task": mine["id"]})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (ago(days=8), question["id"]))
        complete_jobs(c)
        before = c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:finance'").fetchone()[0]
        assert still_needed.sweep(c, H.shift(DAY, hours=3)) == {"bot:finance": [question["id"]]}
        assert c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:finance'").fetchone()[0] == before
        assert still_needed.sweep(c, H.shift(DAY, hours=4)) == {}, "the batch runs once a day"
        assert "finance" in task_roundup.run(c, NOON)["woke"]
        assert (f'- {mine["id"][:8]} Renew the parking permit: no answer in two business days to '
                '"Which permit number do I use?"') in only_list(c)
        task_roundup.run(c, H.shift(NOON, hours=6))
        assert sum("no answer in two business days" in found for found in lists(c)) == 1, "listed once, not resent"


def test_a_still_needed_event_the_base_sent_as_a_message_is_not_relisted_on_upgrade(api):
    """Before the roundup, the weekday batch posted its questions to the bot as a message and recorded that message on the
    `ask.still_needed` event. Those events are already delivered, so an upgrade does not list their questions again."""
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Renew the parking permit")
        settle(c, mine["id"], "finance", ago(days=8))
        conv = H.task(c, mine["id"])["conversation_id"]
        delivered = H.say(c, "bot:finance", "human:ana", "Which permit number do I use?", kind="ask",
                          conversation_id=conv, refs={"task": mine["id"]})
        pending = H.say(c, "bot:finance", "human:ana", "Which renewal fee applies?", kind="ask",
                        conversation_id=conv, refs={"task": mine["id"]})
        for question in (delivered, pending):
            c.execute("UPDATE messages SET created_at=? WHERE id=?", (ago(days=8), question["id"]))
        H.event(c, H.KEEPER, still_needed.ACTION, "bot:finance", {"asks": [delivered["id"]], "message": H.new_id()})
        H.event(c, H.KEEPER, still_needed.ACTION, "bot:finance", {"asks": [pending["id"]]})
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, NOON)["woke"]
        found = " ".join(lists(c))
        assert "Which renewal fee applies?" in found, "a question the roundup's own event names is listed"
        assert "Which permit number do I use?" not in found, "a question already sent as a message is not relisted"


def test_a_list_cut_at_its_cap_keeps_its_group_order_and_counts_the_rest(api, monkeypatch):
    store = api.app.state.store
    monkeypatch.setattr(task_roundup, "MAX_LINES", 2)
    with store.transaction() as c:
        recent = task(c, "Old errand one")
        settle(c, recent["id"], "finance", ago(days=2))
        oldest = task(c, "Old errand two")
        settle(c, oldest["id"], "finance", ago(days=3))
        due = task(c, "Chase the quote")
        settle(c, due["id"], "finance", ago(days=3))
        task_check_backs.set_time(c, H.task(c, due["id"]), ago(hours=1), "Check the quote",
                                  actor=H.human_actor("ana"), at=ago(hours=3))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        lines = only_list(c).split("\n")
        assert lines[1].startswith(f"- {due['id'][:8]} Chase the quote: Follow-up time set by ")
        assert lines[2].startswith(f"- {oldest['id'][:8]} Old errand two: Quiet for 3 days")
        assert lines[3] == "- and 1 more" and len(lines) == 4


def test_a_question_about_a_private_task_asked_from_a_home_room_names_neither_the_task_nor_the_question(api):
    store = api.app.state.store
    with store.transaction() as c:
        private = H.task_create(c, H.human_actor("ana"), "Confidential merger plan", "Keep it quiet.", "bot:finance",
                                lint=False, private=True)
        settle(c, private["id"], "finance", ago(days=8))
        home = H.open_conversation(c, H.human_actor("ana"), [H.human_actor("ana"), H.bot_actor("finance")], kind="chat")
        question = H.say(c, H.bot_actor("finance"), H.human_actor("ana"), "Is the merger price still 40 million?",
                         conversation_id=home["id"], kind="ask", refs={"task": private["id"]})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (ago(days=8), question["id"]))
        complete_jobs(c)
        assert still_needed.sweep(c, H.shift(DAY, hours=3)) == {"bot:finance": [question["id"]]}
        assert "finance" in task_roundup.run(c, NOON)["woke"]
        body = only_list(c)
        assert f"- a private task {private['id'][:8]}: a question to a person had no answer" in body
        for secret in ("merger", "Confidential", "40 million", "Keep it quiet"):
            assert secret not in body, secret


def test_a_late_ticket_its_manager_cannot_read_by_its_owner_bots_access_is_named_nowhere(api):
    from backend import bot_access as A, bot_settings as BS
    store = api.app.state.store
    with store.transaction() as c:
        ticket = late_manager_ticket(c)
        only_ana = A.audience({"people": ["ana"]})
        c.execute("INSERT INTO bot_config(bot,owner_id,access) VALUES('cpo','ana',?) ON CONFLICT(bot) DO UPDATE SET access=excluded.access",
                  (A.stored({"see": A.OPEN_LEVEL, "read": only_ana, "write": only_ana}),))
        BS.write(c, "cpo", {})
        assert not H.bot_reads_task(c, H.bot_actor("coo"), H.task(c, ticket["id"]))
        assert "coo" not in task_roundup.run(c, DAY)["woke"]
        assert lists(c, "coo") == [], "no title, no step, no line"


def test_a_question_about_a_task_its_bot_no_longer_reads_names_neither_the_task_nor_the_question(api):
    from backend import bot_access as A, bot_settings as BS
    store = api.app.state.store
    with store.transaction() as c:
        mine = H.task_create(c, H.human_actor("ana"), "Confidential vendor terms", "Keep it quiet.", "bot:cpo", lint=False)
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (DAY, mine["id"]))
        question = H.say(c, H.bot_actor("finance"), H.human_actor("ana"), "Are the vendor terms still 40 days?",
                         conversation_id=H.task(c, mine["id"])["conversation_id"], kind="ask", refs={"task": mine["id"]})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (ago(days=8), question["id"]))
        complete_jobs(c)
        assert still_needed.sweep(c, H.shift(DAY, hours=3)) == {"bot:finance": [question["id"]]}
        # cpo, the task's bot, now lets only Ana's people read it: finance asked about it while it could.
        only_ana = A.audience({"people": ["ana"]})
        c.execute("INSERT INTO bot_config(bot,owner_id,access) VALUES('cpo','ana',?) ON CONFLICT(bot) DO UPDATE SET access=excluded.access",
                  (A.stored({"see": A.OPEN_LEVEL, "read": only_ana, "write": only_ana}),))
        BS.write(c, "cpo", {})
        assert not H.bot_reads_task(c, H.bot_actor("finance"), H.task(c, mine["id"]))
        assert "finance" not in task_roundup.run(c, NOON)["woke"]
        assert lists(c, "finance") == [], "no title, no question text, no wake"


def test_a_reviewer_is_woken_once_by_its_roundup_and_listed_only_its_returned_question(api):
    from backend import bot_settings as BS
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Renew the parking permit")      # before the profile: a reviewer takes no ordinary task
        BS.write(c, "finance", {"profile": "reviewer"})
        settle(c, mine["id"], "finance", ago(days=8))
        question = H.say(c, "bot:finance", "human:ana", "Which permit number do I use?", kind="ask",
                         conversation_id=H.task(c, mine["id"])["conversation_id"], refs={"task": mine["id"]})
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (ago(days=8), question["id"]))
        complete_jobs(c)
        assert still_needed.sweep(c, H.shift(DAY, hours=3)) == {"bot:finance": [question["id"]]}
        assert "finance" in task_roundup.run(c, NOON)["woke"]
        assert queued_roundups(c, "finance") == 1, "the roundup is one queued wake for a reviewer too"
        body = only_list(c)
        assert "no answer in two business days" in body
        assert named_in(body) == 1 and "Quiet for" not in body, "a reviewer gets no idle or quiet line"


def test_a_missed_start_is_listed_once_for_its_time_and_not_again_on_the_next_run(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Call the venue")["id"]
        settle(c, tid, "finance", ago(days=2))
        H._task_event(c, tid, H.KEEPER, "snoozed_until", ago(hours=5), None, "snooze over")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? AND kind='snoozed_until'",
                  (ago(hours=3), tid))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert f"{tid[:8]} Call the venue: Snooze ended at 11:00 UTC and nothing has picked it up" in only_list(c)
        assert "finance" not in task_roundup.run(c, H.shift(DAY, hours=2))["woke"], "listed once for that time"


def test_a_deferred_time_that_passed_unfired_is_a_missed_start(api):
    store = api.app.state.store
    with store.transaction() as c:
        tid = task(c, "Chase the invoice")["id"]
        settle(c, tid, "finance", ago(days=2))
        conv = H.task(c, tid)["conversation_id"]
        c.execute("INSERT INTO deferrals(id,bot,kind,item,conversation_id,task_id,due_at,created_at,fired_job,settled_at,how,attempt) "
                  "VALUES(?,?,'task',?,?,?,?,?,NULL,NULL,NULL,NULL)",
                  (H.new_id(), "finance", tid, conv, tid, ago(hours=2), ago(hours=4)))
        complete_jobs(c)
        assert "finance" in task_roundup.run(c, DAY)["woke"]
        assert f"{tid[:8]} Chase the invoice: Deferred time passed at 12:00 UTC and nothing has picked it up" in only_list(c)


def test_a_waiting_task_held_only_by_bots_is_listed_on_its_blocker_bots_list_not_its_owners(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Finish the menu", "Ops finishes it.", H.bot_actor("ops"))["id"]
        mine = task(c, "Print the menu")["id"]
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (mine,))
        blocks(c, blocker, mine, ago(days=1))
        complete_jobs(c)
        complete_jobs(c, "ops")
        assert task_roundup.run(c, DAY)["woke"] == ["ops"], "the blocker's bot is listed, not the owner"
        assert f"{mine[:8]} Print the menu: Waiting on you" in only_list(c, "ops")
        assert lists(c) == [], "the owner is not told about its own blocked task"
        assert task_roundup.run(c, DAY)["woke"] == [], "listed once per update of the task"


def test_a_person_owned_waiting_task_never_reaches_task_holds_and_a_bot_owned_one_still_does(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Finish the menu", "Ops finishes it.", H.bot_actor("ops"))["id"]
        person = task(c, "Sign the lease")["id"]
        c.execute("UPDATE tasks SET owner_id=?, status='waiting' WHERE id=?", (H.human_actor("ana"), person))
        blocks(c, blocker, person, ago(days=1))
        mine = task(c, "Print the menu")["id"]
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (mine,))
        blocks(c, blocker, mine, ago(days=1))
        seen = []
        real = TH.holds

        def counting(conn, row):
            seen.append(row["id"])
            return real(conn, row)

        monkeypatch.setattr(TH, "holds", counting)
        waits = task_roundup._blocked_waits(c, DAY)
        assert person not in seen, "a person-owned waiting task is dropped in SQL, before task_holds"
        assert mine in seen, "a bot-owned waiting task of an active bot still reaches task_holds"
        assert [row["id"] for row, _ in waits.get("ops", [])] == [mine]


def test_a_waiting_task_already_listed_for_its_update_is_not_read_by_task_holds_again(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Finish the menu", "Ops finishes it.", H.bot_actor("ops"))["id"]
        mine = task(c, "Print the menu")["id"]
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (mine,))
        blocks(c, blocker, mine, ago(days=1))
        complete_jobs(c)
        complete_jobs(c, "ops")
        assert task_roundup.run(c, DAY)["woke"] == ["ops"]
        seen = []
        real = TH.holds

        def counting(conn, row):
            seen.append(row["id"])
            return real(conn, row)

        monkeypatch.setattr(TH, "holds", counting)
        assert task_roundup._blocked_waits(c, DAY).get("ops", []) == []
        assert mine not in seen, "a task marked as listed for this update is skipped before its holds are read"


def test_a_stale_candidate_the_bot_owns_is_listed_once_while_the_stale_review_rule_is_on(api):
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": True})
        tid = task(c, "Ship the export button")["id"]
        settle(c, tid, "finance", ago(days=9))
        complete_jobs(c)
        assert task_roundup.run(c, DAY)["woke"] == ["finance"]
        body = only_list(c)
        assert f"{tid[:8]} Ship the export button: Stale check: " in body
        assert "Quiet for" not in body, "a task is named once, as its first kind"
        assert task_roundup.run(c, DAY)["woke"] == [], "listed once per update of the task"


def test_with_the_stale_review_rule_off_the_same_task_is_only_quiet(api):
    store = api.app.state.store
    with store.transaction() as c:
        team_rules.save(c, "human:ana", {"stale_review": False})
        tid = task(c, "Ship the export button")["id"]
        settle(c, tid, "finance", ago(days=9))
        complete_jobs(c)
        assert task_roundup.run(c, DAY)["woke"] == ["finance"]
        body = only_list(c)
        assert f"{tid[:8]} Ship the export button: Quiet for 9 days, nothing waiting on anyone" in body
        assert "Stale check" not in body
