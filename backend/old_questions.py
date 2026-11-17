# pyright: strict
"""Questions written before a question was a message marked ❓ (backend/question_marks.py), taken back once.

Old open asks are not converted: each bot asks again, in the new format. Migration
20261117094959_no_structured_questions keeps every open question on an open task (a `requirements` row of kind `answer`
on a visit still in its stage, not answered) in `old_questions` before it deletes the kind, and the take-back here reads
them from there. Each is taken back with a note in the task's history, and the bot that asked it (if one did and it
still exists) is woken on the task with the question's line and choices, to ask again with a ❓ if it still needs the
answer. The wake is direct: a by-hand stage does not hold it, nor the bot's own snooze. A snooze a person set stays
theirs. Nothing else about the task changes: it stays in its stage, on its ladder.

It runs on every start (start_jobs) and is idempotent: each row is deleted once taken back, and so is a row whose task
has closed or left that stage since, so a second run finds nothing. It runs with the code that is serving, after every
migration, which a migration could not."""

import json
from typing import Any, cast

from . import hubdb as _hubdb

H: Any = _hubdb  # untyped module, through Any as in question_marks.py

NOTE = 'Questions are messages now: ask again with tico task ask <task> --to <person> "<question>".'
# The way to ask is the command itself: a ❓ typed into a reply or a note asks nobody (question_marks.unasked_reply).
WAKE = (
    "Your question on this task was taken back: questions are messages now.\n{question}\n"
    'If you still need it answered, ask again by running tico task ask {task} --to <person> "<one short line>", '
    "with the options and your pick on the lines after it: that posts it marked ❓ in their Needs you. "
    "Writing ❓ in your reply asks no one."
)
# The old question as the wake gives it back: its line and each choice, cut to these lengths.
LINE_MAX, CHOICE_MAX, CHOICES_MAX = 300, 120, 8
# Each question the migration kept whose task is still open, on the visit it was asked on.
OPEN = (
    "SELECT q.id, q.task_id, q.added_by_id, q.line, q.choices FROM old_questions q "
    "JOIN visits v ON v.id=q.visit_id JOIN tasks t ON t.id=q.task_id WHERE v.left_at IS NULL AND t.status='open' "
    "ORDER BY q.created_at, q.id"
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
    listed: list[object] = cast(list[object], picks) if isinstance(picks, list) else []
    shown = [_clip(c, CHOICE_MAX) for c in listed if str(c).strip()]
    more = len(shown) - CHOICES_MAX
    rows = [f"- {c}" for c in shown[:CHOICES_MAX]] + ([f"- (and {more} more)"] if more > 0 else [])
    return "\n".join([_clip(line, LINE_MAX) or "(no words)", *rows])


def carry_over(conn: Any) -> int:
    """Take every open question back and wake the bot that asked it (module docstring). Returns how many."""
    from . import task_engine

    E: Any = task_engine  # untyped, imported here to keep start-up imports acyclic

    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='old_questions'").fetchone():
        return 0
    taken = 0
    for qid, task_id, by, line, choices in conn.execute(OPEN).fetchall():
        conn.execute("DELETE FROM old_questions WHERE id=?", (qid,))
        E._event(conn, task_id, H.KEEPER, "requirement_withdrawn", qid, None, NOTE)
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
    conn.execute("DELETE FROM old_questions")  # the rest: their task closed or left that stage since
    return taken
