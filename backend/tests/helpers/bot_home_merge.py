"""The one-time move of each person's personal chat with a bot into the bot's Home, as Tico ran it on each start up to
v0.6.0-rc.1, kept here to build a database as rc.1 left it (backend/bot_home_merge.py reads what it left). Its own
notes, from when it ran:

Everyone's old personal chat with a bot moves into that bot's Home (owner decision, Nov 3).

Before Homes, a person talked to a bot in a personal room: `conversations` scope 'personal', kind 'chat', room_key the
bot, owner the person, participants exactly that person and that bot. Each person's history with a bot sat in a room
only they read. The decision: those chats are the bot's work, so they join the bot's Home, read by whoever reads the
bot's Home. Nothing stays private.

`run_once` runs on every start (hubdb.migrate, after threads.migrate) until every bot has moved, recorded under
registry_metadata `bot-chats-to-home`. Per bot, in a transaction of its own (BEGIN IMMEDIATE, so the write lock is held
for one bot at a time):

1. The Home. The bot's open Home with the most messages (`audience='home'`, owned by `bot:<id>` or by `bot:<slug>`,
   the owner a migrated shared room got). Other open Homes of the bot with no messages are archived; a bot with two
   Homes that both have messages waits for a person to sort out (`pending`, tried again on the next start). A bot with
   no Home gets one made as `threads.home` makes it. A personal room is never turned into the Home: an old task's
   conversation_id may name it, and a thread a task names is that task's thread (`H.task_post_sql`), where a bot's posts
   reach people only by a mention.
2. The rooms. Each eligible room's messages move into the Home, keeping their ids and their global seqs, so the Home's
   timeline is everyone's history in the order it was written. A message keeps its legacy_audience (NULL for a
   personal room; a 'shared:' mark is never added), so the Home's own read rule is the one that applies. What is keyed
   by the room moves with it: deliveries, sub-threads (root, parent, path), follow-ups, routings, feedback, bot files,
   credential requests, deferrals, calls; a run still going there is granted the Home too (attempt_conversations). The
   keyed tables move in one statement each for the whole bot (`_move_keyed`), not one per room. A file scoped to its room
   takes the Home's scope too, unless the Home already has a file of that identity or another moved room's file took it
   first; that file keeps its room's scope and reads as the Home (`scope_home`), so no file is lost or merged.
3. Read marks. Nobody wakes to a flood: the moved messages are history (`refs.moved_home`, which the unread rule never
   counts), in the Home and in each moved sub-thread, for everyone who reads the Home; an old unread reply is not carried
   over (owner decision, Nov 3). A reader's mark moves up to what moved in, but never past an original Home message that
   is still unread for them, which stays unread (`_read_through`). The room's owner follows the Home as they followed the
   room.
4. The room is closed, its room_key becomes `<key>:home:<home id>` (as rooms.sync_shared_room archives old pairs), and
   `home_merges` records where it went (`home_merge_messages`, each message it carried), so it is auditable and can
   be undone by hand. A closed room is out of personal_room, `threads._open_dm` and every pair lookup. A write that
   still names it (an old task's room, a run that started before the move) lands in the Home (`home_for`), and so does
   a chat with the bot that opens later (`home_for_chat`: rooms.personal_room, the chat and work rooms, a bot's message
   with no room named). A task is never filed in a Home (rooms.task_conversation_id). A task's history is read from
   where it was written and from every Home its bot has had, archived ones included, the current one last (`history_ids`).

Left as they are: the Assistant's rooms (it has no Home), the Strategy Owner, the Librarian's "docs" rooms (its per-person
Q&A list reads them), Strategy rooms, task rooms, meetings, DMs (scope 'direct'), closed or archived rows, rows that are
not exactly one person and one bot, rooms of bots synced from an outside app (the sync reads the person's room), and
a room whose owner's viewer may not read the bot's Home (moving it would take their own history from them). An owner with no viewer (a human with no record in the registry) is not an exception: their room moves.

Not moved: tasks.conversation_id (see 1), chat goals and session epochs (a room's own state; a run in the Home starts
its own), history (events, changes, digests), and message-keyed rows (search, links, jobs), which follow the message.
"""

import json
import logging
from contextlib import contextmanager

