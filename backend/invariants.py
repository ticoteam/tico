"""Production invariants: a few plain-SQL checks over the hub for the failures Tico has actually had (lapsed leases,
tasks nothing is running, questions to people who cannot be reached, refusal loops, a provider sign-in that fails,
webhooks and automations that stopped, a hook relay nobody pulls).

The scheduler runs `sweep` every 15 minutes (backend/scheduler.py). Plain code only: no model runs. A check that fires
shows on Health with its count and example ids (`health_checks`) and raises ONE task for its owner, through the same
path as a Needs-you task for a stopped bot (`H.task_create`). While the check keeps firing no second task is raised,
even if the first was closed by hand. When it passes again its task is closed. Nothing is fixed automatically.

Each check reads indexed rows only (the partial indexes in SCHEMA; EXPLAIN QUERY PLAN checked in backend/tests/
test_invariants.py). A rehearsal or demo copy checks nothing: its data is a copy of a live install's and its runners
do not run.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from . import flight, hook_relay, run_backoff
from .store import H

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS invariant_state(
 check_id TEXT PRIMARY KEY, firing INTEGER NOT NULL DEFAULT 0, count INTEGER NOT NULL DEFAULT 0,
 examples TEXT NOT NULL DEFAULT '[]', checked_at TEXT, since TEXT, task_id TEXT);
CREATE INDEX IF NOT EXISTS invariants_lease ON attempts(lease_until) WHERE state IN ('leased','running');
CREATE INDEX IF NOT EXISTS invariants_doing ON tasks(updated_at) WHERE status='doing';
CREATE INDEX IF NOT EXISTS invariants_open_approvals ON approvals(created_at) WHERE decision IS NULL;
"""

LIMIT = 500                  # rows read per check; a count at this many is "500 or more"
EXAMPLES = 5                 # example ids shown and written into the alert
LEASE_MINUTES = 10           # a lease this far past its end, still leased or running, is not being renewed or expired
TASK_QUIET_MINUTES = 60      # a bot's task still "doing" this long with no job queued, leased, running or waiting
REFUSALS_PER_DAY = 20        # the same rule refusing the same bot more often than this in 24 hours
SENDING_MINUTES = 10         # a webhook send still `sending` this long: its worker is gone and recovery did not run
OVERDUE_MINUTES = 30         # a scheduled automation this long past its time has not been run
RELAY_SILENT_MINUTES = 30    # a relay address no pull has reached this long
# Jobs still owed work: a task whose job is in one of these is in progress, not stuck.
LIVE_JOBS = ("queued", "leased", "running", "input", "uncertain")
UNREACHABLE_BOT_STATES = ("paused", "archived", "quarantined")
LONG_HOLDS_PER_HOUR = 30    # write-lock holds of flight.LONG_HOLD_MS (5 s) or more in an hour; a week on Tico Team
                            # averaged about 2 an hour, with bursts far above that
RUNNER_ERROR_PERCENT = 2    # 5xx answers as a share of a runner route's requests in the last hour
RUNNER_MIN_REQUESTS = 200   # fewer requests than this in the hour is too few for a rate
RUNNER_ROUTES = ("POST /api/v2/jobs/claim", "POST /api/v2/attempts/{aid}/events",
                 "POST /api/v2/attempts/{aid}/renew", "POST /api/v2/files/uploads")
DROPPED_MIN = 50            # samples the flight recorder dropped in the hour: fewer is noise
DROPPED_PERCENT = 1         # of the samples it took in the hour (requests plus dropped)


@dataclass(frozen=True)
class Check:
    id: str
    label: str           # the name on Health and in the alert's title
    says: str            # one plain line: what is wrong
    look: str            # where to look, in the alert
    owner: str           # "botops": BotOps (if active, else the owner); "owner": the owner
    read: Callable       # (c, ctx) -> list of ids (strings), at most LIMIT


@dataclass(frozen=True)
class Ctx:
    now: str             # ISO UTC, the same format the hub writes
    at: object           # the datetime the sweep runs at


def _ago(ctx, **delta):
    return H.shift(ctx.now, **{k: -v for k, v in delta.items()})


def lease_overdue(c, ctx):
    rows = c.execute("SELECT id FROM attempts WHERE state IN ('leased','running') AND lease_until<? "
                     "ORDER BY lease_until LIMIT ?", (_ago(ctx, minutes=LEASE_MINUTES), LIMIT)).fetchall()
    return [row["id"] for row in rows]


