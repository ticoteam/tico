"""The Assistant (backend/assistant.py, docs/assistant.md): a private room per person, a fast path that
needs no bot turn, a bot turn that acts as the person and never more, and side effects that wait for the
person's own click."""

import pytest

from backend import assistant
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


def asked(api, path, body, token, ref, expected=200):
    """A write of the assistant's turn that cites the person's message asking for it."""
    r = api.post("/api/v2/" + path, json=body, headers={**headers(token), "X-Tico-On-Behalf-Of": ref})
    assert r.status_code == expected, r.text
    return r.json()


def turn_message(api, attempt):
    with api.app.state.store.read() as c:
        return c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                         (attempt["id"],)).fetchone()[0]


def test_the_assistant_does_the_task_actions_the_person_asked_for_in_their_chat(api):
    with api.app.state.store.transaction() as c:
        review = H.task_create(c, "human:ana", "Review the plan", "Please review.", "human:ben")
        launch = H.task_create(c, "human:ben", "Plan the launch", "For ops.", "bot:ops")
        old = H.task_create(c, "human:ben", "Book the venue", "Last year's.", "human:ben")
    r, attempt = assistant_turn(api, "ben-test", "Mark the plan review done, close the launch and the venue tasks")
    token, said = attempt["token"], turn_message(api, attempt)
    # Without citing what they said it is a card, as before.
    refused = post(api, f"tasks/{launch['id']}", {"version": launch["version"], "close": True}, token=token, expected=403)
    assert refused["error"]["code"] == "confirm_required"
    # Citing the message that started this run, it runs as ben, with no click.
    done = asked(api, f"tasks/{review['id']}", {"version": review["version"], "status": "done", "note": "Reviewed"},
                 token, "turn")["task"]
    assert done["status"] == "done"
    closed = asked(api, f"tasks/{launch['id']}", {"version": launch["version"], "close": True}, token, said)["task"]
    assert closed["status"] == "closed"
    # Reopen, comment (Ana is on it) and hand it to someone else: the same task actions the cards carry.
    reopened = asked(api, f"tasks/{review['id']}", {"version": done["version"], "status": "open"}, token, "turn")["task"]
    assert reopened["status"] == "open"
    asked(api, f"tasks/{review['id']}/comments", {"text": "Reopened: one more pass"}, token, "turn")
    handed = asked(api, f"tasks/{old['id']}", {"version": old["version"], "owner": "human:ana"}, token, "turn")["task"]
    assert handed["owner"] == "human:ana"
    with api.app.state.store.read() as c:
        # Recorded as ben, via the assistant, for the message he sent.
        row = c.execute("SELECT actor,via FROM task_events WHERE task_id=? AND field='status' ORDER BY ts DESC LIMIT 1",
                        (launch["id"],)).fetchone()
        assert (row["actor"], row["via"]) == ("human:ben", "assistant")
        close = c.execute("SELECT actor,detail_json FROM events WHERE action='task.close' AND target=?",
                          (launch["id"],)).fetchone()
        detail = H._json(close["detail_json"], {})
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
            r = api.request(method, "/api/v2/" + path, json=body, params=query, headers={**headers(self.token), **extra})
            if r.status_code >= 400:
                raise APIError(r.json()["error"]["code"], r.json()["error"]["detail"], status=r.status_code)
            return r.json()

        def get(self, path, **query):
            return self.call("GET", path, query=query)

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body or {}, key)
    with api.app.state.store.transaction() as c:
        launch = H.task_create(c, "human:ben", "Plan the launch", "For ops.", "bot:ops")
    r, attempt = assistant_turn(api, "ben-test", "Close the launch task")
    turn = Turn(attempt["token"])
    with pytest.raises(APIError) as refused:
        hubtools.BY_NAME["hub_task_close"]["fn"](turn, {"id": launch["id"]})
    assert refused.value.code == "confirm_required"
    closed = hubtools.BY_NAME["hub_task_close"]["fn"](turn, {"id": launch["id"], "note": "As you asked", "on_behalf_of": "turn"})
    assert closed["task"]["status"] == "closed"
    assert "hub_task_close" in {t["name"] for t in hubtools.listing(kind="assistant")}


