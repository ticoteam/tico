"""Run workflows for one repository of one bot on a person's yes in conversation (backend/run_workflows_yes.py): BotOps
or the person's Assistant reads the change back, the person's plain yes to it is cited, and only that repository's tick
changes, recorded with who said yes and who carried it. With no yes it stays the person's Confirm card; a bot on its own
never gets it.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture). Ops has write on Acme/product and Acme/docs.
Everything is synthetic: the test database, made-up bots and repositories.
"""
import json

import pytest

from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (fixture)
from backend.tests.test_assistant import assistant_turn, say
from backend.tests.test_botops_parity import act
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixture)

ROUTE = "bots/ops/repositories/run-workflows"


@pytest.fixture
def repos(api):
    """Acme/product and Acme/docs ticked in Settings, both write for ops on Chosen repos; Acme/site ticked but not ops'."""
    api.app.state.store.settings.github_owner = "Acme"
    with api.app.state.store.transaction() as c:
        for name in ("Acme/product", "Acme/docs", "Acme/site"):
            c.execute("INSERT INTO repositories(id,full_name,enabled,updated) VALUES(?,?,1,?)", (H.new_id(), name, H.now()))
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0] or "{}")
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'",
                  (json.dumps({**config, "repo_access_mode": "chosen"}),))
        for name in ("Acme/product", "Acme/docs"):
            c.execute("INSERT INTO bot_repo_access VALUES('ops',?,'write')", (name,))


def ticks(api, bot="ops"):
    """The repositories the bot may run workflows in, as Settings shows them."""
    return sorted(r["full_name"] for r in get(api, f"bots/{bot}/repositories", "ana-test")["chosen"] if r.get("run_workflows"))


