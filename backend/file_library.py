"""Library › Files: every file a viewer may see, whoever added it, with full-text search and the owner's hard delete
(docs/files.md, docs/pages.md).

One list over `bot_files`: a bot's files, task and chat attachments, people's uploads, links and imports. Each row is
decided by files.file_visible, the same check as every file read, so a file the viewer may not read never shows its
title, its text or even a count. Lists carry no totals; a list or a search scans a bounded number of rows and hands
back a cursor.

Search is SQLite FTS5 (`file_text`): the current version's text, filled when a version is published from the bytes in
hand, and by a bounded background pass for everything else (`backfill`). Markdown and text as is, HTML as its visible
text, CSV and TSV as the header and the first rows. Ranking follows Docs: title first, then text, with an excerpt.

Hard delete (the owner only) removes every version's bytes from wherever they are stored and keeps one row that says
the file was deleted, by whom and when, without its title or content. Bytes another file still uses stay.
"""

import asyncio
import base64
import binascii
import csv
import io
import json
import logging
import sqlite3
from html.parser import HTMLParser

from fastapi import Request

from . import manual
from . import names
from .store import H, Problem

LOG = logging.getLogger("tico.files")

SCHEMA = [
    # Which version of each file the index holds; `doc` is the file's row id in file_text.
    "CREATE TABLE IF NOT EXISTS file_text_state(doc INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL UNIQUE, "
    "version INTEGER NOT NULL, indexed TEXT NOT NULL)",
    # One row per hard-deleted file: who and when, never what it held.
    "CREATE TABLE IF NOT EXISTS file_deletions(file_id TEXT PRIMARY KEY, actor TEXT NOT NULL, versions INTEGER NOT NULL, "
    "created TEXT NOT NULL)",
    # Bytes a deletion left unused, removed from storage after the write commits and retried until gone.
    "CREATE TABLE IF NOT EXISTS blob_purges(digest TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT '', "
    "queued TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')",
    # Versions are immutable, except that a hard delete (a file_deletions row, written first) removes them.
    "DROP TRIGGER IF EXISTS bot_file_versions_no_delete",
    "CREATE TRIGGER bot_file_versions_no_delete BEFORE DELETE ON bot_file_versions "
    "WHEN NOT EXISTS (SELECT 1 FROM file_deletions WHERE file_id=OLD.file_id) "
    "BEGIN SELECT RAISE(ABORT, 'file versions are immutable'); END",
]
# Created apart so a SQLite built without FTS5 still starts: search then matches titles only.
FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS file_text USING fts5(title, text, "
       "tokenize='porter unicode61')")

LIST_MAX = 100
SEARCH_MAX = 50
LIST_BYTES = 256 * 1024                  # a page of the list stops here, whatever its limit
SCAN_CHUNK = 200
SCAN_MAX = 5000                          # rows one list or search looks at before it answers with a cursor
READ_MAX = 5 * 1024 * 1024               # bytes read to index one version
TEXT_MAX = 200_000                       # characters kept per version
CSV_ROWS = 50
BACKFILL_BATCH = 100
PURGE_ATTEMPTS = 10
# What holds a blob apart from a message or a file (`release_message_files`): a task's attachment, one in the trash, a
# meeting's media, a scanned letter, a piece of market evidence.
BLOB_HOLDERS = (("message_assets", "blob_id"), ("task_assets", "blob_id"), ("task_trash_assets", "blob_id"),
                ("media_assets", "blob_id"), ("mail_items", "blob_id"), ("market_evidence", "blob_id"))
TEXT_TYPES = ("text/markdown", "text/plain", "application/json", "text/yaml")
TABLE_TYPES = {"text/csv": ",", "text/tab-separated-values": "\t"}

# Library kinds: the filter's words, from a file's stored kind (clients/bot_files.py KINDS); a page is its own kind.
KINDS = {"page": "Pages", "document": "Documents", "image": "Images", "data": "Data", "media": "Video and audio",
         "other": "Other"}
STORED = {"document": ("document", "slides", "design"), "image": ("image",), "data": ("spreadsheet", "data"),
          "media": ("video", "audio")}


