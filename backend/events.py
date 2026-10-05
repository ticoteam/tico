"""Live events: one numbered change log and one stream of it for people's pages.

A page that re-reads a list to notice one change is late by its polling interval and reads
everything to find nothing. Here the database keeps a numbered row per change (`changes`),
written by triggers so no write path can forget one, and `GET /api/v2/events` streams them to a
signed-in person as they commit, filtered to what that person may read:

- `tasks`: a task as the list shows it to the viewer, or `gone` for one deleted.
- `messages`: a message as a conversation page shows it, a deleted one, or a chat goal change.
- `runs`: a bot run's output step, or its state moving (queued, started, finished).
- `bots`: a bot's status line, as `GET /api/v2/status` shows it.
- `needs`: the viewer's own Needs-you list, whole, when anything on it may have moved.

`after` (or the browser's `Last-Event-ID`) resumes from the last change number seen, so a
reconnect misses nothing; a client further behind than the log keeps (24 h) is sent `reset` and
reads its page in full.

Delivery has no per-client polling. `Store.mutate` and `Store.write` ring an in-process doorbell
after a write that logged changes commits; one shared reader per event loop also looks for
changes every couple of seconds, for writes from other processes (the Slack gateway, the
scheduler) and from code that writes outside those two. On a ring, each open stream reads the
rows after its own cursor in one query on a worker thread and filters them for its viewer.

The write's own identity is stamped on its rows in the same transaction (`claim`), so a page
can say who changed something. Writes outside a request (the scheduler, a migration) stay
unsigned.
"""

import asyncio
import sqlite3
import threading
import time

from fastapi import Request
from fastapi.responses import StreamingResponse

from .store import H, Problem, encode

TOPICS = ("tasks", "messages", "runs", "bots", "needs")
KEEP_HOURS = 24           # a client away longer than this reads in full
FALLBACK_SECONDS = 1.5    # the shared look for changes another process wrote
LIFETIME_SECONDS = 300    # bounded, so a revoked sign-in stops being served
KEEPALIVE_SECONDS = 15    # under the proxies' idle timeouts
GATHER_SECONDS = 0.05     # a burst of writes (a run's output) goes out as one read
BATCH = 500
MAX_FILTER = 20           # conversation= and bot= values per stream

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
TABLE = [
    "CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, "
    "kind TEXT, subject_id TEXT, ref TEXT, conversation_id TEXT, bot TEXT, actor TEXT, at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS changes_at ON changes(at)",
]


def _row(topic, kind, subject, ref="NULL", conversation="NULL", bot="NULL", where=None):
    return (f"INSERT INTO changes(topic,kind,subject_id,ref,conversation_id,bot,at) "
            f"SELECT '{topic}','{kind}',{subject},{ref},{conversation},{bot},{_NOW}"
            + (f" WHERE {where}" if where else "") + ";")


def _needs(kind, *actors, where=None):
    """A `needs` row for each person among `actors`: their Needs-you list may have moved."""
    people = " UNION ".join(f"SELECT {actor} AS who" for actor in actors)
    return (f"INSERT INTO changes(topic,kind,subject_id,at) SELECT 'needs','{kind}',who,{_NOW} "
            f"FROM ({people}) WHERE who LIKE 'human:%'" + (f" AND {where}" if where else "") + ";")


def _changed(*columns):
    return " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in columns)


_RUN_CONVERSATION = ("(SELECT m.conversation_id FROM attempts a JOIN jobs j ON j.id=a.job_id "
                     "JOIN messages m ON m.id=j.message_id WHERE a.id={aid})")
_PARTIES = ("owner", "requester", "waiting_on")