def history(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM settings_changes WHERE field='run_workflows' ORDER BY created")]


def last_from(api, actor):
    with api.app.state.store.read() as c:
        return c.execute("SELECT id,conversation_id FROM messages WHERE from_actor=? ORDER BY created DESC, rowid DESC "
                         "LIMIT 1", (actor,)).fetchone()


def asked(api, botops, person, repository="Acme/product", on=True, answer="yes"):
    """The person asks BotOps, BotOps reads the change back, the person answers: (the turn after their answer, the
    read-back's id, the answer's id)."""
    first = turn(api, botops, person=person, text=f"Turn {'on' if on else 'off'} Run workflows for ops on {repository}")
    back = act(api, first, "POST", ROUTE + "/read-back", {"repository": repository, "on": on})
    assert back.status_code == 200, back.text
    finish(api, botops, first)
    then = turn(api, botops, person=person, text=answer)
    return then, back.json()["message_id"], last_from(api, "human:" + person.removesuffix("-test"))["id"]


def change(api, attempt, back, yes, repository="Acme/product", on=True, ref="turn"):
    return act(api, attempt, "POST", ROUTE, {"repository": repository, "on": on, "read_back": back, "yes": yes}, ref=ref)


def test_botops_turns_it_on_for_one_repository_on_the_persons_yes_and_history_names_them(api, botops, repos):
    then, back, yes = asked(api, botops, "ana-test")
    with api.app.state.store.read() as c:
        text = c.execute("SELECT body FROM messages WHERE id=?", (back,)).fetchone()["body"]
    assert "for ops on Acme/product" in text and "Turn on" in text
    done = change(api, then, back, yes)
    assert done.status_code == 200, done.text
    reply = done.json()
    # Test 1 and 7: on for that repository, the bot's other repository and its list unchanged.
    assert ticks(api) == ["Acme/product"]
    access = get(api, "bots/ops/repositories", "ana-test")
    assert access["mode"] == "chosen" and sorted(r["full_name"] for r in access["chosen"]) == ["Acme/docs", "Acme/product"]
    # Test 11: what changed, who approved, the next run is the check, no read-back claimed.
    assert reply["changed"] is True and "now on for ops on Acme/product" in reply["reply"]
    assert "approved by" in reply["reply"] and "next RunsOn run" in reply["reply"] and "can't read" in reply["reply"]
    # Test 2 and 12: one entry naming the person, the carrier apart, the bot, the repository, on, the conversation and
    # both messages; Settings history shows it for the bot.
    rows = history(api)
    assert len(rows) == 1
    row, after = rows[0], json.loads(rows[0]["after_json"])
    assert (row["bot"], row["actor"], row["via"]) == ("ops", "human:ana", "botops")
    assert after["repository"] == "Acme/product" and after["on"] is True and after["carrier"] == "botops"
    assert (after["read_back_message"], after["yes_message"]) == (back, yes) and after["conversation_id"]
    shown = [c for c in get(api, "settings/history", "ana-test")["changes"] if c["field"] == "run_workflows"]
    assert shown and shown[0]["bot"] == "ops" and shown[0]["via"] == "botops"


def test_with_no_yes_it_is_the_persons_card_and_a_claimed_yes_counts_for_nothing(api, botops, repos):
    # Test 3 (amended): no yes cited, a card, nothing changed.
    ana = turn(api, botops, person="ana-test", text="Turn on Run workflows for ops on product")
    card = act(api, ana, "POST", ROUTE, {"repository": "product", "on": True})
    assert card.status_code == 200 and card.json()["needs_confirm"] is True, card.text
    assert ticks(api) == [] and history(api) == []
    # The person's own request cited as the yes, before any read-back: not a yes; the card again, with why.
    request = last_from(api, "human:ana")["id"]
    back = act(api, ana, "POST", ROUTE + "/read-back", {"repository": "Acme/product", "on": True}).json()["message_id"]
    claimed = change(api, ana, back, request)
    assert claimed.json()["needs_confirm"] is True and "before the read-back" in claimed.json()["no_yes"]
    # A yes from another conversation (the person's Assistant chat) does not count here.
    say(api, "yes")
    elsewhere = last_from(api, "human:ana")["id"]
    other = change(api, ana, back, elsewhere)
    assert other.json()["needs_confirm"] is True and "another conversation" in other.json()["no_yes"]
    assert ticks(api) == [] and history(api) == []


def test_a_reply_that_is_not_a_plain_yes_changes_nothing(api, botops, repos):
    then, back, said = asked(api, botops, "ana-test", answer="yes but only for docs")
    got = change(api, then, back, said)
    assert got.json()["needs_confirm"] is True and "not a plain yes" in got.json()["no_yes"]
    assert ticks(api) == []


def test_a_yes_answers_one_change_only(api, botops, repos):
    then, back, yes = asked(api, botops, "ana-test")
    # Not for another repository: the read-back named Acme/product.
    got = change(api, then, back, yes, "Acme/docs")
    assert got.json().get("needs_confirm") is True and "not this change" in got.json()["no_yes"], got.text
    assert change(api, then, back, yes).json()["changed"] is True
    # Nor for off: the read-back asked about on.
    got = change(api, then, back, yes, on=False)
    assert got.json().get("needs_confirm") is True and "not this change" in got.json()["no_yes"], got.text
    # Used once: turned off by hand, the same yes can't turn it back on.
    post(api, "bots/ops/repositories/run-workflows", {"repository": "Acme/product", "on": False}, token="ana-test")
    again = change(api, then, back, yes)
    assert again.json().get("needs_confirm") is True and "already used" in again.json()["no_yes"]
    assert ticks(api) == []


def test_a_bot_never_turns_it_on_by_itself(api, botops, repos):
    # Test 4: a bot's own call, with no person behind it, is refused: no card for it to approve itself.
    from backend.tests.test_api import runner
    from backend.tests.test_member_bots import assign, claim, ready
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "messages", {"to": "ops", "text": "Check the build"}, token="ana-test")
    own = claim(api, machine, "ops")
    for path in (ROUTE, ROUTE + "/read-back"):
        got = api.post("/api/v2/" + path, json={"repository": "Acme/product", "on": True}, headers=headers(own["token"]))
        assert got.status_code == 403, got.text
    assert ticks(api) == [] and history(api) == []


def test_a_member_is_refused_as_settings_says(api, botops, repos):
    # Test 5.
    cara = turn(api, botops, person="cara-test", text="Turn on Run workflows for ops on product")
    got = act(api, cara, "POST", ROUTE + "/read-back", {"repository": "Acme/product", "on": True})
    assert got.status_code == 403 and "Only the owner or an admin changes repository access" in got.text
    assert act(api, cara, "POST", ROUTE, {"repository": "Acme/product", "on": True}).status_code == 403
    assert ticks(api) == []


def test_rights_are_read_again_when_the_change_runs(api, botops, repos, monkeypatch):
    # Test 6: Ben was an admin when he said yes and is not when the change runs.
    then, back, yes = asked(api, botops, "ben-test")
    monkeypatch.setattr(api.app.state.auth, "bot_admins", set())
    got = change(api, then, back, yes)
    assert got.status_code == 403 and "Only the owner or an admin" in got.text
    assert ticks(api) == [] and history(api) == []


