"""What holds a waiting task, in words, recomputed whenever one of its holds changes.

A task's Blocked reason used to be its note's first line when the wait began, kept until something moved it on.
Holds then cleared under it (a child finished, a question was answered or withdrawn) and the board went on saying
"Waiting on Chris Hawkins" for weeks. Now the reason is derived from the holds Tico tracks (`holds`), every time one
of them changes (`refresh`):

- a child or blocker still open (a person's question task among them: "waiting on a person" is that, derived), an
  undecided approval, an account a person reconnects, and the external holds `wait_detail` reads (a worktree, a milestone,
  GitHub);
- when the last one clears, a bot's task goes back to open at once with a note naming what cleared, and a person's
  task stays where it is with "Nothing left to wait on: <what cleared>";
- an owner's own hold (a move to Blocked with a reason) is kept as written, and Engineering's gate, check and
  worktree holds keep their own reconciliation (backend/task_blocked.py).

A task waiting on nothing is measured from the blocked hold's `cleared_at` (when the last hold cleared) or its
`created_at` (when a wait on nothing began), never from a note: a note cannot keep a task waiting on nothing. A bot
that parks its task with nothing tracked (cause `stranded`) gets it back: after GRACE_HOURS the hourly pass (`unstrand`)
reopens it, unless its due date or snooze says to look later; one that keeps going back to waiting on nothing is left
waiting and its manager bot, or BotOps, is told.

A task's own holds, its move to Blocked and its snooze, are rows of `task_holds` (migration 0075), one open row of each
kind; `put`, `change` and `release` below write them, and a task row read through hubdb.TASKS carries the open ones'
fields under the names the code always read (`FIELDS`).
"""

NOTHING = "Nothing tracked to wait on"
LEFT = "Nothing left to wait on"
# What a cleared wait with no hold left to name says it cleared (refresh's `cleared`, a waiting flag cleared by hand).
WAIT_CLEARED = "The wait on a person was cleared"
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


def holds(conn, row):
    """Every hold Tico tracks on a task, in the order `wait_detail` reads them: [{"kind", "on", "what"}]. `on` is the
    actor holding it (None for an external hold or an approval with nobody named), `what` a short subject. A child or
    blocker named by its title, or the task a blocked check waits on, also carries its id as `task`."""
    from . import hubdb as H, task_relations as TR, profiles, review_gate

    out = []
    engineering = review_gate.engineering(conn, row)
    check = None
    for child in H._rows(
        conn.execute(
            f"SELECT id,owner_id,title FROM tasks WHERE id IN ({TR.CHILD_IDS}) "
            "AND status NOT IN ('done','closed','declined') ORDER BY created_at,id",
            (row["id"],),
        )
    ):
        # A pipeline check is work at the current step, not a hold, unless the check itself is blocked (wait_detail).
        if engineering and H.is_bot(child["owner_id"]) and profiles.is_review_task(conn, child, parent_id=row["id"]):
            if check is None:
                from .check_blocked import detail as check_detail

                check = check_detail(conn, row) or False
                if check:
                    # The task the check waits on, as `task`: its live blocker (check_blocked.blocked), or for a
                    # waiting check the task its own first hold names, as its reason does (words).
                    held = H.task(conn, check["check"]) or {}
                    if not held:
                        named = None
                    elif held.get("status") == "waiting":
                        named = next(iter(holds(conn, held)), {}).get("task")
                    else:
                        named = TR.live_blocker(conn, held["id"], H.ACTIVE_STATUSES)
                    out.append({"kind": "check", "on": check.get("on"), "what": _line(check.get("why")), "task": named})
            continue
        out.append(_task_hold(conn, row, "child", child))
    own = {r[0] for r in conn.execute(TR.CHILD_IDS, (row["id"],))}
    out += [
        _task_hold(conn, row, "blocker", b)
        for b in TR.live_blockers(conn, row["id"], H.ACTIVE_STATUSES)
        if b["id"] not in own
    ]
    for a in H._rows(
        conn.execute("SELECT * FROM approvals WHERE task_id=? AND decision IS NULL ORDER BY created_at", (row["id"],))
    ):
        H.approval_wait(conn, a)
        out.append({"kind": "approval", "on": a.get("decision_owner"), "what": f"a {a.get('kind') or 'yes'} approval"})
    from .reconnects import hold as reconnect_hold

    reconnect = reconnect_hold(conn, row["id"])
    if reconnect:
        out.append({"kind": "person", "on": reconnect["on"], "what": reconnect["what"]})
    # The external holds wait_detail reads after these: a worktree, a milestone, a GitHub callback.
    detail = H.wait_detail(conn, {**row, "blocked_kind": None})
    if not out and detail and detail.get("on") == "external":
        out.append({"kind": "external", "on": None, "what": _line(detail["why"])})
    return out


