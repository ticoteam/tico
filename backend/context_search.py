"""Read-only, source-linked company knowledge and meeting discovery."""

import json
import re
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import Query, Request

from . import documents, market, media, meetings, threads
from .store import Problem
from . import task_privacy as privacy


def terms(text):
    return re.findall(r"\w+", text.casefold())[:40]


def excerpt(text, words, size=400):
    lower = text.casefold()
    hits = [lower.find(word) for word in words if word in lower]
    start = max(0, min(hits, default=0) - 100)
    return ("…" if start else "") + text[start:start + size] + ("…" if start + size < len(text) else "")


def readable(c, who, rid):
    """Bots may read explicitly shared meetings, never inherit their operator's private access."""
    if who.role != "bot":
        row = media.authorized(c, who, rid)
        if row.get("review_state", "live") != "live":
            raise Problem("not_found", "Meeting not found or unavailable", 404)
        return row
    row = meetings.get(rid, c)
    deleted = c.execute("SELECT deleted_at FROM media_control WHERE meeting_id=?", (rid,)).fetchone()
    if (not row or row.get("review_state", "live") != "live" or (deleted and deleted[0]) or row["metadata"].get("kind") != "meeting"
            or row["metadata"].get("private") is not False):
        raise Problem("not_found", "Meeting not found or unavailable", 404)
    return row


def meeting_info(row):
    meta = row["metadata"]
    return {"id": row["id"], "title": row["title"], "created": row["created"], "started": meta.get("started"),
            "url": "#/meetings?meeting=" + quote(row["id"], safe=""),
            "status": meta.get("status"), "review_state": row.get("review_state", "live"), "duration_ms": meta.get("duration_ms")}


def transcript_text(row):
    return (row["transcript_readable"] or row["transcript_original"]
            or "\n".join(t.get("text", "") for t in row["metadata"].get("turns", [])))


