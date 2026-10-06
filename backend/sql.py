"""Read-only SQL over the hub database, with the caller's visibility built into the database layer.

Every guarded table is shadowed, for one request, by a temp view of the same name that carries
the caller's rules from `backend/auth.py`, so `SELECT * FROM messages` already means "the
messages you may read". The view reads its base table through an inner temp view whose name is
random for this connection, and SQLite's authorizer lets a base table be read only from that
inner view: neither `main.messages` nor a CTE named `messages` (both of which the authorizer
reports the same way as the view) reaches the rows underneath. Everything that is not a read is
denied outright. Nothing here depends on prompt text.
"""

import base64
import hashlib
import re
import secrets
import sqlite3
import time

from fastapi import Request
from pydantic import Field

from . import listening as L
from . import rooms
from . import task_privacy as privacy
from .privacy_index import ReadIndex
from .bot_access import q, qlist
from .models import Contract
from .store import H, Problem

# Rows and seconds per query; `max_rows` in the request may only lower the cap.
LIMITS = {"owner": (5000, 20.0), "human": (500, 5.0), "bot": (500, 5.0)}
# Readable as they are, by everyone: nothing personal, nothing secret, plus the schema.
OPEN = {"cloud_migrations", "rate_limits", "service_health", "learnings", "sqlite_master"}
# Table-valued functions SQLite reports like tables of the main schema.
TABLE_FUNCTIONS = {"json_each", "json_tree"}
SAFE_COLUMNS = {"slack_posts": {"message_id", "bot", "state", "attempts", "next_attempt",
                                "slack_ts", "created", "updated"}}
# Columns nobody reads through SQL. Tables not named in `guarded` (credentials, credential_keys,
# credential_grants, idempotency, runners, enrollments, session_epochs, settings_changes,
# backup_verified_blobs, _litestream_*, sqlite_* except sqlite_master) are denied outright.
HIDDEN = {"bots": {"token_hash", "cwd", "thread_id"}, "attempts": {"token_hash"},
          "service_jobs": {"token_hash"}}
DENIED_FUNCTIONS = {"load_extension"}
# FTS5 reads these as main.<virtual>_* with no view context; they are not a public API.
FTS_SHADOWS = ("_data", "_idx", "_content", "_docsize", "_config")
STATEMENT = re.compile(r"^\s*(?:(?:--[^\n]*\n?|/\*.*?\*/)\s*)*(select|with|explain\s+query\s+plan)\b", re.I | re.S)


class Query(Contract):
    sql: str = Field(min_length=1, max_length=20_000)
    params: list | dict = Field(default_factory=list)
    max_rows: int | None = Field(default=None, ge=1, le=5000)