from backend import rooms
from backend import hubdb as H
from backend.bot_home_merge import (  # noqa: F401  (the read side, under the names the tests use)
    _excluded_bots,
    _moved,
    _synced,
    chat_home_missing,
    file_in_room,
    history_ids,
    home_for,
    home_for_chat,
    home_missing,
    named_home,
    scope_home,
)

KEY = "bot-chats-to-home"
EVENT = "conversation.moved_to_home"
log = logging.getLogger("backend.bot_home_merge")

# Tables whose `column` names the room, moved to the Home with it. A table a release does not have is skipped.
KEYED = (
    ("thread_followups", "conversation_id"),
    ("feedbacks", "conversation_id"),
    ("bot_files", "conversation_id"),
    ("credential_requests", "conversation_id"),
    ("deferrals", "conversation_id"),
    ("calls", "thread_id"),
)
OPEN_ATTEMPTS = ("queued", "leased", "running", "input")


class Skip(Exception):
    """A bot left as it is for now, with the reason: a person has to sort it out, and the next start tries again."""


def done(c):
    return c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (KEY,)).fetchone() is not None


def run_once(c):
    """The move, once per install, after the threads migration. Returns its summary, or None when it had run already.
    A bot that fails, or waits on a person (`pending`), is rolled back and tried again on the next start; the others stay
    moved. The install is marked done only when no bot is left."""
    if not _table(c, "home_merges") or done(c):
        return None
    token = H.NO_TRIGGERS.set(True)  # a moved message is not news: no webhook or automation hears of it
    try:
        summary = merge(c)
    finally:
        H.NO_TRIGGERS.reset(token)
    if not summary["errors"] and not summary["pending"]:
        c.execute(
            "INSERT OR REPLACE INTO registry_metadata(key,value) VALUES(?,?)",
            (KEY, json.dumps({"at": H.now(), **{k: summary[k] for k in ("bots", "rooms", "messages")}})),
        )
    if summary["rooms"] or summary["skipped"] or summary["errors"]:
        log.info("bot chats to Home: %s", json.dumps(summary, sort_keys=True))
    return summary


def merge(c, *, assistant=None):
    """Move every eligible room into its bot's Home. Idempotent: a moved room is closed, so a second run finds none.
    Returns {bots, rooms, messages, homes_made, homes_archived, moot, quieted, kept_legacy, pending, skipped, errors};
    `pending` names the bots that wait on a person (a Skip)."""
    summary = {
        "bots": 0,
        "rooms": 0,
        "messages": 0,
        "homes_made": 0,
        "homes_archived": 0,
        "moot": 0,
        "quieted": 0,
        "kept_legacy": 0,
        "pending": [],
        "skipped": [],
        "errors": [],
    }
    excluded = _excluded_bots(assistant)
    found = _eligible(c, excluded)
    for bot_id in sorted(found):
        try:
            with _transaction(c):
                moved = _merge_bot(c, bot_id, found[bot_id], summary)
            if moved:
                summary["bots"] += 1
        except Skip as why:
            summary["pending"].append(bot_id)
            summary["skipped"].append({"bot": bot_id, "reason": str(why)})
        except Exception as error:  # one bot's failure must not stop the start or the other bots
            log.exception("bot chats to Home: %s failed", bot_id)
            summary["errors"].append({"bot": bot_id, "error": f"{type(error).__name__}: {error}"})
    return summary


def _rooms_sql():
    return (
        "SELECT id, owner_id, room_key, participants, created_at, last_seq, last_unread_seq, last_message_at "
        "FROM conversations WHERE scope='personal' AND kind='chat' AND task_id IS NULL AND parent_id IS NULL "
        "AND parent_message_id IS NULL AND closed_at IS NULL AND archived_at IS NULL "
        "AND COALESCE(audience,'personal')='personal' AND owner_id LIKE 'human:%' AND COALESCE(room_key,'')<>''"
    )


def _eligible(c, excluded):
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
            continue  # the Assistant's, Strategy's and the Librarian's docs rooms are keyed by their role
        if {bot["id"], bot["slug"]} & excluded or _synced(c, bot):
            continue
        found.setdefault(bot["id"], []).append(row)
    return found


