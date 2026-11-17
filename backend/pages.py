"""Pages: versioned HTML shared by people and bots (docs/pages.md).

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
from . import page_links
from . import visuals
from .store import H, Problem
from . import visual_specs
from .api_contract import api_v2
from clients import bot_files as BF

CADENCES = {"none": 0, "daily": 1, "weekly": 7, "monthly": 30}
# A page is stale once 1.5x its cadence has passed without a new version.
STALE_FACTOR = 1.5
LIST_MAX = 50
IDS_MAX = 50
AS_OF = re.compile(r"\d{4}-\d{2}-\d{2}")


class PageLink(M.Contract):
    target: str = Field(min_length=1, max_length=200)
    role: str = Field(default="related", min_length=1, max_length=40)


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
    now = now or H.parse_ts(H.now())  # the hub's clock, which the scenarios freeze
    days = (now - when).total_seconds() / 86400
    return int(days) if days > period * STALE_FACTOR else None


VIEW_VERSION_SQL = "SELECT version,actor,created_at FROM bot_file_versions WHERE file_id=? AND version=?"


def prime_views(c, rows):
    """Read the current version of each file in `rows` (bot_files rows), one query a chunk, and seed what
    Pages.view reads for each, so a page list takes no version statement of its own."""
    if read_cache.cache(c) is None or not rows:
        return
    page_links.prime(c, rows)
    current = {row["id"]: row["current_version"] for row in rows}
    found = {}
    for part in H.chunks(sorted(current)):
        marks = ",".join("?" * len(part))
        for v in c.execute(
            "SELECT v.file_id,v.version,v.actor,v.created_at FROM bot_file_versions v JOIN bot_files f "
            "ON f.id=v.file_id AND f.current_version=v.version WHERE v.file_id IN (%s)" % marks,
            part,
        ):
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
            H.actor_id(who.actor), "bot", row["bot_id"], views.roster(c), views.entries(c), archived, True
        )
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
    from . import api_tools

    api_tools.forget_audience_member(c, actor, field, ident)  # tools' Who can see it, the same audience shape


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
                replaced = None  # a replacement the reader may not see is not named
        by = v["actor"] if v else None
        manager = self.may_manage(c, who, row)
        names_ = names_ if names_ is not None else names.lookup(c, {"bot:" + row["bot_id"], *([by] if by else [])})
        return {
            "links": page_links.links_of(c, self.auth, who, row["id"]),
            "writers": page_links.writers_of(c, row["id"]),
            "may_write": page_links.may_write(c, self.auth, who, row),
            "file_id": row["id"],
            "title": row["title"],
            "bot_id": row["bot_id"],
            "bot_name": names_.get("bot:" + row["bot_id"], row["bot_id"]),
            "version": row["current_version"],
            "updated_at": updated,
            "updated_by": by,
            "updated_by_name": names_.get(by, "") if by else "",
            "cadence": page["cadence"],
            "as_of": page["as_of"],
            "stale_days": stale_days(updated, page["cadence"]),
            "replaced_by_id": replaced,
            "replaced_by_title": replaced_title,
            "archived": bool(page["archived"] or row["archived"]),
            "task_id": row["task_id"]
            if row["task_id"] and self.files.task_info(c, who, row["task_id"], cache)
            else None,
            "src": self.src(c, who, row, row["current_version"]) if v else None,
            **({"restricted": page["audience"] is not None} if manager else {}),
            **({"audience": H._json(page["audience"], None)} if manager and include_audience else {}),
        }

    def rows(self, c, ids):
        found = {}
        for part in H.chunks(ids):
            marks = ",".join("?" * len(part))
            for r in c.execute(
                f"SELECT f.*,p.cadence,p.as_of,p.replaced_by_id,p.audience,p.archived AS page_archived "
                f"FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.file_id IN ({marks}) "
                f"AND f.state<>'deleted'",
                part,
            ):
                found[r["id"]] = r
        return found

    @staticmethod
    def page_of(r):
        return {
            "cadence": r["cadence"],
            "as_of": r["as_of"],
            "replaced_by_id": r["replaced_by_id"],
            "archived": r["page_archived"],
            "audience": r["audience"],
        }

    def read(self, who, fid):
        with self.store.read_transaction() as c:
            r = self.rows(c, [fid]).get(fid)
            if not r or not self.files.visible(c, who, r, {}):
                raise Problem("not_found", "Page not found", 404)
            # What the full view offers this reader: Archive for its managers, Delete for the owner.
            return {
                "page": self.view(c, who, r, self.page_of(r), {}, include_audience=True),
                "can_manage": self.may_manage(c, who, r),
                "can_delete": who.role == "owner",
                "pinned": cards.pinned_places(cards.Ctx(c, self.auth, who, self.store.settings), fid),
            }

    def many(self, who, ids):
        """Up to 50 pages at once, in the order asked, leaving out the ones `who` may not read."""
        wanted = list(dict.fromkeys(i for i in ids if i))[:IDS_MAX]
        with self.store.read_transaction() as c:
            found = self.rows(c, wanted)
            from .files import prime_visible

            prime_visible(c, list(found.values()))
            cache = {}
            shown = [found[i] for i in wanted if i in found and self.files.visible(c, who, found[i], cache)]
            prime_views(c, shown)
            known = names.lookup(c, {"bot:" + r["bot_id"] for r in shown} | self._authors(c, shown))
            return {"pages": [self.view(c, who, r, self.page_of(r), cache, known) for r in shown]}

    @staticmethod
    def _authors(c, rows):
        out = set()
        for r in rows:
            v = read_cache.one(c, VIEW_VERSION_SQL, (r["id"], r["current_version"]))
            if v:
                out.add(v["actor"])
        return out

    def listing(self, who, *, bot="", task="", linked="", role="", writer="", archived=False, limit=20, cursor=""):
        """Pages `who` may read, newest first; archived ones only when asked. Paged; no totals, so a count never
        says what the reader may not see."""
        from .file_library import scan

        where = [
            "f.state<>'deleted'",
            "(p.archived=1 OR f.archived=1)" if archived else "p.archived=0 AND f.archived=0",
        ]
        params = []
        if bot:
            with self.store.read() as c:
                bot = (H.resolve_bot(c, H.actor_id(bot)) or {}).get("id", H.actor_id(bot))
            where.append(
                "f.id IN (SELECT id FROM bot_files WHERE bot_id=? UNION SELECT page_id FROM page_links WHERE target=?)"
            )
            params += [bot, "bot:" + bot]
        if task:
            with self.store.read() as c:
                task = self.auth.resolve_task(c, who, task)
            where.append(
                "f.id IN (SELECT id FROM bot_files WHERE task_id=? UNION "
                "SELECT page_id FROM page_links WHERE target=? UNION "
                "SELECT substr(item,6) FROM pins WHERE place=? AND item LIKE 'page:%')"
            )
            params += [task, "task:" + task, "task:" + task]
        sql, args = page_links.filters(linked, role, writer)
        where.extend(sql)
        params.extend(args)
        with self.store.read_transaction() as c:
            if task:
                self.auth.task(c, who, task)
            cache = {}
            found, nxt = scan(
                c,
                "SELECT f.*,p.cadence,p.as_of,p.replaced_by_id,p.audience,p.archived AS page_archived FROM pages p "
                "JOIN bot_files f ON f.id=p.file_id",
                where,
                params,
                limit,
                cursor,
                lambda r: self.files.visible(c, who, r, cache),
                cursor_key=visuals.key_for(self.app, self.store.settings),
            )
            prime_views(c, found)
            known = names.lookup(c, {"bot:" + r["bot_id"] for r in found} | self._authors(c, found))
            return {
                "pages": [self.view(c, who, r, self.page_of(r), cache, known) for r in found],
                "has_more": nxt is not None,
                "next_cursor": nxt,
            }

    # ------------------------------------------------------------------ writes
    def publish(self, request, who, fields, data):
        """Publish cleaned HTML and links atomically; a page id continues its versions across writers."""
        importing = any(k in fields for k in ("written_by", "written_at"))
        if importing and who.role != "owner":
            raise Problem("forbidden", "Only the owner may import writer or time", 403)
        written_by = str(fields.get("written_by") or "")
        written_at = str(fields.get("written_at") or "")
        if written_by and (not written_by.startswith(("human:", "bot:")) or not written_by.partition(":")[2]):
            raise Problem("validation", "written_by is a bot or person actor", 422)
        if written_at and H.parse_ts(written_at) is None:
            raise Problem("validation", "written_at is an ISO timestamp", 422)
        audience_set, audience = "audience" in fields, fields.get("audience")
        if who.via == "assistant":
            audience_set = False  # a Make page turn keeps the page's default audience; only managers set it
        fields = {k: (v if isinstance(v, (str, int, bool)) else "") for k, v in fields.items() if k != "audience"}
        name = str(
            fields.get("name")
            or str(fields.get("path") or "").rsplit("/", 1)[-1]
            or ("page.html" if fields.get("page") or fields.get("as") else "")
        )
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
        spec = str(fields.get("spec") or "").lower() in ("1", "true", "yes")
        role = str(fields.get("as") or ("Spec" if spec else ""))
        task_role = page_links.role_name(role) if role else ""
        if spec:
            # A spec is a page on a task in a Product pipeline; its styles are inlined so the stored page reads the same anywhere.
            with self.store.read() as c:
                visual_specs.check_publish(c, task)
            data = visual_specs.inline_styles(data)
        cleaned, unembedded = visuals.clean(data, "page", base=False)
        if len(cleaned) > visuals.LIMITS["page"]:
            raise Problem("file_refused", "A page is at most 10 MB", 422)
        # The original's digest stands for the version, so publishing the same page again is no new version.
        fields = {**fields, "name": name, "etag": hashlib.sha256(data).hexdigest()}

        def extra(c, row, changed, editor, linked_task):
            now = H.now()
            c.execute(
                "INSERT INTO pages(file_id,cadence,as_of,created_at,updated_at,spec) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(file_id) DO UPDATE SET cadence=COALESCE(?,cadence),as_of=?,updated_at=?,"
                "spec=COALESCE(?,spec),archived=CASE WHEN ? THEN 0 ELSE archived END",
                (
                    row["id"],
                    cadence or "none",
                    as_of,
                    now,
                    now,
                    visual_specs.CARD if spec else None,
                    cadence,
                    as_of,
                    now,
                    visual_specs.CARD if spec else None,
                    int(bool(changed)),
                ),
            )
            actor = editor.actor
            page_links.link(c, row["id"], actor, "writer", actor)
            if importing:
                from . import audit_log

                imported = dict(
                    c.execute(
                        "SELECT actor,created_at FROM bot_file_versions WHERE file_id=? AND version=?",
                        (row["id"], row["current_version"]),
                    ).fetchone()
                )
                page_links.link(c, row["id"], imported["actor"], "writer", actor)
                audit_log.write(
                    c, who.actor, "page:" + row["id"] + ":import", None, {**imported, "version": row["current_version"]}
                )
            if linked_task:
                page_links.link(c, row["id"], "task:" + linked_task, task_role or "related", actor)
            from . import task_pages

            task_pages.submit_linked(c, row["id"], row["current_version"], actor)
            if audience_set:
                self.set_audience(c, editor, row, checked_audience(c, audience))
            if replaces:
                old = c.execute(
                    "SELECT f.* FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.file_id=?", (replaces,)
                ).fetchone()
                if (
                    not old
                    or old["state"] == "deleted"
                    or who.via == "assistant"
                    and old["task_id"] != row["task_id"]
                    or not self.files.visible(c, editor, old, {})
                ):
                    raise Problem("not_found", "replaces names a page of this bot's", 404)
                if not page_links.may_write(c, self.auth, editor, old):
                    raise Problem("forbidden", "Only page writers replace a page", 403)
                if old["id"] == row["id"]:
                    raise Problem("validation", "A page cannot replace itself", 422)
                H._writer(c, editor.actor, task_id=old["task_id"])
                c.execute("UPDATE pages SET replaced_by_id=?,updated_at=? WHERE file_id=?", (row["id"], now, old["id"]))
                self.files.activity(c, old["id"], editor.actor, "modified", now=now, detail={"replaced_by": row["id"]})
            if linked_task and row["task_id"] == linked_task:
                if (
                    who.via == "assistant"
                    and not c.execute(
                        "SELECT 1 FROM pins WHERE place=? AND item=?", ("task:" + linked_task, "page:" + row["id"])
                    ).fetchone()
                ):
                    self.require_pin_space(c, linked_task)
                self.pin_to_task(c, row, linked_task, actor=who.actor if who.via == "assistant" else None)
            shown = self.view(
                c,
                editor,
                row,
                c.execute("SELECT * FROM pages WHERE file_id=?", (row["id"],)).fetchone(),
                {},
                include_audience=True,
            )
            return {"page": shown, "links": shown["links"], "unembedded": unembedded}

        return self.files.publish(
            request,
            who,
            fields,
            cleaned,
            extra=extra,
            page_publish=True,
            task_role=task_role,
            extra_body={
                "cadence": cadence or "",
                "as_of": as_of or "",
                "replaces": replaces,
                **{k: fields.get(k, "") for k in ("page", "as", "written_by", "written_at")},
                **({"audience": audience} if audience_set else {}),
            },
        )

    def change_link(self, request, fid, target, role, *, remove=False):
        who = request.state.identity

        def work(c):
            row = self.rows(c, [fid]).get(fid)
            if not row or not self.files.visible(c, who, row, {}):
                raise Problem("not_found", "Page not found", 404)
            if not page_links.may_write(c, self.auth, who, row):
                raise Problem("forbidden", "Only page writers change its links", 403)
            selected = target
            kind, _, ident = selected.partition(":")
            chosen_role = page_links.role_name(role)
            if kind == "task":
                ident = self.auth.resolve_task(c, who, ident)
                if not remove:
                    self.auth.task(c, who, ident)
                selected = "task:" + ident
                if not remove and chosen_role not in ("writer", "related"):
                    from .task_reads import comment_rights

                    comment_rights(c, self.auth, who, ident)
            elif not remove:
                if kind == "bot":
                    bot = H.resolve_bot(c, ident)
                    if not bot or not self.auth.bot_access(c, who, bot["id"])["see"]:
                        raise Problem("not_found", "Bot not found", 404)
                    selected = "bot:" + bot["id"]
                elif kind == "human":
                    if not c.execute("SELECT 1 FROM humans WHERE id=?", (ident,)).fetchone():
                        raise Problem("not_found", "Person not found", 404)
                elif kind == "goal":
                    if not c.execute("SELECT 1 FROM goals WHERE id=?", (ident,)).fetchone():
                        raise Problem("not_found", "Goal not found", 404)
                elif kind == "page":
                    other = self.rows(c, [ident]).get(ident)
                    if not other or not self.files.visible(c, who, other, {}):
                        raise Problem("not_found", "Page not found", 404)
            if remove:
                changed = page_links.unlink(c, fid, selected, chosen_role, who.actor)
            else:
                changed = page_links.link(c, fid, selected, chosen_role, who.actor)
            if changed and kind == "task" and chosen_role not in ("related", "writer"):
                from . import task_pages

                task_pages.submit(c, ident, chosen_role, who.actor)
            H.event(
                c, who.actor, "page.unlink" if remove else "page.link", fid, {"target": selected, "role": chosen_role}
            )
            return {"page": self.view(c, who, row, self.page_of(row), {}, include_audience=True)}

        return self.files.write(request, who, {"id": fid, "target": target, "role": role}, work)

    @staticmethod
    def require_pin_space(c, task):
        if c.execute("SELECT count(*) FROM pins WHERE place=?", ("task:" + task,)).fetchone()[0] >= cards.PLACE_MAX:
            raise Problem("conflict", f"This task has {cards.PLACE_MAX} pins", 409)

    @staticmethod
    def pin_to_task(c, row, task, *, actor=None):
        """A page published with --task is pinned at the top of that task, by its bot, once. A task place never
        widens who reads the page (backend/cards.py WIDENING)."""
        actor = actor or "bot:" + row["bot_id"]
        place, item = "task:" + task, "page:" + row["id"]
        count = c.execute("SELECT count(*) FROM pins WHERE place=?", (place,)).fetchone()[0]
        if (
            count >= cards.PLACE_MAX
            or c.execute("SELECT 1 FROM pins WHERE place=? AND item=?", (place, item)).fetchone()
        ):
            return
        c.execute(
            "INSERT INTO pins(id,place,item,position,size,made_by,created_at) VALUES(?,?,?,?,?,?,?)",
            (H.new_id(), place, item, count, "medium", actor, H.now()),
        )
        cards.file_activity(c, row["id"], actor, "pinned", place)

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
            archive_only = body.model_fields_set <= {"archived"}
            if not self.may_manage(c, who, r) and not (archive_only and page_links.may_write(c, self.auth, who, r)):
                raise Problem("forbidden", "Only page managers change page settings; writers may archive", 403)
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
                c.execute(
                    "UPDATE pages SET " + ",".join(k + "=?" for k in fields) + ",updated_at=? WHERE file_id=?",
                    (*fields.values(), now, fid),
                )
                action = "archived" if fields.get("archived") == 1 else "modified"
                self.files.activity(
                    c,
                    fid,
                    who.actor,
                    action,
                    now=now,
                    detail={"page": sorted(fields), **({"restored": True} if fields.get("archived") == 0 else {})},
                )
                H.event(c, who.actor, "page.edit", fid, {"fields": sorted(fields)})
                if "archived" in fields and fields["archived"] != r["page_archived"]:
                    from . import task_pages

                    task_pages.submit_linked(c, fid, None, who.actor)
            r = self.rows(c, [fid])[fid]
            return {"page": self.view(c, who, r, self.page_of(r), {}, include_audience=True)}

        return self.files.write(request, who, {"id": fid, **body.model_dump(exclude_unset=True)}, work)


def install_pages(app, store, auth, files):
    pages = Pages(app, store, auth, files)
    app.state.pages = pages
    visual_specs.bind(files, auth.settings.public_url)
    publish_doc = {
        "requestBody": {
            "required": True,
            "content": {
                "text/html": {"schema": {"type": "string"}},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "text": {"type": "string"},
                            "content_base64": {"type": "string"},
                            "title": {"type": "string"},
                            "cadence": {"enum": list(CADENCES)},
                            "as_of": {"type": "string"},
                            "task": {"type": "string"},
                            "page": {"type": "string"},
                            "as": {"type": "string"},
                            "written_by": {"type": "string"},
                            "written_at": {"type": "string", "format": "date-time"},
                            "replaces": {"type": "string"},
                            "path": {"type": "string"},
                            "scope": {"enum": ["task", "bot"]},
                            "spec": {"type": "boolean"},
                            "audience": {"anyOf": [{"$ref": "#/components/schemas/AccessAudience"}, {"type": "null"}]},
                        },
                    }
                },
            },
        }
    }

    @app.post(
        "/api/v2/pages",
        **api_v2(
            "Pages",
            "publishPage",
            "Publish an HTML page or a new version by page id (raw or JSON); scripts and outside loads are removed",
            "PagePublished",
            openapi_extra=publish_doc,
        ),
    )
    async def publish(request: Request):
        """A person, bot or its computer publishes a page: an .html file, raw with its fields in the
        query, or JSON with text or content_base64. Scripts and anything that would load from elsewhere are removed
        (`unembedded` lists what was). Up to 10 MB."""
        fields, data = files.read_input(
            request.headers.get("content-type", ""), await request.body(), request.query_params
        )
        return await asyncio.to_thread(pages.publish, request, request.state.identity, fields, data)

    @app.get(
        "/api/v2/pages",
        **api_v2(
            "Pages",
            "listPages",
            "Pages the caller may read: `ids` (up to 50) in that order, leaving out the unreadable; else newest first by bot or task (filed on or pinned to it)",
            "PageList",
        ),
    )
    def listing(
        request: Request,
        ids: str = "",
        bot: str = "",
        task: str = "",
        linked: str = "",
        role: str = "",
        writer: str = "",
        archived: bool = False,
        limit: int = 20,
        cursor: str = "",
    ):
        """Pages the caller may read. `ids` (up to 50, comma-separated) reads those pages in that order and leaves out
        the unreadable; otherwise the newest first, filtered by bot or task (filed or pinned there)."""
        who = request.state.identity
        if ids:
            return pages.many(who, [i.strip() for i in ids.split(",")])
        return pages.listing(
            who,
            bot=bot,
            task=task,
            linked=linked,
            role=role,
            writer=writer,
            archived=archived,
            limit=max(1, min(limit, LIST_MAX)),
            cursor=cursor,
        )

    @app.get(
        "/api/v2/pages/{fid}",
        **api_v2("Pages", "getPage", "One page and its status line; 404 when the caller may not read it", "PageShown"),
    )
    def read(request: Request, fid: str):
        return pages.read(request.state.identity, fid)

    @app.post(
        "/api/v2/pages/{fid}/links",
        **api_v2("Pages", "linkPage", "Link a page to a task, bot, person, goal or page", "PageShown"),
    )
    def link_page(request: Request, fid: str, body: PageLink):
        return pages.change_link(request, fid, body.target, body.role)

    @app.delete(
        "/api/v2/pages/{fid}/links",
        **api_v2("Pages", "unlinkPage", "Remove a page link, keeping at least one writer", "PageShown"),
    )
    def unlink_page(request: Request, fid: str, target: str, role: str = "related"):
        return pages.change_link(request, fid, target, role, remove=True)

    @app.patch(
        "/api/v2/pages/{fid}",
        **api_v2(
            "Pages",
            "editPage",
            "Change a page's audience, cadence, data date or replacement, or archive or restore it (the bot, owner and bot managers)",
            "PageShown",
        ),
    )
    def edit(request: Request, fid: str, body: PageEdit):
        """Change a page's cadence, data date or replacement, or archive (`archived: true`) or restore it."""
        return pages.edit(request, fid, body)

    return pages
