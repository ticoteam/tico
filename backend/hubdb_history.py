# pyright: strict
"""History: what happened to each thing, one row per change, in `history_events` (backend/migrations/baseline.sql).

A task's, a goal's, the company profile's, the strategy's and the market graph's. `kind` is the field that changed
(or what happened), `old`/`new` its values, `data` a JSON object of the type's own extras. A task's rows read the
way its old `task_events` rows did through TASK_EVENT_COLUMNS, so its history keeps its shape in every answer.

Split out of hubdb.py, which re-exports every name here, so `H.history_event` and `from .hubdb import ...` callers
are unchanged. This module imports nothing from hubdb at load, so hubdb can import it; its writers reach hubdb's
helpers (`now`, `new_id`, `VIA`) through `_H()` when they run, so a test that patches `H.now` patches them too.
"""

import json
from typing import Any

from . import sources

SRC: Any = sources  # untyped: bound as Any, as strict modules read it


# What hubdb re-exports: the private ones too, which hubdb itself and its callers use.
__all__ = [
    "HISTORY_SUBJECTS",
    "MARKET_SUBJECTS_SQL",
    "TASK_EVENT_COLUMNS",
    "history_event",
    "_task_event",
    "_strategy_changed",
    "task_history",
]


def _H() -> Any:
    from . import hubdb

    return hubdb


HISTORY_SUBJECTS = (
    "task",
    "goal",
    "company",
    "strategy",
    "market_entity",
    "market_edge",
    "market_evidence",
    "market_document",
)
MARKET_SUBJECTS_SQL = "('market_entity','market_edge','market_evidence','market_document')"
TASK_EVENT_COLUMNS = (
    "id, subject_id AS task_id, created_at, actor_id AS actor, kind AS field, old, new, note, action_id, via"
)


def history_event(
    conn: Any,
    subject_type: Any,
    subject_id: Any,
    actor: Any,
    kind: Any,
    old: Any = None,
    new: Any = None,
    note: Any = None,
    data: Any = None,
    *,
    action_id: Any = None,
    via: Any = None,
    created_at: Any = None,
    event_id: Any = None,
):
    """Append one change to `history_events`; returns its id. Values are stored as text, `data` as JSON. `event_id` is
    for a caller whose id is deterministic."""
    H = _H()
    if subject_type not in HISTORY_SUBJECTS:
        raise ValueError(f"unknown history subject type {subject_type!r}")
    data = SRC.stamp_data(data, actor)
    event_id = event_id or H.new_id()
    conn.execute(
        "INSERT INTO history_events (id, subject_type, subject_id, actor_id, kind, old, new, note, action_id, "
        "via, data, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            subject_type,
            subject_id,
            actor,
            kind,
            None if old is None else str(old),
            None if new is None else str(new),
            note,
            action_id,
            via,
            None if data is None else json.dumps(data, sort_keys=True, ensure_ascii=False),
            created_at or H.now(),
        ),
    )
    return event_id


def _task_event(
    conn: Any,
    task_id: Any,
    actor: Any,
    field: Any,
    old: Any,
    new: Any,
    note: Any = "",
    *,
    event_id: Any = None,
    data: Any = None,
):
    """One row of a task's history; returns its id."""
    H = _H()
    # A Strategy Owner decision (or its Undo) stamps each change it makes with its action id (backend/strategy_acts.py).
    from .strategy_acts import ACTION

    return history_event(
        conn,
        "task",
        task_id,
        actor,
        field,
        old,
        new,
        note,
        action_id=ACTION.get() or None,
        via=H.VIA.get() or None,
        data=data,
        event_id=event_id,
    )


def _strategy_changed(conn: Any, task_id: Any):
    """Whether a Strategy Owner decision (or its Undo) is writing this task now or has changed it before."""
    from .strategy_acts import ACTION

    return bool(ACTION.get()) or bool(
        conn.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND action_id IS NOT NULL LIMIT 1",
            (task_id,),
        ).fetchone()
    )


def task_history(conn: Any, task_id: Any, *, provenance: Any = False):
    H = _H()
    columns = TASK_EVENT_COLUMNS + (", data" if provenance else "")
    return H._rows(
        conn.execute(
            f"SELECT {columns} FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY created_at, rowid",
            (task_id,),
        )
    )
