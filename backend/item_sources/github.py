# pyright: strict
"""PR and release metadata: Tico's saved copies of GitHub (docs/pipelines-v3.md "Metadata", "GitHub").

Requirements read metadata only (the conditions `pr_open`, `checks_pass`, `pr_merged`, `in_release` at the end of this
file, `at_head` pages in task_pages). Nothing here calls GitHub while a task is moved or checked. Two ways metadata
comes in: a webhook (backend/github.py hands it to `save_pr` / `save_release`) and the 5-minute re-read (`refresh`),
through `GitHub`.

- One `item_metadata` row per PR (source github, item type pr, key `owner/name#number`). A task reaches its PRs through
  its `task_links` rows for those items.
- Newest wins. `metadata_version` is when the read or webhook that saw the metadata began, in ms (`version_now`). A save
  with an older version than the row's changes nothing, so a slow read can't undo a webhook that came after it began.
- `head_seen_at` is when Tico first saw the PR's newest commit, `checks_started_at` when its checks were first seen
  running there. C13's 10 minutes and the 2 hours of running checks are measured from them.
- `queued_at` is when Tico first saw the PR queued; `removed_at` when it first saw GitHub take it out without merging.
  Each removal is one `removed_at`, and the Merge stage owner's one wake is keyed on it (merge_queue, track C).
- A repeated webhook is dropped by its delivery id (`seen_delivery`).

Every function takes the caller's open write transaction and never commits, except `refresh`, which opens its own
short ones around reads made with no transaction held.
"""

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, cast

from .. import item_metadata as IM
from .. import merge_order as MO
from .. import source_actions as SA
from . import github_release as GR

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

PR_STATES = ("open", "closed", "merged")
# skipped: every check that counts was skipped. cancelled: the newest run of a check was cancelled (or went stale) with
# no run after it, and nothing failed: it is re-run, there is no code to fix.
CHECK_STATES = ("none", "pending", "success", "skipped", "cancelled", "failure")
QUEUE_STATES = ("none", "queued", "auto_merge", "removed")
QUEUED = ("queued", "auto_merge")  # in GitHub's merge queue, or auto-merge on where the repo has no queue
NO_CHECKS_AFTER_S = 600  # C13: no checks for a commit 10 minutes after Tico saw it, and Checks pass is met
CHECKS_TOO_LONG_S = 7200  # checks still running after 2 hours: the owner's turn
SILENT_S = 7200  # a wait on GitHub with no word from GitHub for 2 hours (down, refusing, no such PR): the owner's turn
REREAD_S = 300  # a PR a task waits on is read again when its metadata is older than this
REREAD_BATCH = 50  # at most this many PRs per re-read call
DELIVERY_DAYS = 7  # how long a webhook delivery id is kept for de-dup
LOG = logging.getLogger("tico.github_source")


class GitHub(Protocol):
    """The calls to GitHub that metadata and the merge queue make. The server's is backend/github.py's app client; tests
    and the fault rig use backend/tests/helpers/fake_github.py `FakeGitHub`. Each raises on a failed call, with GitHub's
    reason as the message."""

    def read_pr(self, repo: str, number: int) -> Row | None:
        """{state: open|closed|merged, draft, head_sha, merged_sha, merged_at, queue_state, removed_reason,
        failing_check, failing_check_url}, or None when the PR does not exist (deleted, or no access). `queue_state` is
        `removed` when GitHub took it out of the queue (or turned its auto-merge off) without merging; `removed_reason`
        is GitHub's reason and `failing_check` / `failing_check_url` the check that failed in the queue, when one did."""
        ...

    def read_checks(self, repo: str, sha: str) -> list[Row]:
        """The check runs and statuses on a commit: [{name, app, state: pending|success|skipped|failure, url, tico,
        suite, id}], one per check (`latest_checks`: a re-run's newest run only). `tico` is true for Tico's own check
        runs, which Checks pass ignores. `suite` is a run's check suite id and `id` its run (or status) id."""
        ...

    def read_releases(self, repo: str) -> list[Row]:
        """The repo's published releases, never a draft: [{tag, commit_sha, prerelease, published_at}], newest first."""
        ...

    def contains(self, repo: str, release_sha: str, commit_sha: str) -> bool:
        """Whether `commit_sha` is in the history of `release_sha` (GitHub's compare: behind or identical)."""
        ...

    def pr_diff(self, repo: str, number: int, sha: str) -> str | None:
        """What the PR changes at commit `sha`, as one id (`diff_id`): the diff from where `sha` branched off the PR's
        base branch to `sha` (GitHub's compare, `<base>...<sha>`). None when GitHub cannot give the whole diff."""
        ...

    def ready(self, repo: str, number: int) -> None:
        """Mark a draft PR ready for review, as GitHub queues and merges no draft. A PR that is not a draft (or is
        gone, closed or merged) is left as it is."""
        ...

    def enqueue(self, repo: str, number: int, head_sha: str) -> Row:
        """Put the PR in the merge queue at exactly `head_sha`, or turn on auto-merge where the repo has no queue, or
        merge it where it has neither. {ok, how: queued|auto_merge|merged, reason}. A head that moved, a draft, or a
        PR the base branch's rules refuse (a required review or check) is not ok, with GitHub's reason; one that
        conflicts with its base branch is not ok with `CONFLICTS`. Raises `MERGEABLE_UNKNOWN` while GitHub has not
        worked out whether it merges cleanly."""
        ...

    def dequeue(self, repo: str, number: int) -> Row:
        """Take the PR out of the merge queue, or turn its auto-merge off. {ok, how: dequeued|merged|absent, reason}.
        `merged` means GitHub merged it first ("PR merged anyway")."""
        ...

    def post_check(self, repo: str, sha: str, ok: bool | None, summary: str) -> bool:
        """Post Tico's `tico/merge-ok` check run on a commit: success (True), failure (False) or neutral (None: a merge
        group of PRs no Tico task links, `answer_merge_group`), with a one-line summary. Only the server posts it, with
        its own Checks: write token that no bot's token carries. False when the App has no Checks: write (nothing
        posted); raises when GitHub could not be asked or refused."""
        ...

    def merge_group_prs(self, repo: str, base_ref: str, head_sha: str) -> list[Row]:
        """The PRs in the merge queue's group whose merge commit is `head_sha`: [{number, head_sha}], the group's own
        entry and every one ahead of it; [] when GitHub names no such group."""
        ...

    def cut_release(self, repo: str, kind: str, name: str, sha: str) -> Row:
        """Ask for a release the way the repository makes one (backend/item_sources/github_release.py): `workflow`
        runs the workflow file `name` on the default branch (`workflow_dispatch`); `tag` creates the tag `name` at
        `sha`. {ok, how, reason}. Not ok, without raising, when GitHub refuses or Tico's App lacks the permission
        (the reason then starts with github_release.MISSING)."""
        ...


_last_version = [0]
_version_lock = threading.Lock()


def version_now() -> int:
    """Metadata version for a read or webhook that begins now: the time in ms, never the same twice in this process.
    Two of Tico's own reads are ordered by when they began; only GitHub's same-second stamps tie (item_metadata.save)."""
    with _version_lock:
        _last_version[0] = max(time.time_ns() // 1_000_000, _last_version[0] + 1)
        return _last_version[0]


def sent_version(payload: Row, arrived: int) -> int:
    """A `pull_request` webhook's version: when GitHub last changed the PR (`pull_request.updated_at`), never later than
    it arrived. GitHub promises no order, so a late webhook carries an older state than the metadata may already hold;
    its arrival time would make it newer. Without a readable time it is when it arrived."""
    stamp = str(_obj(payload.get("pull_request")).get("updated_at") or "")
    try:
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return arrived
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return min(arrived, int(at.timestamp() * 1000))


SOURCE, PR = "github", "pr"  # the source id and its one item type


def pr_key(repo: str, number: int) -> str:
    """A PR's item key: `owner/name#12`, the repo as it was given (keys match whatever the case)."""
    return f"{repo}#{int(number)}"


PR_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$", re.IGNORECASE)


def match_link(url: str) -> tuple[str, str] | None:
    """(item type, item key) of a GitHub link: a PR's URL is the PR."""
    found = PR_URL.match(url)
    return (PR, pr_key(f"{found.group(1)}/{found.group(2)}", int(found.group(3)))) if found else None


def split_key(key: str) -> tuple[str, int]:
    """(repo, number) of a PR's item key."""
    repo, _, number = key.rpartition("#")
    return repo, int(number)


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
    """The saved metadata of one PR, or None when Tico has none yet: its fields (`save_pr`'s keys) with `repo`,
    `number` and the item's `id`, `metadata_version`, `read_at`, `due_at`, `created_at` and `updated_at`."""
    return _flat(IM.get(conn, SOURCE, PR, pr_key(repo, number)))


def _flat(found: Row | None) -> Row | None:
    if not found:
        return None
    repo, number = split_key(str(found["item_key"]))
    envelope = ("id", "metadata_version", "read_at", "due_at", "created_at", "updated_at")
    return {**found["fields"], "repo": repo, "number": number, **{key: found[key] for key in envelope}}


def save_pr(conn: "sqlite3.Connection", repo: str, number: int, metadata: Row, version: int) -> bool:
    """Save what GitHub said of a PR, as seen by a read or webhook that began at `version`. `metadata` has the keys
    `GitHub.read_pr` gives, and `checks` (`GitHub.read_checks` at its head) when they were read; a key left out keeps
    the saved value. False, and nothing written, when the saved metadata is newer. A merged PR stays merged.
    `checks_state` is worked out here from `checks`, leaving out Tico's own."""
    H = _h()
    now = H.now()
    old = pr(conn, repo, number)
    if old and int(old["metadata_version"]) > version:
        return False
    merged: Row = {**(old or {}), **{k: v for k, v in metadata.items() if k != "checks"}}
    if old and old.get("state") == "merged" and merged.get("state") != "merged":
        # Merged is for good. A webhook that comes late (a `dequeued` whose snapshot still says open) changes nothing
        # of it: GitHub promises no order, and its version is only when it arrived.
        merged.update({k: old.get(k) for k in ("state", "head_sha", "merged_sha", "merged_at", "queue_state")})
    head = merged.get("head_sha")
    moved = not old or old.get("head_sha") != head
    checks: list[Row] = (
        metadata["checks"] if "checks" in metadata else ([] if moved else list((old or {}).get("checks") or []))
    )
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
        "checks": checks,
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
    }
    key = str(old["repo"]) if old else repo
    # Only a merge is for good: a closed PR can be reopened, so it stays read again while a task waits on it.
    return IM.save(conn, SOURCE, PR, pr_key(key, number), values, version, final=values["state"] == "merged")


def _checks_state(checks: list[Row]) -> str:
    """failure if any of the checks that count failed, else pending if any runs, else cancelled if one's newest run was
    cancelled (`latest_checks` already left out a cancel a newer run superseded), else success if at least one passed;
    skipped when every one was skipped (or neutral): nothing ran and passed, so that is not passing. none when there
    are none. Tico's own check runs (`tico`) never count."""
    states = [str(check.get("state") or "") for check in checks if not check.get("tico")]
    if not states:
        return "none"
    if "failure" in states:
        return "failure"
    if "pending" in states:
        return "pending"
    if "cancelled" in states:
        return "cancelled"
    if "success" in states:
        return "success"
    return "skipped"


def check_key(check: Row) -> str:
    """Which check a check run or commit status is. A status is its context. A check run is its name inside its check
    suite (GitHub makes one suite per workflow run, or per App run, at a commit), or inside its App when the suite is
    not known. A re-run keeps its suite and name, so it is the same check; two suites with one name (two workflows, or
    two Apps, both naming a run `checks`) are two checks, and each counts."""
    name = str(check.get("name") or "Checks")
    if check.get("app") == "status":
        return "status\0" + name
    suite = check.get("suite")
    return ("suite\0" + str(suite) if suite else "app\0" + str(check.get("app") or "")) + "\0" + name


def latest_checks(checks: list[Row]) -> list[Row]:
    """One row per check (`check_key`), the newest run of each by its GitHub id (ids only grow); a row with an equal or
    unknown id replaces the one before it. First-seen order is kept. A cancelled run is left out when a newer run of
    the same name and App exists in another suite: a concurrency group cancels the older duplicate of a workflow run,
    and the newer one is the check (Nov 12 stress run, I38). Only a cancel is superseded so: a red run in one suite is
    never hidden behind another suite's newer run."""
    out: dict[str, Row] = {}
    for check in checks:
        key = check_key(check)
        old = out.get(key)
        if old is None or int(check.get("id") or 0) >= int(old.get("id") or 0):
            out[key] = check
    newest: dict[tuple[str, str], int] = {}
    for check in out.values():
        named = (str(check.get("app") or ""), str(check.get("name") or "Checks"))
        newest[named] = max(newest.get(named, 0), int(check.get("id") or 0))
    return [
        check
        for check in out.values()
        if check.get("state") != "cancelled"
        or int(check.get("id") or 0) >= newest[(str(check.get("app") or ""), str(check.get("name") or "Checks"))]
    ]


