# pyright: strict
"""Tico merges approved work itself (docs/releasing.md, "Tico merges").

A bot's card whose pull request is ready to merge (verdict_moves.merge_ready: the last check before the merge approved
the PR's current head, and CI is green there) is armed by Tico as the GitHub App, at that exact head: added to the merge
queue when the base branch has one, else merged. Nobody is woken to merge it (verdict_moves.merge_wake).

The maintenance pass (`sweep`, from heartbeat.github_sync, at most once a minute) does the rest:
- a ready pull request Tico has not armed at its head is armed;
- one armed more than STALE_MINUTES ago that GitHub does not have queued (arming failed, auto-merge was turned off) is
  armed again, at most MAX_ARMS times per head;
- an approved pull request that conflicts with its base, or one Tico could not arm MAX_ARMS times, becomes one task for
  the card's pipeline manager (`hand_off`);
- a card whose pull requests all merged goes to its merged step (Pending Release), unless it is there or past it.

A queue removal of a head Tico armed is settled by `removal` (from github._settle_removal): a removal that is not a real
failure (a cancelled or superseded run, an infra error, a cleared queue) is armed again once per head. A real failure, a
conflict or a second removal goes to the pipeline manager with the failing jobs and their log links. The builder is not
woken and the card stays where it is. A removal by hand stops Tico arming that head.

The state is the link's `detail.merge_arm`: {head, at, arms, rearmed, handed, stopped, error}. A new head starts over.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

from . import hubdb, step_flow
from .store import Problem

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterable

    from .github_app import GitHubApp

# hubdb and step_flow are not typed: calls through these aliases return Any, which the strict check accepts (as in
# backend/review_pages.py).
H: Any = hubdb
F: Any = step_flow
Row = dict[str, Any]
Jobs = list[Row]


def _row(value: Any) -> Row:
    return cast(Row, value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[Row]:
    return [_row(v) for v in cast(list[Any], value)] if isinstance(value, list) else []


log = logging.getLogger("tico.merge_arm")

SWEEP_EVERY_S = 60
STALE_MINUTES = 10  # an armed head GitHub does not have queued after this long is read and armed again
MAX_ARMS = 3  # arming attempts per head before the manager gets it
CANDIDATES = 200
MERGED_DAYS = 14  # a merged card is moved on by the sweep for this long after its merge
SOFT_REASONS = ("queue_cleared", "roll_back", "rollback", "ci_timeout")  # removals that say nothing about the change
FAILED = ("failure", "timed_out", "startup_failure")
FIX_ROUTED = "merge_fix.routed"
_last_sweep = [0.0]

READ = """query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
mergeCommitAllowed squashMergeAllowed rebaseMergeAllowed
pullRequest(number:$number){id state merged headRefOid mergeable isInMergeQueue isMergeQueueEnabled
autoMergeRequest{enabledAt}}}}"""
AUTO = """mutation($id:ID!,$head:GitObjectID!){
enablePullRequestAutoMerge(input:{pullRequestId:$id,expectedHeadOid:$head}){clientMutationId}}"""
ENQUEUE = """mutation($id:ID!,$head:GitObjectID!){
enqueuePullRequest(input:{pullRequestId:$id,expectedHeadOid:$head}){clientMutationId}}"""
MERGE = """mutation($id:ID!,$head:GitObjectID!,$method:PullRequestMergeMethod!){
mergePullRequest(input:{pullRequestId:$id,expectedHeadOid:$head,mergeMethod:$method}){clientMutationId}}"""


# ----------------------------------------------------------------------------------------------- who merges
def _verdicts() -> Any:
    """backend/verdict_moves, imported late: it imports this module."""
    from . import verdict_moves

    return verdict_moves


def tico_merges(c: sqlite3.Connection, row: Row | None, pr: Row) -> bool:
    """Whether Tico merges this pull request itself: the card is a bot's, and the company's GitHub App is connected to
    the repository's account and reaches the repository. A person's card keeps the person's merge."""
    repo = str(pr.get("repo") or "").lower()
    return bool(
        row
        and H.is_bot(row.get("owner_id"))
        and "/" in repo
        and H._has_table(c, "github_app")
        and c.execute(
            "SELECT 1 FROM github_app a JOIN repositories r ON lower(r.full_name)=? AND r.reachable=1 "
            "WHERE a.id='app' AND lower(a.org)=? LIMIT 1",
            (repo, repo.split("/")[0]),
        ).fetchone()
        is not None
    )


