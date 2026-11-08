# pyright: strict
"""The GitHub check run a pipeline requirement publishes on the pull request at the ticket's commit (D45). Track C owns
this module.

`publish` runs inside a transaction and only records what the check should say (met, failed or waiting) in
`pipeline_check_runs`, one row for each (repository, commit, requirement). It never calls GitHub. `publish_open` records
the checks of every ticket that has a pull request and a stage with requirements; `dispatch` runs it and then `flush` on
its own thread, outside any transaction. `flush` sends the rows whose state GitHub has not been told: a state is posted
once, a failed post is retried with a growing delay, and nothing is posted until the installation reports `checks: write`
(github_app.CHECKS_WITHHELD). A post holds a lease (`lease_until`, `lease_token`) apart from the retry timing, so a state
change never lets a second flush post the same check. A required "Tico" check on a branch is a repository setting, not code.
"""

import logging
import threading
import uuid
from typing import TYPE_CHECKING, Any, cast

from .store import H, Problem

LOG = logging.getLogger("tico.pipeline_github_checks")
_running = threading.Lock()
PUBLISH_BATCH = 200  # tickets recorded per pass; the rest wait for the next one

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


def _heads(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    """The head commits of the ticket's pull requests, lowercase, sorted."""
    return sorted(
        {
            str(r[0])
            for r in conn.execute(
                "SELECT lower(json_extract(detail,'$.head_sha')) FROM task_links WHERE task_id=? AND kind='pr' "
                "AND json_extract(detail,'$.head_sha') IS NOT NULL",
                (task_id,),
            ).fetchall()
        }
    )


def publish_affected(conn: "sqlite3.Connection", task_id: str) -> int:
    """Records, in the caller's transaction, the check of every requirement whose standing the ticket's work or decisions can
    change: each requirement of its current stage and of each stage it has left, at each pull request head the ticket has.
    The standings are the engine's (pipeline_engine.task_standings). Every write that can change one calls this in its own
    transaction: an approval, an undo, a Mark met, a Not validated, an attachment, its removal, and a change of work. Returns
    how many checks were recorded; nothing is posted here."""
    from . import pipeline_engine  # the requirement's standing is the engine's reading of the same facts

    heads = _heads(conn, task_id)
    if not heads:
        return 0
    recorded = 0
    for requirement_id, standing in pipeline_engine.task_standings(conn, [task_id]).get(task_id, {}).items():
        for head in heads:
            publish(conn, task_id, requirement_id, standing, head)
            recorded += 1
    return recorded


def _cursor_of(conn: "sqlite3.Connection") -> str:
    """The last ticket a pass recorded (pipeline_check_cursor, one row), '' when none: the next pass starts from the first."""
    row = conn.execute("SELECT task_id FROM pipeline_check_cursor WHERE id=1").fetchone()
    return str(row[0]) if row else ""


def publish_open(store: "Store", limit: int = PUBLISH_BATCH) -> int:
    """Records the checks of the tickets that have a pull request and a stage with requirements: one batch of tickets after
    the stored cursor, one transaction each, then the cursor moves past them. A short batch stores '' so the next pass
    starts from the first ticket. The cursor is in the database, so a restart or another process resumes the same way.
    Returns how many checks were recorded."""
    with cast(Any, store).read() as conn:
        after = _cursor_of(conn)
        ids = [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT l.task_id FROM task_links l JOIN tasks t ON t.id=l.task_id "
                "WHERE l.kind='pr' AND json_extract(l.detail,'$.head_sha') IS NOT NULL AND t.step_id IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM stage_requirements r WHERE r.pipeline_stage_id=t.step_id "
                "AND r.retired_at IS NULL) AND l.task_id>? ORDER BY l.task_id LIMIT ?",
                (after, limit),
            ).fetchall()
        ]
    recorded = 0
    for task_id in ids:
        with cast(Any, store).transaction() as conn:
            recorded += publish_affected(conn, task_id)
    with cast(Any, store).transaction() as conn:
        conn.execute(
            "INSERT INTO pipeline_check_cursor (id, task_id) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id",
            (ids[-1] if len(ids) >= limit else "",),
        )
    return recorded


