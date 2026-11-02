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
from collections import OrderedDict
from contextvars import copy_context
import hashlib
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from fastapi import Request
from pydantic import Field

from . import listening as L
from . import outcome_stats
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
SAFE_COLUMNS = {"slack_posts": {"message_id", "bot", "state", "attempts", "next_attempt_at",
                                "slack_ts", "created_at", "updated_at"}}
# Columns nobody reads through SQL. Tables not named in `guarded` (credentials, credential_keys,
# credential_grants, credential_uses, idempotency, runners, enrollments, session_epochs, bot_session_epochs, settings_changes,
# backup_verified_blobs, _litestream_*, sqlite_* except sqlite_master) are denied outright.
HIDDEN = {"task_requesters": {"token_hash"}, "bots": {"token_hash", "cwd", "thread_id"}, "attempts": {"token_hash"},
          "service_jobs": {"token_hash"}}
# Columns read as an expression over the caller's own views: a column naming a task the caller may not read gives
# its id to nobody. A bot's claim limit (bot_control.claim_task) on such a task reads '': a limit is on, as
# GET /api/v2/bots says with `claim_scoped` and no `claim_task`.
MASKED = {"bot_control": {"claim_task": "CASE WHEN source.claim_task IS NULL THEN NULL "
                                        "WHEN source.claim_task IN (SELECT id FROM tasks) THEN source.claim_task "
                                        "ELSE '' END"}}
DENIED_FUNCTIONS = {"load_extension"}


def unsettled_watched(c, inner):
    """The ids of the live runs of bots org rules apply to, as a subquery over the inner attempts view, or "" when no
    rule watches a bot: their reply text stays out until it is checked and delivered, as their room, steps, log, events
    and capture keep it (backend/org_rule_check.py hides_run). A late result filed against an expired run included."""
    from .org_rule_check import UNSETTLED, watched_bots
    watched = watched_bots(c)
    return (f"SELECT id FROM {inner('attempts')} WHERE bot_id IN {qlist(watched)} AND state IN {qlist(UNSETTLED)}"
            if watched else "")


def live_masks(unsettled):
    """MASKED for one request: a watched live run's reply-bearing columns read NULL (its result's `text` alone is
    removed, so the rest of the result stays readable); every other column of the run reads as it is."""
    if not unsettled:
        return {}
    hidden = lambda column: f"CASE WHEN source.id IN ({unsettled}) THEN NULL ELSE source.{column} END"  # noqa: E731
    return {"attempts": {"final_text": hidden("final_text"),
                         "result": f"CASE WHEN source.id NOT IN ({unsettled}) THEN source.result "
                                        "WHEN json_valid(source.result) THEN json_remove(source.result,'$.text') "
                                        "ELSE NULL END"},
            "turns": {"summary": hidden("summary")}}
