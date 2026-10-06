"""The shared market graph: seed, read, report, the Librarian curates."""

from pathlib import Path


from backend import market as M
from backend.blobs import Blobs
from backend.store import H
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "registry"


def _seed(api, tmp_path):
    store = api.app.state.store
    with store.transaction() as c:
        M.seed(c, {}, Blobs(store.settings), document=M.load_snapshot(FIXTURE_REGISTRY))
        if not H.bot(c, "listening"):
            c.execute("INSERT INTO bots (slug, display_name, runtime, model, effort, cwd, host, state, created) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      ("listening", "Listening", "grok", "grok-4.6", "high", "", "keeper", "active", H.now()))
        if not c.execute("SELECT 1 FROM bot_config WHERE bot='listening'").fetchone():
            c.execute("INSERT INTO bot_config (bot, config_json, team, operator, description, reports_to, repo) "
                      "VALUES (?,?,?,?,?,?,?)",
                      ("listening", "{}", "marketing", "ana", "Listening", "cmo", "emp-listening"))


def _librarian(api):
    with api.app.state.store.transaction() as c:
        if not H.bot(c, "librarian"):
            c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                      "VALUES('librarian','Librarian','fake','','','','keeper','active',?)", (H.now(),))
            c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('librarian','{}',NULL,'ana')")


def test_the_librarian_gets_the_market_routines_and_the_old_market_analyst_is_retired(api):
    """At boot the Librarian gets its daily curate pass and the urgent-insight routine, once; a deleted one stays
    deleted. A Market Analyst left from before is archived and its open tasks go to the Librarian."""
    _librarian(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('market-analyst','Market Analyst','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('market-analyst','{}',NULL,'ana')")
        task = H.task_create(c, "human:ana", "Check Northwind pricing", "", "bot:market-analyst", lint=False)
        M.ensure_curator(c)
        keys = {r["routine_key"]: r for r in c.execute("SELECT * FROM schedules WHERE bot='librarian'")}
        assert set(keys) >= {"curate-the-market", "urgent-market-insight"}
        assert keys["urgent-market-insight"]["cron"] in ("", None)
        assert H.bot(c, "market-analyst")["state"] == "archived"
        assert c.execute("SELECT owner FROM tasks WHERE id=?", (task["id"],)).fetchone()["owner"] == "bot:librarian"
        c.execute("UPDATE schedules SET deleted_at=? WHERE bot='librarian' AND routine_key='curate-the-market'", (H.now(),))
        M.ensure_curator(c)
        again = c.execute("SELECT COUNT(*) FROM schedules WHERE bot='librarian' AND routine_key='curate-the-market'").fetchone()[0]
        assert again == 1 and c.execute("SELECT deleted_at FROM schedules WHERE bot='librarian' "
                                        "AND routine_key='curate-the-market'").fetchone()[0]


def test_the_server_enforces_writers_evidence_vocabulary_and_no_delete(api, tmp_path):
    _seed(api, tmp_path)
    body = {"type": "company", "name": "Newco", "summary": "A company."}
    post(api, "market/entities", body, token="ben-test", expected=403)
    post(api, "market/entities", body, expected=422)
    evidence = post(api, "market/evidence", {"source_kind": "news", "quote": "Newco launched.", "our_read": "A new company."})["evidence"]
    post(api, "market/edges", {"src": "company/northwind", "rel": "friends_with", "dst": "company/acme",
                               "evidence_ids": [evidence["id"]]}, expected=422)
    post(api, "market/edges", {"src": "company/northwind", "rel": "partners_with", "dst": "company/acme",
                               "since": "2026-05-01", "until": "2026-01-01", "evidence_ids": [evidence["id"]]}, expected=422)
    post(api, "market/entities", {"type": "company", "name": "Also Northwind", "aliases": ["Northwind"],
                                  "evidence_ids": [evidence["id"]]}, expected=422)
    forced = post(api, "market/entities", {"type": "company", "name": "Also Northwind", "aliases": ["Northwind"],
                                           "evidence_ids": [evidence["id"]], "force": True})["entity"]
    post(api, f"market/entities/{forced['id']}", {"aliases": ["Northwind", "also-northwind"]})
    post(api, f"market/entities/{forced['id']}", {"summary": "No evidence."}, expected=422)
    merged = post(api, f"market/entities/{forced['id']}/merge", {"into": "company/northwind"})["entity"]
    assert merged["status"] == "merged" and merged["merged_into"] == "company/northwind"
    assert get(api, f"market/entities/{forced['id']}")["entity"]["id"] == forced["id"]
    retired = post(api, "market/entities/company/pricewise/retire", {})["entity"]
    assert retired["status"] == "retired"
    assert get(api, "market/entities/company/pricewise")["entity"]["status"] == "retired"
    denied = api.delete("/api/v2/market/entities/company/northwind", headers=headers())
    assert denied.status_code == 405
    assert get(api, "market/entities/company/northwind")["entity"]["name"] == "Northwind"
    _librarian(api)
    _, _, librarian = setup_attempt(api, "librarian")
    created = post(api, "market/entities", {"type": "company", "name": "Curated Co", "evidence_ids": [evidence["id"]]},
                   token=librarian["token"])["entity"]
    assert created["created_by"] == "bot:librarian"
    assert get(api, "market/insights", token=librarian["token"])["insights"] is not None   # the queue is the Librarian's
    _, _, listening = setup_attempt(api, "listening")
    get(api, "market/insights", token=listening["token"], expected=403)



