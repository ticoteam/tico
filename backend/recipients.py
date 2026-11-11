"""Who a message may go to: every send's recipient gate (require_recipient), its room (named_destination), and whether
an actor can read a task's question (can_answer). System actors have no inbox."""

from copy import copy
from types import SimpleNamespace

from . import hubdb as H
from . import reach, read_cache, task_privacy
from .auth import Auth
from .store import Problem


def readable(c, target, source=None):
    """A person still on the chart, or a bot with a queue that accepts questions."""
    if H.is_human(target):
        from .auth import standing

        return bool(H.human(c, target)) and not standing(c, H.actor_id(target))
    from . import lifecycle

    if not H.is_bot(target) or not lifecycle.claims_message(c, H.actor_id(target), source or {}):
        return False
    from .enqueue import external_bot_sql
    from .harnesses import SYNCED_SQL

    # Synced inboxes take only their person's chat messages. Independent external
    # agents read their messages themselves; a synced bot needs its desktop delivery queue.
    return not read_cache.one(
        c,
        "SELECT EXISTS(SELECT 1 FROM bot_settings WHERE bot_id=? AND harness IN ("
        + SYNCED_SQL
        + ")) AND "
        + external_bot_sql("?"),
        (H.actor_id(target), target),
    )[0]


def can_answer(c, target, refs=None, conversation_id=None, in_reply_to=None, source=None, *, sender=None):
    """The same full task read for people and bots, including every source of the question."""
    from .auth import actor_reads_task

    message = source or {
        "sender_id": sender,
        "refs": refs or {},
        "conversation_id": conversation_id,
        "in_reply_to": in_reply_to,
    }
    if not readable(c, target, message):
        return False
    try:
        return all(
            actor_reads_task(c, target, H.task(c, tid)) for tid in task_privacy.message_tasks(c, message)
        ) and task_privacy.meeting_sources(c).message_readable(target, message)
    except Problem:
        return False


def _recipient_auth(c):
    """Database-only authorization for routing and migrations, without a sign-in or a server."""
    from . import access

    policy = Auth.__new__(Auth)
    policy.settings = getattr(c, "settings", SimpleNamespace(assistant_bot="coo", assistant_name="Assistant"))
    policy.bot_admins = set(access.owner_and_admin_emails(c))
    policy.owner_email = (access._load_json(c, access.OWNER) or {}).get("email") or ""
    policy._owner_id = ""
    return policy


def named_destination(c, who, body, auth, create=True):
    """The conversation a message lands in, named before any check reads it, so each check and the write see the same
    one. A room that moved names its Home (backend/bot_home_merge.py). A bot's message to a person that names no room
    lands in that person's chat Home when there is one (hubdb.chat_home): where `H.say` would put it, but otherwise
    only after the checks had run, so a private run's words would reach a Home that others read. A message to a bot,
    or from a person with no room named, costs no lookup here."""
    from . import bot_home_merge

    if body.conversation_id:
        return bot_home_merge.home_for(c, body.conversation_id, create=create) or body.conversation_id
    if H.is_human(who.actor) or str(body.to).lower() == "assistant" or str(body.to).startswith("bot:"):
        return None
    to = body.to if str(body.to).startswith("human:") else auth.target(c, who, body.to)
    return H.chat_home(c, who.actor, to, "notice" if body.kind == "notice" else "chat", create=create)


