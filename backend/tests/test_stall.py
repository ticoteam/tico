"""The stall watcher (hubdb.wake_stalled; Ana, 2026-09-27: "ideally never stuck for more than 5
minutes"): a bot task nothing is going to move wakes its bot, spaced and capped, and BotOps takes
the ones waking does not move. Waiting, busy, blocked and routine-covered work is left alone."""
import json

import pytest

from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def aged(c, task_id, minutes):
    c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.shift(H.now(), seconds=-minutes * 60), task_id))


def wakes(c, task_id):
    return c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:finance' AND refs_json LIKE ? "
                     "AND refs_json LIKE '%stalled%'", (f'%{task_id}%',)).fetchone()[0]


def test_a_task_nothing_will_move_wakes_its_bot_within_minutes_and_botops_takes_the_stubborn_ones(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        stuck = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance")
        waiting = H.task_create(c, H.human_actor("ana"), "Wait for the bank", "Reply due Monday.", "bot:finance")
        later = H.task_create(c, H.human_actor("ana"), "Next time you run", "No hurry.", "bot:finance", next_run=True)
        H.task_update(c, "bot:finance", waiting["id"], status="waiting", note="The bank replies Monday.")
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")        # the creation wakes ran
        for t in (stuck, waiting, later):
            aged(c, t["id"], 6)
        # Six minutes quiet, nothing queued: the bot is woken on it; waiting work and a fresh
        # next-run task are left alone.
        out = H.wake_stalled(c)
        assert out["woke"] == [stuck["id"]] and wakes(c, stuck["id"]) == 1
        assert wakes(c, waiting["id"]) == 0 and wakes(c, later["id"]) == 0
        # The wake queued a run: nothing more while the bot is busy.
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='finance' AND state='queued'").fetchone()[0] >= 1
        assert H.wake_stalled(c)["woke"] == []
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")
        # Not again within half an hour...
        assert H.wake_stalled(c)["woke"] == []
        # ...a next-run task gets half an hour before its first wake.
        aged(c, later["id"], 31)
        assert H.wake_stalled(c)["woke"] == [later["id"]]
        assert c.execute("SELECT next_run FROM tasks WHERE id=?", (later["id"],)).fetchone()[0] == 0
        # Three wakes in a day that did not move it: BotOps gets one task, and the bot is left be.
        c.execute("UPDATE events SET ts=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(H.now(), seconds=-3600), stuck["id"]))
        for _ in range(2):
            c.execute("INSERT INTO events(id,ts,actor,action,target,detail_json) VALUES(lower(hex(randomblob(8))),?,?,?,?,'{}')",
                      (H.shift(H.now(), seconds=-3600), H.KEEPER, "task.stall_wake", stuck["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")
        aged(c, stuck["id"], 120)                # it has not moved since before those wakes
        out = H.wake_stalled(c)
        assert out["escalated"] == [stuck["id"]] and wakes(c, stuck["id"]) == 1
        assert c.execute("SELECT count(*) FROM tasks WHERE owner='bot:botops' AND title LIKE 'Find why finance%'").fetchone()[0] == 1
        assert H.wake_stalled(c)["escalated"] == []                              # once a day



def capped(c, task):
    aged(c, task['id'], 120)
    for _ in range(H.STALL_WAKES_PER_DAY):
        H.event(c, H.KEEPER, 'task.stall_wake', task['id'], {})
    c.execute("UPDATE events SET ts=? WHERE action='task.stall_wake' AND target=?",
              (H.shift(H.now(), hours=-1), task['id']))


def test_no_diagnostic_is_filed_about_a_diagnostic_or_for_a_person_and_the_stuck_task_says_why(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        own = H.task_create(c, 'bot:finance', 'Repair the mail registry', '', 'bot:botops')
        stuck = H.task_create(c, 'human:ben', 'Check the bank balance', '', 'bot:finance')
        for task in (own, stuck):
            capped(c, task)
        c.execute("UPDATE jobs SET state='completed'")
        assert H.wake_stalled(c)['escalated'] == [own['id'], stuck['id']]
        diagnostics = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE requester='keeper'")]
        # BotOps's own stuck work gets no diagnostic of its own; the finance task gets one, for BotOps.
        assert [(d['owner'], stuck['id'] in d['body']) for d in diagnostics] == [('bot:botops', True)]
        assert c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:finance' "
                         "AND body LIKE '%BotOps cannot diagnose itself%'").fetchone()[0] == 1
        # The diagnostic sticks too: it closes with the cause, and nothing is filed about it.
        capped(c, diagnostics[0])
        c.execute("UPDATE jobs SET state='completed'")
        assert H.wake_stalled(c)['escalated'] == [diagnostics[0]['id']]
        closed = H.task(c, diagnostics[0]['id'])
        assert closed['status'] == 'closed' and 'did not move after 3 wake-ups' in closed['note']
        assert c.execute("SELECT count(*) FROM tasks WHERE requester='keeper'").fetchone()[0] == 1
        # BotOps away: no person is handed a bot's repair; the task carries the cause for its requester.
        c.execute("UPDATE bots SET state='paused' WHERE slug='botops'")
        later = H.task_create(c, 'human:ben', 'Check the card balance', '', 'bot:finance')
        capped(c, later)
        c.execute("UPDATE jobs SET state='completed'")
        assert H.wake_stalled(c)['escalated'] == [later['id']]
        assert c.execute("SELECT count(*) FROM tasks WHERE requester='keeper'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM messages WHERE to_actor='human:ben' AND body LIKE '%BotOps is paused%'"
                         ).fetchone()[0] == 1
        assert H.task(c, later['id'])['status'] == 'open'





def test_a_chat_task_gets_its_own_room_reports_once_and_a_quiet_session_is_checked_then_flagged(api):
    from backend.tests.test_api import headers, post
    room = post(api, "chat/finance", {"text": "Can you look at cash?"})["conversation_id"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        lines = lambda: [(r[0], json.loads(r[1])) for r in c.execute(
            "SELECT body,refs_json FROM messages WHERE conversation_id=? AND from_actor=? ORDER BY created", (room, H.KEEPER))]
        task = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance",
                             conversation_id=room)
        assert task["conversation_id"] != room and H.conversation(c, task["conversation_id"])["kind"] == "task"
        assert [(b, r.get("task_started")) for b, r in lines()] == [("Started task: Recheck the cash warning", True)]
        # Its session ran 40 minutes ago and nothing moved since: one status check, no stall wake (it is busy).
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created) VALUES('r1','r','ana','h','t')")
        job = c.execute("SELECT id FROM jobs WHERE bot='finance' ORDER BY created DESC LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,lane) "
                  "VALUES('a1',?,'finance','r1',1,'a1','running',?,?,?)",
                  (job, H.shift(H.now(), seconds=600), H.shift(H.now(), seconds=-2400), "task:" + task["id"]))
        aged(c, task["id"], 40)
        out = H.wake_stalled(c)
        assert out["checked"] == [task["id"]] and out["woke"] == [] and out["stuck"] == []
        assert H.wake_stalled(c)["checked"] == []                       # once per quiet stretch
        # Half an hour after the check, still nothing: its bot is retried once.
        c.execute("UPDATE events SET ts=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(H.now(), seconds=-1900), task["id"]))
        out = H.wake_stalled(c)
        assert out["retried"] == [task["id"]] and out["stuck"] == []
        assert H.wake_stalled(c)["retried"] == [] and H.wake_stalled(c)["stuck"] == []     # once, then it waits
        # Half an hour after the retry, still nothing: reported stuck in the main chat, and BotOps (not Ana) finds why.
        c.execute("UPDATE events SET ts=? WHERE action='task.stuck_retry' AND target=?",
                  (H.shift(H.now(), seconds=-1900), task["id"]))
        assert H.wake_stalled(c)["stuck"] == [task["id"]]
        assert lines()[-1][1]["task_report"] == "stuck" and lines()[-1][0].startswith("Recheck the cash warning: stuck.")
        assert c.execute("SELECT count(*) FROM messages WHERE kind='ask' AND to_actor LIKE 'human:%'").fetchone()[0] == 0
        diagnostic = c.execute("SELECT owner,body FROM tasks WHERE requester='keeper' AND title LIKE 'Find why %'").fetchall()
        assert [(d[0], task["id"] in d[1]) for d in diagnostic] == [("bot:botops", True)]
        assert H.wake_stalled(c)["stuck"] == []
        # Done: one report line; the same state is not reported twice.
        H.task_update(c, "bot:finance", task["id"], status="done", note="Cash is fine.")
        H.task_report(c, H.task(c, task["id"]), "done", "Again.")
        assert [b for b, r in lines() if r.get("task_report") == "done"] == ["Recheck the cash warning: done. Cash is fine."]


def test_closing_what_the_bot_already_finished_does_not_wake_it_unless_the_note_says_something_new(api):
    with api.app.state.store.transaction() as c:
        def closed_wakes(task_id):
            rows = c.execute("SELECT m.id,(SELECT count(*) FROM jobs j WHERE j.message_id=m.id) FROM messages m "
                             "WHERE m.to_actor='bot:finance' AND m.body LIKE 'Closed:%' AND m.refs_json LIKE ?",
                             (f'%{task_id}%',)).fetchall()
            return [r[1] for r in rows]
        ana = H.human_actor("ana")
        same, new = (H.task_create(c, ana, title, "Review it.", "bot:finance") for title in ("Review the Q3 deck", "Review the Q4 deck"))
        for row in (same, new):
            H.task_update(c, "bot:finance", row["id"], status="done", note="Reviewed; two fixes made.")
        H.task_close(c, ana, same["id"], note="Reviewed; two fixes made.")
        H.task_close(c, ana, new["id"], note="Ship the fixes to the board too.")
        assert closed_wakes(same["id"]) == [] and closed_wakes(new["id"]) == [0]     # the new note lands without a run


def test_a_bot_still_in_setup_holds_work_from_bots_in_sight_and_is_not_busy_for_the_stall_check(api):
    """A parked bot starts only a person's chat (execution.py claim). Work a bot or Tico sends it waits for its
    setup: the sender is told, Run now says why, its never-starting job is not "busy", and the stall check puts
    its setup in a person's Needs you once instead of waking it; finishing setup clears that."""
    from backend.tests.test_api import headers, post
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot='finance'")
    created = api.post("/api/v2/tasks", json={"owner": "finance", "title": "Reconcile the October invoices", "body": "Before the close."},
                       headers=headers()).json()
    assert "hasn't finished its setup" in created.get("warning", ""), created
    task = created["task"]
    refused = api.post(f"/api/v2/tasks/{task['id']}/run-now", json={}, headers=headers())
    assert refused.status_code >= 400 and "hasn't finished its setup" in refused.text
    with store.transaction() as c:
        c.execute("UPDATE tasks SET requester='bot:ops' WHERE id=?", (task["id"],))   # a bot asked for it
        secret = H.task_create(c, "human:cara", "Review the salary bands", "Only for Cara.", "bot:finance", private=True)
        assert not H.task_private_readable(c, "human:ana", H.task(c, secret["id"]))
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='finance' AND state='queued'").fetchone()[0] == 2
        aged(c, task["id"], 6)
        assert [r["id"] for r in H.stalled_tasks(c)] == [task["id"]], "a job that never starts is not busy"
        assert H.wake_stalled(c)["held"] == [task["id"]]
        assert wakes(c, task["id"]) == 0
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='finance' AND state='queued'").fetchone()[0] == 2
        told = c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:ops' AND refs_json LIKE '%setup_held%'").fetchone()[0]
        assert told == 1, "the bot that asked is told why"
        reminder = [t for t in H.needs_you(c, "ana")["tasks"] if t["title"].startswith("Finish setting up finance")]
        assert len(reminder) == 1 and task["id"] in reminder[0]["body"]
        assert "salary" not in reminder[0]["body"] and secret["id"] not in reminder[0]["body"]
        assert "1 you can't see" in reminder[0]["body"], "a private task is only counted"
        aged(c, task["id"], 60)
        assert H.wake_stalled(c)["held"] == [], "once per task"
    post(api, "bots/finance/onboarded", {})
    with store.read() as c:
        assert H.task(c, reminder[0]["id"])["status"] == "done"
