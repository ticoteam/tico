"""Task comment and reader routes: comment writes, tree, links, readers."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from . import ask_known
from . import ask_shape
from . import bot_to_bot
from . import counsel
from . import delivery
from . import org_rule_check
from . import question_marks
from . import task_privacy as privacy
from . import teammate_access
from . import visuals
from .api_contract import ASK_REFUSED, QUESTION_REFUSED, api_v2
from .api_answers.tasks import CommentResult, TaskComments


# What a bot is told when it comments on its own task during its turn (decision (c): a bot talks one way).
OWN_TURN = (
    "Your reply at the end of this turn is posted on this task: say it there. "
    'To ask a person, mark a ❓: tico task ask <id> --to <person> "<question>".'
)


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
        A person's plain comment on a task where a bot's ❓ waits on them is their reply to it (writing it clears the ❓,
        hubdb). (None, False) for any other plain comment. 422 when `ask` is not a bot's open ❓ on this task."""
        if not body.ask:
            if who.role == "bot" or body.public or body.delivery == "goal":
                return None, False
            mine = c.execute(
                "SELECT bot_id FROM question_marks WHERE task_id=? AND person_id=? AND cleared_at IS NULL "
                "ORDER BY created_at LIMIT 1",
                (task_id, who.actor),
            ).fetchone()
            return (mine[0], True) if mine else (None, False)
        if body.public or body.delivery == "goal":
            raise Problem("validation", "A reply to an ask is a plain comment: send it without public or goal", 422)
        marked = question_marks.get(c, question_marks.mark_id(body.ask) or body.ask)
        if marked and marked["task_id"] == task_id and not marked.get("cleared_at"):
            # A reply to a bot's ❓ on this task: to the bot that marked it. Writing it clears the ❓ (hubdb).
            return marked["bot_id"], marked["person_id"] == who.actor
        raise Problem("validation", "That is not a bot's question on this task", 422)

    def comment_write(c, who, tid, body, ingest=None, rc=None, reader=None, ask_to=None):
        """One task comment, as `POST /api/v2/tasks/{tid}/comments` writes it and Send anyway replays it. `ask_to`: the
        person a ❓ on it will ask (`question_write`), who is in its audience too."""
        reader = reader or who
        mark = H.jobs_mark(c)
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        from .chat_goals import may_direct, own_room, set_goal
        from .task_reads import comment_rights

        comment_rights(c, auth, who, task_id)
        asker, asked_me = replying_to(c, who, task_id, body)
        if ask_to:  # who a ❓ asks is checked before its rule verdict: may they be asked at all
            question_marks.check_asking(c, who.actor, task_id, ask_to, body.text)
        # The org rule check (backend/org_rule_check.py), applied before anything is written: held, nothing is.
        extra = (ask_to,) if ask_to else ()
        audience = rule_comment_audience(c, who, row, body.text, *extra) if rc is not None else None
        rule = org_rule_check.apply(
            c,
            rc,
            body.text,
            {
                "task": task_id,
                "body": body.model_dump(mode="json", by_alias=True),
                **({"to": ask_to} if ask_to else {}),
            },
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
        directs = who.role == "bot" or may_direct(c, who, row)
        # The person a bot's ❓ waits on wakes that bot with their reply, as they may answer it, and that bot only: the
        # comment reaches everyone else as a Defer never would, and only the asker takes the delegation. Unless they
        # chose Defer for it too.
        answers = asked_me and not directs and H.is_bot(asker) and body.delivery not in ("defer", "goal")
        if body.delivery in ("steer", "queue") and bot and not directs and not answers:
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
        if answers:
            way, defer_for = "defer", delivery.NEVER
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
        if answers and asker:
            # The asker's own delivery was deferred with the rest: its run starts now and carries it.
            delivery.now(c, asker, row["conversation_id"])
            H.delegate(c, task_id, asker, who.actor, msg["id"])
            if asker == row["owner_id"]:
                from . import task_engine

                task_engine.person_spoke(c, task_id, who.actor)
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
        if answers:
            way, due = body.delivery or "steer", None  # how it reached the bot it answers
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

    def own_turn(c, who, tid):
        """Refuse a bot's comment on its own open task from a turn on that task: what it says goes in its reply."""
        task_id = auth.resolve_task(c, who, tid)
        row = H.task(c, task_id)
        found = c.execute("SELECT task_id FROM attempts WHERE id=?", (who.attempt_id,)).fetchone()
        if row and found and found[0] == task_id and row["owner_id"] == who.actor and row["status"] == "open":
            raise Problem("own_turn", OWN_TURN, 409)

    @org_rule_check.replayer("comment")
    def rule_replay_comment(c, bot, hold, replay, ingest, gate=None):
        return comment_write(
            c, bot, replay["task"], M.TaskComment.model_validate(replay["body"]), ingest, org_rule_check.decided(gate)
        )

    # Before POST: the API document lists a path's methods in the order they are registered.
    @app.get(
        "/api/v2/tasks/{tid}/comments",
        **api_v2("Tasks", "listTaskComments", "Comments and questions with their answers", TaskComments),
    )
    def task_comments(request: Request, tid: str):
        with store.read() as c:
            task_id = auth.resolve_task(c, request.state.identity, tid)
            auth.task(c, request.state.identity, task_id)
            return {
                "comments": visual_view(
                    c, request.state.identity, H.task_comments(c, task_id, actor=privacy.actor(request.state.identity))
                )
            }

    @app.post(
        "/api/v2/tasks/{tid}/comments",
        **api_v2("Tasks", "commentOnTask", "Comment on a task", CommentResult, responses={"422": ASK_REFUSED}),
    )
    def task_comment(request: Request, tid: str, body: M.TaskComment):
        """A comment on a task. `delivery` says how it reaches the task's bot (backend/delivery.py): steer into its
        running turn, queue right after it, defer to its next turn on the task (at the latest `defer_for`, 24h), or
        goal: the task session's goal becomes the text (chat_goals.set_goal). Omitted, it steers (D13), or defers with no
        deadline for a reader who may not direct the task's bots. It reaches the task's followers and whom it addresses
        (threads.plan). A question to a person is a ❓ (`POST /api/v2/tasks/{tid}/questions`). A bot does not comment
        on its own open task during its turn there: its reply at the end of the turn is posted on the task (`OWN_TURN`);
        a comment that carries files still goes."""
        who = request.state.identity
        if who.role == "bot" and who.attempt_id and not body.attachments:
            with store.read() as c:
                own_turn(c, who, tid)
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

    @app.post(
        "/api/v2/tasks/{tid}/questions",
        **api_v2(
            "Tasks",
            "askOnTaskChat",
            "A bot's ❓: comment on the task's chat and mark it as needing one person's reply (`to`, `text`). It is "
            "listed in their Needs you until they reply there or the task ends; no task is filed and nothing else is "
            "sent. One open ❓ per bot per chat: a new one replaces the old, and `warning` says so. Bots only",
            CommentResult,
            responses={"409": QUESTION_REFUSED},
        ),
    )
    def task_question(request: Request, tid: str, body: M.Question):
        """A bot's ❓ on the task's chat (backend/question_marks.py): its comment, marked as needing `to`'s reply, in one
        step. The comment is checked as a question to a person (the org rules' `kind:ask`); the person must be able to
        read the chat. No task is filed and nothing is sent for it. One Tico can answer from what it holds (the task's
        repository, the bot's access to it, its app view: backend/ask_known.py) asks nobody and the bot gets the answer;
        a "no" only a person can change still asks, with a "Tico checked:" sentence added; `not_known` skips this."""
        who = request.state.identity
        if who.role != "bot":
            raise Problem("forbidden", "Only a bot marks a message ❓", 403)
        known, person, number = None, "", ""
        with store.read() as c:
            to = auth.target(c, who, body.to)  # who it asks: checked by the rules for them too, as a recipient
            if not body.not_known and H.is_human(to) and ask_known.topics(body.text):
                try:
                    task_id = auth.resolve_task(c, who, tid)
                    auth.task(c, who, task_id)
                except Problem:
                    task_id = None  # the ❓ itself is refused below as it always was
                if task_id:
                    known = ask_known.read_now(c, who, auth, task_id, body.text)
                    person = H.actor_name(c, to) or "They"
                    numbered = c.execute("SELECT number FROM tasks WHERE id=?", (task_id,)).fetchone()
                    number = (numbered["number"] if numbered else None) or task_id
        if known and known.answered:
            answer = known.answer(person, number)

            def answered(c):
                H.history_event(
                    c,
                    "task",
                    auth.resolve_task(c, who, tid),
                    who.actor,
                    "ask_known",
                    new=",".join(known.topics),
                    data={"to": to},
                )
                return {"asked": False, "answered_by_tico": answer, "known": [f.text for f in known.facts]}

            return mutate(request, body, answered)
        text = f"{body.text}\n\n{known.checked_line()}" if known else body.text
        if shape := ask_shape.line_refusal(body.text):  # in plain code, before the rule check's call
            mutate(
                request,
                body,
                lambda c: H.refuse(c, who.actor, "task_ask_shape", shape, task=auth.resolve_task(c, who, tid)),
            )
        rc = rule_check(
            who,
            "question",
            text,
            lambda c: rule_comment_audience(c, who, auth.task(c, who, auth.resolve_task(c, who, tid)), text, to),
            place=question_marks.Asking(task_place(who, tid)),
        )

        def write(c):
            done = question_write(c, who, tid, text, to, rc)
            if body.not_known and (done.get("comment") or {}).get("id"):
                # Counted: Tico's answer did not cover it (Tasks spec criterion 12).
                H.history_event(c, "task", auth.resolve_task(c, who, tid), who.actor, "ask_not_known", data={"to": to})
            return done

        return mutate(request, body, write)

    # An older client's question on the asks route is asked here (task_moves_routes.task_ask).
    app.state.task_question = task_question

    def question_write(c, who, tid, text, to, rc, ingest=None):
        """A ❓ on a task's chat, as the route writes it and Send anyway replays it: the comment, then its mark, in one
        transaction. A Block or Escalate rule it breaks refuses it back to the bot, nothing written or held
        (task_rule_gate.refuse_question); a hold from before still replays here."""
        done = comment_write(c, who, tid, M.TaskComment(text=text), ingest, rc, who, ask_to=to)
        said = done.get("comment") or {}
        if not said.get("id"):
            return done
        mark = question_marks.mark(c, who.actor, said["id"], to)
        return question_marks.with_warning({**done, "question": question_marks.public(mark)}, mark)

    def rule_question_audience(c, bot, replay):
        row = auth.task(c, bot, auth.resolve_task(c, bot, replay["task"]))
        return rule_comment_audience(c, bot, row, replay["body"].get("text") or "", replay["to"])

    @org_rule_check.replayer("question", audience=rule_question_audience)
    def rule_replay_question(c, bot, hold, replay, ingest, gate=None):
        text = replay["body"].get("text") or ""
        return question_write(c, bot, replay["task"], text, replay["to"], org_rule_check.decided(gate), ingest)

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
            from . import api_tools

            return {"links": H.task_links(c, task_id, api_tools.connections_seen(c, request.state.identity, auth))}

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
                # Agent Ops acting for a person changes who sees a task only on that person's own tap (a Confirm card).
                return app.state.agent_ops_propose(
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
