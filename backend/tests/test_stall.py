"""The stall watcher (hubdb.wake_stalled; Ana, 2026-09-27: "ideally never stuck for more than 5
minutes"): a bot task nothing is going to move wakes its bot, spaced and capped, and BotOps takes
the ones waking does not move. Waiting, busy, blocked and routine-covered work is left alone."""
import json

import pytest

from backend import milestone_waits as MW
from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def aged(c, task_id, minutes):
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), seconds=-minutes * 60), task_id))


def wakes(c, task_id):
    return c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:finance' AND refs LIKE ? "
                     "AND refs LIKE '%stalled%'", (f'%{task_id}%',)).fetchone()[0]


def test_a_task_nothing_will_move_wakes_its_bot_within_minutes_and_botops_takes_the_stubborn_ones(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        stuck = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance")
        waiting = H.task_create(c, H.human_actor("ana"), "Wait for the bank", "Reply due Monday.", "bot:finance")
        later = H.task_create(c, H.human_actor("ana"), "Next time you run", "No hurry.", "bot:finance", next_run=True)
        H.task_update(c, "bot:finance", waiting["id"], status="waiting", note="The bank replies Monday.")
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")        # the creation wakes ran
        for t in (stuck, waiting, later):
            aged(c, t["id"], 6)
        # Six minutes quiet, nothing queued: the bot is woken on it; waiting work and a fresh
        # next-run task are left alone.
        out = H.wake_stalled(c)
        assert out["woke"] == [stuck["id"]] and wakes(c, stuck["id"]) == 1
        assert wakes(c, waiting["id"]) == 0 and wakes(c, later["id"]) == 0
        # The wake queued a run: nothing more while the bot is busy.
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='finance' AND state='queued'").fetchone()[0] >= 1
        assert H.wake_stalled(c)["woke"] == []
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        # Not again within half an hour...
        assert H.wake_stalled(c)["woke"] == []
        # ...a next-run task gets half an hour before its first wake.
        aged(c, later["id"], 31)
        assert H.wake_stalled(c)["woke"] == [later["id"]]
        assert c.execute("SELECT next_run FROM tasks WHERE id=?", (later["id"],)).fetchone()[0] == 0
        # Three wakes in a day that did not move it: BotOps gets one task, and the bot is left be.
        c.execute("UPDATE events SET created_at=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(H.now(), seconds=-3600), stuck["id"]))
        for _ in range(2):
            c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(lower(hex(randomblob(8))),?,?,?,?,'{}')",
                      (H.shift(H.now(), seconds=-3600), H.KEEPER, "task.stall_wake", stuck["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, stuck["id"], 120)                # it has not moved since before those wakes
        out = H.wake_stalled(c)
        assert out["escalated"] == [stuck["id"]] and wakes(c, stuck["id"]) == 1
        assert c.execute("SELECT count(*) FROM tasks WHERE owner_id='bot:botops' AND title LIKE 'Find why finance%'").fetchone()[0] == 1
        assert H.wake_stalled(c)["escalated"] == []                              # once a day



def capped(c, task):
    aged(c, task['id'], 120)
    for _ in range(H.STALL_WAKES_PER_DAY):
        H.event(c, H.KEEPER, 'task.stall_wake', task['id'], {})
    c.execute("UPDATE events SET created_at=? WHERE action='task.stall_wake' AND target=?",
              (H.shift(H.now(), hours=-1), task['id']))


def test_no_diagnostic_is_filed_about_a_diagnostic_or_for_a_person_and_the_stuck_task_says_why(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        own = H.task_create(c, 'bot:finance', 'Repair the mail registry', '', 'bot:botops')
        stuck = H.task_create(c, 'human:ben', 'Check the bank balance', '', 'bot:finance')
        for task in (own, stuck):
            capped(c, task)
        c.execute("UPDATE jobs SET state='completed'")
        assert H.wake_stalled(c)['escalated'] == [own['id'], stuck['id']]
        diagnostics = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE requester_id='keeper'")]
        # BotOps's own stuck work gets no diagnostic of its own; the finance task gets one, for BotOps.
        assert [(d['owner_id'], stuck['id'] in d['body']) for d in diagnostics] == [('bot:botops', True)]
        assert c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:finance' "
                         "AND body LIKE '%BotOps cannot diagnose itself%'").fetchone()[0] == 1
        # The diagnostic sticks too: it closes with the cause, and nothing is filed about it.
        capped(c, diagnostics[0])
        c.execute("UPDATE jobs SET state='completed'")
        assert H.wake_stalled(c)['escalated'] == [diagnostics[0]['id']]
        closed = H.task(c, diagnostics[0]['id'])
        assert closed['status'] == 'closed' and 'did not move after 3 wake-ups' in closed['note']
        assert c.execute("SELECT count(*) FROM tasks WHERE requester_id='keeper'").fetchone()[0] == 1
        # BotOps away: no person is handed a bot's repair; the task carries the cause for its requester.
        c.execute("UPDATE bots SET state='paused' WHERE slug='botops'")
        later = H.task_create(c, 'human:ben', 'Check the card balance', '', 'bot:finance')
        capped(c, later)
        c.execute("UPDATE jobs SET state='completed'")
        assert H.wake_stalled(c)['escalated'] == [later['id']]
        assert c.execute("SELECT count(*) FROM tasks WHERE requester_id='keeper'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM messages WHERE recipient_id='human:ben' AND body LIKE '%BotOps is paused%'"
                         ).fetchone()[0] == 1
        assert H.task(c, later['id'])['status'] == 'open'





def test_a_task_with_a_recorded_wait_or_a_profile_without_sweeps_is_never_swept_but_stays_in_reports(api, monkeypatch):
    from backend import rules
    store = api.app.state.store
    with store.transaction() as c:
        row = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance")
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, row["id"], 6)
        task = lambda: H.task(c, row["id"])
        assert H.stall_skip(c, task()) is None
        # A pending approval, a milestone wait and an open subtask are each a wait the sweep respects.
        c.execute("INSERT INTO approvals(id,kind,task_id,payload,requested_by_id,created_at) VALUES('ap1','send',?,'{}','bot:finance',?)",
                  (row["id"], H.now()))
        assert H.stall_skip(c, task()) == "approval"
        c.execute("DELETE FROM approvals")
        child = H.task_create(c, H.human_actor("ana"), "A subtask", "x", "bot:finance", parent_id=row["id"])
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, row["id"], 6)
        assert H.stall_skip(c, task()) in ("subtask", "blocked_by")
        H.task_close(c, H.human_actor("ana"), child["id"], note="not needed")
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, row["id"], 6)
        assert H.stall_skip(c, task()) is None
        # A profile with no stall sweeps: nothing wakes it, yet the Task Manager's list still names the task.
        monkeypatch.setattr(rules, "sweeps_on", lambda conn, bot: False)
        assert H.stall_skip(c, task()) == "sweeps_off"
        assert not any(H.wake_stalled(c).values())
        assert row["id"] in [t["id"] for t in H.stalled_tasks(c)]


def test_a_chat_task_gets_its_own_room_reports_once_and_a_quiet_session_is_checked_then_flagged(api):
    from backend.tests.test_api import headers, post
    room = post(api, "chat/finance", {"text": "Can you look at cash?"})["conversation_id"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        lines = lambda: [(r[0], json.loads(r[1])) for r in c.execute(
            "SELECT body,refs FROM messages WHERE conversation_id=? AND sender_id=? ORDER BY created_at", (room, H.KEEPER))]
        task = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance",
                             conversation_id=room)
        assert task["conversation_id"] != room and H.conversation(c, task["conversation_id"])["kind"] == "task"
        assert [(b, r.get("task_started")) for b, r in lines()] == [("Started task: Recheck the cash warning", True)]
        # Its session ran 40 minutes ago and nothing moved since: one status check, no stall wake (it is busy).
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r1','r','ana','h','t')")
        job = c.execute("SELECT id FROM jobs WHERE bot_id='finance' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,lane) "
                  "VALUES('a1',?,'finance','r1',1,'a1','running',?,?,?)",
                  (job, H.shift(H.now(), seconds=600), H.shift(H.now(), seconds=-2400), "task:" + task["id"]))
        aged(c, task["id"], 40)
        assert H.wake_stalled(c) == {k: [] for k in ("woke", "escalated", "checked", "stuck", "held", "retried")}, \
            "a live session on the task is never checked"
        c.execute("UPDATE attempts SET state='completed', finished_at=? WHERE id='a1'", (H.shift(H.now(), seconds=-2400),))
        out = H.wake_stalled(c)
        assert out["checked"] == [task["id"]] and out["woke"] == [] and out["stuck"] == []
        assert H.wake_stalled(c)["checked"] == []                       # once per quiet stretch
        # Half an hour after the check, still nothing: its bot is retried once.
        c.execute("UPDATE events SET created_at=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(H.now(), seconds=-1900), task["id"]))
        out = H.wake_stalled(c)
        assert out["retried"] == [task["id"]] and out["stuck"] == []
        assert H.wake_stalled(c)["retried"] == [] and H.wake_stalled(c)["stuck"] == []     # once, then it waits
        # Half an hour after the retry, still nothing: reported stuck in the main chat, and BotOps (not Ana) finds why.
        c.execute("UPDATE events SET created_at=? WHERE action='task.stuck_retry' AND target=?",
                  (H.shift(H.now(), seconds=-1900), task["id"]))
        assert H.wake_stalled(c)["stuck"] == [task["id"]]
        assert lines()[-1][1]["task_report"] == "stuck" and lines()[-1][0].startswith("Recheck the cash warning: stuck.")
        assert c.execute("SELECT count(*) FROM messages WHERE kind='ask' AND recipient_id LIKE 'human:%'").fetchone()[0] == 0
        diagnostic = c.execute("SELECT owner_id,body FROM tasks WHERE requester_id='keeper' AND title LIKE 'Find why %'").fetchall()
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
                             "WHERE m.recipient_id='bot:finance' AND m.body LIKE 'Closed:%' AND m.refs LIKE ?",
                             (f'%{task_id}%',)).fetchall()
            return [r[1] for r in rows]
        ana = H.human_actor("ana")
        same, new = (H.task_create(c, ana, title, "Review it.", "bot:finance") for title in ("Review the Q3 deck", "Review the Q4 deck"))
        for row in (same, new):
            H.task_update(c, "bot:finance", row["id"], status="done", note="Reviewed; two fixes made.")
        H.task_close(c, ana, same["id"], note="Reviewed; two fixes made.")
        H.task_close(c, ana, new["id"], note="Ship the fixes to the board too.")
        assert closed_wakes(same["id"]) == []
        assert closed_wakes(new["id"]) == [1]  # A person's new instruction still needs a turn.


def test_a_bot_still_in_setup_holds_work_from_bots_in_sight_and_is_not_busy_for_the_stall_check(api):
    """A parked bot starts only a person's chat (execution.py claim). Work a bot or Tico sends it waits for its
    setup: the sender is told, Run now says why, its never-starting job is not "busy", and the stall check puts
    its setup in a person's Needs you once instead of waking it; finishing setup clears that."""
    from backend.tests.test_api import headers, post
    store = api.app.state.store
    # Work it held from before an upgrade, when "Set up" bots still took tasks; onboarding bots take none now.
    task = api.post("/api/v2/tasks", json={"owner": "finance", "title": "Reconcile the October invoices", "body": "Before the close."},
                    headers=headers()).json()["task"]
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    with store.transaction() as c:
        secret = H.task_create(c, "human:cara", "Review the salary bands", "Only for Cara.", "bot:finance", private=True)
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot='finance'")
    refused = api.post("/api/v2/tasks", json={"owner": "finance", "title": "Another one", "body": "x"}, headers=headers())
    assert refused.status_code >= 400 and "still onboarding" in refused.text
    refused = api.post(f"/api/v2/tasks/{task['id']}/run-now", json={}, headers=headers())
    assert refused.status_code >= 400 and "still onboarding" in refused.text
    with store.transaction() as c:
        c.execute("UPDATE tasks SET requester_id='bot:ops' WHERE id=?", (task["id"],))   # a bot asked for it
        assert not H.task_private_readable(c, "human:ana", H.task(c, secret["id"]))
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='finance' AND state='queued'").fetchone()[0] == 2
        aged(c, task["id"], 6)
        assert [r["id"] for r in H.stalled_tasks(c)] == [task["id"]], "a job that never starts is not busy"
        assert H.wake_stalled(c)["held"] == [task["id"]]
        assert wakes(c, task["id"]) == 0
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='finance' AND state='queued'").fetchone()[0] == 2
        told = c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:ops' AND refs LIKE '%setup_held%'").fetchone()[0]
        assert told == 1, "the bot that asked is told why"
        reminder = [t for t in H.needs_you(c, "ana")["tasks"] if t["title"].startswith("Finish onboarding finance")]
        assert len(reminder) == 1 and task["id"] in reminder[0]["body"]
        assert "salary" not in reminder[0]["body"] and secret["id"] not in reminder[0]["body"]
        assert "1 you can't see" in reminder[0]["body"], "a private task is only counted"
        aged(c, task["id"], 60)
        assert H.wake_stalled(c)["held"] == [], "once per task"
    post(api, "bots/finance/onboarded", {})
    with store.read() as c:
        assert H.task(c, reminder[0]["id"])["status"] == "done"


def test_a_task_held_only_by_its_own_quiet_subtask_is_flagged_woken_and_shown_stuck(api):
    """A hold kept open until its parent's window is released, set as that parent's blocker, never clears. The
    writer is warned, and once both are quiet the parent's bot is woken on it and BotOps sees it stuck, even
    while it is `waiting`. A question for a person in a subtask still holds its task as before."""
    from backend.tests.test_api import post
    store = api.app.state.store
    with store.transaction() as c:
        window = H.task_create(c, H.human_actor("ana"), "Open the deploy window", "Post OPEN, then RELEASED.", "bot:ops")
        hold = H.task_create(c, "bot:ops", "Hold merges until RELEASED", "Keep this open until RELEASED.",
                             "bot:finance", parent_id=window["id"])
        asked = H.task_create(c, H.human_actor("ana"), "Pick a vendor", "", "bot:ops")
        card = H.task_create(c, "bot:ops", "Which vendor?", "", H.human_actor("ana"), parent_id=asked["id"], lint=False)
        H.TR.relate(c, "bot:ops", asked["id"], card["id"], "blocked_by")
    flagged = post(api, f"tasks/{window['id']}/relations", {"task": hold["id"], "kind": "blocked_by"})
    assert hold["id"] in flagged["warning"] and "neither can ever move" in flagged["warning"]
    with store.transaction() as c:
        H.task_update(c, "bot:ops", window["id"], status="waiting", note="Waiting on the hold's ack.")
        c.execute("UPDATE jobs SET state='completed'")
        for t in (window, hold, asked, card):
            aged(c, t["id"], 6)
        woken = H.wake_stalled(c)["woke"]
        assert window["id"] in woken and asked["id"] not in woken
        woke = c.execute("SELECT body FROM messages WHERE recipient_id='bot:ops' AND refs LIKE ? "
                         "AND refs LIKE '%stalled%'", (f'%{window["id"]}%',)).fetchall()
        assert len(woke) == 1 and "blocked only by its own subtask" in woke[0][0] and hold["id"] in woke[0][0]
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0] >= 1
        # The question for a person is not a stuck block: its task is left alone.
        c.execute("UPDATE jobs SET state='completed'")      # BotOps's list skips a bot that is busy
        stuck = {t["id"]: t for t in H.stuck_tasks(c)}
        assert stuck[window["id"]]["own_blockers"] == [hold["id"]] and asked["id"] not in stuck


