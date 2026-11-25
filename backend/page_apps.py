# pyright: strict
"""Page apps (docs/pages.md#apps): a page version that reads Tico data through a bridge, and the page's own data rows.

A writer opts a version in at publish (`--app reads=tasks,files`). What it may read is stored on that version
(bot_file_versions.app) and counted in the version check, so a change of access is a new version with its own approval.
An app version is served sealed (visuals.policy: no allow-same-origin, an opaque origin), so sibling pages on the same
host reach neither its frame nor its storage. Tico's script in it (`page-app-bridge.js`) hands the app a MessageChannel
port; the web app that frames it accepts that port once, from that frame only, and sends each call to one route,
POST /api/v2/pages/{id}/app, as the viewer. The call carries a key minted for that viewer, page and version
(`nonce`), and a call for a version that is no longer current is refused.

Every read is the viewer's own list, cut to the rows the page's writer (its current version's author) may also read:
with the network open, a script can send out only what its writer already holds. Either identity gone: nothing.

Page data (page_app_rows) belongs to the page, not a version: writers and agents keep it with `tico page data`, the app
reads it with `store`.
"""

import base64
import hashlib
import hmac
import json
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, cast

from fastapi import Request
from pydantic import Field

from . import app_view
from . import models as M
from . import names
from . import page_app_writes
from . import page_links
from . import teammate_access
from . import visuals
from .api_answers.pages import PageAppActAnswer, PageAppAnswer, PageDataList, PageDataRemoved, PageDataRow
from .api_contract import api_v2
from .store import H, Problem

READS = ("tasks", "files", "pages", "kpis", "contacts", "store")
READ_OPS = ("me", "query", "thumb", "open", "store.get", "store.list")
OPS = READ_OPS + page_app_writes.OPS
ROWS_MAX = 200  # rows in one reply
REPLY_MAX = 1024 * 1024  # bytes in one reply
RATE = 20  # calls a second, per frame (its key)
THUMB_MAX = 200 * 1024
NONCE_LIFE = 12 * 3600  # the frame outlives its two-hour address; the key still names one viewer, page and version
DATA_ROW_MAX = 64 * 1024
DATA_ROWS_MAX = 10_000
DATA_BYTES_MAX = 20 * 1024 * 1024
COLLECTION = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
ROW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,99}")
BRIDGE = Path(__file__).with_name("page-app-bridge.js").read_text().strip()


# ---------------------------------------------------------------------------------------------- the declaration
def parse(text: Any) -> str | None:
    """The stored declaration (canonical JSON) of `--app reads=tasks,files`, or None for no app. Clauses are separated by
    spaces or semicolons; an unknown clause or name is refused, never dropped."""
    text = str(text or "").strip()
    if not text:
        return None
    reads: set[str] = set()
    writes: set[str] = set()
    for clause in re.split(r"[;\s]+", text):
        if not clause:
            continue
        key, _, names_ = clause.partition("=")
        if key == "writes":
            writes.update(page_app_writes.parse_writes(names_.split(","), check_collection))
            continue
        if key != "reads":
            raise Problem("validation", "app declares reads=" + ",".join(READS) + " and writes=...", 422)
        for name in filter(None, names_.split(",")):
            if name not in READS:
                raise Problem("validation", f"app reads one of {', '.join(READS)}; not {name[:40]}", 422)
            reads.add(name)
    if not reads and not writes:
        return None
    found: dict[str, list[str]] = {"reads": sorted(reads)}
    if writes:
        found["writes"] = sorted(writes)
    return json.dumps(found, separators=(",", ":"))


def declared(value: Any) -> dict[str, Any]:
    """The declaration stored on a version: {"reads": [...], "writes": [...]?}, or {} for a page that is no app."""
    try:
        found = json.loads(value) if value else None
    except (TypeError, ValueError):
        found = None
    return cast(dict[str, Any], found) if isinstance(found, dict) else {}


# ---------------------------------------------------------------------------------------------- the key
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _mac(key: bytes, actor: str, body: str) -> str:
    return _b64(hmac.new(key, b"page-app\0" + actor.encode() + b"\0" + body.encode(), hashlib.sha256).digest())


def nonce(key: bytes, actor: str, fid: str, version: int, now: float | None = None) -> str:
    """The key a frame's calls carry: this page and version, for this viewer, until it expires."""
    expires = int(now if now is not None else time.time()) + NONCE_LIFE
    body = _b64(json.dumps([fid, int(version), expires], separators=(",", ":")).encode())
    return body + "." + _mac(key, actor, body)


