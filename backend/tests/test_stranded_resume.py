"""Restarting stranded bot work (backend/stranded_resume.py): a bot's task left in `doing` with no run and nothing to
wait on gets one resume wake through the ordinary wake path, a reviewer's included; a task with a hold, a wait, a live
job or a follow-up to come does not; a sweep takes a few tasks, oldest first, one per bot; three resumes with no
progress stop and list the task once on the bot's manager's roundup, never a person's; progress resets the count.
Synthetic fixtures only."""

from backend import hubdb as H
from backend import profiles as P
from backend import stranded_resume as SR
from backend import task_roundup
from backend.tests.hold_rows import set_holds
from backend.tests.test_api import api, assign, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.test_profiles import add_bot, put


def online(api, *bots):
    """The bots on one computer that was just seen."""
    machine = runner(api)
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, list(bots))
    return machine


def new_task(api, title, owner="ops"):
    return post(api, "tasks", {"owner": owner, "title": title, "body": "Please do " + title + "."})["id"]


def strand(c, tid, hours=2):
    """The task in progress since `hours` ago, its runs over, and nothing said or changed on it since."""
    then = H.shift(H.now(), hours=-hours)
    row = H.task(c, tid)
    c.execute("UPDATE tasks SET status='doing', updated_at=? WHERE id=?", (then, tid))
    c.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (then, row["conversation_id"]))
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (then, tid))
    finish(c, H.actor_id(row["owner_id"]))


def finish(c, bot):
    """The bot's queued runs ran and ended."""
    c.execute("UPDATE jobs SET state='completed' WHERE bot_id=? AND state IN ('queued','leased','running')", (bot,))


def wakes(c, bot, tid):
    """Resume wakes queued for the bot on the task."""
    return c.execute(
        "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? AND j.state='queued' "
        "AND json_extract(m.refs,'$.wake')='resume' AND json_extract(m.refs,'$.stranded')=1 "
        "AND json_extract(m.refs,'$.task')=?",
        (bot, tid),
    ).fetchone()[0]


def resumes(c, tid):
    return [
        r[0]
        for r in c.execute(
            "SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? ORDER BY created_at",
            (tid, SR.RESUMED),
        )
    ]


def stops(c, tid):
    return [
        tuple(r)
        for r in c.execute(
            "SELECT new, note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=?",
            (tid, SR.STOPPED),
        )
    ]


def answer_resume(c, tid):
    """The bot answers its last resume notice and changes nothing: an answer to a resume is not progress."""
    row = H.task(c, tid)
    notice = c.execute(
        "SELECT id FROM messages WHERE conversation_id=? AND sender_id=? AND json_extract(refs,'$.stranded')=1 "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (row["conversation_id"], H.KEEPER),
    ).fetchone()[0]
    c.execute(
        "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,in_reply_to,created_at) "
        "VALUES(?,?,?,?,'say','Still on it.',NULL,?,?)",
        (H.new_id(), row["conversation_id"], row["owner_id"], H.KEEPER, notice, H.now()),
    )


def lists(c, slug):
    return [
        r[0]
        for r in c.execute(
            "SELECT body FROM messages WHERE recipient_id=? AND refs LIKE '%task_roundup%' ORDER BY created_at",
            (H.bot_actor(slug),),
        )
    ]


def test_a_stranded_doing_task_gets_one_resume_wake_and_no_second_while_it_runs(api):
    online(api, "ops")
    tid = new_task(api, "Draft the notes")
    with api.app.state.store.transaction() as c:
        strand(c, tid)
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        assert wakes(c, "ops", tid) == 1 and resumes(c, tid) == ["1"]
        assert SR.run(c, H.shift(H.now(), hours=1)) == {"resumed": 0, "stopped": 0}, "its resume is queued"
        assert wakes(c, "ops", tid) == 1


def test_a_task_quiet_less_than_the_quiet_time_after_its_last_run_is_not_resumed(api):
    online(api, "ops")
    tid = new_task(api, "Draft the notes")
    with api.app.state.store.transaction() as c:
        strand(c, tid)
        c.execute(
            "INSERT INTO attempts(id,bot_id,created_at,settled_at,trigger,message_id,task_id,exit,summary) "
            "VALUES(?,?,?,?,'schedule',NULL,?,'done','')",
            (H.new_id(), "ops", H.now(), H.now(), tid),
        )
        assert SR.run(c, H.shift(H.now(), minutes=5)) == {"resumed": 0, "stopped": 0}
        assert SR.run(c, H.shift(H.now(), minutes=SR.QUIET_MINUTES + 1))["resumed"] == 1


def test_a_reviewer_bots_task_left_in_doing_is_resumed(api):
    # The Post Release QA bot is a reviewer: it pulls no work of its own, so a check it left in doing sat there.
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        parent = H.task_create(c, "bot:ops", "Build 1/9", "x", "bot:cpo")
        tid = H.task_create(c, "bot:ops", "QA: check the build", "x", "bot:checker", parent_id=parent["id"])["id"]
    online(api, "checker")
    with api.app.state.store.transaction() as c:
        strand(c, tid)
        assert SR.run(c, H.now())["resumed"] == 1
        assert wakes(c, "checker", tid) == 1


