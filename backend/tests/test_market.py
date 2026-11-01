"""The shared market graph: seed, read, report, the Librarian curates."""

import json
from datetime import datetime, timezone
from pathlib import Path


from backend import market as M
from backend.blobs import Blobs
from backend.store import H
from backend.tests.test_api import api, get, headers, post, runner, setup_attempt  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "registry"


def _seed(api, tmp_path):
    store = api.app.state.store
    with store.transaction() as c:
        M.seed(c, {}, Blobs(store.settings), document=M.load_snapshot(FIXTURE_REGISTRY))
        if not H.bot(c, "listening"):
            c.execute("INSERT INTO bots (id, slug, display_name, runtime, model, effort, cwd, host, state, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      ("listening", "listening", "Listening", "grok", "grok-4.6", "high", "", "keeper", "active", H.now()))
        if not c.execute("SELECT 1 FROM bot_config WHERE bot='listening'").fetchone():
            c.execute("INSERT INTO bot_config (bot, config, team, operator, description, reports_to, repo) "
                      "VALUES (?,?,?,?,?,?,?)",
                      ("listening", "{}", "marketing", "ana", "Listening", "cmo", "emp-listening"))


def _librarian(api):
    with api.app.state.store.transaction() as c:
        if not H.bot(c, "librarian"):
            c.execute("INSERT INTO bots(id,slug,display_name,runtime,model,effort,cwd,host,state,created_at) "
                      "VALUES('librarian','librarian','Librarian','fake','','','','keeper','active',?)", (H.now(),))
            c.execute("INSERT INTO bot_config(bot,config,team,operator) VALUES('librarian','{}',NULL,'ana')")


def test_the_librarian_gets_the_market_routines_and_the_old_market_analyst_is_retired(api):
    """At boot the Librarian gets its daily curate pass and the urgent-insight routine, once; a deleted one stays
    deleted. A Market Analyst left from before is archived and its open tasks go to the Librarian."""
    _librarian(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,runtime,model,effort,cwd,host,state,created_at) "
                  "VALUES('market-analyst','market-analyst','Market Analyst','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,team,operator) VALUES('market-analyst','{}',NULL,'ana')")
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


def test_market_import_refuses_a_batch_over_the_line_cap(api, tmp_path):
    _seed(api, tmp_path)
    one = _jsonl({"kind": "entity", "type": "company", "name": "Turno", "evidence": _ev("Turno cleans.")}).strip()
    lines = "\n".join([one] * (M.IMPORT_MAX_LINES + 1)) + "\n\n"
    with api.app.state.store.read() as c:
        before = c.execute("SELECT COUNT(*) FROM market_insights").fetchone()[0]
    body = post(api, "market/import", {"lines": lines}, expected=413)
    assert body["error"]["code"] == "too_many_lines" and str(M.IMPORT_MAX_LINES) in body["error"]["detail"]
    with api.app.state.store.read() as c:
        assert not M.entity(c, "company/turno")
        assert c.execute("SELECT COUNT(*) FROM market_insights").fetchone()[0] == before


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





def test_a_market_ask_is_audited_and_capped_per_person_per_day(api, tmp_path, monkeypatch):
    """The ask spends the server's Gemini key: one `market.ask` event per model call, never the question, and a
    clear 429 once the person's daily cap is used. With no key the pulled excerpts are still the answer."""
    _seed(api, tmp_path)
    store = api.app.state.store
    sent = []

    def complete(question, excerpts):
        sent.append(question)
        return "Northwind sells pricing software."

    monkeypatch.setattr(M, "complete", complete)
    store.settings.decisions_daily_calls_owner = 1
    out = post(api, "market/ask", {"question": "What does Northwind sell?"})
    assert out["answer"] == "Northwind sells pricing software."
    with store.read() as c:
        rows = [json.loads(r["detail"]) for r in c.execute(
            "SELECT detail FROM events WHERE action='market.ask' AND actor='human:ana'")]
    assert len(rows) == 1 and rows[0]["model"] == M.ASK_MODEL and "Northwind" not in json.dumps(rows[0])
    over = api.post("/api/v2/market/ask", json={"question": "What does Northwind sell?"}, headers=headers())
    assert over.status_code == 429 and over.json()["error"]["code"] == "budget" and len(sent) == 1

    from clients import docs_qa

    def no_key(question, excerpts):
        raise docs_qa.NoKey("no key")

    monkeypatch.setattr(M, "complete", no_key)
    store.settings.decisions_daily_calls_owner = 5
    out = post(api, "market/ask", {"question": "What does Northwind sell?"})
    assert out["answer"].startswith("Pulled from the market graph.")
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='market.ask'").fetchone()[0] == 1


def test_graph_reads_share_market_permissions_and_hide_inactive_entities(api):
    with api.app.state.store.transaction() as c:
        for name in ('Self', 'Neighbor', 'Retired', 'Merged'):
            M.create_entity(c, M.SEED_ACTOR, type='company', name=name)
        M.create_edge(c, M.SEED_ACTOR, src='company/self', dst='company/neighbor', rel='employs')
        M.create_edge(c, M.SEED_ACTOR, src='company/self', dst='company/retired', rel='employs')
        M.create_edge(c, M.SEED_ACTOR, src='company/merged', dst='company/self', rel='employs')
        M.create_edge(c, M.SEED_ACTOR, src='company/self', dst='company/neighbor', rel='formerly', until='2000-01-01')
        c.execute("UPDATE market_entities SET status='retired' WHERE id='company/retired'")
        c.execute("UPDATE market_entities SET status='merged' WHERE id='company/merged'")
    get(api, 'market/graph', token='invalid-token', expected=401)
    computer = runner(api)
    get(api, 'market/graph', token=computer['token'], expected=403)
    body = get(api, 'market/graph?fill=true', token='ben-test')
    assert {r['id'] for r in body['entities']} == {'company/self', 'company/neighbor'}
    assert [r['degree'] for r in body['entities']] == [1, 1]
    assert len(body['edges']) == 1
    assert not get(api, 'market/graph?focus=company/retired')['entities']


