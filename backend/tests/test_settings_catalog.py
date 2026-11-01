"""The settings catalogue (backend/settings_catalog.py): every route a Settings card writes has an entry, and
`hub settings find|show|set` change a setting with the rights of the person who asked.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import botops_act, settings_catalog, team_rules
from backend.assistant import valid_operation
from backend.settings_entries import ALIASES, ENTRIES, NO_CARD_YET, NOT_SETTINGS, SETTINGS_FILES
from backend.store import H, Problem
from backend.tests.test_api import api, get, post  # noqa: F401  (fixture)
from backend.tests.test_mcp import call as mcp
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)

UI = Path(__file__).resolve().parents[2] / "ui" / "app"
# post('/v2/x', ...), put(`/v2/bots/${slug}/model`, ...), patch('/v2/a/' + id, ...), writeRequest('DELETE', '/v2/x')
CALL = re.compile(r"\b(post|put|patch)\(\s*(['\"`])((?:(?!\2).)*)\2((?:\s*\+\s*[^,)]+)*)", re.S)
RAW = re.compile(r"writeRequest\(\s*'(DELETE|POST|PUT|PATCH)'\s*,\s*(['\"`])((?:(?!\2).)*)\2((?:\s*\+\s*[^,)]+)*)", re.S)


def _template(path, tail):
    """`/v2/a/${id}` + id + '/run' as `a/{x}{x}/run`, then each run of placeholders as one."""
    for piece in (p.strip() for p in tail.split("+") if p.strip()):
        quoted = re.fullmatch(r"(['\"`])([^'\"`]*)\1", piece)
        path += quoted.group(2) if quoted else "{x}"
    path = re.sub(r"(\{x\})+", "{x}", re.sub(r"\$\{[^}]*\}", "{x}", path))
    return re.sub(r"^/?(v2/)?", "", path.split("?")[0])


def settings_writes():
    """(method, route) for every write the Settings pages make, `{x}` where the page fills a value in."""
    found = set()
    for name in SETTINGS_FILES:
        text = (UI / name).read_text()
        found |= {(m.group(1).upper(), _template(m.group(3), m.group(4))) for m in CALL.finditer(text)}
        found |= {(m.group(1), _template(m.group(3), m.group(4))) for m in RAW.finditer(text)}
    return found


def _covers(route, written):
    """An entry's route (`bots/{bot}/model`) covers a written one (`bots/{x}/model`) segment by segment; a
    placeholder on either side stands for any one segment, and `{x}` inside a segment for any text there."""
    mine, theirs = route.split("/"), written.split("/")
    if len(mine) != len(theirs):
        return False
    for a, b in zip(mine, theirs):
        if a == b or re.fullmatch(r"\{\w+\}", a) or b == "{x}":
            continue
        if "{x}" in b and re.fullmatch(re.escape(b).replace(r"\{x\}", ".*"), a):
            continue
        return False
    return True


def test_every_route_a_settings_card_writes_has_a_catalogue_entry():
    writes = settings_writes()
    assert len(writes) > 60                      # the extraction still reads the pages
    missing = sorted((method, route) for method, route in writes
                     if not any(e.method == method and _covers(e.route, route) for e in ENTRIES)
                     and (method, route) not in ALIASES and (method, route) not in NOT_SETTINGS)
    assert not missing, "Settings writes with no entry in backend/settings_entries.py: " + repr(missing)
    stale = sorted(k for k in (*ALIASES, *NOT_SETTINGS) if k not in writes)
    assert not stale, "ALIASES or NOT_SETTINGS list writes no Settings page makes any more: " + repr(stale)
    # A page that builds its path in code still writes a route some entry covers.
    keys = {e.key for e in ENTRIES}
    assert not [k for k in ALIASES.values() if k not in keys]
    assert not [w for w in NOT_SETTINGS if any(e.method == w[0] and _covers(e.route, w[1]) for e in ENTRIES)]


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
        # A Confirm card can only carry a route a person may confirm from BotOps.
        if e.kind == "confirm":
            path = "/api/v2/" + re.sub(r"\{\w+\}", "x", e.route)
            assert valid_operation(e.method, path, None, "botops"), e.key
    assert botops_act  # the classifier the confirm check leans on stays importable


def test_a_setting_waiting_for_a_confirm_card_goes_back_to_confirm_once_botops_can_make_one():
    for e in ENTRIES:
        card = valid_operation(e.method, "/api/v2/" + re.sub(r"\{\w+\}", "x", e.route), None, "botops")
        if e.key in NO_CARD_YET:
            assert not card, e.key + " can be put on a Confirm card now: take it out of NO_CARD_YET"
            assert e.kind == "person" or e.when_held == "person", e.key
        elif e.confirm_when:
            assert card and e.when_held == "confirm", e.key


def _owner(via="botops"):
    return SimpleNamespace(role="owner", via=via, actor="human:ana-test")


def test_botops_never_runs_a_confirm_setting_when_no_card_can_be_made():
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


def test_find_lists_admins_see_sql_with_its_place_and_class(api):
    found = get(api, "settings/catalog?q=sql")["settings"]
    sql = next(s for s in found if s["key"] == "team.rules.admin_sql")
    assert sql["name"] == "Admins see SQL" and sql["class"] == "confirm" and sql["where"].startswith("Settings >")
    assert sql["card"].endswith(ENTRY("team.rules.admin_sql").card)


def ENTRY(key):  # noqa: N802
    return settings_catalog.entry(key)


def test_a_member_changes_a_direct_setting_through_botops_and_the_reply_names_the_value(api, botops):
    ana = turn(api, botops, person="ana-test", text="Set the release to 09:30")
    err, done = mcp(api, "hub_settings_set", {"key": "release.times", "value": "09:30"}, ana["token"])
    assert not err and done["value"] == ["09:30"], done
    err, shown = mcp(api, "hub_settings_show", {"key": "release.times"}, ana["token"])
    assert not err and shown["value"] == ["09:30"] and shown["you_may"] is True, shown
    finish(api, botops, ana)


def test_a_confirm_setting_from_botops_is_a_card_and_nothing_changes(api, botops):
    ana = turn(api, botops, person="ana-test", text="Stop admins seeing SQL")
    err, card = mcp(api, "hub_settings_set", {"key": "team.rules.admin_sql", "value": "off"}, ana["token"])
    assert not err and card["needs_confirm"] is True, card
    assert get(api, "access")["rules"]["admin_sql"] is True
    finish(api, botops, ana)


def test_a_person_only_setting_gets_the_reason_and_the_cards_link(api, botops):
    person_only = next(e for e in ENTRIES if e.kind == "person" and not e.target)
    ana = turn(api, botops, person="ana-test", text="Change it for me")
    err, refused = mcp(api, "hub_settings_set", {"key": person_only.key, "value": "x"}, ana["token"])
    assert err and person_only.card in str(refused), refused
    finish(api, botops, ana)


def test_someone_without_the_rights_is_told_who_can(api, botops):
    cara = turn(api, botops, person="cara-test", text="Set the release to 09:30")
    err, refused = mcp(api, "hub_settings_set", {"key": "release.times", "value": "09:30"}, cara["token"])
    assert err and "owner or an admin" in str(refused), refused
    err, shown = mcp(api, "hub_settings_show", {"key": "release.times"}, cara["token"])
    assert not err and shown["you_may"] is False and "admin" in shown["setting"]["who_may"], shown


def test_show_a_bots_spend_limit_and_who_may_change_it_and_a_member_changes_a_direct_setting(api, botops):
    ana = turn(api, botops, person="ana-test", text="What are BotOps' spend limits?")
    err, shown = mcp(api, "hub_settings_show", {"key": "bot.limit.usd", "bot": "botops"}, ana["token"])
    assert not err and "value_error" not in shown and isinstance(shown["value"], dict), shown
    assert shown["setting"]["who_may"].startswith("The bot's owners") and shown["you_may"] is True, shown
    finish(api, botops, ana)
    cara = turn(api, botops, person="cara-test", text="Make me an incoming trigger called Orders")
    err, done = mcp(api, "hub_settings_set", {"key": "webhooks.incoming.create", "value": {"name": "Orders"}},
                    cara["token"])
    assert not err and done["value"] == {"name": "Orders"}, done
    with api.app.state.store.read() as c:
        assert c.execute("SELECT created_by FROM triggers WHERE name='Orders'").fetchone()[0] == "human:cara"


def test_catalogue_resolves_the_bot_for_id_addressed_transitions(api, botops):
    from backend.tests.test_api import as_member
    from backend.tests.test_member_bots import register

    ana = turn(api, botops, person="cara-test", text="Make me a bot")
    made = register(api, ana, "routine-owner")
    assert made.status_code == 200, made.text
    finish(api, botops, ana)

    # The id resolves the bot for transition controls, including the read-only permission answer.
    now = H.now()
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_transitions(id,bot,kind,target,requested_by,expected_revision,state,created_at,updated_at) "
                  "VALUES('tr-owner','routine-owner','model','{}','human:cara',1,'prepared',?,?)", (now, now))
    err, shown = mcp(api, "hub_settings_show", {"key": "bot.change.cancel", "id": "tr-owner"}, "cara-test")
    assert not err and shown["you_may"] is True, shown

    # An admin retains access; an unrelated ordinary member does not inherit the bot owner's rights.
    err, admin = mcp(api, "hub_settings_show", {"key": "bot.change.cancel", "id": "tr-owner"}, "ben-test")
    assert not err and admin["you_may"] is True, admin
    as_member(api, "ben@acme.example")
    err, unrelated = mcp(api, "hub_settings_show", {"key": "bot.change.cancel", "id": "tr-owner"}, "ben-test")
    assert not err and unrelated["you_may"] is False, unrelated