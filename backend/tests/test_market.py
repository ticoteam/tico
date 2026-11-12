"""The shared market graph: seed, read, report, the Librarian curates."""

import json
from datetime import datetime, timezone
from pathlib import Path


from backend import market as M
from backend.store import H
from backend.tests.helpers.api import get, headers, post, runner, setup_attempt  # noqa: F401
from backend import bot_settings as BS
from backend.tests.helpers.market import _librarian, _seed

ROOT = Path(__file__).resolve().parents[2]


def test_the_librarian_gets_the_market_routines_and_the_old_market_analyst_is_retired(api):
    """At boot the Librarian gets its daily curate pass and the urgent-insight routine, once; a deleted one stays
    deleted. A Market Analyst left from before is archived and its open tasks go to the Librarian."""
    _librarian(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('market-analyst','market-analyst','Market Analyst','','active',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('market-analyst',NULL,'ana')")
        BS.write(c, 'market-analyst', {"runtime": "fake", "host": "keeper"})
        task = H.task_create(c, "human:ana", "Check Northwind pricing", "", "bot:market-analyst", lint=False)
        M.ensure_curator(c)
        keys = {r["routine_key"]: r for r in c.execute("SELECT * FROM schedules WHERE bot='librarian'")}
        assert set(keys) >= {"curate-the-market", "urgent-market-insight"}
        assert keys["urgent-market-insight"]["cron"] in ("", None)
        assert H.bot(c, "market-analyst")["state"] == "archived"
        assert (
            c.execute("SELECT owner_id FROM tasks WHERE id=?", (task["id"],)).fetchone()["owner_id"] == "bot:librarian"
        )
        c.execute(
            "UPDATE schedules SET deleted_at=? WHERE bot='librarian' AND routine_key='curate-the-market'", (H.now(),)
        )
        M.ensure_curator(c)
        again = c.execute(
            "SELECT COUNT(*) FROM schedules WHERE bot='librarian' AND routine_key='curate-the-market'"
        ).fetchone()[0]
        assert (
            again == 1
            and c.execute(
                "SELECT deleted_at FROM schedules WHERE bot='librarian' AND routine_key='curate-the-market'"
            ).fetchone()[0]
        )


def test_the_server_enforces_writers_evidence_vocabulary_and_no_delete(api, tmp_path):
    _seed(api, tmp_path)
    body = {"type": "organization", "name": "Newco", "summary": "A company."}
    post(api, "market/entities", body, token="ben-test", expected=403)
    post(api, "market/entities", body, expected=422)
    evidence = post(
        api, "market/evidence", {"source_kind": "news", "quote": "Newco launched.", "our_read": "A new company."}
    )["evidence"]
    post(
        api,
        "market/edges",
        {"src": "company/northwind", "rel": "friends_with", "dst": "company/acme", "evidence_ids": [evidence["id"]]},
        expected=422,
    )
    post(
        api,
        "market/edges",
        {
            "src": "company/northwind",
            "rel": "partners_with",
            "dst": "company/acme",
            "since": "2026-05-01",
            "until": "2026-01-01",
            "evidence_ids": [evidence["id"]],
        },
        expected=422,
    )
    post(
        api,
        "market/entities",
        {"type": "organization", "name": "Also Northwind", "aliases": ["Northwind"], "evidence_ids": [evidence["id"]]},
        expected=422,
    )
    forced = post(
        api,
        "market/entities",
        {
            "type": "organization",
            "name": "Also Northwind",
            "aliases": ["Northwind"],
            "evidence_ids": [evidence["id"]],
            "force": True,
        },
    )["entity"]
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
    created = post(
        api,
        "market/entities",
        {"type": "organization", "name": "Curated Co", "evidence_ids": [evidence["id"]]},
        token=librarian["token"],
    )["entity"]
    assert created["created_by"] == "bot:librarian"
    assert get(api, "market/insights", token=librarian["token"])["insights"] is not None  # the queue is the Librarian's
    _, _, listening = setup_attempt(api, "social-media-manager")
    get(api, "market/insights", token=listening["token"], expected=403)


def _jsonl(*rows):
    return "\n".join(json.dumps(row) for row in rows)


def _ev(quote):
    return [{"url": "https://turno.example/about", "quote": quote, "source_kind": "site", "captured_at": "2026-10-10"}]


def test_market_import_is_for_the_librarian_and_the_owner(api, tmp_path):
    _seed(api, tmp_path)
    lines = _jsonl({"kind": "entity", "type": "organization", "name": "Turno", "evidence": _ev("Turno cleans.")})
    post(api, "market/import", {"lines": lines}, token="ben-test", expected=403)
    with api.app.state.store.read() as c:
        assert not M.entity(c, "company/turno")


def test_market_import_refuses_a_batch_over_the_line_cap(api, tmp_path):
    _seed(api, tmp_path)
    one = _jsonl({"kind": "entity", "type": "organization", "name": "Turno", "evidence": _ev("Turno cleans.")}).strip()
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
        {
            "kind": "edge",
            "src": "company/turno",
            "rel": "integrates_with",
            "dst": "company/brightline",
            "confidence": "high",
            "evidence": _ev("Turno integrates with Brightline."),
        },
        {
            "kind": "entity",
            "id": "company/turno",
            "type": "organization",
            "name": "Turno",
            "aliases": ["Turno Inc"],
            "external_ids": {"domain": "turno.example"},
            "summary": "Cleaning scheduling.",
            "evidence": _ev("Turno cleans."),
        },
        {
            "kind": "entity",
            "id": "company/brightline-inc",
            "type": "organization",
            "name": "Brightline Inc",
            "external_ids": {"domain": "brightline.example"},
            "summary": "",
            "properties": {"hq": "Austin"},
            "evidence": _ev("Brightline is in Austin."),
        },
        {
            "kind": "edge",
            "src": "company/northwind",
            "rel": "competes_with",
            "dst": "company/acme",
            "evidence": _ev("Northwind competes with Acme."),
        },
        {
            "kind": "edge",
            "src": "company/turno",
            "rel": "friends_with",
            "dst": "company/acme",
            "evidence": _ev("Friends."),
        },
        {
            "kind": "entity",
            "type": "organization",
            "name": "Harborly",
            "aliases": ["Fernwood"],
            "summary": "Both?",
            "evidence": _ev("Harborly, formerly Fernwood."),
        },
        {
            "kind": "entity",
            "type": "product",
            "name": "Brightline Scheduler",
            "external_ids": {"domain": "brightline.example"},
            "evidence": _ev("Brightline Scheduler books cleaners."),
        },
    )
    store = api.app.state.store
    with store.read() as c:
        before = c.execute(
            "SELECT (SELECT COUNT(*) FROM history_events WHERE subject_type IN ('market_entity','market_edge','market_evidence','market_document')) + (SELECT COUNT(*) FROM market_insights)"
        ).fetchone()[0]
    preview = post(api, "market/import", {"lines": lines, "name": "batch.jsonl", "dry_run": True})
    with store.read() as c:
        assert (
            c.execute(
                "SELECT (SELECT COUNT(*) FROM history_events WHERE subject_type IN ('market_entity','market_edge','market_evidence','market_document')) + (SELECT COUNT(*) FROM market_insights)"
            ).fetchone()[0]
            == before
        )
        assert not M.entity(c, "company/turno")
    done = post(api, "market/import", {"lines": lines, "name": "batch.jsonl", "note": "Research batch one."})
    counts = {k: done[k] for k in M.IMPORT_OUTCOMES}
    assert counts == {"created": 3, "updated": 1, "skipped": 1, "needs_human": 1, "refused": 1}
    assert {k: preview[k] for k in M.IMPORT_OUTCOMES} == counts
    assert [line["outcome"] for line in done["lines"]] == [
        "created",
        "created",
        "updated",
        "skipped",
        "refused",
        "needs_human",
        "created",
    ]
    with store.read() as c:
        assert M.entity(c, "product/brightline-scheduler")["type"] == "product"
        assert "Brightline Scheduler" not in M.entity(c, "company/brightline")["aliases"]
        assert M.entity(c, "company/harborly")["summary"] == "" and M.entity(c, "company/fernwood")["summary"] == ""
        brightline = M.entity(c, "company/brightline")
        assert brightline["properties"]["hq"] == "Austin" and "Brightline Inc" in brightline["aliases"]
        assert not M.entity(c, "company/brightline-inc")

        def cited(kind, cid):
            return {
                r[0]
                for r in c.execute(
                    "SELECT e.quote FROM market_citations m JOIN market_evidence e "
                    "ON e.id=m.evidence_id WHERE m.claim_kind=? AND m.claim_id=?",
                    (kind, cid),
                )
            }

        assert "Turno cleans." in cited("entity", "company/turno")
        assert "Brightline is in Austin." in cited("entity", "company/brightline")
        assert "Northwind competes with Acme." in cited("edge", "edge-competes-northwind-acme")
        new_edge = M.edges_of(c, src="company/turno", rel="integrates_with")[0]
        assert "Turno integrates with Brightline." in cited("edge", new_edge["id"])
        row = M.insight(c, done["insight"])
        assert (row["status"], row["reported_by"], row["about"], row["claim"]) == (
            "applied",
            "human:ana",
            "batch.jsonl",
            "Research batch one.",
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM history_events WHERE subject_type IN ('market_entity','market_edge','market_evidence','market_document') AND json_extract(data, '$.insight_id')=?",
                (row["id"],),
            ).fetchone()[0]
            >= len(row["applied_events"])
            > 0
        )


