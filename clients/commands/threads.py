"""Threads, messages, notes and conversations.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from urllib.parse import quote
from clients import s3links
from clients.tico import APIError

from clients.commands._registry import tool
from clients.commands._schemas import DEFER_FOR, DELIVERY, _delivery, _key, _refs, _s, _target
from clients.commands._shared import VISUALS, _CHANNEL, _uploaded, _visuals
from clients.commands.assistant import assistant_send
from clients.commands._requester import _as_person


@tool(
    "tico_whoami",
    "Who you are to Tico: actor, role, Computer and run, or the External "
    "agent harness (a Hermes profile) when that is what runs you.",
    {},
)
def whoami(api, args):
    return api.get("me")


@tool(
    "tico_message_send",
    "Send a message to a bot or a human. Bot-to-human messages are linted "
    "(a nonempty first line, under 120 words) and capped at 10 unsolicited a day. `fyi` expects no reply. "
    "An ordinary bot-to-bot message does not deliver the recipient's final reply, and waits for the recipient's "
    "next run unless needs_action. "
    "To get an answer from a bot, set needs_action. Use in_reply_to or steer for a correction to an active run; "
    "it is queued until the Computer applies it.",
    {
        "to": _s("Recipient: a bot slug, `bot:<slug>`, or a human id"),
        "text": _s("The message"),
        "fyi": {
            "type": "boolean",
            "default": False,
            "description": "An fyi: it expects no reply, and takes no conversation or references",
        },
        "conversation_id": _s("Continue this conversation instead of opening a pair conversation"),
        "in_reply_to": _s("The message this corrects or answers"),
        "command": {"type": "boolean", "description": "Send the text verbatim as a harness command"},
        "steer": {
            "type": "boolean",
            "description": "Apply a correction to your active request; with refs "
            "`task:<id>` you requested, it goes into its owner's running turn on that task",
        },
        "refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "References like `task:<id>` or `approval:<id>`",
        },
        "visuals": VISUALS,
        "needs_action": {
            "type": "boolean",
            "default": False,
            "description": "To a bot: wake it now. Without this a bot's message to a bot waits for that "
            "bot's next run, whatever starts it",
        },
        "delivery": DELIVERY,
        "defer_for": DEFER_FOR,
    },
    required=("to", "text"),
    writes=True,
)
def message_send(api, args):
    # Free text a person reads: an s3:// URI becomes its view URL first (clients/s3links.py); a command goes verbatim.
    args = dict(args)
    links = None if args.get("command") else s3links.apply(api, args, ("text",))
    return s3links.annotate(_message_send(api, args), links)


def _message_send(api, args):
    if str(args["to"]).lower() == "assistant":
        return assistant_send(api, args)
    if args.get("fyi"):
        return api.post(
            "messages",
            {
                "to": args["to"],
                "text": args["text"],
                "kind": "notice",
                "conversation_id": None,
                "refs": {},
                **_delivery(args),
                **({"needs_action": True} if args.get("needs_action") else {}),
            },
            key=_key(args),
        )
    attachments, local = _visuals(
        api,
        args.get("visuals"),
        lambda index, name, data: _uploaded(
            api.request(
                "POST",
                "/api/v2/files/uploads?name=" + quote(name, safe=""),
                raw=data,
                key=_key(args, ":visual:" + index),
            )
        ),
    )
    sent = api.post(
        "messages",
        {
            "to": args["to"],
            "text": args["text"],
            "kind": "steer" if args.get("steer") else "say",
            "conversation_id": args.get("conversation_id"),
            "in_reply_to": args.get("in_reply_to"),
            "refs": _refs(args.get("refs")),
            **({"attachments": attachments} if attachments else {}),
            **({"command": True} if args.get("command") else {}),
            **_delivery(args),
            **({"needs_action": True} if args.get("needs_action") else {}),
        },
        key=_key(args),
    )
    if local and isinstance(sent, dict):
        sent = {**sent, "visuals_local": local}
    return sent


@tool(
    "tico_chat_goal",
    "Read or change the native goal pinned to a bot conversation. Read also returns its supported commands.",
    {
        "conversation_id": _s("The bot conversation"),
        "action": {"type": "string", "enum": ["get", "set", "edit", "pause", "resume", "clear"], "default": "get"},
        "objective": _s("The goal, 1–4,000 characters for set or edit"),
    },
    required=("conversation_id",),
    writes=True,
)
def chat_goal(api, args):
    path = "conversations/" + quote(args["conversation_id"], safe="") + "/goal"
    action = args.get("action") or "get"
    if action == "get":
        return api.get(path)
    return api.post(
        path, {"action": action, **({"objective": args["objective"]} if "objective" in args else {})}, key=_key(args)
    )


@tool(
    "tico_thread_catchup",
    "Catch up on a thread: what is new in it (and its sub-threads, with scope subtree) that you "
    "may read, summarized and posted to your Assistant DM. The newest 40 messages at most. Nothing new posts nothing.",
    {
        "thread_id": _s("Thread id"),
        "scope": {
            "type": "string",
            "enum": ["subtree", "thread"],
            "default": "subtree",
            "description": "subtree: the thread and its sub-threads; thread: this thread only",
        },
    },
    required=("thread_id",),
    writes=True,
)
def thread_catchup(api, args):
    return api.post(
        "threads/" + quote(args["thread_id"], safe="") + "/catch-up",
        {"scope": args.get("scope") or "subtree"},
        key=_key(args),
    )


@tool(
    "tico_note_create",
    "Leave a bot a deferred message (a note): it wakes nobody, asks nothing, and the bot's next "
    "run reads it in the same prompt as whatever woke it. With defer_for, a run starts by then if none came.",
    {"to": _s("The bot: a slug or `bot:<slug>`"), "text": _s("What it should know"), "defer_for": DEFER_FOR},
    required=("to", "text"),
    writes=True,
)
def note(api, args):
    body = {
        "to": args["to"],
        "text": args["text"],
        **({"defer_for": args["defer_for"]} if args.get("defer_for") else {}),
    }
    links = s3links.apply(api, body, ("text",))
    return s3links.annotate(api.post("notes", body, key=_key(args))["note"], links)


@tool(
    "tico_note_list",
    "Deferred messages (notes), newest first: the ones left for you and the ones you left.",
    {
        "to": _s("Only notes to this bot (`me` for you)"),
        "from": _s("Only notes from this bot"),
        "since": _s("ISO time"),
        "waiting": {"type": "boolean", "default": False},
    },
)
def notes(api, args):
    return api.get(
        "notes",
        to=args.get("to"),
        sender=args.get("from"),
        since=args.get("since"),
        waiting="true" if args.get("waiting") else None,
    )


@tool(
    "tico_note_delete",
    "Take back a deferred message (note) you left, before any run has carried it.",
    {"id": _s("The note id")},
    required=("id",),
    writes=True,
)
def unnote(api, args):
    return api.post(f"notes/{args['id']}/cancel", {}, key=_key(args))["note"]


@tool(
    "tico_conversation_show",
    "What was said in a conversation: the newest 200 messages, oldest first. The "
    "way a bot reads back past what its own session holds; the run's prompt names the conversation. "
    "`has_more` means there is an older page: pass its `next_before` as `before`.",
    {
        "conversation": _s("Conversation id"),
        "before": _s("The page before this message id: a page's next_before"),
        "since": _s("Only messages sent after this time (ISO 8601)"),
    },
    required=("conversation",),
)
def history(api, args):
    page = api.get(f"conversations/{args['conversation']}/messages", before=args.get("before"), since=args.get("since"))
    return {
        "conversation": page.get("conversation"),
        "messages": page.get("messages", []),
        "has_more": bool(page.get("has_more")),
        "next_before": page.get("next_before"),
    }


@tool(
    "tico_thread_link_slack",
    "Link a thread to a Slack channel, list its Slack links, or take them away, as the person who "
    "asked you (an owner or an admin). With a channel, asks for the link: Tico checks the channel and the thread, then "
    "activates it or refuses it, and `state` says which (the thread alone shows it again). `history` with a channel also "
    "brings in the channel's earlier messages. `off` takes the thread's Slack links away. With neither, lists them.",
    {
        "thread": _s("The thread's id: the root of a company or members thread"),
        "channel": _CHANNEL,
        "history": {
            "type": "boolean",
            "default": False,
            "description": "With a channel: bring in its earlier messages too",
        },
        "off": {"type": "boolean", "default": False, "description": "Take the thread's Slack link away"},
    },
    required=("thread",),
    writes=True,
)
def thread_link_slack(api, args):
    """`tico thread link-slack` runs this same tool (clients/remotecli.py), so the CLI and MCP cannot differ."""
    thread = str(args.get("thread") or "").strip()
    channel = str(args.get("channel") or "").strip()
    history, off = bool(args.get("history")), bool(args.get("off"))
    if not thread:
        raise APIError("usage", "Give the thread's id")
    if off and (channel or history):
        raise APIError("usage", "--off takes the thread's Slack link away: it takes no channel and no --history")
    if history and not channel:
        raise APIError("usage", "--history brings in a channel's earlier messages: name the channel too")
    person = _as_person(api)
    if channel:
        return person.post("slack/links", {"thread_id": thread, "channel": channel, "history": history}, key=_key(args))
    listing = person.get("slack/links", thread=thread, limit=100)
    if not off:
        return listing
    if not listing["links"]:
        raise APIError("not_found", "This thread has no Slack link to take away")
    return {
        "links": [
            person.call("DELETE", "slack/links/" + quote(link["id"], safe=""), None, _key(args, ":" + link["id"]))
            for link in listing["links"]
        ]
    }


@tool(
    "tico_message_redact",
    "Take a secret out of a message the person sent, replacing it with a mark. Only the writer, or a "
    "credential admin. tico_credential_set already does this for the message you were woken with.",
    {
        "message_id": _s("The message's id"),
        "value": _s("The secret text to remove"),
        "label": _s("What it was saved as"),
    },
    required=("message_id", "value"),
    writes=True,
)
def message_redact(api, args):
    body = {"span": args["value"], "label": args.get("label") or ""}
    if _as_person(api) is not api:
        body["on_behalf_of"] = "turn"
    return api.post(f"messages/{args['message_id']}/redact", body)


@tool(
    "tico_message_list",
    "Messages and notices waiting for you: the newest 100 messages and your first 100 open "
    "tasks; more_messages or more_tasks says more are waiting. Mark messages read to reach older ones.",
    {},
)
def inbox(api, args):
    return api.get("messages", unread="1")


@tool(
    "tico_message_mark_read",
    "Mark a message from your inbox as read. A runner turn does this for you; an "
    "external agent (a Hermes profile) reads its inbox itself and acknowledges what it has handled.",
    {"message_id": _s("The message id from tico_message_list")},
    required=("message_id",),
    writes=True,
)
def ack(api, args):
    return api.post("messages/" + args["message_id"] + "/ack", {}, key=_key(args))


# ----------------------------------------------------------------------------- threads (docs/threads.md)
# A thread is any conversation: a root, a sub-thread (a side branch from one message), a DM or a Home. The routes are
# /api/v2/threads (contract §10); the rules are the server's, and what a person or bot may read is decided there.
THREAD_ID = _s("Thread id: from tico_thread_list or tico_thread_show")


THREAD_CURSOR = _s("The page after this cursor: a page's next_cursor")


THREAD_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100, "description": "Items in this page (default 50)"}


THREAD_ACTOR = _s("Who: human:<id>, bot:<slug> or group:<id>")


def _thread_path(thread, *more):
    return "/".join(["threads", quote(str(thread), safe="")] + list(more))


def _reaction_path(args):
    """The route of one reaction: the message's id, then the emoji (its shortcode's colons and plus sign stay as they are)."""
    return "messages/" + quote(args["message_id"], safe="") + "/reactions/" + quote(args["emoji"], safe=":+")


def _thread_message(args, **more):
    """A message body for a thread post or reply: the text, who it is addressed to and its references."""
    body = {"text": args["text"], "kind": "say", "to": args.get("to"), **more}
    if args.get("refs"):
        body["refs"] = _refs(args["refs"])
    return {key: value for key, value in body.items() if value is not None}


@tool(
    "tico_thread_list",
    "Your threads: Needs attention first, then unread, then recent; direct messages are listed "
    "apart. With with, only the threads shared with that person, bot or group. Paged: next_cursor says whether more "
    "follow.",
    {
        "with": _s("Only the threads shared with this person, bot or group: human:<id>, bot:<slug> or group:<id>"),
        "cursor": THREAD_CURSOR,
        "limit": THREAD_LIMIT,
    },
)
def thread_list(api, args):
    return api.get("threads", **{"with": args.get("with")}, cursor=args.get("cursor"), limit=args.get("limit"))


@tool(
    "tico_thread_show",
    "One thread in tree order: each message is followed by the sub-thread that branched from it, "
    "with its depth. A long sub-thread shows its latest messages under a collapsed row: tico_thread_show on that thread "
    "with the row's cursor reads the rest. Also its title and breadcrumb. Paged over the thread's own messages; "
    "next_cursor reads on. flat: the thread and its open sub-threads as one list in time order, each message with the "
    "reply_to it answers.",
    {
        "thread": THREAD_ID,
        "cursor": THREAD_CURSOR,
        "limit": THREAD_LIMIT,
        "flat": {"type": "boolean", "default": False, "description": "One list in time order"},
    },
    required=("thread",),
)
def thread_show(api, args):
    return api.get(
        _thread_path(args["thread"], "timeline"),
        cursor=args.get("cursor"),
        limit=args.get("limit"),
        **({"flat": 1} if args.get("flat") else {}),
    )


@tool(
    "tico_thread_create",
    "Start a thread. Its audience decides who reads it: members (the people and bots you add; "
    "the default and private), team (the members of a group) or company (everyone). personal starts a direct message "
    "with the members, and reuses an open one with exactly those members. `body` is the first message. A side topic "
    "under a message is tico_thread_reply, not a new thread. Every thread needs a title, except a DM with one "
    "teammate.",
    {
        "title": _s("The title: needed unless it is a DM with one teammate"),
        "audience": {"type": "string", "enum": ["members", "team", "company", "personal"], "default": "members"},
        "audience_group_id": _s("For audience team: the group's id"),
        "members": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The people, bots and groups to add, as human:<id>, bot:<slug> or group:<id>",
        },
        "body": _s("The first message"),
        "pin_for_members": {
            "type": "boolean",
            "default": False,
            "description": "Pin it for you and every person you add",
        },
    },
    writes=True,
)
def thread_create(api, args):
    body = {
        key: args[key]
        for key in ("title", "audience", "audience_group_id", "members", "body", "pin_for_members")
        if args.get(key) not in (None, "", [], False)
    }
    return api.post("threads", body, key=_key(args))


@tool(
    "tico_thread_post",
    "Post a message to a thread. To answer "
    "one message, give its id as reply_to: your message stays in the thread, quoting the one it answers, and a bot "
    "you reply to hears it as an @mention. A name in the text does not add anyone: someone outside the thread gets a "
    "warning and nothing else, until you add them with tico_thread_members_add.",
    {
        "thread": THREAD_ID,
        "text": _s("The message"),
        "to": THREAD_ACTOR,
        "reply_to": _s("The id of the message this answers, in the same thread or a sub-thread under it"),
        "refs": {"type": "array", "items": {"type": "string"}, "description": "References like task:<id>"},
    },
    required=("thread", "text"),
    writes=True,
)
def thread_post(api, args):
    return api.post(
        _thread_path(args["thread"], "messages"),
        _thread_message(args, in_reply_to=args.get("reply_to")),
        key=_key(args),
    )


@tool(
    "tico_thread_bot_status",
    "The bots a thread is waiting on, and why: working (with its current step), up next "
    "(finishing other work first) or can't reply (with what stops it and where to fix it). Five at most, oldest "
    "wait first.",
    {"thread": THREAD_ID},
    required=("thread",),
)
def thread_bot_status(api, args):
    return api.get(_thread_path(args["thread"], "bot-status"))


@tool(
    "tico_thread_reply",
    "Reply in a message's sub-thread: a side branch under that message, made on its first reply. "
    "Only for a separate side topic; to answer a message, use tico_thread_post with reply_to.",
    {
        "thread": THREAD_ID,
        "message": _s("The message the reply branches from"),
        "text": _s("The reply"),
        "refs": {"type": "array", "items": {"type": "string"}, "description": "References like task:<id>"},
    },
    required=("thread", "message", "text"),
    writes=True,
)
def thread_reply(api, args):
    return api.post(
        _thread_path(args["thread"], "messages"),
        _thread_message(args, parent_message_id=args["message"]),
        key=_key(args),
    )


@tool(
    "tico_thread_edit",
    "Edit a message you wrote: its text becomes a new revision, shown as edited. Asks, answers and "
    "notices cannot be edited. A progress message is one edited message per task, not a line per step.",
    {"message": _s("The message's id"), "text": _s("The new text")},
    required=("message", "text"),
    writes=True,
)
def thread_edit(api, args):
    return api.patch("messages/" + quote(args["message"], safe=""), {"text": args["text"]}, key=_key(args))


@tool(
    "tico_thread_sub",
    "Open a message's sub-thread without posting: it is made if it does not exist yet, and the "
    "thread is returned either way.",
    {"message": _s("The message whose sub-thread to open")},
    required=("message",),
    writes=True,
)
def thread_sub(api, args):
    return api.post("threads", {"parent_message_id": args["message"]}, key=_key(args))


@tool(
    "tico_thread_react",
    "Add your reaction to a message, such as :+1:. The answer is the message's reactions: each "
    "emoji, its count and who reacted with it.",
    {"message_id": _s("The message's id"), "emoji": _s("The reaction: an emoji, or its shortcode such as :+1:")},
    required=("message_id", "emoji"),
    writes=True,
)
def thread_react(api, args):
    return api.call("PUT", _reaction_path(args), None, key=_key(args))


@tool(
    "tico_thread_unreact",
    "Remove your reaction from a message. The answer is the message's reactions, as tico_thread_react gives them.",
    {
        "message_id": _s("The message's id"),
        "emoji": _s("The reaction to remove: the emoji, or its shortcode such as :+1:"),
    },
    required=("message_id", "emoji"),
    writes=True,
)
def thread_unreact(api, args):
    return api.call("DELETE", _reaction_path(args), None, key=_key(args))


@tool(
    "tico_thread_follow",
    "Follow a thread, so its activity reaches you at the level you choose. Following never gives "
    "access: a thread you cannot read stays hidden whatever you follow.",
    {
        "thread": THREAD_ID,
        "notify": {
            "type": "string",
            "enum": ["live", "mentions", "digest", "muted"],
            "default": "live",
            "description": "live: every new message; mentions: only what names you; digest: in a batch; "
            "muted: unread counts stay, nothing is sent",
        },
    },
    required=("thread",),
    writes=True,
)
def thread_follow(api, args):
    return api.call(
        "PUT", _thread_path(args["thread"], "follow"), {"notify": args.get("notify") or "live"}, key=_key(args)
    )


@tool(
    "tico_thread_pin",
    "Pin a thread for yourself: it stays at the top of your thread list. Only a top-level thread "
    "is pinned (not a sub-thread). The answer is the thread.",
    {"thread": THREAD_ID},
    required=("thread",),
    writes=True,
)
def thread_pin(api, args):
    return api.call("PUT", _thread_path(args["thread"], "pin"), None, key=_key(args))


@tool(
    "tico_thread_unpin",
    "Unpin a thread for yourself, even one pinned for everyone. The answer is the thread.",
    {"thread": THREAD_ID},
    required=("thread",),
    writes=True,
)
def thread_unpin(api, args):
    return api.call("DELETE", _thread_path(args["thread"], "pin"), None, key=_key(args))


@tool(
    "tico_thread_catch_up",
    "Catch up: each thread with something new for you, and each open mention, ask or "
    "unread DM, mentions first, with the new messages in tree order (at most 20 an item; `more` counts the rest). "
    "count is every item, at most 99. Mark one read with tico_thread_read all. For a written summary of one thread, "
    "tico_thread_catchup.",
    {
        "cursor": THREAD_CURSOR,
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Items in this page (default 20)"},
    },
)
def thread_catch_up(api, args):
    return api.get("threads/catch-up", cursor=args.get("cursor"), limit=args.get("limit"))


@tool(
    "tico_thread_unfollow",
    "Stop following a thread. You keep whatever you can read.",
    {"thread": THREAD_ID},
    required=("thread",),
    writes=True,
)
def thread_unfollow(api, args):
    return api.call("DELETE", _thread_path(args["thread"], "follow"), None, key=_key(args))


@tool(
    "tico_thread_read",
    "Mark a thread read up to a message's seq (from tico_thread_show), or all of it with all. "
    "A sub-thread's read state is its own: reading the thread does not clear its side branches.",
    {
        "thread": THREAD_ID,
        "seq": {"type": "integer", "minimum": 0, "description": "The seq of the last message you read"},
        "all": {"type": "boolean", "default": False, "description": "Read the thread and every sub-thread under it"},
    },
    required=("thread",),
    writes=True,
)
def thread_read(api, args):
    if args.get("all"):
        return api.post(_thread_path(args["thread"], "read-all"), {}, key=_key(args))
    if args.get("seq") is None:
        raise APIError("invalid", "Give seq, the seq of the last message read, or all")
    return api.post(_thread_path(args["thread"], "read"), {"seq": args["seq"]}, key=_key(args))


@tool(
    "tico_thread_search",
    "Search the threads you can read, message by message. Only messages you may see are found, "
    "and a thread you cannot read is not named.",
    {"q": _s("The words to find"), "cursor": THREAD_CURSOR, "limit": THREAD_LIMIT},
    required=("q",),
)
def thread_search(api, args):
    # The route reads the page's next_cursor as `before` (backend/thread_api.py search).
    return api.get("threads/search", q=args["q"], before=args.get("cursor"), limit=args.get("limit"))


@tool(
    "tico_thread_home",
    "A teammate's Home: their thread for what comes to them, made on first use. You need Read on "
    "them; without it the answer is the same as for someone who does not exist.",
    {"actor": _s("Whose Home: bot:<slug>, human:<id> or group:<id>")},
    required=("actor",),
)
def thread_home(api, args):
    return api.get("threads/home/" + quote(args["actor"], safe=":"))


@tool(
    "tico_thread_dm",
    "Your direct message with one person or bot: the open one with exactly you two, or a new one. "
    "You need Write on them, since the reply-back rule applies. A person you cannot see is not found.",
    {"actor": _s("Who: human:<id> or bot:<slug>")},
    required=("actor",),
)
def thread_dm(api, args):
    return api.get("threads/dm", **{"with": args["actor"]})


@tool(
    "tico_thread_dm_group",
    "A group direct message: you and the people or bots in members, with an optional first "
    "message in body. A new one with two or more others needs a title. The answer is the thread, with the message "
    "when a body was given.",
    {
        "members": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "The people or bots, as human:<id> or bot:<slug>: at least one",
        },
        "body": _s("The first message, optional"),
        "title": _s("Its name: needed for a new group DM with two or more others"),
    },
    required=("members",),
    writes=True,
)
def thread_dm_group(api, args):
    members = args.get("members") or []
    if not members:
        raise APIError("invalid", "Give at least one member: human:<id> or bot:<slug>", 422)
    body = {"members": members, "body": args.get("body")}
    if args.get("title"):
        body["title"] = args["title"]
    return api.post("threads/dm", body, key=_key(args))


@tool(
    "tico_thread_members_list",
    "Who can read a thread: its people, bots and groups, with each one's role. Paged.",
    {"thread": THREAD_ID, "cursor": THREAD_CURSOR, "limit": THREAD_LIMIT},
    required=("thread",),
)
def thread_members_list(api, args):
    return api.get(_thread_path(args["thread"], "members"), cursor=args.get("cursor"), limit=args.get("limit"))


@tool(
    "tico_thread_members_add",
    "Add a person, bot or group to a thread, so it can read it. You need Write on them. A "
    "group's members are resolved live, so leaving the group takes the access away. A company or team thread adds "
    "only bots: everyone else already reads it.",
    {
        "thread": THREAD_ID,
        "actor": THREAD_ACTOR,
        "role": {"type": "string", "enum": ["member", "owner"], "default": "member"},
    },
    required=("thread", "actor"),
    writes=True,
)
def thread_members_add(api, args):
    return api.post(
        _thread_path(args["thread"], "members"),
        {"actor": args["actor"], "role": args.get("role") or "member"},
        key=_key(args),
    )


@tool(
    "tico_thread_members_remove",
    "Remove a person, bot or group from a thread: they stop reading it. The thread's "
    "owner or an admin only. A company or team thread removes only bots.",
    {"thread": THREAD_ID, "actor": THREAD_ACTOR},
    required=("thread", "actor"),
    writes=True,
)
def thread_members_remove(api, args):
    return api.call(
        "DELETE", _thread_path(args["thread"], "members", quote(args["actor"], safe=":")), None, key=_key(args)
    )


@tool(
    "tico_thread_update",
    "Rename a thread, archive or unarchive it, change who may read it: members, team (a group) "
    "or company, set how long it keeps its messages, or pin it for everyone who reads it. Only its starter or an "
    "admin can make a thread public or pin it for everyone.",
    {
        "thread": THREAD_ID,
        "title": _s("The new title"),
        "archived": {"type": "boolean"},
        "pinned_for_everyone": {
            "type": "boolean",
            "description": "true pins it for everyone who reads it, false "
            "takes that pin off. Not on a DM with one teammate, a Home or a task's thread",
        },
        "audience": {"type": "string", "enum": ["members", "team", "company"]},
        "audience_group_id": _s("For audience team: the group's id"),
        "retention_days": {
            "type": ["integer", "null"],
            "description": "On a root thread: delete its messages and its "
            "sub-threads' after this many days; null keeps them for good",
        },
    },
    required=("thread",),
    writes=True,
)
def thread_update(api, args):
    body = {
        key: args[key]
        for key in ("title", "archived", "audience", "audience_group_id", "pinned_for_everyone")
        if args.get(key) is not None
    }
    if "retention_days" in args:
        body["retention_days"] = args["retention_days"]
    return api.patch(_thread_path(args["thread"]), body, key=_key(args))


@tool(
    "tico_thread_open_earlier",
    "Open a bot's earlier messages to the people who read its Home. Only a manager of the "
    "bot may. The answer is its Home and the messages.",
    {"thread_id": THREAD_ID},
    required=("thread_id",),
    writes=True,
)
def thread_open_earlier(api, args):
    return api.post(_thread_path(args["thread_id"], "open-earlier"), {}, key=_key(args))


@tool(
    "tico_thread_delete",
    "Delete a message you wrote. It leaves a marker with no text and its sub-thread stays. "
    "Notices cannot be deleted by their writers this way.",
    {"message_id": _s("The message's id")},
    required=("message_id",),
    writes=True,
)
def thread_delete(api, args):
    return api.call("DELETE", "messages/" + quote(args["message_id"], safe=""), None, key=_key(args))


@tool(
    "tico_thread_upload",
    "Post a message with a local file to a thread: the file is streamed to Tico, and `text` is the "
    "message's words (optional). `under` posts it in that message's sub-thread. Needs a Computer to stream from.",
    {
        "thread": THREAD_ID,
        "path": _s("A local file on this computer"),
        "text": _s("The message's text, optional"),
        "to": THREAD_ACTOR,
        "under": _s("The message whose sub-thread the file goes under (optional)"),
        "reply_to": _s("The id of the message this answers (optional): the file stays in the thread, quoting it"),
    },
    required=("thread", "path"),
    writes=True,
)
def thread_upload(api, args):
    if not hasattr(api, "post_multipart"):
        raise ValueError("Stream local files with tico thread upload on your Computer")
    from clients import bot_files as BF

    path, _ = BF.local_file(BF.checkout_root(), args["path"])
    fields = {
        "text": args.get("text"),
        "to": args.get("to"),
        "parent_message_id": args.get("under"),
        "in_reply_to": args.get("reply_to"),
    }
    fields = {name: value for name, value in fields.items() if value is not None}
    return api.post_multipart(
        "uploads/threads/" + quote(str(args["thread"]), safe="") + "/messages", {"file": path}, fields, key=_key(args)
    )


@tool(
    "tico_thread_slack_resume",
    "Check a suspended or refused Slack link again, as the person who asked (an owner or an "
    "admin): Tico looks at the channel and the thread once more, then activates the link or refuses it. The link's id is "
    "in tico_thread_link_slack's listing.",
    {"link_id": _s("The Slack link's id")},
    required=("link_id",),
    writes=True,
)
def thread_slack_resume(api, args):
    return _as_person(api).post("slack/links/" + quote(str(args["link_id"]), safe="") + "/resume", {}, key=_key(args))


@tool(
    "tico_thread_task",
    "Make a task from a thread message you can read. The server copies the message and a link "
    "back, and the task is private unless the thread is company-wide.",
    {
        "message_id": _s("The message id from tico_thread_show"),
        "title": _s("Task title"),
        "owner": _s("Who does it: a bot slug, a person id, or `me`"),
    },
    required=("message_id", "title", "owner"),
    writes=True,
)
def thread_task(api, args):
    return api.post(
        "threads/messages/" + quote(args["message_id"], safe="") + "/task",
        {"title": args["title"], "owner": _target(api, args["owner"])},
        key=_key(args),
    )
