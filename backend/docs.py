"""Docs: the team's written knowledge (docs/docs.md).

Internal documents are editable and versioned. External links keep their identity and metadata;
public GitHub sources also have refreshed, read-only snapshots (backend/doc_sources.py).
Every signed-in person and bot can read and search the team collection.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
import sqlite3
from email.parser import BytesParser
from email.policy import HTTP
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import Query, Request
from pydantic import Field, StringConstraints

from . import docs_import
from . import manual
from . import models as M
from .names import lookup
from .store import H, Problem, encode

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
 id TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
 locked INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
 created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_by_id TEXT NOT NULL, updated_at TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS docs_path_live ON docs(path COLLATE NOCASE) WHERE archived=0;
CREATE INDEX IF NOT EXISTS docs_updated ON docs(updated_at);
CREATE TABLE IF NOT EXISTS doc_versions(
 doc_id TEXT NOT NULL REFERENCES docs(id), version INTEGER NOT NULL, title TEXT NOT NULL, path TEXT NOT NULL,
 body TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(doc_id, version));
CREATE TABLE IF NOT EXISTS linked_docs(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '', added_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS linked_docs_live ON linked_docs(archived, created_at);
"""
# Full-text index over title, path and body. It is created apart so a SQLite built without FTS5
# still starts: search then falls back to LIKE.
FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5("
       "title, path, body, tokenize='porter unicode61')")

KINDS = ("website", "google_drive", "google_doc", "notion", "github", "other")
MAX_BODY = 1_000_000
LIST_MAX = 500
MIGRATED = "docs_migrated"
STOP = frozenset("a an and are as at be but by do does for from how i in is it my of on or our the to us was we what "
                 "when where which who why will with you your can should".split())


# ------------------------------------------------------------------ paths, addresses, kinds
def slugify(text, fallback="untitled"):
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-")[:80].strip("-") or fallback


def clean_path(raw):
    """A folder path ending in .md: `sales/pricing.md`. Never absolute, never with `..`."""
    path = re.sub(r"/{2,}", "/", str(raw or "").strip().replace("\\", "/")).strip("/")
    parts = [part.strip() for part in path.split("/")]
    if (not path or len(path) > 300 or any(part in ("", ".", "..") for part in parts)
            or any(ord(ch) < 32 for ch in path)):
        raise Problem("validation", "Use a path like sales/pricing.md (folders separated by /, no .. or leading /)", 422)
    if not re.search(r"\.(md|markdown)$", parts[-1], re.I):
        parts[-1] += ".md"
    return "/".join(parts)


def clean_url(raw):
    url = str(raw or "").strip()
    parts = urlsplit(url if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) else "https://" + url)
    try:
        host = parts.hostname or ""
    except ValueError:
        host = ""
    if (parts.scheme not in ("http", "https") or not host or "." not in host and host != "localhost"
            or parts.username or parts.password or len(url) > 2000 or re.search(r"\s", url)):
        raise Problem("validation", "Use a web address that starts with https:// and has no password in it", 422)
    normalized = parts.geturl()
    from clients.github_scope import parse_url
    try:
        scope = parse_url(normalized)
    except ValueError:
        scope = None
    return scope['url'] if scope else normalized


OTHER_HOSTS = ("dropbox.com", "box.com", "sharepoint.com", "onedrive.live.com", "1drv.ms", "atlassian.net",
               "confluence.com", "figma.com", "airtable.com", "coda.io", "slab.com", "quip.com", "gitbook.io",
               "readme.io", "slite.com", "clickup.com", "monday.com", "asana.com", "lucid.app", "miro.com",
               "loom.com", "guru.com", "getguru.com", "helpjuice.com", "zendesk.com", "intercom.com")


