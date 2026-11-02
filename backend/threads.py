"""Native threads: who reads a thread, how a message is written and delivered, read state, edits, the migration.

The contract every track builds on is docs/dev/threads-contract.md; this module is its implementation. A thread is a
`conversations` row (the table keeps its name), and its id is the conversation id.

Who reads (C1). Every thread has an `audience`, evaluated on its root (a sub-thread inherits the root's):

    company       every company person
    team          the people in `audience_group_id` and the groups under it, and the company owner
    members       the actors in `thread_members` (a `group:<id>` member names everyone in it, live); hidden
    task          whoever may read the task (auth.task)
    home          whoever holds Read on `owner_actor` (a bot, a person or a group; backend/teammate_access.py)
    personal      `owner_actor` and the members; no owner bypass; the Strategy chat stays with the owner seat
    participants  the actors in `participants_json`, and the company owner (migrated direct conversations)
    meeting       backend/live_privacy.py, as before (migrated meeting rooms)

Every bot reads what it is a member or participant of, Homes it may read, task threads it may read and its own open
asks to a person. An internal bot (in a turn) also reads, as a person in its seat would, every company thread and its
group's team threads, plus the tree its attempt was handed (`attempt_conversations`); an external agent never gets
company or team standing read. The same rule is said three times: in Python (`readable`, which
`Auth.conversation` calls), as SQL for `hub sql` (`readable_sql`, `message_sql`) and for the SSE stream
(`readable_ids`). backend/tests/test_threads_privacy.py runs one matrix through all three.

Inside a readable thread a message can still be hidden: history migrated from a shared bot room keeps its old
readers (`legacy_audience`, evaluated live by rooms.shared_member_bots), and a message marked `restricted` at write
time (a reply, a reference to a task or meeting, an unknown refs key, a task or meeting thread) passes
task_privacy.message_readable too. An unrestricted message needs nothing more than its thread.

Writes (C2, C3, C5). Every message is written by hubdb._write_message, which calls `stamp` before its insert and
`record` after it; `post` is the new door that REST, the CLI and MCP use, and it ends in hubdb.say (with an
addressee) or the same insert (without one). The primary recipient's delivery row is kept by a trigger on
`messages` (one row per message with a `to_actor`, its state following `delivered_at`/`read_at`), so every legacy
writer and reader agrees with the inbox. `record` adds the rest: mention rows (mentions never grant access), the
deliveries for other mentioned teammates and DM members, woken bots (each through a carrier: a keeper notice whose
job starts the run, since `jobs.message_id` is unique), followers, and the loop guard, which writes a bot-caused wake
past LOOP_GUARD_PER_HOUR in a root as `capped` and lets it go later (`resume_capped`). What takes longer than a post
runs after it: routing a message that names nobody (backend/thread_routing.py), a bot's digest run
(backend/thread_bot_digest.py) and retention (backend/thread_retention.py).

Order (C4). `messages.seq` comes from the `sequences` table, set by a trigger on insert (carriers get none);
`last_seq` and `last_unread_seq` on the thread follow it. Read state is `thread_followers.last_read_seq`.

Every message gets an `origin_id` ('<uuid v7>@<install id>') and a `revision`; `message_revisions` keeps each
(origin_id, revision) seen, so a replicated edit or delete is applied once and an older one never wins.
"""

import contextlib
import contextvars
import json
import os
import re
import secrets
import time
import uuid

from . import people as P
from .store import H, Problem

AUDIENCES = ("company", "team", "members", "task", "home", "personal", "participants", "meeting")
PRIVATE_AUDIENCES = ("members", "personal", "participants", "meeting")
PUBLIC_AUDIENCES = ("company", "team")
# refs keys that cannot name a private task, a meeting or another message: a message carrying only these skips the
# per-message check once its thread is readable. Anything else marks it `restricted`.
SAFE_REF_KEYS = frozenset({"via", "rule", "quiet", "unsolicited", "depth", "wake", "reason", "origin", "import",
                           "replay", "caused_by", "bot_to_bot", "wake_rule", "mentions", "warnings", "thread"})
MODES = ("steer", "queue", "defer", "digest", "none")
RETENTION_MAX_DAYS = 36500
REOPEN_ROOTS = 200        # roots one pass of the loop guard's scheduler looks at
ANSWER_WINDOW = 100       # messages one solicited check reads back (a person's message further back is not being answered)
REASONS = ("dm", "mention", "ask", "assign", "addressed", "home", "sub_thread", "routing", "digest", "member",
           "outside")
STATES = ("pending", "delivered", "read", "capped", "held", "failed", "moot")
NOTIFY = ("live", "mentions", "digest", "muted")
OUTSIDE_PREFIXES = ("slack:", "peer:")
REMOTE_ACTOR = re.compile(r"^(human|bot):[^@\s]+@([^@\s]+)$")   # a federation guest: <kind>:<name>@<install>
LOOP_GUARD_PER_HOUR = int(os.environ.get("TICO_THREAD_LOOP_GUARD") or 6)
MIGRATION_KEY = "threads-migration"
STRATEGY_ROOM = "ceo"           # rooms.STRATEGY_ROOM, read here without importing rooms
EDITABLE_KINDS = ("say",)
MAX_TITLE = 200
MAX_PAGE = 200
MAX_PAGE_BYTES = 512 * 1024
BRANCH_TAIL = 20                  # a timeline shows this many of a branch's latest messages; the rest is "N more"
MAX_INLINE_ROWS = 1000            # a page opens no deeper branches past this many inline rows
MAX_BRANCH_LEVELS = 16
BRANCH_CHECKS = 200               # restricted rows a branch count checks one by one per level

# Set while `post` writes, so `record` knows the message came through the new door: only then do mentions of other
# teammates wake them and the loop guard count it. A legacy writer (hubdb.say from an older route) keeps its own rules.
POSTING = contextvars.ContextVar("threads_posting", default=None)

_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
_MEETING_PREFIX = "live-meeting:"   # live_privacy.ROOM_PREFIX (test_threads_privacy checks they agree)


def _q(value):
    return "'" + str(value).replace("'", "''") + "'"


def _qlist(values):
    return "(" + ",".join(_q(v) for v in values) + ")"


def _now():
    return H.now()


# ----------------------------------------------------------------------------- schema
CONVERSATION_COLUMNS = (
    ("audience", "TEXT"), ("audience_group_id", "TEXT"), ("parent_id", "TEXT"), ("parent_message_id", "TEXT"),
    ("root_id", "TEXT"), ("path", "TEXT"), ("last_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("last_unread_seq", "INTEGER NOT NULL DEFAULT 0"), ("archived_at", "TEXT"), ("updated_at", "TEXT"),
    ("scope", "TEXT"), ("owner_actor", "TEXT"), ("room_key", "TEXT"), ("retention_days", "INTEGER"))
MESSAGE_COLUMNS = (
    ("seq", "INTEGER"), ("origin_id", "TEXT"), ("revision", "INTEGER NOT NULL DEFAULT 0"),
    ("restricted", "INTEGER NOT NULL DEFAULT 0"), ("legacy_audience", "TEXT"), ("edited_at", "TEXT"),
    ("deleted_at", "TEXT"))
DELIVERY_COLUMNS = (("unsolicited", "INTEGER NOT NULL DEFAULT 0"),)

TABLES = (
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
    # Outside messages not sent (backend/federation_threads.py UNSENT_WHERE, word for word, or SQLite does not use it).
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
    # Messages waiting to be routed (backend/thread_routing.py), written in the post and read by its worker.
    "CREATE TABLE IF NOT EXISTS thread_routings (message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
    "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS message_deliveries_unsolicited ON message_deliveries(recipient, created_at) "
    "WHERE unsolicited=1",
    "CREATE INDEX IF NOT EXISTS message_deliveries_digest ON message_deliveries(recipient, created_at) "
    "WHERE mode='digest' AND state='pending'",
    "CREATE INDEX IF NOT EXISTS threads_retention ON conversations(id) WHERE retention_days IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS threads_parent_message ON conversations(parent_message_id) "
    "WHERE parent_message_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS threads_one_home ON conversations(owner_actor) "
    "WHERE audience='home' AND closed_at IS NULL AND archived_at IS NULL",
    "CREATE INDEX IF NOT EXISTS threads_root ON conversations(root_id)",
    "CREATE INDEX IF NOT EXISTS threads_path ON conversations(path)",
    "CREATE INDEX IF NOT EXISTS threads_recent ON conversations(root_id, last_seq) WHERE archived_at IS NULL",
    # Not unique: an insert copying another row's seq or origin must not fail before the stamp trigger replaces them.
    # The sequence makes seqs unique; message_revisions makes (origin_id, revision) unique.
    "CREATE INDEX IF NOT EXISTS messages_seq ON messages(seq) WHERE seq IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_origin ON messages(origin_id) WHERE origin_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS messages_thread_seq ON messages(conversation_id, seq)",
    # The people's messages of a thread: the sidebar's unread rank skips a long run of bot posts past someone's mark.
    "CREATE INDEX IF NOT EXISTS messages_thread_human ON messages(conversation_id, seq) WHERE from_actor LIKE 'human:%'",
    # Migrated shared-room history: which Homes still hold messages read by their old readers.
    "CREATE INDEX IF NOT EXISTS messages_legacy_audience ON messages(conversation_id, legacy_audience) "
    "WHERE legacy_audience IS NOT NULL",
)

# The room a shared conversation belongs to, as SQL over a row named {r}: rooms.room_bot for a shared room.
_ROOM_BOT = "substr({r}.room_key,1,instr({r}.room_key||':',':')-1)"
_FIRST_HUMAN = ("(SELECT value FROM json_each(CASE WHEN json_valid({r}.participants_json) THEN {r}.participants_json END) "
                "WHERE value LIKE 'human:%' LIMIT 1)")
_PARTICIPANTS = "json_each(CASE WHEN json_valid({r}.participants_json) THEN {r}.participants_json END)"


# rooms.room_bot as SQL over a row named {r}: a shared room's key names its bot, any other room its first bot.
_LEGACY_ROOM_BOT = ("(CASE WHEN {r}.scope='shared' AND COALESCE({r}.room_key,'')<>'' "
                    "THEN substr({r}.room_key,1,instr({r}.room_key||':',':')-1) "
                    "ELSE (SELECT substr(value,5) FROM json_each(CASE WHEN json_valid({r}.participants_json) "
                    "THEN {r}.participants_json END) WHERE value LIKE 'bot:%' LIMIT 1) END)")


def _derived_audience(r):
    """The audience a conversation written before threads (or by a writer that sets none) has, as SQL."""
    return (f"CASE WHEN substr(COALESCE({r}.room_key,''),1,{len(_MEETING_PREFIX)})='{_MEETING_PREFIX}' THEN 'meeting' "
            f"WHEN {r}.scope='personal' THEN 'personal' WHEN {r}.scope='shared' THEN 'home' "
            f"WHEN {r}.kind='task' AND {r}.task_id IS NOT NULL THEN 'task' ELSE 'participants' END")


def _derived_owner(r):
    return (f"CASE WHEN {r}.scope='shared' AND COALESCE({r}.room_key,'')<>'' THEN 'bot:'||{_ROOM_BOT.format(r=r)} "
            f"WHEN {r}.scope='personal' THEN {_FIRST_HUMAN.format(r=r)} END")


def _carrier_sql(r):
    return f"COALESCE(json_extract(CASE WHEN json_valid({r}.refs_json) THEN {r}.refs_json END,'$.carries'),'')<>''"


def _backfill_sql(r):
    """SQL: a message written as history (`refs.import`, `refs.replay`), which is never unread."""
    refs = f"CASE WHEN json_valid({r}.refs_json) THEN {r}.refs_json END"
    return f"(json_extract({refs},'$.import') IS NOT NULL OR json_extract({refs},'$.replay') IS NOT NULL)"


def _triggers():
    install = ("COALESCE((SELECT json_extract(value_json,'$.install_id') FROM registry_metadata "
               "WHERE key='usage-count'),'local')")
    # No writer vouched for this row's origin: none given, or one copied from another row.
    fresh = ("(NEW.origin_id IS NULL OR EXISTS (SELECT 1 FROM messages o WHERE o.origin_id=NEW.origin_id "
             "AND o.id<>NEW.id))")
    state = ("CASE WHEN NEW.delivered_at IS NOT NULL AND NEW.read_at IS NOT NULL THEN 'read' "
             "WHEN NEW.delivered_at IS NOT NULL THEN 'delivered' ELSE 'pending' END")
    return {
        # A conversation written by any other writer is a root. Its audience stays unset and is derived live from
        # its legacy columns (audience_of, _derived_audience), since those writers set kind, task_id, scope and
        # room_key after the insert.
        "threads_conversations_stamp": (
            "AFTER INSERT ON conversations WHEN NEW.root_id IS NULL",
            "UPDATE conversations SET root_id=NEW.id, path='/'||NEW.id||'/', "
            "updated_at=COALESCE(NEW.updated_at,NEW.created) WHERE id=NEW.id;"),
        # Every message but a carrier takes the next seq (a seq copied with a row is replaced), and its thread's
        # counters follow. A message no writer stamped is marked restricted: nothing vouched that its refs are
        # harmless.
        "threads_messages_stamp": (
            f"AFTER INSERT ON messages WHEN NOT {_carrier_sql('NEW')}",
            "INSERT OR IGNORE INTO sequences(name,value) VALUES('messages',0);"
            "UPDATE sequences SET value=value+1 WHERE name='messages';"
            "UPDATE messages SET seq=(SELECT value FROM sequences WHERE name='messages'), "
            f"origin_id=CASE WHEN {fresh} THEN lower(hex(randomblob(16)))||'@'||{install} ELSE NEW.origin_id END, "
            f"restricted=CASE WHEN {fresh} THEN 1 ELSE NEW.restricted END WHERE id=NEW.id;"
            "UPDATE conversations SET last_seq=(SELECT value FROM sequences WHERE name='messages'), "
            f"last_unread_seq=CASE WHEN (NEW.from_actor LIKE 'human:%' OR COALESCE(NEW.to_actor,'')<>'') "
            f"AND NOT {_backfill_sql('NEW')} "
            "THEN (SELECT value FROM sequences WHERE name='messages') ELSE last_unread_seq END "
            "WHERE id=NEW.conversation_id;"),
        # The primary recipient's delivery: one row per message addressed to someone, in every writer's transaction.
        "threads_primary_delivery": (
            f"AFTER INSERT ON messages WHEN COALESCE(NEW.to_actor,'')<>'' AND NOT {_carrier_sql('NEW')}",
            "INSERT OR IGNORE INTO message_deliveries(id,message_id,recipient,thread_id,root_id,mode,reason,cause,"
            "state,revision,delivered_at,read_at,created_at,updated_at,unsolicited) VALUES(lower(hex(randomblob(16))),NEW.id,"
            "NEW.to_actor,NEW.conversation_id,(SELECT COALESCE(root_id,id) FROM conversations "
            "WHERE id=NEW.conversation_id),"
            "CASE WHEN NEW.to_actor LIKE 'slack:%' OR NEW.to_actor LIKE 'peer:%' THEN 'none' "
            "WHEN COALESCE(json_extract(CASE WHEN json_valid(NEW.refs_json) THEN NEW.refs_json END,'$.quiet'),0) "
            "THEN 'defer' ELSE 'queue' END,"
            "CASE WHEN NEW.to_actor LIKE 'slack:%' OR NEW.to_actor LIKE 'peer:%' THEN 'outside' "
            "WHEN NEW.kind='ask' THEN 'ask' ELSE 'addressed' END,"
            "CASE WHEN NEW.from_actor LIKE 'bot:%' THEN 'bot' WHEN NEW.from_actor LIKE 'human:%' THEN 'human' "
            "WHEN NEW.from_actor LIKE 'slack:%' OR NEW.from_actor LIKE 'peer:%' THEN 'outside' ELSE 'keeper' END,"
            f"{state},COALESCE(NEW.revision,0),NEW.delivered_at,NEW.read_at,NEW.created,NEW.created,"
            "COALESCE(json_extract(CASE WHEN json_valid(NEW.refs_json) THEN NEW.refs_json END,'$.unsolicited'),0));"),
        # Its state follows the message row's receipts (hubdb.mark_delivered, mark_read and every direct UPDATE).
        "threads_delivery_receipts": (
            "AFTER UPDATE OF delivered_at, read_at ON messages",
            "UPDATE message_deliveries SET delivered_at=NEW.delivered_at, read_at=NEW.read_at, "
            f"state=CASE WHEN state IN ('capped','held','failed','moot') THEN state ELSE {state} END, "
            f"updated_at={_NOW_SQL} WHERE message_id=NEW.id AND recipient=NEW.to_actor;"
            # A carrier's run took the message it carries.
            "UPDATE message_deliveries SET delivered_at=NEW.delivered_at, "
            "state=CASE WHEN state='pending' AND NEW.delivered_at IS NOT NULL THEN 'delivered' ELSE state END, "
            f"updated_at={_NOW_SQL} WHERE carrier_id=NEW.id;"),
        # A deleted message keeps no words anywhere: every writer that deletes (a thread delete, a task comment taken
        # back, a hidden roadmap comment) empties its revision history too.
        "threads_revisions_deleted": (
            "AFTER UPDATE OF deleted_at ON messages WHEN NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL",
            "UPDATE message_revisions SET body='' WHERE message_id=NEW.id;"),
    }


def ensure(c):
    """Idempotent: the columns, tables, indexes and triggers. Called on every start (hubdb.migrate and the cloud
    store's boot list), so no migration number can collide with another branch's."""
    for column, declaration in CONVERSATION_COLUMNS:
        H.add_column(c, "conversations", column, declaration)
    for column, declaration in MESSAGE_COLUMNS:
        H.add_column(c, "messages", column, declaration)
    for statement in TABLES:
        c.execute(statement)
    for column, declaration in DELIVERY_COLUMNS:
        H.add_column(c, "message_deliveries", column, declaration)
    c.execute("CREATE TABLE IF NOT EXISTS registry_metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
    c.execute("INSERT OR IGNORE INTO sequences(name,value) SELECT 'messages', COALESCE(MAX(rowid),0) FROM messages")
    installed = {r[0]: r[1] for r in c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                                               "AND name LIKE 'threads\\_%' ESCAPE '\\'")}
    for name, (head, body) in _triggers().items():
        sql = f"CREATE TRIGGER {name} {head} BEGIN {body} END"
        if installed.get(name) == sql:
            continue
        c.execute(f"DROP TRIGGER IF EXISTS {name}")
        c.execute(sql)
    c.execute("DROP INDEX IF EXISTS message_deliveries_inbox")     # replaced by message_deliveries_waiting
    for name in ("messages_seq", "messages_origin"):    # unique in an early build of this branch
        found = c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        if found and "UNIQUE" in str(found[0]).upper():
            c.execute(f"DROP INDEX {name}")
    done = migrated(c)
    for statement in INDEXES:
        if not done and statement in _MESSAGE_INDEXES:
            continue            # built by migrate, after the backfill (building them first is slow, and it stalls)
        c.execute(statement)
    _scrub_audits(c)
    _neutral_titles_once(c)


AUDIT_SCRUB_KEY = "threads-audit-scrub"


def _scrub_audits(c):
    """Once: audit rows an early build of this branch wrote with a message's words or a thread's title keep only
    their lengths (`_audited`)."""
    if c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (AUDIT_SCRUB_KEY,)).fetchone():
        return
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'").fetchone():
        for column in ("before_json", "after_json"):
            c.execute(f"UPDATE audit_log SET {column}=json_set(json_remove({column},'$.body'),'$.length',"
                      f"length(COALESCE(json_extract({column},'$.body'),''))) WHERE object LIKE 'message:%' AND "
                      f"json_valid({column}) AND json_type({column},'$.body') IS NOT NULL")
            c.execute(f"UPDATE audit_log SET {column}=json_set(json_remove({column},'$.title'),'$.title_length',"
                      f"length(COALESCE(json_extract({column},'$.title'),''))) WHERE object LIKE 'thread:%' AND "
                      f"json_valid({column}) AND json_type({column},'$.title') IS NOT NULL")
    c.execute("INSERT INTO registry_metadata(key,value_json) VALUES(?,?)", (AUDIT_SCRUB_KEY, json.dumps({"at": _now()})))


# ----------------------------------------------------------------------------- small helpers
def _new_id():
    """A row id for the tables threads keeps. Not hubdb.new_id: the demo seeds that one as a counter
    (backend/demo_seed.py), and rows written beside each message must not move the ids of the demo's own rows."""
    return str(uuid.uuid4())


