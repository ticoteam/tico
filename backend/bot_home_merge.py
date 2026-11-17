"""Where a person's old chat with a bot went: the Home the one-time move took it into (owner decision, Nov 3), read.

Before Homes, a person talked to a bot in a personal room (scope 'personal', kind 'chat', room_key the bot). Up to
v0.6.0-rc.1 each start moved those rooms into the bot's Home once and recorded each move in `home_merges`
(migration 0067). The move no longer runs: every database this release opens already had it. What it left is read here,
so a write that still names a moved room lands in the Home (`home_for`, `named_home`), a person's later chat with a
moved bot is the Home (`home_for_chat`), a task's history reads every Home its bot has had (`history_ids`), and a file
that kept its room's scope reads as the Home (`scope_home`, `file_in_room`). A bot with no `home_merges` rows, which
is every bot of a database made on 0.6, keeps its chats as they are, for one lookup.
"""

import os
import sqlite3

from . import hubdb as H


def home_for(c, conversation_id, *, create=True):
    """The Home a moved room's writes go to: its bot's open Home now (made again when the one it went into was archived
    since, unless `create` is False), else None. None for a room that did not move."""
    if not conversation_id:
        return None
    row = _moved(c, conversation_id)
    if row is None:
        return None
    if row[2] is None and row[3] is None:
        return row[0]
    from . import threads

    found = threads.home(c, "bot:" + row[1], create=create)
    return found["id"] if found else None


def home_for_chat(c, person, bot_ref, *, create=True):
    """The Home a person's chat with this bot goes into once the bot's chats have moved (rooms.personal_room, the chat and
    work rooms, a bot's message with no room named). Only a covered bot is one whose rooms the move took (it has home_merges
    rows): a bot the move never reached, and a fresh install's, keep their chats as they are, for one lookup. None where the
    move leaves the chat as it is: the Assistant and the Strategy Owner, a bot synced from an outside app, a person whose
    viewer is refused the Home, and a bot with no Home when `create` is False. A person with no viewer (no record in the
    registry) gets the Home, as the move took their chat there."""
    from . import threads

    bot = _covered_bot(c, str(bot_ref or "").strip().removeprefix("bot:"))
    if bot is None or {bot["id"], bot["slug"]} & _excluded_bots() or _synced(c, bot):
        return None
    owner = "bot:" + bot["id"]
    from . import read_cache

    found = read_cache.rows(
        c,
        "SELECT id FROM conversations WHERE audience='home' AND owner_id=? AND closed_at IS NULL "
        "AND archived_at IS NULL LIMIT 1",
        (owner,),
    )
    home_id = found[0][0] if found else None
    if home_id is None:
        home = threads.home(c, owner, create=create)  # a bot's shared room, or a new Home when `create`
        if home is None:
            return None
        home_id = home["id"]
    # An owner with no viewer (a human with no record in the registry) gets the Home too: the move took their old chat there,
    # so a new chat goes there as well and is never split. Only a viewer that is refused the Home keeps the chat where it
    # was. Any other actor with no viewer keeps the old answer, None.
    v = threads.actor_viewer(c, person)
    if v is None and not str(person).startswith("human:"):
        return None
    if v is not None and not threads.readable(c, v, home_id):
        return None
    return home_id


def chat_home_missing(c, person, bot_ref):
    """Whether a person's chat with this bot moved and has no Home to go into now: the bot's Home is archived, so the
    next chat makes it again (`home_for_chat` with create). False for a bot the move never reached (one lookup)."""
    return (
        _covered_bot(c, str(bot_ref or "").strip().removeprefix("bot:")) is not None
        and home_for_chat(c, person, bot_ref, create=False) is None
    )


def named_home(store, conversation_id):
    """The room a write that names `conversation_id` lands in, named before any check reads it: a moved room's Home,
    made first when the one it went into is archived, else the room itself. One read for a room that did not move."""
    if not conversation_id:
        return conversation_id
    with store.read() as c:
        if _moved(c, conversation_id) is None:
            return conversation_id
        home = home_for(c, conversation_id, create=False)
    if home is None:
        with store.transaction() as c:
            home = home_for(c, conversation_id)
    return home or conversation_id


def home_missing(c, conversation_id):
    """Whether a moved room has no open Home to write to: its Home is archived, and the next write makes the bot's Home
    again (`home_for` with create). False for a room that did not move."""
    return _moved(c, conversation_id) is not None and home_for(c, conversation_id, create=False) is None


