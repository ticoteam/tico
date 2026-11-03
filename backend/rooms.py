"""Conversation-mode policy for private assistant rooms and shared bot rooms."""

import json

from . import bot_access as A
from . import read_cache
from .store import H, P, Problem, encode


PERSONAL = "personal"
SHARED = "shared"
# The one private room each person has with the Assistant (backend/assistant.py); distinct from the
# room the person's tasks for the assistant bot land in, so its history is only what they said to it.
ASSISTANT_ROOM = "assistant"
# The owner's earlier chat with the Strategy Bot (backend/strategy_bot.py), from when the owner had it in place of an
# Assistant room: one per company, belonging to the owner seat, history now (read-only under "Earlier chats",
# backend/assistant.py). No new message goes to it; a transfer of ownership still moves it to the new owner. Its stored
# value stays "ceo": the rows stored so far carry it.
STRATEGY_ROOM = "ceo"
# The one private room each person has with the Librarian (backend/librarian.py): their Ask the Librarian questions.
DOCS_ROOM = "docs"


def session_config(conversation, bot, config):
    """The config a turn is handed. In the owner's earlier Strategy Bot chat (a turn queued there before it became
    history) it keys its session on that conversation
    (clients/sessions.py, `session: task`), so the owner's chat is one session and the Strategy Bot's goals session (its
    regular checks and goal work) another, and neither shares the other's context. The bot's own `session` setting
    never applies here: `session: bot` would put the owner's chat on the thread its goals session and other bots'
    messages use. Every runner reads `session` from the turn it is handed, so none needs to know this room."""
    from .strategy_bot import SLUG
    if (bot == SLUG and conversation and conversation.get("scope") == PERSONAL
            and conversation.get("room_key") == STRATEGY_ROOM):
        return {**(config or {}), "session": "task"}
    return config


def roster(c, shared=False):
    return read_cache.value(c, ("roster",), lambda: _roster(c), shared)


def _roster(c):
    row = read_cache.metadata(c, "people")
    return P.load(json.loads(row[0])) if row else P.load({"people": H.humans(c)})


def entries(c, shared=False):
    return read_cache.value(c, ("room_entries",), lambda: _entries(c), shared)


def _entries(c):
    result = {}
    slugs = {row["id"]: row["slug"] for row in c.execute("SELECT id,slug FROM bots")}
    for row in read_cache.configs(c):
        config = json.loads(row["config"] or "{}")
        config.update({"description": row["description"] or "", "reports_to": row["reports_to"],
                       "repo": row["repo"] or ("emp-" + slugs.get(row["bot"], row["bot"]))})
        if row["thread_mode"]:
            config["thread_mode"] = row["thread_mode"]
        if row["owner_ids"] is not None:
            config["owner_ids"] = json.loads(row["owner_ids"])
        result[row["bot"]] = config
    return result


