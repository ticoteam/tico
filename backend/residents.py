"""Resident sessions: a provider process a runner keeps alive between one session's turns.

A runner that advertises `resident_threads_v1` may keep the process that ran a turn and hand it the session's next
turn instead of starting a new one. Only the process is reused: every turn is still claimed, started and completed
as an ordinary attempt, with its own lease, lane and start place. A resident between turns holds none of them, so it
never counts against claim limits, never keeps a bot busy, and never blocks a transfer or a cloud machine stop.

`resident_sessions.id` is the reservation's incarnation. A runner that restarts registers a new one (`replaces`), so a
late renew, release or bind from the lost process names the old id and never touches its replacement. One open
reservation per (bot, session key, login binding) keeps one writer per provider session.

The session key is the turn's privacy scope (clients/sessions.py session_key). The server admits the keys the turn
may continue under any session mode (`admissible`) rather than recomputing the runner's config-dependent choice: a
key outside the turn's scope is refused, and a turn binds only to a resident whose key its own scope admits, so a
private thread's process never serves a public turn or the other way round.

A revocation (`revoke`) ends the residents it touches in the same write as the change, and fences every live turn
bound to them (`resident_turns.fenced_at`): from then on the turn gets no tool authority or credentials
(auth.validate_identity, the runner Gateway credential), no start, no new input and no accepted output, and its
answer is never posted. Its completion is still accepted so its usage is counted. A bot leaving `active`, a bot moved
or unassigned and a computer whose credential is revoked are fenced by triggers in the migration, whichever code path
makes the change. A runner learns of every stop from renew, bind or `reconcile`.
"""

from collections.abc import Iterable
from typing import Any

from .store import H, Problem

CAPABILITY = "resident_threads_v1"
# A resident renews about every minute; one unheard from for this long is closed the next time anything reads it.
LEASE_SECONDS = 300
RENEW_SECONDS = 60

# What a stopped turn's notice says stopped it, by revocation reason (quarantine_stops.resident).
WHY = {
    "thread_member_removed": "it was removed from this thread",
    "audience_changed": "this thread's audience changed",
    "task_access_changed": "who can read this task changed",
    "group_changed": "its group membership changed",
    "credential_revoked": "a credential it used was removed",
    "sign_in_changed": "its computer's sign-in changed",
    "person_left": "the person it works for no longer has access",
    "access_changed": "who can use it changed",
}

OPEN = "state <> 'closed'"


def enabled(runner):
    """Whether this runner row advertises resident sessions."""
    from .execution import capability_set

    return bool(runner) and CAPABILITY in capability_set(runner["capabilities"])


def _runner(execution, c, who):
    runner = execution.runner(c, who)
    if not runner:
        raise Problem("identity", "A registered runner is required", 403)
    if not enabled(runner):
        # An older runner never calls these; a runner that dropped the capability on a refusal must not either.
        raise Problem("resident_threads_off", "This computer has not turned on resident sessions", 409)
    return runner


def view(row):
    return {
        "id": row["id"],
        "bot_id": row["bot_id"],
        "session_key": row["session_key"],
        "binding": row["binding"],
        "provider_session_id": row["provider_session_id"],
        "state": row["state"],
        "expires_at": row["expires_at"],
        "renew_seconds": RENEW_SECONDS,
        "stop": row["state"] == "closed",
        **({"stop_reason": row["stop_reason"], "revoked": bool(row["revoked_at"])} if row["state"] == "closed" else {}),
    }


def fenced(c, aid):
    """Whether a revocation fenced this turn: it may not start, act, take input or have its answer posted."""
    row = c.execute("SELECT fenced_at FROM resident_turns WHERE attempt_id=?", (aid,)).fetchone()
    return bool(row and row[0])


def refuse_fenced(c, aid):
    """Raise the stop every runner already obeys (stale_lease) for a fenced turn."""
    if fenced(c, aid):
        raise Problem("stale_lease", "This turn's access was revoked; stop this attempt", 409)


def admissible(c, row):
    """The session keys this turn may continue, by its privacy scope: its task's own session (or its session root's),
    its thread tree's private or public session, and on a public thread the bot's shared public sessions. The first is
    the default the server records when the runner names none."""
    from .execution import thread_payload, work_task_id

    job = c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone() if row["job_id"] else None
    msg = H.message(c, job[0], include_deleted=True) if job else None
    if not msg:
        return []
    conv = H.conversation(c, msg["conversation_id"])
    task_id = work_task_id(msg, conv)
    task = H.task(c, task_id) if task_id else None
    payload = thread_payload(c, conv, msg) or {}
    root, audience = payload.get("root_id"), payload.get("audience_class")
    # Mirrors clients/sessions.py session_key for a turn with a thread root, across every session mode.
    if task:
        tid = str(task_id)
        keys = ["task:" + str(H.session_root(c, row["bot_id"], tid) or tid), "task:" + tid]
        if audience == "public" and not H.task_private(c, task):
            keys.append("bot")
    elif root:
        keys = [f"root:{root}:{'public' if audience == 'public' else 'private'}"]
        if audience == "public":
            keys.append("public")
    else:
        keys = []
    return list(dict.fromkeys(keys))


