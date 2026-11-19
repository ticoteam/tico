"""An index of the open roots the rail lists, in its title order, so its Pinned and Threads pages read those few hundred
roots and not every task thread.

The rail lists threads (company, team, members) and group DMs (personal, participants), never a task's thread, and a
company's task threads are most of its roots (thread_rail.rail). The index holds only open roots of those audiences
(the stored one, else the one derived from scope and kind, as threads._derived_audience reads it), keyed by the rail's
title order: unnamed last, then the lowercased title, then the id.

Only a query holding the index's own terms can use it: the rail's lists and threads/match, both ordered down to the
id, so no answer and no order of ties changes. (A plain open-roots index would also serve threads._open_dm, whose
timestamp order leaves ties to the plan.)

Running it again changes nothing more.
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS conversations_open_thread_titles ON conversations("
    "CASE WHEN TRIM(COALESCE(subject,''))='' THEN 1 ELSE 0 END, lower(COALESCE(subject,'')), id) "
    "WHERE parent_id IS NULL AND archived_at IS NULL AND closed_at IS NULL AND COALESCE(audience,"
    "CASE WHEN substr(COALESCE(room_key,''),1,13)='live-meeting:' THEN 'meeting' "
    "WHEN scope='personal' THEN 'personal' WHEN scope='shared' THEN 'home' "
    "WHEN kind='task' AND task_id IS NOT NULL THEN 'task' ELSE 'participants' END) "
    "IN ('company','team','members','personal','participants')",
)


def up(conn):
    for statement in INDEXES:
        conn.execute(statement)
