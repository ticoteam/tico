"""The Maintenance run (docs/maintenance.md): the work the team's background jobs used to do on their own timers, now one
run that goes through its steps in a fixed order and logs each one.

When it runs: on its schedule (every hour by default, every 2, 3, 6 or 12 hours, or once a day), with day boundaries at
05:00 company time (updates.ZONE). Run now starts one at once. A run is claimed in one write transaction: while a run is
live, nothing else starts, and a schedule bucket that already has a run in it is not run again. A run whose heartbeat is
30 minutes old is abandoned: it is marked failed with its pending steps, and its occurrence key is renamed so its bucket
can run again.

A step is a module in backend/maintenance_steps/ exposing run(c, ctx) -> dict. The contract (frozen for the builders):
- c is a pooled connection with no transaction open. Read with it. Each write goes in its own store.transaction(), since
  what a step calls (dream_auto, the sweeps) opens its own.
- ctx: run_id, window_start, window_end, settings (the step's row as a dict), now, cap (the month's spend cap in USD),
  store and auth. The last two are added for steps that open their own transactions. Times are H.now() strings.
- It returns {status: ran|skipped|failed|pending, reason, checked, found, woke, cost_usd, details}. `ran` moves the step's
  cursor to window_end in the same transaction as its log row. `pending` leaves the cursor; complete_pending writes the
  later result against the same row.
- A step that raises is logged as failed (a Problem's detail, else the exception's name), and the run goes on.
"""
import json
import logging
import threading
from datetime import datetime, time, timedelta, timezone

from .maintenance_steps import clean_up, collect, dream
from .store import H, Problem
from .updates import START_HOUR, ZONE

LOG = logging.getLogger("tico.maintenance")
REGISTRY_KEY = "maintenance"      # registry_metadata: the run's schedule, pause and monthly cap
ORDER = ("collect", "clean_up", "check_health", "find_stuck", "learn", "suggest", "update", "dream")
NAMES = {"collect": "Collect", "clean_up": "Clean up", "check_health": "Check health", "find_stuck": "Find stuck",
         "learn": "Learn", "suggest": "Suggest", "update": "Update", "dream": "Dream"}
EVERY_RUN = ("collect", "clean_up", "check_health", "find_stuck")   # "How often" is every run, and fixed
OLD_TIMER = ("check_health", "find_stuck", "learn", "suggest", "update")   # still on their own timers until their track lands
LIVE = {"collect": collect.run, "clean_up": clean_up.run, "dream": dream.run}
SCHEDULES = {"hourly": 1, "2h": 2, "3h": 3, "6h": 6, "12h": 12, "daily": 0}
MAX_BOTS_STEPS = ("find_stuck", "dream")          # the steps that take "Most bots per run"
STATUSES = ("ran", "skipped", "failed", "pending")
OLD_TIMER_REASON = "on its old timer"
ABANDONED_MINUTES = 30          # a live run with no heartbeat this long is abandoned
WINDOW_BACK = timedelta(days=1)    # a step that has never moved its cursor looks back this far
DETAILS_MAX = 20000


