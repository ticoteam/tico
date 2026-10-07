"""Current task participants fence derived reads, independently of room or bot grants.

Structured provenance is checked again on each request. This cannot retract a download
or identify private information copied into unrelated, untagged prose.
"""
import re

from .store import H, Problem


def snapshot(c):
    if not getattr(c, "in_transaction", True):
        c.execute("BEGIN")
        return True
    return False


def actor(who):
    other = getattr(who, "task_actor", "")
    return (who.actor, other) if other and other != who.actor else who.actor


def task_readable(c, who, row):
    if snapshot(c) and row:
        row = H.task(c, row["id"])
    principals = actor(who)
    return all(H.task_private_readable(c, a, row) for a in
               (principals if isinstance(principals, tuple) else (principals,)))


def reference_strings(value):
    return {value[5:] if value.startswith("task:") else value, *re.findall(
        r"(?:#/task/|/tasks/)([a-zA-Z0-9_-]+)", value)}


def _candidates(value, out):
    if isinstance(value, str):
        if value[:1] in ("{", "["):
            parsed = H._json(value, None)
            if isinstance(parsed, (dict, list)):
                return _candidates(parsed, out)
        out.update(reference_strings(value))
    elif isinstance(value, dict):
        for item in value.values():
            _candidates(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _candidates(item, out)
    return out


def references(c, value):
    """Task ids anywhere in a structured reference or payload, including nested lists. Every string that could
    name a task is gathered first and looked up in one query: one query per string was most of a busy server's SQL."""
    snapshot(c)
    names = [name for name in _candidates(value, set()) if name]
    found = set()
    for start in range(0, len(names), 500):
        chunk = names[start:start + 500]
        found.update(row[0] for row in c.execute(
            "SELECT id FROM tasks WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk))
    return found


def message_tasks(c, message, seen=None, include_run=True, current=False):
    """`current`: `message` is its row as stored, read in this snapshot, so it is not read again."""
    snapshot(c)
    if not message:
        return set()
    message = dict(message)
    seen = set() if seen is None else seen
    mid = message.get("id")
    if mid in seen:
        return set()
    if len(seen) >= 100:
        # An unbounded reply chain is an unsupported provenance path.
        raise Problem("privacy", "Message provenance is too deep to authorize", 403)
    seen.add(mid)
    if mid and not current:
        message = H.message(c, mid, include_deleted=True) or message
    conv = H.conversation(c, message["conversation_id"]) if message.get("conversation_id") else None
    refs = message.get("refs") or H._json(message.get("refs_json"), {}) or {}
    if not isinstance(refs, dict):
        raise Problem("privacy", "Message provenance cannot be authorized", 403)
    ids = references(c, refs)
    for key in ("answers", "inputs"):
        for mid in refs.get(key, []) if isinstance(refs.get(key), list) else []:
            if isinstance(mid, str):
                ids.update(message_tasks(c, H.message(c, mid, include_deleted=True), seen, include_run=False))
    tid = H.message_task_id(message, conv)
    if tid:
        ids.add(tid)
    if message.get("in_reply_to"):
        ids.update(message_tasks(c, H.message(c, message["in_reply_to"], include_deleted=True), seen, include_run=False))
    run = refs.get("run")
    run_id = refs.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None)
    if include_run and run_id:
        ids.update(attempt_tasks(c, run_id))
    return ids


def attempt_tasks(c, aid, memo=None):
    """`memo`: a dict kept for one read transaction, where a run's tasks cannot change; many files of one
    bot share the run that made them, and a Files page asked this once per file version."""
    if memo is not None:
        key = ("attempt_tasks", aid)
        if key not in memo:
            memo[key] = frozenset(attempt_tasks(c, aid))
        return set(memo[key])
    snapshot(c)
    ids = set()
    for row in c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                         "JOIN messages m ON m.id=j.message_id WHERE a.id=? UNION "
                         "SELECT m.* FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
                         "WHERE i.attempt_id=?", (aid, aid)):
        ids.update(message_tasks(c, row, include_run=False))
    ids.update(r[0] for r in c.execute("SELECT id FROM tasks WHERE carried_by=?", (aid,)))
    for row in c.execute("SELECT detail_json FROM events WHERE action IN (" + RUN_TASK_EVENTS_SQL + ") AND target=?",
                         (aid,)):
        ids.update(references(c, H._json(row[0], {}) or {}))
    return ids


# Events whose target is a run and whose `tasks` the run took in: carried tasks, and the tasks
# of an input that a later run took over after this one had already read it.
INPUT_MOVED = "attempt.input.moved"
RUN_TASK_EVENTS_SQL = "'task.next-run.carried','" + INPUT_MOVED + "'"


def record_moved_input(c, from_attempt, to_attempt, message):
    """Before an input row moves to another run, keep its tasks against the run that read it."""
    H.event(c, H.KEEPER, INPUT_MOVED, from_attempt,
            {"message_id": message["id"], "to_attempt": to_attempt,
             "tasks": sorted(message_tasks(c, message, include_run=False))})


def readable(c, actor, ids):
    snapshot(c)
    principals = actor if isinstance(actor, tuple) else (actor,)
    for tid in ids:
        # The row alone decides: H.task's labels are not read, and it is read once for every principal.
        row = H._one(c, "SELECT * FROM tasks WHERE id=?", (tid,))
        if not all(H.task_private_readable(c, a, row) for a in principals):
            return False
    return True


def message_readable(c, actor, message, current=False):
    """`current`: `message` is its full messages row, read in this snapshot (a join's `m.*`)."""
    snapshot(c)
    if not message:
        return False
    message = dict(message)
    stored = message if current else H.message(c, message.get("id"), include_deleted=True)
    message = stored or message
    if message.get("deleted_at") or message.get("deleted"):
        return False
    try:
        return readable(c, actor, message_tasks(c, message, current=bool(stored)))
    except Problem:
        return False


def require_message(c, who, message):
    if not message_readable(c, actor(who), message):
        raise Problem("not_found", "Message not found", 404)
    if message.get("kind") == "ask" and (message.get("refs") or {}).get("questions"):
        message["answers"] = H.review_answers(c, message["id"], actor=actor(who))
    return message


def page(c, who, cid, *, before=None, since=None, limit=200, task_id=None):
    """Paginate visible rows, so hidden rows cannot leak cursors or counts."""
    snapshot(c)
    clauses, args = ["conversation_id=?"], [cid]
    if before:
        anchor = c.execute("SELECT rowid,* FROM messages WHERE id=? AND conversation_id=?", (before, cid)).fetchone()
        if not anchor or not message_readable(c, actor(who), anchor):
            raise Problem("cursor", "Message cursor is unavailable", 422)
        clauses.append("rowid<?")
        args.append(anchor["rowid"])
    if since:
        clauses.append("created>?")
        args.append(since)
    visible = []
    for row in c.execute("SELECT * FROM messages WHERE " + " AND ".join(clauses) + " ORDER BY rowid DESC", args):
        if not message_readable(c, actor(who), row):
            continue
        if task_id and task_id not in message_tasks(c, row):
            continue
        visible.append({**dict(row), "refs": H._json(row["refs_json"], {}) or {}})
        if len(visible) > limit:
            break
    more = len(visible) > limit
    messages = list(reversed(visible[:limit]))
    return {"messages": messages, "has_more": more,
            "next_before": messages[0]["id"] if more and messages else None}


def deleted_comment_ack(c, principal, payload):
    """Only the canonical, empty deletion receipt may survive message withdrawal."""
    current = H.message(c, payload.get("id"), include_deleted=True)
    if (not current or not current.get("deleted_at") or current.get("body") != ""
            or not H.is_comment(current) or set(current.get("refs", {})) != {"task", "comment"}
            or payload != current):
        return False
    return readable(c, principal, message_tasks(c, current))


def require_payload(c, who, payload, principal=None, checked_for=None):
    """Cached writes are reads too; a stored response cannot restore revoked access.

    `checked_for` is the principal an enclosing call already checked every task this value names for:
    a nested value names only tasks its container names, so that check is not run again for it (it was
    one query per nested dict). Everything else is still checked at every level."""
    principal = principal or actor(who)
    if who.role == "runner" and isinstance(payload, dict) and principal == who.actor:
        attempt = payload.get("attempt")
        aid = payload.get("attempt_id") or (attempt.get("id") if isinstance(attempt, dict) else None)
        if aid:
            hosted = c.execute("SELECT a.bot FROM attempts a JOIN assignments x ON x.bot=a.bot "
                               "WHERE a.id=? AND a.runner_id=? AND x.runner_id=?",
                               (aid, who.runner_id, who.runner_id)).fetchone()
            if not hosted:
                raise Problem("privacy", "This execution is no longer assigned to this computer", 403)
            principal = "bot:" + hosted["bot"]
        if isinstance(attempt, dict) and attempt.get("bot"):
            principal = "bot:" + attempt["bot"]
            if not attempt_readable(c, principal, attempt.get("id")):
                raise Problem("privacy", "This execution is no longer available", 403)
    if principal != checked_for and not readable(c, principal, references(c, payload)):
        raise Problem("privacy", "This response contains a task you can no longer read", 403)
    if isinstance(payload, dict):
        key = payload.get("key")
        if isinstance(key, str) and key.startswith("report:") and not message_readable(c, principal, H.message(c, key[7:])):
            raise Problem("privacy", "This report is no longer available", 403)
        if isinstance(key, str) and key.startswith("approval:"):
            approval = H.approval(c, key[9:])
            if not approval or not message_readable(c, principal, H.message(c, approval["message_id"])):
                raise Problem("privacy", "This approval is no longer available", 403)
        for key in ("items_json", "responses_json", "refs_json"):
            if isinstance(payload.get(key), str):
                require_payload(c, who, H._json(payload[key], {}) or {}, principal)
        if payload.get("attempt_id") and not attempt_readable(c, principal, payload["attempt_id"]):
            raise Problem("privacy", "This execution is no longer available", 403)
        if payload.get("conversation_id") and payload.get("id"):
            if not message_readable(c, principal, payload) and not deleted_comment_ack(c, principal, payload):
                raise Problem("privacy", "This message is no longer available", 403)
        if payload.get("kind") == "ask" and payload.get("id") and isinstance(payload.get("answers"), list):
            # Answer projections have no message id; recheck their source before replaying a receipt.
            visible_answers = H.review_answers(c, payload["id"], actor=principal)
            payload["answers"] = [answer for answer in payload["answers"] if answer in visible_answers]
        for value in payload.values():
            if isinstance(value, (dict, list)):
                require_payload(c, who, value, principal, principal)
    elif isinstance(payload, list):
        for value in payload:
            if isinstance(value, (dict, list)):
                require_payload(c, who, value, principal, principal)
    return payload


def attempt_readable(c, actor, aid, memo=None):
    try:
        return readable(c, actor, attempt_tasks(c, aid, memo))
    except Problem:
        return False


def require_attempt(c, who, aid):
    if not attempt_readable(c, actor(who), aid):
        raise Problem("not_found", "Turn not found", 404)


def private_execution(c, who):
    return bool(who.attempt_id and any(H.task_private(c, H.task(c, tid))
                                     for tid in attempt_tasks(c, who.attempt_id)))


def public_message(c, message):
    """External channel audiences have no task participant authority."""
    if not message or dict(message).get("deleted_at") or dict(message).get("deleted"):
        return False
    try:
        return not any(H.task_private(c, H.task(c, tid)) for tid in message_tasks(c, message))
    except Problem:
        return False


def require_batch(c, who, row):
    if row:
        row = dict(row)
        require_payload(c, who, H._json(row["items_json"], []) or [])
        if row.get("message_id"):
            require_message(c, who, H.message(c, row["message_id"]))


def content_readable(c, actor, payload):
    return readable(c, actor, references(c, payload))


def guard_write(c, who, path, auth):
    """A private execution has no automatic company-publication capability."""
    if (who.role != "bot" and not getattr(who, "task_actor", "")) or not private_execution(c, who):
        return
    if path in ("/api/v2/sql", "/api/v2/mcp", "/api/v2/tasks/dry-run"):
        return
    if path.startswith(("/api/v2/attempts/", "/api/v2/jobs/", "/api/v2/files/", "/api/v2/messages",
                        "/api/v2/conversations/", "/api/v2/chat/", "/api/v2/tasks")):
        task_path = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/.*)?", path)
        if task_path and task_path[1] not in ("dry-run", "labels", "stuck"):
            row = auth.task(c, who, auth.resolve_task(c, who, task_path[1]))
            if row and not H.task_private(c, row):
                raise Problem("privacy", "Private task work cannot write company-visible tasks", 403)
            if row:
                require_destination(c, who, row["owner"], row["conversation_id"], {"task": row["id"]})
        return
    if path.endswith(("/status", "/usage")) or path == "/api/v2/status":
        return
    raise Problem("privacy", "Private task work cannot publish into company-wide resources", 403)


