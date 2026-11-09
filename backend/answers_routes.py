"""Needs-you routes: the one-tap recommendation and the seen markers. A question is a task (backend/question_tasks.py),
so its card is read off the task."""

from fastapi import Request

from . import question_tasks
from . import models as M
from . import views
from .store import H, Problem


def register(app, auth, mutate):
    @app.post("/api/v2/needs-you/recommended")
    def take_recommendation(request: Request, body: M.TakeRecommendation):
        """One tap on "Do what you recommend": answers the question task with the bot's recommendation and closes it."""
        who = request.state.identity
        views.human_only(who)

        def work(c):
            # A question task (backend/question_tasks.py): the card's ask is the task itself.
            row = H.task(c, body.ask)
            if not row or not question_tasks.is_question(row):
                raise Problem("not_found", "Question not found", 404)
            auth.task(c, who, row["id"])
            return question_tasks.take_recommendation(c, auth, who, row, body.version)

        return mutate(request, body, work)

    @app.post("/api/v2/needs-you/seen")
    def needs_seen(request: Request, body: M.AsksSeen):
        """The person's Needs you shows these questions to them. A question is a task now (step 6c), read as any task
        is, so nothing is marked: kept for clients that still send it."""
        who = request.state.identity
        views.human_only(who)
        return mutate(request, body, lambda c: {"delivered": []})

    @app.post("/api/v2/tasks/{tid}/seen")
    def task_seen(request: Request, tid: str, body: M.Empty):
        """The person opened the task, in Needs you or on its own. A question is a task now (step 6c), so nothing is
        marked read: kept for clients that still send it."""
        who = request.state.identity
        views.human_only(who)

        def work(c):
            auth.task(c, who, auth.resolve_task(c, who, tid))
            return {"read": []}

        return mutate(request, body, work)