def host_of(url):
    host = (urlsplit(url).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def detect_kind(url):
    """website | google_drive | google_doc | notion | github | other, from the address alone."""
    parts = urlsplit(url)
    host, path = host_of(url), parts.path.casefold()
    if host == "docs.google.com":
        return "google_doc" if path.startswith("/document") else "google_drive"
    if host in ("drive.google.com", "sites.google.com") or host.endswith(".drive.google.com"):
        return "google_drive" if host != "sites.google.com" else "website"
    if host in ("notion.so", "notion.site") or host.endswith((".notion.so", ".notion.site")):
        return "notion"
    if host in ("github.com", "raw.githubusercontent.com", "gist.github.com"):
        return "github"
    if any(host == h or host.endswith("." + h) for h in OTHER_HOSTS):
        return "other"
    return "website"


def default_title(url):
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    return (host_of(url) + path)[:300]


def linked_view(row, names=None):
    names = names or {}
    return {"id": row["id"], "title": row["title"], "url": row["url"], "kind": row["kind"],
            "host": host_of(row["url"]), "description": row["description"], "added_by": row["added_by"],
            "added_by_name": name_of(row["added_by"], names), "created_at": row["created_at"], "updated_at": row["updated_at"]}


def name_of(actor, names):
    if actor == H.KEEPER:
        return "Tico"
    return names.get(actor) or (actor.split(":", 1)[-1] if actor else "")


def label_names(c, actors):
    return lookup(c, {a for a in actors if a})


# ------------------------------------------------------------------ the store
def has_fts(c):
    return c.execute("SELECT 1 FROM sqlite_master WHERE name='docs_fts'").fetchone() is not None


def index(c, rowid, title, path, body):
    if has_fts(c):
        c.execute("DELETE FROM docs_fts WHERE rowid=?", (rowid,))
        c.execute("INSERT INTO docs_fts(rowid,title,path,body) VALUES(?,?,?,?)", (rowid, title, path, body))


def unindex(c, rowid):
    if has_fts(c):
        c.execute("DELETE FROM docs_fts WHERE rowid=?", (rowid,))


def ensure_schema(c, settings=None):
    """Tables, the full-text index (rebuilt when it is behind the rows) and the one-time move of
    the old linked sources and approved repositories into linked docs. Safe to run at every start."""
    c.executescript(SCHEMA)
    try:
        c.execute(FTS)
    except sqlite3.OperationalError:
        pass
    from .flight import begin
    done = begin(c)
    try:
        if has_fts(c):
            live = c.execute("SELECT COUNT(*) FROM docs WHERE archived=0").fetchone()[0]
            if live != c.execute("SELECT COUNT(*) FROM docs_fts").fetchone()[0]:
                c.execute("DELETE FROM docs_fts")
                c.execute("INSERT INTO docs_fts(rowid,title,path,body) SELECT rowid,title,path,body FROM docs WHERE archived=0")
        migrate(c, settings)
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        done()


def source_url(source):
    """The address a linked repository source stands for: its folder on the branch when it has one."""
    repo = str(source.get("repo") or "").strip()
    ssh = re.fullmatch(r"git@([A-Za-z0-9.-]+):([\w./-]+)", repo)
    if ssh:
        repo = "https://" + ssh.group(1) + "/" + ssh.group(2)
    elif re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        repo = "https://github.com/" + repo
    repo = repo.rstrip("/").removesuffix(".git")
    folder = str(source.get("folder") or "").strip("/")
    if host_of(repo) == "github.com" and folder and folder != ".":
        return repo + "/tree/" + (source.get("branch") or "HEAD") + "/" + quote(folder)
    return repo


def migrate(c, settings=None):
    """Once: each linked documentation source and each approved documentation repository of the old
    mirror becomes a linked doc. The old tables are left as they are."""
    if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (MIGRATED,)).fetchone():
        return 0
    found = []
    row = c.execute("SELECT value FROM registry_metadata WHERE key='document_sources'").fetchone()
    try:
        sources = json.loads(row[0]) if row else []
    except ValueError:
        sources = []
    for source in sources if isinstance(sources, list) else []:
        if isinstance(source, dict) and source.get("repo"):
            folder = str(source.get("folder") or "").strip("/")
            name = re.sub(r"^https?://[^/]+/", "", source_url({"repo": source["repo"]})) or source["repo"]
            found.append((source_url(source), name + (" / " + folder if folder not in ("", ".") else ""),
                          "Linked from the old documentation sources"))
    if settings is not None:
        try:
            approved = json.loads((settings.registry_dir / "company-docs.json").read_text()).get("proposal_repos")
        except (OSError, ValueError, AttributeError):
            approved = None
        for repo in sorted(approved) if isinstance(approved, dict) else []:
            found.append(("https://github.com/" + str(repo), str(repo), "An approved documentation repository"))
    added = 0
    for url, title, description in found:
        try:
            url = clean_url(url)
        except Problem:
            continue
        if c.execute("SELECT 1 FROM linked_docs WHERE archived=0 AND url=?", (url,)).fetchone():
            continue
        now = H.now()
        c.execute("INSERT INTO linked_docs(id,title,url,kind,description,added_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  ("link-" + secrets.token_hex(6), title[:300], url, detect_kind(url), description, H.KEEPER, now, now))
        added += 1
    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (MIGRATED, encode({"at": H.now(), "linked": added})))
    return added


def refresh_generated_wording(c):
    """Repair generated how-to prose from installs that already completed earlier repairs."""
    marker = "librarian_wording35"
    if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (marker,)).fetchone():
        return
    docs = Docs(None, None, None, None)
    rows = c.execute("SELECT rowid AS rid,* FROM docs WHERE archived=0 "
                     "AND (path GLOB '_librarian/*' OR path='FAQ.md') "
                     "AND (updated_by_id='bot:librarian' OR (updated_by_id='keeper' AND (created_by_id='bot:librarian' OR id IN "
                     "(SELECT doc_id FROM doc_versions WHERE actor='bot:librarian' "
                     "OR note='Updated generated Tico wording'))))").fetchall()
    for row in rows:
        text = H.librarian_text(row["body"])
        if text == row["body"]:
            continue
        c.execute("UPDATE docs SET body=?,version=version+1,updated_by_id='keeper',updated_at=? WHERE id=?",
                  (text, H.now(), row["id"]))
        fresh = docs.get_row(c, row["id"])
        docs.snapshot(c, fresh, H.KEEPER, "Updated generated Tico wording")
        index(c, row["rid"], row["title"], row["path"], text)
        H.event(c, H.KEEPER, "docs.updated", row["id"], {"path": row["path"], "version": fresh["version"]})
    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (marker, encode({"at": H.now()})))


