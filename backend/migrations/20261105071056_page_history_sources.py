"""Link both sides of legacy task-file history to page provenance before content is purged."""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pages'").fetchone():
        return
    conn.execute("UPDATE history_events SET data=json_set(coalesce(data,'{}'),'$.page_files',"
                 "json((SELECT json_group_array(DISTINCT v.file_id) FROM bot_file_versions v "
                 "JOIN bot_files f ON f.id=v.file_id JOIN pages p ON p.file_id=f.id "
                 "WHERE f.task_id=history_events.subject_id AND "
                 "(v.name=history_events.old OR v.name=history_events.new OR "
                 "f.title=history_events.old OR f.title=history_events.new)))) "
                 "WHERE subject_type='task' AND kind='file' AND EXISTS ("
                 "SELECT 1 FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id "
                 "JOIN pages p ON p.file_id=f.id WHERE f.task_id=history_events.subject_id AND "
                 "(v.name=history_events.old OR v.name=history_events.new OR "
                 "f.title=history_events.old OR f.title=history_events.new))")
