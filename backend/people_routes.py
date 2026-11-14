"""People routes: remove, edit, Slack links, bot goals, models, conversations, outline."""

from .store import H
from .harnesses import HARNESS_CATALOG
from . import models as M
from .providers import MODEL_CATALOG
from .store import P
from .store import Problem
from . import providers as Providers
from fastapi import Request
from . import gateway_page
from . import gateway_tiers
from . import people_remove
from . import task_privacy as privacy
from . import rooms
from . import teammate_access
from . import turns as turn_work
from . import views


def register(app, auth, mutate, settings, store, visual_view):
    @app.delete("/api/v2/people/{pid}")
    def person_remove(request: Request, pid: str):
        """The owner removes a left person who has no history, such as one added by mistake, for good
        (backend/people_remove.py). Anyone with history stays as left."""
        who = request.state.identity
        auth.domain(who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner removes someone", 403)

        def person(c):
            found = P.person(pid, views.roster(c))
            if not found:
                raise Problem("not_found", "Person not found", 404)
            return found

        with store.read_transaction() as c:  # every table is read once, outside the write lock
            found = P.person(pid, views.roster(c))  # gone already: a retry's replay, or the 404 below
            if found and not people_remove.check(found) and people_remove.history(c, pid):
                raise Problem("has_history", people_remove.KEEPS, 409)

        def work(c):
            roster = views.roster(c)
            people_remove.remove(c, who.actor, roster, person(c))
            return {"person": pid, "removed": True}

        result = mutate(request, M.Empty(), work)
        with store.read() as c:
            auth.sync_access(c)
        return result

    @app.get("/api/v2/people/{pid}/slack")
    def person_slack(request: Request, pid: str):
        """Slack DMs between this person and Tico bots (one Tico app; each bot has its own hub thread)."""
        who = request.state.identity
        auth.domain(who)
        if who.role == "bot":
            raise Problem("forbidden", "Bots read Slack through their own conversations", 403)
        if not (who.role == "owner" or H.actor_id(who.actor) == pid):
            raise Problem("forbidden", "This Slack thread belongs to another person", 403)
        with store.read() as c:
            actor = "human:" + pid
            threads = []
            for row in c.execute(
                "SELECT t.bot,t.channel,t.thread_ts,t.conversation_id,t.last_routed_at,t.created_at "
                "FROM slack_threads t JOIN conversations v ON v.id=t.conversation_id "
                "WHERE t.thread_ts='' AND v.participants LIKE ? "
                "ORDER BY COALESCE(t.last_routed_at, t.created_at) DESC",
                ('%"' + actor + '"%',),
            ).fetchall():
                conv = H.conversation(c, row["conversation_id"])
                if not conv or conv.get("closed_at"):
                    continue
                try:
                    auth.conversation(c, who, conv["id"])
                except Problem:
                    continue
                messages = visual_view(c, who, privacy.page(c, who, conv["id"])["messages"])
                bot = H.bot(c, row["bot"]) or {}
                threads.append(
                    {
                        "bot": row["bot"],
                        "display_name": bot.get("display_name") or row["bot"],
                        "conversation_id": conv["id"],
                        "channel": row["channel"],
                        "last_message_at": conv.get("last_message_at") or row["last_routed_at"],
                        "messages": messages,
                    }
                )
            return {"person": pid, "threads": threads}

    @app.post("/api/v2/bots/{bot}/goals")
    def bot_goals(request: Request, bot: str, body: M.BotGoals):
        who = request.state.identity

        def work(c):
            nonlocal bot
            auth.domain(who)
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Unknown bot", 404)
            if not (auth.bot_manager(c, who, bot) or auth.operator(c, who, bot)):
                raise Problem("forbidden", "Only a person who manages this bot sets its goals", 403)
            c.execute("UPDATE bot_config SET goals=? WHERE bot=?", (body.goals, bot))
            if not c.execute("SELECT 1 FROM bot_config WHERE bot=?", (bot,)).fetchone():
                raise Problem("not_found", "Unknown bot", 404)
            who_name = (H.human(c, H.actor_id(who.actor)) or {}).get("name") or who.actor
            text = (
                f"{who_name} updated your goals.\n\n{body.goals}\n\n"
                "Treat these as your current goals until they change again."
            )
            if not body.goals.strip():
                text = f"{who_name} cleared your goals. Work from your standing instructions until new goals are set."
            # The stored goals are the change: a bot manager's notice to a paused bot whose held queue is full is
            # only withheld, and the goals stand on the bot's settings.
            message, _ = H.unless_queue_full(c, lambda: H.say(c, who.actor, "bot:" + bot, text, kind="notice"))
            H.event(
                c,
                who.actor,
                "bot.goals",
                bot,
                {"chars": len(body.goals), **({} if message else {"notice": "queue_full"})},
            )
            return {"bot": bot, "goals": body.goals, "message_id": message["id"] if message else None}

        return mutate(request, body, work)

    @app.get("/api/v2/models")
    def models(request: Request):
        auth.domain(request.state.identity)
        with store.read() as c:
            choice = Providers.load(c, settings)
            cat = gateway_page.catalog(c, settings)
        return {
            # Each with where a bot on it compacts when none is saved (the Compact list marks it "(default)").
            "models": [
                {**row, "compact_default": Providers.compact_default(row["id"])}
                for row in MODEL_CATALOG
                if not row.get("held")
            ],
            "harnesses": list(HARNESS_CATALOG),
            "gateway": gateway_tiers.view(cat),
            "enabled_providers": choice["enabled"],
            "default": {"runtime": choice["runtime"], "model": choice["model"]},
        }

    @app.get("/api/v2/conversations")
    def conversations(request: Request, bot: str | None = None, chat_with: str | None = None):
        """`chat_with=<bot>`: my open chat with that bot or its Home after the chats move there.
        Otherwise my conversations, or the owner's view of a bot's."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if bot and who.role != "owner":
                raise Problem("forbidden", "Only the owner may inspect a bot's conversations", 403)
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if chat_with:
                chat_with = (H.resolve_bot(c, chat_with) or {}).get("id", chat_with)
                from .shared_bots import route

                chat_with = H.actor_id(route(c, who.actor, "bot:" + chat_with))
            rows = (
                H.chats_with(c, who.actor, "bot:" + chat_with)
                if chat_with
                else H.conversations_for(c, "bot:" + bot if bot else who.actor)
            )
            visible = []
            for row in rows:
                try:
                    auth.conversation(c, who, row["id"])
                    visible.append(row)
                except Problem:
                    pass
            rows = visible
            for row in rows:
                # Room summaries may contain a task notice from a previous assignee.
                for key in ("last_message", "last_body", "preview"):
                    row.pop(key, None)
            return {"actor": who.actor, "conversations": rows}

    @app.post("/api/v2/conversations")
    def create_conversation(request: Request, body: M.ConversationCreate):
        who = request.state.identity

        def work(c):
            participants = [auth.target(c, who, p, need="write") for p in body.participants]
            if who.role == "bot" and any(p.startswith("human:") for p in participants):
                raise Problem(
                    "forbidden", "Use the current conversation to contact its human; use tasks for handoffs", 403
                )
            bots = [H.actor_id(p) for p in participants if p.startswith("bot:")]
            if who.role in ("owner", "human") and body.kind == "chat" and len(bots) == 1:
                if any(p not in ("bot:" + bots[0], who.actor) for p in participants):
                    raise Problem("participants", "Use the bot's canonical personal or shared room", 422)
                return rooms.chat_room(c, auth, who, bots[0], subject=body.subject)
            conv = H.open_conversation(c, who.actor, participants, kind=body.kind, subject=body.subject)
            if who.role == "bot" and who.attempt_id:
                c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (who.attempt_id, conv["id"]))
            return conv

        return mutate(request, body, work)

    @app.get("/api/v2/conversations/{cid}/messages")
    def messages(
        request: Request,
        cid: str,
        since: str | None = None,
        before: str | None = None,
        limit: int | None = None,
        max_bytes: int | None = None,
    ):
        with store.read() as c:
            auth.conversation(c, request.state.identity, cid)
            page = privacy.page(
                c,
                request.state.identity,
                cid,
                since=views.since_time(since),
                before=before,
                **views.page_options(limit, max_bytes),
            )
            turn_work.annotate(c, auth, request.state.identity, page["messages"])
            visual_view(c, request.state.identity, page["messages"])
            known = teammate_access.Known(c, request.state.identity, auth)  # people the reader may not see
            return {
                "conversation": known.conversation(H.conversation(c, cid)),
                **page,
                "messages": known.messages(page["messages"]),
            }

    @app.get("/api/v2/conversations/{cid}/outline")
    def outline(request: Request, cid: str):
        """The humans' prompts in a chat, oldest first, for the Outline list: id, time, a short excerpt and the task
        it names. It reaches past the loaded pages, so each row passes the same message and task checks a page does."""
        who = request.state.identity
        with store.read() as c:
            auth.conversation(c, who, cid)
            prompts = []
            for row in c.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND sender_id LIKE 'human:%' "
                "AND deleted_at IS NULL ORDER BY rowid DESC",
                (cid,),
            ):
                refs = H._json(row["refs"], {}) or {}
                if (
                    refs.get("goal_action")
                    or refs.get("maintenance")
                    or not privacy.message_readable(c, privacy.actor(who), row)
                ):
                    continue
                task, tid = None, H.refs_task_id(refs)
                if tid and (t := H.task(c, tid)) and privacy.task_readable(c, who, t):
                    task = {"id": tid, "title": t["title"]}
                text = " ".join(str(row["body"] or "").split())
                prompts.append(
                    {
                        "id": row["id"],
                        "created_at": row["created_at"],
                        "task": task,
                        "text": text if len(text) <= 140 else text[:139].rstrip() + "…",
                    }
                )
                if len(prompts) >= 500:
                    break
            return {"prompts": prompts[::-1]}
