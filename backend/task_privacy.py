"""Current task participants fence derived reads, independently of room or bot grants.

Structured provenance is checked again on each request. This cannot retract a download
or identify private information copied into unrelated, untagged prose.
"""
import json
import re

from .store import H, Problem
from . import read_cache
from .live_privacy import sources as meeting_sources


def _task(c, tid):
    # Authorization uses task identity and parties; display tags are unrelated to access.
    return H._one(c, "SELECT * FROM tasks WHERE id=?", (tid,))


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
        row = _task(c, row["id"])
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


def _task_rows(c, ids):
    """{id: the parties row of that task, or None when no task has that id}, each id read at most once per snapshot.
    A task page checks every comment, event and run against the same few tasks; reading them per row was thousands
    of queries on a long task. The rows live in read_cache, so any write in the snapshot discards them."""
    snapshot(c)
    values = read_cache.cache(c)
    known = values.setdefault(("task_privacy_rows",), {}) if values is not None else {}
    missing = sorted({tid for tid in ids if tid not in known})
    for start in range(0, len(missing), 500):
        chunk = missing[start:start + 500]
        found = {row[0]: row for row in c.execute(
            "SELECT id,private,owner_id,requester_id,guest FROM tasks WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk)}
        for tid in chunk:
            known[tid] = found.get(tid)
    return {tid: known[tid] for tid in ids}


def private_tasks(c, ids):
    """Those of `ids` that H.task_private holds private (a private task, or no task by that id), read together."""
    return {tid for tid, row in _task_rows(c, ids).items() if H.task_private(c, row)}


def references(c, value):
    """Task ids anywhere in a structured reference or payload, including nested lists. Every string that could
    name a task is gathered first and looked up in one query: one query per string was most of a busy server's SQL."""
    names = {name for name in _candidates(value, set()) if name}
    return {tid for tid, row in _task_rows(c, names).items() if row is not None}


def prime_references(c, values):
    """Look up every task id the given payloads could name in one pass, so the per-row checks that follow
    (content_readable, message_readable) read them from this snapshot instead of one query each."""
    names = set()
    for value in values:
        _candidates(value, names)
    names.discard("")
    _task_rows(c, names)


def message_tasks(c, message, seen=None, include_run=True, current=False):
    snapshot(c)
    if not message or seen is not None:
        return _message_tasks(c, message, seen, include_run, current)
    from . import provenance_clock
    row = dict(message)
    if row.get("id") and not current:
        row = H.message(c, row["id"], include_deleted=True) or row
    refs = H._json(row.get("refs"), {}) or {}
    run = refs.get("run") if isinstance(refs, dict) else None
    has_run = isinstance(refs, dict) and (refs.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None))
    key = ("message_tasks", row.get("id"), bool(include_run and has_run), row.get("conversation_id"),
           json.dumps(refs, sort_keys=True), row.get("in_reply_to"))
    return provenance_clock.cached(c, key, lambda: _message_tasks(c, row, None, include_run, True))


def _message_tasks(c, message, seen=None, include_run=True, current=False):
    """`current`: `message` is its row as stored, read in this snapshot, so it is not read again."""
    snapshot(c)
    if not message:
        return set()
    message = dict(message)
    root = seen is None
    seen = set() if root else seen
    mid = message.get("id")
    if mid in seen:
        return set()
    if len(seen) >= 100:
        # An unbounded reply chain is an unsupported provenance path.
        raise Problem("privacy", "Message provenance is too deep to authorize", 403)
    if mid and not current:
        message = H.message(c, mid, include_deleted=True) or message
    # Only complete root traversals are reusable. A nested traversal can skip ancestors
    # already visited by its caller, so caching that partial result would lose provenance.
    values = read_cache.cache(c)
    refs = H._json(message.get("refs"), {}) or {}
    run = refs.get("run") if isinstance(refs, dict) else None
    has_run = isinstance(refs, dict) and (refs.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None))
    cache_key = ("message_tasks", mid, bool(include_run and has_run), message.get("conversation_id"),
           json.dumps(refs, sort_keys=True),
           message.get("in_reply_to"))
    cached = values.get(cache_key) if values is not None else None
    if cached is not None:
        if len(seen | cached[1]) > 100:
            raise Problem("privacy", "Message provenance is too deep to authorize", 403)
        seen.update(cached[1])
        return set(cached[0])
    seen.add(mid)
    conv = H.conversation(c, message["conversation_id"]) if message.get("conversation_id") else None
    refs = H._json(message.get("refs"), {}) or {}
    if not isinstance(refs, dict):
        raise Problem("privacy", "Message provenance cannot be authorized", 403)
    ids = references(c, refs)
    for key in ("answers", "inputs"):
        for mid in refs.get(key, []) if isinstance(refs.get(key), list) else []:
            if isinstance(mid, str):
                ids.update(message_tasks(c, H.message(c, mid, include_deleted=True), seen, include_run=False))
    # `refs` as parsed here: a row as stored has only `refs`, and its `refs.task` still decides.
    tid = H.message_task_id({**message, "refs": refs}, conv)
    if tid:
        ids.add(tid)
    if message.get("in_reply_to"):
        ids.update(message_tasks(c, H.message(c, message["in_reply_to"], include_deleted=True), seen, include_run=False))
    run = refs.get("run")
    run_id = refs.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None)
    if include_run and run_id:
        ids.update(attempt_tasks(c, run_id))
    if root and values is not None:
        values[cache_key] = (frozenset(ids), frozenset(seen))
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
    from . import provenance_clock
    return read_cache.value(c, ("attempt_tasks", aid), lambda: provenance_clock.cached(
        c, ("attempt_tasks", aid), lambda: _attempt_tasks(c, aid)))


def _attempt_tasks(c, aid):
    ids = set()
    rows = c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                     "JOIN messages m ON m.id=j.message_id WHERE a.id=? UNION "
                     "SELECT m.* FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
                     "WHERE i.attempt_id=?", (aid, aid)).fetchall()
    _prime_inputs(c, rows)
    for row in rows:
        ids.update(message_tasks(c, row, include_run=False, current=True))
    ids.update(r[0] for r in c.execute("SELECT id FROM tasks WHERE carried_by=?", (aid,)))
    for row in c.execute("SELECT detail FROM events WHERE action IN (" + RUN_TASK_EVENTS_SQL + ") AND target=?",
                         (aid,)):
        ids.update(references(c, H._json(row[0], {}) or {}))
    return ids


def carried_tasks(c, aid):
    """The tasks a run carries the text of: attempt_tasks without the ids an assignment notice names only in its
    `reconcile` list (the requester's other open tasks with this bot: id, title and status, never their bodies)."""
    snapshot(c)
    ids = set()
    rows = c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                     "JOIN messages m ON m.id=j.message_id WHERE a.id=? UNION "
                     "SELECT m.* FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
                     "WHERE i.attempt_id=?", (aid, aid)).fetchall()
    for row in rows:
        row = dict(row)
        refs = H._json(row.get("refs"), {}) or {}
        if isinstance(refs, dict) and "reconcile" in refs:
            refs = {key: value for key, value in refs.items() if key != "reconcile"}
            row = {**row, "refs": refs}
        ids.update(_message_tasks(c, row, include_run=False, current=True))
    ids.update(r[0] for r in c.execute("SELECT id FROM tasks WHERE carried_by=?", (aid,)))
    for row in c.execute("SELECT detail FROM events WHERE action IN (" + RUN_TASK_EVENTS_SQL + ") AND target=?",
                         (aid,)):
        ids.update(references(c, H._json(row[0], {}) or {}))
    return ids


