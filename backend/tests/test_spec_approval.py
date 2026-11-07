"""Visual specs (backend/spec_approval.py): a spec is a page on a Product roadmap card, only a person approves it, and
approval hands a frozen copy and the spec text to a builder's Dev ticket, which its review and QA children inherit."""

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


def publish_spec(api, item, bot="cpo", who="ana-test", data=HTML, spec="true"):
    _, attempt = turn(api, bot, who)
    return api.post(
        "/api/v2/pages",
        params={"name": "exports.html", "task": item["id"], "spec": spec},
        content=data,
        headers={**headers(attempt["token"]), "Content-Type": "text/html"},
    )


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
