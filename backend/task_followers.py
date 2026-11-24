"""A task's own thread. Nobody follows a task: its comments are a log, and a comment reaches only whom it addresses
(its @mentions, the author a Reply answers, the agent a session message is written to; threads.plan). The thread's
`thread_followers` rows stay as read marks only.

Until the upgrade floor passes the release that stopped following, the old follower API answers as if nobody
follows: `follow` on create is accepted and ignored, GET .../followers lists nobody, and follow or unfollow answers and
does nothing (backend/task_followers_routes.py).
"""

from . import hubdb as H


def thread_id(conn, row):
    """The task's own thread, or None for a task that lives in a shared chat (or a database from before threads)."""
    if not row or not row.get("conversation_id") or not H._has_table(conn, "thread_followers"):
        return None
    conv = H.conversation(conn, row["conversation_id"])
    return conv["id"] if conv and conv.get("task_id") == row["id"] else None
