"""BotOps acting for a person and repository access: it reads its own repositories as itself, it may give another bot
access with the person's rights, and taking any away is a Confirm card for the person, never done at once."""
import json
import re

import pytest

from backend.tests.test_api import api, get, headers, post, put  # noqa: F401  (fixture)
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.store import H


@pytest.fixture
def repos(api):
    """Acme/product and Acme/docs ticked in Settings; ops has write on product, BotOps read on docs."""
    api.app.state.store.settings.github_owner = "Acme"
    with api.app.state.store.transaction() as c:
        for name in ("Acme/product", "Acme/docs"):
            c.execute("INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?)", (H.new_id(), name, H.now()))
        for bot, name, level in (("ops", "Acme/product", "write"), ("botops", "Acme/docs", "read")):
            config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0] or "{}")
            c.execute("UPDATE bot_config SET config=? WHERE bot=?",
                      (json.dumps({**config, "repo_access_mode": "chosen"}), bot))
            c.execute("INSERT INTO bot_repo_access VALUES(?,?,?)", (bot, name, level))


def effective(api, bot="ops"):
    return get(api, f"bots/{bot}/repositories", "ana-test")["effective"]


def test_botops_acting_for_a_person_reads_its_own_repositories(api, botops, repos):
    ana = turn(api, botops, person="ana-test", text="Which repositories can you read?")
    mine = act(api, ana, "GET", "runners/me/repositories")
    assert mine.status_code == 200, mine.text
    assert [(r["full_name"], r["access"]) for r in mine.json()["repositories"]] == [("Acme/docs", "read")]


def test_botops_acting_for_a_person_cannot_take_another_bots_access_away(api, botops, repos):
    before = effective(api)
    assert before == [{"full_name": "Acme/product", "access": "write"}]
    ana = turn(api, botops, person="ana-test", text="Tidy ops' repositories")
    # Three tries (as on Oct 15): nothing changes, and Ana gets one card naming what would stop.
    cards = [act(api, ana, "PUT", "bots/ops/repositories", {"mode": "own"}) for _ in range(3)]
    assert all(r.status_code == 200 for r in cards), cards[0].text
    card = cards[0].json()
    assert card["needs_confirm"] is True and "nothing has changed" in card["detail"]
    assert cards[2].json()["repeated"] is True
    assert effective(api) == before
    with api.app.state.store.read() as c:
        row = c.execute("SELECT owner,method,path,summary,description,body FROM assistant_actions WHERE id=?",
                        (card["action"]["id"],)).fetchone()
        assert (row["owner"], row["method"], row["path"]) == ("human:ana", "PUT", "/api/v2/bots/ops/repositories")
        # Plain words on the card: who, and what it loses; no method, path or mode word ("own", "chosen").
        assert row["description"] == row["summary"] == "ops: loses access to product."
        assert json.loads(row["body"]) == {"mode": "own"}
        assert not c.execute("SELECT 1 FROM events WHERE action='bot.repos_changed' AND target='ops'").fetchone()
    # Write turned to read is taken away too.
    lowered = act(api, ana, "PUT", "bots/ops/repositories",
                  {"mode": "chosen", "chosen": [{"full_name": "Acme/product", "access": "read"}]})
    assert lowered.json()["needs_confirm"] is True and effective(api) == before
    assert lowered.json()["action"]["description"] == "ops: read-only on product (was write)."
    # Giving access goes through at once, with her rights.
    added = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [
        {"full_name": "Acme/product", "access": "write"}, {"full_name": "Acme/docs", "access": "read"}]})
    assert added.status_code == 200 and added.json()["stopped"] == []
    assert {r["full_name"] for r in effective(api)} == {"Acme/product", "Acme/docs"}
    # Her own click runs it, as her.
    done = post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test")
    assert done["action"]["status"] == "done", done
    assert effective(api) == []


