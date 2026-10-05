"""The task change feed: every change to a task, in order, for a board that stays open.

A board that re-reads every open task to notice one move is late by its polling interval and
reads hundreds of tasks to find nothing. Here the database keeps a numbered row per change to a
task (triggers on `tasks` and `task_links`, so no write path can forget one), and a client
holding `GET /api/v2/task-changes/watch` is sent each changed task, as the list would show it to
that client, within about half a second. `after` resumes from the last number it saw, so a
reconnect misses nothing; a client further behind than the feed keeps is told to read in full.

The write's own identity is stamped on its rows in the same transaction (`claim`), so a board
can say who moved a task. Writes outside a request (the scheduler, a migration) stay unsigned.
"""

import asyncio
import sqlite3

from fastapi import Request
from fastapi.responses import StreamingResponse

from .store import H, Problem, encode

KEEP_HOURS = 24          # a client away longer than this reads in full
POLL_SECONDS = 0.5
LIFETIME_SECONDS = 300   # bounded, so a revoked sign-in stops being served
KEEPALIVE_SECONDS = 15   # under the proxies' idle timeouts
BATCH = 500

_STAMP = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS task_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
    "actor TEXT, ts TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS task_changes_ts ON task_changes(ts)",
] + [
    f"CREATE TRIGGER IF NOT EXISTS task_changes_{name} AFTER {event} ON {table} BEGIN "
    f"INSERT INTO task_changes(task_id, ts) VALUES({row}, {_STAMP}); END"
    for name, event, table, row in (
        ("insert", "INSERT", "tasks", "NEW.id"), ("update", "UPDATE", "tasks", "NEW.id"),
        ("delete", "DELETE", "tasks", "OLD.id"), ("link_insert", "INSERT", "task_links", "NEW.task_id"),
        ("link_update", "UPDATE", "task_links", "NEW.task_id"), ("link_delete", "DELETE", "task_links", "OLD.task_id"))
]


def ensure(c):
    """Idempotent, so it takes no migration number another branch could need. One statement at a
    time: executescript would commit the transaction it is called in."""
    for statement in SCHEMA:
        c.execute(statement)


def mark(c):
    """The last change number before a write; None where the feed is not installed."""
    try:
        return c.execute("SELECT COALESCE(MAX(seq),0) FROM task_changes").fetchone()[0]
    except sqlite3.OperationalError:
        return None


def claim(c, since, actor):
    """Sign the changes a write made with who made it. The write holds the database's write
    lock from `mark` to here, so every row after `since` is its own."""
    if since is not None and actor:
        c.execute("UPDATE task_changes SET actor=? WHERE seq>? AND actor IS NULL", (str(actor), since))


def latest(c):
    return c.execute("SELECT COALESCE(MAX(seq),0) FROM task_changes").fetchone()[0]


def oldest(c):
    row = c.execute("SELECT MIN(seq) FROM task_changes").fetchone()
    return row[0] if row and row[0] is not None else None


def sweep(store, now=None, batch=5000):
    """Drop changes older than the feed keeps, in short transactions."""
    cutoff = H.shift(now or H.now(), hours=-KEEP_HOURS)
    deleted = 0
    while True:
        with store.transaction() as c:
            removed = c.execute("DELETE FROM task_changes WHERE seq IN "
                                "(SELECT seq FROM task_changes WHERE ts<? LIMIT ?)", (cutoff, batch)).rowcount
        deleted += removed
        if removed < batch:
            return deleted


def _trashed_visible(c, who, task_id):
    """A deleted task is reported gone only to someone who could have seen it."""
    try:
        row = c.execute("SELECT owner, requester, private FROM task_trash WHERE task_id=? AND purged_at IS NULL",
                        (task_id,)).fetchone()
    except sqlite3.OperationalError:
        return False
    if not row:
        return False
    return who.actor in (row["owner"], row["requester"]) or not row["private"]


