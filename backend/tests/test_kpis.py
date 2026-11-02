"""KPIs and the Strategy Bot (backend/kpis.py, backend/goals.py, docs/goals-and-kpis.md): the colour
arithmetic, a person's override, what the Strategy Bot may not do without a confirm, the migration of
the old KPI rows and the automatic bot KPIs."""

import sqlite3
from datetime import datetime, timedelta, timezone

from backend import botkpis, strategy_bot, hubdb, kpis as K
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def stamp(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def reading(value, days_ago, quality="measured"):
    return {"id": f"r{days_ago}", "value": value, "period_end": stamp(days_ago), "period_start": stamp(days_ago + 7),
            "collected_at": stamp(days_ago), "created_at": stamp(days_ago), "quality": quality}


ACTIVATION = {"name": "Activation", "unit": "%", "direction": "up", "cadence": "weekly"}
CLIMB = {"kind": "improve", "baseline": 40.0, "baseline_at": stamp(60), "target": 70.0, "deadline": "2026-11-28"}


def test_automatic_cost_uses_measured_api_cost_only(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id, bot, started_at, billing) VALUES('unmeasured', 'ops', ?, 'api')", (stamp(1),))
        assert botkpis.value(c, "ops", "cost_7d", NOW) is None
        c.executemany("INSERT INTO turns(id, bot, started_at, est_cost_usd, billing) VALUES(?, 'ops', ?, ?, ?)", [
            ("measured", stamp(1), 2.5, "api"), ("subscription", stamp(1), 5, "subscription")])
        assert botkpis.value(c, "ops", "cost_7d", NOW) == 2.5
        c.execute("UPDATE turns SET est_cost_usd=0 WHERE billing='api' AND est_cost_usd IS NOT NULL")
        assert botkpis.value(c, "ops", "cost_7d", NOW) == 0


def test_automatic_cost_distinguishes_missing_usage_from_measured_zero(api):
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO turns(id, bot, started_at, est_cost_usd, billing, usage_reason) "
                      "VALUES(?, 'ops', ?, 0, ?, ?)", [
            (f"{reason}-{billing}", stamp(1), billing, reason)
            for reason in ("usage_unreported", "usage_limited", "no_result")
            for billing in ("api", None)
        ])
        # A measured subscription and an API reading outside the window cannot supply a reading.
        c.executemany("INSERT INTO turns(id, bot, started_at, est_cost_usd, billing) VALUES(?, 'ops', ?, ?, ?)", [
            ("subscription", stamp(1), 50, "subscription"), ("old", stamp(8), 100, "api")])
        assert botkpis.value(c, "ops", "cost_7d", NOW) is None
        assert all(r["period_end"] != stamp(0) for r in botkpis.readings(c, "auto:ops:cost_7d", NOW))
        c.executemany("INSERT INTO turns(id, bot, started_at, est_cost_usd, billing) VALUES(?, 'ops', ?, ?, ?)", [
            ("measured", stamp(1), 2.5, "api"), ("legacy-billing", stamp(1), 1.25, None)])
        assert botkpis.value(c, "ops", "cost_7d", NOW) == 3.75
        c.execute("UPDATE turns SET est_cost_usd=0 WHERE id IN ('measured', 'legacy-billing')")
        assert botkpis.value(c, "ops", "cost_7d", NOW) == 0
        assert botkpis.readings(c, "auto:ops:cost_7d", NOW)[-1]["value"] == 0


def colour(link, value, days_ago=0, kpi=ACTIVATION, **more):
    return K.assess({**kpi, **more}, link, [reading(value, days_ago)], NOW)


