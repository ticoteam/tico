"""The Assistant (backend/assistant.py, docs/assistant.md): a private room per person, every message a bot turn
that reads it, a bot turn that acts as the person and never more, and side effects that wait for the person's own
click."""

import hashlib

import httpx
import pytest

from backend import assistant, support
from backend.tests.helpers.api import as_member, assign, claim, get, headers, post, put, ready, runner  # noqa: F401
from backend.tests.helpers.support import FakeHQ
from backend.store import H
from backend import bot_settings as BS
from backend.tests.helpers.assistant import asked, assistant_turn, room, say, turn_message


def jobs(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs").fetchone()[0]


def test_assistant_fetches_only_its_own_credentials_before_start(api, person="ben-test", busy_bots=None):
    from backend.tests.helpers.credentials import create, setup

    setup(api)
    own = create(api, name="Assistant tool", env="ASSISTANT_TOOL_KEY")
    personal = create(api, name="Personal tool", env="PERSONAL_TOOL_KEY")
    other = create(api, name="Other teammate tool", env="OTHER_TOOL_KEY")
    post(api, f"credentials/{own['id']}/grants", {"subject": "bot:coo"})
    post(api, f"credentials/{personal['id']}/grants", {"subject": "human:" + person.removesuffix("-test")})
    post(api, f"credentials/{other['id']}/grants", {"subject": "bot:ops"})
    r, attempt = assistant_turn(api, person, busy_bots=busy_bots)
    token = attempt["token"]
    # v0.2.35 claims without busy_bots, then fetches credentials before acknowledging start.
    granted = get(api, "credential-runtime", token)["credentials"]
    assert [value["id"] for value in granted] == [own["id"]]
    assert get(api, "me", token)["actor"] == "human:" + person.removesuffix("-test")
    get(api, "credential-runtime", person, expected=403)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "assistant-start"}, token=r["token"])
    assert [value["id"] for value in get(api, "credential-runtime", token)["credentials"]] == [own["id"]]
    made = post(
        api,
        "tasks",
        {"owner": "human:" + person.removesuffix("-test"), "title": "Draft my week", "body": "Monday first."},
        token=token,
    )
    assert made["requester_id"] == "human:" + person.removesuffix("-test")
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, token=r["token"])
    get(api, "credential-runtime", token, expected=409)


def test_only_the_owner_of_an_assistant_room_reads_or_posts_in_it(api):
    ana, ben = room(api, "ana-test"), room(api, "ben-test")
    assert ana["available"] and ana["room_id"] and ana["room_id"] != ben["room_id"]
    assert room(api, "ana-test")["room_id"] == ana["room_id"]  # one room each, however often asked
    say(api, "My private question about the launch", "ana-test", expected=200)
    # Not the other person, and not the company owner either. C1 hides a room it cannot read: 404, never 403.
    get(api, f"conversations/{ana['room_id']}/messages", "ben-test", expected=404)
    get(api, f"conversations/{ben['room_id']}/messages", "ana-test", expected=404)
    post(
        api,
        "messages",
        {"to": "coo", "text": "Injected", "conversation_id": ana["room_id"]},
        token="ben-test",
        expected=403,
    )
    post(
        api,
        "messages",
        {"to": "coo", "text": "Injected", "conversation_id": ben["room_id"]},
        token="ana-test",
        expected=403,
    )
    post(api, "chat/coo", {"text": "Anywhere else"}, token="ben-test", expected=403)
    assert [m["body"] for m in room(api, "ben-test")["messages"]] == []
    # The owner of the room may also use the generic message route; the server marks the turn.
    ok = post(api, "messages", {"to": "coo", "text": "Also fine", "conversation_id": ana["room_id"]})
    assert ok["refs"]["assistant"] is True


