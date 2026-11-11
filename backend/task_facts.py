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
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

PR_STATES = ("open", "closed", "merged")
CHECK_STATES = ("none", "pending", "success", "failure")
QUEUE_STATES = ("none", "queued", "auto_merge", "removed")
QUEUED = ("queued", "auto_merge")  # in GitHub's merge queue, or auto-merge on where the repo has no queue
NO_CHECKS_AFTER_S = 600  # C13: no checks for a commit 10 minutes after Tico saw it, and Checks pass is met
CHECKS_TOO_LONG_S = 7200  # checks still running after 2 hours: the owner's turn
SILENT_S = 7200  # a wait on GitHub with no word from GitHub for 2 hours (down, refusing, no such PR): the owner's turn
REREAD_S = 300  # a PR a task waits on is read again when its fact is older than this
REREAD_BATCH = 50  # at most this many PRs per re-read call
DELIVERY_DAYS = 7  # how long a webhook delivery id is kept for de-dup
LOG = logging.getLogger("tico.task_facts")


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
    """The PRs linked to a task, oldest link first: [{repo, number, url, created_at, fact}] where `fact` is `pr()` or
    None."""
    out: list[Row] = []
    for link in _rows(
        conn,
        "SELECT repo, number, url, created_at FROM task_links WHERE task_id=? AND kind='pr' AND repo IS NOT NULL "
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


def linked_tasks(conn: "sqlite3.Connection", repo: str, number: int) -> list[str]:
    """The open tasks on the engine that link this PR, oldest link first."""
    return [
        str(found[0])
        for found in conn.execute(
            "SELECT l.task_id FROM task_links l JOIN tasks t ON t.id = l.task_id WHERE l.kind='pr' "
            "AND lower(l.repo)=lower(?) AND l.number=? AND t.status='open' AND t.pipeline_id IS NOT NULL "
            "GROUP BY l.task_id ORDER BY MIN(l.created_at), l.task_id",
            (repo, int(number)),
        ).fetchall()
    ]


def _nudge(conn: "sqlite3.Connection", task_ids: list[str]) -> None:
    """A fact came in: move each task on if it now has all it needs (`task_engine.advance`). Each in a savepoint, so
    one that cannot move leaves the others and the saved fact as they are; the never-stuck check tries it again."""
    from . import task_engine

    for task_id in task_ids:
        conn.execute("SAVEPOINT nudge")
        try:
            task_engine.advance(conn, task_id)
        except Exception as exc:  # noqa: BLE001 - the fact stands; the next never-stuck pass moves the task
            conn.execute("ROLLBACK TO nudge")
            LOG.warning("Moving %s on after a GitHub fact failed: %s", task_id, exc)
        conn.execute("RELEASE nudge")


def save_and_settle(conn: "sqlite3.Connection", repo: str, number: int, fact: Row, version: int) -> int:
    """`save_pr`, then what a changed fact causes: a queue removal's one wake, the Merge stage's own reactions
    (merge_queue.fact_changed) and a nudge of every open task that links the PR. 1 when the fact was saved, else 0."""
    from . import merge_queue

    before = pr(conn, repo, number)
    if not save_pr(conn, repo, number, fact, version):
        return 0
    after = pr(conn, repo, number)
    if not after:
        return 0
    if after["queue_state"] == "removed" and (not before or before.get("removed_at") != after["removed_at"]):
        merge_queue.removed(conn, after)
    tasks = linked_tasks(conn, repo, number)
    merge_queue.fact_changed(conn, tasks, after, before)
    _nudge(conn, tasks)
    return 1


# ----------------------------------------------------------------------------- webhooks
REMOVAL_WORDS = {
    "ci_failure": "Checks failed in the merge queue",
    "failed_checks": "Checks failed in the merge queue",
    "ci_timeout": "Checks timed out in the merge queue",
    "merge_conflict": "It conflicts with its base branch",
    "manual": "Taken out by hand",
    "queue_cleared": "The merge queue was cleared",
    "roll_back": "The merge queue rolled back",
    "branch_protections": "Branch protection blocked it",
    "git_tree_invalid": "GitHub could not build the merge",
    "invalid_merge_commit": "GitHub could not build the merge",
}
MERGED_REASONS = ("merge", "merged", "already_merged")


def removal_words(reason: object) -> str:
    """GitHub's reason a PR left the merge queue (or lost its auto-merge), as a sentence."""
    text = str(reason or "").strip()
    key = text.lower().replace(" ", "_")
    if key in REMOVAL_WORDS:
        return REMOVAL_WORDS[key]
    if not text:
        return "GitHub gave no reason"
    words = text.replace("_", " ")
    return words[:1].upper() + words[1:].lower() if words.isupper() else words[:1].upper() + words[1:]


def check_state(status: object, conclusion: object) -> str:
    """A check run's or status's state as a fact keeps it: pending, success or failure."""
    if str(status or "completed") not in ("completed", "success", "failure", "error"):
        return "pending"
    result = str(conclusion or status or "")
    if result in ("success", "neutral", "skipped"):
        return "success"
    if result in ("pending", "queued", "in_progress", "requested", "waiting", ""):
        return "pending"
    return "failure"


def _pr_fact_of(payload: Row) -> tuple[Row, bool]:
    """A `pull_request` webhook as a fact, and whether it took the PR out of the merge queue (or its auto-merge off)
    for a reason other than a merge. That removal is not saved from the webhook: its failing check is not in it, so the
    PR is read in full on the next pass (`refresh`), and the one wake carries the check."""
    found: Row = payload.get("pull_request") or {}
    action = str(payload.get("action") or "")
    merged = bool(found.get("merged") or found.get("merged_at"))
    fact: Row = {
        "state": "merged" if merged else "closed" if found.get("state") == "closed" else "open",
        "draft": bool(found.get("draft")),
        "head_sha": _obj(found.get("head")).get("sha"),
    }
    if merged:
        fact.update(merged_sha=found.get("merge_commit_sha"), merged_at=found.get("merged_at"), queue_state="none")
    elif action == "enqueued":
        fact["queue_state"] = "queued"
    elif action == "auto_merge_enabled":
        fact["queue_state"] = "auto_merge"
    elif action in ("dequeued", "auto_merge_disabled"):
        if str(payload.get("reason") or "").lower() in MERGED_REASONS:
            fact["queue_state"] = "none"
        else:
            return fact, True
    return fact, False


def _obj(value: Any) -> Row:
    """A JSON object from a webhook payload, or {} when it is missing or not an object."""
    return cast(Row, value) if isinstance(value, dict) else {}


def webhook(conn: "sqlite3.Connection", event: str, payload: Row, app_slug: str | None = None) -> list[str]:
    """Save what a GitHub webhook says as facts, in the webhook's transaction (backend/github.py dropped a repeated
    delivery first: `seen_delivery`). Only PRs a task links are kept. A check run or status changes that one check in
    the fact of each open PR whose newest commit it is on. A published release is saved. Returns the refs of the PRs
    whose fact changed."""
    repo = str(_obj(payload.get("repository")).get("full_name") or "")
    if not repo:
        return []
    version = version_now()
    out: list[str] = []
    if event == "pull_request":
        number = int(_obj(payload.get("pull_request")).get("number") or payload.get("number") or 0)
        if (
            not number
            or not conn.execute(
                "SELECT 1 FROM task_links WHERE kind='pr' AND lower(repo)=lower(?) AND number=? LIMIT 1", (repo, number)
            ).fetchone()
        ):
            return []
        fact, removal = _pr_fact_of(payload)
        if save_and_settle(conn, repo, number, fact, version):
            out.append(pr_ref(repo, number))
        if removal:
            conn.execute("UPDATE pr_facts SET read_at='' WHERE repo=? AND number=?", (repo, number))
    elif event in ("check_run", "status"):
        if event == "check_run":
            run: Row = payload.get("check_run") or {}
            sha = str(run.get("head_sha") or "")
            slug = str(_obj(run.get("app")).get("slug") or "")
            check: Row = {
                "name": str(run.get("name") or "Checks")[:200],
                "app": slug,
                "state": check_state(run.get("status"), run.get("conclusion")),
                "url": run.get("html_url") or run.get("details_url") or "",
                "tico": bool(app_slug) and slug == app_slug,
            }
        else:
            sha = str(payload.get("sha") or "")
            check = {
                "name": str(payload.get("context") or "Checks")[:200],
                "app": "status",
                "state": check_state(payload.get("state"), payload.get("state")),
                "url": payload.get("target_url") or "",
                "tico": False,
            }
        for number in [
            int(found[0])
            for found in conn.execute(
                "SELECT number FROM pr_facts WHERE repo=? AND head_sha=? AND state='open'", (repo, sha)
            ).fetchall()
        ]:
            old: list[Row] = (pr(conn, repo, number) or {}).get("checks") or []
            checks = [c for c in old if c.get("name") != check["name"]] + [check]
            if save_and_settle(conn, repo, number, {"checks": checks}, version):
                out.append(pr_ref(repo, number))
    elif event == "release":
        release: Row = payload.get("release") or {}
        if payload.get("action") in ("published", "released", "prereleased") and not release.get("draft"):
            if release.get("tag_name"):
                save_release(
                    conn,
                    repo,
                    {
                        "tag": release["tag_name"],
                        "commit_sha": None,
                        "prerelease": bool(release.get("prerelease")),
                        "published_at": release.get("published_at"),
                    },
                    version,
                )
    return out


# The kinds that read a PR fact. A page reads one only when it is for the newest commit (`at_head`).
PR_KINDS = ("pr_open", "checks_pass", "pr_merged", "in_release")
# The open tasks on the engine, with their open visit, whose stage or visit reads PR facts, or whose stage queues PRs.
_WAITING_TASKS = (
    "SELECT t.id FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
    "JOIN stages s ON s.id = t.stage_id WHERE t.status='open' AND t.pipeline_id IS NOT NULL AND ("
    "COALESCE(json_extract(CASE WHEN json_valid(s.on_enter) THEN s.on_enter END, '$.queue_prs'), 0) "
    "OR EXISTS (SELECT 1 FROM requirements r WHERE (r.stage_id = t.stage_id OR r.visit_id = v.id) "
    "AND r.withdrawn_at IS NULL AND (r.kind IN ('pr_open', 'checks_pass', 'pr_merged', 'in_release') "
    "OR r.kind = 'page' AND COALESCE(json_extract(CASE WHEN json_valid(r.settings) THEN r.settings END, "
    "'$.at_head'), 0))))"
)


def due_reads(conn: "sqlite3.Connection", now: str, limit: int = REREAD_BATCH) -> list[tuple[str, int]]:
    """The PRs an open task waits on whose fact is missing or older than REREAD_S, oldest read first, at most `limit`.
    A merged or closed PR is final for what a task waits on, so it is not read again (a webhook still brings a reopen).
    """
    cutoff = _h().shift(now, seconds=-REREAD_S)
    found = conn.execute(
        "SELECT MIN(l.repo), l.number, MIN(COALESCE(f.read_at, '')) AS seen FROM task_links l "
        f"JOIN ({_WAITING_TASKS}) w ON w.id = l.task_id "
        "LEFT JOIN pr_facts f ON f.repo = l.repo AND f.number = l.number "
        "WHERE l.kind='pr' AND l.repo IS NOT NULL AND l.repo <> '' AND l.number IS NOT NULL "
        "AND (f.id IS NULL OR f.state='open' AND f.read_at < ?) "
        "GROUP BY lower(l.repo), l.number ORDER BY seen, lower(l.repo), l.number LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [(str(repo), int(number)) for repo, number, _ in found]


def read_pr(github: GitHub, repo: str, number: int) -> Row | None:
    """What GitHub says of one PR now, with the checks at its head while it is open: a `save_pr` fact. None when the PR
    is gone. Raises when GitHub could not be asked."""
    found = github.read_pr(repo, number)
    if found is None:
        return None
    fact = dict(found)
    if fact.get("state") == "open" and fact.get("head_sha"):
        fact["checks"] = github.read_checks(repo, str(fact["head_sha"]))
    return fact


def refresh(store: Any, github: GitHub, now: str | None = None) -> int:
    """The 5-minute re-read: each of `due_reads` read through `github` with no transaction held, then saved with the
    version taken before its read. Also re-reads the releases of every release repo an open task waits on, and asks
    GitHub whether each waiting merge is in each newer release. A call GitHub fails is logged and left for the next
    pass: the fact stays as it was, so what waits on it keeps waiting on GitHub. Returns how many facts changed."""
    H = _h()
    at: str = now or H.now()
    with store.transaction() as conn:
        conn.execute("DELETE FROM github_deliveries WHERE received_at < ?", (H.shift(at, days=-DELIVERY_DAYS),))
    with store.read() as conn:
        due = due_reads(conn, at)
    changed = 0
    for repo, number in due:
        version = version_now()
        try:
            fact = read_pr(github, repo, number)
        except Exception as exc:  # noqa: BLE001 - GitHub down or refusing: the fact waits for the next pass
            LOG.warning("Reading %s from GitHub failed: %s", pr_ref(repo, number), exc)
            continue
        if fact is None:
            continue
        with store.transaction() as conn:
            changed += save_and_settle(conn, repo, number, vanished(pr(conn, repo, number), fact), version)
    changed += _refresh_releases(store, github, at)
    return changed


def vanished(old: Row | None, fact: Row) -> Row:
    """A re-read that finds an open PR Tico saw queued now neither queued nor removed: it left the merge queue and no
    webhook or timeline event said so. That is a removal, with no reason, so the Merge stage's owner hears of it at
    once (merge_queue.removed) instead of "In the merge queue" waiting for ever."""
    if old and old.get("queue_state") in QUEUED and fact.get("state") == "open" and fact.get("queue_state") == "none":
        return {**fact, "queue_state": "removed", "removed_reason": removal_words(None)}
    return fact


def _release_waits(conn: "sqlite3.Connection") -> list[Row]:
    """Each merged PR an open task's In a release waits on: {task, repo (the release repo), sha, at, prereleases}."""
    out: list[Row] = []
    for task_id, settings in conn.execute(
        "SELECT t.id, r.settings FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "JOIN requirements r ON (r.stage_id = t.stage_id OR r.visit_id = v.id) AND r.withdrawn_at IS NULL "
        "AND r.kind='in_release' WHERE t.status='open' AND t.pipeline_id IS NOT NULL"
    ).fetchall():
        try:
            setting: Row = json.loads(settings or "{}") or {}
        except ValueError:
            setting = {}
        for link in prs_of(conn, str(task_id)):
            fact = link["fact"]
            if fact and fact["state"] == "merged" and fact.get("merged_sha"):
                out.append(
                    {
                        "task": str(task_id),
                        "repo": release_repo(setting, str(link["repo"])),
                        "sha": str(fact["merged_sha"]),
                        "at": fact.get("merged_at"),
                        "prereleases": bool(setting.get("prereleases")),
                    }
                )
    return out


def release_repo(settings: Row, pr_repo: str) -> str:
    """The repo whose releases In a release reads: the requirement's `release_repo` setting, else the PR's own repo."""
    return str(settings.get("release_repo") or "").strip() or pr_repo


def _refresh_releases(store: Any, github: GitHub, now: str) -> int:
    H = _h()
    with store.read() as conn:
        waits = _release_waits(conn)
        cutoff = H.shift(now, seconds=-REREAD_S)
        stale = sorted(
            {
                w["repo"].lower()
                for w in waits
                if not conn.execute(
                    "SELECT 1 FROM release_reads WHERE repo=? AND read_at >= ?", (w["repo"], cutoff)
                ).fetchone()
            }
        )
    changed = 0
    for repo in stale:
        version = version_now()
        try:
            found = github.read_releases(repo)
        except Exception as exc:  # noqa: BLE001 - read again on the next pass
            LOG.warning("Reading the releases of %s failed: %s", repo, exc)
            continue
        with store.transaction() as conn:
            for release in found:
                changed += save_release(conn, repo, release, version)
            saw_releases(conn, repo, version)
    asked = 0
    for wait in waits:
        with store.read() as conn:
            merged = H.parse_ts(wait["at"])
            unread = [
                r
                for r in reversed(releases(conn, wait["repo"], prereleases=wait["prereleases"]))
                if included(conn, r["id"], wait["sha"]) is None
                and not (merged and (H.parse_ts(r["published_at"]) or merged) < merged)
            ]
            known = any(
                included(conn, r["id"], wait["sha"])
                for r in releases(conn, wait["repo"], prereleases=wait["prereleases"])
            )
        for release in [] if known else unread:  # oldest first; the first that holds it is the one that counts
            if asked >= REREAD_BATCH:
                return changed
            asked += 1
            try:
                inside = github.contains(wait["repo"], str(release["commit_sha"] or release["tag"]), wait["sha"])
            except Exception as exc:  # noqa: BLE001 - asked again on the next pass
                LOG.warning("Comparing %s with %s failed: %s", wait["sha"], release["tag"], exc)
                break
            with store.transaction() as conn:
                save_included(conn, release["id"], wait["sha"], inside)
                changed += 1
                if inside:
                    _nudge(conn, [wait["task"]])
            if inside:
                break
    return changed


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
            "UPDATE releases SET commit_sha=COALESCE(?, commit_sha), prerelease=?, published_at=?, fact_version=?, read_at=? WHERE id=?",
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
    """A repo's saved releases that count, newest first: a pre-release (marked so, or a dash in its tag) only when
    `prereleases` (D3)."""
    return _rows(
        conn,
        "SELECT * FROM releases WHERE repo=? AND (? OR prerelease=0 AND instr(tag, '-')=0) "
        "ORDER BY published_at DESC, id",
        (repo, 1 if prereleases else 0),
    )


def releases_read_at(conn: "sqlite3.Connection", repo: str) -> str | None:
    """When a repo's releases were last read in full, or None when never."""
    found = conn.execute("SELECT read_at FROM release_reads WHERE repo=?", (repo,)).fetchone()
    return str(found[0]) if found and found[0] else None


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
