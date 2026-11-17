"""The Strategy Owner writes the strategy draft and publishes only on what the owner told it (backend/strategy.py
owners_words): it names a task of its own the owner wrote on, the history says whose words, and nothing else lets a
bot write it. Acme is the fictional company; Ana is its owner."""

import json

from backend import goals as G
from backend.store import H
from backend.tests.helpers.api import get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.helpers.goal_asks import goal_manager
from backend.tests.helpers.strategy_bot import as_bot

BOT = "strategy-owner-test"
SAID = "We serve small landlords and never sell to enterprise property managers."


def revision(api):
    return get(api, "strategy")["draft"]["revision"]


def asked(api, said=SAID, title="Who we serve"):
    """An add-to-flow question of the Strategy Owner's, which Ana answered on its task."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, G.AUTO_ACTOR, title, "Write it into the strategy.", G.AUTO_ACTOR, lint=False)
        if said:
            H.task_comment(c, "human:ana", task["id"], said)
    return task["id"]


def history(api, kind):
    with api.app.state.store.read() as c:
        return [
            json.loads(r["data"])
            for r in c.execute(
                "SELECT data FROM history_events WHERE subject_type='strategy' AND kind=? ORDER BY created_at", (kind,)
            )
        ]


def draft(api, body, expected=200, token=BOT):
    r = api.put("/api/v2/strategy/draft", json={"revision": revision(api), **body}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_the_strategy_owner_writes_the_draft_only_on_the_owners_words_on_its_task(api):
    goal_manager(api, level=2)
    as_bot(api, "strategy-owner")
    sections = {"serve": "Small landlords.", "not": "Enterprise property managers."}
    assert draft(api, {"sections": sections}, 403)["error"]["code"] == "forbidden"  # no task: it proposes instead
    assert (
        draft(api, {"sections": sections, "task": asked(api, said="", title="What we sell")}, 422)["error"]["code"]
        == "invalid"
    )
    task = asked(api)
    saved = draft(api, {"sections": sections, "task": task})["draft"]
    assert saved["sections"]["serve"] == "Small landlords."
    [entry] = history(api, "draft")
    assert entry["from"] == {"task": task, "person": "human:ana"}


def test_another_bot_naming_a_task_still_only_proposes(api):
    goal_manager(api, level=2)
    task = asked(api)
    as_bot(api, "finance")
    r = api.put(
        "/api/v2/strategy/draft",
        json={"revision": revision(api), "summary": "x", "task": task},
        headers=headers("finance-test"),
    )
    assert r.status_code == 403, r.text


def test_the_strategy_owner_publishes_on_the_owners_words_and_the_note_says_whose(api):
    goal_manager(api, level=2)
    as_bot(api, "strategy-owner")
    post(api, "strategy/bets", {"statement": "Small landlords pay for bots", "status": "active"})
    task = asked(api)
    draft(api, {"sections": {"serve": "Small landlords.", "not": "Enterprise."}, "task": task})
    r = api.post("/api/v2/strategy/publish", json={"note": "", "revision": revision(api)}, headers=headers(BOT))
    assert r.status_code == 403, r.text  # publishing needs the owner's words too
    r = api.post(
        "/api/v2/strategy/publish",
        json={"note": "Who we serve", "revision": revision(api), "task": task},
        headers=headers(BOT),
    )
    assert r.status_code == 200, r.text
    versions = get(api, "strategy/versions")
    latest = (versions if isinstance(versions, list) else versions["versions"])[0]
    assert latest["note"].startswith("Who we serve (From ") and "\u201cWho we serve\u201d" in latest["note"]
    assert history(api, "published")[-1]["from"] == {"task": task, "person": "human:ana"}


def test_a_task_body_never_counts_as_the_owners_words(api):
    """A body is text the owning bot can write or rewrite (filed "for" the owner, or edited after), so it proves
    nothing: only the owner's own comments and answers on the task do."""
    goal_manager(api, level=2)
    as_bot(api, "strategy-owner")
    with api.app.state.store.transaction() as c:
        filed = H.task_create(c, "human:ana", "Update who we serve", SAID, G.AUTO_ACTOR, lint=False)
        empty = H.task_create(c, "human:ana", "Question", "", G.AUTO_ACTOR, lint=False)
    assert (
        draft(api, {"sections": {"serve": "Small landlords."}, "task": filed["id"]}, 422)["error"]["code"] == "invalid"
    )
    r = api.post(f"/api/v2/tasks/{empty['id']}", json={"body": SAID, "version": empty["version"]}, headers=headers(BOT))
    assert r.status_code == 200, r.text
    assert (
        draft(api, {"sections": {"serve": "Small landlords."}, "task": empty["id"]}, 422)["error"]["code"] == "invalid"
    )


