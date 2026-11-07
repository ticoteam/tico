# pyright: strict
"""The GitHub check run a pipeline requirement publishes on the pull request at the ticket's commit (D45). Track C owns
this module.

`publish` runs inside the engine's transaction and only records what the check should say (met, failed or waiting) in
`pipeline_check_runs`, one row for each (repository, commit, requirement). It never calls GitHub. `flush` sends the rows
whose state GitHub has not been told, outside any transaction: a state is posted once, a failed post is retried with a
growing delay, and nothing is posted until the installation reports `checks: write` (github_app.CHECKS_WITHHELD). A
required "Tico" check on a branch is a repository setting, not code.
"""

import uuid
from typing import TYPE_CHECKING, Any, cast

from .store import H, Problem

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from fastapi import FastAPI

    from .auth import Auth
    from .github_app import GitHubApp
    from .store import Store

NAME = "Tico: "
MAX_ATTEMPTS = 8
LEASE_SECONDS = 120  # a row being posted is left alone this long, so two flushes never post it twice
SUMMARY = {
    "met": ("success", "Met"),
    "failed": ("failure", "Not met"),
    "waiting": (None, "Waiting"),
}
Row = dict[str, Any]


def _now() -> str:
    return str(cast(Any, H).now())


def _shift(ts: str, **delta: float) -> str:
    return str(cast(Any, H).shift(ts, **delta))


def state_of(requirement_state: str) -> str:
    """A requirement's standing as the check says it: met, failed, or waiting for anything else (missing, out of date)."""
    return requirement_state if requirement_state in ("met", "failed") else "waiting"


def _repo_of(conn: "sqlite3.Connection", task_id: str, head_sha: str) -> str | None:
    """The repository of the ticket's pull request whose head is `head_sha`."""
    found = conn.execute(
        "SELECT repo FROM task_links WHERE task_id=? AND kind='pr' AND repo IS NOT NULL AND repo<>'' "
        "AND (lower(coalesce(json_extract(detail,'$.head_sha'),''))=? OR lower(coalesce(pr_sha,''))=?) LIMIT 1",
        (task_id, head_sha, head_sha),
    ).fetchone()
    return str(found[0]) if found else None


def publish(conn: "sqlite3.Connection", task_id: str, stage_requirement_id: str, state: str, head_sha: str) -> None:
    """Records the check a requirement should show at `head_sha` (`met`, `failed`, or anything else for waiting). A state
    GitHub already has changes nothing; a new state is queued for `flush`. A commit that no pull request of the ticket
    carries has no check."""
    sha = str(head_sha or "").strip().lower()
    if not sha:
        return
    repo = _repo_of(conn, task_id, sha)
    if repo is None:
        return
    now = _now()
    conn.execute(
        "INSERT INTO pipeline_check_runs (id, task_id, stage_requirement_id, repo, head_sha, state, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(repo, head_sha, stage_requirement_id) DO UPDATE SET "
        "state=excluded.state, updated_at=excluded.updated_at, attempts=0, retry_at=NULL, last_error=NULL "
        "WHERE pipeline_check_runs.state<>excluded.state",
        (f"cr-{uuid.uuid4().hex}", task_id, stage_requirement_id, repo, sha, state_of(state), now, now),
    )


def _due(conn: "sqlite3.Connection", now: str) -> list[Row]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM pipeline_check_runs WHERE (posted_state IS NULL OR posted_state<>state) AND attempts<? "
            "AND (retry_at IS NULL OR retry_at<=?) ORDER BY created_at",
            (MAX_ATTEMPTS, now),
        ).fetchall()
    ]


def _payload(name: str, row: Row) -> Row:
    conclusion, words = SUMMARY[row["state"]]
    body: Row = {
        "name": NAME + name,
        "head_sha": row["head_sha"],
        "external_id": row["stage_requirement_id"],
        "status": "completed" if conclusion else "in_progress",
        "output": {"title": words, "summary": f"{name}: {words.lower()}"},
    }
    if conclusion:
        body["conclusion"] = conclusion
    return body


def flush(store: "Store", github_app: "GitHubApp") -> dict[str, int | bool]:
    """Posts the checks GitHub has not been told. Returns {posted, failed, withheld}: `withheld` is true (and nothing is
    posted) while the installation has not accepted Checks: write."""
    with cast(Any, store).read() as conn:
        waiting = _due(conn, _now())
    if not waiting:
        return {"posted": 0, "failed": 0, "withheld": False}
    if not cast(Any, github_app).checks_write_accepted():
        return {"posted": 0, "failed": 0, "withheld": True}
    posted = failed = 0
    for row in waiting:
        outcome = _post(store, github_app, row)
        posted += outcome is True
        failed += outcome is False
    return {"posted": posted, "failed": failed, "withheld": False}


def _post(store: "Store", github_app: "GitHubApp", row: Row) -> bool | None:
    """One row: True posted, False failed, None when another flush has it or the token is withheld."""
    now = _now()
    with cast(Any, store).transaction() as conn:
        taken = conn.execute(
            "UPDATE pipeline_check_runs SET retry_at=? WHERE id=? AND (retry_at IS NULL OR retry_at<=?) "
            "AND (posted_state IS NULL OR posted_state<>state)",
            (_shift(now, seconds=LEASE_SECONDS), row["id"], now),
        ).rowcount
        if not taken:
            return None
        row = dict(conn.execute("SELECT * FROM pipeline_check_runs WHERE id=?", (row["id"],)).fetchone())
        found = conn.execute(
            "SELECT name FROM stage_requirements WHERE id=?", (row["stage_requirement_id"],)
        ).fetchone()
    name = str(found[0]) if found else "requirement"
    try:
        token = cast("str | None", cast(Any, github_app).mint_checks(row["repo"]))
        if token is None:
            return None
        body = _payload(name, row)
        method, path = "POST", f"/repos/{row['repo']}/check-runs"
        if row["check_run_id"]:
            method, path = "PATCH", f"{path}/{row['check_run_id']}"
            body.pop("head_sha", None)
        response = cast(Any, github_app)._call(  # pyright: ignore[reportPrivateUsage]
            method, path, json=body, headers={"Authorization": "Bearer " + token}
        )
        if response.status_code >= 300:
            raise Problem("github_check", f"GitHub answered HTTP {response.status_code}", 502)
        check_run_id = cast("dict[str, Any]", response.json()).get("id") or row["check_run_id"]
    except (Problem, ValueError) as exc:
        _failed(store, row, str(getattr(exc, "detail", exc)))
        return False
    with cast(Any, store).transaction() as conn:
        conn.execute(
            "UPDATE pipeline_check_runs SET posted_state=?, check_run_id=?, posted_at=?, attempts=0, retry_at=NULL, "
            "last_error=NULL WHERE id=?",
            (row["state"], check_run_id, _now(), row["id"]),
        )
    return True


def _failed(store: "Store", row: Row, error: str) -> None:
    attempts = int(row["attempts"]) + 1
    with cast(Any, store).transaction() as conn:
        conn.execute(
            "UPDATE pipeline_check_runs SET attempts=?, retry_at=?, last_error=? WHERE id=?",
            (attempts, _shift(_now(), seconds=min(60 * 2**attempts, 3600)), error[:200], row["id"]),
        )


def tick(store: "Store", github_app: "GitHubApp") -> dict[str, int | bool]:
    """One pass of `flush` for the scheduler's rest pass (not wired in F0)."""
    return flush(store, github_app)


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """Check runs have no routes."""
    return None
