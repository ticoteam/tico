"""The Automations overview (CONTRACT §2): every run across the automations a caller may see, and their counts over a
time range, for the page's list, its Runs tab and BotOps.

Seeing a run follows seeing its automation (`automations.may_see`), so a peer's mail subject never reaches a run row.
Run rows are kept 30 days (`automations.prune` drops only their step detail), so the 7-day range reads
`automation_runs` directly. The system jobs Tico runs on its own timers record their runs in `system_runs`
(backend/system_routines.py), not here; their read-only built-in automations (`tico.builtin.job.<key>`, the
migration's mirrors) read them from there. A quiet run that repeats its job's last result is folded into that row, so a
system job counts one run per row, with `count` the runs folded into it."""
import json
from datetime import timedelta

from . import automation_flow as F
from .store import H, Problem

# range: (seconds, buckets). 5m is 10 of 30s, 1h 12 of 5 min, 24h 24 of an hour, 7d 28 of 6 hours.
RANGES = {"5m": (300, 10), "1h": (3600, 12), "24h": (86400, 24), "7d": (7 * 86400, 28)}
KINDS = ("message", "task_received", "schedule", "webhook", "listening", "event", "due", "system")
STATUSES = {"done": ("ok", "empty", "skipped"), "ok": ("ok", "empty", "skipped"), "failed": ("failed",),
            "waiting": ("waiting",), "running": ("queued", "running"), "held": ("held",)}
JOB_PREFIX = "tico.builtin.job."
SYSTEM_RUN = "sys_"          # a system job's run id, so it never meets an automation run's
IDS_MAX = 500
STEP_WORDS = {"filter": "Filter", "decide": "Decide", "llm": "LLM", "find": "Find", "bot_turn": "Bot turn",
              "paths": "Paths", "action": "Action"}
DONE_WORDS = {"archive": "Archived", "leave_for_you": "Left for you", "draft_reply": "Draft reply",
              "send_reply": "Replied", "create_task": "Task created", "label": "Labeled", "forward": "Forwarded",
              "wake_bot": "Bot woken", "note": "Noted", "post_update": "Update posted", "call_webhook": "Webhook called",
              "assign": "Assigned"}
ACTION_WORDS = {"archive": "Archive", "leave_for_you": "Leave for you", "draft_reply": "Draft reply",
                "send_reply": "Send reply", "create_task": "Create task", "label": "Label", "forward": "Forward",
                "wake_bot": "Wake a bot", "note": "Note", "post_update": "Post update", "call_webhook": "Call a webhook",
                "assign": "Assign"}


def window(rng):
    """(now, since, bucket seconds, buckets) for a range; an unknown one is refused."""
    if rng not in RANGES:
        raise Problem("invalid", "range is one of " + ", ".join(RANGES), 422)
    seconds, n = RANGES[rng]
    now = H.now()
    return now, H.shift(now, seconds=-seconds), seconds / n, n


def system_key(flow):
    """The system job a read-only built-in shows, else None."""
    name = str((flow.get("trigger") or {}).get("name") or "")
    return name[len(JOB_PREFIX):] if flow.get("readonly") and name.startswith(JOB_PREFIX) else None


def kind_of(flow):
    """The trigger as the page groups it: its type, or `system` for Tico's own jobs (their mirrors, the Health
    Monitor, and a job that became a real automation, Stale ticket review)."""
    trigger = flow.get("trigger") or {}
    if flow.get("builtin") and (system_key(flow) or trigger.get("name") == "tico.builtin.health"
                                or (flow.get("source") or {}).get("kind") == "system_job"):
        return "system"
    return trigger.get("type") or ""