def test_an_answer_about_who_we_serve_ends_up_in_that_section_of_the_published_strategy(api):
    """The add-to-flow round trip: the Strategy Owner asks Ana a ❓ on a task of its own, she replies in free text, and it
    writes her answer into "Who we serve" (and the bet it changes), then publishes citing the task. Only that section
    moves: the summary stays as it was."""
    goal_manager(api, level=2)
    as_bot(api, "strategy-owner")
    draft(
        api,
        {"summary": "Acme runs rentals with bots.", "sections": {"serve": "Anyone who rents.", "not": "Hotels."}},
        token="ana-test",
    )
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, G.AUTO_ACTOR, "Who do we serve?", "An add-to-flow question.", G.AUTO_ACTOR, lint=False)
    r = api.post(
        f"/api/v2/tasks/{task['id']}/questions",
        json={"to": "ana", "text": "Who do we serve first?\n\nEvery bet and page starts from it."},
        headers=headers(BOT),
    )
    assert r.status_code == 200, r.text
    post(api, f"tasks/{task['id']}/comments", {"text": SAID})  # her reply clears the ❓
    draft(api, {"sections": {"serve": SAID}, "task": task["id"]})
    r = api.post(
        "/api/v2/strategy/bets",
        json={
            "statement": "Small landlords pay for bots",
            "status": "active",
            "rules_out": "Enterprise sales.",
            "task": task["id"],
        },
        headers=headers(BOT),
    )
    assert r.status_code == 200, r.text
    r = api.post(
        "/api/v2/strategy/publish",
        json={"note": "Who we serve", "revision": revision(api), "task": task["id"]},
        headers=headers(BOT),
    )
    assert r.status_code == 200, r.text
    published = get(api, "strategy")["current"]
    assert published["sections"]["serve"] == SAID
    assert published["summary"] == "Acme runs rentals with bots."
    assert [b["statement"] for b in published["bets"]] == ["Small landlords pay for bots"]
    assert history(api, "bet.add")[-1]["from"] == {"task": task["id"], "person": "human:ana"}


def test_a_one_word_answer_of_the_owners_is_enough_to_write_and_publish(api):
    """Any answer or comment the owner wrote on the task counts, a one-word "Yes" reply included; none still refuses."""
    goal_manager(api, level=2)
    as_bot(api, "strategy-owner")
    with api.app.state.store.transaction() as c:
        task = H.task_create(
            c, G.AUTO_ACTOR, "Small landlords first?", "An add-to-flow question.", G.AUTO_ACTOR, lint=False
        )
    sections = {"serve": "Small landlords.", "not": "Enterprise."}
    assert draft(api, {"sections": sections, "task": task["id"]}, 422)["error"]["code"] == "invalid"  # nothing of hers
    r = api.post(
        f"/api/v2/tasks/{task['id']}/questions",
        json={"to": "ana", "text": "Do we serve small landlords first?\n\nIt sets who we sell to."},
        headers=headers(BOT),
    )
    assert r.status_code == 200, r.text
    post(api, f"tasks/{task['id']}/comments", {"text": "Yes"})  # her one-word reply to the ❓
    draft(api, {"sections": sections, "task": task["id"]})
    r = api.post(
        "/api/v2/strategy/bets",
        json={
            "statement": "Small landlords pay for bots",
            "status": "active",
            "rules_out": "Enterprise.",
            "task": task["id"],
        },
        headers=headers(BOT),
    )
    assert r.status_code == 200, r.text
    r = api.post("/api/v2/strategy/publish", json={"revision": revision(api), "task": task["id"]}, headers=headers(BOT))
    assert r.status_code == 200, r.text
    assert get(api, "strategy")["current"]["sections"]["serve"] == "Small landlords."