def test_the_assistant_acts_as_the_person_and_never_more(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:  # the private inbox bot's work: ana's, not ben's
        secret = H.task_create(c, "human:ana", "Review the private mail", "Nothing for ben.", "bot:inbox")["id"]
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    assert get(api, "me", token)["actor"] == "human:ben"  # its tools are ben's, not the bot's
    # What ben may not see or touch, his Assistant may not.
    get(api, "tasks/" + secret, token, expected=404)
    post(api, "tasks/" + secret + "/comments", {"text": "Looking"}, token=token, expected=404)
    # Nothing that changes settings, people or bots is the person's to make on a member's rights: the route refuses.
    post(api, "bots/ops/archive", {"expected_revision": 1}, token=token, expected=403)
    # The AI provider choice is the owner's: a member's Assistant gets the route's own refusal, not a chat card.
    providers = api.put("/api/v2/providers", json={"enabled": [], "expected_revision": 0}, headers=headers(token))
    assert providers.status_code == 403, providers.text
    # The turn cites the message that started it, so settling a task of ben's own runs as ben.
    mine = post(
        api, "tasks", {"owner": "human:ben", "title": "Pick the launch date", "body": "Oct 1 or Oct 8?"}, token=token
    )
    post(api, f"tasks/{mine['id']}/done", {"version": mine["version"]}, token=token)
    with api.app.state.store.read() as c:
        assert H.task(c, mine["id"])["status"] == "done"
    # A low-risk write runs directly, as ben, and says so in the record.
    made = post(
        api, "tasks", {"owner": "human:ben", "title": "Draft the launch post", "body": "For Oct 1."}, token=token
    )
    assert made["requester_id"] == "human:ben"
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT via FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY created_at LIMIT 1",
                (made["id"],),
            ).fetchone()[0]
            == "assistant"
        )
        assert c.execute("SELECT count(*) FROM events WHERE detail LIKE '%\"via\": \"assistant\"%'").fetchone()[0] > 0
    # The runner's own lease calls are still the runner's.
    post(api, f"attempts/{attempt['id']}/renew", {}, token=r["token"], expected=200)


def test_a_side_effect_runs_only_after_the_person_confirms(api):
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    operation = {
        "summary": "Archive the Ops bot",
        "method": "POST",
        "path": "/api/v2/bots/ops/archive",
        "body": {"expected_revision": revision},
    }
    # With the owner's rule off, the bot cannot do it itself, and only proposes.
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    assert (
        post(api, "bots/ops/archive", operation["body"], token=token, expected=403)["error"]["code"]
        == "confirm_required"
    )
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200
    action = post(api, "assistant/actions", operation, token=token)["action"]
    assert action["status"] == "pending" and action["owner"] == "human:ana"
    post(api, "assistant/actions", {**operation, "path": "/api/v2/me/tokens"}, token=token, expected=422)

    def archived():
        with api.app.state.store.read() as c:
            return H.bot(c, "ops")["state"] == "archived"

    assert not archived()
    # It cannot confirm its own proposal, nobody else can see it, and nothing has run.
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token=token, expected=403)
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ben-test", expected=404)
    post(api, f"assistant/actions/{action['id']}/cancel", {}, token=token, expected=403)
    assert not archived()
    # The person's own click runs it, as them, via the assistant.
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done" and done["result"]["status_code"] == 200, done["result"]
    assert archived()
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test", expected=409)  # once
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='assistant.action.confirmed'").fetchone()[0] == 1
        assert (
            c.execute(
                "SELECT count(*) FROM events WHERE action LIKE 'bot.%' AND detail LIKE '%\"via\": \"assistant\"%'"
            ).fetchone()[0]
            >= 1
        )
    # A canceled proposal never runs.
    other = post(
        api,
        "assistant/actions",
        {"summary": "Archive Finance", "path": "/api/v2/bots/finance/archive", "body": {"expected_revision": 1}},
        token="ana-test",
    )["action"]
    post(api, f"assistant/actions/{other['id']}/cancel", {}, token="ana-test")
    post(api, f"assistant/actions/{other['id']}/confirm", {}, token="ana-test", expected=409)


def test_owner_confirms_a_group_of_recruiting_seats_before_any_are_added(api):
    as_member(api, "ben@acme.example")
    body = {
        "seats": [
            {
                "title": "Engineering reviewer",
                "description": "Reviews changes.",
                "reports_to": "coo",
                "team": "engineering",
            }
        ]
    }
    operation = {"summary": "Add the engineering reviewer seat", "path": "/api/v2/seats/batch", "body": body}
    post(api, "assistant/actions", operation, token="ben-test", expected=403)

    action = post(api, "assistant/actions", operation, token="ana-test")["action"]
    assert action["status"] == "pending" and get(api, "seats")["seats"] == []
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done" and done["result"]["status_code"] == 200, done["result"]
    assert [(seat["title"], seat["stage"], seat["reports_to"]) for seat in get(api, "seats")["seats"]] == [
        ("Engineering reviewer", "recruiting", "coo")
    ]


def test_a_proposal_cannot_smuggle_a_route_and_never_stores_an_answer(api):
    for path in ("/api/v2/me/tokens", "/api/v2/tasks/../me/tokens"):
        post(api, "assistant/actions", {"summary": "Do it", "path": path}, token="ana-test", expected=422)
    # The stored path is exactly what runs; only the status and an error detail are kept, never the answer.
    action = post(
        api,
        "assistant/actions",
        {
            "summary": "File it",
            "path": "/api/v2/tasks",
            "body": {"title": "Draft the launch plan", "body": "Two pages.", "owner": "human:ana"},
        },
    )["action"]
    done = post(api, f"assistant/actions/{action['id']}/confirm", {})["action"]
    assert done["status"] == "done" and set(done["result"]) == {"status_code", "error"}
    assert "Draft the launch plan" not in str(get(api, f"assistant/actions/{action['id']}")["action"]["result"])