def guarded(c, auth, who, inner, function):
    """Every table a caller may read through a view: name -> (row predicate, hidden columns).

    The predicates say in SQL what `auth.bot_access`, `auth.conversation`, `auth.task` and
    `auth.approval` say in Python. A bot is in the SQL layer for a caller who may read it (a bot
    they may only see or write to is not; the API shows it); `inner(table)` names the connection's unfiltered view of a
    base table, and a plain table name inside a predicate is the filtered view of it.
    """
    me, owner, bot = q(who.actor), who.role == "owner", who.role == "bot"
    listener = L.sees_everything(who)
    mine_inboxes = [] if listener else L.destinations_of(who.actor, L.destinations(auth.settings))
    hidden = sorted(auth.unreadable_bots(c, who))

    def bots_visible(column):
        return f"{column} NOT IN {qlist(hidden)}" if hidden else "1"

    if bot:
        conversations = (f"id IN (SELECT conversation_id FROM {inner('attempt_conversations')} "
                         f"WHERE attempt_id={q(who.attempt_id)}) "
                         "OR (kind='task' AND task_id IS NOT NULL AND task_id IN (SELECT id FROM tasks))")
    else:
        shared = [row["slug"] for row in H.bots(c) if row["slug"] not in hidden
                  and rooms.shared_member(c, auth, who.actor, row["slug"])]
        room_bot = ("COALESCE(NULLIF(substr(room_key,1,instr(room_key||':',':')-1),''),"
                    "(SELECT substr(value,5) FROM json_each(participants_json) WHERE value LIKE 'bot:%' LIMIT 1))")
        first_human = "(SELECT value FROM json_each(participants_json) WHERE value LIKE 'human:%' LIMIT 1)"
        participant = "1" if owner else f"EXISTS (SELECT 1 FROM json_each(participants_json) WHERE value={me})"
        conversations = (f"CASE COALESCE(scope,'direct') WHEN 'personal' THEN COALESCE(owner_actor,{first_human})={me} "
                         f"WHEN 'shared' THEN {room_bot} IN {qlist(shared)} ELSE {participant} END")
    tasks = auth.task_sql(c, who, delegations=inner("task_delegations"))
    # Evaluate provenance only for candidate rows, never scan unrelated content first.
    msg_gate = function("message") + "(id,conversation_id,refs_json,in_reply_to)"
    message_columns = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    for deleted in ("deleted_at", "deleted"):
        if deleted in message_columns:
            msg_gate += f" AND coalesce({deleted},0)=0"
    attempt_gate = function("attempt") + "(id)"
    event_gate = function("event") + "(target,actor,ts,detail_json)"
    tag_gate = function("tag") + "(id,is_template)"
    content_gate = lambda cols: function("content") + "(" + ",".join(cols) + ")"
    blob_gate = function("blob") + "(id)"
    file_gate = ("(task_id IS NULL OR task_id IN (SELECT id FROM tasks)) AND NOT EXISTS ("
                 "SELECT 1 FROM " + inner("bot_file_versions") + " v WHERE v.file_id=id AND (NOT " +
                 function("blob") + "(v.blob_id) OR (v.attempt_id IS NOT NULL AND NOT " +
                 function("attempt") + "(v.attempt_id))))")

    # Even an attempt's retained conversation grant must pass the current task gate.
    conversations = f"({conversations}) AND (task_id IS NULL OR kind<>'task' OR task_id IN (SELECT id FROM tasks))"
    by_task = "task_id IN (SELECT id FROM tasks)"
    by_message = "message_id IN (SELECT id FROM messages)"
    by_schedule = "schedule_id IN (SELECT id FROM schedules)"
    by_attempt = "attempt_id IN (SELECT id FROM attempts)"
    by_job = "job_id IN (SELECT id FROM jobs)"
    by_meeting = "meeting_id IN (SELECT id FROM meetings)"
    mine = f"actor={me}" if bot else "1"
    person = H.actor_id(who.actor)
    # Meetings are the company's; typed notes are their owner's (backend/media.py). A private
    # meeting narrows to the people its invite or its importer names, spelled either way.
    if owner:
        meetings = "1"
    elif bot or not who.email:
        meetings = "0"
    else:
        email = q(who.email.lower())
        meetings = (f"lower(owner)={email} OR (json_extract(metadata_json,'$.kind')='meeting' AND ("
                    "coalesce(json_extract(metadata_json,'$.private'),0)=0 OR EXISTS (SELECT 1 FROM "
                    f"json_each(json_extract(metadata_json,'$.calendar.attendees')) WHERE lower(value)={email}) "
                    "OR EXISTS (SELECT 1 FROM json_each(json_extract(metadata_json,'$.participants')) "
                    f"WHERE lower(json_extract(value,'$.email'))={email})))")
    goals = f"owner NOT IN {qlist(['bot:' + slug for slug in hidden])}" if hidden else "id IS NOT NULL"
    kpis = goals            # a KPI is owned like a goal: a bot's own are read by whoever may read the bot
    admin = who.role == "owner" or auth.bot_admin(who)
    # What happened is for whoever it involved: their own actions and what they may read. The rest is the
    # company's audit trail, which the owner reads whole.
    if owner:
        events = "1"
    elif bot:
        events = mine
    else:
        events = (f"actor={me} OR target IN (SELECT id FROM tasks) OR target IN (SELECT id FROM conversations) "
                  "OR target IN (SELECT slug FROM bots) OR target IN (SELECT 'bot:'||slug FROM bots)")
    # Imported drafts do not enter company search, exports or the audit feed.
    meetings = f"({meetings}) AND review_state='live'"
    events = (f"({events}) AND target NOT IN (SELECT id FROM {inner('meetings')} "
              "WHERE review_state<>'live')")
    rules = {
        "conversations": conversations,
        "messages": "conversation_id IN (SELECT id FROM conversations) AND deleted_at IS NULL AND " + msg_gate,
        "tasks": tasks,
        "tags": tag_gate, "task_tags": by_task,
        "task_events": by_task + " AND " + content_gate(("old", "new", "note")), "task_delegations": by_task,
        "task_reminders": by_task, "task_assets": by_task, "task_links": by_task,
        # A relation is read only when the caller may read both of its tasks.
        "task_relations": "from_task IN (SELECT id FROM tasks) AND to_task IN (SELECT id FROM tasks)",
        "message_assets": by_message,
        "approvals": ("(task_id IS NULL OR " + by_task + ") AND " + by_message + " AND (" +
                      ("1" if owner else f"requested_by={me} OR message_id IN (SELECT id FROM messages WHERE to_actor={me})") + ")"),
        "bots": bots_visible("slug"),
        "bot_status": bots_visible("bot"), "bot_status_history": bots_visible("bot"),
        "schedules": bots_visible("bot"), "bot_config": bots_visible("bot"), "bot_control": bots_visible("bot"),
        "bot_transitions": bots_visible("bot"), "assignments": bots_visible("bot"),
        "schedule_config": by_schedule, "schedule_occurrences": f"{by_schedule} AND (task_id IS NULL OR {by_task})",
        "turns": f"{bots_visible('bot')} AND (message_id IS NULL OR {by_message}) AND "
                 f"(task_id IS NULL OR {by_task}) AND " + attempt_gate,
        "deltas": "turn_id IN (SELECT id FROM turns)",
        "jobs": f"{bots_visible('bot')} AND {by_message}",
        "attempts": by_job + " AND " + attempt_gate, "job_recovery": by_job + " AND " + by_attempt,
        "attempt_events": by_attempt + " AND " + content_gate(("payload_json",)),
        "attempt_inputs": by_attempt, "attempt_conversations": by_attempt,
        "bot_transition_checkpoints": "conversation_id IN (SELECT id FROM conversations)",
        "task_types": "1", "task_steps": "1",
        "humans": "1",
        # Goals are the company's, except a bot's own: a caller reads those of bots they may read. Never a
        # bare "1" (the same reason as intake_items below).
        "goals": goals, "goal_events": "goal_id IN (SELECT id FROM goals)", "kpis": kpis,
        "kpi_readings": "kpi_id IN (SELECT id FROM kpis)", "kpi_definitions": "kpi_id IN (SELECT id FROM kpis)",
        "goal_kpis": "goal_id IN (SELECT id FROM goals)", "goal_checkins": "goal_id IN (SELECT id FROM goals)",
        "goal_proposals": "goal_id IS NULL OR goal_id IN (SELECT id FROM goals)",
        # The market graph is the company's, the same way goals are.
        "market_entities": "1", "market_edges": "1", "market_evidence": "1",
        "market_citations": "1", "market_insights": "1", "market_events": "1",
        # Listening's saved posts carry third parties' names and handles (backend/listening.py):
        # Listening and the owner read them all, a receiving bot only what was routed to it.
        # Which runs happened, and whether they were blocked, is anyone's to read.
        "listen_runs": "1",
        # Never a bare "1" for these three: SQLite would answer count(*) from the base table,
        # which the authorizer rightly refuses, since for everyone else the rows are filtered.
        "intake_items": ("id IS NOT NULL" if listener else f"destination IN {qlist(mine_inboxes)}"
                         if mine_inboxes else "id IS NULL"),
        "listen_items": "id IS NOT NULL" if listener else "id IN (SELECT item_id FROM intake_items)",
        "listen_judgments": "id IS NOT NULL" if listener else "item_id IN (SELECT id FROM listen_items)",
        # The roster, the sign-in lists and roles, onboarding: for the owner and the Admins.
        "registry_metadata": "1" if admin and not bot else "0",
        "events": f"({events}) AND " + event_gate,
        "refusals": "(" + ("1" if owner else mine if bot else f"actor={me}") + ") AND " +
                    function("event") + "('',actor,ts,detail_json)",
        "meetings": f"({meetings}) AND id NOT IN (SELECT meeting_id FROM {inner('media_control')} WHERE deleted_at IS NOT NULL)",
        "meeting_versions": by_meeting, "meeting_deliveries": by_meeting, "media_assets": by_meeting, "media_control": by_meeting,
        "meeting_items": by_meeting, "meeting_brain": by_meeting, "meeting_comments": by_meeting,
        "import_refs": by_meeting,
        "documents": "1" if owner else "visibility='external'",
        "document_versions": "id IN (SELECT id FROM documents)",
        "docs": "id IS NOT NULL", "doc_versions": "doc_id IN (SELECT id FROM docs)",
        "linked_docs": "id IS NOT NULL",
        "bot_files": (file_gate + " AND " + (f"bot={q(H.actor_id(who.actor))} AND " if bot else "")
                      + f"(CASE WHEN scope LIKE 'task:%' THEN substr(scope,6) IN (SELECT id FROM tasks) "
                        "WHEN scope LIKE 'conversation:%' THEN substr(scope,14) IN (SELECT id FROM conversations) "
                        f"ELSE {bots_visible('bot')} END)"),
        "bot_file_versions": "file_id IN (SELECT id FROM bot_files)",
        "bot_file_activity": "file_id IN (SELECT id FROM bot_files)",
        # The company owner is not thereby the owner of everyone's private attachments (media.py).
        "blobs": blob_gate + " AND (" + ("" if bot else f"owner={me} OR ") +
                 "id IN (SELECT blob_id FROM message_assets) OR id IN (SELECT blob_id FROM task_assets) "
                 "OR id IN (SELECT blob_id FROM media_assets))",
        "archives": "1" if owner else "0" if bot else f"owner={me}",
        "connector_snapshots": "1" if owner else "0" if bot else f"owner={q(person)}",
        "service_jobs": "1" if owner else "0" if bot else f"requested_by={me}",
        # Mail copies are read through /api/v2/mail. SQL stays owner-only.
        "mail_mailboxes": "1" if owner else "0",
        "mail_messages": "1" if owner else "0",
        "mail_fts": "1" if owner else "0",
        "slack_posts": by_message if owner else "message_id IS NULL",
    }
    for table in ("bot_status", "bot_status_history"):
        rules[table] += " AND (task_id IS NULL OR " + by_task + ")"
        # Untagged free-form status can retain the previous private turn's content.
        rules[table] += (" AND NOT EXISTS (SELECT 1 FROM " + inner("tasks") +
                         " t WHERE (t.owner='bot:'||bot OR t.requester='bot:'||bot) "
                         "AND (t.private IS NULL OR t.private<>0) AND NOT " +
                         function("task") + "(t.id))")
    for table in ("docs", "doc_versions", "linked_docs", "documents", "document_versions"):
        cols = [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]
        rules[table] = "(" + rules[table] + ") AND " + content_gate(cols)
    rules["task_file_reviews"] = "file_id IN (SELECT id FROM bot_files) OR file_id IN (SELECT id FROM blobs)"
    rules["bot_file_activity"] += " AND (task_id IS NULL OR " + by_task + ") AND (attempt_id IS NULL OR " + by_attempt + ")"
    rules["bot_file_versions"] += " AND (attempt_id IS NULL OR " + by_attempt + ")"
    hidden = {table: set(HIDDEN.get(table, ())) for table in rules}
    if bot:
        hidden["humans"].add("email")
    return {table: (rules[table], hidden[table]) for table in rules}


