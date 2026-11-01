"""A task's run panel: every attempt that worked the task and what happened to it, the subagents each one started,
the background shells and monitors it left running, and the task's checkpoint.

`GET /api/v2/tasks/{id}/run-view` reads only what the hub already records: `turns` (one per attempt, by task),
`attempts` and `jobs` (a job's attempts are its tries), the audit events that record a recovery decision or a lease
grace, and the attempt's `tool` events. A tool event carries the call's name, the file or program it acts on and,
for a subagent, its type and model; never the call's arguments or output, so none can reach this view.
"""

import json
from datetime import timezone

from fastapi import Request

from .store import H, Problem
from . import login_holds, login_waits, run_diagnostics, task_privacy as privacy
from .turns import TOOL_START, TOOL_BACKGROUNDED, clip, seconds

ATTEMPT_LIMIT, NODE_LIMIT, SUMMARY_CHARS = 20, 50, 120
SCAN_LIMIT = 500  # the most of a task's newest runs read to fill one reader's page
LIVE = ("leased", "running")
# What a person or the hub decided about a stopped attempt, and the lease events that kept or lost it.
DECISIONS = ("job.reconcile", "job.auto_reconcile")
LEASE = {"attempt.lease-grace": "grace", "attempt.lease-restored": "restored"}
# Read by action and target (store index events_action_target), never by scanning events since a time.
RECOVERY_SQL = ("action IN ('job.reconcile','job.auto_reconcile','job.retry','attempt.lease-grace',"
                "'attempt.lease-restored')")


def failure_reason(state, result_json, end_reason=None):
    """A limit the attempt ended on is told as such (login_holds.failure_line), else the end reason, else its error."""
    if state not in ("failed", "expired"):
        return end_reason
    try:
        result = json.loads(result_json or "{}") or {}
    except (ValueError, TypeError):
        result = {}
    reason = login_holds.failure_line(result) or end_reason or result.get("error") or result.get("reason") or result.get("message")
    return clip(reason, 280) if reason else (
        "Lease expired before completion" if state == "expired" else "Attempt failed before completion")


def work(rows, live):
    """The subagents and background calls in one attempt's tool events, in the order they started. A call is
    `running` only while its attempt is: a call still open when the attempt ended reports no end."""
    agents, shells = {}, {}
    for p, at in rows:
        key = p.get("item_id")
        if not key:
            continue
        status = str(p.get("status") or "").lower()
        agent = p.get("subagent") if isinstance(p.get("subagent"), dict) else None
        if status == TOOL_BACKGROUNDED:
            # A call moved to the background while it ran: its start did not say background, so it is listed here.
            if key in agents:
                agents[key]["background"] = True
            elif key not in shells:
                shells[key] = {"id": key, "tool": clip(p.get("tool") or "tool", 60), "summary": "",
                               "parent_id": p.get("parent_id"), "status": None, "started_at": at, "ended": None}
        elif agent:
            node = agents.setdefault(key, {"id": key, "parent_id": agent.get("parent_id") or p.get("parent_id"),
                                           "type": None, "model": None, "status": None, "background": False,
                                           "tool_uses": None, "duration_ms": None, "started_at": at, "ended": None})
            node["type"] = clip(agent.get("type") or node["type"] or "agent", 60)
            node["model"] = clip(agent.get("model"), 80) or node["model"]
            for field in ("tool_uses", "duration_ms"):
                if isinstance(agent.get(field), (int, float)):
                    node[field] = agent[field]
            node["background"] = node["background"] or bool(p.get("background"))
            if status not in TOOL_START:
                node["status"], node["ended"] = status, at
        elif p.get("background"):
            if key not in shells and status in TOOL_START:
                shells[key] = {"id": key, "tool": clip(p.get("tool") or "tool", 60), "summary": clip(p.get("summary"), SUMMARY_CHARS),
                               "parent_id": p.get("parent_id"), "status": None, "started_at": at, "ended": None}
            elif key in shells and status not in TOOL_START:
                shells[key]["status"], shells[key]["ended"] = status, at
    for node in (*agents.values(), *shells.values()):
        node["running"] = node["ended"] is None and live
        if node["ended"] is None and not live:
            node["status"] = "no end reported"
        if node.get("duration_ms") is None and "tool_uses" in node and node["ended"]:
            took = seconds(node["started_at"], node["ended"])
            node["duration_ms"] = took * 1000 if took is not None else None
    return capped(list(agents.values())), capped(list(shells.values()))


