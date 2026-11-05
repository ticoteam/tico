"""Small, content-free context lists, rebuilt with current rights for each task read and claim."""
from fastapi import Request

from . import context_search, meetings, turn_scope
from .files import file_visible
from .store import H, Problem

MAX_ITEMS = 10


def listing(c, auth, who, tid, *, carried=None):
    task = auth.task(c, who, auth.resolve_task(c, who, tid))
    # A person's context list follows the same containment as a bot's, before applying their own access.
    scoped = turn_scope.scope(c, who)
    if carried is None:
        carried = turn_scope.tasks(c, auth, who, scoped) if scoped else (task,)
    if not carried:
        return {"pages": [], "meetings": []}
    pages, linked, cache = [], [], {}
    for row in c.execute("SELECT f.* FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.archived=0 "
                         "AND f.archived=0 AND f.state<>'deleted' AND (f.scope=? OR EXISTS "
                         "(SELECT 1 FROM pins WHERE place=? AND item='page:' || f.id)) "
                         "ORDER BY f.last_activity_at DESC,f.id DESC", ("task:" + task["id"], "task:" + task["id"])):
        if (all(turn_scope.file_contained(c, auth, row, t) for t in carried)
                and file_visible(c, auth, who, row, cache)):
            pages.append({"id": row["id"], "title": row["title"][:200], "version": row["current_version"],
                          "updated_at": row["last_activity_at"]})
            if len(pages) == MAX_ITEMS:
                break
    for item in c.execute("SELECT m.id FROM meeting_task_links l JOIN meetings m ON m.id=l.meeting_id "
                          "WHERE l.task_id=? ORDER BY coalesce(json_extract(m.metadata,'$.started'),m.created_at) DESC,m.id DESC",
                          (task["id"],)):
        row = meetings.get(item[0], c)
        if not turn_scope.meeting_contained(c, row):
            continue
        try:
            row = context_search.readable(c, who, item[0], auth=auth)
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
            continue
        linked.append({"id": row["id"], "title": row["title"][:200],
                       "date": row["metadata"].get("started") or row["created_at"]})
        if len(linked) == MAX_ITEMS:
            break
    return {"pages": pages, "meetings": linked}


def for_turn(c, auth, who, task):
    scoped = turn_scope.scope(c, who)
    tid = scoped.task if scoped and scoped.make_page else (task or {}).get("id")
    if not tid:
        return {"pages": [], "meetings": []}
    return listing(c, auth, who, tid)


def install(app, store, auth):
    @app.get("/api/v2/tasks/{tid}/context")
    def context(request: Request, tid: str):
        with store.read_transaction() as c:
            return listing(c, auth, request.state.identity, tid)
