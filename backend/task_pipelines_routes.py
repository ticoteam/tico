# pyright: strict
"""The pipelines' settings records (docs/dev/pipelines-v3-cutover.md, "HTTP"): read them, save one, retire one.

The record is `pipeline_settings`'s: these routes read and write it whole, and every check is there. A private pipeline
(phase 2) is not built: everyone on the team reads every pipeline.
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import Request

from . import models as M
from . import pipeline_settings
from .store import Problem

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

    @app.get("/api/v2/pipelines")
    def pipelines(request: Request):
        """Every pipeline, in the order they were made, each as its settings record."""
        who: Any = request.state.identity
        auth.domain(who)
        with store.read() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM pipelines ORDER BY created_at, id")]
            return {"pipelines": [record(c, who, pid) for pid in ids]}

    @app.get("/api/v2/pipelines/{pipeline_id}")
    def pipeline(request: Request, pipeline_id: str):
        who: Any = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return {"pipeline": record(c, who, pipeline_id)}

    @app.post("/api/v2/pipelines/{pipeline_id}")
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
            pipeline_settings.save(c, who.actor, {**given, "id": pipeline_id}, seen)
            return {"pipeline": record(c, who, pipeline_id)}

        return mutate(request, body, write)

    @app.post("/api/v2/pipelines/{pipeline_id}/retire")
    def pipeline_retire(request: Request, pipeline_id: str, body: M.PipelineRetire):
        """Take a pipeline out of use once it holds no open task."""
        who: Any = request.state.identity
        auth.domain(who)

        def write(c: Any) -> dict[str, Any]:
            pipeline_settings.retire(c, who.actor, pipeline_id, body.settings_version)
            return {"pipeline": record(c, who, pipeline_id)}

        return mutate(request, body, write)
