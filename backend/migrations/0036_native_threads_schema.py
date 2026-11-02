"""Native threads' tables, columns, indexes and triggers, which their modules used to create on every start.

backend/threads.py, thread_search.py, thread_followups.py, thread_digest.py, teammate_access.py, slack_threads.py,
calls.py and federation_schema.py each ran CREATE ... IF NOT EXISTS, add_column and trigger replacement at every start.
This makes the same shape once, for a new file and for one that already has some or all of it: every CREATE is IF NOT
EXISTS, every column is added only when missing, and each trigger is dropped and made again with the text below (the
text the modules installed last). The SQL is frozen here; a later change to any of it is a new migration.

Order on a new file: hubdb's numbered steps have made conversations and messages, so their thread columns are added
here; conversations.scope, owner_id and room_key take hubdb's own declarations (it added them after the migrations, and
now finds them). The five message indexes are built by threads.migrate after its one-time backfill (building them over
unstamped rows first is slow); here they are made only on a file that has already had that backfill.

A few columns belong to tables made elsewhere: live_meetings (hubdb's numbered steps, so it is there), and
inbound_inboxes, org_rules, slack_threads and slack_posts, which their modules create after the migrations. Those four
have the column in their CREATE TABLE now; each column is added here only to a table that exists without it.

Search (message_search, FTS5) is skipped on a SQLite built without FTS5, as before: search then finds nothing. When the
index is made here on a file that has messages, it is filled from them.
"""
import sqlite3

# ----------------------------------------------------------------------------- threads (backend/threads.py)
CONVERSATION_COLUMNS = (
    ("scope", "TEXT NOT NULL DEFAULT 'direct'"), ("owner_id", "TEXT"), ("room_key", "TEXT"),
    ("audience", "TEXT"), ("audience_group_id", "TEXT"), ("parent_id", "TEXT"), ("parent_message_id", "TEXT"),
    ("root_id", "TEXT"), ("path", "TEXT"), ("last_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("last_unread_seq", "INTEGER NOT NULL DEFAULT 0"), ("archived_at", "TEXT"), ("updated_at", "TEXT"),
    ("retention_days", "INTEGER"))
MESSAGE_COLUMNS = (
    ("seq", "INTEGER"), ("origin_id", "TEXT"), ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ("restricted", "INTEGER NOT NULL DEFAULT 0"), ("legacy_audience", "TEXT"), ("edited_at", "TEXT"),
    ("deleted_at", "TEXT"))

THREAD_TABLES = (
    "CREATE TABLE IF NOT EXISTS registry_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS thread_members (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, actor TEXT NOT NULL, "
    "role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','member')), added_by TEXT NOT NULL, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (thread_id, actor))",
    "CREATE INDEX IF NOT EXISTS thread_members_actor ON thread_members(actor, thread_id)",
    "CREATE TABLE IF NOT EXISTS thread_followers (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, actor TEXT NOT NULL, "
    "following INTEGER NOT NULL DEFAULT 1 CHECK (following IN (0,1)), notify TEXT NOT NULL DEFAULT 'live' "
    "CHECK (notify IN ('live','mentions','digest','muted')), reason TEXT NOT NULL DEFAULT 'manual', "
    "last_read_seq INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "UNIQUE (thread_id, actor))",
    "CREATE INDEX IF NOT EXISTS thread_followers_actor ON thread_followers(actor, following, thread_id)",
    "CREATE TABLE IF NOT EXISTS message_deliveries (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, "
    "recipient TEXT NOT NULL, thread_id TEXT, root_id TEXT, "
    "mode TEXT NOT NULL CHECK (mode IN ('steer','queue','defer','digest','none')), reason TEXT NOT NULL, "
    "cause TEXT NOT NULL DEFAULT 'human', state TEXT NOT NULL DEFAULT 'pending' "
    "CHECK (state IN ('pending','delivered','read','capped','held','failed','moot')), "
    "revision INTEGER NOT NULL DEFAULT 0, carrier_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, "
    "error TEXT, delivered_at TEXT, read_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "UNIQUE (message_id, recipient))",
    "CREATE INDEX IF NOT EXISTS message_deliveries_waiting ON message_deliveries(recipient, created_at) "
    "WHERE state IN ('pending','delivered','capped','held')",
    "CREATE INDEX IF NOT EXISTS message_deliveries_capped ON message_deliveries(root_id, created_at) "
    "WHERE state='capped'",
    "CREATE INDEX IF NOT EXISTS message_deliveries_loop ON message_deliveries(root_id, created_at) "
    "WHERE cause='bot' AND recipient LIKE 'bot:%' AND mode IN ('steer','queue')",
    "CREATE INDEX IF NOT EXISTS message_deliveries_outside ON message_deliveries(state, next_attempt_at) "
    "WHERE recipient LIKE 'slack:%' OR recipient LIKE 'peer:%'",
    "CREATE INDEX IF NOT EXISTS message_deliveries_carrier ON message_deliveries(carrier_id) "
    "WHERE carrier_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS message_deliveries_thread ON message_deliveries(thread_id, recipient)",
    # backend/federation_threads.py UNSENT_WHERE, word for word, or SQLite does not use it.
    "CREATE INDEX IF NOT EXISTS message_deliveries_unsent ON message_deliveries(root_id, recipient) "
    "WHERE (state='failed' OR (state='pending' AND attempts>=8))",
    "CREATE TABLE IF NOT EXISTS message_mentions (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, actor TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('mention','ask','assign')), in_audience INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, UNIQUE (message_id, actor))",
    "CREATE INDEX IF NOT EXISTS message_mentions_actor ON message_mentions(actor, created_at)",
    "CREATE TABLE IF NOT EXISTS message_reactions (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, actor TEXT NOT NULL, "
    "emoji TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (message_id, actor, emoji))",
    "CREATE INDEX IF NOT EXISTS message_reactions_actor ON message_reactions(actor, created_at)",
    "CREATE TABLE IF NOT EXISTS message_revisions (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, "
    "origin_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT, deleted INTEGER NOT NULL DEFAULT 0, "
    "actor TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (origin_id, revision))",
    "CREATE INDEX IF NOT EXISTS message_revisions_message ON message_revisions(message_id, revision)",
    "CREATE TABLE IF NOT EXISTS sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS thread_routings (message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
    "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
)
DELIVERY_COLUMNS = (("unsolicited", "INTEGER NOT NULL DEFAULT 0"),)