def test_the_colour_of_a_kpi_is_pace_or_range_and_no_fresh_data_is_gray():
    # 40 -> 70 over 120 days, and today is day 60: the line is at 54.9, and 10% of the 14.9 needed is 1.5.
    assert colour(CLIMB, 56)["status"] == "green"
    on_line = colour(CLIMB, 55)
    assert on_line["status"] == "green" and "vs 54.9% needed on pace" in on_line["reason"]
    assert colour(CLIMB, 53.6)["status"] == "yellow"               # behind by 1.3, inside the 1.5 tolerance
    behind = colour(CLIMB, 52)
    assert behind["status"] == "red" and behind["reason"] == "Activation 52% vs 54.9% needed on pace"
    assert colour(CLIMB, 71)["status"] == "green"                   # past the target is done, not "ahead"
    # Down is the same arithmetic the other way: churn from 8 to 4 by the deadline.
    down = {"kind": "improve", "baseline": 8.0, "baseline_at": stamp(60), "target": 4.0, "deadline": "2026-11-28"}
    assert colour(down, 5.9, kpi={"name": "Churn", "unit": "%", "direction": "down", "cadence": "weekly"})["status"] == "green"
    assert colour(down, 7.5, kpi={"name": "Churn", "unit": "%", "direction": "down", "cadence": "weekly"})["status"] == "red"
    # A range: inside is green, near an edge is yellow, outside is red.
    keep = {"kind": "maintain", "min": 40.0, "max": 60.0}
    assert [colour(keep, v)["status"] for v in (50, 41, 59, 39, 61)] == ["green", "yellow", "yellow", "red", "red"]
    assert colour({"kind": "maintain", "min": 90.0, "max": None}, 85)["reason"] == "Activation 85% below min 90%"
    # No target: a fresh reading is a number, not a colour.
    assert colour({"kind": "none"}, 50)["status"] == "none"
    # Gray is stale or missing, never a value: one period missed is stale, two are missing, never read is missing.
    assert colour(CLIMB, 56, days_ago=8)["freshness"] == "fresh"          # a week and a quarter of grace
    stale = colour(CLIMB, 56, days_ago=10)
    assert (stale["status"], stale["freshness"]) == ("gray", "stale") and stale["value"] is None
    assert (colour(CLIMB, 56, days_ago=17)["status"], colour(CLIMB, 56, days_ago=17)["freshness"]) == ("gray", "missing")
    never = K.assess(ACTIVATION, CLIMB, [], NOW)
    assert (never["status"], never["freshness"], never["value"]) == ("gray", "missing", None)
    # Only half a period of data is not a value to judge.
    assert K.assess(ACTIVATION, CLIMB, [reading(30, 1, "partial")], NOW)["status"] == "gray"
    # A partial update does not refresh the complete value used to judge a target.
    mixed = K.assess(ACTIVATION, CLIMB, [reading(56, 17), reading(20, 0, "partial")], NOW)
    assert (mixed["status"], mixed["freshness"], mixed["value"]) == ("gray", "missing", None)
    assert mixed["reading_id"] == "r17"
    # A reading that supersedes another is what counts: the corrected one is read, the first stays in history.
    assert K.assess(ACTIVATION, CLIMB, [reading(56, 1), {**reading(48, 1), "id": "fix", "collected_at": stamp(0)}], NOW)["value"] == 48


# ------------------------------------------------------------------ the API
def goal_with_kpi(api, target=None, token="ana-test", owner="ana", cadence="daily"):
    goal = post(api, "goals", {"title": "Raise activation", "owner": owner}, token=token)["goal"]
    body = {"name": "Activation", "unit": "%", "cadence": cadence, "definition": "Signed-up accounts that finish setup",
            "goal_id": goal["id"], **(target or {"kind": "improve", "baseline": 40, "target": 70, "deadline": "2099-12-31"})}
    return goal, post(api, "kpis", body, token=token)["kpi"]


def log(api, kpi_id, value, token="ana-test", **more):
    return post(api, f"kpis/{kpi_id}/readings", {"value": value, **more}, token=token)["reading"]


def goal_of(api, goal_id, token="ana-test"):
    return get(api, f"goals/{goal_id}", token)["goal"]


