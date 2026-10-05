"""The shared market graph: seed, read, report, the Librarian curates."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


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


def test_show_find_edges_delta_and_a_report_does_not_write_the_graph(api, tmp_path):
    _seed(api, tmp_path)
    shown = get(api, "market/entities/company/northwind")
    assert shown["entity"]["id"] == "company/northwind"
    assert shown["edges"]["competes_with"]["out"]
    assert shown["edges"]["competes_with"]["in"] == []
    assert shown["evidence"]
    assert len(shown["events"]) <= 10
    acme = get(api, "market/entities/company/acme")
    assert acme["edges"]["competes_with"]["in"]
    assert get(api, "market/entities?q=Brightline%20YC")["entities"][0]["id"] == "company/brightline"
    both = get(api, "market/edges?src=company/acme&rel=competes_with")["edges"]
    assert any(edge["dst"] == "company/acme" and edge["src"] == "company/northwind" for edge in both)
    assert len(both) == len({edge["id"] for edge in both})
    before = get(api, "market/entities?type=company")["entities"]
    report = post(api, "market/insights", {"kind": "edge", "about": "Northwind",
                                           "claim": "Northwind changed a fee.", "confidence": "low"},
                  token="ben-test")["insight"]
    assert report["id"] and report["status"] == "new"
    _, _, listening = setup_attempt(api, "listening")
    other = post(api, "market/insights", {"kind": "other", "about": "Brightline",
                                          "claim": "Brightline was mentioned on a host thread.", "confidence": "medium"},
                 token=listening["token"])["insight"]
    assert other["id"]
    after = get(api, "market/entities?type=company")["entities"]
    assert [(row["id"], row["summary"]) for row in after] == [(row["id"], row["summary"]) for row in before]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM market_edges").fetchone()[0] == 6
    # as-of keeps an edge whose span covers the date
    evidence = post(api, "market/evidence", {"source_kind": "site", "quote": "ended", "our_read": "The partnership ended."})["evidence"]
    edge = post(api, "market/edges", {"src": "company/northwind", "rel": "partners_with", "dst": "company/acme",
                                      "since": "2020-01-01", "until": "2024-01-01", "confidence": "low",
                                      "evidence_ids": [evidence["id"]]})["edge"]
    covered = get(api, f"market/edges?src=company/northwind&rel=partners_with&as_of=2022-06-01")["edges"]
    assert edge["id"] in {row["id"] for row in covered}
    assert get(api, "market/edges?src=company/northwind&rel=partners_with&as_of=2025-01-01")["edges"] == []
    post(api, f"market/entities/company/northwind", {"summary": "A national manager.", "evidence_ids": [evidence["id"]]})
    grouped = get(api, "market/delta?since=1d")["entities"]
    assert "company/northwind" in {row["id"] for row in grouped}


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



def test_the_librarian_writes_the_first_map_and_another_bot_still_cannot(api, tmp_path):
    """The Librarian builds the first map from what the owner gave it (playbooks/market-setup.md): a
    report, then an apply that writes the company itself with an explicit id and a competitor with its
    tier and edge, then a page. A bot that is not a curator is still refused."""
    _seed(api, tmp_path)
    _librarian(api)
    _, _, librarian = setup_attempt(api, "librarian")
    token = librarian["token"]
    insight = post(api, "market/insights", {"kind": "new-entity", "about": "Acme Cleaning",
                   "claim": "Acme Cleaning sells office cleaning to property managers."}, token=token)["insight"]
    own = post(api, f"market/insights/{insight['id']}/apply", {
        "evidence": {"source_url": "https://acme.example", "source_kind": "site",
                     "quote": "Office cleaning for property managers.", "our_read": "The company's own page."},
        "entity": {"id": "company/self", "type": "company", "name": "Acme Cleaning",
                   "summary": "Office cleaning for property managers."}}, token=token)
    assert own["insight"]["status"] == "applied"
    assert get(api, "market/entities/company/self")["entity"]["created_by"] == "bot:librarian"
    rival = post(api, "market/insights", {"kind": "new-entity", "about": "CleanCo", "claim": "CleanCo competes."},
                 token=token)["insight"]
    post(api, f"market/insights/{rival['id']}/apply", {
        "evidence": {"source_url": "https://cleanco.example", "source_kind": "site", "quote": "Office cleaning."},
        "entity": {"type": "company", "name": "CleanCo", "tier": "core", "summary": "Office cleaning."},
        "edge": {"src": "company/cleanco", "rel": "competes_with", "dst": "company/self"}}, token=token)
    shown = get(api, "market/entities/company/cleanco")
    assert shown["entity"]["tier"] == "core" and shown["edges"]["competes_with"]["out"]
    def listed():
        rows = api.get("/api/company-docs?collection=market", headers=headers()).json()["documents"]
        return {row["id"]: row for row in rows}
    assert listed()["market/overview"].get("seeded") is True         # the seed's text: hidden while the graph is empty
    page = post(api, "market/pages/overview", {"body": "# Overview\n\nAcme sells cleaning. [Acme](https://acme.example)"},
                token=token)
    assert page["content"].startswith("# Overview")
    assert "seeded" not in listed()["market/overview"] and listed()["market/channels"]["seeded"] is True
    shown_page = api.get("/api/company-docs/market/overview", headers=headers()).json()
    assert "Acme sells cleaning" in shown_page["content"]
    _, _, listening = setup_attempt(api, "listening")
    post(api, "market/pages/overview", {"body": "no"}, token=listening["token"], expected=403)


def test_a_startup_fix_marks_the_untouched_seed_pages_of_an_older_install_as_seeded(api):
    """Installs made before pages carried `seeded` hide their "None in the seed." pages once, at startup; a page
    somebody wrote, or one the graph has outgrown, stays visible, and running it again marks nothing."""
    store = api.app.state.store
    with store.transaction() as c:
        M.seed(c, {}, Blobs(store.settings), document={})
        rows = c.execute("SELECT id, payload_json FROM documents WHERE collection='market'").fetchall()
        assert {row["id"] for row in rows} == {page[0] for page in M.PAGES} and len(rows) == 9
        for row in rows:
            payload = json.loads(row["payload_json"])
            assert payload.get("seeded") is True                       # a new install tags them at seed time
            payload.pop("seeded")                                      # what an older install stored
            c.execute("UPDATE documents SET payload_json=? WHERE id=?", (json.dumps(payload), row["id"]))
        # The curator's own theses page: not the seed's text.
        payload = json.loads(c.execute("SELECT payload_json FROM documents WHERE id='market/theses'").fetchone()[0])
        payload["content"] = "# Theses\n\nBuyers pay for speed. [Source](https://acme.example)"
        c.execute("UPDATE documents SET payload_json=? WHERE id='market/theses'", (json.dumps(payload),))

    def listed():
        rows = api.get("/api/company-docs?collection=market", headers=headers()).json()["documents"]
        return {row["id"]: row for row in rows}
    assert not any(row.get("seeded") for row in listed().values())
    with store.transaction() as c:
        marked = M.mark_seeded_pages(c)
    assert sorted(marked) == sorted(page[0] for page in M.PAGES if page[0] != "market/theses") and len(marked) == 8
    seen = listed()
    assert all(seen[doc_id].get("seeded") is True for doc_id in marked)
    assert "seeded" not in seen["market/theses"] and "Buyers pay" in api.get(
        "/api/company-docs/market/theses", headers=headers()).json()["content"]
    assert "None in the seed." in api.get("/api/company-docs/market/overview", headers=headers()).json()["content"]
    # Idempotent, and a start-up does the same thing on its own.
    with store.transaction() as c:
        assert M.mark_seeded_pages(c) == []
        c.execute("UPDATE documents SET payload_json=json_remove(payload_json,'$.seeded') WHERE id='market/channels'")
    store.initialize()
    assert listed()["market/channels"].get("seeded") is True
    # A page whose graph has moved on (a company added since) is not the seed's text any more and is left alone.
    with store.transaction() as c:
        M.create_entity(c, M.SEED_ACTOR, entity_id="company/acme", type="company", name="Acme", tier="core", summary="")
        c.execute("UPDATE documents SET payload_json=json_remove(payload_json,'$.seeded') WHERE id='market/coverage-universe'")
        assert M.mark_seeded_pages(c) == []
