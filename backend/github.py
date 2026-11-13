"""GitHub keeps the pull requests attached to a task up to date; it never moves the task.

A task carries its pull request as a link (`tico task link`). A link is information only: opening,
merging or closing the PR, a release tag or a deploy changes the link, never the task's status or
step. A person moves the task. The repository's webhook posts here signed with
`TICO_GITHUB_WEBHOOK_SECRET`; nothing else on this path is trusted:

- a pull request opened, merged or closed -> the link is `open`, `draft`, `merged` or `closed`
- checks                            -> nothing per check; one `pr_checks_done` per head (and result) once every check
                                       on it has finished, which backend/rules.py turns into a wake or a note. Checks
                                       left pending are read afresh from GitHub (`refresh_pending_checks`, and a task
                                       read's refresh): a suite with no check run is not pending
- conflicts and reviews             -> specific owner notices grouped within three minutes; a review or comment that is
                                       not from a person is a note (`pr_review`)
- a link armed with `wake_once`     -> one notice when its checks finish (pass or fail), it gets an
                                       approving or change-requesting review, or a release ships it;
                                       then it disarms. It wakes the owner's turn and changes nothing else. Its checks
                                       result is the head's one `pr_checks_done`, never a second wake.
- a review requested or withdrawn   -> that person is on or off the task in `github_review_role`
- a push to main                    -> the commits are recorded, in order, in `main_pushes`
- a branch moved (a merge, a push)  -> the open pull requests based on it are read for conflicts at once (`base_moved`)

A merge queue (`enqueued`, `dequeued`) sets the link's `merge_queue` state. A removal from the queue for any reason but a
merge is read by the maintenance pass first (`_queue_event`, `refresh_queue_removals`), and wakes the owner once when
the pull request is still open. Nothing else about a queued pull request is news.

The last hop is a fact of the deploy, not of GitHub: the release manifest names the commit the
running code was built from (`release.py` writes it from CI), and `ship_deployed` marks a merged
link `shipped` once its merge commit is at or before that commit on main. Only the repository this
release came from ships that way; a merged link in another repository stays `merged`. A release that
contains the merge and then its revert (a change dropped after QA failed) does not ship it: GitHub is
asked whether the pull request's own lines are still there at the release commit (`change_kept`).
"""

import asyncio
import base64
import hashlib
import hmac
import itertools
import json
import logging
import re
import threading
import time
from typing import Any
from urllib.parse import quote

from fastapi import Request, Response

from . import hubdb as H
from .batch_work import isolated
from .store import Problem
from . import bot_settings as BS
from . import pr_identity as PR

PATH = "/api/v2/github/webhook"  # under /api/v2: the runner hostname routes only that prefix
PR_LINK = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$", re.IGNORECASE)
COMMIT_LINK = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/commit/([0-9a-f]{7,40})/?$", re.IGNORECASE)


def verify(secret, headers, body):
    """The `X-Hub-Signature-256` GitHub sends, checked in constant time. False when unset."""
    if not secret:
        return False
    given = str(headers.get("x-hub-signature-256") or "")
    want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(given, want)


def _links_for(c, url):
    identity = PR.identity(url)
    if not identity:
        return []
    return [
        p
        for p in H._rows(
            c.execute(
                "SELECT * FROM task_links WHERE kind='pr' AND lower(repo)=? AND number=?",
                (f"{identity[1]}/{identity[2]}", identity[3]),
            )
        )
        if PR.same(p, url)
    ]


def _due(c, task, burst):
    # A bot owner's burst goes out on the next flush and the release policy's notice wait coalesces it from then
    # (backend/wake_presets.py); a person's keeps the three-minute burst.
    return H.now() if _bot_owner(task) else burst.get("due") or H.shift(H.now(), seconds=170)


def _save_burst(c, task, burst, **parts):
    from .repositories import save_metadata

    kept = {k: v for k, v in {**burst, **parts, "due": _due(c, task, burst)}.items() if v or k == "due"}
    save_metadata(c, "github-task-wake:" + task["id"], kept)


def queue_wake(c, task, item, batch=None):
    """Keep one bounded, durable burst per task; the scheduler sends it within three minutes.

    `batch` is the kind of nonblocking notice the item is (wake_presets.BATCH_KINDS: review comments, a finished
    request), which the release policy's notice wait holds. A burst stays batchable only while every item in it is;
    one item that is not (a conflict, a result an armed link waits on) makes the whole burst wake at once. The burst
    also carries `done` (a head's finished checks) and `notes` (reviews that are not a person's), each sent as its own
    message so one can never lift or lower another."""
    from .repositories import metadata

    burst = metadata(c, "github-task-wake:" + task["id"])
    items = burst.get("items", [])
    if item not in items:
        items = (items + [item[:1000]])[-50:]
    kind = batch if not burst.get("items") or burst.get("batch") == batch else None
    _save_burst(
        c,
        task,
        {k: v for k, v in burst.items() if k != "batch"},
        items=items,
        **({"batch": kind} if kind else {}),
    )


def queue_note(c, task, item):
    """A review or comment on the pull request that is not from a person: a note for the owner's next run."""
    from .repositories import metadata

    burst = metadata(c, "github-task-wake:" + task["id"])
    notes = burst.get("notes", [])
    if item not in notes:
        notes = (notes + [item[:1000]])[-50:]
    _save_burst(c, task, burst, notes=notes)


def queue_checks_done(c, task, head, result, item, armed):
    """Every check on `head` finished with `result` (pass or fail): the one `pr_checks_done` event for it. `armed` is
    the link's `wake_once`, which the same event answers. A result still waiting to be sent for the same head is
    replaced (a suite GitHub never announced finished late), and keeps an arming it already answered."""
    from .repositories import metadata

    burst = metadata(c, "github-task-wake:" + task["id"])
    older = [d for d in burst.get("done", []) if d["head"] == head]
    done = [d for d in burst.get("done", []) if d["head"] != head]
    armed = armed or any(d.get("armed") for d in older)
    done.append({"head": head, "result": result, "item": item[:1000], **({"armed": True} if armed else {})})
    _save_burst(c, task, burst, done=done[-10:])


def _bot_owner(task):
    return str(task.get("owner_id") or "").startswith("bot:")


def fire_once(detail, item):
    """A link armed with `wake_once` gives this result, once: disarm it in the same write that queues it."""
    if not detail.get("wake_once"):
        return False
    detail.pop("wake_once")
    detail["wake_once_fired"] = {"at": H.now(), "item": item[:500]}
    return True


def end_own_snooze(c, task_id, why):
    """Every check on a head finished: a snooze the owner bot set on its own task is how it waits for them, so it ends
    here, as SYSTEM, and the head's `pr_checks_done` wakes it (stress run, Nov 12: "wakes when its checks finish" slept
    40 minutes past them). A snooze anyone else set stays, and so does the owner's while the task waits on a person:
    those results stay notes (`rules._pr_decision`). When the checks passing was the last thing the stage asked, it is
    nobody's turn: the snooze ends all the same and the task moves on at once when the next stage has room, else it
    waits for room like any other (Nov 12: Tico Team's Build tasks sat for hours, snoozed "until checks pass", with
    their checks passed and Review 1 unlimited). True when it ended."""
    from . import task_engine as E

    row = E.task(c, task_id)
    if not row or not E.own_snooze(row):
        return False
    if E.ready_but_snoozed(c, row):
        if not E.move_on_past_own_snooze(c, task_id, why):
            E.unsnooze(c, task_id, E.SYSTEM, why)
        return True
    if E._turn_of(c, {**row, "snooze": None})[0] != row["owner_id"]:
        return False
    E.unsnooze(c, task_id, E.SYSTEM, why)
    return True


def flush_wakes(c):
    """Send each due PR burst independently; keep a failed or blocked notice for retry. A burst is up to three
    messages: its grouped items, its review notes, and one `pr_checks_done` per head that finished."""
    from . import task_engine as E
    from .repositories import metadata

    sent = []
    for row in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'github-task-wake:%'").fetchall():
        with isolated(c, "github_wake", row["key"], private=True):
            burst = metadata(c, row["key"])
            if burst.get("due", "") > H.now():
                continue
            task = H.task(c, row["key"].split(":", 1)[1])
            live = bool(task and task["status"] == "open")
            if live and task:
                # Through the engine: one keyed wake per part of the burst, a run only on the owner bot's turn.
                at = H.now()
                if burst.get("items"):
                    E.tell_owner(
                        c,
                        task["id"],
                        "\n".join(burst["items"]),
                        f"github:{at}:items",
                        refs={"wake_batch": burst["batch"]} if burst.get("batch") else None,
                        trigger="pr_checks",
                    )
                if burst.get("notes"):  # reviews that are not a person's: a note for the next run
                    E.tell_owner(c, task["id"], "\n".join(burst["notes"]), f"github:{at}:notes", wake=False)
                if burst.get("done"):
                    end_own_snooze(c, task["id"], burst["done"][-1]["item"])
                for done in burst.get("done", []):
                    E.tell_owner(
                        c,
                        task["id"],
                        done["item"],
                        f"github:checks:{done['head']}:{done['result']}:{at}",
                        refs={
                            "pr_head": done["head"],
                            "pr_result": done["result"],
                            **({"pr_armed": True} if done.get("armed") else {}),
                        },
                        trigger="pr_checks",
                    )
            c.execute("DELETE FROM registry_metadata WHERE key=?", (row["key"],))
            if live and task:
                sent.append(task["id"])
    return sent


def _review_role(c, task, payload, role):
    """Who GitHub asks to review is on the task in `role`, matched by the `github` login on the roster.

    A withdrawn request takes the person off; a request GitHub drops because their review arrived
    leaves them on, so the opened and refresh paths only add."""
    from . import task_roles as TRo

    action, role = str(payload.get("action") or ""), TRo.role_name(role)
    if not role or task["status"] in ("done", "closed"):
        return False
    if action in ("review_requested", "review_request_removed"):
        logins = [(payload.get("requested_reviewer") or {}).get("login")]
    elif action in ("opened", "reopened", "ready_for_review", "refresh"):
        logins = [r.get("login") for r in (payload.get("pull_request") or {}).get("requested_reviewers") or []]
    else:
        return False
    from .views import roster

    by_login = {}
    for p in roster(c)["people"]:
        if p.get("github") and not p.get("hidden"):
            by_login.setdefault(p["github"], []).append(p["id"])
    # A login two people share (a roster from before the profile refused duplicates) names nobody.
    matches = [by_login.get(str(login or "").lower()) or [] for login in logins]
    people = [ids[0] for ids in matches if len(ids) == 1]
    people = [H.human_actor(pid) for pid in people if pid and H.human(c, pid)]
    people = [who for who in people if not H.task_private(c, task) or H.task_private_readable(c, who, task)]
    if not people:
        return False
    have = TRo.roles_of(c, task["id"]).get(role, [])
    want = (
        [who for who in have if who not in people]
        if action == "review_request_removed"
        else have + [who for who in people if who not in have]
    )
    if want == have:
        return False
    return TRo.set_roles(
        c,
        H.KEEPER,
        task["id"],
        {role: want},
        mover=True,
        note="GitHub review " + ("request withdrawn" if action == "review_request_removed" else "requested"),
    )


# The branch `tico task worktree add` names: tico/<the task's first 8 id characters>-<title slug>.
WORKTREE_BRANCH = re.compile(r"^tico/[0-9a-f]{8}-")


LIVE_PR_STATES = ("open", "draft")


def _settle_branch_prs(c, task_id, repo, branch, number, state, have):
    """A task keeps one live pull request per worktree branch: two open on one head are a race (a bot's own
    `gh pr create` and Tico's draft in the same second), and the earliest is kept. Later live links on the branch are
    removed, and so is `have` (this pull request's own link) when an earlier live one exists. A closed pull request is
    never newly linked to a branch that already has one. Returns whether this pull request may stay or be linked."""
    others = H._rows(
        c.execute(
            "SELECT id,number,coalesce(state,'open') AS state FROM task_links WHERE task_id=? AND kind='pr' "
            "AND lower(repo)=? AND branch=? AND number<>?",
            (task_id, repo.lower(), branch, number),
        )
    )
    if not others:
        return True
    if state == "closed":
        return bool(have)
    live = [o for o in others if o["state"] in LIVE_PR_STATES]
    if state == "open" and (not have or have["state"] in LIVE_PR_STATES):
        if any(o["number"] < number for o in live):
            if have:
                H.task_unlink(c, H.KEEPER, task_id, have["id"])
            return False
        for o in live:
            H.task_unlink(c, H.KEEPER, task_id, o["id"])
    return True


def register_worktree_pr(c, pr):
    """A pull request whose head is a task worktree's branch in the same repository belongs to that task: its link is
    registered here, so a worktree whose bot never ran `tico task link` still gets the link its release waits on.
    Returns the ids of the links it made; a fork's branch, another organization's repository or a branch no
    worktree holds registers nothing."""
    url = str(pr.get("html_url") or "").split("?")[0].rstrip("/")
    match = PR_LINK.match(url)
    head = pr.get("head") or {}
    branch = str(head.get("ref") or "")
    if not match or not WORKTREE_BRANCH.match(branch):
        return set()
    repo = f"{match.group(1)}/{match.group(2)}"
    # Only a head GitHub names as this same repository: a missing head repository is a deleted fork, not ours.
    if str((head.get("repo") or {}).get("full_name") or "").lower() != repo.lower():
        return set()
    app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    if not app or str(app["org"] or "").lower() != match.group(1).lower():
        return set()
    from .item_sources import github as GS

    made = set()
    number = int(match.group(3))
    state = "merged" if pr.get("merged") else "closed" if pr.get("state") == "closed" else "open"
    for row in H._rows(
        c.execute(
            "SELECT id,task_id,detail FROM task_links WHERE kind='worktree' AND lower(repo)=? AND branch=?",
            (repo.lower(), branch),
        )
    ):
        have = c.execute(
            "SELECT id,coalesce(state,'open') AS state FROM task_links WHERE task_id=? AND kind='pr' "
            "AND lower(repo)=? AND number=?",
            (row["task_id"], repo.lower(), number),
        ).fetchone()
        # In Merge only a person links a new PR, and the links the stage queues stay as they are.
        if (
            not GS.queues_now(c, row["task_id"])
            and _settle_branch_prs(c, row["task_id"], repo, branch, number, state, have)
            and not have
        ):
            link = H.task_link(c, H.KEEPER, row["task_id"], url)
            made.add(link["id"])
            H.event(c, H.KEEPER, "worktree.pr_registered", row["task_id"], {"url": url, "branch": branch})
        # The branch has its pull request now, however it was found, so an earlier failure to open one is over.
        detail = H._json(row["detail"], {}) or {}
        if detail.pop("pr_error", None):
            c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), row["id"]))
    return made


def health_wait_reason(link, detail):
    """The PR condition whose age Health measures, from the already synced link only."""
    if link.get("state") not in ("open", "draft"):
        return ""
    if link.get("mergeable") == "conflict":
        return "conflict"
    reviewed = (
        detail.get("health_reviewed_head") == detail.get("head_sha")
        if "health_reviewed_head" in detail
        else bool(link.get("review_state"))
    )
    if (
        link.get("state") == "open"
        and link.get("mergeable") == "clean"
        and link.get("checks") == "passing"
        and not reviewed
    ):
        return "review"
    return ""