def prime_attempts(c, aids):
    """Read the provenance of many runs in a fixed number of queries, for a page that then checks each run
    (a task's comments and runs). The results are the ones attempt_tasks and the meeting index would build one
    run at a time, kept for this snapshot; a run that cannot be authorized is left for its own check to refuse."""
    snapshot(c)
    values = read_cache.cache(c)
    if values is None:
        return
    from . import provenance_clock
    meetings = meeting_sources(c)
    wanted = sorted({aid for aid in aids if isinstance(aid, str) and aid}
                    - {key[1] for key in values if isinstance(key, tuple) and key[:1] == ("attempt_tasks",)})
    for start in range(0, len(wanted), 250):
        chunk = wanted[start:start + 250]
        marks = ",".join("?" * len(chunk))
        inputs = {aid: [] for aid in chunk}
        for row in c.execute("SELECT a.id AS primed_attempt,m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                             f"JOIN messages m ON m.id=j.message_id WHERE a.id IN ({marks}) UNION "
                             "SELECT i.attempt_id AS primed_attempt,m.* FROM attempt_inputs i "
                             f"JOIN messages m ON m.id=i.message_id WHERE i.attempt_id IN ({marks})", chunk * 2):
            row = dict(row)
            inputs[row.pop("primed_attempt")].append(row)
        _prime_inputs(c, [message for rows in inputs.values() for message in rows])
        carried = {aid: [] for aid in chunk}
        for row in c.execute(f"SELECT carried_by,id FROM tasks WHERE carried_by IN ({marks})", chunk):
            carried[row[0]].append(row[1])
        events = {aid: [] for aid in chunk}
        moved = {aid: [] for aid in chunk}
        for row in c.execute("SELECT target,action,detail FROM events WHERE action IN (" + RUN_TASK_EVENTS_SQL +
                             f") AND target IN ({marks})", chunk):
            events[row[0]].append(row[2])
            if row[1] == INPUT_MOVED:
                moved[row[0]].append(row[2])
        details = [H._json(detail, {}) or {} for rows in events.values() for detail in rows]
        prime_references(c, details)
        moved_ids = {detail.get("message_id") for detail in details
                     if isinstance(detail, dict) and isinstance(detail.get("message_id"), str)}
        _prime_inputs(c, H.prime_messages(c, moved_ids).values())
        meetings.prime_attempts(inputs, moved)
        tasks = set()
        for aid in chunk:
            try:
                ids = set(carried[aid])
                for message in inputs[aid]:
                    ids.update(message_tasks(c, message, include_run=False, current=True))
                for detail in events[aid]:
                    ids.update(references(c, H._json(detail, {}) or {}))
            except Problem:
                continue
            values[("attempt_tasks", aid)] = provenance_clock.cached(c, ("attempt_tasks", aid), lambda ids=ids: ids)
            tasks.update(ids)
        _task_rows(c, tasks)


def _parent_ids(message, refs):
    """The messages a provenance walk reads next from `message`: the one it replies to and those it answers."""
    parent = message.get("in_reply_to")
    out = {parent} if isinstance(parent, str) else set()
    for key in ("answers", "inputs"):
        if isinstance(refs, dict) and isinstance(refs.get(key), list):
            out.update(v for v in refs[key] if isinstance(v, str))
    return out


def _prime_inputs(c, rows):
    """A run's messages as stored (a join's `m.*` in this snapshot), with the messages they reply to and answer, the
    tasks their refs name and their rooms, read together. Every call that checks a run (each event batch, each input
    poll) rebuilds its provenance once other writes move the clock, and a long goal run has taken in many inputs."""
    rows = H.prime_message_rows(c, rows)
    prime_references(c, [H._json(row["refs"], {}) or {} for row in rows])
    H.prime_conversations(c, {row["conversation_id"] for row in rows})
    meeting_sources(c).prime_conversations({row["conversation_id"] for row in rows})


def prime_queued(c, messages):
    """Before a loop checks queued messages one at a time (a claim's candidates, a running turn's next inputs):
    `messages`, stored rows read in this snapshot, with what they reply to and answer, their rooms and meeting
    provenance, the tasks they are about and name, and the runs behind them, read together. Each message is still
    checked on its own by the same rules; a write in the loop discards all of it (read_cache)."""
    snapshot(c)
    messages = [dict(m) for m in messages]
    H.prime_message_rows(c, messages)
    prime_messages(c, messages)
    about = set()
    for m in messages:
        refs = H._json(m.get("refs"), {}) or {}
        about.add(H.message_task_id({"refs": refs if isinstance(refs, dict) else {}},
                                    H.conversation(c, m["conversation_id"]) if m.get("conversation_id") else None))
    H.prime_tasks(c, about)


def prime_messages(c, messages):
    """Before checking each of these stored messages: look up the tasks their refs name, the messages they reply to
    and answer, their rooms and meeting provenance, and the runs behind them together, so a long task's comments are
    not a few queries per comment."""
    messages = [dict(m) for m in messages]
    refs = [H._json(m.get("refs"), {}) or {} for m in messages]
    # The walks read each message's parents by id, and on a long task those are mostly the page's own messages,
    # given here as views: they are read as stored with the rest of their ancestry.
    parents = set().union(*(_parent_ids(m, r) for m, r in zip(messages, refs))) if messages else set()
    # Views and joins may carry extra columns. Only rows fetched from messages seed its stored-row cache.
    stored = H.prime_messages(c, parents | {m.get("id") for m in messages})
    ancestry_rows = [dict(row) for row in H.prime_message_rows(c, stored.values())]
    ancestry = [H._json(row["refs"], {}) or {} for row in ancestry_rows]
    prime_references(c, refs + ancestry)
    rooms = {m.get("conversation_id") for m in messages + ancestry_rows}
    H.prime_conversations(c, rooms)
    if read_cache.cache(c) is not None:
        meeting_sources(c).prime_conversations(rooms)
    runs = set()
    for ref in refs + ancestry:
        if not isinstance(ref, dict):
            continue
        run = ref.get("run")
        # Only a string run id can be primed. A list or object here used to be reached only when that
        # message was checked; a deleted or past-the-page row must not abort the whole page.
        aid = ref.get("turn_id") or (run.get("attempt_id") if isinstance(run, dict) else None)
        if isinstance(aid, str) and aid:
            runs.add(aid)
    prime_attempts(c, runs)
    about = {H.message_task_id({**m, "refs": r}, H.conversation(c, m["conversation_id"])
                              if m.get("conversation_id") else None)
             for m, r in zip(messages + ancestry_rows, refs + ancestry) if isinstance(r, dict)}
    _task_rows(c, about - {None, ""})


# Events whose target is a run and whose `tasks` the run took in: carried tasks, and the tasks
# of an input that a later run took over after this one had already read it.
INPUT_MOVED = "attempt.input.moved"
RUN_TASK_EVENTS_SQL = "'task.next-run.carried','" + INPUT_MOVED + "'"


def record_moved_input(c, from_attempt, to_attempt, message):
    """Before an input row moves to another run, keep its tasks against the run that read it."""
    H.event(c, H.KEEPER, INPUT_MOVED, from_attempt,
            {"message_id": message["id"], "to_attempt": to_attempt,
             "tasks": sorted(message_tasks(c, message, include_run=False)),
             "live_meetings": sorted(meeting_sources(c).message(message, include_run=False))})


def readable(c, actor, ids):
    principals = actor if isinstance(actor, tuple) else (actor,)
    # Every permission is current in this snapshot. Tags and wide task bodies
    # contribute nothing to the decision, and shared provenance needs one lookup.
    rows = _task_rows(c, set(ids))
    return all(row is not None and all(H.task_private_readable(c, a, row) for a in principals)
               for row in rows.values())


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
        return (meeting_sources(c).message_readable(actor, message)
                and readable(c, actor, message_tasks(c, message, current=bool(stored))))
    except Problem:
        return False


def readable_messages(c, actor, messages):
    """The messages message_readable passes, each checked against its row as stored, read together in this snapshot
    rather than one query per message. A message not stored is checked as given, as message_readable does."""
    snapshot(c)
    ids = sorted({dict(m).get("id") for m in messages} - {None, ""})
    stored = H.prime_messages(c, ids)
    prime_messages(c, list(stored.values()))
    return [m for m in messages if (message_readable(c, actor, stored[mid], current=True)
                                    if (mid := dict(m).get("id")) in stored else message_readable(c, actor, m))]


def require_message(c, who, message):
    if not message_readable(c, actor(who), message):
        raise Problem("not_found", "Message not found", 404)
    if message.get("kind") == "ask" and (message.get("refs") or {}).get("questions"):
        message["answers"] = H.review_answers(c, message["id"], actor=actor(who))
    return message