def test_an_assistant_message_never_lends_the_persons_authority_to_botops(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('botops','botops','BotOps','','active',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('botops',NULL,'ana')")
        BS.write(c, 'botops', {"runtime": "fake", "host": "keeper"})
        H.VIA.set("assistant")
        via = H.say(c, "human:ana", "bot:botops", "Rename the Ops bot to Operations", kind="say")
        H.VIA.set("")
        plain = H.say(c, "human:ana", "bot:botops", "Rename the Ops bot to Operations, please", kind="say")
    assert via["refs"].get("via") == "assistant" and "via" not in plain["refs"]
    r = runner(api)
    assign(api, r, "botops")
    ready(api, r, ["botops"])
    token = claim(api, r, "botops")["token"]
    body = {"display_name": "Operations", "expected_revision": 1}
    refused = post(api, "bots/ops/definition", {**body, "on_behalf_of": via["id"]}, token=token, expected=403)
    assert "written by the Assistant" in refused["error"]["detail"]
    other = api.post("/api/v2/bots/ops/definition", json={**body, "on_behalf_of": plain["id"]}, headers=headers(token))
    assert "written by the Assistant" not in other.text


def test_direct_writes_stay_inside_the_team(api):
    with api.app.state.store.transaction() as c:
        theirs = H.task_create(c, "human:ana", "Review the plan", "Please review.", "human:ben")
        anas = H.task_create(c, "human:ben", "Ana's own item", "For Ana to decide.", "human:ana")
        botwork = H.task_create(c, "human:ben", "Plan the launch", "For ops.", "bot:ops")
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    # With "Assistant acts without asking" off, nothing that spends, messages anyone, or changes a task that is not theirs
    # alone runs directly: each is a card.
    post(api, "messages", {"to": "human:cara", "text": "Hello"}, token=token, expected=403)
    post(api, "messages", {"to": "ops", "text": "Hello"}, token=token, expected=403)
    post(api, f"tasks/{theirs['id']}/run-now", {}, token=token, expected=403)
    post(api, f"tasks/{theirs['id']}", {"version": theirs["version"], "owner": "ops"}, token=token, expected=403)
    post(api, f"tasks/{anas['id']}", {"version": anas["version"], "note": "x"}, token=token, expected=403)  # not ben's
    post(api, f"tasks/{theirs['id']}/comments", {"text": "Started"}, token=token, expected=403)  # Ana is on it
    post(
        api, "tasks", {"owner": "human:cara", "title": "Review the plan", "body": "Please."}, token=token, expected=403
    )
    post(
        api,
        "tasks",
        {"owner": "ops", "title": "Draft the launch post", "body": "For Oct 1."},
        token=token,
        expected=403,
    )
    post(api, f"tasks/{botwork['id']}/comments", {"text": "Please hurry"}, token=token, expected=403)
    # What is ben's alone still runs: his own tasks, and marking updates read.
    post(api, "tasks", {"owner": "human:ben", "title": "Draft my week", "body": "Monday first."}, token=token)
    post(api, "updates/read", {"all": True}, token=token)
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200
    # With the rule on, the same messages and tasks run as ben, and each is marked as the assistant's doing.
    post(api, "messages", {"to": "human:cara", "text": "Hello"}, token=token)
    post(api, "messages", {"to": "ops", "text": "Hello"}, token=token)
    post(api, "chat/ops", {"text": "Hello again"}, token=token)
    post(api, f"tasks/{botwork['id']}/comments", {"text": "Please hurry"}, token=token)
    made = post(api, "tasks", {"owner": "ops", "title": "Draft the launch post", "body": "For Oct 1."}, token=token)
    assert made["owner_id"] == "bot:ops" and made["requester_id"] == "human:ben"
    with api.app.state.store.read() as c:
        # What it wrote is marked, so BotOps ignores it.
        assert (
            c.execute(
                "SELECT count(*) FROM messages WHERE recipient_id='bot:ops' AND refs LIKE '%\"via\": \"assistant\"%'"
            ).fetchone()[0]
            >= 2
        )


def test_the_assistant_creates_a_task_for_another_person_without_a_card(api):
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    made = post(
        api,
        "tasks",
        {"owner": "human:cara", "title": "Review the plan", "body": "Please review the plan."},
        token=token,
    )
    assert made["owner_id"] == "human:cara" and made["requester_id"] == "human:ben"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM assistant_actions WHERE status='pending'").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT via FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY created_at LIMIT 1",
                (made["id"],),
            ).fetchone()[0]
            == "assistant"
        )
    # The owner's "Assistant acts without asking" off puts the card back.
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    refused = post(
        api,
        "tasks",
        {"owner": "human:cara", "title": "Review the budget", "body": "Please."},
        token=token,
        expected=403,
    )
    assert refused["error"]["code"] == "confirm_required"
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200