def record_health_wait(link, detail):
    """Keep the first observation through refreshes; a resolved condition clears its clock."""
    reason = health_wait_reason(link, detail)
    old = detail.get("health_wait") or {}
    head = detail.get("head_sha") or ""
    if not reason:
        detail.pop("health_wait", None)
    elif old.get("reason") != reason or reason == "review" and old.get("head") != head:
        detail["health_wait"] = {"reason": reason, "since": H.now(), "head": head}


# GitHub's `dequeued` payload has a required `reason`. Its schema types the field as a plain string and documents no values:
# octokit's webhook schema and GraphQL's RemovedFromMergeQueueEvent.reason are both String, with no enum. The values below
# are the queue merge's as named for this change, not checked against a delivery. Nothing relies on the list being
# complete: any other reason is read again before its owner is woken (refresh_queue_removals).
MERGE_REASONS = ("merge", "already_merged", "merged")  # compared lower-cased


def _queue_event(detail, action, payload, pr, head):
    """A pull request's merge-queue state (`detail["merge_queue"]`): queued on `enqueued`; on `dequeued`, the reason it was
    taken out. A removal that was not a merge is marked `verify`, and the maintenance pass reads the pull request and wakes
    the owner once if it is still open at this head (refresh_queue_removals). The webhook sends nothing itself."""
    if action == "enqueued":
        detail["merge_queue"] = {"state": "queued", "head": head, "at": H.now()}
        return
    reason = str(payload.get("reason") or "").strip()
    before = detail.get("merge_queue") or {}
    if (before.get("state"), before.get("head"), before.get("reason")) == ("dequeued", head, reason):
        return  # a redelivery of the removal already recorded; its read is pending or done
    detail["merge_queue"] = {"state": "dequeued", "reason": reason, "head": head, "at": H.now()}
    if reason.lower() not in MERGE_REASONS and not pr.get("merged"):
        detail["merge_queue"]["verify"] = True


QUEUE_READS_PER_PASS = 20


def refresh_queue_removals(service):
    """The maintenance pass's read of merge-queue removals that were not merges (`_queue_event` marks them `verify`), on open
    pull requests of active tasks. Each is read from GitHub first: one that has merged or closed is no news here (its closed
    event and reconcile say what happened), and one pushed since the removal is not news about that head. An open pull
    request still at the removed head gets the owner's wake once. A read that fails keeps `verify` for the next pass."""
    with service.store.read() as c:
        app = service.row(c)
        if not app:
            return
        marks = ",".join("?" * len(H.ACTIVE_STATUSES))
        rows = H._rows(
            c.execute(
                "SELECT l.id,l.task_id,l.url,l.repo,l.number,l.detail FROM task_links l "
                "JOIN tasks t ON t.id=l.task_id JOIN repositories r ON lower(r.full_name)=lower(l.repo) "
                "WHERE l.kind='pr' AND l.state='open' AND json_extract(l.detail,'$.merge_queue.verify')=1 "
                "AND l.number IS NOT NULL AND r.reachable=1 "
                f"AND lower(l.repo) LIKE ? AND t.status IN ({marks}) "
                "ORDER BY coalesce(l.updated_at,l.created_at),l.id LIMIT ?",
                (str(app["org"] or "").lower() + "/%", *H.ACTIVE_STATUSES, QUEUE_READS_PER_PASS),
            )
        )
    for r in rows:
        try:
            token, _ = service.mint([r["repo"]], {"pull_requests": "read"})
            response = service._call(
                "GET",
                f"/repos/{quote(r['repo'], safe='/')}/pulls/{r['number']}",
                headers={"Authorization": "Bearer " + token},
            )
            if response.status_code not in (200, 404):
                continue  # still marked: the next pass reads it again
            pr = response.json() if response.status_code == 200 else {"state": "closed"}
        except Exception:
            logging.getLogger("tico.github").exception("Queue removal read failed for %s #%s", r["repo"], r["number"])
            continue
        with service.store.transaction() as c:
            _settle_removal(c, r["id"], pr)


CONFLICT_READS_PER_PASS = 30
CONFLICT_UNKNOWN_MINUTES = 5
CONFLICT_STALE_MINUTES = 30
CONFLICT_RETRY_S = 1800  # a read GitHub refused or that failed waits half an hour, not one 10 s pass
# After a base move GitHub answers `mergeable: null` until it has computed the new answer, which the first read starts:
# a PR still unknown is read again after each of these, about a minute in all, then left to the five-minute read.
RECHECK_AFTER_S = (10, 20, 30)
_conflict_retry = {}  # link id -> monotonic time a failed read may be tried again


def base_moved(c, repo, branch, sha="", *, default_branch="", merged_number=None):
    """`branch` of `repo` moved (a pull request merged into it, the merge queue's merge, any push): GitHub sends no event
    for the other pull requests on it, so each open or draft one on an open task is marked to be read on the next pass
    (`detail.recheck`, read by `refresh_conflicts`). A link whose base is not known yet counts as on the default branch.
    One move is one read per pull request: a mark not yet read is left as it is, and so is one already read for the
    same commit (the merge's webhook, the push and Tico's own merge call all report one move). Returns how many links
    were marked."""
    repo, branch = str(repo or ""), str(branch or "")
    if not repo or not branch:
        return 0
    if not default_branch:
        found = c.execute(
            "SELECT default_branch FROM repositories WHERE lower(full_name)=?", (repo.lower(),)
        ).fetchone()
        default_branch = str((found and found[0]) or "")
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    rows = H._rows(
        c.execute(
            "SELECT l.id,l.number,l.detail FROM task_links l JOIN tasks t ON t.id=l.task_id "
            "WHERE l.kind='pr' AND l.state IN ('open','draft') AND lower(l.repo)=? AND l.number IS NOT NULL "
            f"AND t.status IN ({marks}) AND (json_extract(l.detail,'$.base_ref')=? "
            "OR (json_extract(l.detail,'$.base_ref') IS NULL AND ?=?))",
            (repo.lower(), *H.ACTIVE_STATUSES, branch, branch, default_branch),
        )
    )
    now, marked = H.now(), 0
    for link in rows:
        if merged_number is not None and int(link["number"]) == int(merged_number):
            continue
        detail = H._json(link["detail"], {}) or {}
        old = detail.get("recheck") or {}
        if old and (not old.get("tries") or not sha or not old.get("sha") or old["sha"] == sha):
            if sha and not old.get("sha"):
                old["sha"] = sha
                c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
            continue
        detail["recheck"] = {"sha": sha, "at": now, "due": now, "tries": 0}
        c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
        marked += 1
    from .item_sources import github as GS

    # A bot snoozed on the Merge stage may wait for this move ("until #3417 merges"): it is woken now.
    GS.base_moved(c, repo, branch, sha, default_branch=default_branch, merged_number=merged_number)
    return marked


def merged_by_tico(c, repo, number):
    """Tico's own merge call (or its dequeue) found the pull request merged: its base moved (`base_moved`), before the
    merge's webhook says so."""
    found = c.execute(
        "SELECT json_extract(detail,'$.base_ref') FROM task_links WHERE kind='pr' AND lower(repo)=? AND number=? "
        "AND json_extract(detail,'$.base_ref') IS NOT NULL LIMIT 1",
        (str(repo).lower(), int(number)),
    ).fetchone()
    if found:
        return base_moved(c, repo, found[0], merged_number=number)
    default = c.execute("SELECT default_branch FROM repositories WHERE lower(full_name)=?", (str(repo).lower(),))
    branch = (default.fetchone() or [""])[0]
    return base_moved(c, repo, branch, merged_number=number) if branch else 0


def _settle_recheck(c, ids, at, pr):
    """After a base move's read: a known answer (or a PR no longer open) ends the mark; GitHub's `null` (still computing)
    is read again after the next RECHECK_AFTER_S step, and after the last one the mark ends. A link marked again since
    the read (`at` differs: another move) keeps its new mark."""
    known = (
        pr.get("state") != "open" or pr.get("mergeable") is not None or pr.get("mergeable_state") in ("dirty", "clean")
    )
    for link_id in ids:
        found = c.execute("SELECT detail FROM task_links WHERE id=?", (link_id,)).fetchone()
        detail = H._json(found[0] if found else None, {}) or {}
        mark = detail.get("recheck") or {}
        if mark.get("at") != at:
            continue
        tries = int(mark.get("tries") or 0) + 1
        if known or tries > len(RECHECK_AFTER_S):
            detail.pop("recheck", None)
        else:
            detail["recheck"] = {**mark, "tries": tries, "due": H.shift(H.now(), seconds=RECHECK_AFTER_S[tries - 1])}
        c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link_id))


def refresh_conflicts(service: Any) -> int:
    """The maintenance pass's read of open pull requests on open tasks whose mergeability is not known (read again
    after five minutes: GitHub computes it lazily) or has not been read for thirty (a move of the base branch sends no
    pull request event). First come the pull requests whose base branch moved (`base_moved`, due at once, then while
    GitHub still computes): one read per pull request however many links name it. A conflict found wakes the owner
    once per head, as the webhook's would (`pull_request`). At most CONFLICT_READS_PER_PASS pull requests are read per
    pass, the moved ones first, then the longest unread."""
    with service.store.read() as c:
        app = service.row(c)
        if not app:
            return 0
        marks = ",".join("?" * len(H.ACTIVE_STATUSES))
        moved = H._rows(
            c.execute(
                "SELECT l.id,l.url,l.repo,l.number,json_extract(l.detail,'$.recheck.at') AS recheck FROM task_links l "
                "JOIN tasks t ON t.id=l.task_id JOIN repositories r ON lower(r.full_name)=lower(l.repo) "
                "WHERE l.kind='pr' AND l.state IN ('open','draft') AND l.number IS NOT NULL AND r.reachable=1 "
                f"AND lower(l.repo) LIKE ? AND t.status IN ({marks}) "
                "AND json_extract(l.detail,'$.recheck.due')<=? "
                "ORDER BY json_extract(l.detail,'$.recheck.due'),l.id LIMIT ?",
                (str(app["org"] or "").lower() + "/%", *H.ACTIVE_STATUSES, H.now(), 500),
            )
        )
        rows = H._rows(
            c.execute(
                "SELECT l.id,l.url,l.repo,l.number FROM task_links l "
                "JOIN tasks t ON t.id=l.task_id JOIN repositories r ON lower(r.full_name)=lower(l.repo) "
                "WHERE l.kind='pr' AND l.state='open' AND l.number IS NOT NULL AND r.reachable=1 "
                f"AND lower(l.repo) LIKE ? AND t.status IN ({marks}) "
                "AND ((coalesce(l.mergeable,'unknown')='unknown' AND coalesce(l.updated_at,l.created_at)<?) "
                "OR coalesce(l.updated_at,l.created_at)<?) "
                "ORDER BY coalesce(l.updated_at,l.created_at),l.id LIMIT ?",
                (
                    str(app["org"] or "").lower() + "/%",
                    *H.ACTIVE_STATUSES,
                    H.shift(H.now(), minutes=-CONFLICT_UNKNOWN_MINUTES),
                    H.shift(H.now(), minutes=-CONFLICT_STALE_MINUTES),
                    500,  # the backing-off ones are dropped below, so they never fill the pass
                ),
            )
        )
    # A read refreshes the link's updated_at, so it is not due again for half an hour; a failed read writes nothing,
    # so it waits here instead of being read again every pass.
    now = time.monotonic()
    for gone in [k for k, due in _conflict_retry.items() if due <= now]:
        _conflict_retry.pop(gone, None)
    # One read per pull request: the moved ones first, each with the links (and their marks) it settles.
    due = {}
    for r in moved + rows:
        if r["id"] in _conflict_retry:
            continue
        key = (str(r["repo"]).lower(), int(r["number"]))
        entry = due.setdefault(key, {**r, "marks": {}})
        if r.get("recheck"):
            entry["marks"][r["id"]] = r["recheck"]
    read = 0
    for r in list(due.values())[:CONFLICT_READS_PER_PASS]:
        _conflict_retry[r["id"]] = now + CONFLICT_RETRY_S
        pr = None
        try:
            token, _ = service.mint([r["repo"]], {"pull_requests": "read"})
            response = service._call(
                "GET",
                f"/repos/{quote(r['repo'], safe='/')}/pulls/{r['number']}",
                headers={"Authorization": "Bearer " + token},
            )
            if response.status_code == 200:
                pr = response.json()
                pr["html_url"] = r["url"]
        except Exception:
            logging.getLogger("tico.github").exception("Conflict read failed for %s #%s", r["repo"], r["number"])
        if pr is None and not r["marks"]:
            continue
        with service.store.transaction() as c:
            if pr is not None:
                pull_request(
                    c,
                    {"action": "refresh", "pull_request": pr},
                    review_role=service.settings.github_review_role,
                    release_repo=service.settings.release_repo,
                    read_refresh=True,
                )
            # A failed read ends a base move's mark: the half-hour wait above and the stale read take it from there.
            for at in set(r["marks"].values()):
                _settle_recheck(c, [i for i, a in r["marks"].items() if a == at], at, pr or {"state": "unread"})
        if pr is None:
            continue
        _conflict_retry.pop(r["id"], None)
        read += 1
    return read


def _engine_speaks(c, task_id):
    """Whether the task is on a pipeline stage that waits on GitHub (an item requirement of source `github`: a PR
    open, checks, a merge, a release) or queues
    its PRs (backend/item_sources/github.py). There the engine tells the stage's owner of a removal from the queue, a merge
    (the move on) and a close (the standing it leaves unmet): this module's wakes for those would be a second message
    about the same event, in other words. The webhook saves the engine's metadata first (`_item_metadata`), so a merge
    has already moved the task on to the stage after Merge when this is asked."""
    from . import task_engine
    from .item_sources import github as GS
    from . import task_requirements as R

    row = task_engine.task(c, task_id)
    if not row or not row.get("visit"):
        return False
    if GS.queues_prs(row.get("stage")):
        return True
    return any(r["kind"] == "item" and r.get("source") == GS.SOURCE for r in R.for_visit(c, row["visit"]))


def _settle_removal(c, link_id, pr):
    """Clear a removal's `verify`. A pull request still open at the removed head wakes its owner once."""
    found = H._rows(c.execute("SELECT id,task_id,url,number,detail FROM task_links WHERE id=?", (link_id,)))
    if not found:
        return
    link = found[0]
    detail = H._json(link["detail"], {}) or {}
    queue = detail.get("merge_queue") or {}
    if not queue.get("verify"):
        return
    head = str((pr.get("head") or {}).get("sha") or "")
    reason = str(queue.get("reason") or "")
    queue.pop("verify", None)
    detail["merge_queue"] = queue
    c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link_id))
    moved = pr.get("merged") or pr.get("state") != "open" or (bool(queue.get("head")) and head != queue["head"])
    if moved:
        return
    task = H.task(c, link["task_id"])
    H._task_event(c, task["id"], H.KEEPER, "merge_queue_failed", None, f"{link['url']}@{head}", reason)
    if _engine_speaks(c, link["task_id"]):
        return
    queue_wake(
        c,
        task,
        f"Removed from the merge queue ({reason or 'unknown'}) at {(head or 'unknown')[:12]}: fix and merge again",
    )


