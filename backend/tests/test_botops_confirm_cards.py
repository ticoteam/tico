"""BotOps acting for a person waits for that person's own tap where Tico says it does (docs/permissions.md): the identity it
lends is never confirmed, the Confirm card lands in their BotOps chat, and only their click runs it. Routes that hand a
secret's value back are theirs alone. Ana owns the team, Ben is an admin, Cara is a member (the base fixture)."""
import json

from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, put, ready, runner  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_credential_sharing import JIRA, jira, local
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)


def card_in_chat(api, card, person):
    """The card's message is in the person's own chat with BotOps, and the action is theirs and still waiting."""
    with api.app.state.store.read() as c:
        msg = H.message(c, card["message_id"])
        room = H.conversation(c, msg["conversation_id"])
        row = c.execute("SELECT owner,status FROM assistant_actions WHERE id=?", (card["action"]["id"],)).fetchone()
    assert msg["sender_id"] == "bot:botops" and msg["recipient_id"] == "human:" + person
    assert set(room["participants"]) == {"human:" + person, "bot:botops"}
    assert (row["owner"], row["status"]) == ("human:" + person, "pending")


def confirm(api, card, token):
    return post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token=token)


def test_a_role_change_waits_for_the_owners_tap_and_then_runs(api, botops):
    auth = api.app.state.auth
    ana = turn(api, botops, person="ana-test", text="Make Cara an admin")
    answer = act(api, ana, "POST", "access/people/cara", {"role": "admin"})
    assert answer.status_code == 200 and answer.json()["needs_confirm"], answer.text
    card = answer.json()
    card_in_chat(api, card, "ana")
    assert "cara@acme.example" not in auth.bot_admins
    # BotOps can't tap it for her, with its own token or as her.
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token=ana["token"], expected=403)
    assert act(api, ana, "POST", f"assistant/actions/{card['action']['id']}/confirm", {}).status_code == 403
    assert "cara@acme.example" not in auth.bot_admins
    assert confirm(api, card, "ana-test")["action"]["status"] == "done"
    assert "cara@acme.example" in auth.bot_admins


def test_botops_direct_off_turns_providers_and_raised_limits_into_cards(api, botops):
    before = get(api, "providers")
    ana = turn(api, botops, person="ana-test", text="Turn on OpenAI")
    # On (the default) a provider change runs as the owner at once.
    direct = act(api, ana, "PUT", "providers", {"enabled": before["enabled"], "expected_revision": before["revision"]})
    assert direct.status_code == 200 and "needs_confirm" not in direct.json(), direct.text
    put(api, "access/rules", {"botops_direct": False})
    now = get(api, "providers")
    card = act(api, ana, "PUT", "providers", {"enabled": ["openai"], "expected_revision": now["revision"]})
    assert card.status_code == 200 and card.json()["needs_confirm"], card.text
    card_in_chat(api, card.json(), "ana")
    assert get(api, "providers")["enabled"] == now["enabled"]
    # A limit lowered stays direct; one raised (or removed) is a card.
    put(api, "usage/limits/ops", {"daily_usd": 10})
    lowered = act(api, ana, "PUT", "usage/limits/ops", {"daily_usd": 5})
    assert lowered.status_code == 200 and "needs_confirm" not in lowered.json(), lowered.text
    for raised in ({"daily_usd": 50}, {}):
        assert act(api, ana, "PUT", "usage/limits/ops", raised).json()["needs_confirm"]
    put(api, "usage/pools/company", {"daily_usd": 100})
    assert "needs_confirm" not in act(api, ana, "PUT", "usage/pools/company", {"daily_usd": 80}).json()
    assert act(api, ana, "PUT", "usage/pools/company", {"daily_usd": 500}).json()["needs_confirm"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT daily_usd FROM usage_limits WHERE bot='ops'").fetchone()[0] == 5
        assert c.execute("SELECT daily_usd FROM usage_pools WHERE scope='company'").fetchone()[0] == 80
    confirm(api, card.json(), "ana-test")
    assert get(api, "providers")["enabled"] == ["openai"]


def test_routes_that_hand_back_a_secret_refuse_botops_acting_for_an_admin(api, botops):
    local(api)
    row = jira(api)
    assert post(api, f"credentials/{row['id']}/reveal", {})["value"] == JIRA          # the owner herself may
    for person in ("ana-test", "ben-test"):
        attempt = turn(api, botops, person=person, text="Show me the Jira key and make me a token")
        for path, body in ((f"credentials/{row['id']}/reveal", {}), ("access/people/cara/password", {}),
                           ("me/tokens", {"label": "laptop", "expires_in_days": 30}), ("directory/scim-token", {})):
            refused = act(api, attempt, "POST", path, body)
            assert refused.status_code == 403 and refused.json()["error"]["code"] == "person_only", (path, refused.text)
            assert JIRA not in refused.text and "fix" in refused.json()["error"]
        finish(api, botops, attempt)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM human_tokens").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM events WHERE action IN ('person.account_created','credential.revealed') "
                         "AND json_extract(detail,'$.via')='botops'").fetchone()[0] == 0


