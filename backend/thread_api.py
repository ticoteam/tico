"""Thread routes (docs/dev/threads-contract.md §10, spec §3.4): the sidebar, a thread and its timeline, read marks, search,
create, post, title and audience, members, follow, and edit or delete of one's own message.

Each route is the HTTP face of one service function in backend/threads.py, which decides what the viewer reads (C1).
A thread the viewer may not read answers 404, never 403, so its existence does not show. A post by a person or a bot
passes the org-rule check (backend/org_rule_check.py) outside the write, as a message does: a post or a first message is
checked as operation `threads` (a held one writes nothing but its hold, and Send anyway replays it under the same
operation), an edit as `threads_edit`. Every list is paged and byte-capped by the service function that makes it.
"""

import asyncio
import json

from fastapi import Query, Request
from pydantic import BaseModel, Field

from . import org_rule_check, thread_search, threads
from .store import H, Problem, encode


class Nothing(BaseModel):
    """The body of a route that takes none (mutate keys its idempotency on the body)."""


class ThreadCreate(BaseModel):
    title: str = ""
    audience: str = "members"
    audience_group_id: str | None = None
    members: list[str] = Field(default_factory=list)
    parent_message_id: str | None = None
    kind: str = "chat"
    task_id: str | None = None
    body: str | None = None                       # a first message, posted with the thread


class ThreadPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None
    audience: str | None = None
    audience_group_id: str | None = None
    retention_days: int | None = None             # sent as null: keep its messages for good


class ThreadPost(BaseModel):
    text: str
    kind: str = "say"
    to: str | None = None
    refs: dict = Field(default_factory=dict)
    parent_message_id: str | None = None


class MessagePatch(BaseModel):
    text: str


class ReadMark(BaseModel):
    seq: int


class MemberAdd(BaseModel):
    actor: str
    role: str = "member"


class Follow(BaseModel):
    notify: str = "live"


class DmCreate(BaseModel):
    """A DM with these teammates, with an optional first message (POST /api/v2/threads/dm)."""
    members: list[str] = Field(..., min_length=1, max_length=50)
    body: str | None = None
    title: str = ""


class ThreadUpload(BaseModel):
    """A message with files in a thread (POST /api/v2/uploads/threads/{thread_id}/messages). The text may be empty when
    files come with it; each file's caption is in `titles`, in upload order."""
    text: str = Field("", max_length=200_000)
    kind: str = "say"
    to: str | None = None
    parent_message_id: str | None = None
    titles: list[str] = Field(default_factory=list, max_length=10)


def _thread_message(message):
    """A message as the thread routes answer it: with thread_id beside conversation_id, as the timeline and events have it."""
    return {**message, "thread_id": message.get("conversation_id")} if message and message.get("conversation_id") else message


def _read_or_404(c, v, target):
    """Read on a teammate, or the same 404 whether they are missing or the viewer may not read them. A missing teammate
    passes this check and fails in threads.home with the same 404, so the two never differ."""
    from . import teammate_access
    try:
        levels = teammate_access.levels_for(c, v.who, target, auth=v.auth)
    except (ValueError, KeyError):
        raise Problem("not_found", "Unknown teammate", 404) from None
    if not levels["read"]:
        raise Problem("not_found", "Unknown teammate", 404)


def _home_owner(target):
    """The owner actor a Home is keyed by (`person:` reads as `human:`), as threads.home takes it."""
    from . import teammate_access
    kind, ident = teammate_access.parse_target(target)
    return f"{kind}:{ident}"


def _shared(c, v, actor, *, cursor=None, limit=None):
    """The threads the viewer may read that `actor` is a member of, newest activity first, in the sidebar's shape. The
    viewer's read rule is the sidebar's own SQL, so a thread the viewer cannot read never shows, whoever `actor` is."""
    limit = max(1, min(int(limit or 50), threads.MAX_PAGE))
    activity = "(SELECT MAX(s.last_seq) FROM conversations s WHERE s.root_id=r.id)"
    where = ["r.parent_id IS NULL", "r.archived_at IS NULL", "r.closed_at IS NULL",
             "EXISTS (SELECT 1 FROM thread_members m WHERE m.thread_id=r.id AND m.actor=?)"]
    args = [str(actor)]
    if cursor:
        try:
            at, cid = str(cursor).split(":", 1)
            where.append(f"({activity}<? OR ({activity}=? AND r.id<?))")
            args += [int(at), int(at), cid]
        except ValueError:
            raise Problem("cursor", "Unknown cursor", 422) from None
    sql = (f"SELECT r.*, {activity} AS activity FROM conversations r WHERE " + " AND ".join(where)
           + " AND " + threads.readable_sql(c, v, "r") + " ORDER BY activity DESC, r.id DESC LIMIT ?")
    found, budget = [], threads.MAX_PAGE_BYTES
    for r in c.execute(sql, (*args, limit * 3)):
        r = dict(r)
        if not threads.thread_readable(c, v, r["id"]):
            continue
        item = threads.view(c, v, r)
        item["activity_seq"] = r["activity"] or 0
        budget -= len(json.dumps(item, default=str))
        if budget < 0 or len(found) >= limit:
            break
        found.append(item)
    dms = [t for t in found if t["audience"] in ("personal", "participants")]
    listed = [t for t in found if t["audience"] not in ("personal", "participants")]
    tail = found[-1] if found else None
    more = len(found) >= limit
    return {"attention": [], "threads": listed, "dms": dms,
            "next_cursor": f"{tail['activity_seq']}:{tail['id']}" if more and tail else None}


