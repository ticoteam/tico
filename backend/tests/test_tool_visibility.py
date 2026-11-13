"""Who can see an API tool (backend/api_tools.py `visible`): Only listed hides a tool and its keys from everyone but the
owner, the list and whoever can use it, on every path: the tool index, the tool's page, its activity, check and writes,
the vault's list, reveal, change, grant, revoke and delete, the chat card, history, SQL, Add's id, a key stored
elsewhere, a bot copy, Counsel's hint, Health and the Assistant's card. Not visible is a 404, never a 403. Everyone (NULL) keeps the rules from before.

Ana owns the company and Ben is an admin (both credential administrators); Cara and Eve are members."""

import json

import pytest

from backend import api_tools
from backend.auth import Identity
from backend.store import Problem
from backend.tests.helpers.api import get, headers, post, put  # noqa: F401  (fixture)
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.tool_visibility import ONLY, SECRET, SECRET_R, bank, eve, patch


def index(api, token):
    return {r["id"]: r for r in get(api, "tool-index", token)["rows"]}


def vault_ids(api, token):
    return {r["id"] for r in get(api, "credentials", token)["credentials"]}


def test_a_credential_admin_who_is_not_listed_gets_404_everywhere_and_never_sees_its_keys(api, monkeypatch):
    tid, rw, ro = bank(api)
    grant = post(api, f"credentials/{rw}/grants", {"subject": "bot:ops"})["id"]
    monkeypatch.setattr(api_tools, "send_check", lambda url, hdrs, params: 200)
    assert "api:" + tid not in index(api, "ben-test")
    for path in (f"api-tools/{tid}", f"api-tools/{tid}/activity"):
        r = api.get("/api/v2/" + path, headers=headers("ben-test"))
        assert r.status_code == 404 and "No such tool" in r.text and "Acme" not in r.text
    for method, path, body in (
        ("post", f"api-tools/{tid}/check", {}),
        ("patch", f"api-tools/{tid}", {"name": "Mine now"}),
        ("patch", f"api-tools/{tid}", {"audience": None}),
        ("post", f"api-tools/{tid}/keys", {"name": "x", "value": "y" * 20, "access_level": "read"}),
        ("patch", f"api-tools/{tid}/keys/{rw}", {"expires_on": "2030-01-01"}),
        ("delete", f"api-tools/{tid}/keys/{rw}", None),
        ("put", f"api-tools/{tid}/access/human:ben", {"level": "read_write"}),
        ("put", f"api-tools/{tid}/access/bot:ops", {"level": "read"}),
        ("delete", f"api-tools/{tid}", None),
    ):
        kw = {"json": body} if body is not None else {}
        r = getattr(api, method)("/api/v2/" + path, headers=headers("ben-test"), **kw)
        assert r.status_code == 404, (method, path, r.text)
        assert SECRET not in r.text and "Acme Bank" not in r.text
    # The vault: not listed, and every action on its keys is a 404, as for a credential that does not exist.
    assert not {rw, ro} & vault_ids(api, "ben-test")
    for path, body in (
        (f"credentials/{rw}/reveal", {}),
        (f"credentials/{rw}/grants", {"subject": "human:ben"}),
        (f"credentials/{ro}/grants", {"subject": "bot:ops"}),
        (f"credentials/{rw}", {"name": "x"}),
    ):
        r = api.post("/api/v2/" + path, json=body, headers=headers("ben-test"))
        assert r.status_code == 404 and SECRET not in r.text and "Acme" not in r.text, (path, r.text)
    assert (
        api.post(f"/api/v2/credentials/{rw}/grants/{grant}/revoke", json={}, headers=headers("ben-test")).status_code
        == 404
    )
    assert api.delete(f"/api/v2/credentials/{rw}", headers=headers("ben-test")).status_code == 404
    # Nothing changed: the tool, both keys and their values are as Ana left them.
    page = get(api, f"api-tools/{tid}")
    assert page["tool"]["name"] == "Acme Bank" and len(page["keys"]) == 2 and page["private"] is True
    assert post(api, f"credentials/{rw}/reveal", {})["value"] == SECRET
    with api.app.state.store.read() as c:
        assert c.execute("SELECT revoked_at FROM credential_grants WHERE id=?", (grant,)).fetchone()[0] is None