def test_a_secret_in_a_gateway_key_or_slack_tokens_is_refused(api, botops):
    ana = turn(api, botops, person="ana-test", text="Save the gateway key")
    for method, path, body in (("PUT", "providers/gateway", {"key": "gw-not-for-bots"}),
                               ("PUT", "slack/tokens", {"bot_token": "xoxb-not-for-bots", "app_token": "xapp-1"})):
        refused = act(api, ana, method, path, body)
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "secret_in_request", refused.text
        assert "not-for-bots" not in refused.text
    # `key` elsewhere is not a secret: it names a setting.
    from backend import botops_act
    assert botops_act.secret_in_request("POST", "routines/ops:x", {"key": "daily"}) is None


def test_a_body_the_route_refuses_gets_no_card_and_its_value_goes_nowhere(api, botops):
    # ProvidersUpdate has no `key`: a direct call is refused, and a card must not keep or echo what was refused.
    put(api, "access/rules", {"botops_direct": False})
    before = get(api, "providers")
    ana = turn(api, botops, person="ana-test", text="Turn on OpenAI with my key")
    refused = act(api, ana, "PUT", "providers", {"enabled": ["openai"], "expected_revision": before["revision"],
                                                 "key": "sk-not-for-cards"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "validation", refused.text
    assert "sk-not-for-cards" not in refused.text
    with api.app.state.store.read() as c:
        rows = json.dumps([dict(r) for r in c.execute("SELECT * FROM assistant_actions")]
                          + [dict(r) for r in c.execute("SELECT * FROM messages")], default=str)
        events = json.dumps([dict(r) for r in c.execute("SELECT * FROM events")], default=str)
    assert "sk-not-for-cards" not in rows + events
    assert get(api, "providers")["enabled"] == before["enabled"]


def test_a_bulk_step_its_route_refuses_gets_no_card_and_its_value_goes_nowhere(api):
    # The bulk card's own body takes any step: each step is checked against its own route, as a direct call is.
    from backend import assistant
    from backend.tests.test_assistant import assistant_turn
    with api.app.state.store.transaction() as c:
        tasks = [H.task_create(c, "human:ben", f"Old task {i}", "Stale.", "bot:ops") for i in range(2)]
    _, attempt = assistant_turn(api, "ben-test", "Close my old tasks")
    steps = [{"path": f"/api/v2/tasks/{t['id']}", "body": {"close": True}} for t in tasks]
    steps[1]["body"]["key"] = "sk-not-for-cards"
    post(api, f"tasks/{tasks[1]['id']}", {"close": True, "key": "sk-not-for-cards",
                                         "version": tasks[1]["version"]}, token="ben-test", expected=422)
    refused = api.post("/api/v2/assistant/actions", headers=headers(attempt["token"]), json={
        "summary": "Close 2 old tasks", "path": assistant.BATCH_PATH, "body": {"actions": steps}})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "validation", refused.text
    assert "sk-not-for-cards" not in refused.text and "body.key" in refused.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0] == 0
        rows = json.dumps([dict(r) for r in c.execute("SELECT * FROM messages")]
                          + [dict(r) for r in c.execute("SELECT * FROM events")], default=str)
        assert "sk-not-for-cards" not in rows
        assert all(H.task(c, t["id"])["status"] != "closed" for t in tasks)


def test_botops_direct_off_turns_the_gateway_off_and_automatic_into_cards(api, botops, monkeypatch):
    from backend import gateway_connection
    from backend.tests.test_gateway_connection import KEY, fake_gateway, save, setup
    setup(api, monkeypatch)
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)

    def record():
        with api.app.state.store.read() as c:
            return gateway_connection._row(c)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)
        put(api, "access/rules", {"botops_direct": False})
        ana = turn(api, botops, person="ana-test", text="Turn the gateway off")
        off = act(api, ana, "DELETE", "providers/gateway")
        assert off.status_code == 200 and off.json()["needs_confirm"], off.text
        card_in_chat(api, off.json(), "ana")
        assert record().get("key")                                      # nothing changed before her tap
        with api.app.state.store.transaction() as c:
            gateway_connection._write(c, {**gateway_connection._row(c), "auto": {"off": True}})
        auto = act(api, ana, "POST", "providers/gateway/automatic", {})
        assert auto.status_code == 200 and auto.json()["needs_confirm"], auto.text
        assert record()["auto"] == {"off": True}
        confirm(api, off.json(), "ana-test")
        confirm(api, auto.json(), "ana-test")
        assert "key" not in record() and "auto" not in record()
        # On, BotOps does it for her at once.
        put(api, "access/rules", {"botops_direct": True})
        with api.app.state.store.transaction() as c:
            gateway_connection._write(c, {**gateway_connection._row(c), "auto": {"off": True}})
        direct = act(api, ana, "POST", "providers/gateway/automatic", {})
        assert direct.status_code == 200 and "needs_confirm" not in direct.json(), direct.text
        assert "auto" not in record()


