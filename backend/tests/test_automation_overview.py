"""The Automations overview (backend/automation_overview.py): runs and counts across every automation the caller may see.
Seeing a run follows seeing its automation, so these are a privacy boundary."""
import json

from backend.store import H
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_automation_api import as_bot


def _make(api, name, owner, kind="event"):
    trigger = {"type": "event", "name": "x.y"} if kind == "event" else {"type": "schedule", "cron": "0 7 * * *"}
    aid = "auto_" + H.new_id()
    flow = {"name": name, "owner": owner, "enabled": True, "builtin": False, "trigger": trigger, "steps": []}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,flow,version,created_at,created_by_id,"
                  "updated_at) VALUES(?,?,?,?,1,?,1,?,?,?)",
                  (aid, name, owner, trigger["type"], json.dumps(flow), H.now(), "human:ana", H.now()))
    return aid


def _run(api, aid, status, minutes_ago, payload=None, steps=None, cost=0.0):
    rid = "run_" + H.new_id()
    started = H.shift(H.now(), minutes=-minutes_ago)
    state = {"status": status, "steps": steps or [], "payload": payload or {}}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO automation_runs(id,automation_id,version,trigger_ref,started_at,finished_at,status,cost,"
                  "saved_state) VALUES(?,?,1,'',?,?,?,?,?)", (rid, aid, started, started, status, cost, json.dumps(state)))
    return rid