def test_real_assistant_lease_keeps_actual_bot_private_boundaries_and_revocation(api):
    from backend.store import Problem

    private = post(
        api,
        'tasks',
        {
            'owner': 'human:ben',
            'title': 'Review the confidential packet',
            'body': 'Confidential packet.',
            'private': True,
        },
    )
    attached = post(api, 'tasks/' + private['id'] + '/files', {'name': 'packet.md', 'text': 'Confidential attachment.'})
    r, attempt = assistant_turn(api, 'ben-test')
    token = attempt['token']
    who = api.app.state.auth.authenticate({'authorization': 'Bearer ' + token}, '/api/v2/tasks/' + private['id'], 'GET')
    assert who.task_actor == 'bot:coo' and who.attempt_id == attempt['id']
    for path in (
        'tasks/' + private['id'],
        'conversations/' + private['conversation_id'] + '/messages',
        'files/' + attached['file']['id'],
    ):
        response = api.get('/api/v2/' + path, headers=headers(token))
        assert response.status_code in (403, 404), response.text
        assert 'Confidential' not in response.text
    sql = api.post(
        '/api/v2/sql',
        json={'sql': 'SELECT id,body FROM tasks WHERE id=?', 'params': [private['id']]},
        headers=headers(token),
    )
    assert sql.status_code == 403 and sql.json()['error']['code'] == 'confirm_required', sql.text
    assert 'Confidential' not in sql.text
    from backend.sql import connect, run

    with api.app.state.store.read() as c:
        conn = connect(api.app.state.store.settings.db_path, c, api.app.state.auth, who)
        try:
            assert run(conn, 'SELECT id,body FROM tasks WHERE id=?', [private['id']], 500, 5)['rows'] == []
        finally:
            conn.close()
    own = post(
        api,
        'tasks',
        {'owner': 'bot:coo', 'title': 'Review the assistant packet', 'body': 'Review it.', 'private': True},
        token='ben-test',
    )
    assert get(api, 'tasks/' + own['id'], token)['task']['id'] == own['id']
    published = api.post(
        '/api/v2/tasks/' + own['id'], json={'version': own['version'], 'private': False}, headers=headers(token)
    )
    assert published.status_code in (403, 422) and published.json()['error']['code'] in ('privacy', 'confirm_required')
    assert get(api, 'tasks/' + own['id'], 'ben-test')['task']['private']
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id=?", (attempt['id'],))
    with pytest.raises(Problem) as revoked:
        api.app.state.store.write(who, lambda c: {})
    assert revoked.value.status == 409


def test_the_assistant_answers_what_a_bot_asks_the_person_and_no_one_elses_asks(api):
    """A bot's question to ben, asked on its task, is his Assistant's to answer on his words. Ana's ask on her private task
    is neither readable nor answerable on his turn."""
    with api.app.state.store.transaction() as c:
        ship = H.task_create(c, "human:ben", "Ship the zip fix", "For the next release.", "bot:ops")
        mine = H.task_ask(
            c, "bot:ops", ship["id"], "human:ben", "Ship the zip fix in the next release?", seen_version=ship["version"]
        )
        budget = H.task_create(c, "human:ana", "Plan the budget", "Q4.", "bot:ops", private=True)
        hers = H.task_ask(
            c, "bot:ops", budget["id"], "human:ana", "Is the Q4 budget right?", seen_version=budget["version"]
        )
        secret = H.task_create(
            c, "human:ben", "Review the confidential packet", "Confidential packet.", "human:ben", private=True
        )
        ship_version, budget_version = H.task(c, ship["id"])["version"], H.task(c, budget["id"])["version"]
    r, attempt = assistant_turn(api, "ben-test", "Yes, ship the zip fix")
    token = attempt["token"]
    assert get(api, "tasks/" + ship["id"], token)["task"]["id"] == ship["id"]
    for task in (budget, secret):
        hidden = api.get("/api/v2/tasks/" + task["id"], headers=headers(token))
        assert hidden.status_code in (403, 404), hidden.text
        assert "Q4 budget" not in hidden.text and "Confidential" not in hidden.text
    asked(
        api,
        f"tasks/{ship['id']}/asks/{mine['requirement_id']}/answer",
        {"value": "Yes, ship the zip fix", "version": ship_version},
        token,
        "turn",
    )
    denied = api.post(
        f"/api/v2/tasks/{budget['id']}/asks/{hers['requirement_id']}/answer",
        json={"value": "Yes", "version": budget_version},
        headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"},
    )
    assert denied.status_code in (403, 404), denied.text
    with api.app.state.store.read() as c:
        answered = {r["requirement_id"]: r for r in H.task_view(c, ship["id"], "human:ben")["to_leave"]}
        assert (
            answered[mine["requirement_id"]]["met"] and answered[mine["requirement_id"]]["answer"]["by"] == "human:ben"
        )
        theirs = {r["requirement_id"]: r for r in H.task_view(c, budget["id"], "human:ana")["to_leave"]}
        assert not theirs[hers["requirement_id"]]["met"]


