"""A bot's ❓ to a person that Tico answers itself (Tasks spec "Tico answers what it already knows",
backend/ask_known.py): which repository, whether the bot can push to it, whether it can view the app. A yes asks
nobody; a "no" goes to the person with a plain "Tico checked:" sentence; anything else goes on unchanged."""

import pytest

from backend import ask_known as K
from backend.tests.helpers.api import get, headers
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    not_a_friday,
    ruled,
    sql,
    task_for_ops,
)

REPO = "acme/app"


def ask(api, task, line, token="ops-test", to="ana", **more):
    body = {"to": to, "text": line, **more}
    return api.post(f"/api/v2/tasks/{task['id']}/questions", json=body, headers=headers(token))


def asked(api, r):
    """The ❓'s text as the person reads it, or None when nobody was asked."""
    assert r.status_code == 200, r.text
    if "question" not in r.json():
        return None
    return sql(api, "SELECT body FROM messages WHERE id=?", (r.json()["comment"]["id"],))[0]["body"]


def history(api, kind):
    return sql(api, "SELECT subject_id, actor_id, new FROM history_events WHERE kind=? ORDER BY rowid", (kind,))


@pytest.fixture
def facts(ruled, monkeypatch):
    """The task's repository and the bot's GitHub access, as a test sets them; app view is the real setting."""
    api, _ = ruled
    state = {"repos": [REPO], "access": "write"}
    monkeypatch.setattr("backend.stage_inputs.repositories", lambda c, task_id, readable: list(state["repos"]))

    def access(c, bot, org, reads=None):
        level = state["access"]
        return {"effective": [{"full_name": REPO, "access": level}] if level else []}

    monkeypatch.setattr("backend.repositories.access", access)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,installation_id,html_url,ciphertext,"
            "nonce,created_at,created_by) VALUES('app',1,'s','c','acme',0,NULL,'',x'00',x'00','t','ana')"
        )
    return api, state


def app_view(api, on):
    with api.app.state.store.transaction() as c:
        if on:
            c.execute(
                "INSERT INTO bot_app_view(bot, turned_on_by, turned_on_at) VALUES('ops','human:ana','2026-11-15T10:00:00Z')"
            )
        else:
            c.execute("DELETE FROM bot_app_view WHERE bot='ops'")


# ---- matching (plain code) -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,want",
    [
        ("Which repo is this in?", [K.REPO]),
        ("Which repo is this in, and do I have write access?", [K.REPO, K.ACCESS]),
        ("Do I have write access to acme/app?", [K.ACCESS]),
        ("Can I push to the repository?", [K.ACCESS]),
        ("Can you turn on app view for me?", [K.APP_VIEW]),
        ("Is app view on for me? Thanks!", [K.APP_VIEW]),
        ("Which of these two designs should I build?", None),
        ("Which repo, and which of these designs?", None),  # a known topic mixed with another question
        ("Should the access screen show dates?", None),  # about the product, not this bot
        ("Should the app view page have a dark mode?", None),
        ("Do I have read/write access?", [K.ACCESS]),
        ("Which repository?", [K.REPO]),
        # Not about the repository or the app itself: the person's to answer (Review 1).
        ("Do I have access to the Stripe dashboard?", None),
        ("Can I get access to the Google Analytics account?", None),
        ("Do I have permission to send this email to the customer?", None),
        ("Do I have your permission to spend $40 on the domain?", None),
        ("Do I have access to the production database?", None),
        ("Do I have rights to publish this post?", None),
        ("Which repo should the new Recorder plugin live in?", None),
        ("Can I see the app's billing page in staging?", None),
        # A go-ahead, not a fact about the repository (Review 2).
        ("Can I write a reply to the customer?", None),
        ("Can I push the release tag?", None),
        ("Do I have merge rights on this PR?", None),
        ("Can we commit to the Friday date?", None),
        ("Can I push to main directly?", None),
        ("Can I commit the migration that drops the users table?", None),
        ("Can I write the blog post about this repo?", None),
        ("Can I commit to shipping this by Friday?", None),
        ("Can you remove my write access to acme/app?", None),
        ("Can I get write access to the Stripe account?", None),
        ("Which repo do I put the new plugin in?", None),
        ("Can I push?", [K.ACCESS]),
        ("Can I push to acme/app?", [K.ACCESS]),
        ("Do I have write access to the repo?", [K.ACCESS]),
    ],
)
def test_a_question_is_known_only_when_every_part_of_it_is_a_known_topic(line, want):
    assert K.topics(line) == want


# ---- a yes asks nobody (AC-1 to AC-3, AC-6, AC-11) -----------------------------------------------------------------


