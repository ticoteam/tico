"""Small, content-free context lists, rebuilt with current rights for each task read and claim."""
from fastapi import Request

from . import context_search, media, teammate_access, threads
from .files import file_visible
from .store import H, Problem

MAX_ITEMS = 10


def page_limited(c, row):
    """The page's own audience and filing settings, independent of the task receiving its reference."""
    if row["audience"] is not None:
        return True
    if row["task_id"] and H.task_private(c, H.task(c, row["task_id"])):
        return True
    kind, _, ident = row["scope"].partition(":")
    if kind == "task":
        return H.task_private(c, H.task(c, ident))
    if kind == "conversation":
        conversation = H.conversation(c, ident)
        if not conversation:
            return True
        room = threads.root_of(c, conversation)
        audience = threads.audience_of(room)
        if audience == "company":
            return False
        if audience == "task":
            return H.task_private(c, H.task(c, room.get("task_id")))
        if audience == "home":
            owner = threads.home_owner(room)
            return not owner or not teammate_access.document(c, owner)["read"]["everyone"]
        if audience == "meeting":
            meeting = c.execute("SELECT visibility FROM live_meetings WHERE id=?",
                                ((room.get("room_key") or "").removeprefix("live-meeting:"),)).fetchone()
            return not meeting or meeting[0] != "team"
        return True
    return not teammate_access.document(c, "bot:" + row["bot_id"])["read"]["everyone"]


def listing(c, auth, who, tid):
    task = auth.task(c, who, auth.resolve_task(c, who, tid))
    pages, linked, cache = [], [], {}
    for row in c.execute("SELECT f.*,p.audience,coalesce(v.created_at,f.last_activity_at) AS context_updated_at "
                         "FROM pages p JOIN bot_files f ON f.id=p.file_id LEFT JOIN bot_file_versions v "
                         "ON v.file_id=f.id AND v.version=f.current_version WHERE p.archived=0 "
                         "AND f.archived=0 AND f.state<>'deleted' AND (f.task_id=? OR f.scope=? OR EXISTS "
                         "(SELECT 1 FROM pins WHERE place=? AND item='page:' || f.id)) "
                         "ORDER BY context_updated_at DESC,f.id DESC", (task["id"], "task:" + task["id"], "task:" + task["id"])):
        if file_visible(c, auth, who, row, cache):
            pages.append({"id": row["id"], "title": row["title"][:200], "version": row["current_version"],
                          "updated_at": row["context_updated_at"], "limited": page_limited(c, row)})
            if len(pages) == MAX_ITEMS:
                break
    for item in c.execute("SELECT m.id FROM meeting_task_links l JOIN meetings m ON m.id=l.meeting_id "
                          "WHERE l.task_id=? ORDER BY coalesce(julianday(json_extract(m.metadata,'$.started')),"
                          "julianday(m.created_at)) DESC,m.id DESC",
                          (task["id"],)):
        try:
            row = context_search.readable(c, who, item[0], auth=auth)
        except Problem as exc:
            if exc.status not in (403, 404):
                raise
            continue
        linked.append({"id": row["id"], "title": row["title"][:200],
                       "date": str(row["metadata"].get("started") or row["created_at"])[:40],
                       "limited": media.effective_audience(row["metadata"]) != "team"})
        if len(linked) == MAX_ITEMS:
            break
    return {"pages": pages, "meetings": linked}


def for_turn(c, auth, who, task):
    tid = (task or {}).get("id")
    if not tid:
        return {"pages": [], "meetings": []}
    return listing(c, auth, who, tid)


def install(app, store, auth):
    @app.get("/api/v2/tasks/{tid}/context")
    def context(request: Request, tid: str):
        with store.read_transaction() as c:
            return listing(c, auth, request.state.identity, tid)