def test_a_person_in_settings_still_narrows_and_is_told_what_stops(api, botops, repos):
    changed = put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    assert changed["stopped"] == [{"full_name": "Acme/product", "access": "write"}]
    assert changed["effective"] == [] and changed["chosen"] == [{"full_name": "Acme/product", "access": "write"}]
    with api.app.state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM events WHERE action='bot.repos_changed' AND target='ops'"
                                      ).fetchone()[0])
    assert detail["stopped"] == [{"full_name": "Acme/product", "access": "write"}]
    back = put(api, "bots/ops/repositories", {"mode": "chosen"}, token="ana-test")
    assert back["stopped"] == [] and back["effective"] == [{"full_name": "Acme/product", "access": "write"}]


def test_the_cli_says_what_a_mode_change_took_away(monkeypatch, capsys):
    from argparse import Namespace
    from clients import hubtools, remotecli
    monkeypatch.setenv("HUB_API_URL", "https://hub.example")
    monkeypatch.setenv("HUB_TOKEN", "test")
    monkeypatch.setattr(hubtools, "bot_repos_set", lambda client, body: {
        "mode": body["mode"], "stopped": [{"full_name": "Acme/product", "access": "write"}], "creation_stopped": True})
    args = Namespace(cmd="bot", fn="bot repos", bot="ops", all=False, own=True, chosen=None)
    assert remotecli.run(args)["mode"] == "own"
    assert capsys.readouterr().err.strip() == "ops no longer has: Acme/product (write), repository creation"
    # Clearing only a workflow option is a loss too.
    monkeypatch.setattr(hubtools, "bot_repos_set", lambda client, body: {
        "mode": "chosen", "stopped": [], "workflow_edits_stopped": ["Acme/product"]})
    remotecli.run(Namespace(cmd="bot", fn="bot repos", bot="ops", all=False, own=False, chosen=["Acme/product:write-edit"]))
    assert capsys.readouterr().err.strip() == "ops no longer has: Acme/product (edit workflow files)"


def test_the_cli_grant_suffix_sets_each_workflow_option_or_leaves_it():
    from clients.remotecli import chosen_grant
    from clients.tico import APIError
    assert chosen_grant("Acme/product:write") == {"full_name": "Acme/product", "access": "write"}
    assert chosen_grant("Acme/product:write+actions+edit") == {
        "full_name": "Acme/product", "access": "write", "run_workflows": True, "edit_workflows": True}
    assert chosen_grant("Acme/product:write+edit-actions") == {
        "full_name": "Acme/product", "access": "write", "edit_workflows": True, "run_workflows": False}
    for wrong in ("Acme/product:read+edit", "Acme/product:write+edit+edit", "Acme/product:write+deploy"):
        with pytest.raises(APIError):
            chosen_grant(wrong)


FLAG = {"full_name": "Acme/product", "access": "write", "run_workflows": True}
# Each workflow option BotOps may only put on a Confirm card, and what the card says it allows.
OPTIONS = [("run_workflows", "may run workflows in product"), ("edit_workflows", "may edit workflow files in product")]


def _stored_card(api, card):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT owner,method,path,description,body,status FROM assistant_actions WHERE id=?",
                              (card["action"]["id"],)).fetchone())


CARDS = [("run_workflows", "may run workflows in product: it can start CI there, and CI uses that repository's secrets"),
         ("edit_workflows", "may edit workflow files in product: a workflow it pushes on a branch runs with that "
                            "repository's secrets before review")]


@pytest.mark.parametrize("option,words", CARDS)
@pytest.mark.parametrize("person,name", [("ana-test", "ana"), ("ben-test", "ben")])
def test_an_owner_or_admin_asking_botops_to_tick_run_workflows_gets_a_confirm_card(api, botops, repos, person, name,
                                                                                   option, words):
    """Chris, Oct 27: BotOps refused to tick Run workflows for him. The owner or an admin gets a Confirm card in their
    BotOps chat naming the bot, the repository and what running workflows allows; their tap turns it on."""
    FLAG = {"full_name": "Acme/product", "access": "write", option: True}
    asked = turn(api, botops, person=person, text="Tick " + option + " on product for ops")
    for _ in range(2):
        card = act(api, asked, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [FLAG]})
        assert card.status_code == 200 and card.json()["needs_confirm"] is True, card.text
    assert effective(api) == [{"full_name": "Acme/product", "access": "write"}]
    row = _stored_card(api, card.json())
    assert (row["owner_id"], row["method"], row["path"]) == ("human:" + name, "PUT", "/api/v2/bots/ops/repositories")
    assert row["description"] == "ops: " + words + ".", row
    done = post(api, f"assistant/actions/{card.json()['action']['id']}/confirm", {}, token=person)
    assert done["action"]["status"] == "done", done
    assert effective(api) == [FLAG]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT actor FROM events WHERE action='bot.repos_changed' AND target='ops'").fetchone()[0] == \
            "human:" + name