def test_graph_reads_share_market_permissions_and_hide_inactive_entities(api):
    with api.app.state.store.transaction() as c:
        for name in ('Self', 'Neighbor', 'Retired', 'Merged'):
            M.create_entity(c, M.SEED_ACTOR, type='organization', name=name, entity_id=f'company/{name.lower()}')
        M.create_edge(c, M.SEED_ACTOR, src='company/self', dst='company/neighbor', rel='employs')
        M.create_edge(c, M.SEED_ACTOR, src='company/self', dst='company/retired', rel='employs')
        M.create_edge(c, M.SEED_ACTOR, src='company/merged', dst='company/self', rel='employs')
        M.create_edge(c, M.SEED_ACTOR, src='company/self', dst='company/neighbor', rel='formerly', until='2000-01-01')
        c.execute("UPDATE entities SET status='retired' WHERE id='company/retired'")
        c.execute("UPDATE entities SET status='merged' WHERE id='company/merged'")
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
            M.create_entity(c, M.SEED_ACTOR, type='organization', name=f'Node {i:02}', entity_id=f'company/n{i}')
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
    """No new insight on a weekday: no task, the skip on record as `automation.skipped`. An insight brings it
    back."""
    import backend.routines as routines
    from backend.scheduler import Scheduler

    _librarian(api)
    store = api.app.state.store
    with store.transaction() as c:
        M.ensure_curator(c)
        aid = routines.row(c, "librarian:curate-the-market")["automation"]  # the routine is its automation now
        c.execute("UPDATE automations SET next_due_at=? WHERE id=?", ("2026-10-12T00:00:00.000000Z", aid))
    tick = lambda at: Scheduler(store, api.app.state.execution).tick(at)
    tick(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc))  # Tuesday, 05:00 in Los Angeles
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE owner_id='bot:librarian'").fetchone()
        assert not c.execute("SELECT 1 FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()
        skipped = [
            json.loads(r[0])
            for r in c.execute("SELECT detail FROM events WHERE action='automation.skipped' AND target=?", (aid,))
        ]
    assert [(bool(d["reason"]), d["at"]) for d in skipped] == [(True, "2026-10-12T00:00:00.000000Z")]
    with store.transaction() as c:
        c.execute(
            "INSERT INTO market_insights(id,reported_by,reported_at,kind) VALUES('i1','bot:ops',?,'pricing')",
            (H.now(),),
        )
    tick(datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))
    with store.read() as c:
        assert c.execute("SELECT title FROM tasks WHERE owner_id='bot:librarian'").fetchone()[0] == "Curate the market"
    # A team that rewrote the automation's words gave it other work: it runs with nothing new to curate.
    with store.transaction() as c:
        c.execute("UPDATE market_insights SET status='applied'")
        for (done,) in c.execute("SELECT id FROM tasks WHERE owner_id='bot:librarian'").fetchall():
            H.task_done(c, "human:ana", done, "Curated")
    flow = get(api, f"automations/{aid}")
    steps = [{**step, "prompt": "Curate the market, then tidy the glossary."} for step in flow["steps"]]
    r = api.patch(f"/api/v2/automations/{aid}", json={"steps": steps}, headers=headers())
    assert r.status_code == 200, r.text
    tick(datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE owner_id='bot:librarian'").fetchone()[0] == 2


def test_applying_an_insight_makes_an_entity_and_sets_its_verified_date(api, tmp_path):
    _seed(api, tmp_path)
    evidence = {
        "source_kind": "page-change",
        "source_url": "https://acme.example/pricing",
        "quote": "Pro is $12",
        "our_read": "A price rise.",
    }
    with api.app.state.store.transaction() as c:
        for insight in ("i1", "i2"):
            c.execute(
                "INSERT INTO market_insights(id,reported_by,reported_at,kind) VALUES(?,'bot:ops',?,'pricing')",
                (insight, H.now()),
            )
    post(
        api,
        "market/insights/i1/apply",
        {
            "evidence": evidence,
            "entity": {
                "id": "company/contoso",
                "type": "organization",
                "name": "Contoso",
                "last_verified_at": "2026-10-01",
            },
        },
    )
    post(
        api,
        "market/insights/i2/apply",
        {"evidence": evidence, "entity_id": "company/contoso", "last_verified_at": "2026-10-02"},
    )
    with api.app.state.store.read() as c:
        row = c.execute("SELECT last_verified_at FROM entities WHERE id='company/contoso'").fetchone()
    assert row[0] == "2026-10-02"


def _apply_insights(api, *insights):
    with api.app.state.store.transaction() as c:
        for insight in insights:
            c.execute(
                "INSERT INTO market_insights(id,reported_by,reported_at,kind) VALUES(?,'bot:ops',?,'pricing')",
                (insight, H.now()),
            )


def test_applying_a_verified_date_to_an_entity_with_none_sets_it_and_applies_the_insight(api, tmp_path):
    _seed(api, tmp_path)
    _apply_insights(api, "i-new", "i-check")
    evidence = {
        "source_kind": "site",
        "source_url": "https://fabrikam.example/pricing",
        "quote": "Pro is $12",
        "our_read": "A price.",
    }
    post(
        api,
        "market/insights/i-new/apply",
        {"evidence": evidence, "entity": {"id": "company/fabrikam", "type": "organization", "name": "Fabrikam"}},
    )
    with api.app.state.store.read() as c:
        assert c.execute("SELECT last_verified_at FROM entities WHERE id='company/fabrikam'").fetchone()[0] is None
    post(
        api,
        "market/insights/i-check/apply",
        {"evidence": evidence, "entity_id": "company/fabrikam", "last_verified_at": "2026-11-03"},
    )
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT last_verified_at FROM entities WHERE id='company/fabrikam'").fetchone()[0] == "2026-11-03"
        )
        assert c.execute("SELECT status FROM market_insights WHERE id='i-check'").fetchone()[0] == "applied"