THREAD_INDEXES = (
    "CREATE INDEX IF NOT EXISTS message_deliveries_unsolicited ON message_deliveries(recipient, created_at) "
    "WHERE unsolicited=1",
    "CREATE INDEX IF NOT EXISTS message_deliveries_digest ON message_deliveries(recipient, created_at) "
    "WHERE mode='digest' AND state='pending'",
    "CREATE INDEX IF NOT EXISTS threads_retention ON conversations(id) WHERE retention_days IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS threads_parent_message ON conversations(parent_message_id) "
    "WHERE parent_message_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS threads_one_home ON conversations(owner_id) "
    "WHERE audience='home' AND closed_at IS NULL AND archived_at IS NULL",
    "CREATE INDEX IF NOT EXISTS threads_root ON conversations(root_id)",
    "CREATE INDEX IF NOT EXISTS threads_path ON conversations(path)",
    "CREATE INDEX IF NOT EXISTS threads_recent ON conversations(root_id, last_seq) WHERE archived_at IS NULL",
)
# threads._MESSAGE_INDEXES, word for word: made here only on a file whose backfill already ran (threads.migrate
# makes them otherwise).
MESSAGE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS messages_seq ON messages(seq) WHERE seq IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_origin ON messages(origin_id) WHERE origin_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_thread_seq ON messages(conversation_id, seq)",
    "CREATE INDEX IF NOT EXISTS messages_thread_human ON messages(conversation_id, seq) WHERE sender_id LIKE 'human:%'",
    "CREATE INDEX IF NOT EXISTS messages_legacy_audience ON messages(conversation_id, legacy_audience) "
    "WHERE legacy_audience IS NOT NULL",
)