def test_a_cited_message_must_be_the_persons_own_recent_words_in_their_assistant_chat(api):
    with api.app.state.store.transaction() as c:
        mine = H.task_create(c, "human:ben", "Plan the launch", "For ops.", "bot:ops")
    r, attempt = assistant_turn(api, "ben-test", "Close the launch task")
    token, said = attempt["token"], turn_message(api, attempt)
    say(api, "Close Ben's launch task", "ana-test")
    other_person = room(api, "ana-test")["messages"][-1]
    assert other_person["from_actor"] == "human:ana"
    other_person = other_person["id"]
    elsewhere = post(api, "chat/ops", {"text": "Close the launch task"}, token="ben-test")["id"]
    with api.app.state.store.transaction() as c:
        bens = assistant.find_room(c, "human:ben")
        from_bot = H._write_message(c, "bot:coo", "human:ben", "Close the launch task", bens, "say",
                                    {"assistant": True}, None, None, delivered_at=H.now())["id"]
    body = {"version": mine["version"], "close": True}
    path = f"tasks/{mine['id']}"
    for ref, why in ((other_person, "own Assistant chat"), (elsewhere, "own Assistant chat"),
                     (from_bot, "own Assistant chat"), ("no-such-message", "own Assistant chat")):
        refused = asked(api, path, body, token, ref, expected=403)
        assert refused["error"]["code"] == "on_behalf_of" and why in refused["error"]["detail"], refused
    # Not a person's own credential either: a citation lends nothing to anyone but their Assistant's run.
    plain = api.post("/api/v2/" + path, json=body, headers={**headers("ben-test"), "X-Tico-On-Behalf-Of": said})
    assert plain.status_code == 403
    # Only task actions: run-now, an approval and a message to another person stay cards whatever is cited.
    assert asked(api, f"tasks/{mine['id']}/run-now", {}, token, "turn", expected=403)["error"]["code"] == "confirm_required"
    assert asked(api, "messages", {"to": "human:ana", "text": "Hi"}, token, "turn", expected=403)["error"]["code"] == "confirm_required"
    # When the owner turns "Assistant acts without asking" off, asking in chat is a card again.
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    assert asked(api, path, body, token, "turn", expected=403)["error"]["code"] == "confirm_required"
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200
    # Too old: they have to ask again.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET created=? WHERE id=?", (H.shift(H.now(), hours=-2), said))
    refused = asked(api, path, body, token, "turn", expected=403)
    assert refused["error"]["code"] == "on_behalf_of" and "minutes old" in refused["error"]["detail"]
    assert get(api, path, "ben-test")["task"]["status"] != "closed"