def capped(nodes):
    """At most NODE_LIMIT, in start order: every running one first, then the newest of the ended."""
    running = [n for n in nodes if n["running"]][-NODE_LIMIT:]
    room = NODE_LIMIT - len(running)
    ended = [n for n in nodes if not n["running"]][-room:] if room else []
    keep = {id(n) for n in running + ended}
    return [n for n in nodes if id(n) in keep]


def decision_note(actor, detail):
    """A person's decision keeps its note. The hub's own shows the line it wrote for people, or else only the note's
    first paragraph: the rest of an automatic resume note is what the stopped run saved, the bot's text."""
    note = str(detail.get("note") or "")
    if actor != H.KEEPER:
        return clip(note, 400)
    return clip(detail.get("shown") or note.split("\n\n")[0], 400)


# The job's state beside a recovery decision: a saved choice still waiting for its lane is held.
JOB_WORDS = {"uncertain": "Held", "cancelled": "Cancelled", "queued": "Queued", "leased": "Running", "running": "Running",
             "input": "Running", "completed": "Done"}


def decided_by(actor):
    """Who made a recovery decision: Tico's own auto-reconcile, BotOps, a person, or another bot (an operator)."""
    return ("tico" if actor == H.KEEPER else "botops" if actor == "bot:" + H.FLEET_MAINTAINER
            else "person" if H.is_human(actor) else "bot")


def recovery(c, job_ids):
    """{job_id: decision} from job_recovery, the one record of a stopped run's resume or dismiss (the stopped-runs
    card reads it too, execution.stopped_rows): each job's latest decision and the attempt it was about. Read here
    each time, never copied onto the job, so a later decision replaces an earlier one wherever the job shows.
    job_recovery keeps that row after the job runs again, so `current` says whether the decision is still about the
    job's current try, and only then does `state` carry the job's state: a later try's stop is not the decision's."""
    out = {}
    for part in H.chunks(sorted(set(job_ids))):
        for r in c.execute("SELECT r.job_id,r.attempt_id,r.decision,r.note,r.actor,r.created_at,j.state AS job_state,"
                           "j.attempt_id AS job_attempt FROM job_recovery r JOIN jobs j ON j.id=r.job_id "
                           f"WHERE r.job_id IN ({','.join('?' * len(part))})", part):
            current = r["job_attempt"] == r["attempt_id"]
            out[r["job_id"]] = {"decision": "resume" if r["decision"] == "resume" else "dismiss",
                                "attempt_id": r["attempt_id"], "current": current,
                                "state": r["job_state"] if current else None,
                                "by": r["actor"], "by_kind": decided_by(r["actor"]),
                                "at": r["created_at"], "note": decision_note(r["actor"], {"note": r["note"]})}
    return out


def decision_label(decided, name):
    """"Cancelled: dismissed by BotOps at 09:50Z, <note>": the job's state, the decision, who, when (UTC) and the
    note, as one line. Once the job has moved to a later try the state is not this decision's, so the line says only
    what was decided: "Resumed by BotOps at 09:50Z, <note>". `name` turns the decider's actor into a person's name."""
    who = "Tico's auto-reconcile" if decided["by_kind"] == "tico" else name(decided["by"])
    at = H.parse_ts(decided["at"])
    at = at and at.astimezone(timezone.utc)
    verb = "resumed" if decided["decision"] == "resume" else "dismissed"
    state = decided.get("state")
    line = ((f"{JOB_WORDS.get(state, str(state).capitalize())}: {verb}" if state else verb.capitalize())
            + f" by {who}" + (f" at {at.strftime('%H:%M')}Z" if at else ""))
    return line + (f", {decided['note']}" if decided.get("note") else "")


