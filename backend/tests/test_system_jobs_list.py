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
from backend.tests.test_api import api, get  # noqa: F401

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def db(api):
    return api.app.state.store


def mirrors(api):
    """Every read-only built-in automation: {id: (trigger name, source id, deleted_at, enabled)}."""
    with db(api).read() as c:
        out = {}
        for row in c.execute("SELECT * FROM automations WHERE builtin=1 AND json_extract(flow,'$.readonly')=1"):
            flow = json.loads(row["flow"])
            out[row["id"]] = (flow["trigger"]["name"], (flow.get("source") or {}).get("id"), row["deleted_at"],
                              row["enabled"])
        return out


def live(api, name):
    return [aid for aid, (trigger, _, deleted, _) in mirrors(api).items() if trigger == name and not deleted]


def sync(api):
    M.catch_up(db(api))                                          # a cutover that waited for its owner, as the tick does
    with db(api).transaction() as c:
        return automations.sync_system_mirrors(c)


def dead_mirror(api, name, key, source, words="Gone"):
    """A mirror the cutover made for something Tico no longer runs."""
    with db(api).transaction() as c:
        who = M.system_who(c)
        flow = M._mirror(name, M._owner_person(who), key, {"on": True}, source, words)
        return M._save(c, who, flow)


def test_the_sync_makes_missing_mirrors_retires_dead_ones_and_a_second_pass_changes_nothing(api):
    sync(api)                                                    # whatever the start left, settled
    with db(api).read() as c:
        assert M.state(c)["state"] == "done" and not M.state(c).get("owner_wait"), "the cutover ran at start"
    with db(api).transaction() as c:                             # a job added after this install converted
        c.execute("DELETE FROM automations WHERE json_extract(flow,'$.trigger.name')='tico.builtin.job.thread_rank'")
    still_wanted = dead_mirror(api, "Still wanted", "job.still_wanted", {"kind": "system_job", "id": "job:still_wanted"})
    exceptions = dead_mirror(api, "Engineering exceptions", "job.engineering_exceptions",
                             {"kind": "system_job", "id": "job:engineering_exceptions"})
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
        mark = c.execute("SELECT automation FROM redesign_migrated WHERE kind='system_job' AND id='job:thread_rank'").fetchone()
    assert mark["automation"] == made, "marked as the cutover marks a mirror"

    # Every job has exactly one live mirror, and nothing else is a job mirror.
    names = [t for t, _, deleted, _ in mirrors(api).values() if not deleted and t.startswith("tico.builtin.job.")]
    assert sorted(names) == sorted("tico.builtin.job." + job.key for job in SR.JOBS)
    before = mirrors(api)
    assert sync(api) == {"made": [], "retired": [], "renamed": []}
    assert mirrors(api) == before


def test_a_mirror_whose_words_drifted_takes_the_jobs_words(api):
    sync(api)
    (aid,) = live(api, "tico.builtin.job.due_reminders")
    with db(api).transaction() as c:
        c.execute("UPDATE automations SET flow=json_set(flow,'$.name','Old name') WHERE id=?", (aid,))
    assert sync(api)["renamed"] == [aid]
    with db(api).read() as c:
        flow = json.loads(c.execute("SELECT flow FROM automations WHERE id=?", (aid,)).fetchone()["flow"])
    assert flow["name"] == SR.BY_KEY["due_reminders"].name
    assert sync(api)["renamed"] == []


def test_a_task_rule_mirror_whose_rule_is_gone_is_retired(api):
    sync(api)
    gone = dead_mirror(api, "Old rule", "task_rule", {"kind": "task_rule", "id": "task-rule-gone"})
    assert sync(api)["retired"] == [gone]
    assert mirrors(api)[gone][2]


