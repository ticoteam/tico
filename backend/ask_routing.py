"""Questions need someone who can answer: system actors have no inbox."""
import logging

from . import hubdb as H
from . import reach, read_cache, task_privacy
from .auth import Auth, Identity
from .store import Problem


def readable(c, target):
    """A person still on the chart, or a bot with a queue that accepts questions."""
    if H.is_human(target):
        from .auth import standing
        return bool(H.human(c, target)) and not standing(c, H.actor_id(target))
    if not H.is_bot(target) or (H.bot(c, target) or {}).get("state") != "active":
        return False
    from .enqueue import external_bot_sql
    from .harnesses import SYNCED_SQL
    # Synced inboxes take only their person's chat messages. Independent external
    # agents read asks themselves; a synced bot needs its desktop delivery queue.
    return not read_cache.one(c,
        "SELECT EXISTS(SELECT 1 FROM bot_settings WHERE bot_id=? AND harness IN (" + SYNCED_SQL + ")) AND "
        + external_bot_sql("?"), (H.actor_id(target), target))[0]


def can_answer(c, target, refs=None, conversation_id=None, in_reply_to=None, source=None):
    """The same full task read for people and bots, including every source of the question."""
    from .auth import actor_reads_task
    if not readable(c, target):
        return False
    message = source or {"refs": refs or {}, "conversation_id": conversation_id, "in_reply_to": in_reply_to}
    try:
        return (all(actor_reads_task(c, target, H.task(c, tid))
                    for tid in task_privacy.message_tasks(c, message))
                and task_privacy.meeting_sources(c).message_readable(target, message))
    except Problem:
        return False


def require_recipient(c, who, target, refs=None, conversation_id=None, in_reply_to=None, *,
                      kind="ask", source=None, ctx=None):
    """Routing tries the same recipient gates as send before choosing someone."""
    Auth.require_bot_contact(c, who, target, conversation_id, H.refs_task_id(refs), kind="message")
    reach.check(c, who, target, "chat", task_id=H.refs_task_id(refs), conversation_id=conversation_id,
                in_reply_to=in_reply_to, ctx=ctx)
    if kind == "ask" and not can_answer(c, target, refs, conversation_id, in_reply_to, source):
        raise Problem("recipient", "Recipient cannot read or answer the question", 409)
    if conversation_id:
        from . import threads
        from .auth import actor_reads_task
        conv = H.conversation(c, conversation_id)
        if not conv:
            raise Problem("not_found", "Conversation not found", 404)
        if conv.get("audience") == "home":
            allowed = threads.readable(c, threads.actor_viewer(c, target), conv["id"])
        else:
            allowed = target in threads.legacy_participants(c, conv) or (
                conv.get("task_id") and actor_reads_task(c, target, H.task(c, conv["task_id"]))
                and threads.readable(c, threads.actor_viewer(c, target), conv["id"]))
        if not allowed:
            raise Problem("forbidden", "Recipient is not a conversation participant", 403)
    task_privacy.require_destination(c, who, target, conversation_id, refs,
                                     H.message(c, in_reply_to) if in_reply_to else None)


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
        if candidate == actor:
            continue
        try:
            require_recipient(c, who, candidate, refs, conversation_id, in_reply_to, source=source, ctx=ctx)
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


def _carry_links(c, old, new):
    """Move live question pointers; keep the old delivery records as history."""
    for table, columns in (("task_file_reviews", ("ask_message_id", "comment_id")),
                           ("approvals", ("message_id",)),
                           ("task_delegations", ("message_id",)),
                           ("task_check_back_wakes", ("message_id",))):
        if H._has_table(c, table):
            for column in columns:
                c.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (new, old))
    if H._has_table(c, "message_assets"):
        c.execute("INSERT OR IGNORE INTO message_assets(message_id,blob_id) "
                  "SELECT ?,blob_id FROM message_assets WHERE message_id=?", (new, old))


def _repair_one(c, row):
    from . import task_holds
    old = dict(row)
    refs = H._json(old["refs"], {}) or {}
    old["refs"] = refs
    conv = H.conversation(c, old["conversation_id"])
    tid = H.message_task_id(old, conv)
    task = H.task(c, tid) if tid else None
    if task and task["status"] in H.TASK_ENDED:
        return None
    meetings = task_privacy.meeting_sources(c).message(old)
    if meetings:
        old["refs"] = {**refs, "live_meetings": sorted(meetings)}
    # Only the same room preserves its audience, including Home and meeting gates.
    # A different room gets a fresh notice, with no old words, sources or files.
    if task:
        conv = H.conversation(c, task["conversation_id"])
    copy = bool(task and conv["id"] == old["conversation_id"] and not conv.get("closed_at")
                and not old.get("legacy_audience"))
    if copy:
        refs = dict(old["refs"])
        refs.setdefault("task", tid)
        body, reply = old["body"], old["in_reply_to"]
    else:
        refs = {"task": tid} if task else {}
        body = ("Please ask the owner to restate their question here." if task else
                "Please ask the sender to restate their question here.")
        reply = None
    target, refs = resolve(c, old["sender_id"], old["recipient_id"], refs,
                           conv["id"] if task else None, reply, source=old if copy else None)
    if target == old["recipient_id"]:
        return None
    if not task:
        conv = H._pair_conversation(c, old["sender_id"], target, "ask", allow_held=True)
    at = H.now()
    refs.update(resent_from=old["id"], asked_again_at=at)
    c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (at, old["id"]))
    new = H._write_message(c, old["sender_id"], target, body, conv, "ask", refs, reply, old["wait_s"])
    if copy:
        _carry_links(c, old["id"], new["id"])
    c.execute("UPDATE messages SET refs=? WHERE id=?",
              (H._dump({**old["refs"], "resent_as": new["id"]}), old["id"]))
    if task:
        if task.get("waiting_on") == old["recipient_id"]:
            c.execute("UPDATE tasks SET waiting_on=? WHERE id=?", (target, tid))
            H._task_event(c, tid, H.KEEPER, "waiting_on", old["recipient_id"], target, notice(c, new))
        task_holds.refresh(c, tid)
        if (task.get("blocked_reason") or "").startswith("Waiting on " + old["recipient_id"]):
            task_holds.change(c, tid, "blocked", reason=task_holds.words(c, task_holds.holds(c, H.task(c, tid))))
        H._task_event(c, tid, H.KEEPER, "ask_resent", old["id"], new["id"], notice(c, new))
    H.event(c, H.KEEPER, "ask.resent", old["id"], {"to": target, "message": new["id"], "task": tid})
    return new["id"]


def repair(c):
    """Re-send each open system ask atomically; one failed row must never prevent startup."""
    moved = []
    for row in c.execute("SELECT m.* FROM messages m WHERE m.kind='ask' AND m.deleted_at IS NULL "
                         "AND coalesce(m.recipient_id,'') NOT LIKE 'human:%' "
                         f"AND coalesce(m.recipient_id,'') NOT LIKE 'bot:%' AND {H.OPEN_ASK_SQL} "
                         "ORDER BY m.created_at").fetchall():
        c.execute("SAVEPOINT resend_system_ask")
        try:
            mid = _repair_one(c, row)
        except Exception:
            c.execute("ROLLBACK TO resend_system_ask")
            logging.getLogger(__name__).exception("Could not re-send system ask %s; left for Health", row["id"])
        else:
            if mid:
                moved.append(mid)
        finally:
            c.execute("RELEASE resend_system_ask")
    return moved