def system_jobs(c, settings=None):
    """{job key: {schedule, on, next, last}}: each system job's schedule in words and its newest run, read once for a
    list of their mirrors."""
    from datetime import datetime, timezone
    from . import system_routines as SR
    if not H._has_table(c, "system_runs"):
        return {}
    now = datetime.now(timezone.utc)
    last, cfgs = SR.last_runs(c), SR.config(c)
    out = {}
    for job in SR.JOBS:
        cfg, ctl = cfgs.get(job.key), SR.CONTROLS.get(job.key)
        timed = cfg if ctl and ctl.presets else None
        row = last.get(job.key)
        try:
            following = SR.next_at(job, row, now, settings, c, timed) if job.records else None
        except Exception:  # noqa: BLE001 - a job whose source cannot be read just shows no next run
            following = None
        out[job.key] = {"schedule": SR.schedule_words(job.key, cfg) if timed else job.schedule,
                        "on": cfg["on"] if cfg else True, "next": SR._stamp(following) if following else None,
                        "last": _system_last(row) if row else None}
    return out


def _system_status(row):
    return "running" if not row["finished_at"] else "ok" if row["ok"] else "failed"


def _system_last(row):
    return {"id": SYSTEM_RUN + row["id"], "status": _system_status(row), "started_at": row["started_at"],
            "finished_at": row["finished_at"]}


def visible(c, who):
    """(row, flow) of every automation `who` may see, newest first; one org chart for the lot."""
    from . import automations as AU
    chart = AU.Chart(c) if who.role in ("human", "bot") else None
    out = []
    for r in c.execute("SELECT * FROM automations WHERE deleted_at IS NULL ORDER BY created_at DESC").fetchall():
        flow = AU.flow_of(r)
        if AU.may_change(c, who, {**flow, "readonly": False}, chart):
            out.append((dict(r), flow))
    return out


def _pick(rows, trigger="", owner="", ids=""):
    kinds = {k for k in str(trigger or "").split(",") if k}
    if kinds - set(KINDS):
        raise Problem("invalid", "trigger is one or more of " + ", ".join(KINDS), 422)
    wanted = [i for i in str(ids or "").split(",") if i]
    if len(wanted) > IDS_MAX:
        raise Problem("invalid", f"ids names at most {IDS_MAX} automations", 422)
    owner = F.owner_ref(owner) if owner else ""
    return [(r, f) for r, f in rows if (not kinds or kind_of(f) in kinds) and (not owner or r["owner_id"] == owner)
            and (not wanted or r["id"] in wanted)]


# ---------------------------------------------------------------------------------------------------- a run's row
def _words(name):
    return str(name or "").replace("_", " ").replace(".", " ").strip().capitalize()


def started_by(payload, trigger_ref="", kind=""):
    """What started the run, in a few words: the sender and subject, the task's title, the webhook's event."""
    p = payload if isinstance(payload, dict) else {}
    m, t, w, li, e, d = (p.get(k) if isinstance(p.get(k), dict) else None
                         for k in ("message", "task", "webhook", "listening", "event", "due"))
    if m:
        if m.get("source") == "slack":
            words = [str(m.get("inbox") or "Slack").replace("slack:dm", "Slack DM").replace("slack:#", "Slack #"),
                     str(m.get("text") or "").split("\n")[0]]
        else:
            words = ["From " + str(m.get("from_name") or m.get("from") or "?"), str(m.get("subject") or "")]
    elif t:
        words = [str(t.get("title") or t.get("id") or "Task")]
    elif w:
        data = w.get("data") if isinstance(w.get("data"), dict) else {}
        words = [str(w.get("name") or "Webhook"), str(w.get("type") or data.get("type") or data.get("event") or "")]
    elif li:
        words = [str(li.get("source") or "Listening"), str(li.get("url") or li.get("author") or "")]
    elif e:
        words = [_words(e.get("name")), str(e.get("subject") or "")]
    elif d:
        words = ["Due " + str(d.get("due_on") or "")]
    elif p.get("manual"):
        words = ["Run by hand"]
    elif kind in ("schedule", "system"):
        words = ["Scheduled"]
    else:
        words = [str(trigger_ref or "").split(":")[0].capitalize() or "—"]
    return " · ".join(w for w in words if w)[:140]


def _find(steps, sid):
    for step in steps or []:
        if step.get("id") == sid:
            return step
        if step.get("type") == "paths":
            for path in [*(step.get("paths") or []), step.get("otherwise") or {}]:
                hit = _find(path.get("steps"), sid)
                if hit:
                    return hit
    return None


