"""Small, content-free context lists, rebuilt with current rights for each task read and claim."""

from itertools import islice

from fastapi import Request

from . import context_search, page_links
from .files import visible_rows
from .store import Problem
from .api_contract import api_v2

MAX_ITEMS = 10


def pages_query(tid):
    place = "task:" + tid
    return (
        "SELECT f.*,coalesce(v.created_at,f.last_activity_at) AS context_updated_at "
        "FROM pages p JOIN bot_files f ON f.id=p.file_id LEFT JOIN bot_file_versions v "
        "ON v.file_id=f.id AND v.version=f.current_version WHERE p.archived=0 "
        "AND f.archived=0 AND f.state<>'deleted' AND f.id IN ("
        "SELECT id FROM bot_files WHERE task_id=? UNION "
        "SELECT id FROM bot_files WHERE bot_id IN (SELECT id FROM bots) AND scope=? UNION "
        + page_links.TASK_PAGE_IDS
        + ") ORDER BY context_updated_at DESC,f.id DESC",
        (tid, place, place, place),
    )


def listing(c, auth, who, tid):
    task = auth.task(c, who, auth.resolve_task(c, who, tid))
    sql, params = pages_query(task["id"])
    rows = list(islice(visible_rows(c, auth, who, sql, params), MAX_ITEMS))
    page_links.prime(c, rows)
    pages = [
        {
            "id": row["id"],
            "links": page_links.links_of(c, auth, who, row["id"]),
            "writers": page_links.writers_of(c, row["id"]),
            "may_write": page_links.may_write(c, auth, who, row["id"]),
            "title": row["title"][:200],
            "version": row["current_version"],
            "updated_at": row["context_updated_at"],
        }
        for row in rows
    ]
    linked = []
    for item in c.execute(
        "SELECT m.id FROM meeting_task_links l JOIN meetings m ON m.id=l.meeting_id "
        "WHERE l.task_id=? ORDER BY coalesce(julianday(json_extract(m.metadata,'$.started')),"
        "julianday(m.created_at)) DESC,m.id DESC",
        (task["id"],),
    ):
        try:
            row = context_search.readable(c, who, item[0], auth=auth)
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
            continue
        linked.append(
            {
                "id": row["id"],
                "title": row["title"][:200],
                "date": str(row["metadata"].get("started") or row["created_at"])[:40],
            }
        )
        if len(linked) == MAX_ITEMS:
            break
    return {"pages": pages, "meetings": linked}


def for_turn(c, auth, who, task):
    tid = (task or {}).get("id")
    if not tid:
        return {"pages": [], "meetings": []}
    return listing(c, auth, who, tid)


def install(app, store, auth):
    @app.get(
        "/api/v2/tasks/{tid}/context",
        **api_v2(
            "Tasks",
            "getTaskContext",
            "Up to 10 pages and 10 meetings, newest first, filtered by your current read access; metadata only",
            "TaskContext",
        ),
    )
    def context(request: Request, tid: str):
        with store.read_transaction() as c:
            return listing(c, auth, request.state.identity, tid)
