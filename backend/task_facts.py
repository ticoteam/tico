# pyright: strict
"""PR and release facts: Tico's saved copies of GitHub (docs/pipelines-v3.md "Facts", "GitHub").

Requirements read facts only (`pr_open`, `checks_pass`, `pr_merged`, `in_release` in task_requirements, `at_head` pages
in task_pages). Nothing here calls GitHub while a task is moved or checked. Two ways a fact comes in: a webhook
(backend/github.py hands it to `save_pr` / `save_release`) and the 5-minute re-read (`refresh`), through `GitHub`.

- One `pr_facts` row per PR. A task reaches its PRs through its `task_links` rows of kind `pr` (repo, number).
- Newest wins. `fact_version` is when the read or webhook that saw the fact began, in ms (`version_now`). A save with an
  older version than the row's changes nothing, so a slow read can't undo a webhook that came after it began.
- `head_seen_at` is when Tico first saw the PR's newest commit, `checks_started_at` when its checks were first seen
  running there. C13's 10 minutes and the 2 hours of running checks are measured from them.
- `queued_at` is when Tico first saw the PR queued; `removed_at` when it first saw GitHub take it out without merging.
  Each removal is one `removed_at`, and the Merge stage owner's one wake is keyed on it (merge_queue, track C).
- A repeated webhook is dropped by its delivery id (`seen_delivery`).

Every function takes the caller's open write transaction and never commits, except `refresh`, which opens its own
short ones around reads made with no transaction held.
"""

import json
import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

PR_STATES = ("open", "closed", "merged")
CHECK_STATES = ("none", "pending", "success", "failure")
QUEUE_STATES = ("none", "queued", "auto_merge", "removed")
QUEUED = ("queued", "auto_merge")  # in GitHub's merge queue, or auto-merge on where the repo has no queue
NO_CHECKS_AFTER_S = 600  # C13: no checks for a commit 10 minutes after Tico saw it, and Checks pass is met
CHECKS_TOO_LONG_S = 7200  # checks still running after 2 hours: the owner's turn
REREAD_S = 300  # a PR a task waits on is read again when its fact is older than this
REREAD_BATCH = 50  # at most this many PRs per re-read call
DELIVERY_DAYS = 7  # how long a webhook delivery id is kept for de-dup


class GitHub(Protocol):
    """The calls to GitHub that facts and the merge queue make. The server's is backend/github.py's app client; tests
    and the fault rig use backend/tests/helpers/fake_github.py `FakeGitHub`. Each raises on a failed call, with GitHub's
    reason as the message."""

    def read_pr(self, repo: str, number: int) -> Row | None:
        """{state: open|closed|merged, draft, head_sha, merged_sha, merged_at, queue_state, removed_reason,
        failing_check, failing_check_url}, or None when the PR does not exist (deleted, or no access). `queue_state` is
        `removed` when GitHub took it out of the queue (or turned its auto-merge off) without merging; `removed_reason`
        is GitHub's reason and `failing_check` / `failing_check_url` the check that failed in the queue, when one did."""
        ...

    def read_checks(self, repo: str, sha: str) -> list[Row]:
        """The check runs and statuses on a commit: [{name, app, state: pending|success|failure, url, tico}]. `tico`
        is true for Tico's own check runs, which Checks pass ignores."""
        ...

    def read_releases(self, repo: str) -> list[Row]:
        """The repo's published releases, never a draft: [{tag, commit_sha, prerelease, published_at}], newest first."""
        ...

    def contains(self, repo: str, release_sha: str, commit_sha: str) -> bool:
        """Whether `commit_sha` is in the history of `release_sha` (GitHub's compare: behind or identical)."""
        ...

    def enqueue(self, repo: str, number: int, head_sha: str) -> Row:
        """Put the PR in the merge queue at exactly `head_sha`, or turn on auto-merge where the repo has no queue, or
        merge it where it has neither. {ok, how: queued|auto_merge|merged, reason}. A head that moved is not ok."""
        ...

    def dequeue(self, repo: str, number: int) -> Row:
        """Take the PR out of the merge queue, or turn its auto-merge off. {ok, how: dequeued|merged|absent, reason}.
        `merged` means GitHub merged it first ("PR merged anyway")."""
        ...


def version_now() -> int:
    """A fact version for a read or webhook that begins now: the time in ms."""
    return time.time_ns() // 1_000_000


def pr_ref(repo: str, number: int) -> str:
    """How a PR is named in lines and in `requirement_pages.heads`: `owner/name#12`, the repo in lower case."""
    return f"{repo.lower()}#{int(number)}"


def _h() -> Any:
    from . import hubdb

    return hubdb