def test_a_task_rule_mirror_takes_its_rules_name_and_switch(api):
    sync(api)
    rid = "task-rule-close-after-done"
    (aid,) = [a for a, (t, src, deleted, _) in mirrors(api).items() if t == "tico.builtin.task_rule" and src == rid
              and not deleted]
    with db(api).transaction() as c:
        c.execute("UPDATE task_rules SET name='Close finished work', enabled=0 WHERE id=?", (rid,))
    assert sync(api)["renamed"] == [aid]
    with db(api).read() as c:
        row = c.execute("SELECT name, enabled, flow FROM automations WHERE id=?", (aid,)).fetchone()
    assert (row["name"], row["enabled"], json.loads(row["flow"])["controls"]["on"]) == ("Close finished work", 0, False)
    assert sync(api)["renamed"] == []


def test_the_health_monitor_mirror_comes_back_with_its_bot_and_follows_its_switch(api):
    sync(api)
    with db(api).transaction() as c:                             # restored in Settings > Bots
        if H.bot(c, H.HEALTH_MONITOR):
            c.execute("UPDATE bots SET state='active' WHERE id=?", (H.HEALTH_MONITOR,))
        else:
            c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,'Health Monitor','active')",
                      (H.HEALTH_MONITOR, H.HEALTH_MONITOR))
    (made,) = sync(api)["made"]
    assert live(api, "tico.builtin.health") == [made]
    with db(api).read() as c:
        mark = c.execute("SELECT automation FROM redesign_migrated WHERE kind='health_monitor' "
                         "AND id='bot:health-monitor'").fetchone()
    assert mark["automation"] == made
    with db(api).transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id=?", (H.HEALTH_MONITOR,))
    assert sync(api)["renamed"] == [made] and mirrors(api)[made][3] == 0
    with db(api).transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE id=?", (H.HEALTH_MONITOR,))
    assert sync(api)["retired"] == [made]


def test_retiring_a_twin_points_the_cutover_mark_at_the_mirror_kept(api):
    sync(api)
    (kept,) = live(api, "tico.builtin.job.goal_colours")
    twin = dead_mirror(api, "Goal colours", "job.goal_colours", {"kind": "system_job", "id": "job:goal_colours"})
    with db(api).transaction() as c:
        c.execute("UPDATE redesign_migrated SET automation=? WHERE kind='system_job' AND id='job:goal_colours'", (twin,))
    assert sync(api)["retired"] == [twin]
    with db(api).read() as c:
        mark = c.execute("SELECT automation FROM redesign_migrated WHERE kind='system_job' AND id='job:goal_colours'")
        report = {(i["kind"], i["id"]): i for i in M.inventory(c)}
        assert mark.fetchone()["automation"] == kept
    assert report[("system_job", "job:goal_colours")]["state"] == "on", "the report names the live mirror"


def test_a_mirror_that_cannot_be_written_waits_an_hour_and_warns_once(api, monkeypatch, caplog):
    sync(api)
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


def test_nothing_is_made_before_the_cutover_is_done(api):
    with db(api).transaction() as c:
        c.execute("DELETE FROM automations WHERE json_extract(flow,'$.trigger.name')='tico.builtin.job.thread_rank'")
        c.execute("UPDATE registry_metadata SET value=json_set(value,'$.state','running') WHERE key=?", (M.CUTOVER,))
    assert sync(api) == {"made": [], "retired": [], "renamed": []}
    assert live(api, "tico.builtin.job.thread_rank") == []


@pytest.fixture
def switch(monkeypatch):
    """kpi_asks applies only while `on["kpi"]` is true."""
    on = {"kpi": False}
    jobs = [dataclasses.replace(job, applies=lambda c, s: on["kpi"]) if job.key == "kpi_asks" else job for job in SR.JOBS]
    monkeypatch.setattr(SR, "JOBS", jobs)
    monkeypatch.setattr(SR, "BY_KEY", {job.key: job for job in jobs})
    return on