def tag_readable(c, who, row):
    snapshot(c)
    linked = {r[0] for r in c.execute("SELECT task_id FROM task_tags WHERE tag_id=?", (row["id"],))}
    for event in c.execute("SELECT detail_json FROM events WHERE target=? AND action='tag.attach'", (row["id"],)):
        linked.update(references(c, H._json(event[0], {}) or {}))
    return (not linked or bool(row.get("is_template")) or
            any(task_readable(c, who, H.task(c, tid)) for tid in linked)) and content_readable(c, actor(who), row)


def require_destination(c, who, to, conversation_id, refs, reply=None):
    ids = references(c, refs)
    ids.update(message_tasks(c, reply) if reply else ())
    if who.attempt_id:
        ids.update(attempt_tasks(c, who.attempt_id))
    private = [H.task(c, tid) for tid in ids if H.task_private(c, H.task(c, tid))]
    if not private:
        return
    conv = H.conversation(c, conversation_id) if conversation_id else None
    audience = set((conv or {}).get("participants") or []) | {who.actor, to}
    if getattr(who, "task_actor", ""):
        audience.add(who.task_actor)
    for row in private:
        if not all(H.task_private_readable(c, actor, row) for actor in audience):
            raise Problem("privacy", "Private task content stays with its requester and current owner", 403)
    if (conv or {}).get("scope") == "shared":
        raise Problem("privacy", "Discuss private tasks in their task conversation", 403)


