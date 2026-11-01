"""The thread routes' OpenAPI entries, staged until the routes ship (docs/threads.md; contract section 10 in
docs/dev/threads-contract.md).

backend/openapi_v2.py describes only the routes the server serves: spec() raises on a listed path the app does not
serve, so these entries wait here. When the threads API adds the routes, it moves ROUTES into STABLE, TAG into TAGS
and SCHEMAS into ANSWERS, and regenerates docs/openapi/v2.json. backend/tests/test_openapi_threads.py keeps the
drafts honest meanwhile.

An answer of None keeps what FastAPI declares for the route: the contract gives no shape for it yet.
"""

from .openapi_v2 import items, obj, ref

TAG = ("Threads", "Conversations among people and bots (docs/threads.md): a thread and its sub-threads, who can read "
                  "it, its messages, and what each reader has read or follows.")

CRUMB = obj({"id": "s", "title": "s"})

THREAD_VIEW = obj({
    "id": "s", "title": "s",
    "audience": {"type": "string", "enum": ["members", "team", "company", "personal", "task", "home", "participants",
                                            "meeting"]},
    "audience_group_id": "n", "owner_actor": "s", "parent_id": "n", "parent_message_id": "n", "root_id": "s",
    "path": items({"type": "string"}), "kind": "s", "task_id": "n", "created_at": "s", "last_message_at": "n",
    "archived_at": "n", "last_seq": "i", "last_unread_seq": "i",
    "home": {"type": ["object", "null"], "description": "Only for audience home: {actor}"},
    "me": obj({"member": "b", "following": "b",
               "notify": {"type": "string", "enum": ["live", "mentions", "digest", "muted"]},
               "last_read_seq": "i", "unread": "i", "activity": "b", "can_post": "b", "can_manage": "b"}),
}, required=["id", "title", "audience", "owner_actor", "root_id", "path", "created_at", "last_seq", "last_unread_seq",
             "me"])

THREAD_DETAIL = obj({**THREAD_VIEW["properties"], "breadcrumb": items(ref("Crumb"))},
                    required=sorted(THREAD_VIEW["required"] + ["breadcrumb"]))

# A visible message carries the body; a hidden row (the reader may not see it) carries only these three.
THREAD_MESSAGE = obj({
    "id": "s", "conversation_id": "s", "thread_id": "s", "root_id": "s", "seq": "i", "revision": "i",
    "origin_id": "n", "from_actor": "s", "to_actor": "n", "kind": "s", "body": "s", "refs": "o", "created": "s",
    "edited_at": "n", "deleted": "b", "depth": "i", "parent_message_id": "n",
    "sub_thread": {"type": ["object", "null"],
                   "description": "The sub-thread branching from this message: {id, count, last_seq, unread, more}"},
    "hidden": "b",
}, required=["id", "seq", "thread_id"])

THREAD_TIMELINE = obj({"thread": ref("ThreadView"), "breadcrumb": items(ref("Crumb")),
                       "messages": items(ref("ThreadMessage")), "next_cursor": "n"},
                      required=["thread", "breadcrumb", "messages", "next_cursor"])

THREAD_SIDEBAR = obj({"attention": items(ref("ThreadView")), "threads": items(ref("ThreadView")),
                      "dms": items(ref("ThreadView")), "next_cursor": "n"},
                     required=["attention", "threads", "dms", "next_cursor"])

_PATH = "/api/v2/threads/{thread}"

ROUTES = [
    # Path, method, tag, operationId, summary, name of the 200 answer in SCHEMAS (None: as FastAPI declares it).
    ("/api/v2/threads", "get", "Threads", "listThreads",
     "The caller's threads: needs attention first, then the rest, DMs apart; paged by cursor and limit", "ThreadSidebar"),
    ("/api/v2/threads", "post", "Threads", "createThread",
     "Start a thread with an audience, its members and an optional first message; parent_message_id gets or makes the "
     "sub-thread of a message", None),
    ("/api/v2/threads/search", "get", "Threads", "searchThreads",
     "Messages in the threads the caller can read, by q; paged by cursor and limit", None),
    (_PATH, "get", "Threads", "getThread", "One thread's view and its breadcrumb", "ThreadDetail"),
    (_PATH, "patch", "Threads", "updateThread",
     "Rename, archive or unarchive a thread, or change its audience (team or company: its starter or an admin)", None),
    (_PATH + "/timeline", "get", "Threads", "getThreadTimeline",
     "The thread's messages in tree order, each sub-thread's summary under its parent; paged by cursor and limit",
     "ThreadTimeline"),
    (_PATH + "/messages", "post", "Threads", "postThreadMessage",
     "Post a message: kind say or ask, to an actor, with refs; parent_message_id replies in a sub-thread", None),
    (_PATH + "/read", "post", "Threads", "markThreadRead", "Mark the thread read up to seq", None),
    (_PATH + "/read-all", "post", "Threads", "markAllThreadRead",
     "Mark the thread and every sub-thread under it read", None),
    (_PATH + "/members", "get", "Threads", "listThreadMembers",
     "Who can read the thread: people, bots and groups, with their roles; paged", None),
    (_PATH + "/members", "post", "Threads", "addThreadMember",
     "Add a person, bot or group to the thread (needs Write on them); role owner or member", None),
    (_PATH + "/members/{actor}", "delete", "Threads", "removeThreadMember",
     "Remove an actor from the thread: its owner or an admin", None),
    (_PATH + "/follow", "put", "Threads", "followThread",
     "Follow the thread at a level: live, mentions, digest or muted; following never grants access", None),
    (_PATH + "/follow", "delete", "Threads", "unfollowThread",
     "Stop following the thread; the caller keeps what it can read", None),
    ("/api/v2/messages/{mid}", "patch", "Threads", "editThreadMessage",
     "Edit a message's text as its author; the edit is a new revision", None),
    ("/api/v2/messages/{mid}", "delete", "Threads", "deleteThreadMessage",
     "Delete a message: it stays as a tombstone with no body", None),
]

SCHEMAS = {"Crumb": CRUMB, "ThreadView": THREAD_VIEW, "ThreadDetail": THREAD_DETAIL, "ThreadMessage": THREAD_MESSAGE,
           "ThreadTimeline": THREAD_TIMELINE, "ThreadSidebar": THREAD_SIDEBAR}
