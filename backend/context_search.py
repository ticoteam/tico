"""Read-only, source-linked company knowledge and meeting discovery."""

import itertools
import json
import re
from contextlib import closing
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
    return ("…" if start else "") + text[start : start + size] + ("…" if start + size < len(text) else "")


def readable(c, who, rid, *, auth=None):
    """Bots may read explicitly shared meetings, never inherit their operator's private access."""
    if who.role != "bot":
        row = media.authorized(c, who, rid)
        if row.get("review_state", "live") != "live":
            raise Problem("not_found", "Meeting not found or unavailable", 404)
        return row
    row = meetings.get(rid, c)
    deleted = c.execute("SELECT deleted_at FROM media_control WHERE meeting_id=?", (rid,)).fetchone()
    if (
        not row
        or row.get("review_state", "live") != "live"
        or (deleted and deleted[0])
        or row["metadata"].get("kind") != "meeting"
    ):
        raise Problem("not_found", "Meeting not found or unavailable", 404)
    team = media.effective_audience(row["metadata"]) == "team" and row["metadata"].get("private") is False
    if team:
        return row
    attached = c.execute(
        "SELECT 1 FROM live_meetings m JOIN live_meeting_bots b ON b.meeting_id=m.id "
        "WHERE m.imported_meeting_id=? AND m.discarded_at IS NULL AND b.bot=?",
        (rid, who.actor.removeprefix("bot:")),
    ).fetchone()
    if not attached:
        raise Problem("not_found", "Meeting not found or unavailable", 404)
    return row


def meeting_info(row):
    meta = row["metadata"]
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "started_at": meta.get("started"),
        "url": "#/meetings?meeting=" + quote(row["id"], safe=""),
        "status": meta.get("status"),
        "review_state": row.get("review_state", "live"),
        "duration_ms": meta.get("duration_ms"),
    }


def transcript_text(row):
    return (
        row["transcript_readable"]
        or row["transcript_original"]
        or "\n".join(t.get("text", "") for t in row["metadata"].get("turns", []))
    )


def search_text(row, body):
    """What a meeting search matches its words against, case-folded once per candidate."""
    return (row["title"] + " " + body).casefold()


# The metadata access and the date need; a candidate's turns and transcripts are not read to decide either.
ACCESS_KEYS = ("kind", "audience", "private", "audience_emails", "calendar", "participants", "started")
# The date today's check compares (str(started or created_at)[:10]), where SQL can say it exactly: a text `started`,
# else an unset, null, false or empty one falls back to a text created_at. Anything else is NULL, kept for Python.
# The index meetings_recorded_day (migration 20261112080700) is on this expression, character for character.
RECORDED_DAY = (
    "CASE WHEN json_type(metadata,'$.started')='text' AND json_extract(metadata,'$.started')<>'' "
    "THEN substr(json_extract(metadata,'$.started'),1,10) "
    "WHEN (json_type(metadata,'$.started') IS NULL OR json_type(metadata,'$.started') IN ('null','false') "
    "OR json_type(metadata,'$.started')='text') AND typeof(created_at)='text' THEN substr(created_at,1,10) END"
)


