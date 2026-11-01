"""Pipeline stats for a task type in one read (`GET /api/v2/pipeline`, `hub pipeline`, the `hub_pipeline` tool).

The tickets are the Tasks board's own read: `H.tasks` over the caller's `Auth.task_sql`, the company lane and the live
statuses, so a private ticket counts only for a reader who may open it and a closed one never counts. Each step
says how many tickets sit in it, how long they have (from the step event that put them there), whose they are and whom
they wait on (`H.wait_detail`, the same reasons a ticket's Waiting tag gives), and their open pull requests.

The queue is told apart honestly: a queued job behind an interrupted run nobody reviewed (`execution.unfenced_sql`)
is never handed out until that run is resumed or dismissed, so it is `dead`, never `queued`; a job its wake preset
holds is `held`. Failed attempts in the window are counted by cause with their first and last time. Every job and
attempt counts only when the caller may read it (backend/task_privacy.py), as on a bot's page. A stopped run's
recovery decision (resume or dismiss) is listed beside the bot's queue with who made it, when and its note.

Counts and ids only: no titles, bodies or run text; a decision's note is the decider's words (Tico's own can name the
task), shown only to a reader who may open the run's conversation.
"""

import json
import re
from statistics import median

from fastapi import Request

from . import pipeline_exceptions, task_privacy as privacy
from .store import H, Problem

TASK_CAP = 5000            # tickets read for one report; `truncated` says when a type holds more live ones
IDS_PER_STEP = 50          # ticket ids listed per step; the count is always whole
DEAD_IDS = 20              # dead job ids listed per bot
DECISIONS_LISTED = 20      # held or recently decided stopped runs listed per bot, newest first
ATTEMPT_CAP = 5000         # readable failed attempts counted for one window; past it `complete_after` says how far back counts are whole
ATTEMPT_SCAN = 50000       # failed attempts read for one window, readable or not
ATTEMPT_PAGE = 1000
WINDOW_MAX_DAYS = 7
CAUSES = ("quota", "capacity", "restart", "timeout", "crash", "stopped")
QUOTA = re.compile(r"(?i)quota|usage limit|rate.?limit|\b429\b|billing|credit balance|insufficient_quota")
TIMEOUT = re.compile(r"(?i)timed? ?out|timeout|time limit|lease expired|heartbeat stopped")
RESTART = re.compile(r"(?i)restart")


def default_type(c):
    """The engineering board's type (Dev tickets on a factory team), else the only numbered type."""
    row = c.execute("SELECT id FROM task_types WHERE role='engineering' ORDER BY created_at, id LIMIT 1").fetchone()
    if not row:
        numbered = c.execute("SELECT id FROM task_types WHERE numbered=1 LIMIT 2").fetchall()
        row = numbered[0] if len(numbered) == 1 else None
    return H.type_get(c, row[0]) if row else None


def hours(since, now):
    a, b = H.parse_ts(since), H.parse_ts(now)
    return round((b - a).total_seconds() / 3600, 1) if a and b else None


def waits_kind(on):
    if not on:
        return "nothing"
    on = str(on)
    if on.startswith("bot:"):
        return "bot"
    if on.startswith("human:") or on == "person":
        return "person"
    return "external"


