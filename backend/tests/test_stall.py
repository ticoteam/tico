"""The stall watcher (hubdb.wake_stalled; Ana, 2026-09-27: "ideally never stuck for more than 5
minutes"): a bot task nothing is going to move wakes its bot, spaced and capped, and BotOps takes
the ones waking does not move. Waiting, busy, blocked and routine-covered work is left alone."""
import json

import pytest

from backend.store import H, encode
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



def test_status_notes_do_not_reset_the_daily_stall_wake_cap(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        task = H.task_create(c, H.human_actor('ana'), 'Recheck the cash warning', '', 'bot:finance')
        for _ in range(H.STALL_WAKES_PER_DAY):
            H.event(c, H.KEEPER, 'task.stall_wake', task['id'], {})
        c.execute("UPDATE events SET ts=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(H.now(), seconds=-3600), task['id']))
        H.task_update(c, 'bot:finance', task['id'], note='Still unable to make progress', quiet=True)
        aged(c, task['id'], 6)   # note is newer than the wakes, but is not a reason to wake forever
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")
        out = H.wake_stalled(c)
        assert out == {'woke': [], 'escalated': [task['id']], 'silent': []}
        assert H.wake_stalled(c) == {'woke': [], 'escalated': [], 'silent': []}

        # The cap expires after a day; it does not strand the task permanently.
        tomorrow = H.shift(H.now(), hours=25)
        assert task['id'] in H.wake_stalled(c, at=tomorrow)['woke']


def capped(c, task):
    aged(c, task['id'], 120)
    for _ in range(H.STALL_WAKES_PER_DAY):
        H.event(c, H.KEEPER, 'task.stall_wake', task['id'], {})
    c.execute("UPDATE events SET ts=? WHERE action='task.stall_wake' AND target=?",
              (H.shift(H.now(), hours=-1), task['id']))


def test_botops_cannot_be_its_own_last_resort_and_sources_do_not_share_a_repair_task(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        first = H.task_create(c, 'bot:finance', 'Repair the mail registry', '', 'bot:botops')
        second = H.task_create(c, H.KEEPER, 'Investigate a stuck repair', '', 'bot:botops')
        for task in (first, second):
            capped(c, task)
        c.execute("UPDATE jobs SET state='completed' WHERE bot='botops'")
        assert H.wake_stalled(c)['escalated'] == [first['id'], second['id']]
        recoveries = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE requester='keeper' AND id NOT IN (?,?)",
                                               (first['id'], second['id']))]
        assert len(recoveries) == 2
        assert {r['owner'] for r in recoveries} == {H.human_actor(H.default_human(c))}
        for task in (first, second):
            event = c.execute("SELECT detail_json FROM events WHERE action='task.stall_escalated' AND target=?",
                              (task['id'],)).fetchone()
            detail = json.loads(event[0])
            recovery = H.task(c, detail['recovery_task'])
            assert task['id'] in recovery['body']
            assert detail['owner'] == recovery['owner']
        assert H.wake_stalled(c) == {'woke': [], 'escalated': [], 'silent': []}
        assert not c.execute("SELECT 1 FROM jobs WHERE bot='botops' AND state='queued'").fetchone()


def test_different_stalled_tasks_on_one_bot_get_distinct_botops_diagnostics(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        tasks = [H.task_create(c, 'human:ana', title, '', 'bot:finance')
                 for title in ('Check the bank balance', 'Reconcile the invoice')]
        for task in tasks:
            capped(c, task)
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")
        assert set(H.wake_stalled(c)['escalated']) == {t['id'] for t in tasks}
        repairs = list(c.execute("SELECT * FROM tasks WHERE owner='bot:botops'"))
        assert len(repairs) == 2


@pytest.mark.parametrize('state', ['paused', 'planned', 'archived', None])
def test_unavailable_botops_routes_stalled_work_to_a_human(api, state):
    with api.app.state.store.transaction() as c:
        if state:
            c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps',?)", (state,))
        task = H.task_create(c, 'human:ben', 'Check the bank balance', '', 'bot:finance')
        capped(c, task)
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")
        assert H.wake_stalled(c)['escalated'] == [task['id']]
        assert c.execute("SELECT owner FROM tasks WHERE requester='keeper'").fetchone()[0] == 'human:ben'


def test_equally_named_source_tasks_keep_separate_recovery_identities(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        sources = [H.task_create(c, requester, 'Reconcile the invoice', '', 'bot:finance')
                   for requester in ('human:ana', 'human:ben')]
        for source in sources:
            capped(c, source)
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")
        assert set(H.wake_stalled(c)['escalated']) == {source['id'] for source in sources}
        repairs = []
        for source in sources:
            details = json.loads(c.execute("SELECT detail_json FROM events WHERE action='task.stall_escalated' "
                                           "AND target=?", (source['id'],)).fetchone()[0])
            repair = H.task(c, details['recovery_task'])
            assert source['id'] in repair['body']
            repairs.append(repair['id'])
        assert len(set(repairs)) == 2


def test_an_unresolved_diagnostic_is_reused_when_the_daily_retry_window_rolls_over(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(slug,display_name,state) VALUES('botops','BotOps','active')")
        source = H.task_create(c, 'human:ana', 'Check the bank balance', '', 'bot:finance')
        capped(c, source)
        c.execute("UPDATE jobs SET state='completed' WHERE bot='finance'")
        assert H.wake_stalled(c)['escalated'] == [source['id']]
        repair = c.execute("SELECT id FROM tasks WHERE requester=?", (H.KEEPER,)).fetchone()[0]
        tomorrow = H.shift(H.now(), seconds=90000)
        for _ in range(3):
            H.event(c, H.KEEPER, 'task.stall_wake', source['id'], {})
        c.execute("UPDATE events SET ts=? WHERE action='task.stall_wake' AND target=?",
                  (H.shift(tomorrow, seconds=-3600), source['id']))
        assert H.wake_stalled(c, at=tomorrow)['escalated'] == [source['id']]
        assert c.execute("SELECT count(*) FROM tasks WHERE requester=?", (H.KEEPER,)).fetchone()[0] == 1
        details = json.loads(c.execute("SELECT detail_json FROM events WHERE action='task.stall_escalated' "
                                      "AND target=? ORDER BY rowid DESC LIMIT 1", (source['id'],)).fetchone()[0])
        assert details['recovery_task'] == repair


def test_a_wait_that_names_no_one_or_runs_past_its_time_goes_in_front_of_a_person(api):
    store = api.app.state.store
    with store.transaction() as c:
        silent = H.task_create(c, H.human_actor("ana"), "Wait for the bank", "Reply due Monday.", "bot:finance")
        named = H.task_create(c, H.human_actor("ana"), "Wait for Ana", "She signs.", "bot:finance")
        late = H.task_create(c, H.human_actor("ana"), "Wait for the crawl", "Recheck.", "bot:finance")
        H.task_update(c, "bot:finance", silent["id"], status="waiting", note="Holding until the 19:05 window.")
        H.task_update(c, "bot:finance", named["id"], status="waiting", waiting_on="ana", note="Sign the form.")
        H.task_update(c, "bot:finance", late["id"], status="waiting", note="Crawl.",
                      wait_until=H.shift(H.now(), seconds=-60))
        for t in (silent, named):
            aged(c, t["id"], 61)
        assert sorted(H.flag_silent_waits(c)) == sorted([silent["id"], late["id"]])
        waiting = {t["id"]: t for t in H.needs_you(c, "ana")["waiting"]}
        assert waiting[silent["id"]]["why"] == "Waiting over an hour and names no one"
        assert "Past its expected time" in waiting[late["id"]]["why"] and "why" not in waiting[named["id"]]
        assert H.flag_silent_waits(c) == []                                       # once per wait
        # Ana's word goes back to the bot and takes it off her list.
        H.task_comment(c, H.human_actor("ana"), silent["id"], "No window was asked for; send it now.")
        assert H.task(c, silent["id"])["wait_escalated_to"] is None
        # The bot naming a time it has not reached yet keeps it off.
        H.task_update(c, "bot:finance", late["id"], status="waiting", note="Crawl.",
                      wait_until=H.shift(H.now(), seconds=3600))
        assert H.task(c, late["id"])["wait_escalated_to"] is None and late["id"] not in H.flag_silent_waits(c)
