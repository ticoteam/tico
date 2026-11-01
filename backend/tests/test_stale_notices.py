"""A notice that news has overtaken is dropped at claim, not run (backend/execution.obsolete_notices_sql): a status
check of a quiet task its owner has acted on since, and a pull request's checks result for a head the pull request
has moved past, and a step's late warning for a ticket that has left the step. A notice that is still news runs."""
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


def test_a_status_check_the_owner_has_acted_on_since_is_dropped(api):
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        row, other = task(c), task(c, "Fix the login")
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")         # the new-task notices are not this test
        c.execute("UPDATE task_events SET ts=? WHERE task_id IN (?,?)", (H.shift(H.now(), minutes=-10), row["id"], other["id"]))
        quiet, kept = (H._wake(c, t, "bot:ops", "Status check: no activity for 2 h.", {"wake": "step_status"},
                               trigger="stall_check") for t in (row, other))
        c.execute("UPDATE messages SET created_at=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-5), quiet["id"], kept["id"]))
        H.task_update(c, "bot:ops", row["id"], note="Pushed the fix.")
    handed = [claim(api, r), claim(api, r)]
    with api.app.state.store.read() as c:
        assert queued(c, quiet) == "cancelled"
    assert [a["job_id"] for a in handed if a] == [job(api, kept)], "a task nobody touched still gets its check"


def test_checks_news_for_a_head_the_pull_request_moved_past_is_dropped(api):
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        row = task(c)
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")
        c.execute("INSERT INTO task_links(id,task_id,kind,url,state,added_by,created_at,detail) "
                  "VALUES('l1',?,'pr','https://github.example/acme/app/pull/7','open','bot:ops',?,?)",
                  (row["id"], H.now(), json.dumps({"head_sha": "b" * 40})))
        old = H._wake(c, row, "bot:ops", "Checks failed on aaaaaaa.",
                      {"wake": "pr_checks_done", "pr_head": "a" * 40, "pr_result": "fail"}, trigger="pr_checks")
        new = H._wake(c, row, "bot:ops", "Checks failed on bbbbbbb.",
                      {"wake": "pr_checks_done", "pr_head": "b" * 40, "pr_result": "fail"}, trigger="pr_checks")
    handed = [claim(api, r), claim(api, r)]
    with api.app.state.store.read() as c:
        assert queued(c, old) == "cancelled"
    assert [a["job_id"] for a in handed if a] == [job(api, new)], "the result for the head it is at still runs"


def test_a_late_warning_for_a_ticket_that_has_left_its_step_is_dropped(api):
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        moved, still = task(c), task(c, "Fix the login")
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")
        c.execute("UPDATE task_events SET ts=? WHERE task_id IN (?,?)", (H.shift(H.now(), minutes=-10), moved["id"], still["id"]))
        late, kept = (H._wake(c, t, "bot:ops", "Late: it has been at In Progress for 4 h.",
                              {"wake": "blocker", "step_late": True}, trigger="stall_check") for t in (moved, still))
        c.execute("UPDATE messages SET created_at=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-5), late["id"], kept["id"]))
        # Tico moved it on by itself (a verdict, green checks): no person or bot acted, but it is no longer late there.
        H._task_event(c, moved["id"], H.KEEPER, "step", "in-progress", "review-1")
    claim(api, r)
    with api.app.state.store.read() as c:
        assert queued(c, late) == "cancelled"
        assert queued(c, kept) != "cancelled", "a ticket still at its step keeps its warning"


def test_news_about_a_step_the_ticket_has_left_is_dropped(api):
    from backend.tests.test_dev_ticket_rules import dev_type
    r = fleet(api)
    with api.app.state.store.transaction() as c:
        typ = dev_type(c)
        step = {st["name"]: st["id"] for st in typ["steps"]}
        left, still = (H.task_create(c, "human:ana", title, "Build.", "bot:ops", type=typ["id"], step="QA Fixes",
                                     lint=False) for title in ("Fix the connection", "Fix the login"))
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")
        old, kept = (H._wake(c, t, "bot:ops", "Tico moved this ticket to QA Fixes: QA gave changes.",
                             {"handoff": "verdict", "at_step": step["QA Fixes"]}) for t in (left, still))
        # A person merged it by hand: the ticket is past the fixes, and the feedback with it.
        c.execute("UPDATE tasks SET step_id=? WHERE id=?", (step["Pending Release"], left["id"]))
    claim(api, r)
    with api.app.state.store.read() as c:
        assert queued(c, old) == "cancelled"
        assert queued(c, kept) != "cancelled", "a ticket still at the step keeps its news"


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
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot='ops'")
        for t in (moved, still):
            review_gate.notify(c, "human:ana", {"task": t["id"], "kind": "receipt", "head": "a" * 40,
                                                "pr": "https://github.example/acme/app/pull/7",
                                                "text": "Review refused: missing test receipt."})
        old, kept = (c.execute("SELECT * FROM messages WHERE json_extract(refs,'$.task')=? "
                               "AND json_extract(refs,'$.review_gate') IS NOT NULL", (t["id"],)).fetchone()
                     for t in (moved, still))
        # The refusal held both (the ticket keeps its step) before it told the Builder.
        c.execute("UPDATE task_events SET ts=? WHERE task_id IN (?,?)", (H.shift(H.now(), minutes=-10), moved["id"], still["id"]))
        c.execute("UPDATE messages SET created_at=? WHERE id IN (?,?)", (H.shift(H.now(), minutes=-5), old["id"], kept["id"]))
        H._task_event(c, moved["id"], H.KEEPER, "step", step["In Progress"], step["Pending Release"])
    claim(api, r)
    with api.app.state.store.read() as c:
        assert queued(c, old) == "cancelled"
        assert queued(c, kept) != "cancelled", "a ticket still held keeps the refusal"
