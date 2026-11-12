# pyright: strict
"""Bot repositories made without anyone asking (docs/github-app.md "Creating bot repositories").

When the GitHub App may create repositories (installed on all repositories, Administration accepted), each bot whose
own repository GitHub does not have yet gets it here, a few at a time, from the server's poll (heartbeat.py
`repository_waits`): a company that connects GitHub with twenty bots, and every bot hired later. The create is
GitHubApp.create_repo, the one `tico bot repo-create --empty` uses, so a lost answer or a second try never doubles a
repository or marks a just-made one as someone else's. A GitHub error or rate limit waits and tries again, longer each
time; a refused permission stops it until the permission is back."""

import logging
import time
from collections.abc import Callable
from typing import Any

from . import hubdb, store

# The modules below are not strictly typed yet; what is read from them is typed where it is used.
H: Any = hubdb
Problem: Any = store.Problem

log = logging.getLogger("tico.github")

STATE = "bot-repositories-making"  # {"failures": n, "retry_at": epoch seconds, "problem": words for Tools > GitHub}
PER_PASS = 3  # creates in one pass; with GAP, about twelve a minute, well under GitHub's limit on creating content
GAP = 15  # seconds between passes
LONGEST_WAIT = 3600
# Refusals that mean the App may not create here: nothing more is tried until the stored permission says it may.
STOPS = ("github_permission_missing", "github_selected_repos", "github_user_token", "github_not_connected")
SYSTEM = "system"


def survey(c: Any, settings: Any, row: Any = None) -> tuple[dict[str, int], list[str]]:
    """({"made": n, "taken": n, "waiting": n}, the waiting bots' slugs in name order) for the bots whose own repository
    is `<connected org>/<the name Tico creates>`: one a person pointed elsewhere, or a branch's, is not Tico's to make."""
    from . import execution
    from .repositories import Homes

    work: Any = execution  # its repository readers are untyped
    app: Any = row or work._github_app(c)
    counts: dict[str, int] = {"made": 0, "taken": 0, "waiting": 0}
    waiting: list[str] = []
    if not app:
        return counts, waiting
    homes: Any = Homes(c)
    there: set[str] = {str(r[0]).lower() for r in c.execute("SELECT full_name FROM repositories WHERE reachable=1")}
    bots: list[Any] = c.execute("SELECT id,slug FROM bots WHERE state<>'archived' ORDER BY slug").fetchall()
    for bot in bots:
        found: tuple[str, str] = work._repository_of(c, settings, bot["id"], app=(app,))
        repository, owner = found
        named: str = H.bot_repository_name(c, bot["id"])
        if owner != bot["id"] or repository.lower() != f"{app['org']}/{named}".lower():
            continue
        name = repository.lower()
        if name in homes.taken or (name not in homes.ours and homes.why(bot["id"], repository)):
            counts["taken"] += 1
        elif name in homes.ours or name in there:
            counts["made"] += 1
        else:
            counts["waiting"] += 1
            waiting.append(str(bot["slug"]))
    return counts, waiting


def may_create(c: Any, service: Any, row: Any) -> bool:
    """What the server already knows, with no call to GitHub: installed on all repositories with Administration, and
    for a personal account the owner's authorization."""
    from . import github_app, repositories

    app: Any = github_app
    homes: Any = repositories
    if not row or not row["installation_id"] or not row["administration"] or homes.selection(c) == "selected":
        return False
    return app.account_kind(row) != app.PERSONAL or bool(service.user_authorized(c))


def progress(c: Any, service: Any, settings: Any) -> dict[str, Any]:
    """Tools > GitHub's one line: how many bot repositories are made, and why the rest wait, if GitHub said."""
    from . import repositories

    homes: Any = repositories
    counts, _ = survey(c, settings)
    state: dict[str, Any] = homes.metadata(c, STATE)
    return {**counts, "problem": str(state.get("problem") or "") if counts["waiting"] else ""}


def run(service: Any, now: Callable[[], float] = time.time, limit: int = PER_PASS) -> list[str]:
    """One pass: up to `limit` creates. Returns the repositories made. Called holding no transaction."""
    from .github_app import DEFAULT_TEMPLATE
    from . import repositories

    homes: Any = repositories
    store, settings = service.store, service.settings
    if now() < getattr(service, "bot_repo_next", 0):
        return []
    with store.read() as c:
        row = service.row(c)
        if not may_create(c, service, row):
            return []
        state: dict[str, Any] = homes.metadata(c, STATE)
        waiting = survey(c, settings, row)[1]
    if not waiting or float(state.get("retry_at") or 0) > now():
        if not waiting and state:
            with store.transaction() as c:
                homes.save_metadata(c, STATE, {})
        return []
    service.bot_repo_next = now() + GAP
    made: list[str] = []
    problem: Any = None
    for slug in waiting[:limit]:
        try:
            result: dict[str, Any] = service.create_repo(slug, DEFAULT_TEMPLATE, empty=True)
        except Problem as raised:
            if getattr(raised, "code", "") == "github_repo_taken":
                continue  # remembered as someone else's (GitHubApp._existing): the bot works from its computer
            problem = raised
            break
        made.append(str(result["repository"]))
        if not result.get("existing"):
            with store.transaction() as c:
                H.event(c, SYSTEM, "github.repo_created", result["repository"], {"empty": True, "automatic": True})
    with store.transaction() as c:
        if problem is None or problem.code in STOPS:
            homes.save_metadata(c, STATE, {})
        else:
            failures = int(state.get("failures") or 0) + 1
            wait = min(LONGEST_WAIT, 60 * 2 ** (failures - 1))
            homes.save_metadata(
                c, STATE, {"failures": failures, "retry_at": now() + wait, "problem": str(problem.detail)[:300]}
            )
            log.warning("Bot repositories: GitHub refused a create (%s); trying again in %s s", problem.code, wait)
    return made
