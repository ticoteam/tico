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
from typing import Annotated, Literal

from fastapi import Query, Request
from pydantic import BaseModel, Field

from . import (
    delivery,
    org_rule_check,
    page_owners,
    reach,
    secret_drop,
    teammate_access,
    thread_rail,
    thread_search,
    threads,
)
from .store import H, Problem

# How a person's message reaches the bot across a bot's Home or a DM with it (backend/delivery.py). Optional: with none
# a post goes as it always has, into the bot's running turn.
Way = Literal["steer", "queue", "defer"]


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
    body: str | None = None  # a first message, posted with the thread
    pin_for_members: bool = False  # pin it for its starter and every person added


class ThreadPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None
    audience: str | None = None
    audience_group_id: str | None = None
    retention_days: int | None = None  # sent as null: keep its messages for good
    pinned_for_everyone: bool | None = None  # a manager pins the root for everyone who reads it, or unpins it


class ThreadPost(BaseModel):
    text: str
    kind: str = "say"
    to: str | None = None
    refs: dict = Field(default_factory=dict)
    parent_message_id: str | None = None
    in_reply_to: str | None = None  # the message this one replies to, in the same root (flat replies)
    delivery: Way | None = None  # only in a bot's Home or a DM with one bot
    defer_for: str | None = None  # with delivery defer: <n>m, <n>h, <n>d or never (the default)
    # Digests of suspect keys the person un-marked in the Tools chat (backend/secret_drop.py); never stored.
    unmarked: list[str] = Field(default_factory=list, max_length=50)


class MessagePatch(BaseModel):
    text: str


class ReadMark(BaseModel):
    seq: int


class ReadAll(BaseModel):
    # Read what was there this long ago, not what came since: Catch up sends it once its Undo is gone, so a message
    # that arrived while Undo showed stays unread. Milliseconds, measured by the client, so its clock never matters.
    ago_ms: int | None = Field(None, ge=0, le=600_000)


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
    delivery: Way | None = None  # with `body`, in a DM with one bot
    defer_for: str | None = None


class ThreadUpload(BaseModel):
    """A message with files in a thread (POST /api/v2/uploads/threads/{thread_id}/messages). The text may be empty when
    files come with it; each file's caption is in `titles`, in upload order."""

    text: str = Field("", max_length=200_000)
    kind: str = "say"
    to: str | None = None
    parent_message_id: str | None = None
    in_reply_to: str | None = None
    titles: list[str] = Field(default_factory=list, max_length=10)
    delivery: Way | None = None
    defer_for: str | None = None


def _unmarked(who, body):
    """The suspects a person un-marked in the Tools chat (backend/secret_drop.py), which the backstop lets through only
    from the web chat: a post made with a personal access token is an API client, whose suspects are refused (AC-15)."""
    return [] if getattr(who, "via_token", False) else list(getattr(body, "unmarked", None) or [])


def _delivery_words(body):
    """The `delivery` and `defer_for` a post named, checked before anything is read: 422 when they do not hold together."""
    if wrong := delivery.check(body.delivery, body.defer_for):
        raise Problem("delivery", wrong, 422)
    if body.delivery and getattr(body, "kind", "say") != "say":
        raise Problem("delivery", "delivery goes with a plain message (kind say)", 422)
    refs = getattr(body, "refs", None) or {}
    page_owners.check_from(refs)  # a message written on a page names it and what was open there
    # The way's marks are the route's own (threads.WAY_KEYS): a client never writes them. A refs.delivery a client
    # sends with no `delivery` goes as it always did; beside a named way it would contradict it.
    if any(k in refs for k in threads.WAY_KEYS):
        raise Problem("delivery", "Name the way with delivery and defer_for", 422)
    if body.delivery and any(k in refs for k in ("delivery", "defer_for", "quiet")):
        raise Problem("delivery", "Name the way with delivery and defer_for, not in refs", 422)
    if body.delivery and getattr(body, "parent_message_id", None):
        raise Problem("delivery", "delivery goes with a message in a bot's Home or DM, not in a sub-thread", 422)


def _reply_check(c, auth, who, thread_id, body):
    """A post's `in_reply_to`, checked before anything is written: a message of the same root's open subtree the poster
    reads (threads.reply_target), never beside `parent_message_id`. 422 reply_target_invalid otherwise."""
    target = getattr(body, "in_reply_to", None)
    if not target:
        return None
    if getattr(body, "parent_message_id", None):
        raise Problem(
            "reply_target_invalid", "A reply goes in the thread: send in_reply_to without parent_message_id", 422
        )
    return threads.reply_target(c, threads.viewer(auth, who), thread_id, target)["id"]


