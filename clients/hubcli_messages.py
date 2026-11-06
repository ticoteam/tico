"""Conversation commands: meetings, messages, threads, notes and feedback."""

from clients.hubcli_core import (
    defer_for,
    delivery_flags,
    thread_page_flags,
)


def add_messages(sub):
    meeting = sub.add_parser(
        "meeting", help="search meetings, read transcripts, import a transcript from another tool"
    ).add_subparsers(dest="sub")
    granola = meeting.add_parser(
        "granola", help="your Granola account; connect in Meetings in your browser"
    ).add_subparsers(dest="granola_action")
    for action in ("status", "sync"):
        granola.add_parser(action).set_defaults(fn="meeting granola " + action)
    s = meeting.add_parser("link", help="link existing tasks to a saved meeting")
    s.add_argument("id")
    s.add_argument("task_ids", nargs="+")
    s.set_defaults(fn="meeting link")
    s = meeting.add_parser("unlink", help="remove a meeting's task link")
    s.add_argument("id")
    s.add_argument("task")
    s.set_defaults(fn="meeting unlink")
    s = meeting.add_parser("search")
    s.add_argument("q", nargs="?", default="")
    s.add_argument("--person", default="")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.set_defaults(fn="meeting search")
    s = meeting.add_parser("read", help="read a transcript")
    s.add_argument("id")
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--limit", type=int, default=20000)
    s.add_argument("--notes", action="store_true", default=None, help="read only the meeting's notes")
    s.set_defaults(fn="meeting read")
    s = meeting.add_parser("delete", help="delete a Meeting you may edit")
    s.add_argument("id")
    s.set_defaults(fn="meeting delete")
    meeting.add_parser("pending", help="your Pending queue").set_defaults(fn="meeting pending")
    s = meeting.add_parser("approve", help="share your Pending meetings")
    target = s.add_mutually_exclusive_group(required=True)
    target.add_argument("id", nargs="?")
    target.add_argument("--all", action="store_true")
    s.add_argument("--private", action="store_true", default=None)
    s.set_defaults(fn="meeting approve")
    for action in ("dismiss", "restore"):
        s = meeting.add_parser(action)
        s.add_argument("id")
        s.set_defaults(fn="meeting " + action)
    s = meeting.add_parser(
        "import", help="file a transcript (text, WebVTT, SRT or JSON segments) as one of your meetings"
    )
    s.add_argument("file", help="the transcript file; - reads standard input")
    s.add_argument("--title", help="default: the file's name")
    s.add_argument(
        "--date", help="when it started: 2026-09-28 or 2026-09-28T16:00 (this Mac's time zone) or with an offset"
    )
    s.add_argument("--participant", dest="participants", action="append", help="an email or a name; repeat for each")
    s.add_argument(
        "--source", default="upload", help="where it came from: zoom, granola, otter, fireflies... (default upload)"
    )
    s.add_argument("--external-id", help="that system's id for it: importing it again updates the meeting")
    s.add_argument("--format", choices=["auto", "text", "vtt", "srt", "json"], default="auto")
    s.add_argument("--notes-file", help="a Markdown file of notes or a summary")
    s.add_argument("--media-url", help="an https link to the recording, if there is one")
    s.add_argument("--send-to", help="a bot to hand the meeting to, as Send does")
    s.add_argument("--private", action="store_true", default=None)
    s.add_argument(
        "--review",
        choices=["pending", "live"],
        help="share immediately with live; otherwise your review setting applies",
    )
    s.set_defaults(fn="meeting import")

    message = sub.add_parser("message", help="send a message, read what waits for you, mark one read").add_subparsers(
        dest="sub"
    )
    s = message.add_parser("send", help="send a message to a bot or a human")
    s.add_argument("to")
    s.add_argument("text")
    s.add_argument(
        "--fyi", action="store_true", help="an fyi: it expects no reply, and takes no conversation or references"
    )
    s.add_argument("--conversation")
    s.add_argument("--ref", action="append")
    ways = delivery_flags(s, steer=False)
    ways.add_argument(
        "--steer",
        action="store_true",
        help="into the bot's running turn now; with --ref task:<id> you requested, into its owner's "
        "running turn on that task",
    )
    s.add_argument(
        "--visual",
        action="append",
        metavar="PATH",
        help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
        "already in Tico); repeat for more",
    )
    s.add_argument(
        "--title",
        action="append",
        dest="visual_title",
        metavar="TITLE",
        help="the caption for each --visual, in the same order",
    )
    s.add_argument(
        "--needs-action",
        dest="needs_action",
        action="store_true",
        help="a bot's message to a bot waits for that bot's next run; this wakes it now",
    )
    s.set_defaults(fn="message send")
    message.add_parser("list", help="the messages and fyis waiting for you").set_defaults(fn="message list")
    s = message.add_parser(
        "mark-read", help="mark a message from your inbox as read (an external agent's own delivery)"
    )
    s.add_argument("message_id")
    s.set_defaults(fn="message mark-read")
    s = message.add_parser("redact", help="replace a secret (read from stdin) in a message with a mark")
    s.add_argument("message_id")
    s.add_argument("--label", help="what it was saved as")
    s.set_defaults(fn="message redact")

    s = (
        sub.add_parser("conversation", help="what was said in a conversation")
        .add_subparsers(dest="sub")
        .add_parser("show")
    )
    s.add_argument("conversation")
    s.add_argument("--before", help="the page before this message id: a page's next_before")
    s.add_argument("--since", help="only messages sent after this time (ISO 8601)")
    s.set_defaults(fn="conversation show")

    thread = sub.add_parser(
        "thread", help="threads: list yours, read one, post, reply in a sub-thread, follow, search, catch up"
    ).add_subparsers(dest="sub")
    s = thread.add_parser("catchup", help="summarize what is new in a thread into your Assistant DM")
    s.add_argument("thread_id")
    s.add_argument(
        "--thread-only",
        dest="scope",
        action="store_const",
        const="thread",
        default="subtree",
        help="this thread only, not its sub-threads",
    )
    s.set_defaults(fn="thread catchup")
    s = thread.add_parser(
        "catch-up", help="each thread with something new for you, mentions first, with its new messages"
    )
    s.add_argument("--cursor", help="the page after this cursor: a page's next_cursor")
    s.add_argument("--limit", type=int, help="items in this page (default 20, at most 50)")
    s.set_defaults(fn="thread catch-up")
    s = thread.add_parser("pin", help="pin a thread for yourself: it stays at the top of your list")
    s.add_argument("thread")
    s.set_defaults(fn="thread pin")
    s = thread.add_parser("unpin", help="unpin a thread for yourself, even one pinned for everyone")
    s.add_argument("thread")
    s.set_defaults(fn="thread unpin")
    s = thread.add_parser("list", help="your threads: needs attention first, then unread, then recent; DMs apart")
    s.add_argument(
        "--with", dest="with", metavar="ACTOR", help="only the threads shared with this person, bot or group"
    )
    thread_page_flags(s)
    s.set_defaults(fn="thread list")
    s = thread.add_parser("show", help="one thread in tree order: each message, then the sub-threads under it")
    s.add_argument("thread")
    thread_page_flags(s)
    s.add_argument(
        "--flat",
        action="store_true",
        default=None,
        help="the thread and its open sub-threads as one list in time order",
    )
    s.set_defaults(fn="thread show")
    s = thread.add_parser("home", help="a teammate's Home thread, made on first use (needs Read on them)")
    s.add_argument("actor", help="bot:<slug>, human:<id> or group:<id>")
    s.set_defaults(fn="thread home")
    s = thread.add_parser("dm-group", help="a group DM with several people or bots, with an optional first message")
    s.add_argument("members", nargs="+", metavar="ACTOR", help="human:<id> or bot:<slug>; one or more")
    s.add_argument("--body", help="the first message")
    s.add_argument("--title", help="its name: needed for a new group DM with two or more others")
    s.set_defaults(fn="thread dm-group")
    s = thread.add_parser("dm", help="your DM with one person or bot: the open one, or a new one")
    s.add_argument("actor", help="human:<id> or bot:<slug>")
    s.set_defaults(fn="thread dm")
    s = thread.add_parser("create", help="start a thread: its audience decides who reads it")
    s.add_argument("title", nargs="?")
    s.add_argument(
        "--audience", choices=["members", "team", "company", "personal"], help="members (private) by default"
    )
    s.add_argument("--group", dest="audience_group_id", help="with --audience team: the group's id")
    s.add_argument(
        "--member",
        dest="members",
        action="append",
        metavar="ACTOR",
        help="a person, bot or group to add: human:<id>, bot:<slug> or group:<id>; repeat for more",
    )
    s.add_argument("--body", help="the first message")
    s.add_argument(
        "--pin-for-members", dest="pin_for_members", action="store_true", help="pin it for you and every person you add"
    )
    s.set_defaults(fn="thread create")
    s = thread.add_parser("post", help="post a message to a thread")
    s.add_argument("thread")
    s.add_argument("text")
    s.add_argument("--to", help="the person or bot it is addressed to: human:<id> or bot:<slug>")
    s.add_argument(
        "--reply-to",
        dest="reply_to",
        metavar="MESSAGE",
        help="the message this answers: yours stays in the thread, quoting it",
    )
    s.add_argument(
        "--ref", dest="refs", action="append", metavar="KIND:ID", help="a reference such as task:<id>; repeat for more"
    )
    s.set_defaults(fn="thread post")
    s = thread.add_parser("bot-status", help="the bots a thread waits on: working, up next or can't reply, and why")
    s.add_argument("thread")
    s.set_defaults(fn="thread bot-status")
    s = thread.add_parser(
        "reply", help="a side topic in a message's sub-thread; to answer a message use post --reply-to"
    )
    s.add_argument("thread")
    s.add_argument("message", help="the message the reply branches from")
    s.add_argument("text")
    s.add_argument(
        "--ref", dest="refs", action="append", metavar="KIND:ID", help="a reference such as task:<id>; repeat for more"
    )
    s.set_defaults(fn="thread reply")
    s = thread.add_parser("edit", help="edit a message you wrote: its text becomes a new revision")
    s.add_argument("message")
    s.add_argument("text")
    s.set_defaults(fn="thread edit")
    s = thread.add_parser("sub", help="open a message's sub-thread without posting; it is made if it does not exist")
    s.add_argument("message")
    s.set_defaults(fn="thread sub")
    s = thread.add_parser("react", help="add your reaction to a message, such as :+1:")
    s.add_argument("message_id")
    s.add_argument("emoji", help="an emoji, or its shortcode such as :+1:")
    s.set_defaults(fn="thread react")
    s = thread.add_parser("unreact", help="remove your reaction from a message")
    s.add_argument("message_id")
    s.add_argument("emoji", help="an emoji, or its shortcode such as :+1:")
    s.set_defaults(fn="thread unreact")
    s = thread.add_parser(
        "open-earlier", help="open a bot's earlier messages to the people who read its Home (a manager of the bot)"
    )
    s.add_argument("thread_id")
    s.set_defaults(fn="thread open-earlier")
    s = thread.add_parser("task", help="make a task from a message: private unless the thread is company-wide")
    s.add_argument("message_id")
    s.add_argument("--title", required=True)
    s.add_argument("--owner", required=True, help="a bot slug, a person id, or me")
    s.set_defaults(fn="thread task")
    s = thread.add_parser("delete", help="delete a message you wrote: a marker stays")
    s.add_argument("message_id")
    s.set_defaults(fn="thread delete")
    s = thread.add_parser("upload", help="post a message with a local file to a thread: the file is streamed to Tico")
    s.add_argument("thread")
    s.add_argument("path", help="a local file on this computer")
    s.add_argument("--text", help="the message's text (optional: the file can come alone)")
    s.add_argument("--to", help="the person or bot it is addressed to: human:<id> or bot:<slug>")
    s.add_argument(
        "--under", metavar="MESSAGE", help="post the file in that message's sub-thread instead of the thread"
    )
    s.set_defaults(fn="thread upload")
    s = thread.add_parser(
        "slack-resume", help="check a suspended or refused Slack link again (admins); its id is in link-slack's listing"
    )
    s.add_argument("link_id")
    s.set_defaults(fn="thread slack-resume")
    s = thread.add_parser("follow", help="follow a thread: live, mentions, digest or muted")
    s.add_argument("thread")
    s.add_argument("--notify", choices=["live", "mentions", "digest", "muted"], help="live by default")
    s.set_defaults(fn="thread follow")
    s = thread.add_parser("unfollow", help="stop following a thread; you keep what you can read")
    s.add_argument("thread")
    s.set_defaults(fn="thread unfollow")
    s = thread.add_parser("read", help="mark a thread read to a message's seq, or all of it")
    s.add_argument("thread")
    s.add_argument("--seq", type=int, help="the seq of the last message you read")
    s.add_argument("--all", action="store_true", help="read the thread and every sub-thread under it")
    s.set_defaults(fn="thread read")
    s = thread.add_parser("search", help="search the threads you can read")
    s.add_argument("q")
    thread_page_flags(s)
    s.set_defaults(fn="thread search")
    members = thread.add_parser("members", help="who can read a thread: list, add, remove").add_subparsers(
        dest="subsub"
    )
    s = members.add_parser("list", help="the people, bots and groups in a thread, with their roles")
    s.add_argument("thread")
    thread_page_flags(s)
    s.set_defaults(fn="thread members list")
    s = members.add_parser("add", help="add a person, bot or group so it can read the thread")
    s.add_argument("thread")
    s.add_argument("actor", help="human:<id>, bot:<slug> or group:<id>")
    s.add_argument("--owner", dest="role", action="store_const", const="owner", help="as an owner: may remove members")
    s.set_defaults(fn="thread members add")
    s = members.add_parser(
        "remove", help="remove a person, bot or group: they stop reading the thread (owner or admin)"
    )
    s.add_argument("thread")
    s.add_argument("actor")
    s.set_defaults(fn="thread members remove")
    s = thread.add_parser("update", help="rename, archive or unarchive a thread, or change who may read it")
    s.add_argument("thread")
    s.add_argument("--title")
    s.add_argument("--archive", dest="archived", action="store_const", const=True)
    s.add_argument("--unarchive", dest="archived", action="store_const", const=False)
    s.add_argument(
        "--audience",
        choices=["members", "team", "company"],
        help="team or company makes it public; only its starter or an admin can",
    )
    s.add_argument("--group", dest="audience_group_id", help="with --audience team: the group's id")
    s.add_argument(
        "--keep-days",
        dest="retention_days",
        type=int,
        help="delete its messages after this many days, sub-threads included (a root thread's owner or admin)",
    )
    s.add_argument("--keep-forever", dest="keep_forever", action="store_true", help="keep its messages for good")
    s.add_argument(
        "--pin-for-everyone",
        dest="pinned_for_everyone",
        action="store_const",
        const=True,
        help="pin it for everyone who reads it (its starter or an admin)",
    )
    s.add_argument(
        "--unpin-for-everyone",
        dest="pinned_for_everyone",
        action="store_const",
        const=False,
        help="take its pin for everyone off",
    )
    s.set_defaults(fn="thread update")
    s = thread.add_parser(
        "link-slack", help="link a thread to a Slack channel (admins), list its links, or --off to take them away"
    )
    s.add_argument("thread", help="the thread's id")
    s.add_argument("channel", nargs="?", help="#name or id (C0123456789); left out, the thread's links are listed")
    s.add_argument("--history", action="store_true", help="also bring in the channel's earlier messages")
    s.add_argument("--off", action="store_true", help="take the thread's Slack link away")
    s.set_defaults(fn="thread link-slack")

    note = sub.add_parser(
        "note", help="a deferred message for a bot: no run now, its next run reads it"
    ).add_subparsers(dest="sub")
    s = note.add_parser("create", help="leave a bot a deferred message: no run now, its next run reads it")
    s.add_argument("to")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--text-file", dest="text_file")
    s.add_argument(
        "--defer",
        dest="defer_for",
        type=defer_for,
        metavar="30m|4h|2d|never",
        help="start a run by this time if none carried it (never when omitted)",
    )
    s.set_defaults(fn="note create")
    s = note.add_parser("list", help="deferred messages (notes), newest first: left for you and by you")
    s.add_argument("--to")
    s.add_argument("--from", dest="sender")
    s.add_argument("--since", help="an ISO time, or 24h / 3d")
    s.add_argument("--waiting", action="store_true", help="only notes no run has carried yet")
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(fn="note list")
    s = note.add_parser("delete", help="take back a note no run has carried yet")
    s.add_argument("id")
    s.set_defaults(fn="note delete")

    feedback = sub.add_parser(
        "feedback", help="a person's 👍/👎 on a bot's work: give, list, make a rule"
    ).add_subparsers(dest="sub")
    subjects = ("message", "task", "approval")
    s = feedback.add_parser("give", help="rate a message, task or approval a bot made; again replaces it")
    s.add_argument("subject_type", choices=subjects)
    s.add_argument("subject_id")
    s.add_argument("rating", choices=("up", "down"))
    s.add_argument("--reason")
    s.add_argument("--tell-bot", dest="tell_bot", action="store_true", help="a 👎 only: a note for the bot's next run")
    s.set_defaults(fn="feedback give")
    s = feedback.add_parser("remove", help="take back your feedback, and its note if no run carried it")
    s.add_argument("id")
    s.set_defaults(fn="feedback remove")
    s = feedback.add_parser("markers", help="your feedback on these subjects and everyone's up/down counts")
    s.add_argument("subject_type", choices=subjects)
    s.add_argument("subject_ids", nargs="+")
    s.set_defaults(fn="feedback markers")
    s = feedback.add_parser("list", help="feedback newest first, a page at a time")
    s.add_argument("--bot", dest="bot_id")
    s.add_argument("--rating", choices=("up", "down"))
    s.add_argument("--since", help="an ISO time, or 24h / 7d")
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="feedback list")
    feedback.add_parser("reasons", help="your five latest distinct reasons").set_defaults(fn="feedback reasons")
    s = feedback.add_parser("rule-draft", help="a one-sentence rule drafted from your 👎; stores nothing")
    s.add_argument("id")
    s.set_defaults(fn="feedback rule-draft")
    s = feedback.add_parser("rule", help="your 👎 as an org rule: active if you manage the bot, else suggested")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.set_defaults(fn="feedback rule")
