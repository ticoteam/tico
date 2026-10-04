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
   repo/pr. A missing field is `Refused("lint", "send needs cc")`.
5. **`status_set(..., state="active")`** is accepted although `active` is not a `bot_status`
   state: it is how §4 rule 8 says a human clears a quarantine. It sets `bots.state=active`
   and `bot_status.state=idle`.
6. **Rule names.** §4 names `reach`, `cap`, `depth`, `unsolicited`, `one-question`,
   `duplicate`, `consumed`, `lint`. The rest are `identity` (acting as someone else, deciding
   an approval as a bot), `kind` (a bad approval kind or status), `close` (closing a task you
   did not request), `escape` (rule 8's escape class), `quarantined` (a quarantined bot's
   writes) and `not-found`.
7. **Severity is classified here, not passed in.** `classify()` reads the body or payload and
   returns `escape` (a `secrets/` path, another bot's repo path, an external URL in a
   hub-change or access request), `sensitive` (money, an outbound send, access, a human's
   inbox) or `normal`. Refusals remain in the audit; repeated failures open an internal
   review. Generic refusal diagnostics do not create human decision tasks.
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
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from pathlib import Path

from .batch_work import isolated
from clients.manifest import repo_dir
from backend.chat_goals_schema import SCHEMA as CHAT_GOALS_SCHEMA
from backend.repositories_schema import SCHEMA as REPOSITORIES_SCHEMA
from backend.subscriptions_schema import SCHEMA as SUBSCRIPTIONS_SCHEMA

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
BOARD_STATUSES = ACTIVE_STATUSES + ("declined",)               # visible until a person reassigns or closes it
LIVE_STATUSES = ACTIVE_STATUSES + ("done",)                # not closed, not declined
OWNER_STATUSES = ("doing", "waiting", "review", "done", "declined")  # what the owner may set
# `ready` is a fact the hub learns (the PR merged), not a claim a bot makes: movers and the
# keeper set it, an owning bot does not.
# The product lane is retired. Old rows may still say 'product' (so it is
# still a lane to read and filter), but every new or moved task is company work.
TASK_LANES = ("company", "product")
LANE_TEAMS = ("product", "engineering")     # store.py's one-time lane migration (history)
# People who may move any task: change its state, rank, lane, owner, labels, blocked-by or
# parent. Keyed on the roster team in
# registry/people.yaml, so adding a mover is a roster edit. The environment owner always may.
MOVER_TEAMS = ("leadership", "product", "engineering")
MOVER_FIELDS = ("lane", "labels", "blocked_by", "parent_id")   # what only a mover changes
LINK_KINDS = ("pr", "issue", "url", "doc", "worktree")
TITLE_LINT = os.environ.get("TICO_TITLE_LINT", "warn")     # warn | refuse | off
APPROVAL_KINDS = ("send", "spend", "publish", "merge")
APPROVAL_FIELDS = {"send": ("to", "cc", "subject", "body_sha256", "mailbox"),
                   "spend": ("amount", "account", "what"),
                   "publish": ("url", "content_sha256"),
                   "merge": ("repo", "pr")}

CAP_PER_HOUR = 20               # rule 3: messages in one conversation between bots, per hour
MAX_ASK_DEPTH = 3               # rule 3: A asks B asks C; C may not ask on
UNSOLICITED_PER_DAY = int(os.environ.get("TICO_UNSOLICITED_PER_DAY", "10"))   # rule 4: per human, per bot, per UTC day
REVIEW_AT = 3                   # rule 8: refusals in a day that open a review task
QUARANTINE_AT = 10              # rule 8: refusals in a day that quarantine the bot
ESCAPE_QUARANTINE_AT = int(os.environ.get("TICO_ESCAPE_QUARANTINE_AT", "3"))   # rule 8: `escape` refusals in a day that quarantine it until a person clears it
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
                              MEETING_SCHEMA, RECORDING_SOURCES_SCHEMA, MEETING_REVIEW_SCHEMA)

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
    for position, status in enumerate(TASK_STATUSES)
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

# Immutable primary/fallback usage reports; append to preserve existing installations.
USAGE_SEGMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_usage_segments (
  turn_id TEXT NOT NULL REFERENCES turns(id), position INTEGER NOT NULL,
  input_tokens INTEGER, cached_tokens INTEGER, output_tokens INTEGER,
  model TEXT, provider TEXT, est_cost_usd REAL, billing TEXT,
  runtime TEXT, harness TEXT, effort TEXT, profile TEXT,
  PRIMARY KEY(turn_id, position));
"""

MIGRATIONS = [SCHEMA, MEETING_SCHEMA, MEETING_ITEMS_SCHEMA,   # index i takes user_version from i
              MEETING_BRAIN_SCHEMA, MEETING_COMMENTS_SCHEMA,  # to i+1; append, never edit
              GOALS_SCHEMA, RECORDING_SOURCES_SCHEMA, MARKET_SCHEMA,
              REPLY_ANSWERS_ASKS, LISTENING_SCHEMA, KPIS_SCHEMA, USAGE_SCHEMA,
              USAGE_LIMITS_SCHEMA, TAGS_SCHEMA, PIPELINES_SCHEMA, CHAT_GOALS_SCHEMA, REPOSITORIES_SCHEMA, TASK_LINKS_V2_SCHEMA, SUBSCRIPTIONS_SCHEMA,
              STORAGE_SCHEMA, TASK_REVIEW_SCHEMA, MEETING_REVIEW_SCHEMA, NUMBERS_SCHEMA, TASK_PRIVACY_SCHEMA, USAGE_SEGMENTS_SCHEMA]


class Refused(Exception):
    """A write the rules do not allow. `rule` names the rule; `detail` says what to fix."""

    def __init__(self, rule, detail="", severity="normal"):
        super().__init__(f"{rule}: {detail}" if detail else str(rule))
        self.rule = rule
        self.detail = detail
        self.severity = severity


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
def connect(path=None, adopt_legacy=False):
    """Open (and if need be create) hub.db. WAL, foreign keys on, migrations applied.

    Precedence: the `path` argument, then `$HUB_DB`, then `<projects>/runtime/hub.db`.
    `$HUB_DB` is what the tests and a second checkout set; it is honoured everywhere.
    """
    target = path or os.environ.get("HUB_DB") or DEFAULT_DB
    target = Path(target).expanduser()
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        migrate(conn, adopt_legacy=adopt_legacy)
    except Exception:
        conn.close()
        raise
    return conn


class UnknownDatabase(RuntimeError):
    """The file has Tico's tables but no version record: not a database this code made."""


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


def migrate(conn, adopt_legacy=False):
    """Apply every migration the file has not seen, by `PRAGMA user_version`.

    Each migration is one transaction with its version bump, so a failure leaves the file at
    the version it had; every statement is also safe to run twice (IF NOT EXISTS, guarded
    ADD COLUMN), so a file that has the change but an older version simply catches up.
    `adopt_legacy` is for `manage.migrate_legacy`, whose source has no version record."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0 and not adopt_legacy:
        _refuse_foreign(conn)
    for i in range(version, len(MIGRATIONS)):
        conn.execute("BEGIN IMMEDIATE")
        try:
            if i == 23:
                migrate_task_privacy(conn)
            else:
                _apply(conn, MIGRATIONS[i])
            conn.execute(f"PRAGMA user_version={i + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    # Cloud added explicit room scopes after the original local schema. Keep old local
    # databases usable without relying on SQLite's unsupported ADD COLUMN IF NOT EXISTS.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "scope" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN scope TEXT NOT NULL DEFAULT 'direct'")
    if "owner_actor" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN owner_actor TEXT")
    if "room_key" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN room_key TEXT")
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
    if "blocked_by" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN blocked_by TEXT")
    # Next-run tasks (`hub task create --next-run`): `next_run` marks a task that waits for its
    # owner's next run instead of starting one; `carried_by` is the attempt that took it there.
    if "next_run" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN next_run INTEGER NOT NULL DEFAULT 0")
    if "carried_by" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN carried_by TEXT")
    # A task comment its author changed or took back (task_comment_edit, task_comment_delete). Checked on
    # every start rather than numbered, so no migration number collides with another branch's.
    add_column(conn, "messages", "edited_at", "TEXT")
    add_column(conn, "messages", "deleted_at", "TEXT")
    # Quiet notes (`hub note`): a line left for a bot's next run, asking nothing. `carried_by` is
    # the attempt that took it there; a cancelled note never goes.
    conn.execute("CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, from_actor TEXT NOT NULL, "
                 "to_actor TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL, carried_by TEXT, "
                 "cancelled_at TEXT, cancelled_by TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS notes_to_created ON notes(to_actor, created)")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_owner_rank ON tasks(owner, rank)")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_lane_status_rank ON tasks(lane,status,rank,created)")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_active_lane_rank ON tasks("
                 "lane,(rank IS NULL),rank,created,id) WHERE status IN "
                 "('open','doing','waiting','review','ready','declined')")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_finished_lane_time ON tasks("
                 "lane,COALESCE(closed_at,done_at,updated,created) DESC,id DESC) "
                 "WHERE status IN ('done','closed')")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_parent ON tasks(parent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_blocked_by ON tasks(blocked_by)")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_occurrences'").fetchone():
        conn.execute("CREATE INDEX IF NOT EXISTS schedule_occurrences_task ON schedule_occurrences(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS events_task_origin ON events(actor,action,target,ts DESC)")
    conn.execute("CREATE TABLE IF NOT EXISTS task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), "
                 "kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created TEXT NOT NULL, "
                 "pr_sha TEXT, pr_merged_at TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS task_links_task ON task_links(task_id)")
    conn.execute("CREATE TABLE IF NOT EXISTS preferences(actor TEXT NOT NULL, key TEXT NOT NULL, "
                 "value_json TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(actor, key))")
    return len(MIGRATIONS)


def _row(row):
    return dict(row) if row is not None else None


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _one(conn, sql, args=()):
    return _row(conn.execute(sql, args).fetchone())


# ----------------------------------------------------------------------------- audit
# Who is really acting when a person's own identity is used by the Assistant ("assistant"): set for
# the length of one request (backend/app.py request_guard), so every event and task-history row the
# request writes says "via assistant" without each write knowing about it.
VIA = contextvars.ContextVar("hub_via", default="")

PRIVATE_WRITE = contextvars.ContextVar("hub_private_write", default=False)


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
        try:
            return fn(*args, **kwargs)
        except Refused as exc:
            exc.private = PRIVATE_WRITE.get()
            raise
        finally:
            PRIVATE_WRITE.reset(token)
    return guarded


def event(conn, actor, action, target="", detail=None):
    """Append the audit row every write leaves behind."""
    if VIA.get() and (detail is None or isinstance(detail, dict)):
        detail = {**(detail or {}), "via": VIA.get()}
    row = {"id": new_id(), "ts": now(), "actor": str(actor), "action": action,
           "target": str(target or ""), "detail_json": _dump(detail)}
    conn.execute("INSERT INTO events (id, ts, actor, action, target, detail_json) "
                 "VALUES (:id, :ts, :actor, :action, :target, :detail_json)", row)
    return row


def writing_refusal(rule, detail):
    """Only diagnostics produced by human-item formatting, never approval validation."""
    return rule == "lint" and bool(re.match(
        r'^(give it a title|start the title with a verb|internal codes|internal jargon|'
        r'write a concrete human decision|write the ask in the first line|\d+ words outside the quoted draft)', detail))


def refuse(conn, actor, rule, detail="", severity="normal"):
    """Record a refusal, escalate it (rule 8), and raise. Never returns."""
    recorded = "Private task write refused" if PRIVATE_WRITE.get() else detail
    row = {"id": new_id(), "ts": now(), "actor": str(actor), "rule": rule,
           "detail_json": _dump({"detail": recorded}), "severity": severity}
    conn.execute("INSERT INTO refusals (id, ts, actor, rule, detail_json, severity) "
                 "VALUES (:id, :ts, :actor, :rule, :detail_json, :severity)", row)
    event(conn, actor, "refused", rule, {"detail": recorded, "severity": severity})
    if rule != "quarantined":
        try:
            _escalate(conn, actor, rule, recorded, severity, row["ts"])
        except Exception:       # an escalation must never hide the refusal it came from
            pass
    if writing_refusal(rule, detail):
        detail += ". Rewrite the title/body to fix these writing errors and retry. Do not ask the human to waive formatting rules."
    raise Refused(rule, detail, severity)


# ----------------------------------------------------------------------------- lint (rule 7)
LINT_MAX_WORDS = 120
LINT_CODES = re.compile(r"\b(owner|status)\s*:\s*[\w:./-]+", re.I)
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
    return problems


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
    """Whether `text` names another bot's repository folder. `emp-<anything>/` always counts (the older prefix). A
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
            return True
        if conn is not None:
            known = known_repos(conn) if known is None else known
            if name[:-1] in known:
                return True
    return False