@pytest.mark.parametrize("option,words", CARDS)
def test_a_tick_under_all_ticked_repos_from_botops_is_a_confirm_card(api, botops, repos, option, words):
    """Dev 641: a repository's own tick under All ticked repos is the person's own click too, both ways, and the
    catalogue cards it from the body."""
    from backend.tests.test_mcp import call as mcp
    put(api, "bots/ops/repositories", {"mode": "all"}, token="ana-test")
    before = effective(api)
    ana = turn(api, botops, person="ana-test", text="Tick " + option + " on product for ops")
    body = {"mode": "all", "all_repos": [{"full_name": "Acme/product", option: True}]}
    card = act(api, ana, "PUT", "bots/ops/repositories", body)
    assert card.status_code == 200 and card.json()["needs_confirm"] is True, card.text
    assert effective(api) == before
    assert _stored_card(api, card.json())["description"] == "ops: " + words + "."
    err, shown = mcp(api, "hub_settings_set", {"key": "bot.repositories", "bot": "ops", "value": body}, ana["token"])
    assert not err and shown["needs_confirm"] is True, shown
    post(api, f"assistant/actions/{card.json()['action']['id']}/confirm", {}, token="ana-test")
    assert {"full_name": "Acme/product", "access": "write", option: True} in effective(api)
    cleared = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "all", "all_repos": [
        {"full_name": "Acme/product", option: False}]})
    assert cleared.json()["needs_confirm"] is True
    assert {"full_name": "Acme/product", "access": "write", option: True} in effective(api)
    # Nor can it keep a tick for later while the bot is on Chosen repos.
    put(api, "bots/ops/repositories", {"mode": "chosen"}, token="ana-test")
    later = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "all_repos": [
        {"full_name": "Acme/docs", option: True}]})
    assert later.json()["needs_confirm"] is True, later.text
    assert _stored_card(api, later.json())["description"] == \
        "ops: " + words.replace(" in product:", " in docs once All ticked repos is on with Write:") + ".", later.text
    # The kept tick coming back with All ticked repos is a card too, though the body holds no tick.
    back = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "all"})
    assert back.json()["needs_confirm"] is True, back.text
    assert {"full_name": "Acme/product", "access": "write", option: True} not in effective(api)
    assert get(api, "bots/ops/repositories", "ana-test")["all_repos"] == [{"full_name": "Acme/product", option: True}]
    # Changing its own repositories in the person's turn, BotOps never ticks one for itself, not even for later.
    itself = act(api, ana, "PUT", "bots/botops/repositories", {"mode": "chosen", "all_repos": [
        {"full_name": "Acme/docs", option: True}]})
    assert itself.status_code == 403 and "Only an Owner or admin lets a bot" in itself.text, itself.text
    assert get(api, "bots/botops/repositories", "ana-test")["all_repos"] == []
    # Nor does it bring back a tick the person kept for it: switching itself to All ticked repos is refused.
    put(api, "bots/botops/repositories", {"mode": "chosen", "all_access": "write", "all_repos": [
        {"full_name": "Acme/docs", option: True}]}, token="ana-test")
    switched = act(api, ana, "PUT", "bots/botops/repositories", {"mode": "all"})
    assert switched.status_code == 403 and "Only an Owner or admin lets a bot" in switched.text, switched.text
    assert get(api, "bots/botops/repositories", "ana-test")["mode"] == "chosen"