def test_graph_and_entity_pages_are_bounded_and_focus_works_beyond_the_first_page(api):
    with api.app.state.store.transaction() as c:
        for i in range(16):
            M.create_entity(c, M.SEED_ACTOR, type='company', name=f'Node {i:02}', entity_id=f'company/n{i}')
        for i in range(12):
            for j in range(12):
                if i != j:
                    M.create_edge(c, M.SEED_ACTOR, src=f'company/n{i}', dst=f'company/n{j}', rel='employs')
        M.create_edge(c, M.SEED_ACTOR, src='company/n15', dst='company/n0', rel='employs')
        M.update_entity(c, M.SEED_ACTOR, 'company/n15', aliases=['Élan'])
    body = get(api, 'market/graph?focus=company/n0&limit=10')
    assert len(body['entities']) == 10 and body['entities'][0]['id'] == 'company/n0'
    assert len(body['edges']) == 80 and body['edges_truncated']
    ids = {r['id'] for r in body['entities']}
    assert all(e['src'] in ids and e['dst'] in ids for e in body['edges'])
    assert len(get(api, 'market/graph?fill=true&limit=16')['entities']) == 16
    cited = get(api, 'market/graph?focus=company/n0&include=company/n14&limit=10')
    assert len(cited['entities']) == 10 and cited['entities'][1]['id'] == 'company/n14'
    assert {r['id'] for r in get(api, 'market/graph?focus=company/n15')['entities']} == {'company/n15', 'company/n0'}
    first = get(api, 'market/entities?status=active&limit=5')
    second = get(api, 'market/entities?status=active&limit=5&offset=5')
    assert first['total'] == 16 and first['next_offset'] == 5
    assert not {r['id'] for r in first['entities']} & {r['id'] for r in second['entities']}
    assert get(api, 'market/entities?q=n15&status=active&limit=5')['entities'][0]['id'] == 'company/n15'
    assert get(api, 'market/entities?q=élan&status=active&limit=5')['entities'][0]['id'] == 'company/n15'
    get(api, 'market/graph?limit=301', expected=422)
    get(api, 'market/entities?limit=501', expected=422)
    assert len(get(api, 'market/entities')['entities']) == 16


def test_the_daily_curate_pass_is_skipped_when_it_has_nothing_to_read(api):
    """No new insight and no Listening for the Librarian on a weekday: no task, the skip on record as
    `automation.skipped`. An insight brings it back."""
    import backend.routines as routines
    from backend.scheduler import Scheduler
    _librarian(api)
    store = api.app.state.store
    with store.transaction() as c:
        M.ensure_curator(c)
        aid = routines.row(c, "librarian:curate-the-market")["automation"]       # the routine is its automation now
        c.execute("UPDATE automations SET next_due=? WHERE id=?", ("2026-10-12T00:00:00.000000Z", aid))
    tick = lambda at: Scheduler(store, api.app.state.execution).tick(at)
    tick(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc))            # Tuesday, 05:00 in Los Angeles
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner='bot:librarian'").fetchone()
        assert not c.execute("SELECT 1 FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()
        skipped = [json.loads(r[0]) for r in c.execute(
            "SELECT detail FROM events WHERE action='automation.skipped' AND target=?", (aid,))]
    assert [("Listening" in d["reason"], d["at"]) for d in skipped] == [(True, "2026-10-12T00:00:00.000000Z")]
    with store.transaction() as c:
        c.execute("INSERT INTO market_insights(id,reported_by,reported_at,kind) VALUES('i1','bot:ops',?,'pricing')",
                  (H.now(),))
    tick(datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))
    with store.read() as c:
        assert c.execute("SELECT title FROM tasks WHERE owner='bot:librarian'").fetchone()[0] == "Curate the market"
    # A team that rewrote the automation's words gave it other work: it runs with nothing new to curate.
    with store.transaction() as c:
        c.execute("UPDATE market_insights SET status='applied'")
        c.execute("UPDATE tasks SET status='done' WHERE owner='bot:librarian'")
    flow = get(api, f"automations/{aid}")
    steps = [{**step, "prompt": "Curate the market, then tidy the glossary."} for step in flow["steps"]]
    r = api.patch(f"/api/v2/automations/{aid}", json={"steps": steps}, headers=headers())
    assert r.status_code == 200, r.text
    tick(datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE owner='bot:librarian'").fetchone()[0] == 2


def test_applying_an_insight_makes_an_entity_and_sets_its_verified_date(api, tmp_path):
    _seed(api, tmp_path)
    evidence = {"source_kind": "page-change", "source_url": "https://acme.example/pricing", "quote": "Pro is $12",
                "our_read": "A price rise."}
    with api.app.state.store.transaction() as c:
        for insight in ("i1", "i2"):
            c.execute("INSERT INTO market_insights(id,reported_by,reported_at,kind) VALUES(?,'bot:ops',?,'pricing')",
                      (insight, H.now()))
    post(api, "market/insights/i1/apply", {"evidence": evidence, "entity": {
        "id": "company/contoso", "type": "company", "name": "Contoso", "last_verified_at": "2026-10-01"}})
    post(api, "market/insights/i2/apply", {"evidence": evidence, "entity_id": "company/contoso",
                                           "last_verified_at": "2026-10-02"})
    with api.app.state.store.read() as c:
        row = c.execute("SELECT last_verified_at FROM market_entities WHERE id='company/contoso'").fetchone()
    assert row[0] == "2026-10-02"