def test_the_assistant_does_the_task_actions_the_person_asked_for_in_their_chat(api):
    with api.app.state.store.transaction() as c:
        review = H.task_create(c, "human:ana", "Review the plan", "Please review.", "human:ben")
        launch = H.task_create(c, "human:ben", "Plan the launch", "For ops.", "bot:ops")
        old = H.task_create(c, "human:ben", "Book the venue", "Last year's.", "human:ben")
    r, attempt = assistant_turn(api, "ben-test", "Mark the plan review done, close the launch and the venue tasks")
    token, said = attempt["token"], turn_message(api, attempt)
    # The run cites the message that started it, so it runs as ben, with no click. With the owner's rule off it is a card.
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    close = {"to": "closed", "version": launch["version"]}
    refused = post(api, f"tasks/{launch['id']}/move", close, token=token, expected=403)
    assert refused["error"]["code"] == "confirm_required"
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200
    # Citing the message that started this run, it runs as ben, with no click.
    done = asked(
        api,
        f"tasks/{review['id']}/move",
        {"to": "done", "why": "Reviewed", "version": review["version"]},
        token,
        "turn",
    )["task"]
    assert done["status"] == "done"
    closed = asked(api, f"tasks/{launch['id']}/move", close, token, said)["task"]
    assert closed["status"] == "closed"
    # Reopen, comment (Ana is on it) and hand it to someone else: the same task actions the cards carry.
    reopened = asked(
        api,
        f"tasks/{review['id']}/move",
        {"to": "reopen", "why": "One more pass", "version": done["version"]},
        token,
        "turn",
    )["task"]
    assert reopened["status"] not in ("done", "closed")
    asked(api, f"tasks/{review['id']}/comments", {"text": "Reopened: one more pass"}, token, "turn")
    handed = asked(api, f"tasks/{old['id']}/owner", {"owner": "human:ana", "version": old["version"]}, token, "turn")[
        "task"
    ]
    assert handed["owner_id"] == "human:ana"
    with api.app.state.store.read() as c:
        # Recorded as ben, via the assistant, for the message he sent.
        row = c.execute(
            "SELECT actor_id AS actor,via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='move' "
            "AND new='general-closed'",
            (launch["id"],),
        ).fetchone()
        assert (row["actor"], row["via"]) == ("human:ben", "assistant")
        close = c.execute(
            "SELECT actor,detail FROM events WHERE action='task.close' AND target=?", (launch["id"],)
        ).fetchone()
        detail = H._json(close["detail"], {})
        assert close["actor"] == "human:ben" and detail["via"] == "assistant"
        assert detail["delegation"] == {"for": "human:ben", "cited": said, "run": "asked"}


def test_the_assistants_tools_cite_the_message_for_it(api):
    from clients import hubtools
    from clients.tico import APIError

    class Turn:
        """`hub`'s client in the assistant's run, answered by this server."""

        def __init__(self, token):
            self.token = token

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            extra = {"X-Tico-On-Behalf-Of": delegate if isinstance(delegate, str) else "turn"} if delegate else {}
            r = api.request(
                method, "/api/v2/" + path, json=body, params=query, headers={**headers(self.token), **extra}
            )
            if r.status_code >= 400:
                raise APIError(r.json()["error"]["code"], r.json()["error"]["detail"], status=r.status_code)
            return r.json()

        def get(self, path, **query):
            return self.call("GET", path, query=query)

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body or {}, key)

    with api.app.state.store.transaction() as c:
        launch = H.task_create(c, "human:ben", "Plan the launch", "For ops.", "bot:ops")
        ads = H.task_create(c, "human:ben", "Run the ads", "Ben picks the channel.", "bot:ops")
        pick = H.task_ask(c, "bot:ops", ads["id"], "human:ben", "Search or social ads?", seen_version=ads["version"])
        ads_version = H.task(c, ads["id"])["version"]
    r, attempt = assistant_turn(api, "ben-test", "Close the launch task and say search ads")
    turn = Turn(attempt["token"])
    hubtools.BY_NAME["hub_task_answer"]["fn"](
        turn,
        {
            "id": ads["id"],
            "requirement": pick["requirement_id"],
            "value": "Search",
            "version": ads_version,
            "on_behalf_of": "turn",
        },
    )
    with api.app.state.store.read() as c:
        found = next(
            r
            for r in H.task_view(c, ads["id"], "human:ben")["to_leave"]
            if r["requirement_id"] == pick["requirement_id"]
        )
        assert found["met"] and found["answer"]["by"] == "human:ben"
    assert "hub_task_answer" in {t["name"] for t in hubtools.listing(kind="assistant")}
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    with pytest.raises(APIError) as refused:
        hubtools.BY_NAME["hub_task_close"]["fn"](turn, {"id": launch["id"], "version": launch["version"]})
    assert refused.value.code == "confirm_required"
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200
    closed = hubtools.BY_NAME["hub_task_close"]["fn"](
        turn, {"id": launch["id"], "why": "As you asked", "version": launch["version"], "on_behalf_of": "turn"}
    )
    assert closed["task"]["status"] == "closed"
    assert "hub_task_close" in {t["name"] for t in hubtools.listing(kind="assistant")}