def run_check(run: Row, app_slug: str | None) -> Row:
    """A check run as GitHub's API or a `check_run` webhook gives it, as one row of metadata `checks`."""
    slug = str(_obj(run.get("app")).get("slug") or "")
    return {
        "name": str(run.get("name") or "Checks")[:200],
        "app": slug,
        "state": check_state(run.get("status"), run.get("conclusion")),
        "url": run.get("html_url") or run.get("details_url") or "",
        "tico": bool(app_slug) and slug == app_slug,
        "suite": _obj(run.get("check_suite")).get("id"),
        "id": run.get("id"),
    }


def status_check(status: Row) -> Row:
    """A commit status (from the API or a `status` webhook) as one row of metadata `checks`."""
    return {
        "name": str(status.get("context") or "Checks")[:200],
        "app": "status",
        "state": check_state(status.get("state"), status.get("state")),
        "url": status.get("target_url") or "",
        "tico": False,
        "suite": None,
        "id": status.get("id"),
    }


def prs_of(conn: "sqlite3.Connection", task_id: str) -> list[Row]:
    """The PRs linked to a task, oldest link first: [{repo, number, url, created_at, metadata}] where `metadata` is
    `pr()` or None."""
    out: list[Row] = []
    for link in IM.of_task(conn, task_id, SOURCE, PR):
        repo, number = split_key(str(link["item_key"]))
        out.append(
            {
                "repo": repo,
                "number": number,
                "url": link["url"],
                "created_at": link["created_at"],
                "metadata": _flat(link["metadata"]),
            }
        )
    return out


def heads(conn: "sqlite3.Connection", task_id: str) -> dict[str, str | None]:
    """{pr_ref: its newest commit} for every PR linked to the task that was not closed without merging. A page `at_head`
    saves this when it is attached and is for the newest commit while it still equals this. None: no metadata yet."""
    out: dict[str, str | None] = {}
    for link in prs_of(conn, task_id):
        metadata = link["metadata"]
        if metadata and metadata["state"] == "closed":
            continue
        out[pr_ref(str(link["repo"]), int(link["number"]))] = metadata["head_sha"] if metadata else None
    return out


# ----------------------------------------------------------------------------- what a commit changes (C22)
# After a person's OK, a new commit that changes the same lines as the OK'd one (a rebase onto a newer base, or a
# conflict fixed without changing the PR's own lines) keeps the OK; one that changes other lines is read by a bot first
# (task_requirements._after_ok). What each commit changes is read from GitHub once (`pr_diff`), only for the PRs of a task
# a person OK'd whose commits moved since (`_refresh_diffs`), and kept in `github_pr_diffs`.
DIFF_FILES_MAX = 300  # GitHub's compare lists at most this many files: a PR at the limit may have more
OK_HEADS_SQL = (
    "SELECT v.task_id, a.heads FROM answers a JOIN visits v ON v.id = a.visit_id JOIN tasks t ON t.id = v.task_id "
    "JOIN requirements r ON r.id = a.requirement_id WHERE t.status = 'open' AND r.kind = 'page' AND a.answer = 'ok' "
    "AND a.undone_at IS NULL AND a.heads IS NOT NULL AND a.by_id LIKE 'human:%' "
    "AND (r.who_oks = 'people_only' OR r.person_id IS NOT NULL) ORDER BY a.created_at, a.id"
)


def diff_id(files: object) -> str | None:
    """One id for what a PR changes, from GitHub's compare `files`: per file (by name) its status, old and new name, and
    its added and removed lines without whitespace, line numbers or context, as `git patch-id` reads a patch. A file
    GitHub gives no patch for (binary, or too large) counts by its contents at the commit (`sha`). None when GitHub left
    part of the diff out: DIFF_FILES_MAX files or more, or a file with neither patch nor contents."""
    if not isinstance(files, list):
        return None
    listed = cast("list[object]", files)
    if len(listed) >= DIFF_FILES_MAX:
        return None
    parts: list[str] = []
    rows = [cast("Row", f) for f in listed if isinstance(f, dict)]
    for found in sorted(rows, key=lambda f: str(f.get("filename") or "")):
        status = str(found.get("status") or "")
        patch = found.get("patch")
        if isinstance(patch, str):
            body = "\n".join("".join(line.split()) for line in patch.splitlines() if line[:1] in ("+", "-"))
        elif status == "removed":
            body = ""
        elif found.get("sha"):
            body = f"contents {found['sha']}"
        else:
            return None
        parts.append("\0".join((status, str(found.get("previous_filename") or ""), str(found.get("filename")), body)))
    return hashlib.sha256("\0\0".join(parts).encode()).hexdigest()


def save_diff(conn: "sqlite3.Connection", repo: str, sha: str, diff: str | None) -> None:
    """Keep what commit `sha` of a PR in `repo` changes (`diff_id`; None: GitHub could not give it all)."""
    conn.execute(
        "INSERT OR REPLACE INTO github_pr_diffs (repo, sha, diff_id, read_at) VALUES (?,?,?,?)",
        (repo.lower(), sha, diff, _h().now()),
    )


def _diff(conn: "sqlite3.Connection", repo: str, sha: str) -> tuple[bool, str | None]:
    """(read, diff id) of a commit: read False while Tico has not asked GitHub yet."""
    found = conn.execute("SELECT diff_id FROM github_pr_diffs WHERE repo=? AND sha=?", (repo.lower(), sha)).fetchone()
    return (True, found[0]) if found else (False, None)


def same_changes(conn: "sqlite3.Connection", before: dict[str, object], after: dict[str, object]) -> bool | None:
    """Whether the PRs at the commits `after` ({pr ref: commit}) change the same lines as at `before`: the same PRs, and
    each moved one's diff the same (`diff_id`). None while a commit's diff is not read yet (`_refresh_diffs`)."""
    if set(before) != set(after):
        return False
    unknown = False
    for ref, sha in after.items():
        if before[ref] == sha:
            continue
        repo = ref.rsplit("#", 1)[0]
        (read_a, a), (read_b, b) = _diff(conn, repo, str(before[ref])), _diff(conn, repo, str(sha))
        if read_a and read_b and (a is None or a != b):
            return False
        unknown = unknown or not (read_a and read_b)
    return None if unknown else True


def _diffs_wanted(conn: "sqlite3.Connection") -> list[tuple[str, str, int, str]]:
    """(task, repo, number, commit) whose diff is not read yet, of each open task where they differ: the commits a
    person's newest OK was given at and the newest ones; in a stage that queues PRs, the commit each was queued at and
    the newest one."""
    newest: dict[str, str] = {}
    for task_id, kept in conn.execute(OK_HEADS_SQL).fetchall():
        newest[str(task_id)] = str(kept)
    for task_id, on_enter in conn.execute(
        "SELECT t.id, s.on_enter FROM tasks t JOIN stages s ON s.id = t.stage_id WHERE t.status = 'open' "
        "AND s.on_enter IS NOT NULL"
    ).fetchall():
        if queues_prs({"on_enter": on_enter}):
            newest.setdefault(str(task_id), "{}")
    out: list[tuple[str, str, int, str]] = []
    for task_id, kept in newest.items():
        try:
            before: Row = json.loads(kept) or {}
        except ValueError:
            continue
        now = {ref: sha for ref, sha in heads(conn, task_id).items() if sha}
        pinned: dict[str, str] = {}
        visit = conn.execute("SELECT id FROM visits WHERE task_id=? AND left_at IS NULL", (task_id,)).fetchone()
        if visit and queues_now(conn, task_id):
            for ref in now:
                first = effects(conn, str(visit[0]), ref)
                if first and first[0].get("pinned"):
                    pinned[ref] = str(first[0]["pinned"])
        for ref, sha in now.items():
            older = {str(x) for x in (before.get(ref), pinned.get(ref)) if x and x != sha}
            if not older:
                continue
            repo, number = ref.rsplit("#", 1)
            for commit in sorted(older | {sha}):
                if not _diff(conn, repo, commit)[0]:
                    out.append((task_id, repo, int(number), commit))
    return out


def _refresh_diffs(store: Any, github: GitHub) -> int:
    """Read from GitHub what each wanted commit changes (`_diffs_wanted`), at most REREAD_BATCH a pass, then move on each
    task that now knows (a rebase keeps the person's OK) and wake the bot asked to read new commits. A call GitHub fails
    is asked again on the next pass. Returns how many were read."""
    from .. import task_wakes

    with store.read() as conn:
        wanted = _diffs_wanted(conn)[:REREAD_BATCH]
    read: dict[tuple[str, str], str | None] = {}
    tasks: list[str] = []
    for task_id, repo, number, sha in wanted:
        if (repo, sha) not in read:
            try:
                read[(repo, sha)] = github.pr_diff(repo, number, sha)
            except Exception as exc:  # noqa: BLE001 - asked again on the next pass
                LOG.warning("Reading what %s changes at %s failed: %s", pr_ref(repo, number), sha[:12], exc)
                continue
        tasks.append(task_id)
    if not read:
        return 0
    with store.transaction() as conn:
        for (repo, sha), diff in read.items():
            save_diff(conn, repo, sha, diff)
        moved = sorted(set(tasks))
        _nudge(conn, moved)
        for task_id in moved:
            task_wakes.review_new(conn, task_id)
    return len(read)


def linked_tasks(conn: "sqlite3.Connection", repo: str, number: int) -> list[str]:
    """The open tasks on the engine that link this PR, oldest link first."""
    return IM.tasks_of(conn, SOURCE, PR, pr_key(repo, number))


def _nudge(conn: "sqlite3.Connection", task_ids: list[str]) -> None:
    """Metadata came in: move each task on if it now has all it needs (item_metadata.nudge)."""
    IM.nudge(conn, task_ids)


def save_and_settle(conn: "sqlite3.Connection", repo: str, number: int, metadata: Row, version: int) -> int:
    """`save_pr`, then what changed metadata causes: a queue removal's one wake, the Merge stage's own reactions
    (metadata_changed, and the news that ends a bot's snooze there: finished checks, a merge), the end of a bot's
    snooze that names this PR once it merges or closes (`named_pr_done`), and a nudge of every open task that links
    the PR. 1 when the metadata was saved, else 0."""
    before = pr(conn, repo, number)
    if not save_pr(conn, repo, number, metadata, version):
        return 0
    after = pr(conn, repo, number)
    if not after:
        return 0
    if after["queue_state"] == "removed" and (not before or before.get("removed_at") != after["removed_at"]):
        removed(conn, after)
    tasks = linked_tasks(conn, repo, number)
    metadata_changed(conn, tasks, after, before)
    checks_news(conn, tasks, after, before)
    if after["state"] == "merged" and (not before or before.get("state") != "merged"):
        for row in _on_queueing_stage(conn, tasks):
            # A snoozed task does not move on: the merge ends a bot's snooze, so the nudge moves it.
            _end_bot_snooze(conn, row, f"{pr_ref(repo, number)} merged")
    if after["state"] in ("merged", "closed") and (not before or before.get("state") != after["state"]):
        named_pr_done(conn, repo, number, str(after["state"]))
    _nudge(conn, tasks)
    if after["state"] == "merged" and (not before or before.get("state") != "merged"):
        # A task that goes after one of these is queued now, or its owner told (merge_order.release).
        MO.released_on_merge(conn, tasks, _engine().SYSTEM)
    if before and before.get("head_sha") and after.get("head_sha") != before["head_sha"] and after["state"] == "open":
        review_again(conn, tasks, after)
    return 1


def review_again(conn: "sqlite3.Connection", task_ids: list[str], metadata: Row) -> int:
    """A new commit on a PR whose review page was for an older one: the page no longer counts ("New commits since the
    review"), so the stage's owner bot is woken now, once per commit, instead of by the two-minute wake for an owner
    that stopped. A bot still in its run on the task is not woken: it sees the new commit when it attaches. On a stage
    where a person OKs a page that is not a review, the bot asked to review the new commits is woken too
    (task_wakes.review_new). Returns how many wakes were added."""
    from .. import task_requirements as R
    from .. import task_wakes

    E = _engine()
    ref = pr_ref(str(metadata["repo"]), int(metadata["number"]))
    head = str(metadata.get("head_sha") or "")
    added = 0
    for task_id in task_ids:
        row: Row | None = E.task(conn, task_id)
        if not row or row["status"] != "open" or row.get("snooze") or not row.get("visit"):
            continue
        owner = row.get("owner_id")
        if not E.is_bot(owner) or task_wakes.live_run(conn, row, str(owner)):
            continue
        if not any(s.line == R.STALE_REVIEW and s.on == owner for s in R.unmet(conn, row)):
            continue
        visit = row["visit"]
        lead = f"{ref} has a new commit ({head[:12]}) since your review. Review it at that commit and attach the page again."
        body = task_wakes.body(conn, row, lead, to=str(owner))
        key = f"{task_id}:{visit['id']}:review_again:{ref}:{head}"
        if task_wakes.add(conn, row, str(owner), visit, body, wake_key=key):
            added += 1
    for task_id in task_ids:
        # Before a person OKs a page that is not a review (QA): the new commit is the last reviewer's to read.
        if task_wakes.review_new(conn, task_id):
            added += 1
    return added


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
# What `enqueue` answers for a PR GitHub says conflicts with its base branch (`mergeable` CONFLICTING, or
# `mergeStateStatus` DIRTY): it is not queued, since the queue would only take it out again.
CONFLICTS = REMOVAL_WORDS["merge_conflict"]
# What `enqueue` raises while GitHub has not worked out whether the PR merges cleanly (`mergeable` UNKNOWN): a failed
# call, so the queue action reads it again after source_actions.RETRY_S instead of queuing on a guess.
MERGEABLE_UNKNOWN = "GitHub has not worked out yet whether it merges cleanly"


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