def ensure(c):
    """Unversioned, like the trash: runs inside initialize's transaction at every start."""
    for statement in SCHEMA:
        c.execute(statement)
    try:
        c.execute(FTS)
    except sqlite3.OperationalError:
        pass


def has_fts(c):
    return c.execute("SELECT 1 FROM sqlite_master WHERE name='file_text'").fetchone() is not None


def own_place(who):
    """The reader's My dashboard (backend/cards.py person places), or '' for anyone who has none."""
    actor = str(who.actor or "")
    return "person:" + actor[6:] if who.role in ("owner", "human") and actor.startswith("human:") else ""


def has_pins(c):
    """Pins are another change's table (docs/pages.md); the filter and the row's places wait for it."""
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pins'").fetchone() is not None


# ---------------------------------------------------------------------------------------------- cursors and scans
def encode_cursor(stamp, ident):
    return base64.urlsafe_b64encode(f"{stamp}|{ident}".encode()).decode()


def decode_cursor(cursor):
    try:
        stamp, _, ident = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise Problem("validation", "That cursor is not valid", 422) from exc
    if not stamp or not ident:
        raise Problem("validation", "That cursor is not valid", 422)
    return stamp, ident


def scan(c, select, where, params, limit, cursor, keep, budget=None):
    """(rows, next cursor): the newest `bot_files` rows (aliased `f`) matching `where` that `keep` accepts, at most
    `limit`, looked at in chunks. After SCAN_MAX rows it answers with what it has and a cursor where it stopped, so a
    list of files the viewer mostly may not see stays bounded and still pages on. `budget(row)` may stop a page early
    (bytes), returning False for the row that would not fit."""
    from .files import prime_visible
    out, last, scanned = [], None, 0
    position = decode_cursor(cursor) if cursor else None
    while True:
        clauses = list(where)
        args = list(params)
        if position:
            clauses.append("(f.last_activity_at<? OR (f.last_activity_at=? AND f.id<?))")
            args += [position[0], position[0], position[1]]
        sql = select + (" WHERE " + " AND ".join(clauses) if clauses else "") + \
            " ORDER BY f.last_activity_at DESC, f.id DESC LIMIT ?"
        batch = c.execute(sql, (*args, SCAN_CHUNK)).fetchall()
        prime_visible(c, batch)
        for r in batch:
            if keep(r):
                if len(out) == limit or (budget and not budget(r)):
                    return out, encode_cursor(out[-1]["last_activity_at"], out[-1]["id"]) if out else \
                        encode_cursor(last["last_activity_at"], last["id"]) if last else None
                out.append(r)
            last = r
        scanned += len(batch)
        if len(batch) < SCAN_CHUNK:
            return out, None
        if scanned >= SCAN_MAX:
            return out, encode_cursor(last["last_activity_at"], last["id"])
        position = (last["last_activity_at"], last["id"])


# ---------------------------------------------------------------------------------------------- text
class _Text(HTMLParser):
    """The text a person sees on a page: no script, style or markup."""
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.depth, self.title, self.in_title = [], 0, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        elif tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.depth:
            self.out.append(data)


def extract(mime, data, full=False):
    """The searchable text of one version's bytes, or "" for a type Tico does not read (PDF and office files later).
    `full`: the whole text the bytes hold (up to READ_MAX bytes), with no cut for the search index (Counsel reads it)."""
    mime = (mime or "").split(";", 1)[0].strip().lower()
    data = data[:READ_MAX]
    if mime in TEXT_TYPES:
        text = data.decode("utf-8", errors="replace")
    elif mime == "text/html":
        parser = _Text()
        try:
            parser.feed(data.decode("utf-8", errors="replace"))
            parser.close()
        except Exception:                    # malformed markup: what was read so far
            pass
        text = " ".join(" ".join(parser.title + parser.out).split())
    elif mime in TABLE_TYPES:
        reader = csv.reader(io.StringIO(data.decode("utf-8", errors="replace")), delimiter=TABLE_TYPES[mime])
        rows = []
        try:
            for i, row in enumerate(reader):
                if i > CSV_ROWS and not full:
                    break
                rows.append(" ".join(cell.strip() for cell in row))
        except csv.Error:
            pass
        text = "\n".join(rows)
    else:
        text = ""
    return text if full else text[:TEXT_MAX]


