"""Conversation-mode policy for private assistant rooms and shared bot rooms."""

import json

from . import bot_access as A
from . import read_cache
from .store import H, P, Problem, encode
from . import bot_settings as BS


PERSONAL = "personal"
SHARED = "shared"
# The one private room each person has with the Assistant (backend/assistant.py); distinct from the
# room the person's tasks for the assistant bot land in, so its history is only what they said to it.
ASSISTANT_ROOM = "assistant"
# The owner's earlier chat with the Strategy Owner (backend/strategy_bot.py), from when the owner had it in place of an
# Assistant room: one per company, belonging to the owner seat, history now (read-only under "Earlier chats",
# backend/assistant.py). No new message goes to it; a transfer of ownership still moves it to the new owner. Its stored
# value stays "ceo": the rows stored so far carry it.
STRATEGY_ROOM = "ceo"
# The one private room each person has with the Librarian (backend/librarian.py): their Ask the Librarian questions.
DOCS_ROOM = "docs"


def personal_root(c, conversation):
    """Whether a room is a person's own (`PERSONAL`) or hangs off one: a sub-thread's root decides, and a root that is
    gone counts as one. The Assistant's turn there is a personal claim (backend/execution.py)."""
    if not conversation:
        return False
    if conversation.get("scope") == PERSONAL:
        return True
    root = conversation.get("root_id")
    if not root or root == conversation.get("id"):
        return False
    found = H.conversation(c, root)
    return not found or found.get("scope") == PERSONAL


def personal_run(c, assistant_bot, bot, conversation, task):
    """Whether a run is the Assistant's for one person: in their own room or a sub-thread of one (`personal_root`), or on
    a private task, which the Assistant holds only as a person's own message routine's turn
    (hubdb.refuse_shared_assistant). Its claim says `personal`, and it is issued no git credential. A voice session in a
    call in their own room is one too, pinned at its claim (`pin_call_lease`)."""
    return bot == assistant_bot and (personal_root(c, conversation) or bool(task and H.task_private(c, task)))


def assistant_room_owner(root):
    """The person whose own private Assistant chat this root is, or "": scope and stored audience personal (a row with
    none stored reads as its scope says, threads.audience_of), the Assistant's room key, a person as owner. The one test
    call_person and threads.refuse_while_private_call share."""
    from . import threads

    person = str((root or {}).get("owner_id") or "")
    if (
        not root
        or root.get("scope") != PERSONAL
        or root.get("room_key") != ASSISTANT_ROOM
        or threads.audience_of(root) != "personal"
        or not H.is_human(person)
    ):
        return ""
    return person


CALL_LEASE = "attempt.call_personal"  # event: target the attempt, detail {"person"}; written once, at its claim


def call_person(c, job_id):
    """The person the shared Assistant's voice session (job `job_id`) may be claimed for, or "". Only a job Tico
    dispatched itself counts: the call's `voice_job`, which call_sessions.ensure writes, never a ref a message carries
    (the ref only says where to look: the job's own meeting row must name the job). Its call is live, in that person's
    own Assistant room (or a sub-thread of it), which grants no other person or group a read, nobody else has been in
    the call and no other agent is attached. Read by primary keys: an ordinary job stops at its own message."""
    bot = H.actor_id(H.assistant_actor(c))
    row = c.execute(
        "SELECT s.id,s.thread_id,s.live_meeting_id FROM jobs j JOIN messages m ON m.id=j.message_id "
        "JOIN live_meeting_bots b ON b.meeting_id=json_extract(m.refs,'$.live_meeting.meeting_id') AND b.bot=? "
        "AND b.voice_job=j.id "
        "JOIN calls s ON s.live_meeting_id=b.meeting_id AND s.state='live' AND s.ending_at IS NULL "
        "WHERE j.id=? AND j.bot_id=? AND m.sender_id=?",
        (bot, job_id, bot, H.TICO_ACTOR),
    ).fetchone()
    room = (H.conversation(c, row["thread_id"]) or {}) if row else {}
    root = (H.conversation(c, room["root_id"]) or {}) if room.get("root_id") not in (None, room.get("id")) else room
    person = assistant_room_owner(root)
    if not person:
        return ""
    # Any member but the person and the Assistant reads the room: a person, a group, or another bot.
    others = c.execute(
        "SELECT 1 FROM thread_members WHERE thread_id IN (?,?) AND actor NOT IN (?,?) "
        "UNION ALL SELECT 1 FROM call_participants WHERE call_id=? AND actor<>? "
        "UNION ALL SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot<>? LIMIT 1",
        (root["id"], room["id"], person, "bot:" + bot, row["id"], person, row["live_meeting_id"], bot),
    ).fetchone()
    return "" if others else person


