"""The stalled and stuck lists (hubdb.stalled_tasks, hubdb.stuck_tasks): a bot task nothing is going to move is listed as
stalled, and BotOps's stuck list takes the ones that quiet subtasks hold. Tico wakes no bot for a stall. Waiting, busy,
blocked and routine-covered work is left alone."""
from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def aged(c, task_id, minutes):
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), seconds=-minutes * 60), task_id))


def test_a_private_quiet_task_is_listed_as_stalled_for_its_owner(api):
    """Private work stalls too: its owner reads it, and its words go no further than that."""
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, H.human_actor("ana"), "Recheck the salary review", "Before the deadline.", "bot:finance",
                            private=True)
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, row["id"], 6)
        assert H.task_private(c, H.task(c, row["id"]))
        assert row["id"] in [r["id"] for r in H.stalled_tasks(c)]


def test_a_task_with_a_recorded_wait_names_it_and_a_profile_without_sweeps_still_lists_it(api, monkeypatch):
    from backend import rules
    store = api.app.state.store
    with store.transaction() as c:
        row = H.task_create(c, H.human_actor("ana"), "Recheck the cash warning", "Before the deadline.", "bot:finance")
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, row["id"], 6)
        task = lambda: H.task(c, row["id"])
        assert H.recorded_wait(c, task()) is None
        # A pending approval, and an open subtask, are each a wait the list reads.
        c.execute("INSERT INTO approvals(id,kind,task_id,payload,requested_by_id,created_at) VALUES('ap1','send',?,'{}','bot:finance',?)",
                  (row["id"], H.now()))
        assert H.recorded_wait(c, task()) == "approval"
        c.execute("DELETE FROM approvals")
        child = H.task_create(c, H.human_actor("ana"), "A subtask", "x", "bot:finance", parent_id=row["id"])
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, row["id"], 6)
        assert H.recorded_wait(c, task()) == "subtask"
        H.task_close(c, H.human_actor("ana"), child["id"], note="not needed")
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='finance'")
        aged(c, row["id"], 6)
        assert H.recorded_wait(c, task()) is None
        # A profile with its sweeps off: the waits read the same, and the stalled list still names the task.
        monkeypatch.setattr(rules, "sweeps_on", lambda conn, bot: False)
        assert row["id"] in [t["id"] for t in H.stalled_tasks(c)]


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


def test_a_bot_still_in_setup_holds_work_from_bots_in_sight_and_is_not_busy_for_the_stalled_list(api):
    """A parked bot starts only a person's chat (execution.py claim). Work a bot or Tico sends it waits for its
    setup: Run now says why, its never-starting job is not "busy", and the stalled list marks its task held."""
    from backend.tests.test_api import headers
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
        [stalled] = H.stalled_tasks(c)
        assert stalled["id"] == task["id"] and stalled["held"] == "needs_setup", "a job that never starts is not busy"
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='finance' AND state='queued'").fetchone()[0] == 2


def test_a_task_held_only_by_its_own_quiet_subtask_is_listed_stuck_even_while_waiting(api):
    """A hold kept open until its parent's window is released, set as that parent's blocker, never clears. Once both
    are quiet, BotOps's stuck list shows the parent even while it is `waiting`. A question for a person in a subtask
    still holds its task as before."""
    from backend.tests.test_api import post
    store = api.app.state.store
    with store.transaction() as c:
        window = H.task_create(c, H.human_actor("ana"), "Open the deploy window", "Post OPEN, then RELEASED.", "bot:ops")
        hold = H.task_create(c, "bot:ops", "Hold merges until RELEASED", "Keep this open until RELEASED.",
                             "bot:finance", parent_id=window["id"])
        asked = H.task_create(c, H.human_actor("ana"), "Pick a vendor", "", "bot:ops")
        card = H.task_create(c, "bot:ops", "Which vendor?", "", H.human_actor("ana"), parent_id=asked["id"], lint=False)
        H.TR.relate(c, "bot:ops", asked["id"], card["id"], "blocked_by")
    related = post(api, f"tasks/{window['id']}/relations", {"task": hold["id"], "kind": "blocked_by"})
    assert hold["id"] in related["warning"] and "neither can ever move" in related["warning"]
    with store.transaction() as c:
        H.task_update(c, "bot:ops", window["id"], status="waiting", note="Waiting on the hold's ack.")
        c.execute("UPDATE jobs SET state='completed'")
        for t in (window, hold, asked, card):
            aged(c, t["id"], 6)
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
        c.execute("UPDATE jobs SET state='completed'")
        for t in (window, hold, second):
            aged(c, t["id"], 6)
        assert {t["id"]: t for t in H.stuck_tasks(c)}[window["id"]]["own_blockers"] == [hold["id"], second["id"]]
