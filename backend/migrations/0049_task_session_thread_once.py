"""A session link keeps its provider thread id once, in its url (`session:<bot>/<thread>`, or the resume command it came
from). Its detail used to repeat it as `thread_id` (hubdb._task_session_link and task_session_started), so the two could
drift apart. hubdb._with_thread reads the id from the url for the API, so the task's links keep the same shape.

This strips the copy from each session row whose url gives back that same thread through hubdb._with_thread. Any other
row (a url that names another thread or none, a detail that is not JSON, a link that is not a session) is left as it is,
so no thread id is lost. Only session rows that carry a copy are read; the updates go in batches. A database without
task_links skips it, and running it again finds nothing to change."""
import json

from backend import hubdb as H

BATCH = 1000


def up(conn):
    if "detail" not in {row[1] for row in conn.execute("PRAGMA table_info(task_links)")}:
        return
    rows = conn.execute(
        "SELECT id, kind, url, detail FROM task_links WHERE kind='session' AND json_valid(detail) "
        "AND json_type(detail) = 'object' AND json_extract(detail, '$.thread_id') IS NOT NULL").fetchall()
    changes = []
    for link_id, kind, url, detail in rows:
        stored = json.loads(detail)
        thread = stored.pop("thread_id")
        kept = json.dumps(stored)
        read_back = H._with_thread({"kind": kind, "url": url, "detail": kept})
        if json.loads(read_back["detail"]).get("thread_id") == thread:
            changes.append((kept, link_id))
    for start in range(0, len(changes), BATCH):
        conn.executemany("UPDATE task_links SET detail=? WHERE id=?", changes[start:start + BATCH])
