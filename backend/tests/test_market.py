"""The shared market graph: seed, read, report, the Librarian curates."""

import json
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


def _jsonl(*rows):
    return "\n".join(json.dumps(row) for row in rows)


def _ev(quote):
    return [{"url": "https://turno.example/about", "quote": quote, "source_kind": "site", "captured_at": "2026-10-10"}]


def test_market_import_is_for_the_librarian_and_the_owner(api, tmp_path):
    _seed(api, tmp_path)
    lines = _jsonl({"kind": "entity", "type": "company", "name": "Turno", "evidence": _ev("Turno cleans.")})
    post(api, "market/import", {"lines": lines}, token="ben-test", expected=403)
    with api.app.state.store.read() as c:
        assert not M.entity(c, "company/turno")


def test_market_import_resolves_cites_skips_and_dry_runs(api, tmp_path):
    _seed(api, tmp_path)
    lines = _jsonl(
        {"kind": "edge", "src": "company/turno", "rel": "integrates_with", "dst": "company/brightline",
         "confidence": "high", "evidence": _ev("Turno integrates with Brightline.")},
        {"kind": "entity", "id": "company/turno", "type": "company", "name": "Turno", "aliases": ["Turno Inc"],
         "external_ids": {"domain": "turno.example"}, "summary": "Cleaning scheduling.", "evidence": _ev("Turno cleans.")},
        {"kind": "entity", "id": "company/brightline-inc", "type": "company", "name": "Brightline Inc",
         "external_ids": {"domain": "brightline.example"}, "summary": "", "properties": {"hq": "Austin"},
         "evidence": _ev("Brightline is in Austin.")},
        {"kind": "edge", "src": "company/northwind", "rel": "competes_with", "dst": "company/acme",
         "evidence": _ev("Northwind competes with Acme.")},
        {"kind": "edge", "src": "company/turno", "rel": "friends_with", "dst": "company/acme", "evidence": _ev("Friends.")},
        {"kind": "entity", "type": "company", "name": "Harborly", "aliases": ["Fernwood"], "summary": "Both?",
         "evidence": _ev("Harborly, formerly Fernwood.")},
        {"kind": "entity", "type": "product", "name": "Brightline Scheduler",
         "external_ids": {"domain": "brightline.example"}, "evidence": _ev("Brightline Scheduler books cleaners.")},
    )
    store = api.app.state.store
    with store.read() as c:
        before = c.execute("SELECT (SELECT COUNT(*) FROM market_events) + (SELECT COUNT(*) FROM market_insights)").fetchone()[0]
    preview = post(api, "market/import", {"lines": lines, "name": "batch.jsonl", "dry_run": True})
    with store.read() as c:
        assert c.execute("SELECT (SELECT COUNT(*) FROM market_events) + (SELECT COUNT(*) FROM market_insights)").fetchone()[0] == before
        assert not M.entity(c, "company/turno")
    done = post(api, "market/import", {"lines": lines, "name": "batch.jsonl", "note": "Research batch one."})
    counts = {k: done[k] for k in M.IMPORT_OUTCOMES}
    assert counts == {"created": 3, "updated": 1, "skipped": 1, "needs_human": 1, "refused": 1}
    assert {k: preview[k] for k in M.IMPORT_OUTCOMES} == counts
    assert [line["outcome"] for line in done["lines"]] == ["created", "created", "updated", "skipped", "refused",
                                                          "needs_human", "created"]
    with store.read() as c:
        assert M.entity(c, "product/brightline-scheduler")["type"] == "product"
        assert "Brightline Scheduler" not in M.entity(c, "company/brightline")["aliases"]
        assert M.entity(c, "company/harborly")["summary"] == "" and M.entity(c, "company/fernwood")["summary"] == ""
        brightline = M.entity(c, "company/brightline")
        assert brightline["properties"]["hq"] == "Austin" and "Brightline Inc" in brightline["aliases"]
        assert not M.entity(c, "company/brightline-inc")

        def cited(kind, cid):
            return {r[0] for r in c.execute("SELECT e.quote FROM market_citations m JOIN market_evidence e "
                                            "ON e.id=m.evidence_id WHERE m.claim_kind=? AND m.claim_id=?", (kind, cid))}
        assert "Turno cleans." in cited("entity", "company/turno")
        assert "Brightline is in Austin." in cited("entity", "company/brightline")
        assert "Northwind competes with Acme." in cited("edge", "edge-competes-northwind-acme")
        new_edge = M.edges_of(c, src="company/turno", rel="integrates_with")[0]
        assert "Turno integrates with Brightline." in cited("edge", new_edge["id"])
        row = M.insight(c, done["insight"])
        assert (row["status"], row["reported_by"], row["about"], row["claim"]) == ("applied", "human:ana", "batch.jsonl", "Research batch one.")
        assert c.execute("SELECT COUNT(*) FROM market_events WHERE insight_id=?", (row["id"],)).fetchone()[0] >= len(row["applied_events"]) > 0



