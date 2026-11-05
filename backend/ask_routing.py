"""Questions need someone who can answer: system actors have no inbox."""
from . import hubdb as H
from . import reach, task_privacy
from .auth import Identity
from .store import Problem


def readable(c, target):
    """A person still on the chart, or a bot that can run."""
    if H.is_human(target):
        from .auth import standing
        return bool(H.human(c, target)) and standing(c, H.actor_id(target)) != "left"
    return H.is_bot(target) and (H.bot(c, target) or {}).get("state") == "active"


def resolve(c, actor, target, refs=None, conversation_id=None, in_reply_to=None, source=None):
    """Route a system question up the asker's chart, preserving reach and private sources."""
    resolved = H.resolve_actor(c, target)
    refs = dict(refs or {})
    if not resolved or H.is_human(resolved) or H.is_bot(resolved):
        return target, refs
    ctx = reach.context(c)
    managers = reach._managers(ctx)
    seen, candidates = {actor}, []
    at = managers.get(actor)
    while at and at not in seen:
        seen.add(at)
        candidates.append(at)
        at = managers.get(at)
    if ctx["owner"] not in seen and ctx["owner"]:
        candidates.append(ctx["owner"])
    who = Identity(actor, H.actor_kind(actor))
    last = None
    for candidate in candidates:
        if candidate == actor or not readable(c, candidate):
            continue
        try:
            reach.check(c, who, candidate, "chat", task_id=H.refs_task_id(refs),
                        conversation_id=conversation_id, in_reply_to=in_reply_to, ctx=ctx)
            task_privacy.require_destination(c, who, candidate, None, refs,
                                             H.message(c, in_reply_to) if in_reply_to else None)
            row = H.task(c, H.refs_task_id(refs) or "")
            if row and H.is_bot(candidate) and not H.bot_reads_task(c, candidate, row):
                continue
            if source and not (task_privacy.readable(c, candidate, task_privacy.message_tasks(c, source))
                               and task_privacy.meeting_sources(c).message_readable(candidate, source)):
                continue
        except Problem as exc:
            last = exc
            continue
        return candidate, {**refs, "rerouted_from": resolved, **({"who": candidate} if "who" in refs else {})}
    if last:
        raise last
    raise Problem("recipient", "The system cannot answer. Set a manager or company owner who can read the question.", 409)


def notice(c, message):
    before = (message.get("refs") or {}).get("rerouted_from")
    return f"{before} is the system; sent to {H.actor_name(c, message['recipient_id'])}" if before else None


def repair(c):
    """Re-send each open system ask once; retain its original and link both records."""
    from . import task_holds, threads
    moved = []
    for row in c.execute("SELECT m.* FROM messages m WHERE m.kind='ask' AND m.deleted_at IS NULL "
                         "AND coalesce(m.recipient_id,'') NOT LIKE 'human:%' "
                         f"AND coalesce(m.recipient_id,'') NOT LIKE 'bot:%' AND {H.OPEN_ASK_SQL} "
                         "ORDER BY m.created_at").fetchall():
        old = dict(row)
        refs = H._json(old["refs"], {}) or {}
        old["refs"] = refs
        conv = H.conversation(c, old["conversation_id"])
        tid = H.message_task_id(old, conv)
        task = H.task(c, tid) if tid else None
        if task and task["status"] in H.TASK_ENDED:
            continue
        if tid and not H.refs_task_id(refs):
            refs["task"] = tid
        try:
            target, refs = resolve(c, old["sender_id"], old["recipient_id"], refs,
                                   old["conversation_id"], old["in_reply_to"], source=old)
        except Problem:
            continue                         # Health names the hold when no safe recipient exists.
        if target == old["recipient_id"]:
            continue
        if not threads.readable(c, threads.actor_viewer(c, target), conv["id"]):
            conv = H._pair_conversation(c, old["sender_id"], target, "ask", allow_held=True)
        at = H.now()
        refs.update(resent_from=old["id"], asked_again_at=at)
        c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (at, old["id"]))
        new = H._write_message(c, old["sender_id"], target, old["body"], conv, "ask", refs,
                               old["in_reply_to"], old["wait_s"])
        c.execute("UPDATE messages SET refs=? WHERE id=?",
                  (H._dump({**(H._json(old["refs"], {}) or {}), "resent_as": new["id"]}), old["id"]))
        if task:
            if task.get("waiting_on") == old["recipient_id"]:
                c.execute("UPDATE tasks SET waiting_on=? WHERE id=?", (target, tid))
                H._task_event(c, tid, H.KEEPER, "waiting_on", old["recipient_id"], target, notice(c, new))
            task_holds.refresh(c, tid)
            if (task.get("blocked_reason") or "").startswith("Waiting on " + old["recipient_id"]):
                task_holds.change(c, tid, "blocked", reason=task_holds.words(c, task_holds.holds(c, H.task(c, tid))))
            H._task_event(c, tid, H.KEEPER, "ask_resent", old["id"], new["id"], notice(c, new))
        H.event(c, H.KEEPER, "ask.resent", old["id"], {"to": target, "message": new["id"], "task": tid})
        moved.append(new["id"])
    return moved