# ----------------------------------------------------------------------------- one bot
def _merge_bot(c, bot_id, rooms, summary):
    from backend import threads

    bot = H.bot(c, bot_id)
    home = _home(c, bot, summary)
    moving = []
    for room in rooms:
        v = threads.actor_viewer(c, room["owner_id"])
        # Who reads the Home is its own rule: Read on the bot for a stored Home, the live shared-room rule for a
        # shared room that stores no audience (docs/dev/threads-contract.md section 7).
        if v is not None and not threads.readable(c, v, home["id"]):
            summary["skipped"].append({"bot": bot_id, "room": room["id"], "reason": "its owner may not read the Home"})
            continue
        moving.append(room)
    if not moving:
        return False
    _map_rooms(c, moving, home["id"])
    _mark_moved(c)
    row = threads.thread_row(c, home["id"])
    for room in moving:
        _move(c, room, row, bot, summary)
    _move_keyed(c, bot["id"])
    return True


def _home(c, bot, summary):
    """The bot's open Home: the one with the most messages; the others, empty, archived. Made when it has none."""
    from backend import threads

    actor = "bot:" + bot["id"]
    owners = sorted({actor, "bot:" + bot["slug"]})
    homes = [
        dict(r)
        for r in c.execute(
            "SELECT h.id, h.owner_id, h.created_at, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=h.id) AS n, "
            "(SELECT COUNT(*) FROM conversations s WHERE s.root_id=h.id AND s.id<>h.id) AS subs FROM conversations h "
            f"WHERE ((h.audience='home' AND h.owner_id IN ({','.join('?' * len(owners))})) "
            # A shared room a legacy writer opened after the threads migration stores no audience: its columns imply it.
            "OR (h.audience IS NULL AND h.scope='shared' AND h.room_key IN (?,?))) AND h.parent_id IS NULL "
            "AND h.closed_at IS NULL AND h.archived_at IS NULL",
            (*owners, bot["id"], bot["slug"]),
        )
    ]
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
def _map_rooms(c, rooms, hid):
    """The rooms this bot's move takes, with the Home each goes to, as a temp table for the bulk updates (`_move_keyed`)."""
    c.execute("CREATE TEMP TABLE IF NOT EXISTS home_moves(rid TEXT PRIMARY KEY, hid TEXT NOT NULL)")
    c.execute("DELETE FROM temp.home_moves")
    c.executemany("INSERT INTO temp.home_moves(rid, hid) VALUES(?,?)", [(room["id"], hid) for room in rooms])


def _home_file_moves(c, bot_id):
    """The moved rooms' files that take their Home's scope (temp.home_file_moves: id, scope, was), and an activity row
    for each that names the scope it had. An identity is unique within its scope (bot, scope, identity), so a file takes
    the Home's scope only when the Home has no file of that identity and it is the first, by when it was first active, of
    the moved files with that identity. Every other file keeps its room's scope; that scope still reads as the Home
    (`scope_home`)."""
    c.execute(
        "CREATE TEMP TABLE IF NOT EXISTS home_file_moves(id TEXT PRIMARY KEY, scope TEXT NOT NULL, was TEXT NOT NULL)"
    )
    c.execute("DELETE FROM temp.home_file_moves")
    c.execute(
        "INSERT INTO temp.home_file_moves(id, scope, was) "
        "SELECT id, scope, was FROM (SELECT f.id, 'conversation:' || m.hid AS scope, f.scope AS was, "
        "ROW_NUMBER() OVER (PARTITION BY m.hid, f.identity ORDER BY f.first_activity_at, f.id) AS rn, "
        "EXISTS (SELECT 1 FROM bot_files o WHERE o.bot_id=f.bot_id AND o.scope='conversation:' || m.hid "
        "AND o.identity=f.identity) AS taken "
        "FROM temp.home_moves m JOIN bot_files f ON f.bot_id=? AND f.scope='conversation:' || m.rid "
        "AND f.conversation_id=m.rid) WHERE rn=1 AND NOT taken",
        (bot_id,),
    )
    c.execute(
        "INSERT INTO bot_file_activity(file_id, actor, action, created_at, detail) "
        "SELECT id, ?, 'moved_to_home', ?, json_object('from', was, 'to', scope) FROM temp.home_file_moves",
        (H.KEEPER, H.now()),
    )