def nonce_ok(key: bytes, token: Any, actor: str, fid: str, version: int, now: float | None = None) -> bool:
    try:
        body, mac = str(token).split(".", 1)
        if not hmac.compare_digest(mac, _mac(key, actor, body)):
            return False
        named, number, expires = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return False
    return named == fid and number == int(version) and int(now if now is not None else time.time()) < int(expires)


# A start tag ends at the first `>` outside its quoted attribute values.
TAG = re.compile(rb"<!--.*?-->|<!doctype[^>]*>|<([a-z][^\s>/]*)(?:\"[^\"]*\"|'[^']*'|[^'\">])*>", re.I | re.S)


def with_bridge(data: bytes, key: str) -> bytes:
    """The page with Tico's bridge as its first script: after the leading doctype, <html> and <head> tags (never before
    the doctype, which would put the page in quirks mode), read in document order up to the first other tag, so a
    `<head>` inside a script or comment is never taken for the real one."""
    script = b'<script data-tico-app="' + key.encode() + b'">' + BRIDGE.encode() + b"</script>"
    end = 0
    for m in TAG.finditer(data[:65536]):
        name = (m.group(1) or b"").lower()
        if m.group(0).startswith(b"<!--"):
            continue
        if name not in (b"html", b"head") and not m.group(0)[:9].lower() == b"<!doctype":
            break
        end = m.end()
        if name == b"head":
            break
    return data[:end] + script + data[end:]


class Rate:
    """At most RATE calls in any one second for each frame's key; a bounded memory of keys."""

    def __init__(self, limit: int = RATE, keep: int = 4096) -> None:
        self.limit, self.keep, self.lock = limit, keep, threading.Lock()
        self.seen: dict[str, deque[float]] = {}

    def take(self, key: str, now: float | None = None) -> bool:
        with self.lock:
            # read under the lock, so each key's calls stay in time order (expiry reads only the oldest)
            now = time.monotonic() if now is None else now
            calls = self.seen.get(key)
            if calls is None:
                if len(self.seen) >= self.keep:
                    self.seen = {k: v for k, v in self.seen.items() if v and v[-1] > now - 1}
                calls = self.seen[key] = deque[float]()
            while calls and calls[0] <= now - 1:
                calls.popleft()
            if len(calls) >= self.limit:
                return False
            calls.append(now)
            return True


# ---------------------------------------------------------------------------------------------- page data
def check_collection(name: Any) -> str:
    if not isinstance(name, str) or not COLLECTION.fullmatch(name):
        raise Problem("validation", "A collection is 1-40 lowercase letters, digits, - or _", 422)
    return name


def check_row_id(row_id: Any) -> str:
    if not isinstance(row_id, str) or not ROW_ID.fullmatch(row_id):
        raise Problem("validation", "A row id is 1-100 letters, digits, and . _ : @ -", 422)
    return row_id


def _row(r: Any) -> dict[str, Any]:
    return {
        "collection": r["collection"],
        "id": r["row_id"],
        "data": json.loads(r["data"]),
        "updated_by": r["updated_by"],
        "updated_at": r["updated_at"],
    }


def data_get(c: Any, fid: str, collection: Any, row_id: Any) -> dict[str, Any] | None:
    r = c.execute(
        "SELECT * FROM page_app_rows WHERE page_id=? AND collection=? AND row_id=?",
        (fid, check_collection(collection), check_row_id(row_id)),
    ).fetchone()
    return _row(r) if r else None


def data_list(c: Any, fid: str, collection: Any, after: Any = "", limit: Any = 50) -> dict[str, Any]:
    """A page of a collection's rows by id, at most REPLY_MAX bytes; `next` is the id to start after."""
    limit = max(1, min(int(limit or 50), ROWS_MAX))
    rows: list[dict[str, Any]] = []
    size = 0
    found = c.execute(
        "SELECT * FROM page_app_rows WHERE page_id=? AND collection=? AND row_id>? ORDER BY row_id LIMIT ?",
        (fid, check_collection(collection), str(after or ""), limit + 1),
    ).fetchall()
    for r in found[:limit]:
        row = _row(r)
        # Measured as the reply is (json.dumps, non-ASCII escaped), so the list fits both the route and the bridge.
        size += len(json.dumps(row)) + 2
        if rows and size > REPLY_MAX - 1024:
            break
        rows.append(row)
    more = len(rows) < len(found)
    return {"rows": rows, "next": rows[-1]["id"] if more and rows else None}