# A bot's unanswered questions to people, and its undecided approvals: `status` counts the readable ones.
# `{}` is `=?` for one bot or `IN (...)` for many (`status_inputs`).
ASKS_SQL = (f"SELECT m.*, {H.MESSAGE_TASK_SQL} AS about FROM messages m "
            "JOIN conversations cv ON cv.id=m.conversation_id "
            "WHERE m.kind='ask' AND m.from_actor{} AND m.to_actor LIKE 'human:%' "
            f"AND m.deleted_at IS NULL AND {H.OPEN_ASK_SQL}")
APPROVALS_SQL = ("SELECT m.*, a.task_id AS approval_task{by} FROM approvals a JOIN messages m ON m.id=a.message_id "
                 "WHERE a.requested_by{match} AND a.decision IS NULL")


def status_inputs(c, bots):
    """What `status` reads for each of many bots, one query per kind per 500 bots:
    {slug: {"tasks": [...], "approvals": [...], "asks": [...]}}, to pass to `status` as keywords."""
    snapshot(c)
    out = {bot: {"tasks": [], "approvals": [], "asks": []} for bot in bots}
    for part in H.chunks(out):
        actors = ["bot:" + bot for bot in part]
        marks = " IN (" + ",".join("?" * len(actors)) + ")"
        for t in c.execute(f"SELECT * FROM tasks WHERE owner{marks} OR requester{marks}", actors * 2):
            t = dict(t)
            for party in {t["owner"], t["requester"]}:
                if party in actors:
                    out[H.actor_id(party)]["tasks"].append(t)
        # Who asked is read alongside, to sort the rows, and left out of the rows `status` is given.
        for m in c.execute(APPROVALS_SQL.format(by=", a.requested_by AS by_", match=marks), actors):
            out[H.actor_id(m["by_"])]["approvals"].append({k: m[k] for k in m.keys() if k != "by_"})
        for m in c.execute(ASKS_SQL.format(marks), actors):
            out[H.actor_id(m["from_actor"])]["asks"].append(m)
    return out