def test_a_goals_worst_kpi_sets_its_colour_and_reason_without_overriding_a_person(api):
    goal, activation = goal_with_kpi(api, {"kind": "maintain", "min": 40, "max": 60})
    alarms = post(api, "kpis", {"name": "False alarms", "unit": "alarms", "direction": "down",
                  "cadence": "daily", "goal_id": goal["id"], "kind": "maintain", "max": 1})["kpi"]
    # A missing reading is gray and must not hide a fresh KPI's colour.
    post(api, "kpis", {"name": "Unreported measure", "goal_id": goal["id"], "kind": "maintain", "min": 1})
    log(api, activation["id"], 50, period_end=H.now())
    log(api, alarms["id"], 3, period_end=H.now())
    red = goal_of(api, goal["id"])
    assert red["status"] == "red"
    assert red["status_note"] == "False alarms 3 above max 1 alarms (+1 more)"

    post(api, f"goals/{goal['id']}/status", {"status": "green", "note": "A temporary exception."})
    post(api, "goals/refresh", {})
    overridden = goal_of(api, goal["id"])
    assert (overridden["status"], overridden["status_source"], overridden["suggest_status"]) == ("green", "person", "red")
    assert post(api, f"goals/{goal['id']}/status/auto", {})["goal"]["status"] == "red"

    # Once the red KPI recovers, yellow still takes priority over green.
    log(api, alarms["id"], 0, period_end=H.now())
    log(api, activation["id"], 41, period_end=H.now())
    yellow = goal_of(api, goal["id"])
    assert yellow["status"] == "yellow"
    assert yellow["status_note"] == "Activation 41% in range 40–60% (+1 more)"
    log(api, activation["id"], 50, period_end=H.now())
    assert goal_of(api, goal["id"])["status"] == "green"


def test_archive_restore_preserves_kpi_history_and_marks_it_on_goals(api):
    goal, kpi = goal_with_kpi(api)
    logged = log(api, kpi["id"], 52, evidence="https://bi.example/activation", note="September", period_end=H.now())
    with api.app.state.store.transaction() as c:
        strategy_bot.set_level(c, "human:ana", 2)                  # above Passive, a stale KPI waits in Needs you
    stale = post(api, "kpis", {"name": "Unreported measure", "owner": "me"})["kpi"]
    assert any(item.get("kpi_id") == stale["id"] for item in get(api, "goals/needs-you")["items"])
    post(api, f"kpis/{stale['id']}/archive", {})
    assert all(item.get("kpi_id") != stale["id"] for item in get(api, "goals/needs-you")["items"])
    post(api, f"kpis/{stale['id']}/restore", {}, token="cara-test", expected=403)
    post(api, f"kpis/{stale['id']}/restore", {})
    original = get(api, f"kpis/{kpi['id']}")
    archived = post(api, f"kpis/{kpi['id']}/archive", {})["kpi"]
    assert archived["archived_at"] and archived["archived_by"] == "human:ana"
    assert all(row["id"] != kpi["id"] for row in get(api, "kpis")["kpis"])
    historical = get(api, "kpis?include_archived=true")["kpis"]
    assert next(row for row in historical if row["id"] == kpi["id"])["archived_at"]
    detail = get(api, f"kpis/{kpi['id']}")
    assert detail["readings"] == original["readings"]
    assert detail["definitions"] == original["definitions"]
    # The stored links and targets stay; each goal's derived colour now changes on archive.
    stable = lambda links: [{k: v for k, v in link.items() if k != "goal_status"} for link in links]
    assert stable(detail["links"]) == stable(original["links"])
    assert detail["readings"][0]["id"] == logged["id"]
    line = next(row for row in goal_of(api, goal["id"])["kpis"] if row["id"] == kpi["id"])
    assert line["archived_at"] == archived["archived_at"]
    assert line["status"] is None and line["spark"] == []
    assert line["reason"] == f"Archived {K.day(archived['archived_at'])}, not counting"
    tree_goal = next(g for g in get(api, "goals/tree")["goals"] if g["id"] == goal["id"])
    assert next(k for k in tree_goal["kpis"] if k["id"] == kpi["id"])["status"] is None
    with api.app.state.store.read() as c:
        assert K.goal_views(c, [goal["id"]])[goal["id"]] == []
    with api.app.state.store.read() as c:
        actions = [r["action"] for r in c.execute(
            "SELECT action FROM events WHERE target=? AND action LIKE 'kpi.%' ORDER BY ts", (kpi["id"],))]
    assert actions[-1:] == ["kpi.archive"]

    restored = post(api, f"kpis/{kpi['id']}/restore", {})["kpi"]
    assert restored["archived_at"] is None and restored["archived_by"] is None
    assert any(row["id"] == kpi["id"] for row in get(api, "kpis")["kpis"])
    assert any(row["id"] == kpi["id"] for row in goal_of(api, goal["id"])["kpis"])
    with api.app.state.store.read() as c:
        actions = [r["action"] for r in c.execute(
            "SELECT action FROM events WHERE target=? AND action LIKE 'kpi.%' ORDER BY ts", (kpi["id"],))]
    assert actions[-2:] == ["kpi.archive", "kpi.restore"]