def uuid7():
    """A UUID version 7: 48 bits of milliseconds, then random bits, so ids sort by time."""
    ms = int(time.time() * 1000)
    raw = bytearray(ms.to_bytes(6, "big") + secrets.token_bytes(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    h = raw.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def install_id(c):
    try:
        row = c.execute("SELECT value_json FROM registry_metadata WHERE key='usage-count'").fetchone()
        value = json.loads(row[0]) if row else {}
        found = str((value or {}).get("install_id") or "")
        if found:
            return found
        from . import census
        return census.install_id_in(c)
    except Exception:  # noqa: BLE001 - a bare hub database (tests, tools) has no census record
        return "local"


def mint_origin(c):
    return f"{uuid7()}@{install_id(c)}"


def _row(c, value, table="conversations"):
    if value is None:
        return None
    if isinstance(value, str):
        row = c.execute(f"SELECT * FROM {table} WHERE id=?", (value,)).fetchone()
        return dict(row) if row else None
    return dict(value)


def thread_row(c, thread_or_id):
    row = _row(c, thread_or_id)
    if row is not None and "participants" not in row:
        row["participants"] = H._json(row.get("participants_json"), []) or []
    return row


def audience_of(row):
    """The stored audience, or the one its legacy columns imply (a row read before its trigger ran, a test row)."""
    found = (row or {}).get("audience")
    if found in AUDIENCES:
        return found
    room = str(row.get("room_key") or "")
    if room.startswith(_MEETING_PREFIX):
        return "meeting"
    if row.get("scope") == "personal":
        return "personal"
    if row.get("scope") == "shared":
        return "home"
    if row.get("kind") == "task" and row.get("task_id"):
        return "task"
    return "participants"


def root_of(c, row):
    row = thread_row(c, row)
    if row is None:
        return None
    rid = row.get("root_id") or row["id"]
    return row if rid == row["id"] else (thread_row(c, rid) or row)


def home_owner(row):
    """The actor whose Home this is (a shared room's bot for a row its trigger has not stamped)."""
    owner = row.get("owner_actor")
    if owner:
        return owner
    room = str(row.get("room_key") or "")
    return "bot:" + room.split(":", 1)[0] if row.get("scope") == "shared" and room else None


def personal_owner(row):
    return row.get("owner_actor") or next((p for p in row.get("participants") or
                                           H._json(row.get("participants_json"), []) or []
                                           if str(p).startswith("human:")), None)


def _event(c, actor, action, target, detail=None):
    H.event(c, actor, action, target, detail or {})


def _audit(c, actor, obj, before, after, via=None):
    try:
        from . import audit_log
        audit_log.write(c, actor, obj, before, after, via)
    except Exception:  # noqa: BLE001, S110 - a bare hub database has no audit_log table; the event still records it
        pass


# ----------------------------------------------------------------------------- the viewer
class _OwnerOnly:
    """What C1 needs of an Auth when the caller has none: the owner's email, read from the database."""

    def __init__(self, c):
        from . import access
        try:
            self.owner_email = str((access._load_json(c, access.OWNER) or {}).get("email") or "").lower()
        except Exception:  # noqa: BLE001 - a bare hub database has no owner record
            self.owner_email = ""

    def identity_for_actor(self, c, actor):
        from . import teammate_access
        found = teammate_access._viewer_for(c, actor)
        if found is None:
            raise Problem("not_found", "Unknown person", 404)
        return found

    def bot_accesses(self, c, who, slugs):
        from . import teammate_access
        return teammate_access.bot_levels(c, who, slugs)


class Viewer:
    """One reader for one request: the identity, its Auth and what deciding many threads for it reads once. Never
    kept across requests (a roster or access change must count at once)."""

    def __init__(self, auth, who):
        self.auth, self.who = auth, who
        self.actor = str(getattr(who, "actor", "") or "")
        self.role = str(getattr(who, "role", "") or "")
        self._chain = self._hidden = self._shared = self._admin = self._tasks = self._standing = self._known = None
        self._backfill = None
        self._threads = {}
        self._legacy = {}

    @property
    def is_bot(self):
        return self.role == "bot"

    @property
    def is_person(self):
        return self.role in ("human", "owner")

    @property
    def runner_turn(self):
        """A bot that is not an external agent reads by its attempt's grant (none without an attempt)."""
        return self.is_bot and not getattr(self.who, "agent", "")

    def in_good_standing(self, c):
        """A person who left, or whose sign-in is off (the owner's never is), reads nothing (auth.standing)."""
        if self._standing is None:
            from .auth import standing
            barred = standing(c, H.actor_id(self.actor)) if self.actor.startswith("human:") else ""
            self._standing = not (barred == "left" or (barred == "off" and self.role != "owner"))
        return self._standing

    def auth_or_db(self, c):
        return self.auth if self.auth is not None else _OwnerOnly(c)

    def chain(self, c):
        """The groups the viewer is in: its own and every group above it."""
        if self._chain is None:
            from . import views
            roster = views.roster(c, shared=True)
            if self.is_bot:
                own = P.team_of(H.actor_id(self.actor), views.entries(c, shared=True), roster) or ""
            elif self.is_person:
                own = (P.person(H.actor_id(self.actor), roster) or {}).get("team") or ""
            else:
                own = ""
            self._chain = list(P.group_chain(own, roster) or ([own] if own else []))
        return self._chain

    def member_names(self, c):
        """The `thread_members.actor` values that name this viewer: itself and its groups."""
        return [self.actor] + ["group:" + g for g in self.chain(c)]

    def known(self, c):
        """Which people a thread answer may name to this viewer (teammate_access.Known), asked once a request."""
        if self._known is None:
            from . import teammate_access
            self._known = teammate_access.Known(c, self.who, self.auth)
        return self._known

    def hidden_read(self, c):
        """Every teammate whose Home this viewer may not read."""
        if self._hidden is None:
            from . import teammate_access
            if not (self.is_person or self.is_bot):
                self._hidden = None
                return set(teammate_access.all_actors(c)) | {"group:" + g for g in _group_ids(c)}
            self._hidden = teammate_access.hidden(c, self.who, "read", auth=self.auth)
        return self._hidden

    def can_read_home(self, c, owner):
        if not owner or not (self.is_person or self.is_bot):
            return False
        if self._hidden is not None:
            return owner not in self._hidden
        from . import teammate_access
        try:
            return teammate_access.can(c, self.who, "read", owner, auth=self.auth)
        except (ValueError, KeyError, Problem):
            return False

    def shared_slugs(self, c, slugs):
        """Of these bots, those whose legacy shared-room history this viewer reads (rooms.shared_member_bots)."""
        if not self.actor.startswith("human:"):
            return set()
        if self._shared is None:
            self._shared = {}
        wanted = [s for s in dict.fromkeys(slugs) if s not in self._shared]
        if wanted:
            from . import rooms
            found = rooms.shared_member_bots(c, self.auth_or_db(c), self.actor, wanted)
            readable = set()
            if found:
                access = (self.auth.bot_accesses(c, self.who, sorted(found)) if self.auth is not None
                          else _OwnerOnly(c).bot_accesses(c, self.who, sorted(found)))
                readable = {s for s in found if access.get(s, {}).get("read")}
            self._shared.update({s: s in readable for s in wanted})
        return {s for s in slugs if self._shared.get(s)}

    def admin(self, c):
        if self._admin is None:
            from . import teammate_access
            self._admin = teammate_access.is_admin(c, self.who, self.auth)
        return self._admin


def _group_ids(c):
    from . import views
    return list((views.roster(c, shared=True).get("org_groups") or {}).keys())


def viewer(auth, who):
    return who if isinstance(who, Viewer) else Viewer(auth, who)


def _as_viewer(principal):
    if isinstance(principal, Viewer):
        return principal
    if hasattr(principal, "actor") and hasattr(principal, "role"):
        return Viewer(None, principal)
    return None


def actor_viewer(c, actor):
    """A viewer for an actor named by a message (a mention, a member), without elevating it."""
    from . import teammate_access
    who = teammate_access._viewer_for(c, actor) if str(actor).startswith(("bot:", "human:")) else None
    return Viewer(None, who) if who is not None else None


# ----------------------------------------------------------------------------- C1 in Python
def _task_readable(c, v, task_id):
    if not task_id:
        return True
    if v.auth is not None:
        try:
            v.auth.task(c, v.who, task_id)
            return True
        except Problem:
            return False
    row = H.task(c, task_id)
    return bool(row) and H.task_private_readable(c, v.actor, row)


def _is_member(c, v, thread_id):
    names = v.member_names(c)
    return c.execute("SELECT 1 FROM thread_members WHERE thread_id=? AND actor IN (%s) LIMIT 1"
                     % ",".join("?" * len(names)), (thread_id, *names)).fetchone() is not None


def _bot_grant(c, v, row):
    root = row.get("root_id") or row["id"]
    return c.execute("SELECT 1 FROM attempt_conversations ac JOIN conversations g ON g.id=ac.conversation_id "
                     "WHERE ac.attempt_id=? AND (g.id=? OR COALESCE(g.root_id,g.id)=?) LIMIT 1",
                     (getattr(v.who, "attempt_id", "") or "", row["id"], root)).fetchone() is not None


_MISSING = (404, "not_found", "Thread not found")


def denial(c, v, row):
    """None when the viewer reads this thread, else (status, code, message) for why not: always a 404, since an
    unreadable thread is never confirmed to exist. The one Python rendering of C1; `readable`, `require` and
    Auth.conversation all ask it."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = thread_row(c, row)
    if row is None:
        return _MISSING
    if v.is_person and not v.in_good_standing(c):
        return _MISSING
    from .live_privacy import sources
    from .task_privacy import actor as principals
    index = sources(c)
    if not index.conversation_readable(principals(v.who), row["id"]):
        return _MISSING
    root = root_of(c, row)
    if root.get("audience") not in AUDIENCES:
        return _legacy_denial(c, v, row, root, index)
    audience = audience_of(root)
    task_id = row.get("task_id") or root.get("task_id")
    if task_id and not _task_readable(c, v, task_id):
        return _MISSING
    if audience == "task":
        return None
    if v.is_bot:
        return _bot_denial(c, v, row, root, audience)
    if not v.is_person:
        return _MISSING
    # A meeting's chat is read by whoever reads the meeting (live_privacy, checked above).
    if audience == "meeting":
        return None
    if audience == "company":
        return None
    if audience == "team":
        return None if v.role == "owner" or root.get("audience_group_id") in v.chain(c) else _MISSING
    if audience == "members":
        return None if _is_member(c, v, root["id"]) else _MISSING
    if audience == "home":
        if v.can_read_home(c, home_owner(root)):
            return None
        # A migrated Home also opens (its root only) to its shared room's old readers, for that history alone.
        found = _legacy_home(c, v, root["id"]) if row["id"] == root["id"] else None
        return None if found and v.shared_slugs(c, sorted(found["slugs"])) else _MISSING
    if audience == "personal":
        # A DM reads by its membership, so whoever leaves it stops reading, its starter too. A special room (room_key: a
        # bot's personal chat, Strategy) has no member rows and stays its owner's.
        owns = bool(root.get("room_key")) and v.actor == personal_owner(root)
        if not owns and not _is_member(c, v, root["id"]):
            return _MISSING
        # The owner's Strategy chat belongs to the owner seat (backend/assistant.py move_strategy_room).
        if root.get("room_key") == STRATEGY_ROOM and v.role != "owner":
            return _MISSING
        return None
    # participants
    return None if v.role == "owner" or v.actor in (root.get("participants") or []) else _MISSING


def _legacy_denial(c, v, row, root, index):
    """C1 for a thread whose root predates threads (or was written by a legacy writer): its audience is not stored, so
    the rules it was read by before threads still decide, exactly (spec section 4: every existing conversation keeps
    its reader set). Shared rooms read their members live (rooms.shared_member_ids). A sub-thread of such a root
    reads as its root, except that a run's grant on any thread of the tree counts."""
    from . import rooms
    task_id = row.get("task_id") or root.get("task_id")
    if task_id:
        if not _task_readable(c, v, task_id):
            return _MISSING
        if root.get("kind") == "task":
            return None
    if v.is_bot:
        from .auth import _own_ask_to_a_person
        if v.runner_turn:
            grant = _bot_grant(c, v, row)
        else:
            grant = v.actor in (root.get("participants") or []) or rooms.room_bot(root) == H.actor_id(v.actor)
        return None if grant or _own_ask_to_a_person(v.who, row) else _MISSING
    if not v.is_person:
        return _MISSING
    if index.bound_conversation(root["id"]):
        return None
    scope = root.get("scope") or "direct"
    if scope == rooms.PERSONAL:
        if v.actor != personal_owner(root):
            return _MISSING
        if root.get("room_key") == STRATEGY_ROOM and v.role != "owner":
            return _MISSING
        return None
    if scope == rooms.SHARED:
        bot = rooms.room_bot(root)
        return None if bot and v.shared_slugs(c, [bot]) else _MISSING
    return None if v.role == "owner" or v.actor in (root.get("participants") or []) else _MISSING


def _bot_denial(c, v, row, root, audience):
    """C1 for a bot. Every bot reads by membership, participation, its Home or one it reads, or a task it may read.
    An internal bot (an AI employee, in a turn) also reads what a person in its seat reads: every company thread and
    its own group's team threads (nested groups included), and the thread tree its turn was woken for. An external
    agent (agent token) never gets company or team standing read: there only its participation counts."""
    from .auth import _own_ask_to_a_person
    if v.runner_turn and _bot_grant(c, v, row):
        return None
    if _bot_standing(c, v, root, audience) or _own_ask_to_a_person(v.who, row):
        return None
    return _MISSING


def _bot_standing(c, v, root, audience):
    if audience == "company":
        return v.runner_turn or v.actor in (root.get("participants") or [])
    if audience == "team":
        if v.runner_turn and root.get("audience_group_id") in v.chain(c):
            return True
        return v.actor in (root.get("participants") or [])
    return audience_has(c, root, v.actor, viewer_=v)


def audience_has(c, thread, actor, *, viewer_=None):
    """Whether `actor` is in the thread's audience: who a mention reaches, who a wake may start, who an agent reads.
    For a person it is C1; for a bot it is whether it may be granted the thread (its Home, a membership, a public
    thread it may see, a task it may read)."""
    root = root_of(c, thread)
    if root is None:
        return False
    if remote_guest(c, actor):
        return remote_member(c, root, actor)
    v = viewer_ or actor_viewer(c, actor)
    if v is None:
        return False
    if not v.is_bot:
        return denial(c, v, root) is None
    audience = audience_of(root)
    if audience == "task":
        return _task_readable(c, v, root.get("task_id"))
    if actor in (root.get("participants") or []):
        return True
    if audience == "meeting":
        # A meeting's chat reaches the bots in it (live_privacy decides who reads the meeting).
        from . import rooms
        return rooms.room_bot(root) == H.actor_id(actor)
    if audience in ("members", "personal") and _is_member(c, v, root["id"]):
        return True
    if audience == "personal":
        from . import rooms
        return rooms.room_bot(root) == H.actor_id(actor)
    if audience == "home":
        owner = home_owner(root)
        return owner == actor or v.can_read_home(c, owner)
    if audience == "company":
        return True
    if audience == "team":
        from . import teammate_access
        return actor in teammate_access.group_member_actors(c, root.get("audience_group_id") or "")
    return False


def readable(c, v, thread_or_message):
    """Whether the viewer reads this thread (a row, dict or id) or message (a dict with `conversation_id`)."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    value = thread_or_message
    if isinstance(value, str):
        thread = thread_row(c, value)
        if thread is not None:
            return denial(c, v, thread) is None
        message = H.message(c, value, include_deleted=False)
        return bool(message) and message_readable(c, v, message)
    value = dict(value)
    if "conversation_id" in value and "from_actor" in value:
        return message_readable(c, v, value)
    return denial(c, v, value) is None


def thread_readable(c, v, thread_id):
    """Cached per viewer: a timeline or SSE batch asks about the same thread many times."""
    if thread_id not in v._threads:
        v._threads[thread_id] = denial(c, v, thread_id) is None
    return v._threads[thread_id]


def _legacy_homes(c):
    """{thread id: (root id, owner)} for every thread of a migrated Home that still holds shared-room history: one
    query per snapshot (read_cache), so a page of messages checks each against a dict."""
    from . import read_cache

    def build():
        return {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT x.id, r.id, r.owner_actor FROM conversations r JOIN conversations x ON x.root_id=r.id "
            "WHERE r.audience='home' AND EXISTS (SELECT 1 FROM messages lm WHERE lm.conversation_id=r.id "
            "AND lm.legacy_audience IS NOT NULL)")}
    return read_cache.value(c, ("threads_legacy_homes",), build, shared=True)


def legacy_home(c, conversation_id):
    """For a thread of a migrated Home that still holds shared-room history: {"root", "owner", "slugs"} (its root, the
    Home's owner and the bots whose old readers read that history), else None."""
    found = _legacy_homes(c).get(conversation_id)
    if found is None:
        return None
    from . import read_cache
    slugs = read_cache.value(c, ("threads_legacy_slugs", found[0]), lambda: {str(r[0])[len("shared:"):] for r in c.execute(
        "SELECT DISTINCT legacy_audience FROM messages WHERE conversation_id=? AND legacy_audience LIKE 'shared:%'",
        (found[0],))})
    return {"root": found[0], "owner": found[1], "slugs": set(slugs)}


def _legacy_home(c, v, conversation_id):
    if conversation_id not in v._legacy:
        v._legacy[conversation_id] = legacy_home(c, conversation_id)
    return v._legacy[conversation_id]


def legacy_reader_only(c, v, thread):
    """A person who opens a migrated Home only as one of its shared room's old readers (no Home Read): they see the
    history they read before and nothing new (spec section 4)."""
    if not v.is_person:
        return False
    found = _legacy_home(c, v, thread["id"])
    return bool(found) and not v.can_read_home(c, found["owner"]) and bool(v.shared_slugs(c, sorted(found["slugs"])))


def legacy_hidden(c, v, message):
    """A message of a migrated Home this viewer may not read: shared-room history is read by exactly its old readers
    (evaluated live: room members for people; participants and the room's bot for an agent, any thread a run is
    granted), and everything new by the Home's Read audience."""
    message = message or {}
    legacy = message.get("legacy_audience")
    if legacy and str(legacy).startswith("shared:"):
        slug = str(legacy)[len("shared:"):]
        if v.is_bot:
            thread = thread_row(c, message.get("conversation_id"))
            if thread is None:
                return True
            if v.runner_turn and _bot_grant(c, v, thread):
                return False
            root = root_of(c, thread) or {}
            return not (v.actor in (root.get("participants") or []) or v.actor == "bot:" + slug)
        return slug not in v.shared_slugs(c, [slug])
    if not v.is_person or not message.get("conversation_id"):
        return False
    found = _legacy_home(c, v, message["conversation_id"])
    return bool(found) and not v.can_read_home(c, found["owner"])


def legacy_gate(c, principals, message):
    """The migrated-Home rule for the per-message check (task_privacy.message_readable), which the legacy readers
    (/conversations, inboxes, claims) pass: each person principal is checked as `legacy_hidden` does. A bot principal
    there acts in a run, which reads by its grant."""
    message = dict(message or {})
    cid = message.get("conversation_id")
    names = principals if isinstance(principals, tuple) else (principals,)
    people = [a for a in names if str(a).startswith("human:")]
    if not cid or not people or cid not in _legacy_homes(c):
        return True
    if "legacy_audience" not in message:
        row = c.execute("SELECT legacy_audience FROM messages WHERE id=?", (message.get("id"),)).fetchone()
        message["legacy_audience"] = row[0] if row else None
    for actor in people:
        v = actor_viewer(c, actor)
        if v is None or legacy_hidden(c, v, message):
            return False
    return True


def readable_before_deletion(c, v, message):
    """Whether the viewer read a deleted message before it went (its thread already checked): only then is its
    tombstone shown, else the quiet hidden row. The per-message check, run on the row as it stands."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    message = dict(message)
    if legacy_hidden(c, v, message):
        return False
    if message.get("restricted") == 0 and "restricted" in message:
        return True
    from . import task_privacy
    principals = task_privacy.actor(v.who)
    try:
        return (task_privacy.meeting_sources(c).message_readable(principals, message)
                and task_privacy.readable(c, principals, task_privacy.message_tasks(c, message, current=True)))
    except Problem:
        return False


def message_readable(c, v, message, *, thread_checked=False):
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    message = dict(message)
    if message.get("deleted_at"):
        return False
    if not thread_checked and not thread_readable(c, v, message["conversation_id"]):
        return False
    if legacy_hidden(c, v, message):
        return False
    if message.get("restricted") == 0 and "restricted" in message:
        return True
    from . import task_privacy
    return task_privacy.message_readable(c, task_privacy.actor(v.who), message)


def require(c, v, thread_id, *, write=False):
    """The thread row, or 404: an unreadable thread is never confirmed to exist."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = thread_row(c, thread_id)
    if row is None or denial(c, v, row) is not None:
        raise Problem("not_found", "Thread not found", 404)
    if write and not can_post(c, v, row):
        raise Problem("forbidden", "You can read this thread but not post in it", 403)
    return row


def can_post(c, v, thread):
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = thread_row(c, thread)
    if row is None or denial(c, v, row) is not None:
        return False
    root = root_of(c, row)
    if row.get("archived_at") or root.get("archived_at") or row.get("closed_at"):
        return False
    if audience_of(root) == "home":
        from . import teammate_access
        owner = home_owner(root)
        if v.is_bot and (owner == v.actor or (v.runner_turn and _bot_grant(c, v, row))):
            return True                  # its own Home, or a reply back into the thread its run was granted
        try:
            return bool(owner) and teammate_access.can(c, v.who, "write", owner, auth=v.auth)
        except (ValueError, KeyError, Problem):
            return False
    if audience_of(root) == "meeting":
        return False
    return True


def can_manage(c, v, thread):
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = thread_row(c, thread)
    if row is None or denial(c, v, row) is not None:
        return False
    root = root_of(c, row)
    if v.is_person and v.admin(c):
        return True
    if root.get("owner_actor") == v.actor and audience_of(root) != "home":
        return True
    if audience_of(root) == "home":
        from . import teammate_access
        owner = home_owner(root)
        return bool(owner) and teammate_access.can(c, v.who, "manage", owner, auth=v.auth)
    return c.execute("SELECT 1 FROM thread_members WHERE thread_id=? AND actor=? AND role='owner'",
                     (root["id"], v.actor)).fetchone() is not None


# ----------------------------------------------------------------------------- C1 as SQL and for SSE
def _grant_sql(v, id_sql, root_sql, t):
    """SQL: the thread is in a tree the bot's turn was granted (_bot_grant)."""
    return (f"COALESCE({root_sql},{id_sql}) IN (SELECT COALESCE(g.root_id,g.id) FROM {t('attempt_conversations')} "
            f"ac JOIN {t('conversations')} g ON g.id=ac.conversation_id WHERE ac.attempt_id="
            f"{_q(getattr(v.who, 'attempt_id', '') or '')})")


def readable_sql(c, v, alias="conversations", *, inner=None, tasks="SELECT id FROM tasks", meeting=None,
                 conversation=None, only=None):
    """A SQL boolean over a conversations row named `alias` (bare columns when alias is ''): the thread is readable.

    `inner(table)` names an unfiltered table (hub sql's temp views); `tasks` is a SELECT of the task ids the viewer
    reads (hub sql passes its filtered view); `meeting(id_sql)` renders the meeting-room check (hub sql's function),
    and without it meeting threads are left to Python (`readable_ids`). `conversation(id_sql)`, when given, is the
    meeting-provenance check every thread also passes. `only` (readable_ids) is every stored audience the roots of
    the rows asked about have (None for a root that predates threads), plus "home" when a row is a Home itself: the
    Home and legacy terms no such row can take are rendered false without reading what they need (every teammate's
    access, every bot's shared room), which only ever narrows."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    t = inner or (lambda name: name)
    x = (alias + ".") if alias else ""
    me = _q(v.actor)
    fence = f"({x}task_id IS NULL OR {x}task_id IN ({tasks}))"
    if v.is_bot:
        internal = v.runner_turn
        grant = _grant_sql(v, x + "id", x + "root_id", t) if internal else "0"
        terms = f"({grant} OR ({x}kind='task' AND {x}task_id IS NOT NULL))"
        names = _qlist(v.member_names(c))
        slug = _q(H.actor_id(v.actor))
        hidden = v.hidden_read(c)
        home_bot = (f"(r.owner_actor IS NOT NULL AND r.owner_actor NOT IN {_qlist(sorted(hidden))})"
                    if hidden is not None else "0")
        participant = f"EXISTS (SELECT 1 FROM {_PARTICIPANTS.format(r='r')} WHERE value={me})"
        if internal:
            # An internal bot reads a legacy root (no stored audience) only by its turn's grant (_legacy_denial);
            # beyond that it reads company threads and its own group's team threads, as a person in its seat.
            standing = (f"r.audience IS NOT NULL AND ({participant} OR r.audience='company' OR "
                        f"(r.audience='team' AND r.audience_group_id IN {_qlist(v.chain(c))}))")
        else:
            # A legacy root reads as _legacy_denial: a participant, or the room's bot.
            standing = f"{participant} OR (r.audience IS NULL AND {_LEGACY_ROOM_BOT.format(r='r')}={slug})"
        terms = (f"({terms} OR COALESCE({x}root_id,{x}id) IN (SELECT r.id FROM {t('conversations')} r WHERE "
                 f"{standing} OR (r.audience='home' AND (r.owner_actor={me} OR {home_bot})) "
                 f"OR (r.audience IN ('members','personal') AND EXISTS (SELECT 1 FROM {t('thread_members')} tm "
                 f"WHERE tm.thread_id=r.id AND tm.actor IN {names}))))")
        out = f"({terms}) AND {fence}"
        return f"({out}) AND {conversation(x + 'id')}" if conversation else out
    names = _qlist(v.member_names(c))
    owner = v.role == "owner"
    person = v.is_person
    homes = only is None or "home" in only
    legacies = only is None or None in only or "home" in only
    chain = _qlist(v.chain(c)) if person else "()"
    hidden = v.hidden_read(c) if (person or v.is_bot) and homes else None
    owner_sql = f"COALESCE(r.owner_actor,{_derived_owner('r')})"
    home = (f"{owner_sql} IS NOT NULL AND {owner_sql} NOT IN {_qlist(sorted(hidden))}" if hidden is not None
            else "0")
    member = f"EXISTS (SELECT 1 FROM {t('thread_members')} tm WHERE tm.thread_id=r.id AND tm.actor IN {names})"
    meeting_term = meeting("r.id") if meeting else "0"
    participant = "1" if owner else f"EXISTS (SELECT 1 FROM {_PARTICIPANTS.format(r='r')} WHERE value={me})"
    # A legacy root (no stored audience) reads as _legacy_denial: by scope, with shared rooms' members read live.
    shared = sorted(v.shared_slugs(c, [b["slug"] for b in H.bots(c)])) if person and legacies else []
    legacy = (f"(CASE COALESCE(r.scope,'direct') WHEN 'personal' THEN COALESCE(r.owner_actor,{_FIRST_HUMAN.format(r='r')})={me} "
              f"AND (COALESCE(r.room_key,'')<>'{STRATEGY_ROOM}' OR {1 if owner else 0}) "
              f"WHEN 'shared' THEN {_LEGACY_ROOM_BOT.format(r='r')} IN {_qlist(shared)} ELSE {participant} END "
              f"OR (r.kind='task' AND r.task_id IS NOT NULL))")
    root = (f"CASE WHEN r.audience IS NULL THEN {legacy} ELSE CASE r.audience "
            f"WHEN 'company' THEN {1 if person else 0} "
            f"WHEN 'team' THEN {'1' if owner else ('r.audience_group_id IN ' + chain)} "
            f"WHEN 'members' THEN {member} "
            f"WHEN 'task' THEN r.task_id IS NOT NULL "
            f"WHEN 'home' THEN {home} "
            f"WHEN 'personal' THEN ((COALESCE(r.room_key,'')<>'' AND COALESCE(r.owner_actor,{_FIRST_HUMAN.format(r='r')})={me}) "
            f"OR {member}) "
            f"AND (COALESCE(r.room_key,'')<>'{STRATEGY_ROOM}' OR {1 if owner else 0}) "
            f"WHEN 'participants' THEN {participant} "
            f"WHEN 'meeting' THEN {meeting_term} ELSE 0 END END")
    if not person or not v.in_good_standing(c):
        return "0"
    bound = f" OR {meeting(x + 'id')}" if meeting else ""
    # A migrated Home's root also opens to its shared room's old readers (legacy_reader_only).
    if shared:
        bound += (f" OR {x}id IN (SELECT lm.conversation_id FROM {t('messages')} lm JOIN {t('conversations')} lr ON "
                  f"lr.id=lm.conversation_id WHERE lr.audience='home' AND lm.legacy_audience IN "
                  f"{_qlist(['shared:' + s for s in shared])})")
    out = (f"(COALESCE({x}root_id,{x}id) IN (SELECT r.id FROM {t('conversations')} r WHERE {root}){bound}) "
           f"AND {fence}")
    return f"({out}) AND {conversation(x + 'id')}" if conversation else out


def message_sql(c, v, alias="messages", *, legacy_slugs=None, inner=None):
    """A SQL boolean over a messages row: its legacy audience admits the viewer. (The thread term and the
    restricted check stay the caller's.) `legacy_slugs` is the viewer's readable shared bots when known."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    t = inner or (lambda name: name)
    x = (alias + ".") if alias else ""
    if v.is_bot:
        # Shared-room history: the room's bot, a participant, or (an internal bot) any thread its turn is granted.
        granted = ""
        if v.runner_turn:
            granted = (f" OR {x}conversation_id IN (SELECT gx.id FROM {t('conversations')} gx WHERE "
                       f"{_grant_sql(v, 'gx.id', 'gx.root_id', t)})")
        return (f"({x}legacy_audience IS NULL OR substr({x}legacy_audience,8)={_q(H.actor_id(v.actor))} OR EXISTS "
                f"(SELECT 1 FROM {t('conversations')} lr, {_PARTICIPANTS.format(r='lr')} WHERE "
                f"lr.id={x}conversation_id AND value={_q(v.actor)}){granted})")
    slugs = [r[0][len("shared:"):] for r in c.execute(
        "SELECT DISTINCT legacy_audience FROM messages WHERE legacy_audience LIKE 'shared:%'")]
    if legacy_slugs is None:
        legacy_slugs = v.shared_slugs(c, slugs)
    # Everything new in a migrated Home is read by its Read audience, whoever read the old room.
    blocked = sorted("bot:" + s for s in slugs if not v.can_read_home(c, "bot:" + s))
    new = f"{x}legacy_audience IS NULL"
    if blocked:
        new += (f" AND {x}conversation_id NOT IN (SELECT lx.id FROM conversations lx JOIN conversations lr ON "
                f"lr.id=COALESCE(lx.root_id,lx.id) WHERE lr.audience='home' AND lr.owner_actor IN {_qlist(blocked)})")
    return f"(({new}) OR substr({x}legacy_audience,8) IN {_qlist(sorted(legacy_slugs))})"


def readable_ids(c, v, ids):
    """The SSE rendering: of these thread ids, those the viewer reads. One query over the ids, then the per-thread
    meeting and task checks Python owns, for the few that need them."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return set()
    out = set()
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        marks = ",".join("?" * len(part))
        rows = c.execute(f"SELECT x.id, COALESCE(r.audience,{_derived_audience('r')}) AS audience, x.task_id, "
                         f"r.audience AS stored, x.audience AS own "
                         f"FROM conversations x JOIN conversations r ON r.id=COALESCE(x.root_id,x.id) "
                         f"WHERE x.id IN ({marks})", part).fetchall()
        # The terms only these rows can take (an id missing here is decided by nothing below).
        only = ({r["stored"] for r in rows} | {"home" for r in rows if r["own"] == "home"}
                if c.in_transaction else None)
        candidates = {r["id"] for r in c.execute(
            f"SELECT x.id FROM conversations x WHERE x.id IN ({marks}) AND "
            + readable_sql(c, v, "x", tasks="SELECT id FROM tasks", only=only), part)}
        for row in rows:
            cid = row["id"]
            needs_python = row["audience"] in ("meeting", "task") or row["task_id"] or v.is_bot
            if cid in candidates and not needs_python:
                from .live_privacy import sources
                from .task_privacy import actor as principals
                if sources(c).conversation_readable(principals(v.who), cid):
                    out.add(cid)
                v._threads[cid] = cid in out
            elif cid in candidates or row["audience"] == "meeting":
                if thread_readable(c, v, cid):
                    out.add(cid)
            else:
                v._threads[cid] = False
    return out


# ----------------------------------------------------------------------------- writing: stamp and record
# Safe keys whose value may be one level of structure (the rest are plain values): what threads itself writes.
NESTED_REF_KEYS = ("origin", "mentions", "warnings")
_REFERENCE = re.compile(r"^task:|#/task/|/tasks/")


def _ref_strings(refs):
    """Every string under `refs`, or None when its shape is not one threads knows to be harmless: a key outside
    SAFE_REF_KEYS, or structure anywhere but one level under NESTED_REF_KEYS (warnings: a list of flat objects)."""
    if refs in (None, "", {}):
        return []
    if not isinstance(refs, dict):
        return None
    out = []

    def flat(value, depth):
        if isinstance(value, str):
            out.append(value)
            return True
        if value is None or isinstance(value, (bool, int, float)):
            return True
        if depth <= 0:
            return False
        items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else None
        return items is not None and all(flat(v, depth - 1) for v in items)

    for key, value in refs.items():
        if key not in SAFE_REF_KEYS:
            return None
        depth = 2 if key == "warnings" else 1 if key in NESTED_REF_KEYS else 0
        if not flat(value, depth):
            return None
    return out


def restricted_for(conv, refs, in_reply_to, attachments=False, c=None):
    """1 when the message needs the per-message check: a reply, files, a task or meeting thread, or refs that are
    not plainly harmless (an unknown key or shape, a string that names a task, or with `c`, any task's id), the same
    nested walk the per-message check makes (task_privacy.message_tasks)."""
    if in_reply_to or attachments:
        return 1
    if conv and (audience_of(conv) in ("task", "meeting") or conv.get("task_id")):
        return 1
    strings = _ref_strings(refs)
    if strings is None or any(_REFERENCE.search(x) for x in strings):
        return 1
    if c is not None and strings:
        from . import task_privacy
        if task_privacy.references(c, list(strings)):
            return 1
    return 0


def stamp(c, row, conv, refs):
    """Before hubdb._write_message inserts `row`: origin, revision 0 and the restricted flag. The seq and the
    thread's counters come from the insert trigger."""
    origin = (POSTING.get() or {}).get("origin") or {}
    row["origin_id"] = str(origin.get("origin_id") or "") or mint_origin(c)
    row["revision"] = int(origin.get("revision") or 0)
    row["restricted"] = restricted_for(conv, refs, row.get("in_reply_to"), (POSTING.get() or {}).get("attachments"), c)
    row["legacy_audience"] = None
    return row


def record(c, message, conv):
    """After the insert: revision history, mentions, the deliveries beyond the primary one, wakes, followers and
    the loop guard. A carrier records nothing (it is how a delivery starts a run)."""
    refs = message.get("refs") or {}
    if refs.get("carries"):
        return {}
    posting = POSTING.get()
    c.execute("INSERT OR IGNORE INTO message_revisions(id,message_id,origin_id,revision,body,deleted,actor,created_at) "
              "VALUES(?,?,?,?,?,0,?,?)", (_new_id(), message["id"], message.get("origin_id") or message["id"],
                                          int(message.get("revision") or 0), message.get("body") or "",
                                          message.get("from_actor") or "", message.get("created") or _now()))
    thread = thread_row(c, conv["id"]) if conv else None
    if thread is None:
        return {}
    author = message.get("from_actor") or ""
    found = mentions(c, thread, message.get("body") or "", author=author) if "@" in str(message.get("body") or "") else []
    warnings, in_audience = [], []
    now = _now()
    for actor, kind in found:
        inside = audience_has(c, thread, actor)
        c.execute("INSERT OR IGNORE INTO message_mentions(id,message_id,actor,kind,in_audience,created_at) "
                  "VALUES(?,?,?,?,?,?)", (_new_id(), message["id"], actor, kind, int(inside), now))
        if inside:
            in_audience.append((actor, kind))
        else:
            warnings.append({"rule": "mention_outside", "actor": actor})
    if in_audience and not message.get("to_actor") and not _is_backfill(refs):
        c.execute("UPDATE conversations SET last_unread_seq=MAX(last_unread_seq,?) WHERE id=?",
                  (message.get("seq") or 0, thread["id"]))
    _follow(c, thread["id"], author, "posted", message.get("seq"))
    # Every write fans out the same way (mentions, DM members, the Home's owner, sub-thread repliers, outside
    # destinations) under the same loop guard, legacy writers (runner replies, old routes) included. A task comment's
    # mentions are the task rules' (hubdb.mentioned_people, task bots), so a legacy write in a task thread leaves them.
    if posting is None and audience_of(root_of(c, thread)) == "task":
        in_audience = []
    woken = deliver(c, thread, message, in_audience, posting, warnings)
    if woken and not message.get("to_actor") and not _is_backfill(refs):
        # Addressed to someone (a group DM's members): the counter says so.
        c.execute("UPDATE conversations SET last_unread_seq=MAX(last_unread_seq,?) WHERE id=?",
                  (message.get("seq") or 0, thread["id"]))
    _emit_message_created(c, thread, message)
    return {"warnings": warnings, "deliveries": woken}


def _follow(c, thread_id, actor, reason, seq=None):
    """The actor's follower row. Posting or being mentioned sets following, whatever a read or an earlier unfollow left,
    and keeps the read mark and the notify level; joining or a DM only makes the row, so it never undoes an unfollow."""
    if not actor or not str(actor).startswith(("human:", "bot:")) or remote_guest(c, actor):
        return
    now = _now()
    on_conflict = ("ON CONFLICT(thread_id,actor) DO UPDATE SET following=1, reason=excluded.reason, "
                   "updated_at=excluded.updated_at WHERE following=0" if reason in ("posted", "mentioned")
                   else "ON CONFLICT(thread_id,actor) DO NOTHING")
    c.execute("INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,"
              f"created_at,updated_at) VALUES(?,?,?,1,'live',?,?,?,?) {on_conflict}",
              (_new_id(), thread_id, actor, reason, max(int(seq or 0), 0), now, now))


def mentions(c, thread, body, *, author=None):
    """[(actor, kind)] the text @mentions, in order, never the author: bots by slug or name, people by id, the part of
    their email before the @, their full name, or a first name no one else shares."""
    text = str(body or "")
    if "@" not in text:
        return []
    found = []
    for b in H.bots(c):
        slug = b.get("slug") or ""
        names = [slug]
        if b.get("name"):
            names.append(b["name"])
        if b.get("display_name"):
            names.append(b["display_name"])
        spots = [_spot(text, n) for n in dict.fromkeys(n for n in names if n)]
        spots = [s for s in spots if s is not None]
        if spots and b.get("state") != "archived":
            found.append((min(spots), "bot:" + slug))
    people = H.humans(c)
    firsts = {}
    for p in people:
        first = str(p.get("name") or "").split(" ")[0].lower()
        if first:
            firsts[first] = firsts.get(first, 0) + 1
    for p in people:
        name = str(p.get("name") or "").strip()
        names = [p["id"], str(p.get("email") or "").split("@")[0], name]
        if name and firsts.get(name.split(" ")[0].lower()) == 1:
            names.append(name.split(" ")[0])
        spots = [s for s in (_spot(text, n) for n in dict.fromkeys(x for x in names if x)) if s is not None]
        if spots:
            found.append((min(spots), "human:" + p["id"]))
    out = []
    for _, actor in sorted(found):
        if actor != author and actor not in [a for a, _ in out]:
            out.append((actor, "mention"))
    return out


def _spot(text, name):
    import re
    m = re.search(r"(?<![\w@])@" + re.escape(name) + r"(?![\w-])", text, re.IGNORECASE)
    return m.start() if m else None


def _cause(author):
    a = str(author or "")
    if a.startswith("bot:"):
        return "bot"
    if a.startswith("human:"):
        return "human"
    if a.startswith(OUTSIDE_PREFIXES):
        return "outside"
    return "keeper"


def _is_backfill(refs):
    """History written as such (a Slack import): it reaches nobody and is never unread (contract §4.3, C4)."""
    return isinstance(refs, dict) and bool(refs.get("import") or refs.get("replay"))


def never_wakes(refs, bot):
    """The message may not start this bot's run, whoever it is addressed to (contract §4.3): an import or a replay, or
    one whose origin names this same bot, which would be its own message coming back. An origin holds no time, so this
    is not limited to the loop window: a bot's own message never wakes it, however late it returns."""
    if not str(bot or "").startswith("bot:") or not isinstance(refs, dict):
        return False
    origin = refs.get("origin")
    return _is_backfill(refs) or (isinstance(origin, dict) and origin.get("actor") == bot)


def deliver(c, thread, message, in_audience, posting, warnings=None):
    """The deliveries for a message that came through `post`: mentions, DM members, the Home's owner, sub-thread
    repliers and outside destinations, each woken (or not) by the thread wake rule (rules.thread_wake). A message that
    names nobody in a thread with several bots waits for routing (backend/thread_routing.py), outside this write."""
    root = root_of(c, thread)
    audience = audience_of(root)
    author = message.get("from_actor") or ""
    primary = message.get("to_actor") or None
    refs = message.get("refs") or {}
    # An import or replay wakes no bot; nor does a legacy writer's notice (the keeper telling a bot something already
    # starts what it should, hubdb.notice).
    replay = _is_backfill(refs) or (posting is None and (message.get("kind") == "notice" or author == H.KEEPER))
    wanted = {}                                          # actor -> reason, first reason wins
    for actor, kind in in_audience:
        wanted.setdefault(actor, "ask" if message.get("kind") == "ask" and actor == primary else kind)
    if audience in ("personal", "participants") and not thread.get("parent_id"):
        for actor in _dm_members(c, root):
            wanted.setdefault(actor, "dm")
    if audience == "home":
        owner = home_owner(root)
        if owner and owner.startswith("bot:"):
            wanted.setdefault(owner, "home")
    if thread.get("parent_id"):
        for actor in _sub_thread_posters(c, thread["id"], message["id"]):
            wanted.setdefault(actor, "sub_thread")
    addressed_bot = any(a.startswith("bot:") and a != author for a in wanted)
    for actor in _followers(c, thread["id"], "digest"):
        wanted.setdefault(actor, "digest")
    wanted.pop(author, None)
    out = []
    if primary and primary in wanted:
        reason = wanted.pop(primary)
        c.execute("UPDATE message_deliveries SET reason=? WHERE message_id=? AND recipient=?",
                  (reason if reason in REASONS else "addressed", message["id"], primary))
        if any(actor == primary for actor, _ in in_audience):
            _follow(c, thread["id"], primary, "mentioned", (message.get("seq") or 1) - 1)
    if primary and primary.startswith("bot:"):
        if never_wakes(refs, primary):
            c.execute("UPDATE message_deliveries SET mode='none', updated_at=? WHERE message_id=? AND recipient=?",
                      (_now(), message["id"], primary))
        else:
            _guard_primary(c, root, message, primary)
    for actor, reason in wanted.items():
        if actor == primary:
            continue
        if actor.startswith("human:"):
            sent = _deliver_person(c, thread, root, message, actor, reason, replay, warnings)
        elif actor.startswith("bot:"):
            sent = _deliver_bot(c, thread, root, message, actor, reason, replay)
        else:
            sent = None
        if sent:
            out.append(sent)
    for actor in _outside(c, thread["id"]):
        _insert_delivery(c, message, actor, thread, root, "none", "outside")
    if _cause(author) == "human":
        resume_capped(c, root["id"])
        if (not (replay or primary or in_audience or addressed_bot) and audience not in ("task", "meeting")
                and not remote_guest(c, author)):
            from . import thread_routing
            thread_routing.enqueue(c, message, thread)
    return out


def _deliver_person(c, thread, root, message, person, reason, replay, warnings):
    """One person's delivery. Each is checked on its own: they must still be on the roster (rule 2), and a bot's say or
    notice that does not answer them counts against the unsolicited cap (contract §4.7). Past the cap the message stays
    in the thread but does not reach them, and its author is told."""
    if _is_backfill(message.get("refs")) or not _reachable(c, person):
        return None
    author = message.get("from_actor") or ""
    unsolicited = False
    if _cause(author) == "bot" and not replay and message.get("kind") in ("say", "notice") and not solicited(
            c, thread, author, person, message.get("refs"), message.get("in_reply_to"), addressed=False,
            before=message.get("seq")):
        if unsolicited_today(c, author, person) >= H.UNSOLICITED_PER_DAY:
            if warnings is not None:
                warnings.append({"rule": "unsolicited_cap", "actor": person})
            return None
        unsolicited = True
    did = _insert_delivery(c, message, person, thread, root, "queue", reason if reason in REASONS else "member")
    if unsolicited:
        c.execute("UPDATE message_deliveries SET unsolicited=1 WHERE id=?", (did,))
    _follow(c, thread["id"], person, "mentioned" if reason == "mention" else "joined", (message.get("seq") or 1) - 1)
    return {"recipient": person, "mode": "queue", "reason": reason}


def _deliver_bot(c, thread, root, message, bot, reason, replay=False):
    """One bot's delivery and wake, under the wake rule and the loop guard. None when it gets no delivery."""
    if reason == "mention":
        _follow(c, thread["id"], bot, "mentioned", (message.get("seq") or 1) - 1)
    if replay or not _reachable(c, bot):
        return None
    mode, rule = wake_mode(c, thread, message, bot, reason)
    if mode is None:
        return None
    author = message.get("from_actor") or ""
    state = "pending"
    if mode in ("queue", "steer") and _cause(author) == "bot" and _over_guard(c, root["id"]):
        state = "capped"
    did = _insert_delivery(c, message, bot, thread, root, mode, reason, state=state)
    if state == "capped":
        _capped_notice(c, root)
    elif mode in ("queue", "steer"):
        carrier = _carrier(c, thread, message, bot, reason, rule)
        if carrier is None:
            c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE id=?", (_now(), did))
            return {"recipient": bot, "mode": mode, "reason": reason, "state": "moot"}
        c.execute("UPDATE message_deliveries SET carrier_id=? WHERE id=?", (carrier, did))
    elif mode == "defer":
        _defer_note(c, message, bot)
    return {"recipient": bot, "mode": mode, "reason": reason, "state": state}


def _reachable(c, actor):
    """Rule 2 for one further delivery (hubdb._reach, without refusing the post): a person on the roster, or a bot that
    runs or is held, whose work waits for it."""
    if remote_guest(c, actor):
        return True
    resolved = H.resolve_actor(c, actor)
    if not resolved:
        return False
    if H.is_bot(resolved):
        return (H.bot(c, H.actor_id(resolved)) or {}).get("state") in ("active",) + H.HELD_STATES
    return True


def solicited(c, thread, bot, person, refs=None, in_reply_to=None, *, addressed=True, before=None):
    """Whether `bot`'s post reaches `person` solicited (contract §4.7), for one delivery: a task or approval ref, a
    reply to them, a DM they opened, or a post that answers them. `addressed`: the post is addressed to them, so a
    thread they opened that is not a Home counts too, as it always did. `before`: the post's own seq, once it is
    written. Having opened a Home never counts: it is the owner's lasting place, so opening it asks for nothing."""
    refs = refs or {}
    if refs.get("task") or refs.get("approval"):
        return True
    row = thread_row(c, thread)
    if row is None:
        return False
    if in_reply_to:
        parent = c.execute("SELECT from_actor, conversation_id FROM messages WHERE id=?", (in_reply_to,)).fetchone()
        if parent and parent["from_actor"] == person and parent["conversation_id"] == row["id"]:
            return True
    root = root_of(c, row)
    audience = audience_of(root)
    direct = audience in ("personal", "participants")
    if direct or (addressed and audience != "home"):
        stored = c.execute("SELECT owner_actor FROM conversations WHERE id=?", (root["id"],)).fetchone()
        if (stored and stored[0] == person) or H._opener(c, root["id"]) == person:
            return True
    return _answers(c, row["id"], bot, person, before, direct or home_owner(root) == bot)


def _answers(c, thread_id, bot, person, before, own_place):
    """Whether the person's latest message here spoke to the bot (addressed to it, an @mention, a reply to it, or any
    message in its own DM or Home) and the bot has not posted since. Reads back ANSWER_WINDOW messages at most."""
    below = " AND seq<?" if before else ""
    args = (thread_id, *((int(before),) if before else ()), ANSWER_WINDOW)
    for row in c.execute("SELECT id, from_actor, to_actor, in_reply_to FROM messages WHERE conversation_id=? "
                         f"AND seq IS NOT NULL AND deleted_at IS NULL{below} ORDER BY seq DESC LIMIT ?", args).fetchall():
        if row["from_actor"] == bot:
            return False
        if row["from_actor"] != person:
            continue
        if own_place or row["to_actor"] == bot:
            return True
        if c.execute("SELECT 1 FROM message_mentions WHERE message_id=? AND actor=? AND in_audience=1",
                     (row["id"], bot)).fetchone():
            return True
        replied = c.execute("SELECT from_actor FROM messages WHERE id=?", (row["in_reply_to"],)).fetchone() \
            if row["in_reply_to"] else None
        return bool(replied and replied["from_actor"] == bot)
    return False


def unsolicited_today(c, author, person):
    """The unsolicited posts by `author` that reached `person` today, counted per delivery."""
    return c.execute("SELECT COUNT(*) FROM message_deliveries d JOIN messages m ON m.id=d.message_id "
                     "WHERE d.recipient=? AND d.created_at>=? AND d.unsolicited=1 AND m.from_actor=?",
                     (person, H.now()[:10], author)).fetchone()[0]


def _dm_members(c, root, rows=None):
    members = (rows if rows is not None
               else [r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (root["id"],))])
    members = list(members)
    audience = audience_of(root)
    # A DM's member rows are all of it. A special room has none: its owner and its bot are its people.
    if audience == "personal" and root.get("room_key"):
        members += [root.get("owner_actor")] + list(root.get("participants") or [])
    if audience == "participants":
        members += list(root.get("participants") or [])
    return [a for a in dict.fromkeys(members) if str(a).startswith(("human:", "bot:"))]


def legacy_participants(c, conv):
    """Who a legacy caller (an old client or runner) may address in this conversation. A thread with a stored audience
    also counts its native members (as _dm_members reads them): a native DM lists only its starter in
    participants_json, and the other people are its thread members. A legacy row keeps its participant list."""
    people = list((conv or {}).get("participants") or [])
    root = root_of(c, conv) if conv else None
    if not root or not root.get("audience"):
        return people
    return list(dict.fromkeys(people + _dm_members(c, root)))


def _sub_thread_posters(c, thread_id, exclude):
    """The bots that posted in this sub-thread: a reply in it wakes them (rule 3)."""
    return [r[0] for r in c.execute("SELECT DISTINCT from_actor FROM messages WHERE conversation_id=? AND id<>? "
                                    "AND from_actor LIKE 'bot:%' AND deleted_at IS NULL", (thread_id, exclude))]


def _followers(c, thread_id, notify):
    return [r[0] for r in c.execute("SELECT actor FROM thread_followers WHERE thread_id=? AND following=1 AND notify=? "
                                    "AND actor LIKE 'bot:%'", (thread_id, notify))]


def _outside(c, thread_id):
    try:
        return [r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=? AND "
                                        "(actor LIKE 'slack:%' OR actor LIKE 'peer:%') AND role='member'",
                                        (thread_id,))]
    except Exception:  # noqa: BLE001
        return []


def _insert_delivery(c, message, recipient, thread, root, mode, reason, *, state="pending"):
    did = _new_id()
    now = _now()
    c.execute("INSERT OR IGNORE INTO message_deliveries(id,message_id,recipient,thread_id,root_id,mode,reason,cause,"
              "state,revision,next_attempt_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (did, message["id"], recipient, thread["id"], root["id"], mode, reason, _cause(message.get("from_actor")),
               state, int(message.get("revision") or 0),
               now if recipient.startswith(OUTSIDE_PREFIXES) else None, message.get("created") or now, now))
    row = c.execute("SELECT id FROM message_deliveries WHERE message_id=? AND recipient=?",
                    (message["id"], recipient)).fetchone()
    return row[0] if row else did


def wake_mode(c, thread, message, bot, reason):
    """The thread wake rule for one bot: (mode, rule id), or (None, None) for no delivery. Precedence (contract
    §4.3): a DM, mention, ask or assign, then the bot's Home, then a sub-thread it posted in queue a run; a digest
    follower gets a digest. The existing wake engine still judges each queued delivery and may make it wait."""
    from . import rules
    if quiet_notice(message) or never_wakes(message.get("refs"), bot):
        return None, None
    if reason in ("dm", "mention", "ask", "assign", "home", "sub_thread", "routing", "addressed"):
        mode = "queue"
    elif reason == "digest":
        return "digest", rules.THREAD_WAKE
    else:
        return None, None
    return rules.thread_wake(c, message, bot, thread, mode)


NO_WAKE_REF_KEYS = ("follow_up", "card", "earlier_messages")


def quiet_notice(message):
    """A follow-up nudge, an update card or a closed-history notice (refs.follow_up, refs.card, refs.earlier_messages) is read,
    never a reason to start a run."""
    refs = (message or {}).get("refs") or {}
    return isinstance(refs, dict) and any(refs.get(k) for k in NO_WAKE_REF_KEYS)


def carrier_current(c, carrier, bot):
    """Whether a carrier may still start `bot`'s run: the message it carries is still there, undeleted, in the same
    thread, the bot is still in that thread's audience, and the per-message check passes for it. Checked when the
    carrier is written and again when its job is claimed (execution.start_claimed)."""
    carrier = dict(carrier or {})
    refs = carrier.get("refs") or H._json(carrier.get("refs_json"), {}) or {}
    source = H.message(c, refs.get("carries")) if isinstance(refs, dict) and refs.get("carries") else None
    if not source or source.get("conversation_id") != carrier.get("conversation_id"):
        return False
    return _source_readable(c, thread_row(c, source["conversation_id"]), source, bot)


def _source_readable(c, thread, message, bot):
    if thread is None or not audience_has(c, thread, bot):
        return False
    from . import task_privacy
    return task_privacy.message_readable(c, bot, message)


def note_readable(c, note, bot):
    """Whether a waiting note may still reach `bot`'s run. A deferred thread message (`message_id`) goes only while the
    bot may read its source now: a bot removed from the thread, or one whose access to the message has gone, loses it.
    A note with no source message has nothing to lose."""
    mid = (note or {}).get("message_id")
    if not mid:
        return True
    message = H.message(c, mid)
    if not message:
        return False
    return _source_readable(c, thread_row(c, message["conversation_id"]), message, bot)


def revalidate_notes(c, bot, notes):
    """The notes out of `notes` that `bot` may still be told: each one whose source it can no longer read is cancelled
    and its copied words cleared, so no run carries them. Called where a run claims its notes (execution.claim)."""
    kept = []
    for item in notes:
        if note_readable(c, item, bot):
            kept.append(item)
            continue
        c.execute("UPDATE notes SET body='', cancelled_at=COALESCE(cancelled_at,?), cancelled_by=? WHERE id=?",
                  (_now(), H.KEEPER, item["id"]))
        H.event(c, H.KEEPER, "note.cancel", item["id"], {"reason": "source_unreadable"})
    return kept


def _carrier(c, thread, message, bot, reason, rule):
    """A keeper notice to `bot` in the same thread whose job starts its run for `message`, or None when the bot may not
    read that message. No seq: no timeline, counter or SSE thread view shows it. It carries the source's provenance
    by reference: the per-message check reads a carrier as the message it carries (task_privacy.message_readable),
    and its legacy audience is copied."""
    if not _source_readable(c, thread, message, bot):
        return None
    cid = _new_id()
    refs = {"carries": message["id"], "wake": "thread", "reason": reason, "from": message.get("from_actor")}
    if rule:
        refs["rule"] = rule
    author = message.get("from_actor") or ""
    body = f"{author} in this thread: {message.get('body') or ''}"
    legacy = c.execute("SELECT legacy_audience FROM messages WHERE id=?", (message["id"],)).fetchone()
    c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,in_reply_to,created,"
              "origin_id,revision,restricted,legacy_audience) VALUES(?,?,?,?,'notice',?,?,NULL,?,?,0,1,?)",
              (cid, thread["id"], H.KEEPER, bot, body, H._dump(refs), _now(), mint_origin(c),
               legacy[0] if legacy else None))
    return cid


def _defer_note(c, message, bot):
    """A deferred delivery rides to the bot's next run as a quiet note, as bot_to_bot.hold leaves one."""
    try:
        c.execute("INSERT INTO notes(id,from_actor,to_actor,body,created,message_id) VALUES(?,?,?,?,?,?)",
                  (_new_id(), message.get("from_actor") or H.KEEPER, bot,
                   str(message.get("body") or "")[:2000], _now(), message["id"]))
    except Exception:  # noqa: BLE001, S110 - a bare hub database has no notes table
        pass


# ----------------------------------------------------------------------------- the loop guard
def _guard_window():
    return H.shift(_now(), hours=-1)


def _guard_used(c, root_id):
    return c.execute("SELECT COUNT(*) FROM message_deliveries WHERE root_id=? AND created_at>=? AND cause='bot' "
                     "AND recipient LIKE 'bot:%' AND mode IN ('steer','queue') AND state<>'capped'",
                     (root_id, _guard_window())).fetchone()[0]


def _over_guard(c, root_id):
    return _guard_used(c, root_id) >= LOOP_GUARD_PER_HOUR


def _guard_primary(c, root, message, bot):
    """A bot's message to a bot through `post`, past the guard: the run it would start waits (`capped`), never lost."""
    if _cause(message.get("from_actor")) != "bot" or (message.get("refs") or {}).get("quiet"):
        return
    n = c.execute("SELECT COUNT(*) FROM message_deliveries WHERE root_id=? AND created_at>=? AND cause='bot' "
                  "AND recipient LIKE 'bot:%' AND mode IN ('steer','queue') AND state<>'capped' AND message_id<>?",
                  (root["id"], _guard_window(), message["id"])).fetchone()[0]
    if n < LOOP_GUARD_PER_HOUR:
        return
    c.execute("UPDATE message_deliveries SET state='capped', updated_at=? WHERE message_id=? AND recipient=?",
              (_now(), message["id"], bot))
    if c.execute("SELECT 1 FROM jobs WHERE id=? AND state='queued'", (message["id"],)).fetchone():
        c.execute("UPDATE jobs SET state='cancelled' WHERE id=? AND state='queued'", (message["id"],))
    _capped_notice(c, root)


def _capped_notice(c, root):
    """One Needs-you event per root while it has capped deliveries (threads.capped lists them)."""
    if c.execute("SELECT 1 FROM events WHERE action='thread.capped' AND target=? AND ts>=? LIMIT 1",
                 (root["id"], _guard_window())).fetchone():
        return
    _event(c, H.KEEPER, "thread.capped", root["id"], {"limit": LOOP_GUARD_PER_HOUR})


def capped(c, v):
    """The roots the viewer reads that hold capped wakes: [{root_id, title, count, oldest}]."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    rows = c.execute("SELECT root_id, COUNT(*) AS n, MIN(created_at) AS oldest FROM message_deliveries "
                     "WHERE state='capped' GROUP BY root_id ORDER BY oldest LIMIT 200").fetchall()
    out = []
    for r in rows:
        if r["root_id"] and thread_readable(c, v, r["root_id"]):
            t = thread_row(c, r["root_id"]) or {}
            out.append({"root_id": r["root_id"], "title": t.get("subject") or "", "count": r["n"],
                        "oldest": r["oldest"]})
    return out


def _involved(c, v, root):
    """The viewer owns the root, is a member or participant, or follows it: whose Needs attention its holds are."""
    if root.get("owner_actor") == v.actor or v.actor in (root.get("participants") or []):
        return True
    if _is_member(c, v, root["id"]):
        return True
    return c.execute("SELECT 1 FROM thread_followers WHERE thread_id=? AND actor=? AND following=1",
                     (root["id"], v.actor)).fetchone() is not None


def resume_capped(c, root_id, *, limit=None):
    """Let capped wakes in this root go, oldest first: each starts its run through a carrier. A person posting in the
    root lets them all go; the scheduler (`reopen_windows`) lets go as many as the reopened window allows, and each of
    those counts in the window from now, or the next pass would see room again and the whole backlog would go at once."""
    rows = c.execute("SELECT d.*, m.conversation_id AS cid FROM message_deliveries d JOIN messages m ON m.id=d.message_id "
                     "WHERE d.root_id=? AND d.state='capped' ORDER BY d.created_at, d.id"
                     + (" LIMIT %d" % int(limit) if limit is not None else ""), (root_id,)).fetchall()
    for d in rows:
        message = H.message(c, d["message_id"])
        thread = thread_row(c, d["cid"])
        if not message or not thread:
            c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE id=?", (_now(), d["id"]))
            continue
        carrier = _carrier(c, thread, message, d["recipient"], d["reason"], None)
        if carrier is None:
            c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE id=?", (_now(), d["id"]))
            continue
        now = _now()
        c.execute("UPDATE message_deliveries SET state='pending', carrier_id=?, updated_at=?, created_at=? WHERE id=?",
                  (carrier, now, now if limit is not None else d["created_at"], d["id"]))
    if rows:
        _event(c, H.KEEPER, "thread.resumed", root_id, {"deliveries": len(rows)})
    return len(rows)


def _reopened(c):
    """[(root_id, room)]: the roots with capped wakes whose rolling hour has room, those waiting longest first, at most
    REOPEN_ROOTS. A root with no room has had a full hour's wakes, so it soon gives way to the older ones."""
    out = []
    for r in c.execute("SELECT root_id FROM message_deliveries WHERE state='capped' AND root_id IS NOT NULL "
                       "GROUP BY root_id ORDER BY MIN(created_at) LIMIT ?", (REOPEN_ROOTS,)).fetchall():
        room = LOOP_GUARD_PER_HOUR - _guard_used(c, r[0])
        if room > 0:
            out.append((r[0], room))
    return out


def reopen_windows(c):
    """Let go, in each root with capped wakes, as many as its rolling hour has room for. Returns how many."""
    return sum(resume_capped(c, root_id, limit=room) for root_id, room in _reopened(c))


def reopen_due(store):
    """The scheduler's pass, on every tick and with no model: with nothing capped it reads one index, and it takes the
    write lock only when a window has room."""
    with store.read() as c:
        if not _reopened(c):
            return 0
    with store.transaction() as c:
        return reopen_windows(c)


# ----------------------------------------------------------------------------- the write door
def _actor_of(principal):
    v = _as_viewer(principal)
    return (v.actor if v else str(principal or "")), v


def _held_answer(c, v, gate):
    """A held post's answer as its sender reads it: the authorized hold view (text, can_withdraw, the nonce for a
    reviewer). A caller with no viewer gets the check's own view."""
    from . import org_rule_check
    return org_rule_check.held_answer(c, v.who, gate, auth=v.auth) if v else org_rule_check.answer(gate)


def _post_making(c, principal, thread_id, body, start, parent_message_id, checked, args):
    """The part of a post that makes its thread: everything is checked, then the org-rule verdict is applied, and only
    a message that goes makes the thread (or the sub-thread). Returns {"held": answer} or the thread to post in."""
    actor, v = _actor_of(principal)
    if not str(body or "").strip() and not args.get("attachments"):
        raise Problem("empty", "Write the message", 422)
    plan = None
    if start is not None:
        if thread_id:
            raise Problem("start", "A post that starts a thread names no thread", 422)
        if args.get("as_actor") is not None:
            raise Problem("start", "A remote author posts into a linked thread", 422)
        start = dict(start)
        if "dm" in start:
            members = _dm_members_for(c, actor, start.get("dm"))
            existing = _open_dm(c, actor, members)
            plan = None if existing else _create_plan(c, principal, audience="personal", members=members,
                                                      title=start.get("title") or "")
            if existing:
                thread_id = existing
        else:
            allowed = ("title", "audience", "audience_group_id", "members", "task_id", "kind")
            plan = _create_plan(c, principal, **{k: start[k] for k in allowed if k in start})
    else:
        parent = H.message(c, parent_message_id)
        thread = thread_row(c, thread_id)
        if not parent or thread is None or parent["conversation_id"] != thread["id"]:
            raise Problem("not_found", "Message not found", 404)
        if v is not None and (not message_readable(c, v, parent) or not can_post(c, v, thread)):
            raise Problem("not_found", "Message not found", 404)
        if args.get("as_actor") is not None and not remote_member(c, root_of(c, thread), args["as_actor"]):
            raise Problem("not_found", "Thread not found", 404)
    text = str(body or "")
    if checked is not None:
        from . import org_rule_check
        replay = {"thread_id": thread_id if start is None else None, "body": text, "kind": args.get("kind"),
                  "refs": args.get("refs") or None, "parent_message_id": parent_message_id,
                  "to_actor": args.get("to_actor"), "via": args.get("via"), "origin": args.get("origin"),
                  "attachments": args.get("attachments"), "start": start}
        gate = org_rule_check.apply(c, checked, text, replay, audience_now=org_rule_check.thread_people(c, replay))
        if gate and gate.get("moved"):
            raise Problem("moved", "The thread changed while the message was checked; send it again", 409,
                          retryable=True)
        if gate and gate.get("held"):
            _emit_message_held(c, thread_row(c, thread_id) if thread_id else None, actor, gate)
            return {"held": _held_answer(c, v, gate)}
        if gate and gate.get("warn"):
            text = org_rule_check.warned_text(gate, text)
    if plan is not None:
        thread_id = _create_from(c, principal, plan)["id"]
    elif start is None:
        thread_id = sub_thread(c, principal, parent_message_id)["id"]
    return {"thread_id": thread_id, "body": text}


def remote_install(actor):
    """The install a remote author belongs to (`human:ana@<install>`, `bot:legal@<install>`), else None."""
    match = REMOTE_ACTOR.match(str(actor or ""))
    return match.group(2) if match else None


def remote_guest(c, actor):
    """`remote_install` for an actor not on this roster: a federation guest (a local id with an @ is a teammate)."""
    install = remote_install(actor)
    return install if install and H.resolve_actor(c, actor) is None else None


def remote_member(c, root, actor):
    """C1 for a remote author (a guest): a member row for it, or for its install's link (`peer:<install>`), on the
    root. Never an owner, never Manage, no in-turn grant."""
    install = remote_guest(c, actor)
    if not install or root is None:
        return False
    return c.execute("SELECT 1 FROM thread_members WHERE thread_id=? AND actor IN (?,?) LIMIT 1",
                     (root["id"], actor, "peer:" + install)).fetchone() is not None


def post(c, principal, thread_id, body, *, kind="say", refs=None, parent_message_id=None, to_actor=None, via=None,
         origin=None, checked=None, attachments=None, as_actor=None, start=None, in_reply_to=None):
    """Write a message (contract §3.1). Inside the caller's write transaction; `checked` is the org-rule verdict its
    preflight returned outside it. `as_actor`: a remote author (federation, spec §7.2), only for the keeper writing
    `via="peer"`, and only in the signing install's namespace (`<kind>:<name>@<origin install>`).

    A post that would make a thread (`start`: a new thread's `create` arguments, or `{"dm": [...], "title"}`, with
    `thread_id` None; or a `parent_message_id` with no sub-thread yet) is checked first: a held message makes
    nothing, and its Send anyway replays this same post."""
    actor, v = _actor_of(principal)
    if as_actor is not None:
        install = str((origin or {}).get("install") or "")
        if actor != H.KEEPER or v is not None or via != "peer" or not install:
            raise Problem("forbidden", "Only the keeper writes as a remote author, for a peer", 403)
        namespaced = re.fullmatch(r"(human|bot):[^@\s]+@" + re.escape(install), str(as_actor))
        if not namespaced or not remote_guest(c, as_actor):
            raise Problem("actor", "A remote author is in its signer's namespace: <kind>:<name>@" + install, 422)
        actor = as_actor                 # the author from here on; the keeper only carried it in
    if origin and origin.get("origin_id") and start is not None:
        raise Problem("start", "A replicated message posts into a linked thread", 422)
    if start is not None or (parent_message_id and not c.execute(
            "SELECT 1 FROM conversations WHERE parent_message_id=?", (parent_message_id,)).fetchone()):
        made = _post_making(c, principal, thread_id, body, start, parent_message_id, checked,
                            dict(kind=kind, refs=refs, to_actor=to_actor, via=via, origin=origin,
                                 attachments=attachments, as_actor=as_actor))
        if made.get("held") is not None:
            return made["held"]
        thread_id, body, parent_message_id, checked = made["thread_id"], made["body"], None, None
    thread = thread_row(c, thread_id)
    if thread is None:
        raise Problem("not_found", "Thread not found", 404)
    if parent_message_id:
        parent = H.message(c, parent_message_id)
        if not parent or parent["conversation_id"] != thread["id"]:
            raise Problem("not_found", "Message not found", 404)
        if v is not None and not message_readable(c, v, parent):
            raise Problem("not_found", "Message not found", 404)
        thread = sub_thread(c, principal, parent_message_id)
    if v is not None:
        if denial(c, v, thread) is not None:
            raise Problem("not_found", "Thread not found", 404)
        if not can_post(c, v, thread):
            raise Problem("forbidden", "You can read this thread but not post in it", 403)
    if as_actor is not None:
        if not remote_member(c, root_of(c, thread), as_actor):
            raise Problem("not_found", "Thread not found", 404)
        if kind not in ("say", "ask", "answer"):
            raise Problem("kind", "A remote author says, asks or answers", 422)
    if origin and origin.get("origin_id"):
        duplicate = _duplicate(c, origin, thread)
        if duplicate is not None:
            return duplicate
    if kind not in ("say", "ask", "answer", "notice", "steer"):
        raise Problem("kind", "A message is say, ask, answer, notice or steer", 422)
    text = str(body or "")
    if not text.strip() and not attachments:
        raise Problem("empty", "Write the message", 422)
    given_refs, refs = refs, dict(refs or {})
    if via:
        refs["via"] = via
    if quiet_notice({"refs": refs}):
        refs["quiet"] = True            # nor does it start the addressed bot's run (the queue trigger reads quiet)
    if origin:
        refs["origin"] = {k: origin.get(k) for k in ("origin_id", "revision", "install", "actor") if origin.get(k)}
    if attachments:
        refs["attachments"] = list(attachments)
    found = mentions(c, thread, text, author=actor)
    target = None
    if to_actor:
        target = H.resolve_actor(c, to_actor) or (to_actor if str(to_actor).startswith(OUTSIDE_PREFIXES) else None)
        if not target:
            raise Problem("not_found", "Unknown recipient", 404)
        # Addressing is an answer about who exists: anyone not in the thread with the author takes the See gate.
        if v is not None and not _in_it_together(c, thread, actor, target) and not _may_see(c, v, target):
            raise Problem("not_found", "Unknown recipient", 404)
        if not str(target).startswith(OUTSIDE_PREFIXES) and not audience_has(c, thread, target):
            raise Problem("forbidden", "That teammate cannot read this thread. Add them first.", 403)
    elif kind == "ask":
        target = next((a for a, _ in found if audience_has(c, thread, a)), None)
        if not target:
            raise Problem("ask", "An ask names who it asks: @mention them", 422)
    elif audience_of(root_of(c, thread)) in ("personal", "participants") and not thread.get("parent_id"):
        others = [a for a in _dm_members(c, root_of(c, thread)) if a != actor]
        target = others[0] if len(others) == 1 else None
    if checked is not None:
        # The org-rule verdict its preflight returned, applied inside the write: a hold writes nothing (no message, no
        # delivery) and answers with the hold; a warning goes on the recipients' copy. The payload is the body the
        # reviewer reads; `replay` is what Send anyway runs (org_rule_check's `threads` replayer: this post again as
        # the person it was held for, the check skipped). Callers preflight with operation `threads`.
        from . import org_rule_check
        replay = {"thread_id": thread_id, "body": text, "kind": kind, "refs": given_refs or None,
                  "parent_message_id": parent_message_id, "to_actor": to_actor, "via": via, "origin": origin,
                  "attachments": attachments, "in_reply_to": in_reply_to}
        gate = org_rule_check.apply(c, checked, text, replay, audience_now=org_rule_check.thread_people(c, replay))
        if gate and gate.get("moved"):
            raise Problem("moved", "The thread changed while the message was checked; send it again", 409,
                          retryable=True)
        if gate and gate.get("held"):
            _emit_message_held(c, thread, actor, gate)
            return _held_answer(c, v, gate)
        if gate and gate.get("warn"):
            text = org_rule_check.warned_text(gate, text)
    token = POSTING.set({"origin": origin or {}, "attachments": bool(attachments), "via": via})
    try:
        if as_actor is not None:
            message = H._write_message(c, as_actor, target, text, thread, kind, refs, in_reply_to, None)
        elif target and not str(target).startswith(OUTSIDE_PREFIXES):
            message = H.say(c, actor, target, text, thread["id"], kind, refs, in_reply_to=in_reply_to)
        else:
            message = _write_open(c, actor, text, thread, kind, refs, target, in_reply_to)
    finally:
        POSTING.reset(token)
    # Here and not in the upload route, so a held message released later (the replay of this post) keeps its files.
    for item in attachments or ():
        c.execute("INSERT OR IGNORE INTO message_assets VALUES(?,?)", (message["id"], item["id"]))
    _audit(c, actor, "message:" + message["id"], None, {"thread_id": thread["id"], "kind": kind}, via)
    return message


def _duplicate(c, origin, thread):
    """A repeat of a replicated message (same origin and revision), checked once the writer may post here: the
    existing message, when it is in this thread and came from the same signer; else 409 (an origin is never
    reused across threads or installs)."""
    found = c.execute("SELECT message_id FROM message_revisions WHERE origin_id=? AND revision=?",
                      (origin["origin_id"], int(origin.get("revision") or 0))).fetchone()
    if not found:
        return None
    existing = H.message(c, found[0], include_deleted=True)
    stored = ((existing or {}).get("refs") or {}).get("origin") or {}
    signer = str(stored.get("install") or str(origin["origin_id"]).rpartition("@")[2])
    if (existing is None or existing.get("conversation_id") != thread["id"]
            or signer != str(origin.get("install") or signer)):
        raise Problem("origin_taken", "That origin belongs to another message", 409)
    return {**existing, "duplicate": True}


# Outgoing thread webhooks (backend/thread_webhooks.py): create, every written message (`record`) and a held post each
# call one. Inside the write transaction, so a rolled-back write queues nothing; they only enqueue and never raise.
def _emit_thread_created(c, thread, actor=None):
    """Every way of making a thread ends here, so a webhook or a rule hears one event of one shape: a thread from a legacy
    writer stores no audience (its columns imply it, and its root decides for a sub-thread), and a shared room no owner."""
    from . import thread_webhooks
    starter = actor or thread.get("owner_actor") or (thread.get("participants") or [None])[0]
    facts = {**thread, "audience": audience_of(root_of(c, thread)), "owner_actor": home_owner(thread)}
    thread_webhooks.thread_created(c, facts, starter)


def _emit_message_created(c, thread, message):
    from . import thread_webhooks
    thread_webhooks.message_created(c, message, thread, message.get("from_actor"))


def _emit_message_held(c, thread, actor, verdict):
    """A held post writes no message, so the event names the hold. A held first message (no thread yet) has no
    readers to tell, so it sends nothing; the hold reaches its reviewer through Needs you."""
    if thread is None or not (verdict or {}).get("hold_id"):
        return
    from . import thread_webhooks
    hold_id = verdict["hold_id"]
    held = {"id": hold_id, "conversation_id": thread["id"], "from_actor": actor, "kind": "say"}
    thread_webhooks.message_held(c, held, thread, hold_id, (verdict.get("rule") or {}).get("id"), actor)


def _write_open(c, actor, text, thread, kind, refs, target, in_reply_to=None):
    """A message addressed to nobody (or to an outside destination): the guards hubdb.say runs that do not need a
    recipient, then the same insert."""
    H._writer(c, actor)
    on_task = refs.get("task") or thread.get("task_id")
    H.path_mentioned(c, actor, "task note" if on_task else "message", text, on_task)
    return H._write_message(c, actor, target, text, thread, kind, refs, in_reply_to, None)


def _message_view(c, message, warnings=None):
    out = dict(message)
    if warnings:
        out["warnings"] = list(out.get("warnings") or []) + warnings
    return out


# ----------------------------------------------------------------------------- creating threads
def _insert_thread(c, actor, *, title, audience, audience_group_id=None, owner_actor=None, parent=None,
                   parent_message_id=None, task_id=None, kind="chat", participants=()):
    tid = _new_id()
    now = _now()
    if parent is not None:
        root = root_of(c, parent)
        audience, audience_group_id, task_id = root.get("audience"), root.get("audience_group_id"), root.get("task_id")
        path = (parent.get("path") or f"/{parent['id']}/") + tid + "/"
        root_id = root["id"]
    else:
        path, root_id = f"/{tid}/", tid
    people = list(dict.fromkeys([actor] + [p for p in participants if p != actor])) if participants else [actor]
    c.execute("INSERT INTO conversations(id,kind,subject,task_id,participants_json,created,last_message_at,closed_at,"
              "scope,owner_actor,room_key,audience,audience_group_id,parent_id,parent_message_id,root_id,path,"
              "last_seq,last_unread_seq,archived_at,updated_at) VALUES(?,?,?,?,?,?,NULL,NULL,?,?,NULL,?,?,?,?,?,?,"
              "0,0,NULL,?)",
              # The legacy scope as hubdb writes it (scope is NOT NULL). Who reads a thread is its root's audience.
              (tid, kind, str(title or "")[:MAX_TITLE], task_id, H._dump(people), now,
               "task" if task_id else "direct", owner_actor, audience,
               audience_group_id, parent["id"] if parent is not None else None, parent_message_id, root_id, path, now))
    return thread_row(c, tid)


def create(c, principal, *, title="", audience="members", audience_group_id=None, members=(), parent_message_id=None,
           task_id=None, kind="chat"):
    """A new root thread (or, with `parent_message_id`, that message's sub-thread). The starter is its owner member.
    A thread made with its first message is `post(..., start={...})`: a held first message makes no thread."""
    if parent_message_id:
        return view(c, principal, sub_thread(c, principal, parent_message_id))
    row = _create_from(c, principal, _create_plan(c, principal, title=title, audience=audience,
                                                  audience_group_id=audience_group_id, members=members,
                                                  task_id=task_id, kind=kind))
    return view(c, principal, thread_row(c, row["id"]))


def _create_plan(c, principal, *, title="", audience="members", audience_group_id=None, members=(), task_id=None,
                 kind="chat"):
    """Every check a new root thread needs, writing nothing: what `_create_from` then makes."""
    actor, v = _actor_of(principal)
    if audience not in ("company", "team", "members", "task", "personal"):
        raise Problem("audience", "A new thread is company, team, members, task or personal", 422)
    if audience == "team":
        if not audience_group_id or audience_group_id not in _group_ids(c):
            raise Problem("audience", "A team thread names its group", 422)
    if audience == "task":
        if not task_id or (v is not None and not _task_readable(c, v, task_id)):
            raise Problem("not_found", "Task not found", 404)
    if audience in ("company", "team") and v is not None and not v.is_person:
        raise Problem("forbidden", "A bot starts members threads; a person makes them public", 403)
    resolved = []
    for m in members or ():
        target = H.resolve_actor(c, m) if not str(m).startswith("group:") else m
        if not target:
            raise Problem("not_found", "Unknown teammate", 404)
        if target != actor:
            _require_write(c, v, target)
            resolved.append(target)
    return {"title": title, "audience": audience, "audience_group_id": audience_group_id, "members": resolved,
            "task_id": task_id, "kind": kind}


def _create_from(c, principal, plan):
    actor, _v = _actor_of(principal)
    audience, resolved = plan["audience"], plan["members"]
    owner = actor if audience != "task" else None
    # On a public thread a bot's participation is its grant (`_bot_standing`); a person already reads it.
    bots = [m for m in resolved if _public_member(c, m)] if audience in PUBLIC_AUDIENCES else ()
    row = _insert_thread(c, actor, title=plan["title"], audience=audience, audience_group_id=plan["audience_group_id"],
                         owner_actor=owner, task_id=plan["task_id"], kind=plan["kind"], participants=bots)
    now = _now()
    if audience in ("members", "personal"):
        for who, role in [(actor, "owner")] + [(m, "member") for m in resolved]:
            c.execute("INSERT OR IGNORE INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?)", (_new_id(), row["id"], who, role, actor, now, now))
    for who in [actor] + resolved:
        _follow(c, row["id"], who, "starter" if who == actor else "joined", 0)
    _event(c, actor, "thread.created", row["id"], {"audience": audience, "members": resolved})
    _emit_thread_created(c, row)
    _audit(c, actor, "thread:" + row["id"], None, {"audience": audience, "title_length": len(row["subject"] or ""),
                                                    "members": resolved})
    return row


def _require_write(c, v, target):
    """Adding a teammate to a thread needs Write on them, or the reply-back rule."""
    if v is None:
        return
    from . import teammate_access
    if str(target).startswith(OUTSIDE_PREFIXES) or remote_guest(c, target):
        return                           # an outside destination or a remote guest: not a teammate here
    try:
        levels = teammate_access.levels_for(c, v.who, target, auth=v.auth)
    except (ValueError, KeyError):
        raise Problem("not_found", "Unknown teammate", 404) from None
    if not levels["see"]:
        raise Problem("not_found", "Unknown teammate", 404)
    if not levels["write"] and not teammate_access.may_reply(c, v.who, target):
        raise Problem("forbidden", f"You can see {target} but not add them. Ask whoever manages them for Write.",
                      403)


def dm(c, principal, members, *, title=""):
    """A personal thread with exactly these teammates: the open one, or a new one. With a first message it is
    `post(..., start={"dm": members})`: a held first message makes no DM."""
    actor, v = _actor_of(principal)
    resolved = _dm_members_for(c, actor, members)
    # Write gates starting a DM (`_create_plan`). Opening one you are already in needs nothing more.
    existing = _open_dm(c, actor, resolved)
    if existing and (v is None or denial(c, v, thread_row(c, existing)) is None):
        return view(c, principal, thread_row(c, existing))
    return create(c, principal, title=title, audience="personal", members=resolved)


def _dm_members_for(c, actor, members):
    resolved = []
    for m in members or ():
        target = H.resolve_actor(c, m)
        if not target:
            raise Problem("not_found", "Unknown teammate", 404)
        if target != actor and target not in resolved:
            resolved.append(target)
    if not resolved:
        raise Problem("members", "Name who the DM is with", 422)
    return resolved


def _open_dm(c, actor, resolved):
    """The open personal thread with exactly `resolved` and the actor, or None."""
    want = set(resolved) | {actor}
    for r in c.execute("SELECT c.id FROM conversations c JOIN thread_members m ON m.thread_id=c.id "
                       "WHERE c.audience='personal' AND c.parent_id IS NULL AND c.closed_at IS NULL "
                       "AND c.archived_at IS NULL AND m.actor=? ORDER BY c.last_seq DESC LIMIT 500", (actor,)):
        have = {x[0] for x in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (r[0],))}
        if have == want:
            return r[0]
    # A migrated DM or personal chat has no stored audience: its participant list is who it is with.
    for r in c.execute("SELECT c.id, c.participants_json, c.scope, c.owner_actor FROM conversations c "
                       "WHERE c.audience IS NULL AND c.kind='chat' AND c.task_id IS NULL AND c.parent_id IS NULL "
                       "AND c.closed_at IS NULL AND c.archived_at IS NULL "
                       "AND COALESCE(c.scope,'direct') IN ('direct','personal') "
                       f"AND EXISTS (SELECT 1 FROM {_PARTICIPANTS.format(r='c')} WHERE value=?) "
                       "ORDER BY COALESCE(c.last_message_at,c.created) DESC LIMIT 200", (actor,)):
        if set(H._json(r[1], []) or []) == want and (r[2] != "personal" or r[3] == actor):
            return r[0]
    return None