# What failed in a merge queue run, for a removal's `failing_check`. The workflow's `checks` job (pr-checks.yml) names the
# same legs in its error line, "Failed: <job>: <step>. Fail fast then cancelled the other legs.", or says "No step failed"
# when the run was cancelled from outside (a superseded queue group, or by hand).
CHECKS_JOB = "checks"
# A leg that failed or hung (timed out) names the change. A fail-fast cancel and a startup failure (the runner's, not the
# change's) do not: they read as cancelled from outside.
LEG_FAILURES = ("failure", "timed_out")
CANCELLED_FROM_OUTSIDE = "no step failed: the run was cancelled from outside"
FAILED_LINE = "Failed: "
FAIL_FAST = ". Fail fast"


def failing_leg(jobs: list[Row], checks_line: Callable[[Row], str | None]) -> tuple[str, str | None] | None:
    """What made a merge queue run fail, as a removal names it: the legs that ended in failure (each job, with the steps
    that failed in it), never a leg a fail-fast cancel stopped. With none, the checks job's "Failed: ..." line, which
    `checks_line` reads and is called only then. Else that no step failed. Returns (name, url) with the url of the first
    failing leg, or of the checks job; None when the run's jobs are not known."""
    if not jobs:
        return None
    legs: list[str] = []
    leg_url: str | None = None
    checks: Row = {}
    for job in jobs:
        if job.get("name") == CHECKS_JOB:
            checks = job
            continue
        job_steps: list[Row] = job.get("steps") or []
        steps = [str(step.get("name") or "") for step in job_steps if step.get("conclusion") == "failure"]
        if job.get("conclusion") not in LEG_FAILURES and not steps:
            continue
        name = str(job.get("name") or "A check")
        legs.append(f"{name}: {', '.join(steps)}" if steps else name)
        leg_url = leg_url or job.get("html_url") or None
    if legs:
        return "; ".join(legs)[:200], leg_url
    checks_url = checks.get("html_url") or None
    line = checks_line(checks) or ""
    if line.startswith(FAILED_LINE):
        return line.removeprefix(FAILED_LINE).split(FAIL_FAST, 1)[0][:200], checks_url
    return CANCELLED_FROM_OUTSIDE, checks_url


def real_failure(check: object) -> bool:
    """Whether a removal's `failing_check` names a check that really failed in the queue's run. The cancelled-from-outside
    line does not, and nor does a removal whose run was not read (no name): those keep the count (`requeue`)."""
    return isinstance(check, str) and check not in ("", CANCELLED_FROM_OUTSIDE)


def check_state(status: object, conclusion: object) -> str:
    """A check run's or status's state as metadata keeps it: pending, success, skipped, cancelled or failure. A neutral
    run is skipped: it fails nothing, and it is not a check that ran and passed. A cancelled (or stale) run failed
    nothing either: GitHub cancels a duplicate run of a concurrency group, or someone did by hand. A failed or timed-out
    run is a failure."""
    if str(status or "completed") not in ("completed", "success", "failure", "error"):
        return "pending"
    result = str(conclusion or status or "")
    if result == "success":
        return "success"
    if result in ("neutral", "skipped"):
        return "skipped"
    if result in ("cancelled", "stale"):
        return "cancelled"
    if result in ("pending", "queued", "in_progress", "requested", "waiting", ""):
        return "pending"
    return "failure"


def _pr_metadata_of(payload: Row) -> tuple[Row, bool]:
    """A `pull_request` webhook as metadata, and whether it took the PR out of the merge queue (or its auto-merge off)
    for a reason other than a merge. That removal is not saved from the webhook: its failing check is not in it, so the
    PR is read in full on the next pass (`refresh`), and the one wake carries the check."""
    found: Row = payload.get("pull_request") or {}
    action = str(payload.get("action") or "")
    merged = bool(found.get("merged") or found.get("merged_at"))
    metadata: Row = {
        "state": "merged" if merged else "closed" if found.get("state") == "closed" else "open",
        "draft": bool(found.get("draft")),
        "head_sha": _obj(found.get("head")).get("sha"),
    }
    if merged:
        metadata.update(merged_sha=found.get("merge_commit_sha"), merged_at=found.get("merged_at"), queue_state="none")
    elif action == "enqueued":
        metadata["queue_state"] = "queued"
    elif action == "auto_merge_enabled":
        metadata["queue_state"] = "auto_merge"
    elif action in ("dequeued", "auto_merge_disabled"):
        if str(payload.get("reason") or "").lower() in MERGED_REASONS:
            metadata["queue_state"] = "none"
        else:
            return metadata, True
    return metadata, False


def _obj(value: Any) -> Row:
    """A JSON object from a webhook payload, or {} when it is missing or not an object."""
    return cast(Row, value) if isinstance(value, dict) else {}


def webhook(conn: "sqlite3.Connection", event: str, payload: Row, app_slug: str | None = None) -> list[str]:
    """Save what a GitHub webhook says as metadata, in the webhook's transaction (backend/github.py dropped a repeated
    delivery first: `seen_delivery`). Only PRs a task links are kept. A check run or status changes that one check in
    the metadata of each open PR whose newest commit it is on. A published release is saved. Returns the refs of the PRs
    whose metadata changed."""
    repo = str(_obj(payload.get("repository")).get("full_name") or "")
    if not repo:
        return []
    version = version_now()
    out: list[str] = []
    if event == "pull_request":
        number = int(_obj(payload.get("pull_request")).get("number") or payload.get("number") or 0)
        if not number or not IM.linked(conn, SOURCE, PR, pr_key(repo, number)):
            return []
        metadata, removal = _pr_metadata_of(payload)
        sent = sent_version(payload, version)
        saved = pr(conn, repo, number)
        if saved and int(saved["metadata_version"]) > sent:
            # Older than what is saved (a late webhook, or one beaten by a check that came first): not saved; an open
            # PR is read in full on the next pass, so what it said is never lost.
            removal = saved["state"] == "open"
        elif save_and_settle(conn, repo, number, metadata, sent):
            out.append(pr_ref(repo, number))
        if removal:
            IM.mark_due(conn, SOURCE, PR, pr_key(repo, number))
    elif event in ("check_run", "status"):
        if event == "check_run":
            run: Row = payload.get("check_run") or {}
            sha = str(run.get("head_sha") or "")
            check = run_check(run, app_slug)
        else:
            sha = str(payload.get("sha") or "")
            check = status_check(payload)
        numbers = _open_at_head(conn, repo, sha)
        if event == "check_run":
            # A check run on a commit no metadata has yet came before its `synchronize`: the PRs it names are read in
            # full on the next pass, which brings the new head and its checks.
            named: list[Any] = _obj(payload.get("check_run")).get("pull_requests") or []
            for entry in named:
                number = int(_obj(entry).get("number") or 0)
                saved = pr(conn, repo, number) if number and number not in numbers else None
                if saved and saved["state"] == "open" and saved.get("head_sha") != sha:
                    IM.mark_due(conn, SOURCE, PR, pr_key(repo, number))
        for number in numbers:
            old: list[Row] = (pr(conn, repo, number) or {}).get("checks") or []
            checks = latest_checks(old + [check])  # this one check changes; a newer run already saved stays
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


def _open_at_head(conn: "sqlite3.Connection", repo: str, sha: str) -> list[int]:
    """The numbers of a repo's open PRs whose newest commit is `sha`."""
    out: list[int] = []
    for (key,) in conn.execute(
        "SELECT item_key FROM item_metadata WHERE source=? AND json_extract(fields, '$.head_sha')=? "
        "AND final_at IS NULL AND json_extract(fields, '$.state')='open' AND item_type=? ORDER BY created_at, id",
        (SOURCE, sha, PR),
    ).fetchall():
        found_repo, number = split_key(str(key))
        if found_repo.lower() == repo.lower():
            out.append(number)
    return out


def due_reads(conn: "sqlite3.Connection", now: str, limit: int = REREAD_BATCH) -> list[tuple[str, int]]:
    """The PRs an open task waits on whose metadata is missing or older than REREAD_S, oldest read first, at most
    `limit`.
    A merged PR is final, so it is not read again; a closed one can be reopened, so it is read like an open one.
    A PR marked for a full read (`read_at` '') is read once more whatever links it: a task closed while GitHub
    merged its PR anyway (Tico's dequeue said merged) so leaves no metadata saying "open, queued" for ever.
    """
    return [split_key(key) for key in IM.due_reads(conn, SOURCE, PR, now, limit)]


def read_pr(github: GitHub, repo: str, number: int) -> Row | None:
    """What GitHub says of one PR now, with the checks at its head while it is open: metadata for `save_pr`. None when
    the PR is gone. Raises when GitHub could not be asked."""
    found = github.read_pr(repo, number)
    if found is None:
        return None
    metadata = dict(found)
    if metadata.get("state") == "open" and metadata.get("head_sha"):
        metadata["checks"] = github.read_checks(repo, str(metadata["head_sha"]))
    return metadata


def refresh(store: Any, github: GitHub, now: str | None = None) -> int:
    """The 5-minute re-read: each of `due_reads` read through `github` with no transaction held, then saved with the
    version taken before its read. Also re-reads the releases of every release repo an open task waits on, and asks
    GitHub whether each waiting merge is in each newer release. A call GitHub fails is logged and left for the next
    pass: the metadata stays as it was, so what waits on it keeps waiting on GitHub. Then reads what new commits after a
    person's OK change (`_refresh_diffs`). Returns how many PRs' metadata changed."""
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
            metadata = read_pr(github, repo, number)
        except Exception as exc:  # noqa: BLE001 - GitHub down or refusing: the metadata waits for the next pass
            LOG.warning("Reading %s from GitHub failed: %s", pr_ref(repo, number), exc)
            continue
        if metadata is None:
            continue
        with store.transaction() as conn:
            changed += save_and_settle(conn, repo, number, vanished(pr(conn, repo, number), metadata), version)
    changed += _refresh_releases(store, github, at)
    _refresh_diffs(store, github)
    return changed


def vanished(old: Row | None, metadata: Row) -> Row:
    """A re-read that finds an open PR Tico saw queued now neither queued nor removed: it left the merge queue and no
    webhook or timeline event said so. That is a removal, with no reason, so the Merge stage's owner hears of it at
    once (merge_queue.removed) instead of "In the merge queue" waiting for ever."""
    if (
        old
        and old.get("queue_state") in QUEUED
        and metadata.get("state") == "open"
        and metadata.get("queue_state") == "none"
    ):
        return {**metadata, "queue_state": "removed", "removed_reason": removal_words(None)}
    return metadata


def _release_waits(conn: "sqlite3.Connection") -> list[Row]:
    """Each merged PR an open task's In a release waits on: {task, repo (the release repo), sha, at, prereleases}."""
    out: list[Row] = []
    for task_id, settings in conn.execute(
        "SELECT t.id, r.settings FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "JOIN requirements r ON (r.stage_id = t.stage_id OR r.visit_id = v.id) AND r.withdrawn_at IS NULL "
        "AND r.kind='item' AND r.source='github' AND r.condition='in_release' "
        "WHERE t.status='open' AND t.pipeline_id IS NOT NULL"
    ).fetchall():
        try:
            setting: Row = json.loads(settings or "{}") or {}
        except ValueError:
            setting = {}
        for link in prs_of(conn, str(task_id)):
            metadata = link["metadata"]
            if metadata and metadata["state"] == "merged" and metadata.get("merged_sha"):
                out.append(
                    {
                        "task": str(task_id),
                        "repo": release_repo(setting, str(link["repo"])),
                        "sha": str(metadata["merged_sha"]),
                        "at": metadata.get("merged_at"),
                        "prereleases": setting.get("prereleases", True) is not False,
                    }
                )
    return out


def release_repo(settings: Row, pr_repo: str) -> str:
    """The repo whose releases In a release reads: the requirement's `release_repo` setting, else the PR's own repo."""
    return str(settings.get("release_repo") or "").strip() or pr_repo


def read_releases_now(store: Any, github: GitHub, task_id: str, repo: str | None = None) -> int:
    """Before Tico asks GitHub to cut a release for a task (github_release): read the releases of each repo the task
    waits on (or of `repo` alone) now, whatever their last read, and save what is new the way the re-read does. A
    release the repo already published with the merge is then found instead of cut again, and the task moves on. A
    call GitHub fails is logged and the ask goes ahead. No transaction may be held."""
    return _refresh_releases(store, github, _h().now(), task=task_id, repo=repo)