def _bot_across(c, v, thread_id, members=None):
    """The bot on the other side of a post: a bot's Home (its owner), or a DM whose one other member is a bot (`members`
    for a DM its first message makes). None anywhere else."""
    if members is not None:
        others = [a for a in dict.fromkeys(H.resolve_actor(c, m) or m for m in members) if a != v.actor]
        return others[0] if len(others) == 1 and str(others[0]).startswith("bot:") else None
    row = threads.thread_row(c, thread_id)
    if row is None or row.get("parent_id"):
        return None
    audience = threads.audience_of(row)
    if audience == "home":
        owner = str(threads.home_owner(row) or "")
        return owner if owner.startswith("bot:") else None
    if audience in ("personal", "participants"):
        others = [a for a in threads._dm_members(c, row) if a != v.actor]
        return others[0] if len(others) == 1 and others[0].startswith("bot:") else None
    return None


def _delivery_refs(c, auth, who, body, thread_id=None, members=None):
    """(the way, the refs that send it) for a post that named `delivery`, or (None, {}) for one that did not, which goes
    exactly as before. The rules are a task comment's (backend/app.py): steer and queue wake the bot, so they take the
    right to message it (Write, or being in a DM with it); a deadline wakes it too, so anyone without that right defers
    with none. A defer while
    the bot has a turn going in this thread rides along as a queue. Only a person names a way; a bot's message goes as
    bot-to-bot messages do."""
    way, defer_for = body.delivery, body.defer_for
    if not way:
        return None, {}
    if who.role == "bot":
        raise Problem("delivery", "A bot's message goes as bot-to-bot messages do: send it without a delivery", 422)
    v = threads.viewer(auth, who)
    bot = _bot_across(c, v, thread_id, members)
    if not bot:
        raise Problem("delivery", "delivery is for a message to a bot: in its Home or a DM with it", 422)
    try:
        directs = teammate_access.can(c, who, "write", bot, auth=auth)
    except (ValueError, KeyError):
        directs = False
    # A DM is a conversation with the bot (contract §4.3: a DM queues a run), and a post there without a way already
    # wakes it: the members of a DM with it may name any way, Write or not. Without this a member's message sent from
    # the composer (which names its remembered way) went as a quiet defer with no deadline.
    if not directs and members is not None:
        directs = True  # a DM's first message: making the DM is checked as it is made (threads.dm)
    elif not directs and thread_id and _dm_with(c, v, thread_id, bot):
        directs = threads.can_post(c, v, thread_id)
    if way in ("steer", "queue") and not directs:
        raise Problem("delivery", "Only someone who may message this bot can wake it: send it with defer", 403)
    if way == "defer" and not directs:
        if defer_for not in (None, delivery.NEVER):
            raise Problem(
                "delivery",
                "Only someone who may message this bot can give it a deadline: send it with defer and no defer_for",
                403,
            )
        defer_for = None
    # Riding along wakes nothing new, but it is a queue's place in the turn: only for someone who may queue.
    if way == "defer" and directs and thread_id and delivery.running_in_session(c, H.actor_id(bot), [thread_id]):
        way = "queue"
    if way == "queue":
        return way, {"send_way": "queue", "delivery": "queue"}  # refs.delivery: execution.inputs keeps it out
    if way == "defer":
        return way, {"send_way": "defer", "quiet": True, **({"send_defer_for": defer_for} if defer_for else {})}
    return way, {}


def _dm_with(c, v, thread_id, bot):
    """Whether `thread_id` is a DM root between the viewer and `bot` alone."""
    row = threads.thread_row(c, thread_id)
    if row is None or row.get("parent_id") or threads.audience_of(row) not in ("personal", "participants"):
        return False
    return sorted(a for a in threads._dm_members(c, row)) == sorted({v.actor, bot})


def _sent_as(c, way, message):
    """What a post that named a way did (delivery.sent_as): the way and, for a defer, its deadline (null: none)."""
    due = None
    if way == "defer":
        row = c.execute(
            "SELECT due_at FROM deferrals WHERE item=? OR item IN (SELECT carrier_id FROM message_deliveries "
            "WHERE message_id=? AND carrier_id IS NOT NULL) LIMIT 1",
            (message["id"], message["id"]),
        ).fetchone()
        due = row[0] if row else None
    return delivery.sent_as(way, due)


