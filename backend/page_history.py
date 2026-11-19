"""Page history: the pages a person opened in the app, kept KEEP_DAYS days so Tico knows where they have been and can
tell what "this", "that one" and "the last thing" mean.

- `POST /api/v2/page-history` records one page as the app opens it: its address path, the one line the app shows for it
  and the record it shows, if any. `via: assistant` marks a page Tico opened for the person; a Tico turn's own write is
  always marked so.
- `GET /api/v2/page-history` lists the caller's own visits, newest first, paged with `limit` and `before`; `page` keeps
  the visits to one page and the pages under it (`/tasks` keeps `/tasks/engineering/412`). Tico reads it through
  `tico_page_history`, as the person.
- `DELETE /api/v2/page-history` clears the caller's history. It cannot take back what was already said in a chat.

A history is its person's alone: nobody else reads it, the owner included, and a bot is refused (`views.human_only`):
another agent reads a person's history only once that person can switch it on for it. A row older than KEEP_DAYS never
lists and is deleted at that person's next visit.
"""

from typing import Any, Literal

from fastapi import Request
from pydantic import Field

from . import hubdb as H
from . import models as M
from . import views
from .api_contract import api_v2
from .store import Problem

KEEP_DAYS = 30
PAGE = 50
PAGE_MAX = 100
COLUMNS = "id, page, title, subject_type, subject_id, via, created_at"


class PageVisitCreate(M.Contract):
    page: str = Field(pattern=r"^/[^\s?#]*$", max_length=300)  # the address path, without the search
    title: str = Field(default="", max_length=200)  # the one line the app shows for the page
    subject_type: str | None = Field(default=None, pattern=r"^[a-z][a-z_]*$", max_length=40)  # task, thread, bot...
    subject_id: str | None = Field(default=None, min_length=1, max_length=200)
    via: Literal["", "assistant"] = ""  # assistant: Tico opened the page for the person


def visit(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in ("id", "page", "title", "subject_type", "subject_id", "via", "created_at")}


def listing(c: Any, actor: str, page: str | None, limit: int, before: str | None) -> dict[str, Any]:
    """One page of `actor`'s visits in the last KEEP_DAYS days, newest first. `next` is the `<created_at>|<id>` of the
    last visit shown, null when nothing older is left; `before` takes it back."""
    sql = f"SELECT {COLUMNS} FROM page_visits WHERE actor_id = ? AND created_at >= ?"
    args: list[Any] = [actor, H.shift(H.now(), days=-KEEP_DAYS)]
    if before:
        at, mark, vid = before.partition("|")
        if not mark or not at or not vid:
            raise Problem("kind", "before is the `next` of the page before: <created_at>|<id>", 422)
        sql += " AND (created_at, id) < (?, ?)"
        args += [at, vid]
    if page:
        under = page.rstrip("/") + "/"
        sql += " AND (page = ? OR substr(page, 1, ?) = ?)"
        args += [page, len(under), under]
    rows = c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    shown = [visit(row) for row in rows[:limit]]
    last = shown[-1] if len(rows) > limit else None
    return {"visits": shown, "next": f"{last['created_at']}|{last['id']}" if last else None}


def install(app: Any, store: Any, mutate: Any) -> None:
    def person(request: Request) -> Any:
        who = request.state.identity
        views.human_only(who)
        return who

    @app.get(
        "/api/v2/page-history",
        **api_v2(
            "Page history",
            "listPageHistory",
            "The pages you opened in the last 30 days, newest first; paged with `limit` and `before`",
            "PageVisitList",
            parameters={
                "page": "Only this page and the pages under it: `/tasks` keeps `/tasks/engineering/412`",
                "limit": f"Visits per page (default {PAGE}, at most {PAGE_MAX})",
                "before": "The `next` of the page before, to read older visits",
            },
        ),
    )
    def page_history(request: Request, page: str | None = None, limit: int = PAGE, before: str | None = None):
        who = person(request)
        with store.read() as c:
            return listing(c, who.actor, page, max(1, min(int(limit), PAGE_MAX)), before)

    @app.post(
        "/api/v2/page-history",
        **api_v2("Page history", "addPageVisit", "Record a page you opened", "PageVisitResult"),
    )
    def page_visit(request: Request, body: PageVisitCreate):
        who = person(request)
        if (body.subject_type is None) != (body.subject_id is None):
            raise Problem("kind", "Give subject_type and subject_id together, or neither", 422)

        def work(c: Any) -> dict[str, Any]:
            at, vid = H.now(), H.new_id()
            c.execute(
                "DELETE FROM page_visits WHERE actor_id = ? AND created_at < ?",
                (who.actor, H.shift(at, days=-KEEP_DAYS)),
            )
            c.execute(
                f"INSERT INTO page_visits({COLUMNS}, actor_id) VALUES(?,?,?,?,?,?,?,?)",
                (
                    vid,
                    body.page,
                    " ".join(body.title.split()),
                    body.subject_type,
                    body.subject_id,
                    "assistant" if who.via == "assistant" else body.via,
                    at,
                    who.actor,
                ),
            )
            row = c.execute(f"SELECT {COLUMNS} FROM page_visits WHERE id = ?", (vid,)).fetchone()
            return {"visit": visit(row)}

        return mutate(request, body, work)

    @app.delete(
        "/api/v2/page-history",
        **api_v2("Page history", "clearPageHistory", "Clear the pages you opened", "PageHistoryCleared"),
    )
    def page_history_clear(request: Request):
        who = person(request)

        def work(c: Any) -> dict[str, Any]:
            return {"cleared": c.execute("DELETE FROM page_visits WHERE actor_id = ?", (who.actor,)).rowcount}

        return mutate(request, M.Empty(), work)
