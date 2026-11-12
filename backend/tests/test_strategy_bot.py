"""The Strategy Owner on every install (backend/strategy_bot.py; spec: Head of Product, Strategy Owner v3): the built-in bot
that was the Goal Manager is named the Strategy Owner everywhere and heads the org chart under the owner, with the Heads
and the bots that report to no one under it. There is no switch a person sees. Only the chart moves: every stored
`reports_to` stays as it was, and so does who may do what. Only the owner renames it."""

import json

from backend import strategy_bot, strategy_suggest
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.goal_manager import BASE, with_goal_manager
from backend.tests.helpers.onboarding import (
    SUPPORT_AGENT,
    SUPPORT_CARD,
    as_person,
    build_team,
    draft,
    environment,
    machine,
    signed_in,
)
from backend import bot_settings as BS
from backend.tests.helpers.strategy_bot import as_bot

LEAD_CARD = {
    "template": "growth-manager",
    "slug": "growth-manager",
    "name": "Growth Manager",
    "required": False,
    "bootstrap": False,
    "lead": True,
    "summary": "Runs growth.",
    "owns": ["the pipeline"],
    "never": ["sends"],
    "reasoning_effort": "high",
    "recommend_when": ["sells_to_businesses"],
}
HELPER_CARD = {
    "template": "inbox-manager",
    "slug": "inbox",
    "name": "Inbox Manager",
    "required": False,
    "kind": "helper",
    "bootstrap": False,
    "summary": "Runs one person's mailbox.",
    "owns": ["a mailbox"],
    "never": ["sends"],
    "reasoning_effort": "high",
    "recommend_when": ["always"],
}
CARDS = BASE + [(LEAD_CARD, ""), (SUPPORT_CARD, SUPPORT_AGENT), (HELPER_CARD, "")]
TEAM = {
    "growth-manager": ("growth-manager", "human:morgan"),  # a Head under the owner
    "eng-lead": ("growth-manager", ""),  # a Head with no manager
    "support": ("support-owner", "growth-manager"),  # under a Head: stays there
    "scribe": ("support-owner", ""),  # no manager at all
    "personal": ("support-owner", "human:morgan"),  # the owner's own bot, not a Head: stays under the owner
    "rileys": ("support-owner", "human:riley"),  # another person's bot
    "inbox": ("inbox-manager", ""),  # a message bot: off the chart
}


def company(environment):
    api = environment(
        seed={
            "assistant": {"name": "assistant", "status": "planned"},
            "botops": {"name": "botops", "status": "planned"},
        },
        cards=CARDS,
    )
    with_goal_manager(api)
    machine(api)
    draft(
        api,
        selected={
            slug: {"template": template, "display_name": slug.title(), "instructions": "Work.", "reports_to": boss}
            for slug, (template, boss) in TEAM.items()
        },
    )
    build_team(api)
    # The team builder put the Heads and their reports in the Heads' department (Marketing, the Growth Manager's). The outside helper bots (no
    # team of their own) sit in the General group, which Tico keeps (the Assistant is in none); the owner has no group yet.
    names = labels(api)
    assert {
        g["id"]: sorted(names.get(bot, bot) for bot in g["bots"])
        for g in api.get("/api/v2/groups", headers=signed_in()).json()
    } == {
        "marketing": ["eng-lead", "growth-manager", "support"],
        "built-in": ["botops", "inbox", "strategy-owner"],
    }
    home = api.post(
        "/api/v2/groups", json={"name": "Home", "add": {"people": ["morgan"], "bots": ["scribe"]}}, headers=signed_in()
    )
    assert home.status_code == 200, home.text
    return api


def labels(api):
    """Bot id to slug. The chart, the groups and the stored `reports_to` hold ids; these tests compare the slugs people
    see, so every reference read here is turned into its slug."""
    with api.app.state.store.read() as c:
        return {r["id"]: r["slug"] for r in c.execute("SELECT id,slug FROM bots")}


def as_slug(names, ref):
    """A reference (`b:<id>`, a bare bot id, or anything else) with the bot's slug in place of its id."""
    if ref.startswith("b:"):
        return "b:" + names.get(ref[2:], ref[2:])
    return names.get(ref, ref)


def chart(api, headers=None):
    view = api.get("/api/v2/org", headers=headers or signed_in()).json()
    names = labels(api)
    return {
        names.get(b["id"], b["id"]): (
            as_slug(names, b["org_parent"]),
            as_slug(names, b["reports_to"] or ""),
            b["display_name"],
        )
        for b in view["bots"]
    }


