"""Creation-time QA classification. Explicit authorship always wins over path rules."""
from pathlib import PurePosixPath

RENDERED = ("ui/", "desktop/", "app/")
BEHAVIOUR = ("backend/chat", "backend/conversation", "backend/message", "backend/bot_to_bot",
             "backend/wake", "backend/execution", "backend/turn", "backend/runner")
NOTE = "QA: not checkable live (rendered UI); check the rendered result on the release candidate."


def classify(paths, *, qa=None, not_checkable_live=False):
    visible, rendered = False, False
    for value in paths or []:
        path = str(PurePosixPath(value.replace("\\", "/"))).removeprefix("./").lower()
        parts = PurePosixPath(path).parts
        # Tests or documentation nested under a visible component remain non-QA.
        if (any(p in ("test", "tests", "__tests__", "docs") for p in parts)
                or path.startswith((".github/", "ci/", "scripts/"))
                or path.endswith((".md", ".rst", ".txt"))):
            continue
        if path.startswith(RENDERED):
            visible, rendered = True, True
        elif path.startswith(BEHAVIOUR) or path.startswith(("bots/", "chat/")):
            visible = True
    chosen = visible if qa is None else qa
    return {"qa": chosen, "note": NOTE if chosen and (rendered or not_checkable_live) else ""}


def create(c, task_id, paths, qa, not_checkable_live=False, explicit_qa=False):
    from .repositories import save_metadata
    save_metadata(c, "qa-default:" + task_id, {"author": qa if qa is not None else True if explicit_qa else None,
                                               "paths": paths or [], "prs": [],
                                               "not_checkable_live": not_checkable_live})


def pr_created(c, pr, paths):
    """A PR's first registration applies the creation default once to its linked tickets.

    The keeper applies the rule; this does not expand any bot's label permissions.
    No later push changes QA classification, and an author's explicit qa/no-qa is retained.
    """
    from . import hubdb as H
    from .github import _links_for
    from .repositories import metadata, save_metadata
    for link in _links_for(c, pr["html_url"]):
        task = H.task(c, link["task_id"])
        if not task:
            continue
        typ = H.type_get(c, task.get("type_id")) or {}
        if typ.get("role") != "engineering" and typ.get("name") != "Dev tickets":
            continue
        key = "qa-default:" + task["id"]
        state = metadata(c, key)
        if pr["html_url"] in state.get("prs", []):
            continue
        labels = H.task_labels(task)
        author = state.get("author")
        pr_labels = {v["name"].lower() for v in pr.get("labels", [])}
        if "qa" in pr_labels:
            author = True
        elif pr_labels & {"no qa", "no-qa", "qa:no"}:
            author = False
        # A qa tag already supplied by a person is never removed by a default.
        result = classify(paths, qa=author if author is not None else True if "qa" in labels else None,
                          not_checkable_live=state.get("not_checkable_live", False))
        wanted = [k for k in labels if k != "qa"] + (["qa"] if result["qa"] else [])
        if set(wanted) != set(labels):
            H._set_task_tags(c, H.KEEPER, task["id"], wanted, "QA default at PR creation")
        if result["note"]:
            H.task_comment(c, H.KEEPER, task["id"], result["note"], wake=False)
        save_metadata(c, key, {**state, "prs": [*state.get("prs", []), pr["html_url"]], "pr_paths": paths})
