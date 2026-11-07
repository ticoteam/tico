"""A person reviews a Design pipeline's Mockup requirement from the review queue (backend/pipeline_review.py).

At hour 1 three design tickets wait at Review with a mockup attached and the bot's check unsure, so the mockup is
ana's. She approves the first through the API: it moves to Shipped. She opens the second, a push lands on it, and her
Approve (of what she saw) is refused, then goes through on the new queue item. She sends the third back with a note:
it leaves her queue, and when a new attachment arrives it returns to her at once, with no bot pass in between."""

import time

from backend import hubdb as H
from evals.factory.scenarios import Scenario, at, standard, tickets
from evals.factory.world import START

ANA = "human:ana"
AT = at(1)
STEPS = [
    {"name": "Making", "status": "doing"},
    {"name": "Review", "status": "doing"},
    {"name": "Shipped", "status": "done"},
]


def setup(run, w, team):
    tickets(run, w, 2)
    run.pr = {"done": False}


def path(task, requirement, verb):
    return f"pipelines/tasks/{task}/requirements/{requirement}/{verb}"


def queue(w):
    return w.get("pipelines/review/queue")["items"]


def item(w, task, requirement):
    return next((i for i in queue(w) if i["task_id"] == task and i["requirement_id"] == requirement), None)


def build(w):
    """The Design pipeline, its Mockup requirement and three tickets with a mockup each and the bot unsure."""
    with w.store.transaction() as c:
        pipeline = H.type_create(c, ANA, "Design", STEPS, mover=True)["id"]
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, pipeline))
        stage = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='Review'", (pipeline,)).fetchone()[0]
        now = H.now()
        c.execute(
            "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, reviewed_by, reviewer_id, "
            "created_at, updated_at) VALUES ('r-mockup',?,?,'Mockup','attachment','bot_then_person',?,?,?)",
            (pipeline, stage, ANA, now, now),
        )
        tasks = [
            H.task_create(c, ANA, f"Logo {n}", "", "ana", type=pipeline, step="Review", lint=False)["id"]
            for n in (1, 2, 3)
        ]
        for task in tasks:
            attach(c, task, "d1")
            c.execute(
                "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, work_version, judge, "
                "verdict, created_at) VALUES (?,?,?,'r-mockup',(SELECT work_revision FROM tasks WHERE id=?),'llm',"
                "'unsure',?)",
                (f"d-{task}", task, stage, task, H.now()),
            )
    return pipeline, tasks


def attach(c, task, digest, later=0):
    c.execute(
        "INSERT INTO requirement_attachments (id, task_id, stage_requirement_id, source, url, digest, work_version, "
        "created_at) VALUES (?,?,'r-mockup','link','https://example.test/mockup',?,"
        "(SELECT work_revision FROM tasks WHERE id=?),?)",
        (f"a-{task}-{digest}-{time.monotonic_ns()}", task, digest, task, H.shift(H.now(), seconds=later)),
    )


def step_name(w, task):
    return w.sql("SELECT s.name FROM tasks t JOIN task_steps s ON s.id=t.step_id WHERE t.id=?", (task,))[0]["name"]


def during(run, w, team, i):
    if run.pr["done"] or w.now() < START + AT:
        return
    run.pr["done"] = True
    pipeline, (first, second, third) = build(w)
    seen = {t: item(w, t, "r-mockup") for t in (first, second, third)}
    run.pr["visible"] = all(seen.values())
    # 1. A plain Approve of what she saw.
    got = w.post(path(first, "r-mockup", "verb".replace("verb", "approve")), body(seen[first]))
    run.pr["approved"] = bool(got.get("advanced")) and step_name(w, first) == "Shipped"
    # 2. A push lands after she opened the second one.
    with w.store.transaction() as c:
        c.execute("UPDATE tasks SET work_revision=work_revision+1 WHERE id=?", (second,))
    status, reply = w.request("post", path(second, "r-mockup", "approve"), body(seen[second]))
    run.pr["stale_status"] = status
    run.pr["stale_code"] = (reply.get("error") or {}).get("code") if isinstance(reply, dict) else None
    run.pr["still_there"] = step_name(w, second) == "Review"
    # 3. Send the third back, then a new attachment arrives.
    w.post(path(third, "r-mockup", "send-back"), {"note": "The logo is too small"})
    run.pr["left"] = item(w, third, "r-mockup") is None
    llm_before = decisions(w, third)
    with w.store.transaction() as c:  # the sim's clock is fixed within a tick: the new work comes a second later
        attach(c, third, "d2", later=1)
    back = item(w, third, "r-mockup")
    run.pr["returned"] = bool(back) and back["attachment_digest"] == "d2" and decisions(w, third) == llm_before
    run.pr["ids"] = (second, third)


def decisions(w, task):
    return w.sql("SELECT count(*) n FROM stage_decisions WHERE task_id=? AND judge IN ('llm','bot')", (task,))[0]["n"]


def body(it):
    """What the queue item tells the reviewer: the work, the attachment and the requirement version they saw."""
    return {
        "work_version": it["work_version"],
        "attachment_digest": it["attachment_digest"],
        "requirement_version": it["requirement_version"],
    }


def reviewed(run):
    ok = run.pr.get("done") and run.pr.get("visible")
    return bool(ok), "the three mockups were in ana's queue" if ok else "not all three were in her queue"


def approve_advances(run):
    return bool(run.pr.get("approved")), "Approve moved the first ticket to Shipped"


def stale_refused(run):
    ok = run.pr.get("stale_status") == 422 and run.pr.get("stale_code") == "stale" and run.pr.get("still_there")
    return bool(ok), (
        f"Approve after a push answered {run.pr.get('stale_status')} {run.pr.get('stale_code')}, ticket still at Review"
    )


def send_back_returns(run):
    ok = run.pr.get("left") and run.pr.get("returned")
    return bool(ok), f"left the queue: {run.pr.get('left')}, back with no bot pass: {run.pr.get('returned')}"


SCENARIOS = [
    Scenario(
        "pipeline_review",
        "A person approves a Design mockup from the review queue, an Approve after a push is refused, "
        "and a sent-back one returns on its next attachment without a bot pass.",
        setup=setup,
        during=during,
        hours=3,
        drain_hours=2,
        checks=standard(
            ("the mockups are in the reviewer's queue", None, reviewed),
            ("Approve advances the ticket", None, approve_advances),
            ("an Approve of stale work is refused", None, stale_refused),
            ("a sent-back item returns on new work with no bot pass", None, send_back_returns),
        ),
    ),
]
