"""Transactions and additive cloud schema over the existing Hub domain layer."""

import hashlib
import heapq
import logging
import json
import os
import sqlite3
import threading
import time
from collections import deque
from contextlib import ExitStack, contextmanager

import orjson
import yaml

from . import goals as G
from . import hubdb as H
from . import idempotency_messages
from . import message_links
from . import people as P
from .harnesses import EXTERNAL_HARNESSES


# A person's own spoken Tico turns, spoken decisions and what a live tool did are history,
# not work: they never queue a job. A live ask does; that is how the text Tico is reached.
# A bot run by an external agent (a Hermes profile) has no runner to claim a job: the message
# waits in its inbox until the agent reads it, so no job is queued for it. A synced bot delivered
# through its platform's desktop app on a computer (backend/app_delivery.py) is the exception while
# that computer is registered: the computer's runner takes the message as a job.
EXTERNAL_BOT_SQL = ("EXISTS (SELECT 1 FROM bot_config WHERE bot=substr(NEW.to_actor,5) AND "
                    "json_extract(config_json,'$.harness') IN ("
                    + ",".join("'" + h + "'" for h in EXTERNAL_HARNESSES) + ") AND NOT EXISTS ("
                    "SELECT 1 FROM runners r WHERE r.id=json_extract(config_json,'$.app_delivery.computer') "
                    "AND r.revoked_at IS NULL))")
