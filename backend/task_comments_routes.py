"""Task comment and reader routes: comment writes, tree, links, readers."""

from .store import H
from . import models as M
from .store import Problem
from contextlib import nullcontext
from typing import Any

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
    def replying_to(c, who, task_id, body, session=None):
        """A reply to a bot's question (`ask`): the bot that asked it, and whether the question waits on the writer.
        A person's plain comment on a task where a bot's ❓ waits on them is their reply to it (writing it clears the ❓,
        hubdb). (None, False) for any other plain comment. 422 when `ask` is not a bot's open ❓ on this task.
        The person a ❓ waits on answering it by name (its card) answers it in this same write, so only while it is
        open (409 `answered` after: a second tap sends nothing, and Send anyway of a held answer checks again), only in
        their own hand (`question_marks.by_hand`: never Agent Ops or an automation writing as them), and it clears
        that ❓ alone (`answer_of`)."""
        if not body.ask:
            if who.role == "bot" or body.public or body.delivery == "goal":
                return None, False
            # A message in one agent's session answers only that agent's ❓ (question_marks.replied).
            only = " AND bot_id=?" if session else ""
            mine = c.execute(
                "SELECT bot_id FROM question_marks WHERE task_id=? AND person_id=? AND cleared_at IS NULL"
                f"{only} ORDER BY created_at LIMIT 1",
                (task_id, who.actor, *((session,) if session else ())),
            ).fetchone()
            return (mine[0], True) if mine else (None, False)
        if body.public or body.delivery == "goal":
            raise Problem("validation", "A reply to an ask is a plain comment: send it without public or goal", 422)
        marked = question_marks.get(c, question_marks.mark_id(body.ask) or body.ask)
        if marked and marked["task_id"] == task_id and marked["person_id"] == who.actor:
            if marked.get("cleared_at"):
                raise Problem("answered", question_marks.ANSWERED, 409)
            if not question_marks.by_hand():
                raise Problem("validation", "Only the person asked answers a ❓, in their own hand", 422)
            return marked["bot_id"], True
        if marked and marked["task_id"] == task_id and not marked.get("cleared_at"):
            # A reply to a bot's ❓ on this task: to the bot that marked it. Writing it clears the ❓ (hubdb).
            return marked["bot_id"], marked["person_id"] == who.actor
        raise Problem("validation", "That is not a bot's question on this task", 422)

    def askers(c, who, task_id, body, session=None):
        """Every agent whose ❓ this comment settles, as the write clears them (question_marks.replied) and delivers to
        each (threads.deliver): the one `ask` names; else, for a person's plain comment, each agent's open ❓ to them on
        the task (only `session`'s in a session message). The org rules read the same list."""
        asker, _ = replying_to(c, who, task_id, body, session)
        if body.ask or not asker:
            return [asker] if asker else []
        only = " AND bot_id=?" if session else ""
        return [
            str(r[0])
            for r in c.execute(
                "SELECT DISTINCT bot_id FROM question_marks WHERE task_id=? AND person_id=? AND cleared_at IS NULL"
                f"{only} ORDER BY bot_id",
                (task_id, who.actor, *((session,) if session else ())),
            )
        ]

    def reply_target(c, who, row, body):
        """The message a Reply answers (`in_reply_to`): one on this task the writer reads, else 422."""
        if not body.in_reply_to:
            return None
        msg = H.comment_on(c, row, body.in_reply_to)
        try:
            if msg and not msg.get("deleted_at"):
                privacy.require_message(c, who, msg)
                return msg
        except Problem:
            pass
        raise Problem("validation", "That is not a message on this task", 422)

    def session_target(c, who, row, body):
        """The agent a session message (`to`) is written to: an active agent the writer may see that reads the task,
        else 422 (the same answer whether or not it exists). A person who may not direct the task's agents gets 403
        first, so they learn nothing about which agents exist or read it; a writer without Write on the agent gets the
        403 any send to it gets."""
        if not body.to:
            return None
        from .chat_goals import may_direct

        if who.role != "bot" and not may_direct(c, who, row):
            raise Problem("delivery", "Only someone on this task can message its agents", 403)
        if body.public or body.ask or body.delivery == "goal" or body.in_reply_to:
            raise Problem(
                "validation",
                "A message to an agent's session is plain: send it without public, ask, goal or in_reply_to",
                422,
            )
        agent = H.resolve_actor(c, body.to)
        found = H.bot(c, H.actor_id(agent)) if agent and H.is_bot(agent) else None
        if (
            not found
            or found.get("state") not in ("active", "paused", "quarantined")  # a held agent gets it on release
            or teammate_access.Known(c, who, auth).unseen_of([agent])
            or not H.task_private_readable(c, agent, row)
        ):
            raise Problem("validation", "No agent by that name can read this task", 422)
        # A session message is a request to the agent, as a message to it anywhere is (recipients.require_recipient):
        # Write on it, else the same 403 every send gives. Being on the task (may_direct) is not Write on its agents.
        auth.require_write(c, who, H.actor_id(agent), missing="No agent by that name can read this task")
        return agent

    def recipients(c, who, row, body, session, replied, asked):
        """Everyone a comment is written to, once its `to` (`session`), Reply (`replied`) and ❓ answers (`asked`, the
        askers it settles) are checked: that agent, the author it answers, each asker, and whom its text @mentions,
        agents and people. The org rule check's audience reads this one list (`comment_audience`) in the preflight, the
        write and Send anyway's replay."""
        from . import threads

        out = [session, *asked]
        if replied and str(replied.get("sender_id") or "").startswith(("human:", "bot:")):
            out.append(replied["sender_id"])
        conv = H.conversation(c, row["conversation_id"]) if row.get("conversation_id") else None
        if conv and body.text:
            out += [a for a, _ in threads.mentions(c, conv, body.text, author=who.actor)]
        return [a for a in dict.fromkeys(out) if a and a != who.actor]

    def addressed_bot(c, who, row, text, replied):
        """The first bot a comment addresses (threads.plan): one it @mentions that reads the task, or the bot a
        person's Reply answers. A plain comment addresses nobody. On a task with no thread of its own (filed into a
        shared chat) every comment goes to its holder bot (hubdb.task_comment)."""
        from . import task_followers, threads

        conv = H.conversation(c, row["conversation_id"]) if row.get("conversation_id") else None
        if not conv:
            return None
        if not task_followers.thread_id(c, row):
            owner = row["owner_id"]
            return owner if H.is_bot(owner) and owner != who.actor else None
        found = threads.with_reply_mention(
            c, conv, who.actor, replied, threads.mentions(c, conv, text, author=who.actor)
        )
        return next((a for a, _ in found if H.is_bot(a) and H.task_private_readable(c, a, row)), None)

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
        session = session_target(c, who, row, body)
        replied = reply_target(c, who, row, body)
        asker, asked_me = replying_to(c, who, task_id, body, session)
        if ask_to:  # who a ❓ asks is checked before its rule verdict: may they be asked at all
            question_marks.check_asking(c, who.actor, task_id, ask_to, body.text)
        # The org rule check (backend/org_rule_check.py), applied before anything is written: held, nothing is.
        also = ((ask_to,) if ask_to else ()) + tuple(
            recipients(c, who, row, body, session, replied, askers(c, who, task_id, body, session))
        )
        audience = rule_comment_audience(c, who, row, body.text, *also) if rc is not None else None
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
        # A comment reaches its @mentions and the author it replies to (threads.plan); a plain one reaches nobody.
        # Steer and queue wake the bots among them, so they take the right to direct the task (a bot's is its
        # needs_action); anyone may defer. Defer and goal wake none: a goal's own control does (set_goal below), on
        # the bot holding the task.
        if body.delivery == "goal":
            bot = comment_bot(c, row)
        else:
            bot = asker or session or addressed_bot(c, who, row, body.text, replied and replied["id"])
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
        elif session:
            way, defer_for = "steer", None  # a message in an agent's session wakes it (session_target checked who may)
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
        more: dict[str, Any] = {"attachments": attached, "files": [a["ref"] for a in attached]} if attached else {}
        # Their answer to this ❓ by name clears it alone (question_marks.replied), not every ❓ to them here, in this
        # write wherever it is posted; a write that did not clear it fails (`cleared_by`).
        named = (question_marks.mark_id(body.ask) or body.ask) if body.ask and asked_me else ""
        if body.needs_action or (who.role == "bot" and body.delivery in ("steer", "queue")):
            more["needs_action"] = True
        with question_marks.answering(named) if named else nullcontext():
            msg = H.task_comment(
                c,
                who.actor,
                task_id,
                text,
                wake=way != "defer",
                public=body.public,
                approval_id=body.approval_id,
                extra_refs=more or None,
                way=way,
                defer_for=defer_for,
                people_notified=people,
                delegate=directs and way != "defer",
                reach=who if who.role == "bot" else None,
                to=asker,
                in_reply_to=replied and replied["id"],
                session=session,
            )
        if named and not question_marks.cleared_by(c, named, msg["id"]):
            raise Problem("answered", question_marks.ANSWERED, 409)  # not cleared by it: write nothing
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
        """Refuse a bot's comment on an open task from its turn on that task, whoever holds it: what it says goes in
        its reply, posted once at the end of the turn."""
        task_id = auth.resolve_task(c, who, tid)
        row = H.task(c, task_id)
        found = c.execute("SELECT task_id FROM attempts WHERE id=?", (who.attempt_id,)).fetchone()
        if row and found and found[0] == task_id and row["status"] == "open":
            raise Problem("own_turn", OWN_TURN, 409)

    def comment_audience(c, who, tid, body, ask_to=None):
        """A comment's audience as the org rules read it, in the preflight and in Send anyway's replay; `comment_write`
        counts the same at the write. Its `to`, Reply and ❓ answer are checked first, so a refused one is refused before
        any rule reads who it names. `ask_to`: the person a ❓ on it asks (`question_write`)."""
        row = auth.task(c, who, auth.resolve_task(c, who, tid))
        session = session_target(c, who, row, body)
        replied = reply_target(c, who, row, body)
        asked = askers(c, who, row["id"], body, session)
        found = recipients(c, who, row, body, session, replied, asked)
        return rule_comment_audience(c, who, row, body.text, *((ask_to,) if ask_to else ()), *found)

    def rule_comment_replay_audience(c, bot, replay):
        return comment_audience(c, bot, replay["task"], M.TaskComment.model_validate(replay["body"]))

    @org_rule_check.replayer("comment", audience=rule_comment_replay_audience)
    def rule_replay_comment(c, bot, hold, replay, ingest, gate=None):
        return comment_write(
            c, bot, replay["task"], M.TaskComment.model_validate(replay["body"]), ingest, org_rule_check.decided(gate)
        )

    # Before POST: the API document lists a path's methods in the order they are registered.
    @app.get(
        "/api/v2/tasks/{tid}/comments",
        **api_v2("Tasks", "listTaskComments", "Comments and questions with their answers", TaskComments),
    )
    def task_comments(request: Request, tid: str, session: str | None = None, comments: bool = False):
        """Everything on the task's thread; `session=<agent>` only that agent's session, `comments=true` only the
        comments (threads.session_sql)."""
        with store.read() as c:
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            agent = H.resolve_actor(c, session) if session else None
            if session and (not H.is_bot(agent) or teammate_access.Known(c, who, auth).unseen_of([agent])):
                raise Problem("validation", "No agent by that name", 422)  # the same answer whether or not it exists
            return {
                "comments": visual_view(
                    c, who, H.task_comments(c, task_id, actor=privacy.actor(who), session=agent, comments=comments)
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
        deadline for a reader who may not direct the task's bots. It reaches only whom it addresses (threads.plan): a
        plain comment reaches nobody. A question to a person is a ❓ (`POST /api/v2/tasks/{tid}/questions`). A bot does not comment
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
            lambda c: comment_audience(c, who, tid, body),
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
            question_marks.with_choices(text, body.choices),  # its suggested answers are shown, and sent, as words
            lambda c: comment_audience(c, who, tid, M.TaskComment(text=text), ask_to=to),
            place=question_marks.Asking(task_place(who, tid)),
        )

        def write(c):
            done = question_write(c, who, tid, text, to, rc, choices=body.choices)
            if body.not_known and (done.get("comment") or {}).get("id"):
                # Counted: Tico's answer did not cover it (Tasks spec criterion 12).
                H.history_event(c, "task", auth.resolve_task(c, who, tid), who.actor, "ask_not_known", data={"to": to})
            return done

        return mutate(request, body, write)

    # An older client's question on the asks route is asked here (task_moves_routes.task_ask).
    app.state.task_question = task_question

    @app.delete(
        "/api/v2/tasks/{tid}/questions",
        **api_v2(
            "Tasks",
            "takeBackTaskQuestion",
            "A bot takes back its own open ❓ on the task's chat: it leaves the person's Needs you, the message stays. "
            "`question` is the ❓ taken back, null when none is open. Bots only",
        ),
    )
    def task_question_take_back(request: Request, tid: str):
        """Take back the caller's own open ❓ on this task (question_marks.take_back), or nothing when none is open."""
        who = request.state.identity
        if who.role != "bot":
            raise Problem("forbidden", "Only the bot that asked takes a ❓ back", 403)

        def write(c):
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            mine = question_marks.open_mine(c, who.actor, task_id)
            taken = question_marks.take_back(c, mine["id"], who.actor) if mine else None
            return {"question": question_marks.public(taken) if taken else None}

        return mutate(request, M.Empty(), write)

    def question_write(c, who, tid, text, to, rc, ingest=None, choices=()):
        """A ❓ on a task's chat, as the route writes it and Send anyway replays it: the comment, then its mark, in one
        transaction. A Block or Escalate rule it breaks refuses it back to the bot, nothing written or held
        (task_rule_gate.refuse_question); a hold from before (with no choices) still replays here. `choices`: its
        suggested answers."""
        question_marks.clean_choices(c, who.actor, choices)  # refused before anything is written
        done = comment_write(c, who, tid, M.TaskComment(text=text), ingest, rc, who, ask_to=to)
        said = done.get("comment") or {}
        if not said.get("id"):
            return done
        mark = question_marks.mark(c, who.actor, said["id"], to, choices=choices)
        return question_marks.with_warning({**done, "question": question_marks.public(mark)}, mark)

    def rule_question_audience(c, bot, replay):
        text = replay["body"].get("text") or ""
        return comment_audience(c, bot, replay["task"], M.TaskComment(text=text), ask_to=replay["to"])

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
            return {"links": H.task_links(c, task_id)}

    def readers_answer(c, who, task_id):
        row = H.task(c, task_id)
        private = H.task_private(c, row)
        view = teammate_access.Known(c, who, auth).task({**row, "readers": H.task_readers(row) if private else []})
        assignee = view.get("assignee_id")
        return {
            "private": private,
            "requester_id": view["requester_id"],
            "owner_id": view["owner_id"],
            # A person assignee reads a private task as its requester does (hubdb.task_private_readable).
            "assignee_id": assignee if private and H.is_human(assignee) else None,
            "readers": view["readers"],
            "shared_with": view["readers"],
        }

    @app.get("/api/v2/tasks/{tid}/readers")
    def get_task_readers(request: Request, tid: str):
        """Who can see the task: on a private one its requester, owner, person assignee and readers (docs/tasks.md)."""
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