def task_stuck(c, ctx):
    """A bot's task in progress (`doing`) with no job for it still owed work, and nothing changed on it for the hour.
    The live-job test is in the query, ahead of LIMIT, so busy old tasks cannot hide a stuck newer one."""
    rows = c.execute(f"SELECT t.id FROM tasks t WHERE t.status='doing' AND t.owner LIKE 'bot:%' AND t.updated_at<? "
                     f"AND NOT EXISTS (SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                     f"JOIN conversations cv ON cv.id=m.conversation_id "
                     f"WHERE j.bot=substr(t.owner, 5) AND j.state IN ({','.join('?' * len(LIVE_JOBS))}) "
                     f"AND {H.MESSAGE_TASK_SQL}=t.id) ORDER BY t.updated_at LIMIT ?",
                     (_ago(ctx, minutes=TASK_QUIET_MINUTES), *LIVE_JOBS, LIMIT)).fetchall()
    return [row["id"] for row in rows]


def _unreachable(actor, people, bots):
    """Whether a person or bot cannot take an answer now: a person not on the roster (or left), a bot that is not
    active. Anything else, including a bot still being set up, is reachable."""
    if actor.startswith("human:"):
        person = people.get(actor[len("human:"):])
        return person is None or bool(person.get("hidden"))
    if actor.startswith("bot:"):
        state = bots.get(actor[len("bot:"):])
        return state is None or state in UNREACHABLE_BOT_STATES
    return False


def owner_unreachable(c, ctx):
    """Questions still open, and approvals still undecided, whose person or bot cannot be reached."""
    from .views import roster
    people = {p["id"]: p for p in roster(c).get("people") or []}
    bots = {row["slug"]: row["state"] for row in c.execute("SELECT slug, state FROM bots")}
    found = []
    # Every open ask's addressee and every undecided approval is read: the reachability test is in Python, so a
    # LIMIT before it could leave an unreachable owner behind reachable ones.
    open_ask = f"m.kind='ask' AND m.deleted_at IS NULL AND {H.OPEN_ASK_SQL}"
    targets = [row["to_actor"] for row in c.execute(f"SELECT DISTINCT m.to_actor FROM messages m WHERE {open_ask}")]
    for actor in targets:
        if _unreachable(actor, people, bots):
            found += [row["id"] for row in c.execute(
                f"SELECT m.id FROM messages m WHERE {open_ask} AND m.to_actor=? ORDER BY m.created_at LIMIT ?",
                (actor, LIMIT))]
    for row in c.execute("SELECT a.id, COALESCE(a.decision_owner, m.to_actor) AS owner FROM approvals a "
                         "LEFT JOIN messages m ON m.id=a.message_id WHERE a.decision IS NULL ORDER BY a.created_at").fetchall():
        if row["owner"] and _unreachable(row["owner"], people, bots):
            found.append(row["id"])
    return found[:LIMIT]


def refused_again(c, ctx):
    """`actor rule` for each bot a rule refused more than REFUSALS_PER_DAY times in the last 24 hours. One range read
    per bot on refusals_actor_ts (actor, ts), so the cost follows the bots, not the refusal log's size."""
    since = _ago(ctx, hours=24)
    found = []
    for (slug,) in c.execute("SELECT slug FROM bots ORDER BY slug LIMIT ?", (LIMIT,)).fetchall():
        for row in c.execute("SELECT rule, count(*) AS n FROM refusals WHERE actor=? AND ts>=? "
                             "GROUP BY rule HAVING n>?", (f"bot:{slug}", since, REFUSALS_PER_DAY)).fetchall():
            found.append((row["n"], f"bot:{slug} {row['rule']} ({row['n']}x)"))
    return [text for _, text in sorted(found, reverse=True)][:LIMIT]


def sign_in_held(c, ctx):
    """Bots held because their runs fail to sign in to the provider (a key or a login that stopped working)."""
    rows = c.execute("SELECT bot FROM run_backoff WHERE held_at IS NOT NULL AND error_class=? ORDER BY held_at LIMIT ?",
                     (run_backoff.SIGN_IN, LIMIT)).fetchall()
    return [row["bot"] for row in rows]


def webhook_stuck(c, ctx):
    rows = c.execute("SELECT id FROM webhook_sends WHERE state='sending' AND claimed_at<? ORDER BY claimed_at LIMIT ?",
                     (_ago(ctx, minutes=SENDING_MINUTES), LIMIT)).fetchall()
    return [row["id"] for row in rows]