# Read-only views built over the caller's connection: name -> (base tables it reads, SELECT over `{table}`, the
# caller's own filtered view of each, and `{all_table}`, its unfiltered inner view). A total adds up only rows the
# caller may read: a bucket of one run would otherwise tell them that run exists, what it cost and whether it acted.
# `{all_table}` may only narrow what is counted, never add a row.
DERIVED = {
    # Runs per week (Monday, UTC), bot, trigger and purpose, with tokens and outcome (backend/run_triggers.py).
    # A run from before triggers were recorded reads `unknown`, and is counted, never dropped. Input is the run's
    # measured usage (every primary and fallback segment). A run from before measured usage has its one-number
    # count in input_tokens (migration 76). An attempt that never got a
    # turn row (one that failed or expired before it started) is counted too, as `unknown`, with no tokens or outcome;
    # one whose turn the caller may not read is not turnless, and is not counted.
    "run_weekly": (("turns", "attempts"), """
        SELECT date(started_at, '-6 days', 'weekday 1') AS week, bot, coalesce(trigger_kind, 'unknown') AS trigger,
               coalesce(purpose, 'unknown') AS purpose,
               count(*) AS runs,
               sum(coalesce(input_tokens, 0) + coalesce(cached_tokens, 0)) AS input_tokens,
               sum(coalesce(cached_tokens, 0)) AS cached_input_tokens,
               sum(coalesce(output_tokens, 0)) AS output_tokens,
               sum(coalesce(outcome, '') = 'acted') AS acted,
               sum(coalesce(outcome, '') = 'no_change') AS no_change,
               sum(coalesce(outcome, '') = 'nothing_to_do') AS nothing_to_do,
               sum(outcome IS NULL) AS unknown,
               sum(coalesce(did_nothing, 0) = 1) AS did_nothing,
               sum(rotated_at IS NOT NULL) AS rotated,
               round(100.0 * sum(coalesce(outcome, '') = 'acted') / count(*), 1) AS acted_pct,
               round(100.0 * sum(coalesce(outcome, '') = 'no_change') / count(*), 1) AS no_change_pct,
               round(100.0 * sum(coalesce(outcome, '') = 'nothing_to_do') / count(*), 1) AS nothing_to_do_pct,
               round(100.0 * sum(outcome IS NULL) / count(*), 1) AS unknown_pct
        FROM (SELECT started_at, bot, trigger_kind, purpose, input_tokens, cached_tokens, output_tokens,
                     outcome, did_nothing, rotated_at
              FROM {turns} WHERE started_at IS NOT NULL
              UNION ALL
              SELECT coalesce(a.started_at, a.created_at), a.bot_id, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
              FROM {attempts} a WHERE NOT EXISTS (SELECT 1 FROM {all_turns} t WHERE t.id = a.id))
        GROUP BY 1, 2, 3, 4"""),
    # Runs per week, bot and wake rule (backend/rules.py): the rule that woke the run, with its tokens and how many did
    # nothing. A run from before rules, or whose message carried none, reads `unknown`. Same token sums as run_weekly.
    "rule_weekly": (("turns",), """
        SELECT date(started_at, '-6 days', 'weekday 1') AS week, bot, coalesce(rule_id, 'unknown') AS rule,
               count(*) AS runs,
               sum(coalesce(input_tokens, 0) + coalesce(cached_tokens, 0)) AS input_tokens,
               sum(coalesce(cached_tokens, 0)) AS cached_input_tokens,
               sum(coalesce(output_tokens, 0)) AS output_tokens,
               sum(coalesce(outcome, '') = 'acted') AS acted,
               sum(coalesce(did_nothing, 0) = 1) AS did_nothing,
               round(100.0 * sum(coalesce(did_nothing, 0) = 1) / count(*), 1) AS did_nothing_pct
        FROM {turns} WHERE started_at IS NOT NULL
        GROUP BY 1, 2, 3"""),
}
# Totals over rows the reader may not read one by one, built only for the owner and a bot the owner granted them
# (backend/outcome_stats.py). Each groups by week and fixed names (status, proposer, bot, tool) and carries no text, no
# id and no person: a proposal's owner, a call's person and its token label never leave the base row.
GRANTED = {
    # Assistant and BotOps proposals per week, proposer and status (pending, running, done, failed, cancelled,
    # expired); `share_pct` is that status's share of its week and proposer.
    "assistant_action_weekly": (("assistant_actions",), """
        SELECT week, proposer, status, actions,
               round(100.0 * actions / sum(actions) OVER (PARTITION BY week, proposer), 1) AS share_pct
        FROM (SELECT date(created_at, '-6 days', 'weekday 1') AS week,
                     coalesce(proposer, proposed_via, 'assistant') AS proposer,
                     status, count(status) AS actions
              FROM {all_assistant_actions} GROUP BY 1, 2, 3)"""),
    # Bot tool calls through MCP per week, by tool and by bot when the reader may read that bot; hidden bots count
    # with no `bot`. People's calls are excluded. A name that is not a hub tool's counts as `other`.
    "mcp_call_weekly": (("events", "bots"), """
        SELECT week, bot, tool, count(*) AS calls, sum(error) AS errors,
               round(100.0 * sum(error) / count(*), 1) AS error_pct,
               CAST(round(avg(ms)) AS INTEGER) AS avg_ms, max(ms) AS max_ms
        FROM (SELECT date(created_at, '-6 days', 'weekday 1') AS week,
                     CASE WHEN actor LIKE 'bot:%' AND substr(actor, 5) IN (SELECT id FROM {bots})
                          THEN substr(actor, 5) END AS bot,
                     CASE WHEN target IN {tool_names} THEN target ELSE 'other' END AS tool,
                     coalesce(json_extract(detail, '$.error'), 0) <> 0 AS error,
                     coalesce(json_extract(detail, '$.ms'), 0) AS ms
              FROM {all_events} WHERE action = 'mcp.call' AND actor LIKE 'bot:%')
        GROUP BY 1, 2, 3"""),
}
# Base tables only a GRANTED view reads, and the columns it may see of them.
GRANTED_SOURCES = {"assistant_actions": ("created_at", "proposer", "proposed_via", "status")}
# FTS5 reads these as main.<virtual>_* with no view context; they are not a public API.
FTS_SHADOWS = ("_data", "_idx", "_content", "_docsize", "_config")
STATEMENT = re.compile(r"^\s*(?:(?:--[^\n]*\n?|/\*.*?\*/)\s*)*(select|with|explain\s+query\s+plan)\b", re.I | re.S)

_SCHEMAS = OrderedDict()
_SCHEMA_LOCK = threading.Lock()


def schema_columns(conn, path):
    """Only schema metadata is shared. DDL and a restored/replaced file invalidate it."""
    path = Path(path).resolve()
    info = path.stat()
    version = conn.execute("PRAGMA main.schema_version").fetchone()[0]
    key = (str(path), info.st_dev, info.st_ino, version)
    with _SCHEMA_LOCK:
        columns = _SCHEMAS.setdefault(key, {})
        _SCHEMAS.move_to_end(key)
        if len(_SCHEMAS) > 16:
            _SCHEMAS.popitem(last=False)

    class Columns:
        def get(self, table, default=()):
            # Load only exposed tables; hidden/internal tables need no view metadata.
            with _SCHEMA_LOCK:
                if table not in columns:
                    columns[table] = tuple(row[1] for row in conn.execute(
                        'PRAGMA main.table_info("' + table.replace('"', '""') + '")'))
                return columns[table] or default
    return Columns()


class Audit:
    """Share a commit among callers queued behind the current audit writer.

    Each request waits for its own durable event. There is no background worker,
    fire-and-forget audit or lock held during query evaluation.
    """
    def __init__(self, store):
        self.store, self.pending = store, []
        self.condition, self.writing = threading.Condition(), False

    def record(self, actor, detail):
        entry = {"actor": actor, "detail": detail, "context": copy_context(), "done": False, "error": None}
        with self.condition:
            self.pending.append(entry)
            self.condition.notify_all()
            while not entry["done"]:
                if self.writing:
                    self.condition.wait()
                    continue
                batch, self.pending, self.writing = self.pending, [], True
                self.condition.release()
                error = None
                try:
                    with self.store.transaction() as c:
                        for item in batch:
                            item["context"].run(H.event, c, item["actor"], "sql.query", "", item["detail"])
                except BaseException as exc:
                    error = exc
                finally:
                    self.condition.acquire()
                    for item in batch:
                        item.update(done=True, error=error)
                    self.writing = False
                    self.condition.notify_all()
            if entry["error"] is not None:
                raise entry["error"]


class ReadConnection(sqlite3.Connection):
    pass


class Query(Contract):
    sql: str = Field(min_length=1, max_length=20_000)
    params: list | dict = Field(default_factory=list)
    max_rows: int | None = Field(default=None, ge=1, le=5000)