def connect(path, c, auth, who, trace=None):
    """A read-only connection whose temp views and authorizer carry this caller's visibility."""
    prefix = "v" + secrets.token_hex(12) + "_"
    inner = lambda table: f'"{prefix}{table}"'  # noqa: E731
    function = lambda name: f'"{prefix}privacy_{name}"'  # noqa: E731
    # Internal provenance SELECTs cannot leave authorized prepared statements for callers.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5, isolation_level=None, cached_statements=0)
    conn.row_factory = sqlite3.Row
    if trace:
        conn.set_trace_callback(trace)
    trusted = [False]
    try:
        # Predicates, provenance and result rows use one read snapshot.
        conn.execute("BEGIN")
        tables = guarded(conn, auth, who, inner, function)
        source_tables = set(tables) | {"blob_media"}
        class Source:
            def execute(self, statement, args=()):
                statement = re.sub(r'\b(FROM|JOIN)\s+(\w+)', lambda m:
                                   m[1] + ' main."' + m[2] + '"' if m[2] in source_tables else m[0], statement,
                                   flags=re.I)
                trusted[0] = True
                try:
                    return conn.execute(statement, args)
                finally:
                    trusted[0] = False
        index = ReadIndex(Source(), who)
        functions = {"message": (4, index.message), "attempt": (1, index.attempt),
                     "event": (4, index.event), "tag": (2, index.tag), "blob": (1, index.blob),
                     "task": (1, lambda tid: tid not in index.denied),
                     "content": (-1, lambda *parts: index.content(list(parts)))}
        for name, (arity, fn) in functions.items():
            conn.create_function(prefix + "privacy_" + name, arity, fn, deterministic=True)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 10_000_000)   # no randomblob(1e9) memory bombs
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.execute("PRAGMA busy_timeout=5000")
        for table, (_, hidden) in tables.items():
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")') if row[1] not in hidden
                       and (table not in SAFE_COLUMNS or row[1] in SAFE_COLUMNS[table])]
            if not columns:
                raise Problem("schema", f"Table {table} is missing from this database", 500)
            names = ",".join(f'"{column}"' for column in columns)
            conn.execute(f'CREATE TEMP VIEW {inner(table)} AS SELECT {names} FROM main."{table}"')
        for table, (predicate, _) in tables.items():
            projection = "*"
            conn.execute(f'CREATE TEMP VIEW "{table}" AS SELECT {projection} FROM {inner(table)} WHERE {predicate}')
        # Older SQLite versions initialize JSON virtual tables with schema authorization calls.
        # Initialize only these built-ins before installing the read-only authorizer.
        for function in sorted(TABLE_FUNCTIONS):
            conn.execute(f"SELECT value FROM {function}('[]')").fetchall()
        conn.execute("PRAGMA query_only=1")
        # Every other object in the file is off limits, even as a bare `count(*)`.
        denied = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")} | {"sqlite_temp_master"}
        denied -= set(tables) | OPEN
    except BaseException:
        conn.close()
        raise
    inner_names = {prefix + table for table in tables}

    def fts_parent(name):
        for suffix in FTS_SHADOWS:
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return None

    def authorize(action, table, column, schema, context):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if column and column.startswith(prefix + "privacy_"):
                return sqlite3.SQLITE_OK if context in tables else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY if column in DENIED_FUNCTIONS else sqlite3.SQLITE_OK
        # FTS5 checks main.data_version before reading; nothing else may PRAGMA.
        if action == sqlite3.SQLITE_PRAGMA:
            return sqlite3.SQLITE_OK if table == "data_version" or trusted[0] and table == "table_info" else sqlite3.SQLITE_DENY
        if action != sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_DENY
        if schema is None:
            # A table named without columns (`count(*)`), a CTE or a subquery: an unqualified
            # name resolves to the temp view first, so only the objects with no view are denied.
            return sqlite3.SQLITE_DENY if table in denied else sqlite3.SQLITE_OK
        if schema == "main":
            if trusted[0] and table in source_tables:
                return sqlite3.SQLITE_OK
            if table in OPEN or table in TABLE_FUNCTIONS:
                return sqlite3.SQLITE_OK
            # These are visible in full (predicate 1). SQLite answers count(*) by reading
            # the base table with no view context; that read is the same rows as the view.
            if table in {"market_entities", "market_edges", "market_evidence", "market_citations",
                         "market_insights", "market_events", "listen_runs", "task_types", "task_steps"}:
                return sqlite3.SQLITE_OK
            # A base table is read only by its own inner view, whose name is this connection's secret.
            if table in tables and context == prefix + table:
                return sqlite3.SQLITE_OK
            # FTS5 reads the virtual table and its shadow tables with no view
            # context. Allow that only for the owner: mail_fts is owner-only.
            parent = fts_parent(table or "")
            if who.role == "owner" and table in tables and str(table).endswith("_fts"):
                return sqlite3.SQLITE_OK
            if who.role == "owner" and parent and parent in tables:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if schema == "temp":
            if table in tables:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_OK if table in inner_names and context in tables else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    return conn


