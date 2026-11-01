"""The `threads` topic of the live stream, and the thread fields of `messages` events (docs/dev/threads-contract.md §9).

events.read() calls these with its own per-read _Viewer, so every answer is C1 for that stream's viewer: a thread the
viewer may not read sends nothing, and a reader's own read mark goes only to that reader. A `counters` change carries
the thread's two counters, not a count per viewer (that would be one COUNT per viewer per message in a busy thread):
a client's unread is last_unread_seq past its own mark, and GET /api/v2/threads/{id} gives the exact count.
"""

from . import threads

KINDS = ("created", "sub_thread", "renamed", "archived", "unarchived", "audience", "members", "counters", "read")


def message_fields(conversation, message):
    """The thread fields a `messages` event carries beside the message (the contract's §9 shape)."""
    thread_id = message["conversation_id"]
    return {"thread_id": thread_id, "root_id": (conversation or {}).get("root_id") or thread_id,
            "message_seq": message.get("seq"), "revision": int(message.get("revision") or 0)}


def threads_entries(view, rows):
    """(row, entry) for each `threads` change this viewer may read, in the order given."""
    out = []
    thread_viewer = None
    for row in rows:
        thread_id = row["conversation_id"] or row["subject_id"]
        kind = row["kind"]
        if kind not in KINDS:
            continue
        if kind == "read" and row["ref"] != view.principal:
            continue                                  # another reader's mark: their device, not this one
        conv = view.conversation(thread_id)
        if not conv:
            continue
        root = conv.get("root_id") or thread_id
        if kind == "counters":
            entry = {"thread_id": thread_id, "root_id": root, "kind": kind,
                     "counters": {"last_seq": int(conv.get("last_seq") or 0),
                                  "last_unread_seq": int(conv.get("last_unread_seq") or 0)}}
        elif kind == "read":
            mark = view.c.execute("SELECT last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?",
                                  (thread_id, view.principal)).fetchone()
            entry = {"thread_id": thread_id, "root_id": root, "kind": kind,
                     "read": {"last_read_seq": int(mark[0]) if mark else 0}}
        else:
            if thread_viewer is None:
                thread_viewer = threads.viewer(view.auth, view.who)
            entry = {"thread_id": thread_id, "root_id": root, "kind": kind,
                     "thread": threads.view(view.c, thread_viewer, conv)}
        out.append((row, entry))
    return out