def test_a_cited_message_must_be_the_persons_own_recent_words_in_their_assistant_chat(api):
    with api.app.state.store.transaction() as c:
        mine = H.task_create(c, "human:ben", "Plan the launch", "For ops.", "bot:ops")
    r, attempt = assistant_turn(api, "ben-test", "Close the launch task")
    token, said = attempt["token"], turn_message(api, attempt)
    say(api, "Close Ben's launch task", "ana-test")
    other_person = room(api, "ana-test")["messages"][-1]
    assert other_person["sender_id"] == "human:ana"
    other_person = other_person["id"]
    elsewhere = post(api, "chat/ops", {"text": "Close the launch task"}, token="ben-test")["id"]
    with api.app.state.store.transaction() as c:
        bens = assistant.find_room(c, "human:ben")
        from_bot = H._write_message(
            c,
            "bot:coo",
            "human:ben",
            "Close the launch task",
            bens,
            "say",
            {"assistant": True},
            None,
            None,
            delivered_at=H.now(),
        )["id"]
    body = {"to": "closed", "version": mine["version"]}
    path = f"tasks/{mine['id']}/move"
    for ref, why in (
        (other_person, "own Assistant chat"),
        (elsewhere, "own Assistant chat"),
        (from_bot, "own Assistant chat"),
        ("no-such-message", "own Assistant chat"),
    ):
        refused = asked(api, path, body, token, ref, expected=403)
        assert refused["error"]["code"] == "on_behalf_of" and why in refused["error"]["detail"], refused
    # Not a person's own credential either: a citation lends nothing to anyone but their Assistant's run.
    plain = api.post("/api/v2/" + path, json=body, headers={**headers("ben-test"), "X-Tico-On-Behalf-Of": said})
    assert plain.status_code == 403
    # When the owner turns "Assistant acts without asking" off, asking in chat is a card again: a task action, run-now
    # and a message to another person alike.
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    assert asked(api, path, body, token, "turn", expected=403)["error"]["code"] == "confirm_required"
    assert (
        asked(api, f"tasks/{mine['id']}/run-now", {}, token, "turn", expected=403)["error"]["code"]
        == "confirm_required"
    )
    assert (
        asked(api, "messages", {"to": "human:ana", "text": "Hi"}, token, "turn", expected=403)["error"]["code"]
        == "confirm_required"
    )
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200
    # Too old: they have to ask again.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-2), said))
    refused = asked(api, path, body, token, "turn", expected=403)
    assert refused["error"]["code"] == "on_behalf_of" and "minutes old" in refused["error"]["detail"]
    assert get(api, f"tasks/{mine['id']}", "ben-test")["task"]["status"] != "closed"


