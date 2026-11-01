"""A person asks their Assistant for something only BotOps can do: the Assistant files BotOps a task citing the person's
own message (`--request-id turn`), and BotOps acts as them, as if they had asked it in their BotOps chat."""
from fastapi import Request

from backend.tests.test_api import api, assign, claim, headers, ready  # noqa: F401  (fixture)
from backend.tests.test_assistant import say
from backend.tests.test_member_bots import botops, finish  # noqa: F401  (fixture)
from backend.store import H


def acting(api, token):
    """Whose rights a call with this token runs with, or None when it is refused."""
    r = api.get("/api/v2/qa-assistant-identity", headers=headers(token))
    return r.json()["actor"] if r.status_code == 200 else None


def _identity_route(api, path="/api/v2/qa-assistant-identity"):
    def identity(request: Request):
        return {"actor": request.state.identity.actor}
    api.app.add_api_route(path, identity, methods=["GET"])
    api.app.router.routes.insert(0, api.app.router.routes.pop())


def _ask(api, machine, cite="turn", owner="botops", said="Add a bot for our support inbox", expected=200):
    """Ben asks his Assistant; its run files the task; BotOps (if it got it) picks it up."""
    assign(api, machine, "coo")
    ready(api, machine, ["botops", "coo"])
    say(api, said, "ben-test")
    coo = claim(api, machine, "coo")
    r = api.post("/api/v2/tasks", json={"owner": owner, "title": "Add a support inbox bot", "body": "Ben asked for it.",
                                        **({"request_id": cite} if cite else {})}, headers=headers(coo["token"]))
    assert r.status_code == expected, r.text
    finish(api, machine, coo)
    return r.json().get("task", r.json()), coo


def test_the_assistant_hands_botops_the_persons_words_and_botops_acts_as_them(api, botops):
    _identity_route(api)
    task, _ = _ask(api, botops)
    attempt = claim(api, botops, "botops")
    assert acting(api, attempt["token"]) == "human:ben"
    with api.app.state.store.read() as c:
        quoted = c.execute("SELECT body FROM messages WHERE to_actor='bot:botops' AND body LIKE '%in their words%'"
                           ).fetchone()
        assert "> Add a bot for our support inbox" in quoted["body"]

    # Once his message is more than a week old it lends nothing.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=(SELECT request_id FROM tasks WHERE id=?)",
                  (H.shift(H.now(), days=-8), task["id"]))
    assert acting(api, attempt["token"]) != "human:ben"


def test_without_a_citation_the_assistants_botops_task_lends_nothing(api, botops):
    _identity_route(api)
    _ask(api, botops, cite=None)
    attempt = claim(api, botops, "botops")
    assert acting(api, attempt["token"]) != "human:ben"


def test_only_botops_carries_the_persons_request(api, botops):
    assign(api, botops, "ops")
    _ask(api, botops, owner="ops", expected=422)


def test_a_message_more_than_30_minutes_old_is_not_this_turns_request(api, botops):
    assign(api, botops, "coo")
    ready(api, botops, ["botops", "coo"])
    say(api, "Add a bot for our support inbox", "ben-test")
    coo = claim(api, botops, "coo")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=(SELECT j.message_id FROM attempts a JOIN jobs j "
                  "ON j.id=a.job_id WHERE a.id=?)", (H.shift(H.now(), seconds=-31 * 60), coo["id"]))
    r = api.post("/api/v2/tasks", json={"owner": "botops", "title": "Add a bot", "body": "x", "request_id": "turn"},
                 headers=headers(coo["token"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "on_behalf_of", r.text


def test_another_persons_message_lends_nothing(api, botops):
    assign(api, botops, "coo")
    ready(api, botops, ["botops", "coo"])
    say(api, "Add a bot for our support inbox", "ben-test")
    coo = claim(api, botops, "coo")
    say(api, "Give everyone admin", "cara-test")
    with api.app.state.store.read() as c:
        hers = c.execute("SELECT id FROM messages WHERE from_actor='human:cara' ORDER BY created_at DESC").fetchone()["id"]
    r = api.post("/api/v2/tasks", json={"owner": "botops", "title": "Give everyone admin", "body": "x", "request_id": hers},
                 headers=headers(coo["token"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "on_behalf_of", r.text