def personal_dm(c, thread_id, actor, people):
    """Make a row a legacy writer just opened a DM as `threads.dm` makes one: `personal`, read by its members only, so
    no owner inspection applies to it. The scope stays as `_insert_thread` writes it."""
    now = _now()
    c.execute("UPDATE conversations SET audience='personal', owner_actor=?, updated_at=? WHERE id=?",
              (actor, now, thread_id))
    for who in people:
        c.execute("INSERT OR IGNORE INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?)", (_new_id(), thread_id, who, "owner" if who == actor else "member", actor,
                                            now, now))
        _follow(c, thread_id, who, "starter" if who == actor else "joined", 0)


def in_membership(c, thread, actor):
    """Whether `actor` is one of the thread's own people: on its root's participant list or among its members, directly
    or through a group. Unlike `audience_has`, a company thread's readers are not all in it."""
    root = root_of(c, thread)
    if root is None:
        return False
    if actor in (root.get("participants") or []):
        return True
    v = actor_viewer(c, actor)
    names = v.member_names(c) if v is not None else [actor]
    return c.execute("SELECT 1 FROM thread_members WHERE thread_id=? AND actor IN (%s) LIMIT 1"
                     % ",".join("?" * len(names)), (root["id"], *names)).fetchone() is not None


def _in_it_together(c, thread, actor, other):
    return in_membership(c, thread, actor) and in_membership(c, thread, other)


