"""The Assistant (backend/assistant.py, docs/assistant.md): a private room per person, a fast path that
needs no bot turn, a bot turn that acts as the person and never more, and side effects that wait for the
person's own click."""

import pytest

from backend.tests.test_api import api, as_member, assign, claim, get, headers, post, ready, runner  # noqa: F401
from backend.store import H


def room(api, token="ana-test"):
    return get(api, "assistant", token)


def say(api, text, token="ana-test", expected=200):
    return post(api, "assistant/messages", {"text": text}, token=token, expected=expected)


def jobs(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs").fetchone()[0]


def assistant_turn(api, person, text="Please plan my week around the launch", busy_bots=None):
    """The assistant bot's runner turn for a message this person sent in their Assistant room."""
    r = runner(api)
    assign(api, r, "coo")
    ready(api, r, ["coo"])
    said = say(api, text, person)
    assert said["fast"] is False
    body = {} if busy_bots is None else {"busy_bots": busy_bots}
    return r, post(api, "jobs/claim", body, token=r["token"])["attempt"]


def test_assistant_fetches_only_its_own_credentials_before_start(api, person="ben-test", busy_bots=None):
    from backend.tests.test_credentials import create, setup

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
    made = post(api, "tasks", {"owner": "human:" + person.removesuffix("-test"),
                               "title": "Draft my week", "body": "Monday first."}, token=token)
    assert made["requester"] == "human:" + person.removesuffix("-test")
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0}, token=r["token"])
    get(api, "credential-runtime", token, expected=409)


def test_only_the_owner_of_an_assistant_room_reads_or_posts_in_it(api):
    ana, ben = room(api, "ana-test"), room(api, "ben-test")
    assert ana["available"] and ana["room_id"] and ana["room_id"] != ben["room_id"]
    assert room(api, "ana-test")["room_id"] == ana["room_id"]          # one room each, however often asked
    say(api, "My private question about the launch", "ana-test", expected=200)
    # Not the other person, and not the company owner either.
    get(api, f"conversations/{ana['room_id']}/messages", "ben-test", expected=403)
    get(api, f"conversations/{ben['room_id']}/messages", "ana-test", expected=403)
    post(api, "messages", {"to": "coo", "text": "Injected", "conversation_id": ana["room_id"]}, token="ben-test",
         expected=403)
    post(api, "messages", {"to": "coo", "text": "Injected", "conversation_id": ben["room_id"]}, token="ana-test",
         expected=403)
    post(api, "chat/coo", {"text": "Anywhere else"}, token="ben-test", expected=403)
    assert [m["body"] for m in room(api, "ben-test")["messages"]] == []
    # The owner of the room may also use the generic message route; the server marks the turn.
    ok = post(api, "messages", {"to": "coo", "text": "Also fine", "conversation_id": ana["room_id"]})
    assert ok["refs"]["assistant"] is True


def test_the_assistant_acts_as_the_person_and_never_more(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:      # the private inbox bot's work: ana's, not ben's
        secret = H.task_create(c, "human:ana", "Review the private mail", "Nothing for ben.", "bot:inbox")["id"]
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    assert get(api, "me", token)["actor"] == "human:ben"           # its tools are ben's, not the bot's
    # What ben may not see or touch, his Assistant may not.
    get(api, "tasks/" + secret, token, expected=404)
    post(api, "tasks/" + secret + "/comments", {"text": "Looking"}, token=token, expected=403)
    # Nothing that changes settings, people or bots runs on its own, whoever it acts for.
    post(api, "bots/ops/archive", {"expected_revision": 1}, token=token, expected=403)
    assert api.put("/api/v2/providers", json={}, headers=headers(token)).json()["error"]["code"] == "confirm_required"
    # Settling a task is deciding a Needs-you item: it is proposed, never done directly.
    mine = post(api, "tasks", {"owner": "human:ben", "title": "Pick the launch date", "body": "Oct 1 or Oct 8?"}, token=token)
    post(api, "tasks/" + mine["id"], {"version": mine["version"], "status": "done"}, token=token, expected=403)
    # A low-risk write runs directly, as ben, and says so in the record.
    made = post(api, "tasks", {"owner": "human:ben", "title": "Draft the launch post", "body": "For Oct 1."}, token=token)
    assert made["requester"] == "human:ben"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT via FROM task_events WHERE task_id=? ORDER BY ts LIMIT 1", (made["id"],)).fetchone()[0] == "assistant"
        assert c.execute("SELECT count(*) FROM events WHERE detail_json LIKE '%\"via\": \"assistant\"%'").fetchone()[0] > 0
    # The runner's own lease calls are still the runner's.
    post(api, f"attempts/{attempt['id']}/renew", {}, token=r["token"], expected=200)


def test_a_side_effect_runs_only_after_the_person_confirms(api):
    r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    operation = {"summary": "Archive the Ops bot", "method": "POST", "path": "/api/v2/bots/ops/archive",
                 "body": {"expected_revision": revision}}
    # The bot cannot do it itself, and only proposes.
    post(api, "bots/ops/archive", operation["body"], token=token, expected=403)
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
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test", expected=409)   # once
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='assistant.action.confirmed'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM events WHERE action LIKE 'bot.%' AND detail_json LIKE '%\"via\": \"assistant\"%'"
                         ).fetchone()[0] >= 1
    # A cancelled proposal never runs.
    other = post(api, "assistant/actions", {"summary": "Archive Finance", "path": "/api/v2/bots/finance/archive",
                                            "body": {"expected_revision": 1}}, token="ana-test")["action"]
    post(api, f"assistant/actions/{other['id']}/cancel", {}, token="ana-test")
    post(api, f"assistant/actions/{other['id']}/confirm", {}, token="ana-test", expected=409)