# (trigger name, event, table, WHEN or None, body). One list, so a new source is one more line
# (task_relations, when it lands, is a `tasks` row per end). A table that does not exist is
# skipped, and a trigger whose definition changed is replaced at the next start.
TRIGGERS = [
    ("changes_tasks_insert", "INSERT", "tasks", None,
     _row("tasks", "insert", "NEW.id") + _needs("task", *("NEW." + p for p in _PARTIES))),
    ("changes_tasks_update", "UPDATE", "tasks", None,
     _row("tasks", "update", "NEW.id") + _needs("task", *("OLD." + p for p in _PARTIES), *("NEW." + p for p in _PARTIES))),
    ("changes_tasks_delete", "DELETE", "tasks", None,
     _row("tasks", "delete", "OLD.id") + _needs("task", *("OLD." + p for p in _PARTIES))),
    ("changes_task_links_insert", "INSERT", "task_links", None, _row("tasks", "link", "NEW.task_id")),
    ("changes_task_links_update", "UPDATE", "task_links", None, _row("tasks", "link", "NEW.task_id")),
    ("changes_task_links_delete", "DELETE", "task_links", None, _row("tasks", "link", "OLD.task_id")),
    ("changes_messages_insert", "INSERT", "messages", None,
     _row("messages", "insert", "NEW.id", conversation="NEW.conversation_id")
     + _needs("ask", "NEW.to_actor", "NEW.from_actor", where="NEW.kind IN ('ask','answer')")),
    # Delivery and read receipts move on every message a bot takes; they are not a change to show.
    ("changes_messages_update", "UPDATE", "messages",
     _changed("body", "refs_json", "kind", "answered_by", "edited_at", "deleted_at", "conversation_id"),
     _row("messages", "update", "NEW.id", conversation="NEW.conversation_id")
     + _needs("ask", "NEW.to_actor", where="NEW.kind='ask'")),
    ("changes_messages_delete", "DELETE", "messages", None,
     _row("messages", "delete", "OLD.id", conversation="OLD.conversation_id")),
    ("changes_chat_goals_insert", "INSERT", "chat_goals", None,
     _row("messages", "goal", "NEW.id", conversation="NEW.conversation_id", bot="NEW.bot")),
    ("changes_chat_goals_update", "UPDATE", "chat_goals", None,
     _row("messages", "goal", "NEW.id", conversation="NEW.conversation_id", bot="NEW.bot")),
    ("changes_attempt_events_insert", "INSERT", "attempt_events", None,
     _row("runs", "output", "NEW.attempt_id", ref="NEW.id",
          conversation=_RUN_CONVERSATION.format(aid="NEW.attempt_id"),
          bot="(SELECT bot FROM attempts WHERE id=NEW.attempt_id)")),
    ("changes_attempts_insert", "INSERT", "attempts", None,
     _row("runs", "attempt", "NEW.id", conversation=_RUN_CONVERSATION.format(aid="NEW.id"), bot="NEW.bot")),
    # A lease renewed is not a change; a run starting, finishing or failing is.
    ("changes_attempts_update", "UPDATE", "attempts", _changed("state"),
     _row("runs", "attempt", "NEW.id", conversation=_RUN_CONVERSATION.format(aid="NEW.id"), bot="NEW.bot")),
    ("changes_jobs_insert", "INSERT", "jobs", None,
     _row("runs", "job", "NEW.attempt_id", ref="NEW.message_id",
          conversation="(SELECT conversation_id FROM messages WHERE id=NEW.message_id)", bot="NEW.bot")),
    ("changes_jobs_update", "UPDATE", "jobs", _changed("state", "attempt_id"),
     _row("runs", "job", "NEW.attempt_id", ref="NEW.message_id",
          conversation="(SELECT conversation_id FROM messages WHERE id=NEW.message_id)", bot="NEW.bot")),
    ("changes_bot_status_insert", "INSERT", "bot_status", None, _row("bots", "status", "NEW.bot", bot="NEW.bot")),
    ("changes_bot_status_update", "UPDATE", "bot_status",
     _changed("state", "focus", "task_id", "since", "last_result", "next_due", "open_tasks", "needs_human"),
     _row("bots", "status", "NEW.bot", bot="NEW.bot")),
    ("changes_bots_update", "UPDATE", "bots", _changed("state"), _row("bots", "state", "NEW.slug", bot="NEW.slug")),
    ("changes_bot_control_insert", "INSERT", "bot_control", None, _row("bots", "control", "NEW.bot", bot="NEW.bot")),
    ("changes_bot_control_update", "UPDATE", "bot_control", None, _row("bots", "control", "NEW.bot", bot="NEW.bot")),
    ("changes_bot_control_delete", "DELETE", "bot_control", None, _row("bots", "control", "OLD.bot", bot="OLD.bot")),
    # An approval is on the Needs-you list of the person its message went to.
    ("changes_approvals_insert", "INSERT", "approvals", None,
     _needs("approval", "(SELECT to_actor FROM messages WHERE id=NEW.message_id)")),
    ("changes_approvals_update", "UPDATE", "approvals", _changed("decision", "consumed_at"),
     _needs("approval", "(SELECT to_actor FROM messages WHERE id=NEW.message_id)")),
]
# #118's first cut, replaced by `changes`.
_RETIRED = ["task_changes_insert", "task_changes_update", "task_changes_delete",
            "task_changes_link_insert", "task_changes_link_update", "task_changes_link_delete"]