def _move_keyed(c, bot_id):
    """What is keyed by the moved rooms moves with them: one statement per table for the whole bot. Several of these
    tables have no index on the room column, so a statement per room would read each of them once per room. A file's
    scope moves in the same statement as its room (`_home_file_moves`)."""
    if _table(c, "bot_files"):
        _home_file_moves(c, bot_id)
    for table, column in KEYED:
        if _table(c, table):
            scope = (
                ", scope=COALESCE((SELECT s.scope FROM temp.home_file_moves s WHERE s.id=bot_files.id), scope)"
                if table == "bot_files"
                else ""
            )
            c.execute(
                f"UPDATE {table} SET {column}=(SELECT hid FROM temp.home_moves WHERE rid={table}.{column}){scope} "
                f"WHERE {column} IN (SELECT rid FROM temp.home_moves)"
            )
    if _table(c, "attempts"):
        c.execute(
            "INSERT OR IGNORE INTO attempt_conversations(attempt_id, conversation_id) "
            "SELECT ac.attempt_id, moves.hid FROM attempt_conversations ac "
            "JOIN temp.home_moves moves ON moves.rid=ac.conversation_id JOIN attempts a ON a.id=ac.attempt_id "
            f"WHERE a.state IN ({','.join('?' * len(OPEN_ATTEMPTS))})",
            OPEN_ATTEMPTS,
        )
    if _table(c, "thread_rankings"):
        c.execute("DELETE FROM thread_rankings WHERE thread_id IN (SELECT rid FROM temp.home_moves)")


def _mark_moved(c):
    """The messages of the bot's moved rooms, and of the sub-threads under them, are history (`refs.moved_home`): never
    unread, for anyone, whatever a reader's mark is. One statement for the whole bot (temp.home_moves)."""
    c.execute(
        "UPDATE messages SET refs=json_set(CASE WHEN json_valid(refs) THEN refs ELSE '{}' END, '$.moved_home', "
        "json('true')) WHERE conversation_id IN (SELECT rid FROM temp.home_moves) OR conversation_id IN "
        "(SELECT s.id FROM conversations s JOIN temp.home_moves m ON s.root_id=m.rid)"
    )


def _move(c, room, home, bot, summary):
    rid, hid, person = room["id"], home["id"], room["owner_id"]
    now = H.now()
    top = int(c.execute("SELECT COALESCE(MAX(seq),0) FROM messages WHERE conversation_id=?", (rid,)).fetchone()[0])

    # Deliveries that did not count where they were (a bot's post in a room an old task names, H.task_post_sql) must
    # not start counting in the Home: they would come back as Needs you. Those that counted and stop counting in a
    # Home a task names are only counted.
    room_task = _task_thread(c, rid)
    home_task = _task_thread(c, hid)
    bot_posts = (
        "FROM messages m WHERE m.conversation_id=? AND m.sender_id LIKE 'bot:%' AND m.kind IN ('say','notice','ask')"
    )
    addressed = (
        "recipient LIKE 'human:%' AND COALESCE(reason,'')<>'mention' AND state IN ('pending','delivered','read')"
    )
    if room_task and not home_task:
        summary["moot"] += c.execute(
            f"UPDATE message_deliveries SET state='moot', updated_at=? WHERE {addressed} AND message_id IN "
            f"(SELECT m.id {bot_posts})",
            (now, rid),
        ).rowcount
    elif home_task and not room_task:
        summary["quieted"] += c.execute(
            f"SELECT COUNT(*) FROM message_deliveries WHERE {addressed} AND message_id IN (SELECT m.id {bot_posts})",
            (rid,),
        ).fetchone()[0]

    c.execute(
        "INSERT OR IGNORE INTO home_merge_messages(message_id, conversation_id) "
        "SELECT id, ? FROM messages WHERE conversation_id=?",
        (rid, rid),
    )
    summary["kept_legacy"] += c.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND legacy_audience IS NOT NULL", (rid,)
    ).fetchone()[0]
    n = c.execute("UPDATE messages SET conversation_id=? WHERE conversation_id=?", (hid, rid)).rowcount
    c.execute("UPDATE message_deliveries SET thread_id=?, root_id=? WHERE thread_id=?", (hid, hid, rid))

    # Sub-threads keep their own rows, now under the Home.
    subs = [
        (r[0], int(r[1] or 0))
        for r in c.execute("SELECT id, last_seq FROM conversations WHERE root_id=? AND id<>?", (rid, rid))
    ]
    if subs:
        prefix = f"/{rid}/"
        c.execute(
            "UPDATE conversations SET root_id=?, parent_id=CASE WHEN parent_id=? THEN ? ELSE parent_id END, "
            "path=CASE WHEN substr(path,1,?)=? THEN ?||substr(path,?) ELSE path END, updated_at=? "
            "WHERE root_id=? AND id<>?",
            (hid, rid, hid, len(prefix), prefix, f"/{hid}/", len(prefix) + 1, now, rid, rid),
        )
        for sub, _ in subs:
            c.execute("UPDATE message_deliveries SET root_id=? WHERE thread_id=?", (hid, sub))

    # Read marks: the moved history is read for everyone who reads the Home, sub-threads included.
    if top:
        _read_through(c, hid, top, now)
    for sub, last in subs:
        if last:
            _read_through(c, sub, last, now)
    _follow_owner(c, rid, hid, person, now)
    c.execute(
        "UPDATE conversations SET last_seq=MAX(last_seq,?), last_unread_seq=MAX(last_unread_seq,?), "
        "last_message_at=NULLIF(MAX(COALESCE(last_message_at,''),?),''), updated_at=? WHERE id=?",
        (int(room["last_seq"] or 0), int(room["last_unread_seq"] or 0), room["last_message_at"] or "", now, hid),
    )
    c.execute(
        "UPDATE conversations SET closed_at=?, updated_at=?, room_key=? WHERE id=?",
        (now, now, f"{room['room_key']}:home:{hid}", rid),
    )
    c.execute(
        "INSERT INTO home_merges(conversation_id, home_id, bot_id, owner_id, room_key, messages_count, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (rid, hid, bot["id"], person, room["room_key"], n, now),
    )
    H.event(c, H.KEEPER, EVENT, rid, {"home": hid, "bot": bot["id"], "owner": person, "messages": n})
    summary["rooms"] += 1
    summary["messages"] += n


