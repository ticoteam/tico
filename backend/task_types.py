"""Company-wide task types and their status steps."""

from fastapi import Request

from . import hubdb as H
from . import models as M
from .store import Problem


def install_task_types(app, store, auth, mutate, mover):
    def require_mover(c, who):
        if not mover(c, who):
            raise Problem("forbidden", "Task types are managed by movers", 403)

    @app.get("/api/v2/task-types")
    def types(request: Request):
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"types": H.type_list(c)}

    @app.get("/api/v2/task-types/{type_id}")
    def task_type(request: Request, type_id: str):
        auth.domain(request.state.identity)
        with store.read() as c:
            row = H.type_get(c, type_id)
            if not row:
                raise Problem("not_found", "Task type not found", 404)
            return {"type": row}

    @app.post("/api/v2/task-types")
    def create(request: Request, body: M.TaskTypeCreate):
        def work(c):
            who = request.state.identity
            require_mover(c, who)
            return {"type": H.type_create(c, who.actor, body.name,
                    [step.model_dump() for step in body.steps], mover=True, bots=body.bots, numbered=body.numbered,
                    steps_only=body.steps_only, default_visibility=body.default_visibility)}
        return mutate(request, body, work)

    @app.post("/api/v2/task-types/{type_id}")
    def update(request: Request, type_id: str, body: M.TaskTypeUpdate):
        def work(c):
            who = request.state.identity
            require_mover(c, who)
            steps = [step.model_dump() for step in body.steps] if body.steps is not None else None
            return {"type": H.type_update(c, who.actor, type_id, body.name, steps, mover=True, bots=body.bots, numbered=body.numbered,
                    steps_only=body.steps_only, default_visibility=body.default_visibility)}
        return mutate(request, body, work)

    @app.delete("/api/v2/task-types/{type_id}")
    @app.post("/api/v2/task-types/{type_id}/delete")
    def delete(request: Request, type_id: str, body: M.Empty):
        def work(c):
            who = request.state.identity
            require_mover(c, who)
            return {"type": H.type_delete(c, who.actor, type_id, mover=True)}
        return mutate(request, body, work)