def page(c, who, cid, *, before=None, since=None, limit=200, task_id=None, max_bytes=None):
    """Paginate visible rows, so hidden rows cannot leak cursors or counts. `max_bytes` also ends the page once
    its messages come to that many bytes as JSON (the newest message is always sent); the rest pages back."""
    snapshot(c)
    clauses, args = ["conversation_id=?"], [cid]
    if before:
        anchor = c.execute("SELECT rowid,* FROM messages WHERE id=? AND conversation_id=?", (before, cid)).fetchone()
        if not anchor or not message_readable(c, actor(who), anchor):
            raise Problem("cursor", "Message cursor is unavailable", 422)
        clauses.append("rowid<?")
        args.append(anchor["rowid"])
    if since:
        clauses.append("created_at>?")
        args.append(since)
    if task_id:
        # Only the messages that could be about the task are read (backend/message_links.py); each is still
        # checked below exactly as before, so an extra candidate costs a check and nothing more.
        from .message_links import candidates
        ids = candidates(c, task_id, cid)
        if ids is not None:
            # Looked up by id: `+` keeps SQLite from walking the room's index instead.
            clauses[0] = "+conversation_id=?"
            clauses.append("id IN (SELECT value FROM json_each(?))")
            args.append(json.dumps(sorted(ids)))
    visible, reader, size, full = [], actor(who), 0, False

    def consider(stored):
        """The same skip rules as one row of the old loop. True once the page must stop."""
        nonlocal size, full
        if stored.get("deleted_at") or stored.get("deleted"):
            return False
        try:
            tasks = message_tasks(c, stored, current=True)
            if task_id and task_id not in tasks:
                return False
            if not readable(c, reader, tasks):
                return False
        except Problem:
            return False
        message = {**stored, "refs": H._json(stored["refs"], {}) or {}}
        if max_bytes and len(visible) < limit:
            size += len(json.dumps(message, default=str).encode())
            if visible and size > max_bytes:
                full = True
                return True
        visible.append(message)
        return len(visible) > limit

    window = []

    def flush():
        """Prime this window, then apply the same visibility rules. A hidden message is still skipped."""
        if not window:
            return False
        prime_messages(c, window)
        stop = False
        for stored in window:
            if consider(stored):
                stop = True
                break
        window.clear()
        return stop

    # message_readable's own steps, with the row's tasks worked out once: a task's page skips another
    # task's message before its privacy check, so a read costs the room once, not twice. The window is
    # primed before those checks, so rooms, replies, inputs and runs are not one query per message.
    for row in c.execute("SELECT * FROM messages WHERE " + " AND ".join(clauses) + " ORDER BY rowid DESC", args):
        window.append(dict(row))
        if len(window) >= 80 and flush():
            break
    else:
        flush()
    more = full or len(visible) > limit
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
    return (meeting_sources(c).message_readable(principal, current)
            and readable(c, principal, message_tasks(c, current)))


def _payload_messages(value, out):
    """The ids of the messages require_payload checks one at a time in `value`: each dict with a room and an id."""
    if isinstance(value, dict):
        if value.get("conversation_id") and isinstance(value.get("id"), str):
            out.add(value["id"])
        value = value.values()
    elif not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, (dict, list)):
            _payload_messages(item, out)
    return out


def _prime_payload_messages(c, payload):
    """A reply that carries many messages (a claim's history, a poll's inputs) checks each one with its parents and
    room: those are read together first, in this snapshot, rather than a few reads per message."""
    ids = _payload_messages(payload, set())
    if len(ids) > 1:
        snapshot(c)
        prime_queued(c, H.prime_messages(c, ids).values())


def require_payload(c, who, payload, principal=None, checked_for=None):
    """Cached writes are reads too; a stored response cannot restore revoked access.

    `checked_for` is the principal an enclosing call already checked every task this value names for:
    a nested value names only tasks its container names, so that check is not run again for it (it was
    one query per nested dict). Everything else is still checked at every level."""
    if principal is None and checked_for is None:
        _prime_payload_messages(c, payload)
    principal = principal or actor(who)
    if who.role == "runner" and isinstance(payload, dict) and principal == who.actor:
        attempt = payload.get("attempt")
        aid = payload.get("attempt_id") or (attempt.get("id") if isinstance(attempt, dict) else None)
        if aid:
            hosted = c.execute("SELECT a.bot_id FROM attempts a JOIN assignments x ON x.bot=a.bot_id "
                               "WHERE a.id=? AND a.runner_id=? AND x.runner_id=?",
                               (aid, who.runner_id, who.runner_id)).fetchone()
            if not hosted:
                raise Problem("privacy", "This execution is no longer assigned to this computer", 403)
            principal = "bot:" + hosted["bot_id"]
        if isinstance(attempt, dict) and attempt.get("bot_id"):
            principal = "bot:" + attempt["bot_id"]
            if not attempt_readable(c, principal, attempt.get("id")):
                raise Problem("privacy", "This execution is no longer available", 403)
    if (who.role == "runner" and principal == who.actor and isinstance(payload, dict)
            and isinstance(payload.get("worktree_actions"), list)):
        # A computer is never a task participant, so its heartbeat's worktree actions are each read as the bot
        # it hosts that owns the worktree; the rest of the heartbeat is still read as the computer.
        if len(readable_worktree_actions(c, who, payload["worktree_actions"])) != len(payload["worktree_actions"]):
            raise Problem("privacy", "This response contains a task you can no longer read", 403)
        require_payload(c, who, {k: v for k, v in payload.items() if k != "worktree_actions"}, principal, checked_for)
        return payload
    if (who.role == "runner" and principal == who.actor and isinstance(payload, dict)
            and payload.get("kind") == "worktree" and payload.get("id") and payload.get("computer_id") == who.runner_id
            and c.execute("SELECT 1 FROM task_links WHERE id=? AND kind='worktree' AND computer_id=?",
                          (payload["id"], who.runner_id)).fetchone()):
        if not worktree_link_readable(c, who, payload):
            raise Problem("privacy", "This response contains a task you can no longer read", 403)
        return payload
    if principal != checked_for and not readable(c, principal, references(c, payload)):
        raise Problem("privacy", "This response contains a task you can no longer read", 403)
    if principal != checked_for and not meeting_sources(c).readable(principal, meeting_sources(c).references(payload)):
        raise Problem("privacy", "This response contains a meeting you can no longer read", 403)
    if isinstance(payload, dict):
        key = payload.get("key")
        if isinstance(key, str) and key.startswith("report:") and not message_readable(c, principal, H.message(c, key[7:])):
            raise Problem("privacy", "This report is no longer available", 403)
        if isinstance(key, str) and key.startswith("approval:"):
            approval = H.approval(c, key[9:])
            if not approval or not message_readable(c, principal, H.message(c, approval["message_id"])):
                raise Problem("privacy", "This approval is no longer available", 403)
        for key in ("items", "responses", "refs"):
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


def readable_worktree_actions(c, who, actions):
    """Batch the existing own-checkout removal exception; prepare/restore still check every task."""
    snapshot(c)
    removals = {a.get("link_id") for a in actions if isinstance(a, dict) and a.get("action") == "remove"}
    owned = set()
    for part in H.chunks(removals):
        owned.update(row[0] for row in c.execute(
            "SELECT id FROM task_links WHERE kind='worktree' AND computer_id=? AND id IN (" +
            ",".join("?" * len(part)) + ")", (who.runner_id, *part)))
    return [a for a in actions if (isinstance(a, dict) and a.get("action") == "remove" and a.get("link_id") in owned)
            or worktree_action_readable(c, who, a)]


def worktree_action_readable(c, who, action):
    """Whether a computer may be sent this worktree action: its task is readable by the computer, or by the bot that
    owns the worktree while that bot is hosted on this computer. A removal of a worktree on this computer is always
    sent: the computer's own worktree list already names its task, path and branch, and withholding it would leave
    the private checkout on disk and its place counted against the bot for good."""
    ids = references(c, action)
    if readable(c, who.actor, ids):
        return True
    if not isinstance(action, dict):
        return False
    if action.get("action") == "remove" and c.execute(
            "SELECT 1 FROM task_links WHERE id=? AND kind='worktree' AND computer_id=?",
            (action.get("link_id"), who.runner_id)).fetchone():
        return True
    return hosted_owner_reads(c, who, action.get("owner"), ids)


def worktree_link_readable(c, who, link):
    """A computer's own write to a worktree link on it answers with the link row, read like its actions: by the bot
    that owns the worktree while that bot is hosted on this computer."""
    ids = references(c, link)
    if readable(c, who.actor, ids):
        return True
    detail = H._json(link.get("detail"), {}) or {}
    owner = detail.get("owner")
    if not owner and link.get("task_id"):
        task = c.execute("SELECT owner_id FROM tasks WHERE id=?", (link["task_id"],)).fetchone()
        owner = task and task["owner_id"]
    return hosted_owner_reads(c, who, H.actor_id(owner) if isinstance(owner, str) else None, ids)


def hosted_owner_reads(c, who, owner, ids):
    hosted = isinstance(owner, str) and c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                                  (owner, who.runner_id)).fetchone()
    return bool(hosted) and readable(c, "bot:" + owner, ids)


def attempt_readable(c, actor, aid, memo=None):
    try:
        return (meeting_sources(c).attempt_readable(actor, aid)
                and readable(c, actor, attempt_tasks(c, aid, memo)))
    except Problem:
        return False


