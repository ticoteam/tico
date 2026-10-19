"""GitHub keeps the pull requests attached to a task up to date; it never moves the task.

A task carries its pull request as a link (`hub task link`). A link is information only: opening,
merging or closing the PR, a release tag or a deploy changes the link, never the task's status or
step. A person moves the task. The repository's webhook posts here signed with
`TICO_GITHUB_WEBHOOK_SECRET`; nothing else on this path is trusted:

- a pull request opened, merged or closed -> the link is `open`, `draft`, `merged` or `closed`
- checks, conflicts and reviews     -> specific owner notices grouped within three minutes
- a link armed with `wake_once`     -> one notice when its checks finish (pass or fail), it gets an
                                       approving or change-requesting review, or a release ships it;
                                       then it disarms. It wakes the owner's turn and changes nothing else.
- a review requested or withdrawn   -> that person is on or off the task in `github_review_role`
- a push to main                    -> the commits are recorded, in order, in `main_pushes`

The last hop is a fact of the deploy, not of GitHub: the release manifest names the commit the
running code was built from (`release.py` writes it from CI), and `ship_deployed` marks a merged
link `shipped` once its merge commit is at or before that commit on main. Only the repository this
release came from ships that way; a merged link in another repository stays `merged`. A release that
contains the merge and then its revert (a change dropped after QA failed) does not ship it: GitHub is
asked whether the pull request's own lines are still there at the release commit (`change_kept`).
"""
import base64
import hashlib
import hmac
import itertools
import json
import logging
import re
import threading
import time
from urllib.parse import quote

from fastapi import Request, Response

from . import hubdb as H
from .batch_work import isolated
from .store import Problem

PATH = "/api/v2/github/webhook"   # under /api/v2: the runner hostname routes only that prefix
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
    match = PR_LINK.match(str(url).split("?")[0].rstrip("/"))
    if not match:
        return []
    return H._rows(c.execute("SELECT * FROM task_links WHERE kind='pr' AND lower(repo)=? AND number=?",
                            ((match.group(1) + "/" + match.group(2)).lower(), int(match.group(3)))))


def queue_wake(c, task, item, batch=None):
    """Keep one bounded, durable burst per task; the scheduler sends it within three minutes.

    `batch` is the kind of nonblocking notice the item is (wake_presets.BATCH_KINDS: review comments, a failing check
    nobody waits on), which the owner's wake preset may hold. A burst stays batchable only while every item in it is;
    one item that is not (a conflict, a result an armed link waits on) makes the whole burst wake at once."""
    from .repositories import metadata, save_metadata
    key = "github-task-wake:" + task["id"]
    burst = metadata(c, key)
    items = burst.get("items", [])
    if item not in items:
        items = (items + [item[:1000]])[-50:]
    kind = batch if not burst or burst.get("batch") == batch else None
    # Under a wake preset the preset is the one timer: the burst goes out on the next flush, and the hold coalesces it
    # from then (backend/wake_presets.py). Current behaviour keeps the three-minute burst.
    due = H.now() if _preset_owner(c, task) else burst.get("due") or H.shift(H.now(), seconds=170)
    save_metadata(c, key, {"due": due, "items": items, **({"batch": kind} if kind else {})})


def _preset_owner(c, task):
    from . import wake_presets
    owner = str(task.get("owner") or "")
    if not owner.startswith("bot:"):
        return False
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (owner[4:],)).fetchone()
    return wake_presets.preset_of(H._json(row[0], {}) if row else {}) != wake_presets.CURRENT


def fire_once(detail, item):
    """A link armed with `wake_once` gives this result, once: disarm it in the same write that queues it."""
    if not detail.get("wake_once"):
        return False
    detail.pop("wake_once")
    detail["wake_once_fired"] = {"at": H.now(), "item": item[:500]}
    return True


def flush_wakes(c):
    """Send each due PR burst independently; keep a failed or blocked notice for retry."""
    from .repositories import metadata
    sent = []
    for row in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'github-task-wake:%'").fetchall():
        with isolated(c, "github_wake", row["key"]):
            burst = metadata(c, row["key"])
            if burst.get("due", "") > H.now():
                continue
            task = H.task(c, row["key"].split(":", 1)[1])
            if task and task["status"] in (*H.ACTIVE_STATUSES, "blocked"):
                H._wake(c, task, task["owner"], "\n".join(burst.get("items", [])),
                        {"wake_batch": burst["batch"]} if burst.get("batch") else None, trigger="pr_checks")
            c.execute("DELETE FROM registry_metadata WHERE key=?", (row["key"],))
            if task and task["status"] in (*H.ACTIVE_STATUSES, "blocked"):
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
    want = ([who for who in have if who not in people] if action == "review_request_removed"
            else have + [who for who in people if who not in have])
    if want == have:
        return False
    return TRo.set_roles(c, H.KEEPER, task["id"], {role: want}, mover=True,
                         note="GitHub review " + ("request withdrawn" if action == "review_request_removed" else "requested"))