SENSITIVE_WORDS = re.compile(
    r"\b(spend|spending|invoice|payment|pay|card|refund|budget|wire|charge|"
    r"send|email|mailbox|inbox|access|credential|credentials|token|password|api[_ -]?key)\b", re.I)


def classify(text, kind=None, to_actor=None, where="item", actor=None, conn=None):
    """The severity rule 8 counts by, read off the thing that was refused.

    `escape`   a `secrets/` path or another bot's repo path. Rule 8 quarantines on this, once
               the bot has done it ESCAPE_QUARANTINE_AT times in a day.
    `sensitive` money, an outbound send, an access change, or a human's inbox.
    `normal`   everything else.

    `where` is "item" (a task or an approval payload) or "message" (a say or an answer). A
    link is never an escape; a secrets path or another bot's repo path is one anywhere. `conn` lets a
    `bot-<slug>/` be recognised as a real bot's folder (see `names_other_repo`).
    """
    body = str(text or "")
    if SECRETS_PATH.search(body) or names_other_repo(body, actor, conn):
        return "escape"
    if kind in ("send", "spend"):
        return "sensitive"
    if is_human(to_actor) and SENSITIVE_WORDS.search(body):
        return "sensitive"
    if SENSITIVE_WORDS.search(body) and kind in ("publish", "merge"):
        return "sensitive"
    return "normal"


def _escalate(conn, actor, rule, detail, severity, ts):
    """Rule 8: count the day's refusals, open review tasks, quarantine when it is bad enough."""
    if not is_bot(actor):
        return
    # A writing correction ("start the title with a verb") is fixed by retrying, not a sign the bot
    # is reaching where it should not: it neither counts nor quarantines. A bot was quarantined by
    # ten refusals, three of them lint on one task title.
    if rule == "lint" and severity != "escape":
        return
    slug = actor_id(actor)
    day = ts[:10]
    # Counted since the day began or the last quarantine lifted, whichever is later.
    lifted = _one(conn, "SELECT max(ts) AS ts FROM events WHERE action='quarantine.lifted' AND target=?",
                  (bot_actor(slug),))
    since = max(day, (lifted or {}).get("ts") or "")
    count = conn.execute("SELECT COUNT(*) FROM refusals WHERE actor=? AND ts>=? AND rule<>'lint'",
                         (actor, since)).fetchone()[0]
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
    escapes = conn.execute("SELECT COUNT(*) FROM refusals WHERE actor=? AND ts>=? AND severity='escape'",
                           (actor, since)).fetchone()[0]
    if (severity == "escape" and escapes >= ESCAPE_QUARANTINE_AT) or count >= QUARANTINE_AT:
        quarantine(conn, slug, f"{rule}: {said}" if severity == "escape"
                   else f"{count} refused writes today")


def task_origin(conn, row):
    """Trusted origin for a keeper review, including pre-metadata records; never parse titles."""
    explicit = _one(conn, "SELECT detail_json FROM events WHERE actor=? AND action='task.origin' "
                         "AND target=? ORDER BY ts DESC LIMIT 1", (KEEPER, row["id"]))
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
COUNT_QUARANTINE = re.compile(r"\d+ refused writes today")


def quarantine_reason(conn, slug):
    row = _one(conn, "SELECT detail_json FROM events WHERE action='quarantine' AND target=? "
                     "ORDER BY ts DESC LIMIT 1", (bot_actor(slug),))
    return ((_json(row["detail_json"], {}) or {}) if row else {}).get("reason") or ""


def quarantine_is_escape(conn, slug):
    """Anything but a refusal-count quarantine is treated as an escape: a person clears it."""
    return not COUNT_QUARANTINE.fullmatch(quarantine_reason(conn, slug))


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


def quarantine(conn, slug, reason):
    """Rule 8's stop: the bot writes nothing more until a human sets it active again."""
    conn.execute("UPDATE bots SET state='quarantined' WHERE slug=?", (slug,))
    event(conn, KEEPER, "quarantine", bot_actor(slug), {"reason": reason})
    try:
        status_set(conn, KEEPER, slug, state="quarantined", focus=reason, reason=reason)
    except Refused:
        pass
    return bot(conn, slug)


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


def _reach(conn, actor, target, allow_planned=False):
    """Rule 2: an active bot, or any human on the roster. Anything else is out of reach.

    `allow_planned` is onboarding's one exception: a bot that has been defined but has not
    started yet may still be handed the task that brings it up, so the backlog is waiting
    when its machine comes online. Nothing runs until the bot is active either way.
    """
    resolved = resolve_actor(conn, target)
    if not resolved:
        refuse(conn, actor, "reach", f"{target} is not a bot or a person on the roster",
               classify(str(target), conn=conn))
    if is_bot(resolved):
        state = (bot(conn, actor_id(resolved)) or {}).get("state")
        if state != "active" and not (allow_planned and state == "planned"):
            refuse(conn, actor, "reach", f"{actor_id(resolved)} is {state}, not active")
    return resolved


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
                      scope=None, owner_actor=None, room_key=None, allow_planned=False):
    """A thread between any set of bots and humans. Reach (rule 2) is checked per participant."""
    _writer(conn, actor)
    if kind not in CONVERSATION_KINDS:
        refuse(conn, actor, "kind", f"a conversation is {'|'.join(CONVERSATION_KINDS)}, not {kind}")
    people = []
    for p in participants or []:
        resolved = p if resolve_actor(conn, p) == actor else _reach(conn, actor, p, allow_planned)
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


def _pair_conversation(conn, actor, other, kind, subject=""):
    """The live conversation of this kind between these two, opened if there is none."""
    want = {actor, other}
    for row in _rows(conn.execute(
            "SELECT * FROM conversations WHERE kind=? AND closed_at IS NULL AND task_id IS NULL "
            "ORDER BY COALESCE(last_message_at, created) DESC LIMIT 200", (kind,))):
        if set(_json(row["participants_json"], []) or []) == want:
            return conversation(conn, row["id"])
    return open_conversation(conn, actor, [actor, other], kind=kind, subject=subject)


def _opener(conn, conversation_id):
    """Who started this conversation: the first message's sender, else whoever opened it."""
    first = _one(conn, "SELECT from_actor FROM messages WHERE conversation_id=? "
                       "ORDER BY created LIMIT 1", (conversation_id,))
    if first:
        return first["from_actor"]
    row = _one(conn, "SELECT actor FROM events WHERE action='conversation.open' AND target=? "
                     "ORDER BY ts LIMIT 1", (conversation_id,))
    return row["actor"] if row else None


# ----------------------------------------------------------------------------- messages
MESSAGE_ESCAPE = ("The message includes a secrets path or another bot’s workspace path. Remove the restricted "
                  "reference and retry; this did not send anything outside the Hub.")


def say(conn, actor, to_actor, body, conversation_id=None, kind="say", refs=None,
        in_reply_to=None, wait_s=None):
    """The one write every message goes through: rules 1, 2, 3, 4, 7 and 8 all live here."""
    _writer(conn, actor)
    if kind not in MESSAGE_KINDS:
        refuse(conn, actor, "kind", f"a message is {'|'.join(MESSAGE_KINDS)}, not {kind}")
    severity = classify(body, to_actor=resolve_actor(conn, to_actor), where="message", actor=actor, conn=conn)
    if severity == "escape" and not (actor == KEEPER and kind == "notice"):
        refuse(conn, actor, "escape", MESSAGE_ESCAPE, "escape")
    target = _reach(conn, actor, to_actor)
    refs = dict(refs or {})

    if is_bot(actor) and actor == target:
        refuse(conn, actor, "self", "a bot cannot send work to itself")

    if conversation_id:
        conv = conversation(conn, conversation_id)
        if not conv:
            refuse(conn, actor, "not-found", f"no conversation {conversation_id}")
    else:
        conv = _pair_conversation(conn, actor, target,
                                  {"ask": "ask", "notice": "notice"}.get(kind, "chat"))

    # rule 3: the loop caps
    if is_bot(actor) and is_bot(target):
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
    if kind == "ask":
        depth = _ask_depth(conn, actor, refs, in_reply_to)
        if depth > MAX_ASK_DEPTH:
            refuse(conn, actor, "depth",
                   f"this ask is at depth {depth}; asks stop at {MAX_ASK_DEPTH}")
        refs["depth"] = depth

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
            problems = lint_human_item(body)
            if problems:
                refuse(conn, actor, "lint", "; ".join(problems), severity)
            refs["unsolicited"] = True

    msg = _write_message(conn, actor, target, body, conv, kind, refs, in_reply_to, wait_s)
    _close_open_asks(conn, actor, target, kind, msg)
    return msg


def _close_open_asks(conn, actor, target, kind, msg):
    """A person's reply answers the named ask, or plain asks on the named task.

    A shared conversation alone does not identify which task a person is answering.
    A direct reply takes precedence over task context so it cannot close sibling asks.
    Structured reviews close only through an explicit answer.
    """
    if kind not in ("say", "answer") or not is_human(actor) or not is_bot(target):
        return
    if msg.get("in_reply_to"):
        scope, scope_id = "m.id", msg["in_reply_to"]
    else:
        scope_id = message_task_id({"refs": _json(msg.get("refs_json"), {}) or msg.get("refs") or {}},
                                   conversation(conn, msg["conversation_id"]))
        if not scope_id:
            return
        scope = MESSAGE_TASK_SQL
    open_asks = [r["id"] for r in _rows(conn.execute(
        "SELECT m.id FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE m.to_actor=? AND m.from_actor=? AND m.kind='ask' "
        "AND json_type(m.refs_json,'$.questions') IS NULL "
        "AND m.answered_by IS NULL AND m.id NOT IN (SELECT in_reply_to FROM messages "
        "WHERE kind='answer' AND in_reply_to IS NOT NULL) "
        f"AND {scope}=?",
        (actor, target, scope_id)))]
    if not open_asks:
        return
    conn.executemany("UPDATE messages SET answered_by=? WHERE id=?",
                     [(msg["id"], mid) for mid in open_asks])
    # Named in the log, because a question closing without anyone typing an answer to it is
    # exactly the thing you want to be able to look up later.
    event(conn, actor, "message.answered_by_reply", msg["id"],
          {"asks": open_asks, "bot": target})


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
# "the runner pulls/syncs" is the Computer only when Tico context follows in the same sentence, even inside code
# or a link ("syncs `AGENT.md`"); an athlete who pulls a hamstring stays one.
RUNNER = re.compile(r"\bthe runner(?=\s+(?:pulls?|syncs?)\b[^.!?\n]*?(?:\b(?:updates?|releases?|changes|instructions|"
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


def _write_message(conn, actor, target, body, conv, kind, refs, in_reply_to, wait_s,
                   expires_at=None, delivered_at=None, read_at=None):
    if actor == "bot:librarian":
        body = librarian_text(body)
    if VIA.get() and is_human(actor):
        refs = {**(refs or {}), "via": VIA.get()}         # the Assistant wrote this for the person
    row = {"id": new_id(), "conversation_id": conv["id"], "from_actor": actor, "to_actor": target,
           "kind": kind, "body": str(body or ""), "refs_json": _dump(refs or {}),
           "in_reply_to": in_reply_to, "created": now(), "delivered_at": delivered_at,
           "read_at": read_at, "expires_at": expires_at, "wait_s": wait_s}
    conn.execute(
        "INSERT INTO messages (id, conversation_id, from_actor, to_actor, kind, body, refs_json, "
        "in_reply_to, created, delivered_at, read_at, expires_at, wait_s) VALUES (:id, "
        ":conversation_id, :from_actor, :to_actor, :kind, :body, :refs_json, :in_reply_to, "
        ":created, :delivered_at, :read_at, :expires_at, :wait_s)", row)
    conn.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (row["created"], conv["id"]))
    event(conn, actor, f"message.{kind}", row["id"], {"to": target, "conversation": conv["id"]})
    return message(conn, row["id"])