def test_runs_show_only_what_the_caller_may_see(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    ops = _make(api, "Ops", "bot:ops")
    cara = _make(api, "Cara's", "person:cara")
    _run(api, ops, "ok", 1, {"message": {"from": "x@acme.example", "subject": "Ops mail"}})
    _run(api, cara, "failed", 2, {"message": {"from": "y@acme.example", "subject": "Private"}})
    assert {r["automation_id"] for r in get(api, "automations/runs?range=1h")["runs"]} == {ops, cara}
    ben = get(api, "automations/runs?range=1h", "ben-test")["runs"]
    assert [r["automation_id"] for r in ben] == [ops] and "Private" not in json.dumps(ben)
    assert [r["automation_id"] for r in get(api, "automations/runs?range=1h", "cara-test")["runs"]] == [cara]
    # A peer bot sees nothing, and asking for a hidden automation by id or owner does not reveal it.
    assert get(api, "automations/runs?range=1h", as_bot(api, "finance"))["runs"] == []
    assert get(api, f"automations/runs?range=1h&ids={cara}", "ben-test")["runs"] == []
    assert get(api, "automations/runs?range=1h&owner=person:cara", "ben-test")["runs"] == []
    stats = get(api, "automations/stats?range=1h", "ben-test")
    assert [s["id"] for s in stats["automations"]] == [ops] and stats["totals"]["runs"] == 1


def test_runs_page_by_cursor_within_the_range(api):
    aid = _make(api, "Busy", "person:ana")
    inside = [_run(api, aid, "ok", m) for m in range(1, 8)]
    _run(api, aid, "ok", 90)                                    # outside 1h
    seen, before = [], ""
    for _ in range(5):
        page = get(api, f"automations/runs?range=1h&limit=3&before={before}")
        seen += [r["id"] for r in page["runs"]]
        before = page["next"]
        if not before:
            break
    assert seen == inside                                       # newest first, none twice, none outside
    assert len(get(api, "automations/runs?range=24h")["runs"]) == 8
    get(api, "automations/runs?range=30d", expected=422)
    get(api, "automations/runs?range=1h&status=nope", expected=422)


def test_runs_say_what_started_them_and_how_they_ended(api):
    aid = _make(api, "Mail", "person:ana")
    _run(api, aid, "failed", 1, {"message": {"from": "dana@acme.example", "from_name": "Dana", "subject": "Invoice"}},
         [{"id": "s1", "type": "llm", "status": "failed", "outputs": {"error": "timed out"}}])
    _run(api, aid, "ok", 2, {"manual": True},
         [{"id": "s1", "type": "action", "status": "ok", "outputs": {"do": "archive"}}])
    failed, done = get(api, "automations/runs?range=1h")["runs"]
    assert failed["started_by"] == "From Dana · Invoice" and failed["outcome"] == "LLM failed: timed out"
    assert done["started_by"] == "Run by hand" and done["outcome"] == "Archived" and done["step_count"] == 1
    assert "steps" not in failed
    assert [r["status"] for r in get(api, "automations/runs?range=1h&status=failed")["runs"]] == ["failed"]


def test_stats_count_runs_and_failures_per_bucket(api):
    a = _make(api, "A", "person:ana")
    b = _make(api, "B", "person:ana", kind="schedule")
    _run(api, a, "ok", 1, cost=0.5)
    _run(api, a, "failed", 1)
    _run(api, a, "ok", 50)
    _run(api, b, "failed", 30)
    _run(api, b, "ok", 120)                                     # outside 1h
    stats = get(api, "automations/stats?range=1h")
    assert stats["bucket_seconds"] == 300 and len(stats["totals"]["buckets"]) == 12
    per = {s["id"]: s for s in stats["automations"]}
    assert (per[a]["runs"], per[a]["failed"], per[a]["cost"]) == (3, 1, 0.5)
    assert (per[b]["runs"], per[b]["failed"]) == (1, 1)
    assert per[a]["buckets"][-1] == {"ok": 1, "failed": 1} and per[a]["buckets"][1]["ok"] == 1
    assert stats["totals"]["runs"] == 4 and stats["totals"]["failed"] == 2
    assert sum(x["failed"] for x in stats["totals"]["buckets"]) == 2
    assert get(api, "automations/runs?range=1h&trigger=schedule")["runs"][0]["automation_id"] == b


def test_system_job_runs_show_under_their_built_in(api):
    flow = {"name": "Still wanted", "owner": "person:ana", "builtin": True, "readonly": True, "enabled": True,
            "trigger": {"type": "event", "name": "tico.builtin.job.still_wanted"}, "steps": [],
            "controls": {"on": True}, "source": {"kind": "system_job", "id": "job:still_wanted"}}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,builtin,flow,version,created_at,"
                  "created_by_id,updated_at) VALUES('auto_sys','Still wanted','person:ana','event',1,1,?,1,?,?,?)",
                  (json.dumps(flow), H.now(), "human:ana", H.now()))
        for i, ok in enumerate((1, 0)):
            at = H.shift(H.now(), minutes=-(i + 1))
            c.execute("INSERT INTO system_runs(id,job,started_at,finished_at,ok,result,runs) VALUES(?,?,?,?,?,?,1)",
                      (f"sr{i}", "still_wanted", at, at, ok, "Asked 2" if ok else "Mail down"))
    runs = get(api, "automations/runs?range=1h&trigger=system")["runs"]
    assert [(r["id"], r["status"], r["outcome"]) for r in runs] == [("sys_sr0", "ok", "Asked 2"),
                                                                    ("sys_sr1", "failed", "Mail down")]
    stats = {s["id"]: s for s in get(api, "automations/stats?range=1h")["automations"]}
    assert (stats["auto_sys"]["runs"], stats["auto_sys"]["failed"]) == (2, 1)
    own = get(api, "automations/auto_sys/runs?limit=1")
    assert own["runs"][0]["id"] == "sys_sr0" and own["next"] == "sys_sr0"
    assert get(api, "automations/auto_sys/runs?limit=1&before=sys_sr0")["runs"][0]["id"] == "sys_sr1"
    listed = {a["id"]: a for a in get(api, "automations?limit=500")["automations"]}
    assert listed["auto_sys"]["kind"] == "system" and listed["auto_sys"]["last_run"]["id"] == "sys_sr0"
    # Only the top of the org sees the system job's runs: it is ana's, and nobody else is above her.
    assert get(api, "automations/runs?range=1h", "ben-test")["runs"] == []