def held_window(c, hold_parent=True):
    """Today's shape: a waiting window task blocked by a bot's hold that stays open until RELEASED."""
    window = H.task_create(c, H.human_actor("ana"), "Open the deploy window", "Post OPEN, then RELEASED.", "bot:ops")
    hold = H.task_create(c, "bot:ops", "Hold merges until RELEASED", "Keep this open until RELEASED.",
                         "bot:finance", parent_id=window["id"] if hold_parent else None)
    H.TR.relate(c, "bot:ops", window["id"], hold["id"], "blocked_by")
    H.task_update(c, "bot:ops", window["id"], status="waiting", note="Waiting on the hold's ack.")
    return window, hold


def flagged(c, window, *tasks, minutes=6, keep_jobs_of=None):
    """(parent woken, parent on BotOps's stuck list) once the parent has been quiet six minutes and `tasks` `minutes`."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot_id IS NOT ?", (keep_jobs_of,))
    aged(c, window["id"], 6)
    for t in tasks:
        aged(c, t["id"], minutes)
    stuck = window["id"] in {t["id"] for t in H.stuck_tasks(c)}    # before the wake makes its bot busy
    return window["id"] in H.wake_stalled(c)["woke"], stuck


def test_todays_hold_with_no_date_snooze_or_resume_still_wakes_its_parent_and_is_stuck(api):
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c)
        H.task_comment(c, "bot:finance", hold["id"], "Checkpoint: holding services merges until RELEASED.", wake=False)
        row = H.task(c, hold["id"])
        assert not row["due_at"] and not row["snoozed_until"] and not row["resume"]
        assert flagged(c, window, hold) == (True, True)


def _later(c, task_id, column, hours):
    c.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (H.shift(H.now(), seconds=hours * 3600), task_id))


@pytest.mark.parametrize("wait", ["due", "snooze", "resume", "milestone"])
def test_a_hold_with_its_own_timed_wait_does_not_stall_its_parent_until_that_time_passes(api, wait):
    column = {"due": "due", "snooze": "snoozed_until"}.get(wait)
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c)
        if wait == "resume":
            c.execute("UPDATE tasks SET resume=? WHERE id=?", (json.dumps({"after": "ci"}), hold["id"]))
        elif wait == "milestone":    # Oct 22: a hold waiting for a release (or a PR merge) is woken by it
            MW.add(c, "bot:finance", hold, "release:ticoteam/tico@v9.9.9")
        else:
            _later(c, hold["id"], column, 24)
        assert flagged(c, window, hold) == (False, False)
        if wait == "milestone":      # once it has woken the hold, the wait is no exemption
            # Settle the real transition before aging it: clearing a hold now restores
            # its previous state, which is activity too, and the wake must precede quiet time.
            MW.happened(c, 'release_published', 'ticoteam/tico@v9.9.9')
            assert flagged(c, window, hold) == (True, True)
        elif wait != "resume":       # a passed date or snooze is no exemption
            _later(c, hold["id"], column, -1)
            assert flagged(c, window, hold) == (True, True)


@pytest.mark.parametrize("case", ["fresh", "not_a_subtask", "mixed", "hold_blocked", "hold_asked", "hold_in_review",
                                  "hold_waiting_on_person", "hold_job_queued", "hold_job_leased", "hold_job_running"])
def test_a_blocker_that_can_still_move_does_not_stall_its_parent(api, case):
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c, hold_parent=case != "not_a_subtask")
        other = H.task_create(c, "bot:ops", "Run the migration", "", "bot:finance")
        if case == "mixed":
            H.TR.relate(c, "bot:ops", window["id"], other["id"], "blocked_by")
        if case == "hold_blocked":
            H.TR.relate(c, "bot:finance", hold["id"], other["id"], "blocked_by")
        if case == "hold_asked":
            H.task_ask(c, "bot:finance", hold["id"], "Which window do you mean?")
        if case == "hold_in_review":     # its reviewer moves it
            c.execute("UPDATE tasks SET status='review' WHERE id=?", (hold["id"],))
        if case == "hold_waiting_on_person":
            c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana' WHERE id=?", (hold["id"],))
        if case.startswith("hold_job_"):
            job = c.execute("SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id "
                            "JOIN conversations cv ON cv.id=m.conversation_id "
                            f"WHERE j.bot_id='finance' AND {H.MESSAGE_TASK_SQL}=?", (hold["id"],)).fetchone()[0]
            c.execute("UPDATE jobs SET state=? WHERE id=?", (case.removeprefix("hold_job_"), job))
        assert flagged(c, window, hold, other, minutes=2 if case == "fresh" else 6,
                       keep_jobs_of="finance" if case.startswith("hold_job_") else None) == (False, False)
        if case.startswith("hold_job_"):
            # Another task's queued job on the same bot cannot exempt this hold.
            c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job,))
            assert flagged(c, window, hold, other, keep_jobs_of="finance") == (True, True)
        elif case == "hold_in_review":
            c.execute("UPDATE tasks SET status='doing' WHERE id=?", (hold["id"],))
            assert flagged(c, window, hold, other) == (True, True)
        elif case == "hold_waiting_on_person":
            c.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (hold["id"],))
            assert flagged(c, window, hold, other) == (True, True)


def test_a_parent_waiting_on_a_person_is_not_stalled_by_its_quiet_hold(api):
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c)
        c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (window["id"],))   # no ask row
        assert flagged(c, window, hold) == (False, False)


@pytest.mark.parametrize("sender", ["bot:ops", "human:ana", "bot:boss"])
def test_a_parked_child_only_exempts_its_parent_for_a_job_the_runner_can_claim(api, sender):
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c)
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup', reports_to='boss' WHERE bot='finance'")
        c.execute("UPDATE messages SET sender_id=? WHERE id IN "
                  "(SELECT j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                  "JOIN conversations cv ON cv.id=m.conversation_id "
                  f"WHERE j.bot_id='finance' AND {H.MESSAGE_TASK_SQL}=?)", (sender, hold["id"]))
        assert flagged(c, window, hold, keep_jobs_of="finance") == ((True, True) if sender == "bot:ops"
                                                                  else (False, False))


@pytest.mark.parametrize("reason", ["uncertain", "scope"])
def test_a_queued_child_job_held_by_the_claim_fence_does_not_silence_its_parent(api, reason):
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c)
        if reason == "uncertain":
            c.execute("UPDATE jobs SET state='uncertain' WHERE bot_id='finance'")
            c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                      "VALUES(?,?,'bot:ops','bot:finance','notice','Continue the hold',?,?)",
                      (H.new_id(), hold["conversation_id"], json.dumps({"task": hold["id"]}), H.now()))
        else:
            other = H.task_create(c, "bot:ops", "Run the migration", "", "bot:finance")
            c.execute("INSERT INTO bot_control(bot,claim_task) VALUES('finance',?) "
                      "ON CONFLICT(bot) DO UPDATE SET claim_task=excluded.claim_task", (other["id"],))
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='finance' AND state='queued' AND held=0").fetchone()[0]
        assert flagged(c, window, hold, keep_jobs_of="finance") == (True, True)
        if reason == "uncertain":
            c.execute("UPDATE jobs SET state='completed' WHERE state='uncertain'")
        else:
            c.execute("UPDATE bot_control SET claim_task=NULL WHERE bot='finance'")
        assert flagged(c, window, hold, keep_jobs_of="finance") == (False, False)


def test_botops_stuck_list_skips_an_own_blocked_parent_while_its_bot_is_busy_or_it_waits_for_the_next_run(api):
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c)
        aged(c, window["id"], 6)
        aged(c, hold["id"], 6)
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0] >= 1
        listed = lambda: window["id"] in {t["id"] for t in H.stuck_tasks(c)}  # noqa: E731
        assert not listed()
        c.execute("UPDATE jobs SET state='completed'")
        c.execute("UPDATE tasks SET next_run=1 WHERE id=?", (window["id"],))
        assert not listed()
        c.execute("UPDATE tasks SET next_run=0 WHERE id=?", (window["id"],))
        assert listed()


def test_creating_a_task_that_blocks_two_of_its_ancestors_returns_both_warnings(api):
    from backend.tests.test_api import post
    root = post(api, "tasks", {"owner": "ops", "title": "Ship the release", "body": "x"})
    window = post(api, "tasks", {"owner": "ops", "title": "Open the deploy window", "body": "x",
                                 "relations": [{"task": root["id"], "kind": "parent"}]})
    made = post(api, "tasks", {"owner": "finance", "title": "Hold merges until RELEASED", "body": "x",
                               "relations": [{"task": window["id"], "kind": "parent"},
                                             {"task": window["id"], "kind": "blocks"},
                                             {"task": root["id"], "kind": "blocks"}]})
    hold = made["task"]["id"]
    assert f"subtask of {window['id']}" in made["warning"] and f"subtask of {root['id']}" in made["warning"]
    assert made["warning"].count(hold) == 2


def test_a_parent_held_by_two_quiet_holds_of_its_own_lists_both(api):
    with api.app.state.store.transaction() as c:
        window, hold = held_window(c)
        second = H.task_create(c, "bot:ops", "Hold deploys until RELEASED", "", "bot:finance", parent_id=window["id"])
        H.TR.relate(c, "bot:ops", window["id"], second["id"], "blocked_by")
        assert flagged(c, window, hold, second) == (True, True)
        c.execute("UPDATE jobs SET state='completed'")
        assert {t["id"]: t for t in H.stuck_tasks(c)}[window["id"]]["own_blockers"] == [hold["id"], second["id"]]


def test_between_the_retry_and_the_stuck_report_no_stall_wake_and_the_report_counts_every_wake(api):
    from backend.tests.test_api import post
    room = post(api, "chat/finance", {"text": "Can you look at cash?"})["conversation_id"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        task = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance",
                             conversation_id=room)
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r1','r','ana','h','t')")
        job = c.execute("SELECT id FROM jobs WHERE bot_id='finance' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,lane,finished_at) "
                  "VALUES('a1',?,'finance','r1',1,'a1','completed',?,?,?,?)",
                  (job, H.shift(H.now(), seconds=600), H.shift(H.now(), seconds=-2400), "task:" + task["id"],
                   H.shift(H.now(), seconds=-2300)))
        aged(c, task["id"], 40)
        assert H.wake_stalled(c)["checked"] == [task["id"]]
        c.execute("UPDATE events SET created_at=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(H.now(), seconds=-1900), task["id"]))
        assert H.wake_stalled(c)["retried"] == [task["id"]]
        # Its session ends without moving it, so it is stalled too: the retry is its wake until the report.
        c.execute("UPDATE attempts SET state='completed', lease_until=? WHERE id='a1'", (H.shift(H.now(), seconds=-60),))
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        out = H.wake_stalled(c)
        assert out["woke"] == [] and out["checked"] == [] and out["stuck"] == []
        # An earlier stall wake today counts in the report with the status check and the retry.
        c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), H.shift(H.now(), seconds=-7200), H.KEEPER, "task.stall_wake", task["id"], "{}"))
        c.execute("UPDATE events SET created_at=? WHERE action='task.stuck_retry' AND target=?",
                  (H.shift(H.now(), seconds=-1900), task["id"]))
        assert H.wake_stalled(c)["stuck"] == [task["id"]]
        escalated = c.execute("SELECT detail FROM events WHERE action='task.stall_escalated' AND target=?",
                              (task["id"],)).fetchone()[0]
        assert json.loads(escalated)["wakes"] == 3


def test_stall_checks_back_off_while_a_task_stands_unchanged_and_start_over_when_it_changes(api):
    store = api.app.state.store
    with store.transaction() as c:
        stuck = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance")
        tid = stuck["id"]
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, tid, 6)
        assert H.wake_stalled(c)["woke"] == [tid]
        stand = H._stall_stand(c, H.task(c, tid))
        assert [e for (e,) in c.execute("SELECT json_extract(detail,'$.stand') FROM events "
                                        "WHERE action='task.stall_wake' AND target=?", (tid,))] == [stand]

        def add_wakes(n):
            for _ in range(n):
                c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(lower(hex(randomblob(8))),?,?,"
                          "'task.stall_wake',?,?)", (H.now(), H.KEEPER, tid, json.dumps({"stand": stand})))

        def gap():
            return H._stall_gap(c, H.task(c, tid), H._stall_stand(c, H.task(c, tid)))
        # Unchanged through each check: 30, 60, 120, 240 minutes, then the 8 hour cap.
        assert [gap(), (add_wakes(1), gap())[1], (add_wakes(1), gap())[1], (add_wakes(1), gap())[1]] == [30, 60, 120, 240]
        add_wakes(1)
        assert gap() == H.STALL_BACKOFF_CAP_MINUTES == 480
        # The next check waits that long, not 30 minutes.
        c.execute("UPDATE events SET created_at=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(H.now(), seconds=-31 * 60), tid))
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        assert H.wake_stalled(c)["woke"] == []
        # The bot saying "still waiting" is not a change; a person's comment is, and the gap starts over.
        c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
                  "VALUES(lower(hex(randomblob(8))),?,'bot:finance','human:ana','comment','Still waiting.',?)",
                  (H.task(c, tid)["conversation_id"], H.now()))
        assert gap() == H.STALL_BACKOFF_CAP_MINUTES
        c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at) "
                  "VALUES(lower(hex(randomblob(8))),?,'human:ana','bot:finance','comment','Any news?',?)",
                  (H.task(c, tid)["conversation_id"], H.now()))
        assert gap() == H.STALL_REPEAT_MINUTES


def test_a_task_unchanged_through_three_spread_out_checks_still_goes_to_botops(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        stuck = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance")
        tid = stuck["id"]
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        stand = H._stall_stand(c, H.task(c, tid))
        # Three checks 9 hours apart: only two fall in the last day, yet nothing moved through all three.
        for hours in (27, 18, 9):
            c.execute("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(lower(hex(randomblob(8))),?,?,"
                      "'task.stall_wake',?,?)", (H.shift(H.now(), hours=-hours), H.KEEPER, tid, json.dumps({"stand": stand})))
        aged(c, tid, 30 * 60)
        assert H.wake_stalled(c)["escalated"] == [tid]
        assert H.wake_stalled(c)["escalated"] == []                          # once a day
