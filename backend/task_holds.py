"""What holds a waiting task, in words, recomputed whenever one of its holds changes.

A task's Blocked reason used to be its note's first line when the wait began, kept until something moved it on.
Holds then cleared under it (a child finished, a question was answered or withdrawn) and the board went on saying
"Waiting on Chris Hawkins" for weeks. Now the reason is derived from the holds Tico tracks (`holds`), every time one
of them changes (`refresh`):

- a question its owner asked on it, a child or blocker still open, an undecided approval, a person it waits on
  (`waiting_on`), and the external holds `wait_detail` reads (a worktree, a milestone, GitHub);
- when the last one clears, a bot's task goes back to open at once with a note naming what cleared, and a person's
  task stays where it is with "Nothing left to wait on: <what cleared>";
- an owner's own hold (a move to Blocked with a reason) is kept as written, and Engineering's gate, check and
  worktree holds keep their own reconciliation (backend/task_blocked.py).

A task waiting on nothing is measured from the blocked hold's `cleared_at` (when the last hold cleared) or its
`created_at` (when a wait on nothing began), never from a note: a note cannot keep a task waiting on nothing.

A task's own holds, its move to Blocked and its snooze, are rows of `task_holds` (migration 0075), one open row of each
kind; `put`, `change` and `release` below write them, and a task row read through hubdb.TASKS carries the open ones'
fields under the names the code always read (`FIELDS`).
"""

NOTHING = "Nothing tracked to wait on"
LEFT = "Nothing left to wait on"
# Holds with a reconciliation of their own: their reason and resume stay with backend/task_blocked.py.
OWN_RECONCILE = ("gate", "check", "worktree")


def name(conn, actor):
    """A holder as people read it: a person's or bot's name, else its id."""
    from . import hubdb as H
    if not actor:
        return "a person"
    if H.is_human(actor):
        return H.person_name(conn, H.actor_id(actor))
    if H.is_bot(actor):
        return (H.bot(conn, actor) or {}).get("name") or H.actor_id(actor)
    return H.actor_id(actor)


def _line(text, limit=160):
    from . import hubdb as H
    first = next((s.strip() for s in str(text or "").splitlines() if s.strip()), "")
    return H._clip(first, limit)


def _shared(conn, row, ask):
    """Whether everyone who reads the task may read this question, so its words may go in the task's reason: asked in
    the task's own room, with no meeting and no private task besides this one in its provenance (reply chain, inputs,
    run). A question tied to the task from a stricter source is named only as "a question"."""
    from . import hubdb as H, task_privacy as TP
    from .live_privacy import sources
    from .store import Problem
    if ask.get("conversation_id") != row.get("conversation_id"):
        return False
    try:
        if sources(conn).message(ask):
            return False
        others = TP.message_tasks(conn, ask) - {row["id"]}
    except Problem:         # provenance that cannot be authorized is not shared
        return False
    return not any(r is None or H.task_private(conn, r) for r in TP._task_rows(conn, others).values())