def index(c, fid, version, title, text):
    """Hold `text` as file `fid`'s searchable text at `version` (its title too)."""
    now = H.now()
    row = c.execute("SELECT doc FROM file_text_state WHERE file_id=?", (fid,)).fetchone()
    if row:
        c.execute("UPDATE file_text_state SET version=?,indexed=? WHERE file_id=?", (version, now, fid))
        doc = row["doc"]
    else:
        doc = c.execute("INSERT INTO file_text_state(file_id,version,indexed) VALUES(?,?,?)", (fid, version, now)).lastrowid
    if has_fts(c):
        c.execute("DELETE FROM file_text WHERE rowid=?", (doc,))
        c.execute("INSERT INTO file_text(rowid,title,text) VALUES(?,?,?)", (doc, title or "", text or ""))


def retitle(c, fid, title):
    """A renamed file is found by its new name at once."""
    row = c.execute("SELECT doc FROM file_text_state WHERE file_id=?", (fid,)).fetchone()
    if row and has_fts(c):
        c.execute("UPDATE file_text SET title=? WHERE rowid=?", (title or "", row["doc"]))


def unindex(c, fid):
    row = c.execute("SELECT doc FROM file_text_state WHERE file_id=?", (fid,)).fetchone()
    if row:
        if has_fts(c):
            c.execute("DELETE FROM file_text WHERE rowid=?", (row["doc"],))
        c.execute("DELETE FROM file_text_state WHERE file_id=?", (fid,))


def adopt_attachments(c, settings, limit=BACKFILL_BATCH):
    """Task attachments people added when they made a task (`task_assets`) become files of the task, as a later
    attachment with the same name makes them (files.attach_task), so the Library lists them. How many."""
    from .files import Files
    from .views import default_bot
    from clients import bot_files as BF
    rows = c.execute("SELECT a.task_id,b.* FROM task_assets a JOIN blobs b ON b.id=a.blob_id "
                     "WHERE NOT EXISTS (SELECT 1 FROM bot_file_versions v WHERE v.blob_id=b.id) "
                     "AND NOT EXISTS (SELECT 1 FROM bot_files f WHERE f.id=b.id) LIMIT ?", (limit,)).fetchall()
    for b in rows:
        task = H.task(c, b["task_id"])
        if not task:
            continue
        bot = next((H.actor_id(a) for a in (b["owner"], task["owner"], task["requester"]) if H.is_bot(a)
                    and H.bot(c, H.actor_id(a))), None) or default_bot(c, settings)
        if not H.bot(c, bot):
            continue
        c.execute("INSERT INTO bot_files(id,bot,scope,identity,title,kind,mime,locator,task_id,current_version,"
                  "first_activity_at,last_activity_at) VALUES(?,?,?,?,?,?,?,'tico_blob',?,1,?,?)",
                  (b["id"], bot, "task:" + b["task_id"], "task-file:" + b["id"], b["name"], BF.kind_of(b["name"]),
                   b["content_type"], b["task_id"], b["created"], b["created"]))
        Files.insert_task_version(c, b["id"], 1, b["id"], b["digest"], b["size"], b["name"], b["content_type"],
                                  b["owner"], b["created"], pending=False)
    return len(rows)