def redo_generated_wording(c):
    """Redo the 0.2.35 wording repair from the text before it: that repair also changed every "company" and
    "machine", so a washing machine became a washing Computer."""
    marker = "librarian_wording36"
    if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (marker,)).fetchone():
        return
    docs = Docs(None, None, None, None)
    rows = c.execute("SELECT d.rowid AS rid,d.*,p.body AS before FROM docs d "
                     "JOIN doc_versions v ON v.doc_id=d.id AND v.version=d.version AND v.note='Updated generated Tico wording' "
                     "JOIN doc_versions p ON p.doc_id=d.id AND p.version=d.version-1 WHERE d.archived=0").fetchall()
    for row in rows:
        text = H.librarian_text(row["before"])
        if text == row["body"]:
            continue
        c.execute("UPDATE docs SET body=?,version=version+1,updated_by_id='keeper',updated_at=? WHERE id=?",
                  (text, H.now(), row["id"]))
        fresh = docs.get_row(c, row["id"])
        docs.snapshot(c, fresh, H.KEEPER, "Redid generated Tico wording")
        index(c, row["rid"], row["title"], row["path"], text)
        H.event(c, H.KEEPER, "docs.updated", row["id"], {"path": row["path"], "version": fresh["version"]})
    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (marker, encode({"at": H.now()})))


def refresh_generated_docs(c):
    """Repair older Librarian caches once, then queue source reconciliation on its next run."""
    redo_generated_wording(c)
    refresh_generated_wording(c)
    marker = "librarian_fix33"
    if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (marker,)).fetchone():
        return
    titles = {
        "_librarian/missing.md": {"Missing and outdated company information": "Docs gaps"},
        "_librarian/faq-log.md": {"Questions and answers": "Question log"},
        "_librarian/glossary.md": {"Company glossary": "Team glossary", "Glossary": "Team glossary"},
        "_librarian/index.md": {"Document index": "Docs index"},
    }
    docs = Docs(None, None, None, None)
    for row in c.execute("SELECT rowid AS rid,* FROM docs WHERE archived=0 AND "
                         "(path GLOB '_librarian/*' OR path='FAQ.md')").fetchall():
        title = titles.get(row["path"], {}).get(row["title"], row["title"])
        text = H.librarian_text(row["body"]) if row["updated_by_id"] == "bot:librarian" else row["body"]
        if (title, text) == (row["title"], row["body"]):
            continue
        c.execute("UPDATE docs SET title=?,body=?,version=version+1,updated_by_id='keeper',updated_at=? WHERE id=?",
                  (title, text, H.now(), row["id"]))
        fresh = docs.get_row(c, row["id"])
        docs.snapshot(c, fresh, H.KEEPER, "Updated generated Tico wording")
        index(c, row["rid"], title, row["path"], text)
        H.event(c, H.KEEPER, "docs.updated", row["id"], {"path": row["path"], "version": fresh["version"]})
    for source in c.execute("SELECT * FROM docs WHERE archived=1").fetchall():
        docs.remove_index_source(c, source, H.KEEPER)
    bot = H.bot(c, "librarian")
    if bot and bot["state"] in ("active", "planned"):
        pending = c.execute("SELECT 1 FROM tasks WHERE requester_id=? AND owner_id='bot:librarian' "
                            "AND title='Refresh the map' AND status IN ('open','doing','ready','waiting','review')",
                            (H.KEEPER,)).fetchone()
        if not pending:
            H.task_create(c, H.KEEPER, "Refresh the map",
                          "Follow playbooks/refresh-the-map.md. Reconcile every cached source against the full live "
                          "doc and link lists, including archived sources and recorded unreadable links. Update "
                          "Sources I could not read from existing map failures even when no fetch is due. "
                          "Use docs/glossary.md for generated wording and preserve source facts.",
                          "bot:librarian", allow_planned=True, next_run=True, lint=False, deduplicate=False)
    else:
        return  # Keep an inactive Librarian off; queue the refresh after it is enabled.
    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (marker, encode({"at": H.now()})))


# ------------------------------------------------------------------ request bodies
# A doc's body is kept exactly as sent: models.Contract trims every other text field, which would drop a
# final newline or the indentation of a code block on line 1. Titles, paths and notes still trim.
Body = Annotated[str, StringConstraints(strip_whitespace=False)]


class DocCreate(M.Contract):
    path: str | None = Field(default=None, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    body: Body = Field(default="", max_length=MAX_BODY)
    note: str = Field(default="", max_length=300)


class DocEdit(M.Contract):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: Body | None = Field(default=None, max_length=MAX_BODY)
    path: str | None = Field(default=None, max_length=300)
    locked: bool | None = None
    archived: bool | None = None
    note: str = Field(default="", max_length=300)


class DocRestore(M.Contract):
    version: int = Field(ge=1)


class DocImport(M.Contract):
    filename: str = Field(max_length=300)
    sha256: str = Field(max_length=64)
    path: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=300)


class LinkCreate(M.Contract):
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=300)


class LinkEdit(M.Contract):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    archived: bool | None = None


def link_row(c, link_id):
    row = c.execute("SELECT * FROM linked_docs WHERE id=?", (link_id,)).fetchone()
    if not row:
        raise Problem("not_found", "Linked doc not found", 404)
    return row


