# pyright: strict
"""A question task's own writes (backend/questions.py): its person answers it, the bot that asked withdraws it, and
either gives it to another person. Each is one transaction; a stale round or a question that is no longer the
caller's is 409 "Look again", so the page reads it again."""

from collections.abc import Callable
from typing import Any

from fastapi import Request

from . import models as M
from . import questions
from .api_contract import api_v2
from .api_answers.tasks import QuestionResult
from .store import Problem

CONFLICT = {"409": {"description": "Look again: the question changed, ended or is no longer yours"}}


def register(app: Any, auth: Any, mutate: Callable[..., Any]) -> None:
    def question_write(request: Request, tid: str, body: Any, act: Callable[[Any, Any, str], Any]) -> Any:
        who = request.state.identity

        def write(c: Any) -> dict[str, Any]:
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            try:
                return {"question": questions.public(act(c, who, task_id))}
            except questions.QuestionError as exc:
                raise Problem(exc.code, exc.detail, exc.status) from exc

        return mutate(request, body, write)

    @app.post(
        "/api/v2/tasks/{tid}/question/answer",
        **api_v2(
            "Tasks",
            "answerQuestionTask",
            "Answer a question filed as a task for you (`text`, at the `round` you were shown): it is finished, the "
            "answer goes to the task it was asked on and the bot that asked is woken there once",
            QuestionResult,
            responses=CONFLICT,
        ),
    )
    def question_answer(request: Request, tid: str, body: M.QuestionAnswer) -> Any:
        return question_write(
            request,
            tid,
            body,
            lambda c, who, task_id: questions.answer(
                c, task_id, who.actor, body.text, body.round, via=getattr(who, "via", None) or None
            ),
        )

    @app.post(
        "/api/v2/tasks/{tid}/question/withdraw",
        **api_v2(
            "Tasks",
            "withdrawQuestionTask",
            "Take back a question you asked as a task: it is closed and its person no longer has it. The bot that asked",
            QuestionResult,
            responses=CONFLICT,
        ),
    )
    def question_withdraw(request: Request, tid: str, body: M.QuestionWithdraw | None = None) -> Any:
        said = body or M.QuestionWithdraw()
        return question_write(
            request, tid, said, lambda c, who, task_id: questions.withdraw(c, task_id, who.actor, said.reason)
        )

    @app.post(
        "/api/v2/tasks/{tid}/question/give",
        **api_v2(
            "Tasks",
            "giveQuestionTask",
            "Give a question task to another person who may be asked it: its person or the bot that asked",
            QuestionResult,
            responses=CONFLICT,
        ),
    )
    def question_give(request: Request, tid: str, body: M.QuestionGive) -> Any:
        def give(c: Any, who: Any, task_id: str) -> Any:
            return questions.give(c, task_id, auth.target(c, who, body.to), who.actor)

        return question_write(request, tid, body, give)