def test_a_task_with_a_hold_a_wait_a_live_job_or_a_follow_up_to_come_is_not_resumed(api):
    online(api, "ops")
    blocked = new_task(api, "Blocked one")
    snoozed = new_task(api, "Snoozed one")
    waits = new_task(api, "Waits on a blocker")
    live = new_task(api, "Waiting for input")
    later = new_task(api, "Follow up later")
    quarantined = new_task(api, "Quarantined one")
    open_task = new_task(api, "Not started")
    plain = new_task(api, "Nothing to wait on")
    with api.app.state.store.transaction() as c:
        for tid in (blocked, snoozed, waits, live, later, quarantined, open_task):
            strand(c, tid, hours=3)
        strand(c, plain, hours=2)  # the newest: the held ones come first and must not use up the bot's one resume
        set_holds(c, blocked, blocked_since=H.shift(H.now(), hours=-3), blocked_reason="Waiting on Ana")
        set_holds(c, snoozed, snoozed_until=H.shift(H.now(), days=1))
        blocker = H.task_create(c, H.human_actor("ana"), "Sign the form", "x", H.human_actor("ana"))
        c.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'blocks','human:ana',?)",
            (blocker["id"], waits, H.now()),
        )
        conv = H.task(c, live)["conversation_id"]
        c.execute(
            "UPDATE jobs SET state='input' WHERE bot_id='ops' AND message_id IN "
            "(SELECT id FROM messages WHERE conversation_id=?)",
            (conv,),
        )
        c.execute("UPDATE tasks SET check_back_at=? WHERE id=?", (H.shift(H.now(), days=1), later))
        c.execute(
            "INSERT INTO quarantine_holds(bot_id,task_id,reason,refusal,created_at) VALUES('ops',?,'x','x',?)",
            (quarantined, H.now()),
        )
        c.execute("UPDATE tasks SET status='open' WHERE id=?", (open_task,))
        person = H.task_create(c, H.human_actor("ben"), "Person's work", "x", H.human_actor("ana"))
        strand(c, person["id"], hours=4)
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        assert wakes(c, "ops", plain) == 1
        for tid in (blocked, snoozed, waits, live, later, quarantined, open_task, person["id"]):
            assert resumes(c, tid) == [], H.task(c, tid)["title"]


def test_a_paused_or_draining_bot_or_one_whose_computer_is_not_seen_gets_no_resume(api):
    online(api, "ops")
    tid = new_task(api, "Draft the notes")
    with api.app.state.store.transaction() as c:
        strand(c, tid)
        c.execute(
            "INSERT INTO bot_status(bot,state,changed_at,updated_at) VALUES('ops','paused',?,?)", (H.now(), H.now())
        )
        assert SR.run(c, H.now())["resumed"] == 0
        c.execute("DELETE FROM bot_status WHERE bot='ops'")
        c.execute("INSERT INTO bot_control(bot,draining) VALUES('ops',1)")
        assert SR.run(c, H.now())["resumed"] == 0
        c.execute("DELETE FROM bot_control WHERE bot='ops'")
        c.execute("UPDATE runner_states SET last_seen_at=?", (H.shift(H.now(), hours=-1),))
        assert SR.run(c, H.now())["resumed"] == 0
        assert resumes(c, tid) == []


def test_one_sweep_resumes_one_task_per_bot_and_at_most_its_cap_oldest_first(api, monkeypatch):
    online(api, "ops", "cpo")
    older = new_task(api, "Older work")
    newer = new_task(api, "Newer work")
    theirs = new_task(api, "Their work", owner="cpo")
    with api.app.state.store.transaction() as c:
        strand(c, older, hours=5)
        strand(c, newer, hours=3)
        strand(c, theirs, hours=4)
        monkeypatch.setattr(SR, "SWEEP_CAP", 1)
        assert SR.run(c, H.now())["resumed"] == 1
        assert resumes(c, older) == ["1"] and resumes(c, theirs) == [] and resumes(c, newer) == []
        monkeypatch.setattr(SR, "SWEEP_CAP", 25)
        # ops is busy with its resume, so the next sweep takes cpo's task and leaves ops's other one for later.
        assert SR.run(c, H.now())["resumed"] == 1
        assert resumes(c, theirs) == ["1"] and resumes(c, newer) == []
        finish(c, "ops")
        finish(c, "cpo")
        at = H.shift(H.now(), minutes=SR.QUIET_MINUTES + 1)
        assert SR.run(c, at)["resumed"] == 2, "one per bot: one of ops's tasks and cpo's"
        assert len(resumes(c, older)) + len(resumes(c, newer)) == 2 and resumes(c, theirs) == ["1", "2"]


