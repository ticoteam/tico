"""The Updates chat (backend/updates_chat.py; spec: Strategy Bot v3, sections 10.8 and 10.9 tests 25, 28 and 29): a
question asked from Updates is a turn of the person's own chat bot, in the same chat as their menu tab, handed the
recent updates that person may read. The server leaves out every update they may not see, and any that links a task,
chat or bot they may not open, so the bot can never quote or link one; each comes ranked by the default order and with
its link."""

import json

from backend import updates
from backend.store import H
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401
from backend.tests.test_strategy_bot_chat import strategy_bot


def bot_id(c, slug):
    """A bot's id by its slug: updates are written and read by the bot's id."""
    return H.resolve_bot(c, slug)["id"]


def ask(api, text, token, place="updates"):
    return post(api, "assistant/messages", {"text": text, **({"place": place} if place else {})}, token=token)


def claim(api, r, bot, text, token, place="updates"):
    sent = ask(api, text, token, place)
    assert sent["fast"] is False, "a question from Updates is always a turn"
    attempt = post(api, "jobs/claim", {"bot": bot}, token=r["token"])["attempt"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, token=r["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Done", "last_seq": 0},
         token=r["token"])
    return sent, attempt


def test_a_question_from_updates_is_handed_only_the_updates_the_person_may_read_and_open(api, monkeypatch):
    from backend.tests.test_api import restrict
    from backend.tests.test_bot_contact import bot
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    for slug, name in (("sales", "Sales"), ("fixer", "Fixer")):
        bot(api, slug, name)
    hidden = post(api, "tasks", {"title": "Quietly let the agency go", "body": "See notes", "owner": "fixer", "private": True})
    with api.app.state.store.transaction() as c:
        restrict(c, "finance", people=["ana"], bots=["coo"])
        updates.post(c, bot_id(c, "sales"), "- Closed the Acme renewal", day="2026-09-29")
        updates.post(c, bot_id(c, "finance"), "- Runway is nine months", day="2026-09-29")
        updates.post(c, bot_id(c, "fixer"), f"- Sorted out [the agency](#/task/{hidden['id']})", day="2026-09-29")
        updates.post(c, bot_id(c, "fixer"), f"- Went through {hidden['id']} with the team", day="2026-09-28")
    r = runner(api)
    assign(api, r, "coo")
    ready(api, r, ["coo"])
    # The menu tab's own question is handed no updates; it reads them only as the person, like any reader.
    menu, plain = claim(api, r, "coo", "What's the top update today?", "cara-test", place=None)
    assert plain["updates"] is None
    sent, attempt = claim(api, r, "coo", "What's the top update today?", "cara-test")
    assert sent["message"]["conversation_id"] == menu["message"]["conversation_id"], "the same chat as her menu tab"
    handed = attempt["updates"]
    assert handed["today"] == "2026-09-29"
    assert [u["text"] for u in handed["updates"]] == ["- Closed the Acme renewal"]
    assert handed["updates"][0]["link"] == f"#/updates?kind=daily&day=2026-09-29&update={handed['updates'][0]['id']}"
    text = json.dumps(attempt)
    for secret in ("Runway", "agency", hidden["id"], "Finance"):
        assert secret not in text, secret + " reached the bot"
    # The owner reads Finance's too, in her own Assistant chat; not the ones that link a private task her Assistant is
    # no party to.
    strategy_bot(api)
    _, owner = claim(api, r, "coo", "What's the top update today?", "ana-test")
    assert sorted(u["text"] for u in owner["updates"]["updates"]) == ["- Closed the Acme renewal", "- Runway is nine months"]


