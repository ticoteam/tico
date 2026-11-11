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
from datetime import datetime, timezone
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

    def ready(self, repo: str, number: int) -> None:
        """Mark a draft PR ready for review, as GitHub queues and merges no draft. A PR that is not a draft (or is
        gone, closed or merged) is left as it is."""
        ...

    def enqueue(self, repo: str, number: int, head_sha: str) -> Row:
        """Put the PR in the merge queue at exactly `head_sha`, or turn on auto-merge where the repo has no queue, or
        merge it where it has neither. {ok, how: queued|auto_merge|merged, reason}. A head that moved, a draft, or a
        PR the base branch's rules refuse (a required review or check) is not ok, with GitHub's reason."""
        ...

    def dequeue(self, repo: str, number: int) -> Row:
        """Take the PR out of the merge queue, or turn its auto-merge off. {ok, how: dequeued|merged|absent, reason}.
        `merged` means GitHub merged it first ("PR merged anyway")."""
        ...


def version_now() -> int:
    """A fact version for a read or webhook that begins now: the time in ms."""
    return time.time_ns() // 1_000_000


def sent_version(payload: Row, arrived: int) -> int:
    """A `pull_request` webhook's version: when GitHub last changed the PR (`pull_request.updated_at`), never later than
    it arrived. GitHub promises no order, so a late webhook carries an older state than the fact may already hold; its
    arrival time would make it newer. Without a readable time it is when it arrived."""
    stamp = str(_obj(payload.get("pull_request")).get("updated_at") or "")
    try:
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return arrived
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return min(arrived, int(at.timestamp() * 1000))


def pr_ref(repo: str, number: int) -> str:
    """How a PR is named in lines and in `requirement_pages.heads`: `owner/name#12`, the repo in lower case."""
    return f"{repo.lower()}#{int(number)}"


