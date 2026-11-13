"""Growth fills its own numbers from a database query (task #307, part 2 of the synced sources): connecting a database
query source saves which named query it runs and a daily scheduled turn by the bot granted that database; that bot's
`tico growth source run` runs the query on its own computer through `tico db` and posts the rows, or the reason it
failed, to the source. Acme, a home-cleaning company, counts its sign-ups per channel in its own SQLite warehouse."""

import json
import sqlite3
from urllib.parse import urlencode

import yaml

from backend import growth_sync as GS
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.company import bot
from backend.tests.helpers.databases import workspace
from clients.commands import strategy
from clients.tico import APIError

NOW = GS.this_month()
LAST = GS._month(GS._index(NOW) - 1)

PAGE = """---
service: acme
title: Acme warehouse
kind: sql
summary: Acme's sign-ups, read through `tico db acme`.
access: "`tico db acme --query <id>`"
credentials:
  - DB_ACME_URL
declared_as: |
  - service: sqlite
    database: acme
    can: [read]
writes: never
owner: ana
---

## What it is
Acme's warehouse.

## What data it has
Sign-ups by month and channel.

## How a bot uses it
Named queries.

## Rules
Read only.

## Recipes
None.

## Gotchas
None.

## Learnings
None.
"""

QUERIES = {
    "queries": [
        {
            "id": "signups-by-channel",
            "title": "Sign-ups by channel and month",
            "description": "New customers per channel per month.",
            "category": "growth",
            "tags": ["signups"],
            "database": "acme",
            "sql": "SELECT month, channel, count(*) AS signups FROM signups GROUP BY 1, 2 ORDER BY 1, 2",
            "params": [],
        },
        {
            "id": "signups-wrong-column",
            "title": "Sign-ups by source (the column is not channel)",
            "description": "A query that does not return a channel column.",
            "category": "growth",
            "tags": ["signups"],
            "database": "acme",
            "sql": "SELECT month, channel AS source, count(*) AS signups FROM signups GROUP BY 1, 2",
            "params": [],
        },
        {
            "id": "signups-since",
            "title": "Sign-ups since a date",
            "description": "Needs a date, so a daily run cannot run it.",
            "category": "growth",
            "tags": ["signups"],
            "database": "acme",
            "sql": "SELECT month, channel, count(*) AS signups FROM signups WHERE month >= :since GROUP BY 1, 2",
            "params": [{"name": "since", "type": "text", "label": "Since", "required": True}],
        },
    ]
}


def catalog(api):
    """Acme's private config: the warehouse page and its named queries, layered over the release's."""
    root = api.app.state.store.settings.registry_dir / "integrations"
    (root / "queries").mkdir(parents=True, exist_ok=True)
    (root / "acme.md").write_text(PAGE)
    (root / "queries" / "acme.yaml").write_text(yaml.safe_dump(QUERIES))
    api.app.state.integrations._cache = None


def warehouse(tmp_path):
    path = tmp_path / "acme.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE signups (id INTEGER PRIMARY KEY, month TEXT, channel TEXT)")
    rows = [(LAST, "search-ads")] * 131 + [(LAST, "referrals")] * 12 + [(NOW, "search-ads")] * 40
    db.executemany("INSERT INTO signups (month, channel) VALUES (?, ?)", rows)
    db.commit()
    db.close()
    return path


