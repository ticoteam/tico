"""Who can see an API tool (backend/api_tools.py `visible`): Only listed hides a tool and its keys from everyone but the
owner, the list and whoever can use it, on every path: the tool index, the tool's page, its activity, check and writes,
the vault's list, reveal, change, grant, revoke and delete, the chat card, history, SQL, Add's id and a key stored
elsewhere. Not visible is a 404, never a 403. Everyone (NULL) keeps the rules from before.

Ana owns the company and Ben is an admin (both credential administrators); Cara and Eve are members."""

import json

import pytest

from backend import api_tools
from backend.store import Problem
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401  (fixture)
from backend.tests.test_api_tools import key, level, tool
from backend.tests.test_credentials import setup

SECRET = "acme-bank-synthetic-rw-0011223344"
SECRET_R = "acme-bank-synthetic-ro-5566778899"
ONLY = {"everyone": False, "people": [], "teams": [], "bots": []}


def patch(api, tid, body, token="ana-test", expected=200):
    r = api.patch(f"/api/v2/api-tools/{tid}", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def bank(api, audience=ONLY):
    """Ana's bank tool with a Read and write key and a Read key, Only listed (by default nobody but her)."""
    setup(api)
    tid = tool(api, "Acme Bank", base_url="https://api.bank.acme.example", check_path="/v1/me")["id"]
    rw = key(api, tid, SECRET)["id"]
    ro = key(api, tid, SECRET_R, "read")["id"]
    if audience is not None:
        patch(api, tid, {"audience": audience})
    return tid, rw, ro


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
            ("delete", f"api-tools/{tid}", None)):
        kw = {"json": body} if body is not None else {}
        r = getattr(api, method)("/api/v2/" + path, headers=headers("ben-test"), **kw)
        assert r.status_code == 404, (method, path, r.text)
        assert SECRET not in r.text and "Acme Bank" not in r.text
    # The vault: not listed, and every action on its keys is a 404, as for a credential that does not exist.
    assert not {rw, ro} & vault_ids(api, "ben-test")
    for path, body in ((f"credentials/{rw}/reveal", {}), (f"credentials/{rw}/grants", {"subject": "human:ben"}),
                       (f"credentials/{ro}/grants", {"subject": "bot:ops"}), (f"credentials/{rw}", {"name": "x"})):
        r = api.post("/api/v2/" + path, json=body, headers=headers("ben-test"))
        assert r.status_code == 404 and SECRET not in r.text and "Acme" not in r.text, (path, r.text)
    assert api.post(f"/api/v2/credentials/{rw}/grants/{grant}/revoke", json={}, headers=headers("ben-test")).status_code == 404
    assert api.delete(f"/api/v2/credentials/{rw}", headers=headers("ben-test")).status_code == 404
    # Nothing changed: the tool, both keys and their values are as Ana left them.
    page = get(api, f"api-tools/{tid}")
    assert page["tool"]["name"] == "Acme Bank" and len(page["keys"]) == 2 and page["private"] is True
    assert post(api, f"credentials/{rw}/reveal", {})["value"] == SECRET
    with api.app.state.store.read() as c:
        assert c.execute("SELECT revoked_at FROM credential_grants WHERE id=?", (grant,)).fetchone()[0] is None


def test_ben_cannot_find_it_through_sql_events_or_history(api):
    tid, rw, _ = bank(api)
    for sql in ("SELECT * FROM tools", "SELECT * FROM credentials", "SELECT * FROM tool_calls",
                "SELECT target_id, payload FROM events"):
        r = api.post("/api/v2/sql", json={"sql": sql}, headers=headers("ben-test"))
        assert tid not in r.text and rw not in r.text and "Acme Bank" not in r.text, (sql, r.text[:300])
    r = api.get("/api/v2/history", headers=headers("ben-test"))
    assert r.status_code == 200 and "Acme Bank" not in r.text and "Close read" not in r.text and rw not in r.text


def test_a_grant_holder_sees_it_and_the_proxy_key_works_for_them(api):
    tid, rw, ro = bank(api)
    level(api, tid, "human:cara", "read")
    level(api, tid, "bot:ops", "read_write")
    assert index(api, "cara-test")["api:" + tid]["private"] is True
    page = get(api, f"api-tools/{tid}", "cara-test")
    assert page["private"] is True and page["can_set_audience"] is False and "audience" not in page and "seen_by" not in page
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
    gid = post(api, "groups", {"name": "Treasury", "add": {"people": ["cara"]}})["id"]
    tid, _, _ = bank(api, {**ONLY, "teams": [gid], "bots": ["ops"]})
    assert "api:" + tid in index(api, "cara-test")
    get(api, f"api-tools/{tid}", "eve-test", expected=404)
    assert ("group:" + gid, "Listed", "Treasury") in [(r["subject"], r["why"], r["name"]) for r in get(api, f"api-tools/{tid}")["seen_by"]]
    with api.app.state.store.read() as c:
        admins = api.app.state.vault.admins
        ops = api.app.state.auth.identity_for_actor(c, "bot:ops")
        coo = api.app.state.auth.identity_for_actor(c, "bot:coo")
        assert api_tools.visible(c, ops, tid, admins) is True
        assert api_tools.visible(c, coo, tid, admins) is False


