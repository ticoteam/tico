"""A notice that news has overtaken is dropped at claim, not run (backend/execution.obsolete_notices_sql): a legacy per-task
check (check_back, step_status) still queued unstarted, which the task roundup lists instead, and a pull request's checks
result for a head the pull request has moved past, and a step's late warning for a ticket that has left the step. A
legacy check whose turn already started is the rest of that turn, so it runs. A notice that is still news runs."""
import json

from backend.store import H
from backend.tests.test_api import api, assign, claim, ready, runner  # noqa: F401


def fleet(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    return r


def task(c, title="Fix the connection"):
    return H.task_create(c, "human:ana", title, "Build.", "bot:ops")


def job(api, message):
    with api.app.state.store.read() as c:
        return c.execute("SELECT id FROM jobs WHERE message_id=?", (message["id"],)).fetchone()[0]


def queued(c, message):
    return c.execute("SELECT state FROM jobs WHERE message_id=?", (message["id"],)).fetchone()[0]


def test_a_legacy_status_check_is_dropped_at_claim_whether_or_not_its_task_was_touched(api):
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        row, other = task(c), task(c, "Fix the login")
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")         # the new-task notices are not this test
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?,?)", (H.shift(H.now(), minutes=-10), row["id"], other["id"]))
        quiet, kept = (H._wake(c, t, "bot:ops", "Status check: no activity for 2 h.", {"wake": "step_status"},
                               trigger="stall_check") for t in (row, other))
        c.execute("UPDATE messages SET created_at=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-5), quiet["id"], kept["id"]))
        H.task_update(c, "bot:ops", row["id"], note="Pushed the fix.")
    handed = [claim(api, r), claim(api, r)]
    with api.app.state.store.read() as c:
        assert queued(c, quiet) == "canceled"
        assert queued(c, kept) == "canceled", "a legacy check is obsolete whatever its task says: the task roundup lists it"
        start = [r[0] for r in c.execute(
            "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id='ops' AND j.state<>'canceled' "
            "AND json_extract(m.refs,'$.rule')='auto_start'").fetchall()]
    assert [a["job_id"] for a in handed if a] == start, "the only claim is the start notice of the task the owner touched"


def test_checks_news_for_a_head_the_pull_request_moved_past_is_dropped(api):
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        row = task(c)
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,detail) "
                  "VALUES('l1',?,'pr','https://github.example/acme/app/pull/7','open','bot:ops',?,?)",
                  (row["id"], H.now(), json.dumps({"head_sha": "b" * 40})))
        old = H._wake(c, row, "bot:ops", "Checks failed on aaaaaaa.",
                      {"wake": "pr_checks_done", "pr_head": "a" * 40, "pr_result": "fail"}, trigger="pr_checks")
        new = H._wake(c, row, "bot:ops", "Checks failed on bbbbbbb.",
                      {"wake": "pr_checks_done", "pr_head": "b" * 40, "pr_result": "fail"}, trigger="pr_checks")
    handed = [claim(api, r), claim(api, r)]
    with api.app.state.store.read() as c:
        assert queued(c, old) == "canceled"
    assert [a["job_id"] for a in handed if a] == [job(api, new)], "the result for the head it is at still runs"