def test_the_person_asking_never_lends_more_than_their_own_rights(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:  # the private inbox bot's work: ana's, not ben's
        secret = H.task_create(c, "human:ana", "Review the private mail", "Nothing for ben.", "bot:inbox")
    close = {"to": "closed", "version": secret["version"]}
    post(api, f"tasks/{secret['id']}/move", close, token="ben-test", expected=404)
    r, attempt = assistant_turn(api, "ben-test", "Close the private mail review")
    token = attempt["token"]
    asked(api, f"tasks/{secret['id']}/move", close, token, "turn", expected=404)
    asked(api, f"tasks/{secret['id']}/comments", {"text": "Closing"}, token, "turn", expected=404)
    with api.app.state.store.read() as c:
        assert H.task(c, secret["id"])["status"] == "open"


def test_a_group_of_task_actions_is_one_card_and_one_click(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, "human:ben", f"Old task {i}", "Stale.", "bot:ops") for i in range(25)]
        secret = H.task_create(c, "human:ana", "Review the private mail", "Nothing for ben.", "bot:inbox")
        ads = H.task_create(c, "human:ben", "Run the ads", "Q4.", "bot:ops")
        spend = H.task_ask(
            c, "bot:ops", ads["id"], "human:ben", "Spend $500 on ads?", kind="ok", seen_version=ads["version"]
        )
    r, attempt = assistant_turn(api, "ben-test", "Clear out all my old tasks")
    token = attempt["token"]
    steps = [
        {"path": f"/api/v2/tasks/{t['id']}/move", "body": {"to": "closed", "why": "Cleared", "version": t["version"]}}
        for t in tasks
    ]
    steps.append({"path": f"/api/v2/tasks/{secret['id']}/move", "body": {"to": "closed", "version": secret["version"]}})
    action = post(
        api,
        "assistant/actions",
        {"summary": "Close your 25 old tasks", "path": assistant.BATCH_PATH, "body": {"actions": steps}},
        token=token,
    )["action"]
    assert action["description"] == "26 task actions, on one Confirm" and len(action["diff"]) == 27
    assert action["diff"][0] == {"field": "Close 26 tasks, under a week old", "new": "each listed below", "group": True}
    assert "Old task 0" in action["diff"][1]["field"] and "to: closed" in action["diff"][1]["new"]
    pending = get(api, "assistant", "ben-test")["pending"]
    assert [p["id"] for p in pending] == [action["id"]]  # one card, not 26
    # Running a task now or anything else is refused on a bulk card.
    for odd in (
        {"path": f"/api/v2/tasks/{tasks[0]['id']}/run-now", "body": {}},
        {"path": "/api/v2/messages", "body": {"to": "human:ana", "text": "Hi"}},
        {"path": f"/api/v2/tasks/{ads['id']}/asks/{spend['requirement_id']}/ok", "body": {}},  # an OK: their tap
    ):
        post(
            api,
            "assistant/actions",
            {"summary": "Sneak", "path": assistant.BATCH_PATH, "body": {"actions": [steps[0], odd]}},
            token=token,
            expected=422,
        )
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token=token, expected=403)  # never the bot's own click
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ben-test")["action"]
    assert done["status"] == "done", done
    assert done["result"]["error"].startswith("1 of 26 did not go through")
    assert [i["status_code"] for i in done["result"]["items"]] == [200] * 25 + [404]
    with api.app.state.store.read() as c:
        assert all(H.task(c, t["id"])["status"] == "closed" for t in tasks)
        assert H.task(c, secret["id"])["status"] == "open"
        asked_ok = next(
            r
            for r in H.task_view(c, ads["id"], "human:ben")["to_leave"]
            if r["requirement_id"] == spend["requirement_id"]
        )
        assert not asked_ok["met"]
        assert (
            c.execute(
                "SELECT count(*) FROM history_events WHERE subject_type='task' AND kind='move' AND new='general-closed' "
                "AND actor_id='human:ben' "
                "AND via='assistant'"
            ).fetchone()[0]
            == 25
        )


def test_a_task_change_card_carries_the_version_the_assistant_saw_and_never_a_fresh_one(api):
    """A card's task change keeps the version the Assistant read. Without one it is refused, alone or as a bulk step:
    filling in the task's version now would let an edit made from an older read pass over changes made since."""
    with api.app.state.store.transaction() as c:
        launch = H.task_create(c, "human:ana", "Plan the launch", "For ops.", "bot:ops")
    path = f"/api/v2/tasks/{launch['id']}"
    for body in (
        {"summary": "Rename it", "path": path, "body": {"title": "Launch plan"}},
        {
            "summary": "Rename all",
            "path": assistant.BATCH_PATH,
            "body": {"actions": [{"path": path, "body": {"title": "X"}}]},
        },
    ):
        refused = api.post("/api/v2/assistant/actions", json=body, headers=headers("ana-test"))
        assert refused.status_code == 422 and "version" in refused.text, refused.text
    seen = launch["version"]
    card = post(
        api,
        "assistant/actions",
        {"summary": "Rename it", "path": path, "body": {"title": "Launch plan", "version": seen}},
    )["action"]
    assert card["body"]["version"] == seen
    # Someone changes the task after the Assistant read it.
    post(api, f"tasks/{launch['id']}", {"title": "Plan the launch, v2", "version": seen})
    stale = api.post(f"/api/v2/assistant/actions/{card['id']}/confirm", json={}, headers=headers("ana-test"))
    assert stale.status_code != 200 or stale.json()["action"]["status"] != "done", stale.text
    with api.app.state.store.read() as c:
        assert H.task(c, launch["id"])["title"] == "Plan the launch, v2"