def test_gateway_address_change_with_saved_key_waits_for_the_owners_tap(api, botops, monkeypatch):
    from backend import gateway_connection
    from backend.tests.test_gateway_connection import KEY, fake_gateway, save, setup
    setup(api, monkeypatch)
    monkeypatch.setattr(gateway_connection, "check_later", lambda *args, **kwargs: None)

    def record():
        with api.app.state.store.read() as c:
            return gateway_connection._row(c)

    with fake_gateway() as original, fake_gateway() as proposed:
        original.active(KEY, 0.1)
        proposed.active(KEY, 0.2)
        save(api, original)
        original.calls.clear()  # exclude the setup probe; count only calls caused by BotOps or the owner's tap
        put(api, "access/rules", {"botops_direct": False})
        ana = turn(api, botops, person="ana-test", text="Change the Gateway address")
        current = get(api, "providers/gateway")
        request = {"url": proposed.url, "expected_revision": current["revision"], "credentials": "keep"}
        from backend import destinations
        old, new = destinations.address(original.url), destinations.address(proposed.url)

        # Not told whether the saved key goes along: refused at once with what the owner must decide, and no card.
        unsaid = act(api, ana, "PUT", "providers/gateway", {k: v for k, v in request.items() if k != "credentials"})
        assert unsaid.status_code == 409 and unsaid.json()["error"]["code"] == "destination_change", unsaid.text
        assert unsaid.json()["error"]["old"]["address"] == old and unsaid.json()["error"]["new"]["address"] == new

        secret = act(api, ana, "PUT", "providers/gateway", {**request, "key": KEY})
        assert secret.status_code == 422 and secret.json()["error"]["code"] == "secret_in_request", secret.text
        assert KEY not in secret.text and proposed.calls == []
        with api.app.state.store.read() as c:
            assert c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0] == 0

        pending = act(api, ana, "PUT", "providers/gateway", request)
        assert pending.status_code == 200 and pending.json()["needs_confirm"], pending.text
        card = pending.json()
        card_in_chat(api, card, "ana")
        # The owner sees the address the key would go to before tapping: scheme, host and port, and that it keeps it.
        assert {"field": "destination", "old": old, "new": new} in card["action"]["diff"], card["action"]
        assert {"field": "credentials", "new": "keep: Gateway key"} in card["action"]["diff"]
        assert "/v1/gateway" not in json.dumps(card["action"]["diff"]) + card["action"]["description"]
        assert proposed.calls == []                         # the saved key has not reached the new address
        assert get(api, "providers/gateway")["url"] == original.url
        assert KEY not in json.dumps(card)

        cancelled = post(api, f"assistant/actions/{card['action']['id']}/cancel", {}, token="ana-test")
        assert cancelled["action"]["status"] == "cancelled"
        assert get(api, "providers/gateway")["url"] == original.url
        assert proposed.calls == []

        # Retrying creates one new card; the exact same URL-only request runs once on the owner's tap.
        pending = act(api, ana, "PUT", "providers/gateway", request)
        card = pending.json()
        assert card["needs_confirm"] and card["action"]["id"] != cancelled["action"]["id"]
        approved = confirm(api, card, "ana-test")
        assert approved["action"]["status"] == "done"
        assert get(api, "providers/gateway")["url"] == proposed.url
        assert proposed.calls == [("/v1/gateway/key", KEY)]
        assert KEY not in json.dumps(approved)
        post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test", expected=409)
        assert proposed.calls == [("/v1/gateway/key", KEY)]

        # A saved key makes every address change a card, even when direct provider changes are enabled.
        put(api, "access/rules", {"botops_direct": True})
        current = get(api, "providers/gateway")
        direct_rule_card = act(api, ana, "PUT", "providers/gateway",
                               {"url": original.url, "expected_revision": current["revision"], "credentials": "keep"})
        assert direct_rule_card.status_code == 200 and direct_rule_card.json()["needs_confirm"], direct_rule_card.text
        assert {"field": "destination", "old": new, "new": old} in direct_rule_card.json()["action"]["diff"]
        assert get(api, "providers/gateway")["url"] == proposed.url
        assert original.calls == []
        assert confirm(api, direct_rule_card.json(), "ana-test")["action"]["status"] == "done"
        assert get(api, "providers/gateway")["url"] == original.url
        assert original.calls == [("/v1/gateway/key", KEY)]

        # A non-owner cannot use the card path even when direct changes are disabled.
        finish(api, botops, ana)
        put(api, "access/rules", {"botops_direct": False})
        ben = turn(api, botops, person="ben-test", text="Change the Gateway address")
        current = get(api, "providers/gateway")
        refused = act(api, ben, "PUT", "providers/gateway",
                      {"url": proposed.url, "expected_revision": current["revision"]})
        assert refused.status_code == 403 and "Only the owner" in refused.json()["error"]["detail"]
        assert get(api, "providers/gateway")["url"] == original.url
        with api.app.state.store.read() as c:            # refused outright: no card in Ben's chat
            assert c.execute("SELECT count(*) FROM assistant_actions WHERE owner='human:ben'").fetchone()[0] == 0
        assert proposed.calls == [("/v1/gateway/key", KEY)]
        with api.app.state.store.read() as c:
            visible = json.dumps([dict(r) for r in c.execute("SELECT body,result FROM assistant_actions")]
                                 + [dict(r) for r in c.execute("SELECT body,refs FROM messages")])
        assert KEY not in visible


