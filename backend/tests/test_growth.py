"""Growth (backend/growth.py): channels, sources, ideas as Idea tasks, cohorts and the unit economics they add up to,
and the KPI tree. Examples use Acme, a home-cleaning company."""

import sqlite3

import pytest

from backend import growth as G
from backend import hubdb as H
from backend.tests.test_api import api, get, headers, post, put  # noqa: F401
from backend.tests.test_company import bot
from backend.tests.test_market_spend import defined


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def add_channel(api, slug="google-search", name="Google Search", **more):
    return post(api, "growth/channels", {"slug": slug, "name": name, **more})


# ----------------------------------------------------------------------------- economics
def test_october_economics_for_google_search_and_the_unknown_row():
    conf = {"revenue_month": 90, "margin_pct": 40}
    google = {"channel": "google-search", "spend": 3400, "bot_cost": 260, "new_customers": 71,
              "kept_d30": 81, "kept_d90": 64, "kept_d180": 52}
    row = G.economics_row(google, conf)
    assert row["cac"] == 51.55
    assert 9 <= row["expected_months"] <= 14 and 320 <= row["ltv"] <= 500
    assert 6 <= row["ltv_cac"] <= 10 and 1.3 <= row["payback_months"] <= 1.6
    unknown = G.economics_row({"channel": None, "new_customers": 18, "kept_d30": 70}, conf)
    assert unknown["new_customers"] == 18 and unknown["cac"] is None and unknown["ltv"] is None
    total = G.economics_total([google, {"channel": None, "new_customers": 18, "kept_d30": 70}], conf)
    assert total["new_customers"] == 89 and total["spend"] == 3400 and total["cac"] == round(3660 / 89, 2)
    assert total["kept_d30"] == round((81 * 71 + 70 * 18) / 89, 2)


def test_expected_months_needs_d30_caps_at_sixty_and_uses_what_is_known():
    assert G.expected_months(None, 60, 50) is None
    assert G.expected_months(100, 100, 100) == G.LIFETIME_CAP
    only_d30 = G.expected_months(50)
    assert 1 < only_d30 < 3          # halves every month: about 0.75 to month 1, then a short geometric tail
    assert G.expected_months(81, None, 52) > G.expected_months(81, None, 40)


def test_economics_route_reads_the_month_and_flags_a_breached_guardrail(api):
    kpi = post(api, "kpis", {"name": "Refund rate", "unit": "%"})["kpi"]
    post(api, f"kpis/{kpi['id']}/readings", {"value": 7, "period_end": "2026-10-31T00:00:00Z"})
    add_channel(api, guardrail_kpi_id=kpi["id"], guardrail_max=5)
    put(api, "growth/settings", {"revenue_month": 90, "margin_pct": 40})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 3400, "bot_cost": 260,
                                "new_customers": 71, "kept_d30": 81, "kept_d90": 64, "kept_d180": 52,
                                "sources": {"spend": "Google Ads billing"}})
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 18})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-09", "spend": 3000, "new_customers": 50})
    eco = get(api, "growth/economics")
    assert eco["month"] == "2026-10" and eco["months"] == ["2026-10", "2026-09"]
    assert [r["channel"] for r in eco["rows"]] == ["google-search", "unknown"]
    assert eco["rows"][0]["cac"] == 51.55 and eco["rows"][0]["sources"] == {"spend": "Google Ads billing"}
    assert eco["total"]["new_customers"] == 89 and eco["total"]["name"] == "All channels"
    assert eco["guardrails"] == [{"channel": "google-search", "kpi_id": kpi["id"], "kpi_name": "Refund rate",
                                  "value": 7.0, "max": 5.0, "breached": True}]
    assert get(api, "growth/economics?month=2026-09")["rows"][0]["cac"] == 60.0
    # The same channel and month again updates the row; only the fields sent change.
    put(api, "growth/cohorts", {"channel": None, "month": "2026-10", "kept_d30": 70})
    unknown = get(api, "growth/cohorts?channel=unknown")["cohorts"]
    assert len(unknown) == 1 and unknown[0]["new_customers"] == 18 and unknown[0]["kept_d30"] == 70