def steps_report(c, typ, rows, now):
    ids = [r["id"] for r in rows]
    entered, prs = {}, {}
    if ids:
        listed = json.dumps(ids)
        for task_id, at in c.execute(
                "SELECT e.task_id, max(e.ts) FROM task_events e JOIN tasks t ON t.id=e.task_id "
                "WHERE e.task_id IN (SELECT value FROM json_each(?)) AND e.field='step' AND e.new=t.step_id "
                "GROUP BY e.task_id", (listed,)):
            entered[task_id] = at
        for task_id, created in c.execute(
                "SELECT task_id, created_at FROM task_links WHERE kind='pr' AND state IN ('open','draft') "
                "AND task_id IN (SELECT value FROM json_each(?))", (listed,)):
            prs.setdefault(task_id, []).append(created)
    known = {s["id"] for s in typ["steps"]}
    by_step = {}
    for r in rows:
        by_step.setdefault(r["step_id"] if r["step_id"] in known else None, []).append(r)

    def age(row):
        from .task_blocked import clocks
        if row.get("blocked_since"):
            return clocks(row, now)["blocked_seconds"] / 3600
        if row.get("building_started_at"):
            return clocks(row, now)["building_seconds"] / 3600
        return hours(entered.get(row["id"]) or row["created_at"], now)

    def one(items):
        ages = [age(r) for r in items]
        ages = [a for a in ages if a is not None]
        owners, waits, on = {}, {"bot": 0, "person": 0, "external": 0, "nothing": 0}, {}
        open_prs = [at for r in items for at in prs.get(r["id"], [])]
        for r in items:
            owners[r["owner"]] = owners.get(r["owner"], 0) + 1
            target = (H.wait_detail(c, r) or {}).get("on")
            waits[waits_kind(target)] += 1
            if target:
                on[target] = on.get(target, 0) + 1
        oldest = max(items, key=lambda r: age(r) or 0, default=None)
        label = (lambda r: r["number"]) if typ["numbered"] else (lambda r: r["id"])
        return {"count": len(items), "snoozed": sum(1 for r in items if H.TS.snoozed(r)),
                "oldest_hours": max(ages) if ages else None, "median_hours": round(median(ages), 1) if ages else None,
                "oldest": label(oldest) if oldest else None,
                "ids": [label(r) for r in items[:IDS_PER_STEP]], "ids_more": max(0, len(items) - IDS_PER_STEP),
                "by_owner": dict(sorted(owners.items(), key=lambda kv: (-kv[1], kv[0]))),
                "waits_on": waits, "waits_on_actor": dict(sorted(on.items(), key=lambda kv: (-kv[1], kv[0]))),
                "prs": {"open": len(open_prs), "oldest_hours": max(hours(at, now) or 0 for at in open_prs) if open_prs else None}}
    steps = [{"id": s["id"], "name": s["name"], "status": s["status"], **one(by_step.get(s["id"], []))}
             for s in typ["steps"] if s["status"] in H.ACTIVE_STATUSES]
    loose = by_step.get(None, [])
    return steps, ({"no_step": one(loose)} if loose else {})


def readable_bots(c, auth, who, bot=None):
    access = auth.bot_accesses(c, who, [bot] if bot else None)
    return sorted(slug for slug, level in access.items() if level.get("read"))


def job_queues(c, who, bots, now=None):
    """{bot: {queued, held, dead, uncertain, oldest_queued_hours, dead_ids}} over the jobs the caller may read: the one
    count of a bot's waiting work, here and on its page (backend/bot_rows.py, views.attention), so the two never
    disagree. A job behind a run nobody reviewed (`execution.unfenced_sql`) is `dead`, a job its wake preset holds
    is `held`; only the rest is `queued`."""
    privacy.snapshot(c)
    actor = privacy.actor(who)
    rows = _queue_job_rows(c, bots)
    # One privacy prime for every queued/uncertain message (and its attempt), not message_readable per row —
    # /api/status and Operations rebuild these counts on every poll.
    shown = {m["id"] for m in privacy.readable_messages(c, actor, [{"id": row["id"]} for row in rows])}
    privacy.prime_attempts(c, [row["job_attempt"] for row in rows if row["id"] in shown and row["job_attempt"]])
    memo = {}

    def keep(row):
        return row["id"] in shown and (
            not row["job_attempt"] or privacy.attempt_readable(c, actor, row["job_attempt"], memo))

    out = _tally_queues(bots, rows, now, keep)
    for q in out.values():
        del q["oldest_queued_at"], q["oldest_dead_at"]
    return out


def queue_counts(c, bots, now=None):
    """job_queues' split over every job, with the oldest queued and dead job's time (`oldest_queued_at`,
    `oldest_dead_at`). Health reads it, so the queued count it shows a bot is the one Pipeline shows an operator who may
    read all of that bot's jobs."""
    return _tally_queues(bots, _queue_job_rows(c, bots), now, None)