def holds(conn, row, *, who=None, auth=None):
    """Every hold Tico tracks on a task, in the order `wait_detail` reads them: [{"kind", "on", "what"}]. `on` is the
    actor holding it (None for an external hold or an approval with nobody named), `what` a short subject."""
    from . import hubdb as H, task_relations as TR, profiles, review_gate
    out = []
    asks = H._rows(conn.execute(
        "SELECT m.* FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE m.sender_id=? AND m.kind='ask' AND m.superseded_at IS NULL AND {H.MESSAGE_TASK_SQL}=? "
        "ORDER BY m.created_at", (row["owner_id"], row["id"])))
    if asks:
        answered = H.answers_to(conn, [a["id"] for a in asks])
        from .turn_scope import message_view
        out += [{"kind": "ask", "on": a["recipient_id"],
                 "what": (_line((message_view(conn, a, who=who, auth=auth) or {}).get("body")) or "a question")
                 if _shared(conn, row, a) else "a question"}
                for a in asks if a["id"] not in answered]
    engineering = review_gate.engineering(conn, row)
    check = None
    for child in H._rows(conn.execute(
            f"SELECT id,owner_id,title FROM tasks WHERE id IN ({TR.CHILD_IDS}) "
            "AND status NOT IN ('done','closed','declined') ORDER BY created_at,id", (row["id"],))):
        # A pipeline check is work at the current step, not a hold, unless the check itself is blocked (wait_detail).
        if engineering and H.is_bot(child["owner_id"]) and profiles.is_review_task(conn, child, parent_id=row["id"]):
            if check is None:
                from .check_blocked import detail as check_detail
                check = check_detail(conn, row) or False
                if check:
                    out.append({"kind": "check", "on": check.get("on"), "what": _line(check.get("why"))})
            continue
        out.append(_task_hold(conn, row, "child", child, who=who, auth=auth))
    own = {r[0] for r in conn.execute(TR.CHILD_IDS, (row["id"],))}
    out += [_task_hold(conn, row, "blocker", b, who=who, auth=auth)
            for b in TR.live_blockers(conn, row["id"], H.ACTIVE_STATUSES) if b["id"] not in own]
    for a in H._rows(conn.execute("SELECT * FROM approvals WHERE task_id=? AND decision IS NULL ORDER BY created_at",
                                  (row["id"],))):
        H.approval_wait(conn, a)
        out.append({"kind": "approval", "on": a.get("decision_owner"), "what": f"a {a.get('kind') or 'yes'} approval"})
    from .reconnects import hold as reconnect_hold
    reconnect = reconnect_hold(conn, row["id"])
    if reconnect:
        out.append({"kind": "person", "on": reconnect["on"], "what": reconnect["what"]})
    if row.get("waiting_on") and not any(h["on"] == row["waiting_on"] for h in out):
        out.append({"kind": "person", "on": row["waiting_on"], "what": ""})
    # The external holds wait_detail reads after these: a worktree, a milestone, a GitHub callback.
    detail = H.wait_detail(conn, {**row, "blocked_kind": None})
    if not out and detail and detail.get("on") == "external":
        out.append({"kind": "external", "on": None, "what": _line(detail["why"])})
    return out


def _task_hold(conn, row, kind, other, *, who=None, auth=None):
    """A child or blocker as a hold. A private one is named by neither title nor owner: the reason is shown to everyone
    who reads the task (its guest and its parties' managers too), and not all of them read the other task."""
    from . import hubdb as H
    if H.task_private(conn, other):
        return {"kind": "private", "on": None, "what": "a private task"}
    from . import turn_scope
    shown = turn_scope.project(conn, turn_scope.scope(conn, who), {'id': other['id'], 'title': other['title']}, auth=auth)
    return {"kind": kind, "on": other["owner_id"], "what": _line((shown or {}).get('title'))}


def words(conn, found):
    """The Blocked reason for these holds: the first named in full, the rest counted."""
    if not found:
        return NOTHING
    first = found[0]
    if first["kind"] in ("external", "private"):
        text = f"Waiting on {first['what']}"
    else:
        who = name(conn, first["on"]) if first["on"] else "a person"
        text = f"Waiting on {who}" + (f": {first['what']}" if first["what"] else "")
    more = len(found) - 1
    return text + (f" (and {more} more)" if more else "")


def text_sources(conn, row):
    """The questions and tasks whose words enter a cached dependency reason."""
    from . import hubdb as H, task_relations as TR
    from .scope_projection import body_sources
    sources = []
    for ask in conn.execute(f"SELECT m.* FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                            f"WHERE m.sender_id=? AND m.kind='ask' AND m.superseded_at IS NULL AND {H.MESSAGE_TASK_SQL}=?",
                            (row['owner_id'], row['id'])):
        sources.extend([{'message': ask['id']}, body_sources(H._json(ask['refs'], {}))])
    sources.extend({'task': tid} for (tid,) in conn.execute(TR.CHILD_IDS, (row['id'],)))
    sources.extend({'task': other['id']} for other in TR.live_blockers(conn, row['id'], H.ACTIVE_STATUSES))
    return [source for source in sources if source]