def _may_see(c, v, target):
    from . import teammate_access
    if str(target).startswith(OUTSIDE_PREFIXES) or remote_guest(c, target):
        return True                      # not a teammate here: the outside destination's own rules apply
    try:
        return teammate_access.levels_for(c, v.who, target, auth=v.auth)["see"]
    except (ValueError, KeyError):
        return False


def asks_still_readable(c, actor, messages):
    """The loose asks to `actor` they may still read. A stored audience is the outer boundary: an ask in a thread they
    left is off their list until they are back. A row with no stored audience keeps its addressee, who may never have
    been on its participant list."""
    v = None
    out = []
    for m in messages:
        root = root_of(c, m["conversation_id"])
        if root is not None and root.get("audience") in AUDIENCES:
            v = v or actor_viewer(c, actor)
            if v is None or not message_readable(c, v, m):
                continue
        out.append(m)
    return out


def search(c, v, query, *, limit=20, before=None):
    """Message search as this viewer: `thread_search` over C1, each hit decided in full and its author named as `Known`
    says. The SQL only narrows; a hit is shown when `message_readable` says so (a private task, a restricted message)."""
    from . import thread_search
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    found = thread_search.search(c, query, thread_sql=readable_sql(c, v, "cv"), message_sql=message_sql(c, v, "m"),
                                 readable=lambda m: message_readable(c, v, m), limit=limit, before=before)
    known = v.known(c)
    hits = [{**hit, "from_actor": known.message({"from_actor": hit["from_actor"],
                                                 "conversation_id": hit["thread_id"]})["from_actor"]}
            for hit in found["hits"]]
    return {**found, "hits": hits}