def _trigger_sql(name, event, table, when, body):
    return (f"CREATE TRIGGER {name} AFTER {event} ON {table}" + (f" WHEN {when}" if when else "")
            + f" BEGIN {body} END")


def ensure(c):
    """Idempotent, so it takes no migration number another branch could need. One statement at a
    time: executescript would commit the transaction it is called in."""
    for statement in TABLE:
        c.execute(statement)
    for name in _RETIRED:
        c.execute(f"DROP TRIGGER IF EXISTS {name}")
    c.execute("DROP TABLE IF EXISTS task_changes")
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    present = {row[0]: row[1] for row in c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                                                   "AND name LIKE 'changes\\_%' ESCAPE '\\'")}
    wanted = set()
    for name, event, table, when, body in TRIGGERS:
        if table not in tables:
            continue
        wanted.add(name)
        sql = _trigger_sql(name, event, table, when, body)
        if present.get(name) == sql:
            continue
        c.execute(f"DROP TRIGGER IF EXISTS {name}")
        c.execute(sql)
    for name in set(present) - wanted:
        c.execute(f"DROP TRIGGER IF EXISTS {name}")


def mark(c):
    """The last change number before a write; None where the log is not installed."""
    try:
        return c.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
    except sqlite3.OperationalError:
        return None


def claim(c, since, actor):
    """Sign the changes a write made with who made it, and return the newest change number (None
    when the write logged nothing). The write holds the database's write lock from `mark` to
    here, so every row after `since` is its own."""
    if since is None:
        return None
    if actor:
        c.execute("UPDATE changes SET actor=? WHERE seq>? AND actor IS NULL", (str(actor), since))
    newest = c.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
    return newest if newest > since else None


