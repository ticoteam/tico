"""A bot's question to a person is never silently lost: the ask returns its id even when the wait for the answer
fails, a retried ask is not sent twice, and a task left waiting on someone who was never asked shows in Health."""
import uuid

from backend.store import H
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_tasks_board import bot_token
from clients import hubtools
from clients.tico import APIError


class Api:
    """hubtools' client over the test server, as one bot; the answer poll fails the way a dropped connection does."""
    def __init__(self, client, token):
        self.client, self.token = client, token

    def post(self, path, body, key=None):
        r = self.client.post("/api/v2/" + path, json=body, headers={
            "Authorization": "Bearer " + self.token, "Idempotency-Key": key or str(uuid.uuid4())})
        if r.status_code != 200:
            raise APIError("refused", r.text, r.status_code)
        return r.json()

    def get(self, path, **query):
        raise APIError("unavailable", "Tico could not confirm this request", retryable=True)


def test_an_ask_whose_wait_fails_still_returns_its_id_and_a_retry_is_not_sent_twice(api):
    bot = Api(api, bot_token(api, "ops"))
    args = {"bots": ["ana"], "text": "Which vendor should Acme pay first this week?", "wait_s": 30}
    first = hubtools.ask(bot, args)
    assert first["ana"]["timeout"] and first["ana"]["message_id"]
    assert hubtools.ask(bot, args) == first
    with api.app.state.store.read() as c:
        sent = c.execute("SELECT id FROM messages WHERE sender_id='bot:ops' AND recipient_id='human:ana' "
                         "AND kind='ask'").fetchall()
    assert [r[0] for r in sent] == [first["ana"]["message_id"]]


def test_a_task_waiting_on_a_person_nobody_asked_shows_in_health_until_they_are_asked(api):
    tid = post(api, "tasks", {"owner": "ops", "title": "Renew the Acme domain", "body": "x"})["id"]
    with api.app.state.store.transaction() as c:
        # Set waiting on Ana before asks became subtasks, so the task itself waits on her.
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana', note=? WHERE id=?",
                  ("Renew the Acme domain for a year? If yes, I'll pay the invoice.", tid))
        H._task_event(c, tid, "bot:ops", "waiting_on", None, "human:ana", "")
        c.execute("UPDATE task_events SET created_at=? WHERE task_id=? AND field='waiting_on'",
                  (H.shift(H.now(), hours=-2), tid))

    def check():
        body = api.get("/api/v2/health", headers=headers()).json()
        return next((row for row in body["checks"] if row["id"] == "silent_waits"), None)

    found = check()
    assert "Renew the Acme domain waits on" in found["summary"] and "nothing was asked" in found["summary"]
    assert found["fixes"][0]["href"] == "#/task/" + tid
    with api.app.state.store.transaction() as c:
        H.task_ask(c, "bot:ops", tid, "Should the domain renew for one year or three?")
    assert check() is None


def test_a_question_asked_outside_any_task_is_in_the_persons_needs_you_until_answered(api):
    token = bot_token(api, "ops")
    asked = post(api, "messages", {"to": "ana", "text": "Which bank should Acme use for payroll?", "kind": "ask"},
                 token=token)

    def needs():
        return api.get("/api/v2/needs-you", headers=headers()).json()["items"]

    item = next(it for it in needs() if it["id"] == asked["id"])
    assert item["kind"] == "ask" and item["requester_id"] == "bot:ops"
    assert api.get("/api/v2/needs-you?count=1", headers=headers()).json()["count"] == len(needs())
    post(api, f"messages/{asked['id']}/answer", {"text": "Acme Bank."})
    assert all(it["id"] != asked["id"] for it in needs())


def test_a_wait_with_no_plain_ask_goes_last_and_its_bot_is_asked_once_to_restate_it(api):
    from backend import restate_asks
    store = api.app.state.store
    plain = "Approve the venue for Friday? If yes, I'll book it."
    explained = post(api, "tasks", {"owner": "ops", "title": "Book the venue", "body": "x"})["id"]
    legacy = post(api, "tasks", {"owner": "ops", "title": "Renew the Acme domain", "body": "x"})["id"]
    with store.transaction() as c:
        explained = H.task_update(c, "bot:ops", explained, status="waiting", waiting_on="ana", note=plain)["asked_in"]["id"]
        # Set waiting before the plain-ask rule, with the bot's shorthand as its only words.
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ana', note='Blocked on db14c907' WHERE id=?",
                  (legacy,))

    def needs():
        return {it["id"]: it for it in api.get("/api/v2/needs-you", headers=headers()).json()["items"]}

    def restate_requests():
        with store.read() as c:
            return c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:ops' AND sender_id=? "
                             "AND refs LIKE '%restate_ask%'", (H.KEEPER,)).fetchone()[0]

    items = needs()
    assert list(items)[-1] == legacy and items[legacy]["ask_missing"] and not items[legacy]["person_ask"]
    assert (items[explained]["person_ask"], items[explained]["ask_missing"]) == (plain, False)
    assert restate_requests() == 0, "reading Needs you asks nobody"
    for expected in ([legacy], []):
        with store.transaction() as c:
            assert restate_asks.ask_restate(c) == expected
    assert restate_requests() == 1
    # The bot restating it plainly clears the mark: the ask becomes Ana's card, the task stays the bot's.
    with store.transaction() as c:
        card = H.task_update(c, "bot:ops", legacy, status="waiting", waiting_on="ana",
                             note="Renew the Acme domain for a year? If yes, I'll pay the invoice.")["asked_in"]["id"]
    items = needs()
    assert legacy not in items and items[card]["ask_missing"] is False
    assert items[card]["person_ask"] == "Renew the Acme domain for a year? If yes, I'll pay the invoice."
