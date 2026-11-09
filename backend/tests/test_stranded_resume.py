"""Restarting stranded bot work (backend/stranded_resume.py): a bot's task left in `doing` with no run and nothing to
wait on gets one resume wake through the ordinary wake path, a reviewer's included; a task with a hold, a wait, a live
job or a follow-up to come does not; a sweep takes a few tasks, oldest first, one per bot; three resumes with no
progress stop and list the task once on the bot's manager's roundup, never a person's; progress resets the count. A resume
still queued when its task has moved on is dropped at claim; one whose task is still stranded is handed out.
Synthetic fixtures only."""

import pytest

from backend import hubdb as H
from backend import profiles as P
from backend import stranded_resume as SR
from backend import task_roundup
from backend.tests.hold_rows import set_holds
from backend.tests.helpers.api import assign, claim, post, ready, runner  # noqa: F401  (api is a fixture)
from backend.tests.helpers.profiles import add_bot, put
from backend.tests.helpers.stranded_resume import finish, new_task, online, resumes, strand, wakes  # noqa: F401


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


def test_a_busy_owner_takes_its_resume_behind_its_other_work(api):
    # A run queued for the bot on other work does not hold its resume back: the wake waits in line behind that work.
    online(api, "ops")
    tid = new_task(api, "Draft the notes")
    other = new_task(api, "Other work")
    with api.app.state.store.transaction() as c:
        strand(c, tid)
        row = H.task(c, other)
        assert H._wake(c, row, row["owner_id"], "Please do the other work.", {"needs_action": True}), "a run is queued"
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0] >= 1
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        assert resumes(c, tid) == ["1"]


def test_a_reviewer_bots_task_left_in_doing_is_resumed(api):
    # The Watch 24h bot is a reviewer: it pulls no work of its own, so a check it left in doing sat there.
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


def queued_resume(api, tid):
    """The task stranded and its resume queued for its bot, with nothing else of the bot's queued. Returns the resume's
    message id. The notice is written before anything that moves the task on below."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued'")  # the new-task notices
        strand(c, tid)
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        message = c.execute(
            "SELECT m.id FROM messages m JOIN jobs j ON j.message_id=m.id WHERE j.bot_id='ops' AND j.state='queued' "
            "AND json_extract(m.refs,'$.stranded')=1 AND json_extract(m.refs,'$.task')=?",
            (tid,),
        ).fetchone()[0]
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), minutes=-30), message))
    return message


def move_on(c, tid, how):
    """The task moves on from the state its resume was written in: a step change, finished, a note its owner wrote, or
    a new owner."""
    if how == "step":
        H._task_event(c, tid, H.KEEPER, "step", "in-progress", "review-1")
    elif how == "finished":
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
    elif how == "note":
        H._task_event(c, tid, H.bot_actor("ops"), "note", None, "Found the draft; two sections left", "")
    else:
        c.execute("UPDATE tasks SET owner_id='bot:cpo' WHERE id=?", (tid,))
        H._task_event(c, tid, H.KEEPER, "owner", "bot:ops", "bot:cpo")


@pytest.mark.parametrize("how", ["step", "finished", "note", "owner"])
def test_a_queued_resume_whose_task_has_moved_on_is_dropped_at_claim_not_handed_out(api, how):
    machine = online(api, "ops")
    tid = new_task(api, "Draft the notes")
    message = queued_resume(api, tid)
    with api.app.state.store.transaction() as c:
        move_on(c, tid, how)
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (message,)).fetchone()[0]
    attempt = claim(api, machine)
    assert attempt is None or attempt["job_id"] != job, "the claim does not hand the stale resume out"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == "canceled", (
            "and it does not stay queued"
        )


def test_a_queued_resume_for_a_task_still_stranded_is_handed_out_at_claim(api):
    machine = online(api, "ops")
    tid = new_task(api, "Draft the notes")
    message = queued_resume(api, tid)
    attempt = claim(api, machine)
    with api.app.state.store.read() as c:
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (message,)).fetchone()[0]
    assert attempt is not None and attempt["job_id"] == job


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


DEV_STEPS = [
    ("Build", "open"),
    ("Review 1", "review"),
    ("QA", "review"),
    ("Merge queue", "review"),
    ("Release", "review"),
    ("Watch 24h", "review"),
    ("Closed", "done"),
]


def dev_ticket(api, title, step="QA", n=412, checks="failing", check=None):
    """A Dev ticket `ops` builds, at `step`, with an open pull request whose CI reads `checks`. `check`: the status of
    a QA check filed under it by `cpo`, or None for no check."""
    from backend import rules

    with api.app.state.store.transaction() as c:
        found = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
        typ = (
            found[0]
            if found
            else H.type_create(
                c, H.KEEPER, "Dev tickets", [{"name": name, "status": status} for name, status in DEV_STEPS]
            )["id"]
        )
        # The QA check on this board: a pass merges, a fail goes back to Build (the Engineering board has no QA step now).
        from backend import step_flow as F

        F.apply(c, typ, {"QA": {"check": "qa", "on_pass": F.MERGE, "on_fail": "Build", "timeout_hours": 2}})
        rules.seed_dev(c)
    url = f"https://github.com/ticoteam/tico/pull/{n}"
    tid = post(
        api,
        "tasks",
        {
            "owner": "ops",
            "title": title,
            "body": "Build " + title + ".",
            "type": typ,
            "step": "Build",
            "links": [url],
        },
    )["id"]
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", tid, step=step)
        c.execute("UPDATE task_links SET state='open', checks=? WHERE task_id=? AND kind='pr'", (checks, tid))
        if check:
            kid = H.task_create(c, "bot:ops", f"QA: PR #{n}", "Check it.", "bot:cpo", parent_id=tid, lint=False)
            c.execute("UPDATE tasks SET status=? WHERE id=?", (check, kid["id"]))
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    return tid


def strand_review(c, tid, hours=2):
    """The ticket left in review since `hours` ago, its runs over, nothing said or changed on it or under it since."""
    then = H.shift(H.now(), hours=-hours)
    row = H.task(c, tid)
    assert row["status"] == "review", row["status"]
    kids = [r[0] for r in c.execute("SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent'", (tid,))]
    for one in [tid, *kids]:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (then, one))
        c.execute("UPDATE messages SET created_at=? WHERE conversation_id=?", (then, H.task(c, one)["conversation_id"]))
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (then, one))
    c.execute("UPDATE task_relations SET created_at=? WHERE to_task=?", (then, tid))
    finish(c, H.actor_id(row["owner_id"]))


def wait_for_reviewer(c, tid):
    """The ticket's check waits for a free reviewer, as check_pools records it: a waiting row and its history line."""
    row = H.task(c, tid)
    wid = H.new_id()
    c.execute(
        "INSERT INTO check_waits(id,ticket_id,step_id,title,state,created_at,updated_at) VALUES(?,?,?,?,'waiting',?,?)",
        (wid, tid, row["step_id"], "QA", H.now(), H.now()),
    )
    H._task_event(c, tid, H.KEEPER, "check_waiting", None, wid, "QA: waiting, no reviewer free")