def json_value(value):
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


def run(conn, sql, params, cap, seconds):
    """Execute one read statement; returns the response body or raises a Problem with SQLite's words."""
    deadline = time.monotonic() + seconds
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
    started = time.monotonic()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchmany(cap + 1)
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise Problem("timeout", f"Query stopped after {seconds:g} s; narrow it or add a LIMIT", 422) from exc
        raise Problem("sql", str(exc), 422) from exc
    except (sqlite3.Error, sqlite3.Warning) as exc:
        raise Problem("sql", str(exc), 422) from exc
    ms = round((time.monotonic() - started) * 1000)
    blobs = any(isinstance(value, bytes) for row in rows for value in row)
    result = {"columns": [d[0] for d in cursor.description or ()],
              "rows": [[json_value(value) for value in row] for row in rows[:cap]],
              "row_count": min(len(rows), cap), "truncated": len(rows) > cap, "ms": ms}
    if blobs:
        result["note"] = "BLOB values are base64"
    return result


def install_sql(app, store, auth):
    @app.post("/api/v2/sql")
    def query(request: Request, body: Query):
        who = request.state.identity
        if not STATEMENT.match(body.sql):
            raise Problem("sql", "Send one SELECT, WITH or EXPLAIN QUERY PLAN statement", 422)
        # Audit metadata cannot copy literals or SQL error excerpts from private work.
        detail = {"query_hash": hashlib.sha256(body.sql.encode()).hexdigest(),
                  "query_bytes": len(body.sql.encode()), "rows": 0, "ms": 0, "error": None}
        try:
            with store.read() as c:
                principal = who
                if who.role == "runner":
                    # Only a personal Mac reads SQL with its registering person's rights.
                    row = c.execute("SELECT operator,platform FROM runners WHERE id=?", (who.runner_id,)).fetchone()
                    if not str(row["platform"] or "").startswith("darwin"):
                        raise Problem("forbidden", "Only a Mac's runner credential reads as its operator; "
                                                   "use a personal API token", 403)
                    principal = auth.identity_for_actor(c, "human:" + row["operator"])
                    detail["as"] = principal.actor
                cap, seconds = LIMITS[principal.role]
                if body.max_rows:
                    cap = min(cap, body.max_rows)
                conn = connect(store.settings.db_path, c, auth, principal)
                try:
                    result = run(conn, body.sql, body.params, cap, seconds)
                    detail.update(rows=result["row_count"], ms=result["ms"])
                    return result
                finally:
                    conn.close()
        except Problem as exc:
            detail["error"] = exc.code
            raise
        finally:
            with store.transaction() as c:
                H.event(c, who.actor, "sql.query", "", detail)
