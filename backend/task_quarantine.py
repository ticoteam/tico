"""Refusal-count quarantines hold one task or chat when all counted refusals come from it.

Two or more distinct tasks/chats, or any refusal with no attributable source, keep the bot-wide stop. Escape
quarantines always stay bot-wide. A run's own task/chat takes precedence over the target of its refused write.
The hold fences claims like interrupted work does, and uses the same quarantine Stop and review rules. It changes
no task status, snooze or dependency, and ends after the refusal cooldown or a person's quarantine clear.

Each is a `quarantine` hold (backend/holds.py) on the task or the conversation, naming the held bot as its bot_id.
"""

from . import hubdb as H
from .batch_work import isolated


def source(c, actor, task_id=None):
    """The task/chat responsible for this refusal, or None when it cannot be established."""
    if not H.is_bot(actor):
        return None
    if H.RUN.get():
        from .execution import queued_task_sql

        row = c.execute(
            f"SELECT coalesce(a.task_id,{queued_task_sql()}) AS task_id,m.conversation_id "
            "FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
            "JOIN conversations cv ON cv.id=m.conversation_id WHERE a.id=? AND a.bot_id=?",
            (H.RUN.get(), H.actor_id(actor)),
        ).fetchone()
        if row:
            if row["task_id"] is not None and not H.task(c, H._task_ref(row["task_id"])):
                return None
            return "task:" + row["task_id"] if row["task_id"] else "chat:" + row["conversation_id"]
        return None
    task_id = H._task_ref(task_id)
    return "task:" + task_id if task_id and H.task(c, task_id) else None


def _held_sql(bot_sql, task_sql, conversation_sql):
    """SQL: an open hold of bot `bot_sql` on the task `task_sql`, or on the chat `conversation_sql` when no task."""
    return (
        f"EXISTS(SELECT 1 FROM holds qh WHERE qh.kind='quarantine' AND qh.bot_id={bot_sql} AND qh.released_at IS NULL "
        f"AND (qh.holdable_type='task' AND qh.holdable_id={task_sql} OR qh.holdable_type='conversation' "
        f"AND qh.holdable_id={conversation_sql} AND {task_sql} IS NULL))"
    )


def scope(hold):
    """The refusals' scope ("task:<id>" or "chat:<id>") of a hold row."""
    return ("task:" if hold["holdable_type"] == "task" else "chat:") + hold["holdable_id"]


def free_sql(task_sql):
    """SQL over job j and message m: a scoped quarantine holds every wake, including a person's new message."""
    return "NOT " + _held_sql("j.bot_id", task_sql, "m.conversation_id")


def for_task(c, bot, task_id):
    """The bot's hold on the task being written, including writes from an external agent or another run."""
    from . import holds

    task_id = H._task_ref(task_id)
    return holds.get(c, holds.QUARANTINE, "task", task_id, bot) if task_id else None


def for_attempt(c, row):
    """The open scoped quarantine of this attempt's work, or None."""
    from .execution import queued_task_sql

    task_sql = f"coalesce(a.task_id,{queued_task_sql()})"
    return c.execute(
        f"SELECT qh.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
        "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
        "JOIN holds qh ON qh.kind='quarantine' AND qh.bot_id=a.bot_id AND qh.released_at IS NULL "
        f"AND (qh.holdable_type='task' AND qh.holdable_id={task_sql} OR "
        f"qh.holdable_type='conversation' AND qh.holdable_id=m.conversation_id AND {task_sql} IS NULL) "
        "WHERE a.id=?",
        (row["id"],),
    ).fetchone()


def hold(c, bot, scope, reason, refusal):
    """Hold this bot's task/chat once, tell its readers why, and keep the cooldown's original start."""
    from . import holds

    kind, ident = scope.split(":", 1)
    holdable = "task" if kind == "task" else "conversation"
    if holds.get(c, holds.QUARANTINE, holdable, ident, bot):
        return
    until = H.shift(H.now(), seconds=H.QUARANTINE_COOLDOWN_S)
    holds.put(
        c,
        holds.QUARANTINE,
        holdable,
        ident,
        actor=H.TICO_ACTOR,
        bot_id=bot,
        reason=reason,
        details={"refusal": refusal},
        until_at=until,
    )
    H.event(c, H.TICO_ACTOR, "quarantine.held", H.bot_actor(bot), {"scope": scope, "reason": reason})
    said = (
        f"Held for {H.actor_name(c, H.bot_actor(bot))}: {reason} ({refusal.get('rule')} rule). This task or chat resumes after "
        f"{until[:16].replace('T', ' ')} UTC, or when a person clears the bot's quarantine. Other tasks can continue."
    )
    if kind == "task":
        H.task_comment(c, H.TICO_ACTOR, ident, said, wake=False)
    else:
        _notice(c, bot, ident, said)


def _notice(c, bot, conversation_id, said):
    """A quiet notice in the held chat: it explains the wait without creating another run."""
    H.say(c, H.TICO_ACTOR, H.bot_actor(bot), said, conversation_id=conversation_id, kind="notice", refs={"quiet": True})


def notes_free_sql():
    """A waiting note n may travel only when its recipient's source task/chat is free, before any batch limit."""
    from .execution import queued_task_sql

    task_sql = queued_task_sql()
    return (
        "NOT EXISTS(SELECT 1 FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE m.id=n.message_id AND {_held_sql('substr(n.to_actor,5)', task_sql, 'm.conversation_id')})"
    )


def _release_notice(c, row):
    # Archiving ends a bot's work; its hold can still expire without addressing an inactive recipient.
    if (H.bot(c, row["bot_id"]) or {}).get("state") == "archived":
        return
    refusal = (H._json(row["details"], {}) or {}).get("refusal") or {}
    said = (
        f"The refusal hold for {H.actor_name(c, H.bot_actor(row['bot_id']))} has lifted "
        f"({refusal.get('rule')} rule). {H.QUARANTINE_LIFTED}"
    )
    if row["holdable_type"] == "task":
        H.task_comment(c, H.TICO_ACTOR, row["holdable_id"], said, wake=False)
    else:
        _notice(c, row["bot_id"], row["holdable_id"], said)


def release(c, actor, bot=None, due=False):
    """Release scoped holds for quarantine clear, or only those whose cooldown is over (`due`) for the scheduler;
    reset only their counts."""
    from . import holds

    rows = c.execute(
        "SELECT * FROM holds WHERE kind='quarantine' AND holdable_type IN ('task','conversation') "
        "AND released_at IS NULL AND (? IS NULL OR bot_id=?) AND (? = 0 OR until_at<=?)",
        (bot, bot, int(due), H.now()),
    ).fetchall()
    released = set()
    for row in rows:
        released_row = False
        with isolated(c, "quarantine_release", row["id"]):
            how = "expired" if due else H.released_how(actor)
            holds.release(
                c,
                holds.QUARANTINE,
                row["holdable_type"],
                row["holdable_id"],
                actor=actor,
                how=how,
                bot_id=row["bot_id"],
            )
            H.event(c, actor, "quarantine.released", H.bot_actor(row["bot_id"]), {"scope": scope(row)})
            released.add(row["bot_id"])
            released_row = True
        # A notice is best effort: rolling it back must preserve the release and every other row in this batch.
        if released_row:
            with isolated(c, "quarantine_release_notice", row["id"]):
                _release_notice(c, row)
    return sorted(released)
