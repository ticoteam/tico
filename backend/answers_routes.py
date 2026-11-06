"""Answer and needs-you routes: answers, clarifications, recommendations, seen markers."""

from .store import H
from . import models as M
from .store import Problem
from fastapi import Request
from . import org_rule_check
from . import task_privacy as privacy
from . import question_tasks
from . import views


def register(app, answer_audience, answer_write, auth, checked_write, mutate):
    @app.post("/api/v2/messages/{mid}/answer")
    def answer(request: Request, mid: str, body: M.Answer):
        who = request.state.identity
        return checked_write(
            request,
            body,
            who,
            "answer",
            body.text,
            lambda c: answer_audience(c, who, mid),
            lambda c, gate: answer_write(c, who, mid, body.text, body.unknown, org_rule_check.warn_line(gate)),
            {"message": mid, "body": body.model_dump(mode="json", by_alias=True)},
        )

    @org_rule_check.replayer("answer")
    def rule_replay_answer(c, bot, hold, replay, ingest, gate=None):
        body = M.Answer.model_validate(replay["body"])
        return answer_write(
            c, bot, replay["message"], body.text, body.unknown, org_rule_check.warn_line(gate), destination=True
        )

    @app.post("/api/v2/needs-you/clarify")
    def clarify_ask(request: Request, body: M.ClarifyAsk):
        """One tap on Clarify: ask the bot behind a stale question whether it still matters, in clearer words."""
        who = request.state.identity
        views.human_only(who)

        def work(c):
            from . import clarify_asks

            asked = H.message(c, body.ask)
            if not asked or asked["kind"] != "ask":
                raise Problem("not_found", "Question not found", 404)
            auth.conversation(c, who, asked["conversation_id"], write=True)
            privacy.require_message(c, who, asked)
            return clarify_asks.clarify(c, who.actor, body.ask)

        return mutate(request, body, work)

    @app.post("/api/v2/needs-you/recommended")
    def take_recommendation(request: Request, body: M.TakeRecommendation):
        """One tap on "Do what you recommend": answers the bot's ask with its recommendation and closes the card."""
        who = request.state.identity
        views.human_only(who)

        def work(c):
            asked = H.message(c, body.ask)
            if not asked:
                # A question task (backend/question_tasks.py): the card's ask is the task itself.
                row = H.task(c, body.ask)
                if row and question_tasks.is_question(row):
                    auth.task(c, who, row["id"])
                    return question_tasks.take_recommendation(c, auth, who, row, body.version)
            if not asked or asked["kind"] != "ask":
                raise Problem("not_found", "Question not found", 404)
            auth.conversation(c, who, asked["conversation_id"], write=True)
            privacy.require_message(c, who, asked)
            return views.take_recommendation(c, auth, who, asked, body.version)

        return mutate(request, body, work)

    @app.post("/api/v2/needs-you/seen")
    def needs_seen(request: Request, body: M.AsksSeen):
        """The person's Needs you shows these questions to them: each is delivered (a question withdrawn later says it
        was withdrawn after they saw it only once they open it: POST /api/v2/tasks/{id}/seen)."""
        who = request.state.identity
        views.human_only(who)

        def work(c):
            readable = [
                m
                for m in (H.message(c, mid) for mid in body.asks)
                if m and m["recipient_id"] == who.actor and privacy.message_readable(c, privacy.actor(who), m)
            ]
            return {"delivered": H.asks_seen(c, who.actor, [m["id"] for m in readable])}

        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/seen")
    def task_seen(request: Request, tid: str, body: M.Empty):
        """The person opened the task, in Needs you or on its own: its open questions to them are read."""
        who = request.state.identity
        views.human_only(who)

        def work(c):
            row = auth.task(c, who, auth.resolve_task(c, who, tid))
            mine = [
                a["id"] for a in H.open_task_asks(c, row, actor=privacy.actor(who)) if a["recipient_id"] == who.actor
            ]
            return {"read": H.asks_seen(c, who.actor, mine, read=True)}

        return mutate(request, body, work)