def test_the_owner_always_sees_it_and_may_change_it_without_being_listed(api):
    tid, _, _ = bank(api, {**ONLY, "people": ["cara"]})
    page = get(api, f"api-tools/{tid}")
    assert page["can_set_audience"] is True and page["audience"] == {**ONLY, "people": ["cara"]}
    assert [(r["subject"], r["why"]) for r in page["seen_by"]] == [("human:ana", "Owner"), ("human:cara", "Listed")]
    assert index(api, "ana-test")["api:" + tid]["private"] is True
    patch(api, tid, {"audience": ONLY})
    assert get(api, f"api-tools/{tid}")["audience"] == ONLY     # the owner is never added: always sees it


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
        rows = c.execute("SELECT actor_id, payload FROM events WHERE kind='tool.audience_changed' AND target_id=? "
                         "ORDER BY rowid", (tid,)).fetchall()
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
    get(api, f"api-tools/{tid}", "cara-test", expected=404)    # a member with no grant still does not see it
    assert "api:" + tid not in index(api, "cara-test")


def test_a_bad_audience_is_refused_and_an_unreadable_stored_one_lists_nobody(api):
    tid, _, _ = bank(api, None)
    patch(api, tid, {"audience": {**ONLY, "people": ["nobody-here"]}}, expected=422)
    patch(api, tid, {"audience": {"everyone": False, "people": "cara"}}, expected=422)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tools SET audience='not json' WHERE id=?", (tid,))
    get(api, f"api-tools/{tid}", "ben-test", expected=404)
    assert get(api, f"api-tools/{tid}")["private"] is True


def test_the_finance_row_shows_in_who_can_use_it_and_seen_by(api):
    tid, rw, ro = bank(api)
    with api.app.state.store.transaction() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS fin_connections (
          id TEXT PRIMARY KEY, provider TEXT NOT NULL,
          purpose TEXT NOT NULL CHECK (purpose IN ('bank','cards','books','payments')), credential_ref TEXT,
          status TEXT NOT NULL DEFAULT 'new', cursor TEXT, last_ok_at TEXT, last_error TEXT,
          created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, name TEXT NOT NULL DEFAULT '',
          settings TEXT NOT NULL DEFAULT '{}', events_cursor TEXT, last_sync_at TEXT, next_sync_at TEXT)""")
        c.execute("INSERT INTO fin_connections(id,provider,purpose,credential_ref,status,created_at,created_by,last_sync_at) "
                  "VALUES('fc1','acme-bank','bank',?,'active','2026-11-01T00:00:00Z','human:ana','2026-11-05T10:00:00Z')", (ro,))
    page = get(api, f"api-tools/{tid}")
    fin = [a for a in page["access"] if a["kind"] == "finance"]
    assert fin == [{"subject": "finance", "kind": "finance", "id": "finance", "name": "Finance", "initials": "F",
                    "level": "read", "last_used_at": "2026-11-05T10:00:00Z", "fixed": True}]
    assert ("finance", "Can use it") in [(r["subject"], r["why"]) for r in page["seen_by"]]
    assert "finance" not in json.dumps(page["audience"])          # read-only: never an audience member
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE fin_connections SET credential_ref=? WHERE id='fc1'", (rw,))
    assert [a["level"] for a in get(api, f"api-tools/{tid}")["access"] if a["kind"] == "finance"] == ["read_write"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE fin_connections SET status='disconnected' WHERE id='fc1'")
    assert not [a for a in get(api, f"api-tools/{tid}")["access"] if a["kind"] == "finance"]


def test_adding_a_tool_or_storing_a_key_never_reveals_a_hidden_tools_id_or_fills_it(api):
    tid, _, _ = bank(api)
    assert tid == "acme-bank"
    mine = post(api, "api-tools", {"name": "Acme Bank"}, "ben-test")
    assert mine["id"].startswith("acme-bank-") and mine["id"] != "acme-bank-2"
    assert post(api, "api-tools", {"name": "Acme Bank"})["id"] == "acme-bank-2"   # Ana sees hers: counted as before
    # A known service's key stored in the vault fills the first free tool of that service, never a private one.
    close = tool(api)["id"]
    patch(api, close, {"audience": ONLY})
    cid = post(api, "credentials", {"name": "Close API key", "env": "CLOSE_API_KEY", "secret": SECRET + "-close"},
               "ben-test")["id"]
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