def _label(record, flow):
    step = _find(flow.get("steps"), record.get("id")) or {}
    if record.get("type") == "action":
        return ACTION_WORDS.get(step.get("do") or (record.get("outputs") or {}).get("do"), "Action")
    return STEP_WORDS.get(record.get("type"), "Step")


def outcome(status, state, flow):
    """The run's result in a few words: the last thing it did, or the step it failed or waits at, and why."""
    steps = (state or {}).get("steps") or []
    if status == "failed":
        bad = next((s for s in steps if s.get("status") == "failed"), None)
        why = str(((bad or {}).get("outputs") or {}).get("error") or (state or {}).get("error") or "")
        head = f"{_label(bad, flow)} failed" if bad else "Failed"
        return (f"{head}: {why}" if why else head)[:140]
    if status == "waiting":
        at = next((s for s in reversed(steps) if s.get("status") == "waiting"), None)
        return f"Waiting at {_label(at, flow)}" if at else "Waiting"
    if status in ("queued", "running"):
        return "Running"
    if status == "held":
        return "Held"
    if status == "empty":
        return "Nothing found"
    if status == "skipped":
        return "Skipped"
    done = [s for s in steps if s.get("status") == "ok" and s.get("type") == "action"]
    if done:
        step = _find(flow.get("steps"), done[-1].get("id")) or {}
        return DONE_WORDS.get(step.get("do") or (done[-1].get("outputs") or {}).get("do"), "Done")
    capped = sum(s.get("status") == "spend_cap" for s in steps)
    return "Spend cap" if capped else "Done"


def run_row(run, row, flow):
    """An automation run for the overview: no step detail, but what started it, how it ended and its step count."""
    try:
        state = json.loads(run["saved_state"]) if run["saved_state"] else None
    except (TypeError, ValueError):
        state = None
    kind = kind_of(flow)
    return {"id": run["id"], "automation_id": row["id"], "automation_name": row["name"], "trigger": kind,
            "owner_id": row["owner_id"], "version": run["version"], "trigger_ref": run["trigger_ref"],
            "started_at": run["started_at"], "finished_at": run["finished_at"], "status": run["status"], "cost": run["cost"],
            "started_by": started_by((state or {}).get("payload"), run["trigger_ref"], kind),
            "outcome": outcome(run["status"], state, flow),
            "step_count": len((state or {}).get("steps") or []) if state else None, "count": 1}


def system_row(run, row):
    status = _system_status(run)
    return {"id": SYSTEM_RUN + run["id"], "automation_id": row["id"], "automation_name": row["name"],
            "trigger": "system", "owner_id": row["owner_id"], "version": row["version"], "trigger_ref": "system",
            "started_at": run["started_at"], "finished_at": run["finished_at"], "status": status, "cost": run["cost_usd"] or 0,
            "started_by": "Scheduled", "outcome": str(run["result"] or ("Done" if status == "ok" else ""))[:140],
            "step_count": None, "count": run["runs"] or 1}


def system_runs_of(c, key, limit, before=""):
    """One system job's runs as an automation's run log shows them (GET /{id}/runs on its mirror)."""
    at = None
    if before:
        at = c.execute("SELECT started_at, id FROM system_runs WHERE id=? AND job=?",
                       (before.removeprefix(SYSTEM_RUN), key)).fetchone() if H._has_table(c, "system_runs") else None
        if not at:
            raise Problem("not_found", "No such run", 404)
    if not H._has_table(c, "system_runs"):
        return [], False
    rows = c.execute("SELECT * FROM system_runs WHERE job=?" + (" AND (started_at<? OR (started_at=? AND id<?))" if at else "")
                     + " ORDER BY started_at DESC, id DESC LIMIT ?",
                     (key, *((at["started_at"], at["started_at"], at["id"]) if at else ()), limit + 1)).fetchall()
    return rows[:limit], len(rows) > limit