def guarded(c, auth, who, inner, function, columns=None):
    """Every table a caller may read through a view: name -> (row predicate, hidden columns).

    The predicates say in SQL what `auth.bot_access`, `auth.conversation`, `auth.task` and
    `auth.approval` say in Python. A bot is in the SQL layer for a caller who may read it (a bot
    they may only see or write to is not; the API shows it); `inner(table)` names the connection's unfiltered view of a
    base table, and a plain table name inside a predicate is the filtered view of it.
    """
    me, owner, bot = q(who.actor), who.role == "owner", who.role == "bot"
    listener = L.sees_everything(who)
    mine_inboxes = [] if listener else L.destinations_of(who.actor, L.destinations(auth.settings, c))
    hidden = sorted(auth.unreadable_bots(c, who))

    def bots_visible(column):
        return f"{column} NOT IN {qlist(hidden)}" if hidden else "1"

    if bot:
        conversations = (f"id IN (SELECT conversation_id FROM {inner('attempt_conversations')} "
                         f"WHERE attempt_id={q(who.attempt_id)}) "
                         "OR (kind='task' AND task_id IS NOT NULL AND task_id IN (SELECT id FROM tasks))")
    else:
        visible = [row["id"] for row in H.bots(c) if row["id"] not in hidden]
        member = rooms.shared_member_bots(c, auth, who.actor, visible)
        shared = [slug for slug in visible if slug in member]
        room_bot = ("COALESCE(NULLIF(substr(room_key,1,instr(room_key||':',':')-1),''),"
                    "(SELECT substr(value,5) FROM json_each(participants) WHERE value LIKE 'bot:%' LIMIT 1))")
        first_human = "(SELECT value FROM json_each(participants) WHERE value LIKE 'human:%' LIMIT 1)"
        participant = "1" if owner else f"EXISTS (SELECT 1 FROM json_each(participants) WHERE value={me})"
        conversations = (f"CASE COALESCE(scope,'direct') WHEN 'personal' THEN COALESCE(owner_id,{first_human})={me} "
                         f"WHEN 'shared' THEN {room_bot} IN {qlist(shared)} ELSE {participant} END "
                         # A task's own room is read by whoever may read the task (auth.conversation).
                         "OR (kind='task' AND task_id IS NOT NULL)")
        conversations = f"({conversations}) OR " + function("meeting_room") + "(id)"
    tasks = auth.task_sql(c, who, delegations=inner("task_delegations"), unreadable=hidden)
    # Evaluate provenance only for candidate rows, never scan unrelated content first.
    msg_gate = function("message") + "(id,conversation_id,refs,in_reply_to)"
    message_columns = set(columns.get("messages", ())) if columns is not None else {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    for deleted in ("deleted_at", "deleted"):
        if deleted in message_columns:
            msg_gate += f" AND coalesce({deleted},0)=0"
    attempt_gate = function("attempt") + "(id)"
    event_gate = function("event") + "(target,actor,created_at,detail)"
    tag_gate = function("tag") + "(id,is_template)"
    content_gate = lambda cols: function("content") + "(" + ",".join(cols) + ")"
    blob_gate = function("blob") + "(id)"
    file_gate = ("(task_id IS NULL OR task_id IN (SELECT id FROM tasks)) AND NOT EXISTS ("
                 "SELECT 1 FROM " + inner("bot_file_versions") + " v WHERE v.file_id=id AND (NOT " +
                 function("blob") + "(v.blob_id) OR (v.attempt_id IS NOT NULL AND NOT " +
                 function("attempt") + "(v.attempt_id))))")

    # Even an attempt's retained conversation grant must pass the current task gate.
    conversations = (f"({conversations}) AND (task_id IS NULL OR kind<>'task' OR task_id IN (SELECT id FROM tasks)) AND "
                     + function("conversation") + "(id)")
    by_task = "task_id IN (SELECT id FROM tasks)"
    # Correlate large history tables by primary key. An IN subquery materializes
    # every readable message/job/attempt before even a LIMIT 1 can return.
    by_message = "EXISTS (SELECT 1 FROM messages readable WHERE readable.id=source.message_id)"
    by_schedule = "schedule_id IN (SELECT id FROM schedules)"
    by_attempt = "EXISTS (SELECT 1 FROM attempts readable WHERE readable.id=source.attempt_id)"
    by_job = "EXISTS (SELECT 1 FROM jobs readable WHERE readable.id=source.job_id)"
    by_meeting = "meeting_id IN (SELECT id FROM meetings)"
    # A live run of a bot org rules apply to keeps its reply text out until it is checked and delivered (live_masks
    # hides its reply columns): its tool steps stay readable.
    unsettled = unsettled_watched(c, inner)
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
        meetings = (f"lower(owner)={email} OR (json_extract(metadata,'$.kind')='meeting' AND ("
                    "coalesce(json_extract(metadata,'$.private'),0)=0 OR EXISTS (SELECT 1 FROM "
                    f"json_each(json_extract(metadata,'$.calendar.attendees')) WHERE lower(value)={email}) "
                    "OR EXISTS (SELECT 1 FROM json_each(json_extract(metadata,'$.participants')) "
                    f"WHERE lower(json_extract(value,'$.email'))={email})))")
    goals = f"owner_id NOT IN {qlist(['bot:' + slug for slug in hidden])}" if hidden else "id IS NOT NULL"
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
                  "OR target IN (SELECT id FROM bots) OR target IN (SELECT 'bot:'||id FROM bots)")
    # Imported drafts do not enter company search, exports or the audit feed.
    meetings = f"({meetings}) AND review_state='live'"
    events = (f"({events}) AND target NOT IN (SELECT id FROM {inner('meetings')} "
              "WHERE review_state<>'live')")
    rules = {
        "conversations": conversations,
        # Rooms are the small authorization set, unlike message/run history.
        # Build their id set once rather than re-evaluate the room view per message.
        "messages": "conversation_id IN (SELECT id FROM conversations) AND deleted_at IS NULL AND " + msg_gate,
        "tasks": tasks,
        "tags": tag_gate, "task_tags": by_task,
        "task_events": by_task + " AND " + content_gate(("old", "new", "note")), "task_delegations": by_task,
        "task_requesters": by_task, "task_reminders": by_task, "task_assets": by_task, "task_links": by_task, "task_roles": by_task,
        # A relation is read only when the caller may read both of its tasks.
        "task_relations": "from_task IN (SELECT id FROM tasks) AND to_task IN (SELECT id FROM tasks)",
        "message_assets": by_message,
        "approvals": ("(task_id IS NULL OR " + by_task + ") AND " + by_message + " AND (" +
                      ("1" if owner else f"requested_by={me} OR message_id IN (SELECT id FROM messages WHERE recipient_id={me})") + ")"),
        "bots": bots_visible("id"),
        "bot_status": bots_visible("bot"), "bot_status_history": bots_visible("bot"),
        "schedules": bots_visible("bot"), "bot_config": bots_visible("bot"), "bot_control": bots_visible("bot"),
        "bot_transitions": bots_visible("bot"), "assignments": bots_visible("bot"),
        "schedule_config": by_schedule, "schedule_occurrences": f"{by_schedule} AND (task_id IS NULL OR {by_task})",
        "turns": f"{bots_visible('bot')} AND (message_id IS NULL OR {by_message}) AND "
                 f"(task_id IS NULL OR {by_task}) AND " + attempt_gate,
        "deltas": "turn_id IN (SELECT id FROM turns)" + (f" AND turn_id NOT IN ({unsettled})" if unsettled else ""),
        "jobs": f"{bots_visible('bot_id')} AND {by_message}",
        "attempts": by_job + " AND " + attempt_gate, "job_recovery": by_job + " AND " + by_attempt,
        "attempt_events": by_attempt + " AND " + content_gate(("payload",)) + (
            f" AND NOT (kind IN ('delta','message') AND attempt_id IN ({unsettled}))" if unsettled else ""),
        "attempt_inputs": by_attempt, "attempt_conversations": by_attempt,
        "bot_transition_checkpoints": "conversation_id IN (SELECT id FROM conversations)",
        "task_types": "1", "task_steps": "1",
        "humans": "1",
        # Recruiting seats are roles on the company chart that everyone sees (backend/lifecycle.py): their titles,
        # job descriptions and reporting lines. Never a bare "1" (the same reason as intake_items below).
        "seats": "id IS NOT NULL",
        # Goals are the company's, except a bot's own: a caller reads those of bots they may read. Never a
        # bare "1" (the same reason as intake_items below).
        "goals": goals, "goal_events": "goal_id IN (SELECT id FROM goals)", "kpis": kpis,
        "kpi_readings": "kpi_id IN (SELECT id FROM kpis)", "kpi_definitions": "kpi_id IN (SELECT id FROM kpis)",
        "goal_kpis": "goal_id IN (SELECT id FROM goals)", "goal_checkins": "goal_id IN (SELECT id FROM goals)",
        # A strategy proposal carries the unpublished draft or every affected goal's title (goal_routes.strategy_visible):
        # the owner's, and the bot's that proposed it.
        "goal_proposals": "(goal_id IS NULL OR goal_id IN (SELECT id FROM goals)) AND " + (
            "1" if owner else f"(kind NOT IN ('strategy_draft','strategy_realign','brand_draft','gtm_draft') "
                              f"OR proposed_by={me})"),
        "goal_asks": "task_id IN (SELECT id FROM tasks)",
        # The company's Vision and Description and their history: everyone's to read, like the company's goals.
        "company_profile": "id IS NOT NULL", "company_events": "id IS NOT NULL",
        # The team's strategy (backend/strategy.py): the published versions are everyone's, like the goals. The draft,
        # the live bets (their words may be unpublished) and the history of both are the owner's.
        "strategy_versions": "id IS NOT NULL" if owner else "status='published'",
        "strategy_bets": "id IS NOT NULL" if owner else "id IS NULL",
        "strategy_events": "id IS NOT NULL" if owner else "id IS NULL",
        # A lens run (backend/lenses.py) on the draft carries the unpublished strategy's words: the owner's. Every other
        # run only discusses what is published.
        "lens_runs": "id IS NOT NULL" if owner else "on_draft=0",
        # The market graph is the company's, the same way goals are.
        "market_entities": "1", "market_edges": "1", "market_evidence": "1",
        "market_citations": "1", "market_insights": "1", "market_events": "1",
        # Page monitors and the weekly briefings (backend/market_watch.py, backend/market_briefing.py): public pages the
        # team watches and what the Librarian wrote about them, the market's to read.
        "market_watches": "1", "market_briefings": "1",
        "market_sizes": "1", "market_figures": "1", "market_sources": "1",
        # Listening's saved posts carry third parties' names and handles (backend/listening.py):
        # Listening and the owner read them all, a receiving bot only what was routed to it.
        # Which runs happened, and whether they were blocked, is anyone's to read.
        "listen_runs": "1",
        # The destinations saved in Settings > Listening: the owner's and the admins'.
        "listening_destinations": "key IS NOT NULL" if admin and not bot else "key IS NULL",
        # Never a bare "1" for these three: SQLite would answer count(*) from the base table,
        # which the authorizer rightly refuses, since for everyone else the rows are filtered.
        "intake_items": ("id IS NOT NULL" if listener else f"destination IN {qlist(mine_inboxes)}"
                         if mine_inboxes else "id IS NULL"),
        "listen_items": "id IS NOT NULL" if listener else "id IN (SELECT item_id FROM intake_items)",
        "listen_judgments": "id IS NOT NULL" if listener else "item_id IN (SELECT id FROM listen_items)",
        # The roster, the sign-in lists and roles, onboarding: for the owner and the Admins. Never the key that
        # hashes roadmap visitors' addresses: with it the small IPv4 space turns every stored hash back into an address.
        # The setup research job (backend/strategy_setup.py) holds the owner's words and an unpublished draft, and the
        # imported company file (backend/company_import.py) its unpublished strategy: the owner's.
        "registry_metadata": ("key!='roadmap_ip_secret'" + ("" if owner else
                                                           " AND key NOT IN ('strategy.draft_from','onboarding.company_file')"))
        if admin and not bot else "0",
        "events": f"({events}) AND " + event_gate,
        "refusals": "(" + ("1" if owner else mine if bot else f"actor={me}") + ") AND " +
                    function("event") + "('',actor,created_at,detail)",
        "meetings": f"({meetings}) AND id NOT IN (SELECT meeting_id FROM {inner('media_control')} WHERE deleted_at IS NOT NULL)",
        "meeting_versions": by_meeting, "meeting_deliveries": by_meeting, "media_assets": by_meeting, "media_control": by_meeting,
        "meeting_items": by_meeting, "meeting_brain": by_meeting, "meeting_comments": by_meeting,
        "import_refs": by_meeting,
        "market_pages": "1",
        "docs": "id IS NOT NULL", "doc_versions": "doc_id IN (SELECT id FROM docs)",
        "linked_docs": "id IS NOT NULL",
        "bot_files": (file_gate + " AND " + (f"bot_id={q(H.actor_id(who.actor))} AND " if bot else "")
                      + f"(CASE WHEN scope LIKE 'task:%' THEN substr(scope,6) IN (SELECT id FROM tasks) "
                        "WHEN scope LIKE 'conversation:%' THEN substr(scope,14) IN (SELECT id FROM conversations) "
                        f"ELSE {bots_visible('bot_id')} END)"),
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
        # Which history rows are settled (backend/read_deps.py): read by the message and run gates, by no caller.
        "read_index": "key IS NULL",
    }
    for table in ("bot_status", "bot_status_history"):
        rules[table] += " AND (task_id IS NULL OR " + by_task + ")"
        # Untagged free-form status can retain the previous private turn's content.
        rules[table] += (" AND NOT EXISTS (SELECT 1 FROM " + inner("tasks") +
                         " t WHERE (t.owner_id='bot:'||bot OR t.requester_id='bot:'||bot) "
                         "AND (t.private IS NULL OR t.private<>0) AND NOT " +
                         function("task") + "(t.id))")
    for table in ("docs", "doc_versions", "linked_docs", "market_pages"):
        cols = columns.get(table, ()) if columns is not None else [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]
        rules[table] = "(" + rules[table] + ") AND " + content_gate(cols)
    rules["task_file_reviews"] = "file_id IN (SELECT id FROM bot_files) OR file_id IN (SELECT id FROM blobs)"
    rules["bot_file_activity"] += " AND (task_id IS NULL OR " + by_task + ") AND (attempt_id IS NULL OR " + by_attempt + ")"
    rules["bot_file_versions"] += " AND (attempt_id IS NULL OR " + by_attempt + ")"
    hidden = {table: set(HIDDEN.get(table, ())) for table in rules}
    if bot:
        hidden["humans"].add("email")
    return {table: (rules[table], hidden[table]) for table in rules}