def pin_call_lease(c, attempt_id, job_id):
    """At a claim: when the lease is the Assistant's voice session in a call that is its person's alone
    (`call_person`), record that person against it, once; returns them, or "". The lease is a personal one for its whole
    life from then on (`personal_attempt`: no git credential), and reads as them only while the call still qualifies
    (`call_lease_person`)."""
    person = call_person(c, job_id)
    if person and not pinned_person(c, attempt_id):
        H.event(c, H.TICO_ACTOR, CALL_LEASE, attempt_id, {"person": person})
    return person


def pinned_person(c, attempt_id):
    """The person a lease was claimed personal for (`pin_call_lease`), or ""."""
    row = c.execute(
        "SELECT json_extract(detail,'$.person') FROM events WHERE action=? AND target=? LIMIT 1",
        (CALL_LEASE, attempt_id),
    ).fetchone()
    return str(row[0] or "") if row else ""


def call_lease_person(c, attempt_id, job_id):
    """The person a voice-session lease reads as now: the one it was claimed personal for, while its call still
    qualifies for that same person; else ""."""
    pinned = pinned_person(c, attempt_id)
    return pinned if pinned and call_person(c, job_id) == pinned else ""


def run_scope(c, attempt_id):
    """What a run of the shared Assistant reads of private tasks, as (kind, value). "person": everything that person reads,
    by their own rule, in their own room or a sub-thread of it (their own chat with it). "task": its own routine turn
    task alone, the one filed for a person's message routine. "gone": nothing at all, its person may no longer sign in
    (auth.standing). "": no person, so no private task; a call's turn is one, except the Assistant's voice session in a
    call in the person's own room (`call_lease_person`). Kept for the read (read_cache)."""
    return read_cache.value(c, ("run_scope", attempt_id), lambda: _run_scope(c, attempt_id), True)


def _run_scope(c, attempt_id):
    from .auth import standing

    def kept(person, scope):
        # Only while that person may sign in (auth.standing): a run for someone gone reads nothing.
        human = H.human(c, H.actor_id(person)) if H.is_human(person) else None
        return scope if human and not standing(c, human["id"]) else ("gone", "")

    row = c.execute(
        "SELECT a.task_id,a.job_id,m.conversation_id FROM attempts a JOIN jobs j ON j.id=a.job_id "
        "JOIN messages m ON m.id=j.message_id WHERE a.id=?",
        (attempt_id,),
    ).fetchone()
    if not row:
        return ("", "")
    if called := call_lease_person(c, attempt_id, row["job_id"]):
        return kept(called, ("person", called))  # its voice session in a call in that person's own room
    task = H.task(c, row["task_id"]) if row["task_id"] else None
    if task and task["owner_id"] == H.assistant_actor(c) and H.task_private(c, task):
        # The turn it was woken for. An earlier turn named the Assistant itself as requester: its person is the one the
        # routine run that filed it works for, by the version that run ran (`_routine_person`). None found: nothing.
        filed_for = task["requester_id"] if H.is_human(task["requester_id"]) else _routine_person(c, task)
        return kept(filed_for, ("task", task["id"]))
    room = H.conversation(c, row["conversation_id"]) or {}
    if room.get("scope") != PERSONAL and room.get("root_id") and room["root_id"] != room.get("id"):
        room = H.conversation(c, room["root_id"]) or {}
    if room.get("scope") != PERSONAL or room.get("room_key") != ASSISTANT_ROOM:
        return ("", "")  # the person's own chat with the Assistant only, not their chat with another bot
    person = str(room.get("owner_id") or "")
    return kept(person, ("person", person)) if H.is_human(person) else ("", "")