def test_a_grant_holder_sees_it_and_the_proxy_key_works_for_them(api):
    tid, rw, ro = bank(api)
    level(api, tid, "human:cara", "read")
    level(api, tid, "bot:ops", "read_write")
    assert index(api, "cara-test")["api:" + tid]["private"] is True
    page = get(api, f"api-tools/{tid}", "cara-test")
    assert (
        page["private"] is True
        and page["can_set_audience"] is False
        and "audience" not in page
        and "seen_by" not in page
    )
    assert post(api, f"credentials/{ro}/reveal", {}, "cara-test")["value"] == SECRET_R
    with api.app.state.store.read() as c:
        assert api_tools.key_for(c, tid, "bot:ops")[0]["id"] == rw
        assert api_tools.key_for(c, tid, "human:cara")[0]["id"] == ro
    # Ben is still out, though Cara and a bot hold its keys.
    get(api, f"api-tools/{tid}", "ben-test", expected=404)


def test_an_audience_member_without_a_grant_sees_it_but_the_proxy_refuses(api):
    tid, rw, _ = bank(api, {**ONLY, "people": ["cara"]})
    assert "api:" + tid in index(api, "cara-test")
    page = get(api, f"api-tools/{tid}", "cara-test")
    assert page["private"] is True and page["can_manage"] is False
    with api.app.state.store.read() as c:
        with pytest.raises(Problem) as refused:
            api_tools.key_for(c, tid, "human:cara")
    assert refused.value.status == 403
    post(api, f"credentials/{rw}/reveal", {}, "cara-test", expected=403)
    get(api, f"api-tools/{tid}", "eve-test", expected=404)


def test_a_listed_group_and_bot_see_it(api):
    setup(api)
    eve(api)
    gid = post(api, "groups", {"name": "Treasury", "add": {"people": ["cara"]}})["id"]
    tid, _, _ = bank(api, {**ONLY, "teams": [gid], "bots": ["ops"]})
    assert "api:" + tid in index(api, "cara-test")
    get(api, f"api-tools/{tid}", "eve-test", expected=404)
    assert ("group:" + gid, "Listed", "Treasury") in [
        (r["subject"], r["why"], r["name"]) for r in get(api, f"api-tools/{tid}")["seen_by"]
    ]
    with api.app.state.store.read() as c:
        admins = api.app.state.vault.admins
        ops, coo = Identity("bot:ops", "bot"), Identity("bot:assistant", "bot")
        assert api_tools.visible(c, ops, tid, admins) is True
        assert api_tools.visible(c, coo, tid, admins) is False


def test_the_owner_always_sees_it_and_may_change_it_without_being_listed(api):
    tid, _, _ = bank(api, {**ONLY, "people": ["cara"]})
    page = get(api, f"api-tools/{tid}")
    assert page["can_set_audience"] is True and page["audience"] == {**ONLY, "people": ["cara"]}
    assert [(r["subject"], r["why"]) for r in page["seen_by"]] == [("human:ana", "Owner"), ("human:cara", "Listed")]
    assert index(api, "ana-test")["api:" + tid]["private"] is True
    patch(api, tid, {"audience": ONLY})
    assert get(api, f"api-tools/{tid}")["audience"] == ONLY  # the owner is never added: always sees it


def test_an_admin_who_sets_only_listed_stays_listed_and_an_event_says_before_and_after(api):
    tid, _, _ = bank(api, None)
    assert get(api, f"api-tools/{tid}", "ben-test")["can_set_audience"] is True
    patch(api, tid, {"audience": {**ONLY, "people": ["cara"]}}, "ben-test")
    page = get(api, f"api-tools/{tid}", "ben-test")
    assert sorted(page["audience"]["people"]) == ["ben", "cara"]
    # Taking himself off puts him straight back: a non-owner never locks himself out.
    patch(api, tid, {"audience": {**ONLY, "people": ["cara"]}}, "ben-test")
    assert sorted(get(api, f"api-tools/{tid}", "ben-test")["audience"]["people"]) == ["ben", "cara"]
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT actor, detail FROM events WHERE action='tool.audience_changed' AND target=? ORDER BY rowid", (tid,)
        ).fetchall()
    assert len(rows) == 1 and rows[0][0] == "human:ben"
    change = json.loads(rows[0][1])
    assert change["before"] is None and sorted(change["after"]["people"]) == ["ben", "cara"]
    # A member may not change it; Cara is listed but no credential administrator.
    patch(api, tid, {"audience": None}, "cara-test", expected=403)
    assert get(api, f"api-tools/{tid}", "cara-test")["can_set_audience"] is False
    # Ana takes Ben off: now he gets 404 like any stranger, and cannot put it back.
    patch(api, tid, {"audience": {**ONLY, "people": ["cara"]}})
    get(api, f"api-tools/{tid}", "ben-test", expected=404)
    patch(api, tid, {"audience": None}, "ben-test", expected=404)
    patch(api, tid, {"audience": None})
    assert get(api, f"api-tools/{tid}", "ben-test")["private"] is False


