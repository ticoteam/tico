"""The settings catalogue (backend/settings_catalog.py): every route a Settings card writes has an entry, and
`tico settings find|show|set` change a setting with the rights of the person who asked.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""

import re
from types import SimpleNamespace

import pytest

from backend import agent_ops_act, settings_catalog, team_rules
from backend.assistant import valid_operation
from backend.settings_entries import ENTRIES
from backend.store import H, Problem
from backend.tests.helpers.api import get, post  # noqa: F401  (fixture)
from backend.tests.helpers.mcp import call as mcp
from backend.tests.helpers.member_bots import agent_ops, finish, turn  # noqa: F401  (fixtures)


def test_every_team_rule_has_an_entry_and_every_entry_is_well_formed():
    keys = {e.key for e in ENTRIES}
    assert len(keys) == len(ENTRIES)
    rules = {e.field for e in ENTRIES if e.route == "access/rules"}
    assert set(team_rules.DEFAULTS) | set(team_rules.NUMBERS) <= rules
    for e in ENTRIES:
        assert re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", e.key), e.key
        assert e.kind in settings_catalog.CLASSES and e.who in settings_catalog.WHO, e.key
        assert e.card.startswith("#/"), e.key
        assert e.kind != "person" or e.why, e.key
        # A Confirm card can only carry a route a person may confirm from Agent Ops.
        if e.kind == "confirm":
            path = settings_catalog.probe(e)
            assert valid_operation(e.method, path, None, "botops"), e.key
    assert agent_ops_act  # the classifier the confirm check leans on stays importable


def _owner(via="botops"):
    return SimpleNamespace(role="owner", via=via, actor="human:ana-test")


def test_agent_ops_never_runs_a_confirm_setting_when_no_card_can_be_made():
    e = ENTRY("team.rules.admin_sql")
    with pytest.raises(Problem) as refused:
        settings_catalog.plan(None, None, _owner(), e, "off", propose=None)
    assert refused.value.code == "unavailable" and e.card in refused.value.detail
    assert "run" in settings_catalog.plan(None, None, _owner(via=""), e, "off", propose=None)


def test_nothing_is_not_a_value_and_the_team_icon_is_an_upload():
    for key in ("release.times", "bot.definition", "release.bot"):
        with pytest.raises(Problem) as refused:
            settings_catalog.parse(ENTRY(key), None)
        assert refused.value.status == 422, key
    e = ENTRY("team.icon")
    with pytest.raises(Problem) as refused:
        settings_catalog.plan(None, None, _owner(via=""), e, "icon.png")
    assert refused.value.status == 422 and e.card in refused.value.detail


def test_the_theme_is_found_but_has_no_request_to_plan():
    e = ENTRY("app.theme")
    with pytest.raises(Problem) as refused:
        settings_catalog.plan(None, None, _owner(via=""), e, "dark")
    assert refused.value.status == 422 and e.card in refused.value.detail


def test_find_lists_admins_see_sql_with_its_place_and_class(api):
    found = get(api, "settings/catalog?q=sql")["settings"]
    sql = next(s for s in found if s["key"] == "team.rules.admin_sql")
    assert sql["name"] == "Admins see SQL" and sql["class"] == "confirm" and sql["where"].startswith("Settings >")
    assert sql["card"].endswith(ENTRY("team.rules.admin_sql").card)


def ENTRY(key):  # noqa: N802
    return settings_catalog.entry(key)


def test_a_cloud_machine_size_is_found_by_its_words_and_named_for_a_bot_opens_that_bots_own_settings(api):
    for words in ("size", "xlarge", "bigger machine", "more room"):
        keys = [s["key"] for s in get(api, "settings/catalog?q=" + words)["settings"]]
        assert {"bot.cloud_machine", "cloud.shared_machine"} <= set(keys), words
    # Named for a bot (its Size row is on the bot's own Settings, Computer), the link opens that page; a search with no
    # bot named opens Settings > Bots, whose bot editor holds the same rows.
    own = get(api, "settings/catalog/bot.cloud_machine?bot=ops")["setting"]
    assert own["card"].endswith("#/bot/ops/settings")
    assert get(api, "settings/catalog/bot.cloud_machine")["setting"]["card"].endswith("#/settings/bots")
    assert (
        settings_catalog.refusal(ENTRY("bot.cloud_machine"), "", {"bot": "a b"}).extra["link"] == "#/bot/a%20b/settings"
    )


def test_a_member_changes_a_direct_setting_through_agent_ops_and_the_reply_names_the_value(api, agent_ops):
    ana = turn(api, agent_ops, person="ana-test", text="Set the release to 09:30")
    err, done = mcp(api, "tico_settings_set", {"key": "release.times", "value": "09:30"}, ana["token"])
    assert not err and done["value"] == ["09:30"], done
    err, shown = mcp(api, "tico_settings_show", {"key": "release.times"}, ana["token"])
    assert not err and shown["value"] == ["09:30"] and shown["you_may"] is True, shown
    finish(api, agent_ops, ana)


def test_a_confirm_setting_from_agent_ops_is_a_card_and_nothing_changes(api, agent_ops):
    ana = turn(api, agent_ops, person="ana-test", text="Stop admins seeing SQL")
    err, card = mcp(api, "tico_settings_set", {"key": "team.rules.admin_sql", "value": "off"}, ana["token"])
    assert not err and card["needs_confirm"] is True, card
    assert get(api, "access")["rules"]["admin_sql"] is True
    finish(api, agent_ops, ana)


def test_mail_access_through_agent_ops_waits_for_the_persons_confirmation(api, agent_ops):
    ana = turn(api, agent_ops, person="ana-test", text="Let Ops read our physical mail")
    err, missing = mcp(
        api, "tico_settings_set", {"key": "mail.access.bot", "bot": "ops", "value": "read"}, ana["token"]
    )
    assert err and "--id" in str(missing), missing
    err, card = mcp(
        api, "tico_settings_set", {"key": "mail.access.bot", "id": "all", "bot": "ops", "value": "read"}, ana["token"]
    )
    assert not err and card["needs_confirm"] is True, card
    assert card["action"]["path"] == "/api/v2/postal/addresses/all/grants/bot/ops"
    assert get(api, "postal/addresses/all/grants")["grants"] == []
    finish(api, agent_ops, ana)
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, "ana-test")
    grant = get(api, "postal/addresses/all/grants")["grants"][0]
    assert (grant["grantee_id"], grant["granted_by_id"]) == ("ops", "human:ana")


def test_a_person_only_setting_gets_the_reason_and_the_cards_link(api, agent_ops):
    person_only = next(e for e in ENTRIES if e.kind == "person" and not e.target)
    ana = turn(api, agent_ops, person="ana-test", text="Change it for me")
    err, refused = mcp(api, "tico_settings_set", {"key": person_only.key, "value": "x"}, ana["token"])
    assert err and person_only.card in str(refused), refused
    finish(api, agent_ops, ana)


def test_someone_without_the_rights_is_told_who_can(api, agent_ops):
    cara = turn(api, agent_ops, person="cara-test", text="Set the release to 09:30")
    err, refused = mcp(api, "tico_settings_set", {"key": "release.times", "value": "09:30"}, cara["token"])
    assert err and "owner or an admin" in str(refused), refused
    err, shown = mcp(api, "tico_settings_show", {"key": "release.times"}, cara["token"])
    assert not err and shown["you_may"] is False and "admin" in shown["setting"]["who_may"], shown


def test_show_a_bots_spend_limit_and_who_may_change_it_and_a_member_changes_a_direct_setting(api, agent_ops):
    ana = turn(api, agent_ops, person="ana-test", text="What are Agent Ops' spend limits?")
    err, shown = mcp(api, "tico_settings_show", {"key": "bot.limit.usd", "bot": "botops"}, ana["token"])
    assert not err and "value_error" not in shown and isinstance(shown["value"], dict), shown
    assert shown["setting"]["who_may"].startswith("The bot's owners") and shown["you_may"] is True, shown
    finish(api, agent_ops, ana)
    cara = turn(api, agent_ops, person="cara-test", text="Make me an incoming trigger called Orders")
    err, done = mcp(
        api, "tico_settings_set", {"key": "webhooks.incoming.create", "value": {"name": "Orders"}}, cara["token"]
    )
    assert not err and done["value"] == {"name": "Orders"}, done
    with api.app.state.store.read() as c:
        assert c.execute("SELECT created_by FROM triggers WHERE name='Orders'").fetchone()[0] == "human:cara"


def test_catalogue_checks_a_bot_setting_against_the_bot_it_names(api, agent_ops):
    from backend.tests.helpers.api import as_member
    from backend.tests.helpers.member_bots import register

    ana = turn(api, agent_ops, person="cara-test", text="Make me a bot")
    made = register(api, ana, "routine-owner")
    assert made.status_code == 200, made.text
    finish(api, agent_ops, ana)

    # Cara manages the bot she made: the bot it names decides, including the read-only permission answer.
    with api.app.state.store.read() as c:
        bot = H.resolve_bot(c, "routine-owner")["id"]
    err, shown = mcp(api, "tico_settings_show", {"key": "bot.task_model", "bot": bot}, "cara-test")
    assert not err and shown["you_may"] is True, shown

    # An admin keeps access; an unrelated ordinary member does not inherit the bot owner's rights.
    err, admin = mcp(api, "tico_settings_show", {"key": "bot.task_model", "bot": bot}, "ben-test")
    assert not err and admin["you_may"] is True, admin
    as_member(api, "ben@acme.example")
    err, unrelated = mcp(api, "tico_settings_show", {"key": "bot.task_model", "bot": bot}, "ben-test")
    assert not err and unrelated["you_may"] is False, unrelated


def test_an_admin_asking_agent_ops_to_raise_a_group_pool_limit_gets_a_card_the_tap_runs(api, agent_ops):
    """Settings stage 3, acceptance 13: a group's pool is `groups/<id>`, two segments of its route, so the card names
    the route Settings uses and the admin's tap sets it. Before, the id was one escaped segment and no card could be
    made, so Agent Ops sent the admin to Settings."""
    from backend.tests.helpers.usage_limits import groups

    groups(api, "product")
    ben = turn(api, agent_ops, person="ben-test", text="Raise the Engineering pool to $50 a day")
    err, card = mcp(
        api,
        "tico_settings_set",
        {"key": "pool.limit.usd", "id": "groups/eng", "value": {"daily_usd": 50}},
        ben["token"],
    )
    assert not err and card["needs_confirm"] is True, card
    assert card["action"]["path"] == "/api/v2/usage/pools/groups/eng", card["action"]
    assert "$50" in card["action"]["description"], card["action"]
    finish(api, agent_ops, ben)
    assert get(api, "usage/limits")["pools"]["groups"]["eng"]["daily_usd"] is None  # nothing until the tap
    post(api, f"assistant/actions/{card['action']['id']}/confirm", {}, "ben-test")
    assert get(api, "usage/limits")["pools"]["groups"]["eng"]["daily_usd"] == 50
    for bad in ("groups", "groups/eng/x", "eng"):
        err, refused = mcp(
            api, "tico_settings_set", {"key": "pool.limit.usd", "id": bad, "value": {"daily_usd": 5}}, "ben-test"
        )
        assert err and refused["error"] == "validation", (bad, refused)