# Per-request sets, each an indexed temp table only this request's views may read.
SETS = ("visible_tasks", "readable_conversations", "denied_tasks", "unreadable_rooms", "run_carriers", "denied_reads")
RUN_EVENTS = "('task.next-run.carried','attempt.input.moved')"


class Gates:
    """SQL that answers yes, without Python, for a row the privacy functions in backend/privacy_index.py allow.

    Each gate is `(fast OR privacy function)`: a fast yes skips the function and anything else still asks it, so a
    fast clause may only be narrower than the function, never wider. A clause reads small sets built once per request
    (the caller's denied private tasks, the rooms whose plain messages they may not read, the runs that carry a denied
    task) and allows a message only when every message its provenance reaches is settled by its own row: its parent
    and the messages it answers have no parents, answers or inputs of their own, and its run's messages likewise.
    With no denied task, no string needs a lookup at all.

    Where SQLite and Python could read the same JSON differently the clause declines and Python decides: a backslash
    (an escaped string), a repeated key (SQLite reads the first, Python the last), a task given as anything but one
    string, a string that is itself JSON, a meeting reference, a moved input, and long provenance.
    """
    KEYS = ("task", "task_id", "turn_id", "run", "attempt_id", "answers", "inputs")

    def __init__(self, inner, private, denied):
        self.inner, self.denied = inner, bool(denied)
        self.tasks, self.messages = inner("tasks"), inner("messages")
        self.denied_tasks = '"' + private("denied_tasks") + '"'
        self.rooms = '"' + private("unreadable_rooms") + '"'
        self.carriers = '"' + private("run_carriers") + '"'

    def strings(self, value):
        """No string in this JSON names a denied task the way ReadIndex.references finds one."""
        if not self.denied:
            return "1"
        return (f"NOT EXISTS (SELECT 1 FROM json_tree({value}) s WHERE s.type='text' AND ("
                f"s.value IN (SELECT id FROM {self.denied_tasks}) OR instr(s.value,'/task')>0 "
                "OR substr(s.value,1,1) IN ('{','[') OR (substr(s.value,1,5)='task:' "
                f"AND substr(s.value,6) IN (SELECT id FROM {self.denied_tasks}))))")

    def task(self, refs, key):
        """`refs.<key>` is absent, or one string naming a task that exists and is not denied."""
        path = f"'$.{key}'"
        return (f"(json_type({refs},{path}) IS NULL OR (json_type({refs},{path})='text' AND "
                f"json_extract({refs},{path}) IN (SELECT id FROM {self.tasks}) AND "
                f"json_extract({refs},{path}) NOT IN (SELECT id FROM {self.denied_tasks})))")

    def refs(self, refs, top):
        """The row's own refs: an object naming no meeting, no denied string and only known readable tasks.

        Only the message being read (`top`) may name ancestors, a bounded few, and a run, which must itself pass."""
        once = " AND ".join(f"instr(substr({refs},instr({refs},'\"{key}\"')+1),'\"{key}\"')=0" for key in self.KEYS)
        checks = (f"json_valid({refs}) AND json_type({refs})='object' AND instr({refs},'\\')=0 "
                  f"AND instr({refs},'live_meeting')=0 AND {once} "
                  f"AND {self.task(refs, 'task')} AND {self.task(refs, 'task_id')} AND {self.strings(refs)}")
        if not top:
            return (f"(coalesce({refs},'') IN ('','{{}}') OR ({checks} "
                    f"AND instr({refs},'\"answers\"')=0 AND instr({refs},'\"inputs\"')=0))")
        turn, run_id = f"json_extract({refs},'$.turn_id')", f"json_extract({refs},'$.run.attempt_id')"
        checks += (f" AND coalesce(json_type({refs},'$.answers'),'array')='array' "
                   f"AND coalesce(json_type({refs},'$.inputs'),'array')='array' "
                   f"AND coalesce(json_array_length({refs},'$.answers'),0)+coalesce(json_array_length({refs},'$.inputs'),0)<=48 "
                   f"AND CASE WHEN json_type({refs},'$.turn_id') IS NOT NULL "
                   f"THEN json_type({refs},'$.turn_id')='text' AND {self.attempt(turn)} "
                   f"WHEN json_type({refs},'$.run.attempt_id') IS NOT NULL "
                   f"THEN json_type({refs},'$.run.attempt_id')='text' AND {self.attempt(run_id)} ELSE 1 END")
        return f"(coalesce({refs},'') IN ('','{{}}') OR ({checks}))"

    def root(self, alias):
        """A message settled by its own row: no parent, answers or inputs, and (unread here) no run."""
        return (f"coalesce(coalesce({alias}.in_reply_to,'')='' AND {alias}.conversation_id NOT IN "
                f"(SELECT id FROM {self.rooms}) AND {self.refs(alias + '.refs', top=False)}, 0)")

    def message(self, alias="source"):
        """ReadIndex.message: the row, its run, and each message it replies to, answers or took in as input."""
        refs = alias + ".refs"
        ancestors = (f"SELECT {alias}.in_reply_to AS id UNION ALL "
                     f"SELECT value FROM json_each({refs},'$.answers') WHERE type='text' UNION ALL "
                     f"SELECT value FROM json_each({refs},'$.inputs') WHERE type='text'")
        return (f"coalesce({alias}.conversation_id NOT IN (SELECT id FROM {self.rooms}) AND {self.refs(refs, top=True)} "
                f"AND (coalesce({alias}.in_reply_to,'')='' AND coalesce(instr({refs},'\"answers\"'),0)=0 "
                f"AND coalesce(instr({refs},'\"inputs\"'),0)=0 OR NOT EXISTS (SELECT 1 FROM ({ancestors}) ancestor JOIN {self.messages} parent "
                f"ON parent.id=ancestor.id WHERE parent.id<>{alias}.id AND NOT {self.root('parent')})), 0)")

    def attempt(self, aid):
        """ReadIndex.attempt: its message and inputs are settled by their own rows, it carries no denied task and its
        retained run events name none."""
        jobs, attempts, inputs, events = (self.inner(t) for t in ("jobs", "attempts", "attempt_inputs", "events"))
        return (f"coalesce(EXISTS (SELECT 1 FROM {attempts} run JOIN {jobs} job ON job.id=run.job_id "
                f"JOIN {self.messages} root ON root.id=job.message_id WHERE run.id={aid} AND {self.root('root')}) "
                f"AND NOT EXISTS (SELECT 1 FROM {inputs} input JOIN {self.messages} root ON root.id=input.message_id "
                f"WHERE input.attempt_id={aid} AND NOT {self.root('root')}) "
                f"AND {aid} NOT IN (SELECT id FROM {self.carriers}) "
                f"AND NOT EXISTS (SELECT 1 FROM {events} retained WHERE retained.action IN {RUN_EVENTS} "
                f"AND retained.target={aid} AND NOT coalesce(retained.action='task.next-run.carried' AND "
                f"(coalesce(retained.detail,'')='' OR (json_valid(retained.detail) AND "
                f"json_type(retained.detail)='object' AND instr(retained.detail,'\\')=0 AND "
                f"{self.strings('retained.detail')})), 0)), 0)")


