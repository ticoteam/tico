"""Demo automations, Learnings nights and Tico release updates: built as the demo builds them, read back as Ana."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.demo_sections.schedules import SETTLE
from backend.scheduler import next_due

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    directory = tmp_path_factory.mktemp("demo_automations")
    return directory, demo.build(directory, now=NOW)


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def automation_ids(api):
    return {row["name"]: row["id"] for row in api.get("/api/v2/automations?limit=100").json()["automations"]}


def runs_of(api, aid):
    body = api.get(f"/api/v2/automations/{aid}/runs?limit=100").json()
    return body["runs"], body["daily"]


def test_each_automation_shows_its_runs_and_drops(built):
    _, settings = built
    with signed_in(settings) as api:
        ids = automation_ids(api)
        calibration, _ = runs_of(api, ids["Calibration failures to technical support"])
        assert len(calibration) == 14 and {run["status"] for run in calibration} == {"ok"}
        twice, daily = runs_of(api, ids["Calibration failed twice to Ben"])
        assert len(twice) == 2
        assert sum(day["dropped"] for day in daily) == 12
        nightly, _ = runs_of(api, ids["Nightly orders to bookkeeping"])
        assert len(nightly) == 7 and {run["status"] for run in nightly} == {"ok"}
        reviews, daily = runs_of(api, ids["1-star reviews to reputation"])
        assert len(reviews) == 2 and sum(day["dropped"] for day in daily) == 4


def test_the_run_guard_held_a_loop_and_ben_dropped_it(built):
    _, settings = built
    with signed_in(settings) as api:
        orders = automation_ids(api)["Marketplace orders to inventory"]
        runs, daily = runs_of(api, orders)
        # The loop's runs are the ones the guard held; Ben's drop skipped them, and the orders after it ran.
        assert {run["status"] for run in runs} == {"ok", "skipped"}
        assert sum(day["runs"] for day in daily) == 128
        assert api.get(f"/api/v2/automations/{orders}").json().get("hold") is None


def test_learnings_shows_a_week_of_nights(built):
    _, settings = built
    with signed_in(settings) as api:
        nights = api.get("/api/v2/learnings").json()["nights"]
        assert len(nights) == 7
        assert sorted(night["status"] for night in nights) == ["done"] * 6 + ["quiet"]
        correction = next(night for night in nights if night["night"] == "2026-09-21")
        packet = api.get(f"/api/v2/learnings/{correction['id']}/packets/support-lead")
        assert packet.status_code == 200
        assert any("refund" in item["text"].lower() for item in packet.json()["items"])


def task_of(run):
    return next(step["outputs"]["task"] for step in run["steps"] if (step.get("outputs") or {}).get("task"))


def test_last_night_shows_the_routed_correction_with_its_source(built):
    _, settings = built
    with signed_in(settings) as api:
        nights = api.get("/api/v2/learnings").json()["nights"]
        last = nights[0]
        assert last["night"] == "2026-09-25" and last["updates"] == 1
        detail = api.get(f"/api/v2/learnings/{last['id']}").json()
        [bot] = detail["sections"]["bots"]
        assert bot["id"] == "technical-support" and "1 task" in bot["saw"]
        [entry] = bot["entries"]
        assert "flat surface" in entry["title"]
        [source] = [s for s in entry["sources"] if "Correction" in s["quote"]]
        assert source["label"].startswith("task Ben in Call back owners whose calibration failed twice")
        assert "Before calibrating" in entry["change"]["diff"]


def test_the_night_before_saves_a_smaller_change(built):
    _, settings = built
    with signed_in(settings) as api:
        night = next(n for n in api.get("/api/v2/learnings").json()["nights"] if n["night"] == "2026-09-23")
        assert night["updates"] == 1
        [bot] = api.get(f"/api/v2/learnings/{night['id']}").json()["sections"]["bots"]
        [entry] = bot["entries"]
        assert "hold the phone still" in entry["title"].lower()
        [source] = [s for s in entry["sources"] if "hold the phone still" in s["quote"].lower()]
        assert source["label"].startswith("task Ben in Call back owners whose calibration failed twice")


LAST_HOUR = "2026-09-25T17:30:00.000000Z"   # the build's now is 18:30 UTC


def test_runs_spread_over_the_week_and_some_fall_in_the_last_hour(built):
    _, settings = built
    with signed_in(settings) as api:
        ids = automation_ids(api)
        calibration, _ = runs_of(api, ids["Calibration failures to technical support"])
        assert len({run["started_at"][:10] for run in calibration}) == 7
        recent = []
        for name in ("Calibration failures to technical support", "Calibration failed twice to Ben",
                     "Firmware rings to health monitor"):
            runs, _ = runs_of(api, ids[name])
            recent += [run for run in runs if run["started_at"] >= LAST_HOUR]
        assert len(recent) >= 4


def at_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def occurrences_between(trigger, start, end):
    """The schedule's own times after `start` and before `end`, worked out as the scheduler works them out."""
    due, found = next_due(trigger["cron"], start, trigger["tz"]), []
    while due < end:
        found.append(due)
        due = next_due(trigger["cron"], due, trigger["tz"])
    return found


def sweep_automation_id(settings):
    """The Assistant's daily sweep (routine key daily-sweep, templates/catalog/assistant/bot.yaml) as its automation:
    a routine moves to an automation (redesign_migration.adopt), and redesign_migrated keeps the pair."""
    with sqlite3.connect(settings.db_path) as db:
        [(aid,)] = db.execute("SELECT m.automation FROM redesign_migrated m JOIN schedules s ON s.id = m.id "
                              "WHERE m.kind='routine' AND s.routine_key='daily-sweep'").fetchall()
    return aid


