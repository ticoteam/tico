"""See, Read, Write and Manage on people and groups, and bots in groups (backend/teammate_access.py).

Acme's Legal group holds Cara, with a Contracts group nested under it holding Fay and the Counsel bot. Cara reports to
Dee. Eve is a peer in Sales. Ana owns the company and Ben is an admin. Each test narrows someone's audiences and asks
every place that shows people the same question.
"""

import pytest

from backend import teammate_access as TA
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (fixture)
from backend.tests.test_bot_access import bot

EVERYONE = {"everyone": True}
NOBODY = {"everyone": False}


@pytest.fixture
def acme(api):
    store = api.app.state.store
    store.settings.test_identities["dee-test"] = Identity("human:dee", "human", "dee@acme.example")
    with store.transaction() as c:
        people = [{"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
                  {"id": "ben", "email": "ben@acme.example"},
                  {"id": "cara", "email": "cara@acme.example", "reports_to": "dee"},
                  {"id": "dee", "email": "dee@acme.example"},
                  {"id": "eve", "email": "eve@acme.example"},
                  {"id": "fay", "email": "fay@acme.example"}]
        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode({"people": people}),))
        H.sync_registry(c, {}, {"people": people})
    bot(api, "counsel")
    bot(api, "analyst")
    post(api, "groups", {"name": "Legal", "add": {"people": ["cara"]}})
    post(api, "groups", {"name": "Contracts", "parent": "legal", "add": {"people": ["fay"], "bots": ["counsel"]}})
    post(api, "groups", {"name": "Sales", "add": {"people": ["eve"]}})
    tokens = {p: p + "-test" for p in ("ana", "ben", "cara", "dee", "eve", "fay")}
    tokens["counsel"] = post(api, "bots/counsel/agent-credential", {})["token"]
    return api, tokens


def who(api, name):
    if name in ("counsel", "analyst"):
        return Identity("bot:" + name, "bot")
    return api.app.state.store.settings.test_identities[name + "-test"]


def levels(api, viewer, target):
    store = api.app.state.store
    with store.read() as c:
        return TA.levels_for(c, who(api, viewer), target, api.app.state.auth)


