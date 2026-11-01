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
    assert "human:cara" in sql_column(api, sql, "eve-test")
    set_access(api, "humans/cara", token="cara-test", see={"teams": ["legal"]}, read={"teams": ["legal"]},
               write={"teams": ["legal"]})
    assert "human:cara" not in sql_column(api, sql, "eve-test")
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