def automation_overdue(c, ctx):
    rows = c.execute("SELECT id FROM automations WHERE deleted_at IS NULL AND enabled=1 AND trigger_type='schedule' "
                     "AND next_due<? ORDER BY next_due LIMIT ?", (_ago(ctx, minutes=OVERDUE_MINUTES), LIMIT)).fetchall()
    return [row["id"] for row in rows]


def relay_silent(c, ctx):
    """Relays exist and no pull has reached HQ for RELAY_SILENT_MINUTES (since the later of the last pull and the
    oldest relay's creation). Rehearsal and demo never pull, so they are not checked at all (see `sweep`)."""
    relays = hook_relay.live(c)
    if not relays:
        return []
    created = [t.replace(tzinfo=t.tzinfo or timezone.utc).timestamp()
               for t in (H.parse_ts(row["created_at"]) for row in relays) if t]
    since = max(hook_relay.state(c)["last_pull_at"] or 0, min(created, default=0))
    if ctx.at.timestamp() - since < RELAY_SILENT_MINUTES * 60:
        return []
    return [row["relay_id"] for row in relays]


def _hour_ago(ctx):
    return int(ctx.at.timestamp()) - 3600


def long_holds(c, ctx):
    """Write-lock holds of LONG_HOLD_MS or more in the last hour (flight_events, kind long_hold), when there are more
    than LONG_HOLDS_PER_HOUR: the routes and code sites holding the one write lock, most holds first."""
    since = _hour_ago(ctx)
    total = c.execute("SELECT count(*) FROM flight_events WHERE kind='long_hold' AND ts>=?", (since,)).fetchone()[0]
    if total <= LONG_HOLDS_PER_HOUR:
        return []
    rows = c.execute("SELECT json_extract(detail,'$.route') AS route, json_extract(detail,'$.site') AS site, "
                     "count(*) AS n, max(json_extract(detail,'$.hold_s')) AS worst FROM flight_events "
                     "WHERE kind='long_hold' AND ts>=? GROUP BY route, site ORDER BY n DESC LIMIT ?",
                     (since, LIMIT)).fetchall()
    return [f"{row['route'] or 'a background job'} at {row['site'] or 'an unknown site'}: {row['n']} holds, "
            f"longest {row['worst']:g} s" for row in rows]


def runner_errors(c, ctx):
    """Runner routes whose 5xx answers in the last hour were more than RUNNER_ERROR_PERCENT of their requests, from at
    least RUNNER_MIN_REQUESTS requests (flight_requests: one row per minute, route and caller)."""
    marks = ",".join("?" * len(RUNNER_ROUTES))
    rows = c.execute(f"SELECT route, sum(n) AS n, sum(errors) AS errors FROM flight_requests "
                     f"WHERE span=60 AND ts>=? AND route IN ({marks}) GROUP BY route",
                     (_hour_ago(ctx), *RUNNER_ROUTES)).fetchall()
    return [f"{row['route']}: {row['errors']} of {row['n']} answered with a 5xx"
            for row in rows
            if row["n"] >= RUNNER_MIN_REQUESTS and row["errors"] * 100 > RUNNER_ERROR_PERCENT * row["n"]]


def recorder_drops(c, ctx):
    """Samples the flight recorder dropped in the last hour (flight_process: one row per minute). A drop is the
    measurement losing data, most on the bad days it exists to show."""
    since = _hour_ago(ctx)
    totals = c.execute("SELECT coalesce(sum(dropped),0) AS dropped, coalesce(sum(requests),0) AS requests "
                       "FROM flight_process WHERE ts>=?", (since,)).fetchone()
    dropped, taken = totals["dropped"], totals["requests"] + totals["dropped"]
    if dropped < DROPPED_MIN or dropped * 100 < DROPPED_PERCENT * taken:
        return []
    rows = c.execute("SELECT ts, dropped FROM flight_process WHERE ts>=? AND dropped>0 ORDER BY dropped DESC LIMIT ?",
                     (since, LIMIT)).fetchall()
    return [f"{datetime.fromtimestamp(row['ts'], timezone.utc):%H:%M} UTC: {row['dropped']} dropped" for row in rows]