def home(c, owner_actor, *, create=True):
    """The open Home of a bot, person or group, made on first use."""
    owner = str(owner_actor or "")
    if not owner.startswith(("bot:", "human:", "group:")):
        raise Problem("not_found", "Unknown teammate", 404)
    row = c.execute("SELECT id FROM conversations WHERE audience='home' AND owner_actor=? AND closed_at IS NULL "
                    "AND archived_at IS NULL LIMIT 1", (owner,)).fetchone()
    if row is None and owner.startswith("bot:"):
        # A bot's shared room is its Home (the migration keeps one open per bot).
        row = c.execute(f"SELECT id FROM conversations c WHERE c.scope='shared' AND c.closed_at IS NULL AND "
                        f"c.archived_at IS NULL AND COALESCE(c.room_key,'')<>'' AND {_ROOM_BOT.format(r='c')}=? "
                        "ORDER BY COALESCE(c.last_message_at,c.created) DESC LIMIT 1", (owner[4:],)).fetchone()
    if row:
        return thread_row(c, row[0])
    if not create:
        return None
    kind, ident = owner.split(":", 1)
    if kind == "bot" and not H.bot(c, ident):
        raise Problem("not_found", "Unknown teammate", 404)
    if kind == "human" and not H.human(c, ident):
        raise Problem("not_found", "Unknown teammate", 404)
    if kind == "group" and ident not in _group_ids(c):
        raise Problem("not_found", "Unknown teammate", 404)
    name = ident
    if kind == "bot":
        b = H.bot(c, ident) or {}
        name = b.get("display_name") or b.get("name") or ident
    elif kind == "human":
        name = (H.human(c, ident) or {}).get("name") or ident
    row = _insert_thread(c, H.KEEPER, title=name, audience="home", owner_actor=owner, kind="chat",
                         participants=[owner] if kind != "group" else ())
    _event(c, H.KEEPER, "thread.created", row["id"], {"audience": "home", "home": owner})
    _emit_thread_created(c, row)
    return row


SUB_THREAD_TITLE = 60
_MARKDOWN = re.compile(r"(!?\[([^\]]*)\]\([^)]*\))|[*_`~>#]+")
_MENTION = re.compile(r"(?<![\w@])@[\w.\-]+")


def sub_thread_title(body):
    """A sub-thread's stored title: its parent message's first line, without markdown or @mentions, clipped to about
    60 characters at a word, with "…" when clipped. Never stored: a sub-thread's title is derived on read (`_shown_title`)
    for a viewer who reads the parent, so the parent's current first line shows, and no copy outlives a narrowed parent."""
    line = next((part for part in str(body or "").splitlines() if part.strip()), "")
    line = _MARKDOWN.sub(lambda m: m.group(2) if m.group(1) else "", line)
    line = " ".join(_MENTION.sub("", line).split())
    if len(line) <= SUB_THREAD_TITLE:
        return line
    head = line[:SUB_THREAD_TITLE + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else line[:SUB_THREAD_TITLE]
    return cut.rstrip(" ,.;:-") + "…"


def _stored_message(c, message_id):
    row = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    return dict(row) if row else None


def _derived_title(c, message_id, title):
    """Whether `title` is a sub-thread title its parent message gives: that message's first line, now or in an earlier
    revision of it (a sub-thread keeps the title it was made with after an edit)."""
    lines = {sub_thread_title(body) for (body,) in c.execute(
        "SELECT substr(body, 1, 2000) FROM messages WHERE id=? UNION ALL SELECT substr(body, 1, 2000) FROM "
        "message_revisions WHERE message_id=? AND body IS NOT NULL", (message_id, message_id))}
    return title in lines


def _shown_title(c, v, row):
    """A thread's title as the viewer may read it. A title that is its parent message's first line (stored by an
    earlier build, or derived here for a sub-thread with none) shows only to a viewer who reads that parent message; any
    other stored title shows as stored."""
    title = row.get("subject") or ""
    parent_id = row.get("parent_message_id")
    if not parent_id or (title and not _derived_title(c, parent_id, title)):
        return title
    parent = H.message(c, parent_id, include_deleted=True)
    if v is None or not parent or not message_readable(c, v, parent):
        return ""
    return title or sub_thread_title(parent.get("body"))


def _neutral_titles(c):
    """Clears every stored sub-thread title that is its parent's first line (now or in an earlier revision): such titles
    are derived on read now, so none is stored where a parent can later become private. Idempotent."""
    rows = c.execute("SELECT id, subject, parent_message_id FROM conversations WHERE parent_message_id IS NOT NULL "
                     "AND COALESCE(subject,'')<>''").fetchall()
    for thread_id, title, parent_id in rows:
        if _derived_title(c, parent_id, title):
            c.execute("UPDATE conversations SET subject='' WHERE id=?", (thread_id,))


NEUTRAL_TITLES_KEY = "threads-neutral-titles"


def _neutral_titles_once(c):
    """Once, after the migration's backfill (which sets the flags the rule reads): a new title follows the rule when it
    is made (stored empty, `sub_thread`), so nothing needs this on a later start."""
    if not migrated(c) or c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (NEUTRAL_TITLES_KEY,)).fetchone():
        return
    _neutral_titles(c)
    c.execute("INSERT INTO registry_metadata(key,value_json) VALUES(?,?)", (NEUTRAL_TITLES_KEY, json.dumps({"at": _now()})))


def sub_thread(c, principal, parent_message_id):
    """The message's sub-thread, made if it has none. Two starts at once make one: the loser reads the winner's row
    (the parent_message_id index is unique)."""
    actor, v = _actor_of(principal)
    parent_message = H.message(c, parent_message_id)
    if not parent_message or _carrier_message(parent_message):
        raise Problem("not_found", "Message not found", 404)
    parent = thread_row(c, parent_message["conversation_id"])
    if v is not None and (not message_readable(c, v, parent_message) or not can_post(c, v, parent)):
        raise Problem("not_found", "Message not found", 404)
    found = c.execute("SELECT id FROM conversations WHERE parent_message_id=?", (parent_message_id,)).fetchone()
    if found:
        return thread_row(c, found[0])
    import sqlite3
    try:
        c.execute("SAVEPOINT threads_sub_thread")
        # The title is stored empty: the parent's first line is derived on read (`_shown_title`), for a viewer who reads
        # that parent only, so no stored copy reaches SQL, search, events, webhooks or digests.
        row = _insert_thread(c, actor, title="", audience=None,
                             parent=parent, parent_message_id=parent_message_id,
                             kind=parent.get("kind") or "chat", owner_actor=None)
        for outside in _outside(c, root_of(c, parent)["id"]):        # an outside destination follows its root's replies
            add_outside_delivery(c, row["id"], outside, added_by=actor)
        c.execute("RELEASE threads_sub_thread")
    except sqlite3.IntegrityError:
        c.execute("ROLLBACK TO threads_sub_thread")
        c.execute("RELEASE threads_sub_thread")
        found = c.execute("SELECT id FROM conversations WHERE parent_message_id=?", (parent_message_id,)).fetchone()
        return thread_row(c, found[0])
    _event(c, actor, "thread.sub_thread", row["id"], {"parent": parent["id"], "message": parent_message_id})
    _emit_thread_created(c, row)
    return row


def reply_destination(c, principal, thread, parent_message_id):
    """Where a run's reply goes: the sub-thread of `parent_message_id` when that message is in the same thread tree
    as the run's thread (made if it has none), else the run's thread. A parent from another tree never moves a reply
    there: a run answers only inside the tree its attempt was granted."""
    thread = thread_row(c, thread)
    if not parent_message_id or thread is None:
        return thread["id"] if thread else None
    if thread.get("parent_message_id") == parent_message_id:
        return thread["id"]
    parent = H.message(c, parent_message_id)
    home = thread_row(c, parent["conversation_id"]) if parent else None
    if home is None or (home.get("root_id") or home["id"]) != (thread.get("root_id") or thread["id"]):
        return thread["id"]
    return sub_thread(c, principal if isinstance(principal, str) else _actor_of(principal)[0], parent_message_id)["id"]


# ----------------------------------------------------------------------------- reactions (spec section 3.4)
MAX_EMOJI = 32
MAX_REACTIONS_PER_MESSAGE = 50     # distinct emoji a message carries; the rest are refused


def _reactable(c, principal, message_id):
    """The message, when the principal reads it (C1 plus the per-message check); else 404."""
    actor, v = _actor_of(principal)
    message = H.message(c, message_id)
    if not message or _carrier_message(message) or (v is not None and not message_readable(c, v, message)):
        raise Problem("not_found", "Message not found", 404)
    return actor, message


def _carrier_message(message):
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else H._json(message.get("refs_json"), {})
    return bool((refs or {}).get("carries"))


def react(c, principal, message_id, emoji):
    """Add the principal's reaction. Idempotent: the same emoji twice is one reaction. Returns the message's
    reactions."""
    actor, message = _reactable(c, principal, message_id)
    emoji = str(emoji or "").strip()
    if not emoji or len(emoji) > MAX_EMOJI or any(ch.isspace() for ch in emoji):
        raise Problem("emoji", "A reaction is one emoji or a short :name:", 422)
    distinct = c.execute("SELECT COUNT(DISTINCT emoji) FROM message_reactions WHERE message_id=? AND emoji<>?",
                         (message["id"], emoji)).fetchone()[0]
    if distinct >= MAX_REACTIONS_PER_MESSAGE:
        raise Problem("too_many", "This message has as many different reactions as it can hold", 422)
    added = c.execute("INSERT OR IGNORE INTO message_reactions(id,message_id,actor,emoji,created_at) VALUES(?,?,?,?,?)",
                      (_new_id(), message["id"], actor, emoji, _now())).rowcount
    if added:
        _event(c, actor, "message.reacted", message["id"], {"emoji": emoji, "thread": message["conversation_id"]})
        _audit(c, actor, "message:" + message["id"], None, {"reaction": emoji})
    return reactions(c, [message["id"]]).get(message["id"], [])


def unreact(c, principal, message_id, emoji):
    """Remove the principal's own reaction (a no-op when there is none). Returns the message's reactions."""
    actor, message = _reactable(c, principal, message_id)
    emoji = str(emoji or "").strip()
    gone = c.execute("DELETE FROM message_reactions WHERE message_id=? AND actor=? AND emoji=?",
                     (message["id"], actor, emoji)).rowcount
    if gone:
        _event(c, actor, "message.unreacted", message["id"], {"emoji": emoji, "thread": message["conversation_id"]})
        _audit(c, actor, "message:" + message["id"], {"reaction": emoji}, None)
    return reactions(c, [message["id"]]).get(message["id"], [])


def reactions(c, message_ids):
    """{message id: [{emoji, count, actors}]} for messages the caller already checked, oldest emoji first. `actors`
    keeps the first 20 per emoji."""
    ids = [i for i in dict.fromkeys(message_ids) if i]
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for row in c.execute("SELECT message_id,emoji,actor FROM message_reactions WHERE message_id IN (%s) "
                             "ORDER BY created_at, rowid" % ",".join("?" * len(part)), part):
            bucket = out.setdefault(row[0], {})
            entry = bucket.setdefault(row[1], {"emoji": row[1], "count": 0, "actors": []})
            entry["count"] += 1
            if len(entry["actors"]) < 20:
                entry["actors"].append(row[2])
    return {mid: list(found.values()) for mid, found in out.items()}


# ----------------------------------------------------------------------------- membership and attention
def _require_thread(c, principal, thread_id):
    actor, v = _actor_of(principal)
    row = require(c, v, thread_id) if v is not None else thread_row(c, thread_id)
    if row is None:
        raise Problem("not_found", "Thread not found", 404)
    return actor, v, row


def _root_for_membership(c, principal, thread_id):
    actor, v, row = _require_thread(c, principal, thread_id)
    root = root_of(c, row)
    if audience_of(root) not in ("members", "personal", "participants") + PUBLIC_AUDIENCES:
        raise Problem("audience", "Only a members or public thread or a DM has members", 422)
    return actor, v, root


def _public_member(c, target):
    return str(target).startswith("bot:") and not remote_guest(c, target)


def _with_participants(c, root, added):
    people = list(root.get("participants") or [])
    fresh = [a for a in added if a not in people]
    if fresh:
        c.execute("UPDATE conversations SET participants_json=? WHERE id=?", (H._dump(people + fresh), root["id"]))


def replica_root(c, root_id):
    """Whether this root is a copy of another install's thread, even one this install was removed from: it holds that
    company's words. A copy's id is its own, its origin the home's."""
    return c.execute("SELECT 1 FROM federation_threads WHERE thread_id=? AND origin_id<>thread_id",
                     (root_id,)).fetchone() is not None


def title_from_outside(c, thread):
    """Whether a thread's stored title may be another company's words, so it stays out of what leaves this install: any
    title on a copy of another install's thread, and a sub-thread's on a thread ever shared, which is the first line of
    the message it hangs from (`sub_thread_title`). A thread this install shared keeps its own title."""
    root = thread.get("root_id") or thread["id"]
    row = c.execute("SELECT origin_id FROM federation_threads WHERE thread_id=?", (root,)).fetchone()
    return row is not None and (row["origin_id"] != root or root != thread["id"])


def shared_root(c, root_id):
    """Whether this root is another company's copy here (`replica_root`), or this install's own with a company in it now."""
    return replica_root(c, root_id) or c.execute(
        "SELECT 1 FROM federation_members WHERE thread_id=? AND status='active' LIMIT 1", (root_id,)).fetchone() is not None


def _shared_membership(c, v, root, target, adding):
    """A person changing who is in a shared thread (the keeper, for federation, is not checked here). Links and guests
    come from federation: `peer:` only through sharing and unsharing, and another company's people only through the
    home, whose roster every copy mirrors. A copy's own readers are the ones its admins add."""
    if v is None:
        return
    if str(target).startswith("peer:") or (adding and remote_guest(c, target)):
        raise Problem("shared_membership", "Other companies join a thread through sharing, not its member list", 409)
    if replica_root(c, root["id"]):
        if remote_guest(c, target):
            raise Problem("shared_membership", "The company that shared this thread decides who of theirs is in it", 409)
        if adding and not can_manage(c, v, root):
            raise Problem("forbidden", "An admin adds readers to a shared copy", 403)


def add_member(c, principal, thread_id, actor, *, role="member"):
    me, v, root = _root_for_membership(c, principal, thread_id)
    target = actor if remote_guest(c, actor) or str(actor).startswith(("group:",) + OUTSIDE_PREFIXES) else H.resolve_actor(c, actor)
    if not target:
        raise Problem("not_found", "Unknown teammate", 404)
    _shared_membership(c, v, root, target, True)
    if role not in ("owner", "member"):
        raise Problem("role", "A member is owner or member", 422)
    public = audience_of(root) in PUBLIC_AUDIENCES
    if public and (role != "member" or not _public_member(c, target)):
        raise Problem("audience", "A public thread adds only bots: everyone else already reads it", 422)
    if role == "owner" and (remote_guest(c, target) or str(target).startswith(OUTSIDE_PREFIXES)):
        raise Problem("role", "A remote member is a guest, never an owner", 422)
    if role == "owner" and v is not None and not can_manage(c, v, root):
        raise Problem("forbidden", "Only the thread's owner adds an owner", 403)
    if v is not None and not can_post(c, v, root):
        raise Problem("forbidden", "You can read this thread but not add to it", 403)
    _require_write(c, v, target)
    joining = _newly_reading(c, root, target)
    now = _now()
    if not public:      # a public thread's bots are its participants: thread_members grants nothing there
        c.execute("INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?) ON CONFLICT(thread_id,actor) DO UPDATE SET role=excluded.role, "
                  "updated_at=excluded.updated_at", (_new_id(), root["id"], target, role, me, now, now))
    if public or audience_of(root) == "participants":
        _with_participants(c, root, [target])
    for actor_ in joining:
        _joined(c, root["id"], actor_)
    c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, root["id"]))
    _event(c, me, "thread.member_added", root["id"], {"actor": target, "role": role})
    _audit(c, me, "thread:" + root["id"], None, {"member": target, "role": role})
    return {"thread_id": root["id"], "actor": target, "role": role}