def require_attempt(c, who, aid):
    if not attempt_readable(c, actor(who), aid):
        raise Problem("not_found", "Turn not found", 404)


def private_meetings(c, ids):
    # A removed source fails closed, just as it does for derived reads.
    return {rid for rid in ids if not (row := c.execute(
        "SELECT visibility FROM live_meetings WHERE id=?", (rid,)).fetchone()) or row["visibility"] != "team"}


def private_execution(c, who):
    return bool(who.attempt_id and (private_meetings(c, meeting_sources(c).attempt(who.attempt_id)) or
                any(H.task_private(c, _task(c, tid)) for tid in attempt_tasks(c, who.attempt_id))))


def question_party(c, who, person, parent=None, guest=None):
    """Whether `person` may be the other party of a private task a bot's run files to put a question to them
    (backend/media.py task_question): they must already read every private task the run carries, and the parent.
    Being a party gives them nothing they could not read before, beyond the parent `guest` names (guest_for)."""
    if parent and not _reads(c, person, parent, guest):
        return False
    return not who.attempt_id or all(_reads(c, person, H._one(c, "SELECT * FROM tasks WHERE id=?", (tid,)), guest)
                                     for tid in attempt_tasks(c, who.attempt_id))


def _reads(c, actor, row, guest=None):
    """`actor` reads `row`, counting the guest a write is about to bring in, (task id, person), as already there."""
    return H.task_private_readable(c, actor, row) or bool(row and guest and guest == (row["id"], actor))


def guest_for(c, who, person, parent):
    """(parent id, person) when a bot's hand-off to `person` under `parent`, a private task with no person on it that
    the bot owns, makes them its guest (hubdb.task_reach); None when it does not; Problem when it is refused.

    The hand-offs are a subtask for them and a question to them (backend/media.py); waiting on them is hubdb's.
    Outside private work a hand-off the guest rules refuse only for what is above the task goes on as before,
    without a guest: the person gets the subtask alone. A second, different person is refused either way."""
    if not (parent and who.role == "bot" and not who.via and who.actor == parent["owner_id"]
            and H.is_bot(parent["requester_id"]) and H.is_human(person) and H.task_private(c, parent)
            and not H.task_private_readable(c, person, parent)):
        return None
    refusal = H.task_reach_refusal(c, who.actor, parent, person)
    if refusal is None:
        return parent["id"], person
    if parent.get("guest") or private_execution(c, who):
        raise Problem("privacy", refusal, 403)
    return None


# ----------------------------------------------------------------------------- a sealed subtask for another bot
# A private task with no person on it could hand nothing to a bot outside its parties: every create from its runs was
# refused, so a repair its owner found stranded (Dev 632). Its owning bot (or that bot's manager) may file one private
# subtask under it for another bot, which reads that subtask and nothing above it: not the parent, its other subtasks
# or the run's other work, only the words the filer writes. A private task with a person on it stays that person's to
# widen: the refusal names them and the route to them.
SEALED_ROUTE = "ask them with `hub task question`, or have them file the subtask"
COPIED_WORDS = 12


def _person_on(row):
    return next((row[k] for k in ("requester_id", "owner_id", "guest") if row.get(k) and H.is_human(row[k])), None)


def _person_name(c, person):
    return (H.human(c, H.actor_id(person)) or {}).get("name") or H.actor_id(person)


def sealed_for(c, who, owner, parent):
    """The private tasks a run's create of a subtask for bot `owner` under private `parent` keeps from it (each one the
    run carries that `owner` cannot read, the parent among them when the run carries it), when that create is a sealed
    hand-off; None when it is not one, so the create is judged as before. Problem when one of them, or a private task
    above it, has a person on it: the words name them and the route to them."""
    if not (parent and who.role == "bot" and not who.via and not getattr(who, "task_actor", "") and who.attempt_id
            and H.is_bot(owner or "") and owner != who.actor and H.task_private(c, parent)
            and H.task_represents(c, who.actor, parent["owner_id"])):
        return None
    kept = {}
    for tid in attempt_tasks(c, who.attempt_id):
        row = _task(c, tid)
        if row and H.task_private(c, row) and not H.task_private_readable(c, owner, row):
            kept.setdefault(row["id"], H.task(c, row["id"]) or dict(row))
    _no_person(c, who, kept.values())
    return list(kept.values()) or None


def _no_person(c, who, rows):
    """Problem naming the person on one of `rows`, or on a private task above one, and the route to them."""
    from . import task_relations as TR
    for row in rows:
        if person := _person_on(row):
            raise Problem("privacy", f"{_person_name(c, person)} is on this private task: {SEALED_ROUTE}", 403)
        for above in TR.ancestors(c, row["id"]):
            above = H.task(c, above)
            if above and H.task_private(c, above) and (person := _person_on(above)):
                # A task above that the filer cannot read keeps its person's name, and the route goes through the
                # filer's own requester, who can reach it.
                if not H.task_private_readable(c, who.actor, above):
                    raise Problem("privacy", "A person is on a private task above this one: ask "
                                  f"{row['requester_id']}, who gave you this task, to file the subtask", 403)
                raise Problem("privacy", f"{_person_name(c, person)} is on a private task above this one: {SEALED_ROUTE}",
                              403)


def _words(text):
    # Letters and digits only, any case: words joined by `_` (a path, a label) are still the words they copy.
    return re.findall(r"[^\W_]+", str(text or "").lower())


def _copied_runs(text):
    words = _words(text)
    return {tuple(words[i:i + COPIED_WORDS]) for i in range(len(words) - COPIED_WORDS + 1)}


# A commit id is long, mixed and unbroken like a secret, yet a review or QA subtask must name the exact head. Only the
# shape heuristic lets one through, and only where it reads as a commit: 40 hex digits right after a commit word, or as
# the commit, tree or blob segment of a GitHub link (whose other segments are then judged one by one). A bare hex run,
# one after a common word such as "at", and a longer one stay secret-shaped, as hex tokens and keys have those shapes.
SHA = r"[0-9a-f]{40}"
COMMIT_REF = re.compile(r"(?i:\b(?:head|base|commit|commits|sha|merge|merged|rev|revision)\b)[\s:=#@`*]*\b" + SHA + r"\b")
GITHUB_PATH = re.compile(r"\bhttps?://(?:www\.)?github\.com(/[^\s?#\"'<>]*)")


def _without_commits(text):
    def link(m):
        parts = m.group(1).split("/")
        return " ".join(part for i, part in enumerate(parts)
                        if not (re.fullmatch(SHA, part) and i and parts[i - 1] in ("commit", "commits", "tree", "blob")))
    return GITHUB_PATH.sub(link, COMMIT_REF.sub(" ", text))


# The preview's long run reaches across `/`, `_` and `-`, so a branch, a session or a dated path would read as a secret.
# A run is let through only when it reads as one of those: each piece between the separators a word, a number or a short
# token (a word is lowercase or capitalised and a number at most eight digits, as a key's pieces are neither), with at
# most one short hex id (a task id or short sha) and at least one real word, once its UUIDs are taken out.
# A base64 or base64url value split by a separator has long mixed pieces, so it is still judged whole.
REF_UUID = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?![A-Za-z0-9])")
REF_PIECE = re.compile(r"[a-z]{0,16}|[A-Z][a-z]{0,15}|[0-9]{0,8}|[A-Za-z0-9]{1,4}")
REF_ID = re.compile(r"[0-9a-f]{7,12}")


def _workflow_ref(run):
    pieces = re.split(r"[/_-]", REF_UUID.sub("", run))
    ids = [piece for piece in pieces if not REF_PIECE.fullmatch(piece)]
    return (len(ids) <= 1 and all(REF_ID.fullmatch(piece) for piece in ids)
            and any(re.fullmatch(r"[A-Za-z]{3,}", piece) for piece in pieces))


def secret_shaped(text):
    """Whether `text` holds a credential-shaped value: the token patterns and secret fields that mask one in a refusal
    preview, or a long mixed run that is not a workflow reference (a branch, a session, a dated path)."""
    from .diagnostics import LONG, SECRET_FIELD, SECRETS, _looks_secret
    text = str(text or "")
    return (any(pattern.search(text) for pattern, _ in SECRETS) or bool(SECRET_FIELD.search(text))
            or any(_looks_secret(m.group(0)) and not _workflow_ref(m.group(0))
                   for m in LONG.finditer(_without_commits(text))))