def require_recipient(
    c,
    who,
    target,
    refs=None,
    conversation_id=None,
    in_reply_to=None,
    *,
    kind="say",
    ctx=None,
    body=None,
    auth=None,
    existing=False,
    delivered=None,
):
    """Every send recipient gate, used for each routing candidate and again on the actual send.

    Existing message repairs preserve their room's audience and meeting gates. Fresh sends also check the sender's
    room and reply access and the entire destination. After writing, the same gate checks the actual deliveries.
    """
    from . import assistant, delivery, lifecycle, rooms, strategy_bot, views

    auth = auth or _recipient_auth(c)
    settings = auth.settings
    if delivered is not None:
        reach.check_delivered(
            c,
            who,
            delivered["id"],
            task_id=H.refs_task_id(body.refs),
            conversation_id=delivered.get("conversation_id"),
            in_reply_to=body.in_reply_to,
        )
        if who.role == "bot":
            conv = H.conversation(c, delivered["conversation_id"])
            if who.attempt_id and all(p.startswith("bot:") for p in conv["participants"]):
                c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, conv["id"]))
            auth.conversation(c, who, conv["id"])
        return
    body = (
        copy(body)
        if body is not None
        else SimpleNamespace(
            to=target,
            refs=dict(refs or {}),
            conversation_id=conversation_id,
            in_reply_to=in_reply_to,
            kind=kind,
            command=False,
            needs_action=False,
            delivery=None,
            defer_for=None,
        )
    )
    body.to = target
    if not existing:
        body.conversation_id = named_destination(c, who, body, auth)
    in_assistant_room = False
    if str(body.to).lower() == "assistant":
        if who.role not in ("human", "owner") or who.via:
            raise Problem("forbidden", "Only a human writes to their private Assistant", 403)
        if body.conversation_id and not assistant.own_room(c, who, body.conversation_id):
            raise Problem("forbidden", "Use your own Assistant room", 403)
        info = assistant.availability(c, settings, who)
        if not info["available"]:
            raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
        body.to = info["bot"]
        body.conversation_id = assistant.chat_room(c, settings, who)["id"]
    to = auth.target(c, who, body.to)
    if not body.conversation_id:
        from .shared_bots import route

        to = auth.target(c, who, route(c, who.actor, to))
    if to.startswith("bot:"):
        docs = (
            who.role in ("human", "owner")
            and body.conversation_id
            and to == "bot:" + views.DOC_BOT
            and views.docs_room(auth.conversation(c, who, body.conversation_id), who)
        )
        if not docs:
            if who.role in ("human", "owner") and H.actor_id(to) == settings.assistant_bot:
                # The assistant takes chat in the caller's own Assistant room and nowhere else.
                if not assistant.own_room(c, who, body.conversation_id):
                    raise Problem(
                        "forbidden",
                        settings.assistant_name + " chats only in your own Assistant "
                        "(/api/v2/assistant); message one of your bots here",
                        403,
                    )
                in_assistant_room = True
            elif who.role in ("human", "owner") and H.actor_id(to) == strategy_bot.SLUG:
                # No person chats with the Strategy Owner: their Assistant asks it (backend/assistant.py HELPERS). A
                # person's post with no room (the Goals page, `tico chat`) goes to their Assistant instead; the
                # owner's earlier chat with it is history, read-only. Bots still message it: its goals session.
                if body.conversation_id or who.via:
                    raise Problem(
                        "forbidden",
                        "Nobody chats with the " + strategy_bot.name(c) + " directly; ask "
                        "your " + settings.assistant_name + ", which hands it the work",
                        403,
                    )
                info = assistant.availability(c, settings, who)
                if not info["available"]:
                    raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
                to = "bot:" + info["bot"]
                body.conversation_id = assistant.chat_room(c, settings, who)["id"]
                in_assistant_room = True
            auth.require_write(c, who, H.actor_id(to), missing="Unknown recipient")
    if body.conversation_id and not existing:
        auth.conversation(c, who, body.conversation_id, write=True)
    if body.in_reply_to:
        msg = H.message(c, body.in_reply_to)
        if not msg:
            raise Problem("not_found", "Original message not found", 404)
        if not existing:
            auth.conversation(c, who, msg["conversation_id"], write=True)
            task_privacy.require_message(c, who, msg)
        if body.conversation_id and msg["conversation_id"] != body.conversation_id:
            raise Problem("reference", "Reply belongs to a different conversation", 422)
    refs = dict(body.refs or {})
    if body.command:
        refs["command"] = True
    if body.needs_action:
        refs["needs_action"] = True
    if wrong := delivery.check(body.delivery, body.defer_for):
        raise Problem("delivery", wrong, 422)
    # How it reaches a bot (backend/delivery.py). With none named a message goes as it always has: a person's
    # steers, a bot's goes as bot-to-bot messages do (backend/bot_to_bot.py).
    way = body.delivery
    if to.startswith("bot:") and way in ("steer", "queue") and who.role == "bot":
        refs["needs_action"] = True
    if in_assistant_room:
        refs["assistant"] = True  # the server's mark: this turn acts for the person
    conversation_id = body.conversation_id
    steer_task = H._task_ref(refs.get("task"))
    if not conversation_id and body.kind == "steer" and who.role == "bot" and steer_task:
        # A requester's steer goes to the task's own room, which its owner's running turn takes input from.
        task = H.task(c, steer_task)
        if not task or task["requester_id"] != who.actor or task["owner_id"] != to or not task.get("conversation_id"):
            raise Problem("steer_target", "Steer a task you requested, sent to its owner", 422)
        conversation_id = task["conversation_id"]
    if not conversation_id and who.role in ("owner", "human") and to.startswith("bot:"):
        conversation_id = rooms.chat_room(c, auth, who, H.actor_id(to))["id"]
    # An onboarding bot only learns: it answers in chat and talks with its manager, and sends nothing else.
    lifecycle.refuse_send(c, who, to, H.conversation(c, conversation_id) if conversation_id else None)
    Auth.require_bot_contact(c, who, to, conversation_id, H.refs_task_id(refs), kind="message")
    reach.check(
        c,
        who,
        to,
        "chat",
        task_id=H.refs_task_id(refs),
        conversation_id=conversation_id,
        in_reply_to=body.in_reply_to,
        ctx=ctx,
    )
    if conversation_id:
        from . import threads
        from .auth import actor_reads_task

        conv = H.conversation(c, conversation_id)
        if not conv:
            raise Problem("not_found", "Conversation not found", 404)
        if conv.get("audience") == "home":
            allowed = threads.readable(c, threads.actor_viewer(c, to), conv["id"])
        else:
            allowed = to in threads.legacy_participants(c, conv) or (
                conv.get("task_id")
                and actor_reads_task(c, to, H.task(c, conv["task_id"]))
                and threads.readable(c, threads.actor_viewer(c, to), conv["id"])
            )
        if not allowed:
            raise Problem("forbidden", "Recipient is not a conversation participant", 403)
    # A repair keeps the room's existing audience; a fresh send checks the whole destination.
    task_privacy.require_destination(
        c,
        who,
        to,
        None if existing else conversation_id,
        refs,
        H.message(c, body.in_reply_to) if body.in_reply_to else None,
    )
    body.to, body.refs, body.conversation_id = to, refs, conversation_id
    return body