def next_step(attempt, tries, job, decisions, retries):
    """What followed an attempt: a recorded decision, a person's retry, the hub's own retry, or a wait."""
    decided = decisions.get(attempt["id"])
    if decided:
        return decided
    later = [t for t in tries if t["created_at"] > attempt["created_at"]]
    if later:
        nxt = later[0]["created_at"]
        by = next((r for r in retries if (attempt["finished_at"] or attempt["created_at"]) <= r["at"] <= nxt), None)
        if by:
            return {"kind": "retried", "by": by["by"], "at": by["at"], "note": "Run again by hand."}
        return {"kind": "retried", "by": H.KEEPER, "at": nxt, "note": "Run again automatically."}
    if attempt["state"] in LIVE:
        return None
    if job["state"] == "uncertain":
        return {"kind": "waiting", "by": None, "at": None,
                "note": "Stopped partway after it may have acted: waiting for a person to decide whether to run it again."}
    if job["state"] in ("queued", "leased"):
        by = next((r for r in reversed(retries) if r["at"] >= (attempt["finished_at"] or attempt["created_at"])), None)
        return {"kind": "queued", "by": by and by["by"], "at": by and by["at"],
                "note": "Queued to run again by hand." if by else "Queued to run again."}
    return None


def run_view(c, auth, who, tid):
    actor = privacy.actor(who)
    # A task party who cannot read the bot sees none of its runs, as on the bot's steps and failed attempts.
    bots = auth.bot_accesses(c, who)

    def readable(r):
        # As on the run's own steps: the reader must open the conversation it answered, not only the task. A person's
        # direct message to the bot that names the task puts that run here, and it stays theirs.
        if not (bots.get(r["bot"]) or {}).get("read") or not privacy.attempt_readable(c, actor, r["id"]):
            return False
        try:
            auth.conversation(c, who, r["conversation_id"])
            return True
        except Problem:
            return False
    # The page limit counts only runs this reader can open: hidden newer runs must not push theirs off the page.
    turns = []
    for row in c.execute(
            "SELECT t.id,t.bot,t.exit,a.job_id,a.state,a.created_at,a.started_at,a.finished_at,a.lease_until,a.result,a.wake,"
            "m.conversation_id FROM turns t JOIN attempts a ON a.id=t.id JOIN jobs j ON j.id=a.job_id "
            "JOIN messages m ON m.id=j.message_id WHERE t.task_id=? ORDER BY a.created_at DESC LIMIT ?",
            (tid, SCAN_LIMIT)):
        r = dict(row)
        if readable(r):
            turns.append(r)
            if len(turns) == ATTEMPT_LIMIT:
                break
    ids = [r["id"] for r in turns]
    jobs = sorted({r["job_id"] for r in turns})
    # A blocked checkpoint is no checkpoint: no prompt or stall report uses it either.
    task = c.execute("SELECT checkpoint,checkpoint_at,checkpoint_by,checkpoint_blocked FROM tasks WHERE id=?", (tid,)).fetchone()
    checkpoint = ({"text": task["checkpoint"], "at": task["checkpoint_at"], "by": task["checkpoint_by"]}
                  if task and (task["checkpoint"] or "").strip() and not task["checkpoint_blocked"] else None)
    if not ids:
        return {"task_id": tid, "checkpoint": checkpoint, "attempts": []}
    marks, job_marks = ",".join("?" * len(ids)), ",".join("?" * len(jobs))
    job_rows = {r["id"]: r for r in H._rows(c.execute(f"SELECT id,state FROM jobs WHERE id IN ({job_marks})", jobs))}
    # Tries are counted over every attempt of the job, not only the ones this page shows.
    tries = {}
    for r in H._rows(c.execute(f"SELECT id,job_id,created_at FROM attempts WHERE job_id IN ({job_marks}) ORDER BY created_at",
                               jobs)):
        tries.setdefault(r["job_id"], []).append(r)
    decisions, retries, lease = {}, {}, {}
    for e in c.execute(f"SELECT ts,actor,action,target,detail FROM events WHERE {RECOVERY_SQL} AND target IN "
                       f"({job_marks},{marks}) ORDER BY ts", (*jobs, *ids)):
        detail = json.loads(e["detail"] or "{}") or {}
        if e["action"] in DECISIONS and detail.get("attempt_id"):
            decisions[detail["attempt_id"]] = {
                "kind": "resumed" if detail.get("decision") == "resume" else "dismissed",
                "by": e["actor"], "at": e["ts"], "note": decision_note(e["actor"], detail)}
        elif e["action"] == "job.retry":
            retries.setdefault(e["target"], []).append({"by": e["actor"], "at": e["ts"]})
        elif e["action"] in LEASE:
            lease.setdefault(e["target"], []).append({"kind": LEASE[e["action"]], "at": e["ts"]})
    events = {aid: [] for aid in ids}
    for r in c.execute(f"SELECT attempt_id,kind,payload,created_at FROM attempt_events WHERE attempt_id IN ({marks}) "
                       "AND kind='tool' AND (json_extract(payload,'$.subagent') IS NOT NULL OR "
                       "json_extract(payload,'$.background') IS NOT NULL OR "
                       "json_extract(payload,'$.status')=?) ORDER BY attempt_id,seq", (*ids, TOOL_BACKGROUNDED)):
        if privacy.content_readable(c, actor, dict(r)):
            events[r["attempt_id"]].append((json.loads(r["payload"]), r["created_at"]))
    from .usage_records import ran_on
    made = ran_on(c, ids)
    saved = recovery(c, jobs)
    out = []
    for r in turns:
        job_tries = tries.get(r["job_id"], [])
        live = r["state"] in LIVE
        subagents, background = work(events[r["id"]], live)
        out.append({
            "run": r["id"], "bot": r["bot"], "job": r["job_id"],
            **run_diagnostics.metadata(c, r["id"]),
            "try": next((n for n, t in enumerate(job_tries, 1) if t["id"] == r["id"]), 1), "tries": len(job_tries),
            "state": r["state"], "outcome": r["exit"], "created_at": r["created_at"], "started_at": r["started_at"],
            "finished_at": r["finished_at"], "took_s": seconds(r["started_at"], r["finished_at"]), "ran_on": made.get(r["id"]),
            "lease_until": r["lease_until"] if live else None, "lease": lease.get(r["id"], []),
            "reason": failure_reason(r["state"], r["result"], run_diagnostics.metadata(c, r["id"])["end_reason"]),
            # Waiting for its ChatGPT login, or where it ran instead of its own harness and why (backend/login_waits.py).
            "waiting": login_waits.waiting(c, r["id"], r["bot"], who, auth) if live else None,
            "moved": login_waits.moved(c, r["id"], r["bot"], who, auth),
            # Stopped to let another turn have the shared login; its job continues next.
            "paused": login_waits.paused(c, r["id"], r["state"], r["result"]),
            "next": next_step(r, job_tries, job_rows.get(r["job_id"]) or {"state": None}, decisions,
                              retries.get(r["job_id"], [])),
            # The job's state now and its recovery decision when that decision was about this try (job_recovery); the
            # decision's own `state` is set only while it is about the job's current try.
            "job_state": (job_rows.get(r["job_id"]) or {}).get("state"),
            "decision": decision if (decision := saved.get(r["job_id"])) and decision["attempt_id"] == r["id"] else None,
            "subagents": subagents, "background": background,
            # What woke the turn and the wake preset in force (backend/wake_presets.py record); None before presets.
            "wake": H._json(r["wake"])})
    return {"task_id": tid, "checkpoint": checkpoint, "attempts": out}


def install(app, store, auth):
    @app.get("/api/v2/tasks/{tid}/run-view")
    def task_run_view(request: Request, tid: str):
        """The task's attempts, subagents, background work and checkpoint. Readable by whoever can read the task;
        an attempt the reader cannot read on its own (it carried a private task they cannot see) is left out."""
        who = request.state.identity
        with store.read() as c:
            tid = auth.resolve_task(c, who, tid)
            auth.task(c, who, tid)
            return run_view(c, auth, who, tid)