def test_a_late_warning_for_a_ticket_that_has_left_its_step_is_dropped(api):
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        moved, still = task(c), task(c, "Fix the login")
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?,?)", (H.shift(H.now(), minutes=-10), moved["id"], still["id"]))
        late, kept = (H._wake(c, t, "bot:ops", "Late: it has been at In Progress for 4 h.",
                              {"wake": "blocker", "step_late": True}, trigger="stall_check") for t in (moved, still))
        c.execute("UPDATE messages SET created_at=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-5), late["id"], kept["id"]))
        # Tico moved it on by itself (a verdict, green checks): no person or bot acted, but it is no longer late there.
        H._task_event(c, moved["id"], H.KEEPER, "step", "in-progress", "review-1")
    claim(api, r)
    with api.app.state.store.read() as c:
        assert queued(c, late) == "canceled"
        assert queued(c, kept) != "canceled", "a ticket still at its step keeps its warning"


def test_news_about_a_step_the_ticket_has_left_is_dropped(api):
    from backend.tests.test_dev_ticket_rules import dev_type
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        step = {st["name"]: st["id"] for st in typ["steps"]}
        left, still = (H.task_create(c, "human:ana", title, "Build.", "bot:ops", type=typ["id"], step="QA Fixes",
                                     lint=False) for title in ("Fix the connection", "Fix the login"))
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
        old, kept = (H._wake(c, t, "bot:ops", "Tico moved this ticket to QA Fixes: QA gave changes.",
                             {"handoff": "verdict", "at_step": step["QA Fixes"]}) for t in (left, still))
        # A person merged it by hand: the ticket is past the fixes, and the feedback with it.
        c.execute("UPDATE tasks SET step_id=? WHERE id=?", (step["Pending Release"], left["id"]))
    claim(api, r)
    with api.app.state.store.read() as c:
        assert queued(c, old) == "canceled"
        assert queued(c, kept) != "canceled", "a ticket still at the step keeps its news"


def test_a_review_gate_refusal_is_dropped_once_the_ticket_has_moved(api):
    # Tico makes a refused review move itself once a receipt is posted (backend/move_retry.py): the refusal the Builder
    # was told of is then no longer news. A ticket still held keeps it.
    from backend import review_gate
    from backend.tests.test_dev_ticket_rules import dev_type
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        step = {st["name"]: st["id"] for st in typ["steps"]}
        moved, still = (H.task_create(c, "human:ana", title, "Build.", "bot:ops", type=typ["id"], step="In Progress",
                                      lint=False) for title in ("Fix the connection", "Fix the login"))
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")
        for t in (moved, still):
            review_gate.notify(c, "human:ana", {"task": t["id"], "kind": "receipt", "head": "a" * 40,
                                                "pr": "https://github.example/acme/app/pull/7",
                                                "text": "Review refused: missing test receipt."})
        old, kept = (c.execute("SELECT * FROM messages WHERE json_extract(refs,'$.task')=? "
                               "AND json_extract(refs,'$.review_gate') IS NOT NULL", (t["id"],)).fetchone()
                     for t in (moved, still))
        # The refusal held both (the ticket keeps its step) before it told the Builder.
        c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?,?)", (H.shift(H.now(), minutes=-10), moved["id"], still["id"]))
        c.execute("UPDATE messages SET created_at=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-5), old["id"], kept["id"]))
        H._task_event(c, moved["id"], H.KEEPER, "step", step["In Progress"], step["Pending Release"])
    claim(api, r)
    with api.app.state.store.read() as c:
        assert queued(c, old) == "canceled"
        assert queued(c, kept) != "canceled", "a ticket still held keeps the refusal"


def test_a_legacy_check_whose_turn_already_started_is_kept_to_finish_it(api):
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        under_way, unstarted = task(c), task(c, "Fix the login")
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")         # the new-task notices are not this test
        started = H._wake(c, under_way, "bot:ops", "Status check: no activity for 2 h.", {"wake": "step_status"},
                          trigger="stall_check")
        late = H._wake(c, unstarted, "bot:ops", "Status check: no activity for 2 h.", {"wake": "step_status"},
                       trigger="stall_check")
        # The first run of the started job's turn ended and yielded: its attempt started, and the job waits to continue it.
        job_id = c.execute("SELECT id FROM jobs WHERE message_id=?", (started["id"],)).fetchone()[0]
        runner_id = c.execute("SELECT id FROM runners LIMIT 1").fetchone()[0]
        now = H.now()
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,started_at) "
                  "VALUES('attempt-under-way',?,'ops',?,1,'hash-under-way','running',?,?,?)",
                  (job_id, runner_id, H.shift(now, minutes=30), now, now))
        c.execute("UPDATE jobs SET attempt_id='attempt-under-way', state='queued' WHERE id=?", (job_id,))
    claim(api, r)
    with api.app.state.store.read() as c:
        assert queued(c, late) == "canceled"
        assert queued(c, started) != "canceled", "a turn that started and yielded is the rest of its turn, not a stale check"


def check_back_refusal(api, machine, job_id):
    """job_refusal's verdict on one queued job, as the claim asks it, with nothing busy."""
    from backend import execution
    with api.app.state.store.read() as c:
        runner = c.execute("SELECT * FROM runners_with_state WHERE id=?", (machine["runner_id"],)).fetchone()
        ready = execution.readiness_document(runner["readiness"])
        job = H._rows(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)))[0]
        return execution.job_refusal(c, job, {}, ready, set(), execution.lanes_on(runner), execution.roots_on(runner))


def test_a_check_back_whose_turn_started_or_a_person_resumed_is_not_refused_as_obsolete_while_check_backs_are_off(api):
    # With the check-backs off every check-back is stale (task_check_backs.stale) and the claim refuses it, but the rest of a
    # turn that started, or that a person resumed, runs: obsolete_notices_sql keeps those, and so must the refusal.
    r = fleet(api)
    store = api.app.state.store
    with store.transaction() as c:
        row = task(c)
        c.execute("UPDATE jobs SET state='canceled' WHERE bot_id='ops'")         # the new-task notices are not this test
        back = H._wake(c, row, "bot:ops", "Check on it.", {"wake": "check_back", "task": row["id"]}, trigger="check_back")
        job_id = c.execute("SELECT id FROM jobs WHERE message_id=?", (back["id"],)).fetchone()[0]
    assert check_back_refusal(api, r, job_id) == "obsolete_check_back", "unstarted, with the check-backs off, it is stale"
    now = H.now()
    with store.transaction() as c:
        runner_id = c.execute("SELECT id FROM runners LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,started_at) "
                  "VALUES('attempt-check-back',?,'ops',?,1,'hash-check-back','running',?,?,?)",
                  (job_id, runner_id, H.shift(now, minutes=30), now, now))
        c.execute("UPDATE jobs SET attempt_id='attempt-check-back' WHERE id=?", (job_id,))
    assert check_back_refusal(api, r, job_id) != "obsolete_check_back", "a turn that started is the rest of it: it runs"
    with store.transaction() as c:
        c.execute("UPDATE attempts SET started_at=NULL WHERE id='attempt-check-back'")
        c.execute("INSERT INTO job_recovery(job_id,attempt_id,decision,note,actor,created_at) "
                  "VALUES(?,'attempt-check-back','resume','Carry on.',?,?)", (job_id, H.human_actor("ana"), now))
    assert check_back_refusal(api, r, job_id) != "obsolete_check_back", "a person resumed it: it runs"