def backfill(store, blobs, limit=BACKFILL_BATCH):
    """One bounded pass: adopt task attachments, then index files whose current version the index does not hold yet.
    Bytes are read with no transaction open. How many files it indexed."""
    with store.transaction() as c:
        adopt_attachments(c, store.settings, limit)
    with store.read() as c:
        todo = c.execute(
            "SELECT f.id,f.title,f.locator,f.url,f.current_version,v.mime,v.size,v.digest FROM bot_files f "
            "LEFT JOIN file_text_state s ON s.file_id=f.id "
            "LEFT JOIN bot_file_versions v ON v.file_id=f.id AND v.version=f.current_version "
            "WHERE f.state<>'deleted' AND (s.file_id IS NULL OR s.version<>f.current_version) "
            "ORDER BY f.last_activity_at DESC LIMIT ?", (limit,)).fetchall()
    found = []
    for r in todo:
        text = ""
        if r["locator"] == "remote_link":
            text = r["url"] or ""
        elif r["digest"] and (r["size"] or 0) <= READ_MAX:
            mime = (r["mime"] or "").split(";", 1)[0].strip().lower()
            if mime in TEXT_TYPES or mime in TABLE_TYPES or mime == "text/html":
                try:
                    text = extract(mime, blobs.get(r["digest"]))
                except Exception:
                    LOG.warning("file %s: its text could not be read for search", r["id"], exc_info=True)
        found.append((r["id"], r["current_version"], r["title"], text))
    if found:
        with store.transaction() as c:
            for fid, version, title, text in found:
                now = c.execute("SELECT current_version,state FROM bot_files WHERE id=?", (fid,)).fetchone()
                if now and now["current_version"] == version and now["state"] != "deleted":
                    index(c, fid, version, title, text)
    return len(found)