def materialize(conn, index, tables, inner, function, private):
    """Build this request's small sets once, then point every predicate at them instead of re-deriving them per row.

    Visible tasks and readable rooms are what a LIMIT 1 would otherwise rebuild for every query: the room predicate
    asks Python about each room. Hidden things are the few denied tasks, unreadable rooms and runs that carry one."""
    def create(name, select, args=()):
        conn.execute(f'CREATE TEMP TABLE "{private(name)}"(id TEXT PRIMARY KEY) WITHOUT ROWID')
        if isinstance(select, str):
            conn.execute(f'INSERT OR IGNORE INTO "{private(name)}" ' + select, args)
        else:
            conn.executemany(f'INSERT OR IGNORE INTO "{private(name)}" VALUES(?)', ((v,) for v in select if v is not None))
        return f'IN (SELECT id FROM "{private(name)}")'

    visible = create("visible_tasks", "SELECT id FROM main.tasks WHERE " + tables["tasks"][0])
    # Reuse the task-id set directly. Going through the tasks view rebuilds
    # the same task list for every nested room lookup inside a privacy UDF.
    tables = {table: (predicate.replace("IN (SELECT id FROM tasks)", visible), hidden)
              for table, (predicate, hidden) in tables.items()}
    tables["tasks"] = ("id " + visible, tables["tasks"][1])
    rooms = create("readable_conversations", "SELECT id FROM main.conversations WHERE " + tables["conversations"][0])
    tables = {table: (predicate.replace("IN (SELECT id FROM conversations)", rooms), hidden)
              for table, (predicate, hidden) in tables.items()}
    tables["conversations"] = ("id " + rooms, tables["conversations"][1])
    create("denied_tasks", sorted(index.denied))
    create("unreadable_rooms", sorted(index.unreadable_rooms()))
    create("run_carriers", sorted(aid for tid, aid in conn.execute(
        "SELECT id,carried_by FROM main.tasks WHERE carried_by IS NOT NULL") if tid in index.denied))
    # Settled history rows that touched one of the caller's denied tasks (backend/read_deps.py). It grows with the
    # rows those tasks reach, a busy private room included: about 35 ms per 100,000 rows here, once per request.
    create("denied_reads", "SELECT key FROM main.read_deps WHERE dep IN (SELECT 't:'||id FROM "
                           f'"{private("denied_tasks")}")')
    gates = Gates(inner, private, index.denied)
    message = function("message") + "(id,conversation_id,refs,in_reply_to)"
    attempt = function("attempt") + "(id)"

    def settled(prefix, fast):
        """A settled row that touched no denied task is read at once; any other row keeps the gate and its walk."""
        key = f"'{prefix}:'||source.id"
        return (f"CASE WHEN {key} IN (SELECT key FROM {inner('read_index')}) AND {key} NOT IN "
                f'(SELECT id FROM "{private("denied_reads")}") THEN 1 ELSE {fast} END')
    # SQLite evaluates the side of an OR without a subquery first, so `fast OR function` asked Python about every
    # row. The fast answer goes in as an argument instead: a fast yes returns before any provenance is read.
    return {table: (predicate.replace(message, function("settled_message") +
                                      f"(id,conversation_id,refs,in_reply_to,{settled('m', gates.message())})")
                    .replace(attempt, function("settled_attempt") + f"(id,{settled('a', gates.attempt('source.id'))})"),
                    hidden)
            for table, (predicate, hidden) in tables.items()}