def _close(c, ids, reason, *, revoked, fence):
    """End these open residents; a revocation (`fence`) also fences their live turns and stops them."""
    if not ids:
        return []
    now = H.now()
    marks = ",".join("?" * len(ids))
    stopped = []
    if fence:
        from . import quarantine_stops

        live = c.execute(
            "SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation FROM resident_turns rt "
            "JOIN attempts a ON a.id=rt.attempt_id LEFT JOIN assignments x ON x.bot=a.bot_id "
            f"WHERE rt.resident_session_id IN ({marks}) AND rt.fenced_at IS NULL AND a.state IN ('leased','running')",
            ids,
        ).fetchall()
        c.execute(
            f"UPDATE resident_turns SET fenced_at=?, fence_reason=? WHERE resident_session_id IN ({marks}) "
            "AND fenced_at IS NULL",
            (now, reason, *ids),
        )
        line = "Stopped: " + WHY.get(reason, "its access changed")
        for row in live:
            # The renew answer carries the stop at once; the turn's job goes back in the queue to start fresh
            # (quarantine_stops.then), unless it may already have done something outside Tico.
            quarantine_stops.resident(c, row, line)
            stopped.append(row["id"])
    c.execute(
        f"UPDATE resident_sessions SET state='closed', stopped_at=?, stop_reason=?, updated_at=?, revoked_at=? "
        f"WHERE id IN ({marks}) AND {OPEN}",
        (now, reason, now, now if revoked else None, *ids),
    )
    H.event(c, H.TICO_ACTOR, "resident.stop", ids[0], {"residents": ids, "reason": reason, "fenced": stopped})
    return stopped


def _lapse(c, where, args):
    """Close the open residents matching `where` whose runner stopped renewing them. Not a revocation: their turns
    are ordinary attempts with leases of their own."""
    now = H.now()
    ids = [
        r[0]
        for r in c.execute(
            f"SELECT id FROM resident_sessions WHERE {OPEN} AND expires_at<=? AND {where}", (now, *args)
        ).fetchall()
    ]
    _close(c, ids, "expired", revoked=False, fence=False)


def revoke(
    c: Any,
    reason: str,
    *,
    bot: str | None = None,
    runner: str | None = None,
    roots: Iterable[str] = (),
    tasks: Iterable[str] = (),
    everyone: bool = False,
    fence: bool = True,
) -> list[str]:
    """End the residents a change of access touches, in the change's own write: one bot's (`bot`), one computer's
    (`runner`), the sessions of these thread trees or tasks (`roots`, `tasks`), or every one (`everyone`). A thread
    or task scope also ends the bots' shared public sessions, which may have read it. `fence=False` (a cleared
    session) ends the processes without stopping their turns. Returns the turns stopped. Free when none is open, as
    on every server whose runners have not turned resident sessions on."""
    if not c.execute(f"SELECT 1 FROM resident_sessions WHERE {OPEN} LIMIT 1").fetchone():
        return []
    where, args = [], []
    if bot:
        where.append("bot_id=?")
        args.append(bot)
    if runner:
        where.append("runner_id=?")
        args.append(runner)
    scope = []
    for root in roots:
        # Any resident that served a turn in the thread tree ends too: a task's session reads its thread.
        scope.append(
            "session_key LIKE ? ESCAPE '\\' OR session_key=? OR EXISTS (SELECT 1 FROM resident_turns rt "
            "JOIN attempts a ON a.id=rt.attempt_id JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
            "JOIN conversations t ON t.id=m.conversation_id "
            "WHERE rt.resident_session_id=resident_sessions.id AND (t.id=? OR t.root_id=?))"
        )
        args += [_like(f"root:{root}:") + "%", "conversation:" + root, root, root]
    for task in tasks:
        # A subtask's turns run in its session root's session, so a resident that served any turn on the task ends too.
        scope.append(
            "session_key=? OR EXISTS (SELECT 1 FROM resident_turns rt JOIN attempts a ON a.id=rt.attempt_id "
            "WHERE rt.resident_session_id=resident_sessions.id AND a.task_id=?)"
        )
        args += ["task:" + task, task]
    if scope:
        scope.append("session_key IN ('public','bot')")
        where.append("(" + " OR ".join(scope) + ")")
    if not where and not everyone:
        return []
    ids = [
        r[0]
        for r in c.execute(
            f"SELECT id FROM resident_sessions WHERE {OPEN}" + "".join(" AND " + w for w in where), args
        ).fetchall()
    ]
    return _close(c, ids, reason, revoked=fence, fence=fence)