def test_every_enabled_schedule_ran_at_its_times_this_week_and_is_next_in_the_future(built):
    _, settings = built
    with signed_in(settings) as api:
        rows = api.get("/api/v2/automations?limit=100").json()["automations"]
        enabled = {row["name"]: row["enabled"] for row in rows}
        assert enabled["Weekday inbox sweep"] is False
        schedules = [row for row in rows if row["enabled"] and row["trigger"]["type"] == "schedule"]
        # 16 on main, and the Assistant's daily sweep is the one new enabled schedule (backend/onboarding.py).
        assert len(schedules) == 17
        sweep = sweep_automation_id(settings)
        week_start = NOW - timedelta(days=7)
        for row in schedules:
            trigger = row["trigger"]
            if row["id"] == sweep:
                # Created at boot after the demo window (onboarding.ensure_builtin_routines), on the real clock, so it
                # has no run in the window. It is next at its first time after its creation.
                made = at_time(row["created_at"])
                assert at_time(row["next_run"]) > made, row["name"]
                assert at_time(row["next_run"]) == next_due(trigger["cron"], made, trigger["tz"]), row["name"]
                continue
            # Next is the schedule's first time after the build's now.
            assert at_time(row["next_run"]) > NOW, row["name"]
            assert at_time(row["next_run"]) == next_due(trigger["cron"], NOW, trigger["tz"]), row["name"]
            due = occurrences_between(trigger, week_start, NOW - SETTLE)
            assert due, row["name"]
            runs, _ = runs_of(api, row["id"])
            started = sorted(at_time(run["started_at"]) for run in runs if at_time(run["started_at"]) > week_start)
            # One finished run at each of its own times this week (weekdays only for a weekday schedule).
            assert [started_at.replace(microsecond=0) for started_at in started] == due, row["name"]
            assert {run["status"] for run in runs if at_time(run["started_at"]) > week_start} == {"ok"}, row["name"]
            assert row["last_run"] and at_time(row["last_run"]["started_at"]).replace(microsecond=0) == due[-1]


def test_the_ledger_and_declined_card_tasks_are_done_and_read_by_ana(built):
    _, settings = built
    with signed_in(settings) as api:
        ids = automation_ids(api)
        for name in ("Nightly orders to bookkeeping", "Declined cards to accounts receivable"):
            runs, _ = runs_of(api, ids[name])
            assert runs
            for run in runs:
                task = api.get(f"/api/v2/tasks/{task_of(run)}").json()["task"]
                assert task["status"] == "done"
                thread = api.get(f"/api/v2/threads/{task['conversation_id']}").json()
                assert thread["me"]["unread"] == 0


def test_each_deploy_closes_its_own_release_notes_task(built):
    _, settings = built
    with signed_in(settings) as api:
        runs, _ = runs_of(api, automation_ids(api)["Finished deploys to release notes"])
        statuses = {}
        for run in runs:
            task = api.get(f"/api/v2/tasks/{task_of(run)}").json()["task"]
            statuses[task["title"]] = task["status"]
        assert statuses == {"Write the release notes for app 3.1.0": "done",
                            "Write the release notes for cloud 1.8.1": "done",
                            "Write the release notes for cloud 1.8.2": "open"}


def test_the_packet_runs_leave_technical_support_its_focus(built):
    _, settings = built
    with signed_in(settings) as api:
        status = {row["bot"]: row for row in api.get("/api/v2/status").json()["bots"]}
        assert status["technical-support"]["focus"] == "Calibration callbacks"


def test_two_bots_hear_each_tico_release_after_the_first(built):
    directory, settings = built
    with sqlite3.connect(settings.db_path) as db:
        notes = sorted(to for (to,) in db.execute(
            "SELECT to_actor FROM notes WHERE body LIKE 'Subscribed update%'"))
    assert notes == ["bot:botops", "bot:botops", "bot:product-manager", "bot:product-manager"]


# 00:30 Pacific on the 25th: tonight's schedule (02:00) and Learnings night (03:00) have not happened yet.
EARLY = datetime(2026, 9, 25, 7, 30, tzinfo=timezone.utc)
# Columns that hold a future time by design: a schedule's next run, a message's expiry, and a task's due date.
FUTURE_BY_DESIGN = {"next_due_at", "expires_at", "due_at", "check_back_at"}


@pytest.fixture(scope="module")
def early(tmp_path_factory):
    directory = tmp_path_factory.mktemp("demo_automations_early")
    return directory, demo.build(directory, now=EARLY)


def test_nothing_is_dated_after_an_early_build(early):
    _, settings = early
    cut = EARLY.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    later = []
    with sqlite3.connect(settings.db_path) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for column in [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]:
                if column in FUTURE_BY_DESIGN:
                    continue
                count = db.execute(
                    f'SELECT count(*) FROM "{table}" WHERE typeof("{column}")=\'text\' AND "{column}" > ? '
                    f'AND "{column}" LIKE \'20__-__-__T%\'' +
                    (" AND NOT (subject_type='task' AND kind IN ('check_back_at','check_back_explicit'))" if table == "history_events" and column in ("old", "new") else ""), (cut,)).fetchone()[0]
                if count:
                    later.append((table, column, count))
    assert later == []


def test_a_night_that_has_not_finished_is_not_seeded(early):
    _, settings = early
    with signed_in(settings) as api:
        nights = [night["night"] for night in api.get("/api/v2/learnings").json()["nights"]]
        assert len(nights) == 6 and "2026-09-25" not in nights
        assert len(runs_of(api, automation_ids(api)["Nightly orders to bookkeeping"])[0]) == 6