QUEUE_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS queue_bot_message AFTER INSERT ON messages "
    "WHEN NEW.to_actor LIKE 'bot:%' AND COALESCE(NEW.from_actor, '') != NEW.to_actor "
    "AND COALESCE(json_extract(NEW.refs_json,'$.live.kind'), '') NOT IN ('transcript','decision','tool') "
    "AND COALESCE(json_extract(NEW.refs_json,'$.quiet'), 0) = 0 "
    "AND NOT " + EXTERNAL_BOT_SQL + " BEGIN "
    "INSERT INTO jobs(id,message_id,bot,created) "
    "VALUES(NEW.id,NEW.id,substr(NEW.to_actor,5),NEW.created); END")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_migrations(version INTEGER PRIMARY KEY, applied TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_links(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 kind TEXT NOT NULL, url TEXT NOT NULL, title TEXT, state TEXT, added_by TEXT, created TEXT NOT NULL,
 pr_sha TEXT, pr_merged_at TEXT);
CREATE INDEX IF NOT EXISTS task_links_task ON task_links(task_id);
CREATE TABLE IF NOT EXISTS main_pushes(seq INTEGER PRIMARY KEY AUTOINCREMENT, sha TEXT NOT NULL UNIQUE,
 repo TEXT, pushed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preferences(actor TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL,
 updated TEXT NOT NULL, PRIMARY KEY(actor, key));
CREATE TABLE IF NOT EXISTS registry_metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
-- repo holds a bare name, owner/name, or a full https URL; see store.repo_url.
CREATE TABLE IF NOT EXISTS bot_config(
 bot TEXT PRIMARY KEY REFERENCES bots(slug), config_json TEXT NOT NULL, team TEXT,
 operator TEXT NOT NULL, owner_ids_json TEXT, revision INTEGER NOT NULL DEFAULT 1,
 description TEXT NOT NULL DEFAULT '', reports_to TEXT, repo TEXT NOT NULL DEFAULT '',
 thread_mode TEXT, definition_updated TEXT,
 definition_updated_by TEXT, goals TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS bot_control(bot TEXT PRIMARY KEY REFERENCES bots(slug), draining INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS bot_transitions(
 id TEXT PRIMARY KEY, bot TEXT NOT NULL REFERENCES bots(slug), kind TEXT NOT NULL,
 target_json TEXT NOT NULL, requested_by TEXT NOT NULL, expected_revision INTEGER NOT NULL,
 expected_generation INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
 undo_change_id TEXT REFERENCES settings_changes(id),
 error TEXT, without_checkpoint INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, updated TEXT NOT NULL, applied_at TEXT);
CREATE TABLE IF NOT EXISTS bot_transition_checkpoints(
 transition_id TEXT NOT NULL REFERENCES bot_transitions(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 message_id TEXT NOT NULL UNIQUE REFERENCES messages(id), state TEXT NOT NULL DEFAULT 'queued',
 checkpoint_json TEXT, error TEXT,
 PRIMARY KEY(transition_id,conversation_id));
CREATE TABLE IF NOT EXISTS settings_changes(
 id TEXT PRIMARY KEY, bot TEXT NOT NULL REFERENCES bots(slug), field TEXT NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL, actor TEXT NOT NULL,
 transition_id TEXT REFERENCES bot_transitions(id), created TEXT NOT NULL,
 undone_by TEXT, undone_at TEXT);
-- The history reads newest first by (created, id) (settings_admin._history_rows). An index that ends in id is read in
-- that order; one that stops at created sorts every same-timestamp tie group.
CREATE INDEX IF NOT EXISTS settings_changes_created_id ON settings_changes(created,id);
CREATE INDEX IF NOT EXISTS settings_changes_bot_id ON settings_changes(bot,created,id);
CREATE TABLE IF NOT EXISTS session_epochs(
 conversation_id TEXT PRIMARY KEY REFERENCES conversations(id), epoch INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
-- Moved by every clear or reset of one bot: a bot's direct chats share its one `bot` thread
-- (clients/sessions.py), so one chat's Clear must reach the next turn of any of them.
CREATE TABLE IF NOT EXISTS bot_session_epochs(
 bot TEXT PRIMARY KEY REFERENCES bots(slug), epoch INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
-- Current provider thread for one bot+runtime+model. Local files hold the real
-- context; this pointer lets a runner try resume, then fall back to a Hub pack.
CREATE TABLE IF NOT EXISTS bot_sessions(
 bot TEXT NOT NULL REFERENCES bots(slug), runtime TEXT NOT NULL, model TEXT NOT NULL,
 thread_id TEXT NOT NULL, runner_id TEXT REFERENCES runners(id),
 tokens_in INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL,
 PRIMARY KEY(bot, runtime, model));
-- One external agent per bot (docs/hermes-agents.md): the credential hash and the last heartbeat.
CREATE TABLE IF NOT EXISTS agents(
 bot TEXT PRIMARY KEY REFERENCES bots(slug), harness TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 created TEXT NOT NULL, created_by TEXT NOT NULL, last_seen TEXT, version TEXT, platform TEXT,
 model TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', profile TEXT NOT NULL DEFAULT '',
 detail TEXT NOT NULL DEFAULT '', revoked_at TEXT, revoked_by TEXT);
-- A Hermes profile asking to be connected to a bot (backend/agents.py): the code a person
-- reads out, the hash of the secret only the profile knows, the public key that secret derives
-- (token_key), and, from approval until the profile collects it once, the minted credential
-- sealed to that key: only the profile's secret opens it.
CREATE TABLE IF NOT EXISTS agent_pairings(
 id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, secret_hash TEXT NOT NULL,
 profile TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '',
 harness TEXT NOT NULL DEFAULT 'hermes', client_hash TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'pending', bot TEXT, token TEXT,
 created TEXT NOT NULL, expires_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT,
 token_key TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS agent_pairings_client ON agent_pairings(client_hash, created);
CREATE TABLE IF NOT EXISTS runners(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, operator TEXT NOT NULL REFERENCES humans(id),
 token_hash TEXT NOT NULL UNIQUE, created TEXT NOT NULL, last_seen TEXT,
 revoked_at TEXT, platform TEXT, version TEXT, capacity INTEGER NOT NULL DEFAULT 4,
 readiness_json TEXT NOT NULL DEFAULT '{}', capabilities_json TEXT NOT NULL DEFAULT '[]',
 awake_since TEXT, checkout_json TEXT,
 restart_requested TEXT, readiness_at TEXT);
CREATE TABLE IF NOT EXISTS model_logins(
 id TEXT PRIMARY KEY, runner_id TEXT NOT NULL REFERENCES runners(id), runtime TEXT NOT NULL,
 profile TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
 user_code TEXT NOT NULL DEFAULT '', lines_json TEXT NOT NULL DEFAULT '[]',
 message TEXT NOT NULL DEFAULT '', pending_code TEXT, code_sent INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, updated TEXT NOT NULL, expires_at TEXT NOT NULL, requested_by TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'sign_in');
CREATE INDEX IF NOT EXISTS model_logins_by_runner ON model_logins(runner_id,state);
CREATE TABLE IF NOT EXISTS model_login_cleared(
 runner_id TEXT NOT NULL REFERENCES runners(id), runtime TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '',
 cleared_at TEXT NOT NULL, cleared_by TEXT NOT NULL, clear_key INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(runner_id,runtime,profile));
-- A model key stored in the vault for one computer (backend/computer_keys.py): staged until the computer checks it.
CREATE TABLE IF NOT EXISTS computer_keys(
 credential_id TEXT PRIMARY KEY REFERENCES credentials(id), runner_id TEXT NOT NULL REFERENCES runners(id),
 runtime TEXT NOT NULL, state TEXT NOT NULL, login_id TEXT, created TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS computer_keys_by_runner ON computer_keys(runner_id,runtime,state);
CREATE TABLE IF NOT EXISTS model_login_key_off(
 runner_id TEXT NOT NULL REFERENCES runners(id), runtime TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '',
 set_at TEXT NOT NULL, set_by TEXT NOT NULL, PRIMARY KEY(runner_id,runtime,profile));
CREATE TABLE IF NOT EXISTS runner_harness_actions(
 id TEXT PRIMARY KEY, runner_id TEXT NOT NULL REFERENCES runners(id), harness TEXT NOT NULL,
 action TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL, updated TEXT NOT NULL, requested_by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runner_harness_actions_by_runner ON runner_harness_actions(runner_id,state);
CREATE TABLE IF NOT EXISTS runner_harness_choices(
 runner_id TEXT NOT NULL REFERENCES runners(id), harness TEXT NOT NULL, enabled INTEGER NOT NULL,
 updated TEXT NOT NULL, updated_by TEXT NOT NULL, PRIMARY KEY(runner_id,harness));
CREATE TABLE IF NOT EXISTS enrollments(
 code_hash TEXT PRIMARY KEY, operator TEXT NOT NULL REFERENCES humans(id),
 expires TEXT NOT NULL, consumed_at TEXT, runner_id TEXT);
CREATE TABLE IF NOT EXISTS hosting_enrollments(
 request_id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS human_tokens(
 id TEXT PRIMARY KEY, human TEXT NOT NULL REFERENCES humans(id), label TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE, created TEXT NOT NULL, created_by TEXT NOT NULL,
 last_used TEXT, expires_at TEXT, revoked_at TEXT);
-- OAuth sign-in for the MCP server (backend/mcp_oauth.py): registered agents, and each person's approval of one,
-- which ends in a human_tokens row whose secret a refresh rotates in place.
CREATE TABLE IF NOT EXISTS oauth_clients(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, redirect_uris_json TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_grants(
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES oauth_clients(id), human TEXT NOT NULL,
 redirect_uri TEXT NOT NULL, code_challenge TEXT NOT NULL, state TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
 code_hash TEXT UNIQUE, code_expires TEXT, token_id TEXT REFERENCES human_tokens(id),
 refresh_hash TEXT UNIQUE, refresh_expires TEXT, denied_at TEXT);
-- Service keys (backend/service_keys.py): another system's credential for a few routes (its scope), as a
-- hash, and the task each (key, that system's own key for the work) pair names.
CREATE TABLE IF NOT EXISTS service_keys(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, created TEXT NOT NULL,
 created_by TEXT NOT NULL, last_used TEXT, revoked_at TEXT, revoked_by TEXT, scope TEXT NOT NULL DEFAULT 'tasks',
 made_with_token TEXT);
CREATE TABLE IF NOT EXISTS service_key_tasks(
 key_id TEXT NOT NULL REFERENCES service_keys(id), external_key TEXT NOT NULL,
 task_id TEXT NOT NULL REFERENCES tasks(id), created TEXT NOT NULL, PRIMARY KEY(key_id, external_key));
CREATE TABLE IF NOT EXISTS oidc_sessions(
 id_hash TEXT PRIMARY KEY, human TEXT NOT NULL REFERENCES humans(id), email TEXT NOT NULL,
 created TEXT NOT NULL, last_seen TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS oidc_sessions_human ON oidc_sessions(human);
CREATE TABLE IF NOT EXISTS oidc_codes(
 code_hash TEXT PRIMARY KEY, human TEXT NOT NULL REFERENCES humans(id), email TEXT NOT NULL,
 challenge TEXT NOT NULL, origin TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assignments(
 bot TEXT PRIMARY KEY REFERENCES bots(slug), runner_id TEXT NOT NULL REFERENCES runners(id),
 generation INTEGER NOT NULL, updated TEXT NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE REFERENCES messages(id),
 bot TEXT NOT NULL REFERENCES bots(slug), state TEXT NOT NULL DEFAULT 'queued',
 created TEXT NOT NULL, attempt_id TEXT);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(bot,state,created);
CREATE TABLE IF NOT EXISTS job_recovery(
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), attempt_id TEXT NOT NULL REFERENCES attempts(id),
 decision TEXT NOT NULL, note TEXT NOT NULL, actor TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), bot TEXT NOT NULL,
 runner_id TEXT NOT NULL REFERENCES runners(id), generation INTEGER NOT NULL,
 token_hash TEXT NOT NULL UNIQUE, state TEXT NOT NULL, lease_until TEXT NOT NULL,
 created TEXT NOT NULL, started TEXT, finished TEXT, thread_id TEXT,
 result_json TEXT, final_text TEXT, last_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS attempt_diagnostics(
 attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), heartbeat_at TEXT, last_event_at TEXT, last_event TEXT,
 end_reason TEXT, ended_at TEXT, stalled_at TEXT, stderr_tail TEXT, stderr_at TEXT);
CREATE INDEX IF NOT EXISTS attempt_diagnostics_stderr ON attempt_diagnostics(stderr_at) WHERE stderr_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS attempt_diagnostics_stalled ON attempt_diagnostics(stalled_at) WHERE stalled_at IS NOT NULL;
-- Bound history reads by time before evaluating their visibility gates.
CREATE INDEX IF NOT EXISTS attempts_created ON attempts(created);
CREATE INDEX IF NOT EXISTS attempts_bot_state ON attempts(bot,state);
CREATE TABLE IF NOT EXISTS attempt_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES attempts(id),
 seq INTEGER NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(attempt_id,seq));
CREATE TABLE IF NOT EXISTS attempt_conversations(
 attempt_id TEXT NOT NULL REFERENCES attempts(id), conversation_id TEXT NOT NULL REFERENCES conversations(id),
 PRIMARY KEY(attempt_id,conversation_id));
CREATE TABLE IF NOT EXISTS attempt_inputs(
 attempt_id TEXT NOT NULL REFERENCES attempts(id), message_id TEXT PRIMARY KEY REFERENCES messages(id),
 acked_at TEXT);
-- A run's inputs by run (backend/task_privacy.py attempt_tasks): without it each lookup scanned every message.
CREATE INDEX IF NOT EXISTS attempt_inputs_attempt ON attempt_inputs(attempt_id);
-- A run's raw provider stream for a bot with `capture_stream` on, kept a week (backend/stream_capture.py).
CREATE TABLE IF NOT EXISTS attempt_captures(
 attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), bot TEXT NOT NULL, provider TEXT NOT NULL,
 frames TEXT NOT NULL, truncated INTEGER NOT NULL DEFAULT 0, dropped_bytes INTEGER NOT NULL DEFAULT 0,
 bytes INTEGER NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS attempt_captures_created ON attempt_captures(created);
-- A run that had a secret taken out while it was going: its capture, handed in at the end, is not kept.
CREATE TABLE IF NOT EXISTS attempt_capture_drops(attempt_id TEXT PRIMARY KEY, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(
 actor TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
 request_hash TEXT NOT NULL, response_json TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(actor,operation,key));
CREATE INDEX IF NOT EXISTS idempotency_by_created ON idempotency(created);
CREATE TABLE IF NOT EXISTS schedule_occurrences(
 schedule_id TEXT NOT NULL REFERENCES schedules(id), occurrence TEXT NOT NULL,
 task_id TEXT REFERENCES tasks(id), outcome TEXT NOT NULL,
 PRIMARY KEY(schedule_id,occurrence));
CREATE TABLE IF NOT EXISTS schedule_config(
 schedule_id TEXT PRIMARY KEY REFERENCES schedules(id), timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
 enabled INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY, person TEXT, state TEXT, created TEXT, committed_at TEXT,
  items_json TEXT NOT NULL DEFAULT '[]', cursor INTEGER NOT NULL DEFAULT 0,
  responses_json TEXT NOT NULL DEFAULT '{}', alerts_json TEXT NOT NULL DEFAULT '[]',
  message_id TEXT, report_json TEXT, scope TEXT);
CREATE INDEX IF NOT EXISTS batches_open ON batches(person, state);
CREATE TABLE IF NOT EXISTS task_reminders(
 task_id TEXT NOT NULL REFERENCES tasks(id), due TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(task_id,due));
CREATE TABLE IF NOT EXISTS service_health(
 service TEXT PRIMARY KEY, last_success TEXT, last_error TEXT, detail_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS meeting_importers(
 source TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, runner_id TEXT,
 updated_by TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS connector_snapshots(
 kind TEXT NOT NULL, owner TEXT NOT NULL REFERENCES humans(id), version INTEGER NOT NULL DEFAULT 1,
 payload_json TEXT NOT NULL, updated TEXT NOT NULL, runner_id TEXT NOT NULL,
 PRIMARY KEY(kind,owner));
CREATE TABLE IF NOT EXISTS calendar_actions(
 id TEXT PRIMARY KEY, requested_by TEXT NOT NULL, calendar_email TEXT NOT NULL,
 summary TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
 attendees_json TEXT NOT NULL DEFAULT '[]', description TEXT NOT NULL DEFAULT '',
 add_meet INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
 result_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
 runner_id TEXT, created TEXT NOT NULL, updated TEXT NOT NULL, started TEXT, finished TEXT);
CREATE INDEX IF NOT EXISTS calendar_actions_queue ON calendar_actions(status,created);
CREATE TABLE IF NOT EXISTS mail_mailboxes(
 address TEXT PRIMARY KEY, person_id TEXT, runner_id TEXT, synced_at TEXT,
 message_count INTEGER NOT NULL DEFAULT 0, oldest_epoch INTEGER, newest_epoch INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS mail_agent_instructions(
 bot TEXT PRIMARY KEY REFERENCES bots(slug), content TEXT NOT NULL,
 runner_id TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bot_agent_instructions(
 bot TEXT PRIMARY KEY REFERENCES bots(slug), content TEXT NOT NULL,
 runner_id TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mail_messages(
 mailbox TEXT NOT NULL, msg_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
 epoch INTEGER NOT NULL, date TEXT NOT NULL, from_addr TEXT NOT NULL, from_header TEXT NOT NULL DEFAULT '',
 to_json TEXT NOT NULL, cc_json TEXT NOT NULL, subject TEXT NOT NULL, snippet TEXT NOT NULL,
 labels_json TEXT NOT NULL, body TEXT NOT NULL, body_truncated INTEGER NOT NULL DEFAULT 0,
 attachments_json TEXT NOT NULL, list_id TEXT NOT NULL DEFAULT '', is_internal INTEGER NOT NULL DEFAULT 0,
 has_unsubscribe INTEGER NOT NULL DEFAULT 0, rule_hits_json TEXT NOT NULL DEFAULT '[]',
 updated TEXT NOT NULL, deleted_at TEXT, PRIMARY KEY(mailbox,msg_id));
CREATE INDEX IF NOT EXISTS mail_messages_thread ON mail_messages(mailbox,thread_id,epoch);
CREATE INDEX IF NOT EXISTS mail_messages_epoch ON mail_messages(mailbox,epoch DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS mail_fts USING fts5(
 subject, body, from_addr, to_text, mailbox UNINDEXED, msg_id UNINDEXED, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS credential_keys(
 id TEXT PRIMARY KEY, kms_key TEXT NOT NULL, wrapped_key BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS credential_uses(
 credential_id TEXT NOT NULL, actor TEXT NOT NULL, bot TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
 logged TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(credential_id, actor, bot));
CREATE INDEX IF NOT EXISTS credential_uses_at ON credential_uses(credential_id, at);
CREATE TABLE IF NOT EXISTS credentials(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL, env TEXT NOT NULL DEFAULT '', preview TEXT NOT NULL DEFAULT '',
 ciphertext BLOB, nonce BLOB, source TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL,
 updated_by TEXT NOT NULL, created_by TEXT, member_stored INTEGER NOT NULL DEFAULT 0);
-- Person-owned remote MCP credentials use the same vault cipher, with no reveal/grant door.
CREATE TABLE IF NOT EXISTS granola_connections(
 actor TEXT PRIMARY KEY, id TEXT NOT NULL, email TEXT NOT NULL,
 ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, metadata_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credential_grants(
 id TEXT PRIMARY KEY, credential_id TEXT NOT NULL REFERENCES credentials(id),
 subject TEXT NOT NULL, granted_by TEXT NOT NULL, parent_id TEXT REFERENCES credential_grants(id),
 created TEXT NOT NULL, revoked TEXT, revoked_by TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS credential_grant_active ON credential_grants(credential_id,subject) WHERE revoked IS NULL;
CREATE TABLE IF NOT EXISTS archives(
 source TEXT PRIMARY KEY, digest TEXT NOT NULL, imported TEXT NOT NULL,
 owner TEXT, kind TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
 id TEXT PRIMARY KEY, visibility TEXT NOT NULL, collection TEXT NOT NULL,
 payload_json TEXT NOT NULL, digest TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_versions(
 id TEXT NOT NULL, digest TEXT NOT NULL, payload_json TEXT NOT NULL, imported TEXT NOT NULL,
 PRIMARY KEY(id,digest));
CREATE TABLE IF NOT EXISTS blobs(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL,
 name TEXT NOT NULL, content_type TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS blobs_by_digest ON blobs(digest,size);
CREATE TABLE IF NOT EXISTS backup_verified_blobs(
 bucket TEXT NOT NULL, sha256 TEXT NOT NULL, key TEXT NOT NULL, verified_at TEXT NOT NULL,
 PRIMARY KEY(bucket,sha256));
CREATE TABLE IF NOT EXISTS media_control(
 meeting_id TEXT PRIMARY KEY REFERENCES meetings(id), version INTEGER NOT NULL DEFAULT 1,
 deleted_at TEXT, next_seq INTEGER NOT NULL DEFAULT 0, received_bytes INTEGER NOT NULL DEFAULT 0,
 last_chunk_at TEXT);
CREATE TABLE IF NOT EXISTS media_assets(
 meeting_id TEXT NOT NULL REFERENCES meetings(id), blob_id TEXT NOT NULL REFERENCES blobs(id),
 kind TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT -1,
 PRIMARY KEY(meeting_id,blob_id));
CREATE UNIQUE INDEX IF NOT EXISTS media_audio_sequence ON media_assets(meeting_id,seq) WHERE kind='audio';
CREATE TABLE IF NOT EXISTS import_refs(
 source TEXT NOT NULL, external_id TEXT NOT NULL,
 meeting_id TEXT NOT NULL UNIQUE REFERENCES meetings(id), runner_id TEXT NOT NULL,
 created TEXT NOT NULL, PRIMARY KEY(source,external_id));
CREATE TABLE IF NOT EXISTS task_assets(
 task_id TEXT NOT NULL REFERENCES tasks(id), blob_id TEXT NOT NULL REFERENCES blobs(id),
 PRIMARY KEY(task_id,blob_id));
CREATE TABLE IF NOT EXISTS message_assets(
 message_id TEXT NOT NULL REFERENCES messages(id), blob_id TEXT NOT NULL REFERENCES blobs(id),
 PRIMARY KEY(message_id,blob_id));
CREATE TABLE IF NOT EXISTS service_jobs(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, resource_id TEXT NOT NULL, requested_by TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', payload_json TEXT NOT NULL DEFAULT '{}',
 attempt INTEGER NOT NULL DEFAULT 0, runner_id TEXT REFERENCES runners(id),
 token_hash TEXT, lease_until TEXT, result_json TEXT, error TEXT,
 created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS service_job_active_resource ON service_jobs(kind,resource_id)
 WHERE state IN ('queued','running');
CREATE TABLE IF NOT EXISTS learnings(
 id TEXT PRIMARY KEY, integration TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL,
 created TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS learnings_by_integration ON learnings(integration,created);
""" + QUEUE_TRIGGER + ";\n"

# The Slack gateway's own tables (backend/slack_gateway.py, docs/slack-gateway.md). An event is
# persisted before its envelope is acknowledged and is unique on Slack's event id and on the
# message it carries; `thread_ts` is the context key (a channel thread's root, "" for a whole
# DM) and `reply_ts` where a reply is posted; a thread maps to one hub conversation per bot; a
# post is one attempt at mirroring a bot's reply, with the state an operator reconciles.
# Every message in a channel Tico is in is stored too (state `stored`, `author` human or bot,
# edits and deletions applied in place, nothing ever pruned); `slack_reads` is each reader
# bot's cursor per channel, moved only in the transaction that delivers a digest, and
# `slack_digests` is what each delivery covered (docs/slack-gateway.md, Channels).
SLACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_events(
 event_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, channel TEXT NOT NULL,
 channel_kind TEXT NOT NULL, thread_ts TEXT NOT NULL, reply_ts TEXT NOT NULL DEFAULT '',
 ts TEXT NOT NULL, user_id TEXT NOT NULL,
 event_type TEXT NOT NULL, text TEXT NOT NULL, received TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'received', actor TEXT, reason TEXT, routing_json TEXT,
 processed TEXT, author TEXT NOT NULL DEFAULT 'human', author_name TEXT,
 edited TEXT, deleted TEXT, updated TEXT, UNIQUE(channel, ts));
CREATE INDEX IF NOT EXISTS slack_events_thread ON slack_events(channel, thread_ts, ts);
CREATE INDEX IF NOT EXISTS slack_events_state ON slack_events(state, received);
CREATE TABLE IF NOT EXISTS slack_reads(
 channel TEXT NOT NULL, reader TEXT NOT NULL, last_ts TEXT NOT NULL DEFAULT '',
 last_run TEXT, digests INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(channel, reader));
CREATE TABLE IF NOT EXISTS slack_digests(
 id TEXT PRIMARY KEY, reader TEXT NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 message_id TEXT NOT NULL REFERENCES messages(id), channels_json TEXT NOT NULL,
 event_ids_json TEXT NOT NULL, count INTEGER NOT NULL, skipped INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS slack_digests_reader ON slack_digests(reader, created);
CREATE TABLE IF NOT EXISTS slack_threads(
 channel TEXT NOT NULL, thread_ts TEXT NOT NULL, bot TEXT NOT NULL,
 conversation_id TEXT NOT NULL REFERENCES conversations(id), created TEXT NOT NULL,
 last_routed TEXT, PRIMARY KEY(channel, thread_ts, bot));
CREATE INDEX IF NOT EXISTS slack_threads_conversation ON slack_threads(conversation_id);
CREATE TABLE IF NOT EXISTS slack_posts(
 message_id TEXT PRIMARY KEY REFERENCES messages(id), channel TEXT NOT NULL,
 thread_ts TEXT NOT NULL, bot TEXT NOT NULL, text TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'ready', attempts INTEGER NOT NULL DEFAULT 0, next_attempt TEXT,
 slack_ts TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS slack_posts_state ON slack_posts(state, next_attempt);
CREATE INDEX IF NOT EXISTS slack_task_completion_notices ON messages(
 json_extract(refs_json,'$.task'), json_extract(refs_json,'$.task_completion.status'), created)
 WHERE json_extract(refs_json,'$.task_completion.notify')=1;
"""
SCHEMA += SLACK_SCHEMA


def refused(c, identity, exc, savepoint="domain_write"):
    """Undo a refused domain write, keep its audit (and rule 8's count), and say what answers it. `savepoint` is the
    write undone: the whole request's, or one task's of a bulk change (app.py task_bulk), which goes on to the next."""
    c.execute("ROLLBACK TO " + savepoint)
    c.execute("RELEASE " + savepoint)
    from . import strategy_acts
    if isinstance(exc, strategy_acts.Proposed):
        # Not a mistake to count toward quarantine: what stays the owner's becomes their proposal instead.
        strategy_acts.settle(c, exc)
        return Problem(exc.rule, exc.detail, 409)
    # Gate notifications survive the refused move, while its step and reviewer wake roll back.
    if getattr(exc, "review_gate", None):
        from . import review_gate
        review_gate.notify(c, identity.actor, exc.review_gate)
        # A refused review move is retried by Tico once what refused it clears (backend/move_retry.py).
        from . import move_retry
        move_retry.remember(c, identity.actor, exc.review_gate)
    if notice := getattr(exc, "task_hold", None):
        from .task_blocked import hold
        hold(c, notice["task"], notice["reason"], notice["kind"])
    # Preserve refusal auditing, but never a partial domain operation.
    token = H.PRIVATE_WRITE.set(getattr(exc, 'private', False))
    try:
        H.refuse(c, identity.actor, exc.rule, exc.detail, exc.severity, task=getattr(exc, 'task', None))
    except H.Refused:
        pass
    finally:
        H.PRIVATE_WRITE.reset(token)
    return Problem(exc.rule, exc.detail, 403 if exc.rule in ("identity", "label", "escape", "quarantined", "close") else 422,
                   retryable=getattr(exc, "retryable", False))


# A client retries a failed write with the same Idempotency-Key within seconds, so two
# days of stored responses is generous. Without a bound the table kept every mutating
# request's response forever: most of the database file.
IDEMPOTENCY_RETENTION_HOURS = 48
MAIL_RETENTION_DAYS = 180
# Polls carry nothing a retry could double: a replayed heartbeat or lease renewal just
# answers again, and an empty claim handed out no lease. They arrive every 15 s per runner
# (the heartbeat reply carries every assignment), and grew to most of the database file. A claim that leased a job stays replayable so a lost reply cannot
# strand the lease.
# A running turn asks for steering input every few seconds, and almost every answer is empty; an answer that
# took input stays replayable. Output events replay by their own sequence numbers (execution.events), so a
# stored receipt for each batch only grew the table: together they were most of its writes.
def is_poll(operation, result):
    if operation in ("/api/v2/runners/heartbeat", "/api/v2/runners/performance"):
        return True
    if operation == "/api/v2/jobs/claim":
        return isinstance(result, dict) and result.get("attempt") is None
    if not operation.startswith("/api/v2/attempts/"):
        return False
    if operation.endswith("/inputs"):
        return isinstance(result, dict) and not result.get("messages")
    return operation.endswith(("/renew", "/events"))


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Health rows nothing writes any more. The old snapshot backup timers (backend/operations.py) are
# replaced by Litestream wherever the container entrypoint sets TICO_BACKUP_MODE, and Health reads
# that from backend/replication.py; `processing:<runner>` has no writer at all.
SNAPSHOT_BACKUP_ROWS = ("backup", "backup-daily", "backup-monthly", "restore-check")


def retire_health_rows(c):
    """Delete health rows from retired mechanisms so they are neither shown nor alerted on. Idempotent."""
    c.execute("DELETE FROM service_health WHERE service LIKE 'processing:%'")
    if os.environ.get("TICO_BACKUP_MODE"):
        c.execute(f"DELETE FROM service_health WHERE service IN ({','.join('?' * len(SNAPSHOT_BACKUP_ROWS))})",
                  SNAPSHOT_BACKUP_ROWS)


def sweep_idempotency(store, now=None, batch=5000):
    """Delete idempotency rows past the retry window in short transactions; returns the count.

    Each batch is its own write transaction so the API's writers only ever wait for a
    few thousand deletions, never for the whole backlog.
    """
    cutoff = H.shift(now or H.now(), hours=-IDEMPOTENCY_RETENTION_HOURS)
    deleted = 0
    while True:
        with store.transaction() as c:
            removed = c.execute("DELETE FROM idempotency WHERE rowid IN "
                                "(SELECT rowid FROM idempotency WHERE created<? LIMIT ?)", (cutoff, batch)).rowcount
        deleted += removed
        if removed < batch:
            return deleted


DELTA_RETENTION_DAYS = 1
DELTA_SWEEP_MAX = 200_000     # rows one hourly pass deletes at most, so a first backlog drains over hours


def sweep_deltas(store, now=None, batch=5000, most=DELTA_SWEEP_MAX):
    """Delete the streamed text pieces (`attempt_events` kind 'delta', not a model's thought) of runs finished more
    than DELTA_RETENTION_DAYS ago, in short transactions like `sweep_idempotency`; returns the count.

    A run's pieces go only when its reply is whole elsewhere: `attempts.final_text` or a final `message` event holds
    it. A run still held for review (a job 'uncertain') keeps them: review_jobs shows its output, and
    auto_reconcile reads the deltas (hop_failed, empty_fallback_interruption) to decide. The pieces only typed the
    live reply (turns.reply_parts, read while a run is live). Thought deltas stay: a turn's steps show them (turns.py).
    Attempts are walked in creation order from a mark kept in registry_metadata, so a pass never rescans the table;
    one created before the cutoff and still unfinished, or not whole, is passed over for good (its pieces stay)."""
    at = now or H.now()
    cutoff = H.shift(at, days=-DELTA_RETENTION_DAYS)
    created_before = H.shift(at, days=-DELTA_RETENTION_DAYS - 1)     # a run finishing a day after it began is late
    with store.read() as c:
        mark = c.execute("SELECT value_json FROM registry_metadata WHERE key='delta-sweep-mark'").fetchone()
    mark = json.loads(mark[0]) if mark else ["", ""]
    deleted = 0
    while deleted < most:
        with store.read() as c:
            rows = c.execute(
                "SELECT a.id,a.created,a.finished,"
                "((a.final_text IS NOT NULL AND a.final_text<>'') OR EXISTS(SELECT 1 FROM attempt_events m "
                "WHERE m.attempt_id=a.id AND m.kind='message' AND json_valid(m.payload_json) "
                "AND json_extract(m.payload_json,'$.final')=1)) "
                "AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.attempt_id=a.id AND j.state='uncertain') AS whole "
                "FROM attempts a WHERE a.created<? AND (a.created,a.id)>(?,?) ORDER BY a.created,a.id LIMIT 200",
                (created_before, *mark)).fetchall()
        done = [r["id"] for r in rows if r["whole"] and r["finished"] and r["finished"] < cutoff]
        while done and deleted < most:
            with store.transaction() as c:
                removed = c.execute(
                    "DELETE FROM attempt_events WHERE id IN (SELECT id FROM attempt_events WHERE attempt_id IN (%s) "
                    "AND kind='delta' AND CASE WHEN json_valid(payload_json) THEN "
                    "json_extract(payload_json,'$.delta_kind') END IS NOT 'thought' LIMIT ?)"
                    % ",".join("?" * len(done)), (*done, batch)).rowcount
            deleted += removed
            if removed < batch:
                break
        else:
            if done:
                break                   # the cap was reached inside this group: it is taken up again next pass
        if not rows:
            break
        mark = [rows[-1]["created"], rows[-1]["id"]]
        with store.transaction() as c:
            c.execute("INSERT INTO registry_metadata VALUES('delta-sweep-mark',?) "
                      "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(mark),))
        if len(rows) < 200:
            break
    return deleted


TOKEN_RETENTION_DAYS = 7


def sweep_tokens(store, now=None, batch=5000, most=DELTA_SWEEP_MAX):
    """Delete the `attempt_events` kind 'tokens' rows of runs finished more than TOKEN_RETENTION_DAYS ago, in short
    transactions like `sweep_deltas`; returns the count.

    Money and usage never come from these rows: a run's totals are its turns row (input_tokens, cached_tokens,
    output_tokens, est_cost_usd, written by turn_finish), turn_usage_segments and attempts.result_json. The rows go only
    once that turn is finished with its totals written, and no job of the run is 'uncertain': auto_reconcile's hop_failed
    and review_jobs read them there. Attempts are walked in creation order from the mark 'tokens-sweep-mark', as
    sweep_deltas does; one still unfinished when the walk passes it keeps its rows."""
    at = now or H.now()
    cutoff = H.shift(at, days=-TOKEN_RETENTION_DAYS)
    created_before = H.shift(at, days=-TOKEN_RETENTION_DAYS - 1)     # a run finishing a day after it began is late
    with store.read() as c:
        mark = c.execute("SELECT value_json FROM registry_metadata WHERE key='tokens-sweep-mark'").fetchone()
    mark = json.loads(mark[0]) if mark else ["", ""]
    deleted = 0
    while deleted < most:
        with store.read() as c:
            rows = c.execute(
                "SELECT a.id,a.created,a.finished,EXISTS(SELECT 1 FROM turns t WHERE t.id=a.id AND t.finished IS NOT NULL "
                "AND (t.input_tokens IS NOT NULL OR t.usage_reason IS NOT NULL)) "
                "AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.attempt_id=a.id AND j.state='uncertain') AS recorded "
                "FROM attempts a WHERE a.created<? AND (a.created,a.id)>(?,?) ORDER BY a.created,a.id LIMIT 200",
                (created_before, *mark)).fetchall()
        if not rows:
            break
        done = [r["id"] for r in rows if r["recorded"] and r["finished"] and r["finished"] < cutoff]
        while done:
            with store.transaction() as c:
                removed = c.execute("DELETE FROM attempt_events WHERE id IN (SELECT id FROM attempt_events "
                                    "WHERE attempt_id IN (%s) AND kind='tokens' LIMIT ?)" % ",".join("?" * len(done)),
                                    (*done, batch)).rowcount
            deleted += removed
            if removed < batch:
                break
        mark = [rows[-1]["created"], rows[-1]["id"]]
        with store.transaction() as c:
            c.execute("INSERT INTO registry_metadata VALUES('tokens-sweep-mark',?) "
                      "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(mark),))
        if len(rows) < 200:
            break
    return deleted


# The `events` log keeps every action except these bookkeeping ones, which the hourly sweep drops once they are older
# than their tier's days. Not swept, because a reader needs old rows of them: `attempt.input.moved` (the privacy
# and provenance of an old run: live_privacy, read_deps, provenance_clock), `attempt.limited` (login_holds names the
# runtime of an old limited run that kept no limit_source), `judge.call` (a budget counts its day, decide_readout reads
# 31 days, and the row repeats the cost), `mcp.call` (tool-call stats up to 90 days, and the weekly SQL view),
# `github.token` and `inbound.read` (security and read audits, like `mail.read`). `credential.revealed` and
# `credential.sent_to_computer` are not swept: they are the credential's audit trail, and their repeats are written less
# often (credentials.record_use). Old rows of them stay; credential_uses keeps each last use, so nothing reads them for it.
# Seven days: writers only, or readers that look back no further than a week (bot.drain: deploy_draining reads the
# last hour, release_deploy_drains a week at most).
EVENT_NOISE_ACTIONS = ("sql.query", "wake.skipped", "message.delivered", "message.read", "status.set", "turn.start",
                       "turn.finish", "github.checks_read", "rate_limit.set", "attempt.fallback", "attempt.lease-held",
                       "job.suppress", "bot.drain", "operator.deployment-undrain", "listen.judgment",
                       "bot.repository_read")
EVENT_NOISE_RETENTION_DAYS = 7
# Thirty days: `message.notice` as before, and `github.pull_request`, which quiet_work.settle reads back to the start of
# the run that carried a task (no run has a length cap, so a 30-day keep is the most it can rely on). Its own mark,
# since the old one had walked past every github.pull_request row already on disk.
EVENT_SWEEP_ACTIONS = ("message.notice", "github.pull_request")
EVENT_RETENTION_DAYS = 30
# Rows per write transaction. A delete costs about 2 ms a row on TIDY (six indexes on events, cold pages), so 5000
# held the one writer for 6-10 s and every request queued behind it; 500 holds it for about one second.
EVENT_SWEEP_BATCH = 500
EVENT_SWEEP_MAX = 20000      # rows one hourly pass deletes at most; a larger backlog drains over later passes


def sweep_events(store, now=None, batch=EVENT_SWEEP_BATCH, most=EVENT_SWEEP_MAX):
    """Delete the bookkeeping rows past their tier's days (EVENT_NOISE_ACTIONS after EVENT_NOISE_RETENTION_DAYS,
    EVENT_SWEEP_ACTIONS after EVENT_RETENTION_DAYS); returns the count. The tiers share no action and keep separate
    marks, so each one's walk is its own. The noise tier goes first within the one `most` budget, so a large first
    backlog drains there and the 30-day tier follows."""
    at = now or H.now()
    deleted = 0
    for key, actions, days in (("events-noise-mark", EVENT_NOISE_ACTIONS, EVENT_NOISE_RETENTION_DAYS),
                               ("events-sweep-30-mark", EVENT_SWEEP_ACTIONS, EVENT_RETENTION_DAYS)):
        deleted += _sweep_event_tier(store, key, actions, H.shift(at, days=-days), batch, most - deleted)
    return deleted


def _sweep_event_tier(store, key, actions, cutoff, batch, most):
    """One tier of sweep_events: the `actions` rows older than `cutoff`, walked forward on `events_ts` from the mark
    kept under `key`; returns the count.

    The scan walks the index from the mark, so a pass reads only the time since the last one (and not the kept rows
    before it again). The scan runs outside the write lock; each batch's delete is its own short transaction, like
    `sweep_idempotency`. Events are only ever written with the current time, so once a pass has reached the cutoff,
    nothing older than it is left behind."""
    if most <= 0:
        return 0
    marks = ",".join("?" * len(actions))
    with store.read() as c:
        saved = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (key,)).fetchone()
    mark = json.loads(saved[0]) if saved else ""
    deleted = 0
    while deleted < most:
        with store.read() as c:
            rows = c.execute(f"SELECT rowid,ts FROM events INDEXED BY events_ts WHERE ts>=? AND ts<? "
                             f"AND action IN ({marks}) ORDER BY ts LIMIT ?",
                             (mark, cutoff, *actions, batch)).fetchall()
        if not rows:
            mark = cutoff
            break
        with store.transaction() as c:
            removed = c.execute(f"DELETE FROM events WHERE rowid IN ({','.join('?' * len(rows))})",
                                [row["rowid"] for row in rows]).rowcount
        deleted += removed
        mark = rows[-1]["ts"]
        if len(rows) < batch:
            mark = cutoff
            break
        if not removed:
            break               # the rows left the table under the scan: the next pass starts again from the mark
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                  (key, json.dumps(mark)))
    return deleted


# Credential use rows written before credentials.record_use kept one per run, not one per USE_WINDOW. This one-time
# trim brings them to that grain: per (credential, actor, bot), in ts order, a row is kept when it is the pair's first
# or USE_WINDOW after the last kept one, which is the rule record_use applies from now on. A row record_use always
# writes (a reveal with no attempt, a computer send by a model key) is kept and counts as the pair's last write, as it
# did then. Person reveals are never touched. Rows at or after the first pass's time are not walked: the deploy's rows
# are already at the new grain. Passes resume from the credential after the last finished one (registry_metadata).
# Paced, not back to back: a batch of 500 deletes plus the WAL checkpoint it triggers held the writer gate for 5-7 s on
# TIDY's first pass (scheduler.py:437), so writers waited up to 9 s against the 10 s WRITE_WAIT. 200 rows per write, then
# a pause, lets writers in between batches.
TRIM_BATCH = 200           # deletes per write transaction
TRIM_PAUSE = 0.5           # seconds slept after each write transaction
TRIM_PAGE = 2000           # rows read per read transaction
TRIM_READ_PAUSE = 0.05     # seconds slept between read pages
TRIM_MAX = 50_000          # deletes one hourly pass makes at most
_sleep = time.sleep        # the pauses go through this so tests can run without waiting
TRIM_ACTIONS = ("credential.revealed", "credential.sent_to_computer")
TRIM_MARK = "credential-use-trim"


def _use_key(actor, detail_json):
    """(bot, windowed) of one row, as credentials.record_use keys it and decides whether it writes."""
    try:
        detail = json.loads(detail_json) if detail_json else {}
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    bot = str(detail.get("bot") or "") or (actor[4:] if actor.startswith("bot:") else "")
    windowed = detail.get("use") == "watcher" or bool(detail.get("attempt"))
    return bot, windowed


def _trim_stream(store, action, target, before, page):
    """One action's rows of one credential before `before`, oldest first, a page per read transaction. Keyset on ts
    (events_action_target: action, target, ts); ids already yielded at the page's last ts are skipped on the next."""
    last, seen = "", set()
    while True:
        limit = page + len(seen)
        with store.read() as c:
            rows = c.execute("SELECT rowid,id,ts,actor,detail_json FROM events WHERE action=? AND target=? "
                             "AND ts>=? AND ts<? ORDER BY ts LIMIT ?", (action, target, last, before, limit)).fetchall()
        fresh = [row for row in rows if not (row["ts"] == last and row["id"] in seen)]
        yield from fresh
        if len(rows) < limit or not fresh:
            return
        _sleep(TRIM_READ_PAUSE)
        last = rows[-1]["ts"]
        seen = {row["id"] for row in rows if row["ts"] == last}


def _trim_target(store, target, before, batch, budget, page):
    """Walk one credential's rows in ts order and delete the ones record_use would not have written. Returns
    (deleted, finished). When `budget` runs out first the walk stops, and the next pass walks the credential again:
    the rows it kept decide the same again, so nothing already passed is deleted twice."""
    from .credentials import USE_WINDOW
    last, doomed, deleted = {}, [], 0
    streams = [_trim_stream(store, action, target, before, page) for action in TRIM_ACTIONS]
    for row in heapq.merge(*streams, key=lambda row: (row["ts"], row["id"])):
        actor = row["actor"] or ""
        if actor.startswith("human:"):
            continue
        bot, windowed = _use_key(actor, row["detail_json"])
        key = (actor, bot)
        prev = last.get(key)
        gap = None
        if prev is not None and windowed:
            at, was = H.parse_ts(row["ts"]), H.parse_ts(prev)
            gap = (at - was).total_seconds() if at and was else None
        if not windowed or prev is None or gap is None or gap >= USE_WINDOW:
            last[key] = row["ts"]
            continue
        doomed.append(row["rowid"])
        if len(doomed) >= min(batch, budget - deleted):
            deleted += _trim_flush(store, doomed)
            doomed = []
            if deleted >= budget:
                return deleted, False
    if doomed:
        deleted += _trim_flush(store, doomed)
    return deleted, True


def _trim_flush(store, rowids):
    with store.transaction() as c:
        deleted = c.execute(f"DELETE FROM events WHERE rowid IN ({','.join('?' * len(rowids))})", rowids).rowcount
    _sleep(TRIM_PAUSE)
    return deleted


def _trim_next_target(store, after):
    """The next credential after `after` (in id order) with a row of a trimmed action, or None."""
    found = []
    for action in TRIM_ACTIONS:
        with store.read() as c:
            row = c.execute("SELECT target FROM events WHERE action=? AND target>? ORDER BY target LIMIT 1",
                            (action, after)).fetchone()
        if row:
            found.append(row[0])
    return min(found) if found else None


def _trim_save(store, mark):
    with store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                  (TRIM_MARK, json.dumps(mark)))


def sweep_credential_uses(store, now=None, batch=TRIM_BATCH, most=TRIM_MAX, page=TRIM_PAGE):
    """One pass of the credential-use trim (see TRIM_BATCH above): deletes at most `most` rows and returns the count.
    The mark holds the pass's `before` (fixed at the first pass), the last finished credential, and `done` once every
    credential is finished; a finished trim costs one read. Each pass is a few read transactions and one short write
    per `batch` rows, so writers wait about as long as they do for sweep_events."""
    with store.read() as c:
        saved = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (TRIM_MARK,)).fetchone()
    mark = json.loads(saved[0]) if saved else None
    if mark and mark.get("done"):
        return 0
    if mark is None:
        mark = {"before": now or H.now(), "after": ""}
        _trim_save(store, mark)
    before, after, deleted = mark["before"], mark["after"], 0
    while deleted < most:
        target = _trim_next_target(store, after)
        if target is None:
            _trim_save(store, {"before": before, "after": after, "done": True})
            break
        count, finished = _trim_target(store, target, before, batch, most - deleted, page)
        deleted += count
        if not finished:
            break
        after = target
        _trim_save(store, {"before": before, "after": after})
    return deleted


# Pages returned to the disk per write transaction: 2000 x 4 KB = 8 MB. The writer gate is held for one step at a time.
VACUUM_STEP_PAGES = 2000
# Free pages kept in the file for reuse, so the next writes take them without growing the file again.
VACUUM_FLOOR_PAGES = 1000
VACUUM_PASS_MAX = 256 * 1024 * 1024      # bytes one hourly pass returns at most; the rest waits for the next pass
_vacuum_hint_logged = False


def reclaim_free_pages(store, step=VACUUM_STEP_PAGES, floor=VACUUM_FLOOR_PAGES, most=VACUUM_PASS_MAX):
    """Return the free pages the sweeps leave behind to the disk (PRAGMA incremental_vacuum); returns the pages returned.

    Only a file with auto_vacuum=INCREMENTAL (hubdb.connect sets it on every new file) is touched, and any other file
    costs one read. Never a full VACUUM. Each step is its own write transaction, and the pass stops at `floor` free
    pages or `most` bytes, whichever comes first."""
    global _vacuum_hint_logged
    with store.read() as c:
        mode = c.execute("PRAGMA auto_vacuum").fetchone()[0]
        if mode != 2:
            if not _vacuum_hint_logged:
                _vacuum_hint_logged = True
                logging.getLogger(__name__).info(
                    "hub database has auto_vacuum=%s: a VACUUM would let the sweeps' freed pages go back to the disk",
                    mode)
            return 0
        page = c.execute("PRAGMA page_size").fetchone()[0]
    budget = most // page
    returned = 0
    while returned < budget:
        # The freelist is read inside the write transaction, so a concurrent delete's pages are counted too.
        with store.transaction() as c:
            free = c.execute("PRAGMA freelist_count").fetchone()[0]
            n = min(step, free - floor, budget - returned)
            if n <= 0:
                break
            removed = len(c.execute(f"PRAGMA incremental_vacuum({int(n)})").fetchall())
        returned += removed
        if not removed:
            break
    return returned


def sweep_mail(store, now=None, batch=5000):
    """Delete mail older than the retention window, including matching FTS rows.

    Same short-transaction batches as `sweep_idempotency` so API writers never wait
    for the whole mailbox.
    """
    days = getattr(store.settings, "mail_retention_days", None) or MAIL_RETENTION_DAYS
    at = H.parse_ts(now or H.now())
    cutoff = int(at.timestamp()) - int(days) * 86400
    deleted, touched = 0, set()
    while True:
        with store.transaction() as c:
            rows = c.execute("SELECT mailbox,msg_id FROM mail_messages WHERE epoch>0 AND epoch<? LIMIT ?",
                             (cutoff, batch)).fetchall()
            for row in rows:
                touched.add(row["mailbox"])
                c.execute("DELETE FROM mail_fts WHERE mailbox=? AND msg_id=?", (row["mailbox"], row["msg_id"]))
                c.execute("DELETE FROM mail_messages WHERE mailbox=? AND msg_id=?", (row["mailbox"], row["msg_id"]))
            deleted += len(rows)
        if len(rows) < batch:
            break
    # The counters the Mail page reads must not outlive the mail they describe: a mailbox whose
    # oldest month just aged out would otherwise keep claiming it until the next publish.
    for address in sorted(touched):
        with store.transaction() as c:
            c.execute("UPDATE mail_mailboxes SET "
                      "message_count=(SELECT count(*) FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL),"
                      "oldest_epoch=(SELECT min(epoch) FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL),"
                      "newest_epoch=(SELECT max(epoch) FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL) "
                      "WHERE address=?", (address, address, address, address))
    return deleted


def readiness_document(value):
    """Return the canonical runner-readiness shape, including legacy heartbeats."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, (str, bytes)):
        try:
            value = orjson.loads(value)
        except (TypeError, ValueError):
            value = {}
    value = value if isinstance(value, dict) else {}
    if value.get("schema_version") in (0, 1) and isinstance(value.get("bots"), dict):
        return value
    return {"schema_version": 0, "runtimes": {},
            "bots": {str(bot): {"ready": ready is True, "runtime": "", "model": "",
                                "repository_present": ready is True, "repository_revision": "",
                                "configuration_valid": ready is True, "problems": []}
                     for bot, ready in value.items() if isinstance(ready, bool)}}


def bot_readiness(value, bot):
    row = readiness_document(value).get("bots", {}).get(bot)
    return row if isinstance(row, dict) else {"ready": False, "problems": ["No readiness report from this computer"]}


def repo_url(repo, github_owner=""):
    """The browsable Git address of a stored bot repository: a URL as given, `owner/name`
    under github.com, or a bare name under the environment's GitHub owner."""
    repo = str(repo or "").strip()
    if repo.startswith("http://") or repo.startswith("https://"):
        return repo
    if "/" in repo:
        return "https://github.com/" + repo
    if repo and github_owner:
        return "https://github.com/" + github_owner + "/" + repo
    return ""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def message_page(c, cid, *, before=None, since=None, limit=200):
    """Newest page, in display order; rowid breaks ties for messages in the same millisecond. A
    deleted task comment is left out: this page is also what a bot's run is handed."""
    clauses, args = ["conversation_id=?", "deleted_at IS NULL"], [cid]
    if before:
        anchor = c.execute("SELECT rowid FROM messages WHERE id=? AND conversation_id=?", (before, cid)).fetchone()
        if not anchor:
            raise Problem("cursor", "Message cursor does not belong to this conversation", 422)
        clauses.append("rowid<?")
        args.append(anchor[0])
    if since:
        clauses.append("created>?")
        args.append(since)
    rows = c.execute("SELECT * FROM messages WHERE " + " AND ".join(clauses) + " ORDER BY rowid DESC LIMIT ?",
                     (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    messages = [{**dict(r), "refs": json.loads(r["refs_json"] or "{}")} for r in reversed(rows[:limit])]
    return {"messages": messages, "has_more": more,
            "next_before": messages[0]["id"] if more and messages else None}


def task_message_page(c, task, **kwargs):
    """Messages about this task. A dedicated task thread returns the whole conversation;
    a shared chat room returns only rows tagged with this task id."""
    page = message_page(c, task["conversation_id"], **kwargs)
    conv = H.conversation(c, task["conversation_id"])
    if conv and conv.get("task_id") == task["id"]:
        return page
    page["messages"] = [m for m in page["messages"] if H.message_task_id(m) == task["id"]]
    return page


def seed_operator(slug, team, roster, owner):
    """Who runs a bot's machines on a first import: the person who claims that bot or its team
    in people.yaml, else the environment owner. A company-wide `*` claim is not a team claim."""
    wanted = {x for x in (slug, team) if x}
    claimed = [p for p in roster["people"] if wanted & set(p["primary_for"])]
    return claimed[0]["id"] if claimed else owner


class Problem(Exception):
    """A refusal the caller can act on. `extra` carries the one fact the page needs to offer the
    next move - the card a duplicate feature request already has, for instance - and is serialized
    beside the code and the detail. Nothing secret goes in it."""

    def __init__(self, code, detail, status=400, retryable=False, extra=None):
        super().__init__(detail)
        self.code, self.detail, self.status, self.retryable = code, detail, status, retryable
        self.extra = extra or {}


POOL_IDLE = 16     # read connections kept open between reads


def _pooled(base):
    """`base` for pooled reads: one a caller changed (a function, an authorizer, a progress handler, a pragma that
    lasts) is closed after its read instead of reused, so no state reaches another caller's read."""
    def taint(name):
        method = getattr(base, name)

        def changed(self, *args, **kwargs):
            self.tainted = True
            if hasattr(self, "read_cache"):
                self.read_cache.clear()
            return method(self, *args, **kwargs)
        return changed

    names = ("create_function", "create_aggregate", "create_window_function", "create_collation", "set_authorizer",
             "set_progress_handler", "set_trace_callback", "enable_load_extension", "setlimit", "executescript")
    body = {name: taint(name) for name in names if hasattr(base, name)}
    def clear(self):
        if hasattr(self, "read_cache"):
            self.read_cache.clear()

    def execute(self, statement, *args, **kwargs):
        # Explicit transaction control starts a new snapshot even without row changes.
        if (statement.lstrip().split(None, 1) or [""])[0].upper() in ("BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "CREATE", "ALTER", "DROP"):
            clear(self)
        return base.execute(self, statement, *args, **kwargs)

    def finish(name):
        def completed(self, *args, **kwargs):
            clear(self)
            return getattr(base, name)(self, *args, **kwargs)
        return completed

    body.update(execute=execute, commit=finish("commit"), rollback=finish("rollback"))
    body["tainted"] = False
    body["pool_file"] = None
    body["tables_seen"] = {}    # {pool_file: tables seen to exist}, shared by this store's connections (hubdb._has_table)
    return type("Pooled" + base.__name__, (base,), body)


# Seconds a writer waits for the gate, then for SQLite's lock (busy_timeout, other processes). Together they bound a
# request's wait for the lock: past that it answers 503 `busy` with Retry-After (app.py storage_handler) instead of
# holding one of the 40 request threads for a minute while client retries queue behind it.
WRITE_WAIT = 10


class WriterGate:
    """FIFO admission: a scheduler opening its next transaction goes behind already waiting requests."""

    def __init__(self, timeout=WRITE_WAIT):
        self.lock = threading.Lock()
        self.queue = deque()
        self.timeout = timeout

    def acquire(self):
        ready = threading.Event()
        with self.lock:
            self.queue.append(ready)
            if len(self.queue) == 1:
                ready.set()
        try:
            if not ready.wait(self.timeout):
                raise sqlite3.OperationalError("database is locked")
        except BaseException:
            with self.lock:
                first = self.queue[0] is ready
                self.queue.remove(ready)
                if first and self.queue:
                    self.queue[0].set()
            raise

    def release(self):
        with self.lock:
            self.queue.popleft()
            if self.queue:
                self.queue[0].set()


class Store:
    def __init__(self, settings, recorder=None):
        self.settings = settings
        # The flight recorder (backend/flight.py) times every statement and write transaction when one is given.
        self.recorder = recorder
        self.factory = sqlite3.Connection
        if recorder is not None:
            from . import flight
            self.factory = flight.connection_class(recorder)
            flight.ACTIVE = recorder        # times write transactions on connections opened outside the Store
        # Reads reuse their connections (`read`): a new one parses the whole schema before its first statement,
        # which on a large install cost more than most reads themselves.
        self.pooled = _pooled(self.factory)
        self.pool, self.pool_file, self.pool_lock = [], None, threading.Lock()
        # In-process writers queue before BEGIN instead of competing in SQLite's busy-handler sleeps.
        # Reads still use independent WAL snapshots; other processes remain governed by busy_timeout.
        self.writer = WriterGate()
        from .provenance_clock import Cache
        self.provenance_cache = Cache()
        self.roster_rows = {}       # {statement: (roster_clock count, rows)}, shared by this store's connections
        from .row_clock import Cache as RowCache
        self.row_cache = RowCache()

    def connect(self, factory=None, check_same_thread=True):
        c = sqlite3.connect(str(self.settings.db_path), timeout=WRITE_WAIT, isolation_level=None,
                            factory=factory or self.factory, check_same_thread=check_same_thread)
        H.leave_wal_for_litestream(c)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        # A writer waits out a backup checkpoint or another process's pass, but not so long that requests pile up
        # (WRITE_WAIT). hubdb.connect keeps 30 s: it opens the file at start-up, for migrations.
        c.execute(f"PRAGMA busy_timeout={WRITE_WAIT * 1000}")
        # In WAL mode NORMAL syncs at checkpoints, not on every commit inside the writer gate, where a sync
        # waiting behind other disk writes on the host held every writer for seconds. The database cannot
        # corrupt; an OS crash or power loss can lose the last commits, as with hubdb.connect.
        c.execute(f"PRAGMA synchronous={H.synchronous('NORMAL')}")
        # The 2 MB default page cache re-read hot index pages on every read of a multi-GB file. 16 MB per connection
        # (POOL_IDLE kept, a few dozen at peak) fills lazily; the mmap is the OS page cache, shared by all of them.
        c.execute("PRAGMA cache_size=-16384")
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA mmap_size=268435456")
        # The WAL file is cut back to this size after a checkpoint; a large write (a migration) otherwise keeps its peak.
        c.execute(f"PRAGMA journal_size_limit={H.WAL_SIZE_LIMIT}")
        return c

    def initialize(self, *, seed_market=True, adopt_legacy=False):
        from . import redesign_migration    # the one-time move to Automations, at the end of this method
        self.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        with H.connect(self.settings.db_path, adopt_legacy=adopt_legacy) as c:
            c.executescript(SCHEMA)
            from . import start_places as _start_places
            c.executescript(_start_places.SCHEMA)
            _start_places.restore_waits(c)
            from . import org_turns as _org_turns
            c.executescript(_org_turns.SCHEMA)
            from . import updates as _updates
            c.executescript(_updates.SCHEMA)
            _updates.ensure_schema(c)
            from . import update_feeds as _update_feeds
            c.executescript(_update_feeds.SCHEMA)
            from . import runner_versions as _runner_versions
            c.executescript(_runner_versions.SCHEMA)
            _runner_versions.ensure_schema(c)
            from . import files as _files
            c.executescript(_files.SCHEMA)
            from . import memory_history as _memory_history
            c.executescript(_memory_history.SCHEMA)
            from . import learnings as _learnings
            c.executescript(_learnings.SCHEMA)
            from . import stale_review as _stale_review
            c.executescript(_stale_review.SCHEMA)
            from . import stall_triage as _stall_triage
            c.executescript(_stall_triage.SCHEMA)
            from . import ask_ingest as _ask_ingest
            c.executescript(_ask_ingest.SCHEMA)
            from . import rule_suggestions as _rule_suggestions
            c.executescript(_rule_suggestions.SCHEMA)
            from . import system_routines as _system_routines
            c.executescript(_system_routines.SCHEMA)
            _system_routines.ensure(c)
            from . import thread_followups as _thread_followups
            _thread_followups.ensure(c)
            from . import thread_digest as _thread_digest
            _thread_digest.ensure(c)
            from . import task_rules as _task_rules
            c.executescript(_task_rules.SCHEMA)
            from . import audit_log as _audit_log
            c.executescript(_audit_log.SCHEMA)
            from . import teammate_access as _teammate_access
            _teammate_access.ensure(c)              # people's and groups' See/Read/Write (docs/permissions.md)
            from . import rules as _rules
            c.executescript(_rules.SCHEMA)
            _rules.ensure_schema(c)
            _rules.seed_dev(c)
            from . import check_pools as _check_pools
            _check_pools.ensure(c)
            _check_pools.seed(c)
            from . import step_flow as _step_flow
            _step_flow.ensure(c)
            _step_flow.seed(c)
            _rules.drop_step_noise(c)
            from . import owner_link as _owner_link
            c.executescript(_owner_link.SCHEMA)
            from . import docs as _docs
            _docs.ensure_schema(c, self.settings)
            from . import doc_sources as _doc_sources
            _doc_sources.ensure_schema(c)
            from . import bot_tools as _bot_tools
            c.executescript(_bot_tools.SCHEMA)
            from . import mail_settings as _mail_settings
            c.executescript(_mail_settings.SCHEMA)
            from . import company_storage as _company_storage
            c.executescript(_company_storage.SCHEMA)
            from . import tico_storage as _tico_storage
            c.executescript(_tico_storage.SCHEMA)
            from . import model_subscriptions as _model_subscriptions
            c.executescript(_model_subscriptions.SCHEMA)
            from . import google_connect as _google_connect
            _google_connect.ensure_schema(c)
            from . import support as _support
            c.executescript(_support.SCHEMA)
            from . import inbound as _inbound
            c.executescript(_inbound.SCHEMA)
            _inbound.ensure(c)
            from . import bills_address as _bills_address
            c.executescript(_bills_address.SCHEMA)
            from . import support_access as _support_access
            c.executescript(_support_access.SCHEMA)
            from . import credential_cards as _credential_cards
            c.executescript(_credential_cards.SCHEMA)
            from . import watchers as _watchers
            c.executescript(_watchers.SCHEMA)
            from . import cloud_machines as _cloud_machines
            c.executescript(_cloud_machines.SCHEMA)
            from . import browser_exit as _browser_exit
            c.executescript(_browser_exit.TRIGGERS)
            # A stop's reservation against the claim (backend/cloud_machines.py `reserve`); NULL is none.
            H.add_column(c, "cloud_machines", "stop_until", "REAL")
            from . import screening as _screening
            c.executescript(_screening.SCHEMA)
            from . import bot_task_tabs as _bot_task_tabs
            c.executescript(_bot_task_tabs.SCHEMA)
            # The sidebar's widgets: order, hidden and heights (Editable bot sidebar); NULL is the default four.
            H.add_column(c, "bot_task_tabs", "widgets_json", "TEXT")
            from . import learned as _learned
            c.executescript(_learned.SCHEMA)
            from . import cards as _cards
            c.executescript(_cards.SCHEMA)
            from . import task_manager as _task_manager
            c.executescript(_task_manager.SCHEMA)
            from . import bookkeeper as _bookkeeper
            c.executescript(_bookkeeper.SCHEMA)
            from . import health_monitor as _health_monitor
            c.executescript(_health_monitor.SCHEMA)
            # When a finding last woke the Health Monitor, and how bad it is: a rise wakes it (backend/health_monitor.py).
            H.add_column(c, "health_findings", "last_woke", "TEXT NOT NULL DEFAULT ''")
            H.add_column(c, "health_findings", "severity", "TEXT NOT NULL DEFAULT 'warn'")
            # The production checks' last answers and alert tasks (backend/invariants.py).
            from . import invariants as _invariants
            c.executescript(_invariants.SCHEMA)
            from . import slack_channels as _slack_channels
            c.executescript(_slack_channels.SCHEMA)
            from . import slack_threads as _slack_threads
            _slack_threads.ensure(c)
            from . import flight as _flight
            c.executescript(_flight.SCHEMA)
            for column, kind in (("sql_ms", "REAL"), ("wait_ms", "REAL"), ("queries", "INTEGER")):
                H.add_column(c, "flight_slow", column, kind)
            from . import performance as _performance
            c.executescript(_performance.SCHEMA)
            from . import invites as _invites
            c.executescript(_invites.SCHEMA)
            _updates.purge_rejected(c)
            # Which stored retry answers quote which message (backend/idempotency_messages.py).
            idempotency_messages.ensure(c)
            # Destinations saved in Settings > Listening (backend/listening.py).
            from . import listening as _listening
            _listening.ensure(c)
            # Tico Meet calls and their people (backend/calls.py): a call belongs to a thread.
            from . import calls as _calls
            _calls.ensure(c)
            # Each credential's newest use per (actor, bot) (credentials.last_used): credentials.record_use keeps it.
            H.backfill_credential_uses(c)
            c.execute("BEGIN IMMEDIATE")
            try:
                # Who may see, read and write to each bot (backend/bot_access.py); NULL is Open.
                # Checked on every start rather than numbered, like the indexes below.
                H.add_column(c, "bot_config", "access_json", "TEXT")
                # A bot's owners (its creator and any co-owners; the operator, the people above it and
                # the Admins are owners without being listed), who created it, and whether a computer
                # takes bots members made (backend/bot_access.py, docs/permissions.md).
                H.add_column(c, "bot_config", "bot_owners_json", "TEXT")
                H.add_column(c, "bot_config", "created_by", "TEXT")
                H.add_column(c, "runners", "accepts_member_bots", "INTEGER NOT NULL DEFAULT 0")
                # Who may open a computer's screen from the app: off, view or control (backend/screen.py).
                H.add_column(c, "runners", "screen", "TEXT NOT NULL DEFAULT 'off'")
                # 1 on a computer enrolled since Screen got a first-offer default, until its first screen offer or a
                # person's choice; computers that existed before keep what they had.
                H.add_column(c, "runners", "screen_default_pending", "INTEGER NOT NULL DEFAULT 0")
                # Performance diagnostics: on unless a person turns them off (backend/performance.py).
                H.add_column(c, "runners", "performance", "TEXT NOT NULL DEFAULT 'on'")
                # When a computer said it is restarting after a person pressed Restart, from its heartbeat; NULL once
                # a heartbeat no longer says so (backend/health.py `restarting`).
                H.add_column(c, "runners", "restart_since", "TEXT")
                # A starter bot is `needs_onboarding` until it says its setup is done, then
                # `onboarded`; NULL for every other bot (backend/onboarding.py).
                H.add_column(c, "bot_config", "onboarding_state", "TEXT")
                H.add_column(c, "settings_changes", "via", "TEXT")
                H.add_column(c, "tasks", "request_id", "TEXT")
                # When an archived bot's agent last used its still-valid credential (backend/agents.py).
                H.add_column(c, "agents", "archived_seen", "TEXT")
                # The pairing secret's public key: a held agent credential is sealed to it (backend/agents.py). A
                # credential held in plaintext by the release before that, and no longer collectable, goes now.
                H.add_column(c, "agent_pairings", "token_key", "TEXT NOT NULL DEFAULT ''")
                from .agents import wipe_unclaimable
                wipe_unclaimable(c)
                # Who added each credential, for the record. Older rows take it from their audit event once, when the
                # column arrives. It gives no rights: those come from `member_stored` (backend/credentials.py `owned`).
                if "created_by" not in {row[1] for row in c.execute("PRAGMA table_info(credentials)")}:
                    H.add_column(c, "credentials", "created_by", "TEXT")
                    c.executemany("UPDATE credentials SET created_by=? WHERE id=? AND created_by IS NULL",
                                  [(r[0], r[1]) for r in c.execute(
                                      "SELECT actor,target FROM events WHERE action='credential.created' ORDER BY ts")])
                # A credential a member stored for their own bot (`Vault.write`'s member path). Every row already here
                # starts at 0: on a server that ran the backfill above, `created_by` named every admin who ever added a
                # company credential as its owner, and nothing recorded which rows a member stored, so none stay theirs.
                H.add_column(c, "credentials", "member_stored", "INTEGER NOT NULL DEFAULT 0")
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=1").fetchone():
                    H.add_column(c, "tasks", "version", "INTEGER NOT NULL DEFAULT 1")
                    H.add_column(c, "tasks", "acceptance_json", "TEXT NOT NULL DEFAULT '[]'")
                    c.execute("INSERT INTO cloud_migrations VALUES(1,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=2").fetchone():
                    columns = {row[1] for row in c.execute("PRAGMA table_info(bot_config)")}
                    if "owner_ids_json" not in columns:
                        c.execute("ALTER TABLE bot_config ADD COLUMN owner_ids_json TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(2,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=3").fetchone():
                    # The version marker makes the new transition/history contract visible
                    # to release checks. CREATE TABLE IF NOT EXISTS above performs the
                    # additive migration for both fresh and existing databases.
                    c.execute("INSERT INTO cloud_migrations VALUES(3,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=4").fetchone():
                    columns = {row[1] for row in c.execute("PRAGMA table_info(bot_transitions)")}
                    if "without_checkpoint" not in columns:
                        c.execute("ALTER TABLE bot_transitions ADD COLUMN without_checkpoint INTEGER NOT NULL DEFAULT 0")
                    c.execute("INSERT INTO cloud_migrations VALUES(4,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=5").fetchone():
                    c.execute("DROP INDEX IF EXISTS one_active_personal_room")
                    c.execute("DROP INDEX IF EXISTS one_active_shared_room")
                    columns = {row[1] for row in c.execute("PRAGMA table_info(conversations)")}
                    if "scope" not in columns:
                        c.execute("ALTER TABLE conversations ADD COLUMN scope TEXT NOT NULL DEFAULT 'direct'")
                    if "owner_actor" not in columns:
                        c.execute("ALTER TABLE conversations ADD COLUMN owner_actor TEXT")
                    if "room_key" not in columns:
                        c.execute("ALTER TABLE conversations ADD COLUMN room_key TEXT")
                    # Existing one-human Tico threads are already private in practice. Mark
                    # that contract explicitly before the authorization layer starts using it.
                    for conversation in c.execute(
                            "SELECT id,kind,task_id,participants_json FROM conversations").fetchall():
                        participants = json.loads(conversation["participants_json"] or "[]")
                        scope, owner, room = ("task", None, None) if conversation["task_id"] else ("direct", None, None)
                        humans = [p for p in participants if str(p).startswith("human:")]
                        if (conversation["kind"] == "chat" and not conversation["task_id"]
                                and "bot:coo" in participants and len(humans) == 1):
                            scope, owner, room = "personal", humans[0], "coo"
                        c.execute("UPDATE conversations SET scope=?,owner_actor=?,room_key=? WHERE id=?",
                                  (scope, owner, room, conversation["id"]))
                    # A person gets one current Tico control room. Older duplicates remain as
                    # readable archives and are never merged with another person's history.
                    duplicate_keys = c.execute(
                        "SELECT owner_actor,room_key FROM conversations WHERE scope='personal' "
                        "AND closed_at IS NULL GROUP BY owner_actor,room_key HAVING count(*)>1").fetchall()
                    for owner, room in duplicate_keys:
                        rows = c.execute(
                            "SELECT id FROM conversations WHERE scope='personal' AND owner_actor=? "
                            "AND room_key=? AND closed_at IS NULL "
                            "ORDER BY COALESCE(last_message_at,created) DESC,id DESC", (owner, room)).fetchall()
                        for old in rows[1:]:
                            c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), old["id"]))
                    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_personal_room "
                              "ON conversations(owner_actor,room_key) "
                              "WHERE scope='personal' AND closed_at IS NULL")
                    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_shared_room "
                              "ON conversations(room_key) WHERE scope='shared' AND closed_at IS NULL")
                    c.execute("INSERT INTO cloud_migrations VALUES(5,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=6").fetchone():
                    columns = {row[1] for row in c.execute("PRAGMA table_info(bot_config)")}
                    additions = {
                        "description": "TEXT NOT NULL DEFAULT ''",
                        "reports_to": "TEXT",
                        "repo": "TEXT NOT NULL DEFAULT ''",
                        "thread_mode": "TEXT",
                        "definition_updated": "TEXT",
                        "definition_updated_by": "TEXT",
                    }
                    for name, declaration in additions.items():
                        if name not in columns:
                            c.execute(f"ALTER TABLE bot_config ADD COLUMN {name} {declaration}")
                    for row in c.execute(
                            "SELECT bc.bot,bc.config_json,b.model,b.effort FROM bot_config bc "
                            "JOIN bots b ON b.slug=bc.bot").fetchall():
                        config = json.loads(row["config_json"] or "{}")
                        # A model decision moves the previously seeded Grok
                        # workers from medium to high. Do this once for an existing cloud DB;
                        # subsequent choices remain backend-managed and are never reconciled
                        # from the bootstrap YAML.
                        if ((config.get("model") or row["model"]) == "grok-4.6"
                                and (config.get("reasoning_effort") or row["effort"]) == "medium"):
                            config["reasoning_effort"] = "high"
                            c.execute("UPDATE bots SET effort='high' WHERE slug=?", (row["bot"],))
                        mode = str(config.get("thread_mode") or
                                   ("personal" if row["bot"] == "coo" else "shared" if row["bot"] == "cpo" else "personal"))
                        c.execute(
                            "UPDATE bot_config SET config_json=?,description=?,reports_to=?,repo=?,thread_mode=?,"
                            "definition_updated=coalesce(definition_updated,?),"
                            "definition_updated_by=coalesce(definition_updated_by,?) WHERE bot=?",
                            (encode(config), str(config.get("description") or ""), config.get("reports_to"),
                             str(config.get("repo") or ("emp-" + row["bot"])), mode,
                             H.now(), H.KEEPER, row["bot"]))
                    c.execute("INSERT INTO cloud_migrations VALUES(6,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=7").fetchone():
                    # The live roster predates the Product Manager rename. Move only the
                    # exact legacy routing values; do not reconcile future database-managed
                    # bot definitions or overwrite subsequent roster choices from YAML.
                    row = c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()
                    if row:
                        people = json.loads(row[0] or "{}")
                        product = (people.get("teams") or {}).get("product") or {}
                        if product.get("root") == "cpo":
                            product["root"] = "product-manager"
                            people.setdefault("teams", {})["product"] = product
                        for person in people.get("people") or []:
                            if person.get("bot") == "cpo":
                                person["bot"] = "product-manager"
                        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'", (encode(people),))
                    c.execute("INSERT INTO cloud_migrations VALUES(7,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=8").fetchone():
                    # A bot cannot usefully execute a message it sent to itself. The former
                    # trigger queued those rows and could turn an accidental task ask into a
                    # reply cascade. Replace it explicitly because IF NOT EXISTS above does
                    # not update a trigger already present in an existing cloud database.
                    c.execute("DROP TRIGGER IF EXISTS queue_bot_message")
                    c.execute("CREATE TRIGGER queue_bot_message AFTER INSERT ON messages "
                              "WHEN NEW.to_actor LIKE 'bot:%' "
                              "AND COALESCE(NEW.from_actor, '') != NEW.to_actor BEGIN "
                              "INSERT INTO jobs(id,message_id,bot,created) "
                              "VALUES(NEW.id,NEW.id,substr(NEW.to_actor,5),NEW.created); END")
                    c.execute("UPDATE jobs SET state='completed' WHERE state='queued' AND EXISTS "
                              "(SELECT 1 FROM messages m WHERE m.id=jobs.message_id "
                              "AND m.from_actor=m.to_actor)")
                    c.execute("INSERT INTO cloud_migrations VALUES(8,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=9").fetchone():
                    # Routines a runner reads from a bot repository are marked 'repo' so the
                    # heartbeat sync can replace only its own rows. Rows imported from the
                    # legacy database keep a NULL source and are never rewritten.
                    columns = {row[1] for row in c.execute("PRAGMA table_info(schedules)")}
                    if "source" not in columns:
                        c.execute("ALTER TABLE schedules ADD COLUMN source TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(9,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=10").fetchone():
                    columns = {row[1] for row in c.execute("PRAGMA table_info(schedules)")}
                    for name, definition in {
                        "routine_key": "TEXT", "definition_json": "TEXT NOT NULL DEFAULT '{}'",
                        "source_repo": "TEXT NOT NULL DEFAULT ''", "source_revision": "TEXT NOT NULL DEFAULT ''",
                        "version": "INTEGER NOT NULL DEFAULT 0", "deleted_at": "TEXT", "updated_at": "TEXT"
                    }.items():
                        if name not in columns:
                            c.execute(f"ALTER TABLE schedules ADD COLUMN {name} {definition}")
                    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS routine_identity ON schedules(bot,routine_key)")
                    c.execute("INSERT INTO cloud_migrations VALUES(10,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=11").fetchone():
                    # Native live transcripts are written into the room without queueing a
                    # runner turn. Replace the trigger explicitly; IF NOT EXISTS above leaves
                    # an existing cloud database's older definition in place.
                    c.execute("DROP TRIGGER IF EXISTS queue_bot_message")
                    c.execute(QUEUE_TRIGGER)
                    c.execute("INSERT INTO cloud_migrations VALUES(11,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=12").fetchone():
                    # Spoken decisions and live tool lines join transcripts as room history
                    # that never queues a turn. Same explicit replacement as migration 11.
                    c.execute("DROP TRIGGER IF EXISTS queue_bot_message")
                    c.execute(QUEUE_TRIGGER)
                    c.execute("INSERT INTO cloud_migrations VALUES(12,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=13").fetchone():
                    # `import_refs` makes "one recording per outside call" a uniqueness
                    # constraint rather than a query (backend/imports.py). CREATE TABLE IF NOT
                    # EXISTS above performs the additive migration; the marker makes the new
                    # contract visible to release checks.
                    c.execute("INSERT INTO cloud_migrations VALUES(13,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=14").fetchone():
                    # A routine may run `on:` an event instead of a cron (`backend/routines.py emit`).
                    columns = {row[1] for row in c.execute("PRAGMA table_info(schedules)")}
                    if "event_name" not in columns:
                        c.execute("ALTER TABLE schedules ADD COLUMN event_name TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(14,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=15").fetchone():
                    # When a machine returns from a silence it must report in steadily before it
                    # is given work (`backend/execution.py waking`). NULL means a machine that has
                    # been here all along, so every enrolled machine keeps working through this
                    # migration and only a real disappearance starts a new waking period.
                    columns = {row[1] for row in c.execute("PRAGMA table_info(runners)")}
                    if "awake_since" not in columns:
                        c.execute("ALTER TABLE runners ADD COLUMN awake_since TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(15,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=16").fetchone():
                    from .harnesses import backfill_config
                    for row in c.execute(
                            "SELECT bc.bot,bc.config_json,b.runtime FROM bot_config bc "
                            "JOIN bots b ON b.slug=bc.bot").fetchall():
                        config, changed = backfill_config(json.loads(row["config_json"] or "{}"),
                                                          slug=row["bot"], runtime=row["runtime"])
                        if changed:
                            c.execute("UPDATE bot_config SET config_json=? WHERE bot=?",
                                      (encode(config), row["bot"]))
                    c.execute("INSERT INTO cloud_migrations VALUES(16,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=17").fetchone():
                    # CREATE TABLE IF NOT EXISTS above installs bot_sessions on fresh and
                    # existing databases. The marker makes the recovery-pack contract visible.
                    c.execute("INSERT INTO cloud_migrations VALUES(17,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=18").fetchone():
                    # Mail copies the Mac already synced: CREATE TABLE / VIRTUAL TABLE
                    # IF NOT EXISTS above installs mail_mailboxes, mail_messages and mail_fts.
                    c.execute("INSERT INTO cloud_migrations VALUES(18,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=19").fetchone():
                    # A bot run by an external agent gets no job for a message (it reads its
                    # inbox itself). The trigger text changed, and IF NOT EXISTS keeps the old one.
                    c.execute("DROP TRIGGER IF EXISTS queue_bot_message")
                    c.execute(QUEUE_TRIGGER)
                    c.execute("INSERT INTO cloud_migrations VALUES(19,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=20").fetchone():
                    # The Slack gateway's queue tables (SLACK_SCHEMA): CREATE TABLE IF NOT EXISTS
                    # above installs them; the marker makes the contract visible to release checks.
                    c.execute("INSERT INTO cloud_migrations VALUES(20,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=21").fetchone():
                    # A person's batch of what needs them, walked and committed as one
                    # (backend/batch.py). CREATE TABLE IF NOT EXISTS above installs it.
                    c.execute("INSERT INTO cloud_migrations VALUES(21,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=22").fetchone():
                    # Channels read like an employee reads them: stored channel messages carry
                    # their author and any edit or deletion; readers keep a cursor per channel.
                    columns = {row[1] for row in c.execute("PRAGMA table_info(slack_events)")}
                    for name, declaration in (("author", "TEXT NOT NULL DEFAULT 'human'"), ("author_name", "TEXT"),
                                              ("edited", "TEXT"), ("deleted", "TEXT"), ("updated", "TEXT")):
                        if name not in columns:
                            c.execute(f"ALTER TABLE slack_events ADD COLUMN {name} {declaration}")
                    c.execute("INSERT INTO cloud_migrations VALUES(22,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=24").fetchone():
                    columns = {row[1] for row in c.execute("PRAGMA table_info(bot_config)")}
                    if "goals" not in columns:
                        c.execute("ALTER TABLE bot_config ADD COLUMN goals TEXT NOT NULL DEFAULT ''")
                    c.execute("INSERT INTO cloud_migrations VALUES(24,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=28").fetchone():
                    # Routines are rows people and bots write through the API (backend/routines.py).
                    # The repository manifest, its Git sync and the per-version snapshots that
                    # tracked it are gone; the rows, their settings and their occurrence history
                    # stay. A row the manifest had deleted stays deleted.
                    # Whatever they hold is copied to `<table>_retired` first, so a start on a
                    # new tag with no updater snapshot loses nothing.
                    for table in ("routine_tasks", "routine_versions", "routine_sources", "routine_deliveries"):
                        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                            if c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                                c.execute(f"CREATE TABLE IF NOT EXISTS {table}_retired AS SELECT * FROM {table}")
                            c.execute(f"DROP TABLE {table}")
                    columns = {row[1] for row in c.execute("PRAGMA table_info(schedules)")}
                    if {"source", "deleted_at"} <= columns:
                        c.execute("UPDATE schedules SET source='hub' WHERE deleted_at IS NULL")
                    c.execute("INSERT INTO cloud_migrations VALUES(28,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=29").fetchone():
                    # The tasks board: a lane picks the
                    # pipeline, a rank is the place in the owner's queue, labels stand in for
                    # projects (blockers are task_relations rows now, migration 60). task_links and
                    # preferences are created above. A comment tagged `quiet` never queues a
                    # turn (same explicit trigger replacement as migration 11).
                    columns = {row[1] for row in c.execute("PRAGMA table_info(tasks)")}
                    if "lane" not in columns:
                        c.execute("ALTER TABLE tasks ADD COLUMN lane TEXT NOT NULL DEFAULT 'company'")
                    if "rank" not in columns:
                        c.execute("ALTER TABLE tasks ADD COLUMN rank REAL")
                    if "labels_json" not in columns:
                        c.execute("ALTER TABLE tasks ADD COLUMN labels_json TEXT NOT NULL DEFAULT '[]'")
                    c.execute("CREATE INDEX IF NOT EXISTS tasks_owner_rank ON tasks(owner, rank)")
                    # day one looks like yesterday: every open task keeps its creation order
                    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
                    c.execute(f"UPDATE tasks SET rank=(SELECT COUNT(*) FROM tasks t2 WHERE t2.owner=tasks.owner "
                              f"AND t2.status IN ({marks}) AND t2.created<=tasks.created) "
                              f"WHERE rank IS NULL AND status IN ({marks})", (*H.ACTIVE_STATUSES, *H.ACTIVE_STATUSES))
                    # product and engineering bots' open work starts in the product lane
                    c.execute(f"UPDATE tasks SET lane='product' WHERE status IN ({marks}) AND owner IN "
                              f"(SELECT 'bot:' || bot FROM bot_config WHERE team IN ({','.join('?' * len(H.LANE_TEAMS))}))",
                              (*H.ACTIVE_STATUSES, *H.LANE_TEAMS))
                    c.execute("DROP TRIGGER IF EXISTS queue_bot_message")
                    c.execute(QUEUE_TRIGGER)
                    c.execute("INSERT INTO cloud_migrations VALUES(29,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=30").fetchone():
                    # main_pushes (backend/github.py): CREATE TABLE IF NOT EXISTS above installs it;
                    # the marker makes the contract visible to release checks.
                    c.execute("INSERT INTO cloud_migrations VALUES(30,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=31").fetchone():
                    # Existing Close audio imports keep their external identity as the new
                    # transcript-only worker takes over. hubdb's additive migration made the
                    # table before the cloud schema and this transaction run.
                    c.execute("INSERT OR IGNORE INTO recording_source_refs "
                              "(source,resource_type,external_id,meeting_id,created) "
                              "SELECT source,'call',external_id,meeting_id,created FROM import_refs")
                    c.execute("INSERT INTO cloud_migrations VALUES(31,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=32").fetchone():
                    # The market graph (backend/market.py). hubdb's MARKET_SCHEMA creates the
                    # tables on connect; this marker is the cloud contract. The curator is
                    # added once a roster exists, so a first import can still load employees.yaml.
                    from .hubdb import MARKET_SCHEMA
                    c.executescript(MARKET_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(32,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=33").fetchone():
                    # Task list pages filter by lane and status, page finished work by its
                    # completion time, and hydrate these relations in batches.
                    c.execute("CREATE INDEX IF NOT EXISTS tasks_lane_status_rank ON tasks(lane,status,rank,created)")
                    c.execute("CREATE INDEX IF NOT EXISTS tasks_active_lane_rank ON tasks("
                              "lane,(rank IS NULL),rank,created,id) WHERE status IN "
                              "('open','doing','waiting','review','ready','declined')")
                    c.execute("CREATE INDEX IF NOT EXISTS tasks_finished_lane_time ON tasks("
                              "lane,COALESCE(closed_at,done_at,updated,created) DESC,id DESC) "
                              "WHERE status IN ('done','closed')")
                    c.execute("CREATE INDEX IF NOT EXISTS schedule_occurrences_task ON schedule_occurrences(task_id)")
                    c.execute("CREATE INDEX IF NOT EXISTS events_task_origin ON events(actor,action,target,ts DESC)")
                    c.execute("INSERT INTO cloud_migrations VALUES(33,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=34").fetchone():
                    # Personal API tokens (backend/personal_tokens.py): a person's own bearer for
                    # scripts, stored as a hash. CREATE TABLE IF NOT EXISTS above installs the
                    # table; the marker makes the contract visible to release checks.
                    c.execute("INSERT INTO cloud_migrations VALUES(34,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=37").fetchone():
                    # A batch may hold one bot's items (backend/batch.py): `scope` is that bot, or
                    # NULL for the whole list as before.
                    columns = {row[1] for row in c.execute("PRAGMA table_info(batches)")}
                    if "scope" not in columns:
                        c.execute("ALTER TABLE batches ADD COLUMN scope TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(37,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=38").fetchone():
                    # Listening's observation store and the shared intake (backend/listening.py),
                    # plus `market_insights.source_ref`. hubdb's LISTENING_SCHEMA creates them on
                    # connect; the marker makes the contract visible to release checks.
                    c.execute("INSERT INTO cloud_migrations VALUES(38,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=39").fetchone():
                    # `runners.checkout_json`: how a runner's own checkout stands against
                    # origin/main, from its heartbeat (#492).
                    columns = {row[1] for row in c.execute("PRAGMA table_info(runners)")}
                    if "checkout_json" not in columns:
                        c.execute("ALTER TABLE runners ADD COLUMN checkout_json TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(39,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=40").fetchone():
                    # `runners.restart_requested`: a person pressed Restart for that computer; its
                    # next heartbeat hands the request over and clears it.
                    columns = {row[1] for row in c.execute("PRAGMA table_info(runners)")}
                    if "restart_requested" not in columns:
                        c.execute("ALTER TABLE runners ADD COLUMN restart_requested TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(40,?)", (H.now(),))
                # Goals and `tasks.goal_id` are hubdb's own migration (H.GOALS_SCHEMA), applied by
                # H.connect above; the seed below is what the cloud adds on top of the tables.
                # The provider choice goes in first: seeding the roster and the curator resolve
                # their runtime and model from it.
                from . import providers
                providers.seed_from_env(c, self.settings, H.now())
                from . import access
                access.seed(c, self.settings, H.now())
                access.retire_bot_lists(c, self.settings, H.now())
                access.raise_bot_limit(c, H.now())
                self.seed_goals(c)
                from . import routines as R
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=41").fetchone():
                    # No daily open-tasks run per bot; one BotOps sweep instead.
                    R.retire_open_tasks_checks(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(41,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=42").fetchone():
                    # Built-in sign-in sessions (backend/oidc.py): a browser holds a random id
                    # and this table holds its hash. CREATE TABLE IF NOT EXISTS above installs
                    # it; the marker makes the contract visible to release checks.
                    c.execute("INSERT INTO cloud_migrations VALUES(42,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=43").fetchone():
                    # A company goal is one owned by `company`, not just one with no parent. A
                    # person's goal with no parent and goals under it used to be the company goal.
                    c.execute("UPDATE goals SET owner='company' WHERE parent_id IS NULL AND owner LIKE 'human:%' "
                              "AND id IN (SELECT parent_id FROM goals WHERE parent_id IS NOT NULL)")
                    c.execute("INSERT INTO cloud_migrations VALUES(43,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=44").fetchone():
                    # KPIs stand on their own (backend/kpis.py). hubdb's KPIS_SCHEMA added the columns and
                    # tables on connect and turned each old `kpis.goal_id` + `target` into a `goal_kpis` link
                    # with an improvement target; this gives every KPI a slug (the folder name in the Goal
                    # Manager's repository) and records that the goals' automatic colours have been worked out.
                    from . import kpis as _kpis
                    _kpis.fill_slugs(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(44,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=45").fetchone():
                    # A starter bot's status `needs_onboarding` is now `needs_setup` (backend/statuses.py).
                    # Readers accept both for one release.
                    from . import statuses as _statuses
                    _statuses.migrate(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(45,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=46").fetchone():
                    # Hermes pairing (backend/agents.py): CREATE TABLE IF NOT EXISTS above installs
                    # agent_pairings; the marker makes the contract visible to release checks.
                    c.execute("INSERT INTO cloud_migrations VALUES(46,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=47").fetchone():
                    # The hub migration backfills once; never replay legacy labels over newer tags.
                    H._apply(c, H.TAGS_TABLES_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(47,?)", (H.now(),))

                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=48").fetchone():
                    H._apply(c, H.PIPELINES_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(48,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=49").fetchone():
                    H._apply(c, H.CHAT_GOALS_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(49,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=50").fetchone():
                    H._apply(c, H.REPOSITORIES_SCHEMA)
                    from .repositories import migrate
                    migrate(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(50,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=51").fetchone():
                    H._apply(c, H.TASK_LINKS_V2_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(51,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=52").fetchone():
                    H._apply(c, H.SUBSCRIPTIONS_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(52,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=53").fetchone():
                    H._apply(c, H.STORAGE_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(53,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=54").fetchone():
                    H._apply(c, H.TASK_REVIEW_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(54,?)", (H.now(),))
                # What every bot may do with a type's tasks (hubdb.TYPE_BOTS). Checked on every start
                # rather than numbered, so no migration number collides with another branch's.
                H.add_column(c, "task_types", "bots", "TEXT")
                # Sign out / Switch account / Use the shared key ride the sign-in relay (backend/model_login.py).
                H.add_column(c, "model_logins", "kind", "TEXT NOT NULL DEFAULT 'sign_in'")
                # When readiness was last reported (last_seen is any contact): the app waits for a report newer than a
                # sign-out or switch before it stops looking.
                H.add_column(c, "runners", "readiness_at", "TEXT")
                # The owner's "Turns at once" for a computer (NULL: automatic, its own capacity).
                H.add_column(c, "runners", "turns_at_once", "INTEGER")
                # Clear key: a computer that missed its job (offline) clears its key copy at its next key fetch.
                H.add_column(c, "model_login_cleared", "clear_key", "INTEGER NOT NULL DEFAULT 0")
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=55").fetchone():
                    H._apply(c, H.MEETING_REVIEW_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(55,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=56").fetchone():
                    H._apply(c, H.NUMBERS_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(56,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=57").fetchone():
                    H.migrate_task_privacy(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(57,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=58").fetchone():
                    H.add_column(c, "runners", "capabilities_json", "TEXT NOT NULL DEFAULT '[]'")
                    c.execute("INSERT INTO cloud_migrations VALUES(58,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=59").fetchone():
                    H._apply(c, H.WAITING_ON_SCHEMA)
                    c.execute("INSERT INTO cloud_migrations VALUES(59,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=60").fetchone():
                    # Every task-to-task relationship in task_relations; tasks.parent_id and
                    # tasks.blocked_by move into it and are dropped (hubdb migration 30 does the same).
                    from . import task_relations as _task_relations
                    _task_relations.migrate(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(60,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=61").fetchone():
                    # The scheduler's due-date reminder pass and its quiet-task resume read these indexes, not
                    # every task row (H.TASKS_DUE_SQL, quiet_work.due).
                    c.execute(H.TASKS_DUE_INDEX)
                    c.execute(H.TASKS_RESUME_INDEX)
                    c.execute("INSERT INTO cloud_migrations VALUES(61,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=62").fetchone():
                    # A day of history must not authorize every older attempt first.
                    c.execute("CREATE INDEX IF NOT EXISTS attempts_created ON attempts(created)")
                    c.execute("INSERT INTO cloud_migrations VALUES(62,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=63").fetchone():
                    # The one task an operator limited a bot's claims to (execution.scoped_sql); NULL is no limit.
                    H.add_column(c, "bot_control", "claim_task", "TEXT")
                    c.execute("INSERT INTO cloud_migrations VALUES(63,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=64").fetchone():
                    # Finance's tables, indexes and invariant triggers, every phase's (backend/finance/store/schema.py).
                    from .finance.store import schema as _finance_schema
                    _finance_schema.create(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(64,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=65").fetchone():
                    # Finance phase 1 (banking, cards, payments, gate rules): its new fin_* tables, and the columns it
                    # added to tables migration 64 made. Idempotent, so an install made after phase 1 changes nothing.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.phase1(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(65,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=66").fetchone():
                    # Finance phase 2 (bills, invoices, contacts, settlements, the cash view, posting rules): new fin_*
                    # tables and columns. Idempotent like 65.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.phase2(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(66,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=67").fetchone():
                    # Finance phase 3 (bot cards): the saved-card record, which vendor accounts a card is saved on.
                    # Idempotent like 65 and 66.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.phase3(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(67,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=68").fetchone():
                    # Finance phase 4 (the mirror of QuickBooks or Xero): external refs with their revision, and the
                    # restatements of closed months. Idempotent like 65.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.phase4(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(68,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=69").fetchone():
                    # Finance's Stripe feed: indexes on the charge link and the source id in each Stripe row's raw json,
                    # which the refund lookups search by (backend/finance/store/schema.py). Idempotent like 65.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.stripe(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(69,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=70").fetchone():
                    # Finance tax (spec §11): the sales tax jurisdictions and the due dates seeded for the outside
                    # obligations. Idempotent, like 66.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.tax(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(70,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=71").fetchone():
                    # Finance history imports (spec §4.1): the fin_raw_imports table. Idempotent like 65.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.raw_import(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(71,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=72").fetchone():
                    # Finance close snapshots (fin_close_snapshots): a closed month reads the account tree it closed
                    # with. Idempotent like 66.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.close_snapshots(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(72,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=73").fetchone():
                    # The books gaps: fin_transfers, internal transfers between our own bank accounts
                    # (backend/finance/transfers.py). Idempotent like 65 and 66.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.gaps_books(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(73,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=74").fetchone():
                    # Finance's mirror columns (unknown writes, provider line ids) and fin_credit_refunds on an
                    # install that took migrations 64 to 68 before they existed. Idempotent like 65.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.create(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(74,?)", (H.now(),))
                if not c.execute("SELECT 1 FROM cloud_migrations WHERE version=75").fetchone():
                    # The journal line each matched bank row took (fin_feed_txns.entry_line_id), on an install that
                    # took migrations 64 to 74 before it. Idempotent like 65.
                    from .finance.store import schema as _finance_schema
                    _finance_schema.feed_lines(c)
                    c.execute("INSERT INTO cloud_migrations VALUES(75,?)", (H.now(),))
                from .public_roadmaps import migrate as migrate_public_roadmaps
                migrate_public_roadmaps(c)
                # Notes a completed run carried are settled (hubdb.notes_settle): one pass settles the ones from before the
                # column, in one UPDATE. Unversioned and idempotent; it reads only the small unsettled-carried index.
                H.notes_settle_catch_up(c, None)
                H.ensure_market_reads(c)
                # Setup commands cleared in Settings before a clear released the row (backend/repositories.py).
                # Unversioned and idempotent like the trash below: it matches nothing once those rows are released.
                from .repositories import release_cleared_overrides
                release_cleared_overrides(c)
                # Deleted tasks wait here until restored or purged; created unversioned, like the
                # trigger below, so it never takes a migration number another change needs.
                from .task_delete import ensure as ensure_task_trash
                ensure_task_trash(c)
                # Tasks waiting for room for a worktree (backend/worktree_waits.py). Unversioned like the trash.
                from .worktree_waits import ensure as ensure_worktree_waits
                ensure_worktree_waits(c)
                # Milestone waits (backend/milestone_waits.py). Unversioned like the trash.
                from .milestone_waits import ensure as ensure_milestone_waits
                ensure_milestone_waits(c)
                # Failed-run back-off and run budgets (backend/run_backoff.py, backend/turn_budgets.py). Unversioned
                # like the trash: an older release never reads the tables.
                from .run_backoff import ensure as ensure_run_backoff
                from .turn_budgets import ensure as ensure_turn_budgets
                ensure_run_backoff(c)
                ensure_turn_budgets(c)
                # The redesign's automations (backend/automations.py).
                # Unversioned like the trash: an older release never reads the tables.
                from . import automations as _automations
                _automations.ensure(c)
                from . import redesign_migration as _migration
                _migration.ensure(c)                    # the cutover's record and the 30-minute limit columns
                # Recruiting seats (backend/lifecycle.py). Unversioned like the trash.
                from .lifecycle import ensure as ensure_seats
                ensure_seats(c)
                # The company's Vision, Description and their history, and each goal's Why (backend/company.py).
                # Unversioned like the trash: an older release never reads the tables or the columns.
                from .company import ensure as ensure_company
                ensure_company(c)
                # The team's strategy: versions, choices, their history, and each goal's choice (backend/strategy.py).
                # Unversioned the same way; the first run makes the draft from the company profile.
                from .strategy import ensure as ensure_strategy
                ensure_strategy(c)
                # The built-in Idea task type growth ideas are filed as (backend/growth.py).
                from .growth import ensure as ensure_growth
                ensure_growth(c)
                # Org rules and their due dates (backend/org_rules.py, backend/rule_due.py): sources, rules, holds, due
                # dates and their status rows. Unversioned the same way: an older release never reads the tables.
                from .org_rules import ensure as ensure_org_rules
                ensure_org_rules(c)
                # Their verdict cache (backend/org_rule_check.py). Unversioned the same way.
                from .org_rule_check import ensure as ensure_org_rule_check
                ensure_org_rule_check(c)
                # Counsel's documents: the versions it was asked to read, the only versions it may cite (backend/counsel.py).
                from .counsel import ensure as ensure_rule_reads
                ensure_rule_reads(c)
                # Lens runs (backend/lenses.py): the exercises run against the strategy or the market. Unversioned too.
                from .lenses import ensure as ensure_lenses
                ensure_lenses(c)
                # Page monitors and the weekly market briefing (backend/market_watch.py, backend/market_briefing.py).
                # Unversioned the same way: an older release never reads the tables.
                from .market_briefing import ensure as ensure_market_briefing
                from .market_watch import ensure as ensure_market_watch
                ensure_market_watch(c)
                ensure_market_briefing(c)
                from .market import ensure_turn_sources
                ensure_turn_sources(c)                  # which automation turn changed the graph (redesign)
                # Pages, the files' search text and hard deletes (backend/pages.py, backend/file_library.py).
                # Unversioned the same way: an older release never reads the tables.
                from .pages import ensure as ensure_pages
                from .file_library import ensure as ensure_file_library
                ensure_pages(c)
                ensure_file_library(c)
                # Temporary task assignments are retired (#420): their actors are archived and off every computer,
                # their open tasks back with the role. Unversioned and idempotent like the trash; the tables stay inert.
                from .shared_bots import retire_assignments
                retire_assignments(c)
                # A service key's scope (backend/service_keys.py); every older key files tasks. Unversioned like
                # the trash: an older release reads the table as before and never sees the column.
                H.add_column(c, "service_keys", "scope", "TEXT NOT NULL DEFAULT 'tasks'")
                # The personal token a key was made with, which it never outlives (backend/service_keys.py); NULL for a
                # key made signed in, and for every older key. Unversioned like the scope.
                H.add_column(c, "service_keys", "made_with_token", "TEXT")
                # Who asked a running turn to stop (backend/execution.py stop); the runner reads it on its next
                # lease renewal. Unversioned like the key scope: an older release never reads the column.
                H.add_column(c, "attempts", "stop_requested", "TEXT")
                # Which gateway key the attempt's runner was handed (gateway_connection.run_credentials_for): what its
                # Gateway segments are recorded under, even if the key is rotated or cleared before the run ends.
                # NULL with none handed. Unversioned like stop_requested.
                H.add_column(c, "attempts", "gateway_key_id", "TEXT")
                # The runtime and model the claim handed the bot's turn (execution.ran_another_model): a model change
                # while it is out is told from an effort change. NULL for a turn from before. Unversioned like
                # stop_requested.
                H.add_column(c, "attempts", "handed_runtime", "TEXT")
                H.add_column(c, "attempts", "handed_model", "TEXT")
                # A queued message a person sent with Stop and redirect (backend/execution.py stop): it is claimed
                # before the bot's other queued work once the stopped turn ends. Unversioned like stop_requested.
                H.add_column(c, "jobs", "redirect", "INTEGER NOT NULL DEFAULT 0")
                # An attempt's lane (clients/sessions.py lane): a task-keyed turn runs in its own lane beside
                # the bot's other tasks, everything else in `main`. At most one active attempt per bot and lane;
                # an existing row and an older runner's turn are `main`, so a bot without lanes stays serial.
                # Unversioned like stop_requested; an older release must be drained before it runs again,
                # since its per-bot index cannot be built over two active attempts.
                H.add_column(c, "attempts", "lane", "TEXT NOT NULL DEFAULT 'main'")
                c.execute("DROP INDEX IF EXISTS one_active_attempt_per_bot")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt_per_lane ON attempts(bot,lane) "
                          "WHERE state IN ('leased','running')")
                # A bot's wake preset holds its nonblocking notices and sweeps at enqueue time (backend/wake_presets.py).
                # Unversioned like redirect: an older release never reads the columns and claims every queued job.
                from .wake_presets import ensure as ensure_wake_presets
                ensure_wake_presets(c)
                # What a turn in a task's session was handed, the bot's task model or the fallback from it, and why
                # (backend/task_models.py). Unversioned: an older release never reads it.
                H.add_column(c, "attempts", "model_json", "TEXT")
                # The change log live events are read from (backend/events.py); idempotent.
                from .events import ensure as ensure_changes
                ensure_changes(c)
                c.execute("""CREATE TRIGGER IF NOT EXISTS repository_new_bot_default
                    AFTER INSERT ON bot_config
                    WHEN json_extract(NEW.config_json,'$.repo_access_mode') IS NULL
                    BEGIN
                    UPDATE bot_config SET config_json=json_set(config_json,'$.repo_access_mode',
                        coalesce((SELECT json_extract(value_json,'$.new_bot_default')
                            FROM registry_metadata WHERE key='repos_new_bot_default'),'own')) WHERE bot=NEW.bot;
                    END""")
                # Lookups that scanned their whole table (performance pass): a goal's
                # tasks, a bot's or computer's attempts, a job's attempts, and the events read by
                # action and target (quarantines, drains, who opened a conversation). Idempotent,
                # so no migration number to collide with another branch's.
                for name, spec in (("tasks_goal", "tasks(goal_id, status)"),
                                   # History replay reads the tasks made in a window (backend/replay.py).
                                   ("tasks_created", "tasks(created)"),
                                   ("task_links_repo_kind", "task_links(repo COLLATE NOCASE, kind)"),
                                   ("task_links_repo_url", "task_links(kind, state, url COLLATE NOCASE)"),
                                   ("attempts_bot_created", "attempts(bot, created)"),
                                   # A bot's latest usage-limited attempt (execution.last_limit), read per Health render.
                                   ("attempts_bot_finished", "attempts(bot, finished)"),
                                   ("attempts_job", "attempts(job_id)"),
                                   # Lease maintenance walks only active runs, never their history.
                                   ("attempts_live_lease", "attempts(lease_until,id) WHERE state IN ('leased','running')"),
                                   ("jobs_queued", "jobs(id,bot,message_id) WHERE state='queued'"),
                                   ("attempts_runner_state", "attempts(runner_id, state)"),
                                   # The turns a computer ran in a window, read under the write lock by an
                                   # open overload (backend/performance.py `_runs`).
                                   ("attempts_runner_finished", "attempts(runner_id, finished)"),
                                   # A computer asking after the tasks its session checkouts are kept for.
                                   ("attempts_bot_lane", "attempts(bot, lane)"),
                                   # Recent failed and finished runs (Health, Fleet check, the census, /api/runs).
                                   # Partial, so the scheduler's live-lease read keeps attempts_live_lease.
                                   ("attempts_finished_state", "attempts(state, finished) WHERE finished IS NOT NULL"),
                                   ("events_action_target", "events(action, target, ts)"),
                                   # One action's rows in a time window, with no target (Health's limited runs, the
                                   # judge's month, MCP calls): events_action_target walks every row of the action
                                   # (911 ms on TIDY), events_ts every action's rows in the window.
                                   ("events_action_ts", "events(action, ts)"),
                                   # Conversations newest first (hubdb.conversations_for, chats_with): the ORDER BY's
                                   # expression word for word, or SQLite sorts every conversation.
                                   ("conversations_recent", "conversations(COALESCE(last_message_at, created))"),
                                   # A person's open chat with a bot (hubdb._pair_conversation, on every say()).
                                   ("conversations_open_kind", "conversations(kind, COALESCE(last_message_at, created)) "
                                    "WHERE closed_at IS NULL AND task_id IS NULL"),
                                   # Open approvals: a task's (auto_tasks.waiting on the claim path), a requester's
                                   # (status_counts, task_privacy), and the oldest first (expiry, Needs you).
                                   ("approvals_open", "approvals(task_id) WHERE decision IS NULL"),
                                   ("approvals_open_requester", "approvals(requested_by) WHERE decision IS NULL"),
                                   ("approvals_open_created", "approvals(created) WHERE decision IS NULL"),
                                   # Live jobs (/api/status), an attempt's jobs (lease expiry, finish) and uncertain
                                   # jobs (auto_reconcile). Partial, so the claim reads keep jobs_queue and jobs_queued.
                                   ("jobs_live_created", "jobs(created) WHERE state IN ('queued','leased','running','input')"),
                                   ("jobs_attempt", "jobs(attempt_id) WHERE attempt_id IS NOT NULL"),
                                   ("jobs_uncertain", "jobs(created) WHERE state='uncertain'"),
                                   # Open asks to someone, deleted or not (hubdb.tasks_asked_of); conversation_id too,
                                   # so Health's silent_waits keeps a good plan.
                                   ("messages_open_ask_to",
                                    "messages(to_actor, conversation_id) WHERE kind='ask' AND answered_by IS NULL"),
                                   # Replaced asks to someone (views.went_away, on every Needs you read).
                                   ("messages_superseded_asks_to",
                                    "messages(to_actor, superseded_at) WHERE kind='ask' AND superseded_at IS NOT NULL"),
                                   # PRs at a commit (github.pr_signal, on every check and status webhook): the
                                   # query's expression word for word.
                                   ("task_links_pr_head", "task_links(json_extract(detail_json,'$.head_sha')) WHERE kind='pr'"),
                                   # A task's runs (the task view's runs and its Runs section, backend/run_view.py).
                                   ("turns_task", "turns(task_id, started)"),
                                   # A message's runs: task_delete counts them on every delete, and topic_change
                                   # and the run visibility (sql.py) look them up by message.
                                   ("turns_message", "turns(message_id)"),
                                   # A conversation's runs: task_delete counts them, the foreign key check of a
                                   # deleted conversation reads them, and stream_capture looks them up.
                                   ("attempt_conversations_conversation", "attempt_conversations(conversation_id)"),
                                   # A task's file activity: task_delete counts it, and the activity a person may
                                   # see by task (sql.py) reads it.
                                   ("bot_file_activity_task", "bot_file_activity(task_id)"),
                                   # A blob's provenance (task_privacy.blob_readable), read for every
                                   # version of every file a bot's Files tab lists and for each download.
                                   ("message_assets_blob", "message_assets(blob_id)"),
                                   ("blob_media_poster", "blob_media(poster_blob_id) WHERE poster_blob_id IS NOT NULL"),
                                   ("blob_media_thumb", "blob_media(thumb_blob_id) WHERE thumb_blob_id IS NOT NULL"),
                                   ("bot_file_versions_poster",
                                    "bot_file_versions(poster_blob_id) WHERE poster_blob_id IS NOT NULL"),
                                   ("bot_file_versions_thumb",
                                    "bot_file_versions(thumb_blob_id) WHERE thumb_blob_id IS NOT NULL"),
                                   # privacy_index names these two with INDEXED BY. hubdb.migrate also makes them,
                                   # but runs before cloud migration 57 adds tasks.private on an upgraded database.
                                   ("tasks_private_parties",
                                    "tasks(id, requester, owner, guest, private) WHERE private IS NOT 0"),
                                   ("tasks_private_status",
                                    "tasks(id, requester, owner, guest, private, status, waiting_on) WHERE private IS NOT 0"),
                                   # The task list's and status rows' covering indexes (hubdb.migrate), for the same reason.
                                   ("tasks_queue_cover", "tasks(rank, created, id, owner, requester, private, guest)"),
                                   ("tasks_owner_status_cover",
                                    "tasks(owner, status, id, requester, private, guest, waiting_on)"),
                                   ("tasks_requester_status_cover",
                                    "tasks(requester, status, id, owner, private, guest, waiting_on)"),
                                   # A person's view of a bot's settings changes (settings_admin.history): the manager
                                   # bots' changes, found by seek and in id order within a timestamp. It needs the `via`
                                   # column, added above.
                                   ("settings_changes_bot_via_id", "settings_changes(bot, via, created, id)"),
                                   ("conversations_id_task", "conversations(id, task_id)")):
                    c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")
                # The settings_changes indexes these replace, which lack id (the schema above and the list).
                for name in ("settings_changes_created", "settings_changes_bot", "settings_changes_bot_via"):
                    c.execute(f"DROP INDEX IF EXISTS {name}")
                c.execute("DROP INDEX IF EXISTS jobs_state_bot_created")
                # Duplicated the UNIQUE(channel, ts) autoindex.
                c.execute("DROP INDEX IF EXISTS slack_events_channel_ts")
                # Redundant with events_action_target (action, target, ts): every action-and-target read plans on it
                # (backend/tests/test_events_indexes.py), and no read is target-only. 118 MB on TIDY. An older release
                # recreates it when it starts; the next start drops it again.
                c.execute("DROP INDEX IF EXISTS events_target_action")
                R.ensure_task_sweep(c)
                # The Assistant's pending actions (backend/assistant.py) and the "via" of a task-history row.
                from .assistant import ensure_schema as ensure_assistant_schema
                ensure_assistant_schema(c)
                # Tico no longer records: a meeting left waiting on audio or transcription is
                # finished with what it has (backend/meetings.py). Idempotent, no migration number.
                from .meetings import retire_capture
                retire_capture(c)
                retire_health_rows(c)
                # Whenever the queue trigger's SQL changes (an external harness added, a rule changed) the
                # stored trigger is rebuilt; no migration number, so no collision with another branch's.
                # SQLite keeps the statement as written, less "IF NOT EXISTS".
                stored = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                                   "AND name='queue_bot_message'").fetchone()
                if not stored or stored[0] != QUEUE_TRIGGER.replace(" IF NOT EXISTS", "", 1):
                    c.execute("DROP TRIGGER IF EXISTS queue_bot_message")
                    c.execute(QUEUE_TRIGGER)
                if seed_market and not self.settings.test_identities:
                    from . import market as Market
                    from .blobs import Blobs
                    Market.ensure_curator(c)
                    document = Market.load_snapshot(self.settings.registry_dir)
                    sources = Market.locate_sources(self.settings.registry_dir.parent)
                    Market.seed(c, sources, Blobs(self.settings), document=document)
                # Market pages the seed wrote before pages carried `seeded`: hide the untouched ones while the graph
                # is empty (backend/market.py). Idempotent, no migration number.
                from . import market as Market
                Market.mark_seeded_pages(c)
                # Finished tasks still holding an earlier close time (H.clear_finished_close_times). Idempotent.
                H.clear_finished_close_times(c)
                # Teams, org groups and derived departments become groups, once (backend/groups.py). Idempotent,
                # so no migration number to collide with another branch's.
                from . import groups as Groups
                Groups.migrate(c, self.settings)
                # The Goal Manager becomes the Strategy Bot on every install, once (backend/strategy_bot.py).
                # Idempotent.
                from . import strategy_bot as StrategyBot
                StrategyBot.migrate(c)
                # Cards waiting in the owner's earlier Strategy Bot chat move to their Assistant room
                # (backend/assistant.py: one Assistant for everyone). Idempotent.
                from . import assistant as Assistant
                Assistant.migrate(c, self.settings)
                # Every bot gets a profile by its name, once; a bot that has one is left alone (backend/profiles.py).
                from . import profiles as Profiles
                Profiles.migrate(c)
                from . import releases as Releases
                Releases.record_start(c, H.now())
                from .credentials import FILE_MIGRATION, HUB_MIGRATION
                if not c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (FILE_MIGRATION,)).fetchone():
                    pending = [r[0] for r in c.execute("SELECT slug FROM bots")]
                    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (FILE_MIGRATION, encode(pending)))
                    c.execute("INSERT OR IGNORE INTO registry_metadata VALUES(?,?)", (HUB_MIGRATION, encode([])))
                elif not c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (HUB_MIGRATION,)).fetchone():
                    # Installs that ran 0.2.30's migration: every bot it already covered gets the HUB_ pass.
                    done = set(json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key=?",
                                                    (FILE_MIGRATION,)).fetchone()[0]))
                    again = [r[0] for r in c.execute("SELECT slug FROM bots") if r[0] not in done]
                    c.execute("INSERT INTO registry_metadata VALUES(?,?)", (HUB_MIGRATION, encode(again)))
                # Every task is its own session by default now (clients/sessions.py). Older servers wrote
                # `session: bot` into every bot as the default, so clear it once; a later explicit `bot` stays.
                if not c.execute("SELECT 1 FROM registry_metadata WHERE key='task_session_default'").fetchone():
                    c.execute("UPDATE bot_config SET config_json=json_remove(config_json,'$.session') "
                              "WHERE json_valid(config_json) AND json_extract(config_json,'$.session')='bot'")
                    c.execute("INSERT INTO registry_metadata VALUES('task_session_default','true')")
                _docs.refresh_generated_docs(c)
                record = c.execute("SELECT value_json FROM registry_metadata WHERE key='onboarding'").fetchone()
                if record:
                    choices = H._json(record[0], {})
                    if isinstance(choices, dict) and isinstance(choices.get("answers"), dict) and "never_without_person" in choices["answers"]:
                        choices["answers"].pop("never_without_person")
                        c.execute("UPDATE registry_metadata SET value_json=? WHERE key='onboarding'", (encode(choices),))
                c.commit()
            except Exception:
                c.rollback()
                raise
            from . import provenance_clock, roster_clock
            provenance_clock.ensure(c)
            roster_clock.ensure(c)
            from . import row_clock
            row_clock.ensure(c)
            # What each message's and run's readability depends on (backend/read_deps.py); settled beside the server.
            from . import read_deps
            read_deps.ensure(c)
            # Federation between installs (backend/federation_schema.py): its own tables, kept beside the threads.
            from . import federation_schema
            federation_schema.ensure(c)
            # Every start, in short batches of their own: rows written since the last pass, including any an
            # older server wrote after a rollback, get their task links (backend/message_links.py).
            message_links.backfill(c)
        c.close()
        # Once per install: routines and wake rules become automations (backend/redesign_migration.py). Each object
        # in its own write, so a restart part way goes on where it stopped.
        redesign_migration.cutover(self)

    @contextmanager
    def read(self):
        c = self._take()
        try:
            yield c
        finally:
            self._give(c)

    @contextmanager
    def read_transaction(self):
        """A consistent SQLite snapshot for multi-query authorization without taking a write lock."""
        with self.read() as c:
            c.execute("BEGIN")
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

    def _file(self):
        try:
            stat = os.stat(self.settings.db_path)
        except OSError:
            return None
        return stat.st_dev, stat.st_ino

    def _take(self):
        identity = self._file()
        with self.pool_lock:
            if identity != self.pool_file:
                # A restored or replaced database file: connections to the old one are not reused.
                stale, self.pool, self.pool_file = self.pool, [], identity
                from .provenance_clock import Cache
                self.provenance_cache = Cache()
                self.roster_rows = {}
                from .row_clock import Cache as RowCache
                self.row_cache = RowCache()
            else:
                stale = []
            c = self.pool.pop() if self.pool else None
        for old in stale:
            old.close()
        if c is None:
            c = self.connect(factory=self.pooled, check_same_thread=False)
            c.pool_file = identity
        c.read_cache = {}
        c.provenance_cache = self.provenance_cache
        c.roster_rows = self.roster_rows
        c.row_cache = self.row_cache
        c.write_transaction = False
        c.after_commit = []
        c.provenance_changes = c.total_changes
        c.provenance_start = None
        return c

    def _give(self, c):
        c.read_cache.clear()
        # Only a connection left as it was handed out goes back: no open transaction, nothing per-caller installed.
        try:
            if c.in_transaction:
                c.rollback()
            if c.row_factory is not sqlite3.Row:
                c.row_factory = sqlite3.Row
        except sqlite3.Error:
            c.tainted = True
        with self.pool_lock:
            if not c.tainted and c.pool_file == self.pool_file and len(self.pool) < POOL_IDLE:
                self.pool.append(c)
                return
        c.close()

    @contextmanager
    def transaction(self):
        from .flight import begin
        # The gate is released only after commit/rollback, including message-link maintenance.
        with ExitStack() as stack:
            c = None
            def checkout():
                nonlocal c
                c = stack.enter_context(self.read())
                return c
            done = begin(None, self.recorder, gate=self.writer, checkout=checkout)
            try:
                c.write_transaction = True
                from . import provenance_clock
                c.provenance_start = provenance_clock.current(c)
                yield c
                message_links.refresh_dirty(c)
                c.commit()
                # Work that must see the committed rows (a delivery worker's doorbell) runs once the write is durable.
                for after in c.after_commit:
                    try:
                        after()
                    except Exception:
                        logging.getLogger(__name__).exception("after-commit callback failed")
                c.after_commit.clear()
            except BaseException:
                c.rollback()
                c.after_commit.clear()
                raise
            finally:
                done()

    def seed_goals(self, c):
        """`registry/goals.yaml`, once per goal (backend/goals.py `seed`): the first draft of the
        company and team goals. After that the hub's rows are the goals, and the
        file is never read for a goal that already exists. A goal whose owner is not on the
        roster yet is skipped and tried again at the next start, so the order of seeding bots
        and goals does not matter."""
        path = self.settings.registry_dir / "goals.yaml"
        if not path.exists():
            return []
        try:
            document = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            return []
        added = G.seed(c, document, lambda owner: H.resolve_actor(c, owner))
        from . import market as Market
        Market.ensure_curator(c)
        return added

    def seed(self, entries=None, roster=None):
        """Explicit initial import only; stale local checkouts never overwrite assignments."""
        path = self.settings.registry_dir
        registry = yaml.safe_load((path / "employees.yaml").read_text())
        roster = roster or yaml.safe_load((path / "people.yaml").read_text())
        entries = entries or {e["name"]: {**registry.get("defaults", {}), **e} for e in registry["employees"]}
        people = P.load(roster, owner={"email": self.settings.owner_email})
        owner = (P.person_by_email(self.settings.owner_email, people) or {}).get("id") or people["default_user"]
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM bot_config LIMIT 1").fetchone():
                raise Problem("already_initialized", "Registry already imported; use explicit updates", 409)
            previous_states = {r["slug"]: r["state"] for r in H.bots(c)}
            # A roster entry with no runtime or model follows the company's provider choice;
            # with none made yet it stays unresolved and turns refuse to start until it is.
            from . import providers
            choice = providers.load(c, self.settings)
            entries = {slug: providers.fill(choice, entry) for slug, entry in entries.items()}
            H.sync_registry(c, entries, roster)
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode(people),))
            for slug, state in previous_states.items():
                c.execute("UPDATE bots SET state=? WHERE slug=?", (state, slug))
            for slug, entry in entries.items():
                team = P.team_of(slug, entries, people)
                operator = seed_operator(slug, team, people, owner)
                mode = str(entry.get("thread_mode") or
                           ("personal" if slug == "coo" else "shared" if slug == "cpo" else "personal"))
                c.execute(
                    "INSERT INTO bot_config(bot,config_json,team,operator,description,reports_to,repo,"
                    "thread_mode,definition_updated,definition_updated_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (slug, encode(entry), team, operator, str(entry.get("description") or ""),
                     entry.get("reports_to"), str(entry.get("repo") or ("emp-" + slug)), mode,
                     H.now(), H.KEEPER))
            self.seed_goals(c)
            from . import groups as Groups
            Groups.migrate(c, self.settings)

    @staticmethod
    def _principal(identity):
        return identity.actor + (":" + identity.attempt_id if identity.role == "bot" else "")

    def saved(self, c, identity, operation, key):
        """Whether this Idempotency-Key already has a saved answer here: `mutate` replays it (or refuses other content
        under the key) without running the write again, so a check made before `mutate` must not refuse it."""
        return bool(key) and len(key) <= 200 and c.execute(
            "SELECT 1 FROM idempotency WHERE actor=? AND operation=? AND key=?",
            (self._principal(identity), operation, key)).fetchone() is not None

    def mutate(self, identity, operation, key, body, fn, check=None):
        if not key or len(key) > 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
        hashed = digest(encode(body))
        principal = self._principal(identity)
        refusal = logged = None
        with self.transaction() as c:
            # Authenticate leases again under the same write lock as the mutation.
            from .auth import validate_identity
            validate_identity(c, identity)
            if check:
                check(c)
            row = c.execute("SELECT * FROM idempotency WHERE actor=? AND operation=? AND key=?",
                            (principal, operation, key)).fetchone()
            if row:
                if row["request_hash"] != hashed:
                    raise Problem("idempotency_conflict", "This key was used for different content", 409)
                result = json.loads(row["response_json"])
                from .auth import Auth, Identity
                from . import task_privacy as privacy
                replay_auth = Auth(self)
                replay_auth.sync_access(c)
                replay_principal = identity
                if identity.role == "runner" and isinstance(result, dict):
                    saved = result.get("attempt")
                    aid = result.get("attempt_id") or (saved.get("id") if isinstance(saved, dict) else None)
                    if aid:
                        # A finished attempt can replay its receipt without a live bot lease, but
                        # the computer must still own that generation and the bot must still see it.
                        hosted = c.execute("SELECT a.bot FROM attempts a JOIN assignments x ON x.bot=a.bot "
                                           "JOIN bots b ON b.slug=a.bot WHERE a.id=? AND a.runner_id=? "
                                           "AND x.runner_id=? AND x.generation=a.generation AND b.state='active'",
                                           (aid, identity.runner_id, identity.runner_id)).fetchone()
                        if not hosted:
                            raise Problem("privacy", "This execution is no longer assigned to this computer", 403)
                        replay_principal = Identity("bot:" + hosted["bot"], "bot", runner_id=identity.runner_id,
                                                    attempt_id=aid)
                        if not privacy.attempt_readable(c, replay_principal.actor, aid):
                            raise Problem("privacy", "This execution is no longer available", 403)
                # Cached results keep their retry semantics, but access is current on every retry.
                task_ids = set()
                for part in operation.split("/"):
                    if H.task(c, part):
                        task_ids.add(part)
                def referenced(value):
                    if isinstance(value, dict):
                        if "requester" in value and "owner" in value and value.get("id"):
                            task_ids.add(value["id"])
                        for name, item in value.items():
                            if name in ("task", "task_id", "parent_id", "blocked_by") and isinstance(item, str) and H.task(c, item):
                                task_ids.add(item)
                            referenced(item)
                    elif isinstance(value, list):
                        for item in value:
                            referenced(item)
                referenced(result)
                if operation == "/api/v2/task-bulk":
                    for rows in result.get("tasks", {}).values():
                        task_ids.update(task["id"] for task in rows)
                for task_id in task_ids:
                    replay_auth.task(c, replay_principal, task_id)
                privacy.require_payload(c, replay_principal, result)
                return result
            # Who made the changes this write logs (backend/events.py).
            from . import events as changes
            c.execute("SAVEPOINT domain_write")
            since = changes.mark(c)
            try:
                result = fn(c)
                logged = changes.claim(c, since, identity.actor)
                c.execute("RELEASE domain_write")
            except H.Refused as exc:
                logged = None                   # rolled back with the write
                refusal = refused(c, identity, exc)
                result = {"_refusal": {"code": refusal.code, "detail": ("Private task write refused" if getattr(exc, "private", False) else refusal.detail),
                                       "status": refusal.status}}
            if not is_poll(operation, result):
                cached = ({k: v for k, v in result.items() if k != 'requester_token'}
                          if isinstance(result, dict) else result)
                c.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                          (principal, operation, key, hashed, encode(cached), H.now()))
        self.ring(logged)
        if refusal:
            # This key now records a rolled-back operation, not an earlier successful delivery.
            refusal.extra["idempotency_refused"] = True
            raise refusal
        return result

    def write(self, identity, fn):
        """`mutate` without an idempotency record, for a route whose request is its own: the same
        identity check under the write lock, and the same all-or-nothing refusal."""
        refusal = logged = None
        with self.transaction() as c:
            from .auth import validate_identity
            validate_identity(c, identity)
            # Who made the changes this write logs (backend/events.py).
            from . import events as changes
            c.execute("SAVEPOINT domain_write")
            since = changes.mark(c)
            try:
                result = fn(c)
                logged = changes.claim(c, since, identity.actor)
                c.execute("RELEASE domain_write")
            except H.Refused as exc:
                logged = None                   # rolled back with the write
                refusal = refused(c, identity, exc)
        self.ring(logged)
        if refusal:
            raise refusal
        return result

    def ring(self, seq):
        """After a commit that logged changes: wake the open event streams (backend/events.py)."""
        from .events import ring
        ring(self, seq)

    def enqueue_existing(self):
        with self.transaction() as c:
            c.execute("INSERT OR IGNORE INTO jobs(id,message_id,bot,created) "
                      "SELECT id,id,substr(to_actor,5),created FROM messages NEW "
                      "WHERE to_actor LIKE 'bot:%' AND delivered_at IS NULL AND deleted_at IS NULL AND NOT "
                      + EXTERNAL_BOT_SQL)