def add_strategy_bot(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
                  "VALUES('goal-manager','goal-manager','Goal Manager','','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,team,operator) VALUES('goal-manager',?,NULL,'ana')",
                  ('{"runtime":"fake","host":"keeper"}',))


def owners_chat(api):
    """An Assistant turn in the owner's chat, which is their Assistant like everyone's (backend/assistant.py chat_bot):
    it acts as the owner, with the Assistant's rights. The Strategy Bot is a helper it hands work to, never this chat."""
    add_strategy_bot(api)
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"])
    post(api, "assistant/messages", {"text": "Archive the activation KPI, then bring it back"})
    return claim(api, machine, "coo")["token"]


def goals_session(api):
    """A Strategy Bot turn in its goals session, woken by another bot: it acts as itself, never as the owner."""
    add_strategy_bot(api)
    machine = runner(api)
    for bot in ("ops", "goal-manager"):
        assign(api, machine, bot)
    ready(api, machine, ["ops", "goal-manager"])
    post(api, "chat/ops", {"text": "Check the goals."})
    ops = claim(api, machine, "ops")["token"]
    post(api, "messages", {"to": "goal-manager", "text": "Run the pass.", "needs_action": True}, token=ops)
    return claim(api, machine, "goal-manager")["token"]


def test_only_kpi_owner_or_manager_above_can_archive_and_goal_manager_is_refused(api):
    _, kpi = goal_with_kpi(api)
    post(api, f"kpis/{kpi['id']}/archive", {}, token="cara-test", expected=403)

    token = goals_session(api)
    post(api, f"kpis/{kpi['id']}/archive", {}, token=token, expected=403)
    post(api, f"kpis/{kpi['id']}/restore", {}, token=token, expected=403)


def test_in_the_owners_chat_the_assistant_acts_as_the_owner_but_leaves_kpi_archive_and_restore_to_them(api):
    _, kpi = goal_with_kpi(api, owner="ben", token="ben-test")
    token = owners_chat(api)
    assert get(api, "me", token)["actor"] == "human:ana"
    post(api, f"kpis/{kpi['id']}/archive", {}, token="ana-test")
    for change in ("restore", "archive"):
        # With the Assistant's rights, not the owner's: never on its own, and not a card the owner confirms either.
        assert post(api, f"kpis/{kpi['id']}/{change}", {}, token=token,
                    expected=403)["error"]["code"] == "confirm_required"
        post(api, "assistant/actions", {"summary": change.title() + " the activation KPI", "method": "POST",
                                        "path": f"/api/v2/kpis/{kpi['id']}/{change}", "body": {}},
             token=token, expected=422)
        post(api, f"kpis/{kpi['id']}/{change}", {}, token="ana-test")