def candidates(c, who, since=None, until=None):
    """Meetings `who` may read and whose date is in range, newest first, without their transcripts.

    SQL drops only rows the per-meeting rules (`readable`) certainly refuse: anything else stays a candidate, and the
    same rules decide it here, on the metadata they read. One statement for the whole archive, read lazily: without
    dates it walks meetings_newest and stops where the caller stops; with dates meetings_recorded_day finds the range.
    """
    where, args = ["m.review_state='live'", "NOT (typeof(mc.deleted_at)='text' AND mc.deleted_at<>'')"], []
    index = "meetings_newest"
    if since or until:
        index, day = "meetings_recorded_day", f"({RECORDED_DAY})"
        bounds = [f"{day}{op}?" for bound, op in ((since, ">="), (until, "<=")) if bound]
        # Both OR terms must stay answerable from meetings_recorded_day: a clause the index cannot seek turns the
        # plan into a silent scan of the whole index, every meeting's metadata parsed, with the same results.
        where.append(f"(({' AND '.join(bounds)}) OR {day} IS NULL)")
        args += [bound.isoformat() for bound in (since, until) if bound]
    attached = "0"
    if who.role == "bot":
        attached = (
            "m.id IN (SELECT lm.imported_meeting_id FROM live_meetings lm JOIN live_meeting_bots b "
            "ON b.meeting_id=lm.id WHERE lm.discarded_at IS NULL AND b.bot=?)"
        )
        args.insert(0, who.actor.removeprefix("bot:"))
        where += [
            "json_extract(m.metadata,'$.kind')='meeting'",
            f"(json_type(m.metadata,'$.private')='false' OR {attached})",
        ]
        args.append(args[0])
    elif who.role == "human":
        # Another person's typed note is never theirs to read. SQLite's NOCASE folds ASCII only, so the owner
        # comparison is made in SQL only where both sides are ASCII; a non-ASCII owner stays a candidate.
        mine = "0"
        if who.email and who.email.isascii():
            mine = "(m.owner=? COLLATE NOCASE OR length(m.owner)<>length(CAST(m.owner AS BLOB)))"
            args.append(who.email)
        elif who.email:
            mine = "1"
        where.append(f"(json_extract(m.metadata,'$.kind')='meeting' OR {mine})")
    paths = ",".join(f"'$.{key}'" for key in ACCESS_KEYS)
    rows = c.execute(
        f"SELECT m.id,m.owner,m.created_at,m.review_state,mc.deleted_at,json_extract(m.metadata,{paths}) AS access,"
        f"{attached} AS attached FROM meetings m INDEXED BY {index} LEFT JOIN media_control mc ON mc.meeting_id=m.id "
        f"WHERE {' AND '.join(where)} ORDER BY m.created_at DESC,m.id",
        args,
    )
    with closing(rows):
        yield from decided(who, rows, since, until)


def decided(who, rows, since, until):
    for row in rows:
        meta = dict(zip(ACCESS_KEYS, json.loads(row["access"]), strict=True))
        record = {"id": row["id"], "owner": row["owner"], "metadata": meta, "review_state": row["review_state"]}
        if not may_find(who, record, row["deleted_at"], row["attached"]):
            continue
        recorded_date = str(meta.get("started") or row["created_at"])[:10]
        if since and recorded_date < since.isoformat():
            continue
        if until and recorded_date > until.isoformat():
            continue
        yield row["id"]


def may_find(who, record, deleted, attached):
    """`readable`'s answer for one meeting, from its metadata, tombstone and the bot's live-meeting attachment."""
    if deleted or record.get("review_state", "live") != "live":
        return False
    meta = record["metadata"]
    if who.role == "bot":
        if meta.get("kind") != "meeting":
            return False
        return bool(media.effective_audience(meta) == "team" and meta.get("private") is False or attached)
    return media.may_write(who, record) or (who.role in ("human", "owner") and media.shared_meeting(record, who))


def loaded(c, ids, wanted, cap=256):
    """Full meeting rows for candidate `ids`, in their order: one statement per batch, batches growing from `wanted`."""
    ids, size = iter(ids), max(wanted, 8)
    while batch := list(itertools.islice(ids, size)):
        rows = c.execute(
            "SELECT * FROM meetings WHERE id IN (SELECT value FROM json_each(?)) ORDER BY created_at DESC,id",
            (json.dumps(batch),),
        )
        with closing(rows):
            for row in rows:
                record = dict(row)
                record["metadata"] = json.loads(record["metadata"])
                record["metadata"]["review_state"] = record["review_state"]
                yield record
        size = min(size * 2, cap)