def test_null_audience_keeps_the_old_rules(api):
    tid, rw, _ = bank(api, None)
    assert index(api, "ben-test")["api:" + tid]["private"] is False
    page = get(api, f"api-tools/{tid}", "ben-test")
    assert page["private"] is False and page["audience"] is None and page["seen_by"] == []
    assert rw in vault_ids(api, "ben-test")
    get(api, f"api-tools/{tid}", "cara-test", expected=404)  # a member with no grant still does not see it
    assert "api:" + tid not in index(api, "cara-test")


def test_a_bad_audience_is_refused_and_an_unreadable_stored_one_lists_nobody(api):
    tid, _, _ = bank(api, None)
    patch(api, tid, {"audience": {**ONLY, "people": ["nobody-here"]}}, expected=404)  # Unknown person
    patch(api, tid, {"audience": {"everyone": False, "people": "cara"}}, expected=422)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET audience='not json' WHERE id=?", (tid,))
    get(api, f"api-tools/{tid}", "ben-test", expected=404)
    assert get(api, f"api-tools/{tid}")["private"] is True


def test_adding_a_tool_or_storing_a_key_never_reveals_a_hidden_tools_id_or_fills_it(api):
    tid, _, _ = bank(api)
    assert tid == "acme-bank"
    mine = post(api, "api-tools", {"name": "Acme Bank"}, "ben-test")
    ours = post(api, "api-tools", {"name": "Acme Bank"})
    # A taken id takes a random suffix whoever sees the tool that has it: its shape never says a hidden one exists.
    for made in (mine["id"], ours["id"]):
        assert made.startswith("acme-bank-") and len(made) == len("acme-bank-") + 8
    assert mine["id"] != ours["id"]
    assert post(api, "api-tools", {"name": "Fresh Tool"}, "ben-test")["id"] == "fresh-tool"  # a free id is the name
    # A known service's key stored in the vault fills the first free tool of that service, never a private one.
    close = tool(api)["id"]
    patch(api, close, {"audience": ONLY})
    cid = post(
        api, "credentials", {"name": "Close API key", "env": "CLOSE_API_KEY", "secret": SECRET + "-close"}, "ben-test"
    )["id"]
    with api.app.state.store.read() as c:
        landed = c.execute("SELECT tool_id FROM credentials WHERE id=?", (cid,)).fetchone()[0]
        made = c.execute("SELECT name FROM tools WHERE id=?", (landed,)).fetchone()[0]
    assert landed != close and landed != "close-crm-2" and made == "Close CRM"
    assert "api:" + landed in index(api, "ben-test") and "api:" + close not in index(api, "ben-test")


def test_a_removed_teammate_leaves_every_tools_list(api):
    tid, _, _ = bank(api, {**ONLY, "people": ["cara", "eve"], "bots": ["ops"]})
    with api.app.state.store.transaction() as c:
        api_tools.forget_audience_member(c, "human:ana", "people", "cara")
        api_tools.forget_audience_member(c, "human:ana", "bots", "ops")
    assert get(api, f"api-tools/{tid}")["audience"] == {**ONLY, "people": ["eve"]}
    get(api, f"api-tools/{tid}", "cara-test", expected=404)


def test_the_chat_card_never_replaces_a_hidden_key_or_names_it(api):
    """Ben stores the same variable for a bot that already holds Ana's hidden key: without the rule his value would
    overwrite hers. It is refused without naming her key, and her value stays."""
    from backend import credential_cards

    tid, rw, _ = bank(api)
    level(api, tid, "bot:ops", "read_write")
    with api.app.state.store.read() as c:
        env = c.execute("SELECT env FROM credentials WHERE id=?", (rw,)).fetchone()[0]
    vault = api.app.state.vault
    with pytest.raises(Problem) as refused:
        with api.app.state.store.transaction() as c:
            ben = api.app.state.auth.identity_for_actor(c, "human:ben")
            credential_cards.store_for_bot(c, vault, ben, env, "ops", "ben-synthetic-value-0099887766")
    assert refused.value.status == 409 and "Close" not in refused.value.detail and "Acme" not in refused.value.detail
    assert post(api, f"credentials/{rw}/reveal", {})["value"] == SECRET