def test_economics_without_numbers_lists_each_active_channel_and_unknown_blank(api):
    add_channel(api)
    add_channel(api, slug="referrals", name="Referrals")
    add_channel(api, slug="flyers", name="Flyers")
    post(api, "growth/channels/flyers/retire", {})
    eco = get(api, "growth/economics")
    assert eco["month"] is None and eco["months"] == []
    assert [r["channel"] for r in eco["rows"]] == ["google-search", "referrals", "unknown"]
    assert all(r["spend"] is None and r["cac"] is None and r["quality"] is None for r in eco["rows"])
    assert eco["total"]["new_customers"] is None and eco["total"]["quality"] is None
    # A month without numbers, once another month has some, is blank too.
    put(api, "growth/cohorts", {"channel": "referrals", "month": "2026-10", "new_customers": 4})
    assert [r["channel"] for r in get(api, "growth/economics")["rows"]] == ["referrals"]
    empty = get(api, "growth/economics?month=2026-11")
    assert empty["month"] == "2026-11" and len(empty["rows"]) == 3 and empty["rows"][1]["new_customers"] is None


# ----------------------------------------------------------------------------- permissions
def test_channel_and_cohort_permissions(api):
    ops, finance = bot(api, "ops"), bot(api, "finance")
    add_channel(api, owner_ref="ops", budget_month=3000)
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"}, token=ops, expected=403)
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"}, token="cara-test", expected=403)
    post(api, "growth/channels", {"slug": "google-search", "name": "X"}, token="ben-test", expected=409)
    changed = patch(api, "growth/channels/google-search", {"stage": "scaling", "why": "Cheapest bookings",
                                                           "budget_month": 4000}, token=ops)
    assert (changed["stage"], changed["budget_month"]) == ("scaling", 4000)
    assert patch(api, "growth/channels/google-search", {"name": "Mine"}, token=ops, expected=403)["error"]
    patch(api, "growth/channels/google-search", {"stage": "paused"}, token=finance, expected=403)
    patch(api, "growth/channels/google-search", {"name": "Google Ads"}, token="ben-test")
    assert put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 1}, token=finance)["spend"] == 1
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "spend": 1}, token="cara-test", expected=403)
    put(api, "growth/cohorts", {"channel": "nope", "month": "2026-10"}, token=finance, expected=404)
    post(api, "growth/channels/google-search/retire", {}, token=ops, expected=403)
    post(api, "growth/channels/google-search/retire", {})
    assert get(api, "growth/channels")["channels"] == []
    assert get(api, "growth/channels?include_retired=true", token="cara-test")["channels"][0]["retired_at"]


def test_lists_and_settings_say_whether_the_caller_may_edit(api):
    ops = bot(api, "ops")
    for path in ("growth/channels", "growth/sources", "growth/settings"):
        assert get(api, path)["may_edit"] is True and get(api, path, token="ben-test")["may_edit"] is True, path
        assert get(api, path, token="cara-test")["may_edit"] is False and get(api, path, token=ops)["may_edit"] is False


def test_sources_list_conflicts_and_only_admins_write(api):
    post(api, "growth/sources", {"truth_for": "Revenue", "system": "Stripe", "reach_kind": "mcp"}, token=bot(api, "ops"),
         expected=403)
    first = post(api, "growth/sources", {"truth_for": "Revenue", "system": "Stripe", "reach_kind": "mcp"})
    post(api, "growth/sources", {"truth_for": "revenue", "system": "QuickBooks", "reach_kind": "credential"},
         token="ben-test")
    assert [x.lower() for x in get(api, "growth/sources", token="cara-test")["conflicts"]] == ["revenue"]
    post(api, f"growth/sources/{first['id']}/retire", {})
    assert get(api, "growth/sources")["conflicts"] == []