def latest(c):
    """The newest change number ever given, even after the sweep emptied the table."""
    try:
        row = c.execute("SELECT seq FROM sqlite_sequence WHERE name='changes'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] if row else 0


def oldest(c):
    row = c.execute("SELECT MIN(seq) FROM changes").fetchone()
    return row[0] if row and row[0] is not None else None


def sweep(store, now=None, batch=5000):
    """Drop changes older than the log keeps, in short transactions."""
    cutoff = H.shift(now or H.now(), hours=-KEEP_HOURS)
    deleted = 0
    while True:
        with store.transaction() as c:
            removed = c.execute("DELETE FROM changes WHERE seq IN "
                                "(SELECT seq FROM changes WHERE at<? LIMIT ?)", (cutoff, batch)).rowcount
        deleted += removed
        if removed < batch:
            return deleted


class Doorbell:
    """Rung after a write that logged changes commits, from any thread. A waiter passes the
    generation it last saw, so a ring between its read and its wait is never lost.

    `latest` is the newest change number known to this process: a stream with its cursor there
    has nothing to read. One reader per event loop keeps it current for writes this process did
    not make, so no client polls on its own."""

    def __init__(self):
        self._lock = threading.Lock()
        self._waiters = set()
        self._readers = {}
        self.generation = 0
        self.latest = 0

    def ring(self, seq=None):
        with self._lock:
            if seq is not None and seq > self.latest:
                self.latest = seq
            self.generation += 1
            waiters = list(self._waiters)
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:        # a loop that has closed
                pass

    def observe(self, seq):
        """A newer change number seen by a read; rings if it is news."""
        if seq > self.latest:
            self.ring(seq)

    async def wait(self, generation, timeout):
        """Until the next ring after `generation`, or `timeout` seconds. True when rung."""
        if self.generation != generation:
            return True
        entry = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._waiters.add(entry)
        try:
            if self.generation != generation:
                return True
            await asyncio.wait_for(entry[1].wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return self.generation != generation
        finally:
            with self._lock:
                self._waiters.discard(entry)

    def reader(self, store):
        """Join the loop's shared reader (started on first use); call the result to leave."""
        loop = asyncio.get_running_loop()
        with self._lock:
            state = self._readers.get(loop)
            if state is None or state["task"].done():
                state = self._readers[loop] = {"count": 0, "task": None}
                state["task"] = loop.create_task(self._read(store, loop, state))
            state["count"] += 1

        def leave():
            with self._lock:
                state["count"] -= 1
        return leave

    async def _read(self, store, loop, state):
        def look():
            with store.read() as c:
                return latest(c)
        try:
            while True:
                await asyncio.sleep(FALLBACK_SECONDS)
                with self._lock:
                    if state["count"] <= 0:
                        if self._readers.get(loop) is state:
                            del self._readers[loop]
                        return
                try:
                    self.observe(await asyncio.to_thread(look))
                except sqlite3.Error:
                    pass
        except asyncio.CancelledError:
            with self._lock:
                if self._readers.get(loop) is state:
                    del self._readers[loop]
            raise


def bell(store):
    """The store's doorbell (one per Store, so per database)."""
    existing = getattr(store, "bell", None)
    if existing is None:
        existing = store.bell = Doorbell()
    return existing


# ---------------------------------------------------------------- what a viewer is sent

def status_line(c, who, slug, default=None):
    """A bot's status as `GET /api/v2/status` lists it to `who`; None for no status row."""
    from . import task_privacy as privacy
    from . import usage_limits
    row = H.status(c, slug)
    if not row:
        return None
    row = privacy.status(c, who, row)
    row["bot_state"] = (H.bot(c, row["bot"]) or {}).get("state")
    return usage_limits.overlay(c, row, usage_limits.company(c) if default is None else default)


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


class _Viewer:
    """Per-read caches of what `who` may read, so one batch asks each question once."""

    def __init__(self, c, auth, who):
        from . import task_privacy as privacy
        self.c, self.auth, self.who, self.privacy = c, auth, who, privacy
        self.principal = privacy.actor(who)
        self._bots = None
        self._conversations = {}

    def bots(self):
        if self._bots is None:
            self._bots = self.auth.bot_accesses(self.c, self.who)
        return self._bots

    def bot_readable(self, slug):
        return not slug or self.bots().get(slug, self.auth.FULL)["read"]

    def hidden_actor(self, actor):
        return bool(actor) and str(actor).startswith("bot:") and not self.bot_readable(str(actor)[4:])

    def conversation(self, cid):
        if cid not in self._conversations:
            try:
                self._conversations[cid] = self.auth.conversation(self.c, self.who, cid)
            except Problem:
                self._conversations[cid] = None
        return self._conversations[cid]


def _tasks(view, rows, task_views):
    c, who = view.c, view.who
    last = {}
    for row in rows:
        last[row["subject_id"]] = row
    ids = list(last)
    visible_sql = view.auth.task_sql(c, who)
    marks = ",".join("?" * len(ids))
    found = H._rows(c.execute(f"SELECT * FROM tasks WHERE id IN ({marks}) AND ({visible_sql})", ids))
    views = {item["id"]: item for item in task_views(found, c, who, visible_sql)}
    present = {r[0] for r in c.execute(f"SELECT id FROM tasks WHERE id IN ({marks})", ids)}
    out = []
    for task_id, row in last.items():
        entry = {"id": task_id}
        if task_id in views:
            entry["task"] = views[task_id]
        elif task_id not in present and _trashed_visible(c, who, task_id):
            entry["gone"] = True
        else:
            continue
        out.append((row, entry))
    return out


def _messages(view, rows):
    c, privacy = view.c, view.privacy
    last = {}
    for row in rows:
        last[(row["kind"] == "goal", row["subject_id"])] = row
    out = []
    for (goal, subject), row in last.items():
        cid = row["conversation_id"]
        if not cid or not view.conversation(cid):
            continue
        if goal:
            found = c.execute("SELECT * FROM chat_goals WHERE id=?", (subject,)).fetchone()
            if found and privacy.content_readable(c, view.principal, dict(found)) and view.bot_readable(found["bot"]):
                out.append((row, {"conversation_id": cid, "goal_id": subject, "goal": dict(found)}))
            continue
        message = c.execute("SELECT * FROM messages WHERE id=?", (subject,)).fetchone()
        if message is None:
            continue
        message = dict(message)
        if message["conversation_id"] != cid:
            continue
        if privacy.message_readable(c, view.principal, message):
            out.append((row, {"id": subject, "conversation_id": cid,
                              "message": {**message, "refs": H._json(message["refs_json"], {}) or {}}}))
        elif message.get("deleted_at") and _readable_before_deletion(view, message):
            out.append((row, {"id": subject, "conversation_id": cid, "deleted": True}))
    return out


def _readable_before_deletion(view, message):
    try:
        return view.privacy.readable(view.c, view.principal, view.privacy.message_tasks(view.c, message))
    except Problem:
        return False


def _runs(view, rows):
    c, privacy = view.c, view.privacy
    # Each output step is its own event; a run's state is sent once per read, as it stands now.
    picked, states = [], {}
    for row in rows:
        if row["kind"] == "output":
            picked.append(row)
        else:
            key = (row["kind"], row["subject_id"] or row["ref"])
            if key in states:
                picked.remove(states[key])
            states[key] = row
            picked.append(row)
    readable = {}
    out = []
    for row in picked:
        cid, bot, aid = row["conversation_id"], row["bot"], row["subject_id"]
        if cid and not view.conversation(cid):
            continue
        # A run's output is its bot's activity (Read), as on the bot's page.
        if not view.bot_readable(bot):
            continue
        if aid:
            if aid not in readable:
                readable[aid] = privacy.attempt_readable(c, view.principal, aid)
            if not readable[aid]:
                continue
        entry = {"attempt_id": aid, "conversation_id": cid, "bot": bot}
        if row["kind"] == "output":
            event = c.execute("SELECT * FROM attempt_events WHERE id=?", (row["ref"],)).fetchone()
            if event is None:
                continue
            event = dict(event)
            if not privacy.content_readable(c, view.principal, event):
                continue
            event["payload"] = H._json(event.pop("payload_json"), {})
            entry["output"] = event
        elif row["kind"] == "job":
            job = c.execute("SELECT j.*, m.conversation_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                            "WHERE j.message_id=? ORDER BY j.created DESC LIMIT 1", (row["ref"],)).fetchone()
            if job is None or not privacy.message_readable(c, view.principal, H.message(c, job["message_id"])):
                continue
            entry.update(job_id=job["id"], message_id=job["message_id"], state=job["state"], attempt_id=job["attempt_id"])
        else:
            attempt = c.execute("SELECT state, job_id FROM attempts WHERE id=?", (aid,)).fetchone()
            if attempt is None:
                continue
            entry.update(job_id=attempt["job_id"], state=attempt["state"])
        out.append((row, entry))
    return out


def _bots(view, rows):
    last = {}
    for row in rows:
        last[row["subject_id"]] = row
    out = []
    default = None
    for slug, row in last.items():
        if not view.bot_readable(slug):
            continue
        if default is None:
            from . import usage_limits
            default = usage_limits.company(view.c)
        out.append((row, {"bot": slug, "status": status_line(view.c, view.who, slug, default)}))
    return out


def _needs_list(view, rows, task_view):
    from .views import needs_items
    items = needs_items(view.c, view.auth, view.who, task_view)
    return [(rows[-1], {"count": len(items), "items": items})]


def read(c, auth, who, after, *, topics=TOPICS, conversations=(), bots=(), task_views=None, task_view=None,
         limit=BATCH):
    """What `who` may be sent after change `after`: (events, cursor, reset, more). Each event is
    (seq, topic, data). `cursor` moves past every row read, sent or not, so a resume starts after
    what this viewer could not see. `more` when a full batch was read and more waits."""
    c.execute("BEGIN")                  # one snapshot: the bounds and the rows agree
    try:
        newest = latest(c)
        first = oldest(c)
        floor = (first - 1) if first is not None else newest
        if after > newest or after < floor:
            return [], newest, True, False
        clauses, args = ["seq>?", "topic IN (%s)" % ",".join("?" * len(topics))], [after, *topics]
        if conversations:
            clauses.append("(topic NOT IN ('messages','runs') OR conversation_id IN (%s))" % ",".join("?" * len(conversations)))
            args += list(conversations)
        if bots:
            clauses.append("(topic NOT IN ('runs','bots') OR bot IN (%s))" % ",".join("?" * len(bots)))
            args += list(bots)
        if "needs" in topics:
            clauses.append("(topic!='needs' OR subject_id=?)")
            args.append(who.actor)
        rows = [dict(r) for r in c.execute("SELECT * FROM changes WHERE " + " AND ".join(clauses)
                                           + " ORDER BY seq LIMIT ?", (*args, limit))]
        more = len(rows) >= limit
        cursor = rows[-1]["seq"] if more else newest
        if not rows:
            return [], cursor, False, False
        view = _Viewer(c, auth, who)
        by_topic = {}
        for row in rows:
            by_topic.setdefault(row["topic"], []).append(row)
        picked = []
        if by_topic.get("tasks"):
            picked += [("tasks", r, e) for r, e in _tasks(view, by_topic["tasks"], task_views)]
        if by_topic.get("messages"):
            picked += [("messages", r, e) for r, e in _messages(view, by_topic["messages"])]
        if by_topic.get("runs"):
            picked += [("runs", r, e) for r, e in _runs(view, by_topic["runs"])]
        if by_topic.get("bots"):
            picked += [("bots", r, e) for r, e in _bots(view, by_topic["bots"])]
        if by_topic.get("needs"):
            picked += [("needs", r, e) for r, e in _needs_list(view, by_topic["needs"], task_view)]
        events = []
        for topic, row, entry in sorted(picked, key=lambda item: item[1]["seq"]):
            actor = None if view.hidden_actor(row["actor"]) else row["actor"]
            events.append((row["seq"], topic, {"seq": row["seq"], "actor": actor, "at": row["at"], **entry}))
        return events, cursor, False, more
    finally:
        if c.in_transaction:
            c.rollback()


def _frame(seq, topic, data):
    return (f"id: {seq}\n" if seq is not None else "") + f"event: {topic}\ndata: {encode(data)}\n\n"


def _list(value, name):
    items = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if len(items) > MAX_FILTER:
        raise Problem("validation", f"At most {MAX_FILTER} values for {name}", 422)
    return tuple(dict.fromkeys(items))


def install(app, store, auth, task_views, task_view):
    doorbell = bell(store)

    @app.get("/api/v2/events")
    async def events(request: Request, topics: str | None = None, after: int | None = None,
                     conversation: str | None = None, bot: str | None = None):
        """Server-sent events for people's pages: see the module docstring."""
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("human", "owner"):
            raise Problem("forbidden", "Live events are for people's pages", 403)
        wanted = _list(topics, "topics") or TOPICS
        unknown = [topic for topic in wanted if topic not in TOPICS]
        if unknown:
            raise Problem("validation", "Unknown topic: " + ", ".join(unknown) + " (one of " + ", ".join(TOPICS) + ")", 422)
        conversations, bots = _list(conversation, "conversation"), _list(bot, "bot")
        # The browser resends the last id it was given when it reconnects on its own.
        resume = request.headers.get("last-event-id", "")
        if resume.isdigit():
            after = int(resume)

        def start():
            with store.read() as c:
                return latest(c)

        def check():
            auth.authenticate(request.headers)

        def poll(cursor):
            auth.authenticate(request.headers)
            with store.read() as c:
                return read(c, auth, who, cursor, topics=wanted, conversations=conversations, bots=bots,
                            task_views=task_views, task_view=task_view)

        now_seq = await asyncio.to_thread(start)
        doorbell.observe(now_seq)
        cursor = after if after is not None and after >= 0 else now_seq

        async def generate():
            nonlocal cursor
            leave = doorbell.reader(store)
            try:
                yield "retry: 1000\n\n"
                yield _frame(None, "ready", {"seq": now_seq, "after": cursor, "topics": list(wanted)})
                deadline = time.monotonic() + LIFETIME_SECONDS
                quiet_since = time.monotonic()
                due = True                       # the first read catches up from `after`
                while True:
                    if await request.is_disconnected():
                        return
                    generation = doorbell.generation
                    if due or doorbell.latest > cursor:
                        try:
                            sent, next_cursor, reset, more = await asyncio.to_thread(poll, cursor)
                        except Problem:
                            yield "event: expired\ndata: {}\n\n"
                            return
                        due = more
                        if reset:
                            cursor = next_cursor
                            yield _frame(cursor, "reset", {"seq": cursor})
                            quiet_since = time.monotonic()
                        elif next_cursor != cursor:
                            for seq, topic, data in sent:
                                yield _frame(seq, topic, data)
                            # The cursor moves past changes this viewer may not see, so it resumes there.
                            if not sent or sent[-1][0] != next_cursor:
                                yield _frame(next_cursor, "cursor", {"seq": next_cursor})
                            cursor = next_cursor
                            doorbell.observe(cursor)
                            quiet_since = time.monotonic()
                        if more:
                            continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    wait = min(remaining, max(0.0, KEEPALIVE_SECONDS - (time.monotonic() - quiet_since)))
                    if await doorbell.wait(generation, wait):
                        await asyncio.sleep(GATHER_SECONDS)
                        continue
                    if time.monotonic() - quiet_since >= KEEPALIVE_SECONDS:
                        try:
                            await asyncio.to_thread(check)
                        except Problem:
                            yield "event: expired\ndata: {}\n\n"
                            return
                        yield ": keepalive\n\n"
                        quiet_since = time.monotonic()
            finally:
                leave()

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
