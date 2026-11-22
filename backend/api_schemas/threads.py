# pyright: strict
"""The answers of the Threads routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "ThreadView": obj(
            {
                "id": "s",
                "title": "s",
                "audience": {
                    "type": "string",
                    "enum": [
                        "company",
                        "team",
                        "members",
                        "task",
                        "home",
                        "personal",
                        "participants",
                        "meeting",
                        "entity",
                    ],
                },
                "audience_group_id": "n",
                "owner_id": "n",
                "parent_id": "n",
                "parent_message_id": "n",
                "root_id": "s",
                "path": "a",
                "kind": "n",
                "task_id": "n",
                "created_at": "s",
                "last_message_at": "n",
                "archived_at": "n",
                "last_seq": "i",
                "last_unread_seq": "i",
                "me": "o",
                "members": "o",
                "retention_days": {
                    "type": ["integer", "null"],
                    "description": "Days a root keeps its messages; null keeps them for good",
                },
                "home": {"type": ["object", "null"], "description": "Only for audience home: {actor}"},
                "pinned_for_everyone": {
                    "type": "boolean",
                    "description": "Its root is pinned for everyone "
                    "who reads it; `me.pinned` is whether it is pinned for the viewer",
                },
                "live_call": {
                    "type": ["object", "null"],
                    "description": "The thread's live call, or null",
                    "properties": {"id": {"type": "string"}, "live_count": {"type": "integer"}},
                    "required": ["id", "live_count"],
                },
            },
            required=["id", "title", "audience", "root_id", "last_seq", "last_unread_seq", "pinned_for_everyone"],
        ),
        "ThreadReactions": items(
            obj({"emoji": "s", "count": "i", "actors": "a"}, required=["emoji", "count", "actors"])
        ),
        "ThreadEarlier": obj({"home": "s", "messages": "i"}, required=["home", "messages"]),
        "ThreadUploaded": obj(
            {"thread": ref("ThreadView"), "message": ref("ThreadMessage")}, required=["thread", "message"]
        ),
        "ThreadSidebar": obj(
            {
                "attention": items({"type": "object"}),
                "threads": items(ref("ThreadView")),
                "dms": items({"type": "object"}),
                "next_cursor": "n",
                "bot_dms": ref("ThreadDmCounts"),
                "person_dms": ref("ThreadDmCounts"),
            },
            required=["attention", "threads", "dms"],
        ),
        "ThreadDmCounts": {
            "type": "object",
            "additionalProperties": {"type": "integer"},
            "description": "Unread in each open DM with one teammate, by that teammate's actor "
            "(first page only; {} after)",
        },
        "ThreadRail": obj(
            {
                "pinned": items(ref("ThreadView")),
                "threads": items(ref("ThreadView")),
                "catch_up": {
                    "type": ["integer", "null"],
                    "description": "Catch up's count, at most 99 (first page only; null after)",
                },
                "catch_up_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The root ids `catch_up` counts, at most 99 (first page only; "
                    "[] after): a client keeps the set from the live stream",
                },
                "unread": items(ref("ThreadRailUnread")),
                "dms": ref("ThreadDmCounts"),
                "next_cursor": "n",
                "seq": {
                    "type": "integer",
                    "description": "The live change number this first page is as of (first page only): a stream that "
                    "resumes from it (`GET /api/v2/events?after=`) carries every change since",
                },
            },
            ["pinned", "threads", "catch_up", "catch_up_ids", "dms", "next_cursor"],
        ),
        "ThreadRailUnread": {
            **obj(
                {
                    "id": "s",
                    "title": "s",
                    "name": "s",
                    "audience": "s",
                    "dm_with": items(obj({"actor": "s", "name": "s"})),
                    "dm_with_count": "i",
                    "home_actor": {"type": ["string", "null"], "description": "The bot or person whose Home it is"},
                    "new_count": "i",
                    "new_count_partial": {
                        "type": "boolean",
                        "description": "new_count stopped at the per-load budget of messages checked: the thread "
                        "holds at least that many new messages the viewer may read",
                    },
                    "mention": "b",
                    "task_id": {"type": ["string", "null"]},
                    "last_message_at": {"type": ["string", "null"]},
                },
                required=["id", "audience", "new_count", "new_count_partial", "mention"],
            ),
            "description": "A Catch up item the rail does not hold (a 1:1 DM, a Home, a task's thread), in Catch up's "
            "order: mentions first, then newest activity (first page only; [] after)",
        },
        "ThreadBrowse": obj(
            {"threads": items({"allOf": [ref("ThreadView"), obj({"member_count": "i"})]}), "next_cursor": "n"}
        ),
        "ThreadMatch": obj(
            {
                "threads": items(ref("ThreadView")),
                "dm": {
                    "type": ["string", "null"],
                    "description": "The viewer's open DM with the one person or bot named, or null",
                },
            },
            required=["threads", "dm"],
        ),
        "ThreadCatchUp": obj(
            {
                "items": items(
                    obj(
                        {
                            "thread": ref("ThreadView"),
                            "new_count": "i",
                            "new_count_partial": {
                                "type": "boolean",
                                "description": "new_count stopped at the per-load budget of messages checked: the thread "
                                "holds at least that many new messages the viewer may read",
                            },
                            "mention": "b",
                            "messages": items(ref("ThreadMessage")),
                            "more": "i",
                        }
                    )
                ),
                "count": {"type": "integer", "description": "Every item across pages, at most 99"},
                "next_cursor": "n",
            }
        ),
        "ThreadCatchUpCount": obj(
            {
                "count": {"type": "integer", "description": "Every Catch up item, at most 99: its `count`"},
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The root of each item counted, in Catch up's order",
                },
            },
            ["count", "ids"],
        ),
        "ThreadSearch": obj(
            {
                "hits": items(
                    obj(
                        {
                            "message_id": "s",
                            "thread_id": "s",
                            "title": "s",
                            "sender_id": "s",
                            "created_at": "s",
                            "excerpt": "s",
                        },
                        required=["message_id", "thread_id", "excerpt"],
                    )
                ),
                "next_cursor": "n",
            },
            required=["hits", "next_cursor"],
        ),
        "ThreadMessage": obj(
            {
                "id": "s",
                "conversation_id": "s",
                "thread_id": "s",
                "seq": "i",
                "sender_id": "s",
                "recipient_id": "n",
                "kind": "s",
                "body": "n",
                "created_at": "s",
                "edited_at": "n",
                "revision": "i",
                "refs": "o",
                "deleted": "b",
                "root_id": "n",
                "origin_id": "n",
                "parent_message_id": "n",
                "depth": "i",
                "hidden": "b",
                "clipped": "b",
                "sub_thread": {
                    "type": ["object", "null"],
                    "description": "The sub-thread branching from this message: {id, count, last_seq, unread, more}",
                },
                "in_reply_to": "n",
                "reply_to": {
                    "oneOf": [ref("ThreadReplyQuote"), {"type": "null"}],
                    "description": "The message this one answers (or a sub-thread message's "
                    "parent): a quote, `{id, hidden: true}` when the viewer does not "
                    "read it, or null",
                },
                "routed": {
                    "oneOf": [ref("ThreadRouted"), {"type": "null"}],
                    "description": "Who routing asked to answer this message (`to` empty: nobody); "
                    "null when routing never looked at it",
                },
            },
            required=["id", "seq", "thread_id"],
        ),
        "ThreadReplyQuote": obj(
            {
                "id": "s",
                "author": "s",
                "author_name": "s",
                "excerpt": {"type": "string", "description": "Plain text, at most 120 characters"},
                "hidden": "b",
            },
            required=["id"],
        ),
        "ThreadRouted": obj(
            {"to": items(obj({"actor": "s", "name": "s"}, required=["actor", "name"]))}, required=["to"]
        ),
        "ThreadBotStatusRow": obj(
            {
                "actor": "s",
                "name": "s",
                "state": {"type": "string", "enum": ["working", "up_next", "cant_reply"]},
                "detail": "s",
                "since": "s",
                "run_id": "n",
                "fix": {
                    "oneOf": [obj({"label": "s", "href": "s"}, required=["label", "href"]), {"type": "null"}],
                    "description": "Where to fix what stops it (cant_reply only)",
                },
            },
            required=["actor", "name", "state", "detail", "since", "run_id", "fix"],
        ),
        "ThreadBotStatus": obj({"bots": items(ref("ThreadBotStatusRow"))}, required=["bots"]),
        "ThreadReach": obj(
            {
                "thread_id": "s",
                "way": {"type": "string", "enum": ["steer", "queue", "defer"]},
                "reaches": items(
                    obj(
                        {
                            "actor": "s",
                            "reason": {
                                "type": "string",
                                "description": "mention, ask, dm, home, sub_thread, reply, follower or digest",
                            },
                            "mode": {"type": "string", "enum": ["steer", "queue", "defer", "digest"]},
                            "defer_for": "n",
                            "adds_reader": "b",
                        },
                        required=["actor", "reason", "mode"],
                    )
                ),
                "outside": items({"type": "string"}),
            },
            required=["thread_id", "reaches", "outside"],
        ),
        "ThreadCrumb": obj({"id": "s", "title": "s"}),
        "ThreadTimeline": obj(
            {
                "thread": ref("ThreadView"),
                "breadcrumb": items(ref("ThreadCrumb")),
                "messages": items({"type": "object"}),
                "task_activity": {
                    **items(ref("ChatTaskActivity")),
                    "description": "In a person's direct chat with a bot, readable task excerpts in this page's seq window",
                },
                "next_cursor": "n",
                "bot_status": {
                    "type": "array",
                    "items": ref("ThreadBotStatusRow"),
                    "description": "First page only: the bots the root waits on, as `GET .../bot-status` gives them",
                },
                "read_marks": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "description": "`flat` only: the viewer's last_read_seq in the root and in "
                    "each thread a message on the page is in (0: read nothing)",
                },
            },
            required=["thread", "messages"],
        ),
        "ThreadMembers": obj(
            {
                "members": items(
                    obj({"actor": "s", "role": "s", "added_by": "n", "created_at": "s"}, required=["actor", "role"])
                ),
                "next_cursor": "n",
            },
            required=["members"],
        ),
        "ThreadMemberChange": obj(
            {"thread_id": "s", "actor": "s", "role": "s", "removed": "b"}, required=["thread_id", "actor"]
        ),
        "ThreadRead": obj({"thread_id": "s", "last_read_seq": "i"}, required=["thread_id", "last_read_seq"]),
        "ThreadReadAll": obj({"threads": "i"}, required=["threads"]),
        "ThreadFollow": obj({"thread_id": "s", "following": "b", "notify": "s"}, required=["thread_id", "following"]),
        "ThreadMessageDeleted": obj({"id": "s", "deleted": "b", "revision": "i"}, required=["id", "deleted"]),
        "ThreadDeleted": obj({"deleted": "s"}, required=["deleted"]),
    }
)

SCHEMAS["ThreadMessage"]["properties"]["source"] = ref("MessageSource")

SCHEMAS["ThreadSearch"]["properties"]["hits"]["items"]["properties"]["source"] = ref("MessageSource")