def test_a_job_that_does_not_apply_is_left_out_of_the_list_runs_and_counts_and_comes_back(api, switch):
    sync(api)
    (aid,) = live(api, "tico.builtin.job.kpi_asks")
    at = H.shift(H.now(), minutes=-1)
    with db(api).transaction() as c:
        c.execute("INSERT INTO system_runs(id,job,started_at,finished_at,ok,result,runs) VALUES('k1','kpi_asks',?,?,1,'Asked 2',1)",
                  (at, at))

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
    monkeypatch.delenv(billing_check.ENV_KEY, raising=False)    # no HQ staff key
    monkeypatch.setattr(settings, "hq_staff_read_key", "", raising=False)
    monkeypatch.setattr(settings, "hosted", "", raising=False)  # not a hosted install
    monkeypatch.delenv("TICO_CELL", raising=False)
    keys = {j["key"] for j in get(api, "system-routines")["jobs"]}
    for hidden in ("directory_sync", "meeting_sync", "doc_sync", "billing_check", "hosted_update", "cloud_machines",
                   "letters", "physical_mail"):
        assert hidden not in keys, hidden
    for shown in ("topic_fresh", "due_reminders", "run_housekeeping", "finance_upkeep", "inbound_mail",
                  "assigned_tasks"):
        assert shown in keys, shown


def runs_of(api, key):
    with db(api).read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM system_runs WHERE job=? ORDER BY started_at", (key,))]


SCHEDULER_JOBS = ("stall_marks", "run_housekeeping", "ship_deployed", "task_loops", "queue_check", "milestone_waits",
                  "screening", "finance_upkeep", "hourly_deadlines", "thread_loop_guard", "snooze_ends")
# Quiet on an install with nothing waiting, so four ticks keep one row each.
QUIET_JOBS = ("run_housekeeping", "ship_deployed", "task_loops", "milestone_waits", "screening",
              "thread_loop_guard", "snooze_ends", "hourly_deadlines")


def test_a_tick_records_each_scheduler_job_and_quiet_repeats_fold(api):
    store = db(api)
    scheduler = Scheduler(store, api.app.state.execution)
    at = datetime.now(timezone.utc)
    for n in range(4):
        scheduler.tick(at + timedelta(seconds=10 * n))
    for key in SCHEDULER_JOBS:
        found = runs_of(api, key)
        assert found and all(r["ok"] and r["result"] for r in found), (key, found)
    for key in QUIET_JOBS:
        assert len(runs_of(api, key)) == 1, (key, runs_of(api, key))
    # The Company page is closed on a new install: its job does not apply, and a pass with nothing due writes nothing.
    assert runs_of(api, "company_page") == []


def test_a_failing_block_records_its_failure(api, monkeypatch):
    from backend import milestone_waits

    def broken(c, failures=None, task_id=None):
        raise RuntimeError("private words")
    monkeypatch.setattr(milestone_waits, "reconcile", broken)
    with pytest.raises(RuntimeError):
        Scheduler(db(api), api.app.state.execution).tick()
    (run,) = runs_of(api, "milestone_waits")
    assert not run["ok"] and run["result"] == "Failed: unexpected error (RuntimeError)"


def test_a_background_pass_records_one_run_and_quiet_passes_fold(api):
    store = db(api)
    for _ in range(3):
        SR.pass_of(store, "inbound_mail", lambda: None)
    (run,) = runs_of(api, "inbound_mail")
    assert run["result"] == "Done" and run["ok"]
    assert SR.pass_of(store, "physical_mail", lambda: 2, words=lambda n: SR.count_words(n, "letter read", "letters read")) == 2
    assert runs_of(api, "physical_mail")[-1]["result"] == "2 letters read"
    with pytest.raises(ValueError):
        SR.pass_of(store, "letters", lambda: int("x"))
    assert runs_of(api, "letters")[-1]["ok"] == 0


def applying(monkeypatch, *keys):
    """These jobs apply here, as once their feature is set up."""
    for key in keys:
        monkeypatch.setitem(SR.BY_KEY, key, dataclasses.replace(SR.BY_KEY[key], applies=None))


def test_document_and_meeting_sync_record_each_pass(api, monkeypatch):
    api.app.state.doc_sources.tick()
    api.app.state.granola.record(0, 0)
    assert runs_of(api, "doc_sync") == runs_of(api, "meeting_sync") == [], "not set up here: a quiet pass writes nothing"
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


