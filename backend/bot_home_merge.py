"""Everyone's old personal chat with a bot moves into that bot's Home (owner decision, Nov 3).

Before Homes, a person talked to a bot in a personal room: `conversations` scope 'personal', kind 'chat', room_key the
bot, owner the person, participants exactly that person and that bot. Each person's history with a bot sat in a room
only they read. The decision: those chats are the bot's work, so they join the bot's Home, read by whoever reads the
bot's Home. Nothing stays private.

`run_once` runs on every start (hubdb.migrate, after threads.migrate) until it has finished once, recorded under
registry_metadata `bot-chats-to-home`. Per bot, in a transaction of its own (BEGIN IMMEDIATE, so the write lock is held
for one bot at a time):

1. The Home. The bot's open Home with the most messages (`audience='home'`, owned by `bot:<id>` or by `bot:<slug>`,
   the owner a migrated shared room got). Other open Homes of the bot with no messages are archived; a bot with two
   Homes that both have messages is left for a person to sort out. A bot with no Home gets one made as
   `threads.home` makes it. A personal room is never turned into the Home: an old task's conversation_id may name it,
   and a thread a task names is that task's thread (`H.task_post_sql`), where a bot's posts reach people only by a
   mention.
2. The rooms. Each eligible room's messages move into the Home, keeping their ids and their global seqs, so the Home's
   timeline is everyone's history in the order it was written. A message keeps its legacy_audience (NULL for a
   personal room; a 'shared:' mark is never added), so the Home's own read rule is the one that applies. What is keyed
   by the room moves with it: deliveries, sub-threads (root, parent, path), follow-ups, routings, feedback, bot files,
   credential requests, deferrals, calls; a run still going there is granted the Home too (attempt_conversations).
3. Read marks. Nobody wakes to a flood: every person's mark in the Home goes past what moved in, except that an unread
   message a person had (in the Home, or in their own room) stays unread. The room's owner follows the Home as they
   followed the room.
4. The room is closed, its room_key becomes `<key>:home:<home id>` (as rooms.sync_shared_room archives old pairs), and
   `home_merges` records where it went (`home_merge_messages`, each message it carried), so it is auditable and can
   be undone by hand. A closed room is out of personal_room, `threads._open_dm` and every pair lookup, and a write that
   still names it (an old task's room, a run that started before the move) lands in the Home (`home_for`).

Left as they are: the Assistant's rooms (it has no Home), the Librarian's "docs" rooms (its per-person Q&A list reads
them), Strategy rooms, task rooms, meetings, DMs (scope 'direct'), closed or archived rows, rows that are not exactly
one person and one bot, rooms of bots synced from an outside app (the sync reads the person's room), federated rooms,
and a room whose owner may not read the bot's Home (moving it would take their own history from them).

Not moved: tasks.conversation_id (see 1), chat goals and session epochs (a room's own state; a run in the Home starts
its own), history (events, changes, digests), and message-keyed rows (search, links, jobs), which follow the message.
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager

from . import hubdb as H

KEY = "bot-chats-to-home"
EVENT = "conversation.moved_to_home"
log = logging.getLogger(__name__)

# Tables whose `column` names the room, moved to the Home with it. A table a release does not have is skipped.
KEYED = (("thread_followups", "conversation_id"), ("thread_routings", "thread_id"), ("feedbacks", "conversation_id"),
         ("bot_files", "conversation_id"), ("credential_requests", "conversation_id"),
         ("deferrals", "conversation_id"), ("calls", "thread_id"))
OPEN_ATTEMPTS = ("queued", "leased", "running", "input")


class Skip(Exception):
    """A bot left as it is, with the reason."""


def done(c):
    return c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (KEY,)).fetchone() is not None


def run_once(c):
    """The move, once per install, after the threads migration. Returns its summary, or None when it had run already.
    A bot that fails is rolled back and tried again on the next start; the others stay moved."""
    from . import threads
    if not threads.migrated(c) or not _table(c, "home_merges") or done(c):
        return None
    token = H.NO_TRIGGERS.set(True)      # a moved message is not news: no webhook or automation hears of it
    try:
        summary = merge(c)
    finally:
        H.NO_TRIGGERS.reset(token)
    if not summary["errors"]:
        c.execute("INSERT OR REPLACE INTO registry_metadata(key,value) VALUES(?,?)",
                  (KEY, json.dumps({"at": H.now(), **{k: summary[k] for k in ("bots", "rooms", "messages")}})))
    if summary["rooms"] or summary["skipped"] or summary["errors"]:
        log.info("bot chats to Home: %s", json.dumps(summary, sort_keys=True))
    return summary


def merge(c, *, assistant=None):
    """Move every eligible room into its bot's Home. Idempotent: a moved room is closed, so a second run finds none.
    Returns {bots, rooms, messages, homes_made, homes_archived, moot, quieted, kept_legacy, skipped, errors}."""
    summary = {"bots": 0, "rooms": 0, "messages": 0, "homes_made": 0, "homes_archived": 0, "moot": 0, "quieted": 0,
               "kept_legacy": 0, "skipped": [], "errors": []}
    excluded = _excluded_bots(assistant)
    for bot_id in sorted(_candidates(c, excluded)):
        try:
            with _transaction(c):
                moved = _merge_bot(c, bot_id, excluded, summary)
            if moved:
                summary["bots"] += 1
        except Skip as why:
            summary["skipped"].append({"bot": bot_id, "reason": str(why)})
        except Exception as error:  # noqa: BLE001 - one bot's failure must not stop the start or the other bots
            log.exception("bot chats to Home: %s failed", bot_id)
            summary["errors"].append({"bot": bot_id, "error": f"{type(error).__name__}: {error}"})
    return summary


def home_for(c, conversation_id):
    """The Home a moved room went into (its bot's open Home now, if that one was archived since), else None."""
    if not conversation_id:
        return None
    try:
        row = c.execute("SELECT m.home_id, m.bot_id, h.closed_at, h.archived_at FROM home_merges m "
                        "LEFT JOIN conversations h ON h.id=m.home_id WHERE m.conversation_id=?",
                        (conversation_id,)).fetchone()
    except sqlite3.OperationalError:      # a database from before migration 0057
        return None
    if row is None:
        return None
    if row[2] is None and row[3] is None:
        return row[0]
    from . import threads
    found = threads.home(c, "bot:" + row[1], create=False)
    return found["id"] if found else None


# ----------------------------------------------------------------------------- which rooms
def _excluded_bots(assistant=None):
    """The Assistant (no Home: its rooms are each person's own Assistant)."""
    name = assistant or os.environ.get("TICO_ASSISTANT_BOT", "").strip() or "coo"
    return {name}


def _rooms_sql():
    return ("SELECT id, owner_id, room_key, participants, created_at, last_seq, last_unread_seq, last_message_at "
            "FROM conversations WHERE scope='personal' AND kind='chat' AND task_id IS NULL AND parent_id IS NULL "
            "AND parent_message_id IS NULL AND closed_at IS NULL AND archived_at IS NULL "
            "AND COALESCE(audience,'personal')='personal' AND owner_id LIKE 'human:%' AND COALESCE(room_key,'')<>''")


def _eligible(c, excluded, bot_id=None):
    """{bot id: [room rows]} of the rooms that move, oldest first."""
    found, bots = {}, {}
    sql = _rooms_sql() + " ORDER BY created_at, id"
    for row in c.execute(sql).fetchall():
        row = dict(row) if not isinstance(row, dict) else row
        people = H._json(row["participants"], []) or []
        if not isinstance(people, list) or len(people) != 2 or len(set(people)) != 2 or row["owner_id"] not in people:
            continue
        other = next(p for p in people if p != row["owner_id"])
        if not str(other).startswith("bot:"):
            continue
        if other not in bots:
            bots[other] = H.resolve_bot(c, other[4:])
        bot = bots[other]
        if not bot or row["room_key"] not in (bot["id"], bot["slug"]):
            continue          # the Assistant's, Strategy's and the Librarian's docs rooms are keyed by their role
        if bot_id is not None and bot["id"] != bot_id:
            continue
        if {bot["id"], bot["slug"]} & excluded or _synced(c, bot) or _federated(c, row["id"]):
            continue
        found.setdefault(bot["id"], []).append(row)
    return found


def _candidates(c, excluded):
    return set(_eligible(c, excluded))


def _synced(c, bot):
    """A bot synced from an outside app (Grok Bot, Dots, Muse): its sync reads and writes the person's own room."""
    row = c.execute("SELECT config FROM bot_config WHERE bot IN (?,?) LIMIT 1", (bot["id"], bot["slug"])).fetchone()
    config = H._json(row[0], {}) if row else {}
    if not isinstance(config, dict):
        return False
    if config.get("app_delivery"):
        return True
    from .harnesses import EXTERNAL_HARNESSES, resolve_harness
    from .external_sync import HARNESSES
    return resolve_harness(config) in set(EXTERNAL_HARNESSES) | set(HARNESSES)


def _federated(c, thread_id):
    for table in ("federation_threads", "federation_members"):
        if _table(c, table) and c.execute(f"SELECT 1 FROM {table} WHERE thread_id=? LIMIT 1", (thread_id,)).fetchone():
            return True
    return False


# ----------------------------------------------------------------------------- one bot
def _merge_bot(c, bot_id, excluded, summary):
    from . import threads
    rooms = _eligible(c, excluded, bot_id).get(bot_id) or []
    if not rooms:
        return False
    bot = H.bot(c, bot_id)
    home = _home(c, bot, summary)
    if _federated(c, home["id"]):
        raise Skip("its Home is linked to another install")
    moved = 0
    for room in rooms:
        v = threads.actor_viewer(c, room["owner_id"])
        # Who reads the Home is its own rule: Read on the bot for a stored Home, the live shared-room rule for a
        # shared room that stores no audience (docs/dev/threads-contract.md section 7).
        if v is not None and not threads.readable(c, v, home["id"]):
            summary["skipped"].append({"bot": bot_id, "room": room["id"], "reason": "its owner may not read the Home"})
            continue
        _move(c, room, threads.thread_row(c, home["id"]), bot, summary)
        moved += 1
    return moved > 0


def _home(c, bot, summary):
    """The bot's open Home: the one with the most messages; the others, empty, archived. Made when it has none."""
    from . import threads
    actor = "bot:" + bot["id"]
    owners = sorted({actor, "bot:" + bot["slug"]})
    homes = [dict(r) for r in c.execute(
        "SELECT h.id, h.owner_id, h.created_at, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=h.id) AS n, "
        "(SELECT COUNT(*) FROM conversations s WHERE s.root_id=h.id AND s.id<>h.id) AS subs FROM conversations h "
        f"WHERE ((h.audience='home' AND h.owner_id IN ({','.join('?' * len(owners))})) "
        # A shared room a legacy writer opened after the threads migration stores no audience: its columns imply it.
        "OR (h.audience IS NULL AND h.scope='shared' AND h.room_key IN (?,?))) AND h.parent_id IS NULL "
        "AND h.closed_at IS NULL AND h.archived_at IS NULL", (*owners, bot["id"], bot["slug"]))]
    if not homes:
        made = threads.home(c, actor, create=True)
        summary["homes_made"] += 1
        return made
    homes.sort(key=lambda h: (-int(h["n"]), str(h["created_at"] or ""), h["id"]))
    pick, extra = homes[0], homes[1:]
    if any(int(h["n"]) or int(h["subs"]) for h in extra):
        raise Skip("two open Homes have messages: " + ", ".join(h["id"] for h in homes))
    now = H.now()
    for h in extra:
        c.execute("UPDATE conversations SET archived_at=?, updated_at=? WHERE id=?", (now, now, h["id"]))
        H.event(c, H.KEEPER, "conversation.archived_for_home_merge", h["id"], {"home": pick["id"], "bot": bot["id"]})
        summary["homes_archived"] += 1
    found = threads.home(c, actor, create=False)
    if not found or found["id"] != pick["id"]:
        # The Home this bot is found by (`threads.home`) must be the one everything moves into.
        c.execute("UPDATE conversations SET owner_id=?, updated_at=? WHERE id=?", (actor, now, pick["id"]))
        found = threads.home(c, actor, create=False)
        if not found or found["id"] != pick["id"]:
            raise Skip(f"threads.home finds {found and found['id']}, not {pick['id']}")
    return found


# ----------------------------------------------------------------------------- one room
def _move(c, room, home, bot, summary):
    rid, hid, person = room["id"], home["id"], room["owner_id"]
    now = H.now()
    top = int(c.execute("SELECT COALESCE(MAX(seq),0) FROM messages WHERE conversation_id=?", (rid,)).fetchone()[0])
    marks = _marks(c, rid, hid, person, top)

    # Deliveries that did not count where they were (a bot's post in a room an old task names, H.task_post_sql) must
    # not start counting in the Home: they would come back as Needs you. Those that counted and stop counting in a
    # Home a task names are only counted.
    room_task = _task_thread(c, rid)
    home_task = _task_thread(c, hid)
    bot_posts = ("FROM messages m WHERE m.conversation_id=? AND m.sender_id LIKE 'bot:%' "
                 "AND m.kind IN ('say','notice','ask')")
    addressed = ("recipient LIKE 'human:%' AND COALESCE(reason,'')<>'mention' "
                 "AND state IN ('pending','delivered','read')")
    if room_task and not home_task:
        summary["moot"] += c.execute(
            f"UPDATE message_deliveries SET state='moot', updated_at=? WHERE {addressed} AND message_id IN "
            f"(SELECT m.id {bot_posts})", (now, rid)).rowcount
    elif home_task and not room_task:
        summary["quieted"] += c.execute(
            f"SELECT COUNT(*) FROM message_deliveries WHERE {addressed} AND message_id IN (SELECT m.id {bot_posts})",
            (rid,)).fetchone()[0]

    c.execute("INSERT OR IGNORE INTO home_merge_messages(message_id, conversation_id) "
              "SELECT id, ? FROM messages WHERE conversation_id=?", (rid, rid))
    summary["kept_legacy"] += c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? "
                                        "AND legacy_audience IS NOT NULL", (rid,)).fetchone()[0]
    n = c.execute("UPDATE messages SET conversation_id=? WHERE conversation_id=?", (hid, rid)).rowcount
    c.execute("UPDATE message_deliveries SET thread_id=?, root_id=? WHERE thread_id=?", (hid, hid, rid))

    # Sub-threads keep their own rows, now under the Home.
    subs = [r[0] for r in c.execute("SELECT id FROM conversations WHERE root_id=? AND id<>?", (rid, rid))]
    if subs:
        prefix = f"/{rid}/"
        c.execute("UPDATE conversations SET root_id=?, parent_id=CASE WHEN parent_id=? THEN ? ELSE parent_id END, "
                  "path=CASE WHEN substr(path,1,?)=? THEN ?||substr(path,?) ELSE path END, updated_at=? "
                  "WHERE root_id=? AND id<>?",
                  (hid, rid, hid, len(prefix), prefix, f"/{hid}/", len(prefix) + 1, now, rid, rid))
        for sub in subs:
            c.execute("UPDATE message_deliveries SET root_id=? WHERE thread_id=?", (hid, sub))

    for table, column in KEYED:
        if _table(c, table):
            c.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (hid, rid))
    if _table(c, "attempts"):
        c.execute("INSERT OR IGNORE INTO attempt_conversations(attempt_id, conversation_id) SELECT ac.attempt_id, ? "
                  "FROM attempt_conversations ac JOIN attempts a ON a.id=ac.attempt_id WHERE ac.conversation_id=? "
                  f"AND a.state IN ({','.join('?' * len(OPEN_ATTEMPTS))})", (hid, rid, *OPEN_ATTEMPTS))
    if _table(c, "thread_rankings"):
        c.execute("DELETE FROM thread_rankings WHERE thread_id=?", (rid,))

    _follow(c, rid, hid, person, marks, now)
    c.execute("UPDATE conversations SET last_seq=MAX(last_seq,?), last_unread_seq=MAX(last_unread_seq,?), "
              "last_message_at=NULLIF(MAX(COALESCE(last_message_at,''),?),''), updated_at=? WHERE id=?",
              (int(room["last_seq"] or 0), int(room["last_unread_seq"] or 0), room["last_message_at"] or "", now, hid))
    c.execute("UPDATE conversations SET closed_at=?, updated_at=?, room_key=? WHERE id=?",
              (now, now, f"{room['room_key']}:home:{hid}", rid))
    c.execute("INSERT INTO home_merges(conversation_id, home_id, bot_id, owner_id, room_key, messages_count, created_at) "
              "VALUES(?,?,?,?,?,?,?)", (rid, hid, bot["id"], person, room["room_key"], n, now))
    H.event(c, H.KEEPER, EVENT, rid, {"home": hid, "bot": bot["id"], "owner": person, "messages": n})
    summary["rooms"] += 1
    summary["messages"] += n