def _people(c):
    return ["human:" + row[0] for row in c.execute("SELECT id FROM humans ORDER BY id")]


def _read_through(c, thread_id, seq, now):
    """Every person's read mark in a thread goes up to `seq`, what moved in, which is history for them (its messages are
    never unread: refs.moved_home). But never past the first message still unread for them: an original message of the
    thread that was unread before the move stays unread, and so does everything after it. A person with no row gets one
    that is a read mark only, not a follow."""
    from backend import threads

    for actor in _people(c):
        v = threads.actor_viewer(c, actor)
        floor = threads._backfill_floor(c, v) if v is not None else 0
        row = c.execute(
            "SELECT last_read_seq FROM thread_followers WHERE thread_id=? AND actor=?", (thread_id, actor)
        ).fetchone()
        mark = max(int(row[0] or 0) if row else 0, floor)
        first = c.execute(
            f"SELECT MIN(m.seq) FROM messages m WHERE m.conversation_id=? AND m.seq>? "
            f"AND {threads.unread_message_sql()}",
            (thread_id, mark, actor, actor),
        ).fetchone()[0]
        target = seq if first is None else min(seq, int(first) - 1)
        if row is None:
            c.execute(
                "INSERT INTO thread_followers(id,thread_id,actor,following,notify,reason,last_read_seq,created_at,"
                "updated_at) VALUES(?,?,?,0,'live','read',?,?,?)",
                (threads._new_id(), thread_id, actor, target, now, now),
            )
        else:
            c.execute(
                "UPDATE thread_followers SET last_read_seq=MAX(last_read_seq,?), updated_at=? "
                "WHERE thread_id=? AND actor=?",
                (target, now, thread_id, actor),
            )


def _follow_owner(c, rid, hid, person, now):
    """The room's owner follows the Home as they followed the room. A row of their own choice in the Home stays as it is."""
    was = c.execute(
        "SELECT following, notify FROM thread_followers WHERE thread_id=? AND actor=?", (rid, person)
    ).fetchone()
    following, notify = (int(was[0]), was[1]) if was else (1, "live")
    c.execute(
        "UPDATE thread_followers SET following=?, notify=?, reason='joined', updated_at=? "
        "WHERE thread_id=? AND actor=? AND reason='read'",
        (following, notify, now, hid, person),
    )


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


def store(api):
    return api.app.state.store


def room(c, person, bot):
    """A person's personal room with a bot, as rooms.personal_room opened them before Homes (a legacy chat)."""
    return H.open_conversation(
        c,
        person,
        [person, "bot:" + bot],
        kind="chat",
        subject="Chat with " + bot,
        scope=rooms.PERSONAL,
        owner_actor=person,
        room_key=bot,
        allow_held=True,
    )["id"]


def merge_api(api):
    with store(api).transaction() as c:
        return merge(c)
