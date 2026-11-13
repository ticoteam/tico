"""Task comment and reader routes: comment writes, tree, links, readers."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from . import bot_to_bot
from . import counsel
from . import delivery
from . import org_rule_check
from . import task_privacy as privacy
from . import teammate_access
from . import visuals


def register(
    app,
    auth,
    comment_bot,
    comment_way_for,
    mover,
    mutate,
    rule_check,
    rule_comment_audience,
    send,
    settings,
    store,
    task_place,
    visual_view,
):
    def replying_to(c, who, task_id, body):
        """A reply to a bot's question (`ask`): the bot that asked it, and whether the question waits on the writer.
        (None, False) for a plain comment. 422 when `ask` is not a bot's question on this task's open visit."""
        if not body.ask:
            return None, False
        if body.public or body.delivery == "goal":
            raise Problem("validation", "A reply to an ask is a plain comment: send it without public or goal", 422)
        found = c.execute(
            "SELECT r.added_by_id, r.person_id FROM requirements r JOIN visits v ON v.id=r.visit_id "
            "WHERE r.id=? AND v.task_id=? AND v.left_at IS NULL AND r.kind='answer' AND r.withdrawn_at IS NULL",
            (body.ask, task_id),
        ).fetchone()
        if not found or not H.is_bot(found[0]):
            raise Problem("validation", "That is not a bot's question on this task", 422)
        return found[0], found[1] == who.actor

    def comment_write(c, who, tid, body, ingest=None, rc=None, reader=None):
        """One task comment, as `POST /api/v2/tasks/{tid}/comments` writes it and Send anyway replays it."""
        reader = reader or who
        mark = H.jobs_mark(c)
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        from .chat_goals import may_direct, own_room, set_goal
        from .task_reads import comment_rights

        comment_rights(c, auth, who, task_id)
        asker, asked_me = replying_to(c, who, task_id, body)
        # The org rule check (backend/org_rule_check.py), applied before anything is written: held, nothing is.
        audience = rule_comment_audience(c, who, row, body.text) if rc is not None else None
        rule = org_rule_check.apply(
            c,
            rc,
            body.text,
            {"task": task_id, "body": body.model_dump(mode="json", by_alias=True)},
            audience_now=audience,
        )
        if rule and rule.get("moved"):
            raise Problem("moved", "Who this goes to changed while it was checked; send it again", 409, retryable=True)
        if rule and rule.get("held"):
            return org_rule_check.held_answer(c, who, rule, auth=auth)
        text = org_rule_check.warned_text(rule, body.text)
        attached, report = visuals.attach(c, who, app.state.files, app.state.blobs, body.attachments, task_id=task_id)
        # A comment reaches its @mentions, the author it replies to and the task's followers (threads.plan). Steer and
        # queue wake the bots among them, so they take the right to direct the task (a bot's is its needs_action);
        # anyone may defer. Defer and goal wake none: a goal's own control does (set_goal below).
        bot = asker or comment_bot(c, row)
        # The person a bot's question waits on may wake that bot with a reply to it, as they may answer it.
        directs = who.role == "bot" or asked_me or may_direct(c, who, row)
        if body.delivery in ("steer", "queue") and bot and not directs:
            raise Problem("delivery", "Only someone on this task can wake its bots: send it with defer", 403)
        if body.delivery == "goal" and who.role != "bot" and not may_direct(c, who, row):
            raise Problem("delivery", "Only someone on this task can set its goal: send it with defer", 403)
        # A goal is set in the task's own room only. In a chat the task was filed into, the goal is the chat's
        # (chat_goals.own_room), so nothing is saved and no control is queued or canceled there.
        if body.delivery == "goal" and not own_room(c, row):
            raise Problem("delivery", "This task has no room of its own: send it with steer or queue", 409)
        defer_for = body.defer_for
        if body.delivery in ("steer", "queue", "defer"):
            way = body.delivery
        elif body.delivery == "goal":
            way, defer_for = "defer", delivery.NEVER
        else:
            way, defer_for = comment_way_for(c, who, row)
        # A Defer never the reader did not choose (none named, or a defer with no deadline) binds the bots only:
        # people following are notified as their level says (threads.plan).
        people = way == "defer" and not directs and body.defer_for is None
        if way == "defer" and bot and not directs:
            # A deadline wakes the bot as a steer would, so it takes the same right. Someone off the task defers
            # with none: their comment waits for the bots' next turn here.
            if body.defer_for not in (None, delivery.NEVER):
                raise Problem(
                    "delivery",
                    "Only someone on this task can give its bots a deadline: send it with defer and no defer_for",
                    403,
                )
            defer_for = delivery.NEVER
        # Ride-along: a defer someone chose while the bot works this task goes in right after that turn, if they
        # may wake it.
        if (
            way == "defer"
            and bot
            and body.delivery == "defer"
            and directs
            and delivery.running_in_session(c, H.actor_id(bot), [row["conversation_id"]], task_id)
        ):
            way = "queue"
        extra = {"attachments": attached, "files": [a["ref"] for a in attached]} if attached else {}
        if body.needs_action or (who.role == "bot" and body.delivery in ("steer", "queue")):
            extra["needs_action"] = True
        msg = H.task_comment(
            c,
            who.actor,
            task_id,
            text,
            wake=way != "defer",
            public=body.public,
            approval_id=body.approval_id,
            extra_refs=extra or None,
            way=way,
            defer_for=defer_for,
            people_notified=people,
            delegate=directs and way != "defer",
            reach=who if who.role == "bot" else None,
            to=asker,
        )
        org_rule_check.late_on(c, rule, "message", message=msg["id"], task=task_id)
        due = None
        # The bot the comment went to first: the follower it was for, or the bot its @mention reached.
        reached = msg.get("recipient_id") if H.is_bot(msg.get("recipient_id")) else None
        bot = bot or reached
        if way == "defer" and reached and (msg.get("refs") or {}).get("delivery") == "queue":
            way = "queue"  # Defer never with an @mention: the bot it names is reached, queued (D6)
        if way == "defer":
            found = c.execute(
                "SELECT due_at FROM deferrals WHERE item=? OR item IN (SELECT carrier_id FROM "
                "message_deliveries WHERE message_id=? AND carrier_id IS NOT NULL) LIMIT 1",
                (msg["id"], msg["id"]),
            ).fetchone()
            due = found[0] if found else None
        goal = None
        if body.delivery == "goal":
            # The task session's goal, set as a chat's is (chat_goals.set_goal): its /goal control wakes the bot.
            goal = set_goal(c, auth, who, row["conversation_id"], "set", body.text, send)
        for item in attached:
            c.execute(
                "INSERT OR IGNORE INTO task_file_reviews(file_id,version) VALUES(?,?)", (item["id"], item["version"])
            )
            c.execute(
                "UPDATE task_file_reviews SET comment_id=coalesce(comment_id,?) WHERE file_id=? AND version=?",
                (msg["id"], item["id"], item["version"]),
            )
        comments = visual_view(c, who, H.task_comments(c, task_id, actor=privacy.actor(reader)))
        msg = next(m for m in comments if m["id"] == msg["id"])
        held = H.held_note(c, msg.get("recipient_id"))
        waits = bot_to_bot.held_reason(msg)
        if bot_to_bot.held_reason(msg):
            way, due = "defer", None  # a bot's plain comment to a bot waits for its next run
        if body.delivery == "goal":
            way = None
        return {
            "comment": msg,
            "comments": comments,
            **H.comment_delivery(c, msg, since=mark),
            **({"sent_as": delivery.sent_as(way, due)} if bot and way else {}),
            **({"warning": held} if held else {}),
            **({"held": waits} if waits else {}),
            **({"visuals": report} if report else {}),
            **({"goal": goal} if goal else {}),
            **org_rule_check.answer(rule),
        }

    @org_rule_check.replayer("comment")
    def rule_replay_comment(c, bot, hold, replay, ingest, gate=None):
        return comment_write(
            c, bot, replay["task"], M.TaskComment.model_validate(replay["body"]), ingest, org_rule_check.decided(gate)
        )

    @app.post("/api/v2/tasks/{tid}/comments")
    def task_comment(request: Request, tid: str, body: M.TaskComment):
        """A comment on a task. `delivery` says how it reaches the task's bot (backend/delivery.py): steer into its
        running turn, queue right after it, defer to its next turn on the task (at the latest `defer_for`, 24h), or
        goal: the task session's goal becomes the text (chat_goals.set_goal). Omitted, it steers (D13), or defers with no
        deadline for a reader who may not direct the task's bots. It reaches the task's followers and whom it addresses
        (threads.plan). A question to a person is an ask on the task (`POST /api/v2/tasks/{tid}/asks`), never a
        comment."""
        who = request.state.identity
        counsel.no_attachments(who, body.attachments)  # before the rule check holds anything (visuals.attach too)
        if body.delivery and body.needs_action and body.delivery != "steer":
            raise Problem("delivery", "needs_action is steer: send it without a delivery, or with steer", 422)
        if wrong := delivery.check(body.delivery, body.defer_for):
            raise Problem("delivery", wrong, 422)
        if body.delivery == "goal" and (body.attachments or body.public):
            raise Problem("delivery", "A goal is the comment's text: send it without files or as a public comment", 422)
        rc = rule_check(
            who,
            "comment",
            body.text,
            lambda c: rule_comment_audience(c, who, auth.task(c, who, auth.resolve_task(c, who, tid)), body.text),
            place=task_place(who, tid),
            late=True,
        )
        result = mutate(request, body, lambda c: comment_write(c, who, tid, body, None, rc, request.state.identity))
        if body.attachments:
            app.state.file_metadata.wake.set()  # a chat file shared here is a new task copy: preview it now
        return result

    @app.get("/api/v2/tasks/{tid}/comments")
    def task_comments(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {
                "comments": visual_view(
                    c, request.state.identity, H.task_comments(c, task_id, actor=privacy.actor(request.state.identity))
                )
            }

    @app.get("/api/v2/tasks/{tid}/tree")
    def task_tree(request: Request, tid: str):
        with store.read() as c:
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            ids = [r["id"] for r in H.descendants(c, task_id)]
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            visible_ids = {
                r[0]
                for r in c.execute(
                    f"SELECT id FROM tasks WHERE id IN ({marks}) AND (" + auth.task_sql(c, who) + ")", ids
                )
            }
            return H.task_tree(c, task_id, visible_ids)

    @app.get("/api/v2/tasks/{tid}/links")
    def get_task_links(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {"links": H.task_links(c, task_id)}

    def readers_answer(c, who, task_id):
        row = H.task(c, task_id)
        private = H.task_private(c, row)
        view = teammate_access.Known(c, who, auth).task({**row, "readers": H.task_readers(row) if private else []})
        return {
            "private": private,
            "requester_id": view["requester_id"],
            "owner_id": view["owner_id"],
            "readers": view["readers"],
        }

    @app.get("/api/v2/tasks/{tid}/readers")
    def get_task_readers(request: Request, tid: str):
        """Who can see the task: on a private one its requester, owner and reader list (docs/tasks.md)."""
        with store.read() as c:
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            return readers_answer(c, who, task_id)

    def change_readers(request, tid, body, what, **change):
        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            if who.via == "botops" and not who.confirmed:
                # BotOps acting for a person changes who sees a task only on that person's own tap (a Confirm card).
                return app.state.botops_propose(
                    c,
                    who,
                    request.method,
                    f"/api/v2/tasks/{tid}/readers",
                    body.model_dump(exclude_none=True),
                    what + " “" + H.task(c, task_id)["title"] + "”",
                )
            H.set_task_readers(
                c,
                who.actor,
                task_id,
                mover=mover(c, who) or None,
                check=lambda added: privacy.require_readers(c, who, task_id, added, task_id),
                **change,
            )
            return readers_answer(c, who, task_id)

        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/readers")
    def task_readers(request: Request, tid: str, body: M.TaskReaders):
        """Add and remove who else sees a private task; each change is in its history."""
        return change_readers(request, tid, body, "Change who sees", add=body.add, remove=body.remove)

    @app.put("/api/v2/tasks/{tid}/readers")
    def put_task_readers(request: Request, tid: str, body: M.TaskReaderList):
        """Replace the reader list of a private task."""
        return change_readers(request, tid, body, "Change who sees", replace=body.readers)