def approved(c: sqlite3.Connection, task: Row | None, link_id: str, *, ignore_conflict: bool = False) -> Any:
    """(pull request, head) when Tico merges this link (`tico_merges`), the card's checks approved its current head and
    CI is green there, the same reading the merge wake uses (verdict_moves.merge_ready); None otherwise. `ignore_conflict` reads it as if it did
    not conflict: an approved pull request that conflicts is the manager's to fix, not the builder's."""
    V = _verdicts()
    row = V._ticket(c, task)
    if not row or not H.is_bot(row.get("owner_id")):
        return None
    live: list[Row] = [p for p in V._prs(c, row) if p.get("state") in ("open", "draft")]
    if not F.pr_checks(c, row) and len(live) != 1:
        return None  # a card with two open pull requests waits for its owner, as the merge wake does
    pr = next((p for p in live if p["id"] == link_id), None)
    if not pr or not tico_merges(c, row, pr):
        return None
    return V.merge_ready(c, row, pr={**pr, "mergeable": "clean"} if ignore_conflict else pr)


# ----------------------------------------------------------------------------------------------- GitHub
def _graphql(service: Any, token: str, query: str, variables: Row) -> tuple[Row, str]:
    """(data, error message) for one GraphQL call; a failed request raises Problem (retried next pass)."""
    response = service._call(
        "POST",
        "/graphql",
        headers={"Authorization": "Bearer " + token},
        json={"query": query, "variables": variables},
    )
    if response.status_code != 200:
        raise Problem("github_unreachable", f"GitHub answered HTTP {response.status_code}", 502, retryable=True)
    body: Row = response.json() or {}
    errors = "; ".join(str(e.get("message") or e) for e in _rows(body.get("errors")))
    return body.get("data") or {}, errors


def arm(service: Any, repo: str, number: int | str, head: str) -> tuple[str, str, bool]:
    """Arm one pull request at `head`: (result, error, sent). `result` is in_queue (GitHub has it in the queue), queued
    (queued or set to auto-merge just now, or auto-merge already on), merged, moved (closed, or at another head), conflict, refused (GitHub said no; `error` says why) or
    retry (GitHub could not be asked). `sent` is whether a merge request went to GitHub."""
    try:
        token: str = service.mint([repo], {"contents": "write", "pull_requests": "write", "metadata": "read"})[0]
        owner, name = repo.split("/", 1)
        data, error = _graphql(service, token, READ, {"owner": owner, "name": name, "number": int(number)})
        found = _row(_row(data.get("repository")).get("pullRequest"))
        if not found:
            return "retry", error or "GitHub did not return the pull request", False
        if found.get("merged"):
            return "merged", "", False
        if found.get("state") != "OPEN" or str(found.get("headRefOid") or "").lower() != head:
            return "moved", "", False
        if found.get("mergeable") == "CONFLICTING":
            return "conflict", "", False
        if found.get("isInMergeQueue"):
            return "in_queue", "", False
        if found.get("autoMergeRequest"):
            return "queued", "", False
        ids: Row = {"id": found["id"], "head": head}
        if found.get("isMergeQueueEnabled"):
            # As `gh pr merge` does: auto-merge adds a green pull request to the queue; enqueue when auto-merge is off.
            for mutation in (AUTO, ENQUEUE):
                _, error = _graphql(service, token, mutation, ids)
                if not error:
                    return "queued", "", True
            return "refused", error, True
        repository: Row = data["repository"]
        method = next(
            (m for m, ok in (("MERGE", "mergeCommitAllowed"), ("SQUASH", "squashMergeAllowed")) if repository.get(ok)),
            "REBASE",
        )
        _, error = _graphql(service, token, MERGE, {**ids, "method": method})
        return ("refused", error, True) if error else ("merged", "", True)
    except Problem as problem:
        if problem.retryable or problem.code == "github_unreachable":
            return "retry", str(cast(Any, problem).detail), False
        return "refused", str(cast(Any, problem).detail), True
    except Exception:
        log.exception("Arming the merge of %s #%s failed", repo, number)
        return "retry", "", False


