"""Task comment and reader routes: comment writes, answers, tree, links, readers."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from . import ask_ingest
from . import bot_to_bot
from . import counsel
from . import delivery
from . import org_rule_check
from . import task_privacy as privacy
from . import question_tasks
from . import teammate_access
from . import visuals


def register(
    app,
    auth,
    checked_write,
    comment_bot,
    comment_way_for,
    mover,
    mutate,
    readable_task,
    rule_check,
    rule_comment_audience,
    rule_task_audience,
    saved_retry,
    send,
    settings,
    store,
    task_place,
    visual_view,
):
    def comment_write(c, who, tid, body, ingest=None, rc=None, reader=None):
        """One task comment, as `POST /api/v2/tasks/{tid}/comments` writes it and Send anyway replays it."""
        reader = reader or who
        mark = H.jobs_mark(c)
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        from .chat_goals import may_direct, own_room, set_goal
        from .task_review import comment_rights, check_ask

        comment_rights(c, auth, who, task_id)
        ask = check_ask(c, task_id, body.ask, auth, who)
        # The org rule check (backend/org_rule_check.py), applied before anything is written: held, nothing is.
        audience = rule_comment_audience(c, who, row, body.text, ask) if rc is not None else None
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
            if ingest:
                ask_ingest.skipped(c, ingest, "held")
            return org_rule_check.held_answer(c, who, rule, auth=auth)
        text = org_rule_check.warned_text(rule, body.text)
        attached, report = visuals.attach(c, who, app.state.files, app.state.blobs, body.attachments, task_id=task_id)
        moves = mover(c, who)
        # A comment reaches its @mentions, the author it replies to and the task's followers (threads.plan). Steer and
        # queue wake the bots among them, so they take the right to direct the task (a bot's is its needs_action);
        # anyone may defer. Defer and goal wake none: a goal's own control does (set_goal below).
        bot = comment_bot(c, row)
        directs = who.role == "bot" or may_direct(c, who, row)
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
        elif ask:
            way = "steer" if who.role == "bot" or H.task_participant(c, who.actor, row) or moves else "defer"
        else:
            way, defer_for = comment_way_for(c, who, row)
        # A Defer never the reader did not choose (none named, or a defer with no deadline) binds the bots only:
        # people following are notified as their level says (threads.plan).
        people = way == "defer" and not ask and not directs and body.defer_for is None
        if way == "defer" and bot and not ask and not directs:
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
            and not ask
            and body.delivery == "defer"
            and directs
            and delivery.running_in_session(c, H.actor_id(bot), [row["conversation_id"]], task_id)
        ):
            way = "queue"
        extra = {"attachments": attached, "files": [a["ref"] for a in attached]} if attached else {}
        if body.needs_action or (who.role == "bot" and body.delivery in ("steer", "queue")):
            extra["needs_action"] = True
        # Judged against the plate before the question is on it.
        gate = (
            ask_ingest.apply(c, ingest)
            if ingest and ask and ask_ingest.matches(ingest, H.task_ask_recipient(c, who.actor, row, ask))
            else None
        )
        msg = H.task_comment(
            c,
            who.actor,
            task_id,
            text,
            wake=way != "defer",
            ask=ask,
            public=body.public,
            approval_id=body.approval_id,
            extra_refs=extra or None,
            way=None if ask else way,
            defer_for=defer_for,
            people_notified=people,
            delegate=directs and way != "defer",
            reach=who if who.role == "bot" else None,
        )
        due = None
        # The bot the comment went to first: the follower it was for, or the bot its @mention reached.
        reached = msg.get("recipient_id") if H.is_bot(msg.get("recipient_id")) else None
        bot = bot or reached
        if way == "defer" and not ask and reached and (msg.get("refs") or {}).get("delivery") == "queue":
            way = "queue"  # Defer never with an @mention: the bot it names is reached, queued (D6)
        if way == "defer" and not ask:
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
        if gate and not ask_ingest.matches(ingest, msg.get("recipient_id")):
            gate = None
            ask_ingest.skipped(c, ingest, "target")
        elif ingest and not gate:
            ask_ingest.skipped(c, ingest, "not_applied")
        if gate:
            ask_ingest.record(c, ingest, gate, task_id, msg["id"])
            if gate["acts"]:
                H.task_ask_resolve(c, who.actor, task_id, msg["id"])
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
            **ask_ingest.reply(gate),
            **org_rule_check.answer(rule),
        }

    def comment_ingest(who, tid, body):
        if not body.ask or not ask_ingest.gated(who, settings.assistant_bot) or not (row := readable_task(who, tid)):
            return None
        with store.read() as c:
            asked = H.task_ask_recipient(c, who.actor, row, body.ask.model_dump())
        return ask_ingest.preflight(
            store,
            app.state.judge,
            who,
            asked,
            row["title"],
            body.text,
            kind="question",
            asked=[q.model_dump() for q in body.ask.questions],
        )

    def rule_prepare_comment(store_, bot, replay):
        return comment_ingest(bot, replay["task"], M.TaskComment.model_validate(replay["body"]))

    @org_rule_check.replayer("comment", prepare=rule_prepare_comment)
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
        (threads.plan). A bot's
        question to a person passes ask ingest first (backend/ask_ingest.py): a refused write rolls back before it
        is asked."""
        who = request.state.identity
        counsel.no_attachments(who, body.attachments)  # before the rule check holds anything (visuals.attach too)
        if body.delivery and (body.ask or (body.needs_action and body.delivery != "steer")):
            raise Problem(
                "delivery",
                "A question always wakes the one it is for, and needs_action is steer: send it "
                "without a delivery, or with steer",
                422,
            )
        if wrong := delivery.check(body.delivery, body.defer_for):
            raise Problem("delivery", wrong, 422)
        if body.delivery == "goal" and (body.attachments or body.public):
            raise Problem("delivery", "A goal is the comment's text: send it without files or as a public comment", 422)
        rc = rule_check(
            who,
            "comment",
            body.text,
            lambda c: rule_comment_audience(
                c,
                who,
                auth.task(c, who, auth.resolve_task(c, who, tid)),
                body.text,
                body.ask.model_dump() if body.ask else None,
            ),
            place=task_place(who, tid),
            track_route=body.ask is not None,
        )
        ingest = None if org_rule_check.holds(rc) or saved_retry(request, who) else comment_ingest(who, tid, body)
        result = ask_ingest.judged(
            store,
            ingest,
            lambda: mutate(
                request, body, lambda c: comment_write(c, who, tid, body, ingest, rc, request.state.identity)
            ),
        )
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

    @app.get("/api/v2/tasks/{tid}/answers")
    def task_answers(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            who = request.state.identity
            row = auth.task(c, who, task_id)
            answers = [m["answer"] for m in H.task_comments(c, task_id, actor=privacy.actor(who)) if m.get("answer")]
            # A question task's own answer (backend/question_tasks.py) is on the task, not in a comment. Words it copies
            # from a page this reader may not open are redacted, as /api/v2/answers does.
            own = question_tasks.view_fields(row).get("answer")
            from .files import readable_page_refs

            return readable_page_refs(c, auth, who, {"answers": answers + ([own] if own else [])}, task_id=task_id)

    def task_answer_write(c, who, tid, body, warn=""):
        from .task_review import answer_task

        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        wake = who.role == "bot" or H.task_participant(c, who.actor, row) or mover(c, who)
        return answer_task(c, auth, who, task_id, body, wake=wake, warn=warn)

    def task_answer_text(body):
        """What a task answer says, as the rule check reads it: each question's picks and the free text."""
        if body.dismiss:
            return ""
        with store.read() as c:
            asked = H.answer_question(c, body.target.model_dump(exclude_none=True))
            return H.answer_text(asked, body.answers, other=body.other)[0]

    @app.post("/api/v2/tasks/{tid}/answers")
    def task_answer(request: Request, tid: str, body: M.TaskAnswer):
        who = request.state.identity

        def write(c, gate):
            return task_answer_write(c, who, tid, body, org_rule_check.warn_line(gate))

        return checked_write(
            request,
            body,
            who,
            "task_answer",
            task_answer_text(body),
            lambda c: rule_task_audience(c, auth.task(c, who, auth.resolve_task(c, who, tid)), who.actor),
            write,
            {"task": tid, "body": body.model_dump(mode="json", by_alias=True)},
        )

    @org_rule_check.replayer("task_answer")
    def rule_replay_task_answer(c, bot, hold, replay, ingest, gate=None):
        # Its route's guard (`/tasks/{tid}/answers`: the task's readers may read the run's private work) ran in send.
        return task_answer_write(
            c, bot, replay["task"], M.TaskAnswer.model_validate(replay["body"]), org_rule_check.warn_line(gate)
        )

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