def set_access(api, path, token="ana-test", expected=200, **levels_):
    current = api.get(f"/api/v2/{path}/access", headers=headers(token))
    revision = current.json()["revision"] if current.status_code == 200 else 0
    body = {"see": EVERYONE, "read": EVERYONE, "write": EVERYONE, "revision": revision, **levels_}
    r = api.put(f"/api/v2/{path}/access", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def sql_column(api, sql, token):
    r = api.post("/api/v2/sql", json={"sql": sql}, headers=headers(token))
    assert r.status_code == 200, r.text
    return sorted(row[0] for row in r.json()["rows"])


def test_people_start_open_and_groups_write_to_their_members(acme):
    api, tokens = acme
    view = get(api, "humans/cara/access", "cara-test")
    assert view["revision"] == 0 and all(view[level]["everyone"] for level in ("see", "read", "write"))
    assert levels(api, "eve", "human:cara") == {"see": True, "read": True, "write": True, "manage": False}
    assert levels(api, "counsel", "human:cara")["write"]
    # A group: See and Read Everyone, Write its members, the groups under it included, people and bots alike.
    assert levels(api, "eve", "group:legal") == {"see": True, "read": True, "write": False, "manage": False}
    assert levels(api, "fay", "group:legal")["write"] and levels(api, "cara", "group:legal")["write"]
    assert levels(api, "counsel", "group:legal")["write"]
    assert not levels(api, "analyst", "group:legal")["write"]


def test_manage_is_the_person_their_managers_and_admins_never_a_peer(acme):
    api, tokens = acme
    for name in ("cara", "dee", "ben", "ana"):
        assert levels(api, name, "human:cara")["manage"], name
    for name in ("eve", "fay"):
        assert not levels(api, name, "human:cara")["manage"], name
    assert api.get("/api/v2/humans/cara/access", headers=headers("eve-test")).status_code == 403
    set_access(api, "humans/cara", token="eve-test", expected=403, read=NOBODY)
    saved = set_access(api, "humans/cara", token="dee-test", read={"people": ["fay"]})
    assert saved["revision"] == 1 and saved["read"]["people"] == ["fay"]
    with api.app.state.store.read() as c:
        audit = c.execute("SELECT actor FROM audit_log WHERE object='human:cara:access'").fetchall()
        event = c.execute("SELECT 1 FROM events WHERE action='person.access_changed' AND target='cara'").fetchone()
    assert [row["actor"] for row in audit] == ["human:dee"] and event
    # A stale revision is refused, and so is saving what is already there.
    stale = {"see": EVERYONE, "read": EVERYONE, "write": EVERYONE, "revision": 0}
    assert api.put("/api/v2/humans/cara/access", json=stale, headers=headers("cara-test")).status_code == 409


def test_group_leads_manage_their_groups_and_those_below(acme):
    api, tokens = acme
    assert not levels(api, "cara", "group:contracts")["manage"]
    api.patch("/api/v2/groups/legal", json={"lead": "person:cara"}, headers=headers("ana-test"))
    assert levels(api, "cara", "group:legal")["manage"] and levels(api, "cara", "group:contracts")["manage"]
    assert not levels(api, "fay", "group:legal")["manage"]
    set_access(api, "groups/contracts", token="cara-test", see={"teams": ["legal"]})
    set_access(api, "groups/contracts", token="fay-test", expected=403, see=EVERYONE)


def test_manage_carries_read_and_write_without_read_is_requests_only(acme):
    api, tokens = acme
    set_access(api, "humans/cara", token="cara-test", read=NOBODY)
    assert levels(api, "eve", "human:cara") == {"see": True, "read": False, "write": True, "manage": False}
    assert levels(api, "dee", "human:cara") == TA.FULL      # her manager, whatever the audiences say
    assert levels(api, "ben", "human:cara") == TA.FULL      # an admin


def test_a_bot_in_a_nested_group_matches_a_group_audience(acme):
    api, tokens = acme
    store, auth = api.app.state.store, api.app.state.auth
    # A bot's audience naming Legal: Counsel, in Contracts under Legal, matches; Analyst, in no group, does not.
    current = get(api, "bots/analyst/access")
    r = api.put("/api/v2/bots/analyst/access", headers=headers(), json={
        "see": EVERYONE, "read": {"teams": ["legal"]}, "write": {"teams": ["legal"]}, "revision": current["revision"]})
    assert r.status_code == 200, r.text
    with store.read() as c:
        assert auth.bot_access(c, who(api, "counsel"), "analyst") == {"see": True, "read": True, "write": True}
        assert auth.bot_access(c, who(api, "eve"), "analyst") == {"see": True, "read": False, "write": False}
        assert TA.group_member_actors(c, "legal") == {"human:cara", "human:fay", "bot:counsel"}
        assert TA.readers(c, "bot:analyst", "read", auth) >= {"bot:counsel", "human:cara", "human:fay"}
        assert "human:eve" not in TA.readers(c, "bot:analyst", "read", auth)
    # The same for a person's audience. Moving the bot out of the group takes what it gave away, at once.
    set_access(api, "humans/cara", token="cara-test", write={"teams": ["legal"]})
    assert levels(api, "counsel", "human:cara")["write"] and not levels(api, "analyst", "human:cara")["write"]
    api.patch("/api/v2/groups/contracts", json={"remove": {"bots": ["counsel"]}}, headers=headers("ana-test"))
    assert not levels(api, "counsel", "human:cara")["write"]


def test_a_bot_may_answer_a_person_who_narrowed_write(acme):
    api, tokens = acme
    set_access(api, "humans/cara", token="cara-test", write={"people": ["dee"]})
    assert not levels(api, "counsel", "human:cara")["write"]
    post(api, "chat/counsel", {"text": "Can you check this NDA?"}, token="cara-test")
    with api.app.state.store.read() as c:
        assert TA.may_reply(c, who(api, "counsel"), "human:cara")
        assert not TA.may_reply(c, who(api, "analyst"), "human:cara")
    assert levels(api, "counsel", "human:cara")["write"]


def test_see_hides_a_person_everywhere_people_are_shown(acme):
    """The privacy matrix for people: the chart, lists, the @mention roster, person reads and hub sql."""
    api, tokens = acme
    set_access(api, "humans/cara", token="cara-test", see={"teams": ["legal"]}, read={"teams": ["legal"]},
               write={"teams": ["legal"]})
    seen_by = {"ana": True, "ben": True, "dee": True, "fay": True, "cara": True, "eve": False}
    for name, sees in seen_by.items():
        token = tokens[name]
        assert any(p["id"] == "cara" for p in get(api, "org", token)["people"]) is sees, name
        people = api.get("/api/humans", headers=headers(token)).json()
        assert any(p["id"] == "cara" for p in people["people"]) is sees, name
        assert ("cara" in sum(people["by_team"].values(), [])) is sees, name
        legal = next(g for g in get(api, "groups", token) if g["id"] == "legal")
        assert ("cara" in legal["people"]) is sees, name
        photo = api.get("/api/humans/cara/photo", headers=headers(token))
        assert ("Person not found" in photo.text) is not sees, name      # else "No photo": she has none
        assert ("cara" in sql_column(api, "SELECT id FROM humans", token)) is sees, name
    assert levels(api, "counsel", "human:cara")["see"]          # a bot in a group under Legal
    assert not levels(api, "analyst", "human:cara")["see"]
    assert [p["id"] for p in get(api, "org", "eve-test")["people"] if p.get("reports_to") == "cara"] == []


def test_hub_sql_hides_unseen_people_in_participants_and_authors(acme):
    api, tokens = acme
    task = post(api, "tasks", {"owner": "counsel", "title": "Review the lease", "body": "Please."}, token="ana-test")
    post(api, f"tasks/{task['id']}/comments", {"text": "Clause 4 looks odd."}, token="cara-test")
    conversation = get(api, "tasks/" + task["id"], "eve-test").get("conversation_id") or task.get("conversation_id")
    sql = f"SELECT from_actor FROM messages WHERE conversation_id='{conversation}'"
    before = sql_column(api, sql, "eve-test")
    assert "human:cara" in before
    set_access(api, "humans/cara", token="cara-test", see={"teams": ["legal"]}, read={"teams": ["legal"]},
               write={"teams": ["legal"]})
    # Her message stays (it is readable); only its author is not named, as REST and the stream say it.
    after = sql_column(api, sql, "eve-test")
    assert len(after) == len(before) and "human:cara" not in after and "" in after
    assert "human:cara" in sql_column(api, sql, "fay-test")
    rows = sql_column(api, f"SELECT participants_json FROM conversations WHERE id='{conversation}'", "eve-test")
    assert all("human:cara" not in (row or "") for row in rows)


def test_see_hides_a_group_from_lists_the_chart_and_its_page(acme):
    api, tokens = acme
    set_access(api, "groups/legal", see={"teams": ["legal"]}, read={"teams": ["legal"]}, write={"teams": ["legal"]})
    for name, sees in {"ana": True, "ben": True, "cara": True, "fay": True, "eve": False, "dee": False}.items():
        token = tokens[name]
        ids = [g["id"] for g in get(api, "groups", token)]
        assert ("legal" in ids) is sees, name
        assert "contracts" in ids, name                         # its sub-group keeps its own default
        contracts = next(g for g in get(api, "groups", token) if g["id"] == "contracts")
        assert contracts["parent"] == ("legal" if sees else ""), name
        chart = get(api, "org", token)
        assert any(g["id"] == "legal" for g in chart["org_groups"]) is sees, name
        assert all(not str(row.get("org_parent")).endswith(":legal") for row in chart["people"] + chart["bots"]) or sees
        people = api.get("/api/humans", headers=headers(token)).json()
        assert any(g["id"] == "legal" for g in people["org_groups"]) is sees, name
        teams_json = sql_column(api, "SELECT teams_json FROM humans WHERE id='cara'", token)
        assert any("legal" in (row or "") for row in teams_json) is sees, name
    with api.app.state.store.read() as c:
        assert TA.unseen(c, who(api, "eve"), api.app.state.auth) == (set(), {"legal"})
        assert TA.unseen(c, who(api, "counsel"), api.app.state.auth) == (set(), set())


def test_sql_fragment_and_reader_sets_agree_with_levels(acme):
    api, tokens = acme
    set_access(api, "humans/cara", token="cara-test", see={"people": ["fay"]}, read={"people": ["fay"]},
               write={"people": ["fay"]})
    store, auth = api.app.state.store, api.app.state.auth
    with store.read() as c:
        assert TA.readers(c, "human:eve", "read", auth) == "everyone"
        cara = TA.readers(c, "human:cara", "see", auth)
        assert cara == {"human:ana", "human:ben", "human:cara", "human:dee", "human:fay"}
        fragment = TA.sql_visible(c, who(api, "eve"), "from_actor", kinds=("human",), auth=auth)
        assert fragment == "coalesce(from_actor,'') NOT IN ('human:cara')"
        assert TA.sql_visible(c, who(api, "fay"), "from_actor", kinds=("human",), auth=auth) == "1"
        assert TA.sql_visible(c, who(api, "eve"), "id", bare="human", auth=auth) == "coalesce(id,'') NOT IN ('cara')"


def test_the_tools_and_the_cli_set_access_as_whoever_asked(acme):
    from backend.tests.test_automation_cli import Client
    from backend.tests.test_mcp import call as mcp
    from clients import hubcli, remotecli

    api, tokens = acme
    err, view = mcp(api, "hub_human_access", {"human": "cara", "see": "dee,group:legal,bot:counsel"}, "cara-test")
    assert not err, view
    assert view["see"]["people"] == ["dee"] and view["see"]["teams"] == ["legal"] and view["see"]["bots"] == ["counsel"]
    assert view["read"]["everyone"] and view["write"]["everyone"]          # left out: unchanged
    err, _ = mcp(api, "hub_human_access", {"human": "cara", "see": "everyone"}, "eve-test")
    assert err                                                             # a peer may not
    err, view = mcp(api, "hub_group_access", {"group": "legal", "read": "group:legal"}, "ana-test")
    assert not err and view["read"]["teams"] == ["legal"] and view["see"]["everyone"]
    shown = remotecli.via_tool(Client(api, "dee-test"), hubcli.parser().parse_args(["human", "access", "cara"]))
    assert shown["see"]["people"] == ["dee"] and shown["you"]["manage"]
    changed = remotecli.via_tool(Client(api, "dee-test"), hubcli.parser().parse_args(
        ["human", "access", "cara", "--write", "group:legal"]))
    assert changed["write"]["teams"] == ["legal"]
    assert not levels(api, "eve", "human:cara")["write"]
    group = remotecli.via_tool(Client(api, "ana-test"), hubcli.parser().parse_args(["group", "access", "sales"]))
    assert group["write"]["teams"] == ["sales"]


def test_botops_sets_access_with_the_rights_of_the_person_who_asked(acme):
    from backend.tests.test_botops_parity import act
    from backend.tests.test_member_bots import botops as make_botops, finish, turn

    api, tokens = acme
    machine = make_botops.__wrapped__(api)
    body = {"see": EVERYONE, "read": NOBODY, "write": EVERYONE}
    eve = turn(api, machine, person="eve-test", text="Hide Cara's Home")
    assert act(api, eve, "GET", "humans/cara/access").status_code == 403    # only for whoever manages Cara
    revision = 0                                                            # never set: the defaults
    assert act(api, eve, "PUT", "humans/cara/access", {**body, "revision": revision}).status_code == 403
    finish(api, machine, eve)
    dee = turn(api, machine, person="dee-test", text="Hide Cara's Home")
    changed = act(api, dee, "PUT", "humans/cara/access", {**body, "revision": revision})
    assert changed.status_code == 200, changed.text
    with api.app.state.store.read() as c:
        row = c.execute("SELECT actor,detail_json FROM events WHERE action='person.access_changed' ORDER BY rowid DESC").fetchone()
        assert row["actor"] == "human:dee" and '"via": "botops"' in row["detail_json"]


def test_hiding_reaches_task_columns_events_the_stream_and_bot_rows(acme):
    from backend import events as E

    api, tokens = acme
    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='counsel'")
    asked = post(api, "tasks", {"owner": "counsel", "title": "Review the lease", "body": "Please."}, token="cara-test")
    to_eve = post(api, "tasks", {"owner": "human:eve", "title": "Send the signed copy", "body": "Thanks."},
                  token="cara-test")
    requesters = f"SELECT id, requester FROM tasks WHERE id IN ('{asked['id']}','{to_eve['id']}') ORDER BY id"
    actors = f"SELECT actor FROM events WHERE action='task.create' AND target='{asked['id']}'"
    assert sql_column(api, actors, "eve-test") == ["human:cara"]
    set_access(api, "humans/cara", token="cara-test", see={"teams": ["legal"]}, read={"teams": ["legal"]},
               write={"teams": ["legal"]})

    def requester_of(token):
        r = api.post("/api/v2/sql", json={"sql": requesters}, headers=headers(token))
        assert r.status_code == 200, r.text
        return {row[0]: row[1] for row in r.json()["rows"]}
    for name, sees in {"eve": False, "fay": True, "dee": True, "ben": True, "cara": True}.items():
        shown = requester_of(tokens[name])
        assert (shown[asked["id"]] == "human:cara") is sees, name
        if name in ("fay", "dee", "ben"):
            assert sql_column(api, actors, tokens[name]) == ["human:cara"], name
    # A task Cara asked Eve for: Eve is a party to it, so Cara stays known there.
    assert requester_of("eve-test")[to_eve["id"]] == "human:cara"
    assert sql_column(api, actors, "eve-test") == [""]
    # The stream: Cara's name leaves events Eve reads, except on a task Eve is a party to.
    with store.read() as c:
        view = E._Viewer(c, auth, who(api, "eve"))
        assert view.hidden_actor("human:cara", {"topic": "tasks", "subject_id": asked["id"], "conversation_id": None})
        assert not view.hidden_actor("human:cara", {"topic": "tasks", "subject_id": to_eve["id"], "conversation_id": None})
        assert not view.hidden_actor("human:fay", {"topic": "tasks", "subject_id": asked["id"], "conversation_id": None})
        assert not E._Viewer(c, auth, who(api, "fay")).hidden_actor("human:cara", None)
    # Bot rows: Cara runs Counsel, but Eve's /api/employees does not name her.
    for name, sees in {"eve": False, "fay": True}.items():
        row = next(e for e in api.get("/api/employees", headers=headers(tokens[name])).json() if e["name"] == "counsel")
        assert (row.get("operator") == "cara") is sees, name
        assert any(o["id"] == "cara" for o in row.get("bot_owners") or []) is sees, name


def test_a_team_page_is_seen_by_its_groups_read_and_old_audiences_move_there(acme):
    api, tokens = acme
    store = api.app.state.store
    # "Seen by" on the team page sets the group's Read; See (the chart) and Write (the members) stay.
    api.put("/api/v2/places/team:sales", json={}, headers=headers("ana-test"))
    r = api.put("/api/v2/places/team:sales", json={"audience": {"everyone": False, "people": ["dee"]}},
                headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    assert r.json()["audience"]["people"] == ["dee"]
    doc = get(api, "groups/sales/access", "ana-test")
    assert doc["see"]["everyone"] and doc["read"]["people"] == ["dee"] and doc["write"]["teams"] == ["sales"]
    assert any(g["id"] == "sales" for g in get(api, "groups", "fay-test"))     # still on everyone's chart
    assert api.get("/api/v2/places/team:sales", headers=headers("dee-test")).status_code == 200
    assert api.get("/api/v2/places/team:sales", headers=headers("fay-test")).status_code in (403, 404)
    # An install from before: a narrowed page audience moves into its group's Read once, never widening it.
    set_access(api, "groups/legal", see=EVERYONE, read={"people": ["cara"]}, write={"teams": ["legal"]})
    with store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO pin_places(place, audience_json, saved, saved_by) VALUES "
                  "('team:sales', ?, ?, 'human:ana')", (encode({"everyone": False, "people": ["eve"]}), H.now()))
        c.execute("DELETE FROM teammate_accesses WHERE target_type='group' AND target_id='sales'")
        TA.migrate_team_pages(c)
        sales = TA.document(c, "group:sales")
    assert sales["see"]["everyone"] and sales["read"]["people"] == ["eve"]   # on the chart as before; page narrowed
    with store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO pin_places(place, audience_json, saved, saved_by) VALUES "
                  "('team:legal', ?, ?, 'human:ana'), ('team:contracts', ?, ?, 'human:ana')",
                  (encode({"everyone": False, "people": ["fay"]}), H.now(),
                   encode({"everyone": True}), H.now()))
        TA.migrate_team_pages(c)
        TA.migrate_team_pages(c)                    # once: the page audience is cleared
        legal, contracts = TA.document(c, "group:legal"), TA.document(c, "group:contracts")
        audits = c.execute("SELECT count(*) FROM audit_log WHERE object='group:legal:access' AND actor='system'").fetchone()[0]
        left = c.execute("SELECT count(*) FROM pin_places WHERE audience_json IS NOT NULL").fetchone()[0]
    assert legal["see"]["everyone"]                 # See is left as it was
    assert legal["read"] == {"everyone": False, "people": [], "teams": [], "bots": []}   # {Cara} and {Fay}: nobody
    assert contracts == TA.default_document("group", "contracts")
    assert audits == 1 and left == 0