def test_a_proposal_cannot_smuggle_a_route_and_never_stores_an_answer(api):
    for path in ("/api/v2/me/tokens", "/api/v2/tasks/../me/tokens"):
        post(api, "assistant/actions", {"summary": "Do it", "path": path}, token="ana-test", expected=422)
    # The stored path is exactly what runs; only the status and an error detail are kept, never the answer.
    action = post(api, "assistant/actions", {"summary": "File it", "path": "/api/v2/tasks",
                                             "body": {"title": "Draft the launch plan", "body": "Two pages.", "owner": "human:ana"}})["action"]
    done = post(api, f"assistant/actions/{action['id']}/confirm", {})["action"]
    assert done["status"] == "done" and set(done["result"]) == {"status_code", "error"}
    assert "Draft the launch plan" not in str(get(api, f"assistant/actions/{action['id']}")["action"]["result"])


def test_an_assistant_message_never_lends_the_persons_authority_to_botops(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('botops','BotOps','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('botops','{}',NULL,'ana')")
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
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    # Nothing that reaches another person, or spends, or changes a task that is not theirs alone, runs directly.
    post(api, "tasks", {"owner": "human:cara", "title": "Review the plan", "body": "Please."}, token=token, expected=403)
    post(api, "messages", {"to": "human:cara", "text": "Hello"}, token=token, expected=403)
    post(api, "messages", {"to": "cara", "text": "Hello"}, token=token, expected=403)                # a bare name may be a person
    post(api, f"tasks/{theirs['id']}/run-now", {}, token=token, expected=403)
    post(api, f"tasks/{theirs['id']}", {"version": theirs["version"], "owner": "ops"}, token=token, expected=403)
    post(api, f"tasks/{anas['id']}", {"version": anas["version"], "note": "x"}, token=token, expected=403)   # not ben's
    post(api, f"tasks/{theirs['id']}/comments", {"text": "Started"}, token=token, expected=403)   # Ana is on it: it tells her
    post(api, "notes", {"to": "ops", "text": "Remember the launch"}, token=token, expected=403)      # no unmarked notes
    post(api, "tasks", {"owner": "ben", "title": "Draft my week", "body": "Monday."}, token=token, expected=403)  # a bare name
    # A task for a bot, a comment on it and a message to a bot stay in the team: they run directly, as ben.
    made = post(api, "tasks", {"owner": "ops", "title": "Draft the launch post", "body": "For Oct 1."}, token=token)
    assert made["owner"] == "bot:ops" and made["requester"] == "human:ben"
    post(api, f"tasks/{botwork['id']}/comments", {"text": "Please hurry"}, token=token)
    post(api, "messages", {"to": "ops", "text": "Hello"}, token=token)
    post(api, "chat/ops", {"text": "Hello again"}, token=token)
    # And what only touches them, as before.
    post(api, "tasks", {"owner": "human:ben", "title": "Draft my week", "body": "Monday first."}, token=token)
    post(api, f"tasks/{theirs['id']}", {"version": theirs["version"], "note": "On it"}, token=token)
    post(api, "updates/read", {"all": True}, token=token)
    with api.app.state.store.read() as c:
        # What it wrote is marked, so BotOps ignores it.
        assert c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:ops' AND refs_json LIKE '%\"via\": \"assistant\"%'"
                         ).fetchone()[0] >= 2


def test_real_assistant_lease_keeps_actual_bot_private_boundaries_and_revocation(api):
    from backend.store import Problem
    private = post(api, 'tasks', {'owner': 'human:ben', 'title': 'Review the confidential packet',
                                 'body': 'Confidential packet.', 'private': True})
    attached = post(api, 'tasks/' + private['id'] + '/files', {'name': 'packet.md', 'text': 'Confidential attachment.'})
    r, attempt = assistant_turn(api, 'ben-test')
    token = attempt['token']
    who = api.app.state.auth.authenticate({'authorization': 'Bearer ' + token},
                                          '/api/v2/tasks/' + private['id'], 'GET')
    assert who.task_actor == 'bot:coo' and who.attempt_id == attempt['id']
    for path in ('tasks/' + private['id'],
                 'conversations/' + private['conversation_id'] + '/messages',
                 'files/' + attached['file']['id']):
        response = api.get('/api/v2/' + path, headers=headers(token))
        assert response.status_code in (403, 404), response.text
        assert 'Confidential' not in response.text
    sql = api.post('/api/v2/sql', json={'sql': 'SELECT id,body FROM tasks WHERE id=?',
                                      'params': [private['id']]}, headers=headers(token))
    assert sql.status_code == 403 and sql.json()['error']['code'] == 'confirm_required', sql.text
    assert 'Confidential' not in sql.text
    from backend.sql import connect, run
    with api.app.state.store.read() as c:
        conn = connect(api.app.state.store.settings.db_path, c, api.app.state.auth, who)
        try:
            assert run(conn, 'SELECT id,body FROM tasks WHERE id=?', [private['id']], 500, 5)['rows'] == []
        finally:
            conn.close()
    own = post(api, 'tasks', {'owner': 'bot:coo', 'title': 'Review the assistant packet',
                            'body': 'Review it.', 'private': True}, token='ben-test')
    assert get(api, 'tasks/' + own['id'], token)['task']['id'] == own['id']
    published = api.post('/api/v2/tasks/' + own['id'], json={'version': own['version'], 'private': False},
                         headers=headers(token))
    assert published.status_code in (403, 422) and published.json()['error']['code'] in ('privacy', 'confirm_required')
    assert get(api, 'tasks/' + own['id'], 'ben-test')['task']['private']
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE id=?", (attempt['id'],))
    with pytest.raises(Problem) as revoked:
        api.app.state.store.write(who, lambda c: {})
    assert revoked.value.status == 409