def _queue_job_rows(c, bots):
    from .execution import unfenced_sql
    rows = []
    for part in H.chunks(list(bots)):
        marks = ",".join("?" * len(part))
        rows.extend(c.execute(
            f"SELECT m.*, j.id AS job_id, j.bot AS job_bot, j.state AS job_state, j.created_at AS job_created, "
            f"j.held AS job_held, j.attempt_id AS job_attempt, {unfenced_sql()} AS free FROM jobs j "
            "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.state IN ('queued','uncertain') AND j.bot IN ({marks}) ORDER BY j.created_at, j.id", part))
    return rows


def _tally_queues(bots, rows, now, keep):
    now = now or H.now()
    out = {b: {"queued": 0, "held": 0, "dead": 0, "uncertain": 0, "oldest_queued_hours": None, "oldest_queued_at": None,
               "oldest_dead_at": None, "dead_ids": []} for b in bots}
    stopped = {b: set() for b in bots}
    for row in rows:
        if keep and not keep(row):
            continue
        q = out[row["job_bot"]]
        if row["job_state"] == "uncertain":
            # Counted per stopped run: requests folded into one run share its attempt and are one run to a person.
            key = row["job_attempt"] or row["job_id"]
            if key not in stopped[row["job_bot"]]:
                stopped[row["job_bot"]].add(key)
                q["uncertain"] += 1
        elif not row["free"]:
            q["dead"] += 1
            q["oldest_dead_at"] = q["oldest_dead_at"] or row["job_created"]
            if len(q["dead_ids"]) < DEAD_IDS:
                q["dead_ids"].append(row["job_id"])
        elif row["job_held"]:
            q["held"] += 1
        else:
            q["queued"] += 1
            q["oldest_queued_at"] = q["oldest_queued_at"] or row["job_created"]
    for q in out.values():
        q["oldest_queued_hours"] = hours(q["oldest_queued_at"], now)
    return out


def queues_report(c, who, bots, now):
    actor = privacy.actor(who)
    memo = {}
    jobs = job_queues(c, who, bots, now)
    out = {b: {"running": 0, "queued": 0, "held": 0, "dead": 0, "uncertain": 0, "stalled": 0,
               "oldest_queued_hours": None, "dead_ids": [], **jobs[b]} for b in bots}
    for part in H.chunks(list(out)):
        marks = ",".join("?" * len(part))
        for row in c.execute(f"SELECT a.id, a.bot, d.stalled_at FROM attempts a LEFT JOIN attempt_diagnostics d "
                             f"ON d.attempt_id=a.id WHERE a.state IN ('leased','running') AND a.bot IN ({marks})", part):
            if privacy.attempt_readable(c, actor, row["id"], memo):
                out[row["bot"]]["running"] += 1
                out[row["bot"]]["stalled"] += bool(row["stalled_at"])
    return out


