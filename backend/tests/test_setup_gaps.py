"""Setup gaps (backend/setup_gaps.py) and the setup ping (backend/flow_ping.py scope `setup`): which areas wait on a
person, which bot asks for each, the `setup` change that tells a page to read again, and the message the bot gets.
Ana owns Acme, Ben is an admin, Cara a member (the base fixture); its company bots (ops, cpo...) fill the org."""

import json
from pathlib import Path

import yaml

from backend.tests.helpers.api import get, headers

BUILT_INS = (
    ("librarian", "Librarian"),
    ("strategy-owner", "Strategy Owner"),
    ("botops", "BotOps"),
    ("tools-owner", "Tools Owner"),
)
NOW = "2026-11-16T00:00:00Z"


def built_ins(api):
    """The built-ins every new company gets at setup (the base fixture skips setup)."""
    with api.app.state.store.transaction() as c:
        for slug, name in BUILT_INS:
            c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')", (slug, slug, name))


def gaps(api, token="ana-test"):
    return {g["area"]: g for g in get(api, "setup/gaps", token)["areas"]}


def needing(api, token="ana-test"):
    return [a for a, g in gaps(api, token).items() if g["needs"]]


def setup_rings(api):
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT kind FROM changes WHERE topic='setup' ORDER BY seq")]


def test_each_area_needs_the_person_until_it_is_filled_and_names_the_bot_that_asks(api):
    built_ins(api)
    found = gaps(api)
    assert list(found) == ["market", "strategy", "goals", "org", "tools", "budget"]
    assert {a: (g["bot"] or {}).get("id") for a, g in found.items()} == {
        "market": "bot:librarian",
        "strategy": "bot:strategy-owner",
        "goals": "bot:strategy-owner",
        "org": "bot:botops",
        "tools": "bot:tools-owner",
        "budget": "bot:botops",
    }
    assert found["tools"]["href"] == "#/tools" and found["market"]["bot"]["name"] == "Librarian"
    assert needing(api) == ["market", "strategy", "goals", "tools", "budget"]  # the fixture's own bots fill the org
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug NOT IN (?,?,?,?)", tuple(s for s, _ in BUILT_INS))
    assert "org" in needing(api)
    with store.transaction() as c:  # each area filled, the way its own writes fill it
        c.execute(
            "INSERT INTO entities(id,type,name,status,created_by,created_at,updated_at) "
            "VALUES('e1','company','Rival Co','active','bot:librarian',?,?)",
            (NOW, NOW),
        )
        c.execute("UPDATE bots SET state='active' WHERE slug='ops'")
        c.execute(
            "INSERT INTO goals(id,title,owner_id,created_at,created_by_id,updated_at) VALUES('g1','Grow','human:ana',?,?,?)",
            (NOW, "human:ana", NOW),
        )
        c.execute(
            "INSERT INTO tools(id,name,created_by,created_at,updated_at) VALUES('stripe','Stripe','human:ana',?,?)",
            (NOW, NOW),
        )
        c.execute(
            "INSERT INTO budget_nodes(id,scope,period,amount_nano,created_at,updated_at) "
            "VALUES('b1','company','monthly',100000000000,?,?)",
            (NOW, NOW),
        )
    assert needing(api) == ["strategy"]
    with store.transaction() as c:  # a strategy needs its summary and a brand, draft or published
        if not c.execute("SELECT 1 FROM strategy_versions").fetchone():
            c.execute("INSERT INTO strategy_versions(id,status) VALUES('s1','draft')")
        c.execute("UPDATE strategy_versions SET summary='Clean offices, booked in a minute'")
        c.execute("INSERT INTO brand_drafts(id,body) VALUES(1,'{\"voice\": \"\"}')")
    assert needing(api) == ["strategy"]
    with store.transaction() as c:
        c.execute("""UPDATE brand_drafts SET body='{"voice": "Plain and warm"}'""")
    assert needing(api) == []