def data_set(c: Any, fid: str, collection: Any, row_id: Any, value: Any, actor: str) -> dict[str, Any] | None:
    """Write one row as `actor`; a page keeps at most DATA_ROWS_MAX rows and DATA_BYTES_MAX bytes."""
    check_collection(collection)
    check_row_id(row_id)
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if len(text.encode()) > DATA_ROW_MAX:
        raise Problem("too_large", "A row's data is at most 64 KB", 413)
    count, size = c.execute(
        "SELECT count(*),coalesce(sum(length(CAST(data AS BLOB))),0) FROM page_app_rows "
        "WHERE page_id=? AND NOT (collection=? AND row_id=?)",
        (fid, collection, row_id),
    ).fetchone()
    if count >= DATA_ROWS_MAX or size + len(text.encode()) > DATA_BYTES_MAX:
        raise Problem("too_large", "A page keeps at most 10,000 rows and 20 MB of data", 413)
    now = H.now()
    c.execute(
        "INSERT INTO page_app_rows(page_id,collection,row_id,data,updated_by,updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(page_id,collection,row_id) DO UPDATE SET data=excluded.data,updated_by=excluded.updated_by,"
        "updated_at=excluded.updated_at",
        (fid, collection, row_id, text, actor, now),
    )
    return data_get(c, fid, collection, row_id)


def data_rm(c: Any, fid: str, collection: Any, row_id: Any) -> bool:
    return bool(
        c.execute(
            "DELETE FROM page_app_rows WHERE page_id=? AND collection=? AND row_id=?",
            (fid, check_collection(collection), check_row_id(row_id)),
        ).rowcount
    )


# ---------------------------------------------------------------------------------------------- the route
class AppCall(M.Contract):
    version: int = Field(ge=1)
    nonce: str = Field(min_length=10, max_length=400)
    op: str = Field(min_length=2, max_length=20)
    args: dict[str, Any] = Field(default_factory=dict)


class DataRow(M.Contract):
    data: Any


TASK_FIELDS = ("id", "number", "title", "status", "pipeline_id", "stage_id", "owner_id", "assignee_id", "labels")
FILE_FIELDS = ("id", "title", "kind", "library_kind", "mime", "version", "task_id", "last_activity_at")
PAGE_FIELDS = ("file_id", "title", "version", "updated_at", "cadence", "stale_days", "task_id")
KPI_FIELDS = ("id", "name", "unit", "owner_id", "cadence", "latest", "status")
CONTACT_FIELDS = ("id", "type", "name", "status", "primary_email", "primary_domain", "last_activity_at")