# ---------------------------------------------------------------------------------------------- the library
class Library:
    def __init__(self, app, store, auth, files):
        self.app, self.store, self.auth, self.files = app, store, auth, files

    def _team_where(self, c, who, gid):
        """A team's files: its bots' (the group and the groups under it, as team pages count them) and any file on a
        task one of its bots or people owns."""
        from . import cards
        ctx = cards.Ctx(c, self.auth, who, self.store.settings)
        if gid not in ctx.groups:
            raise Problem("not_found", "Team not found", 404)
        actors = sorted(ctx.team_actors(gid))
        bots = [a[4:] for a in actors if a.startswith("bot:")]
        return ("(f.bot IN (SELECT value FROM json_each(?)) OR f.task_id IN "
                "(SELECT id FROM tasks WHERE owner IN (SELECT value FROM json_each(?))))"), [json.dumps(bots),
                                                                                         json.dumps(actors)]

    def _pins(self, c, who, fid, cache):
        """Where a file is pinned, as places the reader may know of; a task place only when they may read the task."""
        if not has_pins(c):
            return []
        out = []
        for r in c.execute("SELECT DISTINCT place FROM pins WHERE item=?", ("page:" + fid,)):
            place = str(r["place"])
            if place.startswith("person:") and place != own_place(who):     # someone's My dashboard is theirs alone
                continue
            if place.startswith("task:") and not self.files.task_info(c, who, place[5:], cache):
                continue
            out.append(place)
        return out

    def row_view(self, c, who, r, caches, known):
        value = self.files.view(c, who, r, caches)
        page = r["page_cadence"] is not None
        value.update(kind_label=KINDS[library_kind(r["kind"], page)], library_kind=library_kind(r["kind"], page),
                     bot_name=known.get("bot:" + r["bot"], r["bot"]), actor_name=known.get(value.get("actor") or "", ""),
                     page={"cadence": r["page_cadence"], "archived": bool(r["page_archived"])} if page else None,
                     archived=bool(r["archived"] or (page and r["page_archived"])),
                     pins=self._pins(c, who, r["id"], caches["task"]))
        for key in ("poster_blob_id", "thumb_blob_id"):
            value.pop(key, None)
        return value

    def listing(self, who, *, kind="", bot="", team="", task="", since="", until="", pinned=False, archived=False,
                limit=50, cursor=""):
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "People and bots list files", 403)
        where = ["f.state<>'deleted'", "(f.locator<>'tico_blob' OR f.current_version>0)"]
        params = []
        where.append("(f.archived=1 OR p.archived=1)" if archived else "f.archived=0 AND COALESCE(p.archived,0)=0")
        if kind:
            if kind not in KINDS:
                raise Problem("validation", "kind is " + ", ".join(KINDS), 422)
            if kind == "page":
                where.append("p.file_id IS NOT NULL")
            elif kind == "other":
                known = [k for group in STORED.values() for k in group]
                where.append("p.file_id IS NULL AND f.kind NOT IN (%s)" % ",".join("?" * len(known)))
                params += known
            else:
                where.append("p.file_id IS NULL AND f.kind IN (%s)" % ",".join("?" * len(STORED[kind])))
                params += list(STORED[kind])
        if bot:
            where.append("f.bot=?")
            params.append(H.actor_id(bot))
        if task:
            where.append("f.task_id=?")
            params.append(task)
        if since:
            where.append("f.last_activity_at>=?")
            params.append(since)
        if until:
            where.append("f.last_activity_at<?")
            params.append(until)
        with self.store.read() as c:
            if team:
                sql, args = self._team_where(c, who, team)
                where.append(sql)
                params += args
            if pinned:
                where.append("EXISTS (SELECT 1 FROM pins WHERE item='page:' || f.id AND "
                             "(place NOT LIKE 'person:%' OR place=?))" if has_pins(c) else "0")
                if has_pins(c):
                    params.append(own_place(who))
            cache, caches = {}, {"task": {}, "repo": {}}
            spent = [0]

            def budget(r):
                # About what the row costs on the wire; the page ends before the one that would pass the cap.
                spent[0] += 900 + len(r["title"] or "") * 2
                return spent[0] <= LIST_BYTES
            rows, nxt = scan(c, "SELECT f.*,p.cadence AS page_cadence,p.archived AS page_archived FROM bot_files f "
                                "LEFT JOIN pages p ON p.file_id=f.id", where, params, limit, cursor,
                             lambda r: self.files.visible(c, who, r, cache), budget)
            known = names.lookup(c, {"bot:" + r["bot"] for r in rows})
            files = [self.row_view(c, who, r, caches, known) for r in rows]
            known.update(names.lookup(c, {f["actor"] for f in files if f.get("actor")}))
            for f in files:
                f["actor_name"] = known.get(f.get("actor") or "", "")
        return {"files": files, "has_more": nxt is not None, "next_cursor": nxt}

    def search(self, who, q, limit=20):
        """Files `who` may read whose title or text matches, best first, each with an excerpt. Archived and deleted
        files are left out, as Docs leaves out archived docs."""
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "People and bots search files", 403)
        tokens = manual.query_words(q)
        if not tokens:
            raise Problem("query", "Search needs at least one word", 422)
        with self.store.read() as c:
            candidates = self._candidates(c, tokens)
            from .files import prime_visible
            cache, out = {}, []
            weights = {t: 1.0 for t in tokens}
            for start in range(0, len(candidates), SCAN_CHUNK):
                chunk = candidates[start:start + SCAN_CHUNK]
                prime_visible(c, chunk)
                for r in chunk:
                    if not self.files.visible(c, who, r, cache):
                        continue
                    text = r["text"] or ""
                    title_words, body = set(manual._words(r["title"] or "")), text.casefold()
                    have = [t for t in tokens if manual._hits(title_words, t) or manual._stem(t) in body]
                    if not have:
                        continue
                    score = sum(weights[t] * (10 + 15 * manual._hits(title_words, t)
                                              + min(body.count(manual._stem(t)), 5)) for t in have)
                    if len(have) == len(tokens):
                        score *= 2
                    out.append((score, r))
                if len(out) >= limit * 4:
                    break
            out.sort(key=lambda pair: (-pair[0], pair[1]["title"].casefold()))
            out = out[:limit]
            known = names.lookup(c, {"bot:" + r["bot"] for _, r in out})
            return {"results": [{"id": r["id"], "title": r["title"], "kind": r["kind"],
                                 "library_kind": library_kind(r["kind"], r["page_cadence"] is not None),
                                 "bot": r["bot"], "bot_name": known.get("bot:" + r["bot"], r["bot"]),
                                 "version": r["current_version"], "updated": r["last_activity_at"],
                                 "url": r["url"] if r["locator"] == "remote_link" else None,
                                 "page": r["page_cadence"] is not None,
                                 "excerpt": excerpt(r["text"] or "", tokens), "score": round(score, 3)}
                                for score, r in out]}

    def _candidates(self, c, tokens):
        """Rows whose title or text match, best match first, at most SCAN_MAX: FTS5 when the build has it, else
        titles alone."""
        select = ("SELECT f.*,p.cadence AS page_cadence,{text} AS text FROM bot_files f "
                  "LEFT JOIN pages p ON p.file_id=f.id {join} WHERE f.state<>'deleted' AND f.archived=0 "
                  "AND COALESCE(p.archived,0)=0 {match} LIMIT ?")
        if has_fts(c):
            from .docs import Docs
            query = select.format(text="file_text.text", join="JOIN file_text_state s ON s.file_id=f.id "
                                  "JOIN file_text ON file_text.rowid=s.doc",
                                  match="AND file_text MATCH ? ORDER BY bm25(file_text, 10.0, 1.0)")
            seen, rows = set(), []
            stemmed = [manual._stem(t) for t in tokens]
            for expression in Docs.expressions(stemmed):
                try:
                    found = c.execute(query, (expression, SCAN_MAX)).fetchall()
                except sqlite3.OperationalError:
                    continue
                rows += [r for r in found if r["id"] not in seen]
                seen |= {r["id"] for r in found}
                if rows:
                    break
            return rows
        like = " AND ".join("f.title LIKE ?" for _ in tokens)
        return c.execute(select.format(text="''", join="", match="AND " + like + " ORDER BY f.last_activity_at DESC"),
                         (*["%" + t + "%" for t in tokens], SCAN_MAX)).fetchall()

    # ------------------------------------------------------------------ hard delete
    def delete(self, request, fid):
        """The owner removes a file for good: every version's bytes, from every place they are stored. The file
        keeps one row that says it was deleted, by whom and when."""
        who = request.state.identity
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner deletes a file for good", 403)

        def work(c):
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
            if not row or row["state"] == "deleted" or not self.files.visible(c, who, row, {}):
                raise Problem("not_found", "File not found", 404)
            return purge_rows(c, who.actor, row)
        result = self.files.write(request, who, {"id": fid, "delete": True}, work)
        drain(self.store, self.files.blobs)
        return result


