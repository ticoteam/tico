"""Routines are rows in the hub (backend/routines.py): people and bots write them through the
API, the scheduler turns each due occurrence into a task, and the listing and occurrence views
read the same rows."""
from datetime import datetime, timezone

from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401

AUDIT = dict(key="audit", cron="0 7 * * 1-5", title="Daily rental stats audit",
             text="Audit the rental stats using this saved instruction.")
WEEKLY = dict(key="weekly", cron="30 16 * * 5", title="Weekly backlog", text="Review the backlog.")
DEBRIEF = dict(key="debrief", on="meeting.ready", title="Meeting debrief",
               text="Pull the product asks out of this meeting.")
AT = datetime(2026, 9, 14, 13, tzinfo=timezone.utc)   # a Monday, 06:00 Pacific


def create(api, bot, fields, token="ana-test", expected=200):
    return post(api, f"bots/{bot}/routines", fields, token=token, expected=expected)


def rows(api, bot="ops", include_deleted=False, token="ana-test"):
    return get(api, f"bots/{bot}/routines" + ("?include_deleted=true" if include_deleted else ""),
               token=token)["routines"]


def scheduler(api):
    return Scheduler(api.app.state.store, api.app.state.execution)


def tick(api, when):
    with api.app.state.store.transaction() as c:
        # The creates above computed next_due from the real clock; the tests run at AT.
        c.execute("UPDATE schedules SET next_due=NULL, last_fired=? WHERE next_due IS NOT NULL",
                  (AT.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),))
    result = scheduler(api).tick(when)
    # A routine starts held under Performance. The next planner pass releases a first occurrence immediately.
    from backend import wake_presets
    with api.app.state.store.transaction() as c:
        wake_presets.tick(c, H.now())
    return result


def setup(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    create(api, "ops", AUDIT)
    create(api, "ops", WEEKLY)
    return r


def test_a_routine_is_created_listed_fired_and_seen_by_the_bot(api):
    r = setup(api)
    listed = rows(api)
    assert [row["key"] for row in listed] == ["audit", "weekly"]
    audit = next(row for row in listed if row["key"] == "audit")
    assert audit["id"] == "ops:audit" and audit["cron"] == AUDIT["cron"] and audit["kind"] == "cron"
    assert audit["text"] == AUDIT["text"] and audit["enabled"] is True and audit["next_due"]
    fired = tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))["fired"]
    assert len(fired) == 1
    task = get(api, "tasks/" + fired[0])["task"]
    assert task["body"] == AUDIT["text"] and task["routine_id"] == "ops:audit" and task["owner"] == "bot:ops"
    attempt = claim(api, r)
    assert attempt["task"]["id"] == fired[0] and attempt["routine"]["id"] == "ops:audit"
    # Editing the text reaches the next occurrence, never the task already opened.
    post(api, "routines/ops:audit", {"text": "Changed for next time"})
    assert get(api, "tasks/" + fired[0])["task"]["body"] == AUDIT["text"]
    assert scheduler(api).tick(datetime(2026, 9, 14, 14, 1, tzinfo=timezone.utc))["fired"] == []


def test_later_occurrences_keep_unfinished_claimed_work_and_deduplicate(api):
    status = 'doing'
    r = setup(api)
    first = tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))['fired'][0]
    attempt = claim(api, r)
    assert attempt['task']['id'] == first
    with api.app.state.store.transaction() as c:
        H.task_update(c, H.KEEPER, first, status=status)
    at = datetime(2026, 9, 15, 14, tzinfo=timezone.utc)
    for _ in range(2):
        assert scheduler(api).tick(at)['fired'] == []
    with api.app.state.store.read() as c:
        occurrences = c.execute('SELECT task_id,outcome FROM schedule_occurrences ORDER BY occurrence').fetchall()
        assert [tuple(row) for row in occurrences] == [(first, 'created'), (first, 'coalesced_into_existing_task')]
        assert H.task(c, first)['status'] == status
        assert c.execute('SELECT state FROM attempts WHERE id=?', (attempt['id'],)).fetchone()[0] == 'leased'


def test_who_may_write_a_bots_routines(api):
    # Ben operates cpo; a member cannot touch ops's routines (an admin could), and Cara cannot touch cpo's.
    create(api, "cpo", DEBRIEF, token="ben-test")
    create(api, "ops", AUDIT, token="cara-test", expected=403)
    create(api, "cpo", WEEKLY, token="cara-test", expected=403)
    post(api, "routines/cpo:debrief", {"enabled": False}, token="cara-test", expected=403)
    post(api, "routines/cpo:debrief/delete", {}, token="cara-test", expected=403)
    assert rows(api, "cpo", token="ben-test")[0]["enabled"] is True
    # A bot sets up its own routines from a turn, with its per-turn credential.
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Set yourself a weekly review."})
    attempt = claim(api, r)
    mine = post(api, "bots/ops/routines", WEEKLY, token=attempt["token"])["routine"]
    assert mine["id"] == "ops:weekly"
    post(api, "bots/cpo/routines", WEEKLY, token=attempt["token"], expected=403)
    assert rows(api, "ops", token=attempt["token"])[0]["key"] == "weekly"