def test_a_close_or_owner_card_runs_through_the_tasks_own_route_and_an_ok_stays_the_persons_tap(api):
    """Cards for "close these" carried `{"status": "closed"}` or `{"close": true}` on `POST /api/v2/tasks/{id}`, which
    takes field edits only. A card like that, new or already waiting, closes through `/move` with the version the
    Assistant saw; a new owner goes through `/owner`; a card that mixes a move with field edits is refused. An OK is
    never a card, alone or as a bulk step."""
    import json

    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, "human:ana", f"Old task {i}", "Stale.", "bot:ops") for i in range(4)]
    path = lambda t: f"/api/v2/tasks/{t['id']}"
    # One stored before this release, exactly as the assistant wrote it.
    stale = post(
        api, "assistant/actions", {"summary": "Close old task 0", "path": path(tasks[0]), "body": {"version": 1}}
    )["action"]
    with api.app.state.store.transaction() as c:
        stored = json.dumps({"status": "closed", "version": tasks[0]["version"]})
        c.execute("UPDATE assistant_actions SET body=? WHERE id=?", (stored, stale["id"]))
    done = post(api, f"assistant/actions/{stale['id']}/confirm", {})["action"]
    assert done["status"] == "done", done["result"]
    # A new one is stored as the move it is, and so is each step of a bulk card.
    fresh = post(
        api,
        "assistant/actions",
        {
            "summary": "Close old task 1",
            "path": path(tasks[1]),
            "body": {"status": "closed", "note": "Stale", "version": tasks[1]["version"]},
        },
    )["action"]
    assert (fresh["path"], fresh["body"]) == (
        path(tasks[1]) + "/move",
        {"to": "closed", "why": "Stale", "version": tasks[1]["version"]},
    )
    step = {"path": path(tasks[2]), "body": {"close": True, "version": tasks[2]["version"]}}
    bulk = post(
        api,
        "assistant/actions",
        {"summary": "Close the rest", "path": assistant.BATCH_PATH, "body": {"actions": [step]}},
    )["action"]
    assert bulk["body"]["actions"][0]["path"] == path(tasks[2]) + "/move"
    for action in (fresh, bulk):
        assert post(api, f"assistant/actions/{action['id']}/confirm", {})["action"]["status"] == "done"
    with api.app.state.store.read() as c:
        assert [H.task(c, t["id"])["status"] for t in tasks[:3]] == ["closed"] * 3
    # A new owner goes through /owner.
    give = post(
        api,
        "assistant/actions",
        {
            "summary": "Give it to Ana",
            "path": path(tasks[3]),
            "body": {"owner": "human:ana", "version": tasks[3]["version"]},
        },
    )["action"]
    assert give["path"] == path(tasks[3]) + "/owner"
    assert {k: give["body"][k] for k in ("owner", "version")} == {"owner": "human:ana", "version": tasks[3]["version"]}
    assert post(api, f"assistant/actions/{give['id']}/confirm", {})["action"]["status"] == "done"
    with api.app.state.store.read() as c:
        assert H.task(c, tasks[3]["id"])["owner_id"] == "human:ana"
        version = H.task(c, tasks[3]["id"])["version"]
    mixed = {
        "summary": "Rename and close",
        "path": path(tasks[3]),
        "body": {"title": "X", "close": True, "version": version},
    }
    refused = api.post("/api/v2/assistant/actions", json=mixed, headers=headers("ana-test"))
    assert refused.status_code == 422 and "one thing" in refused.text, refused.text
    # An OK is the person's own click on its preview: never a card, alone or beside a close.
    ok = {"path": path(tasks[3]) + "/asks/r-1/ok", "body": {}}
    for card in (
        {"summary": "OK it", **ok},
        {
            "summary": "Close and OK",
            "path": assistant.BATCH_PATH,
            "body": {"actions": [ok, {"path": path(tasks[3]), "body": {"close": True, "version": version}}]},
        },
    ):
        refused = api.post("/api/v2/assistant/actions", json=card, headers=headers("ana-test"))
        assert refused.status_code in (403, 422), refused.text
    with api.app.state.store.read() as c:
        assert H.task(c, tasks[3]["id"])["status"] != "closed"


@pytest.mark.parametrize("text", ["help", "what is waiting on me?", "open tasks"])
def test_a_question_the_old_lookups_answered_starts_a_bot_turn(api, text):
    """No canned answer: "help", "what is waiting on me" and "open tasks" are read by the model in a bot turn."""
    r = runner(api)
    assign(api, r, "coo")
    ready(api, r, ["coo"])
    said = say(api, text)
    assert "reply" not in said and "fast" not in said
    assert post(api, "jobs/claim", {}, token=r["token"])["attempt"] is not None
