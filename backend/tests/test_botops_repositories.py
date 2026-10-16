"""BotOps acting for a person and repository access: it reads its own repositories as itself, it may give another bot
access with the person's rights, and taking any away is a Confirm card for the person, never done at once."""
import json

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
        row = c.execute("SELECT owner,method,path,summary,body_json FROM assistant_actions WHERE id=?",
                        (card["action"]["id"],)).fetchone()
        assert (row["owner"], row["method"], row["path"]) == ("human:ana", "PUT", "/api/v2/bots/ops/repositories")
        assert "Acme/product (write)" in row["summary"] and json.loads(row["body_json"]) == {"mode": "own"}
        assert not c.execute("SELECT 1 FROM events WHERE action='bot.repos_changed' AND target='ops'").fetchone()
    # Write turned to read is taken away too.
    lowered = act(api, ana, "PUT", "bots/ops/repositories",
                  {"mode": "chosen", "chosen": [{"full_name": "Acme/product", "access": "read"}]})
    assert lowered.json()["needs_confirm"] is True and effective(api) == before
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
