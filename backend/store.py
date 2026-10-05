"""Transactions and additive cloud schema over the existing Hub domain layer."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager

import yaml

from . import goals as G
from . import hubdb as H
from . import people as P
from .harnesses import EXTERNAL_HARNESSES


# A person's own spoken Tico turns, spoken decisions and what a live tool did are history,
# not work: they never queue a job. A live ask does; that is how the text Tico is reached.
# A bot run by an external agent (a Hermes profile) has no runner to claim a job: the message
# waits in its inbox until the agent reads it, so no job is queued for it.
EXTERNAL_BOT_SQL = ("EXISTS (SELECT 1 FROM bot_config WHERE bot=substr(NEW.to_actor,5) AND "
                    "json_extract(config_json,'$.harness') IN ("
                    + ",".join("'" + h + "'" for h in EXTERNAL_HARNESSES) + "))")
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
CREATE INDEX IF NOT EXISTS settings_changes_created ON settings_changes(created);
CREATE TABLE IF NOT EXISTS session_epochs(
 conversation_id TEXT PRIMARY KEY REFERENCES conversations(id), epoch INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
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
-- reads out, the hash of the secret only the profile knows, and, from approval until the
-- profile collects it once, the minted credential.
CREATE TABLE IF NOT EXISTS agent_pairings(
 id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, secret_hash TEXT NOT NULL,
 profile TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '',
 harness TEXT NOT NULL DEFAULT 'hermes', client_hash TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'pending', bot TEXT, token TEXT,
 created TEXT NOT NULL, expires_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT);
CREATE INDEX IF NOT EXISTS agent_pairings_client ON agent_pairings(client_hash, created);
CREATE TABLE IF NOT EXISTS runners(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, operator TEXT NOT NULL REFERENCES humans(id),
 token_hash TEXT NOT NULL UNIQUE, created TEXT NOT NULL, last_seen TEXT,
 revoked_at TEXT, platform TEXT, version TEXT, capacity INTEGER NOT NULL DEFAULT 4,
 readiness_json TEXT NOT NULL DEFAULT '{}', capabilities_json TEXT NOT NULL DEFAULT '[]',
 awake_since TEXT, checkout_json TEXT,
 restart_requested TEXT);
CREATE TABLE IF NOT EXISTS model_logins(
 id TEXT PRIMARY KEY, runner_id TEXT NOT NULL REFERENCES runners(id), runtime TEXT NOT NULL,
 profile TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
 user_code TEXT NOT NULL DEFAULT '', lines_json TEXT NOT NULL DEFAULT '[]',
 message TEXT NOT NULL DEFAULT '', pending_code TEXT, code_sent INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, updated TEXT NOT NULL, expires_at TEXT NOT NULL, requested_by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS model_logins_by_runner ON model_logins(runner_id,state);
CREATE TABLE IF NOT EXISTS runner_harness_actions(
 id TEXT PRIMARY KEY, runner_id TEXT NOT NULL REFERENCES runners(id), harness TEXT NOT NULL,
 action TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
 created TEXT NOT NULL, updated TEXT NOT NULL, requested_by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runner_harness_actions_by_runner ON runner_harness_actions(runner_id,state);
CREATE TABLE IF NOT EXISTS enrollments(
 code_hash TEXT PRIMARY KEY, operator TEXT NOT NULL REFERENCES humans(id),
 expires TEXT NOT NULL, consumed_at TEXT, runner_id TEXT);
CREATE TABLE IF NOT EXISTS human_tokens(
 id TEXT PRIMARY KEY, human TEXT NOT NULL REFERENCES humans(id), label TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE, created TEXT NOT NULL, created_by TEXT NOT NULL,
 last_used TEXT, expires_at TEXT, revoked_at TEXT);
-- Service keys (backend/service_keys.py): another system's credential for a few routes (its scope), as a
-- hash, and the task each (key, that system's own key for the work) pair names.
CREATE TABLE IF NOT EXISTS service_keys(
 id TEXT PRIMARY KEY, label TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, created TEXT NOT NULL,
 created_by TEXT NOT NULL, last_used TEXT, revoked_at TEXT, revoked_by TEXT, scope TEXT NOT NULL DEFAULT 'tasks');
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
CREATE TABLE IF NOT EXISTS assignment_branches(
 id TEXT PRIMARY KEY, source_bot TEXT NOT NULL REFERENCES bots(slug), assignment_key TEXT NOT NULL,
 generation INTEGER NOT NULL CHECK(generation>=1), bot TEXT NOT NULL UNIQUE REFERENCES bots(slug),
 task_id TEXT NOT NULL REFERENCES tasks(id), allocator TEXT NOT NULL, runner_id TEXT NOT NULL REFERENCES runners(id),
 display_name TEXT NOT NULL, phase TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 request_hash TEXT NOT NULL, checkpoint_json TEXT NOT NULL DEFAULT '{}', receipts_json TEXT NOT NULL DEFAULT '{}',
 created TEXT NOT NULL, updated TEXT NOT NULL, archived_at TEXT,
 UNIQUE(source_bot,assignment_key,generation));
CREATE UNIQUE INDEX IF NOT EXISTS assignment_branch_live_task ON assignment_branches(source_bot,task_id)
 WHERE phase NOT IN ('archived','cancelled');
CREATE INDEX IF NOT EXISTS assignment_branch_source_phase ON assignment_branches(source_bot,phase,created);
CREATE TABLE IF NOT EXISTS assignment_branch_events(
 id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignment_branches(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS assignment_branch_events_by_assignment ON assignment_branch_events(assignment_id,created);
CREATE TABLE IF NOT EXISTS assignment_branch_cleanup(
 assignment_id TEXT PRIMARY KEY REFERENCES assignment_branches(id), runner_id TEXT NOT NULL REFERENCES runners(id),
 state TEXT NOT NULL CHECK(state IN ('requested','blocked','complete')), requested_by TEXT NOT NULL,
 requested TEXT NOT NULL, updated TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', completed TEXT,
 attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt>=1));
CREATE INDEX IF NOT EXISTS assignment_branch_cleanup_pending ON assignment_branch_cleanup(runner_id,state,updated);
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
CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt_per_bot ON attempts(bot)
 WHERE state IN ('leased','running');
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
CREATE TABLE IF NOT EXISTS credentials(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL, env TEXT NOT NULL DEFAULT '', preview TEXT NOT NULL DEFAULT '',
 ciphertext BLOB, nonce BLOB, source TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL,
 updated_by TEXT NOT NULL);
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
CREATE INDEX IF NOT EXISTS slack_events_channel_ts ON slack_events(channel, ts);
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


def refused(c, identity, exc):
    """Undo a refused domain write, keep its audit (and rule 8's count), and say what answers it."""
    c.execute("ROLLBACK TO domain_write")
    c.execute("RELEASE domain_write")
    # Preserve refusal auditing, but never a partial domain operation.
    token = H.PRIVATE_WRITE.set(getattr(exc, 'private', False))
    try:
        H.refuse(c, identity.actor, exc.rule, exc.detail, exc.severity)
    except H.Refused:
        pass
    finally:
        H.PRIVATE_WRITE.reset(token)
    return Problem(exc.rule, exc.detail, 403 if exc.rule in ("identity", "escape", "quarantined", "close") else 422)


# A client retries a failed write with the same Idempotency-Key within seconds, so two
# days of stored responses is generous. Without a bound the table kept every mutating
# request's response forever: most of the database file.
IDEMPOTENCY_RETENTION_HOURS = 48
MAIL_RETENTION_DAYS = 180
# Polls carry nothing a retry could double: a replayed heartbeat or lease renewal just
# answers again, and an empty claim handed out no lease. They arrive every 15 s per runner
# (the heartbeat reply carries every assignment), and grew to most of the database file. A claim that leased a job stays replayable so a lost reply cannot
# strand the lease.
def is_poll(operation, result):
    if operation == "/api/v2/runners/heartbeat":
        return True
    if operation == "/api/v2/jobs/claim":
        return isinstance(result, dict) and result.get("attempt") is None
    return operation.startswith("/api/v2/attempts/") and operation.endswith("/renew")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
    if isinstance(value, str):
        try:
            value = json.loads(value)
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


class Store:
    def __init__(self, settings):
        self.settings = settings

    def connect(self):
        c = sqlite3.connect(str(self.settings.db_path), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        # As hubdb.connect: a writer waits out a backup checkpoint or a long scheduler pass
        # rather than answering storage_unavailable.
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA synchronous=FULL")
        return c

    def initialize(self, *, seed_market=True, adopt_legacy=False):
        self.settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        with H.connect(self.settings.db_path, adopt_legacy=adopt_legacy) as c:
            c.executescript(SCHEMA)
            from . import updates as _updates
            c.executescript(_updates.SCHEMA)
            _updates.ensure_schema(c)
            from . import runner_versions as _runner_versions
            c.executescript(_runner_versions.SCHEMA)
            from . import files as _files
            c.executescript(_files.SCHEMA)
            from . import memory_history as _memory_history
            c.executescript(_memory_history.SCHEMA)
            from . import docs as _docs
            _docs.ensure_schema(c, self.settings)
            from . import bot_tools as _bot_tools
            c.executescript(_bot_tools.SCHEMA)
            from . import support as _support
            c.executescript(_support.SCHEMA)
            from . import credential_cards as _credential_cards
            c.executescript(_credential_cards.SCHEMA)
            from . import watchers as _watchers
            c.executescript(_watchers.SCHEMA)
            from . import slack_channels as _slack_channels
            c.executescript(_slack_channels.SCHEMA)
            _updates.purge_rejected(c)
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
                # A starter bot is `needs_onboarding` until it says its setup is done, then
                # `onboarded`; NULL for every other bot (backend/onboarding.py).
                H.add_column(c, "bot_config", "onboarding_state", "TEXT")
                H.add_column(c, "settings_changes", "via", "TEXT")
                H.add_column(c, "tasks", "request_id", "TEXT")
                # When an archived bot's agent last used its still-valid credential (backend/agents.py).
                H.add_column(c, "agents", "archived_seen", "TEXT")
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
                # Deleted tasks wait here until restored or purged; created unversioned, like the
                # trigger below, so it never takes a migration number another change needs.
                from .task_delete import ensure as ensure_task_trash
                ensure_task_trash(c)
                # A service key's scope (backend/service_keys.py); every older key files tasks. Unversioned like
                # the trash: an older release reads the table as before and never sees the column.
                H.add_column(c, "service_keys", "scope", "TEXT NOT NULL DEFAULT 'tasks'")
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
                                   ("task_links_repo_kind", "task_links(repo COLLATE NOCASE, kind)"),
                                   ("task_links_repo_url", "task_links(kind, state, url COLLATE NOCASE)"),
                                   ("attempts_bot_created", "attempts(bot, created)"),
                                   ("attempts_job", "attempts(job_id)"),
                                   ("attempts_runner_state", "attempts(runner_id, state)"),
                                   ("events_action_target", "events(action, target, ts)")):
                    c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")
                c.execute("DROP INDEX IF EXISTS jobs_state_bot_created")
                R.ensure_task_sweep(c)
                # The Assistant's pending actions (backend/assistant.py) and the "via" of a task-history row.
                from .assistant import ensure_schema as ensure_assistant_schema
                ensure_assistant_schema(c)
                # Tico no longer records: a meeting left waiting on audio or transcription is
                # finished with what it has (backend/meetings.py). Idempotent, no migration number.
                from .meetings import retire_capture
                retire_capture(c)
                # The queue trigger names the external harnesses it skips. When one is added
                # the stored trigger is rebuilt; no migration number, so no
                # collision with another branch's.
                stored = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                                   "AND name='queue_bot_message'").fetchone()
                if not stored or any("'" + h + "'" not in stored[0] for h in EXTERNAL_HARNESSES):
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
                # Teams, org groups and derived departments become groups, once (backend/groups.py). Idempotent,
                # so no migration number to collide with another branch's.
                from . import groups as Groups
                Groups.migrate(c, self.settings)
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
        c.close()

    @contextmanager
    def read(self):
        c = self.connect()
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def transaction(self):
        with self.read() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

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

    def mutate(self, identity, operation, key, body, fn, check=None):
        if not key or len(key) > 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1–200 characters", 422)
        hashed = digest(encode(body))
        principal = identity.actor + (":" + identity.attempt_id if identity.role == "bot" else "")
        refusal = None
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
                for task_id in task_ids:
                    replay_auth.task(c, replay_principal, task_id)
                privacy.require_payload(c, replay_principal, result)
                return result
            c.execute("SAVEPOINT domain_write")
            try:
                result = fn(c)
                c.execute("RELEASE domain_write")
            except H.Refused as exc:
                refusal = refused(c, identity, exc)
                result = {"_refusal": {"code": refusal.code, "detail": ("Private task write refused" if getattr(exc, "private", False) else refusal.detail),
                                       "status": refusal.status}}
            if not is_poll(operation, result):
                c.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
                          (principal, operation, key, hashed, encode(result), H.now()))
        if refusal:
            raise refusal
        return result

    def write(self, identity, fn):
        """`mutate` without an idempotency record, for a route whose request is its own: the same
        identity check under the write lock, and the same all-or-nothing refusal."""
        refusal = None
        with self.transaction() as c:
            from .auth import validate_identity
            validate_identity(c, identity)
            c.execute("SAVEPOINT domain_write")
            try:
                result = fn(c)
                c.execute("RELEASE domain_write")
            except H.Refused as exc:
                refusal = refused(c, identity, exc)
        if refusal:
            raise refusal
        return result

    def enqueue_existing(self):
        with self.transaction() as c:
            c.execute("INSERT OR IGNORE INTO jobs(id,message_id,bot,created) "
                      "SELECT id,id,substr(to_actor,5),created FROM messages NEW "
                      "WHERE to_actor LIKE 'bot:%' AND delivered_at IS NULL AND deleted_at IS NULL AND NOT "
                      + EXTERNAL_BOT_SQL)