def feed(conn, target, body, conv, refs=None):
    """Context the system hands a bot to read: a Slack channel digest (backend/slack_gateway.py).

    Written by the keeper, not by a bot or a person: nobody said it. It is a turn for the bot
    (the queue trigger fires, as for any message to a bot), but it is quoted Slack text, so the
    escape and lint rules a bot's own words go through do not apply; what it quotes was said
    in a channel the bot is in. Rule 2 still holds: the bot must be active.
    """
    resolved = _reach(conn, KEEPER, target)
    return _write_message(conn, KEEPER, resolved, body, conv, "say", dict(refs or {}), None, None)


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
    if classify(body if classify_text is None else classify_text, where="message", actor=actor, conn=conn) == "escape":
        refuse(conn, actor, "escape", "The reply includes a secrets path or another bot’s workspace path. Remove the restricted reference and retry; this did not send anything outside the Hub.",
               "escape")
    refs = {"depth": (asked.get("refs") or {}).get("depth", 1), **(comment_refs or {})}
    if unknown:
        refs["unknown"] = True
    conv = conversation(conn, asked["conversation_id"])
    msg = _write_message(conn, actor, comment_target or asked["from_actor"], body, conv, "answer", refs, message_id, None)
    about = message_task_id(asked, conv)
    if about:                               # an answered question on a task changes the task
        conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), about))
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
        row["steps"] = _rows(conn.execute("SELECT * FROM task_steps WHERE type_id=? ORDER BY position,id", (row["id"],)))
    return row


def type_list(conn):
    rows = _rows(conn.execute("SELECT * FROM task_types ORDER BY id<>?,name", (GENERAL_TYPE,)))
    steps = _rows(conn.execute("SELECT * FROM task_steps ORDER BY position,id"))
    for row in rows:
        row["numbered"] = bool(row.get("numbered"))
        row["steps"] = [step for step in steps if step["type_id"] == row["id"]]
    return rows


# What every bot may do with the tasks of a type, beyond the tasks it is a party to. A team's board
# (a developer board, a support queue) is shared work: the bots that file, build and hand it on need
# to read all of it, where a person's own to-dos stay with the people and bots on them.
#   read: read every task of the type, comment on it and file a subtask under it.
#   work: also change it as its owner or requester could (step, owner, due, body, rank) and add or
#         remove its links. Closing it, a ready step and its labels stay with people.
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
    if not row:
        return False
    row = dict(row)
    if not task_private(conn, row):
        return True
    if "owner" not in row or "requester" not in row:
        row = task(conn, row["id"])
    return bool(row and actor in (row["owner"], row["requester"]))


def _task_private_writer(conn, actor, row):
    # The keeper performs internal maintenance; it is never an authenticated task reader.
    if actor != KEEPER and not task_private_readable(conn, actor, row):
        refuse(conn, actor, "not-found", "Task not found")


def private_tasks_default(conn, actor):
    if not is_bot(actor) or not _has_table(conn, "bot_config"):
        return False
    from .shared_bots import declared, source_of
    config = declared(conn, actor_id(actor))
    if source_of(config):
        config = declared(conn, source_of(config))
    return config.get("private_tasks_default", config.get("template") == "general-counsel") is True



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
        rows = _rows(conn.execute('SELECT id,requester,owner,parent_id FROM tasks'))
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
        thread = {"id": new_id()}
        conn.execute("INSERT INTO conversations(id,kind,subject,participants_json,created,scope,task_id) "
                     "VALUES(?,?,?,?,?,?,?)", (thread["id"], "task", row["title"], _dump(parties), now(), "task", row["id"]))
        if conv:
            for message in _rows(conn.execute("SELECT * FROM messages WHERE conversation_id=?", (conv["id"],))):
                if message_task_id(message, conv) == row["id"]:
                    conn.execute("UPDATE messages SET conversation_id=? WHERE id=?", (thread["id"], message["id"]))
        conn.execute("UPDATE conversations SET task_id=? WHERE id=?", (row["id"], thread["id"]))
        conn.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (thread["id"], row["id"]))
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
    existing = {row["id"]: row for row in (type_get(conn, type_id) or {}).get("steps", [])}
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
            refuse(conn, actor, "kind", f"A step status is {'|'.join(TASK_STATUSES)}")
        names.add(name); ids.add(ident)
        clean.append({"id": ident, "type_id": type_id, "name": name,
                      "position": raw.get("position") if raw.get("position") is not None else position,
                      "status": status})
    for row in clean:
        if (row["id"] in existing and row["status"] != existing[row["id"]]["status"]
                and _one(conn, "SELECT 1 FROM tasks WHERE step_id=? LIMIT 1", (row["id"],))):
            refuse(conn, actor, "in-use", "Move tasks off this step before changing its status, or create a new step")
    removed = set(existing) - ids
    for ident in removed:
        if _one(conn, "SELECT id FROM tasks WHERE step_id=? LIMIT 1", (ident,)):
            refuse(conn, actor, "in-use", "Move tasks off this step before deleting it")
    for ident in removed:
        conn.execute("DELETE FROM task_steps WHERE id=?", (ident,))
    # Temporary names let retained steps swap names without dropping referenced rows.
    for ident in ids & set(existing):
        conn.execute("UPDATE task_steps SET name=? WHERE id=?", (new_id(), ident))
    for row in clean:
        conn.execute("INSERT INTO task_steps(id,type_id,name,position,status) "
                     "VALUES(:id,:type_id,:name,:position,:status) ON CONFLICT(id) DO UPDATE SET "
                     "name=excluded.name,position=excluded.position,status=excluded.status", row)


def type_create(conn, actor, name, steps=(), mover=None, bots=None, numbered=False):
    _type_writer(conn, actor, mover)
    name = _type_name(conn, actor, name)
    bots = _type_bots(conn, actor, bots)
    ident, ts = new_id(), now()
    conn.execute("INSERT INTO task_types(id,name,bots,numbered,created,updated) VALUES(?,?,?,?,?,?)",
                 (ident, name, bots, int(bool(numbered)), ts, ts))
    _type_steps(conn, actor, ident, steps)
    row = type_get(conn, ident)
    event(conn, actor, "task_type.create", ident, row)
    return row


def type_update(conn, actor, type_id, name=None, steps=None, mover=None, bots=None, numbered=None):
    _type_writer(conn, actor, mover)
    row = type_get(conn, type_id)
    if not row:
        refuse(conn, actor, "not-found", "No such task type")
    if row["id"] == GENERAL_TYPE:
        refuse(conn, actor, "built-in", "General keeps the standard statuses")
    if name is not None:
        name = _type_name(conn, actor, name, row["id"])
        conn.execute("UPDATE task_types SET name=? WHERE id=?", (name, row["id"]))
    if numbered is not None:
        conn.execute("UPDATE task_types SET numbered=? WHERE id=?", (int(bool(numbered)), row["id"]))
    if steps is not None:
        _type_steps(conn, actor, row["id"], steps)
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


def _task_state(conn, actor, row, status=None, type=None, step=None):
    """Resolve the step before checking permissions on its effective status."""
    typ = type_get(conn, type if type is not None else row.get("type_id") or GENERAL_TYPE)
    if not typ:
        refuse(conn, actor, "not-found", "No such task type")
    effective = status if status is not None else row.get("status") or "open"
    current = next((s for s in typ["steps"] if s["id"] == row.get("step_id")), None)
    if step is not None:
        chosen = next((s for s in typ["steps"] if s["id"] == step), None)
        chosen = chosen or next((s for s in typ["steps"] if s["name"] == step), None)
        if not chosen and step != "":
            refuse(conn, actor, "not-found", "No such step in this task type")
        if chosen:
            effective = chosen["status"]
    elif current and current["status"] == effective:
        chosen = current
    else:
        chosen = next((s for s in typ["steps"] if s["status"] == effective), None)
    if effective not in TASK_STATUSES:
        refuse(conn, actor, "kind", f"A status is {'|'.join(TASK_STATUSES)}, not {effective}")
    return typ["id"], chosen["id"] if chosen else None, effective


def _set_status(conn, actor, row, status=None, type=None, step=None, note=""):
    """Every task status write keeps its type and step consistent, including old runners. A task
    that enters a step joins its end; one that lands on a numbered type without a number gets one."""
    type_id, step_id, effective = _task_state(conn, actor, row, status, type, step)
    ts = now()
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
    return effective


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
    return number


def _next_number(conn, actor, task_id, type_id, note=""):
    """The team's next number (the highest yet, plus one) for a task without one on a numbered type."""
    numbered = _one(conn, "SELECT 1 FROM task_types WHERE id=? AND numbered=1", (type_id,))
    if numbered and conn.execute("UPDATE tasks SET number=(SELECT COALESCE(MAX(number), 0) + 1 FROM tasks) "
                                 "WHERE id=? AND number IS NULL "
                                 "AND (SELECT COALESCE(MAX(number), 0) FROM tasks)<999999999", (task_id,)).rowcount:
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


