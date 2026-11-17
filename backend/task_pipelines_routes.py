# pyright: strict
"""The pipelines' settings records (docs/dev/pipelines-v3-cutover.md, "HTTP"): read them, save one, add one from a
preset, retire one.

The record is `pipeline_settings`'s: these routes read and write it whole, and every check is there. Everyone on the
team reads every pipeline record, a private one too: its tasks are private (backend/private_pipelines.py), not it.
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request

from . import models as M
from . import pipeline_settings
from .store import Problem
from .api_contract import api_v2, ref

if TYPE_CHECKING:
    from fastapi import FastAPI


# A pipeline made by its save names its own id: lowercase words joined by dashes. Not one of the paths 0.5 had under
# /api/v2/pipelines/, so an old client's call there is a 404, never a new pipeline.
NEW_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
RESERVED = frozenset({"overview", "setup", "decisions", "review", "tasks", "new", "log", "retire"})


def register(app: "FastAPI", auth: Any, mutate: "Callable[..., Any]", store: Any) -> None:
    def record(c: Any, who: Any, pipeline_id: str) -> dict[str, Any]:
        found = pipeline_settings.read(c, pipeline_id)
        if not found:
            raise Problem("not_found", "No such pipeline", 404)
        row = c.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
        return {**found, "may_change": pipeline_settings.may_change(c, who.actor, dict(row))}

    @app.get(
        "/api/v2/pipelines",
        **api_v2("Pipelines", "listPipelines", "Every pipeline, each as its settings record", "PipelineSettingsList"),
    )
    def pipelines(request: Request):
        """Every pipeline, in the order they were made, each as its settings record."""
        who: Any = request.state.identity
        auth.domain(who)
        with store.read() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM pipelines ORDER BY created_at, id")]
            return {"pipelines": [record(c, who, pid) for pid in ids]}

    @app.get(
        "/api/v2/pipelines/{pipeline_id}",
        **api_v2("Pipelines", "getPipeline", "One pipeline's settings record", "PipelineSettingsResult"),
    )
    def pipeline(request: Request, pipeline_id: str):
        who: Any = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return {"pipeline": record(c, who, pipeline_id)}

    @app.post(
        "/api/v2/pipelines/{pipeline_id}",
        **api_v2(
            "Pipelines",
            "savePipeline",
            "Make or change a pipeline: the whole record, with the settings_version it was read at. Removing a stage that holds open tasks answers 409 stage_has_tasks (stages, and a move_to hint) until the record carries move_tasks_to {pipeline, stage}: its tasks move there first, in any pipeline",
            "PipelineSettingsResult",
            description="Set side: true on a work stage after the first to make it hand-moved. Side stages have no requirements, limit, expected time or on-enter action.",
            request_body={
                "required": True,
                "content": {"application/json": {"schema": ref("PipelineSettingsInput")}},
            },
        ),
    )
    def pipeline_save(request: Request, pipeline_id: str, body: M.PipelineRecord):
        """Make the pipeline or change it: the whole record, with the `settings_version` it was read at (a bot must
        give it; a person may leave it out). A new pipeline's id is lowercase words joined by dashes, and none of the
        0.5 paths (RESERVED): those answer 404."""
        who: Any = request.state.identity
        auth.domain(who)

        def write(c: Any) -> dict[str, Any]:
            made = c.execute("SELECT 1 FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
            if not made and (pipeline_id in RESERVED or not NEW_ID.match(pipeline_id)):
                raise Problem("not_found", "No such pipeline", 404)
            given = body.model_dump()
            seen = given.pop("settings_version", None)
            given.pop("may_change", None)
            raw: Any = given.pop("move_tasks_to", None)
            given = {**given, "id": pipeline_id}
            move_to = cast("dict[str, Any] | None", raw) if raw is None or isinstance(raw, dict) else None
            if raw is not None and not (move_to and move_to.get("pipeline")):
                raise Problem("move_tasks_to", "move_tasks_to names a pipeline and a stage", 422)
            gone = pipeline_settings.holding(c, given)
            if gone and not move_to:
                # A stage that holds tasks goes once they have somewhere to go: the editor asks, then sends move_tasks_to.
                names = ", ".join(f"{g['name']} ({g['tasks']})" for g in gone)
                raise Problem(
                    "stage_has_tasks",
                    f"Say where the tasks of {names} go",
                    409,
                    extra={"stages": gone, "move_to": pipeline_settings.move_to_hint(c, given)},
                )
            pipeline_settings.save(c, who.actor, given, seen, move_to)
            return {"pipeline": record(c, who, pipeline_id)}

        return mutate(request, body, write)

    @app.post(
        "/api/v2/pipelines/{pipeline_id}/from-preset",
        **api_v2(
            "Pipelines",
            "addPipelineFromPreset",
            "Make a pipeline from a preset the company does not have (Product beside the pair it replaced)",
            "PipelineSettingsResult",
        ),
    )
    def pipeline_from_preset(request: Request, pipeline_id: str, body: M.PipelineFromPreset):
        """Make a pipeline from a preset the company does not have, under this new id: Product (`features`) beside the
        Product and Engineering pair it replaced, whose open tasks finish there. The company's owner and Admins."""
        who: Any = request.state.identity
        auth.domain(who)

        def write(c: Any) -> dict[str, Any]:
            if pipeline_id in RESERVED or not NEW_ID.match(pipeline_id):
                raise Problem("not_found", "No such pipeline", 404)
            pipeline_settings.add_preset(c, who.actor, body.preset, pipeline_id, body.name, body.prefix)
            return {"pipeline": record(c, who, pipeline_id)}

        return mutate(request, body, write)

    @app.post(
        "/api/v2/pipelines/{pipeline_id}/retire",
        **api_v2(
            "Pipelines",
            "retirePipeline",
            "Take a pipeline out of use once it holds no open task; move_tasks_to {pipeline, stage} (another pipeline) moves its open tasks there first",
        ),
    )
    def pipeline_retire(request: Request, pipeline_id: str, body: M.PipelineRetire):
        """Take a pipeline out of use once it holds no open task."""
        who: Any = request.state.identity
        auth.domain(who)

        def write(c: Any) -> dict[str, Any]:
            place = body.move_tasks_to
            to = {"pipeline": place.pipeline, "stage": place.stage} if place else None
            pipeline_settings.retire(c, who.actor, pipeline_id, body.settings_version, to)
            return {"pipeline": record(c, who, pipeline_id)}

        return mutate(request, body, write)
