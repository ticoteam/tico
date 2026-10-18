"""BotOps acting for a person and repository access: it reads its own repositories as itself, it may give another bot
access with the person's rights, and taking any away is a Confirm card for the person, never done at once."""
import json
import re

import pytest

from backend.tests.test_api import api, get, headers, post, put  # noqa: F401  (fixture)
from backend.tests.test_member_bots import botops, turn  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.store import H


@pytest.fixture
def repos(api):
    """Acme/product and Acme/docs ticked in Settings; ops has write on product, BotOps read on docs."""
    api.app.state.store.settings.github_owner = "Acme"
    with api.app.state.store.transaction() as c:
        for name in ("Acme/product", "Acme/docs"):
            c.execute("INSERT INTO repositories(id,full_name,enabled,updated) VALUES(?,?,1,?)", (H.new_id(), name, H.now()))
        for bot, name, level in (("ops", "Acme/product", "write"), ("botops", "Acme/docs", "read")):
            config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0] or "{}")
            c.execute("UPDATE bot_config SET config_json=? WHERE bot=?",
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
        row = c.execute("SELECT owner,method,path,summary,description,body_json FROM assistant_actions WHERE id=?",
                        (card["action"]["id"],)).fetchone()
        assert (row["owner"], row["method"], row["path"]) == ("human:ana", "PUT", "/api/v2/bots/ops/repositories")
        # Plain words on the card: who, and what it loses; no method, path or mode word ("own", "chosen").
        assert row["description"] == row["summary"] == "ops: loses access to product."
        assert json.loads(row["body_json"]) == {"mode": "own"}
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
        detail = json.loads(c.execute("SELECT detail_json FROM events WHERE action='bot.repos_changed' AND target='ops'"
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


def test_only_an_owner_or_admin_lets_a_bot_run_workflows(api, botops, repos):
    flag = {"full_name": "Acme/product", "access": "write", "run_workflows": True}
    ana = turn(api, botops, person="ana-test", text="Let ops start the release check")
    refused = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [flag]})
    assert refused.status_code == 403 and "Owner or admin" in refused.text
    assert effective(api) == [{"full_name": "Acme/product", "access": "write"}]
    assert put(api, "bots/ops/repositories", {"mode": "chosen", "chosen": [flag]}, token="ana-test")["effective"] == [flag]
    # BotOps adding a grant leaves the setting as it is; it cannot clear it either.
    added = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [
        {"full_name": "Acme/product", "access": "write"}, {"full_name": "Acme/docs", "access": "read"}]})
    assert added.status_code == 200, added.text
    assert flag in effective(api)
    cleared = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen", "chosen": [{**flag, "run_workflows": False}]})
    assert cleared.status_code == 403 and flag in effective(api)
    # A kept setting coming back into effect is a gain too: BotOps cannot switch Own back to Chosen under it.
    put(api, "bots/ops/repositories", {"mode": "own"}, token="ana-test")
    back = act(api, ana, "PUT", "bots/ops/repositories", {"mode": "chosen"})
    assert back.status_code == 403 and effective(api) == []


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