# The branch `hub task worktree add` names: tico/<the task's first 8 id characters>-<title slug>.
WORKTREE_BRANCH = re.compile(r"^tico/[0-9a-f]{8}-")


def register_worktree_pr(c, pr):
    """A pull request whose head is a task worktree's branch in the same repository belongs to that task: its link is
    registered here, so a worktree whose bot never ran `hub task link` still gets the link its release waits on.
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
    made = set()
    for row in H._rows(c.execute("SELECT id,task_id,detail_json FROM task_links WHERE kind='worktree' AND lower(repo)=? "
                                 "AND branch=?", (repo.lower(), branch))):
        if not c.execute("SELECT 1 FROM task_links WHERE task_id=? AND kind='pr' AND lower(repo)=? AND number=?",
                         (row["task_id"], repo.lower(), int(match.group(3)))).fetchone():
            link = H.task_link(c, H.KEEPER, row["task_id"], url)
            made.add(link["id"])
            H.event(c, H.KEEPER, "worktree.pr_registered", row["task_id"], {"url": url, "branch": branch})
        # The branch has its pull request now, however it was found, so an earlier failure to open one is over.
        detail = H._json(row["detail_json"], {}) or {}
        if detail.pop("pr_error", None):
            c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps(detail), row["id"]))
    return made


def pull_request(c, payload, review_role="", release_repo=""):
    pr = payload.get("pull_request") or {}
    url = str(pr.get("html_url") or "").split("?")[0].rstrip("/")
    action = str(payload.get("action") or "")
    registered = register_worktree_pr(c, pr)
    links = _links_for(c, url)
    for link in links:
        task = H.task(c, link["task_id"])
        if not task:
            continue
        state = "merged" if pr.get("merged") else ("closed" if pr.get("state") == "closed" or action == "closed"
                else "draft" if pr.get("draft") or action == "converted_to_draft" else "open")
        if link["state"] == "shipped" and state == "merged":
            state = "shipped"
        detail = H._json(link.get("detail_json"), {}) or {}
        head_sha = (pr.get("head") or {}).get("sha")
        new_head = head_sha and detail.get("head_sha") != head_sha
        mergeable = "unknown" if action == "synchronize" or new_head else link.get("mergeable") or "unknown"
        if action in ("opened", "reopened", "synchronize", "ready_for_review", "refresh"):
            if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
                mergeable = "conflict"
            elif pr.get("mergeable") is True or pr.get("mergeable_state") == "clean":
                mergeable = "clean"
        match = PR_LINK.match(url)
        c.execute("UPDATE task_links SET state=?,mergeable=?,repo=?,number=?,branch=?,updated=?,"
                  "pr_sha=coalesce(?,pr_sha),pr_merged_at=coalesce(?,pr_merged_at) WHERE id=?",
                  (state, mergeable, match.group(1) + "/" + match.group(2) if match else None,
                   int(match.group(3)) if match else pr.get("number"), (pr.get("head") or {}).get("ref"), H.now(),
                   pr.get("merge_commit_sha") if state == "merged" else None,
                   pr.get("merged_at") or H.now() if state == "merged" else None, link["id"]))
        author = str((pr.get("user") or {}).get("login") or "").lower()
        if author:
            detail["author_login"] = author
        if action != "refresh":
            detail["tracked"] = True
        if head_sha:
            if detail.get("head_sha") != head_sha:
                detail.pop("checks", None)
                detail.pop("head_login", None)
                detail.pop("conflict_head", None)
                c.execute("UPDATE task_links SET checks='pending' WHERE id=?", (link["id"],))
            detail["head_sha"] = head_sha
            login = (str((payload.get("sender") or {}).get("login") or "").lower()
                     if action == "synchronize" else str(payload.get("head_login") or "").lower())
            if login:
                if login == detail.get("author_login"):
                    detail["head_login"] = login
                else:
                    detail.pop("head_login", None)
        wake_once = False
        if (state == "merged" and link["state"] != "merged" and match
                and f"{match.group(1)}/{match.group(2)}".lower() != str(release_repo).lower()):
            # Only the release's own repository ships, so a merge elsewhere is the last result an armed link gets.
            wake_once = fire_once(detail, f"Merged: {link['title']}; it is not in the repository Tico releases, "
                                          f"so no shipped wake follows {url}")
        conflict_head = detail.get("head_sha") or "unknown"
        conflict_wake = mergeable == "conflict" and detail.get("conflict_head") != conflict_head
        if mergeable == "conflict":
            detail["conflict_head"] = conflict_head
        elif mergeable == "clean":
            detail.pop("conflict_head", None)
        c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps(detail), link["id"]))
        current = c.execute("SELECT * FROM task_links WHERE id=?", (link["id"],)).fetchone()
        if any(current[field] != link.get(field) for field in ("state", "mergeable", "repo", "number", "branch", "checks")):
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), task["id"]))
        item = f"{link['title']} {state}" if state != "closed" else f"{link['title']} was closed without merging"
        if mergeable == "conflict":
            item += ": Merge conflict"
        if wake_once:
            queue_wake(c, task, detail["wake_once_fired"]["item"])
        if (state == "closed" and link["state"] != state and link["id"] not in registered
                or conflict_wake):
            # Closed without merging is news; a conflict is a blocker on the bot's own work.
            queue_wake(c, task, item, "completion" if not conflict_wake and not wake_once else None)
        _review_role(c, task, payload, review_role)
        H.event(c, H.KEEPER, "github.pull_request", task["id"], {"action": action, "url": url})
    return {"pr": url, "action": action, "tasks": len(links)}


def _own_comment(c, task, payload, key, detail):
    login = str(((payload.get(key) or {}).get("user") or {}).get("login") or "").lower()
    if not login:
        return False
    author = str(((payload.get("pull_request") or {}).get("user") or {}).get("login") or "").lower()
    author = author or str(detail.get("author_login") or "").lower()
    head_login = str(detail.get("head_login") or "").lower()
    app = c.execute("SELECT slug FROM github_app LIMIT 1").fetchone()
    config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (H.actor_id(task["owner"]),)).fetchone()
    bot_login = (H._json(config[0], {}) or {}).get("github_login", "") if config else ""
    via_app = (payload.get(key) or {}).get("performed_via_github_app") or {}
    return (app and str(via_app.get("slug") or "").lower() == str(app[0]).lower()
            or login in {author, str(bot_login).lower(), head_login if head_login == author else "",
                         (str(app[0]) + "[bot]").lower() if app else ""})


def _suite_key(suite, run):
    """One key per check suite: an App such as GitHub Actions makes one suite per workflow."""
    slug = str((suite.get("app") or run.get("app") or {}).get("slug") or "Checks")
    return ("suite:" + slug[:150] + (f"#{suite['id']}" if suite.get("id") else ""))[:200]


def pr_signal(c, event, payload):
    """Checks, commit statuses, reviews and review comments update every attached PR."""
    repo = str((payload.get("repository") or {}).get("full_name") or "")
    pr = payload.get("pull_request") or {}
    signal = payload.get("check_run") or payload.get("check_suite") or payload
    prs = signal.get("pull_requests") or ([pr] if pr else [])
    def base_repo(p):
        base = ((p.get("base") or {}).get("repo") or {})
        return base.get("full_name") or (repo.rsplit("/", 1)[0] + "/" + base["name"] if base.get("name") and "/" in repo else repo)
    urls = {p.get("html_url") or f"https://github.com/{base_repo(p)}/pull/{p['number']}"
            for p in prs if p.get("number") or p.get("html_url")}
    links = []
    for url in urls:
        links.extend(_links_for(c, url))
    if event == "status" or event in ("check_run", "check_suite") and not prs:
        # Fork checks and commit statuses can omit the PR list.
        sha = payload.get("sha") if event == "status" else signal.get("head_sha")
        links = H._rows(c.execute("SELECT * FROM task_links WHERE kind='pr' AND lower(repo)=? "
                                  "AND json_extract(detail_json,'$.head_sha')=?", (repo.lower(), sha)))
    for link in links:
        task = H.task(c, link["task_id"])
        if not task:
            continue
        detail = H._json(link.get("detail_json"), {}) or {}
        fields = {}
        label = link["title"]
        wake, batch = False, None
        if event in ("check_run", "check_suite", "status"):
            if signal.get("head_sha") and detail.get("head_sha") and signal["head_sha"] != detail["head_sha"]:
                continue
            result = signal.get("conclusion") or signal.get("state") or "pending"
            state = "passing" if result in ("success", "neutral", "skipped") else (
                    "pending" if result in ("pending", "queued", "in_progress", "requested", "waiting") else "failing")
            checks = detail.get("checks", {})
            name = (_suite_key(signal, signal) if event == "check_suite"
                    else str(signal.get("name") or signal.get("context") or "Checks")[:200])
            wake = state == "failing" and checks.get(name) != "failing"
            batch = "ci"
            left_pending = checks.get(name) == "pending" and state != "pending"
            checks[name] = state
            own = (signal.get("check_suite") or {}) if event == "check_run" else {}
            if own and own.get("status") != "completed" and (
                    signal.get("status") != "completed" or checks.get(_suite_key(own, signal), "pending") == "pending"):
                # A run says its suite is still running even when that suite's `requested` never came here. A run
                # that is still going reopens it (a re-run keeps the suite id); a finished run delivered after its
                # suite completed is a stale snapshot and does not.
                checks[_suite_key(own, signal)] = "pending"
            detail["checks"] = dict(list(checks.items())[-100:])
            fields["checks"] = "failing" if "failing" in checks.values() else "pending" if "pending" in checks.values() else "passing"
            item = f"Checks {state} on {label}: {name.split('#')[0] if event == 'check_suite' else name}"
            # A suite completes once all of its runs have; a single run or commit status may be one of many.
            # A run or status that finishes after a suite already has can be the last one, so it counts too.
            suite_done = any(k.startswith("suite:") and v != "pending" for k, v in checks.items())
            if ((event == "check_suite" and signal.get("status") == "completed" and state != "pending"
                    or event != "check_suite" and left_pending and suite_done)
                    and "pending" not in checks.values()):
                # GitHub sends a suite's `requested` only to its own App, so a single run or status still pending
                # is often the only sign that another service's checks are not done.
                sha = str(signal.get("head_sha") or payload.get("sha") or detail.get("head_sha") or "")[:12]
                failed = list(dict.fromkeys(k[6:].split("#")[0] if k.startswith("suite:") else k
                                            for k, v in checks.items() if v == "failing"))
                done = (f"Checks finished on {label}: {'failed (' + ', '.join(failed) + ')' if failed else 'passed'} "
                        f"{link['url']}/checks" + (f" at {sha}" if sha else ""))
                if fire_once(detail, done):
                    wake, item, batch = True, done, None
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
            wake = fresh and payload.get("action") != "dismissed" and (
                state == "changes_requested" or state == "commented" and not _own_comment(c, task, payload, "review", detail))
            item = f"Review {state.replace('_', ' ')} on {label}"
            batch = "review"
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
            fields["pending_comments"] = max(0, (link.get("pending_comments") or 0) + (1 if action == "created" else -1))
            wake = action == "created" and not _own_comment(c, task, payload, "comment", detail)
            item = f"{fields['pending_comments']} review comments on {label}"
            batch = "review"
        changed = any(value != link.get(field) for field, value in fields.items())
        fields.update(detail_json=json.dumps(detail), updated=H.now())
        if changed:
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), task["id"]))
        c.execute("UPDATE task_links SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                  (*fields.values(), link["id"]))
        if wake:
            queue_wake(c, task, item, batch)
    return {"tasks": len({l["task_id"] for l in links})}


def refresh_task_prs(service, task_id):
    """Schedule a bounded refresh; task reads always use the last known state."""
    with service.lock:
        if getattr(service, "pr_refresh_running", False):
            return
        cache = getattr(service, "pr_refresh_cache", {})
        cached = [repo + "#" + str(number) for (repo, number), entry in cache.items()
                  if entry.get("next", 0) > time.monotonic()]
        with service.store.read() as c:
            app = service.row(c)
            if not app:
                return
            sql = ("SELECT l.* FROM task_links l JOIN repositories r ON lower(r.full_name)=lower(l.repo) "
                   "WHERE l.task_id=? AND l.kind='pr' AND l.number IS NOT NULL AND r.reachable=1 "
                   "AND lower(l.repo) LIKE ?")
            args = [task_id, str(app["org"] or "").lower() + "/%"]
            if cached:
                sql += " AND (lower(l.repo)||'#'||l.number) NOT IN (" + ",".join("?" * len(cached)) + ")"
                args.extend(cached)
            links = H._rows(c.execute(sql + " ORDER BY coalesce(l.updated,l.created),l.id LIMIT 20", args))
        due = list({(l["repo"].lower(), l["number"]): l for l in links}.values())
        if not due:
            return
        service.pr_refresh_running = True
        service.pr_refresh_cache = cache

    def refresh():
        try:
            for link in due:
                key = (link["repo"].lower(), link["number"])
                success = False
                try:
                    token, _ = service.mint([link["repo"]], {"pull_requests": "read", "contents": "read"})
                    response = service._call("GET", f"/repos/{quote(link['repo'], safe='/')}/pulls/{link['number']}",
                                             headers={"Authorization": "Bearer " + token})
                    if response.status_code == 200:
                        pr = response.json()
                        pr["html_url"] = link["url"]
                        head = (pr.get("head") or {}).get("sha")
                        head_login = None
                        if head:
                            try:
                                commit_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name") or link["repo"]
                                commit = service._call("GET", f"/repos/{quote(commit_repo, safe='/')}/commits/{quote(head, safe='')}",
                                                       headers={"Authorization": "Bearer " + token})
                                if commit.status_code == 200:
                                    data = commit.json()
                                    head_login = str((data.get("author") or {}).get("login") or
                                                     (data.get("committer") or {}).get("login") or "").lower()
                            except Exception:
                                logging.getLogger("tico.github").debug("Head identity unavailable for %s #%s", *key)
                        with service.store.transaction() as c:
                            current = c.execute("SELECT updated FROM task_links WHERE id=?", (link["id"],)).fetchone()
                            if not current or current[0] != link.get("updated"):
                                success = True
                                continue
                            pull_request(c, {"action": "refresh", "pull_request": pr, "head_login": head_login},
                                         review_role=service.settings.github_review_role,
                                         release_repo=service.settings.release_repo)
                        success = True
                except Exception:
                    logging.getLogger("tico.github").exception("PR refresh failed for %s #%s", *key)
                finally:
                    with service.lock:
                        failures = 0 if success else min(cache.get(key, {}).get("failures", 0) + 1, 5)
                        if key not in cache and len(cache) >= 512:
                            cache.pop(next(iter(cache)))
                        cache[key] = {"failures": failures, "next": time.monotonic() + (180 if success else min(300 * 2 ** (failures - 1), 3600))}
        finally:
            with service.lock:
                service.pr_refresh_running = False
    threading.Thread(target=refresh, name="tico-pr-refresh", daemon=True).start()


def _worktree_detail(service, repo, branch, change):
    """Apply `change(detail)` to every worktree link on this repository branch, in its own write."""
    with service.store.transaction() as c:
        for row in H._rows(c.execute("SELECT id,detail_json FROM task_links WHERE kind='worktree' AND lower(repo)=? "
                                     "AND branch=?", (repo.lower(), branch))):
            detail = H._json(row["detail_json"], {}) or {}
            change(detail)
            c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps(detail), row["id"]))


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
        response = service._call("POST", f"/repos/{quote(repo, safe='/')}/pulls",
                                 headers={"Authorization": "Bearer " + token},
                                 json={"title": (worktree.get("title") or branch)[:200], "head": branch,
                                       "base": worktree.get("base") or "main", "draft": True,
                                       "body": "Opened as a draft by Tico at this task branch's first push. Mark it "
                                               "ready for review (`gh pr ready`) and edit it (`gh pr edit`) when the "
                                               "work is."})
    except Exception:
        _pr_error(service, repo, branch, failed + " (GitHub did not answer)")
        return []
    if response.status_code != 201:
        _pr_error(service, repo, branch, failed + f" (GitHub {response.status_code})")
        return []
    return [response.json()]


def _remote_work(service, repo, branch, base, auth):
    """How many commits the branch has on GitHub that its base does not: None when GitHub has no such branch, the
    evidence a missed push webhook leaves behind and the line between a task's own work and a branch only moved to
    newer main."""
    response = service._call("GET", f"/repos/{quote(repo, safe='/')}/compare/{quote(base, safe='')}...{quote(branch, safe='')}",
                             headers=auth, params={"per_page": 1})
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"GitHub {response.status_code}")
    return int(response.json().get("ahead_by") or 0)


def reconcile_worktree_prs(service):
    """The background repair for the pull request links a worktree's release reads. Unfinished links on any task that
    holds a worktree (closed, declined and never-read tasks too) are read again, and a worktree branch with no link is
    looked up on GitHub, so a missed webhook heals without anyone opening the task; a branch with commits of its own
    and no pull request gets its draft. At most one pass every three minutes and 20 items a pass, shared between
    links and branches with the least recently read first, so neither kind can starve the other. A branch is asked
    again after 15 minutes when it has no pull request, or as soon as a new head is pushed or reported."""
    with service.lock:
        now = time.monotonic()
        if getattr(service, "worktree_reconcile_running", False) or getattr(service, "worktree_reconcile_next", 0) > now:
            return
        service.worktree_reconcile_next = now + 180
        cache = service.__dict__.setdefault("worktree_reconcile_cache", {})
        with service.store.read() as c:
            app = service.row(c)
            if not app:
                return
            org = str(app["org"] or "").lower() + "/%"
            held = ("EXISTS (SELECT 1 FROM repositories r WHERE lower(r.full_name)=lower({0}.repo) AND r.reachable=1) "
                    "AND lower({0}.repo) LIKE ?")
            # Both lists are as long as the worktrees the cap allows, so no row limit can leave one unread.
            links = H._rows(c.execute(
                "SELECT p.* FROM task_links p WHERE p.kind='pr' AND p.number IS NOT NULL "
                "AND coalesce(p.state,'open') NOT IN ('merged','closed','shipped') AND " + held.format("p") +
                " AND EXISTS (SELECT 1 FROM task_links w WHERE w.task_id=p.task_id AND w.kind='worktree' "
                "AND coalesce(w.state,'unknown')<>'removed')", (org,)))
            branches = H._rows(c.execute(
                "SELECT w.repo,w.branch,max(json_extract(w.detail_json,'$.pushed')) AS pushed,"
                "max(CASE WHEN json_extract(w.detail_json,'$.ahead')=0 THEN json_extract(w.detail_json,'$.last_commit') END) AS reported,"
                "min(CASE WHEN coalesce(t.private,0)=0 THEN t.title END) AS title,"
                "(SELECT r.default_branch FROM repositories r WHERE lower(r.full_name)=lower(w.repo)) AS base "
                "FROM task_links w JOIN tasks t ON t.id=w.task_id WHERE w.kind='worktree' "
                "AND coalesce(w.state,'unknown')<>'removed' AND w.branch LIKE 'tico/%' AND " + held.format("w") +
                " AND NOT EXISTS (SELECT 1 FROM task_links p WHERE p.task_id=w.task_id AND p.kind='pr' "
                "AND lower(p.repo)=lower(w.repo) AND p.branch=w.branch) GROUP BY w.repo,w.branch", (org,)))
        def ready(kind, repo, key, heads=None):
            seen = cache.get((kind, repo, key))
            return not seen or seen["next"] <= now or heads is not None and seen.get("heads") != heads
        def last(item):
            return (cache.get(item[:3]) or {}).get("last", 0)
        prs = sorted((("pr", l["repo"], l["number"], l) for l in links if ready("pr", l["repo"], l["number"])), key=last)
        heads = {(b["repo"], b["branch"]): sorted(h for h in (b["pushed"], b["reported"]) if h) for b in branches}
        found = sorted((("branch", b["repo"], b["branch"], b) for b in branches if WORKTREE_BRANCH.match(b["branch"])
                        and ready("branch", b["repo"], b["branch"], heads[(b["repo"], b["branch"])])), key=last)
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
                        response = service._call("GET", path, headers=auth, params={
                            "head": repo.split("/")[0] + ":" + key, "state": "all", "per_page": 10})
                        if response.status_code != 200:
                            raise RuntimeError(f"GitHub {response.status_code}")
                        found = response.json() if isinstance(response.json(), list) else []
                        retry = 180 if found else 900
                        if not found:
                            known = heads[(repo, key)]
                            ahead = _remote_work(service, repo, key, link["base"] or "main", auth)
                            # What GitHub held when asked covers every head known before asking.
                            _worktree_detail(service, repo, key, lambda detail: detail.update(
                                remote={"ahead": ahead or 0, "absent": ahead is None, "heads": known, "at": H.now()}))
                            if ahead:
                                found = _open_draft(service, repo, key, link)
                                retry = 180 if found else 900
                    registering = kind == "branch" and found
                    try:
                        with service.store.transaction() as c:
                            for pr in found:
                                if kind == "pr":
                                    current = c.execute("SELECT updated FROM task_links WHERE id=?", (link["id"],)).fetchone()
                                    if not current or current[0] != link.get("updated"):
                                        continue
                                    pr["html_url"] = link["url"]
                                match = PR_LINK.match(str(pr.get("html_url") or "").split("?")[0].rstrip("/"))
                                # A webhook that wrote or registered this pull request while it was being read holds
                                # newer facts, for every task it is linked to.
                                if match and c.execute(
                                        "SELECT 1 FROM task_links WHERE kind='pr' AND lower(repo)=? AND number=? "
                                        "AND updated>=?", (f"{match.group(1)}/{match.group(2)}".lower(),
                                                           int(match.group(3)), started)).fetchone():
                                    continue
                                pull_request(c, {"action": "refresh", "pull_request": pr},
                                             review_role=service.settings.github_review_role,
                                             release_repo=service.settings.release_repo)
                    except Exception:
                        if registering:
                            _pr_error(service, repo, key, f"The pull request for {key} was found but linking it to "
                                                          "the task failed; the next pass links it")
                        raise
                except Exception:
                    logging.getLogger("tico.github").exception("Worktree PR reconcile failed for %s %s", repo, key)
                    retry = 900
                with service.lock:
                    if (kind, repo, key) not in cache and len(cache) >= 512:
                        cache.pop(next(iter(cache)))
                    cache[(kind, repo, key)] = {"next": time.monotonic() + retry, "last": time.monotonic(),
                                                "heads": heads.get((repo, key)) if kind == "branch" else None}
        finally:
            with service.lock:
                service.worktree_reconcile_running = False
    threading.Thread(target=reconcile, name="tico-worktree-pr-reconcile", daemon=True).start()


def push(c, payload):
    """A push to the default branch: every commit, in order, so a deploy can be placed on main."""
    ref = str(payload.get("ref") or "")
    repo = str((payload.get("repository") or {}).get("full_name") or "")
    default = str((payload.get("repository") or {}).get("default_branch") or "main")
    head_commit = payload.get("head_commit") or {}
    head_sha = str(head_commit.get("id") or payload.get("after") or "")
    login = str((payload.get("sender") or {}).get("login") or
                (head_commit.get("committer") or {}).get("username") or
                (head_commit.get("author") or {}).get("username") or "").lower()
    if head_sha and login:
        for link in H._rows(c.execute("SELECT id,detail_json FROM task_links WHERE kind='pr' AND lower(repo)=? "
                                       "AND json_extract(detail_json,'$.head_sha')=?", (repo.lower(), head_sha))):
            detail = H._json(link["detail_json"], {}) or {}
            if login == detail.get("author_login"):
                detail["head_login"] = login
            else:
                detail.pop("head_login", None)
            c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps(detail), link["id"]))
    branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ""
    if WORKTREE_BRANCH.match(branch) and not payload.get("deleted") and head_sha:
        # A worktree branch's first push asks for its draft pull request; the background pass opens or reuses it
        # (reconcile_worktree_prs), so nothing here calls GitHub inside the write and the push's checks run as usual.
        for link in H._rows(c.execute("SELECT id,detail_json FROM task_links WHERE kind='worktree' AND lower(repo)=? "
                                       "AND branch=?", (repo.lower(), branch))):
            detail = H._json(link["detail_json"], {}) or {}
            detail.update(pushed=head_sha, pushed_at=detail.get("pushed_at") or H.now())
            c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps(detail), link["id"]))
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


DEPLOY_QUERY = ("SELECT l.*,p.seq AS merge_seq FROM task_links l JOIN tasks t ON t.id=l.task_id "
               "LEFT JOIN main_pushes p ON p.sha=l.pr_sha "
               "WHERE l.kind='pr' AND l.state='merged' AND l.url LIKE ? ESCAPE '\\' "
               "AND l.pr_sha IS NOT NULL AND t.status NOT IN ('done','closed')")


def refresh_deployed_tasks(service):
    """Verify release ancestry outside transactions, then mark the contained links shipped."""
    settings = service.settings
    repo, commit = settings.release_repo.lower(), settings.release_commit
    if (settings.rehearsal or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)
            or not re.fullmatch(r"[0-9a-f]{40}", commit)):
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
            shas = [r[0] for r in c.execute(
                "SELECT DISTINCT l.pr_sha FROM task_links l JOIN tasks t ON t.id=l.task_id "
                "WHERE t.status NOT IN ('done','closed') AND l.kind='pr' AND l.state='merged' AND lower(l.repo)=? "
                "AND l.pr_sha IS NOT NULL", (repo,))]
        keys = [(repo, sha, commit) for sha in shas if sha != commit and re.fullmatch(r"[0-9a-f]{40}", sha)]
        cache = getattr(service, "deploy_refresh_cache", {})
        due = sorted((key for key in keys if not cache.get(key, {}).get("verified")
                      and cache.get(key, {}).get("next", 0) <= now),
                     key=lambda key: cache.get(key, {}).get("next", 0))[:20]
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
                    response = service._call("GET", f"/repos/{quote(repo, safe='/')}/compare/{key[1]}...{commit}",
                                             params={"per_page": 1}, headers={"Authorization": "Bearer " + token})
                    if response.status_code == 200:
                        data = response.json()
                        verified = (data.get("status") in ("ahead", "identical")
                                    and (data.get("base_commit") or {}).get("sha") == key[1]
                                    and (data.get("merge_base_commit") or {}).get("sha") == key[1])
                    if verified and change_kept(lambda path, **params: _read(service, token, path, params),
                                                repo, key[1], commit) is False:
                        # Both commits are fixed, so the answer is too: never asked again for this release.
                        verified, reverted = False, True
                        logging.getLogger("tico.github").info("Release %s reverts %s in %s: not shipped",
                                                              commit[:12], key[1][:12], repo)
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


# What a release kept of a pull request is read hunk by hunk: release notes move between sections at every cut and
# tests are cut routinely, so the product files decide when the pull request has any.
NOTES_FILES = {"CHANGELOG.md"}
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
    return any(lines[i:i + n] == block for i in range(len(lines) - n + 1) if lines[i] == block[0])


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
    if len(parents) > 1:   # a merge commit: the pull request is what it changed on top of main
        files = (get(f"{base}/compare/{parents[0]['sha']}...{merge}") or {}).get("files") or []
    files = [f for f in files if f.get("filename") not in NOTES_FILES]
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
            return True    # the file is as the merge left it
        old = at_release(f.get("previous_filename") or f["filename"])[0]
        for before, after in hunks:
            if after and _contains(new, after):
                return True    # this part of the change is still there
            if before and not _contains(old, before):
                return True    # changed again since: not a revert
    return None if unknown else False


def checked_on_github(c, settings, repo):
    """`refresh_deployed_tasks` can ask GitHub about this release, so it decides what ships, reverts included."""
    app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    return bool(not settings.rehearsal and app and repo.split("/")[0].lower() == str(app["org"]).lower()
                and c.execute("SELECT 1 FROM repositories WHERE lower(full_name)=? AND reachable=1",
                              (repo.lower(),)).fetchone())


def ship_deployed(c, settings, *, ancestry=None):
    """Every merged pull request link in the running release is `shipped`; the task itself does not move."""
    commit, repo = settings.release_commit, settings.release_repo
    if not commit or not repo:
        return []
    # Push order alone can't see a revert; it ships only where GitHub can't be asked.
    here = (ancestry is None and not checked_on_github(c, settings, repo)
            and c.execute("SELECT seq FROM main_pushes WHERE sha=?", (commit,)).fetchone())
    shipped = []
    candidates = H._rows(c.execute(DEPLOY_QUERY,
        ("https://github.com/" + repo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/pull/%",)))

    def included(link):
        match = PR_LINK.match(link["url"])
        if not match or f"{match.group(1)}/{match.group(2)}".lower() != repo.lower():
            return False
        verified = (repo.lower(), link["pr_sha"], commit) in (ancestry or ())
        historical = ancestry is None and here and link["merge_seq"] and link["merge_seq"] <= here["seq"]
        return bool(link["pr_sha"] == commit or verified or historical)

    for link in candidates:
        with isolated(c, "ship_deployed", link["task_id"]):
            if not included(link):
                continue
            c.execute("UPDATE task_links SET state='shipped',updated=? WHERE id=?", (H.now(), link["id"]))
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), link["task_id"]))
            detail = H._json(link.get("detail_json"), {}) or {}
            item = f"Shipped: {link['title']} is in the running release {commit[:12]} {link['url']}"
            if fire_once(detail, item):
                c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps(detail), link["id"]))
                queue_wake(c, H.task(c, link["task_id"]), item)
            evidence = {"release": commit, "url": link["url"]}
            if (repo.lower(), link["pr_sha"], commit) in (ancestry or ()):
                evidence["ancestry"] = {"repository": repo.lower(), "merge": link["pr_sha"], "deployed": commit,
                                        "comparison": f"https://github.com/{repo}/compare/{link['pr_sha']}...{commit}"}
            H.event(c, H.KEEPER, "github.shipped", link["task_id"], evidence)
            if link["task_id"] not in shipped:
                shipped.append(link["task_id"])
    return shipped


def install_github(app, settings, store):
    @app.post(PATH)
    async def webhook(request: Request):
        body = await request.body()
        app_secret = app.state.github_app.webhook_secret()
        if not (verify(settings.github_webhook_secret, request.headers, body) or verify(app_secret, request.headers, body)):
            # unset: nothing to verify against, and the path does not exist for anyone
            raise Problem("forbidden", "Bad signature", 403 if settings.github_webhook_secret else 404)
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            raise Problem("payload", "Send JSON", 400)
        event = str(request.headers.get("x-github-event") or "")
        if event == "ping":
            return {"ok": True}
        if event in ("installation", "installation_repositories"):
            from .repositories import queue_sync
            # Accepting new permissions arrives here: a turn asks for all its reads again at once.
            app.state.github_app.live.pop("turn-reads", None)
            app.state.github_app.live.pop("workflows-refused", None)
            queue_sync(app.state.github_app, refresh=True)
            return {"ok": True}
        with store.transaction() as c:
            if event == "pull_request":
                result = pull_request(c, payload, review_role=settings.github_review_role,
                                      release_repo=settings.release_repo)
            elif event in ("check_run", "check_suite", "status", "pull_request_review", "pull_request_review_comment"):
                result = pr_signal(c, event, payload)
            elif event == "push":
                result = push(c, payload)
                result["shipped"] = ship_deployed(c, settings)
            else:
                return Response(status_code=204)
        return {"ok": True, **result}