def test_gateway_address_change_with_an_environment_key_is_refused_at_once(api, botops, monkeypatch):
    from backend.tests.test_gateway_connection import KEY, fake_gateway, setup
    setup(api, monkeypatch)
    with fake_gateway() as proposed:
        monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
        put(api, "access/rules", {"botops_direct": True})
        ana = turn(api, botops, person="ana-test", text="Change the Gateway address")
        current = get(api, "providers/gateway")
        answer = act(api, ana, "PUT", "providers/gateway", {"url": proposed.url, "expected_revision": current["revision"]})
        # Told at once, not handed a card that fails on the tap; the key never reaches the address.
        assert answer.status_code == 409 and answer.json()["error"]["code"] == "environment", answer.text
        assert KEY not in answer.text and proposed.calls == []
        with api.app.state.store.read() as c:
            assert c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0] == 0


def test_a_computers_code_is_only_on_the_persons_card(api, botops):
    ana = turn(api, botops, person="ana-test", text="Add a computer for me")
    answer = act(api, ana, "POST", "enrollments", {"operator": "ana"})
    assert answer.status_code == 200 and answer.json()["needs_confirm"] and "code" not in answer.json(), answer.text
    card = answer.json()
    card_in_chat(api, card, "ana")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM enrollments WHERE consumed_at IS NULL").fetchone()[0] == 0
    done = confirm(api, card, "ana-test")
    code = done["shown_once"]["code"]
    assert done["action"]["status"] == "done" and len(code) >= 32
    # Nowhere BotOps reads: not the stored card, its result, or the chat.
    with api.app.state.store.read() as c:
        stored = json.dumps([dict(r) for r in c.execute("SELECT * FROM assistant_actions")])
        said = json.dumps([dict(r) for r in c.execute("SELECT body,refs FROM messages")])
    assert code not in stored and code not in said
    again = act(api, ana, "GET", f"assistant/actions/{card['action']['id']}")
    assert code not in again.text
    # The code works once, as Ana's computer.
    joined = api.post("/api/v2/runners/enroll", json={"code": code, "label": "Ana Mac", "platform": "test"},
                      headers={"Idempotency-Key": "k-ana-mac"})
    assert joined.status_code == 200, joined.text


def test_ownership_moves_only_on_the_owners_own_tap(api, botops):
    auth = api.app.state.auth
    revision = get(api, "access")["owner"]["revision"]
    body = {"person": "ben", "expected_revision": revision, "confirm": True}
    # An admin asking BotOps is told at once; no card.
    ben = turn(api, botops, person="ben-test", text="Make me the owner")
    refused = act(api, ben, "POST", "access/owner", body)
    assert refused.status_code == 403 and "owner" in refused.json()["error"]["detail"]
    finish(api, botops, ben)
    ana = turn(api, botops, person="ana-test", text="Hand the team to Ben")
    answer = act(api, ana, "POST", "access/owner", body)
    assert answer.status_code == 200 and answer.json()["needs_confirm"], answer.text
    card_in_chat(api, answer.json(), "ana")
    assert act(api, ana, "POST", f"assistant/actions/{answer.json()['action']['id']}/confirm", {}).status_code == 403
    assert auth.owner_email == "ana@acme.example"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM assistant_actions WHERE owner='human:ben'").fetchone()[0] == 0
    confirm(api, answer.json(), "ana-test")
    assert auth.owner_email == "ben@acme.example"


