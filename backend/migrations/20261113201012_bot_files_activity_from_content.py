"""A stored file's last activity is when its content last changed, not when its computer last sent the same bytes.

Until this change an unchanged publish (`bot_file_activity.action = 'published'`, a re-sync with the same digest) moved
`bot_files.last_activity_at` to the moment of the re-sync, so a computer that sent a bot's whole checkout again put
old files above the ones the bot had just written (backend/files.py Files.add_version). Each stored file whose last
activity is exactly such a touch goes back to its newest real activity: the latest activity before it that is not a
touch (created, modified, archived, promoted, ...). Nothing else moves: a link's touch is the bot working on that
document, and a file with no earlier activity keeps its time. A new database has no `bot_files` yet (files.py makes
it), so there is nothing to do.

The time a repository's file was first copied cannot be recovered here: the bot's computer sends each file's commit
time once more after its update (runner-rs/src/files_publish.rs, file_dated), and an unchanged publish carrying an
earlier time moves a copy's time back to it."""


def up(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"bot_files", "bot_file_activity"} <= tables:
        return
    conn.execute(
        "UPDATE bot_files SET last_activity_at=("
        "SELECT MAX(a.created_at) FROM bot_file_activity a WHERE a.file_id=bot_files.id AND a.action<>'published' "
        "AND a.created_at<=bot_files.last_activity_at) "
        "WHERE locator='tico_blob' "
        "AND EXISTS (SELECT 1 FROM bot_file_activity t WHERE t.file_id=bot_files.id AND t.action='published' "
        "AND t.created_at=bot_files.last_activity_at) "
        "AND NOT EXISTS (SELECT 1 FROM bot_file_activity r WHERE r.file_id=bot_files.id AND r.action<>'published' "
        "AND r.created_at=bot_files.last_activity_at) "
        "AND EXISTS (SELECT 1 FROM bot_file_activity e WHERE e.file_id=bot_files.id AND e.action<>'published' "
        "AND e.created_at<bot_files.last_activity_at)"
    )