def library_kind(stored, page=False):
    if page:
        return "page"
    for name, kinds in STORED.items():
        if stored in kinds:
            return name
    return "other"


def excerpt(text, tokens, width=240):
    """A short piece of the text around the first word asked for."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    low = text.casefold()
    at = min((i for i in (low.find(manual._stem(t)) for t in tokens) if i >= 0), default=0)
    begin = max(0, at - width // 3)
    if begin:
        begin = text.find(" ", begin) + 1 or begin
    piece = text[begin:begin + width]
    cut = piece.rfind(" ")
    return ("… " if begin else "") + (piece[:cut] if cut > width // 2 else piece) + " …"


def still_used(c, digest):
    """Whether any stored row still needs these bytes: another blob row (another file, a meeting, a message) or the
    team icon, which keeps a digest of its own."""
    if c.execute("SELECT 1 FROM blobs WHERE digest=? LIMIT 1", (digest,)).fetchone():
        return True
    icon = c.execute("SELECT value_json FROM registry_metadata WHERE key='team_icon'").fetchone()
    if icon:
        try:
            value = json.loads(icon[0])
        except ValueError:
            value = {}
        if digest in (value.get("digest"), value.get("retired_digest")):
            return True
    return False


def purge_rows(c, actor, row):
    """In one write: the deletion record, every version row, the blob rows only this file used and their links, the
    file's search text and page row; the file row becomes a tombstone. Bytes no row needs any more are queued."""
    fid, now = row["id"], H.now()
    versions = c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (fid,)).fetchall()
    c.execute("INSERT INTO file_deletions(file_id,actor,versions,created) VALUES(?,?,?,?)",
              (fid, actor, len(versions), now))
    blob_ids = {b for v in versions for b in (v["blob_id"], v["poster_blob_id"], v["thumb_blob_id"]) if b}
    c.execute("DELETE FROM bot_file_versions WHERE file_id=?", (fid,))
    c.execute("UPDATE bot_files SET title='Deleted file',state='deleted',archived=1,url=NULL,current_version=0,"
              "identity=?,last_activity_at=? WHERE id=?", ("deleted:" + fid, now, fid))
    release_blobs(c, blob_ids, now)
    unindex(c, fid)
    c.execute("UPDATE pages SET replaced_by=NULL WHERE replaced_by=?", (fid,))
    c.execute("DELETE FROM pages WHERE file_id=?", (fid,))
    if has_pins(c):
        c.execute("DELETE FROM pins WHERE item=?", ("page:" + fid,))
    c.execute("INSERT INTO bot_file_activity(file_id,actor,action,task_id,created,detail_json) VALUES(?,?,?,?,?,?)",
              (fid, actor, "deleted", row["task_id"], now, json.dumps({"versions": len(versions)})))
    H.event(c, actor, "file.delete", fid, {"versions": len(versions)})
    return {"deleted": fid, "versions": len(versions)}


