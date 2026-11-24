"""The @ picker over the API (`GET /api/v2/mentions`, docs/connect-an-agent.md "Build an @ picker"): whom a custom
frontend may offer, in the web picker's order, with a handle the server reads back as that one teammate and whether an
@mention would reach them. Fictional people: ana (the owner), ben, cara."""

import json
from pathlib import Path

from backend.tests.helpers.api import headers, post, restrict
from backend.tests.helpers.openapi_v2 import conforms

FIXTURE = Path(__file__).parent / "fixtures" / "mention_ranking.json"


def found(api, token="ana-test", expected=200, **params):
    r = api.get("/api/v2/mentions", params=params, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()["candidates"] if expected == 200 else r.json()


def ids(rows):
    return [r["id"] for r in rows]


def test_the_ranking_matches_the_web_pickers_case_list():
    """Every case of the list web/src/conversation/mentions.test.ts runs through `mentionMatches` and `mentionText`."""
    from backend import mention_search as M

    cases = json.loads(FIXTURE.read_text())
    for name, handle in cases["handles"].items():
        assert M.handle_of(next(a for a in cases["actors"] if a["id"] == name)) == handle, name
    for case in cases["cases"]:
        got = M.ranked(cases["actors"], case["query"], cases["me"], case.get("first", ()), case.get("only"))
        assert [a["id"] for a, _ in got] == case["want"], case


def test_a_tasks_people_come_first_and_a_private_task_widens_to_whom_it_names(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Close the month", "body": "Reconcile May."}, token="ben-test")
    rows = found(api, task=task["id"], limit=50)
    assert ids(rows)[:2] == ["human:ben", "bot:ops"], ids(rows)
    assert [r["on_task"] for r in rows[:3]] == [True, True, False]
    # Cara cannot read this private task, but Ana's @mention would add her to its readers: it reaches her.
    cara = next(r for r in rows if r["id"] == "human:cara")
    assert cara["wakes"] is True and "reason" not in cara
    # A query still ranks within the task's people first, then everyone else.
    assert ids(found(api, task=task["id"], q="o"))[:1] == ["bot:ops"]
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    op = document["paths"]["/api/v2/mentions"]["get"]
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    r = api.get("/api/v2/mentions", params={"task": task["id"]}, headers=headers())
    assert conforms(r.json(), schema, document) is None, conforms(r.json(), schema, document)


def test_a_direct_chat_offers_only_its_members_and_a_private_chat_tells_who_it_misses(api):
    from backend.tests.threads_e2e import create_thread

    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers()).json()["id"]
    assert ids(found(api, thread=dm, limit=50)) == ["human:ben"]
    room = create_thread(api, "ana-test", members=["human:ben"])
    rows = {r["id"]: r for r in found(api, thread=room["id"], limit=50)}
    assert rows["human:ben"]["wakes"] is True
    assert rows["human:cara"]["wakes"] is False and rows["human:cara"]["reason"]
    found(api, "cara-test", 404, thread=room["id"])  # a thread the caller cannot read is never confirmed


def test_never_the_caller_an_archived_agent_or_one_they_cannot_see(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='doc-updater'")
    everyone = ids(found(api, limit=50))
    assert "human:ana" not in everyone and "bot:doc-updater" not in everyone
    assert {"human:ben", "human:cara", "bot:ops", "bot:inbox"} <= set(everyone)
    # The inbox agent is Ana's alone: Cara is never offered it, by any query.
    assert "bot:inbox" not in ids(found(api, "cara-test", limit=50))
    assert found(api, "cara-test", q="inbox") == []


def test_each_handle_reads_back_as_that_teammate_alone(api):
    """`threads.mentions` reads every handle as exactly its teammate, a clash included: a person whose email starts
    like an agent's slug gets that agent its display name instead."""
    from backend import threads
    from backend.store import encode

    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('fin','Fin Lee','finance@acme.example')")
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        people["people"].append({"id": "fin", "name": "Fin Lee", "email": "finance@acme.example"})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(people),))
        c.execute("UPDATE bots SET display_name='Money Desk' WHERE slug='finance'")
    rows = found(api, limit=50)
    by_id = {r["id"]: r["handle"] for r in rows}
    assert by_id["bot:finance"] == "@Money Desk" and by_id["bot:ops"] == "@ops" and by_id["human:fin"] == "@fin"
    with api.app.state.store.read() as c:
        for r in rows:
            assert threads.mentions(c, None, f"{r['handle']} can you look?") == [(r["id"], "mention")], r


def test_an_agent_without_the_callers_write_does_not_wake(api):
    with api.app.state.store.transaction() as c:
        restrict(
            c, "finance", see={"people": ["ana", "cara"]}, read={"people": ["ana", "cara"]}, write={"people": ["ana"]}
        )
    finance = next(r for r in found(api, "cara-test", q="fin") if r["id"] == "bot:finance")
    assert finance["wakes"] is False
    assert (
        finance["reason"]
        == "You can see finance but not send it requests. Ask the person who runs it for Write access."
    )
    assert next(r for r in found(api, q="fin") if r["id"] == "bot:finance")["wakes"] is True


def test_an_agent_over_mcp_finds_the_same_candidates(api):
    from backend.tests.helpers.company import bot
    from backend.tests.helpers.mcp import call, rpc

    token = bot(api, "ops")
    listed = {t["name"] for t in rpc(api, "tools/list", token=token)["result"]["tools"]}
    assert "tico_mention_search" in listed
    err, answer = call(api, "tico_mention_search", {"q": "fin", "limit": 3}, token=token)
    assert not err, answer
    assert answer["candidates"][0]["id"] == "bot:finance" and answer["candidates"][0]["handle"] == "@finance"
    assert "bot:ops" not in ids(answer["candidates"])  # never the caller, an agent too
