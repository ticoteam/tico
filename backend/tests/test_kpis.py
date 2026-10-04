"""KPIs and the Goal Manager (backend/kpis.py, backend/goals.py, docs/goals-and-kpis.md): the colour
arithmetic, a person's override, what the Goal Manager may not do without a confirm, the migration of
the old KPI rows and the automatic bot KPIs."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend import goals as G
from backend import hubdb, kpis as K
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner  # noqa: F401

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def stamp(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def reading(value, days_ago, quality="measured"):
    return {"id": f"r{days_ago}", "value": value, "period_end": stamp(days_ago), "period_start": stamp(days_ago + 7),
            "collected_at": stamp(days_ago), "created": stamp(days_ago), "quality": quality}


ACTIVATION = {"name": "Activation", "unit": "%", "direction": "up", "cadence": "weekly"}
CLIMB = {"kind": "improve", "baseline": 40.0, "baseline_at": stamp(60), "target": 70.0, "deadline": "2026-11-28"}


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


def test_a_reason_says_a_word_unit_once_and_rounds_counts_to_whole_numbers():
    studios = {"name": "Paying studios", "unit": "studios", "direction": "up", "cadence": "weekly"}
    # 121 -> 149, and the line is at 134.9 today: said once, as a whole number.
    climb = {**CLIMB, "baseline": 121.0, "target": 149.0}
    assert colour(climb, 130, kpi=studios)["reason"] == "Paying studios 130 vs 135 studios needed on pace"
    assert colour(climb, 149, kpi=studios)["reason"] == "Paying studios 149 reached 149 studios"
    assert (K.amount(134.5, "studios"), K.amount(2.25, "%"), K.amount(1200, "$")) == ("135 studios", "2.3%", "$1,200")
    assert colour({"kind": "improve", "target": 150.0}, 140.4, kpi=studios)["reason"] == \
        "Paying studios 140, target 150 studios has no deadline"
    assert colour({"kind": "maintain", "min": 90.0, "max": None}, 85, kpi=studios)["reason"] == "Paying studios 85 below min 90 studios"
    assert colour({"kind": "none"}, 12.5, kpi=studios)["reason"] == "Paying studios 13 studios"
    # Months and percentages keep one decimal, rounded half up; a symbol stays on both sides.
    runway = {"name": "Runway", "unit": "months", "direction": "up", "cadence": "monthly"}
    assert colour({**CLIMB, "baseline": 6.0, "target": 12.25}, 7.25, kpi=runway)["reason"] == "Runway 7.3 vs 9.1 months needed on pace"
    assert colour(CLIMB, 52.25)["reason"] == "Activation 52.3% vs 54.9% needed on pace"


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


def test_a_persons_colour_sticks_until_they_hand_it_back(api):
    # The pace line starts at baseline_at and 40 is on it only at that instant: a reading taken later is behind by a
    # sliver (30 * seconds / 73 years), and past 0.08 s that is yellow, not green. So the baseline and the first
    # reading name the same instant, and the test no longer depends on how fast the machine is.
    start = H.now()
    goal, kpi = goal_with_kpi(api, {"kind": "improve", "baseline": 40, "baseline_at": start, "target": 70,
                                    "deadline": "2099-12-31"})
    assert goal["status"] is None                                       # no data: it stays unscored
    log(api, kpi["id"], 40, period_end=start)
    seen = goal_of(api, goal["id"])
    assert (seen["status"], seen["status_source"], seen["status_by"]) == ("green", "auto", "bot:goal-manager")
    assert [e["status_source"] for e in seen["events"] if e["field"] == "status"] == ["auto"]
    # Ana says red, in a sentence. It is hers: a reading the arithmetic likes does not take it back.
    post(api, f"goals/{goal['id']}/status", {"status": "red", "note": "The launch slipped a month."})
    log(api, kpi["id"], 41, period_end=H.shift(start, seconds=1))       # the arithmetic still likes it
    mine = goal_of(api, goal["id"])
    assert (mine["status"], mine["status_source"], mine["status_by"]) == ("red", "person", "human:ana")
    assert mine["status_note"] == "The launch slipped a month."
    assert mine["suggest_status"] == "green" and "needed on pace" in mine["suggest_note"]    # a visible suggestion, no change
    post(api, "goals/refresh", {})                                      # the pass suggests again and never overwrites
    assert goal_of(api, goal["id"])["status"] == "red"
    # Only a person hands it back, and the colour is worked out at once.
    post(api, f"goals/{goal['id']}/status/auto", {}, token="cara-test", expected=403)
    back = post(api, f"goals/{goal['id']}/status/auto", {})["goal"]
    assert (back["status"], back["status_source"], back["status_by"], back["suggest_status"]) == ("green", "auto", "bot:goal-manager", None)
    post(api, f"goals/{goal['id']}/status/auto", {}, expected=422)      # already automatic
    events = goal_of(api, goal["id"])["events"]
    assert [(e["new"], e["status_source"], e["status_by"]) for e in events if e["field"] == "status"] == [
        ("green", "auto", "bot:goal-manager"), ("red", "person", "human:ana"), ("green", "auto", "bot:goal-manager")]
    # Time passing is what turns fresh data stale: a month on, with no new reading, the goal is gray, not green.
    with api.app.state.store.transaction() as c:
        G.refresh(c, at=datetime.now(timezone.utc) + timedelta(days=30))
    later = goal_of(api, goal["id"])
    assert later["status"] == "gray" and "missing" in later["status_note"] and later["status_source"] == "auto"


def test_a_goal_without_kpis_is_coloured_by_its_owners_check_in_or_stays_gray(api):
    goal = post(api, "goals", {"title": "Close the month on time", "owner": "ana"})["goal"]
    post(api, "goals/refresh", {})
    assert goal_of(api, goal["id"])["status"] is None                   # nothing to go on: no colour at all
    post(api, f"goals/{goal['id']}/checkins", {"body": "Month end is on Friday and two vendors are late.", "signal": "at_risk"})
    said = goal_of(api, goal["id"])
    assert (said["status"], said["status_source"]) == ("yellow", "auto") and said["status_note"].startswith("Check-in")
    assert said["checkins"][0]["signal"] == "at_risk"
    post(api, f"goals/{goal['id']}/checkins", {"body": "Nothing to say.", "signal": "bogus"}, expected=422)


def test_archive_restore_preserves_kpi_history_and_hides_it_from_active_views(api):
    goal, kpi = goal_with_kpi(api)
    logged = log(api, kpi["id"], 52, evidence="https://bi.example/activation", note="September", period_end=H.now())
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
    assert detail["links"] == original["links"]
    assert detail["readings"][0]["id"] == logged["id"]
    assert all(row["id"] != kpi["id"] for row in goal_of(api, goal["id"])["kpis"])
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


def test_only_kpi_owner_or_manager_above_can_archive_and_goal_manager_is_refused(api):
    _, kpi = goal_with_kpi(api)
    post(api, f"kpis/{kpi['id']}/archive", {}, token="cara-test", expected=403)

    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('goal-manager','Goal Manager','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('goal-manager','{}',NULL,'ana')")
    machine = runner(api)
    assign(api, machine, "goal-manager")
    ready(api, machine, ["goal-manager"])
    post(api, "chat/goal-manager", {"text": "Run the pass."})
    token = claim(api, machine)["token"]
    post(api, f"kpis/{kpi['id']}/archive", {}, token=token, expected=403)


def test_the_goal_manager_reads_and_writes_facts_but_a_target_needs_the_owners_confirm(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) "
                  "VALUES('goal-manager','Goal Manager','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator) VALUES('goal-manager','{}',NULL,'ana')")
    machine = runner(api)
    assign(api, machine, "goal-manager")
    ready(api, machine, ["goal-manager"])
    post(api, "chat/goal-manager", {"text": "Run the pass."})
    token = claim(api, machine)["token"]
    goal, kpi = goal_with_kpi(api, {"kind": "improve", "baseline": 40, "target": 70, "deadline": "2099-12-31"}, token="ben-test", owner="ben")
    # It writes the readings and the automatic colours...
    log(api, kpi["id"], 45, token=token, evidence="https://analytics.example/q/12", period_end=H.now())
    assert goal_of(api, goal["id"], token)["status_source"] == "auto"
    assert post(api, "goals/refresh", {}, token=token)["checked"] >= 1
    # ...and nothing else: not the target, the definition, a colour, a new KPI or a new link.
    for path, body in ((f"goals/{goal['id']}/kpis/{kpi['id']}", {"kind": "improve", "target": 50, "deadline": "2099-01-01"}),
                       (f"kpis/{kpi['id']}", {"definition": "Whatever is easiest to hit"}),
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
    conn.execute("INSERT INTO goals (id, title, owner, status, status_note, status_by, created, created_by, updated) "
                 "VALUES ('g1','Grow','company','green','On track','human:ana','2026-01-01T00:00:00Z','human:ana','2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO kpis (id, goal_id, name, unit, target, created, created_by) VALUES "
                 "('k1','g1','Signups','signups',500,'2026-01-02T00:00:00Z','human:ana'), "
                 "('k2','g1','NPS','',NULL,'2026-01-02T00:00:00Z','human:ana')")
    conn.execute("INSERT INTO kpi_readings (id, kpi_id, ts, value, actor, source, note, created) VALUES "
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


def test_every_bot_has_automatic_kpis_computed_from_ticos_own_data_and_they_link_like_any_kpi(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Reconcile March", "body": "Do it."})
    post(api, f"tasks/{task['id']}", {"version": task["version"], "status": "doing"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", (H.now(), task["id"]))
        c.execute("INSERT INTO approvals (id, kind, payload_json, payload_hash, requested_by, decision, decided_by, decided_at, created) "
                  "VALUES ('a1','send','{}','h1','bot:ops','approved','human:ana',?,?), "
                  "('a2','send','{}','h2','bot:ops','declined','human:ana',?,?)", (H.now(), H.now(), H.now(), H.now()))
        c.execute("INSERT INTO turns (id, bot, started, cost) VALUES ('t1','ops',?,0.25), ('t2','ops',?,0.5)", (H.now(), H.now()))
    shown = {k["id"]: k for k in get(api, "bots/ops/kpis")["kpis"]}
    assert set(shown) == {f"auto:ops:{m}" for m in ("tasks_done_7d", "first_response_min", "approval_rate_30d", "failed_runs_7d", "cost_7d")}
    assert shown["auto:ops:tasks_done_7d"]["latest"]["value"] == 1 and shown["auto:ops:approval_rate_30d"]["latest"]["value"] == 50
    assert shown["auto:ops:cost_7d"]["latest"]["value"] == 0.75 and shown["auto:ops:failed_runs_7d"]["latest"]["value"] == 0
    assert shown["auto:ops:first_response_min"]["latest"] is None and shown["auto:ops:first_response_min"]["status"] == "gray"   # nothing to measure is not zero
    # Reading them takes Read on the bot; a linked one is judged like any KPI, on the link's target.
    get(api, "bots/inbox/kpis", "cara-test", expected=404)
    goal = post(api, "goals", {"title": "Get the books done", "owner": "bot:ops"})["goal"]
    linked = post(api, f"goals/{goal['id']}/kpis", {"kpi_id": "auto:ops:tasks_done_7d", "kind": "improve", "baseline": 0,
                                                  "target": 5, "deadline": "2099-12-31"})["kpi"]
    assert linked["auto"] and linked["latest"]["value"] == 1 and linked["status"] in ("green", "yellow", "red")
    assert goal_of(api, goal["id"])["status_source"] == "auto"
    assert get(api, "kpis/auto:ops:cost_7d")["kpi"]["owner"] == "bot:ops"
    post(api, "kpis/auto:ops:cost_7d/readings", {"value": 1}, expected=422)       # computed, never logged