def test_three_resumes_with_no_progress_stop_and_list_once_on_the_managers_roundup(api):
    online(api, "ops", "cpo")
    tid = new_task(api, "Draft the notes")
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        strand(c, tid)
        at = H.now()
        for _ in range(SR.NO_PROGRESS_LIMIT):
            assert SR.run(c, at) == {"resumed": 1, "stopped": 0}
            answer_resume(c, tid)
            finish(c, "ops")
            at = H.shift(at, minutes=SR.QUIET_MINUTES + 1)
        assert resumes(c, tid) == ["1", "2", "3"]
        assert SR.run(c, at) == {"resumed": 0, "stopped": 1}
        assert stops(c, tid) == [("bot:cpo", SR.STOP_NOTE)]
        assert SR.run(c, H.shift(at, hours=2)) == {"resumed": 0, "stopped": 0}, "stopped stays stopped"
        assert wakes(c, "ops", tid) == 0

        assert "cpo" in task_roundup.run(c, at)["woke"]
        body = lists(c, "cpo")[-1]
        assert f"{tid[:8]} Draft the notes" in body and "no progress after 3 automatic resumes" in body
        assert not any("automatic resumes" in b for b in lists(c, "ops")), "the manager hears, not the owner"
        task_roundup.run(c, H.shift(at, hours=6))
        assert sum("automatic resumes" in b for b in lists(c, "cpo")) == 1, "listed once"
        assert (
            c.execute(
                "SELECT count(*) FROM messages WHERE recipient_id LIKE 'human:%' AND body LIKE '%automatic resume%'"
            ).fetchone()[0]
            == 0
        ), "no person is told"


def test_a_stop_that_reaches_the_manager_gives_it_one_ticket_at_once(api):
    online(api, "ops", "cpo")
    tid = new_task(api, "Draft the notes")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        put(c, "cpo", P.LEAD)  # a lead's catch-all digest row must not hold the manager's wake
        strand(c, tid)
        at = H.now()
        for _ in range(SR.NO_PROGRESS_LIMIT):
            assert SR.run(c, at)["resumed"] == 1
            finish(c, "ops")
            at = H.shift(at, minutes=SR.QUIET_MINUTES + 1)
        assert SR.run(c, at)["stopped"] == 1
        made = c.execute(
            "SELECT owner_id, title FROM tasks WHERE title LIKE ?", ("%made no progress in 3 runs%",)
        ).fetchall()
        assert len(made) == 1, "one ticket for the manager"
        assert made[0][0] == "bot:cpo" and "builder ops" in made[0][1]
        held = c.execute(
            "SELECT j.held FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.bot_id='cpo' AND json_extract(m.refs,'$.wake')='nudge'"
        ).fetchall()
        assert [row[0] for row in held] == [0], "the manager's wake is immediate, not held for its digest"
        assert SR.run(c, H.shift(at, hours=2)) == {"resumed": 0, "stopped": 0}
        assert (
            c.execute("SELECT count(*) FROM tasks WHERE title LIKE ?", ("%made no progress in 3 runs%",)).fetchone()[0]
            == 1
        ), "a stop is made once"


def test_a_bot_with_no_manager_bot_hears_of_its_own_stop(api):
    online(api, "ops")
    tid = new_task(api, "Draft the notes")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to=NULL WHERE bot='ops'")
        strand(c, tid)
        at = H.now()
        for _ in range(SR.NO_PROGRESS_LIMIT):
            assert SR.run(c, at)["resumed"] == 1
            finish(c, "ops")
            at = H.shift(at, minutes=SR.QUIET_MINUTES + 1)
        assert SR.run(c, at)["stopped"] == 1
        assert stops(c, tid) == [("bot:ops", SR.STOP_NOTE)]
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE ?", ("%made no progress%",)).fetchone()[0] == 0, (
            "a stop that reaches the builder itself makes no ticket"
        )


def test_progress_between_resumes_resets_the_count(api):
    online(api, "ops")
    tid = new_task(api, "Draft the notes")
    with api.app.state.store.transaction() as c:
        strand(c, tid)
        at = H.now()
        for _ in range(2):
            assert SR.run(c, at)["resumed"] == 1
            finish(c, "ops")
            at = H.shift(at, minutes=SR.QUIET_MINUTES + 1)
        H._task_event(c, tid, "bot:ops", "note", None, "Found the draft; two sections left", "")
        at = H.shift(at, minutes=SR.QUIET_MINUTES + 1)
        for _ in range(SR.NO_PROGRESS_LIMIT - 1):
            assert SR.run(c, at)["resumed"] == 1
            finish(c, "ops")
            at = H.shift(at, minutes=SR.QUIET_MINUTES + 1)
        assert resumes(c, tid) == ["1", "2", "1", "2"]
        assert SR.run(c, at) == {"resumed": 1, "stopped": 0}, "two since the progress: not stopped yet"
        assert resumes(c, tid)[-1] == "3"


def test_health_stuck_task_check_leaves_a_task_the_sweep_just_resumed_to_it(api):
    from backend import invariants as I

    online(api, "ops")
    tid = new_task(api, "Draft the notes")
    with api.app.state.store.transaction() as c:
        strand(c, tid)
        assert tid in I.task_stuck(c, I.Ctx(now=H.now(), at=None))
        assert SR.run(c, H.now())["resumed"] == 1
        finish(c, "ops")
        assert tid not in I.task_stuck(c, I.Ctx(now=H.now(), at=None))
