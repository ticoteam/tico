#!/usr/bin/env python3
"""hub.db: the schema, the write layer and its rules, and the read helpers.

The contract is `docs/history/hub-v2.md` §0-§5. This module is the whole database layer: the cloud
service (`backend/store.py` and everything behind it) goes through it and nowhere else. Pure
stdlib, one file. It grew up under the local keeper, which is the only host there is.

Shape of every write:

    f(conn, actor, ...) -> row dict          # `actor` first after the connection
    raises Refused(rule, detail, severity)   # also written to `refusals`, never stored

`actor` is `"bot:<slug>"`, `"human:<id>"` or `"keeper"`. Every write appends an `events` row.
Nothing is edited or deleted (rule 9): tasks and status carry their own history tables. A plain task
comment's author may change or take it back: the row stays marked, without retaining
withdrawn text in the audit trail.

    conn = connect()                         # <projects>/runtime/hub.db, or $HUB_DB
    sync_registry(conn, employees, people)   # bots and humans from the registry

## Deviations from docs/history/hub-v2.md (also listed in that file's "Deviations" section)

1. **`conn` first.** §4 writes the signature as `say(actor, ...)`. There is no ambient
   connection, so every function is `say(conn, actor, ...)`; `actor` is still the first
   argument the caller thinks about, and every other call in §1 puts `conn` first too. Read
   helpers are `tasks(conn, owner=...)` and so on.
2. **No token helpers here.** The §1 `issue_token` and `verify_bot` are not in this module. A
   bot's token hash lives on its `agents` row (`backend/agents.py`), where auth reads it.
3. **Lint (rule 7) applies to unsolicited human items.** A bot's `say`/`notice` to a human is
   linted only when rule 4 counts it as unsolicited; a reply to the human's own message,
   a reply inside a conversation the human started, and `answer`, are exempt. Every task with
   a human owner is linted. Without this carve-out a chat reply longer
   than 120 words would be refused, which
   would break S1; the handoffs rule the lint comes from is about *asking* a person for
   something, not about answering them.
4. **`status_set(..., state="active")`** is accepted although `active` is not a `bot_status`
   state: it is how §4 rule 8 says a human clears a quarantine. It sets `bots.state=active`
   and `bot_status.state=idle`.
5. **Rule names.** §4 names `reach`, `cap`, `depth`, `unsolicited`, `one-question`,
   `duplicate`, `lint`. The rest are `identity` (acting as someone else), `kind` (a bad kind or
   value), `quarantined` (a quarantined bot's writes), `not-found`, and the task engine's
   `task_*` rules.
6. **Severity is classified here, not passed in.** `classify()` reads the body or payload and
   returns `sensitive` (money, an outbound send, access, a human's inbox) or `normal`. Words
   that name a `secrets/` path or another bot's folder are written as they are and recorded
   (`path_mentioned`); they are never refused or counted. Refusals remain in the audit; repeated
   failures open an internal review. Generic refusal diagnostics do not create human decision tasks.
7. **`sync_registry` never lowers a state.** A bot the registry calls `active` that hubdb has
   `quarantined` stays quarantined; only a human clears it. Tokens, thread ids and
   `last_turn_at` are never overwritten by a sync.
8. **Extra read helpers.** `bot(conn, slug)`, `human(conn, id)`, `task(conn, id)`, `task_history(conn, id)`,
   `message(conn, id)`, `conversation(conn, id)`, `refusals_for(...)`,
   `undelivered(conn, to_actor=None)`.
"""

import contextvars
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from pathlib import Path
from collections.abc import Iterable
from typing import Any, NoReturn

from .batch_work import isolated
from . import idempotency_messages
from . import task_relations as TR
from .task_rights import represents as task_represents, participant as task_participant, mover as task_mover
from . import task_roles as TRo
from . import run_triggers as RT
from . import status_values as SV
from backend.chat_goals_schema import SCHEMA as CHAT_GOALS_SCHEMA
from backend.repositories_schema import SCHEMA as REPOSITORIES_SCHEMA
from backend.subscriptions_schema import SCHEMA as SUBSCRIPTIONS_SCHEMA
from backend.triggers_schema import SCHEMA as TRIGGERS_SCHEMA, SIGNING_SCHEMA as TRIGGER_SIGNING_SCHEMA
from backend.rules_schema import SCHEMA as RULE_EVENTS_SCHEMA
from backend.triggers_incoming_schema import (
    SCHEMA as TRIGGERS_INCOMING_SCHEMA,
    CONSUMER_SCHEMA as TRIGGERS_CONSUMER_SCHEMA,
)
from backend.hook_relay_schema import SCHEMA as HOOK_RELAY_SCHEMA
from backend.webhooks_schema import SCHEMA as WEBHOOKS_SCHEMA
from backend.sleep_idle_schema import SCHEMA as SLEEP_IDLE_SCHEMA
from backend.tico_notices_schema import SCHEMA as TICO_NOTICES_SCHEMA
from backend.postal_mail_schema import (
    SCHEMA as POSTAL_MAIL_SCHEMA,
    V2 as POSTAL_MAIL_V2_SCHEMA,
    V3 as POSTAL_MAIL_V3_SCHEMA,
)
from backend.webhooks_v2_schema import SCHEMA as WEBHOOKS_V2_SCHEMA
from backend.webhooks_v3_schema import SCHEMA as WEBHOOKS_V3_SCHEMA
from backend.webhooks_v4_schema import SCHEMA as WEBHOOKS_V4_SCHEMA
from backend.webhooks_v5_schema import SCHEMA as WEBHOOKS_V5_SCHEMA
from backend.webhooks_v6_schema import SCHEMA as WEBHOOKS_V6_SCHEMA
from . import bot_settings as BS
from . import sources as SRC

from .hubdb_core import (
    KEEPER,
    bot_actor,
    human_actor,
    actor_kind,
    actor_id,
    is_bot,
    is_human,
)
from .hubdb_text import (
    LINT_MAX_WORDS,  # noqa: F401
    LINT_CODES,
    LINT_IDENT,  # noqa: F401
    LINT_CAPS,  # noqa: F401
    LINT_CAPS_OK,  # noqa: F401
    LINT_JARGON,
    LINT_VERBS,  # noqa: F401
    LINT_NOT_VERBS,  # noqa: F401
    _outside_quotes,  # noqa: F401
    lint_human_item,
    lint_human_title,
    lint_title,
    LINT_HEX_ID,  # noqa: F401
    LINT_CURRENCY,  # noqa: F401
    LINT_NAMED_NUMBER,  # noqa: F401
    LINT_PR_REF,  # noqa: F401
    LINT_BARE_REF,  # noqa: F401
    LINT_ASK_WORDS,  # noqa: F401
    LINT_ASK_EXAMPLE,  # noqa: F401
    LINT_S3,  # noqa: F401
    LINT_PATH,  # noqa: F401
    LINT_TOOL,  # noqa: F401
    LINT_SENTENCE_END,  # noqa: F401
    LINT_ABBREV,  # noqa: F401
    ask_sentences,  # noqa: F401
    lint_sentences,  # noqa: F401
    lint_person_ask,  # noqa: F401
    LINT_IF_YES,  # noqa: F401
    LINT_NEGATIVE_CONDITION,  # noqa: F401
    LINT_OUTCOME,  # noqa: F401
    LINT_REQUEST,  # noqa: F401
    LINT_LEAD_IN,  # noqa: F401
    person_ask_rewrite,  # noqa: F401
    LINT_ERROR,  # noqa: F401
    lint_plain,  # noqa: F401
    person_ask_of_body,
    person_ask_refusal,
    LINT_LISTED_PICKS,  # noqa: F401
    lint_question,  # noqa: F401
    QUESTION_EXAMPLE,  # noqa: F401
    question_refusal,
    lint_approval,  # noqa: F401
    URL_SPECIAL,  # noqa: F401
    url_authority,  # noqa: F401
    lint_support,  # noqa: F401
    support_text_matches,  # noqa: F401
    LIBRARIAN_LITERAL,  # noqa: F401
    JARGON,  # noqa: F401
    RUNNER,  # noqa: F401
    librarian_text,
)

HUB_DIR = Path(__file__).resolve().parent.parent
ROOT = HUB_DIR.parent  # employees are siblings of the hub
DEFAULT_DB = ROOT / "runtime" / "hub.db"


def task_news(sender_id, refs):
    """A keeper message about a task: started, finished. It is the task's own history,
    shown on the task, and it never reaches a person (no delivery, so no unread, Inbox row, digest or push): people are
    not chatted about their own tasks. Bots still get it, as before."""
    return sender_id == KEEPER and isinstance(refs, dict) and bool(refs.get("task"))


def task_news_sql(m="m"):
    """SQL: the messages row `m` is task news (`task_news`)."""
    refs = f"CASE WHEN json_valid({m}.refs) THEN {m}.refs END"
    return f"({m}.sender_id='{KEEPER}' AND COALESCE(json_extract({refs},'$.task'),'')<>'')"


# A bot's posts in a task's own thread reach a person only for these reasons: an @mention, a reply to them, or
# following the task (`counts_for_person`). Rows written for any other reason before followers did not count, and
# still do not.
BOT_TASK_KINDS = ("say", "notice", "ask")
BOT_TASK_REASONS = ("mention", "reply", "follower")


def task_post_sql(m="m"):
    """SQL: the messages row `m` is a bot's say, notice or ask in some task's own thread (the thread is a task's
    conversation_id, which tasks_conversation indexes)."""
    return (
        f"({m}.sender_id LIKE 'bot:%' AND {m}.kind IN ('say','notice','ask') "
        f"AND EXISTS (SELECT 1 FROM tasks t WHERE t.conversation_id={m}.conversation_id))"
    )


def delivery_counts_sql(m, recipient, reason):
    """SQL: a delivery of message `m` to `recipient`, whose reason is the SQL `reason`, is a reason for a person to see
    it. Not when the recipient is a person and the message is task news (`task_news_sql`), nor when it is a bot's post
    in a task's own thread (`task_post_sql`) for a reason outside `BOT_TASK_REASONS`. One rule for the write side
    (threads' primary delivery trigger, `counts_for_person`) and every read side (unread, the Inbox, the sidebar,
    digests), so deliveries already made stop counting without a migration."""
    kept = ",".join(f"'{r}'" for r in BOT_TASK_REASONS)
    return (
        f"NOT ({recipient} LIKE 'human:%' AND ({task_news_sql(m)} "
        f"OR ({task_post_sql(m)} AND COALESCE({reason},'') NOT IN ({kept}))))"
    )


def counts_for_person(conn, message, reason, conversation_id):
    """The Python side of `delivery_counts_sql`: whether a delivery of `message` to a person, for `reason`, in
    conversation `conversation_id`, is written. False for task news, and for a bot's post in a task's thread for a
    reason outside `BOT_TASK_REASONS`. Used by threads._insert_delivery."""
    sender = str(message.get("sender_id") or "")
    if task_news(sender, message.get("refs")):
        return False
    if (
        sender.startswith("bot:")
        and message.get("kind") in BOT_TASK_KINDS
        and reason not in BOT_TASK_REASONS
        and conn.execute("SELECT 1 FROM tasks WHERE conversation_id=? LIMIT 1", (conversation_id,)).fetchone()
    ):
        return False
    return True


REVIEW_OWNER = "assistant"  # rule 8's first review task
FLEET_MAINTAINER = "botops"  # the fleet's maintainer bot: reviews refused writes and may start any bot's task
BOT_STATES = SV.BOT_STATES
STATUS_STATES = SV.BOT_STATUS_STATES
MESSAGE_KINDS = ("say", "notice", "steer")
# Kinds old rows may still have, read as history only: nothing writes an ask or an answer in a message (a question
# to a person is an ask on a task, `tico task ask`; to a bot, a DM flagged needs-action).
OLD_MESSAGE_KINDS = ("ask", "answer")
ASK_GONE = (
    "asks are gone: ask a person on a task with tico task ask <task> --to <person> \"<question>\" --version N; "
    "ask a bot with tico message send <bot> \"<question>\" --needs-action"
)
CONVERSATION_KINDS = ("chat", "ask", "task", "notice")
CONVERSATION_SCOPES = ("direct", "personal", "shared", "task")
# A task is open, done or closed. Where it stands in its pipeline and what it waits on are the engine's
# (task_engine, waiting_on), never a status.
TASK_STATUSES = ("open", "done", "closed")
ACTIVE_STATUSES = ("open",)  # not yet finished
TASK_ENDED = {"done": "marked done", "closed": "closed"}
# The numbered start-up step that seeded the old General type's steps (PIPELINES_SCHEMA) reads this list, as it was.
STEP_STATUSES = ("open", "doing", "review", "ready", "done", "closed")
# The product lane is retired. Old rows may still say 'product' (so it is
# still a lane to read and filter), but every new or moved task is company work.
TASK_LANES = ("company", "product")
LANE_TEAMS = ("product", "engineering")  # store.py's one-time lane migration (history)
# People who may move any task: change its state, rank, lane, owner, labels, parent, blockers or
# parent. Keyed on the roster team in
# registry/people.yaml, so adding a mover is a roster edit. The environment owner always may.
MOVER_TEAMS = ("leadership", "product", "engineering")
# Labels are a mover's too, except that a bot may change a classification label on a task it manages
# (_label_writer). Labels Tico acts on stay a mover's: a task goal (execution.task_goal) ...
WORKFLOW_LABELS = ("goal:on", "goal:off")
# ...and a label on a public roadmap (roadmaps.settings), which roadmaps.approve checks for a bot.
# A label refusal counts toward a quarantine once per task in this window (_escalate), so a bot
# retrying a refusal it already had cannot quarantine itself in seconds.
LABEL_REFUSAL_WINDOW_S = 3600
LINK_KINDS = ("pr", "issue", "url", "doc", "worktree", "session")
TITLE_LINT = os.environ.get("TICO_TITLE_LINT", "warn")  # warn | refuse | off
# Rule 7's writing checks on an item for a person and the shape of an update: warned about with
# the write accepted, refused only when TICO_STYLE_LINT=refuse. Bots lost whole runs rewriting
# for them. A missing title is structural and always refused.
STYLE_LINT = os.environ.get("TICO_STYLE_LINT", "warn")  # warn | refuse | off
CAP_PER_HOUR = 20  # rule 3: messages in one conversation between bots, per hour
UNSOLICITED_PER_DAY = int(os.environ.get("TICO_UNSOLICITED_PER_DAY", "10"))  # rule 4: per human, per bot, per UTC day
HELD_QUEUE_ITEMS = int(os.environ.get("TICO_HELD_QUEUE_ITEMS", "200"))
HELD_QUEUE_BYTES = int(os.environ.get("TICO_HELD_QUEUE_BYTES", "2097152"))
HELD_QUEUE_SENDER_ITEMS = int(os.environ.get("TICO_HELD_QUEUE_SENDER_ITEMS", "50"))
HELD_QUEUE_SENDER_BYTES = int(os.environ.get("TICO_HELD_QUEUE_SENDER_BYTES", "524288"))
REVIEW_AT = 3  # rule 8: refusals in a day that open a review task
QUARANTINE_AT = 10  # rule 8: repeated refusals in a day that hold the task/chat or quarantine the bot
QUARANTINE_TOTAL_AT = 3 * QUARANTINE_AT  # rule 8: any refusals in a day, repeated or not (a loop over new targets)
NOTICE_DAYS = 14  # how long a notice stays in the inbox

# ----------------------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
  id TEXT PRIMARY KEY NOT NULL, slug TEXT NOT NULL UNIQUE, display_name TEXT, runtime TEXT, model TEXT, effort TEXT,
  cwd TEXT, host TEXT DEFAULT 'keeper', thread_id TEXT, token_hash TEXT,
  state TEXT DEFAULT 'active', created_at TEXT, last_turn_at TEXT);

CREATE TABLE IF NOT EXISTS humans (
  id TEXT PRIMARY KEY, name TEXT, email TEXT, slack_id TEXT, teams TEXT);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, kind TEXT, subject TEXT, task_id TEXT, participants TEXT,
  created_at TEXT, last_message_at TEXT, closed_at TEXT);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id),
  sender_id TEXT, recipient_id TEXT, kind TEXT, body TEXT, refs TEXT, in_reply_to TEXT,
  created_at TEXT, delivered_at TEXT, read_at TEXT, expires_at TEXT, wait_s INTEGER);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT, body TEXT, requester_id TEXT, owner_id TEXT, status TEXT,
  due_at TEXT, parent_id TEXT, conversation_id TEXT,
  created_at TEXT, updated_at TEXT, done_at TEXT, closed_at TEXT, closed_by_id TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS task_events (
  id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), created_at TEXT, actor TEXT,
  field TEXT, old TEXT, new TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, kind TEXT, task_id TEXT, message_id TEXT, payload TEXT,
  payload_hash TEXT, requested_by_id TEXT, decided_by_id TEXT, decision TEXT,
  decided_at TEXT, consumed_at TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS bot_status (
  bot TEXT PRIMARY KEY, state TEXT, focus TEXT, task_id TEXT, changed_at TEXT, last_turn_at TEXT,
  last_result TEXT, next_due_at TEXT, open_tasks INTEGER DEFAULT 0, needs_human INTEGER DEFAULT 0,
  updated_at TEXT);

CREATE TABLE IF NOT EXISTS bot_status_history (
  id TEXT PRIMARY KEY, bot TEXT, state TEXT, focus TEXT, task_id TEXT,
  started_at TEXT, ended_at TEXT, reason TEXT, by TEXT);

CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, bot TEXT, cron TEXT, title TEXT, playbook TEXT,
  last_fired_at TEXT, next_due_at TEXT);

CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY, bot TEXT, thread_id TEXT, started_at TEXT, finished_at TEXT, trigger TEXT,
  message_id TEXT, task_id TEXT, exit TEXT, summary TEXT);

CREATE TABLE IF NOT EXISTS deltas (
  id TEXT PRIMARY KEY, turn_id TEXT, seq INTEGER, kind TEXT, text TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS rate_limits (
  runtime TEXT PRIMARY KEY, used_percent REAL, window_minutes INTEGER,
  resets_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS refusals (
  id TEXT PRIMARY KEY, created_at TEXT, actor TEXT, rule TEXT, detail TEXT, severity TEXT);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, created_at TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);

CREATE INDEX IF NOT EXISTS messages_to_delivered ON messages(recipient_id, delivered_at);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS events_ts ON events(created_at);
CREATE INDEX IF NOT EXISTS deltas_turn ON deltas(turn_id, seq);
CREATE INDEX IF NOT EXISTS refusals_actor_ts ON refusals(actor, created_at);
CREATE INDEX IF NOT EXISTS status_history_bot ON bot_status_history(bot, started_at);
CREATE INDEX IF NOT EXISTS turns_bot_started ON turns(bot, started_at);
"""

from .meetings_schema import (
    MEETING_BRAIN_SCHEMA,
    MEETING_COMMENTS_SCHEMA,
    MEETING_ITEMS_SCHEMA,
    MEETING_SCHEMA,
    RECORDING_SOURCES_SCHEMA,
    MEETING_REVIEW_SCHEMA,
    LIVE_MEETINGS_SCHEMA,
    LIVE_MEETINGS_ROUTING_LIMITS_SCHEMA,
    LIVE_MEETINGS_CHAT_ROUTING_SCHEMA,
    LIVE_MEETING_ACCESS_SCHEMA,
    LIVE_MEETINGS_CLIENT_SCHEMA,
    LIVE_MEETINGS_MODEL_SCHEMA,
    LIVE_MEETING_BOT_CONVERSATION_SCHEMA,
    LIVE_MEETING_TURN_RETRY_SCHEMA,
)

# Goals and KPIs (backend/goals.py). A task may name the goal
# it serves; the column is optional and nothing requires it.
GOALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, owner_id TEXT NOT NULL, parent_id TEXT,
  body TEXT NOT NULL DEFAULT '', status TEXT, status_note TEXT NOT NULL DEFAULT '',
  status_by_id TEXT, status_at TEXT, rank INTEGER,
  last_read_at TEXT, last_read_by_id TEXT,
  created_at TEXT NOT NULL, created_by_id TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS goals_owner ON goals(owner_id);
CREATE INDEX IF NOT EXISTS goals_parent ON goals(parent_id);

CREATE TABLE IF NOT EXISTS goal_events (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL,
  field TEXT NOT NULL, old TEXT, new TEXT, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS goal_events_goal ON goal_events(goal_id, created_at);

CREATE TABLE IF NOT EXISTS kpis (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '',
  target REAL, created_at TEXT NOT NULL, created_by_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kpis_goal ON kpis(goal_id);

CREATE TABLE IF NOT EXISTS kpi_readings (
  id TEXT PRIMARY KEY, kpi_id TEXT NOT NULL, measured_at TEXT NOT NULL, value REAL NOT NULL,
  actor_id TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'measured', note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kpi_readings_kpi ON kpi_readings(kpi_id, measured_at);
ALTER TABLE tasks ADD COLUMN goal_id TEXT;
"""

# KPIs as records of their own, and how goals are coloured (backend/kpis.py, backend/goals.py). A KPI
# no longer belongs to one goal: `goal_kpis` links a goal to a KPI and holds the target (an improvement
# from a baseline to a value by a deadline, or a range to stay inside). Readings keep the business time
# (`period_start`/`period_end`) apart from `collected_at`, and a correction is a new reading that
# `supersedes` the old one. The old `kpis.goal_id` and `kpis.target` are copied into `goal_kpis` here; migration 0044
# drops `goal_id` (and its index), and `kpis.target` stays, read by nothing. Rows that existed before this migration
# are recognised by `owner IS NULL`.
KPIS_SCHEMA = """
ALTER TABLE kpis ADD COLUMN slug TEXT;
ALTER TABLE kpis ADD COLUMN definition TEXT NOT NULL DEFAULT '';
ALTER TABLE kpis ADD COLUMN direction TEXT NOT NULL DEFAULT 'up';
ALTER TABLE kpis ADD COLUMN cadence TEXT NOT NULL DEFAULT 'weekly';
ALTER TABLE kpis ADD COLUMN owner_id TEXT;
ALTER TABLE kpis ADD COLUMN source_note TEXT NOT NULL DEFAULT '';
ALTER TABLE kpis ADD COLUMN definition_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE kpis ADD COLUMN updated_at TEXT;
ALTER TABLE kpi_readings ADD COLUMN period_start TEXT;
ALTER TABLE kpi_readings ADD COLUMN period_end TEXT;
ALTER TABLE kpi_readings ADD COLUMN collected_at TEXT;
ALTER TABLE kpi_readings ADD COLUMN evidence TEXT NOT NULL DEFAULT '';
ALTER TABLE kpi_readings ADD COLUMN quality TEXT NOT NULL DEFAULT 'measured';
ALTER TABLE kpi_readings ADD COLUMN definition_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE kpi_readings ADD COLUMN supersedes TEXT;
CREATE INDEX IF NOT EXISTS kpi_readings_supersedes ON kpi_readings(supersedes);
ALTER TABLE goals ADD COLUMN status_source TEXT;
ALTER TABLE goals ADD COLUMN suggest_status TEXT;
ALTER TABLE goals ADD COLUMN suggest_note TEXT;
ALTER TABLE goals ADD COLUMN suggest_at TEXT;
ALTER TABLE goal_events ADD COLUMN status_by TEXT;
ALTER TABLE goal_events ADD COLUMN status_source TEXT;

CREATE TABLE IF NOT EXISTS goal_kpis (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, kpi_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'none',
  baseline REAL, baseline_at TEXT, target REAL, deadline TEXT, min REAL, max REAL,
  created_at TEXT NOT NULL, created_by_id TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by_id TEXT NOT NULL,
  UNIQUE (goal_id, kpi_id));
CREATE INDEX IF NOT EXISTS goal_kpis_kpi ON goal_kpis(kpi_id);

CREATE TABLE IF NOT EXISTS kpi_definitions (
  id TEXT PRIMARY KEY, kpi_id TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL,
  name TEXT NOT NULL, definition TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL, cadence TEXT NOT NULL, source_note TEXT NOT NULL DEFAULT '',
  UNIQUE (kpi_id, version));

CREATE TABLE IF NOT EXISTS goal_checkins (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, kpi_id TEXT, created_at TEXT NOT NULL, author TEXT NOT NULL,
  sender_id TEXT NOT NULL, body TEXT NOT NULL, signal TEXT);
CREATE INDEX IF NOT EXISTS goal_checkins_goal ON goal_checkins(goal_id, created_at);

CREATE TABLE IF NOT EXISTS goal_proposals (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, goal_id TEXT, kpi_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '', proposed_by TEXT NOT NULL, proposed_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, decided_at TEXT, decision_note TEXT NOT NULL DEFAULT '',
  result TEXT);
CREATE INDEX IF NOT EXISTS goal_proposals_status ON goal_proposals(status, proposed_at);

UPDATE kpi_readings SET period_end=measured_at, period_start=measured_at, collected_at=created_at WHERE period_end IS NULL;
UPDATE kpi_readings SET quality='estimate' WHERE source='estimate' AND quality='measured' AND supersedes IS NULL AND period_start=period_end;
UPDATE kpis SET owner_id=COALESCE((SELECT owner_id FROM goals WHERE goals.id=kpis.goal_id), 'company'),
  cadence='monthly', updated_at=created_at WHERE owner_id IS NULL;
INSERT OR IGNORE INTO goal_kpis (id, goal_id, kpi_id, kind, target, created_at, created_by_id, updated_at, updated_by_id)
  SELECT id, goal_id, id, CASE WHEN target IS NULL THEN 'none' ELSE 'improve' END, target, created_at, created_by_id,
         created_at, created_by_id FROM kpis WHERE goal_id != '' AND goal_id IN (SELECT id FROM goals);
INSERT OR IGNORE INTO kpi_definitions (id, kpi_id, version, created_at, actor, name, definition, unit, direction, cadence, source_note)
  SELECT id || ':1', id, 1, created_at, created_by_id, name, definition, unit, direction, cadence, source_note FROM kpis;
UPDATE goals SET status_source='person' WHERE status IS NOT NULL AND status_source IS NULL;
UPDATE goal_events SET status_by=actor, status_source='person' WHERE field='status' AND status_source IS NULL;
"""

KPI_ARCHIVE_SCHEMA = """
ALTER TABLE kpis ADD COLUMN archived_at TEXT;
ALTER TABLE kpis ADD COLUMN archived_by_id TEXT;
"""

# The shared market model (backend/market.py).
# Six tables plus an FTS index. Rows are retired, merged or ended, never deleted.
MARKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_entities (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  aliases TEXT NOT NULL DEFAULT '[]',
  external_ids TEXT NOT NULL DEFAULT '{}',
  tier TEXT,
  summary TEXT NOT NULL DEFAULT '',
  properties TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  merged_into TEXT,
  last_verified_at TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS market_edges (
  id TEXT PRIMARY KEY,
  src TEXT NOT NULL,
  rel TEXT NOT NULL,
  dst TEXT NOT NULL,
  valid_from_on TEXT,
  valid_until_on TEXT,
  confidence TEXT NOT NULL DEFAULT 'medium',
  properties TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS market_edges_src ON market_edges(src, rel);
CREATE INDEX IF NOT EXISTS market_edges_dst ON market_edges(dst, rel);
CREATE INDEX IF NOT EXISTS market_edges_until ON market_edges(valid_until_on);

CREATE TABLE IF NOT EXISTS market_evidence (
  id TEXT PRIMARY KEY,
  source_url TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT 'other',
  captured_at TEXT,
  captured_by TEXT NOT NULL,
  quote TEXT NOT NULL DEFAULT '',
  our_read TEXT NOT NULL DEFAULT '',
  blob_id TEXT);

CREATE TABLE IF NOT EXISTS market_citations (
  claim_kind TEXT NOT NULL,
  claim_id TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  PRIMARY KEY (claim_kind, claim_id, evidence_id));
CREATE INDEX IF NOT EXISTS market_citations_evidence ON market_citations(evidence_id);

CREATE TABLE IF NOT EXISTS market_insights (
  id TEXT PRIMARY KEY,
  reported_by TEXT NOT NULL,
  reported_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  about TEXT NOT NULL DEFAULT '',
  claim TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  quote TEXT NOT NULL DEFAULT '',
  confidence TEXT NOT NULL DEFAULT 'medium',
  urgent INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  resolution TEXT NOT NULL DEFAULT '',
  applied_events TEXT NOT NULL DEFAULT '[]',
  resolved_at TEXT,
  resolved_by TEXT,
  filed_task TEXT);
CREATE INDEX IF NOT EXISTS market_insights_status ON market_insights(status, reported_at);

CREATE TABLE IF NOT EXISTS market_events (
  id TEXT PRIMARY KEY,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  field TEXT NOT NULL,
  old TEXT,
  new TEXT,
  insight_id TEXT,
  note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS market_events_subject ON market_events(subject_kind, subject_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS market_fts USING fts5(
  kind UNINDEXED,
  ref UNINDEXED,
  name,
  aliases,
  summary,
  quote,
  our_read,
  tokenize = 'unicode61 remove_diacritics 1');

CREATE TRIGGER IF NOT EXISTS market_entities_ai AFTER INSERT ON market_entities BEGIN
  INSERT INTO market_fts(kind, ref, name, aliases, summary, quote, our_read)
  VALUES ('entity', new.id, new.name, new.aliases, new.summary, '', '');
END;
CREATE TRIGGER IF NOT EXISTS market_entities_ad AFTER DELETE ON market_entities BEGIN
  DELETE FROM market_fts WHERE kind='entity' AND ref=old.id;
END;
CREATE TRIGGER IF NOT EXISTS market_entities_au AFTER UPDATE ON market_entities BEGIN
  DELETE FROM market_fts WHERE kind='entity' AND ref=old.id;
  INSERT INTO market_fts(kind, ref, name, aliases, summary, quote, our_read)
  VALUES ('entity', new.id, new.name, new.aliases, new.summary, '', '');
END;
CREATE TRIGGER IF NOT EXISTS market_evidence_ai AFTER INSERT ON market_evidence BEGIN
  INSERT INTO market_fts(kind, ref, name, aliases, summary, quote, our_read)
  VALUES ('evidence', new.id, '', '', '', new.quote, new.our_read);
END;
CREATE TRIGGER IF NOT EXISTS market_evidence_ad AFTER DELETE ON market_evidence BEGIN
  DELETE FROM market_fts WHERE kind='evidence' AND ref=old.id;
END;
CREATE TRIGGER IF NOT EXISTS market_evidence_au AFTER UPDATE ON market_evidence BEGIN
  DELETE FROM market_fts WHERE kind='evidence' AND ref=old.id;
  INSERT INTO market_fts(kind, ref, name, aliases, summary, quote, our_read)
  VALUES ('evidence', new.id, '', '', '', new.quote, new.our_read);
END;
"""

# A person's reply closes whatever that bot asked them. `answered_by` is the message that did it.
REPLY_ANSWERS_ASKS = """
ALTER TABLE messages ADD COLUMN answered_by_id TEXT;
-- Stays after step 6d: sender and recipient lookups use it (teammate_access, triggers, assistant, auth).
CREATE INDEX IF NOT EXISTS messages_open_asks ON messages(recipient_id, sender_id, kind, answered_by_id);
"""

# A market insight names what it was filed from in `source_ref`, once per reporter. (The listening tables this once
# made are gone: backend/migrations/20261111190425_listening_feed.py.)
LISTENING_SCHEMA = """
ALTER TABLE market_insights ADD COLUMN source_ref TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS market_insights_source_ref
  ON market_insights(reported_by, source_ref) WHERE source_ref IS NOT NULL;
"""

# Usage (backend/usage.py): what a run's tokens were, on which model, and what they cost at list price.
# `billing` is `subscription` for a run on a ChatGPT or Claude sign-in, whose cost is an API-equivalent
# and not money spent. Every statement is safe to run twice.
USAGE_SCHEMA = """
ALTER TABLE turns ADD COLUMN input_tokens INTEGER;
ALTER TABLE turns ADD COLUMN cached_tokens INTEGER;
ALTER TABLE turns ADD COLUMN output_tokens INTEGER;
ALTER TABLE turns ADD COLUMN model TEXT;
ALTER TABLE turns ADD COLUMN provider TEXT;
ALTER TABLE turns ADD COLUMN est_cost_usd REAL;
ALTER TABLE turns ADD COLUMN billing TEXT;
CREATE INDEX IF NOT EXISTS turns_started ON turns(started_at);
"""

# Spend limits (backend/usage_limits.py): a bot's own daily and monthly cap in estimated USD (null: follow the
# company default), and the warnings already sent, one per bot, period, limit and level.
USAGE_LIMITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_limits (
  bot TEXT PRIMARY KEY, daily_usd REAL, monthly_usd REAL, updated_at TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS usage_alerts (
  bot TEXT NOT NULL, period TEXT NOT NULL, level INTEGER NOT NULL, sent TEXT NOT NULL,
  PRIMARY KEY (bot, period, level));
"""

TAGS_TABLES_SCHEMA = """
ALTER TABLE tasks ADD COLUMN labels TEXT NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}', markdown TEXT NOT NULL DEFAULT '',
  is_template INTEGER NOT NULL DEFAULT 0, template_id TEXT REFERENCES tags(id),
  owner TEXT, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_tags (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES tags(id), PRIMARY KEY(task_id, tag_id));
CREATE INDEX IF NOT EXISTS task_tags_tag ON task_tags(tag_id, task_id);
"""

TAGS_BACKFILL = """
INSERT OR IGNORE INTO tags(id,key,label,created_at,updated_at)
  SELECT 'tag-'||lower(hex(randomblob(16))), value, value,
         strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
  FROM (SELECT DISTINCT value FROM tasks, json_each(tasks.labels) WHERE type='text' AND value<>'');
INSERT OR IGNORE INTO task_tags(task_id,tag_id)
  SELECT tasks.id,tags.id FROM tasks,json_each(tasks.labels) old
  JOIN tags ON tags.key=old.value;
"""

TAGS_SCHEMA = TAGS_TABLES_SCHEMA + TAGS_BACKFILL

GENERAL_TYPE = "general"
GENERAL_PIPELINE = "general"  # the pipeline a task goes into when none is named
# Numbered step 14 made the old engine's task types and steps. A database that ran it has them until the Pipelines v3
# cut-over drops them; a new database never makes them (only a database with no tasks runs these steps). The two
# columns stay: a shipped migration file reads them, and the cut-over drops them with the old tasks table.
PIPELINES_SCHEMA = """
ALTER TABLE tasks ADD COLUMN type_id TEXT;
ALTER TABLE tasks ADD COLUMN step_id TEXT;
"""

TASK_LINKS_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created_at TEXT NOT NULL,
  pr_sha TEXT, pr_merged_at TEXT);
ALTER TABLE task_links ADD COLUMN repo TEXT;
ALTER TABLE task_links ADD COLUMN number INTEGER;
ALTER TABLE task_links ADD COLUMN branch TEXT;
ALTER TABLE task_links ADD COLUMN computer_id TEXT;
ALTER TABLE task_links ADD COLUMN path TEXT;
ALTER TABLE task_links ADD COLUMN checks TEXT;
ALTER TABLE task_links ADD COLUMN mergeable TEXT;
ALTER TABLE task_links ADD COLUMN review_state TEXT;
ALTER TABLE task_links ADD COLUMN pending_comments INTEGER;
ALTER TABLE task_links ADD COLUMN detail TEXT;
ALTER TABLE task_links ADD COLUMN updated_at TEXT;
CREATE INDEX IF NOT EXISTS task_links_repo_number ON task_links(repo,number);
UPDATE task_links SET repo=substr(url,20,instr(url,'/pull/')-20),
  number=CAST(substr(url,instr(url,'/pull/')+6) AS INTEGER), updated_at=created_at
  WHERE kind='pr' AND url LIKE 'https://github.com/%/pull/%' AND repo IS NULL;

"""

# Migration 20 is the storage engineer's schema; reviews append migration 21.
from .storage_schema import SCHEMA as STORAGE_SCHEMA

TASK_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_file_reviews(
 file_id TEXT NOT NULL, version INTEGER NOT NULL, note TEXT,
 ask_message_id TEXT REFERENCES messages(id), comment_id TEXT REFERENCES messages(id),
 PRIMARY KEY(file_id,version));
"""

# Each task's team-wide number, kept for good. (This step also numbered the old engine's types and ranked tasks in
# their steps; a new database never makes those.)
NUMBERS_SCHEMA = """
ALTER TABLE tasks ADD COLUMN number INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS tasks_number ON tasks(number);
"""

# Existing records have no reliable sensitivity marker. Preserve them privately on upgrade;
# legacy inserts that omit the new field also fail closed. Current creates specify the default.
TASK_PRIVACY_SCHEMA = """
ALTER TABLE tasks ADD COLUMN private INTEGER NOT NULL DEFAULT 1 CHECK (private IN (0,1));
"""

# The person a bot's `waiting` task waited on, until step 6b of threads + tasks: migration
# 20261106112609_derived_waiting turns each into a question task and drops the column (question_tasks.waiting_on).
WAITING_ON_SCHEMA = """
ALTER TABLE tasks ADD COLUMN waiting_on TEXT;
"""

# Every task-to-task relationship (backend/task_relations.py). Migration 30 creates the table, moves
# tasks.parent_id and tasks.blocked_by into it and drops both columns (task_relations.migrate).
TASK_RELATIONS_SCHEMA = TR.SCHEMA

# Immutable primary/fallback usage reports; append to preserve existing installations.
USAGE_SEGMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_usage_segments (
  turn_id TEXT NOT NULL REFERENCES turns(id), position INTEGER NOT NULL,
  input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER,
  model TEXT, provider TEXT, est_cost_usd REAL, billing TEXT,
  runtime TEXT, harness TEXT, effort TEXT, profile TEXT,
  PRIMARY KEY(turn_id, position));
"""

# A subtask a bot runs on another model (backend/check_models.py); append to preserve existing installations.
CHECK_MODELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_check_models (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id), runtime TEXT NOT NULL, model TEXT NOT NULL,
  harness TEXT NOT NULL, effort TEXT NOT NULL DEFAULT '', role TEXT NOT NULL, created_by TEXT, created_at TEXT);
"""

# What the Strategy Owner has asked a person on the Goals page's behalf (backend/goal_asks.py): one row per
# KPI or proposal, kind and person, naming the task that asks it; append to preserve existing installations.
GOAL_ASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS goal_asks (
  kind TEXT NOT NULL, ref TEXT NOT NULL, owner TEXT NOT NULL, task_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (kind, ref, owner));
CREATE INDEX IF NOT EXISTS goal_asks_task ON goal_asks(task_id);
"""

# What woke a run and whether it changed anything (backend/run_triggers.py); append to preserve existing installations.
# A run from before this has NULLs, which `run_weekly` in hub sql reads as `unknown`.
RUN_TRIGGERS_SCHEMA = """
ALTER TABLE turns ADD COLUMN trigger_kind TEXT;
ALTER TABLE turns ADD COLUMN trigger_source TEXT;
ALTER TABLE turns ADD COLUMN writes INTEGER;
ALTER TABLE turns ADD COLUMN outcome TEXT;
ALTER TABLE turns ADD COLUMN did_nothing INTEGER;
"""

# What a run is for (backend/run_triggers.py); the migration fills it on every run already recorded.
RUN_PURPOSE_SCHEMA = """
ALTER TABLE turns ADD COLUMN purpose TEXT;
"""

# The one person a private task with no person on it could reach, before its reader list (migration
# 20261105213649_task_readers turns it into a reader and drops the column).
TASK_GUEST_SCHEMA = """
ALTER TABLE tasks ADD COLUMN guest TEXT;
"""

# Why a run left its session for a fresh one (runner-rs/src/rotate.rs; backend/run_triggers.py rotation): `tokens` or
# `turns`, NULL for a run that resumed or started fresh for any other reason. Append to preserve existing installations.
RUN_ROTATION_SCHEMA = """
ALTER TABLE turns ADD COLUMN rotated TEXT;
"""

# Whether a run resumed its provider session or started a new one, and why (`turns.session_kind` resumed|new,
# `turns.session_why`; backend/run_triggers.py session), and the one-shot mark that makes a thread's next run compact
# its session before it starts after a prompt too large to send (backend/run_backoff.py compact_first). Append to
# preserve existing installations.
RUN_SESSION_SCHEMA = """
ALTER TABLE turns ADD COLUMN session_kind TEXT;
ALTER TABLE turns ADD COLUMN session_why TEXT;
CREATE TABLE IF NOT EXISTS compact_first(
  bot TEXT NOT NULL, conversation_id TEXT NOT NULL, marked_at TEXT NOT NULL, taken_by TEXT,
  PRIMARY KEY(bot, conversation_id));
"""

# The Strategy Owner's merged asks (backend/strategy_merge.py): each original item in a person's Needs you that one
# Strategy Owner item stands for, and what became of it.
STRATEGY_MERGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_merges (
  merged_into TEXT NOT NULL, original TEXT NOT NULL, person TEXT NOT NULL, asker TEXT NOT NULL,
  how TEXT NOT NULL DEFAULT 'asked', ask_id TEXT,
  state TEXT NOT NULL DEFAULT 'merged', created_at TEXT NOT NULL, settled_at TEXT, note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (merged_into, original));
CREATE INDEX IF NOT EXISTS strategy_merges_original ON strategy_merges(original, state);
CREATE INDEX IF NOT EXISTS strategy_merges_person ON strategy_merges(person, created_at);
"""

# The Strategy Owner log (backend/strategy_acts.py): each decision it made alone at level 3, or each thing it proposed
# because it stays the owner's, and its Undo. Every task event one decision (or its Undo) wrote carries its action id.
STRATEGY_ACTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_actions (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  goal_id TEXT, state TEXT NOT NULL, proposal_task TEXT, note TEXT NOT NULL DEFAULT '',
  changes TEXT NOT NULL DEFAULT '{}', undone_at TEXT, undone_by TEXT, undo TEXT);
CREATE INDEX IF NOT EXISTS strategy_actions_created ON strategy_actions(created_at);
ALTER TABLE task_events ADD COLUMN action_id TEXT;
CREATE INDEX IF NOT EXISTS task_events_action ON task_events(action_id);
"""

# One login per AI tool per computer (backend/subscriptions.py): a bot's own subscription choice goes, and the bot
# runs on its computer's login. The table and the computers' profile reports stay, unread, for one release.
ONE_LOGIN_PER_COMPUTER_SCHEMA = """
DELETE FROM subscription_assignments WHERE scope='bot';
"""

# The market by spend (backend/market_spend.py): sized segments, player figures and the team's own sources. Rows are
# retired or rejected, never deleted. A size's evidence is a list of market_evidence ids.
MARKET_SPEND_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_sizes (
  id TEXT PRIMARY KEY,
  segment_id TEXT NOT NULL,
  dimension TEXT NOT NULL DEFAULT 'offering',
  basis TEXT NOT NULL DEFAULT 'product',
  year INTEGER NOT NULL,
  spend_usd REAL NOT NULL,
  low_usd REAL NOT NULL,
  high_usd REAL NOT NULL,
  growth_pct REAL,
  method TEXT NOT NULL,
  arithmetic TEXT NOT NULL DEFAULT '',
  formula TEXT,
  params TEXT NOT NULL DEFAULT '{}',
  evidence_ids TEXT NOT NULL DEFAULT '[]',
  confidence TEXT NOT NULL DEFAULT 'medium',
  overlaps TEXT NOT NULL DEFAULT '',
  spread TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'proposed',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS market_sizes_segment ON market_sizes(segment_id, dimension, year, status);

CREATE TABLE IF NOT EXISTS market_figures (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL,
  text TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  as_of TEXT NOT NULL,
  evidence_id TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS market_figures_segment ON market_figures(segment_id, entity_id);

CREATE TABLE IF NOT EXISTS market_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  covers TEXT NOT NULL DEFAULT '[]',
  access TEXT NOT NULL,
  reliability TEXT NOT NULL,
  how TEXT NOT NULL DEFAULT '',
  url TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL);
"""

# Task caps (backend/task_caps.py, redesign/CONTRACT.md §1): the cap per sender and manager, every send (kept per task,
# so a hand-over counts too), and the caps a bot proposes for a person to approve.
TASK_CAPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_caps(sender TEXT, receiver TEXT, per_day INTEGER, updated_at TEXT, updated_by TEXT,
  PRIMARY KEY(sender, receiver));
"""

# Every task cap send is kept (backend/task_caps.py): one row per task, sender, receiver and time, so a hand-over adds a
# send and never replaces the one before it.
TASK_CAP_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_cap_log(task_id TEXT NOT NULL, sender TEXT NOT NULL, receiver TEXT NOT NULL,
  sent_at TEXT NOT NULL, PRIMARY KEY(task_id, sender, receiver, sent_at));
CREATE INDEX IF NOT EXISTS task_cap_log_pair ON task_cap_log(sender, receiver, sent_at);
"""

# A pull request's last activity (CONTRACT section 6, PR find): the time of its newest GitHub pull_request, review or
# review comment event. NULL on rows from before it, which read as their creation (automation_actions FIND).
TASK_LINK_ACTIVITY_SCHEMA = """
ALTER TABLE task_links ADD COLUMN activity_at TEXT;
"""

# Task trigger tables (rule_task_runs, rule_task_state) were added to RULE_EVENTS_SCHEMA after it shipped, so an
# install that ran it earlier never got them and every task trigger failed quietly. Every statement is IF NOT EXISTS.
RULE_TASK_TABLES_SCHEMA = (
    "-- rule_task_runs, rule_task_state for installs that ran RULE_EVENTS_SCHEMA first\n" + RULE_EVENTS_SCHEMA
)

# The model one task's runs use (backend/task_run_models.py), kept apart from task_check_models: a check holds when its
# model cannot run, and this falls back to the bot's own model. One row per task; appended so existing installs get it.
TASK_RUN_MODELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_run_models (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id), runtime TEXT NOT NULL, model TEXT NOT NULL,
  harness TEXT NOT NULL, effort TEXT NOT NULL DEFAULT '', set_by TEXT, set_at TEXT);
"""

# Browser exits (backend/browser_exit.py): a computer's owner shares its internet with chosen bots under a daily byte
# cap; a bot browses through one such computer; what went through, per day, bot and hostname, is kept 30 days.
# `generation` fences the relay's grants read before a change: an exit's policy (bumped with each change and when the
# computer is removed), a bot's selection (its exit, cleared to NULL rather than deleted so the number only grows, and
# the computer it runs on, bumped in the same transaction as the assignment by browser_exit.TRIGGERS, which
# store.initialize creates once the execution tables exist). The relay's usage batches are recorded once each: per relay
# boot, the highest batch sequence recorded (`browser_exit_marks`).
BROWSER_EXITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_exits (
  runner_id TEXT PRIMARY KEY, sharing INTEGER NOT NULL DEFAULT 0, bots TEXT NOT NULL DEFAULT '[]',
  daily_cap_bytes INTEGER NOT NULL DEFAULT 5000000000, generation INTEGER NOT NULL DEFAULT 1,
  changed_by TEXT, changed_at TEXT);
CREATE TABLE IF NOT EXISTS bot_browser_exits (
  bot TEXT PRIMARY KEY, runner_id TEXT, generation INTEGER NOT NULL DEFAULT 1, set_by TEXT, set_at TEXT);
CREATE TABLE IF NOT EXISTS browser_exit_usage (
  runner_id TEXT NOT NULL, day TEXT NOT NULL, bot TEXT NOT NULL, host TEXT NOT NULL,
  bytes INTEGER NOT NULL DEFAULT 0, tunnels INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(runner_id, day, bot, host));
CREATE INDEX IF NOT EXISTS browser_exit_usage_day ON browser_exit_usage(day);
CREATE TABLE IF NOT EXISTS browser_exit_marks (boot TEXT PRIMARY KEY, seq INTEGER NOT NULL);
"""


# Growth (backend/growth.py) and the brand page (backend/brand.py). The brand keeps one draft row (id 1, with a revision
# so two editors never overwrite each other) and numbered published versions. The idea and go-to-market tables are
# gone: backend/migrations/0035_growth_rethink.py dropped the ones an older database still had, so this step creates
# only what is live.
# A cohort with no channel is the Unknown row: SQLite treats NULLs as distinct in UNIQUE, so the one-row-per-month
# rule is an expression index over coalesce(channel_id, '').
GROWTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS brand_drafts (
  id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0,
  updated_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS brand_versions (
  id TEXT PRIMARY KEY, n INTEGER NOT NULL UNIQUE, body TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '',
  published_by TEXT, published_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gtm_sources (
  id TEXT PRIMARY KEY, truth_for TEXT NOT NULL, system TEXT NOT NULL,
  reach_kind TEXT NOT NULL CHECK(reach_kind IN ('mcp','credential','url','person')), reach_ref TEXT NOT NULL DEFAULT '',
  may TEXT NOT NULL DEFAULT '', owner_ref TEXT, created_at TEXT NOT NULL, retired_at TEXT);
CREATE TABLE IF NOT EXISTS growth_channels (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  track TEXT NOT NULL DEFAULT 'performance' CHECK(track IN ('performance','brand')),
  stage TEXT NOT NULL DEFAULT 'idea' CHECK(stage IN ('idea','testing','scaling','paused')),
  owner_ref TEXT, budget_month REAL, source_id TEXT, why TEXT NOT NULL DEFAULT '', guardrail_kpi_id TEXT,
  guardrail_max REAL, sort INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, retired_at TEXT);
CREATE TABLE IF NOT EXISTS growth_cohorts (
  id TEXT PRIMARY KEY, channel_id TEXT, month TEXT NOT NULL, spend REAL, bot_cost REAL, new_customers INTEGER,
  kept_d30 REAL, kept_d90 REAL, kept_d180 REAL, revenue_month REAL, margin_pct REAL,
  quality TEXT NOT NULL DEFAULT 'actual' CHECK(quality IN ('actual','estimate')), sources TEXT NOT NULL DEFAULT '{}',
  updated_by TEXT, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS growth_cohorts_month ON growth_cohorts(coalesce(channel_id, ''), month);
CREATE TABLE IF NOT EXISTS growth_settings (
  id INTEGER PRIMARY KEY CHECK(id=1), unit TEXT NOT NULL DEFAULT 'customers', revenue_month REAL, margin_pct REAL);
ALTER TABLE kpis ADD COLUMN parent_kpi_id TEXT;
ALTER TABLE kpis ADD COLUMN growth_role TEXT;
ALTER TABLE kpis ADD COLUMN stage TEXT;
ALTER TABLE kpis ADD COLUMN loop_name TEXT;
ALTER TABLE kpis ADD COLUMN loop_step INTEGER;
"""


MIGRATIONS = [
    SCHEMA,
    MEETING_SCHEMA,
    MEETING_ITEMS_SCHEMA,  # index i takes user_version from i
    MEETING_BRAIN_SCHEMA,
    MEETING_COMMENTS_SCHEMA,  # to i+1; append, never edit
    GOALS_SCHEMA,
    RECORDING_SOURCES_SCHEMA,
    MARKET_SCHEMA,
    REPLY_ANSWERS_ASKS,
    LISTENING_SCHEMA,
    KPIS_SCHEMA,
    USAGE_SCHEMA,
    USAGE_LIMITS_SCHEMA,
    TAGS_SCHEMA,
    PIPELINES_SCHEMA,
    CHAT_GOALS_SCHEMA,
    REPOSITORIES_SCHEMA,
    TASK_LINKS_V2_SCHEMA,
    SUBSCRIPTIONS_SCHEMA,
    STORAGE_SCHEMA,
    TASK_REVIEW_SCHEMA,
    MEETING_REVIEW_SCHEMA,
    NUMBERS_SCHEMA,
    TASK_PRIVACY_SCHEMA,
    USAGE_SEGMENTS_SCHEMA,
    KPI_ARCHIVE_SCHEMA,
    WAITING_ON_SCHEMA,
    LIVE_MEETINGS_SCHEMA,
    LIVE_MEETINGS_ROUTING_LIMITS_SCHEMA,
    LIVE_MEETINGS_CHAT_ROUTING_SCHEMA,
    TASK_RELATIONS_SCHEMA,
    TRIGGERS_SCHEMA,
    TRIGGER_SIGNING_SCHEMA,
    CHECK_MODELS_SCHEMA,
    GOAL_ASKS_SCHEMA,
    RUN_TRIGGERS_SCHEMA,
    TASK_GUEST_SCHEMA,
    STRATEGY_MERGES_SCHEMA,
    ONE_LOGIN_PER_COMPUTER_SCHEMA,
    RUN_PURPOSE_SCHEMA,
    RUN_ROTATION_SCHEMA,
    LIVE_MEETING_ACCESS_SCHEMA,
    STRATEGY_ACTIONS_SCHEMA,
    MARKET_SPEND_SCHEMA,
    RULE_EVENTS_SCHEMA,
    TRIGGERS_INCOMING_SCHEMA,
    HOOK_RELAY_SCHEMA,
    WEBHOOKS_SCHEMA,
    SLEEP_IDLE_SCHEMA,
    TICO_NOTICES_SCHEMA,
    WEBHOOKS_V2_SCHEMA,
    WEBHOOKS_V3_SCHEMA,
    WEBHOOKS_V4_SCHEMA,
    WEBHOOKS_V5_SCHEMA,
    WEBHOOKS_V6_SCHEMA,
    RUN_SESSION_SCHEMA,
    TASK_CAPS_SCHEMA,
    TASK_CAP_LOG_SCHEMA,
    TASK_LINK_ACTIVITY_SCHEMA,
    RULE_TASK_TABLES_SCHEMA,
    TASK_RUN_MODELS_SCHEMA,
    LIVE_MEETINGS_CLIENT_SCHEMA,
    LIVE_MEETINGS_MODEL_SCHEMA,
    LIVE_MEETING_BOT_CONVERSATION_SCHEMA,
    LIVE_MEETING_TURN_RETRY_SCHEMA,
    POSTAL_MAIL_SCHEMA,
    POSTAL_MAIL_V2_SCHEMA,
    POSTAL_MAIL_V3_SCHEMA,
    TRIGGERS_CONSUMER_SCHEMA,
    GROWTH_SCHEMA,
    BROWSER_EXITS_SCHEMA,
]
# The list ends here, at user_version 71 (migrations.MIN_BRIDGE_USER_VERSION): every later persistent change is a file in
# backend/migrations.


class Refused(Exception):
    """A write the rules do not allow. `rule` names the rule; `detail` says what to fix."""

    def __init__(self, rule, detail="", severity="normal", task=None):
        super().__init__(f"{rule}: {detail}" if detail else str(rule))
        self.rule = rule
        self.detail = detail
        self.severity = severity
        self.task = task  # the task a `label` refusal was about (refuse)
        # What the caller needs to act on it, sent out beside the rule and the detail: for the task engine's refusals
        # what is missing, the commands allowed next and the task's version (store.refused).
        self.extra = {}
        # The detail and extra as kept on record (the audit, a stored response), when they are not what the caller is
        # told: {"detail", "extra"}. A refusal said to someone who may read a private task it names keeps it off.
        self.stored = None


# ----------------------------------------------------------------------------- basics
def now():
    """ISO-8601 UTC to the microsecond: sorts lexicographically, which every query relies on,
    and two writes in one loop never collide."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_ts(ts):
    """A timestamp this module wrote, back as an aware datetime. None when it cannot be read."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def shift(ts, **kw):
    """`ts` moved by a timedelta, in the same format."""
    at = parse_ts(ts) or datetime.now(timezone.utc)
    return (at + timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def new_id():
    return str(uuid.uuid4())


def uuid7():
    """A UUIDv7 (RFC 9562): the Unix time in milliseconds, then random bits, so ids sort by creation time."""
    value = bytearray(os.urandom(16))
    value[0:6] = int(datetime.now(timezone.utc).timestamp() * 1000).to_bytes(6, "big")
    value[6] = (value[6] & 0x0F) | 0x70
    value[8] = (value[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(value)))


# A message written in a person's name by someone else's hand says so in refs.via (_write_message): the Assistant,
# BotOps, an automation, a personal token ("token"). Slack is the one way a person types a message themselves.
OWN_HAND_VIA = ("slack",)


def delegated(refs) -> bool:
    """Whether a message's refs say it was written in a person's name but not by their hand (refs.via). The same notion
    as task_asks.refuses_ok_by for a request: the Assistant, BotOps, a token, anything acting for the person."""
    via = str((_json(refs, {}) or {}).get("via") or "")
    return bool(via) and via not in OWN_HAND_VIA


def by_own_hand(actor) -> bool:
    """Whether this request's act is a person's own, in their own session: not the Assistant, BotOps, an automation or
    a personal token acting as them (VIA, BY_TOKEN, set by backend/app.py for the request). The engine's twin of
    task_asks.refuses_ok_by, and the act's twin of typed_by_person: only this lifts a task Late brought to a person."""
    return is_human(actor) and not (VIA.get() or BY_TOKEN.get())


def in_person_name(message):
    """Whether a message is in a person's name: one they typed, one the Assistant, BotOps or a token wrote for them
    (they act with the person's rights), or the keeper's carrier of one (threads._carrier: refs.carries, refs.from).
    Enough for the claim's order, a snooze or done hold, fences and labels. in_person_name_sql is the row test."""
    message = message or {}
    sender = str(message.get("sender_id") or "")
    if is_human(sender):
        return True
    refs = _json(message.get("refs"), {}) or {}
    return sender == KEEPER and bool(refs.get("carries")) and is_human(str(refs.get("from") or ""))


def typed_by_person(message):
    """Whether a message is in a person's name and typed by their own hand: not delegated (the Assistant, BotOps, a
    token; a carrier copies its source's refs.via). Only that lifts a safeguard against runaway automatic runs and
    spend: the 7 runs and level person, a retry backoff, a usage-limit cooldown. typed_by_person_sql is the row test."""
    return in_person_name(message) and not delegated((message or {}).get("refs"))


def _json(value, default=None) -> Any:
    """A stored JSON column as a value. A row a caller already decoded holds the value itself under the column's
    name (row['refs'] is the dict), so a dict or list passes through."""
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dump(value):
    return json.dumps(value, sort_keys=True, default=str) if value is not None else None


def sourced_text(text, sources=(), refs=None):
    """Pair generated text with the identities it copies, for message refs or event/history data.

    Keep the pair together when forwarding text to another store: its readers use readable_page_refs before
    projecting away metadata. Existing refs survive, and repeated copies keep only one of each source.
    """
    refs = dict(refs or {})
    kept = list(refs.get("body_sources") or [])
    for source in sources:
        if source and source not in kept:
            kept.append(source)
    if kept:
        refs["body_sources"] = kept
    return text, refs


def answer_question(conn, target):
    """The stored question behind a structured answer target: a comment ask, or a file version's."""
    if target.get("comment"):
        return message(conn, target["comment"])
    row = conn.execute(
        "SELECT ask_message_id FROM task_file_reviews WHERE file_id=? AND version=?",
        (target.get("file"), target.get("version")),
    ).fetchone()
    return message(conn, row[0]) if row and row[0] else None


def answer_text(question, answers, *, text=None, other=None, passed=None, refs=None, file_subject=False):
    """Pair answer prose with sources resolved from the stored question, never from the submitted picks.

    A map names structured picks; a string quotes an option or the question's fallback. Callers may supply their
    prose, but source resolution stays here for held, automatic, quoted and delivered answers alike.
    """
    asked = (question or {}).get("refs") or {}
    questions = asked.get("questions") or []
    passed = passed or {}
    labels = {
        q["id"]: {
            o["label"]: o.get("source") or {"file": o["file"]}
            for o in q.get("options") or []
            if o.get("source") or o.get("file")
        }
        for q in questions
    }
    labels = {qid: sources for qid, sources in labels.items() if sources}
    if isinstance(answers, str):
        wanted = answers.strip().casefold()
        copied = {
            q["id"]: [o["label"] for o in q.get("options") or [] if str(o["label"]).strip().casefold() == wanted]
            for q in questions
        }
    else:
        copied = answers or {}
    sources = [
        source
        for qid, options in labels.items()
        for label, source in options.items()
        if label in copied.get(qid, []) or label in passed.get(qid, [])
    ]
    default = asked.get("if_no_answer") or {}
    if isinstance(answers, str) and answers.strip().casefold() == str(default.get("do") or "").strip().casefold():
        sources.append(default.get("source"))
    if file_subject and (asked.get("target") or {}).get("file"):
        sources.append(asked["target"])
    if text is None:
        lines = [f"{qid}: {', '.join(picks)}" for qid, picks in sorted(copied.items()) if picks]
        text = "\n".join([*lines, *([other] if other else [])])
    refs = dict(refs or {})
    if isinstance(answers, dict) and labels and refs.get("answer"):
        refs["answer"] = {**refs["answer"], "label_sources": labels}
    return sourced_text(text, sources, refs)


def payload_hash(payload):
    """The exact-payload hash rule 6 matches duplicates on."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ----------------------------------------------------------------------------- connection
def synchronous(default):
    """The PRAGMA synchronous a connection uses: `default`, or OFF when $TICO_SQLITE_SYNC is "off".

    Only the test suite sets it (conftest.py). Each test builds its own database and throws it away, so a sync per
    commit buys nothing there, while a shared host's disk paid for it: two pytest workers on the team runner wrote
    ~30 MB/s, a quarter of the volume's throughput, and pushed the live server's pages out of the cache."""
    return "OFF" if os.environ.get("TICO_SQLITE_SYNC", "").lower() == "off" else default


class _SyncWal:
    """An after-commit callback that fsyncs the WAL: what synchronous=FULL does at commit, for a transaction that was
    opened at NORMAL before it knew it would write money. Equal to every other one, so a transaction queues one. A
    `barrier`: Store.transaction runs it first and lets its error through, so a write that did not reach the disk is
    never answered as done. A failed sync stays owed (`_OWED`): every later write transaction on that file queues it
    again, so a retry that only replays the stored result still waits for the disk."""

    barrier = True

    def __init__(self, path):
        self.path = path

    def __eq__(self, other):
        return isinstance(other, _SyncWal)

    __hash__ = None

    def __call__(self):
        try:
            fd = os.open(self.path + "-wal", os.O_RDONLY)
        except FileNotFoundError:
            _OWED.discard(self.path)
            return
        try:
            os.fsync(fd)
        except BaseException:
            _OWED.add(self.path)
            raise
        finally:
            os.close(fd)
        _OWED.discard(self.path)


_OWED: set[str] = set()  # database files whose WAL sync failed and has not succeeded since


def _main_path(c: sqlite3.Connection) -> str:
    return next((row[2] for row in c.execute("PRAGMA database_list") if row[1] == "main"), "")


def owed_sync(c: sqlite3.Connection) -> None:
    """Store.transaction calls this before every commit: a WAL sync that failed earlier is queued again, so nothing on
    that file (an idempotent replay included) is answered as done until a sync succeeds."""
    hooks = getattr(c, "after_commit", None)
    if not _OWED or hooks is None:
        return
    path = _main_path(c)
    if path in _OWED and _SyncWal(path) not in hooks:
        hooks.append(_SyncWal(path))


def durable_commit(c: sqlite3.Connection) -> None:
    """Make this transaction's commit reach the disk before the request answers, from inside the transaction: SQLite
    refuses to change synchronous once it has begun, so the WAL is fsynced once the commit is done. Nothing to do on a
    connection already at FULL (a `durable` transaction), on one with no after-commit queue (a plain connection), or
    when the test suite turned syncing off."""
    hooks = getattr(c, "after_commit", None)
    if hooks is None or synchronous("NORMAL") == "OFF" or c.execute("PRAGMA synchronous").fetchone()[0] >= 2:
        return
    path = _main_path(c)
    if path and _SyncWal(path) not in hooks:
        hooks.append(_SyncWal(path))


# After a checkpoint the WAL file is cut back to this size, so a migration's peak is not kept on disk.
WAL_SIZE_LIMIT = 64 * 1024 * 1024


def leave_wal_for_litestream(conn):
    """Keep the -wal file on disk when this connection closes, instead of folding it into the database and deleting it.

    SQLite's last close normally checkpoints and deletes the WAL. Litestream resumes from its place in that file, so
    a WAL deleted on every stop makes every start upload the whole database. Checkpoints while the server runs still
    keep the WAL small (journal_size_limit). Explicit `PRAGMA wal_checkpoint` calls are unaffected. Needs Python 3.12+."""
    conn.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    return conn


def connect(path=None, adopt_legacy=False):
    """Open (and if need be create) hub.db. WAL, foreign keys on, migrations applied.

    Precedence: the `path` argument, then `$HUB_DB`, then `<projects>/runtime/hub.db`.
    `$HUB_DB` is what the tests and a second checkout set; it is honoured everywhere.
    """
    target = path or os.environ.get("HUB_DB") or DEFAULT_DB
    target = Path(target).expanduser()
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    # A file that does not exist yet (or is empty) is new: it takes auto_vacuum=INCREMENTAL before its first table, so
    # the sweeps' freed pages can go back to the disk (store.reclaim_free_pages). An existing file keeps its mode;
    # changing that needs a VACUUM.
    fresh = str(target) == ":memory:" or not target.exists() or target.stat().st_size == 0
    conn = leave_wal_for_litestream(sqlite3.connect(str(target), timeout=30, isolation_level=None))
    conn.row_factory = sqlite3.Row
    if fresh:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute(f"PRAGMA synchronous={synchronous('NORMAL')}")
    conn.execute(f"PRAGMA journal_size_limit={WAL_SIZE_LIMIT}")
    try:
        migrate(conn, adopt_legacy=adopt_legacy)
    except Exception:
        conn.close()
        raise
    return conn


class UnknownDatabase(RuntimeError):
    """The file has Tico's tables but no version record: not a database this code made."""


def backfill_credential_uses(conn):
    """Once: each credential's newest use per (actor, bot), from the events log (the rule credentials.record_use
    applies from now on: a bot's run or a computer sent it, never a person's reveal). One grouped read: a per-credential
    loop measured 62 s cold on TIDY against 16 s for this (2 s each warm). Reads events by (target, action)."""
    if conn.execute("SELECT 1 FROM registry_metadata WHERE key='credential-use-backfill'").fetchone():
        return
    known = {row[0] for row in conn.execute("SELECT id FROM credentials")}
    rows = conn.execute(
        "SELECT target, actor, COALESCE(NULLIF(CASE WHEN detail LIKE '{%' "
        "THEN json_extract(detail,'$.bot') END,''), "
        "CASE WHEN actor LIKE 'bot:%' THEN substr(actor,5) END, '') AS bot, MAX(created_at) "
        "FROM events WHERE action IN ('credential.revealed','credential.sent_to_computer') "
        "AND actor NOT LIKE 'human:%' GROUP BY target, actor, bot"
    ).fetchall()
    # The newest event row of a pair is both its newest use and the last one logged.
    conn.executemany(
        "INSERT INTO credential_uses(credential_id,actor,bot,used_at,logged_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(credential_id,actor,bot) DO UPDATE SET used_at=MAX(used_at,excluded.used_at),logged_at=MAX(logged_at,excluded.logged_at)",
        [(target, actor, bot or '', at, at) for target, actor, bot, at in rows if at and target in known],
    )
    conn.execute("INSERT INTO registry_metadata VALUES('credential-use-backfill',?)", (json.dumps(True),))


# The task rows every task read reads, as a table: `SELECT * FROM {TASKS} tasks`. It is the table itself.
TASKS = "tasks"
# The open snooze of the task aliased `t` (the engine's `snoozes`), for a filter: `NOT {SNOOZED % 't'}` keeps the tasks
# not snoozed.
SNOOZED = "EXISTS (SELECT 1 FROM snoozes sz WHERE sz.task_id=%s.id AND sz.ended_at IS NULL)"


def add_column(conn, table, column, declaration):
    """ALTER TABLE ... ADD COLUMN unless the column is there: SQLite has no IF NOT EXISTS for it."""
    if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


ADD_COLUMN = re.compile(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+([^;]+);", re.I)


def _statements(script):
    """The complete statements of a script (a trigger body keeps its inner semicolons)."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
    if buffer.strip():
        yield buffer.strip()


# The logs migration 0064 moved into history_events. A numbered step that names one is skipped on a file that already
# has the history table (one whose version was set back, as the upgrade tests do), so the step neither fails on the
# missing table nor brings it back empty beside the table that replaced it.
MOVED_TO_HISTORY = re.compile(r"\b(task_events|goal_events|market_events)\b")
# A numbered step's statement on the old run tables. Migration 0072 moved them into attempts (with turn_usage_segments
# already gone to usage_facts, migration 0053), so on a file it has reached a step that names them has nothing to do.
OLD_RUN_TABLES = re.compile(r"(?:\bON|\bTABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+(turns|turn_usage_segments)\b", re.I)


def _runs_merged(conn):
    """True once migration 0072 has moved the runs: no turns table, but attempts. A fresh file has neither yet, so its
    numbered steps create turns and migration 0072 moves it."""
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('turns','attempts')")
    }
    return "attempts" in names and "turns" not in names


def _apply(conn, script):
    moved = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_events'").fetchone()
    for statement in _statements(script):
        if moved and MOVED_TO_HISTORY.search(statement):
            continue
        if OLD_RUN_TABLES.search(statement) and _runs_merged(conn):
            continue
        match = ADD_COLUMN.fullmatch(statement)
        if match:
            add_column(conn, *match.groups())
        else:
            conn.execute(statement)


def _refuse_foreign(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables & {"bots", "tasks", "messages"} and "cloud_migrations" not in tables:
        raise UnknownDatabase(
            "This database file has Tico-like tables but no version record (user_version 0, no "
            "cloud_migrations table), so it is not one this Tico made. Refusing to start: point "
            "TICO_DB at an empty path or a Tico database, or restore a snapshot."
        )


def migrate(conn, adopt_legacy=False):
    """Apply every migration the file has not seen, by `PRAGMA user_version`.

    Each migration is one transaction with its version bump, so a failure leaves the file at
    the version it had; every statement is also safe to run twice (IF NOT EXISTS, guarded
    ADD COLUMN), so a file that has the change but an older version simply catches up.
    `adopt_legacy` is for `manage.migrate_legacy`, whose source has no version record.
    A database made by Tico 0.5 (it has tasks and has not had the cut-over) is refused before anything is written."""
    from . import migrations

    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and not adopt_legacy:
        _refuse_foreign(conn)
    migrations.refuse_old_tasks(conn)
    if version < len(MIGRATIONS):
        # A file from before the bridge (the legacy import; preflight refuses one otherwise) runs the numbered steps
        # below on its old shape, and they name today's columns, so it takes the cleanup's column renames first. A file
        # at the bridge gets them from their migration files (backend/migrations/0013_rails_renames_wave1.py), and a new
        # file has no tables to rename.
        from .migrations import renames

        renames.apply_now(conn)
    from .flight import begin

    for i in range(version, len(MIGRATIONS)):
        done = begin(conn)
        try:
            if i == 23:
                migrate_task_privacy(conn)
            elif i == 30:
                TR.migrate(conn)
            elif i == 39:
                _apply(conn, MIGRATIONS[i])
                if _runs_merged(conn):
                    RT.backfill(conn)
                else:
                    RT.backfill(conn, "turns", "bot")
            else:
                _apply(conn, MIGRATIONS[i])
            conn.execute(f"PRAGMA user_version={i + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            done()
    # The migration files (backend/migrations) run next, before every CREATE, ALTER and add_column below and in the
    # module schema code: those name the current columns, so a rename must land first or they would add an empty
    # column beside the old one.
    migrations.apply(conn)
    # conversations.scope, owner_id and room_key (cloud room scopes) and their two room indexes are migration 0047's.
    # Deleted tasks wait here until restored or purged (backend/task_delete.py).
    from .task_delete import ensure as ensure_task_trash

    ensure_task_trash(conn)
    # Reconnect cards: a task waits for a person to fix a missing or refused account (backend/reconnects.py).
    from .reconnects import ensure as ensure_reconnects

    ensure_reconnects(conn)
    # Recruiting seats: org-chart roles with no bot yet (backend/lifecycle.py).
    from .lifecycle import ensure as ensure_seats

    ensure_seats(conn)
    # A task's lane and its place in its owner's queue. The cloud store backfills rank and lane once (its migration
    # 29); here the columns simply exist.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "lane" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN lane TEXT NOT NULL DEFAULT 'company'")
    if "rank" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN rank REAL")
    if "labels" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN labels TEXT NOT NULL DEFAULT '[]'")
    # Every task-to-task relationship; idempotent, so a database at any version catches up.
    TR.migrate(conn)
    # Who is on a task, by role (task_roles).
    TRo.migrate(conn)
    # The due-date routine occurrence a task is for, "<automation_id>:<due_on>" (backend/automation_actions.py).
    add_column(conn, "tasks", "occurrence", "TEXT")
    # The step of the routine that opened it: one task per occurrence per producing step (task_create).
    add_column(conn, "tasks", "occurrence_step", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS tasks_occurrence_step ON tasks(occurrence, occurrence_step) "
        "WHERE occurrence IS NOT NULL"
    )
    # The rule due date an occurrence's run was fired for (backend/rule_due.py marks it when the task completes).
    add_column(conn, "tasks", "rule_due_id", "TEXT")
    task_columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "private" in task_columns:
        if "readers" in task_columns:
            # The few private tasks: their parties, which every privacy-checked read loads (backend/row_clock.py PARTIES,
            # backend/privacy_index.py), and a bot's private tasks for its status rows (task_privacy.status_tasks_sql)
            # with the two columns those read beyond the parties. The predicate matches both queries word for word and
            # the index holds every column they read, so no body is read.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS tasks_private_status ON tasks("
                "id, requester_id, owner_id, readers, private, status) WHERE private IS NOT 0"
            )
            # A task list's visibility and queue order, and a bot's status rows (task_privacy.status_tasks_sql), each
            # answered from an index alone: the table's pages hold every body, and these reads need none of them.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS tasks_queue_cover ON tasks("
                "rank, created_at, id, owner_id, requester_id, private, readers)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS tasks_owner_status_cover ON tasks("
                "owner_id, status, id, requester_id, private, readers)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS tasks_requester_status_cover ON tasks("
                "requester_id, status, id, owner_id, private, readers)"
            )
    # privacy_index.conversation_tasks is `SELECT id, task_id FROM conversations`, on every task list.
    # The conversation row holds participants, and the primary key scan reads those pages.
    conn.execute("CREATE INDEX IF NOT EXISTS conversations_id_task ON conversations(id, task_id)")
    # Status and provenance reads stay indexed as question and task history grows.
    conn.execute("CREATE INDEX IF NOT EXISTS messages_reply_kind ON messages(in_reply_to, kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS messages_from_kind ON messages(sender_id, kind)")
    # A task comment its author changed or took back (task_comment_edit, task_comment_delete). Checked on
    # every start rather than numbered, so no migration number collides with another branch's.
    add_column(conn, "messages", "edited_at", "TEXT")
    add_column(conn, "messages", "deleted_at", "TEXT")
    # An old ask made moot, or moved to a question task by the asks migration (question_tasks.move_asks).
    add_column(conn, "messages", "superseded_at", "TEXT")
    # Where a task stood after its last completed turn (execution.complete), so a fresh session picks
    # it up from there.
    add_column(conn, "tasks", "checkpoint", "TEXT")
    add_column(conn, "tasks", "checkpoint_at", "TEXT")
    add_column(conn, "tasks", "checkpoint_by_id", "TEXT")
    # Where the task stood by the others' changes when that checkpoint was left (wake_rules.state_of, token saving 3/9).
    add_column(conn, "tasks", "checkpoint_state", "TEXT")
    # Set once a turn in the task's own session took in another private task: that session may
    # carry it from then on, so no later turn's reply becomes the checkpoint (execution.complete).
    add_column(conn, "tasks", "checkpoint_blocked", "INTEGER NOT NULL DEFAULT 0")
    # The Tico account a person signed in with (backend/oidc.py person): the issuer and its subject id. An install
    # matches the account id before the email, so a changed address keeps the same person. NULL until their first
    # sign-in with an account; one account is one person.
    add_column(conn, "humans", "idp_issuer", "TEXT")
    add_column(conn, "humans", "idp_sub", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS humans_idp ON humans(idp_issuer, idp_sub) WHERE idp_sub IS NOT NULL"
    )
    from .roadmaps import migrate as migrate_roadmaps

    migrate_roadmaps(conn)
    # A bot's unread backlog, read newest first a page at a time on every inbox read (inbox_pages).
    conn.execute(UNREAD_INDEX)
    # The keeper's meeting inputs, the few messages a room's meeting provenance is read from (live_privacy.Sources) on
    # nearly every privacy check: the room index read every message of the room for them, millions of times a week.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS messages_live_meeting_input ON messages(conversation_id, created_at) "
        "WHERE json_extract(refs,'$.live_meeting.turn_id') IS NOT NULL"
    )
    # The questions a comment answered, reopened when it is deleted (`task_comment_delete`, inside the write
    # transaction): without it that lookup read every message.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS messages_answered_by ON messages(answered_by_id) WHERE answered_by_id IS NOT NULL"
    )
    # The media check visits blobs often; an unindexed version lookup multiplies both histories.
    conn.execute("CREATE INDEX IF NOT EXISTS bot_file_versions_blob_media ON bot_file_versions(blob_id,media_state)")
    # The media poll's pending rows, a handful even when blobs number in the tens of thousands.
    conn.execute("CREATE INDEX IF NOT EXISTS blob_media_pending ON blob_media(blob_id) WHERE media_state='pending'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS bot_file_versions_pending ON bot_file_versions(blob_id) WHERE media_state='pending'"
    )
    # Quiet notes (`tico note`): a line left for a bot's next run, asking nothing. `carried_by` is
    # the attempt that took it there; a canceled note never goes.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, from_actor TEXT NOT NULL, "
        "to_actor TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, carried_by TEXT, "
        "canceled_at TEXT, canceled_by TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS notes_to_created ON notes(to_actor, created_at)")
    # A bot-to-bot message that could wait (backend/bot_to_bot.py) rides to the next run as a note linked to it.
    add_column(conn, "notes", "message_id", "TEXT")
    # `settled_at`: the carrying run completed, so the note is read for good. It only narrows the waiting scan to
    # recent notes; NOTE_WAITING_SQL still asks the carrier, so a note no path settled is merely slower, never lost.
    # Both indexes hold unsettled rows only, so they stay small however many notes a bot has been sent.
    add_column(conn, "notes", "settled_at", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS notes_waiting ON notes(to_actor, created_at, id) "
        "WHERE canceled_at IS NULL AND settled_at IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS notes_unsettled_carried ON notes(carried_by) "
        "WHERE carried_by IS NOT NULL AND settled_at IS NULL"
    )
    # A deferred item and its deadline (backend/delivery.py). No foreign keys: a row outlives a deleted task or
    # message and is settled `moot` by the next pass. `conversation_id` '' is the bot's next turn of any kind.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS deferrals(id TEXT PRIMARY KEY, bot TEXT NOT NULL, kind TEXT NOT NULL, "
        "item TEXT NOT NULL, conversation_id TEXT NOT NULL DEFAULT '', task_id TEXT, due_at TEXT, "
        "created_at TEXT NOT NULL, fired_job TEXT, settled_at TEXT, how TEXT, attempt TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS deferrals_waiting ON deferrals(bot, conversation_id) WHERE settled_at IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS deferrals_due ON deferrals(due_at) WHERE settled_at IS NULL AND due_at IS NOT NULL"
    )
    # The hot queries in backend/delivery.py: a claim finds its carried rows by attempt, a run's end finds the deadline
    # runs still open by fired_job, and a stalled task checks for a deferral by item. Settled rows stay (no pruning yet),
    # so only the attempt index is not partial to unsettled rows.
    conn.execute("CREATE INDEX IF NOT EXISTS deferrals_attempt ON deferrals(attempt) WHERE attempt IS NOT NULL")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS deferrals_fired ON deferrals(fired_job) WHERE settled_at IS NULL AND fired_job IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS deferrals_item ON deferrals(item, kind) WHERE settled_at IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_owner_rank ON tasks(owner_id, rank)")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_lane_status_rank ON tasks(lane,status,rank,created_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS tasks_active_lane_rank ON tasks("
        "lane,(rank IS NULL),rank,created_at,id) WHERE status='open'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS tasks_finished_lane_time ON tasks("
        "lane,COALESCE(closed_at,done_at,updated_at,created_at) DESC,id DESC) "
        "WHERE status IN ('done','closed')"
    )
    conn.execute("DROP INDEX IF EXISTS tasks_due_active")  # the due-date reminder pass it served is gone
    # A task's history and the Tasks page's stats read history_events through the indexes its migration made
    # (backend/migrations/0064_history_events.py).
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_occurrences'").fetchone():
        conn.execute("CREATE INDEX IF NOT EXISTS schedule_occurrences_task ON schedule_occurrences(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS events_task_origin ON events(actor,action,target,created_at DESC)")
    # A tag's attach events (the tag list, privacy_index.tags): only ~6k rows of a 2M-row events table, read with
    # their detail. Partial and covering, so the list's IN(...) reads a few dense pages, not one row per event.
    conn.execute("CREATE INDEX IF NOT EXISTS events_tag_attach ON events(target, detail) WHERE action='tag.attach'")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), "
        "kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created_at TEXT NOT NULL, "
        "pr_sha TEXT, pr_merged_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS task_links_task ON task_links(task_id)")
    # A computer's worktrees (worktrees.inventory, on every heartbeat): a seek to its rows, not a scan of every
    # worktree link ever kept. Partial, so it holds worktree rows only.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS task_links_worktree_computer ON task_links(computer_id) WHERE kind='worktree'"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS preferences(actor TEXT NOT NULL, key TEXT NOT NULL, "
        "value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(actor, key))"
    )
    # Which messages could be about a task, so a task's page does not read its whole room (backend/message_links.py).
    from . import message_links
    from . import threads

    threads.ensure(conn)
    threads.migrate(conn)
    # Once: everyone's old personal chat with a bot moves into that bot's Home (backend/bot_home_merge.py).
    from . import bot_home_merge

    bot_home_merge.run_once(conn)
    message_links.ensure(conn)
    # The pipelines every company has (General), made when missing on every start, and given their manager once that
    # bot exists (backend/pipeline_settings.py). Here, so a hub opened without the cloud store has them too.
    from . import pipeline_settings

    if conn.in_transaction:
        pipeline_settings.ensure_presets(conn)
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            pipeline_settings.ensure_presets(conn)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def _row(row):
    return dict(row) if row is not None else None


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _one(conn, sql, args=()):
    from .read_cache import one

    return _row(one(conn, sql, args))


# ----------------------------------------------------------------------------- audit
# Who is really acting when a person's own identity is used by the Assistant ("assistant"): set for
# the length of one request (backend/app.py request_guard), so every event and task-history row the
# request writes says "via assistant" without each write knowing about it.
VIA = contextvars.ContextVar("hub_via", default="")
# When BotOps acts with a person's rights: whose, which request lent them (a message or task id), and whether the person
# asked in this run (`live`) or BotOps cites their open task in a later one (`follow_through`). Every event then says so.
# The Assistant sets it too, for a task action the person asked for in their own Assistant chat (`asked`, the message cited).
DELEGATION = contextvars.ContextVar("hub_delegation", default=None)
# A person's own identity used through a personal token for this request (backend/app.py request_guard): a message it
# writes in their name says refs.via "token" (_write_message), so it is not theirs by hand (delegated).
BY_TOKEN = contextvars.ContextVar("hub_by_token", default=False)

PRIVATE_WRITE = contextvars.ContextVar("hub_private_write", default=False)
# A dry run reads the same rules as the write without recording or escalating a refusal (backend/mcp.py).
CLOSE_PROBE = contextvars.ContextVar("hub_close_probe", default=False)
# The run (attempt id) whose token made this request (backend/app.py request_guard): each write it logs is counted on
# that run's `attempts.writes`, which says whether the run acted (backend/run_triggers.py).
RUN = contextvars.ContextVar("hub_run", default="")
# A task write made without a run token still attributes its refusal to the task being changed.
REFUSAL_TASK = contextvars.ContextVar("hub_refusal_task", default=None)
# The bulk change (backend/app.py task_bulk) a refusal came from: each task it skipped keeps its own refusal on record,
# but one call counts once toward rule 8's quarantine, however many tasks it skipped (_escalate).
BULK_CALL = contextvars.ContextVar("hub_bulk_call", default="")
# A task filed from an outside reader's words (backend/company_page/readers.py) reaches a person and nothing else: no
# webhook, automation or task rule hears the write, so no bot runs on it until a person passes it on.
NO_TRIGGERS = contextvars.ContextVar("hub_no_triggers", default=False)


@contextmanager
def refusal_task(task_id):
    """Attribute the whole mutation; refuse keeps its scope on the exception across rollback."""
    token = REFUSAL_TASK.set(_task_ref(task_id) or REFUSAL_TASK.get())
    try:
        yield
    finally:
        REFUSAL_TASK.reset(token)


def private_task_write(fn):
    """Keep refusal audit/escalation content-free for a private domain write, including local runners."""
    import functools
    import inspect

    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def guarded(*args, **kwargs):
        values = signature.bind_partial(*args, **kwargs).arguments
        conn, actor = values['conn'], values['actor']
        row = task(conn, values.get('task_id')) if values.get('task_id') else None
        private = bool(row and task_private(conn, row) or values.get('private'))
        if fn.__name__ == 'task_create':
            if values.get('private') is False and (not is_human(actor) or VIA.get()):
                values['private'] = None
            target = resolve_actor(conn, values.get('owner_id'))
            parent = task(conn, values.get('parent_id')) if values.get('parent_id') else None
            private = bool(
                (
                    values.get('private')
                    if values.get('private') is not None
                    else private_tasks_default(conn, actor) or private_tasks_default(conn, target)
                )
                or parent
                and task_private(conn, parent)
            )
        token = PRIVATE_WRITE.set(PRIVATE_WRITE.get() or private)
        task_token = REFUSAL_TASK.set((row or {}).get('id') or REFUSAL_TASK.get())
        from . import task_trigger_rules

        try:
            with task_trigger_rules.pending(conn):
                result = fn(*args, **kwargs)
            # Actions may have changed the task after the original write completed.
            if (
                fn.__name__ in ('task_create', 'task_update', 'task_close')
                and isinstance(result, dict)
                and result.get('id')
            ):
                result = {**result, **(task(conn, result['id']) or {})}
            return result
        except Refused as exc:
            exc.private = PRIVATE_WRITE.get()
            raise
        finally:
            REFUSAL_TASK.reset(task_token)
            PRIVATE_WRITE.reset(token)

    return guarded


def event(conn, actor, action, target="", detail=None):
    """Append the audit row every write leaves behind."""
    # The run that filed a task: what feedback on that work names as its run (backend/feedback.py), since a task does
    # not keep the run that made it.
    if action == "task.create" and RUN.get():
        detail = {**(detail or {}), "attempt_id": RUN.get()}
    if VIA.get() and (detail is None or isinstance(detail, dict)):
        detail = {**(detail or {}), "via": VIA.get()}
        if VIA.get() in ("botops", "assistant") and DELEGATION.get():
            detail["delegation"] = DELEGATION.get()
    detail = SRC.stamp_data(detail, actor)  # what a keeper event was for (backend/sources.py), when a scope names it
    row = {
        "id": new_id(),
        "created_at": now(),
        "actor": str(actor),
        "action": action,
        "target": str(target or ""),
        "detail": _dump(detail),
    }
    conn.execute(
        "INSERT INTO events (id, created_at, actor, action, target, detail) "
        "VALUES (:id, :created_at, :actor, :action, :target, :detail)",
        row,
    )
    if RUN.get() and RT.counts(action):
        conn.execute("UPDATE attempts SET writes=coalesce(writes,0)+1 WHERE id=?", (RUN.get(),))
    return row


def _building(conn, task_id):
    """Mark the task being made on this connection. A plain sqlite3 connection (an upgrade script's) takes no
    attributes; there the mark is skipped, and the only cost is a state-change event beside task.created."""
    try:
        conn.building = task_id
    except AttributeError:
        pass


def hook(conn, type, subject, data, actor, revision=None):
    """Tell the outgoing webhooks (backend/webhooks.py) and the automations (backend/automations.py) that `type` happened
    to `subject`, in the caller's write. `data` is a function that makes the event's thin payload, called only when
    someone is listening. Never raises: a webhook problem must not undo or refuse the change that caused it."""
    if NO_TRIGGERS.get():
        return
    try:
        from . import webhooks

        if webhooks.listening(conn):
            webhooks.publish(conn, type, subject, data(), actor, revision=revision)
    except Exception:
        logging.getLogger(__name__).warning("webhook event %s was not queued", type, exc_info=True)
    try:
        from . import automations

        # An automation hears of a private task only when its owner reads it, as a webhook endpoint's owner does.
        hears = (lambda row: _automation_hears(conn, row.get("owner_id"), subject)) if task(conn, subject) else None
        automations.on_event(conn, type, subject, data, actor, access=hears)
    except Exception:
        logging.getLogger(__name__).warning("automations did not hear %s", type, exc_info=True)


def _automation_hears(conn, owner, task_id):
    """Whether an automation owned by `owner` (`person:<id>`, `bot:<id>`, `group:<id>`) may hear an event about task
    `task_id`: always for a company task; for a private one, only when the owner reads it (webhooks.view_for). A group
    stands for no one, so it hears of no private task."""
    row = task(conn, task_id)
    if not row or not task_private(conn, row):
        return True
    kind, _, ident = str(owner or "").partition(":")
    if kind == "group" or not ident:
        return False
    from . import webhooks

    who = "human:" + ident if kind == "person" else str(owner)
    return webhooks.view_for(conn, who, "task.event", task_id, {}, {}) is not None


def _task_facts(conn, task_id, actor, **more):
    row = task(conn, task_id) or {}
    return {
        "id": task_id,
        "title": row.get("title"),
        "status": row.get("status"),
        "pipeline": row.get("pipeline_id"),
        "stage": row.get("stage_id"),
        "owner_id": row.get("owner_id"),
        "requester_id": row.get("requester_id"),
        "actor": actor,
        **more,
    }


def writing_refusal(rule, detail):
    """Only diagnostics produced by human-item formatting."""
    return rule == "lint" and bool(
        re.match(
            r'^(give it a title|start the title with a verb|internal codes|internal jargon|'
            r'write a concrete human decision|write the ask in the first line|\d+ words outside the quoted draft)',
            detail,
        )
    )


def refuse(
    conn, actor, rule, detail="", severity="normal", context=None, task=None, scope=None, extra=None, stored=None
) -> NoReturn:
    """Record a refusal, escalate it (rule 8), and raise. Never returns. `context` is what a person
    reviewing the refusal reads; a private task write keeps only what was refused. `task` is the task
    a `label` refusal was about, which _escalate counts it by. `extra` goes out with the refusal, unrecorded: what the
    caller needs to act on it (`Refused.extra`). `stored` is {"detail", "extra"} as kept on record, when the caller
    is told more than may be kept (`Refused.stored`)."""
    if CLOSE_PROBE.get():
        probed = Refused(rule, detail, severity, task)
        probed.extra = dict(extra or {})
        probed.stored = stored
        raise probed
    from . import task_quarantine

    scope = scope or task_quarantine.source(conn, actor, task or REFUSAL_TASK.get() or (context or {}).get("task"))
    recorded = "Private task write refused" if PRIVATE_WRITE.get() else (stored or {}).get("detail", detail)
    if context and PRIVATE_WRITE.get():
        # A private task's refusal keeps only what was refused.
        context = {"what": context.get("what")}
    row = {
        "id": new_id(),
        "created_at": now(),
        "actor": str(actor),
        "rule": rule,
        "detail": _dump(
            {
                "detail": recorded,
                **({"context": context} if context else {}),
                **({"task": task} if task else {}),
                **({"scope": scope} if scope else {}),
                **({"bulk": BULK_CALL.get()} if BULK_CALL.get() else {}),
            }
        ),
        "severity": severity,
    }
    conn.execute(
        "INSERT INTO refusals (id, created_at, actor, rule, detail, severity) "
        "VALUES (:id, :created_at, :actor, :rule, :detail, :severity)",
        row,
    )
    event(conn, actor, "refused", rule, {"detail": recorded, "severity": severity})
    if rule != "quarantined":
        try:
            _escalate(conn, actor, rule, recorded, severity, row["created_at"], context)
        except Exception:  # an escalation must never hide the refusal it came from
            pass
    if writing_refusal(rule, detail):
        detail += ". Rewrite the title/body to fix these writing errors and retry. Do not ask the human to waive formatting rules."
    refused = Refused(rule, detail, severity, task)
    refused.scope = scope  # survives the domain-write rollback (store.refused)
    refused.extra = dict(extra or {})
    refused.stored = stored
    raise refused


def require_person_ask(conn, actor, person, text, where):
    """Refuse a bot's write that leaves `person` without a plain ask. A writing refusal (rule `lint`) is recorded
    but never counts toward a quarantine (_escalate): it is corrected by rewriting, not a sign the bot reaches
    where it should not."""
    detail = person_ask_refusal(person, text, where)
    if detail:
        refuse(conn, actor, "lint", detail)


def require_plain_question(conn, actor, person, questions, *, written=False):
    """Refuse a bot's question to a person that breaks the asking rules (question_refusal). Like require_person_ask,
    a `lint` refusal is fixed by rewriting and never counts toward a quarantine."""
    if not is_bot(actor) or not is_human(person):
        return
    detail = question_refusal(person, questions, written=written)
    if detail:
        refuse(conn, actor, "lint", detail)


def style_problems(conn, actor, problems, severity="normal"):
    """Rule 7's writing problems as warnings: returned for the write to carry, so the bot does
    better next time, and refused only when TICO_STYLE_LINT=refuse."""
    if not problems or STYLE_LINT == "off":
        return []
    if STYLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(problems), severity)
    return list(problems)


def _clip(text, limit=180):
    """A refused body, safe to quote inside a task a human will read."""
    one = " ".join(str(text or "").split())[:limit]
    return LINT_JARGON.sub("[code]", LINT_CODES.sub("[code]", one))


# ----------------------------------------------------------------------------- severity (rule 8)
SECRETS_PATH = re.compile(r"(^|[\s\"'(/])secrets/", re.I)
OTHER_REPO = re.compile(
    r"\b(?:emp|bot)-[a-z0-9-]+/", re.I
)  # a bot's repository: bot-<slug>, or emp-<slug> for an older bot


def known_repos(conn):
    """The repository folder names this hub knows: `bot-<slug>` for every bot, and each name a bot's record gives."""
    names = {"bot-" + str(row["slug"]).lower() for row in _rows(conn.execute("SELECT slug FROM bots"))}
    for row in _rows(conn.execute("SELECT repo FROM bot_config WHERE repo IS NOT NULL AND repo != ''")):
        names.add(str(row["repo"]).rstrip("/").rsplit("/", 1)[-1].lower())
    return names


def own_repos(conn, actor):
    """The repository a bot runs in, including its original when it is a branch."""
    if not is_bot(actor) or not _has_table(conn, "bot_config"):
        return ()
    from .shared_bots import declared, follow

    slug = actor_id(actor)
    config = follow(conn, slug, declared(conn, slug))
    row = _one(conn, "SELECT repo FROM bot_config WHERE bot=?", (slug,))
    repo = config.get("repo") or (row["repo"] if row else "") or "emp-" + (config.get("name") or slug)
    return (str(repo).rstrip("/").rsplit("/", 1)[-1].removesuffix(".git").lower(),)


def names_other_repo(text, actor, conn=None):
    """Whether `text` names another bot's repository folder (`other_repo_named`)."""
    return bool(other_repo_named(text, actor, conn))


def other_repo_named(text, actor, conn=None):
    """The first other bot's repository folder `text` names, or "". `emp-<anything>/` always counts (the older prefix). A
    `bot-<name>/` counts only when it is a real bot's folder (known bots and their recorded repositories), so ordinary
    words such as "bot-driven/" are not an escape; with no `conn` to look them up, only the `emp-` form is checked."""
    mine = (f"emp-{actor_id(actor)}/", f"bot-{actor_id(actor)}/") if actor else ()
    if conn is not None:
        mine += tuple(repo + "/" for repo in own_repos(conn, actor))
    known = None
    for match in OTHER_REPO.finditer(str(text or "")):
        name = match.group(0).lower()
        if name in mine:
            continue
        if name.startswith("emp-"):
            return match.group(0)
        if conn is not None:
            known = known_repos(conn) if known is None else known
            if name[:-1] in known:
                return match.group(0)
    return ""


SENSITIVE_WORDS = re.compile(
    r"\b(spend|spending|invoice|payment|pay|card|refund|budget|wire|charge|"
    r"send|email|mailbox|inbox|access|credential|credentials|token|password|api[_ -]?key)\b",
    re.I,
)


def classify(text, kind=None, to_actor=None, where="item", actor=None, conn=None):
    """The severity rule 8 counts by, read off the thing that was refused.

    `sensitive` money, an outbound send, an access change, or a human's inbox.
    `normal`   everything else.

    `escape` is no longer read off words: text that names a `secrets/` path or another bot's
    folder reaches nothing, so it is written as it is (`path_mentioned` records it). `actor` and
    `conn` are kept so callers need not change; `where` is "item" or "message".
    """
    body = str(text or "")
    if kind in ("send", "spend"):
        return "sensitive"
    if is_human(to_actor) and SENSITIVE_WORDS.search(body):
        return "sensitive"
    if SENSITIVE_WORDS.search(body) and kind in ("publish", "merge"):
        return "sensitive"
    return "normal"


def path_mentioned(conn, actor, what, text, task_id=None):
    """A bot's words that name a `secrets/` path or another bot's folder are kept as written:
    naming a path opens nothing, and refusing it cost bots their turns and quarantined them.
    The mention is recorded (`path.mentioned`) for whoever reviews the bot. What was found, or ""."""
    if not is_bot(actor):
        return ""
    found = escape_found(text, actor, conn)
    if found:
        # The task id a bot's ref names: a list is its first task (_task_ref), so it binds into the event as a string.
        event(
            conn,
            actor,
            "path.mentioned",
            _task_ref(task_id) or "",
            {"what": what} if PRIVATE_WRITE.get() else {"what": what, "found": found},
        )
    return found


def escape_found(text, actor=None, conn=None):
    """What made `text` an escape: `secrets/`, or the other bot's folder it names."""
    if SECRETS_PATH.search(str(text or "")):
        return "secrets/"
    return other_repo_named(text, actor, conn)


ESCAPE_PREVIEW_MAX = 400


def escape_preview(text):
    """The refused words as a reviewer may see them: credential-shaped values masked, at most ESCAPE_PREVIEW_MAX."""
    from .diagnostics import LONG, SECRET_FIELD, SECRETS, _looks_secret

    out = str(text or "")
    for pattern, label in SECRETS:
        out = pattern.sub(label, out)
    out = SECRET_FIELD.sub(lambda m: m.group(1) + "[hidden]", out)
    out = LONG.sub(lambda m: "[hidden]" if _looks_secret(m.group(0)) else m.group(0), out)
    out = out.strip()
    return out if len(out) <= ESCAPE_PREVIEW_MAX else out[: ESCAPE_PREVIEW_MAX - 1] + "…"


def _escalate(conn, actor, rule, detail, severity, ts, context=None):
    """Rule 8: count the day's refusals, open review tasks, quarantine when it is bad enough."""
    if not is_bot(actor):
        return
    # Capacity waiting is legitimate, and must never feed review, repeats or the daily backstop.
    # A writing correction ("start the title with a verb") is fixed by retrying, not a sign the bot
    # is reaching where it should not: it neither counts nor quarantines. A bot was quarantined by
    # ten refusals, three of them lint on one task title.
    # A move the one task engine refused (backend/task_engine.py, every rule starts `task_`) says what to send
    # instead: a newer version, a reason, a requirement still missing. It is a correction too.
    if rule in ("lint", "queue_full") or str(rule).startswith("task_"):
        return
    slug = actor_id(actor)
    day = ts[:10]
    # Counted since the day began or the last quarantine lifted, whichever is later.
    lifted = _one(
        conn,
        "SELECT max(created_at) AS created_at FROM events WHERE action='quarantine.lifted' AND target=?",
        (bot_actor(slug),),
    )
    since = max(day, (lifted or {}).get("created_at") or "")
    from . import task_quarantine

    held, released = set(), {}
    for hold in conn.execute(
        "SELECT task_id,conversation_id,max(released_at) AS released_at, "
        "max(released_at IS NULL) AS held FROM quarantine_holds "
        "WHERE bot_id=? AND (released_at IS NULL OR released_at>=?) "
        "GROUP BY task_id,conversation_id",
        (slug, since),
    ):
        scope = "task:" + hold["task_id"] if hold["task_id"] else "chat:" + hold["conversation_id"]
        if hold["held"]:
            held.add(scope)
        elif hold["released_at"]:
            released[scope] = hold["released_at"]
    counted, last, total, calls, again, scopes = [], {}, 0, set(), False, set()
    for refused in _rows(
        conn.execute(
            "SELECT rule, detail, created_at FROM refusals WHERE actor=? AND created_at>=? "
            "AND rule NOT IN ('lint','queue_full') AND substr(rule, 1, 5) != 'task_' "
            "ORDER BY created_at, id",
            (actor, since),
        )
    ):
        facts = _json(refused["detail"], {}) or {}
        scope = facts.get("scope")
        # A scope's own hold contains its refusals, so its loop cannot stop unrelated work.
        if scope in held or scope in released and refused["created_at"] < released[scope]:
            continue
        if refused["rule"] == "label":
            # A label refusal says who may make the change instead; retrying it is not reaching further, so
            # it counts once per task an hour (LABEL_REFUSAL_WINDOW_S). Nine in a minute quarantined a bot.
            about = facts.get("task")
            if about in last and parse_ts(refused["created_at"]) - last[about] < timedelta(
                seconds=LABEL_REFUSAL_WINDOW_S
            ):
                continue
            last[about] = parse_ts(refused["created_at"])
        # Attribution does not change whether the same refused action was retried.
        key = _dump({k: v for k, v in facts.items() if k != "scope"})
        # Every source matters even when several refusals count as one bulk try.
        scopes.add(scope)
        if call := facts.get("bulk"):
            # One bulk change counts once, by its first counted refusal: a single call over 50 tasks the bot may not
            # change is one refused try, not 50. Its words count as a repeat of the same refusal made one by one.
            if call in calls:
                again = again or call == BULK_CALL.get()
                continue
            calls.add(call)
            key = _dump({k: v for k, v in facts.items() if k not in ("bulk", "scope")})
        total += 1
        # The target bot is paused, quarantined or planned: the sender broke nothing, and the same words come
        # back every time, so it opens no review and is no repeat. It still counts toward the day's backstop.
        if refused["rule"] != "target_inactive":
            counted.append((refused["rule"], key))
    if again:
        # This bulk change's first refusal already counted, reviewed and quarantined as need be.
        return
    count, distinct = len(counted), len(set(counted))

    def contain(reason, refusal):
        if len(scopes) == 1 and None not in scopes:
            task_quarantine.hold(conn, slug, next(iter(scopes)), reason, refusal)
        else:
            quarantine(conn, slug, reason, refusal=refusal)

    if rule == "target_inactive":
        # A sender retrying it in a loop still meets the day's backstop.
        if total >= QUARANTINE_TOTAL_AT:
            contain(f"{total} refusals today", {"rule": rule, "detail": _clip(detail)})
        return
    # Only a refusal the bot has already had (same rule, same words) counts toward the quarantine: retrying a
    # refused action is what the limit stops. A first try at something new that is refused is recorded, not counted.
    repeats = count - distinct
    said = _clip(detail)
    reviewer = bot_actor(REVIEW_OWNER if slug == FLEET_MAINTAINER else FLEET_MAINTAINER)
    if slug == FLEET_MAINTAINER and (bot(conn, REVIEW_OWNER) or {}).get("state") in (None, "planned", "archived"):
        # A company without an assistant has nobody to review BotOps: a person does.
        reviewer = human_actor(default_human(conn))
    # By the name people know it by, not its id (Initech, Nov 11: "Review 01a1cb0d-…'s refused writes").
    found = bot(conn, slug) or {}
    name = found.get("display_name") or found.get("slug") or slug
    if count >= REVIEW_AT and not _review_exists(conn, f"Review {name}'s refused writes", day):
        _review_task(
            conn,
            reviewer,
            f"Review {name}'s refused writes",
            f"{count} writes from {name} were turned down today. "
            f"The last one broke the {rule} rule: {said}. "
            f"Read its refusals and decide whether its playbook or its reach needs a change.",
            origin=actor,
        )
    # Refusals are diagnostics for the bot, not decisions for the human.
    # Keep the audit, internal repeated-failure review, and quarantine enforcement.
    stopped = {"rule": rule, "detail": said}  # what the bot is told when it is back (quarantine_lifted_note)
    if repeats >= QUARANTINE_AT:
        contain(f"{repeats} repeated refusals today", stopped)
    elif total >= QUARANTINE_TOTAL_AT:
        # The backstop: refusals that each name a new target are no repeats, but this many still mean a loop.
        contain(f"{total} refusals today", stopped)


def _review_exists(conn, title, day):
    return (
        conn.execute(
            "SELECT 1 FROM tasks WHERE title=? AND requester_id=? AND substr(created_at,1,10)=?", (title, KEEPER, day)
        ).fetchone()
        is not None
    )


def _review_task(conn, owner, title, body, origin=None):
    try:
        # lint=False: a person's review reads as written; the shape rule is for asks bots write.
        row = task_create(conn, KEEPER, title, body, owner, lint=False)
        if origin and is_bot(origin):
            event(conn, KEEPER, "task.origin", row["id"], {"bot": origin})
    except Refused:
        pass  # a live duplicate is the once-a-day guard doing its job


QUARANTINE_COOLDOWN_S = 3600
COUNT_QUARANTINE = re.compile(r"\d+ (?:refused writes|repeated refusals|refusals) today")  # the first: before #287


# A bot's latest quarantine event, and when it was last quarantined: the statements the readers below run, named so a
# batch can seed them (prime_quarantines).
QUARANTINE_DETAIL_SQL = (
    "SELECT detail FROM events WHERE action='quarantine' AND target=? ORDER BY created_at DESC, rowid DESC LIMIT 1"
)
QUARANTINE_SINCE_SQL = "SELECT max(created_at) AS created_at FROM events WHERE action='quarantine' AND target=?"


def prime_quarantines(conn, bots):
    """The latest quarantine event and last quarantine time of every bot in `bots` (ids), in two reads for all of them
    (per 500 bots): a loop of explanations then reads none of them again. Outside a snapshot nothing is kept."""
    from .read_cache import prime

    actors = sorted({bot_actor(b) for b in bots if b})
    for part in chunks(actors):
        marks = ",".join("?" * len(part))
        latest = {
            r["target"]: r
            for r in conn.execute(
                "SELECT target, detail FROM (SELECT target, detail, row_number() OVER (PARTITION BY target "
                "ORDER BY created_at DESC, rowid DESC) AS n FROM events WHERE action='quarantine' AND target IN ("
                + marks
                + ")) WHERE n=1",
                part,
            )
        }
        since = {
            r["target"]: r["created_at"]
            for r in conn.execute(
                "SELECT target, max(created_at) AS created_at FROM events WHERE action='quarantine' AND target IN ("
                + marks
                + ") GROUP BY target",
                part,
            )
        }
        prime(conn, QUARANTINE_DETAIL_SQL, {(a,): latest.get(a) for a in part})
        prime(conn, QUARANTINE_SINCE_SQL, {(a,): {"created_at": since.get(a)} for a in part})


def quarantine_reason(conn, slug):
    row = _one(conn, QUARANTINE_DETAIL_SQL, (bot_actor(slug),))
    return ((_json(row["detail"], {}) or {}) if row else {}).get("reason") or ""


def quarantine_is_escape(conn, slug):
    """Anything but a refusal-count quarantine is treated as an escape: a person clears it."""
    return not COUNT_QUARANTINE.fullmatch(quarantine_reason(conn, slug))


# A quarantine recorded before its refusal was kept: "escape: the task note reaches outside the hub: <words>".
LEGACY_ESCAPE = re.compile(r"escape: the (.+?) (?:payload )?reaches outside the hub: (.*)", re.S)


def quarantine_review(conn, slug):
    """What a person resuming an escape quarantine reads: what was being written (`what`), what the check
    matched (`found`), the refused words with credential values masked (`preview`) and the task (`task`)."""
    row = _one(conn, QUARANTINE_DETAIL_SQL, (bot_actor(slug),))
    detail = (_json(row["detail"], {}) or {}) if row else {}
    if detail.get("review"):
        return detail["review"]
    old = LEGACY_ESCAPE.fullmatch(detail.get("reason") or "")
    if not old:
        return None
    return {
        "what": old.group(1),
        "found": escape_found(old.group(2), bot_actor(slug), conn),
        "preview": escape_preview(old.group(2)),
        "task": None,
    }


def quarantine_resumes(conn, slug):
    """Since when a bot is quarantined, whether it lifts by itself (a refusal count), and when (`resumes_at`)."""
    since = _one(conn, QUARANTINE_SINCE_SQL, (bot_actor(slug),))["created_at"]
    auto = not quarantine_is_escape(conn, slug)
    return {
        "since": since,
        "auto": auto,
        "resumes_at": shift(since, seconds=QUARANTINE_COOLDOWN_S) if auto and since else None,
    }


def lift_cooled_quarantines(conn, cooldown=QUARANTINE_COOLDOWN_S):
    """After an hour, release refusal-count holds and bot quarantines and start their counts over.
    Runs from the scheduler's tick. Returns the bots whose work was let out; escapes still need a person."""
    from . import task_quarantine

    cutoff = shift(now(), seconds=-cooldown)
    lifted = task_quarantine.release(conn, KEEPER, cutoff=cutoff)
    for row in _rows(conn.execute("SELECT id FROM bots WHERE state='quarantined'")):
        with isolated(conn, "lift_cooled_quarantines", row["id"]):
            slug = row["id"]
            since = _one(
                conn,
                "SELECT max(created_at) AS created_at FROM events WHERE action='quarantine' AND target=?",
                (bot_actor(slug),),
            )
            if quarantine_is_escape(conn, slug) or not since or not since["created_at"] or since["created_at"] > cutoff:
                continue
            status_set(conn, KEEPER, slug, state="active", reason="Cooldown over: the refusal count starts again.")
            if slug not in lifted:
                lifted.append(slug)
    return lifted


def quarantine(conn, slug, reason, context=None, refusal=None):
    """Rule 8's stop: the bot writes nothing more until a human sets it active again. `context` is the
    refusal that stopped it, for the person who reviews it (`quarantine_review`); `refusal` ({rule, detail})
    is the last refused write of a refusal-count quarantine, for the bot when it is back."""
    conn.execute("UPDATE bots SET state='quarantined' WHERE id=?", (slug,))
    event(
        conn,
        KEEPER,
        "quarantine",
        bot_actor(slug),
        {"reason": reason, **({"review": context} if context else {}), **({"refusal": refusal} if refusal else {})},
    )
    # The status line people read says what the check matched, not the rule's own words.
    focus = (
        f"Paused for review: the safety check stopped a {context['what']} naming {context['found']}"
        if context and context.get("found")
        else reason
    )
    try:
        status_set(conn, KEEPER, slug, state="quarantined", focus=focus, reason=reason)
    except Refused:
        pass
    return bot(conn, slug)


QUARANTINE_LIFTED = "Do not retry this action; leave a note for a person if you still need it."


def quarantine_lifted_note(conn, slug):
    """When a quarantine lifts (the cooldown, or a person or BotOps clearing it), a quiet note for the bot's next
    run: the refusal that stopped it and not to retry it. While quarantined nobody could tell it why, and a bot
    back from a cooldown retried the same refused write."""
    row = _one(conn, QUARANTINE_DETAIL_SQL, (bot_actor(slug),))
    detail = (_json(row["detail"], {}) or {}) if row else {}
    review, refused = quarantine_review(conn, slug) or {}, detail.get("refusal") or {}
    if review.get("what"):
        what = f"the safety check stopped a {review['what']}" + (
            f" naming {review['found']}" if review.get("found") else ""
        )
    elif refused.get("detail"):
        what = f"{refused['detail']} ({refused.get('rule')})"
    else:
        what = detail.get("reason") or "a refused write"
    body = f"You were quarantined and are back. The refusal that stopped you: {what}. {QUARANTINE_LIFTED}"
    ident = new_id()
    conn.execute(
        "INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES(?,?,?,?,?)",
        (ident, KEEPER, bot_actor(slug), body[:NOTE_MAX], now()),
    )
    event(conn, KEEPER, "note.create", ident, {"to": bot_actor(slug), "quarantine": True})


# ----------------------------------------------------------------------------- identity
def _actor_exists(conn, actor):
    kind = actor_kind(actor)
    if kind == KEEPER:
        return True
    if kind == "bot":
        return bot(conn, actor_id(actor)) is not None
    if kind == "human":
        return human(conn, actor_id(actor)) is not None
    return False


def _writer(conn, actor, task_id=None, attempt_id=None):
    """Rule 1: the actor must be someone hubdb knows, outside a bot quarantine or a held turn."""
    if not actor_kind(actor):
        refuse(conn, str(actor), "identity", f"{actor!r} is not an actor")
    if not _actor_exists(conn, actor):
        refuse(conn, actor, "identity", f"{actor} is not on the roster")
    if is_bot(actor) and (bot(conn, actor_id(actor)) or {}).get("state") == "quarantined":
        timing = quarantine_resumes(conn, actor_id(actor))
        release = "a human clears it"
        if timing["auto"]:
            at = parse_ts(timing.get("resumes_at"))
            release = "it resumes " + (at.strftime("%Y-%m-%d %H:%M UTC") if at else "after its cooldown")
        refuse(conn, actor, "quarantined", f"{actor_name(conn, actor)} is quarantined; {release}")
    if is_bot(actor):
        from . import task_quarantine

        attempt_id = RUN.get() or attempt_id
        hold = task_quarantine.for_attempt(conn, {"id": attempt_id}) if attempt_id else None
        hold = hold or task_quarantine.for_task(conn, actor_id(actor), task_id or REFUSAL_TASK.get())
        if hold:
            refuse(
                conn,
                actor,
                "quarantined",
                "This task or chat is held after repeated refusals; it resumes after "
                "its cooldown or when a person clears quarantine. Other tasks can continue",
                task=task_id,
            )
    return actor


def _message_writer(conn, actor, msg, conv):
    """A message's task reference cannot mask its thread's own task association."""
    _writer(conn, actor, task_id=(conv or {}).get("task_id"))
    _writer(conn, actor, task_id=message_task_id(msg, conv))


def resolve_actor(conn: object, value: object) -> "str | None":
    """`cmo`, `bot:cmo`, `ana`, `human:ana`, `keeper` -> the actor string, or None.

    A bare name no bot or person answers to is the one live bot built from that template (`growth-manager`), if there is
    exactly one and no open seat is set up from that template (`_template_owner`).

    None is what rule 2 refuses on: an address that is not a bot, a human or the keeper.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if actor_kind(raw) == "bot":
        # `bot:<id>` or `bot:<slug>`: the same bot either way (resolve_bot takes both)
        found = resolve_bot(conn, actor_id(raw))
        return bot_actor(found["id"]) if found else None
    if actor_kind(raw):
        return raw if _actor_exists(conn, raw) else None
    found = resolve_bot(conn, raw)
    if found:
        return bot_actor(found["id"])
    if human(conn, raw):
        return human_actor(raw)
    return _template_owner(conn, raw)


def _template_owner(conn, name):
    """The one live bot built from the template `name` (a template's own `--owner growth-manager`), as its actor.
    None when no live bot is, or two are: an ambiguous name is still refused. An archived bot is not live. An open seat
    set up from the template (lifecycle.py: its `template`, closed once a bot fills it) is one more claim on the name."""
    if not _has_table(conn, "bot_settings"):
        return None
    found = _rows(
        conn.execute(
            "SELECT s.bot_id FROM bot_settings s JOIN bots b ON b.id=s.bot_id "
            "WHERE s.template=? AND b.state!='archived' LIMIT 2",
            (name,),
        )
    )
    if (
        _has_table(conn, "seats")
        and conn.execute("SELECT 1 FROM seats WHERE template=? AND closed_at IS NULL LIMIT 1", (name,)).fetchone()
    ):
        return None
    return bot_actor(found[0]["bot_id"]) if len(found) == 1 else None


HELD_STATES = ("quarantined", "paused")
# A bot that exists but is not running. A write refused only for that says nothing about the sender.
INACTIVE_STATES = HELD_STATES + ("planned",)


def back_when(conn, slug, state):
    """When a held or planned bot runs again, in words the sender can act on."""
    if state == "planned":
        from .setup_live import being_set_up

        return "is still being set up" if being_set_up(conn, slug) else "has not started yet"
    resumes = quarantine_resumes(conn, slug)["resumes_at"] if state == "quarantined" else None
    if resumes:
        return f"is quarantined until about {resumes[11:16]} UTC, when it resumes by itself"
    return f"is {state} until a person resumes it"


def target_inactive(conn, slug, state, sender=None):
    """The refusal text for a write to a bot that is not running: when it is back, so the sender waits instead of
    retrying. A bot sender is also told how little it counts against it; a person is never reviewed for a refusal, so
    a person reads only when the bot is back."""
    back = f"{actor_name(conn, bot_actor(slug))} {back_when(conn, slug, state)}."
    if sender is not None and not is_bot(sender):
        return f"{back} Send it again once it is back"
    return (
        f"{back} This refusal is not a repeat and opens no review of you; "
        "only the day's limit of 30 refusals counts it. Send it again once it is back"
    )


def held_note(conn, target):
    """What the sender of a kept message reads when its bot is paused or quarantined; else None."""
    if not is_bot(target):
        return None
    slug = actor_id(target)
    state = (bot(conn, slug) or {}).get("state")
    if state not in HELD_STATES:
        return None
    return f"{actor_name(conn, target)} {back_when(conn, slug, state)}. Your message is kept for when it is back; no need to resend it."


def held_for(conn, actor, target):
    """Whether a note `actor` leaves a paused or quarantined bot is kept for when it is back, rather than refused
    (rule 2; messages and task comments are always kept, see say): a person's, or, to a quarantined bot, its own manager bot's (bot_config.reports_to, else its declared
    reports_to, as auth.bot_contact reads it), so it can say why it stopped. A paused bot stays a person's."""
    if is_human(actor):
        return True
    target = resolve_actor(conn, target)
    if not (is_bot(actor) and is_bot(target)) or not _has_table(conn, "bot_config"):
        return False
    if (bot(conn, actor_id(target)) or {}).get("state") != "quarantined":
        return False
    row = (
        _one(
            conn,
            f"SELECT bc.reports_to,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bc.bot=?",
            (actor_id(target),),
        )
        or {}
    )
    boss = str(row.get("reports_to") or (_json(row.get("config"), {}) or {}).get("reports_to") or "")
    return bool(boss) and not boss.startswith("human:") and actor_id(boss) == actor_id(actor)


def _reach(conn, actor, target, allow_planned=False, allow_held=False):
    """Rule 2: an active bot, or any human on the roster. Anything else is out of reach.

    `allow_planned` is onboarding's one exception: a bot that has been defined but has not
    started yet may still be handed the task that brings it up, so the backlog is waiting
    when its machine comes online. Nothing runs until the bot is active either way.

    A bot that exists but is paused, quarantined or planned is refused as `target_inactive`, not
    `reach`: the sender broke no rule, so rule 8 does not count it as a repeat (_escalate).
    """
    if str(target).startswith("seat:") and _has_table(conn, "seats"):
        _recruiting(conn, actor, target)
    resolved = resolve_actor(conn, target)
    if not resolved:
        refuse(
            conn, actor, "reach", f"{target} is not a bot or a person on the roster", classify(str(target), conn=conn)
        )
    if is_bot(resolved):
        slug = actor_id(resolved)
        state = (bot(conn, slug) or {}).get("state")
        if (
            state != "active"
            and not (allow_planned and state == "planned")
            and not (allow_held and state in HELD_STATES)
        ):
            if state in INACTIVE_STATES:
                refuse(conn, actor, "target_inactive", target_inactive(conn, slug, state, actor))
            refuse(conn, actor, "reach", f"{actor_name(conn, resolved)} is {state}, not active")
    return resolved


def task_taking_refusal(conn, actor, target, title=None, parent=None, routine=False, *, labels=None, task=None):
    """A bot a person syncs in from another platform reads only its chat with them, so a task for it would
    sit open with nobody to work it: it is refused with what to do instead. So is a task its profile says it does
    not take (backend/profiles.py): none for a Responder, only review and QA requests for a Reviewer, only its
    watcher's for a Monitor. Returns (rule, why) or None without recording a refusal, so automatic assignment
    can skip an ineligible bot. `title`, `parent` and `labels` say what is being filed, `task` which existing task is
    being given; `routine` is a routine's own run."""
    if not is_bot(target) or not _has_table(conn, "bot_config"):
        return
    from .harnesses import SYNCED_HARNESSES, resolve_harness

    row = _one(
        conn,
        f"SELECT b.display_name,{BS.CONFIG} AS config FROM bots b JOIN bot_config bc ON bc.bot=b.id {BS.JOIN} "
        "WHERE b.id=?",
        (actor_id(target),),
    )
    if row and resolve_harness(_json(row["config"], {}) or {}) in SYNCED_HARNESSES:
        name = row["display_name"] or actor_id(target)
        return (
            "external-bot",
            f"{name} works on another platform and only syncs its chat here, so it "
            "never sees tasks. Write to it in its chat, or give this task to a bot that runs in Tico.",
        )
    if row:
        from . import profiles

        return profiles.refusal(
            conn,
            row["display_name"] or actor_id(target),
            _json(row["config"], {}) or {},
            actor,
            title,
            parent,
            routine,
            owner=target,
            labels=labels,
            task=task,
        )


def _takes_tasks(conn, actor, target, title=None, parent=None, routine=False, *, labels=None, task=None):
    if found := task_taking_refusal(conn, actor, target, title, parent, routine, labels=labels, task=task):
        refuse(conn, actor, *found)


def recruiting(conn, target):
    """Why a Recruiting seat (backend/lifecycle.py) takes no work, when `target` is one; else None. It holds no
    work, which stays with the manager it reports to."""
    if not str(target or "").startswith("seat:") or not _has_table(conn, "seats"):
        return None
    seat = _one(conn, "SELECT title,reports_to FROM seats WHERE id=? AND closed_at IS NULL", (str(target)[5:],))
    if not seat:
        return None
    reports = str(seat["reports_to"] or "")
    manager = (
        (human(conn, reports[6:]) or {}).get("name") or reports[6:]
        if reports.startswith("human:")
        else (bot(conn, reports) or {}).get("display_name") or reports
    )
    return f"{seat['title']} is still being recruited, so it can't take work yet" + (
        f". Give it to {manager}, who it reports to." if manager else "."
    )


def _recruiting(conn, actor, target):
    """The sender broke nothing, so a seat's refusal is `target_inactive`."""
    if why := recruiting(conn, target):
        refuse(conn, actor, "target_inactive", why)


def _onboarding(conn, slug):
    """Whether this bot is onboarding (backend/lifecycle.py); False on a database with no setup state at all."""
    if not _has_table(conn, "bot_config") or "onboarding_state" not in {
        r[1] for r in conn.execute("PRAGMA table_info(bot_config)")
    }:
        return False
    from .statuses import PARKED

    row = _one(conn, "SELECT onboarding_state FROM bot_config WHERE bot=?", (slug,))
    return bool(row) and row["onboarding_state"] in PARKED


def _learning(conn, actor, target):
    """An onboarding bot learns and takes no task until a person above it marks onboarding complete
    (backend/lifecycle.py). The sender broke nothing, so it is refused as `target_inactive`, like a paused bot."""
    if is_bot(target) and _onboarding(conn, actor_id(target)):
        name = (bot(conn, actor_id(target)) or {}).get("display_name") or actor_id(target)
        refuse(
            conn,
            actor,
            "target_inactive",
            f"{name} is still onboarding, so it can't take tasks yet. Someone above "
            "it on the org chart marks onboarding complete when it is ready.",
        )


def _learner(conn, actor):
    """An onboarding bot files no task of its own while it learns: it asks in chat instead."""
    if is_bot(actor) and _onboarding(conn, actor_id(actor)):
        refuse(
            conn,
            actor,
            "onboarding",
            "You are still onboarding, so you can't create or hand out tasks yet. Ask "
            "your manager in chat; a person above you marks onboarding complete when you are ready.",
        )


# The retired Health Monitor's slug (archived by backend/migrations/0096_retire_health_monitor.py). Its id is its slug, so its actor is bot:health-monitor.
HEALTH_MONITOR = "health-monitor"


def _botops_only(conn, actor, target):
    """The Health Monitor gives its fixes to BotOps and never a person a task or a message nobody asked for
    (docs/health-monitor.md): BotOps decides who to ask."""
    if actor != bot_actor(HEALTH_MONITOR) or not is_human(target or ""):
        return
    refuse(
        conn,
        actor,
        "botops-only",
        "The Health Monitor gives its fixes to BotOps, not to a person: file it with "
        "`tico task create --owner botops` (or hand a task to it with `tico task update <id> --owner botops`), and "
        "BotOps decides who to ask",
    )


# ----------------------------------------------------------------------------- registry sync
def default_human(conn):
    """Who an unaddressed human item goes to: the roster's `default_user`, else its first
    person. The environment configures this in registry/people.yaml, never here."""
    if _one(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='registry_metadata'"):
        row = _one(conn, "SELECT value FROM registry_metadata WHERE key='people'")
        pid = str((_json(row["value"], {}) if row else {}).get("default_user") or "")
        if pid and human(conn, pid):
            return pid
    row = _one(conn, "SELECT id FROM humans ORDER BY rowid LIMIT 1")
    return row["id"] if row else ""


def sync_registry(conn, employees, people):
    """Bring `bots` and `humans` up to date with the registry. Nothing is ever deleted.

    `employees` is `{slug: entry}` as the registry merge returns it (defaults
    merged); `people` is `registry/people.yaml`
    as a parsed document, or the list of person rows inside it.
    """
    ts = now()
    for slug, e in (employees or {}).items():
        e = e or {}
        state = str(e.get("status") or "active").strip() or "active"
        if state not in BOT_STATES:
            state = "active"
        # The runtime, model and host are the entry's own, in bot_config (the seed writes the whole entry there).
        row = {
            "slug": slug,
            "display_name": str(e.get("display_name") or slug),
            "effort": str(e.get("reasoning_effort") or e.get("effort") or ""),
            "state": state,
            "created_at": ts,
        }
        have = resolve_bot(conn, slug)
        if have is None:
            # a registry bot's id is its slug: its actor string is the one the code already builds from it
            row["id"] = slug
            conn.execute(
                "INSERT INTO bots (id, slug, display_name, effort, state, created_at) "
                "VALUES (:id, :slug, :display_name, :effort, :state, :created_at)",
                row,
            )
        else:
            # never lower a state a human set
            row["id"] = have["id"]
            row["state"] = "quarantined" if have.get("state") == "quarantined" else state
            conn.execute("UPDATE bots SET display_name=:display_name, effort=:effort, state=:state WHERE id=:id", row)
    doc = people if isinstance(people, dict) else {"people": people or []}
    for p in doc.get("people") or []:
        p = p if isinstance(p, dict) else {}
        pid = str(p.get("id") or str(p.get("email") or "").split("@")[0]).strip()
        if not pid:
            continue
        teams = sorted({t for t in [str(p.get("team") or "").strip(), *(p.get("primary_for") or [])] if t})
        row = {
            "id": pid,
            "name": str(p.get("name") or pid.title()),
            "email": str(p.get("email") or "").lower(),
            "slack_id": str(p.get("slack_id") or "").strip() or None,
            "teams": _dump(teams),
        }
        if human(conn, pid) is None:
            conn.execute(
                "INSERT INTO humans (id, name, email, slack_id, teams) VALUES (:id, :name, :email, :slack_id, :teams)",
                row,
            )
        else:
            conn.execute(
                "UPDATE humans SET name=:name, email=:email, "
                "slack_id=COALESCE(:slack_id, slack_id), teams=:teams "
                "WHERE id=:id",
                row,
            )
    event(conn, KEEPER, "registry.sync", "", {"bots": len(employees or {}), "humans": len(doc.get("people") or [])})
    return {"bots": bots(conn), "humans": humans(conn)}


# ----------------------------------------------------------------------------- conversations
def open_conversation(
    conn,
    actor,
    participants,
    kind="chat",
    subject="",
    task_id=None,
    scope=None,
    owner_actor=None,
    room_key=None,
    allow_planned=False,
    allow_held=False,
    announce=True,
):
    """A thread between any set of bots and humans. Reach (rule 2) is checked per participant. `announce=False`: the
    caller has not finished it (who reads a task's room depends on the task), and announces it when it has."""
    _writer(conn, actor)
    if kind not in CONVERSATION_KINDS:
        refuse(conn, actor, "kind", f"a conversation is {'|'.join(CONVERSATION_KINDS)}, not {kind}")
    people = []
    for p in participants or []:
        resolved = p if resolve_actor(conn, p) == actor else _reach(conn, actor, p, allow_planned, allow_held)
        if resolved not in people:
            people.append(resolved)
    if actor not in people:
        people.insert(0, actor)
    # A person's new DM (any set of people, or bots beyond a canonical one-bot room) is `personal`, however it was
    # asked for: `participants` is for migrated rows alone, and no owner inspection reaches a new DM.
    new_dm = (
        scope is None
        and not task_id
        and is_human(actor)
        and len(people) >= 2
        and all(is_human(p) or is_bot(p) for p in people)
        and (len(people) > 2 or all(is_human(p) for p in people))
    )
    if scope is None:
        scope = "task" if task_id else "direct"
        humans = [p for p in people if is_human(p)]
        if kind == "chat" and len(humans) == 1 and "bot:assistant" in people:
            scope, owner_actor, room_key = "personal", humans[0], "assistant"
    if scope not in CONVERSATION_SCOPES:
        refuse(conn, actor, "scope", f"a conversation scope is {'|'.join(CONVERSATION_SCOPES)}, not {scope}")
    if scope == "personal":
        humans = [p for p in people if is_human(p)]
        owner_actor = owner_actor or (humans[0] if len(humans) == 1 else None)
        if not owner_actor or owner_actor not in people or not is_human(owner_actor) or not room_key:
            refuse(conn, actor, "scope", "a personal conversation needs one participant owner and a room key")
    if scope == "shared" and not room_key:
        refuse(conn, actor, "scope", "a shared conversation needs a room key")
    row = {
        "id": new_id(),
        "kind": kind,
        "subject": str(subject or ""),
        "task_id": task_id,
        "participants": _dump(people),
        "scope": scope,
        "owner_id": owner_actor,
        "room_key": room_key,
        "created_at": now(),
        "last_message_at": None,
        "closed_at": None,
    }
    conn.execute(
        "INSERT INTO conversations (id, kind, subject, task_id, participants, "
        "scope, owner_id, room_key, created_at, last_message_at, closed_at) "
        "VALUES (:id, :kind, :subject, :task_id, :participants, :scope, :owner_id, "
        ":room_key, :created_at, :last_message_at, :closed_at)",
        row,
    )
    event(conn, actor, "conversation.open", row["id"], {"kind": kind, "scope": scope, "participants": people})
    if new_dm and scope == "direct":
        from . import threads

        threads.personal_dm(conn, row["id"], actor, people)
    made = conversation(conn, row["id"])
    if announce:
        from . import threads

        threads._emit_thread_created(conn, made, actor)
    return made


def chat_home(conn, actor, other, kind="chat", *, create=True):
    """The Home a message with no room named goes to, when the pair is a person and a bot whose chats have moved into the
    bot's Home (backend/bot_home_merge.py home_for_chat), else None. `kind` is the pair's kind as `_pair_conversation`
    takes it: an ask or a notice is never a chat."""
    if kind != "chat" or is_human(actor) == is_human(other):
        return None
    from . import bot_home_merge

    person, bot = (actor, other) if is_human(actor) else (other, actor)
    return bot_home_merge.home_for_chat(conn, person, actor_id(bot), create=create)


def _pair_conversation(conn, actor, other, kind, subject="", allow_held=False):
    """The live conversation of this kind between these two, opened if there is none."""
    want = {actor, other}
    if is_human(actor) and is_human(other):
        # Two people have one DM, a native row included: its participant list names only its starter.
        from . import threads

        dm = threads._open_dm(conn, actor, [other])
        if dm:
            return conversation(conn, dm)
    home = chat_home(conn, actor, other, kind)
    if home:
        return conversation(conn, home)
    for row in _rows(
        conn.execute(
            "SELECT * FROM conversations WHERE kind=? AND closed_at IS NULL AND task_id IS NULL "
            "ORDER BY COALESCE(last_message_at, created_at) DESC LIMIT 200",
            (kind,),
        )
    ):
        if set(_json(row["participants"], []) or []) == want:
            return conversation(conn, row["id"])
    return open_conversation(conn, actor, [actor, other], kind=kind, subject=subject, allow_held=allow_held)


def _opener(conn, conversation_id):
    """Who started this conversation: the first message's sender, else whoever opened it, which may be
    the person even when only notices are in the room.

    The keeper's notices do not count: a "New task" notice can land first in a person's chat with a
    bot, and the person's own message after it is what the bot answers."""
    first = _one(
        conn,
        "SELECT sender_id FROM messages WHERE conversation_id=? AND sender_id<>? ORDER BY created_at LIMIT 1",
        (conversation_id, KEEPER),
    )
    if first:
        return first["sender_id"]
    row = _one(
        conn,
        "SELECT actor FROM events WHERE action='conversation.open' AND target=? ORDER BY created_at LIMIT 1",
        (conversation_id,),
    )
    return row["actor"] if row else None


# ----------------------------------------------------------------------------- messages
def check_message_refs(refs, *, make_page=False):
    """Only the task Make page route mints the initiating marker."""
    if refs and "make_page" in refs and not make_page:
        from .store import Problem

        raise Problem("forbidden", "Use the task’s Make page control", 403)


def say(
    conn,
    actor,
    to_actor,
    body,
    conversation_id=None,
    kind="say",
    refs=None,
    in_reply_to=None,
    wait_s=None,
    *,
    make_page=False,
):
    """The one write every message goes through: rules 1, 2, 3, 4, 7 and 8 all live here."""
    check_message_refs(refs, make_page=make_page)
    on_task = message_task_id({"refs": refs}, conversation(conn, conversation_id) if conversation_id else None)
    with refusal_task(on_task):
        return _say(conn, actor, to_actor, body, conversation_id, kind, refs, in_reply_to, wait_s)


def _say(conn, actor, to_actor, body, conversation_id, kind, refs, in_reply_to, wait_s):
    _writer(conn, actor)
    if kind in OLD_MESSAGE_KINDS:
        refuse(conn, actor, "kind", ASK_GONE)
    if kind not in MESSAGE_KINDS:
        refuse(conn, actor, "kind", f"a message is {'|'.join(MESSAGE_KINDS)}, not {kind}")
    severity = classify(body, to_actor=resolve_actor(conn, to_actor), where="message", actor=actor, conn=conn)
    on_task = (refs or {}).get("task")
    path_mentioned(conn, actor, "task note" if on_task else "message", body, on_task)
    # A message to a paused or quarantined bot is kept: runners claim only active bots' work
    # (execution.claim), so it runs when the bot is back. Refusing a bot's message instead made the
    # sender repeat it and quarantined the sender too, spreading one quarantine to every bot it works with.
    # A note is kept only when held_for says so.
    target = _reach(conn, actor, to_actor, allow_held=True)
    refs = dict(refs or {})
    warnings = []
    from . import threads

    if threads.never_wakes(refs, target):
        refs["quiet"] = True  # decided before anything below reads quiet: no hold, no note, no run

    if is_bot(actor) and actor == target:
        refuse(conn, actor, "self", "a bot cannot send work to itself")

    if conversation_id:
        # A person's old chat with a bot moved into the bot's Home: what still names it (an old task's room, a run
        # that started before the move) is written there (backend/bot_home_merge.py). Only a closed room can have moved.
        conv = conversation(conn, conversation_id)
        if conv and conv.get("closed_at"):
            from . import bot_home_merge

            moved = bot_home_merge.home_for(conn, conversation_id)
            if moved:
                conversation_id, conv = moved, conversation(conn, moved)
        if not conv:
            refuse(conn, actor, "not-found", f"no conversation {conversation_id}")
    else:
        conv = _pair_conversation(conn, actor, target, "notice" if kind == "notice" else "chat", allow_held=True)

    _message_writer(conn, actor, {"refs": refs}, conv)

    # rules 4 and 7: what a person has to read
    if is_bot(actor) and is_human(target) and kind in ("say", "notice"):
        if not threads.solicited(conn, conv["id"], actor, target, refs, in_reply_to):
            # Counted per delivery (backend/threads.py): what reached this person, addressed or not.
            n = threads.unsolicited_today(conn, actor, target)
            if n >= UNSOLICITED_PER_DAY:
                refuse(
                    conn,
                    actor,
                    "unsolicited",
                    f"{n} unsolicited messages to {actor_name(conn, target)} today; the cap is "
                    f"{UNSOLICITED_PER_DAY}. File a task instead.",
                    severity,
                )
            warnings = style_problems(conn, actor, lint_human_item(body), severity)
            refs["unsolicited"] = True

    # Token saving (Dev 149): a plain bot-to-bot message waits for the target's next run unless flagged.
    from . import bot_to_bot

    from . import rules as wake_rule_engine

    # A plain bot-to-bot message waits under `bot_to_bot` unless a wake rule says otherwise (backend/rules.py).
    decided = wake_rule_engine.bot_to_bot_decision(conn, actor, target, kind, refs, body, conv, in_reply_to)
    waits = bool(decided and decided.rule == wake_rule_engine.BOT_TO_BOT)
    if waits:
        refs.update(quiet=True, **{bot_to_bot.MARK: bot_to_bot.RULE})
    msg = _write_message(
        conn, actor, target, body, conv, kind, refs, in_reply_to, wait_s, check_cap=True, decided=decided
    )
    if waits:
        bot_to_bot.hold(conn, msg, task(conn, on_task) if isinstance(on_task, str) and on_task else None)
    if warnings:
        event(conn, actor, "message.lint", msg["id"], {"problems": warnings})
        return {**msg, "warnings": list(msg.get("warnings") or []) + warnings}
    return msg


# One rule decides both whether a new message is charged and which stored messages count: _held_queue_drains,
# read from the stored references. It needs no job or note, so it holds for an external harness's inbox (hermes,
# openclaw and the rest get no job without a delivery computer, and a waking bot-to-bot message gets no carry-note)
# and on a hub database without Store's job queue. A record that settles a message later only ever removes it:
# a job finished or canceled (a duplicate wake), or carry-notes that are no longer waiting.


def _held_queue_drains(actor, target, refs_json):
    """Whether a bot-to-bot message, stored or about to be stored, is held for the recipient until it resumes.

    The single test for the held queue: admission applies it before insert and the count applies it to every
    pending stored row. A bot's message to itself, a quiet message nothing carries (a wake=False comment, a
    same-state skip) and live run output are never handed over, and charging them would fill the cap for good."""
    if actor == target:
        return False  # enqueue queues no job for a bot's message to itself
    try:
        refs = refs_json if isinstance(refs_json, dict) else json.loads(refs_json or "{}")
    except ValueError:
        return True
    if not isinstance(refs, dict):
        return True
    from . import bot_to_bot, wake_rules

    if refs.get("quiet"):
        held = refs.get(wake_rules.MARK)
        return refs.get(bot_to_bot.MARK) == bot_to_bot.RULE or bool(held and held not in wake_rules.SILENT)
    live = refs.get("live")
    return not (isinstance(live, dict) and live.get("kind") in ("transcript", "decision", "tool"))


def _held_queue_settled_sql(conn):
    """SQL true for a stored message a later record says resume will not hand over, on whichever tables exist."""
    settled = []
    if _has_table(conn, "jobs"):
        # A runner-delivered bot gets a held message only through its job, so one finished or canceled (a
        # duplicate wake) is never handed over. An external harness's inbox still holds it whatever its job did.
        from .enqueue import external_bot_sql

        settled.append(
            "(NOT " + external_bot_sql("m.recipient_id") + " AND EXISTS(SELECT 1 FROM jobs j "
            "WHERE j.message_id=m.id) AND NOT EXISTS(SELECT 1 FROM jobs j "
            "WHERE j.message_id=m.id AND j.state NOT IN ('completed','canceled')))"
        )
    waiting = NOTE_WAITING_SQL if _has_table(conn, "attempts") else "n.canceled_at IS NULL"
    settled.append(
        "(EXISTS(SELECT 1 FROM notes n WHERE n.message_id=m.id) AND NOT EXISTS(SELECT 1 FROM notes n "
        f"WHERE n.to_actor=m.recipient_id AND n.message_id=m.id AND {waiting}))"
    )
    return " OR ".join(settled)


def _held_queue_pending(conn, target, *, excluding=None):
    """Per sender, the items and stored bytes of every pending message _held_queue_drains holds for target."""
    size_sql = "coalesce(length(CAST(m.body AS BLOB)),0) + coalesce(length(CAST(m.refs AS BLOB)),0)"
    totals = {}
    for r in conn.execute(
        f"SELECT m.sender_id, m.refs, {size_sql} AS bytes FROM messages m WHERE m.recipient_id=? "
        "AND m.delivered_at IS NULL AND m.deleted_at IS NULL AND m.sender_id LIKE 'bot:%' "
        f"AND m.sender_id<>m.recipient_id AND (? IS NULL OR m.id<>?) AND NOT ({_held_queue_settled_sql(conn)})",
        (target, excluding, excluding),
    ):
        if _held_queue_drains(r["sender_id"], target, r["refs"]):
            t = totals.setdefault(r["sender_id"], {"items": 0, "bytes": 0})
            t["items"] += 1
            t["bytes"] += r["bytes"]
    return totals


def _held_queue_admit(conn, actor, target, body, refs_json, *, replacing=None):
    """Charge bot-authored pending messages across every room that resume will deliver.

    Called with the final stored text/references under Store's BEGIN IMMEDIATE write transaction,
    immediately before insertion. Permission/contact checks belong to the caller and still run first.
    System notices, people, active recipients and recovery controls retain their existing behavior.
    """
    if not isinstance(refs_json, str):
        refs_json = _dump(refs_json) or "{}"  # a message view holds refs decoded; charge its stored text
    if not (is_bot(actor) and is_bot(target)) or not _held_queue_drains(actor, target, refs_json):
        return
    state = (bot(conn, actor_id(target)) or {}).get("state")
    if state not in HELD_STATES:
        return
    totals = _held_queue_pending(conn, target, excluding=replacing)
    items = sum(t["items"] for t in totals.values())
    size = sum(t["bytes"] for t in totals.values())
    own = totals.get(actor)
    added = len(body.encode("utf-8")) + len(refs_json.encode("utf-8"))
    for label, used, charge, limit in (
        ("recipient item", items, 1, HELD_QUEUE_ITEMS),
        ("recipient byte", size, added, HELD_QUEUE_BYTES),
        ("your item", own["items"] if own else 0, 1, HELD_QUEUE_SENDER_ITEMS),
        ("your byte", own["bytes"] if own else 0, added, HELD_QUEUE_SENDER_BYTES),
    ):
        if used + charge > limit:
            refuse(
                conn,
                actor,
                "queue_full",
                f"{actor_name(conn, target)} is {state}; its held queue is full ({label} limit {limit}). "
                "Already accepted items are kept. Wait until the recipient resumes before sending again. "
                "Waiting does not count against the sender and does not release a quarantine.",
            )


def held_queue_recheck(conn, message_id):
    """Recheck a newly inserted message after its sending path adds stored references, in the same transaction."""
    msg = message(conn, message_id)
    if msg and not msg.get("delivered_at") and not msg.get("deleted_at"):
        _held_queue_admit(conn, msg["sender_id"], msg["recipient_id"], msg["body"], msg["refs"], replacing=message_id)


def unless_queue_full(conn, write):
    """Run `write`, a system path's one extra message to a bot, so a full held queue withholds only that message.

    Returns (result, None), or (None, the Refused) with everything `write` did undone when the recipient's held
    queue was full. The work around it (a finished run, a stored change) stands: the sender did not choose to
    send, so nothing is refused against it. Any other refusal is raised as before."""
    conn.execute("SAVEPOINT held_queue_write")
    try:
        out = write()
    except Refused as exc:
        if exc.rule == "queue_full":
            conn.execute("ROLLBACK TO held_queue_write")
            conn.execute("RELEASE held_queue_write")
            return None, exc
        conn.execute("RELEASE held_queue_write")
        raise
    except BaseException:
        conn.execute("RELEASE held_queue_write")
        raise
    conn.execute("RELEASE held_queue_write")
    return out, None


def _write_message(
    conn,
    actor,
    target,
    body,
    conv,
    kind,
    refs,
    in_reply_to,
    wait_s,
    expires_at=None,
    delivered_at=None,
    read_at=None,
    *,
    check_cap=False,
    decided=None,
):
    if actor == "bot:librarian":
        body = librarian_text(body)
    if conv and ("closed_at" not in conv or conv.get("closed_at")):
        # A person's old chat with a bot is the bot's Home now: whatever writes to the room (a task's notice, a reconnect
        # notice, a run's reply) writes to the Home (backend/bot_home_merge.py). Only a closed room can have moved, so an
        # open one, the common case, costs no lookup.
        from . import bot_home_merge

        moved = bot_home_merge.home_for(conn, conv["id"])
        if moved and moved != conv["id"]:
            conv = conversation(conn, moved)
    if (VIA.get() or BY_TOKEN.get()) and is_human(actor):
        # The Assistant, BotOps or a personal token wrote this for the person: not by their hand (delegated).
        refs = {**(refs or {}), "via": VIA.get() or "token"}
    from . import wake_rules

    if actor == KEEPER and str(target).startswith("bot:") and is_human(wake_rules.CAUSE.get()):
        # Tico's notice of a person's own act keeps who acted: the request's cause ends with it, and the reply
        # rule at the turn's end (execution.quiet_recheck) must still see that a person is owed the answer.
        refs = {**(refs or {}), wake_rules.CAUSED_BY: wake_rules.CAUSE.get()}
    # Token saving 2/9: a wake its bot's own write caused, or news about a parked task, waits for the next run. The
    # rules engine (backend/rules.py) decides, names the rule on the message (refs.rule) and fails open.
    from . import threads as thread_rules

    if thread_rules.never_wakes(refs, target) and not (refs or {}).get("quiet"):
        refs = {**refs, "quiet": True}  # a writer that came here without `say` (a remote author's post)
    from . import rules as wake_rule_engine

    if isinstance(refs, dict) and wake_rule_engine.PARKED_NOTICE in refs:
        refs = {k: v for k, v in refs.items() if k != wake_rule_engine.PARKED_NOTICE}
    decision = wake_rule_engine.judge(conn, actor, target, kind, refs, body, conv, in_reply_to, decided)
    if decision and decision.outcome in ("ask", "action"):
        decision = wake_rule_engine.carry_out(conn, decision, actor, target, kind, refs, body, conv)
    held = None
    if decision:
        refs = wake_rule_engine.stamp(refs, decision)
        held = decision.reason
        if held:
            refs = {**refs, "quiet": True, wake_rules.MARK: held}
    row = {
        "id": new_id(),
        "conversation_id": conv["id"],
        "sender_id": actor,
        "recipient_id": target,
        "kind": kind,
        "body": str(body or ""),
        "refs": _dump(SRC.stamp(refs or {}, actor)),
        "in_reply_to": in_reply_to,
        "created_at": now(),
        "delivered_at": delivered_at,
        "read_at": read_at,
        "expires_at": expires_at,
        "wait_s": wait_s,
    }
    if delivered_at is None:
        _held_queue_admit(conn, actor, target, row["body"], row["refs"])
    # Native threads (backend/threads.py): the origin, revision and restricted flag; the insert trigger adds the seq.
    from . import threads

    threads.stamp(conn, row, conv, refs)
    # Capacity waiting is not loop misconduct, even when this conversation also hit its cap.
    # rule 3: the loop caps. A message written through threads.post is held by the thread loop guard instead, which
    # makes the run it would start wait (`capped`) rather than refusing the message.
    if check_cap and is_bot(actor) and is_bot(target) and threads.POSTING.get() is None:
        since = shift(now(), hours=-1)
        # The cap stops two bots talking in circles, so it counts bot-to-bot messages only: the
        # keeper's notices (a routine's "new task", "finished") and a person's messages are not a
        # loop. A bot's private room with its owner, with a sweep every 30 minutes, could hit
        # the cap on messages of which many were notices or the owner's.
        n = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND created_at>=? "
            "AND sender_id LIKE 'bot:%' AND recipient_id LIKE 'bot:%' AND kind<>'notice'",
            (conv["id"], since),
        ).fetchone()[0]
        if n >= CAP_PER_HOUR:
            refuse(conn, actor, "cap", f"{n} messages in this conversation in the last hour; the cap is {CAP_PER_HOUR}")
    conn.execute(
        "INSERT INTO messages (id, conversation_id, sender_id, recipient_id, kind, body, refs, "
        "in_reply_to, created_at, delivered_at, read_at, expires_at, wait_s, origin_id, revision, restricted, "
        "legacy_audience) VALUES (:id, :conversation_id, :sender_id, :recipient_id, :kind, :body, :refs, "
        ":in_reply_to, :created_at, :delivered_at, :read_at, :expires_at, :wait_s, :origin_id, :revision, "
        ":restricted, :legacy_audience)",
        row,
    )
    if is_human(actor) and is_bot(target) and row["body"].lstrip().startswith("/"):
        from . import skill_runtime

        skill_runtime.record_thread_usage(conn, row, conv, refs)  # a person's `/name` for a company skill
    from .enqueue import enqueue

    enqueue(conn, row)  # the job it starts, in this transaction (backend/enqueue.py)
    conn.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (row["created_at"], conv["id"]))
    # A mention outside the audience grants nothing and warns the author (contract §2): carried on the result.
    mention_warnings = (threads.record(conn, message(conn, row["id"]), conv) or {}).get("warnings") or []
    event(conn, actor, f"message.{kind}", row["id"], {"to": target, "conversation": conv["id"]})
    if held:
        wake_rules.hold(conn, message(conn, row["id"]), held, rule_id=decision.rule)
    written = message(conn, row["id"])
    return {**written, "warnings": mention_warnings} if mention_warnings else written


def feed(conn, target, body, conv, refs=None):
    """Context the system hands a bot to read: a Slack channel digest (backend/slack_gateway.py).

    Written by the keeper, not by a bot or a person: nobody said it. It is a turn for the bot
    (enqueue queues its job, as for any message to a bot), but it is quoted Slack text, so the
    escape and lint rules a bot's own words go through do not apply; what it quotes was said
    in a channel the bot is in. Rule 2 still holds: the bot must be active.
    """
    resolved = _reach(conn, KEEPER, target)
    return _write_message(conn, KEEPER, resolved, body, conv, "say", {"wake": "feed", **(refs or {})}, None, None)


def has_pick_options(msg):
    """Questions offering only free text keep the ordinary answer protocol."""
    refs = _json(msg.get("refs"), {}) or {}
    return any(q.get("options") for q in refs.get("questions", []))


def notice(conn, actor, to_actor, body, refs=None, expires_days=NOTICE_DAYS):
    """An fyi. It is never a task; it leaves the inbox after `expires_days`."""
    msg = say(conn, actor, to_actor, body, kind="notice", refs=refs)
    stop = shift(msg["created_at"], days=int(expires_days or NOTICE_DAYS))
    conn.execute("UPDATE messages SET expires_at=? WHERE id=?", (stop, msg["id"]))
    return message(conn, msg["id"])


def _secondary_recipient(conn, message_id, recipient):
    """True when `recipient` is one of the message's further deliveries (a mention, a DM member), not its addressee:
    their receipt is theirs alone (message_deliveries), the message row's is the addressee's."""
    if recipient is None:
        return False
    row = _one(conn, "SELECT recipient_id FROM messages WHERE id=?", (message_id,))
    return bool(row) and row["recipient_id"] != recipient


def mark_delivered(conn, actor, message_id, recipient=None):
    """The keeper sets this only once the runtime accepted the input (at-least-once delivery). The addressee's
    delivery row follows the message row (a trigger, backend/threads.py); another recipient's is set here."""
    if _secondary_recipient(conn, message_id, recipient):
        conn.execute(
            "UPDATE message_deliveries SET delivered_at=COALESCE(delivered_at,?), state=CASE WHEN "
            "state='pending' THEN 'delivered' ELSE state END, updated_at=? WHERE message_id=? AND recipient=?",
            (now(), now(), message_id, recipient),
        )
    else:
        conn.execute("UPDATE messages SET delivered_at=? WHERE id=? AND delivered_at IS NULL", (now(), message_id))
    event(conn, actor, "message.delivered", message_id)
    return message(conn, message_id)


def mark_read(conn, actor, message_id, recipient=None):
    if _secondary_recipient(conn, message_id, recipient):
        conn.execute(
            "UPDATE message_deliveries SET read_at=COALESCE(read_at,?), state=CASE WHEN state IN "
            "('pending','delivered') THEN 'read' ELSE state END, updated_at=? WHERE message_id=? AND recipient=?",
            (now(), now(), message_id, recipient),
        )
    else:
        conn.execute("UPDATE messages SET read_at=? WHERE id=? AND read_at IS NULL", (now(), message_id))
    event(conn, actor, "message.read", message_id)
    return message(conn, message_id)


# ----------------------------------------------------------------------------- tasks
def can_move(conn, actor):
    """Whether this person may move any task: on a mover team in the roster (MOVER_TEAMS).
    The environment owner is decided by the caller."""
    row = human(conn, actor) if is_human(actor) else None
    return bool(row and set(row.get("teams") or []) & set(MOVER_TEAMS))


# ----------------------------------------------------------------------------- who works a task
GENERAL_MANAGER = "bot:general-manager"


def general_manager_acts(conn, actor, row):
    """The General Manager's narrow mover right (docs/general-manager.md): on a task it may read it moves and closes it with
    a note, relates, comments and reassigns within the department. It is not a `mover` (no lane, number, labels or
    tags) and never a bot with rights on a bot, a credential, a send or spending. The slug alone is not
    enough: the bot behind it must be built from the general-manager template, so a bot someone names the same cannot ride on
    it. A private task it is not on stays unreadable (task_private_readable), so this never widens privacy."""
    if actor != GENERAL_MANAGER or not row:
        return False
    built = (
        _one(conn, f"SELECT s.template AS template FROM bot_config bc {BS.JOIN} WHERE bot=?", ("general-manager",))
        if _has_table(conn, "bot_config")
        else None
    )
    return (built or {}).get("template") == "general-manager" and task_private_readable(conn, actor, row)


def task_private(conn, row):
    """Read partial rows conservatively; callers often select only the task's identity fields."""
    if not row:
        return True
    row = dict(row)
    if "private" in row:
        return bool(row["private"] is None or row["private"])
    found = _one(conn, "SELECT private FROM tasks WHERE id=?", (row["id"],))
    return not found or bool(found["private"] is None or found["private"])


def task_private_readable(conn, actor, row):
    """Private tasks admit their parties, their readers (`tasks.readers`, a group's by its members), bot parties’
    org-chart managers, meeting participants and the Rules Owner page copies' readers."""
    if not row:
        return False
    row = dict(row)
    from . import counsel, teammate_access

    copied = counsel.page_copies(conn).get(row["id"])
    if copied and (not copied["removed"] or copied["page"]["state"] == "deleted"):
        # Native hub connections autocommit. A read gate must not leave their later writes in a new transaction.
        outside = not conn.in_transaction
        try:
            if not counsel.page_copy_readable(conn, copied, teammate_access._viewer_for(conn, actor)):
                return False
        finally:
            if outside and conn.in_transaction:
                conn.rollback()
    if not task_private(conn, row):
        return True
    if "owner_id" not in row or "requester_id" not in row:
        row = task(conn, row["id"])
        if not row:
            return False
    if actor in (row["owner_id"], row["requester_id"]):
        return True
    from .task_rights import private_subjects

    inherited = private_subjects(conn, actor)
    if any(row[k] in inherited for k in ("owner_id", "requester_id")):
        return True
    from .meeting_audience import participant_tasks

    if row['id'] in participant_tasks(conn, actor):
        return True
    if "readers" not in row:
        row = _one(conn, "SELECT readers FROM tasks WHERE id=?", (row["id"],)) or {}
    return task_reader(conn, actor, row)


def assistant_turn_person(who):
    """Whether `who` is a person's own Assistant turn: the person acts, and the Assistant's bot is its task actor."""
    return getattr(who, "via", "") == "assistant" and who.role in ("owner", "human")


# ----------------------------------------------------------------------------- a private task's readers
# Who can see a private task: its requester and owner, and its reader list (`tasks.readers`, a JSON list of people,
# bots and `group:<id>`s). A group reader names everyone in it and in the groups under it, decided at each check, so
# leaving the group takes the access away. Anyone who may change the task changes the list (set_task_readers), and
# each add and remove is in the task's history. The inherited readers (bot parties' managers, meeting participants,
# The Rules Owner page copies) are task_private_readable's and are never listed.


def task_readers(row):
    """The task's reader list as stored: people, bots and `group:<id>`s, in the order they were added."""
    value = (row or {}).get("readers")
    if isinstance(value, (list, tuple)):
        return list(value)
    return [a for a in (_json(value, []) or []) if isinstance(a, str) and a]


def reader_names(conn, actor):
    """The reader-list values that name `actor`: itself and every group it is in (its own and each one above it)."""
    from . import people as P, views

    roster = views.roster(conn, shared=True)
    if is_bot(actor):
        own = P.team_of(actor_id(actor), views.entries(conn, shared=True), roster) or ""
    elif is_human(actor):
        own = (P.person(actor_id(actor), roster) or {}).get("team") or ""
    else:
        own = ""
    return [actor] + ["group:" + g for g in (P.group_chain(own, roster) or ([own] if own else []))]


def task_reader(conn, actor, row):
    """Whether `actor` is on the task's reader list, by name or through a group."""
    readers = task_readers(row)
    if not readers or not actor:
        return False
    if actor in readers:
        return True
    return any(r.startswith("group:") for r in readers) and bool(set(readers) & set(reader_names(conn, actor)))


def reader_actors(conn, row):
    """The people and bots the reader list names, each group's by its members now."""
    from .teammate_access import group_member_actors

    out = set()
    for reader in task_readers(row):
        out |= group_member_actors(conn, reader[6:]) if reader.startswith("group:") else {reader}
    return out


def _reader_target(conn, actor, value):
    """A reader list value from what a caller named: a person, a bot or `group:<id>`; refused otherwise."""
    value = str(value or "").strip()
    if value.startswith("group:"):
        from . import views

        if value[6:] not in ((views.roster(conn, shared=True) or {}).get("org_groups") or {}):
            refuse(conn, actor, "not-found", f"no group {value[6:]}")
        return value
    who = resolve_actor(conn, value)
    if not who or not (is_human(who) and human(conn, actor_id(who)) or is_bot(who) and bot(conn, actor_id(who))):
        refuse(conn, actor, "not-found", f"{value} is not a person, bot or group on the team")
    return who


def new_readers(conn, actor, row, names):
    """The readers `names` would add to task `row`: not on its list yet, and not its requester or owner, who read it
    already. A bot adds people and bots only: a group may be the whole company, which only a person opens it to."""
    have = set(task_readers(row)) | {row.get("owner_id"), row.get("requester_id")}
    added = [r for r in dict.fromkeys(_reader_target(conn, actor, v) for v in names) if r not in have]
    if is_bot(actor) and any(r.startswith("group:") for r in added):
        refuse(conn, actor, "identity", "Only a person adds a group to Who can see")
    return added


def may_change_readers(conn, actor, row, mover=None):
    """Whoever may change the task (task_update's identity rule) changes its readers: its parties, their managers and
    delegates, a party above it, or a task mover."""
    if mover is None:
        mover = is_human(actor) and can_move(conn, actor)
    return actor == KEEPER or _task_party(conn, actor, row) or bool(task_mover(conn, actor, row, mover))


def set_task_readers(conn, actor, task_id, add=(), remove=(), *, replace=None, mover=None, check=None):
    """Add and remove readers of private task `task_id` (or `replace` the whole list); each change is a task event.
    `check(added)` may refuse the readers being added (task_privacy.require_readers). Returns the list as it is now."""
    _writer(conn, actor, task_id=task_id)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    if not task_private(conn, row):
        refuse(conn, actor, "private", "A company task is seen by everyone: make it private to choose who sees it")
    if not may_change_readers(conn, actor, row, mover):
        refuse(
            conn,
            actor,
            "identity",
            "Only the requester, owner, their managers or a task mover change who sees a private task",
        )
    before = task_readers(row)
    if replace is not None:
        fresh = new_readers(conn, actor, row, replace)
        wanted = [
            r for r in dict.fromkeys(_reader_target(conn, actor, v) for v in replace) if r in before or r in fresh
        ]
    else:
        gone = {
            _reader_target(conn, actor, v) if str(v).startswith("group:") else resolve_actor(conn, v) or v
            for v in remove
        }
        kept = [r for r in before if r not in gone]
        fresh = [r for r in new_readers(conn, actor, row, add) if r not in kept]
        wanted = kept + fresh
    if fresh and check:
        check(fresh)
    if len(wanted) > MAX_TASK_READERS:
        refuse(conn, actor, "lint", f"A private task lists at most {MAX_TASK_READERS} readers: add a group instead")
    if wanted == before:
        return before
    conn.execute(
        "UPDATE tasks SET readers=?, updated_at=? WHERE id=?", (_dump(wanted) if wanted else None, now(), task_id)
    )
    for reader in before:
        if reader not in wanted:
            _task_event(conn, task_id, actor, "reader", reader, None, "")
    for reader in wanted:
        if reader not in before:
            _task_event(conn, task_id, actor, "reader", None, reader, "")
    return wanted


MAX_TASK_READERS = 50


# A private task with no person on it (a bot's task for another bot, or for itself) would have no way to reach anyone:
# every hand-off from its runs is refused because nobody it could go to reads it. Its owning bot may bring in ONE
# person by handing them work: a subtask under it, or an ask on it. That person becomes a reader
# of that task and nothing else: not its parent, its other subtasks, or anything the run also carries. Once a person
# reads it, a hand-off to anyone else is refused: a person on the task widens it (Who can see), not the bot.


def bot_only(row):
    """No person on this task: its requester and owner are both bots (the same bot for a bot's task to itself), and
    no person is on its reader list."""
    return (
        is_bot(row.get("requester_id") or "")
        and is_bot(row.get("owner_id") or "")
        and not any(is_human(r) for r in task_readers(row))
    )


def task_reach_refusal(conn, actor, row, person):
    """Why `actor` may not make `person` a reader of private task `row`, or None when it may (or they already read
    it). The words are the refusal a create or an ask gives, and a dry-run gives the same."""
    if not row or not task_private(conn, row) or task_private_readable(conn, person, row):
        return None
    row = task(conn, row["id"]) or row
    who = actor_id(person)
    if not is_human(person) or not human(conn, who):
        return f"{person} is not a person on the team"
    people = [r for r in task_readers(row) if is_human(r)]
    if people and is_bot(row.get("requester_id") or "") and is_bot(row.get("owner_id") or ""):
        names = ", ".join(actor_name(conn, p) for p in people)
        return (
            f"This private task already reached {names}, and {who} cannot read it: hand this to {names}, or ask "
            "a person on the task to add them under Who can see"
        )
    if not bot_only(row):
        return "Private task content stays with its requester and current owner"
    if not task_represents(conn, actor, row["owner_id"]):
        return "Only the bot that owns a private task or its managers bring a person into it"
    for parent_id in TR.ancestors(conn, row["id"]):
        parent = task(conn, parent_id)
        if (
            parent
            and task_private(conn, parent)
            and not bot_only(parent)
            and not task_private_readable(conn, person, parent)
        ):
            return (
                f"A private task above this one has a person on it whom {who} cannot read, so its work "
                f"cannot reach {who}"
            )
    return None


def subtask_flows_up(conn, row, parent):
    """Whether private subtask `row`'s words may reach readers of `parent`, its private parent, who cannot read the
    subtask: every party and reader of the subtask reads the parent. The person a bot handed a subtask or an ask
    under its task reads that task, so their answer comes back to the task it was asked for, and to nobody that task
    does not already have."""
    if not row or not parent or not task_private(conn, parent) or TR.parent_of(conn, row["id"]) != parent["id"]:
        return False
    row = task(conn, row["id"]) or {}
    parties = [p for p in (row.get("requester_id"), row.get("owner_id")) if p] + sorted(reader_actors(conn, row))
    return bool(parties) and all(task_private_readable(conn, p, parent) for p in parties)


def task_reach(conn, actor, row, person):
    """Make `person` a reader of private task `row` (task_reach_refusal said yes); a no-op when they already read it.
    Recorded in the task's history, so its parties see who it reached."""
    if task_private_readable(conn, person, row):
        return
    readers = task_readers(task(conn, row["id"]))
    conn.execute("UPDATE tasks SET readers=? WHERE id=?", (_dump(readers + [person]), row["id"]))
    _task_event(conn, row["id"], actor, "reader", None, person, "")


def _task_private_writer(conn, actor, row):
    # The keeper performs internal maintenance; it is never an authenticated task reader.
    if actor != KEEPER and not task_private_readable(conn, actor, row):
        refuse(conn, actor, "not-found", "Task not found")


def private_tasks_default(conn, actor, declared=None):
    """`declared(slug)` answers from bot configurations already read (backend/bot_rows.py)."""
    if not is_bot(actor) or not _has_table(conn, "bot_config"):
        return False
    from .shared_bots import declared as stored, source_of

    declared = declared or (lambda slug: stored(conn, slug))
    config = declared(actor_id(actor))
    if source_of(config):
        config = declared(source_of(config))
    return config.get("private_tasks_default", config.get("template") in PRIVATE_BY_TEMPLATE) is True


# The templates whose tasks are private unless their config says otherwise: a general counsel's, and the Rules Owner's (a
# document's text and a can-I question are a company's own, so a teammate who cannot read the source cannot read them).
PRIVATE_BY_TEMPLATE = ("general-counsel", "rules-owner")
# The built-in Rules Owner's slug (backend/counsel.py): reserved under Tico's own name, so a company's own bot named
# "counsel" (a legal team's, say) is never taken for it.
RULES_OWNER = "rules-owner"
# The built-in Design Owner's slug (docs/onboarding.md): it owns design and keeps the brand book.
DESIGN_OWNER = "design-owner"
# Bots the product names in code and refers to by slug (the built-in helpers and the assistant). Their id is their
# slug, so that the actor string the code builds from the constant, `bot:<slug>`, is still the bot's actor. A bot a
# person creates gets a UUIDv7 id (new_bot_id).
PRODUCT_BOT_SLUGS = frozenset(
    {
        FLEET_MAINTAINER,
        RULES_OWNER,
        DESIGN_OWNER,
        "librarian",
        "strategy-owner",
        "general-manager",
        "health-monitor",
        "finance-owner",
        "doc-updater",
        "support-owner",
        "assistant",
        "market-analyst",
        "social-media-manager",
        "cpo",
        "updates-owner",
        "automations-owner",
    }
)


def new_bot_id(slug):
    """The id a new bot is created with: its slug for a product bot (PRODUCT_BOT_SLUGS, or the Assistant an install
    names with TICO_ASSISTANT_BOT), else a UUIDv7."""
    assistant = os.environ.get("TICO_ASSISTANT_BOT", "").strip()
    return slug if slug in PRODUCT_BOT_SLUGS or (assistant and slug == assistant) else uuid7()


def bot_folder(bot_id, declared):
    """The folder a bot's checkout has on a computer, as recorded: its config's `folder` for a bot made with a UUID id
    (named once, when it was made), else `bot-<id>` for an older bot, whose id is its first slug (a computer that
    still has that bot's older `emp-<id>` folder finds it, clients/manifest.py folder_dir)."""
    folder = str((declared or {}).get("folder") or "").strip() if isinstance(declared, dict) else ""
    return folder or "bot-" + str(bot_id)


def bot_folders_taken(conn):
    """Every folder name a bot's checkout has or may have on a computer, lowercased (a Mac's disk ignores case): each
    bot's recorded folder, its older `emp-<id>` one, and the folder its recorded repository clones into."""
    taken = set()
    for row in conn.execute(
        f"SELECT b.id, {BS.CONFIG} AS config, bc.repo FROM bots b LEFT JOIN bot_config bc ON bc.bot=b.id {BS.JOIN}"
    ):
        try:
            declared = json.loads(row["config"]) if row["config"] else {}
        except ValueError:
            declared = {}
        taken.add(bot_folder(row["id"], declared).lower())
        taken.add(("emp-" + str(row["id"])).lower())
        repo = str(row["repo"] or "").strip().rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        if repo:
            taken.add(repo.lower())
    return taken


def free_bot_folder(conn, slug, bot_id):
    """The folder for a new bot with a UUID id: `bot-<slug>`, or `bot-<slug>-<8 hex of its id>` when another bot's
    checkout has or had that name (a bot renamed away from this slug keeps its folder). The id's last hex digits are
    its random part; its first ones are the time it was made, which two bots made within a minute share."""
    taken = bot_folders_taken(conn)
    folder = "bot-" + slug
    if folder.lower() not in taken:
        return folder
    tail = str(bot_id).replace("-", "")
    for width in (8, 12, 16, len(tail)):
        folder = f"bot-{slug}-{tail[-width:]}"
        if folder.lower() not in taken:
            return folder
    raise ValueError(f"no free folder for {slug}")


def folder_clash(conn, bot_id, runner_id):
    """The slug of another bot on this computer whose checkout would be this bot's folder, or None. A branch of a bot
    with branches works in its original's checkout by design (shared_from), so it neither clashes nor is clashed with."""

    def declared(raw):
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            value = {}
        return value if isinstance(value, dict) else {}

    mine = _one(conn, BS.ONE_SQL, (bot_id,))
    config = declared(mine["config"] if mine else None)
    if config.get("shared_from"):
        return None
    folder = bot_folder(bot_id, config).lower()
    for row in conn.execute(
        f"SELECT b.id, b.slug, {BS.CONFIG} AS config FROM assignments a JOIN bots b ON b.id=a.bot "
        f"LEFT JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN} WHERE a.runner_id=? AND a.bot<>?",
        (runner_id, bot_id),
    ):
        other = declared(row["config"])
        if not other.get("shared_from") and bot_folder(row["id"], other).lower() == folder:
            return row["slug"]
    return None


def bot_repository_name(conn, ref):
    """The GitHub repository name a bot's repository is created under: for a bot with a recorded folder, its recorded
    repository's name (the folder's, unless a person gave another), so two bots that were once given the same slug
    never share one; for an older bot, `bot-<its slug>`."""
    found = resolve_bot(conn, ref)
    slug = str((found or {}).get("slug") or ref or "").removeprefix("emp-")
    if not found:
        return "bot-" + slug
    row = _one(
        conn, f"SELECT {BS.CONFIG} AS config,bc.repo FROM bot_config bc {BS.JOIN} WHERE bc.bot=?", (found["id"],)
    )
    try:
        declared = json.loads(row["config"]) if row and row["config"] else {}
    except ValueError:
        declared = {}
    if not isinstance(declared, dict) or not declared.get("folder"):
        return "bot-" + slug
    repo = str(row["repo"] or "").strip().rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return repo if re.fullmatch(r"[A-Za-z0-9._-]+", repo or "") else str(declared["folder"])


def migrate_task_privacy(conn):
    """Classify pre-privacy rows from stored roster/config identities; keep ambiguity private."""
    had_privacy = 'private' in {row[1] for row in conn.execute('PRAGMA table_info(tasks)')}
    _apply(conn, TASK_PRIVACY_SCHEMA)
    if not had_privacy:
        humans = {row[0] for row in conn.execute('SELECT id FROM humans')}
        bots = {row[0] for row in conn.execute('SELECT slug FROM bots')}
        # Numbered step 23 runs on a file from before migration 0070 (the declaration still in bot_config.config);
        # the store's cloud step 57 runs after the migrations (the declaration in bot_settings).
        if not _has_table(conn, 'bot_config'):
            configs = {}
        elif 'config' in {row[1] for row in conn.execute('PRAGMA table_info(bot_config)')}:
            configs = {row[0]: _json(row[1]) for row in conn.execute('SELECT bot,config FROM bot_config')}
        else:
            configs = {
                row[0]: _json(row[1])
                for row in conn.execute(f'SELECT bot,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN}')
            }

        def sensitive_or_unknown(actor):
            if actor == KEEPER:
                return False
            if is_human(actor):
                return actor_id(actor) not in humans
            if not is_bot(actor) or actor_id(actor) not in bots:
                return True
            slug, seen = actor_id(actor), set()
            while slug not in seen:
                seen.add(slug)
                config = configs.get(slug)
                if not isinstance(config, dict):
                    return True
                if config.get('shared_from'):
                    slug = str(config['shared_from'])
                    continue
                setting = config.get('private_tasks_default')
                if 'private_tasks_default' in config and not isinstance(setting, bool):
                    return True
                return setting if setting is not None else config.get('template') == 'general-counsel'
            return True

        rows = _rows(
            conn.execute(
                'SELECT id,requester_id,owner_id,'
                + (
                    'parent_id'
                    if 'parent_id' in {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}
                    else TR.parent_sql() + ' AS parent_id'
                )
                + ' FROM tasks'
            )
        )
        private = {
            row['id']
            for row in rows
            if sensitive_or_unknown(row['requester_id']) or sensitive_or_unknown(row['owner_id'])
        }
        ids = {row['id'] for row in rows}
        children = {}
        for row in rows:
            children.setdefault(row['parent_id'], []).append(row['id'])
            if row['parent_id'] and row['parent_id'] not in ids:
                private.add(row['id'])
        pending = list(private)
        while pending:
            for child in children.get(pending.pop(), []):
                if child not in private:
                    private.add(child)
                    pending.append(child)
        conn.executemany(
            'UPDATE tasks SET private=? WHERE id=?', [(int(row['id'] in private), row['id']) for row in rows]
        )
    add_column(conn, 'conversations', 'scope', "TEXT NOT NULL DEFAULT 'direct'")
    for row in _rows(conn.execute('SELECT * FROM tasks WHERE private IS NULL OR private<>0')):
        isolate_private_task(conn, row)


def isolate_private_task(conn, row):
    """Move tracked task messages intact out of broader rooms and revoke former thread members."""
    if not task_private(conn, row):
        return row
    conv = conversation(conn, row["conversation_id"])
    parties = list(dict.fromkeys([row["requester_id"], row["owner_id"]]))
    if not conv or conv.get("kind") != "task" or conv.get("task_id") != row["id"]:
        thread = _task_room(conn, row, parties)
        if conv:
            for message in _rows(conn.execute("SELECT * FROM messages WHERE conversation_id=?", (conv["id"],))):
                if message_task_id(message, conv) == row["id"]:
                    conn.execute("UPDATE messages SET conversation_id=? WHERE id=?", (thread["id"], message["id"]))
        conv = thread
    conn.execute("UPDATE conversations SET participants=? WHERE id=?", (_dump(parties), conv["id"]))
    return task(conn, row["id"])


def task_reopen_hint(conn, actor, row):
    """Name a next move the caller is allowed to make; closure retains its own authority."""
    closer = row.get('closed_by_id') or row['requester_id']
    if row['status'] == 'closed' and is_bot(actor) and (actor != row['requester_id'] or is_human(closer)):
        return f"ask {actor_name(conn, closer)} to reopen it, or file a new task"
    if actor == row['owner_id'] or is_human(actor) or (actor == row['requester_id'] and row['status'] == 'closed'):
        return f"reopen it with `tico task move {row['id']} reopen -m \"why\" --version N`, or file a new task"
    return f"ask the owner to reopen it with `tico task move {row['id']} reopen`, or file a new task"


def _number_free(conn, actor, number):
    """A number a mover gives a task: a positive whole number no other task has (one sequence for the team)."""
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 999_999_999:
        refuse(conn, actor, "kind", "A task number is a whole number from 1 to 999999999")
    taken = _one(conn, "SELECT id FROM tasks WHERE number=?", (number,))
    if taken:
        refuse(conn, actor, "duplicate", f"#{number} is already another task's number; a number belongs to one task")
    # A deleted task keeps its number in the trash (backend/task_delete.py), restored or purged.
    if _one(conn, "SELECT 1 FROM task_trash WHERE number=?", (number,)):
        refuse(conn, actor, "duplicate", f"#{number} belongs to a deleted task; a number belongs to one task")
    return number


def _next_number(conn, actor, task_id, pipeline_id, note=""):
    """The team's next number (the highest yet, plus one) for a task without one in a pipeline that numbers its tasks
    (one with a prefix)."""
    numbered = _one(conn, "SELECT 1 FROM pipelines WHERE id=? AND prefix IS NOT NULL AND prefix<>''", (pipeline_id,))
    # A deleted task keeps its number in the trash, so a restore never meets it on another task.
    top = (
        "(SELECT MAX(n) FROM (SELECT COALESCE(MAX(number), 0) AS n FROM tasks "
        "UNION ALL SELECT COALESCE(MAX(number), 0) FROM task_trash))"
    )
    if (
        numbered
        and conn.execute(
            f"UPDATE tasks SET number={top} + 1 WHERE id=? AND number IS NULL AND {top}<999999999", (task_id,)
        ).rowcount
    ):
        number = _one(conn, "SELECT number FROM tasks WHERE id=?", (task_id,))["number"]
        _task_event(conn, task_id, actor, "number", None, number, note)
    elif numbered and _one(conn, "SELECT 1 FROM tasks WHERE id=? AND number IS NULL", (task_id,)):
        refuse(conn, actor, "number", "The team's task numbers have reached 999999999")


def _labels(labels):
    """Labels are lower-case free text, one word or a hyphenated few, unique, in the order given."""
    out = []
    for raw in labels if isinstance(labels, (list, tuple)) else str(labels or "").split(","):
        one = re.sub(r"\s+", "-", str(raw or "").strip().lower()).strip("-")[:40]
        if one and one not in out:
            out.append(one)
    return out[:20]


# ----------------------------------------------------------------------------- tags
def tag(conn, ident):
    row = _one(conn, "SELECT * FROM tags WHERE id=? OR key=? ORDER BY id=? DESC LIMIT 1", (ident, ident, ident))
    return _tag_view(row) if row else None


def tag_by_key(conn, key):
    row = _one(conn, "SELECT * FROM tags WHERE key=?", (key,))
    return _tag_view(row) if row else None


def _tag_view(row):
    value = dict(row)
    value["metadata"] = _json(value.pop("metadata"), {}) or {}
    value["is_template"] = bool(value["is_template"])
    return value


def tags(conn, is_template=None):
    where = "" if is_template is None else " WHERE is_template=?"
    args = () if is_template is None else (int(is_template),)
    return [_tag_view(row) for row in conn.execute("SELECT * FROM tags" + where + " ORDER BY label,key", args)]


def tag_can_edit(conn, actor, row, mover=None):
    if mover is None:
        mover = actor == KEEPER or can_move(conn, actor)
    return bool(mover or row.get("owner") == actor)


def tag_create(
    conn,
    actor,
    key,
    label=None,
    metadata=None,
    markdown=None,
    is_template=False,
    template_id=None,
    owner=None,
    mover=None,
):
    _writer(conn, actor)
    keys = _labels([key])
    if not keys:
        refuse(conn, actor, "kind", "give the tag a key")
    key = keys[0]
    if tag_by_key(conn, key):
        refuse(conn, actor, "duplicate", f"tag {key} already exists")
    if not tag_can_edit(conn, actor, {"owner": owner}, mover):
        refuse(conn, actor, "identity", "create a tag you own, or ask a task mover")
    template = tag(conn, template_id) if template_id else None
    if template_id and (not template or not template["is_template"]):
        refuse(conn, actor, "kind", "make an instance from a template tag")
    if template and is_template:
        refuse(conn, actor, "kind", "a template instance is a task tag")
    defaults = dict(template["metadata"]) if template else {}
    defaults.update(metadata or {})
    label = str(label if label is not None else template["label"] if template else key).strip()
    if not label:
        refuse(conn, actor, "kind", "give the tag a label")
    ts, ident = now(), new_id()
    conn.execute(
        "INSERT INTO tags(id,key,label,metadata,markdown,is_template,template_id,owner,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            ident,
            key,
            label,
            _dump(defaults),
            markdown if markdown is not None else template["markdown"] if template else "",
            int(is_template),
            template["id"] if template else None,
            owner,
            ts,
            ts,
        ),
    )
    event(conn, actor, "tag.create", ident, {"key": key, "template_id": template["id"] if template else None})
    return tag(conn, ident)


def tag_update(conn, actor, ident, version, *, label=None, metadata=None, markdown=None, owner=None, mover=None):
    _writer(conn, actor)
    row = tag(conn, ident)
    if not row:
        refuse(conn, actor, "not-found", "no such tag")
    if not tag_can_edit(conn, actor, row, mover):
        refuse(conn, actor, "identity", "this tag is edited by its owner or a task mover")
    if row["version"] != version:
        refuse(conn, actor, "version_conflict", "Tag changed; fetch it and retry your update")
    fields = {
        k: v
        for k, v in (
            ("label", label),
            ("metadata", _dump(metadata) if metadata is not None else None),
            ("markdown", markdown),
            ("owner", owner),
        )
        if v is not None
    }
    if label is not None and not label.strip():
        refuse(conn, actor, "kind", "give the tag a label")
    if not fields:
        return row
    if "owner" in fields:
        fields["owner"] = fields["owner"] or None
    sets = ",".join(f"{name}=?" for name in fields)
    changed = conn.execute(
        f"UPDATE tags SET {sets},updated_at=?,version=version+1 WHERE id=? AND version=?",
        (*fields.values(), now(), row["id"], version),
    ).rowcount
    if not changed:
        refuse(conn, actor, "version_conflict", "Tag changed; fetch it and retry your update")
    event(conn, actor, "tag.update", row["id"], {"fields": list(fields), "version": version + 1})
    return tag(conn, row["id"])


TASK_TAGS_SQL = (
    "SELECT tags.*,task_tags.task_id FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
    "WHERE task_tags.task_id IN (%s) ORDER BY task_tags.rowid"
)


def hydrate_task_tags(conn, rows):
    """Read tag display data for a page in one query, including rows from older readers."""
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        row["tags"] = []
    if by_id:
        marks = ",".join("?" * len(by_id))
        from .read_cache import rows as cached_rows

        for row in cached_rows(conn, TASK_TAGS_SQL % marks, tuple(by_id)):
            value = _tag_view(row)
            by_id[value.pop("task_id")]["tags"].append(value)


def _set_task_tags(conn, actor, task_id, labels, note="", approval_id=None, approved=False):
    """`approved`: _label_writer already used this change's publish approval."""
    keys, resolved = _labels(labels), []
    for key in keys:
        row = tag_by_key(conn, key)
        if row and row["is_template"]:
            refuse(conn, actor, "kind", f"{key} is a template; make an instance to put on a task")
        resolved.append((key, row))
    task_row = task(conn, task_id)
    old = task_labels(task_row)
    from .roadmaps import settings as roadmap_settings, approve as approve_roadmap

    if (
        not approved
        and task_row['visibility'] == 'public'
        and (set(keys) - set(old)) & set(roadmap_settings(conn)['labels'])
    ):
        approve_roadmap(conn, actor, task_id, 'labels', {'labels': keys}, approval_id)
    old_ids = {r[0] for r in conn.execute("SELECT tag_id FROM task_tags WHERE task_id=?", (task_id,))}
    wanted, ordered = set(), []
    for key, row in resolved:
        # Unknown legacy label keys still create plain tags for every existing task writer.
        row = row or tag_create(conn, actor, key, owner=actor, mover=True)
        wanted.add(row["id"])
        ordered.append(row["id"])
    conn.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
    for ident in ordered:
        conn.execute("INSERT INTO task_tags(task_id,tag_id) VALUES(?,?)", (task_id, ident))
        if ident not in old_ids:
            event(conn, actor, "tag.attach", ident, {"task_id": task_id})
    for ident in old_ids - wanted:
        event(conn, actor, "tag.detach", ident, {"task_id": task_id})
    if old != keys:
        _task_event(conn, task_id, actor, "labels", _dump(old), _dump(keys), note)


def _label_writer(conn, actor, row, labels, approval_id=None):
    """A bot that is not a mover changing labels on `row` (task_update): it may change a classification label
    on a task it manages, one it owns or asked for or whose pipeline it manages, and nothing
    else. A label Tico acts on (WORKFLOW_LABELS) stays a mover's. A label on a public roadmap puts the task
    there once it is public, so adding or removing one takes the bot's exact approved publish approval
    (roadmaps.approve), or a mover when the team does not ask bots for one. Refused as rule `label`, which
    counts once per task an hour (_escalate). Returns whether the approval was used."""
    from .roadmaps import settings as roadmap_settings, approve as approve_roadmap

    keys = _labels(labels)
    changed = sorted(set(keys) ^ set(task_labels(row)))
    if not changed:
        return False
    teams = ", ".join(MOVER_TEAMS)
    pipe = _one(conn, "SELECT manager_id FROM pipelines WHERE id=?", (row.get("pipeline_id") or "",)) or {}
    if not task_participant(conn, actor, row) and pipe.get("manager_id") != actor:
        refuse(
            conn,
            actor,
            "label",
            f"{actor_name(conn, actor)} changes labels only on a task it owns, asked for, whose party it manages or whose pipeline it manages; labels on "
            f"{row['id']} are changed by a person on the {teams} teams",
            task=row["id"],
        )
    settings = roadmap_settings(conn)
    public = [k for k in changed if k in settings["labels"]]
    flow = [k for k in changed if k in WORKFLOW_LABELS]
    if public and settings["bot_public_approval"] and approval_id and not flow:
        # An approval that is pending, spent or for other labels is still this label refusal: a bot retrying
        # it would otherwise count every retry and quarantine itself, as before.
        try:
            approve_roadmap(conn, actor, row["id"], "labels", {"labels": keys}, approval_id)
        except Refused as exc:
            if exc.rule != "approval":
                raise
            refuse(
                conn,
                actor,
                "label",
                f"{approval_id} is no OK of exactly this change; a person on the {teams} teams changes it",
                task=row["id"],
            )
        return True
    held = _held_labels(
        flow,
        public,
        teams,
        (
            f"the public roadmap is not open to bots yet: a person on the {teams} teams changes it"
            if settings["bot_public_approval"]
            else f"a person on the {teams} teams changes it"
        ),
    )
    if held:
        rest = [k for k in changed if k not in public and k not in flow]
        refuse(
            conn,
            actor,
            "label",
            "; ".join(held) + (f". {', '.join(rest)} you may change on its own" if rest else ""),
            task=row["id"],
        )
    return False


def _held_labels(flow, public, teams, publish):
    """The clauses of a `label` refusal: `flow` labels Tico acts on, `public` roadmap labels and what `publish`
    says the bot does about them."""
    held = []
    if flow:
        held.append(
            f"{', '.join(flow)} {'changes' if len(flow) == 1 else 'change'} how Tico runs the task, so "
            f"a person on the {teams} teams changes {'it' if len(flow) == 1 else 'them'}"
        )
    if public:
        held.append(
            f"{', '.join(public)} {'is a public roadmap label' if len(public) == 1 else 'are public roadmap labels'}"
            f" (adding or removing one puts the task on a roadmap or takes it off), so " + publish
        )
    return held


def _create_labels(conn, actor, keys):
    """A bot that is not a mover creating a task with `keys` (task_create): _label_writer's rule for the new task.
    A classification label goes on as before; a label Tico acts on stays a mover's, and a public roadmap label is
    refused before anything is written. A publish approval is for a task that exists (publication-preview), so the
    refusal says to create the task without it and add it on the task. Refused as rule `label`."""
    from .roadmaps import settings as roadmap_settings

    settings = roadmap_settings(conn)
    public = [k for k in keys if k in settings["labels"]]
    flow = [k for k in keys if k in WORKFLOW_LABELS]
    teams = ", ".join(MOVER_TEAMS)
    held = _held_labels(
        flow,
        public,
        teams,
        (
            f"the public roadmap is not open to bots yet: create the task without {'it' if len(public) == 1 else 'them'}, "
            f"and a person on the {teams} teams adds {'it' if len(public) == 1 else 'them'}"
            if settings["bot_public_approval"]
            else f"a person on the {teams} teams adds {'it' if len(public) == 1 else 'them'}"
        ),
    )
    if held:
        rest = [k for k in keys if k not in public and k not in flow]
        refuse(conn, actor, "label", "; ".join(held) + (f". {', '.join(rest)} you may set on its own" if rest else ""))


def _lane_for(conn, lane, owner, actor):
    """The pipeline a new task follows: always company (the product lane is retired). A lane
    is still accepted for compatibility and checked, but not stored."""
    if lane and lane not in TASK_LANES:
        refuse(conn, actor, "kind", f"a lane is {'|'.join(TASK_LANES)}, not {lane}")
    return "company"


def _has_table(conn, name):
    # A store connection remembers the tables it has seen, per database file (backend/store.py `_pooled`):
    # tables are created at start and not dropped while serving, and a restored file is a new file.
    seen = getattr(conn, "tables_seen", None)
    known = seen.setdefault(conn.pool_file, set()) if seen is not None and conn.pool_file is not None else None
    if known is not None and name in known:
        return True
    present = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    if present and known is not None:
        known.add(name)
    return present


def _queue_end(conn, owner, top=False):
    """The rank that puts a task at the bottom (or the top) of this owner's queue."""
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    lo, hi = conn.execute(
        f"SELECT MIN(rank), MAX(rank) FROM tasks WHERE owner_id=? AND status IN ({marks})", (owner, *ACTIVE_STATUSES)
    ).fetchone()
    if top:
        return (lo if lo is not None else 1.0) - 1.0
    return (hi if hi is not None else 0.0) + 1.0


PR_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$", re.IGNORECASE)
ISSUE_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)/?$")


def link_kind(url):
    """What a URL is, from its shape: a pull request, an issue, a document, a link."""
    if PR_URL.match(url):
        return "pr"
    if ISSUE_URL.match(url):
        return "issue"
    if re.match(r"^https://(docs\.google\.com|www\.notion\.so|claude\.ai/(code/)?artifact)", url):
        return "doc"
    return "url"


def worktree_heads(detail):
    """What a worktree's branch is known to hold, as the background pass records what it checked: the push webhook's
    head and the computer's committed head, the latter marked while the computer reports it unpushed so a later push
    of that same head is checked again."""
    last = detail.get("last_commit")
    local = last if not last or detail.get("ahead") == 0 else last + " unpushed"
    return sorted({detail.get("pushed"), local} - {None, ""})


@private_task_write
def task_link(conn, actor, task_id, url, title=None, kind=None, mover=None, wake_once=None, release_repo=""):
    """Attach a link to a task.

    `wake_once` arms (True) or disarms (False) one wake on a pull request link: the next time its checks
    finish, it gets an approving or change-requesting review, or a release ships it, the task's owner hears
    once, in the task's conversation. It never changes the task's status (`github.py`)."""
    _writer(conn, actor, task_id=task_id)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_link_allowed(conn, actor, row, mover, kind or link_kind(str(url or "")))
    url = str(url or "").strip()
    if kind == "session":
        return _task_session_link(conn, actor, task_id, url, title)
    if not re.match(r"^https?://\S+$", url):
        refuse(conn, actor, "kind", "a link is an http(s) URL")
    kind = kind or link_kind(url)
    match = PR_URL.match(url)
    app = _one(conn, "SELECT org FROM github_app LIMIT 1") if _has_table(conn, "github_app") else None
    if kind == "pr" and match and app and match.group(1).lower() != app["org"].lower():
        kind = "url"
    if kind not in LINK_KINDS:
        refuse(conn, actor, "kind", f"a link is {'|'.join(LINK_KINDS)}, not {kind}")
    if wake_once is not None and (kind != "pr" or not app):
        refuse(
            conn, actor, "kind", "a one-shot wake needs a pull request link in the connected GitHub App's organization"
        )
    if kind == "pr" and match:
        # GitHub treats owner and repository names case-insensitively, so one pull request is one link.
        # A duplicate made before this matched by exact URL stays reachable through its own URL.
        from . import pr_identity as PR

        candidates = _rows(
            conn.execute(
                "SELECT * FROM task_links WHERE task_id=? AND kind='pr' AND lower(repo)=? AND number=? "
                "ORDER BY url=? DESC, created_at",
                (task_id, f"{match.group(1)}/{match.group(2)}".lower(), int(match.group(3)), url),
            )
        )
        have = next((p for p in candidates if PR.same(p, url)), None)
    else:
        have = _one(conn, "SELECT * FROM task_links WHERE task_id=? AND url=?", (task_id, url))
    if have:
        if wake_once and (
            have["state"] in ("closed", "shipped")
            or have["state"] == "merged"
            and str(have.get("repo") or "").lower() != str(release_repo).lower()
        ):
            refuse(
                conn,
                actor,
                "state",
                f"{have['title']} is {have['state']}"
                + (" outside the repository Tico releases" if have["state"] == "merged" else "")
                + ": no result is left to wake on",
            )
        return _arm_wake_once(conn, actor, have, wake_once)
    if kind == "pr" and match and not is_human(actor):
        from .item_sources import github as GS

        if GS.queues_now(conn, task_id):
            # The Merge stage queues the PRs that were signed off: a new one is linked by a person, or after going back.
            refuse(conn, actor, "task_stage", GS.LINK_IN_MERGE)
    title = str(title or "").strip()
    if not title:
        m = PR_URL.match(url) or ISSUE_URL.match(url)
        title = f"{m.group(2)}#{m.group(3)}" if m else urlsplit(url).netloc
    link = {
        "id": new_id(),
        "task_id": task_id,
        "kind": kind,
        "url": url,
        "title": title[:200],
        "state": "open" if kind == "pr" else None,
        "added_by": actor,
        "created_at": now(),
    }
    conn.execute(
        "INSERT INTO task_links (id, task_id, kind, url, title, state, added_by, created_at) "
        "VALUES (:id, :task_id, :kind, :url, :title, :state, :added_by, :created_at)",
        link,
    )
    match = PR_URL.match(url)
    if kind == "pr" and match:
        conn.execute(
            "UPDATE task_links SET repo=?,number=?,updated_at=? WHERE id=?",
            (match.group(1) + "/" + match.group(2), int(match.group(3)), now(), link["id"]),
        )
        from .item_sources import match_link

        item = match_link(url)
        if item:
            # The item the link is (backend/item_sources), once per task: a second link to it keeps none.
            conn.execute(
                "UPDATE task_links SET source=?,item_type=?,item_key=? WHERE id=? AND NOT EXISTS (SELECT 1 FROM "
                "task_links d WHERE d.task_id=? AND d.source=? AND d.item_type=? AND d.item_key=?)",
                (*item, link["id"], task_id, *item),
            )
        link = _one(conn, "SELECT * FROM task_links WHERE id=?", (link["id"],))
    _task_event(conn, task_id, actor, "link", None, url, title)
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
    event(conn, actor, "task.link", task_id, {"url": url, "kind": kind})
    return _arm_wake_once(conn, actor, link, wake_once)


def _arm_wake_once(conn, actor, link, wake_once):
    """Arm or disarm a pull request link's one wake; None leaves it as it is."""
    if wake_once is None:
        return link
    detail = _json(link.get("detail"), {}) or {}
    if bool(detail.get("wake_once")) == bool(wake_once):
        return link
    if wake_once:
        detail["wake_once"] = {"by": actor, "at": now()}
    else:
        detail.pop("wake_once", None)
    conn.execute("UPDATE task_links SET detail=?,updated_at=? WHERE id=?", (json.dumps(detail), now(), link["id"]))
    event(conn, actor, "task.link.wake_once", link["task_id"], {"url": link["url"], "armed": bool(wake_once)})
    return _one(conn, "SELECT * FROM task_links WHERE id=?", (link["id"],))


# A harness's resume command, optionally after the `cd` into the bot's folder that botResumeCommand writes.
RESUME_COMMAND = re.compile(
    r"^(?:cd \S+ && )?(claude|codex|gemini|grok|pi|cursor-agent|antigravity)"
    r"\s+(?:--resume|resume|-r)\s+([\w.:-]+)$"
)


def _session_detail(link):
    try:
        detail = json.loads(link.get("detail") or "{}")
    except ValueError:
        return {}
    return detail if isinstance(detail, dict) else {}


def _with_thread(link):
    """A session link as the API shows it: the provider thread id joins its detail. The url is the one place the
    thread is stored (`session:<bot>/<thread>`, or the resume command it came from), so the detail does not repeat it."""
    if not link or link.get("kind") != "session":
        return link
    detail, url = _session_detail(link), str(link.get("url") or "")
    thread = None
    if detail.get("bot") and url.startswith(f"session:{detail['bot']}/"):
        thread = url[len(f"session:{detail['bot']}/") :]
    elif detail.get("harness") and url.startswith("session:"):
        resume = RESUME_COMMAND.match(url[len("session:") :])
        thread = resume.group(2) if resume else None
    return {**link, "detail": json.dumps({**detail, "thread_id": thread})} if thread else link


def _task_session_link(conn, actor, task_id, value, title):
    """An alternate session someone tracks on a task: a Bot Desk thread (http), a resume command, an Orca
    reference. Tracking only: task chat goes to the owning bot, never here. A value that is not a page is
    stored as `session:<value>` so nothing that opens a link's url treats it as one."""
    if not value or len(value) > 500 or any(ord(ch) < 32 for ch in value):
        refuse(conn, actor, "kind", "a session link is one line: a URL, a resume command or a reference")
    url = value if re.match(r"^https?://\S+$", value) else "session:" + value
    have = _one(conn, "SELECT * FROM task_links WHERE task_id=? AND url=?", (task_id, url))
    if have:
        return _with_thread(have)
    resume = RESUME_COMMAND.match(value)
    detail = {"primary": False, **({"harness": resume.group(1)} if resume else {})}
    title = str(title or "").strip() or (
        urlsplit(value).netloc if url == value else f"{resume.group(1)} {resume.group(2)[:8]}" if resume else value[:60]
    )
    link = {
        "id": new_id(),
        "task_id": task_id,
        "url": url,
        "title": title[:200],
        "added_by": actor,
        "created_at": now(),
        "detail": json.dumps(detail),
    }
    conn.execute(
        "INSERT INTO task_links (id, task_id, kind, url, title, added_by, created_at, detail, updated_at) "
        "VALUES (:id, :task_id, 'session', :url, :title, :added_by, :created_at, :detail, :created_at)",
        link,
    )
    _task_event(conn, task_id, actor, "link", None, url, title)
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
    event(conn, actor, "task.link", task_id, {"url": url, "kind": "session"})
    return _with_thread(_one(conn, "SELECT * FROM task_links WHERE id=?", (link["id"],)))


def _demote_sessions(conn, task_id, owner=None):
    """At most one primary session per task: the owning bot's (`owner` is the task's owner, `bot:<slug>`).
    Another owner's stays as an alternate."""
    for link in _rows(conn.execute("SELECT * FROM task_links WHERE task_id=? AND kind='session'", (task_id,))):
        detail = _session_detail(link)
        if detail.get("primary") and "bot:" + str(detail.get("bot")) != owner:
            detail["primary"] = False
            conn.execute(
                "UPDATE task_links SET detail=?,updated_at=? WHERE id=?", (json.dumps(detail), now(), link["id"])
            )


# Bots that keep nothing from one task to the next (the Rules Owner): each task is judged on its own. Their runner starts a
# session for every task and never resumes one across tasks, reads and keeps no memory/ or state.md, copies no harness
# memory into the repository, and gets no nightly learnings (backend/learnings.py roster). A bot's config says
# `stateless: true`; a template listed here makes its bots stateless too, so bots built before the flag are.
STATELESS_BY_TEMPLATE = ("rules-owner",)


def stateless(config):
    """Whether a bot keeps nothing from one task to the next. An explicit `stateless` in its config wins."""
    config = config or {}
    if "stateless" in config:
        return config["stateless"] is True
    return config.get("template") in STATELESS_BY_TEMPLATE


def with_stateless(config):
    """A copy of `config` with `stateless` set when stateless() says so by the template too. The session keys
    (clients/sessions.py) read only that key, and the runner's attempt config carries it, so a config read without
    its template (a branch's, or a bot's raw row) keys its sessions as the runner does."""
    config = dict(config or {})
    if stateless(config):
        config["stateless"] = True
    return config


# How far up a session root is looked for: deeper trees are rare, and the cap bounds the walk.
SESSION_ROOT_DEPTH = 20


def session_root(conn, bot, task_id, config=None):
    """The task whose session a bot's turn on `task_id` continues. A subtask someone filed for the bot under
    its task (a person's, another bot's, or a person's subtask under it) does not get a session of its own: the
    walk goes up through parents the same bot owns and stops at the topmost, so it continues that task's session.
    A subtask the bot filed for itself is new work it planned out, so it is its own root and runs in its own
    session beside its parent; a dozen filed from one turn would otherwise run one after another in that turn's lane.
    A parent with another owner, or one whose readers differ, ends the walk: a bot-owned subtask under a
    person's task is its own root, and a session never carries one task's words to people who cannot read it.
    `subtask_session: own` in the bot's config keeps every subtask its own session (clients/sessions.py), and a
    subtask on a check model (backend/check_models.py) is always its own: it reads the subtask, not the parent's thread.
    A stateless bot (`stateless`) has no shared session at all: every task is its own."""
    if (
        not task_id
        or (config or {}).get("subtask_session") == "own"
        or stateless(config)
        or check_model(conn, task_id, bot)
    ):
        return task_id
    root, seen = task_id, {task_id}
    current = _one(conn, "SELECT id,owner_id,requester_id,private FROM tasks WHERE id=?", (task_id,))
    for _ in range(SESSION_ROOT_DEPTH):
        parent_id = TR.parent_of(conn, root) if current and current["requester_id"] != "bot:" + bot else None
        if not parent_id or parent_id in seen:
            break
        parent = _one(conn, "SELECT id,owner_id,requester_id,private FROM tasks WHERE id=?", (parent_id,))
        if not parent or parent["owner_id"] != "bot:" + bot or not _same_readers(conn, current, parent):
            break
        seen.add(parent_id)
        root, current = parent_id, parent
    return root


def _same_readers(conn, one, other):
    """Whether two tasks may share a session: both public, or both private with every party of each able to
    read the other, so nothing one session turn says reaches a reader of either task who could not read both."""
    if task_private(conn, one) != task_private(conn, other):
        return False
    return not task_private(conn, one) or all(
        task_private_readable(conn, who, b)
        for a, b in ((one, other), (other, one))
        for who in (a["owner_id"], a["requester_id"])
        if who
    )


def task_session_started(conn, bot, lane, message_id, thread_id, runtime):
    """Record the owning bot's task session as the task's primary session when its turn starts. Only a
    turn in a task's own lane (`task:<id>`, set at claim from the config the runner keyed its session
    on) is one: a bot's shared thread carries every other conversation too, so it is nobody's task session.
    A subtask's turn runs in its session root's lane (`session_root`), so the link goes on that root: the
    lane set at claim decides, even if the subtask moved since; a lane naming another bot's task links nothing."""
    msg = message(conn, message_id) if message_id else None
    turn_task = msg and message_task_id(msg, conversation(conn, msg["conversation_id"]))
    task_id = (lane or "")[len("task:") :] if (lane or "").startswith("task:") else None
    row = task(conn, task_id) if turn_task and task_id else None
    if not thread_id or not row or row["owner_id"] != "bot:" + bot:
        return
    url = f"session:{bot}/{thread_id}"
    detail = json.dumps({"primary": True, "harness": runtime, "bot": bot})
    links = _rows(conn.execute("SELECT * FROM task_links WHERE task_id=? AND kind='session'", (task_id,)))
    # The row this thread already has, else the bot's primary from an earlier thread (a reset, a
    # compaction), which this one replaces in place.
    target = next((link for link in links if link["url"] == url), None) or next(
        (link for link in links if _session_detail(link).get("primary") and _session_detail(link).get("bot") == bot),
        None,
    )
    if target and (target["url"], target.get("detail")) != (url, detail):
        conn.execute(
            "UPDATE task_links SET url=?,title=?,detail=?,updated_at=? WHERE id=?",
            (url, bot, detail, now(), target["id"]),
        )
    elif not target:
        target = {"id": new_id()}
        conn.execute(
            "INSERT INTO task_links (id, task_id, kind, url, title, added_by, created_at, detail, updated_at) "
            "VALUES (?,?,'session',?,?,?,?,?,?)",
            (target["id"], task_id, url, bot, KEEPER, now(), detail, now()),
        )
    # At most one primary: every other session on the task is an alternate now.
    for link in links:
        if link["id"] != target["id"] and _session_detail(link).get("primary"):
            other = {**_session_detail(link), "primary": False}
            conn.execute(
                "UPDATE task_links SET detail=?,updated_at=? WHERE id=?", (json.dumps(other), now(), link["id"])
            )


def _task_link_allowed(conn, actor, row, mover=None, kind=None):
    _task_private_writer(conn, actor, row)
    if is_human(actor) and kind != "worktree":
        return
    if mover is None:
        mover = actor == KEEPER or is_human(actor) and can_move(conn, actor)
    delegated = _one(
        conn,
        "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>?",
        (row["id"], actor, now()),
    )
    mover = task_mover(conn, actor, row, mover)
    if (
        not mover
        and not task_participant(conn, actor, row)
        and not delegated
        and not task_ancestor_party(conn, actor, row)
        and not (kind is None and general_manager_acts(conn, actor, row))
    ):  # relations only, never pull request links
        refuse(
            conn,
            actor,
            "identity",
            "Only the owner, requester, their managers, delegates or task movers change this task",
        )


@private_task_write
def task_unlink(conn, actor, task_id, link_id, mover=None):
    _writer(conn, actor, task_id=task_id)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    have = _one(conn, "SELECT * FROM task_links WHERE id=? AND task_id=?", (link_id, task_id))
    if not have:
        refuse(conn, actor, "not-found", f"no link {link_id} on {task_id}")
    _task_link_allowed(conn, actor, row, mover, have["kind"])
    from .worktrees import retired

    if (
        have['kind'] == 'worktree'
        and have.get('state') != 'removed'
        and 'detail' in have
        and not retired(conn, have.get('computer_id'))
    ):
        detail = json.loads(have.get('detail') or '{}')
        detail['delete_requested'] = True
        detail.pop('restore_on_reopen', None)
        # Each authorized request also bypasses the computer's old backoff once, after its safety checks.
        detail['cleanup_retry_generation'] = str(uuid.uuid4())
        for key in ('cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up'):
            detail.pop(key, None)
        conn.execute('UPDATE task_links SET detail=?,updated_at=? WHERE id=?', (json.dumps(detail), now(), link_id))
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
        _task_event(conn, task_id, actor, 'link', have['url'], None, 'cleanup requested')
        event(conn, actor, 'task.unlink', task_id, {'url': have['url'], 'cleanup': True})
        return have
    conn.execute("DELETE FROM task_links WHERE id=?", (link_id,))
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
    _task_event(conn, task_id, actor, "link", have["url"], None, "removed")
    event(conn, actor, "task.unlink", task_id, {"url": have["url"]})
    return have


def task_ancestor_party(conn, actor, row):
    if not task_private_readable(conn, actor, row):
        return False
    for parent_id in TR.ancestors(conn, row["id"]):
        parent = task(conn, parent_id)
        if not parent:
            break
        if task_participant(conn, actor, parent):
            return True
    return False


def _task_parent(conn, actor, task_id, parent_id):
    _writer(conn, actor, task_id=parent_id)
    parent = task(conn, parent_id)
    if not parent:
        refuse(conn, actor, "not-found", f"no task {parent_id} to file this under")
    _task_private_writer(conn, actor, parent)
    if (
        is_bot(actor)
        and not task_participant(conn, actor, parent)
        and not task_ancestor_party(conn, actor, parent)
        and not _one(
            conn,
            "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>?",
            (parent["id"], actor, now()),
        )
    ):
        refuse(conn, actor, "identity", "Only the task's parties, their managers and delegates file a subtask under it")
    if task_id and (parent["id"] == task_id or task_id in TR.ancestors(conn, parent["id"])):
        refuse(conn, actor, "kind", "a task cannot become its own ancestor")
    return parent


def descendants(conn, task_id):
    """Every task below this one, each with its `parent_id` (from task_relations)."""
    return _rows(
        conn.execute(
            "WITH RECURSIVE tree(id,parent_id) AS ("
            "SELECT from_task,to_task FROM task_relations WHERE kind='parent' AND to_task=? UNION "
            "SELECT r.from_task,r.to_task FROM task_relations r JOIN tree ON r.to_task=tree.id AND r.kind='parent') "
            f"SELECT t.*,tree.parent_id FROM {TASKS} t JOIN tree ON t.id=tree.id ORDER BY t.created_at,t.id",
            (task_id,),
        )
    )


def children_summaries(conn, task_ids, visible_sql="1"):
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    # Visibility is checked per child by primary key. A `visible` CTE over the whole tasks table was
    # materialized on every call by older SQLite (the server image's), a full scan for one task page.
    seen = "EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.from_task AND (" + visible_sql + "))"
    rows = conn.execute(
        "WITH RECURSIVE tree(root,id,live,direct) AS ("
        f"SELECT r.to_task,r.from_task,1,1 FROM task_relations r WHERE r.to_task IN ({marks}) "
        f"AND r.kind='parent' AND {seen} UNION "
        "SELECT tree.root,r.from_task,tree.live AND parent.status='open',0 "
        "FROM tree JOIN tasks parent ON parent.id=tree.id "
        f"JOIN task_relations r ON r.to_task=tree.id AND r.kind='parent' WHERE {seen}), "
        "prs AS (SELECT task_id,count(*) total,sum(state IN ('merged','shipped')) merged "
        "FROM task_links WHERE kind='pr' AND coalesce(state,'open')!='closed' "
        "AND task_id IN (SELECT id FROM tree) GROUP BY task_id) "
        "SELECT tree.root,count(*) total,sum(t.status IN ('done','closed')) done,"
        "sum(tree.live AND t.status='open') open,"
        "sum(tree.direct) direct_total,sum(tree.direct AND t.status IN ('done','closed')) direct_done,"
        "sum(coalesce(prs.total,0)) prs_total,sum(coalesce(prs.merged,0)) prs_merged "
        "FROM tree JOIN tasks t ON t.id=tree.id LEFT JOIN prs ON prs.task_id=t.id GROUP BY tree.root",
        task_ids,
    )
    keys = ("total", "open", "done", "prs_total", "prs_merged", "direct_total", "direct_done")
    result = {tid: dict.fromkeys(keys, 0) for tid in task_ids}
    for row in rows:
        result[row["root"]] = {key: row[key] for key in keys}
    return result


def children_summary(conn, task_id, visible_sql="1"):
    return children_summaries(conn, [task_id], visible_sql)[task_id]


def pr_state(links):
    states = []
    for link in links:
        if link["kind"] != "pr":
            continue
        if link.get("state") in ("merged", "shipped"):
            states.append("merged")
        elif link.get("state") == "closed":
            continue
        elif link.get("checks") == "failing":
            states.append("failing")
        elif link.get("mergeable") == "conflict":
            states.append("conflict")
        elif link.get("review_state") == "changes_requested":
            states.append("changes_requested")
        else:
            states.append("open")
    return next(
        (state for state in ("failing", "conflict", "changes_requested", "open", "merged") if state in states), None
    )


def ticket_refs(conn, ids):
    """Each task's label (its pipeline's prefix and its number) and its stage's name, for many ids in one query:
    {id: {"label": str|None, "stage": str|None}}. A task with no number, no prefix or no stage has None for that."""
    ids = list(ids)
    refs: dict[str, dict[str, str | None]] = {ident: {"label": None, "stage": None} for ident in ids}
    if not ids:
        return refs
    marks = ",".join("?" * len(ids))
    for row in _rows(
        conn.execute(
            "SELECT k.id, k.number, p.prefix, s.name AS stage FROM tasks k "
            "LEFT JOIN pipelines p ON p.id = k.pipeline_id LEFT JOIN stages s ON s.id = k.stage_id "
            f"WHERE k.id IN ({marks})",
            ids,
        )
    ):
        if row["prefix"] and row["number"]:
            refs[row["id"]]["label"] = f"{row['prefix']}{row['number']}"
        refs[row["id"]]["stage"] = row["stage"]
    return refs


def task_tree(conn, task_id, visible_ids=None):
    rows = descendants(conn, task_id)
    nodes = {r["id"]: {k: r[k] for k in ("id", "title", "status", "owner_id")} for r in rows}
    links = {}
    if nodes:
        marks = ",".join("?" * len(nodes))
        for link in _rows(
            conn.execute(
                f"SELECT task_id,kind,state,checks,mergeable,review_state FROM task_links WHERE task_id IN ({marks})",
                list(nodes),
            )
        ):
            links.setdefault(link["task_id"], []).append(link)
    refs = ticket_refs(conn, nodes)
    for row in rows:
        node = nodes[row["id"]]
        node.update(pr_state=pr_state(links.get(row["id"], [])), children=[], **refs[row["id"]])
    roots = []
    for row in rows:
        (nodes[row["parent_id"]]["children"] if row["parent_id"] in nodes else roots).append(nodes[row["id"]])
    if visible_ids is not None:

        def visible(items):
            result = []
            for node in items:
                if node["id"] in visible_ids:
                    node["children"] = visible(node["children"])
                    result.append(node)
            return result

        roots = visible(roots)
    return roots


def check_model(conn, task_id, bot=None):
    """The model a bot's check subtask runs on (backend/check_models.py), or None. With `bot`, only while that
    bot both filed the check and owns the subtask: a check handed to another bot is that bot's ordinary task."""
    if not task_id or not _has_table(conn, "task_check_models"):
        return None
    if bot is None:
        return _one(conn, "SELECT runtime,model,harness,effort,role FROM task_check_models WHERE task_id=?", (task_id,))
    actor = "bot:" + bot
    return _one(conn, CHECK_MODEL_SQL, (task_id, actor, actor))


CHECK_MODEL_SQL = (
    "SELECT m.runtime,m.model,m.harness,m.effort,m.role FROM task_check_models m "
    "JOIN tasks t ON t.id=m.task_id WHERE m.task_id=? AND m.created_by=? AND t.owner_id=?"
)


def _made_private(conn, actor, task_id):
    """A company-visible task goes private because the task above it is: the flag, its record, and the follow-ups."""
    conn.execute("UPDATE tasks SET private=1,visibility='private' WHERE id=?", (task_id,))
    _task_event(conn, task_id, actor, "private", 0, 1, "")
    _private_followups(conn, task_id)
    from .reconnects import access_changed as reconnects_access_changed

    reconnects_access_changed(conn, task_id)  # a reconnect's person may not read it now


def _private_followups(conn, task_id):
    """A task just made private: its thread narrows to its parties and delegations end."""
    isolate_private_task(conn, task(conn, task_id))
    conn.execute("DELETE FROM task_delegations WHERE task_id=?", (task_id,))


def task_links(conn, task_id):
    from .worktrees import statuses

    return [
        _with_thread(link)
        for link in statuses(
            conn, conn.execute("SELECT * FROM task_links WHERE task_id=? ORDER BY created_at", (task_id,))
        )
    ]


def bot_reads_task(conn, bot, row):
    """Whether this bot may read the task by every rule a read of it checks (privacy, bot activity access, an
    assignment's scope): a notice or an answer it is sent carries the task's words."""
    if not task_private_readable(conn, bot, row):
        return False
    if not _has_table(conn, "bot_config"):
        return True  # no bot has an access list: every bot is open
    from .auth import bot_reads_task as reads

    return reads(conn, bot, row)


@private_task_write
def task_comment(
    conn,
    actor,
    task_id,
    text,
    wake=True,
    *,
    extra_refs=None,
    public=False,
    approval_id=None,
    way=None,
    defer_for=None,
    delegate=None,
    reach=None,
    people_notified=False,
    to=None,
):
    """A comment on a task: one message in the task's thread, tagged with the task, from whoever wrote it. It goes as
    any post there does (threads.post): to whom it addresses (its @mentions, the author it replies to) and to the
    task's followers, each by its way and their level (threads.plan). `way` (steer, queue, or defer until `defer_for`)
    defaults to Steer when `wake` (D13), else to Defer never: each following bot takes it on its next turn here, and
    nobody following is notified. A bot's comment flagged needs-action steers whatever `wake` says (bot_to_bot).
    `people_notified`: a Defer never that binds the bots only (a reader who may not wake them: people are notified as
    their level says). `delegate`: whether a person's comment delegates the task to the bots it wakes (default: `wake`;
    only one who may direct the task does). `reach`: the bot's Identity when this is its own write through the API, so
    whoever the comment reaches is checked against the bot's reach as a thread post is (reach.check_thread). `to`: the
    bot it is written to besides (a relayed answer, strategy_merge). A question to a person is an ask on the task
    (task_ask), never a comment."""
    _writer(conn, actor, task_id=task_id)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    text = str(text or "").strip()
    if not text:
        refuse(conn, actor, "lint", "write the comment")
    if public:
        from .roadmaps import check_public_comment

        if extra_refs:
            refuse(conn, actor, 'kind', 'A public comment is plain text without attachments')
        check_public_comment(conn, actor, row, text, approval_id)
    refs = {"task": task_id, "comment": True, **(extra_refs or {})}
    if way is None:
        way = "steer" if wake or (is_bot(actor) and refs.get("needs_action") is True) else "defer"
        defer_for = defer_for or ("never" if way == "defer" else None)
    conv = conversation(conn, row["conversation_id"])
    if not conv:
        refuse(conn, actor, "not-found", f"task {task_id} has no conversation")
    refs.update(comment_way_refs(way, defer_for))
    if people_notified and way == "defer":
        refs["send_people"] = True
    from . import task_followers, threads

    # A system line is addressed to the task's owner bot, and so is a comment on a task with no thread of its own
    # (filed into a shared chat, which has no task followers): everyone else is reached as in any thread.
    owner = row["owner_id"]
    to = to or (
        owner
        if (
            (actor == KEEPER or not task_followers.thread_id(conn, row))
            and is_bot(owner)
            and owner != actor
            and task_private_readable(conn, owner, row)
        )
        else None
    )
    if to == actor or (to and not task_private_readable(conn, to, row)):
        to = None
    if reach is not None:
        from . import reach as R

        R.check_thread(conn, reach, conv["id"], text)
    msg = threads.post(conn, actor, conv["id"], text, refs=refs, to_actor=to)
    from . import wake_rules

    wake_rules.own_comment(conn, msg, row)  # token saving 2/9: a bot's own comment never wakes it; log the skip
    hook(
        conn,
        "task.commented",
        task_id,
        lambda: _task_facts(conn, task_id, actor, comment_id=msg["id"]),
        actor,
        revision=msg["id"],
    )
    if actor != KEEPER:
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
    if is_human(actor) and (wake if delegate is None else delegate):
        delegate_woken(conn, task_id, actor, msg)
    if is_human(actor) and is_bot(owner) and owner in (bot for bot, _ in _woken_bots(conn, msg)):
        from . import task_engine

        task_engine.person_spoke(conn, task_id, actor)  # the owner bot's run on it is the first of a new count
    if public:
        conn.execute('UPDATE messages SET public=1 WHERE id=?', (msg['id'],))
        msg = message(conn, msg['id'])
    if is_human(actor):
        from . import ask_ingest

        ask_ingest.on_reply(conn, actor, task_id, text, text_sources=refs.get("body_sources", []))
    return msg


def comment_way_refs(way, defer_for=None):
    """The marks a comment's way leaves on it (threads.WAY_KEYS, as the thread routes write them): Steer leaves
    none."""
    if way == "queue":
        return {"send_way": "queue", "delivery": "queue"}
    if way == "defer":
        return {"send_way": "defer", "quiet": True, **({"send_defer_for": defer_for} if defer_for else {})}
    return {}


def delegate_woken(conn, task_id, person, msg):
    """task_delegations: each bot a person's message on the task woke (its own run, or a steer or queue delivery
    pending for it) acts on the task with party rights for 24 hours."""
    until = shift(now(), hours=24)
    for recipient, mid in _woken_bots(conn, msg):
        conn.execute(
            "INSERT OR IGNORE INTO task_delegations(task_id,delegate,requested_by,message_id,expires_at) "
            "VALUES(?,?,?,?,?)",
            (task_id, recipient, person, mid, until),
        )


def _woken_bots(conn, msg):
    """The bots a message wakes, each with the message that starts its run: the bot it is written to, unless it is
    quiet, and each bot a steer or queue delivery is pending for."""
    woken = []
    refs = _json(msg.get("refs"), {}) or {}
    if is_bot(msg.get("recipient_id")) and not refs.get("quiet"):
        woken.append((msg["recipient_id"], msg["id"]))
    return woken + [
        (r["recipient"], r["carrier_id"])
        for r in _rows(
            conn.execute(
                "SELECT recipient, carrier_id FROM message_deliveries WHERE message_id=? AND recipient LIKE 'bot:%' "
                "AND carrier_id IS NOT NULL AND mode IN ('steer','queue') AND state='pending'",
                (msg["id"],),
            )
        )
    ]


def jobs_mark(conn):
    """Where the jobs table stands, to tell later which bot jobs a write queued (comment_delivery)."""
    return conn.execute("SELECT coalesce(max(rowid),0) FROM jobs").fetchone()[0]


def comment_delivery(conn, msg, *, since=0):
    """What a comment write did, for its response: `woke` is true only when the write queued a bot job (the bot will
    take a turn on it), counting the comment's own job, its carriers' and any the write caused elsewhere; `since` is jobs_mark from before the write.
    `delivered` is true when it reached a person, who sees it in their own lists."""
    queued = conn.execute("SELECT 1 FROM jobs WHERE rowid>? OR message_id=?", (since, msg["id"])).fetchone() is not None
    delivered = (
        is_human(msg.get("recipient_id"))
        or conn.execute(
            "SELECT 1 FROM message_deliveries WHERE message_id=? AND recipient LIKE 'human:%' LIMIT 1", (msg["id"],)
        ).fetchone()
        is not None
    )
    return {"woke": queued, "delivered": delivered}


def task_comments(conn, task_id, *, actor=None):
    """Every comment and ask on this task, oldest first, with who wrote it. A deleted comment is not
    listed; an edited one carries `edited_at`."""
    from . import task_privacy as privacy

    row = task(conn, task_id)
    if not row or not row.get("conversation_id"):
        return []
    # A person's old chat with the bot moved into its Home, comments and all: read from where it was written and from
    # the Home it is in now (backend/bot_home_merge.py history_ids).
    from . import bot_home_merge

    cids = bot_home_merge.history_ids(conn, row["conversation_id"])
    conv = conversation(conn, cids[-1])
    out, clauses, args = [], [], []
    # In a bot's room, only the messages that name the task (backend/message_links.py), not the whole room.
    from .message_links import named

    for cid in cids:
        ids = named(conn, task_id, cid)
        if ids is None:
            clauses.append("conversation_id=?")
            args.append(cid)
        else:
            clauses.append("(+conversation_id=? AND id IN (SELECT value FROM json_each(?)))")
            args += [cid, _dump(sorted(ids))]
    sql = "(" + " OR ".join(clauses) + ")"
    if actor is not None:
        # The rows below are read in the snapshot their checks use, so each check takes the row as stored.
        privacy.snapshot(conn)
    rows = _rows(
        conn.execute("SELECT * FROM messages WHERE " + sql + " AND deleted_at IS NULL ORDER BY created_at", args)
    )
    for m in rows:
        m["refs"] = _json(m.get("refs"), {}) or {}
    if actor is not None:
        privacy.prime_messages(conn, rows)
    for m in rows:
        if message_task_id(m, conv) == task_id and m.get("kind") in ("say", "ask", "answer"):
            if actor is not None and not privacy.message_readable(conn, actor, m, current=True):
                continue
            if m["kind"] == "ask" and m["refs"].get("questions"):
                m["ask"] = {**{k: m["refs"].get(k) for k in ("questions", "who")}, "by": m["sender_id"]}
                m["answers"] = review_answers(conn, m["id"], actor=actor)
            if m["refs"].get("answer"):
                m["answer"] = m["refs"]["answer"]
            out.append(m)
    return out


REVIEW_ANSWERS_SQL = (
    "SELECT * FROM messages WHERE deleted_at IS NULL AND ((kind='answer' AND in_reply_to=?) "
    "OR id=(SELECT answered_by_id FROM messages WHERE id=?)) ORDER BY created_at,rowid"
)


def prime_review_answers(conn, messages):
    """Read question replies together as stored rows; each reader still filters their provenance in review_answers."""
    from .read_cache import cache, prime_rows, rows

    values = cache(conn)
    if values is None:
        return []
    asks = {}
    for message in messages:
        refs = _json(message["refs"], {}) or {}
        if message["kind"] == "ask" and isinstance(refs, dict) and refs.get("questions"):
            asks[message["id"]] = message
    wanted = {mid: m for mid, m in asks.items() if (REVIEW_ANSWERS_SQL, (mid, mid)) not in values}
    for part in chunks(sorted(wanted)):
        marks = ",".join("?" * len(part))
        answered = {}
        for mid in part:
            if wanted[mid]["answered_by_id"]:
                answered.setdefault(wanted[mid]["answered_by_id"], set()).add(mid)
        found = {mid: [] for mid in part}
        answers = conn.execute(
            f"SELECT * FROM messages WHERE deleted_at IS NULL AND ((kind='answer' AND in_reply_to IN ({marks})) "
            "OR id IN (SELECT value FROM json_each(?))) ORDER BY created_at,rowid",
            (*part, _dump(list(answered))),
        )
        for row in answers:
            linked = set(answered.get(row["id"], ()))
            if row["kind"] == "answer" and row["in_reply_to"] in found:
                linked.add(row["in_reply_to"])
            for mid in linked:
                found[mid].append(row)
        prime_rows(conn, REVIEW_ANSWERS_SQL, {(mid, mid): replies for mid, replies in found.items()})
    return [row for mid in asks for row in rows(conn, REVIEW_ANSWERS_SQL, (mid, mid))]


def review_answers(conn, message_id, *, actor=None):
    from . import task_privacy as privacy
    from .read_cache import rows

    out = []
    for row in rows(conn, REVIEW_ANSWERS_SQL, (message_id, message_id)):
        if actor is not None and not privacy.message_readable(conn, actor, row, current=True):
            continue
        refs = _json(row["refs"], {}) or {}
        out.append(refs.get("answer") or {"by": row["sender_id"], "text": row["body"], "at": row["created_at"]})
    return out


def comment_on(conn, task_row, message_id):
    """The message `message_id` when it is on this task's thread the way `task_comments` finds it
    (deleted or not, any kind), else None."""
    from . import bot_home_merge

    msg = message(conn, message_id, include_deleted=True)
    if not msg or not task_row or not task_row.get("conversation_id"):
        return None
    # The task's history: its room, and the Homes a person's old chat moved into (bot_home_merge.history_ids).
    if msg["conversation_id"] not in bot_home_merge.history_ids(conn, task_row["conversation_id"]):
        return None
    return msg if message_task_id(msg, conversation(conn, msg["conversation_id"])) == task_row["id"] else None


def is_comment(msg):
    """Written by `task_comment`: a say tagged `comment`. Not a question, an answer, a notice,
    a bot's reply to its run or a chat message that only mentions the task."""
    refs = msg.get("refs") or {}
    # `moved_home`: the mark a comment in a room that moved into a Home carries (bot_home_merge._mark_moved); the
    # rest are the marks its way and a bot-to-bot hold leave (threads.PRIMARY_MARKS).
    return (
        msg.get("kind") == "say"
        and bool(refs.get("comment"))
        and not (
            set(refs)
            - {
                "task",
                "comment",
                "quiet",
                "via",
                "rule",
                "wake_immediate",
                "delivery",
                "moved_home",
                "send_way",
                "send_defer_for",
                "send_people",
                "can_wait",
                "held_by",
            }
        )
    )


def _withdraw_queued_comment_copies(conn, message_id):
    """Stop unsent copies and drop local outbound text; already delivered copies remain elsewhere."""
    if _has_table(conn, 'slack_posts'):
        conn.execute(
            "UPDATE slack_posts SET text='',state=CASE WHEN state IN ('ready','retry') "
            "THEN 'canceled' ELSE state END,updated_at=? WHERE message_id=?",
            (now(), message_id),
        )


def _refresh_comment_replies(conn, msg):
    """A retry keeps its original effect, but never replays withdrawn comment words."""
    conn.execute(
        "UPDATE events SET detail=json_remove(detail,'$.old') "
        "WHERE target=? AND action IN ('message.edited','message.deleted')",
        (msg["id"],),
    )
    if not _has_table(conn, "idempotency"):
        return

    def refresh(value):
        if isinstance(value, dict):
            if value.get("id") == msg["id"] and ("sender_id" in value or "from_actor" in value):
                return {**value, **msg}  # what the route added beside the message (thread_id, warnings) stays
            return {k: refresh(v) for k, v in value.items()}
        if isinstance(value, list):
            return [
                refresh(v)
                for v in value
                if not (
                    msg.get("deleted_at")
                    and isinstance(v, dict)
                    and v.get("id") == msg["id"]
                    and ("sender_id" in v or "from_actor" in v)
                )
            ]
        return value

    # By index (backend/idempotency_messages.py): reading every stored answer here held the write lock for 35 s.
    rows = idempotency_messages.quoting(conn, msg["id"]) if _has_table(conn, "idempotency_messages") else None
    if rows is None:  # not indexed yet: an older file, or before its first start's backfill finished
        rows = conn.execute(
            "SELECT rowid,response FROM idempotency WHERE binding IS NULL AND instr(response,?)>0", (msg["id"],)
        ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE idempotency SET response=? WHERE rowid=?",
            (_dump(refresh(_json(row["response"], {}))), row["rowid"]),
        )


def _own_comment(conn, actor, task_id, message_id):
    """The live comment `message_id` on this task, when `actor` wrote it; refused otherwise."""
    _writer(conn, actor, task_id=task_id)
    row = task(conn, task_id)
    readable = globals().get("task_private_readable")
    if row and row.get("private") and (not readable or not readable(conn, actor, row)):
        refuse(conn, actor, "identity", "This task is not available to you")
    msg = comment_on(conn, row, message_id)
    if not msg or msg.get("deleted_at"):
        refuse(conn, actor, "not-found", f"no comment {message_id} on task {task_id}")
    if not is_comment(msg):
        refuse(
            conn,
            actor,
            "kind",
            "only a comment is edited or deleted, not a question, an answer, a notice or a chat message",
        )
    if msg["sender_id"] != actor:
        refuse(conn, actor, "identity", f"{actor_name(conn, msg['sender_id'])} wrote this comment; only they change it")
    _message_writer(conn, actor, msg, conversation(conn, msg["conversation_id"]))
    return msg


@private_task_write
def task_comment_edit(conn, actor, task_id, message_id, text, approval_id=None):
    """The author changes a comment's text. The new text gets the checks a new comment's text gets
    (`task_comment`, `say`); rule 7's lint is for unsolicited items, and a comment is tagged with its
    task, so it never applies. Nothing is sent and nobody wakes: whoever reads the task next reads the
    new text."""
    msg = _own_comment(conn, actor, task_id, message_id)
    text = str(text or "").strip()
    if not text:
        refuse(conn, actor, "lint", "write the comment")
    if msg.get('public') and text != msg['body']:
        from .roadmaps import check_public_comment

        check_public_comment(conn, actor, task(conn, task_id), text, approval_id)
    path_mentioned(conn, actor, "task note edit", text, task_id)
    if actor == "bot:librarian":
        text = librarian_text(text)
    if text == msg["body"]:
        return msg  # the same words are no edit: no mark, no history line
    if not msg.get("delivered_at") and len(text.encode("utf-8")) > len(msg["body"].encode("utf-8")):
        _held_queue_admit(conn, actor, msg["recipient_id"], text, msg["refs"], replacing=message_id)
    ts = now()
    conn.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (text, ts, message_id))
    _task_event(conn, task_id, actor, "comment", message_id, message_id)
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (ts, task_id))
    event(conn, actor, "message.edited", message_id, {"task": task_id})
    edited = message(conn, message_id)
    _withdraw_queued_comment_copies(conn, message_id)
    _refresh_comment_replies(conn, edited)
    return edited


@private_task_write
def task_comment_delete(conn, actor, task_id, message_id):
    """The author takes a comment back. Only its metadata stays for the audit trail, excluded from future comment reads: a run it queued and nobody started is canceled, the questions it answered
    are open again, and the bot it woke loses the delegation that came with it (`task_delegations`)."""
    _own_comment(conn, actor, task_id, message_id)
    ts = now()
    conn.execute(
        "UPDATE messages SET body='',refs=?,deleted_at=? WHERE id=?",
        (_dump({"task": task_id, "comment": True}), ts, message_id),
    )
    reopened = [r["id"] for r in _rows(conn.execute("SELECT id FROM messages WHERE answered_by_id=?", (message_id,)))]
    conn.execute("UPDATE messages SET answered_by_id=NULL WHERE answered_by_id=?", (message_id,))
    # The bots it woke beyond the first each woke through a carrier (threads.deliver), keyed by the carrier's id.
    woke = [message_id] + [
        r[0]
        for r in conn.execute(
            "SELECT carrier_id FROM message_deliveries WHERE message_id=? AND carrier_id IS NOT NULL", (message_id,)
        )
    ]
    marks = ",".join("?" * len(woke))
    conn.execute(
        f"UPDATE task_delegations SET expires_at=? WHERE message_id IN ({marks}) AND expires_at>?", (ts, *woke, ts)
    )
    canceled = (
        _has_table(conn, "jobs")
        and conn.execute(
            f"UPDATE jobs SET state='canceled' WHERE message_id IN ({marks}) AND state='queued'", tuple(woke)
        ).rowcount
        > 0
    )
    _task_event(conn, task_id, actor, "comment", message_id, None)
    conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (ts, task_id))
    row = task(conn, task_id)
    recount(conn, [row["owner_id"], row["requester_id"]])
    event(conn, actor, "message.deleted", message_id, {"task": task_id, "reopened": reopened, "canceled_run": canceled})
    deleted = message(conn, message_id, include_deleted=True)
    _withdraw_queued_comment_copies(conn, message_id)
    _refresh_comment_replies(conn, deleted)
    return deleted


PR_IN_TEXT = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", re.IGNORECASE)


def review_finding_route(conn, actor, task_id, target):
    """A bot never gives a person a General task about a pull request that an open pipeline task owns: a finding on a
    task's own pull request goes back to that task's Builder through its review (docs/pipelines-v3.md, Engineering),
    and what only a person decides there (the spec, the OK) is asked on that task. The pull request is one the task
    links, or one its title or body names by URL. Refused as `review_route`, naming the task it belongs on."""
    if not is_bot(actor) or not is_human(target):
        return
    row = task(conn, task_id)
    if not row or row.get("pipeline_id") != GENERAL_PIPELINE:
        return
    prs = {
        (str(repo).lower(), int(number))
        for repo, number in conn.execute(
            "SELECT repo, number FROM task_links WHERE task_id=? AND kind='pr' AND repo IS NOT NULL "
            "AND number IS NOT NULL",
            (task_id,),
        )
    }
    words = f"{row.get('title') or ''}\n{row.get('body') or ''}"
    prs |= {(f"{owner}/{name}".lower(), int(number)) for owner, name, number in PR_IN_TEXT.findall(words)}
    for repo, number in sorted(prs):
        own = conn.execute(
            "SELECT t.title, s.name FROM task_links l JOIN tasks t ON t.id=l.task_id JOIN stages s ON s.id=t.stage_id "
            "WHERE l.kind='pr' AND lower(l.repo)=? AND l.number=? AND t.id<>? AND t.status='open' "
            "AND t.pipeline_id IS NOT NULL AND t.pipeline_id<>? ORDER BY t.created_at LIMIT 1",
            (repo, number, task_id, GENERAL_PIPELINE),
        ).fetchone()
        if own:
            refuse(
                conn,
                actor,
                "review_route",
                f"{repo}#{number} is the pull request of the task “{own[0]}”, now in {own[1]}. A finding on it goes "
                "back to that task's Builder through its review (blocking on the Review page, or `no` on the "
                "reviewers' answer in Fixes 2), never to a person: put it there and close this task with a link. Only "
                "what the pipeline gives a person (the spec, the OK) is theirs, asked on that task.",
            )


def task_create(
    conn,
    actor,
    title,
    body,
    owner,
    due=None,
    parent_id=None,
    *,
    deduplicate=True,
    allow_planned=False,
    conversation_id=None,
    lane=None,
    labels=None,
    top=False,
    lint=True,
    goal_id=None,
    pipeline=None,
    stage=None,
    link="sub_task",
    number=None,
    mover=None,
    private=None,
    requester_actor=None,
    own_room=True,
    visibility=None,
    public_title=None,
    public_summary=None,
    wake_refs=None,
    trigger=None,
    trigger_source=None,
    from_private=False,
    routine=False,
    same=None,
    occurrence=None,
    occurrence_step=None,
    rule_due_id=None,
    public_warning="",
    follow=False,
    readers=(),
    readers_check=None,
    unassigned=False,
):
    """Rule 5. Anyone may open a task for any active owner; a human owner's General task is linted (rule 7).

    The task is made and put into the first stage of its pipeline in one step (`task_engine.enter`): `pipeline` is a
    pipeline's id, General when none is given. `owner` is who the task is given to (its assignee); the stage says who
    owns it there, which in General is the assignee. A bot that owns it is woken. `parent_id` files it under the task
    that asked for it, as a sub-task or (`link`) a handoff. Nothing is left behind when the engine refuses.

    `public_warning` is an org rule's Warn line (backend/org_rule_check.py): when the task is stored public, its
    public summary carries the line, past the summary's cap the bot's own text met.
    `conversation_id` attaches a person's task to an existing chat room. A room is not claimed as this task's private
    thread: many tasks share one room, and messages carry `refs.task`. A bot's task gets its own room whatever is
    passed; the chat room named gets a quiet "Started task" line.
    `lane` is company or product (default by the owner's team); the task joins the bottom of the owner's queue unless
    `top`.
    `number` keeps an imported task's number: a mover's (`mover`), and free.
    `routine` is an automation's own run: a Monitor or Reviewer takes it as its own work.
    `unassigned` enters it with no assignee: `owner` is still its requester and reach, but a stage owned by the task's
    assignee has nobody, and its pipeline's manager is woken to pick one (a handoff, backend/stage_effects.py).
    `occurrence` is the marker of a due-date routine's occurrence, "<automation_id>:<due_on>": each day's task is its
    own, so an open one with the same title (an overdue day's) does not count as a duplicate of it, and it is stored
    on the task (`tasks.occurrence`) for the sweeps and the app. With `occurrence_step` too, the task is one per
    occurrence per producing step: a retry of a run whose step already opened it gets that task back
    (`_reuse_occurrence`), never a second one.
    `wake_refs`, `trigger` and `trigger_source` go on the owner bot's wake: what the run it starts is for.
    `follow` makes the filer a follower of the task (backend/task_followers.py); the owner follows anyway.
    `readers` are who else sees a private task (`set_task_readers`, `readers_check(task_id, added)` its check), set
    before it enters its first stage: the engine lets a private task in only where whoever has it there reads it.
    """
    _writer(conn, actor)
    # Public text is only ever what someone wrote as public text; the internal title and body never stand in.
    public_title = str(public_title or "").strip()
    public_summary = str(public_summary or "").strip()
    if len(public_title) > 300 or len(public_summary) > 8000:
        refuse(conn, actor, 'lint', 'Public title is at most 300 characters and public summary at most 8000')
    from .shared_bots import route

    owner = route(conn, actor, owner)
    _learner(conn, actor)
    target = _reach(conn, actor, owner, allow_planned=allow_planned)
    _botops_only(conn, actor, target)
    from . import profiles

    labels = profiles.review_labels(title, parent_id, _labels(labels))
    _takes_tasks(conn, actor, target, title, parent_id, routine, labels=labels)
    _learning(conn, actor, target)
    if occurrence and occurrence_step:
        again = _one(
            conn, "SELECT id FROM tasks WHERE occurrence=? AND occurrence_step=? LIMIT 1", (occurrence, occurrence_step)
        )
        if again:
            return _reuse_occurrence(conn, actor, target, again["id"], title, wake_refs, trigger, trigger_source)
    parent = _task_parent(conn, actor, None, parent_id) if parent_id else None
    from . import private_pipelines, task_engine, visual_specs

    body = visual_specs.child_body(conn, parent_id, body)  # tickets under a spec'd roadmap card get its spec
    # Whoever files a subtask asked for it: it shows them as asking and their notices reach them.
    requester = actor
    if private is False and (not is_human(actor) or VIA.get()):
        private = None  # a bot cannot override a sensitive default to publish
    # A task filed from private work (app.task_create, task_privacy.from_private) is company-visible under its private
    # parent: its words were checked to carry nothing of the parent.
    private = not from_private and bool(
        (private if private is not None else private_tasks_default(conn, actor) or private_tasks_default(conn, target))
        or parent
        and task_private(conn, parent)
    )
    # A private pipeline's task is private whatever was asked, `from_private` too; its readers are written before it
    # enters (backend/private_pipelines.py).
    pipeline_id = str(pipeline or GENERAL_PIPELINE)
    pipe = task_engine.pipeline(conn, pipeline_id)
    in_private_pipeline = private_pipelines.is_private(pipe)
    if in_private_pipeline:
        private = True
    if private:
        requester = actor
    from .roadmaps import settings as roadmap_settings

    # Bots (and Assistant/BotOps acting for a person) publish only through an approved publication.
    if visibility == 'public' or public_title or public_summary:
        from .strategy_acts import gate

        gate(conn, actor, "publish", f"publish a new task on the public roadmap: {public_title or title}")
    bot_review = (is_bot(actor) or VIA.get()) and roadmap_settings(conn)['bot_public_approval']
    if bot_review and (visibility == 'public' or public_title or public_summary):
        # Said rather than dropped, so the bot knows its text is not on the roadmap.
        refuse(
            conn,
            actor,
            'approval',
            'A bot publishes only through an approved publication: create the task, then '
            'request one with POST /api/v2/tasks/{id}/publication-preview',
        )
    if visibility == 'public' and not public_title and not private:
        refuse(
            conn, actor, 'lint', 'Write a public title to publish this task; its internal title and body stay private'
        )
    if requester_actor is not None:
        # The keeper may attribute server-filed work to a person; a bot may attribute only its own verified work.
        if not is_human(requester_actor) or (actor != KEEPER and target != actor) or not human(conn, requester_actor):
            refuse(conn, actor, "identity", "Human requester attribution requires the bot's own verified work")
        requester = requester_actor
    title = str(title or "").strip()
    body = str(body or "")
    severity = classify(f"{title}\n{body}", to_actor=target, actor=actor, conn=conn)
    path_mentioned(conn, actor, "new task", f"{title}\n{body}".strip())
    lane = _lane_for(conn, lane, target, actor)
    if (
        not pipe
        or pipe.get("retired_at")
        or not any(s["kind"] == "work" for s in task_engine.stages(conn, pipeline_id))
    ):
        refuse(conn, actor, "task_no_such_stage", "No such pipeline")
    if parent and task_engine.depth(conn, parent["id"]) >= task_engine.MAX_DEPTH:
        refuse(conn, actor, "task_too_deep", f"Sub-tasks nest at most {task_engine.MAX_DEPTH} deep")
    general = pipeline_id == GENERAL_PIPELINE
    # Rule 7 shapes an ask to a person (a verb, the ask first). A task in another pipeline is written the way that
    # pipeline writes them, not as an ask.
    if not title:
        refuse(conn, actor, "lint", "give it a title that says what you are asking for")
    if is_bot(actor) and is_human(target) and lint and general:
        # A bot giving a person work opens the body with the ask: their card leads with it (lint_person_ask).
        require_person_ask(conn, actor, target, person_ask_of_body(body, title), "assign")
    warnings = (
        style_problems(conn, actor, lint_human_item(body, title=title), severity)
        if is_human(target) and lint and general
        else []
    )
    # A task in another pipeline keeps that pipeline's own references ("#18945", "(B/F)") in its title.
    plain = lint_title(title) if is_bot(actor) and TITLE_LINT != "off" and general else []
    if plain and TITLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(plain), severity)
    plain = warnings + plain
    labels = _labels(labels)
    if str(title).lstrip().lower().startswith("incident:") and "incident" not in labels:
        labels.insert(0, "incident")
    if labels and is_bot(actor) and not mover:
        _create_labels(conn, actor, labels)  # people, movers and the keeper as before
    # An automation's turn is the same work as another only when its wake names the same run, step and record: two
    # finds' items (or two runs) may share a title, and the title is never what makes a turn a duplicate.
    turn = _turn_identity(wake_refs)
    scope = (
        "SELECT id,title FROM tasks WHERE requester_id=? AND owner_id=? "
        f"AND coalesce({TR.parent_sql()},'')=? "
        f"AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})"
    )
    if turn:
        same_turn = " AND ".join(f"json_extract(m.refs,'$.{k}')=?" for k in AUTOMATION_TURN)
        twins = _rows(
            conn.execute(
                scope
                + f" AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=tasks.conversation_id AND {same_turn})"
                " ORDER BY created_at,id",
                (requester, target, parent_id or "", *ACTIVE_STATUSES, *(turn[k] for k in AUTOMATION_TURN)),
            )
        )
        dup = twins[0] if twins else None
    elif occurrence:
        dup = None
    else:
        dup = _same_title(
            conn,
            scope + " ORDER BY created_at,id",
            (requester, target, parent_id or "", *ACTIVE_STATUSES),
            title,
            keep=same,
        )
    if dup and task_private_readable(conn, actor, task(conn, dup["id"])) and (turn or deduplicate or actor != KEEPER):
        detail = (
            "An existing task already asks for this work"
            if VIA.get() and task_private(conn, task(conn, dup["id"]))
            else f"{dup['id']} already asks {actor_name(conn, target)} for this"
        )
        refuse(conn, actor, "duplicate", detail)
    if number is not None:
        if not (actor == KEEPER or mover or mover is None and can_move(conn, actor)):
            refuse(
                conn,
                actor,
                "identity",
                f"a task's number is given by a person on the "
                f"{', '.join(MOVER_TEAMS)} teams, not by {actor_name(conn, actor)}",
            )
        number = _number_free(conn, actor, number)
    from contextlib import ExitStack

    from . import task_trigger_rules

    # One step: if anything below is refused, the task's thread, its row and the triggers it queued go with it.
    with ExitStack() as undo:
        if conn.in_transaction:
            undo.enter_context(task_trigger_rules.savepoint(conn))
        ts = now()
        dedicated = False
        # A bot's task always gets its own room: its comments, notices and replies stay out of the chat
        # the bot's main session reads. The chat it was started from gets one "Started task" line.
        # `own_room=False` (the Assistant, whose turns act for the person of the room) keeps the old way.
        started_in = conversation(conn, conversation_id) if conversation_id and is_bot(target) and own_room else None
        if conversation_id and not private and (not is_bot(target) or not own_room):
            conv = conversation(conn, conversation_id)
            if not conv:
                refuse(conn, actor, "not-found", f"no conversation {conversation_id}")
        else:
            if conversation_id and not private and not started_in:
                refuse(conn, actor, "not-found", f"no conversation {conversation_id}")
            # The person of the chat it came from follows it into its room, where its replies reach them.
            watcher = (
                _room_person(conn, started_in, {"requester_id": requester, "owner_id": target})
                if started_in and not private
                else None
            )
            conv = open_conversation(
                conn,
                actor,
                list(dict.fromkeys([actor, requester, target, *([watcher] if is_human(watcher) else [])])),
                kind="task",
                subject=title,
                scope="task",
                allow_planned=allow_planned,
                announce=False,
            )
            dedicated = True
        row = {
            "id": new_id(),
            "title": title,
            "body": body,
            "requester_id": requester,
            "owner_id": target,
            "status": "open",
            "due_at": due,
            "conversation_id": conv["id"],
            "created_at": ts,
            "updated_at": ts,
            "done_at": None,
            "closed_at": None,
            "closed_by_id": None,
            "note": "",
            "lane": lane,
            "rank": _queue_end(conn, target, top),
            "goal_id": goal_id or None,
            "number": number,
            "private": int(private),
            "occurrence": occurrence or None,
            "occurrence_step": occurrence_step or None,
            "rule_due_id": (rule_due_id or None) if occurrence else None,
        }
        conn.execute(
            "INSERT INTO tasks (id, title, body, requester_id, owner_id, status, due_at, "
            "conversation_id, created_at, updated_at, done_at, closed_at, closed_by_id, note, lane, rank, "
            "goal_id, number, private, occurrence, occurrence_step, rule_due_id) VALUES "
            "(:id, :title, :body, :requester_id, :owner_id, :status, :due_at, "
            ":conversation_id, :created_at, :updated_at, :done_at, :closed_at, :closed_by_id, :note, "
            ":lane, :rank, :goal_id, :number, :private, :occurrence, :occurrence_step, :rule_due_id)",
            row,
        )
        _building(conn, row["id"])  # its first stage is part of task.created, not a change
        if parent:
            # Filed under its parent as it is made: _task_parent above checked the parent.
            TR._insert(conn, actor, row["id"], parent["id"], "parent", ts)
            _task_event(conn, row["id"], actor, "parent_id", None, parent["id"], "")
            _task_event(conn, parent["id"], actor, "subtask", None, row["id"], "")
        _set_task_tags(conn, actor, row["id"], labels)
        if readers:
            set_task_readers(
                conn,
                actor,
                row["id"],
                add=readers,
                mover=mover,
                check=(lambda added: readers_check(row["id"], added)) if readers_check else None,
            )
        if in_private_pipeline:
            private_pipelines.admit(conn, row["id"], pipe)
        if dedicated:
            conn.execute("UPDATE conversations SET task_id=? WHERE id=?", (row["id"], conv["id"]))
        if number is not None:
            _task_event(conn, row["id"], actor, "number", None, number, "")
        public = visibility or 'private'
        if public not in ('public', 'private'):
            refuse(conn, actor, 'kind', 'visibility is public or private')
        if private:
            public = 'private'
        if not public_title:
            # Only a task created with public text is published.
            public = 'private'
        if public == 'public' and public_warning:
            public_summary = public_warning + ("\n\n" + public_summary if public_summary else "")
        conn.execute(
            'UPDATE tasks SET visibility=?,public_title=?,public_summary=? WHERE id=?',
            (public, public_title, public_summary, row['id']),
        )
        for field, value in (('public_title', public_title), ('public_summary', public_summary)):
            if value:  # written public text leaves a record, as a later publication's does
                _task_event(conn, row['id'], actor, field, None, value, '')
        if goal_id:
            _task_event(conn, row["id"], actor, "goal_id", None, goal_id, "")
        if plain:
            # warn mode: the problem is on the record, the task still exists (the refuse week follows)
            _task_event(conn, row["id"], KEEPER, "lint", None, "; ".join(plain), "")
            event(conn, actor, "task.lint", row["id"], {"problems": plain})
        # Give a bot the rest of this relationship's active work with the new task. The runner puts current-message refs
        # in its prompt, so the bot can reuse or reconcile overlapping work without merging distinct deliverables.
        related = []
        if is_bot(target):
            marks = ",".join("?" * len(ACTIVE_STATUSES))
            related = _rows(
                conn.execute(
                    "SELECT id,title,status,owner_id,requester_id,rank,private FROM tasks WHERE id<>? "
                    f"AND status IN ({marks}) AND ((owner_id=? AND requester_id=?) OR (owner_id=? AND requester_id=?)) "
                    "ORDER BY (rank IS NULL),rank,created_at LIMIT 8",
                    (row["id"], *ACTIVE_STATUSES, target, actor, actor, target),
                )
            )
            # The wake lands in this task's conversation. A private task its readers can't read is left out: listed
            # there, it made the whole run private and its reply undeliverable. A public task's own room is read by
            # everyone who may read the task, so it lists no private task.
            readers = set(conv.get("participants") or []) | {conv.get("owner_id"), target} - {None}
            related = [
                item
                for item in related
                if all(task_private_readable(conn, reader, item) for reader in readers)
                and (private or not dedicated or not task_private(conn, item))
            ]
        reconcile = (
            {
                "with_actor": actor,
                "active": [
                    {k: item.get(k) for k in ("id", "title", "status", "owner_id", "requester_id")} for item in related
                ],
                "guidance": "Check for overlap. Reuse shared work; keep distinct decisions and deliverables separate.",
            }
            if related
            else None
        )
        # A task a bot files for itself in one of its runs starts its own run only once that run has ended
        # (execution.job_refusal): the filing run may finish it first, and the claim then cancels the wake rather than
        # start a run that finds nothing left to do.
        filed_in = RUN.get() if is_bot(target) and target == actor else ""
        wake = {
            **({"reconcile": reconcile} if reconcile else {}),
            **({"filed_in": filed_in} if filed_in else {}),
            "news": "assignment",
            **(wake_refs or {}),
        }
        # Into the first stage of its pipeline: the engine sets the stage and the owner, opens the visit and wakes a bot
        # that owns it, with `wake` on that wake.
        entered = task_engine.enter(
            conn,
            row["id"],
            pipeline_id,
            actor,
            assignee=None if unassigned else target,
            parent=parent["id"] if parent else None,
            link=link,
            wake=wake,
            trigger=trigger,
            source=trigger_source,
            start=stage,
        )
        _next_number(conn, actor, row["id"], pipeline_id)
        event(conn, actor, "task.create", row["id"], {"owner": target, "title": title})
        _building(conn, None)
        if dedicated:
            from . import threads

            threads._emit_thread_created(conn, conversation(conn, conv["id"]), actor)
        hook(
            conn,
            "task.created",
            row["id"],
            lambda: _task_facts(conn, row["id"], actor, parent_id=parent_id),
            actor,
            revision=0,
        )
        task_trigger_rules.created(conn, row["id"], actor)
        if started_in:
            _task_started(conn, task(conn, row["id"]), started_in)
        if dedicated:
            # Its own thread: the maker reads the wake their action wrote; a requester who did not make it sees it as new.
            from . import task_wakes, threads

            notice = _one(
                conn, "SELECT id FROM messages WHERE wake_key=?", (task_wakes.key(row["id"], entered["visit"]["id"]),)
            )
            threads.task_opened(conn, conv, actor, requester, notice)
        from . import task_followers

        made = task(conn, row["id"])
        if made is None:
            raise LookupError(f"task {row['id']} is not there after its insert")
        task_followers.seed_owner(conn, made)
        if follow:
            task_followers.follow_on_create(conn, made, actor)
        recount(conn, [target, actor, made.get("owner_id")])
    made = task(conn, row["id"])
    return {**made, "warnings": plain} if plain else made


def title_key(title):
    """What two task titles are compared by, for a duplicate: case-folded, trimmed, inner spaces collapsed. The
    create and rename refusals, the General Manager's duplicates and the stale review's twin all use it; the stored
    title stays as typed. In Python, since SQLite's lower() folds ASCII only."""
    return " ".join(str(title or "").split()).casefold()


def _same_title(conn, sql, args, title, keep=None):
    """The first row `sql` reads (id, title) whose title is `title` by title_key, or None. `keep(conn, task_id)`, when
    given, also has to say it is the same work: a file review asks the same words about other versions."""
    key = title_key(title)
    return next(
        (
            r
            for r in _rows(conn.execute(sql, args))
            if title_key(r["title"]) == key and (keep is None or keep(conn, r["id"]))
        ),
        None,
    )


AUTOMATION_TURN = ("automation_run", "automation_step", "automation_key")  # what names an automation's turn


def _reuse_occurrence(conn, actor, target, task_id, title, wake_refs, trigger, trigger_source):
    """The task an occurrence's step opened before (a retry whose step had already committed): returned as it is. A
    bot turn wakes its bot again for the new run, since the turn is the run's; a task that is already finished takes
    no turn, so that step is refused and the run fails rather than wait for a turn that never comes."""
    row = task(conn, task_id)
    if _turn_identity(wake_refs):
        if row["status"] not in ACTIVE_STATUSES:
            refuse(conn, actor, "duplicate", f"that occurrence's task {task_id} is already finished")
        _wake(
            conn,
            row,
            target,
            f"Again: {title}",
            wake_refs,
            trigger=trigger or "status_or_relation",
            source=trigger_source,
        )
    return row


def _turn_identity(wake_refs):
    """The run, step and record an automation turn's wake names (backend/automation_actions.py `start_turn`), or None
    for any other task. Another action of the same automation (a create_task) names its run but not its step."""
    refs = wake_refs or {}
    if any(refs.get(k) in (None, "") for k in AUTOMATION_TURN):
        return None
    return {k: str(refs[k]) for k in AUTOMATION_TURN}


def _retitle(conn, actor, row, title, owner, general):
    """A new title gets the checks a new task's title would, against the task as the update
    leaves it: never empty; on a General task for a person, rule 7's title half; no active task
    between the same requester and owner, under the same parent, already called that (task_create's
    duplicate rule: a finished one does not count); for a bot on General, the plain-English check,
    whose warnings are returned to be recorded once the title lands."""
    target = resolve_actor(conn, owner) if owner is not None else row["owner_id"]
    parent = TR.parent_of(conn, row["id"])
    if not title:
        refuse(conn, actor, "lint", "give it a title that says what you are asking for")
    warnings = style_problems(conn, actor, lint_human_title(title)) if is_human(target) and general else []
    dup = _same_title(
        conn,
        "SELECT id,title FROM tasks WHERE id<>? AND requester_id=? AND owner_id=? "
        f"AND coalesce({TR.parent_sql()},'')=? "
        f"AND status IN ({','.join('?' * len(ACTIVE_STATUSES))}) ORDER BY created_at,id",
        (row["id"], row["requester_id"], target, parent or "", *ACTIVE_STATUSES),
        title,
    )
    if dup and task_private_readable(conn, actor, task(conn, dup["id"])):
        detail = (
            "An existing task already asks for this work"
            if VIA.get() and task_private(conn, task(conn, dup["id"]))
            else f"{dup['id']} already asks {actor_name(conn, target)} for this"
        )
        refuse(conn, actor, "duplicate", detail)
    plain = lint_title(title) if is_bot(actor) and TITLE_LINT != "off" and general else []
    if plain and TITLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(plain))
    return warnings + plain


@private_task_write
def task_update(
    conn,
    actor,
    task_id,
    status=None,
    note=None,
    owner=None,
    due=None,
    body=None,
    lane=None,
    labels=None,
    rank=None,
    mover=None,
    goal_id=None,
    quiet=False,
    *,
    number=None,
    title=None,
    private=None,
    roles=None,
    visibility=None,
    public_title=None,
    public_summary=None,
    approval_id=None,
    size=None,
    clear_size=False,
    public_allowance=0,
    seen_version=None,
):
    """Edit a task's fields: title, body, due date, note, labels, lane, rank, goal, number, privacy, roles, size and
    its public text. One edit raises the task's version once. `seen_version` is the version the caller looked at: an
    edit against an older one is refused.

    This never writes the task's status, stage or owner: the engine does (backend/task_engine.py). For the callers
    that still say it this way, `status="done"` is `task_done`, `status="closed"` is `task_close`, `status="open"` on
    a task that ended reopens it, and `owner=` is `task_reassign`, each with `note` as its reason, after the field
    edits. Any other status is an error.

    `lane` and `labels` are a mover's to change (`mover` says whether this actor is one; the engine itself always
    is). `rank` is the position in the owner's queue. `goal_id` names the goal the task serves; "" takes it off.
    `number` gives a task that has none its number; it never changes. `title` renames it, checked as a new task's
    title would be (`_retitle`). `roles` replaces the people in the roles it names ({role: [actors]}; task_roles).
    """
    if status is not None and status not in ("open", "done", "closed"):
        raise ValueError(f"a task is open, done or closed, not {status!r}: move it with task_move")
    _writer(conn, actor, task_id=task_id)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    if seen_version is not None and int(seen_version) != int(row.get("version") or 0):
        refuse(conn, actor, "task_changed", "The task changed since you looked. Read it again.", task=task_id)
    if private is not None and bool(private) != task_private(conn, row):
        if not task_participant(conn, actor, row):
            refuse(conn, actor, "private", "Only the requester, assignee or their managers can change task privacy")
        if not private and (actor != row["requester_id"] or not is_human(actor) or VIA.get()):
            refuse(conn, actor, "private", "Only the human requester can publish a private task")
        parent = task(conn, TR.parent_of(conn, task_id) or "")
        if not private and parent:
            if task_private(conn, parent):
                refuse(conn, actor, "private", "Detach this task from its private parent before publishing")
        from . import private_pipelines

        if not private and private_pipelines.of_task(conn, task_id):
            refuse(conn, actor, "private", private_pipelines.STAYS_PRIVATE)
    # The same owner sent in another form (`cmo` for `bot:cmo`) is no reassignment.
    if owner is not None and resolve_actor(conn, owner) == row["owner_id"]:
        owner = None
    from . import task_calls

    mine = _task_party(conn, actor, row) or general_manager_acts(conn, actor, row)
    if actor == GENERAL_MANAGER and actor not in (row["owner_id"], row["requester_id"]):
        # Its grant is narrow (general_manager_acts): notes, a move and a reassignment, nothing else.
        beyond = [
            name
            for name, value in (
                ("lane", lane),
                ("labels", labels),
                ("rank", rank),
                ("number", number),
                ("title", title),
                ("body", body),
                ("due", due),
                ("goal", goal_id),
                ("private", private),
                ("roles", roles),
                ("visibility", visibility),
                ("public title", public_title),
                ("public summary", public_summary),
                ("size", size),
                ("approval", approval_id),
            )
            if value is not None
        ]
        if beyond or clear_size:
            refuse(
                conn,
                actor,
                "identity",
                f"The General Manager moves, notes and reassigns tasks only, not {', '.join(beyond or ['size'])}",
            )
    if mover is None:
        mover = actor == KEEPER or is_human(actor) and can_move(conn, actor)
    mover = task_mover(conn, actor, row, mover)
    labels_only = (
        labels is not None
        and not clear_size
        and all(
            v is None
            for v in (
                status,
                note,
                owner,
                due,
                body,
                lane,
                rank,
                goal_id,
                number,
                title,
                private,
                roles,
                visibility,
                public_title,
                public_summary,
                size,
            )
        )
    )
    # The bot that manages the task's pipeline may change its labels (_label_writer decides which); nothing else on
    # the task becomes its to change.
    manager = (_one(conn, "SELECT manager_id FROM pipelines WHERE id=?", (row.get("pipeline_id"),)) or {}).get(
        "manager_id"
    )
    mine = mine or labels_only and is_bot(actor) and task_private_readable(conn, actor, row) and manager == actor
    if not mine and not mover and actor != KEEPER:
        refuse(
            conn,
            actor,
            "identity",
            f"{task_id} is changed by its owner, whoever asked for it, their managers, delegates or task movers. "
            "Comment on it, or file a new task.",
        )
    # A close is a decision: a bot's late note over the close note must not change it. A requester bot may, on what
    # no person closed.
    closer = row.get("closed_by_id") or row["requester_id"]
    if (
        row["status"] == "closed"
        and is_bot(actor)
        and note is not None
        and note != row.get("note")
        and (
            not task_represents(conn, actor, row["requester_id"])
            or is_human(closer)
            and not task_represents(conn, actor, closer)
        )
    ):
        refuse(
            conn,
            actor,
            "close",
            f"{actor_name(conn, closer)} closed this task, so it stays closed. Add anything new as a comment "
            "(tico task comment).",
        )
    if number is not None and number == row.get("number"):
        number = None  # sent back unchanged
    wanted = [k for k, v in (("lane", lane), ("number", number)) if v is not None]
    if (
        not is_bot(actor)
        and labels is not None
        and not any(is_bot(row[k]) and task_represents(conn, actor, row[k]) for k in ("owner_id", "requester_id"))
    ):
        wanted.insert(1, "labels")  # a person who is not a mover changes no label, as before
    if wanted and not mover:
        refuse(
            conn,
            actor,
            "identity",
            f"{', '.join(wanted)} on a task {'is' if len(wanted) == 1 else 'are'} changed by a person on "
            f"the {', '.join(MOVER_TEAMS)} teams, not by {actor_name(conn, actor)}",
        )
    approved = labels is not None and not mover and _label_writer(conn, actor, row, labels, approval_id)
    if lane is not None:
        lane = _lane_for(conn, lane, row["owner_id"], actor)  # a task moves only onto company
    if title is not None:
        title = str(title).strip()
    if title == row["title"]:
        title = None  # sent back unchanged: nothing to check or record
    general = (row.get("pipeline_id") or GENERAL_PIPELINE) == GENERAL_PIPELINE
    plain = _retitle(conn, actor, row, title, None, general) if title is not None else []
    ts = now()
    sets, args = [], {}
    if number is not None:
        if row.get("number") is not None:
            refuse(conn, actor, "kind", f"this task is #{row['number']}; a task's number never changes")
        conn.execute("UPDATE tasks SET number=? WHERE id=?", (_number_free(conn, actor, number), task_id))
        _task_event(conn, task_id, actor, "number", None, number, note or "")
        sets.append("updated_at=:updated_at")
    if private is not None and bool(private) != task_private(conn, row):
        sets.append("private=:private")
        args["private"] = int(private)
        _task_event(conn, task_id, actor, "private", int(task_private(conn, row)), int(private), "")
        if not private and task_readers(row):
            # Everyone sees a company task: its reader list goes, so making it private again starts from its parties.
            sets.append("readers=NULL")
            for reader in task_readers(row):
                _task_event(conn, task_id, actor, "reader", reader, None, "")
    for field, value in (("title", title), ("note", note), ("due", due), ("body", body), ("lane", lane)):
        if value is None:
            continue
        if field in ("title", "body"):
            path_mentioned(conn, actor, f"task {field}", str(value), task_id)
        column = "due_at" if field == "due" else field  # the event's field keeps its name
        if value == row.get(column):
            continue
        sets.append(f"{column}=:{column}")
        args[column] = value
        _task_event(conn, task_id, actor, field, row.get(column), value, note or "")
    if labels is not None:
        _set_task_tags(conn, actor, task_id, labels, note or "", approval_id=approval_id, approved=approved)
        sets.append("updated_at=:updated_at")
    if rank is not None:
        sets.append("rank=:rank")
        args["rank"] = float(rank)
        # A queue move writes no event of its own, except on a task a Strategy Owner decision changed: its Undo keeps a
        # later move, even one back to the same place (backend/strategy_acts.py).
        if float(rank) != row.get("rank") and _strategy_changed(conn, task_id):
            _task_event(conn, task_id, actor, "rank", row.get("rank"), float(rank), note or "")
    if goal_id is not None and (goal_id or None) != row.get("goal_id"):
        sets.append("goal_id=:goal_id")
        args["goal_id"] = goal_id or None
        _task_event(conn, task_id, actor, "goal_id", row.get("goal_id"), goal_id or None, note or "")
    edited = bool(
        sets or roles or clear_size or any(v is not None for v in (visibility, public_title, public_summary, size))
    )
    after = row
    if edited:
        # A bot's result rewritten: that run's outcome.
        if title is not None or body is not None and body != row.get("body"):
            RT.turn_result(conn, actor, row, "edited", event)
        args["id"] = task_id
        args["updated_at"] = ts
        sets = [one for one in dict.fromkeys(sets) if one != "updated_at=:updated_at"]
        conn.execute(
            f"UPDATE tasks SET {', '.join([*sets, 'updated_at=:updated_at', 'version=version+1'])} WHERE id=:id", args
        )
        if args.get("private"):
            # Made private: a reconnect card's person may not read it now (backend/reconnects.py).
            from .reconnects import access_changed as reconnects_access_changed

            reconnects_access_changed(conn, task_id)
        if roles:
            # After the rest is saved, so who may be added is checked against the new privacy; the checks above
            # already said this actor may change the task.
            try:
                TRo.set_roles(conn, actor, task_id, roles, note=note or "", checked=True)
            except ValueError as exc:
                refuse(conn, actor, "kind", str(exc))
        if title is not None:
            # The task's own thread was named after it; a room many tasks share keeps its subject.
            conn.execute(
                "UPDATE conversations SET subject=? WHERE id=? AND task_id=? AND subject=?",
                (title, row["conversation_id"], task_id, row["title"]),
            )
        from .roadmaps import publication

        updated_row = task(conn, task_id)
        if task_private(conn, updated_row):
            conn.execute("UPDATE tasks SET visibility='private' WHERE id=?", (task_id,))
            updated_row = task(conn, task_id)
        publication(
            conn, actor, updated_row, visibility, public_title, public_summary, approval_id, allowance=public_allowance
        )
        if size is not None or clear_size:
            import math

            if size is not None and (not math.isfinite(size) or size < 0):
                refuse(conn, actor, 'kind', 'Size is a nonnegative finite number')
            conn.execute('UPDATE tasks SET size=?,updated_at=? WHERE id=?', (size, now(), task_id))
            _task_event(conn, task_id, actor, 'size', row.get('size'), size, '')
        if plain:
            _task_event(conn, task_id, KEEPER, "lint", None, "; ".join(plain), "")
            event(conn, actor, "task.lint", task_id, {"problems": plain})
        event(conn, actor, "task.update", task_id, {"note": note})
        after = isolate_private_task(conn, task(conn, task_id) or row)
        if private is not None or task_private(conn, after) != task_private(conn, row):
            conn.execute("DELETE FROM task_delegations WHERE task_id=?", (task_id,))
        if task_private(conn, after) and not task_private(conn, row):
            # A task under a private parent is private: every task below this one goes private with it, or a
            # colleague could read a sub-task of a parent they cannot see.
            for below in descendants(conn, task_id):
                if not task_private(conn, below):
                    _made_private(conn, actor, below["id"])
        if note is not None and note != row.get("note") and not quiet and status is None:
            _mirror_task_note(conn, actor, after, note)
        recount(conn, [after["owner_id"], after["requester_id"]])
    # The moves, for the callers that still say them as fields. Each is the engine's, checked there. The version the
    # engine is given is the task's own after the field edits above, but only where the caller gave one or may leave it
    # out: a bot that sent none is refused there, as on any move.
    versionless = is_bot(actor) and seen_version is None

    def fresh():
        return None if versionless else (task(conn, task_id) or row).get("version")

    if owner is not None:
        after = task_calls.task_reassign(conn, actor, task_id, owner, note or None, fresh(), mover=mover)
    if status is not None:
        seen = fresh()
        current = (task(conn, task_id) or row)["status"]
        if status == "done":
            after = task_calls.task_done(conn, actor, task_id, note or "", seen, mover=mover)
        elif status == "closed":
            after = task_calls.task_close(conn, actor, task_id, note or "", seen, mover=mover)
        elif current != "open":
            after = task_calls.task_move(conn, actor, task_id, task_calls.REOPEN, note or None, seen, mover=mover)
    after = task(conn, task_id) or after
    if after is None:  # refuse() above raises for a missing task; the checker cannot see it
        raise LookupError(f"no task {task_id}")
    return {**after, "warnings": plain} if plain else after


def person_name(conn, person):
    return (human(conn, person) or {}).get("name") or actor_id(person)


def actor_name(conn, actor):
    """What a person reads for an actor: a bot's display name (else its slug), or a person's name. A bot made since ids
    has a UUID id, which means nothing to a person, so it is never written into text a person reads."""
    if actor == KEEPER:
        return "Tico"  # Tico itself (hubdb_core.KEEPER): never shown as the id it is stored under
    key = actor_id(actor)
    if not str(actor).startswith("human:"):
        row = bot(conn, key)
        if row:
            return row.get("display_name") or row.get("slug") or key
    return (human(conn, key) or {}).get("name") or key


def _task_party(conn, actor, row):
    """Whether `actor` is on the task as a change to it counts (task_update): its requester, owner or their managers, a delegate it
    lent its rights to for now, or a party to a task above it."""
    if task_participant(conn, actor, row):
        return True
    if task_delegated(conn, row["id"], actor):
        return True
    return task_ancestor_party(conn, actor, row)


_DELEGATED_SQL = (
    "SELECT DISTINCT task_id FROM task_delegations WHERE delegate=? AND expires_at>? "
    "AND task_id IN (SELECT value FROM json_each(?))"
)


def task_delegated(conn, task_id, actor):
    """Whether the task lends `actor` its rights for now (task_delegations), once a snapshot (or for many tasks
    together by `prime_task_delegated`); a write reads it again (read_cache)."""
    from . import read_cache

    def build():
        return bool(conn.execute(_DELEGATED_SQL, (actor, now(), json.dumps([task_id]))).fetchone())

    return read_cache.value(conn, ("task_delegated", task_id, actor), build, shared=True)


def prime_task_delegated(conn, task_ids, actor):
    """`task_delegated` of many tasks for one actor, one statement, for the checks that follow in this snapshot."""
    from . import read_cache

    values = read_cache.cache(conn)
    if values is None:
        return
    unread = sorted({str(t) for t in task_ids if t and ("task_delegated", str(t), actor) not in values})
    if not unread:
        return
    lent = {str(r[0]) for r in conn.execute(_DELEGATED_SQL, (actor, now(), json.dumps(unread)))}
    for task_id in unread:
        values.setdefault(("task_delegated", task_id, actor), task_id in lent)


def _work_said(stopped, canceled):
    """The history line a close writes about the bot work it stopped (`stop_ended_work`), or None when none was in flight."""
    parts = []
    if stopped:
        parts.append(f"stopped {stopped} running {'turn' if stopped == 1 else 'turns'}")
    if canceled:
        parts.append(f"canceled {canceled} queued {'run' if canceled == 1 else 'runs'}")
    return ("; ".join(parts) + ".").capitalize() if parts else None


def stop_ended_work(conn, actor, task_id, status):
    """A task closed: the bot work on it stops (execution.end_task_work). Its queued runs are canceled and its
    running turns asked to stop, never the turn that makes the close. One history event on the task counts them."""
    from . import execution

    stopped, canceled = execution.end_task_work(conn, actor, task_id, status)
    said = _work_said(stopped, canceled)
    if said:
        _task_event(
            conn, task_id, actor, "work", None, said, data={"stopped": stopped, "canceled": canceled, "ended": status}
        )
    return stopped, canceled


# ----------------------------------------------------------------------------- history
# What happened to each thing, one row per change, in `history_events` (backend/migrations/0064_history_events.py):
# a task's, a goal's, the company profile's, the strategy's and the market graph's. `kind` is the field that changed
# (or what happened), `old`/`new` its values, `data` a JSON object of the type's own extras. A task's rows read the
# way its old `task_events` rows did through TASK_EVENT_COLUMNS, so its history keeps its shape in every answer.
HISTORY_SUBJECTS = (
    "task",
    "goal",
    "company",
    "strategy",
    "market_entity",
    "market_edge",
    "market_evidence",
    "market_document",
)
MARKET_SUBJECTS_SQL = "('market_entity','market_edge','market_evidence','market_document')"
TASK_EVENT_COLUMNS = (
    "id, subject_id AS task_id, created_at, actor_id AS actor, kind AS field, old, new, note, action_id, via"
)


def history_event(
    conn,
    subject_type,
    subject_id,
    actor,
    kind,
    old=None,
    new=None,
    note=None,
    data=None,
    *,
    action_id=None,
    via=None,
    created_at=None,
    event_id=None,
):
    """Append one change to `history_events`; returns its id. Values are stored as text, `data` as JSON. `event_id` is
    for a caller whose id is deterministic."""
    if subject_type not in HISTORY_SUBJECTS:
        raise ValueError(f"unknown history subject type {subject_type!r}")
    data = SRC.stamp_data(data, actor)
    event_id = event_id or new_id()
    conn.execute(
        "INSERT INTO history_events (id, subject_type, subject_id, actor_id, kind, old, new, note, action_id, "
        "via, data, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            subject_type,
            subject_id,
            actor,
            kind,
            None if old is None else str(old),
            None if new is None else str(new),
            note,
            action_id,
            via,
            None if data is None else json.dumps(data, sort_keys=True, ensure_ascii=False),
            created_at or now(),
        ),
    )
    return event_id


def _task_event(conn, task_id, actor, field, old, new, note="", *, event_id=None, data=None):
    """One row of a task's history; returns its id."""
    # A Strategy Owner decision (or its Undo) stamps each change it makes with its action id (backend/strategy_acts.py).
    from .strategy_acts import ACTION

    return history_event(
        conn,
        "task",
        task_id,
        actor,
        field,
        old,
        new,
        note,
        action_id=ACTION.get() or None,
        via=VIA.get() or None,
        data=data,
        event_id=event_id,
    )


def _strategy_changed(conn, task_id):
    """Whether a Strategy Owner decision (or its Undo) is writing this task now or has changed it before."""
    from .strategy_acts import ACTION

    return bool(ACTION.get()) or bool(
        conn.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND action_id IS NOT NULL LIMIT 1",
            (task_id,),
        ).fetchone()
    )


def _task_ref(value):
    """One task id out of a ref a bot wrote: a string, or the first string of a list.

    `refs` is a free-form dict, and bots have been sending `{"task": ["<id>"]}` for a while. Binding that list straight into SQL raised sqlite3.ProgrammingError inside
    `jobs/claim` and stalled every claim on the runner whose queue it headed.
    """
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if isinstance(v, str) and v.strip()), None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def refs_task_id(refs):
    """The task a message's refs name, as one id or None: `refs.task`, else `refs.task_id`, read by `_task_ref` (so a
    list names its first task, and anything else is no task). Callers that bind it into SQL or a reach check use this."""
    refs = refs if isinstance(refs, dict) else {}
    return _task_ref(refs.get("task")) or _task_ref(refs.get("task_id"))


def message_task_id(message, conversation=None):
    """The task a message is about: refs on the message, else a dedicated task conversation."""
    return refs_task_id((message or {}).get("refs") or {}) or _task_ref((conversation or {}).get("task_id"))


def _task_ref_sql(path):
    """The SQL twin of `_task_ref` for one `$.key` of `m.refs`: an array yields its first element. A query that reads
    the message alone reads `message_refs.ref_task_id` (migration 0051), the same value for `$.task`; this stays for
    MESSAGE_TASK_SQL, which callers splice under other aliases."""
    return (
        f"CASE WHEN json_type(m.refs,'{path}')='array' THEN json_extract(m.refs,'{path}[0]') "
        f"ELSE json_extract(m.refs,'{path}') END"
    )


def task_ref_sql(column, key="$.task"):
    """SQL predicate: the JSON in `column` names the task whose id is bound to the two `?` at `key`, either as that
    string or as an element of an array. A list names every task in it, so this matches any element, where `_task_ref`
    (Python) and `message_refs.ref_task_id` read the first. Both `?` take the same id. The string arm is the expression
    an index on json_extract(column, key) is built on; the array arm is not indexable."""
    return (
        f"(json_extract({column},'{key}')=? OR (json_type({column},'{key}')='array' AND EXISTS "
        f"(SELECT 1 FROM json_each({column},'{key}') WHERE value=?)))"
    )


# Job/status SQL: the queued message names the task; a dedicated task thread still has task_id.
# A job a wake preset holds (backend/wake_presets.py) is queued but starts no run until its batch is let go: a check
# for a run already waiting, before it writes an immediate wake, counts only the jobs that are not held.
def unheld_sql(alias="j"):
    return f"coalesce({alias}.held,0)=0"


MESSAGE_TASK_SQL = f"coalesce({_task_ref_sql('$.task')}, {_task_ref_sql('$.task_id')}, cv.task_id)"


def in_person_name_sql(alias="m"):
    """SQL (in_person_name over a row): the message `alias` is in a person's name, typed by them or written for them,
    or the keeper's carrier of one."""
    return (
        f"({alias}.sender_id LIKE 'human:%' OR ({alias}.sender_id='keeper' "
        f"AND json_extract({alias}.refs,'$.carries') IS NOT NULL AND json_extract({alias}.refs,'$.from') LIKE 'human:%'))"
    )


def typed_by_person_sql(alias="m"):
    """SQL (typed_by_person over a row): in a person's name and not delegated (refs.via empty or one of OWN_HAND_VIA)."""
    own = ",".join(f"'{v}'" for v in OWN_HAND_VIA)
    return f"({in_person_name_sql(alias)} AND coalesce(json_extract({alias}.refs,'$.via'),'') IN ('',{own}))"


def _mirror_task_note(conn, actor, task_row, note):
    """Copy a task note into the chat room a person's task lives in so people and later turns can read it.
    A bot's task has its own room instead."""
    if task_private(conn, task_row) or is_bot(task_row["owner_id"]):
        return
    text = str(note or "").strip()
    if not text:
        return
    conv = conversation(conn, task_row["conversation_id"])
    if not conv or conv.get("kind") != "chat" or conv.get("scope") not in ("personal", "shared"):
        return
    if _one(
        conn, "SELECT id FROM messages WHERE conversation_id=? AND sender_id=? AND body=?", (conv["id"], actor, text)
    ):
        return
    people = [p for p in conv.get("participants") or [] if is_human(p)]
    target = conv.get("owner_id") if conv.get("scope") == "personal" else (people[0] if people else None)
    if not target or target == task_row["owner_id"]:
        others = [p for p in (conv.get("participants") or []) if p != task_row["owner_id"]]
        target = others[0] if others else None
    if not target or target == task_row["owner_id"]:
        return
    try:
        say(conn, actor, target, text, conversation_id=conv["id"], refs={"task": task_row["id"], "note": True})
    except Refused:
        return


# ----------------------------------------------------------------------------- task rooms
# A bot's task has a room of its own (kind "task"), so its traffic stays out of the chat its main
# session reads. The main chat gets a quiet keeper line about it, `refs.task_started`, when the task is filed from
# that chat. It is written with `quiet`, so it queues no run; the main session reads it in its history the next time
# something real wakes it (execution.lane_history).


def _room_person(conn, room, row):
    """The person a main-chat line about `row` is addressed to: the room's owner, else a person in it."""
    people = [p for p in room.get("participants") or [] if is_human(p)]
    if room.get("owner_id"):
        return room["owner_id"]
    return row["requester_id"] if row.get("requester_id") in people else people[0] if people else row["owner_id"]


def _room_reads(conn, room, row):
    """Whether everyone in the room may read the task, so a line naming it says nothing new to anyone."""
    readers = set(room.get("participants") or []) | {room.get("owner_id")} - {None}
    return all(task_private_readable(conn, who, row) for who in readers)


def _task_started(conn, row, room):
    """The quiet "Started task" line in the chat a bot's task was filed from. A private task is named
    only in a room whose every reader may read it."""
    if not room or room["id"] == row["conversation_id"]:
        return None
    if task_private(conn, row) and not _room_reads(conn, room, row):
        return None
    msg = _write_message(
        conn,
        KEEPER,
        _room_person(conn, room, row),
        f"Started task: {row['title']}",
        room,
        "notice",
        {"task": row["id"], "task_started": True, "quiet": True},
        None,
        None,
    )
    event(conn, KEEPER, "task.started_in", row["id"], {"conversation": room["id"], "message": msg["id"]})
    return msg


def task_from_chat(conn, task_id):
    """Whether a bot's task came from a chat (filed there, or moved out of one): its turns' replies reach
    the person in its room as they would have in the chat, not only once it is done (execution.complete)."""
    return bool(
        _one(
            conn,
            "SELECT 1 FROM events WHERE actor=? AND action IN ('task.started_in','task.room') AND target=? LIMIT 1",
            (KEEPER, task_id),
        )
    )


def _task_room(conn, row, parties):
    """A new room of row's own (kind "task"), with `parties` in it; the task points at it."""
    room = new_id()
    conn.execute(
        "INSERT INTO conversations(id,kind,subject,participants,created_at,scope,task_id) VALUES(?,?,?,?,?,?,?)",
        (room, "task", row["title"], _dump(parties), now(), "task", row["id"]),
    )
    conn.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (room, row["id"]))
    from . import task_followers

    task_followers.seed_owner(conn, task(conn, row["id"]))  # its owner follows the new room (docs/tasks.md)
    return conversation(conn, room)


def give_task_room(conn, row):
    """A bot's live task that still lives in a chat room (filed before task rooms) moves to a room of its
    own, as execution.claim hands out a turn on it. Its earlier messages stay in the chat; the new room
    opens with a line pointing there. Returns the task as it now is."""
    if not row or not is_bot(row["owner_id"]) or row["status"] not in ACTIVE_STATUSES:
        return row
    old = conversation(conn, row["conversation_id"])
    if not old or old.get("kind") != "chat":
        return row
    person = _room_person(conn, old, row) if not task_private(conn, row) else None
    room = _task_room(conn, row, list(dict.fromkeys(p for p in (row["requester_id"], row["owner_id"], person) if p)))
    name = (bot(conn, row["owner_id"]) or {}).get("display_name") or actor_id(row["owner_id"])
    _write_message(
        conn,
        KEEPER,
        row["owner_id"],
        f"Earlier messages are in {name} chat.",
        room,
        "notice",
        {"task": row["id"], "quiet": True, "moved_from": old["id"]},
        None,
        None,
    )
    event(conn, KEEPER, "task.room", row["id"], {"from": old["id"], "to": room["id"]})
    return task(conn, row["id"])


def tasks_only(conn, actor):
    """Whether this bot is set to `bot_contact: tasks`: work arrives as a task and nothing else.

    Everything gated on this is opt-in per bot, so a fleet that has not asked for it keeps the
    behaviour `policies/handoffs.md` describes.
    """
    if not is_bot(actor) or not _has_table(conn, "bot_config"):
        return False
    row = _one(conn, BS.ONE_SQL, (actor_id(actor),))
    if not row or not row.get("config"):
        return False
    return (_json(row["config"], {}) or {}).get("bot_contact") == "tasks"


# A run in one of these states still holds the note it carried (note_waiting); a failed or interrupted one gives it back.
NOTE_CARRIER_SQL = "SELECT 1 FROM attempts WHERE id=? AND state IN ('leased','running','completed')"


def chunks[T](values: Iterable[T], size: int = 500) -> list[list[T]]:
    """`values` in slices small enough for one statement's `IN (?,...)`. Annotated: inferred, its type differed between
    basedpyright runs on the same code (task_privacy.py:1593 on Nov 7)."""
    values = list(values)
    return [values[start : start + size] for start in range(0, len(values), size)]


def parked_bots(conn):
    """Bots still waiting for their first setup (backend/statuses.py). The runner gives such a bot only a
    person's messages (execution.py claim), so work from bots and Tico stays queued until setup is done."""
    if not _has_table(conn, "bot_config"):
        return set()
    from .statuses import PARKED_SQL

    return {r[0] for r in conn.execute(f"SELECT bot FROM bot_config WHERE onboarding_state IN {PARKED_SQL}")}


def setup_hold(conn, actor, sender=None):
    """Why work sent to `actor` does not start, when it is a bot still onboarding; else None. The sender,
    Sending work to it and Run now give these words. Its manager's message (`sender`) starts it: they talk."""
    slug = actor_id(actor)
    if not is_bot(actor) or slug not in parked_bots(conn):
        return None
    if sender and is_bot(sender):
        row = _one(conn, "SELECT reports_to FROM bot_config WHERE bot=?", (slug,))
        if row and row["reports_to"] and sender == bot_actor(row["reports_to"]):
            return None
    name = (bot(conn, slug) or {}).get("display_name") or slug
    return (
        f"{name} is still onboarding, so it starts no work from bots or Tico yet, only chat with people and its "
        "manager: this waits until someone above it on the org chart marks onboarding complete."
    )


def _session_lanes(conn, row):
    """The lanes a bot's turns on `row` may have run in: its own, and its session root's (a runner that
    keys subtasks on their root). Also the root itself."""
    slug = actor_id(row["owner_id"])
    config = {}
    if _has_table(conn, "bot_config"):
        found = _one(conn, BS.ONE_SQL, (slug,))
        config = _json(found["config"], {}) if found else {}
    from .shared_bots import follow  # the followed config is the one the runner keys sessions on

    root = session_root(conn, slug, row["id"], follow(conn, slug, config))
    return ["task:" + row["id"], "task:" + root], root


def _setup_person(conn, slug):
    """The person who finishes a bot's setup: who made it, else its operator, else the default person."""
    row = (
        _one(conn, "SELECT created_by, owner_id FROM bot_config WHERE bot=?", (slug,))
        if _has_table(conn, "bot_config")
        else None
    )
    made = str((row or {}).get("created_by") or "")
    for who in (
        made if is_human(made) else "",
        human_actor((row or {}).get("owner_id") or ""),
        human_actor(default_human(conn)),
    ):
        if actor_id(who) and _actor_exists(conn, who):
            return who
    return None


def _open_setup_reminder(conn, slug):
    previous = _one(
        conn,
        "SELECT detail FROM events WHERE action='bot.setup_reminder' AND target=? ORDER BY rowid DESC LIMIT 1",
        (slug,),
    )
    found = task(conn, _json(previous["detail"], {}).get("task")) if previous else None
    return found if found and found["status"] in ACTIVE_STATUSES else None


def setup_finished(conn, slug, archived=False):
    """A bot's setup is done (onboarding.onboarded), or the bot is archived: its reminder is done too."""
    found = _open_setup_reminder(conn, slug)
    if found:
        name = (bot(conn, slug) or {}).get("display_name") or slug
        attempt(
            conn,
            task_done,
            KEEPER,
            found["id"],
            f"{name} was archived." if archived else f"{name} finished onboarding; its work starts now.",
        )


# BotOps's task to review a bot's stopped runs held for a check (execution._hand_to_maintainer).
HELD_REVIEW_TITLE = "Review {bot}'s stopped runs held for a check"
HELD_OPERATOR_REVIEW_TITLE = "Review {display_name}'s unresolved interrupted runs"
# The operator review's title carries the bot's display name, which is neither unique nor fixed, so its body names the
# bot's slug too and the stall guard reads that first.
HELD_OPERATOR_REVIEW_BOT = "Bot: {slug}."
# A keeper-filed task about one bot says which in its body by the id, the one name a rename leaves as it is: its title is
# for people and carries a slug or a display name (bot_review_tasks).
BOT_REVIEW_ID = "Bot id: {id}."
_BOT_REVIEW_ID = re.compile(r"^Bot id: (\S+)\.$", re.M)


def bot_review_tasks(conn, bot_id, owner, title):
    """The tasks `owner` holds about this bot, the ones still open first and then newest first: those whose body names
    its id, and one filed before bodies did under the title the bot has now, unless its body names another bot."""
    rows = conn.execute(
        "SELECT id,body,status FROM tasks WHERE owner_id=? AND "
        "(title=? OR (requester_id=? AND instr(body,?)>0)) "
        "ORDER BY (status='open') DESC,created_at DESC",
        (owner, title, KEEPER, BOT_REVIEW_ID.format(id=bot_id)),
    ).fetchall()
    named = ((row, _BOT_REVIEW_ID.search(str(row["body"] or ""))) for row in rows)
    return [row for row, mark in named if not mark or mark.group(1) == bot_id]


@private_task_write
def task_run_now(conn, actor, task_id):
    """Start a bot's task now: it gets a run of its own, as the task it is ("Run now" used to send a chat message
    in the person's name, so the run read as a chat and hid the task's own text). Returns (task, queued):
    queued is False when a run for this task was already waiting, so none is added."""
    _writer(conn, actor, task_id=task_id)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    if not is_bot(row["owner_id"]):
        refuse(conn, actor, "run-now", "only a bot's task runs; a person does it themselves")
    if row["status"] not in ACTIVE_STATUSES:
        refuse(conn, actor, "run-now", f"the task is {row['status']}; {task_reopen_hint(conn, actor, row)}")
    if why := setup_hold(conn, row["owner_id"]):
        raise Refused("run-now", why)  # not a refused write: nothing to count against the caller
    if (
        row.get("pipeline_id")
        and conn.execute("SELECT 1 FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_id,)).fetchone()
    ):
        # Run now asks for the work now: like the task's Wake button it ends the snooze, in the presser's name. Left
        # snoozed, the engine holds the run it queues (task_wakes.may_start) and Run now did nothing.
        from . import task_engine

        task_engine.unsnooze(conn, task_id, actor, "Run now")
    waiting = None
    try:
        waiting = _one(
            conn,
            "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.bot_id=? AND j.state='queued' AND {unheld_sql()} AND {MESSAGE_TASK_SQL}=? LIMIT 1",
            (actor_id(row["owner_id"]), task_id),
        )
    except sqlite3.OperationalError:  # a local hub has no jobs table
        waiting = None
    after = task(conn, task_id)
    if not waiting:
        _wake(
            conn,
            after,
            after["owner_id"],
            f"Run now: {after['title']}",
            {"run_now": True},
            trigger="person_message" if is_human(actor) else "other",
        )
    event(conn, actor, "task.run-now", task_id, {"queued": not waiting})
    return after, not waiting


# ----------------------------------------------------------------------------- quiet notes
# A quiet note is something for a bot to know, not to do. It starts nothing; the next run the bot has for any reason
# carries every note waiting for it, in the same prompt, each with the time it was sent. For example, monitor bots leave a
# manager bot a note after each check, and its daily report reads them.
NOTE_MAX = 8000
NOTE_WAITING_SQL = (
    "n.canceled_at IS NULL AND n.settled_at IS NULL AND (n.carried_by IS NULL OR NOT EXISTS("
    "SELECT 1 FROM attempts a WHERE a.id=n.carried_by AND a.state IN ('leased','running','completed')))"
)


def note_create(conn, actor, to, body):
    """Leave a note for a bot's next run. A person has no runs, so only a bot receives one. A paused or
    quarantined bot's note waits for its next run when a person or its manager bot leaves it (held_for)."""
    _writer(conn, actor)
    target = _reach(conn, actor, to, allow_held=held_for(conn, actor, to))
    if not is_bot(target):
        refuse(conn, actor, "note", "a note waits for a bot's next run; a person has none, so tell them another way")
    if target == actor:
        refuse(conn, actor, "note", "a note to yourself belongs in your own repository")
    body = str(body or "").strip()
    if not body:
        refuse(conn, actor, "note", "the note is empty")
    if len(body) > NOTE_MAX:
        refuse(conn, actor, "note", f"a note is at most {NOTE_MAX} characters; put the detail in a file or a task")
    path_mentioned(conn, actor, "note", body)
    row = {"id": new_id(), "from_actor": actor, "to_actor": target, "body": body, "created_at": now()}
    conn.execute(
        "INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES "
        "(:id,:from_actor,:to_actor,:body,:created_at)",
        row,
    )
    event(conn, actor, "note.create", row["id"], {"to": target})
    return note(conn, row["id"])


def note(conn, note_id):
    return _one(conn, "SELECT * FROM notes WHERE id=?", (note_id,))


def note_waiting(conn, row):
    """Whether this note (a row) is still waiting for its bot's next run."""
    if not row or row.get("canceled_at") or row.get("settled_at"):
        return False
    if not row.get("carried_by"):
        return True
    try:
        return not _one(conn, NOTE_CARRIER_SQL, (row["carried_by"],))
    except sqlite3.OperationalError:  # a local hub has no attempts table and no runs
        return True


def note_cancel(conn, actor, note_id, human=False):
    """Take a note back before a run carries it: its sender, or a person."""
    _writer(conn, actor)
    row = note(conn, note_id)
    if not row:
        refuse(conn, actor, "not-found", f"no note {note_id}")
    if actor != row["from_actor"] and not human and not is_human(actor):
        refuse(
            conn,
            actor,
            "note",
            f"{actor_name(conn, row['from_actor'])} left this note; only they or a person take it back",
        )
    if row.get("message_id"):
        msg = message(conn, row["message_id"])
        if msg:
            _message_writer(conn, actor, msg, conversation(conn, msg["conversation_id"]))
    if row["canceled_at"]:
        return row
    if not note_waiting(conn, row):
        refuse(conn, actor, "note", "a run already carried this note; it cannot be taken back")
    conn.execute("UPDATE notes SET canceled_at=?, canceled_by=? WHERE id=?", (now(), actor, note_id))
    event(conn, actor, "note.cancel", note_id, {})
    return note(conn, note_id)


def notes_waiting(conn, bot, limit=50, *, unheld=False):
    """The notes waiting for `bot` (a slug), oldest first after Tico's own: the one a lifted quarantine leaves
    (quarantine_lifted_note) must reach the next run even behind a full backlog. A note that stands for a held
    message (`message_id`) is the message, in the order messages arrived, whoever sent it, the keeper too.
    `unheld` selects notes deliverable now; the full pending list and its counts keep quarantined sources."""
    from . import task_quarantine

    available = " AND " + task_quarantine.notes_free_sql() if unheld else ""
    return _rows(
        conn.execute(
            f"SELECT n.* FROM notes n WHERE n.to_actor=? AND {NOTE_WAITING_SQL}{available} "
            f"ORDER BY NOT (n.from_actor=? AND n.message_id IS NULL),n.created_at,n.id LIMIT ?",
            ("bot:" + bot, KEEPER, limit),
        )
    )


def notes_settle(conn, attempt_id):
    """Mark the notes a completed run carried as read for good (`settled_at`), where its result is recorded."""
    conn.execute("UPDATE notes SET settled_at=? WHERE carried_by=? AND settled_at IS NULL", (now(), attempt_id))


def notes_settle_catch_up(conn, limit=500):
    """Settle up to `limit` notes whose carrier completed but no path marked them (None settles them all: the
    migration's one pass). Reads the small unsettled-carried index, so it is cheap on every scheduler tick, and
    a note it leaves for the next pass stays correct, since waiting is still decided by the carrier. Returns the count."""
    if not _has_table(conn, "attempts"):
        return 0
    stamp = now()
    return conn.execute(
        "UPDATE notes SET settled_at=? WHERE id IN (SELECT n.id FROM notes n JOIN attempts a ON a.id=n.carried_by "
        "WHERE n.carried_by IS NOT NULL AND n.settled_at IS NULL AND a.state='completed' LIMIT ?)",
        (stamp, -1 if limit is None else limit),
    ).rowcount


def notes_waiting_counts(conn, bots, limit=500):
    """`len(notes_waiting(conn, bot, limit))` for many bots in one query per 500: {slug: count}."""
    out = dict.fromkeys(bots, 0)
    for part in chunks(out):
        for row in conn.execute(
            f"SELECT n.to_actor,count(*) FROM notes n WHERE n.to_actor IN "
            f"({','.join('?' * len(part))}) AND {NOTE_WAITING_SQL} GROUP BY n.to_actor",
            ["bot:" + bot for bot in part],
        ):
            out[actor_id(row[0])] = min(row[1], limit)
    return out


def _wake(
    conn,
    task_row,
    target,
    body,
    refs=None,
    quiet_bots=False,
    quiet=False,
    trigger="status_or_relation",
    source=None,
):
    """Tell someone their task moved. Task traffic never counts against rule 4.

    `trigger` is what this wake is, for the run it starts (backend/run_triggers.py): a task's status or
    relation moved unless the caller says otherwise; `source` names the event behind it where there is one.

    `quiet_bots` is for news rather than work: a task finished or closed. A bot that asked for
    `tasks` gets it quietly — written into its room, no run — and reads it in the conversation
    history the next time something real wakes it. Waking a bot to tell it that something it
    asked for is done costs a whole run to learn a fact it can act on later or not at all.

    Any other bot is told exactly as before: this is opt-in, and a fleet using the handoff flow
    in `policies/handoffs.md` still gets the wake that flow waits on.

    `quiet` is stronger: the caller knows this notice asks nothing of any bot, so no bot is
    woken for it, whatever it opted into.
    """
    if quiet_bots and (quiet or tasks_only(conn, target)):
        refs = {**(refs or {}), "quiet": True}
    if (
        is_bot(target)
        and target == task_row.get("owner_id")
        and not (refs or {}).get("run_now")
        and (refs or {}).get("wake") != "snooze"
        and conn.execute("SELECT 1 FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_row["id"],)).fetchone()
    ):
        # Nobody plans to get to a snoozed task before its date: news about it waits for its bot's next run.
        # Anyone else (a requester bot handed it for review) hears as usual.
        refs = {**(refs or {}), "quiet": True}
    if not target or not _actor_exists(conn, target) or not task_private_readable(conn, target, task_row):
        return None
    if is_bot(target) and target == task_row.get("owner_id") and not (refs or {}).get("quiet"):
        # A reconnect wake still owed on this task rides on this one (backend/reconnects.py): one wake, not two.
        from .reconnects import fold_into as reconnects_fold

        body, refs = reconnects_fold(conn, task_row, body, refs)
    message_refs = RT.stamp({"task": task_row["id"], **(refs or {})}, trigger, source)
    return _write_message(
        conn, KEEPER, target, body, conversation(conn, task_row["conversation_id"]), "notice", message_refs, None, None
    )


# ----------------------------------------------------------------------------- status
def status_set(conn, actor, bot_slug, state=None, focus=None, task_id=None, reason=""):
    """One active row per bot; the old one moves to `bot_status_history` with `until`.

    `state="active"` is how a human clears a quarantine (deviation 4): it sets `bots.state`
    and leaves the live status `idle`.
    """
    _writer(conn, actor, task_id=task_id)
    slug = actor_id(bot_slug) if actor_kind(bot_slug) else str(bot_slug).strip()
    if not bot(conn, slug):
        refuse(conn, actor, "not-found", f"no bot {slug}")
    lifting = actor == bot_actor(FLEET_MAINTAINER) and state == "active"  # BotOps ending a quarantine
    if is_bot(actor) and actor_id(actor) != slug and not lifting:
        refuse(conn, actor, "identity", f"{actor_name(conn, actor)} may only set its own status")
    if state is not None and state not in STATUS_STATES and state != "active":
        refuse(conn, actor, "kind", f"a state is {'|'.join(STATUS_STATES)}, not {state}")
    quarantined = (bot(conn, slug) or {}).get("state") == "quarantined"
    if quarantined and state == "active" and not is_human(actor):
        # A quarantine for too many refusals lifts without a person (the cooldown,
        # or BotOps); only an escape (secrets, another bot's repo, reaching outside) waits for one.
        lifter = actor in (KEEPER, bot_actor(FLEET_MAINTAINER)) and actor != bot_actor(slug)
        if not lifter or quarantine_is_escape(conn, slug):
            refuse(conn, actor, "quarantined", f"only a person takes {slug} out of this quarantine")
    if quarantined and state == "active":
        event(conn, actor, "quarantine.lifted", bot_actor(slug), {"reason": reason})
        quarantine_lifted_note(conn, slug)
    if state == "active" and (quarantined or is_human(actor) or lifting and actor != bot_actor(slug)):
        from . import task_quarantine

        task_quarantine.release(conn, actor, bot=slug)
    if quarantined and state not in ("active", "quarantined"):
        # Lease expiry and completion are routine keeper updates. Neither can clear or mask
        # the human review required by quarantine.
        state = "quarantined"
        focus = None
    ts = now()
    old = _one(conn, "SELECT * FROM bot_status WHERE bot=?", (slug,))
    _writer(conn, actor, task_id=task_id if task_id is not None else (old or {}).get("task_id"))
    if old:
        conn.execute(
            "INSERT INTO bot_status_history (id, bot, state, focus, task_id, started_at, ended_at, "
            "reason, by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), slug, old["state"], old["focus"], old["task_id"], old["changed_at"], ts, reason, actor),
        )
    live = state
    if state == "active":
        conn.execute("UPDATE bots SET state='active' WHERE id=?", (slug,))
        live = "idle"
    elif state in ("paused", "quarantined"):
        conn.execute("UPDATE bots SET state=? WHERE id=?", (state, slug))
    row = {
        "bot": slug,
        "state": live if live is not None else (old or {}).get("state") or "idle",
        "focus": focus if focus is not None else (old or {}).get("focus") or "",
        "task_id": task_id if task_id is not None else (old or {}).get("task_id"),
        "changed_at": ts if (not old or live not in (None, old["state"])) else old["changed_at"],
        "last_turn_at": (old or {}).get("last_turn_at"),
        "last_result": (old or {}).get("last_result"),
        "next_due_at": (old or {}).get("next_due_at"),
        "updated_at": ts,
    }
    conn.execute(
        "INSERT INTO bot_status (bot, state, focus, task_id, changed_at, last_turn_at, last_result, "
        "next_due_at, open_tasks, needs_human, updated_at) VALUES (:bot, :state, :focus, :task_id, "
        ":changed_at, :last_turn_at, :last_result, :next_due_at, 0, 0, :updated_at) "
        "ON CONFLICT(bot) DO UPDATE SET state=:state, focus=:focus, task_id=:task_id, changed_at=:changed_at, "
        "updated_at=:updated_at",
        row,
    )
    event(conn, actor, "status.set", slug, {"state": live, "focus": focus, "reason": reason})
    _recount(conn, bot_actor(slug))
    return status(conn, slug)


def status_result(conn, actor, bot_slug, last_result=None, last_turn_at=None, next_due=None):
    """The keeper's side of a status row: what came out of the last turn, and when the next is."""
    _writer(conn, actor)
    slug = actor_id(bot_slug) if actor_kind(bot_slug) else str(bot_slug).strip()
    if not status(conn, slug):
        status_set(conn, actor, slug, state="idle")
    for field, value in (("last_result", last_result), ("last_turn_at", last_turn_at), ("next_due_at", next_due)):
        if value is not None:
            conn.execute(f"UPDATE bot_status SET {field}=?, updated_at=? WHERE bot=?", (value, now(), slug))
    if last_turn_at:
        conn.execute("UPDATE bots SET last_turn_at=? WHERE id=?", (last_turn_at, slug))
    return status(conn, slug)


def status_counts(conn, actor):
    """`(open_tasks, needs_human)` for a bot, as they are now.

    `needs_human` is one per thing of the bot's that waits on a person: an open task it asked a person for, and an
    ask it put on a task (an OK or a question naming a person) that is still unanswered."""
    live = ACTIVE_STATUSES
    marks = ",".join("?" * len(live))
    open_tasks = conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE owner_id=? AND status IN ({marks})", (actor, *live)
    ).fetchone()[0]
    waits = conn.execute(
        f"SELECT COUNT(*) FROM tasks t WHERE t.status IN ({marks}) AND t.requester_id=? AND t.owner_id LIKE 'human:%'",
        (*live, actor),
    ).fetchone()[0]
    asks = conn.execute(
        "SELECT COUNT(*) FROM requirements r JOIN visits v ON v.id = r.visit_id AND v.left_at IS NULL "
        "JOIN tasks t ON t.id = v.task_id AND t.status = 'open' "
        "WHERE r.added_by_id=? AND r.withdrawn_at IS NULL AND r.person_id LIKE 'human:%' AND NOT EXISTS "
        "(SELECT 1 FROM answers a WHERE a.requirement_id = r.id AND a.visit_id = v.id AND a.undone_at IS NULL "
        "AND a.answer NOT IN ('give', 'again'))",
        (actor,),
    ).fetchone()[0]
    return open_tasks, waits + asks


def _recount(conn, actor):
    """`open_tasks` and `needs_human` on a bot's status row, after anything that moves a task."""
    if not is_bot(actor):
        return
    slug = actor_id(actor)
    if not _one(conn, "SELECT bot FROM bot_status WHERE bot=?", (slug,)):
        return
    open_tasks, waiting = status_counts(conn, actor)
    conn.execute(
        "UPDATE bot_status SET open_tasks=?, needs_human=?, updated_at=? WHERE bot=?",
        (open_tasks, waiting, now(), slug),
    )


def recount(conn, actors):
    """`_recount` for each bot among `actors` (task deletes and restores)."""
    for actor in dict.fromkeys(a for a in actors if a):
        _recount(conn, actor)


# ----------------------------------------------------------------------------- schedules, turns, limits
def schedule_sync(conn, actor, entries):
    """The hosted bots' crons, from their `bot.yaml`. Replaces the rows for the bots named."""
    _writer(conn, actor)
    # A schedule names its bot by id; a computer may still send the slug.
    entries = [
        {**e, "bot": (resolve_bot(conn, str(e["bot"])) or {}).get("id", actor_id(str(e["bot"])))}
        for e in (entries or [])
        if (e or {}).get("bot")
    ]
    touched = sorted({str(e["bot"]) for e in entries})
    for slug in touched:
        conn.execute("DELETE FROM schedules WHERE bot=?", (slug,))
    out = []
    for e in entries:
        row = {
            "id": new_id(),
            "bot": str(e["bot"]),
            "cron": str(e.get("cron") or ""),
            "title": str(e.get("title") or ""),
            "playbook": str(e.get("playbook") or ""),
            "last_fired_at": e.get("last_fired_at"),
            "next_due_at": e.get("next_due_at"),
        }
        conn.execute(
            "INSERT INTO schedules (id, bot, cron, title, playbook, last_fired_at, next_due_at) "
            "VALUES (:id, :bot, :cron, :title, :playbook, :last_fired_at, :next_due_at)",
            row,
        )
        out.append(row)
    event(conn, actor, "schedule.sync", ",".join(touched), {"count": len(out)})
    return out


def schedule_fired(conn, actor, schedule_id, next_due=None):
    _writer(conn, actor)
    conn.execute(
        "UPDATE schedules SET last_fired_at=?, next_due_at=COALESCE(?, next_due_at) WHERE id=?",
        (now(), next_due, schedule_id),
    )
    event(conn, actor, "schedule.fired", schedule_id)
    return _one(conn, "SELECT * FROM schedules WHERE id=?", (schedule_id,))


def schedules(conn, bot_slug=None):
    if bot_slug:
        found = resolve_bot(conn, bot_slug)
        return _rows(
            conn.execute(
                "SELECT * FROM schedules WHERE bot=? ORDER BY title", ((found or {}).get("id") or actor_id(bot_slug),)
            )
        )
    return _rows(conn.execute("SELECT * FROM schedules ORDER BY bot, title"))


def rate_limit_set(conn, actor, runtime, used_percent=None, window_minutes=None, resets_at=None):
    """What a daemon reports about its account's window."""
    _writer(conn, actor)
    row = {
        "runtime": str(runtime),
        "used_percent": used_percent,
        "window_minutes": window_minutes,
        "resets_at": resets_at,
        "updated_at": now(),
    }
    conn.execute(
        "INSERT INTO rate_limits (runtime, used_percent, window_minutes, resets_at, updated_at) "
        "VALUES (:runtime, :used_percent, :window_minutes, :resets_at, :updated_at) "
        "ON CONFLICT(runtime) DO UPDATE SET "
        "used_percent=COALESCE(:used_percent, used_percent), "
        "window_minutes=COALESCE(:window_minutes, window_minutes), "
        "resets_at=COALESCE(:resets_at, resets_at), updated_at=:updated_at",
        row,
    )
    event(conn, actor, "rate_limit.set", runtime, {"used_percent": used_percent})
    return _one(conn, "SELECT * FROM rate_limits WHERE runtime=?", (str(runtime),))


def rate_limits(conn):
    return _rows(conn.execute("SELECT * FROM rate_limits ORDER BY runtime"))


# A run's record as it reads in answers (`/api/turns`, H.turns, turn_start/turn_finish): its attempts row under the
# names a run record had before it moved onto `attempts` (migration 0072). `job_id IS NULL OR trigger IS NOT NULL`
# marks a run that has a record: the claim writes `trigger` with the lease, and a run recorded with no lease has no job.
# An attempt with a job and no trigger failed before its record was written (old history) and has none.
TURN_SELECT = (
    "id, bot_id AS bot, thread_id, created_at AS started_at, settled_at AS finished_at, trigger, "
    "message_id, task_id, exit, summary, input_tokens, cached_tokens, cache_write_tokens, output_tokens, "
    "usage_model AS model, "
    "provider, est_cost_usd, billing, trigger_kind, trigger_source, writes, outcome, did_nothing, purpose, "
    "rotated, session_kind, session_why, prompt_tokens, usage_reason, rule_id"
)
LEASE = ("job_id", "runner_id", "generation", "token_hash", "state", "lease_until", "lane")


def turn(conn, turn_id):
    """One run's record (TURN_SELECT), or None."""
    return _one(
        conn, f"SELECT {TURN_SELECT} FROM attempts WHERE id=? AND (job_id IS NULL OR trigger IS NOT NULL)", (turn_id,)
    )


def turn_start(
    conn,
    actor,
    bot_slug,
    thread_id=None,
    trigger="message",
    message_id=None,
    task_id=None,
    trigger_kind=None,
    trigger_source=None,
    purpose=None,
    rule_id=None,
    lease=None,
):
    """A run on a bot's thread: its attempts row, with what woke it. Replaces the legacy dispatcher's `runs.jsonl`
    record. `trigger_kind` is what woke it (backend/run_triggers.py TRIGGERS) and `trigger_source` the event that did.
    `purpose` is what the run is for; the caller names it, or the bot and trigger map in run_triggers does.
    `rule_id` is the wake rule that started it (backend/rules.py). `lease` is the claim's lease (LEASE: job_id,
    runner_id, generation, token_hash, state, lease_until, lane), written in the same row; without it (the demo seed)
    the run has no lease."""
    _writer(conn, actor)
    slug = actor_id(bot_slug) if actor_kind(bot_slug) else str(bot_slug).strip()
    if is_bot(actor) and actor_id(actor) != slug:
        refuse(conn, actor, "identity", f"{actor_id(actor)} may only record its own turns")
    row = {
        "id": new_id(),
        "bot_id": slug,
        "thread_id": thread_id,
        "created_at": now(),
        # A run record is told from a bare lease by its trigger, so it always has one.
        "trigger": trigger or "message",
        "message_id": message_id,
        "task_id": task_id,
        "trigger_kind": trigger_kind or "other",
        "trigger_source": trigger_source,
        "purpose": RT.clean_purpose(purpose) or RT.purpose_for(slug, trigger_kind or "other"),
        "rule_id": (str(rule_id)[:80] if rule_id else None),
        "writes": 0,
        **{k: v for k, v in (lease or {}).items() if k in LEASE},
    }
    conn.execute(f"INSERT INTO attempts ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})", row)
    event(
        conn,
        actor,
        "turn.start",
        row["id"],
        {"bot": slug, "trigger": trigger, "trigger_kind": row["trigger_kind"], "purpose": row["purpose"]},
    )
    return turn(conn, row["id"])


# Why a finished run's usage says 0 tokens or no cost (`attempts.usage_reason`). Usage is written on every finish, so a
# sum over input_tokens, cached_tokens, cache_write_tokens, output_tokens and est_cost_usd counts every run, failed and
# stopped ones too. cache_write_tokens is NULL on a run whose runner did not count cache writes apart (an older runner):
# its input_tokens include them.
USAGE_REASONS = {
    "usage_unreported": "its runner sent a result with no token counts (the harness reported none)",
    "usage_limited": "the runtime refused the turn on a usage limit and reported no tokens",
    "no_result": "its runner never sent a result (the lease ran out, or a restore ended it)",
    "cost_unpriced": "tokens were counted, but its model has no list price, so est_cost_usd is null",
}
NO_USAGE = {"input_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0}


def turn_finish(conn, actor, turn_id, exit_code="ok", summary=None, usage=None, usage_reason="usage_unreported"):
    """Close a turn. `exit_code` is the `exit` column (`exit` is a builtin, not a keyword here).
    `usage` is what the run cost in tokens: input_tokens (uncached, without cache writes), cached_tokens,
    cache_write_tokens (None when its runner did not count them apart), output_tokens, model, provider,
    est_cost_usd and billing (see USAGE_SCHEMA), and prompt_tokens, its newest request's prompt when known.
    Without `usage` the run is recorded at 0 tokens and $0 with `usage_reason` (USAGE_REASONS) saying why; never
    left blank."""
    _writer(conn, actor)
    ts = now()
    conn.execute("UPDATE attempts SET settled_at=?, exit=?, summary=? WHERE id=?", (ts, exit_code, summary, turn_id))
    # Whether it acted, from what it wrote and its task's state as it ends (backend/run_triggers.py).
    conn.execute(f"UPDATE attempts SET {RT.SETTLE_SQL} WHERE id=?", (turn_id,))
    measured = usage or None
    if usage:
        reason = "cost_unpriced" if usage.get("est_cost_usd") is None else None
    else:
        reason, usage = usage_reason, NO_USAGE
    # Once a run's usage is kept as facts the first report stands for its own row too: a late result (or a lease
    # that ran out after the result) leaves the columns the facts were taken from as they are.
    recorded = conn.execute(
        "SELECT 1 FROM usage_facts WHERE run_id=? AND source='estimate' LIMIT 1", (turn_id,)
    ).fetchone()
    if not recorded:
        conn.execute(
            "UPDATE attempts SET input_tokens=:input_tokens, cached_tokens=:cached_tokens, "
            "cache_write_tokens=:cache_write_tokens, output_tokens=:output_tokens, usage_model=:model, provider=:provider, est_cost_usd=:est_cost_usd, "
            "billing=:billing, prompt_tokens=:prompt_tokens, usage_reason=:usage_reason WHERE id=:id",
            {
                "input_tokens": None,
                "cached_tokens": None,
                "cache_write_tokens": None,
                "output_tokens": None,
                "model": None,
                "provider": None,
                "est_cost_usd": None,
                "billing": None,
                "prompt_tokens": None,
                **usage,
                "usage_reason": reason,
                "id": turn_id,
            },
        )
    row = turn(conn, turn_id)
    if measured and not recorded:
        # One append-only fact per segment it measured (backend/usage_facts.py); every usage reader sums those.
        from . import usage_facts

        usage_facts.record_run(conn, row, measured, ts)
    if row:
        conn.execute("UPDATE bots SET last_turn_at=? WHERE id=?", (ts, row["bot"]))
        conn.execute("UPDATE bot_status SET last_turn_at=?, updated_at=? WHERE bot=?", (ts, ts, row["bot"]))
    from . import run_diagnostics

    run_diagnostics.finish(conn, turn_id, exit_code, summary)
    event(conn, actor, "turn.finish", turn_id, {"exit": exit_code, "est_cost_usd": usage.get("est_cost_usd")})
    return row


def delta_append(conn, actor, turn_id, text, kind="text"):
    """Streamed text or thought while a run goes. Pruned after an hour by `prune_deltas`."""
    seq = (
        conn.execute("SELECT COALESCE(MAX(seq), 0) FROM deltas WHERE attempt_id=?", (turn_id,)).fetchone()[0] or 0
    ) + 1
    row = {
        "id": new_id(),
        "attempt_id": turn_id,
        "seq": seq,
        "kind": kind,
        "text": str(text or ""),
        "created_at": now(),
    }
    conn.execute(
        "INSERT INTO deltas (id, attempt_id, seq, kind, text, created_at) "
        "VALUES (:id, :attempt_id, :seq, :kind, :text, :created_at)",
        row,
    )
    return row


def deltas(conn, turn_id, since_seq=0):
    return _rows(conn.execute("SELECT * FROM deltas WHERE attempt_id=? AND seq>? ORDER BY seq", (turn_id, since_seq)))


def prune_deltas(conn, before=None):
    """Deltas are the only rows that go: they are a stream, not a record (rule 9)."""
    cutoff = before or shift(now(), hours=-1)
    n = conn.execute("DELETE FROM deltas WHERE created_at < ?", (cutoff,)).rowcount
    return n


# ----------------------------------------------------------------------------- reads
def bot(conn, bot_id):
    """The bot with this id: an actor's id, or a foreign key's value. Never a slug: a person's or a client's input
    goes through resolve_bot, which accepts either."""
    return _one(conn, "SELECT * FROM bots WHERE id=?", (actor_id(bot_id),))


def resolve_bot(conn, ref):
    """The bot a person or a client named, by id or by slug (a route, a field, a command argument), or None.
    No slug can name another bot's id (bots_identity_insert), so at most one row can match."""
    value = str(ref or "").strip()
    if value.startswith("bot:"):
        value = value[4:]
    if not value:
        return None
    return _one(conn, "SELECT * FROM bots WHERE id=? OR slug=?", (value, value))


def rename_bot(conn, bot_id, new_slug):
    """Give a bot a new slug. Only the label changes: its id, and so every actor string, reference and history row
    that names it, stays as it was. A slug may not name another bot's id (bots_identity_slug)."""
    slug = str(new_slug or "").strip()
    row = bot(conn, bot_id)
    if row is None:
        raise ValueError(f"no bot {bot_id!r}")
    if not slug:
        raise ValueError("a bot needs a slug")
    conn.execute("UPDATE bots SET slug=? WHERE id=?", (slug, row["id"]))
    return bot(conn, row["id"])


def bots(conn, state=None):
    sql, args = "SELECT * FROM bots", []
    where = []
    if state:
        where.append("state=?")
        args.append(state)
    if where:
        sql += " WHERE " + " AND ".join(where)
    from .read_cache import rows

    return [dict(row) for row in rows(conn, sql + " ORDER BY slug", args)]


# The Tico account a person signed in with (backend/oidc.py `person`): matched by the sign-in only, never sent out.
HUMAN_ACCOUNT_COLUMNS = ("idp_issuer", "idp_sub")


def human(conn, pid):
    row = _one(conn, "SELECT * FROM humans WHERE id=?", (actor_id(pid),))
    if row:
        row["teams"] = _json(row.get("teams"), []) or []
        for key in HUMAN_ACCOUNT_COLUMNS:
            row.pop(key, None)
    return row


def humans(conn):
    out = _rows(conn.execute("SELECT * FROM humans ORDER BY id"))
    for row in out:
        row["teams"] = _json(row.get("teams"), []) or []
        for key in HUMAN_ACCOUNT_COLUMNS:
            row.pop(key, None)
    return out


CONVERSATION_SQL = "SELECT * FROM conversations WHERE id=?"


def conversation(conn, conversation_id):
    row = _one(conn, CONVERSATION_SQL, (conversation_id,))
    if row:
        row["participants"] = _json(row.get("participants"), []) or []
    return row


def prime_conversations(conn, ids):
    """Read these conversations in one query per 500 for the conversation() lookups that follow in this snapshot."""
    from .read_cache import cache, prime

    values = cache(conn)
    wanted = {cid for cid in ids if cid}
    if values is not None:
        wanted = {cid for cid in wanted if ("one", CONVERSATION_SQL, (cid,)) not in values}
    for part in chunks(sorted(wanted)):
        found = {
            row["id"]: row
            for row in conn.execute("SELECT * FROM conversations WHERE id IN (%s)" % ",".join("?" * len(part)), part)
        }
        prime(conn, CONVERSATION_SQL, {(cid,): found.get(cid) for cid in part})


def conversations_for(conn, actor, limit=100):
    """Every conversation this actor is a participant in, newest first."""
    # Filtered in SQL: a window of the newest conversations company-wide lost an older chat behind many newer ones.
    out = []
    for row in _rows(
        conn.execute(
            "SELECT * FROM conversations c WHERE EXISTS (SELECT 1 FROM "
            "json_each(CASE WHEN json_valid(c.participants) THEN c.participants END) WHERE value=?) ORDER BY "
            "COALESCE(c.last_message_at, c.created_at) DESC LIMIT ?",
            (actor, limit),
        )
    ):
        row["participants"] = _json(row.get("participants"), []) or []
        out.append(row)
    return out


def chats_with(conn, actor, bot_actor_id):
    """The actor's open chat with this bot, or its readable Home after the bot's chats move, newest first.
    Found directly, not from the newest conversations: a bot's page opened a month after the last
    message still finds its chat (conversations_for scans a recent window only)."""
    out = []
    for row in _rows(
        conn.execute(
            "SELECT * FROM conversations WHERE kind='chat' AND task_id IS NULL "
            "AND closed_at IS NULL AND participants LIKE ? ORDER BY "
            "COALESCE(last_message_at, created_at) DESC",
            (f'%"{bot_actor_id}"%',),
        )
    ):
        row["participants"] = _json(row.get("participants"), []) or []
        if actor in row["participants"] and bot_actor_id in row["participants"]:
            out.append(row)
    from . import bot_home_merge

    home_id = bot_home_merge.home_for_chat(conn, actor, bot_actor_id, create=False)
    if home_id:
        home = _one(conn, "SELECT * FROM conversations WHERE id=?", (home_id,))
        if home and all(row["id"] != home_id for row in out):
            home["participants"] = _json(home.get("participants"), []) or []
            out.append(home)
            out.sort(
                key=lambda row: (row.get("last_message_at") or row.get("created_at") or "", row["id"]), reverse=True
            )
    return out


MESSAGE_SQL = "SELECT * FROM messages WHERE id=?"


def message(conn, message_id, *, include_deleted=False):
    row = _one(conn, MESSAGE_SQL + ("" if include_deleted else " AND deleted_at IS NULL"), (message_id,))
    return message_view(conn, row)


def prime_messages(conn, ids):
    """Read these messages in one query per 500 for the message() lookups that follow in this snapshot, deleted or not.
    Returns {id: stored row} of those found (empty outside a snapshot, where nothing is kept)."""
    from .read_cache import cache

    values = cache(conn)
    if values is None:
        return {}
    wanted = {mid for mid in ids if mid}
    known = {mid for mid in wanted if ("one", MESSAGE_SQL, (mid,)) in values}
    out = {
        mid: values[("one", MESSAGE_SQL, (mid,))] for mid in known if values[("one", MESSAGE_SQL, (mid,))] is not None
    }
    for part in chunks(sorted(wanted - known)):
        found = {
            row["id"]: row
            for row in conn.execute("SELECT * FROM messages WHERE id IN (%s)" % ",".join("?" * len(part)), part)
        }
        _prime_message_lookups(conn, part, found)
        out.update(found)
    return out


def _prime_message_lookups(conn, ids, found):
    """Seed both message() lookups for `ids` from `found`, {id: stored row}: a row missing from it is no message."""
    from .read_cache import prime

    prime(conn, MESSAGE_SQL, {(mid,): found.get(mid) for mid in ids})
    live = {mid: row for mid, row in found.items() if row["deleted_at"] is None}
    prime(conn, MESSAGE_SQL + " AND deleted_at IS NULL", {(mid,): live.get(mid) for mid in ids})


MESSAGE_PARENT_DEPTH = 100  # task_privacy and live_privacy refuse a provenance walk of more messages than this


def prime_message_rows(conn, rows):
    """Seed message() with stored rows already read whole in this snapshot (a join's `m.*`), then read the messages
    they reply to or answer, and their question replies, a level at a time in one query per 500 each, so the walks
    (task_privacy.message_tasks, live_privacy) read none of them one by one. Returns every row seeded or read."""
    from .read_cache import cache

    values = cache(conn)
    if values is None:
        return []
    rows = {row["id"]: row for row in rows if row and row["id"]}
    _prime_message_lookups(conn, list(rows), rows)
    level, seen = list(rows.values()), set(rows)
    for _ in range(MESSAGE_PARENT_DEPTH):
        parents = set()
        answers = {row["id"]: row for row in prime_review_answers(conn, level)}
        _prime_message_lookups(conn, list(answers), answers)
        parents.update(answers)
        for row in level:
            refs = _json(row["refs"], {}) or {}
            parents.add(row["in_reply_to"])
            for key in ("answers", "inputs"):
                if isinstance(refs, dict) and isinstance(refs.get(key), list):
                    parents.update(v for v in refs[key] if isinstance(v, str))
        wanted = {mid for mid in parents if mid and mid not in seen}
        if not wanted:
            break
        seen |= wanted
        level = list(prime_messages(conn, wanted).values())
        rows.update((row["id"], row) for row in level)
    return list(rows.values())


def message_view(conn, row):
    """Decorate a complete stored row without reading the same message again."""
    if row:
        row = dict(row)
        row["refs"] = _json(row.get("refs"), {}) or {}
        if row["kind"] == "ask" and row["refs"].get("questions"):
            row["ask"] = {k: row["refs"].get(k) for k in ("questions", "who")}
            row["answers"] = review_answers(conn, row["id"])
        if row["refs"].get("answer"):
            row["answer"] = row["refs"]["answer"]
    return row


def messages(conn, conversation_id, since=None, limit=200):
    sql = "SELECT * FROM messages WHERE conversation_id=? AND deleted_at IS NULL"
    args = [conversation_id]
    if since:
        sql += " AND created_at > ?"
        args.append(since)
    out = _rows(conn.execute(sql + " ORDER BY created_at LIMIT ?", (*args, limit)))
    for row in out:
        row["refs"] = _json(row.get("refs"), {}) or {}
    return out


def undelivered(conn, to_actor=None, limit=200):
    """What the keeper's mailbox loop reads: messages no runtime has accepted yet. For one recipient, its pending
    deliveries (message_deliveries: the messages addressed to it and those that reach it otherwise)."""
    if to_actor:
        out = _rows(
            conn.execute(
                "SELECT m.* FROM message_deliveries d JOIN messages m ON m.id=d.message_id WHERE d.recipient=? "
                "AND d.state='pending' AND m.deleted_at IS NULL ORDER BY d.created_at LIMIT ?",
                (to_actor, limit),
            )
        )
        for row in out:
            row["refs"] = _json(row.get("refs"), {}) or {}
        return out
    sql = "SELECT * FROM messages WHERE delivered_at IS NULL AND deleted_at IS NULL"
    args = []
    out = _rows(conn.execute(sql + " ORDER BY created_at LIMIT ?", (*args, limit)))
    for row in out:
        row["refs"] = _json(row.get("refs"), {}) or {}
    return out


# A message still waiting for its recipient: not yet delivered, or not yet read. The partial index repeats these terms
# word for word, or SQLite does not use it (`inbox_pages`).
UNREAD_TERMS = "(delivered_at IS NULL OR read_at IS NULL) AND deleted_at IS NULL"
UNREAD_INDEX = (
    "CREATE INDEX IF NOT EXISTS messages_unread_to ON messages(recipient_id, created_at) WHERE " + UNREAD_TERMS
)
# The inbox reads deliveries (backend/threads.py): a message addressed to the reader, or reaching them another way (a
# mention, a DM), still waiting. The addressee's row follows the message's delivered_at/read_at. The terms repeat the
# partial index message_deliveries_waiting word for word, or SQLite does not use it.
WAITING_TERMS = "d.state IN ('pending','delivered','capped','held')"
# Paged on the delivery's own (created_at, rowid), which the waiting index holds in order: no sort.
UNREAD_PAGE_SQL = (
    "SELECT d.rowid AS _seq, d.created_at AS _at, m.* FROM message_deliveries d "
    "JOIN messages m ON m.id=d.message_id WHERE d.recipient=? AND " + WAITING_TERMS + " "
    "AND " + delivery_counts_sql("m", "d.recipient", "d.reason") + " AND m.deleted_at IS NULL "
    "AND (m.expires_at IS NULL OR m.expires_at > ?) AND d.created_at<=? "
    "AND (d.created_at<? OR d.rowid<?) ORDER BY d.created_at DESC, d.rowid DESC LIMIT ?"
)


def inbox_pages(conn, actor, at=None, size=100):
    """`actor`'s unread or undelivered messages, newest first, `size` at a time, each once. A bot that never marks
    messages read holds thousands, and a reader that wants only the newest few stops paging: the rest are not read.
    Each page carries on below the last (created, rowid) of the one before, so messages sent in the same instant are
    neither repeated nor skipped. Read the pages in one snapshot."""
    at = at or now()
    created, seq = "\uffff", 1 << 62  # above every stored time and rowid: the first page is the newest
    while True:
        rows = _rows(conn.execute(UNREAD_PAGE_SQL, (actor, at, created, created, seq, size)))
        if not rows:
            return
        created, seq = rows[-1]["_at"], rows[-1]["_seq"]
        for row in rows:
            del row["_seq"], row["_at"]
            row["refs"] = _json(row.get("refs"), {}) or {}
        yield rows
        if len(rows) < size:
            return


def mark_answered(conn, rows):
    """Set each message's `answered_at`, kept on the inbox shape: always None, since nothing asks in a message now."""
    for row in rows:
        row["answered_at"] = None
    return rows


def inbox(conn, actor, at=None):
    """What is waiting for me: unread or undelivered messages, oldest first, and my open tasks."""
    rows = [row for page in inbox_pages(conn, actor, at, size=500) for row in page][::-1]
    return {
        "actor": actor,
        "messages": mark_answered(conn, rows),
        "tasks": tasks(conn, owner=actor, status=ACTIVE_STATUSES),
    }


def notices(conn, actor, at=None):
    """The inbox tab: unexpired notices, unread first."""
    at = at or now()
    rows = _rows(
        conn.execute(
            "SELECT * FROM messages WHERE recipient_id=? AND kind='notice' "
            "AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY (read_at IS NOT NULL), created_at DESC",
            (actor, at),
        )
    )
    for row in rows:
        row["refs"] = _json(row.get("refs"), {}) or {}
    return rows


TASK_SQL = f"SELECT * FROM {TASKS} tasks WHERE id=?"


def prime_tasks(conn, ids, with_tags=True):
    """Read these tasks and their tags in one query each per 500, for the task() lookups that follow in this snapshot
    (a list that reads each task, its ancestors and its relations one at a time). `with_tags=False`: the rows only."""
    from .read_cache import cache, prime, prime_rows

    values = cache(conn)
    if values is None:
        return
    wanted = {tid for tid in ids if tid}
    # A row already read in this snapshot is not read again; only its missing tags are.
    unread = sorted(tid for tid in wanted if ("one", TASK_SQL, (tid,)) not in values)
    for part in chunks(unread):
        marks = ",".join("?" * len(part))
        found = {row["id"]: row for row in conn.execute(f"SELECT * FROM {TASKS} tasks WHERE id IN ({marks})", part)}
        prime(conn, TASK_SQL, {(tid,): found.get(tid) for tid in part})
    for part in chunks(sorted(tid for tid in wanted if with_tags and (TASK_TAGS_SQL % "?", (tid,)) not in values)):
        marks = ",".join("?" * len(part))
        tags = {tid: [] for tid in part}
        for row in conn.execute(TASK_TAGS_SQL % marks, part):
            tags[row["task_id"]].append(row)
        prime_rows(conn, TASK_TAGS_SQL % "?", {(tid,): found for tid, found in tags.items()})


def task(conn, task_id):
    row = _one(conn, TASK_SQL, (task_id,))
    if row:
        hydrate_task_tags(conn, [row])
    return row


def task_untagged(conn, task_id):
    """The task row without its tags: for a check that reads none of them (who may read it), so a page of checks
    primed by prime_tasks(with_tags=False) reads no tags one task at a time."""
    return _one(conn, TASK_SQL, (task_id,))


def task_labels(row):
    return [tag["key"] for tag in (row or {}).get("tags", [])]


def labels_in_use(conn, visible="1"):
    """Every label on a task that is still open, most used first. `visible` limits it to
    the tasks the caller may read (`Auth.task_sql`)."""
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    return [
        r[0]
        for r in conn.execute(
            f"SELECT tags.key, COUNT(*) n FROM (SELECT id FROM tasks WHERE status IN ({marks}) "
            f"AND ({visible})) visible_tasks JOIN task_tags ON task_tags.task_id=visible_tasks.id "
            "JOIN tags ON tags.id=task_tags.tag_id "
            "GROUP BY tags.key ORDER BY n DESC, tags.key",
            ACTIVE_STATUSES,
        )
    ]


# A keyset page starts after the last row's sort key: queue order (rank-less last, rank, created, id) or finished order
# (most recently finished first, id). Row values compare left to right as the ORDER BY sorts. created_at is a nullable
# column: a missing date is '' here, which sorts where NULL does (before every date ascending, after every date
# descending), so a row with none is still reached and its key still round-trips as a string.
KEYSET_SQL = {
    "queue": "((rank IS NULL), COALESCE(rank, 0), COALESCE(created_at, ''), id) > (?, ?, ?, ?)",
    "finished": "(COALESCE(done_at,closed_at,updated_at,created_at,''), id) < (?, ?)",
}


def task_sort_key(row, order="queue"):
    """The sort key `tasks(after=...)` takes, of one raw task row, as a list that survives JSON."""
    if order == "queue":
        return [int(row.get("rank") is None), row.get("rank") or 0, row.get("created_at") or "", row["id"]]
    when = next((row[k] for k in ("done_at", "closed_at", "updated_at", "created_at") if row.get(k) is not None), "")
    return [when, row["id"]]


def tasks(
    conn,
    owner=None,
    requester=None,
    status=None,
    limit: int | None = 500,
    lane=None,
    label=None,
    offset=0,
    order="queue",
    visible=None,
    number=None,
    updated_since=None,
    member=None,
    role=None,
    ids=None,
    snoozed=None,
    q=None,
    exclude_owner=None,
    match=(),
    after=None,
    keys_only=False,
):
    """Tasks, newest work first. `visible` is a WHERE fragment over the task's own columns (from
    `Auth.task_sql`), so a caller's page and its `offset` are cut in the query. `updated_since` is a stored timestamp.
    `member` keeps the tasks that actor is on
    in some role (task_roles), or in `role` when given. `ids` limits it to those tasks (a delta
    read's changed ones). `snoozed` is "hide" (leave out snoozed tasks), "only", or None for both.
    `exclude_owner` leaves out one owner's tasks. `limit=None` reads every match. `match` is more WHERE fragments
    over the task's own columns, each with its arguments (backend/task_match.py, the Tasks page's filters). `after` is
    a keyset start: the sort key of the last row a page gave (`task_sort_key`), so the page holds the rows after it.
    `keys_only` answers every match's (id, title, number) in order instead, for a filter worked out in Python."""
    sql, args, where = "SELECT * FROM tasks", [], []
    for fragment, values in match:
        where.append("(" + fragment + ")")
        args += list(values)
    if q and q.strip():
        query = q.strip()[:1000]
        if re.fullmatch(r"#\d+", query):
            where.append("number=?")
            args.append(int(query[1:]) if len(query) <= 10 else -1)
        else:
            for word in query.split():
                where.append("instr(lower(title),lower(?))>0")
                args.append(word)
    if member:
        where.append(TRo.member_sql(role))
        args += [member] + ([role] if role else [])
    if visible and visible != "1":
        where.append("(" + visible + ")")
    if owner:
        where.append("owner_id=?")
        args.append(owner)
    if exclude_owner:
        where.append("owner_id IS NOT ?")  # NULL-safe: a task with no owner is not that owner's either
        args.append(exclude_owner)
    if requester:
        where.append("requester_id=?")
        args.append(requester)
    if lane:
        where.append("lane=?")
        args.append(lane)
    if number is not None:
        where.append("number=?")
        args.append(number)
    if updated_since:
        where.append("updated_at>?")
        args.append(updated_since)
    if ids is not None:
        where.append("id IN (SELECT value FROM json_each(?))")
        args.append(json.dumps(list(ids)))
    if snoozed in ("hide", "only"):
        where.append(("NOT " if snoozed == "hide" else "") + SNOOZED % "tasks")
    if label:
        where.append(
            "EXISTS (SELECT 1 FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
            "WHERE task_tags.task_id=tasks.id AND tags.key=?)"
        )
        args.append(str(label).strip().lower())
    if status:
        wanted = [status] if isinstance(status, str) else list(status)
        # The finished list's hot path uses a matching partial index (tasks_finished_lane_time). The values are fixed
        # constants, while every other caller remains parameterized.
        if tuple(wanted) == ("done", "closed"):
            where.append("status IN ('done','closed')")
        else:
            where.append(f"status IN ({','.join('?' * len(wanted))})")
            args += wanted
    if order == "queue":
        ordering = "(rank IS NULL), rank, created_at, id"
    elif order == "finished":
        ordering = "COALESCE(done_at,closed_at,updated_at,created_at) DESC, id DESC"  # most recently done first
    else:
        raise ValueError("task order is queue or finished")
    if after is not None:
        # The same tuple the ORDER BY sorts on; a rank-less row's rank is never compared (its first part decides).
        where.append(KEYSET_SQL[order])
        args += list(after)
    if where:
        sql += " WHERE " + " AND ".join(where)
    if keys_only:
        return conn.execute(
            "SELECT id, title, number" + sql[len("SELECT *") :] + f" ORDER BY {ordering}", args
        ).fetchall()
    page = (*args, -1 if limit is None else max(1, int(limit)), max(0, int(offset)))
    if order == "queue" and limit is not None:
        # The page's ids first, then its full rows. Sorting `SELECT *` copied every visible task's body into the
        # sorter (26 MB of bodies at TIDY's 10.8k tasks) on each list page; tasks_queue_cover answers the ids alone.
        ids = [
            row[0]
            for row in conn.execute(
                "SELECT id" + sql[len("SELECT *") :] + f" ORDER BY {ordering} LIMIT ? OFFSET ?", page
            )
        ]
        found = {}
        for part in chunks(ids):
            found.update(
                (row["id"], row)
                for row in _rows(
                    conn.execute(f"SELECT * FROM {TASKS} tasks WHERE id IN ({','.join('?' * len(part))})", part)
                )
            )
        rows = [found[ident] for ident in ids if ident in found]
    else:
        rows = _rows(
            conn.execute(
                f"SELECT * FROM {TASKS} tasks"
                + sql[len("SELECT * FROM tasks") :]
                + f" ORDER BY {ordering} LIMIT ? OFFSET ?",
                page,
            )
        )
    hydrate_task_tags(conn, rows)
    return rows


def task_flow(
    conn,
    since,
    until=None,
    tz_minutes=0,
    visible="1",
    owner=None,
    requester=None,
    owner_kind=None,
    label=None,
    recurring=None,
):
    """How tasks moved through their statuses between `since` and `until`, read from the task history.

    Every status change is already an event, so nothing extra is stored. `days` counts the tasks
    that entered each status on each local day ("created" is a new task, "open" a reopened one). `stages` is how
    long tasks sat in a status they left in the window, and `people` is the same count by the
    task's current owner. `visible` is the caller's WHERE fragment over task columns
    (`Auth.task_sql`); the other filters also apply to the task as it is now.
    """
    where, args = ["(" + (visible or "1") + ")", "lane='company'"], []
    if owner:
        where.append("owner_id=?")
        args.append(owner)
    if requester:
        where.append("requester_id=?")
        args.append(requester)
    if owner_kind in ("bot", "human"):
        where.append("owner_id LIKE ?")
        args.append(owner_kind + ":%")
    if label:
        where.append(
            "EXISTS (SELECT 1 FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
            "WHERE task_tags.task_id=tasks.id AND tags.key=?)"
        )
        args.append(str(label).strip().lower())
    if recurring is not None:
        if _has_table(conn, "schedule_occurrences"):
            where.append(
                ("" if recurring else "NOT ") + "EXISTS (SELECT 1 FROM schedule_occurrences o WHERE o.task_id=tasks.id)"
            )
        elif recurring:
            where.append("0")
    picked = "picked AS (SELECT id, owner_id FROM tasks WHERE " + " AND ".join(where) + ")"
    fields = "e.subject_type='task' AND e.kind='status'"
    window = f"{fields} AND e.created_at>=?" + (" AND e.created_at<?" if until else "")
    window_args = [since] + ([until] if until else [])
    stage = "CASE WHEN e.old IS NULL THEN 'created' ELSE e.new END"
    days, people = {}, {}
    for day, owner_actor, name, n in conn.execute(
        f"WITH {picked} SELECT date(e.created_at, ?) AS day, t.owner_id, {stage} AS stage, count(*) FROM history_events e "
        f"JOIN picked t ON t.id=e.subject_id WHERE {window} GROUP BY day, t.owner_id, stage",
        (*args, f"{int(tz_minutes):+d} minutes", *window_args),
    ):
        days.setdefault(day, {})
        days[day][name] = days[day].get(name, 0) + n
        people.setdefault(owner_actor, {})
        people[owner_actor][name] = people[owner_actor].get(name, 0) + n
    # Time in a status: from entering it to the next change, for statuses left in the window.
    spans = {}
    for name, hours in conn.execute(
        f"WITH {picked}, s AS (SELECT e.kind, e.new, e.created_at, LEAD(e.created_at) OVER "
        "(PARTITION BY e.subject_id, e.kind ORDER BY e.created_at, e.id) AS left_at FROM history_events e "
        f"WHERE {fields} AND e.subject_id IN (SELECT e.subject_id FROM history_events e JOIN picked t "
        f"ON t.id=e.subject_id WHERE {window})) SELECT new, "
        "(julianday(left_at)-julianday(created_at))*24 FROM s WHERE new IS NOT NULL AND left_at>=?"
        + (" AND left_at<?" if until else ""),
        (*args, *window_args, *window_args),
    ):
        spans.setdefault(name, []).append(hours)

    def summary(values):
        values = sorted(values)
        pick = lambda q: round(values[min(len(values) - 1, int(q * len(values)))], 2)
        return {"n": len(values), "median_hours": pick(0.5), "p90_hours": pick(0.9)}

    totals = {}
    for counts in days.values():
        for name, n in counts.items():
            totals[name] = totals.get(name, 0) + n
    return {
        "days": [{"day": day, **days[day]} for day in sorted(days)],
        "totals": totals,
        "stages": {name: summary(values) for name, values in spans.items()},
        "people": sorted(
            ({"actor": actor, **counts} for actor, counts in people.items()),
            key=lambda row: (-sum(v for k, v in row.items() if k != "actor"), row["actor"]),
        ),
    }


def task_history(conn, task_id, *, provenance=False):
    columns = TASK_EVENT_COLUMNS + (", data" if provenance else "")
    return _rows(
        conn.execute(
            f"SELECT {columns} FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY created_at, rowid",
            (task_id,),
        )
    )


def status(conn, bot_slug):
    """A bot's status row as stored. Its counts are recounted on every change that moves them
    (`_recount`); `status_live` counts them afresh for a display."""
    return _one(conn, "SELECT * FROM bot_status WHERE bot=?", (actor_id(bot_slug),))


def status_live(conn, bot_slug):
    """`status`, with `open_tasks` and `needs_human` counted now: for what a person is shown."""
    row = status(conn, bot_slug)
    if row:
        row["open_tasks"], row["needs_human"] = status_counts(conn, bot_actor(row["bot"]))
    return row


def status_all(conn):
    return _rows(conn.execute("SELECT * FROM bot_status ORDER BY bot"))


def status_history(conn, bot_slug, since=None, limit=200):
    sql = "SELECT * FROM bot_status_history WHERE bot=?"
    args = [actor_id(bot_slug)]
    if since:
        sql += " AND started_at >= ?"
        args.append(since)
    return _rows(conn.execute(sql + " ORDER BY started_at DESC LIMIT ?", (*args, limit)))


def turns(conn, bot_slug=None, since=None, limit=200):
    """Runs' records (TURN_SELECT), newest first."""
    sql, args, where = f"SELECT {TURN_SELECT} FROM attempts", [], ["(job_id IS NULL OR trigger IS NOT NULL)"]
    if bot_slug:
        where.append("bot_id=?")
        args.append(actor_id(bot_slug))
    if since:
        where.append("created_at >= ?")
        args.append(since)
    sql += " WHERE " + " AND ".join(where)
    return _rows(conn.execute(sql + " ORDER BY created_at DESC LIMIT ?", (*args, limit)))


def refusals_for(conn, actor=None, day=None):
    sql, args, where = "SELECT * FROM refusals", [], []
    if actor:
        where.append("actor=?")
        args.append(actor)
    if day:
        where.append("substr(created_at,1,10)=?")
        args.append(day)
    if where:
        sql += " WHERE " + " AND ".join(where)
    out = _rows(conn.execute(sql + " ORDER BY created_at", args))
    for row in out:
        row["detail"] = (_json(row.get("detail"), {}) or {}).get("detail", "")
    return out


def events(conn, since=None, actor=None, limit=200):
    sql, args, where = "SELECT * FROM events", [], []
    if since:
        where.append("created_at >= ?")
        args.append(since)
    if actor:
        where.append("actor=?")
        args.append(actor)
    if where:
        sql += " WHERE " + " AND ".join(where)
    out = _rows(conn.execute(sql + " ORDER BY created_at DESC LIMIT ?", (*args, limit)))
    for row in out:
        row["detail"] = _json(row.get("detail"), None)
    return out


TASK_ORIGIN_SQL = (
    "SELECT detail FROM events WHERE actor=? AND action='task.origin' AND target=? ORDER BY created_at DESC LIMIT 1"
)


def prime_task_origins(conn, ids):
    """Read the origin events of these tasks in one query per 500, for the task_origin() calls that follow."""
    from .read_cache import cache, prime

    if cache(conn) is None:
        return
    for part in chunks(sorted({tid for tid in ids if tid})):
        found = {}
        for row in conn.execute(
            "SELECT target,detail FROM events WHERE actor=? AND action='task.origin' "
            "AND target IN (%s) ORDER BY created_at DESC" % ",".join("?" * len(part)),
            (KEEPER, *part),
        ):
            found.setdefault(row["target"], {"detail": row["detail"]})
        prime(conn, TASK_ORIGIN_SQL, {(KEEPER, tid): found.get(tid) for tid in part})


def task_origin(conn, row):
    """Trusted origin for a keeper review, including pre-metadata records; never parse titles."""
    explicit = _one(conn, TASK_ORIGIN_SQL, (KEEPER, row["id"]))
    if explicit:
        origin = (_json(explicit["detail"], {}) or {}).get("bot")
        return origin if is_bot(origin) else None
    if row.get("requester_id") != KEEPER or row.get("owner_id") != human_actor(default_human(conn)):
        return None
    # Legacy _escalate wrote no foreign key. Require its exact canonical body and a
    # unique sensitive refusal immediately preceding creation. The actor comes from
    # the trusted refusal record, not a bot name supplied in the title/body.
    origins, candidates = set(), set()
    for refusal in _rows(
        conn.execute(
            "SELECT actor,detail FROM refusals WHERE severity IN ('sensitive','escape') "
            "AND created_at>=? AND created_at<=?",
            (shift(row["created_at"], seconds=-5), row["created_at"]),
        )
    ):
        actor = refusal["actor"]
        if not is_bot(actor):
            continue
        candidates.add(actor)
        detail = (_json(refusal["detail"], {}) or {}).get("detail", "")
        body = (
            f"{actor_name(conn, actor)} tried a write that touches something you care about and it was "
            f"turned down: {_clip(detail)}. Tell me whether that is a mistake to fix or a "
            f"limit to keep."
        )
        if row.get("body") == body:
            origins.add(actor)
    return next(iter(origins)) if len(origins) == 1 and len(candidates) == 1 else None


# ----------------------------------------------------------------------------- the task calls
# Done, move, close, reassign, asks, answers and snooze (backend/task_calls.py), under the names every caller uses:
# H.task_move(...). Imported last: task_calls reads hubdb when a call runs, not when it loads.
from . import task_calls as _task_calls
from .task_calls import (
    attempt as attempt,
    task_answer as task_answer,
    task_answer_undo as task_answer_undo,
    task_ask as task_ask,
    task_close as task_close,
    task_done as task_done,
    task_give as task_give,
    task_move as task_move,
    task_page_attach as task_page_attach,
    task_reassign as task_reassign,
    task_release as task_release,
    task_requeue as task_requeue,
    task_snooze as task_snooze,
    task_thumbs as task_thumbs,
    task_try_again as task_try_again,
    task_unsnooze as task_unsnooze,
    task_view as task_view,
    task_waits as task_waits,
    task_withdraw_ask as task_withdraw_ask,
)

TASK_NEXT, TASK_DONE, TASK_CLOSED, TASK_REOPEN = (
    _task_calls.NEXT,
    _task_calls.DONE,
    _task_calls.CLOSED,
    _task_calls.REOPEN,
)
task_can_work = _task_calls.can_work