def test_folded_system_runs_count_each_repeat_where_it_happened(api):
    """A quiet repeat folds into the job's last row (system_routines.save): `started_at` moves to the latest, `runs` grows,
    `first_started_at` stays. Counts spread `runs` over that time, so a job that runs hourly is not one run a day."""
    import sqlite3

    from backend import system_routines as SR
    flow = {"name": "Health", "owner": "person:ana", "builtin": True, "readonly": True, "enabled": True,
            "trigger": {"type": "event", "name": "tico.builtin.job.health_monitor"}, "steps": [],
            "source": {"kind": "system_job", "id": "job:health_monitor"}}
    now = H.now()
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO automations(id,name,owner_id,trigger_type,enabled,builtin,flow,version,created_at,"
                  "created_by_id,updated_at) VALUES('auto_hm','Health','person:ana','event',1,1,?,1,?,?,?)",
                  (json.dumps(flow), now, "human:ana", now))
        # Saved through the job registry: a fold keeps first_started and moves started.
        first = SR.save(c, SR.Run("health_monitor", started=H.shift(now, hours=-30)).did("All well", quiet=True),
                        finished=H.shift(now, hours=-30))
        assert SR.save(c, SR.Run("health_monitor", started=H.shift(now, hours=-29)).did("All well", quiet=True),
                       finished=H.shift(now, hours=-29)) == first
        row = c.execute("SELECT first_started_at, started_at, runs FROM system_runs WHERE id=?", (first,)).fetchone()
        assert (row["first_started_at"], row["runs"]) == (H.shift(now, hours=-30), 2) and row["started_at"] > row["first_started_at"]
        # One a hour for a day: 24 repeats from 23h ago to 30 s ago.
        last = H.shift(now, seconds=-30)
        c.execute("INSERT INTO system_runs(id,job,started_at,first_started_at,finished_at,ok,result,runs) "
                  "VALUES('hm1','health_monitor',?,?,?,1,'All well',24)", (last, H.shift(last, hours=-23), last))
        # Three failures folded over 20 minutes, and an older row without first_started (counts once).
        c.execute("INSERT INTO system_runs(id,job,started_at,first_started_at,finished_at,ok,result,runs) "
                  "VALUES('hm2','health_monitor',?,?,?,0,'Mail down',3)",
                  (H.shift(now, minutes=-2), H.shift(now, minutes=-22), H.shift(now, minutes=-2)))
        c.execute("INSERT INTO system_runs(id,job,started_at,finished_at,ok,result,runs) "
                  "VALUES('hm3','health_monitor',?,?,1,'All well',5)", (H.shift(now, hours=-3), H.shift(now, hours=-3)))
    hour = get(api, "automations/stats?range=1h")["automations"]
    hm = next(s for s in hour if s["id"] == "auto_hm")
    assert (hm["runs"], hm["failed"]) == (4, 3)                 # one hourly repeat, three failures
    day = next(s for s in get(api, "automations/stats?range=24h")["automations"] if s["id"] == "auto_hm")
    assert (day["runs"], day["failed"]) == (28, 3)              # 24 + 3 + 1; the 30h-old row is outside
    assert max(b["ok"] for b in day["buckets"]) <= 3, "spread over the day, not piled in the last hour"
    # The Runs tab keeps one row per folded record, with its count.
    runs = {r["id"]: r for r in get(api, "automations/runs?range=1h&trigger=system")["runs"]}
    assert runs["sys_hm1"]["count"] == 24 and runs["sys_hm2"]["count"] == 3
    # A database made before first_started gains it.
    old = sqlite3.connect(":memory:")
    old.execute("CREATE TABLE system_runs(id TEXT PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL, runs INTEGER)")
    SR.ensure(old)
    assert "first_started_at" in {r[1] for r in old.execute("PRAGMA table_info(system_runs)")}