def test_a_manager_bot_reads_its_reports_routines_and_no_other_bot_does(api):
    # ops reports to cpo: cpo's own run reads ops's routines and their occurrences; finance, beside it, cannot.
    r = setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        c.execute("UPDATE bot_config SET reports_to=NULL WHERE bot IN ('cpo','finance')")
    tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))
    ben = runner(api, "ben", "Ben Mac")

    def place(computer, bot):
        with api.app.state.store.read() as c:
            placed = c.execute("SELECT generation FROM assignments WHERE bot=?", (bot,)).fetchone()
        assign(api, computer, bot, generation=placed[0] if placed else 0)
        ready(api, computer, [bot])
        post(api, "chat/" + bot, {"text": "How are your reports doing?"})
        return claim(api, computer, bot=bot)

    manager = place(ben, "cpo")
    assert [row["key"] for row in rows(api, "ops", token=manager["token"])] == ["audit", "weekly"]
    assert get(api, "routines/ops:audit/occurrences", token=manager["token"])["occurrences"]
    create(api, "ops", DEBRIEF, token=manager["token"], expected=403)
    other = place(r, "finance")
    get(api, "bots/ops/routines", token=other["token"], expected=403)
    get(api, "routines/ops:audit/occurrences", token=other["token"], expected=403)


def test_delete_keeps_history_closes_unclaimed_work_and_leaves_running_work(api):
    r = setup(api)
    fired = tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))["fired"]
    gone = post(api, "routines/ops:audit/delete", {})["routine"]
    assert gone["deleted_at"] and gone["next_due"] is None
    assert [row["key"] for row in rows(api)] == ["weekly"]
    assert len(rows(api, include_deleted=True)) == 2
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM schedule_occurrences").fetchone()[0] == 1
        assert H.task(c, fired[0])["status"] == "closed"
    assert claim(api, r) is None
    post(api, "routines/ops:audit", {"title": "Back"}, expected=404)
    # The same key brings it back, settings and history intact.
    restored = create(api, "ops", {**AUDIT, "timezone": "UTC"})["routine"]
    assert restored["deleted_at"] is None and restored["timezone"] == "UTC"
    assert len(rows(api, include_deleted=True)) == 2
    # A routine whose task a machine already holds is removed without touching that attempt.
    post(api, "routines/ops:weekly", {"enabled": False})
    assert len(tick(api, datetime(2026, 9, 21, 14, tzinfo=timezone.utc))["fired"]) == 1
    # The occurrence clock moved a week; advance the wake planner too, beyond the earlier unclaimed sweep.
    from backend import wake_presets
    with api.app.state.store.transaction() as c:
        wake_presets.tick(c, H.shift(H.now(), days=7))
    attempt = claim(api, r)
    post(api, "routines/ops:audit/delete", {})
    with api.app.state.store.read() as c:
        assert H.task(c, attempt["task"]["id"])["status"] == "open"
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0] == "leased"


def test_run_now_during_another_task_stays_queued_for_its_own_run(api):
    r = setup(api)
    task = post(api, "tasks", {"title": "Write a report", "body": "Write the report", "owner": "ops"})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "report"}, r["token"])
    fired = post(api, "routines/ops:audit/run", {})
    tid = fired["task_id"]
    assert tid != task["id"]
    assert post(api, f"attempts/{attempt['id']}/inputs", {}, r["token"])["messages"] == []
    with api.app.state.store.transaction() as c:
        message = c.execute("SELECT m.id FROM jobs j JOIN messages m ON m.id=j.message_id "
                            "WHERE j.bot='ops' AND json_extract(m.refs_json,'$.task')=? AND j.state='queued'", (tid,)).fetchone()[0]
        c.execute("INSERT INTO attempt_inputs VALUES(?,?,?)", (attempt["id"], message, H.now()))
        c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id=?", (attempt["id"], message))
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Report finished"}, r["token"])
    next_attempt = claim(api, r)
    assert next_attempt["task"]["id"] == tid
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (next_attempt["job_id"],)).fetchone()[0] == "leased"


