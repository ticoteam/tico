"""Invalidate prepared run provenance only when its source rows change.

Lease extensions, event deltas and flight-recorder commits do not change which tasks a
run carries. SQLite triggers cover writes from every connection, including older clients.
This is an invalidation clock, never a permission cache.
"""

# Sources used by task_privacy.attempt_tasks/message_tasks/references. Over-invalidation is
# intentional: e.g. any new task may turn a formerly unresolved structured reference into a task.
SOURCES = {
    'attempts': ('id', 'bot_id', 'job_id', 'state'),
    'attempt_inputs': ('attempt_id', 'message_id'),
    'jobs': ('id', 'message_id'),
    'messages': ('id', 'conversation_id', 'refs', 'in_reply_to', 'sender_id', 'recipient_id'),
    'conversations': ('id', 'task_id', 'room_key'),
    'live_meeting_turns': ('id', 'meeting_id', 'bot'),
    'tasks': ('id', 'carried_by'),
    'events': ('action', 'target', 'detail'),
}


def ensure(c):
    c.execute('CREATE TABLE IF NOT EXISTS provenance_clock(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL)')
    c.execute('INSERT OR IGNORE INTO provenance_clock VALUES(1,0)')
    for table, columns in SOURCES.items():
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            action = operation + (' OF ' + ','.join(columns) if operation == 'UPDATE' else '')
            condition = (' OR '.join(f'OLD.{col} IS NOT NEW.{col}' for col in columns) if operation == 'UPDATE' else '1')
            if table == 'events':
                kinds = "('task.next-run.carried','attempt.input.moved')"
                relevant = (' OR '.join(f'{side}.action IN {kinds}' for side in
                                       (('OLD', 'NEW') if operation == 'UPDATE' else
                                        ('OLD',) if operation == 'DELETE' else ('NEW',))))
                condition = f'({condition}) AND ({relevant})'
            # Upgrade existing clocks when meeting provenance adds source columns.
            suffix = '_v2' if operation == 'UPDATE' and table in {'messages', 'conversations'} else ''
            if suffix:
                c.execute(f'DROP TRIGGER IF EXISTS provenance_{table}_{operation.lower()}')
            c.execute(f'CREATE TRIGGER IF NOT EXISTS provenance_{table}_{operation.lower()}{suffix} AFTER {action} ON {table} '
                      f'WHEN {condition} BEGIN UPDATE provenance_clock SET value=value+1 WHERE id=1; END')


def current(c):
    from . import hubdb as H
    from . import read_cache
    if not H._has_table(c, 'provenance_clock'):
        return None
    return read_cache.one(c, 'SELECT value FROM provenance_clock WHERE id=1')[0]


class Cache:
    """Bounded neutral provenance, shared only by one Store's connections at the same source clock."""
    def __init__(self):
        from collections import OrderedDict
        from threading import Lock
        self.values, self.lock = OrderedDict(), Lock()

    def read(self, clock, key, build, publish=True):
        key = (clock, key)
        with self.lock:
            hit = self.values.get(key)
            if hit is not None:
                self.values.move_to_end(key)
                return set(hit)
        result = build()
        if publish:
            with self.lock:
                self.values[key] = frozenset(result)
                self.values.move_to_end(key)
                while len(self.values) > 4096:
                    self.values.popitem(last=False)
        return set(result)


def cached(c, key, build):
    # Uncommitted source changes can roll back to the same clock value. Never publish
    # shared entries from a writer transaction. An unchanged source clock can still
    # consume committed neutral entries; changed sources are rebuilt locally.
    cache = getattr(c, 'provenance_cache', None)
    if cache is None:
        return build()
    version = current(c)
    writing = (getattr(c, 'write_transaction', False) or
               c.total_changes != getattr(c, 'provenance_changes', c.total_changes))
    if version is None or writing and version != getattr(c, 'provenance_start', None):
        return build()
    return cache.read(version, key, build, publish=not writing)