def channels(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"})


def connect(api, query="signups-by-channel", token="ana-test", expected=200, **more):
    body = {
        "truth_for": "new customers",
        "system": "Signups (warehouse)",
        "reach_kind": "credential",
        "reach_ref": "DB_ACME_URL",
        "owner_ref": "human:ben",
        "sync_kind": "db_query",
        "sync_map": {"fields": {"new_customers": "signups"}, "database": "acme", "query": query, "bot": "ops"},
        **more,
    }
    return post(api, "growth/sources", body, token=token, expected=expected)


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def automation(api, aid):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()
    return dict(row) if row else None


class Hub:
    """The tico client a bot's computer has, over the test server, as that bot."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def _answer(self, r):
        data = r.json()
        if r.status_code != 200:
            error = data.get("error") or {}
            raise APIError(error.get("code", "error"), error.get("detail", r.text), r.status_code)
        return data

    def get(self, path, **query):
        query = {k: v for k, v in query.items() if v is not None}
        url = "/api/v2/" + path + ("?" + urlencode(query) if query else "")
        return self._answer(self.api.get(url, headers=headers(self.token)))

    def post(self, path, body, key=None):
        return self._answer(self.api.post("/api/v2/" + path, json=body, headers=headers(self.token, key)))


def on_the_bots_computer(monkeypatch, tmp_path, url):
    """The bot's checkout declares the acme database, and its run holds the connection string."""
    grant = {"service": "sqlite", "database": "acme", "can": ["read"], "identity": "file"}
    for name, value in workspace(tmp_path, "ops", [grant]).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DB_ACME_URL", url)


def test_connecting_saves_the_named_query_and_a_daily_turn_by_the_granted_bot(api):
    """Connecting a database query source keeps its database, query and bot, and makes a daily schedule automation
    whose one step is that bot's turn running `tico growth source run <id>`."""
    catalog(api)
    channels(api)
    bot(api, "ops")
    src = connect(api)
    assert (src["kind"], src["fills"], src["health"]) == ("db_query", ["new_customers"], "waiting")
    assert (src["map"]["database"], src["map"]["query"], src["map"]["bot"]) == ("acme", "signups-by-channel", "ops")
    row = automation(api, src["map"]["automation"])
    flow = json.loads(row["flow"])
    assert row["enabled"] == 1 and row["owner_id"] == "bot:ops" and row["next_due_at"]
    assert flow["trigger"] == {**flow["trigger"], "type": "schedule", "cron": GS.DB_CRON, "tz": "UTC"}
    (step,) = flow["steps"]
    assert step["type"] == "bot_turn" and step["bot"] == "bot:ops"
    assert f"tico growth source run {src['id']}" in step["prompt"]
    # Retiring the source removes its daily run.
    post(api, f"growth/sources/{src['id']}/retire", {})
    assert automation(api, src["map"]["automation"])["deleted_at"]


def test_a_query_the_catalog_lacks_or_one_that_needs_a_parameter_is_refused(api):
    catalog(api)
    bot(api, "ops")
    missing = connect(api, query="no-such-query", expected=422)
    assert "No query no-such-query in the acme catalog" in missing["error"]["detail"]
    needs = connect(api, query="signups-since", expected=422)
    assert "needs since" in needs["error"]["detail"]
    nobot = connect(
        api,
        expected=422,
        sync_map={
            "fields": {"new_customers": "signups"},
            "database": "acme",
            "query": "signups-by-channel",
            "bot": "nobody",
        },
    )
    assert "No bot nobody" in nobot["error"]["detail"]
    half = connect(api, expected=422, sync_map={"fields": {"new_customers": "signups"}, "database": "acme"})
    assert "names its database, query and bot" in half["error"]["detail"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM gtm_sources").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM automations WHERE name LIKE 'Growth:%'").fetchone()[0] == 0


def test_the_daily_run_fills_new_customers_from_the_query_marked_with_the_source_and_run(api, tmp_path, monkeypatch):
    """AC-6: the bot's run returns month, channel and new_customers; those channels and months fill, each number marked
    with the source and the run time."""
    catalog(api)
    channels(api)
    token = bot(api, "ops")
    src = connect(api)
    on_the_bots_computer(monkeypatch, tmp_path, f"sqlite:///{warehouse(tmp_path)}")
    result = strategy.growth_source_run(Hub(api, token), {"id": src["id"]})
    assert result["ok"] and result["written"] == 3 and result["not_written"] == []
    search = get(api, f"growth/cohorts?channel=search-ads&month={LAST}")["cohorts"][0]
    assert search["new_customers"] == 131
    origin = search["origins"]["new_customers"]
    assert (origin["origin"], origin["quality"], origin["source"]) == ("synced", "actual", "Signups (warehouse)")
    assert origin["at"]
    assert get(api, f"growth/cohorts?channel=referrals&month={LAST}")["cohorts"][0]["new_customers"] == 12
    assert get(api, f"growth/cohorts?channel=search-ads&month={NOW}")["cohorts"][0]["new_customers"] == 40
    listed = get(api, "growth/sources")["sources"][0]
    assert listed["health"] == "fresh" and listed["last_good_run"] and listed["last_error"] is None
    with api.app.state.store.read() as c:
        audit = c.execute("SELECT detail FROM events WHERE action='db.query' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert audit and "signups-by-channel" in audit[0]  # the query is on record as any `tico db` query is


def test_a_failing_query_shows_failing_with_the_reason_keeps_every_number_and_tells_the_owner_once(
    api, tmp_path, monkeypatch
):
    """AC-7: a wrong column, a login refused (here: a database the run cannot open) or any other failure ends the run
    failed; the source shows Failing with the reason, the numbers it wrote stay, and its owner gets one notice."""
    catalog(api)
    channels(api)
    token = bot(api, "ops")
    src = connect(api)
    hub = Hub(api, token)
    on_the_bots_computer(monkeypatch, tmp_path, f"sqlite:///{warehouse(tmp_path)}")
    assert strategy.growth_source_run(hub, {"id": src["id"]})["ok"]

    # The database refuses the run: the file is gone.
    on_the_bots_computer(monkeypatch, tmp_path, f"sqlite:///{tmp_path / 'gone.db'}")
    refused = strategy.growth_source_run(hub, {"id": src["id"]})
    assert refused["ok"] is False and refused["written"] == 0
    assert refused["error"].startswith("acme signups-by-channel: ")
    listed = get(api, "growth/sources")["sources"][0]
    assert listed["health"] == "failing" and listed["last_error"] == refused["error"] and listed["health_since"]

    # The query is changed to one that has no channel column: a second failure, no second notice.
    on_the_bots_computer(monkeypatch, tmp_path, f"sqlite:///{tmp_path / 'acme.db'}")
    patch(
        api,
        f"growth/sources/{src['id']}",
        {"sync_map": {"fields": {"new_customers": "signups"}, "query": "signups-wrong-column"}},
    )
    wrong = strategy.growth_source_run(hub, {"id": src["id"]})
    assert wrong["ok"] is False and wrong["error"] == 'column "channel" not found'
    assert get(api, f"growth/cohorts?channel=search-ads&month={LAST}")["cohorts"][0]["new_customers"] == 131
    with api.app.state.store.read() as c:
        notices = [
            r[0]
            for r in c.execute(
                "SELECT body FROM messages WHERE kind='notice' AND recipient_id='human:ben' ORDER BY seq"
            )
        ]
    assert len(notices) == 1 and "stopped syncing" in notices[0]


def test_only_the_sources_bot_owner_or_an_admin_posts_its_rows(api):
    catalog(api)
    channels(api)
    bot(api, "ops")
    other = bot(api, "cpo")
    src = connect(api)
    body = {"rows": [{"month": NOW, "channel": "search-ads", "signups": 3}]}
    post(api, f"growth/sources/{src['id']}/rows", body, token=other, expected=403)
    post(api, f"growth/sources/{src['id']}/rows", body, token="ops-test")


def test_sync_starts_the_bots_run_now(api):
    """AC-17 for a database query source: `tico growth source sync` cannot run the query on the server, so it starts
    the daily run's bot turn now."""
    catalog(api)
    bot(api, "ops")
    src = connect(api)
    started = post(api, f"growth/sources/{src['id']}/sync", {})
    assert started["started"] and "signups-by-channel runs on acme now" in started["text"]
    with api.app.state.store.read() as c:
        runs = c.execute(
            "SELECT count(*) FROM automation_runs WHERE automation_id=?", (src["map"]["automation"],)
        ).fetchone()[0]
    assert runs == 1


def test_changing_the_bot_moves_the_daily_turn_and_a_file_source_has_none(api):
    catalog(api)
    bot(api, "ops")
    bot(api, "cpo")
    src = connect(api)
    aid = src["map"]["automation"]
    moved = patch(
        api,
        f"growth/sources/{src['id']}",
        {"sync_map": {"fields": {"new_customers": "signups"}, "bot": "cpo"}},
    )
    assert moved["map"]["automation"] == aid and moved["map"]["bot"] == "cpo"
    flow = json.loads(automation(api, aid)["flow"])
    assert flow["owner"] == "bot:cpo" and flow["steps"][0]["bot"] == "bot:cpo"
    patch(api, f"growth/sources/{src['id']}", {"sync_kind": "file", "sync_map": {"fields": {"new_customers": "n"}}})
    assert automation(api, aid)["deleted_at"]