def hide(api, path, *names):
    audience = {"people": list(names)}
    set_access(api, path, see=audience, read=audience, write=audience)


def test_migration_keeps_whoever_both_boundaries_let_in(acme):
    api, tokens = acme
    store = api.app.state.store
    with store.transaction() as c:
        assert TA._narrowest(c, {"everyone": True}, {"everyone": False, "people": ["eve"]})["people"] == ["eve"]
        # Read Legal (Cara, Fay, Counsel), page {Fay, Eve}: Fay only, named one by one.
        both = TA._narrowest(c, A_({"teams": ["legal"]}), A_({"people": ["fay", "eve"]}))
        assert both == {"everyone": False, "people": ["fay"], "teams": [], "bots": []}
        # One inside the other: the narrower is kept as it is, its group still resolving live.
        assert TA._narrowest(c, A_({"teams": ["contracts"]}), A_({"teams": ["legal"]}))["teams"] == ["contracts"]
        assert TA._narrowest(c, A_({"teams": ["legal"]}), A_({"people": ["cara"]}))["people"] == ["cara"]


def A_(level):
    from backend import bot_access
    return bot_access.audience({"everyone": False, **level})


def test_a_deleted_group_takes_its_access_with_it(acme):
    api, tokens = acme
    set_access(api, "groups/sales", write={"people": ["fay"]})
    set_access(api, "humans/cara", token="cara-test", read={"teams": ["sales", "legal"]})
    assert levels(api, "fay", "group:sales")["write"]
    assert api.delete("/api/v2/groups/sales", headers=headers("ana-test")).status_code == 200
    post(api, "groups", {"name": "Sales", "add": {"people": ["dee"]}})
    assert get(api, "groups/sales/access", "ana-test")["revision"] == 0      # the defaults, not the old grant
    assert not levels(api, "fay", "group:sales")["write"] and levels(api, "dee", "group:sales")["write"]
    assert get(api, "humans/cara/access", "cara-test")["read"]["teams"] == ["legal"]
    assert not levels(api, "dee", "human:cara")["read"] or levels(api, "dee", "human:cara")["manage"]
    with api.app.state.store.read() as c:
        gone = c.execute("SELECT before_json, after_json FROM audit_log WHERE object='group:sales:access' "
                         "AND via='group-deleted'").fetchone()
    assert gone and '"fay"' in gone["before_json"] and gone["after_json"] is None