def _task_hold(conn, row, kind, other):
    """A child or blocker as a hold. A private one is named by neither title nor owner: the reason is shown to everyone
    who reads the task (its readers and its parties' managers too), and not all of them read the other task. A private
    subtask whose words reach its private parent's readers (hubdb.subtask_flows_up: the person a bot asked under its
    task) is named there."""
    from . import hubdb as H

    if H.task_private(conn, other) and not H.subtask_flows_up(conn, other, row):
        return {"kind": "private", "on": None, "what": "a private task"}
    return {"kind": kind, "on": other["owner_id"], "what": _line(other["title"]), "task": other["id"]}


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


def reason(conn, row):
    """The reason a task entering a wait shows (task_blocked.transition), or None to keep the caller's own."""
    found = holds(conn, row)
    return (words(conn, found), "dependency") if found else (NOTHING, "stranded")


def _applies(conn, row):
    from . import hubdb as H

    if not row or row.get("status") != "waiting" or not row.get("blocked_since"):
        return False
    if row.get("blocked_kind") == "manual" or row.get("blocked_kind") in OWN_RECONCILE:
        return False
    return (H.type_get(conn, row.get("type_id")) or {}).get("role") != "product-roadmap"


def refresh(conn, task_id, cleared=None, at=None):
    """Recompute a waiting task's reason from its holds. `cleared` names a hold of this task that just went away (a
    child done, a question answered or withdrawn, an approval decided): when it was the last,
    a bot's task goes back to work and a person's says there is nothing left to wait on. Without `cleared` (a note,
    a new hold) only the words change. Returns what happened: "held", "open", "resumed", "nothing" or None."""
    from . import hubdb as H, review_gate, task_blocked

    row = H.task(conn, task_id)
    if not _applies(conn, row):
        return None
    at = at or H.now()
    found = holds(conn, row)
    if found:
        text = words(conn, found)
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
        # The restore is automatic, so the stage's rules may refuse it: then only the restore is undone, it is logged on the
        # parent (auto_move_refused), and the child's change that cleared the hold stands.
        if not H.try_auto_move(conn, H.KEEPER, row, status="open", note=note):
            return None
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
            change(
                conn,
                task_id,
                "blocked",
                reason=text[:1000],
                cause="stranded",
                cleared_at=at if cleared else row.get("holds_cleared_at"),
            )
            task_blocked.bump(conn, row)
    return "nothing"


def refresh_private_names(conn):
    """Make again the Blocked reason of each waiting private task with an open private subtask: whom that reason may
    name follows who reads both (_task_hold), and a group's members are readers by the group. Called when a group or its
    members change (groups._finish); a task's own reader list refreshes it there (hubdb.set_task_readers)."""
    from . import hubdb as H

    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    refresh_many(
        conn,
        [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT r.to_task FROM task_relations r JOIN tasks p ON p.id=r.to_task JOIN tasks k ON k.id=r.from_task "
                "WHERE r.kind='parent' AND p.status='waiting' AND p.private IS NOT 0 AND k.private IS NOT 0 "
                f"AND k.status IN ({marks})",
                H.ACTIVE_STATUSES,
            )
        ],
    )


def refresh_many(conn, task_ids, cleared=None):
    for task_id in dict.fromkeys(t for t in task_ids if t):
        refresh(conn, task_id, cleared)


