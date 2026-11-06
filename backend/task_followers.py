"""Who follows a task: the follower rows (`thread_followers`, following=1) of the task's own thread.

The owner follows by default, at `live` (reason `owner`). Filing a task follows only with `follow` (reason
`manual`), and posting in a task's thread follows nobody (threads.post). Changing the owner makes the new owner
follow; the old owner's row goes unless they chose to follow (reason `manual`) or were @mentioned. A row that goes
keeps its read mark: it is a read mark only (following=0).

Only someone who can read the task follows it. Anyone who reads it follows or unfollows themselves and sets their own
level; whoever may change the task (its parties, their managers, a task mover) adds, removes or sets anyone else's,
a bot only for teammates in its reach. A task with no thread of its own (one filed into a shared chat) has no
followers.
"""

from . import hubdb as H

LEVELS = ("live", "mentions", "digest", "muted")
OWNER = "owner"      # the row the task's ownership made
CHOSEN = "manual"    # a follow someone chose (the reason threads.follow writes too)
KEPT = (CHOSEN, "mentioned")   # rows an owner change leaves: a choice, or a mention


def thread_id(conn, row):
    """The task's own thread, or None for a task that lives in a shared chat (or a database from before threads)."""
    if not row or not row.get("conversation_id") or not H._has_table(conn, "thread_followers"):
        return None
    conv = H.conversation(conn, row["conversation_id"])
    return conv["id"] if conv and conv.get("task_id") == row["id"] else None


def _upsert(conn, tid, actor, reason, notify=None):
    """`actor` follows thread `tid`. A row that was only a read mark (or unfollowed) takes the new reason; one already
    following keeps its own unless the new reason is a choice. `notify` sets the level; None keeps a follower's and
    starts anyone else at `live`. The row id is SQLite's, so seeding a row draws no uuid4 (the demo's ids are a counter
    of those: backend/demo.py)."""
    now = H.now()
    conn.execute(
        "INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,updated_at) "
        "VALUES(lower(hex(randomblob(16))),?,?,1,?,?,0,?,?) ON CONFLICT(thread_id,actor) DO UPDATE SET "
        "reason=CASE WHEN following=0 OR excluded.reason=? THEN excluded.reason ELSE reason END, following=1, "
        "notify=COALESCE(?,CASE WHEN following=0 THEN 'live' ELSE notify END), updated_at=excluded.updated_at",
        (tid, actor, notify or "live", reason, now, now, CHOSEN, notify))


def seed_owner(conn, row, owner=None):
    """The owner follows the task's thread (a new task, or a new owner)."""
    owner = owner or row.get("owner_id")
    tid = thread_id(conn, row)
    if tid and str(owner or "").startswith(("human:", "bot:")):
        _upsert(conn, tid, owner, OWNER)


def owner_moved(conn, row, old, new):
    """The new owner follows; the old owner's row goes unless they chose to follow or were @mentioned."""
    if old == new:
        return
    seed_owner(conn, row, new)
    tid = thread_id(conn, row)
    if tid and old:
        conn.execute(f"UPDATE thread_followers SET following=0, reason='read', updated_at=? WHERE thread_id=? AND "
                     f"actor=? AND following=1 AND reason NOT IN ({','.join('?' * len(KEPT))})",
                     (H.now(), tid, old, *KEPT))


def follow_on_create(conn, row, actor):
    """The filer's own choice to follow the task they filed."""
    tid = thread_id(conn, row)
    if tid and str(actor or "").startswith(("human:", "bot:")):
        _upsert(conn, tid, actor, CHOSEN)


def listed(conn, row):
    """[{actor, notify, reason}] who follows the task, first follow first. On a private task, only those who still
    see it: someone taken off Who can see (or a past owner) keeps a row but is no follower."""
    tid = thread_id(conn, row)
    if not tid:
        return []
    rows = [{"actor": r[0], "notify": r[1], "reason": r[2]} for r in conn.execute(
        "SELECT actor, notify, reason FROM thread_followers WHERE thread_id=? AND following=1 AND "
        "(actor LIKE 'human:%' OR actor LIKE 'bot:%') ORDER BY created_at, actor", (tid,))]
    if H.task_private(conn, row):
        rows = [r for r in rows if H.task_private_readable(conn, r["actor"], row)]
    return rows


def change(conn, actor, task_id, target=None, *, following=True, notify=None, mover=None, check=None):
    """Follow (`following`) or unfollow task `task_id` for `target` (default: `actor`), at level `notify` (None: as it
    is, `live` for a new follower). `check(target)` may refuse a target someone else adds (reach.check). Returns the
    task row."""
    H._writer(conn, actor, task_id=task_id)
    row = H.task(conn, task_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no task {task_id}")
    H._task_private_writer(conn, actor, row)
    if notify is not None and notify not in LEVELS:
        H.refuse(conn, actor, "notify", "A level is live, mentions, digest or muted")
    who = actor if target in (None, "", "me") else H.resolve_actor(conn, target)
    if not who or not (H.is_human(who) and H.human(conn, H.actor_id(who)) or H.is_bot(who) and H.bot(conn, H.actor_id(who))):
        H.refuse(conn, actor, "not-found", f"{target} is not a person or bot on the team")
    tid = thread_id(conn, row)
    if not tid:
        H.refuse(conn, actor, "no-thread", "This task lives in a chat, not a thread of its own: follow the chat")
    if who != actor:
        if not H.may_change_readers(conn, actor, row, mover):
            H.refuse(conn, actor, "identity", "Only someone who may change the task changes who else follows it")
        if following and not H.task_private_readable(conn, who, row):
            H.refuse(conn, actor, "private", f"{H.actor_name(conn, who)} cannot see this task: add them under Who can "
                                             "see first")
        if following and check:
            check(who)
    had = conn.execute("SELECT following, notify FROM thread_followers WHERE thread_id=? AND actor=?",
                       (tid, who)).fetchone()
    if following:
        # Your own follow, or a new follower someone added, is a choice; an editor's level change keeps the reason.
        if who == actor or not (had and had[0]):
            _upsert(conn, tid, who, CHOSEN, notify)
        elif notify:
            conn.execute("UPDATE thread_followers SET notify=?, updated_at=? WHERE thread_id=? AND actor=?",
                         (notify, H.now(), tid, who))
    elif had and had[0]:
        conn.execute("UPDATE thread_followers SET following=0, reason='read', updated_at=? WHERE thread_id=? AND actor=?",
                     (H.now(), tid, who))
    after = conn.execute("SELECT notify FROM thread_followers WHERE thread_id=? AND actor=? AND following=1",
                         (tid, who)).fetchone()
    if bool(had and had[0]) != bool(after) or (had and after and had[1] != after[0]):
        H.event(conn, actor, "task.followers", task_id, {"actor": who, "following": bool(after),
                                                         "notify": after[0] if after else None})
    return row