def test_a_bot_ticket_left_at_a_check_step_after_its_check_is_resumed(api):
    """Nov 8, PR 3055: QA passed, CI was cancelled and the failure never reached the builder; the ticket sat at QA
    for hours. With its check over, no CI going and nothing recorded to wait on, its builder hears of it."""
    online(api, "ops")
    tid = dev_ticket(api, "Pipeline manager 1/3", check="done")
    with api.app.state.store.transaction() as c:
        strand_review(c, tid)
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        assert wakes(c, "ops", tid) == 1 and resumes(c, tid) == ["1"]
        body = c.execute(
            "SELECT m.body FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' AND j.state='queued' "
            "AND json_extract(m.refs,'$.stranded')=1"
        ).fetchone()[0]
        assert "at QA" in body and "re-run CI" in body
        assert SR.handles(c, H.task(c, tid)), "the roundup's quiet line leaves it to the sweep"


def test_a_review_ticket_someone_else_moves_is_not_resumed(api):
    """An open check, CI still going, a merge queued, a check that finished within the hour, a step that is not a
    check (Release), and a review with no flow (a person's look) all wait on someone else."""
    online(api, "ops")
    checking = dev_ticket(api, "Check open", n=1, check="doing")
    ci = dev_ticket(api, "CI going", n=2, checks="pending")
    queued = dev_ticket(api, "Merge queued", n=3, checks="passing")
    fresh = dev_ticket(api, "Check just finished", n=4, check="done")
    release = dev_ticket(api, "Merged", n=5, step="Release", checks="passing")
    look = new_task(api, "A person's look")
    rerun = dev_ticket(api, "Failed, re-run going", n=7, checks="failing")
    pooled = dev_ticket(api, "No reviewer free", n=8, checks="passing")
    plain = dev_ticket(api, "Nothing to wait on", n=6, check="done")
    held = (checking, ci, queued, fresh, release, look, rerun, pooled)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='review' WHERE id=?", (look,))
        c.execute(
            "UPDATE task_links SET detail=json_set(coalesce(detail,'{}'),'$.merge_queue',json('{\"state\":\"queued\"}')) "
            "WHERE task_id=?",
            (queued,),
        )
        c.execute(
            "UPDATE task_links SET detail=json_set(coalesce(detail,'{}'),'$.checks',json(?)) WHERE task_id=?",
            ('{"suite:github-actions#1": "failing", "suite:github-actions#2": "pending"}', rerun),
        )
        wait_for_reviewer(c, pooled)
        for tid in held:
            strand_review(c, tid, hours=3)
        strand_review(c, plain, hours=2)  # the newest: the held ones come first and must not use up the bot's resume
        kid = c.execute("SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent'", (fresh,)).fetchone()[
            0
        ]
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(H.now(), minutes=-20), kid))
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        assert wakes(c, "ops", plain) == 1
        for tid in held:
            assert resumes(c, tid) == [], H.task(c, tid)["title"]
        assert not SR.handles(c, H.task(c, release)) and not SR.handles(c, H.task(c, look))
        assert SR.state(c, H.task(c, fresh), H.shift(H.now(), minutes=SR.REVIEW_QUIET_MINUTES)), (
            "an hour after its check finished, it is stranded"
        )