def test_support_staff_never_see_a_private_tool(api):
    tid, _, _ = bank(api)
    with api.app.state.store.read() as c:
        ana = api.app.state.auth.identity_for_actor(c, "human:ana")
        support = type("Who", (), {"role": "human", "actor": "human:support", "support": "app"})()
        tool_row = c.execute("SELECT * FROM tools WHERE id=?", (tid,)).fetchone()
        assert api_tools.visible(c, ana, tool_row, api.app.state.vault.admins) is True
        assert api_tools.visible(c, support, tool_row, api.app.state.vault.admins) is False
        assert api_tools.may_set_audience(c, support, tool_row, api.app.state.vault.admins) is False


def test_level_writes_by_the_owner_still_work_on_a_private_tool(api):
    tid, _, ro = bank(api)
    put(api, f"api-tools/{tid}/access/human:cara", {"level": "read"})
    assert "api:" + tid in index(api, "cara-test")
    put(api, f"api-tools/{tid}/access/human:cara", {"level": "none"})
    get(api, f"api-tools/{tid}", "cara-test", expected=404)
    post(api, f"credentials/{ro}/reveal", {}, "cara-test", expected=404)


def ben_and_ana(api, c):
    auth = api.app.state.auth
    return auth.identity_for_actor(c, "human:ben"), auth.identity_for_actor(c, "human:ana")


def test_history_drops_a_hidden_keys_events_even_the_ones_the_reader_made(api):
    """Ben granted and revealed the key while its tool was Everyone; once Ana makes it Only listed, those rows are gone
    from his Log (not just unnamed), and still in hers."""
    tid, rw, _ = bank(api, None)
    post(api, f"credentials/{rw}/grants", {"subject": "bot:ops"}, "ben-test")
    post(api, f"credentials/{rw}/reveal", {}, "ben-test")

    def rows(token):
        page = get(api, "history?kind=credential.revealed,permission.changed", token)
        return {i["id"]: i for i in page["items"] if i["link"] == "#/tools/keys?show=credentials"}

    before = rows("ben-test")
    assert {i["kind"] for i in before.values()} == {"credential.revealed", "permission.changed"}
    patch(api, tid, {"audience": ONLY})
    assert not set(rows("ben-test")) & set(before)
    assert set(before) <= set(rows("ana-test"))


def test_a_new_credentials_name_never_steps_around_a_hidden_keys_name(api):
    """The chat card renames a new credential whose name is taken; a hidden key's name is not taken for Ben, so the name
    he gets is the one he asked for, as if the hidden key did not exist."""
    from backend import credential_cards

    bank(api)
    vault = api.app.state.vault
    post(api, "credentials", {"name": "Ben visible", "env": "BEN_VISIBLE_KEY", "secret": SECRET + "-v"}, "ben-test")
    with api.app.state.store.transaction() as c:
        ben, _ = ben_and_ana(api, c)
        hidden = credential_cards.store_for_bot(
            c, vault, ben, "ACME_OTHER_KEY", "ops", SECRET + "-a", name="Close read_write"
        )
        seen = credential_cards.store_for_bot(c, vault, ben, "ACME_THIRD_KEY", "ops", SECRET + "-b", name="Ben visible")
    assert hidden["name"] == "Close read_write"
    assert seen["name"] != "Ben visible"  # a name he can see is still stepped around, as before


def test_counsels_who_hint_never_counts_a_hidden_key(api):
    from backend import counsel

    tid, _, _ = bank(api)
    level(api, tid, "bot:ops", "read_write")
    with api.app.state.store.read() as c:
        ben, ana = ben_and_ana(api, c)
        auth = api.app.state.auth
        assert counsel.suggested_who(c, "Close", ben, auth)["bots"] == []
        assert counsel.suggested_who(c, "Close", ana, auth)["bots"] == ["ops"]


def test_copying_a_bot_never_reports_or_tries_a_hidden_key(api):
    """Ops holds the hidden key; Ben's copy of Ops is told the original has none (as if it did not exist), never
    "Credential not found"; Ana's copy gets it."""
    from backend import bot_copy

    tid, rw, _ = bank(api)
    level(api, tid, "bot:ops", "read_write")
    vault = api.app.state.vault
    with api.app.state.store.read() as c:
        env = c.execute("SELECT env FROM credentials WHERE id=?", (rw,)).fetchone()[0]
    tools = [{"env": env, "service": ""}]
    with api.app.state.store.transaction() as c:
        ben, ana = ben_and_ana(api, c)
        bens = bot_copy.credentials_for(c, vault, ben, "ops", "assistant", tools, [])
        anas = bot_copy.credentials_for(c, vault, ana, "ops", "assistant", tools, [])
    assert bens["granted"] == [] and bens["needs"][0]["detail"] == "The original has no stored credential for it."
    assert anas["granted"] == [{"env": env, "service": "", "credential": "Close read_write"}]