def thread_mode(c, bot, row=None):
    """The main assistant is structurally personal; CPO is shared before registry republish.
    `row` is the bot's bot_config row when the caller has read it already."""
    if bot == "coo":
        return PERSONAL
    row = row or c.execute("SELECT thread_mode,config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    config = json.loads(row["config"] or "{}") if row else {}
    value = str((row["thread_mode"] if row else None) or config.get("thread_mode") or "").strip().lower()
    if value in (PERSONAL, SHARED):
        return value
    return SHARED if bot == "cpo" else PERSONAL


def shared_member_ids(c, auth, bot):
    """The bot's explicit/primary users plus the company owner, of whom only those who may read
    the bot: a shared room is its activity (docs/permissions.md)."""
    people, configs = roster(c), entries(c)
    members = {p["id"] for p in P.primary_users(bot, people, configs)}
    stored = c.execute("SELECT access FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if stored and not A.document(stored["access"])["read"]["everyone"]:
        members = {pid for pid in members if auth.person_can(c, "human:" + pid, bot, "read")}
    owner_email = auth.owner_email
    owner = next((p for p in people["people"] if str(p.get("email") or "").lower() == owner_email), None)
    if owner:
        members.add(owner["id"])
    return members


def shared_member(c, auth, actor, bot):
    return bot in shared_member_bots(c, auth, actor, [bot])


def shared_member_bots(c, auth, actor, slugs):
    """The slugs in `slugs` whose shared room `actor` is a member of: `shared_member_ids` answered for
    `actor` alone, for every bot at once. The roster and configs are read once (not per bot), the access rows
    in one statement, and the read check runs only for `actor`, only where the bot's read is not open to everyone."""
    slugs = list(dict.fromkeys(slugs))
    if not slugs or not str(actor).startswith("human:"):
        return set()
    actor_id = H.actor_id(actor)
    # Read only below: the snapshot's own roster and configurations, never copied (read_cache.value).
    people, configs = roster(c, shared=True), entries(c, shared=True)
    owner_email = auth.owner_email
    owner = next((p["id"] for p in people["people"] if str(p.get("email") or "").lower() == owner_email), None)
    stored = {}
    for start in range(0, len(slugs), 500):
        part = slugs[start:start + 500]
        stored.update({row["bot"]: row["access"] for row in c.execute(
            "SELECT bot,access FROM bot_config WHERE bot IN (" + ",".join("?" * len(part)) + ")", part)})
    found, needs_read = set(), []
    for slug in slugs:
        if owner == actor_id:
            found.add(slug)
            continue
        if actor_id not in {p["id"] for p in P.primary_users(slug, people, configs)}:
            continue
        if slug in stored and not A.document(stored[slug])["read"]["everyone"]:
            needs_read.append(slug)
        else:
            found.add(slug)
    if needs_read:
        try:
            who = auth.identity_for_actor(c, "human:" + actor_id)
        except Problem:
            who = None
        if who is not None:
            access = auth.bot_accesses(c, who, needs_read)
            found.update(slug for slug in needs_read if access[slug]["read"])
    return found


def room_bot(conversation):
    room = str(conversation.get("room_key") or "")
    if conversation.get("scope") == SHARED and room:
        return room.split(":", 1)[0]
    return next((H.actor_id(p) for p in conversation.get("participants", [])
                 if str(p).startswith("bot:")), None)


def personal_room(c, human_actor, bot="coo", subject="Private control room"):
    row = c.execute(
        "SELECT id FROM conversations WHERE scope='personal' AND owner_id=? AND room_key=? "
        "AND closed_at IS NULL ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1",
        (human_actor, bot)).fetchone()
    if row:
        return H.conversation(c, row["id"])

    # Adopt an open legacy pair instead of splitting the same person's history.
    pair = {human_actor, "bot:" + bot}
    for candidate in H.conversations_for(c, human_actor, limit=500):
        if (candidate["kind"] == "chat" and not candidate.get("task_id") and not candidate["closed_at"]
                and set(candidate["participants"]) == pair):
            c.execute("UPDATE conversations SET scope='personal',owner_id=?,room_key=? WHERE id=?",
                      (human_actor, bot, candidate["id"]))
            return H.conversation(c, candidate["id"])
    # A person's own room opens even while the bot is paused: their message waits for it (hubdb.say).
    return H.open_conversation(c, human_actor, [human_actor, "bot:" + bot], kind="chat",
                               subject=subject, scope=PERSONAL,
                               owner_actor=human_actor, room_key=bot, allow_held=H.is_human(human_actor))


def sync_shared_room(c, auth, bot, actor=None, create=False):
    members = shared_member_ids(c, auth, bot)
    if actor and str(actor).startswith("human:"):
        if H.actor_id(actor) not in members:
            raise Problem("forbidden", "You are not a member of this shared bot room", 403)
    participants = ["bot:" + bot] + ["human:" + pid for pid in sorted(members)]
    row = c.execute(
        "SELECT id,participants FROM conversations WHERE scope='shared' AND room_key=? "
        "AND closed_at IS NULL ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1", (bot,)).fetchone()
    if row:
        if json.loads(row["participants"] or "[]") != participants:
            c.execute("UPDATE conversations SET participants=? WHERE id=?", (encode(participants), row["id"]))
            H.event(c, H.KEEPER, "conversation.members_synced", row["id"], {"participants": participants})
        return H.conversation(c, row["id"])
    if not create:
        return None

    # Old CPO chats were private pairs. Archive them intact; never merge private human
    # context into the new group room.
    for old in c.execute(
            "SELECT id,participants FROM conversations WHERE kind='chat' AND task_id IS NULL "
            "AND closed_at IS NULL AND coalesce(scope,'direct')!='shared'").fetchall():
        old_participants = json.loads(old["participants"] or "[]")
        humans = [p for p in old_participants if str(p).startswith("human:")]
        if "bot:" + bot in old_participants and len(old_participants) == 2 and len(humans) == 1:
            # Preserve the original pair as that person's private archive. Giving each
            # legacy archive its own room key avoids colliding with their active room.
            c.execute(
                "UPDATE conversations SET scope='personal',owner_id=?,room_key=?,closed_at=? WHERE id=?",
                (humans[0], f"{bot}:legacy:{old['id']}", H.now(), old["id"]))
            H.event(c, H.KEEPER, "conversation.archived_for_shared_room", old["id"], {"bot": bot})
    opener = actor or next((p for p in participants if p.startswith("human:")), H.KEEPER)
    return H.open_conversation(c, opener, participants, kind="chat",
                               subject=(H.bot(c, bot) or {}).get("display_name", bot) + " shared room",
                               scope=SHARED, room_key=bot, allow_held=H.is_human(opener))


def chat_room(c, auth, who, bot, subject=None):
    from .shared_bots import route
    bot = H.actor_id(route(c, who.actor, "bot:" + bot))
    mode = thread_mode(c, bot)
    # Someone who is not a member of the shared room (a person who may write to the bot but is
    # not one of the people it works for) talks to it in a room of their own.
    if mode == SHARED and shared_member(c, auth, who.actor, bot):
        room = sync_shared_room(c, auth, bot, actor=who.actor, create=True)
    else:
        title = "Private " + auth.settings.assistant_name + " control room" if bot == "coo" else (
            "Chat with " + ((H.bot(c, bot) or {}).get("display_name") or bot))
        room = personal_room(c, who.actor, bot, subject or title)
        if room["subject"] == "Private " + auth.settings.assistant_name + " control room" and bot != "coo":
            c.execute("UPDATE conversations SET subject=? WHERE id=?", (subject or title, room["id"]))
        if subject and room["subject"] in (title, "Private " + auth.settings.assistant_name + " control room"):
            c.execute("UPDATE conversations SET subject=? WHERE id=?", (subject, room["id"]))
    return H.conversation(c, room["id"])


def work_room(c, auth, bot, requester):
    """The chat room a bot's task and routine work should appear in.

    Shared bots use the shared room. Personal bots use the requester's room when a
    person filed the task, otherwise the operator's room (scheduled runs, bot-to-bot).
    """
    if thread_mode(c, bot) == SHARED and not (str(requester).startswith("human:")
                                              and not shared_member(c, auth, requester, bot)):
        actor = requester if str(requester).startswith("human:") else None
        return sync_shared_room(c, auth, bot, actor=actor, create=True)
    human = requester if str(requester).startswith("human:") else None
    if not human:
        row = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (bot,)).fetchone()
        if row and row["owner_id"] and H.human(c, row["owner_id"]):
            human = "human:" + row["owner_id"]
    if not human:
        owner = auth.owner_id(c)
        if owner:
            human = "human:" + owner
    if not human:
        return None
    subject = ("Private " + auth.settings.assistant_name + " control room") if bot == "coo" else (
        "Chat with " + ((H.bot(c, bot) or {}).get("display_name") or bot))
    return personal_room(c, human, bot, subject)


def task_conversation_id(c, auth, owner, requester):
    """Conversation id for a new task: the bot's chat room, or None to open a task thread."""
    if not str(owner).startswith("bot:"):
        return None
    try:
        room = work_room(c, auth, H.actor_id(owner), requester)
    except H.Refused:
        return None
    return room["id"] if room else None


def archive_personal_room(c, actor, bot="coo"):
    row = c.execute(
        "SELECT id FROM conversations WHERE scope='personal' AND owner_id=? AND room_key=? "
        "AND closed_at IS NULL ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1",
        (actor, bot)).fetchone()
    if not row:
        return None
    room = H.conversation(c, row["id"])
    active = c.execute(
        "SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
        "WHERE m.conversation_id=? AND a.state IN ('leased','running') AND a.lease_until>?",
        (room["id"], H.now())).fetchone()
    if active:
        raise Problem("busy", "Wait for this conversation's current turn to finish", 409)
    c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), room["id"]))
    H.event(c, actor, "conversation.archive", room["id"], {"scope": PERSONAL})
    return room


def archive_shared_room(c, actor, bot):
    rows = c.execute("SELECT id FROM conversations WHERE scope='shared' AND room_key=? "
                     "AND closed_at IS NULL", (bot,)).fetchall()
    for row in rows:
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), row["id"]))
        H.event(c, actor, "conversation.archive", row["id"], {"scope": SHARED, "bot": bot})
    return len(rows)