def pull_request(c, payload, review_role="", release_repo="", *, read_refresh=False):
    pr = payload.get("pull_request") or {}
    url = str(pr.get("html_url") or "").split("?")[0].rstrip("/")
    action = str(payload.get("action") or "")
    registered = set() if read_refresh else register_worktree_pr(c, pr)
    links = _links_for(c, url)
    merged = []  # the links this event merged for the first time (_merge_wakes)
    for link in links:
        task = H.task(c, link["task_id"])
        if not task:
            continue
        state = (
            "merged"
            if pr.get("merged")
            else (
                "closed"
                if pr.get("state") == "closed" or action == "closed"
                else "draft"
                if pr.get("draft") or action == "converted_to_draft"
                else "open"
            )
        )
        if link["state"] == "shipped" and state == "merged":
            state = "shipped"
        detail = H._json(link.get("detail"), {}) or {}
        detail.pop("qa_default_pending", None)
        head_sha = (pr.get("head") or {}).get("sha")
        new_head = head_sha and detail.get("head_sha") != head_sha
        mergeable = "unknown" if action == "synchronize" or new_head else link.get("mergeable") or "unknown"
        if action in ("opened", "reopened", "synchronize", "ready_for_review", "refresh"):
            if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
                mergeable = "conflict"
            elif pr.get("mergeable") is True or pr.get("mergeable_state") == "clean":
                mergeable = "clean"
        match = PR_LINK.match(url)
        # A GitHub event is activity for the stale-PR find; an internal refresh of the PR's state is not.
        activity = H.now() if action != "refresh" else link.get("activity_at")
        c.execute(
            "UPDATE task_links SET state=?,mergeable=?,repo=?,number=?,branch=?,updated_at=?,activity_at=?,"
            "pr_sha=coalesce(?,pr_sha),pr_merged_at=coalesce(?,pr_merged_at) WHERE id=?",
            (
                state,
                mergeable,
                match.group(1) + "/" + match.group(2) if match else None,
                int(match.group(3)) if match else pr.get("number"),
                (pr.get("head") or {}).get("ref"),
                H.now(),
                activity,
                pr.get("merge_commit_sha") if state == "merged" else None,
                pr.get("merged_at") or H.now() if state == "merged" else None,
                link["id"],
            ),
        )
        author = str((pr.get("user") or {}).get("login") or "").lower()
        if author:
            detail["author_login"] = author
        base_ref = str((pr.get("base") or {}).get("ref") or "")
        if base_ref:
            detail["base_ref"] = base_ref  # the branch whose moves send this PR's mergeability to be read (base_moved)
        if pr.get("mergeable_state"):
            detail["mergeable_state"] = pr["mergeable_state"]
        elif new_head:
            detail.pop("mergeable_state", None)
        if action != "refresh":
            detail["tracked"] = True
        if head_sha:
            if detail.get("head_sha") != head_sha:
                detail.pop("checks", None)
                detail.pop("checks_dropped", None)
                detail.pop(SUITE_RUNS, None)
                detail.pop(DROPPED_PENDING, None)
                detail.pop("head_login", None)
                detail.pop("conflict_head", None)
                detail["health_reviewed_head"] = ""
                c.execute("UPDATE task_links SET checks='pending' WHERE id=?", (link["id"],))
            detail["head_sha"] = head_sha
            login = (
                str((payload.get("sender") or {}).get("login") or "").lower()
                if action == "synchronize"
                else str(payload.get("head_login") or "").lower()
            )
            if login:
                if login == detail.get("author_login"):
                    detail["head_login"] = login
                else:
                    detail.pop("head_login", None)
        wake_once = False
        if (
            state == "merged"
            and link["state"] != "merged"
            and match
            and f"{match.group(1)}/{match.group(2)}".lower() != str(release_repo).lower()
        ):
            # Only the release's own repository ships, so a merge elsewhere is the last result an armed link gets.
            wake_once = fire_once(
                detail,
                f"Merged: {link['title']}; it is not in the repository Tico releases, so no shipped wake follows {url}",
            )
        if action in ("enqueued", "dequeued"):
            _queue_event(detail, action, payload, pr, detail.get("head_sha") or "")
        conflict_head = detail.get("head_sha") or "unknown"
        conflict_wake = mergeable == "conflict" and detail.get("conflict_head") != conflict_head
        # A commit that starts conflicting, or stops at the same commit (its base moved again): news on the Merge stage,
        # where it ends a bot's snooze (item_sources/github.py `mergeable_news`). Clean after a new commit is the push's.
        mergeable_moved = conflict_wake or (mergeable == "clean" and detail.get("conflict_head") == conflict_head)
        if mergeable == "conflict":
            detail["conflict_head"] = conflict_head
        elif mergeable == "clean":
            detail.pop("conflict_head", None)
        record_health_wait(
            {**link, "state": state, "mergeable": mergeable, **({"checks": "pending"} if new_head else {})},
            detail,
        )
        # The merge's one wake (_merge_wakes), recorded with the link: a stale event that reopens the link, and a later
        # reading of the same merge, cannot send it again.
        merge_wake = state == "merged" and link["state"] not in ("merged", "shipped") and not detail.get("merged_wake")
        if merge_wake:
            detail["merged_wake"] = True
        c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
        if merge_wake:
            # One wake per merge per task: an armed link's last result rides on the merge's own wake.
            merged.append({**link, "no_ship": wake_once})
            wake_once = False
        current = c.execute("SELECT * FROM task_links WHERE id=?", (link["id"],)).fetchone()
        changed = [
            field for field in ("state", "repo", "number", "branch", "checks") if current[field] != link.get(field)
        ]
        # GitHub computes unknown mergeability after a push, without a corresponding user action.
        if (
            link.get("mergeable") in ("clean", "conflict")
            and mergeable in ("clean", "conflict")
            and link["mergeable"] != mergeable
        ):
            changed.append("mergeable")
        if new_head:
            changed.append("head")
        if changed and not read_refresh:
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), task["id"]))
        item = f"{link['title']} {state}" if state != "closed" else f"{link['title']} was closed without merging"
        if mergeable == "conflict":
            item += ": Merge conflict"
        if changed:
            H._task_event(
                c,
                task["id"],
                H.KEEPER,
                "github",
                f"{link['title']} {link.get('state') or 'unknown'}",
                item,
                f"GitHub: {item} ({', '.join(changed)})",
            )
        if wake_once:
            queue_wake(c, task, detail["wake_once_fired"]["item"])
        if mergeable_moved and match:
            from .item_sources import github as GS

            ref = GS.pr_ref(f"{match.group(1)}/{match.group(2)}", int(match.group(3)))
            if GS.mergeable_news(c, task["id"], ref, str(detail.get("head_sha") or ""), mergeable):
                conflict_wake = False  # that wake says it: no second one from the burst
        if conflict_wake and match:
            from . import pr_conflicts

            # Before Merge a conflict is the bot's that built the PR, not the stage's (a reviewer cannot push): it is
            # woken once per conflicting commit, and the task stays where it is (pr_conflicts).
            pr_key = f"{match.group(1)}/{match.group(2)}#{match.group(3)}".lower()
            if pr_conflicts.tell(c, task["id"], pr_key, conflict_head):
                conflict_wake = False
        closed = state == "closed" and link["state"] != state and link["id"] not in registered
        if closed and not conflict_wake and _engine_speaks(c, task["id"]):
            closed = False
        if closed or conflict_wake:
            # Closed without merging is news; a conflict is a blocker on the bot's own work.
            queue_wake(c, task, item, "completion" if not conflict_wake and not wake_once else None)
        if not read_refresh:
            _review_role(c, task, payload, review_role)
        H.event(c, H.KEEPER, "github.pull_request", task["id"], {"action": action, "url": url})
    _merge_wakes(c, merged, url)
    pr_match = PR_LINK.match(url)
    if action == "closed" and pr_match:
        from .item_sources import github as GS

        # A bot may be snoozed on another task "waiting for #3417 to land": this PR need not be linked to that task.
        repo_name = f"{pr_match.group(1)}/{pr_match.group(2)}"
        GS.named_pr_done(c, repo_name, int(pr_match.group(3)), "merged" if pr.get("merged") else "closed")
    if action == "closed" and pr.get("merged"):
        # The merge moved its base: the other pull requests on it may conflict now, and GitHub tells them nothing.
        repository = payload.get("repository") or {}
        match = PR_LINK.match(url)
        base_moved(
            c,
            repository.get("full_name") or (f"{match.group(1)}/{match.group(2)}" if match else ""),
            (pr.get("base") or {}).get("ref"),
            str(pr.get("merge_commit_sha") or ""),
            default_branch=str(repository.get("default_branch") or ""),
            merged_number=pr.get("number") or (int(match.group(3)) if match else None),
        )
    return {"pr": url, "action": action, "tasks": len(links)}


def _merge_wakes(c, links, url):
    """A linked pull request merged: the bot that has the link's task gets one wake, to carry it on. A person's task is
    not woken (the person sees it), nor a task that is not open or is snoozed (`H._wake`), and a task a merge of this
    pull request already woke through another of its links is not woken again (`github-merge-wake:<task>` records the
    pull requests each task has been woken for). The caller passes only a merge's first reading. Other tasks hear
    nothing: a task waits on the tasks it filed, never on another task's pull request."""
    from .repositories import metadata, save_metadata

    match = PR_LINK.match(url)
    pr_key = f"{match.group(1)}/{match.group(2)}#{match.group(3)}".lower() if match else url.lower()
    for link in links:
        row = H.task(c, link["task_id"])
        if not row or not H.is_bot(row["owner_id"]) or row["status"] != "open" or _engine_speaks(c, row["id"]):
            continue
        key = "github-merge-wake:" + row["id"]
        woke = metadata(c, key).get("prs", [])
        if pr_key in woke:
            continue
        save_metadata(c, key, {"prs": [*woke, pr_key]})
        said = f"{link['title']} merged: {url}. Carry this task on: say it is done, or close it."
        if link.get("no_ship"):
            said += " It is not in the repository Tico releases, so no shipped wake follows."
        queue_wake(c, row, said)


def release(c, payload, release_repo=""):
    """A release published: nothing waits on it in General (In a release is a requirement of the Engineering pipeline,
    which comes later). A release of the server's release repository, not a draft or a prerelease (a tag with a
    suffix), is `release.published` for the automations and outgoing webhooks, once: `github-release:<repo>@<tag>`
    records it, so GitHub redelivering the webhook fires nothing. The payload names the release, never a task."""
    from .repositories import metadata, save_metadata

    rel = payload.get("release") or {}
    tag = str(rel.get("tag_name") or "")
    repo = str((payload.get("repository") or {}).get("full_name") or "")
    published = payload.get("action") == "published" and not rel.get("draft")
    action = "published" if published else payload.get("action")
    if published and tag and "-" not in tag and repo and repo.lower() == str(release_repo or "").lower():
        target = f"{repo}@{tag}"
        key = "github-release:" + target.lower()
        if not metadata(c, key):
            save_metadata(c, key, {"at": H.now()})
            facts = {"release": tag, "repository": repo, "url": str(rel.get("html_url") or "")}
            H.hook(c, "release.published", target, lambda: facts, H.KEEPER, revision=0)
    return {"release": tag, "action": action, "woken": []}


def _own_comment(c, task, payload, key, detail):
    login = str(((payload.get(key) or {}).get("user") or {}).get("login") or "").lower()
    if not login:
        return False
    author = str(((payload.get("pull_request") or {}).get("user") or {}).get("login") or "").lower()
    author = author or str(detail.get("author_login") or "").lower()
    head_login = str(detail.get("head_login") or "").lower()
    app = c.execute("SELECT slug FROM github_app LIMIT 1").fetchone()
    config = c.execute(BS.ONE_SQL, (H.actor_id(task["owner_id"]),)).fetchone()
    bot_login = (H._json(config[0], {}) or {}).get("github_login", "") if config else ""
    via_app = (payload.get(key) or {}).get("performed_via_github_app") or {}
    return (
        app
        and str(via_app.get("slug") or "").lower() == str(app[0]).lower()
        or login
        in {
            author,
            str(bot_login).lower(),
            head_login if head_login == author else "",
            (str(app[0]) + "[bot]").lower() if app else "",
        }
    )


def by_person(c, payload, key):
    """Whether a review or comment on a pull request came from a person: a login that is not a GitHub App's (`[bot]`,
    or posted through an App), and not a bot's own `github_login`. Everything else is a bot's, which is news."""
    obj = payload.get(key) or {}
    login = str((obj.get("user") or {}).get("login") or "").lower()
    if not login or login.endswith("[bot]") or obj.get("performed_via_github_app"):
        return False
    return not c.execute(
        f"SELECT 1 FROM bot_config bc {BS.JOIN} WHERE lower(s.github_login)=? LIMIT 1", (login,)
    ).fetchone()


def _suite_key(suite, run):
    """One key per check suite: an App such as GitHub Actions makes one suite per workflow."""
    slug = str((suite.get("app") or run.get("app") or {}).get("slug") or "Checks")
    return ("suite:" + slug[:150] + (f"#{suite['id']}" if suite.get("id") else ""))[:200]


def _check_state(result):
    """One check's result as the link keeps it: passing, skipped, pending, cancelled or failing. A cancelled (or stale)
    run failed nothing: GitHub cancels the older duplicate of a concurrency group, or someone did by hand."""
    return (
        "passing"
        if result in ("success", "neutral")
        else "skipped"
        if result == "skipped"
        else "cancelled"
        if result in ("cancelled", "stale")
        else ("pending" if result in ("pending", "queued", "in_progress", "requested", "waiting") else "failing")
    )


# A link's `detail` key: {suite key: names of the check runs seen in that suite}, so a cancelled suite is superseded
# only by a newer run of the same workflow (`_counted`). GitHub Actions makes one suite per workflow run, and a check
# suite names no workflow, so its runs' names are what tie two suites to one workflow.
SUITE_RUNS = "suite_runs"
SUITE_RUN_NAMES = 20  # names kept per suite: one shared name is enough to tie two suites


def _add_suite_run(suite_runs, suite_key, name):
    names = suite_runs.setdefault(suite_key, [])
    if name not in names and len(names) < SUITE_RUN_NAMES:
        names.append(name)


def _counted(checks, suite_runs=None):
    """A head's checks without each cancelled suite that a newer suite of the same workflow superseded: a concurrency
    group cancels the older duplicate workflow run when the newer one starts (Nov 12 stress run, I38). The same
    workflow is a newer suite (ids only grow) of the same App with a check run of the same name (`SUITE_RUNS`). A
    suite with no id, or whose runs were never seen, is never superseded: a cancelled workflow is never hidden behind
    another workflow of the same App. Tico's own `tico/merge-ok` is left out too, with the suite it ran in: it is the
    Merge stage's gate, red on every commit the stage did not queue, never a CI result (#395)."""
    from .item_sources.github import MERGE_OK

    suite_runs = suite_runs or {}

    def split(key):
        app, _, sid = key[6:].partition("#")
        return app, int(sid) if sid.isdigit() else 0

    def superseded(key):
        app, sid = split(key)
        names = set(suite_runs.get(key) or ())
        return bool(sid and names) and any(
            other.startswith("suite:")
            and split(other)[0] == app
            and split(other)[1] > sid
            and names & set(suite_runs.get(other) or ())
            for other in checks
        )

    def gate(key):
        return key == MERGE_OK or set(suite_runs.get(key) or ()) == {MERGE_OK}

    return {
        k: v
        for k, v in checks.items()
        if not gate(k) and not (v == "cancelled" and k.startswith("suite:") and superseded(k))
    }