def _newly_reading(c, root, target):
    """The people and bots this member brings to the thread, a group's by its people and bots: only those who could not
    read it before. Someone who already reads it keeps what they have not read yet."""
    if str(target).startswith("group:"):
        from . import teammate_access
        actors = sorted(teammate_access.group_member_actors(c, target[6:]))
    else:
        actors = [target]
    return [a for a in actors if str(a).startswith(("human:", "bot:")) and not remote_guest(c, a)
            and not audience_has(c, root, a)]


def _joined(c, root_id, actor):
    """A join reads up to now across the whole tree: the root (followed) and each sub-thread (a read mark, not a follow),
    so nothing written before it counts as unread. One statement, however many sub-threads there are."""
    now = _now()
    c.execute("INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,"
              "updated_at) SELECT lower(hex(randomblob(16))), id, ?, CASE WHEN id=? THEN 1 ELSE 0 END, 'live', 'joined', "
              "last_seq, ?, ? FROM conversations WHERE root_id=? ON CONFLICT(thread_id,actor) DO UPDATE SET "
              "last_read_seq=MAX(last_read_seq,excluded.last_read_seq), following=MAX(following,excluded.following), "
              "updated_at=excluded.updated_at", (actor, root_id, now, now, root_id))


def remove_member(c, principal, thread_id, actor):
    me, v, root = _root_for_membership(c, principal, thread_id)
    if v is not None and actor != me and not can_manage(c, v, root):
        raise Problem("forbidden", "Only the thread's owner removes a member", 403)
    _shared_membership(c, v, root, actor, False)
    if audience_of(root) in PUBLIC_AUDIENCES and not _public_member(c, actor):
        raise Problem("audience", "A public thread removes only bots: everyone else reads it anyway", 422)
    gone = c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor=?", (root["id"], actor)).rowcount
    # Whatever the audience: a participant's grant reads as membership does, so leaving takes both.
    people = list(root.get("participants") or [])
    if actor in people:
        gone = 1
        c.execute("UPDATE conversations SET participants_json=? WHERE id=?",
                  (H._dump([p for p in people if p != actor]), root["id"]))
    # Losing access: the follower rows of the subtree go, and undelivered deliveries become moot.
    c.execute("DELETE FROM thread_followers WHERE actor=? AND thread_id IN (SELECT id FROM conversations WHERE "
              "root_id=?)", (actor, root["id"]))
    c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE recipient=? AND root_id=? "
              "AND state IN ('pending','capped')", (_now(), actor, root["id"]))
    c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), root["id"]))
    _event(c, me, "thread.member_removed", root["id"], {"actor": actor})
    _audit(c, me, "thread:" + root["id"], {"member": actor}, None)
    return {"thread_id": root["id"], "actor": actor, "removed": bool(gone)}


def members(c, v, thread_id, *, cursor=None, limit=100):
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = require(c, v, thread_id)
    root = root_of(c, row)
    limit = max(1, min(int(limit or 100), MAX_PAGE))
    rows = [dict(r) for r in c.execute("SELECT actor, role, added_by, created_at FROM thread_members WHERE thread_id=? "
                                       "AND actor>? ORDER BY actor LIMIT ?", (root["id"], cursor or "", limit + 1))]
    participant = lambda a: {"actor": a, "role": "member", "added_by": None, "created_at": root.get("created")}
    if audience_of(root) in PUBLIC_AUDIENCES:
        have = {r["actor"] for r in rows}
        rows += [participant(a) for a in root.get("participants") or []
                 if _public_member(c, a) and a > (cursor or "") and a not in have]
        rows = sorted(rows, key=lambda r: r["actor"])[:limit + 1]
    page = rows[:limit]
    if audience_of(root) == "participants" and not page and not cursor:
        page = [participant(a) for a in root.get("participants") or []][:limit]
    # The cursor keeps to the stored rows, so what this viewer may not be told does not move it.
    next_cursor = page[-1]["actor"] if len(rows) > limit else None
    return {"members": v.known(c).members(page, root["id"]), "next_cursor": next_cursor}


def follow(c, v, thread_id, *, notify="live"):
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = require(c, v, thread_id)
    if notify not in NOTIFY:
        raise Problem("notify", "notify is live, mentions, digest or muted", 422)
    now = _now()
    c.execute("INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,"
              "updated_at) VALUES(?,?,?,1,?,'manual',?,?,?) ON CONFLICT(thread_id,actor) DO UPDATE SET following=1, "
              "notify=excluded.notify, updated_at=excluded.updated_at",
              (_new_id(), row["id"], v.actor, notify, row.get("last_seq") or 0, now, now))
    _event(c, v.actor, "thread.followed", row["id"], {"notify": notify})
    _audit(c, v.actor, "thread:" + row["id"], None, {"following": True, "notify": notify})
    return {"thread_id": row["id"], "following": True, "notify": notify}


def unfollow(c, v, thread_id):
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = require(c, v, thread_id)
    c.execute("UPDATE thread_followers SET following=0, updated_at=? WHERE thread_id=? AND actor=?",
              (_now(), row["id"], v.actor))
    _event(c, v.actor, "thread.unfollowed", row["id"], {})
    _audit(c, v.actor, "thread:" + row["id"], {"following": True}, {"following": False})
    return {"thread_id": row["id"], "following": False}


def set_audience(c, principal, thread_id, audience, *, audience_group_id=None):
    """A root's audience, by its starter or an admin; its sub-threads follow."""
    actor, v, row = _require_thread(c, principal, thread_id)
    root = root_of(c, row)
    if root["id"] != row["id"]:
        raise Problem("audience", "A sub-thread has its root's audience", 422)
    if v is not None and not can_manage(c, v, root):
        raise Problem("forbidden", "Only the thread's starter or an admin changes who reads it", 403)
    current = audience_of(root)
    if current not in ("members", "team", "company") or audience not in ("members", "team", "company"):
        raise Problem("audience", "Only a members, team or company thread changes audience", 422)
    if audience != current and shared_root(c, root["id"]):
        raise Problem("shared_audience", "A thread shared with another company stays a members thread", 409)
    if audience == "team" and (not audience_group_id or audience_group_id not in _group_ids(c)):
        raise Problem("audience", "A team thread names its group", 422)
    group = audience_group_id if audience == "team" else None
    c.execute("UPDATE conversations SET audience=?, audience_group_id=?, updated_at=? WHERE root_id=?",
              (audience, group, _now(), root["id"]))
    if audience == "members":
        now = _now()
        owner = root.get("owner_actor") or actor
        c.execute("INSERT OR IGNORE INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                  "VALUES(?,?,?,'owner',?,?,?)", (_new_id(), root["id"], owner, actor, now, now))
        # The bots a public thread had keep their grant, as members now.
        for bot in [a for a in root.get("participants") or [] if _public_member(c, a)]:
            c.execute("INSERT OR IGNORE INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                      "VALUES(?,?,?,'member',?,?,?)", (_new_id(), root["id"], bot, actor, now, now))
    else:
        # A member bot keeps its grant on the public thread: an external agent has no standing read there.
        _with_participants(c, root, [r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?",
                                                             (root["id"],)) if _public_member(c, r[0])])
    _event(c, actor, "thread.audience", root["id"], {"from": current, "to": audience, "group": group})
    _audit(c, actor, "thread:" + root["id"], {"audience": current, "audience_group_id": root.get("audience_group_id")},
           {"audience": audience, "audience_group_id": group})
    return view(c, principal, thread_row(c, root["id"]))


def set_retention(c, principal, thread_id, days):
    """How many days a root keeps its messages and its sub-threads', None to keep them for good. The purge is
    backend/thread_retention.py. A task's or a meeting's messages are kept by their own record."""
    actor, v, row = _require_thread(c, principal, thread_id)
    root = root_of(c, row)
    if root["id"] != row["id"]:
        raise Problem("retention", "A sub-thread keeps its messages as its root does", 422)
    if v is not None and not can_manage(c, v, root):
        raise Problem("forbidden", "Only the thread's owner changes how long it keeps messages", 403)
    if audience_of(root) in ("task", "meeting"):
        raise Problem("retention", "A task's or a meeting's messages are kept with it", 422)
    if days is not None and not (isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= RETENTION_MAX_DAYS):
        raise Problem("retention", f"Keep messages for 1 to {RETENTION_MAX_DAYS} days, or for good", 422)
    before = c.execute("SELECT retention_days FROM conversations WHERE id=?", (root["id"],)).fetchone()[0]
    c.execute("UPDATE conversations SET retention_days=?, updated_at=? WHERE id=?", (days, _now(), root["id"]))
    _event(c, actor, "thread.retention", root["id"], {"days": days})
    _audit(c, actor, "thread:" + root["id"], {"retention_days": before}, {"retention_days": days})
    return view(c, principal, thread_row(c, root["id"]))


def update(c, principal, thread_id, *, title=None, archived=None):
    actor, v, row = _require_thread(c, principal, thread_id)
    if v is not None and not can_manage(c, v, row):
        raise Problem("forbidden", "Only the thread's owner changes it", 403)
    before = {"title_length": len(row.get("subject") or ""), "archived_at": row.get("archived_at")}
    now = _now()
    if title is not None:
        c.execute("UPDATE conversations SET subject=?, updated_at=? WHERE id=?", (str(title)[:MAX_TITLE], now, row["id"]))
    if archived is not None:
        c.execute("UPDATE conversations SET archived_at=?, updated_at=? WHERE id=?",
                  (now if archived else None, now, row["id"]))
    after = thread_row(c, row["id"])
    _event(c, actor, "thread.updated", row["id"], {"renamed": title is not None, "archived": archived})
    _audit(c, actor, "thread:" + row["id"], before, {"title_length": len(after.get("subject") or ""),
                                                    "archived_at": after.get("archived_at")})
    return view(c, principal, after)


# ----------------------------------------------------------------------------- C4: read state
def mark_read(c, v, thread_id, seq):
    """Advance the viewer's read mark in this thread (max wins: another device may be ahead)."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = require(c, v, thread_id)
    seq = min(max(int(seq or 0), 0), int(row.get("last_seq") or 0))
    return {"thread_id": row["id"], "last_read_seq": _read_to(c, row, v.actor, seq)}


def _read_to(c, row, actor, seq):
    """Move `actor`'s read mark in a thread up to `seq` (never back), and read the deliveries up to it. Returns the mark."""
    now = _now()
    c.execute("INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,"
              "updated_at) VALUES(?,?,?,0,'live','read',?,?,?) ON CONFLICT(thread_id,actor) DO UPDATE SET "
              "last_read_seq=MAX(last_read_seq,excluded.last_read_seq), updated_at=excluded.updated_at "
              "WHERE excluded.last_read_seq>last_read_seq", (_new_id(), row["id"], actor, seq, now, now))
    c.execute("UPDATE message_deliveries SET state='read', read_at=?, updated_at=? WHERE recipient=? AND thread_id=? "
              "AND state IN ('pending','delivered') AND message_id IN (SELECT id FROM messages WHERE conversation_id=? "
              "AND seq<=?) AND recipient NOT LIKE 'bot:%'", (now, now, actor, row["id"], row["id"], seq))
    got = c.execute("SELECT last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?",
                    (row["id"], actor)).fetchone()
    return got[0] if got else seq


def task_opened(c, conversation, actor, requester, notice):
    """A task's own thread, as hubdb.task_create makes it (a thread of its own, not a chat it was filed from). Its maker
    reads the thread up to now, so the notice their own action wrote is not unread for them. A requester who did not
    make the task hears of it as new: a silent delivery (mode none, which wakes and pushes nothing), so the thread
    lists in the sidebar until they read it."""
    row = thread_row(c, conversation["id"])
    if row is None:
        return
    if H.is_human(actor):
        _read_to(c, row, actor, int(row.get("last_seq") or 0))
    message = H.message(c, notice["id"]) if notice and notice.get("id") else None
    if message and H.is_human(requester) and requester != actor and not remote_guest(c, requester):
        _insert_delivery(c, message, requester, row, root_of(c, row), "none", "member")


def mark_all_read(c, v, thread_id):
    """Read up to now in the thread and every sub-thread under it."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = require(c, v, thread_id)
    path = row.get("path") or f"/{row['id']}/"
    n = 0
    for sub in c.execute("SELECT id, last_seq FROM conversations WHERE path LIKE ? ESCAPE '\\'",
                         (path.replace("%", "\\%").replace("_", "\\_") + "%",)).fetchall():
        mark_read(c, v, sub["id"], sub["last_seq"])
        n += 1
    return {"threads": n}


def unread_message_sql(m="m"):
    """What makes a message unread for a reader once it is past their mark (contract §5), as SQL over a messages row:
    written by a person or delivered to the reader, not their own, and not history written back. Takes the reader's actor twice. Every surface
    that counts unread (the thread, the sidebar, DMs, email digests) reads this one rule."""
    return (f"{m}.deleted_at IS NULL AND {m}.from_actor<>? AND NOT {_backfill_sql(m)} "
            f"AND ({m}.from_actor LIKE 'human:%' OR EXISTS (SELECT 1 "
            f"FROM message_deliveries unread_d WHERE unread_d.message_id={m}.id AND unread_d.recipient=?))")


def unread(c, v, thread_id, *, follower=None):
    """{unread, activity}: messages after the viewer's mark written by a person or delivered to the viewer (not their
    own) count; other bot posts only make it active."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = thread_row(c, thread_id)
    if row is None:
        return {"unread": 0, "activity": False}
    f = follower if follower is not None else c.execute(
        "SELECT last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?", (row["id"], v.actor)).fetchone()
    # No follower row: the viewer has read nothing here, so the mark is 0 and the contract's rule counts every human or
    # addressed message (a deliveries-only count missed the humans in a sub-thread the viewer never followed).
    # What the migration carried over is never unread (no read marks came with it), nor is imported history.
    mark = max(0 if f is None else (f[0] if not isinstance(f, int) else f), _backfill_floor(c, v))
    if int(row.get("last_seq") or 0) <= mark:
        return {"unread": 0, "activity": False}
    n = c.execute(f"SELECT COUNT(*) FROM messages m WHERE m.conversation_id=? AND m.seq>? AND {unread_message_sql()}",
                  (row["id"], mark, v.actor, v.actor)).fetchone()[0]
    return {"unread": n, "activity": True}


# How many of a DM's other people a thread view names; a title shows three and counts the rest.
DM_WITH_SHOWN = 4
# The threads of a root that are not archived or closed, with the viewer's mark in each (`_subtree_mark`): what its row
# in a list sums up.
_SUBTREE = "conversations s LEFT JOIN thread_followers f ON f.thread_id=s.id AND f.actor={me} "
_SUBTREE_OPEN = "s.archived_at IS NULL AND s.closed_at IS NULL"


def _subtree_mark(c, v):
    """A viewer's mark in a subtree's row, as SQL: their read point, and at least what the migration carried over, which
    is never unread (`unread`)."""
    return f"MAX(COALESCE(f.last_read_seq,0),{int(_backfill_floor(c, v))})"


def subtree_summaries(c, v, root_ids):
    """Per root, for this viewer, from the counters of its whole subtree (contract §5): {root id: {"unread",
    "activity", "activity_seq", "last_message_at"}}, and `new_at` when what is new starts in a sub-thread: the oldest
    unread message, else the sub-thread with the newest activity. A reply under an old parent may be pages up the root's
    timeline, so a row opens the place itself. Asked of the rows a page lists, so its cost follows the page."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    out = {}
    subtree, mark = _SUBTREE.format(me="?"), _subtree_mark(c, v)
    for part in H.chunks(list(root_ids)):
        marks = ",".join("?" * len(part))
        for r in c.execute(f"SELECT s.root_id AS root, MAX(s.last_seq) AS seq, MAX(s.last_message_at) AS at, "
                           f"MAX(s.last_seq>{mark}) AS active FROM {subtree}WHERE s.root_id IN ({marks}) "
                           f"AND {_SUBTREE_OPEN} GROUP BY s.root_id", (v.actor, *part)):
            out[r["root"]] = {"unread": 0, "activity": bool(r["active"]), "activity_seq": int(r["seq"] or 0),
                              "last_message_at": r["at"]}
        for r in c.execute(f"SELECT s.root_id AS root, s.id AS thread, MAX(s.last_seq) FROM {subtree}WHERE s.root_id IN "
                           f"({marks}) AND {_SUBTREE_OPEN} AND s.last_seq>{mark} GROUP BY s.root_id",
                           (v.actor, *part)):
            if r["thread"] != r["root"] and r["root"] in out:
                out[r["root"]]["new_at"] = {"thread_id": r["thread"], "message_id": None}
        # With one MIN in the select, SQLite takes the bare columns from that row: the oldest unread message.
        for r in c.execute(f"SELECT s.root_id AS root, COUNT(*) AS n, MIN(m.seq), s.id AS thread, m.id AS message "
                           f"FROM {subtree}JOIN messages m ON "
                           f"m.conversation_id=s.id AND m.seq>{mark} WHERE s.root_id IN ({marks}) "
                           f"AND {_SUBTREE_OPEN} AND s.last_unread_seq>{mark} AND {unread_message_sql()} "
                           "GROUP BY s.root_id", (v.actor, *part, v.actor, v.actor)):
            if r["root"] in out:
                out[r["root"]]["unread"] = r["n"]
                out[r["root"]]["new_at"] = (None if r["thread"] == r["root"]
                                            else {"thread_id": r["thread"], "message_id": r["message"]})
    return out


def _backfill_floor(c, v):
    """The last seq the threads migration carried over, 0 on an install that had nothing to carry. Read once per
    Viewer: a sidebar asks for it per thread."""
    if v._backfill is None:
        row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (MIGRATION_KEY,)).fetchone()
        v._backfill = int((H._json(row[0], {}) or {}).get("seq") or 0) if row else 0
    return v._backfill


def unread_dms(c, v, *, limit=50):
    """The viewer's DMs with unread messages, newest first (Needs attention)."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    out = []
    for r in c.execute("SELECT c.* FROM conversations c JOIN thread_followers f ON f.thread_id=c.id AND f.actor=? "
                       f"WHERE COALESCE(c.audience,{_derived_audience('c')}) IN ('personal','participants') "
                       "AND c.parent_id IS NULL AND c.last_unread_seq>f.last_read_seq ORDER BY c.last_unread_seq DESC LIMIT ?",
                       (v.actor, max(1, min(int(limit), MAX_PAGE)))).fetchall():
        if thread_readable(c, v, r["id"]):
            u = unread(c, v, r["id"])
            if u["unread"]:
                out.append({"thread_id": r["id"], "title": r["subject"] or "", **u})
    return out


# ----------------------------------------------------------------------------- C5: edit and delete
def _own_say(c, actor, v, message_id):
    message = H.message(c, message_id)
    if not message:
        raise Problem("not_found", "Message not found", 404)
    if v is not None and not message_readable(c, v, message):
        raise Problem("not_found", "Message not found", 404)
    if message.get("from_actor") != actor:
        raise Problem("forbidden", "Only its author changes a message", 403)
    if message.get("kind") not in EDITABLE_KINDS or (message.get("refs") or {}).get("carries"):
        raise Problem("kind", "Asks, answers and notices cannot be changed", 422)
    return message


def _revise(c, message, actor, *, body=None, deleted=False, revision=None, purge=False):
    """Write the new revision: the row, its history line, and what depends on it (mentions, deliveries, runs not started
    yet and every other copy of its words). `purge`: retention removing it here, so an outside destination is not told;
    its copy is that destination's. Returns (revision, reopened asks, warnings for its author)."""
    new = int(revision if revision is not None else int(message.get("revision") or 0) + 1)
    now = _now()
    origin = message.get("origin_id") or message["id"]
    warnings = []
    if deleted:
        c.execute("UPDATE messages SET body='', deleted_at=?, revision=? WHERE id=?", (now, new, message["id"]))
        reopened = [r[0] for r in c.execute("SELECT id FROM messages WHERE answered_by=?", (message["id"],))]
        c.execute("UPDATE messages SET answered_by=NULL WHERE answered_by=?", (message["id"],))
        # Nobody gets a deleted message any more, except an outside destination that already received it: it gets
        # the tombstone below, even while an edit to it was still on its way.
        c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE message_id=? "
                  "AND state IN ('pending','capped','held') AND NOT ((recipient LIKE 'slack:%' OR recipient LIKE "
                  "'peer:%') AND delivered_at IS NOT NULL)", (now, message["id"]))
        if purge:
            c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE message_id=? AND state='pending'",
                      (now, message["id"]))
        if _has_jobs(c):
            c.execute("UPDATE jobs SET state='cancelled' WHERE message_id=? AND state='queued'", (message["id"],))
            c.execute("UPDATE jobs SET state='cancelled' WHERE state='queued' AND message_id IN (SELECT carrier_id FROM "
                      "message_deliveries WHERE message_id=? AND carrier_id IS NOT NULL)", (message["id"],))
        c.execute("DELETE FROM message_mentions WHERE message_id=?", (message["id"],))
        c.execute("DELETE FROM message_reactions WHERE message_id=?", (message["id"],))
        c.execute("DELETE FROM thread_routings WHERE message_id=?", (message["id"],))
        c.execute("UPDATE conversations SET subject='' WHERE parent_message_id=?", (message["id"],))
        H._withdraw_queued_comment_copies(c, message["id"])
        _rewrite_copies(c, message, None)
        if reopened:
            H.recount(c, [message.get("from_actor"), message.get("to_actor")])
    else:
        c.execute("UPDATE messages SET body=?, edited_at=?, revision=? WHERE id=?", (body, now, new, message["id"]))
        warnings = _remention(c, {**message, "body": body, "revision": new}, now)
        _rewrite_copies(c, message, body)
        reopened = []
    if not purge:
        # Outside destinations get the new revision (an edit, or the tombstone).
        c.execute("UPDATE message_deliveries SET state='pending', revision=?, attempts=0, next_attempt_at=?, error=NULL, "
                  "updated_at=? WHERE message_id=? AND (recipient LIKE 'slack:%' OR recipient LIKE 'peer:%') "
                  "AND state IN ('delivered','failed','pending')", (new, now, now, message["id"]))
    c.execute("INSERT OR IGNORE INTO message_revisions(id,message_id,origin_id,revision,body,deleted,actor,created_at) "
              "VALUES(?,?,?,?,?,?,?,?)", (_new_id(), message["id"], origin, new, None if deleted else body,
                                          int(bool(deleted)), actor, now))
    # A retry of an earlier post or edit keeps its effect but never replays words that were changed or taken back.
    H._refresh_comment_replies(c, H.message(c, message["id"], include_deleted=True))
    return new, reopened, warnings


def _has_jobs(c):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone() is not None


def _remention(c, message, now):
    """An edit's mentions, against the ones it had. One taken out no longer reaches anyone who has not had it yet, and
    its run does not start; one added is delivered as a post's mention is, under the same cap, wake rule and loop guard.
    Returns the warnings for the author."""
    thread = thread_row(c, message["conversation_id"])
    root = root_of(c, thread)
    had = {r[0] for r in c.execute("SELECT actor FROM message_mentions WHERE message_id=? AND in_audience=1",
                                   (message["id"],))}
    c.execute("DELETE FROM message_mentions WHERE message_id=?", (message["id"],))
    named, added, warnings = set(), [], []
    for actor, kind in mentions(c, thread, message["body"], author=message.get("from_actor")):
        inside = audience_has(c, thread, actor)
        named.add(actor)
        c.execute("INSERT OR IGNORE INTO message_mentions(id,message_id,actor,kind,in_audience,created_at) "
                  "VALUES(?,?,?,?,?,?)", (_new_id(), message["id"], actor, kind, int(inside), now))
        if not inside:
            warnings.append({"rule": "mention_outside", "actor": actor})
        elif actor not in had:
            added.append(actor)
    for d in c.execute("SELECT id, recipient, carrier_id FROM message_deliveries WHERE message_id=? AND reason='mention' "
                       "AND state='pending'", (message["id"],)).fetchall():
        if d["recipient"] not in named:
            c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE id=?", (now, d["id"]))
            if d["carrier_id"] and _has_jobs(c):
                c.execute("UPDATE jobs SET state='cancelled' WHERE message_id=? AND state='queued'", (d["carrier_id"],))
    if added and not _is_backfill(message.get("refs")):
        c.execute("UPDATE conversations SET last_unread_seq=MAX(last_unread_seq,?) WHERE id=?",
                  (message.get("seq") or 0, thread["id"]))
    for actor in added:
        # A mention taken out and put back gets a delivery again; anyone who already has one keeps it, and is not woken
        # twice for one message.
        c.execute("DELETE FROM message_deliveries WHERE message_id=? AND recipient=? AND reason='mention' AND state='moot'",
                  (message["id"], actor))
        if c.execute("SELECT 1 FROM message_deliveries WHERE message_id=? AND recipient=?",
                     (message["id"], actor)).fetchone():
            continue
        if actor.startswith("human:"):
            _deliver_person(c, thread, root, message, actor, "mention", False, warnings)
        elif actor.startswith("bot:"):
            _deliver_bot(c, thread, root, message, actor, "mention")
    return warnings


