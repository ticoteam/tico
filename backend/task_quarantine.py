"""Refusal-count quarantines hold one task or chat when all counted refusals come from it.

Two or more distinct tasks/chats, or any refusal with no attributable source, keep the bot-wide stop. Escape
quarantines always stay bot-wide. A run's own task/chat takes precedence over the target of its refused write.
The hold fences claims like interrupted work does, and uses the same quarantine Stop and review rules. It changes
no task status, snooze or dependency, and ends after the refusal cooldown or a person's quarantine clear.
"""

from . import hubdb as H


def source(c, actor, task_id=None):
    """The task/chat responsible for this refusal, or None when it cannot be established."""
    if not H.is_bot(actor):
        return None
    if H.RUN.get():
        row = c.execute(f"SELECT coalesce(a.task_id,{H.MESSAGE_TASK_SQL}) AS task_id,m.conversation_id "
                        "FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                        "JOIN conversations cv ON cv.id=m.conversation_id WHERE a.id=? AND a.bot_id=?",
                        (H.RUN.get(), H.actor_id(actor))).fetchone()
        if row:
            if row["task_id"] is not None and not H.task(c, H._task_ref(row["task_id"])):
                return None
            return "task:" + row["task_id"] if row["task_id"] else "chat:" + row["conversation_id"]
        return None
    task_id = H._task_ref(task_id)
    return "task:" + task_id if task_id and H.task(c, task_id) else None


def free_sql(task_sql):
    """SQL over job j and message m: a scoped quarantine holds every wake, including a person's new message."""
    return ("NOT EXISTS(SELECT 1 FROM quarantine_holds qh WHERE qh.bot_id=j.bot_id AND qh.released_at IS NULL "
            f"AND (qh.task_id={task_sql} OR (qh.conversation_id=m.conversation_id AND {task_sql} IS NULL)))")


def for_attempt(c, row):
    """The open scoped quarantine of this attempt's work, or None."""
    return c.execute(f"SELECT qh.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                     "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
                     "JOIN quarantine_holds qh ON qh.bot_id=a.bot_id AND qh.released_at IS NULL "
                     f"AND (qh.task_id=coalesce(a.task_id,{H.MESSAGE_TASK_SQL}) OR "
                     f"(qh.conversation_id=m.conversation_id AND coalesce(a.task_id,{H.MESSAGE_TASK_SQL}) IS NULL)) "
                     "WHERE a.id=?", (row["id"],)).fetchone()


def hold(c, bot, scope, reason, refusal):
    """Hold this bot's task/chat once, tell its readers why, and keep the cooldown's original start."""
    kind, ident = scope.split(":", 1)
    column = "task_id" if kind == "task" else "conversation_id"
    if c.execute(f"SELECT 1 FROM quarantine_holds WHERE bot_id=? AND {column}=? AND released_at IS NULL",
                 (bot, ident)).fetchone():
        return
    at = H.now()
    c.execute(f"INSERT INTO quarantine_holds(bot_id,{column},reason,refusal,created_at) VALUES(?,?,?,?,?)",
              (bot, ident, reason, H._dump(refusal), at))
    H.event(c, H.KEEPER, "quarantine.held", H.bot_actor(bot), {"scope": scope, "reason": reason})
    until = H.shift(at, seconds=H.QUARANTINE_COOLDOWN_S)
    said = (f"Held for {H.actor_name(c, H.bot_actor(bot))}: {reason} ({refusal.get('rule')} rule). This task or chat resumes after "
            f"{until[:16].replace('T', ' ')} UTC, or when a person clears the bot's quarantine. Other tasks can continue.")
    if kind == "task":
        H.task_comment(c, H.KEEPER, ident, said, wake=False)
    else:
        _notice(c, bot, ident, said)


def _notice(c, bot, conversation_id, said):
    """A quiet notice in the held chat: it explains the wait without creating another run."""
    H.say(c, H.KEEPER, H.bot_actor(bot), said, conversation_id=conversation_id, kind="notice", refs={"quiet": True})


def release(c, actor, bot=None, cutoff=None):
    """Release scoped holds for quarantine clear, or only cooled ones for the scheduler; reset only their counts."""
    rows = c.execute("SELECT * FROM quarantine_holds WHERE released_at IS NULL "
                     "AND (? IS NULL OR bot_id=?) AND (? IS NULL OR created_at<=?)",
                     (bot, bot, cutoff, cutoff)).fetchall()
    at = H.now()
    for row in rows:
        c.execute("UPDATE quarantine_holds SET released_at=? WHERE id=?", (at, row["id"]))
        scope = "task:" + row["task_id"] if row["task_id"] else "chat:" + row["conversation_id"]
        H.event(c, actor, "quarantine.released", H.bot_actor(row["bot_id"]), {"scope": scope})
        refusal = H._json(row["refusal"], {})
        said = (f"The refusal hold for {H.actor_name(c, H.bot_actor(row['bot_id']))} has lifted "
                f"({refusal.get('rule')} rule). {H.QUARANTINE_LIFTED}")
        if row["task_id"]:
            H.task_comment(c, H.KEEPER, row["task_id"], said, wake=False)
        else:
            _notice(c, row["bot_id"], row["conversation_id"], said)
    return sorted({row["bot_id"] for row in rows})