def _marks(c, rid, hid, person, top):
    """{person: new read mark in the Home}, computed before the move: past what moves in (seq <= `top`), but short of
    the first message a person had unread, in the Home or (for its owner) in the room."""
    from . import threads
    if not top:
        return {}
    floor = _floor(c)
    unread = threads.unread_message_sql("m")
    first_sql = f"SELECT MIN(m.seq) FROM messages m WHERE m.conversation_id=? AND m.seq>? AND {unread}"
    out = {}
    for (actor,) in c.execute("SELECT 'human:'||id FROM humans ORDER BY id").fetchall():
        mark = max(_mark(c, hid, actor), floor)
        if top <= mark:
            continue
        first = [c.execute(first_sql, (hid, mark, actor, actor)).fetchone()[0]]
        if actor == person:
            first.append(c.execute(first_sql, (rid, max(_mark(c, rid, actor), floor), actor, actor)).fetchone()[0])
        first = [int(s) for s in first if s is not None]
        target = min([top] + [s - 1 for s in first])
        if target > mark:
            out[actor] = target
    return out


def _follow(c, rid, hid, person, marks, now):
    """Write the read marks; the room's owner follows the Home as they followed the room."""
    from . import threads
    was = c.execute("SELECT following, notify FROM thread_followers WHERE thread_id=? AND actor=?",
                    (rid, person)).fetchone()
    following, notify = (int(was[0]), was[1]) if was else (1, "live")
    for actor in sorted(set(marks) | {person}):
        mark = int(marks.get(actor) or 0)
        row = c.execute("SELECT reason FROM thread_followers WHERE thread_id=? AND actor=?", (hid, actor)).fetchone()
        owner = actor == person
        if row is None:
            if not H.human(c, actor[6:]):
                continue          # someone who has left: nothing to mark
            c.execute("INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,"
                      "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (threads._new_id(), hid, actor, following if owner else 0, notify if owner else "live",
                       "joined" if owner else "read", mark, now, now))
            continue
        # A row of ours (a read mark, `reason` 'read') becomes the owner's follow; a person's own choice stays.
        c.execute("UPDATE thread_followers SET last_read_seq=MAX(last_read_seq,?), "
                  "following=CASE WHEN ? AND reason='read' THEN ? ELSE following END, "
                  "notify=CASE WHEN ? AND reason='read' THEN ? ELSE notify END, "
                  "reason=CASE WHEN ? AND reason='read' THEN 'joined' ELSE reason END, updated_at=? "
                  "WHERE thread_id=? AND actor=?",
                  (mark, owner, following, owner, notify, owner, now, hid, actor))


def _mark(c, thread_id, actor):
    row = c.execute("SELECT last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?",
                    (thread_id, actor)).fetchone()
    return int(row[0] or 0) if row else 0


def _floor(c):
    from . import threads
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (threads.MIGRATION_KEY,)).fetchone()
    return int((H._json(row[0], {}) or {}).get("seq") or 0) if row else 0


def _task_thread(c, thread_id):
    return c.execute("SELECT 1 FROM tasks WHERE conversation_id=? LIMIT 1", (thread_id,)).fetchone() is not None


def _table(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


@contextmanager
def _transaction(c):
    """One bot's move: its own transaction (the write lock held for that bot only), or a savepoint inside a caller's."""
    if c.in_transaction:
        c.execute("SAVEPOINT bot_home_merge")
        try:
            yield
        except BaseException:
            c.execute("ROLLBACK TO bot_home_merge")
            c.execute("RELEASE bot_home_merge")
            raise
        c.execute("RELEASE bot_home_merge")
        return
    c.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")