def recompute_reasons(conn):
    """The one-time repair (migration 20261107140000): each waiting task's open hold that names a person gets the reason
    its holds give now (`words`, or NOTHING when none is left). That is a dependency hold, and a hold a keeper or person
    set by hand whose reason names someone ("Waiting on ..."); a hold set by hand over a step ("In Review") keeps its
    reason. Only the reason text changes: the hold, its cause, its clock, its resume step and the task's status stay as
    they are, so nothing clears, wakes, is messaged or touches an approval. Returns how many reasons changed."""
    from . import hubdb as H

    changed = 0
    for task_id in [
        r[0]
        for r in conn.execute(
            "SELECT t.id FROM tasks t JOIN task_holds h ON h.task_id=t.id AND h.kind='blocked' AND h.released_at IS NULL "
            "WHERE t.status='waiting' AND (h.cause='dependency' OR (h.cause='manual' AND h.reason LIKE 'Waiting on %')) "
            "ORDER BY t.created_at,t.id"
        ).fetchall()
    ]:
        row = H.task(conn, task_id)
        if row is None or not _repairable(conn, row):
            continue
        found = holds(conn, row)
        text = (words(conn, found) if found else NOTHING)[:1000]
        if text != row.get("blocked_reason"):
            change(conn, task_id, "blocked", reason=text)
            changed += 1
    return changed


def _repairable(conn, row):
    """A waiting task whose hold the repair may re-word: the same as _applies, but a hold set by hand (blocked_kind
    manual) is in scope, since the repair only rewords it and never clears it."""
    from . import hubdb as H

    if not row or row.get("status") != "waiting" or not row.get("blocked_since"):
        return False
    if row.get("blocked_kind") in OWN_RECONCILE:
        return False
    return (H.type_get(conn, row.get("type_id")) or {}).get("role") != "product-roadmap"


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


# A bot's task waiting on nothing tracked (cause `stranded`) has nothing that will ever move it: a note wakes nobody.
GRACE_HOURS = 2  # how long it may wait on nothing before it goes back to open
REOPEN_LIMIT = 3  # reopened this often in 24 hours: it stays parked and its bot's manager is told instead
PASS_LIMIT = 50  # tasks reopened per hourly pass
REOPENED = (
    f"Nothing tracked to wait on for {GRACE_HOURS} h; back to work. File a subtask, relate a blocker, "
    "name a person with --on, or set --due to wait."
)
TOLD = "stranded_told"  # the task history kind of a manager's notice about one such task


def _manager(conn, row):
    """Who hears about a bot's task that keeps going back to waiting on nothing: its bot's manager when that is an
    active bot that reads the task, else BotOps; never a person."""
    from . import hubdb as H

    config = (
        conn.execute("SELECT reports_to FROM bot_config WHERE bot=?", (H.actor_id(row["owner_id"]),)).fetchone()
        if H._has_table(conn, "bot_config")
        else None
    )
    target = (config[0] if config else "") or ""
    target = target if not target or H.actor_kind(target) else H.bot_actor(target)
    for who in (target, H.bot_actor(H.FLEET_MAINTAINER)):
        if (
            who
            and H.is_bot(who)
            and who != row["owner_id"]
            and (H.bot(conn, H.actor_id(who)) or {}).get("state") == "active"
            and H.bot_reads_task(conn, who, row)
        ):
            return who
    return None