def test_already_on_changes_nothing_and_off_takes_only_that_repository(api, botops, repos):
    for name in ("Acme/product", "Acme/docs"):
        post(api, ROUTE, {"repository": name, "on": True}, token="ana-test")
    assert ticks(api) == ["Acme/docs", "Acme/product"]
    entries = len(history(api))
    # Test 8: already on, nothing changes, no entry, the reply says so; no question is put to the person.
    ana = turn(api, botops, person="ana-test", text="Turn on Run workflows for ops on product")
    asked_again = act(api, ana, "POST", ROUTE + "/read-back", {"repository": "Acme/product", "on": True}).json()
    assert asked_again["already"] is True and "message_id" not in asked_again
    same = act(api, ana, "POST", ROUTE, {"repository": "Acme/product", "on": True})
    assert same.json()["already"] is True and "already on" in same.json()["reply"]
    assert len(history(api)) == entries
    finish(api, botops, ana)
    # Test 9: off, only that repository comes off, one entry says off.
    then, back, yes = asked(api, botops, "ana-test", on=False)
    assert change(api, then, back, yes, on=False).json()["changed"] is True
    assert ticks(api) == ["Acme/docs"]
    last = json.loads(history(api)[-1]["after_json"])
    assert (last["repository"], last["on"], len(history(api))) == ("Acme/product", False, entries + 1)


def test_refusals_say_why_in_plain_words(api, botops, repos):
    # Test 10, and the bot-not-found and ambiguous cases.
    ana = turn(api, botops, person="ana-test", text="Turn on Run workflows")
    cases = [(ROUTE, "Acme/nowhere", "not connected to Tico"),
             (ROUTE, "Acme/site", "not one of ops's repositories"),
             ("bots/nobody/repositories/run-workflows", "Acme/product", "No bot named nobody")]
    for path, repository, words in cases:
        got = act(api, ana, "POST", path + "/read-back", {"repository": repository, "on": True})
        assert got.status_code in (404, 422) and words in got.text, got.text
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_repo_access VALUES('ops','Other/product','write')")
    got = act(api, ana, "POST", ROUTE + "/read-back", {"repository": "product", "on": True})
    assert got.status_code == 409 and "say which" in got.text
    assert ticks(api) == []


def test_all_ticked_repos_ticks_the_one_repository_and_keeps_the_mode(api, botops, repos):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'",
                  (json.dumps({**config, "repo_access_mode": "all", "repo_all_access": "write"}),))
    then, back, yes = asked(api, botops, "ana-test")
    assert change(api, then, back, yes).json()["changed"] is True
    access = get(api, "bots/ops/repositories", "ana-test")
    assert access["mode"] == "all" and [r["full_name"] for r in access["all_repos"] if r.get("run_workflows")] == [
        "Acme/product"]
    assert sorted(r["full_name"] for r in access["effective"] if r.get("run_workflows")) == ["Acme/product"]


def test_the_assistant_carries_it_the_same_way(api):
    # Test 12: the person's Assistant, in their own chat; the history names it as the carrier.
    api.app.state.store.settings.github_owner = "Acme"
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO repositories(id,full_name,enabled,updated) VALUES(?,?,1,?)", (H.new_id(), "Acme/product",
                                                                                            H.now()))
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0] or "{}")
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps({**config, "repo_access_mode": "chosen"}),))
        c.execute("INSERT INTO bot_repo_access VALUES('ops','Acme/product','write')")
    r, first = assistant_turn(api, "ana-test", "Turn on Run workflows for ops on product")
    call = lambda attempt, path, body: api.post("/api/v2/" + path, json=body, headers=headers(attempt["token"]))
    back = call(first, ROUTE + "/read-back", {"repository": "product", "on": True})
    assert back.status_code == 200, back.text
    # No yes yet: a card in the person's chat, nothing changed.
    assert call(first, ROUTE, {"repository": "product", "on": True}).json()["needs_confirm"] is True
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t"}, r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, r["token"])
    say(api, "Yes please")
    yes = last_from(api, "human:ana")["id"]
    then = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    done = call(then, ROUTE, {"repository": "product", "on": True, "read_back": back.json()["message_id"], "yes": yes})
    assert done.status_code == 200 and done.json()["changed"] is True, done.text
    row = history(api)[-1]
    assert (row["actor"], row["via"], json.loads(row["after_json"])["carrier"]) == ("human:ana", "assistant", "assistant")


def test_a_word_from_the_person_between_the_read_back_and_the_yes_voids_it(api, botops, repos):
    first = turn(api, botops, person="ana-test", text="Turn on Run workflows for ops on product")
    back = act(api, first, "POST", ROUTE + "/read-back", {"repository": "Acme/product", "on": True}).json()["message_id"]
    finish(api, botops, first)
    post(api, "chat/botops", {"text": "Hmm, which bot was that?"}, token="ana-test")
    then = turn(api, botops, person="ana-test", text="yes")
    got = change(api, then, back, last_from(api, "human:ana")["id"])
    assert got.json().get("needs_confirm") is True and "not a plain yes" in got.json()["no_yes"], got.text
    assert ticks(api) == []


