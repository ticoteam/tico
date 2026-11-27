# pyright: strict
"""Quiet notes: something for a bot to know, not to do.

A note starts nothing; the next run the bot has for any reason carries every note waiting for it, in the same prompt,
each with the time it was sent. For example, monitor bots leave a manager bot a note after each check, and its daily
report reads them.

Split out of hubdb.py, which re-exports every name here, so `H.note_create` and `from .hubdb import ...` callers are
unchanged. This module imports nothing from hubdb at load, so hubdb can import it; its writers reach hubdb's
helpers (`now`, `new_id`, `refuse`, `chunks`...) through `_H()` when they run, so a test that patches `H.now` or
`H.chunks` patches them too.
"""

import sqlite3
from typing import Any

from . import hubdb_core
from .hubdb_core import TICO_ACTOR

_core: Any = hubdb_core  # its actor helpers are untyped: bound as Any, as strict modules read them
actor_id = _core.actor_id
is_bot = _core.is_bot
is_human = _core.is_human


# What hubdb re-exports: the private ones too, which hubdb itself and its callers use.
__all__ = [
    "NOTE_MAX",
    "NOTE_WAITING_SQL",
    "NOTE_CARRIER_SQL",
    "note_create",
    "note",
    "note_waiting",
    "note_cancel",
    "notes_waiting",
    "notes_settle",
    "notes_settle_catch_up",
    "notes_waiting_counts",
]


def _H() -> Any:
    from . import hubdb

    return hubdb


NOTE_MAX = 8000
NOTE_WAITING_SQL = (
    "n.canceled_at IS NULL AND n.settled_at IS NULL AND (n.carried_by IS NULL OR NOT EXISTS("
    "SELECT 1 FROM attempts a WHERE a.id=n.carried_by AND a.state IN ('leased','running','completed')))"
)


# A run in one of these states still holds the note it carried (note_waiting); a failed or interrupted one gives it back.
NOTE_CARRIER_SQL = "SELECT 1 FROM attempts WHERE id=? AND state IN ('leased','running','completed')"


def note_create(conn: Any, actor: Any, to: Any, body: Any):
    """Leave a note for a bot's next run. A person has no runs, so only a bot receives one. A paused or
    quarantined bot's note waits for its next run when a person or its manager bot leaves it (held_for)."""
    H = _H()
    H._writer(conn, actor)
    target = H._reach(conn, actor, to, allow_held=H.held_for(conn, actor, to))
    if not is_bot(target):
        H.refuse(conn, actor, "note", "a note waits for a bot's next run; a person has none, so tell them another way")
    if target == actor:
        H.refuse(conn, actor, "note", "a note to yourself belongs in your own repository")
    body = str(body or "").strip()
    if not body:
        H.refuse(conn, actor, "note", "the note is empty")
    if len(body) > NOTE_MAX:
        H.refuse(conn, actor, "note", f"a note is at most {NOTE_MAX} characters; put the detail in a file or a task")
    H.path_mentioned(conn, actor, "note", body)
    row = {"id": H.new_id(), "from_actor": actor, "to_actor": target, "body": body, "created_at": H.now()}
    conn.execute(
        "INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES "
        "(:id,:from_actor,:to_actor,:body,:created_at)",
        row,
    )
    H.event(conn, actor, "note.create", row["id"], {"to": target})
    return note(conn, row["id"])


def note(conn: Any, note_id: Any):
    H = _H()
    return H._one(conn, "SELECT * FROM notes WHERE id=?", (note_id,))


def note_waiting(conn: Any, row: Any):
    """Whether this note (a row) is still waiting for its bot's next run."""
    H = _H()
    if not row or row.get("canceled_at") or row.get("settled_at"):
        return False
    if not row.get("carried_by"):
        return True
    try:
        return not H._one(conn, NOTE_CARRIER_SQL, (row["carried_by"],))
    except sqlite3.OperationalError:  # a local hub has no attempts table and no runs
        return True


