"""Slack channels bots read become read-only thread links (D9-5).

A channel on the list in Settings > Tools > Slack with readers was read by the gateway's hourly readers' pass, which kept
a cursor per channel and reader in `slack_reads` and wrote a digest message. Now the channel links to a thread that only
takes in what Slack says (`slack_thread_links.read_only`), and each reader follows that thread at `digest`, so the
hourly bot digest (`thread_bot_digest.sweep`) wakes it.

For each listed channel with readers: the channel's open link is used when it has one (its thread gets the readers as
followers); otherwise a members thread named for the channel is made, owned by the person who last changed the entry
(else the company owner), with the readers as members and digest followers, and a `requested` read-only link the
gateway then checks. Then `slack_channels.readers` and `digest_hours` and the `slack_reads` table go. `slack_digests`
stays, read-only. Each step checks first, so a second run changes nothing.
"""

import json
import uuid
from datetime import datetime, timezone

OPEN = ("requested", "active", "suspended")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _id():
    return str(uuid.uuid4())


def _ids(raw):
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for item in value if isinstance(value, list) else []:
        slug = str(item or "").strip().removeprefix("bot:")
        if slug and slug not in out:
            out.append(slug)
    return out


def _owner(conn, updated_by):
    """The person the new thread and link belong to: whoever last changed the entry, else the company owner, else the
    roster's first person."""
    actor = str(updated_by or "")
    if actor.startswith("human:") and conn.execute("SELECT 1 FROM humans WHERE id=?", (actor[6:],)).fetchone():
        return actor
    row = conn.execute("SELECT value FROM registry_metadata WHERE key='owner'").fetchone()
    try:
        email = str(json.loads(row[0]).get("email") or "").strip().lower() if row else ""
    except (TypeError, ValueError, AttributeError):
        email = ""
    person = conn.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone() if email else None
    person = person or conn.execute("SELECT id FROM humans ORDER BY rowid LIMIT 1").fetchone()
    return "human:" + person[0] if person else ""


def _open_link(conn, key, channel_id):
    marks = ",".join("?" * len(OPEN))
    row = conn.execute(
        f"SELECT thread_id FROM slack_thread_links WHERE channel_ref=? AND state IN ({marks})", (key, *OPEN)
    ).fetchone()
    if not row and channel_id:
        row = conn.execute(
            "SELECT thread_id FROM slack_thread_links WHERE channel_id=? AND state IN ('active','suspended')",
            (channel_id,),
        ).fetchone()
    return row[0] if row else None


def _new_thread(conn, owner, title, now):
    tid = _id()
    conn.execute(
        "INSERT INTO conversations(id,kind,subject,task_id,participants,created_at,last_message_at,closed_at,"
        "scope,owner_id,room_key,audience,audience_group_id,parent_id,parent_message_id,root_id,path,"
        "last_seq,last_unread_seq,archived_at,updated_at) VALUES(?,'chat',?,NULL,?,?,NULL,NULL,'direct',?,NULL,"
        "'members',NULL,NULL,NULL,?,?,0,0,NULL,?)",
        (tid, title[:200], json.dumps([owner]), now, owner, tid, f"/{tid}/", now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
        "VALUES(?,?,?,'owner',?,?,?)",
        (_id(), tid, owner, owner, now, now),
    )
    # The owner reads it, but a channel's every message is not theirs to be told about: a joined row, not a follow.
    conn.execute(
        "INSERT OR IGNORE INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,"
        "updated_at) VALUES(?,?,?,0,'live','joined',0,?,?)",
        (_id(), tid, owner, now, now),
    )
    return tid


def _add_reader(conn, thread_id, bot, by, now):
    """The bot reads the thread (a member of a members thread, a participant of a public one) and follows it at
    digest."""
    actor = "bot:" + bot
    row = conn.execute("SELECT audience, participants, last_seq FROM conversations WHERE id=?", (thread_id,)).fetchone()
    if not row:
        return
    if row[0] in ("company", "team"):
        try:
            people = json.loads(row[1] or "[]")
        except (TypeError, ValueError):
            people = []
        if actor not in people:
            conn.execute(
                "UPDATE conversations SET participants=? WHERE id=?", (json.dumps(people + [actor]), thread_id)
            )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
            "VALUES(?,?,?,'member',?,?,?)",
            (_id(), thread_id, actor, by, now, now),
        )
    conn.execute(
        "INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,updated_at) "
        "VALUES(?,?,?,1,'digest','manual',?,?,?) ON CONFLICT(thread_id,actor) DO UPDATE SET following=1, "
        "notify='digest', reason='manual', updated_at=excluded.updated_at",
        (_id(), thread_id, actor, row[2] or 0, now, now),
    )


def up(conn):
    if "read_only" not in _columns(conn, "slack_thread_links"):
        conn.execute("ALTER TABLE slack_thread_links ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS slack_thread_links_ref ON slack_thread_links(channel_ref, state)")
    columns = _columns(conn, "slack_channels")
    if "readers" in columns:
        now = _now()
        for key, channel_id, name, raw, updated_by in conn.execute(
            "SELECT key, channel_id, name, readers, updated_by FROM slack_channels ORDER BY key"
        ).fetchall():
            bots = [b for b in _ids(raw) if conn.execute("SELECT 1 FROM bots WHERE id=?", (b,)).fetchone()]
            if not bots:
                continue
            thread_id, owner = _open_link(conn, key, channel_id), _owner(conn, updated_by)
            if thread_id is None:
                if not owner:
                    continue  # nobody to own the thread: an empty roster has no readers to keep either
                thread_id = _new_thread(conn, owner, "#" + (name or channel_id), now)
                conn.execute(
                    "INSERT INTO slack_thread_links (id,thread_id,channel_ref,channel_name,audience,state,history_state,"
                    "linked_by,read_only,created_at,updated_at) VALUES (?,?,?,?,'members','requested','off',?,1,?,?)",
                    (_id(), thread_id, key, name, owner, now, now),
                )
            for bot in bots:
                _add_reader(conn, thread_id, bot, owner, now)
        conn.execute("ALTER TABLE slack_channels DROP COLUMN readers")
    if "digest_hours" in columns:
        conn.execute("ALTER TABLE slack_channels DROP COLUMN digest_hours")
    conn.execute("DROP TABLE IF EXISTS slack_reads")