def revoke_for(c: Any, reason: str, subject: object) -> list[str]:
    """`revoke` for what a grant or a key was given to: one bot's residents for `bot:<id>`, every one otherwise (a
    group, a person, the company), since which bots act under it is not cheap to tell here and a stopped resident
    only costs its next turn a fresh start."""
    subject = str(subject or "")
    if subject.startswith("bot:"):
        return revoke(c, reason, bot=subject[4:])
    return revoke(c, reason, everyone=True)


def _like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _own(c, who, rid):
    row = c.execute("SELECT * FROM resident_sessions WHERE id=?", (rid,)).fetchone()
    if not row or row["runner_id"] != who.runner_id:
        raise Problem("not_found", "No such resident session on this computer", 404)
    return row


def _read(c, rid):
    return c.execute("SELECT * FROM resident_sessions WHERE id=?", (rid,)).fetchone()


def _still_assigned(c, row):
    """Whether the resident's bot is still active and assigned to its computer at its generation. The triggers keep
    this true for every open resident; checked again so a missed path stops it rather than serving."""
    x = c.execute(
        "SELECT x.runner_id, x.generation, b.state FROM assignments x JOIN bots b ON b.id=x.bot WHERE x.bot=?",
        (row["bot_id"],),
    ).fetchone()
    return bool(x and x[0] == row["runner_id"] and x[1] == row["generation"] and x[2] == "active")


def _key(c, row, wanted):
    keys = admissible(c, row)
    if not keys:
        raise Problem("resident_refused", "This turn has no session a resident can keep", 409)
    if wanted is not None and wanted not in keys:
        raise Problem("session_key_mismatch", "This session key is outside this turn's audience", 409)
    return wanted or keys[0], keys


def register(execution, c, who, aid, body):
    """Reserve the process that runs this ordinary, authorized turn as the resident of its session."""
    runner = _runner(execution, c, who)
    row = execution.attempt(c, who, aid)
    refuse_fenced(c, aid)
    if row["stop_requested_at"]:
        raise Problem("resident_refused", "This turn is stopping", 409)
    key, _ = _key(c, row, body.session_key)
    bound = c.execute("SELECT resident_session_id FROM resident_turns WHERE attempt_id=?", (aid,)).fetchone()
    _lapse(c, "bot_id=?", (row["bot_id"],))
    held = c.execute(
        f"SELECT * FROM resident_sessions WHERE bot_id=? AND session_key=? AND binding=? AND {OPEN}",
        (row["bot_id"], key, body.binding),
    ).fetchone()
    if held and bound and bound[0] == held["id"] and held["provider_session_id"] == body.provider_session_id:
        return view(held)  # a retried register
    if bound:
        raise Problem("resident_turn_bound", "This turn already belongs to a resident session", 409)
    if held:
        if held["id"] != body.replaces or held["runner_id"] != runner["id"]:
            raise Problem("resident_taken", "Another process already keeps this session", 409)
        # The runner lost the process behind its old incarnation; the old one's turns end with their own leases.
        _close(c, [held["id"]], "replaced", revoked=False, fence=False)
    now = H.now()
    rid = H.new_id()
    c.execute(
        "INSERT INTO resident_sessions(id,bot_id,runner_id,generation,session_key,binding,provider_session_id,runtime,"
        "source_attempt_id,state,created_at,updated_at,renewed_at,expires_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,'live',?,?,?,?)",
        (
            rid,
            row["bot_id"],
            runner["id"],
            row["generation"],
            key,
            body.binding,
            body.provider_session_id,
            row["handed_runtime"] or "",
            aid,
            now,
            now,
            now,
            H.shift(now, seconds=LEASE_SECONDS),
        ),
    )
    c.execute("INSERT INTO resident_turns(attempt_id,resident_session_id,created_at) VALUES(?,?,?)", (aid, rid, now))
    H.event(c, who.actor, "resident.register", rid, {"bot": row["bot_id"], "attempt_id": aid, "key": key})
    return view(_read(c, rid))


def renew(execution, c, who, rid, body):
    """Keep a resident: the answer says stop when it was closed, revoked, lapsed or its bot moved."""
    _runner(execution, c, who)
    row = _own(c, who, rid)
    if row["state"] != "closed":
        if row["expires_at"] <= H.now():
            _close(c, [rid], "expired", revoked=False, fence=False)
        elif not _still_assigned(c, row):
            _close(c, [rid], "bot_moved", revoked=True, fence=True)
        else:
            now = H.now()
            c.execute(
                "UPDATE resident_sessions SET state=?, provider_session_id=COALESCE(?,provider_session_id), "
                "renewed_at=?, expires_at=?, updated_at=? WHERE id=?",
                (body.state, body.provider_session_id, now, H.shift(now, seconds=LEASE_SECONDS), now, rid),
            )
    return view(_read(c, rid))