def test_a_word_from_the_person_after_the_yes_takes_it_back(api, botops, repos):
    then, back, yes = asked(api, botops, "ana-test")
    post(api, "chat/botops", {"text": "wait, no"}, token="ana-test")
    got = change(api, then, back, yes)
    assert got.json().get("needs_confirm") is True and "not a plain yes" in got.json()["no_yes"], got.text
    assert ticks(api) == []


def test_a_second_read_back_before_the_yes_leaves_the_first_unanswered(api, botops, repos):
    first = turn(api, botops, person="ana-test", text="Turn on Run workflows for ops on product and docs")
    back = act(api, first, "POST", ROUTE + "/read-back", {"repository": "Acme/product", "on": True}).json()["message_id"]
    assert act(api, first, "POST", ROUTE + "/read-back", {"repository": "Acme/docs", "on": True}).status_code == 200
    finish(api, botops, first)
    then = turn(api, botops, person="ana-test", text="yes")
    got = change(api, then, back, last_from(api, "human:ana")["id"])
    assert got.json().get("needs_confirm") is True and "not a plain yes" in got.json()["no_yes"], got.text
    assert ticks(api) == []


def test_the_settings_plan_is_the_card_without_a_yes_and_the_change_with_one(api, botops, repos):
    plan = "settings/catalog/bot.run_workflows/plan"
    then, back, yes = asked(api, botops, "ana-test")
    card = act(api, then, "POST", plan, {"value": {"repository": "Acme/product", "on": True}, "bot": "ops"})
    assert card.status_code == 200 and card.json().get("needs_confirm") is True and "run" not in card.json(), card.text
    assert ticks(api) == []
    run = act(api, then, "POST", plan, {"value": {"repository": "Acme/product", "on": True, "read_back": back,
                                                  "yes": yes}, "bot": "ops"}).json()["run"]
    assert act(api, then, run["method"], run["path"], run["body"]).json()["changed"] is True
    assert ticks(api) == ["Acme/product"]


def test_the_assistants_settings_plan_is_the_card_without_a_yes(api, repos):
    _, attempt = assistant_turn(api, "ana-test", "Turn on Run workflows for ops on product")
    got = api.post("/api/v2/settings/catalog/bot.run_workflows/plan", headers=headers(attempt["token"]),
                   json={"value": {"repository": "Acme/product", "on": True}, "bot": "ops"})
    assert got.status_code == 200 and got.json().get("needs_confirm") is True and "run" not in got.json(), got.text
    assert ticks(api) == []


def test_the_assistants_yes_follows_the_owners_rule_assistant_acts_without_asking(api, repos):
    # Engineering, Oct 28: like approving on the person's words, the Assistant's yes path runs only while "Assistant acts
    # without asking" is on; with it off the change is the person's Confirm card, even with a yes cited.
    r, first = assistant_turn(api, "ana-test", "Turn on Run workflows for ops on product")
    call = lambda attempt, path, body: api.post("/api/v2/" + path, json=body, headers=headers(attempt["token"]))
    back = call(first, ROUTE + "/read-back", {"repository": "product", "on": True}).json()
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t"}, r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, r["token"])
    say(api, "Yes")
    yes = last_from(api, "human:ana")["id"]
    then = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    cited = {"repository": "product", "on": True, "read_back": back["message_id"], "yes": yes}
    assert api.put("/api/v2/access/rules", json={"assistant_direct": False}, headers=headers()).status_code == 200
    got = call(then, ROUTE, cited).json()
    assert got.get("needs_confirm") is True and "Assistant acts without asking" in got["no_yes"], got
    planned = api.post("/api/v2/settings/catalog/bot.run_workflows/plan", headers=headers(then["token"]),
                       json={"value": {"repository": "Acme/product", "on": True, "read_back": back["message_id"],
                                       "yes": yes}, "bot": "ops"}).json()
    assert planned.get("needs_confirm") is True and "run" not in planned, planned
    again = call(then, ROUTE + "/read-back", {"repository": "product", "on": True}).json()
    assert again["card_instead"] is True and "message_id" not in again, again
    assert ticks(api) == [] and history(api) == []
    # With the rule back on, the same yes carries it.
    assert api.put("/api/v2/access/rules", json={"assistant_direct": True}, headers=headers()).status_code == 200
    done = call(then, ROUTE, cited)
    assert done.status_code == 200 and done.json()["changed"] is True, done.text