def _routine_person(c, task):
    """The person a routine turn was filed for, from its stored wake (refs.automation_run, the run that filed it) and the
    version that run ran, never the routine as it is now: that version's owner when a person, else the one person
    whose email is the inbox it starts on; "" when it names none or more than one, or the run or its version is gone."""
    wake = c.execute(
        "SELECT json_extract(refs,'$.automation_run') FROM messages WHERE conversation_id=? "
        "AND json_extract(refs,'$.automation_run') IS NOT NULL ORDER BY created_at,id LIMIT 1",
        (task.get("conversation_id"),),
    ).fetchone()
    row = (
        c.execute(
            "SELECT v.flow FROM automation_runs r "
            "JOIN automation_versions v ON v.automation_id=r.automation_id AND v.n=r.version WHERE r.id=?",
            (wake[0],),
        ).fetchone()
        if wake and wake[0]
        else None
    )
    try:
        flow = (json.loads(row["flow"] or "{}") or {}) if row else {}
    except ValueError:
        return ""
    if not isinstance(flow, dict):
        return ""
    owner = str(flow.get("owner") or "")
    if owner.startswith("person:"):
        return H.human_actor(owner.split(":", 1)[1])
    trigger = flow.get("trigger") or {}
    inbox = str(trigger.get("inbox") or "").strip().lower() if isinstance(trigger, dict) else ""
    found = c.execute("SELECT id FROM humans WHERE lower(email)=?", (inbox,)).fetchall() if inbox else []
    return H.human_actor(found[0]["id"]) if len(found) == 1 else ""


def scoped_wake(c, auth, attempt):
    """A claim of the shared Assistant's run as that run may read it: the other active tasks its wake lists to reconcile
    with (refs.reconcile.active, stored when the task was filed) only as far as the run reads them by its whole task
    rule (auth.task: its scope, rooms.run_scope, and the Assistant's bot activity access), in every copy of a message the
    claim carries (`message`, `history`, `refs` and `refs_json`), at the claim and at its replay. The stored messages
    stay as they are: what the run may read of them is decided as it reads (task_privacy). Changed in place; returns
    it."""
    from .auth import Identity

    shared = H.assistant_actor(c)
    if not isinstance(attempt, dict) or "bot:" + str(attempt.get("bot") or "") != shared:
        return attempt
    reader = Identity(shared, "bot", attempt_id=str(attempt.get("id") or ""))

    def readable(task_id):
        try:
            auth.task(c, reader, task_id)
            return True
        except Problem:
            return False

    def scoped(message):
        refs = message.get("refs")
        if isinstance(refs, str):
            refs = H._json(refs, {})
        reconcile = refs.get("reconcile") if isinstance(refs, dict) else None
        if not (isinstance(refs, dict) and isinstance(reconcile, dict) and isinstance(reconcile.get("active"), list)):
            return message
        kept = [i for i in reconcile["active"] if isinstance(i, dict) and readable(str(i.get("id") or ""))]
        if len(kept) == len(reconcile["active"]):
            return message
        refs = {k: v for k, v in refs.items() if k != "reconcile"}
        if kept:
            refs["reconcile"] = {**reconcile, "active": kept}
        shown = {**message, "refs": refs}
        if "refs_json" in message:
            shown["refs_json"] = json.dumps(refs)
        return shown

    if isinstance(attempt.get("message"), dict):
        attempt["message"] = scoped(attempt["message"])
    if isinstance(attempt.get("history"), list):
        attempt["history"] = [scoped(m) if isinstance(m, dict) else m for m in attempt["history"]]
    return attempt


def personal_attempt(c, assistant_bot, attempt_id):
    """`personal_run` for a run by its id, or a lease claimed personal (`pin_call_lease`), for its whole life
    (backend/github_app.py token)."""
    if pinned_person(c, attempt_id):
        return True
    row = c.execute(
        "SELECT a.bot_id,a.task_id,m.conversation_id FROM attempts a JOIN jobs j ON j.id=a.job_id "
        "JOIN messages m ON m.id=j.message_id WHERE a.id=?",
        (attempt_id,),
    ).fetchone()
    return bool(row) and personal_run(
        c,
        assistant_bot,
        row["bot_id"],
        H.conversation(c, row["conversation_id"]),
        H.task(c, row["task_id"]) if row["task_id"] else None,
    )


