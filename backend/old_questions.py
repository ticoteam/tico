"""Questions written before a question was a message marked ❓ (backend/question_marks.py), carried over once.

Each open question on an open task (a `requirements` row of kind `answer` on a visit still in its stage, not answered)
becomes a ❓: the bot that asked it posts its words on the task's chat (the line, its why, and its choices as plain
lines) and marks them for the person it asked, through the same `question_marks.ask` a live ❓ uses. The person still
finds it in Needs you, and their reply clears it and wakes the bot, on a by-hand stage or under the bot's snooze too.
The question row is withdrawn with a note in the task's history. Nothing else about the task changes: it stays in its
stage, on its ladder.

A question a person asked, or one whose asker or addressee can no longer take part, cannot be a bot's ❓: it is
withdrawn with the note, and the bot that asked it (if one did) is woken on the task to ask again, whatever its stage
or snooze. A person's own snooze stays.

It runs on every start (start_jobs) and is idempotent: what it carried over is withdrawn, so a second run finds nothing.
It runs with the code that is serving, after every migration, which a migration could not."""

import json
from typing import Any

from . import hubdb as H

NOTE = "Questions are messages now: this one was asked again as a ❓ on the task's chat."
NOTE_AGAIN = 'Questions are messages now: ask again with a ❓, tico task ask <task> --to <person> "<question>".'
WAKE = (
    "Your question on this task ({line}) was taken back: questions are messages now. "
    'If you still need it answered, ask again with a ❓: tico task ask {task} --to <person> "<question>".'
)
# Each open question, answered by nothing still standing. The answers are read by their visit first, as the open-ask
# readers do (the index answers_visit leads with visit_id): never a scan of every answer ever given.
OPEN = (
    "SELECT r.id, r.visit_id, v.task_id, r.added_by_id, r.person_id, r.line, r.choices, r.settings FROM requirements r "
    "JOIN visits v ON v.id=r.visit_id JOIN tasks t ON t.id=v.task_id "
    "WHERE r.kind='answer' AND r.withdrawn_at IS NULL AND v.left_at IS NULL AND t.status='open' "
    "AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.visit_id=v.id AND a.requirement_id=r.id AND a.undone_at IS NULL) "
    "ORDER BY r.created_at, r.id"
)


def text_of(line: object, choices: object, settings: object) -> str:
    """The ❓'s words: the question's line, its why on the next line, then each choice as a plain line."""
    meta: Any = _loads(settings, {})
    brief: Any = meta.get("brief") if isinstance(meta, dict) else None
    why = str(brief.get("why") or "").strip() if isinstance(brief, dict) else ""
    picks = [str(c).strip() for c in _loads(choices, []) or [] if str(c).strip()]
    return "\n".join([str(line or "").strip(), *([why] if why else []), *(f"- {c}" for c in picks)]).strip()


def _loads(value: object, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) and value else default
    except ValueError:
        return default


def _withdraw(conn: Any, requirement_id: str, task_id: str, note: str) -> None:
    from . import task_engine as E

    conn.execute("UPDATE requirements SET withdrawn_at=? WHERE id=?", (H.now(), requirement_id))
    E._event(conn, task_id, H.KEEPER, "requirement_withdrawn", requirement_id, None, note)


def carry_over(conn: Any) -> dict[str, int]:
    """Carry every open question over (module docstring). Returns {"marked": n, "taken_back": n}."""
    from . import question_marks
    from .store import Problem

    out = {"marked": 0, "taken_back": 0}
    for rid, _visit, task_id, by, person, line, choices, settings in conn.execute(OPEN).fetchall():
        text = text_of(line, choices, settings)
        if H.is_bot(by) and H.is_human(person) and text:
            conn.execute("SAVEPOINT old_question")
            try:
                question_marks.ask(conn, str(by), str(task_id), str(person), text, checked=False)
                _withdraw(conn, str(rid), str(task_id), NOTE)
            except (H.Refused, Problem):
                conn.execute("ROLLBACK TO old_question")
                conn.execute("RELEASE old_question")
            else:
                conn.execute("RELEASE old_question")
                out["marked"] += 1
                continue
        _withdraw(conn, str(rid), str(task_id), NOTE_AGAIN)
        out["taken_back"] += 1
        if H.is_bot(by) and H.bot(conn, H.actor_id(str(by))):
            # A direct wake: a by-hand stage does not hold it, nor a bot's own snooze (the ladder's would). A snooze a
            # person set stays theirs: the bot hears it on its next run there.
            snoozed = conn.execute(
                "SELECT by_id FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_id,)
            ).fetchone()
            now = not snoozed or not H.is_human(snoozed[0])
            H._wake(
                conn,
                H.task(conn, task_id),
                str(by),
                WAKE.format(line=str(line or "").strip()[:200], task=task_id),
                {"wake": "question_taken_back", **({"run_now": True} if now else {})},
                trigger="task_turn",
            )
    return out