def _overall(checks, suite_runs=None):
    """A head's checks as one state: failing if any failed, else pending if any is still going, else cancelled if one
    was cancelled with no newer run of its workflow after it (`_counted`), else passing. A head whose every check was
    skipped has not been checked (a draft pull request's run skips every job, and nothing on main requires a check), so
    it reads pending: an all-skipped head never reads green."""
    states = set(_counted(checks, suite_runs).values())
    return (
        "failing"
        if "failing" in states
        else "pending"
        if "pending" in states or states <= {"skipped"}
        else "cancelled"
        if "cancelled" in states
        else "passing"
    )


CHECKS_KEPT = 100


def _kept(checks):
    """What a link stores of a head's checks: at most CHECKS_KEPT keys, every failing one first, then pending, then
    the most recently added. A later webhook reads the stored copy back, so keeping the checks that decide the state
    keeps `_overall` of the copy equal to the head's own state, even when an early failure or a running check is
    among the older keys. That holds only while at most CHECKS_KEPT of a head's checks are not passing: past that
    some are dropped, and a webhook that turns the kept ones green can read the head passing while a dropped one
    still fails."""
    if len(checks) <= CHECKS_KEPT:
        return dict(checks)
    keys = list(checks)
    chosen = set()
    for want in ("failing", "pending", None):
        for k in reversed(keys):
            if len(chosen) >= CHECKS_KEPT:
                break
            if want is None or checks[k] == want:
                chosen.add(k)
    return {k: checks[k] for k in keys if k in chosen}


def _dropped(checks, kept):
    """The worst state of a non-passing check that the stored copy `kept` leaves out, or None. The copy cannot show it,
    so the link keeps this marker (`checks_dropped`) until a fresh read from GitHub (`apply_checks`) stores the head's
    whole list again: the head reads no better than this while the copy's own checks turn green."""
    gone = [
        state for key, state in checks.items() if key not in kept and state not in ("passing", "skipped", "cancelled")
    ]
    return "failing" if "failing" in gone else "pending" if gone else None


def _with_dropped(state, dropped):
    """A head's state as a webhook reads it from the stored copy: the copy's state, or the dropped check's if worse."""
    return "failing" if "failing" in (state, dropped) else "pending" if "pending" in (state, dropped) else state


def _set_suite_runs(detail, suite_runs):
    """Keep the run names of at most `CHECKS_KEPT` suites, the newest added last, and none when there are none. A
    suite not in the stored copy keeps its names too: a run's webhook can come before its suite's own."""
    kept_runs = dict(list(suite_runs.items())[-CHECKS_KEPT:])
    if kept_runs:
        detail[SUITE_RUNS] = kept_runs
    else:
        detail.pop(SUITE_RUNS, None)


def _worst(a, b):
    return "failing" if "failing" in (a, b) else "pending" if "pending" in (a, b) else None


# Whether a check the stored copy leaves out was still going: `checks_dropped` keeps only the worst state, so a running
# check left out beside a dropped failure would read "failing" and could never be seen to finish.
DROPPED_PENDING = "checks_dropped_pending"


def _dropped_pending(checks, kept):
    return any(state == "pending" for key, state in checks.items() if key not in kept)


def _set_dropped(detail, dropped, pending=False):
    if dropped:
        detail["checks_dropped"] = dropped
    else:
        detail.pop("checks_dropped", None)
    if dropped and pending:
        detail[DROPPED_PENDING] = True
    else:
        detail.pop(DROPPED_PENDING, None)


def _still_going(detail):
    """Whether the stored copy says a check on the head was still going: one it kept, or one it left out. Tico's own
    gate is not a check that is going (`_counted`)."""
    return (
        "pending" in _counted(detail.get("checks") or {}, detail.get(SUITE_RUNS)).values()
        or detail.get("checks_dropped") == "pending"
        or bool(detail.get(DROPPED_PENDING))
    )


def _finish(c, task, link, detail, full, checks):
    """Every check on head `full` has finished: the head's one `pr_checks_done` per result. A redelivery or a re-run that
    ends the same way says nothing new, unless the bot armed the link again to hear that re-run. A head whose only
    non-passing check was cancelled (and not superseded, `_counted`) says so: re-run it, there is no code to fix.
    Returns (head, outcome) either way."""
    counted = _counted(checks, detail.get(SUITE_RUNS))

    def named(state):
        return list(
            dict.fromkeys(
                k[6:].split("#")[0] if k.startswith("suite:") else k for k, v in counted.items() if v == state
            )
        )

    failed, cancelled = named("failing"), named("cancelled")
    outcome = "fail" if failed else "cancelled" if cancelled else "pass"
    finished = detail.get("checks_done", [])
    if f"{full}:{outcome}" in finished and not detail.get("wake_once"):
        return full, outcome
    detail["checks_done"] = list(dict.fromkeys(finished + [f"{full}:{outcome}"]))[-10:]
    sha = full[:12]
    said = (
        "failed (" + ", ".join(failed) + ")"
        if failed
        else "cancelled, not failed (" + ", ".join(cancelled) + "); re-run them, there is no code to fix"
        if cancelled
        else "passed"
    )
    done = f"Checks finished on {link['title']}: {said} {link['url']}/checks" + (f" at {sha}" if sha else "")
    armed = fire_once(detail, done)
    queue_checks_done(c, task, full, outcome, done, armed)
    return full, outcome


class ReadChecks(dict):
    """`read_checks`' answer: {check key: state}, and `suite_runs` ({suite key: its check runs' names}, `SUITE_RUNS`)."""

    def __init__(self, checks, suite_runs):
        super().__init__(checks)
        self.suite_runs = suite_runs


# A check suite GitHub queued for a head that has had no check run for this long never started: an installed App (a
# deploy or monitoring service) gets a suite on every push whether or not it runs anything, and no webhook ever says
# it finished (Dev 646: tico-team-hub, tico-team-bots, vercel and sentry suites queued with zero runs on every head).
STUCK_SUITE_MINUTES = 10


def read_checks(service, headers, repo, head):
    """The checks on `head` as GitHub has them now, keyed as `pr_signal` keys them: each check run (the latest per name),
    each commit status (the latest per context) and each check suite, except a suite not completed with no check run
    that has sat `STUCK_SUITE_MINUTES`, which is not pending (a completed one keeps its conclusion, failed or not). None when GitHub's answer is incomplete or there is nothing on the head
    (a fresh read never turns a head with no checks green)."""
    base = f"/repos/{quote(repo, safe='/')}/commits/{quote(head, safe='')}"

    def pages(path, field):
        rows = []
        for page in range(1, 11):
            response = service._call("GET", base + path, headers=headers, params={"per_page": 100, "page": page})
            if response.status_code != 200:
                return None
            got = response.json().get(field) or []
            rows.extend(got)
            if len(got) < 100:
                return rows
        return None

    suites, runs, statuses = (
        pages("/check-suites", "check_suites"),
        pages("/check-runs", "check_runs"),
        pages("/status", "statuses"),
    )
    if suites is None or runs is None or statuses is None:
        return None
    stuck_before = H.parse_ts(H.shift(H.now(), minutes=-STUCK_SUITE_MINUTES))
    checks = {}
    for suite in suites:
        created = H.parse_ts(suite.get("created_at"))
        if (
            suite.get("status") != "completed"
            and not suite.get("latest_check_runs_count")
            and created
            and created <= stuck_before
        ):
            continue
        checks[_suite_key(suite, suite)] = _check_state(suite.get("conclusion") or suite.get("status") or "pending")
    suite_runs = {}
    for run in runs:
        name = str(run.get("name") or "Checks")[:200]
        checks[name] = _check_state(run.get("conclusion") or run.get("status") or "pending")
        if (run.get("check_suite") or {}).get("id"):
            _add_suite_run(suite_runs, _suite_key(run["check_suite"], run), name)
    for status in statuses:
        checks[str(status.get("context") or "Checks")[:200]] = _check_state(status.get("state") or "pending")
    return ReadChecks(checks, suite_runs) if checks else None


def _fresh_checks(service, repo, head):
    token, _ = service.mint([repo], {"checks": "read", "statuses": "read"})
    return read_checks(service, {"Authorization": "Bearer " + token}, repo, head)


PENDING_READ_EVERY_S = 600  # one fresh read per pending link per ten minutes
PENDING_READS_PER_PASS = 10
_pending_reads = {}  # link id -> monotonic time its next read is due
_pending_lock = threading.Lock()


def refresh_pending_checks(service):
    """The background pass's read of checks that have sat pending with no word from GitHub for `STUCK_SUITE_MINUTES`,
    on open pull requests of active tasks: each is rebuilt from GitHub's current lists (`read_checks`, `apply_checks`),
    so a suite that never started stops holding a green head (Dev 646). A failed head whose stored copy still has a
    pending check is read too: a failure outranks pending, so such a link reads "failing", but the pending suite keeps
    the webhook from finishing the head, and its failed-check hand-off never came (Nov 8, PR 3055). A link whose stored
    copy dropped a non-passing check (`checks_dropped`) is read the same way, so the copy's green cannot stand. Reads outside the write, and applies
    a read only to a link nothing wrote while it ran (a webhook's newer check stands); the longest-quiet links first.
    Returns the ids of pending links that left pending."""
    with service.store.read() as c:
        app = service.row(c)
        if not app:
            return []
        marks = ",".join("?" * len(H.ACTIVE_STATUSES))
        rows = H._rows(
            c.execute(
                "SELECT l.id,l.repo,l.number,l.updated_at,l.checks,json_extract(l.detail,'$.head_sha') AS head "
                "FROM task_links l "
                "JOIN tasks t ON t.id=l.task_id JOIN repositories r ON lower(r.full_name)=lower(l.repo) "
                "WHERE l.kind='pr' AND l.state='open' AND (l.checks='pending' "
                "OR json_extract(l.detail,'$.checks_dropped') IS NOT NULL "
                "OR l.checks='failing' AND EXISTS (SELECT 1 FROM json_each(l.detail,'$.checks') WHERE value='pending')) "
                "AND l.number IS NOT NULL AND r.reachable=1 "
                f"AND lower(l.repo) LIKE ? AND t.status IN ({marks}) AND coalesce(l.updated_at,l.created_at)<? "
                "ORDER BY coalesce(l.updated_at,l.created_at),l.id LIMIT 200",
                (
                    str(app["org"] or "").lower() + "/%",
                    *H.ACTIVE_STATUSES,
                    H.shift(H.now(), minutes=-STUCK_SUITE_MINUTES),
                ),
            )
        )
    now = time.monotonic()
    with _pending_lock:
        for gone in set(_pending_reads) - {r["id"] for r in rows}:
            _pending_reads.pop(gone, None)
        due = [r for r in rows if r["head"] and _pending_reads.get(r["id"], 0) <= now][:PENDING_READS_PER_PASS]
        for r in due:
            _pending_reads[r["id"]] = now + PENDING_READ_EVERY_S
    left, read = [], {}
    for r in due:
        key = (r["repo"].lower(), r["head"])
        try:
            if key not in read:
                read[key] = _fresh_checks(service, r["repo"], r["head"])
            if read[key]:
                with service.store.transaction() as c:
                    state = apply_checks(c, r["id"], r["head"], read[key], since=r["updated_at"])
                    if r["checks"] == "pending" and state not in (None, "pending"):
                        left.append(r["id"])
        except Exception:
            logging.getLogger("tico.github").exception("Checks read failed for %s #%s", r["repo"], r["number"])
    return left


def apply_checks(c, link_id, head, checks, since=None):
    """Rebuild a PR link's checks from a fresh read (`read_checks`) at the head the link is at now. Checks that leave
    pending this way finish the head as a webhook's last check would (`_finish`).
    `since` is the link's updated stamp when the read began: a link written after it (a webhook during the read) keeps
    its checks, which are newer than the read. A failed head whose stored copy had a pending check and whose read has
    none finishes too, though it read "failing" before and after: the pending check is what kept it from finishing.
    Returns the link's checks state, or None when the read no longer applies (another head, a closed PR, a write since)."""
    row = c.execute("SELECT * FROM task_links WHERE id=?", (link_id,)).fetchone()
    link = dict(row) if row else None
    detail = H._json(link.get("detail"), {}) or {} if link else {}
    task = link and H.task(c, link["task_id"])
    if not checks or link is None or not task or link.get("state") != "open" or detail.get("head_sha") != head:
        return None
    if since is not None and link.get("updated_at") != since:
        return None
    # The state and the failures named come from the whole read; only the stored copy is cut.
    suite_runs = getattr(checks, "suite_runs", None)
    if suite_runs is not None:
        suite_runs = {k: v for k, v in suite_runs.items() if k in checks}
    else:
        suite_runs = detail.get(SUITE_RUNS) or {}
    state, kept = _overall(checks, suite_runs), _kept(checks)
    dropped, dropped_pending = _dropped(checks, kept), _dropped_pending(checks, kept)
    # The stored copy still held a running check (kept or left out): a read that finds none going finishes the head
    # even when its state stays the same.
    counted = _counted(checks, suite_runs)
    held = _still_going(detail) and "pending" not in counted.values()
    if (
        detail.get("checks") == kept
        and link.get("checks") == state
        and detail.get("checks_dropped") == dropped
        and bool(detail.get(DROPPED_PENDING)) == dropped_pending
        and (detail.get(SUITE_RUNS) or {}) == suite_runs
        and not held
    ):
        return state
    detail["checks"] = kept
    _set_suite_runs(detail, suite_runs)
    _set_dropped(detail, dropped, dropped_pending)
    changed = state != link.get("checks")
    # As the webhook: a head finishes only once no check on it is still going, whatever has failed already.
    finished = state != "pending" and "pending" not in counted.values() and (changed or held)
    if finished:
        _finish(c, task, link, detail, head, checks)
    record_health_wait({**link, "checks": state}, detail)
    c.execute(
        "UPDATE task_links SET checks=?,detail=?,updated_at=? WHERE id=?", (state, json.dumps(detail), H.now(), link_id)
    )
    if changed:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), task["id"]))
        H.event(c, H.KEEPER, "github.checks_read", task["id"], {"url": link["url"], "head": head, "checks": state})
    return state


def _own_app(c, suite):
    """Whether a check suite is the company GitHub App's own, the one that posts `tico/merge-ok`."""
    slug = str((suite.get("app") or {}).get("slug") or "").lower()
    app = c.execute("SELECT slug FROM github_app LIMIT 1").fetchone()
    return bool(slug and app and str(app["slug"]).lower() == slug)