def test_the_person_asking_never_lends_more_than_their_own_rights(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:      # the private inbox bot's work: ana's, not ben's
        secret = H.task_create(c, "human:ana", "Review the private mail", "Nothing for ben.", "bot:inbox")
    post(api, f"tasks/{secret['id']}", {"version": secret["version"], "close": True}, token="ben-test", expected=404)
    r, attempt = assistant_turn(api, "ben-test", "Close the private mail review")
    token = attempt["token"]
    asked(api, f"tasks/{secret['id']}", {"version": secret["version"], "close": True}, token, "turn", expected=404)
    asked(api, f"tasks/{secret['id']}/comments", {"text": "Closing"}, token, "turn", expected=404)
    with api.app.state.store.read() as c:
        assert H.task(c, secret["id"])["status"] == "open"


def test_asking_in_chat_never_decides_an_approval(api):
    with api.app.state.store.transaction() as c:
        approvals = [H.approval_request(c, "bot:ops", kind, payload)["id"] for kind, payload in (
            ("send", {"to": "x@acme.example", "cc": [], "subject": "Hi", "body_sha256": "a" * 64, "mailbox": "work"}),
            ("spend", {"amount": 500, "account": "card", "what": "Ads"}),
            ("publish", {"url": "https://acme.example/post", "content_sha256": "b" * 64}),
            ("merge", {"repo": "acme/site", "pr": 7}))]
    r, attempt = assistant_turn(api, "ana-test", "Approve all four: the email, the spend, the post and the merge")
    token = attempt["token"]
    for aid in approvals:
        for ref in ("turn", turn_message(api, attempt)):
            refused = asked(api, "approvals/" + aid, {"decision": "approved"}, token, ref, expected=403)
            assert refused["error"]["code"] == "confirm_required"
        with api.app.state.store.read() as c:
            assert H.approval(c, aid)["decision"] is None
    # The person's own decision still works, as today.
    post(api, "approvals/" + approvals[0], {"decision": "approved"})


def test_a_group_of_task_actions_is_one_card_and_one_click(api):
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, "human:ben", f"Old task {i}", "Stale.", "bot:ops") for i in range(25)]
        secret = H.task_create(c, "human:ana", "Review the private mail", "Nothing for ben.", "bot:inbox")
        approval = H.approval_request(c, "bot:ops", "spend", {"amount": 500, "account": "card", "what": "Ads"})["id"]
    r, attempt = assistant_turn(api, "ben-test", "Clear out all my old tasks")
    token = attempt["token"]
    steps = [{"path": f"/api/v2/tasks/{t['id']}", "body": {"close": True, "note": "Cleared"}} for t in tasks]
    steps.append({"path": f"/api/v2/tasks/{secret['id']}", "body": {"close": True}})       # not his to close
    action = post(api, "assistant/actions", {"summary": "Close your 25 old tasks", "path": assistant.BATCH_PATH,
                                             "body": {"actions": steps}}, token=token)["action"]
    assert action["description"] == "26 task actions, on one Confirm" and len(action["diff"]) == 26
    assert "Old task 0" in action["diff"][0]["field"] and "close: True" in action["diff"][0]["new"]
    pending = get(api, "assistant", "ben-test")["pending"]
    assert [p["id"] for p in pending] == [action["id"]]                                  # one card, not 26
    # Only task actions go on a bulk card: an approval, running a task now or anything else is refused.
    for odd in ({"path": "/api/v2/approvals/" + approval, "body": {"decision": "approved"}},
                {"path": f"/api/v2/tasks/{tasks[0]['id']}/run-now", "body": {}},
                {"path": "/api/v2/messages", "body": {"to": "human:ana", "text": "Hi"}}):
        post(api, "assistant/actions", {"summary": "Sneak", "path": assistant.BATCH_PATH,
                                        "body": {"actions": [steps[0], odd]}}, token=token, expected=422)
    post(api, f"assistant/actions/{action['id']}/confirm", {}, token=token, expected=403)   # never the bot's own click
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ben-test")["action"]
    assert done["status"] == "done", done
    assert done["result"]["error"].startswith("1 of 26 did not go through")
    assert [i["status_code"] for i in done["result"]["items"]] == [200] * 25 + [404]
    with api.app.state.store.read() as c:
        assert all(H.task(c, t["id"])["status"] == "closed" for t in tasks)
        assert H.task(c, secret["id"])["status"] == "open"
        assert H.approval(c, approval)["decision"] is None
        assert c.execute("SELECT count(*) FROM task_events WHERE field='status' AND new='closed' AND actor='human:ben' "
                         "AND via='assistant'").fetchone()[0] == 25


def test_a_close_card_without_the_tasks_version_still_runs(api):
    with api.app.state.store.transaction() as c:
        launch = H.task_create(c, "human:ana", "Plan the launch", "For ops.", "bot:ops")
    action = post(api, "assistant/actions", {"summary": "Close the launch task", "path": f"/api/v2/tasks/{launch['id']}",
                                             "body": {"close": True}})["action"]
    assert action["body"]["version"] == launch["version"]
    done = post(api, f"assistant/actions/{action['id']}/confirm", {})["action"]
    assert done["status"] == "done", done["result"]


