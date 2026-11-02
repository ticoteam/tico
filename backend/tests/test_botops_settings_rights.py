"""BotOps changes a team setting with the rights of the person who asked, never its own (docs/permissions.md): the
release schedule, the Task Manager, stalled task check, the team's run budget, task types, tags and incoming triggers.

Ana owns the company, Ben is an admin, Cara is a member (the base fixture).
"""
from backend.tests.test_api import api, post  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_mcp import call as mcp
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)


def test_an_owner_changes_team_settings_through_botops_and_a_member_is_refused(api, botops):
    ana = turn(api, botops, person="ana-test", text="Turn the daily release off and set the team budget")
    for name, args in (("hub_release_schedule", {"on": False}), ("hub_team_budget", {"hour": 5}),
                       ("hub_stall_triage", {"on": False}), ("hub_task_manager_set", {"on": True})):
        err, result = mcp(api, name, args, ana["token"])
        assert not err, (name, result)
    err, made = mcp(api, "hub_task_type_create", {"name": "Ops", "default_visibility": "private"}, ana["token"])
    assert not err and made["type"]["default_visibility"] == "private", made
    with api.app.state.store.read() as c:
        row = c.execute("SELECT actor,detail FROM events WHERE action='task_type.create' ORDER BY rowid DESC "
                        "LIMIT 1").fetchone()
        assert row["actor"] == "human:ana", dict(row)            # hers, via BotOps
    finish(api, botops, ana)

    cara = turn(api, botops, person="cara-test", text="Turn the daily release on")
    for name, args in (("hub_release_schedule", {"on": True}), ("hub_team_budget", {"hour": 9}),
                       ("hub_stall_triage", {"on": True}), ("hub_task_type_delete", {"id": "Ops"})):
        err, result = mcp(api, name, args, cara["token"])
        assert err, (name, result)
    # The same through a plain `hub api` call with no on_behalf_of: the route still checks Cara, not BotOps.
    assert act(api, cara, "PATCH", "release-schedule", {"on": True}, ref=None).status_code == 403


def test_a_member_cannot_change_another_persons_trigger_through_botops(api, botops):
    post(api, "triggers", {"name": "Shop"}, token="ben-test")
    cara = turn(api, botops, person="cara-test", text="Rename the shop trigger")
    err, result = mcp(api, "hub_trigger_update", {"id": "shop", "name": "Cara's"}, cara["token"])
    assert err, result
    for path, body in (("triggers/shop", {"name": "Cara's"}), ("triggers/shop/delete", {}),
                       ("triggers/shop/relay/wake", {"wake": True})):
        assert act(api, cara, "POST", path, body, ref=None).status_code == 403, path
    # Her own trigger, made through BotOps, is hers.
    err, mine = mcp(api, "hub_trigger_create", {"name": "Cara orders"}, cara["token"])
    assert not err, mine
    err, tag = mcp(api, "hub_tag_create", {"key": "cara-launch", "label": "Launch"}, cara["token"])
    assert not err and tag["tag"]["owner_id"] == "human:cara", tag
    with api.app.state.store.read() as c:
        assert c.execute("SELECT created_by FROM triggers WHERE id=?", (mine["id"],)).fetchone()[0] == "human:cara"
        assert c.execute("SELECT name FROM triggers WHERE id='shop'").fetchone()[0] == "Shop"
    finish(api, botops, cara)
    ana = turn(api, botops, person="ana-test", text="Rename the shop trigger")
    err, result = mcp(api, "hub_trigger_update", {"id": "shop", "name": "Store"}, ana["token"])
    assert not err and result["name"] == "Store", result


def test_an_owner_changes_a_triggers_signing_through_botops_but_never_hands_it_a_secret(api, botops):
    post(api, "triggers", {"name": "Hook"}, token="ana-test")
    ana = turn(api, botops, person="ana-test", text="Check GitHub's signature on the hook trigger, then stop")
    err, signed = mcp(api, "hub_trigger_sign", {"id": "hook", "preset": "github"}, ana["token"])
    assert not err and signed["preset"] == "github", signed
    err, off = mcp(api, "hub_trigger_sign", {"id": "hook", "off": True}, ana["token"])
    assert not err and off["signed"] is False, off
    # A secret typed into a chat with BotOps is refused before the route runs: the person sets it in Settings.
    err, refused = mcp(api, "hub_trigger_sign", {"id": "hook", "secret": "s" * 24}, ana["token"])
    assert err, refused
    finish(api, botops, ana)
