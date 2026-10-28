"""Ask someone who can (settings stage 3, backend/settings_asks.py): a person who may not change a setting says yes to
"Shall I ask an admin?", and one person who may gets one request with the setting's Confirm card; their tap or cancel
settles it, and the asker is told.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). Everything here is synthetic: the test
database, made-up bots, and no request leaves the test server.
"""
import json

from backend.store import H
from backend.tests.test_api import api, get, post, runner  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_mcp import call as mcp
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)


def asks(api, person):
    """The requests filed for `person` by an ask: (task, the card it carries)."""
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT * FROM assistant_actions WHERE owner=? AND ask_json IS NOT NULL",
                         ("human:" + person,)).fetchall()
        return [(H.task(c, json.loads(r["ask_json"])["task"]), dict(r)) for r in rows]


def times(api):
    return get(api, "release-schedule")["times"]


def told(api, person, action_id):
    """What the asker was told about this ask's card, in the chat they asked from."""
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT body, refs_json FROM messages WHERE to_actor=?", ("human:" + person,)).fetchall()
        return [r["body"] for r in rows if json.loads(r["refs_json"] or "{}").get("settings_ask") == action_id]


_SAID = iter(range(1000))


def ask(api, botops, person, key, value, **more):
    """`person` asks BotOps for a setting they may not change, then says yes to asking someone who can (each yes its
    own words: the same message twice starts no second run)."""
    attempt = turn(api, botops, person=person + "-test", text=f"Yes, ask an admin ({next(_SAID)})")
    err, got = mcp(api, "hub_settings_ask", {"key": key, "value": value, **more}, attempt["token"])
    finish(api, botops, attempt)
    return err, got


def test_a_members_yes_to_asking_an_admin_gives_an_admin_one_request_with_a_card_and_tells_the_member(api, botops):
    """Acceptance 14. Cara may not change Release times (an admin's). BotOps refuses her, naming who may; on her yes
    Ben, the admin, gets one request: the card in his BotOps chat and a task he owns that Cara requested. Nothing
    changes until his tap, which runs as him; the task is then done, and Cara is told in her chat with BotOps."""
    before = times(api)
    cara = turn(api, botops, person="cara-test", text="Release at 09:30")
    err, refused = mcp(api, "hub_settings_set", {"key": "release.times", "value": "09:30"}, cara["token"])
    finish(api, botops, cara)
    assert err and refused["error"] == "forbidden", refused
    assert "Only the owner or an admin changes" in refused["detail"], refused
    err, got = ask(api, botops, "cara", "release.times", "09:30", note="Customers are up early")
    assert not err and got["asked"] == {"person": "human:ben", "name": got["asked"]["name"]}, got
    assert "Nothing has changed yet" in got["detail"], got
    [(task, card)] = asks(api, "ben")
    assert task["owner"] == "human:ben" and task["requester"] == "human:cara" and task["status"] == "open", task
    assert "Customers are up early" in task["body"] and card["status"] == "pending", (task, card)
    assert card["path"] == "/api/v2/release-schedule" and times(api) == before  # nothing until Ben's tap
    with api.app.state.store.read() as c:
        room = c.execute("SELECT * FROM conversations WHERE id=?", (card["conversation_id"],)).fetchone()
    assert "human:ben" in room["participants_json"] and "bot:botops" in room["participants_json"]
    assert not asks(api, "ana") and not asks(api, "cara")                      # one request, to one person
    tap = post(api, f"assistant/actions/{card['id']}/confirm", {}, "ben-test")["action"]
    assert tap["status"] == "done", tap
    assert times(api) == ["09:30"]
    [(task, card)] = asks(api, "ben")
    assert task["status"] == "done", task
    [said] = told(api, "cara", card["id"])
    assert "confirmed it" in said and "Release times" in said, said


def test_cancelling_an_asked_card_declines_the_request_and_tells_the_asker(api, botops):
    before = times(api)
    err, got = ask(api, botops, "cara", "release.times", "10:15")
    assert not err, got
    [(_, card)] = asks(api, "ben")
    post(api, f"assistant/actions/{card['id']}/cancel", {}, "ben-test")
    [(task, _)] = asks(api, "ben")
    assert task["status"] == "declined" and times(api) == before, task
    [said] = told(api, "cara", card["id"])
    assert "said no" in said and "nothing changed" in said, said


