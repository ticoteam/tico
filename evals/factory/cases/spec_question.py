"""A visual spec is approved by a person's answer to a question (backend/spec_approval.py, backend/question_tasks.py).

At hour 1 a builder publishes its spec page on its Product roadmap card while it runs a turn. At hour 2 it asks ana the
approval as a question task under the card, on the spec topic, with two picks: Approve and Not yet. At hour 3 ana
answers Approve. The question closes done with that answer, the card moves to Approved as ana, and the handoff files a
Dev ticket with the frozen, pinned spec. The builder never moves the card itself. The board keeps moving its tickets
through the checks the whole time."""

import json

from evals.factory.cases.spec_handoff import (
    SPEC,
    builder_refused,
    spec_published,
    spec_published_once,
    ticket_frozen_and_pinned,
)
from evals.factory.scenarios import Scenario, standard, tickets
from evals.factory.sim import at
from evals.factory.world import START

ASK_AT, ANSWER_AT = at(2), at(3)
SPEC_AT = at(1)
ROADMAP = "Product roadmap"
TOPIC = "spec-approval"
PICKS = [
    {"label": "Approve", "then": "The card moves to Approved and a builder ticket is filed"},
    {"label": "Not yet", "then": "The card stays in Spec"},
]
QUESTION = "Approve this exports spec?"


def setup(run, w, team):
    tickets(run, w, 4)
    run.builder = team.builders[0]
    card = w.post(
        "tasks",
        {
            "owner": run.builder.slug,
            "title": "Exports question",
            "body": "Spec the exports.",
            "type": ROADMAP,
            "step": "Spec",
        },
    )
    run.card = card.get("task", card)["id"]
    run.asked = None  # (status code, reply) of the builder's question to ana
    run.answered = None  # (status code, reply) of ana's answer


def during(run, w, team, i):
    now = w.now()
    if now < START + SPEC_AT or run.answered is not None:
        return
    if not spec_published(run):
        if run.builder.running and run.builder.attempt:
            w.request(
                "post",
                "pages",
                {"name": "exports.html", "text": SPEC, "task": run.card, "spec": True},
                token=run.builder.attempt["token"],
            )
        return
    if run.asked is None:
        if now >= START + ASK_AT and run.builder.running and run.builder.attempt:
            run.asked = w.request(
                "post",
                "task-questions",
                {
                    "on": "ana",
                    "question": QUESTION,
                    "why": "Builders start on it once it is approved.",
                    "topic": TOPIC,
                    "parent": run.card,
                    "options": PICKS,
                },
                token=run.builder.attempt["token"],
            )
        return
    if now >= START + ANSWER_AT:
        found = question_of(run)
        if found is not None:
            qid, key = found
            run.answered = w.request(
                "post",
                "tasks/" + qid + "/answers",
                {"target": {"task": qid}, "answers": {key: ["Approve"]}},
                token="ana-test",
            )


def question_of(run):
    """The approval question under the card on the spec topic: its task id and the key of its one question, or None."""
    rows = run.w.sql(
        "SELECT id,questions FROM tasks WHERE question_topic=? AND id IN "
        "(SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent') ORDER BY created_at LIMIT 1",
        (TOPIC, run.card),
    )
    if not rows:
        return None
    return rows[0]["id"], json.loads(rows[0]["questions"])[0]["id"]


def asked_under_card(run):
    """The builder asked ana the approval: a question task under the card, on the spec topic, owned by ana."""
    if run.asked is None:
        return False, "no question sent"
    status, _ = run.asked
    owners = run.w.sql(
        "SELECT t.owner_id FROM tasks t WHERE t.question_topic=? AND t.id IN "
        "(SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent')",
        (TOPIC, run.card),
    )
    ok = status == 200 and bool(owners) and all(row["owner_id"] == "human:ana" for row in owners)
    return ok, f"HTTP {status}, {len(owners)} approval questions under the card"


def approved_as_ana(run):
    """ana's Approve went through, and it moved the card to Approved as ana."""
    if run.answered is None:
        return False, "no answer sent"
    status, _ = run.answered
    moves = run.w.sql(
        "SELECT 1 FROM history_events e WHERE e.subject_type='task' AND e.subject_id=? AND e.kind='step' "
        "AND e.actor_id='human:ana' AND e.new=(SELECT s.id FROM task_steps s JOIN tasks t ON t.type_id=s.type_id "
        "WHERE t.id=e.subject_id AND s.name='Approved')",
        (run.card,),
    )
    return status == 200 and bool(moves), f"HTTP {status}, {len(moves)} moves by ana to Approved"


def answer_closed(run):
    """The question closed done, with Approve recorded as its answer."""
    found = question_of(run)
    if found is None:
        return False, "no approval question"
    row = run.w.sql("SELECT status,answer FROM tasks WHERE id=?", (found[0],))
    if not row:
        return False, "the question is gone"
    answer = row[0]["answer"] or ""
    return row[0]["status"] == "done" and "Approve" in answer, f"status {row[0]['status']}"


SCENARIOS = [
    Scenario(
        "spec_question",
        "A builder publishes its spec page on its Product roadmap card, asks ana the approval as a question under the "
        "card, and ana answers Approve: the card moves to Approved as ana, and the Dev ticket it files carries a frozen, "
        "pinned copy of the spec. The builder never moves the card itself.",
        setup=setup,
        during=during,
        checks=standard(
            ("the spec page is on the card, once", "Spec question", spec_published_once),
            ("the builder asks ana the approval under the card", "Spec question", asked_under_card),
            ("ana's Approve moves the card to Approved, as ana", "Spec question", approved_as_ana),
            ("the question closes done with Approve", "Spec question", answer_closed),
            ("a bot never moves a spec to Approved", "Spec question", builder_refused),
            ("the Dev ticket carries a frozen, pinned spec", "Spec question", ticket_frozen_and_pinned),
        ),
    ),
]