def _row(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> Row | None:
    cursor = conn.execute(sql, args)
    found = cursor.fetchone()
    if found is None:
        return None
    return dict(zip([d[0] for d in cursor.description], found, strict=True))


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


# ----------------------------------------------------------------------------- PRs
def pr(conn: "sqlite3.Connection", repo: str, number: int) -> Row | None:
    """The saved fact of one PR (a `pr_facts` row with `checks` loaded), or None when Tico has none yet."""
    found = _row(conn, "SELECT * FROM pr_facts WHERE repo=? AND number=?", (repo, int(number)))
    if found:
        found["checks"] = json.loads(found["checks"] or "[]")
    return found


def save_pr(conn: "sqlite3.Connection", repo: str, number: int, fact: Row, version: int) -> bool:
    """Save what GitHub said of a PR, as seen by a read or webhook that began at `version`. `fact` has the keys
    `GitHub.read_pr` gives, and `checks` (`GitHub.read_checks` at its head) when they were read; a key left out keeps
    the saved value. False, and nothing written, when the saved fact is newer. `checks_state` is worked out here from
    `checks`, leaving out Tico's own."""
    H = _h()
    now = H.now()
    old = pr(conn, repo, number)
    if old and int(old["fact_version"]) > version:
        return False
    merged: Row = {**(old or {}), **{k: v for k, v in fact.items() if k != "checks"}}
    head = merged.get("head_sha")
    moved = not old or old.get("head_sha") != head
    checks: list[Row] = fact["checks"] if "checks" in fact else ([] if moved else list((old or {}).get("checks") or []))
    state = _checks_state(checks)
    was_running = bool(old) and not moved and (old or {}).get("checks_state") == "pending"
    queue = str(merged.get("queue_state") or "none")
    was_queued = queue in QUEUED and (old or {}).get("queue_state") in QUEUED
    was_removed = queue == "removed" and (old or {}).get("queue_state") == "removed"
    values = {
        "state": merged.get("state") or "open",
        "draft": 1 if merged.get("draft") else 0,
        "head_sha": head,
        "head_seen_at": now if moved else (old or {}).get("head_seen_at"),
        "checks_state": state,
        "checks": json.dumps(checks),
        "checks_started_at": ((old or {}).get("checks_started_at") if was_running else now)
        if state == "pending"
        else None,
        "merged_sha": merged.get("merged_sha"),
        "merged_at": merged.get("merged_at"),
        "queue_state": queue,
        "queued_at": (old or {}).get("queued_at") if was_queued else (now if queue in QUEUED else None),
        "removed_at": ((old or {}).get("removed_at") if was_removed else now) if queue == "removed" else None,
        "removed_reason": merged.get("removed_reason") if queue == "removed" else None,
        "failing_check": merged.get("failing_check") if queue == "removed" else None,
        "failing_check_url": merged.get("failing_check_url") if queue == "removed" else None,
        "fact_version": version,
        "read_at": now,
        "updated_at": now,
    }
    if old:
        sets = ", ".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE pr_facts SET {sets} WHERE id=?", (*values.values(), old["id"]))
    else:
        keys = ["id", "repo", "number", "created_at", *values]
        conn.execute(
            f"INSERT INTO pr_facts ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
            (H.new_id(), repo, int(number), now, *values.values()),
        )
    return True


def _checks_state(checks: list[Row]) -> str:
    """failure if any of the checks that count failed, else pending if any runs, else success; none when there are none.
    Tico's own check runs (`tico`) never count."""
    states = [str(check.get("state") or "") for check in checks if not check.get("tico")]
    if not states:
        return "none"
    if "failure" in states:
        return "failure"
    if "pending" in states:
        return "pending"
    return "success"


def prs_of(conn: "sqlite3.Connection", task_id: str) -> list[Row]:
    """The PRs linked to a task, oldest link first: [{repo, number, url, fact}] where `fact` is `pr()` or None."""
    out: list[Row] = []
    for link in _rows(
        conn,
        "SELECT repo, number, url FROM task_links WHERE task_id=? AND kind='pr' AND repo IS NOT NULL "
        "AND number IS NOT NULL ORDER BY created_at, id",
        (task_id,),
    ):
        out.append({**link, "fact": pr(conn, str(link["repo"]), int(link["number"]))})
    return out


def heads(conn: "sqlite3.Connection", task_id: str) -> dict[str, str | None]:
    """{pr_ref: its newest commit} for every PR linked to the task that was not closed without merging. A page `at_head`
    saves this when it is attached and is for the newest commit while it still equals this. None: no fact yet."""
    out: dict[str, str | None] = {}
    for link in prs_of(conn, task_id):
        fact = link["fact"]
        if fact and fact["state"] == "closed":
            continue
        out[pr_ref(str(link["repo"]), int(link["number"]))] = fact["head_sha"] if fact else None
    return out


def due_reads(conn: "sqlite3.Connection", now: str, limit: int = REREAD_BATCH) -> list[tuple[str, int]]:
    """The PRs an open task waits on whose fact is missing or older than REREAD_S, oldest read first, at most `limit`.
    Track C."""
    raise NotImplementedError("task_facts.due_reads: track C")


def refresh(store: Any, github: GitHub, now: str | None = None) -> int:
    """The 5-minute re-read: each of `due_reads` read through `github` with no transaction held, then saved with the
    version taken before its read. Also re-reads the releases of every release repo an open task waits on. Returns how
    many facts changed. Track C."""
    raise NotImplementedError("task_facts.refresh: track C")


# ----------------------------------------------------------------------------- releases
def save_release(conn: "sqlite3.Connection", repo: str, release: Row, version: int) -> bool:
    """Save one published release (never a draft): {tag, commit_sha, prerelease, published_at}. False when the saved
    one is newer."""
    H = _h()
    now = H.now()
    old = _row(conn, "SELECT id, fact_version FROM releases WHERE repo=? AND tag=?", (repo, release["tag"]))
    if old and int(old["fact_version"]) > version:
        return False
    values = (
        release.get("commit_sha"),
        1 if release.get("prerelease") else 0,
        release.get("published_at") or now,
        version,
        now,
    )
    if old:
        conn.execute(
            "UPDATE releases SET commit_sha=?, prerelease=?, published_at=?, fact_version=?, read_at=? WHERE id=?",
            (*values, old["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO releases (commit_sha, prerelease, published_at, fact_version, read_at, id, repo, tag, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*values, H.new_id(), repo, release["tag"], now),
        )
    return True


def saw_releases(conn: "sqlite3.Connection", repo: str, version: int) -> None:
    """Record that a repo's releases were read in full at `version` (`release_reads`), with or without any."""
    conn.execute(
        "INSERT INTO release_reads (repo, read_at, fact_version) VALUES (?,?,?) ON CONFLICT(repo) DO UPDATE SET "
        "read_at=excluded.read_at, fact_version=excluded.fact_version WHERE excluded.fact_version >= fact_version",
        (repo, _h().now(), version),
    )


def releases(conn: "sqlite3.Connection", repo: str, *, prereleases: bool = False) -> list[Row]:
    """A repo's saved releases that count, newest first: a pre-release only when `prereleases` (D3)."""
    return _rows(
        conn,
        "SELECT * FROM releases WHERE repo=? AND (? OR prerelease=0) ORDER BY published_at DESC, id",
        (repo, 1 if prereleases else 0),
    )


def publishes_releases(conn: "sqlite3.Connection", repo: str) -> bool | None:
    """Whether a repo publishes releases: True when any release of it is saved (a pre-release too), False when its
    releases were read and there were none (C31), None when they were never read."""
    if conn.execute("SELECT 1 FROM releases WHERE repo=? LIMIT 1", (repo,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM release_reads WHERE repo=?", (repo,)).fetchone():
        return False
    return None


def included(conn: "sqlite3.Connection", release_id: str, commit_sha: str) -> bool | None:
    """Whether a commit is in a release's history, as read from GitHub; None when not read yet."""
    found = conn.execute(
        "SELECT included FROM release_commits WHERE release_id=? AND commit_sha=?", (release_id, commit_sha)
    ).fetchone()
    return None if found is None else bool(found[0])


def save_included(conn: "sqlite3.Connection", release_id: str, commit_sha: str, value: bool) -> None:
    """Record whether a commit is in a release's history. A release's history never changes, so it is read once."""
    conn.execute(
        "INSERT OR REPLACE INTO release_commits (release_id, commit_sha, included, checked_at) VALUES (?,?,?,?)",
        (release_id, commit_sha, 1 if value else 0, _h().now()),
    )


# ----------------------------------------------------------------------------- webhook de-dup
def seen_delivery(conn: "sqlite3.Connection", delivery_id: str | None, event: str) -> bool:
    """Whether this webhook delivery was taken before. The first time, it is recorded and False is returned. A delivery
    with no id is never a repeat."""
    if not delivery_id:
        return False
    taken = conn.execute(
        "INSERT OR IGNORE INTO github_deliveries (id, event, received_at) VALUES (?,?,?)",
        (delivery_id, event, _h().now()),
    ).rowcount
    return taken == 0
