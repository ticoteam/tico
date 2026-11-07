"""Visual specs (backend/spec_approval.py): a spec is a page on a Product roadmap card. Only a person approves it: by hand,
by answering the approval question a bot asks under the card, or through the person's own Assistant on their words.
Approval hands a frozen copy and the spec text to a builder's Dev ticket, which its review and QA children inherit."""

import json

import pytest

from backend import hubdb as H
from backend import spec_approval as SA
from backend.tests.test_files import turn
from backend.tests.test_roadmaps import _dev_tickets, _item, _step
from backend.tests.test_tasks_board import api, get, headers, post  # noqa: F401  (fixture)

HTML = (
    b"<html><head><title>Export</title><script>alert('x')</script></head>"
    b"<body><h1>Exports</h1><p>Pick a range, then <b>download</b>.</p></body></html>"
)
PICKS = [
    {"label": "Approve", "then": "The card moves to Approved and a builder ticket is filed"},
    {"label": "Not yet", "then": "The card stays in Spec"},
]


def publish_spec(api, item, bot="cpo", who="ana-test", data=HTML, spec="true"):
    _, attempt = turn(api, bot, who)
    return api.post(
        "/api/v2/pages",
        params={"name": "exports.html", "task": item["id"], "spec": spec},
        content=data,
        headers={**headers(attempt["token"]), "Content-Type": "text/html"},
    )


def ask_approval(api, item, bot="cpo"):
    """The bot asks the person the approval: a question under the card, on the spec topic, with Approve and Not yet."""
    _, attempt = turn(api, bot, "ana-test")
    return api.post(
        "/api/v2/task-questions",
        json={
            "on": "ana",
            "question": "Approve this exports spec?",
            "why": "Builders start on it once it is approved.",
            "topic": SA.TOPIC,
            "parent": item["id"],
            "options": PICKS,
        },
        headers=headers(attempt["token"]),
    )


def approval_question(api, item):
    """The question task under the card on the spec topic, and the key of its one question."""
    with api.app.state.store.read() as c:
        found = c.execute(
            "SELECT t.id,t.questions FROM tasks t JOIN task_relations r ON r.from_task=t.id "
            "WHERE r.to_task=? AND r.kind='parent' AND t.question_topic=?",
            (item["id"], SA.TOPIC),
        ).fetchone()
    return found[0], json.loads(found[1])[0]["id"]


def answer(api, question, picks, who="ana-test"):
    qid, key = question
    return api.post(
        f"/api/v2/tasks/{qid}/answers",
        json={"target": {"task": qid}, "answers": {key: picks}},
        headers=headers(who),
    )


def step_of(api, item):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT s.name FROM tasks t JOIN task_steps s ON s.id=t.step_id WHERE t.id=?", (item["id"],)
        ).fetchone()[0]


def builder_ticket(api, item):
    """The Dev ticket under the card (row: id, body) and the id of the frozen spec pinned on it; (None, None) before."""
    with api.app.state.store.read() as c:
        ticket = c.execute(
            "SELECT t.id,t.body FROM tasks t JOIN task_types y ON y.id=t.type_id "
            "WHERE t.id IN (SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent') AND y.role='engineering'",
            (item["id"],),
        ).fetchone()
        frozen = (
            c.execute(
                "SELECT f.id FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.task_id=? AND p.spec='frozen'",
                (ticket[0],),
            ).fetchone()
            if ticket
            else None
        )
    return ticket, frozen[0] if frozen else None


def test_styles_are_inlined_before_the_head_closes_and_the_page_keeps_its_markup():
    out = SA.inline_styles(HTML).decode()
    assert out.index("<style>") < out.index("</head>")
    assert ":root" in out  # tokens.css defines the variables the rest of the styles read
    assert "<h1>Exports</h1>" in out
    assert SA.page_text(HTML) == "Exports Pick a range, then download."


def test_a_spec_goes_on_a_product_roadmap_card_only(api):
    general = post(api, "tasks", {"owner": "cpo", "title": "Not a card", "body": "x"})
    r = publish_spec(api, general)
    assert r.status_code == 422, r.text


def test_a_person_approves_a_spec_with_a_page_and_the_builder_ticket_carries_it(api):
    _dev_tickets(api)
    item = _item(api, "Exports", step="Spec")
    assert publish_spec(api, item).status_code == 200
    _step(api, item, "Approved")
    with api.app.state.store.read() as c:
        ticket = c.execute(
            "SELECT t.id,t.body FROM tasks t JOIN task_types y ON y.id=t.type_id "
            "WHERE t.id IN (SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent') AND y.role='engineering'",
            (item["id"],),
        ).fetchone()
        frozen = c.execute(
            "SELECT p.spec FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE f.task_id=?", (ticket[0],)
        ).fetchall()
        assert ticket is not None and "Spec page:" in ticket[1]
        assert "frozen" in [r[0] for r in frozen]