def system_run_view(run, aid):
    """A system job's run in the shape of an automation's run (no steps: the job is one block of Tico's code)."""
    return {"id": SYSTEM_RUN + run["id"], "automation_id": aid, "version": 1, "trigger_ref": "system",
            "started_at": run["started_at"], "finished_at": run["finished_at"], "status": _system_status(run),
            "cost": run["cost_usd"] or 0, "summary": str(run["result"] or "")[:140] or None, "steps": [],
            "count": run["runs"] or 1}


# ---------------------------------------------------------------------------------------------------- the routes
def _cursor(before):
    if not before:
        return None
    started, sep, rid = str(before).rpartition("|")
    if not sep or not started or not rid:
        raise Problem("invalid", "before is the `next` of the page before", 422)
    return started, rid.removeprefix(SYSTEM_RUN)


def runs(c, who, rng="1h", status="", trigger="", owner="", ids="", limit=50, before=""):
    """Runs across what `who` may see, newest first: `next` is the `before` for the page after."""
    from . import automations as AU
    _, since, _, _ = window(rng)
    if status and status not in STATUSES:
        raise Problem("invalid", "status is one of " + ", ".join(STATUSES), 422)
    limit, at = max(1, min(int(limit), 100)), _cursor(before)
    picked = _pick(visible(c, who), trigger, owner, ids)
    by_id = {r["id"]: (r, f) for r, f in picked}
    jobs = {key: (r, f) for r, f in picked if (key := system_key(f))}
    page_at = " AND (started_at<? OR (started_at=? AND id<?))" if at else ""
    marks = (at[0], at[0], at[1]) if at else ()
    found = []
    if by_id:
        states = STATUSES.get(status)
        found += [("a", r) for r in c.execute(
            "SELECT * FROM automation_runs WHERE started_at>=? AND automation_id IN (SELECT value FROM json_each(?))"
            + (f" AND status IN ({','.join('?' * len(states))})" if states else "") + page_at
            + " ORDER BY started_at DESC, id DESC LIMIT ?",
            (since, json.dumps(list(by_id)), *(states or ()), *marks, limit + 1)).fetchall()]
    if jobs and status not in ("waiting", "held") and H._has_table(c, "system_runs"):
        where = {"done": " AND ok=1 AND finished_at IS NOT NULL", "ok": " AND ok=1 AND finished_at IS NOT NULL",
                 "failed": " AND ok=0", "running": " AND finished_at IS NULL"}.get(status, "")
        found += [("s", r) for r in c.execute(
            "SELECT * FROM system_runs WHERE started_at>=? AND job IN (SELECT value FROM json_each(?))" + where + page_at
            + " ORDER BY started_at DESC, id DESC LIMIT ?", (since, json.dumps(list(jobs)), *marks, limit + 1)).fetchall()]
    found.sort(key=lambda x: (x[1]["started_at"], x[1]["id"]), reverse=True)
    more = len(found) > limit
    found = found[:limit]
    out = [run_row(r, *by_id[r["automation_id"]]) if src == "a" else system_row(r, jobs[r["job"]][0])
           for src, r in found]
    shown, cut = AU.page(out, limit, 0, AU.RUN_BYTES)
    nxt = None
    if (more or cut is not None) and shown:
        last = found[len(shown) - 1][1]
        nxt = f"{last['started_at']}|{last['id']}"
    return {"runs": shown, "next": nxt, "since": since}


