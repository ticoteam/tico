"""System routines: the jobs Tico runs on its own timers (backend/scheduler.py), one registry and one run record per run,
for the read-only System section at the top of Settings > Routines (`GET /api/v2/system-routines`).

`JOBS` is every built-in job: its plain name, one line on what it does, its group, its schedule in words and in seconds,
whether it asks Tico Decide, and whether it is one of the six a person will be able to switch and reschedule (`control`;
the controls themselves are not here). A job's run is recorded by `recording` around its block in the scheduler: start,
finish, the result in words, or "Failed: <plain reason>", never an exception's text. A Decide job adds which route
answered (the Gateway, the company's own key, or the company's AI provider), the first failure when the Gateway did not
answer, and the run's cost. The Decide fallback itself is judge.py's; nothing here changes which route answers.

A job that runs every few seconds and finds nothing to do does not write a row each time: a quiet run with the same
result as the job's last row moves that row's times forward, at most once a minute. Each job keeps its last KEEP rows.
A month's spend is read from the `judge.spend` audit by question set (`label`), which the monthly cap already keeps, so
pruning runs never loses it."""

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .store import H

LOG = logging.getLogger("tico.system_routines")

SCHEMA = """
CREATE TABLE IF NOT EXISTS system_runs(
 id TEXT PRIMARY KEY, job TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, ok INTEGER NOT NULL DEFAULT 1,
 result TEXT NOT NULL DEFAULT '', route TEXT, fallback TEXT, cost_usd REAL, runs INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS system_runs_job ON system_runs(job, started);
"""

KEEP = 50                   # rows per job
QUIET_WRITE_SECONDS = 60    # a repeated quiet run moves its row at most this often

CHECKS, LEARNING, REMINDERS, HOUSEKEEPING = "checks", "learning", "reminders", "housekeeping"
GROUPS = [(CHECKS, "Checks that suggest"), (LEARNING, "Learning"), (REMINDERS, "Reminders and deadlines"),
          (HOUSEKEEPING, "Housekeeping")]

# Which route answered a Decide call, from the answering provider (judge.py's order: Gateway, own key, company AI).
ROUTE_WORDS = {"gateway": "Gateway", "own_key": "Company key", "team_llm": "Company AI"}
FELL_BACK_TO = {"own_key": "Ran on your own key", "team_llm": "Ran on company AI"}
# Why the first route did not answer, from clients/judge.py's error codes.
FALLBACK_WORDS = {"unavailable": "unavailable", "auth": "refused the key", "unconfigured": "has no key",
                  "http": "answered with an error", "shape": "answer could not be read", "not_found": "not found",
                  "spend_cap": "at its spend cap", "invalid": "refused the request"}
PROVIDER_WORDS = {"gateway": "Gateway", "own_key": "Your own key", "team_llm": "Company AI"}


@dataclass(frozen=True)
class Job:
    key: str
    name: str
    what: str
    group: str
    schedule: str               # in words
    every: int | None = None    # seconds between runs, for the next run; None: `next_at` or unknown
    ai: bool = False            # asks Tico Decide
    control: bool = False       # one of the six a person will switch and reschedule
    labels: tuple = ()          # Decide question sets whose spend is this job's
    records: bool = True        # False: each connection keeps its own last run


