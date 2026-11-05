"""Index frozen letter provenance without depending on another pending migration."""


def up(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS letter_sources (letter_id TEXT NOT NULL REFERENCES letters(id) ON DELETE CASCADE, "
                 "source_type TEXT NOT NULL CHECK(source_type IN ('task','thread')), source_id TEXT NOT NULL)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS letter_sources_source ON letter_sources(source_type,source_id,letter_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS letter_sources_letter ON letter_sources(letter_id)")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'letters' not in tables:
        return
    columns = {r[1] for r in conn.execute('PRAGMA table_info(letters)')}
    for kind in ('task', 'thread'):
        if kind + '_id' in columns:
            conn.execute(f"INSERT OR IGNORE INTO letter_sources SELECT id,'{kind}',{kind}_id FROM letters WHERE {kind}_id IS NOT NULL")
        if 'source_provenance' in columns:
            conn.execute("INSERT OR IGNORE INTO letter_sources SELECT l.id,?,j.value FROM letters l, "
                         "json_each(CASE WHEN json_valid(l.source_provenance) THEN l.source_provenance ELSE '{}' END,?) j "
                         "WHERE j.type='text'", (kind, '$.' + kind + 's'))
    if 'conversations' not in tables:
        return
    columns = {r[1] for r in conn.execute('PRAGMA table_info(conversations)')}
    if 'root_id' in columns:
        conn.execute("INSERT OR IGNORE INTO letter_sources SELECT s.letter_id,'thread',c.root_id FROM letter_sources s "
                     "JOIN conversations c ON c.id=s.source_id WHERE s.source_type='thread' AND c.root_id IS NOT NULL")
    if 'task_id' in columns:
        conn.execute("INSERT OR IGNORE INTO letter_sources SELECT s.letter_id,'task',c.task_id FROM letter_sources s "
                     "JOIN conversations c ON c.id=s.source_id WHERE s.source_type='thread' AND c.task_id IS NOT NULL")