@pytest.mark.parametrize("option,words", CARDS)
def test_a_tick_kept_on_chosen_repos_while_the_bot_is_on_own_repos_says_when_it_starts(api, botops, repos, option,
                                                                                        words):
    put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    ana = turn(api, botops, person="ana-test", text="Tick " + option + " on product for ops")
    card = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "own", "chosen": [
        {"full_name": "Acme/product", "access": "write", option: True}]})
    assert card.json()["needs_confirm"] is True, card.text
    assert _stored_card(api, card.json())["description"] == \
        "ops: " + words.replace(" in product:", " in product once Chosen repos is on with Write there:") + ".", card.text


def test_a_declined_run_workflows_card_changes_nothing(api, botops, repos):
    ana = turn(api, botops, person="ana-test", text="Tick Run workflows on product for ops")
    card = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [FLAG]}).json()
    assert card["needs_confirm"] is True
    post(api, f"assistant/actions/{card['action']['id']}/cancel", {}, token="ana-test")
    assert _stored_card(api, card)["status"] != "done"
    assert effective(api) == [{"full_name": "Acme/product", "access": "write"}]


@pytest.mark.parametrize("option,words", OPTIONS)
def test_a_member_asking_botops_for_run_workflows_changes_nothing_and_is_told_who_can(api, botops, repos, option, words):
    from backend.tests.test_mcp import call as mcp
    FLAG = {"full_name": "Acme/product", "access": "write", option: True}
    cara = turn(api, botops, person="cara-test", text="Tick Run workflows on product for ops")
    refused = act(api, cara, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [FLAG]})
    assert refused.status_code == 403 and "owner or an admin" in refused.text, refused.text
    err, answer = mcp(api, "hub_settings_set", {"key": "bot.repositories", "bot": "ops",
                                                "value": {"mode": "chosen", "chosen": [FLAG]}}, cara["token"])
    assert err and "the owner or an admin" in str(answer), answer
    assert effective(api) == [{"full_name": "Acme/product", "access": "write"}]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM assistant_actions").fetchone()


def test_run_workflows_on_a_read_only_grant_says_write_comes_first_and_makes_no_card(api, botops, repos):
    from backend.tests.test_mcp import call as mcp
    ana = turn(api, botops, person="ana-test", text="Let ops run workflows in docs")
    body = {"mode": "chosen", "chosen": [{"full_name": "Acme/product", "access": "write"},
                                         {"full_name": "Acme/docs", "access": "read", "run_workflows": True}]}
    refused = act(api, ana, "PUT", "bots/ops/repositories", body)
    assert refused.status_code == 422 and "needs write access" in refused.text, refused.text
    err, answer = mcp(api, "hub_settings_set", {"key": "bot.repositories", "bot": "ops", "value": body}, ana["token"])
    assert err and "needs write access" in str(answer), answer
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM assistant_actions").fetchone()
    assert effective(api) == [{"full_name": "Acme/product", "access": "write"}]


def test_the_catalogue_puts_run_workflows_on_a_card_in_the_bots_own_words(api, botops, repos):
    from backend.tests.test_mcp import call as mcp
    ana = turn(api, botops, person="ana-test", text="Tick Run workflows on product for ops")
    err, shown = mcp(api, "hub_settings_show", {"key": "bot.repositories", "bot": "ops"}, ana["token"])
    assert not err and shown["setting"]["class_when"] == {"class": "confirm", "body": {
        "chosen.run_workflows": [True], "chosen.edit_workflows": [True], "all_repos.run_workflows": [True],
        "all_repos.edit_workflows": [True], "create_product_repositories": [True]}}, shown
    assert "Run workflows or Edit workflow files tick" in shown["setting"]["why"] and "Confirm card" in shown["setting"]["why"]
    err, card = mcp(api, "hub_settings_set", {"key": "bot.repositories", "bot": "ops",
                                              "value": {"mode": "chosen", "chosen": [FLAG]}}, ana["token"])
    assert not err and card["needs_confirm"] is True, card
    assert card["action"]["description"].startswith("ops: may run workflows in product"), card
    assert effective(api) == [{"full_name": "Acme/product", "access": "write"}]
    # Adding a grant with no tick is still direct, and keeps nothing it shouldn't.
    err, done = mcp(api, "hub_settings_set", {"key": "bot.repositories", "bot": "ops", "value": {"mode": "chosen",
                    "chosen": [{"full_name": "Acme/product", "access": "write"}, {"full_name": "Acme/docs", "access": "read"}]}},
                    ana["token"])
    assert not err and "needs_confirm" not in done, done
    assert {r["full_name"] for r in effective(api)} == {"Acme/product", "Acme/docs"}


