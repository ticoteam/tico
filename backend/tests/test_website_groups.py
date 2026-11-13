"""Website logins shared with a group (#398): every member, nested groups included, uses the login through the same
group resolution API tools use for Who can see it; only a credential administrator grants or removes a group."""

import json

import pytest

from backend import bot_settings as BS
from backend import website_tools as W
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.credentials import create, setup

SITE = "crm.acme.example"


@pytest.fixture
def s(api, monkeypatch):
    """A website tool with one shared login; Design (Cara) with Design bots (ops) nested under it, and Empty."""
    setup(api)
    closed: list[str] = []
    monkeypatch.setattr(W, "_close", closed.append)
    tid = post(api, "website-tools", {"name": "CRM", "sites": [SITE]})["id"]
    aid = post(api, f"website-tools/{tid}/accounts", {"username": "", "password": "synthetic"})["id"]
    roster(
        api,
        {
            "design": {"name": "Design"},
            "design-bots": {"name": "Design bots", "parent": "design"},
            "empty": {"name": "Empty"},
        },
        cara="design",
    )
    team(api, "ops", "design-bots")
    return type("S", (), {"api": api, "tid": tid, "aid": aid, "closed": closed, "store": api.app.state.store})


def team(api, slug, gid):
    """Put a bot in a group, as Settings does: its declared `team`."""
    with api.app.state.store.transaction() as c:
        bot = H.resolve_bot(c, slug)["id"]
        declared = json.loads(c.execute(BS.ONE_SQL, (bot,)).fetchone()[0] or "{}")
        BS.write(c, bot, {**declared, "team": gid})


def bot(api, slug):
    with api.app.state.store.read() as c:
        return "bot:" + H.resolve_bot(c, slug)["id"]


def roster(api, groups=None, **teams):
    with api.app.state.store.transaction() as c:
        doc = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        if groups is not None:
            doc["org_groups"] = groups
        for person in doc["people"]:
            if person["id"] in teams:
                person["team"] = teams[person["id"]]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(doc),))


def share(s, subject, on=True, token="ana-test", expected=200):
    path = f"website-tools/{s.tid}/accounts/{s.aid}/access/{subject}"
    return put(s.api, path, {"on": on}, token, expected=expected)


def pick(s, actor):
    with s.store.read() as c:
        return W.pick(c, Identity(actor, "bot" if actor.startswith("bot:") else "human"), SITE)


def refused(s, actor):
    with pytest.raises(Problem) as found:
        pick(s, actor)
    return (found.value.status, found.value.code)


def access(s, token="ana-test"):
    tool = get(s.api, f"website-tools/{s.tid}", token=token)
    return next(a for a in tool["accounts"] if a["id"] == s.aid)["access"]


def tool_rows(s, token="ana-test"):
    return get(s.api, "tool-index", token=token)["rows"]


def rows(s):
    with s.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT subject,revoked_at FROM credential_grants WHERE credential_id=? ORDER BY created_at", (s.aid,)
            )
        ]


def test_a_group_grant_signs_in_every_member_nested_included_and_removing_it_ends_it(s):
    """AC-8, AC-9 and the guard: Design's person and its nested group's bot sign in; nobody else does; removing
    Design refuses them at the next use and closes the account's sessions."""
    assert refused(s, "human:cara") == (403, "no_account")
    answer = share(s, "group:design")
    assert (answer["subject"], answer["on"]) == ("group:design", True)
    assert pick(s, "human:cara")["account_id"] == s.aid
    assert pick(s, bot(s.api, "ops"))["account_id"] == s.aid
    for outsider in ("human:ben", bot(s.api, "finance")):
        assert refused(s, outsider) == (403, "no_account")
    assert share(s, "group:design", on=False)["on"] is False
    assert refused(s, "human:cara") == (403, "no_account")
    assert refused(s, bot(s.api, "ops")) == (403, "no_account")
    assert s.closed == [s.aid]