def test_the_default_order_is_a_goal_then_waits_on_you_then_newest(api, monkeypatch):
    from backend.tests.test_bot_contact import bot, token
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    for slug, name in (("boss", "Manager"), ("analyst", "Analyst"), ("writer", "Writer"), ("crier", "Crier")):
        bot(api, slug, name)
    post(api, "goals", {"owner": "boss", "title": "Launch in October"})
    post(api, "tasks", {"title": "Approve the market sizing", "body": "See notes", "owner": "human:cara"}, token=token(api, "analyst"))
    with api.app.state.store.transaction() as c:
        updates.post(c, bot_id(c, "boss"), "- Planned the launch", day="2026-09-28")
        updates.post(c, bot_id(c, "analyst"), "- Sized the market", day="2026-09-29")
        updates.post(c, bot_id(c, "crier"), "- Cara, the press list needs a look", day="2026-09-29")
        updates.post(c, bot_id(c, "writer"), "- Wrote the launch post", day="2026-09-29")
    r = runner(api)
    assign(api, r, "coo")
    ready(api, r, ["coo"])
    _, attempt = claim(api, r, "coo", "hi", "cara-test")
    ranked = [(u["author"], u["rank"], u["goals"], u["waits_on_you"]) for u in attempt["updates"]["updates"]]
    assert ranked == [("Manager", 1, ["Launch in October"], False), ("Crier", 2, [], True),
                      ("Analyst", 3, [], True), ("Writer", 4, [], False)]
    assert attempt["updates"]["updates"][0]["day"] == "2026-09-28", "the latest earlier day is there to offer"


def test_private_references_in_links_and_prose_never_reach_an_updates_chat_turn(api, monkeypatch):
    from backend.tests.test_api import restrict
    from backend.tests.test_bot_contact import bot
    monkeypatch.setattr(updates, "today", lambda at=None: "2026-09-29")
    bot(api, "fixer", "Fixer")
    hidden = post(api, "tasks", {"title": "Private market plan", "body": "See notes", "owner": "fixer", "private": True})
    rows = [("short_id", hidden["id"][:8]),
            ("singular_short_link", f"[task](#/task/{hidden['id'][:8]})"),
            ("plural_short_link", f"[task](#/tasks/{hidden['id'][:8]})"),
            ("plural_number_link", None),
            ("number_hash", None),
            ("number_words", None),
            ("dev_number", None),
            ("private_bot_slug", "privatebot"),
            ("private_bot_name", "Private Bot"),
            ("private_bot_link", "#/bot/privatebot"),
            ("secret_bot_slug", "secretbot"),
            ("secret_bot_name", "Secret Bot"),
            ("secret_bot_link", "#/bot/secretbot")]
    bot(api, "privatebot", "Private Bot")
    bot(api, "secretbot", "Secret Bot")
    bot(api, "source-public", "Public source")
    for slug, _ in rows:
        source = "source-" + slug.replace("_", "-")
        bot(api, source, "Source " + slug)
    with api.app.state.store.transaction() as c:
        number = 123456789012345678
        c.execute("UPDATE tasks SET number=? WHERE id=?", (number, hidden["id"]))
        # Cara can read Private Bot, but her Assistant cannot; Secret Bot is hidden from both.
        restrict(c, "privatebot", people=["cara"])
        restrict(c, "secretbot", people=["ana"])
        rows = [(slug, (f"[task](#/tasks/{number})" if slug == "plural_number_link" else
                        f"Task #{number}" if slug == "number_hash" else
                        f"task {number}" if slug == "number_words" else
                        f"Dev {number}" if slug == "dev_number" else ref))
                for slug, ref in rows]
        for slug, body in rows:
            source = "source-" + slug.replace("_", "-")
            updates.post(c, source, "- Reviewed " + body, day="2026-09-29")
        updates.post(c, bot_id(c, "source-public"), "- Published the public launch note", day="2026-09-29")
        updates.post(c, bot_id(c, "privatebot"), "- Private Bot report visible only to Cara", day="2026-09-29")
        updates.post(c, bot_id(c, "secretbot"), "- Secret Bot report visible only to Ana", day="2026-09-29")
    r = runner(api)
    assign(api, r, "coo")
    ready(api, r, ["coo"])
    _, attempt = claim(api, r, "coo", "What's the top update today?", "cara-test")
    handed = [row["text"] for row in attempt["updates"]["updates"]]
    assert handed == ["- Published the public launch note"]
    serialized = json.dumps(attempt)
    for _, reference in rows:
        assert reference not in serialized, reference + " reached the bot"
    assert hidden["id"] not in serialized