def add_link(c, actor, body):
    """One new linked doc (also what the Getting started card adds). A repeat address is a 409."""
    url = clean_url(body.url)
    dupe = c.execute("SELECT l.* FROM linked_docs l LEFT JOIN doc_sources s ON s.source_id=l.id "
                     "WHERE l.archived=0 AND (l.url=? OR s.url=?)", (url, url)).fetchone()
    if dupe:
        raise Problem("already_linked", "That address is already linked", 409, extra={"linked": linked_view(dupe)})
    if c.execute("SELECT COUNT(*) FROM linked_docs WHERE archived=0").fetchone()[0] >= 500:
        raise Problem("limit", "At most 500 linked docs", 422)
    now, link_id = H.now(), "link-" + secrets.token_hex(6)
    c.execute("INSERT INTO linked_docs(id,title,url,kind,description,added_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
              (link_id, body.title or default_title(url), url, detect_kind(url), body.description, actor, now, now))
    H.event(c, actor, "docs.linked", link_id, {"url": url})
    from .doc_sources import sync_link
    sync_link(c, link_id)
    return linked_view(link_row(c, link_id), label_names(c, [actor]))


# ------------------------------------------------------------------ the service
def merge_ranked(groups, limit, tokens):
    # Database and Python relevance scores have different scales; positions are comparable.
    def weight(row):
        if row.get('type') == 'linked':
            return 0.005
        if row.get('type') != 'internal' or not row.get('path', '').startswith('_librarian/'):
            return 1
        title = [t for t in manual.query_words(row['title']) if t not in ('team', 'docs', 'doc')]
        named = bool(title) and all(t in tokens for t in title)
        named = named or all(t in tokens for t in manual._words(row['path']))
        return 1 if named else 0.2
    out = [{**row, "score": round(weight(row) * 100 / (60 + rank), 6)}
           for group in groups for rank, row in enumerate(group, 1)]
    return sorted(out, key=lambda row: -row["score"])[:limit]