def connect(path, c, auth, who, trace=None, seconds=None):
    """A read-only connection whose temp views and authorizer carry this caller's visibility.

    `seconds` bounds building this request's visibility sets as `run` bounds the query itself."""
    deadline = None if seconds is None else time.monotonic() + seconds
    prefix = "v" + secrets.token_hex(12) + "_"
    inner = lambda table: f'"{prefix}{table}"'  # noqa: E731
    function = lambda name: f'"{prefix}privacy_{name}"'  # noqa: E731
    private = lambda name: prefix + name  # noqa: E731
    # Internal provenance SELECTs cannot leave authorized prepared statements for callers.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5, isolation_level=None, cached_statements=0, factory=ReadConnection)
    conn.row_factory = sqlite3.Row
    conn.read_cache, conn.private_prefix = {}, prefix
    if trace:
        conn.set_trace_callback(trace)
    trusted = [False]
    try:
        # Predicates, provenance and result rows use one read snapshot.
        conn.execute("BEGIN")
        columns = schema_columns(conn, path)
        tables = guarded(conn, auth, who, inner, function, columns)
        source_tables = set(tables) | {"blob_media", "live_meetings", "live_meeting_access", "live_meeting_bots",
                                       "live_meeting_turns"}
        class Source:
            def __init__(self):
                self.statements = {}

            # Provenance helpers share this request's snapshot cache through the restricted source wrapper.
            @property
            def read_cache(self):
                return conn.read_cache

            # The store's shared row cache (row_clock.parties and access rows). This connection only reads and never
            # publishes, so it shares rows at the clock its own snapshot reads, like a pooled read connection does.
            row_cache = getattr(c, "row_cache", None)
            write_transaction = False

            @property
            def provenance_changes(self):
                return conn.total_changes

            @property
            def in_transaction(self):
                return conn.in_transaction

            @property
            def total_changes(self):
                return conn.total_changes

            def execute(self, statement, args=()):
                source = self.statements.get(statement)
                if source is None:
                    source = re.sub(r'\b(FROM|JOIN)\s+(\w+)', lambda m:
                                    m[1] + ' main."' + m[2] + '"' if m[2] in source_tables else m[0], statement,
                                    flags=re.I)
                    if len(self.statements) < 128:
                        self.statements[statement] = source
                statement = source
                trusted[0] = True
                try:
                    return conn.execute(statement, args)
                finally:
                    trusted[0] = False
        index = ReadIndex(Source(), who, rooms=True)
        functions = {"message": (4, index.message), "attempt": (1, index.attempt),
                     "conversation": (1, index.conversation), "meeting_room": (1, index.meeting_room),
                     "event": (4, index.event), "tag": (2, index.tag), "blob": (1, index.blob),
                     "settled_message": (5, lambda mid, cid, refs, reply, fast:
                                         True if fast else index.message(mid, cid, refs, reply)),
                     "settled_attempt": (2, lambda aid, fast: True if fast else index.attempt(aid)),
                     "task": (1, lambda tid: tid not in index.denied),
                     "content": (-1, lambda *parts: index.content(list(parts)))}
        for name, (arity, fn) in functions.items():
            conn.create_function(prefix + "privacy_" + name, arity, fn, deterministic=True)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 10_000_000)   # no randomblob(1e9) memory bombs
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.execute("PRAGMA busy_timeout=5000")
        exposed = {}
        for table, (_, hidden) in tables.items():
            exposed[table] = [name for name in columns.get(table, ()) if name not in hidden
                              and (table not in SAFE_COLUMNS or name in SAFE_COLUMNS[table])]
            if not exposed[table]:
                raise Problem("schema", f"Table {table} is missing from this database", 500)
            names = ",".join(f'"{column}"' for column in exposed[table])
            conn.execute(f'CREATE TEMP VIEW {inner(table)} AS SELECT {names} FROM main."{table}"')
        if deadline is not None:
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
        tables = materialize(conn, index, tables, inner, function, private)
        conn.set_progress_handler(None, 0)
        masks = {**MASKED, **live_masks(unsettled_watched(conn, inner))}
        for table, (predicate, _) in tables.items():
            masked = masks.get(table, {})
            projection = ",".join(f'({masked[name]}) AS "{name}"' if name in masked else f'source."{name}"'
                                  for name in exposed[table]) if masked else "*"
            conn.execute(f'CREATE TEMP VIEW "{table}" AS SELECT {projection} FROM {inner(table)} AS source WHERE {predicate}')
        # The Assistant creates its table on first use: a view over a table or column not there yet is left out.
        granted = {name: view for name, view in GRANTED.items() if outcome_stats.may_read(c, who)
                   and all(set(GRANTED_SOURCES.get(base, ())) <= set(columns.get(base, ())) for base in view[0])}
        views = {**DERIVED, **granted}
        sources = {table for bases, _ in views.values() for table in bases} & set(GRANTED_SOURCES)
        for table in sources:
            names = ",".join(f'"{column}"' for column in GRANTED_SOURCES[table])
            conn.execute(f'CREATE TEMP VIEW {inner(table)} AS SELECT {names} FROM main."{table}"')
        for name, (bases, select) in views.items():
            conn.execute(f'CREATE TEMP VIEW "{name}" AS ' + select.format(
                **{base: f'"{base}"' for base in bases}, **{"all_" + base: inner(base) for base in bases},
                tool_names=qlist(sorted(outcome_stats.TOOL_NAMES))))
        # Older SQLite versions initialize JSON virtual tables with schema authorization calls.
        # Initialize only these built-ins before installing the read-only authorizer.
        for function in sorted(TABLE_FUNCTIONS):
            conn.execute(f"SELECT value FROM {function}('[]')").fetchall()
        conn.execute("PRAGMA query_only=1")
        # Every other object in the file is off limits, even as a bare `count(*)`.
        denied = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")} | {"sqlite_temp_master"}
        denied -= set(tables) | OPEN
    except sqlite3.OperationalError as exc:
        conn.close()
        if deadline is not None and (time.monotonic() > deadline or "interrupted" in str(exc)):
            raise Problem("timeout", f"Query stopped after {seconds:g} s; narrow it or add a LIMIT", 422) from exc
        raise
    except BaseException:
        conn.close()
        raise
    inner_names = {prefix + table for table in set(tables) | sources}
    sets = {private(name) for name in SETS}

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
                         "market_insights", "market_events", "market_watches", "market_briefings", "market_sizes",
                         "market_figures", "market_sources", "listen_runs", "task_types", "task_steps"}:
                return sqlite3.SQLITE_OK
            # A base table is read only by its own inner view, whose name is this connection's secret.
            if (table in tables or table in sources) and context == prefix + table:
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
            if table in sets:
                return sqlite3.SQLITE_OK if context in tables else sqlite3.SQLITE_DENY
            if table in tables or table in views:
                return sqlite3.SQLITE_OK
            if table in inner_names and context in views:
                return sqlite3.SQLITE_OK if table[len(prefix):] in views[context][0] else sqlite3.SQLITE_DENY
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
    interrupted = False

    def progress():
        nonlocal interrupted
        interrupted = interrupted or time.monotonic() > deadline
        return int(interrupted)

    conn.set_progress_handler(progress, 2000)
    started = time.monotonic()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchmany(cap + 1)
    except sqlite3.OperationalError as exc:
        # SQLite wraps an interrupt from a nested SELECT inside a Python UDF
        # as "user-defined function raised exception". Preserve its cause.
        if interrupted or "interrupted" in str(exc):
            raise Problem("timeout", f"Query stopped after {seconds:g} s; narrow it or add a LIMIT", 422) from exc
        raise Problem("sql", str(exc), 422) from exc
    except (sqlite3.Error, sqlite3.Warning) as exc:
        if interrupted:
            raise Problem("timeout", f"Query stopped after {seconds:g} s; narrow it or add a LIMIT", 422) from exc
        raise Problem("sql", str(exc), 422) from exc
    finally:
        conn.set_progress_handler(None, 0)
    ms = round((time.monotonic() - started) * 1000)
    blobs = any(isinstance(value, bytes) for row in rows for value in row)
    kind = STATEMENT.match(sql)
    explain = bool(kind and kind[1].lower().startswith("explain"))
    result = {"columns": [d[0] for d in cursor.description or ()],
              "rows": [[json_value(value).replace(conn.private_prefix, "") if isinstance(value, str) and
                        explain else json_value(value)
                        for value in row] for row in rows[:cap]],
              "row_count": min(len(rows), cap), "truncated": len(rows) > cap, "ms": ms}
    if blobs:
        result["note"] = "BLOB values are base64"
    return result


def install_sql(app, store, auth):
    audit = Audit(store)
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
                    row = c.execute("SELECT owner_id,platform FROM runners WHERE id=?", (who.runner_id,)).fetchone()
                    if not str(row["platform"] or "").startswith("darwin"):
                        raise Problem("forbidden", "Only a Mac's runner credential reads as its operator; "
                                                   "use a personal API token", 403)
                    principal = auth.identity_for_actor(c, "human:" + row["owner_id"])
                    detail["as"] = principal.actor
                cap, seconds = LIMITS[principal.role]
                if body.max_rows:
                    cap = min(cap, body.max_rows)
                conn = connect(store.settings.db_path, c, auth, principal, seconds=seconds)
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
            audit.record(who.actor, detail)