JOBS = [
    Job("stale_review", "Stale ticket review",
        "Finds open tasks that look finished or forgotten and suggests to each owner what to do.",
        CHECKS, "Hourly", 3600, ai=True, control=True, labels=("stale-review",)),
    Job("still_wanted", "Still wanted?",
        "Asks a task's person once whether a task idle for a week is still wanted.",
        CHECKS, "Daily", 86400, control=True),
    Job("kpi_asks", "KPI stale asks",
        "Asks a KPI's keeper for a reading when it goes out of date, and a goal's owner when a KPI goes off track.",
        CHECKS, "Hourly", 3600, control=True),
    Job("health_scan", "Health scan",
        "Checks Tico's health and wakes the Health Monitor only for a finding the last scan did not have.",
        CHECKS, "Hourly", 3600, control=True),
    Job("learnings", "Nightly learnings",
        "Reads yesterday's chats, tasks, Slack, mail and meetings and sends each bot what it should remember.",
        LEARNING, "Nightly at 03:00", ai=True, control=True, labels=("learnings-route",)),
    Job("due_reminders", "Due reminders", "Tells a bot when one of its tasks reaches its due time.",
        REMINDERS, "Every 10 seconds", 10),
    Job("ask_deadlines", "Ask go-ahead",
        "A bot's question past its deadline goes ahead on its default answer, or expires.",
        REMINDERS, "Hourly", 3600, control=True),
    Job("approval_expiry", "Approval expiry", "Takes approvals and asks nobody will act on out of Needs you.",
        REMINDERS, "Hourly", 3600),
    Job("still_needed", "Unanswered questions",
        "On weekday mornings, sends questions unanswered for two business days back to their bots.",
        REMINDERS, "Hourly", 3600),
    Job("routine_timer", "Bot routine timer", "Starts each bot routine below when it is due.",
        HOUSEKEEPING, "Every 10 seconds", 10),
    Job("stall_checks", "Stall checks",
        "Wakes the bot on a task nothing is moving, and drops queued work its bot can no longer read.",
        HOUSEKEEPING, "Every minute", 60),
    Job("waiting_checks", "Waiting checks",
        "Sends bot tasks waiting on nothing back to work, and turns a bot's plain ask to a person into a small task.",
        HOUSEKEEPING, "Hourly", 3600),
    Job("snooze_ends", "Snooze ends",
        "Brings snoozed tasks back when their date comes and starts deferred runs that are due.",
        HOUSEKEEPING, "Every 10 seconds", 10),
    Job("go_live_expiry", "Go-live expiry", "Ends a go-live waiting for a computer that never reports.",
        HOUSEKEEPING, "Every 10 minutes", 600),
    Job("goal_colours", "Goal colours", "Works out every goal's colour again as its data ages.",
        HOUSEKEEPING, "Hourly", 3600),
    Job("cleanup", "Cleanup", "Clears old events, mail, retry keys and recorder data.",
        HOUSEKEEPING, "Hourly", 3600),
    Job("gateway_check", "Gateway key check",
        "Asks the Tico AI Gateway whether this server's key still works and how much of the month's cap is used.",
        HOUSEKEEPING, "Hourly", 3600),
    Job("billing_check", "Billing check", "Reads HQ's nightly billing check for the owner's Health page.",
        HOUSEKEEPING, "Hourly", 3600),
    Job("updates", "Updates", "Asks people and bots for today's updates, one at a time.",
        HOUSEKEEPING, "Every 10 seconds", 10),
    Job("github_sync", "GitHub sync",
        "Catches up on pull requests, merges and releases whose notice from GitHub never arrived.",
        HOUSEKEEPING, "Every 10 seconds", 10),
    Job("repository_waits", "Repository and setup waits",
        "Finishes a bot waiting for its repository or a worktree, and tells a person about a bot stuck in setup.",
        HOUSEKEEPING, "Every 10 seconds", 10),
    Job("release_check", "Release and feed check", "Checks for a new Tico release and fetches subscribed updates.",
        HOUSEKEEPING, "Every 10 seconds; a new release is looked for every 6 hours", 10),
    Job("directory_sync", "Directory sync", "Brings people in from the company directory.",
        HOUSEKEEPING, "Hourly"),
    Job("meeting_sync", "Meeting notes sync", "Brings in meeting notes from each person's connected Granola.",
        HOUSEKEEPING, "Every 30 seconds", 30, records=False),
    Job("doc_sync", "Document sync", "Refreshes linked documents from their sources.",
        HOUSEKEEPING, "Every few seconds", 5, records=False),
]
BY_KEY = {job.key: job for job in JOBS}


# ----------------------------------------------------------------------------- the run record
@dataclass
class Run:
    """One run of a job, filled in by the job's block: `result` in words, and for a Decide job `ai(...)`."""
    job: str
    started: str = field(default_factory=H.now)
    result: str = ""
    error: str | None = None
    route: str | None = None
    fallback: str | None = None
    cost_usd: float | None = None
    quiet: bool = False         # nothing was done: may be folded into the job's last row

    def did(self, words, quiet=False):
        self.result, self.quiet = words, quiet
        return self

    def ai(self, routes=None, fallback=None, cost_usd=None):
        """The Decide calls of this run: `routes` counts calls per route (stale_review.route_of), `fallback` the
        first failure (`{"code", "from"}` or a bare code) when the first route did not answer, `cost_usd` the total."""
        routes = {k: v for k, v in (routes or {}).items() if v}
        if routes:
            self.route = max(routes, key=routes.get)
        self.fallback = fallback_words(fallback, self.route) if fallback and self.route != "gateway" else None
        self.cost_usd = round(float(cost_usd or 0), 6)
        return self


def fallback_words(fallback, route):
    """ "Ran on your own key: Gateway unavailable": what answered and why the first route did not."""
    if isinstance(fallback, dict):
        code, first = fallback.get("code") or "", fallback.get("from") or "gateway"
    else:
        code, first = str(fallback or ""), "gateway"
    lead = FELL_BACK_TO.get(route or "", "Fell back")
    return f"{lead}: {PROVIDER_WORDS.get(first, 'Gateway')} {FALLBACK_WORDS.get(code, 'unavailable')}"