# ----------------------------------------------------------------------------- ideas
def test_a_bot_files_and_edits_its_own_idea_but_not_another_bots(api):
    ops, finance = bot(api, "ops"), bot(api, "finance")
    add_channel(api)
    made = post(api, "growth/ideas", {"title": "Show prices on the booking page", "hypothesis": "Visitors leave "
                "when they cannot see a price", "prediction": "Bookings up 10%", "channels": ["google-search"],
                "ice_i": 8, "ice_c": 6, "ice_e": 9, "source_kind": "competitor"}, token=ops)["idea"]
    assert made["status"] == "idea" and made["channels"] == ["google-search"] and made["ice"] == 7.7
    assert made["created_by"] == "bot:ops" and made["assignee"] == "bot:ops"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT type_id FROM tasks WHERE id=?", (made["task_id"],)).fetchone()[0] == G.IDEA_TYPE
    tid = made["task_id"]
    assert patch(api, f"growth/ideas/{tid}", {"prediction": "Bookings up 12%"}, token=ops)["idea"]["prediction"] \
        == "Bookings up 12%"
    patch(api, f"growth/ideas/{tid}", {"prediction": "Mine"}, token=finance, expected=403)
    post(api, f"growth/ideas/{tid}/score", {"i": 1, "c": 1, "e": 1}, token=finance, expected=403)
    post(api, f"growth/ideas/{tid}/score", {"i": 11, "c": 1, "e": 1}, token=ops, expected=422)
    assert post(api, f"growth/ideas/{tid}/score", {"i": 9, "c": 9, "e": 9}, token="ben-test")["idea"]["ice"] == 9.0
    plain = post(api, "tasks", {"owner": "ops", "title": "Clean the van", "body": "Clean it."})
    get(api, f"growth/ideas/{plain['id']}", expected=404)


def test_idea_test_and_verdict_close_the_task_and_count_as_bet_evidence(api):
    ops, finance = bot(api, "ops"), bot(api, "finance")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO strategy_bets (id, statement, status, created_at, updated_at, updated_by) VALUES "
                  "('bet1','Homeowners book online','active',?,?,'human:ana')", (H.now(), H.now()))
    post(api, "growth/ideas", {"title": "Weekend discount", "bet_id": "nope"}, token="cara-test", expected=404)
    first = post(api, "growth/ideas", {"title": "Weekend discount", "assignee": "ops", "bet_id": "bet1"},
                 token="cara-test")["idea"]
    tid = first["task_id"]
    post(api, f"growth/ideas/{tid}/start", {"test_to": "2026-11-14", "change": "10% off Saturdays",
                                            "stop_if": "Refunds pass 5%"}, token="cara-test", expected=403)
    post(api, f"growth/ideas/{tid}/start", {"test_to": "2026-11-14", "change": "10% off Saturdays",
                                            "stop_if": "Refunds pass 5%"}, token=finance, expected=403)
    started = post(api, f"growth/ideas/{tid}/start", {"test_from": "2026-11-01", "test_to": "2026-11-14",
                                                      "change": "10% off Saturdays", "stop_if": "Refunds pass 5%",
                                                      "min_events": 40}, token=ops)["idea"]
    assert (started["status"], started["test_to"], started["min_events"]) == ("testing", "2026-11-14", 40)

    def step():
        with api.app.state.store.read() as c:
            return c.execute("SELECT s.name FROM tasks t JOIN task_steps s ON s.id=t.step_id WHERE t.id=?",
                             (tid,)).fetchone()[0]
    assert step() == "Testing"
    paused = post(api, f"growth/ideas/{tid}/verdict", {"status": "paused"}, token=ops)["idea"]
    assert step() == "Paused"
    assert paused["verdict_at"] is None and paused["task_status"] != "closed"
    won = post(api, f"growth/ideas/{tid}/verdict", {"status": "won", "result": "Bookings up 14%", "learning": "Price "
               "matters on weekends", "bet_effect": "supports", "confidence": "high"}, token=ops)["idea"]
    assert step() == "Won"
    # The bet is live but unpublished: only the owner reads its words (strategy.view's rule).
    assert won["verdict_by"] == "bot:ops" and won["task_status"] == "closed" and won["bet_title"] is None
    assert get(api, f"growth/ideas/{tid}")["idea"]["bet_title"] == "Homeowners book online"
    assert get(api, "growth/evidence", token="cara-test")["bets"][0]["title"] is None
    evidence = get(api, "growth/evidence")["bets"]
    assert evidence == [{"bet_id": "bet1", "title": "Homeowners book online", "supports": {"low": 0, "medium": 0, "high": 1},
                         "weakens": {"low": 0, "medium": 0, "high": 0}, "none": 0, "ideas": [tid]}]
    follow = post(api, "growth/ideas", {"title": "Weekday discount", "follow_up_of": tid}, token=ops)["idea"]
    assert follow["follow_up_of"] == tid
    week = get(api, "growth/week")
    assert [v["task_id"] for v in week["recent_verdicts"]] == [tid]
    assert [i["task_id"] for i in get(api, "growth/ideas?status=won")["ideas"]] == [tid]
    assert [i["task_id"] for i in get(api, "growth/ideas?q=weekday")["ideas"]] == [follow["task_id"]]