@pytest.mark.parametrize("how", ["ci", "failing_running", "merge_queue", "step", "check_reopened", "pool_busy"])
def test_a_queued_review_resume_is_dropped_at_claim_once_ci_or_the_merge_queue_takes_over(api, how):
    """The resume waited behind other work; meanwhile CI started again on the head, the pull request joined the merge
    queue, or the ticket left its check step. Its "no CI going" text is stale, so the claim drops it."""
    machine = online(api, "ops")
    tid = dev_ticket(api, "Pipeline manager 1/3", check="done")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued'")
        strand_review(c, tid)
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        message, job = c.execute(
            "SELECT m.id, j.id FROM messages m JOIN jobs j ON j.message_id=m.id WHERE j.bot_id='ops' "
            "AND j.state='queued' AND json_extract(m.refs,'$.stranded')=1"
        ).fetchone()
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), minutes=-30), message))
        if how == "ci":
            c.execute("UPDATE task_links SET checks='pending' WHERE task_id=?", (tid,))
        elif how == "failing_running":  # failing already, a re-run of the failed job still going
            c.execute(
                "UPDATE task_links SET detail=json_set(coalesce(detail,'{}'),'$.checks',json(?)) WHERE task_id=?",
                ('{"suite:github-actions#1": "failing", "suite:github-actions#2": "pending"}', tid),
            )
        elif how == "pool_busy":
            wait_for_reviewer(c, tid)
        elif how == "check_reopened":
            kid = c.execute("SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent'", (tid,)).fetchone()
            c.execute("UPDATE tasks SET status='doing', updated_at=? WHERE id=?", (H.now(), kid[0]))
        elif how == "merge_queue":
            c.execute(
                "UPDATE task_links SET detail=json_set(coalesce(detail,'{}'),'$.merge_queue.state','queued') "
                "WHERE task_id=?",
                (tid,),
            )
        else:
            H._task_event(c, tid, H.KEEPER, "step", "QA", "Build")
    attempt = claim(api, machine)
    assert attempt is None or attempt["job_id"] != job, "the claim does not hand the stale resume out"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == "canceled"


def test_a_queued_review_resume_still_stranded_is_handed_out_at_claim(api):
    machine = online(api, "ops")
    tid = dev_ticket(api, "Pipeline manager 1/3", check="done")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued'")
        strand_review(c, tid)
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        job = c.execute(
            "SELECT j.id FROM messages m JOIN jobs j ON j.message_id=m.id WHERE j.bot_id='ops' "
            "AND j.state='queued' AND json_extract(m.refs,'$.stranded')=1"
        ).fetchone()[0]
    attempt = claim(api, machine)
    assert attempt is not None and attempt["job_id"] == job


def test_resumes_the_claim_dropped_before_they_ran_do_not_stop_resuming(api):
    """CI started again three times while a review resume waited, and each was dropped at claim: the bot had no run,
    so the fourth time the ticket is stranded it is resumed again, not stopped."""
    online(api, "ops")
    tid = dev_ticket(api, "Pipeline manager 1/3", check="done")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued'")
        strand_review(c, tid)
        at = H.now()
        for _ in range(SR.NO_PROGRESS_LIMIT):
            assert SR.run(c, at) == {"resumed": 1, "stopped": 0}
            c.execute(
                "UPDATE jobs SET state='canceled' WHERE bot_id='ops' AND state='queued' AND message_id IN "
                "(SELECT id FROM messages WHERE json_extract(refs,'$.stranded')=1)"
            )
            at = H.shift(at, minutes=SR.REVIEW_QUIET_MINUTES + 1)
        assert SR.handles(c, H.task(c, tid))
        assert SR.run(c, at) == {"resumed": 1, "stopped": 0}
        assert stops(c, tid) == []


def test_a_review_ticket_whose_truncated_checks_all_finished_failing_is_resumed(api):
    """More checks failed than a link keeps: the copy keeps `checks_dropped` "failing" for good, but nothing is going,
    so the ticket is the builder's to move."""
    online(api, "ops")
    tid = dev_ticket(api, "Pipeline manager 1/3", check="done")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE task_links SET detail=json_set(coalesce(detail,'{}'),'$.checks_dropped','failing') WHERE task_id=?",
            (tid,),
        )
        strand_review(c, tid)
        assert SR.run(c, H.now()) == {"resumed": 1, "stopped": 0}
        c.execute(
            "UPDATE task_links SET detail=json_set(detail,'$.checks_dropped_pending',json('true')) WHERE task_id=?",
            (tid,),
        )
        assert SR._ci_going(c, tid), "a check left out of the copy is still going"