THREAD_TRIGGERS = {
    "threads_conversations_stamp": (
        'CREATE TRIGGER threads_conversations_stamp AFTER INSERT ON conversations WHEN NEW.root_id IS NULL '
        "BEGIN UPDATE conversations SET root_id=NEW.id, path='/'||NEW.id||'/', "
        'updated_at=COALESCE(NEW.updated_at,NEW.created_at) WHERE id=NEW.id; END'),
    "threads_messages_stamp": (
        'CREATE TRIGGER threads_messages_stamp AFTER INSERT ON messages WHEN NOT COALESCE(json_extract(CASE '
        "WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.carries'),'')<>'' BEGIN INSERT OR IGNORE INTO "
        "sequences(name,value) VALUES('messages',0);UPDATE sequences SET value=value+1 WHERE "
        "name='messages';UPDATE messages SET seq=(SELECT value FROM sequences WHERE name='messages'), "
        'origin_id=CASE WHEN (NEW.origin_id IS NULL OR EXISTS (SELECT 1 FROM messages o WHERE '
        "o.origin_id=NEW.origin_id AND o.id<>NEW.id)) THEN lower(hex(randomblob(16)))||'@'||COALESCE((SELECT "
        "json_extract(value,'$.install_id') FROM registry_metadata WHERE key='usage-count'),'local') ELSE "
        'NEW.origin_id END, restricted=CASE WHEN (NEW.origin_id IS NULL OR EXISTS (SELECT 1 FROM messages o '
        'WHERE o.origin_id=NEW.origin_id AND o.id<>NEW.id)) THEN 1 ELSE NEW.restricted END WHERE '
        "id=NEW.id;UPDATE conversations SET last_seq=(SELECT value FROM sequences WHERE name='messages'), "
        "last_unread_seq=CASE WHEN (NEW.sender_id LIKE 'human:%' OR COALESCE(NEW.recipient_id,'')<>'') AND "
        "NOT (json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.import') IS NOT NULL OR "
        "json_extract(CASE WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.replay') IS NOT NULL) THEN (SELECT "
        "value FROM sequences WHERE name='messages') ELSE last_unread_seq END WHERE id=NEW.conversation_id; "
        'END'),
    "threads_primary_delivery": (
        'CREATE TRIGGER threads_primary_delivery AFTER INSERT ON messages WHEN '
        "COALESCE(NEW.recipient_id,'')<>'' AND NOT COALESCE(json_extract(CASE WHEN json_valid(NEW.refs) THEN "
        "NEW.refs END,'$.carries'),'')<>'' BEGIN INSERT OR IGNORE INTO "
        'message_deliveries(id,message_id,recipient,thread_id,root_id,mode,reason,cause,state,revision,'
        'delivered_at,read_at,created_at,updated_at,unsolicited) '
        'VALUES(lower(hex(randomblob(16))),NEW.id,NEW.recipient_id,NEW.conversation_id,(SELECT '
        'COALESCE(root_id,id) FROM conversations WHERE id=NEW.conversation_id),CASE WHEN NEW.recipient_id '
        "LIKE 'slack:%' OR NEW.recipient_id LIKE 'peer:%' THEN 'none' WHEN COALESCE(json_extract(CASE WHEN "
        "json_valid(NEW.refs) THEN NEW.refs END,'$.quiet'),0) THEN 'defer' ELSE 'queue' END,CASE WHEN "
        "NEW.recipient_id LIKE 'slack:%' OR NEW.recipient_id LIKE 'peer:%' THEN 'outside' WHEN "
        "NEW.kind='ask' THEN 'ask' ELSE 'addressed' END,CASE WHEN NEW.sender_id LIKE 'bot:%' THEN 'bot' WHEN "
        "NEW.sender_id LIKE 'human:%' THEN 'human' WHEN NEW.sender_id LIKE 'slack:%' OR NEW.sender_id LIKE "
        "'peer:%' THEN 'outside' ELSE 'keeper' END,CASE WHEN NEW.delivered_at IS NOT NULL AND NEW.read_at IS "
        "NOT NULL THEN 'read' WHEN NEW.delivered_at IS NOT NULL THEN 'delivered' ELSE 'pending' "
        'END,COALESCE(NEW.revision,0),NEW.delivered_at,NEW.read_at,NEW.created_at,NEW.created_at,'
        'COALESCE(json_extract(CASE '
        "WHEN json_valid(NEW.refs) THEN NEW.refs END,'$.unsolicited'),0)); END"),
    "threads_delivery_receipts": (
        'CREATE TRIGGER threads_delivery_receipts AFTER UPDATE OF delivered_at, read_at ON messages BEGIN '
        'UPDATE message_deliveries SET delivered_at=NEW.delivered_at, read_at=NEW.read_at, state=CASE WHEN '
        "state IN ('capped','held','failed','moot') THEN state ELSE CASE WHEN NEW.delivered_at IS NOT NULL "
        "AND NEW.read_at IS NOT NULL THEN 'read' WHEN NEW.delivered_at IS NOT NULL THEN 'delivered' ELSE "
        "'pending' END END, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE message_id=NEW.id AND "
        'recipient=NEW.recipient_id;UPDATE message_deliveries SET delivered_at=NEW.delivered_at, state=CASE '
        "WHEN state='pending' AND NEW.delivered_at IS NOT NULL THEN 'delivered' ELSE state END, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE carrier_id=NEW.id; END"),
    "threads_revisions_deleted": (
        'CREATE TRIGGER threads_revisions_deleted AFTER UPDATE OF deleted_at ON messages WHEN NEW.deleted_at '
        "IS NOT NULL AND OLD.deleted_at IS NULL BEGIN UPDATE message_revisions SET body='' WHERE "
        'message_id=NEW.id; END'),
}