def _h() -> Any:
    from .. import hubdb

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
    the saved value. False, and nothing written, when the saved fact is newer. A merged PR stays merged. `checks_state`
    is worked out here from `checks`, leaving out Tico's own."""
    H = _h()
    now = H.now()
    old = pr(conn, repo, number)
    if old and int(old["fact_version"]) > version:
        return False
    merged: Row = {**(old or {}), **{k: v for k, v in fact.items() if k != "checks"}}
    if old and old.get("state") == "merged" and merged.get("state") != "merged":
        # Merged is for good. A webhook that comes late (a `dequeued` whose snapshot still says open) changes nothing
        # of it: GitHub promises no order, and its version is only when it arrived.
        merged.update({k: old.get(k) for k in ("state", "head_sha", "merged_sha", "merged_at", "queue_state")})
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
    from .. import task_engine

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
    before = pr(conn, repo, number)
    if not save_pr(conn, repo, number, fact, version):
        return 0
    after = pr(conn, repo, number)
    if not after:
        return 0
    if after["queue_state"] == "removed" and (not before or before.get("removed_at") != after["removed_at"]):
        removed(conn, after)
    tasks = linked_tasks(conn, repo, number)
    fact_changed(conn, tasks, after, before)
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
        sent = sent_version(payload, version)
        saved = pr(conn, repo, number)
        if saved and int(saved["fact_version"]) > sent:
            # Older than what is saved (a late webhook, or one beaten by a check that came first): not saved; an open
            # PR is read in full on the next pass, so what it said is never lost.
            removal = saved["state"] == "open"
        elif save_and_settle(conn, repo, number, fact, sent):
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
        numbers = [
            int(found[0])
            for found in conn.execute(
                "SELECT number FROM pr_facts WHERE repo=? AND head_sha=? AND state='open'", (repo, sha)
            ).fetchall()
        ]
        if event == "check_run":
            # A check run on a commit no fact has yet came before its `synchronize`: the PRs it names are read in full
            # on the next pass, which brings the new head and its checks.
            named: list[Any] = _obj(payload.get("check_run")).get("pull_requests") or []
            for entry in named:
                number = int(_obj(entry).get("number") or 0)
                if number and number not in numbers:
                    conn.execute(
                        "UPDATE pr_facts SET read_at='' WHERE repo=? AND number=? AND state='open' AND head_sha<>?",
                        (repo, number, sha),
                    )
        for number in numbers:
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
    An open PR marked for a full read (`read_at` '') is read once more whatever links it: a task closed while GitHub
    merged its PR anyway (Tico's dequeue said merged) so leaves no fact saying "open, queued" for ever.
    """
    cutoff = _h().shift(now, seconds=-REREAD_S)
    found = conn.execute(
        "SELECT MIN(l.repo), l.number, MIN(COALESCE(f.read_at, '')) AS seen FROM task_links l "
        f"LEFT JOIN ({_WAITING_TASKS}) w ON w.id = l.task_id "
        "LEFT JOIN pr_facts f ON f.repo = l.repo AND f.number = l.number "
        "WHERE l.kind='pr' AND l.repo IS NOT NULL AND l.repo <> '' AND l.number IS NOT NULL "
        "AND (w.id IS NOT NULL AND (f.id IS NULL OR f.state='open' AND f.read_at < ?) "
        "OR f.state='open' AND f.read_at = '') "
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


# ----------------------------------------------------------------------------- the merge queue (was backend/merge_queue.py)
# The Merge stage: Tico puts a task's PRs in GitHub's merge queue and takes them out (docs/pipelines-v3.md "GitHub",
# the Engineering pipeline's Merge stage). Track C owns this file.
#
# - Entering a stage whose `on_enter` is `{"queue_prs": true}` (`stage_effects.enter`) owes one `queue_pr` effect per open
#   linked PR at its head (`queue`). The Heartbeat's effects step (`run`) carries it out with GitHub's GraphQL
#   `enqueuePullRequest` (auto-merge where the repo has no queue). No bot runs a merge command. Effect key:
#   `queue_pr:<visit>:<ref>@<head sha>`; a second owe of the same key is nothing. A PR whose fact comes in after the task
#   entered is owed then (`fact_changed`).
# - A failed GitHub call is tried again after RETRY_S; after TRIES failed calls, or when GitHub answers no, the effect is
#   `failed`: "PR merged" stands NOT_MET on the owner with "GitHub refused the merge: <reason>".
# - GitHub removes a PR from the queue without merging it: the fact is saved (task_facts.save_pr: `queue_state` removed,
#   `removed_at`, `removed_reason`, `failing_check`, `failing_check_url`). "PR merged" stands NOT_MET on the stage owner
#   with "Dropped from the merge queue: <reason>", and the owner gets ONE wake (`removed`), keyed
#   `<task>:<visit>:queue_removed:<ref>:<removed_at>`. Tico never queues it again by itself. The third removal in one
#   visit (any PR, counted from the `pr_queue_removed` history rows) goes up the never-stuck ladder instead: the
#   pipeline's manager bot has it on its 3 tries; a removal after that goes to a person.
# - The owner (Release Manager, who may push to PR branches) then: queues it again as it is (`requeue`: CLI
#   `tico task queue <task>`, MCP `tico_task_queue`, `POST /api/v2/tasks/{tid}/queue`); or pushes its own fix commits to
#   the PR branch and then queues again; or sends the task back to Fixes 2 with the ordinary move
#   (`tico task move <task> --to "Fixes 2"`), where the Builder owns it again. A commit pushed in Merge keeps the task in
#   Merge: nothing sends it back through Review; the merge queue's checks are the gate. The manager hears of the new
#   commit once (`changed_after_ok`, C22).
# - The task is closed or sent back while a PR is queued: `pull` owes `dequeue_pr` (`dequeue_pr:<visit>:<ref>`), carried
#   out with `dequeuePullRequest` (auto-merge off). A PR GitHub merged first stays merged ("merged anyway" note to the
#   manager, keyed `<task>:<visit>:merged_anyway:<ref>`).
# - History: `pr_queued` (asked again, and GitHub accepted), `pr_dequeued`, `pr_queue_removed`.
#
# Effects use the existing `effects` table (owed, done, failed, moot) with `repo`, `number`, `head_sha` and `next_try_at`.
# Every function takes the caller's open write transaction and never commits, except `run`.

QUEUE = "queue_pr"
DEQUEUE = "dequeue_pr"
WAKE_REMOVED = "queue_removed"  # the stage owner's one wake per removal
WAKE_MERGED_ANYWAY = "merged_anyway"
WAKE_CHANGED = "changed_after_ok"
WAKE_REFUSED = "queue_refused"
DROPS = 3  # removals in one visit before the task goes up the ladder instead of back to the stage owner
RETRY_S = (60, 300, 900)  # a failed GitHub call is tried again after these; then the effect is `failed`
TRIES = len(RETRY_S)  # failed calls before the effect is `failed`
BATCH = 50  # effects carried out per pass at most


def queue_key(visit_id: str, ref: str, head_sha: str) -> str:
    return f"{QUEUE}:{visit_id}:{ref}@{head_sha}"


def dequeue_key(visit_id: str, ref: str) -> str:
    return f"{DEQUEUE}:{visit_id}:{ref}"


def removed_wake_key(task_id: str, visit_id: str, ref: str, removed_at: str) -> str:
    return f"{task_id}:{visit_id}:{WAKE_REMOVED}:{ref}:{removed_at}"


def _engine() -> Any:
    """task_engine, untyped here so its private helpers (`_refuse`, `_event`, `_tell_manager`) can be called."""
    from .. import task_engine

    return task_engine


def queues_prs(stage: Row | None) -> bool:
    """Whether a stage's on-enter action is "queue the PRs to merge"."""
    action: Any = (stage or {}).get("on_enter")
    if isinstance(action, str):
        try:
            action = json.loads(action)
        except ValueError:
            return False
    return isinstance(action, dict) and cast(Row, action).get("queue_prs") is True


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def effects(conn: "sqlite3.Connection", visit_id: str, ref: str | None = None, kind: str = QUEUE) -> list[Row]:
    """A visit's effects of one kind, oldest first; for one PR when `ref` is given."""
    found = _rows(
        conn, "SELECT * FROM effects WHERE visit_id=? AND kind=? ORDER BY created_at, rowid", (visit_id, kind)
    )
    if ref is None:
        return found
    return [e for e in found if e["repo"] and pr_ref(str(e["repo"]), int(e["number"])) == ref]


def newest(conn: "sqlite3.Connection", visit_id: str, ref: str) -> Row | None:
    """The newest `queue_pr` effect of a visit for one PR that is not moot (what "PR merged" reads), or None."""
    found = [e for e in effects(conn, visit_id, ref) if e["state"] != "moot"]
    found.sort(key=lambda e: (str(e["updated_at"]), str(e["created_at"])))
    return found[-1] if found else None


def _owe(
    conn: "sqlite3.Connection", visit_id: str, kind: str, key: str, repo: str, number: int, head: str | None
) -> Row | None:
    """Owe one effect, once per key: the row written, or None when that key was owed before."""
    H = _h()
    ts = H.now()
    effect_id = H.new_id()
    written = conn.execute(
        "INSERT OR IGNORE INTO effects (id, visit_id, kind, key, state, tries, created_at, updated_at, repo, number, "
        "head_sha, next_try_at) VALUES (?, ?, ?, ?, 'owed', 0, ?, ?, ?, ?, ?, NULL)",
        (effect_id, visit_id, kind, key, ts, ts, repo, int(number), head),
    ).rowcount
    if not written:
        return None
    return _rows(conn, "SELECT * FROM effects WHERE id=?", (effect_id,))[0]


def _owe_queue(conn: "sqlite3.Connection", visit_id: str, link: Row) -> Row | None:
    fact = link["fact"]
    if not fact or fact["state"] != "open" or not fact.get("head_sha"):
        return None
    repo, number, head = str(link["repo"]), int(link["number"]), str(fact["head_sha"])
    return _owe(conn, visit_id, QUEUE, queue_key(visit_id, pr_ref(repo, number), head), repo, number, head)


def queue(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe a `queue_pr` effect for each open linked PR of the task at its saved head (`stage_effects.enter`). Returns
    the effects owed now. A PR whose fact is not read yet is owed when it comes in (`fact_changed`)."""
    visit = task.get("visit")
    if not visit or not queues_prs(task.get("stage")):
        return []
    owed = [_owe_queue(conn, str(visit["id"]), link) for link in prs_of(conn, str(task["id"]))]
    return [e for e in owed if e]


def failing_names(fact: Row) -> str:
    """The names of the checks that failed on a PR's newest commit, Tico's own left out."""
    checks: list[Row] = fact.get("checks") or []
    names = [str(c.get("name")) for c in checks if c.get("state") == "failure" and not c.get("tico")]
    return ", ".join(names) or "a check"


def requeue(conn: "sqlite3.Connection", task_id: str, by: str, *, seen_version: int | None = None) -> list[Row]:
    """Queue again: the stage owner's action on the Merge stage after GitHub removed a PR (or refused to queue it).
    Owes a new `queue_pr` for each such PR at its newest saved head: the old effect is moot, and one for that same head
    is owed again. Refuses `task_stage` (not on a stage whose on_enter queues PRs), `task_requirement` when no PR was
    removed or refused, while the PR's own checks on its newest commit are running ("The checks on <ref> are still
    running") or failing ("Checks failed on <ref>: <names>"), and when the caller may not; `task_changed` (a bot's
    stale `seen_version`), `task_ended`. Who may: the stage owner, the pipeline's manager, any person."""
    E = _engine()
    row: Row | None = E.task(conn, task_id)
    if not row:
        E._refuse(conn, by, "not-found", "No such task")
        return []

    def refuse(rule: str, detail: str) -> None:
        E._refuse(conn, by, rule, detail, task_id, row=row, then=("show",), version=row.get("version"))

    if row["status"] != "open":
        refuse("task_ended", f"The task is {row['stage']['name']}. Reopen it first.")
    E.check_version(conn, by, row, seen_version)
    pipeline: Row = row.get("pipeline") or {}
    manager = pipeline.get("manager_id")
    if not (E.is_human(by) or by in (row.get("owner_id"), manager)):
        refuse("task_requirement", "Only the stage owner, the pipeline's manager or a person can queue it again")
    if not queues_prs(row.get("stage")):
        refuse("task_stage", f"{row['stage']['name']} doesn't queue PRs")
    visit_id = str(row["visit"]["id"])
    again: list[Row] = []
    for link in prs_of(conn, task_id):
        fact = link["fact"]
        if not fact or fact["state"] != "open":
            continue
        ref = pr_ref(str(link["repo"]), int(link["number"]))
        last = newest(conn, visit_id, ref)
        if last and last["state"] == "owed":
            continue  # already queuing again
        if fact["queue_state"] == "removed" or (last and last["state"] == "failed"):
            again.append({**link, "ref": ref})
    if not again:
        refuse("task_requirement", "Nothing was taken out of the merge queue")
    for link in again:
        fact, ref = link["fact"], link["ref"]
        if fact["checks_state"] == "pending":
            refuse("task_requirement", f"The checks on {ref} are still running")
        if fact["checks_state"] == "failure":
            refuse("task_requirement", f"Checks failed on {ref}: {failing_names(fact)}")
    ts = _h().now()
    owed: list[Row] = []
    for link in again:
        ref, head = link["ref"], str(link["fact"]["head_sha"])
        key = queue_key(visit_id, ref, head)
        conn.execute(
            "UPDATE effects SET state='moot', updated_at=? WHERE visit_id=? AND kind=? AND key<>? "
            "AND lower(repo)=lower(?) AND number=? AND state IN ('owed', 'done', 'failed')",
            (ts, visit_id, QUEUE, key, link["repo"], int(link["number"])),
        )
        conn.execute(
            "UPDATE effects SET state='owed', tries=0, last_error=NULL, next_try_at=NULL, updated_at=? WHERE key=?",
            (ts, key),
        )
        _owe(conn, visit_id, QUEUE, key, str(link["repo"]), int(link["number"]), head)
        owed.append(_rows(conn, "SELECT * FROM effects WHERE key=?", (key,))[0])
        E._event(conn, task_id, by, "pr_queued", None, ref, "Queue again", {"ref": ref, "head": head, "again": True})
    conn.execute("UPDATE tasks SET version=?, updated_at=? WHERE id=?", (int(row.get("version") or 0) + 1, ts, task_id))
    E.changed(conn, task_id, by)
    return owed


def pull(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe a `dequeue_pr` for each PR a `queue_pr` of the visit put in the queue (the task closed or went back). A
    `queue_pr` still owed is moot instead: nothing was queued yet (`run` takes out one GitHub queued meanwhile).
    Idempotent."""
    visit = task.get("visit")
    if not visit:
        return []
    visit_id = str(visit["id"])
    conn.execute(
        "UPDATE effects SET state='moot', updated_at=? WHERE visit_id=? AND kind=? AND state='owed'",
        (_h().now(), visit_id, QUEUE),
    )
    owed = [_owe_dequeue(conn, visit_id, e) for e in effects(conn, visit_id) if e["state"] == "done"]
    return [e for e in owed if e]


def _owe_dequeue(conn: "sqlite3.Connection", visit_id: str, effect: Row) -> Row | None:
    repo, number = str(effect["repo"]), int(effect["number"])
    return _owe(conn, visit_id, DEQUEUE, dequeue_key(visit_id, pr_ref(repo, number)), repo, number, None)


def _on_queueing_stage(conn: "sqlite3.Connection", task_ids: list[str]) -> list[Row]:
    E = _engine()
    out: list[Row] = []
    for task_id in task_ids:
        row: Row | None = E.task(conn, task_id)
        if row and row["status"] == "open" and row.get("visit") and queues_prs(row.get("stage")):
            out.append(row)
    return out


def removed_line(fact: Row) -> str:
    """Why a PR left the merge queue, with the check that failed there when one did."""
    reason = str(fact.get("removed_reason") or "GitHub gave no reason")
    check = fact.get("failing_check")
    return f"Dropped from the merge queue: {reason} ({check})" if check else f"Dropped from the merge queue: {reason}"


def removed(conn: "sqlite3.Connection", fact: Row) -> int:
    """After a saved fact shows a PR removed from the queue: give each task on a queueing stage that links it the one
    `queue_removed` wake for this `removed_at`, and a `pr_queue_removed` history row. Returns how many wakes were
    added. Tico never queues it again by itself."""
    from .. import task_wakes

    E = _engine()
    repo, number = str(fact["repo"]), int(fact["number"])
    ref = pr_ref(repo, number)
    added = 0
    for row in _on_queueing_stage(conn, linked_tasks(conn, repo, number)):
        task_id, visit = str(row["id"]), row["visit"]
        key = removed_wake_key(task_id, str(visit["id"]), ref, str(fact["removed_at"]))
        if conn.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' "
            "AND json_valid(data) AND json_extract(data, '$.key')=?",
            (task_id, key),
        ).fetchone():
            continue
        data = {
            "key": key,
            "visit": visit["id"],
            "ref": ref,
            "reason": fact.get("removed_reason"),
            "check": fact.get("failing_check"),
            "url": fact.get("failing_check_url"),
        }
        E._event(conn, task_id, E.SYSTEM, "pr_queue_removed", None, ref, removed_line(fact), data)
        E.changed(conn, task_id, E.SYSTEM)
        owner = row.get("owner_id")
        if not E.is_bot(owner):
            continue  # a person owner has it in Needs you
        drops = drops_in_visit(conn, task_id, str(visit["id"]))
        if drops >= DROPS:
            _up_the_ladder(
                conn, task_id, drops, f"GitHub took {ref} out of the merge queue {drops} times in this stage."
            )
            continue
        link = f" {fact['failing_check_url']}" if fact.get("failing_check_url") else ""
        lead = (
            f"GitHub took {ref} out of the merge queue. {removed_line(fact)}.{link} Queue it again with "
            f"`tico task queue {task_id}`, push a fix to the PR branch and then queue it again, or move it back."
        )
        fresh: Row = E.task(conn, task_id) or row
        body = task_wakes.body(conn, fresh, lead, to=str(owner))
        if task_wakes.add(conn, fresh, str(owner), visit, body, wake_key=key):
            added += 1
    return added


def drops_in_visit(conn: "sqlite3.Connection", task_id: str, visit_id: str) -> int:
    """How many times GitHub took a PR of the task out of the merge queue during this visit, any PR: its
    `pr_queue_removed` history rows, one per removal."""
    found = conn.execute(
        "SELECT COUNT(*) FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' "
        "AND json_valid(data) AND json_extract(data, '$.visit')=?",
        (task_id, visit_id),
    ).fetchone()
    return int(found[0]) if found else 0


def _up_the_ladder(conn: "sqlite3.Connection", task_id: str, drops: int, why: str) -> None:
    """The DROPS-th removal in a visit: the never-stuck ladder's next level has the task instead of the stage owner,
    the pipeline's manager bot on its 3 tries (else a person). Any removal after that: a person, so a PR that keeps
    falling out is never queued round and round by bots."""
    NS: Any = _never_stuck()
    row: Row | None = _engine().task(conn, task_id)
    if not row or not row.get("visit"):
        return
    done = {"woken": 0, "to_manager": 0, "to_person": 0}
    manager = NS._manager(conn, row, {})
    if drops == DROPS and manager and manager != row.get("owner_id"):
        NS._to_manager(conn, row, manager, why, _h().now(), done)
    else:
        NS._to_person(conn, row, row["visit"].get("level") or "owner", done)


def _never_stuck() -> Any:
    from .. import never_stuck

    return never_stuck


def fact_changed(conn: "sqlite3.Connection", task_ids: list[str], after: Row, before: Row | None) -> None:
    """What a changed PR fact means on the Merge stage, for the open tasks that link it: a PR whose fact came in after
    the task entered is owed its `queue_pr` now (never once one was owed for it in this visit: Tico never queues a PR
    again by itself), and a new commit tells the pipeline's manager once ("Changed after OK", C22)."""
    E = _engine()
    repo, number = str(after["repo"]), int(after["number"])
    ref = pr_ref(repo, number)
    for row in _on_queueing_stage(conn, task_ids):
        visit_id = str(row["visit"]["id"])
        if not effects(conn, visit_id, ref):
            _owe_queue(conn, visit_id, {"repo": repo, "number": number, "fact": after})
        head = after.get("head_sha")
        if before and before.get("head_sha") and head and head != before["head_sha"] and after["state"] == "open":
            E._tell_manager(conn, row, f"Changed after OK: {ref} has a new commit", f"{WAKE_CHANGED}:{head}")


# ----------------------------------------------------------------------------- the effects step
def due(conn: "sqlite3.Connection", now: str, limit: int = BATCH) -> list[Row]:
    """Owed effects whose next try is due, oldest first, with the task each is for."""
    return _rows(
        conn,
        "SELECT e.*, v.task_id FROM effects e JOIN visits v ON v.id = e.visit_id WHERE e.state='owed' "
        "AND e.repo IS NOT NULL AND (e.next_try_at IS NULL OR e.next_try_at <= ?) "
        "ORDER BY e.created_at, e.rowid LIMIT ?",
        (now, limit),
    )


def run(store: Any, github: "GitHub", now: str | None = None) -> int:
    """The effects step on the Heartbeat: carry out owed `queue_pr` / `dequeue_pr` effects with no transaction held
    during GitHub calls, record each in a short write, and save what GitHub said as a fact. Returns how many were
    carried out (done, or failed for good)."""
    at: str = now or _h().now()
    with store.read() as conn:
        owed = due(conn, at)
    count = 0
    for effect in owed:
        repo, number = str(effect["repo"]), int(effect["number"])
        if effect["kind"] == QUEUE:
            with store.transaction() as conn:
                if _queued_already(conn, effect):
                    count += 1
                    continue
        version: int = version_now()
        try:
            if effect["kind"] == QUEUE:
                github.ready(repo, number)  # a Builder's draft reaches Merge: GitHub queues no draft
                said = github.enqueue(repo, number, str(effect["head_sha"]))
            else:
                said = github.dequeue(repo, number)
        except Exception as exc:  # noqa: BLE001 - GitHub down or refusing the call: tried again later
            with store.transaction() as conn:
                count += _failed_call(conn, effect, str(exc) or type(exc).__name__, at)
            continue
        with store.transaction() as conn:
            count += _record(conn, effect, said, version)
    return count


def _queued_already(conn: "sqlite3.Connection", effect: Row) -> bool:
    """Someone else queued the PR (or GitHub merged it) before Tico's owed `queue_pr` ran: it is done, with no call."""
    fact = pr(conn, str(effect["repo"]), int(effect["number"]))
    if not fact or (fact["queue_state"] not in QUEUED and fact["state"] != "merged"):
        return False
    return bool(
        conn.execute(
            "UPDATE effects SET state='done', last_error=NULL, next_try_at=NULL, updated_at=? WHERE id=? AND state='owed'",
            (_h().now(), effect["id"]),
        ).rowcount
    )


def _failed_call(conn: "sqlite3.Connection", effect: Row, reason: str, now: str) -> int:
    """A GitHub call that failed: tried again after RETRY_S; after TRIES failed calls the effect is `failed`."""
    H = _h()
    tries = int(effect["tries"] or 0) + 1
    if tries < TRIES:
        conn.execute(
            "UPDATE effects SET tries=?, last_error=?, next_try_at=?, updated_at=? WHERE id=? AND state='owed'",
            (tries, reason[:500], H.shift(now, seconds=RETRY_S[tries - 1]), H.now(), effect["id"]),
        )
        return 0
    return _give_up(conn, effect, tries, reason)


def _give_up(conn: "sqlite3.Connection", effect: Row, tries: int, reason: str) -> int:
    """The effect is `failed`. For a `queue_pr` "PR merged" says GitHub refused, on the stage owner, told once."""
    E = _engine()
    written = conn.execute(
        "UPDATE effects SET state='failed', tries=?, last_error=?, next_try_at=NULL, updated_at=? "
        "WHERE id=? AND state='owed'",
        (tries, reason[:500], _h().now(), effect["id"]),
    ).rowcount
    if not written:
        return 0
    if effect["kind"] != QUEUE:
        return 1
    row: Row | None = E.task(conn, str(effect["task_id"]))
    visit: Row = (row.get("visit") or {}) if row else {}
    if row and row["status"] == "open" and visit.get("id") == effect["visit_id"]:
        ref = pr_ref(str(effect["repo"]), int(effect["number"]))
        E.changed(conn, str(row["id"]), E.SYSTEM)
        E.tell_owner(
            conn,
            str(row["id"]),
            f"GitHub refused the merge of {ref}: {reason}. Queue it again with `tico task queue {row['id']}` once "
            "it can merge, or move it back.",
            f"{WAKE_REFUSED}:{ref}:{effect['head_sha']}",
        )
    return 1


def _record(conn: "sqlite3.Connection", effect: Row, said: Row, version: int) -> int:
    """Record what GitHub said to one effect, and save it as the PR's fact."""
    E = _engine()
    repo, number = str(effect["repo"]), int(effect["number"])
    ref: str = pr_ref(repo, number)
    task_id = str(effect["task_id"])
    found = conn.execute("SELECT state FROM effects WHERE id=?", (effect["id"],)).fetchone()
    state = str(found[0]) if found else "moot"
    if not said.get("ok"):
        if state != "owed":
            return 0
        return _give_up(conn, effect, int(effect["tries"] or 0) + 1, str(said.get("reason") or "GitHub said no"))
    how = str(said.get("how") or "")
    conn.execute(
        "UPDATE effects SET state=CASE WHEN state='owed' THEN 'done' ELSE state END, tries=tries+1, last_error=NULL, "
        "next_try_at=NULL, updated_at=? WHERE id=?",
        (_h().now(), effect["id"]),
    )
    if effect["kind"] == QUEUE:
        if how in QUEUED:
            save_and_settle(conn, repo, number, {"queue_state": how}, version)
            data = {"ref": ref, "head": effect["head_sha"], "how": how}
            E._event(conn, task_id, E.SYSTEM, "pr_queued", None, ref, None, data)
            if state == "moot":  # the task left the stage while GitHub was queuing it: take it out again
                _owe_dequeue(conn, str(effect["visit_id"]), effect)
        else:  # merged at once, where the repo has neither a queue nor auto-merge: read in full on the next pass
            conn.execute("UPDATE pr_facts SET read_at='' WHERE repo=? AND number=?", (repo, number))
        return 1
    if how == "merged":
        conn.execute("UPDATE pr_facts SET read_at='' WHERE repo=? AND number=?", (repo, number))
        _merged_anyway(conn, task_id, str(effect["visit_id"]), ref)
    elif how == "dequeued":
        save_and_settle(conn, repo, number, {"queue_state": "none"}, version)
        E._event(conn, task_id, E.SYSTEM, "pr_dequeued", ref, None, None, {"ref": ref})
    return 1


def _merged_anyway(conn: "sqlite3.Connection", task_id: str, visit_id: str, ref: str) -> None:
    """GitHub merged a PR before Tico could take it out of the queue: it stays merged. The pipeline's manager bot hears
    of it once, keyed on the visit that queued it."""
    from .. import task_wakes

    E = _engine()
    row: Row | None = E.task(conn, task_id)
    if not row:
        return
    pipeline: Row = row.get("pipeline") or {}
    manager = pipeline.get("manager_id")
    visit = _rows(conn, "SELECT * FROM visits WHERE id=?", (visit_id,))
    if not E.is_bot(manager) or not visit:
        return
    task_wakes.add(
        conn,
        row,
        str(manager),
        visit[0],
        f"{row.get('title') or 'A task'}: {ref} merged anyway. GitHub merged it before Tico could take it out of the "
        "merge queue.",
        wake_key=f"{task_id}:{visit_id}:{WAKE_MERGED_ANYWAY}:{ref}",
        quiet=True,
    )


# ----------------------------------------------------------------------------- the four GitHub requirement kinds
# (were in backend/task_requirements.py)
from ..task_requirements import GITHUB, MET, NOT_MET, WAITING, Standing


# The four GitHub kinds read saved facts only (backend/item_sources/github.py) and, for "PR merged", the Merge stage's effects
# (backend/item_sources/github.py). With many PRs, the first one not met, in link order, gives the line. A PR closed without
# merging is left out while another is linked; when every one is, the owner is told. A wait on GitHub ends: when GitHub
# has said nothing about the PR (or the releases it waits for) for 2 hours, it is the owner's turn (`_silent`).
def _now() -> Any:
    from .. import hubdb

    return hubdb


def _prs(conn: "sqlite3.Connection", task: Row, requirement: Row) -> tuple[list[Row], Standing | None]:
    """The PRs a GitHub kind reads, each with `ref` and `fact`, or the standing when there are none to read."""
    links: list[Row] = [
        {**link, "ref": pr_ref(str(link["repo"]), int(link["number"]))} for link in prs_of(conn, task["id"])
    ]
    if not links:
        return [], Standing(requirement, NOT_MET, task.get("owner_id"), "No PR linked")
    live = [link for link in links if not (link["fact"] and link["fact"]["state"] == "closed")]
    if not live:
        return [], Standing(requirement, NOT_MET, task.get("owner_id"), f"{links[0]['ref']} was closed without merging")
    return live, None


def _first(requirement: Row, found: list[Standing]) -> Standing:
    """The first unmet standing of many PRs', else the first met one."""
    return next((s for s in found if not s.met), found[0])


def _reading(requirement: Row) -> Standing:
    return Standing(requirement, WAITING, GITHUB, "Reading the PR from GitHub")


def _seconds_since(ts: object) -> float | None:
    H = _now()
    at, now = H.parse_ts(ts), H.parse_ts(H.now())
    return None if at is None or now is None else (now - at).total_seconds()


def _silent(task: Row, requirement: Row, link: Row, found: Standing, heard: object = None) -> Standing:
    """A wait on GitHub about one PR becomes the owner's turn once GitHub has said nothing for SILENT_S: no webhook and
    no read of the PR got through (GitHub down or refusing, or the PR gone). `heard` is when GitHub last answered, when
    the caller knows better than the PR's fact (its releases' read); else the fact's last read, else when it was
    linked. Every wait on GitHub so ends in a bot's turn, whatever the stage's expected time."""
    if found.state != WAITING or found.on != GITHUB:
        return found
    fact: Row = link.get("fact") or {}
    quiet = _seconds_since(heard or fact.get("read_at") or fact.get("updated_at") or link.get("created_at"))
    if quiet is None or quiet < SILENT_S:
        return found
    return Standing(
        requirement, NOT_MET, task.get("owner_id"), f"GitHub hasn't answered about {link['ref']} for 2 hours"
    )


def pr_open(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(requirement, [_open_one(task, requirement, link) for link in prs])


def _open_one(task: Row, requirement: Row, link: Row) -> Standing:
    """Met once GitHub said the PR exists. An open PR is re-read every few minutes; one GitHub stopped answering about
    (a deleted PR reads as gone, which saves nothing) is the owner's after the 2-hour silence rule, not met for ever."""
    quiet = _silent(task, requirement, link, _reading(requirement))
    fact: Row | None = link["fact"]
    if not fact or (fact["state"] == "open" and quiet.state == NOT_MET):
        return quiet
    return Standing(requirement, MET, line="PR open")


def _checks_one(task: Row, requirement: Row, link: Row) -> Standing:

    fact, ref = link["fact"], link["ref"]
    if not fact:
        return _reading(requirement)
    if fact["state"] == "merged":
        return Standing(requirement, MET, line="Checks pass")
    state = fact["checks_state"]
    if state == "failure":
        return Standing(requirement, NOT_MET, task.get("owner_id"), f"Checks failed on {ref}: {failing_names(fact)}")
    if state == "pending":
        running = _seconds_since(fact.get("checks_started_at"))
        if running is not None and running >= CHECKS_TOO_LONG_S:
            return Standing(requirement, NOT_MET, task.get("owner_id"), "Checks still running after 2 hours")
        return Standing(requirement, WAITING, GITHUB, "Checks running")
    if state == "none":
        seen = _seconds_since(fact.get("head_seen_at"))
        if seen is None or seen < NO_CHECKS_AFTER_S:
            return Standing(requirement, WAITING, GITHUB, "Waiting for checks")
        return Standing(requirement, MET, line="No checks on this repo")  # C13
    return Standing(requirement, MET, line="Checks pass")


def checks_pass(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    found = [_silent(task, requirement, link, _checks_one(task, requirement, link)) for link in prs]
    if all(s.met for s in found):
        return Standing(requirement, MET, line=next((s.line for s in found if s.line == "Checks pass"), found[0].line))
    return _first(requirement, found)


def _merged_one(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:
    fact, ref = link["fact"], link["ref"]
    if not fact:
        return _reading(requirement)
    if fact["state"] == "merged":
        return Standing(requirement, MET, line="Merged")
    owner = task.get("owner_id")
    queues = queues_prs(task.get("stage"))
    effect = newest(conn, task["visit"]["id"], ref) if queues else None
    if fact["queue_state"] in ("queued", "auto_merge"):  # queued, by Tico or anyone: an owed queue_pr is then done
        return Standing(requirement, WAITING, GITHUB, "In the merge queue")
    if effect and effect["state"] == "owed":
        return Standing(requirement, WAITING, GITHUB, "Queuing the PR")
    if effect and effect["state"] == "failed":
        return Standing(requirement, NOT_MET, owner, f"GitHub refused the merge: {effect.get('last_error') or ref}")
    if fact["queue_state"] == "removed":
        return Standing(requirement, NOT_MET, owner, removed_line(fact))
    if effect:
        return Standing(requirement, WAITING, GITHUB, "In the merge queue")
    if queues:
        return Standing(requirement, WAITING, GITHUB, "Queuing the PR")  # owed as soon as its head is known
    return Standing(requirement, NOT_MET, owner, "Not merged yet")


def pr_merged(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(
        requirement, [_silent(task, requirement, link, _merged_one(conn, task, requirement, link)) for link in prs]
    )


def _release_one(conn: "sqlite3.Connection", requirement: Row, link: Row) -> Standing:
    fact = link["fact"]
    if not fact:
        return _reading(requirement)
    if fact["state"] != "merged" or not fact.get("merged_sha"):
        return Standing(requirement, WAITING, GITHUB, "Not merged yet")
    settings: Row = requirement.get("settings") or {}
    repo = release_repo(settings, str(link["repo"]))
    publishes = publishes_releases(conn, repo)
    if publishes is None:
        return Standing(requirement, WAITING, GITHUB, "Reading releases")
    if not publishes:
        return Standing(requirement, MET, line="This repo doesn't publish releases")  # C31
    for release in reversed(releases(conn, repo, prereleases=bool(settings.get("prereleases")))):
        if included(conn, release["id"], str(fact["merged_sha"])):
            return Standing(requirement, MET, line=f"In {release['tag']}")
    return Standing(requirement, WAITING, GITHUB, f"Waiting for a release of {repo}")


def in_release(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(requirement, [_release_silent(conn, task, requirement, link) for link in prs])


def _release_silent(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:
    """In a release for one PR, with `_silent`: a merged PR is not read again, so once it merged GitHub's word is the
    read of the release repo's releases (never read yet: since it merged)."""
    found = _release_one(conn, requirement, link)
    fact: Row = link.get("fact") or {}
    if fact.get("state") != "merged":
        return _silent(task, requirement, link, found)
    repo = release_repo(requirement.get("settings") or {}, str(link["repo"]))
    return _silent(task, requirement, link, found, releases_read_at(conn, repo) or fact.get("updated_at"))