def session_config(conversation, bot, config):
    """The config a turn is handed. In the owner's earlier Strategy Owner chat (a turn queued there before it became
    history) it keys its session on that conversation
    (clients/sessions.py, `session: task`), so the owner's chat is one session and the Strategy Owner's goals session (its
    regular checks and goal work) another, and neither shares the other's context. The bot's own `session` setting
    never applies here: `session: bot` would put the owner's chat on the thread its goals session and other bots'
    messages use. Every runner reads `session` from the turn it is handed, so none needs to know this room."""
    from .strategy_bot import SLUG

    if (
        bot == SLUG
        and conversation
        and conversation.get("scope") == PERSONAL
        and conversation.get("room_key") == STRATEGY_ROOM
    ):
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
        config.update(
            {
                "description": row["description"] or "",
                "reports_to": row["reports_to"],
                "repo": row["repo"] or ("emp-" + slugs.get(row["bot"], row["bot"])),
            }
        )
        if row["thread_mode"]:
            config["thread_mode"] = row["thread_mode"]
        if row["owner_ids"] is not None:
            config["owner_ids"] = json.loads(row["owner_ids"])
        result[row["bot"]] = config
    return result


def thread_mode(c, bot, row=None):
    """The main assistant is structurally personal; CPO is shared before registry republish.
    `row` is the bot's bot_config row when the caller has read it already."""
    if bot == "assistant":
        return PERSONAL
    row = (
        row
        or c.execute(
            f"SELECT bc.thread_mode,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bc.bot=?", (bot,)
        ).fetchone()
    )
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
        part = slugs[start : start + 500]
        stored.update(
            {
                row["bot"]: row["access"]
                for row in c.execute(
                    "SELECT bot,access FROM bot_config WHERE bot IN (" + ",".join("?" * len(part)) + ")", part
                )
            }
        )
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
    return next((H.actor_id(p) for p in conversation.get("participants", []) if str(p).startswith("bot:")), None)


def personal_room(c, human_actor, bot="assistant", subject="Private control room"):
    # Once a bot's chats have moved, a person's chat with it is the bot's Home (backend/bot_home_merge.py). The Assistant's
    # rooms stay as they are: home_for_chat names no Home for them.
    from . import bot_home_merge

    home = bot_home_merge.home_for_chat(c, human_actor, bot)
    if home:
        return H.conversation(c, home)
    row = c.execute(
        "SELECT id FROM conversations WHERE scope='personal' AND owner_id=? AND room_key=? "
        "AND closed_at IS NULL ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1",
        (human_actor, bot),
    ).fetchone()
    if row:
        return H.conversation(c, row["id"])

    # Adopt an open legacy pair instead of splitting the same person's history.
    pair = {human_actor, "bot:" + bot}
    for candidate in H.conversations_for(c, human_actor, limit=500):
        if (
            candidate["kind"] == "chat"
            and not candidate.get("task_id")
            and not candidate["closed_at"]
            and set(candidate["participants"]) == pair
        ):
            c.execute(
                "UPDATE conversations SET scope='personal',owner_id=?,room_key=? WHERE id=?",
                (human_actor, bot, candidate["id"]),
            )
            return H.conversation(c, candidate["id"])
    # A person's own room opens even while the bot is paused: their message waits for it (hubdb.say).
    return H.open_conversation(
        c,
        human_actor,
        [human_actor, "bot:" + bot],
        kind="chat",
        subject=subject,
        scope=PERSONAL,
        owner_actor=human_actor,
        room_key=bot,
        allow_held=H.is_human(human_actor),
    )


def sync_shared_room(c, auth, bot, actor=None, create=False):
    members = shared_member_ids(c, auth, bot)
    if actor and str(actor).startswith("human:"):
        if H.actor_id(actor) not in members:
            raise Problem("forbidden", "You are not a member of this shared bot room", 403)
    participants = ["bot:" + bot] + ["human:" + pid for pid in sorted(members)]
    row = c.execute(
        "SELECT id,participants FROM conversations WHERE scope='shared' AND room_key=? "
        "AND closed_at IS NULL ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1",
        (bot,),
    ).fetchone()
    if row:
        if json.loads(row["participants"] or "[]") != participants:
            c.execute("UPDATE conversations SET participants=? WHERE id=?", (encode(participants), row["id"]))
            H.event(c, H.TICO_ACTOR, "conversation.members_synced", row["id"], {"participants": participants})
        return H.conversation(c, row["id"])
    if not create:
        return None

    # Old CPO chats were private pairs. Archive them intact; never merge private human
    # context into the new group room.
    for old in c.execute(
        "SELECT id,participants FROM conversations WHERE kind='chat' AND task_id IS NULL "
        "AND closed_at IS NULL AND coalesce(scope,'direct')!='shared'"
    ).fetchall():
        old_participants = json.loads(old["participants"] or "[]")
        humans = [p for p in old_participants if str(p).startswith("human:")]
        if "bot:" + bot in old_participants and len(old_participants) == 2 and len(humans) == 1:
            # Preserve the original pair as that person's private archive. Giving each
            # legacy archive its own room key avoids colliding with their active room.
            c.execute(
                "UPDATE conversations SET scope='personal',owner_id=?,room_key=?,closed_at=? WHERE id=?",
                (humans[0], f"{bot}:legacy:{old['id']}", H.now(), old["id"]),
            )
            H.event(c, H.TICO_ACTOR, "conversation.archived_for_shared_room", old["id"], {"bot": bot})
    opener = actor or next((p for p in participants if p.startswith("human:")), H.TICO_ACTOR)
    return H.open_conversation(
        c,
        opener,
        participants,
        kind="chat",
        subject=(H.bot(c, bot) or {}).get("display_name", bot) + " shared room",
        scope=SHARED,
        room_key=bot,
        allow_held=H.is_human(opener),
    )