def sealed_words(kept, title, body):
    """Refuse a sealed subtask whose words carry what the other bot may not read: a secret-shaped value, or 12 or more
    words in a row copied from a private task it is kept from. The refusal names the check, never the text; it is a
    writing correction (Problem, not a recorded refusal), so it never counts toward quarantine."""
    text = f"{title or ''}\n{body or ''}"
    if secret_shaped(text):
        raise Problem("lint", "This subtask holds a secret-shaped value: take it out, and say only what the repair "
                              "needs", 422)
    runs = _copied_runs(text)
    if runs and any(runs & _copied_runs(f"{r.get('title') or ''}\n{r.get('body') or ''}\n{r.get('note') or ''}")
                    for r in kept):
        raise Problem("lint", f"This subtask copies {COPIED_WORDS} or more words in a row from private work the other "
                              "bot cannot read: say only what the repair needs, in your own words", 422)


# ----------------------------------------------------------------------------- a company-visible task from private work
# A repair a private task's owner found may belong in the company's work: filed sealed, nobody else could pick it up or
# see it done. The owning bot (or that bot's manager), from its own run, with no person on the work, files a
# company-visible task "from private work" for any bot. Its own words are all it carries, so they are checked before it
# is written: no secret-shaped value, no 12 words in a row from the private work, no file or link of a private task.
# Filed under the private task, its owner is told when it is done, as for any subtask; the private task stays sealed.
FROM_PRIVATE = True
COMPANY_REFUSAL = ("Private task work cannot create company-visible tasks: its owning bot may file one from private "
                   "work, in its own words (`hub task create --from-private`)")
FILE_ID = re.compile(r"\bfile-[0-9a-f]{24}\b")


def from_private(c, who, owner, parent):
    """The private tasks a company-visible task filed from private work must keep out of its words: its parent and every
    private task the run carries. Problem when the route is not open to this create: switched off, not a bot's own
    run on private task work, a parent that is not private, a private task the filer neither owns nor manages the owner
    of (a guest or reader only), or a person on one or above it (named, with the route to them, as for a sealed one)."""
    if not FROM_PRIVATE:
        raise Problem("privacy", COMPANY_REFUSAL, 403)
    if not (who.role == "bot" and not who.via and not getattr(who, "task_actor", "") and who.attempt_id
            and private_execution(c, who)):
        raise Problem("privacy", "Only a bot's own run on private work files a task from it", 403)
    if private_meetings(c, meeting_sources(c).attempt(who.attempt_id)):
        raise Problem("privacy", "Private meeting work cannot create company-visible tasks", 403)
    if not H.is_bot(owner or ""):
        raise Problem("privacy", "A task from private work is for a bot: ask a person with `hub task question`", 403)
    if parent and not H.task_private(c, parent):
        raise Problem("privacy", "File a task from private work under the private task it comes from, or with no "
                                 "parent", 403)
    rows = {parent["id"]: parent} if parent else {}
    for tid in attempt_tasks(c, who.attempt_id):
        row = _task(c, tid)
        if row and H.task_private(c, row):
            rows.setdefault(row["id"], H.task(c, row["id"]) or dict(row))
    if any(not H.task_represents(c, who.actor, row["owner_id"]) for row in rows.values()):
        raise Problem("privacy", "Only a private task's owning bot, or its manager, files a task from its work", 403)
    _no_person(c, who, rows.values())
    return list(rows.values())


def _private_words(c, row):
    criteria = H._json(row.get("acceptance"), []) or []
    return "\n".join([row.get("title") or "", row.get("body") or "", row.get("note") or "",
                       *(str(item or "") for item in (criteria if isinstance(criteria, list) else [])),
                       *(m.get("body") or "" for m in H.task_comments(c, row["id"]))])


def _carries_private_file_or_link(c, kept, text):
    """Whether `text` names a link on one of `kept`, a file of a private task or made by a run on one, or an
    attachment of one of `kept` or of its comments."""
    ids = [row["id"] for row in kept]
    marks = ",".join("?" * len(ids))
    if ids:
        for row in c.execute(f"SELECT url FROM task_links WHERE task_id IN ({marks})", ids):
            if row["url"] and row["url"] in text:
                return True
        for row in c.execute(f"SELECT blob_id FROM task_assets WHERE task_id IN ({marks})", ids):
            if row["blob_id"] in text:
                return True
        for row in kept:
            for m in H.task_comments(c, row["id"]):
                for asset in c.execute("SELECT blob_id FROM message_assets WHERE message_id=?", (m["id"],)):
                    if asset["blob_id"] in text:
                        return True
    for fid in set(FILE_ID.findall(text)):
        found = c.execute("SELECT task_id,scope FROM bot_files WHERE id=?", (fid,)).fetchone()
        if not found:
            continue
        tasks = {found["task_id"], found["scope"][5:] if found["scope"].startswith("task:") else None} - {None, ""}
        if any(H.task_private(c, _task(c, tid)) for tid in tasks):
            return True
        for version in c.execute("SELECT attempt_id FROM bot_file_versions WHERE file_id=?", (fid,)):
            if version["attempt_id"] and any(H.task_private(c, _task(c, tid))
                                             for tid in attempt_tasks(c, version["attempt_id"])):
                return True
    return False


def create_texts(body):
    """Every text a task create keeps besides its title and body (models.TaskCreate): what a task from private work
    could otherwise carry past the words checks."""
    from .task_roles import role_name
    blocker = body.blocker
    # A role's name is checked as sent and as stored (task_roles.role_name): the store lower-cases and trims it.
    roles = [name for raw in (body.roles or {}) for name in (str(raw), role_name(raw) or "")]
    return [*body.acceptance_criteria, *body.labels, *body.change_paths, *body.links, body.public_title or "",
            body.public_summary or "", body.step or "", body.type or "", *((blocker.repo, blocker.endpoint) if blocker else ()),
            *roles]


def from_private_words(c, kept, title, body, texts=()):
    """Refuse a task from private work whose words carry it: a secret-shaped value, 12 or more words in a row copied
    from one of `kept` (its title, body, note or comments), or a file or link from a private task, in its title, body or
    any other text it keeps (`texts`, create_texts). The refusal names the check, never the text; it is a writing
    correction (Problem, not a recorded refusal), so it never counts toward quarantine."""
    text = "\n".join([title or "", body or "", *(t for t in texts or () if t)])
    if secret_shaped(text):
        raise Problem("lint", "This task holds a secret-shaped value: take it out, and say only what the work needs", 422)
    runs = _copied_runs(text)
    if runs and any(runs & _copied_runs(_private_words(c, row)) for row in kept):
        raise Problem("lint", f"This task copies {COPIED_WORDS} or more words in a row from private work: say only what "
                              "the work needs, in your own words", 422)
    # A private task's title is shorter than a copied run yet names it outright; a false hit costs a rewrite.
    words = " " + " ".join(_words(text)) + " "
    if any((name := " ".join(_words(row.get("title")))) and f" {name} " in words for row in kept):
        raise Problem("lint", "This task names a private task by its title: describe the work without it", 422)
    if _carries_private_file_or_link(c, kept, text):
        raise Problem("lint", "This task carries a file or link from a private task: take it out, and say in your own "
                              "words what the work needs", 422)


def require_create(c, who, private, owner, parent, actor=None, title=None, body=None, from_private_work=False,
                   texts=(), related=()):
    """The privacy refusals of a task create (app.task_create), in one place so its dry-run gives the create's own
    words. `who` is the caller as authenticated, `actor` the identity the task is filed as (a delegated person).
    Returns guest_for's (parent id, person) for the create to make with hubdb.task_reach, or None.
    `from_private_work`: a company-visible task filed from private work (from_private), whose words (`texts`, every
    other text it keeps) are checked here; `related` are the ids of the tasks it is related to besides its parent."""
    if from_private_work:
        if private:
            raise Problem("privacy", "A task from private work is company-visible: leave out --private", 422)
        kept = from_private(c, who, owner, parent)
        if any(H.task_private(c, _task(c, tid)) for tid in related):
            # Related, blocked by or a duplicate of a private task, it would say so to that task's readers and wait on it.
            raise Problem("privacy", "A task from private work is related to no private task but its parent", 403)
        from_private_words(c, kept, title, body, texts)
        return None
    execution = private_execution(c, who)
    if execution and private is False:
        raise Problem("privacy", COMPANY_REFUSAL, 403)
    guest = guest_for(c, who, owner, parent)
    if execution:
        kept = None if guest else sealed_for(c, who, owner, parent)
        if kept is not None:
            sealed_words(kept, title, body)
        require_destination(c, who, owner, None, {}, guest=guest, sealed={r["id"] for r in kept or ()})
        require_destination(c, who, actor or who.actor, None, {}, guest=guest)
    return guest