def test_group_catalogues_leave_out_groups_the_reader_may_not_see(acme):
    api, tokens = acme
    hide(api, "groups/legal", "cara")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='eve' WHERE bot='analyst'")
    for name, sees in {"eve": False, "cara": True}.items():
        token = tokens[name]
        mine = get(api, f"humans/{name}/access", token)["teams"]             # their own Access editor
        assert any(g["id"] == "legal" for g in mine) is sees, name
        picker = get(api, "cards/choices", token)["teams"]
        assert any(g["id"] == "legal" for g in picker) is sees, name
        contracts = next(g for g in picker if g["id"] == "contracts")
        assert contracts["parent"] == ("legal" if sees else None), name
    bot = get(api, "bots/analyst/access", "eve-test")["teams"]             # a bot's Access editor
    assert not any(g["id"] == "legal" for g in bot) and any(g["id"] == "sales" for g in bot)


def test_hub_sql_masks_bot_identities_and_message_recipients(acme):
    api, tokens = acme
    task = post(api, "tasks", {"owner": "counsel", "title": "Review the lease", "body": "Please."}, token="ana-test")
    post(api, f"tasks/{task['id']}/comments", {"text": "Clause 4 looks odd."}, token="dee-test")
    conversation = get(api, "tasks/" + task["id"], "ana-test")["task"]["conversation_id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara', bot_owners_json='[\"cara\",\"ana\"]', "
                  "created_by='human:cara', config_json=json_set(config_json,'$.reports_to','cara') WHERE bot='counsel'")
        c.execute("UPDATE messages SET to_actor='human:cara' WHERE conversation_id=?", (conversation,))
    hide(api, "humans/cara", "dee", "fay")
    hide(api, "groups/contracts", "fay")
    row = "SELECT operator||'|'||bot_owners_json||'|'||created_by||'|'||coalesce(team,'-') FROM bot_config " \
          "WHERE bot='counsel'"
    assert sql_column(api, row, "eve-test") == ['|["ana"]||-']
    assert sql_column(api, row, "fay-test") == ['cara|["cara","ana"]|human:cara|contracts']
    config = sql_column(api, "SELECT config_json FROM bot_config WHERE bot='counsel'", "eve-test")[0]
    assert '"team"' not in config and '"reports_to"' not in config and '"Counsel"' in config
    recipients = f"SELECT DISTINCT to_actor FROM messages WHERE conversation_id='{conversation}'"
    assert sql_column(api, recipients, "eve-test") == [""]
    assert sql_column(api, recipients, "fay-test") == ["human:cara"]
    assert sql_column(api, recipients, "dee-test") == ["human:cara"]      # in the conversation: known there


