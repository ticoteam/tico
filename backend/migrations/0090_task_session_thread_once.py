"""A session link keeps its provider thread id once, in its url (`session:<bot>/<thread>`, or the resume command it came
from). Its detail used to repeat it as `thread_id` (hubdb._task_session_link and task_session_started), so the two could
drift apart. hubdb._with_thread reads the id from the url for the API, so the task's links keep the same shape.

This strips the copy from each session row whose url still names that thread. A row whose url does not name it keeps its
detail, so nothing is lost. A database without task_links skips it, and a file from a release that never wrote the copy
has no row to change."""


def up(conn):
    found = {row[1] for row in conn.execute("PRAGMA table_info(task_links)")}
    if "detail" not in found:
        return
    conn.execute("""
        UPDATE task_links SET detail = json_remove(detail, '$.thread_id')
        WHERE kind = 'session'
          AND CASE WHEN json_valid(detail) THEN json_extract(detail, '$.thread_id') END IS NOT NULL
          AND instr(url, CASE WHEN json_valid(detail) THEN json_extract(detail, '$.thread_id') END) > 0
    """)