# A failure's plain reason, from its kind; an exception's own text can carry a person's data and never shows.
FAILURE_WORDS = {"OperationalError": "the database was busy", "DatabaseError": "the database could not be read",
                 "IntegrityError": "a record conflicted", "TimeoutError": "it took too long", "Refused": "Tico refused it",
                 "Problem": "Tico refused it", "ConnectionError": "a service did not answer",
                 "OSError": "a file or connection failed", "JudgeError": "Decide did not answer"}


def failure_words(exc):
    name = type(exc).__name__
    return "Failed: " + FAILURE_WORDS.get(name, f"unexpected error ({name})")


def count_words(n, one, many=None):
    many = many or one + "s"
    return f"{n} {one if n == 1 else many}"


def words_of(found, one="change", none="Nothing to do"):
    """A job's return in words: a count of a list or a number, or the counts of a dict of lists/numbers."""
    if isinstance(found, dict):
        parts = [f"{len(v) if isinstance(v, (list, tuple, set, dict)) else int(v or 0)} {k.replace('_', ' ')}"
                 for k, v in found.items() if isinstance(v, (list, tuple, set, dict, int)) and not isinstance(v, bool)]
        parts = [p for p in parts if not p.startswith("0 ")]
        return ", ".join(parts) or none
    n = len(found) if isinstance(found, (list, tuple, set)) else int(found or 0) if isinstance(found, int) else 0
    return count_words(n, one) if n else none


def _repeat(c, run, finished):
    """(the job's last row, whether it was moved under a minute ago) when `run` is a quiet repeat of it, else None."""
    if not run.quiet or run.error is not None or run.route is not None:
        return None
    last = c.execute("SELECT id,ok,result,route,finished FROM system_runs WHERE job=? ORDER BY started DESC LIMIT 1",
                     (run.job,)).fetchone()
    if not last or not last["ok"] or last["route"] is not None or last["result"] != run.result:
        return None
    moved = H.parse_ts(last["finished"])
    return last, bool(moved and H.parse_ts(finished) - moved < timedelta(seconds=QUIET_WRITE_SECONDS))


def save(c, run, finished=None):
    """Write `run` in `c`: a new row, or a quiet repeat folded into the job's last row."""
    finished = finished or H.now()
    if repeat := _repeat(c, run, finished):
        last, recent = repeat
        if recent:
            return last["id"]
        c.execute("UPDATE system_runs SET started=?,finished=?,runs=runs+1 WHERE id=?", (run.started, finished, last["id"]))
        return last["id"]
    rid = H.new_id()
    c.execute("INSERT INTO system_runs(id,job,started,finished,ok,result,route,fallback,cost_usd) VALUES(?,?,?,?,?,?,?,?,?)",
              (rid, run.job, run.started, finished, 0 if run.error else 1, run.error or run.result or "Done",
               run.route, run.fallback, run.cost_usd))
    return rid


def keep(c, run):
    """`save` inside a caller's transaction, where a failure to record must not undo the caller's work."""
    try:
        save(c, run)
    except Exception as exc:
        LOG.warning("Could not record the %s run: %s", run.job, type(exc).__name__)


def write(store, run):
    """`save` in its own short transaction; a failure to record never fails the job. A quiet repeat moved under a
    minute ago takes no write lock at all."""
    try:
        with store.read() as c:
            repeat = _repeat(c, run, H.now())
        if repeat and repeat[1]:
            return
        with store.transaction() as c:
            save(c, run)
    except Exception as exc:
        LOG.warning("Could not record the %s run: %s", run.job, type(exc).__name__)


@contextmanager
def recording(store, key, failures=None, name=None, c=None):
    """Around one job's block: yields its `Run`; records it on the way out, in `c` when given (a block inside the
    scheduler's own transaction), else in its own transaction. An exception is the run's failure and, as the
    scheduler always did, one `{name: kind}` entry in `failures`; it does not propagate when `failures` is given."""
    run = Run(key)
    try:
        yield run
    except Exception as exc:
        run.error = failure_words(exc)
        if failures is None:
            raise
        failures.append({name or key: type(exc).__name__})
    finally:
        if c is not None:
            keep(c, run)
        else:
            write(store, run)


def prune(c):
    """Each job keeps its last KEEP rows."""
    for (job,) in c.execute("SELECT DISTINCT job FROM system_runs").fetchall():
        c.execute("DELETE FROM system_runs WHERE job=? AND id NOT IN "
                  "(SELECT id FROM system_runs WHERE job=? ORDER BY started DESC LIMIT ?)", (job, job, KEEP))