def test_rest_and_the_stream_leave_hidden_people_out_of_messages_and_tasks(acme):
    from backend import events as E

    api, tokens = acme
    store, auth = api.app.state.store, api.app.state.auth
    with store.read() as c:
        cursor = E.latest(c)
    task = post(api, "tasks", {"owner": "counsel", "title": "Review the lease", "body": "Please."}, token="cara-test")
    post(api, f"tasks/{task['id']}/comments", {"text": "Clause 4 looks odd."}, token="cara-test")
    conversation = get(api, "tasks/" + task["id"], "ana-test")["task"]["conversation_id"]
    hide(api, "humans/cara", "fay")
    for name, sees in {"eve": False, "fay": True, "ben": True}.items():
        token = tokens[name]
        page = api.get(f"/api/v2/conversations/{conversation}/messages", headers=headers(token)).json()
        authors = {m["from_actor"] for m in page["messages"]}
        assert ("human:cara" in authors) is sees, name
        assert ("human:cara" in (page["conversation"].get("participants") or [])) is sees, name
        assert (get(api, "tasks/" + task["id"], token)["task"]["requester"] == "human:cara") is sees, name
        listed = next(t for t in get(api, "tasks?owner=counsel", token)["tasks"] if t["id"] == task["id"])
        assert (listed["requester"] == "human:cara") is sees, name
        with store.read() as c:
            sent = E.read(c, auth, who(api, name), cursor, topics=("messages",))[0]
        streamed = {data["message"]["from_actor"] for _, _, data in sent if "message" in data}
        assert ("human:cara" in streamed) is sees, name
    # Eve asked Cara for work: a party to that task, Cara stays named to her there.
    mine = post(api, "tasks", {"owner": "human:cara", "title": "Sign the lease", "body": "Thanks."}, token="eve-test")
    assert get(api, "tasks/" + mine["id"], "eve-test")["task"]["owner"] == "human:cara"