def pr_signal(c, event, payload):
    """Checks, commit statuses, reviews and review comments update every attached PR."""
    repo = str((payload.get("repository") or {}).get("full_name") or "")
    pr = payload.get("pull_request") or {}
    signal = payload.get("check_run") or payload.get("check_suite") or payload.get("workflow_run") or payload
    if event == "workflow_run" and (payload.get("action") != "completed" or not payload.get("workflow_run")):
        return {"tasks": 0}  # only a finished workflow run is news; a started one is a check still going
    prs = signal.get("pull_requests") or ([pr] if pr else [])

    def base_repo(p):
        base = (p.get("base") or {}).get("repo") or {}
        return base.get("full_name") or (
            repo.rsplit("/", 1)[0] + "/" + base["name"] if base.get("name") and "/" in repo else repo
        )

    urls = {
        p.get("html_url") or f"https://github.com/{base_repo(p)}/pull/{p['number']}"
        for p in prs
        if p.get("number") or p.get("html_url")
    }
    links = []
    for url in urls:
        links.extend(_links_for(c, url))
    if event == "status" or event in ("check_run", "check_suite", "workflow_run") and not prs:
        # Fork checks and commit statuses can omit the PR list.
        sha = payload.get("sha") if event == "status" else signal.get("head_sha")
        # A workflow run on a push to main has no PR list either: only an open PR's head is its news, never a
        # merged PR whose head is now main's.
        still_open = " AND state IN ('open','draft')" if event == "workflow_run" else ""
        links = H._rows(
            c.execute(
                "SELECT * FROM task_links WHERE kind='pr' AND lower(repo)=? AND json_extract(detail,'$.head_sha')=?"
                + still_open,
                (repo.lower(), sha),
            )
        )
    for link in links:
        task = H.task(c, link["task_id"])
        if not task:
            continue
        detail = H._json(link.get("detail"), {}) or {}
        fields = {}
        label = link["title"]
        wake, batch, who_key = False, None, ""
        if event in ("check_run", "check_suite", "status", "workflow_run"):
            if signal.get("head_sha") and detail.get("head_sha") and signal["head_sha"] != detail["head_sha"]:
                continue
            state = _check_state(signal.get("conclusion") or signal.get("state") or "pending")
            checks = detail.get("checks", {})
            name = (
                _suite_key(signal, signal)
                if event == "check_suite"
                else ("workflow:" + str(signal.get("name") or "run"))[:200]
                if event == "workflow_run"
                else str(signal.get("name") or signal.get("context") or "Checks")[:200]
            )
            left_pending = checks.get(name) == "pending" and state != "pending"
            checks[name] = state
            own = (signal.get("check_suite") or {}) if event == "check_run" else {}
            if (
                own
                and own.get("status") != "completed"
                and (signal.get("status") != "completed" or checks.get(_suite_key(own, signal), "pending") == "pending")
            ):
                # A run says its suite is still running even when that suite's `requested` never came here. A run
                # that is still going reopens it (a re-run keeps the suite id); a finished run delivered after its
                # suite completed is a stale snapshot and does not.
                checks[_suite_key(own, signal)] = "pending"
            suite_runs = detail.get(SUITE_RUNS) or {}
            if own.get("id"):
                _add_suite_run(suite_runs, _suite_key(own, signal), name)
            if event == "check_suite" and _own_app(c, signal):
                # The company App posts only `tico/merge-ok`: its suite is the gate's even before its run is seen.
                from .item_sources.github import MERGE_OK

                _add_suite_run(suite_runs, name, MERGE_OK)
            kept = _kept(checks)
            dropped = _worst(detail.get("checks_dropped"), _dropped(checks, kept))
            pending = bool(detail.get(DROPPED_PENDING)) or _dropped_pending(checks, kept)
            detail["checks"] = kept
            _set_dropped(detail, dropped, pending)
            _set_suite_runs(detail, suite_runs)
            fields["checks"] = _with_dropped(_overall(checks, suite_runs), dropped)
            # One check finishing writes nothing (rule `pr_check`). A suite completes once all of its runs have; a
            # single run or commit status may be one of many. A run or status that finishes after a suite already has
            # can be the last one, so it counts too.
            # Tico's own gate and its App's suite are not CI (`_counted`): one queued that never runs holds nothing.
            counted = _counted(checks, suite_runs)
            suite_done = any(k.startswith("suite:") and v != "pending" for k, v in counted.items())
            if (
                (
                    event == "check_suite"
                    and signal.get("status") == "completed"
                    and state != "pending"
                    or event == "workflow_run"
                    and state != "pending"
                    or event not in ("check_suite", "workflow_run")
                    and left_pending
                    and (suite_done or not any(k.startswith("suite:") for k in counted))
                )
                and "pending" not in counted.values()
                and not set(counted.values()) <= {"skipped"}
                # A copy that dropped a check cannot finish the head green: only its own failure finishes it.
                and (not dropped or "failing" in counted.values())
                # A check the copy left out may still be going: the next fresh read (apply_checks) finishes the head.
                and not _still_going(detail)
            ):
                # GitHub sends a suite's `requested` only to its own App, so a single run or status still pending
                # is often the only sign that another service's checks are not done.
                full = str(signal.get("head_sha") or payload.get("sha") or detail.get("head_sha") or "")
                _finish(c, task, link, detail, full, checks)
        elif event == "pull_request_review":
            state = str((payload.get("review") or {}).get("state") or "").lower()
            if payload.get("action") == "dismissed":
                state = "commented"
            if state not in ("approved", "changes_requested", "commented"):
                continue
            review = payload.get("review") or {}
            review_id = str(review.get("id") or "")
            reviews = detail.get("reviews", {})
            signature = str(payload.get("action") or "") + ":" + state
            fresh = reviews.get(review_id) != signature if review_id else link.get("review_state") != state
            if review_id:
                reviews[review_id] = signature
                detail["reviews"] = dict(list(reviews.items())[-100:])
            fields["review_state"] = state
            if payload.get("action") != "dismissed" and not _own_comment(c, task, payload, "review", detail):
                detail["health_reviewed_head"] = review.get("commit_id") or detail.get("head_sha") or ""
            wake = (
                fresh
                and payload.get("action") != "dismissed"
                and (
                    state == "changes_requested"
                    or state == "commented"
                    and not _own_comment(c, task, payload, "review", detail)
                )
            )
            item = f"Review {state.replace('_', ' ')} on {label}"
            batch, who_key = "review", "review"
            if fresh and payload.get("action") == "submitted" and state in ("approved", "changes_requested"):
                who = str((review.get("user") or {}).get("login") or "")
                done = item + (f" by {who}" if who else "") + " " + str(review.get("html_url") or link["url"])
                if fire_once(detail, done):
                    wake, item, batch = True, done, None
        else:
            action = payload.get("action")
            if action not in ("created", "deleted"):
                continue
            comment_id = str((payload.get("comment") or {}).get("id") or "")
            comments = detail.get("comments", {})
            if comment_id:
                if action == "created" and comments.get(comment_id) == "created":
                    continue
                if action == "deleted" and comments.get(comment_id) == "deleted":
                    continue
                comments[comment_id] = action
                detail["comments"] = dict(list(comments.items())[-100:])
            fields["pending_comments"] = max(
                0, (link.get("pending_comments") or 0) + (1 if action == "created" else -1)
            )
            wake = action == "created" and not _own_comment(c, task, payload, "comment", detail)
            item = f"{fields['pending_comments']} review comments on {label}"
            batch, who_key = "review", "comment"
        if event in ("pull_request_review", "pull_request_review_comment"):
            fields["activity_at"] = H.now()  # the stale-PR find's clock (automation_actions FIND)
        changed = any(value != link.get(field) for field, value in fields.items())
        record_health_wait({**link, **fields}, detail)
        fields.update(detail=json.dumps(detail), updated_at=H.now())
        if changed:
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), task["id"]))
        c.execute(
            "UPDATE task_links SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
            (*fields.values(), link["id"]),
        )
        if wake and batch == "review" and not by_person(c, payload, who_key):
            queue_note(c, task, item)  # a bot's or an App's review is news for the next run, not a wake
        elif wake:
            queue_wake(c, task, item, batch)
    return {"tasks": len({l["task_id"] for l in links})}


def refresh_task_prs(service, task_id):
    """Schedule a bounded refresh; task reads always use the last known state."""
    # A task read serves stored PR state. A background inventory may hold this mutex
    # while reading a large checkout set; scheduling a refresh must never wait for it.
    if not service.lock.acquire(blocking=False):
        return
    try:
        if getattr(service, "pr_refresh_running", False):
            return
        cache = getattr(service, "pr_refresh_cache", {})
        cached = [
            repo + "#" + str(number)
            for (repo, number), entry in cache.items()
            if entry.get("next", 0) > time.monotonic()
        ]
        with service.store.read() as c:
            app = service.row(c)
            if not app:
                return
            sql = (
                "SELECT l.* FROM task_links l JOIN repositories r ON lower(r.full_name)=lower(l.repo) "
                "WHERE l.task_id=? AND l.kind='pr' AND l.number IS NOT NULL AND r.reachable=1 "
                "AND lower(l.repo) LIKE ?"
            )
            args = [task_id, str(app["org"] or "").lower() + "/%"]
            if cached:
                sql += " AND (lower(l.repo)||'#'||l.number) NOT IN (" + ",".join("?" * len(cached)) + ")"
                args.extend(cached)
            links = H._rows(c.execute(sql + " ORDER BY coalesce(l.updated_at,l.created_at),l.id LIMIT 20", args))
        due = list({(l["repo"].lower(), l["number"]): l for l in links}.values())
        if not due:
            return
        service.pr_refresh_running = True
        service.pr_refresh_cache = cache
    finally:
        service.lock.release()

    def refresh():
        try:
            for link in due:
                key = (link["repo"].lower(), link["number"])
                success = False
                try:
                    token, _ = service.mint([link["repo"]], {"pull_requests": "read", "contents": "read"})
                    response = service._call(
                        "GET",
                        f"/repos/{quote(link['repo'], safe='/')}/pulls/{link['number']}",
                        headers={"Authorization": "Bearer " + token},
                    )
                    if response.status_code == 200:
                        pr = response.json()
                        pr["html_url"] = link["url"]
                        head = (pr.get("head") or {}).get("sha")
                        head_login = None
                        if head:
                            try:
                                commit_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name") or link[
                                    "repo"
                                ]
                                commit = service._call(
                                    "GET",
                                    f"/repos/{quote(commit_repo, safe='/')}/commits/{quote(head, safe='')}",
                                    headers={"Authorization": "Bearer " + token},
                                )
                                if commit.status_code == 200:
                                    data = commit.json()
                                    head_login = str(
                                        (data.get("author") or {}).get("login")
                                        or (data.get("committer") or {}).get("login")
                                        or ""
                                    ).lower()
                            except Exception:
                                logging.getLogger("tico.github").debug("Head identity unavailable for %s #%s", *key)
                        # Pending checks, and a copy that dropped a check, are read afresh: a suite that never starts sends
                        # no webhook (Dev 646), and a dropped check's own webhook cannot say the others are green. So is
                        # a failing link that still holds a running check: this refresh moves the link's updated_at, so
                        # task reads every few minutes would keep refresh_pending_checks from ever reading it (PR 3055).
                        checks = None
                        stored = H._json(link.get("detail"), {}) or {}
                        dropped = stored.get("checks_dropped")
                        if (
                            head
                            and (link.get("checks") == "pending" or dropped or _still_going(stored))
                            and pr.get("state") == "open"
                        ):
                            try:
                                checks = _fresh_checks(service, link["repo"], head)
                            except Exception:
                                logging.getLogger("tico.github").debug("Checks unavailable for %s #%s", *key)
                        with service.store.transaction() as c:
                            current = c.execute(
                                "SELECT updated_at FROM task_links WHERE id=?", (link["id"],)
                            ).fetchone()
                            if not current or current[0] != link.get("updated_at"):
                                success = True
                                continue
                            pull_request(
                                c,
                                {"action": "refresh", "pull_request": pr, "head_login": head_login},
                                review_role=service.settings.github_review_role,
                                release_repo=service.settings.release_repo,
                                read_refresh=True,
                            )
                            if checks:
                                apply_checks(c, link["id"], head, checks)
                        success = True
                except Exception:
                    logging.getLogger("tico.github").exception("PR refresh failed for %s #%s", *key)
                finally:
                    with service.lock:
                        failures = 0 if success else min(cache.get(key, {}).get("failures", 0) + 1, 5)
                        if key not in cache and len(cache) >= 512:
                            cache.pop(next(iter(cache)))
                        cache[key] = {
                            "failures": failures,
                            "next": time.monotonic() + (180 if success else min(300 * 2 ** (failures - 1), 3600)),
                        }
        finally:
            with service.lock:
                service.pr_refresh_running = False

    threading.Thread(target=refresh, name="tico-pr-refresh", daemon=True).start()


def _worktree_detail(service, repo, branch, change):
    """Apply `change(detail)` to every worktree link on this repository branch, in its own write."""
    with service.store.transaction() as c:
        for row in H._rows(
            c.execute(
                "SELECT id,detail FROM task_links WHERE kind='worktree' AND lower(repo)=? AND branch=?",
                (repo.lower(), branch),
            )
        ):
            detail = H._json(row["detail"], {}) or {}
            change(detail)
            c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), row["id"]))


def _pr_error(service, repo, branch, text):
    """A failure the task and the cap view show on the branch's worktrees until its pull request is registered
    (register_worktree_pr clears it); the next pass tries again."""
    _worktree_detail(service, repo, branch, lambda detail: detail.update(pr_error=text))


def _open_draft(service, repo, branch, worktree):
    """A pushed worktree branch with no pull request gets a draft one, so its work is never an orphan. Any failure,
    before GitHub answers too, is recorded with a reason that carries no GitHub text, and the next pass tries again.
    Returns the created pull request as a one-item list, or an empty one."""
    failed = f"Opening a draft pull request for {branch} failed"
    try:
        token = service.mint([repo], {"pull_requests": "write", "contents": "read"})[0]
    except Exception:
        _pr_error(service, repo, branch, failed + " (no GitHub token for it)")
        return []
    try:
        response = service._call(
            "POST",
            f"/repos/{quote(repo, safe='/')}/pulls",
            headers={"Authorization": "Bearer " + token},
            json={
                "title": (worktree.get("title") or branch)[:200],
                "head": branch,
                "base": worktree.get("base") or "main",
                "draft": True,
                "body": "Opened as a draft by Tico at this task branch's first push. Mark it "
                "ready for review (`gh pr ready`) and edit it (`gh pr edit`) when the "
                "work is.",
            },
        )
    except Exception:
        _pr_error(service, repo, branch, failed + " (GitHub did not answer)")
        return []
    if response.status_code != 201:
        _pr_error(service, repo, branch, failed + f" (GitHub {response.status_code})")
        return []
    return [_one_draft(service, repo, branch, response.json(), token)]