def queue_jobs(service: Any, repo: str, number: int | str, since: str) -> Jobs | None:
    """The jobs of the pull request's newest merge-queue run (GitHub Actions `merge_group` runs on its
    `gh-readonly-queue/<base>/pr-<number>-<sha>` branch) since `since`: [{name, conclusion, url}], or None when
    GitHub will not say (another CI, no Actions permission, GitHub down)."""
    try:
        token: str = service.mint([repo], {"actions": "read", "metadata": "read"})[0]
        headers = {"Authorization": "Bearer " + token}
        path = f"/repos/{quote(repo, safe='/')}/actions/runs"
        response = service._call(
            "GET",
            path,
            headers=headers,
            params={"event": "merge_group", "per_page": 100, "created": ">=" + since[:19] + "Z"},
        )
        if response.status_code != 200:
            return None
        branch = re.compile(rf"^gh-readonly-queue/.+/pr-{int(number)}-[0-9a-f]+$")
        runs: list[Row] = [
            r
            for r in _rows(_row(response.json()).get("workflow_runs"))
            if branch.match(str(r.get("head_branch") or ""))
        ]
        if not runs:
            return None
        newest: str = max(runs, key=lambda r: str(r.get("created_at") or ""))["head_sha"]
        jobs: Jobs = []
        for run in (r for r in runs if r.get("head_sha") == newest):
            listed = service._call("GET", f"{path}/{run['id']}/jobs", headers=headers, params={"per_page": 100})
            if listed.status_code != 200:
                return None
            jobs += [
                {
                    "name": str(j.get("name") or ""),
                    "conclusion": str(j.get("conclusion") or ""),
                    "url": j.get("html_url"),
                }
                for j in _rows(_row(listed.json()).get("jobs"))
            ]
        return jobs
    except Exception:
        log.exception("Reading the merge queue run of %s #%s failed", repo, number)
        return None


def real_failure(jobs: Jobs | None) -> bool:
    """Whether a queue run's jobs show the change failing. A cancelled run (superseded, or another entry's failure)
    leaves its legs cancelled and at most a gate job failed; a real failure fails a leg and the gate, or fails a job
    with nothing cancelled. Unknown jobs are not a real failure: the head is armed once more."""
    failed = [j for j in jobs or [] if j["conclusion"] in FAILED]
    cancelled = [j for j in jobs or [] if j["conclusion"] == "cancelled"]
    return len(failed) >= 2 or bool(failed) and not cancelled


def removal_kind(reason: str | None) -> str:
    """conflict, manual, soft, or check (read the queue run's jobs to tell soft from a real failure)."""
    words = str(reason or "").strip().lower()
    if "conflict" in words:
        return "conflict"
    if "manual" in words:
        return "manual"
    return "soft" if words in SOFT_REASONS else "check"


# ----------------------------------------------------------------------------------------------- the manager's task
def _manager(c: sqlite3.Connection, task: Row) -> str:
    """The bot that fixes the card's red or conflicting pull request: its pipeline's manager (the board's, for a
    ticket reviewed outside a board), else the card's own owner."""
    board = F.pr_checks(c, task)
    manager = H.type_manager(c, {"type_id": board[0]["type_id"]} if board else task)
    owner = H.bot_actor(manager) if manager else None
    if owner and H.task_private(c, task) and not H.task_private_readable(c, owner, task):
        owner = None
    return str(owner or task["owner_id"])


def hand_off(
    c: sqlite3.Connection, task: Row, url: str, number: int | str, head: str, what: str, jobs: Iterable[Row] = ()
) -> Row:
    """One task per pull request for the pipeline manager: a red or conflicting PR Tico could not merge. An open one is
    woken with the news instead of a second being filed."""
    owner = _manager(c, task)
    failing = [j for j in jobs if j["conclusion"] in FAILED]
    lines = [
        f"Tico could not merge PR #{number} at head {head[:12]}: {what}.",
        f"Pull request: {url}",
        f"Card: {task['title']} ({task['id']})",
        *(["Failing:"] + [f"- {j['name']}: {j['url'] or 'no log link'}" for j in failing] if failing else []),
        "Fix it on the pull request's branch and push (for a conflict, merge its base branch in). Tico merges it once "
        "the card's checks approve the new head and CI is green there. If it was a flake, enqueue it again at the same "
        "head. Tico closes this task when the pull request merges.",
    ]
    body = "\n".join(lines)
    key = url.lower()
    previous = H._one(
        c, "SELECT detail FROM events WHERE action=? AND target=? ORDER BY rowid DESC LIMIT 1", (FIX_ROUTED, key)
    )
    open_task: Row | None = H.task(c, _row(H._json(previous["detail"], {})).get("task")) if previous else None
    if open_task and open_task["status"] in H.ACTIVE_STATUSES:
        H._wake(c, open_task, open_task["owner_id"], body, {"wake": "merge_fix"}, trigger="pr_checks")
        return open_task
    actor = task["owner_id"] if H.task_private(c, task) else H.KEEPER
    made: Row = H.task_create(
        c, actor, f"Fix PR #{number} for merge"[:150], body, owner, parent_id=task["id"], deduplicate=False, lint=False
    )
    H.event(c, H.KEEPER, FIX_ROUTED, key, {"task": made["id"], "owner": owner, "head": head})
    H._task_event(c, task["id"], H.KEEPER, "merge_handed", None, f"{url}@{head}", what)
    return made