def test_bot_metadata_leaves_out_hidden_people_and_groups_everywhere(acme):
    api, tokens = acme
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara', owner_ids_json='[\"cara\"]', "
                  "bot_owners_json='[\"cara\"]' WHERE bot='counsel'")
    hide(api, "humans/cara", "fay")
    hide(api, "groups/contracts", "fay")
    for name, sees in {"eve": False, "fay": True}.items():
        token = tokens[name]
        listed = next(b for b in get(api, "bots", token) if b["slug"] == "counsel")
        detail = get(api, "bots/counsel", token)
        chart = next(b for b in get(api, "org", token)["bots"] if b["id"] == "counsel")
        for row in (listed, detail):
            assert (row["operator"] == "cara") is sees, name
            assert any(o["id"] == "cara" for o in row["owners"] + row["bot_owners"]) is sees, name
            assert row["team"] == ("contracts" if sees else "legal"), name
        assert (detail.get("operator_name") is not None) is sees, name
        assert chart["team"] == ("contracts" if sees else "legal"), name
        assert chart["department"] == ("Contracts" if sees else "Legal"), name


def test_report_order_names_only_people_the_reader_may_see(acme):
    api, tokens = acme
    hide(api, "humans/cara", "dee", "fay")
    reports = lambda token: [r["id"] for r in get(api, "org/order/dee", token)["reports"]]  # noqa: E731
    assert reports("fay-test") == ["cara"] and reports("eve-test") == []
    assert api.get("/api/v2/org/order/cara", headers=headers("eve-test")).status_code == 404
    assert api.get("/api/v2/org/order/cara", headers=headers("fay-test")).status_code == 200