def _one_draft(service, repo, branch, mine, token):
    """GitHub accepts two pull requests on one head when both are opened in the same moment (a bot's own
    `gh pr create` racing the draft above). The branch's open pull requests are read again right after: the earliest
    on the same base is kept, and each later one is unlinked from its tasks and then closed with a note, so its close
    is no news to anyone. Returns the kept pull request; a read that fails keeps the new draft."""
    number = mine.get("number")
    if not isinstance(number, int):
        return mine
    auth = {"Authorization": "Bearer " + token}
    path = f"/repos/{quote(repo, safe='/')}"
    try:
        response = service._call(
            "GET",
            path + "/pulls",
            headers=auth,
            params={"head": repo.split("/")[0] + ":" + branch, "state": "open", "per_page": 10},
        )
        listed = response.json() if response.status_code == 200 else []
    except (Problem, ValueError):  # GitHub unreachable, or an answer that is not JSON
        logging.getLogger("tico.github").warning("Reading %s %s after its draft failed", repo, branch)
        return mine
    base = (mine.get("base") or {}).get("ref")
    same = {
        pr["number"]: pr
        for pr in (listed if isinstance(listed, list) else [])
        if isinstance(pr, dict)
        and isinstance(pr.get("number"), int)
        and (pr.get("head") or {}).get("ref") == branch
        and (pr.get("base") or {}).get("ref") == base
    }
    same[number] = mine
    kept = same[min(same)]
    later = sorted(n for n in same if n != kept["number"])
    if not later:
        return mine
    with service.store.transaction() as c:
        for row in H._rows(
            c.execute(
                "SELECT id,task_id FROM task_links WHERE kind='pr' AND lower(repo)=? AND number IN ("
                + ",".join("?" * len(later))
                + ") AND coalesce(state,'open') IN ('open','draft')",
                (repo.lower(), *later),
            )
        ):
            H.task_unlink(c, H.KEEPER, row["task_id"], row["id"])
    for n in later:
        try:
            service._call(
                "POST",
                f"{path}/issues/{n}/comments",
                headers=auth,
                json={"body": f"Closed by Tico as a duplicate: #{kept['number']} is this branch's pull request."},
            )
            service._call("PATCH", f"{path}/pulls/{n}", headers=auth, json={"state": "closed"})
        except Problem:  # GitHub unreachable: the task already keeps only the earliest
            logging.getLogger("tico.github").warning("Closing duplicate %s #%s failed", repo, n)
    logging.getLogger("tico.github").info(
        "Two pull requests opened on %s %s at once: kept #%s, closed %s", repo, branch, kept["number"], later
    )
    return kept


def _remote_work(service, repo, branch, base, auth):
    """How many commits the branch has on GitHub that its base does not: None when GitHub has no such branch, the
    evidence a missed push webhook leaves behind and the line between a task's own work and a branch only moved to
    newer main."""
    response = service._call(
        "GET",
        f"/repos/{quote(repo, safe='/')}/compare/{quote(base, safe='')}...{quote(branch, safe='')}",
        headers=auth,
        params={"per_page": 1},
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"GitHub {response.status_code}")
    return int(response.json().get("ahead_by") or 0)


def _covered(service, repo, branch, prs, auth):
    """The open pull request of the same task that already holds all of this branch's work, by number, or None. A bot
    that rebases a task's pull request from its own worktree pushes the pull request's branch, and its worktree's
    branch can be pushed too, at the same commit: that is no new work, and a draft for it would be the task's second
    pull request. A pull request whose head is not known yet is read."""
    for number, head in sorted(prs.items()):
        if not head:
            response = service._call("GET", f"/repos/{quote(repo, safe='/')}/pulls/{number}", headers=auth)
            head = (response.json().get("head") or {}).get("sha") if response.status_code == 200 else None
        if head and _remote_work(service, repo, branch, head, auth) == 0:
            return number
    return None


def reconcile_worktree_prs(service):
    """The background repair for the pull request links a worktree's release reads. Unfinished links on any task that
    holds a worktree (closed, declined and never-read tasks too) are read again, and a worktree branch with no link is
    looked up on GitHub, so a missed webhook heals without anyone opening the task; a branch with commits of its own
    and no pull request gets its draft. At most one pass every three minutes and 20 items a pass, shared between
    links and branches with the least recently read first, so neither kind can starve the other. A branch is asked
    again after 15 minutes when it has no pull request, or as soon as a new head is pushed or reported."""
    with service.lock:
        now = time.monotonic()
        if (
            getattr(service, "worktree_reconcile_running", False)
            or getattr(service, "worktree_reconcile_next", 0) > now
        ):
            return
        service.worktree_reconcile_next = now + 180
        cache = service.__dict__.setdefault("worktree_reconcile_cache", {})
        with service.store.read() as c:
            app = service.row(c)
            if not app:
                return
            org = str(app["org"] or "").lower() + "/%"
            held = (
                "EXISTS (SELECT 1 FROM repositories r WHERE lower(r.full_name)=lower({0}.repo) AND r.reachable=1) "
                "AND lower({0}.repo) LIKE ?"
            )
            # Both lists are as long as the worktrees the cap allows, so no row limit can leave one unread.
            links = H._rows(
                c.execute(
                    "SELECT p.* FROM task_links p WHERE p.kind='pr' AND p.number IS NOT NULL "
                    "AND coalesce(p.state,'open') NOT IN ('merged','closed','shipped') AND "
                    + held.format("p")
                    + " AND EXISTS (SELECT 1 FROM task_links w WHERE w.task_id=p.task_id AND w.kind='worktree' "
                    "AND coalesce(w.state,'unknown')<>'removed')",
                    (org,),
                )
            )
            branches = {}
            for w in H._rows(
                c.execute(
                    "SELECT w.repo,w.branch,w.detail,CASE WHEN coalesce(t.private,0)=0 THEN t.title END AS title,"
                    "(SELECT r.default_branch FROM repositories r WHERE lower(r.full_name)=lower(w.repo)) AS base,"
                    "(SELECT json_group_array(json_array(p.number,json_extract(p.detail,'$.head_sha'))) FROM task_links p "
                    "WHERE p.task_id=w.task_id AND p.kind='pr' AND lower(p.repo)=lower(w.repo) AND p.number IS NOT NULL "
                    "AND coalesce(p.state,'open') IN ('open','draft')) AS prs "
                    "FROM task_links w JOIN tasks t ON t.id=w.task_id WHERE w.kind='worktree' "
                    "AND coalesce(w.state,'unknown')<>'removed' AND w.branch LIKE 'tico/%' AND "
                    + held.format("w")
                    + " AND NOT EXISTS (SELECT 1 FROM task_links p WHERE p.task_id=w.task_id AND p.kind='pr' "
                    "AND lower(p.repo)=lower(w.repo) AND p.branch=w.branch)",
                    (org,),
                )
            ):
                b = branches.setdefault((w["repo"], w["branch"]), {**w, "title": None, "heads": set(), "prs": {}})
                b["title"] = min(filter(None, (b["title"], w["title"])), default=None)
                b["prs"].update((n, sha) for n, sha in H._json(w["prs"], []) or [])
                b["heads"].update(H.worktree_heads(H._json(w["detail"], {}) or {}))
            branches = list(branches.values())

        def ready(kind, repo, key, heads=None):
            seen = cache.get((kind, repo, key))
            return not seen or seen["next"] <= now or heads is not None and seen.get("heads") != heads

        def last(item):
            return (cache.get(item[:3]) or {}).get("last", 0)

        prs = sorted(
            (("pr", l["repo"], l["number"], l) for l in links if ready("pr", l["repo"], l["number"])), key=last
        )
        heads = {(b["repo"], b["branch"]): sorted(b["heads"]) for b in branches}
        found = sorted(
            (
                ("branch", b["repo"], b["branch"], b)
                for b in branches
                if WORKTREE_BRANCH.match(b["branch"])
                and ready("branch", b["repo"], b["branch"], heads[(b["repo"], b["branch"])])
            ),
            key=last,
        )
        due = [item for pair in itertools.zip_longest(prs, found) for item in pair if item][:20]
        if not due:
            return
        service.worktree_reconcile_running = True

    def reconcile():
        tokens = {}
        try:
            for kind, repo, key, link in due:
                retry = 900
                started = H.now()
                try:
                    if repo.lower() not in tokens:
                        tokens[repo.lower()] = service.mint([repo], {"pull_requests": "read", "contents": "read"})[0]
                    auth = {"Authorization": "Bearer " + tokens[repo.lower()]}
                    path = f"/repos/{quote(repo, safe='/')}/pulls"
                    if kind == "pr":
                        response = service._call("GET", f"{path}/{key}", headers=auth)
                        found = [response.json()] if response.status_code == 200 else []
                        retry = 180 if response.status_code == 200 else 900
                    else:
                        response = service._call(
                            "GET",
                            path,
                            headers=auth,
                            params={"head": repo.split("/")[0] + ":" + key, "state": "all", "per_page": 10},
                        )
                        if response.status_code != 200:
                            raise RuntimeError(f"GitHub {response.status_code}")
                        found = response.json() if isinstance(response.json(), list) else []
                        retry = 180 if found else 900
                        if not found:
                            known = heads[(repo, key)]
                            ahead = _remote_work(service, repo, key, link["base"] or "main", auth)
                            # What GitHub held when asked covers every head known before asking.
                            _worktree_detail(
                                service,
                                repo,
                                key,
                                lambda detail: detail.update(
                                    remote={"ahead": ahead or 0, "absent": ahead is None, "heads": known, "at": H.now()}
                                ),
                            )
                            covered = ahead and _covered(service, repo, key, link["prs"], auth)
                            if covered:
                                logging.getLogger("tico.github").info(
                                    "No draft for %s %s: its work is already in #%s", repo, key, covered
                                )
                            elif ahead:
                                found = _open_draft(service, repo, key, link)
                                retry = 180 if found else 900
                    registering = kind == "branch" and found
                    try:
                        with service.store.transaction() as c:
                            for pr in found:
                                if kind == "pr":
                                    current = c.execute(
                                        "SELECT updated_at FROM task_links WHERE id=?", (link["id"],)
                                    ).fetchone()
                                    if not current or current[0] != link.get("updated_at"):
                                        continue
                                    pr["html_url"] = link["url"]
                                match = PR_LINK.match(str(pr.get("html_url") or "").split("?")[0].rstrip("/"))
                                # A webhook that wrote or registered this pull request while it was being read holds
                                # newer metadata, for every task it is linked to.
                                if (
                                    match
                                    and c.execute(
                                        "SELECT 1 FROM task_links WHERE kind='pr' AND lower(repo)=? AND number=? "
                                        "AND updated_at>=?",
                                        (f"{match.group(1)}/{match.group(2)}".lower(), int(match.group(3)), started),
                                    ).fetchone()
                                ):
                                    continue
                                pull_request(
                                    c,
                                    {"action": "refresh", "pull_request": pr},
                                    review_role=service.settings.github_review_role,
                                    release_repo=service.settings.release_repo,
                                )
                    except Exception:
                        if registering:
                            _pr_error(
                                service,
                                repo,
                                key,
                                f"The pull request for {key} was found but linking it to "
                                "the task failed; the next pass links it",
                            )
                        raise
                except Exception:
                    logging.getLogger("tico.github").exception("Worktree PR reconcile failed for %s %s", repo, key)
                    retry = 900
                with service.lock:
                    if (kind, repo, key) not in cache and len(cache) >= 512:
                        cache.pop(next(iter(cache)))
                    cache[(kind, repo, key)] = {
                        "next": time.monotonic() + retry,
                        "last": time.monotonic(),
                        "heads": heads.get((repo, key)) if kind == "branch" else None,
                    }
        finally:
            with service.lock:
                service.worktree_reconcile_running = False

    threading.Thread(target=reconcile, name="tico-worktree-pr-reconcile", daemon=True).start()


def push(c, payload):
    """A push to the default branch: every commit, in order, so a deploy can be placed on main. A push to any branch
    has the open pull requests based on it read again (`base_moved`)."""
    ref = str(payload.get("ref") or "")
    repo = str((payload.get("repository") or {}).get("full_name") or "")
    default = str((payload.get("repository") or {}).get("default_branch") or "main")
    head_commit = payload.get("head_commit") or {}
    head_sha = str(head_commit.get("id") or payload.get("after") or "")
    login = str(
        (payload.get("sender") or {}).get("login")
        or (head_commit.get("committer") or {}).get("username")
        or (head_commit.get("author") or {}).get("username")
        or ""
    ).lower()
    if head_sha and login:
        for link in H._rows(
            c.execute(
                "SELECT id,detail FROM task_links WHERE kind='pr' AND lower(repo)=? "
                "AND json_extract(detail,'$.head_sha')=?",
                (repo.lower(), head_sha),
            )
        ):
            detail = H._json(link["detail"], {}) or {}
            if login == detail.get("author_login"):
                detail["head_login"] = login
            else:
                detail.pop("head_login", None)
            c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
    branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ""
    if WORKTREE_BRANCH.match(branch) and not payload.get("deleted") and head_sha:
        # A worktree branch's first push asks for its draft pull request; the background pass opens or reuses it
        # (reconcile_worktree_prs), so nothing here calls GitHub inside the write and the push's checks run as usual.
        for link in H._rows(
            c.execute(
                "SELECT id,detail FROM task_links WHERE kind='worktree' AND lower(repo)=? AND branch=?",
                (repo.lower(), branch),
            )
        ):
            detail = H._json(link["detail"], {}) or {}
            detail.update(pushed=head_sha, pushed_at=detail.get("pushed_at") or H.now(), pushed_last_at=H.now())
            detail.pop("stale_push_cleared", None)  # a release names only a stale push the newest push left behind
            c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
    if branch and not payload.get("deleted") and payload.get("after"):
        # Any branch that moved may be some open pull request's base (a merge, the merge queue's, a direct push).
        base_moved(c, repo, branch, str(payload["after"]), default_branch=default)
    if ref != "refs/heads/" + default:
        return {"ref": ref, "commits": 0}
    shas = [str(x.get("id") or "") for x in (payload.get("commits") or [])]
    head = str((payload.get("head_commit") or {}).get("id") or "")
    if head and head not in shas:
        shas.append(head)
    n = 0
    for sha in shas:
        if sha and not c.execute("SELECT 1 FROM main_pushes WHERE sha=?", (sha,)).fetchone():
            c.execute("INSERT INTO main_pushes(sha, repo, pushed_at) VALUES(?,?,?)", (sha, repo, H.now()))
            n += 1
    return {"ref": ref, "commits": n}


DEPLOY_QUERY = (
    "SELECT l.*,p.seq AS merge_seq FROM task_links l JOIN tasks t ON t.id=l.task_id "
    "LEFT JOIN main_pushes p ON p.sha=l.pr_sha "
    "WHERE l.kind='pr' AND l.state='merged' AND l.url LIKE ? ESCAPE '\\' "
    "AND l.pr_sha IS NOT NULL AND t.status NOT IN ('done','closed')"
)