def control(api, bot, action):
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]
    post(api, f"bots/{bot}/control", {"action": action, "expected_revision": revision})


def test_pausing_a_bot_holds_its_routines_and_resuming_brings_back_the_enabled_ones(api):
    r = setup(api)
    queued = tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))["fired"][0]
    direct = post(api, "tasks", {"title": "Write a report", "body": "Write the report", "owner": "ops"})
    post(api, "routines/ops:weekly", {"enabled": False})
    control(api, "ops", "pause")
    with api.app.state.store.read() as c:
        # The routine run nobody started is closed with a reason; a person's own work stays for the bot.
        assert H.task(c, queued)["status"] == "closed" and "paused" in H.task(c, queued)["note"]
        assert H.task(c, direct["id"])["status"] == "open"
    listed = {row["key"]: row for row in rows(api)}
    assert listed["audit"]["enabled"] is True and listed["audit"]["held_by_bot"] == "paused"
    assert listed["weekly"]["held_by_bot"] is None and not listed["audit"]["active"]
    assert scheduler(api).tick(datetime(2026, 9, 15, 14, tzinfo=timezone.utc))["fired"] == []
    control(api, "ops", "resume")
    listed = {row["key"]: row for row in rows(api)}
    assert listed["audit"]["active"] and listed["audit"]["held_by_bot"] is None
    assert listed["weekly"]["enabled"] is False     # a routine a person turned off stays off
    fired = scheduler(api).tick(datetime(2026, 9, 16, 14, tzinfo=timezone.utc))["fired"]
    assert [get(api, "tasks/" + tid)["task"]["routine_id"] for tid in fired] == ["ops:audit"]


def test_a_bot_still_in_setup_gets_no_timed_runs_and_keeps_its_event_runs(api):
    setup(api)
    create(api, "ops", DEBRIEF)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET onboarding_state='needs_setup' WHERE bot='ops'")
        from backend import routines
        # A meeting's notes are announced once, so its run waits for the bot instead of being dropped.
        assert len(routines.emit(c, "meeting.ready", "rec-1", title="Planning")) == 1
    assert tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))["fired"] == []
    assert {row["held_by_bot"] for row in rows(api)} == {"needs_setup"}
    post(api, "routines/ops:audit/run", {}, expected=409)     # as the page, which offers no Run now


def test_pausing_keeps_event_runs_run_now_and_runs_handed_to_another_bot(api):
    setup(api)
    create(api, "ops", DEBRIEF)
    queued = tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))["fired"][0]
    from backend import routines
    with api.app.state.store.transaction() as c:
        event_run = routines.emit(c, "meeting.ready", "rec-1", title="Planning", content="Ship Friday")[0]
        moved = routines.open_task(c, None, routines.row(c, "ops:weekly"), "Weekly backlog", "Review", H.now())
        c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,?)", ("ops:weekly", "2026-09-11T16:30", moved["id"], "created"))
        c.execute("UPDATE tasks SET owner='bot:other' WHERE id=?", (moved["id"],))
        asked = routines.open_task(c, None, routines.row(c, "ops:weekly"), "Weekly backlog", "Review", H.now())
        c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,?)", ("ops:weekly", "manual:x", asked["id"], "created"))
    control(api, "ops", "pause")
    with api.app.state.store.read() as c:
        assert H.task(c, queued)["status"] == "closed"
        assert H.task(c, event_run)["status"] == "open"      # one-off content, never announced again
        assert H.task(c, moved["id"])["status"] == "open"    # the other bot's now
        assert H.task(c, asked["id"])["status"] == "open"    # a person's Run now


def test_a_routine_run_stays_company_scope_and_a_private_next_run_task_gets_its_own_run(api):
    from backend import task_privacy as privacy
    from backend.auth import Identity
    r = setup(api)
    secret = post(api, "tasks", {"owner": "ops", "title": "Review the settlement", "body": "Private.",
                                 "next_run": True, "private": True})
    assert secret["private"] is True
    fired = tick(api, datetime(2026, 9, 14, 14, tzinfo=timezone.utc))["fired"]
    attempt = post(api, "jobs/claim", {"next_run": True}, token=r["token"])["attempt"]
    assert attempt["task"]["id"] == fired[0] and not attempt.get("next_run")
    with api.app.state.store.read() as c:
        assert not privacy.private_execution(c, Identity("bot:ops", "bot", attempt_id=attempt["id"]))
    # It gets a run of its own rather than waiting for one that may never come.
    assert not get(api, "tasks/" + secret["id"])["task"].get("next_run_waiting")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot='ops' "
                         "AND j.state='queued' AND json_extract(m.refs_json,'$.task')=?", (secret["id"],)).fetchone()