def test_updating_tico_and_rules_on_botops_own_powers_always_ask(api, botops):
    from backend import team_rules
    ana = turn(api, botops, person="ana-test", text="Update Tico and let BotOps manage bots")
    assert act(api, ana, "POST", "system/update", {"version": "9.9.9"}).json()["needs_confirm"]
    for rule in ("botops_direct", "botops_manages_bots"):
        card = act(api, ana, "PUT", "access/rules", {rule: False})
        assert card.status_code == 200 and card.json()["needs_confirm"], card.text
    # Any other rule runs as the owner at once.
    other = act(api, ana, "PUT", "access/rules", {"admin_sql": False})
    assert other.status_code == 200 and "needs_confirm" not in other.json(), other.text
    with api.app.state.store.read() as c:
        rules = team_rules.load(c)
        assert rules["botops_direct"] and rules["botops_manages_bots"] and not rules["admin_sql"]


def test_botops_lists_a_seats_candidates_but_the_pick_is_the_persons_tap(api, botops):
    from backend.tests.test_recruiting import providers
    providers(api, "anthropic", "openai")
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "coo"})
    ana = turn(api, botops, person="ana-test", text="Hire a support lead")
    found = act(api, ana, "POST", f"seats/{seat['id']}/recruit", {})
    assert found.status_code == 200 and found.json()["candidates"], found.text
    with api.app.state.store.read() as c:
        before = {r["slug"] for r in c.execute("SELECT slug FROM bots")}
    card = act(api, ana, "POST", f"seats/{seat['id']}/hire", {"template": found.json()["candidates"][0]["template"]})
    assert card.status_code == 200 and card.json()["needs_confirm"], card.text
    card_in_chat(api, card.json(), "ana")
    with api.app.state.store.read() as c:
        assert {r["slug"] for r in c.execute("SELECT slug FROM bots")} == before
        assert not c.execute("SELECT filled_by FROM seats WHERE id=?", (seat["id"],)).fetchone()[0]


def test_a_client_that_sends_no_header_gets_the_same_card_or_refusal(api, botops):
    """An older `hub` sends no delegation header: BotOps still acts for the person who asked, so each family of
    routes answers with her card (or the plain refusal), never BotOps' own 403 or an unconfirmed change."""
    local(api)
    row = jira(api)
    seat = post(api, "seats", {"title": "Support lead", "reports_to": "coo"})
    put(api, "usage/limits/ops", {"daily_usd": 10})
    put(api, "access/rules", {"botops_direct": False})
    revision = get(api, "providers")["revision"]
    ana = turn(api, botops, person="ana-test", text="Do these for me")
    cards = (("POST", "access/owner", {"person": "ben", "expected_revision": get(api, "access")["owner"]["revision"],
                                       "confirm": True}),
             ("POST", "enrollments", {"operator": "ana"}),
             ("POST", "system/update", {"version": "9.9.9"}),
             ("PUT", "access/rules", {"botops_manages_bots": False}),
             ("POST", f"seats/{seat['id']}/hire", {"template": "support"}),
             ("PUT", "providers", {"enabled": ["openai"], "expected_revision": revision}),
             ("PUT", "usage/limits/ops", {"daily_usd": 50}),
             ("POST", "access/people/cara", {"role": "admin"}),
             ("POST", f"credentials/{row['id']}/grants", {"subject": "human:cara"}))
    for method, path, body in cards:
        answer = act(api, ana, method, path, body, ref=None)
        assert answer.status_code == 200 and answer.json().get("needs_confirm"), (path, answer.text)
        card_in_chat(api, answer.json(), "ana")
    refused = act(api, ana, "POST", f"credentials/{row['id']}/reveal", {}, ref=None)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "person_only", refused.text
    assert JIRA not in refused.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM enrollments WHERE consumed_at IS NULL").fetchone()[0] == 0
        assert c.execute("SELECT daily_usd FROM usage_limits WHERE bot='ops'").fetchone()[0] == 10
    assert api.app.state.auth.owner_email == "ana@acme.example"
    assert "cara@acme.example" not in api.app.state.auth.bot_admins