def test_working_lists_open_subtasks_of_ideas(api):
    ops = bot(api, "ops")
    made = post(api, "growth/ideas", {"title": "Door hangers in Elm Park"}, token=ops)["idea"]
    sub = post(api, "tasks", {"owner": "ops", "title": "Print 500 door hangers", "body": "Print them.",
                              "relations": [{"task": made["task_id"], "kind": "parent"}]}, token=ops)
    items = get(api, "growth/working")["items"]
    assert [(i["task_id"], i["idea_task_id"]) for i in items] == [(sub["id"], made["task_id"])]
    assert get(api, f"growth/ideas/{made['task_id']}")["idea"]["subtasks"] == {"total": 1, "done": 0}
    # A private idea's visible subtask never names the idea to someone who may not read it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (made["task_id"],))
    get(api, f"growth/ideas/{made['task_id']}", token="cara-test", expected=404)
    items = get(api, "growth/working", token="cara-test")["items"]
    assert [(i["task_id"], i["idea_task_id"], i["idea_title"]) for i in items] == [(sub["id"], None, None)]


def test_the_total_weighs_profit_per_cohort_and_a_hidden_kpis_guardrail_stays_hidden(api):
    # Rich-but-thin and poor-but-fat cohorts: profit per customer-month is 10 and 9, so 9.5 together, never
    # the 55 x 50% = 27.5 that averaging revenue and margin apart would invent.
    rows = [{"channel": "a", "spend": 950, "new_customers": 10, "kept_d30": 80, "revenue_month": 100, "margin_pct": 10},
            {"channel": "b", "spend": 950, "new_customers": 10, "kept_d30": 80, "revenue_month": 10, "margin_pct": 90}]
    total = G.economics_total(rows, {})
    assert total["cac"] == 95.0 and total["payback_months"] == 10.0
    assert total["ltv"] == round(9.5 * total["expected_months"], 2)
    kpi = post(api, "kpis", {"name": "Refund rate"})["kpi"]
    add_channel(api, guardrail_kpi_id=kpi["id"], guardrail_max=5)
    with api.app.state.store.transaction() as c:
        assert [g["kpi_id"] for g in G.guardrails(c)] == [kpi["id"]]
        c.execute("UPDATE kpis SET owner_id='bot:acme-helper' WHERE id=?", (kpi["id"],))
        assert G.guardrails(c, {"bot:acme-helper"}) == []


