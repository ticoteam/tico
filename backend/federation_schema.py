"""The tables behind peering between Tico installs (backend/federation*.py). `ensure(c)` runs on every start, idempotently,
like backend/strategy.py: an older release never reads these tables.

- `federation_keys`: this install's Ed25519 keys, the private half sealed by the vault. One is current (`retired_at`
  NULL); registering a new one with HQ resets every peering (HQ side and here).
- `federation_peerings`: one row per peering with another install, keyed to HQ's peering id. The peer's key is pinned
  here at this install's approval; an envelope from the peer is checked against that key, not against HQ's list.
- `federation_directory`: HQ's published keys for installs this one met through a shared thread (author signatures).
- `federation_threads`: a thread shared over HQ. `thread_id` is this install's conversation; `origin_id` is the id the
  home gave it. The home numbers the stream (`high_seq`); a replica records how far it has applied the stream
  (`applied_through`). `removed_at_seq` is the membership tombstone on a removed install.
- `federation_members`: which installs the home shares a thread with, and from which home number (`joined_at_seq`). A
  message is for a member only when it was created after the member joined: `joined_message_seq` is the newest core
  message when the member joined, so eligibility is fixed at creation, not by when the home numbers it.
  `catch_up_after` is how far a catch-up the home is still paging for that member has got (NULL when none is).
- `federation_threads.subtree_seq`: how far the sub-threads of a shared thread have been looked at for deliveries.
- `federation_messages.sent_revision`: the newest revision of the message HQ took from this install (a tombstone is sent
  only when an earlier revision may have gone out).
- `federation_items`: the home's stream, one row per number. Each keeps the signed payload it was sent with, so a
  catch-up re-sends exactly what was signed.
- `federation_messages`: which local message an origin id is, its author and revision, and its home number once known.
- `federation_receipts`: every envelope processed, by (sender, envelope id), for replay refusal.
- `federation_outbox`: what this install still has to send: a delivery from core (a replica's post to the home), an
  item (the home's stream to a member), a catch-up request, or a refusal receipt for a message this install did not take.
  A delivery is rebuilt from the message as it is when it is sent; an item to a member the message was not for goes as
  a signed skip, so the member's stream has no hole. `thread_origin` is the root's origin id: a row waits behind an
  earlier row for the same peer and root, and only the first row of each such lane is sent.
- `federation_deferrals`: envelopes this install has acked to HQ but cannot apply yet (a rule check that has not run,
  a paused peering, or an envelope behind one of those in its own root). Kept per peer and root in arrival order; a
  lane waits for its first one, and other roots and peers are applied as they arrive.
"""

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS federation_directory (
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
CREATE TABLE IF NOT EXISTS federation_outbox (
  id TEXT PRIMARY KEY, recipient TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('delivery','item','catch_up','refusal')),
  delivery_id TEXT, item_id TEXT, payload BLOB, thread_origin TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE (kind, delivery_id), UNIQUE (kind, item_id, recipient));
CREATE INDEX IF NOT EXISTS federation_outbox_due ON federation_outbox(next_attempt_at);
CREATE TABLE IF NOT EXISTS federation_deferrals (
  id TEXT PRIMARY KEY, peer_install TEXT NOT NULL, thread_origin TEXT NOT NULL, envelope_id TEXT NOT NULL,
  headers_json TEXT NOT NULL, body BLOB NOT NULL, received_at REAL NOT NULL, reason TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  UNIQUE (peer_install, envelope_id));
CREATE TABLE IF NOT EXISTS federation_state (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1), cursor INTEGER NOT NULL DEFAULT 0,
  acked INTEGER NOT NULL DEFAULT 0, last_pull_at REAL, failing_since REAL, last_error TEXT NOT NULL DEFAULT '',
  listed_at REAL, company TEXT NOT NULL DEFAULT '');
INSERT OR IGNORE INTO federation_state(singleton) VALUES (1);
"""


# Columns added after their table first existed: a database created before them gets them on open.
COLUMNS = (("federation_peerings", "peer_company", "TEXT NOT NULL DEFAULT ''"), ("federation_peerings", "approved_by", "TEXT"),
           ("federation_state", "company", "TEXT NOT NULL DEFAULT ''"),
           ("federation_threads", "subtree_seq", "INTEGER NOT NULL DEFAULT 0"),
           ("federation_members", "joined_message_seq", "INTEGER NOT NULL DEFAULT 0"),
           ("federation_members", "catch_up_after", "INTEGER"),
           ("federation_messages", "sent_revision", "INTEGER"),
           ("federation_outbox", "thread_origin", "TEXT NOT NULL DEFAULT ''"))


def ensure(c):
    for statement in SCHEMA.split(";\n"):
        if statement.strip():
            c.execute(statement)
    for table, column, kind in COLUMNS:
        if column not in {row[1] for row in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
