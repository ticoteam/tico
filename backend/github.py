"""GitHub keeps the pull requests attached to a task up to date; it never moves the task.

A task carries its pull request as a link (`hub task link`). A link is information only: opening,
merging or closing the PR, a release tag or a deploy changes the link, never the task's status or
step. A person moves the task. The repository's webhook posts here signed with
`TICO_GITHUB_WEBHOOK_SECRET`; nothing else on this path is trusted:

- a pull request opened, merged or closed -> the link is `open`, `draft`, `merged` or `closed`
- checks, conflicts and reviews     -> specific owner notices grouped within three minutes
- a review requested or withdrawn   -> that person is on or off the task in `github_review_role`
- a push to main                    -> the commits are recorded, in order, in `main_pushes`

The last hop is a fact of the deploy, not of GitHub: the release manifest names the commit the
running code was built from (`release.py` writes it from CI), and `ship_deployed` marks a merged
link `shipped` once its merge commit is at or before that commit on main. Only the repository this
release came from ships that way; a merged link in another repository stays `merged`.
"""
import hashlib
import hmac
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


def queue_wake(c, task, item):
    """Keep one bounded, durable burst per task; the scheduler sends it within three minutes."""
    from .repositories import metadata, save_metadata
    key = "github-task-wake:" + task["id"]
    burst = metadata(c, key)
    items = burst.get("items", [])
    if item not in items:
        items = (items + [item[:1000]])[-50:]
    save_metadata(c, key, {"due": burst.get("due") or H.shift(H.now(), seconds=170), "items": items})


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
                H._wake(c, task, task["owner"], "\n".join(burst.get("items", [])))
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


def pull_request(c, payload, review_role=""):
    pr = payload.get("pull_request") or {}
    url = str(pr.get("html_url") or "").split("?")[0].rstrip("/")
    action = str(payload.get("action") or "")
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
        if (state == "closed" and link["state"] != state
                or conflict_wake):
            queue_wake(c, task, item)
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
        wake = False
        if event in ("check_run", "check_suite", "status"):
            if signal.get("head_sha") and detail.get("head_sha") and signal["head_sha"] != detail["head_sha"]:
                continue
            result = signal.get("conclusion") or signal.get("state") or "pending"
            state = "passing" if result in ("success", "neutral", "skipped") else (
                    "pending" if result in ("pending", "queued", "in_progress", "requested", "waiting") else "failing")
            checks = detail.get("checks", {})
            name = str("suite:" + str((signal.get("app") or {}).get("slug") or signal.get("id") or "Checks")
                       if event == "check_suite" else signal.get("name") or signal.get("context") or "Checks")[:200]
            wake = state == "failing" and checks.get(name) != "failing"
            checks[name] = state
            detail["checks"] = dict(list(checks.items())[-100:])
            fields["checks"] = "failing" if "failing" in checks.values() else "pending" if "pending" in checks.values() else "passing"
            item = f"Checks {state} on {label}: {name}"
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
        changed = any(value != link.get(field) for field, value in fields.items())
        fields.update(detail_json=json.dumps(detail), updated=H.now())
        if changed:
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), task["id"]))
        c.execute("UPDATE task_links SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                  (*fields.values(), link["id"]))
        if wake:
            queue_wake(c, task, item)
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
                                         review_role=service.settings.github_review_role)
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
                verified = False
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
                except Exception:
                    logging.getLogger("tico.github").warning("Release ancestry unavailable for %s at %s", repo, key[1])
                with service.lock:
                    if key not in cache and len(cache) >= 512:
                        cache.pop(next(iter(cache)))
                    cache[key] = {"verified": verified, "next": time.monotonic() + 300}
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


def ship_deployed(c, settings, *, ancestry=None):
    """Every merged pull request link in the running release is `shipped`; the task itself does not move."""
    commit, repo = settings.release_commit, settings.release_repo
    if not commit or not repo:
        return []
    here = c.execute("SELECT seq FROM main_pushes WHERE sha=?", (commit,)).fetchone()
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
            queue_sync(app.state.github_app, refresh=True)
            return {"ok": True}
        with store.transaction() as c:
            if event == "pull_request":
                result = pull_request(c, payload, review_role=settings.github_review_role)
            elif event in ("check_run", "check_suite", "status", "pull_request_review", "pull_request_review_comment"):
                result = pr_signal(c, event, payload)
            elif event == "push":
                result = push(c, payload)
                result["shipped"] = ship_deployed(c, settings)
            else:
                return Response(status_code=204)
        return {"ok": True, **result}