def groups(api, path="/api/v2/org"):
    names = labels(api)
    return {g["id"]: as_slug(names, g["org_parent"]) for g in api.get(path, headers=signed_in()).json()["org_groups"]}


def employees(api):
    names = labels(api)
    return {
        e["name"]: (as_slug(names, e["org_parent"]), e["display_name"])
        for e in api.get("/api/employees", headers=signed_in()).json()
    }


def stored(api):
    names = labels(api)
    with api.app.state.store.read() as c:
        reports = {
            names.get(r["bot"], r["bot"]): as_slug(names, r["reports_to"] or "")
            for r in c.execute("SELECT bot,reports_to FROM bot_config")
        }
        display = {r["slug"]: r["display_name"] for r in c.execute("SELECT slug,display_name FROM bots")}
    return reports, display


def definition(api, body, headers=None):
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='strategy-owner'").fetchone()[0]
    return api.post(
        "/api/v2/bots/strategy-owner/definition",
        json={**body, "expected_revision": revision},
        headers=headers or signed_in(),
    )


# Where the old names may still be written: the upgrade that renames them, and the UI's fallback for a roster read
# before it.
OLD_NAME_ALLOWED = {
    "backend/strategy_bot.py",
    "backend/store.py",
    "ui/app/notices.ts",
    "ui/app/state.ts",
    # Written before the rename to Strategy Owner, and never changed after.
    "backend/redesign_migration.py",
    "backend/migrations/0006_drop_old_strategy_tables.sql",
    "backend/migrations/0035_growth_rethink.py",
    "backend/migrations/0064_history_events.py",
}


def hide(api, value, headers=None):
    return api.post(
        "/api/v2/preferences/" + strategy_bot.HIDE_PREF, json={"value": value}, headers=headers or signed_in()
    )


def rows(api, headers=None):
    return {e["name"]: e for e in api.get("/api/employees", headers=headers or signed_in()).json()}


def drawn(api, headers):
    """Every chart a person's screen draws from, for the three places that call `chart_entries` (and the seats'
    parents beside them): the org chart API, the employee rows, the people chart."""
    org = api.get("/api/v2/org", headers=headers).json()
    humans = api.get("/api/humans", headers=headers).json()
    names = labels(api)
    return {
        "org": {
            names.get(b["id"], b["id"]): (as_slug(names, b["org_parent"]), as_slug(names, b["reports_to"] or ""))
            for b in org["bots"]
        },
        "org_groups": {g["id"]: g["org_parent"] for g in org["org_groups"]},
        "org_seats": {s["id"]: s["org_parent"] for s in org["seats"]},
        "employees": {
            name: (as_slug(names, e["org_parent"]), e.get("chart_hidden", False))
            for name, e in rows(api, headers).items()
        },
        "humans_groups": {g["id"]: g["org_parent"] for g in humans["org_groups"]},
        "humans_seats": {s["id"]: s["org_parent"] for s in humans["seats"]},
        "seats": {s["id"]: s["org_parent"] for s in api.get("/api/v2/seats", headers=headers).json()["seats"]},
    }