def require_wait(c, who, row, waiting_on):
    """A private run's `--status waiting --on <person>` puts its ask in front of that person (hubdb._ask_in_subtask) and,
    on a private task with no person on it, makes them its guest: so they must read every private task the run carries,
    as for a subtask or a question to them (require_create, question_party). Checked in the write's own transaction."""
    person = H.resolve_actor(c, str(waiting_on or "").strip()) if waiting_on else None
    if not person or not H.is_human(person) or not private_execution(c, who):
        return
    guest = None if H.task_private_readable(c, person, row) else (row["id"], person)
    require_destination(c, who, person, None, {}, guest=guest)


def public_message(c, message):
    """External channel audiences have no task participant authority."""
    if not message or dict(message).get("deleted_at") or dict(message).get("deleted"):
        return False
    try:
        return not any(H.task_private(c, _task(c, tid)) for tid in message_tasks(c, message))
    except Problem:
        return False


def require_batch(c, who, row):
    if row:
        row = dict(row)
        require_payload(c, who, H._json(row["items"], []) or [])
        if row.get("message_id"):
            require_message(c, who, H.message(c, row["message_id"]))


def content_readable(c, actor, payload):
    return (meeting_sources(c).readable(actor, meeting_sources(c).references(payload))
            and readable(c, actor, references(c, payload)))


def content_readable_rows(c, actor, rows):
    """The rows content_readable passes, with the tasks they name looked up together first."""
    rows = list(rows)
    prime_references(c, rows)
    return [row for row in rows if content_readable(c, actor, row)]


# What a private meeting's bot turn may write besides its own meeting's chat (guard_write): reads that go over POST, the
# hub's MCP door (each tool call it makes is a request of its own, gated here), its run files, which keep the run's
# provenance and so the meeting's readers, and a GitHub token, which is read-only for it (github_app.py token). Its
# repository stays out of the bot's checkout too (runner-rs/src/sessions.rs, private lanes). The meeting's owner shares
# its notes and action items after it, by review.
PRIVATE_MEETING_WRITES = ("/api/v2/sql", "/api/v2/mcp", "/api/v2/tasks/dry-run", "/api/v2/files/uploads",
                          "/api/v2/files/imports", "/api/v2/github/token")
PRIVATE_MEETING_REFUSAL = "This is a private meeting: nothing from it is saved or shared until its owner reviews it."


def private_meeting_run(c, attempt_id):
    """Whether a run carries a private meeting (guard_write's test): a source that cannot be read counts as one."""
    if not attempt_id:
        return False
    try:
        return bool(private_meetings(c, meeting_sources(c).attempt(attempt_id)))
    except Problem:
        return True


def guard_write(c, who, path, auth):
    """A private execution has no automatic company-publication capability."""
    # These controls already enforce their own run ownership and never publish company content.
    if path.startswith(("/api/v2/attempts/", "/api/v2/jobs/")):
        return
    if (who.role != "bot" and not getattr(who, "task_actor", "")) or not private_execution(c, who):
        return
    meetings = meeting_sources(c).attempt(who.attempt_id)
    if private_meetings(c, meetings):
        # A meeting turn is one scoped reply, not an ordinary task or company publisher.
        if path in PRIVATE_MEETING_WRITES:
            return
        scoped = re.fullmatch(r"/api/v2/live-meetings/([^/]+)/(chat|turns/[^/]+/claim)", path)
        if (scoped and meetings == {scoped[1]} and not any(
                H.task_private(c, H.task(c, tid)) for tid in attempt_tasks(c, who.attempt_id))):
            return
        raise Problem("privacy", PRIVATE_MEETING_REFUSAL, 403)
    if path in ("/api/v2/sql", "/api/v2/mcp", "/api/v2/tasks/dry-run"):
        return
    if path == "/api/v2/task-questions":
        return              # a private task of its own, to a person question_party lets read the run's work (media.py)
    if path.startswith(("/api/v2/files/", "/api/v2/messages",
                        "/api/v2/conversations/", "/api/v2/chat/", "/api/v2/tasks")):
        task_path = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/.*)?", path)
        if task_path and task_path[1] not in ("dry-run", "labels", "stuck"):
            row = auth.task(c, who, auth.resolve_task(c, who, task_path[1]))
            if row and not H.task_private(c, row):
                raise Problem("privacy", "Private task work cannot write company-visible tasks", 403)
            if row:
                require_destination(c, who, row["owner_id"], row["conversation_id"], {"task": row["id"]})
        return
    if path.endswith(("/status", "/usage")) or path == "/api/v2/status":
        return
    if path == "/api/v2/rules":
        from . import counsel
        if counsel.run_read(c, who):
            return          # Counsel's suggestions from its own read task; the route checks each (counsel.private_suggestion)
    raise Problem("privacy", "Private task work cannot publish into company-wide resources", 403)


def tag_readable(c, who, row):
    snapshot(c)
    linked = {r[0] for r in c.execute("SELECT task_id FROM task_tags WHERE tag_id=?", (row["id"],))}
    return _tag_readable(c, who, row, linked, tag_attaches(c, [row["id"]]).get(row["id"], ()))


# Attach events of a batch of tags. The index is named because the general target/action index fetches every event
# row from a table of millions; this one holds the attach events alone, covering their detail. Read as one row of
# JSON, as status_tasks is: a busy tag list is thousands of attach rows, each step a GIL hand-off (41 ms on TIDY).
TAG_ATTACHES_SQL = ("SELECT json_group_array(json_array(target,detail)) FROM events INDEXED BY events_tag_attach "
                    "WHERE target IN ({marks}) AND action='tag.attach'")


def tag_attaches(c, tags):
    """{tag: [attach detail, ...]} for up to H.chunks' size of tags."""
    out = {}
    packed = c.execute(TAG_ATTACHES_SQL.format(marks=",".join("?" * len(tags))), tags).fetchone()[0]
    for tag, detail in json.loads(packed or "[]"):
        out.setdefault(tag, []).append(H._json(detail, {}) or {})
    return out


def tags_readable(c, who, rows):
    """The rows tag_readable passes, with every tag's task links and attach events read together and the tasks they
    name looked up together first: one query per tag and per attachment was most of a tag list's SQL."""
    snapshot(c)
    rows = list(rows)
    linked, attached = {}, {}
    for part in H.chunks([row["id"] for row in rows]):
        marks = ",".join("?" * len(part))
        for tag, tid in c.execute(f"SELECT tag_id,task_id FROM task_tags WHERE tag_id IN ({marks})", part):
            linked.setdefault(tag, set()).add(tid)
        attached.update(tag_attaches(c, part))
    prime_references(c, [detail for found in attached.values() for detail in found] + rows)
    _task_rows(c, set().union(*linked.values()))
    return [row for row in rows if _tag_readable(c, who, row, linked.get(row["id"], set()), attached.get(row["id"], ()))]


def _tag_readable(c, who, row, linked, attached):
    linked = set(linked)
    for detail in attached:
        linked.update(references(c, detail))
    # The parties row (id, private, owner, requester, guest) is all task_readable reads; no body is loaded.
    parties = _task_rows(c, linked) if linked and not row.get("is_template") else {}
    return (not linked or bool(row.get("is_template")) or
            any(task_readable(c, who, parties[tid]) for tid in linked)) and content_readable(c, actor(who), row)


def require_destination(c, who, to, conversation_id, refs, reply=None, guest=None, sealed=()):
    """Refuse a write whose audience cannot read every private task it carries. `guest` is the (task id, person) the
    write itself makes a guest (guest_for), counted as already made so a dry-run refuses what the write would.
    `sealed` are the private tasks a sealed subtask for `to` keeps from it (sealed_for): `to` need not read those.
    A private subtask's words may go to its parent's readers when every party of the subtask reads the parent
    (hubdb.subtask_flows_up): a guest's answer comes back to the task it was asked for."""
    ids = references(c, refs)
    ids.update(message_tasks(c, reply) if reply else ())
    if who.attempt_id:
        ids.update(attempt_tasks(c, who.attempt_id))
    meetings = meeting_sources(c)
    meeting_ids = meetings.references(refs)
    if reply:
        meeting_ids.update(meetings.message(reply))
    if who.attempt_id:
        meeting_ids.update(meetings.attempt(who.attempt_id))
    restricted_meetings = private_meetings(c, meeting_ids)
    private = [_task(c, tid) for tid in ids if H.task_private(c, _task(c, tid))]
    if not private and not restricted_meetings:
        return
    conv = H.conversation(c, conversation_id) if conversation_id else None
    audience = set((conv or {}).get("participants") or []) | {who.actor, to}
    if getattr(who, "task_actor", ""):
        audience.add(who.task_actor)
    if not all(meetings.readable(actor, restricted_meetings) for actor in audience):
        raise Problem("privacy", "Private meeting content stays with its current members", 403)
    target = (refs or {}).get("task") if isinstance(refs, dict) else None
    target = _task(c, target) if isinstance(target, str) else None
    for row in private:
        if all(_reads(c, actor, row, guest) for actor in audience if not (actor == to and row["id"] in sealed)):
            continue
        if not (target and H.subtask_flows_up(c, row, target) and all(_reads(c, actor, target) for actor in audience)):
            raise Problem("privacy", "Private task content stays with its requester and current owner", 403)
    if (conv or {}).get("scope") == "shared":
        raise Problem("privacy", "Discuss private tasks in their task conversation", 403)