def test_access_reads_and_writes_answer_alike_for_missing_and_unseen(acme):
    api, tokens = acme
    hide(api, "humans/cara", "fay")
    hide(api, "groups/legal", "cara")
    body = {"see": EVERYONE, "read": EVERYONE, "write": EVERYONE, "revision": 0}
    for path in ("humans/cara", "humans/nobody-here", "groups/legal", "groups/no-such-group"):
        read = api.get(f"/api/v2/{path}/access", headers=headers("eve-test"))
        write = api.put(f"/api/v2/{path}/access", json=body, headers=headers("eve-test"))
        assert read.status_code == write.status_code == 404, path
        assert problem(read) == problem(write) and problem(read), path
    assert api.get("/api/v2/humans/dee/access", headers=headers("eve-test")).status_code == 403   # seen, not hers


def problem(response):
    body = response.json()
    return {k: v for k, v in (body.get("error") or body).items() if k in ("code", "message", "detail", "title")}


def test_botops_sets_access_as_the_person_without_the_delegation_header(acme):
    from backend.tests.test_botops_parity import act
    from backend.tests.test_member_bots import botops as make_botops, finish, turn

    api, tokens = acme
    machine = make_botops.__wrapped__(api)
    body = {"see": EVERYONE, "read": NOBODY, "write": EVERYONE, "revision": 0}
    eve = turn(api, machine, person="eve-test", text="Hide Cara's Home")
    assert act(api, eve, "PUT", "humans/cara/access", body, ref=None).status_code == 403
    finish(api, machine, eve)
    dee = turn(api, machine, person="dee-test", text="Hide Cara's Home")
    changed = act(api, dee, "PUT", "humans/cara/access", body, ref=None)
    assert changed.status_code == 200, changed.text
    group = act(api, dee, "POST", "groups/sales/access", {**body, "read": EVERYONE, "write": {"teams": ["legal"]}},
                ref=None)
    assert group.status_code == 403                                        # Dee does not lead Sales
    with api.app.state.store.read() as c:
        row = c.execute("SELECT actor FROM events WHERE action='person.access_changed' ORDER BY rowid DESC").fetchone()
    assert row["actor"] == "human:dee"
    from backend import assistant
    assert assistant.valid_operation("PUT", "/api/v2/humans/cara/access", proposer="botops")
    assert assistant.valid_operation("POST", "/api/v2/groups/sales/access", proposer="botops")


def test_the_people_list_carries_the_callers_access_and_a_manager_edits_their_report(acme):
    api, tokens = acme
    def row(token, pid):
        return next(p for p in api.get("/api/people", headers=headers(token)).json()["people"] if p["id"] == pid)
    def group(token, gid):
        return next(g for g in api.get("/api/people", headers=headers(token)).json()["org_groups"] if g["id"] == gid)
    assert row("dee-test", "cara")["my_access"]["manage"] and not row("eve-test", "cara")["my_access"]["manage"]
    assert group("fay-test", "legal")["my_access"]["write"] and not group("eve-test", "legal")["my_access"]["write"]
    # The list says what the server does: her manager edits Cara's profile and a peer does not.
    assert api.post("/api/v2/people/cara", json={"title": "Counsel"}, headers=headers("dee-test")).status_code == 200
    assert api.post("/api/v2/people/cara", json={"title": "Peer"}, headers=headers("eve-test")).status_code == 403