# ----------------------------------------------------------------------------- the thread helpers' own tables
HELPER_TABLES = (
    # backend/thread_followups.py
    "CREATE TABLE IF NOT EXISTS thread_followups (message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
    "to_actor TEXT NOT NULL, nudge_message_id TEXT, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS thread_followups_conversation ON thread_followups(conversation_id, created_at)",
    "CREATE TABLE IF NOT EXISTS thread_earlier_notices (bot TEXT NOT NULL, actor TEXT NOT NULL, message_id TEXT, "
    "created_at TEXT NOT NULL, PRIMARY KEY (bot, actor))",
    # backend/thread_digest.py
    "CREATE TABLE IF NOT EXISTS thread_digests (thread_id TEXT NOT NULL, actor TEXT NOT NULL, "
    "emailed_seq INTEGER NOT NULL DEFAULT 0, emailed_at TEXT, PRIMARY KEY (thread_id, actor))",
    # backend/teammate_access.py
    "CREATE TABLE IF NOT EXISTS teammate_accesses (id TEXT PRIMARY KEY, target_type TEXT NOT NULL "
    "CHECK (target_type IN ('human','group')), target_id TEXT NOT NULL, access TEXT NOT NULL, "
    "revision INTEGER NOT NULL DEFAULT 1, updated_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS teammate_accesses_target ON teammate_accesses(target_type, target_id)",
)

# ----------------------------------------------------------------------------- search (backend/thread_search.py)
SEARCH_KEYS = ("CREATE TABLE IF NOT EXISTS message_search_keys(id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "message_id TEXT NOT NULL UNIQUE)")
SEARCH_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(body, message_id UNINDEXED, "
              "tokenize='porter unicode61')")
SEARCHABLE = ("m.body IS NOT NULL AND m.body<>'' AND m.deleted_at IS NULL AND "
              "(CASE WHEN json_valid(m.refs) THEN json_extract(m.refs,'$.carries') END) IS NULL")