def stats(c, who, rng="1h"):
    """Per automation `who` may see that ran in the range: runs, failed, cost and its buckets oldest first; the
    totals the same way. An automation with no run in the range is left out."""
    now, since, width, n = window(rng)
    rows = visible(c, who)
    seen = {r["id"] for r, _ in rows}
    jobs = {key: r["id"] for r, f in rows if (key := system_key(f))}
    per = {}

    def add(aid, b, runs, failed, cost):
        s = per.setdefault(aid, {"id": aid, "runs": 0, "failed": 0, "cost": 0.0,
                                 "buckets": [{"ok": 0, "failed": 0} for _ in range(n)]})
        b = min(max(int(b or 0), 0), n - 1)
        s["runs"] += runs
        s["failed"] += failed
        s["cost"] += cost or 0
        s["buckets"][b]["ok"] += runs - failed
        s["buckets"][b]["failed"] += failed

    if seen:
        for r in c.execute("SELECT automation_id, CAST((julianday(substr(started_at,1,23)) - julianday(?)) * 86400.0 / ? "
                           "AS INTEGER) AS b, COUNT(*) AS n, SUM(status='failed') AS f, SUM(cost) AS cost "
                           "FROM automation_runs WHERE started_at>=? AND automation_id IN (SELECT value FROM json_each(?)) "
                           "GROUP BY automation_id, b", (since[:23], width, since, json.dumps(list(seen)))):
            add(r["automation_id"], r["b"], r["n"], r["f"] or 0, r["cost"])
    if jobs and H._has_table(c, "system_runs"):
        start, step = H.parse_ts(since), timedelta(seconds=width)
        for r in c.execute("SELECT job, started_at, first_started_at, runs, ok, cost_usd FROM system_runs WHERE started_at>=? "
                           "AND job IN (SELECT value FROM json_each(?))", (since, json.dumps(list(jobs)))):
            at = H.parse_ts(r["started_at"])
            if not at:
                add(jobs[r["job"]], n - 1, 1, 0 if r["ok"] else 1, r["cost_usd"])
                continue
            # A folded row stands for `runs` quiet repeats between first_started and started (its latest): they are
            # spread evenly over that time and only those inside the range count. A row from before first_started
            # was kept counts once, at started.
            first, k = H.parse_ts(r["first_started_at"] or ""), max(int(r["runs"] or 1), 1)
            if not first or first >= at or k == 1:
                times = [at]
            else:
                times = [first + (at - first) * i / (k - 1) for i in range(k)]
            buckets = {}
            for t in times:
                if t >= start:
                    b = int((t - start) / step)
                    buckets[b] = buckets.get(b, 0) + 1
            for b, m in buckets.items():
                add(jobs[r["job"]], b, m, 0 if r["ok"] else m, (r["cost_usd"] or 0) * m / len(times))
    totals = {"runs": 0, "failed": 0, "cost": 0.0, "buckets": [{"ok": 0, "failed": 0} for _ in range(n)]}
    for s in per.values():
        s["cost"] = round(s["cost"], 6)
        totals["runs"] += s["runs"]
        totals["failed"] += s["failed"]
        totals["cost"] += s["cost"]
        for i, b in enumerate(s["buckets"]):
            totals["buckets"][i]["ok"] += b["ok"]
            totals["buckets"][i]["failed"] += b["failed"]
    totals["cost"] = round(totals["cost"], 6)
    return {"range": rng, "since": since, "until": now, "bucket_seconds": int(width), "automations": list(per.values()),
            "totals": totals}


def install(app, store, auth):
    """Registered before GET /api/v2/automations/{aid}, which would read `runs` and `stats` as ids."""
    from fastapi import Request

    @app.get("/api/v2/automations/runs")
    def automations_all_runs(request: Request, range: str = "1h", status: str = "", trigger: str = "",  # noqa: A002
                             owner: str = "", ids: str = "", limit: int = 50, before: str = ""):
        """Runs across every automation you may see, newest first, in the last `range` (5m, 1h, 24h, 7d): what started
        each, its outcome or failing step, its step count, time and cost; no step detail. `status` (done, failed,
        waiting, running, held), `trigger` (kinds, comma-separated, `system` for Tico's own jobs), `owner` and `ids` narrow
        it; `next` is the `before` for the page after."""
        who = request.state.identity
        with store.read() as c:
            auth.domain(who)
            return runs(c, who, range, status, trigger, owner, ids, limit, before)

    @app.get("/api/v2/automations/stats")
    def automations_stats(request: Request, range: str = "1h"):  # noqa: A002
        """Runs, failures and cost per automation you may see over the last `range` (5m, 1h, 24h, 7d), each split into
        buckets oldest first (10 of 30s, 12 of 5 min, 24 of an hour, 28 of 6 hours), and the totals the same way."""
        who = request.state.identity
        with store.read() as c:
            auth.domain(who)
            return stats(c, who, range)