def test_a_job_that_does_not_apply_writes_its_failures_but_not_its_quiet_passes(api, monkeypatch):
    store = db(api)
    for key in ("letters", "bookkeeper_check"):
        monkeypatch.setitem(SR.BY_KEY, key, dataclasses.replace(SR.BY_KEY[key], applies=lambda c, s: False))
    SR.pass_of(store, "letters", lambda: None)
    assert runs_of(api, "letters") == []
    with pytest.raises(ValueError):
        SR.pass_of(store, "letters", lambda: int("x"))
    assert [r["ok"] for r in runs_of(api, "letters")] == [0], "a failure always shows"
    # A controlled job's last start says when it runs next: it is written even where it does not apply.
    SR.write(store, SR.Run("bookkeeper_check").did("Nothing new", quiet=True))
    assert len(runs_of(api, "bookkeeper_check")) == 1


def test_a_failure_that_repeats_folds_into_the_first_and_counts_the_repeats(api):
    store = db(api)
    long_ago = H.shift(H.now(), seconds=-(SR.QUIET_WRITE_SECONDS + 60))
    with store.transaction() as c:
        first = SR.save(c, SR.Run("snooze_ends", started=long_ago, error="Failed: the database was busy"), finished=long_ago)
    for _ in range(3):                           # once more after the interval, then twice inside it: no write
        SR.write(store, SR.Run("snooze_ends", error="Failed: the database was busy"))
    (row,) = runs_of(api, "snooze_ends")
    assert row["id"] == first and row["runs"] == 2 and row["first_started_at"] == long_ago and not row["ok"]
    SR.write(store, SR.Run("snooze_ends", error="Failed: Tico refused it"))
    SR.write(store, SR.Run("snooze_ends").did("Nothing due", quiet=True))
    SR.write(store, SR.Run("snooze_ends", error="Failed: Tico refused it"))
    assert [(r["ok"], r["result"]) for r in runs_of(api, "snooze_ends")] == [
        (0, "Failed: the database was busy"), (0, "Failed: Tico refused it"), (1, "Nothing due"),
        (0, "Failed: Tico refused it")], "another reason, or a success between, is a new row"


def test_a_topic_check_records_a_fresh_start_and_folds_kept_chats(api):
    from backend import topic_change
    from backend.auth import Identity
    who = Identity("human:ana", "owner", "ana@acme.example")
    for _ in range(2):
        topic_change._audit(db(api), who, {"label": "topic-change@1", "clear": False})
    topic_change._audit(db(api), who, {"label": "topic-change@1", "clear": True})
    topic_change._audit(db(api), who, {"label": "topic-change@1", "clear": False, "error": "timeout"})
    assert [(r["result"], r["runs"]) for r in runs_of(api, "topic_fresh")] == [
        ("Same topic", 1), ("New topic: started fresh", 1), ("Kept the chat: the check took too long", 1)]


# ----------------------------------------------------------------------------- every recorded key is a job
KEY_PATTERNS = (
    r"(?:SR|system_routines)\.Run\(\s*['\"]([a-z_]+)['\"]",
    r"(?:SR|system_routines)\.recording\(\s*[\w.]+\s*,\s*['\"]([a-z_]+)['\"]",
    r"(?:SR|system_routines)\.pass_of\s*[,(]\s*[\w.]+\s*,\s*['\"]([a-z_]+)['\"]",
    r"\brecorded\(\s*['\"]([a-z_]+)['\"]",                 # app.py's loop helper: recorded("key", work)
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
    assert {"stall_marks", "run_housekeeping", "github_sync", "inbound_mail", "doc_sync", "assigned_tasks"} <= set(found)
    assert {key: where for key, where in found.items() if key not in SR.BY_KEY} == {}


def test_every_job_has_a_name_schedule_one_sentence_and_known_actions():
    for job in SR.JOBS:
        assert job.name and job.schedule and job.records, job.key
        assert job.what.endswith(".") and job.what[0].isupper(), job.key
        assert all(key in SR.ACTIONS for key in job.then), job.key
    assert len({job.key for job in SR.JOBS}) == len(SR.JOBS)