def _pick(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {k: row.get(k) for k in fields if k in row}


class PageApps:
    def __init__(self, app: Any, store: Any, auth: Any, files: Any, pages: Any, visible_tasks: Any) -> None:
        self.app, self.store, self.auth, self.files, self.pages = app, store, auth, files, pages
        self.visible_tasks, self.rate, self.pending = visible_tasks, Rate(), page_app_writes.Pending()

    def key(self) -> bytes:
        return cast(bytes, cast(Any, visuals.key_for)(self.app, self.store.settings))

    # ------------------------------------------------------------------ who
    def _page(self, c: Any, who: Any, fid: str) -> Any:
        row = self.pages.rows(c, [fid]).get(fid)
        if not row or not self.files.visible(c, who, row, {}):
            raise Problem("not_found", "Page not found", 404)
        return row

    def _writer(self, c: Any, actor: Any) -> Any:
        """The version's writer as Tico checks them now (an agent as itself, without a run), or a refusal: a writer who
        is gone lends the app nothing. Every read below is cut to what they may read."""
        writer: Any = cast(Any, visuals.token_reader)(c, self.auth, actor)
        if writer is None:
            raise Problem("writer_gone", "This app's writer can no longer read for it", 403)
        return writer

    # ------------------------------------------------------------------ the call
    def check_open(
        self, c: Any, who: Any, fid: str, version: int, nonce: str | None = None
    ) -> tuple[dict[str, Any], Any, Any]:
        """The declaration, writer and page row for a call on `version`, or a refusal: the page gone from the viewer,
        archived, a newer version out, no app, the frame's key (`nonce`, when given) not this viewer's or over its rate,
        or the writer gone."""
        row = self._page(c, who, fid)
        if row["archived"] or row["page_archived"]:
            raise Problem("app_stopped", "This page is archived; its app is stopped", 409)
        if version != row["current_version"]:
            raise Problem("version_replaced", "A newer version of this page is out; reload it", 409)
        v = c.execute(
            "SELECT app,actor FROM bot_file_versions WHERE file_id=? AND version=?", (fid, version)
        ).fetchone()
        decl = declared(v["app"] if v else None)
        if not decl.get("reads") and not decl.get("writes"):
            raise Problem("not_an_app", "This page version is not an app", 409)
        if nonce is not None:
            if not nonce_ok(self.key(), nonce, who.actor, fid, version):
                raise Problem("forbidden", "This frame's key is not for you, this page and this version", 403)
            if not self.rate.take(nonce):
                raise Problem("rate_limited", "At most 20 calls a second", 429, True)
        return decl, self._writer(c, v["actor"]), row

    def keeps_data(self, c: Any, writer: Any, row: Any) -> None:
        """The page's data is its writers': an author who is no longer one, or no longer sees the page, lends their app
        none of it, to read or to write. Its maintainer agent is excepted alone: as itself it never sees a chat's
        page."""
        from . import pages

        maintainer = writer.role == "bot" and cast(Any, pages).may_manage(c, self.auth, writer, row)
        if not page_links.may_write(c, self.auth, writer, row) or not (
            maintainer or self.files.visible(c, writer, row, {})
        ):
            raise Problem("writer_gone", "This app's writer no longer keeps this page's data", 403)

    def call(self, who: Any, fid: str, body: AppCall) -> dict[str, Any]:
        if body.op not in OPS:
            raise Problem("validation", "op is one of " + ", ".join(OPS), 422)
        if who.view and body.op not in READ_OPS:  # a bot's read-only view (backend/app_view.py) runs only the reads
            raise Problem(app_view.CODE, app_view.REFUSED, 403)
        # One snapshot: the version checked current is the one whose data the op reads.
        with self.store.read_transaction() as c:
            decl, writer, row = self.check_open(c, who, fid, body.version, body.nonce)
            if body.op == "act":
                return {"result": page_app_writes.hold(self, c, who, writer, fid, body, decl)}
            if body.op not in page_app_writes.STORE_OPS:
                result = self.op(c, who, writer, row, decl.get("reads") or [], body.op, body.args)
                if len(json.dumps(result, default=str)) > REPLY_MAX:
                    raise Problem("too_large", "A reply is at most 1 MB; ask for fewer rows", 413)
                return {"result": result}

        def opened(c: Any) -> dict[str, Any]:  # checked again under the write lock: the page may have changed
            decl, writer, row = self.check_open(c, who, fid, body.version)
            self.keeps_data(c, writer, row)
            return decl

        return {"result": page_app_writes.store_write(self, who, fid, body, opened)}

    def op(self, c: Any, who: Any, writer: Any, row: Any, reads: list[str], op: str, args: dict[str, Any]) -> Any:
        fid = row["id"]
        if op == "me":
            return {"actor": who.actor, "name": cast(Any, names.lookup)(c, {who.actor}).get(who.actor, "")}
        if op == "open":
            return {"href": self.href(c, who, writer, reads, str(args.get("ref") or ""))}
        if op == "thumb":
            need(reads, "files")
            return self.thumb(c, who, writer, str(args.get("file") or ""))
        if op.startswith("store."):
            need(reads, "store")
            self.keeps_data(c, writer, row)
            if op == "store.get":
                return {"row": data_get(c, fid, args.get("collection"), args.get("id"))}
            return data_list(c, fid, args.get("collection"), args.get("after") or "", _limit(args))
        kind = str(args.get("kind") or "")
        need(reads, kind if kind in READS and kind != "store" else "")
        return getattr(self, "query_" + kind)(c, who, writer, args)

    # ------------------------------------------------------------------ reads: the viewer's list, cut to the writer's
    def query_tasks(self, c: Any, who: Any, writer: Any, args: dict[str, Any]) -> dict[str, Any]:
        both = f"({self.auth.task_sql(c, who)}) AND ({self.auth.task_sql(c, writer)})"
        offset = _offset(args.get("cursor"))
        rows, more = self.visible_tasks(
            c,
            who,
            status=_text(args.get("status")),
            label=_text(args.get("label")),
            q=_text(args.get("q")),
            limit=_limit(args),
            offset=offset,
            visible_sql=both,
        )
        # The people on each row as the writer may be told of them, too (the viewer's view already hides theirs).
        rows = cast(Any, teammate_access.Known)(c, writer, self.auth).tasks(rows)
        return {"rows": [_pick(r, TASK_FIELDS) for r in rows], "next": str(more) if more else None}

    def _task_filter(self, c: Any, who: Any, writer: Any, args: dict[str, Any]) -> str | None:
        """A task filter both may read, else None: a list on another task would tell the app what links to it."""
        task = _text(args.get("task")) or ""
        for x in (who, writer) if task else ():
            try:
                self.auth.task(c, x, task)
            except Problem:
                return None
        return task

    def query_files(self, c: Any, who: Any, writer: Any, args: dict[str, Any]) -> dict[str, Any]:
        task = self._task_filter(c, who, writer, args)
        if task is None:
            return {"rows": [], "next": None}
        listed = self.app.state.library.listing(
            who,
            kind=_text(args.get("library_kind")) or "",
            task=task,
            limit=min(_limit(args), 50),
            cursor=_cursor(args.get("cursor")) or "",
        )
        kept = self._writer_files(c, writer, [f["id"] for f in listed["files"]])
        # Tags and metadata join here once files carry them (rows stay the general file fields).
        rows = [_pick(f, FILE_FIELDS) for f in listed["files"] if f["id"] in kept]
        return {"rows": rows, "next": listed.get("next_cursor") if listed.get("has_more") else None}

    def query_pages(self, c: Any, who: Any, writer: Any, args: dict[str, Any]) -> dict[str, Any]:
        task = self._task_filter(c, who, writer, args)
        if task is None:
            return {"rows": [], "next": None}
        listed = self.pages.listing(
            who,
            task=task,
            limit=min(_limit(args), 50),
            cursor=_cursor(args.get("cursor")) or "",
        )
        kept = self._writer_files(c, writer, [p["file_id"] for p in listed["pages"]], pages=True)
        rows = [{"id": p["file_id"], **_pick(p, PAGE_FIELDS[1:])} for p in listed["pages"] if p["file_id"] in kept]
        return {"rows": rows, "next": listed.get("next_cursor") if listed.get("has_more") else None}

    def _writer_files(self, c: Any, writer: Any, ids: list[str], pages: bool = False) -> set[str]:
        from . import files

        found: dict[str, Any] = self.pages.rows(c, ids) if pages else {}
        if not pages:
            for part in H.chunks(ids):
                marks = ",".join("?" * len(part))
                found.update({r["id"]: r for r in c.execute(f"SELECT * FROM bot_files WHERE id IN ({marks})", part)})
        cast(Any, files).prime_visible(c, list(found.values()))
        cache: dict[str, Any] = {}
        return {fid for fid, r in found.items() if self.files.visible(c, writer, r, cache)}

    def query_kpis(self, c: Any, who: Any, writer: Any, args: dict[str, Any]) -> dict[str, Any]:
        from . import goal_routes, kpis

        K, hidden_owners = cast(Any, kpis), cast(Any, goal_routes)._hidden_owners
        hidden: set[str] = hidden_owners(c, self.auth, who) | hidden_owners(c, self.auth, writer)
        offset, limit = _offset(args.get("cursor")), _limit(args)
        records = [
            dict(r)
            for r in c.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created_at,id")
            if r["owner_id"] not in hidden
        ]
        page = records[offset : offset + limit]
        data = K.effective_many(c, [r["id"] for r in page])
        rows = [_pick(K.view(r, data.get(r["id"], []), None), KPI_FIELDS) for r in page]
        more = len(records) > offset + limit
        return {"rows": rows, "next": str(offset + limit) if more else None}

    def query_contacts(self, c: Any, who: Any, writer: Any, args: dict[str, Any]) -> dict[str, Any]:
        from .entities import store as ES

        viewer, lender = (page_links.entity_reader(c, self.auth, x) for x in (who, writer))
        if not viewer or not lender:
            return {"rows": [], "next": None}
        kind = _text(args.get("type")) or "organization"
        if not ES.type_exists(c, kind) or kind in ES.NOT_CONTACTS:
            raise Problem("not_found", f"No contact type {kind}", 404)
        try:
            # Both readers' rule in the one query, so each row and the cursor after it are ones both may see.
            listed = ES.find(
                c,
                viewer,
                kind,
                name=_text(args.get("name")),
                status=_text(args.get("status")),
                after=_cursor(args.get("cursor")),
                limit=min(_limit(args), ES.LIMIT),
                also=lender,
            )
        except ValueError as error:
            raise Problem("validation", str(error), 422) from None
        return {"rows": [_pick(r, CONTACT_FIELDS) for r in listed["rows"]], "next": listed.get("next")}

    def thumb(self, c: Any, who: Any, writer: Any, fid: str) -> dict[str, Any]:
        """A file's small picture as a data: address, for a file both may read: its thumbnail, or the picture itself
        when it is small enough; null when it has neither."""
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        if not row or row["archived"] or any(not self.files.visible(c, x, row, {}) for x in (who, writer)):
            raise Problem("not_found", "File not found", 404)
        v = c.execute(
            "SELECT v.*,m.thumb_blob_id AS media_thumb FROM bot_file_versions v LEFT JOIN blob_media m "
            "ON m.blob_id=v.blob_id WHERE v.file_id=? AND v.version=?",
            (fid, row["current_version"]),
        ).fetchone()
        if not v:
            return {"url": None}
        thumb = v["thumb_blob_id"] or v["media_thumb"]
        blob: Any = c.execute("SELECT * FROM blobs WHERE id=?", (thumb,)).fetchone() if thumb else None
        stored_type = cast(Any, visuals.stored_type)
        stored = str(stored_type(v["mime"]) or "")
        if blob is None and visuals.KINDS.get(stored) == "image":
            blob = {"digest": v["digest"], "size": v["size"], "content_type": stored}
        if not blob or blob["size"] > THUMB_MAX:
            return {"url": None}
        mime = str(stored_type(blob["content_type"]) or "")
        if visuals.KINDS.get(mime) != "image":
            return {"url": None}
        data = self.app.state.blobs.get(blob["digest"])
        return {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}

    def href(self, c: Any, who: Any, writer: Any, reads: list[str], ref: str) -> str:
        """Where Tico opens a task, file or page of a declared kind that both may read: the app goes there, never the
        frame. An undeclared kind or one either may not read is refused, so open answers nothing a query would not."""
        kind, _, ident = ref.partition(":")
        if kind == "task" and ident:
            need(reads, "tasks")
            try:
                for x in (who, writer):
                    self.auth.task(c, x, ident)
            except Problem:
                raise Problem("not_found", "Task not found", 404) from None
            return "/task/" + ident
        if kind in ("file", "page") and ident:
            need(reads, kind + "s")
            if kind == "page":
                row = self.pages.rows(c, [ident]).get(ident)
                shown = row and not row["page_archived"]
            else:
                row = c.execute("SELECT * FROM bot_files WHERE id=? AND state<>'deleted'", (ident,)).fetchone()
                shown = bool(row)
            if shown and not row["archived"] and all(self.files.visible(c, x, row, {}) for x in (who, writer)):
                return "/library/files?file=" + ident
            raise Problem("not_found", "File not found", 404)
        raise Problem("validation", "open takes task:<id>, file:<id> or page:<id>", 422)

    # ------------------------------------------------------------------ page data, for writers and agents
    def data_read(
        self, who: Any, fid: str, collection: str, row_id: str | None = None, after: str = "", limit: int = 50
    ) -> dict[str, Any]:
        with self.store.read() as c:
            self._writable(c, who, fid)
            if row_id is None:
                return data_list(c, fid, collection, after, limit)
            found = data_get(c, fid, collection, row_id)
        if found is None:
            raise Problem("not_found", "No such row", 404)
        return {"row": found}

    def data_write(
        self, request: Any, fid: str, collection: str, row_id: str, value: Any = None, remove: bool = False
    ) -> Any:
        who = request.state.identity

        def work(c: Any) -> dict[str, Any]:
            self._writable(c, who, fid)
            if remove:
                return {"removed": data_rm(c, fid, collection, row_id)}
            return {"row": data_set(c, fid, collection, row_id, value, who.actor)}

        body = {"page": fid, "collection": collection, "id": row_id, "remove": remove, "data": value}
        return self.files.write(request, who, body, work)

    def _writable(self, c: Any, who: Any, fid: str) -> Any:
        row = self._page(c, who, fid)
        if not page_links.may_write(c, self.auth, who, row):
            raise Problem("forbidden", "Only the page's writers keep its data", 403)
        return row


def need(reads: list[str], name: str) -> None:
    if name not in reads:
        raise Problem("not_declared", f"This app did not declare reads={name or '…'}", 403)


def _text(value: Any) -> str | None:
    return str(value)[:200] if isinstance(value, (str, int)) and str(value) else None


def _cursor(value: Any) -> str | None:
    """A list's cursor as its list made it (a contact's carries its name, past _text's length), within a bound."""
    return value if isinstance(value, str) and 0 < len(value) <= 4096 else None


def _limit(args: dict[str, Any]) -> int:
    try:
        return max(1, min(int(args.get("limit") or 50), ROWS_MAX))
    except (TypeError, ValueError):
        return 50


def _offset(cursor: Any) -> int:
    try:
        return max(0, int(cursor or 0))
    except (TypeError, ValueError):
        return 0


def install_page_apps(app: Any, store: Any, auth: Any, files: Any, pages: Any) -> PageApps:
    from . import task_list_routes

    apps = PageApps(app, store, auth, files, pages, cast(Any, task_list_routes).readers(auth)[3])
    app.state.page_apps = apps

    @app.post(
        "/api/v2/pages/{fid}/app",
        **api_v2(
            "Pages",
            "callPageApp",
            "A page app's call through Tico's bridge, as the viewer: me, query, thumb, open, store.get, store.list, "
            "store.set and store.rm in a collection the version opens, or act, which holds declared Tico writes for the "
            "viewer's confirm (POST /api/v2/pages/{fid}/app/act)",
            PageAppAnswer,
            idempotency="Not needed: reads, and store writes by row id, are safe to repeat; act only holds actions.",
        ),
    )
    def call(request: Request, fid: str, body: AppCall) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """One call from an app frame. Reads are the viewer's, cut to what the page's writer may also read."""
        return apps.call(request.state.identity, fid, body)

    @app.post(
        "/api/v2/pages/{fid}/app/act",
        **api_v2(
            "Pages",
            "answerPageAppAct",
            "The viewer's own tap on a page app's confirm sheet: confirm runs the held actions once, as the viewer, "
            "each through its own route; or cancel. Never callable by a bot, the Assistant or a token",
            PageAppActAnswer,
            idempotency="Not needed: the key works once.",
        ),
    )
    async def act(  # pyright: ignore[reportUnusedFunction]
        request: Request, fid: str, body: page_app_writes.ActAnswer
    ) -> dict[str, Any]:
        return await page_app_writes.answer(apps, request, fid, body)

    @app.get(
        "/api/v2/pages/{fid}/data",
        **api_v2("Pages", "listPageData", "A page's data rows in one collection, by id (its writers)", PageDataList),
    )
    def data_list_route(  # pyright: ignore[reportUnusedFunction]
        request: Request, fid: str, collection: str, after: str = "", limit: int = 50
    ) -> dict[str, Any]:
        return apps.data_read(request.state.identity, fid, collection, after=after, limit=limit)

    @app.get(
        "/api/v2/pages/{fid}/data/{collection}/{row_id}",
        **api_v2("Pages", "getPageData", "One of a page's data rows (its writers)", PageDataRow),
    )
    def data_get_route(  # pyright: ignore[reportUnusedFunction]
        request: Request, fid: str, collection: str, row_id: str
    ) -> dict[str, Any]:
        return apps.data_read(request.state.identity, fid, collection, row_id)

    @app.put(
        "/api/v2/pages/{fid}/data/{collection}/{row_id}",
        **api_v2("Pages", "setPageData", "Write one of a page's data rows (its writers; at most 64 KB)", PageDataRow),
    )
    def data_set_route(  # pyright: ignore[reportUnusedFunction]
        request: Request, fid: str, collection: str, row_id: str, body: DataRow
    ) -> Any:
        return apps.data_write(request, fid, collection, row_id, body.data)

    @app.delete(
        "/api/v2/pages/{fid}/data/{collection}/{row_id}",
        **api_v2("Pages", "removePageData", "Remove one of a page's data rows (its writers)", PageDataRemoved),
    )
    def data_rm_route(  # pyright: ignore[reportUnusedFunction]
        request: Request, fid: str, collection: str, row_id: str
    ) -> Any:
        return apps.data_write(request, fid, collection, row_id, remove=True)

    return apps
