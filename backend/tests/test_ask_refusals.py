"""A bot's question to a person is refused, before anything is written, when its default is a reversible internal step
(do it, say so) or when an open question to the same person already covers it (wait on that one)."""

from backend import question_tasks as QT, team_rules
from backend.store import H
from backend.tests.helpers.api import assign, claim, headers, post, ready, runner, setup_attempt  # noqa: F401  (fixture)


def _ask(api, attempt, question, **more):
    return api.post(
        "/api/v2/task-questions",
        json={"on": "human:ana", "question": question, **more},
        headers=headers(attempt["token"]),
    )


def _refused(r, code):
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code, r.text
    return r.json()["error"]["detail"]


def _open_cards(api):
    with api.app.state.store.read() as c:
        return [r["id"] for r in QT.open_rows(c)]


def _other_bot_asks(api, text, title=None, parent=None, bot="bot:cpo"):
    with api.app.state.store.transaction() as c:
        card = QT.ask_plain(c, bot, "human:ana", text, parent=parent)
        if title:
            c.execute("UPDATE tasks SET title=? WHERE id=?", (title, card["id"]))
        return card["id"]


def test_a_reversible_default_is_refused_with_what_to_do_and_nothing_is_written(api):
    _, _, attempt = setup_attempt(api)
    r = _ask(api, attempt, "Archive the old drafts?", if_no_answer={"do": "Yes", "kind": "reversible"})
    detail = _refused(r, "ask_default_go_ahead")
    assert 'do your default now ("Yes")' in detail and "one line on the task" in detail and "undo" in detail
    assert _open_cards(api) == []


def test_a_send_spend_or_delete_default_and_no_default_are_still_asked(api):
    _, _, attempt = setup_attempt(api)
    for i, kind in enumerate(("send", "spend", "delete")):
        r = _ask(api, attempt, f"Do thing number {i} now?", topic=f"t{i}", if_no_answer={"do": "Yes", "kind": kind})
        assert r.status_code == 200, r.text
    assert _ask(api, attempt, "Pick a quite different colour?", topic="colour").status_code == 200
    assert len(_open_cards(api)) == 4


def test_a_reversible_default_is_asked_when_the_owner_turned_going_ahead_off(api):
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        team_rules.save(c, "human:ana", {"bots_go_ahead": False})
    r = _ask(api, attempt, "Archive the old drafts?", if_no_answer={"do": "Yes", "kind": "reversible"})
    assert r.status_code == 200, r.text


def test_a_question_on_a_task_that_already_has_one_open_is_refused_and_names_it(api):
    # A chat makes no task, so the bot is handed one and asks while working on it.
    r = runner(api, "ana")
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    mine = post(api, "tasks", {"owner": "ops", "title": "Summarize", "body": "Please summarize the current work."})
    with api.app.state.store.transaction() as c:  # cpo asked for it, so cpo may file its own question under it
        c.execute("UPDATE tasks SET requester_id='bot:cpo' WHERE id=?", (mine["id"],))
    attempt = claim(api, r)
    other = _other_bot_asks(api, "Which vendor should we use for payroll?", title="Payroll vendor", parent=mine)
    detail = _refused(_ask(api, attempt, "Is the weather nice?"), "ask_duplicate")
    assert other in detail and "Payroll vendor" in detail and f"blocked_by {other}" in detail
    assert _open_cards(api) == [other]


def test_nearly_the_same_words_from_another_bot_is_refused_and_a_different_subject_is_not(api):
    _, _, attempt = setup_attempt(api)
    _other_bot_asks(api, "Can the Assistant read and draft Chris's mailbox?")
    same = _ask(api, attempt, "Can the assistant read and draft chris's mailbox", parent=None)
    assert "already has an open question that covers this" in _refused(same, "ask_duplicate")
    different = _ask(api, attempt, "Which colour should the new logo be?")
    assert different.status_code == 200, different.text


def test_a_person_never_meets_either_refusal(api):
    with api.app.state.store.transaction() as c:
        QT.ask_plain(c, "bot:cpo", "human:ana", "Can the Assistant read my mailbox?")
    # A person's own comment or question is no bot ask: the route is a bot's only.
    r = api.post("/api/v2/task-questions", json={"on": "human:ana", "question": "x?"}, headers=headers("ben-test"))
    assert r.status_code == 403, r.text