def release_blobs(c, blob_ids, now):
    """The blob rows here, with their previews, that no file, meeting or other blob uses, and their links. Bytes no row
    needs any more are queued, and `drain` removes them from storage after the write commits."""
    from . import visuals
    blob_ids = set(blob_ids)
    for bid in list(blob_ids):
        media = c.execute("SELECT poster_blob_id,thumb_blob_id FROM blob_media WHERE blob_id=?", (bid,)).fetchone()
        if media:
            blob_ids |= {b for b in (media["poster_blob_id"], media["thumb_blob_id"]) if b}
    digests = {}
    for bid in sorted(blob_ids):
        shared = c.execute(
            "SELECT 1 FROM bot_file_versions WHERE blob_id=? OR poster_blob_id=? OR thumb_blob_id=? "
            "UNION ALL SELECT 1 FROM media_assets WHERE blob_id=? "
            "UNION ALL SELECT 1 FROM blob_media WHERE blob_id<>? AND (poster_blob_id=? OR thumb_blob_id=?) LIMIT 1",
            (bid, bid, bid, bid, bid, bid, bid)).fetchone()
        blob = c.execute("SELECT digest,size,content_type FROM blobs WHERE id=?", (bid,)).fetchone()
        if shared or not blob:
            continue
        for table in ("task_assets", "message_assets", "blob_media_retries", "blob_media"):
            c.execute(f"DELETE FROM {table} WHERE blob_id=?", (bid,))
        c.execute("DELETE FROM blobs WHERE id=?", (bid,))
        mime = (blob["content_type"] or "").split(";", 1)[0].strip().lower()
        digests[blob["digest"]] = visuals.kind_of(mime, blob["size"]) if mime else ""
    for digest, kind in digests.items():
        if not still_used(c, digest):
            c.execute("INSERT INTO blob_purges(digest,kind,queued) VALUES(?,?,?) ON CONFLICT(digest) DO UPDATE SET "
                      "attempts=0,error=''", (digest, kind if kind in ("page", "svg") else "", now))


def _kept_elsewhere(c, bid):
    """Whether something besides its messages still holds this blob. A file in a conversation made for this one
    attachment (`chat-file:`, one version) is its copy, and goes with it; any other file is someone's own."""
    for table, column in BLOB_HOLDERS:
        if H._has_table(c, table) and c.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (bid,)).fetchone():
            return True
    return c.execute(
        "SELECT 1 FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id WHERE (v.blob_id=? OR v.poster_blob_id=? "
        "OR v.thumb_blob_id=?) AND (f.identity<>? OR (SELECT COUNT(*) FROM bot_file_versions o WHERE o.file_id=f.id)>1) "
        "LIMIT 1", (bid, bid, bid, "chat-file:" + bid)).fetchone() is not None


