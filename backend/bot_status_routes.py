"""Bot status routes: updates, status, quarantine, inbox, answers, acknowledgements, history, turns."""

from fastapi import Request
from . import models as M, rooms, task_privacy as privacy, views
from .store import H, Problem


# The inbox sends at most this many messages and this many tasks; the rest wait for a later read.
INBOX_LIMIT = 100


INBOX_PAGE = 500  # unread messages read per query while looking for the newest INBOX_LIMIT readable ones


def _first_visible(items, visible):
    """The first `INBOX_LIMIT` of `items` that pass `visible`, and whether one more item passes it too: a hidden item
    never sets the flag. `items` may be a generator, read only as far as that."""
    kept = []
    for item in items:
        if visible(item):
            if len(kept) == INBOX_LIMIT:
                return kept, True
            kept.append(item)
    return kept, False


def register(app, auth, delegated_identity, mutate, settings_admin, store, reset_bot_sessions):
    @app.post("/api/v2/bots/{bot}/definition")
    def update_bot(request: Request, bot: str, body: M.BotDefinitionUpdate):
        who = request.state.identity
        with store.read() as c:  # the report-manager check below runs before the write, on the id
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)

        def work(c):
            acting = delegated_identity(c, who, body.on_behalf_of) if body.on_behalf_of else who
            result = settings_admin.update_bot(c, acting, bot, body)
            if acting is not who:
                H.event(
                    c,
                    who.actor,
                    "bot.definition_delegated",
                    bot,
                    {"on_behalf_of": acting.actor, "message_id": body.on_behalf_of},
                )
            before, after = result.pop("previous_thread_mode"), result["thread_mode"]
            if before != after:
                if after == rooms.SHARED:
                    rooms.sync_shared_room(c, auth, bot, actor=who.actor, create=True)
                else:
                    rooms.archive_shared_room(c, who.actor, bot)
                reset_bot_sessions(c, bot)
            return result

        return mutate(request, body, settings_admin.as_report_manager(who, bot, "definition", work))

    @app.post("/api/v2/bots/{bot}/status")
    def status_set(request: Request, bot: str, body: M.StatusUpdate):
        who = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if who.actor != "bot:" + bot and not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "You cannot manage this bot", 403)
            from .shared_bots import declared, source_of

            source = source_of(declared(c, bot))
            if (
                source
                and (H.bot(c, source) or {}).get("state") == "archived"
                and body.state in ("active", "paused", "quarantined")
            ):
                raise Problem("original_archived", "Restore the original before changing its branch's status", 409)
            if body.state == "active" and (H.bot(c, bot) or {}).get("state") == "quarantined":
                raise Problem("quarantined", "Review the refusal and use quarantine clear to resume this bot", 409)
            if body.task_id:
                auth.task(c, who, body.task_id)
            if privacy.private_execution(c, who):
                sources = [tid for tid in privacy.attempt_tasks(c, who.attempt_id) if H.task_private(c, H.task(c, tid))]
                if body.task_id and body.task_id not in sources:
                    raise Problem("privacy", "Private execution status must keep its task provenance", 403)
                if not body.task_id and sources:
                    body.task_id = sources[0]
            return H.status_set(c, who.actor, bot, state=body.state, focus=body.focus, task_id=body.task_id)

        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/quarantine/clear")
    def quarantine_clear(request: Request, bot: str, body: M.QuarantineClear):
        caller = request.state.identity

        def work(c):
            nonlocal bot
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            # A person's request in chat ("why is my content not unblocked?") is
            # enough; BotOps lifts it as that person, so an escape still needs a person's say-so.
            # BotOps never clears one on its own authority: it acts as the person who asked, and that person must
            # manage the bot.
            botops = caller.actor == H.bot_actor(H.FLEET_MAINTAINER)
            if botops and not body.on_behalf_of:
                raise Problem(
                    "on_behalf_of",
                    "BotOps clears a quarantine only as the person who asked it: cite their message",
                    403,
                )
            who = delegated_identity(c, caller, body.on_behalf_of) if body.on_behalf_of else caller
            if who is not caller:
                H.event(
                    c,
                    caller.actor,
                    "quarantine.clear_delegated",
                    bot,
                    {"on_behalf_of": who.actor, "message_id": body.on_behalf_of},
                )
            if not (auth.operator(c, who, bot) or auth.bot_manager(c, who, bot)):
                raise Problem("forbidden", "Only a person who manages this bot can clear quarantine", 403)
            from . import task_quarantine

            quarantined = (H.bot(c, bot) or {}).get("state") == "quarantined"
            held = c.execute(
                "SELECT 1 FROM quarantine_holds WHERE bot_id=? AND released_at IS NULL LIMIT 1", (bot,)
            ).fetchone()
            if not quarantined and not held:
                raise Problem("quarantined", "This bot is not quarantined", 409)
            if quarantined:
                settings_admin.ensure_activation(c, who, bot)
            task_quarantine.release(c, who.actor, bot=bot)
            if not quarantined:
                return {"status": H.status(c, bot)}
            # Stopped runs settle on their own (execution.auto_reconcile); they no longer hold a resume.
            return {
                "status": H.status_set(
                    c, who.actor, bot, state="active", focus="", reason=(body.note or "").strip() or "Resumed"
                )
            }

        return mutate(request, body, work)

    @app.get("/api/v2/inbox")
    def inbox(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            privacy.snapshot(c)
            rooms = {}

            def room_open(message):
                """Anyone reads a message only in a thread they may read now (C1, the same check for people and bots):
                checked once per thread, before the message's own privacy, so a backlog in threads this reader was
                never given costs a lookup each, not a provenance walk."""
                if message["conversation_id"] not in rooms:
                    try:
                        auth.conversation(c, who, message["conversation_id"])
                        rooms[message["conversation_id"]] = True
                    except Problem:
                        rooms[message["conversation_id"]] = False
                return rooms[message["conversation_id"]]

            def newest():
                """The unread backlog newest first, a page at a time: each page's rooms, their meeting provenance and
                their tasks read together (privacy.prime_rooms), then its messages in open rooms read together
                INBOX_LIMIT at a time. Rooms and messages are checked one by one only as far as the answer reads."""
                for page in H.inbox_pages(c, who.actor, size=INBOX_PAGE):
                    privacy.prime_rooms(c, {m["conversation_id"] for m in page} - set(rooms))
                    part = []
                    for at, message in enumerate(page, 1):
                        if room_open(message):
                            part.append(message)
                        if len(part) == INBOX_LIMIT or (part and at == len(page)):
                            privacy.prime_queued(c, H.prime_messages(c, [m["id"] for m in part]).values())
                            yield from part
                            part = []

            # A bot that never marks messages read can hold thousands: the newest INBOX_LIMIT readable ones are
            # checked and sent, oldest first as before, and `more_messages` says older ones are waiting. Paging stops
            # there, so the rest of the backlog is never read.
            kept, more = _first_visible(newest(), lambda m: privacy.message_readable(c, privacy.actor(who), m))
            result = {"actor": who.actor, "messages": H.mark_answered(c, kept[::-1]), "more_messages": more}
            result["tasks"], result["more_tasks"] = _first_visible(
                H.tasks(c, owner=who.actor, status=("open",)), lambda t: privacy.task_readable(c, who, t)
            )
            result["notices"] = [m for m in result["messages"] if m["kind"] == "notice"]
            from .files import readable_page_refs

            return readable_page_refs(c, auth, who, result)

    @app.post("/api/v2/messages/{mid}/ack")
    def acknowledge(request: Request, mid: str, body: M.Empty):
        who = request.state.identity

        def work(c):
            msg = H.message(c, mid)
            # Addressed to the caller, or reaching them another way (a mention, a DM): a delivery row (threads).
            if not msg or (
                msg["recipient_id"] != who.actor
                and not c.execute(
                    "SELECT 1 FROM message_deliveries WHERE message_id=? AND recipient=?", (mid, who.actor)
                ).fetchone()
            ):
                raise Problem("forbidden", "You may acknowledge only messages addressed to you", 403)
            auth.conversation(c, who, msg["conversation_id"])
            privacy.require_message(c, who, msg)
            H._message_writer(c, who.actor, msg, H.conversation(c, msg["conversation_id"]))
            H.mark_read(c, who.actor, mid, recipient=who.actor)
            H.mark_delivered(c, who.actor, mid, recipient=who.actor)
            return {"read": True}

        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/history")
    def history(request: Request, bot: str, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.require_read(c, who, bot)
            inputs = privacy.status_inputs(c, [bot])[bot]
            return [
                privacy.status(c, who, row, **inputs) for row in H.status_history(c, bot, since=views.since_time(since))
            ]

    @app.get("/api/v2/bots/{bot}/turns")
    def turns(request: Request, bot: str, since: str | None = None):
        who = request.state.identity
        auth.domain(who)
        # A snapshot, so the page's messages and runs are read together and stay cached for each check.
        with store.read_transaction() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.require_read(c, who, bot)
            since_at = views.since_time(since)
            found = list(H.turns(c, bot, since=since_at))
            stored = H.prime_messages(c, [turn["message_id"] for turn in found])
            privacy.prime_messages(c, list(stored.values()))
            from . import threads

            threads.prime_members(c, who, [row["conversation_id"] for row in stored.values()])
            privacy.prime_attempts(c, [turn["id"] for turn in found])
            views.prime_attempt_details(c, [turn["id"] for turn in found])
            result = []
            for turn in found:
                msg = H.message(c, turn["message_id"])
                if not msg:
                    continue
                try:
                    auth.run_conversation(c, who, bot, msg)
                    privacy.require_message(c, who, msg)
                    privacy.require_attempt(c, who, turn["id"])
                except Problem:
                    continue
                result.append({**turn, **views.attempt_details(c, turn)})
            result.extend(views.failed_attempts(c, who, auth, bot, since_at))
            result.sort(key=lambda row: row.get("started_at") or "", reverse=True)
            from .usage_records import ran_on

            made = ran_on(c, [row["id"] for row in result])
            return [{**row, "ran_on": made[row["id"]]} for row in result]
