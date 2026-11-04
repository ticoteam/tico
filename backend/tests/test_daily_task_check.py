"""The daily task check (backend/daily_task_check.py): once a UTC day each active bot gets at most one wake, with one list of
its tasks that have a missed update or have gone quiet. A task waiting on a person or a live blocker with nothing new
is not listed, a task waiting on nothing is listed once quiet a day, Tico's own notices and check-backs are not news,
a task with a run already queued is not listed, and a bot with nothing to list gets no message.
Synthetic fixtures only."""
from backend import daily_task_check
from backend.store import H
from backend.tests.test_api import api  # noqa: F401

DAY = "2026-11-03T14:00:00.000000Z"     # a fixed UTC day, so the tests do not depend on when they run


def ago(**kw):
    return H.shift(DAY, **{k: -v for k, v in kw.items()})


def task(c, title):
    return H.task_create(c, H.human_actor("ana"), title, f"{title}, for the daily check.", "bot:finance")


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
    return [row[0] for row in c.execute("SELECT body FROM messages WHERE recipient_id=? AND refs LIKE '%daily_task_check%' "
                                        "ORDER BY created_at", (H.bot_actor(slug),)).fetchall()]


def only_list(c, slug="finance"):
    found = lists(c, slug)
    assert len(found) == 1, found
    return found[0]


def test_a_task_blocked_on_a_person_with_nothing_new_is_not_listed_until_the_blocker_is_closed_before_the_turn(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Sign the lease", "Ana signs it.", "human:ana")
        mine = task(c, "Hold the warehouse for the lease")
        blocks(c, blocker["id"], mine["id"], ago(days=3))
        settle(c, mine["id"], "finance", ago(days=3))
        complete_jobs(c)
        # The blocker is still open: the bot waits on Ana, and nothing has changed since its turn.
        assert "finance" not in daily_task_check.run(c, DAY)["woke"]
        assert lists(c) == []
        # Closed a day before the bot's turn: nothing is missed, but the task is quiet and nothing waits on anyone.
        c.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (ago(days=4), blocker["id"]))
        assert "finance" in daily_task_check.run(c, DAY)["woke"]
        assert f"{mine['id'][:8]} Hold the warehouse for the lease: Quiet for 3 days" in only_list(c)


def test_a_blocker_closed_after_the_bots_last_turn_is_listed_as_a_missed_update(api):
    store = api.app.state.store
    with store.transaction() as c:
        blocker = H.task_create(c, H.human_actor("ana"), "Sign the lease", "Ana signs it.", "human:ana")
        mine = task(c, "Hold the warehouse for the lease")
        blocks(c, blocker["id"], mine["id"], ago(days=3))
        settle(c, mine["id"], "finance", ago(days=3))
        complete_jobs(c)
        c.execute("UPDATE tasks SET status='done', updated_at=? WHERE id=?", (ago(hours=2), blocker["id"]))
        assert "finance" in daily_task_check.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{mine['id'][:8]} Hold the warehouse for the lease: Update you may have missed: a blocker is done" in body


def test_an_idle_open_task_untouched_two_days_is_listed_as_quiet_and_a_recent_one_is_not(api):
    store = api.app.state.store
    with store.transaction() as c:
        idle = task(c, "Renew the parking permit")
        settle(c, idle["id"], "finance", ago(days=2))
        recent = task(c, "Book the dentist")
        settle(c, recent["id"], "finance", ago(hours=12))
        complete_jobs(c)
        assert "finance" in daily_task_check.run(c, DAY)["woke"]
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
        assert "finance" in daily_task_check.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{idle['id'][:8]} Plan the offsite" in body
        assert queued["id"][:8] not in body


def test_a_bot_with_nothing_to_list_gets_no_message(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('idlebot','idlebot','Idle Bot','active')")
        complete_jobs(c)
        out = daily_task_check.run(c, DAY)
        assert "idlebot" not in out["woke"] and lists(c, "idlebot") == []
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key='daily_task_check:idlebot'").fetchone()[0] == 0


def test_two_runs_on_one_utc_day_give_one_message_and_a_list_names_at_most_25_tasks(api):
    store = api.app.state.store
    with store.transaction() as c:
        for n in range(30):
            settle(c, task(c, f"Old errand {n:02d}")["id"], "finance", ago(days=2))
        complete_jobs(c)
        first = daily_task_check.run(c, DAY)
        again = daily_task_check.run(c, H.shift(DAY, hours=3))         # the same UTC day, later
        assert "finance" in first["woke"] and "finance" not in again["woke"]
        body = only_list(c)
        lines = body.split("\n")
        named = [line for line in lines if line.startswith("- ") and not line.startswith("- and ")]
        assert len(named) == 25 and lines[-1] == "- and 5 more"
        assert lines[0] == "Daily task check: 30 of your tasks need a look. Work each one, or set it waiting on whoever must act."
        assert c.execute("SELECT count(*) FROM events WHERE action='daily_task_check.sent'").fetchone()[0] == 1
        # The next UTC day the list is sent again, while the tasks are still quiet.
        assert "finance" in daily_task_check.run(c, H.shift(DAY, days=1))["woke"]
        assert len(lists(c)) == 2


def test_tico_notices_and_unsent_messages_after_the_turn_are_not_news_and_do_not_reset_quiet(api):
    store = api.app.state.store
    with store.transaction() as c:
        mine = task(c, "Renew the car insurance")
        settle(c, mine["id"], "finance", ago(days=3))
        note(c, mine["id"], H.KEEPER, ago(days=1))      # Tico's wake notice, a day after the turn
        note(c, mine["id"], None, ago(days=1))          # a message with no sender
        complete_jobs(c)
        assert "finance" in daily_task_check.run(c, DAY)["woke"]
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
        assert "finance" in daily_task_check.run(c, DAY)["woke"]
        body = only_list(c)
        assert f"{plants['id'][:8]} Water the office plants: Quiet for 3 days, nothing waiting on anyone" in body
        assert (f"{moved['id'][:8]} File the expense report: Update you may have missed: status changed by Tico "
                "(1 since you last worked on it)") in body


def test_a_waiting_task_that_waits_on_nothing_is_listed_after_two_days(api):
    store = api.app.state.store
    with store.transaction() as c:
        stranded = task(c, "Confirm the delivery date")
        settle(c, stranded["id"], "finance", ago(days=2))
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (stranded["id"],))
        complete_jobs(c)
        assert "finance" in daily_task_check.run(c, DAY)["woke"]
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
        assert "finance" not in daily_task_check.run(c, DAY)["woke"]
        assert lists(c) == []