def test_which_repository_is_answered_with_the_repository_and_nobody_is_asked(facts):
    api, _ = facts
    task = task_for_ops(api)
    r = ask(api, task, "Which repo is this in?")
    assert asked(api, r) is None
    out = r.json()
    assert out["asked"] is False
    assert f"Repository: {REPO} (tico task worktree add {REPO})." in out["answered_by_tico"]
    assert "Next time: tico task show" in out["answered_by_tico"] and "--not-known" in out["answered_by_tico"]
    assert sql(api, "SELECT 1 FROM question_marks") == []
    assert sql(api, "SELECT 1 FROM refusals WHERE actor LIKE '%ops%'") == []
    assert history(api, "ask_known") == [{"subject_id": task["id"], "actor_id": "bot:ops", "new": "repository"}]


def test_write_access_is_answered_from_the_bots_github_access(facts):
    api, _ = facts
    task = task_for_ops(api)
    r = ask(api, task, "Which repo is this in, and do I have write access?")
    assert asked(api, r) is None
    assert "Write access to acme/app: yes, through the GitHub App." in r.json()["answered_by_tico"]


def test_app_view_on_is_answered_with_who_turned_it_on_read_at_the_moment_of_the_ask(facts):
    api, _ = facts
    task = task_for_ops(api)
    app_view(api, False)
    assert asked(api, ask(api, task, "Can I view the app?")), "off: the person is asked"
    app_view(api, True)  # turned on during the run
    task = task_for_ops(api, title="Take the screenshots")
    r = ask(api, task, "Can I view the app?")
    assert asked(api, r) is None
    assert "App view: on, turned on by" in r.json()["answered_by_tico"]


# ---- a "no" goes to the person, with what Tico checked (AC-5) -------------------------------------------------------


@pytest.mark.parametrize(
    "line,access,said",
    [
        ("Can you turn on app view for me?", "write", "this bot can't see the app yet"),
        ("Do I have write access to acme/app?", "read", "can read acme/app but can't push to it"),
        ("Do I have write access to acme/app?", "", "this bot can't reach acme/app yet"),
    ],
)
def test_a_no_reaches_the_person_with_one_plain_tico_checked_sentence(facts, line, access, said):
    api, state = facts
    state["access"] = access
    task = task_for_ops(api)
    text = asked(api, ask(api, task, line))
    assert text and text.startswith(line) and "\n\nTico checked: " in text and said in text
    assert "bot_app_view" not in text and "ops" not in text, "plain words: no setting names or ids"
    assert history(api, "ask_known") == []


# ---- everything else goes on unchanged (AC-7 to AC-10) -------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Which of these two designs should I build?",
        "Which repo, and which of these designs should I build?",
        "Do I have your permission to spend $40 on the domain?",
        "Can I push the release tag?",
    ],
)
def test_another_question_or_a_mixed_one_reaches_the_person_unchanged(facts, line):
    api, _ = facts
    task = task_for_ops(api)
    assert asked(api, ask(api, task, line)) == line
    assert history(api, "ask_known") == []


def test_a_repository_tico_cannot_name_leaves_the_question_to_the_person(facts):
    api, state = facts
    state["repos"] = ["acme/app", "acme/site"]
    task = task_for_ops(api)
    assert asked(api, ask(api, task, "Which repo is this in?")) == "Which repo is this in?"


def test_a_repository_tico_does_not_know_is_left_to_the_person_unchanged(facts):
    api, _ = facts
    task = task_for_ops(api)
    line = "Do I have write access to acme/other?"
    assert asked(api, ask(api, task, line)) == line, "no Tico checked sentence about a repository Tico can't see"


def test_not_known_sends_an_answered_question_to_the_person_and_is_counted(facts):
    api, _ = facts
    task = task_for_ops(api)
    assert asked(api, ask(api, task, "Which repo is this in?", not_known=True)) == "Which repo is this in?"
    assert history(api, "ask_known") == []
    assert [h["subject_id"] for h in history(api, "ask_not_known")] == [task["id"]]


def test_a_slash_word_is_not_taken_for_a_repository(facts):
    api, _ = facts
    task = task_for_ops(api)
    r = ask(api, task, "Do I have read/write access?")
    assert asked(api, r) is None
    assert "Write access to acme/app: yes" in r.json()["answered_by_tico"]


def test_a_bots_question_to_a_bot_is_never_answered_by_tico(facts):
    api, _ = facts
    task = task_for_ops(api)
    r = ask(api, task, "Which repo is this in?", to="finance")
    assert "answered_by_tico" not in r.text
    assert history(api, "ask_known") == []


def test_an_older_clients_question_on_the_asks_route_is_answered_too(facts):
    """A `tico` from before ❓ marks asks on the asks route, which asks it as a ❓ (task_moves_routes.task_ask). Its why,
    folded into the ❓'s words, is another part of the question: that one goes to the person."""
    api, _ = facts
    task = task_for_ops(api)
    body = {"to": "ana", "line": "Which repo is this in?", "version": task["version"]}
    r = api.post(f"/api/v2/tasks/{task['id']}/asks", json=body, headers=headers("ops-test"))
    assert r.status_code == 200 and r.json().get("asked") is False, r.text