# A bot's unanswered questions to people, and its undecided approvals: `status` counts the readable ones.
# `{}` is `=?` for one bot or `IN (...)` for many (`status_inputs`).
ASKS_SQL = (f"SELECT m.*, {H.MESSAGE_TASK_SQL} AS about FROM messages m "
            "JOIN conversations cv ON cv.id=m.conversation_id "
            "WHERE m.kind='ask' AND m.sender_id{} AND m.recipient_id LIKE 'human:%' "
            f"AND m.deleted_at IS NULL AND {H.OPEN_ASK_SQL}")
APPROVALS_SQL = ("SELECT m.*, a.task_id AS approval_task{by} FROM approvals a JOIN messages m ON m.id=a.message_id "
                 "WHERE a.requested_by_id{match} AND a.decision IS NULL")


# The only tasks `status` looks at: a live one (its counts) or a private one (whether its focus may be shown).
# A long-lived bot's finished company tasks are most of its rows and change nothing.
STATUS_TASKS_SQL = "(status IN (%s) OR private IS NOT 0)" % ",".join("'%s'" % s for s in H.ACTIVE_STATUSES)
# The columns `status` reads of them: never the bodies, which were megabytes a status page on a real install.
STATUS_TASK_COLUMNS = "id,owner_id,requester_id,status,private,guest,waiting_on"


def status_tasks_sql(match):
    """A bot's tasks `status` reads, `match` being `=?` or `IN (...)` and bound four times. Each part is one
    index: OR-ing owner and requester made SQLite read every task the bots ever had, finished ones included,
    to test status and privacy columns stored past the bodies; a busy team's page spent most of a second there."""
    live = "status IN (%s)" % ",".join("'%s'" % s for s in H.ACTIVE_STATUSES)
    return (f"SELECT {STATUS_TASK_COLUMNS} FROM tasks WHERE owner_id{match} AND {live} "
            f"UNION SELECT {STATUS_TASK_COLUMNS} FROM tasks WHERE requester_id{match} AND {live} "
            # `+` keeps the few private tasks on their own index (tasks_private_status), not the owner's every task.
            f"UNION SELECT {STATUS_TASK_COLUMNS} FROM tasks INDEXED BY tasks_private_status WHERE private IS NOT 0 "
            f"AND (+owner_id{match} OR +requester_id{match})")


def status_tasks(c, match, args):
    """`status_tasks_sql`'s rows as dicts of STATUS_TASK_COLUMNS, read as one row of JSON. The rows are a thousand on a
    busy team (most of them its private tasks), and each row stepped is the GIL given up and taken back: with other
    requests running Python each take back waits up to the 5 ms switch interval, so a 2 ms read took 100 ms to 1 s on
    Tico Team. One row is one wait."""
    names = STATUS_TASK_COLUMNS.split(",")
    packed = c.execute(f"SELECT json_group_array(json_array({STATUS_TASK_COLUMNS})) FROM ({status_tasks_sql(match)})",
                       args).fetchone()[0]
    return [dict(zip(names, values)) for values in json.loads(packed)]


def status_inputs(c, bots):
    """What `status` reads for each of many bots, one query per kind per 500 bots:
    {bot id: {"tasks": [...], "approvals": [...], "asks": [...]}}, to pass to `status` as keywords."""
    snapshot(c)
    out = {bot: {"tasks": [], "approvals": [], "asks": []} for bot in bots}
    for part in H.chunks(out):
        actors = ["bot:" + bot for bot in part]
        marks = " IN (" + ",".join("?" * len(actors)) + ")"
        for t in status_tasks(c, marks, actors * 4):
            for party in {t["owner_id"], t["requester_id"]}:
                if party in actors:
                    out[H.actor_id(party)]["tasks"].append(t)
        # Who asked is read alongside, to sort the rows, and left out of the rows `status` is given.
        for m in c.execute(APPROVALS_SQL.format(by=", a.requested_by_id AS by_", match=marks), actors):
            out[H.actor_id(m["by_"])]["approvals"].append({k: m[k] for k in m.keys() if k != "by_"})
        for m in c.execute(ASKS_SQL.format(marks), actors):
            out[H.actor_id(m["sender_id"])]["asks"].append(m)
    # Each open ask and approval is checked as read here, its tasks, parents and rooms read together: not a few
    # queries per message, which on a busy team were most of a status page's statements.
    if read_cache.cache(c) is not None:
        prime_messages(c, [m for kinds in out.values() for m in (*kinds["approvals"], *kinds["asks"])])
    return out


def status(c, who, row, *, tasks=None, approvals=None, asks=None):
    """Free-form focus/results can contain task content; hide it when provenance is lost."""
    snapshot(c)
    if not row:
        return row
    row = dict(row)
    tid = row.get("task_id")
    tasks = tasks if tasks is not None else status_tasks(c, "=?", ("bot:" + row["bot"],) * 4)
    task = next((t for t in tasks if t["id"] == tid), None) if tid else None
    if tid and task is None:
        task = _task(c, tid)
    private = [task] if task and H.task_private(c, task) else []
    if not tid:
        private = [t for t in tasks if H.task_private(c, t)]
    if any(not task_readable(c, who, t) for t in private):
        row.update(task_id=None, focus="", last_result="", reason="")
    tasks = [t for t in tasks if task_readable(c, who, t)]
    if "open_tasks" in row:
        row["open_tasks"] = sum(t["owner_id"] == "bot:" + row["bot"] and t["status"] in H.ACTIVE_STATUSES for t in tasks)
    if "needs_human" in row:
        # One per readable task that waits on a person (hubdb._recount): filed for a person, set
        # waiting on one, or carrying the bot's unanswered question to one.
        bot = "bot:" + row["bot"]
        live = {t["id"]: t for t in tasks if t["status"] in H.ACTIVE_STATUSES}
        waits = {i for i, t in live.items()
                 if (t["requester_id"] == bot and H.is_human(t["owner_id"]))
                 or (t["owner_id"] == bot and t["status"] == "waiting" and H.is_human(t.get("waiting_on") or ""))}
        if len(waits) < len(live):
            # Both are the message rows (`m.*`) as stored, read in this snapshot.
            for m in asks if asks is not None else c.execute(ASKS_SQL.format("=?"), (bot,)):
                if m["about"] in live and m["about"] not in waits and message_readable(c, actor(who), m, current=True):
                    waits.add(m["about"])
        approvals = approvals if approvals is not None else c.execute(APPROVALS_SQL.format(by="", match="=?"), ("bot:" + row["bot"],))
        # An approval on a task already counted is the same wait, counted once (hubdb.status_counts).
        row["needs_human"] = len(waits) + sum(
            message_readable(c, actor(who), m, current=True) and not (dict(m).get("approval_task") in waits)
            for m in approvals)
    return row


def job_count(c, who, bot, states=("queued",)):
    snapshot(c)
    marks = ",".join("?" * len(states))
    # The rows are the messages as stored in this snapshot (as in job_counts), checked after one lookup for them all.
    rows = [dict(row) for row in c.execute("SELECT m.*,j.attempt_id AS job_attempt FROM jobs j JOIN messages m "
                                           f"ON m.id=j.message_id WHERE j.bot_id=? AND j.state IN ({marks})", (bot, *states))]
    jobs = [(row.pop("job_attempt"), row) for row in rows]
    prime_messages(c, rows)
    prime_attempts(c, [aid for aid, _ in jobs])
    return sum(message_readable(c, actor(who), row, current=True) and
               (not aid or attempt_readable(c, actor(who), aid)) for aid, row in jobs)