def test_an_open_market_task_takes_the_gap_off_the_person(api):
    built_ins(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO tasks(id,title,owner_id,requester_id,status,created_at,updated_at) "
            "VALUES('t1','Set up the market map','bot:librarian','human:ana','open',?,?)",
            (NOW, NOW),
        )
    assert "market" not in needing(api)


def test_only_the_owner_an_admin_or_a_bot_is_waited_on(api):
    built_ins(api)
    assert "market" in needing(api, "ben-test")
    assert needing(api, "cara-test") == []
    assert gaps(api, "cara-test")["market"]["bot"]["id"] == "bot:librarian"  # where it is, never that it waits on them


def test_a_write_that_closes_a_gap_rings_setup_once(api):
    built_ins(api)
    before = setup_rings(api)
    with api.app.state.store.transaction() as c:
        for n in (1, 2):
            c.execute(
                "INSERT INTO goals(id,title,owner_id,created_at,created_by_id,updated_at) VALUES(?,?,?,?,?,?)",
                (f"g{n}", f"Goal {n}", "human:ana", NOW, "human:ana", NOW),
            )
        for n in (1, 2):
            c.execute(
                "INSERT INTO entities(id,type,name,status,created_by,created_at,updated_at) "
                "VALUES(?,'company',?,'active','bot:librarian',?,?)",
                (f"e{n}", f"Rival {n}", NOW, NOW),
            )
    assert setup_rings(api)[len(before) :] == ["goals", "market"]  # the first goal and entity only
    assert "goals" not in needing(api)


def setup_messages(api, bot):
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT sender_id, body, refs FROM messages WHERE recipient_id=? AND json_extract(refs, '$.setup') IS NOT NULL",
            ("bot:" + bot,),
        ).fetchall()
    return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]


def ping(api, token="ana-test", expected=200, **body):
    r = api.post("/api/v2/flow/ping", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_a_mark_tap_pings_the_areas_bot_with_the_setup_skill(api):
    built_ins(api)
    got = ping(api, scope="setup", id="market")
    assert [b["id"] for b in got["pinged"]] == ["librarian"]
    [sent] = setup_messages(api, "librarian")
    assert sent["sender_id"] == "human:ana" and sent["refs"]["setup"] == ["market"]
    assert "Market" in sent["body"] and "Ana" in sent["body"]
    skill = Path(__file__).resolve().parents[2] / "skills/setup-in-flow/SKILL.md"
    assert str(skill.relative_to(skill.parents[2])) in sent["body"]
    front = yaml.safe_load(skill.read_text().split("---")[1])
    assert front["name"] == "setup-in-flow" and front["description"]
    assert ping(api, scope="setup", id="market")["recent"][0]["id"] == "librarian"  # a second tap asks once
    assert ping(api, expected=404, scope="setup", id="payroll")["error"]["code"] == "not_found"


def test_the_onboarding_finish_pings_each_open_areas_bot_once_with_its_areas(api):
    built_ins(api)
    assert ping(api, "cara-test", scope="setup") == {"pinged": [], "recent": []}  # a member is not waited on
    got = ping(api, "ben-test", scope="setup")
    assert sorted(b["id"] for b in got["pinged"]) == ["botops", "librarian", "strategy-owner", "tools-owner"]
    [strategy] = setup_messages(api, "strategy-owner")
    assert strategy["refs"]["setup"] == ["strategy", "goals"] and "Strategy, Goals" in strategy["body"]
    assert setup_messages(api, "botops")[0]["refs"]["setup"] == ["budget"]


def test_ping_your_bots_also_picks_setup_up(api):
    built_ins(api)
    got = ping(api)  # Ana's reports, plus the bots her open setup areas wait on
    assert {"botops", "librarian", "strategy-owner", "tools-owner"} <= {b["id"] for b in got["pinged"]}
    assert setup_messages(api, "tools-owner")[0]["refs"]["setup"] == ["tools"]
