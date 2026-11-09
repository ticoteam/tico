"""Company-wide task types and their status steps."""

from fastapi import Request

from . import hubdb as H
from . import models as M
from . import pipeline_engine, pipeline_rights
from .store import Problem


def install_task_types(app, store, auth, mutate, mover):
    def require_mover(c, who):
        if not mover(c, who):
            raise Problem("forbidden", "Task types are managed by movers", 403)

    @app.get("/api/v2/task-types")
    def types(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            # A members-only pipeline is listed only to the people it is for (pipeline_rights.may_see).
            listed = H.type_list(c)
            shown = pipeline_rights.visible_pipeline_ids(c, who.actor, [typ["id"] for typ in listed])
            listed = [typ for typ in listed if typ["id"] in shown]
            # Each pipeline says whether this caller may change its rules: the check a move's override uses.
            editable = pipeline_rights.editable_pipeline_ids(c, who.actor, [typ["id"] for typ in listed])
            for typ in listed:
                typ["can_edit_rules"] = typ["id"] in editable
                # Whether it has any requirement, so the pipeline keeps its plain layout without one.
                typ["has_requirements"] = pipeline_engine.has_requirements(c, typ["id"])
            return {"types": listed}

    @app.get("/api/v2/task-types/{type_id}")
    def task_type(request: Request, type_id: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = H.type_get(c, type_id)
            # Named by id or by name; a pipeline the caller may not see is not found, whichever way it was named.
            if not row or not pipeline_rights.may_see(c, who.actor, row["id"]):
                raise Problem("not_found", "Task type not found", 404)
            return {"type": row}

    @app.get("/api/v2/check-pool-candidates")
    def checker_models(request: Request):
        """A small, on-demand view for the pool editor; keep runtime history off every task-board payload."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            access = auth.bot_accesses(c, who)
            slugs = list(
                dict.fromkeys(s.strip() for s in request.query_params.get("bots", "").split(",") if s.strip())
            )[:40]
            ids = [(H.resolve_bot(c, slug) or {}).get("id", slug) for slug in slugs]
            visible = [bot for bot in ids if access.get(bot, auth.FULL)["read"]]
            from . import check_pools

            return {"candidates": check_pools.candidate_details(c, visible)}

    @app.post("/api/v2/task-types")
    def create(request: Request, body: M.TaskTypeCreate):
        def work(c):
            who = request.state.identity
            require_mover(c, who)
            return {
                "type": H.type_create(
                    c,
                    who.actor,
                    body.name,
                    [step.model_dump() for step in body.steps],
                    mover=True,
                    bots=body.bots,
                    numbered=body.numbered,
                    steps_only=body.steps_only,
                    default_visibility=body.default_visibility,
                )
            }

        return mutate(request, body, work)

    @app.post("/api/v2/task-types/{type_id}")
    def update(request: Request, type_id: str, body: M.TaskTypeUpdate):
        def work(c):
            who = request.state.identity
            require_mover(c, who)
            steps = [step.model_dump() for step in body.steps] if body.steps is not None else None
            return {
                "type": H.type_update(
                    c,
                    who.actor,
                    type_id,
                    body.name,
                    steps,
                    mover=True,
                    bots=body.bots,
                    numbered=body.numbered,
                    steps_only=body.steps_only,
                    default_visibility=body.default_visibility,
                )
            }

        return mutate(request, body, work)

    @app.delete("/api/v2/task-types/{type_id}")
    @app.post("/api/v2/task-types/{type_id}/delete")
    def delete(request: Request, type_id: str, body: M.Empty):
        def work(c):
            who = request.state.identity
            require_mover(c, who)
            return {"type": H.type_delete(c, who.actor, type_id, mover=True)}

        return mutate(request, body, work)