CHECKS = (
    Check("lease_overdue", "Runs past their lease",
          "Runs whose lease ended over 10 minutes ago, still leased or running, with nothing renewed or expired.",
          "The runner that holds each run, and whether the server's lease sweep is running (Health > Runs).",
          "botops", lease_overdue),
    Check("task_stuck", "Tasks in progress with no run",
          "Bot tasks marked in progress with no run queued or live for them, and nothing changed for an hour.",
          "The task's owner bot: its last run, and whether it should be woken or the task moved on.",
          "botops", task_stuck),
    Check("owner_unreachable", "Waiting on someone who cannot be reached",
          "Questions and approvals still open to a person who left or a bot that is paused, archived or quarantined.",
          "Who the question is to; reassign it, answer it, or bring that bot back.",
          "owner", owner_unreachable),
    Check("refused_again", "Same refusal over and over",
          f"A rule refused the same bot more than {REFUSALS_PER_DAY} times in a day.",
          "The bot's runs and the rule named; the bot is likely looping on a move it cannot make.",
          "botops", refused_again),
    Check("sign_in_held", "Provider sign-in failing",
          "A bot is held because its runs keep failing to sign in to its model provider.",
          "The bot's model key or login (Settings > Computers); a fixed key releases the bot on its next try.",
          "owner", sign_in_held),
    Check("webhook_stuck", "Webhook sends stuck",
          f"Outgoing webhook sends still marked as sending {SENDING_MINUTES} minutes after their worker claimed them.",
          "The webhook's worker and the server log; the sends return to ready on the next recovery pass.",
          "owner", webhook_stuck),
    Check("automation_overdue", "Automations overdue",
          f"Scheduled automations {OVERDUE_MINUTES} minutes or more past their time and not run.",
          "Whether the scheduler is running, and the automation's last run (Automations).",
          "owner", automation_overdue),
    Check("relay_silent", "Webhook relay not pulled",
          f"Webhook relay addresses exist, and no delivery was pulled from Tico Gateway for {RELAY_SILENT_MINUTES} minutes.",
          "This server's Gateway key and network (Settings > Webhooks); deliveries wait at Tico Gateway until it works.",
          "owner", relay_silent),
    Check("long_holds", "Write lock held too long",
          f"More than {LONG_HOLDS_PER_HOUR} write transactions held the database lock for "
          f"{flight.LONG_HOLD_MS // 1000} seconds or more in the last hour.",
          "The routes and code sites named, most holds first: that code holds the one write lock for too long.",
          "botops", long_holds),
    Check("runner_errors", "Runner requests failing",
          f"A runner route answered with a 5xx for more than {RUNNER_ERROR_PERCENT}% of its requests in the last hour.",
          "The route named and the server log for those minutes; runners retry, so a burst means claims, events, "
          "renewals or uploads are failing.",
          "botops", runner_errors),
    Check("recorder_drops", "Flight recorder dropping samples",
          f"The flight recorder dropped more than {DROPPED_PERCENT}% of its samples in the last hour, so the numbers "
          "for those minutes are incomplete.",
          "The minutes named (Health's minute rows); the server is likely too busy to record, see its CPU and lock waits.",
          "botops", recorder_drops),
)
BY_ID = {check.id: check for check in CHECKS}


def _owner(c, auth, role):
    """The actor a check's task goes to, in order: BotOps when it is active (role "botops"), then the owner."""
    actors = []
    if role == "botops":
        from .onboarding import BOTOPS
        if (H.bot(c, BOTOPS) or {}).get("state") == "active":
            actors.append("bot:" + BOTOPS)
    try:
        owner = auth.owner_id(c)
    except Exception:  # noqa: BLE001 - an owner who cannot be read leaves the alert to BotOps alone
        owner = ""
    if owner:
        actors.append(H.human_actor(owner))
    return actors


def _alert(c, auth, check, hits):
    """One task for the check's owner, or None when nobody can take it (it is retried on the next sweep)."""
    title = f"Production check: {check.label}"
    body = (f"{check.says}\n\n{len(hits)} found. Examples: {', '.join(hits[:EXAMPLES])}.\n\n"
            f"Where to look: {check.look}\n\n"
            "Tico checks this every 15 minutes, shows it on Health, and closes this task when the check passes. "
            "Nothing is fixed automatically.")
    for actor in _owner(c, auth, check.owner):
        try:
            c.execute("SAVEPOINT invariant_alert")
            task = H.task_create(c, H.KEEPER, title, body, actor, lint=False)
            c.execute("RELEASE invariant_alert")
            return task["id"]
        except Exception:  # noqa: BLE001 - a refusal to file one line must not undo the check's record
            c.execute("ROLLBACK TO invariant_alert")
            c.execute("RELEASE invariant_alert")
            log.warning("Could not file the alert for production check %s to %s", check.id, actor, exc_info=True)
    return None