def hydrate_task_tags(conn, rows):
    """Read tag display data for a page in one query, including rows from older readers."""
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        row["tags"] = []
    if by_id:
        marks = ",".join("?" * len(by_id))
        for row in conn.execute("SELECT tags.*,task_tags.task_id FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                                f"WHERE task_tags.task_id IN ({marks}) ORDER BY task_tags.rowid", tuple(by_id)):
            value = _tag_view(row)
            by_id[value.pop("task_id")]["tags"].append(value)


def _set_task_tags(conn, actor, task_id, labels, note=""):
    keys, resolved = _labels(labels), []
    for key in keys:
        row = tag_by_key(conn, key)
        if row and row["is_template"]:
            refuse(conn, actor, "kind", f"{key} is a template; make an instance to put on a task")
        resolved.append((key, row))
    old = task_labels(task(conn, task_id))
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


def _lane_for(conn, lane, owner, actor):
    """The pipeline a new task follows: always company (the product lane is retired). A lane
    is still accepted for compatibility and checked, but not stored."""
    if lane and lane not in TASK_LANES:
        refuse(conn, actor, "kind", f"a lane is {'|'.join(TASK_LANES)}, not {lane}")
    return "company"


def _has_table(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _queue_end(conn, owner, top=False):
    """The rank that puts a task at the bottom (or the top) of this owner's queue."""
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    lo, hi = conn.execute(f"SELECT MIN(rank), MAX(rank) FROM tasks WHERE owner=? AND status IN ({marks})",
                          (owner, *ACTIVE_STATUSES)).fetchone()
    if top:
        return (lo if lo is not None else 1.0) - 1.0
    return (hi if hi is not None else 0.0) + 1.0


def _unblock(conn, done_task):
    """A finished task frees whatever waited on it; a bot owner hears about it."""
    for row in _rows(conn.execute("SELECT * FROM tasks WHERE blocked_by=?", (done_task["id"],))):
        if task_private(conn, done_task) and (not task_private(conn, row) or not all(
                task_private_readable(conn, party, done_task) for party in (row['owner'], row['requester']))):
            continue                  # do not copy private completion into a wider task
        conn.execute("UPDATE tasks SET blocked_by=NULL, updated=?, version=version+1 WHERE id=?",
                     (now(), row["id"]))
        _task_event(conn, row["id"], KEEPER, "blocked_by", done_task["id"], None,
                    f"unblocked: {done_task['title']}")
        if row["status"] in ACTIVE_STATUSES:
            _wake(conn, row, row["owner"], f"Unblocked: {row['title']}\n{done_task['title']} is finished.")


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


@private_task_write
def task_link(conn, actor, task_id, url, title=None, kind=None, mover=None):
    """Attach a link to a task. A pull request link is what moves a product-lane task."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_link_allowed(conn, actor, row, mover, kind or link_kind(str(url or "")))
    url = str(url or "").strip()
    if not re.match(r"^https?://\S+$", url):
        refuse(conn, actor, "kind", "a link is an http(s) URL")
    kind = kind or link_kind(url)
    match = PR_URL.match(url)
    app = _one(conn, "SELECT org FROM github_app LIMIT 1") if _has_table(conn, "github_app") else None
    if kind == "pr" and match and app and match.group(1).lower() != app["org"].lower():
        kind = "url"
    if kind not in LINK_KINDS:
        refuse(conn, actor, "kind", f"a link is {'|'.join(LINK_KINDS)}, not {kind}")
    have = _one(conn, "SELECT * FROM task_links WHERE task_id=? AND url=?", (task_id, url))
    if have:
        return have
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
        link = _one(conn, "SELECT * FROM task_links WHERE id=?", (link["id"],))
    _task_event(conn, task_id, actor, "link", None, url, title)
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
    event(conn, actor, "task.link", task_id, {"url": url, "kind": kind})
    return link


def _task_link_allowed(conn, actor, row, mover=None, kind=None):
    _task_private_writer(conn, actor, row)
    if is_human(actor) and kind != "worktree":
        return
    if mover is None:
        mover = actor == KEEPER or is_human(actor) and can_move(conn, actor)
    delegated = _one(conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires>?",
                     (row["id"], actor, now()))
    if (not mover and actor not in (row["owner"], row["requester"]) and not delegated
            and not task_ancestor_party(conn, actor, row) and not type_bot_works(conn, actor, row)):
        refuse(conn, actor, "identity", "This task is not yours to change")


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
        conn.execute('UPDATE task_links SET detail_json=?,updated=? WHERE id=?', (json.dumps(detail), now(), link_id))
        conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
        _task_event(conn, task_id, actor, 'link', have['url'], None, 'cleanup requested')
        event(conn, actor, 'task.unlink', task_id, {'url': have['url'], 'cleanup': True})
        return have
    conn.execute("DELETE FROM task_links WHERE id=?", (link_id,))
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
    _task_event(conn, task_id, actor, "link", have["url"], None, "removed")
    event(conn, actor, "task.unlink", task_id, {"url": have["url"]})
    if have["kind"] == "pr":
        from .github import _pr_status
        _pr_status(conn, row, "Pull request link removed.")
    return have


def task_ancestor_party(conn, actor, row):
    if not task_private_readable(conn, actor, row):
        return False
    seen = {row["id"]}
    parent_id = row.get("parent_id")
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = task(conn, parent_id)
        if not parent:
            break
        if actor in (parent["owner"], parent["requester"]):
            return True
        parent_id = parent.get("parent_id")
    return False


def _task_parent(conn, actor, task_id, parent_id):
    parent = task(conn, parent_id)
    if not parent:
        refuse(conn, actor, "not-found", f"no task {parent_id} to file this under")
    _task_private_writer(conn, actor, parent)
    if (is_bot(actor) and actor not in (parent["owner"], parent["requester"])
            and not type_bot_reads(conn, actor, parent) and not task_ancestor_party(conn, actor, parent)
            and not _one(conn, "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires>?",
                         (parent["id"], actor, now()))):
        refuse(conn, actor, "identity", "This task type does not allow unrelated bots to create subtasks")
    seen = {task_id} if task_id else set()
    cursor = parent
    while cursor:
        if cursor["id"] in seen:
            refuse(conn, actor, "kind", "a task cannot become its own ancestor")
        seen.add(cursor["id"])
        cursor = task(conn, cursor["parent_id"]) if cursor.get("parent_id") else None
    return parent


def descendants(conn, task_id):
    return _rows(conn.execute("WITH RECURSIVE tree(id) AS ("
        "SELECT id FROM tasks WHERE parent_id=? UNION SELECT t.id FROM tasks t JOIN tree ON t.parent_id=tree.id) "
        "SELECT t.* FROM tasks t JOIN tree ON t.id=tree.id ORDER BY t.created,t.id", (task_id,)))


def children_summaries(conn, task_ids, visible_sql="1"):
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    rows = conn.execute("WITH RECURSIVE visible AS (SELECT * FROM tasks WHERE " + visible_sql + "), "
        "tree(root,id,live,direct) AS ("
        f"SELECT parent_id,id,1,1 FROM visible WHERE parent_id IN ({marks}) UNION "
        "SELECT tree.root,t.id,tree.live AND parent.status NOT IN ('done','closed','declined'),0 "
        "FROM visible t JOIN tree ON t.parent_id=tree.id JOIN visible parent ON parent.id=tree.id), "
        "prs AS (SELECT task_id,count(*) total,sum(state IN ('merged','shipped')) merged "
        "FROM task_links WHERE kind='pr' AND coalesce(state,'open')!='closed' "
        "AND task_id IN (SELECT id FROM tree) GROUP BY task_id) "
        "SELECT tree.root,count(*) total,sum(t.status IN ('done','closed','declined')) done,"
        "sum(tree.live AND t.status NOT IN ('done','closed','declined')) open,"
        "sum(tree.direct) direct_total,sum(tree.direct AND t.status IN ('done','closed','declined')) direct_done,"
        "sum(coalesce(prs.total,0)) prs_total,sum(coalesce(prs.merged,0)) prs_merged "
        "FROM tree JOIN visible t ON t.id=tree.id LEFT JOIN prs ON prs.task_id=t.id GROUP BY tree.root", task_ids)
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
    nodes = {r["id"]: {k: r[k] for k in ("id", "title", "status", "owner")} for r in rows}
    links = {}
    if nodes:
        marks = ",".join("?" * len(nodes))
        for link in _rows(conn.execute(f"SELECT * FROM task_links WHERE task_id IN ({marks})", list(nodes))):
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


def _parent_finished(conn, row, previous, actor=None):
    if task_private(conn, row):
        return
    terminal = ("done", "closed", "declined")
    if previous in terminal or row["status"] not in terminal or not row.get("parent_id"):
        return
    seen = {row["id"]}
    parent_id = row["parent_id"]
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = task(conn, parent_id)
        if not parent or children_summary(conn, parent_id)["open"]:
            break
        if parent["status"] not in terminal and parent["owner"] != actor:
            _wake(conn, parent, parent["owner"], "All subtasks done")
        parent_id = parent.get("parent_id")


def task_links(conn, task_id):
    return _rows(conn.execute("SELECT * FROM task_links WHERE task_id=? ORDER BY created", (task_id,)))


def task_ask_recipient(conn, actor, row, ask):
    target = ask.get("who") or (row["owner"] if actor == row["requester"] else row["requester"])
    target = resolve_actor(conn, target)
    if task_private(conn, row) and target not in (row["owner"], row["requester"]):
        refuse(conn, actor, "private", "Only the requester and assignee can receive private task questions")
    return target


@private_task_write
def task_comment(conn, actor, task_id, text, wake=True, *, ask=None, extra_refs=None, answer_to=None, answer_text=None):
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
    others = [a for a in dict.fromkeys([row["owner"], row["requester"], task_origin(conn, row)])
              if a and a not in (actor, KEEPER) and task_private_readable(conn, a, row)]
    target = next((a for a in others if is_bot(a)), None) or (others[0] if others else None)
    refs = {"task": task_id, "comment": True, **(extra_refs or {})}
    if not wake:
        refs["quiet"] = True
    conv = conversation(conn, row["conversation_id"])
    if not conv:
        refuse(conn, actor, "not-found", f"task {task_id} has no conversation")
    if ask:
        target = task_ask_recipient(conn, actor, row, ask)
        refs.update(ask)
        refs["who"] = ask.get("who")
    if answer_to:
        msg = answer(conn, actor, answer_to, text, comment_refs=refs, comment_target=target or actor, classify_text=answer_text)
    elif ask:
        msg = say(conn, actor, target, text, kind="ask", conversation_id=conv["id"], refs=refs)
    elif target is None:
        # the commenter is the only party (they asked themselves): on the record, wakes nobody
        msg = _write_message(conn, actor, actor, text, conv, "say", refs, None, None)
    else:
        msg = say(conn, actor, target, text, conversation_id=conv["id"], refs=refs)
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))
    if wake and is_bot(target) and is_human(actor):
        conn.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires) VALUES(?,?,?,?,?)",
                     (task_id, target, actor, msg["id"], shift(now(), hours=24)))
    return msg


def task_comments(conn, task_id, *, actor=None):
    """Every comment and ask on this task, oldest first, with who wrote it. A deleted comment is not
    listed; an edited one carries `edited_at`."""
    from . import task_privacy as privacy
    row = task(conn, task_id)
    if not row or not row.get("conversation_id"):
        return []
    conv = conversation(conn, row["conversation_id"])
    out = []
    for m in _rows(conn.execute("SELECT * FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                                "ORDER BY created", (row["conversation_id"],))):
        m["refs"] = _json(m.get("refs_json"), {}) or {}
        if message_task_id(m, conv) == task_id and m.get("kind") in ("say", "ask", "answer"):
            if actor is not None and not privacy.message_readable(conn, actor, m):
                continue
            if m["kind"] == "ask" and m["refs"].get("questions"):
                m["ask"] = {**{k: m["refs"].get(k) for k in ("questions", "who")}, "by": m["from_actor"]}
                m["answers"] = review_answers(conn, m["id"], actor=actor)
            if m["refs"].get("answer"):
                m["answer"] = m["refs"]["answer"]
            out.append(m)
    return out


def review_answers(conn, message_id, *, actor=None):
    from . import task_privacy as privacy
    out = []
    for row in conn.execute(
            "SELECT * FROM messages WHERE deleted_at IS NULL AND ((kind='answer' AND in_reply_to=?) "
            "OR id=(SELECT answered_by FROM messages WHERE id=?)) ORDER BY created,rowid",
            (message_id, message_id)):
        if actor is not None and not privacy.message_readable(conn, actor, row):
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
        "AND m.answered_by IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM messages a WHERE a.in_reply_to=m.id AND a.kind='answer') ORDER BY m.rowid DESC",
        (conv, task_row["id"])))
    if actor is not None:
        asks = [ask for ask in asks if privacy.message_readable(conn, actor, ask)]
    for ask in asks:
        ask["refs"] = _json(ask.get("refs_json"), {}) or {}
    return asks


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
            and not (set(refs) - {"task", "comment", "quiet", "via"}))



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
    for row in conn.execute("SELECT rowid,response_json FROM idempotency WHERE instr(response_json,?)>0",
                            (msg["id"],)).fetchall():
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
                                    "a notice or a chat message")
    if msg["from_actor"] != actor:
        refuse(conn, actor, "identity", f"{actor_id(msg['from_actor'])} wrote this comment; only they change it")
    return msg


@private_task_write
def task_comment_edit(conn, actor, task_id, message_id, text):
    """The author changes a comment's text. The new text gets the checks a new comment's text gets
    (`task_comment`, `say`); rule 7's lint is for unsolicited items, and a comment is tagged with its
    task, so it never applies. Nothing is sent and nobody wakes: whoever reads the task next reads the
    new text."""
    msg = _own_comment(conn, actor, task_id, message_id)
    text = str(text or "").strip()
    if not text:
        refuse(conn, actor, "lint", "write the comment")
    if classify(text, to_actor=msg["to_actor"], where="message", actor=actor, conn=conn) == "escape":
        refuse(conn, actor, "escape", MESSAGE_ESCAPE, "escape")
    if actor == "bot:librarian":
        text = librarian_text(text)
    if text == msg["body"]:
        return msg                  # the same words are no edit: no mark, no history line
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
    msg = _own_comment(conn, actor, task_id, message_id)
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
    event(conn, actor, "message.deleted", message_id,
          {"task": task_id, "reopened": reopened, "cancelled_run": cancelled})
    deleted = message(conn, message_id, include_deleted=True)
    _withdraw_queued_comment_copies(conn, message_id)
    _refresh_comment_replies(conn, deleted)
    return deleted


@private_task_write
def task_create(conn, actor, title, body, owner, due=None, parent_id=None, *, deduplicate=True,
                allow_planned=False, conversation_id=None, lane=None, labels=None, top=False, lint=True,
                goal_id=None, next_run=False, type=None, step=None, number=None, mover=None, private=None,
                requester_actor=None):
    """Rule 5. Anyone may open a task for any active owner; a human owner's General task is
    linted (rule 7).

    `next_run` files it for the bot's next run instead of starting one: the notice is written
    quietly, and the next run the bot has for any reason carries the task in its prompt
    (NEXT_RUN_WAITING_SQL, execution.claim). A person has no runs, so only a bot may get one.

    `conversation_id` attaches the task to an existing chat room. A room is not claimed as
    this task's private thread: many tasks share one room, and messages carry `refs.task`.
    `lane` picks the pipeline (company or product; default by the owner's team); the task
    joins the bottom of the owner's queue and of its step unless `top`.
    `number` keeps an imported task's number: a mover's (`mover`, as in `task_update`), and free.
    """
    _writer(conn, actor)
    from .shared_bots import route
    owner = route(conn, actor, owner)
    target = _reach(conn, actor, owner, allow_planned=allow_planned)
    parent = _task_parent(conn, actor, None, parent_id) if parent_id else None
    requester = parent["requester"] if parent and is_human(actor) and is_human(parent["requester"]) else actor
    if private is False and (not is_human(actor) or VIA.get()):
        private = None              # a bot cannot override a sensitive default to publish
    private = bool((private if private is not None else
                    private_tasks_default(conn, actor) or private_tasks_default(conn, target))
                   or parent and task_private(conn, parent))
    if private:
        requester = actor
    if requester_actor is not None:
        # Only the server supplies a verified human origin for a bot's own work.
        if not is_human(requester_actor) or target != actor or not human(conn, requester_actor):
            refuse(conn, actor, "identity", "Human requester attribution requires the bot's own verified work")
        requester = requester_actor
    title = str(title or "").strip()
    body = str(body or "")
    severity = classify(f"{title}\n{body}", to_actor=target, actor=actor, conn=conn)
    # rule 8 is about bots reaching outside the hub; a person's notes are not an escape
    if severity == "escape" and is_bot(actor):
        refuse(conn, actor, "escape", f"the task reaches outside the hub: {_clip(body, 80)}", severity)
    if next_run and not is_bot(target):
        refuse(conn, actor, "next-run", "only a bot has a next run; file an ordinary task for a person")
    lane = _lane_for(conn, lane, target, actor)
    general = _task_state(conn, actor, {}, type=type, step=step)[0] == GENERAL_TYPE
    # Rule 7 shapes an ask to a person (a verb, the ask first). A task on a custom type is a
    # ticket on that type's board, written the way the board writes them, not an ask.
    if is_human(target) and lint and general:
        problems = lint_human_item(body, title=title)
        if problems:
            refuse(conn, actor, "lint", "; ".join(problems), severity)
    elif not title:
        refuse(conn, actor, "lint", "give it a title that says what you are asking for")
    # A ticket keeps the board's own references ("#18945", "(B/F)") in its title.
    plain = lint_title(title) if is_bot(actor) and TITLE_LINT != "off" and general else []
    if plain and TITLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(plain), severity)
    labels = _labels(labels)
    dup = _one(conn, "SELECT id FROM tasks WHERE requester=? AND owner=? AND title=? "
                     "AND coalesce(parent_id,'')=? "
                     f"AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
               (requester, target, title, parent_id or "", *ACTIVE_STATUSES))
    if dup and task_private_readable(conn, actor, task(conn, dup["id"])) and (deduplicate or actor != KEEPER):
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
    if conversation_id and not private:
        conv = conversation(conn, conversation_id)
        if not conv:
            refuse(conn, actor, "not-found", f"no conversation {conversation_id}")
    else:
        conv = open_conversation(conn, actor, list(dict.fromkeys([actor, requester, target])), kind="task", subject=title, scope="task",
                                 allow_planned=allow_planned)
        dedicated = True
    row = {"id": new_id(), "title": title, "body": body, "requester": requester, "owner": target,
           "status": "open", "due": due, "parent_id": parent_id, "conversation_id": conv["id"],
           "created": ts, "updated": ts, "done_at": None, "closed_at": None, "closed_by": None,
           "note": "", "lane": lane, "rank": _queue_end(conn, target, top),
           "goal_id": goal_id or None, "next_run": 1 if next_run else 0, "number": number, "private": int(private)}
    conn.execute("INSERT INTO tasks (id, title, body, requester, owner, due, parent_id, "
                 "conversation_id, created, updated, done_at, closed_at, closed_by, note, lane, rank, "
                 "goal_id, next_run, number, private) VALUES "
                 "(:id, :title, :body, :requester, :owner, :due, :parent_id, "
                 ":conversation_id, :created, :updated, :done_at, :closed_at, :closed_by, :note, "
                 ":lane, :rank, :goal_id, :next_run, :number, :private)", row)
    _set_task_tags(conn, actor, row["id"], labels)
    if dedicated:
        conn.execute("UPDATE conversations SET task_id=? WHERE id=?", (row["id"], conv["id"]))
    if number is not None:
        _task_event(conn, row["id"], actor, "number", None, number, "")
    _set_status(conn, actor, {**row, "status": None}, status="open", type=type)
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
    # Give a bot the rest of this relationship's active work with the new task. The runner puts
    # current-message refs in its prompt, so the bot can reuse or reconcile overlapping work
    # without making the human-facing notice noisy or merging distinct deliverables automatically.
    related = []
    if is_bot(target):
        reconcile_statuses = ACTIVE_STATUSES + ("declined",)
        marks = ",".join("?" * len(reconcile_statuses))
        related = _rows(conn.execute(
            "SELECT id,title,status,owner,requester,rank FROM tasks WHERE id<>? "
            f"AND status IN ({marks}) AND ((owner=? AND requester=?) OR (owner=? AND requester=?)) "
            "ORDER BY (rank IS NULL),rank,created LIMIT 8",
            (row["id"], *reconcile_statuses, target, actor, actor, target)))
    reconcile = {"with_actor": actor, "active": [
        {k: item.get(k) for k in ("id", "title", "status", "owner", "requester")} for item in related
    ], "guidance": "Check for overlap. Reuse shared work; keep distinct decisions and deliverables separate."} if related else None
    _wake(conn, task(conn, row["id"]), target, f"New task from {actor}: {title}",
          {"reconcile": reconcile} if reconcile else None, quiet_bots=next_run, quiet=next_run)
    _recount(conn, target)
    _recount(conn, actor)
    return task(conn, row["id"])


def _retitle(conn, actor, row, title, owner, type_id, parent_id=None):
    """A new title gets the checks a new task's title would, against the task as the update
    leaves it: never empty; on a General task for a person, rule 7's title half; no live task
    between the same requester and owner, under the same parent, already called that (task_create's
    duplicate rule); for a bot on General, the plain-English check, whose warnings are returned to
    be recorded once the title lands."""
    target = resolve_actor(conn, owner) if owner is not None else row["owner"]
    parent = row.get("parent_id") if parent_id is None else (str(parent_id).strip() or None)
    general = type_id == GENERAL_TYPE
    if not title:
        refuse(conn, actor, "lint", "give it a title that says what you are asking for")
    if is_human(target) and general:
        problems = lint_human_title(title)
        if problems:
            refuse(conn, actor, "lint", "; ".join(problems))
    dup = _one(conn, "SELECT id FROM tasks WHERE id<>? AND requester=? AND owner=? AND title=? "
                     "AND coalesce(parent_id,'')=? "
                     f"AND status IN ({','.join('?' * len(LIVE_STATUSES))})",
               (row["id"], row["requester"], target, title, parent or "", *LIVE_STATUSES))
    if dup and task_private_readable(conn, actor, task(conn, dup["id"])):
        detail = ("An existing task already asks for this work" if VIA.get() and task_private(conn, task(conn, dup["id"]))
                  else f"{dup['id']} already asks {actor_id(target)} for this")
        refuse(conn, actor, "duplicate", detail)
    plain = lint_title(title) if is_bot(actor) and TITLE_LINT != "off" and general else []
    if plain and TITLE_LINT == "refuse":
        refuse(conn, actor, "lint", "; ".join(plain))
    return plain


@private_task_write
def task_update(conn, actor, task_id, status=None, note=None, owner=None, due=None, body=None,
                lane=None, labels=None, blocked_by=None, rank=None, parent_id=None, mover=None, goal_id=None, quiet=False, type=None, step=None,
                step_rank=None, number=None, title=None, private=None):
    """Rule 5. The owner may set doing|waiting|review|done|declined and a note; it may not close.

    `lane`, `labels` and `blocked_by` are a mover's to change (`mover` says whether
    this actor is one; the keeper always is). The owner/requester may re-parent under a task
    they participate in. Pass `blocked_by=""` or `parent_id=""` to clear.
    `rank` is the position in the owner's queue: a participant may rank its own tasks.
    `step_rank` is its place within its step, lower first, set the same way.
    `goal_id` names the goal the task serves; "" takes it off (backend/goals.py).
    `number` gives a task that has none its number (a mover's, as on create); it never changes.
    `title` renames it, checked as a new task's title would be (`_retitle`).
    """
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_private_writer(conn, actor, row)
    if private is not None and bool(private) != task_private(conn, row):
        if actor not in (row["owner"], row["requester"]):
            refuse(conn, actor, "private", "Only the requester or assignee can change task privacy")
        if not private and (actor != row["requester"] or not is_human(actor) or VIA.get()):
            refuse(conn, actor, "private", "Only the human requester can publish a private task")
        if not private and row.get("parent_id") and parent_id != "":
            parent = task(conn, row["parent_id"])
            if parent and task_private(conn, parent):
                refuse(conn, actor, "private", "Detach this task from its private parent before publishing")
    mine = actor in (row["owner"], row["requester"])
    delegated = _one(conn, "SELECT message_id FROM task_delegations WHERE task_id=? AND delegate=? AND expires>? LIMIT 1",
                     (task_id, actor, now()))
    mine = mine or bool(delegated) or task_ancestor_party(conn, actor, row) or type_bot_works(conn, actor, row)
    if mover is None:
        mover = actor == KEEPER or is_human(actor) and can_move(conn, actor)
    if not mine and not mover and actor != KEEPER:
        refuse(conn, actor, "identity", f"{task_id} is not yours to change")
    state_change = status is not None or type is not None or step is not None
    type_id = row.get("type_id") or GENERAL_TYPE
    if state_change:
        type_id, _, effective = _task_state(conn, actor, row, status, type, step)
        if status is not None or step is not None:
            status = effective
        # A move between steps with the same status changes only the pipeline label.
        if step is not None and effective == row["status"]:
            status = None
    closing_step = status == "closed" and step is not None
    if closing_step:
        _task_close_allowed(conn, actor, row, note)
    if status is not None and not closing_step:
        if status not in TASK_STATUSES:
            refuse(conn, actor, "kind", f"a status is {'|'.join(TASK_STATUSES)}, not {status}")
        if status == "done" and not is_human(actor) and children_summary(conn, task_id)["open"]:
            refuse(conn, actor, "children", "Finish the open subtasks before marking this task done")
        if status == "closed":
            refuse(conn, actor, "close", "close a task with task_close; the requester closes it")
        if actor == row["owner"] and not is_human(actor) and status not in OWNER_STATUSES:
            refuse(conn, actor, "close",
                   f"the owner may set {'|'.join(OWNER_STATUSES)}, not {status}")
        if status == "ready" and is_bot(actor):
            refuse(conn, actor, "close", "ready to ship is set when the pull request merges, not by the bot")
        if (status == "done" and is_human(actor) and is_human(row["owner"])
                and is_bot(row["requester"]) and not str(note or "").strip()):
            refuse(conn, actor, "lint", "Tell the requesting bot what you decided or completed in a note")
        # A bot parking a task it asked itself for has nobody to answer it unless it has filed
        # something to wait on first. Several stranded tasks were this.
        # Validate the requested dependency, not the old one: callers can park work and
        # attach its blocker atomically. Normal dependency/access checks still run below.
        waiting_row = {**row, "blocked_by": str(blocked_by or "").strip() or None} if blocked_by is not None else row
        if (status == "waiting" and row["status"] != "waiting" and is_bot(row["owner"])
                and row["owner"] == row["requester"] and not waiting_on(conn, waiting_row)):
            refuse(conn, actor, "lint",
                   "You asked for this task yourself, so nobody will answer it: file the child task, "
                   "blocker or approval you are waiting on first, or keep working")
    # The owner says what its own task waits on: the waiting rule above tells a bot to "file the
    # blocker you are waiting on first", and a bot was refused for doing exactly
    # that. Lane, labels and the parent stay with the movers.
    own_blocker = blocked_by is not None and actor == row["owner"]
    if own_blocker and blocked_by == task_id:
        refuse(conn, actor, "blocked_by", "a task cannot wait on itself")
    if number is not None and number == row.get("number"):
        number = None               # sent back unchanged
    wanted = {k: v for k, v in (("lane", lane), ("labels", labels),
                                ("blocked_by", None if own_blocker else blocked_by),
                                ("parent_id", None if actor in (row["owner"], row["requester"]) else parent_id), ("number", number)) if v is not None}
    if wanted and not mover:
        refuse(conn, actor, "identity",
               f"{', '.join(wanted)} on a task {'is' if len(wanted) == 1 else 'are'} changed by a person on "
               f"the {', '.join(MOVER_TEAMS)} teams, not by {actor_id(actor)}")
    if lane is not None:
        lane = _lane_for(conn, lane, row["owner"], actor)    # a task moves only onto company
    if title is not None:
        title = str(title).strip()
    if title == row["title"]:
        title = None                # sent back unchanged: nothing to check or record
    plain = _retitle(conn, actor, row, title, owner, type_id, parent_id) if title is not None else []
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
    for field, value in (("title", title), ("note", note), ("due", due), ("body", body), ("lane", lane)):
        if value is None:
            continue
        severity = classify(str(value), actor=actor, conn=conn) if field in ("title", "body") and is_bot(actor) else "normal"
        if severity == "escape":
            refuse(conn, actor, "escape", f"the task {field} reaches outside the hub: {_clip(value, 80)}", severity)
        sets.append(f"{field}=:{field}")
        args[field] = value
        _task_event(conn, task_id, actor, field, row.get(field), value, note or "")
    if labels is not None:
        _set_task_tags(conn, actor, task_id, labels, note or "")
        sets.append("updated=:updated")
    if blocked_by is not None:
        blocker = str(blocked_by or "").strip() or None
        if blocker:
            if blocker == task_id:
                refuse(conn, actor, "kind", "a task cannot block itself")
            if not task(conn, blocker):
                refuse(conn, actor, "not-found", f"no task {blocker} to block on")
            block = task(conn, blocker)
            _task_private_writer(conn, actor, block)
            if task_private(conn, block) and (not (args.get('private', row.get('private'))) or not all(
                    task_private_readable(conn, party, block)
                    for party in (args.get('owner', row['owner']), row['requester']))):
                refuse(conn, actor, 'private', 'A private dependency requires the same private audience')
        sets.append("blocked_by=:blocked_by")
        args["blocked_by"] = blocker
        _task_event(conn, task_id, actor, "blocked_by", row.get("blocked_by"), blocker, note or "")
    if parent_id is not None:
        parent = str(parent_id or "").strip() or None
        if row.get("parent_id") and parent != row["parent_id"] and not mover:
            old_parent = task(conn, row["parent_id"])
            if old_parent and actor not in (old_parent["owner"], old_parent["requester"]):
                refuse(conn, actor, "identity", "Only the parent's owner, requester or a mover can move a subtask away")
        if parent:
            target_parent = _task_parent(conn, actor, task_id, parent)
            if task_private(conn, target_parent):
                if private is False:
                    refuse(conn, actor, "private", "A task under a private parent stays private")
                sets.append("private=1")
            if not mover and actor not in (target_parent["owner"], target_parent["requester"]) and not task_ancestor_party(conn, actor, target_parent):
                refuse(conn, actor, "identity", "Re-parent under a task you own or requested")
        sets.append("parent_id=:parent_id")
        args["parent_id"] = parent
        _task_event(conn, task_id, actor, "parent_id", row.get("parent_id"), parent, note or "")
    if owner is not None:
        new_owner = _reach(conn, actor, owner)
        if private_tasks_default(conn, new_owner):
            sets.append("private=1")
        if is_bot(row['owner']) and 'detail_json' in {r[1] for r in conn.execute('PRAGMA table_info(task_links)')}:
            for link in task_links(conn, task_id):
                if link['kind'] == 'worktree':
                    detail = json.loads(link.get('detail_json') or '{}')
                    detail.setdefault('owner', row['owner'])
                    conn.execute('UPDATE task_links SET detail_json=? WHERE id=?', (json.dumps(detail), link['id']))
        sets.append("owner=:owner")
        args["owner"] = new_owner
        _task_event(conn, task_id, actor, "owner", row["owner"], new_owner, note or "")
        if new_owner != row["owner"] and rank is None:
            rank = _queue_end(conn, new_owner)      # the bottom of the new queue
    if rank is not None:
        sets.append("rank=:rank")
        args["rank"] = float(rank)
    if goal_id is not None and (goal_id or None) != row.get("goal_id"):
        sets.append("goal_id=:goal_id")
        args["goal_id"] = goal_id or None
        _task_event(conn, task_id, actor, "goal_id", row.get("goal_id"), goal_id or None, note or "")
    if status == "done":
        sets.append("done_at=:done_at")
        args["done_at"] = ts
    elif status in ("open", "doing", "waiting", "review", "ready"):
        for field in ("done_at", "closed_at", "closed_by"):
            if row.get(field) is not None:
                _task_event(conn, task_id, actor, field, row[field], None, note or "")
            sets.append(field + "=NULL")
    if not sets and not state_change:
        return row
    if closing_step:
        task_close(conn, actor, task_id, note=note or "", quiet=quiet, type=type, step=step)
    elif state_change:
        _set_status(conn, actor, row, status, type, step, note or "")
    args["id"] = task_id
    args["updated"] = ts
    if sets:
        conn.execute(f"UPDATE tasks SET {', '.join(sets)}, updated=:updated WHERE id=:id", args)
    if title is not None:
        # The task's own thread was named after it; a room many tasks share keeps its subject.
        conn.execute("UPDATE conversations SET subject=? WHERE id=? AND task_id=? AND subject=?",
                     (title, row["conversation_id"], task_id, row["title"]))
    if plain:
        _task_event(conn, task_id, KEEPER, "lint", None, "; ".join(plain), "")
        event(conn, actor, "task.lint", task_id, {"problems": plain})
    event(conn, actor, "task.update", task_id, {"status": status, "note": note})
    after = isolate_private_task(conn, task(conn, task_id))
    if owner is not None or private is not None or task_private(conn, after) != task_private(conn, row):
        conn.execute("DELETE FROM task_delegations WHERE task_id=?", (task_id,))
    # A bot that asked for `tasks` is always told and never woken for it, so there is nothing to
    # weigh: the news is free. Everyone else keeps the original rule, where the only way to
    # spare a bot the run was to withhold the notice.
    notify_requester = (status in ("done", "declined") and actor != row["requester"]
                        and (status == "declined" or tasks_only(conn, after["requester"])
                             or after.get("parent_id") and is_bot(after["requester"])
                             or _owed_the_news(conn, after)))
    if note is not None and not notify_requester and not quiet:
        _mirror_task_note(conn, actor, after, note)
    if status == "done":
        _unblock(conn, after)
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
              f"{'Finished' if status == 'done' else 'Declined'}: {after['title']}"
              + (f"\n{note}" if note and not quiet else ""), refs=refs, quiet_bots=True)
    if (status == "open" or owner is not None and owner != row["owner"]) and actor != after["owner"]:
        _wake(conn, after, after["owner"], f"Open: {after['title']}")
    if parent_id is not None and row.get("parent_id") and row["parent_id"] != after.get("parent_id") and row["status"] not in ("done", "closed", "declined"):
        _parent_finished(conn, {**row, "status": "closed"}, row["status"], actor)
    _recount(conn, after["owner"])
    _recount(conn, after["requester"])
    if not closing_step:
        _parent_finished(conn, after, row["status"], actor)
    return after


def _task_close_allowed(conn, actor, row, note):
    _task_private_writer(conn, actor, row)
    if actor != KEEPER and not is_human(actor) and row["status"] != "closed" and children_summary(conn, row["id"])["open"]:
        refuse(conn, actor, "children", "Finish the open subtasks before closing this task")
    if actor != row["requester"] and not is_human(actor) and actor != KEEPER:
        refuse(conn, actor, "close", f"{actor_id(row['requester'])} asked for this; only they close it")
    if (row["status"] not in ("done", "declined", "closed") and is_human(actor)
            and is_human(row["owner"]) and is_bot(row["requester"])
            and not str(note or "").strip()):
        refuse(conn, actor, "lint", "Tell the requesting bot why you are closing this task in a note")


@private_task_write
def task_close(conn, actor, task_id, note="", quiet=False, *, type=None, step=None):
    """Rule 5. Closing is an explicit requester, human or keeper decision; work delegation is not acceptance."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    _task_close_allowed(conn, actor, row, note)
    if row["status"] == "closed":
        if type is not None or step is not None:
            _set_status(conn, actor, row, status="closed", type=type, step=step, note=note or "")
        return task(conn, task_id)
    ts = now()
    _set_status(conn, actor, row, status="closed", type=type, step=step, note=note or "")
    conn.execute("UPDATE tasks SET closed_at=?, closed_by=?, updated=?, "
                 "note=COALESCE(NULLIF(?, ''), note) WHERE id=?", (ts, actor, ts, note or "", task_id))
    event(conn, actor, "task.close", task_id, {"note": note})
    after = task(conn, task_id)
    _unblock(conn, after)
    said = f"Closed: {after['title']}" + (f"\n{note}" if note else "")
    # Closing work that was already done, with nothing said, is acceptance: news, not work. It
    # goes to the bot's inbox without a run. Hundreds of runs were a bot
    # waking to read "Closed:" and saying "acknowledged", hours of agent time. The keeper's
    # own closes carry no instruction either.
    # A note from a person, or a close before the work was done, still wakes: that may change
    # what the bot does next.
    accepted = row["status"] in ("done", "declined") and (actor == KEEPER or not str(note or "").strip())
    # A next-run task closed before any run carried it is a cancel: the bot never saw it, so
    # there is nothing to tell it and no run to spend telling it.
    accepted = quiet or accepted or next_run_waiting(conn, row)
    told = [who for who in dict.fromkeys([after["owner"], after["requester"]]) if who != actor]
    for who in told:   # everyone but whoever tapped
        _wake(conn, after, who, said, quiet_bots=True, quiet=accepted)
    parent = task(conn, after["parent_id"]) if after.get("parent_id") else None
    if parent and parent["owner"] != actor and parent["owner"] not in told:
        _wake(conn, after, parent["owner"], f"Child closed: {after['title']}", quiet_bots=True, quiet=True)
    _parent_finished(conn, after, row["status"], actor)
    _recount(conn, after["owner"])
    _recount(conn, after["requester"])
    return after


def waiting_on(conn, row):
    """What a task's owner is waiting for, in words, or None when there is nothing.

    Something is: a question its owner asked about it that nobody has answered, an unfinished
    child, an unfinished blocker, or an approval not yet decided.
    """
    live = "('done','closed','declined')"
    asks = [r["id"] for r in _rows(conn.execute(
        "SELECT m.id FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE m.from_actor=? AND m.kind='ask' AND {MESSAGE_TASK_SQL}=?",
        (row["owner"], row["id"])))]
    if asks and len(answers_to(conn, asks)) < len(asks):
        return "an unanswered question"
    if _one(conn, f"SELECT 1 FROM tasks WHERE parent_id=? AND status NOT IN {live} LIMIT 1", (row["id"],)):
        return "an open child task"
    if row.get("blocked_by") and _one(conn, f"SELECT 1 FROM tasks WHERE id=? AND status NOT IN {live}",
                                      (row["blocked_by"],)):
        return "an open blocker"
    if _one(conn, "SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL LIMIT 1", (row["id"],)):
        return "an undecided approval"
    return None


def sweep_stranded(conn, at=None, grace_hours=24):
    """Daily: a bot's `waiting` task with nothing to wait on goes back to work.

    Bot tasks were found waiting with no live blocker, some for days on nothing at all. A waiting routine task is worse than idle: the scheduler lets it
    absorb every later occurrence without a word, so the routine stops. A routine's task that a
    later occurrence already landed on is closed, and the next occurrence opens a fresh one; any
    other is reopened with a note that wakes its owner.
    """
    at = at or now()
    cutoff = shift(at, hours=-grace_hours)
    moved = []
    for row in _rows(conn.execute("SELECT * FROM tasks WHERE status='waiting' AND owner LIKE 'bot:%' "
                                  "AND updated<=? ORDER BY updated", (cutoff,))):
        with isolated(conn, "sweep_stranded", row["id"]):
            if waiting_on(conn, row) or (bot(conn, actor_id(row["owner"])) or {}).get("state") != "active":
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
                                 "approval is outstanding. Continue it, ask, or mark it done or declined.")
                moved.append((row["id"], "open"))
    return moved


def task_ask(conn, actor, task_id, body):
    """Rule 5's one clarifying question: the owner gets one ask before an answer comes back."""
    _writer(conn, actor)
    row = task(conn, task_id)
    if not row:
        refuse(conn, actor, "not-found", f"no task {task_id}")
    if actor != row["owner"]:
        refuse(conn, actor, "identity", "only the task owner can ask its requester")
    if actor == row["owner"]:
        unanswered = unanswered_ask(conn, row)
        if unanswered:
            refuse(conn, actor, "one-question",
                   "you already asked about this task; wait for the answer")
    msg = say(conn, actor, row["requester"], body, conversation_id=row["conversation_id"],
              kind="ask", refs={"task": task_id})
    conn.execute("UPDATE tasks SET updated=? WHERE id=?", (now(), task_id))     # its question is part of it
    if row["status"] in ("open", "doing", "review"):
        task_update(conn, actor, task_id, status="waiting")
    return msg


def _task_event(conn, task_id, actor, field, old, new, note=""):
    via = VIA.get()
    if via and not any(r[1] == "via" for r in conn.execute("PRAGMA table_info(task_events)")):
        conn.execute("ALTER TABLE task_events ADD COLUMN via TEXT")
    conn.execute("INSERT INTO task_events (id, task_id, ts, actor, field, old, new, note" + (", via" if via else "")
                 + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?" + (", ?" if via else "") + ")",
                 (new_id(), task_id, now(), actor, field,
                  None if old is None else str(old), None if new is None else str(new), note,
                  *((via,) if via else ())))


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
MESSAGE_TASK_SQL = f"coalesce({_task_ref_sql('$.task')}, {_task_ref_sql('$.task_id')}, cv.task_id)"


def _mirror_task_note(conn, actor, task_row, note):
    """Copy a task note into the bot's chat room so people and later turns can read it."""
    if task_private(conn, task_row):
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
    parent_id = child.get("parent_id")
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


# A next-run task still waiting for its owner's next run: open, and not taken by a run that is
# under way or finished. A run that failed or was interrupted gives it back, so the next one
# carries it again; a task the bot already moved on (doing, done) is its own business by then.
NEXT_RUN_WAITING_SQL = (
    "t.next_run=1 AND t.status='open' AND (t.carried_by IS NULL OR NOT EXISTS("
    "SELECT 1 FROM attempts a WHERE a.id=t.carried_by AND a.state IN ('leased','running','completed')))")


def next_run_waiting(conn, row):
    """Whether this task (a row) is a next-run task still waiting to be carried."""
    if not row or not row.get("next_run") or row.get("status") != "open":
        return False
    if not row.get("carried_by"):
        return True
    try:
        return not _one(conn, "SELECT 1 FROM attempts WHERE id=? AND state IN ('leased','running','completed')",
                        (row["carried_by"],))
    except sqlite3.OperationalError:     # a local hub has no attempts table and no runs
        return True


def next_run_tasks(conn, bot, exclude=None):
    """The next-run tasks waiting for `bot` (a slug), oldest first."""
    return _rows(conn.execute(
        f"SELECT t.* FROM tasks t WHERE t.owner=? AND t.id IS NOT ? AND {NEXT_RUN_WAITING_SQL} "
        "ORDER BY t.created,t.id", ("bot:" + bot, exclude)))


def stuck_tasks(conn, hours=STUCK_HOURS, hidden=()):
    """Open work on an active bot that has not moved in `hours` and is waiting on nobody: no person
    owes an answer, no open task blocks it, it is not a quiet task, and no run for its bot is queued
    or going. One BotOps sweep finds these instead of a daily run per bot."""
    cutoff = shift(now(), seconds=-hours * 3600)
    rows = _rows(conn.execute(
        "SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug WHERE b.state='active' "
        "AND t.status IN ('open','doing','ready') AND coalesce(t.next_run,0)=0 AND t.updated<? "
        "ORDER BY t.updated", (cutoff,)))
    try:
        busy = {r["bot"] for r in conn.execute(
            "SELECT DISTINCT bot FROM jobs WHERE state IN ('queued','leased','running')")}
    except sqlite3.OperationalError:     # a local hub has no jobs table
        busy = set()
    out = []
    for row in rows:
        slug = actor_id(row["owner"])
        if slug in hidden or slug in busy or unanswered_ask(conn, row):
            continue
        if not_yet_due(row, now()):     # parked until its due date, like the stall watcher
            continue
        blocker = task(conn, row["blocked_by"]) if row.get("blocked_by") else None
        if blocker and blocker["status"] in ACTIVE_STATUSES:
            continue
        out.append({"id": row["id"], "short_id": row["id"][:8], "title": row["title"], "owner": row["owner"], "status": row["status"],
                    "requester": row["requester"], "updated": row["updated"], "note": row.get("note") or ""})
    return out


# Tasks should not stay stuck for more than a few minutes. The scheduler looks every minute
# for a bot's open or doing task that nothing is going to move: no run for its bot queued or going, no routine of that bot due
# soon, no person owing an answer, no open task blocking it. It wakes the bot on the task, spaced
# and capped; a bot that means to wait says so with `waiting` and a reason, which this never
# touches. If waking does not move it, BotOps gets one task to find out why.
STALL_MINUTES = 5            # quiet this long with nothing set to move it: wake the bot
STALL_NEXT_RUN_MINUTES = 30  # a next-run task was filed not to wake the bot at once; give it longer
STALL_REPEAT_MINUTES = 30    # between two wakes for the same task
STALL_WAKES_PER_DAY = 3      # then BotOps takes it


def not_yet_due(row, at):
    """A task with a future due date says when to look again: the scheduler's "Due:" notice wakes
    its owner then. Legacy dates without an offset read as Pacific."""
    due = parse_ts(row.get("due")) if row.get("due") else None
    if due and due.tzinfo is None:
        from zoneinfo import ZoneInfo
        due = due.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return bool(due and due > parse_ts(at))


def stalled_tasks(conn, at=None):
    """Bot tasks nothing is going to move, with how long they have been quiet (minutes)."""
    at = at or now()
    rows = _rows(conn.execute(
        "SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug WHERE b.state='active' "
        "AND t.status IN ('open','doing') AND coalesce(t.private,1)=0 AND t.updated<? ORDER BY t.updated",
        (shift(at, seconds=-STALL_MINUTES * 60),)))
    if not rows:
        return []
    try:
        busy = {r[0] for r in conn.execute("SELECT DISTINCT bot FROM jobs WHERE state IN ('queued','leased','running')")}
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
        blocker = task(conn, row["blocked_by"]) if row.get("blocked_by") else None
        if blocker and blocker["status"] in ACTIVE_STATUSES:
            continue
        quiet = int((base - parse_ts(row["updated"])).total_seconds() // 60)
        out.append({**row, "quiet_minutes": quiet})
    return out


def wake_stalled(conn, at=None):
    """Wake the owner of each stalled task (at most once per STALL_REPEAT_MINUTES, STALL_WAKES_PER_DAY
    a day); past that, one diagnostic per stuck task. BotOps cannot be its own last resort:
    its stalled work, or work it cannot take while unavailable, goes to a human. Returns what it did."""
    at = at or now()
    woke, escalated = [], []
    for row in stalled_tasks(conn, at):
        with isolated(conn, "wake_stalled", row["id"]):
            # A status note must not buy another round of retries. Cap automatic wakes
            # over the whole day; busy work is excluded above and explicit runs remain available.
            since = shift(at, seconds=-86400)
            count = conn.execute("SELECT count(*) FROM events WHERE action='task.stall_wake' AND target=? AND ts>?",
                                 (row["id"], since)).fetchone()[0] or 0
            last = conn.execute("SELECT max(ts) FROM events WHERE action='task.stall_wake' AND target=?",
                                (row["id"],)).fetchone()[0]
            if last and last > shift(at, seconds=-STALL_REPEAT_MINUTES * 60):
                continue
            if count >= STALL_WAKES_PER_DAY:
                if not conn.execute("SELECT 1 FROM events WHERE action='task.stall_escalated' AND target=? AND ts>?",
                                    (row["id"], shift(at, seconds=-86400))).fetchone():
                    repair_bot = bot(conn, "botops")
                    if row["owner"] != "bot:botops" and repair_bot and repair_bot["state"] == "active":
                        owner = "bot:botops"
                    else:
                        owner = (row["requester"] if is_human(row["requester"]) and _actor_exists(conn, row["requester"])
                                 else human_actor(default_human(conn)))
                        if not _actor_exists(conn, owner):
                            continue
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
                                "or set it waiting with the reason. Complete this diagnostic when the cause is known; "
                                "record any remaining repair dependency on the original task.", owner,
                                deduplicate=False, lint=False)
                    event(conn, KEEPER, "task.stall_escalated", row["id"],
                          {"bot": actor_id(row["owner"]), "wakes": count, "recovery_task": repair["id"], "owner": owner})
                    escalated.append(row["id"])
                continue
            if row.get("next_run"):
                conn.execute("UPDATE tasks SET next_run=0 WHERE id=?", (row["id"],))
            _wake(conn, row, row["owner"],
                  f"Stalled {row['quiet_minutes']} min: {row['title']}. Nothing is set to move it. Carry it on now, "
                  "or set it waiting with the reason (hub task update --status waiting --note).",
                  {"wake": "stalled"})
            event(conn, KEEPER, "task.stall_wake", row["id"],
                  {"bot": actor_id(row["owner"]), "quiet_minutes": row["quiet_minutes"], "wake": count + 1})
            woke.append(row["id"])
    return {"woke": woke, "escalated": escalated}


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
        refuse(conn, actor, "run-now", f"the task is {row['status']}; reopen it first")
    if row.get("next_run"):
        conn.execute("UPDATE tasks SET next_run=0, updated=? WHERE id=?", (now(), task_id))
        _task_event(conn, task_id, actor, "next_run", "1", "0", "run now")
    waiting = None
    try:
        waiting = _one(conn,
            "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.bot=? AND j.state='queued' AND {MESSAGE_TASK_SQL}=? LIMIT 1", (actor_id(row["owner"]), task_id))
    except sqlite3.OperationalError:     # a local hub has no jobs table
        waiting = None
    after = task(conn, task_id)
    if not waiting:
        _wake(conn, after, after["owner"], f"Run now: {after['title']}", {"run_now": True})
    event(conn, actor, "task.run-now", task_id, {"queued": not waiting})
    return after, not waiting


# ----------------------------------------------------------------------------- quiet notes
# A quiet note is the next-run task's lighter twin: something for a bot to know, not to do. It
# starts nothing; the next run the bot has for any reason carries every note waiting for it, in
# the same prompt, each with the time it was sent. For example, monitor bots leave a
# manager bot a note after each check, and its daily report reads them.
NOTE_MAX = 8000
NOTE_WAITING_SQL = (
    "n.cancelled_at IS NULL AND (n.carried_by IS NULL OR NOT EXISTS("
    "SELECT 1 FROM attempts a WHERE a.id=n.carried_by AND a.state IN ('leased','running','completed')))")


def note_create(conn, actor, to, body):
    """Leave a note for a bot's next run. A person has no runs, so only a bot receives one."""
    _writer(conn, actor)
    target = _reach(conn, actor, to)
    if not is_bot(target):
        refuse(conn, actor, "note", "a note waits for a bot's next run; a person has none, so tell them another way")
    if target == actor:
        refuse(conn, actor, "note", "a note to yourself belongs in your own repository")
    body = str(body or "").strip()
    if not body:
        refuse(conn, actor, "note", "the note is empty")
    if len(body) > NOTE_MAX:
        refuse(conn, actor, "note", f"a note is at most {NOTE_MAX} characters; put the detail in a file or a task")
    if classify(body, to_actor=target, actor=actor, where="message", conn=conn) == "escape" and is_bot(actor):
        refuse(conn, actor, "escape", f"the note reaches outside the hub: {_clip(body, 80)}", "escape")
    row = {"id": new_id(), "from_actor": actor, "to_actor": target, "body": body, "created": now()}
    conn.execute("INSERT INTO notes(id,from_actor,to_actor,body,created) VALUES "
                 "(:id,:from_actor,:to_actor,:body,:created)", row)
    event(conn, actor, "note.create", row["id"], {"to": target})
    return note(conn, row["id"])


def note(conn, note_id):
    return _one(conn, "SELECT * FROM notes WHERE id=?", (note_id,))


def note_waiting(conn, row):
    """Whether this note (a row) is still waiting for its bot's next run."""
    if not row or row.get("cancelled_at"):
        return False
    if not row.get("carried_by"):
        return True
    try:
        return not _one(conn, "SELECT 1 FROM attempts WHERE id=? AND state IN ('leased','running','completed')",
                        (row["carried_by"],))
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
    """The notes waiting for `bot` (a slug), oldest first."""
    return _rows(conn.execute(
        f"SELECT n.* FROM notes n WHERE n.to_actor=? AND {NOTE_WAITING_SQL} ORDER BY n.created,n.id LIMIT ?",
        ("bot:" + bot, limit)))


def _wake(conn, task_row, target, body, refs=None, quiet_bots=False, quiet=False):
    """Tell someone their task moved. Task traffic never counts against rule 4.

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
    if quiet_bots and (quiet or tasks_only(conn, target)):
        refs = {**(refs or {}), "quiet": True}
    if not target or not _actor_exists(conn, target) or not task_private_readable(conn, target, task_row):
        return None
    message_refs = {"task": task_row["id"], **(refs or {})}
    return _write_message(conn, KEEPER, target, body,
                          conversation(conn, task_row["conversation_id"]), "notice",
                          message_refs, None, None)


# ----------------------------------------------------------------------------- approvals
def approval_request(conn, actor, kind, payload, task_id=None):
    """Rule 6. One exact payload, one open request per payload, a human decides, spent once."""
    _writer(conn, actor)
    if kind not in APPROVAL_KINDS:
        refuse(conn, actor, "kind", f"an approval is {'|'.join(APPROVAL_KINDS)}, not {kind}")
    text = json.dumps(payload, sort_keys=True, default=str)
    severity = classify(text, kind=kind, conn=conn)
    if severity == "escape":
        refuse(conn, actor, "escape", f"the {kind} payload reaches outside the hub: {_clip(text, 100)}", severity)
    problems = lint_approval(kind, payload)
    if problems:
        refuse(conn, actor, "lint", "; ".join(problems), classify(text, kind=kind, conn=conn))
    digest = payload_hash(payload)
    dup = _one(conn, "SELECT id FROM approvals WHERE payload_hash=? AND decision IS NULL", (digest,))
    if dup:
        refuse(conn, actor, "duplicate", f"{dup['id']} already asks for exactly this")
    owner = None
    if task_id:
        row = task(conn, task_id)
        if row and is_human(row["owner"]):
            owner = row["owner"]
    owner = owner or human_actor(default_human(conn))
    ts = now()
    msg = _write_message(conn, actor, owner,
                         f"Approve this {kind}: " + _clip(text, 200),
                         _pair_conversation(conn, actor, owner, "notice"), "notice",
                         {"approval": "pending", "task": task_id}, None, None)
    row = {"id": new_id(), "kind": kind, "task_id": task_id, "message_id": msg["id"],
           "payload_json": _dump(payload), "payload_hash": digest, "requested_by": actor,
           "decided_by": None, "decision": None, "decided_at": None, "consumed_at": None,
           "created": ts}
    conn.execute("INSERT INTO approvals (id, kind, task_id, message_id, payload_json, payload_hash, "
                 "requested_by, decided_by, decision, decided_at, consumed_at, created) VALUES "
                 "(:id, :kind, :task_id, :message_id, :payload_json, :payload_hash, :requested_by, "
                 ":decided_by, :decision, :decided_at, :consumed_at, :created)", row)
    conn.execute("UPDATE messages SET refs_json=? WHERE id=?",
                 (_dump({"approval": row["id"], "task": task_id}), msg["id"]))
    event(conn, actor, "approval.request", row["id"], {"kind": kind, "to": owner})
    _recount(conn, actor)
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
    conn.execute("UPDATE approvals SET decision=?, decided_by=?, decided_at=? WHERE id=?",
                 (decision, actor, ts, approval_id))
    event(conn, actor, "approval.decide", approval_id, {"decision": decision, "note": note})
    after = approval(conn, approval_id)
    conv = conversation(conn, (message(conn, row["message_id"]) or {}).get("conversation_id")) \
        if row.get("message_id") else None
    if conv:
        _write_message(conn, KEEPER, row["requested_by"],
                       f"{decision.title()}: your {row['kind']} approval." + (f"\n{note}" if note else ""),
                       conv, "notice", {"approval": approval_id}, None, None)
    _recount(conn, row["requested_by"])
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


def _recount(conn, actor):
    """`open_tasks` and `needs_human` on a bot's status row, after anything that moves a task."""
    if not is_bot(actor):
        return
    slug = actor_id(actor)
    if not _one(conn, "SELECT bot FROM bot_status WHERE bot=?", (slug,)):
        return
    live = ACTIVE_STATUSES
    marks = ",".join("?" * len(live))
    open_tasks = conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE owner=? AND status IN ({marks})",
        (actor, *live)).fetchone()[0]
    waiting = conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE requester=? AND owner LIKE 'human:%' AND status IN ({marks})",
        (actor, *live)).fetchone()[0]
    waiting += conn.execute("SELECT COUNT(*) FROM approvals WHERE requested_by=? AND decision IS NULL",
                            (actor,)).fetchone()[0]
    conn.execute("UPDATE bot_status SET open_tasks=?, needs_human=?, updated_at=? WHERE bot=?",
                 (open_tasks, waiting, now(), slug))


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
               task_id=None):
    """A turn on a bot's thread. Replaces the legacy dispatcher's `runs.jsonl` record."""
    _writer(conn, actor)
    slug = actor_id(bot_slug) if actor_kind(bot_slug) else str(bot_slug).strip()
    if is_bot(actor) and actor_id(actor) != slug:
        refuse(conn, actor, "identity", f"{actor_id(actor)} may only record its own turns")
    row = {"id": new_id(), "bot": slug, "thread_id": thread_id, "started": now(),
           "finished": None, "trigger": trigger, "message_id": message_id, "task_id": task_id,
           "exit": None, "tokens_in": None, "tokens_out": None, "cost": None, "summary": None}
    conn.execute("INSERT INTO turns (id, bot, thread_id, started, finished, trigger, message_id, "
                 "task_id, exit, tokens_in, tokens_out, cost, summary) VALUES (:id, :bot, "
                 ":thread_id, :started, :finished, :trigger, :message_id, :task_id, :exit, "
                 ":tokens_in, :tokens_out, :cost, :summary)", row)
    event(conn, actor, "turn.start", row["id"], {"bot": slug, "trigger": trigger})
    return _one(conn, "SELECT * FROM turns WHERE id=?", (row["id"],))