def test_a_member_added_after_the_grant_uses_it_and_opens_an_only_listed_tool(s):
    """AC-10: Ben's audience does not list Cara; she joins Design after Design was granted and needs no grant."""
    roster(s.api, cara="")
    audience = {"audience": {"everyone": False, "people": [], "teams": [], "bots": []}}
    r = s.api.patch(f"/api/v2/website-tools/{s.tid}", json=audience, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    share(s, "group:design")
    get(s.api, f"website-tools/{s.tid}", token="cara-test", expected=404)
    roster(s.api, cara="design")
    tool = get(s.api, f"website-tools/{s.tid}", token="cara-test")
    assert [a["can_use"] for a in tool["accounts"]] == [True]
    assert any(r["id"] == "website:" + s.tid for r in tool_rows(s, "cara-test"))
    assert pick(s, "human:cara")["account_id"] == s.aid
    seen = {r["subject"]: r["why"] for r in tool.get("seen_by") or get(s.api, f"website-tools/{s.tid}")["seen_by"]}
    assert seen["group:design"] == "Can use it"


def test_rows_name_groups_first_with_members_and_a_direct_grant_also_in_a_group_shows_once(s):
    """AC-1, AC-11, AC-11b, AC-11c: groups first with their member count; Cara direct and in Design shows once with
    via; the answer to each change is the full list, so via goes when Design goes."""
    roster(s.api, ben="design")
    share(s, bot(s.api, "ops"))
    answer = share(s, "human:cara")
    assert [w["subject"] for w in answer["access"]] == ["human:cara", bot(s.api, "ops")]
    assert "via" not in answer["access"][0]
    added = share(s, "group:design")["access"]
    assert [w["subject"] for w in added] == ["group:design", "human:cara", bot(s.api, "ops")]
    group = added[0]
    assert (group["kind"], group["id"], group["name"], group["members"]) == ("group", "design", "Design", 3)
    assert added[1]["via"] == ["Design"] and added[2]["via"] == ["Design"]
    share(s, "group:empty")
    listed = access(s)
    assert [(w["subject"], w.get("members")) for w in listed[:2]] == [("group:design", 3), ("group:empty", 0)]
    assert listed[2]["via"] == ["Design"]
    after = share(s, "group:design", on=False)["access"]
    assert [w["subject"] for w in after] == ["group:empty", "human:cara", bot(s.api, "ops")]
    assert "via" not in after[1]
    # Every grant made before stays as it was.
    assert pick(s, "human:cara")["account_id"] == s.aid


def test_two_granted_groups_both_name_a_direct_grant(s):
    roster(
        s.api, {"design": {"name": "Design"}, "eng": {"name": "Engineering"}, "web": {"name": "Web", "parent": "eng"}}
    )
    roster(s.api, cara="web")
    share(s, "human:cara")
    team(s.api, "ops", "design")
    share(s, "group:design")
    share(s, "group:eng")
    listed = access(s)
    cara = next(w for w in listed if w["subject"] == "human:cara")
    assert cara["via"] == ["Engineering"]
    roster(
        s.api,
        {
            "design": {"name": "Design"},
            "eng": {"name": "Engineering", "parent": "design"},
            "web": {"name": "Web", "parent": "eng"},
        },
    )
    cara = next(w for w in access(s) if w["subject"] == "human:cara")
    assert cara["via"] == ["Design", "Engineering"]


def test_a_member_never_sees_group_rows_and_only_an_admin_grants_or_removes_a_group(s):
    """AC-12: Cara (not a credential administrator) gets 403 either way and no grant row changes; a member's view
    lists only themselves."""
    share(s, "human:cara")
    share(s, "group:empty")
    before = rows(s)
    for on in (True, False):
        for gid in ("design", "empty"):
            body = share(s, f"group:{gid}", on=on, token="cara-test", expected=403)
            assert body["error"]["code"] == "forbidden"
    assert rows(s) == before
    assert [w["subject"] for w in access(s, "cara-test")] == ["human:cara"]
    assert "via" not in access(s, "cara-test")[0]


@pytest.mark.parametrize("subject", ["group:missing", "group:", "team:design"])
def test_a_group_that_does_not_exist_is_refused(s, subject):
    body = share(s, subject, expected=422)
    assert body["error"]["code"] == "subject"
    assert rows(s) == []


def test_one_who_manages_a_bot_with_the_login_through_a_group_reaches_its_browser_as_with_a_direct_grant(s):
    """AC-12b: Cara manages ops, which has the login only through Design bots."""
    roster(s.api, cara="")
    share(s, "group:design-bots")
    with s.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='cara' WHERE bot=?", (bot(s.api, "ops")[4:],))
    cara = Identity("human:cara", "human")
    with s.store.read() as c:
        assert W._bot_user_managed(c, cara, W._account(c, s.tid, s.aid))
        assert not W._bot_user_managed(c, Identity("human:eve", "human"), W._account(c, s.tid, s.aid))


def test_deleting_a_group_revokes_its_login_grants_like_a_removal(s):
    """AC-13: the grant is revoked with a credential.revoked event, sessions close, a former member is refused, and a
    new group with the same id has no login."""
    share(s, "group:design-bots")
    assert pick(s, bot(s.api, "ops"))["account_id"] == s.aid
    r = s.api.delete("/api/v2/groups/design-bots", headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    assert [(g["subject"], bool(g["revoked_at"])) for g in rows(s)] == [("group:design-bots", True)]
    with s.store.read() as c:
        events = c.execute(
            "SELECT detail FROM events WHERE action='credential.revoked' AND target=?", (s.aid,)
        ).fetchall()
    assert [json.loads(e["detail"])["subject"] for e in events] == ["group:design-bots"]
    assert s.closed == [s.aid]
    assert refused(s, bot(s.api, "ops")) == (403, "no_account")
    assert all(not g.get("subject", "").startswith("group:") for g in access(s))
    roster(s.api, {"design": {"name": "Design"}, "design-bots": {"name": "Design bots", "parent": "design"}})
    team(s.api, "ops", "design-bots")
    assert refused(s, bot(s.api, "ops")) == (403, "no_account")


def test_an_api_key_never_goes_to_a_group(s):
    """AC-14: group grants are for website logins only."""
    key = create(s.api)["id"]
    r = s.api.post(f"/api/v2/credentials/{key}/grants", json={"subject": "group:design"}, headers=headers("ana-test"))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "subject"


def test_the_groups_read_counts_members_for_credential_administrators_only(s):
    found = {g["id"]: (g["name"], g["members"]) for g in get(s.api, "website-tools/groups")}
    assert found == {"design": ("Design", 2), "design-bots": ("Design bots", 1), "empty": ("Empty", 0)}
    get(s.api, "website-tools/groups", token="cara-test", expected=403)


def test_the_tools_list_shows_a_granted_group_as_a_face(s):
    """Faces elsewhere: the Tools row names the group with its member count."""
    share(s, "group:design")
    row = next(r for r in tool_rows(s) if r["id"] == "website:" + s.tid)
    assert {k: row["who"][0][k] for k in ("kind", "id", "name", "members")} == {
        "kind": "group",
        "id": "design",
        "name": "Design",
        "members": 2,
    }


def test_group_answers_are_the_declared_ones(s):
    from backend.tests.helpers.openapi_v2 import conforms

    document = s.api.get("/api/v2/openapi.json", headers=headers()).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def declared(operation, answer):
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(answer, schema, document) is None, (operation, conforms(answer, schema, document), answer)

    share(s, "human:cara")
    declared("setWebsiteAccountAccess", share(s, "group:design"))
    declared("listWebsiteToolGroups", get(s.api, "website-tools/groups"))
    declared("getWebsiteTool", get(s.api, f"website-tools/{s.tid}"))