def close_fix(c: sqlite3.Connection, url: str) -> None:
    """The pull request merged: its manager's fix task, if still open, is done."""
    previous = H._one(
        c,
        "SELECT detail FROM events WHERE action=? AND target=? ORDER BY rowid DESC LIMIT 1",
        (FIX_ROUTED, url.lower()),
    )
    found: Row | None = H.task(c, _row(H._json(previous["detail"], {})).get("task")) if previous else None
    if found and found["status"] in H.ACTIVE_STATUSES:
        H.task_close(c, H.KEEPER, found["id"], note="The pull request merged.", strict=False)


# ----------------------------------------------------------------------------------------------- state on the link
def _save(c: sqlite3.Connection, link_id: str, detail: Row) -> None:
    c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link_id))


def removal(c: sqlite3.Connection, task: Row | None, link: Row, detail: Row, reason: str, jobs: Jobs | None) -> None:
    """A head Tico armed left the merge queue for `reason` (not a merge) and is still open there."""
    if task is None:
        return
    arm: Row = detail["merge_arm"]
    head = str(arm["head"])
    kind = removal_kind(reason)
    if kind == "check":
        kind = "failure" if real_failure(jobs) else "soft"
    if kind == "manual":
        arm["stopped"] = True
        H._task_event(c, task["id"], H.KEEPER, "merge_stopped", None, f"{link['url']}@{head}", reason)
    elif kind == "soft" and not arm.get("rearmed"):
        arm.update(rearmed=True, at="")  # due on the next sweep
        H._task_event(c, task["id"], H.KEEPER, "merge_rearm", None, f"{link['url']}@{head}", reason or "unknown")
    else:
        arm["handed"] = True
        what = {
            "conflict": "it conflicts with its base branch",
            "failure": f"the merge queue run failed ({reason or 'unknown'})",
        }.get(kind, f"the merge queue dropped it twice ({reason or 'unknown'})")
        hand_off(c, task, link["url"], link.get("number") or "?", head, what, jobs or ())
    _save(c, link["id"], detail)


def _record(c: sqlite3.Connection, link_id: str, task_id: str, head: str, result: str, error: str, sent: bool) -> None:
    """Write one arming's outcome on the link; a conflict, or the last allowed attempt refused, goes to the manager."""
    found: list[Row] = H._rows(c.execute("SELECT * FROM task_links WHERE id=?", (link_id,)))
    task: Row | None = H.task(c, task_id)
    if not found or not task:
        return
    link = found[0]
    detail: Row = H._json(link["detail"], {}) or {}
    if result in ("moved", "retry") or str(detail.get("head_sha") or "").lower() != head:
        return
    earlier: Row = detail.get("merge_arm") or {}
    arm: Row = {**(earlier if earlier.get("head") == head else {}), "head": head, "at": H.now()}
    if sent:
        arm["arms"] = int(arm.get("arms") or 0) + 1
    if error:
        arm["error"] = error[:500]
    detail["merge_arm"] = arm
    if result == "in_queue":  # the enqueued webhook did not reach Tico; GitHub's own answer stands for it
        detail["merge_queue"] = {"state": "queued", "head": head, "at": H.now()}
    if result in ("queued", "merged") and sent:
        H._task_event(c, task_id, H.KEEPER, "merge_armed", None, f"{link['url']}@{head}", result)
    if result == "conflict" or result == "refused" and arm["arms"] >= MAX_ARMS:
        arm["handed"] = True
        what = "it conflicts with its base branch" if result == "conflict" else f"GitHub refused it: {error}"
        hand_off(c, task, link["url"], link["number"], head, what)
    _save(c, link_id, detail)