SEARCH_TRIGGERS = {
    "message_search_insert": (
        'CREATE TRIGGER message_search_insert AFTER INSERT ON messages BEGIN INSERT OR IGNORE INTO '
        "message_search_keys(message_id) SELECT NEW.id WHERE NEW.body IS NOT NULL AND NEW.body<>'' AND "
        "NEW.deleted_at IS NULL AND (CASE WHEN json_valid(NEW.refs) THEN json_extract(NEW.refs,'$.carries') "
        'END) IS NULL; INSERT INTO message_search(rowid, body, message_id) SELECT k.id, '
        'substr(NEW.body,1,20000), NEW.id FROM message_search_keys k WHERE k.message_id=NEW.id AND NEW.body '
        "IS NOT NULL AND NEW.body<>'' AND NEW.deleted_at IS NULL AND (CASE WHEN json_valid(NEW.refs) THEN "
        "json_extract(NEW.refs,'$.carries') END) IS NULL; END"),
    "message_search_update": (
        'CREATE TRIGGER message_search_update AFTER UPDATE OF body, deleted_at, refs ON messages BEGIN '
        'DELETE FROM message_search WHERE rowid=(SELECT id FROM message_search_keys WHERE '
        'message_id=OLD.id); INSERT OR IGNORE INTO message_search_keys(message_id) SELECT NEW.id WHERE '
        "NEW.body IS NOT NULL AND NEW.body<>'' AND NEW.deleted_at IS NULL AND (CASE WHEN "
        "json_valid(NEW.refs) THEN json_extract(NEW.refs,'$.carries') END) IS NULL; INSERT INTO "
        'message_search(rowid, body, message_id) SELECT k.id, substr(NEW.body,1,20000), NEW.id FROM '
        "message_search_keys k WHERE k.message_id=NEW.id AND NEW.body IS NOT NULL AND NEW.body<>'' AND "
        "NEW.deleted_at IS NULL AND (CASE WHEN json_valid(NEW.refs) THEN json_extract(NEW.refs,'$.carries') "
        'END) IS NULL; END'),
    "message_search_delete": (
        'CREATE TRIGGER message_search_delete AFTER DELETE ON messages BEGIN DELETE FROM message_search '
        'WHERE rowid=(SELECT id FROM message_search_keys WHERE message_id=OLD.id); DELETE FROM '
        'message_search_keys WHERE message_id=OLD.id; END'),
}

