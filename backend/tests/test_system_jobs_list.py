"""The System job rows on Automations show exactly what Tico runs here: the mirrors follow system_routines.JOBS on every
automations tick (automations `sync_system_mirrors`), a job that cannot run on this install is left out of the reads
(`Job.applies`), every job records its runs, and every key a block records under is a JOBS job."""

import dataclasses
import json
import logging
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from backend import automations
from backend import redesign_migration as M
from backend import system_routines as SR
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.mirror_rows import db, dead_mirror, live, mirrors, sync
from backend.tests.helpers.api import get  # noqa: F401

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def test_nothing_is_made_before_the_cutover_is_done(api):
    with db(api).transaction() as c:
        c.execute("DELETE FROM automations WHERE json_extract(flow,'$.trigger.name')='tico.builtin.job.thread_rank'")
        c.execute("UPDATE registry_metadata SET value=json_set(value,'$.state','running') WHERE key=?", (M.CUTOVER,))
    assert sync(api) == {"made": [], "retired": [], "renamed": []}
    assert live(api, "tico.builtin.job.thread_rank") == []


def test_the_mirror_sync_makes_retires_renames_and_waits_out_a_broken_write(api, monkeypatch, caplog):
    # the sync makes missing mirrors retires dead ones and a second pass changes nothing
    sync(api)  # whatever the start left, settled
    with db(api).read() as c:
        assert M.state(c)["state"] == "done" and not M.state(c).get("owner_wait"), "the cutover ran at start"
    with db(api).transaction() as c:  # a job added after this install converted
        c.execute("DELETE FROM automations WHERE json_extract(flow,'$.trigger.name')='tico.builtin.job.thread_rank'")
    still_wanted = dead_mirror(
        api, "Still wanted", "job.still_wanted", {"kind": "system_job", "id": "job:still_wanted"}
    )
    exceptions = dead_mirror(
        api,
        "Engineering exceptions",
        "job.engineering_exceptions",
        {"kind": "system_job", "id": "job:engineering_exceptions"},
    )
    health = dead_mirror(api, "Health Monitor", "health", {"kind": "bot", "id": "health-monitor"})
    twin = dead_mirror(api, "Goal colours", "job.goal_colours", {"kind": "system_job", "id": "job:goal_colours"})
    with db(api).read() as c:
        bot = H.bot(c, "health-monitor")
        assert not bot or bot["state"] == "archived", "the Health Monitor is retired on this install"

    done = sync(api)
    assert set(done["retired"]) == {still_wanted, exceptions, health, twin}
    (made,) = done["made"]
    assert live(api, "tico.builtin.job.thread_rank") == [made]
    rows = mirrors(api)
    for aid in (still_wanted, exceptions, health, twin):
        assert rows[aid][2] and rows[aid][3] == 0, "soft-deleted and off, as a delete retires an automation"
    assert len(live(api, "tico.builtin.job.goal_colours")) == 1, "the first mirror of a job stays"
    with db(api).read() as c:
        mark = c.execute(
            "SELECT automation FROM redesign_migrated WHERE kind='system_job' AND id='job:thread_rank'"
        ).fetchone()
    assert mark["automation"] == made, "marked as the cutover marks a mirror"

    # Every job has exactly one live mirror, and nothing else is a job mirror.
    names = [t for t, _, deleted, _ in mirrors(api).values() if not deleted and t.startswith("tico.builtin.job.")]
    assert sorted(names) == sorted("tico.builtin.job." + job.key for job in SR.JOBS)
    before = mirrors(api)
    assert sync(api) == {"made": [], "retired": [], "renamed": []}
    assert mirrors(api) == before
    # a mirror whose words drifted takes the jobs words
    (aid,) = live(api, "tico.builtin.job.goal_colours")
    with db(api).transaction() as c:
        c.execute("UPDATE automations SET flow=json_set(flow,'$.name','Old name') WHERE id=?", (aid,))
    assert sync(api)["renamed"] == [aid]
    with db(api).read() as c:
        flow = json.loads(c.execute("SELECT flow FROM automations WHERE id=?", (aid,)).fetchone()["flow"])
    assert flow["name"] == SR.BY_KEY["goal_colours"].name
    assert sync(api)["renamed"] == []
    # a task rule mirror (task rules ended with 0.6) is retired
    gone = dead_mirror(api, "Old rule", "task_rule", {"kind": "task_rule", "id": "task-rule-gone"})
    assert sync(api)["retired"] == [gone]
    assert mirrors(api)[gone][2]
    # the health monitor mirror comes back with its bot and follows its switch
    with db(api).transaction() as c:  # restored in Settings > Bots
        if H.bot(c, H.HEALTH_MONITOR):
            c.execute("UPDATE bots SET state='active' WHERE id=?", (H.HEALTH_MONITOR,))
        else:
            c.execute(
                "INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,'Health Monitor','active')",
                (H.HEALTH_MONITOR, H.HEALTH_MONITOR),
            )
    (made,) = sync(api)["made"]
    assert live(api, "tico.builtin.health") == [made]
    with db(api).read() as c:
        mark = c.execute(
            "SELECT automation FROM redesign_migrated WHERE kind='health_monitor' AND id='bot:health-monitor'"
        ).fetchone()
    assert mark["automation"] == made
    with db(api).transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id=?", (H.HEALTH_MONITOR,))
    assert sync(api)["renamed"] == [made] and mirrors(api)[made][3] == 0
    with db(api).transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE id=?", (H.HEALTH_MONITOR,))
    assert sync(api)["retired"] == [made]
    # retiring a twin points the cutover mark at the mirror kept
    (kept,) = live(api, "tico.builtin.job.goal_colours")
    twin = dead_mirror(api, "Goal colours", "job.goal_colours", {"kind": "system_job", "id": "job:goal_colours"})
    with db(api).transaction() as c:
        c.execute(
            "UPDATE redesign_migrated SET automation=? WHERE kind='system_job' AND id='job:goal_colours'", (twin,)
        )
    assert sync(api)["retired"] == [twin]
    with db(api).read() as c:
        mark = c.execute("SELECT automation FROM redesign_migrated WHERE kind='system_job' AND id='job:goal_colours'")
        report = {(i["kind"], i["id"]): i for i in M.inventory(c)}
        assert mark.fetchone()["automation"] == kept
    assert report[("system_job", "job:goal_colours")]["state"] == "on", "the report names the live mirror"
    # a mirror that cannot be written waits an hour and warns once
    gone = dead_mirror(api, "Old rule", "task_rule", {"kind": "task_rule", "id": "task-rule-gone"})
    clock, real = [1000.0], automations.remove

    def broken(*a, **k):
        raise RuntimeError("bad row")

    monkeypatch.setattr(automations, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(automations, "remove", broken)
    with caplog.at_level(logging.WARNING, logger="backend.automations"):
        for _ in range(3):
            assert sync(api)["retired"] == []
    assert sum("could not be synced" in r.getMessage() for r in caplog.records) == 1
    monkeypatch.setattr(automations, "remove", real)
    clock[0] += automations.MIRROR_RETRY_SECONDS - 1
    assert sync(api)["retired"] == [], "not tried again inside the hour"
    clock[0] += 1
    assert sync(api)["retired"] == [gone]


@pytest.fixture
def switch(monkeypatch):
    """kpi_asks applies only while `on["kpi"]` is true."""
    on = {"kpi": False}
    jobs = [
        dataclasses.replace(job, applies=lambda c, s: on["kpi"]) if job.key == "kpi_asks" else job for job in SR.JOBS
    ]
    monkeypatch.setattr(SR, "JOBS", jobs)
    monkeypatch.setattr(SR, "BY_KEY", {job.key: job for job in jobs})
    return on


def test_a_job_that_does_not_apply_is_left_out_of_the_list_runs_and_counts_and_comes_back(api, switch):
    sync(api)
    (aid,) = live(api, "tico.builtin.job.kpi_asks")
    at = H.shift(H.now(), minutes=-1)
    with db(api).transaction() as c:
        c.execute(
            "INSERT INTO system_runs(id,job,started_at,finished_at,ok,result,runs) VALUES('k1','kpi_asks',?,?,1,'Asked 2',1)",
            (at, at),
        )

    def seen():
        listed = {a["id"] for a in get(api, "automations?limit=500")["automations"]}
        runs = {r["id"] for r in get(api, "automations/runs?range=1h&trigger=system")["runs"]}
        counted = {s["id"] for s in get(api, "automations/stats?range=1h")["automations"]}
        routines = {j["key"] for j in get(api, "system-routines")["jobs"]}
        return aid in listed, "sys_k1" in runs, aid in counted, "kpi_asks" in routines

    assert seen() == (False, False, False, False)
    assert sync(api) == {"made": [], "retired": [], "renamed": []}, "hidden in the reads, never retired"
    switch["kpi"] = True
    assert seen() == (True, True, True, True)


def test_a_job_whose_check_fails_stays_listed(api, monkeypatch):
    def broken(c, settings):
        raise RuntimeError("no such table")

    jobs = [dataclasses.replace(job, applies=broken) if job.key == "kpi_asks" else job for job in SR.JOBS]
    monkeypatch.setattr(SR, "JOBS", jobs)
    with db(api).read() as c:
        assert "kpi_asks" not in SR.not_applying(c, db(api).settings)


def test_on_a_plain_install_the_feature_jobs_are_hidden_and_the_rest_listed(api, monkeypatch):
    from backend import billing_check

    settings = db(api).settings
    monkeypatch.delenv(billing_check.ENV_KEY, raising=False)  # no HQ staff key
    monkeypatch.setattr(settings, "hq_staff_read_key", "", raising=False)
    monkeypatch.setattr(settings, "hosted", "", raising=False)  # not a hosted install
    monkeypatch.delenv("TICO_CELL", raising=False)
    keys = {j["key"] for j in get(api, "system-routines")["jobs"]}
    for hidden in (
        "directory_sync",
        "meeting_sync",
        "doc_sync",
        "billing_check",
        "hosted_update",
        "cloud_machines",
        "letters",
        "physical_mail",
    ):
        assert hidden not in keys, hidden
    for shown in ("topic_fresh", "heartbeat", "finance_upkeep", "inbound_mail", "never_stuck_reading"):
        assert shown in keys, shown


def runs_of(api, key):
    with db(api).read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM system_runs WHERE job=? ORDER BY started_at", (key,))]


SCHEDULER_JOBS = ("heartbeat", "screening", "finance_upkeep", "hourly_deadlines")
# Quiet on an install with nothing waiting, so four ticks keep one row each.
QUIET_JOBS = ("screening", "hourly_deadlines")  # the Heartbeat's quiet beats fold too: checked below
# The blocks that became Heartbeat steps (backend/heartbeat.py): each on its step row, none on a run row of its own.
HEARTBEAT_STEPS = (
    "stall_marks",
    "run_housekeeping",
    "ship_deployed",
    "never_stuck",
    "queue_check",
    "thread_loop_guard",
)


def step_rows(api):
    """The Heartbeat's step rows as the screen shows them: stored, with the counts memory holds until its next write."""
    from backend import heartbeat as HB

    with db(api).read() as c:
        return HB.merged_steps(c, db(api))


def applying(monkeypatch, *keys):
    """These jobs apply here, as once their feature is set up."""
    for key in keys:
        monkeypatch.setitem(SR.BY_KEY, key, dataclasses.replace(SR.BY_KEY[key], applies=None))


def test_scheduler_jobs_and_background_passes_record_their_runs(api, monkeypatch):
    store = db(api)
    # a tick records each scheduler job and quiet repeats fold
    scheduler = Scheduler(store, api.app.state.execution)
    at = datetime.now(timezone.utc)
    for n in range(4):
        scheduler.tick(at + timedelta(seconds=10 * n))
    for key in SCHEDULER_JOBS:
        found = runs_of(api, key)
        assert found and all(r["ok"] and r["result"] for r in found), (key, found)
    for key in QUIET_JOBS:
        assert len(runs_of(api, key)) == 1, (key, runs_of(api, key))
    beats = [r["result"] for r in runs_of(api, "heartbeat")]
    assert beats.count("All quiet") == 1, beats  # the first beat may do something; the quiet ones after it fold
    steps = step_rows(api)
    for key in HEARTBEAT_STEPS:
        assert steps[key]["last_result"] and not steps[key]["last_error"], (key, steps[key])
        assert runs_of(api, key) == [], key
    assert steps["run_housekeeping"]["runs_count"] == 4 and steps["stall_marks"]["runs_count"] == 1, (
        "every beat; a minute"
    )
    # The Company page is closed on a new install: a beat with nothing due is quiet, on its step row only.
    assert steps["company_page"]["last_result"] == "Nothing due" and steps["company_page"]["last_did_at"] is None
    assert runs_of(api, "company_page") == []
    with db(api).read() as c:
        kept = [r["steps"] for r in c.execute("SELECT steps FROM heartbeat_beats")]
    assert kept == [], kept
    # a failing block records its failure
    from backend import never_stuck

    def broken(c, now=None, failures=None):
        raise RuntimeError("private words")

    monkeypatch.setattr(never_stuck, "check", broken)
    failures = Scheduler(db(api), api.app.state.execution).tick()["failures"]  # a Heartbeat step: the rest still ran
    assert {"never_stuck": "RuntimeError"} in failures
    step = step_rows(api)["never_stuck"]
    assert step["last_error"] == "Failed: unexpected error (RuntimeError)" and step["failed_count"] == 1
    run = runs_of(api, "heartbeat")[-1]
    assert not run["ok"] and run["result"] == "Failed: Never-stuck check"
    assert "private" not in json.dumps(step) + json.dumps(run)
    # a background pass records one run and quiet passes fold
    for _ in range(3):
        SR.pass_of(store, "inbound_mail", lambda: None)
    (run,) = runs_of(api, "inbound_mail")
    assert run["result"] == "Done" and run["ok"]
    assert (
        SR.pass_of(store, "physical_mail", lambda: 2, words=lambda n: SR.count_words(n, "letter read", "letters read"))
        == 2
    )
    assert runs_of(api, "physical_mail")[-1]["result"] == "2 letters read"
    with pytest.raises(ValueError):
        SR.pass_of(store, "letters", lambda: int("x"))
    assert runs_of(api, "letters")[-1]["ok"] == 0
    # document and meeting sync record each pass
    api.app.state.doc_sources.tick()
    api.app.state.granola.record(0, 0)
    assert runs_of(api, "doc_sync") == runs_of(api, "meeting_sync") == [], (
        "not set up here: a quiet pass writes nothing"
    )
    applying(monkeypatch, "doc_sync", "meeting_sync")
    for _ in range(3):
        api.app.state.doc_sources.tick()
    (run,) = runs_of(api, "doc_sync")
    assert run["result"] == "Nothing to refresh"
    granola = api.app.state.granola
    granola.record(0, 0)
    granola.record(0, 0)
    assert [r["result"] for r in runs_of(api, "meeting_sync")] == ["Nothing due"]
    granola.record(2, 1)
    assert runs_of(api, "meeting_sync")[-1]["result"] == "2 syncs started, 1 connection could not be read"
    # a job that does not apply writes its failures but not its quiet passes
    with store.transaction() as c:
        c.execute("DELETE FROM system_runs WHERE job='letters'")
    for key in ("letters", "bookkeeper_check"):
        monkeypatch.setitem(SR.BY_KEY, key, dataclasses.replace(SR.BY_KEY[key], applies=lambda c, s: False))
    SR.pass_of(store, "letters", lambda: None)
    assert runs_of(api, "letters") == []
    with pytest.raises(ValueError):
        SR.pass_of(store, "letters", lambda: int("x"))
    assert [r["ok"] for r in runs_of(api, "letters")] == [0], "a failure always shows"
    # A controlled job's last start says when it runs next: it is written even where it does not apply.
    seen_before = len(runs_of(api, "bookkeeper_check"))
    SR.write(store, SR.Run("bookkeeper_check").did("Nothing new", quiet=True))
    assert len(runs_of(api, "bookkeeper_check")) == seen_before + 1
    # a failure that repeats folds into the first and counts the repeats
    with store.transaction() as c:
        c.execute("DELETE FROM system_runs WHERE job='screening'")
    long_ago = H.shift(H.now(), seconds=-(SR.QUIET_WRITE_SECONDS + 60))
    with store.transaction() as c:
        first = SR.save(
            c, SR.Run("screening", started=long_ago, error="Failed: the database was busy"), finished=long_ago
        )
    for _ in range(3):  # once more after the interval, then twice inside it: no write
        SR.write(store, SR.Run("screening", error="Failed: the database was busy"))
    (row,) = runs_of(api, "screening")
    assert row["id"] == first and row["runs"] == 2 and row["first_started_at"] == long_ago and not row["ok"]
    SR.write(store, SR.Run("screening", error="Failed: Tico refused it"))
    SR.write(store, SR.Run("screening").did("Nothing due", quiet=True))
    SR.write(store, SR.Run("screening", error="Failed: Tico refused it"))
    assert [(r["ok"], r["result"]) for r in runs_of(api, "screening")] == [
        (0, "Failed: the database was busy"),
        (0, "Failed: Tico refused it"),
        (1, "Nothing due"),
        (0, "Failed: Tico refused it"),
    ], "another reason, or a success between, is a new row"
    # a topic check records a fresh start and folds kept chats
    from backend import topic_change
    from backend.auth import Identity

    who = Identity("human:ana", "owner", "ana@acme.example")
    for _ in range(2):
        topic_change._audit(db(api), who, {"label": "topic-change@1", "clear": False})
    topic_change._audit(db(api), who, {"label": "topic-change@1", "clear": True})
    topic_change._audit(db(api), who, {"label": "topic-change@1", "clear": False, "error": "timeout"})
    assert [(r["result"], r["runs"]) for r in runs_of(api, "topic_fresh")] == [
        ("Same topic", 1),
        ("New topic: started fresh", 1),
        ("Kept the chat: the check took too long", 1),
    ]


# ----------------------------------------------------------------------------- every recorded key is a job
KEY_PATTERNS = (
    r"(?:SR|system_routines)\.Run\(\s*['\"]([a-z_]+)['\"]",
    r"(?:SR|system_routines)\.recording\(\s*[\w.]+\s*,\s*['\"]([a-z_]+)['\"]",
    r"(?:SR|system_routines)\.pass_of\s*[,(]\s*[\w.]+\s*,\s*['\"]([a-z_]+)['\"]",
    r"\brecorded\(\s*['\"]([a-z_]+)['\"]",  # app.py's loop helper: recorded("key", work)
    r"\bto_thread\(\s*recorded\s*,\s*['\"]([a-z_]+)['\"]",  # ...or to_thread(recorded, "key", work)
)


def recorded_keys():
    found = {}
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text()
        for pattern in KEY_PATTERNS:
            for key in re.findall(pattern, text):
                found.setdefault(key, path.name)
    return found


def test_every_key_a_block_records_under_is_a_job():
    found = recorded_keys()
    assert {"heartbeat", "release_check", "inbound_mail", "doc_sync", "never_stuck_reading"} <= set(found)
    from backend import heartbeat

    assert not set(found) & set(heartbeat.BY_KEY), "a Heartbeat step records on its step row"
    assert {key: where for key, where in found.items() if key not in SR.BY_KEY} == {}
