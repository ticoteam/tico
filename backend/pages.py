"""Pages: HTML files a bot keeps up to date to show people what it did and found (docs/pages.md).

A page is a bot file (backend/files.py) with a `pages` row beside it: how often the bot means to update it, the date
its data is from, the page that replaced it and whether it is archived. Versions, visibility and storage are the
file's; the bytes are cleaned as a visual page is (backend/visuals.py `clean`), so a page runs no script and loads
nothing from elsewhere. The status line (updated, by, version, cadence, data date, stale or replaced) is drawn by the
app from these fields, outside the page, so a page cannot fake it.
"""

import asyncio
import hashlib
import json
import re
from datetime import date

from fastapi import Request
from pydantic import Field

from . import cards
from . import models as M
from . import names
from . import read_cache
from . import visuals
from .store import H, Problem
from clients import bot_files as BF

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages(
 file_id TEXT PRIMARY KEY REFERENCES bot_files(id),
 cadence TEXT NOT NULL DEFAULT 'none' CHECK(cadence IN ('none','daily','weekly','monthly')),
 as_of TEXT, replaced_by_id TEXT, audience TEXT, deleted_at TEXT, archived INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pages_replaced ON pages(replaced_by_id) WHERE replaced_by_id IS NOT NULL;
"""

CADENCES = {"none": 0, "daily": 1, "weekly": 7, "monthly": 30}
# A page is stale once 1.5x its cadence has passed without a new version.
STALE_FACTOR = 1.5
LIST_MAX = 50
IDS_MAX = 50
AS_OF = re.compile(r"\d{4}-\d{2}-\d{2}")


def ensure(c):
    """Unversioned, like the trash: an older release never reads the table. Runs inside initialize's transaction."""
    for statement in SCHEMA.split(";"):
        if statement.strip():
            c.execute(statement)


class PageEdit(M.Contract):
    cadence: str | None = Field(default=None, max_length=10)
    as_of: str | None = Field(default=None, max_length=10)
    replaced_by: str | None = Field(default=None, max_length=100)
    archived: bool | None = None
    audience: M.AccessAudience | None = None


def check_cadence(value):
    value = str(value or "").strip().lower()
    if value not in CADENCES:
        raise Problem("validation", "cadence is none, daily, weekly or monthly", 422)
    return value


def check_as_of(value):
    """A YYYY-MM-DD date (a longer timestamp is cut to its date), or None."""
    value = str(value or "").strip()[:10]
    if not value:
        return None
    try:
        if not AS_OF.fullmatch(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError as exc:
        raise Problem("validation", "as_of is a date: YYYY-MM-DD", 422) from exc
    return value


def stale_days(updated, cadence, now=None):
    """Whole days since `updated_at` once 1.5x the cadence has passed, else None."""
    period = CADENCES.get(cadence or "none", 0)
    when = H.parse_ts(updated)
    if not period or when is None:
        return None
    now = now or H.parse_ts(H.now())        # the hub's clock, which the scenarios freeze
    days = (now - when).total_seconds() / 86400
    return int(days) if days > period * STALE_FACTOR else None


VIEW_VERSION_SQL = "SELECT version,actor,created_at FROM bot_file_versions WHERE file_id=? AND version=?"


def prime_views(c, rows):
    """Read the current version of each file in `rows` (bot_files rows), one query a chunk, and seed what
    Pages.view reads for each, so a page list takes no version statement of its own."""
    if read_cache.cache(c) is None or not rows:
        return
    current = {row["id"]: row["current_version"] for row in rows}
    found = {}
    for part in H.chunks(sorted(current)):
        marks = ",".join("?" * len(part))
        for v in c.execute("SELECT v.file_id,v.version,v.actor,v.created_at FROM bot_file_versions v JOIN bot_files f "
                           "ON f.id=v.file_id AND f.current_version=v.version WHERE v.file_id IN (%s)" % marks, part):
            found[v["file_id"]] = v
    # The query joins each file to its current version only, so a file's row is its current_version's, or none.
    read_cache.prime(c, VIEW_VERSION_SQL, {(fid, ver): found.get(fid) for fid, ver in current.items()})


def may_manage(c, auth, who, row):
    """The maintainer bot and the people who may manage the page, shared with file visibility."""
    if who.role == "bot":
        bot = H.bot(c, H.actor_id(who.actor))
        return bool(bot and bot.get("state") != "archived" and bot["id"] == row["bot_id"])
    if who.role == "owner":
        return True
    if who.role != "human":
        return False
    if auth is None:
        from . import people, teammate_access, views
        archived = {bot["id"] for bot in H.bots(c) if bot.get("state") == "archived"}
        return teammate_access.is_admin(c, who) or people.manages(
            H.actor_id(who.actor), "bot", row["bot_id"], views.roster(c), views.entries(c), archived, True)
    return auth.bot_admin(who) or auth.manages(c, who, "bot", row["bot_id"])


def checked_audience(c, value):
    if value is None:
        return None
    from . import teammate_access
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise Problem("validation", "audience is an access audience or null", 422) from exc
    if value is None:
        return None
    level = teammate_access.parse_audience(c, value)
    return None if level["everyone"] else json.dumps(level, sort_keys=True, separators=(",", ":"))


def access_reset(c):
    """Group changes can revoke a page just as an edit to its audience can."""
    c.execute("INSERT INTO changes(topic,kind,ref,created_at) VALUES('tasks','bulk','page_access',?)", (H.now(),))


def group_access_changed(c):
    from . import files, counsel
    if any(H._json(row["audience"], {}).get("teams") for row in files.restricted_pages(c).values()):
        counsel.revoke_page_copies(c, None)
        access_reset(c)


def forget_audience_member(c, actor, field, ident):
    """Deletion never leaves a grant for a later teammate or group that reuses this identifier."""
    from . import audit_log, counsel, files
    changed = False
    for row in list(files.restricted_pages(c).values()):
        before = H._json(row["audience"], {})
        if ident not in before.get(field, []):
            continue
        after = {**before, field: [v for v in before[field] if v != ident]}
        c.execute("UPDATE pages SET audience=?,updated_at=? WHERE file_id=?", (json.dumps(after), H.now(), row["id"]))
        audit_log.write(c, actor, "page:" + row["id"] + ":access", before, after, "teammate-removed")
        changed = True
    if changed:
        counsel.revoke_page_copies(c, None)
        access_reset(c)


class Pages:
    def __init__(self, app, store, auth, files):
        self.app, self.store, self.auth, self.files = app, store, auth, files

    # ------------------------------------------------------------------ reads
    def src(self, c, who, row, version):
        """A fresh visual address for this version, as a message's visual gets one (visuals.src_for: a visual
        address, or Tico file hosting), or None when this install can't show pages inline: the page is then an Open
        card."""
        if row["archived"] or row["locator"] != "tico_blob":
            return None
        return visuals.src_for(self.app, self.store.settings, c, who, row["id"], version)

    def view(self, c, who, row, page, cache, names_=None, *, include_audience=False):
        """The contract's page read: what the status line and a card need."""
        v = read_cache.one(c, VIEW_VERSION_SQL, (row["id"], row["current_version"]))
        updated = v["created_at"] if v else row["last_activity_at"]
        replaced, replaced_title = page["replaced_by_id"], None
        if replaced:
            other = c.execute("SELECT * FROM bot_files WHERE id=?", (replaced,)).fetchone()
            if other and other["state"] != "deleted" and self.files.visible(c, who, other, cache):
                replaced_title = other["title"]
            else:
                replaced = None             # a replacement the reader may not see is not named
        by = v["actor"] if v else None
        manager = self.may_manage(c, who, row)
        names_ = names_ if names_ is not None else names.lookup(c, {"bot:" + row["bot_id"], *([by] if by else [])})
        return {"file_id": row["id"], "title": row["title"], "bot_id": row["bot_id"],
                "bot_name": names_.get("bot:" + row["bot_id"], row["bot_id"]), "version": row["current_version"],
                "updated_at": updated, "updated_by": by, "updated_by_name": names_.get(by, "") if by else "",
                "cadence": page["cadence"], "as_of": page["as_of"],
                "stale_days": stale_days(updated, page["cadence"]),
                "replaced_by_id": replaced, "replaced_by_title": replaced_title,
                "archived": bool(page["archived"] or row["archived"]),
                "task_id": row["task_id"] if row["task_id"] and self.files.task_info(c, who, row["task_id"], {}) else None,
                "src": self.src(c, who, row, row["current_version"]) if v else None,
                **({"restricted": page["audience"] is not None} if manager else {}),
                **({"audience": H._json(page["audience"], None)} if manager and include_audience else {})}

    def rows(self, c, ids):
        found = {}
        for part in H.chunks(ids):
            marks = ",".join("?" * len(part))
            for r in c.execute(f"SELECT f.*,p.cadence,p.as_of,p.replaced_by_id,p.audience,p.archived AS page_archived "
                               f"FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.file_id IN ({marks}) "
                               f"AND f.state<>'deleted'", part):
                found[r["id"]] = r
        return found

    @staticmethod
    def page_of(r):
        return {"cadence": r["cadence"], "as_of": r["as_of"], "replaced_by_id": r["replaced_by_id"],
                "archived": r["page_archived"], "audience": r["audience"]}

    def read(self, who, fid):
        with self.store.read() as c:
            r = self.rows(c, [fid]).get(fid)
            if not r or not self.files.visible(c, who, r, {}):
                raise Problem("not_found", "Page not found", 404)
            # What the full view offers this reader: Archive for its managers, Delete for the owner.
            return {"page": self.view(c, who, r, self.page_of(r), {}, include_audience=True), "can_manage": self.may_manage(c, who, r),
                    "can_delete": who.role == "owner",
                    "pinned": cards.pinned_places(cards.Ctx(c, self.auth, who, self.store.settings), fid)}

    def many(self, who, ids):
        """Up to 50 pages at once, in the order asked, leaving out the ones `who` may not read."""
        wanted = list(dict.fromkeys(i for i in ids if i))[:IDS_MAX]
        with self.store.read() as c:
            found = self.rows(c, wanted)
            from .files import prime_visible
            prime_visible(c, list(found.values()))
            cache = {}
            shown = [found[i] for i in wanted if i in found and self.files.visible(c, who, found[i], cache)]
            known = names.lookup(c, {"bot:" + r["bot_id"] for r in shown} | self._authors(c, shown))
            return {"pages": [self.view(c, who, r, self.page_of(r), cache, known) for r in shown]}

    @staticmethod
    def _authors(c, rows):
        out = set()
        for r in rows:
            v = c.execute("SELECT actor FROM bot_file_versions WHERE file_id=? AND version=?",
                          (r["id"], r["current_version"])).fetchone()
            if v:
                out.add(v["actor"])
        return out

    def listing(self, who, *, bot="", task="", archived=False, limit=20, cursor=""):
        """Pages `who` may read, newest first; archived ones only when asked. Paged; no totals, so a count never
        says what the reader may not see."""
        from .file_library import scan
        where = ["f.state<>'deleted'", "(p.archived=1 OR f.archived=1)" if archived else "p.archived=0 AND f.archived=0"]
        params = []
        if bot:
            with self.store.read() as c:
                bot = (H.resolve_bot(c, H.actor_id(bot)) or {}).get("id", H.actor_id(bot))
            where.append("f.bot_id=?")
            params.append(bot)
        if task:
            where.append("(f.task_id=? OR EXISTS (SELECT 1 FROM pins WHERE place=? AND item='page:' || f.id))")
            params += [task, "task:" + task]
        with self.store.read() as c:
            if task:
                self.auth.task(c, who, task)
            cache = {}
            found, nxt = scan(c, "SELECT f.*,p.cadence,p.as_of,p.replaced_by_id,p.audience,p.archived AS page_archived FROM pages p "
                              "JOIN bot_files f ON f.id=p.file_id", where, params, limit, cursor,
                              lambda r: self.files.visible(c, who, r, cache),
                              cursor_key=visuals.key_for(self.app, self.store.settings))
            known = names.lookup(c, {"bot:" + r["bot_id"] for r in found} | self._authors(c, found))
            return {"pages": [self.view(c, who, r, self.page_of(r), cache, known) for r in found],
                    "has_more": nxt is not None, "next_cursor": nxt}

    # ------------------------------------------------------------------ writes
    def publish(self, request, who, fields, data):
        """A bot publishes a page: cleaned HTML stored as a version of its file, and the page row beside it, in one
        transaction. The same path (or name) again is a new version of the same page."""
        audience_set, audience = "audience" in fields, fields.get("audience")
        fields = {k: (v if isinstance(v, (str, int, bool)) else "") for k, v in fields.items() if k != "audience"}
        name = str(fields.get("name") or str(fields.get("path") or "").rsplit("/", 1)[-1] or "")
        if not name.lower().endswith((".html", ".htm")):
            raise Problem("file_refused", "A page is an .html file", 422)
        try:
            BF.check_name(name)
        except BF.Refused as exc:
            raise Problem("file_refused", str(exc), 422) from exc
        cadence = check_cadence(fields["cadence"]) if fields.get("cadence") else None
        as_of = check_as_of(fields.get("as_of"))
        replaces = str(fields.get("replaces") or "").strip()
        task = str(fields.get("task") or "").strip()
        cleaned, unembedded = visuals.clean(data, "page", base=False)
        if len(cleaned) > visuals.LIMITS["page"]:
            raise Problem("file_refused", "A page is at most 10 MB", 422)
        # The original's digest stands for the version, so publishing the same page again is no new version.
        fields = {**fields, "name": name, "etag": hashlib.sha256(data).hexdigest()}

        def extra(c, row, changed):
            now = H.now()
            c.execute("INSERT INTO pages(file_id,cadence,as_of,created_at,updated_at) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(file_id) DO UPDATE SET cadence=COALESCE(?,cadence),as_of=?,updated_at=?,"
                      "archived=CASE WHEN ? THEN 0 ELSE archived END",
                      (row["id"], cadence or "none", as_of, now, now, cadence, as_of, now, int(bool(changed))))
            if audience_set:
                editor = who
                if who.role == "runner":
                    from .auth import Identity
                    editor = Identity("bot:" + row["bot_id"], "bot")
                self.set_audience(c, editor, row, checked_audience(c, audience))
            if replaces:
                old = c.execute("SELECT f.* FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.file_id=?",
                                (replaces,)).fetchone()
                if not old or old["bot_id"] != row["bot_id"] or old["state"] == "deleted":
                    raise Problem("not_found", "replaces names a page of this bot's", 404)
                if old["id"] == row["id"]:
                    raise Problem("validation", "A page cannot replace itself", 422)
                H._writer(c, "bot:" + row["bot_id"], task_id=old["task_id"])
                c.execute("UPDATE pages SET replaced_by_id=?,updated_at=? WHERE file_id=?", (row["id"], now, old["id"]))
                self.files.activity(c, old["id"], "bot:" + row["bot_id"], "modified", now=now,
                                    detail={"replaced_by": row["id"]})
            if task and row["task_id"] == task:
                self.pin_to_task(c, row, task)
            return {"page": self.view(c, who, row, c.execute("SELECT * FROM pages WHERE file_id=?",
                                                              (row["id"],)).fetchone(), {}, include_audience=True),
                    "unembedded": unembedded}
        return self.files.publish(request, who, fields, cleaned, extra=extra,
                                  extra_body={"cadence": cadence or "", "as_of": as_of or "", "replaces": replaces,
                                              **({"audience": audience} if audience_set else {})})

    @staticmethod
    def pin_to_task(c, row, task):
        """A page published with --task is pinned at the top of that task, by its bot, once. A task place never
        widens who reads the page (backend/cards.py WIDENING)."""
        place, item = "task:" + task, "page:" + row["id"]
        count = c.execute("SELECT count(*) FROM pins WHERE place=?", (place,)).fetchone()[0]
        if count >= cards.PLACE_MAX or c.execute("SELECT 1 FROM pins WHERE place=? AND item=?", (place, item)).fetchone():
            return
        c.execute("INSERT INTO pins(id,place,item,position,size,made_by,created_at) VALUES(?,?,?,?,?,?,?)",
                  (H.new_id(), place, item, count, "medium", "bot:" + row["bot_id"], H.now()))
        cards.file_activity(c, row["id"], "bot:" + row["bot_id"], "pinned", place)

    def may_manage(self, c, who, row):
        return may_manage(c, self.auth, who, row)

    def set_audience(self, c, who, row, value):
        if not self.may_manage(c, who, row):
            raise Problem("forbidden", "Only page managers change access", 403)
        before = c.execute("SELECT audience FROM pages WHERE file_id=?", (row["id"],)).fetchone()[0]
        if before == value:
            return
        now = H.now()
        c.execute("UPDATE pages SET audience=?,updated_at=? WHERE file_id=?", (value, now, row["id"]))
        from . import counsel
        counsel.revoke_page_copies(c, self.auth, row["id"])
        detail = {"before": H._json(before, None), "after": H._json(value, None)}
        self.files.activity(c, row["id"], who.actor, "page.access_changed", now=now, detail=detail)
        H.event(c, who.actor, "page.access_changed", row["id"], detail)
        # Task readers receive refreshed files; all open surfaces receive the existing reset invalidation.
        if row["task_id"]:
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now, row["task_id"]))
        access_reset(c)

    def edit(self, request, fid, body):
        who = request.state.identity

        def work(c):
            r = self.rows(c, [fid]).get(fid)
            if not r or not self.files.visible(c, who, r, {}):
                raise Problem("not_found", "Page not found", 404)
            if not self.may_manage(c, who, r):
                raise Problem("forbidden", "Only the page's bot, its managers, the owner and admins change a page", 403)
            H._writer(c, who.actor, task_id=r["task_id"])
            if "audience" in body.model_fields_set:
                value = body.audience.model_dump() if body.audience is not None else None
                self.set_audience(c, who, r, checked_audience(c, value))
            fields = {}
            if body.cadence is not None:
                fields["cadence"] = check_cadence(body.cadence)
            if body.as_of is not None:
                fields["as_of"] = check_as_of(body.as_of)
            if body.replaced_by is not None:
                target = body.replaced_by.strip()
                if target:
                    other = self.rows(c, [target]).get(target)
                    if not other or other["id"] == fid or not self.files.visible(c, who, other, {}):
                        raise Problem("not_found", "replaced_by names a page you can see", 404)
                fields["replaced_by_id"] = target or None
            if body.archived is not None:
                fields["archived"] = int(body.archived)
            if fields:
                now = H.now()
                c.execute("UPDATE pages SET " + ",".join(k + "=?" for k in fields) + ",updated_at=? WHERE file_id=?",
                          (*fields.values(), now, fid))
                action = ("archived" if fields.get("archived") == 1 else "modified")
                self.files.activity(c, fid, who.actor, action, now=now,
                                    detail={"page": sorted(fields), **({"restored": True} if fields.get("archived") == 0 else {})})
                H.event(c, who.actor, "page.edit", fid, {"fields": sorted(fields)})
            r = self.rows(c, [fid])[fid]
            return {"page": self.view(c, who, r, self.page_of(r), {}, include_audience=True)}
        return self.files.write(request, who, {"id": fid, **body.model_dump(exclude_unset=True)}, work)


