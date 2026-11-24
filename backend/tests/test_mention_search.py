"""The @ picker over the API (`GET /api/v2/mentions`, docs/connect-an-agent.md "Build an @ picker"): whom a custom
frontend may offer, in the web picker's order, with a handle a post reads as that one teammate. Fictional people: ana
(the owner), ben, cara."""

import json
from pathlib import Path

from backend.tests.helpers.api import headers, post
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


def test_a_tasks_people_come_first(api):
    task = post(
        api,
        "tasks",
        {"owner": "ops", "title": "Close the month", "body": "Reconcile May.", "private": True},
        token="ben-test",
    )
    rows = found(api, task=task["id"], limit=50)
    assert ids(rows)[:2] == ["human:ben", "bot:ops"], ids(rows)
    assert [r["on_task"] for r in rows[:3]] == [True, True, False]
    # A query still ranks within the task's people first, then everyone else.
    assert ids(found(api, task=task["id"], q="o"))[:1] == ["bot:ops"]
    found(api, "cara-test", 404, task=task["id"])  # a private task the caller cannot read is never confirmed
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    op = document["paths"]["/api/v2/mentions"]["get"]
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    r = api.get("/api/v2/mentions", params={"task": task["id"]}, headers=headers())
    assert conforms(r.json(), schema, document) is None, conforms(r.json(), schema, document)


def test_a_direct_chat_and_a_thread_under_it_offer_only_its_members(api):
    from backend.tests.threads_e2e import create_thread, say

    dm = api.get("/api/v2/threads/dm", params={"with": "human:ben"}, headers=headers()).json()["id"]
    assert ids(found(api, thread=dm, limit=50)) == ["human:ben"]
    said = say(api, "ana-test", dm, "The May totals?")
    sub = say(api, "ana-test", dm, "One more thing.", parent_message_id=said["id"])["thread_id"]
    assert sub != dm
    assert ids(found(api, thread=sub, limit=50)) == ["human:ben"]
    # A group room is not narrowed: an outsider's @mention there gets the post's `mention_outside` warning.
    room = create_thread(api, "ana-test", members=["human:ben"])
    assert {"human:ben", "human:cara"} <= set(ids(found(api, thread=room["id"], limit=50)))
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


def add_person(api, pid, name, email):
    from backend.store import encode

    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES(?,?,?)", (pid, name, email))
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        people["people"].append({"id": pid, "name": name, "email": email})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(people),))


def name_agent(api, slug, display):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET display_name=? WHERE slug=?", (display, slug))


def handles(api):
    """{actor: handle} for everyone Ana is offered, each checked the way a post reads it: one word, and a post of it
    followed by more words names that teammate alone."""
    from backend import threads

    rows = found(api, limit=50)
    with api.app.state.store.read() as c:
        for r in rows:
            if r["handle"] is not None:
                assert " " not in r["handle"], r
                said = threads.mentions(c, None, f"{r['handle']} can you check?", author="human:ana")
                assert said == [(r["id"], "mention")], r
    return {r["id"]: r["handle"] for r in rows}


def test_a_handle_is_one_word_a_post_reads_as_that_teammate_alone(api):
    """`Money Desk` beside `Money Desk Can`: "@Money Desk can you check?" names the second, so a name with a space is
    never offered; Finance's slug is also Fin's email prefix, which leaves it no handle at all."""
    add_person(api, "fin", "Fin Lee", "finance@acme.example")
    name_agent(api, "finance", "Money Desk")
    name_agent(api, "ops", "Money Desk Can")
    by_id = handles(api)
    assert by_id["bot:finance"] is None and by_id["bot:ops"] == "@ops" and by_id["human:fin"] == "@fin"


def test_a_handle_is_matched_as_a_post_matches_it_case_and_all(api):
    """A post reads `@iris` as anyone whose name is `ırıs` too (re's case-insensitive i), so Iris gets her email prefix."""
    add_person(api, "iris", "Iris Park", "ipark@acme.example")
    add_person(api, "kemal", "Kemal Ay", "ırıs@acme.example")
    assert handles(api)["human:iris"] == "@ipark"


def test_a_unique_first_name_is_a_handle_when_nothing_else_is(api):
    """Nora's id and email prefix are both `ops`, the Ops agent's slug: only her first name is hers alone."""
    add_person(api, "ops", "Nora Lee", "ops@acme.example")
    name_agent(api, "ops", "Operations")
    by_id = handles(api)
    assert by_id["human:ops"] == "@Nora" and by_id["bot:ops"] == "@Operations"


def test_a_handle_never_begins_another_teammates_longer_name(api):
    """`@ops can you check?` names only Ops, but `@ops please` would name the agent `Ops Please`: Ops gets its display
    name instead."""
    name_agent(api, "ops", "Operations")
    name_agent(api, "finance", "Ops Please")
    by_id = handles(api)
    assert by_id["bot:ops"] == "@Operations" and by_id["bot:finance"] == "@finance"


def test_no_handle_when_every_name_also_names_someone_else(api):
    """An agent Ops (slug ops) and a person whose email starts with ops: `@ops` and `@Ops` reach both, so the agent gets
    no handle; the person still has their id."""
    add_person(api, "olive", "Olive Park", "ops@acme.example")
    name_agent(api, "ops", "Ops")
    by_id = handles(api)
    assert by_id["bot:ops"] is None and by_id["human:olive"] == "@olive"


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