def _due(detail: Row, at: str) -> bool:
    """Whether the sweep arms this link now, by its own state only (the card's approval is read after)."""
    head = str(detail.get("head_sha") or "").lower()
    queue: Row = detail.get("merge_queue") or {}
    arm: Row = detail.get("merge_arm") or {}
    if not head or queue.get("verify"):
        return False  # a removal not read yet is settled first (github.refresh_queue_removals)
    if queue.get("state") == "queued" and queue.get("head") == head:
        return False
    if arm.get("head") != head:
        return True
    if arm.get("handed") or arm.get("stopped"):
        return False
    return str(arm.get("at") or "") < str(H.shift(at, minutes=-STALE_MINUTES))


# ----------------------------------------------------------------------------------------------- the sweep
def sweep(service: "GitHubApp | None", *, paced: bool = False) -> list[str]:
    """Arm what is ready, hand what conflicts to the manager, and move merged cards on. Returns the links armed."""
    if service is None:
        return []
    if paced and time.monotonic() - _last_sweep[0] < SWEEP_EVERY_S:
        return []
    _last_sweep[0] = time.monotonic()
    at = str(H.now())
    app_service: Any = service
    store: Any = app_service.store
    with store.transaction() as c:
        moved_on(c)
    with store.read() as c:
        app: Row | None = app_service.row(c)
        if not app:
            return []
        marks = ",".join("?" * len(H.ACTIVE_STATUSES))
        rows: list[Row] = H._rows(
            c.execute(
                "SELECT l.id,l.task_id,l.url,l.repo,l.number,l.mergeable,l.detail FROM task_links l "
                "JOIN tasks t ON t.id=l.task_id JOIN repositories r ON lower(r.full_name)=lower(l.repo) "
                "WHERE l.kind='pr' AND l.state='open' AND l.number IS NOT NULL AND r.reachable=1 "
                f"AND t.owner_id LIKE 'bot:%' AND t.status IN ({marks}) AND lower(l.repo) LIKE ? "
                "AND (l.checks='passing' OR l.mergeable='conflict') "
                "ORDER BY coalesce(l.updated_at,l.created_at),l.id LIMIT ?",
                (*H.ACTIVE_STATUSES, str(app["org"] or "").lower() + "/%", CANDIDATES),
            )
        )
        ready: list[tuple[Row, str]] = []
        conflicts: list[tuple[Row, str]] = []
        for r in rows:
            detail: Row = H._json(r["detail"], {}) or {}
            if not _due(detail, at):
                continue
            task: Row | None = H.task(c, r["task_id"])
            head = str(detail["head_sha"]).lower()
            if r["mergeable"] == "conflict":
                if approved(c, task, r["id"], ignore_conflict=True):
                    conflicts.append((r, head))
            elif approved(c, task, r["id"]):
                ready.append((r, head))
    for r, head in conflicts:
        with store.transaction() as c:
            _record(c, r["id"], r["task_id"], head, "conflict", "", False)
    armed: list[str] = []
    for r, head in ready:
        result, error, sent = arm(app_service, r["repo"], r["number"], head)
        with store.transaction() as c:
            _record(c, r["id"], r["task_id"], head, result, error, sent)
        if result in ("in_queue", "queued", "merged"):
            armed.append(r["id"])
    return armed


def moved_on(c: sqlite3.Connection) -> None:
    """Each bot card whose pull requests all merged goes to its merged step, when it is before it (a merge read on a
    refresh, or a webhook that never came)."""
    V = _verdicts()
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    for (task_id,) in c.execute(
        "SELECT DISTINCT t.id FROM tasks t "
        "JOIN task_links l ON l.task_id=t.id AND l.kind='pr' AND l.state IN ('merged','shipped') "
        "JOIN task_steps s ON s.id=t.step_id "
        "JOIN task_steps m ON m.type_id=t.type_id AND json_extract(m.flow,'$.on_merge')=1 "
        f"WHERE t.owner_id LIKE 'bot:%' AND t.status IN ({marks}) AND s.position<m.position "
        "AND coalesce(l.pr_merged_at,'')>=? "
        "AND NOT EXISTS (SELECT 1 FROM task_links o WHERE o.task_id=t.id AND o.kind='pr' AND o.state IN ('open','draft'))",
        (*H.ACTIVE_STATUSES, H.shift(H.now(), days=-MERGED_DAYS)),
    ).fetchall():
        V.merged(c, H.task(c, task_id))