def install_pages(app, store, auth, files):
    pages = Pages(app, store, auth, files)
    app.state.pages = pages
    publish_doc = {"requestBody": {"required": True, "content": {
        "text/html": {"schema": {"type": "string"}},
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
        "application/json": {"schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "text": {"type": "string"}, "content_base64": {"type": "string"},
            "title": {"type": "string"}, "cadence": {"enum": list(CADENCES)}, "as_of": {"type": "string"},
            "task": {"type": "string"}, "replaces": {"type": "string"}, "path": {"type": "string"},
            "scope": {"enum": ["task", "bot"]},
            "audience": {"anyOf": [{"$ref": "#/components/schemas/AccessAudience"}, {"type": "null"}]}}}}}}}

    @app.post("/api/v2/pages", openapi_extra=publish_doc)
    async def publish(request: Request):
        """A bot (or the computer running its turn) publishes a page: an .html file, raw with its fields in the
        query, or JSON with text or content_base64. Scripts and anything that would load from elsewhere are removed
        (`unembedded` lists what was). Up to 10 MB."""
        fields, data = files.read_input(request.headers.get("content-type", ""), await request.body(),
                                        request.query_params)
        return await asyncio.to_thread(pages.publish, request, request.state.identity, fields, data)

    @app.get("/api/v2/pages")
    def listing(request: Request, ids: str = "", bot: str = "", task: str = "", archived: bool = False,
                limit: int = 20, cursor: str = ""):
        """Pages the caller may read. `ids` (up to 50, comma-separated) reads those pages in that order and leaves out
        the unreadable; otherwise the newest first, filtered by bot or task (filed or pinned there)."""
        who = request.state.identity
        if ids:
            return pages.many(who, [i.strip() for i in ids.split(",")])
        return pages.listing(who, bot=bot, task=task, archived=archived, limit=max(1, min(limit, LIST_MAX)),
                             cursor=cursor)

    @app.get("/api/v2/pages/{fid}")
    def read(request: Request, fid: str):
        return pages.read(request.state.identity, fid)

    @app.patch("/api/v2/pages/{fid}")
    def edit(request: Request, fid: str, body: PageEdit):
        """Change a page's cadence, data date or replacement, or archive (`archived: true`) or restore it."""
        return pages.edit(request, fid, body)
    return pages