def test_the_goal_manager_reads_and_writes_facts_but_a_target_needs_the_owners_confirm(api):
    token = goals_session(api)
    goal, kpi = goal_with_kpi(api, {"kind": "improve", "baseline": 40, "target": 70, "deadline": "2099-12-31"}, token="ben-test", owner="ben")
    # It writes the readings and the automatic colours...
    log(api, kpi["id"], 45, token=token, evidence="https://analytics.example/q/12", period_end=H.now())
    assert goal_of(api, goal["id"], token)["status_source"] == "auto"
    assert post(api, "goals/refresh", {}, token=token)["checked"] >= 1
    # ...and nothing else: not the target, the definition, a colour, a new KPI or a new link. A target is refused and
    # becomes a proposal in the owner's Needs you (backend/strategy_acts.py `gate`); the rest are refused.
    post(api, f"goals/{goal['id']}/kpis/{kpi['id']}", {"kind": "improve", "target": 50, "deadline": "2099-01-01"},
         token=token, expected=409)
    for path, body in ((f"kpis/{kpi['id']}", {"definition": "Whatever is easiest to hit"}),
                       (f"goals/{goal['id']}/status", {"status": "green", "note": "Fine."}),
                       (f"goals/{goal['id']}/status/auto", {}),
                       ("kpis", {"name": "Vanity"}), (f"goals/{goal['id']}/kpis", {"kpi_id": kpi["id"]})):
        post(api, path, body, token=token, expected=403)
    # It proposes; the owner of the goal confirms, as themselves, and only then does the target move.
    proposal = post(api, "goal-proposals", {"kind": "kpi_target", "goal_id": goal["id"], "kpi_id": kpi["id"],
                    "reason": "The deadline cannot be met at this pace",
                    "payload": {"kind": "improve", "baseline": 40, "target": 60, "deadline": "2099-12-31"}}, token=token)["proposal"]
    assert get(api, f"goals/{goal['id']}", "ben-test")["goal"]["kpis"][0]["link"]["target"] == 70
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token=token, expected=403)
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="cara-test", expected=403)
    done = post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm", "note": "Agreed"}, token="ben-test")["proposal"]
    assert (done["status"], done["decided_by"]) == ("confirmed", "human:ben")
    assert get(api, f"goals/{goal['id']}", "ben-test")["goal"]["kpis"][0]["link"]["target"] == 60
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "reject"}, token="ben-test", expected=422)     # once
    # A definition change is one too, and confirming it starts a new definition version.
    change = post(api, "goal-proposals", {"kind": "kpi_definition", "kpi_id": kpi["id"], "payload": {"definition": "Finish setup within 7 days"}},
                  token=token)["proposal"]
    post(api, f"goal-proposals/{change['id']}/decide", {"decision": "confirm"}, token="ben-test")
    shown = get(api, f"kpis/{kpi['id']}", "ben-test")
    assert shown["kpi"]["definition_version"] == 2 and [d["version"] for d in shown["definitions"]] == [2, 1]
    # A reading made under the old definition keeps saying so; a correction is a new reading, the old one stays.
    fixed = log(api, kpi["id"], 46, token=token, supersedes=shown["readings"][0]["id"], note="The first pull double counted")
    assert fixed["definition_version"] == 2
    every = get(api, f"kpis/{kpi['id']}/readings", "ben-test")["readings"]
    assert [r["superseded_by"] for r in every] == [fixed["id"], None] and every[0]["definition_version"] == 1
    assert get(api, f"kpis/{kpi['id']}/readings?effective=true", "ben-test")["readings"][0]["value"] == 46
    post(api, f"kpis/{kpi['id']}/readings", {"value": 47, "supersedes": every[0]["id"], "note": "again"}, token=token, expected=422)


def test_the_kpis_of_the_old_tables_become_links_with_an_improvement_target():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    for i, script in enumerate(hubdb.MIGRATIONS[:hubdb.MIGRATIONS.index(hubdb.KPIS_SCHEMA)]):
        hubdb._apply(conn, script)
        conn.execute(f"PRAGMA user_version={i + 1}")
    conn.execute("INSERT INTO goals (id, title, owner, status, status_note, status_by, created_at, created_by, updated_at) "
                 "VALUES ('g1','Grow','company','green','On track','human:ana','2026-01-01T00:00:00Z','human:ana','2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO kpis (id, goal_id, name, unit, target, created_at, created_by) VALUES "
                 "('k1','g1','Signups','signups',500,'2026-01-02T00:00:00Z','human:ana'), "
                 "('k2','g1','NPS','',NULL,'2026-01-02T00:00:00Z','human:ana')")
    conn.execute("INSERT INTO kpi_readings (id, kpi_id, ts, value, actor, source, note, created_at) VALUES "
                 "('r1','k1','2026-03-01T00:00:00Z',120,'human:ana','measured','from the dashboard','2026-03-02T00:00:00Z'), "
                 "('r2','k1','2026-04-01T00:00:00Z',200,'human:ana','estimate','','2026-04-01T00:00:00Z')")
    hubdb.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(hubdb.MIGRATIONS)
    links = {r["kpi_id"]: dict(r) for r in conn.execute("SELECT * FROM goal_kpis")}
    assert (links["k1"]["kind"], links["k1"]["target"], links["k1"]["goal_id"]) == ("improve", 500, "g1")
    assert (links["k2"]["kind"], links["k2"]["target"]) == ("none", None)
    kpi = dict(conn.execute("SELECT * FROM kpis WHERE id='k1'").fetchone())
    assert (kpi["owner"], kpi["cadence"], kpi["direction"], kpi["definition_version"]) == ("company", "monthly", "up", 1)
    old = [dict(r) for r in conn.execute("SELECT * FROM kpi_readings ORDER BY id")]
    assert [(r["value"], r["period_end"], r["collected_at"], r["quality"], r["note"]) for r in old] == [
        (120, "2026-03-01T00:00:00Z", "2026-03-02T00:00:00Z", "measured", "from the dashboard"),
        (200, "2026-04-01T00:00:00Z", "2026-04-01T00:00:00Z", "estimate", "")]
    goal = dict(conn.execute("SELECT * FROM goals").fetchone())
    assert (goal["status"], goal["status_source"]) == ("green", "person")      # a colour somebody set is theirs
    assert conn.execute("SELECT count(*) FROM kpi_definitions").fetchone()[0] == 2
    hubdb.migrate(conn)                                                         # and it is safe to run again
    assert conn.execute("SELECT count(*) FROM goal_kpis").fetchone()[0] == 2