def _thread_message(message):
    """A message as the thread routes answer it: with thread_id beside conversation_id, as the timeline and events have it."""
    return (
        {**message, "thread_id": message.get("conversation_id")}
        if message and message.get("conversation_id")
        else message
    )


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
    """The threads the viewer may read that `actor` is in, newest activity first, in the sidebar's shape: as a member
    (directly or through a group), a participant (a migrated DM, a bot on a public thread), or a party to the task. The
    viewer's read rule is the sidebar's own SQL, so a thread the viewer cannot read never shows, whoever `actor` is, and
    a teammate the viewer may not see has no threads to show."""
    limit = max(1, min(int(limit or 50), threads.MAX_PAGE))
    actor = str(actor)
    if actor.startswith("bot:"):
        actor = H.resolve_actor(c, actor) or actor  # a bot named by its slug: rows hold its id
    try:
        seen = teammate_access.levels_for(c, v.who, actor, auth=v.auth)["see"]
    except (ValueError, KeyError):
        seen = False
    if not seen:
        return {"attention": [], "threads": [], "dms": [], "next_cursor": None}
    activity = "(SELECT MAX(s.last_seq) FROM conversations s WHERE s.root_id=r.id)"
    them = threads.actor_viewer(c, actor)
    names = them.member_names(c) if them is not None else [actor]
    where = [
        "r.parent_id IS NULL",
        "r.archived_at IS NULL",
        "r.closed_at IS NULL",
        f"(EXISTS (SELECT 1 FROM thread_members m WHERE m.thread_id=r.id AND m.actor IN ({','.join('?' * len(names))})) "
        f"OR EXISTS (SELECT 1 FROM {threads._PARTICIPANTS.format(r='r')} WHERE value=?) "
        "OR EXISTS (SELECT 1 FROM tasks t WHERE t.id=r.task_id AND (t.owner_id=? OR t.requester_id=?)))",
    ]
    args = [*names, actor, actor, actor]
    if cursor:
        try:
            at, cid = str(cursor).split(":", 1)
            where.append(f"({activity}<? OR ({activity}=? AND r.id<?))")
            args += [int(at), int(at), cid]
        except ValueError:
            raise Problem("cursor", "Unknown cursor", 422) from None
    sql = (
        f"SELECT r.*, {activity} AS activity FROM conversations r WHERE "
        + " AND ".join(where)
        + " AND "
        + threads.readable_sql(c, v, "r")
        + " ORDER BY activity DESC, r.id DESC LIMIT ?"
    )
    rows = [dict(r) for r in c.execute(sql, (*args, limit * 3))]
    rows = [r for r in rows if threads.thread_readable(c, v, r["id"])]
    summaries = threads.subtree_summaries(c, v, [r["id"] for r in rows[: limit + 1]])
    dms_with = threads.dm_with(c, v, rows[: limit + 1])
    calls = threads.live_calls(c, [r["id"] for r in rows[: limit + 1]])
    found, budget = [], threads.MAX_PAGE_BYTES
    for r in rows:
        item = threads.view(c, v, r, summary=summaries.get(r["id"]), dm=dms_with.get(r["id"]), calls=calls)
        item["activity_seq"] = r["activity"] or 0
        budget -= len(json.dumps(item, default=str))
        if budget < 0 or len(found) >= limit:
            break
        found.append(item)
    dms = [t for t in found if t["audience"] in ("personal", "participants")]
    listed = [t for t in found if t["audience"] not in ("personal", "participants")]
    tail = found[-1] if found else None
    more = len(found) >= limit
    return {
        "attention": [],
        "threads": listed,
        "dms": dms,
        "next_cursor": f"{tail['activity_seq']}:{tail['id']}" if more and tail else None,
    }


MATCH_LIMIT = 5  # threads New message offers for one set of teammates
MATCH_ACTORS = 50  # teammates one match names, at most (a DM takes as many)


def _match_target(c, actor):
    """The `thread_members.actor` value a teammate named by the app stands for (a bot by its slug or id), or None."""
    actor = str(actor or "").strip()
    if actor.startswith("group:"):
        return actor if actor[6:] in threads._group_ids(c) else None
    return H.resolve_actor(c, actor) if actor.startswith(("human:", "bot:")) else None


def _match(c, v, actors):
    """New message's matches for these teammates: {"threads", "dm"}. `threads` holds the open roots the viewer may read
    whose members, the viewer left out, are exactly `actors`, newest activity first, at most MATCH_LIMIT; `dm` is the id
    of the viewer's open DM with the one person or bot named, when there is one (it is never in `threads`). A teammate
    the server does not know matches nothing. The candidates are one query driven by the first teammate's membership
    rows (`thread_members_actor`), then each passes the same read check as the rail."""
    if not actors:
        raise Problem("actor", "Name at least one teammate", 422)
    if len(actors) > MATCH_ACTORS:
        raise Problem("actor", f"At most {MATCH_ACTORS} teammates", 422)
    wanted = []
    for actor in actors:
        target = _match_target(c, actor)
        if target is None:
            return {"threads": [], "dm": None}
        if target != v.actor and target not in wanted:
            wanted.append(target)
    if not wanted:
        return {"threads": [], "dm": None}
    dm = None
    if len(wanted) == 1 and not wanted[0].startswith("group:"):
        found = threads._open_dm(c, v.actor, wanted)
        if found and threads.denial(c, v, threads.thread_row(c, found)) is None:
            dm = found
    names = {f"a{i}": a for i, a in enumerate(wanted)}
    others = "m.thread_id=r.id AND m.actor<>:me"
    sql = (
        f"SELECT r.*, {thread_rail.ACTIVITY} AS activity FROM conversations r "
        "WHERE r.id IN (SELECT m.thread_id FROM thread_members m WHERE m.actor=:a0) "
        f"AND {thread_rail.root_open(c, v)} AND r.deleted_at IS NULL "
        f"AND COALESCE(r.audience,{threads._derived_audience('r')}) IN "
        "('company','team','members','personal','participants') "
        f"AND (SELECT COUNT(*) FROM thread_members m WHERE {others})=:n "
        f"AND NOT EXISTS (SELECT 1 FROM thread_members m WHERE {others} AND m.actor NOT IN "
        f"({','.join(':' + k for k in names)})) "
        f"AND {threads.readable_sql(c, v, 'r')} ORDER BY activity DESC, r.id DESC LIMIT :fetch"
    )
    found = c.execute(sql, {**names, "me": v.actor, "n": len(wanted), "fetch": MATCH_LIMIT + 20})
    rows = []
    for r in (threads.thread_row(c, dict(r)) for r in found):
        if r and r["id"] != dm and threads.thread_readable(c, v, r):
            rows.append(r)
        if len(rows) >= MATCH_LIMIT:
            break
    v._members = {}
    try:
        shown = thread_rail._views(c, v, rows)
    finally:
        v._members = None
    return {"threads": [shown[r["id"]] for r in rows], "dm": dm}


