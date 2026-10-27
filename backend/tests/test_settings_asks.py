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


def told(api, person, action_id):
    """What the asker was told about this ask's card, in the chat they asked from."""
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT body, refs_json FROM messages WHERE to_actor=?", ("human:" + person,)).fetchall()
        return [r["body"] for r in rows if json.loads(r["refs_json"] or "{}").get("settings_ask") == action_id]


def ask(api, botops, person, key, value, **more):
    """`person` asks BotOps for a setting they may not change, then says yes to asking someone who can."""
    attempt = turn(api, botops, person=person + "-test", text="Yes, ask an admin")
    err, got = mcp(api, "hub_settings_ask", {"key": key, "value": value, **more}, attempt["token"])
    finish(api, botops, attempt)
    return err, got


def test_a_members_yes_to_asking_an_admin_gives_an_admin_one_request_with_a_card_and_tells_the_member(api, botops):
    """Acceptance 14. Cara may not change Sleep when idle (an admin's). BotOps refuses her, naming who may; on her yes
    Ben, the admin, gets one request: the card in his BotOps chat and a task he owns that Cara requested. Nothing
    changes until his tap, which runs as him; the task is then done, and Cara is told in her chat with BotOps."""
    before = get(api, "sleep")["hours"]
    cara = turn(api, botops, person="cara-test", text="Sleep after 3 quiet hours")
    err, refused = mcp(api, "hub_settings_set", {"key": "sleep.hours", "value": 3}, cara["token"])
    finish(api, botops, cara)
    assert err and refused["error"] == "forbidden" and refused["who_may"] == "The owner or an admin", refused
    err, got = ask(api, botops, "cara", "sleep.hours", 3, note="Nights are quiet")
    assert not err and got["asked"] == {"person": "human:ben", "name": got["asked"]["name"]}, got
    assert "Nothing has changed yet" in got["detail"], got
    [(task, card)] = asks(api, "ben")
    assert task["owner"] == "human:ben" and task["requester"] == "human:cara" and task["status"] == "open", task
    assert "Nights are quiet" in task["body"] and card["status"] == "pending" and card["path"] == "/api/v2/sleep"
    assert get(api, "sleep")["hours"] == before                                # nothing until Ben's tap
    with api.app.state.store.read() as c:
        room = c.execute("SELECT * FROM conversations WHERE id=?", (card["conversation_id"],)).fetchone()
    assert "human:ben" in room["participants_json"] and "bot:botops" in room["participants_json"]
    assert not asks(api, "ana") and not asks(api, "cara")                      # one request, to one person
    tap = post(api, f"assistant/actions/{card['id']}/confirm", {}, "ben-test")["action"]
    assert tap["status"] == "done", tap
    assert get(api, "sleep")["hours"] == 3
    [(task, card)] = asks(api, "ben")
    assert task["status"] == "done", task
    [said] = told(api, "cara", card["id"])
    assert "confirmed it" in said and "Sleep when idle" in said, said


def test_cancelling_an_asked_card_declines_the_request_and_tells_the_asker(api, botops):
    before = get(api, "sleep")["hours"]
    err, got = ask(api, botops, "cara", "sleep.hours", 5)
    assert not err, got
    [(_, card)] = asks(api, "ben")
    post(api, f"assistant/actions/{card['id']}/cancel", {}, "ben-test")
    [(task, _)] = asks(api, "ben")
    assert task["status"] == "declined" and get(api, "sleep")["hours"] == before, task
    [said] = told(api, "cara", card["id"])
    assert "said no" in said and "nothing changed" in said, said


def test_the_owners_setting_is_asked_of_the_owner_and_an_admin_asks_too(api, botops):
    """Go-ahead days is the owner's alone: Ben, an admin who may not change it, asks, and the request goes to Ana."""
    err, got = ask(api, botops, "ben", "team.rules.go_ahead_days", 3)
    assert not err and got["asked"]["person"] == "human:ana", got
    [(task, card)] = asks(api, "ana")
    assert task["requester"] == "human:ben" and card["path"] == "/api/v2/access/rules", (task, card)


def test_the_same_ask_again_is_the_same_request(api, botops):
    first = ask(api, botops, "cara", "sleep.hours", 4)[1]
    again = ask(api, botops, "cara", "sleep.hours", 4)[1]
    assert first["task"] == again["task"] and first["action"]["id"] == again["action"]["id"]
    assert len(asks(api, "ben")) == 1


def test_no_ask_for_a_setting_the_asker_may_change_or_one_only_a_person_changes(api, botops):
    err, got = ask(api, botops, "ben", "sleep.hours", 3)
    assert err and got["error"] == "may", got                                  # Ben may: he sets it
    err, got = ask(api, botops, "cara", "hooks.wake_url", "https://wake.acme.example/x")
    assert err and got["error"] == "person_only" and got["who_may"] == "The owner", got
    err, got = ask(api, botops, "cara", "sleep.hours", 3, to="cara")
    assert err and got["error"] == "no_one", got                               # never the asker
    assert not asks(api, "ben") and not asks(api, "ana")


def test_a_bot_on_its_own_rights_cannot_ask_for_anyone(api, botops):
    err, got = mcp(api, "hub_settings_ask", {"key": "sleep.hours", "value": 3}, "finance-test")
    assert err and got["error"] == "forbidden", got
    assert not asks(api, "ben")


def test_a_members_raw_refused_write_names_who_may_as_hub_settings_does(api, botops):
    """Build item 9: the route's own refusal of Cara's revoke said only "You cannot revoke this runner"; through
    BotOps it now carries the catalogue's who-may line, as `hub settings set` gives it."""
    machine = runner(api)
    cara = turn(api, botops, person="cara-test", text="Revoke that computer")
    refused = act(api, cara, "POST", f"computers/{machine['runner_id']}/revoke", {})
    finish(api, botops, cara)
    assert refused.status_code == 403, refused.text
    error = refused.json()["error"]
    assert error["who_may"] == "The computer's operator, an admin or the owner", error
    assert error["fix"].startswith("Only the computer's operator, an admin or the owner changes Revoke a computer")