def _stamp(at):
    """A datetime as an H.now() string: the format every timestamp column keeps."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _dump(value):
    text = json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= DETAILS_MAX else json.dumps({"truncated": True})


def _loads(text):
    try:
        value = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stored(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (REGISTRY_KEY,)).fetchone()
    return _loads(row[0]) if row else {}


def run_settings(c):
    """The run's own settings: the schedule (hourly by default), whether it is paused, and the monthly cap saved here
    (None when none is saved, so the environment's TICO_DECISIONS_MONTHLY_USD holds)."""
    stored = _stored(c)
    cap = stored.get("monthly_cap_usd")
    return {"schedule": stored.get("schedule") if stored.get("schedule") in SCHEDULES else "hourly",
            "paused": stored.get("paused") is True,
            "monthly_cap_usd": float(cap) if isinstance(cap, (int, float)) and not isinstance(cap, bool) else None}


def steps_of(c):
    return {row["key"]: dict(row) for row in c.execute("SELECT * FROM maintenance_steps")}


def day_start(at):
    """The most recent 05:00 company time at or before `at`: where a day's "once a day" work starts."""
    local = at.astimezone(ZONE)
    start = local.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    return start if local >= start else start - timedelta(days=1)


def due(row, at):
    """Whether a step is due at `at` by its "How often". The clock counts from its last attempt, not its last success:
    a step that failed does not retry every run."""
    if row["frequency"] == "run" or not row["last_started_at"]:
        return True
    last = H.parse_ts(row["last_started_at"])
    if row["frequency"] == "day":
        return last < day_start(at)
    if row["frequency"] == "week":
        return at - last >= timedelta(days=7)
    every = row["custom_every"] or 1
    return at - last >= (timedelta(hours=every) if row["custom_unit"] == "hours" else timedelta(days=every))


def bucket(schedule, at):
    """The company-time start of the schedule bucket `at` falls in: company midnight plus whole N-hour steps, or 05:00
    for a daily run."""
    local = at.astimezone(ZONE)
    if schedule == "daily":
        return datetime.combine((local - timedelta(hours=START_HOUR)).date(), time(START_HOUR), ZONE)
    every = SCHEDULES[schedule]
    return datetime.combine(local.date(), time(local.hour // every * every), ZONE)


def next_bucket(schedule, at):
    start = bucket(schedule, at)
    return start + (timedelta(days=1) if schedule == "daily" else timedelta(hours=SCHEDULES[schedule]))


def live_run(c, now):
    """The live run: not finished, and its heartbeat fresh. A stale one is for recover() to end."""
    cutoff = H.shift(now, minutes=-ABANDONED_MINUTES)
    return c.execute("SELECT id, started_at FROM maintenance_runs WHERE finished_at IS NULL AND heartbeat_at>=? "
                     "ORDER BY started_at DESC LIMIT 1", (cutoff,)).fetchone()


def recover(c, now):
    """Abandoned runs: live with no heartbeat for ABANDONED_AFTER. Marked failed with their pending steps, and the key
    renamed so the occurrence it claimed can run again."""
    cutoff = H.shift(now, minutes=-ABANDONED_MINUTES)
    for (run_id,) in c.execute("SELECT id FROM maintenance_runs WHERE finished_at IS NULL AND heartbeat_at<?",
                               (cutoff,)).fetchall():
        c.execute("UPDATE maintenance_run_steps SET status='failed', reason='abandoned', finished_at=? "
                  "WHERE maintenance_run_id=? AND status='pending'", (now, run_id))
        c.execute("UPDATE maintenance_runs SET finished_at=?, ok=0, result='abandoned', "
                  "occurrence_key=occurrence_key||':abandoned:'||id WHERE id=?", (now, run_id))


def _insert(c, key, trigger, step, now):
    run_id = H.new_id()
    c.execute("INSERT INTO maintenance_runs(id,occurrence_key,trigger,step,started_at,heartbeat_at,result) "
              "VALUES(?,?,?,?,?,?,'')", (run_id, key, trigger, step, now, now))
    return run_id


def tick(store, auth, at, failures=None):
    """From the scheduler's tick: start a run when the schedule says one is due. One per bucket, none while paused, none
    beside a live run. Returns the run id it started, else None. Errors go to `failures`; a tick never raises."""
    now = H.now()
    try:
        with store.read() as c:
            cfg = run_settings(c)
            start = _stamp(bucket(cfg["schedule"], at))
            # Most ticks stop here, without a write transaction.
            if cfg["paused"] or live_run(c, now) or c.execute(
                    "SELECT 1 FROM maintenance_runs WHERE started_at>=? AND result<>'abandoned' LIMIT 1",
                    (start,)).fetchone():
                return None
        with store.transaction() as c:
            recover(c, now)
            if live_run(c, now) or c.execute(
                    "SELECT 1 FROM maintenance_runs WHERE started_at>=? AND result<>'abandoned' LIMIT 1",
                    (start,)).fetchone():
                return None
            run_id = _insert(c, "schedule:" + start, "schedule", None, now)
    except Exception as exc:
        LOG.exception("maintenance tick failed")
        if failures is not None:
            failures.append({"maintenance": type(exc).__name__})
        return None
    _start(store, auth, run_id, None, wait=False)
    return run_id


def run_now(store, auth, actor, step=None, wait=False):
    """Run now (POST /api/v2/maintenance/run, `hub maintenance run`): one run, claimed like a scheduled one and due by the same rules. `step` runs only
    that step. A run already live is returned as it is, so Run now and the schedule give one run. Returns (run, started)."""
    if step is not None and step not in ORDER:
        raise Problem("invalid", f"No such Maintenance step: {step}", 422)
    now = H.now()
    with store.transaction() as c:
        recover(c, now)
        live = live_run(c, now)
        if live:
            run_id, started = live["id"], False
        else:
            run_id = _insert(c, "run_now:" + H.new_id(), "run_now", step, now)
            H.event(c, actor, "maintenance.run_now", run_id, {"step": step})
            started = True
    if started:
        _start(store, auth, run_id, step, wait)
    return get_run(store, run_id), started


def _start(store, auth, run_id, only, wait):
    if wait:
        _execute(store, auth, run_id, only)
        return
    threading.Thread(target=_execute, args=(store, auth, run_id, only), name="maintenance-run", daemon=True).start()


def _execute(store, auth, run_id, only=None):
    tally, error = {}, None
    try:
        _steps(store, auth, run_id, only, tally)
    except Exception as exc:
        LOG.exception("maintenance run %s stopped", run_id)
        error = type(exc).__name__
    _finish(store, run_id, tally, error)


def _steps(store, auth, run_id, only, tally):
    from .judge import budget_cap
    now = H.now()
    at = H.parse_ts(now)
    with store.read() as c:
        steps = steps_of(c)
        cap = budget_cap(c, store.settings)
    names = [key for key in ORDER if only in (None, key)]
    plan = {}
    for key in names:
        row = steps[key]
        if not row["enabled"]:
            plan[key] = ("off", "switched off")
        elif key in OLD_TIMER:
            plan[key] = ("skipped", OLD_TIMER_REASON)
        elif not due(row, at):
            plan[key] = ("skipped", "not due")
        else:
            plan[key] = ("run", None)
    # Each step reads from its own cursor. Collect reads from the oldest cursor among the steps about to run.
    windows = {key: steps[key]["cursor_at"] or H.shift(now, days=-WINDOW_BACK.days) for key in names if plan[key][0] == "run"}
    for key in names:
        status, reason = plan[key]
        if status != "run":
            _log(store, run_id, key, status, reason, now, tally)
            continue
        window_start = min(windows.values()) if key == "collect" else windows[key]
        _run_step(store, auth, run_id, key, steps[key], window_start, now, cap, tally)


def _log(store, run_id, key, status, reason, now, tally):
    """One step's log row for a step that did not run this time (off, skipped), written finished."""
    with store.transaction() as c:
        c.execute("INSERT INTO maintenance_run_steps(id,maintenance_run_id,step,status,reason,started_at,finished_at,details) "
                  "VALUES(?,?,?,?,?,?,?,'{}')", (H.new_id(), run_id, key, status, reason, now, now))
        c.execute("UPDATE maintenance_runs SET heartbeat_at=? WHERE id=?", (H.now(), run_id))
    tally[status] = tally.get(status, 0) + 1


def _run_step(store, auth, run_id, key, row, window_start, now, cap, tally):
    row_id = H.new_id()
    window = {"window_start": window_start, "window_end": now}
    with store.transaction() as c:
        c.execute("INSERT INTO maintenance_run_steps(id,maintenance_run_id,step,status,reason,started_at,details) "
                  "VALUES(?,?,?,'pending','',?,?)", (row_id, run_id, key, now, _dump(window)))
        c.execute("UPDATE maintenance_steps SET last_started_at=? WHERE key=?", (now, key))
        c.execute("UPDATE maintenance_runs SET heartbeat_at=? WHERE id=?", (H.now(), run_id))
    ctx = {"run_id": run_id, "window_start": window_start, "window_end": now, "settings": row, "now": now,
           "cap": cap, "store": store, "auth": auth}
    try:
        with store.read() as c:
            result = _normal(LIVE[key](c, ctx))
    except Exception as exc:
        LOG.exception("maintenance step %s failed", key)
        result = _failed(exc)
    with store.transaction() as c:
        _finish_row(c, row_id, result)
        if result["status"] == "ran":
            c.execute("UPDATE maintenance_steps SET cursor_at=?, last_ran_at=? WHERE key=?", (now, H.now(), key))
        c.execute("UPDATE maintenance_runs SET cost_usd=cost_usd+?, heartbeat_at=? WHERE id=?",
                  (result["cost_usd"], H.now(), run_id))
    tally[result["status"]] = tally.get(result["status"], 0) + 1


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _normal(result):
    """A step's result, checked. A step that returns nothing, or a status it does not have, is a failure, not a skip."""
    if not isinstance(result, dict):
        return _result("failed", "returned no result")
    if result.get("status") not in STATUSES:
        return _result("failed", f"returned status {result.get('status')!r}")
    cost = result.get("cost_usd")
    return {"status": result["status"], "reason": str(result.get("reason") or "")[:300],
            "checked": _count(result.get("checked")), "found": _count(result.get("found")),
            "woke": _count(result.get("woke")),
            "cost_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else 0.0,
            "details": result["details"] if isinstance(result.get("details"), dict) else {}}


def _result(status, reason):
    return {"status": status, "reason": reason, "checked": 0, "found": 0, "woke": 0, "cost_usd": 0.0, "details": {}}


def _failed(exc):
    detail = getattr(exc, "detail", None) if isinstance(exc, Problem) else None
    return _result("failed", str(detail or type(exc).__name__)[:300])


def _finish_row(c, row_id, result):
    prior = c.execute("SELECT details FROM maintenance_run_steps WHERE id=?", (row_id,)).fetchone()
    details = {**_loads(prior[0] if prior else None), **result["details"]}
    finished = None if result["status"] == "pending" else H.now()
    c.execute("UPDATE maintenance_run_steps SET status=?, reason=?, checked=?, found=?, woke=?, cost_usd=?, "
              "finished_at=?, details=? WHERE id=?",
              (result["status"], result["reason"], result["checked"], result["found"], result["woke"],
               result["cost_usd"], finished, _dump(details), row_id))


def complete_pending(store, row_id, result):
    """A pending step's later completion (the contract's `pending`), written against its own run-step row. A `ran` moves
    the step's cursor to the window end that row kept. Returns False when the row is not pending."""
    result = _normal(result)
    with store.transaction() as c:
        row = c.execute("SELECT step, details FROM maintenance_run_steps WHERE id=? AND status='pending'",
                        (row_id,)).fetchone()
        if row is None:
            return False
        _finish_row(c, row_id, result)
        if result["status"] == "ran":
            c.execute("UPDATE maintenance_steps SET cursor_at=?, last_ran_at=? WHERE key=?",
                      (_loads(row["details"]).get("window_end"), H.now(), row["step"]))
    return True


def _finish(store, run_id, tally, error):
    now = H.now()
    ok = error is None and not tally.get("failed")
    text = f"Stopped: {error}" if error else (", ".join(f"{n} {k}" for k, n in sorted(tally.items())) or "Nothing ran")
    try:
        with store.transaction() as c:
            c.execute("UPDATE maintenance_runs SET finished_at=?, ok=?, result=?, heartbeat_at=? "
                      "WHERE id=? AND finished_at IS NULL", (now, int(bool(ok)), text[:300], now, run_id))
    except Exception:
        LOG.exception("could not finish maintenance run %s; recovery will end it", run_id)


def save_settings(c, actor, schedule=None, paused=None, monthly_cap_usd=None):
    """The run's schedule, pause and monthly cap; only what is sent changes."""
    if schedule is not None and schedule not in SCHEDULES:
        raise Problem("invalid", "Run every hour, every 2, 3, 6 or 12 hours, or once a day", 422)
    before, stored = run_settings(c), _stored(c)
    if schedule is not None:
        stored["schedule"] = schedule
    if paused is not None:
        stored["paused"] = bool(paused)
    if monthly_cap_usd is not None:
        stored["monthly_cap_usd"] = round(float(monthly_cap_usd), 2)
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (REGISTRY_KEY, json.dumps(stored, sort_keys=True)))
    after = run_settings(c)
    if after != before:
        H.event(c, actor, "maintenance.updated", "", {"before": before, "after": after})


STEP_FIELDS = ("enabled", "frequency", "custom_every", "custom_unit", "max_bots")


def save_step(c, actor, change):
    """One step's switch, "How often", custom interval and "Most bots per run"; only what is sent changes."""
    key = change["key"]
    row = c.execute("SELECT * FROM maintenance_steps WHERE key=?", (key,)).fetchone()
    if row is None:
        raise Problem("not_found", "No such Maintenance step", 404)
    before = dict(row)
    after = {field: before[field] for field in STEP_FIELDS}
    for field in STEP_FIELDS:
        if change.get(field) is not None:
            after[field] = change[field]
    after["enabled"] = bool(after["enabled"])
    if key in EVERY_RUN and after["frequency"] != "run":
        raise Problem("invalid", f"{NAMES[key]} runs every run", 422)
    if key == "collect" and not after["enabled"]:
        raise Problem("invalid", "Collect is always on", 422)
    if after["frequency"] == "custom":
        if after["custom_every"] is None or after["custom_unit"] not in ("hours", "days"):
            raise Problem("invalid", "A custom schedule needs a number and hours or days", 422)
    else:
        after["custom_every"] = after["custom_unit"] = None
    if change.get("max_bots") is not None and key not in MAX_BOTS_STEPS:
        raise Problem("invalid", f"{NAMES[key]} takes no number of bots", 422)
    c.execute("UPDATE maintenance_steps SET enabled=?, frequency=?, custom_every=?, custom_unit=?, max_bots=?, "
              "updated_at=?, updated_by_id=? WHERE key=?",
              (int(after["enabled"]), after["frequency"], after["custom_every"], after["custom_unit"],
               after["max_bots"], H.now(), str(actor), key))
    changed = {field: after[field] for field in STEP_FIELDS if after[field] != before[field]}
    if changed:
        H.event(c, actor, "maintenance.step_updated", key, {"changed": changed})


def get_run(store, run_id):
    with store.read() as c:
        row = c.execute("SELECT * FROM maintenance_runs WHERE id=?", (run_id,)).fetchone()
        return _run_view(c, row) if row else None


def _run_view(c, row, details=True):
    """A run with its step rows. A list leaves each step's details out (they can be long); one run keeps them."""
    view = dict(row)
    view["steps"] = []
    columns = "step,status,reason,checked,found,woke,cost_usd,started_at,finished_at" + (",details" if details else "")
    for step in c.execute(f"SELECT {columns} FROM maintenance_run_steps WHERE maintenance_run_id=? "
                          "ORDER BY started_at, rowid", (row["id"],)).fetchall():
        item = dict(step)
        if details:
            item["details"] = _loads(item["details"])
        view["steps"].append(item)
    return view


def runs(c, limit=25, before=None):
    """One page of runs, newest first, each with its step rows. `next` is the cursor for the page after, or None."""
    limit = max(1, min(int(limit), 100))
    if before:
        rows = c.execute("SELECT * FROM maintenance_runs WHERE started_at<? ORDER BY started_at DESC LIMIT ?",
                         (before, limit)).fetchall()
    else:
        rows = c.execute("SELECT * FROM maintenance_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    out = [_run_view(c, row, details=False) for row in rows]
    return {"runs": out, "next": out[-1]["started_at"] if len(out) == limit else None}


def listing(c, settings, auth, who):
    """What GET /api/v2/maintenance returns: the run's settings, every step, the last and the live run, and the next run."""
    from .judge import budget_cap
    cfg, steps, now = run_settings(c), steps_of(c), H.parse_ts(H.now())
    rows = []
    for key in ORDER:
        step = steps[key]
        rows.append({"key": key, "name": NAMES[key], "enabled": bool(step["enabled"]), "frequency": step["frequency"],
                     "custom_every": step["custom_every"], "custom_unit": step["custom_unit"],
                     "max_bots": step["max_bots"], "fixed": key in EVERY_RUN, "old_timer": key in OLD_TIMER,
                     "last_started_at": step["last_started_at"], "last_ran_at": step["last_ran_at"],
                     "cursor_at": step["cursor_at"]})
    last = c.execute("SELECT id FROM maintenance_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    live = live_run(c, H.now())
    return {"settings": {**cfg, "monthly_cap_usd_in_effect": budget_cap(c, settings)},
            "steps": rows,
            "last_run": _run_view(c, c.execute("SELECT * FROM maintenance_runs WHERE id=?", (last[0],)).fetchone())
            if last else None,
            "live_run_id": live["id"] if live else None,
            "next_run_at": None if cfg["paused"] else _stamp(next_bucket(cfg["schedule"], now)),
            "can_change": can_change(auth, who)}


def can_change(auth, who):
    if getattr(who, "support", None) == "app":
        return False             # Tico support reads as an admin, but its app tier writes none of these
    try:
        may_change(auth, who)
        return True
    except Problem:
        return False


def may_change(auth, who):
    """An owner or an admin changes the schedule, the steps and Run now. Raises a 403 for anyone else."""
    auth.domain(who)
    if getattr(who, "support", None) == "app" or not auth.bot_admin(who):
        raise Problem("forbidden", "Only an owner or an admin changes Maintenance", 403)


def install(app, store, auth, mutate):
    from fastapi import Request

    from . import models as M

    @app.get("/api/v2/maintenance")
    def maintenance(request: Request):
        """The Maintenance run: its schedule, pause and monthly cap, each step with its switch, "How often" and cursor,
        the last run, the live run, the next run, and `can_change` for this viewer. Any signed-in person reads it."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, store.settings, auth, who)

    @app.patch("/api/v2/maintenance")
    def maintenance_save(request: Request, body: M.MaintenanceUpdate):
        """Change the run's schedule, pause or monthly cap, and each step's switch, "How often" or number of bots. Only
        what is sent changes. An owner or an admin."""
        who = request.state.identity
        may_change(auth, who)

        def work(c):
            save_settings(c, who.actor, body.schedule, body.paused, body.monthly_cap_usd)
            for change in body.steps or []:
                save_step(c, who.actor, change.model_dump())
        mutate(request, body, work)
        with store.read() as c:
            return listing(c, store.settings, auth, who)

    @app.get("/api/v2/maintenance/runs")
    def maintenance_runs(request: Request, limit: int = 25, before: str | None = None):
        """Maintenance runs, newest first, a page at a time, each with its step rows. Pass `next` back as `before`."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return runs(c, limit, before)

    @app.post("/api/v2/maintenance/run")
    def maintenance_run(request: Request, body: M.MaintenanceRunRequest):
        """Run now: one run, due by the same rules as a scheduled one. `step` runs only that step. A run already live is
        returned instead of a second. `started` is false in that case. An owner or an admin."""
        who = request.state.identity
        may_change(auth, who)
        run, started = run_now(store, auth, who.actor, body.step)
        return {"run": run, "started": started}