def status(c, who, row, *, tasks=None, approvals=None, asks=None):
    """Free-form focus/results can contain task content; hide it when provenance is lost."""
    snapshot(c)
    if not row:
        return row
    row = dict(row)
    tid = row.get("task_id")
    tasks = tasks if tasks is not None else [dict(t) for t in c.execute(
        "SELECT * FROM tasks WHERE owner=? OR requester=?", ("bot:" + row["bot"], "bot:" + row["bot"]))]
    task = next((t for t in tasks if t["id"] == tid), None) if tid else None
    if tid and task is None:
        task = H.task(c, tid)
    private = [task] if task and H.task_private(c, task) else []
    if not tid:
        private = [t for t in tasks if H.task_private(c, t)]
    if any(not task_readable(c, who, t) for t in private):
        row.update(task_id=None, focus="", last_result="", reason="")
    tasks = [t for t in tasks if task_readable(c, who, t)]
    if "open_tasks" in row:
        row["open_tasks"] = sum(t["owner"] == "bot:" + row["bot"] and t["status"] in H.ACTIVE_STATUSES for t in tasks)
    if "needs_human" in row:
        # One per readable task that waits on a person (hubdb._recount): filed for a person, set
        # waiting on one, or carrying the bot's unanswered question to one.
        bot = "bot:" + row["bot"]
        live = {t["id"]: t for t in tasks if t["status"] in H.ACTIVE_STATUSES}
        waits = {i for i, t in live.items()
                 if (t["requester"] == bot and H.is_human(t["owner"]))
                 or (t["owner"] == bot and t["status"] == "waiting" and H.is_human(t.get("waiting_on") or ""))}
        if len(waits) < len(live):
            for m in asks if asks is not None else c.execute(ASKS_SQL.format("=?"), (bot,)):
                if m["about"] in live and m["about"] not in waits and message_readable(c, actor(who), m):
                    waits.add(m["about"])
        approvals = approvals if approvals is not None else c.execute(APPROVALS_SQL.format(by="", match="=?"), ("bot:" + row["bot"],))
        # An approval on a task already counted is the same wait, counted once (hubdb.status_counts).
        row["needs_human"] = len(waits) + sum(
            message_readable(c, actor(who), m) and not (dict(m).get("approval_task") in waits)
            for m in approvals)
    return row