@pytest.mark.parametrize("option,words", OPTIONS)
def test_botops_never_clears_or_brings_back_run_workflows_without_a_card(api, botops, repos, option, words):
    FLAG = {"full_name": "Acme/product", "access": "write", option: True}
    ana = turn(api, botops, person="ana-test", text="Let ops start the release check")
    assert put(api, "bots/ops/repositories", {"mode": "chosen", "chosen": [FLAG]}, token="ana-test")["effective"] == [FLAG]
    # BotOps adding a grant leaves the setting as it is.
    added = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [
        {"full_name": "Acme/product", "access": "write"}, {"full_name": "Acme/docs", "access": "read"}]})
    assert added.status_code == 200 and "needs_confirm" not in added.json(), added.text
    assert FLAG in effective(api)
    cleared = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [{**FLAG, option: False}]})
    assert cleared.json()["needs_confirm"] is True and FLAG in effective(api)
    # A kept setting coming back into effect is a gain too: switching Own back to Chosen under it is a card.
    put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    back = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen"})
    assert back.json()["needs_confirm"] is True and effective(api) == []
    assert words in back.json()["action"]["description"].lower()
    # Nor can it keep a tick for later on a grant that is not in effect yet.
    put(api, "bots/ops/repositories", {"mode": "chosen", "chosen": [{**FLAG, option: False}]}, token="ana-test")
    put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    kept = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "own", "chosen": [FLAG]})
    assert kept.json()["needs_confirm"] is True, kept.text
    assert get(api, "bots/ops/repositories", "ana-test")["chosen"] == [{"full_name": "Acme/product", "access": "write"}]


def test_the_catalogue_classifies_a_repository_change_by_what_it_does_not_only_by_its_body(api, botops, repos):
    """Review 2 of PR #1700: a kept Run workflows tick comes back with `{"mode": "chosen"}`, which holds no tick; the
    catalogue tries the change as the person's own click, so it asks for the card the route would make."""
    from backend.tests.test_mcp import call as mcp

    def ask(value):
        err, answer = mcp(api, "hub_settings_set", {"key": "bot.repositories", "bot": "ops", "value": value},
                          ana["token"])
        assert not err, answer
        return answer
    ana = turn(api, botops, person="ana-test", text="Put ops back on its chosen repositories")
    put(api, "bots/ops/repositories", {"mode": "chosen", "chosen": [FLAG]}, token="ana-test")
    put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    back = ask({"mode": "chosen"})
    assert back["needs_confirm"] is True and effective(api) == [], back
    assert back["action"]["description"].startswith("ops: gets write on product. May run workflows in product"), back
    post(api, f"assistant/actions/{back['action']['id']}/confirm", {}, token="ana-test")
    assert effective(api) == [FLAG]
    # Sending again a tick that is on changes nothing the route cards, so the catalogue runs it, as the route does.
    again = ask({"mode": "chosen", "chosen": [FLAG]})
    assert "needs_confirm" not in again and effective(api) == [FLAG], again
    # Taking access away is the route's card too, though the body holds no tick.
    away = ask({"mode": "own"})
    assert away["needs_confirm"] is True and effective(api) == [FLAG], away