def refresh_deployed_tasks(service):
    """Verify release ancestry outside transactions, then mark the contained links shipped."""
    settings = service.settings
    repo, commit = settings.release_repo.lower(), settings.release_commit
    if settings.rehearsal or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return
    with service.lock:
        now = time.monotonic()
        if getattr(service, "deploy_refresh_running", False) or getattr(service, "deploy_refresh_next", 0) > now:
            return
        service.deploy_refresh_next = now + 180
        with service.store.read() as c:
            app = service.row(c)
            if not app or repo.split("/")[0] != app["org"].lower():
                return
            if not c.execute("SELECT 1 FROM repositories WHERE lower(full_name)=? AND reachable=1", (repo,)).fetchone():
                return
            shas = [
                r[0]
                for r in c.execute(
                    "SELECT DISTINCT l.pr_sha FROM task_links l JOIN tasks t ON t.id=l.task_id "
                    "WHERE t.status NOT IN ('done','closed') AND l.kind='pr' AND l.state='merged' AND lower(l.repo)=? "
                    "AND l.pr_sha IS NOT NULL",
                    (repo,),
                )
            ]
        keys = [(repo, sha, commit) for sha in shas if sha != commit and re.fullmatch(r"[0-9a-f]{40}", sha)]
        cache = getattr(service, "deploy_refresh_cache", {})
        due = sorted(
            (
                key
                for key in keys
                if not cache.get(key, {}).get("verified") and cache.get(key, {}).get("next", 0) <= now
            ),
            key=lambda key: cache.get(key, {}).get("next", 0),
        )[:20]
        service.deploy_refresh_cache = cache
        service.deploy_refresh_running = True

    def refresh():
        try:
            token = None
            for key in due:
                if service.repository_stop.is_set():
                    return
                verified = reverted = False
                try:
                    if token is None:
                        token, _ = service.mint([repo], {"contents": "read"}, diagnose=False)
                    response = service._call(
                        "GET",
                        f"/repos/{quote(repo, safe='/')}/compare/{key[1]}...{commit}",
                        params={"per_page": 1},
                        headers={"Authorization": "Bearer " + token},
                    )
                    if response.status_code == 200:
                        data = response.json()
                        verified = (
                            data.get("status") in ("ahead", "identical")
                            and (data.get("base_commit") or {}).get("sha") == key[1]
                            and (data.get("merge_base_commit") or {}).get("sha") == key[1]
                        )
                    if (
                        verified
                        and change_kept(
                            lambda path, **params: _read(service, token, path, params), repo, key[1], commit
                        )
                        is False
                    ):
                        # Both commits are fixed, so the answer is too: never asked again for this release.
                        verified, reverted = False, True
                        logging.getLogger("tico.github").info(
                            "Release %s reverts %s in %s: not shipped", commit[:12], key[1][:12], repo
                        )
                except Exception:
                    verified = False
                    logging.getLogger("tico.github").warning("Release ancestry unavailable for %s at %s", repo, key[1])
                with service.lock:
                    if key not in cache and len(cache) >= 512:
                        cache.pop(next(iter(cache)))
                    cache[key] = {"verified": verified, "next": float("inf") if reverted else time.monotonic() + 300}
            if service.repository_stop.is_set():
                return
            with service.lock:
                verified = {key for key in keys if cache.get(key, {}).get("verified")}
            # Current links are read again after the network reads.
            if (settings.release_repo.lower(), settings.release_commit) == (repo, commit):
                with service.store.transaction() as c:
                    ship_deployed(c, settings, ancestry=verified)
        except Exception:
            logging.getLogger("tico.github").exception("Deployed task reconciliation failed")
        finally:
            with service.lock:
                service.deploy_refresh_running = False

    threading.Thread(target=refresh, name="tico-deploy-refresh", daemon=True).start()


# What a release kept of a pull request is read hunk by hunk: tests are cut routinely, so the product files decide
# when the pull request has any.
TEST_PATH = re.compile(r"(^|/)(tests?|testdata|fixtures)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.test\.[a-z]+$")


def _hunks(patch):
    """Each hunk of a unified diff as (lines before, lines after), context included and whitespace kept exactly."""
    hunks, before, after = [], None, None
    for line in (patch or "").removesuffix("\n").split("\n"):
        if line.startswith("@@"):
            if before is not None:
                hunks.append((before, after))
            before, after = [], []
        elif before is None or line.startswith("\\"):
            continue
        elif line[:1] == "+":
            after.append(line[1:])
        elif line[:1] == "-":
            before.append(line[1:])
        else:
            before.append(line[1:])
            after.append(line[1:])
    if before is not None:
        hunks.append((before, after))
    return [(b, a) for b, a in hunks if b != a]


def _contains(lines, block):
    """`block` appears in `lines` as a run of consecutive lines."""
    n = len(block)
    return any(lines[i : i + n] == block for i in range(len(lines) - n + 1) if lines[i] == block[0])


def _read(service, token, path, params):
    """One GitHub read for `change_kept`: the JSON, None when it is not there, an exception otherwise."""
    response = service._call("GET", path, params=params or None, headers={"Authorization": "Bearer " + token})
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"GitHub answered {response.status_code} for {path}")
    return response.json()


def change_kept(get, repo, merge, release):
    """Whether `release`, which contains `merge`, still has what that merge changed.

    False only for a revert: in every hunk of every file it measures, the text after the change (with its context)
    is gone from the release and the text before it is back where it was, under the old name of a renamed file. True
    as soon as one hunk is still there, or is neither (changed again since); None when a file has no diff to read
    (binary, too large), which leaves ancestry to decide as before."""
    base = f"/repos/{quote(repo, safe='/')}"
    commit = get(f"{base}/commits/{merge}")
    if not commit:
        return None
    parents = commit.get("parents") or []
    files = commit.get("files") or []
    if len(parents) > 1:  # a merge commit: the pull request is what it changed on top of main
        files = (get(f"{base}/compare/{parents[0]['sha']}...{merge}") or {}).get("files") or []
    product = [f for f in files if not TEST_PATH.search(f["filename"])]
    if not (product or files):
        return None
    cache = {}

    def at_release(path):
        """The file's lines in the release ([] when it isn't there) and its blob sha."""
        if path not in cache:
            there = get(f"{base}/contents/{quote(path)}", ref=release)
            sha = there.get("sha") if isinstance(there, dict) else None
            text = ""
            if sha:
                blob = get(f"{base}/git/blobs/{sha}") or {}
                text = base64.b64decode(blob.get("content") or "").decode("utf-8", "replace")
            cache[path] = (text.removesuffix("\n").split("\n") if text else [], sha)
        return cache[path]

    unknown = False
    for f in product or files:
        hunks = _hunks(f.get("patch"))
        if not hunks:
            unknown = True
            continue
        new, sha = at_release(f["filename"])
        if sha and f.get("status") != "removed" and sha == f.get("sha"):
            return True  # the file is as the merge left it
        old = at_release(f.get("previous_filename") or f["filename"])[0]
        for before, after in hunks:
            if after and _contains(new, after):
                return True  # this part of the change is still there
            if before and not _contains(old, before):
                return True  # changed again since: not a revert
    return None if unknown else False


def checked_on_github(c, settings, repo):
    """`refresh_deployed_tasks` can ask GitHub about this release, so it decides what ships, reverts included."""
    app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    return bool(
        not settings.rehearsal
        and app
        and repo.split("/")[0].lower() == str(app["org"]).lower()
        and c.execute("SELECT 1 FROM repositories WHERE lower(full_name)=? AND reachable=1", (repo.lower(),)).fetchone()
    )


def ship_deployed(c, settings, *, ancestry=None):
    """Every merged pull request link in the running release is `shipped`; the task itself does not move."""
    commit, repo = settings.release_commit, settings.release_repo
    if not commit or not repo:
        return []
    # Push order alone can't see a revert; it ships only where GitHub can't be asked.
    here = (
        ancestry is None
        and not checked_on_github(c, settings, repo)
        and c.execute("SELECT seq FROM main_pushes WHERE sha=?", (commit,)).fetchone()
    )
    shipped = []
    candidates = H._rows(
        c.execute(
            DEPLOY_QUERY,
            ("https://github.com/" + repo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/pull/%",),
        )
    )

    def included(link):
        match = PR_LINK.match(link["url"])
        if not match or f"{match.group(1)}/{match.group(2)}".lower() != repo.lower():
            return False
        verified = (repo.lower(), link["pr_sha"], commit) in (ancestry or ())
        historical = ancestry is None and here and link["merge_seq"] and link["merge_seq"] <= here["seq"]
        return bool(link["pr_sha"] == commit or verified or historical)

    for link in candidates:
        with isolated(c, "ship_deployed", link["task_id"], private=True):
            if not included(link):
                continue
            c.execute("UPDATE task_links SET state='shipped',updated_at=? WHERE id=?", (H.now(), link["id"]))
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), link["task_id"]))
            detail = H._json(link.get("detail"), {}) or {}
            item = f"Shipped: {link['title']} is in the running release {commit[:12]} {link['url']}"
            if fire_once(detail, item):
                c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
                queue_wake(c, H.task(c, link["task_id"]), item)
            evidence = {"release": commit, "url": link["url"]}
            if (repo.lower(), link["pr_sha"], commit) in (ancestry or ()):
                evidence["ancestry"] = {
                    "repository": repo.lower(),
                    "merge": link["pr_sha"],
                    "deployed": commit,
                    "comparison": f"https://github.com/{repo}/compare/{link['pr_sha']}...{commit}",
                }
            H.event(c, H.KEEPER, "github.shipped", link["task_id"], evidence)
            if link["task_id"] not in shipped:
                shipped.append(link["task_id"])
    return shipped


def _item_metadata(c, github_app, event, payload):
    """The engine's PR and release metadata from a webhook (backend/item_sources/github.py), in a savepoint: a fault there is
    logged and the 5-minute re-read brings it, and what else the webhook does still happens."""
    from .item_sources import github as GS

    if event not in ("pull_request", "check_run", "status", "release"):
        return []
    app = github_app.row(c) if github_app is not None else None
    c.execute("SAVEPOINT item_metadata")
    try:
        found = GS.webhook(c, event, payload, (app["slug"] if app else None) or None)
    except Exception:
        c.execute("ROLLBACK TO item_metadata")
        logging.getLogger("tico.github").exception("Saving the metadata of a %s webhook failed", event)
        found = []
    c.execute("RELEASE item_metadata")
    return found


def install_github(app, settings, store):
    @app.post(PATH)
    async def webhook(request: Request):
        # The secret read (sealed, maybe KMS) and the write transaction wait on the database: off the event loop.
        return await asyncio.to_thread(handle, request.headers, await request.body())

    def handle(headers, body):
        app_secret = app.state.github_app.webhook_secret()
        if not (verify(settings.github_webhook_secret, headers, body) or verify(app_secret, headers, body)):
            # unset: nothing to verify against, and the path does not exist for anyone
            raise Problem("forbidden", "Bad signature", 403 if settings.github_webhook_secret else 404)
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise Problem("payload", "Send JSON", 400)
        event = str(headers.get("x-github-event") or "")
        if event == "ping":
            return {"ok": True}
        if event in ("installation", "installation_repositories"):
            from .repositories import queue_sync

            # Accepting new permissions arrives here: a turn asks for all its reads again at once.
            app.state.github_app.live.pop("turn-reads", None)
            app.state.github_app.live.pop("workflows-refused", None)
            queue_sync(app.state.github_app, refresh=True)
            return {"ok": True}
        if event == "merge_group":
            return merge_group(headers, payload)
        with store.transaction() as c:
            from .item_sources import github as GS

            # A delivery GitHub sends again (a retry, a redelivery) is taken once (docs/pipelines-v3.md "GitHub").
            if GS.seen_delivery(c, headers.get("x-github-delivery"), event):
                return {"ok": True, "repeat": True}
            metadata = _item_metadata(c, app.state.github_app, event, payload)
            if event == "pull_request":
                result = pull_request(
                    c, payload, review_role=settings.github_review_role, release_repo=settings.release_repo
                )
            elif event in (
                "check_run",
                "check_suite",
                "status",
                "pull_request_review",
                "pull_request_review_comment",
                "workflow_run",
            ):
                result = pr_signal(c, event, payload)
            elif event == "push":
                result = push(c, payload)
                result["shipped"] = ship_deployed(c, settings)
            elif event == "release":
                result = release(c, payload, release_repo=settings.release_repo)
            elif metadata:
                result = {}
            else:
                return Response(status_code=204)
        return {"ok": True, **result, **({"metadata": metadata} if metadata else {})}

    def merge_group(headers, payload):
        """GitHub's merge queue asks for the checks on a group's merge commit: `tico/merge-ok` is answered there at
        once (item_sources/github.py `answer_merge_group`), with no transaction held during GitHub's calls."""
        from .item_sources import github as GS

        with store.transaction() as c:
            if GS.seen_delivery(c, headers.get("x-github-delivery"), "merge_group"):
                return {"ok": True, "repeat": True}
        answer = GS.answer_merge_group(store, AppGitHub(app.state.github_app), payload)
        return {"ok": True, "merge_ok": answer.get("ok"), "posted": answer.get("posted")}


# ----------------------------------------------------------------------------- the engine's GitHub (item_sources.github.GitHub)
FACT_READ = """query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
mergeCommitAllowed squashMergeAllowed rebaseMergeAllowed
pullRequest(number:$number){id state merged isDraft headRefOid mergedAt mergeCommit{oid} isInMergeQueue
isMergeQueueEnabled autoMergeRequest{enabledAt} mergeable mergeStateStatus
timelineItems(last:1,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT,AUTO_MERGE_ENABLED_EVENT,
AUTO_MERGE_DISABLED_EVENT]){nodes{__typename ... on RemovedFromMergeQueueEvent{reason createdAt}
... on AutoMergeDisabledEvent{reason reasonCode createdAt}}}}}}"""
FACT_ENQUEUE = """mutation($id:ID!,$head:GitObjectID!){
enqueuePullRequest(input:{pullRequestId:$id,expectedHeadOid:$head}){clientMutationId}}"""
FACT_AUTO = """mutation($id:ID!,$head:GitObjectID!){
enablePullRequestAutoMerge(input:{pullRequestId:$id,expectedHeadOid:$head}){clientMutationId}}"""
FACT_MERGE = """mutation($id:ID!,$head:GitObjectID!,$method:PullRequestMergeMethod!){
mergePullRequest(input:{pullRequestId:$id,expectedHeadOid:$head,mergeMethod:$method}){clientMutationId}}"""
FACT_READY = """mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){clientMutationId}}"""
FACT_DEQUEUE = """mutation($id:ID!){dequeuePullRequest(input:{id:$id}){clientMutationId}}"""
FACT_AUTO_OFF = """mutation($id:ID!){disablePullRequestAutoMerge(input:{pullRequestId:$id}){clientMutationId}}"""
MERGE_GROUP_READ = """query($owner:String!,$name:String!,$branch:String!){repository(owner:$owner,name:$name){
mergeQueue(branch:$branch){entries(first:100){nodes{position headCommit{oid} pullRequest{number headRefOid}}}}}}"""
WRITE_PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}


class GitHubRefused(RuntimeError):
    """A GitHub call that failed or was refused; the message is GitHub's reason."""


