"""A question to a person is a message marked ❓ now (backend/question_marks.py), not an ask the task waits on.

Each open question on a task (a `requirements` row of kind `answer` on a visit still in its stage, of an open task, not
answered) is withdrawn by Tico, with a note in the task's history saying how to ask again. Its visit's ladder starts
again from the owner, due now, so the bot whose turn it is runs and asks again with a ❓. A stage's own typed answer
(no visit) is left as it is.

Running this again changes nothing: the rows it withdrew are withdrawn.
"""

import uuid
from datetime import datetime, timezone

NOTE = 'Questions are messages now: ask again with a ❓, tico task ask <task> --to <person> "<question>".'
OPEN = (
    "SELECT r.id, v.id, v.task_id FROM requirements r JOIN visits v ON v.id=r.visit_id "
    "JOIN tasks t ON t.id=v.task_id WHERE r.kind='answer' AND r.withdrawn_at IS NULL AND v.left_at IS NULL "
    "AND t.status='open' AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.requirement_id=r.id AND a.undone_at IS NULL)"
)


def up(conn):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    found = conn.execute(OPEN).fetchall()
    for requirement, _visit, task in found:
        conn.execute("UPDATE requirements SET withdrawn_at=? WHERE id=?", (now, requirement))
        conn.execute(
            "INSERT INTO history_events (id, subject_type, subject_id, actor_id, kind, old, new, note, created_at) "
            "VALUES (?, 'task', ?, 'keeper', 'requirement_withdrawn', ?, NULL, ?, ?)",
            (uuid.uuid4().hex, task, requirement, NOTE, now),
        )
    for visit in sorted({row[1] for row in found}):
        conn.execute(
            "UPDATE visits SET tries=0, level='owner', person_id=NULL, next_try_at=?, last_change_at=? WHERE id=?",
            (now, now, visit),
        )