def reason(conn, row):
    """The reason a task entering a wait shows (task_blocked.transition), or None to keep the caller's own."""
    found = holds(conn, row)
    if found:
        from . import hubdb as H
        text = H.remember_sourced_text(conn, 'task', row['id'], 'blocked_reason', words(conn, found), text_sources(conn, row))
        return text, "dependency"
    return NOTHING, "stranded"


def _applies(conn, row):
    from . import hubdb as H
    if not row or row.get("status") != "waiting" or not row.get("blocked_since"):
        return False
    if row.get("blocked_kind") == "manual" or row.get("blocked_kind") in OWN_RECONCILE:
        return False
    return (H.type_get(conn, row.get("type_id")) or {}).get("role") != "product-roadmap"


def refresh(conn, task_id, cleared=None, at=None):
    """Recompute a waiting task's reason from its holds. `cleared` names a hold of this task that just went away (a
    child done, a question answered or withdrawn, an approval decided, a person wait cleared): when it was the last,
    a bot's task goes back to work and a person's says there is nothing left to wait on. Without `cleared` (a note,
    a new hold) only the words change. Returns what happened: "held", "open", "resumed", "nothing" or None."""
    from . import hubdb as H, review_gate, task_blocked
    row = H.task(conn, task_id)
    if not _applies(conn, row):
        return None
    at = at or H.now()
    found = holds(conn, row)
    if found:
        text = H.remember_sourced_text(conn, 'task', row['id'], 'blocked_reason', words(conn, found), text_sources(conn, row))
        if (row.get("blocked_reason"), row.get("blocked_kind")) != (text, "dependency") or row.get("holds_cleared_at"):
            change(conn, task_id, "blocked", reason=text[:1000], cause="dependency", cleared_at=None)
            task_blocked.bump(conn, row)
        return "held"
    if cleared and review_gate.engineering(conn, row) and row.get("blocked_kind") == "dependency":
        # A board ticket goes back to the step it left, as any hold that clears does (task_blocked.reconcile).
        task_blocked.reconcile(conn, task_id)
        if H.task(conn, task_id)["status"] != "waiting":
            return "resumed"
    if cleared and H.is_bot(row["owner_id"]):
        # A reconnect wake owed on it goes now, with what cleared, as its one wake (backend/reconnects.py).
        from . import reconnects
        if reconnects.deliver(conn, task_id, why="holds", cleared=cleared):
            task_blocked.bump(conn, row)
            return "open"
        note = f"{cleared}; nothing left to wait on"
        H._set_status(conn, H.KEEPER, row, "open", note=note)
        conn.execute("UPDATE tasks SET note=? WHERE id=?", (note, task_id))
        task_blocked.bump(conn, row)
        H._task_event(conn, task_id, H.KEEPER, "note", row.get("note"), note, "")
        from . import check_pools, verdict_moves
        check_pools.safely(conn, verdict_moves.resumed, task_id)
        return "open"
    text = f"{LEFT}: {cleared}" if cleared else NOTHING
    if cleared or row.get("blocked_kind") != "stranded" or (row.get("blocked_reason") or "").startswith("Waiting on"):
        # Keep "Nothing left to wait on: ..." when a later note recomputes it: it still says what cleared.
        if cleared or not (row.get("blocked_reason") or "").startswith(LEFT):
            change(conn, task_id, "blocked", reason=text[:1000], cause="stranded",
                   cleared_at=at if cleared else row.get("holds_cleared_at"))
            task_blocked.bump(conn, row)
    return "nothing"


def refresh_many(conn, task_ids, cleared=None):
    for task_id in dict.fromkeys(t for t in task_ids if t):
        refresh(conn, task_id, cleared)


def title(conn, row):
    """A child or blocker named in what cleared: a private one by no title, as _task_hold names it."""
    from . import hubdb as H
    return "A private task" if H.task_private(conn, row) else _line(row["title"], 120)


def holders(conn, task_id):
    """`task_id` came back to life, or its title or owner changed: the tasks it holds as a child or blocker name it
    afresh. Nothing cleared, so none of them is released."""
    from . import task_relations as TR
    refresh_many(conn, [TR.parent_of(conn, task_id), *TR.blocked_ids(conn, task_id)])


