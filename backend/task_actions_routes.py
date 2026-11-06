"""Task action routes: run model, check-back, snooze, later, unsnooze."""

from .store import H
from . import models as M
from fastapi import Request
from . import quiet_work


def register(app, auth, mover, mutate, settings_admin, task_view):
    @app.post("/api/v2/tasks/{tid}/model")
    def task_run_model(request: Request, tid: str, body: M.TaskRunModel):
        """The model this task's runs use, with its effort and harness (backend/task_run_models.py). An absent
        `model` keeps it and changes only what is named; a null `model` clears it, so the bot's task model runs
        the task again.
        Whoever may change the bot's model may change this, and a bot never does. `readiness` says whether the bot's
        computer can run it now; `warning` is plain words for a cheaper pick on a bot that sends, spends, publishes or
        writes. It still saves."""

        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            return settings_admin.set_task_run_model(c, who, task_id, body)

        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/check-back")
    def task_check_back(request: Request, tid: str, body: M.TaskCheckBack):
        who = request.state.identity

        def work(c):
            from . import task_check_backs

            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            H.TS._check(c, who.actor, task_id, mover(c, who))
            return {
                "task": task_view(
                    task_check_backs.set_time(c, H.task(c, task_id), body.at, body.note, actor=who.actor),
                    c,
                    visible_sql=auth.task_sql(c, who),
                    who=who,
                )
            }

        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/snooze")
    def task_snooze(request: Request, tid: str, body: M.TaskSnooze):
        """Snooze a task until a date: off the board, lists and Needs you, and quiet, until then
        (backend/task_snooze.py). The requester, the owner, anyone on it by role, or a mover."""

        def work(c):
            from . import task_snooze as TS

            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            after = TS.snooze(c, who.actor, task_id, body.until, mover=mover(c, who), note=body.note)
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who)}

        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/later")
    @app.post("/api/v2/tasks/{tid}/defer")  # its older name, kept for clients from before Steer, Queue, Defer
    def task_defer(request: Request, tid: str, body: M.TaskDefer):
        """Say when a deferred task a run carried (or one still waiting for a run) is picked up again: after another
        task is done, at a time, or with the next run; or that the run handled it (backend/quiet_work.py). The task's
        own bot, or a person (`tico task later`)."""

        def work(c):
            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            after = None
            if body.after:
                after = auth.resolve_task(c, who, body.after)
                auth.task(c, who, after)
            row = quiet_work.defer(
                c, who.actor, task_id, after=after, at=body.at, next_run=body.next_run, handled=body.handled
            )
            return {"task": task_view(row, c, visible_sql=auth.task_sql(c, who), who=who)}

        return mutate(request, body, work)

    @app.post("/api/v2/tasks/{tid}/unsnooze")
    def task_unsnooze(request: Request, tid: str, body: M.Empty):
        """End a snooze early: the task is back now and its owner bot is woken once."""

        def work(c):
            from . import task_snooze as TS

            who = request.state.identity
            task_id = auth.resolve_task(c, who, tid)
            auth.task(c, who, task_id)
            after = TS.unsnooze(c, who.actor, task_id, mover=mover(c, who))
            return {"task": task_view(after, c, visible_sql=auth.task_sql(c, who), who=who)}

        return mutate(request, body, work)