def install_context_search(app, store, auth):
    @app.get("/api/v2/context/search")
    def search(request: Request, q: str = Query(min_length=1, max_length=500),
               source: Literal["all", "docs", "market", "threads"] = "all",
               limit: int = Query(default=20, ge=1, le=50)):
        who = request.state.identity
        auth.domain(who)
        words = terms(q)
        if not words:
            raise Problem("query", "Search needs at least one word", 422)
        found = []
        index = getattr(app.state, "thread_index", None)     # the thread search index, made on first use
        if index and source in ("all", "threads"):
            index()
        with store.read() as c:
            if source in ("all", "threads"):
                # Thread messages the viewer may read: C1 for each hit, so a thread they may not read is never one.
                v = threads.viewer(auth, who)
                for hit in threads.search(c, v, q, limit=limit)["hits"]:
                    found.append({"kind": "thread_message", "id": hit["message_id"], "title": hit["title"] or "",
                                  "thread_id": hit["thread_id"], "excerpt": hit["excerpt"],
                                  "url": "#/thread/" + quote(hit["thread_id"], safe=""), "score": len(words)})
            if source in ("all", "docs"):
                # The company's own docs: internal ones (written or imported in Tico) and linked ones.
                for hit in app.state.docs.find(c, q, limit):
                    if not privacy.content_readable(c, privacy.actor(who), hit):
                        continue
                    if hit["type"] == "internal":
                        found.append({"kind": "document", "id": hit["id"], "title": hit["title"], "collection": "docs",
                                      "path": hit["path"], "url": "#/docs/" + quote(hit["id"], safe=""),
                                      "excerpt": hit["excerpt"], "score": hit["score"]})
                    elif hit["type"] == "external":
                        found.append({**hit, "kind": "external_document", "collection": "docs",
                                      "url": "#/docs/" + quote(hit["id"], safe="")})
                    else:
                        found.append({"kind": "linked_doc", "id": hit["id"], "title": hit["title"], "collection": "docs",
                                      "url": hit["url"], "excerpt": hit["description"], "score": hit["score"]})
            if source in ("all", "market"):
                for row in c.execute("SELECT * FROM documents WHERE collection='market' ORDER BY id"):
                    if not documents.visible(who, row):
                        continue
                    doc = json.loads(row["payload_json"])
                    if not privacy.content_readable(c, privacy.actor(who), doc):
                        continue
                    title, content = doc.get("title", ""), doc.get("content", "")
                    body = "\n".join((content, str(doc.get("search", ""))))
                    if all(word in (title + " " + body).casefold() for word in words):
                        found.append({"kind": "document", "id": row["id"], "title": title,
                                      "collection": row["collection"], "updated": row["updated"],
                                      "url": doc.get("url") or "#/strategy/market?note=" + quote(row["id"], safe=""),
                                      "excerpt": excerpt(body, words),
                                      "score": sum(10 if w in title.casefold() else 1 for w in words)})
            if source in ("all", "market"):
                matches = market.find(c, q, limit=limit)
                for kind, rows in (("market_entity", matches["entities"]), ("market_evidence", matches["evidence"])):
                    for row in rows:
                        if not privacy.content_readable(c, privacy.actor(who), row):
                            continue
                        body = row.get("summary") or row.get("quote") or row.get("our_read") or ""
                        found.append({"kind": kind, "id": row["id"],
                                      "title": row.get("name") or row["id"], "excerpt": excerpt(body, words),
                                      "url": row.get("source_url") or "#/strategy/market?note=" + quote(row["id"], safe=""), "score": len(words)})
        found.sort(key=lambda r: (-r["score"], r["kind"], r["id"]))
        return {"query": q, "mode": "keyword", "results": found[:limit], "has_more": len(found) > limit}

    @app.get("/api/v2/context/document")
    def document(request: Request, id: str = Query(min_length=1, max_length=200)):
        with store.read_transaction() as c:
            auth.domain(request.state.identity)
            if id.startswith("external:"):
                app.state.docs.reader(request.state.identity)
                return {**app.state.doc_sources.read_from(c, id), "collection": "docs"}
            row = c.execute("SELECT * FROM docs WHERE archived=0 AND (id=? OR path=? COLLATE NOCASE)", (id, id)).fetchone()
            if row:
                if not privacy.content_readable(c, privacy.actor(request.state.identity), dict(row)):
                    raise Problem("not_found", "Document not found", 404)
                return {"id": row["id"], "title": row["title"], "content": row["body"], "path": row["path"],
                        "collection": "docs", "category": "Internal / " + (row["path"].rpartition("/")[0] or "Docs"),
                        "url": "#/docs/" + quote(row["id"], safe=""), "version": row["version"], "updated": row["updated"]}
            result = documents.document(c, auth, request.state.identity, id)
            if not privacy.content_readable(c, privacy.actor(request.state.identity), result):
                raise Problem("not_found", "Document not found", 404)
            return result

    # `recordings` is the old name of both routes; installed CLIs and MCP clients still call it.
    @app.get("/api/v2/recordings/search", include_in_schema=False)
    @app.get("/api/v2/meetings/search")
    def meetings_search(request: Request, q: str = Query(default="", max_length=500),
                          person: str = Query(default="", max_length=200),
                          since: date | None = None, until: date | None = None,
                          limit: int = Query(default=20, ge=1, le=50),
                          offset: int = Query(default=0, ge=0)):
        who = request.state.identity
        auth.domain(who)
        if since and until and since > until:
            raise Problem("dates", "since must be on or before until", 422)
        words, found = terms(q), []
        with store.read() as c:
            for item in c.execute("SELECT id FROM meetings ORDER BY created DESC,id"):
                try:
                    row = readable(c, who, item[0])
                except Problem as exc:
                    if exc.status not in (403, 404):
                        raise
                    continue
                recorded_date = str(row["metadata"].get("started") or row["created"])[:10]
                if since and recorded_date < since.isoformat():
                    continue
                if until and recorded_date > until.isoformat():
                    continue
                meta = row["metadata"]
                turns = meta.get("turns") or []
                people = json.dumps([row["owner"], meta.get("recorded_by"), meta.get("calendar"),
                                     meta.get("participants"),
                                     (meta.get("source_context") or {}).get("lead_name"),
                                     [t.get("speaker") for t in turns]])
                if person and person.casefold() not in people.casefold():
                    continue
                text = transcript_text(row)
                body = "\n".join([text, row["notes"] or "", str(meta.get("note") or ""),
                                  "\n".join(t.get("text", "") for t in turns)])
                if not all(w in (row["title"] + " " + body).casefold() for w in words):
                    continue
                passages = [{"text": excerpt(t.get("text", ""), words), "speaker": t.get("speaker"),
                             "start_ms": t.get("start_ms"), "end_ms": t.get("end_ms")}
                            for t in turns if words and any(w in t.get("text", "").casefold() for w in words)][:5]
                found.append({**meeting_info(row), "excerpt": excerpt(body, words), "passages": passages})
                if len(found) > offset + limit:
                    break
        return {"results": found[offset:offset + limit],
                "next_offset": offset + limit if len(found) > offset + limit else None, "mode": "keyword"}

    @app.get("/api/v2/recordings/transcript", include_in_schema=False)
    @app.get("/api/v2/meetings/transcript")
    def transcript(request: Request, id: str = Query(min_length=1, max_length=200),
                   offset: int = Query(default=0, ge=0), limit: int = Query(default=20000, ge=1, le=50000)):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = readable(c, who, id)
            text = transcript_text(row)
            return {**meeting_info(row), "text": text[offset:offset + limit], "offset": offset,
                    "next_offset": offset + limit if offset + limit < len(text) else None}