def turn_finish(conn, actor, turn_id, exit_code="ok", tokens_in=None, tokens_out=None, cost=None,
                summary=None, usage=None):
    """Close a turn. `exit_code` is the `exit` column (`exit` is a builtin, not a keyword here).
    `usage` is what the run cost in tokens: input_tokens, cached_tokens, output_tokens, model, provider,
    est_cost_usd and billing (see USAGE_SCHEMA)."""
    _writer(conn, actor)
    ts = now()
    conn.execute("UPDATE turns SET finished=?, exit=?, tokens_in=?, tokens_out=?, cost=?, summary=? "
                 "WHERE id=?", (ts, exit_code, tokens_in, tokens_out, cost, summary, turn_id))
    if usage:
        conn.execute("UPDATE turns SET input_tokens=:input_tokens, cached_tokens=:cached_tokens, "
                     "output_tokens=:output_tokens, model=:model, provider=:provider, est_cost_usd=:est_cost_usd, "
                     "billing=:billing WHERE id=:id",
                     {"input_tokens": None, "cached_tokens": None, "output_tokens": None, "model": None,
                      "provider": None, "est_cost_usd": None, "billing": None, **usage, "id": turn_id})
    if usage and usage.get("segments"):
        conn.execute("DELETE FROM turn_usage_segments WHERE turn_id=?", (turn_id,))
        for position, part in enumerate(usage["segments"]):
            conn.execute("INSERT INTO turn_usage_segments VALUES(:turn_id,:position,:input_tokens,:cached_tokens,"
                         ":output_tokens,:model,:provider,:est_cost_usd,:billing,:runtime,:harness,:effort,:profile)",
                         {**part, "turn_id": turn_id, "position": position})
    row = _one(conn, "SELECT * FROM turns WHERE id=?", (turn_id,))
    if row:
        conn.execute("UPDATE bots SET last_turn_at=? WHERE slug=?", (ts, row["bot"]))
        conn.execute("UPDATE bot_status SET last_turn_at=?, updated_at=? WHERE bot=?",
                     (ts, ts, row["bot"]))
    event(conn, actor, "turn.finish", turn_id, {"exit": exit_code, "cost": cost})
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
    return _rows(conn.execute(sql + " ORDER BY slug", args))


