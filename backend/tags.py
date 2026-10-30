"""Tags and template instances. Task visibility still governs every task on a tag page."""

from fastapi import Request

from .models import Contract, ID
from .store import H, Problem
from . import task_privacy as privacy


class TagCreate(Contract):
    key: ID
    label: str | None = None
    metadata: dict | None = None
    markdown: str | None = None
    is_template: bool = False
    template_id: ID | None = None
    owner: str | None = None


class TagUpdate(Contract):
    version: int
    label: str | None = None
    metadata: dict | None = None
    markdown: str | None = None
    owner: str | None = None


def install(app, store, auth, mutate, task_views):
    def mover(c, who):
        return who.role == "owner" or who.role == "human" and H.can_move(c, who.actor)

    def require_tag(c, who, ident):
        row = H.tag(c, ident)
        if not row or not privacy.tag_readable(c, who, row):
            raise Problem("not_found", "Tag not found", 404)
        return row

    @app.get("/api/v2/tags")
    def list_tags(request: Request, is_template: bool | None = None):
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"tags": privacy.tags_readable(c, request.state.identity, H.tags(c, is_template))}

    @app.get("/api/v2/tags/{tag_id}")
    def show_tag(request: Request, tag_id: str, limit: int = 500, offset: int = 0):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = require_tag(c, who, tag_id)
            limit, offset = max(1, min(limit, 500)), max(0, offset)
            tasks = H.tasks(c, label=row["key"], visible=auth.task_sql(c, who), limit=limit + 1, offset=offset)
            return {"tag": row, "editable": H.tag_can_edit(c, who.actor, row, mover(c, who)),
                    "tasks": task_views(tasks[:limit], c, who, auth.task_sql(c, who)),
                    "next_offset": offset + limit if len(tasks) > limit else None}

    def create(c, who, body, template_id=None):
        auth.domain(who)
        owner = auth.target(c, who, body.owner) if body.owner else who.actor
        fields = body.model_dump(exclude={"owner", "template_id"})
        return {"tag": H.tag_create(c, who.actor, **fields, owner=owner,
                    template_id=template_id or body.template_id, mover=mover(c, who))}

    @app.post("/api/v2/tags")
    def create_tag(request: Request, body: TagCreate):
        return mutate(request, body, lambda c: create(c, request.state.identity, body))

    @app.post("/api/v2/tags/{tag_id}/instances")
    def create_instance(request: Request, tag_id: str, body: TagCreate):
        def work(c):
            template = require_tag(c, request.state.identity, tag_id)
            if body.template_id and H.tag(c, body.template_id) != template:
                raise Problem("kind", "Use the template named in the path", 422)
            return create(c, request.state.identity, body, template["id"])
        return mutate(request, body, work)

    @app.post("/api/v2/tags/{tag_id}")
    def update_tag(request: Request, tag_id: str, body: TagUpdate):
        def work(c):
            who = request.state.identity
            auth.domain(who)
            row = require_tag(c, who, tag_id)
            if not H.tag_can_edit(c, who.actor, row, mover(c, who)):
                raise Problem("forbidden", "This tag is edited by its owner or a task mover", 403)
            if row["version"] != body.version:
                raise Problem("version_conflict", "Tag changed; fetch it and retry your update", 409)
            fields = body.model_dump(exclude={"version"}, exclude_none=True)
            if fields.get("owner"):
                fields["owner"] = auth.target(c, who, fields["owner"])
            return {"tag": H.tag_update(c, who.actor, row["id"], body.version,
                                       **fields, mover=mover(c, who))}
        return mutate(request, body, work)