def test_turning_product_repositories_back_on_is_the_owners_card_and_an_admin_is_told_only_the_owner(api, botops, repos):
    from backend.tests.test_mcp import call as mcp
    assert put(api, "bots/ops/repositories", {"mode": "chosen", "create_product_repositories": False},
               token="ana-test")["create_product_repositories"] is False
    on = {"mode": "chosen", "create_product_repositories": True}
    ben = turn(api, botops, person="ben-test", text="Let ops create product repositories again")
    refused = act(api, ben, "PUT", "bots/ops/repositories", on)
    assert refused.status_code == 403 and "Only the Owner" in refused.text, refused.text
    err, answer = mcp(api, "hub_settings_set", {"key": "bot.repositories", "bot": "ops", "value": on}, ben["token"])
    assert err and "Only the Owner" in str(answer), answer
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM assistant_actions").fetchone()
    finish(api, botops, ben)
    ana = turn(api, botops, person="ana-test", text="Let ops create product repositories again")
    card = act(api, ana, "PUT", "bots/ops/repositories", on).json()
    assert card["needs_confirm"] is True and card["action"]["description"] == \
        "ops: may create product repositories again.", card
    assert get(api, "bots/ops/repositories", "ana-test")["create_product_repositories"] is False
    done = post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, token="ana-test")
    assert done["action"]["status"] == "done", done
    assert get(api, "bots/ops/repositories", "ana-test")["create_product_repositories"] is True


def test_a_repository_card_says_in_plain_words_what_changes():
    """Chris, Oct 17: the card read "PUT /api/v2/bots/security-engineer-2/repositories" and "access to chosen"."""
    from backend.repositories import plain_change, plain_request
    org = "ticoteam/"
    before = {"effective": [{"full_name": org + n, "access": "write"} for n in ("tico", "tico-marketing-site", "tico-recorder")]}
    result = {"effective": [{"full_name": org + "tico", "access": "read"}],
              "stopped": [{"full_name": org + n, "access": "write"} for n in ("tico", "tico-marketing-site", "tico-recorder")]}
    assert plain_change("Security Engineer", before, result) == (
        "Security Engineer: read-only on tico (was write). Loses access to tico-marketing-site and tico-recorder.")
    gained = {"effective": [{"full_name": org + "tico", "access": "write"}, {"full_name": org + "docs", "access": "read"}],
              "stopped": [], "workflows_stopped": [org + "tico"], "creation_stopped": True}
    assert plain_change("Ops", {"effective": [{"full_name": org + "tico", "access": "write"}]}, gained) == (
        "Ops: gets read-only on docs. Can no longer run workflows in tico. Can no longer create repositories.")
    assert plain_request("Ops", {"mode": "chosen", "chosen": [{"full_name": org + "tico", "access": "read"}]}) == (
        "Ops: read-only on tico, besides its own repository.")
    assert plain_request("Ops", {"mode": "all", "all_access": "read"}) == "Ops: read-only on every repository the team uses."
    assert plain_request("Ops", {"mode": "own"}) == "Ops: only its own repository."
    # Left out, the list and the level keep what is stored (set_access): the line never guesses "only its own".
    assert plain_request("Ops", {"mode": "chosen"}) == "Ops: the repositories picked for it before, again."
    assert plain_request("Ops", {"mode": "all"}) == "Ops: the access level set before on every repository the team uses."


def test_a_card_made_elsewhere_says_what_confirming_really_does(api, botops, repos):
    """A carried hand-off's card (create_proposal, not bot_set): {"mode": "chosen"} with no list turns the stored grants
    back on, and the title says so, from the change tried as the person and rolled back."""
    from backend.repositories import preview_change
    put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:
        assert preview_change(c, settings, "human:ana", "ops", {"mode": "chosen"}) == "ops: gets write on product."
        assert preview_change(c, settings, "human:ana", "ops", {"mode": "bogus"}) is None
    assert effective(api) == []                      # nothing kept from the try
    # The card itself, as a carried hand-off makes it (app.py confirm_for_carried -> create_proposal).
    from backend import rooms
    from backend.assistant import create_proposal
    state = api.app.state
    with state.store.transaction() as c:
        ana = state.auth.identity_for_actor(c, "human:ana")
        room = rooms.chat_room(c, state.auth, ana, "botops")
        card = create_proposal(c, settings, ana, room, "botops", "Ops asked BotOps for this for you: Tidy",
                               "PUT", "/api/v2/bots/ops/repositories", {"mode": "chosen"})
    assert card["action"]["description"] == "ops: gets write on product."
    assert effective(api) == []