def ended(conn, row, status):
    """`row` just finished as `status`: its parent and the tasks it blocked lose a hold."""
    from . import hubdb as H, task_relations as TR
    what = f"{title(conn, row)} {'is done' if status == 'done' else 'was ' + H.TASK_ENDED[status]}"
    parent = TR.parent_of(conn, row["id"])
    if parent:
        refresh(conn, parent, what)
    for blocked in TR.blocked_ids(conn, row["id"]):
        refresh(conn, blocked, what)


def withdrawn(conn, task_id, ask_ids, why, *, by=None, told=True):
    """Questions to people on `task_id` were just withdrawn: its own task ended, a newer question replaced them, or
    "still needed?" sent them back (a question lives and dies with its own task, never with a task above it). Each
    question shows whether its person had seen it ("Withdrawn before Chris saw it: <why>"); its asker is told in the
    same transaction unless it made the withdrawal itself (`by`) or `told` is False (still needed? sends its own one
    notice per bot); and the task's holds are recomputed, so a bot's task left with nothing to wait on goes back to
    open with a note naming what happened."""
    from . import hubdb as H
    if not ask_ids:
        return
    row = H.task(conn, task_id)
    if not row:
        return
    marks = ",".join("?" * len(ask_ids))
    asks = H._rows(conn.execute(f"SELECT id, sender_id, recipient_id, body, read_at FROM messages "
                                f"WHERE id IN ({marks}) ORDER BY created_at", list(ask_ids)))
    why = str(why or "it was withdrawn")
    day_ago = H.shift(H.now(), hours=-24)
    noticed = set()
    for ask in asks:
        person = name(conn, ask["recipient_id"])
        status = f"Withdrawn {'after' if ask.get('read_at') else 'before'} {person} saw it: {why}"
        conn.execute("UPDATE messages SET refs=json_set(coalesce(refs,'{}'),'$.withdrawn',?) WHERE id=?",
                     (status, ask["id"]))
        asker = ask["sender_id"]
        if (not told or asker == by or not H.is_bot(asker) or (asker, ask["recipient_id"]) in noticed
                or not H.bot_reads_task(conn, asker, row)):
            continue
        noticed.add((asker, ask["recipient_id"]))
        # The same question withdrawn again and again is a re-ask loop: the notice says so (to watch, not a block).
        again = conn.execute(
            "SELECT count(*) FROM messages m JOIN conversations cv ON cv.id=m.conversation_id WHERE m.kind='ask' "
            f"AND m.sender_id=? AND m.recipient_id=? AND m.superseded_at>=? AND {H.MESSAGE_TASK_SQL}=?",
            (asker, ask["recipient_id"], day_ago, task_id)).fetchone()[0]
        loop = (f"\nThis is the {_nth(again)} question from you to {person} on this task withdrawn in 24 hours."
                if again >= 3 else "")
        H._wake(conn, row, asker, f"Your question to {person} was withdrawn: {row['title']}\n{status}.\n"
                f"Question: {_line(ask['body'], 300)}{loop}\nAsk again on a live task if you still need it.",
                refs={"news": "ask_withdrawn", "ask": ask["id"]}, source=ask["id"])
    if asks:
        # Its owner asking again or ending it knows already: only the words follow.
        refresh(conn, task_id, None if by == row["owner_id"] else
                f"Your question to {name(conn, asks[0]['recipient_id'])} was withdrawn ({why})")