def test_week_lists_tests_ending_within_seven_days(api):
    from datetime import datetime, timedelta, timezone
    day = lambda n: (datetime.now(timezone.utc) + timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731
    due = {}
    for n in (5, 10):
        made = post(api, "growth/ideas", {"title": f"Test ending in {n} days"})["idea"]
        post(api, f"growth/ideas/{made['task_id']}/start", {"test_to": day(n), "change": "x", "stop_if": "y"})
        due[n] = made["task_id"]
    assert [i["task_id"] for i in get(api, "growth/week")["tests_due"]] == [due[5]]


# ----------------------------------------------------------------------------- KPI tree
def test_kpi_tree_fields_and_one_north_star(api):
    active = post(api, "kpis", {"name": "Active customers"})["kpi"]
    booked = post(api, "kpis", {"name": "Bookings"})["kpi"]
    post(api, f"kpis/{active['id']}", {"growth_role": "north_star"})
    post(api, f"kpis/{booked['id']}", {"parent_kpi_id": active["id"], "growth_role": "input", "stage": "acquire",
                                       "loop_name": "Referral", "loop_step": 2})
    tree = {k["id"]: k for k in get(api, "growth/kpis")["kpis"]}
    assert tree[booked["id"]]["parent_kpi_id"] == active["id"] and tree[booked["id"]]["loop_step"] == 2
    post(api, f"kpis/{active['id']}", {"parent_kpi_id": booked["id"]}, expected=422)      # a loop in the tree
    post(api, f"kpis/{booked['id']}", {"growth_role": "north_star"})
    tree = {k["id"]: k for k in get(api, "growth/kpis")["kpis"]}
    assert tree[active["id"]]["growth_role"] is None and tree[booked["id"]]["growth_role"] == "north_star"
    post(api, f"kpis/{booked['id']}", {"stage": None})
    assert {k["id"]: k for k in get(api, "growth/kpis")["kpis"]}[booked["id"]]["stage"] is None


def test_a_kpi_says_whether_the_caller_may_edit_it_and_an_unreadable_one_has_no_name(api):
    kpi = post(api, "kpis", {"name": "Refund rate"})["kpi"]
    hidden = post(api, "kpis", {"name": "Inbox depth"})["kpi"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE kpis SET owner_id='bot:inbox' WHERE id=?", (hidden["id"],))   # the fixture restricts inbox to Ana
    may = lambda token: {k["id"]: k["may_edit"] for k in get(api, "growth/kpis", token)["kpis"]}   # noqa: E731
    assert may("ana-test") == {kpi["id"]: True, hidden["id"]: True}
    assert may("cara-test") == {kpi["id"]: False} and may(bot(api, "ops"))[kpi["id"]] is False
    made = post(api, "growth/ideas", {"title": "Cut inbox depth", "kpi_id": hidden["id"], "assignee": "cara"})["idea"]
    assert get(api, f"growth/ideas/{made['task_id']}")["idea"]["kpi_name"] == "Inbox depth"
    seen = get(api, f"growth/ideas/{made['task_id']}", "cara-test")["idea"]
    assert seen["kpi_id"] == hidden["id"] and seen["kpi_name"] is None and seen["judged_kpi_name"] is None


# ----------------------------------------------------------------------------- migration
def test_migration_applies_on_an_empty_file_and_on_one_at_the_previous_version(tmp_path):
    for start in (0, H.MIGRATIONS.index(H.GROWTH_SCHEMA), len(H.MIGRATIONS)):
        conn = sqlite3.connect(tmp_path / f"db{start}.sqlite", isolation_level=None)
        conn.row_factory = sqlite3.Row
        if start:
            for i, script in enumerate(H.MIGRATIONS[:start]):
                if i == 23:
                    H.migrate_task_privacy(conn)
                elif i == 30:
                    H.TR.migrate(conn)
                else:
                    H._apply(conn, script)
            conn.execute(f"PRAGMA user_version={start}")
        H.migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(H.MIGRATIONS)
        assert {"parent_kpi_id", "growth_role", "stage", "loop_name", "loop_step"} <= {
            r[1] for r in conn.execute("PRAGMA table_info(kpis)")}
        conn.execute("INSERT INTO growth_cohorts (id, month, updated_at) VALUES ('a','2026-10','x')")
        try:
            conn.execute("INSERT INTO growth_cohorts (id, month, updated_at) VALUES ('b','2026-10','x')")
            raise AssertionError("two Unknown rows for one month")
        except sqlite3.IntegrityError:
            pass
        # One segment's share of the same month is its own row, once.
        conn.execute("INSERT INTO growth_cohorts (id, month, segment_id, updated_at) VALUES ('c','2026-10','s1','x')")
        try:
            conn.execute("INSERT INTO growth_cohorts (id, month, segment_id, updated_at) VALUES ('d','2026-10','s1','x')")
            raise AssertionError("two rows for one segment's month")
        except sqlite3.IntegrityError:
            pass


def test_migration_starts_segments_the_graph_already_marks_us_or_next(tmp_path):
    start = len(H.MIGRATIONS)
    conn = sqlite3.connect(tmp_path / "db.sqlite", isolation_level=None)
    conn.row_factory = sqlite3.Row
    for i, script in enumerate(H.MIGRATIONS[:start]):
        if i == 23:
            H.migrate_task_privacy(conn)
        elif i == 30:
            H.TR.migrate(conn)
        else:
            H._apply(conn, script)
    conn.execute(f"PRAGMA user_version={start}")
    for sid, props in (("segment/homes", "{}"), ("segment/offices", '{"next": true}'), ("segment/hotels", "{}")):
        conn.execute("INSERT INTO market_entities (id, type, name, properties, status, created_at, created_by, updated_at) "
                     "VALUES (?, 'segment', ?, ?, 'active', 'x', 'x', 'x')", (sid, sid.split("/")[1].title(), props))
    conn.execute("INSERT INTO market_edges (id, src, rel, dst, created_at, created_by, updated_at) VALUES "
                 "('e1','company/self','operates_in','segment/homes','x','x','x')")
    conn.execute("INSERT INTO market_edges (id, src, rel, dst, valid_until_on, created_at, created_by, updated_at) VALUES "
                 "('e2','company/self','operates_in','segment/hotels','2020-01-01','x','x','x')")   # ended: not Us
    H.migrate(conn)
    rows = {r["segment_id"]: r["status"] for r in conn.execute("SELECT segment_id, status FROM growth_segments")}
    assert rows == {"segment/homes": "serving", "segment/offices": "next"}


def test_a_file_that_already_ran_the_segments_step_as_user_version_72_opens_and_keeps_its_rows(tmp_path):
    """A release before the migration files ran this as hubdb's step 72. Such a file opens, keeps a person's choices
    (a segment dropped from the graph's start stays dropped) and is not seeded again; one step newer is refused."""
    path = tmp_path / "hub.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    H.migrate(conn)
    conn.execute("INSERT INTO market_entities (id, type, name, properties, status, created_at, created_by, updated_at) "
                 "VALUES ('segment/homes', 'segment', 'Homes', '{\"next\": true}', 'active', 'x', 'x', 'x')")
    conn.execute("INSERT INTO growth_segments (id, segment_id, status, created_at, updated_at) "
                 "VALUES ('kept', 'segment/homes', 'dropped', 'x', 'x')")
    conn.execute("INSERT INTO growth_cohorts (id, month, segment_id, updated_at) VALUES ('c','2026-10','s1','x')")
    conn.execute("DROP TABLE schema_migrations")      # as that release left it: no migration files recorded
    conn.execute("PRAGMA user_version=72")
    conn.close()
    from backend import migrations
    migrations.preflight(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    H.migrate(conn)
    assert [tuple(r) for r in conn.execute("SELECT id, status FROM growth_segments")] == [("kept", "dropped")]
    assert conn.execute("SELECT segment_id FROM growth_cohorts WHERE id='c'").fetchone()[0] == "s1"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 72
    conn.execute("PRAGMA user_version=73")
    conn.close()
    with pytest.raises(migrations.MigrationError, match="newer than this code"):
        migrations.preflight(path)


# ----------------------------------------------------------------------------- segments
def statuses(api):
    return {s["name"]: s["status"] for s in get(api, "growth/segments")["segments"]}


def where_we_play(api):
    seen = get(api, "market/spend")["segments"]
    return sorted(s["name"] for s in seen if s["self"]), sorted(s["name"] for s in seen if s["next"])


def test_segments_by_name_status_and_where_we_play_follow_each_other(api):
    first, second, sub = defined(api)             # Screening, Payments, Credit checks
    homes = post(api, "growth/segments", {"name": "screening", "status": "serving", "motion": "self_serve",
                                          "who_buys": "Small landlords", "deal_size": "$40/mo"})["segment"]
    assert homes["segment_id"] == first and homes["name"] == "Screening"        # found by name, not made again
    made = post(api, "growth/segments", {"name": "Property managers", "status": "serving"})["segment"]
    assert made["segment_id"] == "segment/property-managers"
    post(api, "growth/segments", {"segment_id": second, "status": "next"})
    post(api, "growth/segments", {"segment_id": sub, "status": "testing"})
    post(api, "growth/segments", {"name": "Screening"}, expected=409)
    post(api, "growth/segments", {"status": "testing"}, expected=422)
    listed = get(api, "growth/segments", token="cara-test")
    assert [s["status"] for s in listed["segments"]] == ["serving", "serving", "testing", "next"]
    assert listed["may_edit"] is False and get(api, "growth/segments")["may_edit"] is True
    # Several Us: Where we play shows both serving segments, and the new one sits under the market's root.
    assert where_we_play(api) == (["Property managers", "Screening"], ["Payments"])
    # A status change moves the graph; a click on the Strategy page moves Growth.
    patch(api, f"growth/segments/{second}", {"status": "serving"})
    assert where_we_play(api) == (["Payments", "Property managers", "Screening"], [])
    patch(api, f"growth/segments/{homes['id']}", {"status": "dropped"})
    assert where_we_play(api)[0] == ["Payments", "Property managers"]
    post(api, "market/segments/target", {"id": sub, "target": "next"})
    post(api, "market/segments/target", {"id": first, "target": "ours"})
    assert statuses(api) == {"Screening": "serving", "Property managers": "serving", "Payments": "serving",
                             "Credit checks": "next"}
    patch(api, f"growth/segments/{sub}", {"status": "testing"})
    assert where_we_play(api)[1] == []
    # Removing a segment on the Strategy page drops its Growth row.
    post(api, "market/segments/remove", {"id": second})
    assert statuses(api)["Payments"] == "dropped"


def test_segment_permissions(api):
    defined(api)
    ops = bot(api, "ops")
    post(api, "growth/segments", {"name": "Screening", "status": "serving"}, token=ops, expected=403)
    post(api, "growth/segments", {"name": "Screening", "status": "testing"}, token="cara-test", expected=403)
    tested = post(api, "growth/segments", {"name": "Screening", "status": "testing", "why": "Cheap to reach"},
                  token=ops)["segment"]
    assert tested["status"] == "testing" and tested["why"] == "Cheap to reach"
    changed = patch(api, f"growth/segments/{tested['id']}", {"who_buys": "Landlords with 1-5 units",
                                                              "deal_size": "$30/mo"}, token=ops)["segment"]
    assert (changed["who_buys"], changed["deal_size"]) == ("Landlords with 1-5 units", "$30/mo")
    patch(api, f"growth/segments/{tested['id']}", {"status": "serving"}, token=ops, expected=403)
    patch(api, f"growth/segments/{tested['id']}", {"motion": "local"}, token=ops, expected=403)
    patch(api, f"growth/segments/{tested['id']}", {"why": "x"}, token="cara-test", expected=403)
    patch(api, f"growth/segments/{tested['id']}", {"status": "serving"}, token="ben-test")
    # A bot never adds a segment over one Where we play already marks Us or Next.
    post(api, "market/segments/target", {"id": "segment/payments", "target": "next"})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM growth_segments WHERE segment_id='segment/payments'")
    post(api, "growth/segments", {"segment_id": "segment/payments", "status": "testing"}, token=ops, expected=403)
    patch(api, "growth/segments/nope", {"why": "x"}, expected=404)


def test_channels_ideas_and_cohorts_name_segments_and_economics_splits_by_one(api):
    defined(api)
    put(api, "growth/settings", {"revenue_month": 90, "margin_pct": 40})
    homes = post(api, "growth/segments", {"name": "Screening", "status": "serving"})["segment"]
    pms = post(api, "growth/segments", {"name": "Payments", "status": "testing"})["segment"]
    channel = add_channel(api, segments=[homes["id"], "segment/payments"])
    assert channel["segments"] == [homes["id"], pms["id"]]
    post(api, "growth/channels", {"slug": "nope", "name": "Nope", "segments": ["segment/none"]}, expected=404)
    assert patch(api, "growth/channels/google-search", {"segments": [pms["id"]]})["segments"] == [pms["id"]]
    assert get(api, "growth/channels")["channels"][0]["segments"] == [pms["id"]]
    idea = post(api, "growth/ideas", {"title": "Landlord webinar", "segments": [pms["id"]]})["idea"]
    other = post(api, "growth/ideas", {"title": "Screening ad", "segments": [homes["segment_id"]]})["idea"]
    assert idea["segments"] == [pms["id"]]
    assert [i["task_id"] for i in get(api, f"growth/ideas?segment={pms['id']}")["ideas"]] == [idea["task_id"]]
    assert patch(api, f"growth/ideas/{other['task_id']}", {"segments": []})["idea"]["segments"] == []
    listed = {s["id"]: s for s in get(api, "growth/segments")["segments"]}
    assert listed[pms["id"]]["channels"] == ["google-search"] and listed[pms["id"]]["ideas"] == 1
    # One channel's October, split by segment: each segment's numbers apart, and both added up without one.
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "segment_id": homes["id"],
                                "spend": 2000, "new_customers": 40, "kept_d30": 80})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "segment_id": "segment/payments",
                                "spend": 1000, "new_customers": 10, "kept_d30": 60})
    put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-10", "segment_id": homes["id"],
                                "new_customers": 50})                                 # the same row again
    assert len(get(api, "growth/cohorts")["cohorts"]) == 2
    assert [c["segment_id"] for c in get(api, f"growth/cohorts?segment={pms['id']}")["cohorts"]] == [pms["id"]]
    one = get(api, f"growth/economics?segment={homes['id']}")
    assert one["segment"] == homes["id"] and one["rows"][0]["cac"] == 40.0 and one["total"]["new_customers"] == 50
    # A segment's own channels without its numbers, and Unknown, stay on its table, blank.
    add_channel(api, slug="referrals", name="Referrals", segments=[homes["id"]])
    add_channel(api, slug="flyers", name="Flyers")                                     # not this segment's
    one = get(api, f"growth/economics?segment={homes['id']}")
    assert [r["channel"] for r in one["rows"]] == ["google-search", "referrals", "unknown"]
    assert one["rows"][1]["new_customers"] is None and one["rows"][2]["cac"] is None
    assert one["total"]["new_customers"] == 50
    every = get(api, "growth/economics")
    assert [r["channel"] for r in every["rows"]] == ["google-search"]
    assert every["rows"][0]["spend"] == 3000 and every["rows"][0]["new_customers"] == 60 and every["rows"][0]["cac"] == 50.0
    assert every["total"]["cac"] == 50.0
    get(api, "growth/economics?segment=nope", expected=404)
    assert {s["id"]: s["new_customers_last_month"] for s in get(api, "growth/segments")["segments"]} == \
        {homes["id"]: 50, pms["id"]: 10}
    # A channel's row with no segment is its whole month; segment rows beside it are a breakdown, not more.
    for seg_id, spend, new in ((None, 1000, 100), (homes["id"], 600, 60), (pms["id"], 400, 40)):
        put(api, "growth/cohorts", {"channel": "google-search", "month": "2026-09", "segment_id": seg_id,
                                    "spend": spend, "new_customers": new})
    whole = get(api, "growth/economics?month=2026-09")
    assert [(r["spend"], r["new_customers"]) for r in whole["rows"] if r["channel"] == "google-search"] == [(1000, 100)]
    assert whole["total"]["new_customers"] == 100 and whole["total"]["spend"] == 1000
    part = get(api, f"growth/economics?month=2026-09&segment={homes['id']}")
    assert (part["rows"][0]["spend"], part["rows"][0]["new_customers"]) == (600, 60)


def test_a_member_cannot_move_the_north_star_off_a_kpi_they_may_not_edit_nor_read_a_hidden_one(api):
    company = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{company['id']}", {"growth_role": "north_star"})
    own = post(api, "kpis", {"name": "Cara's signups"})["kpi"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE kpis SET owner_id='human:cara' WHERE id=?", (own["id"],))
    refused = post(api, f"kpis/{own['id']}", {"growth_role": "north_star"}, token="cara-test", expected=403)
    assert "Active customers" not in str(refused)
    roles = {k["id"]: k["growth_role"] for k in get(api, "growth/kpis")["kpis"]}
    assert roles[company["id"]] == "north_star" and roles[own["id"]] is None
    # A bot's North Star is named only to those who may read that bot (the fixture restricts inbox to Ana).
    put(api, "gtm/draft", {"sections": {"motion": {"primary": "local"}}})
    post(api, "gtm/publish", {"note": "First"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE kpis SET owner_id='bot:inbox' WHERE id=?", (company["id"],))
    assert "Active customers" in get(api, "gtm/summary")["text"]
    assert "Active customers" not in get(api, "gtm/summary", "cara-test")["text"]