class Docs:
    def __init__(self, app, store, auth, mutate):
        self.app, self.store, self.auth, self.mutate = app, store, auth, mutate

    # -------------------------------------------------------------- who
    def reader(self, who):
        self.auth.domain(who)
        if who.role not in ("owner", "human", "bot"):
            raise Problem("forbidden", "Docs are for people and bots", 403)

    def admin(self, who):
        return who.role in ("owner", "human") and self.auth.bot_admin(who)

    # -------------------------------------------------------------- views
    def doc_view(self, c, row, body=True, names=None):
        names = names if names is not None else label_names(c, [row["created_by"], row["updated_by"]])
        out = {"id": row["id"], "path": row["path"], "title": row["title"],
               "updated_at": row["updated_at"], "updated_by": row["updated_by"],
               "updated_by_name": name_of(row["updated_by"], names),
               "locked": bool(row["locked"]), "version": row["version"]}
        if body:
            out.update(body=row["body"], created_by=row["created_by"], created_at=row["created_at"],
                       created_by_name=name_of(row["created_by"], names), archived=bool(row["archived"]))
        return out

    def get_row(self, c, ref):
        row = c.execute("SELECT * FROM docs WHERE id=?", (ref,)).fetchone()
        if not row:
            raise Problem("not_found", "Doc not found", 404)
        return row

    # -------------------------------------------------------------- internal docs
    def listing(self, who, prefix, limit, cursor, archived=False):
        self.reader(who)
        marks, args = ["archived=?"], [int(archived)]
        if prefix:
            marks.append("substr(path,1,?) = ? COLLATE NOCASE")
            args += [len(prefix), prefix]
        if cursor:
            try:
                after = base64.urlsafe_b64decode(cursor.encode()).decode()
            except (ValueError, binascii.Error, UnicodeError) as exc:
                raise Problem("validation", "That cursor is not valid", 422) from exc
            marks.append("path COLLATE NOCASE > ?")
            args.append(after)
        with self.store.read() as c:
            rows = c.execute("SELECT * FROM docs WHERE " + " AND ".join(marks)
                             + " ORDER BY path COLLATE NOCASE LIMIT ?", (*args, limit + 1)).fetchall()
            page = rows[:limit]
            names = label_names(c, [r["updated_by_id"] for r in page])
            more = len(rows) > limit
            return {"docs": [self.doc_view(c, r, body=False, names=names) for r in page],
                    "next_cursor": base64.urlsafe_b64encode(page[-1]["path"].casefold().encode()).decode()
                    if more and page else None}

    def read(self, who, doc_id):
        self.reader(who)
        with self.store.read() as c:
            return {"doc": self.doc_view(c, self.get_row(c, doc_id))}

    def free_path(self, c, want, own=None, unique=False):
        """`want` itself, or (for a path nobody chose) the next free `name-2.md`."""
        stem, dot, ext = want.rpartition(".")
        path, n = want, 2
        while True:
            taken = c.execute("SELECT id FROM docs WHERE archived=0 AND path=? COLLATE NOCASE", (path,)).fetchone()
            if not taken or taken["id"] == own:
                return path
            if not unique:
                raise Problem("path_taken", "Another doc already has the path " + path, 409,
                              extra={"doc_id": taken["id"]})
            path, n = f"{stem}-{n}.{ext}", n + 1

    def snapshot(self, c, row, actor, note):
        c.execute("INSERT INTO doc_versions(doc_id,version,title,path,body,actor,created_at,note) VALUES(?,?,?,?,?,?,?,?)",
                  (row["id"], row["version"], row["title"], row["path"], row["body"], actor, row["updated_at"], note))

    def insert(self, c, actor, title, body, path=None, note="", event="docs.created", detail=None):
        """One new doc at version 1: the row, its first version, its index entry and the audit event."""
        path = self.free_path(c, clean_path(path)) if path else self.free_path(c, slugify(title) + ".md", unique=True)
        if actor == "bot:librarian":
            body = H.librarian_text(body)
        now, doc_id = H.now(), "doc-" + secrets.token_hex(6)
        c.execute("INSERT INTO docs(id,path,title,body,version,created_by_id,created_at,updated_by_id,updated_at) "
                  "VALUES(?,?,?,?,1,?,?,?,?)", (doc_id, path, title, body, actor, now, actor, now))
        row = self.get_row(c, doc_id)
        self.snapshot(c, row, actor, note or "Created")
        index(c, c.execute("SELECT rowid FROM docs WHERE id=?", (doc_id,)).fetchone()[0], title, path, body)
        H.event(c, actor, event, doc_id, {"path": path, "title": title, **(detail or {})})
        return row

    def create(self, request, body, imported=None):
        who = request.state.identity
        self.reader(who)

        def work(c):
            row = self.insert(c, who.actor, body.title, body.body, body.path,
                              note=body.note or ("Imported from " + imported if imported else ""),
                              event="docs.imported" if imported else "docs.created",
                              detail={"file": imported} if imported else None)
            return {"doc": self.doc_view(c, row)}
        return work

    def edit(self, request, doc_id, body):
        who = request.state.identity
        self.reader(who)

        def work(c):
            row = self.get_row(c, doc_id)
            locking = body.locked is not None and bool(body.locked) != bool(row["locked"])
            if locking and not self.admin(who):
                raise Problem("forbidden", "Only an owner or bot administrator can lock or unlock a doc", 403)
            if row["locked"] and not self.admin(who):
                raise Problem("locked", "This doc is locked: only an owner or bot administrator can change it", 403)
            if body.version != row["version"]:
                names = label_names(c, [row["updated_by_id"]])
                raise Problem("version_conflict", "Someone changed this doc after you opened it. Reload to see their "
                              "version, then apply your change to it.", 409,
                              extra={"version": row["version"], "updated_at": row["updated_at"], "updated_by_id": row["updated_by_id"],
                                     "updated_by_name": name_of(row["updated_by_id"], names)})
            title = body.title if body.title is not None else row["title"]
            text = body.body if body.body is not None else row["body"]
            if who.actor == "bot:librarian" and body.body is not None:
                text = H.librarian_text(text)
            archived = int(body.archived) if body.archived is not None else row["archived"]
            path = clean_path(body.path) if body.path is not None else row["path"]
            if not archived and (path.casefold() != row["path"].casefold() or row["archived"]):
                path = self.free_path(c, path, own=row["id"])
            changed = (title, text, path, archived) != (row["title"], row["body"], row["path"], row["archived"])
            now = H.now()
            if locking:
                c.execute("UPDATE docs SET locked=? WHERE id=?", (int(body.locked), row["id"]))
                H.event(c, who.actor, "docs.locked" if body.locked else "docs.unlocked", row["id"], {"path": row["path"]})
            if changed:
                c.execute("UPDATE docs SET title=?,body=?,path=?,archived=?,version=version+1,updated_by_id=?,updated_at=? WHERE id=?",
                          (title, text, path, archived, who.actor, now, row["id"]))
                fresh = self.get_row(c, row["id"])
                note = body.note or ("Archived" if archived and not row["archived"] else "Unarchived" if row["archived"] and not archived else "")
                self.snapshot(c, fresh, who.actor, note)
                rowid = c.execute("SELECT rowid FROM docs WHERE id=?", (row["id"],)).fetchone()[0]
                unindex(c, rowid) if archived else index(c, rowid, title, path, text)
                H.event(c, who.actor, "docs.archived" if archived and not row["archived"] else "docs.updated", row["id"],
                        {"path": path, "version": fresh["version"]})
                if archived and not row["archived"]:
                    self.remove_index_source(c, row, who.actor)
            return {"doc": self.doc_view(c, self.get_row(c, row["id"]))}
        return work

    def remove_index_source(self, c, source, actor):
        """An archived source leaves the cached index in the same transaction."""
        row = c.execute("SELECT rowid AS rid,* FROM docs WHERE path='_librarian/index.md' AND archived=0").fetchone()
        if not row or row["id"] == source["id"]:
            return
        reused = c.execute("SELECT 1 FROM docs WHERE path=? COLLATE NOCASE AND archived=0 AND id!=?",
                           (source["path"], source["id"])).fetchone()
        patterns = [r"\]\(doc:" + re.escape(source["id"]) + r"\)"]
        if not reused:
            patterns.append(r"(?<![\w/.-])" + re.escape(source["path"]) + r"(?![\w/.-])")
        refs = re.compile("|".join(patterns))
        text = "".join(line for line in row["body"].splitlines(keepends=True) if not refs.search(line))
        if text == row["body"]:
            return
        c.execute("UPDATE docs SET body=?,version=version+1,updated_by_id=?,updated_at=? WHERE id=?",
                  (text, actor, H.now(), row["id"]))
        fresh = self.get_row(c, row["id"])
        self.snapshot(c, fresh, actor, "Removed archived source " + source["path"])
        index(c, row["rid"], fresh["title"], fresh["path"], text)
        H.event(c, actor, "docs.updated", row["id"], {"path": row["path"], "version": fresh["version"]})

    def versions(self, who, doc_id):
        self.reader(who)
        with self.store.read() as c:
            row = self.get_row(c, doc_id)
            found = c.execute("SELECT version,title,path,actor,created_at,note,length(body) AS size FROM doc_versions "
                              "WHERE doc_id=? ORDER BY version DESC", (doc_id,)).fetchall()
            names = label_names(c, [v["actor"] for v in found])
            return {"doc": doc_id, "versions": [
                {"version": v["version"], "current": v["version"] == row["version"], "title": v["title"], "path": v["path"],
                 "actor": v["actor"], "actor_name": name_of(v["actor"], names), "created_at": v["created_at"],
                 "note": v["note"], "size": v["size"]} for v in found]}

    def version(self, who, doc_id, number):
        self.reader(who)
        with self.store.read() as c:
            self.get_row(c, doc_id)
            v = c.execute("SELECT * FROM doc_versions WHERE doc_id=? AND version=?", (doc_id, number)).fetchone()
            if not v:
                raise Problem("not_found", "Version not found", 404)
            names = label_names(c, [v["actor"]])
            return {"version": {"version": v["version"], "title": v["title"], "path": v["path"], "body": v["body"],
                                "actor": v["actor"], "actor_name": name_of(v["actor"], names),
                                "created_at": v["created_at"], "note": v["note"]}}

    def restore(self, request, doc_id, body):
        who = request.state.identity
        self.reader(who)

        def work(c):
            row = self.get_row(c, doc_id)
            if row["locked"] and not self.admin(who):
                raise Problem("locked", "This doc is locked: only an owner or bot administrator can change it", 403)
            old = c.execute("SELECT * FROM doc_versions WHERE doc_id=? AND version=?", (doc_id, body.version)).fetchone()
            if not old:
                raise Problem("not_found", "Version not found", 404)
            path = old["path"]
            if c.execute("SELECT 1 FROM docs WHERE archived=0 AND id!=? AND path=? COLLATE NOCASE", (doc_id, path)).fetchone():
                path = row["path"]
            if row["archived"]:                 # restoring an archived doc: its path may have been taken since
                path = self.free_path(c, path, own=doc_id, unique=True)
            if (old["title"], old["body"], path, 0) == (row["title"], row["body"], row["path"], row["archived"]):
                return {"doc": self.doc_view(c, row)}
            c.execute("UPDATE docs SET title=?,body=?,path=?,archived=0,version=version+1,updated_by_id=?,updated_at=? WHERE id=?",
                      (old["title"], old["body"], path, who.actor, H.now(), doc_id))
            fresh = self.get_row(c, doc_id)
            self.snapshot(c, fresh, who.actor, f"Restored version {body.version}")
            index(c, c.execute("SELECT rowid FROM docs WHERE id=?", (doc_id,)).fetchone()[0], fresh["title"], path, fresh["body"])
            H.event(c, who.actor, "docs.restored", doc_id, {"from": body.version, "version": fresh["version"]})
            return {"doc": self.doc_view(c, fresh)}
        return work

    # -------------------------------------------------------------- linked docs
    def links(self, who):
        self.reader(who)
        with self.store.read() as c:
            rows = c.execute("SELECT * FROM linked_docs WHERE archived=0 ORDER BY created_at, id").fetchall()
            names = label_names(c, [r["added_by"] for r in rows])
            return {"linked": [linked_view(r, names) for r in rows]}

    def link_add(self, request, body):
        who = request.state.identity
        self.reader(who)
        return lambda c: {"linked": add_link(c, who.actor, body)}

    def link_edit(self, request, link_id, body):
        who = request.state.identity
        self.reader(who)

        def work(c):
            row = link_row(c, link_id)
            if row["added_by"] != who.actor and not self.admin(who):
                raise Problem("forbidden", "Only who added a linked doc, an owner or a bot administrator can change it", 403)
            url = clean_url(body.url) if body.url is not None else row["url"]
            if url != row["url"] and c.execute("SELECT 1 FROM linked_docs l LEFT JOIN doc_sources s ON s.source_id=l.id "
                                               "WHERE l.archived=0 AND (l.url=? OR s.url=?) AND l.id!=?",
                                               (url, url, link_id)).fetchone():
                raise Problem("already_linked", "That address is already linked", 409)
            title = body.title if body.title is not None else row["title"]
            description = body.description if body.description is not None else row["description"]
            archived = int(body.archived) if body.archived is not None else row["archived"]
            c.execute("UPDATE linked_docs SET title=?,description=?,url=?,kind=?,archived=?,updated_at=? WHERE id=?",
                      (title, description, url, detect_kind(url), archived, H.now(), link_id))
            H.event(c, who.actor, "docs.link_removed" if archived and not row["archived"] else "docs.link_updated",
                    link_id, {"url": url})
            from .doc_sources import sync_link
            sync_link(c, link_id)
            names = label_names(c, [row["added_by"]])
            return {"linked": linked_view(link_row(c, link_id), names)}
        return work

    # -------------------------------------------------------------- search
    def search(self, who, q, limit, source_id=""):
        self.reader(who)
        with self.store.read() as c:
            return {"results": self.find(c, q, limit, source_id)}

    def find(self, c, q, limit=20, source_id=""):
        """Internal and external sections, followed by linked metadata, each with its `type`."""
        tokens = manual.query_words(q)
        if not tokens:
            raise Problem("query", "Search needs at least one word", 422)
        external = self.app.state.doc_sources.find(c, tokens, limit, source_id) if self.app else []
        if source_id:
            return external
        internal, linked = self.find_internal(c, tokens, limit), self.find_linked(c, tokens)
        if external:
            contents = merge_ranked([internal, external], limit, tokens)
            metadata = [{**row, 'score': 0.5 / (60 + i)} for i, row in
                        enumerate(sorted(linked, key=lambda r: -r['score']), 1)]
            return (contents + metadata)[:limit]
        results = internal + linked
        results.sort(key=lambda r: (-r["score"], r["type"] != "internal", r["title"].casefold()))
        return results[:limit]

    def find_internal(self, c, tokens, limit):
        out = []
        weights = manual.query_weights(tokens)
        # All candidate titles use one glossary snapshot, rather than restatting every manual file per row.
        glossary = manual.pages().get('glossary', {}).get('body', '')
        sql, params = "SELECT id,path,title,body FROM docs WHERE archived=0", ()
        if has_fts(c):
            expression = " OR ".join('"' + manual._stem(token) + '"*' for token in tokens)
            sql += " AND rowid IN (SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?)"
            params = (expression,)
        for row in c.execute(sql, params):
            page = manual.page_text(row["path"], row["body"], row["title"])
            best = manual.best_section(page, tokens, weights)
            if not best:
                continue
            score, section = best
            title_tokens = [t for t in manual.query_words(row["title"], glossary) if t not in ("team", "docs", "doc")]
            named = bool(title_tokens) and all(t in tokens for t in title_tokens)
            named = named or all(t in tokens for t in manual._words(row["path"]))
            if named:
                score += 100 * sum(weights[t] for t in tokens if manual._hits(page["title_words"], t))
            elif row["path"].startswith("_librarian/"):
                score *= 0.2
            out.append({"type": "internal", "id": row["id"], "path": row["path"], "title": row["title"],
                        "section": section["heading"], "anchor": section["anchor"],
                        "excerpt": manual._excerpt(section["body"], tokens), "score": round(score, 3)})
        return sorted(out, key=lambda r: -r["score"])[:limit]

    @staticmethod
    def expressions(tokens):
        """Every word with the last one a prefix, then (when that finds nothing) any of the
        meaningful words, so a question in a sentence still reaches the docs that answer it."""
        quoted = ['"' + t + '"' for t in tokens]
        yield " AND ".join(quoted[:-1] + [quoted[-1] + "*"])
        wanted = [t for t in tokens if t not in STOP] or tokens
        if len(wanted) > 1:
            yield " OR ".join('"' + t + '"' for t in wanted)

    def find_linked(self, c, tokens):
        out = []
        weights = manual.query_weights(tokens)
        for r in c.execute("SELECT * FROM linked_docs WHERE archived=0"):
            fields = [set(manual._words(r[key])) for key in ("title", "description", "url")]
            hits = [(t, [manual._hits(field, t) for field in fields]) for t in tokens]
            score = sum(weights[t] * (2.0 * h[0] + h[1] + 0.5 * h[2]) for t, h in hits)
            if not score:
                continue
            out.append({"type": "linked", "id": r["id"], "title": r["title"], "url": r["url"], "kind": r["kind"],
                        "description": r["description"], "score": score})
        return out

    # -------------------------------------------------------------- import
    @staticmethod
    def upload(content_type, raw):
        """(filename, bytes, fields) from a multipart body with one `file` part."""
        if not content_type.lower().startswith("multipart/form-data"):
            raise Problem("validation", "Send the file as multipart/form-data in a `file` field", 422)
        message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw)
        if not message.is_multipart() or message.defects:
            raise Problem("validation", "That upload is malformed", 422)
        fields, upload = {}, None
        for index_, part in enumerate(message.iter_parts()):
            if index_ >= 10 or part.is_multipart():
                raise Problem("validation", "That upload is malformed", 422)
            name = part.get_param("name", header="content-disposition")
            data = part.get_payload(decode=True) or b""
            if part.get_filename() is not None:
                if name != "file" or upload:
                    raise Problem("validation", "Send exactly one file, in a `file` field", 422)
                upload = (part.get_filename(), data)
            elif name:
                fields[name] = data.decode("utf-8", "replace")
        if not upload:
            raise Problem("validation", "Send a file in a `file` field", 422)
        return upload[0], upload[1], fields

    def convert(self, filename, data, fields):
        """The DocImport body and the Markdown the file becomes, before any write."""
        try:
            text, guess = docs_import.to_markdown(filename, data)
        except ValueError as exc:
            raise Problem("import_failed", str(exc), 422) from exc
        if len(text) > MAX_BODY:
            raise Problem("import_failed", "That file is too long to import (over a million characters)", 422)
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip()
        heading = re.match(r"#\s+(.+)", text)
        title = (fields.get("title") or "").strip() or (heading.group(1).strip() if heading else "") or guess or stem or "Imported doc"
        return (DocImport(filename=filename[:300], sha256=hashlib.sha256(data).hexdigest(),
                          path=(fields.get("path") or "").strip(), title=title[:300]), text)