def human(conn, pid):
    row = _one(conn, "SELECT * FROM humans WHERE id=?", (actor_id(pid),))
    if row:
        row["teams"] = _json(row.get("teams_json"), []) or []
    return row


def humans(conn):
    out = _rows(conn.execute("SELECT * FROM humans ORDER BY id"))
    for row in out:
        row["teams"] = _json(row.get("teams_json"), []) or []
    return out


def conversation(conn, conversation_id):
    row = _one(conn, "SELECT * FROM conversations WHERE id=?", (conversation_id,))
    if row:
        row["participants"] = _json(row.get("participants_json"), []) or []
    return row


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


def message(conn, message_id, *, include_deleted=False):
    row = _one(conn, "SELECT * FROM messages WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL"),
               (message_id,))
    if row:
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
        row = _one(conn, "SELECT * FROM messages WHERE in_reply_to=? AND kind='answer' AND deleted_at IS NULL "
                         "ORDER BY created LIMIT 1", (mid,))
        if not row:
            asked = _one(conn, "SELECT answered_by FROM messages WHERE id=?", (mid,))
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


def inbox(conn, actor, at=None):
    """What is waiting for me: unread or undelivered messages, and my open tasks."""
    at = at or now()
    rows = _rows(conn.execute(
        "SELECT * FROM messages WHERE to_actor=? AND (delivered_at IS NULL OR read_at IS NULL) "
        "AND (expires_at IS NULL OR expires_at > ?) AND deleted_at IS NULL ORDER BY created", (actor, at)))
    answered = answers_to(conn, [r["id"] for r in rows if r.get("kind") == "ask"])
    for row in rows:
        row["refs"] = _json(row.get("refs_json"), {}) or {}
        got = answered.get(row["id"])
        row["answered_at"] = got["created"] if got else None
    return {"actor": actor, "messages": rows,
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


def task(conn, task_id):
    row = _one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
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
          updated_since=None, tickets=True):
    """Tasks, newest work first. `visible` is a WHERE fragment over the task's own columns (from
    `Auth.task_sql`), so a caller's page and its `offset` are cut in the query. `order="step"` is
    a board's: by step, then each task's place in it. `updated_since` is a stored timestamp.
    `tickets=False` leaves out the tasks on a numbered type."""
    sql, args, where = "SELECT * FROM tasks", [], []
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
    rows = _rows(conn.execute(sql + f" ORDER BY {ordering} LIMIT ? OFFSET ?",
                              (*args, max(1, int(limit)), max(0, int(offset)))))
    hydrate_task_tags(conn, rows)
    return rows