def test_a_new_entity_applied_with_a_verified_date_keeps_it(api, tmp_path):
    _seed(api, tmp_path)
    _apply_insights(api, "i-made")
    evidence = {"source_kind": "site", "source_url": "https://initech.example/", "quote": "Hello", "our_read": "New."}
    post(
        api,
        "market/insights/i-made/apply",
        {
            "evidence": evidence,
            "entity": {
                "id": "company/initech",
                "type": "organization",
                "name": "Initech",
                "last_verified_at": "2026-11-03",
            },
        },
    )
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT last_verified_at FROM entities WHERE id='company/initech'").fetchone()[0] == "2026-11-03"
        )


def test_a_verified_date_that_is_not_yyyy_mm_dd_is_refused_and_nothing_is_written(api, tmp_path):
    _seed(api, tmp_path)
    _apply_insights(api, "i-bad")
    evidence = {"source_kind": "site", "source_url": "https://acme.example/bad-date", "quote": "Q", "our_read": "R"}
    r = api.post(
        "/api/v2/market/insights/i-bad/apply",
        headers=headers(),
        json={"evidence": evidence, "entity_id": "company/acme", "last_verified_at": "3 Nov 2026"},
    )
    assert r.status_code >= 400 and "YYYY-MM-DD" in r.text, r.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT status FROM market_insights WHERE id='i-bad'").fetchone()[0] == "new"
        assert (
            c.execute(
                "SELECT COUNT(*) FROM market_evidence WHERE source_url='https://acme.example/bad-date'"
            ).fetchone()[0]
            == 0
        )


