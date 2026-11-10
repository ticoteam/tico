"""Task action routes: the model a task's runs use."""

from . import models as M
from fastapi import Request


def register(app, auth, mutate, settings_admin):
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