def tasks_due_for_bots(conn, through):
    """Live tasks a bot owns whose due date may have arrived by `through` (a "YYYY-MM-DD").

    Due dates are text in more than one shape (an offset, a bare date, an old local time), so this
    is a day-level bound with a day of slack; the caller compares the exact instant. No row cap:
    the reminder pass needs every one of them, however many tasks the hub holds.
    """
    return _rows(conn.execute(
        f"SELECT * FROM tasks WHERE status IN ({','.join(repr(x) for x in ACTIVE_STATUSES)}) "
        "AND owner LIKE 'bot:%' AND due IS NOT NULL AND due!='' AND substr(due,1,10)<=? "
        "AND NOT EXISTS (SELECT 1 FROM task_reminders r WHERE r.task_id=tasks.id AND r.due=tasks.due) "
        "ORDER BY created,id", (through,)))


def task_history(conn, task_id):
    return _rows(conn.execute("SELECT * FROM task_events WHERE task_id=? ORDER BY ts", (task_id,)))


def unanswered_ask(conn, task_row, *, actor=None):
    """The latest unanswered ask on this task, or None."""
    return next(iter(open_task_asks(conn, task_row, actor=actor)), None)


def tasks_asked_of(conn, actor):
    """Live tasks with an unanswered ask addressed to this actor, even if they do not own them."""
    return _rows(conn.execute(
        "SELECT t.* FROM tasks t JOIN conversations cv ON cv.id=t.conversation_id "
        f"JOIN messages m ON m.conversation_id=t.conversation_id AND {MESSAGE_TASK_SQL}=t.id "
        f"WHERE (t.status IN ({','.join(repr(x) for x in ACTIVE_STATUSES)}) "
        "OR (t.status='done' AND json_type(m.refs_json,'$.questions')='array')) "
        "AND m.kind='ask' AND m.to_actor=? AND m.answered_by IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM messages a WHERE a.in_reply_to=m.id AND a.kind='answer') "
        "GROUP BY t.id ORDER BY t.created", (actor,)))