def unstrand(conn, at=None):
    """Every hour: a bot's task waiting on nothing tracked for GRACE_HOURS (from when the wait on nothing began or its
    last hold cleared) goes back to open with a note saying how to wait properly, unless a due date or snooze says to
    look later. Each bot is woken once, however many of its tasks moved. A task sent back REOPEN_LIMIT times in 24
    hours is left waiting and its bot's manager (a bot) or BotOps hears once a day instead. A hold by hand, Engineering's
    own holds, roadmap items and people's tasks are never touched. Returns {"opened", "woken", "told"}."""
    from . import hubdb as H, review_gate, task_blocked

    at = at or H.now()
    day_ago = H.shift(at, hours=-24)
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT t.id FROM task_holds h JOIN tasks t ON t.id=h.task_id WHERE h.kind='blocked' AND h.released_at IS NULL "
            "AND h.cause='stranded' AND t.status='waiting' AND t.owner_id LIKE 'bot:%' "
            # A card On ice (D69) waits for the person who parked it: the pass leaves it, as every sweep does.
            f"AND NOT {H.ON_ICE % 't'} "
            "AND coalesce(h.cleared_at,h.created_at)<=? ORDER BY h.created_at,t.id",
            (H.shift(at, hours=-GRACE_HOURS),),
        )
    ]
    opened, told, count = {}, {}, 0
    for task_id in ids:
        if count >= PASS_LIMIT:
            break
        row = H.task(conn, task_id)
        if not _applies(conn, row) or row.get("blocked_kind") != "stranded" or H.not_yet_due(row, at):
            continue
        if (H.bot(conn, row["owner_id"]) or {}).get("state") != "active":
            continue
        if holds(conn, row):
            refresh(conn, task_id, at=at)  # a hold its last refresh missed: the words follow, it stays
            continue
        again = conn.execute(
            "SELECT count(*) FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='status' "
            "AND actor_id=? AND note=? AND created_at>=?",
            (task_id, H.KEEPER, REOPENED, day_ago),
        ).fetchone()[0]
        if again >= REOPEN_LIMIT:
            if not conn.execute(
                "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? "
                "AND created_at>=? LIMIT 1",
                (task_id, TOLD, day_ago),
            ).fetchone():
                who = _manager(conn, row)
                if who:
                    told.setdefault(who, []).append(row)
                    H._task_event(
                        conn,
                        task_id,
                        H.KEEPER,
                        TOLD,
                        None,
                        who,
                        f"Back to waiting on nothing {again} times in 24 hours",
                    )
            continue
        engineering = review_gate.engineering(conn, row)
        status = (
            row.get("blocked_from_status")
            if engineering and row.get("blocked_from_status") in ("open", "doing")
            else "open"
        )
        H._set_status(conn, H.KEEPER, row, status, note=REOPENED)
        if H.task(conn, task_id)["status"] == "waiting":
            refresh(conn, task_id, at=at)  # an external hold keeps it (task_blocked.wait)
            continue
        conn.execute("UPDATE tasks SET note=? WHERE id=?", (REOPENED, task_id))
        task_blocked.bump(conn, row)
        H._task_event(conn, task_id, H.KEEPER, "note", row.get("note"), REOPENED, "")
        opened.setdefault(row["owner_id"], []).append(row)
        count += 1
    for owner, rows in opened.items():
        # One wake per bot, in its first task's room: it names only that task, never another task's title.
        more = len(rows) - 1
        H._wake(
            conn,
            H.task(conn, rows[0]["id"]),
            owner,
            f"Open: {rows[0]['title']}\n{REOPENED}"
            + (f"\n{more} more of your tasks waiting on nothing are open again too." if more else ""),
            refs={"news": "assignment"},
        )
    for who, rows in told.items():
        more = len(rows) - 1
        first = H.task(conn, rows[0]["id"])
        H._wake(
            conn,
            first,
            who,
            f"Stuck: {first['title']}\n{name(conn, first['owner_id'])} has parked this task with nothing tracked "
            f"to wait on, and it went back to open {REOPEN_LIMIT} times in 24 hours; it now stays waiting. Help: "
            "file the subtask or blocker it waits on, name the person, set a due date, or close it."
            + (f"\n{more} more such tasks are left waiting too." if more else ""),
            refs={"news": "stranded_loop"},
        )
    return {
        "opened": sum(len(r) for r in opened.values()),
        "woken": len(opened),
        "told": sum(len(r) for r in told.values()),
    }


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
    for chunk in (snoozed[i : i + 500] for i in range(0, len(snoozed), 500)):
        for e in conn.execute(
            f"SELECT subject_id AS task_id,max(created_at) created_at FROM history_events WHERE subject_type='task' "
            f"AND kind='snoozed_until' AND new IS NOT NULL "
            f"AND subject_id IN ({','.join('?' * len(chunk))}) GROUP BY subject_id",
            chunk,
        ):
            found.setdefault(e["task_id"], {})["snoozed_since"] = e["created_at"]
    for chunk in (manual[i : i + 500] for i in range(0, len(manual), 500)):
        # An owner's hold is the latest move a person or bot made into it: the move to Blocked, or a step move that
        # declared the hold over one Tico found (task_blocked.transition).
        for e in conn.execute(
            f"SELECT subject_id AS task_id,actor_id AS actor,max(created_at) created_at FROM history_events "
            f"WHERE subject_type='task' AND kind IN ('status','step') AND actor_id<>? "
            f"AND subject_id IN ({','.join('?' * len(chunk))}) GROUP BY subject_id",
            (H.KEEPER, *chunk),
        ):
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
        found.append(
            {
                "kind": "blocked",
                "reason": row.get("blocked_reason"),
                "since": row["blocked_since"],
                "until": None,
                "by": mark.get("blocked_by") if cause == "manual" else H.KEEPER,
                "cause": cause,
            }
        )
    if row.get("snoozed_until"):
        found.append(
            {
                "kind": "snoozed",
                "reason": row.get("snoozed_note"),
                "since": mark.get("snoozed_since"),
                "until": row["snoozed_until"],
                "by": row.get("snoozed_by_id"),
            }
        )
    return found