def install(app, store, auth, mutate, rule_check):
    from .media import upload_contract

    def recipients(c, who, thread_id, to):
        """Who a thread write is for, as the org-rule check reads it (`org_rule_check.thread_people`). The recipient and
        the thread are authorized first (`auth`: 404 for a caller who may not read them), so no check reads members the
        caller cannot see and no hold names them."""
        target = auth.target(c, who, to) if to else None
        auth.conversation(c, who, thread_id)
        return org_rule_check.thread_people(c, {"thread_id": thread_id, "to_actor": target})

    def thread_place(who, thread_id):
        """A thread write's place, as `where:` reads it (its thread, root, audience and teams). The thread is authorized
        first, as `recipients` does."""
        def place(c, author, people):
            auth.conversation(c, who, thread_id)
            return org_rule_check.place_for(c, author, people, thread_id)
        return place

    def start_place(who, start, parent_message_id=None):
        """A first message's place: its sub-thread's (its parent's thread), or the new thread's own (`place_for_start`).
        Nothing is made before the check, so the place reads what the create would make."""
        def place(c, author, people):
            if parent_message_id:
                message = H.message(c, parent_message_id) or {}
                if not message.get("conversation_id"):
                    raise Problem("not_found", "Message not found", 404)
                auth.conversation(c, who, message["conversation_id"])
                return org_rule_check.place_for(c, author, people, message["conversation_id"])
            return org_rule_check.place_for_start(c, author, people, audience=start["audience"],
                                                  audience_group_id=start["audience_group_id"],
                                                  members=start["members"])
        return place

    def check(who, thread_id, body):
        """The org-rule check of one message, for a person or a bot alike (operation `threads`), outside the write."""
        return rule_check(who, "threads", body.text, lambda c: recipients(c, who, thread_id, body.to), "",
                          {"thread_id": thread_id}, place=thread_place(who, thread_id))

    @org_rule_check.replayer("threads_edit", audience=lambda c, bot, replay: org_rule_check.edit_people(c, replay))
    def replay_edit(c, bot, hold, replay, ingest, gate=None):
        """Send anyway's replay of a held edit: the edit as the person who made it, the check skipped (the hold's nonce
        and the reviewer's send are the check)."""
        return _thread_message(threads.edit_message(c, threads.viewer(auth, bot), replay["message_id"], replay["text"]))

    @app.get("/api/v2/threads")
    def sidebar(request: Request, cursor: str | None = None, limit: int | None = None,
                with_actor: str | None = Query(None, alias="with")):
        """Needs attention, the threads (recent first) and the DMs the viewer may read, one page. With `with=<actor>`,
        only the threads that teammate is a member of, in the same shape."""
        v = threads.viewer(auth, request.state.identity)
        with store.read() as c:
            if with_actor:
                return _shared(c, v, with_actor, cursor=cursor, limit=limit)
            return threads.sidebar(c, v, cursor=cursor, limit=limit)

    @app.get("/api/v2/threads/search")
    def search(request: Request, q: str = "", before: str | None = None, limit: int | None = None):
        """Messages whose text matches every word of `q`, newest first: only in threads and messages the viewer may
        read (C1, rendered as SQL), so a hidden thread's hits and its existence never show."""
        search_index()
        with store.read() as c:
            v = threads.viewer(auth, request.state.identity)
            return thread_search.search(c, q, thread_sql=threads.readable_sql(c, v, "cv"),
                                        message_sql=threads.message_sql(c, v, "m"), limit=limit or 20, before=before)

    @app.get("/api/v2/threads/home/{actor}")
    def home_thread(request: Request, actor: str):
        """A teammate's Home (`actor` = bot:<slug>, human:<id> or group:<id>, in the path), made on first use. The viewer
        needs Read on that teammate; without it, or when the teammate does not exist, the answer is the same 404."""
        v = threads.viewer(auth, request.state.identity)
        with store.transaction() as c:
            _read_or_404(c, v, actor)
            row = threads.home(c, _home_owner(actor))
            return threads.get(c, v, row["id"])

    @app.get("/api/v2/threads/dm")
    def dm_thread(request: Request, peer: str = Query(..., alias="with")):
        """My DM with `with`: the open one with exactly us two, or a new one. It needs Write on them (the reply-back rule
        applies): 404 when I cannot see them, 403 naming Write when I can see them but may not message them."""
        v = threads.viewer(auth, request.state.identity)
        with store.transaction() as c:
            threads._require_write(c, v, peer)
            return threads.dm(c, v, [peer])

    @app.get("/api/v2/threads/{thread_id}")
    def get_thread(request: Request, thread_id: str):
        """The thread view, with its first page of members under `members` (the members route's shape, capped)."""
        v = threads.viewer(auth, request.state.identity)
        with store.read() as c:
            return {**threads.get(c, v, thread_id), "members": threads.members(c, v, thread_id, limit=50)}

    @app.get("/api/v2/threads/{thread_id}/timeline")
    def timeline(request: Request, thread_id: str, cursor: str | None = None, limit: int | None = None,
                 max_bytes: int | None = None):
        """The thread's messages in tree order: a page of its own messages, sub-threads summarised inline, the parent
        and the breadcrumb. `next_cursor` is the seq to pass as `cursor` for the page above."""
        with store.read() as c:
            return threads.timeline(c, threads.viewer(auth, request.state.identity), thread_id, cursor=cursor,
                                    limit=limit, max_bytes=max_bytes)

    @app.get("/api/v2/threads/{thread_id}/members")
    def members(request: Request, thread_id: str, cursor: str | None = None, limit: int | None = None):
        with store.read() as c:
            return threads.members(c, threads.viewer(auth, request.state.identity), thread_id, cursor=cursor,
                                   limit=limit)

    @app.post("/api/v2/threads")
    def create_thread(request: Request, body: ThreadCreate):
        """A root thread (audience `members` unless asked otherwise) or, with `parent_message_id`, a sub-thread. With
        `body`, its first message is posted with it, and a held first message makes neither (core's post checks before
        it creates, and answers with the hold)."""
        who = request.state.identity
        start = {"title": body.title, "audience": body.audience, "audience_group_id": body.audience_group_id,
                 "members": body.members, "task_id": body.task_id, "kind": body.kind}
        def audience(c):
            if body.parent_message_id:
                thread_id = (H.message(c, body.parent_message_id) or {}).get("conversation_id")
                if not thread_id:
                    raise Problem("not_found", "Message not found", 404)
                auth.conversation(c, who, thread_id)
                return org_rule_check.thread_people(c, {"thread_id": thread_id})
            return org_rule_check.thread_people(c, {"start": start})
        rc = rule_check(who, "threads", body.body, audience, "", {},
                        place=start_place(who, start, body.parent_message_id)) if body.body else None

        def work(c):
            v = threads.viewer(auth, who)
            if not body.body:
                return threads.create(c, v, title=body.title, audience=body.audience,
                                      audience_group_id=body.audience_group_id, members=body.members,
                                      parent_message_id=body.parent_message_id, task_id=body.task_id, kind=body.kind)
            if body.parent_message_id:
                # A sub-thread's first message: post makes the sub-thread only once the message goes through.
                parent = H.message(c, body.parent_message_id)
                if not parent:
                    raise Problem("not_found", "Message not found", 404)
                posted = threads.post(c, v, parent["conversation_id"], body.body,
                                      parent_message_id=body.parent_message_id, checked=rc)
            else:
                # A new thread's first message: post makes the thread only once the message goes through, so a held
                # first message leaves no thread behind.
                posted = threads.post(c, v, None, body.body, checked=rc, start=start)
            if "id" not in posted:          # held: no thread is made, its first message is the hold
                return posted
            return {**threads.get(c, v, posted["conversation_id"]), "message": _thread_message(posted)}
        return mutate(request, body, work)

    @app.patch("/api/v2/threads/{thread_id}")
    def patch_thread(request: Request, thread_id: str, body: ThreadPatch):
        who = request.state.identity

        def work(c):
            v = threads.viewer(auth, who)
            if body.title is not None or body.archived is not None:
                threads.update(c, v, thread_id, title=body.title, archived=body.archived)
            if body.audience is not None:
                threads.set_audience(c, v, thread_id, body.audience, audience_group_id=body.audience_group_id)
            if "retention_days" in body.model_fields_set:
                threads.set_retention(c, v, thread_id, body.retention_days)
            return threads.get(c, v, thread_id)
        return mutate(request, body, work)

    @app.post("/api/v2/threads/{thread_id}/messages")
    def post_message(request: Request, thread_id: str, body: ThreadPost):
        """A message in the thread (in its sub-thread with `parent_message_id`). A person's or a bot's message passes the
        org-rule check first, outside the write; a held one writes nothing but its hold."""
        who = request.state.identity
        rc = check(who, thread_id, body)

        def work(c):
            message = threads.post(c, threads.viewer(auth, who), thread_id, body.text, kind=body.kind, refs=body.refs,
                                   parent_message_id=body.parent_message_id, to_actor=body.to, checked=rc)
            if "id" not in message:         # held: post wrote nothing and answers with the hold
                return message
            return _thread_message(message)
        return mutate(request, body, work)

    @app.patch("/api/v2/messages/{message_id}")
    def edit_message(request: Request, message_id: str, body: MessagePatch):
        """An edit of one's own message. Its new text is checked as `threads_edit` in the message's thread; a held edit
        changes nothing and Send anyway makes it."""
        who = request.state.identity
        with store.read() as c:
            thread_id = (H.message(c, message_id) or {}).get("conversation_id") or ""
        rc = rule_check(who, "threads_edit", body.text, lambda c: recipients(c, who, thread_id, None), "",
                        {"message_id": message_id}, place=thread_place(who, thread_id)) if thread_id else None

        def work(c):
            gate = org_rule_check.apply(c, rc, body.text, {"message_id": message_id, "text": body.text},
                                        audience_now=recipients(c, who, thread_id, None) if rc else None)
            if gate and gate.get("moved"):
                raise Problem("moved", "The thread changed while the edit was checked; send it again", 409,
                              retryable=True)
            if gate and gate.get("held"):
                return org_rule_check.held_answer(c, who, gate, auth=auth)
            edited = threads.edit_message(c, threads.viewer(auth, who), message_id, body.text)
            return {**_thread_message(edited), **org_rule_check.answer(gate)}
        return mutate(request, body, work)

    @app.delete("/api/v2/messages/{message_id}")
    def delete_message(request: Request, message_id: str):
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: threads.delete_message(c, threads.viewer(auth, who), message_id))

    @app.post("/api/v2/threads/{thread_id}/read")
    def read(request: Request, thread_id: str, body: ReadMark):
        """The viewer has read up to `seq` here. Max wins, so another device that is ahead keeps its mark."""
        who = request.state.identity
        return mutate(request, body, lambda c: threads.mark_read(c, threads.viewer(auth, who), thread_id, body.seq))

    @app.post("/api/v2/threads/{thread_id}/read-all")
    def read_all(request: Request, thread_id: str):
        """Read up to now in the thread and every sub-thread under it."""
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: threads.mark_all_read(c, threads.viewer(auth, who), thread_id))

    @app.post("/api/v2/threads/{thread_id}/members")
    def add_member(request: Request, thread_id: str, body: MemberAdd):
        who = request.state.identity

        def work(c):
            threads.add_member(c, threads.viewer(auth, who), thread_id, body.actor, role=body.role)
            return {"thread_id": thread_id, "actor": body.actor, "role": body.role}
        return mutate(request, body, work)

    @app.delete("/api/v2/threads/{thread_id}/members/{actor:path}")
    def remove_member(request: Request, thread_id: str, actor: str):
        who = request.state.identity

        def work(c):
            threads.remove_member(c, threads.viewer(auth, who), thread_id, actor)
            return {"thread_id": thread_id, "actor": actor, "removed": True}
        return mutate(request, Nothing(), work)

    @app.put("/api/v2/threads/{thread_id}/follow")
    def follow(request: Request, thread_id: str, body: Follow):
        who = request.state.identity
        return mutate(request, body, lambda c: threads.follow(c, threads.viewer(auth, who), thread_id,
                                                              notify=body.notify))

    @app.delete("/api/v2/threads/{thread_id}/follow")
    def unfollow(request: Request, thread_id: str):
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: threads.unfollow(c, threads.viewer(auth, who), thread_id))

    @app.post("/api/v2/threads/dm")
    def dm_group(request: Request, body: DmCreate):
        """A DM with these teammates: the open one with exactly these people, or a new one, with the first message when
        `body` is given. Needs Write on each of them (the reply-back rule applies), as adding them to a thread does."""
        who = request.state.identity
        start = {"dm": body.members, "title": body.title}
        rc = rule_check(who, "threads", body.body, lambda c: org_rule_check.thread_people(c, {"start": start}), "", {},
                        place=lambda c, author, people: org_rule_check.place_for_start(
                            c, author, people, audience="personal", members=body.members)) if body.body else None

        def work(c):
            v = threads.viewer(auth, who)
            for member in body.members:
                threads._require_write(c, v, member)
            if not body.body:
                return threads.dm(c, v, body.members, title=body.title)
            # The open DM with these people gets the message, or a new one is made with it: a held message makes none.
            posted = threads.post(c, v, None, body.body, checked=rc, start=start)
            if "id" not in posted:          # held: no DM is made, its first message is the hold
                return posted
            return {**threads.get(c, v, posted["conversation_id"]), "message": _thread_message(posted)}
        return mutate(request, body, work)

    @app.put("/api/v2/messages/{message_id}/reactions/{emoji}")
    def react(request: Request, message_id: str, emoji: str):
        """My reaction on a message I read (the same emoji twice is one). Answers the message's reactions."""
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: threads.react(c, threads.viewer(auth, who), message_id, emoji))

    @app.delete("/api/v2/messages/{message_id}/reactions/{emoji}")
    def unreact(request: Request, message_id: str, emoji: str):
        """Removes my reaction (a no-op without one). Answers the message's reactions."""
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: threads.unreact(c, threads.viewer(auth, who), message_id, emoji))

    @app.post("/api/v2/threads/{thread_id}/open-earlier")
    def open_earlier(request: Request, thread_id: str):
        """A manager of a bot opens its Home's earlier shared-room messages to the Home's readers. Only a bot's Home has
        them (422 otherwise); a viewer who may not manage the bot gets 403 from the service."""
        who = request.state.identity

        def work(c):
            v = threads.viewer(auth, who)
            row = threads.get(c, v, thread_id)
            owner = str(row.get("owner_actor") or "")
            if row.get("audience") != "home" or not owner.startswith("bot:"):
                raise Problem("home", "Only a bot's Home has earlier messages to open", 422)
            return threads.open_earlier_messages(c, v, owner[4:])
        return mutate(request, Nothing(), work)

    @app.post("/api/v2/uploads/threads/{thread_id}/messages", openapi_extra=upload_contract(ThreadUpload))
    async def thread_upload(request: Request, thread_id: str):
        """A person's message with files in a thread, stored as a chat's files are: each one linked to the message, under
        the same upload limit. Who may post is checked and the rule check runs before any byte is stored."""
        from .blobs import register
        from .views import human_only
        who = request.state.identity
        human_only(who)
        checked = {}

        def admit(body):
            with store.read() as c:
                threads.require(c, threads.viewer(auth, who), thread_id, write=True)
            checked["rc"] = check(who, thread_id, body)
        body, uploads = await app.state.uploads.form(request, ThreadUpload, admit)
        if not body.text.strip() and not uploads:
            raise Problem("empty", "A message needs text or a file", 422)

        def work(c):
            v = threads.viewer(auth, who)
            items = [register(c, who, **upload) for upload in uploads]
            for index, item in enumerate(items):
                caption = body.titles[index] if index < len(body.titles) else ""
                item["title"] = caption or item["name"]
            posted = threads.post(c, v, thread_id, body.text, kind=body.kind, to_actor=body.to,
                                  parent_message_id=body.parent_message_id, checked=checked["rc"],
                                  attachments=items or None)
            if "id" not in posted:          # held: no file is linked to a message that was not written
                return posted
            for item in items:
                c.execute("INSERT INTO message_assets VALUES(?,?)", (posted["id"], item["id"]))
            message = H.message(c, posted["id"])
            refs = {**(message.get("refs") or {}), "attachments": items}
            c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(refs), posted["id"]))
            return {"thread": threads.get(c, v, thread_id), "message": _thread_message(H.message(c, posted["id"]))}
        return await asyncio.to_thread(app.state.uploads.write, request, body, uploads, work)

    # The search index follows every write from here on (its triggers) and is filled from the messages that exist when
    # it is made. Install can run before the schema, so the first call that finds the tables makes it (at boot if they
    # are there, else on the first search); a write before then is indexed by that fill.
    ready = []

    def search_index():
        if not ready:
            with store.transaction() as c:
                if thread_search.ensure(c):
                    ready.append(True)

    search_index()
    app.state.thread_index = search_index     # context search asks for the index before it reads