def job_counts(c, who, bots, states=("queued",)):
    """`job_count` for many bots, reading their jobs in one query per 500 bots: {bot id: count}.
    Each job is still checked on its own."""
    snapshot(c)
    out = dict.fromkeys(bots, 0)
    marks = ",".join("?" * len(states))
    for part in H.chunks(out):
        for row in c.execute("SELECT m.*,j.attempt_id,j.bot_id AS job_bot FROM jobs j JOIN messages m ON m.id=j.message_id "
                             f"WHERE j.bot_id IN ({','.join('?' * len(part))}) AND j.state IN ({marks})", (*part, *states)):
            out[row["job_bot"]] += bool(message_readable(c, actor(who), row, current=True) and
                                        (not row["attempt_id"] or attempt_readable(c, actor(who), row["attempt_id"])))
    return out


BLOB_PARENTS_SQL = "SELECT blob_id FROM blob_media WHERE poster_blob_id=? OR thumb_blob_id=?"
BLOB_TASKS_SQL = "SELECT task_id FROM task_assets WHERE blob_id=?"
BLOB_FILES_SQL = ("SELECT f.task_id,f.scope,v.attempt_id FROM bot_files f "
                  "JOIN bot_file_versions v ON v.file_id=f.id WHERE v.blob_id=? "
                  "OR v.poster_blob_id=? OR v.thumb_blob_id=?")
BLOB_MESSAGES_SQL = "SELECT m.* FROM message_assets a JOIN messages m ON m.id=a.message_id WHERE a.blob_id=?"
BLOB_TRASHED_SQL = "SELECT 1 FROM task_trash_assets WHERE blob_id=?"


def prime_blobs(c, bids):
    """Read what blob_readable checks for each of `bids` together, a few queries per 500, into this snapshot's
    read_cache. A bot's Files page checks every file version it has; one blob at a time was six queries each, over
    seven thousand for one page. The decision is blob_readable's own, made from the same rows."""
    snapshot(c)
    if read_cache.cache(c) is None:
        return
    pending, seen = {bid for bid in bids if bid}, set()
    tasks, attempts, messages = set(), set(), {}
    # A decision visits at most 100 distinct blobs. Read each ancestry level together,
    # including parents outside the listed files, without caching a path-dependent answer.
    for _ in range(100):
        pending -= seen
        if not pending:
            break
        seen.update(pending)
        pending, linked_tasks, linked_attempts, linked_messages = _prime_blob_links(c, pending)
        tasks.update(linked_tasks)
        attempts.update(linked_attempts)
        messages.update(linked_messages)
    H.prime_message_rows(c, messages.values())
    prime_messages(c, messages.values())
    prime_attempts(c, attempts)
    _task_rows(c, tasks)


def _prime_blob_links(c, bids):
    parent_ids, task_ids, attempt_ids, message_rows = set(), set(), set(), {}
    for part in H.chunks(sorted({bid for bid in bids if bid})):
        marks = ",".join("?" * len(part))
        wanted = set(part)
        parents, tasks, files, messages = ({bid: [] for bid in part} for _ in range(4))
        for row in c.execute("SELECT blob_id,poster_blob_id,thumb_blob_id FROM blob_media "
                             f"WHERE poster_blob_id IN ({marks}) OR thumb_blob_id IN ({marks})", part * 2):
            for bid in {row["poster_blob_id"], row["thumb_blob_id"]} & wanted:
                parents[bid].append(row)
            parent_ids.add(row["blob_id"])
        for row in c.execute(f"SELECT task_id,blob_id FROM task_assets WHERE blob_id IN ({marks})", part):
            tasks[row["blob_id"]].append(row)
            task_ids.add(row["task_id"])
        for row in c.execute("SELECT f.task_id,f.scope,v.attempt_id,v.blob_id,v.poster_blob_id,v.thumb_blob_id "
                             "FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id "
                             f"WHERE v.blob_id IN ({marks}) OR v.poster_blob_id IN ({marks}) "
                             f"OR v.thumb_blob_id IN ({marks})", part * 3):
            for bid in {row["blob_id"], row["poster_blob_id"], row["thumb_blob_id"]} & wanted:
                files[bid].append(row)
            if row["task_id"]:
                task_ids.add(row["task_id"])
            if row["scope"].startswith("task:"):
                task_ids.add(row["scope"][5:])
            attempt_ids.add(row["attempt_id"])
        assets = c.execute(f"SELECT blob_id,message_id FROM message_assets WHERE blob_id IN ({marks})", part).fetchall()
        found = H.prime_messages(c, {row["message_id"] for row in assets})
        message_rows.update(found)
        for row in assets:
            if row["message_id"] in found:
                messages[row["blob_id"]].append(found[row["message_id"]])
        trashed = {row[0] for row in c.execute(
            f"SELECT DISTINCT blob_id FROM task_trash_assets WHERE blob_id IN ({marks})", part)}
        read_cache.prime_rows(c, BLOB_PARENTS_SQL, {(bid, bid): parents[bid] for bid in part})
        read_cache.prime_rows(c, BLOB_TASKS_SQL, {(bid,): tasks[bid] for bid in part})
        read_cache.prime_rows(c, BLOB_FILES_SQL, {(bid, bid, bid): files[bid] for bid in part})
        read_cache.prime_rows(c, BLOB_MESSAGES_SQL, {(bid,): messages[bid] for bid in part})
        read_cache.prime(c, BLOB_TRASHED_SQL, {(bid,): (1,) if bid in trashed else None for bid in part})
    return parent_ids, task_ids, attempt_ids, message_rows


def blob_readable(c, actor, bid, seen=None, memo=None):
    """A task attachment cannot regain access through uploader ownership or another link.
    `memo` is attempt_tasks' cache for one read transaction. Only linkage rows are cached
    for blobs: a cycle or depth refusal depends on this walk, not just the blob."""
    snapshot(c)
    seen = set() if seen is None else seen
    if bid in seen:
        return True
    if len(seen) >= 100:
        return False
    seen.add(bid)
    for parent in read_cache.rows(c, BLOB_PARENTS_SQL, (bid, bid)):
        if not blob_readable(c, actor, parent[0], seen, memo):
            return False
    ids = {r[0] for r in read_cache.rows(c, BLOB_TASKS_SQL, (bid,))}
    for row in read_cache.rows(c, BLOB_FILES_SQL, (bid, bid, bid)):
        if row["task_id"]:
            ids.add(row["task_id"])
        if row["scope"].startswith("task:"):
            ids.add(row["scope"][5:])
        if row["attempt_id"]:
            if not meeting_sources(c).attempt_readable(actor, row["attempt_id"]):
                return False
            ids.update(attempt_tasks(c, row["attempt_id"], memo))
    linked = bool(ids)
    for row in read_cache.rows(c, BLOB_MESSAGES_SQL, (bid,)):
        linked = True
        if not message_readable(c, actor, row, current=True):
            return False
    # Attached to a task that was deleted, and to nothing else: its uploader may not open it either.
    if not linked and read_cache.one(c, BLOB_TRASHED_SQL, (bid,)):
        return False
    return readable(c, actor, ids)


def event_readable(c, actor, event):
    event = dict(event)
    meetings = meeting_sources(c)
    if not meetings.readable(actor, meetings.references(H._json(event.get("detail"), {}) or {})):
        return False
    ids = references(c, H._json(event.get("detail"), {}) or {}) | references(c, event.get("target"))
    target = event.get("target")
    if _task(c, target):
        ids.add(target)
    msg = H.message(c, target)
    if msg and not message_readable(c, actor, msg):
        return False
    if c.execute("SELECT 1 FROM attempts WHERE id=?", (target,)).fetchone():
        if not meetings.attempt_readable(actor, target):
            return False
        ids.update(attempt_tasks(c, target))
    if c.execute("SELECT 1 FROM conversations WHERE id=?", (target,)).fetchone():
        if not meetings.conversation_readable(actor, target):
            return False
    if c.execute("SELECT 1 FROM live_meetings WHERE id=?", (target,)).fetchone():
        if not meetings.readable(actor, {target}):
            return False
    # Tool/audit events from a private execution often have no explicit task ref.
    if H.is_bot(event.get("actor")):
        for row in c.execute("SELECT id FROM attempts WHERE bot_id=? AND created_at<=? "
                             "AND coalesce(finished_at,?)>=?", (H.actor_id(event["actor"]),
                                                            event["created_at"], H.now(), event["created_at"])):
            if not meetings.attempt_readable(actor, row[0]):
                return False
            ids.update(attempt_tasks(c, row[0]))
    return readable(c, actor, ids)