def _nth(n):
    return {3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


BACKFILL = "task_holds.backfill"


def backfill(conn, at=None):
    """Once, at the first hourly check after this shipped: every waiting task is put right by the same rules. Its
    reason names what holds it; a bot's task held by nothing goes back to open, and each bot hears once, however many
    of its tasks moved. Returns {"fixed", "opened", "woken"}, or None when it already ran."""
    from . import hubdb as H, review_gate, task_blocked
    if conn.execute("SELECT 1 FROM events WHERE action=? LIMIT 1", (BACKFILL,)).fetchone():
        return None
    at = at or H.now()
    fixed, opened = 0, {}
    for row in H._rows(conn.execute(f"SELECT * FROM {H.TASKS} tasks WHERE status='waiting' AND blocked_since IS NOT NULL "
                                    "ORDER BY created_at, id")):
        if not _applies(conn, row):
            continue
        if holds(conn, row):
            fixed += refresh(conn, row["id"], at=at) == "held"
            continue
        if review_gate.engineering(conn, row) and row.get("blocked_kind") == "dependency":
            task_blocked.reconcile(conn, row["id"])
            if H.task(conn, row["id"])["status"] != "waiting":
                fixed += 1
                continue
        active = H.is_bot(row["owner_id"]) and (H.bot(conn, row["owner_id"]) or {}).get("state") == "active"
        if active and not H.not_yet_due(row, at):
            note = ("Back to open: nothing Tico tracks was holding it (no open question, child task, blocker, "
                    "approval or person to wait on). Continue it, ask, or finish it.")
            H._set_status(conn, H.KEEPER, row, "open", note=note)
            conn.execute("UPDATE tasks SET note=? WHERE id=?", (note, row["id"]))
            task_blocked.bump(conn, row)
            H._task_event(conn, row["id"], H.KEEPER, "note", row.get("note"), note, "")
            opened.setdefault(row["owner_id"], []).append(row)
        else:
            refresh(conn, row["id"], at=at)
            fixed += 1
    for owner, rows in opened.items():
        # One wake per bot, in its first task's room: it names only that task, never another task's title.
        more = len(rows) - 1
        H._wake(conn, H.task(conn, rows[0]["id"]), owner,
                f"Open: {rows[0]['title']}\nNothing Tico tracks was holding it"
                + (f", nor {more} more of your waiting tasks, now open too" if more else "")
                + ". Continue each, ask, or finish it.", refs={"news": "assignment"})
    result = {"fixed": fixed, "opened": sum(len(r) for r in opened.values()), "woken": len(opened)}
    H.event(conn, H.KEEPER, BACKFILL, "", result)
    return result


# The holds a task's API view lists (docs/tasks.md, Holds): one per kind, from the task's open task_holds rows. A
# blocked hold is Tico's or an owner's move to Blocked (`blocked_*`), a snoozed one a snooze (`snoozed_*`); the waits a hold is
# made of (`waiting_on`, `worktree_waits`, `check_waits`, `milestone_waits`, `deferred`) keep their own fields.
HOLD_KINDS = ("blocked", "snoozed")


def marks(conn, rows):
    """Who set each held task's hold and when a snooze began, for `public`: two reads for a whole page, none when
    no row is snoozed or blocked by hand."""
    from . import hubdb as H
    snoozed = [r["id"] for r in rows if r.get("snoozed_until")]
    manual = [r["id"] for r in rows if r.get("blocked_since") and r.get("blocked_kind") == "manual"]
    found = {}
    for chunk in (snoozed[i:i + 500] for i in range(0, len(snoozed), 500)):
        for e in conn.execute(
                f"SELECT subject_id AS task_id,max(created_at) created_at FROM history_events WHERE subject_type='task' "
                f"AND kind='snoozed_until' AND new IS NOT NULL "
                f"AND subject_id IN ({','.join('?' * len(chunk))}) GROUP BY subject_id", chunk):
            found.setdefault(e["task_id"], {})["snoozed_since"] = e["created_at"]
    for chunk in (manual[i:i + 500] for i in range(0, len(manual), 500)):
        # An owner's hold is the latest move a person or bot made into it: the move to Blocked, or a step move that
        # declared the hold over one Tico found (task_blocked.transition).
        for e in conn.execute(
                f"SELECT subject_id AS task_id,actor_id AS actor,max(created_at) created_at FROM history_events "
                f"WHERE subject_type='task' AND kind IN ('status','step') AND actor_id<>? "
                f"AND subject_id IN ({','.join('?' * len(chunk))}) GROUP BY subject_id", (H.KEEPER, *chunk)):
            found.setdefault(e["task_id"], {})["blocked_by"] = e["actor"]
    return found


def public(row, mark=None):
    """The task's holds as API v2 shows them: `[{kind, reason, since, until, by}]`, kind unique, `until` null when
    open-ended, `by` the actor who set it; a blocked hold says its `cause` (manual, dependency, stranded, gate, check,
    worktree). `mark` is this task's entry from `marks`."""
    from . import hubdb as H
    mark = mark or {}
    found = []
    if row.get("blocked_since"):
        cause = row.get("blocked_kind")
        found.append({"kind": "blocked", "reason": row.get("blocked_reason"), "since": row["blocked_since"],
                      "until": None, "by": mark.get("blocked_by") if cause == "manual" else H.KEEPER,
                      "cause": cause})
    if row.get("snoozed_until"):
        found.append({"kind": "snoozed", "reason": row.get("snoozed_note"), "since": mark.get("snoozed_since"),
                      "until": row["snoozed_until"], "by": row.get("snoozed_by_id")})
    return found


# ----------------------------------------------------------------------------- the rows (migration 0075)
# A task's open hold of each kind is one task_holds row. Code reads a task with its holds' fields under the names
# below (hubdb.TASKS): a hold's column -> the field a task row carries.
FIELDS = {
    "blocked": {"blocked_since": "created_at", "blocked_reason": "reason", "blocked_kind": "cause",
                "blocked_from_step": "from_step_id", "blocked_from_status": "from_status", "blocked_gate": "gate",
                "holds_cleared_at": "cleared_at"},
    "snoozed": {"snoozed_until": "until_at", "snoozed_by_id": "by_id", "snoozed_note": "reason"},
}
COLUMNS = ("cause", "reason", "by_id", "until_at", "from_step_id", "from_status", "gate", "cleared_at")
SHOWN = ("created_at", *COLUMNS)


def open_hold(conn, task_id, kind):
    """The task's open hold of `kind` ('blocked' or 'snoozed'), or None."""
    row = conn.execute(f"SELECT {', '.join(SHOWN)} FROM task_holds WHERE task_id=? AND kind=? AND released_at IS NULL",
                       (task_id, kind)).fetchone()
    return dict(zip(SHOWN, row, strict=True)) if row is not None else None


def put(conn, task_id, kind, at, **fields):
    """Open a hold of `kind` on the task from `at`. A task has one open hold of each kind (task_holds_open): one already
    open takes `at` and these fields instead, as writing the old columns again did."""
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"task_holds has no {sorted(unknown)}")
    names = ["task_id", "kind", "created_at", *fields]
    conn.execute(f"INSERT INTO task_holds({','.join(names)}) VALUES({','.join('?' * len(names))}) "
                 "ON CONFLICT(task_id, kind) WHERE released_at IS NULL DO UPDATE SET "
                 + ",".join(f"{name}=excluded.{name}" for name in names[2:]),
                 (task_id, kind, at, *fields.values()))