RAW_WORDS = {"own", "all", "chosen", "none"}


def test_the_new_bot_repository_default_reads_as_what_new_bots_get(api, botops):
    """{"new_bot_default": "all"} is the card a person confirms for Settings > Repositories > New bots get."""
    from backend import rooms
    from backend.assistant import create_proposal
    state = api.app.state
    with state.store.transaction() as c:
        ana = state.auth.identity_for_actor(c, "human:ana")
        room = rooms.chat_room(c, state.auth, ana, "botops")
        cards = [create_proposal(c, state.store.settings, ana, room, "botops", "Open up new bots",
                                 "PUT", "/api/v2/repositories/settings", {"new_bot_default": v})["action"] for v in ("all", "own", "x")]
    assert [a["description"] for a in cards] == [
        "New bots get every repository ticked in Settings > Repositories", "New bots get only their own repository",
        "Change which repositories new bots get"]


def test_no_card_is_titled_with_its_method_and_path(api):
    """Every route a card may name has a plain title: a route with no sentence of its own still gets words."""
    from backend.assistant import describe
    from backend import botops_act
    with api.app.state.store.read() as c:
        for path in ("/api/v2/bots/ops/repositories", "/api/v2/bots/ops/go-live", "/api/v2/bots/ops/tools/t1/delete",
                     "/api/v2/routines/r1", "/api/v2/groups/g1", "/api/v2/repositories/Acme/product", "/api/v2/slack/channels"):
            for method in ("POST", "PUT", "DELETE"):
                what, _ = describe(c, method, path, {"mode": "chosen", "enabled": True})
                assert "/api/" not in what and not what.startswith(method + " "), (method, path, what)
                assert not RAW_WORDS & set(re.findall(r"[a-z]+", what.lower())), (method, path, what)
        # A setting value is never read out as a word; one whose field holds a name is.
        assert describe(c, "PUT", "/api/v2/access/rules", {"mode": "own", "scope": "all"})[0] == \
            "Make a change in Tico (access): mode, scope"
        assert describe(c, "POST", "/api/v2/bots/ops/model", {"model": "gpt-6.1", "paused": False})[0] == \
            "Change bot ops's model: model: gpt-6.1, paused: no"
        assert describe(c, "DELETE", "/api/v2/groups/g1", {})[0] == "Remove something in Tico (groups)"
        assert describe(c, "POST", "/api/v2/bots/ops/go-live", {})[0] == "Put bot ops live"
    assert botops_act.DO     # the routes above are BotOps' own (botops_act.DO)


def test_a_repository_card_stored_before_the_plain_wording_reads_plainly(api, botops, repos):
    """Chris's card: stored titled with its method and path and summed up as "access to chosen". It is read again from
    the request it sends, and the old line is kept, only for Details."""
    from backend import rooms
    from backend.assistant import create_proposal
    state = api.app.state
    body = {"mode": "chosen", "chosen": [{"full_name": "Acme/product", "access": "read"}]}
    old = "Set ops's repository access to chosen. This stops: Acme/product (write)"
    with state.store.transaction() as c:
        ana = state.auth.identity_for_actor(c, "human:ana")
        room = rooms.chat_room(c, state.auth, ana, "botops")
        aid = create_proposal(c, state.store.settings, ana, room, "botops", old, "PUT", "/api/v2/bots/ops/repositories", body)["action"]["id"]
        c.execute("UPDATE assistant_actions SET description=?, summary=? WHERE id=?", ("PUT /api/v2/bots/ops/repositories", old, aid))
    action = get(api, f"assistant/actions/{aid}", "ana-test")["action"]
    assert action["description"] == "ops: read-only on product, besides its own repository."
    assert action["summary"] == "" and action["original_summary"] == old
    assert action["body"] == body and action["method"] == "PUT"      # what Confirm sends is untouched
