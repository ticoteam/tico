#!/usr/bin/env python3
"""hub.db: the schema, the write layer and its rules, and the read helpers.

The contract is `docs/history/hub-v2.md` §0-§5. This module is the whole database layer: the cloud
service (`backend/store.py` and everything behind it) goes through it and nowhere else. Pure
stdlib, one file. It grew up under the local keeper; the `host` column's `dispatcher` default and
the token helpers are that history, kept because rows and callers still carry them.

Shape of every write:

    f(conn, actor, ...) -> row dict          # `actor` first after the connection
    raises Refused(rule, detail, severity)   # also written to `refusals`, never stored

`actor` is `"bot:<slug>"`, `"human:<id>"` or `"keeper"`. Every write appends an `events` row.
Nothing is edited or deleted (rule 9): tasks and status carry their own history tables. A plain task
comment's author may change or take it back: the row stays marked, without retaining
withdrawn text in the audit trail.

    conn = connect()                         # <projects>/runtime/hub.db, or $HUB_DB
    sync_registry(conn, employees, people)   # bots and humans from the registry
    token = issue_token(conn, "cmo")         # the keeper hands this to the turn
    verify_bot(conn, "cmo", token)           # hubdb checks before any bot write

## Deviations from docs/history/hub-v2.md (also listed in that file's "Deviations" section)

1. **`conn` first.** §4 writes the signature as `say(actor, ...)`. There is no ambient
   connection, so every function is `say(conn, actor, ...)`; `actor` is still the first
   argument the caller thinks about, and `issue_token(conn, slug)` in §1 already puts `conn`
   first. Read helpers are `tasks(conn, owner=...)` and so on.
2. **`verify_bot` raises.** It returns the bot row and raises `Refused("identity")` on a bad
   or missing token, so a caller cannot forget to check a boolean.
3. **Lint (rule 7) applies to unsolicited human items.** A bot's `say`/`notice` to a human is
   linted only when rule 4 counts it as unsolicited; a reply to the human's own message,
   a reply inside a conversation the human started, and `answer`, are exempt. Every task with
   a human owner and every approval is linted. Without this carve-out a chat reply longer
   than 120 words would be refused, which
   would break S1; the handoffs rule the lint comes from is about *asking* a person for
   something, not about answering them.
4. **Approval lint is the payload's fields.** An approval has no title or body, so rule 7 on
   an approval means "the payload is the exact thing": `send` needs to/cc/subject/
   body_sha256/mailbox, `spend` amount/account/what, `publish` url/content_sha256, `merge`
   repo/pr. A missing field is `Refused("lint", "send needs cc")`. A `publish` payload's
   content_sha256 must be 64 lower-case hex characters, and its optional `text` must be a string
   whose SHA-256 (of its UTF-8, as lower-case hex) is exactly content_sha256, or it is refused.
5. **`status_set(..., state="active")`** is accepted although `active` is not a `bot_status`
   state: it is how §4 rule 8 says a human clears a quarantine. It sets `bots.state=active`
   and `bot_status.state=idle`.
6. **Rule names.** §4 names `reach`, `cap`, `depth`, `unsolicited`, `one-question`,
   `duplicate`, `consumed`, `lint`. The rest are `identity` (acting as someone else, deciding
   an approval as a bot), `kind` (a bad approval kind or status), `close` (closing a task you
   did not request), `quarantined` (a quarantined bot's writes) and `not-found`.
7. **Severity is classified here, not passed in.** `classify()` reads the body or payload and
   returns `sensitive` (money, an outbound send, access, a human's inbox) or `normal`. Words
   that name a `secrets/` path or another bot's folder are written as they are and recorded
   (`path_mentioned`); they are never refused or counted. Refusals remain in the audit; repeated
   failures open an internal review. Generic refusal diagnostics do not create human decision tasks.
8. **`sync_registry` never lowers a state.** A bot the registry calls `active` that hubdb has
   `quarantined` stays quarantined; only a human clears it. Tokens, thread ids and
   `last_turn_at` are never overwritten by a sync.
9. **Extra read helpers.** `answers_to(conn, ids)` (what `hub question ask --wait` polls),
   `bot(conn, slug)`, `human(conn, id)`, `task(conn, id)`, `task_history(conn, id)`,
   `approval(conn, id)`, `message(conn, id)`, `conversation(conn, id)`, `refusals_for(...)`,
   `undelivered(conn, to_actor=None)`.
"""
import contextvars
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from pathlib import Path

from .batch_work import isolated
from . import delivery
from . import idempotency_messages
from . import task_relations as TR
from .task_rights import represents as task_represents, participant as task_participant, mover as task_mover
from . import task_roles as TRo
from . import task_snooze as TS
from . import run_triggers as RT
from clients.manifest import repo_dir
from backend.chat_goals_schema import SCHEMA as CHAT_GOALS_SCHEMA
from backend.repositories_schema import SCHEMA as REPOSITORIES_SCHEMA
from backend.subscriptions_schema import SCHEMA as SUBSCRIPTIONS_SCHEMA
from backend.triggers_schema import SCHEMA as TRIGGERS_SCHEMA, SIGNING_SCHEMA as TRIGGER_SIGNING_SCHEMA
from backend.rules_schema import SCHEMA as RULE_EVENTS_SCHEMA
from backend.company_page.schema import COMPANY_PAGE_SCHEMA
from backend.triggers_incoming_schema import SCHEMA as TRIGGERS_INCOMING_SCHEMA, CONSUMER_SCHEMA as TRIGGERS_CONSUMER_SCHEMA
from backend.hook_relay_schema import SCHEMA as HOOK_RELAY_SCHEMA
from backend.webhooks_schema import SCHEMA as WEBHOOKS_SCHEMA
from backend.sleep_idle_schema import SCHEMA as SLEEP_IDLE_SCHEMA
from backend.tico_notices_schema import SCHEMA as TICO_NOTICES_SCHEMA
from backend.postal_mail_schema import SCHEMA as POSTAL_MAIL_SCHEMA, V2 as POSTAL_MAIL_V2_SCHEMA, V3 as POSTAL_MAIL_V3_SCHEMA
from backend.webhooks_v2_schema import SCHEMA as WEBHOOKS_V2_SCHEMA
from backend.webhooks_v3_schema import SCHEMA as WEBHOOKS_V3_SCHEMA
from backend.webhooks_v4_schema import SCHEMA as WEBHOOKS_V4_SCHEMA
from backend.webhooks_v5_schema import SCHEMA as WEBHOOKS_V5_SCHEMA
from backend.webhooks_v6_schema import SCHEMA as WEBHOOKS_V6_SCHEMA

HUB_DIR = Path(__file__).resolve().parent.parent
ROOT = HUB_DIR.parent                       # employees are siblings of the hub
DEFAULT_DB = ROOT / "runtime" / "hub.db"
KEEPER = "keeper"
REVIEW_OWNER = "coo"                        # rule 8's first review task
FLEET_MAINTAINER = "botops"                 # sweeps every bot's stuck tasks and may start them
STUCK_HOURS = 24                            # an open bot task untouched this long is stuck

BOT_STATES = ("active", "paused", "planned", "quarantined", "archived")
STATUS_STATES = ("idle", "running", "waiting_human", "waiting_bot", "blocked",
                 "limited", "crashed", "paused", "quarantined")
MESSAGE_KINDS = ("say", "ask", "answer", "notice", "steer")
CONVERSATION_KINDS = ("chat", "ask", "task", "notice")
CONVERSATION_SCOPES = ("direct", "personal", "shared", "task")
# `review` and `ready` belong to the product lane (a PR is open; a PR is merged and waits for
# a deploy). To everything that only knows the company lane they are a kind of `doing`.
TASK_STATUSES = ("open", "doing", "waiting", "review", "ready", "done", "closed", "declined")
ACTIVE_STATUSES = ("open", "doing", "waiting", "review", "ready")   # still on the board
# tasks_due_active (TASKS_DUE_INDEX) is built from this list. An existing index keeps the list it was created with, so
# changing it needs that index under a new name and a migration, or upgraded hubs quietly scan every task again.
BOARD_STATUSES = ACTIVE_STATUSES + ("declined",)               # visible until a person reassigns or closes it
LIVE_STATUSES = ACTIVE_STATUSES + ("done",)                # not closed, not declined
# A seeded type has no step of these statuses: a waiting task stays in the working step it is in (a Waiting tag shows
# it), and a declined one goes to its type's closed step (`_task_state`, `retire_held_steps`). A team may still add a
# waiting step of its own, a column for parked work that only a move by hand fills; a declined step it may not add.
# The Product roadmap keeps its public Declined.
HELD_STATUSES = ("waiting", "declined")
STEP_STATUSES = tuple(s for s in TASK_STATUSES if s not in HELD_STATUSES)
MADE_STEP_STATUSES = tuple(s for s in TASK_STATUSES if s != "declined")   # what a type's own step may be
OWNER_STATUSES = ("open", "doing", "waiting", "review", "done", "declined")  # what the owner may set
# `ready` is a fact the hub learns (the PR merged), not a claim a bot makes: movers and the
# keeper set it, an owning bot does not.
# The product lane is retired. Old rows may still say 'product' (so it is
# still a lane to read and filter), but every new or moved task is company work.
TASK_LANES = ("company", "product")
LANE_TEAMS = ("product", "engineering")     # store.py's one-time lane migration (history)
# People who may move any task: change its state, rank, lane, owner, labels, parent, blockers or
# parent. Keyed on the roster team in
# registry/people.yaml, so adding a mover is a roster edit. The environment owner always may.
MOVER_TEAMS = ("leadership", "product", "engineering")
MOVER_FIELDS = ("lane", "number")   # what only a mover changes (a parent or blocker: task_relations)
# Labels are a mover's too, except that a bot may change a classification label on a task it manages
# (_label_writer). Labels Tico acts on stay a mover's: a task goal (execution.task_goal) ...
WORKFLOW_LABELS = ("goal:on", "goal:off")
# ...and a label on a public roadmap (roadmaps.settings) needs the bot's approved publish approval.
# A label refusal counts toward a quarantine once per task in this window (_escalate), so a bot
# retrying a refusal it already had cannot quarantine itself in seconds.
LABEL_REFUSAL_WINDOW_S = 3600
LINK_KINDS = ("pr", "issue", "url", "doc", "worktree", "session")
TITLE_LINT = os.environ.get("TICO_TITLE_LINT", "warn")     # warn | refuse | off
# Rule 7's writing checks on an item for a person and the shape of an update: warned about with
# the write accepted, refused only when TICO_STYLE_LINT=refuse. Bots lost whole runs rewriting
# for them. A missing title or approval field is structural and always refused.
STYLE_LINT = os.environ.get("TICO_STYLE_LINT", "warn")     # warn | refuse | off
APPROVAL_KINDS = ("send", "spend", "publish", "merge", "support", "task_cap", "spend_limit", "finance")
APPROVAL_FIELDS = {"send": ("to", "cc", "subject", "body_sha256", "mailbox"),
                   "spend": ("amount", "account", "what"),
                   "publish": ("url", "content_sha256"),
                   "merge": ("repo", "pr"),
                   # A bot's own task cap (backend/task_caps.py): the pair; per_day null asks to remove the cap.
                   "task_cap": ("sender", "receiver"),
                   # The redesign's other proposal (backend/proposals.py checks its whole shape): a spend limit.
                   "spend_limit": (),
                   # A report to the Tico team (backend/support.py `file_approved`): the message, its hash, and whether
                   # the version and install ID go with it. Nothing else may: no email, no diagnostics bundle.
                   "support": ("message", "content_sha256")}
SUPPORT_APPROVAL_KEYS = ("message", "content_sha256", "include_ids")

CAP_PER_HOUR = 20               # rule 3: messages in one conversation between bots, per hour
MAX_ASK_DEPTH = 3               # rule 3: A asks B asks C; C may not ask on
UNSOLICITED_PER_DAY = int(os.environ.get("TICO_UNSOLICITED_PER_DAY", "10"))   # rule 4: per human, per bot, per UTC day
HELD_QUEUE_ITEMS = int(os.environ.get("TICO_HELD_QUEUE_ITEMS", "200"))
HELD_QUEUE_BYTES = int(os.environ.get("TICO_HELD_QUEUE_BYTES", "2097152"))
HELD_QUEUE_SENDER_ITEMS = int(os.environ.get("TICO_HELD_QUEUE_SENDER_ITEMS", "50"))
HELD_QUEUE_SENDER_BYTES = int(os.environ.get("TICO_HELD_QUEUE_SENDER_BYTES", "524288"))
REVIEW_AT = 3                   # rule 8: refusals in a day that open a review task
QUARANTINE_AT = 10              # rule 8: repeated refusals in a day that quarantine the bot
QUARANTINE_TOTAL_AT = 3 * QUARANTINE_AT   # rule 8: any refusals in a day, repeated or not (a loop over new targets)
NOTICE_DAYS = 14                # how long a notice stays in the inbox

# ----------------------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
  slug TEXT PRIMARY KEY, display_name TEXT, runtime TEXT, model TEXT, effort TEXT,
  cwd TEXT, host TEXT DEFAULT 'dispatcher', thread_id TEXT, token_hash TEXT,
  state TEXT DEFAULT 'active', created TEXT, last_turn_at TEXT);

CREATE TABLE IF NOT EXISTS humans (
  id TEXT PRIMARY KEY, name TEXT, email TEXT, slack_id TEXT, teams_json TEXT);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, kind TEXT, subject TEXT, task_id TEXT, participants_json TEXT,
  created TEXT, last_message_at TEXT, closed_at TEXT);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id),
  from_actor TEXT, to_actor TEXT, kind TEXT, body TEXT, refs_json TEXT, in_reply_to TEXT,
  created TEXT, delivered_at TEXT, read_at TEXT, expires_at TEXT, wait_s INTEGER);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT, body TEXT, requester TEXT, owner TEXT, status TEXT,
  due TEXT, parent_id TEXT, conversation_id TEXT,
  created TEXT, updated TEXT, done_at TEXT, closed_at TEXT, closed_by TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS task_events (
  id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), ts TEXT, actor TEXT,
  field TEXT, old TEXT, new TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, kind TEXT, task_id TEXT, message_id TEXT, payload_json TEXT,
  payload_hash TEXT, requested_by TEXT, decided_by TEXT, decision TEXT,
  decided_at TEXT, consumed_at TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS bot_status (
  bot TEXT PRIMARY KEY, state TEXT, focus TEXT, task_id TEXT, since TEXT, last_turn_at TEXT,
  last_result TEXT, next_due TEXT, open_tasks INTEGER DEFAULT 0, needs_human INTEGER DEFAULT 0,
  updated_at TEXT);

CREATE TABLE IF NOT EXISTS bot_status_history (
  id TEXT PRIMARY KEY, bot TEXT, state TEXT, focus TEXT, task_id TEXT,
  since TEXT, until TEXT, reason TEXT, by TEXT);

CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, bot TEXT, cron TEXT, title TEXT, playbook TEXT,
  last_fired TEXT, next_due TEXT);

CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY, bot TEXT, thread_id TEXT, started TEXT, finished TEXT, trigger TEXT,
  message_id TEXT, task_id TEXT, exit TEXT, tokens_in INTEGER, tokens_out INTEGER,
  cost REAL, summary TEXT);

CREATE TABLE IF NOT EXISTS deltas (
  id TEXT PRIMARY KEY, turn_id TEXT, seq INTEGER, kind TEXT, text TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS rate_limits (
  runtime TEXT PRIMARY KEY, used_percent REAL, window_minutes INTEGER,
  resets_at TEXT, updated TEXT);

CREATE TABLE IF NOT EXISTS refusals (
  id TEXT PRIMARY KEY, ts TEXT, actor TEXT, rule TEXT, detail_json TEXT, severity TEXT);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, ts TEXT, actor TEXT, action TEXT, target TEXT, detail_json TEXT);

CREATE INDEX IF NOT EXISTS messages_to_delivered ON messages(to_actor, delivered_at);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, created);
CREATE INDEX IF NOT EXISTS tasks_owner_status ON tasks(owner, status);
CREATE INDEX IF NOT EXISTS tasks_requester_status ON tasks(requester, status);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS deltas_turn ON deltas(turn_id, seq);
CREATE INDEX IF NOT EXISTS refusals_actor_ts ON refusals(actor, ts);
CREATE INDEX IF NOT EXISTS status_history_bot ON bot_status_history(bot, since);
CREATE INDEX IF NOT EXISTS turns_bot_started ON turns(bot, started);
"""

from .meetings_schema import (MEETING_BRAIN_SCHEMA, MEETING_COMMENTS_SCHEMA, MEETING_ITEMS_SCHEMA,
                              MEETING_SCHEMA, RECORDING_SOURCES_SCHEMA, MEETING_REVIEW_SCHEMA,
                              LIVE_MEETINGS_SCHEMA, LIVE_MEETINGS_ROUTING_LIMITS_SCHEMA,
                              LIVE_MEETINGS_CHAT_ROUTING_SCHEMA, LIVE_MEETING_ACCESS_SCHEMA, LIVE_MEETINGS_CLIENT_SCHEMA,
                              LIVE_MEETINGS_MODEL_SCHEMA, LIVE_MEETING_BOT_CONVERSATION_SCHEMA,
                              LIVE_MEETING_TURN_RETRY_SCHEMA)

# Goals and KPIs (backend/goals.py). A task may name the goal
# it serves; the column is optional and nothing requires it.
GOALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL, parent_id TEXT,
  body TEXT NOT NULL DEFAULT '', status TEXT, status_note TEXT NOT NULL DEFAULT '',
  status_by TEXT, status_at TEXT, rank INTEGER,
  last_read_at TEXT, last_read_by TEXT,
  created TEXT NOT NULL, created_by TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS goals_owner ON goals(owner);
CREATE INDEX IF NOT EXISTS goals_parent ON goals(parent_id);

CREATE TABLE IF NOT EXISTS goal_events (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, ts TEXT NOT NULL, actor TEXT NOT NULL,
  field TEXT NOT NULL, old TEXT, new TEXT, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS goal_events_goal ON goal_events(goal_id, ts);

CREATE TABLE IF NOT EXISTS kpis (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '',
  target REAL, created TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kpis_goal ON kpis(goal_id);

CREATE TABLE IF NOT EXISTS kpi_readings (
  id TEXT PRIMARY KEY, kpi_id TEXT NOT NULL, ts TEXT NOT NULL, value REAL NOT NULL,
  actor TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'measured', note TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kpi_readings_kpi ON kpi_readings(kpi_id, ts);
ALTER TABLE tasks ADD COLUMN goal_id TEXT;
"""

# KPIs as records of their own, and how goals are coloured (backend/kpis.py, backend/goals.py). A KPI
# no longer belongs to one goal: `goal_kpis` links a goal to a KPI and holds the target (an improvement
# from a baseline to a value by a deadline, or a range to stay inside). Readings keep the business time
# (`period_start`/`period_end`) apart from `collected_at`, and a correction is a new reading that
# `supersedes` the old one. The old `kpis.goal_id` and `kpis.target` stay for the rows that had them; a
# new KPI stores '' in `goal_id`. Rows that existed before this migration are recognised by `owner IS NULL`.
KPIS_SCHEMA = """
ALTER TABLE kpis ADD COLUMN slug TEXT;
ALTER TABLE kpis ADD COLUMN definition TEXT NOT NULL DEFAULT '';
ALTER TABLE kpis ADD COLUMN direction TEXT NOT NULL DEFAULT 'up';
ALTER TABLE kpis ADD COLUMN cadence TEXT NOT NULL DEFAULT 'weekly';
ALTER TABLE kpis ADD COLUMN owner TEXT;
ALTER TABLE kpis ADD COLUMN source_note TEXT NOT NULL DEFAULT '';
ALTER TABLE kpis ADD COLUMN definition_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE kpis ADD COLUMN updated TEXT;
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
  created TEXT NOT NULL, created_by TEXT NOT NULL, updated TEXT NOT NULL, updated_by TEXT NOT NULL,
  UNIQUE (goal_id, kpi_id));
CREATE INDEX IF NOT EXISTS goal_kpis_kpi ON goal_kpis(kpi_id);

CREATE TABLE IF NOT EXISTS kpi_definitions (
  id TEXT PRIMARY KEY, kpi_id TEXT NOT NULL, version INTEGER NOT NULL, ts TEXT NOT NULL, actor TEXT NOT NULL,
  name TEXT NOT NULL, definition TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '',
  direction TEXT NOT NULL, cadence TEXT NOT NULL, source_note TEXT NOT NULL DEFAULT '',
  UNIQUE (kpi_id, version));

CREATE TABLE IF NOT EXISTS goal_checkins (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, kpi_id TEXT, ts TEXT NOT NULL, author TEXT NOT NULL,
  source_actor TEXT NOT NULL, body TEXT NOT NULL, signal TEXT);
CREATE INDEX IF NOT EXISTS goal_checkins_goal ON goal_checkins(goal_id, ts);

CREATE TABLE IF NOT EXISTS goal_proposals (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, goal_id TEXT, kpi_id TEXT, payload_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '', proposed_by TEXT NOT NULL, proposed_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, decided_at TEXT, decision_note TEXT NOT NULL DEFAULT '',
  result_json TEXT);
CREATE INDEX IF NOT EXISTS goal_proposals_status ON goal_proposals(status, proposed_at);

UPDATE kpi_readings SET period_end=ts, period_start=ts, collected_at=created WHERE period_end IS NULL;
UPDATE kpi_readings SET quality='estimate' WHERE source='estimate' AND quality='measured' AND supersedes IS NULL AND period_start=period_end;
UPDATE kpis SET owner=COALESCE((SELECT owner FROM goals WHERE goals.id=kpis.goal_id), 'company'),
  cadence='monthly', updated=created WHERE owner IS NULL;
INSERT OR IGNORE INTO goal_kpis (id, goal_id, kpi_id, kind, target, created, created_by, updated, updated_by)
  SELECT id, goal_id, id, CASE WHEN target IS NULL THEN 'none' ELSE 'improve' END, target, created, created_by,
         created, created_by FROM kpis WHERE goal_id != '' AND goal_id IN (SELECT id FROM goals);
INSERT OR IGNORE INTO kpi_definitions (id, kpi_id, version, ts, actor, name, definition, unit, direction, cadence, source_note)
  SELECT id || ':1', id, 1, created, created_by, name, definition, unit, direction, cadence, source_note FROM kpis;
UPDATE goals SET status_source='person' WHERE status IS NOT NULL AND status_source IS NULL;
UPDATE goal_events SET status_by=actor, status_source='person' WHERE field='status' AND status_source IS NULL;
"""

KPI_ARCHIVE_SCHEMA = """
ALTER TABLE kpis ADD COLUMN archived_at TEXT;
ALTER TABLE kpis ADD COLUMN archived_by TEXT;
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
  last_verified TEXT,
  created TEXT NOT NULL,
  created_by TEXT NOT NULL,
  updated TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS market_edges (
  id TEXT PRIMARY KEY,
  src TEXT NOT NULL,
  rel TEXT NOT NULL,
  dst TEXT NOT NULL,
  since TEXT,
  until TEXT,
  confidence TEXT NOT NULL DEFAULT 'medium',
  properties TEXT NOT NULL DEFAULT '{}',
  created TEXT NOT NULL,
  created_by TEXT NOT NULL,
  updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS market_edges_src ON market_edges(src, rel);
CREATE INDEX IF NOT EXISTS market_edges_dst ON market_edges(dst, rel);
CREATE INDEX IF NOT EXISTS market_edges_until ON market_edges(until);

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
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  field TEXT NOT NULL,
  old TEXT,
  new TEXT,
  insight_id TEXT,
  note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS market_events_subject ON market_events(subject_kind, subject_id, ts);

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
ALTER TABLE messages ADD COLUMN answered_by TEXT;
CREATE INDEX IF NOT EXISTS messages_open_asks ON messages(to_actor, from_actor, kind, answered_by);
"""

# Listening's observation store and the shared intake (backend/listening.py). Four tables: what a
# sweep covered, what it saw, what the decision model scored, and where each post was sent. A market insight
# filed from an intake item names it in `source_ref`, once per reporter.
LISTENING_SCHEMA = """
CREATE TABLE IF NOT EXISTS listen_runs (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  query TEXT NOT NULL,
  started_at TEXT NOT NULL,
  status TEXT NOT NULL,
  pages_read INTEGER DEFAULT 0,
  items_seen INTEGER DEFAULT 0,
  note TEXT);
CREATE INDEX IF NOT EXISTS listen_runs_started ON listen_runs(started_at, source);

CREATE TABLE IF NOT EXISTS listen_items (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  native_id TEXT NOT NULL,
  url TEXT NOT NULL,
  author TEXT,
  author_url TEXT,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  published_at TEXT,
  first_seen_at TEXT NOT NULL,
  first_run_id TEXT REFERENCES listen_runs(id),
  UNIQUE (source, native_id));

CREATE TABLE IF NOT EXISTS listen_judgments (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES listen_items(id),
  question_set TEXT NOT NULL,
  scores_json TEXT NOT NULL,
  judged_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS listen_judgments_item ON listen_judgments(item_id, judged_at);

CREATE TABLE IF NOT EXISTS intake_items (
  id TEXT PRIMARY KEY,
  destination TEXT NOT NULL,
  item_id TEXT NOT NULL REFERENCES listen_items(id),
  judgment_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',
  receiver_ref TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  UNIQUE (destination, item_id));
CREATE INDEX IF NOT EXISTS intake_items_queue ON intake_items(destination, status, created_at);

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
CREATE INDEX IF NOT EXISTS turns_started ON turns(started);
"""

# Spend limits (backend/usage_limits.py): a bot's own daily and monthly cap in estimated USD (null: follow the
# company default), and the warnings already sent, one per bot, period, limit and level.
USAGE_LIMITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_limits (
  bot TEXT PRIMARY KEY, daily_usd REAL, monthly_usd REAL, updated TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS usage_alerts (
  bot TEXT NOT NULL, period TEXT NOT NULL, level INTEGER NOT NULL, sent TEXT NOT NULL,
  PRIMARY KEY (bot, period, level));
"""

TAGS_TABLES_SCHEMA = """
ALTER TABLE tasks ADD COLUMN labels_json TEXT NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', markdown TEXT NOT NULL DEFAULT '',
  is_template INTEGER NOT NULL DEFAULT 0, template_id TEXT REFERENCES tags(id),
  owner TEXT, version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_tags (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES tags(id), PRIMARY KEY(task_id, tag_id));
CREATE INDEX IF NOT EXISTS task_tags_tag ON task_tags(tag_id, task_id);
"""

TAGS_BACKFILL = """
INSERT OR IGNORE INTO tags(id,key,label,created,updated)
  SELECT 'tag-'||lower(hex(randomblob(16))), value, value,
         strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
  FROM (SELECT DISTINCT value FROM tasks, json_each(tasks.labels_json) WHERE type='text' AND value<>'');
INSERT OR IGNORE INTO task_tags(task_id,tag_id)
  SELECT tasks.id,tags.id FROM tasks,json_each(tasks.labels_json) old
  JOIN tags ON tags.key=old.value;
"""

TAGS_SCHEMA = TAGS_TABLES_SCHEMA + TAGS_BACKFILL

GENERAL_TYPE = "general"
PIPELINES_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_types (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS task_steps (
  id TEXT PRIMARY KEY, type_id TEXT NOT NULL REFERENCES task_types(id), name TEXT NOT NULL,
  position INTEGER NOT NULL, status TEXT NOT NULL
    CHECK(status IN ('open','doing','waiting','review','ready','done','closed','declined')),
  UNIQUE(type_id, name));
CREATE INDEX IF NOT EXISTS task_steps_type ON task_steps(type_id, position, id);
ALTER TABLE tasks ADD COLUMN type_id TEXT REFERENCES task_types(id);
ALTER TABLE tasks ADD COLUMN step_id TEXT REFERENCES task_steps(id);
CREATE INDEX IF NOT EXISTS tasks_type_step ON tasks(type_id, step_id);
INSERT OR IGNORE INTO task_types(id,name,created,updated)
  VALUES('general','General',strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'));
""" + "\n".join(
    f"INSERT OR IGNORE INTO task_steps(id,type_id,name,position,status) "
    f"VALUES('general-{status}','general','{status.title()}',{position},'{status}');"
    for position, status in enumerate(STEP_STATUSES)
) + """
UPDATE tasks SET type_id='general', step_id=(SELECT id FROM task_steps
  WHERE type_id='general' AND status=tasks.status ORDER BY position,id LIMIT 1)
  WHERE type_id IS NULL;
"""

TASK_LINKS_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created TEXT NOT NULL,
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
ALTER TABLE task_links ADD COLUMN detail_json TEXT;
ALTER TABLE task_links ADD COLUMN updated TEXT;
CREATE INDEX IF NOT EXISTS task_links_repo_number ON task_links(repo,number);
UPDATE task_links SET repo=substr(url,20,instr(url,'/pull/')-20),
  number=CAST(substr(url,instr(url,'/pull/')+6) AS INTEGER), updated=created
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

# A numbered type gives each of its tasks the team's next number, kept for good; `step_rank` is a
# task's place within its step, apart from `rank`, its place in the owner's queue. Every task
# already in a step keeps the order it was filed in (rowid order), so a new one joins the end.
NUMBERS_SCHEMA = """
ALTER TABLE task_types ADD COLUMN numbered INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN number INTEGER;
ALTER TABLE tasks ADD COLUMN step_rank REAL;
CREATE UNIQUE INDEX IF NOT EXISTS tasks_number ON tasks(number);
CREATE INDEX IF NOT EXISTS tasks_step_rank ON tasks(step_id, step_rank);
UPDATE tasks SET step_rank=rowid WHERE step_id IS NOT NULL AND step_rank IS NULL;
"""

# Existing records have no reliable sensitivity marker. Preserve them privately on upgrade;
# legacy inserts that omit the new field also fail closed. Current creates specify the default.
TASK_PRIVACY_SCHEMA = """
ALTER TABLE tasks ADD COLUMN private INTEGER NOT NULL DEFAULT 1 CHECK (private IN (0,1));
"""

# The person a bot's `waiting` task waits on (`hub task update --status waiting --on <person>`):
# it puts the task in that person's Needs you and counts toward the bot's `needs_human`.
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
  harness TEXT NOT NULL, effort TEXT NOT NULL DEFAULT '', role TEXT NOT NULL, created_by TEXT, created TEXT);
"""

# What the Strategy Bot has asked a person on the Goals page's behalf (backend/goal_asks.py): one row per
# KPI or proposal, kind and person, naming the task that asks it; append to preserve existing installations.
GOAL_ASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS goal_asks (
  kind TEXT NOT NULL, ref TEXT NOT NULL, owner TEXT NOT NULL, task_id TEXT NOT NULL, created TEXT NOT NULL,
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

# The one person a private task with no person on it may reach (hubdb.task_reach): see "a private task's guest".
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
  bot TEXT NOT NULL, conversation_id TEXT NOT NULL, marked TEXT NOT NULL, taken_by TEXT,
  PRIMARY KEY(bot, conversation_id));
"""

# The Strategy Bot's merged asks (backend/strategy_merge.py): each original item in a person's Needs you that one
# Strategy Bot item stands for, and what became of it. An install that has the old table gets the table and its rows
# before the migrations run (`_sync_strategy_tables`), so this creates the table only on a new install.
STRATEGY_MERGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_merges (
  merged_into TEXT NOT NULL, original TEXT NOT NULL, person TEXT NOT NULL, asker TEXT NOT NULL,
  how TEXT NOT NULL DEFAULT 'asked', ask_id TEXT,
  state TEXT NOT NULL DEFAULT 'merged', created TEXT NOT NULL, settled_at TEXT, note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (merged_into, original));
CREATE INDEX IF NOT EXISTS strategy_merges_original ON strategy_merges(original, state);
CREATE INDEX IF NOT EXISTS strategy_merges_person ON strategy_merges(person, created);
"""

# The Strategy Bot log (backend/strategy_acts.py): each decision it made alone at level 3, or each thing it proposed
# because it stays the owner's, and its Undo. Every task event one decision (or its Undo) wrote carries its action id.
# As for STRATEGY_MERGES_SCHEMA, an install with the old table gets the table and its rows first.
STRATEGY_ACTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_actions (
  id TEXT PRIMARY KEY, created TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  goal_id TEXT, state TEXT NOT NULL, proposal_task TEXT, note TEXT NOT NULL DEFAULT '',
  changes_json TEXT NOT NULL DEFAULT '{}', undone_at TEXT, undone_by TEXT, undo_json TEXT);
CREATE INDEX IF NOT EXISTS strategy_actions_created ON strategy_actions(created);
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
  created TEXT NOT NULL,
  updated TEXT NOT NULL);
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
  created TEXT NOT NULL);
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
  created TEXT NOT NULL);
"""

# Task caps (backend/task_caps.py, redesign/CONTRACT.md §1): the cap per sender and manager, every send (kept per task,
# so a hand-over counts too), and the caps a bot proposes for a person to approve.
TASK_CAPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_caps(sender TEXT, receiver TEXT, per_day INTEGER, updated TEXT, updated_by TEXT,
  PRIMARY KEY(sender, receiver));
CREATE TABLE IF NOT EXISTS task_cap_sends(task_id TEXT PRIMARY KEY, sender TEXT, receiver TEXT, sent_at TEXT);
CREATE INDEX IF NOT EXISTS task_cap_sends_pair ON task_cap_sends(sender, receiver, sent_at);
"""

# Every task cap send is kept (backend/task_caps.py): one row per task, sender, receiver and time, so a hand-over adds a
# send and never replaces the one before it. `task_cap_sends` (one row per task) is copied in and no longer written.
TASK_CAP_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_cap_log(task_id TEXT NOT NULL, sender TEXT NOT NULL, receiver TEXT NOT NULL,
  sent_at TEXT NOT NULL, PRIMARY KEY(task_id, sender, receiver, sent_at));
CREATE INDEX IF NOT EXISTS task_cap_log_pair ON task_cap_log(sender, receiver, sent_at);
INSERT OR IGNORE INTO task_cap_log(task_id, sender, receiver, sent_at)
  SELECT task_id, sender, receiver, sent_at FROM task_cap_sends
  WHERE task_id IS NOT NULL AND sender IS NOT NULL AND receiver IS NOT NULL AND sent_at IS NOT NULL;
"""

# A pull request's last activity (CONTRACT section 6, PR find): the time of its newest GitHub pull_request, review or
# review comment event. NULL on rows from before it, which read as their creation (automation_actions FIND).
TASK_LINK_ACTIVITY_SCHEMA = """
ALTER TABLE task_links ADD COLUMN activity_at TEXT;
"""

# Task trigger tables (rule_task_runs, rule_task_state) were added to RULE_EVENTS_SCHEMA after it shipped, so an
# install that ran it earlier never got them and every task trigger failed quietly. Every statement is IF NOT EXISTS.
RULE_TASK_TABLES_SCHEMA = "-- rule_task_runs, rule_task_state for installs that ran RULE_EVENTS_SCHEMA first\n" + RULE_EVENTS_SCHEMA

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
  runner_id TEXT PRIMARY KEY, sharing INTEGER NOT NULL DEFAULT 0, bots_json TEXT NOT NULL DEFAULT '[]',
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


# Growth (backend/growth.py) and the brand and go-to-market pages (backend/brand_gtm.py). Brand and GTM each keep
# one draft row (id 1, with a revision so two editors never overwrite each other) and numbered published versions.
# A cohort with no channel is the Unknown row: SQLite treats NULLs as distinct in UNIQUE, so the one-row-per-month
# rule is an expression index over coalesce(channel_id, '').
GROWTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS brand_drafts (
  id INTEGER PRIMARY KEY CHECK(id=1), body_json TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0,
  updated_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS brand_versions (
  id TEXT PRIMARY KEY, n INTEGER NOT NULL UNIQUE, body_json TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '',
  published_by TEXT, published_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gtm_drafts (
  id INTEGER PRIMARY KEY CHECK(id=1), body_json TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0,
  updated_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS gtm_versions (
  id TEXT PRIMARY KEY, n INTEGER NOT NULL UNIQUE, body_json TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS growth_ideas (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id), hypothesis TEXT NOT NULL DEFAULT '', prediction TEXT NOT NULL DEFAULT '',
  bet_id TEXT, kpi_id TEXT, judged_kpi_id TEXT, ice_i INTEGER, ice_c INTEGER, ice_e INTEGER,
  status TEXT NOT NULL DEFAULT 'idea'
    CHECK(status IN ('idea','testing','paused','won','lost','inconclusive','dropped')),
  source_kind TEXT, source_ref TEXT, test_from TEXT, test_to TEXT, change TEXT, stop_if TEXT, min_events INTEGER,
  audience TEXT, external_ref TEXT, result TEXT, learning TEXT,
  bet_effect TEXT CHECK(bet_effect IS NULL OR bet_effect IN ('supports','weakens','none')),
  confidence TEXT CHECK(confidence IS NULL OR confidence IN ('low','medium','high')),
  verdict_at TEXT, verdict_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS growth_ideas_status ON growth_ideas(status, updated_at);
CREATE INDEX IF NOT EXISTS growth_ideas_bet ON growth_ideas(bet_id) WHERE bet_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS growth_idea_channels (
  task_id TEXT NOT NULL REFERENCES tasks(id), channel_id TEXT NOT NULL, PRIMARY KEY(task_id, channel_id));
CREATE INDEX IF NOT EXISTS growth_idea_channels_channel ON growth_idea_channels(channel_id);
CREATE TABLE IF NOT EXISTS growth_cohorts (
  id TEXT PRIMARY KEY, channel_id TEXT, month TEXT NOT NULL, spend REAL, bot_cost REAL, new_customers INTEGER,
  kept_d30 REAL, kept_d90 REAL, kept_d180 REAL, revenue_month REAL, margin_pct REAL,
  quality TEXT NOT NULL DEFAULT 'actual' CHECK(quality IN ('actual','estimate')), sources_json TEXT NOT NULL DEFAULT '{}',
  updated_by TEXT, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS growth_cohorts_month ON growth_cohorts(coalesce(channel_id, ''), month);
CREATE TABLE IF NOT EXISTS growth_settings (
  id INTEGER PRIMARY KEY CHECK(id=1), unit TEXT NOT NULL DEFAULT 'customers', revenue_month REAL, margin_pct REAL,
  framework TEXT NOT NULL DEFAULT '', verdict_win_pct REAL NOT NULL DEFAULT 60);
ALTER TABLE kpis ADD COLUMN parent_kpi_id TEXT;
ALTER TABLE kpis ADD COLUMN growth_role TEXT;
ALTER TABLE kpis ADD COLUMN stage TEXT;
ALTER TABLE kpis ADD COLUMN loop_name TEXT;
ALTER TABLE kpis ADD COLUMN loop_step INTEGER;
"""


# Growth segments (backend/growth.py): who the company sells to, each a market `segment` entity with its growth
# fields. Serving mirrors `company/self` `operates_in` the segment and next its `next: true` property
# (backend/strategy_market.py keeps both in step). Channels and ideas name the segments they reach; a cohort may be one
# segment's share of a channel's month, so the one-row rule takes the segment in. Segments the graph already marks Us
# or Next start as serving or next.
SEGMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS growth_segments (
  id TEXT PRIMARY KEY, segment_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('serving','testing','next','dropped')),
  motion TEXT CHECK(motion IS NULL OR motion IN ('self_serve','sales_led','partner','marketplace','local','community',
                                                 'ecommerce')),
  who_buys TEXT, deal_size TEXT, why TEXT NOT NULL DEFAULT '', owner_ref TEXT, sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT);
CREATE TABLE IF NOT EXISTS growth_channel_segments (
  channel_id TEXT NOT NULL, segment_row_id TEXT NOT NULL, PRIMARY KEY(channel_id, segment_row_id));
CREATE INDEX IF NOT EXISTS growth_channel_segments_segment ON growth_channel_segments(segment_row_id);
CREATE TABLE IF NOT EXISTS growth_idea_segments (
  task_id TEXT NOT NULL, segment_row_id TEXT NOT NULL, PRIMARY KEY(task_id, segment_row_id));
CREATE INDEX IF NOT EXISTS growth_idea_segments_segment ON growth_idea_segments(segment_row_id);
ALTER TABLE growth_cohorts ADD COLUMN segment_id TEXT;
DROP INDEX IF EXISTS growth_cohorts_month;
CREATE UNIQUE INDEX IF NOT EXISTS growth_cohorts_month_segment
  ON growth_cohorts(coalesce(channel_id, ''), month, coalesce(segment_id, ''));
INSERT OR IGNORE INTO growth_segments (id, segment_id, status, created_at, updated_at)
  SELECT lower(hex(randomblob(16))), e.id,
    CASE WHEN EXISTS (SELECT 1 FROM market_edges m WHERE m.rel='operates_in' AND m.src='company/self' AND m.dst=e.id
      AND (m.since IS NULL OR substr(m.since,1,10)<=date('now')) AND (m.until IS NULL OR substr(m.until,1,10)>=date('now')))
    THEN 'serving' ELSE 'next' END,
    strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')
  FROM market_entities e WHERE e.type='segment' AND e.status='active' AND (
    EXISTS (SELECT 1 FROM market_edges m WHERE m.rel='operates_in' AND m.src='company/self' AND m.dst=e.id
      AND (m.since IS NULL OR substr(m.since,1,10)<=date('now')) AND (m.until IS NULL OR substr(m.until,1,10)>=date('now')))
    OR (json_valid(e.properties) AND json_extract(e.properties,'$.next')));
"""

MIGRATIONS = [SCHEMA, MEETING_SCHEMA, MEETING_ITEMS_SCHEMA,   # index i takes user_version from i
              MEETING_BRAIN_SCHEMA, MEETING_COMMENTS_SCHEMA,  # to i+1; append, never edit
              GOALS_SCHEMA, RECORDING_SOURCES_SCHEMA, MARKET_SCHEMA,
              REPLY_ANSWERS_ASKS, LISTENING_SCHEMA, KPIS_SCHEMA, USAGE_SCHEMA,
              USAGE_LIMITS_SCHEMA, TAGS_SCHEMA, PIPELINES_SCHEMA, CHAT_GOALS_SCHEMA, REPOSITORIES_SCHEMA, TASK_LINKS_V2_SCHEMA, SUBSCRIPTIONS_SCHEMA,
              STORAGE_SCHEMA, TASK_REVIEW_SCHEMA, MEETING_REVIEW_SCHEMA, NUMBERS_SCHEMA, TASK_PRIVACY_SCHEMA,
              USAGE_SEGMENTS_SCHEMA, KPI_ARCHIVE_SCHEMA, WAITING_ON_SCHEMA,
              LIVE_MEETINGS_SCHEMA, LIVE_MEETINGS_ROUTING_LIMITS_SCHEMA,
              LIVE_MEETINGS_CHAT_ROUTING_SCHEMA, TASK_RELATIONS_SCHEMA, TRIGGERS_SCHEMA,
              TRIGGER_SIGNING_SCHEMA, CHECK_MODELS_SCHEMA, GOAL_ASKS_SCHEMA, RUN_TRIGGERS_SCHEMA,
              TASK_GUEST_SCHEMA, STRATEGY_MERGES_SCHEMA, ONE_LOGIN_PER_COMPUTER_SCHEMA, RUN_PURPOSE_SCHEMA,
              RUN_ROTATION_SCHEMA, LIVE_MEETING_ACCESS_SCHEMA, STRATEGY_ACTIONS_SCHEMA,
              MARKET_SPEND_SCHEMA, RULE_EVENTS_SCHEMA,
              TRIGGERS_INCOMING_SCHEMA, HOOK_RELAY_SCHEMA,
              WEBHOOKS_SCHEMA, SLEEP_IDLE_SCHEMA,
              TICO_NOTICES_SCHEMA, WEBHOOKS_V2_SCHEMA, WEBHOOKS_V3_SCHEMA,
              WEBHOOKS_V4_SCHEMA, WEBHOOKS_V5_SCHEMA, WEBHOOKS_V6_SCHEMA, RUN_SESSION_SCHEMA,
              TASK_CAPS_SCHEMA, TASK_CAP_LOG_SCHEMA, TASK_LINK_ACTIVITY_SCHEMA,
              RULE_TASK_TABLES_SCHEMA, TASK_RUN_MODELS_SCHEMA, LIVE_MEETINGS_CLIENT_SCHEMA,
              LIVE_MEETINGS_MODEL_SCHEMA, LIVE_MEETING_BOT_CONVERSATION_SCHEMA, LIVE_MEETING_TURN_RETRY_SCHEMA,
              POSTAL_MAIL_SCHEMA, POSTAL_MAIL_V2_SCHEMA, POSTAL_MAIL_V3_SCHEMA, TRIGGERS_CONSUMER_SCHEMA,
              GROWTH_SCHEMA, BROWSER_EXITS_SCHEMA, SEGMENTS_SCHEMA, COMPANY_PAGE_SCHEMA]


class Refused(Exception):
    """A write the rules do not allow. `rule` names the rule; `detail` says what to fix."""

    def __init__(self, rule, detail="", severity="normal", task=None):
        super().__init__(f"{rule}: {detail}" if detail else str(rule))
        self.rule = rule
        self.detail = detail
        self.severity = severity
        self.task = task            # the task a `label` refusal was about (refuse)


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


def bot_actor(slug):
    return f"bot:{str(slug).strip()}"


def human_actor(pid):
    return f"human:{str(pid).strip()}"


def actor_kind(actor):
    """`bot`, `human`, `keeper`, or None for anything else."""
    a = str(actor or "").strip()
    if a == KEEPER:
        return KEEPER
    if a.startswith("bot:"):
        return "bot"
    if a.startswith("human:"):
        return "human"
    return None


def actor_id(actor):
    """The slug or person id inside an actor string; `keeper` for the keeper."""
    a = str(actor or "").strip()
    return a.split(":", 1)[1] if ":" in a else a


def is_bot(actor):
    return actor_kind(actor) == "bot"


def is_human(actor):
    return actor_kind(actor) == "human"


def _json(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dump(value):
    return json.dumps(value, sort_keys=True, default=str) if value is not None else None


def payload_hash(payload):
    """The exact-payload hash rule 6 matches duplicates on."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def token_hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


# ----------------------------------------------------------------------------- connection
def synchronous(default):
    """The PRAGMA synchronous a connection uses: `default`, or OFF when $TICO_SQLITE_SYNC is "off".

    Only the test suite sets it (conftest.py). Each test builds its own database and throws it away, so a sync per
    commit buys nothing there, while a shared host's disk paid for it: two pytest workers on the team runner wrote
    ~30 MB/s, a quarter of the volume's throughput, and pushed the live server's pages out of the cache."""
    return "OFF" if os.environ.get("TICO_SQLITE_SYNC", "").lower() == "off" else default


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
    rows = conn.execute("SELECT target, actor, COALESCE(NULLIF(CASE WHEN detail_json LIKE '{%' "
                        "THEN json_extract(detail_json,'$.bot') END,''), "
                        "CASE WHEN actor LIKE 'bot:%' THEN substr(actor,5) END, '') AS bot, MAX(ts) "
                        "FROM events WHERE action IN ('credential.revealed','credential.sent_to_computer') "
                        "AND actor NOT LIKE 'human:%' GROUP BY target, actor, bot").fetchall()
    # The newest event row of a pair is both its newest use and the last one logged.
    conn.executemany("INSERT INTO credential_uses(credential_id,actor,bot,at,logged) VALUES(?,?,?,?,?) "
                     "ON CONFLICT(credential_id,actor,bot) DO UPDATE SET at=MAX(at,excluded.at),logged=MAX(logged,excluded.logged)",
                     [(target, actor, bot or '', at, at) for target, actor, bot, at in rows
                      if at and target in known])
    conn.execute("INSERT INTO registry_metadata VALUES('credential-use-backfill',?)", (json.dumps(True),))


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


def _apply(conn, script):
    for statement in _statements(script):
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
            "TICO_DB at an empty path or a Tico database, or restore a snapshot.")


def ensure_market_reads(conn):
    """Additive indexes and exact resolution keys, shared by local and cloud boot."""
    conn.execute("SAVEPOINT market_read_indexes")
    try:
        for name, spec in (
            ("market_entities_name", "market_entities(name, id)"),
            ("market_entities_status_name", "market_entities(status, name, id)"),
            ("market_entities_type_name", "market_entities(type, name, id)"),
            ("market_events_ts", "market_events(ts, id)"),
            ("market_edges_rel_pair", "market_edges(rel, src, dst)"),
        ):
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='market_entity_keys'").fetchone()
        conn.execute("CREATE TABLE IF NOT EXISTS market_entity_keys (key TEXT NOT NULL, entity_id TEXT NOT NULL, "
                     "PRIMARY KEY(key, entity_id)) WITHOUT ROWID")
        conn.execute("CREATE INDEX IF NOT EXISTS market_entity_keys_entity ON market_entity_keys(entity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS market_entity_keys_unicode ON market_entity_keys(entity_id) "
                     "WHERE key GLOB '*[^ -~]*'")
        def keys(ref):
            return f"""SELECT lower(trim({ref}.name)) AS key
                UNION SELECT lower(trim(json_extract(CASE WHEN json_valid({ref}.external_ids) THEN {ref}.external_ids ELSE '{{}}' END, '$.domain')))
                UNION SELECT lower(trim(value)) FROM json_each(CASE WHEN json_valid({ref}.aliases) THEN {ref}.aliases ELSE '[]' END)"""
        if not exists:
            conn.execute("""INSERT OR IGNORE INTO market_entity_keys
                SELECT lower(trim(name)), id FROM market_entities WHERE trim(name)<>''
                UNION SELECT lower(trim(json_extract(CASE WHEN json_valid(external_ids) THEN external_ids ELSE '{}' END,'$.domain'))), id
                    FROM market_entities WHERE coalesce(trim(json_extract(CASE WHEN json_valid(external_ids) THEN external_ids ELSE '{}' END,'$.domain')),'')<>''
                UNION SELECT lower(trim(j.value)), e.id FROM market_entities e, json_each(CASE WHEN json_valid(e.aliases) THEN e.aliases ELSE '[]' END) j
                    WHERE trim(j.value)<>''""")
        for event, before in (("INSERT", ""), ("UPDATE OF name, aliases, external_ids",
                                               "DELETE FROM market_entity_keys WHERE entity_id=old.id;")):
            suffix = "insert" if event == "INSERT" else "update"
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS market_keys_{suffix} AFTER {event} ON market_entities BEGIN
                {before}
                INSERT OR IGNORE INTO market_entity_keys SELECT key, new.id FROM ({keys('new')})
                    WHERE key IS NOT NULL AND key<>'';
            END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS market_keys_delete AFTER DELETE ON market_entities BEGIN
            DELETE FROM market_entity_keys WHERE entity_id=old.id;
        END""")
    except Exception:
        conn.execute("ROLLBACK TO market_read_indexes")
        raise
    finally:
        conn.execute("RELEASE market_read_indexes")


# The Strategy Bot's tables, each under the name an older release gives it. That release, reopened on the file after a
# rollback, counts the migrations that make its tables as run, so for one release the old tables stay here. Triggers
# keep each equal to its new one, so that release reads every row as it last was; it writes only to the old table, so
# on start the old table holds the rows as they are, and the new one is made equal to it (`_sync_strategy_tables`).
STRATEGY_TABLES = (("ceo_merges", "strategy_merges", STRATEGY_MERGES_SCHEMA),
                   ("ceo_actions", "strategy_actions", STRATEGY_ACTIONS_SCHEMA))
_MIRRORS = ("insert", "update", "delete")


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _shared_columns(conn, old, new):
    kept = {row[1] for row in conn.execute(f"PRAGMA table_info({new})")}
    return [row[1] for row in conn.execute(f"PRAGMA table_info({old})") if row[1] in kept]


def _mirror(conn, old, new):
    """The triggers that copy every write to `new` to `old` (STRATEGY_TABLES)."""
    columns = _shared_columns(conn, old, new)
    names, values = ",".join(columns), ",".join("new." + column for column in columns)
    keys = [row[1] for row in conn.execute(f"PRAGMA table_info({new})") if row[5]]
    match = " AND ".join(f"{key}=old.{key}" for key in keys)
    upsert = f"INSERT OR REPLACE INTO {old}({names}) VALUES({values});"
    for event, body in (("insert", upsert), ("update", f"DELETE FROM {old} WHERE {match}; {upsert}"),
                        ("delete", f"DELETE FROM {old} WHERE {match};")):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {new}_to_old_{event} AFTER {event.upper()} ON {new} "
                     f"BEGIN {body} END")


def _sync_strategy_tables(conn):
    """Make each Strategy Bot table under its new name equal to the one under its old name, before the migrations run:
    the old one holds every row as it last was, whichever release wrote it (STRATEGY_TABLES). A file from before the
    rename gets its new tables here. What differs is looked at again under the write lock, so two servers starting
    together copy once."""
    def pending(old, new):
        found = _tables(conn)
        if old not in found:
            return False
        if new not in found:
            return True
        columns = ",".join(_shared_columns(conn, old, new))
        return conn.execute(f"SELECT 1 FROM (SELECT {columns} FROM {old} EXCEPT SELECT {columns} FROM {new}) UNION ALL "
                            f"SELECT 1 FROM (SELECT {columns} FROM {new} EXCEPT SELECT {columns} FROM {old}) "
                            "LIMIT 1").fetchone() is not None
    for old, new, schema in STRATEGY_TABLES:
        if not pending(old, new):
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            if pending(old, new):
                _apply(conn, schema)
                for event in _MIRRORS:      # the copy below is not this release's write to mirror back
                    conn.execute(f"DROP TRIGGER IF EXISTS {new}_to_old_{event}")
                columns = ",".join(_shared_columns(conn, old, new))
                conn.execute(f"DELETE FROM {new}")
                conn.execute(f"INSERT INTO {new}({columns}) SELECT {columns} FROM {old}")
                _mirror(conn, old, new)     # back before the lock goes, so no write by another server is missed
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _keep_old_strategy_tables(conn):
    """Give each Strategy Bot table its old-named twin, with its columns, where a new install has none, and the
    triggers that copy every write to it (STRATEGY_TABLES)."""
    triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    if all(old in _tables(conn) and {f"{new}_to_old_{event}" for event in _MIRRORS} <= triggers
           for old, new, _ in STRATEGY_TABLES):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        for old, new, _ in STRATEGY_TABLES:
            if old not in _tables(conn):
                sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (new,)).fetchone()[0]
                conn.execute(re.sub(r'^CREATE TABLE (IF NOT EXISTS )?("?)\w+\2', f"CREATE TABLE {old}", sql))
                columns = ",".join(_shared_columns(conn, old, new))
                conn.execute(f"INSERT INTO {old}({columns}) SELECT {columns} FROM {new}")
            _mirror(conn, old, new)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def migrate(conn, adopt_legacy=False):
    """Apply every migration the file has not seen, by `PRAGMA user_version`.

    Each migration is one transaction with its version bump, so a failure leaves the file at
    the version it had; every statement is also safe to run twice (IF NOT EXISTS, guarded
    ADD COLUMN), so a file that has the change but an older version simply catches up.
    `adopt_legacy` is for `manage.migrate_legacy`, whose source has no version record."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and not adopt_legacy:
        _refuse_foreign(conn)
    _sync_strategy_tables(conn)
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
                RT.backfill(conn)
            else:
                _apply(conn, MIGRATIONS[i])
            conn.execute(f"PRAGMA user_version={i + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            done()
    _keep_old_strategy_tables(conn)
    ensure_market_reads(conn)
    # Cloud added explicit room scopes after the original local schema. Keep old local
    # databases usable without relying on SQLite's unsupported ADD COLUMN IF NOT EXISTS.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "scope" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN scope TEXT NOT NULL DEFAULT 'direct'")
    if "owner_actor" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN owner_actor TEXT")
    if "room_key" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN room_key TEXT")
    # Deleted tasks wait here until restored or purged (backend/task_delete.py).
    from .task_delete import ensure as ensure_task_trash
    ensure_task_trash(conn)
    # Milestone waits: a task waits for a pull request to merge or a release to be published (backend/milestone_waits.py).
    from .milestone_waits import ensure as ensure_milestone_waits
    ensure_milestone_waits(conn)
    # Reconnect cards: a task waits for a person to fix a missing or refused account (backend/reconnects.py).
    from .reconnects import ensure as ensure_reconnects
    ensure_reconnects(conn)
    # Recruiting seats: org-chart roles with no bot yet (backend/lifecycle.py).
    from .lifecycle import ensure as ensure_seats
    ensure_seats(conn)
    # Hourly and weekly spend limits, and limits on a group's or the whole team's combined spend (backend/usage_limits.py).
    from .usage_limits import ensure as ensure_usage_limits
    ensure_usage_limits(conn)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_personal_room "
                 "ON conversations(owner_actor,room_key) WHERE scope='personal' AND closed_at IS NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_shared_room "
                 "ON conversations(room_key) WHERE scope='shared' AND closed_at IS NULL")
    # What every bot may do with a type's tasks (TYPE_BOTS); NULL keeps them to the parties.
    add_column(conn, "task_types", "bots", "TEXT")
    # The tasks board columns. The cloud store backfills rank
    # and lane once (its migration 29); here the columns simply exist.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "lane" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN lane TEXT NOT NULL DEFAULT 'company'")
    if "rank" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN rank REAL")
    if "labels_json" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN labels_json TEXT NOT NULL DEFAULT '[]'")
    # Every task-to-task relationship; idempotent, so a database at any version catches up.
    TR.migrate(conn)
    # Who is on a task, by role (task_roles).
    TRo.migrate(conn)
    # Next-run tasks (`hub task create --next-run`): `next_run` marks a task that waits for its
    # owner's next run instead of starting one; `carried_by` is the attempt that took it there.
    if "next_run" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN next_run INTEGER NOT NULL DEFAULT 0")
    if "carried_by" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN carried_by TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_carried_by ON tasks(carried_by) WHERE carried_by IS NOT NULL")
    # When a run that carried a next-run task without doing it picks it up again (backend/quiet_work.py).
    add_column(conn, "tasks", "resume_json", "TEXT")
    # The due-date routine occurrence a task is for, "<automation_id>:<due_on>" (backend/automation_actions.py).
    add_column(conn, "tasks", "occurrence", "TEXT")
    # The step of the routine that opened it: one task per occurrence per producing step (task_create).
    add_column(conn, "tasks", "occurrence_step", "TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS tasks_occurrence_step ON tasks(occurrence, occurrence_step) "
                 "WHERE occurrence IS NOT NULL")
    # The rule due date an occurrence's run was fired for (backend/rule_due.py marks it when the task completes).
    add_column(conn, "tasks", "rule_due_id", "TEXT")
    task_columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "private" in task_columns:
        # The few private tasks, which every privacy-checked read loads (backend/privacy_index.py).
        conn.execute("CREATE INDEX IF NOT EXISTS tasks_private ON tasks(id) WHERE private IS NOT 0")
        if "guest" in task_columns:
            # That id-only index still fetches each private task's body. privacy_index reads only these
            # columns, and the predicate matches its query word for word, so the scan never opens a body.
            conn.execute("CREATE INDEX IF NOT EXISTS tasks_private_parties ON tasks("
                         "id, requester, owner, guest, private) WHERE private IS NOT 0")
            # A bot's private tasks for its status rows (task_privacy.status_tasks_sql) with the two columns it reads
            # beyond the parties: each private row was a body-page read before this.
            conn.execute("CREATE INDEX IF NOT EXISTS tasks_private_status ON tasks("
                         "id, requester, owner, guest, private, status, waiting_on) WHERE private IS NOT 0")
            # A task list's visibility and queue order, and a bot's status rows (task_privacy.status_tasks_sql), each
            # answered from an index alone: the table's pages hold every body, and these reads need none of them.
            conn.execute("CREATE INDEX IF NOT EXISTS tasks_queue_cover ON tasks("
                         "rank, created, id, owner, requester, private, guest)")
            conn.execute("CREATE INDEX IF NOT EXISTS tasks_owner_status_cover ON tasks("
                         "owner, status, id, requester, private, guest, waiting_on)")
            conn.execute("CREATE INDEX IF NOT EXISTS tasks_requester_status_cover ON tasks("
                         "requester, status, id, owner, private, guest, waiting_on)")
    # privacy_index.conversation_tasks is `SELECT id, task_id FROM conversations`, on every task list.
    # The conversation row holds participants_json, and the primary key scan reads those pages.
    conn.execute("CREATE INDEX IF NOT EXISTS conversations_id_task ON conversations(id, task_id)")
    # Status and provenance reads stay indexed as question and task history grows.
    conn.execute("CREATE INDEX IF NOT EXISTS messages_reply_kind ON messages(in_reply_to, kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS messages_from_kind ON messages(from_actor, kind)")
    # A task comment its author changed or took back (task_comment_edit, task_comment_delete). Checked on
    # every start rather than numbered, so no migration number collides with another branch's.
    add_column(conn, "messages", "edited_at", "TEXT")
    add_column(conn, "messages", "deleted_at", "TEXT")
    # An ask that a later ask by the same bot on the same task, or the task's end, made moot (supersede_asks).
    add_column(conn, "messages", "superseded_at", "TEXT")
    # Where a task stood after its last completed turn (execution.complete), so a fresh session picks
    # it up from there; and when it was last asked "Still wanted?" (backend/still_wanted.py).
    add_column(conn, "tasks", "checkpoint", "TEXT")
    add_column(conn, "tasks", "checkpoint_at", "TEXT")
    add_column(conn, "tasks", "checkpoint_by", "TEXT")
    # Where the task stood by the others' changes when that checkpoint was left (wake_rules.state_of, token saving 3/9).
    add_column(conn, "tasks", "checkpoint_state", "TEXT")
    add_column(conn, "tasks", "stale_asked_at", "TEXT")
    # Set once a turn in the task's own session took in another private task: that session may
    # carry it from then on, so no later turn's reply becomes the checkpoint (execution.complete).
    add_column(conn, "tasks", "checkpoint_blocked", "INTEGER NOT NULL DEFAULT 0")
    # A task snoozed until a date, and who snoozed it (backend/task_snooze.py).
    add_column(conn, "tasks", "snoozed_until", "TEXT")
    add_column(conn, "tasks", "snoozed_by", "TEXT")
    # What the snoozer wants checked when the snooze ends, shown in the wake (backend/task_snooze.py).
    add_column(conn, "tasks", "snoozed_note", "TEXT")
    # A review or QA child's verdict (approve|changes|pass|fail) and the pull request head it judged (record_verdict).
    add_column(conn, "tasks", "verdict", "TEXT")
    add_column(conn, "tasks", "verdict_head", "TEXT")
    # An approval a person's own Assistant decided for them: whose assistant, and the person's words it acted on.
    add_column(conn, "approvals", "decided_via", "TEXT")
    add_column(conn, "approvals", "decided_for_json", "TEXT")
    # When an approval's exact payload passed its checks and could be decided, and whose decision it is (the person it
    # was sent to). Older rows read their request time and their message's recipient (approval_wait).
    add_column(conn, "approvals", "ready_since", "TEXT")
    add_column(conn, "approvals", "decision_owner", "TEXT")
    # The Tico account a person signed in with (backend/oidc.py person): the issuer and its subject id. An install
    # matches the account id before the email, so a changed address keeps the same person. NULL until their first
    # sign-in with an account; one account is one person.
    add_column(conn, "humans", "idp_issuer", "TEXT")
    add_column(conn, "humans", "idp_sub", "TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS humans_idp ON humans(idp_issuer, idp_sub) WHERE idp_sub IS NOT NULL")
    # 1 when a run segment's estimate may be low: its model's long prompts cost more and the runner did not
    # send every request's tokens (providers.estimate). NULL on segments from before it was recorded.
    add_column(conn, "turn_usage_segments", "est_cost_low", "INTEGER")
    # On a Tico AI Gateway segment, which gateway key paid for it (backend/gateway_connection.py `key_id`, never the key)
    # and this install's id (backend/census.py), so the Gateway's own ledger can be matched. NULL on every other one.
    add_column(conn, "turn_usage_segments", "gateway_key_id", "TEXT")
    add_column(conn, "turn_usage_segments", "install_id", "TEXT")
    # The newest of the bot's own model requests' whole prompt (uncached + cached), when its runner marked them from
    # its subagents': the session's context size (backend/topic_change.py). NULL when it did not; the summed columns
    # are not a context size.
    add_column(conn, "turns", "prompt_tokens", "INTEGER")
    # Why a finished run's usage reads 0 tokens or no cost (USAGE_REASONS, turn_finish): null when its runner counted
    # its tokens and the model has a price. NULL throughout on a run finished before it was recorded.
    add_column(conn, "turns", "usage_reason", "TEXT")
    # The wake rule that started the run (backend/rules.py): the message's `refs.rule`, copied at claim. NULL on a run
    # from before rules, or whose message carried none (`rule_weekly` in hub sql shows those as 'unknown').
    add_column(conn, "turns", "rule_id", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_snoozed ON tasks(snoozed_until) WHERE snoozed_until IS NOT NULL")
    # A type whose tasks change status only by moving between its steps (`_task_state`).
    add_column(conn, "task_types", "steps_only", "INTEGER NOT NULL DEFAULT 0")
    # The Product roadmap and Dev tickets types are found by their role, never by who manages them.
    from .roadmap_flow import ensure as ensure_roadmap_types, migrate as migrate_roadmap_roles
    from . import task_blocked, blocker_asks
    task_blocked.ensure(conn)
    blocker_asks.ensure(conn)
    migrate_roadmap_roles(conn)
    if "manager" not in {r[1] for r in conn.execute("PRAGMA table_info(task_types)")}:
        add_column(conn, "task_types", "manager", "TEXT")
        ensure_roadmap_types(conn)
    from .roadmaps import migrate as migrate_roadmaps
    migrate_roadmaps(conn)
    from .roadmap_flow import ready_step as roadmap_ready_step
    roadmap_ready_step(conn)
    # Won't do is no longer a board step (`_task_state`): move its tasks off, then delete it.
    retire_held_steps(conn)
    task_blocked.stamp_builds(conn)
    task_blocked.backfill(conn)
    task_blocked.forget_retired_resumes(conn)
    task_blocked.leave_retired_blocked(conn)
    # A person's open asks, read on every Needs-you load (loose_asks_to).
    conn.execute("CREATE INDEX IF NOT EXISTS messages_open_asks_to ON messages(to_actor, created) "
                 "WHERE kind='ask' AND answered_by IS NULL AND deleted_at IS NULL")
    # A bot's unread backlog, read newest first a page at a time on every inbox read (inbox_pages).
    conn.execute(UNREAD_INDEX)
    conn.execute("CREATE INDEX IF NOT EXISTS messages_pending_conversation ON messages(conversation_id) "
                 "WHERE kind='ask' AND answered_by IS NULL AND deleted_at IS NULL")
    # The keeper's meeting inputs, the few messages a room's meeting provenance is read from (live_privacy.Sources) on
    # nearly every privacy check: the room index read every message of the room for them, millions of times a week.
    conn.execute("CREATE INDEX IF NOT EXISTS messages_live_meeting_input ON messages(conversation_id, created) "
                 "WHERE json_extract(refs_json,'$.live_meeting.turn_id') IS NOT NULL")
    # The questions a comment answered, reopened when it is deleted (`task_comment_delete`, inside the write
    # transaction): without it that lookup read every message.
    conn.execute("CREATE INDEX IF NOT EXISTS messages_answered_by ON messages(answered_by) WHERE answered_by IS NOT NULL")
    # The media check visits blobs often; an unindexed version lookup multiplies both histories.
    conn.execute("CREATE INDEX IF NOT EXISTS bot_file_versions_blob_media ON bot_file_versions(blob_id,media_state)")
    # The media poll's pending rows, a handful even when blobs number in the tens of thousands.
    conn.execute("CREATE INDEX IF NOT EXISTS blob_media_pending ON blob_media(blob_id) WHERE media_state='pending'")
    conn.execute("CREATE INDEX IF NOT EXISTS bot_file_versions_pending ON bot_file_versions(blob_id) "
                 "WHERE media_state='pending'")
    # Quiet notes (`hub note`): a line left for a bot's next run, asking nothing. `carried_by` is
    # the attempt that took it there; a cancelled note never goes.
    conn.execute("CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, from_actor TEXT NOT NULL, "
                 "to_actor TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL, carried_by TEXT, "
                 "cancelled_at TEXT, cancelled_by TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS notes_to_created ON notes(to_actor, created)")
    # A bot-to-bot message that could wait (backend/bot_to_bot.py) rides to the next run as a note linked to it.
    add_column(conn, "notes", "message_id", "TEXT")
    # `settled_at`: the carrying run completed, so the note is read for good. It only narrows the waiting scan to
    # recent notes; NOTE_WAITING_SQL still asks the carrier, so a note no path settled is merely slower, never lost.
    # Both indexes hold unsettled rows only, so they stay small however many notes a bot has been sent.
    add_column(conn, "notes", "settled_at", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS notes_waiting ON notes(to_actor, created, id) "
                 "WHERE cancelled_at IS NULL AND settled_at IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS notes_unsettled_carried ON notes(carried_by) "
                 "WHERE carried_by IS NOT NULL AND settled_at IS NULL")
    # A deferred item and its deadline (backend/delivery.py). No foreign keys: a row outlives a deleted task or
    # message and is settled `moot` by the next pass. `conversation_id` '' is the bot's next turn of any kind.
    conn.execute("CREATE TABLE IF NOT EXISTS deferrals(id TEXT PRIMARY KEY, bot TEXT NOT NULL, kind TEXT NOT NULL, "
                 "item TEXT NOT NULL, conversation_id TEXT NOT NULL DEFAULT '', task_id TEXT, due TEXT, "
                 "created TEXT NOT NULL, fired_job TEXT, settled TEXT, how TEXT, attempt TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS deferrals_waiting ON deferrals(bot, conversation_id) WHERE settled IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS deferrals_due ON deferrals(due) WHERE settled IS NULL AND due IS NOT NULL")
    # The hot queries in backend/delivery.py: a claim finds its carried rows by attempt, a run's end finds the deadline
    # runs still open by fired_job, and a stalled task checks for a deferral by item. Settled rows stay (no pruning yet),
    # so only the attempt index is not partial to unsettled rows.
    conn.execute("CREATE INDEX IF NOT EXISTS deferrals_attempt ON deferrals(attempt) WHERE attempt IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS deferrals_fired ON deferrals(fired_job) WHERE settled IS NULL AND fired_job IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS deferrals_item ON deferrals(item, kind) WHERE settled IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_owner_rank ON tasks(owner, rank)")
    # Health's waits-on-a-person check (backend/health.py silent_waits) reads only these few rows.
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_waiting_on ON tasks(waiting_on) WHERE waiting_on IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_lane_status_rank ON tasks(lane,status,rank,created)")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_active_lane_rank ON tasks("
                 "lane,(rank IS NULL),rank,created,id) WHERE status IN "
                 "('open','doing','waiting','review','ready','declined')")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_finished_lane_time ON tasks("
                 "lane,COALESCE(closed_at,done_at,updated,created) DESC,id DESC) "
                 "WHERE status IN ('done','closed')")
    conn.execute(TASKS_DUE_INDEX)                  # the scheduler's due-date reminder pass
    conn.execute(TASKS_RESUME_INDEX)               # ...and its quiet tasks whose deferred time came (quiet_work.due)
    # A task's history, and the Tasks page's stats: how many tasks reached each status, by day.
    conn.execute("CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task_id, ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS task_events_field_ts ON task_events(field, ts)")
    # One task's history of one field (status, owner, waiting_on): without it, and without ANALYZE, SQLite picks
    # task_events_field_ts and reads that field's changes on every task.
    conn.execute("CREATE INDEX IF NOT EXISTS task_events_task_field ON task_events(task_id, field, ts)")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_occurrences'").fetchone():
        conn.execute("CREATE INDEX IF NOT EXISTS schedule_occurrences_task ON schedule_occurrences(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS events_task_origin ON events(actor,action,target,ts DESC)")
    # A tag's attach events (the tag list, privacy_index.tags): only ~6k rows of a 2M-row events table, read with
    # their detail. Partial and covering, so the list's IN(...) reads a few dense pages, not one row per event.
    conn.execute("CREATE INDEX IF NOT EXISTS events_tag_attach ON events(target, detail_json) WHERE action='tag.attach'")
    conn.execute("CREATE TABLE IF NOT EXISTS task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), "
                 "kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created TEXT NOT NULL, "
                 "pr_sha TEXT, pr_merged_at TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS task_links_task ON task_links(task_id)")
    # A computer's worktrees (worktrees.inventory, on every heartbeat): a seek to its rows, not a scan of every
    # worktree link ever kept. Partial, so it holds worktree rows only.
    conn.execute("CREATE INDEX IF NOT EXISTS task_links_worktree_computer ON task_links(computer_id) WHERE kind='worktree'")
    # The Overview's bot hours (backend/bot_hours.py) read the runs still going or finished inside its weeks,
    # however long ago they started.
    conn.execute("CREATE INDEX IF NOT EXISTS turns_finished ON turns(finished)")
    # Each bot's last finished run, for the bot list (views.employee_rows): one seek per bot, not a sort of all runs.
    conn.execute("CREATE INDEX IF NOT EXISTS turns_bot_finished ON turns(bot, finished, id) WHERE finished IS NOT NULL")
    conn.execute("CREATE TABLE IF NOT EXISTS preferences(actor TEXT NOT NULL, key TEXT NOT NULL, "
                 "value_json TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(actor, key))")
    # Which messages could be about a task, so a task's page does not read its whole room (backend/message_links.py).
    from . import message_links
    message_links.ensure(conn)
    clear_finished_close_times(conn)
    return len(MIGRATIONS)


def clear_finished_close_times(conn):
    """A done or declined task that kept the close time of an earlier close (a move out of closed left it before
    task_update cleared it) never closes again: the close rule reads closed_at IS NULL. Clear it, on the record, once;
    nothing writes such a row any more, so a later start finds none. Returns the ids it cleared."""
    rows = _rows(conn.execute("SELECT id, closed_at, closed_by FROM tasks "
                              "WHERE status IN ('done','declined') AND closed_at IS NOT NULL"))
    for row in rows:
        conn.execute("UPDATE tasks SET closed_at=NULL, closed_by=NULL WHERE id=?", (row["id"],))
        for field in ("closed_at", "closed_by"):
            if row[field] is not None:
                _task_event(conn, row["id"], KEEPER, field, row[field], None, "It is no longer closed")
    return [row["id"] for row in rows]


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

PRIVATE_WRITE = contextvars.ContextVar("hub_private_write", default=False)
# The run (attempt id) whose token made this request (backend/app.py request_guard): each write it logs is counted on
# that run's `turns.writes`, which says whether the run acted (backend/run_triggers.py).
RUN = contextvars.ContextVar("hub_run", default="")
# The bulk change (backend/app.py task_bulk) a refusal came from: each task it skipped keeps its own refusal on record,
# but one call counts once toward rule 8's quarantine, however many tasks it skipped (_escalate).
BULK_CALL = contextvars.ContextVar("hub_bulk_call", default="")


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
            target = resolve_actor(conn, values.get('owner'))
            parent = task(conn, values.get('parent_id')) if values.get('parent_id') else None
            private = bool((values.get('private') if values.get('private') is not None else
                            private_tasks_default(conn, actor) or private_tasks_default(conn, target))
                           or parent and task_private(conn, parent))
        token = PRIVATE_WRITE.set(PRIVATE_WRITE.get() or private)
        from . import task_trigger_rules
        try:
            with task_trigger_rules.pending(conn):
                result = fn(*args, **kwargs)
            # Actions may have changed the task after the original write completed.
            if fn.__name__ in ('task_create', 'task_update', 'task_close') and isinstance(result, dict) and result.get('id'):
                result = {**result, **(task(conn, result['id']) or {})}
            return result
        except Refused as exc:
            exc.private = PRIVATE_WRITE.get()
            raise
        finally:
            PRIVATE_WRITE.reset(token)
    return guarded


def event(conn, actor, action, target="", detail=None):
    """Append the audit row every write leaves behind."""
    if action == "approval.consume" and RUN.get():
        detail = {**(detail or {}), "attempt_id": RUN.get()}
    if VIA.get() and (detail is None or isinstance(detail, dict)):
        detail = {**(detail or {}), "via": VIA.get()}
        if VIA.get() in ("botops", "assistant") and DELEGATION.get():
            detail["delegation"] = DELEGATION.get()
    row = {"id": new_id(), "ts": now(), "actor": str(actor), "action": action,
           "target": str(target or ""), "detail_json": _dump(detail)}
    conn.execute("INSERT INTO events (id, ts, actor, action, target, detail_json) "
                 "VALUES (:id, :ts, :actor, :action, :target, :detail_json)", row)
    if RUN.get() and RT.counts(action):
        conn.execute("UPDATE turns SET writes=coalesce(writes,0)+1 WHERE id=?", (RUN.get(),))
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
    try:
        from . import webhooks
        if webhooks.listening(conn):
            webhooks.publish(conn, type, subject, data(), actor, revision=revision)
    except Exception:
        logging.getLogger(__name__).warning("webhook event %s was not queued", type, exc_info=True)
    try:
        from . import automations
        automations.on_event(conn, type, subject, data, actor)
    except Exception:
        logging.getLogger(__name__).warning("automations did not hear %s", type, exc_info=True)


def _task_facts(conn, task_id, actor, **more):
    row = task(conn, task_id) or {}
    return {"id": task_id, "title": row.get("title"), "status": row.get("status"), "step": row.get("step_id"),
            "owner": row.get("owner"), "requester": row.get("requester"), "actor": actor, **more}


def writing_refusal(rule, detail):
    """Only diagnostics produced by human-item formatting, never approval validation."""
    return rule == "lint" and bool(re.match(
        r'^(give it a title|start the title with a verb|internal codes|internal jargon|'
        r'write a concrete human decision|write the ask in the first line|\d+ words outside the quoted draft)', detail))


def refuse(conn, actor, rule, detail="", severity="normal", context=None, task=None):
    """Record a refusal, escalate it (rule 8), and raise. Never returns. `context` is what a person
    reviewing the refusal reads; a private task write keeps only what was refused. `task` is the task
    a `label` refusal was about, which _escalate counts it by."""
    recorded = "Private task write refused" if PRIVATE_WRITE.get() else detail
    if context and PRIVATE_WRITE.get():
        context = {"what": context.get("what")}
    row = {"id": new_id(), "ts": now(), "actor": str(actor), "rule": rule,
           "detail_json": _dump({"detail": recorded, **({"context": context} if context else {}),
                                 **({"task": task} if task else {}),
                                 **({"bulk": BULK_CALL.get()} if BULK_CALL.get() else {})}), "severity": severity}
    conn.execute("INSERT INTO refusals (id, ts, actor, rule, detail_json, severity) "
                 "VALUES (:id, :ts, :actor, :rule, :detail_json, :severity)", row)
    event(conn, actor, "refused", rule, {"detail": recorded, "severity": severity})
    if rule != "quarantined":
        try:
            _escalate(conn, actor, rule, recorded, severity, row["ts"], context)
        except Exception:       # an escalation must never hide the refusal it came from
            pass
    if writing_refusal(rule, detail):
        detail += ". Rewrite the title/body to fix these writing errors and retry. Do not ask the human to waive formatting rules."
    raise Refused(rule, detail, severity, task)


# ----------------------------------------------------------------------------- lint (rule 7)
LINT_MAX_WORDS = 120
# `status: doing`, `owner:bot:cmo`. A colon that opens a plain clause ("the order status: it says pending") is prose.
LINT_CODES = re.compile(r"\b(owner|status)\s*:\s*(?!(?:it|this|that|the|an?|there|they|we|you|i)\b)[\w:./-]+", re.I)
# Plain-English titles: an identifier is a bare
# reference number - `DC040302`, `03QEZT3C`, `#18823` - that belongs in the body or a link.
# Dates, versions (3.5) and short model names are not identifiers.
LINT_IDENT = re.compile(r"(?<![\w/.-])(?:#\d{3,}|[A-Z]{1,4}-?\d{4,}|\d{6,}|(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,})(?![\w/.-])")
LINT_CAPS = re.compile(r"\b[A-Z]{4,}\b")
LINT_CAPS_OK = {"HTML", "JSON", "YAML", "HTTP", "HTTPS", "README", "TODO", "HEAD", "MAIN",
                "AWS", "CSV", "PDF", "SAAS", "OKR", "OKRS", "CEO", "COO", "CTO", "CPO", "CMO", "CRO",
                "SEO", "ASAP", "USA", "NPS", "CRM", "SQL", "URL", "API", "MCP", "AI", "PR", "PRS"}
LINT_JARGON = re.compile(r"\b(keeper|GEO citation|routine \d|class:\s*\w+/\w+|paste-ready|see spec)\b", re.I)
# Imperative openers a human item may start with. The shape is policies/handoffs.md's
# "Asking the owner for anything": the ask first, in a verb. The list is generous on purpose:
# refusing a real verb ("Connect", "Authorize") costs a bot a turn, while letting through an
# unlisted verb costs nothing. `_is_verb_opener` refuses only what is clearly not a verb.
LINT_VERBS = {
    "accept", "access", "acknowledge", "activate", "add", "adjust", "advise", "agree", "align", "allocate", "allow",
    "analyse", "analyze", "announce", "answer", "appoint", "approve", "archive", "arrange", "ask", "assign",
    "attach", "audit", "authenticate", "authorise", "authorize", "back", "backfill", "backup", "ban", "block",
    "book", "boost", "bring", "browse", "budget", "build", "buy", "call", "cancel", "capture", "certify", "change",
    "check", "choose", "claim", "clarify", "clear", "click", "close", "collect", "comment", "commit",
    "compare", "complete", "configure", "confirm", "connect", "consider", "contact", "continue", "convert",
    "coordinate", "copy", "correct", "create", "cut", "debug", "decide", "decline", "define", "delegate", "delete",
    "deliver", "deploy", "describe", "designate", "disable", "disconnect", "dismiss", "document", "download",
    "draft", "drop", "edit", "email", "enable", "enroll", "enrol", "ensure", "enter", "escalate", "establish",
    "evaluate", "examine", "expand", "expedite", "explain", "export", "extend", "extract", "file", "fill", "find",
    "finalise", "finalize", "finish", "fix", "flag", "follow", "forward", "freeze", "generate", "get", "give", "grant",
    "handle", "help", "hire", "hold", "identify", "implement", "import", "improve", "inspect", "install",
    "instruct", "introduce", "investigate", "invite", "issue", "join", "keep", "kick", "launch", "leave", "let",
    "link", "list", "load", "locate", "log", "look", "make", "map", "mark", "match", "meet", "merge", "migrate",
    "monitor", "move", "name", "negotiate", "nominate", "note", "notify", "obtain", "onboard", "open", "opt",
    "order", "outline", "own", "pause", "pay", "pick", "pin", "ping", "plan", "post", "prepare", "present",
    "prioritise", "prioritize", "proceed", "process", "procure", "promote", "propose", "provide", "provision",
    "publish", "pull", "purchase", "push", "put", "quote", "raise", "rank", "rate", "read", "reassign", "rebuild",
    "recommend", "reconcile", "record", "recover", "reduce", "refresh", "refund", "register", "reject", "release",
    "reload", "remind", "remove", "rename", "renew", "reopen", "reorder", "replace", "reply", "report",
    "request", "require", "reschedule", "reset", "resolve", "respond", "restart", "restore", "restrict", "resume",
    "retire", "retry", "return", "review", "revise", "revoke", "roll", "rotate", "route", "run", "sanity-check",
    "save", "schedule", "scope", "screen", "search", "secure", "select", "send", "set", "settle", "share", "sign",
    "sign-off", "simplify", "skip", "snooze", "sort", "specify", "split", "spot-check", "start", "stop", "submit",
    "subscribe", "suggest", "supply", "supervise", "suspend", "swap", "switch", "sync", "take", "talk", "tell",
    "test", "thank", "throttle", "tighten", "track", "train", "transfer", "translate", "triage", "trim", "try",
    "turn", "unblock", "undo", "unpause", "unsubscribe", "update", "upgrade", "upload", "use", "validate",
    "verify", "view", "visit", "vote", "wait", "waive", "walk", "watch", "weigh", "whitelist", "wire", "write",
}
# Openers that are clearly not verbs: articles, pronouns, prepositions, conjunctions, and the
# labels and status words people put where the verb goes ("Needs you: ...", "New: ...", "Urgent").
LINT_NOT_VERBS = {
    "a", "an", "the", "this", "that", "these", "those", "my", "our", "your", "their", "his", "her", "its",
    "i", "we", "you", "they", "he", "she", "it", "there", "here", "who", "what", "when", "where", "why", "how",
    "which", "and", "or", "but", "if", "so", "then", "of", "for", "to", "in", "on", "at", "by", "with", "from",
    "about", "re", "fwd", "fw", "new", "urgent", "important", "fyi", "todo", "task", "blocked",
    "needs", "need", "needed", "waiting", "pending", "done", "ready", "final", "action", "decision",
    "question", "approval", "reminder", "status", "is", "are", "was", "were", "be",
    "can", "could", "should", "would", "will", "may", "might", "must", "not", "no", "yes", "please",
}


def _is_verb_opener(head):
    """True when a title's first word can start an imperative. Only what is clearly not a verb is refused."""
    first = re.split(r"[\s,;]+", head.strip(), 1)[0]
    word = first.strip("\"'`*_-()[]").lower()
    if not word:
        return False
    label = first.rstrip("*_`\"'").endswith(":") and not word.rstrip(":") in LINT_VERBS
    word = word.rstrip(":")
    if word in LINT_VERBS:
        return True
    if label or word in LINT_NOT_VERBS or any(ch.isdigit() for ch in word):
        return False
    letters = re.sub(r"[^A-Za-z]", "", first)
    if len(letters) >= 2 and letters.isupper():      # NEEDS-YOU, URGENT, PR: a label, not a verb
        return False
    if len(word) > 5 and word.endswith("ing"):       # Reviewing X: a gerund, not the ask
        return False
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) \
            and (word[:-1] in LINT_VERBS or word[:-2] in LINT_VERBS):   # Needs, Reviews: third person, not imperative
        return False
    if "-" in word and word.split("-")[0] in LINT_NOT_VERBS:            # Needs-you
        return False
    return True                                       # an unlisted word may still be a verb


def _first_line(text):
    return next((l.strip() for l in str(text or "").strip().splitlines() if l.strip()), "")


def _outside_quotes(text):
    """The text a human actually has to read: quoted drafts and URLs do not count."""
    kept = "\n".join(l for l in str(text or "").splitlines() if not l.lstrip().startswith(">"))
    return re.sub(r"https?://\S+", "", kept)


def lint_human_item(text, title=None):
    """Rule 7. Return the problems with something addressed to a human; empty means it passes.

    The same shape as the legacy Issue dispatcher's `lint_request`, moved here so the server and the
    CLI share one check: the ask in the first line, a verb at the front of a title, under
    120 words outside quoted drafts, no `owner:`/`status:` codes.
    """
    problems = []
    body = str(text or "")
    if "mistake to fix or a limit to keep" in body or "touches something you care about" in body:
        problems.append("write a concrete human decision, recommendation, and next step; do not forward a refusal diagnostic")
    if title is not None:
        problems += lint_human_title(title)
    if not _first_line(body):
        problems.append("write the ask in the first line, with your recommendation")
    outside = _outside_quotes(body)
    n = len(outside.split())
    if n > LINT_MAX_WORDS:
        problems.append(f"{n} words outside the quoted draft; keep it under {LINT_MAX_WORDS}")
    m = LINT_CODES.search(body)
    if m:
        problems.append(f'internal codes: "{m.group(0)}"')
    m = LINT_JARGON.search(body)
    if m:
        problems.append(f'internal jargon: "{m.group(0)}"')
    return problems


def lint_human_title(title):
    """Rule 7's title half: a verb at the front, no `owner:`/`status:` codes. What a renamed task
    for a person is checked on, since its body was checked when it was filed."""
    head = str(title or "").strip()
    if not head:
        return ["give it a title that says what you are asking for"]
    problems = []
    if not _is_verb_opener(head):
        problems.append(f'start the title with a verb (it starts "{head.split()[0]}")')
    m = LINT_CODES.search(head) or LINT_JARGON.search(head)
    if m:
        problems.append(f'internal codes in the title: "{m.group(0)}"')
    return problems


def lint_title(title):
    """Plain English in a title a bot wrote: no reference numbers, no shouting. The problems,
    empty when it passes. Applied to every bot-written General task title, whoever the owner is."""
    head = str(title or "")
    problems = []
    m = LINT_IDENT.search(head)
    if m:
        problems.append(f'reference number in the title: "{m.group(0)}" - say what it is in plain words '
                        f'and put the number in the body or a link')
    caps = [w for w in LINT_CAPS.findall(head) if w not in LINT_CAPS_OK]
    if caps:
        problems.append(f'all-caps word in the title: "{caps[0]}" - write it in plain words')
    return problems


# A waiting note is put in front of the person as the ask: a task or message id (`db14c907`, a uuid) or a commit
# hash (7 to 40 hex characters) is the bot's own shorthand, and so is a page of status. A run of hex needs a digit
# and a letter, so a word ("deadbeef", "defaced") or a plain number is not an id. One may end a sentence.
LINT_HEX_ID = re.compile(r"(?<![\w/.-])(?=[0-9a-f-]*\d)(?=[0-9a-f-]*[a-f])"
                         r"(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){1,4}(?:[0-9a-f]{8})?|[0-9a-f]{7,40})(?![\w/-]|\.[\w/-])", re.I)
LINT_CURRENCY = "$£€¥"
# A year after a few letters (FY2026, CY2025, ES2022) or a standard's number (ISO27001) names a thing, not a record.
LINT_NAMED_NUMBER = re.compile(r"[A-Z]{1,4}-?(?:19|20)\d{2}|ISO-?\d+")
# A pull request is public and a person can look it up, so a line put in front of them may name one, in exactly the
# form "PR #1555". A bare "#1555" is still a reference number, also where it ends a sentence or follows a word
# ("Merge #1555.", "PR#1555", "#1555/files").
LINT_PR_REF = re.compile(r"(?<![\w#/.-])PR #\d+(?![\w#/-]|\.\w)")
LINT_BARE_REF = re.compile(r"(?<![&#/.-])#\d{3,}(?!\w)")
LINT_WAIT_WORDS = 40


def lint_waiting_note(note, person, asked=False):
    """The note on a task a bot sets waiting on a person (`--status waiting --on <person> --note`): the person reads
    it as what they are asked to do, so it is one plain request to them, not the bot's working notes. The problems,
    empty when it passes. No note passes only when an open question to them on the task already asks."""
    text, name = str(note or "").strip(), actor_id(person)
    if not text:
        return [] if asked else [f"say in one plain sentence what you need {name} to do (--note)"]
    problems = []
    # An amount ("$250000") is not a reference number, nor is a pull request named "PR #1555". Only here: a title
    # keeps LINT_IDENT as it is.
    unref = LINT_PR_REF.sub("PR", text)
    m = LINT_HEX_ID.search(unref) or next((i for i in LINT_IDENT.finditer(unref)
                                           if not (i.start() and unref[i.start() - 1] in LINT_CURRENCY)
                                           and not LINT_NAMED_NUMBER.fullmatch(i.group(0))), None) \
        or LINT_BARE_REF.search(unref)
    if m:
        problems.append(f'id in the note for {name}: "{m.group(0)}" - say what it is in plain words')
    n = len(text.split())
    sentences = len([s for s in re.split(r"(?<=[.!?;])\s+", text) if s.strip()])
    if n > LINT_WAIT_WORDS or sentences > 2:
        problems.append(f"the note for {name} reads as working notes ({n} words): write one plain sentence "
                        f"asking them for what you need, and keep your notes in a comment")
    m = LINT_CODES.search(text) or LINT_JARGON.search(text)
    if m:
        problems.append(f'internal jargon in the note for {name}: "{m.group(0)}"')
    return problems


# The ask a bot puts to a person, when it sets a task waiting on them or gives them a task: what their card leads
# with, read by someone busy. Unlike the warnings above it is always refused, whatever TICO_STYLE_LINT says: a card
# with no plain ask costs the person more than the bot's retry costs the bot. Paths, links to stored objects and
# tool or field names are the bot's own shorthand; an http(s) link is something a person can open, so it stays.
LINT_ASK_WORDS = 40
LINT_ASK_EXAMPLE = "Approve the video tool trial on Thursday? If yes, I'll send the invite."
LINT_S3 = re.compile(r"\bs3://\S*", re.I)
LINT_PATH = re.compile(r"(?<![\w:/.-])(?:~|\.{1,2})?/[\w.@-]+/[\w.@/-]*"                  # /Users/x, ~/x, ./x/y
                       r"|(?<![\w/.-])[\w.-]+(?:/[\w.-]+)+\.[A-Za-z][\w]{0,4}\b"              # reports/x.md
                       r"|\b(?!(?-i:[A-Z][\w-]*\.(?:js|ts))\b)[\w-]+\.(?:py|js|cjs|mjs|ts|tsx|md|json|ya?ml|toml|sh|sql|log|ipynb)\b",  # not Node.js
                       re.I)
LINT_TOOL = re.compile(r"\bhub\s+(?:task|message|question|approval|note|chat|doc|file|goal|kpi|bot|routine|update|tool|calendar|meeting|support|team|tag|human|group|conversation|whoami|sql)\b|(?<![\w-])--[a-z][\w-]*|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|`[^`]+`")


# A sentence ends at . ! or ? before a capital letter, a digit or a quote, so "3 p.m. on Friday" and "U.S. customers"
# stay one sentence; initials (U.S.), these short titles and months never end one, so "Dr. Smith" and "Oct. 20" do not.
LINT_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9])")
LINT_ABBREV = re.compile(r"(?:\b(?:[A-Za-z]\.){2,}|\b(?:Mr|Mrs|Ms|Dr|Prof|St|vs|etc|Inc|Ltd|Co|Jr|Sr|No|approx|e\.g|i\.e|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.)$")


def ask_sentences(text):
    """An ask's sentences, a stop after an abbreviation or initials not counting as an end."""
    parts, start = [], 0
    for m in LINT_SENTENCE_END.finditer(text):
        if not LINT_ABBREV.search(text[start:m.start()]):
            parts.append(text[start:m.start()])
            start = m.end()
    return [" ".join(s.split()) for s in [*parts, text[start:]] if s.strip()]


def lint_sentences(text):
    """How many sentences an ask has, not counting a stop after an abbreviation or initials as an end."""
    return len(ask_sentences(text))


def lint_person_ask(text):
    """The problems with an ask to a person, empty when it passes: there is one, it is one or two sentences of at
    most LINT_ASK_WORDS words, and it carries no ids, hashes, file paths, s3:// links, tool and field names or error
    output.
    Deterministic on purpose: the same words always pass or fail, so a bot can fix them in one retry."""
    text = str(text or "").strip()
    if not text:
        return ["is missing"]
    problems = []
    n = len(text.split())
    sentences = lint_sentences(text)
    if n > LINT_ASK_WORDS or sentences > 2:
        problems.append(f"is {n} words in {sentences} sentence{'s' if sentences != 1 else ''}")
    return problems + lint_plain(text)


# What a too-long ask is cut down to: the one sentence that asks for something, and the one after it that says what
# happens then ("If yes, I'll ...", "Then ...", "I will ..."). The rest is the bot's context and goes under Details.
# A sentence that answers the request ("If yes, ...") is the outcome over one that only opens like one ("Then the
# runner failed."), wherever it comes after the request.
LINT_IF_YES = re.compile(r"if (?:yes|so|approved|you (?:agree|approve|do|say yes))\b", re.I)
# A matching opener can still decline the request ("If you do not approve", "If you agree not to merge").
# Inspect only the condition: a negation in what the bot does afterward does not change the person's answer.
LINT_NEGATIVE_CONDITION = re.compile(r"\b(?:no|not|never)\b|\b\w+n['’]t\b", re.I)
LINT_OUTCOME = re.compile(rf"(?:{LINT_IF_YES.pattern}|if (?:not|no)\b|then\b|once\b"
                          r"|after (?:that|you|it)\b|i(?:'ll|\s+will|\s+can|\s+then)\b|we(?:'ll|\s+will)\b"
                          r"|th(?:at|is) (?:lets|unblocks|means)\b)", re.I)
LINT_REQUEST = re.compile(r"\b(?:please|(?:need|want|would like) you to|you (?:must|should|need to|have to))\b", re.I)
# Words that open a condition or a time ("If yes", "Once approved", "After you approve"): the clause after one leads.
LINT_LEAD_IN = {"if", "once", "after", "when", "whenever", "before", "as", "unless", "until", "since", "because",
                "while", "assuming", "given"}


def _asks_for_something(sentence):
    """A question, an indirect request, or an imperative clause, including one after a condition."""
    if sentence.rstrip("\"')] ").endswith("?"):
        return True
    # An outcome opener does not make the rest of its sentence safe to discard: "Once approved, please sign ..."
    # still asks for a second decision. Treat possible requests conservatively rather than silently losing them.
    if LINT_REQUEST.search(sentence):
        return True

    def first_word(clause):
        clause = re.sub(r"^(?:(?:and|please|also|then)\s+)+", "", clause.strip(), flags=re.I)
        return re.split(r"[\s,;:]+", clause, 1)[0].strip("\"'*_()").lower()

    if not LINT_OUTCOME.match(sentence):
        return any(first_word(clause) in LINT_VERBS for clause in re.split(r"[,;]\s*", sentence))
    # An outcome lists what happens ("If yes, I'll sign it, send it and close this task."), so only the clause that
    # leads each part, past its condition, can tell the person to do something ("Once approved, sign the contract.").
    for part in sentence.split(";"):
        for clause in part.split(","):
            word = first_word(clause)
            if word in LINT_VERBS:
                return True
            if word not in LINT_LEAD_IN:
                break
    return False


def person_ask_rewrite(text):
    """For an ask too long or with too many sentences: (rewrite, "") where the rewrite keeps its one request and what
    happens after, in one or two plain sentences of at most LINT_ASK_WORDS words, or ("", repair) saying exactly what
    to change when no shorter wording keeps the same decision. Deterministic, like lint_person_ask: the bot retries
    once with the rewrite and puts the other sentences under Details, so nothing is lost or decided for it."""
    sentences = ask_sentences(str(text or "").strip())
    # Every question and every request counts: an ask that asks one thing and requests another is two decisions,
    # and keeping only one of them would change what the person is asked.
    asks = [i for i, s in enumerate(sentences) if _asks_for_something(s)]
    if not asks:
        return "", ("none of its sentences asks for anything: open it with the one question or request, "
                    "then say what happens after")
    if len(asks) > 1:
        quoted = "; ".join(f'"{sentences[i]}"' for i in asks)
        return "", (f"it asks {len(asks)} things ({quoted}): keep the one decision this ask is for in one sentence, "
                    "ask any other separately, and put the reasons in the details")
    i = asks[0]
    after = [s for s in sentences[i + 1:] if LINT_OUTCOME.match(s)]
    outcome = next((s for s in after if LINT_IF_YES.match(s)
                    and not LINT_NEGATIVE_CONDITION.search(re.split(r"[,;:]", s, 1)[0])),
                   after[0] if after else None)
    kept = [sentences[i], *([outcome] if outcome else [])]
    rewrite = " ".join(kept)
    n = len(rewrite.split())
    if n > LINT_ASK_WORDS:
        what = "its request and what happens after are" if outcome else "its request alone is"
        return "", (f'{what} {n} words: shorten "{rewrite}" to at most {LINT_ASK_WORDS} words, keeping what you '
                    "need and what happens after, and put the reasons in the details")
    unclear = lint_plain(rewrite)
    if unclear:
        return "", (f'its request "{rewrite}" {"; ".join(unclear)}: say that in plain words and put the reference '
                    "in the details")
    return rewrite, ""


# Error output a bot pasted: a traceback, an exception or error class name (bare or with its message), an exit code, an
# HTTP error status. "an error in the invoice" is prose; "TypeError", "ValueError: bad date" and "HTTP 502" are the
# bot's own record, so they go under Details.
LINT_ERROR = re.compile(r"\bTraceback\b|(?-i:\b[A-Z]\w*(?:Error|Exception)\b)|(?-i:\b(?:ERROR|FATAL|PANIC|Error|Fatal)\b)\s*:"
                        r"|\bexit(?:ed)?\s+(?:code|status)\s+-?\d+|\bHTTP\s*[45]\d\d\b|(?-i:\bE[A-Z]{3,}\b)(?=:)"
                        r"|\bstack ?trace\b", re.I)


def lint_plain(text):
    """What in `text` belongs under Details, not in front of a person, empty when nothing does: an s3:// link, an id
    or hash (a pull request named "PR #1555" is neither), a file path, a tool or field name, or error output. The part of the ask rules (lint_person_ask,
    lint_question) that holds for every line a card shows above Details."""
    text = str(text or "").strip()
    problems = []
    m = LINT_S3.search(text)
    if m:
        problems.append(f'has an s3:// link: "{m.group(0)}"')
    plain = LINT_PR_REF.sub("PR", LINT_S3.sub(" ", re.sub(r"https?://\S+", " ", text)))
    m = LINT_HEX_ID.search(plain) or next((i for i in LINT_IDENT.finditer(plain)
                                           if not (i.start() and plain[i.start() - 1] in LINT_CURRENCY)
                                           and not LINT_NAMED_NUMBER.fullmatch(i.group(0))), None) \
        or LINT_BARE_REF.search(plain)
    if m:
        problems.append(f'has an id or hash: "{m.group(0)}"')
    m = LINT_PATH.search(plain)
    if m:
        problems.append(f'has a file path: "{m.group(0)}"')
    m = LINT_TOOL.search(plain) or LINT_CODES.search(plain) or LINT_JARGON.search(plain)
    if m:
        problems.append(f'has a tool or field name: "{m.group(0)}"')
    m = LINT_ERROR.search(plain)
    if m:
        problems.append(f'has error output: "{m.group(0)}"')
    return problems


def person_ask_of_body(body, title=""):
    """The ask in a task given to a person: the body's first paragraph, after a first line that only repeats the
    title. The rest of the body is details."""
    lines = str(body or "").strip().splitlines()
    if lines and str(title or "").strip() and lines[0].strip() == str(title).strip():
        lines = lines[1:]
    return re.split(r"\n\s*\n", "\n".join(lines).strip(), 1)[0].strip()


def person_ask_refusal(person, text, where):
    """What a refusal of this ask says, or "" when it passes: what failed, what to do about it in this same turn, the
    rule and an example, so the bot fixes it in one retry. An ask refused for its length carries a rewrite to retry
    with (person_ask_rewrite), or, when none keeps the decision, the exact repair: a blocker is never left unasked
    for want of a shorter wording. `where` is "waiting" (the waiting note) or "assign" (the body of a task given to
    them)."""
    text = str(text or "").strip()
    problems = lint_person_ask(text)
    if not problems:
        return ""
    name = actor_id(person)
    place = ("the waiting note (--note)" if where == "waiting"
             else "the first paragraph of the task body (--body)")
    fix = ""
    if len(text.split()) > LINT_ASK_WORDS or lint_sentences(text) > 2:
        rewrite, repair = person_ask_rewrite(text)
        details = ("a comment on the task" if where == "waiting"
                   else "the body under it, after a blank line")
        fix = (f' Retry once with this ask, which keeps what you need and what happens after: "{rewrite}" Put the '
               f"rest of your text in {details}, so nothing is lost." if rewrite else
               f" No shorter wording keeps the same decision, so nothing was sent and the task is unchanged. To fix "
               f"it, {repair}; then retry once.")
    return (f"Your ask to {name} {'; '.join(problems)}.{fix} The rule: {place} is one or two plain sentences saying "
            f"what you need from {name} and what happens after, at most {LINT_ASK_WORDS} words, with no ids, hashes, "
            f"file paths, s3:// links, tool names or error output (a pull request may be named as \"PR #<number>\"); "
            f'put details in a comment or further down the body. Example: "{LINT_ASK_EXAMPLE}"')


def require_person_ask(conn, actor, person, text, where):
    """Refuse a bot's write that leaves `person` without a plain ask. A writing refusal (rule `lint`) is recorded
    but never counts toward a quarantine (_escalate): it is corrected by rewriting, not a sign the bot reaches
    where it should not."""
    detail = person_ask_refusal(person, text, where)
    if detail:
        refuse(conn, actor, "lint", detail)


# A question that lists its picks in its own words ("1. Calls ... 2. Pages ...", "(a) ... (b) ...") instead of as
# options: the card could only show them as text, with nothing to tick.
LINT_LISTED_PICKS = re.compile(r"(?:^|\s)(?:\(?[1-9][.)]|\([a-f]\)|[a-f]\))\s+\S.*?(?:^|\s)(?:\(?[2-9][.)]|\([b-f]\)|[b-f]\))\s+\S",
                               re.S)


def lint_question(question):
    """The problems with one question a bot puts to a person, as a card shows it (ui/app/question-card.js), empty
    when it passes. Its picks are options, each saying what happens when it is chosen (`then`); several picks
    (`multi`) mark the ones the bot recommends; and the question, its "why it matters" line and each `then` line
    carry nothing that belongs under Details (lint_plain), nor does any pick's label or description. A question with no options is a yes/no or a written
    answer, unless it lists picks in its words or is several picks. Deterministic, so a bot fixes it in one retry."""
    q = question if isinstance(question, dict) else question.model_dump()
    text, options, problems = str(q.get("question") or ""), q.get("options") or [], []
    for where, value in (("the question", text), ('the "why it matters" line (why)', q.get("why"))):
        problems += [f"{where} {p}" for p in lint_plain(value)]
    if not options and (q.get("multi") or LINT_LISTED_PICKS.search(text)):
        problems.append("lists picks but has no options: give each pick as an option")
    missing = [o.get("label") for o in options if not str(o.get("then") or "").strip()]
    if missing:
        problems.append(f'option{"s" if len(missing) > 1 else ""} {", ".join(chr(34) + str(m) + chr(34) for m in missing)} '
                        f'{"have" if len(missing) > 1 else "has"} no "what happens" line (then)')
    for o in options:
        # Every visible part of a pick sits above Details, so it keeps the same plain rule as the question.
        problems += [f'option "{o.get("label")}" {p}' for p in lint_plain(o.get("label"))]
        problems += [f'the description of "{o.get("label")}" {p}' for p in lint_plain(o.get("description"))]
        problems += [f'the "what happens" line of "{o.get("label")}" {p}' for p in lint_plain(o.get("then"))]
    if q.get("multi") and options and not any(o.get("recommended") for o in options):
        problems.append("is several picks with none marked recommended: mark the ones you recommend")
    return problems


QUESTION_EXAMPLE = ('{"question": "Website sales chat: go with these picks?", "why": "Engineering can\'t start until '
                    'these are set.", "multi": true, "options": [{"label": "Spend cap $10/day", "then": "Chat stops '
                    'for the day at $10", "recommended": true}, ...]}')


def question_refusal(person, questions):
    """What a refusal of these questions to `person` says, or "" when they pass: what failed, the rule and an example."""
    problems = []
    for q in questions:
        label = (q if isinstance(q, dict) else q.model_dump()).get("question") or ""
        problems += [f'"{str(label)[:60]}": {p}' for p in lint_question(q)]
    if not problems:
        return ""
    return (f"Your question to {actor_id(person)} can't be shown as a clear card: {'; '.join(problems)}. The rule: a "
            f"question with picks gives each pick as an option with a short \"what happens\" line (then); several "
            f"picks (multi) mark the recommended ones (recommended); the question, its why line and each pick's "
            f"label, description and then line have no ids, hashes, file paths, tool names or error output. Put "
            f"those in the task body or a comment, which the card keeps under Details. Example: {QUESTION_EXAMPLE}")


def require_plain_question(conn, actor, person, questions):
    """Refuse a bot's question to a person that breaks the asking rules (question_refusal). Like require_person_ask,
    a `lint` refusal is fixed by rewriting and never counts toward a quarantine."""
    if not is_bot(actor) or not is_human(person):
        return
    detail = question_refusal(person, questions)
    if detail:
        refuse(conn, actor, "lint", detail)


def lint_approval(kind, payload):
    """Rule 7 for an approval: the payload is the exact thing, so every field must be there."""
    problems = []
    fields = APPROVAL_FIELDS.get(kind, ())
    if not isinstance(payload, dict):
        return [f"{kind} needs a payload object with {', '.join(fields)}"]
    for f in fields:
        if f not in payload or payload[f] in (None, ""):
            if f == "cc" and "cc" in payload:
                continue                    # an empty Cc is a real answer
            problems.append(f"{kind} needs {f}")
    digest = payload.get("content_sha256")
    if kind == "publish" and digest not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        # Every publisher compares against hashlib's hexdigest(), so any other shape could never publish.
        problems.append("content_sha256 must be 64 lower-case hex characters")
    if kind == "publish" and "@" in url_authority(payload.get("url")):
        # A card cuts a long field, so "https://trusted.example…@other.example" could hide the real host.
        problems.append("url must not carry a user name (user@host); give the plain address")
    if kind == "support":
        problems += lint_support(payload)
    if kind == "merge" and "merge needs pr" in problems:
        # A merge approval is for one pull request; a change with none is a decision for a person.
        problems[problems.index("merge needs pr")] = ("merge needs pr (the pull request number); for a change "
                                                      "with no pull request, file a decision task for the person instead")
    return problems


URL_SPECIAL = ("http", "https", "ws", "wss", "ftp", "file")


def url_authority(url):
    """The part of a url where user@ would go, read as a browser reads it; "" when there is none. After a web scheme a
    browser skips any run of "/" and "\\", or none ("https:a@b" is user a at host b); with no scheme, a leading pair of
    them makes the link leave the app for another host. A backslash later counts as part of it, so "https://a\\@b" is
    refused rather than guessed at."""
    text = re.sub(r"[\s\x00-\x1f]", "", str(url or ""))
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", text)
    rest = text[m.end():] if m else text
    if m and m.group(1).lower() not in URL_SPECIAL and not rest.startswith("//"):
        return ""
    if not m and not re.match(r"[/\\]{2}", rest):
        return ""
    return re.split(r"[/?#]", rest.lstrip("/\\"), maxsplit=1)[0]


def lint_support(payload):
    """A support report is sent exactly as the person read it: the message already clean (support.clean_message would
    leave it unchanged), its SHA-256 matching, and nothing but the three keys."""
    from .support import CONTROL, MAX_MESSAGE
    problems = []
    extra = sorted(set(payload) - set(SUPPORT_APPROVAL_KEYS))
    if extra:
        problems.append(f"support takes only {', '.join(SUPPORT_APPROVAL_KEYS)}, not {', '.join(extra)}")
    message, digest = payload.get("message"), payload.get("content_sha256")
    if "include_ids" in payload and not isinstance(payload["include_ids"], bool):
        problems.append("include_ids must be true or false")
    if message in (None, ""):
        return problems
    if not isinstance(message, str):
        return problems + ["message must be a string"]
    if len(message) > MAX_MESSAGE:
        problems.append(f"message must be at most {MAX_MESSAGE} characters")
    if "\r" in message or CONTROL.search(message) or message != message.strip():
        problems.append("message must be plain text with no control characters and no leading or trailing blank space")
    if digest not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        problems.append("content_sha256 must be 64 lower-case hex characters")
    elif digest and not support_text_matches(payload):
        problems.append("the message doesn't match content_sha256; send the exact message that will be sent")
    return problems


def support_text_matches(payload):
    """A support payload's message is still the one its hash names (SHA-256 of its UTF-8 bytes)."""
    message, digest = payload.get("message"), payload.get("content_sha256")
    if not isinstance(message, str) or not isinstance(digest, str):
        return False
    try:
        return hashlib.sha256(message.encode("utf-8")).hexdigest() == digest
    except UnicodeEncodeError:
        return False


def publish_text_matches(payload):
    """A publish payload's `text` is what the person approves; `content_sha256` is what the publisher
    checks. They agree only when the hash is SHA-256 of the text's UTF-8 bytes (hq-tickets hashes the
    reply file's bytes and sends them decoded, so a UTF-8 file round-trips exactly)."""
    text, digest = payload.get("text"), payload.get("content_sha256")
    if not isinstance(text, str) or not isinstance(digest, str):
        return False
    try:
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:              # a lone surrogate is no text anyone could publish
        return False
    return actual == digest                 # exact: hq-tickets compares against a lower-case hexdigest


def style_problems(conn, actor, problems, severity="normal"):
    """Rule 7's writing problems as warnings: returned for the write to carry, so the bot does
    better next time, and refused only when TICO_STYLE_LINT=refuse."""
    if not problems or STYLE_LINT == "off":
        return []
    if STYLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(problems), severity)
    return list(problems)


def open_children(conn, task_id):
    """The titles of a task's unfinished subtasks, oldest first. A private subtask under a task
    that is not private is not named: the parent's readers may not read it."""
    parent_private = task_private(conn, task(conn, task_id))
    return [r["title"] if parent_private or not task_private(conn, r) else "a private subtask"
            for r in _rows(conn.execute(
                "SELECT * FROM tasks WHERE id IN (" + TR.CHILD_IDS + ") "
                "AND status NOT IN ('done','closed','declined') ORDER BY created,id", (task_id,)))]


def _clip(text, limit=180):
    """A refused body, safe to quote inside a task a human will read."""
    one = " ".join(str(text or "").split())[:limit]
    return LINT_JARGON.sub("[code]", LINT_CODES.sub("[code]", one))


# ----------------------------------------------------------------------------- severity (rule 8)
SECRETS_PATH = re.compile(r"(^|[\s\"'(/])secrets/", re.I)
OTHER_REPO = re.compile(r"\b(?:emp|bot)-[a-z0-9-]+/", re.I)      # a bot's repository: bot-<slug>, or emp-<slug> for an older bot


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
    repo = config.get("repo") or (row["repo"] if row else "") or "emp-" + slug
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
    r"send|email|mailbox|inbox|access|credential|credentials|token|password|api[_ -]?key)\b", re.I)


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
        event(conn, actor, "path.mentioned", task_id or "",
              {"what": what} if PRIVATE_WRITE.get() else {"what": what, "found": found})
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
    return out if len(out) <= ESCAPE_PREVIEW_MAX else out[:ESCAPE_PREVIEW_MAX - 1] + "…"


def _escalate(conn, actor, rule, detail, severity, ts, context=None):
    """Rule 8: count the day's refusals, open review tasks, quarantine when it is bad enough."""
    if not is_bot(actor):
        return
    # Capacity waiting is legitimate, and must never feed review, repeats or the daily backstop.
    # A writing correction ("start the title with a verb") is fixed by retrying, not a sign the bot
    # is reaching where it should not: it neither counts nor quarantines. A bot was quarantined by
    # ten refusals, three of them lint on one task title.
    if rule in ("lint", "queue_full"):
        return
    slug = actor_id(actor)
    day = ts[:10]
    # Counted since the day began or the last quarantine lifted, whichever is later.
    lifted = _one(conn, "SELECT max(ts) AS ts FROM events WHERE action='quarantine.lifted' AND target=?",
                  (bot_actor(slug),))
    since = max(day, (lifted or {}).get("ts") or "")
    counted, last, total, calls, again = [], {}, 0, set(), False
    for refused in _rows(conn.execute("SELECT rule, detail_json, ts FROM refusals WHERE actor=? AND ts>=? "
                                      "AND rule NOT IN ('lint','queue_full') ORDER BY ts, id", (actor, since))):
        facts = _json(refused["detail_json"], {}) or {}
        if refused["rule"] == "label":
            # A label refusal says who may make the change instead; retrying it is not reaching further, so
            # it counts once per task an hour (LABEL_REFUSAL_WINDOW_S). Nine in a minute quarantined a bot.
            about = facts.get("task")
            if about in last and parse_ts(refused["ts"]) - last[about] < timedelta(seconds=LABEL_REFUSAL_WINDOW_S):
                continue
            last[about] = parse_ts(refused["ts"])
        key = refused["detail_json"]
        if call := facts.get("bulk"):
            # One bulk change counts once, by its first counted refusal: a single call over 50 tasks the bot may not
            # change is one refused try, not 50. Its words count as a repeat of the same refusal made one by one.
            if call in calls:
                again = again or call == BULK_CALL.get()
                continue
            calls.add(call)
            key = _dump({k: v for k, v in facts.items() if k != "bulk"})
        total += 1
        # The target bot is paused, quarantined or planned: the sender broke nothing, and the same words come
        # back every time, so it opens no review and is no repeat. It still counts toward the day's backstop.
        if refused["rule"] != "target_inactive":
            counted.append((refused["rule"], key))
    if again:
        # This bulk change's first refusal already counted, reviewed and quarantined as need be.
        return
    count, distinct = len(counted), len(set(counted))
    if rule == "target_inactive":
        # A sender retrying it in a loop still meets the day's backstop.
        if total >= QUARANTINE_TOTAL_AT:
            quarantine(conn, slug, f"{total} refusals today", refusal={"rule": rule, "detail": _clip(detail)})
        return
    # Only a refusal the bot has already had (same rule, same words) counts toward the quarantine: retrying a
    # refused action is what the limit stops. A first try at something new that is refused is recorded, not counted.
    repeats = count - distinct
    said = _clip(detail)
    reviewer = bot_actor(REVIEW_OWNER if slug == FLEET_MAINTAINER else FLEET_MAINTAINER)
    if slug == FLEET_MAINTAINER and (bot(conn, REVIEW_OWNER) or {}).get("state") in (None, "planned", "archived"):
        # A company without an assistant has nobody to review BotOps: a person does.
        reviewer = human_actor(default_human(conn))
    if count >= REVIEW_AT and not _review_exists(conn, f"Review {slug}'s refused writes", day):
        _review_task(conn, reviewer, f"Review {slug}'s refused writes",
                     f"{count} writes from {slug} were turned down today. "
                     f"The last one broke the {rule} rule: {said}. "
                     f"Read its refusals and decide whether its playbook or its reach needs a change.", origin=actor)
    # Refusals are diagnostics for the bot, not decisions for the human.
    # Keep the audit, internal repeated-failure review, and quarantine enforcement.
    stopped = {"rule": rule, "detail": said}       # what the bot is told when it is back (quarantine_lifted_note)
    if repeats >= QUARANTINE_AT:
        quarantine(conn, slug, f"{repeats} repeated refusals today", refusal=stopped)
    elif total >= QUARANTINE_TOTAL_AT:
        # The backstop: refusals that each name a new target are no repeats, but this many still mean a loop.
        quarantine(conn, slug, f"{total} refusals today", refusal=stopped)


def _review_exists(conn, title, day):
    return conn.execute("SELECT 1 FROM tasks WHERE title=? AND requester=? AND substr(created,1,10)=?",
                        (title, KEEPER, day)).fetchone() is not None


def _review_task(conn, owner, title, body, origin=None):
    try:
        # lint=False: a person's review reads as written; the shape rule is for asks bots write.
        row = task_create(conn, KEEPER, title, body, owner, lint=False)
        if origin and is_bot(origin):
            event(conn, KEEPER, "task.origin", row["id"], {"bot": origin})
    except Refused:
        pass                                # a live duplicate is the once-a-day guard doing its job


QUARANTINE_COOLDOWN_S = 3600
COUNT_QUARANTINE = re.compile(r"\d+ (?:refused writes|repeated refusals|refusals) today")   # the first: before #287


# A bot's latest quarantine event, and when it was last quarantined: the statements the readers below run, named so a
# batch can seed them (prime_quarantines).
QUARANTINE_DETAIL_SQL = "SELECT detail_json FROM events WHERE action='quarantine' AND target=? ORDER BY ts DESC, rowid DESC LIMIT 1"
QUARANTINE_SINCE_SQL = "SELECT max(ts) AS ts FROM events WHERE action='quarantine' AND target=?"


def prime_quarantines(conn, slugs):
    """The latest quarantine event and last quarantine time of every bot in `slugs`, in two reads for all of them
    (per 500 bots): a loop of explanations then reads none of them again. Outside a snapshot nothing is kept."""
    from .read_cache import prime
    actors = sorted({bot_actor(s) for s in slugs if s})
    for part in chunks(actors):
        marks = ",".join("?" * len(part))
        latest = {r["target"]: r for r in conn.execute(
            "SELECT target, detail_json FROM (SELECT target, detail_json, row_number() OVER (PARTITION BY target "
            "ORDER BY ts DESC, rowid DESC) AS n FROM events WHERE action='quarantine' AND target IN (" + marks + ")) WHERE n=1", part)}
        since = {r["target"]: r["ts"] for r in conn.execute(
            "SELECT target, max(ts) AS ts FROM events WHERE action='quarantine' AND target IN (" + marks + ") GROUP BY target",
            part)}
        prime(conn, QUARANTINE_DETAIL_SQL, {(a,): latest.get(a) for a in part})
        prime(conn, QUARANTINE_SINCE_SQL, {(a,): {"ts": since.get(a)} for a in part})


def quarantine_reason(conn, slug):
    row = _one(conn, QUARANTINE_DETAIL_SQL, (bot_actor(slug),))
    return ((_json(row["detail_json"], {}) or {}) if row else {}).get("reason") or ""


def quarantine_is_escape(conn, slug):
    """Anything but a refusal-count quarantine is treated as an escape: a person clears it."""
    return not COUNT_QUARANTINE.fullmatch(quarantine_reason(conn, slug))


# A quarantine recorded before its refusal was kept: "escape: the task note reaches outside the hub: <words>".
LEGACY_ESCAPE = re.compile(r"escape: the (.+?) (?:payload )?reaches outside the hub: (.*)", re.S)


def quarantine_review(conn, slug):
    """What a person resuming an escape quarantine reads: what was being written (`what`), what the check
    matched (`found`), the refused words with credential values masked (`preview`) and the task (`task`)."""
    row = _one(conn, QUARANTINE_DETAIL_SQL, (bot_actor(slug),))
    detail = (_json(row["detail_json"], {}) or {}) if row else {}
    if detail.get("review"):
        return detail["review"]
    old = LEGACY_ESCAPE.fullmatch(detail.get("reason") or "")
    if not old:
        return None
    return {"what": old.group(1), "found": escape_found(old.group(2), bot_actor(slug), conn),
            "preview": escape_preview(old.group(2)), "task": None}


def quarantine_resumes(conn, slug):
    """Since when a bot is quarantined, whether it lifts by itself (a refusal count), and when (`resumes_at`)."""
    since = _one(conn, QUARANTINE_SINCE_SQL, (bot_actor(slug),))["ts"]
    auto = not quarantine_is_escape(conn, slug)
    return {"since": since, "auto": auto,
            "resumes_at": shift(since, seconds=QUARANTINE_COOLDOWN_S) if auto and since else None}


def lift_cooled_quarantines(conn, cooldown=QUARANTINE_COOLDOWN_S):
    """A refusal-count quarantine is a cooldown: after an hour the bot runs again, with its count
    started over. Runs from the scheduler's tick. Returns the bots let out."""
    cutoff = shift(now(), seconds=-cooldown)
    lifted = []
    for row in _rows(conn.execute("SELECT slug FROM bots WHERE state='quarantined'")):
        with isolated(conn, "lift_cooled_quarantines", row["slug"]):
            slug = row["slug"]
            since = _one(conn, "SELECT max(ts) AS ts FROM events WHERE action='quarantine' AND target=?",
                         (bot_actor(slug),))
            if quarantine_is_escape(conn, slug) or not since or not since["ts"] or since["ts"] > cutoff:
                continue
            status_set(conn, KEEPER, slug, state="active", reason="Cooldown over: the refusal count starts again.")
            lifted.append(slug)
    return lifted


def quarantine(conn, slug, reason, context=None, refusal=None):
    """Rule 8's stop: the bot writes nothing more until a human sets it active again. `context` is the
    refusal that stopped it, for the person who reviews it (`quarantine_review`); `refusal` ({rule, detail})
    is the last refused write of a refusal-count quarantine, for the bot when it is back."""
    conn.execute("UPDATE bots SET state='quarantined' WHERE slug=?", (slug,))
    event(conn, KEEPER, "quarantine", bot_actor(slug), {"reason": reason, **({"review": context} if context else {}),
                                                         **({"refusal": refusal} if refusal else {})})
    # The status line people read says what the check matched, not the rule's own words.
    focus = (f"Paused for review: the safety check stopped a {context['what']} naming {context['found']}"
             if context and context.get("found") else reason)
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
    detail = (_json(row["detail_json"], {}) or {}) if row else {}
    review, refused = quarantine_review(conn, slug) or {}, detail.get("refusal") or {}
    if review.get("what"):
        what = f"the safety check stopped a {review['what']}" + (f" naming {review['found']}" if review.get("found") else "")
    elif refused.get("detail"):
        what = f"{refused['detail']} ({refused.get('rule')})"
    else:
        what = detail.get("reason") or "a refused write"
    body = f"You were quarantined and are back. The refusal that stopped you: {what}. {QUARANTINE_LIFTED}"
    ident = new_id()
    conn.execute("INSERT INTO notes(id,from_actor,to_actor,body,created) VALUES(?,?,?,?,?)",
                 (ident, KEEPER, bot_actor(slug), body[:NOTE_MAX], now()))
    event(conn, KEEPER, "note.create", ident, {"to": bot_actor(slug), "quarantine": True})


# ----------------------------------------------------------------------------- identity
def issue_token(conn, slug):
    """A fresh per-thread token for a bot; only its sha256 is stored. Returns the token."""
    row = bot(conn, slug)
    if not row:
        raise Refused("not-found", f"no bot {slug}")
    token = secrets.token_urlsafe(32)
    conn.execute("UPDATE bots SET token_hash=? WHERE slug=?", (token_hash(token), slug))
    event(conn, KEEPER, "token.issue", bot_actor(slug))
    return token


def verify_bot(conn, slug, token):
    """The bot row when the token matches what the keeper issued; else `Refused("identity")`.

    Raises rather than returning False so a caller cannot forget to check (deviation 2).
    """
    row = bot(conn, slug)
    if not row or not row.get("token_hash"):
        raise Refused("identity", f"no live token for {slug}")
    if not secrets.compare_digest(str(row["token_hash"]), token_hash(token or "")):
        raise Refused("identity", f"the token for {slug} does not match")
    return row


def _actor_exists(conn, actor):
    kind = actor_kind(actor)
    if kind == KEEPER:
        return True
    if kind == "bot":
        return bot(conn, actor_id(actor)) is not None
    if kind == "human":
        return human(conn, actor_id(actor)) is not None
    return False


def _writer(conn, actor):
    """Rule 1: the actor must be someone hubdb knows, and not a quarantined bot."""
    if not actor_kind(actor):
        refuse(conn, str(actor), "identity", f"{actor!r} is not an actor")
    if not _actor_exists(conn, actor):
        refuse(conn, actor, "identity", f"{actor} is not on the roster")
    if is_bot(actor) and (bot(conn, actor_id(actor)) or {}).get("state") == "quarantined":
        refuse(conn, actor, "quarantined", f"{actor_id(actor)} is quarantined; a human clears it")
    return actor


def resolve_actor(conn, value):
    """`cmo`, `bot:cmo`, `ana`, `human:ana`, `keeper` -> the actor string, or None.

    None is what rule 2 refuses on: an address that is not a bot, a human or the keeper.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if actor_kind(raw):
        return raw if _actor_exists(conn, raw) else None
    if bot(conn, raw):
        return bot_actor(raw)
    if human(conn, raw):
        return human_actor(raw)
    return None


HELD_STATES = ("quarantined", "paused")
# A bot that exists but is not running. A write refused only for that says nothing about the sender.
INACTIVE_STATES = HELD_STATES + ("planned",)


def back_when(conn, slug, state):
    """When a held or planned bot runs again, in words the sender can act on."""
    if state == "planned":
        return "has not started yet"
    resumes = quarantine_resumes(conn, slug)["resumes_at"] if state == "quarantined" else None
    if resumes:
        return f"is quarantined until about {resumes[11:16]} UTC, when it resumes by itself"
    return f"is {state} until a person resumes it"


def target_inactive(conn, slug, state):
    """The refusal text for a write to a bot that is not running: when it is back, and how little it counts
    against the sender, so the sender waits instead of retrying."""
    return (f"{slug} {back_when(conn, slug, state)}. This refusal is not a repeat and opens no review of you; "
            "only the day's limit of 30 refusals counts it. Send it again once it is back")


def held_note(conn, target):
    """What the sender of a kept message reads when its bot is paused or quarantined; else None."""
    if not is_bot(target):
        return None
    slug = actor_id(target)
    state = (bot(conn, slug) or {}).get("state")
    if state not in HELD_STATES:
        return None
    return f"{slug} {back_when(conn, slug, state)}. Your message is kept for when it is back; no need to resend it."


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
    row = _one(conn, "SELECT reports_to, config_json FROM bot_config WHERE bot=?", (actor_id(target),)) or {}
    boss = str(row.get("reports_to") or (_json(row.get("config_json"), {}) or {}).get("reports_to") or "")
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
        refuse(conn, actor, "reach", f"{target} is not a bot or a person on the roster",
               classify(str(target), conn=conn))
    if is_bot(resolved):
        slug = actor_id(resolved)
        state = (bot(conn, slug) or {}).get("state")
        if state != "active" and not (allow_planned and state == "planned") and not (allow_held and state in HELD_STATES):
            if state in INACTIVE_STATES:
                refuse(conn, actor, "target_inactive", target_inactive(conn, slug, state))
            refuse(conn, actor, "reach", f"{slug} is {state}, not active")
    return resolved


def task_taking_refusal(conn, actor, target, title=None, parent=None, routine=False):
    """A bot a person syncs in from another platform reads only its chat with them, so a task for it would
    sit open with nobody to work it: it is refused with what to do instead. So is a task its profile says it does
    not take (backend/profiles.py): none for a Responder, only review and QA requests for a Reviewer, only its
    watcher's for a Monitor. Returns (rule, why) or None without recording a refusal, so automatic assignment
    can skip an ineligible bot. `title` and `parent` say what is being filed; `routine` is a routine's own run."""
    if not is_bot(target) or not _has_table(conn, "bot_config"):
        return
    from .harnesses import SYNCED_HARNESSES, resolve_harness
    row = _one(conn, "SELECT b.display_name,bc.config_json FROM bots b JOIN bot_config bc ON bc.bot=b.slug "
                     "WHERE b.slug=?", (actor_id(target),))
    if row and resolve_harness(_json(row["config_json"], {}) or {}) in SYNCED_HARNESSES:
        name = row["display_name"] or actor_id(target)
        return ("external-bot", f"{name} works on another platform and only syncs its chat here, so it "
                "never sees tasks. Write to it in its chat, or give this task to a bot that runs in Tico.")
    if row:
        from . import profiles
        return profiles.refusal(conn, row["display_name"] or actor_id(target), _json(row["config_json"], {}) or {},
                                actor, title, parent, routine, owner=target)


def _takes_tasks(conn, actor, target, title=None, parent=None, routine=False):
    if found := task_taking_refusal(conn, actor, target, title, parent, routine):
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
    manager = ((human(conn, reports[6:]) or {}).get("name") or reports[6:] if reports.startswith("human:")
               else (bot(conn, reports) or {}).get("display_name") or reports)
    return (f"{seat['title']} is still being recruited, so it can't take work yet"
            + (f". Give it to {manager}, who it reports to." if manager else "."))


def _recruiting(conn, actor, target):
    """The sender broke nothing, so a seat's refusal is `target_inactive`."""
    if why := recruiting(conn, target):
        refuse(conn, actor, "target_inactive", why)


def _onboarding(conn, slug):
    """Whether this bot is onboarding (backend/lifecycle.py); False on a database with no setup state at all."""
    if not _has_table(conn, "bot_config") or "onboarding_state" not in {r[1] for r in conn.execute("PRAGMA table_info(bot_config)")}:
        return False
    from .statuses import PARKED
    row = _one(conn, "SELECT onboarding_state FROM bot_config WHERE bot=?", (slug,))
    return bool(row) and row["onboarding_state"] in PARKED


def _learning(conn, actor, target):
    """An onboarding bot learns and takes no task until a person above it marks onboarding complete
    (backend/lifecycle.py). The sender broke nothing, so it is refused as `target_inactive`, like a paused bot."""
    if is_bot(target) and _onboarding(conn, actor_id(target)):
        name = (bot(conn, actor_id(target)) or {}).get("display_name") or actor_id(target)
        refuse(conn, actor, "target_inactive", f"{name} is still onboarding, so it can't take tasks yet. Someone above "
               "it on the org chart marks onboarding complete when it is ready.")


def _learner(conn, actor):
    """An onboarding bot files no task of its own while it learns: it asks in chat instead."""
    if is_bot(actor) and _onboarding(conn, actor_id(actor)):
        refuse(conn, actor, "onboarding", "You are still onboarding, so you can't create or hand out tasks yet. Ask "
               "your manager in chat; a person above you marks onboarding complete when you are ready.")


# ----------------------------------------------------------------------------- registry sync
def default_human(conn):
    """Who an unaddressed human item goes to: the roster's `default_user`, else its first
    person. The environment configures this in registry/people.yaml, never here."""
    if _one(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='registry_metadata'"):
        row = _one(conn, "SELECT value_json FROM registry_metadata WHERE key='people'")
        pid = str((_json(row["value_json"], {}) if row else {}).get("default_user") or "")
        if pid and human(conn, pid):
            return pid
    row = _one(conn, "SELECT id FROM humans ORDER BY rowid LIMIT 1")
    return row["id"] if row else ""


def sync_registry(conn, employees, people):
    """Bring `bots` and `humans` up to date with the registry. Nothing is ever deleted.

    `employees` is `{slug: entry}` as the registry merge returns it (defaults
    merged, `dir` set); `host` defaults to `dispatcher`. `people` is `registry/people.yaml`
    as a parsed document, or the list of person rows inside it.
    """
    ts = now()
    for slug, e in (employees or {}).items():
        e = e or {}
        state = str(e.get("status") or "active").strip() or "active"
        if state not in BOT_STATES:
            state = "active"
        row = {"slug": slug,
               "display_name": str(e.get("display_name") or slug),
               "runtime": str(e.get("runtime") or ""),
               "model": str(e.get("model") or ""),
               "effort": str(e.get("reasoning_effort") or e.get("effort") or ""),
               "cwd": str(e.get("dir") or e.get("cwd") or repo_dir(ROOT, slug)),
               "host": str(e.get("host") or "dispatcher"),
               "state": state, "created": ts}
        have = bot(conn, slug)
        if have is None:
            conn.execute(
                "INSERT INTO bots (slug, display_name, runtime, model, effort, cwd, host, state, created) "
                "VALUES (:slug, :display_name, :runtime, :model, :effort, :cwd, :host, :state, :created)", row)
        else:
            # never lower a state a human set, and never touch the token or the thread
            row["state"] = "quarantined" if have.get("state") == "quarantined" else state
            conn.execute("UPDATE bots SET display_name=:display_name, runtime=:runtime, model=:model, "
                         "effort=:effort, cwd=:cwd, host=:host, state=:state WHERE slug=:slug", row)
    doc = people if isinstance(people, dict) else {"people": people or []}
    for p in (doc.get("people") or []):
        p = p if isinstance(p, dict) else {}
        pid = str(p.get("id") or str(p.get("email") or "").split("@")[0]).strip()
        if not pid:
            continue
        teams = sorted({t for t in [str(p.get("team") or "").strip(), *(p.get("primary_for") or [])] if t})
        row = {"id": pid, "name": str(p.get("name") or pid.title()),
               "email": str(p.get("email") or "").lower(),
               "slack_id": str(p.get("slack_id") or "").strip() or None,
               "teams_json": _dump(teams)}
        if human(conn, pid) is None:
            conn.execute("INSERT INTO humans (id, name, email, slack_id, teams_json) "
                         "VALUES (:id, :name, :email, :slack_id, :teams_json)", row)
        else:
            conn.execute("UPDATE humans SET name=:name, email=:email, "
                         "slack_id=COALESCE(:slack_id, slack_id), teams_json=:teams_json "
                         "WHERE id=:id", row)
    event(conn, KEEPER, "registry.sync", "",
          {"bots": len(employees or {}), "humans": len(doc.get("people") or [])})
    return {"bots": bots(conn), "humans": humans(conn)}


# ----------------------------------------------------------------------------- conversations
def open_conversation(conn, actor, participants, kind="chat", subject="", task_id=None,
                      scope=None, owner_actor=None, room_key=None, allow_planned=False, allow_held=False):
    """A thread between any set of bots and humans. Reach (rule 2) is checked per participant."""
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
    if scope is None:
        scope = "task" if task_id else "direct"
        humans = [p for p in people if is_human(p)]
        if kind == "chat" and len(humans) == 1 and "bot:coo" in people:
            scope, owner_actor, room_key = "personal", humans[0], "coo"
    if scope not in CONVERSATION_SCOPES:
        refuse(conn, actor, "scope", f"a conversation scope is {'|'.join(CONVERSATION_SCOPES)}, not {scope}")
    if scope == "personal":
        humans = [p for p in people if is_human(p)]
        owner_actor = owner_actor or (humans[0] if len(humans) == 1 else None)
        if not owner_actor or owner_actor not in people or not is_human(owner_actor) or not room_key:
            refuse(conn, actor, "scope", "a personal conversation needs one participant owner and a room key")
    if scope == "shared" and not room_key:
        refuse(conn, actor, "scope", "a shared conversation needs a room key")
    row = {"id": new_id(), "kind": kind, "subject": str(subject or ""), "task_id": task_id,
           "participants_json": _dump(people), "scope": scope, "owner_actor": owner_actor,
           "room_key": room_key, "created": now(), "last_message_at": None, "closed_at": None}
    conn.execute("INSERT INTO conversations (id, kind, subject, task_id, participants_json, "
                 "scope, owner_actor, room_key, created, last_message_at, closed_at) "
                 "VALUES (:id, :kind, :subject, :task_id, :participants_json, :scope, :owner_actor, "
                 ":room_key, :created, :last_message_at, :closed_at)", row)
    event(conn, actor, "conversation.open", row["id"],
          {"kind": kind, "scope": scope, "participants": people})
    return conversation(conn, row["id"])


def _pair_conversation(conn, actor, other, kind, subject="", allow_held=False):
    """The live conversation of this kind between these two, opened if there is none."""
    want = {actor, other}
    for row in _rows(conn.execute(
            "SELECT * FROM conversations WHERE kind=? AND closed_at IS NULL AND task_id IS NULL "
            "ORDER BY COALESCE(last_message_at, created) DESC LIMIT 200", (kind,))):
        if set(_json(row["participants_json"], []) or []) == want:
            return conversation(conn, row["id"])
    return open_conversation(conn, actor, [actor, other], kind=kind, subject=subject, allow_held=allow_held)


def _opener(conn, conversation_id):
    """Who started this conversation: the first message's sender, else whoever opened it, which may be
    the person even when only notices are in the room.

    The keeper's notices do not count: a "New task" notice can land first in a person's chat with a
    bot, and the person's own message after it is what the bot answers."""
    first = _one(conn, "SELECT from_actor FROM messages WHERE conversation_id=? AND from_actor<>? "
                       "ORDER BY created LIMIT 1", (conversation_id, KEEPER))
    if first:
        return first["from_actor"]
    row = _one(conn, "SELECT actor FROM events WHERE action='conversation.open' AND target=? "
                     "ORDER BY ts LIMIT 1", (conversation_id,))
    return row["actor"] if row else None


# ----------------------------------------------------------------------------- messages
def say(conn, actor, to_actor, body, conversation_id=None, kind="say", refs=None,
        in_reply_to=None, wait_s=None):
    """The one write every message goes through: rules 1, 2, 3, 4, 7 and 8 all live here."""
    _writer(conn, actor)
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

    if is_bot(actor) and actor == target:
        refuse(conn, actor, "self", "a bot cannot send work to itself")

    if conversation_id:
        conv = conversation(conn, conversation_id)
        if not conv:
            refuse(conn, actor, "not-found", f"no conversation {conversation_id}")
    else:
        conv = _pair_conversation(conn, actor, target,
                                  {"ask": "ask", "notice": "notice"}.get(kind, "chat"), allow_held=True)

    if kind == "ask":
        depth = _ask_depth(conn, actor, refs, in_reply_to)
        if depth > MAX_ASK_DEPTH:
            refuse(conn, actor, "depth",
                   f"this ask is at depth {depth}; asks stop at {MAX_ASK_DEPTH}")
        refs["depth"] = depth
        if is_bot(actor) and is_human(target):
            about = message_task_id({"refs": refs}, conv)
            ended = task(conn, about) if about else None
            # It would only be superseded (supersede_asks, approval_expiry): nobody acts on an ended task's
            # questions. A done task still asks its requester to accept what it delivered, with structured questions.
            if ended and ended["status"] in TASK_ENDED and not (ended["status"] == "done" and refs.get("questions")
                                                               and target == ended["requester"]):
                refuse(conn, actor, "lint", f"task {ended['id']} is {ended['status']}; {task_reopen_hint(actor, ended)}")

    # rules 4 and 7: what a person has to read
    if is_bot(actor) and is_human(target) and kind in ("say", "notice"):
        parent = (_one(conn, "SELECT from_actor,conversation_id FROM messages WHERE id=?", (in_reply_to,))
                  if in_reply_to else None)
        replying_to_person = bool(parent and parent["from_actor"] == target and
                                  parent["conversation_id"] == conv["id"])
        solicited = (replying_to_person or _opener(conn, conv["id"]) == target or
                     bool(refs.get("task") or refs.get("approval")))
        if not solicited:
            day = now()[:10]
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE from_actor=? AND to_actor=? "
                "AND substr(created,1,10)=? AND refs_json LIKE '%\"unsolicited\": true%'",
                (actor, target, day)).fetchone()[0]
            if n >= UNSOLICITED_PER_DAY:
                refuse(conn, actor, "unsolicited",
                       f"{n} unsolicited messages to {actor_id(target)} today; the cap is "
                       f"{UNSOLICITED_PER_DAY}. File a task or an approval instead.", severity)
            warnings = style_problems(conn, actor, lint_human_item(body), severity)
            refs["unsolicited"] = True

    # Token saving (Dev 149): a plain bot-to-bot message waits for the target's next run unless flagged.
    from . import bot_to_bot
    # A delegated verdict belongs to the requesting task's session. Keep the original on the review,
    # quietly, and deliver once on the parent, including when the runner posts the review's final reply.
    from .wake_rules import VERDICT_FIELDS
    result_task = (task(conn, _task_ref(refs.get("task")) or "")
                   if kind in ("say", "notice") and is_bot(actor) and is_bot(target)
                   and not refs.get("quiet") and VERDICT_FIELDS.search(str(body or "")) else None)
    result_parent = (_subtask_result_parent(conn, result_task) if result_task and result_task["owner"] == actor
                     and result_task["requester"] == target else None)
    result_key = _subtask_result_key(conn, result_task, body) if result_parent and result_parent["owner"] == target else None
    if result_key:
        refs = {**refs, "quiet": True}
    from . import rules as wake_rule_engine
    # A plain bot-to-bot message waits under `bot_to_bot` unless a wake rule says otherwise (backend/rules.py).
    decided = wake_rule_engine.bot_to_bot_decision(conn, actor, target, kind, refs, body, conv, in_reply_to)
    waits = bool(decided and decided.rule == wake_rule_engine.BOT_TO_BOT)
    if waits:
        refs.update(quiet=True, **{bot_to_bot.MARK: bot_to_bot.RULE})
    msg = _write_message(conn, actor, target, body, conv, kind, refs, in_reply_to, wait_s, check_cap=True,
                         decided=decided)
    if result_key:
        _deliver_subtask_result(conn, result_task, result_parent, body, result_key, source=msg["id"], resume_hint=True)
    if waits:
        bot_to_bot.hold(conn, msg, task(conn, on_task) if isinstance(on_task, str) and on_task else None)
    _close_open_asks(conn, actor, target, kind, msg)
    if kind == "ask" and is_bot(actor) and is_human(target):
        about = message_task_id({"refs": refs}, conv)
        if about and (replaced := supersede_asks(conn, about, from_actor=actor, to_actor=target, keep=msg["id"])):
            from . import task_holds
            task_holds.withdrawn(conn, about, replaced, "a newer question replaced it", by=actor)
            _recount(conn, actor)
        hook(conn, "needs_you.created", msg["id"], lambda: {"id": msg["id"], "to": target, "from": actor, "task_id": about},
             actor, revision=0)
    if warnings:
        event(conn, actor, "message.lint", msg["id"], {"problems": warnings})
        return {**msg, "warnings": warnings}
    return msg


def _close_open_asks(conn, actor, target, kind, msg):
    """A person's reply answers plain asks in scope; structured asks require a recorded choice.
    The bot wakes on a note as on any message, while the decision stays open.

    A shared conversation alone does not identify which task a person is answering.
    A direct reply takes precedence over task context so it cannot close sibling asks.
    """
    refs = _json(msg.get("refs_json"), {}) or msg.get("refs") or {}
    if (kind not in ("say", "answer") or not is_human(actor) or not is_bot(target)
            or refs.get("clarify") or refs.get("decision_open")):
        return      # asking for a clearer question (backend/clarify_asks.py) is not an answer
    if msg.get("in_reply_to"):
        scope, scope_id = "m.id", msg["in_reply_to"]
    else:
        scope_id = message_task_id({"refs": refs}, conversation(conn, msg["conversation_id"]))
        if not scope_id:
            return
        scope = MESSAGE_TASK_SQL
    open_asks = [r["id"] for r in _rows(conn.execute(
        "SELECT m.id,m.refs_json FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE m.to_actor=? AND m.from_actor=? AND m.kind='ask' AND m.superseded_at IS NULL "
        "AND m.answered_by IS NULL AND m.id NOT IN (SELECT in_reply_to FROM messages "
        "WHERE kind='answer' AND in_reply_to IS NOT NULL) "
        f"AND {scope}=?",
        (actor, target, scope_id))) if refs.get("answer") or not has_pick_options(r)]
    if not open_asks:
        return
    conn.executemany("UPDATE messages SET answered_by=? WHERE id=?",
                     [(msg["id"], mid) for mid in open_asks])
    _recount(conn, target)                  # its questions to this person are answered
    # Named in the log, because a question closing without anyone typing an answer to it is
    # exactly the thing you want to be able to look up later.
    event(conn, actor, "message.answered_by_reply", msg["id"],
          {"asks": open_asks, "bot": target})
    for task_id in dict.fromkeys(filter(None, (
            scope_id if scope == MESSAGE_TASK_SQL else message_task_id(m, conversation(conn, m["conversation_id"]))
            for m in (message(conn, mid) for mid in open_asks)))):
        _answered_holds(conn, actor, task_id, open_asks, target)


def _answered_holds(conn, actor, task_id, ask_ids, asker):
    """`actor` just answered `ask_ids` on `task_id`. A wait on them for those questions ends once none to them is open,
    and the task's reason follows; when that was its last hold, the bot that asked goes back to work, and a person's
    task says the answer is what cleared (backend/task_holds.py). Another bot's question answered on a bot's task
    changes only its words: that bot was not waiting on it."""
    from . import task_holds
    before = (task(conn, task_id) or {}).get("waiting_on")
    drop_asked_waits(conn, task_id, ask_ids, "")
    row = task(conn, task_id) or {}
    own = row.get("owner") == asker
    held = own or row.get("waiting_on") != before or is_human(row.get("owner") or "")
    whose = "your" if own else f"{task_holds.name(conn, asker)}'s"
    task_holds.refresh(conn, task_id, f"{task_holds.name(conn, actor)} answered {whose} question" if held else None)


LIBRARIAN_LITERAL = re.compile(
    r"^[ \t]{0,3}(?P<bf>`{3,})[^\n]*\n[\s\S]*?(?:^[ \t]{0,3}(?P=bf)`*[ \t]*$|\Z)"   # fenced code: the closing line
    r"|^[ \t]{0,3}(?P<tf>~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]{0,3}(?P=tf)~*[ \t]*$|\Z)"  # is the same character only
    r"|^(?:(?: {4}|\t)[^\n]*(?:\n|\Z))+"                      # indented code
    r"|(?<!`)(?P<ticks>`+)(?!`)[\s\S]*?(?<!`)(?P=ticks)(?!`)"   # code spans: equal-length backtick runs
    r"|\[[^\]\n]*\](?:\([^)]*\)|\[[^\]]*\])?"                # link labels, inline or reference
    r"|https?://\S+|[\w.-]+/[\w/.-]+"                           # URLs and paths
    r'|"[^"]*"|“[^”]*”|‘[^’]*’|(?<!\w)\'[^\']+\'(?!\w)'       # quotes, also across lines
    r"|^[ \t]*>[^\n]*", re.M)                                   # blockquotes
JARGON = ((r"\bthrough the runner\b", "through Tico"), (r"\bstanding instructions\b", "Instructions"),
          (r"\bHub docs\b", "Tico docs"))
# "the runner pulls/syncs" is the Computer only when Tico context follows before the next semicolon or sentence
# end, even inside code or a link ("syncs `AGENT.md`"); an athlete who pulls a hamstring stays one when the Tico
# word comes after a semicolon. Colons and dashes do not stop it: they can introduce what is synced.
RUNNER = re.compile(r"\bthe runner(?=\s+(?:pulls?|syncs?)\b[^.!?;\n]*?(?:\b(?:updates?|releases?|changes|instructions|"
                    r"repository|repo|next run|Tico)\b|AGENT\.md))", re.I)


def _keep_case(found, new):
    """`new` in the case of the words it replaces: ALL CAPS, Title Case, Sentence case or lower."""
    if found.isupper():
        return new.upper()
    if found.istitle() and " " in found.strip():
        return new.title()
    return new[0].upper() + new[1:] if found[0].isupper() else new


def librarian_text(body):
    """Repair generated prose (Tico's own jargon only) while keeping code, links, quotes and blockquotes as written:
    the team's prose about its business (its company, coworkers, machines, a race runner) is a source fact."""
    body = str(body or "")
    literals = [m.span() for m in LIBRARIAN_LITERAL.finditer(body)]
    runners = {m.start() for m in RUNNER.finditer(body)}
    out, at = [], 0
    for start, end in literals + [(len(body), len(body))]:
        text = ""
        if at < start:
            text = re.sub(r"\bthe runner\b", lambda m, base=at: (_keep_case(m[0], "the Computer")
                                                                if base + m.start() in runners else m[0]),
                          body[at:start], flags=re.I).replace(r"\n", "\n")
            for old, new in JARGON:
                text = re.sub(old, lambda m, new=new: _keep_case(m[0], new), text, flags=re.I)
        out += [text, body[start:end]]
        at = end
    return "".join(out)


# One rule decides both whether a new message is charged and which stored messages count: _held_queue_drains,
# read from the stored references. It needs no job or note, so it holds for an external harness's inbox (hermes,
# openclaw and the rest get no job without a delivery computer, and a waking bot-to-bot message gets no carry-note)
# and on a hub database without Store's job queue. A record that settles a message later only ever removes it:
# a job finished or cancelled (a duplicate wake), or carry-notes that are no longer waiting.


def _held_queue_drains(actor, target, refs_json):
    """Whether a bot-to-bot message, stored or about to be stored, is held for the recipient until it resumes.

    The single test for the held queue: admission applies it before insert and the count applies it to every
    pending stored row. A bot's message to itself, a quiet message nothing carries (a wake=False comment, a
    same-state skip) and live run output are never handed over, and charging them would fill the cap for good."""
    if actor == target:
        return False                       # store.QUEUE_TRIGGER queues no job for a bot's message to itself
    try:
        refs = json.loads(refs_json or "{}")
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
        # A runner-delivered bot gets a held message only through its job, so one finished or cancelled (a
        # duplicate wake) is never handed over. An external harness's inbox still holds it whatever its job did.
        from .store import EXTERNAL_BOT_SQL
        settled.append("(NOT " + EXTERNAL_BOT_SQL.replace("NEW.", "m.") + " AND EXISTS(SELECT 1 FROM jobs j "
                       "WHERE j.message_id=m.id) AND NOT EXISTS(SELECT 1 FROM jobs j "
                       "WHERE j.message_id=m.id AND j.state NOT IN ('completed','cancelled')))")
    waiting = NOTE_WAITING_SQL if _has_table(conn, "attempts") else "n.cancelled_at IS NULL"
    settled.append("(EXISTS(SELECT 1 FROM notes n WHERE n.message_id=m.id) AND NOT EXISTS(SELECT 1 FROM notes n "
                   f"WHERE n.to_actor=m.to_actor AND n.message_id=m.id AND {waiting}))")
    return " OR ".join(settled)


def _held_queue_pending(conn, target, *, excluding=None):
    """Per sender, the items and stored bytes of every pending message _held_queue_drains holds for target."""
    size_sql = "coalesce(length(CAST(m.body AS BLOB)),0) + coalesce(length(CAST(m.refs_json AS BLOB)),0)"
    totals = {}
    for r in conn.execute(
            f"SELECT m.from_actor, m.refs_json, {size_sql} AS bytes FROM messages m WHERE m.to_actor=? "
            "AND m.delivered_at IS NULL AND m.deleted_at IS NULL AND m.from_actor LIKE 'bot:%' "
            f"AND m.from_actor<>m.to_actor AND (? IS NULL OR m.id<>?) AND NOT ({_held_queue_settled_sql(conn)})",
            (target, excluding, excluding)):
        if _held_queue_drains(r["from_actor"], target, r["refs_json"]):
            t = totals.setdefault(r["from_actor"], {"items": 0, "bytes": 0})
            t["items"] += 1
            t["bytes"] += r["bytes"]
    return totals


def _held_queue_admit(conn, actor, target, body, refs_json, *, replacing=None):
    """Charge bot-authored pending messages across every room that resume will deliver.

    Called with the final stored text/references under Store's BEGIN IMMEDIATE write transaction,
    immediately before insertion. Permission/contact checks belong to the caller and still run first.
    System notices, people, active recipients and recovery controls retain their existing behavior.
    """
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
            refuse(conn, actor, "queue_full",
                   f"{actor_id(target)} is {state}; its held queue is full ({label} limit {limit}). "
                   "Already accepted items are kept. Wait until the recipient resumes before sending again. "
                   "Waiting does not count against the sender and does not release a quarantine.")


def held_queue_recheck(conn, message_id):
    """Recheck a newly inserted message after its sending path adds stored references, in the same transaction."""
    msg = message(conn, message_id)
    if msg and not msg.get("delivered_at") and not msg.get("deleted_at"):
        _held_queue_admit(conn, msg["from_actor"], msg["to_actor"], msg["body"], msg["refs_json"],
                          replacing=message_id)


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


def _write_message(conn, actor, target, body, conv, kind, refs, in_reply_to, wait_s,
                   expires_at=None, delivered_at=None, read_at=None, *, check_cap=False, decided=None):
    if actor == "bot:librarian":
        body = librarian_text(body)
    if VIA.get() and is_human(actor):
        refs = {**(refs or {}), "via": VIA.get()}         # the Assistant wrote this for the person
    from . import wake_rules
    if actor == KEEPER and str(target).startswith("bot:") and is_human(wake_rules.CAUSE.get()):
        # Tico's notice of a person's own act keeps who acted: the request's cause ends with it, and the reply
        # rule at the turn's end (execution.quiet_recheck) must still see that a person is owed the answer.
        refs = {**(refs or {}), wake_rules.CAUSED_BY: wake_rules.CAUSE.get()}
    # Token saving 2/9: a wake its bot's own write caused, or news about a parked task, waits for the next run. The
    # rules engine (backend/rules.py) decides, names the rule on the message (refs.rule) and fails open.
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
    row = {"id": new_id(), "conversation_id": conv["id"], "from_actor": actor, "to_actor": target,
           "kind": kind, "body": str(body or ""), "refs_json": _dump(refs or {}),
           "in_reply_to": in_reply_to, "created": now(), "delivered_at": delivered_at,
           "read_at": read_at, "expires_at": expires_at, "wait_s": wait_s}
    if delivered_at is None:
        _held_queue_admit(conn, actor, target, row["body"], row["refs_json"])
    # Capacity waiting is not loop misconduct, even when this conversation also hit its cap.
    # rule 3: the loop caps
    if check_cap and is_bot(actor) and is_bot(target):
        since = shift(now(), hours=-1)
        # The cap stops two bots talking in circles, so it counts bot-to-bot messages only: the
        # keeper's notices (a routine's "new task", "finished") and a person's messages are not a
        # loop. A bot's private room with its owner, with a sweep every 30 minutes, could hit
        # the cap on messages of which many were notices or the owner's.
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND created>=? "
                         "AND from_actor LIKE 'bot:%' AND to_actor LIKE 'bot:%' AND kind<>'notice'",
                         (conv["id"], since)).fetchone()[0]
        if n >= CAP_PER_HOUR:
            refuse(conn, actor, "cap",
                   f"{n} messages in this conversation in the last hour; the cap is {CAP_PER_HOUR}")
    conn.execute(
        "INSERT INTO messages (id, conversation_id, from_actor, to_actor, kind, body, refs_json, "
        "in_reply_to, created, delivered_at, read_at, expires_at, wait_s) VALUES (:id, "
        ":conversation_id, :from_actor, :to_actor, :kind, :body, :refs_json, :in_reply_to, "
        ":created, :delivered_at, :read_at, :expires_at, :wait_s)", row)
    conn.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (row["created"], conv["id"]))
    # Notes, comments and runner final replies all pass here, including quiet ones
    # and checks requested by a person. Only the check owner may supply its verdict.
    if kind in ("say", "notice") and wake_rules.VERDICT_FIELDS.search(str(body or "")):
        reviewed = task(conn, message_task_id({"refs": refs or {}}, conv) or "")
        if reviewed and reviewed["owner"] == actor:
            record_verdict(conn, actor, reviewed, text=body)
    if kind == "ask" and (asked_about := message_task_id({"refs": refs or {}}, conv)):
        from . import task_holds
        task_holds.refresh(conn, asked_about)     # a new question on a waiting task is what it waits on now
    event(conn, actor, f"message.{kind}", row["id"], {"to": target, "conversation": conv["id"]})
    if held:
        wake_rules.hold(conn, message(conn, row["id"]), held, rule_id=decision.rule)
    return message(conn, row["id"])


def feed(conn, target, body, conv, refs=None):
    """Context the system hands a bot to read: a Slack channel digest (backend/slack_gateway.py).

    Written by the keeper, not by a bot or a person: nobody said it. It is a turn for the bot
    (the queue trigger fires, as for any message to a bot), but it is quoted Slack text, so the
    escape and lint rules a bot's own words go through do not apply; what it quotes was said
    in a channel the bot is in. Rule 2 still holds: the bot must be active.
    """
    resolved = _reach(conn, KEEPER, target)
    return _write_message(conn, KEEPER, resolved, body, conv, "say", {"wake": "feed", **(refs or {})}, None, None)


def _depth_of(row):
    try:
        return int((_json((row or {}).get("refs_json"), {}) or {}).get("depth") or 1)
    except (TypeError, ValueError):
        return 1


def _ask_depth(conn, actor, refs, in_reply_to):
    """Rule 3: "an ask made while answering an ask at depth 3" is depth 4, and is refused.

    An asker is "answering" every ask addressed to it that nobody has answered yet, so the
    depth of a new ask is one more than the deepest of those: A asks B (1), B asks C (2),
    C asks D (3), D may not ask on. Repeat asks to the same bot stay at depth 1 — depth
    counts the chain, not the traffic. A declared `refs.depth` (what the keeper renders into
    an ask) is taken when it is deeper, so a bot cannot reset the count by claiming depth 1.
    """
    try:
        declared = int((refs or {}).get("depth") or 0)
    except (TypeError, ValueError):
        declared = 0
    parent = message(conn, in_reply_to) if in_reply_to else None
    if parent is not None and parent.get("kind") == "ask":
        return max(declared, _depth_of(parent) + 1, 1)
    open_asks = conn.execute(
        "SELECT refs_json FROM messages m WHERE m.to_actor=? AND m.kind='ask' AND NOT EXISTS "
        "(SELECT 1 FROM messages a WHERE a.in_reply_to=m.id AND a.kind='answer')",
        (actor,)).fetchall()
    deepest = max([_depth_of({"refs_json": r["refs_json"]}) for r in open_asks], default=0)
    return max(declared, deepest + 1, 1)


def has_pick_options(msg):
    """Questions offering only free text keep the ordinary answer protocol."""
    refs = msg.get("refs") or _json(msg.get("refs_json"), {}) or {}
    return any(q.get("options") for q in refs.get("questions", []))


def answer(conn, actor, message_id, body, unknown=False, *, comment_refs=None, comment_target=None, classify_text=None):
    """Reply to an ask. A checked task comment may also answer a structured task ask."""
    _writer(conn, actor)
    asked = message(conn, message_id)
    if not asked:
        refuse(conn, actor, "not-found", f"no message {message_id}")
    task_reply = bool(comment_refs and (asked.get("refs") or {}).get("questions")
                      and comment_refs.get("task") == (asked.get("refs") or {}).get("task"))
    if (asked.get("refs") or {}).get("questions") and asked["from_actor"] == actor:
        refuse(conn, actor, "identity", "The asker cannot answer their own question")
    if asked["to_actor"] != actor and not task_reply:
        refuse(conn, actor, "identity", f"{message_id} was not addressed to {actor}")
    path_mentioned(conn, actor, "reply", body if classify_text is None else classify_text, (comment_refs or {}).get("task"))
    refs = {"depth": (asked.get("refs") or {}).get("depth", 1), **(comment_refs or {})}
    if unknown:
        refs["unknown"] = True
    if has_pick_options(asked) and not refs.get("answer"):
        refs.update({"decision_open": True})
        about = message_task_id(asked, conversation(conn, asked["conversation_id"]))
        if about:
            refs["task"] = about
        return _write_message(conn, actor, comment_target or asked["from_actor"], body,
                              conversation(conn, asked["conversation_id"]), "say", refs, message_id, None)
    conv = conversation(conn, asked["conversation_id"])
    msg = _write_message(conn, actor, comment_target or asked["from_actor"], body, conv, "answer", refs, message_id, None)
    about = message_task_id(asked, conv)
    if about:                               # an answered question on a task changes the task
        conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), about))
        if asked.get("kind") == "ask":
            _answered_holds(conn, actor, about, [message_id], asked["from_actor"])
    _recount(conn, asked["from_actor"])     # it no longer waits on whoever answered
    if is_human(actor) and asked.get("kind") == "ask":
        hook(conn, "needs_you.answered", message_id, lambda: {"id": message_id, "to": actor, "from": asked["from_actor"],
                                                              "task_id": about}, actor, revision=0)
    if about and asked["from_actor"] == "bot:goal-manager" and is_human(actor):
        from . import strategy_merge
        strategy_merge.on_reply(conn, actor, task(conn, about), body)     # answered in Needs you: on to each asker
    if about and is_human(actor):
        from . import ask_ingest
        ask_ingest.on_reply(conn, actor, about, body)     # repeats the ask filter merged here hear the answer too
    return msg


def notice(conn, actor, to_actor, body, refs=None, expires_days=NOTICE_DAYS):
    """An fyi. It is never a task; it leaves the inbox after `expires_days`."""
    msg = say(conn, actor, to_actor, body, kind="notice", refs=refs)
    stop = shift(msg["created"], days=int(expires_days or NOTICE_DAYS))
    conn.execute("UPDATE messages SET expires_at=? WHERE id=?", (stop, msg["id"]))
    return message(conn, msg["id"])


def mark_delivered(conn, actor, message_id):
    """The keeper sets this only once the runtime accepted the input (at-least-once delivery)."""
    conn.execute("UPDATE messages SET delivered_at=? WHERE id=? AND delivered_at IS NULL",
                 (now(), message_id))
    event(conn, actor, "message.delivered", message_id)
    return message(conn, message_id)


def mark_read(conn, actor, message_id):
    conn.execute("UPDATE messages SET read_at=? WHERE id=? AND read_at IS NULL",
                 (now(), message_id))
    event(conn, actor, "message.read", message_id)
    return message(conn, message_id)


# ----------------------------------------------------------------------------- tasks
def can_move(conn, actor):
    """Whether this person may move any task: on a mover team in the roster (MOVER_TEAMS).
    The environment owner is decided by the caller."""
    row = human(conn, actor) if is_human(actor) else None
    return bool(row and set(row.get("teams") or []) & set(MOVER_TEAMS))


# ----------------------------------------------------------------------------- task pipelines
def type_get(conn, value):
    row = _one(conn, "SELECT * FROM task_types WHERE id=? OR name=? COLLATE NOCASE ORDER BY id=? DESC LIMIT 1",
               (value, value, value))
    if row:
        row["numbered"] = bool(row.get("numbered"))
        row["steps_only"] = bool(row.get("steps_only"))
        row["steps"] = _step_views(conn, _rows(conn.execute("SELECT * FROM task_steps WHERE type_id=? ORDER BY position,id",
                                                            (row["id"],))))
    return row


def _step_views(conn, steps):
    """Steps as callers read them: a check step's pool as a list and its limit (backend/check_pools.py), and its flow
    with step names (backend/step_flow.py)."""
    from . import check_pools, step_flow
    steps = [check_pools.view(step) for step in steps] if steps and "check_pool" in steps[0] else steps
    if not steps or "flow_json" not in steps[0]:
        return steps
    by_type = {}
    for step in steps:
        by_type.setdefault(step["type_id"], []).append(step)
    shown = {s["id"]: s for group in by_type.values() for s in step_flow.view(conn, group)}
    return [shown[step["id"]] for step in steps]


def type_list(conn):
    from .read_cache import rows as shared_rows
    rows = _rows(conn.execute("SELECT * FROM task_types ORDER BY id<>?,name", (GENERAL_TYPE,)))
    # Shared between snapshots until a step changes (backend/roster_clock.py); copied, since views add keys to them.
    steps = _step_views(conn, [dict(r) for r in shared_rows(conn, "SELECT * FROM task_steps ORDER BY position,id")])
    for row in rows:
        row["numbered"] = bool(row.get("numbered"))
        row["steps_only"] = bool(row.get("steps_only"))
        row["steps"] = [step for step in steps if step["type_id"] == row["id"]]
    return rows


# What every bot may do with the tasks of a type, beyond the tasks it is a party to. A team's board
# (a developer board, a support queue) is shared work: the bots that file, build and hand it on need
# to read all of it, where a person's own to-dos stay with the people and bots on them.
#   read: read every task of the type, comment on it and file a subtask under it.
#   work: also change it as its owner or requester could (step, owner, due, body, rank), add or
#         remove its links, and close it once it is in a ready step (shipped). Moving it into ready
#         and its labels stay with people.
TYPE_BOTS = ("read", "work")


def _type_bots(conn, actor, bots):
    if bots in (None, "", "parties"):
        return None
    if bots not in TYPE_BOTS:
        refuse(conn, actor, "kind", f"bots is parties, {' or '.join(TYPE_BOTS)}, not {bots}")
    return bots


def type_bots(conn, row):
    """What every bot may do with this task because of its type: None, "read" or "work"."""
    type_id = row.get("type_id") if "type_id" in row else (
        (_one(conn, "SELECT type_id FROM tasks WHERE id=?", (row["id"],)) or {}).get("type_id"))
    if not type_id:
        return None
    found = _one(conn, "SELECT bots FROM task_types WHERE id=?", (type_id,))
    return (found or {}).get("bots") or None


def type_bot_reads(conn, actor, row):
    return is_bot(actor) and task_private_readable(conn, actor, row) and type_bots(conn, row) in TYPE_BOTS


def type_bot_works(conn, actor, row):
    return is_bot(actor) and task_private_readable(conn, actor, row) and type_bots(conn, row) == "work"


TASK_MANAGER = "bot:task-manager"


def task_manager_acts(conn, actor, row):
    """The Task Manager's narrow mover right (docs/task-manager.md): on a task it may read it closes with a note, sets
    status, step and waits, relates, comments and reassigns within the department. It is not a `mover` (no lane, number,
    labels, types or tags) and never a bot with rights on a bot, a credential, a send or spending. The slug alone is not
    enough: the bot behind it must be built from the task-manager template, so a bot someone names the same cannot ride on
    it. A private task it is not on stays unreadable (task_private_readable), so this never widens privacy."""
    if actor != TASK_MANAGER or not row:
        return False
    built = _one(conn, "SELECT json_extract(config_json,'$.template') AS template FROM bot_config WHERE bot=?",
                 ("task-manager",)) if _has_table(conn, "bot_config") else None
    return (built or {}).get("template") == "task-manager" and task_private_readable(conn, actor, row)


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
    """Private tasks admit their parties, bot parties’ org-chart managers and their one guest (task_reach)."""
    if not row:
        return False
    row = dict(row)
    if not task_private(conn, row):
        return True
    if "owner" not in row or "requester" not in row:
        row = task(conn, row["id"])
        if not row:
            return False
    if actor in (row["owner"], row["requester"]):
        return True
    from .task_rights import private_subjects
    inherited = private_subjects(conn, actor)
    if any(row[k] in inherited for k in ("owner", "requester")):
        return True
    if "guest" not in row:
        row = _one(conn, "SELECT guest FROM tasks WHERE id=?", (row["id"],)) or {}
    return bool(row.get("guest")) and actor == row["guest"]


# ----------------------------------------------------------------------------- a private task's guest
# A private task with no person on it (a bot's task for another bot, or for itself) would have no way to reach anyone:
# every hand-off from its runs is refused because nobody it could go to reads it. Its owning bot may bring in ONE
# person, its guest, by handing them work: a subtask under it, a question on it, or waiting on them. The guest reads
# that task as its parties do and nothing else: not its parent, its other subtasks, or anything the run also carries.
# The first person is kept; a hand-off to anyone else is refused. Changing the owner ends it.

def bot_only(row):
    """No person on this task: its requester and owner are both bots (the same bot for a bot's task to itself)."""
    return is_bot(row.get("requester") or "") and is_bot(row.get("owner") or "") and not row.get("guest")


def task_reach_refusal(conn, actor, row, person):
    """Why `actor` may not make `person` the guest of private task `row`, or None when it may (or they already read
    it). The words are the refusal a create, a question or a wait gives, and a dry-run gives the same."""
    if not row or not task_private(conn, row) or task_private_readable(conn, person, row):
        return None
    row = task(conn, row["id"]) or row
    who = actor_id(person)
    if not is_human(person) or not human(conn, who):
        return f"{person} is not a person on the team"
    if row.get("guest"):
        return (f"This private task already reached {actor_id(row['guest'])}, its one guest, and {who} cannot read it: "
                f"hand this to {actor_id(row['guest'])}, or ask a person on the task to make it company-visible")
    if not bot_only(row):
        return "Private task content stays with its requester and current owner"
    if not task_represents(conn, actor, row["owner"]):
        return "Only the bot that owns a private task or its managers bring a person into it"
    for parent_id in TR.ancestors(conn, row["id"]):
        parent = task(conn, parent_id)
        if parent and task_private(conn, parent) and not bot_only(parent) and not task_private_readable(
                conn, person, parent):
            return (f"A private task above this one has a person on it whom {who} cannot read, so its work "
                    f"cannot reach {who}")
    return None


def subtask_flows_up(conn, row, parent):
    """Whether private subtask `row`'s words may reach readers of `parent`, its private parent, who cannot read the
    subtask: every party of the subtask (requester, owner and guest) reads the parent. The person a bot handed a
    subtask or a question under its task is that task's guest, so their answer comes back to the task it was asked
    for, and to nobody that task does not already have."""
    if not row or not parent or not task_private(conn, parent) or TR.parent_of(conn, row["id"]) != parent["id"]:
        return False
    row = task(conn, row["id"]) or {}
    parties = [p for p in (row.get("requester"), row.get("owner"), row.get("guest")) if p]
    return bool(parties) and all(task_private_readable(conn, p, parent) for p in parties)


def task_reach(conn, actor, row, person):
    """Make `person` the guest of private task `row` (task_reach_refusal said yes); a no-op when they already read it.
    Recorded in the task's history, so its parties see who it reached."""
    if task_private_readable(conn, person, row):
        return
    conn.execute("UPDATE tasks SET guest=? WHERE id=?", (person, row["id"]))
    _task_event(conn, row["id"], actor, "guest", None, person, "")


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


# The templates whose tasks are private unless their config says otherwise: a general counsel's, and Counsel's (a
# document's text and a can-I question are a company's own, so a teammate who cannot read the source cannot read them).
PRIVATE_BY_TEMPLATE = ("general-counsel", "tico-counsel")
# The built-in Counsel's slug (backend/counsel.py): reserved under Tico's own name, so a company's own bot named
# "counsel" (a legal team's, say) is never taken for it.
COUNSEL = "tico-counsel"



def migrate_task_privacy(conn):
    """Classify pre-privacy rows from stored roster/config identities; keep ambiguity private."""
    had_privacy = 'private' in {row[1] for row in conn.execute('PRAGMA table_info(tasks)')}
    _apply(conn, TASK_PRIVACY_SCHEMA)
    if not had_privacy:
        humans = {row[0] for row in conn.execute('SELECT id FROM humans')}
        bots = {row[0] for row in conn.execute('SELECT slug FROM bots')}
        configs = {row[0]: _json(row[1]) for row in conn.execute('SELECT bot,config_json FROM bot_config')} \
            if _has_table(conn, 'bot_config') else {}
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
        rows = _rows(conn.execute('SELECT id,requester,owner,' + (
            'parent_id' if 'parent_id' in {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}
            else TR.parent_sql() + ' AS parent_id') + ' FROM tasks'))
        private = {row['id'] for row in rows if sensitive_or_unknown(row['requester'])
                   or sensitive_or_unknown(row['owner'])}
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
        conn.executemany('UPDATE tasks SET private=? WHERE id=?',
                         [(int(row['id'] in private), row['id']) for row in rows])
    add_column(conn, 'conversations', 'scope', "TEXT NOT NULL DEFAULT 'direct'")
    for row in _rows(conn.execute('SELECT * FROM tasks WHERE private IS NULL OR private<>0')):
        isolate_private_task(conn, row)


def isolate_private_task(conn, row):
    """Move tracked task messages intact out of broader rooms and revoke former thread members."""
    if not task_private(conn, row):
        return row
    conv = conversation(conn, row["conversation_id"])
    parties = list(dict.fromkeys([row["requester"], row["owner"]]))
    if not conv or conv.get("kind") != "task" or conv.get("task_id") != row["id"]:
        thread = _task_room(conn, row, parties)
        if conv:
            for message in _rows(conn.execute("SELECT * FROM messages WHERE conversation_id=?", (conv["id"],))):
                if message_task_id(message, conv) == row["id"]:
                    conn.execute("UPDATE messages SET conversation_id=? WHERE id=?", (thread["id"], message["id"]))
        conv = thread
    conn.execute("UPDATE conversations SET participants_json=? WHERE id=?", (_dump(parties), conv["id"]))
    return task(conn, row["id"])


def bot_readable_types(conn):
    """The ids of the types whose every task any bot may read."""
    return [r[0] for r in conn.execute("SELECT id FROM task_types WHERE bots IN (%s)"
                                       % ",".join("?" * len(TYPE_BOTS)), TYPE_BOTS)]


def _type_writer(conn, actor, mover):
    _writer(conn, actor)
    if not (actor == KEEPER or mover or mover is None and can_move(conn, actor)):
        refuse(conn, actor, "identity", "Task types are managed by movers")


def _type_name(conn, actor, name, type_id=None):
    name = str(name or "").strip()
    if not name:
        refuse(conn, actor, "lint", "Give the type a name")
    existing = type_get(conn, name)
    if existing and existing["id"] != type_id:
        refuse(conn, actor, "duplicate", "A task type already has that name")
    return name


def _type_steps(conn, actor, type_id, steps):
    from . import check_pools, step_flow
    typ = type_get(conn, type_id) or {}
    existing = {row["id"]: row for row in _rows(conn.execute("SELECT * FROM task_steps WHERE type_id=?", (type_id,)))}
    pools = check_pools._ready(conn)
    flows = step_flow._ready(conn)
    # A new step's public name is its name only on a type whose tasks are public by default; on a private one (the
    # Product roadmap) an internal step name does not go public unless someone gives it a public name.
    unnamed = "" if typ.get("default_visibility", "private") != "public" else None
    clean, names, ids = [], set(), set()
    for position, raw in enumerate(steps):
        name = str(raw.get("name") or "").strip()
        status = raw.get("status")
        ident = raw.get("id") or new_id()
        if not name or name in names or ident in ids:
            refuse(conn, actor, "kind", "Steps need distinct names and ids")
        if raw.get("id") and ident not in existing:
            refuse(conn, actor, "not-found", "That step does not belong to this type")
        if status not in TASK_STATUSES:
            refuse(conn, actor, "kind", f"A step status is {'|'.join(MADE_STEP_STATUSES)}")
        if status == "declined" and existing.get(ident, {}).get("status") != status:
            refuse(conn, actor, "kind", "A step can't be declined: a declined task goes to the closed step. A step "
                                        f"status is {'|'.join(MADE_STEP_STATUSES)}")
        names.add(name); ids.add(ident)
        pool, limit = check_pools.clean(conn, actor, raw, existing.get(ident)) if pools else (None, None)
        flow = step_flow.clean(conn, actor, raw, existing.get(ident)) if flows else {}
        clean.append({"id": ident, "type_id": type_id, "name": name, "check_pool": pool, "check_limit": limit,
                      "flow": flow,
                      "position": raw.get("position") if raw.get("position") is not None else position,
                      "status": status, "public_name": str(raw.get("public_name") if raw.get("public_name") is not None else
                          existing.get(ident, {}).get("public_name", name if unnamed is None else unnamed)).strip()})
    def in_trash(ident):
        return _has_table(conn, "task_trash") and _one(conn,
            "SELECT 1 FROM task_trash WHERE purged_at IS NULL "
            "AND json_extract(rows_json,'$.rows.tasks[0].step_id')=? LIMIT 1", (ident,))

    def in_use(ident):
        return _one(conn, "SELECT 1 FROM tasks WHERE step_id=? LIMIT 1", (ident,)) or in_trash(ident)

    if flows:
        step_flow.resolve(conn, actor, clean)
    for row in clean:
        if (row["id"] in existing and row["status"] != existing[row["id"]]["status"]
                and in_use(row["id"])):
            refuse(conn, actor, "in-use", "Move tasks off this step before changing its status, including tasks in the trash, or create a new step")
    removed = set(existing) - ids
    for ident in removed:
        # A waiting step's tickets are its holds' places, not work in the way: they move, below.
        if in_trash(ident) or existing[ident]["status"] != "waiting" and in_use(ident):
            refuse(conn, actor, "in-use", "Move tasks off this step before deleting it, including tasks in the trash")
    from . import redesign_migration
    named = redesign_migration.wakes_naming(conn, type_id, [(i, existing[i]["name"]) for i in removed])
    if named:
        refuse(conn, actor, "in-use", "Change or delete these wake rules first, they name a step being deleted: "
                                      + "; ".join(named))
    # A ticket in a waiting step that goes goes back to the step its hold came from while that still exists and works,
    # else to On Deck, before the step row does. Only steps that stay and keep their status are landing places.
    kept = sorted((r for r in clean if r["id"] in existing and existing[r["id"]]["status"] == r["status"]),
                  key=lambda r: (r["position"], r["id"]))
    working = {r["id"] for r in kept if r["status"] in ("open", "doing", "review", "ready")}
    deck = (next((r["id"] for r in kept if r["name"] == "On Deck" and r["status"] == "open"), None)
            or next((r["id"] for r in kept if r["status"] == "open"), None))
    moving = []
    for ident in removed:
        if existing[ident]["status"] == "waiting":
            for task in _rows(conn.execute("SELECT * FROM tasks WHERE step_id=?", (ident,))):
                target = task.get("blocked_from_step") if task.get("blocked_from_step") in working else deck
                if not target:
                    refuse(conn, actor, "in-use", "Move tasks off this step before deleting it: no step is left for them")
                moving.append((task, target))
    for task, target in moving:
        _set_status(conn, KEEPER, task, step=target, note="Its waiting step was removed")
    for ident in removed:
        conn.execute("DELETE FROM task_steps WHERE id=?", (ident,))
    # Temporary names let retained steps swap names without dropping referenced rows. Only a step being renamed
    # needs one: a write to a step is a change to every task in it (backend/events.py).
    for row in clean:
        if row["id"] in existing and existing[row["id"]]["name"] != row["name"]:
            conn.execute("UPDATE task_steps SET name=? WHERE id=?", (new_id(), row["id"]))
    for row in clean:
        conn.execute("INSERT INTO task_steps(id,type_id,name,position,status,public_name) "
                     "VALUES(:id,:type_id,:name,:position,:status,:public_name) ON CONFLICT(id) DO UPDATE SET "
                     "name=excluded.name,position=excluded.position,status=excluded.status,public_name=excluded.public_name", row)
        if pools:
            conn.execute("UPDATE task_steps SET check_pool=:check_pool,check_limit=:check_limit WHERE id=:id", row)
        if flows:
            conn.execute("UPDATE task_steps SET flow_json=? WHERE id=?",
                         (json.dumps(row["flow"], sort_keys=True) if row["flow"] else None, row["id"]))
    renamed = {existing[r["id"]]["name"]: r["name"] for r in clean
               if r["id"] in existing and existing[r["id"]]["name"] != r["name"]}
    if renamed:
        from .rules import steps_renamed
        steps_renamed(conn, type_id, renamed)       # the board's rules follow its steps' new names


def type_create(conn, actor, name, steps=(), mover=None, bots=None, numbered=False, steps_only=False, default_visibility='private'):
    _type_writer(conn, actor, mover)
    name = _type_name(conn, actor, name)
    bots = _type_bots(conn, actor, bots)
    ident, ts = new_id(), now()
    conn.execute("INSERT INTO task_types(id,name,bots,numbered,steps_only,created,updated) VALUES(?,?,?,?,?,?,?)",
                 (ident, name, bots, int(bool(numbered)), int(bool(steps_only)), ts, ts))
    if default_visibility not in ('public', 'private'):
        refuse(conn, actor, 'kind', 'default_visibility is public or private')
    conn.execute('UPDATE task_types SET default_visibility=? WHERE id=?', (default_visibility, ident))
    _type_steps(conn, actor, ident, steps)
    from .roadmap_flow import claim as claim_roles
    claim_roles(conn)
    from . import step_flow
    step_flow.seed(conn)        # a new Dev tickets type starts with the Dev board's flow
    row = type_get(conn, ident)
    event(conn, actor, "task_type.create", ident, row)
    return row


def type_update(conn, actor, type_id, name=None, steps=None, mover=None, bots=None, numbered=None, steps_only=None, default_visibility=None):
    _type_writer(conn, actor, mover)
    row = type_get(conn, type_id)
    if not row:
        refuse(conn, actor, "not-found", "No such task type")
    if row["id"] == GENERAL_TYPE:
        refuse(conn, actor, "built-in", "General keeps the standard statuses")
    if default_visibility is not None:
        if default_visibility not in ('public', 'private'):
            refuse(conn, actor, 'kind', 'default_visibility is public or private')
        conn.execute('UPDATE task_types SET default_visibility=? WHERE id=?', (default_visibility, row['id']))
    if name is not None:
        name = _type_name(conn, actor, name, row["id"])
        conn.execute("UPDATE task_types SET name=? WHERE id=?", (name, row["id"]))
        from .roadmap_flow import claim as claim_roles
        claim_roles(conn)
    if numbered is not None:
        conn.execute("UPDATE task_types SET numbered=? WHERE id=?", (int(bool(numbered)), row["id"]))
    if steps_only is not None:
        conn.execute("UPDATE task_types SET steps_only=? WHERE id=?", (int(bool(steps_only)), row["id"]))
    if steps is not None:
        _type_steps(conn, actor, row["id"], steps)
        from . import check_pools
        check_pools.safely(conn, check_pools.drain)     # a pool that grew may take a check that waits
    if bots is not None:
        conn.execute("UPDATE task_types SET bots=? WHERE id=?", (_type_bots(conn, actor, bots), row["id"]))
    conn.execute("UPDATE task_types SET updated=? WHERE id=?", (now(), row["id"]))
    after = type_get(conn, row["id"])
    event(conn, actor, "task_type.update", row["id"], after)
    return after


def type_delete(conn, actor, type_id, mover=None):
    _type_writer(conn, actor, mover)
    row = type_get(conn, type_id)
    if not row:
        refuse(conn, actor, "not-found", "No such task type")
    if row["id"] == GENERAL_TYPE:
        refuse(conn, actor, "built-in", "General keeps the standard statuses")
    if _one(conn, "SELECT id FROM tasks WHERE type_id=? LIMIT 1", (row["id"],)):
        refuse(conn, actor, "in-use", "Move tasks to another type before deleting it")
    conn.execute("DELETE FROM task_steps WHERE type_id=?", (row["id"],))
    conn.execute("DELETE FROM task_types WHERE id=?", (row["id"],))
    event(conn, actor, "task_type.delete", row["id"], {"name": row["name"]})
    return row


def task_reopen_hint(actor, row):
    """Name a next move the caller is allowed to make; closure retains its own authority."""
    closer = row.get('closed_by') or row['requester']
    if row['status'] == 'closed' and is_bot(actor) and (actor != row['requester'] or is_human(closer)):
        return f"ask {actor_id(closer)} to reopen it, or file a new task"
    if actor == row['owner'] or is_human(actor) or (actor == row['requester'] and row['status'] == 'closed'):
        return f"reopen it with `hub task update {row['id']} --status doing`, or file a new task"
    return "ask the owner to reopen it with " + f"`hub task update {row['id']} --status doing`, or file a new task"


def _task_state(conn, actor, row, status=None, type=None, step=None):
    """Resolve the step before checking permissions on its effective status."""
    typ = type_get(conn, type if type is not None else row.get("type_id") or GENERAL_TYPE)
    if not typ:
        refuse(conn, actor, "not-found", "No such task type")
    effective = status if status is not None else row.get("status") or "open"
    if (type is not None and typ['id'] != row.get('type_id') and row.get('blocked_kind') == 'gate'
            and status is None and step is None):
        effective = row.get('blocked_from_status') or effective
    current = next((s for s in typ["steps"] if s["id"] == row.get("step_id")), None)
    if step is not None:
        chosen = next((s for s in typ["steps"] if s["id"] == step), None)
        chosen = chosen or next((s for s in typ["steps"] if s["name"] == step), None)
        if not chosen and step != "":
            refuse(conn, actor, "not-found", "No such step in this task type")
        if chosen:
            effective = chosen["status"]
    elif typ.get("role") == "engineering" and row.get("blocked_from_step") and status != "open" and effective in ("open", "doing") and (resume := next(
            (s for s in typ["steps"] if s["id"] == row["blocked_from_step"]), None)):
        chosen, effective = resume, resume["status"]
    elif current and current["status"] == effective:
        chosen = current
    elif typ.get("steps_only") and effective == "closed" and _closes_by_hand(actor):
        # A person's close on a steps-only type lands on its last closed step (Closed, Done), or none.
        chosen = next((s for s in reversed(typ["steps"]) if s["status"] == "closed"), None)
    else:
        # A team's own waiting step takes a task only when someone moves it there, never by a status change.
        chosen = next((s for s in typ["steps"] if s["status"] == effective), None) if effective != "waiting" else None
        if effective == 'open' and row.get('status') in ('doing', 'waiting', 'review', 'done'):
            from .review_gate import engineering
            if engineering(conn, {**row, 'type_id': typ['id']}):
                chosen = next((s for s in typ['steps'] if s['name'] == 'On Deck' and s['status'] == 'open'), chosen)
        if not chosen and effective == "waiting":
            # Waiting is not a column: the task stays in the working step it is in, or starts at the first.
            chosen = (current if current and current["status"] in ACTIVE_STATUSES
                      else next((s for s in typ["steps"] if s["status"] in ("open", "doing")), None))
        elif not chosen and effective == "declined":
            chosen = next((s for s in typ["steps"] if s["status"] == "closed"), None)
    if row.get('blocked_since') and effective in ('open', 'doing', 'review', 'ready'):
        from . import task_blocked, review_gate
        held = task_blocked.wait(conn, row, include_manual=False)
        if held or (step is None and effective in ('open', 'doing') and row.get('blocked_kind') == 'gate'
                    and review_gate.problem(conn, row, row['owner'])):
            # A hold is a mark on the ticket, not a column: a held ticket keeps the step it is in (or the step it was
            # moved to) and its status is waiting, which is what wakes, claims and the build clock read.
            effective = 'waiting'
            if step is None and current:
                chosen = current
    if effective not in TASK_STATUSES:
        refuse(conn, actor, "kind", f"A status is {'|'.join(TASK_STATUSES)}, not {effective}")
    # On a steps-only type a task is finished or brought back only by a step move: a bare close, decline,
    # done or reopen would land it in whichever step shares that status. Working statuses (an ask's
    # waiting, a turn's doing) still change, as do filing onto the type and the keeper's own automation.
    # A person can always close one: closing takes it off the board, whatever its type.
    # Its owner may also reopen a Done one with doing or open, which lands on that status's working step.
    if (typ.get("steps_only") and step is None and actor != KEEPER and row.get("status")
            and typ["id"] == (row.get("type_id") or GENERAL_TYPE) and effective != row["status"]
            and not (effective in ACTIVE_STATUSES and row["status"] in ACTIVE_STATUSES)
            and not (actor == row.get('owner') and row['status'] == 'done' and effective in ('open', 'doing'))
            and not (effective == "closed" and _closes_by_hand(actor))):
        # Naming the task keeps a bot's refusals on different tickets distinct, so working a board is not
        # counted as retrying one refused write (rule 8); a retry on the same task still is.
        which = f"#{row['number']}" if row.get("number") else f'"{_clip(row.get("title") or "", 80)}"'
        steps = ", ".join(s["name"] for s in typ["steps"])
        refuse(conn, actor, "kind", f"{which} is a {typ['name']} task: it changes status only by moving it to a step "
                                    f"({steps}), not by closing, declining, reopening or finishing it; move it, or delete it",
               context={"what": f"task {row.get('id')}"})
    return typ["id"], chosen["id"] if chosen else None, effective


def _closes_by_hand(actor):
    return (actor or "").startswith("human:")


# The note on a waiting task's move out of a retired waiting step: its step came from its history, not from a move
# anyone made (task_blocked.forget_retired_resumes).
RETIRED_WAITING = "Waiting is a tag now, not a step"


def retire_held_steps(conn):
    """Take the Declined (Won't do) steps off every type but the Product roadmap, whose Declined is public. A task on
    one goes to its type's closed step, or no step when there is none, and keeps status declined. A waiting step stays:
    it is the team's own column for parked work. Typed waiting or declined tasks with no step are placed too: a waiting
    one in the working step it last left (its step history), else its type's first open or doing step. Runs at every
    start; once nothing is left to move it only reads."""
    from .roadmap_flow import PRODUCT
    held = _rows(conn.execute("SELECT s.id,s.type_id,s.status FROM task_steps s JOIN task_types ty ON ty.id=s.type_id "
                              "WHERE s.status='declined' AND coalesce(ty.role,'')<>?", (PRODUCT,)))
    loose = _rows(conn.execute("SELECT t.id,t.type_id,t.status FROM tasks t JOIN task_types ty ON ty.id=t.type_id "
                               "WHERE t.step_id IS NULL AND t.status IN ('waiting','declined') AND coalesce(ty.role,'')<>?",
                               (PRODUCT,)))
    if not held and not loose:
        return
    gone = {row["id"] for row in held}
    version = "version" in {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    conn.execute("SAVEPOINT retire_held_steps")
    try:
        moving = [(t["id"], t["type_id"], t["status"]) for t in loose]
        for step in held:
            moving += [(r[0], step["type_id"], "declined")
                       for r in conn.execute("SELECT id FROM tasks WHERE step_id=?", (step["id"],))]
        steps = {}
        for task_id, type_id, held_as in moving:
            if type_id not in steps:
                steps[type_id] = [s for s in _rows(conn.execute(
                    "SELECT id,status FROM task_steps WHERE type_id=? ORDER BY position,id", (type_id,))) if s["id"] not in gone]
            mine = steps[type_id]
            if held_as == "waiting":
                working = {s["id"] for s in mine if s["status"] in ACTIVE_STATUSES}
                left = (r[0] for r in conn.execute("SELECT old FROM task_events WHERE task_id=? AND field='step' "
                                                   "ORDER BY ts DESC,rowid DESC", (task_id,)))
                target = next((old for old in left if old in working), None) \
                    or next((s["id"] for s in mine if s["status"] in ("open", "doing")), None)
            else:
                target = next((s["id"] for s in mine if s["status"] == "closed"), None)
            current = conn.execute("SELECT step_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            if target == current:
                continue
            conn.execute("UPDATE tasks SET step_id=?,step_rank=?" + (",version=version+1" if version else "") + " WHERE id=?",
                         (target, _step_end(conn, target, task_id) if target else None, task_id))
            _task_event(conn, task_id, KEEPER, "step", current, target,
                        RETIRED_WAITING if held_as == "waiting" else "Declined goes to Closed now, not a step")
        for ident in gone:
            conn.execute("DELETE FROM task_steps WHERE id=?", (ident,))
    except BaseException:
        conn.execute("ROLLBACK TO retire_held_steps")
        raise
    finally:
        conn.execute("RELEASE retire_held_steps")


def steps_only(conn, row):
    """The step names of the task's type when it finishes and reopens its tasks only by a step move
    (`_task_state`), else an empty list."""
    typ = type_get(conn, row.get("type_id") or GENERAL_TYPE) or {}
    return [s["name"] for s in typ["steps"]] if typ.get("steps_only") else []


def declared_hold(conn, actor, row, step, step_id):
    """A move by hand into a waiting step declares a hold, which never reopens by itself: the owner's move into a
    waiting step, or a person's move into one. A hold Tico finds keeps the ticket's step, so no move into a working
    step counts."""
    if step is None:
        return False
    chosen = _one(conn, "SELECT s.name,s.status FROM task_steps s WHERE s.id=?", (step_id,)) or {}
    if step not in (step_id, chosen.get("name")):
        return False        # a hold put it elsewhere than the step it was moved to
    if chosen.get("status") != "waiting":
        return False
    if actor == row.get("owner"):
        return True
    return is_human(actor)


def _set_status(conn, actor, row, status=None, type=None, step=None, note="", *, skip_step_wake=()):
    """Every task status write keeps its type and step consistent, including old runners. A task
    that enters a step joins its end; one that lands on a numbered type without a number gets one."""
    type_id, step_id, effective = _task_state(conn, actor, row, status, type, step)
    ts = now()
    from .task_blocked import transition
    transition(conn, row, effective, step_id, note, ts,
               manual=effective == 'waiting' and declared_hold(conn, actor, row, step, step_id), stepped=step is not None)
    conn.execute("UPDATE tasks SET status=?,type_id=?,step_id=?,updated=? WHERE id=?",
                 (effective, type_id, step_id, ts, row["id"]))
    if row.get("type_id") != type_id:
        _task_event(conn, row["id"], actor, "type", row.get("type_id"), type_id, note)
        _next_number(conn, actor, row["id"], type_id, note)
    if row.get("step_id") != step_id:
        conn.execute("UPDATE tasks SET step_rank=? WHERE id=?",
                     (_step_end(conn, step_id, row["id"]) if step_id else None, row["id"]))
        _task_event(conn, row["id"], actor, "step", row.get("step_id"), step_id, note)
    if row.get("status") != effective:
        _task_event(conn, row["id"], actor, "status", row.get("status"), effective, note)
        if row.get("occurrence"):
            # A routine's task for a rule's due date: completing it marks that day Satisfied, reopening takes it back.
            from . import rule_due
            rule_due.on_task_status(conn, actor, row, row.get("status"), effective)
        # Someone moving a bot's finished result on is what became of the run that made it (backend/run_triggers.py).
        RT.turn_result(conn, actor, row, RT.result_outcome(row.get("status"), effective), event, effective)
        if effective in ("done", "closed", "declined", "review"):
            TS.clear_finished(conn, actor, row)     # review hands it on: the requester looks now, not at the date
        if effective in ("done", "closed", "declined"):
            # A finished task's open questions to people are moot, every kind. A question lives and dies with its own
            # task: one on an open task below it carries on (an open subtask says it is still needed), so a bot asking on
            # a subtask and then finishing the parent no longer withdraws it seconds later (Dev 627).
            # A done task asks its requester to accept what it delivered only after this, so that question stays.
            from . import task_holds
            task_holds.withdrawn(conn, row["id"], supersede_asks(conn, row["id"]),
                                 f"its task was {TASK_ENDED[effective]}", by=actor)
            supersede_ask_cards(conn, row, effective)
            # ...and so is a yes it was waiting for: nothing will act on it now.
            withdraw_task_approvals(conn, row["id"], f"Its task was {TASK_ENDED[effective]}.")
            # Its parent and the tasks it blocked lose a hold: their reason follows, and the last one clearing frees them.
            task_holds.ended(conn, {**row, "status": effective}, effective)
        elif row.get("status") in ("done", "closed", "declined"):
            # Reopened: it holds its parent and the tasks it blocks again.
            from . import task_holds
            task_holds.holders(conn, row["id"])
    from .roadmap_flow import changed as roadmap_changed
    # A step chosen by hand holds: the keeper re-syncs the item from its tickets when one of them moves next.
    roadmap_changed(conn, row["id"], itself=actor == KEEPER or step is None)
    if row.get("status") != effective:
        from .ticket_notices import moved as ticket_moved
        ticket_moved(conn, actor, row["id"], row.get("status"), effective)
    if step_id and row.get("step_id") != step_id and row.get("status") is not None:
        # A step entered is news a type's rules may route (QA Fixes to the Builder, In QA to QA, ...).
        from . import rules as wake_rule_engine
        wake_rule_engine.step_entered(conn, actor, task(conn, row["id"]), skip=skip_step_wake)
    if row.get("status") != effective and row.get("status") is not None:
        from .check_blocked import changed as check_changed
        check_changed(conn, row["id"])      # a Review or QA check's ticket shows it blocked (Dev 553)
    if row.get("status") in ACTIVE_STATUSES and effective not in ACTIVE_STATUSES:
        # A checker that finished a check takes the oldest one waiting in its pools (backend/check_pools.py).
        from . import check_pools
        check_pools.safely(conn, check_pools.freed, row)
    return effective


OPEN_ASK_SQL = ("m.answered_by IS NULL AND m.superseded_at IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM messages a WHERE a.in_reply_to=m.id AND a.kind='answer')")
# The one ask a done task `t` keeps: asking its requester, with structured questions, to accept what it delivered.
# Asked once the task is done; whatever was asked before that was about the work, and ended with it (_set_status).
ACCEPTANCE_ASK_SQL = ("t.status='done' AND json_type(m.refs_json,'$.questions')='array' AND m.to_actor=t.requester "
                      "AND m.created>=coalesce(t.done_at,'') AND json_extract(m.refs_json,'$.stuck') IS NOT 1")


def ask_clock(message):
    """When this ask was last put to the person: when it was written, or when the bot last asked it again and it was
    folded in (fold_question). Its age, its "older" move and its default's days run from here."""
    refs = (message or {}).get("refs")
    if not isinstance(refs, dict):
        refs = _json((message or {}).get("refs_json"), {}) or {}
    return refs.get("asked_again_at") or (message or {}).get("created")


def ask_older(message, at=None):
    """True when this unanswered ask has waited two business days (backend/still_needed.py). It stays in the person's
    queue, marked stale, until that weekday morning's batch sends it back to its bot."""
    from .still_needed import crossed_at
    if not parse_ts(ask_clock(message)):
        return False
    return crossed_at(ask_clock(message)) <= (at or datetime.now(timezone.utc))


def supersede_asks(conn, task_id, *, from_actor=None, to_actor=None, keep=None, why=None):
    """Mark the task's unanswered asks to people superseded: those `from_actor` sent `to_actor` before the ask `keep`,
    or all of them. They stay
    in the thread and leave every queue and count. A question to one person never replaces a different person's
    question. `why` is the task event's note."""
    # Only the task's own thread, where its queue and counts look (open_task_asks): a lookup by conversation, not a
    # scan of every pending ask on the server each time a task finishes.
    sql = ("SELECT m.id FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
           "WHERE m.conversation_id=(SELECT conversation_id FROM tasks WHERE id=?) "
           f"AND m.kind='ask' AND m.deleted_at IS NULL AND m.to_actor LIKE 'human:%' AND {OPEN_ASK_SQL} "
           f"AND {MESSAGE_TASK_SQL}=?")
    args = [task_id, task_id]
    if from_actor:
        sql += " AND m.from_actor=?"
        args.append(from_actor)
    if to_actor:
        sql += " AND m.to_actor=?"
        args.append(to_actor)
    if keep:
        sql += " AND m.id<>?"
        args.append(keep)
    ids = [r[0] for r in conn.execute(sql, args)]
    if ids:
        conn.executemany("UPDATE messages SET superseded_at=? WHERE id=?", [(now(), mid) for mid in ids])
        _task_event(conn, task_id, KEEPER, "asks_superseded", None, str(len(ids)),
                    why or ("Replaced by a newer question" if keep else "The task finished"))
        recount(conn, asking_actors(conn, ids))     # each asking bot's needs_human counts its open asks
    return ids


def asking_actors(conn, ask_ids):
    """Who sent these asks."""
    if not ask_ids:
        return []
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT from_actor FROM messages WHERE id IN ({','.join('?' * len(ask_ids))})", list(ask_ids))]


def drop_asked_waits(conn, task_id, ask_ids, why):
    """A task left waiting on a person for asks that were just superseded (`ask_ids`) waits on nobody once none of its
    questions to them is open: it leaves their Needs you with its questions."""
    if not ask_ids:
        return
    row = task(conn, task_id)
    person = (row or {}).get("waiting_on")
    if not person or row["status"] != "waiting":
        return
    marks = ",".join("?" * len(ask_ids))
    if not conn.execute(f"SELECT 1 FROM messages WHERE id IN ({marks}) AND to_actor=?", (*ask_ids, person)).fetchone():
        return
    if any(a["to_actor"] == person for a in open_task_asks(conn, row)):
        return
    conn.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (task_id,))
    _task_event(conn, task_id, KEEPER, "waiting_on", person, None, why)
    _recount(conn, row["owner"])            # its owner's needs_human counted this wait


def _step_end(conn, step_id, task_id, top=False):
    """The step_rank that puts a task at the end (or the top) of a step's other tasks."""
    lo, hi = conn.execute("SELECT MIN(step_rank), MAX(step_rank) FROM tasks WHERE step_id=? AND id<>?",
                          (step_id, task_id)).fetchone()
    if top:
        return (lo if lo is not None else 1.0) - 1.0
    return (hi if hi is not None else 0.0) + 1.0


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


def _next_number(conn, actor, task_id, type_id, note=""):
    """The team's next number (the highest yet, plus one) for a task without one on a numbered type."""
    numbered = _one(conn, "SELECT 1 FROM task_types WHERE id=? AND numbered=1", (type_id,))
    # A deleted task keeps its number in the trash, so a restore never meets it on another task.
    top = ("(SELECT MAX(n) FROM (SELECT COALESCE(MAX(number), 0) AS n FROM tasks "
           "UNION ALL SELECT COALESCE(MAX(number), 0) FROM task_trash))")
    if numbered and conn.execute(f"UPDATE tasks SET number={top} + 1 "
                                 "WHERE id=? AND number IS NULL "
                                 f"AND {top}<999999999", (task_id,)).rowcount:
        number = _one(conn, "SELECT number FROM tasks WHERE id=?", (task_id,))["number"]
        _task_event(conn, task_id, actor, "number", None, number, note)
    elif numbered and _one(conn, "SELECT 1 FROM tasks WHERE id=? AND number IS NULL", (task_id,)):
        refuse(conn, actor, "number", "The team's task numbers have reached 999999999")


def _labels(labels):
    """Labels are lower-case free text, one word or a hyphenated few, unique, in the order given."""
    out = []
    for raw in (labels if isinstance(labels, (list, tuple)) else str(labels or "").split(",")):
        one = re.sub(r"\s+", "-", str(raw or "").strip().lower()).strip("-")[:40]
        if one and one not in out:
            out.append(one)
    return out[:20]


# ----------------------------------------------------------------------------- tags
def tag(conn, ident):
    row = _one(conn, "SELECT * FROM tags WHERE id=? OR key=? ORDER BY id=? DESC LIMIT 1",
               (ident, ident, ident))
    return _tag_view(row) if row else None


def tag_by_key(conn, key):
    row = _one(conn, "SELECT * FROM tags WHERE key=?", (key,))
    return _tag_view(row) if row else None


def _tag_view(row):
    value = dict(row)
    value["metadata"] = _json(value.pop("metadata_json"), {}) or {}
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


def tag_create(conn, actor, key, label=None, metadata=None, markdown=None, is_template=False,
               template_id=None, owner=None, mover=None):
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
    conn.execute("INSERT INTO tags(id,key,label,metadata_json,markdown,is_template,template_id,owner,created,updated) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)", (ident, key, label, _dump(defaults),
                 markdown if markdown is not None else template["markdown"] if template else "",
                 int(is_template), template["id"] if template else None, owner, ts, ts))
    event(conn, actor, "tag.create", ident, {"key": key, "template_id": template["id"] if template else None})
    return tag(conn, ident)


def tag_update(conn, actor, ident, version, *, label=None, metadata=None, markdown=None,
               owner=None, mover=None):
    _writer(conn, actor)
    row = tag(conn, ident)
    if not row:
        refuse(conn, actor, "not-found", "no such tag")
    if not tag_can_edit(conn, actor, row, mover):
        refuse(conn, actor, "identity", "this tag is edited by its owner or a task mover")
    if row["version"] != version:
        refuse(conn, actor, "version_conflict", "Tag changed; fetch it and retry your update")
    fields = {k: v for k, v in (("label", label), ("metadata_json", _dump(metadata) if metadata is not None else None),
                                ("markdown", markdown), ("owner", owner)) if v is not None}
    if label is not None and not label.strip():
        refuse(conn, actor, "kind", "give the tag a label")
    if not fields:
        return row
    if "owner" in fields:
        fields["owner"] = fields["owner"] or None
    sets = ",".join(f"{name}=?" for name in fields)
    changed = conn.execute(f"UPDATE tags SET {sets},updated=?,version=version+1 WHERE id=? AND version=?",
                           (*fields.values(), now(), row["id"], version)).rowcount
    if not changed:
        refuse(conn, actor, "version_conflict", "Tag changed; fetch it and retry your update")
    event(conn, actor, "tag.update", row["id"], {"fields": list(fields), "version": version + 1})
    return tag(conn, row["id"])


TASK_TAGS_SQL = ("SELECT tags.*,task_tags.task_id FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                 "WHERE task_tags.task_id IN (%s) ORDER BY task_tags.rowid")


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
    if not approved and task_row['visibility'] == 'public' and (set(keys) - set(old)) & set(roadmap_settings(conn)['labels']):
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
        if actor != KEEPER and ("qa" in old) != ("qa" in keys):
            from .repositories import metadata, save_metadata
            saved = metadata(conn, "qa-default:" + task_id)
            if saved:
                save_metadata(conn, "qa-default:" + task_id, {**saved, "author": "qa" in keys})


def type_manager(conn, row):
    """The slug of the bot that manages this task's type (task_types.manager), or None."""
    return (_one(conn, "SELECT manager FROM task_types WHERE id=?", (row.get("type_id") or "",)) or {}).get("manager")


def _label_writer(conn, actor, row, labels, approval_id=None):
    """A bot that is not a mover changing labels on `row` (task_update): it may change a classification label
    on a task it manages, one it owns or asked for or whose type it manages (task_types.manager), and nothing
    else. A label Tico acts on (WORKFLOW_LABELS) stays a mover's. A label on a public roadmap puts the task
    there once it is public, so adding or removing one takes the bot's exact approved publish approval
    (roadmaps.approve), or a mover when the team does not ask bots for one. Refused as rule `label`, which
    counts once per task an hour (_escalate). Returns whether the approval was used."""
    from .roadmaps import settings as roadmap_settings, approve as approve_roadmap
    keys = _labels(labels)
    changed = sorted(set(keys) ^ set(task_labels(row)))
    if not changed:
        return False
    slug, teams = actor_id(actor), ", ".join(MOVER_TEAMS)
    if not task_participant(conn, actor, row) and type_manager(conn, row) != slug:
        refuse(conn, actor, "label",
               f"{slug} changes labels only on a task it owns, asked for, whose party it manages or whose type it manages; labels on "
               f"{row['id']} are changed by a person on the {teams} teams", task=row["id"])
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
            refuse(conn, actor, "label",
                   f"approval {approval_id} does not cover this change: a publish approval is used once, must be "
                   f"approved, and must match `hub task publication-preview {row['id']}` with a --label for each "
                   f"label the task keeps; request a new one, or ask a person on the {teams} teams", task=row["id"])
        return True
    held = _held_labels(flow, public, teams, (
        f"it needs an approved publish approval: get the payload with `hub task publication-preview {row['id']}` and "
        f"a --label for each label the task keeps, request the approval, then retry with its id (--approval-id), or "
        f"ask a person on the {teams} teams" if settings["bot_public_approval"] else
        f"a person on the {teams} teams changes it"))
    if held:
        rest = [k for k in changed if k not in public and k not in flow]
        refuse(conn, actor, "label", "; ".join(held) + (f". {', '.join(rest)} you may change on its own" if rest else ""),
               task=row["id"])
    return False


def _held_labels(flow, public, teams, publish):
    """The clauses of a `label` refusal: `flow` labels Tico acts on, `public` roadmap labels and what `publish`
    says the bot does about them."""
    held = []
    if flow:
        held.append(f"{', '.join(flow)} {'changes' if len(flow) == 1 else 'change'} how Tico runs the task, so "
                    f"a person on the {teams} teams changes {'it' if len(flow) == 1 else 'them'}")
    if public:
        held.append(f"{', '.join(public)} {'is a public roadmap label' if len(public) == 1 else 'are public roadmap labels'}"
                    f" (adding or removing one puts the task on a roadmap or takes it off), so " + publish)
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
    held = _held_labels(flow, public, teams, (
        f"it needs an approved publish approval, which is for a task that exists: create the task without "
        f"{'it' if len(public) == 1 else 'them'}, then add {'it' if len(public) == 1 else 'them'} on the task with the "
        f"approval (`hub task publication-preview <id>`, then --approval-id), or ask a person on the {teams} teams"
        if settings["bot_public_approval"] else f"a person on the {teams} teams adds {'it' if len(public) == 1 else 'them'}"))
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
    lo, hi = conn.execute(f"SELECT MIN(rank), MAX(rank) FROM tasks WHERE owner=? AND status IN ({marks})",
                          (owner, *ACTIVE_STATUSES)).fetchone()
    if top:
        return (lo if lo is not None else 1.0) - 1.0
    return (hi if hi is not None else 0.0) + 1.0


def _unblock(conn, done_task, told=()):
    """A finished task frees whatever waited on it; a bot owner hears about it once nothing else
    blocks the task. `told` are the tasks already woken about it (_answered)."""
    from .roadmap_flow import delivers
    # A quiet task a run deferred until this one is done gets its run (backend/quiet_work.py).
    from . import quiet_work
    quiet_work.finished(conn, done_task)
    for blocked_id in TR.blocked_ids(conn, done_task["id"]):
        row = task(conn, blocked_id)
        if task_private(conn, done_task) and not subtask_flows_up(conn, done_task, row) and (
                not task_private(conn, row) or not all(
                    task_private_readable(conn, party, done_task) for party in (row['owner'], row['requester']))):
            continue                  # do not copy private completion into a wider task
        # A roadmap item keeps its finished Dev tickets: they are what it shipped, and it follows one that reopens.
        # A done blocker blocks nothing (live_blocker).
        kept = delivers(conn, row, done_task)
        if not kept:
            TR.remove_blocker(conn, KEEPER, row["id"], done_task["id"], keeper_note=f"unblocked: {done_task['title']}")
        conn.execute("UPDATE tasks SET version=version+1 WHERE id=?", (row["id"],))
        # Only a blocker still open holds the wake: a kept, finished ticket blocks nothing. A kept ticket that finishes
        # again (reopened, then done) does not wake the item's owner twice.
        waits = TR.live_blocker(conn, row["id"], ACTIVE_STATUSES) or kept and _one(
            conn, "SELECT 1 FROM messages WHERE conversation_id=? AND json_extract(refs_json,'$.task')=? "
                  "AND json_extract(refs_json,'$.trigger_source')=? AND body LIKE 'Unblocked: %' LIMIT 1",
            (row["conversation_id"], row["id"], done_task["id"]))
        if row["status"] in ACTIVE_STATUSES and not waits and row["id"] not in told:
            outcome = "was declined" if done_task["status"] == "declined" else "is finished"
            _wake(conn, row, row["owner"], f"Unblocked: {row['title']}\n{done_task['title']} {outcome}.",
                  refs={"blocker_done": done_task["id"], "blocker_status": done_task["status"]}, source=done_task["id"])


def _answered(conn, actor, task_id, card, told=None):
    """`card`, an ask this rule filed (ask_card) that a bot's task is blocked on, is done, declined or closed by `actor`: the
    task's note becomes the answer, it opens again when nothing else holds it, it no longer waits on a declined card,
    and its bot is woken on it once, unless `told` (_subtask_wake) already woke it there. The note names the person
    as answering only when they finished the card, so a bot closing it never puts its words in their mouth. Returns
    whether this was such a task."""
    person = ask_card(conn, card)
    row = task(conn, task_id)
    if not person or not row or not is_bot(row["owner"]) or row["status"] not in ACTIVE_STATUSES:
        return False
    if task_private(conn, card) and not task_private(conn, row):
        return False
    name = person_name(conn, person)
    said = str(card.get("note") or "").strip() or {"done": "Done.", "declined": "Declined."}.get(card["status"], "")
    closer = "Tico" if actor == KEEPER else person_name(conn, actor)   # the keeper closes an ask that expired
    if card["status"] == "closed":
        who = f"{name} closed the ask" if actor == person else f"{closer} closed the ask to {name}"
    else:
        who = f"{name} answered" if actor == person else f"{closer} closed the ask to {name}"
    note = f"{who}: {said}" if said else f"{who}."
    if card["status"] != "done":
        TR.remove_blocker(conn, KEEPER, row["id"], card["id"], keeper_note=f"answered: {card['title']}")
    row = task(conn, row["id"])
    if row["status"] == "waiting" and not waiting_for(conn, row):
        # A board ticket held on the ask goes back to the step it left, as any hold that clears does; opening it would
        # send it to the board's first open step and drop its place (a review it waited at, its verdict).
        from . import task_blocked
        if row.get("blocked_since"):
            task_blocked.reconcile(conn, row["id"])
            row = task(conn, row["id"])
        if row["status"] == "waiting":
            _set_status(conn, KEEPER, row, "open", note=note)
    conn.execute("UPDATE tasks SET note=?, version=version+1 WHERE id=?", (note, row["id"]))
    _task_event(conn, row["id"], KEEPER, "note", row.get("note"), note, "")
    if row["owner"] != told or TR.parent_of(conn, card["id"]) != row["id"]:
        _wake(conn, row, row["owner"], f"{who}: {card['title']}" + (f"\n{said}" if said else ""), refs={"subtask": card["id"]},
              source=card["id"])
    return True


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


def unlinked_pushes(conn, task_id):
    """The task's worktrees whose branch carries pushed commits of the task's own and no pull request link on that
    repository (a link whose branch GitHub has not reported yet counts): work a hand-off would leave with nothing to
    review, merge or release. Pushed is the push webhook's head, or a committed head the computer reports nothing
    unpushed on; the background pass's look at the branch on GitHub (github.reconcile_worktree_prs) settles both, so a
    missed webhook still counts and a branch only moved to newer main does not. A branch that pass can read and has
    not read since its last known head counts too, `unchecked`: a push whose webhook was missed and that no computer
    has reported is on GitHub only. A head the tree started at, or one on the repository's default branch, is no work
    of the task's. Each is {link_id, repo, branch, unchecked}."""
    # The GitHub App's table exists only once its module has set up the store; without it no branch is readable.
    app = conn.execute("SELECT org FROM github_app WHERE id='app'").fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='github_app'").fetchone() else None
    out = []
    for w in _rows(conn.execute("SELECT id,repo,branch,detail_json FROM task_links WHERE task_id=? AND kind='worktree' "
                                "AND coalesce(state,'unknown')<>'removed' AND repo IS NOT NULL AND branch IS NOT NULL",
                                (task_id,))):
        detail = _json(w["detail_json"], {}) or {}
        remote = detail.get("remote") or {}
        heads = {detail.get("pushed"), detail.get("last_commit") if detail.get("ahead") == 0 else None}
        heads -= {None, "", detail.get("expected_head"), *(remote.get("heads") or [])}
        work = remote.get("ahead") or any(
            not conn.execute("SELECT 1 FROM main_pushes WHERE sha=? AND lower(repo)=lower(?)", (head, w["repo"])).fetchone()
            for head in heads)
        readable = (app and re.match(r"^tico/[0-9a-f]{8}-", w["branch"])
                    and w["repo"].lower().startswith(str(app[0] or "").lower() + "/")
                    and conn.execute("SELECT 1 FROM repositories WHERE lower(full_name)=lower(?) AND reachable=1",
                                     (w["repo"],)).fetchone())
        unchecked = bool(readable) and not (remote.get("at") and set(worktree_heads(detail)) <= set(remote.get("heads") or []))
        if (work or unchecked) and not conn.execute(
                "SELECT 1 FROM task_links WHERE task_id=? AND kind='pr' AND lower(repo)=lower(?) "
                "AND (branch=? OR branch IS NULL)", (task_id, w["repo"], w["branch"])).fetchone():
            out.append({"link_id": w["id"], "repo": w["repo"], "branch": w["branch"], "unchecked": not work})
    return out


def _unlinked_text(missing):
    pushed = [m for m in missing if not m["unchecked"]]
    unchecked = [m for m in missing if m["unchecked"]]
    parts = ([("has commits with no pull request linked: " + "; ".join(f"{m['repo']} branch {m['branch']}" for m in pushed))]
             if pushed else []) + ([("has a branch Tico has not checked on GitHub since its last commit or push: "
                                     + "; ".join(f"{m['repo']} branch {m['branch']}" for m in unchecked)
                                     + " (Tico checks it within a few minutes)")] if unchecked else [])
    return ("; and ".join(parts) + ". Open the pull request (Tico links a worktree branch's PR itself) or run "
            "`hub task link <PR URL>`")


@private_task_write
def task_link(conn, actor, task_id, url, title=None, kind=None, mover=None, wake_once=None, release_repo=""):
    """Attach a link to a task. A pull request link is what moves a product-lane task.

    `wake_once` arms (True) or disarms (False) one wake on a pull request link: the next time its checks
    finish, it gets an approving or change-requesting review, or a release ships it, the task's owner hears
    once, in the task's conversation. It never changes the task's status (`github.py`)."""
    _writer(conn, actor)
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
        refuse(conn, actor, "kind", "a one-shot wake needs a pull request link in the connected GitHub App's organization")
    if kind == "pr" and match:
        # GitHub treats owner and repository names case-insensitively, so one pull request is one link.
        # A duplicate made before this matched by exact URL stays reachable through its own URL.
        have = _one(conn, "SELECT * FROM task_links WHERE task_id=? AND kind='pr' AND lower(repo)=? AND number=? "
                          "ORDER BY url=? DESC, created LIMIT 1",
                    (task_id, f"{match.group(1)}/{match.group(2)}".lower(), int(match.group(3)), url))
    else:
        have = _one(conn, "SELECT * FROM task_links WHERE task_id=? AND url=?", (task_id, url))
    if have:
        if wake_once and (have["state"] in ("closed", "shipped") or have["state"] == "merged"
                          and str(have.get("repo") or "").lower() != str(release_repo).lower()):
            refuse(conn, actor, "state", f"{have['title']} is {have['state']}"
                   + (" outside the repository Tico releases" if have["state"] == "merged" else "")
                   + ": no result is left to wake on")
        return _arm_wake_once(conn, actor, have, wake_once)
    title = str(title or "").strip()
    if not title:
        m = PR_URL.match(url) or ISSUE_URL.match(url)
        title = f"{m.group(2)}#{m.group(3)}" if m else urlsplit(url).netloc
    link = {"id": new_id(), "task_id": task_id, "kind": kind, "url": url, "title": title[:200],
            "state": "open" if kind == "pr" else None, "added_by": actor, "created": now()}
    conn.execute("INSERT INTO task_links (id, task_id, kind, url, title, state, added_by, created) "
                 "VALUES (:id, :task_id, :kind, :url, :title, :state, :added_by, :created)", link)
    match = PR_URL.match(url)
    if kind == "pr" and match:
        conn.execute("UPDATE task_links SET repo=?,number=?,updated=? WHERE id=?",
                     (match.group(1) + "/" + match.group(2), int(match.group(3)), now(), link["id"]))
        from .review_gate import engineering
        if engineering(conn, row):
            conn.execute("UPDATE task_links SET detail_json=? WHERE id=?",
                         (json.dumps({"qa_default_pending": True}), link["id"]))
        link = _one(conn, "SELECT * FROM task_links WHERE id=?", (link["id"],))
    _task_event(conn, task_id, actor, "link", None, url, title)
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
    event(conn, actor, "task.link", task_id, {"url": url, "kind": kind})
    return _arm_wake_once(conn, actor, link, wake_once)


def _arm_wake_once(conn, actor, link, wake_once):
    """Arm or disarm a pull request link's one wake; None leaves it as it is."""
    if wake_once is None:
        return link
    detail = _json(link.get("detail_json"), {}) or {}
    if bool(detail.get("wake_once")) == bool(wake_once):
        return link
    if wake_once:
        detail["wake_once"] = {"by": actor, "at": now()}
    else:
        detail.pop("wake_once", None)
    conn.execute("UPDATE task_links SET detail_json=?,updated=? WHERE id=?", (json.dumps(detail), now(), link["id"]))
    event(conn, actor, "task.link.wake_once", link["task_id"], {"url": link["url"], "armed": bool(wake_once)})
    return _one(conn, "SELECT * FROM task_links WHERE id=?", (link["id"],))


# A harness's resume command, optionally after the `cd` into the bot's folder that botResumeCommand writes.
RESUME_COMMAND = re.compile(r"^(?:cd \S+ && )?(claude|codex|gemini|grok|pi|cursor-agent|antigravity)"
                            r"\s+(?:--resume|resume|-r)\s+([\w.:-]+)$")


def _session_detail(link):
    try:
        return json.loads(link.get("detail_json") or "{}")
    except ValueError:
        return {}


def _task_session_link(conn, actor, task_id, value, title):
    """An alternate session someone tracks on a task: a Bot Desk thread (http), a resume command, an Orca
    reference. Tracking only: task chat goes to the owning bot, never here. A value that is not a page is
    stored as `session:<value>` so nothing that opens a link's url treats it as one."""
    if not value or len(value) > 500 or any(ord(ch) < 32 for ch in value):
        refuse(conn, actor, "kind", "a session link is one line: a URL, a resume command or a reference")
    url = value if re.match(r"^https?://\S+$", value) else "session:" + value
    have = _one(conn, "SELECT * FROM task_links WHERE task_id=? AND url=?", (task_id, url))
    if have:
        return have
    resume = RESUME_COMMAND.match(value)
    detail = {"primary": False, **({"harness": resume.group(1), "thread_id": resume.group(2)} if resume else {})}
    title = str(title or "").strip() or (urlsplit(value).netloc if url == value else
                                         f"{resume.group(1)} {resume.group(2)[:8]}" if resume else value[:60])
    link = {"id": new_id(), "task_id": task_id, "url": url, "title": title[:200], "added_by": actor,
            "created": now(), "detail_json": json.dumps(detail)}
    conn.execute("INSERT INTO task_links (id, task_id, kind, url, title, added_by, created, detail_json, updated) "
                 "VALUES (:id, :task_id, 'session', :url, :title, :added_by, :created, :detail_json, :created)", link)
    _task_event(conn, task_id, actor, "link", None, url, title)
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
    event(conn, actor, "task.link", task_id, {"url": url, "kind": "session"})
    return _one(conn, "SELECT * FROM task_links WHERE id=?", (link["id"],))


def _demote_sessions(conn, task_id, owner=None):
    """At most one primary session per task: the owning bot's (`owner` is the task's owner, `bot:<slug>`).
    Another owner's stays as an alternate."""
    for link in _rows(conn.execute("SELECT * FROM task_links WHERE task_id=? AND kind='session'", (task_id,))):
        detail = _session_detail(link)
        if detail.get("primary") and "bot:" + str(detail.get("bot")) != owner:
            detail["primary"] = False
            conn.execute("UPDATE task_links SET detail_json=?,updated=? WHERE id=?", (json.dumps(detail), now(), link["id"]))


# Bots that keep nothing from one task to the next (Counsel): each task is judged on its own. Their runner starts a
# session for every task and never resumes one across tasks, reads and keeps no memory/ or state.md, copies no harness
# memory into the repository, and gets no nightly learnings (backend/learnings.py roster). A bot's config says
# `stateless: true`; a template listed here makes its bots stateless too, so bots built before the flag are.
STATELESS_BY_TEMPLATE = ("tico-counsel",)


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
    if not task_id or (config or {}).get("subtask_session") == "own" or stateless(config) or check_model(conn, task_id, bot):
        return task_id
    root, seen = task_id, {task_id}
    current = _one(conn, "SELECT id,owner,requester,private FROM tasks WHERE id=?", (task_id,))
    for _ in range(SESSION_ROOT_DEPTH):
        parent_id = TR.parent_of(conn, root) if current and current["requester"] != "bot:" + bot else None
        if not parent_id or parent_id in seen:
            break
        parent = _one(conn, "SELECT id,owner,requester,private FROM tasks WHERE id=?", (parent_id,))
        if not parent or parent["owner"] != "bot:" + bot or not _same_readers(conn, current, parent):
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
        task_private_readable(conn, who, b) for a, b in ((one, other), (other, one))
        for who in (a["owner"], a["requester"]) if who)


def task_session_started(conn, bot, lane, message_id, thread_id, runtime):
    """Record the owning bot's task session as the task's primary session when its turn starts. Only a
    turn in a task's own lane (`task:<id>`, set at claim from the config the runner keyed its session
    on) is one: a bot's shared thread carries every other conversation too, so it is nobody's task session.
    A subtask's turn runs in its session root's lane (`session_root`), so the link goes on that root: the
    lane set at claim decides, even if the subtask moved since; a lane naming another bot's task links nothing."""
    msg = message(conn, message_id) if message_id else None
    turn_task = msg and message_task_id(msg, conversation(conn, msg["conversation_id"]))
    task_id = (lane or "")[len("task:"):] if (lane or "").startswith("task:") else None
    row = task(conn, task_id) if turn_task and task_id else None
    if not thread_id or not row or row["owner"] != "bot:" + bot:
        return
    url = f"session:{bot}/{thread_id}"
    detail = json.dumps({"primary": True, "harness": runtime, "thread_id": thread_id, "bot": bot})
    links = _rows(conn.execute("SELECT * FROM task_links WHERE task_id=? AND kind='session'", (task_id,)))
    # The row this thread already has, else the bot's primary from an earlier thread (a reset, a
    # compaction), which this one replaces in place.
    target = (next((link for link in links if link["url"] == url), None)
              or next((link for link in links if _session_detail(link).get("primary")
                       and _session_detail(link).get("bot") == bot), None))
    if target and (target["url"], target.get("detail_json")) != (url, detail):
        conn.execute("UPDATE task_links SET url=?,title=?,detail_json=?,updated=? WHERE id=?",
                     (url, bot, detail, now(), target["id"]))
    elif not target:
        target = {"id": new_id()}
        conn.execute("INSERT INTO task_links (id, task_id, kind, url, title, added_by, created, detail_json, updated) "
                     "VALUES (?,?,'session',?,?,?,?,?,?)", (target["id"], task_id, url, bot, KEEPER, now(), detail, now()))
    # At most one primary: every other session on the task is an alternate now.
    for link in links:
        if link["id"] != target["id"] and _session_detail(link).get("primary"):
            other = {**_session_detail(link), "primary": False}
            conn.execute("UPDATE task_links SET detail_json=?,updated=? WHERE id=?", (json.dumps(other), now(), link["id"]))


def _task_link_allowed(conn, actor, row, mover=None, kind=None):
    _task_private_writer(conn, actor, row)
    if is_human(actor) and kind != "worktree":
        return
    if mover is None:
        mover = actor == KEEPER or is_human(actor) and can_move(conn, actor)
    delegated = _one(conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires>?",
                     (row["id"], actor, now()))
    mover = task_mover(conn, actor, row, mover)
    if (not mover and not task_participant(conn, actor, row) and not delegated
            and not task_ancestor_party(conn, actor, row) and not type_bot_works(conn, actor, row)
            and not (kind is None and task_manager_acts(conn, actor, row))):     # relations only, never pull request links
        refuse(conn, actor, "identity", "Only the owner, requester, their managers, delegates or task movers change this task")


@private_task_write
def task_unlink(conn, actor, task_id, link_id, mover=None):
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    have = _one(conn, "SELECT * FROM task_links WHERE id=? AND task_id=?", (link_id, task_id))
    if not have:
        refuse(conn, actor, "not-found", f"no link {link_id} on {task_id}")
    _task_link_allowed(conn, actor, row, mover, have["kind"])
    if have['kind'] == 'worktree' and have.get('state') != 'removed' and 'detail_json' in have:
        detail = json.loads(have.get('detail_json') or '{}')
        detail['delete_requested'] = True
        detail.pop('restore_on_reopen', None)
        # Each authorized request also bypasses the computer's old backoff once, after its safety checks.
        detail['cleanup_retry_generation'] = str(uuid.uuid4())
        for key in ('cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up'):
            detail.pop(key, None)
        conn.execute('UPDATE task_links SET detail_json=?,updated=? WHERE id=?', (json.dumps(detail), now(), link_id))
        conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
        _task_event(conn, task_id, actor, 'link', have['url'], None, 'cleanup requested')
        event(conn, actor, 'task.unlink', task_id, {'url': have['url'], 'cleanup': True})
        return have
    conn.execute("DELETE FROM task_links WHERE id=?", (link_id,))
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
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
    parent = task(conn, parent_id)
    if not parent:
        refuse(conn, actor, "not-found", f"no task {parent_id} to file this under")
    _task_private_writer(conn, actor, parent)
    if (is_bot(actor) and not task_participant(conn, actor, parent)
            and not type_bot_reads(conn, actor, parent) and not task_ancestor_party(conn, actor, parent)
            and not _one(conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires>?",
                         (parent["id"], actor, now()))):
        refuse(conn, actor, "identity", "This task type does not allow unrelated bots to create subtasks")
    if task_id and (parent["id"] == task_id or task_id in TR.ancestors(conn, parent["id"])):
        refuse(conn, actor, "kind", "a task cannot become its own ancestor")
    return parent


def descendants(conn, task_id):
    """Every task below this one, each with its `parent_id` (from task_relations)."""
    return _rows(conn.execute("WITH RECURSIVE tree(id,parent_id) AS ("
        "SELECT from_task,to_task FROM task_relations WHERE kind='parent' AND to_task=? UNION "
        "SELECT r.from_task,r.to_task FROM task_relations r JOIN tree ON r.to_task=tree.id AND r.kind='parent') "
        "SELECT t.*,tree.parent_id FROM tasks t JOIN tree ON t.id=tree.id ORDER BY t.created,t.id", (task_id,)))


def children_summaries(conn, task_ids, visible_sql="1"):
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    # Visibility is checked per child by primary key. A `visible` CTE over the whole tasks table was
    # materialized on every call by older SQLite (the server image's), a full scan for one task page.
    seen = "EXISTS (SELECT 1 FROM tasks WHERE tasks.id=r.from_task AND (" + visible_sql + "))"
    rows = conn.execute("WITH RECURSIVE tree(root,id,live,direct) AS ("
        f"SELECT r.to_task,r.from_task,1,1 FROM task_relations r WHERE r.to_task IN ({marks}) "
        f"AND r.kind='parent' AND {seen} UNION "
        "SELECT tree.root,r.from_task,tree.live AND parent.status NOT IN ('done','closed','declined'),0 "
        "FROM tree JOIN tasks parent ON parent.id=tree.id "
        f"JOIN task_relations r ON r.to_task=tree.id AND r.kind='parent' WHERE {seen}), "
        "prs AS (SELECT task_id,count(*) total,sum(state IN ('merged','shipped')) merged "
        "FROM task_links WHERE kind='pr' AND coalesce(state,'open')!='closed' "
        "AND task_id IN (SELECT id FROM tree) GROUP BY task_id) "
        "SELECT tree.root,count(*) total,sum(t.status IN ('done','closed','declined')) done,"
        "sum(tree.live AND t.status NOT IN ('done','closed','declined')) open,"
        "sum(tree.direct) direct_total,sum(tree.direct AND t.status IN ('done','closed','declined')) direct_done,"
        "sum(coalesce(prs.total,0)) prs_total,sum(coalesce(prs.merged,0)) prs_merged "
        "FROM tree JOIN tasks t ON t.id=tree.id LEFT JOIN prs ON prs.task_id=t.id GROUP BY tree.root", task_ids)
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
    return next((state for state in ("failing", "conflict", "changes_requested", "open", "merged")
                 if state in states), None)


def task_tree(conn, task_id, visible_ids=None):
    rows = descendants(conn, task_id)
    # A review or QA child's verdict rides along, so the parent's Subtasks list shows it (ui/app/task-code.js).
    nodes = {r["id"]: {**{k: r[k] for k in ("id", "title", "status", "owner")},
                       "verdict": r.get("verdict"), "verdict_head": r.get("verdict_head")} for r in rows}
    links = {}
    if nodes:
        marks = ",".join("?" * len(nodes))
        for link in _rows(conn.execute(
                f"SELECT task_id,kind,state,checks,mergeable,review_state FROM task_links "
                f"WHERE task_id IN ({marks})", list(nodes))):
            links.setdefault(link["task_id"], []).append(link)
    for row in rows:
        node = nodes[row["id"]]
        node.update(pr_state=pr_state(links.get(row["id"], [])), children=[])
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


def _awaited_by(conn, row, who):
    """Whether `who` waits on this task: it owns the parent and parked it waiting, or one of its tasks is blocked on
    this one. Its finishing then wakes `who` at once; otherwise the news is a nonblocking completion a wake
    preset may batch (backend/wake_presets.py)."""
    parent = task(conn, TR.parent_of(conn, row["id"]) or "")
    if parent and parent["owner"] == who and parent["status"] == "waiting":
        return True
    return any((task(conn, tid) or {}).get("owner") == who for tid in TR.blocked_ids(conn, row["id"]))


def _parent_finished(conn, row, previous, actor=None, parent=None, told=None, wake=True):
    """Wake the immediate parent whose subtasks are now all finished. `parent` names it when the
    task has just left it; `told` is a bot `_subtask_wake` already woke on it; `wake=False` leaves the parent asleep."""
    terminal = ("done", "closed", "declined")
    if not wake:
        return
    first = parent or TR.parent_of(conn, row["id"])
    if previous in terminal or row["status"] not in terminal or not first:
        return
    parent = task(conn, first)
    if not parent or children_summary(conn, first)["open"]:
        return
    # A private subtask's end reaches the parent's owner when it reads the subtask (it filed it, or owns it). The
    # notice names the subtask as its source, so nobody else on the parent who cannot read the subtask sees it.
    if task_private(conn, row) and not task_private_readable(conn, parent["owner"], row):
        return
    # The owner finishing work in the parent's own session already knows; one finished in a session of
    # its own (a subtask the bot filed for itself) has to wake the parent's session.
    same_session = parent["owner"] == actor and is_bot(actor) and \
        _session_lanes(conn, {**row, "owner": actor})[1] == _session_lanes(conn, parent)[1]
    if parent["status"] not in terminal and (parent["owner"] != actor or is_bot(actor) and not same_session) \
            and not (told and parent["owner"] == told):
        _wake(conn, parent, parent["owner"], "All subtasks done", refs={"wake": "subtask_finished"}, source=row["id"])


def check_model(conn, task_id, bot=None):
    """The model a bot's check subtask runs on (backend/check_models.py), or None. With `bot`, only while that
    bot both filed the check and owns the subtask: a check handed to another bot is that bot's ordinary task."""
    if not task_id or not _has_table(conn, "task_check_models"):
        return None
    if bot is None:
        return _one(conn, "SELECT runtime,model,harness,effort,role FROM task_check_models WHERE task_id=?", (task_id,))
    actor = "bot:" + bot
    return _one(conn, CHECK_MODEL_SQL, (task_id, actor, actor))


CHECK_MODEL_SQL = ("SELECT m.runtime,m.model,m.harness,m.effort,m.role FROM task_check_models m "
                   "JOIN tasks t ON t.id=m.task_id WHERE m.task_id=? AND m.created_by=? AND t.owner=?")


def _subtask_result_parent(conn, row):
    """The bot-owned parent allowed to receive this child's result, without widening its readers."""
    parent = task(conn, TR.parent_of(conn, row["id"]) or "")
    if not parent or not is_bot(parent["owner"]):
        return None
    if not bot_reads_task(conn, parent["owner"], parent) or not task_private_readable(conn, parent["owner"], row):
        return None
    if task_private(conn, row) and (not task_private(conn, parent) or not all(
            task_private_readable(conn, who, row) for who in (parent["owner"], parent["requester"]) if who)
    ) and not subtask_flows_up(conn, row, parent):
        return None
    return parent


def _subtask_result_key(conn, row, text):
    """The same gate verdict in a comment, Done note, final reply or the verdict fields is one delivery per review
    cycle: keyed by the verdict and the head it judged, however it was given, so a new verdict or head is news."""
    from .wake_rules import verdict_fields
    if found := verdict_fields(text):
        verdict, head = found
    elif row.get("verdict"):
        verdict, head = row["verdict"], row.get("verdict_head")
    else:
        return None
    return _subtask_result_cycle(conn, row) + f"verdict {verdict.lower()} {(head or '').lower()}".strip()


def _subtask_result_cycle(conn, row):
    reopened = conn.execute("SELECT coalesce(max(rowid),0) FROM task_events WHERE task_id=? AND field='status' "
                            "AND old IN ('done','closed','declined') AND new NOT IN ('done','closed','declined')",
                            (row["id"],)).fetchone()[0]
    return f"{reopened}:"


def _check_cycle_start(conn, row):
    """When a check's current cycle began (its last reopen, else "") and whether its stored verdict came after it."""
    reopened = _one(conn, "SELECT rowid AS n, ts FROM task_events WHERE task_id=? AND field='status' "
                          "AND old IN ('done','closed','declined') AND new NOT IN ('done','closed','declined') "
                          "ORDER BY rowid DESC LIMIT 1", (row["id"],))
    if not reopened:
        return {"at": "", "current": bool(row.get("verdict"))}
    given = conn.execute("SELECT coalesce(max(rowid),0) FROM task_events WHERE task_id=? AND field='verdict' "
                         "AND new IS NOT NULL", (row["id"],)).fetchone()[0]
    return {"at": reopened["ts"], "current": bool(row.get("verdict")) and given > reopened["n"]}


VERDICTS = ("approve", "changes", "pass", "fail")
HEAD_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def record_verdict(conn, actor, row, verdict=None, head=None, text=None):
    """Set a review or QA child's `verdict` and `verdict_head`: from the flag, else from a `VERDICT:` clause in `text`
    (the prose keeps working and fills the field; the latest clause wins, as a review of a new head replaces the last).
    Only the child's own owner records one, and only on a subtask. Returns whether the fields changed."""
    from .wake_rules import verdict_fields
    flagged = verdict is not None or head is not None
    if not flagged:
        found = verdict_fields(text)
        if not found:
            return False
        verdict, head = found
    # A new verdict judged a head of its own: one given alone leaves the head unknown, a head given alone keeps the verdict.
    verdict = str(verdict).strip().lower() if verdict is not None else row.get("verdict")
    head = str(head).strip().lower() or None if head is not None else None
    if flagged:
        if verdict not in VERDICTS:
            refuse(conn, actor, "kind", f"a verdict is {'|'.join(VERDICTS)}, not {verdict}")
        if head and not HEAD_SHA.match(head):
            refuse(conn, actor, "kind", "a verdict head is the pull request's commit sha, 7 to 40 hex characters")
        if actor != row["owner"] and actor != KEEPER:
            refuse(conn, actor, "identity", "a verdict is recorded by the owner of the review or QA subtask")
        if not TR.parent_of(conn, row["id"]):
            refuse(conn, actor, "kind", "a verdict belongs on a review or QA subtask, which this task is not")
    elif actor != row["owner"] or not TR.parent_of(conn, row["id"]):
        return False
    if (verdict, head) == (row.get("verdict"), row.get("verdict_head")):
        return False
    conn.execute("UPDATE tasks SET verdict=?, verdict_head=? WHERE id=?", (verdict, head, row["id"]))
    _task_event(conn, row["id"], actor, "verdict", row.get("verdict"), verdict + (f" {head}" if head else ""), "")
    # The verdict moves its Dev ticket, as Tico, the moment it is recorded (backend/verdict_moves.py).
    from . import verdict_moves
    verdict_moves.after_verdict(conn, row)
    return True


def _deliver_subtask_result(conn, row, parent, text, key=None, *, source=None, quiet=False, resume_hint=False):
    """Record a result on its immediate parent once; an ended parent never starts a worker for it."""
    if key and _one(conn, "SELECT 1 FROM messages WHERE conversation_id=? AND to_actor=? "
                         "AND json_extract(refs_json,'$.subtask')=? "
                         "AND json_extract(refs_json,'$.subtask_result')=? LIMIT 1",
                    (parent["conversation_id"], parent["owner"], row["id"], key)):
        return
    refs = {"subtask": row["id"], **({"subtask_result": key} if key else {})}
    # The parent's owner reads the verdict and the head it judged as fields, not only as prose.
    judged = task(conn, row["id"]) or row
    if judged.get("verdict"):
        refs["verdict"] = judged["verdict"]
        if judged.get("verdict_head"):
            refs["verdict_head"] = judged["verdict_head"]
    if source:
        original = message(conn, source)
        if original:
            # Preserve the review's references and run inputs when readers' access changes later.
            refs["inputs"] = [source]
            refs.update({k: v for k, v in original["refs"].items() if k in ("turn_id", "run")})
    silent = quiet or parent["status"] in ("done", "closed", "declined")
    # What the verdict did to the ticket: a move Tico made (an approval it moved on asks no builder turn), a Fixes
    # step or a merge to do, which goes first in the builder's queue (backend/verdict_moves.py).
    from . import verdict_moves
    moved = verdict_moves.delivery(conn, judged) if verdict_moves._verdict(judged) else None
    if moved:
        text = moved["say"] + "\n\n" + text
        silent = silent or moved["quiet"]
        if moved.get("handoff") and not silent:
            refs[verdict_moves.HANDOFF] = moved["handoff"]
    if resume_hint:
        text = ("Subtask result returned to this task's session. Continue this task here; the result needs "
                "no relay, separate delivery turn or task run command.\n\n" + text)
    _wake(conn, parent, parent["owner"], text, refs=refs, source=source or row["id"],
          quiet_bots=silent, quiet=silent)


def _subtask_wake(conn, actor, row, previous, note=None, quiet=False):
    """A person finished, declined or closed a subtask under a bot's open task: wake that bot on the parent,
    where a bot files what it is blocked on (docs/tasks.md), so its turn continues the parent's session
    (session_root). Returns the bot woken, else None; callers leave it out of their own notices on the subtask.
    A wake still queued for this subtask is not repeated. A quiet update leaves its note out, as on the subtask.
    A bot or its manager finishing a delegated subtask returns the result the same way;
    a check still records its verdict only through its own owner."""
    terminal = ("done", "closed", "declined")
    check = is_bot(row["owner"]) and check_model(conn, row["id"], row["owner"][len("bot:"):])
    # Done or Declined carries a verdict; the bot closing its own check is a cancellation the parent need not hear.
    by_checker = bool(check and actor == row["owner"] and row["status"] in ("done", "declined"))
    by_worker = is_bot(actor) and task_represents(conn, actor, row["owner"]) and row["requester"] != row["owner"]
    if not (is_human(actor) or by_checker or by_worker) or row["status"] not in terminal:
        return None
    parent = _subtask_result_parent(conn, row)
    if not parent:
        return None
    bot = parent["owner"]
    if previous in terminal:
        # Acceptance and repeated Done writes carry no new work; keep any new note in the parent's history.
        if by_worker and note and not quiet and previous == row["status"]:
            _deliver_subtask_result(conn, row, parent, f"Subtask update: {row['title']}\n{note}", quiet=True)
        return bot if by_worker else None
    if not by_worker and _has_table(conn, "jobs") and _one(
            conn, "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=? AND j.state='queued' "
            f"AND {unheld_sql()} AND json_extract(m.refs_json,'$.subtask')=? LIMIT 1", (actor_id(bot), row["id"])):
        return bot
    # Turns on a check run on its model; one marked done from another turn before any ran there did not.
    ran = by_checker and _one(conn, "SELECT 1 FROM turns WHERE task_id=? AND bot=? LIMIT 1",
                              (row["id"], actor_id(row["owner"])))
    verdict = (f"{check['role'].capitalize()} done (ran on {check['model']})" if ran
               else f"{check['role'].capitalize()} done without running on {check['model']}") if by_checker else ""
    said = ({"declined": "Subtask declined", "closed": "Subtask closed"}.get(row["status"])
            or (f"{verdict}: {row['title']}" if by_checker else None)
            or _marked_done(conn, actor, row["title"], "this subtask"))
    # The parent's "All subtasks done" (_parent_finished) rides on this one notice, not a second wake.
    last = "" if children_summary(conn, parent["id"])["open"] else "All subtasks done\n"
    if by_worker and not by_checker:
        said = f"Subtask {row['status']}: {row['title']}"
    text = f"{last}{said}" + (f": {row['title']}" if row["status"] != "done" and not by_worker else "")
    text += f"\n{note}" if note and not quiet else ""
    key = _subtask_result_key(conn, row, note) if (note or row.get("verdict")) and not quiet else None
    # A reviewer often posts its verdict before marking Done with a short bookkeeping note. The verdict
    # already resumed the parent; keep that completion in history without buying another turn.
    delivered_verdict = by_worker and not key and row["status"] == "done" and _one(
        conn, "SELECT 1 FROM messages WHERE conversation_id=? AND to_actor=? "
              "AND json_extract(refs_json,'$.subtask')=? "
              "AND json_extract(refs_json,'$.subtask_result') LIKE ? LIMIT 1",
        (parent["conversation_id"], bot, row["id"], _subtask_result_cycle(conn, row) + "%"))
    _deliver_subtask_result(conn, row, parent, text, key, quiet=bool(delivered_verdict), resume_hint=by_worker)
    return bot


def _marked_done(conn, actor, title, what="this"):
    """What a bot hears when a person finishes its request: who did it, and the task. Done is one tap, so a
    note is optional; the caller adds it on the next line when there is one."""
    from . import names
    who = names.lookup(conn, [actor]).get(actor) or actor_id(actor)
    return f"{who} marked {what} done: {title}"


def _made_private(conn, actor, task_id):
    """A company-visible task goes private because the task above it is: the flag, its record, and the follow-ups."""
    conn.execute("UPDATE tasks SET private=1,visibility='private' WHERE id=?", (task_id,))
    _task_event(conn, task_id, actor, "private", 0, 1, "")
    _private_followups(conn, task_id)
    from .reconnects import access_changed as reconnects_access_changed
    reconnects_access_changed(conn, task_id)    # a reconnect card's person may not read it now


def _private_followups(conn, task_id):
    """A task just made private: its thread narrows to its parties, delegations end, and nobody
    outside it is waited on."""
    after = isolate_private_task(conn, task(conn, task_id))
    conn.execute("DELETE FROM task_delegations WHERE task_id=?", (task_id,))
    if after.get("waiting_on") and not task_private_readable(conn, after["waiting_on"], after):
        conn.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (task_id,))
        _task_event(conn, task_id, KEEPER, "waiting_on", after["waiting_on"], None, "")


def task_links(conn, task_id):
    return _rows(conn.execute("SELECT * FROM task_links WHERE task_id=? ORDER BY created", (task_id,)))




def bot_reads_task(conn, bot, row):
    """Whether this bot may read the task by every rule a read of it checks (privacy, bot activity access, an
    assignment's scope): a notice or an answer it is sent carries the task's words."""
    if not task_private_readable(conn, bot, row):
        return False
    if not _has_table(conn, "bot_config"):
        return True                         # no bot has an access list: every bot is open
    from .auth import bot_reads_task as reads
    return reads(conn, bot, row)


def task_ask_recipient(conn, actor, row, ask):
    directing = actor == row["requester"] or (
        actor not in (row["owner"], row["requester"]) and task_participant(conn, actor, row))
    target = ask.get("who") or (row["owner"] if directing else row["requester"])
    target = resolve_actor(conn, target)
    if task_private(conn, row) and target not in (row["owner"], row["requester"]):
        refuse(conn, actor, "private", "Only the requester and assignee can receive private task questions")
    return target


@private_task_write
def task_comment(conn, actor, task_id, text, wake=True, *, ask=None, extra_refs=None, answer_to=None, answer_text=None, public=False, approval_id=None):
    """A comment on a task: one message in the task's conversation, tagged with the task, from
    whoever wrote it. It wakes the bot on the other side of the task when `wake` (a mover, the
    owner or the requester left it); otherwise it is saved for the bot's next turn on the task."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    text = str(text or "").strip()
    if not text:
        refuse(conn, actor, "lint", "write the comment")
    if public:
        from .roadmaps import check_public_comment
        if ask or extra_refs or answer_to:
            refuse(conn, actor, 'kind', 'A public comment is plain text without attachments or asks')
        check_public_comment(conn, actor, row, text, approval_id)
    target = comment_target(conn, actor, row)
    refs = {"task": task_id, "comment": True, **(extra_refs or {})}
    # A bot's comment flagged needs-action wakes the other side even where its comment alone would not (a snoozed
    # task): the sender said it cannot wait (backend/bot_to_bot.py).
    if not wake and not (is_bot(actor) and refs.get("needs_action") is True):
        refs["quiet"] = True
    conv = conversation(conn, row["conversation_id"])
    if not conv:
        refuse(conn, actor, "not-found", f"task {task_id} has no conversation")
    if ask:
        target = task_ask_recipient(conn, actor, row, ask)
        refs.update(ask)
        refs["who"] = ask.get("who")
    if answer_to:
        # The answer goes back to the bot that asked, which may be neither the owner nor the requester.
        asker = (message(conn, answer_to) or {}).get("from_actor")
        if is_bot(asker) and asker != actor and _actor_exists(conn, asker) and bot_reads_task(conn, asker, row):
            target = asker
        msg = answer(conn, actor, answer_to, text, comment_refs=refs, comment_target=target or actor, classify_text=answer_text)
    elif ask:
        msg = say(conn, actor, target, text, kind="ask", conversation_id=conv["id"], refs=refs)
    elif target is None:
        # the commenter is the only party (they asked themselves): on the record, wakes nobody
        msg = _write_message(conn, actor, actor, text, conv, "say", refs, None, None)
    else:
        msg = say(conn, actor, target, text, conversation_id=conv["id"], refs=refs)
    from . import wake_rules
    wake_rules.own_comment(conn, msg, row)        # token saving 2/9: a bot's own comment never wakes it; log the skip
    hook(conn, "task.commented", task_id, lambda: _task_facts(conn, task_id, actor, comment_id=msg["id"]), actor, revision=msg["id"])
    if actor != KEEPER or ask:
        conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
    now_row = task(conn, task_id) or {}
    if (now_row.get("status") == "waiting" and now_row.get("waiting_on") == actor
            and not any(a.get("to_actor") == actor and has_pick_options(a)
                        for a in open_task_asks(conn, now_row))):
        # Plain replies clear a person wait; a structured decision keeps waiting until a choice is recorded.
        conn.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (task_id,))
        _task_event(conn, task_id, actor, "waiting_on", actor, None, "")
        from . import task_holds
        task_holds.refresh(conn, task_id, f"{task_holds.name(conn, actor)} replied")
    if ask or row.get("waiting_on") == actor:
        _recount(conn, row["owner"])
    if wake and is_bot(target) and is_human(actor):
        conn.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires) VALUES(?,?,?,?,?)",
                     (task_id, target, actor, msg["id"], shift(now(), hours=24)))
    if public:
        conn.execute('UPDATE messages SET public=1 WHERE id=?', (msg['id'],))
        msg = message(conn, msg['id'])
    if is_human(actor) and row["owner"] == "bot:goal-manager":
        from . import strategy_merge
        # a person's answer to the Strategy Bot's merged item goes to each asker
        strategy_merge.on_reply(conn, actor, row, text)
    if is_human(actor):
        from . import ask_ingest
        ask_ingest.on_reply(conn, actor, task_id, text)     # repeats the ask filter merged here hear the answer too
    return msg


def jobs_mark(conn):
    """Where the jobs table stands, to tell later which bot jobs a write queued (comment_delivery)."""
    return conn.execute("SELECT coalesce(max(rowid),0) FROM jobs").fetchone()[0]


def comment_delivery(conn, msg, copies=(), *, since=0):
    """What a comment write did, for its response: `woke` is true only when the write queued a bot job (the bot will
    take a turn on it), counting the comment's own job and any the write caused elsewhere, such as the notice a
    delegated review's verdict forwards to the parent task; `since` is jobs_mark from before the write. `delivered`
    is true when it reached a person (its recipient or an @mentioned copy), who sees it in their own lists."""
    ids = [msg["id"], *(m["id"] for m in copies)]
    queued = conn.execute("SELECT 1 FROM jobs WHERE rowid>? OR message_id IN (%s)" % ",".join("?" * len(ids)),
                          [since, *ids]).fetchone() is not None
    return {"woke": queued,
            "delivered": any(is_human(m.get("to_actor")) for m in (msg, *copies))}


def comment_target(conn, actor, row):
    """Who `actor`'s plain comment on the task goes to: the first bot among its owner, requester and origin, else the
    first person among them, else no one."""
    others = [a for a in dict.fromkeys([row["owner"], row["requester"], task_origin(conn, row)])
              if a and a not in (actor, KEEPER) and task_private_readable(conn, a, row)]
    return next((a for a in others if is_bot(a)), None) or (others[0] if others else None)


def comment_way(conn, actor, row, text, *, mover=False):
    """How a comment from `actor` with no delivery named reaches the bot on the task (backend/delivery.py): `steer`,
    `queue` or `defer`, with the defer's deadline (`24h`, `never`) or None. A comment that wakes the bot steers, as a
    person's comment always has: it joins the bot's turn running on the task.

    A bot's comment goes as bot messages always have (steer), or waits on a snoozed task. A person's:
    - someone neither on the task nor a mover: defer, no deadline, so a reader never spends a bot's turns on a task
      that isn't theirs;
    - no bot on the task: steer (it reaches a person, who has no turns);
    - the task waits on them: steer, they are answering it;
    - a task a bot filed for a person (its requester or origin a bot, its owner not): defer 24h, unless the comment
      @names a bot on the task: queue. Such a comment is most often a progress note, not a question for the bot;
    - a task a bot owns: steer, the person is directing its work."""
    if is_bot(actor):
        return ("steer", None) if not TS.snoozed(row) else ("defer", "never")
    if not (mover or task_participant(conn, actor, row)):
        return "defer", "never"
    origin = task_origin(conn, row)
    if not any(is_bot(a) for a in (row["owner"], row["requester"], origin)):
        return "steer", None
    if row.get("waiting_on") == actor:
        return "steer", None
    if not is_bot(row["owner"]) and (is_bot(row["requester"]) or is_bot(origin)):
        return ("queue", None) if names_a_bot(conn, text, row) else ("defer", "24h")
    return "steer", None


def comment_wakes(conn, actor, row, text, *, mover=False):
    """Whether a comment from `actor` with no delivery named wakes the bot on the other side of the task."""
    return comment_way(conn, actor, row, text, mover=mover)[0] != "defer"


def task_bots(conn, row):
    """The bots on the task: its owner, requester and origin."""
    return [a for a in dict.fromkeys([row["owner"], row["requester"], task_origin(conn, row)]) if is_bot(a)]


def names_a_bot(conn, text, row):
    """`text` @mentions a bot on the task (its owner, requester or origin)."""
    return any(_mentions_bot(conn, text, a) for a in task_bots(conn, row))


def bot_handles(conn, row):
    """The @names that reach the bots on the task (names_a_bot): each one's slug and name."""
    handles = []
    for a in task_bots(conn, row):
        name = (bot(conn, actor_id(a)) or {}).get("name")
        handles += [actor_id(a)] + ([name] if name else [])
    return handles


def names_a_filer(conn, text, row):
    """`text` @mentions the bot that filed the task (its requester or origin)."""
    filers = [a for a in dict.fromkeys([row["requester"], task_origin(conn, row)]) if is_bot(a)]
    return any(_mentions_bot(conn, text, a) for a in filers)


def filer_handles(conn, row):
    """The @names that reach the bots that filed the task (names_a_filer): each one's slug and name."""
    handles = []
    for a in dict.fromkeys([row["requester"], task_origin(conn, row)]):
        if is_bot(a):
            name = (bot(conn, actor_id(a)) or {}).get("name")
            handles += [actor_id(a)] + ([name] if name else [])
    return handles


def _at(text, name):
    return re.search(r"(?<![\w@])@" + re.escape(name) + r"(?![\w-])", str(text or ""), re.IGNORECASE) is not None


def _mentions_bot(conn, text, actor):
    """`text` names the bot `actor` with an @, by slug (@product-manager) or by name (@Product Manager)."""
    slug = actor_id(actor)
    row = bot(conn, slug) or {}
    return any(_at(text, n) for n in [slug] + ([row["name"]] if row.get("name") else []))


def mentioned_people(conn, text, row, author):
    """The people `text` @mentions who may read the task, in the order they appear, never the author.
    A person is named by id, by the part of their email before the @, by full name or by first name
    when no one else shares it."""
    if "@" not in str(text or ""):
        return []
    people = humans(conn)
    firsts = {}
    for p in people:
        first = str(p.get("name") or "").split(" ")[0].lower()
        if first:
            firsts[first] = firsts.get(first, 0) + 1
    found = []
    for p in people:
        actor = human_actor(p["id"])
        if actor == author or not task_private_readable(conn, actor, row):
            continue
        name = str(p.get("name") or "").strip()
        names = [p["id"], str(p.get("email") or "").split("@")[0], name]
        if name and firsts.get(name.split(" ")[0].lower()) == 1:
            names.append(name.split(" ")[0])
        spots = [m.start() for n in dict.fromkeys(x for x in names if x)
                 for m in re.finditer(r"(?<![\w@])@" + re.escape(n) + r"(?![\w-])", text, re.IGNORECASE)]
        if spots:
            found.append((min(spots), actor))
    return [actor for _, actor in sorted(found)]


def mention_ask(text, person):
    """The question a mention puts to `person`: the comment itself, answered in their own words."""
    return {"questions": [{"id": "mention", "header": "Mention", "question": str(text).strip()[:300],
                           "options": [], "multi": False, "other": True}], "who": person}


def task_comments(conn, task_id, *, actor=None):
    """Every comment and ask on this task, oldest first, with who wrote it. A deleted comment is not
    listed; an edited one carries `edited_at`."""
    from . import task_privacy as privacy
    row = task(conn, task_id)
    if not row or not row.get("conversation_id"):
        return []
    conv = conversation(conn, row["conversation_id"])
    out, sql, args = [], "conversation_id=?", [row["conversation_id"]]
    # In a bot's room, only the messages that name the task (backend/message_links.py), not the whole room.
    from .message_links import named
    ids = named(conn, task_id, row["conversation_id"])
    if ids is not None:
        sql, args = "+conversation_id=? AND id IN (SELECT value FROM json_each(?))", args + [_dump(sorted(ids))]
    if actor is not None:
        # The rows below are read in the snapshot their checks use, so each check takes the row as stored.
        privacy.snapshot(conn)
    rows = _rows(conn.execute("SELECT * FROM messages WHERE " + sql + " AND deleted_at IS NULL ORDER BY created", args))
    for m in rows:
        m["refs"] = _json(m.get("refs_json"), {}) or {}
    if actor is not None:
        privacy.prime_messages(conn, rows)
    for m in rows:
        if message_task_id(m, conv) == task_id and m.get("kind") in ("say", "ask", "answer"):
            if actor is not None and not privacy.message_readable(conn, actor, m, current=True):
                continue
            if m["kind"] == "ask" and m["refs"].get("questions"):
                m["ask"] = {**{k: m["refs"].get(k) for k in ("questions", "who")}, "by": m["from_actor"]}
                m["answers"] = review_answers(conn, m["id"], actor=actor)
            if m["refs"].get("answer"):
                m["answer"] = m["refs"]["answer"]
            out.append(m)
    return out


REVIEW_ANSWERS_SQL = (
    "SELECT * FROM messages WHERE deleted_at IS NULL AND ((kind='answer' AND in_reply_to=?) "
    "OR id=(SELECT answered_by FROM messages WHERE id=?)) ORDER BY created,rowid")


def prime_review_answers(conn, messages):
    """Read question replies together as stored rows; each reader still filters their provenance in review_answers."""
    from .read_cache import cache, prime_rows, rows
    values = cache(conn)
    if values is None:
        return []
    asks = {}
    for message in messages:
        refs = _json(message["refs_json"], {}) or {}
        if message["kind"] == "ask" and isinstance(refs, dict) and refs.get("questions"):
            asks[message["id"]] = message
    wanted = {mid: m for mid, m in asks.items() if (REVIEW_ANSWERS_SQL, (mid, mid)) not in values}
    for part in chunks(sorted(wanted)):
        marks = ",".join("?" * len(part))
        answered = {}
        for mid in part:
            if wanted[mid]["answered_by"]:
                answered.setdefault(wanted[mid]["answered_by"], set()).add(mid)
        found = {mid: [] for mid in part}
        answers = conn.execute(
            f"SELECT * FROM messages WHERE deleted_at IS NULL AND ((kind='answer' AND in_reply_to IN ({marks})) "
            "OR id IN (SELECT value FROM json_each(?))) ORDER BY created,rowid", (*part, _dump(list(answered))))
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
        refs = _json(row["refs_json"], {}) or {}
        out.append(refs.get("answer") or {"by": row["from_actor"], "text": row["body"], "at": row["created"]})
    return out


def open_task_asks(conn, task_row, *, actor=None):
    from . import task_privacy as privacy
    conv = (task_row or {}).get("conversation_id")
    if not conv:
        return []
    asks = _rows(conn.execute(
        "SELECT m.* FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE m.conversation_id=? AND m.kind='ask' AND {MESSAGE_TASK_SQL}=? "
        f"AND {OPEN_ASK_SQL} ORDER BY m.rowid DESC",
        (conv, task_row["id"])))
    if actor is not None:
        asks = [ask for ask in asks if privacy.message_readable(conn, actor, ask)]
    for ask in asks:
        ask["refs"] = _json(ask.get("refs_json"), {}) or {}
    return asks


def open_tasks_asks(conn, task_rows, *, actor):
    """{task id: open_task_asks(conn, row, actor=actor)} for many tasks: their open asks read in one query per 500
    tasks and checked together (task_privacy.readable_messages), each by the same rule open_task_asks applies."""
    from . import task_privacy as privacy
    rows = [row for row in task_rows if row.get("conversation_id")]
    out = {row["id"]: [] for row in task_rows}
    asks = []
    for part in chunks(rows):
        found = _rows(conn.execute(
            "SELECT m.*,t.id AS ask_task_id FROM tasks t JOIN messages m ON m.conversation_id=t.conversation_id "
            f"JOIN conversations cv ON cv.id=m.conversation_id WHERE t.id IN ({','.join('?' * len(part))}) "
            f"AND m.kind='ask' AND {MESSAGE_TASK_SQL}=t.id "
            f"AND {OPEN_ASK_SQL} ORDER BY m.rowid DESC", [row["id"] for row in part]))
        asks.extend(found)
    tasks = [ask.pop("ask_task_id") for ask in asks]
    shown = {id(ask) for ask in privacy.readable_messages(conn, actor, asks)}
    for tid, ask in zip(tasks, asks):
        if id(ask) in shown:
            ask["refs"] = _json(ask.get("refs_json"), {}) or {}
            out[tid].append(ask)
    return out


def comment_on(conn, task_row, message_id):
    """The message `message_id` when it is on this task's thread the way `task_comments` finds it
    (deleted or not, any kind), else None."""
    msg = message(conn, message_id, include_deleted=True)
    if not msg or not task_row or msg["conversation_id"] != task_row.get("conversation_id"):
        return None
    return msg if message_task_id(msg, conversation(conn, msg["conversation_id"])) == task_row["id"] else None


def is_comment(msg):
    """Written by `task_comment`: a say tagged `comment`. Not a question, an answer, a notice, an
    approval, a bot's reply to its run or a chat message that only mentions the task."""
    refs = msg.get("refs") or {}
    return (msg.get("kind") == "say" and bool(refs.get("comment"))
            and not (set(refs) - {"task", "comment", "quiet", "via", "rule", "wake_immediate",
                                         "delivery"}))



def _withdraw_queued_comment_copies(conn, message_id):
    """Stop unsent copies and drop local outbound text; already delivered copies remain elsewhere."""
    if _has_table(conn, 'slack_posts'):
        conn.execute("UPDATE slack_posts SET text='',state=CASE WHEN state IN ('ready','retry') "
                     "THEN 'cancelled' ELSE state END,updated=? WHERE message_id=?", (now(), message_id))


def _refresh_comment_replies(conn, msg):
    """A retry keeps its original effect, but never replays withdrawn comment words."""
    conn.execute("UPDATE events SET detail_json=json_remove(detail_json,'$.old') "
                 "WHERE target=? AND action IN ('message.edited','message.deleted')", (msg["id"],))
    if not _has_table(conn, "idempotency"):
        return
    def refresh(value):
        if isinstance(value, dict):
            if value.get("id") == msg["id"] and "from_actor" in value:
                return msg
            return {k: refresh(v) for k, v in value.items()}
        if isinstance(value, list):
            return [refresh(v) for v in value if not (msg.get("deleted_at") and isinstance(v, dict)
                    and v.get("id") == msg["id"] and "from_actor" in v)]
        return value
    # By index (backend/idempotency_messages.py): reading every stored answer here held the write lock for 35 s.
    rows = idempotency_messages.quoting(conn, msg["id"]) if _has_table(conn, "idempotency_messages") else None
    if rows is None:        # not indexed yet: an older file, or before its first start's backfill finished
        rows = conn.execute("SELECT rowid,response_json FROM idempotency WHERE instr(response_json,?)>0",
                            (msg["id"],)).fetchall()
    for row in rows:
        conn.execute("UPDATE idempotency SET response_json=? WHERE rowid=?",
                     (_dump(refresh(_json(row["response_json"], {}))), row["rowid"]))


def _own_comment(conn, actor, task_id, message_id):
    """The live comment `message_id` on this task, when `actor` wrote it; refused otherwise."""
    _writer(conn, actor)
    row = task(conn, task_id)
    readable = globals().get("task_private_readable")
    if row and row.get("private") and (not readable or not readable(conn, actor, row)):
        refuse(conn, actor, "identity", "This task is not available to you")
    msg = comment_on(conn, row, message_id)
    if not msg or msg.get("deleted_at"):
        refuse(conn, actor, "not-found", f"no comment {message_id} on task {task_id}")
    if not is_comment(msg):
        refuse(conn, actor, "kind", "only a comment is edited or deleted, not a question, an answer, "
                                    "a notice or a chat message"
                                    + ("; settle your open question with hub task ask-resolve" if msg.get("kind") == "ask" else ""))
    if msg["from_actor"] != actor:
        refuse(conn, actor, "identity", f"{actor_id(msg['from_actor'])} wrote this comment; only they change it")
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
        return msg                  # the same words are no edit: no mark, no history line
    if not msg.get("delivered_at") and len(text.encode("utf-8")) > len(msg["body"].encode("utf-8")):
        _held_queue_admit(conn, actor, msg["to_actor"], text, msg["refs_json"], replacing=message_id)
    ts = now()
    conn.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (text, ts, message_id))
    _task_event(conn, task_id, actor, "comment", message_id, message_id)
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (ts, task_id))
    event(conn, actor, "message.edited", message_id, {"task": task_id})
    edited = message(conn, message_id)
    _withdraw_queued_comment_copies(conn, message_id)
    _refresh_comment_replies(conn, edited)
    return edited


@private_task_write
def task_comment_delete(conn, actor, task_id, message_id):
    """The author takes a comment back. Only its metadata stays for the audit trail, excluded from future comment reads: a run it queued and nobody started is cancelled, the questions it answered
    are open again, and the bot it woke loses the delegation that came with it (`task_delegations`)."""
    _own_comment(conn, actor, task_id, message_id)
    ts = now()
    conn.execute("UPDATE messages SET body='',refs_json=?,deleted_at=? WHERE id=?",
                 (_dump({"task": task_id, "comment": True}), ts, message_id))
    reopened = [r["id"] for r in _rows(conn.execute("SELECT id FROM messages WHERE answered_by=?", (message_id,)))]
    conn.execute("UPDATE messages SET answered_by=NULL WHERE answered_by=?", (message_id,))
    conn.execute("UPDATE task_delegations SET expires=? WHERE message_id=? AND expires>?", (ts, message_id, ts))
    cancelled = _has_table(conn, "jobs") and conn.execute(
        "UPDATE jobs SET state='cancelled' WHERE message_id=? AND state='queued'", (message_id,)).rowcount > 0
    _task_event(conn, task_id, actor, "comment", message_id, None)
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (ts, task_id))
    row = task(conn, task_id)
    recount(conn, [row["owner"], row["requester"]])     # a question taken back, or reopened by it
    event(conn, actor, "message.deleted", message_id,
          {"task": task_id, "reopened": reopened, "cancelled_run": cancelled})
    deleted = message(conn, message_id, include_deleted=True)
    _withdraw_queued_comment_copies(conn, message_id)
    _refresh_comment_replies(conn, deleted)
    return deleted


ASK_QUOTE_MAX = 4000        # a quoted answer is what the person said, not a document


@private_task_write
def task_ask_resolve(conn, actor, task_id, message_id, quote=None, *, relay=False):
    """Settle an open question on a task that was answered somewhere else, or is no longer wanted.

    With `quote`, the person's words from where they answered (a chat, a call): the question's asker, or the task's
    owner bot, records them as the answer, marked as quoted by whoever recorded it. Without, the asker withdraws the
    question: it stays in the thread, superseded. Either way it leaves every Needs you list and count. Nothing is sent
    and nobody wakes: the answer is filed quietly for the asker's next turn on the task.

    `relay` is the Strategy Bot passing on the answer a person gave once for several bots (backend/strategy_merge.py),
    never a route: the Strategy Bot records it on a task that is not its own, and the asker wakes to it."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    asked = comment_on(conn, row, message_id)
    if not asked or asked.get("deleted_at") or asked.get("kind") != "ask":
        refuse(conn, actor, "not-found", f"no question {message_id} on task {task_id}")
    asker, person = asked["from_actor"], asked["to_actor"]
    if quote is None and not task_represents(conn, actor, asker):
        refuse(conn, actor, "identity", f"{actor_id(asker)} asked this question; only they or their managers withdraw it")
    if (quote is not None and not task_represents(conn, actor, asker)
            and not (is_bot(row["owner"]) and task_represents(conn, actor, row["owner"])) and not relay):
        refuse(conn, actor, "identity", "only the question's asker, the task's owner bot or their managers record an answer given elsewhere")
    if not _one(conn, f"SELECT 1 FROM messages m WHERE m.id=? AND {OPEN_ASK_SQL}", (message_id,)):
        refuse(conn, actor, "kind", "this question was already answered or withdrawn")
    ts = now()
    reply = None
    if quote is None:
        conn.execute("UPDATE messages SET superseded_at=? WHERE id=?", (ts, message_id))
    else:
        quote = str(quote).strip()
        if not quote:
            refuse(conn, actor, "lint", "quote the person's answer, or leave the quote out to withdraw the question")
        if len(quote) > ASK_QUOTE_MAX:
            refuse(conn, actor, "lint", f"a quoted answer is at most {ASK_QUOTE_MAX} characters")
        path_mentioned(conn, actor, "reply", quote, task_id)

        def name(who):
            found = human(conn, who) if is_human(who) else bot(conn, who) if is_bot(who) else None
            return (found or {}).get("name") or actor_id(who)
        refs = {"depth": asked["refs"].get("depth", 1), "task": task_id, "comment": True, "quiet": True,
                "quoted": True, "for": person}
        if relay:
            del refs["quiet"]
        if asked["refs"].get("questions"):
            # Read as any other structured answer (`task_answers`): the person's words are its free text.
            refs["answer"] = {"target": {"comment": message_id}, "other": quote, "dismiss": False,
                              "answers": {q["id"]: [] for q in asked["refs"]["questions"]},
                              "by": person, "quoted_by": actor, "at": ts, "quoted": True}
        # The answer is the person's words, recorded by `actor`. A bot that asked hears it on its next turn (its inbox)
        # when someone else recorded it; an asker recording its own, or the keeper, needs no message.
        own = actor == asker or not is_bot(asker)
        reply = _write_message(conn, actor, asker, f'{name(person)} (quoted by {name(actor)}): "{quote}"',
                               conversation(conn, asked["conversation_id"]), "answer", refs, message_id, None,
                               delivered_at=ts if own else None, read_at=ts if own else None)
    _task_event(conn, task_id, actor, "ask", message_id, "answered" if reply else "withdrawn")
    if row["status"] == "waiting" and row.get("waiting_on") == person and not any(
            a["to_actor"] == person for a in open_task_asks(conn, row)):
        # Nothing else on the task waits on them: the next move is the bot's, and it leaves their Needs you.
        conn.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (task_id,))
        _task_event(conn, task_id, actor, "waiting_on", person, None, "")
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (ts, task_id))
    recount(conn, [row["owner"], row["requester"], asker])
    event(conn, actor, "ask.answered_elsewhere" if reply else "ask.withdrawn", message_id,
          {"task": task_id, "to": person, **({"answer": reply["id"]} if reply else {})})
    return {"ask": message(conn, message_id), "answer": reply}


@private_task_write
def task_create(conn, actor, title, body, owner, due=None, parent_id=None, *, deduplicate=True,
                allow_planned=False, conversation_id=None, lane=None, labels=None, top=False, lint=True,
                goal_id=None, next_run=False, type=None, step=None, number=None, mover=None, private=None,
                requester_actor=None, own_room=True, visibility=None, public_title=None, public_summary=None,
                wake_refs=None, trigger="status_or_relation", trigger_source=None, review_prs=None,
                change_paths=None, qa=None, qa_not_checkable_live=False, receipt_owner=None,
                quiet_notice=False, from_private=False, routine=False, same=None, occurrence=None,
                occurrence_step=None, rule_due_id=None, public_warning=""):
    """Rule 5. Anyone may open a task for any active owner; a human owner's General task is
    linted (rule 7).

    `public_warning` is an org rule's Warn line (backend/org_rule_check.py): when the task is stored public, its
    public summary carries the line, past the summary's cap the bot's own text met.

    `next_run` files it for the bot's next run instead of starting one: the notice is written
    quietly, and the next run the bot has for any reason carries the task in its prompt
    (NEXT_RUN_WAITING_SQL, execution.claim). A person has no runs, so only a bot may get one.

    `conversation_id` attaches a person's task to an existing chat room. A room is not claimed as
    this task's private thread: many tasks share one room, and messages carry `refs.task`. A bot's
    task gets its own room whatever is passed; the chat room named gets a quiet "Started task" line.
    `lane` picks the pipeline (company or product; default by the owner's team); the task
    joins the bottom of the owner's queue and of its step unless `top`.
    `number` keeps an imported task's number: a mover's (`mover`, as in `task_update`), and free.
    `quiet_notice` writes the owner's notice without waking it: a task joined to an open ask about the same blocker
    (backend/blocker_asks.py) wakes when that ask is done.
    `routine` is an automation's own run (a routine's, before the move): a Monitor or Reviewer takes it as its own work.
    `occurrence` is the marker of a due-date routine's occurrence, "<automation_id>:<due_on>": each day's task is its
    own, so an open one with the same title (an overdue day's) does not count as a duplicate of it, and it is stored
    on the task (`tasks.occurrence`) for the sweeps and the app. With `occurrence_step` too, the task is one per
    occurrence per producing step: a retry of a run whose step already opened it gets that task back
    (`_reuse_occurrence`), never a second one.
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
    _takes_tasks(conn, actor, target, title, parent_id, routine)
    _learning(conn, actor, target)
    if occurrence and occurrence_step:
        again = _one(conn, "SELECT id FROM tasks WHERE occurrence=? AND occurrence_step=? LIMIT 1",
                     (occurrence, occurrence_step))
        if again:
            return _reuse_occurrence(conn, actor, target, again["id"], title, wake_refs, trigger, trigger_source)
    parent = _task_parent(conn, actor, None, parent_id) if parent_id else None
    from . import review_gate
    if parent and review_gate.is_review(conn, parent, title):
        review_gate.check(conn, actor, parent, review_prs, require_pr=True, receipt_owner=receipt_owner)
    if parent:
        review_gate.refuse_second(conn, actor, parent, title, review_prs)
    # Whoever files a subtask asked for it: it shows them as asking and their notices reach them.
    requester = actor
    if private is False and (not is_human(actor) or VIA.get()):
        private = None              # a bot cannot override a sensitive default to publish
    # A task filed from private work (app.task_create, task_privacy.from_private) is company-visible under its private
    # parent: its words were checked to carry nothing of the parent.
    private = not from_private and bool((private if private is not None else
                                         private_tasks_default(conn, actor) or private_tasks_default(conn, target))
                                        or parent and task_private(conn, parent))
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
        refuse(conn, actor, 'approval', 'A bot publishes only through an approved publication: create the task, then '
               'request one with POST /api/v2/tasks/{id}/publication-preview')
    if visibility == 'public' and not public_title and not private:
        refuse(conn, actor, 'lint', 'Write a public title to publish this task; its internal title and body stay private')
    if requester_actor is not None:
        # Only the server supplies a verified human origin for a bot's own work.
        if not is_human(requester_actor) or target != actor or not human(conn, requester_actor):
            refuse(conn, actor, "identity", "Human requester attribution requires the bot's own verified work")
        requester = requester_actor
    title = str(title or "").strip()
    body = str(body or "")
    severity = classify(f"{title}\n{body}", to_actor=target, actor=actor, conn=conn)
    path_mentioned(conn, actor, "new task", f"{title}\n{body}".strip())
    if next_run and not is_bot(target):
        refuse(conn, actor, "next-run", "only a bot has a next run; file an ordinary task for a person")
    lane = _lane_for(conn, lane, target, actor)
    creation_type = _task_state(conn, actor, {}, type=type, step=step)[0]
    general = creation_type == GENERAL_TYPE
    from .review_gate import engineering
    dev_ticket = engineering(conn, {"type_id": creation_type})
    # Rule 7 shapes an ask to a person (a verb, the ask first). A task on a custom type is a
    # ticket on that type's board, written the way the board writes them, not an ask.
    if not title:
        refuse(conn, actor, "lint", "give it a title that says what you are asking for")
    if is_bot(actor) and is_human(target) and lint and general:
        # A bot giving a person work opens the body with the ask: their card leads with it (lint_person_ask).
        require_person_ask(conn, actor, target, person_ask_of_body(body, title), "assign")
    warnings = (style_problems(conn, actor, lint_human_item(body, title=title), severity)
                if is_human(target) and lint and general else [])
    # A ticket keeps the board's own references ("#18945", "(B/F)") in its title.
    plain = lint_title(title) if is_bot(actor) and TITLE_LINT != "off" and general else []
    if plain and TITLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(plain), severity)
    plain = warnings + plain
    labels = _labels(labels)
    if str(title).lstrip().lower().startswith("incident:") and "incident" not in labels:
        labels.insert(0, "incident")
    if qa is False:
        labels = [key for key in labels if key != "qa"]
    author_qa = qa if qa is not None else True if "qa" in labels else None
    from . import qa_defaults
    qa_rule = qa_defaults.classify(change_paths, qa=author_qa, not_checkable_live=qa_not_checkable_live)
    if dev_ticket and qa_rule["qa"] and "qa" not in labels:
        labels.append("qa")
    if labels and is_bot(actor) and not mover:
        _create_labels(conn, actor, labels)     # people, movers and the keeper as before
    # An automation's turn is the same work as another only when its wake names the same run, step and record: two
    # finds' items (or two runs) may share a title, and the title is never what makes a turn a duplicate.
    turn = _turn_identity(wake_refs)
    scope = ("SELECT id,title FROM tasks WHERE requester=? AND owner=? "
             f"AND coalesce({TR.parent_sql()},'')=? "
             f"AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})")
    if turn:
        same_turn = " AND ".join(f"json_extract(m.refs_json,'$.{k}')=?" for k in AUTOMATION_TURN)
        twins = _rows(conn.execute(
            scope + f" AND EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=tasks.conversation_id AND {same_turn})"
                    " ORDER BY created,id",
            (requester, target, parent_id or "", *ACTIVE_STATUSES, *(turn[k] for k in AUTOMATION_TURN))))
        dup = twins[0] if twins else None
    elif occurrence:
        dup = None
    else:
        dup = _same_title(conn, scope + " ORDER BY created,id",
                          (requester, target, parent_id or "", *ACTIVE_STATUSES), title, keep=same)
    if dup and task_private_readable(conn, actor, task(conn, dup["id"])) and (turn or deduplicate or actor != KEEPER):
        detail = ("An existing task already asks for this work" if VIA.get() and task_private(conn, task(conn, dup["id"]))
                  else f"{dup['id']} already asks {actor_id(target)} for this")
        refuse(conn, actor, "duplicate", detail)
    if number is not None:
        if not (actor == KEEPER or mover or mover is None and can_move(conn, actor)):
            refuse(conn, actor, "identity", f"a task's number is given by a person on the "
                                            f"{', '.join(MOVER_TEAMS)} teams, not by {actor_id(actor)}")
        number = _number_free(conn, actor, number)
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
        watcher = _room_person(conn, started_in, {"requester": requester, "owner": target}) if started_in and not private else None
        conv = open_conversation(conn, actor, list(dict.fromkeys([actor, requester, target, *([watcher] if is_human(watcher) else [])])),
                                 kind="task", subject=title, scope="task", allow_planned=allow_planned)
        dedicated = True
    row = {"id": new_id(), "title": title, "body": body, "requester": requester, "owner": target,
           "status": "open", "due": due, "conversation_id": conv["id"],
           "created": ts, "updated": ts, "done_at": None, "closed_at": None, "closed_by": None,
           "note": "", "lane": lane, "rank": _queue_end(conn, target, top),
           "goal_id": goal_id or None, "next_run": 1 if next_run else 0, "number": number, "private": int(private),
           "occurrence": occurrence or None, "occurrence_step": occurrence_step or None,
           "rule_due_id": (rule_due_id or None) if occurrence else None}
    conn.execute("INSERT INTO tasks (id, title, body, requester, owner, due, "
                 "conversation_id, created, updated, done_at, closed_at, closed_by, note, lane, rank, "
                 "goal_id, next_run, number, private, occurrence, occurrence_step, rule_due_id) VALUES "
                 "(:id, :title, :body, :requester, :owner, :due, "
                 ":conversation_id, :created, :updated, :done_at, :closed_at, :closed_by, :note, "
                 ":lane, :rank, :goal_id, :next_run, :number, :private, :occurrence, :occurrence_step, :rule_due_id)", row)
    _building(conn, row["id"])     # its first status and step are part of task.created, not changes (_task_changed)
    if parent:
        # Filed under its parent as it is made: _task_parent above checked the parent.
        TR._insert(conn, actor, row["id"], parent["id"], "parent", ts)
        _task_event(conn, row["id"], actor, "parent_id", None, parent["id"], "")
        _task_event(conn, parent["id"], actor, "subtask", None, row["id"], "")
    _set_task_tags(conn, actor, row["id"], labels)
    if dev_ticket:
        qa_defaults.create(conn, row["id"], change_paths, qa, qa_not_checkable_live, explicit_qa=author_qa is True)
        if qa_rule["note"]:
            conn.execute("UPDATE tasks SET note=? WHERE id=?", (qa_rule["note"], row["id"]))
    if dedicated:
        conn.execute("UPDATE conversations SET task_id=? WHERE id=?", (row["id"], conv["id"]))
    if number is not None:
        _task_event(conn, row["id"], actor, "number", None, number, "")
    _set_status(conn, actor, {**row, "status": None}, status="open", type=type)
    if parent:
        # A waiting parent now waits on this subtask too: its reason names it (backend/task_holds.py).
        from . import task_holds
        task_holds.refresh(conn, parent["id"])
    public = visibility or type_get(conn, task(conn, row['id'])['type_id'])['default_visibility']
    if public not in ('public', 'private'):
        refuse(conn, actor, 'kind', 'visibility is public or private')
    if private:
        public = 'private'
    if not public_title:
        # A type that defaults to public publishes only tasks created with public text.
        public = 'private'
    if public == 'public' and public_warning:
        public_summary = public_warning + ("\n\n" + public_summary if public_summary else "")
    conn.execute('UPDATE tasks SET visibility=?,public_title=?,public_summary=? WHERE id=?',
                 (public, public_title, public_summary, row['id']))
    for field, value in (('public_title', public_title), ('public_summary', public_summary)):
        if value:   # written public text leaves a record, as a later publication's does
            _task_event(conn, row['id'], actor, field, None, value, '')
    if step is not None:
        task_update(conn, actor, row["id"], step=step)
    if top:
        placed = task(conn, row["id"])
        if placed["step_id"]:
            conn.execute("UPDATE tasks SET step_rank=? WHERE id=?",
                         (_step_end(conn, placed["step_id"], row["id"], top=True), row["id"]))
    if goal_id:
        _task_event(conn, row["id"], actor, "goal_id", None, goal_id, "")
    if plain:
        # warn mode: the problem is on the record, the task still exists (the refuse week follows)
        _task_event(conn, row["id"], KEEPER, "lint", None, "; ".join(plain), "")
        event(conn, actor, "task.lint", row["id"], {"problems": plain})
    event(conn, actor, "task.create", row["id"], {"owner": target, "title": title})
    _building(conn, None)
    hook(conn, "task.created", row["id"], lambda: _task_facts(conn, row["id"], actor, parent_id=parent_id), actor, revision=0)
    from . import task_trigger_rules
    task_trigger_rules.created(conn, row["id"], actor)
    # Give a bot the rest of this relationship's active work with the new task. The runner puts
    # current-message refs in its prompt, so the bot can reuse or reconcile overlapping work
    # without making the human-facing notice noisy or merging distinct deliverables automatically.
    related = []
    if is_bot(target):
        reconcile_statuses = ACTIVE_STATUSES + ("declined",)
        marks = ",".join("?" * len(reconcile_statuses))
        related = _rows(conn.execute(
            "SELECT id,title,status,owner,requester,rank,private FROM tasks WHERE id<>? "
            f"AND status IN ({marks}) AND ((owner=? AND requester=?) OR (owner=? AND requester=?)) "
            "ORDER BY (rank IS NULL),rank,created LIMIT 8",
            (row["id"], *reconcile_statuses, target, actor, actor, target)))
        # The notice lands in this task's conversation. A private task its readers can't read is
        # left out: listed there, it made the whole run private and its reply undeliverable.
        # A public task's own room is read by everyone who may read the task, so it lists no private task.
        readers = set(conv.get("participants") or []) | {conv.get("owner_actor"), target} - {None}
        related = [item for item in related
                   if all(task_private_readable(conn, reader, item) for reader in readers)
                   and (private or not dedicated or not task_private(conn, item))]
    reconcile = {"with_actor": actor, "active": [
        {k: item.get(k) for k in ("id", "title", "status", "owner", "requester")} for item in related
    ], "guidance": "Check for overlap. Reuse shared work; keep distinct decisions and deliverables separate."} if related else None
    if started_in:
        _task_started(conn, task(conn, row["id"]), started_in)
    if parent and review_gate.check_of(conn, parent, title):
        review_gate.remember_heads(conn, actor, row["id"], parent, review_prs)
    # A task a bot files for itself in one of its turns starts its own turn only once that turn has ended
    # (execution.job_refusal): the filing turn may finish it first, and the claim then cancels the notice
    # rather than start a turn that finds nothing left to do (Dev 359).
    filed_in = RUN.get() if is_bot(target) and target == actor else ""
    # A review, QA or Security request has its own event, so a rule can wake reviewers apart
    # from other assignments.
    # Any other is an assignment, named so that rewording the notice never turns it into a note.
    # Both events retain filed_in above: execution.filer_live holds a self-filed task independently of news/wake.
    from . import profiles
    gate_child = {"wake": "review_request"} if profiles.is_review_task(conn, task(conn, row["id"])) \
        else {"news": "assignment"}
    _wake(conn, task(conn, row["id"]), target, f"New task from {actor}: {title}",
          {**({"reconcile": reconcile} if reconcile else {}), **({"filed_in": filed_in} if filed_in else {}),
           **gate_child, **(wake_refs or {})} or None,
          quiet_bots=next_run or quiet_notice, quiet=next_run or quiet_notice, trigger=trigger, source=trigger_source)
    if parent and is_bot(parent["owner"]) and is_human(target):
        # A bot's task now waits on a person: one line in the bot's main chat.
        task_report(conn, task(conn, parent["id"]), "blocked", f"Waiting on {actor_id(target)}: {title}")
    _recount(conn, target)
    _recount(conn, actor)
    made = task(conn, row["id"])
    return {**made, "warnings": plain} if plain else made


def title_key(title):
    """What two task titles are compared by, for a duplicate: case-folded, trimmed, inner spaces collapsed. The
    create and rename refusals, the Task Manager's duplicates and the stale review's twin all use it; the stored
    title stays as typed. In Python, since SQLite's lower() folds ASCII only."""
    return " ".join(str(title or "").split()).casefold()


def _same_title(conn, sql, args, title, keep=None):
    """The first row `sql` reads (id, title) whose title is `title` by title_key, or None. `keep(conn, task_id)`, when
    given, also has to say it is the same work: a file review asks the same words about other versions."""
    key = title_key(title)
    return next((r for r in _rows(conn.execute(sql, args))
                 if title_key(r["title"]) == key and (keep is None or keep(conn, r["id"]))), None)


AUTOMATION_TURN = ("automation_run", "automation_step", "automation_key")   # what names an automation's turn


def _reuse_occurrence(conn, actor, target, task_id, title, wake_refs, trigger, trigger_source):
    """The task an occurrence's step opened before (a retry whose step had already committed): returned as it is. A
    bot turn wakes its bot again for the new run, since the turn is the run's; a task that is already finished takes
    no turn, so that step is refused and the run fails rather than wait for a turn that never comes."""
    row = task(conn, task_id)
    if _turn_identity(wake_refs):
        if row["status"] not in ACTIVE_STATUSES:
            refuse(conn, actor, "duplicate", f"that occurrence's task {task_id} is already finished")
        _wake(conn, row, target, f"Again: {title}", wake_refs, trigger=trigger, source=trigger_source)
    return row


def _turn_identity(wake_refs):
    """The run, step and record an automation turn's wake names (backend/automation_actions.py `start_turn`), or None
    for any other task. Another action of the same automation (a create_task) names its run but not its step."""
    refs = wake_refs or {}
    if any(refs.get(k) in (None, "") for k in AUTOMATION_TURN):
        return None
    return {k: str(refs[k]) for k in AUTOMATION_TURN}


def _retitle(conn, actor, row, title, owner, type_id):
    """A new title gets the checks a new task's title would, against the task as the update
    leaves it: never empty; on a General task for a person, rule 7's title half; no active task
    between the same requester and owner, under the same parent, already called that (task_create's
    duplicate rule: a finished one does not count); for a bot on General, the plain-English check,
    whose warnings are returned to be recorded once the title lands."""
    target = resolve_actor(conn, owner) if owner is not None else row["owner"]
    parent = TR.parent_of(conn, row["id"])
    general = type_id == GENERAL_TYPE
    if not title:
        refuse(conn, actor, "lint", "give it a title that says what you are asking for")
    warnings = style_problems(conn, actor, lint_human_title(title)) if is_human(target) and general else []
    dup = _same_title(conn, "SELECT id,title FROM tasks WHERE id<>? AND requester=? AND owner=? "
                            f"AND coalesce({TR.parent_sql()},'')=? "
                            f"AND status IN ({','.join('?' * len(ACTIVE_STATUSES))}) ORDER BY created,id",
                      (row["id"], row["requester"], target, parent or "", *ACTIVE_STATUSES), title)
    if dup and task_private_readable(conn, actor, task(conn, dup["id"])):
        detail = ("An existing task already asks for this work" if VIA.get() and task_private(conn, task(conn, dup["id"]))
                  else f"{dup['id']} already asks {actor_id(target)} for this")
        refuse(conn, actor, "duplicate", detail)
    plain = lint_title(title) if is_bot(actor) and TITLE_LINT != "off" and general else []
    if plain and TITLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(plain))
    return warnings + plain


@private_task_write
def task_update(conn, actor, task_id, status=None, note=None, owner=None, due=None, body=None,
                lane=None, labels=None, rank=None, mover=None, goal_id=None, quiet=False, type=None, step=None,
                step_rank=None, number=None, title=None, private=None, waiting_on=None, roles=None, visibility=None, public_title=None,
                public_summary=None, approval_id=None, size=None, clear_size=False, review_prs=None, verdict=None,
                verdict_head=None, skip_step_wake=(), review_reader=None, receipt_owner=None, public_allowance=0,
                wake_others=True):
    """Rule 5. The owner may set open|doing|waiting|review|done|declined and a note; it may not close.

    `lane` and `labels` are a mover's to change (`mover` says whether this actor is one; the
    keeper always is). A parent or a blocker is a relation: task_relations.relate.
    `rank` is the position in the owner's queue: a participant may rank its own tasks.
    `step_rank` is its place within its step, lower first, set the same way.
    `goal_id` names the goal the task serves; "" takes it off (backend/goals.py).
    `number` gives a task that has none its number (a mover's, as on create); it never changes.
    `title` renames it, checked as a new task's title would be (`_retitle`).
    `waiting_on` names the person a `waiting` task waits on ("" clears it); only its owner bot names
    one (`_waiting_person`). Any other update to the status, the note or the owner clears it unless
    it names the person again, as does anything that leaves the task unreadable to them.
    `roles` replaces the people in the roles it names ({role: [actors]}; task_roles).
    `verdict` and `verdict_head` record a review or QA subtask's verdict and the head it judged (record_verdict).
    """
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    if private is not None and bool(private) != task_private(conn, row):
        if not task_participant(conn, actor, row):
            refuse(conn, actor, "private", "Only the requester, assignee or their managers can change task privacy")
        if not private and (actor != row["requester"] or not is_human(actor) or VIA.get()):
            refuse(conn, actor, "private", "Only the human requester can publish a private task")
        parent = task(conn, TR.parent_of(conn, task_id) or "")
        if not private and parent:
            if task_private(conn, parent):
                refuse(conn, actor, "private", "Detach this task from its private parent before publishing")
    # The same owner sent in another form (`cmo` for `bot:cmo`) is no reassignment: nothing to record or wake.
    if owner is not None and resolve_actor(conn, owner) == row["owner"]:
        owner = None
    mine = (_task_party(conn, actor, row) or type_bot_works(conn, actor, row)
            or task_manager_acts(conn, actor, row))
    if actor == TASK_MANAGER and actor not in (row["owner"], row["requester"]):
        # Its grant is narrow (task_manager_acts): status, step, note, waits and a reassignment, nothing else.
        beyond = [name for name, value in (
            ("type", type), ("lane", lane), ("labels", labels), ("rank", rank), ("number", number), ("title", title),
            ("body", body), ("due", due), ("goal", goal_id), ("step rank", step_rank), ("private", private),
            ("roles", roles), ("visibility", visibility), ("public title", public_title),
            ("public summary", public_summary), ("size", size), ("review PRs", review_prs), ("verdict", verdict),
            ("verdict head", verdict_head), ("approval", approval_id)) if value is not None]
        if beyond or clear_size:
            refuse(conn, actor, "identity", f"The Task Manager sets status, step, notes, waits and owners only, "
                                            f"not {', '.join(beyond or ['size'])}")
    if mover is None:
        mover = actor == KEEPER or is_human(actor) and can_move(conn, actor)
    mover = task_mover(conn, actor, row, mover)
    labels_only = labels is not None and not clear_size and all(v is None for v in (
        status, note, owner, due, body, lane, rank, goal_id, type, step, step_rank, number, title, private,
        waiting_on, roles, visibility, public_title, public_summary, size, verdict, verdict_head))
    # The bot that manages the task's type may change its labels whatever the type's `bots` setting
    # (_label_writer decides which); nothing else on the task becomes its to change.
    mine = mine or labels_only and is_bot(actor) and task_private_readable(conn, actor, row) and type_manager(conn, row) == actor_id(actor)
    requested_status = status
    state_change = status is not None or type is not None or step is not None
    if state_change and actor != KEEPER:
        # A reviewer comments and gives a verdict; the Builder moves the ticket.
        from . import review_gate
        check = review_gate.reviewing(conn, actor, row)
        if check:
            refuse(conn, actor, "reviewer",
                   f"You are reviewing this ticket, so you don't move it. Finish your review task {check['id']} with "
                   f"your verdict (hub task done {check['id']} --verdict approve|changes --head <sha>); the Builder "
                   "moves the ticket.")
    if not mine and not mover and actor != KEEPER:
        # A closed task's closer reopens it, so an outsider's hint names the closer.
        refuse(conn, actor, "identity",
               f"{task_id} is changed by its owner, requester, their managers, delegates or task movers; " + (
                   f"ask {actor_id(row.get('closed_by') or row['requester'])} to reopen it, or file a new task"
                   if row['status'] == 'closed' else "ask the owner to set doing or open, or file a new task"))
    type_id = row.get("type_id") or GENERAL_TYPE
    queue_return = (requested_status == 'open' and step is None and actor == row['owner']
                    and row['status'] in ('doing', 'waiting', 'review', 'done'))
    if queue_return and is_bot(actor) and not str(note or '').strip():
        refuse(conn, actor, 'lint', 'Putting your task back to open requires a note: '
               f'hub task update {task_id} --status open --note "why it goes back"')
    if state_change:
        type_id, step_id, effective = _task_state(conn, actor, row, status, type, step)
        if status is not None or step is not None:
            status = effective
        if effective == "done":
            from . import profiles
            from .wake_rules import verdict_fields
            if profiles.is_check(conn, row):
                supplied = verdict is not None or actor == row["owner"] and verdict_fields(note)
                since = _check_cycle_start(conn, row)
                if row.get("verdict") and not since["current"]:
                    # A verdict from before the last reopen judged an earlier head; it does not finish this cycle.
                    row = {**row, "verdict": None, "verdict_head": None}
                if not (row.get("verdict") or supplied):
                    # Older releases missed inline clauses. Recover only the owner's saved words from this
                    # cycle, never a requester or another participant's verdict.
                    sources = _rows(conn.execute(
                        "SELECT ts AS at, new AS text FROM task_events WHERE task_id=? AND actor=? AND field='note' "
                        "AND ts>? UNION ALL SELECT m.created AS at, m.body AS text FROM messages m "
                        "LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE m.conversation_id=? "
                        "AND m.from_actor=? AND m.kind IN ('say','notice') AND m.deleted_at IS NULL "
                        f"AND m.created>? AND {MESSAGE_TASK_SQL}=? ORDER BY at DESC",
                        (task_id, row["owner"], since["at"], row["conversation_id"], row["owner"], since["at"], task_id)))
                    for source in sources:
                        if verdict_fields(source["text"]):
                            record_verdict(conn, row["owner"], row, text=source["text"])
                            row = task(conn, task_id)
                            break
                from . import step_flow
                # On a step with a flow, a check finished with no verdict is feedback: it goes to the step's on_fail.
                if not (row.get("verdict") or supplied or step_flow.check_step(conn, row)):
                    refuse(conn, actor, "verdict",
                           "Finish a review or QA check with --verdict approve|changes|pass|fail "
                           "and --head <sha>, or record a VERDICT: clause in its note, comment or final reply")
        # A hand-off to a review step (from another review step too) or to done, with pushed work no pull request
        # carries, leaves an orphan worktree.
        if (effective == "done" and row["status"] != "done"
                or effective == "review" and (row["status"] != "review" or step_id != row.get("step_id"))):
            missing = unlinked_pushes(conn, task_id)
            if missing:
                try:
                    refuse(conn, actor, "worktree_pr", "This task " + _unlinked_text(missing))
                except Refused as exc:
                    exc.task_hold = {"task": task_id, "reason": exc.detail, "kind": "worktree"}
                    raise
        target_step = _one(conn, "SELECT name FROM task_steps WHERE id=?", (step_id,))
        from . import step_flow
        if target_step and step_flow.gated(conn, step_id) and step_id != row.get("step_id"):
            from . import review_gate
            try:
                review_gate.check(conn, actor, {**row, "type_id": type_id}, review_prs, note=note, reader=review_reader,
                                  receipt_owner=receipt_owner)
            except Refused as exc:
                # The move Tico retries once what refused it clears (backend/move_retry.py).
                if getattr(exc, "review_gate", None):
                    exc.review_gate["step"] = target_step["name"]
                    # A move onto another board names that board: its step names mean nothing on the task's own.
                    if type_id != row.get("type_id"):
                        exc.review_gate["type"] = type_id
                raise
        # A move between steps with the same status changes only the pipeline label.
        if step is not None and effective == row["status"]:
            status = None
    closing_step = status == "closed" and step is not None
    self_wait = []
    if state_change and effective == 'waiting' and row.get('blocked_since') and (
            requested_status not in (None, 'waiting') or step is not None and not _one(
                conn, "SELECT 1 FROM task_steps WHERE status='waiting' AND (id=? OR name=?)", (step, step))):
        # The hold keeps the ticket where it asked to go, but not in the working step it named.
        self_wait.append('Task remains Blocked: ' + (row.get('blocked_reason') or 'a hold is still outstanding'))
    if closing_step:
        _task_close_allowed(conn, actor, row, note, mover)
    if waiting_on is not None:
        waiting_on = _waiting_person(conn, actor, row, waiting_on, status, owner, private)
    # A close is the requester's or a person's decision; a bot's update that started before it (a late
    # completion, a late note over the close note, or a hand-off to a new owner) must not undo it. A person
    # reopens a closed task; a requester bot reopens only what no person closed.
    closer = row.get("closed_by") or row["requester"]
    if (row["status"] == "closed" and is_bot(actor)
            and (not task_represents(conn, actor, row["requester"])
                 or is_human(closer) and not task_represents(conn, actor, closer))
            and (status is not None and status != "closed" or note is not None and note != row.get("note")
                 or owner is not None)):
        refuse(conn, actor, "close",
               f"{actor_id(closer)} closed this task, so it stays closed; {task_reopen_hint(actor, row)}; "
               "add anything new as a comment (hub task comment)")
    if (row["status"] == "closed" and is_human(actor) and status is not None and status != "closed" and not mover
            and not _task_close_party(conn, actor, row)):
        refuse(conn, actor, "close", f"Only someone who may close this task reopens it: its requester "
                                     f"({actor_id(row['requester'])}), a person who owns it, their managers, "
                                     "or a person who moves tasks")
    if status is not None and not closing_step:
        if status not in TASK_STATUSES:
            refuse(conn, actor, "kind", f"a status is {'|'.join(TASK_STATUSES)}, not {status}")
        if status == "closed":
            refuse(conn, actor, "close", "close a task with task_close; the requester closes it")
        if (task_represents(conn, actor, row["owner"]) and is_bot(row["owner"])
                and not is_human(actor) and status not in OWNER_STATUSES):
            refuse(conn, actor, "close",
                   f"the owner may set {'|'.join(OWNER_STATUSES)}, not {status}")
        if status == "ready" and is_bot(actor):
            refuse(conn, actor, "close", "ready to ship is set when the pull request merges, not by the bot")
        # A bot parking a task it asked itself for has nobody to answer it unless it has filed
        # something to wait on first. Several stranded tasks were this.
        # Validate the requested dependency, not the old one: callers can park work and
        # attach its blocker atomically. Normal dependency/access checks still run below.
        waiting_row = dict(row)
        # A move to waiting starts with nobody waited on unless this update names someone.
        waiting_row["waiting_on"] = (waiting_on or None) if waiting_on is not None else None
        if (status == "waiting" and row["status"] != "waiting" and is_bot(row["owner"])
                and not waiting_for(conn, waiting_row)):
            # A move by hand into a waiting step is a manual hold (declared_hold, the same rule as _set_status), which
            # never reopens by itself. A person's move needs no note: nobody has to answer a hold they declared.
            durable = declared_hold(conn, actor, row, step, step_id)
            if row["owner"] == row["requester"] and not str(note or "").strip() and not (durable and is_human(actor)):
                refuse(conn, actor, "lint",
                       "You asked for this task yourself, so nobody will answer it: say what you are waiting on "
                       "in a note, file the child task, blocker or approval first, name the person with --on, "
                       "or keep working")
            # A note naming what it waits on parks it, but a note wakes nobody (bots parked tasks "waiting on
            # BotOps" that BotOps never heard of): the hint goes back with the write.
            self_wait = self_wait if durable else [f"nothing this task waits on is recorded, so it goes back to open in "
                         f"{STRANDED_GRACE_HOURS} hours: file a subtask for whoever must act (hub task create "
                         f"--parent {task_id} --owner <bot or person>), relate a blocker, name the person with --on, "
                         "or set --due for when to look again"]
    # A bot that needs a person on its own task asks them in one small subtask they own (_ask_in_subtask): the task
    # stays the bot's, blocked on it, and is never itself a card in their Needs you. An open question to them on the
    # task (`hub task question`) is already that card, so the task waits on them as before.
    asked_in = None
    if (waiting_on and is_bot(row["owner"]) and task_represents(conn, actor, row["owner"]) and is_human(waiting_on)
            and not any(a["to_actor"] == waiting_on for a in open_task_asks(conn, row))):
        said = str(note if note is not None else row.get("note") or "").strip()
        if said.startswith(BLOCKED_ON.format(name=person_name(conn, waiting_on), ask="")):
            said = said.split(": ", 1)[1]          # the note this rule wrote, sent back as it was
        require_person_ask(conn, actor, waiting_on, said, "waiting")
        asked_in = _ask_in_subtask(conn, actor, row, waiting_on, said)
        note, waiting_on = BLOCKED_ON.format(name=person_name(conn, waiting_on), ask=said), ""
    if number is not None and number == row.get("number"):
        number = None               # sent back unchanged
    wanted = [k for k, v in (("lane", lane), ("number", number)) if v is not None]
    if not is_bot(actor) and labels is not None and not any(
            is_bot(row[k]) and task_represents(conn, actor, row[k]) for k in ("owner", "requester")):
        wanted.insert(1, "labels")  # a person who is not a mover changes no label, as before
    if wanted and not mover:
        refuse(conn, actor, "identity",
               f"{', '.join(wanted)} on a task {'is' if len(wanted) == 1 else 'are'} changed by a person on "
               f"the {', '.join(MOVER_TEAMS)} teams, not by {actor_id(actor)}")
    approved = labels is not None and not mover and _label_writer(conn, actor, row, labels, approval_id)
    if lane is not None:
        lane = _lane_for(conn, lane, row["owner"], actor)    # a task moves only onto company
    if title is not None:
        title = str(title).strip()
    if title == row["title"]:
        title = None                # sent back unchanged: nothing to check or record
    plain = _retitle(conn, actor, row, title, owner, type_id) if title is not None else []
    ts = now()
    sets, args = [], {}
    if number is not None:
        if row.get("number") is not None:
            refuse(conn, actor, "kind", f"this task is #{row['number']}; a task's number never changes")
        # Written before any move below, so a move onto a numbered type finds it already there.
        conn.execute("UPDATE tasks SET number=? WHERE id=?", (_number_free(conn, actor, number), task_id))
        _task_event(conn, task_id, actor, "number", None, number, note or "")
        sets.append("updated=:updated")
    if step_rank is not None:
        sets.append("step_rank=:step_rank")
        args["step_rank"] = float(step_rank)
    if private is not None and bool(private) != task_private(conn, row):
        sets.append("private=:private")
        args["private"] = int(private)
        _task_event(conn, task_id, actor, "private", int(task_private(conn, row)), int(private), "")
    # Who it waits on goes with the wait as said: a new status or owner starts with nobody waited on unless this
    # update names the person again. A note alone keeps the person: it is detail beneath the wait, which only
    # clearing it or answering ends (backend/task_holds.py).
    changed = (status is not None and status != row["status"]
               or owner is not None and resolve_actor(conn, owner) != row["owner"])
    person = (waiting_on or None) if waiting_on is not None else (None if changed else row.get("waiting_on"))
    # A note that waits on a person is put in front of them, so it is checked like a title or body.
    checked = ("title", "body", "note") if person else ("title", "body")
    # ...and it is read as the ask: one plain request to them, not the bot's working notes (warned, as titles are).
    # The note is refused without a plain ask (require_person_ask), unless an open question to them on the task
    # already asks (`hub task question`): the card leads with that question, and the note is only warned about.
    if person and is_bot(actor) and is_human(person) and (person != row.get("waiting_on") or note is not None):
        asked = any(a["to_actor"] == person for a in open_task_asks(conn, row))
        said = note if note is not None else row.get("note")
        # A note-only edit under a wait already recorded keeps that wait: its new words are warned about, not refused.
        kept = waiting_on is None and person == row.get("waiting_on")
        if asked or kept:
            plain = plain + style_problems(conn, actor, lint_waiting_note(said, person, asked))
        else:
            require_person_ask(conn, actor, person, said, "waiting")
    # A bot handing a General task to a person opens its body with the ask, as on create.
    if owner is not None and is_bot(actor) and type_id == GENERAL_TYPE:
        handed = resolve_actor(conn, owner)
        if is_human(handed) and handed != row["owner"]:
            require_person_ask(conn, actor, handed, person_ask_of_body(
                body if body is not None else row.get("body"), title or row["title"]), "assign")
    for field, value in (("title", title), ("note", note), ("due", due), ("body", body), ("lane", lane)):
        if value is None:
            continue
        if field in checked:
            path_mentioned(conn, actor, f"task {field}", str(value), task_id)
        sets.append(f"{field}=:{field}")
        args[field] = value
        _task_event(conn, task_id, actor, field, row.get(field), value, note or "")
    if labels is not None:
        _set_task_tags(conn, actor, task_id, labels, note or "", approval_id=approval_id, approved=approved)
        sets.append("updated=:updated")
    if owner is not None:
        new_owner = _reach(conn, actor, owner)
        if actor == TASK_MANAGER and new_owner != row["owner"]:
            from . import task_manager
            if not task_manager.same_department(conn, row["owner"], new_owner):
                refuse(conn, actor, "identity", "The Task Manager reassigns only within the owner's department; "
                       "leave a comment for the department instead")
        if is_human(new_owner) and new_owner != row["owner"]:
            from .strategy_acts import TASK, gate
            # The proposal lands in the Strategy Bot log, which names the task only to a reader who may open it.
            gate(conn, actor, "person_task", f"give {TASK} to {person_name(conn, new_owner)}", task_id=task_id)
        _takes_tasks(conn, actor, new_owner, row["title"], TR.parent_of(conn, task_id))
        if new_owner != row["owner"]:
            _learner(conn, actor)
            _learning(conn, actor, new_owner)
        if private_tasks_default(conn, new_owner):
            sets.append("private=1")
        if is_bot(row['owner']) and 'detail_json' in {r[1] for r in conn.execute('PRAGMA table_info(task_links)')}:
            _demote_sessions(conn, task_id, owner=new_owner)
            for link in task_links(conn, task_id):
                if link['kind'] == 'worktree':
                    detail = json.loads(link.get('detail_json') or '{}')
                    detail.setdefault('owner', row['owner'])
                    conn.execute('UPDATE task_links SET detail_json=? WHERE id=?', (json.dumps(detail), link['id']))
        sets.append("owner=:owner")
        args["owner"] = new_owner
        _task_event(conn, task_id, actor, "owner", row["owner"], new_owner, note or "")
        if new_owner != row["owner"]:
            from . import task_run_models
            task_run_models.moved(conn, actor, task_id)
        if new_owner != row["owner"] and row.get("guest"):
            # The guest was the old owner's to bring in; a new owner starts with the task's two parties.
            sets.append("guest=NULL")
            _task_event(conn, task_id, actor, "guest", row["guest"], None, "")
        if new_owner != row["owner"] and is_bot(row["owner"]) and row.get("conversation_id"):
            # The goal Tico set for the old owner ends with its ownership: only the owner's turns end a task goal,
            # and a person owner has none to end it. A new bot owner's first turn sets its own.
            from .chat_goals import clear_task_goal
            room = conversation(conn, row["conversation_id"])
            if room:
                clear_task_goal(conn, room)
        if new_owner != row["owner"] and rank is None:
            rank = _queue_end(conn, new_owner)      # the bottom of the new queue
    if rank is not None:
        sets.append("rank=:rank")
        args["rank"] = float(rank)
        # A queue move writes no event of its own, except on a task a Strategy Bot decision changed: its Undo keeps a
        # later move, even one back to the same place (backend/strategy_acts.py).
        if float(rank) != row.get("rank") and _strategy_changed(conn, task_id):
            _task_event(conn, task_id, actor, "rank", row.get("rank"), float(rank), note or "")
    if person != row.get("waiting_on"):
        sets.append("waiting_on=:waiting_on")
        args["waiting_on"] = person
        _task_event(conn, task_id, actor, "waiting_on", row.get("waiting_on"), person, note or "")
    if goal_id is not None and (goal_id or None) != row.get("goal_id"):
        sets.append("goal_id=:goal_id")
        args["goal_id"] = goal_id or None
        _task_event(conn, task_id, actor, "goal_id", row.get("goal_id"), goal_id or None, note or "")
    # A task keeps only the times of the state it is in: one moved out of closed loses its close time (the close
    # rule reads closed_at IS NULL, so a done task that kept it would never close again), one out of done its done time.
    if status == "done":
        sets.append("done_at=:done_at")
        args["done_at"] = ts
    cleared = {"done": ("closed_at", "closed_by"), "declined": ("done_at", "closed_at", "closed_by")}.get(
        status, ("done_at", "closed_at", "closed_by") if status in ACTIVE_STATUSES else ())
    for field in cleared:
        if row.get(field) is not None:
            _task_event(conn, task_id, actor, field, row[field], None, note or "")
            sets.append(field + "=NULL")
    # A reopened check judges again; record any new verdict after the reopen event starts its cycle.
    reopening = row["status"] in ("done", "closed", "declined") and status in ACTIVE_STATUSES
    if reopening:
        for field in ("verdict", "verdict_head"):
            if row.get(field) is not None:
                _task_event(conn, task_id, actor, field, row[field], None, note or "")
        conn.execute("UPDATE tasks SET verdict=NULL, verdict_head=NULL WHERE id=?", (task_id,))
        row = {**row, "verdict": None, "verdict_head": None}
    # After the status write is decided and before the parent hears of it: its wake carries the verdict.
    verdict_set = False if reopening else record_verdict(conn, actor, row, verdict, verdict_head, note)
    if not sets and not state_change and not roles and not clear_size and not verdict_set and all(
            v is None for v in (visibility, public_title, public_summary, size)):
        return row
    # A person finishing it is news for every bot on it; the bots that asked it open questions are read
    # before the status write supersedes those questions.
    finishing = status == "done" and row["status"] != "done" and is_human(actor)
    askers = [m["from_actor"] for m in open_task_asks(conn, row)] if finishing else []
    if finishing:
        # A waiting bot's person-owned question can live in a child instead of waiting_on on this card.
        # Capture its requester before completion supersedes those questions.
        askers += [child['requester'] for tid in TR.child_ids(conn, task_id)
                   if (child := task(conn, tid)) and child['owner'] == actor
                   and child['status'] in ACTIVE_STATUSES and is_bot(child['requester'])]
    # A bot's result rewritten or handed to someone else, before any status move below: that run's outcome.
    if title is not None or body is not None and body != row.get("body"):
        RT.turn_result(conn, actor, row, "edited", event)
    elif args.get("owner", row["owner"]) != row["owner"]:
        RT.turn_result(conn, actor, row, "overridden", event)
    if closing_step:
        task_close(conn, actor, task_id, note=note or "", quiet=quiet, type=type, step=step, mover=mover)
    elif state_change:
        _set_status(conn, actor, row, status, type, step, note or "", skip_step_wake=skip_step_wake)
    if queue_return:
        returned = task(conn, task_id)
        if returned.get('blocked_since'):
            # Keep the live hold, but clearing it returns to the queue the owner selected.
            steps = type_get(conn, returned['type_id'])['steps']
            deck = next((s for s in steps if s['name'] == 'On Deck' and s['status'] == 'open'), None)
            deck = deck or next((s for s in steps if s['status'] == 'open'), None)
            conn.execute('UPDATE tasks SET blocked_from_step=?,blocked_from_status=? WHERE id=?',
                         (deck['id'] if deck else None, 'open', task_id))
        # A derived Blocked state can hide the requested open transition from status history.
        _task_event(conn, task_id, actor, 'queue_return', row['status'], 'open', note or '')
    if reopening:
        verdict_set = record_verdict(conn, actor, row, verdict, verdict_head, note)
    args["id"] = task_id
    args["updated"] = ts
    if sets:
        conn.execute(f"UPDATE tasks SET {', '.join(sets)}, updated=:updated WHERE id=:id", args)
    if args.get("private"):
        # Made private: a reconnect card's person may not read it now (backend/reconnects.py).
        from .reconnects import access_changed as reconnects_access_changed
        reconnects_access_changed(conn, task_id)
    if person and status == "waiting":
        conn.execute("UPDATE tasks SET blocked_kind='dependency',blocked_reason=? WHERE id=? AND coalesce(blocked_kind,'')<>'manual'", ("Waiting on " + person_name(conn, person), task_id))
    after = task(conn, task_id) or {}
    if (after.get("title"), after.get("owner")) != (row["title"], row["owner"]):
        # The tasks it holds name it by title and owner (backend/task_holds.py).
        from . import task_holds
        task_holds.holders(conn, task_id)
    if (task(conn, task_id) or {}).get("status") == "waiting":
        # Its reason follows what holds it after any change: a person wait set or cleared, a note, a status
        # (backend/task_holds.py). Someone else clearing the person it waited on clears that hold. The owner clearing
        # it in its own write chose the status that write leaves (often waiting on something untracked): no reopen.
        from . import task_holds
        gone = row.get("waiting_on") if (not person and row.get("waiting_on") and row["status"] == "waiting"
                                         and status in (None, "waiting") and actor != row["owner"]) else None
        task_holds.refresh(conn, task_id, f"No longer waiting on {task_holds.name(conn, gone)}" if gone else None)
        if note is not None:
            from .check_blocked import changed as check_changed
            check_changed(conn, task_id)    # a waiting check's ticket shows its note, written after the status move
    if roles:
        # After the rest is saved, so who may be added is checked against the new owner and privacy;
        # the checks above already said this actor may change the task.
        try:
            TRo.set_roles(conn, actor, task_id, roles, note=note or "", checked=True)
        except ValueError as exc:
            refuse(conn, actor, "kind", str(exc))
    if title is not None:
        # The task's own thread was named after it; a room many tasks share keeps its subject.
        conn.execute("UPDATE conversations SET subject=? WHERE id=? AND task_id=? AND subject=?",
                     (title, row["conversation_id"], task_id, row["title"]))
    from .roadmaps import publication
    updated_row = task(conn, task_id)
    if task_private(conn, updated_row):
        conn.execute("UPDATE tasks SET visibility='private' WHERE id=?", (task_id,))
        updated_row = task(conn, task_id)
    publication(conn, actor, updated_row, visibility, public_title, public_summary, approval_id,
                allowance=public_allowance)
    if size is not None or clear_size:
        import math
        if size is not None and (not math.isfinite(size) or size < 0):
            refuse(conn, actor, 'kind', 'Size is a nonnegative finite number')
        conn.execute('UPDATE tasks SET size=?,updated=? WHERE id=?', (size, now(), task_id))
        _task_event(conn, task_id, actor, 'size', row.get('size'), size, '')
    plain = plain + self_wait
    if plain:
        _task_event(conn, task_id, KEEPER, "lint", None, "; ".join(plain), "")
        event(conn, actor, "task.lint", task_id, {"problems": plain})
    still_open = open_children(conn, task_id) if status == "done" and row["status"] != "done" else []
    if still_open:
        # Done with subtasks still open: they stay open, and the record says which.
        _task_event(conn, task_id, KEEPER, "children", None, "Still open: " + "; ".join(still_open), "")
    event(conn, actor, "task.update", task_id, {"status": status, "note": note,
                                                **({"still_open": still_open} if still_open else {})})
    after = isolate_private_task(conn, task(conn, task_id))
    if after.get("waiting_on") and not task_private_readable(conn, after["waiting_on"], after):
        # Made private, or moved under a private parent: it no longer goes in front of that person.
        conn.execute("UPDATE tasks SET waiting_on=NULL WHERE id=?", (task_id,))
        _task_event(conn, task_id, KEEPER, "waiting_on", after["waiting_on"], None, "")
        after = task(conn, task_id)
    if owner is not None or private is not None or task_private(conn, after) != task_private(conn, row):
        conn.execute("DELETE FROM task_delegations WHERE task_id=?", (task_id,))
    if task_private(conn, after) and not task_private(conn, row):
        # A task under a private parent is private (task_relations.set_parent's rule on attach): every task below
        # this one goes private with it, or a colleague could read a subtask of a parent they cannot see.
        for below in descendants(conn, task_id):
            if not task_private(conn, below):
                _made_private(conn, actor, below["id"])
    # A bot that asked for `tasks` is always told and never woken for it, so there is nothing to
    # weigh: the news is free. Everyone else keeps the original rule, where the only way to
    # spare a bot the run was to withhold the notice.
    notify_requester = (wake_others and status in ("done", "declined") and actor != row["requester"]
                        and (status == "declined" or tasks_only(conn, after["requester"])
                             or TR.parent_of(conn, task_id) and is_bot(after["requester"])
                             or _owed_the_news(conn, after)))
    if note is not None and not notify_requester and not quiet:
        _mirror_task_note(conn, actor, after, note)
    # A person finishing a subtask under a bot's task: the bot hears it once, on the parent.
    if after["status"] == "done" and row["status"] != "done" and not after.get("verdict"):
        # A check finished with no verdict moves its ticket like one with changes (backend/verdict_moves.py).
        from . import check_pools, step_flow, verdict_moves
        if step_flow.check_step(conn, after):
            check_pools.safely(conn, verdict_moves.after_verdict, after)
    subtask_told = None if closing_step else _subtask_wake(conn, actor, after, row["status"], note, quiet)
    if subtask_told == after["requester"]:
        notify_requester = False
    # A person answering a bot's ask (_ask_in_subtask), yes or no: each task blocked on it opens again.
    answered = set()
    if status in ("done", "declined") and row["status"] != status and not closing_step:
        answered = {tid for tid in TR.blocked_ids(conn, task_id) if _answered(conn, actor, tid, after, subtask_told)}
    if status in ("done", "declined"):
        if subtask_told:
            answered.add(TR.parent_of(conn, task_id))  # the result also satisfies this parent's blocker notice
        _unblock(conn, after, told=answered)
    # A decline always goes back: nobody is doing the work, which is news whoever asked for it.
    if notify_requester:
        refs = None
        if is_human(after["requester"]):
            # Keep completion notices out of ordinary Slack reply mirroring, including quiet
            # updates. One eligible notice per task/status also covers reopen/retry cycles.
            duplicate = _one(conn, "SELECT 1 FROM messages WHERE json_extract(refs_json,'$.task')=? "
                             "AND json_extract(refs_json,'$.task_completion.status')=? "
                             "AND json_extract(refs_json,'$.task_completion.notify')=1 LIMIT 1",
                             (task_id, status))
            from . import people as P
            saved = _one(conn, "SELECT value_json FROM registry_metadata WHERE key='people'") if _one(
                conn, "SELECT 1 FROM sqlite_master WHERE name='registry_metadata'") else None
            roster = P.load(_json(saved["value_json"], {}) if saved else {"people": humans(conn)})
            person = P.person(actor_id(after["requester"]), roster) or {}
            linked = person.get("slack_id") or (human(conn, after["requester"]) or {}).get("slack_id")
            if not linked and _one(conn, "SELECT 1 FROM sqlite_master WHERE name='slack_events'"):
                linked = _one(conn, "SELECT 1 FROM slack_events WHERE actor=? "
                              "AND state IN ('routed','recorded') LIMIT 1", (after["requester"],))
            refs = {"task_completion": {"status": status, "owner": after["owner"],
                    "note": str(note or "").splitlines()[0] if note else "",
                    "notify": bool(not quiet and row["status"] != status and not duplicate
                                   and linked and person.get("notify_slack_task_done") is not False)}}
        _wake(conn, after, after["requester"],
              (_marked_done(conn, actor, after["title"]) if status == "done" and is_human(actor)
               and is_bot(after["requester"]) else
               f"{'Finished' if status == 'done' else 'Declined'}: {after['title']}")
              + (f"\n{note}" if note and not quiet else "")
              + (f"\nStill open: {'; '.join(still_open)}" if still_open else ""),
              refs={**(refs or {}), "news": "task_done" if status == "done" else "task_declined",
                    **({"wake_batch": "completion"} if status == "done" and is_bot(actor)
                       and not _awaited_by(conn, after, after["requester"]) else {})},
              quiet_bots=True, human_completion=is_human(actor) and status == 'done',
              completion_actionable=bool(str(note or '').strip()) and not quiet)
    if finishing:
        # Not only the requester: the owner that parked it waiting on this person, a bot that asked them a
        # question on it, and the parent's owner. Each once, never whoever tapped; tasks-only bots quietly,
        # and only bots that may read the task (_wake).
        parent = task(conn, TR.parent_of(conn, task_id) or "")
        told = {actor, KEEPER, subtask_told} | ({after["requester"]} if notify_requester else set())
        for who in dict.fromkeys([after["requester"], after["owner"], *askers, (parent or {}).get("owner")]):
            if not who or who in told or not is_bot(who) or not bot_reads_task(conn, who, after):
                continue
            told.add(who)
            # A bot's own task finished with nothing said asks nothing of it: it hears quietly, with no run. A note
            # is the person talking to it, and a bot that asked on it or owns its parent has work waiting on it.
            idle = (who == after["owner"] and not (note and not quiet) and who not in askers
                    and who != (parent or {}).get("owner"))
            _wake(conn, after, who, _marked_done(conn, actor, after["title"]) + (f"\n{note}" if note and not quiet else "")
                  + (f"\nStill open: {'; '.join(still_open)}" if still_open else ""), {"news": "task_done"},
                  quiet_bots=True, quiet=idle, human_completion=is_human(actor),
                  completion_actionable=bool(str(note or '').strip()) and not quiet or who in askers
                  or who == row['owner'] and row.get('waiting_on') in (actor, actor_id(actor)))
    if status == "review" and row["status"] != "review" and actor != after["requester"]:
        # Review is the requester's turn. Nothing else tells a bot that asked for the work, so
        # without this a handoff between bots waits until someone notices.
        # On a Dev ticket every review-status step is a hand-off to a reviewer, not to the requester: its move waits as
        # a note. Anywhere else the requester is the reviewer and is woken.
        from . import review_gate
        _wake(conn, after, after["requester"], f"Ready for review: {after['title']}"
              + (f"\n{note}" if note and not quiet else ""),
              {"news": "ready_for_review"} if review_gate.engineering(conn, after) else {"wake": "review_handback"})
    if (status == "open" or owner is not None and owner != row["owner"]) and actor != after["owner"]:
        _wake(conn, after, after["owner"], f"Open: {after['title']}", {"news": "assignment"})
    # A bot's milestone goes to its main chat as one quiet line (task_report).
    if is_bot(after["owner"]):
        if after["status"] in ("done", "declined") and row["status"] != after["status"]:
            task_report(conn, after, after["status"], note if note is not None else after.get("note") or after.get("checkpoint"))
        elif (after["status"] == "waiting" and after.get("waiting_on") and is_human(after["waiting_on"])
              and (row["status"] != "waiting" or row.get("waiting_on") != after["waiting_on"])):
            task_report(conn, after, "blocked", f"Waiting on {actor_id(after['waiting_on'])}"
                        + (f": {after['note']}" if after.get("note") else ""))
    if after.get("step_id") != row.get("step_id") and not closing_step:
        # A check step with a pool files its check for the current head, as Tico (backend/check_pools.py).
        # A step the ticket's tag skips sends it straight on instead (backend/step_flow.py, skip_tag).
        from . import check_pools, step_flow, verdict_moves
        if step_flow.skip_target(conn, after, step_flow.step(conn, after.get("step_id"))):
            check_pools.safely(conn, verdict_moves.skip, after)
        else:
            check_pools.safely(conn, check_pools.entered, actor, after, review_prs)
    recount(conn, [after["owner"], after["requester"], row["owner"]])   # the old owner too, on a handoff
    if not closing_step:
        _parent_finished(conn, after, row["status"], actor, told=subtask_told, wake=wake_others)
    if asked_in:
        after = {**after, "asked_in": {k: asked_in[k] for k in ("id", "title", "owner")}}
    return {**after, "warnings": plain} if plain else after


BLOCKED_ON = "Blocked on {name}: {ask}"


def person_name(conn, person):
    return (human(conn, person) or {}).get("name") or actor_id(person)


def _ask_title(ask):
    """A subtask's title from the ask: its first question, else its first sentence."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(ask).strip()) if s.strip()]
    return next((s for s in sentences if s.endswith("?")), sentences[0] if sentences else str(ask).strip())


ASK_CARD = "task.ask_subtask"


def ask_card(conn, card):
    """The person `card` asks for this rule (_ask_in_subtask), or None: a subtask it filed for them, or a bot's own
    question to them that a task of the bot was blocked on. Marked by an event the bot that asked wrote on the card."""
    row = _one(conn, "SELECT detail_json FROM events WHERE actor=? AND action=? AND target=? ORDER BY ts LIMIT 1",
               (card["requester"], ASK_CARD, card["id"]))
    return _json(row["detail_json"], {}).get("to") if row else None


def person_card(conn, row, person):
    """The open card this rule already put in front of `person` in this task's tree, or None. Only a card it made
    (ask_card) is joined, never another task the person was given, and never across privacy: a private task's ask
    stays on its own private card, one per task, and a public task never joins a private one."""
    for tid in TR.blocker_ids(conn, row["id"]):
        card = task(conn, tid)
        if (card and card["status"] in ACTIVE_STATUSES and card["owner"] == person and ask_card(conn, card) == person
                and bool(task_private(conn, card)) == bool(task_private(conn, row))):
            return card
    if task_private(conn, row):
        return None
    above = TR.ancestors(conn, row["id"])
    root = above[-1] if above else row["id"]
    skip = {row["id"], *above}
    for tid in TR.descendant_ids(conn, root):
        card = task(conn, tid)
        if (tid in skip or not card or card["status"] not in ACTIVE_STATUSES or task_private(conn, card)
                or card["owner"] != person or ask_card(conn, card) != person):
            continue
        return card
    return None


def own_question(conn, actor, row, person):
    """A question `actor` already put to `person` under this task (`hub task question`): its own open subtask
    waiting on them. That is the card, so no second one is filed."""
    for tid in TR.child_ids(conn, row["id"]):
        card = task(conn, tid)
        if (card and card["owner"] == actor and card["status"] in ACTIVE_STATUSES and card.get("waiting_on") == person
                and any(a["to_actor"] == person for a in open_task_asks(conn, card))):
            return card
    return None


QUESTION_FOLDED = "task.question_folded"
EARLIER_ASKS_KEPT = 10      # earlier wordings a folded question keeps; older ones are still in the task's history


def question_topic(value):
    """A topic key as compared: its letters and digits in any script, case folded, words joined by single hyphens
    ("Backup model" == "backup-model", "预算" stays "预算"). None only when there is no letter or digit in it."""
    return "-".join(re.findall(r"[^\W_]+", str(value or "").casefold()))[:80] or None


def open_question(conn, actor, person, *, parent=None, topic=None, private=False):
    """`actor`'s open question to `person` (`hub task question`) that a new one on the same topic joins, or None.

    One open question per topic: with a topic key, the open question with that key, under any task; without one, an
    open question under the same parent task that has no key either (a bot names a second, different decision on the
    same task with its own key). Open: the question task is live and waits on them, and its ask is unanswered. Never
    across privacy: a private question joins only a private one, and only under the same parent."""
    topic = question_topic(topic)
    if not topic and not parent:
        return None
    rows = _rows(conn.execute(
        "SELECT t.id AS task_id, m.id AS message_id, json_extract(m.refs_json,'$.topic') AS topic "
        "FROM tasks t JOIN messages m ON m.conversation_id=t.conversation_id JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE t.owner=? AND t.waiting_on=? AND t.status='waiting' "
        f"AND m.from_actor=? AND m.to_actor=? AND m.kind='ask' AND m.deleted_at IS NULL AND {OPEN_ASK_SQL} "
        f"AND {MESSAGE_TASK_SQL}=t.id AND json_extract(m.refs_json,'$.questions[0].id') LIKE 'decision%' "
        "ORDER BY t.created, t.rowid", (actor, person, actor, person)))
    for row in rows:
        card = task(conn, row["task_id"])
        if bool(task_private(conn, card)) != bool(private):
            continue
        same_parent = bool(parent) and TR.parent_of(conn, card["id"]) == parent
        if private and not same_parent:
            continue
        if (question_topic(row["topic"]) == topic) and (topic or same_parent):
            return {"task": card, "ask": message(conn, row["message_id"])}
    return None


def fold_question(conn, actor, found, question, ask, *, title=None, body=None):
    """Put a new question on a topic into `found`, the open one (open_question): the person keeps one card, with the
    latest wording and options, and the earlier asks listed under it. The ask is rewritten in place, so nothing new is
    sent: no second Needs-you item, no Slack or mail line, no run. Its clock restarts (ask_clock), so the person gets
    the team's days from the latest wording before a default goes ahead. Returns the ask as it now reads."""
    card, asked = found["task"], found["ask"]
    old = asked.get("refs") or {}
    was = ((old.get("questions") or [{}])[0].get("question") or asked.get("body") or "").strip()
    earlier = [*(old.get("earlier") or []), {"question": was, "at": ask_clock(asked)}][-EARLIER_ASKS_KEPT:]
    at = now()
    # A review's files and spend are the latest wording's: an ask without them drops the earlier ones.
    refs = {k: v for k, v in old.items() if k not in ("questions", "if_no_answer", "who", "review", "attachments", "spend")}
    # Each wording is its own question id ("decision-2"...): an answer the person picked on the wording before is
    # keyed by the old id and refused (task_review.answer_task), never taken as an answer to this one.
    questions = [{**q, "id": f"decision-{len(earlier) + 1}"} if not i else q for i, q in enumerate(ask["questions"])]
    refs.update({**ask, "questions": questions, "earlier": earlier, "asked_again_at": at})
    if not ask.get("if_no_answer"):
        refs.pop("if_no_answer", None)
    conn.execute("UPDATE messages SET body=?, refs_json=?, edited_at=? WHERE id=?",
                 (question, json.dumps(refs), at, asked["id"]))
    before = "\n".join(f"- {e['question']} ({str(e.get('at') or '')[:10]})" for e in reversed(earlier) if e.get("question"))
    text = (body or question).strip() + (f"\n\nAsked before:\n{before}" if before else "")
    task_update(conn, actor, card["id"], title=title or question, body=text, note=question,
                waiting_on=card["waiting_on"], quiet=True)
    event(conn, actor, QUESTION_FOLDED, card["id"],
          {"message": asked["id"], "to": card["waiting_on"], "topic": refs.get("topic"), "asks": len(earlier) + 1})
    return message(conn, asked["id"])


def _ask_in_subtask(conn, actor, row, person, ask):
    """One question, one card: `actor`, the bot that owns `row`, needs `person`. Unless its own question to them is
    already open under the task (own_question), or this rule already filed them a card in the task's tree
    (person_card), file one small subtask they own whose title is the ask (its question, when it has one) and whose
    body is the ask; their Needs you shows it with Yes and No when it asks a yes-or-no question. A second bot in the
    same tree asking them adds its ask to that card as a comment instead. The task is blocked on the card; when it is
    finished, the bot hears it once on the task and the task opens again (_answered). Returns the card."""
    card = own_question(conn, actor, row, person) or person_card(conn, row, person)
    if card is None:
        card = task_create(conn, actor, _ask_title(ask), ask, person, parent_id=row["id"], lint=False)
    elif card["owner"] == person and ask not in (card["title"], str(card.get("body") or "").strip()) and not any(
            str(m.get("body") or "").strip() == ask for m in task_comments(conn, card["id"], actor=actor)):
        task_comment(conn, actor, card["id"], ask)
    if card["requester"] == actor and not ask_card(conn, card):
        event(conn, actor, ASK_CARD, card["id"], {"for": row["id"], "to": person})
    if card["id"] not in TR.blocker_ids(conn, row["id"]):
        TR.add_blocker(conn, actor, row["id"], card["id"], note="asked in a subtask")
    return card


def supersede_ask_cards(conn, row, status):
    """Retire generated asks whose work ended, keeping cards still needed by another live task."""
    ended = {row["id"], *TR.descendant_ids(conn, row["id"])}
    candidates = set(ended)
    for tid in ended:
        candidates.update(TR.blocker_ids(conn, tid))
    for tid in candidates - {row["id"]}:
        card = task(conn, tid)
        if not card or card["status"] not in ACTIVE_STATUSES or not ask_card(conn, card):
            continue
        if any(blocked not in ended and (task(conn, blocked) or {}).get("status") in ACTIVE_STATUSES
               for blocked in TR.blocked_ids(conn, tid)):
            continue
        task_close(conn, KEEPER, tid, note=f"Ask superseded: its task was {TASK_ENDED[status]}.", quiet=True)


def _waiting_person(conn, actor, row, value, status, owner=None, private=None):
    """The person `hub task update --status waiting --on <person>` names, or "" to clear it.

    It is what makes work that waits on a person show as needing that person: the task is in
    their Needs you and batch, and the bot counts toward `needs_human`. Only a person who can read
    the task: a private task cannot be put in front of anyone outside its requester and owner, except the one
    guest its owning bot brings into a private task with no person on it (task_reach)."""
    value = str(value or "").strip()
    if not value:
        return ""
    # The Needs-you item is the owner's ask: nobody else names who the owner waits on.
    if not is_bot(row["owner"]) or not task_represents(conn, actor, row["owner"]):
        refuse(conn, actor, "identity", "Only the bot that owns a task or its managers say who it waits on")
    if owner is not None and resolve_actor(conn, owner) != row["owner"]:
        refuse(conn, actor, "kind", "Hand the task over first; its new owner says who it waits on")
    if (status if status is not None else row["status"]) != "waiting":
        refuse(conn, actor, "kind", "--on names who a waiting task waits on: set --status waiting with it")
    person = resolve_actor(conn, value)
    if person and is_bot(person):
        return _waiting_bot(conn, actor, row, person)
    if not person or not is_human(person):
        refuse(conn, actor, "waiting_on",
               f"{value} is not a person or an active bot on the team: a task waits on a person with --on, or on "
               "another task with hub task wait <task> --on task:<other> (then --on bot:<its owner> names that bot)")
    if not task_private_readable(conn, person, row if private is None else {**row, "private": int(bool(private))}):
        # A private task with no person on it can take one, its guest, when its owning bot waits on them.
        if private is not None or not bot_only(row) and not row.get("guest"):
            refuse(conn, actor, "private",
                   f"This task is private and {actor_id(person)} cannot read it: file a task for them instead")
        refusal = task_reach_refusal(conn, actor, row, person)
        if refusal:
            refuse(conn, actor, "private", refusal)
        task_reach(conn, actor, row, person)
    return person


def _waiting_bot(conn, actor, row, bot_actor_id):
    """`--status waiting --on bot:<slug>`: the task waits on that bot's prerequisite work, or the write is refused.

    A bot alone does not say which of its work must finish, so the wait is the open blocker it owns (`hub task wait
    <task> --on task:<other>`, a blocked_by relation): that holds the task's work while it is open and wakes its owner
    with the outcome when it is done, declined or closed (_unblock). Nothing more is stored, so `waiting_on` stays the
    person a task waits on (their Needs you); the note is the reason shown beside the bot's name."""
    slug = actor_id(bot_actor_id)
    if (bot(conn, slug) or {}).get("state") != "active":
        refuse(conn, actor, "waiting_on",
               f"{slug} is not an active bot on the team, so nothing it does can resume this task: wait on a person "
               "with --on, or on an active bot's task with hub task wait <task> --on task:<other>")
    # The first open blocker, in the order the task's relations are served (TR.grouped), is the one its views name,
    # so the bot named here must own that one: naming a bot behind it would show another owner's name.
    first = (TR.live_blockers(conn, row["id"], ACTIVE_STATUSES) or [None])[0]
    if not first:
        refuse(conn, actor, "waiting_on",
               f"Nothing {slug} owns blocks this task, so Tico cannot tell which of its work resumes it: link that "
               f"task first with hub task wait {row['id']} --on task:<its task>, then --on {bot_actor_id}")
    if first["owner"] != bot_actor_id:
        # Keep diagnostics independent of the blocker's read audience.
        refuse(conn, actor, "waiting_on",
               f"This task's first open blocker belongs to another owner, so its views would not name {slug}: wait on that "
               f"blocker's owner, or take it off with hub task wait {row['id']} --on task:<it> --remove first")
    return ""


def _task_party(conn, actor, row):
    """Whether `actor` is on the task as a change to it counts (task_update): its requester, owner or their managers, a delegate it
    lent its rights to for now, or a party to a task above it."""
    if task_participant(conn, actor, row):
        return True
    if _one(conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires>? LIMIT 1",
            (row["id"], actor, now())):
        return True
    return task_ancestor_party(conn, actor, row)


def _task_close_party(conn, actor, row):
    """_task_party for closing and reopening. A bot that owns a task does not close what another asked for, so its
    managers inherit no close from it: they close through the requester, or a person who is a party here or above."""
    def closes(party, requester):
        return task_represents(conn, actor, party) and (requester or is_human(party))
    if closes(row["requester"], True) or closes(row["owner"], False):
        return True
    if _one(conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires>? LIMIT 1",
            (row["id"], actor, now())):
        return True
    if not task_private_readable(conn, actor, row):
        return False
    for parent_id in TR.ancestors(conn, row["id"]):
        parent = task(conn, parent_id)
        if not parent:
            break
        if closes(parent["owner"], False) or closes(parent["requester"], False):
            return True
    return False


def _task_close_allowed(conn, actor, row, note, mover=None):
    _task_private_writer(conn, actor, row)
    # A person closes a task they can edit, as task_update decides: on it (_task_close_party), or a mover. The person
    # it waits on may close it too: it asked them to decide, and "not now" is a decision. Reading it is not enough:
    # someone refused its status and note must not be able to end it.
    mover = task_mover(conn, actor, row, mover)
    if is_human(actor) and not _task_close_party(conn, actor, row) and row.get("waiting_on") != actor:
        if not (can_move(conn, actor) if mover is None else mover):
            refuse(conn, actor, "close", f"Only someone who can edit this task closes it: its requester "
                                         f"({actor_id(row['requester'])}), its owner, the person it waits on, "
                                         "or a person who moves tasks")
    # A board's shipped column is ready; a bot that works the board (a deploy bot) closes what shipped.
    shipped = row["status"] == "ready" and type_bot_works(conn, actor, row)
    shipped = shipped or task_manager_acts(conn, actor, row)       # it closes with a note: the one rule below, not a mover
    if actor == TASK_MANAGER and actor != row["requester"] and not (note or "").strip():
        refuse(conn, actor, "close", "The Task Manager closes a task only with a note saying why it is finished")
    if not task_represents(conn, actor, row["requester"]) and not is_human(actor) and actor != KEEPER and not shipped:
        refuse(conn, actor, "close", f"{actor_id(row['requester'])} asked for this; only they or their managers close it")
    # A person closes without giving a reason; the requesting bot is still told it was closed (task_close wakes it).


@private_task_write
def task_close(conn, actor, task_id, note="", quiet=False, *, type=None, step=None, mover=None):
    """Rule 5. Closing is an explicit requester, keeper or decision of a person who can edit the task; work delegation
    is not acceptance. `mover` says whether a person may move any task (the caller knows the environment owner)."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_close_allowed(conn, actor, row, note, mover)
    if row["status"] == "closed":
        if type is not None or step is not None:
            _set_status(conn, actor, row, status="closed", type=type, step=step, note=note or "")
        return task(conn, task_id)
    ts = now()
    _set_status(conn, actor, row, status="closed", type=type, step=step, note=note or "")
    conn.execute("UPDATE tasks SET closed_at=?, closed_by=?, updated=?, "
                 "note=COALESCE(NULLIF(?, ''), note) WHERE id=?", (ts, actor, ts, note or "", task_id))
    still_open = open_children(conn, task_id)
    if still_open:
        # Closed with subtasks still open: they stay open, and the record says which.
        _task_event(conn, task_id, KEEPER, "children", None, "Still open: " + "; ".join(still_open), "")
    event(conn, actor, "task.close", task_id, {"note": note, **({"still_open": still_open} if still_open else {})})
    after = task(conn, task_id)
    # A person closing a subtask under a bot's task: the bot hears it once, on the parent.
    subtask_told = _subtask_wake(conn, actor, after, row["status"], note, quiet)
    # Closing an open ask card (_ask_in_subtask) settles the ask like a decline: each task blocked on it opens again.
    answered = set()
    if row["status"] not in ("done", "declined"):
        answered = {tid for tid in TR.blocked_ids(conn, task_id) if _answered(conn, actor, tid, after, subtask_told)}
    if subtask_told:
        answered.add(TR.parent_of(conn, task_id))
    _unblock(conn, after, told=answered)
    said = (f"Closed: {after['title']}" + (f"\n{note}" if note else "")
            + (f"\nStill open: {'; '.join(still_open)}" if still_open else ""))
    # Closing work that was already done, with nothing said, is acceptance: news, not work. It
    # goes to the bot's inbox without a run. Hundreds of runs were a bot
    # waking to read "Closed:" and saying "acknowledged", hours of agent time. The keeper's
    # own closes carry no instruction either.
    # A note from a person, or a close before the work was done, still wakes: that may change
    # what the bot does next.
    accepted = row["status"] in ("done", "declined") and (actor == KEEPER or not str(note or "").strip())
    # Work not started yet, closed with nothing said, asks nothing of its bot either: it never starts it.
    accepted = accepted or (row["status"] == "open" and not str(note or "").strip())
    # A next-run task closed before any run carried it is a cancel: the bot never saw it, so
    # there is nothing to tell it and no run to spend telling it.
    accepted = quiet or accepted or next_run_waiting(conn, row)
    # The asking side closing what the owning bot already finished (done, or handed over for review) tells
    # the bot nothing it needs: no wake. A close note that says something new still reaches it, quietly.
    finished = (is_bot(after["owner"]) and row["status"] in ("done", "review")
                and (actor in (row["requester"], KEEPER) or is_human(actor)))
    new_note = str(note or "").strip() and str(note or "").strip() != str(row.get("note") or "").strip()
    told = [who for who in dict.fromkeys([after["owner"], after["requester"]]) if who != actor]
    for who in told:   # everyone but whoever tapped
        if who == subtask_told or (finished and who == after["owner"] and not new_note):
            continue
        # Another bot closing what this one asked for is a nonblocking completion unless it waits on it; an owner
        # told its work was closed, and anything a person said, wake at once.
        batch = who == after["requester"] and is_bot(actor) and not _awaited_by(conn, after, who)
        _wake(conn, after, who, said, {"news": "task_done", **({"wake_batch": "completion"} if batch else {})},
              quiet_bots=True,
              quiet=accepted or bool(finished and who == after["owner"] and not new_note),
              human_completion=is_human(actor), completion_actionable=bool(new_note) and not quiet)
    parent = task(conn, TR.parent_of(conn, after["id"]) or "")
    if parent and parent["owner"] != actor and parent["owner"] not in told and parent["owner"] != subtask_told:
        _wake(conn, after, parent["owner"], f"Child closed: {after['title']}", quiet_bots=True, quiet=True)
    _parent_finished(conn, after, row["status"], actor, told=subtask_told)
    _recount(conn, after["owner"])
    _recount(conn, after["requester"])
    return after


def waiting_for(conn, row, *, children=True):
    """What a task's owner is waiting for, in words, or None when there is nothing (`wait_detail`)."""
    return (wait_detail(conn, row, children=children) or {}).get("why")


def live_blocker(conn, task_id, *, children=True):
    """The first unfinished task blocking this one; with `children=False`, the first that is not one of its subtasks."""
    if children:
        return TR.live_blocker(conn, task_id, ACTIVE_STATUSES)
    own = {r[0] for r in conn.execute(TR.CHILD_IDS, (task_id,))}
    return next((b["id"] for b in TR.live_blockers(conn, task_id, ACTIVE_STATUSES) if b["id"] not in own), None)


def wait_detail(conn, row, *, children=True):
    """What a task's owner is waiting for, or None when there is nothing: {"why": words, "on": the actor it waits on, or
    None when no one actor holds it ("person" for an undecided approval, "external" for GitHub or a release)}.

    Something is: a question its owner asked about it that nobody has answered, an unfinished
    child, an unfinished blocker, an approval not yet decided, or a person it names (`waiting_on`).
    `children=False` leaves out its open subtasks, as children and as blockers: what still holds a subtask's
    result back from it (auto_tasks.waiting with `result`).
    """
    from .read_cache import rows as cached_rows
    asks = [dict(r) for r in cached_rows(conn, WAIT_ASKS_SQL, (row["owner"], row["id"]))]
    if asks:
        answered = answers_to(conn, [a["id"] for a in asks])
        open_ask = next((a for a in asks if a["id"] not in answered), None)
        if open_ask:
            return {"why": "an unanswered question", "on": open_ask["to_actor"]}
    from . import profiles, review_gate
    engineering = review_gate.engineering(conn, row)
    open_children = [dict(r) for r in cached_rows(conn, OPEN_CHILDREN_SQL, (row["id"],))] if children else []
    check = None
    for child in open_children:
        # Pipeline checks are work at the current step, not dependencies preventing it.
        # An explicit blocked_by relation below still holds on any such child.
        if engineering and is_bot(child["owner"]) and profiles.is_review_task(conn, child, parent_id=row["id"]):
            if check is None:
                from .check_blocked import detail as check_detail
                check = check_detail(conn, row) or False     # unless the check itself is blocked (Dev 553)
            if check:
                return check
            continue
        return {"why": "an open child task", "on": child["owner"]}
    blocker = live_blocker(conn, row["id"], children=children)
    if blocker:
        return {"why": "an open blocker", "on": (_one(conn, "SELECT owner FROM tasks WHERE id=?", (blocker,)) or {}).get("owner")}
    if _one(conn, "SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL LIMIT 1", (row["id"],)):
        return {"why": "an undecided approval", "on": "person"}
    from .reconnects import hold as reconnect_hold
    reconnect = reconnect_hold(conn, row["id"])
    if reconnect:
        return {"why": reconnect["why"], "on": reconnect["on"]}
    if row.get("waiting_on"):
        return {"why": f"{actor_id(row['waiting_on'])} to act", "on": row["waiting_on"]}
    from .worktree_waits import for_task as worktree_waits
    worktrees = worktree_waits(conn, row["id"])
    if worktrees:
        wait = worktrees[0]
        return {"why": f"a worktree for {wait['repo']}" + (" to be ready" if wait["state"] == "reserved" else " to be available"),
                "on": "external"}
    from .milestone_waits import pending as milestone_pending
    milestone = milestone_pending(conn, row["id"])
    if milestone:
        return {"why": milestone, "on": "external"}
    # Only a PR GitHub reports on can wake it: one the App has sent an event for, or in a repository it reaches.
    armed = _one(conn, "SELECT l.title FROM task_links l WHERE l.task_id=? AND l.kind='pr' "
                       "AND l.state IN ('open','draft','merged') AND json_extract(l.detail_json,'$.wake_once') IS NOT NULL "
                       "AND (json_extract(l.detail_json,'$.tracked') OR EXISTS(SELECT 1 FROM repositories r "
                       "WHERE lower(r.full_name)=lower(l.repo) AND r.reachable=1)) LIMIT 1", (row["id"],))
    if armed:
        return {"why": f"GitHub on {armed['title']}", "on": "external"}
    if row.get("blocked_kind") == "manual":
        return {"why": row.get("blocked_reason") or "an owner-declared hold", "on": None}
    return None


STRANDED_GRACE_HOURS = 2     # a bot's task waiting on nothing goes back to open after this (sweep_stranded)


def sweep_stranded(conn, at=None, grace_hours=STRANDED_GRACE_HOURS):
    """Hourly: a bot's `waiting` task with nothing to wait on goes back to work.

    Bot tasks were found waiting with no live blocker, some for days on nothing at all. A waiting routine task is worse than idle: the scheduler lets it
    absorb every later occurrence without a word, so the routine stops. A routine's task that a
    later occurrence already landed on is closed, and the next occurrence opens a fresh one; any
    other is reopened with a note that wakes its owner.
    """
    at = at or now()
    cutoff = shift(at, hours=-grace_hours)
    moved = []
    from . import move_retry, review_gate
    # Its clock runs from when its last hold cleared, else from when its wait began (backend/task_holds.py): a note
    # written while it waits on nothing does not hold it back.
    clock = "coalesce(holds_cleared_at, blocked_since, updated)" if "holds_cleared_at" in {
        r[1] for r in conn.execute("PRAGMA table_info(tasks)")} else "updated"
    for row in _rows(conn.execute("SELECT * FROM tasks WHERE status='waiting' AND owner LIKE 'bot:%' "
                                  f"AND {clock}<=? ORDER BY {clock}", (cutoff,))):
        with isolated(conn, "sweep_stranded", row["id"]):
            if (waiting_for(conn, row) or not_yet_due(row, at)
                    or (bot(conn, actor_id(row["owner"])) or {}).get("state") != "active"):
                continue
            # Worktree holds stay on every board: unlinked pushes are still outstanding. Engineering gate holds
            # clear through task_blocked reconciliation, and a move Tico still retries clears when it is made.
            # Only a gate hold on another board that no retry will clear is released here, or nothing ever would.
            if (row.get("blocked_kind") == "worktree" or row.get("blocked_kind") == "gate" and (
                    review_gate.engineering(conn, row) or move_retry.remembered(conn, row, parse_ts(at)))):
                continue
            superseded = _has_table(conn, "schedule_occurrences") and _one(
                conn, "SELECT 1 FROM schedule_occurrences o WHERE o.task_id=? AND EXISTS("
                      "SELECT 1 FROM schedule_occurrences later WHERE later.schedule_id=o.schedule_id "
                      "AND later.occurrence>o.occurrence) LIMIT 1", (row["id"],))
            if superseded and row["requester"] == KEEPER:
                task_close(conn, KEEPER, row["id"],
                           "Closed by the keeper: it was waiting on nothing while later runs of its routine "
                           "came due. The next run opens a fresh task.")
                moved.append((row["id"], "closed"))
            else:
                # Setting it open is what wakes the owner ("Open: <title>"); the note says why.
                task_update(conn, KEEPER, row["id"], status="open",
                            note="Back to open: it was waiting, but no question, child task, blocker or "
                                 "approval is outstanding. Continue it, ask, or finish it.")
                moved.append((row["id"], "open"))
    return moved


def task_ask(conn, actor, task_id, body, park=True):
    """Rule 5's one clarifying question: the owner gets one ask before an answer comes back. `park` False leaves the
    task's status as it is (a question ask ingest sends back, backend/ask_ingest.py)."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    if not task_represents(conn, actor, row["owner"]):
        refuse(conn, actor, "identity", "only the task owner or its managers can ask its requester")
    if task_represents(conn, actor, row["owner"]):
        unanswered = unanswered_ask(conn, row)
        if unanswered:
            refuse(conn, actor, "one-question",
                   "you already asked about this task; wait for the answer")
    msg = say(conn, actor, row["requester"], body, conversation_id=row["conversation_id"],
              kind="ask", refs={"task": task_id})
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))     # its question is part of it
    if park and row["status"] in ("open", "doing", "review"):
        task_update(conn, actor, task_id, status="waiting")
    return msg


def _task_changed(conn, task_id, actor, field, old, new, revision):
    """One outgoing-webhook event for a status, owner or step change: the specific one when the status has a name
    (done, declined, closed, blocked, reopened), else task.state_changed."""
    if getattr(conn, "building", None) == task_id:
        return
    if field == "owner":
        name = "task.assigned"
    elif field == "step":
        name = "task.step_changed"
    elif new in ("done", "declined", "closed"):
        name = f"task.{new}"
    elif new == "waiting":
        name = "task.blocked"
    elif old in TASK_ENDED and new in ACTIVE_STATUSES:
        name = "task.reopened"
    else:
        name = "task.state_changed"
    from . import task_trigger_rules
    task_trigger_rules.enqueue(conn, name, task_id, actor, revision, field=field, new=new)
    # The owner column is written just after its event, so a new owner is said from `new`.
    hook(conn, name, task_id, lambda: {**_task_facts(conn, task_id, actor, change={"field": field, "from": old, "to": new}),
                                       **({"owner": new} if field == "owner" else {})}, actor, revision=revision)


def _task_event(conn, task_id, actor, field, old, new, note=""):
    event_id = new_id()
    via = VIA.get()
    if via and not any(r[1] == "via" for r in conn.execute("PRAGMA table_info(task_events)")):
        conn.execute("ALTER TABLE task_events ADD COLUMN via TEXT")
    # A Strategy Bot decision (or its Undo) stamps each change it makes with its action id (backend/strategy_acts.py).
    from .strategy_acts import ACTION
    action = ACTION.get()
    extra = (("via", via),) * bool(via) + (("action_id", action),) * bool(action)
    conn.execute("INSERT INTO task_events (id, task_id, ts, actor, field, old, new, note"
                 + "".join(", " + k for k, _ in extra) + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?" + ", ?" * len(extra) + ")",
                 (event_id, task_id, now(), actor, field,
                  None if old is None else str(old), None if new is None else str(new), note,
                  *(v for _, v in extra)))
    if field in ("status", "owner"):
        # A reconnect card stands only on its unfinished task with the owner it was opened for (backend/reconnects.py).
        from .reconnects import moved as reconnects_moved
        reconnects_moved(conn, task_id, field, old, new)
    if field in ("status", "owner", "step"):
        _task_changed(conn, task_id, actor, field, old, new, event_id)


def _strategy_changed(conn, task_id):
    """Whether a Strategy Bot decision (or its Undo) is writing this task now or has changed it before."""
    from .strategy_acts import ACTION
    return bool(ACTION.get()) or bool(conn.execute(
        "SELECT 1 FROM task_events WHERE task_id=? AND action_id IS NOT NULL LIMIT 1", (task_id,)).fetchone())


def _task_ref(value):
    """One task id out of a ref a bot wrote: a string, or the first string of a list.

    `refs` is a free-form dict, and bots have been sending `{"task": ["<id>"]}` for a while. Binding that list straight into SQL raised sqlite3.ProgrammingError inside
    `jobs/claim` and stalled every claim on the runner whose queue it headed.
    """
    if isinstance(value, (list, tuple)):
        value = next((v for v in value if isinstance(v, str) and v.strip()), None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def message_task_id(message, conversation=None):
    """The task a message is about: refs on the message, else a dedicated task conversation."""
    refs = (message or {}).get("refs") or {}
    return (_task_ref(refs.get("task")) or _task_ref(refs.get("task_id"))
            or _task_ref((conversation or {}).get("task_id")))


def _task_ref_sql(path):
    """The SQL twin of `_task_ref` for one `$.key` of `m.refs_json`: an array yields its first element."""
    return (f"CASE WHEN json_type(m.refs_json,'{path}')='array' THEN json_extract(m.refs_json,'{path}[0]') "
            f"ELSE json_extract(m.refs_json,'{path}') END")


# Job/status SQL: the queued message names the task; a dedicated task thread still has task_id.
# A job a wake preset holds (backend/wake_presets.py) is queued but starts no run until its batch is let go: a check
# for a run already waiting, before it writes an immediate wake, counts only the jobs that are not held.
def unheld_sql(alias="j"):
    return f"coalesce({alias}.held,0)=0"


MESSAGE_TASK_SQL = f"coalesce({_task_ref_sql('$.task')}, {_task_ref_sql('$.task_id')}, cv.task_id)"
# wait_detail's and answers_to's one-task reads (auto_tasks.prime_waits seeds them for many tasks)
WAIT_ASKS_SQL = ("SELECT m.id, m.to_actor FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                 f"WHERE m.from_actor=? AND m.kind='ask' AND m.superseded_at IS NULL AND {MESSAGE_TASK_SQL}=? "
                 "ORDER BY m.created")
ANSWER_SQL = "SELECT * FROM messages WHERE in_reply_to=? AND kind='answer' AND deleted_at IS NULL ORDER BY created LIMIT 1"
ANSWERED_BY_SQL = "SELECT answered_by FROM messages WHERE id=?"
OPEN_CHILDREN_SQL = (f"SELECT id,owner,title FROM tasks WHERE id IN ({TR.CHILD_IDS}) AND status NOT IN ('done','closed','declined') "
                     "ORDER BY created,id")


def _mirror_task_note(conn, actor, task_row, note):
    """Copy a task note into the chat room a person's task lives in so people and later turns can read it.
    A bot's task has its own room and reports its milestones to the bot's main chat (task_report) instead."""
    if task_private(conn, task_row) or is_bot(task_row["owner"]):
        return
    text = str(note or "").strip()
    if not text:
        return
    conv = conversation(conn, task_row["conversation_id"])
    if not conv or conv.get("kind") != "chat" or conv.get("scope") not in ("personal", "shared"):
        return
    if _one(conn, "SELECT id FROM messages WHERE conversation_id=? AND from_actor=? AND body=?",
            (conv["id"], actor, text)):
        return
    people = [p for p in conv.get("participants") or [] if is_human(p)]
    target = conv.get("owner_actor") if conv.get("scope") == "personal" else (people[0] if people else None)
    if not target or target == task_row["owner"]:
        others = [p for p in (conv.get("participants") or []) if p != task_row["owner"]]
        target = others[0] if others else None
    if not target or target == task_row["owner"]:
        return
    try:
        say(conn, actor, target, text, conversation_id=conv["id"],
            refs={"task": task_row["id"], "note": True})
    except Refused:
        return


# ----------------------------------------------------------------------------- task rooms
# A bot's task has a room of its own (kind "task"), so its traffic stays out of the chat its main
# session reads. The main chat gets two kinds of quiet keeper line about it: `refs.task_started`
# when the task is filed from that chat, and `refs.task_report` at each milestone (blocked, done,
# declined, stuck). Both are written with `quiet`, so they queue no run; the main session reads
# them in its history the next time something real wakes it (execution.lane_history).
REPORT_STATES = ("blocked", "done", "declined", "stuck")
REPORT_NOTE_CHARS = 140


def _room_person(conn, room, row):
    """The person a main-chat line about `row` is addressed to: the room's owner, else a person in it."""
    people = [p for p in room.get("participants") or [] if is_human(p)]
    if room.get("owner_actor"):
        return room["owner_actor"]
    return row["requester"] if row.get("requester") in people else people[0] if people else row["owner"]


def _room_reads(conn, room, row):
    """Whether everyone in the room may read the task, so a line naming it says nothing new to anyone."""
    readers = set(room.get("participants") or []) | {room.get("owner_actor")} - {None}
    return all(task_private_readable(conn, who, row) for who in readers)


def _task_started(conn, row, room):
    """The quiet "Started task" line in the chat a bot's task was filed from. A private task is named
    only in a room whose every reader may read it."""
    if not room or room["id"] == row["conversation_id"]:
        return None
    if task_private(conn, row) and not _room_reads(conn, room, row):
        return None
    msg = _write_message(conn, KEEPER, _room_person(conn, room, row), f"Started task: {row['title']}", room, "notice",
                         {"task": row["id"], "task_started": True, "quiet": True}, None, None)
    event(conn, KEEPER, "task.started_in", row["id"], {"conversation": room["id"], "message": msg["id"]})
    return msg


def _main_room(conn, row):
    """The owning bot's main chat for a report on `row`: the chat it was started from, else the bot's room
    with its requester (its operator when a bot asked), else its shared room. None when there is none:
    reports never open a room."""
    slug = actor_id(row["owner"])
    started = _one(conn, "SELECT detail_json FROM events WHERE actor=? AND action='task.started_in' AND target=? "
                         "ORDER BY ts DESC LIMIT 1", (KEEPER, row["id"]))
    room = conversation(conn, _json(started["detail_json"], {}).get("conversation")) if started else None
    if room and not room.get("closed_at"):
        return room
    person = row["requester"] if is_human(row["requester"]) else None
    if not person and _has_table(conn, "bot_config"):
        found = _one(conn, "SELECT operator FROM bot_config WHERE bot=?", (slug,))
        person = human_actor(found["operator"]) if found and found.get("operator") else None
    for sql, args in (("scope='personal' AND owner_actor=? AND room_key=?", (person, slug)),
                      ("scope='shared' AND room_key=?", (slug,))):
        if None in args:
            continue
        found = _one(conn, f"SELECT id FROM conversations WHERE {sql} AND closed_at IS NULL "
                           "ORDER BY COALESCE(last_message_at,created) DESC LIMIT 1", args)
        if found:
            return conversation(conn, found["id"])
    return None


def task_report(conn, row, state, note=""):
    """One quiet line in the owning bot's main chat when its task is blocked on a person, done, declined
    or stuck: "<title>: <state>. <note>". It starts no run; the main session reads it with the chat.
    The same state is not reported twice in a row (a status change in between starts a new row).
    A private task is reported only in a room whose every reader may read it; otherwise it is skipped."""
    if not row or not is_bot(row["owner"]) or state not in REPORT_STATES or not _actor_exists(conn, row["owner"]):
        return None
    last = _one(conn, "SELECT ts,detail_json FROM events WHERE actor=? AND action='task.report' AND target=? "
                      "ORDER BY ts DESC LIMIT 1", (KEEPER, row["id"]))
    if last and _json(last["detail_json"], {}).get("state") == state and not _one(
            conn, "SELECT 1 FROM task_events WHERE task_id=? AND field='status' AND ts>? LIMIT 1", (row["id"], last["ts"])):
        return None
    room = _main_room(conn, row)
    if not room or room["id"] == row["conversation_id"]:
        return None
    if task_private(conn, row) and not _room_reads(conn, room, row):
        return None
    text = " ".join(str(note or "").split())
    if len(text) > REPORT_NOTE_CHARS:
        text = text[:REPORT_NOTE_CHARS - 1].rstrip() + "…"
    msg = _write_message(conn, KEEPER, _room_person(conn, room, row), f"{row['title']}: {state}." + (f" {text}" if text else ""),
                         room, "notice", {"task": row["id"], "task_report": state, "quiet": True}, None, None)
    event(conn, KEEPER, "task.report", row["id"], {"state": state, "conversation": room["id"], "message": msg["id"]})
    return msg


def task_from_chat(conn, task_id):
    """Whether a bot's task came from a chat (filed there, or moved out of one): its turns' replies reach
    the person in its room as they would have in the chat, not only once it is done (execution.complete)."""
    return bool(_one(conn, "SELECT 1 FROM events WHERE actor=? AND action IN ('task.started_in','task.room') "
                           "AND target=? LIMIT 1", (KEEPER, task_id)))


def _task_room(conn, row, parties):
    """A new room of row's own (kind "task"), with `parties` in it; the task points at it."""
    room = new_id()
    conn.execute("INSERT INTO conversations(id,kind,subject,participants_json,created,scope,task_id) "
                 "VALUES(?,?,?,?,?,?,?)", (room, "task", row["title"], _dump(parties), now(), "task", row["id"]))
    conn.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (room, row["id"]))
    return conversation(conn, room)


def give_task_room(conn, row):
    """A bot's live task that still lives in a chat room (filed before task rooms) moves to a room of its
    own, as execution.claim hands out a turn on it. Its earlier messages stay in the chat; the new room
    opens with a line pointing there. Returns the task as it now is."""
    if not row or not is_bot(row["owner"]) or row["status"] not in ACTIVE_STATUSES:
        return row
    old = conversation(conn, row["conversation_id"])
    if not old or old.get("kind") != "chat":
        return row
    person = _room_person(conn, old, row) if not task_private(conn, row) else None
    room = _task_room(conn, row, list(dict.fromkeys(p for p in (row["requester"], row["owner"], person) if p)))
    name = (bot(conn, row["owner"]) or {}).get("display_name") or actor_id(row["owner"])
    _write_message(conn, KEEPER, row["owner"], f"Earlier messages are in {name} chat.", room, "notice",
                   {"task": row["id"], "quiet": True, "moved_from": old["id"]}, None, None)
    event(conn, KEEPER, "task.room", row["id"], {"from": old["id"], "to": room["id"]})
    return task(conn, row["id"])


def _owed_the_news(conn, child):
    """Whether finishing this task should wake the person or bot that asked for it.

    Only about finishing. A decline is always delivered: it means nobody is doing the work.

    Waking a bot is a whole run for it, and most tasks a bot files are filed and forgotten: an
    analyst that files six tickets is not waiting on any of them, and six "Finished:" notices
    cost six runs that read the news and have nothing to do about it.

    A bot is woken when a person finishes the decision it requested, or when it is blocked on
    a delegated bot task - the flow in `policies/handoffs.md`, where the requester files a
    child with `--parent` and parks the parent as `waiting`. A person is always told: a
    message to a person starts nothing.
    """
    requester = child.get("requester")
    if not is_bot(requester):
        return True
    if is_human(child.get("owner")):
        return True
    parent_id = TR.parent_of(conn, child["id"])
    if not parent_id:
        return False
    parent = task(conn, parent_id)
    return bool(parent and parent["status"] == "waiting" and parent["owner"] == requester)


def tasks_only(conn, actor):
    """Whether this bot is set to `bot_contact: tasks`: work arrives as a task and nothing else.

    Everything gated on this is opt-in per bot, so a fleet that has not asked for it keeps the
    behaviour `policies/handoffs.md` describes.
    """
    if not is_bot(actor) or not _has_table(conn, "bot_config"):
        return False
    row = _one(conn, "SELECT config_json FROM bot_config WHERE bot=?", (actor_id(actor),))
    if not row or not row.get("config_json"):
        return False
    return (_json(row["config_json"], {}) or {}).get("bot_contact") == "tasks"


# A next-run task still waiting for its owner's next run: open, with no resume condition holding it, and not taken
# by a run that is under way or finished. A run that failed or was interrupted gives it back, so the next one
# carries it again, and so does one that finished without working it or saying when to (backend/quiet_work.py
# settle); a task the bot already moved on (doing, done) is its own business by then.
NEXT_RUN_WAITING_SQL = (
    "t.next_run=1 AND t.status='open' AND t.snoozed_until IS NULL AND t.resume_json IS NULL "
    "AND (t.carried_by IS NULL OR NOT EXISTS("
    "SELECT 1 FROM attempts a WHERE a.id=t.carried_by AND a.state IN ('leased','running','completed')))")


# A carrier in one of these states still holds its next-run task (quiet_work.prime reads them for a whole list).
NEXT_RUN_CARRIER_STATES = ("leased", "running", "completed")
NEXT_RUN_CARRIER_SQL = "SELECT 1 FROM attempts WHERE id=? AND state IN ('leased','running','completed')"


def next_run_waiting(conn, row):
    """Whether this task (a row) is a next-run task still waiting to be carried."""
    if not row or not row.get("next_run") or row.get("status") != "open" or row.get("snoozed_until") \
            or row.get("resume_json"):
        return False
    if not row.get("carried_by"):
        return True
    try:
        return not _one(conn, NEXT_RUN_CARRIER_SQL, (row["carried_by"],))
    except sqlite3.OperationalError:     # a local hub has no attempts table and no runs
        return True


def next_run_tasks(conn, bot, exclude=None):
    """The next-run tasks waiting for `bot` (a slug), oldest first."""
    return _rows(conn.execute(
        f"SELECT t.* FROM tasks t WHERE t.owner=? AND t.id IS NOT ? AND {NEXT_RUN_WAITING_SQL} "
        "ORDER BY t.created,t.id", ("bot:" + bot, exclude)))


def chunks(values, size=500):
    """`values` in slices small enough for one statement's `IN (?,...)`."""
    values = list(values)
    return [values[start:start + size] for start in range(0, len(values), size)]


def next_run_tasks_by(conn, bots):
    """`next_run_tasks` for many bots in one query per 500: {slug: [task, ...]}, each oldest first."""
    out = {bot: [] for bot in bots}
    for part in chunks(out):
        for row in _rows(conn.execute(
                f"SELECT t.* FROM tasks t WHERE t.owner IN ({','.join('?' * len(part))}) AND {NEXT_RUN_WAITING_SQL} "
                "ORDER BY t.created,t.id", ["bot:" + bot for bot in part])):
            out[actor_id(row["owner"])].append(row)
    return out


def own_stuck_blockers(conn, row, at=None):
    """The blockers of `row` when every one is its own subtask that nothing visible will finish, else [].

    Such a subtask belongs to a bot, has gone quiet, and waits on no task, no person's answer and no time of
    its own: what it waits for, if anything, is its parent, which waits on it. A hold kept open until its
    parent's window is released is the case seen; a question for a person in a subtask is not (it
    belongs to the person), nor is a subtask with a future due date, a snooze, a stored resume or a pending
    milestone wait (each brings it back by itself; once the date or snooze has passed, or the milestone has
    woken it, it is quiet again). Review and a queued or active run of the subtask also move it independently.
    A parent or subtask waiting on a person belongs to that person. These waits exempt the parent from the
    stall watcher."""
    from .milestone_waits import pending as milestone_pending
    from .quiet_work import own_job
    at = at or now()
    if row.get("status") == "waiting" and is_human(row.get("waiting_on")):
        return []
    blockers = TR.live_blockers(conn, row["id"], ACTIVE_STATUSES)
    if not blockers:
        return []
    quiet = shift(at, seconds=-STALL_MINUTES * 60)
    for block in blockers:
        if (not is_bot(block["owner"]) or block["status"] == "review" or block["updated"] >= quiet
                or (block["status"] == "waiting" and is_human(block.get("waiting_on")))
                or not TR.is_descendant(conn, row["id"], block["id"])
                or TR.live_blocker(conn, block["id"], ACTIVE_STATUSES) or unanswered_ask(conn, block)
                or due_later(block, at) or TS.snoozed(block, at) or block.get("resume_json")
                or milestone_pending(conn, block["id"]) or own_job(conn, block, claimable=True)):
            return []
    return [block["id"] for block in blockers]


def stuck_tasks(conn, hours=STUCK_HOURS, hidden=()):
    """Open work on an active bot that has not moved in `hours` and is waiting on nobody: no person
    owes an answer, no open task blocks it, it is not a quiet task, and no run for its bot is queued
    or going. One BotOps sweep finds these instead of a daily run per bot.

    Also, at any age and even `waiting`, a task held only by its own quiet subtasks (own_stuck_blockers):
    that wait never ends by itself. Those carry `own_blockers`, under the same busy and next-run rules."""
    at = now()
    cutoff = shift(at, seconds=-hours * 3600)
    rows = _rows(conn.execute(
        "SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug WHERE b.state='active' "
        "AND (t.status IN ('open','doing','ready') AND coalesce(t.next_run,0)=0 AND t.updated<? "
        f"OR t.status IN ({','.join(repr(x) for x in ACTIVE_STATUSES)}) AND t.updated<? "
        "AND EXISTS (SELECT 1 FROM task_relations r WHERE r.to_task=t.id AND r.kind='blocks')) "
        "ORDER BY t.updated", (cutoff, shift(at, seconds=-STALL_MINUTES * 60))))
    try:
        busy = {r["bot"] for r in conn.execute(
            "SELECT DISTINCT bot FROM jobs WHERE state IN ('queued','leased','running')")}
    except sqlite3.OperationalError:     # a local hub has no jobs table
        busy = set()
    out = []
    for row in rows:
        slug = actor_id(row["owner"])
        if slug in hidden or slug in busy or row.get("next_run") or unanswered_ask(conn, row):
            continue
        if not_yet_due(row, at):     # parked until its due date, like the stall watcher
            continue
        own = own_stuck_blockers(conn, row, at)
        if not own and (row["status"] not in ("open", "doing", "ready") or row["updated"] >= cutoff
                        or TR.live_blocker(conn, row["id"], ACTIVE_STATUSES)):
            continue
        out.append({"id": row["id"], "short_id": row["id"][:8], "title": row["title"], "owner": row["owner"], "status": row["status"],
                    "requester": row["requester"], "updated": row["updated"], "note": row.get("note") or "",
                    **({"own_blockers": own} if own else {})})
    return out


# Tasks should not stay stuck for more than a few minutes. The scheduler looks every minute
# for a bot's open or doing task that nothing is going to move: no run for its bot queued or going, no routine of that bot due
# soon, no person owing an answer, no open task blocking it. It wakes the bot on the task, spaced
# and capped; a bot that means to wait says so with `waiting` and a reason, which this never
# touches. If waking does not move it, BotOps gets one task to find out why.
STALL_MINUTES = 5            # quiet this long with nothing set to move it: wake the bot
STALL_NEXT_RUN_MINUTES = 30  # a next-run task was filed not to wake the bot at once; give it longer
STALL_REPEAT_MINUTES = 30    # between two wakes for the same task; doubles while the task stands unchanged
STALL_BACKOFF_CAP_MINUTES = 480  # the longest wait between two wakes for a task that has not changed (token saving 4/9)
STALL_WAKES_PER_DAY = 3      # then BotOps takes it
# Spot checks, on the same path and the same cap: a task whose own session ran lately (or runs now) but
# that has made no progress (a checkpoint, a comment, a status change, a message from its bot, a turn
# finishing) this long gets one "Status check" into its session; that long again with still none and it
# is reported stuck in the bot's main chat, with a question to the person who asked for it.
SPOT_CHECK_MINUTES = 30
SPOT_STUCK_MINUTES = 30
SPOT_SESSION_HOURS = 24      # a session idle longer than this is not "recent": the stall wake covers it
STUCK_TRIAGE = "stuck"        # refs.triaged on the one wake Decide's "stuck" lets through (backend/stall_triage.py)
STATUS_CHECK = "Status check: checkpoint where you are, or mark it blocked or done."
STUCK_RETRY = ("Still no progress after the status check. Carry the task on now, checkpoint where you are, or mark it "
               "blocked or done; if it is still stuck, BotOps is asked to find why.")


def not_yet_due(row, at):
    """A task with a future due date, or snoozed until a later moment, says when to look again: the
    scheduler's "Due:" or "Snooze over" notice wakes its owner then. Legacy dates without an offset
    read as Pacific."""
    return TS.snoozed(row, at) or due_later(row, at)


def due_later(row, at):
    """Whether this task's due date is still ahead of `at`. An occurrence's due is its deadline, not a wake date: its
    bot works it through the lead time, so it is never parked until then (explicit snoozes still park)."""
    if row.get("occurrence"):
        return False
    due = parse_ts(row.get("due")) if row.get("due") else None
    if due and due.tzinfo is None:
        from zoneinfo import ZoneInfo
        due = due.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return bool(due and due > parse_ts(at))


def parked_bots(conn):
    """Bots still waiting for their first setup (backend/statuses.py). The runner gives such a bot only a
    person's messages (execution.py claim), so work from bots and Tico stays queued until setup is done."""
    if not _has_table(conn, "bot_config"):
        return set()
    from .statuses import PARKED_SQL
    return {r[0] for r in conn.execute(f"SELECT bot FROM bot_config WHERE onboarding_state IN {PARKED_SQL}")}


def setup_hold(conn, actor, sender=None):
    """Why work sent to `actor` does not start, when it is a bot still onboarding; else None. The sender,
    Run now and the stall check all give these words. Its manager's message (`sender`) starts it: they talk."""
    slug = actor_id(actor)
    if not is_bot(actor) or slug not in parked_bots(conn):
        return None
    if sender and is_bot(sender):
        row = _one(conn, "SELECT reports_to FROM bot_config WHERE bot=?", (slug,))
        if row and row["reports_to"] and sender == bot_actor(row["reports_to"]):
            return None
    name = (bot(conn, slug) or {}).get("display_name") or slug
    return (f"{name} is still onboarding, so it starts no work from bots or Tico yet, only chat with people and its "
            "manager: this waits until someone above it on the org chart marks onboarding complete.")


def stalled_tasks(conn, at=None, task_ids=None, queued_here=None):
    """Bot tasks nothing is going to move, with how long they have been quiet (minutes). Private ones too: their
    owner reads them, and a private parent whose subtasks finished would otherwise wait for nobody; their words go no
    further (no diagnostic for BotOps, _no_diagnosis; no Decide read, stall_triage.inputs). A task whose bot is
    still in setup carries `held`: waking it would only queue another run that never starts. A task held only
    by its own quiet subtasks (own_stuck_blockers) counts too, even `waiting`, and carries `own_blockers`."""
    at = at or now()
    rows = _rows(conn.execute(
        "SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug WHERE b.state='active' "
        "AND (t.status IN ('open','doing') OR t.status='waiting' AND EXISTS "
        "(SELECT 1 FROM task_relations r WHERE r.to_task=t.id AND r.kind='blocks')) "
        "AND t.updated<?" +
        (" AND t.id IN (" + ",".join("?" * len(task_ids)) + ")" if task_ids else "") + " ORDER BY t.updated",
        (shift(at, seconds=-STALL_MINUTES * 60), *(task_ids or ()))))
    if not rows:
        return []
    parked = parked_bots(conn)
    try:
        # A parked bot's queued job that is not a person's message never starts: it is not busy.
        busy = {r[0] for r in conn.execute(
            "SELECT j.bot, max(j.state!='queued' OR coalesce(m.from_actor,'') LIKE 'human:%') FROM jobs j "
            "LEFT JOIN messages m ON m.id=j.message_id WHERE j.state IN ('queued','leased','running') " +
            ("AND j.message_id NOT IN (SELECT value FROM json_each(?)) " if queued_here else "") +
            "GROUP BY j.bot", (json.dumps(queued_here),) if queued_here else ())
            if r[1] or r[0] not in parked}
    except sqlite3.OperationalError:     # a local hub has no jobs table
        busy = set()
    soon = shift(at, seconds=(STALL_REPEAT_MINUTES + 5) * 60)
    try:
        routine = {r[0] for r in conn.execute(
            "SELECT DISTINCT s.bot FROM schedules s LEFT JOIN schedule_config sc ON sc.schedule_id=s.id "
            "WHERE s.deleted_at IS NULL AND coalesce(sc.enabled,1)=1 AND s.next_due IS NOT NULL AND s.next_due<=?", (soon,))}
    except sqlite3.OperationalError:
        routine = set()
    out, base = [], parse_ts(at)
    for row in rows:
        slug = actor_id(row["owner"])
        if slug in busy or slug in routine or unanswered_ask(conn, row):
            continue
        # A task with a future due date says when to look again: the scheduler's "Due:" notice
        # wakes the bot then (a follow-up that waits for Google's crawl).
        if not_yet_due(row, at):
            continue
        if row.get("next_run") and row["updated"] > shift(at, seconds=-STALL_NEXT_RUN_MINUTES * 60):
            continue
        if row.get("next_run") and delivery.holds_task(conn, row["id"]):
            continue        # deferred: its deadline brings it (backend/delivery.py)
        if row.get("resume_json"):
            continue        # a run said when to pick it up (backend/quiet_work.py): that brings it back
        own = own_stuck_blockers(conn, row, at)
        if not own and (row["status"] == "waiting" or TR.live_blocker(conn, row["id"], ACTIVE_STATUSES)):
            continue
        quiet = int((base - parse_ts(row["updated"])).total_seconds() // 60)
        out.append({**row, "quiet_minutes": quiet, **({"held": "needs_setup"} if slug in parked else {}),
                    **({"own_blockers": own} if own else {})})
    return out


def _session_lanes(conn, row):
    """The lanes a bot's turns on `row` may have run in: its own, and its session root's (a runner that
    keys subtasks on their root). Also the root itself."""
    slug = actor_id(row["owner"])
    config = {}
    if _has_table(conn, "bot_config"):
        found = _one(conn, "SELECT config_json FROM bot_config WHERE bot=?", (slug,))
        config = _json(found["config_json"], {}) if found else {}
    from .shared_bots import follow   # the followed config is the one the runner keys sessions on
    root = session_root(conn, slug, row["id"], follow(conn, slug, config))
    return ["task:" + row["id"], "task:" + root], root


def spot_check_tasks(conn, at=None, task_ids=None):
    """Bot tasks whose own session ran in the last SPOT_SESSION_HOURS (or runs now) with no progress for
    SPOT_CHECK_MINUTES, each with when it last progressed (`progress`). A subtask whose turns run in its
    parent's session is checked through the parent, so a session is never steered twice for one stretch."""
    at = at or now()
    if not _has_table(conn, "attempts"):
        return []
    rows = _rows(conn.execute(
        "SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug WHERE b.state='active' "
        "AND t.status IN ('open','doing') AND coalesce(t.private,1)=0 AND t.updated<?" +
        (" AND t.id IN (" + ",".join("?" * len(task_ids)) + ")" if task_ids else "") + " ORDER BY t.updated",
        (shift(at, seconds=-SPOT_CHECK_MINUTES * 60), *(task_ids or ()))))
    out, recent, base = [], shift(at, hours=-SPOT_SESSION_HOURS), parse_ts(at)
    for row in rows:
        if not_yet_due(row, at) or row.get("next_run") or unanswered_ask(conn, row):
            continue
        if TR.live_blocker(conn, row["id"], ACTIVE_STATUSES):
            continue
        lanes, root = _session_lanes(conn, row)
        if root != row["id"]:
            continue
        slug = actor_id(row["owner"])
        session = conn.execute(
            "SELECT max(CASE WHEN state IN ('leased','running') THEN 1 ELSE 0 END), max(coalesce(finished,created)), "
            "max(finished) FROM attempts WHERE bot=? AND lane IN (?,?)", (slug, *lanes)).fetchone()
        if not session or session[1] is None or (not session[0] and session[1] < recent):
            continue
        said = conn.execute(f"SELECT max(m.created) FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                            f"WHERE m.conversation_id=? AND m.from_actor=? AND {MESSAGE_TASK_SQL}=?",
                            (row["conversation_id"], row["owner"], row["id"])).fetchone()[0]
        progress = max(x for x in (row["updated"], row.get("checkpoint_at"), said, session[2]) if x)
        if progress > shift(at, seconds=-SPOT_CHECK_MINUTES * 60):
            continue
        out.append({**row, "progress": progress,
                    "quiet_minutes": int((base - parse_ts(progress)).total_seconds() // 60)})
    return out


def _report_stuck(conn, row, count=1):
    """A spot-checked task that did not move after its status check and one retry: one "stuck" line in the bot's
    main chat, and a task for BotOps to find why (_escalate_stuck). A stuck check never goes to a person: they see it
    only when BotOps asks them a real question."""
    text = (row.get("checkpoint") if not row.get("checkpoint_blocked") else "") or row.get("note") or ""
    task_report(conn, row, "stuck", f"No progress for {row['quiet_minutes']} min. {text}".strip())
    repair = _escalate_stuck(conn, row, count)
    event(conn, KEEPER, "task.stuck", row["id"], {"bot": actor_id(row["owner"]), "quiet_minutes": row["quiet_minutes"],
                                                 "recovery_task": (repair or {}).get("id")})


def _escalate_stuck(conn, row, count):
    """File (or reuse) BotOps's "Find why" task for a stuck task; returns it. When BotOps cannot take one, the
    stuck task carries the cause instead (_settle_undiagnosed) and this returns None."""
    cause = _no_diagnosis(conn, row, count)
    if cause:
        _settle_undiagnosed(conn, row, cause)
        return None
    owner = bot_actor(FLEET_MAINTAINER)
    # Identity comes from the source event, not the human-facing title: even
    # equally named tasks have separate diagnoses. Reuse unresolved work later.
    previous = _one(conn, "SELECT detail_json FROM events WHERE action='task.stall_escalated' "
                    "AND target=? ORDER BY rowid DESC LIMIT 1", (row["id"],))
    repair = task(conn, _json(previous["detail_json"], {}).get("recovery_task")) if previous else None
    if repair and (repair["owner"] != owner or repair["status"] not in ACTIVE_STATUSES):
        repair = None
    title = f"Find why {actor_id(row['owner'])}'s task stays stuck: {row['title']}"
    repair = repair or task_create(conn, KEEPER, title[:150],
                f"Task {row['id']} ({row['title'][:120]}) has been {row['status']} with nothing moving it; "
                f"{count} wake-ups today did not move it. Fix the cause (routine, runner, instructions) "
                "or file a subtask for whoever must act and set it waiting. Complete this diagnostic when the cause is known; "
                "record any remaining repair dependency on the original task.", owner,
                deduplicate=False, lint=False)
    event(conn, KEEPER, "task.stall_escalated", row["id"],
          {"bot": actor_id(row["owner"]), "wakes": count, "recovery_task": repair["id"], "owner": owner})
    return repair


def _setup_person(conn, slug):
    """The person who finishes a bot's setup: who made it, else its operator, else the default person."""
    row = _one(conn, "SELECT * FROM bot_config WHERE bot=?", (slug,)) if _has_table(conn, "bot_config") else None
    made = str((row or {}).get("created_by") or "")
    for who in (made if is_human(made) else "", human_actor((row or {}).get("operator") or ""),
                human_actor(default_human(conn))):
        if actor_id(who) and _actor_exists(conn, who):
            return who
    return None


def _open_setup_reminder(conn, slug):
    previous = _one(conn, "SELECT detail_json FROM events WHERE action='bot.setup_reminder' AND target=? "
                          "ORDER BY rowid DESC LIMIT 1", (slug,))
    found = task(conn, _json(previous["detail_json"], {}).get("task")) if previous else None
    return found if found and found["status"] in ACTIVE_STATUSES else None


def setup_finished(conn, slug, archived=False):
    """A bot's setup is done (onboarding.onboarded), or the bot is archived: its reminder is done too."""
    found = _open_setup_reminder(conn, slug)
    if found:
        name = (bot(conn, slug) or {}).get("display_name") or slug
        task_update(conn, KEEPER, found["id"], status="done",
                    note=f"{name} was archived." if archived else f"{name} finished onboarding; its work starts now.")


def _setup_reminder(conn, slug):
    """One "Finish setting up <bot>" task per parked bot in a person's Needs you, reused while it is open."""
    found = _open_setup_reminder(conn, slug)
    if found:
        return found
    person = _setup_person(conn, slug)
    if not person:
        return None
    name = (bot(conn, slug) or {}).get("display_name") or slug
    held = _rows(conn.execute("SELECT * FROM tasks WHERE owner=? AND status IN ('open','doing') ORDER BY created",
                              (bot_actor(slug),)))
    # The person is shown only the tasks they may read; the rest are a count.
    seen = [t for t in held if task_private_readable(conn, person, t)]
    lines = "\n".join(f"- {t['title'][:120]} ({t['id']})" for t in seen[:10])
    if len(held) > len(seen[:10]):
        unseen = len(held) - len(seen)
        lines += (f"\n- and {len(held) - len(seen[:10])} more"
                  + (f" ({unseen} you can't see)" if unseen else ""))
    reminder = task_create(conn, KEEPER, f"Finish onboarding {name}: work sent to it is not starting"[:150],
                           f"{name} is still onboarding, so tasks and messages from bots and Tico stay queued and never "
                           f"start. Talk with it in its chat and, when it is ready, mark its onboarding complete (hub bot "
                           f"setup-done {slug}), or move its tasks to another bot.\n\nWaiting on it:\n{lines}", person,
                           deduplicate=False, lint=False)
    event(conn, KEEPER, "bot.setup_reminder", slug, {"task": reminder["id"], "owner": person})
    return reminder


def _hold_for_setup(conn, row):
    """A stalled task whose bot is still in setup: waking the bot would only queue another run that never
    starts. Once per task, say why on the task, tell a bot that asked for it, and put the bot's setup in a
    person's Needs you. Returns whether it did."""
    if conn.execute("SELECT 1 FROM events WHERE action='task.setup_held' AND target=? LIMIT 1", (row["id"],)).fetchone():
        return False
    why = setup_hold(conn, row["owner"])
    reminder = _setup_reminder(conn, actor_id(row["owner"]))
    if not reminder:        # nobody to finish its setup yet: try again on a later sweep
        return False
    task_comment(conn, KEEPER, row["id"], f"Not started: {why}", wake=False)
    asker = row["requester"]
    if is_bot(asker) and asker != row["owner"] and actor_id(asker) not in parked_bots(conn):
        _wake(conn, row, asker, f"Your task for {actor_id(row['owner'])} is not starting: {why} "
                                "Move it to another bot if it can't wait.", {"setup_held": True}, trigger="stall_check")
    event(conn, KEEPER, "task.setup_held", row["id"], {"bot": actor_id(row["owner"]), "reminder": reminder["id"]})
    return True


def _is_diagnostic(conn, row):
    """A task the hub filed to find why other work is stuck or keeps stopping."""
    if row["requester"] != KEEPER or not str(row["title"]).startswith("Find why "):
        return False
    return bool(conn.execute("SELECT 1 FROM events WHERE action='task.stall_escalated' "
                             "AND json_extract(detail_json,'$.recovery_task')=? LIMIT 1", (row["id"],)).fetchone()
                or "'s runs keep stopping" in row["title"] or "'s task stays stuck: " in row["title"])


def _no_diagnosis(conn, row, count):
    """Why a stuck task gets no diagnostic, or None when BotOps takes one. A diagnostic about a diagnostic
    finds nothing new, and a person cannot repair a bot's runs: neither is filed."""
    if _is_diagnostic(conn, row):
        return f"this diagnostic did not move after {count} wake-ups today"
    if task_private(conn, row):
        return (f"{actor_id(row['owner'])} did not move it after {count} wake-ups today, "
                "and BotOps cannot read a private task")
    repair_bot = bot(conn, FLEET_MAINTAINER)
    if row["owner"] == bot_actor(FLEET_MAINTAINER):
        return f"BotOps's own task did not move after {count} wake-ups today, and BotOps cannot diagnose itself"
    if not repair_bot or repair_bot["state"] != "active":
        state = repair_bot["state"] if repair_bot else "not on this team"
        return f"{actor_id(row['owner'])} did not move it after {count} wake-ups today, and BotOps is {state}"
    return None


# BotOps's task to review a bot's stopped runs held for a check (execution._hand_to_maintainer).
HELD_REVIEW_TITLE = "Review {bot}'s stopped runs held for a check"
HELD_OPERATOR_REVIEW_TITLE = "Review {display_name}'s unresolved interrupted runs"
# The operator review's title carries the bot's display name, which is neither unique nor fixed, so its body names the
# bot's slug too and the stall guard reads that first.
HELD_OPERATOR_REVIEW_BOT = "Bot: {slug}."


def held_review_bot(conn, row):
    """The bot a keeper-filed held-run review task is for while one of its runs is still held with no saved decision,
    else None. This includes BotOps reviews and the operator review used when BotOps cannot read the run."""
    prefix, suffix = HELD_REVIEW_TITLE.split("{bot}")
    title = str(row["title"] or "")
    if row["requester"] != KEEPER or not _has_table(conn, "jobs"):
        return None
    if title.startswith(prefix) and title.endswith(suffix) and len(title) > len(prefix) + len(suffix):
        slug = title[len(prefix):-len(suffix)]
    else:
        owner_prefix, owner_suffix = "Review ", "'s unresolved interrupted runs"
        if not title.startswith(owner_prefix) or not title.endswith(owner_suffix):
            return None
        body = conn.execute("SELECT body FROM tasks WHERE id=?", (row["id"],)).fetchone()
        named = re.search(r"^Bot: (\S+)\.$", str(body["body"] or "") if body else "", re.M)
        if named:
            slug = named.group(1)
        else:
            display_name = title[len(owner_prefix):-len(owner_suffix)]
            bot = conn.execute("SELECT slug FROM bots WHERE display_name=? LIMIT 1", (display_name,)).fetchone()
            slug = bot["slug"] if bot else None
        if not slug:
            return None
    held = conn.execute("SELECT 1 FROM jobs j WHERE j.bot=? AND j.state='uncertain' AND NOT EXISTS("
                        "SELECT 1 FROM job_recovery r WHERE r.job_id=j.id AND r.attempt_id=j.attempt_id) LIMIT 1",
                        (slug,)).fetchone()
    return slug if held else None


def _settle_undiagnosed(conn, row, cause):
    """Work the hub filed for itself closes with the cause; anyone else's task stays open and its requester is
    told the cause, at most once a day while it stays stuck. A review of held runs stays open while a run it is for is
    still held with no decision (held_review_bot)."""
    if row["requester"] == KEEPER and held_review_bot(conn, row):
        event(conn, KEEPER, "task.stall_escalated", row["id"],
              {"bot": actor_id(row["owner"]), "recovery_task": None, "owner": None, "cause": cause,
               "kept_open": "runs still held"})
        return
    if row["requester"] == KEEPER:
        task_close(conn, KEEPER, row["id"], f"Closed by the stall check: {cause}.", quiet=True)
    elif _actor_exists(conn, row["requester"]):
        _wake(conn, row, row["requester"], f"Stuck: {row['title']}. {cause[0].upper()}{cause[1:]}. "
              "Reassign it, run it, or close it.", {"wake": "stuck_cause"}, trigger="stall_check")
    event(conn, KEEPER, "task.stall_escalated", row["id"],
          {"bot": actor_id(row["owner"]), "recovery_task": None, "owner": None, "cause": cause})


def _stall_stand(conn, row):
    """What a stall check can tell moved: the task's status, step, owner, what it waits on, its relations and their
    states, and every comment from anyone but its bot. The bot's own notes ("still waiting") are not a change."""
    tid = row["id"]
    parts = [row["status"], row.get("step_id"), row["owner"], row.get("waiting_on"),
             [list(r) for r in conn.execute(
                 "SELECT r.kind, r.from_task, r.to_task, o.status FROM task_relations r JOIN tasks o "
                 "ON o.id=CASE WHEN r.from_task=? THEN r.to_task ELSE r.from_task END "
                 "WHERE ? IN (r.from_task, r.to_task) ORDER BY r.kind, r.from_task, r.to_task", (tid, tid))],
             [list(r) for r in conn.execute(
                 "SELECT id, edited_at, deleted_at FROM messages WHERE conversation_id=? AND from_actor NOT IN (?,?) "
                 "ORDER BY rowid", (row["conversation_id"], row["owner"], KEEPER))]]
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def _stall_streak(conn, row, stand, quiet=False):
    """How many of the task's latest stall wakes in a row it stood unchanged through. With `quiet`, notices a wake rule
    held (task.stall_quiet) count too: they space the next notice, and never count as a wake that was tried."""
    actions = "('task.stall_wake','task.stall_quiet')" if quiet else "('task.stall_wake')"
    streak = 0
    for (detail,) in conn.execute(f"SELECT detail_json FROM events WHERE action IN {actions} AND target=? "
                                  "ORDER BY ts DESC LIMIT 6", (row["id"],)):
        if _json(detail, {}).get("stand") != stand:
            break
        streak += 1
    return streak


def _stall_gap(conn, row, stand, quiet=False):
    """Minutes to leave between two stall wakes. STALL_REPEAT_MINUTES after a wake that the task has changed since;
    doubled for each earlier wake in a row the task stood unchanged through (30, 60, 120, then the cap)."""
    streak = _stall_streak(conn, row, stand, quiet)
    return min(STALL_REPEAT_MINUTES * 2 ** max(streak - 1, 0), STALL_BACKOFF_CAP_MINUTES)


def stall_skip(conn, row, at=None):
    """Why a stall or status check leaves this task alone, or None when it may be checked: the owner has a live
    session on it (a leased or running attempt in its lane), it waits on something recorded (a blocker, a subtask,
    an approval, an ask, a due date, a snooze, a stored resume, a pull request armed with wake_once or a milestone),
    or the owner's profile has no stall sweeps (rules.sweeps_on). A task held only by its own quiet subtasks
    (`own_blockers`) keeps being checked: that deadlock is what the check is for. Task Manager reports read
    `stalled_tasks`, not this, so a task no sweep wakes still reaches a person's report."""
    from . import rules
    at = at or now()
    slug = actor_id(row["owner"])
    if not rules.sweeps_on(conn, slug):
        return "sweeps_off"
    if _has_table(conn, "attempts"):
        lanes, _ = _session_lanes(conn, row)
        if conn.execute("SELECT 1 FROM attempts WHERE bot=? AND lane IN (?,?) AND state IN ('leased','running') "
                        "LIMIT 1", (slug, *lanes)).fetchone():
            return "live_session"
    return recorded_wait(conn, row, at)


def recorded_wait(conn, row, at=None):
    """What a quiet task waits on, as recorded, or None: an ask, a snooze, a due date, a stored resume, an approval, or
    (unless only its own quiet subtasks hold it) a blocker, a pull request or milestone wait, or an open subtask. The
    same whatever the owner's profile says about sweeps, so a list of stuck work reads a reviewer's waits too."""
    from . import wake_rules
    at = at or now()
    if unanswered_ask(conn, row):
        return "ask"
    if TS.snoozed(row, at):
        return "snooze"
    if due_later(row, at):
        return "due"
    if row.get("resume_json"):
        return "resume"
    if conn.execute("SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL AND consumed_at IS NULL LIMIT 1",
                    (row["id"],)).fetchone():
        return "approval"
    if not row.get("own_blockers") and not own_stuck_blockers(conn, row, at):
        parked = wake_rules.parked(conn, row)
        if parked:
            return parked
        if open_children(conn, row["id"]):
            return "subtask"
    return None


def wake_stalled(conn, at=None, task_ids=None, queued_here=None):
    """Wake the owner of each stalled task (at most once per STALL_REPEAT_MINUTES, doubling to
    STALL_BACKOFF_CAP_MINUTES while the task has not changed; STALL_WAKES_PER_DAY a day); past that, one diagnostic per stuck task, for BotOps. No diagnostic is filed about another
    diagnostic or sent to a person: when BotOps cannot take one, the stuck task carries the cause instead
    (_settle_undiagnosed). A bot still in setup
    is not woken: its task says why once and its setup goes to a person (_hold_for_setup).

    Spot checks share the path, its events and its cap: a task with a recent session and no progress
    (spot_check_tasks) that is not stalled gets one status check per quiet stretch; a status check or a
    stall wake with still no progress SPOT_STUCK_MINUTES later retries its bot once, and SPOT_STUCK_MINUTES after
    that reports it stuck to BotOps (_report_stuck), never to a person. Returns what it did."""
    from . import stall_triage, wake_rules
    at = at or now()
    from .task_blocked import sweep as blocked_sweep
    blocked_sweep(conn, at, task_ids=task_ids)
    woke, escalated, checked, stuck, held, retried_ids = [], [], [], [], [], []
    stalled = {row["id"]: row for row in stalled_tasks(conn, at, task_ids=task_ids, queued_here=queued_here)}
    spots = {row["id"]: row for row in spot_check_tasks(conn, at, task_ids=task_ids)}
    parked = parked_bots(conn)
    for row in [*stalled.values(), *[r for r in spots.values() if r["id"] not in stalled]]:
        with isolated(conn, "wake_stalled", row["id"]):
            if stall_skip(conn, row, at):
                continue
            if actor_id(row["owner"]) in parked:
                if row["id"] in stalled and _hold_for_setup(conn, row):
                    held.append(row["id"])
                continue
            spot, nudged = spots.get(row["id"]), None
            if spot:
                nudged = conn.execute("SELECT min(ts) FROM events WHERE action='task.stall_wake' AND target=? AND ts>?",
                                      (row["id"], spot["progress"])).fetchone()[0]
                if nudged and nudged <= shift(at, seconds=-SPOT_STUCK_MINUTES * 60) and not conn.execute(
                        "SELECT 1 FROM events WHERE action='task.stuck' AND target=? AND ts>?",
                        (row["id"], spot["progress"])).fetchone():
                    # Still nothing after the status check: its bot is retried once, then BotOps finds why.
                    retried = conn.execute("SELECT min(ts) FROM events WHERE action='task.stuck_retry' AND target=? "
                                           "AND ts>?", (row["id"], spot["progress"])).fetchone()[0]
                    if not retried:
                        msg = _wake(conn, row, row["owner"], STUCK_RETRY, {"wake": "stuck_retry"}, trigger="stall_check")
                        if queued_here is not None and msg:
                            queued_here.append(msg["id"])
                        event(conn, KEEPER, "task.stuck_retry", row["id"],
                              {"bot": actor_id(row["owner"]), "quiet_minutes": spot["quiet_minutes"]})
                        retried_ids.append(row["id"])
                        continue
                    if retried <= shift(at, seconds=-SPOT_STUCK_MINUTES * 60):
                        woken = conn.execute("SELECT count(*) FROM events WHERE action='task.stall_wake' AND target=? "
                                             "AND ts>?", (row["id"], shift(at, seconds=-86400))).fetchone()[0] or 0
                        _report_stuck(conn, spot, woken + 1)    # the stall wakes today and the retry
                        stuck.append(row["id"])
                    # Between the retry and the report the retry is its wake: no stall wake on top of it.
                    continue
            # A status note must not buy another round of retries. Cap automatic wakes
            # over the whole day; busy work is excluded above and explicit runs remain available.
            since = shift(at, seconds=-86400)
            count = conn.execute("SELECT count(*) FROM events WHERE action='task.stall_wake' AND target=? AND ts>?",
                                 (row["id"], since)).fetchone()[0] or 0
            last = conn.execute("SELECT max(ts) FROM events WHERE action='task.stall_wake' AND target=?",
                                (row["id"],)).fetchone()[0]
            stand = _stall_stand(conn, row)
            streak = _stall_streak(conn, row, stand)
            # Tico Decide read the task at this stand and called it stuck (backend/stall_triage.py): this one wake is
            # let through the same_state hold, at the place and under the gap and cap below.
            triaged = ({"triaged": STUCK_TRIAGE} if _has_table(conn, "stall_triage")
                       and stall_triage.go_ahead(conn, row, stand) else {})
            if last and last > shift(at, seconds=-STALL_REPEAT_MINUTES * 60):
                continue
            # Escalation reads the same count as before, or the same number of checks in a row that moved nothing,
            # which the back-off spreads over more than a day.
            if count >= STALL_WAKES_PER_DAY or streak >= STALL_WAKES_PER_DAY:
                if not conn.execute("SELECT 1 FROM events WHERE action='task.stall_escalated' AND target=? AND ts>?",
                                    (row["id"], shift(at, seconds=-86400))).fetchone():
                    _escalate_stuck(conn, row, max(count, streak))
                    escalated.append(row["id"])
                    continue
                if count >= STALL_WAKES_PER_DAY:
                    continue
            # A wake a rule held leaves no stall wake, so its own event spaces the next one, or every pass would write
            # another notice. Decide's go-ahead is not such a notice: it keeps to the gap of the wakes that went out.
            quiet = None if triaged else conn.execute("SELECT max(ts) FROM events WHERE action='task.stall_quiet' "
                                                      "AND target=?", (row["id"],)).fetchone()[0]
            if max(last or "", quiet or "") > shift(at, seconds=-_stall_gap(conn, row, stand, not triaged) * 60):
                continue
            if row["id"] not in stalled:
                # A spot check: one status check into the task's session per quiet stretch.
                if nudged:
                    continue
                msg = _wake(conn, row, row["owner"], STATUS_CHECK, {"wake": "status_check", **triaged},
                            trigger="stall_check")
                if queued_here is not None and msg:
                    queued_here.append(msg["id"])
                if msg and msg["refs"].get(wake_rules.MARK) in wake_rules.SILENT:
                    # No attempted wake for the retry/escalation machinery to count.
                    event(conn, KEEPER, "task.stall_quiet", row["id"], {"bot": actor_id(row["owner"]), "stand": stand,
                                                                        "kind": "status_check"})
                    continue
                if triaged:
                    stall_triage.sent(conn, row, at)
                event(conn, KEEPER, "task.stall_wake", row["id"],
                      {"bot": actor_id(row["owner"]), "quiet_minutes": row["quiet_minutes"], "wake": count + 1,
                       "kind": "status_check", "stand": stand})
                checked.append(row["id"])
                continue
            if row.get("own_blockers"):
                own = ", ".join(row["own_blockers"])
                text = (f"Stalled {row['quiet_minutes']} min: {row['title']}. It is blocked only by its own subtask "
                        f"{own}, which has gone quiet and waits on nothing else. If that subtask waits for this task "
                        "(a hold or an acknowledgement kept open until this one finishes), neither can move: read its "
                        f"comments, then take the block off (hub task relate {row['id']} {row['own_blockers'][0]} "
                        "--kind blocked_by --remove) and carry this on.")
            else:
                text = (f"Stalled {row['quiet_minutes']} min: {row['title']}. Nothing is set to move it. Carry it on now, "
                        f"or, when someone else must act, file a subtask for them (hub task create --parent {row['id']} "
                        "--owner <bot or person>) and set this waiting; finishing the subtask wakes it.")
            msg = _wake(conn, row, row["owner"], text, {"wake": "stalled", **triaged}, trigger="stall_check")
            if queued_here is not None and msg:
                queued_here.append(msg["id"])
            if msg and msg["refs"].get(wake_rules.MARK) in wake_rules.SILENT:
                event(conn, KEEPER, "task.stall_quiet", row["id"], {"bot": actor_id(row["owner"]), "stand": stand})
                continue
            if triaged:
                stall_triage.sent(conn, row, at)
            if row.get("next_run"):
                conn.execute("UPDATE tasks SET next_run=0 WHERE id=?", (row["id"],))
            event(conn, KEEPER, "task.stall_wake", row["id"],
                  {"bot": actor_id(row["owner"]), "quiet_minutes": row["quiet_minutes"], "wake": count + 1,
                   "stand": stand})
            woke.append(row["id"])
    return {"woke": woke, "escalated": escalated, "checked": checked, "stuck": stuck, "held": held,
            "retried": retried_ids}


@private_task_write
def task_run_now(conn, actor, task_id):
    """Start a bot's task now: a quiet task stops waiting for the next run, and any task gets a
    run of its own, as the task it is ("Run now" used to send a chat message
    in the person's name, so the run read as a chat and hid the task's own text). Returns (task, queued):
    queued is False when a run for this task was already waiting, so none is added."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    if not is_bot(row["owner"]):
        refuse(conn, actor, "run-now", "only a bot's task runs; a person does it themselves")
    if row["status"] not in ACTIVE_STATUSES:
        refuse(conn, actor, "run-now", f"the task is {row['status']}; {task_reopen_hint(actor, row)}")
    if why := setup_hold(conn, row["owner"]):
        raise Refused("run-now", why)       # not a refused write: nothing to count against the caller
    if row.get("next_run"):
        conn.execute("UPDATE tasks SET next_run=0, resume_json=NULL, updated=? WHERE id=?", (now(), task_id))
        _task_event(conn, task_id, actor, "next_run", "1", "0", "run now")
    waiting = None
    try:
        waiting = _one(conn,
            "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.bot=? AND j.state='queued' AND {unheld_sql()} AND {MESSAGE_TASK_SQL}=? LIMIT 1",
            (actor_id(row["owner"]), task_id))
    except sqlite3.OperationalError:     # a local hub has no jobs table
        waiting = None
    after = task(conn, task_id)
    if not waiting:
        _wake(conn, after, after["owner"], f"Run now: {after['title']}", {"run_now": True},
              trigger="person_message" if is_human(actor) else "other")
    event(conn, actor, "task.run-now", task_id, {"queued": not waiting})
    return after, not waiting


# ----------------------------------------------------------------------------- quiet notes
# A quiet note is the next-run task's lighter twin: something for a bot to know, not to do. It
# starts nothing; the next run the bot has for any reason carries every note waiting for it, in
# the same prompt, each with the time it was sent. For example, monitor bots leave a
# manager bot a note after each check, and its daily report reads them.
NOTE_MAX = 8000
NOTE_WAITING_SQL = (
    "n.cancelled_at IS NULL AND n.settled_at IS NULL AND (n.carried_by IS NULL OR NOT EXISTS("
    "SELECT 1 FROM attempts a WHERE a.id=n.carried_by AND a.state IN ('leased','running','completed')))")


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
    row = {"id": new_id(), "from_actor": actor, "to_actor": target, "body": body, "created": now()}
    conn.execute("INSERT INTO notes(id,from_actor,to_actor,body,created) VALUES "
                 "(:id,:from_actor,:to_actor,:body,:created)", row)
    event(conn, actor, "note.create", row["id"], {"to": target})
    return note(conn, row["id"])


def note(conn, note_id):
    return _one(conn, "SELECT * FROM notes WHERE id=?", (note_id,))


def note_waiting(conn, row):
    """Whether this note (a row) is still waiting for its bot's next run."""
    if not row or row.get("cancelled_at") or row.get("settled_at"):
        return False
    if not row.get("carried_by"):
        return True
    try:
        return not _one(conn, NEXT_RUN_CARRIER_SQL, (row["carried_by"],))
    except sqlite3.OperationalError:     # a local hub has no attempts table and no runs
        return True


def note_cancel(conn, actor, note_id, human=False):
    """Take a note back before a run carries it: its sender, or a person."""
    _writer(conn, actor)
    row = note(conn, note_id)
    if not row:
        refuse(conn, actor, "not-found", f"no note {note_id}")
    if actor != row["from_actor"] and not human and not is_human(actor):
        refuse(conn, actor, "note", f"{actor_id(row['from_actor'])} left this note; only they or a person take it back")
    if row["cancelled_at"]:
        return row
    if not note_waiting(conn, row):
        refuse(conn, actor, "note", "a run already carried this note; it cannot be taken back")
    conn.execute("UPDATE notes SET cancelled_at=?, cancelled_by=? WHERE id=?", (now(), actor, note_id))
    event(conn, actor, "note.cancel", note_id, {})
    return note(conn, note_id)


def notes_waiting(conn, bot, limit=50):
    """The notes waiting for `bot` (a slug), oldest first after Tico's own: the one a lifted quarantine leaves
    (quarantine_lifted_note) must reach the next run even behind a full backlog. A note that stands for a held
    message (`message_id`) is the message, in the order messages arrived, whoever sent it, the keeper too."""
    return _rows(conn.execute(
        f"SELECT n.* FROM notes n WHERE n.to_actor=? AND {NOTE_WAITING_SQL} "
        f"ORDER BY NOT (n.from_actor=? AND n.message_id IS NULL),n.created,n.id LIMIT ?", ("bot:" + bot, KEEPER, limit)))


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
        (stamp, -1 if limit is None else limit)).rowcount


def notes_waiting_counts(conn, bots, limit=500):
    """`len(notes_waiting(conn, bot, limit))` for many bots in one query per 500: {slug: count}."""
    out = dict.fromkeys(bots, 0)
    for part in chunks(out):
        for row in conn.execute(f"SELECT n.to_actor,count(*) FROM notes n WHERE n.to_actor IN "
                                f"({','.join('?' * len(part))}) AND {NOTE_WAITING_SQL} GROUP BY n.to_actor",
                                ["bot:" + bot for bot in part]):
            out[actor_id(row[0])] = min(row[1], limit)
    return out


def _wake(conn, task_row, target, body, refs=None, quiet_bots=False, quiet=False, trigger="status_or_relation",
          source=None, human_completion=False, completion_actionable=False):
    """Tell someone their task moved. Task traffic never counts against rule 4.

    `trigger` is what this wake is, for the run it starts (backend/run_triggers.py): a task's status or
    relation moved unless the caller says otherwise; `source` names the event behind it where there is one.

    `quiet_bots` is for news rather than work: a task finished, declined or closed. A person is
    always told properly, because a message to a person starts nothing. A bot that asked for
    `tasks` gets it quietly — written into its room, no run — and reads it in the conversation
    history the next time something real wakes it. Waking a bot to tell it that something it
    asked for is done costs a whole run to learn a fact it can act on later or not at all.

    Any other bot is told exactly as before: this is opt-in, and a fleet using the handoff flow
    in `policies/handoffs.md` still gets the wake that flow waits on.

    `quiet` is stronger: the caller knows this notice asks nothing of any bot, so no bot is
    woken for it, whatever it opted into.
    """
    actionable_completion = human_completion and (completion_actionable or '?' in str(body or '') or _awaited_by(conn, task_row, target)
                             or is_human(task_row.get('owner')) and task_row.get('requester') == target)
    if (human_completion and is_bot(target) and _actor_exists(conn, target)
            and task_private_readable(conn, target, task_row)
            and (not actionable_completion or (bot(conn, actor_id(target)) or {}).get('state') != 'active')):
        from . import completion_notes
        completion_notes.record(conn, target, task_row['id'])
        refs = {**(refs or {}), 'quiet': True}
    if quiet_bots and (quiet or tasks_only(conn, target)):
        refs = {**(refs or {}), "quiet": True}
    if (is_bot(target) and target == task_row.get("owner") and TS.snoozed(task_row)
            and not (refs or {}).get("run_now") and (refs or {}).get("wake") != "snooze"):
        # Nobody plans to get to a snoozed task before its date: news about it waits for its bot's next run.
        # Anyone else (a requester bot handed it for review) hears as usual.
        refs = {**(refs or {}), "quiet": True}
    if not target or not _actor_exists(conn, target) or not task_private_readable(conn, target, task_row):
        return None
    if is_bot(target) and target == task_row.get("owner") and not (refs or {}).get("quiet"):
        # A reconnect wake still owed on this task rides on this one (backend/reconnects.py): one wake, not two.
        from .reconnects import fold_into as reconnects_fold
        body, refs = reconnects_fold(conn, task_row, body, refs)
    message_refs = RT.stamp({"task": task_row["id"], **(refs or {})}, trigger, source)
    return _write_message(conn, KEEPER, target, body,
                          conversation(conn, task_row["conversation_id"]), "notice",
                          message_refs, None, None)


# ----------------------------------------------------------------------------- approvals
APPROVAL_STALE_DAYS = 14       # an undecided approval older than this is withdrawn (approval_expiry.sweep)
TASK_ENDED = {"done": "marked done", "closed": "closed", "declined": "declined"}
# What an approval acts on. A newer request on the same task for the same target replaces the older one; a bot may
# rightly have several open on one task (three posts to publish), so a different target is a separate decision.
APPROVAL_TARGET = {"publish": ("url",), "send": ("to", "subject"), "merge": ("repo", "pr"), "spend": ("account", "what")}


def approval_target(kind, payload):
    keys = APPROVAL_TARGET.get(kind)
    values = [(payload or {}).get(k) for k in keys or ()]
    if kind == "send" and values and values[0]:
        # "a@x" and ["A@x"] are the same recipients.
        to = values[0] if isinstance(values[0], list) else [values[0]]
        values[0] = sorted(str(v).strip().lower() for v in to)
    return json.dumps(values, sort_keys=True, default=str) if keys and all(v not in (None, "", []) for v in values) \
        else None


def approval_withdraw(conn, approval_id, note):
    """An undecided approval nobody needs any more leaves the person's queue as `withdrawn`: its task ended, a newer
    request replaced it, or nobody decided it in APPROVAL_STALE_DAYS. Only the keeper withdraws, so a withdrawal is
    never mistaken for a person's no; the bot that asked hears once, quietly, and may ask again."""
    row = approval(conn, approval_id)
    if not row or row["decision"]:
        return None
    if not conn.execute("UPDATE approvals SET decision='withdrawn', decided_by=?, decided_at=? "
                        "WHERE id=? AND decision IS NULL", (KEEPER, now(), approval_id)).rowcount:
        return None
    event(conn, KEEPER, "approval.withdraw", approval_id, {"decision": "withdrawn", "note": note})
    if row.get("task_id"):
        from . import task_holds
        task_holds.refresh(conn, row["task_id"], f"Your {row['kind']} approval was withdrawn")
    conv = conversation(conn, (message(conn, row["message_id"]) or {}).get("conversation_id")) \
        if row.get("message_id") else None
    if conv and _actor_exists(conn, row["requested_by"]):
        _write_message(conn, KEEPER, row["requested_by"], f"Withdrawn: your {row['kind']} approval. {note}",
                       conv, "notice", {"approval": approval_id, "quiet": True}, None, None)
    _recount(conn, row["requested_by"])
    hook(conn, "approval.withdrawn", approval_id, lambda: _approval_facts(row), KEEPER, revision=0)
    return approval(conn, approval_id)


def withdraw_task_approvals(conn, task_id, note):
    """Withdraw every undecided approval on a task that ended."""
    return [aid for (aid,) in conn.execute("SELECT id FROM approvals WHERE task_id=? AND decision IS NULL "
                                           "ORDER BY created", (task_id,)).fetchall()
            if approval_withdraw(conn, aid, note)]


def _approval_facts(row):
    """What an outgoing webhook says of an approval: who asked, what kind, which task. Never the payload."""
    return {"id": row["id"], "kind": row["kind"], "task_id": row.get("task_id"), "requested_by": row["requested_by"],
            "decision_owner": row.get("decision_owner")}


def approval_request(conn, actor, kind, payload, task_id=None, supersedes=None, to=None):
    """Rule 6. One exact payload, one open request per payload, a human decides, spent once.

    A request replaces the asker's older undecided one it names (`supersedes`), and any it has open on the same task
    for the same target (APPROVAL_TARGET): a re-cut release or a redrafted email is the same decision, asked again."""
    _writer(conn, actor)
    if kind not in APPROVAL_KINDS:
        refuse(conn, actor, "kind", f"an approval is {'|'.join(APPROVAL_KINDS)}, not {kind}")
    text = json.dumps(payload, sort_keys=True, default=str)
    severity = classify(text, kind=kind, actor=actor, conn=conn)
    path_mentioned(conn, actor, f"{kind} approval", text, task_id)
    problems = lint_approval(kind, payload)
    if problems:
        refuse(conn, actor, "lint", "; ".join(problems), severity)
    if kind == "publish" and "text" in payload and not publish_text_matches(payload):
        # The person reads `text`, the publisher checks `content_sha256`: they must be the same reply.
        if not isinstance(payload["text"], str):
            refuse(conn, actor, "lint", "text must be a string", severity)
        refuse(conn, actor, "lint", "the text doesn't match content_sha256; send the exact text that will be published",
               severity)
    digest = payload_hash(payload)
    dup = _one(conn, "SELECT id FROM approvals WHERE payload_hash=? AND decision IS NULL", (digest,))
    if dup:
        refuse(conn, actor, "duplicate", f"{dup['id']} already asks for exactly this")
    if supersedes:
        named = approval(conn, supersedes)
        if not named or named["requested_by"] != actor:
            refuse(conn, actor, "not-found", f"you have no approval {supersedes} to replace")
        if named["decision"] == "approved" and not named["consumed_at"]:
            # Withdrawing only the undecided would leave the old yes spendable next to the new ask.
            refuse(conn, actor, "lint", f"{supersedes} is already approved and not spent; use it, or ask on its "
                   "own without --supersedes")
    owner = None
    if task_id:
        row = task(conn, task_id)
        if row and row["status"] in TASK_ENDED:
            # It would only be withdrawn: nothing acts on an ended task's approvals.
            refuse(conn, actor, "lint", f"task {task_id} is {row['status']}; {task_reopen_hint(actor, row)}")
        if row and is_human(row["owner"]):
            owner = row["owner"]
    if kind == "support" and is_human(actor):
        # A person's own report (their token, or the Assistant for them) is theirs to approve and is filed as them.
        owner = actor
    owner = to or owner or human_actor(default_human(conn))     # `to`: a redesign proposal's decider
    ts = now()
    ask = ("Send this to Tico support? " + _clip(payload["message"], 200) if kind == "support"
           else f"Approve this {kind}: " + _clip(text, 200))
    msg = _write_message(conn, actor, owner, ask,
                         _pair_conversation(conn, actor, owner, "notice"), "notice",
                         {"approval": "pending", "task": task_id}, None, None)
    row = {"id": new_id(), "kind": kind, "task_id": task_id, "message_id": msg["id"],
           "payload_json": _dump(payload), "payload_hash": digest, "requested_by": actor,
           "decided_by": None, "decision": None, "decided_at": None, "consumed_at": None,
           "created": ts, "ready_since": ts, "decision_owner": owner}
    # Ready now: a payload that fails its checks is refused above, so this is when it became a decision for `owner`.
    conn.execute("INSERT INTO approvals (id, kind, task_id, message_id, payload_json, payload_hash, "
                 "requested_by, decided_by, decision, decided_at, consumed_at, created, ready_since, decision_owner) "
                 "VALUES (:id, :kind, :task_id, :message_id, :payload_json, :payload_hash, :requested_by, "
                 ":decided_by, :decision, :decided_at, :consumed_at, :created, :ready_since, :decision_owner)", row)
    conn.execute("UPDATE messages SET refs_json=? WHERE id=?",
                 (_dump({"approval": row["id"], "task": task_id}), msg["id"]))
    event(conn, actor, "approval.request", row["id"], {"kind": kind, "to": owner})
    replaced = {supersedes} if supersedes else set()
    target = approval_target(kind, payload)
    if task_id and target and is_bot(actor):
        replaced |= {r["id"] for r in conn.execute(
            "SELECT id, payload_json FROM approvals WHERE task_id=? AND kind=? AND requested_by=? AND decision IS NULL "
            "AND id<>?", (task_id, kind, actor, row["id"])).fetchall()
            if approval_target(kind, _json(r["payload_json"], {})) == target}
    for aid in sorted(replaced):
        approval_withdraw(conn, aid, f"Replaced by {row['id']}.")
    _recount(conn, actor)
    hook(conn, "approval.requested", row["id"], lambda: _approval_facts(row), actor, revision=0)
    return approval(conn, row["id"])


def approval_decide(conn, actor, approval_id, decision, note=""):
    """Rule 6. Only a human decides, and only once."""
    _writer(conn, actor)
    if not is_human(actor):
        refuse(conn, actor, "identity", "only a person decides an approval")
    row = approval(conn, approval_id)
    if not row:
        refuse(conn, actor, "not-found", f"no approval {approval_id}")
    if decision not in ("approved", "declined"):
        refuse(conn, actor, "kind", f"a decision is approved|declined, not {decision}")
    if row["decision"]:
        refuse(conn, actor, "duplicate", f"{approval_id} was already {row['decision']}")
    ts = now()
    # The person's own Assistant deciding on their words (backend/assistant.py): recorded on the approval itself, so its
    # history says "decided by <assistant> for <person>" with the words quoted, and the person can undo it.
    delegated = DELEGATION.get() if VIA.get() == "assistant" else None
    conn.execute("UPDATE approvals SET decision=?, decided_by=?, decided_at=?, decided_via=?, decided_for_json=? "
                 "WHERE id=?", (decision, actor, ts, "assistant" if delegated else None,
                                _dump(delegated) if delegated else None, approval_id))
    event(conn, actor, "approval.decide", approval_id, {"decision": decision, "note": note})
    after = approval(conn, approval_id)
    if row.get("task_id"):
        from . import task_holds
        task_holds.refresh(conn, row["task_id"], f"Your {row['kind']} approval was {decision}")
    conv = conversation(conn, (message(conn, row["message_id"]) or {}).get("conversation_id")) \
        if row.get("message_id") else None
    if conv:
        _write_message(conn, KEEPER, row["requested_by"],
                       f"{decision.title()}: your {row['kind']} approval." + (f"\n{note}" if note else ""),
                       conv, "notice", {"approval": approval_id}, None, None)
    _recount(conn, row["requested_by"])
    hook(conn, "approval.decided", approval_id, lambda: {**_approval_facts(row), "decision": decision, "decided_by": actor},
         actor, revision=decision)
    return after


def approval_consume(conn, actor, approval_id):
    """Rule 6. The connector spends the approval; a second call is `Refused("consumed")`."""
    _writer(conn, actor)
    row = approval(conn, approval_id)
    if not row:
        refuse(conn, actor, "not-found", f"no approval {approval_id}")
    if row["decision"] != "approved":
        refuse(conn, actor, "kind", f"{approval_id} is {row['decision'] or 'undecided'}, not approved")
    if row["consumed_at"]:
        refuse(conn, actor, "consumed", f"{approval_id} was already spent at {row['consumed_at']}",
               classify(json.dumps(row.get("payload") or {}, default=str), kind=row["kind"], conn=conn))
    conn.execute("UPDATE approvals SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                 (now(), approval_id))
    event(conn, actor, "approval.consume", approval_id, {"kind": row["kind"]})
    return approval(conn, approval_id)


# ----------------------------------------------------------------------------- status
def status_set(conn, actor, bot_slug, state=None, focus=None, task_id=None, reason=""):
    """One active row per bot; the old one moves to `bot_status_history` with `until`.

    `state="active"` is how a human clears a quarantine (deviation 5): it sets `bots.state`
    and leaves the live status `idle`.
    """
    _writer(conn, actor)
    slug = actor_id(bot_slug) if actor_kind(bot_slug) else str(bot_slug).strip()
    if not bot(conn, slug):
        refuse(conn, actor, "not-found", f"no bot {slug}")
    lifting = actor == bot_actor(FLEET_MAINTAINER) and state == "active"   # BotOps ending a quarantine
    if is_bot(actor) and actor_id(actor) != slug and not lifting:
        refuse(conn, actor, "identity", f"{actor_id(actor)} may only set its own status")
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
    if quarantined and state not in ("active", "quarantined"):
        # Lease expiry and completion are routine keeper updates. Neither can clear or mask
        # the human review required by quarantine.
        state = "quarantined"
        focus = None
    ts = now()
    old = _one(conn, "SELECT * FROM bot_status WHERE bot=?", (slug,))
    if old:
        conn.execute("INSERT INTO bot_status_history (id, bot, state, focus, task_id, since, until, "
                     "reason, by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (new_id(), slug, old["state"], old["focus"], old["task_id"], old["since"],
                      ts, reason, actor))
    live = state
    if state == "active":
        conn.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        live = "idle"
    elif state in ("paused", "quarantined"):
        conn.execute("UPDATE bots SET state=? WHERE slug=?", (state, slug))
    row = {"bot": slug,
           "state": live if live is not None else (old or {}).get("state") or "idle",
           "focus": focus if focus is not None else (old or {}).get("focus") or "",
           "task_id": task_id if task_id is not None else (old or {}).get("task_id"),
           "since": ts if (not old or live not in (None, old["state"])) else old["since"],
           "last_turn_at": (old or {}).get("last_turn_at"),
           "last_result": (old or {}).get("last_result"),
           "next_due": (old or {}).get("next_due"), "updated_at": ts}
    conn.execute(
        "INSERT INTO bot_status (bot, state, focus, task_id, since, last_turn_at, last_result, "
        "next_due, open_tasks, needs_human, updated_at) VALUES (:bot, :state, :focus, :task_id, "
        ":since, :last_turn_at, :last_result, :next_due, 0, 0, :updated_at) "
        "ON CONFLICT(bot) DO UPDATE SET state=:state, focus=:focus, task_id=:task_id, since=:since, "
        "updated_at=:updated_at", row)
    event(conn, actor, "status.set", slug, {"state": live, "focus": focus, "reason": reason})
    _recount(conn, bot_actor(slug))
    return status(conn, slug)


def status_result(conn, actor, bot_slug, last_result=None, last_turn_at=None, next_due=None):
    """The keeper's side of a status row: what came out of the last turn, and when the next is."""
    _writer(conn, actor)
    slug = actor_id(bot_slug) if actor_kind(bot_slug) else str(bot_slug).strip()
    if not status(conn, slug):
        status_set(conn, actor, slug, state="idle")
    for field, value in (("last_result", last_result), ("last_turn_at", last_turn_at),
                         ("next_due", next_due)):
        if value is not None:
            conn.execute(f"UPDATE bot_status SET {field}=?, updated_at=? WHERE bot=?",
                         (value, now(), slug))
    if last_turn_at:
        conn.execute("UPDATE bots SET last_turn_at=? WHERE slug=?", (last_turn_at, slug))
    return status(conn, slug)


def status_counts(conn, actor):
    """`(open_tasks, needs_human)` for a bot, as they are now.

    `needs_human` is one per thing that waits on a person: a live task the bot filed for a person,
    its own task set waiting on a person (`waiting_on`), a live task carrying its unanswered question
    to a person, and an undecided approval it asked for. A task counts once whichever of these it
    has, its approval included, as does a task blocked on a person's subtask (_ask_in_subtask)."""
    live = ACTIVE_STATUSES
    marks = ",".join("?" * len(live))
    open_tasks = conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE owner=? AND status IN ({marks})",
        (actor, *live)).fetchone()[0]
    waits = {r[0] for r in conn.execute(
        f"SELECT t.id FROM tasks t WHERE t.status IN ({marks}) AND (t.owner=? OR t.requester=?) AND ("
        "(t.requester=? AND t.owner LIKE 'human:%') "
        "OR (t.owner=? AND t.status='waiting' AND t.waiting_on LIKE 'human:%') "
        "OR EXISTS (SELECT 1 FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE m.conversation_id=t.conversation_id AND {MESSAGE_TASK_SQL}=t.id AND m.kind='ask' "
        f"AND m.from_actor=? AND m.to_actor LIKE 'human:%' AND m.deleted_at IS NULL AND {OPEN_ASK_SQL}))",
        (*live, actor, actor, actor, actor, actor))}
    held = waits | {r[0] for r in conn.execute(
        "SELECT r.to_task FROM task_relations r JOIN tasks t ON t.id=r.from_task WHERE r.kind='blocks' "
        f"AND t.requester=? AND t.owner LIKE 'human:%' AND t.status IN ({marks})", (actor, *live))}
    approvals = sum(not r[0] or r[0] not in held for r in conn.execute(
        "SELECT task_id FROM approvals WHERE requested_by=? AND decision IS NULL", (actor,)))
    return open_tasks, len(waits) + approvals


def _recount(conn, actor):
    """`open_tasks` and `needs_human` on a bot's status row, after anything that moves a task."""
    if not is_bot(actor):
        return
    slug = actor_id(actor)
    if not _one(conn, "SELECT bot FROM bot_status WHERE bot=?", (slug,)):
        return
    open_tasks, waiting = status_counts(conn, actor)
    conn.execute("UPDATE bot_status SET open_tasks=?, needs_human=?, updated_at=? WHERE bot=?",
                 (open_tasks, waiting, now(), slug))


def recount(conn, actors):
    """`_recount` for each bot among `actors` (task deletes and restores)."""
    for actor in dict.fromkeys(a for a in actors if a):
        _recount(conn, actor)


# ----------------------------------------------------------------------------- schedules, turns, limits
def schedule_sync(conn, actor, entries):
    """The hosted bots' crons, from their `bot.yaml`. Replaces the rows for the bots named."""
    _writer(conn, actor)
    entries = [e for e in (entries or []) if (e or {}).get("bot")]
    touched = sorted({str(e["bot"]) for e in entries})
    for slug in touched:
        conn.execute("DELETE FROM schedules WHERE bot=?", (slug,))
    out = []
    for e in entries:
        row = {"id": new_id(), "bot": str(e["bot"]), "cron": str(e.get("cron") or ""),
               "title": str(e.get("title") or ""), "playbook": str(e.get("playbook") or ""),
               "last_fired": e.get("last_fired"), "next_due": e.get("next_due")}
        conn.execute("INSERT INTO schedules (id, bot, cron, title, playbook, last_fired, next_due) "
                     "VALUES (:id, :bot, :cron, :title, :playbook, :last_fired, :next_due)", row)
        out.append(row)
    event(conn, actor, "schedule.sync", ",".join(touched), {"count": len(out)})
    return out


def schedule_fired(conn, actor, schedule_id, next_due=None):
    _writer(conn, actor)
    conn.execute("UPDATE schedules SET last_fired=?, next_due=COALESCE(?, next_due) WHERE id=?",
                 (now(), next_due, schedule_id))
    event(conn, actor, "schedule.fired", schedule_id)
    return _one(conn, "SELECT * FROM schedules WHERE id=?", (schedule_id,))


def schedules(conn, bot_slug=None):
    if bot_slug:
        return _rows(conn.execute("SELECT * FROM schedules WHERE bot=? ORDER BY title",
                                  (actor_id(bot_slug),)))
    return _rows(conn.execute("SELECT * FROM schedules ORDER BY bot, title"))


def rate_limit_set(conn, actor, runtime, used_percent=None, window_minutes=None, resets_at=None):
    """What a daemon reports about its account's window."""
    _writer(conn, actor)
    row = {"runtime": str(runtime), "used_percent": used_percent,
           "window_minutes": window_minutes, "resets_at": resets_at, "updated": now()}
    conn.execute("INSERT INTO rate_limits (runtime, used_percent, window_minutes, resets_at, updated) "
                 "VALUES (:runtime, :used_percent, :window_minutes, :resets_at, :updated) "
                 "ON CONFLICT(runtime) DO UPDATE SET "
                 "used_percent=COALESCE(:used_percent, used_percent), "
                 "window_minutes=COALESCE(:window_minutes, window_minutes), "
                 "resets_at=COALESCE(:resets_at, resets_at), updated=:updated", row)
    event(conn, actor, "rate_limit.set", runtime, {"used_percent": used_percent})
    return _one(conn, "SELECT * FROM rate_limits WHERE runtime=?", (str(runtime),))


def rate_limits(conn):
    return _rows(conn.execute("SELECT * FROM rate_limits ORDER BY runtime"))


def turn_start(conn, actor, bot_slug, thread_id=None, trigger="message", message_id=None,
               task_id=None, trigger_kind=None, trigger_source=None, purpose=None, rule_id=None):
    """A turn on a bot's thread. Replaces the legacy dispatcher's `runs.jsonl` record.
    `trigger_kind` is what woke it (backend/run_triggers.py TRIGGERS) and `trigger_source` the event that did.
    `purpose` is what the run is for; the caller names it, or the bot and trigger map in run_triggers does.
    `rule_id` is the wake rule that started it (backend/rules.py)."""
    _writer(conn, actor)
    slug = actor_id(bot_slug) if actor_kind(bot_slug) else str(bot_slug).strip()
    if is_bot(actor) and actor_id(actor) != slug:
        refuse(conn, actor, "identity", f"{actor_id(actor)} may only record its own turns")
    row = {"id": new_id(), "bot": slug, "thread_id": thread_id, "started": now(),
           "finished": None, "trigger": trigger, "message_id": message_id, "task_id": task_id,
           "exit": None, "tokens_in": None, "tokens_out": None, "cost": None, "summary": None,
           "trigger_kind": trigger_kind or "other", "trigger_source": trigger_source,
           "purpose": RT.clean_purpose(purpose) or RT.purpose_for(slug, trigger_kind or "other"),
           "rule_id": (str(rule_id)[:80] if rule_id else None)}
    conn.execute("INSERT INTO turns (id, bot, thread_id, started, finished, trigger, message_id, "
                 "task_id, exit, tokens_in, tokens_out, cost, summary, trigger_kind, trigger_source, purpose, writes, "
                 "rule_id) "
                 "VALUES (:id, :bot, :thread_id, :started, :finished, :trigger, :message_id, :task_id, :exit, "
                 ":tokens_in, :tokens_out, :cost, :summary, :trigger_kind, :trigger_source, :purpose, 0, "
                 ":rule_id)", row)
    event(conn, actor, "turn.start", row["id"], {"bot": slug, "trigger": trigger, "trigger_kind": row["trigger_kind"],
                                              "purpose": row["purpose"]})
    return _one(conn, "SELECT * FROM turns WHERE id=?", (row["id"],))


# Why a finished run's usage says 0 tokens or no cost (`turns.usage_reason`). Usage is written on every finish, so a
# sum over input_tokens, cached_tokens, output_tokens and est_cost_usd counts every run, failed and stopped ones too.
USAGE_REASONS = {
    "usage_unreported": "its runner sent a result with no token counts (the harness reported none)",
    "usage_limited": "the runtime refused the turn on a usage limit and reported no tokens",
    "no_result": "its runner never sent a result (the lease ran out, or a restore ended it)",
    "cost_unpriced": "tokens were counted, but its model has no list price, so est_cost_usd is null",
}
NO_USAGE = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0}


def turn_finish(conn, actor, turn_id, exit_code="ok", summary=None, usage=None, usage_reason="usage_unreported"):
    """Close a turn. `exit_code` is the `exit` column (`exit` is a builtin, not a keyword here).
    `usage` is what the run cost in tokens: input_tokens, cached_tokens, output_tokens, model, provider,
    est_cost_usd and billing (see USAGE_SCHEMA), and prompt_tokens, its newest request's prompt when known.
    Without `usage` the run is recorded at 0 tokens and $0 with `usage_reason` (USAGE_REASONS) saying why; never
    left blank. The legacy `tokens_in`, `tokens_out` and `cost` columns are no longer written (docs/hub-sql.md)."""
    _writer(conn, actor)
    ts = now()
    conn.execute("UPDATE turns SET finished=?, exit=?, summary=? WHERE id=?", (ts, exit_code, summary, turn_id))
    # Whether it acted, from what it wrote and its task's state as it ends (backend/run_triggers.py).
    conn.execute(f"UPDATE turns SET {RT.SETTLE_SQL} WHERE id=?", (turn_id,))
    if usage:
        reason = "cost_unpriced" if usage.get("est_cost_usd") is None else None
    else:
        reason, usage = usage_reason, NO_USAGE
    conn.execute("UPDATE turns SET input_tokens=:input_tokens, cached_tokens=:cached_tokens, "
                 "output_tokens=:output_tokens, model=:model, provider=:provider, est_cost_usd=:est_cost_usd, "
                 "billing=:billing, prompt_tokens=:prompt_tokens, usage_reason=:usage_reason WHERE id=:id",
                 {"input_tokens": None, "cached_tokens": None, "output_tokens": None, "model": None,
                  "provider": None, "est_cost_usd": None, "billing": None, "prompt_tokens": None, **usage,
                  "usage_reason": reason, "id": turn_id})
    if usage and usage.get("segments"):
        conn.execute("DELETE FROM turn_usage_segments WHERE turn_id=?", (turn_id,))
        for position, part in enumerate(usage["segments"]):
            conn.execute("INSERT INTO turn_usage_segments(turn_id,position,input_tokens,cached_tokens,output_tokens,"
                         "model,provider,est_cost_usd,billing,runtime,harness,effort,profile,est_cost_low,"
                         "gateway_key_id,install_id) "
                         "VALUES(:turn_id,:position,:input_tokens,:cached_tokens,:output_tokens,:model,:provider,"
                         ":est_cost_usd,:billing,:runtime,:harness,:effort,:profile,:est_cost_low,"
                         ":gateway_key_id,:install_id)",
                         {"est_cost_low": None, "gateway_key_id": None, "install_id": None, **part,
                          "turn_id": turn_id, "position": position})
    row = _one(conn, "SELECT * FROM turns WHERE id=?", (turn_id,))
    if row:
        conn.execute("UPDATE bots SET last_turn_at=? WHERE slug=?", (ts, row["bot"]))
        conn.execute("UPDATE bot_status SET last_turn_at=?, updated_at=? WHERE bot=?",
                     (ts, ts, row["bot"]))
    from . import run_diagnostics
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='attempts' AND type='table'").fetchone():
        run_diagnostics.finish(conn, turn_id, exit_code, summary)
    from . import check_pools
    check_pools.capture_attempt(conn, turn_id)
    event(conn, actor, "turn.finish", turn_id, {"exit": exit_code, "est_cost_usd": usage.get("est_cost_usd")})
    return row


def delta_append(conn, actor, turn_id, text, kind="text"):
    """Streamed text or thought while a turn runs. Pruned after an hour by `prune_deltas`."""
    seq = (conn.execute("SELECT COALESCE(MAX(seq), 0) FROM deltas WHERE turn_id=?",
                        (turn_id,)).fetchone()[0] or 0) + 1
    row = {"id": new_id(), "turn_id": turn_id, "seq": seq, "kind": kind, "text": str(text or ""),
           "ts": now()}
    conn.execute("INSERT INTO deltas (id, turn_id, seq, kind, text, ts) "
                 "VALUES (:id, :turn_id, :seq, :kind, :text, :ts)", row)
    return row


def deltas(conn, turn_id, since_seq=0):
    return _rows(conn.execute("SELECT * FROM deltas WHERE turn_id=? AND seq>? ORDER BY seq",
                              (turn_id, since_seq)))


def prune_deltas(conn, before=None):
    """Deltas are the only rows that go: they are a stream, not a record (rule 9)."""
    cutoff = before or shift(now(), hours=-1)
    n = conn.execute("DELETE FROM deltas WHERE ts < ?", (cutoff,)).rowcount
    return n


# ----------------------------------------------------------------------------- reads
def bot(conn, slug):
    return _one(conn, "SELECT * FROM bots WHERE slug=?", (actor_id(slug),))


def bots(conn, host=None, state=None):
    sql, args = "SELECT * FROM bots", []
    where = []
    if host:
        where.append("host=?")
        args.append(host)
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
        row["teams"] = _json(row.get("teams_json"), []) or []
        for key in HUMAN_ACCOUNT_COLUMNS:
            row.pop(key, None)
    return row


def humans(conn):
    out = _rows(conn.execute("SELECT * FROM humans ORDER BY id"))
    for row in out:
        row["teams"] = _json(row.get("teams_json"), []) or []
        for key in HUMAN_ACCOUNT_COLUMNS:
            row.pop(key, None)
    return out


CONVERSATION_SQL = "SELECT * FROM conversations WHERE id=?"


def conversation(conn, conversation_id):
    row = _one(conn, CONVERSATION_SQL, (conversation_id,))
    if row:
        row["participants"] = _json(row.get("participants_json"), []) or []
    return row


def prime_conversations(conn, ids):
    """Read these conversations in one query per 500 for the conversation() lookups that follow in this snapshot."""
    from .read_cache import cache, prime
    values = cache(conn)
    wanted = {cid for cid in ids if cid}
    if values is not None:
        wanted = {cid for cid in wanted if ("one", CONVERSATION_SQL, (cid,)) not in values}
    for part in chunks(sorted(wanted)):
        found = {row["id"]: row for row in conn.execute(
            "SELECT * FROM conversations WHERE id IN (%s)" % ",".join("?" * len(part)), part)}
        prime(conn, CONVERSATION_SQL, {(cid,): found.get(cid) for cid in part})


def conversations_for(conn, actor, limit=100):
    """Every conversation this actor is a participant in, newest first."""
    out = []
    for row in _rows(conn.execute("SELECT * FROM conversations ORDER BY "
                                  "COALESCE(last_message_at, created) DESC LIMIT ?", (limit * 5,))):
        row["participants"] = _json(row.get("participants_json"), []) or []
        if actor in row["participants"]:
            out.append(row)
        if len(out) >= limit:
            break
    return out


def chats_with(conn, actor, bot_actor_id):
    """The open chats (not task threads) that this actor and this bot are both in, newest first.
    Found directly, not from the newest conversations: a bot's page opened a month after the last
    message still finds its chat (conversations_for scans a recent window only)."""
    out = []
    for row in _rows(conn.execute("SELECT * FROM conversations WHERE kind='chat' AND task_id IS NULL "
                                  "AND closed_at IS NULL AND participants_json LIKE ? ORDER BY "
                                  "COALESCE(last_message_at, created) DESC", (f'%"{bot_actor_id}"%',))):
        row["participants"] = _json(row.get("participants_json"), []) or []
        if actor in row["participants"] and bot_actor_id in row["participants"]:
            out.append(row)
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
    out = {mid: values[("one", MESSAGE_SQL, (mid,))] for mid in known
           if values[("one", MESSAGE_SQL, (mid,))] is not None}
    for part in chunks(sorted(wanted - known)):
        found = {row["id"]: row for row in conn.execute(
            "SELECT * FROM messages WHERE id IN (%s)" % ",".join("?" * len(part)), part)}
        _prime_message_lookups(conn, part, found)
        out.update(found)
    return out


def _prime_message_lookups(conn, ids, found):
    """Seed both message() lookups for `ids` from `found`, {id: stored row}: a row missing from it is no message."""
    from .read_cache import prime
    prime(conn, MESSAGE_SQL, {(mid,): found.get(mid) for mid in ids})
    live = {mid: row for mid, row in found.items() if row["deleted_at"] is None}
    prime(conn, MESSAGE_SQL + " AND deleted_at IS NULL", {(mid,): live.get(mid) for mid in ids})


MESSAGE_PARENT_DEPTH = 100      # task_privacy and live_privacy refuse a provenance walk of more messages than this


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
            refs = _json(row["refs_json"], {}) or {}
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
        row["refs"] = _json(row.get("refs_json"), {}) or {}
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
        sql += " AND created > ?"
        args.append(since)
    out = _rows(conn.execute(sql + " ORDER BY created LIMIT ?", (*args, limit)))
    for row in out:
        row["refs"] = _json(row.get("refs_json"), {}) or {}
    return out


def answers_to(conn, message_ids):
    """`{ask id: answer row}` for the asks that have been answered. What `hub question ask --wait` polls.

    An answer is either a reply addressed to the ask, or -- when the person simply wrote back --
    the message that closed it (`answered_by`). Both are the person answering, and a bot waiting
    on one should not keep waiting through the other.
    """
    out = {}
    for mid in message_ids or []:
        row = _one(conn, ANSWER_SQL, (mid,))
        if not row:
            asked = _one(conn, ANSWERED_BY_SQL, (mid,))
            if asked and asked.get("answered_by"):
                row = _one(conn, "SELECT * FROM messages WHERE id=? AND deleted_at IS NULL", (asked["answered_by"],))
        if row:
            row["refs"] = _json(row.get("refs_json"), {}) or {}
            out[mid] = row
    return out


def undelivered(conn, to_actor=None, limit=200):
    """What the keeper's mailbox loop reads: messages no runtime has accepted yet."""
    sql = "SELECT * FROM messages WHERE delivered_at IS NULL AND deleted_at IS NULL"
    args = []
    if to_actor:
        sql += " AND to_actor=?"
        args.append(to_actor)
    out = _rows(conn.execute(sql + " ORDER BY created LIMIT ?", (*args, limit)))
    for row in out:
        row["refs"] = _json(row.get("refs_json"), {}) or {}
    return out


# A message still waiting for its recipient: not yet delivered, or not yet read. The partial index repeats these terms
# word for word, or SQLite does not use it (`inbox_pages`).
UNREAD_TERMS = "(delivered_at IS NULL OR read_at IS NULL) AND deleted_at IS NULL"
UNREAD_INDEX = "CREATE INDEX IF NOT EXISTS messages_unread_to ON messages(to_actor, created) WHERE " + UNREAD_TERMS
UNREAD_PAGE_SQL = ("SELECT rowid AS _seq,* FROM messages WHERE to_actor=? AND " + UNREAD_TERMS +
                   " AND (expires_at IS NULL OR expires_at > ?) AND created<=? AND (created<? OR rowid<?)"
                   " ORDER BY created DESC, rowid DESC LIMIT ?")


def inbox_pages(conn, actor, at=None, size=100):
    """`actor`'s unread or undelivered messages, newest first, `size` at a time, each once. A bot that never marks
    messages read holds thousands, and a reader that wants only the newest few stops paging: the rest are not read.
    Each page carries on below the last (created, rowid) of the one before, so messages sent in the same instant are
    neither repeated nor skipped. Read the pages in one snapshot."""
    at = at or now()
    created, seq = "\uffff", 1 << 62                   # above every stored time and rowid: the first page is the newest
    while True:
        rows = _rows(conn.execute(UNREAD_PAGE_SQL, (actor, at, created, created, seq, size)))
        if not rows:
            return
        created, seq = rows[-1]["created"], rows[-1]["_seq"]
        for row in rows:
            del row["_seq"]
            row["refs"] = _json(row.get("refs_json"), {}) or {}
        yield rows
        if len(rows) < size:
            return


def mark_answered(conn, rows):
    """Set each message's `answered_at`: when its ask was answered, or None."""
    answered = answers_to(conn, [r["id"] for r in rows if r.get("kind") == "ask"])
    for row in rows:
        got = answered.get(row["id"])
        row["answered_at"] = got["created"] if got else None
    return rows


def inbox(conn, actor, at=None):
    """What is waiting for me: unread or undelivered messages, oldest first, and my open tasks."""
    rows = [row for page in inbox_pages(conn, actor, at, size=500) for row in page][::-1]
    return {"actor": actor, "messages": mark_answered(conn, rows),
            "tasks": tasks(conn, owner=actor, status=ACTIVE_STATUSES)}


def notices(conn, actor, at=None):
    """The inbox tab: unexpired notices, unread first."""
    at = at or now()
    rows = _rows(conn.execute(
        "SELECT * FROM messages WHERE to_actor=? AND kind='notice' "
        "AND (expires_at IS NULL OR expires_at > ?) "
        "ORDER BY (read_at IS NOT NULL), created DESC", (actor, at)))
    for row in rows:
        row["refs"] = _json(row.get("refs_json"), {}) or {}
    return rows


TASK_SQL = "SELECT * FROM tasks WHERE id=?"


def prime_tasks(conn, ids, with_tags=True):
    """Read these tasks and their tags in one query each per 500, for the task() lookups that follow in this snapshot
    (a Needs you card reads its task, its ancestors and what they block, one at a time). `with_tags=False`: the rows only."""
    from .read_cache import cache, prime, prime_rows
    values = cache(conn)
    if values is None:
        return
    wanted = {tid for tid in ids if tid}
    # A row already read in this snapshot (prime_task_rows) is not read again; only its missing tags are.
    unread = sorted(tid for tid in wanted if ("one", TASK_SQL, (tid,)) not in values)
    for part in chunks(unread):
        marks = ",".join("?" * len(part))
        found = {row["id"]: row for row in conn.execute("SELECT * FROM tasks WHERE id IN (%s)" % marks, part)}
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


def task_labels(row):
    return [tag["key"] for tag in (row or {}).get("tags", [])]


def labels_in_use(conn, visible="1"):
    """Every label on a task that is still on the board, most used first. `visible` limits it to
    the tasks the caller may read (`Auth.task_sql`)."""
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    return [r[0] for r in conn.execute(
        f"SELECT tags.key, COUNT(*) n FROM (SELECT id FROM tasks WHERE status IN ({marks}) "
        f"AND ({visible})) visible_tasks JOIN task_tags ON task_tags.task_id=visible_tasks.id "
        "JOIN tags ON tags.id=task_tags.tag_id "
        "GROUP BY tags.key ORDER BY n DESC, tags.key", ACTIVE_STATUSES)]


STEP_POSITION = "(SELECT position FROM task_steps WHERE task_steps.id=tasks.step_id)"


def tasks(conn, owner=None, requester=None, status=None, limit=500, lane=None, label=None,
          offset=0, order="queue", visible=None, type_id=None, step_ids=None, number=None,
          updated_since=None, tickets=True, member=None, role=None, ids=None, snoozed=None):
    """Tasks, newest work first. `visible` is a WHERE fragment over the task's own columns (from
    `Auth.task_sql`), so a caller's page and its `offset` are cut in the query. `order="step"` is
    a board's: by step, then each task's place in it. `updated_since` is a stored timestamp.
    `tickets=False` leaves out the tasks on a numbered type. `member` keeps the tasks that actor is on
    in some role (task_roles), or in `role` when given. `ids` limits it to those tasks (a delta
    read's changed ones). `snoozed` is "hide" (leave out snoozed tasks), "only", or None for both.
    `limit=None` reads every match."""
    sql, args, where = "SELECT * FROM tasks", [], []
    if member:
        where.append(TRo.member_sql(role))
        args += [member] + ([role] if role else [])
    if visible and visible != "1":
        where.append("(" + visible + ")")
    if owner:
        where.append("owner=?")
        args.append(owner)
    if requester:
        where.append("requester=?")
        args.append(requester)
    if lane:
        where.append("lane=?")
        args.append(lane)
    if type_id:
        where.append("type_id=?")
        args.append(type_id)
    if step_ids:
        where.append(f"step_id IN ({','.join('?' * len(step_ids))})")
        args += list(step_ids)
    if number is not None:
        where.append("number=?")
        args.append(number)
    if updated_since:
        where.append("updated>?")
        args.append(updated_since)
    if ids is not None:
        where.append("id IN (SELECT value FROM json_each(?))")
        args.append(json.dumps(list(ids)))
    if snoozed in ("hide", "only"):
        where.append("coalesce(snoozed_until,'')" + ("<=?" if snoozed == "hide" else ">?"))
        args.append(now())
    if not tickets:
        where.append("NOT EXISTS (SELECT 1 FROM task_types WHERE task_types.id=tasks.type_id AND task_types.numbered=1)")
    if label:
        where.append("EXISTS (SELECT 1 FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                     "WHERE task_tags.task_id=tasks.id AND tags.key=?)")
        args.append(str(label).strip().lower())
    if status:
        wanted = [status] if isinstance(status, str) else list(status)
        # These two hot paths use matching partial indexes. The values are fixed constants,
        # while every other caller remains parameterized.
        if tuple(wanted) == BOARD_STATUSES:
            where.append("status IN ('open','doing','waiting','review','ready','declined')")
        elif tuple(wanted) == ("done", "closed"):
            where.append("status IN ('done','closed')")
        else:
            where.append(f"status IN ({','.join('?' * len(wanted))})")
            args += wanted
    if where:
        sql += " WHERE " + " AND ".join(where)
    if order == "queue":
        ordering = "(rank IS NULL), rank, created, id"
    elif order == "finished":
        ordering = "COALESCE(done_at,closed_at,updated,created) DESC, id DESC"   # most recently done first
    elif order == "step":
        ordering = (f"{STEP_POSITION} IS NULL, {STEP_POSITION}, step_id, (step_rank IS NULL), step_rank, "
                    "created, id")
    else:
        raise ValueError("task order is queue, finished or step")
    page = (*args, -1 if limit is None else max(1, int(limit)), max(0, int(offset)))
    if order == "queue" and limit is not None:
        # The page's ids first, then its full rows. Sorting `SELECT *` copied every visible task's body into the
        # sorter (26 MB of bodies at TIDY's 10.8k tasks) on each list page; tasks_queue_cover answers the ids alone.
        ids = [row[0] for row in conn.execute("SELECT id" + sql[len("SELECT *"):] + f" ORDER BY {ordering} LIMIT ? OFFSET ?",
                                              page)]
        found = {}
        for part in chunks(ids):
            found.update((row["id"], row) for row in _rows(conn.execute(
                f"SELECT * FROM tasks WHERE id IN ({','.join('?' * len(part))})", part)))
        rows = [found[ident] for ident in ids if ident in found]
    else:
        rows = _rows(conn.execute(sql + f" ORDER BY {ordering} LIMIT ? OFFSET ?", page))
    hydrate_task_tags(conn, rows)
    return rows


# The due-date reminder pass (backend/scheduler.py) reads only the columns it uses, through tasks_due_active: its
# predicate is this query's status list and due test, word for word, or SQLite will not use it and scans every task.
TASKS_DUE_SQL = (
    f"SELECT id,title,owner,due,conversation_id FROM tasks WHERE status IN ({','.join(repr(x) for x in ACTIVE_STATUSES)}) "
    "AND due IS NOT NULL AND due!='' AND substr(due,1,10)<=? AND owner LIKE 'bot:%' AND snoozed_until IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM task_reminders r WHERE r.task_id=tasks.id AND r.due=tasks.due) "
    "ORDER BY created,id")
TASKS_DUE_INDEX = (
    "CREATE INDEX IF NOT EXISTS tasks_due_active ON tasks(substr(due,1,10)) "
    f"WHERE status IN ({','.join(repr(x) for x in ACTIVE_STATUSES)}) AND due IS NOT NULL AND due!=''")
# quiet_work.due's query, the same way: its predicate is that query's, word for word.
TASKS_RESUME_INDEX = ("CREATE INDEX IF NOT EXISTS tasks_resume_due ON tasks(json_extract(resume_json,'$.until')) "
                      "WHERE resume_json IS NOT NULL AND next_run=1 AND status='open'")


def tasks_due_for_bots(conn, through):
    """Live tasks a bot owns whose due date may have arrived by `through` (a "YYYY-MM-DD").

    Due dates are text in more than one shape (an offset, a bare date, an old local time), so this
    is a day-level bound with a day of slack; the caller compares the exact instant. No row cap:
    the reminder pass needs every one of them, however many tasks the hub holds.
    """
    return _rows(conn.execute(TASKS_DUE_SQL, (through,)))


FLOW_STATUSES = ("created",) + TASK_STATUSES


def task_flow(conn, since, until=None, tz_minutes=0, visible="1", owner=None, requester=None,
              owner_kind=None, label=None, recurring=None, type_id=None):
    """How tasks moved through their statuses between `since` and `until`, read from `task_events`.

    Every status change is already an event, so nothing extra is stored. `days` counts the tasks
    that entered each stage on each local day ("created" is a new task, "open" a reopened one);
    with `type_id`, each of that type's steps is a stage too, keyed "step:<id>". `stages` is how
    long tasks sat in a stage they left in the window, and `people` is the same count by the
    task's current owner. `visible` is the caller's WHERE fragment over task columns
    (`Auth.task_sql`); the other filters also apply to the task as it is now.
    """
    where, args = ["(" + (visible or "1") + ")", "lane='company'"], []
    if owner:
        where.append("owner=?"); args.append(owner)
    if requester:
        where.append("requester=?"); args.append(requester)
    if owner_kind in ("bot", "human"):
        where.append("owner LIKE ?"); args.append(owner_kind + ":%")
    if type_id:
        where.append("type_id=?"); args.append(type_id)
    if label:
        where.append("EXISTS (SELECT 1 FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                     "WHERE task_tags.task_id=tasks.id AND tags.key=?)")
        args.append(str(label).strip().lower())
    if recurring is not None:
        if _has_table(conn, "schedule_occurrences"):
            where.append(("" if recurring else "NOT ") + "EXISTS (SELECT 1 FROM schedule_occurrences o WHERE o.task_id=tasks.id)")
        elif recurring:
            where.append("0")
    picked = "picked AS (SELECT id, owner FROM tasks WHERE " + " AND ".join(where) + ")"
    fields = "e.field='status'" if not type_id else "(e.field='status' OR (e.field='step' AND e.new IS NOT NULL))"
    window = f"{fields} AND e.ts>=?" + (" AND e.ts<?" if until else "")
    window_args = [since] + ([until] if until else [])
    stage = ("CASE WHEN e.field='step' THEN 'step:' || e.new WHEN e.old IS NULL THEN 'created' ELSE e.new END")
    days, people = {}, {}
    for day, owner_actor, name, n in conn.execute(
            f"WITH {picked} SELECT date(e.ts, ?) AS day, t.owner, {stage} AS stage, count(*) FROM task_events e "
            f"JOIN picked t ON t.id=e.task_id WHERE {window} GROUP BY day, t.owner, stage",
            (*args, f"{int(tz_minutes):+d} minutes", *window_args)):
        days.setdefault(day, {})
        days[day][name] = days[day].get(name, 0) + n
        people.setdefault(owner_actor, {})
        people[owner_actor][name] = people[owner_actor].get(name, 0) + n
    # Time in a stage: from entering it to the field's next change, for stages left in the window.
    spans = {}
    for name, hours in conn.execute(
            f"WITH {picked}, s AS (SELECT e.field, e.new, e.ts, LEAD(e.ts) OVER "
            "(PARTITION BY e.task_id, e.field ORDER BY e.ts, e.id) AS left_at FROM task_events e "
            f"WHERE {fields} AND e.task_id IN (SELECT e.task_id FROM task_events e JOIN picked t ON t.id=e.task_id "
            f"WHERE {window})) SELECT CASE WHEN field='step' THEN 'step:' || new ELSE new END, "
            "(julianday(left_at)-julianday(ts))*24 FROM s WHERE new IS NOT NULL AND left_at>=?"
            + (" AND left_at<?" if until else ""),
            (*args, *window_args, *window_args)):
        spans.setdefault(name, []).append(hours)

    def summary(values):
        values = sorted(values)
        pick = lambda q: round(values[min(len(values) - 1, int(q * len(values)))], 2)
        return {"n": len(values), "median_hours": pick(0.5), "p90_hours": pick(0.9)}
    totals = {}
    for counts in days.values():
        for name, n in counts.items():
            totals[name] = totals.get(name, 0) + n
    steps = _rows(conn.execute("SELECT id, name, position FROM task_steps WHERE type_id=? ORDER BY position, id",
                               (type_id,))) if type_id else []
    return {"days": [{"day": day, **days[day]} for day in sorted(days)], "totals": totals, "steps": steps,
            "stages": {name: summary(values) for name, values in spans.items()},
            "people": sorted(({"actor": actor, **counts} for actor, counts in people.items()),
                             key=lambda row: (-sum(v for k, v in row.items() if k != "actor"), row["actor"]))}


def task_history(conn, task_id):
    return _rows(conn.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY ts", (task_id,)))


def unanswered_ask(conn, task_row, *, actor=None):
    """The latest unanswered ask on this task, or None."""
    return next(iter(open_task_asks(conn, task_row, actor=actor)), None)


def tasks_asked_of(conn, actor):
    """Live tasks with an unanswered ask addressed to this actor, even if they do not own them. A done task stays
    while it asks its requester to accept what it delivered (ACCEPTANCE_ASK_SQL).

    The ask is the small side: start from messages addressed to this actor, then the task that message is in.
    The join is the same one as reading every task's room first (`m.conversation_id=t.conversation_id` and
    `MESSAGE_TASK_SQL=t.id`), so an ask in some other room still does not attach."""
    return _rows(conn.execute(
        "SELECT t.* FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        f"JOIN tasks t ON t.conversation_id=m.conversation_id AND t.id={MESSAGE_TASK_SQL} "
        f"WHERE m.kind='ask' AND m.to_actor=? AND {OPEN_ASK_SQL} "
        f"AND (t.status IN ({','.join(repr(x) for x in ACTIVE_STATUSES)}) "
        f"OR ({ACCEPTANCE_ASK_SQL})) "
        "GROUP BY t.id ORDER BY t.created", (actor,)))


def loose_asks_to(conn, actor):
    """Unanswered questions bots asked this person outside any task (`hub question ask <person>`), oldest first.
    No task carries them into Needs you, so they are listed on their own; an expired one has left."""
    return _rows(conn.execute(
        "SELECT m.* FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE m.to_actor=? AND m.kind='ask' AND m.answered_by IS NULL AND m.deleted_at IS NULL "
        f"AND m.from_actor LIKE 'bot:%' AND {MESSAGE_TASK_SQL} IS NULL AND {OPEN_ASK_SQL} "
        "AND (m.expires_at IS NULL OR m.expires_at>?) ORDER BY m.created", (actor, now())))


def needs_you(conn, who):
    """The "Needs you" list: my open tasks, unanswered asks to me, bot tasks waiting on me,
    approvals, declined-to-me.

    A task on a numbered type is a ticket on that type's board, worked through there: it is on
    the list only while it carries a question for the person, and a declined one stays off its
    requester's list, as does a declined one on a steps-only type."""
    actor = who if actor_kind(who) else human_actor(who)
    pending = _rows(conn.execute("SELECT * FROM approvals WHERE decision IS NULL ORDER BY created"))
    # approval_wait may resolve decision_owner via message(); prime those rows once for Needs you.
    prime_messages(conn, [row["message_id"] for row in pending if row.get("message_id")])
    at = now()
    for row in pending:
        row["payload"] = _json(row.get("payload_json"), {}) or {}
        approval_wait(conn, row, at)
    # The person's queue: asks first (each blocks a bot), then their own tasks in rank order.
    # Product-lane work is a backlog a developer works through on the Product board, not a
    # decision owed today: it counts here only when it carries a question for the person.
    # The same goes for a numbered type's tickets, a board of its own.
    # A snoozed task is nobody's to act on before its date (backend/task_snooze.py).
    # Every one: a person's list does not stop at a page of their tasks (the caller pages the items).
    owned = [t for t in tasks(conn, owner=actor, status=ACTIVE_STATUSES, tickets=False, snoozed="hide", limit=None)
             if (t.get("lane") or "company") != "product"]
    seen = {t["id"] for t in owned}
    asked = [t for t in tasks_asked_of(conn, actor) if t["id"] not in seen and not TS.snoozed(t)]
    seen |= {t["id"] for t in asked}
    # A bot's task set waiting on this person: one concrete ask, its title and waiting note.
    waited = [t for t in _rows(conn.execute("SELECT * FROM tasks WHERE status='waiting' AND waiting_on=? "
                                            "ORDER BY created", (actor,))) if t["id"] not in seen and not TS.snoozed(t)]
    # A bot's task stopped on an account this person fixes (backend/reconnects.py): its card, as a wait on them.
    from . import reconnects
    seen |= {t["id"] for t in waited}
    waited += [t for t in reconnects.tasks_for(conn, actor) if t["id"] not in seen and not TS.snoozed(t)]
    # What the Strategy Bot merged into one item of its own is that item here, not each original
    # (backend/strategy_merge.py).
    from . import strategy_merge
    merged = strategy_merge.hidden(conn, actor)
    # A declined task on a steps-only type (the Product roadmap) stays on its Declined step, where the board and
    # the public roadmap show it: closing it off the list would take it off both, so it is not on the list.
    by_step = {r["id"] for r in conn.execute("SELECT id FROM task_types WHERE steps_only=1")}
    declined = tasks(conn, requester=actor, status="declined", tickets=False, snoozed="hide", limit=None)
    # The rows just read are this snapshot's task rows: a card's task, its ancestors and what they block are primed
    # from them (prime_tasks), not read again by id. Copies, so nothing the caller adds to a row reaches the cache.
    prime_task_rows(conn, [*owned, *asked, *waited, *declined])
    return {"actor": actor,
            "tasks": [t for t in asked + owned if t["id"] not in merged],
            "waiting": [t for t in waited if t["id"] not in merged],
            "approvals": pending,
            "declined": [t for t in declined if (t.get("type_id") or GENERAL_TYPE) not in by_step]}


def prime_task_rows(conn, rows):
    """Seed the snapshot's task reads (`task`, `prime_tasks`) with rows already read from `tasks` in this snapshot,
    as stored: the tags `tasks()` hydrated are left out, as a read by id would have them."""
    from .read_cache import prime
    prime(conn, TASK_SQL, {(row["id"],): {k: v for k, v in row.items() if k != "tags"} for row in rows})


def approval(conn, approval_id):
    row = _one(conn, "SELECT * FROM approvals WHERE id=?", (approval_id,))
    if row:
        row["payload"] = _json(row.get("payload_json"), {}) or {}
        row["decided_for"] = _json(row.pop("decided_for_json", None), None)
        approval_wait(conn, row)
    return row


def is_release_approval(kind, payload):
    """A release's publish approval: asked by its release page with the exact `version` and `commit` it would ship."""
    payload = payload if isinstance(payload, dict) else {}
    return kind == "publish" and all(payload.get(k) not in (None, "") for k in ("version", "commit"))


def approval_wait(conn, row, at=None):
    """Adds to an approval row how long it has waited and on whom: `ready_since`, `decision_owner` and its
    `decision_owner_name`, and `age_s`, the seconds from ready until its decision (until now while undecided). A row from
    before these were recorded reads its request time and the person its request was sent to."""
    row["ready_since"] = row.get("ready_since") or row.get("created")
    if not row.get("decision_owner") and row.get("message_id"):
        row["decision_owner"] = (message(conn, row["message_id"]) or {}).get("to_actor")
    owner = row.get("decision_owner")
    row["decision_owner_name"] = person_name(conn, actor_id(owner)) if owner and is_human(owner) else ""
    start, end = parse_ts(row["ready_since"]), parse_ts(row.get("decided_at") or at or now())
    row["age_s"] = max(0, int((end - start).total_seconds())) if start and end else None
    return row


def wait_words(seconds):
    """'2 h 10 min' for a wait; under a minute is '0 min'."""
    minutes = max(0, int(seconds or 0)) // 60
    days, hours, mins = minutes // 1440, minutes % 1440 // 60, minutes % 60
    if days:
        return f"{days} d {hours} h" if hours else f"{days} d"
    if hours:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    return f"{mins} min"


def approval_wait_line(row):
    """'Ready 2 h 10 min, waiting on Chris': what Needs you says of an undecided approval (approval_wait's fields)."""
    who = row.get("decision_owner_name") or "a person"
    return f"Ready {wait_words(row.get('age_s'))}, waiting on {who}"


def approval_undo(conn, actor, approval_id):
    """The person takes back what their Assistant decided for them, while nothing has acted on it: the approval is
    waiting for them again. A decision of their own, or one already spent, stands."""
    _writer(conn, actor)
    row = approval(conn, approval_id)
    if not row:
        refuse(conn, actor, "not-found", f"no approval {approval_id}")
    if row["decided_by"] != actor or row.get("decided_via") != "assistant":
        refuse(conn, actor, "identity", "only a decision your Assistant made for you can be undone here")
    if row["consumed_at"]:
        raise Refused("consumed", "it already ran; it can't be undone")
    if _one(conn, "SELECT id FROM approvals WHERE payload_hash=? AND decision IS NULL AND id<>?",
            (row["payload_hash"], approval_id)):
        raise Refused("duplicate", "the bot already asked for this again; decide that one instead")
    conn.execute("UPDATE approvals SET decision=NULL, decided_by=NULL, decided_at=NULL, decided_via=NULL, "
                 "decided_for_json=NULL WHERE id=?", (approval_id,))
    event(conn, actor, "approval.undo", approval_id, {"was": row["decision"], "decided_for": row.get("decided_for")})
    conv = conversation(conn, (message(conn, row["message_id"]) or {}).get("conversation_id")) \
        if row.get("message_id") else None
    if conv:
        _write_message(conn, KEEPER, row["requested_by"],
                       f"Undone: the {'approval' if row['decision'] == 'approved' else 'decline'} of your {row['kind']} request was taken back. It is waiting "
                       "for a decision again; don't act on it until then.", conv, "notice", {"approval": approval_id},
                       None, None)
    _recount(conn, row["requested_by"])
    return approval(conn, approval_id)


def approvals(conn, requested_by=None, decision=None, pending=False):
    sql, args, where = "SELECT * FROM approvals", [], []
    if requested_by:
        where.append("requested_by=?")
        args.append(requested_by)
    if decision:
        where.append("decision=?")
        args.append(decision)
    if pending:
        where.append("decision IS NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    out = _rows(conn.execute(sql + " ORDER BY created", args))
    at = now()
    for row in out:
        row["payload"] = _json(row.get("payload_json"), {}) or {}
        row["decided_for"] = _json(row.pop("decided_for_json", None), None)
        approval_wait(conn, row, at)
    return out


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
        sql += " AND since >= ?"
        args.append(since)
    return _rows(conn.execute(sql + " ORDER BY since DESC LIMIT ?", (*args, limit)))


def turns(conn, bot_slug=None, since=None, limit=200):
    sql, args, where = "SELECT * FROM turns", [], []
    if bot_slug:
        where.append("bot=?")
        args.append(actor_id(bot_slug))
    if since:
        where.append("started >= ?")
        args.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return _rows(conn.execute(sql + " ORDER BY started DESC LIMIT ?", (*args, limit)))


def refusals_for(conn, actor=None, day=None):
    sql, args, where = "SELECT * FROM refusals", [], []
    if actor:
        where.append("actor=?")
        args.append(actor)
    if day:
        where.append("substr(ts,1,10)=?")
        args.append(day)
    if where:
        sql += " WHERE " + " AND ".join(where)
    out = _rows(conn.execute(sql + " ORDER BY ts", args))
    for row in out:
        row["detail"] = (_json(row.get("detail_json"), {}) or {}).get("detail", "")
    return out


def events(conn, since=None, actor=None, limit=200):
    sql, args, where = "SELECT * FROM events", [], []
    if since:
        where.append("ts >= ?")
        args.append(since)
    if actor:
        where.append("actor=?")
        args.append(actor)
    if where:
        sql += " WHERE " + " AND ".join(where)
    out = _rows(conn.execute(sql + " ORDER BY ts DESC LIMIT ?", (*args, limit)))
    for row in out:
        row["detail"] = _json(row.get("detail_json"), None)
    return out


TASK_ORIGIN_SQL = ("SELECT detail_json FROM events WHERE actor=? AND action='task.origin' "
                   "AND target=? ORDER BY ts DESC LIMIT 1")


def prime_task_origins(conn, ids):
    """Read the origin events of these tasks in one query per 500, for the task_origin() calls that follow."""
    from .read_cache import cache, prime
    if cache(conn) is None:
        return
    for part in chunks(sorted({tid for tid in ids if tid})):
        found = {}
        for row in conn.execute("SELECT target,detail_json FROM events WHERE actor=? AND action='task.origin' "
                                "AND target IN (%s) ORDER BY ts DESC" % ",".join("?" * len(part)), (KEEPER, *part)):
            found.setdefault(row["target"], {"detail_json": row["detail_json"]})
        prime(conn, TASK_ORIGIN_SQL, {(KEEPER, tid): found.get(tid) for tid in part})


def task_origin(conn, row):
    """Trusted origin for a keeper review, including pre-metadata records; never parse titles."""
    explicit = _one(conn, TASK_ORIGIN_SQL, (KEEPER, row["id"]))
    if explicit:
        origin = (_json(explicit["detail_json"], {}) or {}).get("bot")
        return origin if is_bot(origin) else None
    if row.get("requester") != KEEPER or row.get("owner") != human_actor(default_human(conn)):
        return None
    # Legacy _escalate wrote no foreign key. Require its exact canonical body and a
    # unique sensitive refusal immediately preceding creation. The actor comes from
    # the trusted refusal record, not a bot name supplied in the title/body.
    origins, candidates = set(), set()
    for refusal in _rows(conn.execute("SELECT actor,detail_json FROM refusals WHERE severity IN ('sensitive','escape') "
                                      "AND ts>=? AND ts<=?", (shift(row["created"], seconds=-5), row["created"]))):
        actor = refusal["actor"]
        if not is_bot(actor):
            continue
        candidates.add(actor)
        detail = (_json(refusal["detail_json"], {}) or {}).get("detail", "")
        body = (f"{actor_id(actor)} tried a write that touches something you care about and it was "
                f"turned down: {_clip(detail)}. Tell me whether that is a mistake to fix or a "
                f"limit to keep.")
        if row.get("body") == body:
            origins.add(actor)
    return next(iter(origins)) if len(origins) == 1 and len(candidates) == 1 else None