def test_the_owners_setting_is_asked_of_the_owner_and_an_admin_asks_too(api, botops):
    """Go-ahead days is the owner's alone: Ben, an admin who may not change it, asks, and the request goes to Ana."""
    err, got = ask(api, botops, "ben", "team.rules.go_ahead_days", 3)
    assert not err and got["asked"]["person"] == "human:ana", got
    [(task, card)] = asks(api, "ana")
    assert task["requester"] == "human:ben" and card["path"] == "/api/v2/access/rules", (task, card)


def test_the_same_ask_again_is_the_same_request(api, botops):
    first = ask(api, botops, "cara", "release.times", "11:00")[1]
    again = ask(api, botops, "cara", "release.times", "11:00")[1]
    assert first["task"] == again["task"] and first["action"]["id"] == again["action"]["id"]
    assert len(asks(api, "ben")) == 1


def test_no_ask_for_a_setting_the_asker_may_change_or_one_only_a_person_changes(api, botops):
    err, got = ask(api, botops, "ben", "release.times", "09:30")
    assert err and got["error"] == "may", got                                  # Ben may: he sets it
    err, got = ask(api, botops, "cara", "hooks.wake_url", "https://wake.acme.example/x")
    assert err and got["error"] == "person_only" and "the owner changes it in Settings" in got["detail"], got
    err, got = ask(api, botops, "cara", "release.times", "09:30", to="cara")
    assert err and got["error"] == "no_one", got                               # never the asker
    err, got = ask(api, botops, "cara", "me.tokens", "{}")                      # each person's own: no one to ask
    assert err and got["error"] == "person_only" and "for themselves" in got["detail"], got
    err, got = ask(api, botops, "cara", "webhooks.outgoing.create", "{}")      # hers to make, in Settings
    assert err and got["error"] == "person_only" and "you change it yourself" in got["detail"], got
    assert not asks(api, "ben") and not asks(api, "ana")