def test_a_core_company_verified_31_days_ago_is_reported_stale_by_the_sweep(api, tmp_path):
    from datetime import date

    _seed(api, tmp_path)
    _librarian(api)
    today = date(2026, 11, 4)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE entities SET tier='core', last_verified_at='2026-10-04' WHERE id='company/acme'")
        c.execute("UPDATE entities SET tier='core', last_verified_at='2026-10-06' WHERE id='company/brightline'")
        out = M.sweep(
            c,
            "bot:librarian",
            [{"id": "company/acme", "look_for": "pricing"}, {"id": "company/brightline", "look_for": "pricing"}],
            today=today,
        )
    assert [row["entity_id"] for row in out["listening_tasks"]] == ["company/acme"]


def test_an_impossible_calendar_day_is_refused_on_apply_to_an_entity_or_a_new_one_with_nothing_written(api, tmp_path):
    """2026-02-31 has the right shape but no such day: the server refuses it on both apply paths, and nothing is
    written (the insight stays new, no evidence row, no entity, the existing date unchanged)."""
    _seed(api, tmp_path)
    _apply_insights(api, "i-feb", "i-feb-new")
    with api.app.state.store.read() as c:
        before = c.execute("SELECT last_verified_at FROM entities WHERE id='company/acme'").fetchone()[0]
    evidence = {"source_kind": "site", "source_url": "https://acme.example/feb", "quote": "Q", "our_read": "R"}
    r = api.post(
        "/api/v2/market/insights/i-feb/apply",
        headers=headers(),
        json={"evidence": evidence, "entity_id": "company/acme", "last_verified_at": "2026-02-31"},
    )
    assert r.status_code >= 400 and "calendar" in r.text, r.text
    new_evidence = {"source_kind": "site", "source_url": "https://ghostco.example/", "quote": "Q", "our_read": "R"}
    r = api.post(
        "/api/v2/market/insights/i-feb-new/apply",
        headers=headers(),
        json={
            "evidence": new_evidence,
            "entity": {
                "id": "company/ghostco",
                "type": "organization",
                "name": "Ghostco",
                "last_verified_at": "2026-02-31",
            },
        },
    )
    assert r.status_code >= 400 and "calendar" in r.text, r.text
    with api.app.state.store.read() as c:
        assert (
            c.execute(
                "SELECT status FROM market_insights WHERE id IN ('i-feb','i-feb-new') AND status<>'new'"
            ).fetchall()
            == []
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM market_evidence WHERE source_url IN "
                "('https://acme.example/feb','https://ghostco.example/')"
            ).fetchone()[0]
            == 0
        )
        assert c.execute("SELECT COUNT(*) FROM entities WHERE id='company/ghostco'").fetchone()[0] == 0
        assert c.execute("SELECT last_verified_at FROM entities WHERE id='company/acme'").fetchone()[0] == before