def install(app, store, auth, mutate, rule_check):
    from .media import upload_contract
    from . import bot_home_merge

    def recipients(c, who, thread_id, to):
        """Who a thread write is for, as the org-rule check reads it (`org_rule_check.thread_people`). The recipient and
        the thread are authorized first (`auth`: 404 for a caller who may not read them), so no check reads members the
        caller cannot see and no hold names them. An outside destination is no teammate: its address is the recipient,
        as `threads.post` writes it, and the check still reads it."""
        auth.conversation(c, who, thread_id)
        if str(to or "").startswith(threads.OUTSIDE_PREFIXES):
            return org_rule_check.thread_people(c, {"thread_id": thread_id, "recipient_id": to})
        target = H.resolve_actor(c, to) if to else None
        # Two people already in a thread need no See or Write on each other; anyone else takes the teammate gate.
        if to and not (target and threads._in_it_together(c, thread_id, who.actor, target)):
            target = auth.target(c, who, to)
        return org_rule_check.thread_people(c, {"thread_id": thread_id, "recipient_id": target})

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
            return org_rule_check.place_for_start(
                c,
                author,
                people,
                audience=start["audience"],
                audience_group_id=start["audience_group_id"],
                members=start["members"],
            )

        return place

    def check(who, thread_id, body):
        """The org-rule check of one message, for a person or a bot alike (operation `threads`), outside the write. A
        bot's post to someone out of its reach is refused first (reach.check_thread), so it spends no judgment; so is a
        reply to a message the poster can't reply to. A key in a message to the Tools owner is refused before anything
        reads the text (backend/secret_drop.py)."""
        with store.read() as c:
            secret_drop.refuse(
                c,
                threads.thread_row(c, thread_id),
                who.actor,
                body.text,
                None,
                _unmarked(who, body),
                audit=False,
            )
        if getattr(body, "in_reply_to", None):
            with store.read() as c:
                _reply_check(c, auth, who, thread_id, body)
        reach.precheck(
            store, who, lambda c: reach.check_thread(c, who, thread_id, body.text, body.to, body.parent_message_id)
        )
        return rule_check(
            who,
            "threads",
            body.text,
            lambda c: recipients(c, who, thread_id, body.to),
            "",
            {"thread_id": thread_id},
            place=thread_place(who, thread_id),
        )

    @org_rule_check.replayer("threads_edit", audience=lambda c, bot, replay: org_rule_check.edit_people(c, replay))
    def replay_edit(c, bot, hold, replay, ingest, gate=None):
        """Send anyway's replay of a held edit: the edit as the person who made it, the check skipped (the hold's nonce
        and the reviewer's send are the check)."""
        return _thread_message(threads.edit_message(c, threads.viewer(auth, bot), replay["message_id"], replay["text"]))

    @app.get("/api/v2/threads")
    def sidebar(
        request: Request,
        cursor: str | None = None,
        limit: int | None = None,
        with_actor: str | None = Query(None, alias="with"),
        sort: str | None = None,
    ):
        """Needs attention, the threads and the DMs the viewer may read, one page. `sort=recent` lists newest first,
        `sort=important` by each thread's 1-10 level, then newest, without noise; neither, unread first. With
        `with=<actor>`, only the threads that teammate is a member of, in the same shape."""
        v = threads.viewer(auth, request.state.identity)
        # One snapshot for the whole page: its reads are read_cache's (neutral rows, membership) only inside a
        # transaction, so a plain read would take each of them again.
        with store.read_transaction() as c:
            if with_actor:
                return _shared(c, v, with_actor, cursor=cursor, limit=limit)
            return threads.sidebar(c, v, cursor=cursor, limit=limit, sort=sort)

    @app.get("/api/v2/threads/search")
    def search(request: Request, q: str = "", before: str | None = None, limit: int | None = None):
        """Messages whose text matches every word of `q`, newest first: only in threads and messages the viewer may
        read (C1, decided for each hit), so a hidden thread's hits and its existence never show."""
        search_index()
        with store.read() as c:
            v = threads.viewer(auth, request.state.identity)
            return threads.search(c, v, q, limit=limit or 20, before=before)

    @app.get("/api/v2/threads/rail")
    def rail(request: Request, cursor: str | None = None, limit: int | None = None):
        """The threads the viewer is in, by title: `pinned` (first page), then `threads`, paged. Never a 1:1 DM, a Home,
        a task's thread or a meeting. The first page also has Catch up's count and the unread in each one-teammate DM."""
        v = threads.viewer(auth, request.state.identity)
        with store.read_transaction() as c:
            return thread_rail.rail(c, v, cursor=cursor, limit=limit)

    @app.get("/api/v2/threads/browse")
    def browse(request: Request, q: str | None = None, cursor: str | None = None, limit: int | None = None):
        """Company and team threads the viewer may read and is not in, newest activity first, `q` matching the title.
        Following one (PUT .../follow) puts it in the rail."""
        v = threads.viewer(auth, request.state.identity)
        with store.read_transaction() as c:
            return thread_rail.browse(c, v, q=q, cursor=cursor, limit=limit)

    @app.get("/api/v2/threads/match")
    def match(request: Request, actor: Annotated[list[str], Query()] = []):  # noqa: B006 (FastAPI reads the default)
        """New message: the open threads the viewer may read whose members, the viewer left out, are exactly these
        teammates (`actor` once each: human:<id>, bot:<slug>, group:<id>), newest activity first, at most 5; and `dm`,
        the viewer's open DM with the one person or bot named, or null."""
        v = threads.viewer(auth, request.state.identity)
        with store.read_transaction() as c:
            return _match(c, v, actor)

    @app.get("/api/v2/threads/archived")
    def archived(request: Request, cursor: str | None = None, limit: int | None = None):
        """The threads the viewer archived and may still manage, newest first: Browse threads' Archived view. Unarchive is
        PATCH with `archived` false."""
        v = threads.viewer(auth, request.state.identity)
        with store.read_transaction() as c:
            return thread_rail.archived(c, v, cursor=cursor, limit=limit)

    @app.get("/api/v2/threads/catch-up")
    def catch_up(request: Request, cursor: str | None = None, limit: int | None = None):
        """Each thread with something new for the viewer, and each open Needs attention item, mentions first, with its
        new messages in tree order. `count` (at most 99) is the badge. POST .../read-all reads one."""
        v = threads.viewer(auth, request.state.identity)
        with store.read_transaction() as c:
            return thread_rail.catch_up(c, v, cursor=cursor, limit=limit)

    @app.get("/api/v2/threads/home/{actor}")
    def home_thread(request: Request, actor: str):
        """A teammate's Home (`actor` = bot:<slug>, human:<id> or group:<id>, in the path), made on first use. The viewer
        needs Read on that teammate; without it, or when the teammate does not exist, the answer is the same 404."""
        v = threads.viewer(auth, request.state.identity)
        # An existing Home is read under a snapshot: the writer is taken only to make a missing one, and access and
        # existence are checked again under it (another request may have made it meanwhile).
        with store.read_transaction() as c:
            _read_or_404(c, v, actor)
            row = threads.home(c, _home_owner(actor), create=False)
            if row is not None:
                return threads.get(c, v, row["id"])
        if getattr(
            request.state.identity, "view", ""
        ):  # a bot's read-only view of the app makes nothing (backend/app_view.py)
            raise Problem("no_home", "This teammate has no Home yet", 404)
        with store.transaction() as c:
            _read_or_404(c, v, actor)
            row = threads.home(c, _home_owner(actor))
            return threads.get(c, v, row["id"])

    @app.get("/api/v2/threads/dm")
    def dm_thread(request: Request, peer: str = Query(..., alias="with"), create: bool = True):
        """My DM with `with`: the open one with exactly us two, or a new one. Only starting one needs Write on them (the
        reply-back rule applies): 404 when I cannot see them, 403 naming Write when I can see them but may not message
        them. `create=false` makes nothing: 404 `no_dm` when there is none yet."""
        v = threads.viewer(auth, request.state.identity)
        if not create or getattr(
            request.state.identity, "view", ""
        ):  # a read-only view never starts a DM (backend/app_view.py)
            with store.read() as c:
                return threads.dm(c, v, [peer], make=False)
        with store.transaction() as c:
            return threads.dm(c, v, [peer])

    @app.get("/api/v2/threads/{thread_id}")
    def get_thread(request: Request, thread_id: str):
        """The thread view, with its first page of members under `members` (the members route's shape, capped)."""
        v = threads.viewer(auth, request.state.identity)
        with store.read() as c:
            return {**threads.get(c, v, thread_id), "members": threads.members(c, v, thread_id, limit=50)}

    @app.get("/api/v2/threads/{thread_id}/timeline")
    def timeline(
        request: Request,
        thread_id: str,
        cursor: str | None = None,
        limit: int | None = None,
        max_bytes: int | None = None,
        flat: int = 0,
    ):
        """The thread in tree order: a page of its own messages, each followed by its sub-thread's messages, and the
        breadcrumb; with `flat=1`, the thread and its open sub-threads in time order, every message at depth 0.
        `next_cursor` is the seq to pass as `cursor` for the page above. Each message has `reply_to` and `routed`; the
        first page has `bot_status`."""
        with store.read() as c:
            return threads.timeline(
                c,
                threads.viewer(auth, request.state.identity),
                thread_id,
                cursor=cursor,
                limit=limit,
                max_bytes=max_bytes,
                flat=bool(flat),
            )

    @app.get("/api/v2/threads/{thread_id}/bot-status")
    def bot_status(request: Request, thread_id: str):
        """The bots the thread's root waits on, and why (threads-contract §4.10): `{"bots": [...]}`, five at most,
        oldest wait first. 404 for a thread the viewer cannot read."""
        from . import thread_bot_status

        with store.read() as c:
            v = threads.viewer(auth, request.state.identity)
            row = threads.require(c, v, thread_id)
            root = threads.root_of(c, row)
            if root["id"] != row["id"]:
                root = threads.require(c, v, root["id"])
            return {"bots": thread_bot_status.status(c, v, root)}

    @app.get("/api/v2/threads/{thread_id}/reach")
    def reach_preview(
        request: Request,
        thread_id: str,
        text: str = "",
        delivery_: Annotated[Way | None, Query(alias="delivery")] = None,
        defer_for: str | None = None,
        in_reply_to: str | None = None,
    ):
        """Who a message with `text` (its @mentions) sent this way would reach, each with its mode: steer when no
        `delivery` is named. Writes nothing; 404 for a thread the viewer cannot read."""
        if wrong := delivery.check(delivery_, defer_for):
            raise Problem("delivery", wrong, 422)
        with store.read() as c:
            v = threads.viewer(auth, request.state.identity)
            target = threads.reply_target(c, v, thread_id, in_reply_to)["id"] if in_reply_to else None
            return threads.reaches(
                c, v, thread_id, text[:20000], way=delivery_, defer_for=defer_for, in_reply_to=target
            )

    @app.get("/api/v2/threads/{thread_id}/members")
    def members(request: Request, thread_id: str, cursor: str | None = None, limit: int | None = None):
        with store.read() as c:
            return threads.members(
                c, threads.viewer(auth, request.state.identity), thread_id, cursor=cursor, limit=limit
            )

    @app.post("/api/v2/threads")
    def create_thread(request: Request, body: ThreadCreate):
        """A root thread (audience `members` unless asked otherwise) or, with `parent_message_id`, a sub-thread. With
        `body`, its first message is posted with it, and a held first message makes neither (core's post checks before
        it creates, and answers with the hold)."""
        who = request.state.identity
        start = {
            "title": body.title,
            "audience": body.audience,
            "audience_group_id": body.audience_group_id,
            "members": body.members,
            "task_id": body.task_id,
            "kind": body.kind,
            **({"pin_for_members": True} if body.pin_for_members else {}),
        }
        if body.body and not body.parent_message_id and body.audience in ("members", "team", "company"):
            threads.require_name(who.actor, body.audience, body.title, 0)  # before the rule check spends a call

        def audience(c):
            if body.parent_message_id:
                thread_id = (H.message(c, body.parent_message_id) or {}).get("conversation_id")
                if not thread_id:
                    raise Problem("not_found", "Message not found", 404)
                auth.conversation(c, who, thread_id)
                return org_rule_check.thread_people(c, {"thread_id": thread_id})
            return org_rule_check.thread_people(c, {"start": start})

        rc = (
            rule_check(
                who, "threads", body.body, audience, "", {}, place=start_place(who, start, body.parent_message_id)
            )
            if body.body
            else None
        )

        def work(c):
            v = threads.viewer(auth, who)
            if not body.body:
                return threads.create(
                    c,
                    v,
                    title=body.title,
                    audience=body.audience,
                    audience_group_id=body.audience_group_id,
                    members=body.members,
                    parent_message_id=body.parent_message_id,
                    task_id=body.task_id,
                    kind=body.kind,
                    pin_for_members=body.pin_for_members,
                )
            if body.parent_message_id:
                # A sub-thread's first message: post makes the sub-thread only once the message goes through.
                parent = H.message(c, body.parent_message_id)
                if not parent:
                    raise Problem("not_found", "Message not found", 404)
                posted = threads.post(
                    c,
                    v,
                    parent["conversation_id"],
                    body.body,
                    parent_message_id=body.parent_message_id,
                    checked=rc,
                    reach=True,
                )
            else:
                # A new thread's first message: post makes the thread only once the message goes through, so a held
                # first message leaves no thread behind.
                posted = threads.post(c, v, None, body.body, checked=rc, start=start, reach=True)
            if "id" not in posted:  # held: no thread is made, its first message is the hold
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
            if body.pinned_for_everyone is not None:
                thread_rail.set_pinned_for_everyone(c, v, thread_id, body.pinned_for_everyone)
            return threads.get(c, v, thread_id)

        return mutate(request, body, work)

    @app.post("/api/v2/threads/{thread_id}/messages")
    def post_message(request: Request, thread_id: str, body: ThreadPost):
        """A message in the thread (in its sub-thread with `parent_message_id`). A person's or a bot's message passes the
        org-rule check first, outside the write; a held one writes nothing but its hold."""
        who = request.state.identity
        # A room that moved is checked and written as its Home (bot_home_merge.named_home), named before the check.
        thread_id = bot_home_merge.named_home(store, thread_id)
        _delivery_words(body)
        rc = check(who, thread_id, body)

        def work(c):
            v = threads.viewer(auth, who)
            replying = _reply_check(c, auth, who, thread_id, body)
            way, marks = _delivery_refs(c, auth, who, body, thread_id) if body.delivery else (None, {})
            message = threads.post(
                c,
                v,
                thread_id,
                body.text,
                kind=body.kind,
                refs={**body.refs, **marks} if marks else body.refs,
                parent_message_id=body.parent_message_id,
                to_actor=body.to,
                checked=rc,
                reach=True,
                in_reply_to=replying,
                unmarked=_unmarked(who, body),
            )
            if "id" not in message:  # held: post wrote nothing and answers with the hold
                return message
            out = _thread_message(message)
            out["reply_to"] = threads.reply_quotes(c, v, [out]).get(out["id"])
            return {**out, "sent_as": _sent_as(c, way, message)} if way else out

        return mutate(request, body, work)

    @app.patch("/api/v2/messages/{message_id}")
    def edit_message(request: Request, message_id: str, body: MessagePatch):
        """An edit of one's own message. Its new text is checked as `threads_edit` in the message's thread; a held edit
        changes nothing and Send anyway makes it."""
        who = request.state.identity
        viewer = threads.viewer(auth, who)
        with store.read() as c:
            # Only one's own message one can read is edited: anything else is the 404/403 before any check, so neither
            # the reach check nor the rule check reads (or names who reads) a message hidden from the caller.
            # Writer refusals belong in the mutation transaction; this lookup only checks ownership.
            thread_id = (
                threads._own_say(c, *threads._actor_of(viewer), message_id, check_writer=False).get("conversation_id")
                or ""
            )
        if thread_id:  # a key in an edit to the Tools owner is refused before the rule check reads it
            with store.read() as c:
                secret_drop.refuse(c, threads.thread_row(c, thread_id), who.actor, body.text, audit=False)
        # A bot's edit that @mentions someone new out of its reach is refused before the rule check spends judgment.
        reach.precheck(store, who, lambda c: reach.check_edit(c, who, message_id, body.text, viewer))
        rc = (
            rule_check(
                who,
                "threads_edit",
                body.text,
                lambda c: recipients(c, who, thread_id, None),
                "",
                {"message_id": message_id},
                place=thread_place(who, thread_id),
            )
            if thread_id
            else None
        )

        def work(c):
            threads._own_say(c, *threads._actor_of(viewer), message_id)
            gate = org_rule_check.apply(
                c,
                rc,
                body.text,
                {"message_id": message_id, "text": body.text},
                audience_now=recipients(c, who, thread_id, None) if rc else None,
            )
            if gate and gate.get("moved"):
                raise Problem(
                    "moved", "The thread changed while the edit was checked; send it again", 409, retryable=True
                )
            if gate and gate.get("held"):
                return org_rule_check.held_answer(c, who, gate, auth=auth)
            reach.check_edit(c, who, message_id, body.text, viewer)
            edited = threads.edit_message(c, viewer, message_id, body.text)
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
    def read_all(request: Request, thread_id: str, body: ReadAll | None = None):
        """Read up to now in the thread and every sub-thread under it (`ago_ms`: up to that long ago)."""
        who = request.state.identity
        body = body or ReadAll()
        return mutate(
            request, body, lambda c: threads.mark_all_read(c, threads.viewer(auth, who), thread_id, ago_ms=body.ago_ms)
        )

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
        return mutate(
            request, body, lambda c: threads.follow(c, threads.viewer(auth, who), thread_id, notify=body.notify)
        )

    @app.put("/api/v2/threads/{thread_id}/pin")
    def pin(request: Request, thread_id: str):
        """Pin a root thread for me. Reading it is enough. The answer is the thread view."""
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: thread_rail.pin(c, threads.viewer(auth, who), thread_id))

    @app.delete("/api/v2/threads/{thread_id}/pin")
    def unpin(request: Request, thread_id: str):
        """Unpin a root thread for me, over a pin for everyone too. The answer is the thread view."""
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: thread_rail.unpin(c, threads.viewer(auth, who), thread_id))

    @app.delete("/api/v2/threads/{thread_id}")
    def delete_thread(request: Request, thread_id: str):
        """Delete a thread whose messages are all deleted and that no one else is in. Refused with a plain reason (409)
        otherwise. It leaves every list and reads as not found."""
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: threads.delete(c, threads.viewer(auth, who), thread_id))

    @app.delete("/api/v2/threads/{thread_id}/follow")
    def unfollow(request: Request, thread_id: str):
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: threads.unfollow(c, threads.viewer(auth, who), thread_id))

    @app.post("/api/v2/threads/dm")
    def dm_group(request: Request, body: DmCreate):
        """A DM with these teammates: the open one with exactly these people, or a new one, with the first message when
        `body` is given. Starting one needs Write on each of them (the reply-back rule applies), as adding them to a
        thread does."""
        who = request.state.identity
        if body.delivery and not body.body:
            raise Problem("delivery", "delivery goes with a first message (body)", 422)
        _delivery_words(body)
        start = {"dm": body.members, "title": body.title}
        if body.body:  # a first message to the Tools owner holding a key makes no DM and reaches no check
            with store.read() as c:
                secret_drop.refuse_start(c, who.actor, body.members, body.body)
        rc = (
            rule_check(
                who,
                "threads",
                body.body,
                lambda c: org_rule_check.thread_people(c, {"start": start}),
                "",
                {},
                place=lambda c, author, people: org_rule_check.place_for_start(
                    c, author, people, audience="personal", members=body.members
                ),
            )
            if body.body
            else None
        )

        def work(c):
            v = threads.viewer(auth, who)
            if not body.body:
                return threads.dm(c, v, body.members, title=body.title)
            # The open DM with these people gets the message, or a new one is made with it: a held message makes none.
            way, marks = (None, {})
            if body.delivery:
                # The open DM with these people, when there is one: a defer while the bot runs there rides along.
                actor = v.actor
                existing = threads._open_dm(c, actor, threads._dm_members_for(c, actor, body.members))
                way, marks = _delivery_refs(c, auth, who, body, thread_id=existing, members=body.members)
            posted = threads.post(c, v, None, body.body, refs=marks or None, checked=rc, start=start, reach=True)
            if "id" not in posted:  # held: no DM is made, its first message is the hold
                return posted
            message = _thread_message(posted)
            if way:
                message = {**message, "sent_as": _sent_as(c, way, posted)}
            return {**threads.get(c, v, posted["conversation_id"]), "message": message}

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
            owner = str(row.get("owner_id") or "")
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
        thread_id = bot_home_merge.named_home(store, thread_id)  # as post_message names it: the Home, before the check
        checked = {}

        def admit(body):
            _delivery_words(body)
            with store.read() as c:
                threads.require(c, threads.viewer(auth, who), thread_id, write=True)
            checked["rc"] = check(who, thread_id, body)

        body, uploads = await app.state.uploads.form(request, ThreadUpload, admit)
        if not body.text.strip() and not uploads:
            raise Problem("empty", "A message needs text or a file", 422)

        def work(c):
            v = threads.viewer(auth, who)
            replying = _reply_check(c, auth, who, thread_id, body)
            way, marks = _delivery_refs(c, auth, who, body, thread_id) if body.delivery else (None, {})
            items = [register(c, who, **upload) for upload in uploads]
            for index, item in enumerate(items):
                caption = body.titles[index] if index < len(body.titles) else ""
                item["title"] = caption or item["name"]
            posted = threads.post(
                c,
                v,
                thread_id,
                body.text,
                kind=body.kind,
                to_actor=body.to,
                refs=marks or None,
                parent_message_id=body.parent_message_id,
                checked=checked["rc"],
                attachments=items or None,
                in_reply_to=replying,
            )
            if "id" not in posted:  # held: its files are in the hold's replay, linked when it is released
                return posted
            message = _thread_message(H.message(c, posted["id"]))
            message["reply_to"] = threads.reply_quotes(c, v, [message]).get(message["id"])
            if way:
                message = {**message, "sent_as": _sent_as(c, way, posted)}
            return {"thread": threads.get(c, v, thread_id), "message": message}

        return await asyncio.to_thread(app.state.uploads.write, request, body, uploads, work)

    # The search index (migration 0047) follows every write through its triggers. Install can run before the schema,
    # so the first call that finds the index marks search ready (at boot if it is there, else on the first search).
    ready = []

    def search_index():
        if not ready:
            with store.transaction() as c:
                if thread_search.ensure(c):
                    ready.append(True)

    search_index()
    app.state.thread_index = search_index  # context search asks for the index before it reads