class AppGitHub:
    """`item_sources.github.GitHub` through the company's GitHub App (backend/github_app.py `GitHubApp`): what the PR and release
    metadata reads, and the merge queue's enqueue and dequeue (GraphQL). Each call mints a token for the one repo. A failed
    call raises `GitHubRefused` (or the app's Problem) with GitHub's reason; the caller keeps the metadata as it was."""

    def __init__(self, service):
        self.service = service

    def _headers(self, repo, permissions):
        token, _ = self.service.mint([repo], permissions)
        return {"Authorization": "Bearer " + token}

    def _get(self, repo, path, permissions, params=None, ok=(200,)):
        response = self.service._call(
            "GET", f"/repos/{quote(repo, safe='/')}{path}", headers=self._headers(repo, permissions), params=params
        )
        if response.status_code not in ok:
            raise GitHubRefused(f"GitHub answered HTTP {response.status_code}")
        return response

    def _graphql(self, repo, permissions, query, variables):
        """(data, errors) of one GraphQL call; an HTTP failure raises."""
        response = self.service._call(
            "POST", "/graphql", headers=self._headers(repo, permissions), json={"query": query, "variables": variables}
        )
        if response.status_code != 200:
            raise GitHubRefused(f"GitHub answered HTTP {response.status_code}")
        body = response.json() or {}
        errors = "; ".join(str((e or {}).get("message") or e) for e in body.get("errors") or [])
        return body.get("data") or {}, errors

    def _pull(self, repo, number, permissions):
        """(repository, pullRequest) from GraphQL, the pull request None when it does not exist."""
        owner, name = repo.split("/", 1)
        data, errors = self._graphql(
            repo, permissions, FACT_READ, {"owner": owner, "name": name, "number": int(number)}
        )
        repository = data.get("repository") or {}
        found = repository.get("pullRequest")
        if not found and errors and "Could not resolve" not in errors:
            raise GitHubRefused(errors)
        return repository, found

    # ------------------------------------------------------------------ reads
    def read_pr(self, repo, number):
        from .item_sources import github as GS

        _, found = self._pull(repo, number, {"pull_requests": "read", "metadata": "read"})
        if not found:
            return None
        merged = bool(found.get("merged"))
        state = "merged" if merged else "open" if found.get("state") == "OPEN" else "closed"
        meta = {
            "state": state,
            "draft": bool(found.get("isDraft")),
            "head_sha": str(found.get("headRefOid") or "").lower() or None,
            "merged_sha": ((found.get("mergeCommit") or {}).get("oid") or None) if merged else None,
            "merged_at": found.get("mergedAt") if merged else None,
            "queue_state": "none",
            "removed_reason": None,
            "failing_check": None,
            "failing_check_url": None,
        }
        if merged or state != "open":
            return meta
        if found.get("isInMergeQueue"):
            meta["queue_state"] = "queued"
        elif found.get("autoMergeRequest"):
            meta["queue_state"] = "auto_merge"
        else:
            last = ((found.get("timelineItems") or {}).get("nodes") or [None])[-1] or {}
            kind = last.get("__typename")
            reason = str(last.get("reasonCode") or last.get("reason") or "")
            if kind in ("RemovedFromMergeQueueEvent", "AutoMergeDisabledEvent") and (
                reason.lower() not in GS.MERGED_REASONS
            ):
                meta["queue_state"] = "removed"
                meta["removed_reason"] = GS.removal_words(reason)
                if kind == "RemovedFromMergeQueueEvent":
                    failed = self._queue_failure(repo, number, str(last.get("createdAt") or ""))
                    if failed:
                        meta["failing_check"], meta["failing_check_url"] = failed
        return meta

    def _queue_failure(self, repo, number, before):
        """(name, url) of what failed in the PR's newest merge-queue run (GitHub Actions `merge_group`, on its
        `gh-readonly-queue/<base>/pr-<number>-<sha>` branch) up to `before`: its failing legs, else the checks job's line
        (`item_sources.github.failing_leg`). None when GitHub will not say."""
        from .item_sources import github as GS

        try:
            runs = (
                self._get(
                    repo,
                    "/actions/runs",
                    {"actions": "read", "metadata": "read"},
                    params={"event": "merge_group", "per_page": 50},
                )
                .json()
                .get("workflow_runs")
                or []
            )
            mine = [
                r
                for r in runs
                if f"/pr-{int(number)}-" in str(r.get("head_branch") or "")
                and (not before or str(r.get("created_at") or "") <= before)
            ]
            for found in sorted(mine, key=lambda r: str(r.get("created_at") or ""), reverse=True)[:1]:
                jobs = self.service._call(
                    "GET",
                    str(found.get("jobs_url") or ""),
                    headers=self._headers(repo, {"actions": "read", "metadata": "read"}),
                )
                if jobs.status_code != 200:
                    return None
                return GS.failing_leg(jobs.json().get("jobs") or [], lambda job: self._checks_line(repo, job))
        except Exception:  # noqa: BLE001 - the removal stands without its check
            logging.getLogger("tico.github").info("No merge-queue run read for %s #%s", repo, number)
        return None

    def _checks_line(self, repo, job):
        """The `Failed: ...` error line the workflow's checks job printed (an annotation on its check run), or None when
        there is none or GitHub will not say. The removal names the run without it."""
        url = str(job.get("check_run_url") or "")
        if not url:
            return None
        try:
            notes = self.service._call(
                "GET", url + "/annotations", headers=self._headers(repo, {"checks": "read", "metadata": "read"})
            )
            if notes.status_code != 200:
                return None
            for note in notes.json() or []:
                message = str(note.get("message") or "")
                if message.startswith("Failed: "):
                    return message
        except Exception:  # noqa: BLE001 - the removal names the run's outcome without the line
            logging.getLogger("tico.github").info("No checks line read for %s", repo)
        return None

    def read_checks(self, repo, sha):
        """Every check run (`filter=latest`: GitHub's newest run of each name in each check suite) and commit status on
        `sha`, one row per check (`item_sources.github.latest_checks`). Two runs with one name in two suites (two
        workflows, or two Apps) are two checks; a red one is never hidden behind a green one of the same name."""
        from .item_sources import github as GS

        slug = ""
        row = self.service.row()
        if row is not None:
            slug = str(row["slug"] or "")
        permissions = {"checks": "read", "statuses": "read", "metadata": "read"}
        base = f"/commits/{quote(sha, safe='')}"
        checks = []
        for page in range(1, 11):
            params = {"per_page": 100, "page": page, "filter": "latest"}
            runs = self._get(repo, base + "/check-runs", permissions, params).json().get("check_runs") or []
            checks.extend(GS.run_check(run, slug) for run in runs)
            if len(runs) < 100:
                break
        for status in self._get(repo, base + "/status", permissions, {"per_page": 100}).json().get("statuses") or []:
            checks.append(GS.status_check(status))
        return GS.latest_checks(checks)

    def read_releases(self, repo):
        response = self._get(repo, "/releases", {"contents": "read", "metadata": "read"}, {"per_page": 100}, (200, 404))
        if response.status_code == 404:
            return []
        return [
            {
                "tag": r["tag_name"],
                "commit_sha": None,  # a release names its tag; `contains` compares against the tag
                "prerelease": bool(r.get("prerelease")),
                "published_at": r.get("published_at") or r.get("created_at"),
            }
            for r in response.json() or []
            if r.get("tag_name") and not r.get("draft")
        ]

    def contains(self, repo, release_sha, commit_sha):
        path = f"/compare/{quote(commit_sha, safe='')}...{quote(release_sha, safe='')}"
        response = self._get(repo, path, {"contents": "read", "metadata": "read"}, ok=(200, 404))
        if response.status_code == 404:
            return False
        return (response.json() or {}).get("status") in ("ahead", "identical")

    def pr_diff(self, repo, number, sha):
        from .item_sources import github as GS

        permissions = {"contents": "read", "pull_requests": "read", "metadata": "read"}
        base = str(
            ((self._get(repo, f"/pulls/{int(number)}", permissions).json() or {}).get("base") or {}).get("ref") or ""
        )
        if not base:
            return None
        path = f"/compare/{quote(base, safe='')}...{quote(sha, safe='')}"
        return GS.diff_id((self._get(repo, path, permissions).json() or {}).get("files"))

    # ------------------------------------------------------------------ the merge queue
    def ready(self, repo, number):
        _, found = self._pull(repo, number, WRITE_PERMISSIONS)
        if not found or not found.get("isDraft") or found.get("state") != "OPEN":
            return
        _, errors = self._graphql(repo, WRITE_PERMISSIONS, FACT_READY, {"id": found["id"]})
        if errors:
            raise GitHubRefused(errors)

    def enqueue(self, repo, number, head_sha):
        repository, found = self._pull(repo, number, WRITE_PERMISSIONS)
        if not found:
            return {"ok": False, "how": None, "reason": "The pull request is gone"}
        if found.get("merged"):
            return {"ok": True, "how": "merged", "reason": ""}
        if found.get("state") != "OPEN":
            return {"ok": False, "how": None, "reason": "The pull request is not open"}
        if str(found.get("headRefOid") or "").lower() != str(head_sha).lower():
            return {"ok": False, "how": None, "reason": "The head moved"}
        if found.get("isInMergeQueue"):
            return {"ok": True, "how": "queued", "reason": ""}
        if found.get("autoMergeRequest"):
            return {"ok": True, "how": "auto_merge", "reason": ""}
        # A PR that conflicts with its base branch would only come back out of the queue: the Builder rebases it first.
        # Reading `mergeable` also starts GitHub's work on it when it has none yet, so the next try has the answer.
        from .item_sources import github as GS

        mergeable = str(found.get("mergeable") or "").upper()
        if mergeable == "CONFLICTING" or str(found.get("mergeStateStatus") or "").upper() == "DIRTY":
            return {"ok": False, "how": None, "reason": GS.CONFLICTS}
        if mergeable == "UNKNOWN":
            raise GitHubRefused(GS.MERGEABLE_UNKNOWN)
        ids = {"id": found["id"], "head": head_sha}
        if found.get("isMergeQueueEnabled"):
            _, errors = self._graphql(repo, WRITE_PERMISSIONS, FACT_ENQUEUE, ids)
            return {"ok": not errors, "how": "queued" if not errors else None, "reason": errors}
        _, errors = self._graphql(repo, WRITE_PERMISSIONS, FACT_AUTO, ids)
        if not errors:
            return {"ok": True, "how": "auto_merge", "reason": ""}
        if "clean status" not in errors.lower():
            return {"ok": False, "how": None, "reason": errors}
        # Auto-merge refuses a PR that can merge now: merge it, with the first way the repo allows.
        method = next(
            (m for m, on in (("MERGE", "mergeCommitAllowed"), ("SQUASH", "squashMergeAllowed")) if repository.get(on)),
            "REBASE",
        )
        _, errors = self._graphql(repo, WRITE_PERMISSIONS, FACT_MERGE, {**ids, "method": method})
        return {"ok": not errors, "how": "merged" if not errors else None, "reason": errors}

    def post_check(self, repo, sha, ok, summary):
        """Tico's `tico/merge-ok` check run on `sha`, completed as success (`ok` True), failure (False) or neutral (None:
        a merge group of PRs no Tico task links; GitHub counts neutral as passing) (a new run each time: GitHub counts
        the newest run of a name). Minted with the server's own Checks: write token (`mint_checks`), which no
        bot's token carries. False when the installation has no Checks: write (nothing posted: the check is only extra
        where a ruleset does not require it, and GitHub refuses the merge where one does)."""
        from .item_sources import github as GS

        permissions = self.service.live_permissions()
        if permissions is None:
            raise GitHubRefused("GitHub did not say whether the App may post checks")
        token = self.service.mint_checks(repo) if permissions.get("checks") == "write" else None
        if not token:
            return False
        response = self.service._call(
            "POST",
            f"/repos/{quote(repo, safe='/')}/check-runs",
            headers={"Authorization": "Bearer " + token},
            json={
                "name": GS.MERGE_OK,
                "head_sha": sha,
                "status": "completed",
                "conclusion": "neutral" if ok is None else "success" if ok else "failure",
                "output": {
                    "title": "Not a Tico task" if ok is None else "Signed off in Tico" if ok else "Not queued by Tico",
                    "summary": summary[:500],
                },
            },
        )
        if response.status_code not in (200, 201):
            raise GitHubRefused(f"GitHub answered HTTP {response.status_code} to {GS.MERGE_OK}")
        return True

    def merge_group_prs(self, repo, base_ref, head_sha):
        branch = str(base_ref or "").removeprefix("refs/heads/")
        owner, name = repo.split("/", 1)
        data, errors = self._graphql(
            repo,
            {"contents": "read", "pull_requests": "read", "metadata": "read"},
            MERGE_GROUP_READ,
            {"owner": owner, "name": name, "branch": branch},
        )
        if errors:
            raise GitHubRefused(errors)
        queue = (data.get("repository") or {}).get("mergeQueue") or {}
        entries = sorted(
            ((queue.get("entries") or {}).get("nodes") or []), key=lambda e: int((e or {}).get("position") or 0)
        )
        out = []
        for entry in entries:
            pull = (entry or {}).get("pullRequest") or {}
            out.append({"number": int(pull.get("number") or 0), "head_sha": str(pull.get("headRefOid") or "").lower()})
            if str(((entry or {}).get("headCommit") or {}).get("oid") or "").lower() == head_sha.lower():
                return out  # the group: this entry and every one ahead of it
        return []

    def dequeue(self, repo, number):
        _, found = self._pull(repo, number, WRITE_PERMISSIONS)
        if not found:
            return {"ok": True, "how": "absent", "reason": ""}
        if found.get("merged"):
            return {"ok": True, "how": "merged", "reason": ""}
        if found.get("isInMergeQueue"):
            mutation = FACT_DEQUEUE
        elif found.get("autoMergeRequest"):
            mutation = FACT_AUTO_OFF
        else:
            return {"ok": True, "how": "absent", "reason": ""}
        _, errors = self._graphql(repo, WRITE_PERMISSIONS, mutation, {"id": found["id"]})
        if errors:
            raise GitHubRefused(errors)
        return {"ok": True, "how": "dequeued", "reason": ""}

    # ------------------------------------------------------------------ a release (item_sources/github_release.py)
    def cut_release(self, repo, kind, name, sha):
        """Run the release workflow `name` on the default branch, or create the tag `name` at `sha`. The token names
        only the one permission it needs; an installation without it is a not-ok answer saying so (never retried:
        only a person can grant it). A failed call raises, to be tried again."""
        from .item_sources import github_release as GR

        needed, missing = ("actions", GR.MISSING_ACTIONS) if kind == "workflow" else ("contents", GR.MISSING_CONTENTS)
        permissions = {needed: "write", "metadata": "read"}
        live = self.service.live_permissions()
        if live is not None and live.get(needed) != "write":
            return {"ok": False, "how": None, "reason": missing}
        try:
            headers = self._headers(repo, permissions)
        except Problem as problem:
            if problem.code == "github_app_permissions":
                return {"ok": False, "how": None, "reason": missing}
            raise
        path = f"/repos/{quote(repo, safe='/')}"
        if kind == "workflow":
            branch = (self._get(repo, "", {"metadata": "read"}).json() or {}).get("default_branch") or "main"
            response = self.service._call(
                "POST",
                f"{path}/actions/workflows/{quote(name, safe='')}/dispatches",
                headers=headers,
                json={"ref": branch},
            )
            if response.status_code == 204:
                return {"ok": True, "how": f"workflow {name} on {branch}", "reason": ""}
        else:
            response = self.service._call(
                "POST", f"{path}/git/refs", headers=headers, json={"ref": f"refs/tags/{name}", "sha": sha}
            )
            if response.status_code == 201:
                return {"ok": True, "how": f"tag {name}", "reason": ""}
            if response.status_code == 422:  # the tag is there: fine when it already names this commit
                found = self._get(
                    repo,
                    f"/git/ref/tags/{quote(name, safe='')}",
                    {"metadata": "read", "contents": "read"},
                    ok=(200, 404),
                )
                target = ((found.json() or {}).get("object") or {}).get("sha") if found.status_code == 200 else None
                if target and str(target).lower() == sha.lower():
                    return {"ok": True, "how": f"tag {name}", "reason": ""}
                return {"ok": False, "how": None, "reason": f"The tag {name} is already on another commit"}
        try:
            said = str((response.json() or {}).get("message") or "")[:200]
        except (ValueError, AttributeError):
            said = ""
        if response.status_code == 403 and "not accessible" in said.lower():
            return {"ok": False, "how": None, "reason": missing}
        if response.status_code in (404, 422):  # no such workflow, or it has no workflow_dispatch trigger
            return {"ok": False, "how": None, "reason": f"GitHub answered HTTP {response.status_code}: {said}"}
        raise GitHubRefused(f"GitHub answered HTTP {response.status_code}: {said}")