def test_the_strategy_bot_heads_the_chart_gains_no_access_and_answers_to_its_old_paths(environment):
    api = company(environment)
    # every install has the strategy bot at the top of the chart
    reports_before = stored(api)[0]
    shown = api.get("/api/v2/strategy-bot", headers=signed_in()).json()
    assert (
        "on" not in shown
        and shown["name"] == "Strategy Owner"
        and shown["level"] == 2
        and shown["regular_checks"] is True
    )
    assert "strategy_bot" not in api.get("/api/v2/config", headers=signed_in()).json()
    flip = api.post("/api/v2/strategy-bot", json={"on": False}, headers=signed_in())
    assert flip.status_code == 405, flip.text  # nobody turns it off, the owner included
    bots = chart(api)
    assert bots["strategy-owner"][:1] == ("p:morgan",) and bots["strategy-owner"][2] == "Strategy Owner"
    under_strategy = {slug for slug, (parent, _, _) in bots.items() if parent == "b:strategy-owner"}
    assert under_strategy == {"growth-manager", "eng-lead", "scribe"}
    assert bots["support"][0] == "b:growth-manager"
    # The Head's department hangs under the Strategy Owner with it; the owner's own group stays, or the chart would loop.
    # The General group is top level, not under the Strategy Owner even though strategy-owner sits in it.
    expected = {"marketing": "b:strategy-owner", "home": "", "built-in": ""}
    assert groups(api) == groups(api, "/api/humans") == expected
    assert bots["personal"][0] == "p:morgan" and bots["rileys"][0] == "p:riley"
    assert (
        bots["assistant"][0] == "p:morgan" and bots["inbox"][0] != "b:strategy-owner"
    )  # the Assistant is not under it
    # The chart moved; who each bot reports to did not, in the API or in the database.
    assert {slug: row[1] for slug, row in bots.items()} == {slug: reports_before[slug] or "" for slug in bots}
    reports_after, names = stored(api)
    assert reports_after == reports_before and names["strategy-owner"] == "Strategy Owner"
    people = employees(api)
    assert people["strategy-owner"] == ("p:morgan", "Strategy Owner") and people["eng-lead"][0] == "b:strategy-owner"
    # the strategy bot gains no access
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE bot_config SET access=? WHERE bot=?",
            (
                '{"see":{"everyone":true},"read":{"people":["morgan"]},"write":{"people":["morgan"]}}',
                H.resolve_bot(c, "eng-lead")["id"],
            ),
        )
    after = api.get("/api/v2/org", headers=as_bot(api, "strategy-owner")).json()
    names = labels(api)
    access = {names.get(b["id"], b["id"]): b["access"] for b in after["bots"]}
    assert access["eng-lead"]["write"] is False and access["eng-lead"]["read"] is False
    # the old ceo bot paths answer as the strategy bot paths and stay out of the api document
    old, new = api.get("/api/v2/ceo-bot", headers=signed_in()), api.get("/api/v2/strategy-bot", headers=signed_in())
    assert old.status_code == new.status_code == 200 and old.json() == new.json()
    changed = api.post("/api/v2/ceo-bot/level", json={"level": 2}, headers=signed_in())
    assert changed.status_code == 200 and changed.json()["level"] == 2
    assert api.get("/api/v2/strategy-bot", headers=signed_in()).json()["level"] == 2
    assert api.get("/api/v2/ceo-bot/log", headers=signed_in()).status_code == 200
    paths = api.get("/api/v2/openapi.json", headers=signed_in()).json()["paths"]
    assert "/api/v2/strategy-bot" in paths and not [p for p in paths if "ceo-bot" in p]