def _rewrite_copies(c, message, body):
    """The words a carrier or a deferred note copied from this message follow it: the new text, or none once deleted.
    A note taken back is cancelled, so no later run carries it."""
    carriers = [r[0] for r in c.execute("SELECT carrier_id FROM message_deliveries WHERE message_id=? "
                                        "AND carrier_id IS NOT NULL", (message["id"],))]
    text = "" if body is None else f"{message.get('from_actor') or ''} in this thread: {body}"
    for carrier in carriers:
        c.execute("UPDATE messages SET body=? WHERE id=? AND from_actor=?", (text, carrier, H.KEEPER))
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes'").fetchone():
        if body is None:
            c.execute("UPDATE notes SET body='', cancelled_at=COALESCE(cancelled_at,?) WHERE message_id=?",
                      (_now(), message["id"]))
        else:
            c.execute("UPDATE notes SET body=? WHERE message_id=?", (str(body)[:2000], message["id"]))


def _audited(message_or_text, revision):
    """What an audit row keeps of a message: its revision and length, never its words (the audit log is read by the
    owner, and a DM or a deleted message's text must not reach them through it)."""
    text = message_or_text.get("body") if isinstance(message_or_text, dict) else message_or_text
    return {"revision": int(revision or 0), "length": len(str(text or ""))}


def edit_message(c, principal, message_id, body):
    actor, v = _actor_of(principal)
    message = _own_say(c, actor, v, message_id)
    text = str(body or "")
    if not text.strip():
        raise Problem("empty", "Write the message", 422)
    if text == message.get("body"):
        return message
    thread = thread_row(c, message["conversation_id"])
    if v is not None and not can_post(c, v, thread):
        raise Problem("forbidden", "You can read this thread but not post in it", 403)
    # A private run's edit may only put its words in its own private task's thread (the same check as its post): a
    # company message it wrote earlier cannot carry them. The edit keeps the run's provenance: the message is restricted
    # (its per-message check applies) and the event names the run.
    from . import task_privacy
    private = v is not None and task_privacy.private_execution(c, v.who)
    if private:
        task_privacy.private_thread_destination(c, v.who, thread)
        c.execute("UPDATE messages SET restricted=1 WHERE id=?", (message_id,))
    H.path_mentioned(c, actor, "message", text, thread.get("task_id"))
    revision, _, warnings = _revise(c, message, actor, body=text)
    _event(c, actor, "message.edited", message_id, {"thread": thread["id"], "revision": revision,
                                                     **({"attempt": v.who.attempt_id} if private else {})})
    _audit(c, actor, "message:" + message_id, _audited(message, message.get("revision")), _audited(text, revision))
    edited = H.message(c, message_id)
    return {**edited, "warnings": warnings} if warnings else edited


def delete_message(c, principal, message_id):
    actor, v = _actor_of(principal)
    message = _own_say(c, actor, v, message_id)
    revision, reopened, _ = _revise(c, message, actor, deleted=True)
    _event(c, actor, "message.deleted", message_id, {"thread": message["conversation_id"], "revision": revision,
                                                      "reopened": reopened})
    _audit(c, actor, "message:" + message_id, _audited(message, message.get("revision")),
           {"deleted": True, "revision": revision})
    return {"id": message_id, "deleted": True, "revision": revision}


def apply_revision(c, origin, *, body=None, deleted=False):
    """A replicated edit or delete (Slack, federation). Highest revision wins; an older or repeated one, or anything
    after a delete, is ignored. Returns the message, or None when nothing was applied."""
    oid, revision = str(origin.get("origin_id") or ""), int(origin.get("revision") or 0)
    if not oid:
        return None
    row = c.execute("SELECT * FROM messages WHERE origin_id=?", (oid,)).fetchone()
    if row is None:
        return None
    row = dict(row)
    if c.execute("SELECT 1 FROM message_revisions WHERE origin_id=? AND revision=?", (oid, revision)).fetchone():
        return None
    if row.get("deleted_at") or revision <= int(row.get("revision") or 0):
        c.execute("INSERT OR IGNORE INTO message_revisions(id,message_id,origin_id,revision,body,deleted,actor,"
                  "created_at) VALUES(?,?,?,?,?,?,?,?)", (_new_id(), row["id"], oid, revision,
                                                          None if deleted or row.get("deleted_at") else body,
                                                          int(bool(deleted)),
                                                          str(origin.get("actor") or row["from_actor"]), _now()))
        return None
    actor = str(origin.get("actor") or row["from_actor"])
    _revise(c, row, actor, body=None if deleted else str(body or ""), deleted=deleted, revision=revision)
    _event(c, actor, "message.deleted" if deleted else "message.edited", row["id"],
           {"thread": row["conversation_id"], "revision": revision, "replicated": True})
    return H.message(c, row["id"], include_deleted=True)


# ----------------------------------------------------------------------------- views
def dm_with(c, v, rows):
    """{root id: {"dm_with": [{"actor", "name"}], "dm_with_count"}} for the DMs among `rows`: who else is in each, for its
    title. One read of each table however many DMs a page lists. A reader who is not in a DM is told only of the
    teammates they may see (teammate_access.Known.members); a person in it may name everyone in it, so the name comes
    from here, for someone their people list leaves out."""
    roots = [thread_row(c, r) for r in rows]
    roots = [r for r in roots if audience_of(r) in ("personal", "participants") and not r.get("parent_id")]
    stored = {r["id"]: [] for r in roots}
    for part in H.chunks(list(stored)):
        for r in c.execute("SELECT thread_id, actor FROM thread_members WHERE thread_id IN (%s)"
                           % ",".join("?" * len(part)), part):
            stored[r["thread_id"]].append(r["actor"])
    known, mine, others = v.known(c), set(v.member_names(c)), {}
    for root in roots:
        people = _dm_members(c, root, stored[root["id"]])
        if not mine.intersection(stored[root["id"]]) and not mine.intersection(people):
            people = known.seen(people)
        others[root["id"]] = [a for a in people if a != v.actor]
    shown = {a for people in others.values() for a in people[:DM_WITH_SHOWN]}
    names = {}
    for part in H.chunks(sorted(a[6:] for a in shown if a.startswith("human:"))):
        names.update(("human:" + r[0], r[1]) for r in c.execute(
            "SELECT id, name FROM humans WHERE id IN (%s)" % ",".join("?" * len(part)), part))
    for part in H.chunks(sorted(a[4:] for a in shown if a.startswith("bot:"))):
        names.update(("bot:" + r[0], r[1]) for r in c.execute(
            "SELECT slug, display_name FROM bots WHERE slug IN (%s)" % ",".join("?" * len(part)), part))
    return {root: {"dm_with": [{"actor": a, "name": names.get(a) or ""} for a in people[:DM_WITH_SHOWN]],
                   "dm_with_count": len(people)} for root, people in others.items()}


def view(c, principal, row, *, follower=None, summary=None, dm=None):
    """A thread as `principal` reads it. `summary` (subtree_summaries) makes `me.unread` and `me.activity` the whole
    subtree's, as a root row in a list shows them, in place of this thread's own, and adds `new_at`. `dm` is this row's
    entry from `dm_with`, for a list that read them together."""
    v = _as_viewer(principal)
    row = thread_row(c, row)
    root = root_of(c, row)
    path = [p for p in str(row.get("path") or f"/{row['id']}/").split("/") if p]
    out = {"id": row["id"], "title": _shown_title(c, v, row), "audience": audience_of(root),
           "audience_group_id": root.get("audience_group_id"), "owner_actor": root.get("owner_actor"),
           "parent_id": row.get("parent_id"), "parent_message_id": row.get("parent_message_id"),
           "root_id": row.get("root_id") or row["id"], "path": path, "kind": row.get("kind"),
           "task_id": row.get("task_id"), "created_at": row.get("created"),
           "last_message_at": row.get("last_message_at"), "archived_at": row.get("archived_at"),
           "last_seq": int(row.get("last_seq") or 0), "last_unread_seq": int(row.get("last_unread_seq") or 0),
           "retention_days": root.get("retention_days")}
    if out["audience"] == "home":
        out["home"] = {"actor": home_owner(root)}
    elif v is not None:
        out["owner_actor"] = v.known(c).name(out["owner_actor"], root["id"])
    only_old = v is not None and legacy_reader_only(c, v, row)
    if only_old:
        last = c.execute("SELECT MAX(seq), MAX(created) FROM messages WHERE conversation_id=? AND legacy_audience "
                         "IS NOT NULL", (row["id"],)).fetchone()
        out.update(last_seq=int(last[0] or 0), last_unread_seq=int(last[0] or 0), last_message_at=last[1])
    if v is not None:
        f = follower if follower is not None else c.execute(
            "SELECT following, notify, last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?",
            (row["id"], v.actor)).fetchone()
        u = ({"unread": 0, "activity": False} if only_old else summary if summary is not None
             else unread(c, v, row["id"], follower=(f["last_read_seq"],) if f else None))
        out["me"] = {"member": _is_member(c, v, root["id"]) or v.actor in (root.get("participants") or []),
                     "following": bool(f and f["following"]), "notify": f["notify"] if f else "live",
                     "last_read_seq": f["last_read_seq"] if f else 0, "unread": u["unread"],
                     "activity": u["activity"], "can_post": can_post(c, v, row), "can_manage": can_manage(c, v, row)}
        if summary and not only_old and summary.get("new_at"):
            out["new_at"] = summary["new_at"]
        if out["audience"] in ("personal", "participants") and not row.get("parent_id"):
            out.update(dm or dm_with(c, v, [root]).get(root["id"]) or {})
    return out


def breadcrumb(c, v, row):
    """The thread's path from its root, each title as `v` may read it (`_shown_title`)."""
    v = _as_viewer(v)
    ids = [p for p in str(row.get("path") or f"/{row['id']}/").split("/") if p]
    names = {r["id"]: _shown_title(c, v, dict(r)) for r in c.execute(
        "SELECT id, subject, parent_message_id FROM conversations WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)} \
        if ids else {}
    return [{"id": i, "title": names.get(i, "")} for i in ids]


def get(c, v, thread_id):
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = require(c, v, thread_id)
    return {**view(c, v, row), "breadcrumb": breadcrumb(c, v, row)}


def _message_out(m):
    refs = H._json(m.get("refs_json"), {}) or m.get("refs") or {}
    out = {k: m.get(k) for k in ("id", "conversation_id", "from_actor", "to_actor", "kind", "body", "in_reply_to",
                                 "created", "edited_at", "seq", "revision", "origin_id")}
    out.update(thread_id=m.get("conversation_id"), refs=refs, deleted=bool(m.get("deleted_at")))
    if out["deleted"]:
        out.update(body=None, refs={}, to_actor=None, in_reply_to=None)
    return out


def _depth(row):
    """A thread's place in its tree: 0 for a root, 1 for a sub-thread, and so on (`path`)."""
    return max(0, len([p for p in str(row.get("path") or f"/{row['id']}/").split("/") if p]) - 1)


def _timeline_rows(c, v, rows, thread_id, depth):
    """Messages of one thread, as given, each through the per-message check: the message, its tombstone for a viewer who
    read it before it went, or the quiet hidden row. Consecutive hidden rows of one kind collapse into one; a row that
    stands for shared-room history the viewer's Home Read doesn't reach says so (`earlier`), the "Earlier messages" row."""
    known = v.known(c)
    out, hidden_open = [], None
    for m in rows:
        shown = (readable_before_deletion(c, v, m) if m.get("deleted_at")
                 else message_readable(c, v, m, thread_checked=True))
        if not shown:
            kind = "earlier" if m.get("legacy_audience") and legacy_hidden(c, v, m) else "plain"
            if hidden_open != kind:
                out.append({"id": m["id"], "seq": m["seq"], "thread_id": thread_id, "depth": depth, "hidden": True,
                            **({"earlier": True} if kind == "earlier" else {})})
            hidden_open = kind
            continue
        out.append({**known.message(_message_out(m)), "depth": depth})
        hidden_open = None
    return out


def _branch_tails(c, ids):
    """The latest BRANCH_TAIL messages of each of these threads, newest first in each: one query, whatever the count."""
    marks = ",".join("?" * len(ids))
    tails = {i: [] for i in ids}
    for m in c.execute(
            "SELECT m.* FROM conversations t JOIN messages m ON m.conversation_id=t.id AND m.seq IS NOT NULL AND m.seq>="
            "COALESCE((SELECT x.seq FROM messages x WHERE x.conversation_id=t.id AND x.seq IS NOT NULL "
            f"ORDER BY x.seq DESC LIMIT 1 OFFSET ?),0) WHERE t.id IN ({marks}) ORDER BY m.conversation_id, m.seq DESC",
            (BRANCH_TAIL - 1, *ids)):
        tails[m["conversation_id"]].append(dict(m))
    return tails


def _branch_stats(c, v, branches, below):
    """{thread id: {"count", "older", "unseen", "unread"}} of the messages the viewer reads: how many there are, how many
    sit below the rendered tail (`below`, a seq per thread), how many of those are past the viewer's mark, and the unread
    count (`unread`'s rule). The counts come from one query. Only the rows it can't decide, restricted or from an old
    room, are checked one by one, up to BRANCH_CHECKS, so a count never gives away a message the viewer can't read."""
    me = v.actor
    cuts = ",".join(["(?,?,?)"] * len(branches))
    args = [x for b in branches for x in (b["id"], below[b["id"]], b["mark"])]
    stats = {b["id"]: {"count": 0, "older": 0, "unseen": 0, "unread": 0} for b in branches}
    for r in c.execute(
            f"WITH cut(tid,below,mark) AS (VALUES {cuts}) SELECT cut.tid, COUNT(*), SUM(m.seq<cut.below), "
            "SUM(m.seq<cut.below AND m.seq>cut.mark AND m.from_actor<>?), "
            "SUM(m.seq>cut.mark AND m.from_actor<>? AND (m.from_actor LIKE 'human:%' OR EXISTS (SELECT 1 FROM "
            "message_deliveries d WHERE d.message_id=m.id AND d.recipient=?))) "
            "FROM cut JOIN messages m ON m.conversation_id=cut.tid WHERE m.seq IS NOT NULL AND m.deleted_at IS NULL "
            "AND m.restricted=0 AND m.legacy_audience IS NULL GROUP BY cut.tid", (*args, me, me, me)):
        stats[r[0]].update(count=r[1], older=r[2] or 0, unseen=r[3] or 0, unread=r[4] or 0)
    ids = [b["id"] for b in branches]
    marks = {b["id"]: b["mark"] for b in branches}
    for m in c.execute(f"SELECT * FROM messages WHERE conversation_id IN ({','.join('?' * len(ids))}) AND seq IS NOT NULL "
                       "AND deleted_at IS NULL AND (restricted<>0 OR legacy_audience IS NOT NULL) ORDER BY seq DESC LIMIT ?",
                       (*ids, BRANCH_CHECKS)).fetchall():
        m = dict(m)
        if not message_readable(c, v, m, thread_checked=True):
            continue
        tid, at, past = m["conversation_id"], stats[m["conversation_id"]], m["seq"] > marks[m["conversation_id"]]
        at["count"] += 1
        if m["seq"] < below[tid]:
            at["older"] += 1
            at["unseen"] += int(past and m["from_actor"] != me)
        if past and m["from_actor"] != me and (str(m["from_actor"]).startswith("human:") or m.get("to_actor") == me):
            at["unread"] += 1
    return stats


def _more_row(thread_id, depth, count, cursor, unseen):
    """The older part of a long branch: `GET /threads/{thread_id}/timeline?cursor=` reads it, to put in this row's place."""
    return {"id": f"more:{thread_id}", "thread_id": thread_id, "depth": depth, "collapsed": True, "count": count,
            "cursor": cursor, "unread": bool(unseen)}


def _own_titles(c, found):
    """{branch id: the title someone gave it}, "" while its stored title is only its parent's first line, as the parent
    reads now or read before an edit: a header that repeated the line right above it would say nothing."""
    parents = list({b["parent_message_id"] for b in found})
    marks = ",".join("?" * len(parents))
    lines = {}
    for mid, body in c.execute(
            f"SELECT id, substr(body, 1, 2000) FROM messages WHERE id IN ({marks}) UNION ALL "
            f"SELECT message_id, substr(body, 1, 2000) FROM message_revisions WHERE message_id IN ({marks}) "
            "AND body IS NOT NULL", (*parents, *parents)):
        lines.setdefault(mid, set()).add(sub_thread_title(body))
    return {b["id"]: "" if (b["subject"] or "") in lines.get(b["parent_message_id"], ()) else b["subject"] or ""
            for b in found}


def _branches(c, v, tops):
    """{parent message id: {"sub_thread", "more", "rows"}} for these timeline rows and the rows under them. It walks one
    level of the tree at a time, so the queries follow the tree's depth, not its number of branches. Past MAX_INLINE_ROWS
    or MAX_BRANCH_LEVELS a branch comes as its summary and one "N more" row."""
    out = {}
    frontier, total, level = [t["id"] for t in tops if not t.get("hidden")], 0, 0
    while frontier:
        found = [dict(r) for r in c.execute(
            "SELECT id, parent_message_id, path, subject, last_seq FROM conversations WHERE parent_message_id IN "
            f"({','.join('?' * len(frontier))})", frontier)]
        allowed = readable_ids(c, v, [b["id"] for b in found])
        found = [b for b in found if b["id"] in allowed]
        if not found:
            break
        ids = [b["id"] for b in found]
        read = {r[0]: r[1] for r in c.execute(
            f"SELECT thread_id, last_read_seq FROM thread_followers WHERE actor=? AND thread_id IN ({','.join('?' * len(ids))})",
            (v.actor, *ids))}
        for b in found:
            b["mark"] = int(read.get(b["id"]) or 0)
        tails = _branch_tails(c, ids) if total < MAX_INLINE_ROWS and level < MAX_BRANCH_LEVELS else {}
        below = {b["id"]: (tails[b["id"]][-1]["seq"] if tails.get(b["id"]) else int(b["last_seq"] or 0) + 1) for b in found}
        stats = _branch_stats(c, v, found, below)
        titles = _own_titles(c, found)
        frontier = []
        for b in found:
            at = stats[b["id"]]
            depth = _depth(b)
            rows = _timeline_rows(c, v, tails.get(b["id"], []), b["id"], depth)
            rows.reverse()
            total += len(rows)
            frontier += [r["id"] for r in rows if not r.get("hidden")]
            out[b["parent_message_id"]] = {
                "sub_thread": {"id": b["id"], "title": titles[b["id"]], "count": at["count"], "last_seq": b["last_seq"],
                               "last_read_seq": b["mark"], "unread": at["unread"], "more": at["older"]},
                "more": _more_row(b["id"], depth, at["older"], below[b["id"]], at["unseen"]) if at["older"] else None,
                "rows": rows}
        level += 1
    return out


def timeline(c, v, thread_id, *, cursor=None, limit=50, max_bytes=None):
    """A thread in tree order, newest page first: `cursor` and `limit` page its own messages, and each is followed by its
    sub-thread's messages, recursively, each with its `depth`. A long branch shows its latest messages under a
    `collapsed` row ("N more", with the cursor that reads the rest). Every row passes the per-message check: hidden
    messages are one quiet row, and carriers never show. A page always ends on a whole message and its branch; a first
    message whose branch alone is over the byte cap keeps its summary and an "N more" row in the branch's place."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    row = require(c, v, thread_id)
    limit = max(1, min(int(limit or 50), MAX_PAGE))
    max_bytes = min(int(max_bytes or MAX_PAGE_BYTES), MAX_PAGE_BYTES)
    below = int(cursor) if cursor not in (None, "") else (1 << 62)
    # An old reader of a migrated Home opens only its root, for the history they read: there is no branch for them.
    old_only = legacy_reader_only(c, v, row)
    only_old = " AND legacy_audience IS NOT NULL" if old_only else ""
    rows = [dict(r) for r in c.execute("SELECT * FROM messages WHERE conversation_id=? AND seq IS NOT NULL AND seq<?"
                                       f"{only_old} ORDER BY seq DESC LIMIT ?", (row["id"], below, limit + 1))]
    more = len(rows) > limit
    tops = _timeline_rows(c, v, rows[:limit], row["id"], _depth(row))
    branches = {} if old_only else _branches(c, v, tops)
    shown = [r["id"] for r in tops if not r.get("hidden")] + [
        r["id"] for b in branches.values() for r in b["rows"] if not r.get("hidden")]
    marks = reactions(c, shown)
    known = v.known(c)

    def block(item):
        if item["id"] in marks and not item.get("deleted"):
            item["reactions"] = known.reactions(marks[item["id"]], item["thread_id"])
        found = branches.get(item["id"])
        if not found:
            return [item]
        item["sub_thread"] = found["sub_thread"]
        return [item, *([found["more"]] if found["more"] else []), *(r for child in found["rows"] for r in block(child))]

    pages, size = [], 0
    for item in tops:
        rows_of = block(item)
        bytes_of = sum(len(json.dumps(r, default=str)) for r in rows_of)
        if pages and size + bytes_of > max_bytes:
            more = True
            break
        if bytes_of > max_bytes and len(rows_of) > 1:
            st = item["sub_thread"]
            rows_of = [item, _more_row(st["id"], rows_of[1]["depth"], st["count"], int(st["last_seq"] or 0) + 1,
                                       int(st["last_seq"] or 0) > int(st["last_read_seq"] or 0))]
            bytes_of = sum(len(json.dumps(r, default=str)) for r in rows_of)
        size += bytes_of
        pages.append(rows_of)
    next_cursor = pages[-1][0]["seq"] if more and pages else None
    return {"thread": {**view(c, v, row), **_earlier(c, v, row)}, "breadcrumb": breadcrumb(c, v, row),
            "messages": [r for rows_of in reversed(pages) for r in rows_of], "next_cursor": next_cursor}


def _attention_roots(c, v):
    """{root id: extra row fields}: the roots of what waits on the viewer in Needs attention (thread_attention: open
    mentions and asks, unread DMs, held messages), and every root they are in whose bots' wakes the loop guard holds
    (`capped`): a person's post there lets them go. `new_at` says where the oldest item is, when a sub-thread holds it."""
    from . import thread_attention
    roots = {}
    for item in thread_attention.items(c, None, v, with_capped=False):
        place = item.get("conversation_id") or item.get("thread_id")
        thread = thread_row(c, place) if place else None
        if thread is not None:
            root = thread.get("root_id") or thread["id"]
            roots.setdefault(root, {}).setdefault("new_at", None if thread["id"] == root else {
                "thread_id": thread["id"], "message_id": item.get("message_id")})
    for row in capped(c, v):
        root = thread_row(c, row["root_id"])
        if root is not None and _involved(c, v, root):
            roots.setdefault(root["id"], {}).update(capped=row["count"], capped_since=row["oldest"])
    return roots


def _history_only_roots(c, v):
    """The migrated Homes this viewer reads only as an old reader (legacy_reader_only): nothing new in them may rank or
    date a row of theirs."""
    if not v.is_person:
        return []
    return sorted({root for root, _ in _legacy_homes(c).values() if legacy_reader_only(c, v, {"id": root})})


# A list the cursor has finished; `next_cursor` is the threads' position, "|", the DMs' position.
_SIDEBAR_DONE = "-"


def _sidebar_positions(cursor):
    """(threads, DMs), each None to start, _SIDEBAR_DONE when finished or (place, seq, id). A cursor from before the lists
    were paged apart holds one position, and both lists rank by the same order, so it stands for each."""
    if not cursor:
        return None, None
    parts = str(cursor).split("|")
    if len(parts) == 1:
        parts *= 2
    if len(parts) != 2:
        raise Problem("cursor", "Unknown cursor", 422)
    out = []
    for part in parts:
        if part == _SIDEBAR_DONE:
            out.append(part)
            continue
        try:
            at_place, at_seq, at_id = part.split(":", 2)
            out.append((int(at_place), int(at_seq), at_id))
        except ValueError:
            raise Problem("cursor", "Unknown cursor", 422) from None
    return tuple(out)


def sidebar(c, v, *, cursor=None, limit=50):
    """The roots the viewer follows or belongs to, and the public ones, ranked before they are paged (spec §1): what
    needs their attention first, then what is unread for them, then the rest, each newest activity first. Following a
    sub-thread lists its root. A root row speaks for its whole subtree, for this viewer (`subtree_summaries`), and a
    sub-thread is never a row of its own. `threads` and `dms` are ranked and paged apart, up to `limit` rows each, so
    someone with many threads still gets their DMs: one query each, whatever the page holds. A task's thread is listed
    only while it has unread for the viewer (its parties and posters are no reason), so task threads never crowd out
    the rest. `attention` is the page's rows with an open Needs attention item. `cursor` is the `next_cursor` of the
    page before."""
    v = viewer(None, v) if not isinstance(v, Viewer) else v
    limit = max(1, min(int(limit or 50), MAX_PAGE))
    readable = readable_sql(c, v, "r")
    names = v.member_names(c)
    needing = _attention_roots(c, v)
    history = _history_only_roots(c, v)
    args = {"me": v.actor, "needing": json.dumps(sorted(needing)), "history": json.dumps(history),
            "fetch": limit * 3, **{f"name{i}": name for i, name in enumerate(names)}}
    marks = ",".join(f":name{i}" for i in range(len(names)))
    is_needing = "r.id IN (SELECT value FROM json_each(:needing))"
    is_history = "r.id IN (SELECT value FROM json_each(:history))"
    subtree, mark = _SUBTREE.format(me=":me"), _subtree_mark(c, v)
    activity = (f"COALESCE(CASE WHEN {is_history} THEN (SELECT MAX(h.seq) FROM messages h WHERE h.conversation_id=r.id "
                f"AND h.legacy_audience IS NOT NULL) ELSE (SELECT MAX(s.last_seq) FROM conversations s "
                f"WHERE s.root_id=r.id AND {_SUBTREE_OPEN}) END,0)")
    # unread_message_sql's two halves, each by its own index: a person's message past the mark, or a delivery still open
    # for the viewer. The counter leaves out every thread with neither.
    by_person = (f"EXISTS (SELECT 1 FROM {subtree}JOIN messages m INDEXED BY messages_thread_human ON "
                 f"m.conversation_id=s.id AND m.seq>{mark} WHERE s.root_id=r.id AND {_SUBTREE_OPEN} "
                 f"AND s.last_unread_seq>{mark} AND m.from_actor LIKE 'human:%' AND m.from_actor<>:me "
                 "AND m.deleted_at IS NULL)")
    to_viewer = ("r.id IN (SELECT s.root_id FROM message_deliveries d JOIN messages m ON m.id=d.message_id "
                 "JOIN conversations s ON s.id=d.thread_id LEFT JOIN thread_followers f ON f.thread_id=s.id "
                 "AND f.actor=d.recipient WHERE d.recipient=:me AND d.state IN ('pending','delivered','capped','held') "
                 f"AND {_SUBTREE_OPEN} AND m.seq>{mark} AND m.from_actor<>:me AND m.deleted_at IS NULL)")
    place = f"CASE WHEN {is_needing} THEN 0 WHEN NOT {is_history} AND ({by_person} OR {to_viewer}) THEN 1 ELSE 2 END"
    # A task's thread lists only while it has unread for the viewer: its parties (maker, requester, owner) and anyone who
    # posted in it are no reason on their own. The full Threads page, the Tasks page and search still show it.
    is_task = f"COALESCE(r.audience,{_derived_audience('r')})='task'"
    where = ["r.parent_id IS NULL", "r.archived_at IS NULL", "r.closed_at IS NULL",
             f"(NOT {is_task} OR {by_person} OR {to_viewer})",
             f"(r.audience IN ('company','team') OR r.owner_actor=:me OR {is_needing} OR EXISTS (SELECT 1 FROM "
             f"thread_members m WHERE m.thread_id=r.id AND m.actor IN ({marks})) OR EXISTS (SELECT 1 FROM "
             f"thread_followers f JOIN conversations s ON s.id=f.thread_id WHERE s.root_id=r.id AND f.actor=:me "
             f"AND f.following=1 AND {_SUBTREE_OPEN}) OR EXISTS (SELECT 1 FROM "
             f"{_PARTICIPANTS.format(r='r')} WHERE value=:me))", readable]
    positions = _sidebar_positions(cursor)
    is_dm = f"COALESCE(r.audience,{_derived_audience('r')}) IN ('personal','participants')"

    def section(test, position):
        """One list's rows, ranked and paged by its own position, and whether more follow."""
        if position == _SIDEBAR_DONE:
            return [], False
        after, at = "", {}
        if position:
            at_place, at_seq, at_id = position
            at = {"at_place": at_place, "at_seq": at_seq, "at_id": at_id}
            after = ("WHERE place>:at_place OR (place=:at_place AND (activity<:at_seq OR "
                     "(activity=:at_seq AND id<:at_id))) ")
        sql = (f"SELECT * FROM (SELECT r.*, {activity} AS activity, {place} AS place FROM conversations r WHERE "
               + " AND ".join([*where, test]) + f") {after}ORDER BY place, activity DESC, id DESC LIMIT :fetch")
        rows = []
        for r in c.execute(sql, {**args, **at}):
            r = dict(r)
            if not thread_readable(c, v, r["id"]):
                continue
            if len(rows) >= limit:
                return rows, True
            rows.append(r)
        return rows, False

    listed = [section(f"NOT {is_dm}", positions[0]), section(is_dm, positions[1])]
    every = [r for rows, _ in listed for r in rows]
    summaries = subtree_summaries(c, v, [r["id"] for r in every])
    dms = dm_with(c, v, every)

    def items_of(rows, more):
        """Each row as its list shows it, up to half a page's bytes, so a long list leaves the other room; and its next
        position."""
        found, budget = [], MAX_PAGE_BYTES // 2
        for r in rows:
            summary = summaries.get(r["id"]) or {"unread": 0, "activity": False}
            item = view(c, v, r, summary=summary, dm=dms.get(r["id"]))
            item["activity_seq"] = r["activity"]
            if needing.get(r["id"], {}).get("new_at"):
                item["new_at"] = needing[r["id"]]["new_at"]
            if r["id"] not in history and summary.get("last_message_at"):
                item["last_message_at"] = summary["last_message_at"]
            budget -= len(json.dumps(item, default=str))
            if budget < 0:
                more = True
                break
            found.append((r, item))
        tail = found[-1] if found else None
        return found, f"{tail[0]['place']}:{tail[1]['activity_seq']}:{tail[1]['id']}" if more and tail else _SIDEBAR_DONE

    (threads_found, threads_next), (dms_found, dms_next) = items_of(*listed[0]), items_of(*listed[1])
    attention = sorted(((r, item) for r, item in threads_found + dms_found if r["place"] == 0),
                       key=lambda pair: (pair[1]["activity_seq"], pair[1]["id"]), reverse=True)
    return {"attention": [{**needing[r["id"]], **item} for r, item in attention],
            "threads": [item for _, item in threads_found], "dms": [item for _, item in dms_found],
            "next_cursor": None if threads_next == dms_next == _SIDEBAR_DONE else f"{threads_next}|{dms_next}"}


# ----------------------------------------------------------------------------- outside destinations (other tracks)
def add_outside_delivery(c, thread_id, recipient, *, added_by=H.KEEPER):
    """Link an outside destination (`slack:<channel>`, `peer:<install>`) to a thread: every later message gets a
    delivery for it."""
    if not str(recipient).startswith(OUTSIDE_PREFIXES):
        raise Problem("recipient", "An outside destination is slack:<channel> or peer:<install>", 422)
    row = thread_row(c, thread_id)
    if row is None:
        raise Problem("not_found", "Thread not found", 404)
    now = _now()
    c.execute("INSERT OR IGNORE INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
              "VALUES(?,?,?,'member',?,?,?)", (_new_id(), row["id"], recipient, added_by, now, now))
    _event(c, added_by, "thread.outside_linked", row["id"], {"recipient": recipient})
    return {"thread_id": row["id"], "recipient": recipient}


def remove_outside_member(c, principal, thread_id, actor):
    """Take an outside destination (`slack:<channel>`, `peer:<install>`) off a thread: its member row goes and its
    unsent deliveries become `moot`. One check: Manage on the thread, or an admin (who runs the bridges); a system
    write (an actor string) is the keeper's only. Idempotent: a destination not linked returns removed False."""
    who, v = _actor_of(principal)
    recipient = str(actor or "")
    if not recipient.startswith(OUTSIDE_PREFIXES):
        raise Problem("recipient", "An outside destination is slack:<channel> or peer:<install>", 422)
    if v is not None:
        row = require(c, v, thread_id)
        if not (can_manage(c, v, row) or (v.is_person and v.admin(c))):
            raise Problem("forbidden", "Only someone who manages this thread, or an admin, can do that", 403)
    else:
        if who != H.KEEPER:
            raise Problem("forbidden", "Only the keeper changes a thread's outside links without a viewer", 403)
        row = thread_row(c, thread_id)
        if row is None:
            raise Problem("not_found", "Thread not found", 404)
    gone = c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor=?", (row["id"], recipient)).rowcount
    if gone:
        c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE thread_id=? AND recipient=? "
                  "AND state='pending'", (_now(), row["id"], recipient))
        _event(c, who, "thread.outside_removed", row["id"], {"recipient": recipient})
        _audit(c, who, "thread:" + row["id"], {"outside_member": recipient}, None)
    return {"thread_id": row["id"], "recipient": recipient, "removed": bool(gone)}


def outside_due(c, prefix, limit=50):
    """Outside deliveries due to send for one destination kind, oldest first."""
    if prefix not in OUTSIDE_PREFIXES and prefix + ":" not in OUTSIDE_PREFIXES:
        raise Problem("recipient", "slack: or peer:", 422)
    prefix = prefix if prefix.endswith(":") else prefix + ":"
    rows = c.execute("SELECT d.*, m.body, m.from_actor, m.origin_id, m.deleted_at, m.conversation_id "
                     "FROM message_deliveries d JOIN messages m ON m.id=d.message_id WHERE d.recipient LIKE ? "
                     "AND d.state='pending' AND (d.next_attempt_at IS NULL OR d.next_attempt_at<=?) "
                     "ORDER BY d.created_at LIMIT ?", (prefix + "%", _now(), max(1, min(int(limit), 500)))).fetchall()
    return [dict(r) for r in rows]


def settle_delivery(c, delivery_id, state, error=None):
    if state not in STATES:
        raise Problem("state", "Unknown delivery state", 422)
    now = _now()
    c.execute("UPDATE message_deliveries SET state=?, error=?, attempts=attempts+1, updated_at=?, "
              "delivered_at=CASE WHEN ?='delivered' THEN ? ELSE delivered_at END WHERE id=?",
              (state, error, now, state, now, delivery_id))
    return {"id": delivery_id, "state": state}


# ----------------------------------------------------------------------------- §4 migration
MIGRATION_PROGRESS_KEY = "threads-migration-progress"
MIGRATION_CHUNK = 20_000        # messages per transaction: the write lock is held for a chunk, never the whole backfill
MIGRATION_STEPS = ("seq", "origin", "restricted", "legacy", "deliveries")    # the per-message backfill, in order


def migrated(c):
    return c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (MIGRATION_KEY,)).fetchone() is not None