def needs_you(conn, who):
    """The "Needs you" list: my open tasks, unanswered asks to me, approvals, declined-to-me.

    A task on a numbered type is a ticket on that type's board, worked through there: it is on
    the list only while it carries a question for the person, and a declined one stays off its
    requester's list."""
    actor = who if actor_kind(who) else human_actor(who)
    pending = _rows(conn.execute("SELECT * FROM approvals WHERE decision IS NULL ORDER BY created"))
    for row in pending:
        row["payload"] = _json(row.get("payload_json"), {}) or {}
    # The person's queue: asks first (each blocks a bot), then their own tasks in rank order.
    # Product-lane work is a backlog a developer works through on the Product board, not a
    # decision owed today: it counts here only when it carries a question for the person.
    # The same goes for a numbered type's tickets, a board of its own.
    owned = [t for t in tasks(conn, owner=actor, status=ACTIVE_STATUSES, tickets=False)
             if (t.get("lane") or "company") != "product"]
    seen = {t["id"] for t in owned}
    asked = [t for t in tasks_asked_of(conn, actor) if t["id"] not in seen]
    return {"actor": actor,
            "tasks": asked + owned,
            "approvals": pending,
            "declined": tasks(conn, requester=actor, status="declined", tickets=False)}


def approval(conn, approval_id):
    row = _one(conn, "SELECT * FROM approvals WHERE id=?", (approval_id,))
    if row:
        row["payload"] = _json(row.get("payload_json"), {}) or {}
    return row


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
    for row in out:
        row["payload"] = _json(row.get("payload_json"), {}) or {}
    return out


def status(conn, bot_slug):
    return _one(conn, "SELECT * FROM bot_status WHERE bot=?", (actor_id(bot_slug),))


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


def task_origin(conn, row):
    """Trusted origin for a keeper review, including pre-metadata records; never parse titles."""
    explicit = _one(conn, "SELECT detail_json FROM events WHERE actor=? AND action='task.origin' "
                         "AND target=? ORDER BY ts DESC LIMIT 1", (KEEPER, row["id"]))
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