def release_message_files(c, actor, message_id):
    """A message's files, when it is removed for good: each one nothing else holds goes the way a file's delete takes it
    (`purge_rows`, or `release_blobs` for an attachment with no file row). Returns how many went."""
    blob_ids = [r[0] for r in c.execute("SELECT blob_id FROM message_assets WHERE message_id=?", (message_id,))]
    c.execute("DELETE FROM message_assets WHERE message_id=?", (message_id,))
    gone = 0
    for bid in blob_ids:
        if _kept_elsewhere(c, bid):
            continue
        for row in c.execute("SELECT f.* FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id WHERE v.blob_id=?",
                             (bid,)).fetchall():
            purge_rows(c, actor, row)
        release_blobs(c, {bid}, H.now())
        gone += 1
    return gone


def drain(store, blobs, limit=50):
    """Remove queued bytes from storage, each checked again first: bytes a row needs again stay, and bytes this server
    wrote in the last few minutes (blobs.FRESH_SECONDS: a write whose row is still to come, or a copy landing) wait
    for a later pass. A place that fails is tried again on the next pass, up to PURGE_ATTEMPTS times. How many were
    removed."""
    with store.read() as c:
        queued = c.execute("SELECT * FROM blob_purges WHERE attempts<? ORDER BY queued LIMIT ?",
                           (PURGE_ATTEMPTS, limit)).fetchall()
    removed = 0
    for q in queued:
        error, fresh = "", False
        # Held while checking and removing, so a write of the same bytes either marks them first or comes after
        # (and stores them again).
        with blobs.purging(q["digest"]) as fresh:
            if fresh:
                continue
            with store.read() as c:
                needed = still_used(c, q["digest"])
            if not needed:
                try:
                    blobs.purge(q["digest"], q["kind"] or None)
                except Exception as exc:              # an unreachable place: the next pass tries again
                    error = str(exc)[:500]
                    LOG.warning("A deleted file's bytes could not be removed everywhere yet: %s", error)
        with store.transaction() as c:
            if error:
                c.execute("UPDATE blob_purges SET attempts=attempts+1,error=? WHERE digest=?", (error, q["digest"]))
            else:
                c.execute("DELETE FROM blob_purges WHERE digest=?", (q["digest"],))
                if not needed:
                    c.execute("DELETE FROM blob_locations WHERE digest=?", (q["digest"],))
                    removed += 1
    return removed


def loop(store, blobs, stop, interval=60):
    """Index and purge in bounded passes beside the server, until `stop` is set."""
    if stop.wait(5):
        return
    while True:
        try:
            for _ in range(20):                  # at most 2,000 files a minute
                if stop.is_set() or not backfill(store, blobs):
                    break
            drain(store, blobs)
        except Exception:
            LOG.warning("Library files pass failed; trying again", exc_info=True)
        if stop.wait(interval):
            return


def install_library(app, store, auth, files):
    """GET /api/v2/files and /api/v2/files/search must be registered before media.py's GET /api/v2/files/{bid}."""
    library = Library(app, store, auth, files)
    app.state.library = library

    @app.get("/api/v2/files")
    def listing(request: Request, kind: str = "", bot: str = "", team: str = "", task: str = "", since: str = "",
                until: str = "", pinned: bool = False, archived: bool = False, limit: int = 50, cursor: str = ""):
        """Library › Files: every file the caller may see, newest activity first. Paged and byte-capped; no totals."""
        return library.listing(request.state.identity, kind=kind, bot=bot, team=team, task=task, since=since,
                               until=until, pinned=pinned, archived=archived, limit=max(1, min(limit, LIST_MAX)),
                               cursor=cursor)

    @app.get("/api/v2/files/search")
    def search(request: Request, q: str = "", limit: int = 20):
        """Search the files the caller may read by title and text, best first, each with an excerpt."""
        return library.search(request.state.identity, q[:500], max(1, min(limit, SEARCH_MAX)))

    @app.delete("/api/v2/files/{fid}")
    async def delete(request: Request, fid: str):
        """The owner deletes a file for good: every version's bytes leave storage. Its activity keeps that it was
        deleted, by whom and when."""
        return await asyncio.to_thread(library.delete, request, fid)
    return library

