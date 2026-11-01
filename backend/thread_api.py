"""Thread routes (docs/dev/threads-contract.md §10, spec §3.4): the sidebar, a thread and its timeline, read marks, search,
create, post, title and audience, members, follow, and edit or delete of one's own message.

Each route is the HTTP face of one service function in backend/threads.py, which decides what the viewer reads (C1).
A thread the viewer may not read answers 404, never 403, so its existence does not show. A post by a bot passes the
org-rule check (backend/org_rule_check.py) outside the write, as a message does, and its hold replays under Send
anyway as `thread_message`. Every list is paged and byte-capped by the service function that makes it.
"""

from fastapi import Request
from pydantic import BaseModel, Field

from . import org_rule_check, thread_search, threads
from .store import H, Problem


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


def _thread_message(message):
    """A message as the thread routes answer it: with thread_id beside conversation_id, as the timeline and events have it."""
    return {**message, "thread_id": message.get("conversation_id")} if message and message.get("conversation_id") else message


def _recipients(c, thread_id, to):
    """Who a thread post is for, as the org-rule check reads it: the named recipient and the root's members."""
    out = {to} if to else set()
    row = threads.thread_row(c, thread_id)
    if row:
        out |= {r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?",
                                        (row.get("root_id") or row["id"],))}
    return sorted(a for a in out if a)


def install(app, store, auth, mutate, rule_check):
    def check(who, thread_id, body):
        """The org-rule check of one bot post, outside the write (a person's post is not checked)."""
        return rule_check(who, "thread_message", body.text, lambda c: _recipients(c, thread_id, body.to),
                          "", {"thread_id": thread_id})

    def replay_post(c, bot, hold, replay, ingest, gate=None):
        """Send anyway's replay: the post as the bot that wrote it, through the same door."""
        return threads.post(c, threads.viewer(auth, bot), replay["thread_id"], replay["text"],
                            kind=replay.get("kind") or "say", refs=replay.get("refs") or {},
                            parent_message_id=replay.get("parent_message_id"), to_actor=replay.get("to"),
                            checked=org_rule_check.decided(gate))

    org_rule_check.replayer("thread_message")(replay_post)

    @app.get("/api/v2/threads")
    def sidebar(request: Request, cursor: str | None = None, limit: int | None = None):
        """Needs attention, the threads (recent first) and the DMs the viewer may read, one page."""
        with store.read() as c:
            return threads.sidebar(c, threads.viewer(auth, request.state.identity), cursor=cursor, limit=limit)

    @app.get("/api/v2/threads/search")
    def search(request: Request, q: str = "", before: str | None = None, limit: int | None = None):
        """Messages whose text matches every word of `q`, newest first: only in threads and messages the viewer may
        read (C1, rendered as SQL), so a hidden thread's hits and its existence never show."""
        search_index()
        with store.read() as c:
            v = threads.viewer(auth, request.state.identity)
            return thread_search.search(c, q, thread_sql=threads.readable_sql(c, v, "cv"),
                                        message_sql=threads.message_sql(c, v, "m"), limit=limit or 20, before=before)

    @app.get("/api/v2/threads/{thread_id}")
    def get_thread(request: Request, thread_id: str):
        with store.read() as c:
            return threads.get(c, threads.viewer(auth, request.state.identity), thread_id)

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
        `body`, its first message is posted with it."""
        who = request.state.identity
        rc = rule_check(who, "thread_message", body.body, lambda c: [], "", {}) if body.body else None

        def work(c):
            v = threads.viewer(auth, who)
            made = threads.create(c, v, title=body.title, audience=body.audience,
                                  audience_group_id=body.audience_group_id, members=body.members,
                                  parent_message_id=body.parent_message_id, task_id=body.task_id, kind=body.kind)
            if not body.body:
                return made
            posted = threads.post(c, v, made["id"], body.body, checked=rc)
            return {**made, "message": posted}
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
            return threads.get(c, v, thread_id)
        return mutate(request, body, work)

    @app.post("/api/v2/threads/{thread_id}/messages")
    def post_message(request: Request, thread_id: str, body: ThreadPost):
        """A message in the thread (in its sub-thread with `parent_message_id`). Bots pass the org-rule check."""
        who = request.state.identity
        rc = check(who, thread_id, body)

        def work(c):
            return _thread_message(threads.post(c, threads.viewer(auth, who), thread_id, body.text, kind=body.kind,
                                                refs=body.refs, parent_message_id=body.parent_message_id,
                                                to_actor=body.to, checked=rc))
        return mutate(request, body, work)

    @app.patch("/api/v2/messages/{message_id}")
    def edit_message(request: Request, message_id: str, body: MessagePatch):
        who = request.state.identity
        return mutate(request, body, lambda c: _thread_message(
            threads.edit_message(c, threads.viewer(auth, who), message_id, body.text)))

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