def test_a_close_card_written_as_status_closed_runs_as_a_close(api):
    """The cards an assistant wrote for "close these" carried `{"status": "closed"}` and no version: every Confirm
    failed with "body.version: Field required; body.status: Input should be ...". A card like that, new or already
    waiting, now closes the task with the version it has when the person clicks."""
    import json
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, "human:ana", f"Old task {i}", "Stale.", "bot:ops") for i in range(3)]
    # One stored before this release, exactly as the assistant wrote it.
    stale = post(api, "assistant/actions", {"summary": "Close old task 0", "path": f"/api/v2/tasks/{tasks[0]['id']}",
                                            "body": {"version": 1}})["action"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE assistant_actions SET body_json=? WHERE id=?", (json.dumps({"status": "closed"}), stale["id"]))
    done = post(api, f"assistant/actions/{stale['id']}/confirm", {})["action"]
    assert done["status"] == "done", done["result"]
    # A new one is stored as the close it is, and so is each step of a bulk card.
    fresh = post(api, "assistant/actions", {"summary": "Close old task 1", "path": f"/api/v2/tasks/{tasks[1]['id']}",
                                            "body": {"status": "closed", "note": "Stale"}})["action"]
    assert fresh["body"] == {"close": True, "note": "Stale", "version": tasks[1]["version"]}
    bulk = post(api, "assistant/actions", {"summary": "Close the rest", "path": assistant.BATCH_PATH, "body": {"actions": [
        {"path": f"/api/v2/tasks/{tasks[2]['id']}", "body": {"status": "closed"}}]}})["action"]
    for action in (fresh, bulk):
        assert post(api, f"assistant/actions/{action['id']}/confirm", {})["action"]["status"] == "done"
    with api.app.state.store.read() as c:
        assert [H.task(c, t["id"])["status"] for t in tasks] == ["closed"] * 3


def no_decisions(monkeypatch):
    calls = []
    async def unavailable(*args, **kwargs):
        calls.append(args)
        return None
    monkeypatch.setattr(assistant, "decide", unavailable)
    return calls


@pytest.mark.parametrize("text", [
    "what kind of task types do we use here?",
])
def test_task_types_question_is_answered_from_the_api_with_no_model_or_job(api, monkeypatch, text):
    post(api, "task-types", {"name": "Fixture repairs", "steps": [{"name": "Triage", "status": "open"}]})
    decisions = no_decisions(monkeypatch)
    before = jobs(api)
    result = post(api, "assistant/messages", {"text": text}, key="task-types")
    assert result["fast"] is True
    assert not decisions, "A simple inventory must not wait for an intent provider"
    answer = result["reply"]["body"]
    assert "General" in answer and "Fixture repairs" in answer
    assert "From [Task types and steps]" not in answer
    # Dev tickets is an example in the documentation, not in this company's inventory.
    assert "Dev tickets" not in answer
    retry = post(api, "assistant/messages", {"text": text}, key="task-types")
    assert retry["reply"]["id"] == result["reply"]["id"]
    messages = room(api)["messages"]
    assert len(messages) == 2
    assert jobs(api) == before


def test_a_greeting_is_answered_at_once_with_no_model_or_job(api, monkeypatch):
    decisions = no_decisions(monkeypatch)
    before = jobs(api)
    result = post(api, "assistant/messages", {"text": "hi"}, key="greeting")
    assert result["fast"] is True and result["reply"]["body"]
    assert not decisions, "A simple greeting must not wait for an intent provider"
    retry = post(api, "assistant/messages", {"text": "hi"}, key="greeting")
    assert retry["reply"]["id"] == result["reply"]["id"]
    assert len(room(api)["messages"]) == 2 and jobs(api) == before


@pytest.mark.parametrize("status_code", [403])
def test_task_types_the_api_refuses_are_not_shown_as_none(api, monkeypatch, status_code):
    decisions = no_decisions(monkeypatch)
    calls = []

    async def rejected(_self, method, path, **kwargs):
        calls.append((method, path))
        return type("Response", (), {"status_code": status_code})()

    monkeypatch.setattr(assistant.Internal, "call", rejected)
    before = jobs(api)
    result = post(api, "assistant/messages", {"text": "what task types do we use?"}, key="task-types-refused")
    assert result["fast"] is True
    assert result["reply"]["body"] == "I couldn't read the task types for this Tico workspace. Please try again later."
    assert calls == [("GET", "task-types")]
    assert not decisions
    assert jobs(api) == before


def test_how_to_questions_get_the_docs_and_a_greeting_with_work_goes_to_the_model(api, monkeypatch):
    result = post(api, "assistant/messages", {"text": "How do I create a task type?"})
    assert result["fast"] is True and result["intent"] == "help"
    assert "From [" in result["reply"]["body"]
    async def no_decision(*args, **kwargs):
        return None
    monkeypatch.setattr(assistant, "decide", no_decision)
    result = post(api, "assistant/messages", {"text": "Hi, please plan tomorrow's launch"})
    assert result["fast"] is False


@pytest.mark.parametrize("text", [
    "what task types do we use and create one?",
])
def test_task_types_question_that_also_asks_for_a_change_goes_to_the_model(text):
    assert assistant.route(text) == (None, None)