def note_cancel(conn: Any, actor: Any, note_id: Any, human: Any = False):
    """Take a note back before a run carries it: its sender, or a person."""
    H = _H()
    H._writer(conn, actor)
    row = note(conn, note_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no note {note_id}")
    if actor != row["from_actor"] and not human and not is_human(actor):
        H.refuse(
            conn,
            actor,
            "note",
            f"{H.actor_name(conn, row['from_actor'])} left this note; only they or a person take it back",
        )
    if row.get("message_id"):
        msg = H.message(conn, row["message_id"])
        if msg:
            H._message_writer(conn, actor, msg, H.conversation(conn, msg["conversation_id"]))
    if row["canceled_at"]:
        return row
    if not note_waiting(conn, row):
        H.refuse(conn, actor, "note", "a run already carried this note; it cannot be taken back")
    conn.execute("UPDATE notes SET canceled_at=?, canceled_by=? WHERE id=?", (H.now(), actor, note_id))
    H.event(conn, actor, "note.cancel", note_id, {})
    return note(conn, note_id)


def notes_waiting(conn: Any, bot: Any, limit: Any = 50, *, unheld: Any = False):
    """The notes waiting for `bot` (a slug), oldest first after Tico's own: the one a lifted quarantine leaves
    (quarantine_lifted_note) must reach the next run even behind a full backlog. A note that stands for a held
    message (`message_id`) is the message, in the order messages arrived, whoever sent it, Tico too.
    `unheld` selects notes deliverable now; the full pending list and its counts keep quarantined sources."""
    H = _H()
    from . import task_quarantine

    available = " AND " + task_quarantine.notes_free_sql() if unheld else ""
    return H._rows(
        conn.execute(
            f"SELECT n.* FROM notes n WHERE n.to_actor=? AND {NOTE_WAITING_SQL}{available} "
            f"ORDER BY NOT (n.from_actor=? AND n.message_id IS NULL),n.created_at,n.id LIMIT ?",
            ("bot:" + bot, TICO_ACTOR, limit),
        )
    )


def notes_settle(conn: Any, attempt_id: Any):
    """Mark the notes a completed run carried as read for good (`settled_at`), where its result is recorded."""
    H = _H()
    conn.execute("UPDATE notes SET settled_at=? WHERE carried_by=? AND settled_at IS NULL", (H.now(), attempt_id))


def notes_settle_catch_up(conn: Any, limit: int | None = 500):
    """Settle up to `limit` notes whose carrier completed but no path marked them (None settles them all: the
    migration's one pass). Reads the small unsettled-carried index, so it is cheap on every scheduler tick, and
    a note it leaves for the next pass stays correct, since waiting is still decided by the carrier. Returns the count."""
    H = _H()
    if not H._has_table(conn, "attempts"):
        return 0
    stamp = H.now()
    return conn.execute(
        "UPDATE notes SET settled_at=? WHERE id IN (SELECT n.id FROM notes n JOIN attempts a ON a.id=n.carried_by "
        "WHERE n.carried_by IS NOT NULL AND n.settled_at IS NULL AND a.state='completed' LIMIT ?)",
        (stamp, -1 if limit is None else limit),
    ).rowcount


def notes_waiting_counts(conn: Any, bots: Any, limit: Any = 500):
    """`len(notes_waiting(conn, bot, limit))` for many bots in one query per 500: {slug: count}."""
    H = _H()
    out = dict.fromkeys(bots, 0)
    for part in H.chunks(out):
        for row in conn.execute(
            f"SELECT n.to_actor,count(*) FROM notes n WHERE n.to_actor IN "
            f"({','.join('?' * len(part))}) AND {NOTE_WAITING_SQL} GROUP BY n.to_actor",
            ["bot:" + bot for bot in part],
        ):
            out[actor_id(row[0])] = min(row[1], limit)
    return out