def _due(conn: "sqlite3.Connection", now: str) -> list[Row]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM pipeline_check_runs WHERE (posted_state IS NULL OR posted_state<>state) AND attempts<? "
            "AND (retry_at IS NULL OR retry_at<=?) AND (lease_until IS NULL OR lease_until<=?) ORDER BY created_at",
            (MAX_ATTEMPTS, now, now),
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
    """One row: True posted, False failed, None when another flush holds its lease or the token is withheld. The lease
    token is what the acknowledgement must still carry, so a flush whose lease ran out writes nothing back."""
    now = _now()
    token = uuid.uuid4().hex
    with cast(Any, store).transaction() as conn:
        taken = conn.execute(
            "UPDATE pipeline_check_runs SET lease_until=?, lease_token=? WHERE id=? "
            "AND (lease_until IS NULL OR lease_until<=?) AND (retry_at IS NULL OR retry_at<=?) "
            "AND (posted_state IS NULL OR posted_state<>state)",
            (_shift(now, seconds=LEASE_SECONDS), token, row["id"], now, now),
        ).rowcount
        if not taken:
            return None
        row = dict(conn.execute("SELECT * FROM pipeline_check_runs WHERE id=?", (row["id"],)).fetchone())
        found = conn.execute(
            "SELECT name FROM stage_requirements WHERE id=?", (row["stage_requirement_id"],)
        ).fetchone()
    name = str(found[0]) if found else "requirement"
    try:
        minted = cast("str | None", cast(Any, github_app).mint_checks(row["repo"]))
        if minted is None:
            _release(store, row, token)
            return None
        body = _payload(name, row)
        method, path = "POST", f"/repos/{row['repo']}/check-runs"
        if row["check_run_id"]:
            method, path = "PATCH", f"{path}/{row['check_run_id']}"
            body.pop("head_sha", None)
        response = cast(Any, github_app)._call(  # pyright: ignore[reportPrivateUsage]
            method, path, json=body, headers={"Authorization": "Bearer " + minted}
        )
        if response.status_code >= 300:
            raise Problem("github_check", f"GitHub answered HTTP {response.status_code}", 502)
        check_run_id = cast("dict[str, Any]", response.json()).get("id") or row["check_run_id"]
    except (Problem, ValueError) as exc:
        _failed(store, row, token, str(getattr(exc, "detail", exc)))
        return False
    with cast(Any, store).transaction() as conn:
        # The state posted is the one this lease sent; a newer state stays unposted and goes out on the next pass.
        conn.execute(
            "UPDATE pipeline_check_runs SET posted_state=?, check_run_id=?, posted_at=?, attempts=0, retry_at=NULL, "
            "last_error=NULL, lease_until=NULL, lease_token=NULL WHERE id=? AND lease_token=?",
            (row["state"], check_run_id, _now(), row["id"], token),
        )
    return True


def _release(store: "Store", row: Row, token: str) -> None:
    with cast(Any, store).transaction() as conn:
        conn.execute(
            "UPDATE pipeline_check_runs SET lease_until=NULL, lease_token=NULL WHERE id=? AND lease_token=?",
            (row["id"], token),
        )


def _failed(store: "Store", row: Row, token: str, error: str) -> None:
    attempts = int(row["attempts"]) + 1
    with cast(Any, store).transaction() as conn:
        conn.execute(
            "UPDATE pipeline_check_runs SET attempts=?, retry_at=?, last_error=?, lease_until=NULL, lease_token=NULL "
            "WHERE id=? AND lease_token=?",
            (attempts, _shift(_now(), seconds=min(60 * 2**attempts, 3600)), error[:200], row["id"], token),
        )


def dispatch(store: "Store", github_app: "GitHubApp", failures: list[dict[str, str]]) -> threading.Thread | None:
    """The scheduler's one call into this module: records the checks, then posts what GitHub has not been told. It runs on
    its own thread, so GitHub never holds a tick; one pass at a time. Returns the thread, or None when a pass is running."""
    if not _running.acquire(blocking=False):
        return None
    try:
        thread = threading.Thread(target=_pass, args=(store, github_app), name="pipeline-checks", daemon=True)
        thread.start()
    except RuntimeError as exc:
        _running.release()
        failures.append({"pipeline_github_checks": type(exc).__name__})
        return None
    return thread


def _pass(store: "Store", github_app: "GitHubApp") -> None:
    """The pass on its thread: a failure is logged and the next pass still runs."""
    try:
        publish_open(store)
        flush(store, github_app)
    except Exception as exc:  # noqa: BLE001 - a thread must not die silently; the next pass finds the rows still waiting
        LOG.warning("The pipeline check pass failed: %s", type(exc).__name__)
    finally:
        _running.release()


def tick(store: "Store", github_app: "GitHubApp") -> dict[str, int | bool]:
    """One pass of `publish_open` and `flush`, run synchronously (tests and tools); the scheduler uses `dispatch`."""
    publish_open(store)
    return flush(store, github_app)


def install(app: "FastAPI", store: "Store", mutate: "Callable[..., Any]", auth: "Auth") -> None:
    """Check runs have no routes."""
    return None