# ----------------------------------------------------------------------------- the rows (migration 0075)
# A task's open hold of each kind is one task_holds row. Code reads a task with its holds' fields under the names
# below (hubdb.TASKS): a hold's column -> the field a task row carries.
FIELDS = {
    "blocked": {
        "blocked_since": "created_at",
        "blocked_reason": "reason",
        "blocked_kind": "cause",
        "blocked_from_step": "from_step_id",
        "blocked_from_status": "from_status",
        "blocked_gate": "gate",
        "holds_cleared_at": "cleared_at",
    },
    "snoozed": {"snoozed_until": "until_at", "snoozed_by_id": "by_id", "snoozed_note": "reason"},
}
COLUMNS = ("cause", "reason", "by_id", "until_at", "from_step_id", "from_status", "gate", "cleared_at")
SHOWN = ("created_at", *COLUMNS)


def open_hold(conn, task_id, kind):
    """The task's open hold of `kind` ('blocked' or 'snoozed'), or None."""
    row = conn.execute(
        f"SELECT {', '.join(SHOWN)} FROM task_holds WHERE task_id=? AND kind=? AND released_at IS NULL", (task_id, kind)
    ).fetchone()
    return dict(zip(SHOWN, row, strict=True)) if row is not None else None


def put(conn, task_id, kind, at, **fields):
    """Open a hold of `kind` on the task from `at`. A task has one open hold of each kind (task_holds_open): one already
    open takes `at` and these fields instead, as writing the old columns again did."""
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"task_holds has no {sorted(unknown)}")
    names = ["task_id", "kind", "created_at", *fields]
    conn.execute(
        f"INSERT INTO task_holds({','.join(names)}) VALUES({','.join('?' * len(names))}) "
        "ON CONFLICT(task_id, kind) WHERE released_at IS NULL DO UPDATE SET "
        + ",".join(f"{name}=excluded.{name}" for name in names[2:]),
        (task_id, kind, at, *fields.values()),
    )


def change(conn, task_id, kind, **fields):
    """Set fields of the task's open hold of `kind`; nothing when it has none."""
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"task_holds has no {sorted(unknown)}")
    if fields:
        conn.execute(
            f"UPDATE task_holds SET {','.join(f'{name}=?' for name in fields)} "
            "WHERE task_id=? AND kind=? AND released_at IS NULL",
            (*fields.values(), task_id, kind),
        )


def release(conn, task_id, kind, at):
    """End the task's open hold of `kind`: it stays as history with `released_at`."""
    conn.execute(
        "UPDATE task_holds SET released_at=? WHERE task_id=? AND kind=? AND released_at IS NULL", (at, task_id, kind)
    )


def with_holds(conn, row):
    """`row` with its open holds' fields (FIELDS), read when it came from a plain `SELECT * FROM tasks`."""
    if row is None or "blocked_since" in row or not row.get("id"):
        return row
    out = {**row, **{field: None for names in FIELDS.values() for field in names}}
    for kind, *values in conn.execute(
        f"SELECT kind, {', '.join(SHOWN)} FROM task_holds WHERE task_id=? AND released_at IS NULL", (row["id"],)
    ):
        hold = dict(zip(SHOWN, values, strict=True))
        out.update({field: hold[column] for field, column in FIELDS[kind].items()})
    return out