def release(execution, c, who, rid, body):
    """The runner ended this incarnation's process. Names one incarnation, so a late release from a process the
    runner already replaced leaves the replacement alone."""
    _runner(execution, c, who)
    row = _own(c, who, rid)
    if row["state"] != "closed":
        _close(c, [rid], body.reason or "released", revoked=False, fence=False)
    return view(_read(c, rid))


def bind(execution, c, who, aid, body):
    """Hand this claimed turn to a live resident of its session. Refused (the runner starts the turn the ordinary
    way) when the resident stopped, lapsed or belongs to another bot, computer, generation or audience."""
    _runner(execution, c, who)
    row = execution.attempt(c, who, aid)
    if row["stop_requested_at"]:
        raise Problem("resident_refused", "This turn is stopping", 409)
    resident = _own(c, who, body.resident_id)
    bound = c.execute("SELECT resident_session_id FROM resident_turns WHERE attempt_id=?", (aid,)).fetchone()
    if bound:
        if bound[0] != resident["id"]:
            raise Problem("resident_turn_bound", "This turn already belongs to another resident session", 409)
        refuse_fenced(c, aid)
        return {**view(resident), "attempt_id": aid}
    if resident["state"] != "closed" and resident["expires_at"] <= H.now():
        _close(c, [resident["id"]], "expired", revoked=False, fence=False)
        resident = _read(c, resident["id"])
    if resident["state"] == "closed":
        raise Problem(
            "resident_stopped",
            "This resident session was stopped; start the turn fresh",
            409,
            extra={"stop_reason": resident["stop_reason"]},
        )
    if resident["bot_id"] != row["bot_id"] or resident["generation"] != row["generation"]:
        raise Problem("resident_mismatch", "This resident session belongs to other work", 409)
    if resident["session_key"] not in admissible(c, row):
        raise Problem("session_key_mismatch", "This resident session is outside this turn's audience", 409)
    now = H.now()
    c.execute(
        "INSERT INTO resident_turns(attempt_id,resident_session_id,created_at) VALUES(?,?,?)",
        (aid, resident["id"], now),
    )
    c.execute(
        "UPDATE resident_sessions SET state='live', renewed_at=?, expires_at=?, updated_at=? WHERE id=?",
        (now, H.shift(now, seconds=LEASE_SECONDS), now, resident["id"]),
    )
    H.event(c, who.actor, "resident.bind", resident["id"], {"bot": row["bot_id"], "attempt_id": aid})
    return {**view(_read(c, resident["id"])), "attempt_id": aid}


def reconcile(execution, c, who, body):
    """A runner's check-in after reconnecting: it names every resident it holds. The answer lists the ones it must
    stop (closed, revoked, lapsed, unknown), the turns it must stop because a revocation fenced them, and renews the
    rest. A resident the server has open on this computer that the runner no longer holds is closed."""
    runner = _runner(execution, c, who)
    _lapse(c, "runner_id=?", (runner["id"],))
    held = {r.id: r.state for r in body.residents}
    open_ids = [
        r[0]
        for r in c.execute(f"SELECT id FROM resident_sessions WHERE runner_id=? AND {OPEN}", (runner["id"],)).fetchall()
    ]
    _close(c, [rid for rid in open_ids if rid not in held], "not_held", revoked=False, fence=False)
    keep, stops = [], []
    now = H.now()
    for rid, state in held.items():
        row = _read(c, rid)
        if not row or row["runner_id"] != runner["id"]:
            stops.append({"id": rid, "stop_reason": "unknown", "revoked": False})
            continue
        if row["state"] != "closed" and not _still_assigned(c, row):
            _close(c, [rid], "bot_moved", revoked=True, fence=True)
            row = _read(c, rid)
        if row["state"] == "closed":
            stops.append({"id": rid, "stop_reason": row["stop_reason"], "revoked": bool(row["revoked_at"])})
            continue
        c.execute(
            "UPDATE resident_sessions SET state=?, renewed_at=?, expires_at=?, updated_at=? WHERE id=?",
            (state, now, H.shift(now, seconds=LEASE_SECONDS), now, rid),
        )
        keep.append(view(_read(c, rid)))
    fenced_turns = [
        r[0]
        for r in c.execute(
            "SELECT rt.attempt_id FROM resident_turns rt JOIN resident_sessions s ON s.id=rt.resident_session_id "
            "JOIN attempts a ON a.id=rt.attempt_id WHERE s.runner_id=? AND rt.fenced_at IS NOT NULL "
            "AND a.state IN ('leased','running')",
            (runner["id"],),
        ).fetchall()
    ]
    return {"residents": keep, "stops": stops, "fenced_attempts": fenced_turns, "renew_seconds": RENEW_SECONDS}