def test_the_name_sticks_and_only_the_owner_renames_it(environment):
    api = company(environment)
    # the name sticks and its history stays attached
    kpi = api.post(
        "/api/v2/kpis", json={"name": "Weekly signups", "owner": "company", "cadence": "weekly"}, headers=signed_in()
    )
    assert kpi.status_code == 200, kpi.text
    kid = kpi.json()["kpi"]["id"]
    logged = api.post(f"/api/v2/kpis/{kid}/readings", json={"value": 12}, headers=as_bot(api, "strategy-owner"))
    assert logged.status_code == 200, logged.text
    readings = api.get(f"/api/v2/kpis/{kid}/readings", headers=signed_in()).json()["readings"]
    assert [r["actor_id"] for r in readings] == ["bot:strategy-owner"]
    from backend import names

    with api.app.state.store.read() as c:
        assert names.lookup(c, {"bot:strategy-owner"}) == {"bot:strategy-owner": "Strategy Owner"}
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    from backend.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(api.app.state.store.settings)):  # a restart is an update
        pass
    assert stored(api)[1]["strategy-owner"] == "Strategy Owner"
    refused = api.post("/api/v2/strategy-owner/turn-on", json={}, headers=as_person(api, "quinn"))
    assert refused.status_code == 403 and "Strategy Owner" in refused.json()["error"]["detail"]
    # only the owner renames it and the new name shows everywhere
    for headers in (as_person(api, "riley"), as_person(api, "quinn"), as_bot(api, "botops")):
        refused = definition(api, {"display_name": "Atlas"}, headers)
        assert refused.status_code == 403, refused.text
    # The Strategy Owner itself is refused too, and its try becomes a proposal to the owner (backend/strategy_acts.py
    # `gate`).
    refused = definition(api, {"display_name": "Atlas"}, as_bot(api, "strategy-owner"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "proposed", refused.text
    assert stored(api)[1]["strategy-owner"] == "Strategy Owner"
    renamed = definition(api, {"display_name": "Atlas"})
    assert renamed.status_code == 200, renamed.text
    assert chart(api)["strategy-owner"][2] == "Atlas" and employees(api)["strategy-owner"][1] == "Atlas"
    assert api.get("/api/v2/strategy-bot", headers=signed_in()).json()["name"] == "Atlas"
    from backend import names

    with api.app.state.store.read() as c:
        assert names.lookup(c, {"bot:strategy-owner"}) == {"bot:strategy-owner": "Atlas"}
        assert strategy_bot.name(c) == "Atlas"
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    from backend.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(api.app.state.store.settings)):
        pass
    assert stored(api)[1]["strategy-owner"] == "Atlas"


def test_a_person_hides_the_strategy_bot_from_their_own_chart_and_only_with_a_yes_or_no(environment):
    api = company(environment)
    # a person hides the strategy bot from their own chart
    seat = api.post(
        "/api/v2/seats", json={"title": "Chief of Staff", "reports_to": "strategy-owner"}, headers=signed_in()
    )
    assert seat.status_code == 200, seat.text
    sid = seat.json()["id"]
    riley, bot = as_person(api, "riley"), as_bot(api, "botops")
    before = {"morgan": drawn(api, signed_in()), "riley": drawn(api, riley)}
    bot_before = chart(api, bot)
    stored_before = stored(api)
    with api.app.state.store.read() as c:
        config_before = tuple(c.execute(BS.ROW_SQL + " WHERE bc.bot='strategy-owner'").fetchone())
    strategy_before = api.get("/api/v2/strategy-bot", headers=signed_in()).json()
    row_before = rows(api)["strategy-owner"]
    assert before["morgan"]["org"]["eng-lead"][0] == "b:strategy-owner"  # today's chart: the Strategy Owner heads it

    assert hide(api, True).status_code == 200
    mine = drawn(api, signed_in())
    # The org chart API: no Strategy Owner; nothing under it; groups where they were; a seat under it hangs from the
    # owner.
    from backend import people as P

    with api.app.state.store.read() as c:
        from backend import views

        archived = {r["id"] for r in H.bots(c) if r.get("state") == "archived"}
        unmoved = P.org_view(views.roster(c), views.entries(c), archived)
    names = labels(api)
    expected = {
        names.get(b["id"], b["id"]): as_slug(names, b["org_parent"])
        for b in unmoved["bots"]
        if names.get(b["id"]) != "strategy-owner"
    }
    assert "strategy-owner" not in mine["org"]
    assert {slug: parent for slug, (parent, _) in mine["org"].items()} == expected
    assert not any(parent == "b:strategy-owner" for parent, _ in mine["org"].values())
    assert mine["org"]["eng-lead"][0] != "b:strategy-owner" and mine["org"]["support"][0] == "b:growth-manager"
    # reports_to as stored
    assert {slug: row[1] for slug, row in mine["org"].items()} == {
        slug: row[1] for slug, row in before["morgan"]["org"].items() if slug != "strategy-owner"
    }
    assert mine["org_groups"] == mine["humans_groups"] == {"marketing": "", "home": "", "built-in": ""}
    assert mine["org_seats"][sid] == "p:morgan"
    # The employee rows: every bot still listed, the Strategy Owner marked for the chart to leave off, nothing moved under
    # it.
    assert set(mine["employees"]) == set(before["morgan"]["employees"])
    assert mine["employees"]["strategy-owner"][1] is True
    assert {n for n, (_, hidden) in mine["employees"].items() if hidden} == {"strategy-owner"}
    assert {n: parent for n, (parent, _) in mine["employees"].items() if n != "strategy-owner"} == {
        n: p for n, p in expected.items() if n in mine["employees"]
    }
    # The people chart's seats and GET /api/v2/seats read the same unmoved chart.
    assert mine["humans_seats"] == mine["seats"] == before["morgan"]["seats"]

    # Anyone else's chart, and a bot's, is today's.
    assert drawn(api, riley) == before["riley"]
    assert chart(api, bot) == bot_before
    # The bot itself: nothing changed but where this person's chart draws it.
    assert stored(api) == stored_before
    with api.app.state.store.read() as c:
        assert tuple(c.execute(BS.ROW_SQL + " WHERE bc.bot='strategy-owner'").fetchone()) == config_before
        assert [
            tuple(r) for r in c.execute("SELECT actor,value FROM preferences WHERE key=?", (strategy_bot.HIDE_PREF,))
        ] == [("human:morgan", "true")]
    assert api.get("/api/v2/strategy-bot", headers=signed_in()).json() == strategy_before
    row = rows(api)["strategy-owner"]
    assert {k: v for k, v in row.items() if k != "chart_hidden"} == row_before
    assert api.get("/api/v2/bots/strategy-owner", headers=signed_in()).status_code == 200

    # Show on my chart: back to today's, for every place.
    assert hide(api, False).status_code == 200
    assert drawn(api, signed_in()) == before["morgan"]
    # hiding the strategy bot is a persons own yes or no
    refused = hide(api, "yes")
    assert refused.status_code == 422, refused.text
    assert hide(api, True, as_bot(api, "botops")).status_code == 403
    with api.app.state.store.transaction() as c:  # even a row a bot left some other way hides nothing for it
        c.execute(
            "INSERT INTO preferences(actor,key,value,updated_at) VALUES('bot:botops',?,'true',?)",
            (strategy_bot.HIDE_PREF, H.now()),
        )
    assert "strategy-owner" in chart(api, as_bot(api, "botops"))
    assert hide(api, True, as_person(api, "riley")).status_code == 200
    assert "strategy-owner" not in chart(api, as_person(api, "riley")) and "strategy-owner" in chart(api)
    # A call on the person's API token reads the whole chart: an agent finding who handles work.
    settings = api.app.state.store.settings
    settings.test_identities["riley-token"] = Identity(
        "human:riley", "human", email="riley@acme.example", via_token=True
    )
    assert "strategy-owner" in chart(api, signed_in("riley-token"))
    assert not rows(api, signed_in("riley-token"))["strategy-owner"].get("chart_hidden")


def test_an_upgrade_names_it_moves_its_settings_and_leaves_an_older_release_its_keys(environment):
    api = company(environment)
    # an install from before becomes the strategy bot once at upgrade
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE bots SET display_name='Goal Manager' WHERE slug='strategy-owner'"
        )  # as an older release left it
        c.execute("DELETE FROM registry_metadata WHERE key IN (?,?)", (strategy_bot.KEY, strategy_bot.OLD_KEY))
    from backend.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(api.app.state.store.settings)):  # the upgrade: a restart
        pass
    assert stored(api)[1]["strategy-owner"] == "Strategy Owner"
    with api.app.state.store.transaction() as c:
        assert strategy_bot._record(c)["on"] is True
        assert strategy_bot.migrate(c) is False  # once
        c.execute("UPDATE bots SET display_name='Chief' WHERE slug='strategy-owner'")
        c.execute("DELETE FROM registry_metadata WHERE key IN (?,?)", (strategy_bot.KEY, strategy_bot.OLD_KEY))
        assert strategy_bot.migrate(c) is True
    assert stored(api)[1]["strategy-owner"] == "Chief"  # a person's name is theirs
    # the settings an install from before stored under old keys move to the new ones
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (strategy_bot.KEY,))
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata(key,value) VALUES(?,?)",
            (strategy_bot.OLD_KEY, '{"migrated_at":"2026-01-01T00:00:00Z","on":true}'),
        )
        for person in ("human:ana", "human:ben"):
            c.execute(
                "INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,'true',?)",
                (person, strategy_bot.OLD_HIDE_PREF, H.now()),
            )
        c.execute(
            "INSERT INTO preferences(actor,key,value,updated_at) VALUES('human:ben',?,'false',?)",
            (strategy_bot.HIDE_PREF, H.shift(H.now(), minutes=1)),
        )
        # Cara chose under the new key, then an older release ran and she chose again under the old one.
        c.execute(
            "INSERT INTO preferences(actor,key,value,updated_at) VALUES('human:cara',?,'true',?)",
            (strategy_bot.HIDE_PREF, H.shift(H.now(), minutes=-5)),
        )
        c.execute(
            "INSERT INTO preferences(actor,key,value,updated_at) VALUES('human:cara',?,'false',?)",
            (strategy_bot.OLD_HIDE_PREF, H.now()),
        )
    with api.app.state.store.transaction() as c:
        assert strategy_bot.migrate(c) is False  # the moved record says it has run
        record = json.loads(
            c.execute("SELECT value FROM registry_metadata WHERE key=?", (strategy_bot.KEY,)).fetchone()[0]
        )
        old_rows = c.execute("SELECT COUNT(*) FROM registry_metadata WHERE key=?", (strategy_bot.OLD_KEY,)).fetchone()[
            0
        ]
        hidden = {
            r[0]: r[1] for r in c.execute("SELECT actor, value FROM preferences WHERE key=?", (strategy_bot.HIDE_PREF,))
        }
        left = {
            r[0]: r[1]
            for r in c.execute("SELECT actor, value FROM preferences WHERE key=?", (strategy_bot.OLD_HIDE_PREF,))
        }
    assert record["migrated_at"] == "2026-01-01T00:00:00Z" and old_rows == 1
    assert hidden == {"human:ana": "true", "human:ben": "false", "human:cara": "false"} and left == hidden
    # a level and checks chosen while an older release ran win over the record
    with api.app.state.store.transaction() as c:
        strategy_bot._save(c, {**strategy_bot._record(c), "level": 3, "checks": True, "review_settled": True})
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata(key,value) VALUES(?,?)",
            (strategy_bot.OLD_KEY, '{"on":true,"level":1,"checks":false}'),
        )
    with api.app.state.store.transaction() as c:
        strategy_bot.migrate(c)
        record = strategy_bot._record(c)
        old = json.loads(
            c.execute("SELECT value FROM registry_metadata WHERE key=?", (strategy_bot.OLD_KEY,)).fetchone()[0]
        )
    assert (record["level"], record["checks"], record.get("review_settled")) == (1, False, True)
    assert old == record
    # an older release after a rollback reads the owners settings and hide choice
    with api.app.state.store.transaction() as c:
        strategy_bot._save(c, {**strategy_bot._record(c), "level": 1, "checks": False})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM preferences WHERE key IN (?,?)", (strategy_bot.HIDE_PREF, strategy_bot.OLD_HIDE_PREF))
    r = api.post("/api/v2/preferences/" + strategy_bot.HIDE_PREF, json={"value": True}, headers=signed_in())
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        old = json.loads(
            c.execute("SELECT value FROM registry_metadata WHERE key=?", (strategy_bot.OLD_KEY,)).fetchone()[0]
        )
        hide = c.execute("SELECT value FROM preferences WHERE key=?", (strategy_bot.OLD_HIDE_PREF,)).fetchall()
        assert (old["level"], old["checks"]) == (1, False) and [tuple(h) for h in hide] == [("true",)]
        # A second start with nothing changed under the old keys leaves the record as it is.
        before = strategy_bot._record(c)
        strategy_bot.migrate(c)
        assert strategy_bot._record(c) == before
    # a withdrawn recovery reads the current note and another note is kept
    rows = (
        ("old-passive", strategy_bot.OLD_WITHDRAWN[0]),
        ("old-level-1", strategy_bot.OLD_WITHDRAWN[1]),
        ("declined", "Declined: not this quarter"),
    )
    with api.app.state.store.transaction() as c:
        for pid, note in rows:
            c.execute(
                "INSERT INTO goal_proposals(id,kind,proposed_by,proposed_at,status,decided_by,decided_at,"
                "decision_note) VALUES(?,'recovery','bot:strategy-owner',?,'rejected','human:ana',?,?)",
                (pid, H.now(), H.now(), note),
            )
    with api.app.state.store.transaction() as c:
        strategy_bot.migrate(c)
        notes = {
            r[0]: (r[1], r[2])
            for r in c.execute("SELECT id, decision_note, decided_by FROM goal_proposals WHERE kind='recovery'")
        }
    assert notes == {
        "old-passive": (strategy_suggest.WITHDRAWN, H.KEEPER),
        "old-level-1": (strategy_suggest.WITHDRAWN, H.KEEPER),
        "declined": ("Declined: not this quarter", "human:ana"),
    }
    # the name an older release gave it becomes the strategy bot
    for old in strategy_bot.OLD_NAMES:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bots SET display_name=? WHERE slug=?", (old, strategy_bot.SLUG))
            strategy_bot.migrate(c)
            assert H.bot(c, strategy_bot.SLUG)["display_name"] == strategy_bot.NAME, old
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Atlas' WHERE slug=?", (strategy_bot.SLUG,))
        strategy_bot.migrate(c)
        assert H.bot(c, strategy_bot.SLUG)["display_name"] == "Atlas"
