"""A demo built at a moment other than the one the section tests use keeps its invariants. Each build is the expensive
part (~45 s in the gate), so each moment is built once and every invariant that depends on the build's moment is read
from it: no task left stuck, nothing dated after the build, every message body kept, the market dated to the build's
year, and (for the mid-month build) Finance's default month and the early-morning Learnings nights."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import time_machine
from fastapi.testclient import TestClient

from backend import demo
from backend.app import create_app
from backend.tests import test_demo_finance as finance

# 00:30 Pacific on the 15th, in another year and mid-month: tonight's schedule (02:00) and Learnings night (03:00) have
# not happened yet.
EARLY = datetime(2027, 1, 15, 8, 30, tzinfo=timezone.utc)
# The machine's clock when the second demo is built, months after the day it is built for.
MACHINE = datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc)
# Chosen for its edges: Sun 2026-03-08 19:00 PDT is already Monday 02:00 in UTC (the UTC date is ahead of the Pacific
# one, the fix f5a873860 case), a Pacific evening, the Sunday to Monday edge of a growth window, and the night after
# spring-forward. It is months before MACHINE.
LONG_AFTER = datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc)
# Columns that hold a future time by design: a schedule's next run, a message's expiry, and a task's due date.
FUTURE_BY_DESIGN = {"next_due_at", "expires_at", "due_at", "check_back_at"}


def signed_in(settings):
    client = TestClient(create_app(settings), base_url="http://127.0.0.1:8765")
    client.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    return client


def nothing_is_dated_after(settings, moment, label, run_on_the_machines_clock=False):
    """`run_on_the_machines_clock`: a build whose machine clock is not the build's moment stamps each system run with
    the machine's own start time, so those two columns are left out (the other build reads them at its own moment)."""
    cut = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    later = []
    with closing(sqlite3.connect(settings.db_path)) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for column in [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]:
                if column in FUTURE_BY_DESIGN or (run_on_the_machines_clock and table == "system_runs"
                                                  and column in ("started_at", "first_started_at")):
                    continue
                count = db.execute(
                    f'SELECT count(*) FROM "{table}" WHERE typeof("{column}")=\'text\' AND "{column}" > ? '
                    f'AND "{column}" LIKE \'20__-__-__T%\'' +
                    (" AND NOT (subject_type='task' AND kind IN ('check_back_at','check_back_explicit'))" if table == "history_events" and column in ("old", "new") else ""), (cut,)).fetchone()[0]
                if count:
                    later.append((table, column, count))
    assert later == [], f"{label}: rows dated after the build"


def every_message_body_is_kept(settings, label):
    """Retention runs on the demo's own clock while it is built: a demo built months before today keeps its bodies."""
    with closing(sqlite3.connect(settings.db_path)) as db:
        total = db.execute("SELECT COUNT(*) FROM inbound_messages").fetchone()[0]
        removed = db.execute("SELECT COUNT(*) FROM inbound_messages WHERE body IS NULL OR retention_removed=1").fetchone()[0]
    assert total == 11 and removed == 0, f"{label}: {removed} of {total} inbound message bodies were removed"


def no_task_is_stuck(api, label):
    assert api.get("/api/v2/tasks/stuck").json()["tasks"] == [], f"{label}: a task is left stuck"


def the_market_is_dated_to_the_year(api, year, label):
    spend = api.get("/api/v2/market/spend").json()
    insights = api.get("/api/v2/market/insights").json()["insights"]
    assert spend["year"] == spend["definition"]["year"] == year, label
    listening = [i for i in insights if i["about"].startswith("Listening:")]
    urls = [e["source_url"] for seg in spend["segments"] for row in seg["estimates"] for e in row["evidence"]]
    urls += [i["source_url"] for i in listening]
    dated = [url for url in urls if str(year - 1) in url or str(year) in url]
    assert dated and all(f"-{year}" in url for url in dated), f"{label}: a source url is dated to another year"
    assert any(str(year) in i["quote"] for i in listening), f"{label}: no listening quote names {year}"
    assert not any(str(year - 1) in i["quote"] + i["claim"] for i in listening), (
        f"{label}: a listening quote or claim names {year - 1}")


def test_a_demo_built_early_in_the_day_in_another_year_keeps_its_invariants(tmp_path):
    label = "built 00:30 Pacific on 2027-01-15"
    with time_machine.travel(EARLY, tick=False):
        settings = demo.build(tmp_path, now=EARLY)
        # Read from the database before any app has opened it.
        nothing_is_dated_after(settings, EARLY, label)
        every_message_body_is_kept(settings, label)
        with signed_in(settings) as api:
            no_task_is_stuck(api, label)
            the_market_is_dated_to_the_year(api, 2027, label)
            nights = [night["night"] for night in api.get("/api/v2/learnings").json()["nights"]]
            assert len(nights) == 6 and "2027-01-15" not in nights, f"{label}: a night that has not finished is seeded"
            ids = {row["name"]: row["id"] for row in api.get("/api/v2/automations?limit=100").json()["automations"]}
            runs = api.get(f"/api/v2/automations/{ids['Nightly orders to bookkeeping']}/runs?limit=100").json()["runs"]
            assert len(runs) == 6, f"{label}: nightly orders to bookkeeping has {len(runs)} runs"
        # Finance reads its default month from the clock, so every test of test_demo_finance.py runs on this
        # mid-month build too.
        for name in sorted(vars(finance)):
            if name.startswith("test_"):
                try:
                    getattr(finance, name)((settings, EARLY))
                except AssertionError as error:
                    raise AssertionError(f"{label}: {name}") from error


def test_a_demo_built_long_after_its_dates_keeps_its_invariants(tmp_path):
    label = "built 2026-03-09 02:00 UTC, months before the machine's clock"
    with time_machine.travel(MACHINE, tick=False):
        settings = demo.build(tmp_path, now=LONG_AFTER)
    nothing_is_dated_after(settings, LONG_AFTER, label, run_on_the_machines_clock=True)
    every_message_body_is_kept(settings, label)
    with time_machine.travel(LONG_AFTER, tick=False), signed_in(settings) as api:
        no_task_is_stuck(api, label)
        the_market_is_dated_to_the_year(api, LONG_AFTER.year, label)