# ----------------------------------------------------------------------------- Slack bridge (backend/slack_threads.py)
SLACK_TABLES = (
    """CREATE TABLE IF NOT EXISTS slack_thread_links (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  channel_ref TEXT NOT NULL,
  channel_id TEXT NOT NULL DEFAULT '',
  channel_name TEXT NOT NULL DEFAULT '',
  channel_private INTEGER NOT NULL DEFAULT 0,
  audience TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK (state IN ('requested','active','suspended','refused','unlinked')),
  reason TEXT NOT NULL DEFAULT '',
  history_state TEXT NOT NULL DEFAULT 'off' CHECK (history_state IN ('off','pending','running','done','failed')),
  history_count INTEGER NOT NULL DEFAULT 0,
  linked_by TEXT NOT NULL,
  needs_task_id TEXT,
  failure_task_id TEXT,
  checked_at TEXT,
  activated_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS slack_thread_links_thread ON slack_thread_links(thread_id, state)",
    "CREATE INDEX IF NOT EXISTS slack_thread_links_channel ON slack_thread_links(channel_id, state)",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_thread_links_open_thread ON slack_thread_links(thread_id) "
    "WHERE state IN ('requested','active','suspended')",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_thread_links_open_channel ON slack_thread_links(channel_id) "
    "WHERE state IN ('active','suspended') AND channel_id <> ''",
    """CREATE TABLE IF NOT EXISTS slack_link_members (
  link_id TEXT NOT NULL REFERENCES slack_thread_links(id),
  slack_user_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (link_id, slack_user_id))""",
    """CREATE TABLE IF NOT EXISTS slack_message_links (
  id TEXT PRIMARY KEY,
  link_id TEXT NOT NULL REFERENCES slack_thread_links(id),
  channel_id TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  ts TEXT NOT NULL DEFAULT '',
  thread_ts TEXT NOT NULL DEFAULT '',
  message_id TEXT,
  delivery_id TEXT,
  origin_id TEXT,
  actor TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL CHECK (state IN ('sent','held','deleted','sending','uncertain','ready','failed')),
  edit_ts TEXT NOT NULL DEFAULT '',
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_message_links_ts ON slack_message_links(channel_id, ts) WHERE ts <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_message_links_out ON slack_message_links(link_id, message_id) "
    "WHERE direction = 'out'",
    "CREATE INDEX IF NOT EXISTS slack_message_links_message ON slack_message_links(message_id)",
    "CREATE INDEX IF NOT EXISTS slack_message_links_state ON slack_message_links(state, updated_at)",
    """CREATE TABLE IF NOT EXISTS slack_inbound_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  link_id TEXT NOT NULL REFERENCES slack_thread_links(id),
  event_key TEXT NOT NULL UNIQUE,
  event TEXT NOT NULL,
  created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS slack_inbound_events_link ON slack_inbound_events(link_id, seq)",
)

# ----------------------------------------------------------------------------- Tico Meet calls (backend/calls.py)
CALL_TABLES = """
CREATE TABLE IF NOT EXISTS calls(
 id TEXT PRIMARY KEY,
 thread_id TEXT NOT NULL,
 live_meeting_id TEXT NOT NULL UNIQUE REFERENCES live_meetings(id) ON DELETE CASCADE,
 connector_actor TEXT NOT NULL,
 title TEXT NOT NULL,
 state TEXT NOT NULL CHECK (state IN ('live','ended')),
 room_name TEXT NOT NULL UNIQUE,
 end_reason TEXT,
 started_at TEXT NOT NULL,
 ended_at TEXT,
 media_seen_at TEXT,
 transcript_posted_at TEXT,
 action_items_posted_at TEXT,
 finalized_at TEXT,
 wrapped_up_at TEXT,
 action_items_failed_at TEXT,
 action_items_attempts INTEGER NOT NULL DEFAULT 0,
 action_items_retry_at TEXT,
 action_items_given_up_at TEXT,
 speak_replies INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS calls_thread ON calls(thread_id, started_at);
CREATE INDEX IF NOT EXISTS calls_state ON calls(state);
CREATE TABLE IF NOT EXISTS call_holds(
 id TEXT PRIMARY KEY,
 call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK (kind IN ('transcript','action_items')),
 reason TEXT NOT NULL,
 detail TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS call_holds_call ON call_holds(call_id);
CREATE TABLE IF NOT EXISTS call_participants(
 call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
 actor TEXT NOT NULL,
 joined_at TEXT NOT NULL,
 left_at TEXT,
 removed_at TEXT,
 media_removed_at TEXT,
 PRIMARY KEY(call_id, actor));
"""

# ----------------------------------------------------------------------------- federation (backend/federation_schema.py)
FEDERATION_TABLES = """
CREATE TABLE IF NOT EXISTS federation_keys (
  key_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, sealed_private_key TEXT NOT NULL,
  registered_at TEXT, created_at TEXT NOT NULL, retired_at TEXT);
CREATE TABLE IF NOT EXISTS federation_peerings (
  id TEXT PRIMARY KEY, hq_peering_id TEXT NOT NULL UNIQUE, peer_install TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('outgoing','incoming')),
  status TEXT NOT NULL CHECK (status IN ('pending','active','revoked')),
  approved_at TEXT, peer_key_id TEXT, peer_public_key TEXT, paused_at TEXT, paused_by TEXT,
  revoked_at TEXT, peer_company TEXT NOT NULL DEFAULT '', approved_by TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS federation_peerings_live ON federation_peerings(peer_install) WHERE status <> 'revoked';
CREATE TABLE IF NOT EXISTS federation_directory_entries (
  install TEXT NOT NULL, key_id TEXT NOT NULL, public_key TEXT NOT NULL, fetched_at TEXT NOT NULL,
  PRIMARY KEY (install, key_id));
CREATE TABLE IF NOT EXISTS federation_threads (
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE, home_install TEXT NOT NULL, origin_id TEXT NOT NULL,
  applied_through INTEGER NOT NULL DEFAULT 0, high_seq INTEGER NOT NULL DEFAULT 0, removed_at_seq INTEGER,
  catch_up_requested_at REAL, subtree_seq INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (home_install, origin_id));
CREATE TABLE IF NOT EXISTS federation_members (
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, install TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','removed')),
  joined_at_seq INTEGER NOT NULL, joined_message_seq INTEGER NOT NULL DEFAULT 0, removed_at_seq INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (thread_id, install));
CREATE TABLE IF NOT EXISTS federation_items (
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, home_seq INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('message','membership')),
  origin_id TEXT, revision INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL, envelope_id TEXT NOT NULL,
  sent_at INTEGER NOT NULL, key_id TEXT NOT NULL, signature TEXT NOT NULL, payload BLOB NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (thread_id, home_seq));
CREATE UNIQUE INDEX IF NOT EXISTS federation_items_revision ON federation_items(origin_id, revision)
  WHERE origin_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS federation_messages (
  id TEXT PRIMARY KEY, origin_id TEXT NOT NULL UNIQUE, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
  author TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, home_seq INTEGER, sent_revision INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS federation_messages_message ON federation_messages(message_id);
CREATE TABLE IF NOT EXISTS federation_receipts (
  id TEXT PRIMARY KEY, peer_install TEXT NOT NULL, envelope_id TEXT NOT NULL, outcome TEXT NOT NULL,
  received_at TEXT NOT NULL, UNIQUE (peer_install, envelope_id));
CREATE TABLE IF NOT EXISTS federation_outbox_items (
  id TEXT PRIMARY KEY, recipient TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('delivery','item','catch_up','refusal')),
  delivery_id TEXT, item_id TEXT, payload BLOB, thread_origin TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE (kind, delivery_id), UNIQUE (kind, item_id, recipient));
CREATE INDEX IF NOT EXISTS federation_outbox_items_due ON federation_outbox_items(next_attempt_at);
CREATE TABLE IF NOT EXISTS federation_deferrals (
  id TEXT PRIMARY KEY, peer_install TEXT NOT NULL, thread_origin TEXT NOT NULL, envelope_id TEXT NOT NULL,
  headers TEXT NOT NULL, body BLOB NOT NULL, received_at REAL NOT NULL, reason TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  UNIQUE (peer_install, envelope_id));
CREATE TABLE IF NOT EXISTS federation_states (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1), cursor INTEGER NOT NULL DEFAULT 0,
  acked INTEGER NOT NULL DEFAULT 0, last_pull_at REAL, started_failing_at REAL, last_error TEXT NOT NULL DEFAULT '',
  listed_at REAL, company TEXT NOT NULL DEFAULT '');
INSERT OR IGNORE INTO federation_states(singleton) VALUES (1);
"""

# ----------------------------------------------------------------------------- columns added after a table first existed
# (table, column, declaration), each added when its table exists without it. The tables above exist by now, and so does
# live_meetings (hubdb's numbered steps); the last five belong to tables made after the migrations (inbound, org_rules,
# store's Slack schema), whose CREATE TABLE now has the column, so on a new file there is nothing to add to them.
COLUMNS = (
    ("calls", "finalized_at", "TEXT"), ("calls", "wrapped_up_at", "TEXT"), ("calls", "action_items_failed_at", "TEXT"),
    ("calls", "action_items_attempts", "INTEGER NOT NULL DEFAULT 0"), ("calls", "action_items_retry_at", "TEXT"),
    ("calls", "action_items_given_up_at", "TEXT"), ("call_participants", "media_removed_at", "TEXT"),
    ("slack_thread_links", "failure_task_id", "TEXT"),
    ("federation_peerings", "peer_company", "TEXT NOT NULL DEFAULT ''"), ("federation_peerings", "approved_by", "TEXT"),
    ("federation_states", "company", "TEXT NOT NULL DEFAULT ''"),
    ("federation_states", "history_through", "REAL"), ("federation_states", "history_cursor", "TEXT"),
    ("federation_states", "history_top", "REAL"),
    ("federation_threads", "subtree_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("federation_members", "joined_message_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("federation_members", "catch_up_after", "INTEGER"),
    ("federation_messages", "sent_revision", "INTEGER"),
    ("federation_outbox_items", "thread_origin", "TEXT NOT NULL DEFAULT ''"),
    ("live_meetings", "thread_id", "TEXT"),
    ("inbound_inboxes", "audience", "TEXT NOT NULL DEFAULT 'company'"), ("inbound_inboxes", "group_id", "TEXT"),
    ("org_rules", "where_terms", "TEXT NOT NULL DEFAULT '[]'"),
    ("slack_threads", "recipient_actor", "TEXT"), ("slack_posts", "recipient_actor", "TEXT"),
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add(conn, table, column, declaration):
    columns = _columns(conn, table)
    if columns and column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _script(conn, sql):
    for statement in sql.split(";\n"):
        if statement.strip():
            conn.execute(statement)


def _exists(conn, kind, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type=? AND name=?", (kind, name)).fetchone() is not None


def _triggers(conn, triggers):
    for name, sql in triggers.items():
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(sql)


def threads_schema(conn):
    """The threads columns, tables, triggers and indexes (all but the message indexes on a file not yet backfilled)."""
    for column, declaration in CONVERSATION_COLUMNS:
        _add(conn, "conversations", column, declaration)
    for column, declaration in MESSAGE_COLUMNS:
        _add(conn, "messages", column, declaration)
    for statement in THREAD_TABLES:
        conn.execute(statement)
    for column, declaration in DELIVERY_COLUMNS:
        _add(conn, "message_deliveries", column, declaration)
    conn.execute("INSERT OR IGNORE INTO sequences(name,value) SELECT 'messages', COALESCE(MAX(rowid),0) FROM messages")
    _triggers(conn, THREAD_TRIGGERS)
    conn.execute("DROP INDEX IF EXISTS message_deliveries_inbox")     # replaced by message_deliveries_waiting
    for name in ("messages_seq", "messages_origin"):    # unique in an early build of threads
        found = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if found and "UNIQUE" in str(found[0]).upper():
            conn.execute(f"DROP INDEX {name}")
    for statement in THREAD_INDEXES:
        conn.execute(statement)
    if conn.execute("SELECT 1 FROM registry_metadata WHERE key='threads-migration'").fetchone():
        for statement in MESSAGE_INDEXES:
            conn.execute(statement)


def search_schema(conn):
    """The search index and its triggers; filled from the messages there when it is new. False without FTS5."""
    if not _exists(conn, "table", "messages"):
        return False
    existed = _exists(conn, "table", "message_search")
    conn.execute(SEARCH_KEYS)
    try:
        conn.execute(SEARCH_FTS)
    except sqlite3.OperationalError:
        return False
    _triggers(conn, SEARCH_TRIGGERS)
    if not existed:
        conn.execute(f"INSERT OR IGNORE INTO message_search_keys(message_id) SELECT m.id FROM messages m "
                     f"WHERE {SEARCHABLE} ORDER BY m.rowid")
        conn.execute("INSERT INTO message_search(rowid, body, message_id) SELECT k.id, substr(m.body,1,20000), m.id "
                     f"FROM message_search_keys k JOIN messages m ON m.id=k.message_id WHERE {SEARCHABLE}")
    return True


def up(conn):
    threads_schema(conn)
    for statement in HELPER_TABLES:
        conn.execute(statement)
    search_schema(conn)
    for statement in SLACK_TABLES:
        conn.execute(statement)
    _script(conn, CALL_TABLES)
    _script(conn, FEDERATION_TABLES)
    for table, column, declaration in COLUMNS:
        _add(conn, table, column, declaration)