def install_docs(app, store, auth, mutate):
    docs = Docs(app, store, auth, mutate)
    app.state.docs = docs
    from .doc_sources import install as install_sources
    install_sources(app, docs, mutate)

    def create_body(item, text):
        return DocCreate(path=item.path or None, title=item.title, body=text)

    def read_only(doc_id):
        if str(doc_id).startswith("manual:"):
            raise Problem("read_only", "The Tico manual is read-only; it ships with each release", 405)

    @app.get("/api/v2/docs")
    def list_docs(request: Request, path_prefix: str = Query(default="", max_length=300),
                  limit: int = Query(default=200, ge=1, le=LIST_MAX), cursor: str = Query(default="", max_length=400),
                  archived: bool = False):
        return docs.listing(request.state.identity, path_prefix.strip().lstrip("/"), limit, cursor, archived)

    @app.post("/api/v2/docs")
    def create_doc(request: Request, body: DocCreate):
        return mutate(request, body, docs.create(request, body))

    @app.get("/api/v2/docs/search")
    def search_docs(request: Request, q: str = Query(min_length=1, max_length=4000),
                    limit: int = Query(default=20, ge=1, le=50),
                    collection: str = Query(default="company", pattern="^(team|company|manual|all)$"),
                    source_id: str = Query(default="", max_length=100)):
        """`team` (`company` is the legacy default alias) is the team's docs; `manual` is the read-only Tico manual (backend/manual.py);
        `all` ranks team and manual sections together, each labelled with its collection."""
        who = request.state.identity
        docs.reader(who)
        if source_id:
            return docs.search(who, q, limit, source_id)
        if collection == "manual":
            return {"results": manual.search(q, limit)}
        found = docs.search(who, q, limit, source_id)["results"]
        if collection == "all":
            team = [{**r, "collection": "company"} for r in found]
            if any(r["type"] == "external" for r in found):
                return {"results": merge_ranked([team, manual.search(q, limit)], limit, manual.query_words(q))}
            found = team + manual.search(q, limit)
            found.sort(key=lambda row: (-row["score"], row["type"] == "linked", row["title"].casefold()))
            found = found[:limit]
        return {"results": found}

    @app.get("/api/v2/docs/manual")
    def manual_list(request: Request):
        docs.reader(request.state.identity)
        return {"pages": manual.listing()}

    @app.get("/api/v2/docs/manual/{name}")
    def manual_page(request: Request, name: str):
        docs.reader(request.state.identity)
        page = manual.read(name)
        if not page:
            raise Problem("not_found", "No such page in the Tico manual", 404)
        return {"doc": page}

    upload_doc = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"], "properties": {
            "file": {"type": "string", "format": "binary", "description": ".md .markdown .txt .html .htm .docx or .pdf, at most 20 MB"},
            "title": {"type": "string", "description": "Defaults to the first # heading, else the file name"},
            "path": {"type": "string", "description": "Defaults to a slug of the title"}}}}}}}

    @app.post("/api/v2/docs/import", openapi_extra=upload_doc)
    async def import_doc(request: Request):
        """A file (.md .markdown .txt .html .htm .docx .pdf, up to 20 MB) becomes an internal doc."""
        who = request.state.identity
        docs.reader(who)
        # Parsing up to 20 MB and the write both block: off the event loop, like the conversion.
        filename, data, fields = await asyncio.to_thread(docs.upload, request.headers.get("content-type", ""),
                                                         await request.body())
        item, text = await asyncio.to_thread(docs.convert, filename, data, fields)
        return await asyncio.to_thread(
            lambda: mutate(request, item, docs.create(request, create_body(item, text), imported=item.filename)))

    @app.get("/api/v2/docs/{doc_id}")
    def get_doc(request: Request, doc_id: str):
        return docs.read(request.state.identity, doc_id)

    @app.patch("/api/v2/docs/{doc_id}")
    def edit_doc(request: Request, doc_id: str, body: DocEdit):
        read_only(doc_id)
        return mutate(request, body, docs.edit(request, doc_id, body))

    @app.get("/api/v2/docs/{doc_id}/versions")
    def doc_versions(request: Request, doc_id: str):
        return docs.versions(request.state.identity, doc_id)

    @app.get("/api/v2/docs/{doc_id}/versions/{number}")
    def doc_version(request: Request, doc_id: str, number: int):
        return docs.version(request.state.identity, doc_id, number)

    @app.post("/api/v2/docs/{doc_id}/restore")
    def restore_doc(request: Request, doc_id: str, body: DocRestore):
        read_only(doc_id)
        return mutate(request, body, docs.restore(request, doc_id, body))

    @app.get("/api/v2/linked-docs")
    def list_links(request: Request):
        return docs.links(request.state.identity)

    @app.post("/api/v2/linked-docs")
    def add_linked(request: Request, body: LinkCreate):
        return mutate(request, body, docs.link_add(request, body))

    @app.patch("/api/v2/linked-docs/{link_id}")
    def edit_link(request: Request, link_id: str, body: LinkEdit):
        return mutate(request, body, docs.link_edit(request, link_id, body))
    return docs