# ----------------------------------------------------------------------------- the list
def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def next_at(job, last, now, settings=None, c=None):
    """When the job runs next, from its schedule and its last run: `every` after the last start (never in the past),
    the nightly learning run's next 03:00, the directory's own interval. None when it cannot be told."""
    if job.key == "learnings":
        from .learnings import HOUR, ZONE
        local = now.astimezone(ZONE)
        at = local.replace(hour=HOUR, minute=0, second=0, microsecond=0)
        return at if at > local and (not last or H.parse_ts(last["started"]).astimezone(ZONE).date() < local.date()) \
            else at + timedelta(days=1)
    every = job.every
    if job.key == "directory_sync" and c is not None:
        every = (directory_minutes(c) or 0) * 60
    if not every:
        return None
    if not last:
        return None
    started = H.parse_ts(last["started"])
    return max(started + timedelta(seconds=every), now)


def directory_minutes(c):
    """The directory sync's interval in minutes, or None while no directory is connected (or its sync is off)."""
    from .directory import Sources, load
    cfg = load(c)
    if cfg["source"] not in Sources.FETCH or not cfg["interval_minutes"] or not cfg["confirmed"]:
        return None
    return int(cfg["interval_minutes"])


def spent_this_month(c, labels, at=None):
    """USD this month's `judge.spend` rows of these question sets cost (label `<set>@<version>`)."""
    if not labels:
        return None
    at = at or datetime.now(timezone.utc)
    month = at.astimezone(timezone.utc).strftime("%Y-%m-01T00:00:00")
    where = " OR ".join("json_extract(detail_json,'$.label') LIKE ?" for _ in labels)
    total = c.execute("SELECT coalesce(sum(json_extract(detail_json,'$.cost_usd')),0) FROM events "
                      f"WHERE action='judge.spend' AND ts>=? AND ({where})",
                      (month, *[f"{label}@%" for label in labels])).fetchone()[0]
    return round(float(total or 0), 4)


def last_runs(c):
    """The newest row per job."""
    rows = c.execute("SELECT r.* FROM system_runs r JOIN (SELECT job, max(started) AS started FROM system_runs "
                     "GROUP BY job) m ON m.job=r.job AND m.started=r.started").fetchall()
    return {r["job"]: dict(r) for r in rows}


def last_ai_runs(c):
    """The newest row with a route per Decide job: a run that asked nothing says nothing of the route."""
    keys = [job.key for job in JOBS if job.ai]
    out = {}
    for key in keys:
        row = c.execute("SELECT * FROM system_runs WHERE job=? AND route IS NOT NULL ORDER BY started DESC LIMIT 1",
                        (key,)).fetchone()
        if row:
            out[key] = dict(row)
    return out


def listing(c, settings, now=None):
    """Every job by group, with its last run, next run and, for a Decide job, its route and this month's spend."""
    now = now or datetime.now(timezone.utc)
    last, ai_last = last_runs(c), last_ai_runs(c)
    groups = []
    for gkey, gname in GROUPS:
        rows = []
        for job in (j for j in JOBS if j.group == gkey):
            row = last.get(job.key)
            schedule = job.schedule
            if job.key == "directory_sync":
                minutes = directory_minutes(c)
                schedule = ("Off: no directory connected" if minutes is None else "Hourly" if minutes == 60
                            else f"Every {minutes} minutes")
            following = next_at(job, row, now, settings, c) if job.records else None
            item = {"key": job.key, "name": job.name, "what": job.what, "schedule": schedule, "ai": job.ai,
                    "controllable": job.control, "needed": job.group == HOUSEKEEPING, "records": job.records,
                    "last": None, "next": _stamp(following) if following else None}
            if row:
                item["last"] = {"started": row["started"], "finished": row["finished"], "ok": bool(row["ok"]),
                                "result": row["result"], "runs": row["runs"]}
            if job.ai:
                answered = ai_last.get(job.key)
                item["route"] = ROUTE_WORDS.get(answered["route"], "Other") if answered else None
                item["fallback"] = answered["fallback"] if answered else None
                item["spent_this_month_usd"] = spent_this_month(c, job.labels, now)
            rows.append(item)
        groups.append({"key": gkey, "name": gname, "jobs": rows})
    return {"groups": groups, "now": _stamp(now)}


def install(app, store, auth):
    from fastapi import Request

    @app.get("/api/v2/system-routines")
    def system_routines(request: Request):
        """The jobs Tico runs on its own timers, by group: what each does, its schedule, its last and next run, and for
        a Decide job which route answered and this month's spend. Read-only; everyone who can open Settings."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, store.settings)