def decisions_report(c, auth, who, bots, since):
    """{bot: [decision]}: each readable job with a recovery decision (run_view.recovery, the job_recovery record) that
    still holds it (a saved choice waiting for its lane) or was made since `since`, newest first, at most
    DECISIONS_LISTED a bot. Each row names the job's state while the decision is about its current try (null and
    `current` false once a later try has run), the decision, who made it (BotOps, a person or Tico's
    auto-reconcile), when, and its note, and `label` says it as one line. The note is a decider's own words; it is left out for a reader who cannot open the run's
    conversation, as the run page leaves the run out."""
    from .execution import owner_name
    from .run_view import decision_label, recovery
    actor = privacy.actor(who)
    memo, out, opened = {}, {b: [] for b in bots}, {}

    def can_open(cid):
        if cid not in opened:
            try:
                auth.conversation(c, who, cid)
                opened[cid] = True
            except Problem:
                opened[cid] = False
        return opened[cid]
    for part in H.chunks(list(out)):
        marks = ",".join("?" * len(part))
        rows = c.execute(
            "SELECT m.*, j.id AS job_id, j.bot AS job_bot, j.state AS job_state, r.attempt_id AS decided_attempt "
            "FROM job_recovery r JOIN jobs j ON j.id=r.job_id JOIN messages m ON m.id=j.message_id "
            f"WHERE j.bot IN ({marks}) AND ((j.state='uncertain' AND r.attempt_id=j.attempt_id) OR r.created_at>=?) "
            "ORDER BY r.created_at DESC, j.id",
            (*part, since)).fetchall()
        kept, listed = [], {}
        for r in rows:
            if (listed.get(r["job_bot"], 0) < DECISIONS_LISTED and privacy.message_readable(c, actor, r, current=True)
                    and privacy.attempt_readable(c, actor, r["decided_attempt"], memo)):
                kept.append(r)
                listed[r["job_bot"]] = listed.get(r["job_bot"], 0) + 1
        saved = recovery(c, [r["job_id"] for r in kept])
        for r in kept:
            d = saved[r["job_id"]]
            if not can_open(r["conversation_id"]):
                d = {**d, "note": None}
            # A decision about an earlier try is history: no state of the job's later try is put beside it.
            state = d["state"] and ("held" if d["state"] == "uncertain" else d["state"])
            out[r["job_bot"]].append({"job": r["job_id"], "attempt": d["attempt_id"], "current": d["current"],
                                      "state": state, "decision": d["decision"], "by": d["by"],
                                      "by_kind": d["by_kind"], "at": d["at"], "note": d["note"],
                                      "label": decision_label(d, lambda a: owner_name(c, a))})
    return out


def cause_of(row):
    """Why one failed, interrupted or expired attempt ended, as one of CAUSES."""
    try:
        result = json.loads(row["result"] or "{}")
    except (TypeError, ValueError):
        result = {}
    result = result if isinstance(result, dict) else {}
    text = " ".join(str(v) for v in (row["end_reason"], result.get("text"), result.get("error"), result.get("reason")) if v)
    source = result.get("limit_source") if isinstance(result.get("limit_source"), dict) else {}
    if row["stop_requested_at"]:
        return "stopped"
    if source.get("kind") == "capacity":
        return "capacity"
    if source or result.get("limited") or result.get("primary_limited") or QUOTA.search(text):
        return "quota"
    if row["state"] == "interrupted" or RESTART.search(text):
        return "restart"
    if row["state"] == "expired" or TIMEOUT.search(text):
        return "timeout"
    return "crash"


def failures_report(c, who, bots, since):
    # Pages walk back from the newest and skip unreadable rows before counting, so unreadable newer runs never push
    # readable older ones out of the window; a stop at a cap says from when the counts are whole.
    actor = privacy.actor(who)
    memo = {}
    causes, per_bot, total, scanned, complete_after = {}, {}, 0, 0, None
    at = "coalesce(a.finished_at, a.created_at)"
    for part in H.chunks(list(bots)):
        marks = ",".join("?" * len(part))
        before = None
        while True:
            page = c.execute(
                f"SELECT a.id, a.bot, a.state, a.result, a.stop_requested_at, {at} AS at, d.end_reason "
                "FROM attempts a LEFT JOIN attempt_diagnostics d ON d.attempt_id=a.id "
                f"WHERE a.state IN ('failed','interrupted','expired') AND a.bot IN ({marks}) AND {at}>=? "
                + (f"AND ({at}<? OR ({at}=? AND a.id<?)) " if before else "")
                + f"ORDER BY {at} DESC, a.id DESC LIMIT ?",
                (*part, since, *((before[0], before[0], before[1]) if before else ()), ATTEMPT_PAGE)).fetchall()
            stop = False
            for row in page:
                if total >= ATTEMPT_CAP or scanned >= ATTEMPT_SCAN:
                    complete_after, stop = max(complete_after or row["at"], row["at"]), True
                    break
                scanned += 1
                before = (row["at"], row["id"])
                if not privacy.attempt_readable(c, actor, row["id"], memo):
                    continue
                total += 1
                cause = cause_of(row)
                seen = causes.setdefault(cause, {"n": 0, "first": row["at"], "last": row["at"]})
                seen["n"] += 1
                seen["first"], seen["last"] = min(seen["first"], row["at"]), max(seen["last"], row["at"])
                per_bot.setdefault(row["bot"], {}).setdefault(cause, 0)
                per_bot[row["bot"]][cause] += 1
            if stop or len(page) < ATTEMPT_PAGE:
                break
    return {"since": since, "total": total, "truncated": complete_after is not None, "complete_after": complete_after,
            "by_cause": {k: causes[k] for k in CAUSES if k in causes},
            "by_bot": dict(sorted(per_bot.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])))}


