"""The engineering manager's one exception list, read from factory records.

This is a view over task, run, pool and pull-request state. It makes no routing or notification decisions; each live
cause is emitted once for its ticket and cause, with the first time that cause appeared.
"""
import re

from . import task_privacy as privacy
from .store import H

NO_JOB_AFTER_SECONDS = 45 * 60
MAX_EXCEPTIONS = 500
CHECK_TITLE = re.compile(r"^(?:Review [12]|QA|Post Release QA|Security)(?:\b|:)", re.I)


def _event_times(c, ticket_ids):
    if not ticket_ids:
        return {}
    marks = ",".join("?" * len(ticket_ids))
    out = {}
    for row in c.execute(
            "SELECT task_id, max(ts) AS entered FROM task_events e JOIN tasks t ON t.id=e.task_id "
            f"WHERE e.task_id IN ({marks}) AND e.field='step' AND e.new=t.step_id GROUP BY e.task_id", ticket_ids):
        out[row["task_id"]] = row["entered"]
    return out


def _ticket_ids_for_messages(c, messages, child_to_ticket):
    found = set()
    for message in messages:
        try:
            ids = privacy.message_tasks(c, dict(message), include_run=False, current=True)
        except Exception:
            continue
        for task_id in ids:
            found.add(child_to_ticket.get(task_id, task_id))
    return found


def _working_jobs(c, ticket_ids, child_to_ticket):
    """Tickets with queued or live work, including a check task filed below the ticket."""
    if not ticket_ids:
        return set()
    related = ticket_ids | set(child_to_ticket)
    marks = ",".join("?" * len(related))
    messages = c.execute(
        "SELECT m.* FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE j.state IN ('queued','uncertain') AND (cv.task_id IN (" + marks + ") OR "
        "json_extract(m.refs_json,'$.task') IN (" + marks + ")) UNION "
        "SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
        "JOIN conversations cv ON cv.id=m.conversation_id WHERE a.state IN ('leased','running') AND "
        "(cv.task_id IN (" + marks + ") OR json_extract(m.refs_json,'$.task') IN (" + marks + "))",
        (*related, *related, *related, *related)).fetchall()
    return _ticket_ids_for_messages(c, messages, child_to_ticket) & ticket_ids


def _runs(c, who, ticket_ids, child_to_ticket, add, now):
    """A current failed/cut run is one cause per ticket; a later successful run clears that episode."""
    if not ticket_ids:
        return
    actor = privacy.actor(who)
    related = ticket_ids | set(child_to_ticket)
    marks = ",".join("?" * len(related))
    bots = {row[0][4:] for row in c.execute(
        "SELECT owner FROM tasks WHERE id IN (" + marks + ")", list(related))
        if isinstance(row[0], str) and row[0].startswith("bot:")}
    if not bots:
        return
    bot_marks = ",".join("?" * len(bots))
    since = H.shift(now, days=-7)
    rows = c.execute(
        "SELECT a.id,a.job_id,a.bot,a.state,a.created,a.started,a.finished,a.thread_id,a.result_json,a.stop_requested,"
        "d.end_reason FROM attempts a LEFT JOIN attempt_diagnostics d ON d.attempt_id=a.id "
        f"WHERE a.state IN ('failed','interrupted','expired') AND a.bot IN ({bot_marks}) "
        "AND coalesce(a.finished,a.created)>=? ORDER BY coalesce(a.finished,a.created),a.id LIMIT 5001",
        (*sorted(bots), since)).fetchall()
    if not rows:
        return
    privacy.prime_attempts(c, [r["id"] for r in rows])
    from .pipeline_stats import cause_of
    timeline = []
    for row in rows:
        try:
            if not privacy.attempt_readable(c, actor, row["id"]):
                continue
            affected = privacy.attempt_tasks(c, row["id"]) & ticket_ids
        except Exception:
            continue
        for ticket_id in affected:
            kind = "run was cut" if row["stop_requested"] or row["state"] == "interrupted" else "run failed"
            cause = cause_of(row)
            when = row["finished"] or row["created"]
            timeline.append((when, row["id"], ticket_id, (ticket_id, kind, cause)))
    # A later completed attempt on the same ticket resolves earlier run failures.
    successes = c.execute(
        f"SELECT id,created,started,finished FROM attempts WHERE state='completed' AND bot IN ({bot_marks}) "
        "AND coalesce(finished,created)>=? ORDER BY coalesce(finished,created),id LIMIT 5001",
        (*sorted(bots), since)).fetchall()
    for attempt in successes:
        try:
            affected = privacy.attempt_tasks(c, attempt["id"]) & ticket_ids
        except Exception:
            continue
        when = attempt["finished"] or attempt["created"]
        for ticket_id in affected:
            timeline.append((when, attempt["id"], ticket_id, None))
    failure_by_ticket = {}
    for when, _attempt_id, ticket_id, key in sorted(timeline):
        if key is None:
            for old in [item for item in failure_by_ticket if item[0] == ticket_id]:
                del failure_by_ticket[old]
        else:
            failure_by_ticket.setdefault(key, when)
    for (ticket_id, kind, cause), when in failure_by_ticket.items():
        add(ticket_id, f"{kind}: {cause}", when)