def install_context_search(app, store, auth):
    @app.get("/api/v2/context/search")
    def search(
        request: Request,
        q: str = Query(min_length=1, max_length=500),
        source: Literal["all", "docs", "market", "threads"] = "all",
        limit: int = Query(default=20, ge=1, le=50),
    ):
        who = request.state.identity
        auth.domain(who)
        words = terms(q)
        if not words:
            raise Problem("query", "Search needs at least one word", 422)
        found = []
        index = getattr(app.state, "thread_index", None)  # the thread search index, made on first use
        if index and source in ("all", "threads"):
            index()
        with store.read() as c:
            if source in ("all", "threads"):
                # Thread messages the viewer may read: C1 for each hit, so a thread they may not read is never one.
                v = threads.viewer(auth, who)
                for hit in threads.search(c, v, q, limit=limit)["hits"]:
                    found.append(
                        {
                            "kind": "thread_message",
                            "id": hit["message_id"],
                            "title": hit["title"] or "",
                            "thread_id": hit["thread_id"],
                            "excerpt": hit["excerpt"],
                            "url": "#/thread/" + quote(hit["thread_id"], safe=""),
                            "score": len(words),
                        }
                    )
            if source in ("all", "docs"):
                # The company's own docs: internal ones (written or imported in Tico) and linked ones.
                for hit in app.state.docs.find(c, q, limit):
                    if not privacy.content_readable(c, privacy.actor(who), hit):
                        continue
                    if hit["type"] == "internal":
                        found.append(
                            {
                                "kind": "document",
                                "id": hit["id"],
                                "title": hit["title"],
                                "collection": "docs",
                                "path": hit["path"],
                                "url": "#/docs/" + quote(hit["id"], safe=""),
                                "excerpt": hit["excerpt"],
                                "score": hit["score"],
                            }
                        )
                    elif hit["type"] == "external":
                        found.append(
                            {
                                **hit,
                                "kind": "external_document",
                                "collection": "docs",
                                "url": "#/docs/" + quote(hit["id"], safe=""),
                            }
                        )
                    else:
                        found.append(
                            {
                                "kind": "linked_doc",
                                "id": hit["id"],
                                "title": hit["title"],
                                "collection": "docs",
                                "url": hit["url"],
                                "excerpt": hit["description"],
                                "score": hit["score"],
                            }
                        )
            if source in ("all", "market"):
                for doc in market.list_pages(c):
                    if not privacy.content_readable(c, privacy.actor(who), doc):
                        continue
                    title, content = doc.get("title", ""), doc.get("content", "")
                    body = "\n".join((content, str(doc.get("search", ""))))
                    if all(word in (title + " " + body).casefold() for word in words):
                        found.append(
                            {
                                "kind": "document",
                                "id": doc["id"],
                                "title": title,
                                "collection": doc["collection"],
                                "updated_at": doc["fetched"],
                                "url": "#/strategy/market?note=" + quote(doc["id"], safe=""),
                                "excerpt": excerpt(body, words),
                                "score": sum(10 if w in title.casefold() else 1 for w in words),
                            }
                        )
            if source in ("all", "market"):
                matches = market.find(c, q, limit=limit)
                for kind, rows in (("market_entity", matches["entities"]), ("market_evidence", matches["evidence"])):
                    for row in rows:
                        if not privacy.content_readable(c, privacy.actor(who), row):
                            continue
                        body = row.get("summary") or row.get("quote") or row.get("our_read") or ""
                        found.append(
                            {
                                "kind": kind,
                                "id": row["id"],
                                "title": row.get("name") or row["id"],
                                "excerpt": excerpt(body, words),
                                "url": row.get("source_url") or "#/strategy/market?note=" + quote(row["id"], safe=""),
                                "score": len(words),
                            }
                        )
        found.sort(key=lambda r: (-r["score"], r["kind"], r["id"]))
        return {"query": q, "mode": "keyword", "results": found[:limit], "has_more": len(found) > limit}

    @app.get("/api/v2/context/document")
    def document(request: Request, id: str = Query(min_length=1, max_length=200)):
        with store.read_transaction() as c:
            auth.domain(request.state.identity)
            if id.startswith("external:"):
                app.state.docs.reader(request.state.identity)
                return {**app.state.doc_sources.read_from(c, id), "collection": "docs"}
            row = c.execute(
                "SELECT * FROM docs WHERE archived=0 AND (id=? OR path=? COLLATE NOCASE)", (id, id)
            ).fetchone()
            if row:
                if not privacy.content_readable(c, privacy.actor(request.state.identity), dict(row)):
                    raise Problem("not_found", "Document not found", 404)
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "content": row["body"],
                    "path": row["path"],
                    "collection": "docs",
                    "category": "Internal / " + (row["path"].rpartition("/")[0] or "Docs"),
                    "url": "#/docs/" + quote(row["id"], safe=""),
                    "version": row["version"],
                    "updated_at": row["updated_at"],
                }
            result = documents.document(c, auth, request.state.identity, id)
            if not privacy.content_readable(c, privacy.actor(request.state.identity), result):
                raise Problem("not_found", "Document not found", 404)
            return result

    # `recordings` is the old name of both routes; installed CLIs and MCP clients still call it.
    @app.get("/api/v2/recordings/search", include_in_schema=False)
    @app.get("/api/v2/meetings/search")
    def meetings_search(
        request: Request,
        q: str = Query(default="", max_length=500),
        person: str = Query(default="", max_length=200),
        since: date | None = None,
        until: date | None = None,
        limit: int = Query(default=20, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
    ):
        who = request.state.identity
        auth.domain(who)
        if since and until and since > until:
            raise Problem("dates", "since must be on or before until", 422)
        words, found, wanted = terms(q), [], offset + limit + 1
        # One snapshot: every access decision and every row returned come from the same moment.
        with (
            store.read_transaction() as c,
            closing(candidates(c, who, since, until)) as ids,
            closing(loaded(c, ids, wanted)) as rows,
        ):
            for row in rows:
                meta = row["metadata"]
                turns = meta.get("turns") or []
                people = json.dumps(
                    [
                        row["owner"],
                        meta.get("recorded_by"),
                        meta.get("calendar"),
                        meta.get("participants"),
                        (meta.get("source_context") or {}).get("lead_name"),
                        [t.get("speaker") for t in turns],
                    ]
                )
                if person and person.casefold() not in people.casefold():
                    continue
                text = transcript_text(row)
                body = "\n".join(
                    [text, row["notes"] or "", str(meta.get("note") or ""), "\n".join(t.get("text", "") for t in turns)]
                )
                haystack = search_text(row, body)
                if not all(w in haystack for w in words):
                    continue
                passages = [
                    {
                        "text": excerpt(t.get("text", ""), words),
                        "speaker": t.get("speaker"),
                        "start_ms": t.get("start_ms"),
                        "end_ms": t.get("end_ms"),
                    }
                    for t in turns
                    if words and any(w in t.get("text", "").casefold() for w in words)
                ][:5]
                found.append({**meeting_info(row), "excerpt": excerpt(body, words), "passages": passages})
                if len(found) >= wanted:
                    break
        return {
            "results": found[offset : offset + limit],
            "next_offset": offset + limit if len(found) > offset + limit else None,
            "mode": "keyword",
        }

    @app.get("/api/v2/recordings/transcript", include_in_schema=False)
    @app.get("/api/v2/meetings/transcript")
    def transcript(
        request: Request,
        id: str = Query(min_length=1, max_length=200),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20000, ge=1, le=50000),
        notes: bool = False,
    ):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = readable(c, who, id, auth=auth)
            text = (row["notes"] or "") if notes else transcript_text(row)
            return {
                **meeting_info(row),
                "text": text[offset : offset + limit],
                "offset": offset,
                **({"notes": (row["notes"] or "")[:50000]} if offset == 0 else {}),
                "next_offset": offset + limit if offset + limit < len(text) else None,
            }