def report(c, auth, who, *, type=None, owner=None, label=None, updated_since=None, snoozed="all", bot=None,
           since="24h"):
    from .views import since_time
    auth.domain(who)
    typ = H.type_get(c, type) if type else default_type(c)
    if not typ:
        raise Problem("type", f"No task type {type}" if type else "Name a task type: this team has no engineering board", 422)
    if snoozed not in ("all", "hide", "only"):
        raise Problem("snoozed", "snoozed is all, hide or only", 422)
    owner_actor = H.resolve_actor(c, owner) if owner else None
    if owner and not owner_actor:
        raise Problem("owner", "owner is a person or a bot on the roster", 422)
    changed = since_time(updated_since) if updated_since else None
    window = since_time(since or "24h")
    if window < H.shift(H.now(), days=-WINDOW_MAX_DAYS):
        raise Problem("since", f"since reaches back at most {WINDOW_MAX_DAYS} days", 422)
    now = H.now()
    rows = H.tasks(c, owner=owner_actor, status=H.ACTIVE_STATUSES, lane="company", label=label, limit=TASK_CAP + 1,
                   type_id=typ["id"], updated_since=changed, visible=auth.task_sql(c, who),
                   snoozed=None if snoozed == "all" else snoozed, order="step")
    truncated = len(rows) > TASK_CAP
    rows = rows[:TASK_CAP]
    steps, loose = steps_report(c, typ, rows, now)
    bots = readable_bots(c, auth, who, bot) if not bot or H.bot(c, bot) else []
    if bot and not bots:
        raise Problem("not_found", "Bot not found", 404)      # unknown, or not one whose runs the caller may read
    queues = queues_report(c, who, bots, now)
    failures = failures_report(c, who, bots, window)
    for b, decided in decisions_report(c, auth, who, bots, window).items():
        queues[b]["decisions"] = decided
    busy = {b for b, q in queues.items() if any(q[k] for k in ("running", "queued", "held", "dead", "uncertain",
                                                                "decisions"))}
    busy |= set(failures["by_bot"])
    # Only the type's manager reads its exceptions list (backend/pipeline_exceptions.py); everyone else has no key.
    exceptions = pipeline_exceptions.report(c, auth, who, typ, at=now)
    return {"now": now, "type": {"id": typ["id"], "name": typ["name"]},
            "filters": {"owner": owner_actor, "label": label, "updated_since": changed, "snoozed": snoozed,
                        "bot": bot, "since": window},
            "total": len(rows), "truncated": truncated, "steps": steps, **loose,
            "queues": {b: queues[b] for b in sorted(busy)}, "failures": failures,
            **({"exceptions": exceptions} if exceptions is not None else {})}


def install(app, store, auth):
    @app.get("/api/v2/pipeline")
    def pipeline(request: Request, type: str | None = None, owner: str | None = None, label: str | None = None,
                 updated_since: str | None = None, snoozed: str = "all", bot: str | None = None, since: str = "24h"):
        """A task type's pipeline in one read: per step the count, age, owners, whom it waits on and open PRs; each
        readable bot's running, queued, held, dead and uncertain jobs; failed attempts by cause since `since`."""
        with store.read() as c:
            return report(c, auth, request.state.identity, type=type, owner=owner, label=label,
                          updated_since=updated_since, snoozed=snoozed, bot=bot, since=since)
