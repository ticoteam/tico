"""Questions written before a question was a message marked ❓ (backend/question_marks.py), taken back once.

Old open asks are not converted: each bot asks again, in the new format. Every open question on an open task (a
`requirements` row of kind `answer` on a visit still in its stage, not answered) is withdrawn with a note in the task's
history, and the bot that asked it (if one did and it still exists) is woken on the task with the question's line and
choices, to ask again with a ❓ if it still needs the answer. The wake is direct: a by-hand stage does not hold it, nor
the bot's own snooze. A snooze a person set stays theirs. Nothing else about the task changes: it stays in its stage,
on its ladder.

It runs on every start (start_jobs) and is idempotent: what it took back is withdrawn, so a second run finds nothing.
It runs with the code that is serving, after every migration, which a migration could not."""

import json
from typing import Any

from . import hubdb as H

NOTE = 'Questions are messages now: ask again with a ❓, tico task ask <task> --to <person> "<question>".'
WAKE = (
    "Your question on this task was taken back: questions are messages now.\n{question}\n"
    'If you still need it answered, ask again with a ❓: tico task ask {task} --to <person> "<question>".'
)
# The old question as the wake gives it back: its line and each choice, cut to these lengths.
LINE_MAX, CHOICE_MAX, CHOICES_MAX = 300, 120, 8
# Each open question, answered by nothing still standing. The answers are read by their visit first, as the open-ask
# readers do (the index answers_visit leads with visit_id): never a scan of every answer ever given.
OPEN = (
    "SELECT r.id, v.task_id, r.added_by_id, r.line, r.choices FROM requirements r "
    "JOIN visits v ON v.id=r.visit_id JOIN tasks t ON t.id=v.task_id "
    "WHERE r.kind='answer' AND r.withdrawn_at IS NULL AND v.left_at IS NULL AND t.status='open' "
    "AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.visit_id=v.id AND a.requirement_id=r.id AND a.undone_at IS NULL) "
    "ORDER BY r.created_at, r.id"
)


def _clip(text: object, limit: int) -> str:
    said = " ".join(str(text or "").split())
    return said if len(said) <= limit else said[: limit - 1].rstrip() + "…"


def question_of(line: object, choices: object) -> str:
    """The old question as its bot reads it back: its line, then each choice as a "- " line, every part capped."""
    try:
        picks: Any = json.loads(choices) if isinstance(choices, str) and choices else []
    except ValueError:
        picks = []
    shown = [_clip(c, CHOICE_MAX) for c in (picks if isinstance(picks, list) else []) if str(c).strip()]
    more = len(shown) - CHOICES_MAX
    rows = [f"- {c}" for c in shown[:CHOICES_MAX]] + ([f"- (and {more} more)"] if more > 0 else [])
    return "\n".join([_clip(line, LINE_MAX) or "(no words)", *rows])


def carry_over(conn: Any) -> int:
    """Take every open question back and wake the bot that asked it (module docstring). Returns how many."""
    from . import task_engine as E

    taken = 0
    for rid, task_id, by, line, choices in conn.execute(OPEN).fetchall():
        conn.execute("UPDATE requirements SET withdrawn_at=? WHERE id=?", (H.now(), rid))
        E._event(conn, task_id, H.KEEPER, "requirement_withdrawn", rid, None, NOTE)
        taken += 1
        if not (H.is_bot(by) and H.bot(conn, H.actor_id(str(by)))):
            continue
        # A direct wake: a by-hand stage does not hold it, nor a bot's own snooze (the ladder's would). A snooze a
        # person set stays theirs: the bot hears it on its next run there.
        snoozed = conn.execute("SELECT by_id FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_id,)).fetchone()
        now = not snoozed or not H.is_human(snoozed[0])
        H._wake(
            conn,
            H.task(conn, task_id),
            str(by),
            WAKE.format(question=question_of(line, choices), task=task_id),
            {"wake": "question_taken_back", **({"run_now": True} if now else {})},
            trigger="task_turn",
        )
    return taken