def test_an_asked_card_nobody_taps_expires_within_the_hour_after_a_day_and_tells_the_asker(api, botops):
    """Review 1 (delta): the hourly sweep expires an asked card left a day, as a late tap would find it, so the request
    is declined, the asker is told and the ask no longer holds one of her five. A newer card is left alone."""
    from backend import approval_expiry
    before = times(api)
    ask(api, botops, "cara", "release.times", "07:45")
    ask(api, botops, "cara", "release.times", "08:15")
    old, new = sorted((card for _, card in asks(api, "ben")), key=lambda card: card["body_json"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE assistant_actions SET created=? WHERE id=?", (H.shift(H.now(), hours=-25), old["id"]))
        assert approval_expiry.sweep(c)["cards"] == [old["id"]]
        assert approval_expiry.sweep(c)["cards"] == []                           # once
    status = {card["id"]: (card["status"], task["status"]) for task, card in asks(api, "ben")}
    assert status == {old["id"]: ("expired", "declined"), new["id"]: ("pending", "open")}, status
    [said] = told(api, "cara", old["id"])
    assert "expired" in said and "nothing changed" in said, said
    assert not told(api, "cara", new["id"]) and times(api) == before
    with api.app.state.store.read() as c:
        waiting = c.execute("SELECT count(*) FROM assistant_actions WHERE status='pending' AND "
                            "json_extract(ask_json, '$.asker')='human:cara'").fetchone()[0]
    assert waiting == 1


def test_a_bot_that_made_an_incoming_trigger_may_change_it_as_its_route_allows(api):
    """Review 1 (delta) question: triggers.manages lets whoever made a trigger change it, a bot too; the catalogue
    agrees, and a bot that didn't make it, or one that made an outgoing webhook, still may not."""
    from types import SimpleNamespace
    from backend import settings_catalog as K
    trigger = post(api, "triggers", {"name": "Made by a bot"})["trigger"]
    maker, other = (SimpleNamespace(role="bot", actor=a, support="", email="") for a in ("bot:maker", "bot:other"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE triggers SET created_by='bot:maker' WHERE id=?", (trigger["id"],))
        auth, incoming = api.app.state.auth, K.entry("webhooks.incoming")
        assert K.may(c, auth, maker, incoming, {"id": trigger["id"]})
        assert not K.may(c, auth, other, incoming, {"id": trigger["id"]})
        assert not K.may(c, auth, maker, K.entry("webhooks.outgoing"), {"id": trigger["id"]})


def test_a_bot_on_its_own_rights_cannot_ask_for_anyone(api, botops):
    attempt = turn(api, botops, person="cara-test", text="Ask an admin")
    refused = act(api, attempt, "POST", "settings/catalog/release.times/ask", {"value": "09:30"}, ref=None)
    finish(api, botops, attempt)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden", refused.text
    assert not asks(api, "ben")


def test_a_members_raw_refused_write_names_who_may_as_hub_settings_does(api, botops):
    """Build item 9. Through BotOps, a member's raw write to a setting that isn't theirs is refused in the catalogue's
    words, as `hub settings set` gives them: a computer she doesn't operate gets no card her tap would fail ("a card,
    then refused"), and a trigger Ana made, whose route said only who may change it in its own words, names it too."""
    machine = runner(api)
    trigger = post(api, "triggers", {"name": "Ana's trigger"})["trigger"]
    cara = turn(api, botops, person="cara-test", text="Revoke that computer and rename that trigger")
    revoke = act(api, cara, "POST", f"computers/{machine['runner_id']}/revoke", {})
    rename = act(api, cara, "POST", f"triggers/{trigger['id']}", {"name": "Cara's now"})
    finish(api, botops, cara)
    for refused, name, who in ((revoke, "Revoke a computer", "The computer's operator, an admin or the owner"),
                               (rename, "Incoming trigger", "The person who made it, an admin or the owner")):
        assert refused.status_code == 403, refused.text
        error = refused.json()["error"]
        assert error["who_may"] == who and error["fix"].startswith("Only " + who[0].lower() + who[1:]), error
        assert name in error["fix"], error
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM assistant_actions WHERE owner='human:cara'").fetchone()
        assert c.execute("SELECT revoked_at FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()[0] is None


def test_the_assistant_asks_an_admin_on_the_members_cited_yes_and_never_without_it(api, botops):
    """The same from Cara's Assistant: with her words cited, Ben gets the request and she is told in her Assistant
    chat; without them, nothing is asked. The raw refusal of her release time names who may, as `hub settings` does."""
    from backend.tests.test_api import as_member, headers
    from backend.tests.test_assistant import assistant_turn
    from backend.tests.test_settings_assistant import mcp as assistant_mcp
    as_member(api, "cara@acme.example")
    r, attempt = assistant_turn(api, "cara-test", "Yes, ask an admin to set the release to 09:30")
    token = attempt["token"]
    err, got = assistant_mcp(api, "hub_settings_ask", {"key": "release.times", "value": "09:30"}, token)
    assert err and got["error"] == "on_behalf_of", got                         # not cited: nothing asked
    assert not asks(api, "ben")
    err, got = assistant_mcp(api, "hub_settings_ask", {"key": "release.times", "value": "09:30",
                                                       "on_behalf_of": "turn"}, token)
    assert not err and got["asked"]["person"] == "human:ben", got
    [(task, card)] = asks(api, "ben")
    assert task["requester"] == "human:cara" and card["path"] == "/api/v2/release-schedule", (task, card)
    post(api, f"assistant/actions/{card['id']}/confirm", {}, "ben-test")
    assert times(api) == ["09:30"]
    [said] = told(api, "cara", card["id"])
    with api.app.state.store.read() as c:
        room = c.execute("SELECT m.conversation_id, v.room_key FROM messages m JOIN conversations v ON "
                         "v.id=m.conversation_id WHERE m.body=?", (said,)).fetchone()
    assert room["room_key"] == "assistant", dict(room)
    raw = api.patch("/api/v2/release-schedule", json={"times": ["10:00"]},
                    headers={**headers(token), "X-Tico-On-Behalf-Of": "turn"})
    assert raw.status_code == 403 and raw.json()["error"]["who_may"] == "The owner or an admin", raw.text