def job_count(c, who, bot, states=("queued",)):
    snapshot(c)
    marks = ",".join("?" * len(states))
    return sum(message_readable(c, actor(who), row) and
               (not row["attempt_id"] or attempt_readable(c, actor(who), row["attempt_id"]))
               for row in c.execute("SELECT m.*,j.attempt_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                                    f"WHERE j.bot=? AND j.state IN ({marks})", (bot, *states)))


def job_counts(c, who, bots, states=("queued",)):
    """`job_count` for many bots, reading their jobs in one query per 500 bots: {slug: count}.
    Each job is still checked on its own."""
    snapshot(c)
    out = dict.fromkeys(bots, 0)
    marks = ",".join("?" * len(states))
    for part in H.chunks(out):
        for row in c.execute("SELECT m.*,j.attempt_id,j.bot AS job_bot FROM jobs j JOIN messages m ON m.id=j.message_id "
                             f"WHERE j.bot IN ({','.join('?' * len(part))}) AND j.state IN ({marks})", (*part, *states)):
            out[row["job_bot"]] += bool(message_readable(c, actor(who), row, current=True) and
                                        (not row["attempt_id"] or attempt_readable(c, actor(who), row["attempt_id"])))
    return out


def blob_readable(c, actor, bid, seen=None, memo=None):
    """A task attachment cannot regain access through uploader ownership or another link.
    `memo` is attempt_tasks' cache for one read transaction."""
    snapshot(c)
    seen = set() if seen is None else seen
    if bid in seen:
        return True
    if len(seen) >= 100:
        return False
    seen.add(bid)
    for parent in c.execute("SELECT blob_id FROM blob_media WHERE poster_blob_id=? OR thumb_blob_id=?", (bid, bid)):
        if not blob_readable(c, actor, parent[0], seen, memo):
            return False
    ids = {r[0] for r in c.execute("SELECT task_id FROM task_assets WHERE blob_id=?", (bid,))}
    for row in c.execute("SELECT f.task_id,f.scope,v.attempt_id FROM bot_files f "
                         "JOIN bot_file_versions v ON v.file_id=f.id WHERE v.blob_id=? "
                         "OR v.poster_blob_id=? OR v.thumb_blob_id=?", (bid, bid, bid)):
        if row["task_id"]:
            ids.add(row["task_id"])
        if row["scope"].startswith("task:"):
            ids.add(row["scope"][5:])
        if row["attempt_id"]:
            ids.update(attempt_tasks(c, row["attempt_id"], memo))
    for row in c.execute("SELECT m.* FROM message_assets a JOIN messages m ON m.id=a.message_id "
                         "WHERE a.blob_id=?", (bid,)):
        if not message_readable(c, actor, row):
            return False
    return readable(c, actor, ids)


def event_readable(c, actor, event):
    event = dict(event)
    ids = references(c, H._json(event.get("detail_json"), {}) or {}) | references(c, event.get("target"))
    target = event.get("target")
    if H.task(c, target):
        ids.add(target)
    msg = H.message(c, target)
    if msg and not message_readable(c, actor, msg):
        return False
    if c.execute("SELECT 1 FROM attempts WHERE id=?", (target,)).fetchone():
        ids.update(attempt_tasks(c, target))
    # Tool/audit events from a private execution often have no explicit task ref.
    if H.is_bot(event.get("actor")):
        for row in c.execute("SELECT id FROM attempts WHERE bot=? AND created<=? "
                             "AND coalesce(finished,?)>=?", (H.actor_id(event["actor"]),
                                                            event["ts"], H.now(), event["ts"])):
            ids.update(attempt_tasks(c, row[0]))
    return readable(c, actor, ids)