def _refresh_releases(store: Any, github: GitHub, now: str, *, task: str | None = None, repo: str | None = None) -> int:
    """The re-read of releases. With `task`, only that task's waits (in `repo` when given), read whatever their age."""
    H = _h()
    with store.read() as conn:
        waits = [
            w
            for w in _release_waits(conn)
            if (task is None or w["task"] == task) and (repo is None or w["repo"].lower() == repo.lower())
        ]
        cutoff = H.shift(now, seconds=-REREAD_S)
        stale = sorted(
            {
                w["repo"].lower()
                for w in waits
                if task is not None
                or not conn.execute(
                    "SELECT 1 FROM github_release_reads WHERE repo=? AND read_at >= ?", (w["repo"], cutoff)
                ).fetchone()
            }
        )
    changed = 0
    for name in stale:
        version = version_now()
        try:
            found = github.read_releases(name)
        except Exception as exc:  # noqa: BLE001 - read again on the next pass
            LOG.warning("Reading the releases of %s failed: %s", name, exc)
            continue
        with store.transaction() as conn:
            for release in found:
                changed += save_release(conn, name, release, version)
            saw_releases(conn, name, version)
    asked = 0
    listed: dict[tuple[str, bool], list[Row]] = {}  # each repo's releases, read once in this pass
    for wait in waits:
        with store.read() as conn:
            merged = H.parse_ts(wait["at"])
            key = (wait["repo"].lower(), bool(wait["prereleases"]))
            if key not in listed:
                listed[key] = releases(conn, wait["repo"], prereleases=wait["prereleases"])
            held = holds(conn, wait["sha"])
            unread = [
                r
                for r in reversed(listed[key])
                if str(r["id"]) not in held and not (merged and (H.parse_ts(r["published_at"]) or merged) < merged)
            ]
            known = holding(listed[key], held) is not None
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
    old = _row(conn, "SELECT id, metadata_version FROM github_releases WHERE repo=? AND tag=?", (repo, release["tag"]))
    if old and int(old["metadata_version"]) > version:
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
            "UPDATE github_releases SET commit_sha=COALESCE(?, commit_sha), prerelease=?, published_at=?, metadata_version=?, read_at=? WHERE id=?",
            (*values, old["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO github_releases (commit_sha, prerelease, published_at, metadata_version, read_at, id, repo, tag, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*values, H.new_id(), repo, release["tag"], now),
        )
    return True


def saw_releases(conn: "sqlite3.Connection", repo: str, version: int) -> None:
    """Record that a repo's releases were read in full at `version` (`release_reads`), with or without any."""
    conn.execute(
        "INSERT INTO github_release_reads (repo, read_at, metadata_version) VALUES (?,?,?) ON CONFLICT(repo) DO UPDATE "
        "SET read_at=excluded.read_at, metadata_version=excluded.metadata_version "
        "WHERE excluded.metadata_version >= metadata_version",
        (repo, _h().now(), version),
    )


def releases(conn: "sqlite3.Connection", repo: str, *, prereleases: bool = False) -> list[Row]:
    """A repo's saved releases that count, newest first: a pre-release (marked so, or a dash in its tag) only when
    `prereleases` (D3)."""
    return _rows(
        conn,
        "SELECT * FROM github_releases WHERE repo=? AND (? OR prerelease=0 AND instr(tag, '-')=0) "
        "ORDER BY published_at DESC, id",
        (repo, 1 if prereleases else 0),
    )


def releases_read_at(conn: "sqlite3.Connection", repo: str) -> str | None:
    """When a repo's releases were last read in full, or None when never."""
    found = conn.execute("SELECT read_at FROM github_release_reads WHERE repo=?", (repo,)).fetchone()
    return str(found[0]) if found and found[0] else None


def publishes_releases(conn: "sqlite3.Connection", repo: str) -> bool | None:
    """Whether a repo publishes releases: True when any release of it is saved (a pre-release too), False when its
    releases were read and there were none (C31), None when they were never read."""
    if conn.execute("SELECT 1 FROM github_releases WHERE repo=? LIMIT 1", (repo,)).fetchone():
        return True
    if conn.execute("SELECT 1 FROM github_release_reads WHERE repo=?", (repo,)).fetchone():
        return False
    return None


def included(conn: "sqlite3.Connection", release_id: str, commit_sha: str) -> bool | None:
    """Whether a commit is in one release's history, as read from GitHub; None when not read yet. One question per call:
    to find which release holds a commit, use `release_of` (or `holds` with `holding`), never this in a loop."""
    found = conn.execute(
        "SELECT included FROM github_release_commits WHERE release_id=? AND commit_sha=?", (release_id, commit_sha)
    ).fetchone()
    return None if found is None else bool(found[0])


def holds(conn: "sqlite3.Connection", commit_sha: str) -> dict[str, bool]:
    """Every saved release read for a commit, in one read: release id -> whether its history holds the commit. A release
    missing here is not read yet (`included` None)."""
    return {
        str(release_id): bool(inside)
        for release_id, inside in conn.execute(
            "SELECT release_id, included FROM github_release_commits WHERE commit_sha=?", (commit_sha,)
        )
    }


def holding(listed: list[Row], held: dict[str, bool]) -> Row | None:
    """The release that counts for a commit: the oldest of `listed` (newest first, as `releases` gives them) that
    holds it by `held` (`holds`), or None."""
    return next((release for release in reversed(listed) if held.get(str(release["id"]))), None)


def release_of(conn: "sqlite3.Connection", repo: str, commit_sha: str, *, prereleases: bool) -> Row | None:
    """The saved release of `repo` that counts as holding a commit (`holding`), in two reads however many releases."""
    return holding(releases(conn, repo, prereleases=prereleases), holds(conn, commit_sha))


def save_included(conn: "sqlite3.Connection", release_id: str, commit_sha: str, value: bool) -> None:
    """Record whether a commit is in a release's history. A release's history never changes, so it is read once."""
    conn.execute(
        "INSERT OR REPLACE INTO github_release_commits (release_id, commit_sha, included, checked_at) VALUES (?,?,?,?)",
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
# - Entering a stage whose `on_enter` is `{"act": "github.queue"}` (`stage_effects.enter`) owes one `queue` action
#   (backend/source_actions.py, `act`) per open linked PR, pinned to its head (`queue`). The Heartbeat's
#   `source_actions` step carries it out with GitHub's GraphQL `enqueuePullRequest` (auto-merge where the repo has no
#   queue). No bot runs a merge command. Key: `act:github.queue:<visit>:<ref>@<head sha>`; a second owe of the same key
#   is nothing. A PR whose metadata comes in after the task entered is owed then (`metadata_changed`).
# - A failed GitHub call is tried again after source_actions.RETRY_S; after its TRIES failed calls, or when GitHub
#   answers no, the action is `failed`: "PR merged" stands NOT_MET on the owner with "GitHub refused the merge: <reason>".
# - GitHub removes a PR from the queue without merging it: the metadata is saved (save_pr: `queue_state` removed,
#   `removed_at`, `removed_reason`, `failing_check`, `failing_check_url`). "PR merged" stands NOT_MET on the stage owner
#   with "Dropped from the merge queue: <reason>", and the owner gets ONE wake (`removed`), keyed
#   `<task>:<visit>:queue_removed:<ref>:<removed_at>`. Tico never queues it again by itself. The third removal in one
#   visit (any PR, counted from the `pr_queue_removed` history rows) goes up the never-stuck ladder instead: the
#   pipeline's manager bot has it on its 3 tries; a removal after that goes to a person. A second removal for failed
#   checks at the same head is not queued again: its wake says to send the task back to QA, and `requeue` refuses it.
# - A PR that conflicts with its base branch is never queued: `enqueue` reads GitHub's `mergeable` first and answers
#   CONFLICTS (the action fails at once; the owner is told to send it back to QA, where the Builder rebases), or
#   raises while GitHub still works it out (UNKNOWN: read again after RETRY_S). `requeue` refuses while the PR's link
#   says it conflicts, and a removal for a conflict wakes the owner with the same send-back (SENT_BACK_FOR_CONFLICT).
# - The owner (Release Owner, who may push to PR branches) then: queues it again as it is (`requeue`: CLI
#   `tico task queue <task>`, MCP `tico_task_queue`, `POST /api/v2/tasks/{tid}/queue`); or pushes its own fix commits to
#   the PR branch and then queues again; or sends the task back to QA with the ordinary move
#   (`tico task move <task> --to "QA"`), where the Builder owns it again. A commit pushed in Merge keeps the task in
#   Merge. When it changes the same lines as the commit the task came in with (a rebase, `same_changes`), the owner
#   queues it again; when it changes others, a bot sends the task back to QA, where Reviewer 2 reads the new
#   commits and the person's OK stands on its OK (`SENT_BACK_FOR_NEW`, task_requirements._after_ok). A person may queue
#   it as it is. The manager hears of the new commit once (`changed_after_ok`).
# - A task that goes after another one (backend/merge_order.py) is not queued while that one is open and has not
#   merged: `queue` and `metadata_changed` ask `merge_order.stops_queue`, `requeue` refuses a bot (`HELD_FOR`) and
#   queues for a person; `merge_order.release` queues it when the earlier task merges or moves on, and a conflict then
#   names the task to rebase onto (`SENT_BACK_AFTER`).
# - The task is closed or sent back while a PR is queued: `pull` owes the `undo`
#   (`undo:github.queue:<visit>:<ref>@<head sha>`), carried out with `dequeuePullRequest` (auto-merge off), and
#   `tico/merge-ok` on that head turns to failure. The undo is moot while an open visit (the task's next one, or
#   another task's) still wants the PR queued at that head (`undo_moot`); one that took the PR out anyway owes that
#   visit's queue again (`_wanted_again`). A PR GitHub merged first stays merged ("merged anyway" note to the
#   manager, keyed `<task>:<visit>:merged_anyway:<ref>`).
# - History: `pr_queued` (asked again, and GitHub accepted), `pr_dequeued`, `pr_queue_removed`.
# - A bot's snooze waits for GitHub, never for a clock time it guessed: it ends, as SYSTEM, and whoever has the turn is
#   woken once (`snooze_news`, keyed `<task>:<visit>:merge_news:<what>`). On any stage of a pipeline: when the checks on
#   the newest commit of the task's PR finish, the PR starts or stops conflicting (backend/github.py; before Merge a
#   conflict's wake is pr_conflicts'), or a PR the snooze's reason names ("waiting for #3417") merges or closes
#   (`named_pr_done`). On this stage also when GitHub takes the PR out of the queue (`removed`, whose own wake it is) or
#   merges it (the task then moves on), or the PR's base branch moves (`base_moved`; elsewhere every merge into main
#   would be noise). A person's snooze is never ended this way.
#
# The actions are `effects` rows (owed, done, failed, moot) of source `github`, action `queue`, item type `pr`.
# Every function takes the caller's open write transaction and never commits, except `run`.

QUEUE = "queue"  # GitHub's one stage action: put the PR in the merge queue (undo: take it out)
ACTIONS = (QUEUE,)  # what a stage's on_enter may act; a release is asked for with a tool (github_release.cut)
RELEASE = GR.RELEASE
WAKE_REMOVED = "queue_removed"  # the stage owner's one wake per removal
WAKE_MERGED_ANYWAY = "merged_anyway"
WAKE_CHANGED = "changed_after_ok"
WAKE_REFUSED = "queue_refused"
WAKE_NEWS = "merge_news"  # GitHub news that ends a bot's snooze (`snooze_news`); the name predates other stages
CHECKS_DONE = ("success", "failure", "skipped", "cancelled")
# "#3417" or "owner/repo#3417" in a snooze's reason: a PR it waits on. A bare number is in the task's own repository.
PR_NAMED = re.compile(r"(?<![\w/#])(?:([\w.-]+/[\w.-]+))?#(\d+)\b")
DROPS = 3  # removals in one visit before the task goes up the ladder instead of back to the stage owner
# Why a bot may not queue again a PR with commits after the person's OK (`requeue`).
SENT_BACK_FOR_NEW = (
    "{ref} has new changes since the person's OK. Move the task back to QA, where Reviewer 2 reads them."
)
# Why nobody queues a PR GitHub says conflicts with its base branch (`requeue`, a refused queue, a removal): the queue
# would only take it out again. The Builder rebases it in QA.
SENT_BACK_FOR_CONFLICT = (
    "{ref} conflicts with its base branch, so it is not queued: the merge queue would only take it out again. Move the "
    'task back to QA for the Builder to rebase it: `tico task move {task} "QA" -m "{ref} conflicts with its '
    'base branch: rebase it" --version N`.'
)
# GitHub's removal reasons for a check that failed in the queue's run, as `removal_words` words them. Taken out this way
# twice at one head in one visit, that head is not queued again: the task goes back to QA.
FAILED_CHECKS = frozenset(removal_words(reason) for reason in ("failed_checks", "checks_failed"))
COMPARING = "Tico is still reading what {ref}'s new commits change. Try again in a minute."
# The same send-back for a task that goes after another one (backend/merge_order.py), once that one merged: the Builder
# rebases onto it.
SENT_BACK_AFTER = (
    "{ref} conflicts with its base branch now that {earlier}, which it goes after, has merged, so it is not queued. "
    'Move the task back to QA for the Builder to rebase it onto {earlier}: `tico task move {task} "QA" -m '
    '"{ref} conflicts now that {earlier} merged: rebase it onto {earlier}" --version N`.'
)
# Why a bot may not queue a task that goes after another one still open (merge_order.held). A person may.
HELD_FOR = "Waits for {earlier} to merge. A person may queue it anyway."


def removed_wake_key(task_id: str, visit_id: str, ref: str, removed_at: str) -> str:
    return f"{task_id}:{visit_id}:{WAKE_REMOVED}:{ref}:{removed_at}"


def _engine() -> Any:
    """task_engine, untyped here so its private helpers (`_refuse`, `_event`, `_tell_manager`) can be called."""
    from .. import task_engine

    return task_engine


def queues_prs(stage: Row | None) -> bool:
    """Whether a stage's on-enter action is "queue the PRs to merge" (`{"act": "github.queue"}`)."""
    return SA.act_of(stage) == (SOURCE, QUEUE)


LINK_IN_MERGE = "Send the task back before linking another pull request."


def queues_now(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Whether the open task is in a stage whose on-enter action queues its PRs. While it is, only a person links a
    new PR to it (hubdb.task_link refuses anyone else with LINK_IN_MERGE): every PR the stage queues was signed off on
    the stages before it, or by the person who linked it."""
    found = conn.execute(
        "SELECT s.on_enter FROM tasks t JOIN stages s ON s.id = t.stage_id WHERE t.id=? AND t.status='open'",
        (task_id,),
    ).fetchone()
    return bool(found) and queues_prs({"on_enter": found[0]})


def entered(conn: "sqlite3.Connection", task: Row, by: str, action: str) -> list[Row]:
    """A stage that acts on GitHub was entered (source_actions.enter): its one action queues the task's PRs."""
    return queue(conn, task, by) if action == QUEUE else []


def effects(conn: "sqlite3.Connection", visit_id: str, ref: str | None = None) -> list[Row]:
    """A visit's `queue` actions, oldest first; for one PR when `ref` is given."""
    return SA.of_visit(conn, visit_id, SOURCE, QUEUE, item_key=ref)


def newest(conn: "sqlite3.Connection", visit_id: str, ref: str) -> Row | None:
    """The newest `queue` action of a visit for one PR that is not moot (what "PR merged" reads), or None."""
    found = [e for e in effects(conn, visit_id, ref) if e["state"] != "moot"]
    found.sort(key=lambda e: (str(e["updated_at"]), str(e["created_at"])))
    return found[-1] if found else None


def _owe_queue(conn: "sqlite3.Connection", visit_id: str, link: Row) -> Row | None:
    metadata = link["metadata"]
    if not metadata or metadata["state"] != "open" or not metadata.get("head_sha"):
        return None
    key = pr_key(str(link["repo"]), int(link["number"]))
    return SA.owe(conn, visit_id, SA.ACT, SOURCE, QUEUE, PR, key, str(metadata["head_sha"]))


def queue(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe a `queue` action for each open linked PR of the task at its saved head (`stage_effects.enter`). Returns
    the actions owed now. A PR whose metadata is not read yet is owed when it comes in (`metadata_changed`)."""
    visit = task.get("visit")
    if not visit or not queues_prs(task.get("stage")):
        return []
    if MO.stops_queue(conn, task):
        return []  # it goes after a task that has not merged (merge_order.release queues it then), or one that closed
    owed = [_owe_queue(conn, str(visit["id"]), link) for link in prs_of(conn, str(task["id"]))]
    return [e for e in owed if e]


def failing_names(metadata: Row, state: str = "failure") -> str:
    """The names of the checks that failed (or, with `state`, were cancelled) on a PR's newest commit, Tico's own left
    out, each once."""
    checks: list[Row] = metadata.get("checks") or []
    names = [str(c.get("name")) for c in checks if c.get("state") == state and not c.get("tico")]
    return ", ".join(dict.fromkeys(names)) or "a check"


def cancelled_line(metadata: Row, at: str = "") -> str:
    """What to say of a PR whose checks were cancelled, not failed: the owner re-runs them instead of fixing code."""
    where = f" at {at}" if at else ""
    return f"were cancelled{where}, not failed: {failing_names(metadata, 'cancelled')}. Re-run them; there is no code to fix"


def conflicts(conn: "sqlite3.Connection", task_id: str, repo: str, number: int) -> bool:
    """Whether GitHub last said the task's open PR conflicts with its base branch: its link's `mergeable` (kept by
    backend/github.py from webhooks and its read of PRs whose base moved; a new commit sets it back to unknown)."""
    found = conn.execute(
        "SELECT 1 FROM task_links WHERE task_id=? AND kind='pr' AND state IN ('open','draft') AND mergeable='conflict' "
        "AND lower(repo)=lower(?) AND number=?",
        (task_id, repo, int(number)),
    ).fetchone()
    return found is not None


def requeue(conn: "sqlite3.Connection", task_id: str, by: str, *, seen_version: int | None = None) -> list[Row]:
    """Queue again: the stage owner's action on the Merge stage after GitHub removed a PR (or refused to queue it).
    Owes a new `queue` for each such PR at its newest saved head: the old action is moot, and one for that same head
    is owed again. Refuses `task_stage` (not on a stage whose on_enter queues PRs), `task_requirement` when no PR was
    removed or refused, while the PR's own checks on its newest commit are running ("The checks on <ref> are still
    running") or failing ("Checks failed on <ref>: <names>"), while GitHub says it conflicts with its base branch
    (`SENT_BACK_FOR_CONFLICT`), and when the caller may not; `task_changed` (a bot's stale `seen_version`),
    `task_ended`. A head taken out for failed checks twice in this visit is refused too (send it back to QA). Who
    may: the stage owner, the pipeline's manager, any person. A bot may not queue a PR whose newest commit changes other
    lines than the one the task came into Merge with (`SENT_BACK_FOR_NEW`): the person's OK was for that one. A rebase
    that changes the same lines (`same_changes`) it may; while Tico has not read what the new commit changes yet, it is
    asked to try again (`COMPARING`). A task that goes after one that has not merged (merge_order.held) is refused to a
    bot (`HELD_FOR`) and queued for the first time for a person ("Queued before #40 merged" in its history); one whose
    earlier task closed without merging is queued for the first time for whoever may queue it."""
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
    # It goes after a task that has not merged (merge_order.held): a bot waits; a person may queue it anyway, and its
    # history says so. One whose earlier task closed without merging is its owner's to queue for the first time.
    earlier = MO.held(conn, row)
    if earlier and not E.is_human(by):
        refuse("task_requirement", HELD_FOR.format(earlier=MO.ref(earlier)))
    first = bool(earlier) or MO.closed_before(conn, row) is not None
    visit_id = str(row["visit"]["id"])
    again: list[Row] = []
    for link in prs_of(conn, task_id):
        metadata = link["metadata"]
        if not metadata or metadata["state"] != "open":
            continue
        ref = pr_ref(str(link["repo"]), int(link["number"]))
        last = newest(conn, visit_id, ref)
        if last and last["state"] == "owed":
            continue  # already queuing again
        if metadata["queue_state"] == "removed" or (last and last["state"] == "failed") or (first and not last):
            again.append({**link, "ref": ref})
    if not again:
        refuse("task_requirement", "Nothing was taken out of the merge queue")
    for link in again:
        metadata, ref = link["metadata"], link["ref"]
        first = effects(conn, visit_id, ref)
        pinned = first[0].get("pinned") if first else None
        if not E.is_human(by) and pinned not in (None, metadata["head_sha"]):
            # The person OK'd the commit the task came into Merge with. A newer one that changes the same lines (a
            # rebase, a conflict fixed without changing the PR's own lines) keeps the OK; one that changes others is
            # Reviewer 2's to read in QA first. A person may queue it as it is.
            same = same_changes(conn, {ref: pinned}, {ref: metadata["head_sha"]})
            if same is None:
                refuse("task_requirement", COMPARING.format(ref=ref))
            if not same:
                refuse("task_requirement", SENT_BACK_FOR_NEW.format(ref=ref))
        if conflicts(conn, task_id, str(link["repo"]), int(link["number"])):
            refuse("task_requirement", MO.conflict_line(conn, row, ref))
        if metadata["checks_state"] == "pending":
            refuse("task_requirement", f"The checks on {ref} are still running")
        if metadata["checks_state"] == "failure":
            refuse("task_requirement", f"Checks failed on {ref}: {failing_names(metadata)}")
        if metadata["checks_state"] == "cancelled":
            refuse("task_requirement", f"The checks on {ref} {cancelled_line(metadata)}")
        removals = failed_checks_removals(conn, task_id, visit_id, str(metadata["head_sha"]))
        named = [removal for removal in removals if removal.get("real")]
        if named:
            refuse(
                "task_requirement",
                f"GitHub took {ref} out of the merge queue for failed checks ({named[0].get('check')}). Send the task back "
                "to QA instead.",
            )
        if len(removals) >= 2:
            refuse(
                "task_requirement",
                f"GitHub took {ref} out of the merge queue for failed checks twice at this commit. Send the task back "
                "to QA instead.",
            )
    ts = _h().now()
    owed: list[Row] = []
    for link in again:
        ref, head = link["ref"], str(link["metadata"]["head_sha"])
        key = pr_key(str(link["repo"]), int(link["number"]))
        owed.append(SA.owe_again(conn, visit_id, SOURCE, QUEUE, PR, key, head))
        if earlier:
            note, data = MO.QUEUED_ANYWAY.format(ref=MO.ref(earlier)), {"before_merged": earlier["id"]}
        else:
            note, data = "Queue again", {}
        E._event(conn, task_id, by, "pr_queued", None, ref, note, {"ref": ref, "head": head, "again": True, **data})
    conn.execute("UPDATE tasks SET version=?, updated_at=? WHERE id=?", (int(row.get("version") or 0) + 1, ts, task_id))
    E.changed(conn, task_id, by)
    return owed


def pull(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe the `undo` of each `queue` the visit carried out (the task closed or went back; source_actions.undo).
    Idempotent."""
    visit = task.get("visit")
    if not visit:
        return []
    return SA.undo(conn, str(visit["id"]), SOURCE, QUEUE)


def _on_queueing_stage(conn: "sqlite3.Connection", task_ids: list[str]) -> list[Row]:
    E = _engine()
    out: list[Row] = []
    for task_id in task_ids:
        row: Row | None = E.task(conn, task_id)
        if row and row["status"] == "open" and row.get("visit") and queues_prs(row.get("stage")):
            out.append(row)
    return out


def removed_line(metadata: Row) -> str:
    """Why a PR left the merge queue, with the check that failed there when one did."""
    reason = str(metadata.get("removed_reason") or "GitHub gave no reason")
    check = metadata.get("failing_check")
    return f"Dropped from the merge queue: {reason} ({check})" if check else f"Dropped from the merge queue: {reason}"


def removed(conn: "sqlite3.Connection", metadata: Row) -> int:
    """After saved metadata shows a PR removed from the queue: give each task on a queueing stage that links it the one
    `queue_removed` wake for this `removed_at`, and a `pr_queue_removed` history row. Returns how many wakes were
    added. Tico never queues it again by itself."""
    from .. import task_wakes

    E = _engine()
    repo, number = str(metadata["repo"]), int(metadata["number"])
    ref = pr_ref(repo, number)
    added = 0
    for row in _on_queueing_stage(conn, linked_tasks(conn, repo, number)):
        task_id, visit = str(row["id"]), row["visit"]
        key = removed_wake_key(task_id, str(visit["id"]), ref, str(metadata["removed_at"]))
        if conn.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' "
            "AND json_valid(data) AND json_extract(data, '$.key')=?",
            (task_id, key),
        ).fetchone():
            continue
        head = str(metadata.get("head_sha") or "")
        failed = metadata.get("removed_reason") in FAILED_CHECKS
        real = failed and real_failure(metadata.get("failing_check"))
        data = {
            "key": key,
            "visit": visit["id"],
            "ref": ref,
            "reason": metadata.get("removed_reason"),
            "check": metadata.get("failing_check"),
            "url": metadata.get("failing_check_url"),
            "head": head,
            "checks_failed": failed,
            "real": real,
        }
        E._event(conn, task_id, E.SYSTEM, "pr_queue_removed", None, ref, removed_line(metadata), data)
        E.changed(conn, task_id, E.SYSTEM)
        owner = row.get("owner_id")
        if not E.is_bot(owner):
            continue  # a person owner has it in Needs you
        _end_bot_snooze(conn, row, removed_line(metadata))  # a snoozed task's wake would wait for the snooze
        drops = drops_in_visit(conn, task_id, str(visit["id"]))
        if drops >= DROPS:
            _up_the_ladder(
                conn, task_id, drops, f"GitHub took {ref} out of the merge queue {drops} times in this stage."
            )
            continue
        link = f" {metadata['failing_check_url']}" if metadata.get("failing_check_url") else ""
        if failed and real:
            lead = (
                f"GitHub took {ref} out of the merge queue for failed checks. {removed_line(metadata)}.{link} It is not "
                "queued again as it is: send the task back to QA with the failing step and the run link."
            )
        elif failed and len(failed_checks_removals(conn, task_id, str(visit["id"]), head)) >= 2:
            lead = (
                f"GitHub took {ref} out of the merge queue for failed checks again at the same commit. It is not queued "
                f"again as it is: send the task back to QA with the failing step and the run link.{link}"
            )
        elif metadata.get("removed_reason") == CONFLICTS:
            lead = f"GitHub took {ref} out of the merge queue. {MO.conflict_line(conn, row, ref)}"
        else:
            lead = (
                f"GitHub took {ref} out of the merge queue. {removed_line(metadata)}.{link} Queue it again with "
                f"`tico task queue {task_id}`, push a fix to the PR branch and then queue it again, or move it back."
            )
        fresh: Row = E.task(conn, task_id) or row
        body = task_wakes.body(conn, fresh, lead, to=str(owner))
        if task_wakes.add(conn, fresh, str(owner), visit, body, wake_key=key):
            added += 1
    return added


def _end_bot_snooze(conn: "sqlite3.Connection", row: Row, why: str) -> bool:
    """End the task's snooze as SYSTEM when a bot set it; a person's snooze stays. True when one ended."""
    E = _engine()
    snoozed: Row = row.get("snooze") or {}
    if not E.is_bot(snoozed.get("by_id")):
        return False
    E.unsnooze(conn, str(row["id"]), E.SYSTEM, why)
    return True


def snooze_news(conn: "sqlite3.Connection", task_id: str, what: str, lead: str, same: tuple[str, ...] = ()) -> bool:
    """GitHub news a bot's snooze on a pipeline stage may wait on (`what`, unique per event: the PR, its commit, the new
    state). The snooze ends, as SYSTEM, and whoever has the turn is woken once with `lead`, instead of sleeping to the
    clock time the bot guessed. The bot's snooze count in the stage stays as it was. Nothing happens to a person's
    snooze, a task outside a pipeline, news that woke the task already in this visit (a sweep that reads it again; a
    snooze set since waits for other news; `same` names other news that is this news, told another way), or while nobody
    but GitHub has the turn (the PR is in the merge queue). True when the task was woken."""
    from .. import task_wakes

    E = _engine()
    row: Row | None = E.task(conn, task_id)
    if not row or row["status"] != "open" or not row.get("visit"):
        return False
    snoozed: Row = row.get("snooze") or {}
    if not E.is_bot(snoozed.get("by_id")):
        return False
    key, *others = (f"{task_id}:{row['visit']['id']}:{WAKE_NEWS}:{w}" for w in (what, *same))
    if any(conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (k,)).fetchone() for k in (key, *others)):
        return False
    if not E._turn_of(conn, {**row, "snooze": None})[0]:
        return False
    _end_bot_snooze(conn, row, lead)
    fresh: Row = E.task(conn, task_id) or row
    target, _ = E._turn_of(conn, fresh)
    if not target:
        return False
    why = str(snoozed.get("why") or "").strip()
    text = f"{lead} Tico ended your snooze" + (f": {why}" if why else ".")
    body = task_wakes.body(conn, fresh, text, to=target)
    return bool(task_wakes.add(conn, fresh, target, fresh["visit"], body, wake_key=key))


def checks_news(conn: "sqlite3.Connection", task_ids: list[str], after: Row, before: Row | None) -> int:
    """The checks on a PR's newest commit finished (passed, failed, were cancelled or were all skipped), or finished again another way:
    `snooze_news` for each task that links it. Returns how many were woken."""
    state, head = str(after.get("checks_state") or ""), str(after.get("head_sha") or "")
    if after["state"] != "open" or state not in CHECKS_DONE or not head:
        return 0
    if before and (before.get("head_sha"), before.get("checks_state")) == (head, state):
        return 0
    ref = pr_ref(str(after["repo"]), int(after["number"]))
    if state == "success":
        lead = f"The checks on {ref} passed at {head[:12]}."
    elif state == "failure":
        lead = f"Checks failed on {ref} at {head[:12]}: {failing_names(after)}."
    elif state == "cancelled":
        lead = f"The checks on {ref} {cancelled_line(after, head[:12])}."
    else:
        lead = f"Every check on {ref} was skipped at {head[:12]}."
    return sum(snooze_news(conn, task_id, f"checks:{ref}:{head}:{state}", lead) for task_id in task_ids)


def mergeable_news(conn: "sqlite3.Connection", task_id: str, ref: str, head: str, mergeable: str) -> bool:
    """GitHub says a task's PR now conflicts with its base branch, or merges cleanly again (backend/github.py reads
    it): `snooze_news`, once per PR, commit and state. Before the Merge stage a new conflict is pr_conflicts' to tell (it
    says how to rebase): a bot's snooze only ends here, so that wake is not held behind it, and this returns False."""
    if mergeable == "conflict" and not queues_now(conn, task_id):
        row: Row | None = _engine().task(conn, task_id)
        if row and row.get("visit"):
            _end_bot_snooze(conn, row, f"{ref} conflicts with its base branch now.")
        return False
    if mergeable == "conflict":
        lead = f"{ref} conflicts with its base branch now."
    else:
        lead = f"{ref} merges cleanly with its base branch again."
    return snooze_news(conn, task_id, f"mergeable:{ref}:{head}:{mergeable}", lead)


def base_moved(
    conn: "sqlite3.Connection",
    repo: str,
    branch: str,
    sha: str = "",
    *,
    default_branch: str = "",
    merged_number: int | None = None,
) -> int:
    """`branch` of `repo` moved to `sha` (a merge into it, the merge queue's, a push; backend/github.py `base_moved`),
    `merged_number` the PR that merged when that is known. For each task a bot snoozed on the Merge stage whose open PR
    is based on it: `snooze_news`, once per task and base commit. Not while a PR of the task is in the queue or its
    checks run (their own news wakes it), and not on another stage, where every merge into main would wake it. A snooze
    whose reason names other PRs ("waiting for #3417 to merge") waits for one of those (`named_pr_done`), not for any
    move. Returns how many were woken."""
    if merged_number is not None:
        named_pr_done(conn, repo, int(merged_number), "merged")
    if not repo or not branch or not (sha or merged_number):
        return 0
    found = conn.execute(
        "SELECT DISTINCT l.task_id FROM task_links l JOIN tasks t ON t.id = l.task_id "
        "JOIN snoozes z ON z.task_id = t.id AND z.ended_at IS NULL AND z.by_id LIKE 'bot:%' "
        "WHERE l.kind='pr' AND l.state IN ('open','draft') AND lower(l.repo)=? AND t.status='open' "
        "AND t.pipeline_id IS NOT NULL AND coalesce(json_extract(l.detail,'$.base_ref'), ?)=? ORDER BY l.task_id",
        (repo.lower(), default_branch, branch),
    ).fetchall()
    woken = 0
    for (task_id,) in found:
        if not queues_now(conn, str(task_id)):
            continue
        own = prs_of(conn, str(task_id))
        numbers = {int(p["number"]) for p in own if str(p["repo"]).lower() == repo.lower()}
        if merged_number is not None and int(merged_number) in numbers:
            continue  # its own PR merged: the task moves on
        facts: list[Row] = [p["metadata"] for p in own if p["metadata"] and p["metadata"]["state"] == "open"]
        if any(f["queue_state"] in QUEUED or f["checks_state"] == "pending" for f in facts):
            continue
        snoozed = conn.execute("SELECT why FROM snoozes WHERE task_id=? AND ended_at IS NULL", (task_id,)).fetchone()
        if named_prs(conn, str(task_id), str((snoozed and snoozed[0]) or "")):
            continue
        what = f"base:{repo.lower()}:{branch}:{sha or '#' + str(merged_number)}"
        merged = f"{repo}#{merged_number} merged into {branch}." if merged_number is not None else ""
        # The merge may have woken it already as the PR its earlier snooze named (`named_pr_done`).
        same = (f"named:{repo.lower()}#{int(merged_number)}:merged",) if merged_number is not None else ()
        woken += snooze_news(conn, str(task_id), what, merged or f"{branch} moved to {sha[:12]}.", same)
    return woken


def named_prs(conn: "sqlite3.Connection", task_id: str, why: str) -> set[tuple[str, int]]:
    """The PRs a snooze's reason names, other than the task's own: {(repo, number)}, lower-case repo. A bare "#3417" is
    in each repository the task has a PR in, or in any ("") when it has none."""
    own = prs_of(conn, task_id)
    mine = {(str(p["repo"]).lower(), int(p["number"])) for p in own}
    repos = {repo for repo, _ in mine} or {""}
    out: set[tuple[str, int]] = set()
    for repo, number in PR_NAMED.findall(why):
        out |= {(repo.lower(), int(number))} if repo else {(r, int(number)) for r in repos}
    return out - mine


def named_pr_done(conn: "sqlite3.Connection", repo: str, number: int, state: str) -> int:
    """`repo`#`number` merged or was closed without merging (`state`). Each open pipeline task a bot snoozed with a
    reason naming it ("waiting for #3417 to land") gets `snooze_news`, on any stage, once per PR and state. Returns how
    many were woken."""
    if not repo or state not in ("merged", "closed"):
        return 0
    found = conn.execute(
        "SELECT z.task_id, z.why FROM snoozes z JOIN tasks t ON t.id = z.task_id "
        "WHERE z.ended_at IS NULL AND z.by_id LIKE 'bot:%' AND t.status='open' AND t.pipeline_id IS NOT NULL "
        "AND z.why LIKE ? ORDER BY z.task_id",
        (f"%#{int(number)}%",),
    ).fetchall()
    ref = pr_ref(repo, number)
    lead = f"{ref} merged." if state == "merged" else f"{ref} was closed without merging."
    woken = 0
    for task_id, why in found:
        names = named_prs(conn, str(task_id), str(why or ""))
        if (repo.lower(), int(number)) in names or ("", int(number)) in names:
            woken += snooze_news(conn, str(task_id), f"named:{repo.lower()}#{int(number)}:{state}", lead)
    return woken


def drops_in_visit(conn: "sqlite3.Connection", task_id: str, visit_id: str) -> int:
    """How many times GitHub took a PR of the task out of the merge queue during this visit, any PR: its
    `pr_queue_removed` history rows, one per removal."""
    found = conn.execute(
        "SELECT COUNT(*) FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' "
        "AND json_valid(data) AND json_extract(data, '$.visit')=?",
        (task_id, visit_id),
    ).fetchone()
    return int(found[0]) if found else 0


def failed_checks_removals(conn: "sqlite3.Connection", task_id: str, visit_id: str, head: str) -> list[Row]:
    """The removals of the task's PRs for failed checks at `head` during this visit, one `pr_queue_removed` row each
    (`removed` writes them): `check` is the check named there and `real` whether it really failed."""
    found = conn.execute(
        "SELECT data FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' "
        "AND json_valid(data) AND json_extract(data, '$.visit')=? AND json_extract(data, '$.head')=? "
        "AND json_extract(data, '$.checks_failed')=1",
        (task_id, visit_id, head),
    ).fetchall()
    return [cast(Row, json.loads(row[0])) for row in found]


def queued_as_is(conn: "sqlite3.Connection", task_id: str) -> list[Row]:
    """The task's open PRs still at the commit they were last queued at (the newest `pr_queued` row for each names it),
    oldest link first: [{ref, head, reason, check, failed}], `reason` and `check` from the newest removal from the queue
    at that commit, a failed check's first (None when none was recorded), and `failed` whether that removal was for a
    failed check. Task #344: the head the merge queue just ran."""
    out: list[Row] = []
    for link in prs_of(conn, task_id):
        metadata = link["metadata"]
        if not metadata or metadata["state"] != "open" or not metadata.get("head_sha"):
            continue
        ref, head = pr_ref(str(link["repo"]), int(link["number"])), str(metadata["head_sha"])
        queued = conn.execute(
            "SELECT json_extract(data, '$.head') FROM history_events WHERE subject_type='task' AND subject_id=? "
            "AND kind='pr_queued' AND json_valid(data) AND json_extract(data, '$.ref')=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id, ref),
        ).fetchone()
        if not queued or queued[0] != head:
            continue
        removal = conn.execute(
            "SELECT json_extract(data, '$.reason'), json_extract(data, '$.check'), "
            "COALESCE(json_extract(data, '$.checks_failed'), 0) FROM history_events "
            "WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' AND json_valid(data) "
            "AND json_extract(data, '$.ref')=? AND json_extract(data, '$.head')=? "
            "ORDER BY COALESCE(json_extract(data, '$.checks_failed'), 0) DESC, created_at DESC, rowid DESC LIMIT 1",
            (task_id, ref, head),
        ).fetchone()
        out.append(
            {
                "ref": ref,
                "head": head,
                "reason": removal[0] if removal else None,
                "check": removal[1] if removal else None,
                "failed": bool(removal and removal[2]),
            }
        )
    return out


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


def metadata_changed(conn: "sqlite3.Connection", task_ids: list[str], after: Row, before: Row | None) -> None:
    """What changed PR metadata means on the Merge stage, for the open tasks that link it: a PR whose metadata came in
    after the task entered is owed its `queue` now (never once one was owed for it in this visit: Tico never queues a PR
    again by itself), and a new commit tells the pipeline's manager once ("Changed after OK", C22)."""
    E = _engine()
    repo, number = str(after["repo"]), int(after["number"])
    ref = pr_ref(repo, number)
    for row in _on_queueing_stage(conn, task_ids):
        visit_id = str(row["visit"]["id"])
        if not effects(conn, visit_id, ref) and not MO.stops_queue(conn, row):
            _owe_queue(conn, visit_id, {"repo": repo, "number": number, "metadata": after})
        head = after.get("head_sha")
        if before and before.get("head_sha") and head and head != before["head_sha"] and after["state"] == "open":
            E._tell_manager(conn, row, f"Changed after OK: {ref} has a new commit", f"{WAKE_CHANGED}:{head}")


# ----------------------------------------------------------------------------- carrying out the queue (source_actions)
def run(store: Any, github: "GitHub", now: str | None = None) -> int:
    """The Heartbeat's `source_actions` step with GitHub's client alone (backend/source_actions.py `run`)."""
    return SA.run(store, {SOURCE: github}, now)


def _pr_of(effect: Row) -> tuple[str, int]:
    return split_key(str(effect["item_key"]))


def look_first(store: Any, github: "GitHub", effect: Row) -> None:
    """Before a release asked for is carried out, read the repo's releases from GitHub now (`read_releases_now`): the
    repo may have published one with the merge since Tico last read them."""
    if effect["action"] == RELEASE and effect["kind"] == SA.ACT:
        read_releases_now(store, github, str(effect["task_id"]), str(effect["item_key"]))


def done_already(conn: "sqlite3.Connection", effect: Row) -> bool:
    """GitHub merged the PR before Tico's owed `queue` ran: it is done, with no call. One someone else queued (or set
    to merge by itself) is still carried out, so `tico/merge-ok` is posted on its head and it does not wait forever on
    a ruleset that requires the check (`enqueue` answers ok for a PR already queued). A release asked for is not cut
    when a published release already holds the task's merge (github_release.found_already)."""
    if effect["action"] == RELEASE:
        return GR.found_already(conn, effect)
    metadata = pr(conn, *_pr_of(effect))
    return metadata is not None and metadata["state"] == "merged"


def undo_moot(conn: "sqlite3.Connection", effect: Row) -> bool:
    """An `undo` of a visit that left Merge takes the PR out of the queue for good, whoever else queued it: it is moot
    while an open visit (the task's next one, or another task's on the same PR) has queued that PR at the same head
    (`queued_by_tico`, done). A queue still only owed does not count: it may be mooted unrun, which owes no undo, and
    the PR would stay queued; it runs after this older undo anyway. Against another head the undo is carried out, and
    `recorded` queues the wanted head again."""
    if effect["action"] != QUEUE or not effect.get("pinned"):
        return False
    return queued_by_tico(conn, *_pr_of(effect), str(effect["pinned"]), states=("done",))


def _wanted_again(conn: "sqlite3.Connection", effect: Row) -> None:
    """An old visit's `undo` took the PR out of the queue: each `queue` an open visit had carried out on it (and not
    taken back) is owed again, so the queue holds what the stage wants now. Only after Tico's own dequeue: a PR GitHub
    took out (failed checks) answers `absent` and still waits for an explicit queue-again. (A visit leaves Merge only
    back or closed, which owes its undo, or past a merged PR, so `left_at` is a belt to the undo's braces.)"""
    found = _rows(
        conn,
        "SELECT e.visit_id, e.item_type, e.item_key, e.pinned FROM effects e JOIN visits v ON v.id = e.visit_id "
        "WHERE e.source=? AND e.action=? AND e.kind=? AND e.state='done' AND lower(e.item_key)=lower(?) "
        "AND e.visit_id<>? AND v.left_at IS NULL AND NOT EXISTS (SELECT 1 FROM effects u WHERE u.visit_id = e.visit_id "
        "AND u.source = e.source AND u.action = e.action AND u.kind=? AND lower(u.item_key) = lower(e.item_key))",
        (SOURCE, QUEUE, SA.ACT, str(effect["item_key"]), str(effect["visit_id"]), SA.UNDO),
    )
    for row in found:
        LOG.warning(
            "merge queue: old undo %s dequeued %s; visit %s is owed its queue again",
            effect["id"],
            row["item_key"],
            row["visit_id"],
        )
        SA.owe_again(
            conn, str(row["visit_id"]), SOURCE, QUEUE, str(row["item_type"]), str(row["item_key"]), row["pinned"]
        )


MERGE_OK = "tico/merge-ok"  # the check only the server posts (docs/pipelines-v3.md "GitHub": "tico/merge-ok")


def merge_ok_line(effect: Row, ok: bool) -> str:
    """The one-line summary of the check: the task by id only (a private task's title stays in Tico)."""
    if ok:
        return f"Queued by Tico's Merge stage for task {effect.get('task_id')}, after its sign-off."
    return f"Taken out by Tico: task {effect.get('task_id')} left the Merge stage."


def carry_out(github: "GitHub", effect: Row) -> Row:
    """GitHub's call for one action, no transaction held: queue the PR at its pinned head, or take it out.

    Queueing posts `tico/merge-ok` = success on exactly the pinned head first, the one `enqueue` is about to queue;
    a post that fails raises, so nothing is queued and the call is tried again like any failed call (then the owner's,
    `gave_up`). Taking it out posts failure on that head after the dequeue, so neither a bot nor GitHub queues it
    again with Tico's success. A release asked for: github_release.carry_out."""
    if effect["action"] == RELEASE:
        return GR.carry_out(github, effect)
    repo, number = _pr_of(effect)
    head = str(effect.get("pinned") or "")
    if effect["kind"] == SA.ACT:
        github.ready(repo, number)  # a Builder's draft reaches Merge: GitHub queues no draft
        github.post_check(repo, head, True, merge_ok_line(effect, True))
        said = github.enqueue(repo, number, head)
    else:
        said = github.dequeue(repo, number)
        if head:
            github.post_check(repo, head, False, merge_ok_line(effect, False))
    return said if said.get("ok") or said.get("reason") else {**said, "reason": "GitHub said no"}


def gave_up(conn: "sqlite3.Connection", effect: Row, reason: str) -> None:
    """A `queue` failed for good: "PR merged" says GitHub refused, on the stage owner, told once. A release refused:
    github_release.gave_up."""
    if effect["action"] == RELEASE:
        GR.gave_up(conn, effect, reason)
        return
    if effect["kind"] != SA.ACT:
        return
    E = _engine()
    row: Row | None = E.task(conn, str(effect["task_id"]))
    visit: Row = (row.get("visit") or {}) if row else {}
    if row and row["status"] == "open" and visit.get("id") == effect["visit_id"]:
        ref = pr_ref(*_pr_of(effect))
        E.changed(conn, str(row["id"]), E.SYSTEM)
        if reason == CONFLICTS:
            text = MO.conflict_line(conn, row, ref)
        else:
            text = (
                f"GitHub refused the merge of {ref}: {reason}. Queue it again with `tico task queue {row['id']}` once "
                "it can merge, or move it back."
            )
        E.tell_owner(conn, str(row["id"]), text, f"{WAKE_REFUSED}:{ref}:{effect['pinned']}")


def recorded(conn: "sqlite3.Connection", effect: Row, said: Row, state: str, version: int) -> bool:
    """Save what GitHub said to an action it carried out as the PR's metadata. True when the PR went in the queue
    (source_actions takes it out again when the action was moot by then). A release asked: github_release.recorded."""
    if effect["action"] == RELEASE:
        GR.recorded(conn, effect)
        return False
    E = _engine()
    repo, number = _pr_of(effect)
    ref: str = pr_ref(repo, number)
    task_id = str(effect["task_id"])
    how = str(said.get("how") or "")
    if effect["kind"] == SA.ACT:
        if how in QUEUED:
            save_and_settle(conn, repo, number, {"queue_state": how}, version)
            data = {"ref": ref, "head": effect["pinned"], "how": how}
            E._event(conn, task_id, E.SYSTEM, "pr_queued", None, ref, None, data)
            return True
        # merged at once, where the repo has neither a queue nor auto-merge: read in full on the next pass
        IM.mark_due(conn, SOURCE, PR, pr_key(repo, number))
        if how == "merged":
            _merged_by_tico(conn, repo, number)
        return False
    if how == "merged":
        IM.mark_due(conn, SOURCE, PR, pr_key(repo, number))
        _merged_by_tico(conn, repo, number)
        _merged_anyway(conn, task_id, str(effect["visit_id"]), ref)
    elif how == "dequeued":
        save_and_settle(conn, repo, number, {"queue_state": "none"}, version)
        E._event(conn, task_id, E.SYSTEM, "pr_dequeued", ref, None, None, {"ref": ref})
        _wanted_again(conn, effect)
    return False


def _merged_by_tico(conn: "sqlite3.Connection", repo: str, number: int) -> None:
    """Tico's call found the PR merged: the other PRs on its base are read at once (backend/github.py `base_moved`)."""
    from .. import github as G

    cast(Any, G).merged_by_tico(conn, repo, number)


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


# ----------------------------------------------------------------------------- the merge queue's own commit
def queued_by_tico(
    conn: "sqlite3.Connection", repo: str, number: int, head_sha: str, states: tuple[str, ...] = ("owed", "done")
) -> bool:
    """Whether Tico's Merge stage queues the PR at exactly `head_sha`: a `queue` action at that head, done or still
    owed (GitHub may ask for the group's checks before the enqueue's answer is recorded), in the open visit of an open
    task, and not taken back. `states` narrows it (`undo_moot`: done only)."""
    marks = ",".join("?" * len(states))
    found = conn.execute(
        "SELECT 1 FROM effects e JOIN visits v ON v.id = e.visit_id JOIN tasks t ON t.id = v.task_id "
        f"WHERE e.source=? AND e.action=? AND e.kind=? AND e.state IN ({marks}) AND lower(e.item_key)=lower(?) "
        "AND lower(e.pinned)=lower(?) AND v.left_at IS NULL AND t.status='open' AND NOT EXISTS (SELECT 1 FROM effects u "
        "WHERE u.visit_id = e.visit_id AND u.source = e.source AND u.action = e.action AND u.kind=? "
        "AND lower(u.item_key) = lower(e.item_key))",
        (SOURCE, QUEUE, SA.ACT, *states, pr_key(repo, number), head_sha, SA.UNDO),
    ).fetchone()
    return found is not None


GROUP_READ_WAITS = (1.0, 2.0)  # seconds between reads of a group GitHub does not list yet (a webhook has 10)


def tracked(conn: "sqlite3.Connection", repo: str, number: int) -> bool:
    """Whether a PR is a Tico task's: an open task on the engine links it (`linked_tasks`)."""
    return bool(linked_tasks(conn, repo, number))


def answer_merge_group(store: Any, github: "GitHub", payload: Row) -> Row:
    """GitHub's merge queue asks for the checks on a group's merge commit (`merge_group` `checks_requested`): post
    `tico/merge-ok` there. Success when Tico's Merge stage queued every PR in the group at exactly its head now. Failure
    when a PR of a Tico task is in it that the Merge stage did not queue at that head, and when Tico cannot read or
    decide (closed). Neutral when the rest are PRs no Tico task links: on a repository shared with people, their PRs
    are not Tico's to pass or fail, and GitHub counts neutral as passing a required check. A repository whose ruleset
    requires the check still lets no untracked PR in: it is refused at its own head, where Tico never posts the check
    (docs/github-app.md "Require Tico's merge check"). No transaction is held during GitHub's calls. Returns {ok, posted}:
    `ok` is True (success), False (failure) or None (neutral)."""
    group = _obj(payload.get("merge_group"))
    repo = str(_obj(payload.get("repository")).get("full_name") or "")
    head = str(group.get("head_sha") or "")
    if payload.get("action") != "checks_requested" or not repo or not head:
        return {}
    ok: bool | None = False
    line = "Tico could not read this merge group, so it can't say each PR was signed off."
    try:
        prs = github.merge_group_prs(repo, str(group.get("base_ref") or ""), head)
        for wait in GROUP_READ_WAITS:  # GitHub may list the group's entry a moment after it asks
            if prs:
                break
            time.sleep(wait)
            prs = github.merge_group_prs(repo, str(group.get("base_ref") or ""), head)
        with store.read() as conn:
            others = [p for p in prs if not queued_by_tico(conn, repo, int(p["number"]), str(p.get("head_sha") or ""))]
            theirs = [p for p in others if tracked(conn, repo, int(p["number"]))]
        if prs and not others:
            ok, line = True, f"Tico's Merge stage queued every PR in this group: {len(prs)}."
        elif theirs:
            refs = ", ".join(pr_ref(repo, int(p["number"])) for p in theirs)
            line = f"Not queued by Tico's Merge stage at this commit: {refs}."
        elif prs:
            refs = ", ".join(pr_ref(repo, int(p["number"])) for p in others)
            ok, line = None, f"Not Tico tasks' PRs, so not Tico's to pass: {refs}."
    except Exception:  # noqa: BLE001 - fail closed: the group waits on a failure, never on a guess
        LOG.warning("Reading the merge group %s of %s failed", head, repo, exc_info=True)
    try:
        posted = github.post_check(repo, head, ok, line)
    except Exception:  # noqa: BLE001 - nothing posted: the queue times the group out, which removes it
        LOG.warning("Posting %s on the merge group %s of %s failed", MERGE_OK, head, repo, exc_info=True)
        posted = False
    return {"ok": ok, "posted": posted}


# ----------------------------------------------------------------------------- GitHub's four conditions
# (were requirement kinds of their own in backend/task_requirements.py; now kind `item` with source `github`)
from ..task_requirements import MET, NOT_MET, WAITING, Standing
from . import Condition, Item


# The four conditions read saved metadata only (backend/item_sources/github.py) and, for "PR merged", the Merge stage's effects
# (backend/item_sources/github.py). With many PRs, the first one not met, in link order, gives the line. A PR closed without
# merging is left out while another is linked; when every one is, the owner is told. A wait on GitHub ends: when GitHub
# has said nothing about the PR (or the releases it waits for) for 2 hours, it is the owner's turn (`_silent`).
def _now() -> Any:
    from .. import hubdb

    return hubdb


# A task with no PR linked needs a repository the company's GitHub App reaches. With no App installed nobody can open
# one, so the condition says a person connects GitHub first (`Standing.fix`): whoever has the task is Paused on it, the
# company's Owner has one row in Needs you for every task it holds up, and the tasks carry on once the App is installed
# (waiting_on).
CONNECT = "Connect GitHub"
CONNECT_AT = "#/tools/github"
CHOOSE = "Choose a repository"
CHOOSE_AT = "#/settings/general/set-repos"
_INSTALLED = "SELECT installation_id FROM github_app WHERE id='app'"
_IN_USE = "SELECT 1 FROM repositories WHERE enabled=1 AND bot_repo=0 LIMIT 1"


def _one(conn: "sqlite3.Connection", statement: str) -> bool:
    import sqlite3

    from .. import read_cache

    try:
        found = cast("Any", read_cache).one(conn, statement)
    except sqlite3.OperationalError:
        return False  # no table: GitHub was never set up here
    return bool(found and found[0])


def installed(conn: "sqlite3.Connection") -> bool:
    """Whether the company's GitHub App is connected and installed, as Health's GitHub check counts it."""
    return _one(conn, _INSTALLED)


def first_fix(conn: "sqlite3.Connection") -> tuple[str, str] | None:
    """What a person does before any bot can work on a repository, as (line, where), or None when nothing is missing:
    connect GitHub (no App installed), then choose a repository (none is In use, docs/repositories.md). Bots reach only
    the repositories In use, so with none a task's bot has nowhere to open a PR."""
    if not installed(conn):
        return CONNECT, CONNECT_AT
    if not _one(conn, _IN_USE):
        return CHOOSE, CHOOSE_AT
    return None


def _prs(conn: "sqlite3.Connection", task: Row, requirement: Row) -> tuple[list[Row], Standing | None]:
    """The PRs a GitHub condition reads, each with `ref` and `metadata`, or the standing when there are none to read."""
    links: list[Row] = [
        {**link, "ref": pr_ref(str(link["repo"]), int(link["number"]))} for link in prs_of(conn, task["id"])
    ]
    fix = None if links else first_fix(conn)
    if fix:
        return [], Standing(requirement, NOT_MET, task.get("owner_id"), fix[0], fix=fix[1])
    if not links:
        return [], Standing(requirement, NOT_MET, task.get("owner_id"), "No PR linked")
    live = [link for link in links if not (link["metadata"] and link["metadata"]["state"] == "closed")]
    if not live:
        return [], Standing(requirement, NOT_MET, task.get("owner_id"), f"{links[0]['ref']} was closed without merging")
    return live, None


def _first(requirement: Row, found: list[Standing]) -> Standing:
    """The standing of many PRs' the task shows, in link order: the first that is someone's turn (a PR dropped from the
    queue while another still waits in it), else the first that waits, else the first met one."""
    return next((s for s in found if s.state == NOT_MET), None) or next((s for s in found if not s.met), found[0])


def _reading(requirement: Row) -> Standing:
    return Standing(requirement, WAITING, SOURCE, "Reading the PR from GitHub")


def _seconds_since(ts: object) -> float | None:
    H = _now()
    at, now = H.parse_ts(ts), H.parse_ts(H.now())
    return None if at is None or now is None else (now - at).total_seconds()


def _silent(task: Row, requirement: Row, link: Row, found: Standing, heard: object = None) -> Standing:
    """A wait on GitHub about one PR becomes the owner's turn once GitHub has said nothing for SILENT_S: no webhook and
    no read of the PR got through (GitHub down or refusing, or the PR gone). `heard` is when GitHub last answered, when
    the caller knows better than the PR's metadata (its releases' read); else the metadata's last read, else when it was
    linked. Every wait on GitHub so ends in a bot's turn, whatever the stage's expected time."""
    if found.state != WAITING or found.on != SOURCE:
        return found
    metadata: Row = link.get("metadata") or {}
    read = None if metadata.get("due_at") else metadata.get("read_at")  # marked for a full read: heard of when saved
    quiet = _seconds_since(heard or read or metadata.get("updated_at") or link.get("created_at"))
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
    metadata: Row | None = link["metadata"]
    if not metadata or (metadata["state"] == "open" and quiet.state == NOT_MET):
        return quiet
    return Standing(requirement, MET, line="PR open")


def _checks_one(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:

    metadata, ref = link["metadata"], link["ref"]
    if not metadata:
        return _reading(requirement)
    if metadata["state"] == "merged":
        return Standing(requirement, MET, line="Checks pass")
    state = metadata["checks_state"]
    if state == "failure":
        return Standing(
            requirement, NOT_MET, task.get("owner_id"), f"Checks failed on {ref}: {failing_names(metadata)}"
        )
    if state == "cancelled":
        return Standing(requirement, NOT_MET, task.get("owner_id"), f"Checks on {ref} {cancelled_line(metadata)}")
    if state == "skipped":
        return Standing(requirement, NOT_MET, task.get("owner_id"), f"No checks ran on {ref}: every check was skipped")
    if state == "pending":
        running = _seconds_since(metadata.get("checks_started_at"))
        if running is not None and running >= CHECKS_TOO_LONG_S:
            return Standing(requirement, NOT_MET, task.get("owner_id"), "Checks still running after 2 hours")
        return Standing(requirement, WAITING, SOURCE, "Checks running")
    if state == "none":
        seen = _seconds_since(metadata.get("head_seen_at"))
        if seen is None or seen < NO_CHECKS_AFTER_S:
            return Standing(requirement, WAITING, SOURCE, "Waiting for checks")
        if metadata.get("draft") or not runs_checks(conn, str(link["repo"])):
            return Standing(requirement, MET, line="No checks on this repo")  # C13
        # A ready PR on a repo that runs checks: no checks yet is an outage or a skipped run, never a pass.
        if seen < CHECKS_TOO_LONG_S:
            return Standing(requirement, WAITING, SOURCE, "Waiting for checks")
        return Standing(requirement, NOT_MET, task.get("owner_id"), f"No checks on {ref} after 2 hours")
    return Standing(requirement, MET, line="Checks pass")


def runs_checks(conn: "sqlite3.Connection", repo: str) -> bool:
    """Whether a repo runs checks: a PR of it Tico has saved had any (running, passed, failed, cancelled or skipped), not counting
    Tico's own. Read from saved PRs, so it costs no call to GitHub; a repo's first PR has none to go by (C13 then)."""
    prefix = repo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "#%"
    found = conn.execute(
        "SELECT 1 FROM item_metadata WHERE source=? AND item_type=? AND item_key LIKE ? ESCAPE '\\' "
        "AND json_extract(fields, '$.checks_state') IN ('pending', 'success', 'failure', 'skipped', 'cancelled') LIMIT 1",
        (SOURCE, PR, prefix),
    ).fetchone()
    return found is not None


def checks_pass(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    found = [_silent(task, requirement, link, _checks_one(conn, task, requirement, link)) for link in prs]
    if all(s.met for s in found):
        return Standing(requirement, MET, line=next((s.line for s in found if s.line == "Checks pass"), found[0].line))
    return _first(requirement, found)


def _merged_one(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:
    metadata, ref = link["metadata"], link["ref"]
    if not metadata:
        return _reading(requirement)
    if metadata["state"] == "merged":
        return Standing(requirement, MET, line="Merged")
    owner = task.get("owner_id")
    queues = queues_prs(task.get("stage"))
    effect = newest(conn, task["visit"]["id"], ref) if queues else None
    if metadata["queue_state"] in ("queued", "auto_merge"):  # queued, by Tico or anyone: an owed queue_pr is then done
        return Standing(requirement, WAITING, SOURCE, "In the merge queue")
    if effect and effect["state"] == "owed":
        return Standing(requirement, WAITING, SOURCE, "Queuing the PR")
    if effect and effect["state"] == "failed":
        return Standing(requirement, NOT_MET, owner, f"GitHub refused the merge: {effect.get('last_error') or ref}")
    if metadata["queue_state"] == "removed":
        return Standing(requirement, NOT_MET, owner, removed_line(metadata))
    if effect:
        return Standing(requirement, WAITING, SOURCE, "In the merge queue")
    if queues:
        earlier = MO.held(conn, task)
        if earlier:
            # It goes after a task that has not merged: merge_order's own standing is its turn; this says why.
            return Standing(requirement, WAITING, SOURCE, f"Not queued until {MO.ref(earlier)} merges")
        closed = MO.closed_before(conn, task)
        if closed:
            return Standing(requirement, NOT_MET, owner, MO.closed_line(closed))
        return Standing(requirement, WAITING, SOURCE, "Queuing the PR")  # owed as soon as its head is known
    return Standing(requirement, NOT_MET, owner, "Not merged yet")


def pr_merged(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(
        requirement, [_silent(task, requirement, link, _merged_one(conn, task, requirement, link)) for link in prs]
    )


def _release_one(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:
    metadata = link["metadata"]
    if not metadata:
        return _reading(requirement)
    if metadata["state"] != "merged" or not metadata.get("merged_sha"):
        return Standing(requirement, WAITING, SOURCE, "Not merged yet")
    settings: Row = requirement.get("settings") or {}
    repo = release_repo(settings, str(link["repo"]))
    publishes = publishes_releases(conn, repo)
    if publishes is None:
        return Standing(requirement, WAITING, SOURCE, "Reading releases")
    if not publishes:
        return Standing(requirement, MET, line="This repo doesn't publish releases")  # C31
    release = release_of(
        conn, repo, str(metadata["merged_sha"]), prereleases=settings.get("prereleases", True) is not False
    )
    if release:
        return Standing(requirement, MET, line=f"In {release['tag']}")
    return GR.standing(conn, task, requirement, repo, metadata)  # who cuts it, and what became of the ask


def in_release(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None) -> Standing:
    prs, said = _prs(conn, task, requirement)
    if said:
        return said
    return _first(requirement, [_release_silent(conn, task, requirement, link) for link in prs])


def _release_silent(conn: "sqlite3.Connection", task: Row, requirement: Row, link: Row) -> Standing:
    """In a release for one PR, with `_silent`: a merged PR is not read again, so once it merged GitHub's word is the
    read of the release repo's releases (never read yet: since it merged)."""
    found = _release_one(conn, task, requirement, link)
    metadata: Row = link.get("metadata") or {}
    if metadata.get("state") != "merged":
        return _silent(task, requirement, link, found)
    repo = release_repo(requirement.get("settings") or {}, str(link["repo"]))
    return _silent(task, requirement, link, found, releases_read_at(conn, repo) or metadata.get("updated_at"))


NAME = "GitHub"
ITEMS = {PR: Item(PR, "PR")}
CONDITIONS = {
    "pr_open": Condition("pr_open", "PR open", PR, pr_open),
    "checks_pass": Condition("checks_pass", "Checks pass", PR, checks_pass),
    "pr_merged": Condition("pr_merged", "PR merged", PR, pr_merged, can_push_past="never"),
    # `release_repo` ("owner/name"; empty means the server's release repo, else each PR's own repo) and `prereleases`
    # (a pre-release counts, default on); `release_with`, how Tico cuts a release when the Release Owner asks
    # (`workflow:<file>`, `tag`, or empty: the repo releases on its own; github_release.py).
    "in_release": Condition(
        "in_release",
        "In a release",
        PR,
        in_release,
        ("release_repo", "prereleases", "release_with"),
        can_push_past="never",
    ),
}