def test_archive_restore_recomputes_every_linked_goal_with_its_own_target(api):
    goal, kpi = goal_with_kpi(api, {"kind": "maintain", "min": 40, "max": 60})
    other = post(api, "goals", {"title": "Second goal", "owner": "ana"})["goal"]
    post(api, f"goals/{other['id']}/kpis", {"kpi_id": kpi["id"], "kind": "maintain", "min": 70})
    log(api, kpi["id"], 50, period_end=H.now())
    assert [goal_of(api, g["id"])["status"] for g in (goal, other)] == ["green", "red"]
    links = get(api, f"kpis/{kpi['id']}")["links"]
    archived = post(api, f"kpis/{kpi['id']}/archive", {})["kpi"]
    reason = f"Its only measure was archived on {K.day(archived['archived_at'])}"
    post(api, "goals/refresh", {})
    for g in (goal, other):
        current = goal_of(api, g["id"])
        assert current["status"] == "gray" and current["status_note"] == reason
        assert current["kpis"][0]["status"] is None
    post(api, f"kpis/{kpi['id']}/restore", {}, token="cara-test", expected=403)
    post(api, f"kpis/{kpi['id']}/restore", {})
    assert get(api, f"kpis/{kpi['id']}")["links"] == links
    assert [goal_of(api, g["id"])["status"] for g in (goal, other)] == ["green", "red"]
    for g in (goal, other):
        assert goal_of(api, g["id"])["kpis"][0]["archived_at"] is None


def test_archived_kpis_do_not_change_remaining_measure_or_owner_fallback(api):
    goal, removed = goal_with_kpi(api, {"kind": "maintain", "min": 40, "max": 60})
    kept = post(api, "kpis", {"name": "Remaining", "goal_id": goal["id"],
                              "kind": "maintain", "min": 40, "max": 60})["kpi"]
    log(api, removed["id"], 100, period_end=H.now())
    log(api, kept["id"], 50, period_end=H.now())
    assert goal_of(api, goal["id"])["status"] == "red"
    post(api, f"kpis/{removed['id']}/archive", {})
    current = goal_of(api, goal["id"])
    assert current["status"] == "green" and current["status_note"].startswith("Remaining")
    post(api, f"kpis/{kept['id']}/archive", {})
    assert "only measure" not in goal_of(api, goal["id"])["status_note"]
    fallback, measure = goal_with_kpi(api, {"kind": "maintain", "min": 1})
    post(api, f"goals/{fallback['id']}/checkins", {"body": "On track", "signal": "on_track"})
    post(api, f"kpis/{measure['id']}/archive", {})
    assert goal_of(api, fallback["id"])["status"] == "green"
    post(api, f"goals/{goal['id']}/status", {"status": "yellow", "note": "Person's call"})
    post(api, f"kpis/{removed['id']}/restore", {})
    assert goal_of(api, goal["id"])["status_source"] == "person"
    assert goal_of(api, goal["id"])["status"] == "yellow"