def test_a_grant_refused_by_a_hidden_holder_names_no_key_nor_its_variable(api):
    tid, rw, _ = bank(api)
    level(api, tid, "bot:ops", "read_write")
    with api.app.state.store.read() as c:
        env = c.execute("SELECT env FROM credentials WHERE id=?", (rw,)).fetchone()[0]
    mine = post(api, "credentials", {"name": "Ben bank", "env": env, "secret": SECRET + "-ben"}, "ben-test")["id"]
    r = api.post(f"/api/v2/credentials/{mine}/grants", json={"subject": "bot:ops"}, headers=headers("ben-test"))
    assert r.status_code == 409, r.text
    assert env not in r.text and "Close" not in r.text and "Acme" not in r.text and "another credential" not in r.text
    # Ana sees the key, so she is told which one.
    r = api.post(f"/api/v2/credentials/{mine}/grants", json={"subject": "bot:ops"}, headers=headers())
    assert r.status_code == 409 and "Close read_write" in r.text


def test_health_and_the_assistants_card_never_name_a_hidden_key(api):
    from backend import assistant, health

    tid, rw, _ = bank(api)
    level(api, tid, "bot:ops", "read_write")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credentials SET env='TICO_ACME_BANK' WHERE id=?", (rw,))  # a reserved name, after the grant
    admins = api.app.state.vault.admins
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        ben, ana = ben_and_ana(api, c)
        assert ("Close read_write", "TICO_ACME_BANK", "ops") in health.reserved_credentials(c, ana, admins, auth)
        assert not [r for r in health.reserved_credentials(c, ben, admins, auth) if r[0] == "Close read_write"]
        path = f"/api/v2/credentials/{rw}"
        assert "Close read_write" in assistant.describe(c, "DELETE", path, {}, ana, admins)[0]
        said = assistant.describe(c, "DELETE", path, {}, ben, admins)[0]
        assert "Close" not in said and rw in said


def sql_targets(api, token):
    r = api.post("/api/v2/sql", json={"sql": "SELECT target FROM events"}, headers=headers(token))
    assert r.status_code == 200, r.text
    return {row[0] for row in r.json()["rows"]}


def test_sql_events_follow_historys_rule_for_a_hidden_tool_and_a_key_deleted_from_it(api):
    """POST /api/v2/sql reads events as the Log does: once Ana makes the tool Only listed, Ben's own grant and reveal rows,
    and the rows about a key she deletes from it, are gone from his events, and still in hers."""
    tid, rw, ro = bank(api, None)
    post(api, f"credentials/{rw}/grants", {"subject": "bot:ops"}, "ben-test")
    post(api, f"credentials/{rw}/reveal", {}, "ben-test")
    post(api, f"credentials/{ro}/reveal", {}, "ben-test")
    assert {rw, ro} <= sql_targets(api, "ben-test")
    patch(api, tid, {"audience": ONLY})
    assert api.delete(f"/api/v2/api-tools/{tid}/keys/{ro}", headers=headers("ana-test")).status_code == 200
    assert not {tid, rw, ro} & sql_targets(api, "ben-test")
    assert {tid, rw, ro} <= sql_targets(api, "ana-test")


def test_history_names_a_deleted_key_to_whoever_could_see_it(api):
    """A key deleted from its tool or from the vault keeps its last name in the Log, not "A credential". A key of an
    Only listed tool deleted since is named to the owner alone."""
    tid, rw, ro = bank(api, None)
    for cid in (rw, ro):
        post(api, f"credentials/{cid}/reveal", {}, "ben-test")
    assert api.delete(f"/api/v2/api-tools/{tid}/keys/{rw}", headers=headers("ana-test")).status_code == 200
    assert api.delete(f"/api/v2/credentials/{ro}", headers=headers("ana-test")).status_code == 200
    payroll = tool(api, "Acme Payroll", base_url="https://api.payroll.acme.example", check_path="/v1/me")["id"]
    pay = post(
        api,
        f"api-tools/{payroll}/keys",
        {"name": "Payroll read", "value": "acme-payroll-synthetic-ro-0099887766", "access_level": "read"},
    )["id"]
    post(api, f"credentials/{pay}/reveal", {}, "ben-test")
    patch(api, payroll, {"audience": ONLY})
    assert api.delete(f"/api/v2/api-tools/{payroll}", headers=headers("ana-test")).status_code == 200

    def named(token):
        page = get(api, "history?kind=credential.revealed", token)
        return sorted(i["subject"] for i in page["items"])

    assert named("ana-test") == ["Close read", "Close read_write", "Payroll read"]
    assert named("ben-test") == ["A credential", "Close read", "Close read_write"]