def _close(c, task_id):
    row = H.task(c, task_id) if task_id else None
    if not row or row["status"] not in H.ACTIVE_STATUSES:
        return
    try:
        c.execute("SAVEPOINT invariant_close")
        H.task_close(c, H.KEEPER, task_id, note="Cleared: the production check passes now.")
        c.execute("RELEASE invariant_close")
    except Exception:  # noqa: BLE001 - a task that cannot be closed stays open for a person to close
        c.execute("ROLLBACK TO invariant_close")
        c.execute("RELEASE invariant_close")
        log.warning("Could not close the alert task %s", task_id, exc_info=True)


def _apply(c, auth, check, hits, now):
    row = c.execute("SELECT * FROM invariant_state WHERE check_id=?", (check.id,)).fetchone()
    was = bool(row and row["firing"])
    task_id = row["task_id"] if row else None
    since = row["since"] if row else None
    if hits:
        if not was:
            since, task_id = now, None           # a new episode: one alert for it
        if not task_id:
            task_id = _alert(c, auth, check, hits)
        firing = 1
    else:
        if was:
            _close(c, task_id)
        firing, task_id, since = 0, None, None
    c.execute("INSERT INTO invariant_state(check_id,firing,count,examples,checked_at,since,task_id) "
              "VALUES(?,?,?,?,?,?,?) ON CONFLICT(check_id) DO UPDATE SET firing=excluded.firing, count=excluded.count, "
              "examples=excluded.examples, checked_at=excluded.checked_at, since=excluded.since, "
              "task_id=excluded.task_id",
              (check.id, firing, len(hits), json.dumps(hits[:EXAMPLES]), now, since, task_id))


def _changed(row, hits):
    """Whether a sweep must write this check's row or alert: a new firing episode, a firing check whose alert is
    missing, a count or example list that moved, or a check that stopped firing. A check that passes and passed
    before writes nothing."""
    was = bool(row and row["firing"])
    if not hits:
        return was
    if not was or not row["task_id"]:
        return True
    return row["count"] != len(hits) or row["examples"] != json.dumps(hits[:EXAMPLES])


def sweep(store, auth, settings, at, record=None):
    """Reads every check in one read snapshot (no write lock), then, only if a check's state changed, opens ONE write
    transaction that raises or clears its alert and writes its row. `record(c, counts)`, when given, runs inside that
    transaction and only then (the run record goes there). Answers {check id: count} for the checks run, or {} on a
    rehearsal or demo copy."""
    if settings.rehearsal or settings.demo:
        return {}
    ctx = Ctx(now=at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z", at=at)
    with store.read_transaction() as c:
        state = {row["check_id"]: row for row in c.execute("SELECT * FROM invariant_state")}
        found = {check.id: check.read(c, ctx) for check in CHECKS}
    counts = {check.id: len(found[check.id]) for check in CHECKS}
    changed = [check for check in CHECKS if _changed(state.get(check.id), found[check.id])]
    if changed:
        with store.transaction() as c:
            for check in changed:
                _apply(c, auth, check, found[check.id], ctx.now)
            if record is not None:
                record(c, counts)
    return counts


def health_checks(c):
    """Health's rows for the checks that fire now: count and example ids, and a link to the alert's task."""
    out = []
    for row in c.execute("SELECT * FROM invariant_state WHERE firing=1 ORDER BY check_id"):
        check = BY_ID.get(row["check_id"])
        if not check:
            continue
        examples = json.loads(row["examples"] or "[]")
        count = row["count"]
        shown = f"{count}{'+' if count >= LIMIT else ''}"
        more = f" and {count - len(examples)} more" if count > len(examples) else ""
        since = (row["since"] or "")[:16].replace("T", " ")
        fixes = [{"label": "Open the alert", "href": "#/tasks/" + row["task_id"], "tab": "", "click": ""}] \
            if row["task_id"] else []
        out.append({"id": "invariant:" + check.id, "label": check.label, "status": "warn",
                    "summary": f"{check.says} {shown} found: {', '.join(examples)}{more}. Since {since} UTC.",
                    "fixes": fixes})
    return out