def changes(c, auth, who, task_views, after, limit=BATCH):
    """What changed after `after`, one entry per task with its latest state: `{seq, id, actor,
    at, task}`, where `task` is the task as the list shows it to `who`, or `gone: true` for a
    task deleted since. A task `who` may not see is left out. Returns (entries, cursor, reset,
    more): `more` when the batch was full and further changes wait."""
    first = oldest(c)
    if first is not None and after < first - 1:
        return [], latest(c), True, False
    rows = c.execute("SELECT seq, task_id, actor, ts FROM task_changes WHERE seq>? ORDER BY seq LIMIT ?",
                     (after, limit)).fetchall()
    if not rows:
        return [], after, False, False
    cursor = rows[-1]["seq"]
    last = {}
    for row in rows:
        last[row["task_id"]] = row
    ids = list(last)
    visible_sql = auth.task_sql(c, who)
    marks = ",".join("?" * len(ids))
    found = H._rows(c.execute(f"SELECT * FROM tasks WHERE id IN ({marks}) AND ({visible_sql})", ids))
    views = {view["id"]: view for view in task_views(found, c, who, visible_sql)}
    present = {r[0] for r in c.execute(f"SELECT id FROM tasks WHERE id IN ({marks})", ids)}
    # A bot whose activity `who` may not read is not named as the one who changed a task.
    hidden = {"bot:" + slug for slug in auth.unreadable_bots(c, who)}
    out = []
    for task_id, row in sorted(last.items(), key=lambda item: item[1]["seq"]):
        actor = None if row["actor"] in hidden else row["actor"]
        entry = {"seq": row["seq"], "id": task_id, "actor": actor, "at": row["ts"]}
        if task_id in views:
            entry["task"] = views[task_id]
        elif task_id not in present and _trashed_visible(c, who, task_id):
            entry["gone"] = True
        else:
            continue
        out.append(entry)
    return out, cursor, False, len(rows) >= limit


def install(app, store, auth, task_views):
    def people_only(who):
        auth.domain(who)
        if who.role not in ("human", "owner"):
            raise Problem("forbidden", "The task feed is for people's boards", 403)

    @app.get("/api/v2/task-changes")
    def task_changes(request: Request, after: int | None = None):
        """One read of the feed: for a client that cannot hold a stream, and to catch up. Without
        `after`, only where the feed stands."""
        who = request.state.identity
        people_only(who)
        with store.read() as c:
            if after is None or after < 0:
                return {"changes": [], "seq": latest(c), "reset": False}
            entries, cursor, reset, more = changes(c, auth, who, task_views, after)
            return {"changes": entries, "seq": cursor, "reset": reset, "more": more}

    @app.get("/api/v2/task-changes/watch")
    async def watch_task_changes(request: Request, after: int | None = None):
        who = request.state.identity
        people_only(who)

        def start():
            with store.read() as c:
                return latest(c)

        def poll(cursor):
            auth.authenticate(request.headers)
            with store.read() as c:
                return changes(c, auth, who, task_views, cursor)

        now_seq = await asyncio.to_thread(start)
        cursor = after if after is not None and after >= 0 else now_seq

        async def generate():
            nonlocal cursor
            # `ready` says where the feed stands; a client that asked from further back than it
            # keeps gets `reset` first and reads its board in full.
            yield f"event: ready\ndata: {encode({'seq': now_seq})}\n\n"
            quiet = 0.0
            for _ in range(int(LIFETIME_SECONDS / POLL_SECONDS)):
                if await request.is_disconnected():
                    return
                try:
                    entries, next_cursor, reset, more = await asyncio.to_thread(poll, cursor)
                except Problem:
                    yield "event: expired\ndata: {}\n\n"
                    return
                if reset:
                    cursor = next_cursor
                    yield f"event: reset\ndata: {encode({'seq': cursor})}\n\n"
                    quiet = 0.0
                elif next_cursor != cursor:
                    cursor = next_cursor
                    for entry in entries:
                        yield f"id: {entry['seq']}\nevent: task\ndata: {encode(entry)}\n\n"
                    # The cursor moves past changes this client may not see, so it resumes there.
                    yield f"id: {cursor}\nevent: seq\ndata: {encode({'seq': cursor})}\n\n"
                    quiet = 0.0
                elif quiet >= KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    quiet = 0.0
                # A full batch means more is waiting: read it straight away.
                if not more:
                    await asyncio.sleep(POLL_SECONDS)
                    quiet += POLL_SECONDS

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