def change(conn, task_id, kind, **fields):
    """Set fields of the task's open hold of `kind`; nothing when it has none."""
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"task_holds has no {sorted(unknown)}")
    if fields:
        conn.execute(f"UPDATE task_holds SET {','.join(f'{name}=?' for name in fields)} "
                     "WHERE task_id=? AND kind=? AND released_at IS NULL", (*fields.values(), task_id, kind))


def release(conn, task_id, kind, at):
    """End the task's open hold of `kind`: it stays as history with `released_at`."""
    conn.execute("UPDATE task_holds SET released_at=? WHERE task_id=? AND kind=? AND released_at IS NULL",
                 (at, task_id, kind))


def with_holds(conn, row):
    """`row` with its open holds' fields (FIELDS), read when it came from a plain `SELECT * FROM tasks`."""
    if row is None or "blocked_since" in row or not row.get("id"):
        return row
    out = {**row, **{field: None for names in FIELDS.values() for field in names}}
    for kind, *values in conn.execute(f"SELECT kind, {', '.join(SHOWN)} FROM task_holds "
                                      "WHERE task_id=? AND released_at IS NULL", (row["id"],)):
        hold = dict(zip(SHOWN, values, strict=True))
        out.update({field: hold[column] for field, column in FIELDS[kind].items()})
    return out
