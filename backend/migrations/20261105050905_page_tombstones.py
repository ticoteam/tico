"""Keep page authorization and identity provenance after purging its content."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pages)")}
    if columns and "deleted_at" not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN deleted_at TEXT")
    conn.execute("CREATE TABLE IF NOT EXISTS page_identities("
                 "identity TEXT NOT NULL,file_id TEXT NOT NULL REFERENCES pages(file_id),"
                 "PRIMARY KEY(identity,file_id))")
    # Older task-file history stored only a name. Link every matching page rather than guessing which copy it was.
    if columns:
        conn.execute("UPDATE history_events SET data=json_set(coalesce(data,'{}'),'$.page_files',"
                     "json((SELECT json_group_array(DISTINCT v.file_id) FROM bot_file_versions v "
                     "JOIN bot_files f ON f.id=v.file_id JOIN pages p ON p.file_id=f.id "
                     "WHERE f.task_id=history_events.subject_id AND v.name=history_events.new))) "
                     "WHERE subject_type='task' AND kind='file' AND EXISTS ("
                     "SELECT 1 FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id "
                     "JOIN pages p ON p.file_id=f.id WHERE f.task_id=history_events.subject_id AND v.name=history_events.new)")
