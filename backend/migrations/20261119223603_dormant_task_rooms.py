"""A finished task's room with nothing posted since the finish is marked dormant, so the live reads skip it.

A task's thread drops out of the rail, Browse and Catch up while its task is done or closed and no post has come since
its finish (threads.task_open_sql); a post the reader reads brings it back. That rule was worked out on every read, over
every finished task's room: on the hosted TIDY company (Nov 19) 17,144 of 18,662 task rooms, with only 348 of the
finished ones holding a post since. `conversations.dormant_at` records the rule's answer for everyone at once: set while
the room's task is finished and no message other than a notice (deleted or not, in the room or a thread under it) is
dated after the finish, which hides it from every reader. Cleared by anything that could bring it back: a post, a reopen,
a finish that moves earlier, the task going away. Nothing else changes: the room stays open, readable and postable, its
unread still counts, its mentions still need you; only the per-reader check runs on the few rooms that are not dormant.

The triggers keep it so for every write path:
1. A message other than a notice, written or restored, wakes its root (`conversations_wake_on_post`,
   `conversations_wake_on_message_change`).
2. A task's status, `closed_at` or `done_at` changing, while finished before or after, sets or clears its room again,
   as do a task row written anew or removed (`conversations_dormant_on_task_*`).
3. A task's move recorded or removed in history sets or clears its room again (`conversations_dormant_on_move_*`): the
   finish is its latest move into a Done or Closed stage.
4. A task room written, or a thread moved, is judged again (`conversations_dormant_on_room_*`).

`conversations_awake_task_roots` holds only the task roots that are not dormant (open tasks' and the few revived), so
the reads find the finished ones that can show without visiting the rest.

The backfill judges every task room once (about 0.2 s on TIDY's hub). Running it again changes nothing more.
"""

FINISHED = "('done','closed')"
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
# threads._finish_sql: the task's latest move into a Done or Closed stage, else its own timestamps.
FINISH = (
    "COALESCE((SELECT MAX(h.created_at) FROM history_events h WHERE h.subject_type='task' AND h.subject_id=t.id "
    "AND h.kind='move' AND h.new IN (SELECT id FROM stages WHERE kind IN ('done','closed'))),"
    "t.closed_at,t.done_at,t.updated_at)"
)
# The room (the row being updated, `conversations`) is hidden from every reader by its task: finished, and no message
# but a notice dated after the finish anywhere under it. Wider than what brings a room back for one reader (a deleted
# or unreadable post does not), so a dormant room is one no reader is shown.
DORMANT = (
    f"EXISTS (SELECT 1 FROM tasks t WHERE t.id=conversations.task_id AND t.status IN {FINISHED} "
    "AND NOT EXISTS (SELECT 1 FROM conversations s JOIN messages m ON m.conversation_id=s.id "
    "WHERE s.root_id=conversations.id AND COALESCE(m.kind,'')<>'notice' AND m.deleted_at IS NULL "
    f"AND m.created_at>{FINISH}))"
)


def judge(where):
    """Set or clear dormant_at on the task roots `where` picks (SQL over `conversations`)."""
    return (
        f"UPDATE conversations SET dormant_at=CASE WHEN {DORMANT} THEN COALESCE(dormant_at,{NOW}) END "
        f"WHERE parent_id IS NULL AND task_id IS NOT NULL AND ({where});"
    )


def wake(root):
    return f"UPDATE conversations SET dormant_at=NULL WHERE dormant_at IS NOT NULL AND id=({root});"


ROOT_OF = "SELECT COALESCE(root_id,id) FROM conversations WHERE id=NEW.conversation_id"
POSTED = "COALESCE(NEW.kind,'')<>'notice' AND NEW.deleted_at IS NULL"

TRIGGERS = (
    f"CREATE TRIGGER IF NOT EXISTS conversations_wake_on_post AFTER INSERT ON messages WHEN {POSTED} "
    f"BEGIN {wake(ROOT_OF)} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_wake_on_message_change "
    f"AFTER UPDATE OF kind, deleted_at, conversation_id, created_at ON messages WHEN {POSTED} "
    f"BEGIN {wake(ROOT_OF)} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_task_change "
    f"AFTER UPDATE OF status, closed_at, done_at ON tasks WHEN OLD.status IN {FINISHED} OR NEW.status IN {FINISHED} "
    f"BEGIN {judge('task_id=NEW.id')} END",
    f"CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_task_insert AFTER INSERT ON tasks "
    f"BEGIN {judge('task_id=NEW.id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_task_delete AFTER DELETE ON tasks "
    "BEGIN UPDATE conversations SET dormant_at=NULL WHERE task_id=OLD.id AND dormant_at IS NOT NULL; END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_move_insert AFTER INSERT ON history_events "
    "WHEN NEW.subject_type='task' AND NEW.kind='move' "
    f"BEGIN {judge('task_id=NEW.subject_id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_move_delete AFTER DELETE ON history_events "
    "WHEN OLD.subject_type='task' AND OLD.kind='move' "
    f"BEGIN {judge('task_id=OLD.subject_id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_room_insert AFTER INSERT ON conversations "
    "WHEN NEW.task_id IS NOT NULL AND NEW.parent_id IS NULL "
    f"BEGIN {judge('id=NEW.id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_room_move "
    "AFTER UPDATE OF task_id, parent_id, root_id ON conversations "
    "BEGIN UPDATE conversations SET dormant_at=NULL WHERE id=NEW.id AND dormant_at IS NOT NULL "
    "AND (parent_id IS NOT NULL OR task_id IS NULL); "
    f"{wake('SELECT NEW.root_id WHERE NEW.root_id IS NOT NEW.id')} {judge('id=NEW.id')} END",
)

BACKFILL = (
    f"UPDATE conversations SET dormant_at={NOW} WHERE parent_id IS NULL AND task_id IS NOT NULL "
    f"AND dormant_at IS NULL AND {DORMANT}"
)
INDEX = (
    "CREATE INDEX IF NOT EXISTS conversations_awake_task_roots ON conversations(task_id) "
    "WHERE task_id IS NOT NULL AND parent_id IS NULL AND dormant_at IS NULL"
)


def up(conn):
    if "dormant_at" not in {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}:
        conn.execute("ALTER TABLE conversations ADD COLUMN dormant_at TEXT")
    for statement in TRIGGERS:
        conn.execute(statement)
    conn.execute(BACKFILL)
    conn.execute(INDEX)