def migrate(c):
    """Once per install: every conversation gets its audience, root and path; every message its seq, origin and
    restricted flag; shared rooms become their bot's Home with their history keeping its readers. Recorded under
    registry_metadata `threads-migration`.

    Resumable. The per-message backfill runs in chunks of MIGRATION_CHUNK rows, each a transaction of its own that
    also records how far it got (`threads-migration-progress`), so a crash or a restart resumes there and the write
    lock is held for one chunk at a time. Each step is idempotent per row, so a chunk that ran before a crash runs
    again harmlessly. The message indexes are built after the backfill (they are skipped by ensure until then).
    Inside a caller's transaction the chunks join it, and the whole migration is one transaction, as before."""
    if migrated(c):
        return False
    _migrate(c)
    return True


@contextlib.contextmanager
def _chunk(c):
    """One piece of the migration as a savepoint: it commits when it is the outermost transaction (a server start),
    and joins the caller's transaction when there is one."""
    c.execute("SAVEPOINT threads_migration")
    try:
        yield
    except Exception:
        c.execute("ROLLBACK TO threads_migration")
        c.execute("RELEASE threads_migration")
        raise
    c.execute("RELEASE threads_migration")


def _progress(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (MIGRATION_PROGRESS_KEY,)).fetchone()
    return json.loads(row[0]) if row else {}


def _set_progress(c, step, rowid):
    c.execute("INSERT OR REPLACE INTO registry_metadata(key,value_json) VALUES(?,?)",
              (MIGRATION_PROGRESS_KEY, json.dumps({"step": step, "rowid": rowid})))


def rename_rule_reviews(c):
    """Rule-review threads made before the plain title (org_rule_check.REVIEW_TITLE) took the task's "Held: ..." or
    "Appeal: ..." title as their subject. Only the threads of tasks labelled as rule reviews change; any other held
    thread (a screening hold, say) keeps its title. Returns how many changed."""
    from .org_rule_check import REVIEW_LABEL, REVIEW_TITLE
    return c.execute("UPDATE conversations SET subject=?, updated_at=? WHERE task_id IN (SELECT tt.task_id FROM task_tags tt "
                     "JOIN tags g ON g.id=tt.tag_id WHERE g.key=?) AND (subject LIKE 'Held: %' OR subject LIKE 'Appeal: %')",
                     (REVIEW_TITLE, _now(), REVIEW_LABEL)).rowcount


def _migrate(c):
    now = _now()
    # Two open shared rooms for one bot (never written, but a Home is one per owner): the newest is the Home, the
    # others stay readable as they were, archived.
    with _chunk(c):
        c.execute("UPDATE conversations SET archived_at=? WHERE scope='shared' AND closed_at IS NULL AND archived_at IS NULL "
                  "AND COALESCE(room_key,'')<>'' AND id NOT IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY "
                  f"{_ROOM_BOT.format(r='conversations')} ORDER BY COALESCE(last_message_at,created) DESC, id DESC) AS n "
                  "FROM conversations WHERE scope='shared' AND closed_at IS NULL AND archived_at IS NULL) WHERE n=1)", (now,))
        # Audience and owner stay unset on other existing conversations: derived live from their columns. The open shared
        # room is its bot's Home, stored: everything new follows Home Read, its history keeps its readers (below).
        c.execute("UPDATE conversations SET root_id=COALESCE(root_id,id), path=COALESCE(path,'/'||id||'/'), "
                  "updated_at=COALESCE(updated_at,last_message_at,created)")
        _store_shared_homes(c)
        rename_rule_reviews(c)
    install = install_id(c)
    _backfill_messages(c, install, now)
    # Conversation counters read the stamped seqs; a temporary table of each conversation's last seq (one pass, no index
    # on messages needed), then one pass over the conversations.
    with _chunk(c):
        c.execute("INSERT INTO sequences(name,value) SELECT 'messages', COALESCE(MAX(seq),0) FROM messages WHERE true "
                  "ON CONFLICT(name) DO UPDATE SET value=MAX(value,excluded.value)")
    c.execute("DROP TABLE IF EXISTS temp.threads_migration_last")
    c.execute("CREATE TEMP TABLE threads_migration_last(conversation_id TEXT PRIMARY KEY, seq INTEGER NOT NULL)")
    c.execute("INSERT INTO threads_migration_last SELECT conversation_id, MAX(seq) FROM messages "
              "WHERE conversation_id IS NOT NULL GROUP BY conversation_id")
    with _chunk(c):
        c.execute("UPDATE conversations SET last_seq=COALESCE((SELECT seq FROM temp.threads_migration_last l "
                  "WHERE l.conversation_id=conversations.id),0)")
        c.execute("UPDATE conversations SET last_unread_seq=last_seq")
    c.execute("DROP TABLE temp.threads_migration_last")
    # The message indexes come last: built once over the backfilled rows instead of kept up through every update.
    for statement in _MESSAGE_INDEXES:
        with _chunk(c):
            c.execute(statement)
    with _chunk(c):
        c.execute("DELETE FROM registry_metadata WHERE key=?", (MIGRATION_PROGRESS_KEY,))
        c.execute("INSERT INTO registry_metadata(key,value_json) VALUES(?,?)",
                  (MIGRATION_KEY, json.dumps({"at": now, "seq": c.execute(
                      "SELECT value FROM sequences WHERE name='messages'").fetchone()[0]})))
    with _chunk(c):
        _neutral_titles_once(c)     # the backfill sets the flags that decide a sub-thread's title


def _backfill_messages(c, install, now):
    """The per-message steps, each over rowid chunks, resuming from the recorded progress."""
    top = c.execute("SELECT COALESCE(MAX(rowid),0) FROM messages").fetchone()[0]
    progress = _progress(c)
    step, low = progress.get("step", MIGRATION_STEPS[0]), progress.get("rowid", 0)
    if step not in MIGRATION_STEPS:             # every step ran; only the finishing steps were left
        return
    for name in MIGRATION_STEPS[MIGRATION_STEPS.index(step):]:
        start = low if name == step else 0
        while start < top:
            end = min(top, start + MIGRATION_CHUNK)
            with _chunk(c):
                c.execute(_STEP_SQL[name], _step_params(name, install, now, start, end))
                _set_progress(c, name, end)
            start = end
        with _chunk(c):
            following = MIGRATION_STEPS[MIGRATION_STEPS.index(name) + 1:][:1]
            _set_progress(c, following[0] if following else "done", 0)


def _step_params(name, install, now, low, high):
    if name == "origin":
        return (install, low, high)
    if name == "deliveries":
        return (now, low, high)
    return (low, high)


# The migration's rendering of _ref_strings/restricted_for: structure outside NESTED_REF_KEYS, or any string that
# names a task (a task: prefix, a task link or any task's id), restricts the message.
_NESTED_REFS_SQL = (
    "EXISTS (SELECT 1 FROM json_tree(refs_json) t WHERE t.fullkey<>'$' AND ("
    "(t.type IN ('object','array') AND NOT (t.fullkey IN ('$.origin','$.mentions','$.warnings') OR "
    "(t.path='$.warnings' AND t.type='object'))) OR "
    "(t.type='text' AND (t.atom LIKE 'task:%' OR t.atom LIKE '%#/task/%' OR t.atom LIKE '%/tasks/%' OR "
    "t.atom IN (SELECT id FROM tasks)))))")


_ORIGIN_SQL = ("UPDATE messages SET origin_id=lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-7'||"
               "substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(2)))||'-'||lower(hex(randomblob(6)))||'@'||? "
               "WHERE rowid>? AND rowid<=? AND origin_id IS NULL")
_RESTRICTED_SQL = (
    "UPDATE messages SET restricted=CASE WHEN COALESCE(in_reply_to,'')<>'' THEN 1 "
    f"WHEN conversation_id IN (SELECT id FROM conversations v WHERE {_derived_audience('v')} IN ('task','meeting') "
    "OR task_id IS NOT NULL) THEN 1 "
    "WHEN COALESCE(refs_json,'')<>'' AND (NOT json_valid(refs_json) OR json_type(refs_json)<>'object' OR "
    f"EXISTS (SELECT 1 FROM json_each(refs_json) WHERE key NOT IN {_qlist(sorted(SAFE_REF_KEYS))}) OR {_NESTED_REFS_SQL}) "
    "THEN 1 ELSE 0 END WHERE rowid>? AND rowid<=?")
_LEGACY_SQL = (
    f"UPDATE messages SET legacy_audience='shared:'||(SELECT {_ROOM_BOT.format(r='v')} FROM conversations v "
    "WHERE v.id=messages.conversation_id), restricted=1 WHERE rowid>? AND rowid<=? AND conversation_id IN "
    "(SELECT id FROM conversations WHERE scope='shared' AND COALESCE(room_key,'')<>'')")
_DELIVERIES_SQL = (
    "INSERT OR IGNORE INTO message_deliveries(id,message_id,recipient,thread_id,root_id,mode,reason,cause,"
    "state,revision,delivered_at,read_at,created_at,updated_at) SELECT lower(hex(randomblob(16))),m.id,"
    "m.to_actor,m.conversation_id,m.conversation_id,"
    "CASE WHEN COALESCE(json_extract(CASE WHEN json_valid(m.refs_json) THEN m.refs_json END,'$.quiet'),0) "
    "THEN 'defer' ELSE 'queue' END, CASE WHEN m.kind='ask' THEN 'ask' ELSE 'addressed' END,"
    "CASE WHEN m.from_actor LIKE 'bot:%' THEN 'bot' WHEN m.from_actor LIKE 'human:%' THEN 'human' "
    "ELSE 'keeper' END, CASE WHEN m.delivered_at IS NOT NULL THEN 'delivered' ELSE 'pending' END,0,"
    "m.delivered_at,m.read_at,m.created,? FROM messages m WHERE m.rowid>? AND m.rowid<=? "
    "AND COALESCE(m.to_actor,'')<>'' AND (m.delivered_at IS NULL OR m.read_at IS NULL) AND m.deleted_at IS NULL")
_STEP_SQL = {
    "seq": "UPDATE messages SET seq=rowid WHERE rowid>? AND rowid<=? AND seq IS NULL",
    "origin": _ORIGIN_SQL,
    "restricted": _RESTRICTED_SQL,
    "legacy": _LEGACY_SQL,
    "deliveries": _DELIVERIES_SQL,
}
_MESSAGE_INDEXES = tuple(s for s in INDEXES if " ON messages(" in s)


def _store_shared_homes(c, slug=None):
    """Each bot's open shared room becomes its Home: audience 'home', owned by the bot (unless it already has one)."""
    one = f" AND {_ROOM_BOT.format(r='s')}=?" if slug else ""
    c.execute(f"UPDATE conversations SET audience='home', owner_actor='bot:'||{_ROOM_BOT.format(r='conversations')} "
              f"WHERE id IN (SELECT s.id FROM conversations s WHERE s.scope='shared' AND COALESCE(s.room_key,'')<>'' "
              f"AND s.closed_at IS NULL AND s.archived_at IS NULL AND s.audience IS NULL{one} AND NOT EXISTS (SELECT 1 "
              f"FROM conversations h WHERE h.audience='home' AND h.closed_at IS NULL AND "
              f"h.owner_actor='bot:'||{_ROOM_BOT.format(r='s')}))", (slug,) if slug else ())


def _earlier(c, v, row):
    """`{"earlier": {"count": n}}` for a manager of a bot on its Home's root while shared-room history is still closed:
    what the control to open it needs, whether or not this manager already reads that history. Nobody else gets a count."""
    found = legacy_home(c, row["id"]) if row["id"] == (row.get("root_id") or row["id"]) else None
    owner = str((found or {}).get("owner") or "")
    if not owner.startswith("bot:") or not v.is_person:
        return {}
    from . import teammate_access
    if not teammate_access.can(c, v.who, "manage", owner, auth=v.auth):
        return {}
    count = c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND legacy_audience IS NOT NULL",
                      (row["id"],)).fetchone()[0]
    return {"earlier": {"count": count}} if count else {}


def open_earlier_messages(c, principal, slug):
    """A manager of the bot opens its Home's earlier (shared-room) messages to the Home's Read audience."""
    actor, v = _actor_of(principal)
    owner = "bot:" + H.actor_id("bot:" + str(slug))
    if v is not None:
        from . import teammate_access
        if not teammate_access.can(c, v.who, "manage", owner, auth=v.auth):
            raise Problem("forbidden", "Only whoever manages this bot opens its earlier messages", 403)
    n = c.execute("UPDATE messages SET legacy_audience=NULL WHERE legacy_audience=?", ("shared:" + str(slug),)).rowcount
    _event(c, actor, "thread.earlier_opened", owner, {"messages": n})
    _audit(c, actor, "home:" + owner, {"legacy_audience": "shared:" + str(slug)}, {"legacy_audience": None})
    return {"home": owner, "messages": n}


# ----------------------------------------------------------------------------- sessions (contract §8)
def audience_class(conv, message=None):
    """`private` for a members, personal, participants or meeting thread, or a legacy-audience input; else
    `public`. The runner keys a turn's session on it (clients/sessions.py)."""
    if not conv:
        return "public"
    if audience_of(conv) in PRIVATE_AUDIENCES:
        return "private"
    if message and message.get("legacy_audience"):
        return "private"
    return "public"


# A reader with no standing of its own: what it may read, every reader of the thread may.
_NO_STANDING = "bot:~audience"


def _narrower_than_thread(c, message):
    if message.get("legacy_audience"):
        return True
    if not message.get("restricted"):
        return False
    from . import task_privacy
    refs = H._json(message.get("refs_json"), {}) or {}
    if isinstance(refs, dict) and refs.get("carries"):
        source = c.execute("SELECT * FROM messages WHERE id=?", (refs["carries"],)).fetchone()
        if source and source["legacy_audience"]:
            return True
    return not task_privacy.message_readable(c, _NO_STANDING, message, current=True)


def mark_narrower(c, history):
    """Flag the messages of a public turn's page that some reader of the thread may not read (`audience_limited`): history
    kept for its old readers, or a message tied to a private task or a meeting. A public session is read back into a
    thread everyone there reads, so the runner never gives it these (contract section 8). The page itself stays whole:
    the runner's cursor is a message id on it."""
    if not history:
        return history
    marks = ",".join("?" * len(history))
    rows = {r["id"]: dict(r) for r in c.execute(f"SELECT * FROM messages WHERE id IN ({marks})", [m["id"] for m in history])}
    for message in history:
        stored = rows.get(message["id"])
        if stored and _narrower_than_thread(c, stored):
            message["audience_limited"] = True
    return history


def private_task_thread(c, conv):
    return bool(conv and conv.get("task_id") and H.task_private(c, H.task(c, conv["task_id"]) or {}))
