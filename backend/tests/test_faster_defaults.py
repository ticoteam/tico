"""The faster defaults for permissions: what BotOps does with requester rights (docs/permissions.md). Ana owns the Team, Ben is an admin, Cara is a member (the base fixture)."""

from backend import botops_act as B
from backend.tests.test_api import api, post, put, ready  # noqa: F401  (fixture)
from backend.tests.test_botops_parity import act
from backend.tests.test_member_bots import botops, call, turn  # noqa: F401  (fixtures)


def test_which_routes_run_without_a_card_and_which_still_need_one():
    to_bot = lambda name: str(name).startswith("bot:")
    direct = [("POST", "runners/r1/restart", None), ("POST", "credentials/c1/grants/g1/revoke", None),
              ("PUT", "usage/limits/ops", None), ("PUT", "usage/limits", None), ("PUT", "providers", None),
              ("POST", "chat/ops", None), ("POST", "messages", {"to": "bot:ops"}), ("POST", "runners/r1/logins", None),
              ("GET", "runners/r1/logins/l1", None), ("POST", "runners/r1/inbox-sharing", None),
              ("PUT", "bots/ops/github-repos", None), ("POST", "bots/ops/archive", None),
              ("POST", "system/update", None), ("PUT", "access/rules", None)]
    cards = [("POST", "runners/r1/revoke", None), ("POST", "runners/r1/member-bots", None), ("PUT", "access/allow", None),
             ("PUT", "access/limits", None),
             ("POST", "messages", {"to": "human:ben"}), ("POST", "messages", {"to": "nobody"}), ("POST", "messages", None)]
    for method, path, body in direct:
        assert B.classify(method, path, body, None, to_bot) == "do", (method, path)
    for method, path, body in cards:
        assert B.classify(method, path, body, None, to_bot) == "confirm", (method, path)
    # The pasted sign-in code and personal tokens are never BotOps's.
    assert B.classify("POST", "runners/r1/logins/l1/code", {}, None, to_bot) is None
    assert B.classify("POST", "me/tokens", {}, None, to_bot) is None
    # The owner's rule turns providers and limits back into cards, and nothing else.
    tight = {"botops_direct": False}
    for method, path in (("PUT", "providers"), ("PUT", "usage/limits"), ("PUT", "usage/limits/ops")):
        assert B.classify(method, path, {}, tight, to_bot) == "confirm", path
    for method, path in (("POST", "runners/r1/restart"), ("POST", "chat/ops"), ("POST", "credentials/c1/grants/g1/revoke")):
        assert B.classify(method, path, {}, tight, to_bot) == "do", path


def test_botops_restarts_a_computer_and_starts_a_model_sign_in_without_a_card(api, botops):
    ready(api, botops, ["botops"])
    ana = turn(api, botops, person="ana-test", text="Restart the Mac and sign Claude in")
    restarted = act(api, ana, "POST", f"runners/{botops['runner_id']}/restart", {})
    assert restarted.status_code == 200 and restarted.json()["requested"] is True, restarted.text
    started = act(api, ana, "POST", f"runners/{botops['runner_id']}/logins", {"runtime": "claude"})
    assert started.status_code == 200 and started.json()["state"] == "requested", started.text
    read = act(api, ana, "GET", f"runners/{botops['runner_id']}/logins/{started.json()['id']}")
    assert read.status_code == 200
    # Requester rights reach the code route; its sign-in state check still applies.
    assert act(api, ana, "POST", f"runners/{botops['runner_id']}/logins/{started.json()['id']}/code", {"code": "abcdef"}).status_code == 409
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM assistant_actions").fetchone()[0] == 0
        row = c.execute("SELECT actor,detail_json FROM events WHERE action='runner.restart'").fetchone()
        assert row["actor"] == "human:ana" and '"via": "botops"' in row["detail_json"]


def test_inbox_sharing_is_turned_on_by_botops_only_where_one_owner_runs_everything(api, botops):
    from backend import inbox_isolation
    ana = turn(api, botops, person="ana-test", text="Let the inbox bots share this computer")
    path = f"runners/{botops['runner_id']}/inbox-sharing"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='ana'")                    # one person runs every bot and computer
        assert inbox_isolation.single_owner(c)
    on = act(api, ana, "POST", path, {"allowed": True})
    assert on.status_code == 200 and on.json() == {"allowed": True}, on.text
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='ops'")   # now someone else runs a bot
        assert not inbox_isolation.single_owner(c)
    refused = act(api, ana, "POST", path, {"allowed": True})
    assert refused.status_code == 403 and "one owner" in refused.json()["error"]["detail"]
    assert act(api, ana, "POST", path, {"allowed": False}).status_code == 200      # turning it off is always fine
    # An admin by hand is as before.
    assert call(api, "post", path, "ben-test", {"allowed": True}).status_code == 200


def test_a_member_makes_a_personal_token_until_the_owner_says_otherwise(api):
    assert post(api, "me/tokens", {"label": "laptop"}, "cara-test")["token"].startswith("tico_pt_")
    assert put(api, "access/rules", {"member_tokens": False}, "ana-test")["member_tokens"] is False
    assert call(api, "post", "me/tokens", "cara-test", {"label": "again"}).status_code == 403
    assert call(api, "get", "me/tokens", "cara-test").json()["can_create"] is False      # no Connect on her row
    assert call(api, "get", "me/tokens", "ben-test").json()["can_create"] is True
    assert post(api, "me/tokens", {"label": "admin's"}, "ben-test")["token"]          # admins and the owner still do
