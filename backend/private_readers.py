# pyright: strict
"""Who reads a private task as it moves through a pipeline, told to whoever files it there (Product #449).

A private task admits its parties and readers. Since the stage-holder rule (task_engine.names, `stage_read`), the bot a
pipeline names for a stage reads a private task while it holds that stage, and so does everyone above that bot on the
org chart (task_rights.private_subjects). A stage that waits on an open pull request puts the work in GitHub, where
anyone with access to the repository reads it. Nothing here changes who reads: it only says so, before the filer saves
(the new-task window, `tico task create` and its dry run) and in the task's history once filed. It never refuses.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# The history kind that records what the filer was told at filing: `new` is the line.
KIND = "private_readers"


def _said(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _stage_bots(stage: Row) -> list[str]:
    """The bots the pipeline names for `stage`, who read a private task while they hold it (task_engine.names)."""
    from . import stage_pick, task_engine

    pick = stage_pick.config(stage)
    bots = list(pick.bots) if pick else [stage.get("owner")]
    return [b for b in bots if b and task_engine.names(stage, b)]


def _managers(conn: "sqlite3.Connection", named: list[str], skip: set[str]) -> list[str]:
    """Everyone above a named bot on the org chart, who reads what it holds (task_rights.private_subjects): bots and
    people, nearest first (the fewer they are above, the nearer)."""
    from . import hubdb, task_rights, views

    H: Any = hubdb  # untyped modules
    rights: Any = task_rights
    people: Any = views
    roster: Row = people.roster(conn, shared=True)
    candidates: list[str] = [f"bot:{b['id']}" for b in H.bots(conn) if b.get("state") != "archived"]
    candidates += [f"human:{p['id']}" for p in roster.get("people", []) if p.get("id") and not p.get("hidden")]
    found: list[tuple[int, str]] = []
    for actor in candidates:
        if actor in skip or actor in named:
            continue
        below: set[str] = rights.private_subjects(conn, actor)
        if any(b in below for b in named):
            found.append((len(below), actor))
    return [actor for _, actor in sorted(found)]


def _github(conn: "sqlite3.Connection", stages: list[Row]) -> tuple[bool, str]:
    """Whether a stage waits on an open pull request (the work goes to GitHub), and the repository a GitHub requirement
    names, '' when it leaves it to the server's (pipeline_settings: release_repo)."""
    from . import task_requirements as R

    github, repo = False, ""
    for stage in stages:
        for req in R.on_stage(conn, stage["id"]):
            if req.get("source") != "github":
                continue
            github = github or req.get("condition") == "pr_open"
            settings: Row = req.get("settings") or {}
            repo = repo or str(settings.get("release_repo") or "").strip()
    return github, repo


def readers(
    conn: "sqlite3.Connection", pipeline_id: str, parties: "list[str | None] | tuple[str | None, ...]" = ()
) -> Row | None:
    """Who reads a private task filed into `pipeline_id` beyond `parties` (its requester and readers, who read it
    already): `{"pipeline", "stages": [[name, ...], ...], "managers": [name, ...], "github", "repo", "line"}`, one
    group per stage list ("Reviewer 1 or Reviewer 2"), or None when nobody else reads it."""
    from . import hubdb, task_engine

    H: Any = hubdb  # untyped module
    pipe = task_engine.pipeline(conn, pipeline_id)
    if not pipe or pipe.get("retired_at"):
        return None
    have = {p for p in parties if p}
    work = [s for s in task_engine.stages(conn, pipeline_id) if s["kind"] == "work"]
    groups: list[tuple[str, ...]] = []
    for stage in work:
        bots = tuple(b for b in _stage_bots(stage) if b not in have)
        if bots and bots not in groups:
            groups.append(bots)
    named = list(dict.fromkeys(b for group in groups for b in group))
    managers = _managers(conn, named, have) if named else []
    github, repo = _github(conn, work)
    if not named and not github:
        return None
    stages: list[list[str]] = [[str(H.actor_name(conn, b)) for b in group] for group in groups]
    bosses: list[str] = [str(H.actor_name(conn, m)) for m in managers]
    parts: list[str] = []
    if stages:
        held = [" or ".join(group) for group in stages]
        # "A, B, and their manager, M": with managers to follow, the stage list is joined by commas alone.
        who = ", ".join(held) if bosses else _said(held)
        if bosses:
            who += f", and {'their manager' if len(bosses) == 1 else 'their managers'}, {_said(bosses)}"
        parts.append(f"Private, but these can read it as it moves through {pipe['name']}: {who}.")
    if github:
        where = f"GitHub ({repo})" if repo else "GitHub"
        lead = "Its" if stages else "Private, but its"
        parts.append(
            f"{lead} code and pull request go to {where}, where anyone with access to the repository can read them."
        )
    return {
        "pipeline": pipe["name"],
        "stages": stages,
        "managers": bosses,
        "github": github,
        "repo": repo,
        "line": " ".join(parts),
    }


def will_be_private(
    conn: "sqlite3.Connection",
    actor: str,
    target: str | None,
    private: bool | None,
    parent: Row | None,
    pipeline_id: str,
) -> bool:
    """Whether a task `actor` is about to file would be private, as hubdb.task_create decides it: asked for, else the
    filer's or the owner's private default; under a private parent; or in a private pipeline. A preview only: the
    create decides again, and its history line says what it found."""
    from . import hubdb, private_pipelines, task_engine

    H: Any = hubdb  # untyped module

    if private is False and not H.is_human(actor):
        private = None  # a bot cannot override a sensitive default to publish
    chosen = (
        private
        if private is not None
        else H.private_tasks_default(conn, actor) or bool(target and H.private_tasks_default(conn, target))
    )
    if chosen or (parent and H.task_private(conn, parent)):
        return True
    return bool(private_pipelines.is_private(task_engine.pipeline(conn, pipeline_id)))


def told(conn: "sqlite3.Connection", task_id: str, by: str) -> str | None:
    """At filing: when the new task is private and others read it as it moves, write what the filer was told into its
    history and return the line, else None."""
    from . import hubdb, task_engine

    H: Any = hubdb  # untyped module
    row: Row | None = H.task(conn, task_id)
    if not row or not row.get("pipeline_id") or not H.task_private(conn, row):
        return None
    # Its owner is not left out: the first stage's bot owns it now, and the filer is told of every stage alike.
    found = readers(conn, row["pipeline_id"], [row.get("requester_id"), *H.task_readers(row)])
    if not found:
        return None
    task_engine._event(conn, task_id, by, KIND, None, found["line"], None, {"pipeline": row["pipeline_id"]})  # pyright: ignore[reportPrivateUsage]
    return str(found["line"])
