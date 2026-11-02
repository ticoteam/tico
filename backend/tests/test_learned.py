"""What a person's chat bot has learned, in two layers (backend/learned.py; spec: Strategy Bot v3, sections 10.7 and
10.9 test 29): the company layer only the owner reads and changes, and only their Assistant uses in the owner's chat; a
personal layer per person that nobody else reads, the owner and admins included. Teaching happens in the person's own
chat, and each turn there is handed both layers as they are when it starts, so an entry deleted now is gone from the
next turn."""

from backend.tests.test_api import api, assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_assistant import say
from backend.tests.test_botops_parity import act
from backend.tests.test_strategy_bot_chat import strategy_bot, personal
from backend.tests.test_member_bots import botops, turn  # noqa: F401  (fixture)


def call(api, method, path, body=None, token="ana-test", expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def learn(api, text, layer="personal", token="ana-test", expected=200):
    return post(api, "assistant/learned", {"layer": layer, "text": text}, token=token, expected=expected)


def claim_turn(api, r, bot, person, text):
    """The bot's next runner turn for what this person said in their own chat."""
    assert say(api, text, person)["fast"] is False
    return post(api, "jobs/claim", {"bot": bot}, token=r["token"])["attempt"]


def finish(api, r, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, token=r["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Done", "last_seq": 0},
         token=r["token"])


def test_only_the_owner_reads_and_changes_the_company_layer(api):
    strategy_bot(api)
    post(api, "company", {"vision": "Every team runs on bots"})
    post(api, "goals", {"owner": "company", "title": "Ship the desktop app"})
    mine = get(api, "assistant/learned")
    assert mine["personal"] == []
    assert (mine["company"]["vision"], [g["title"] for g in mine["company"]["priorities"]], mine["company"]["entries"]) \
        == ("Every team runs on bots", ["Ship the desktop app"], [])
    entry = learn(api, "Launches matter more than hiring news", "company")["entry"]
    assert (entry["layer"], entry["taught_in_chat"]) == ("company", False)
    # An admin and a member see their own layer only, and can neither add to the company's nor touch its entries.
    for token in ("ben-test", "cara-test"):
        assert get(api, "assistant/learned", token)["company"] is None
        learn(api, "Hiring news first", "company", token, expected=403)
        call(api, "PATCH", f"assistant/learned/{entry['id']}", {"text": "Hiring first"}, token, expected=404)
        call(api, "DELETE", f"assistant/learned/{entry['id']}", None, token, expected=404)
    # Without its Strategy Bot there is no company layer, for the owner either.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='goal-manager'")
    assert get(api, "assistant/learned")["company"] is None
    learn(api, "Partners matter most", "company", expected=403)


def test_a_personal_layer_is_its_persons_alone(api):
    ben = learn(api, "Put customer mail first", token="ben-test")["entry"]
    assert [e["text"] for e in get(api, "assistant/learned", "ben-test")["personal"]] == ["Put customer mail first"]
    # Nobody else reads, edits or deletes it: not a member, not the owner. A refusal never says it is there.
    for token in ("cara-test", "ana-test"):
        assert get(api, "assistant/learned", token)["personal"] == []
        call(api, "PATCH", f"assistant/learned/{ben['id']}", {"text": "Mine now"}, token, expected=404)
        call(api, "DELETE", f"assistant/learned/{ben['id']}", None, token, expected=404)
    # Nor does SQL, for the owner either: the table is not one it reads.
    r = api.post("/api/v2/sql", json={"sql": "SELECT text FROM learned_entries"}, headers=headers())
    assert r.status_code == 422 and "prohibited" in r.text, r.text
    # Nor its audit trail: entries are named by id, never by their words.
    with api.app.state.store.read() as c:
        assert "customer mail" not in str([dict(e) for e in c.execute("SELECT * FROM events WHERE action LIKE 'learned.%'")])
    # Ben edits and deletes his own; the same words twice are one entry.
    assert learn(api, "Put customer mail first", token="ben-test")["entry"]["id"] == ben["id"]
    edited = call(api, "PATCH", f"assistant/learned/{ben['id']}", {"text": "Customer mail, then launches"}, "ben-test")
    assert edited["entry"]["text"] == "Customer mail, then launches" and edited["entry"]["updated_at"]
    assert call(api, "DELETE", f"assistant/learned/{ben['id']}", None, "ben-test") == {"deleted": ben["id"]}
    assert get(api, "assistant/learned", "ben-test")["personal"] == []
    # One short line, and a bot of its own has no layer at all.
    learn(api, "x" * 281, token="ben-test", expected=422)
    learn(api, "   ", token="ben-test", expected=422)
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Hello"})
    ops = post(api, "jobs/claim", {"bot": "ops"}, token=r["token"])["attempt"]
    get(api, "assistant/learned", ops["token"], expected=403)
    learn(api, "Bots first", token=ops["token"], expected=403)


def test_botops_working_for_a_person_never_reaches_their_layer(api, botops):
    # BotOps doing what she asked borrows her rights, but it is not her chat: it neither reads her layer (asked as her
    # by default or explicitly) nor adds to it.
    learn(api, "Cara's secret ranking", token="cara-test")
    attempt = turn(api, botops, person="cara-test", text="Fix the deploy")
    for ref in (None, "turn"):
        r = act(api, attempt, "GET", "assistant/learned", ref=ref)
        assert r.status_code == 403 and "secret ranking" not in r.text, r.text
    r = act(api, attempt, "POST", "assistant/learned", {"layer": "personal", "text": "Deploys first"})
    assert r.status_code == 403, r.text
    assert [e["text"] for e in get(api, "assistant/learned", "cara-test")["personal"]] == ["Cara's secret ranking"]


def test_an_assistant_learns_in_its_persons_chat_and_reads_it_fresh_each_turn(api):
    r = runner(api)
    assign(api, r, "coo")
    ready(api, r, ["coo"])
    first = claim_turn(api, r, "coo", "cara-test", "Launches matter more than hiring news, remember that")
    assert first["learned"] == {"personal": []}                    # an Assistant's turn: no company layer, ever
    # Its turn saves what she taught it, in her layer, and may not reach the company's.
    saved = learn(api, "Launches matter more than hiring news", token=first["token"])["entry"]
    assert saved["taught_in_chat"] is True
    learn(api, "Launches first for everyone", "company", first["token"], expected=403)
    # It adds; she edits and deletes, on What it has learned.
    call(api, "PATCH", f"assistant/learned/{saved['id']}", {"text": "Hiring first"}, first["token"], expected=403)
    call(api, "DELETE", f"assistant/learned/{saved['id']}", None, first["token"], expected=403)
    assert [e["text"] for e in get(api, "assistant/learned", "cara-test")["personal"]] == \
        ["Launches matter more than hiring news"]
    finish(api, r, first)
    second = claim_turn(api, r, "coo", "cara-test", "What's the top update today?")
    assert second["learned"] == {"personal": ["Launches matter more than hiring news"]}
    finish(api, r, second)
    # She deletes it: the next turn no longer has it.
    call(api, "DELETE", f"assistant/learned/{saved['id']}", None, "cara-test")
    third = claim_turn(api, r, "coo", "cara-test", "And now?")
    assert third["learned"] == {"personal": []}
    finish(api, r, third)
    # Ben's turn is handed Ben's layer, never Cara's.
    learn(api, "Cara's secret ranking", token="cara-test")
    bens = claim_turn(api, r, "coo", "ben-test", "What's the top update today?")
    assert bens["learned"] == {"personal": []}


def test_the_owners_assistant_learns_both_layers_in_the_owners_chat_and_nowhere_else(api):
    strategy_bot(api)
    post(api, "company", {"vision": "Every team runs on bots"})
    post(api, "goals", {"owner": "company", "title": "Ship the desktop app"})
    learn(api, "Short summaries, numbers first")
    r = runner(api)
    for bot in ("ops", "goal-manager", "coo"):
        assign(api, r, bot)
    ready(api, r, ["ops", "goal-manager", "coo"])
    turn = claim_turn(api, r, "coo", "ana-test", "Launches matter more than hiring news")
    assert turn["learned"]["personal"] == ["Short summaries, numbers first"]
    assert (turn["learned"]["company"]["vision"], turn["learned"]["company"]["priorities"],
            turn["learned"]["company"]["taught"]) == ("Every team runs on bots", ["Ship the desktop app"], [])
    # Its turn saves what she taught it about the company, as her, in the company layer.
    entry = learn(api, "Launches matter more than hiring news", "company", turn["token"])["entry"]
    assert (entry["layer"], entry["taught_in_chat"]) == ("company", True)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT created_by, via FROM learned_entries WHERE id=?", (entry["id"],)).fetchone()
    assert (row["created_by"], row["via"]) == ("human:ana", "bot:coo")
    finish(api, r, turn)
    again = claim_turn(api, r, "coo", "ana-test", "What's the top update today?")
    assert again["learned"]["company"]["taught"] == ["Launches matter more than hiring news"]
    finish(api, r, again)
    # The Strategy Bot's goals session (a bot's message) is handed neither layer, and it reads none on its own.
    post(api, "chat/ops", {"text": "Check the launch goal"})
    ops = post(api, "jobs/claim", {"bot": "ops"}, token=r["token"])["attempt"]
    post(api, "messages", {"to": "goal-manager", "text": "The launch goal slipped", "needs_action": True},
         token=ops["token"])
    goals = post(api, "jobs/claim", {"bot": "goal-manager"}, token=r["token"])["attempt"]
    assert goals["learned"] is None
    get(api, "assistant/learned", goals["token"], expected=403)
    learn(api, "Hiring first", "company", goals["token"], expected=403)


def test_an_owner_who_hands_ownership_on_keeps_their_layer_and_the_company_layer_stays(api):
    ana, ben = personal(api, "ana"), personal(api, "ben")
    strategy_bot(api)
    learn(api, "Numbers first", token=ana)
    learn(api, "Launches matter most", "company", ana)
    owner = get(api, "access", ana)["owner_id"]
    post(api, "access/owner", {"person": "ben", "expected_revision": owner["revision"], "confirm": True}, token=ana)
    # The company layer stays with the owner seat; each person's own layer stays theirs.
    his = get(api, "assistant/learned", ben)
    assert his["personal"] == [] and [e["text"] for e in his["company"]["entries"]] == ["Launches matter most"]
    hers = get(api, "assistant/learned", ana)
    assert [e["text"] for e in hers["personal"]] == ["Numbers first"] and hers["company"] is None
    learn(api, "Hiring matters most", "company", ana, expected=403)


def test_without_the_strategy_bot_the_owners_assistant_has_no_company_layer(api):
    # The company layer comes with the Strategy Bot: before it, the owner's Assistant turn is handed none and may not
    # add to it; her own layer is hers either way.
    r = runner(api)
    assign(api, r, "coo")
    ready(api, r, ["coo"])
    old = claim_turn(api, r, "coo", "ana-test", "Launches matter more than hiring news")
    assert old["learned"] == {"personal": []}
    assert get(api, "assistant/learned", old["token"])["company"] is None
    learn(api, "Launches matter more than hiring news", "company", old["token"], expected=403)
    learn(api, "Launches matter more than hiring news", token=old["token"])
    strategy_bot(api)
    assert [e["text"] for e in get(api, "assistant/learned")["personal"]] == ["Launches matter more than hiring news"]
    assert get(api, "assistant/learned")["company"]["entries"] == []
