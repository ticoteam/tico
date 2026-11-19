"""A finished task's room with nothing posted since the finish is marked dormant, so the live reads skip it.

A task's thread drops out of the rail, Browse and Catch up while its task is done or closed and no post has come since
its finish (threads.task_open_sql); a post the reader reads brings it back. That rule was worked out on every read, over
every finished task's room: on the hosted TIDY company (Nov 19) 17,144 of 18,662 task rooms, with only 348 of the
finished ones holding a post since. `conversations.dormant_at` records the rule's answer for everyone at once: set while
the room's task is finished and no message other than a notice (deleted or not, in the room or a thread under it) is
dated after the finish, which hides it from every reader. Nothing else changes: the room stays open, readable and
postable, its unread still counts, its mentions still need you; only the per-reader check runs on the few rooms that are
not dormant. The mark is internal: no API answer and no SQL read shows it (it moves on posts a reader may not read).

Triggers judge a task root again whenever something its answer reads changes, so the mark never hides a room the rule
would show:
1. A message: written into a dormant root, removed from an awake one, or changed (kind, deletion, room, time), judges
   its root, old and new (`conversations_dormant_on_post`, `..._message_delete`, `..._message_change`).
2. A task: its status, finish times, `updated_at` (the last fallback) or id changing, while finished before or after,
   and a task written or removed (`conversations_dormant_on_task_*`).
3. A task's move recorded, changed or removed in history, and a stage written, changing kind or removed
   (`conversations_dormant_on_move_*`, `conversations_dormant_on_stage_*`): the finish is the latest move into a Done or
   Closed stage.
4. A task room written, a thread written under a root with its messages, or a thread moved
   (`conversations_dormant_on_room_*`).

Two small indexes hold what is not dormant: `conversations_awake_task_roots` (open tasks' roots and the few revived),
from which the reads find the finished ones that can show, and `conversations_awake_open_roots` (every open root but the
dormant), which Catch up's and Browse's pages read instead of every root.

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
    """Set or clear dormant_at on the task roots `where` picks (SQL over `conversations`), as DORMANT says now."""
    return (
        f"UPDATE conversations SET dormant_at=CASE WHEN {DORMANT} THEN COALESCE(dormant_at,{NOW}) END "
        f"WHERE parent_id IS NULL AND task_id IS NOT NULL AND ({where});"
    )


def root_of(conversation):
    return f"id=(SELECT COALESCE(root_id,id) FROM conversations WHERE id={conversation})"


POSTED = "COALESCE(NEW.kind,'')<>'notice' AND NEW.deleted_at IS NULL"
MOVE = "{row}.subject_type='task' AND {row}.kind='move'"
MOVED_INTO = "task_id IN (SELECT subject_id FROM history_events WHERE subject_type='task' AND kind='move' AND new={})"
EITHER_FINISHED = f"OLD.status IN {FINISHED} OR NEW.status IN {FINISHED} OR OLD.id IS NOT NEW.id"

TRIGGERS = (
    # The common case, a post into an open room, costs one lookup: only a dormant root is judged.
    f"CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_post AFTER INSERT ON messages WHEN {POSTED} "
    f"BEGIN {judge('dormant_at IS NOT NULL AND ' + root_of('NEW.conversation_id'))} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_message_change "
    "AFTER UPDATE OF kind, deleted_at, conversation_id, created_at ON messages "
    f"BEGIN {judge(root_of('NEW.conversation_id'))} {judge(root_of('OLD.conversation_id'))} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_message_delete AFTER DELETE ON messages "
    f"BEGIN {judge('dormant_at IS NULL AND ' + root_of('OLD.conversation_id'))} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_task_change "
    f"AFTER UPDATE OF status, closed_at, done_at, updated_at, id ON tasks WHEN {EITHER_FINISHED} "
    f"BEGIN {judge('task_id IN (OLD.id, NEW.id)')} END",
    f"CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_task_insert AFTER INSERT ON tasks "
    f"BEGIN {judge('task_id=NEW.id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_task_delete AFTER DELETE ON tasks "
    "BEGIN UPDATE conversations SET dormant_at=NULL WHERE task_id=OLD.id AND dormant_at IS NOT NULL; END",
    f"CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_move_insert AFTER INSERT ON history_events "
    f"WHEN {MOVE.format(row='NEW')} BEGIN {judge('task_id=NEW.subject_id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_move_change "
    "AFTER UPDATE OF subject_type, subject_id, kind, new, created_at ON history_events "
    f"WHEN ({MOVE.format(row='OLD')}) OR ({MOVE.format(row='NEW')}) "
    f"BEGIN {judge('task_id IN (OLD.subject_id, NEW.subject_id)')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_move_delete AFTER DELETE ON history_events "
    f"WHEN {MOVE.format(row='OLD')} BEGIN {judge('task_id=OLD.subject_id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_stage_insert AFTER INSERT ON stages "
    f"BEGIN {judge(MOVED_INTO.format('NEW.id'))} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_stage_change AFTER UPDATE OF id, kind ON stages "
    f"BEGIN {judge(MOVED_INTO.format('OLD.id') + ' OR ' + MOVED_INTO.format('NEW.id'))} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_stage_delete AFTER DELETE ON stages "
    f"BEGIN {judge(MOVED_INTO.format('OLD.id'))} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_room_insert AFTER INSERT ON conversations "
    f"BEGIN {judge('id=NEW.id OR id=NEW.root_id')} END",
    "CREATE TRIGGER IF NOT EXISTS conversations_dormant_on_room_move "
    "AFTER UPDATE OF task_id, parent_id, root_id, id ON conversations "
    "BEGIN UPDATE conversations SET dormant_at=NULL WHERE id=NEW.id AND dormant_at IS NOT NULL "
    "AND (parent_id IS NOT NULL OR task_id IS NULL); "
    f"{judge('id IN (NEW.id, NEW.root_id, OLD.root_id)')} END",
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS conversations_awake_task_roots ON conversations(task_id) "
    "WHERE task_id IS NOT NULL AND parent_id IS NULL AND dormant_at IS NULL",
    "CREATE INDEX IF NOT EXISTS conversations_awake_open_roots ON conversations(id) "
    "WHERE parent_id IS NULL AND archived_at IS NULL AND closed_at IS NULL AND dormant_at IS NULL",
)
BACKFILL = (
    f"UPDATE conversations SET dormant_at={NOW} WHERE parent_id IS NULL AND task_id IS NOT NULL "
    f"AND dormant_at IS NULL AND {DORMANT}"
)


def up(conn):
    if "dormant_at" not in {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}:
        conn.execute("ALTER TABLE conversations ADD COLUMN dormant_at TEXT")
    for statement in TRIGGERS:
        conn.execute(statement)
    conn.execute(BACKFILL)
    for statement in INDEXES:
        conn.execute(statement)