def test_a_bot_cannot_approve_a_spec(api):
    _dev_tickets(api)
    item = _item(api, "Exports bot", step="Spec")
    assert publish_spec(api, item).status_code == 200
    with api.app.state.store.transaction() as c:
        row = H.task(c, item["id"])
        step = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='Approved'", (row["type_id"],)).fetchone()
        with pytest.raises(H.Refused) as refused:
            SA.check(c, "bot:cpo", row, step[0])
        assert "Only a person" in str(refused.value)


def test_approval_without_a_spec_is_refused(api):
    _dev_tickets(api)
    item = _item(api, "No spec yet", step="Spec")
    version = get(api, "tasks/" + item["id"])["task"]["version"]
    r = api.post(
        "/api/v2/tasks/" + item["id"], json={"version": version, "step": "Approved"}, headers=headers("ana-test")
    )
    assert r.status_code >= 400 and "Publish the spec page on this card first" in r.text, r.text


def test_a_review_child_of_the_builder_ticket_gets_the_spec_link_and_text(api):
    _dev_tickets(api)
    item = _item(api, "Exports child", step="Spec")
    assert publish_spec(api, item).status_code == 200
    _step(api, item, "Approved")
    with api.app.state.store.read() as c:
        ticket = c.execute(
            "SELECT t.id FROM tasks t WHERE t.id IN (SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent')",
            (item["id"],),
        ).fetchone()[0]
    child = post(
        api,
        "tasks",
        {
            "owner": "cpo",
            "title": "Review it",
            "body": "Check the diff.",
            "relations": [{"task": ticket, "kind": "parent"}],
        },
    )
    assert "Spec page:" in get(api, "tasks/" + child["id"])["task"]["body"]


def test_an_approve_answer_moves_the_card_as_the_person_asked_and_files_the_builder_ticket(api):
    _dev_tickets(api)
    item = _item(api, "Exports asked", step="Spec")
    assert publish_spec(api, item).status_code == 200
    assert ask_approval(api, item).status_code == 200
    r = answer(api, approval_question(api, item), ["Approve"])
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        moved = c.execute(
            "SELECT 1 FROM history_events e WHERE e.subject_type='task' AND e.subject_id=? AND e.kind='step' "
            "AND e.actor_id='human:ana' AND e.new=(SELECT s.id FROM task_steps s JOIN tasks t ON t.type_id=s.type_id "
            "WHERE t.id=? AND s.name='Approved')",
            (item["id"], item["id"]),
        ).fetchone()
    assert moved is not None
    ticket, frozen = builder_ticket(api, item)
    assert ticket is not None and frozen is not None
    assert f"#/page/{frozen}" in ticket[1] and "Spec page:" in ticket[1]


def test_a_not_yet_answer_leaves_the_card_in_spec(api):
    _dev_tickets(api)
    item = _item(api, "Exports not yet", step="Spec")
    assert publish_spec(api, item).status_code == 200
    assert ask_approval(api, item).status_code == 200
    assert answer(api, approval_question(api, item), ["Not yet"]).status_code == 200
    assert step_of(api, item) == "Spec"
    assert builder_ticket(api, item) == (None, None)


def test_an_approve_with_no_spec_page_is_a_note_on_the_card(api):
    _dev_tickets(api)
    item = _item(api, "Exports no spec", step="Spec")
    assert ask_approval(api, item).status_code == 200
    assert answer(api, approval_question(api, item), ["Approve"]).status_code == 200
    assert step_of(api, item) == "Spec"
    with api.app.state.store.read() as c:
        notes = H.task_comments(c, item["id"])
    assert "Not approved: Publish the spec page on this card first" in str(notes)


def test_the_persons_own_assistant_approves_on_their_words_and_nothing_else_does(api):
    _dev_tickets(api)
    item = _item(api, "Assistant approves", step="Spec")
    assert publish_spec(api, item).status_code == 200
    words = {"for": "human:ana", "cited": "msg-1", "run": "asked"}
    with api.app.state.store.transaction() as c:
        row = H.task(c, item["id"])
        step = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name='Approved'", (row["type_id"],)).fetchone()[
            0
        ]
        via, lent = H.VIA.set("assistant"), H.DELEGATION.set(words)
        try:
            SA.check(c, "human:ana", row, step)  # the person's Assistant, on what the person asked: allowed
        finally:
            H.DELEGATION.reset(lent)
            H.VIA.reset(via)
        for actor, source, record in [
            ("human:ana", "assistant", None),  # the Assistant with no words of the person's behind it
            ("human:ana", "botops", words),  # BotOps with the person's rights
            ("bot:cpo", "", None),  # a bot
        ]:
            via, lent = H.VIA.set(source), H.DELEGATION.set(record)
            try:
                with pytest.raises(H.Refused) as refused:
                    SA.check(c, actor, row, step)
                assert "Only a person" in str(refused.value)
            finally:
                H.DELEGATION.reset(lent)
                H.VIA.reset(via)