def chat_room(c, auth, who, bot, subject=None):
    from .shared_bots import route

    bot = H.actor_id(route(c, who.actor, "bot:" + bot))
    mode = thread_mode(c, bot)
    # Someone who is not a member of the shared room (a person who may write to the bot but is
    # not one of the people it works for) talks to it in a room of their own.
    if mode == SHARED and shared_member(c, auth, who.actor, bot):
        room = sync_shared_room(c, auth, bot, actor=who.actor, create=True)
    else:
        title = (
            "Private " + auth.settings.assistant_name + " control room"
            if bot == "assistant"
            else ("Chat with " + ((H.bot(c, bot) or {}).get("display_name") or bot))
        )
        room = personal_room(c, who.actor, bot, subject or title)
        if room["subject"] == "Private " + auth.settings.assistant_name + " control room" and bot != "assistant":
            c.execute("UPDATE conversations SET subject=? WHERE id=?", (subject or title, room["id"]))
        if subject and room["subject"] in (title, "Private " + auth.settings.assistant_name + " control room"):
            c.execute("UPDATE conversations SET subject=? WHERE id=?", (subject, room["id"]))
    return H.conversation(c, room["id"])


def work_room(c, auth, bot, requester):
    """The chat room a bot's task and routine work should appear in.

    Shared bots use the shared room. Personal bots use the requester's room when a
    person filed the task, otherwise the operator's room (scheduled runs, bot-to-bot).
    """
    if thread_mode(c, bot) == SHARED and not (
        str(requester).startswith("human:") and not shared_member(c, auth, requester, bot)
    ):
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
    subject = (
        ("Private " + auth.settings.assistant_name + " control room")
        if bot == "assistant"
        else ("Chat with " + ((H.bot(c, bot) or {}).get("display_name") or bot))
    )
    return personal_room(c, human, bot, subject)


def task_conversation_id(c, auth, owner, requester):
    """Conversation id for a new task: the bot's chat room, or None to open a task thread. The Assistant's task always
    opens its own: its chat rooms are each person's own."""
    if not str(owner).startswith("bot:") or owner == H.assistant_actor(c):
        return None
    try:
        room = work_room(c, auth, H.actor_id(owner), requester)
    except H.Refused:
        return None
    # A Home is never a task's conversation (backend/bot_home_merge.py): the task opens its own thread there.
    if not room or room.get("audience") == "home":
        return None
    return room["id"]


def archive_personal_room(c, actor, bot="assistant"):
    row = c.execute(
        "SELECT id FROM conversations WHERE scope='personal' AND owner_id=? AND room_key=? "
        "AND closed_at IS NULL ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1",
        (actor, bot),
    ).fetchone()
    if not row:
        return None
    room = H.conversation(c, row["id"])
    active = c.execute(
        "SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
        "WHERE m.conversation_id=? AND a.state IN ('leased','running') AND a.lease_until>?",
        (room["id"], H.now()),
    ).fetchone()
    if active:
        raise Problem("busy", "Wait for this conversation's current turn to finish", 409)
    c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), room["id"]))
    H.event(c, actor, "conversation.archive", room["id"], {"scope": PERSONAL})
    return room


def archive_shared_room(c, actor, bot):
    rows = c.execute(
        "SELECT id FROM conversations WHERE scope='shared' AND room_key=? AND closed_at IS NULL", (bot,)
    ).fetchall()
    for row in rows:
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), row["id"]))
        H.event(c, actor, "conversation.archive", row["id"], {"scope": SHARED, "bot": bot})
    return len(rows)