def scope_home(c, conversation_id):
    """The conversation a file scoped to `conversation_id` is read in: the Home its room went into when the move took it
    (a file that kept its room's scope reads as the Home, `_home_file_moves`), else the conversation itself."""
    row = _moved(c, conversation_id) if conversation_id else None
    return row[0] if row else conversation_id


def file_in_room(c, scope, conversation_id):
    """Whether a file with this scope is in the conversation (visuals.attach): the scope names it, or names a room the
    move took into the same Home."""
    return (
        bool(conversation_id)
        and scope.startswith("conversation:")
        and scope_home(c, scope[13:]) == scope_home(c, conversation_id)
    )


def history_ids(c, conversation_id):
    """The conversations a room's history is read from, oldest first: the room, then every Home its bot has had, archived
    ones included (an archived Home keeps the writes it took, and the one after it takes the next), and the Home a write to
    the room lands in now, last. A room that did not move has only itself."""
    row = _moved(c, conversation_id)
    if row is None:
        return [conversation_id]
    current = home_for(c, conversation_id, create=False)
    older = [h for h in _bot_homes(c, row[1], row[0]) if h not in (conversation_id, current)]
    return [conversation_id] + older + ([current] if current else [])


def histories(c, conversation_ids):
    """{room id: history_ids(c, room id)} for many rooms: one read for the rooms that moved, and a room that did not
    move has only itself."""
    ids = list(dict.fromkeys(cid for cid in conversation_ids if cid))
    moved = set()
    for part in H.chunks(ids):
        try:
            moved |= {
                r[0]
                for r in c.execute(
                    f"SELECT conversation_id FROM home_merges WHERE conversation_id IN ({','.join('?' * len(part))})",
                    part,
                )
            }
        except sqlite3.OperationalError:  # a database from before migration 0067
            break
    return {cid: history_ids(c, cid) if cid in moved else [cid] for cid in ids}


def _bot_homes(c, bot_id, recorded):
    """Every Home of a bot, open or archived, oldest first: those stored under the bot's id or slug, the ones a moved room
    went into (home_merges), and the one recorded for this room."""
    bot = H.bot(c, bot_id) or {}
    owners = sorted({"bot:" + bot_id, "bot:" + (bot.get("slug") or bot_id)})
    return [
        r[0]
        for r in c.execute(
            f"SELECT id FROM conversations WHERE (audience='home' AND owner_id IN ({','.join('?' * len(owners))})) "
            "OR id IN (SELECT home_id FROM home_merges WHERE bot_id=?) OR id=? ORDER BY created_at, id",
            (*owners, bot_id, recorded),
        ).fetchall()
    ]


def _covered_bot(c, ref):
    """The bot this id or slug names, when the move took its rooms (home_merges has rows for it), else None: one lookup."""
    try:
        bot = H._one(
            c,
            "SELECT b.id, b.slug, EXISTS(SELECT 1 FROM home_merges m WHERE m.bot_id=b.id) AS covered "
            "FROM bots b WHERE b.id=? OR b.slug=? LIMIT 1",
            (ref, ref),
        )
    except sqlite3.OperationalError:  # a database from before migration 0067
        return None
    return bot if bot and bot.get("covered") else None


def _moved(c, conversation_id):
    """(home id, bot id, home closed_at, home archived_at) of a room that moved, else None."""
    try:
        return c.execute(
            "SELECT m.home_id, m.bot_id, h.closed_at, h.archived_at FROM home_merges m "
            "LEFT JOIN conversations h ON h.id=m.home_id WHERE m.conversation_id=?",
            (conversation_id,),
        ).fetchone()
    except sqlite3.OperationalError:  # a database from before migration 0067
        return None


def _excluded_bots(assistant=None):
    """The Assistant (no Home: its rooms are each person's own Assistant), and the Strategy Owner (no person chats with it)."""
    from .strategy_bot import SLUG

    name = assistant or os.environ.get("TICO_ASSISTANT_BOT", "").strip() or "assistant"
    return {name, SLUG}


def _synced(c, bot):
    """A bot synced from an outside app (Grok Bot, Dots, Muse): its sync reads and writes the person's own room."""
    from . import bot_settings as BS, read_cache

    found = read_cache.rows(
        c,
        f"SELECT {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bc.bot IN (?,?) LIMIT 1",
        (bot["id"], bot["slug"]),
    )
    row = found[0] if found else None
    config = H._json(row["config"], {}) if row else {}
    if not isinstance(config, dict):
        return False
    from .harnesses import EXTERNAL_HARNESSES, resolve_harness
    from .external_sync import HARNESSES

    return resolve_harness(config) in set(EXTERNAL_HARNESSES) | set(HARNESSES)