def _pool_exceptions(c, active, add):
    """Consume the review pool wait rows written by check_pools; no wait table means no pool finding."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='check_waits'").fetchone():
        return
    for wait in c.execute("SELECT w.ticket_id,w.step_id,w.reason,w.created,s.name AS step_name FROM check_waits w "
                          "LEFT JOIN task_steps s ON s.id=w.step_id WHERE w.state='waiting' ORDER BY w.created,w.id"):
        if wait["ticket_id"] not in active:
            continue
        reason = str(wait["reason"] or "").strip()
        if "pool" not in reason.lower() and "reviewer" not in reason.lower() and "builder" not in reason.lower():
            continue
        pool = "builder" if str(wait["step_name"] or "").casefold() == "on deck" else "review"
        add(wait["ticket_id"], f"empty {pool} pool: " + (reason or "no member is available"), wait["created"])


def _refusals(c, rows, add):
    """A repeated gate refusal is read from the existing move_retry event history."""
    active = {r["id"]: r for r in rows}
    grouped = {}
    for event in c.execute(
            "SELECT task_id,ts,old,new FROM task_events WHERE field='move_retry' ORDER BY ts,rowid"):
        ticket = active.get(event["task_id"])
        if not ticket or event["old"] != ticket.get("step_id"):
            continue
        detail = H._json(event["new"], {}) or {}
        cause = str(detail.get("problem_code") or detail.get("kind") or
                    ("GitHub unavailable" if detail.get("outage") else "review gate refused the move"))
        key = (event["task_id"], cause, str(detail.get("head") or ""), str(event["old"] or ""))
        grouped.setdefault(key, []).append(event["ts"])
    for (ticket_id, cause, _head, _step), times in grouped.items():
        if len(times) >= 2:
            add(ticket_id, "refusal repeated twice", min(times))

    # Refusals that carry a task id are also part of the shared refusal ledger. Keep the count keyed by the rule,
    # which avoids merging unrelated causes on one ticket.
    counted = {}
    for event in c.execute("SELECT ts,rule,detail_json FROM refusals ORDER BY ts,id"):
        detail = H._json(event["detail_json"], {}) or {}
        ticket_id = detail.get("task")
        if ticket_id not in active:
            continue
        key = (ticket_id, str(event["rule"] or "refused write"))
        counted.setdefault(key, []).append(event["ts"])
    for (ticket_id, rule), times in counted.items():
        if len(times) >= 2:
            add(ticket_id, "refusal repeated twice", min(times))


def report(c, auth, who, typ, rows=None, now=None):
    """Return the current exceptions visible to the engineering type's manager.

    The same visibility-filtered ticket rows used by Pipeline are passed in when available. This keeps private work
    out of the manager's report unless the normal task authorization lets them read it.
    """
    if not typ or typ.get("role") != "engineering":
        return []
    manager = H.type_manager(c, {"type_id": typ["id"]})
    if not manager or privacy.actor(who).actor != "bot:" + manager:
        return []
    now = now or H.now()
    if rows is None:
        rows = H.tasks(c, status=H.ACTIVE_STATUSES, lane="company", type_id=typ["id"],
                       limit=5000, visible=auth.task_sql(c, who), order="step")
    rows = [dict(r) for r in rows]
    by_id = {r["id"]: r for r in rows}
    ids = set(by_id)
    if not ids:
        return []
    # Include an active check task's run when it is the current work for the ticket.
    child_to_ticket = {}
    marks = ",".join("?" * len(ids))
    for child in c.execute(
            "SELECT t.id,t.title,r.to_task FROM task_relations r JOIN tasks t ON t.id=r.from_task "
            f"WHERE r.kind='parent' AND r.to_task IN ({marks}) AND t.status IN "
            "('open','doing','waiting','review','ready')", list(ids)):
        if CHECK_TITLE.match(str(child["title"] or "").strip()):
            child_to_ticket[child["id"]] = child["to_task"]

    items = {}

    def add(ticket_id, cause, since, *, pr=None, head=None):
        task = by_id.get(ticket_id)
        if not task or not since:
            return
        key = (ticket_id, cause)
        current = items.get(key)
        when = str(since)
        step = c.execute("SELECT name FROM task_steps WHERE id=?", (task.get("step_id") or "",)).fetchone()
        if current:
            current["since"] = min(current["since"], when)
            if pr is not None:
                current.update({"pr": pr, "head": head})
            return
        items[key] = {"ticket": {"id": task["id"], "number": task.get("number"), "title": task.get("title"),
                                 "step": step[0] if step else None},
                      "cause": str(cause), "since": when,
                      **({"pr": pr, "head": head} if pr is not None else {})}

    # A ticket can wait with no job while it sits at any step; a queued, uncertain or running job counts as work.
    busy = _working_jobs(c, ids, child_to_ticket)
    entered = _event_times(c, list(ids))
    for task in rows:
        since = entered.get(task["id"]) or task.get("created")
        when = H.parse_ts(since)
        if task["id"] not in busy and when and (H.parse_ts(now) - when).total_seconds() >= NO_JOB_AFTER_SECONDS:
            add(task["id"], "no job for 45 minutes", H.shift(since, seconds=NO_JOB_AFTER_SECONDS))

    _runs(c, who, ids, child_to_ticket, add, now)
    _pool_exceptions(c, ids, add)
    _refusals(c, rows, add)

    # GitHub's current mergeability is the persisted result of a failed/conflicted merge. A new head or a clean
    # merge removes the exception on the next webhook refresh.
    for task in rows:
        for link in c.execute("SELECT number,pr_sha,detail_json,mergeable,updated,created,state FROM task_links "
                              "WHERE task_id=? AND kind='pr' AND state NOT IN ('merged','shipped','closed') "
                              "ORDER BY updated,created",
                              (task["id"],)):
            if link["mergeable"] != "conflict":
                continue
            detail = H._json(link["detail_json"], {}) or {}
            head = detail.get("head_sha") or link["pr_sha"] or ""
            if not link["number"] or not head:
                continue
            add(task["id"], "failed merge", link["updated"] or link["created"],
                pr=link["number"], head=head)

    ordered = sorted(items.values(), key=lambda row: (row["since"], row["ticket"].get("number") or 0,
                                                      row["cause"], row.get("pr") or 0))
    return ordered[:MAX_EXCEPTIONS]
