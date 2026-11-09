"""A bot whose GitHub repository could not be created because the GitHub App lacks Administration.

Creating `<org>/bot-<slug>` needs the App's Administration (write) permission. Without it a person must
act: create the repository on GitHub, or grant the permission. The server puts one Needs-you task in
front of the owner per bot (never one per attempt), keeps the bot as it is (planned, with its files on
the computer that built it), and finishes on its own once either fix is in place: it creates the empty
repository when the permission appears, or finds the repository a person made. The bot's runner then
publishes the local history into it (runner_support/git_credentials.publish_history), as after any empty create.

Triggers: the GitHub App's installation webhooks (a permission accepted, repositories added), the
owner's "Check again" in Tools > GitHub, and a scheduler tick every few minutes while anything waits.
"""

import json
import logging
import threading
import time
from urllib.parse import urlencode

from . import hubdb as H
from .store import Problem

log = logging.getLogger("tico.repo_waits")

KEY = "github-repo-waits"  # registry_metadata: {bot: {"repository", "task_id", "template", "since"}}
OWNER_KEY = "github-app-owner"  # the owner's account type once lived here; the App's row holds it now, so a disconnect clears it
CHECK_EVERY = 300  # seconds between scheduler checks while a bot waits
_lock = threading.Lock()
_last = {"at": 0.0}


def _load(c, key):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _save(c, key, value):
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, sort_keys=True)),
    )


def waits(c):
    return _load(c, KEY)


def forget(c):
    c.execute("DELETE FROM registry_metadata WHERE key IN (?,?)", (OWNER_KEY, KEY))


def permissions_url(c, row):
    """The App's permissions page on GitHub, where Administration is turned on (backend/github_app.py `links`)."""
    from .github_app import links

    return links(row)["permissions_url"]


def create_url(org, name=""):
    """GitHub's new-repository page with the owner, name and private visibility filled in."""
    query = {"owner": org, "visibility": "private"}
    if name:
        query["name"] = name
    return "https://github.com/new?" + urlencode(query)


def fixes(c, row, slug=""):
    """The two ways out, as links: create the repository (prefilled), or let BotOps create repositories. In a personal
    account the second is authorizing Tico to create empty repositories there, which is shown in the task instead."""
    from .github_app import account_kind

    name = H.bot_repository_name(c, slug) if slug else ""  # named for the bot, id or slug in
    return {
        "repository": f"{row['org']}/{name}" if name else "",
        "account_type": account_kind(row),
        "create_url": create_url(row["org"], name),
        "permissions_url": permissions_url(c, row),
    }


def _body(found):
    repository = found["repository"]
    if found.get("account_type") == "User":
        return (
            f"BotOps could not create the GitHub repository {repository}. The bot is built and waits on its computer "
            "until the repository exists. Either is enough:\n\n"
            "1. Authorize Tico in Tools > GitHub: Tico then creates the empty repository in your account itself.\n"
            f"2. Create {repository} on GitHub as an empty private repository (no README, no licence): "
            f"{found['create_url']}\n\n"
            "Tico checks every few minutes and finishes on its own: the bot's computer publishes its history, and this "
            "task closes. If the app is installed on selected repositories only, also add the new repository to the "
            "installation."
        )
    return (
        f"BotOps could not create the GitHub repository {repository}: the GitHub App was set up without "
        "permission to create repositories. The bot is built and waits on its computer until one of these "
        "is done. Either is enough:\n\n"
        f"1. Create {repository} on GitHub as an empty private repository (no README, no licence): "
        f"{found['create_url']}\n"
        f"2. Let BotOps create repositories: turn on Administration (Read and write) on {found['permissions_url']}, "
        "then accept the new permission for the organisation when GitHub asks.\n\n"
        "Tico checks every few minutes and finishes on its own: it creates the repository if needed, the bot's "
        "computer publishes its history, and this task closes. If the app is installed on selected "
        "repositories only, also add the new repository to the installation."
    )


def raise_item(c, bot, row, owner_id, template=""):
    """One Needs-you task per waiting bot, for `owner_id` (the owner, else an Admin). A repeated attempt finds the
    open one and returns it. With nobody who can change the app, no task is filed and Health's alert stands."""
    bot = (H.resolve_bot(c, bot) or {}).get(
        "id", bot
    )  # the waits are keyed by the bot's id, not the slug it was asked by
    found = fixes(c, row, bot)
    every = waits(c)
    current = every.get(bot) or {}
    task = H.task(c, current.get("task_id") or "") if current.get("task_id") else None
    if task and task["status"] in H.ACTIVE_STATUSES:
        return {**found, "task_id": task["id"], "created": False}
    owner = owner_id
    task_id = None
    if owner:
        try:
            made = H.task_create(
                c,
                H.KEEPER,
                f"Create {found['repository']} on GitHub"
                + ("" if found["account_type"] == "User" else ", or let BotOps create repositories"),
                _body(found),
                H.human_actor(owner),
                lint=False,
            )
            task_id = made["id"]
        except H.Refused:
            log.warning("Could not file the Needs-you task for %s's repository", bot)
    every[bot] = {
        "repository": found["repository"],
        "task_id": task_id,
        "template": template or "",
        "since": current.get("since") or H.now(),
    }
    _save(c, KEY, every)
    H.event(c, H.KEEPER, "github.repo_waiting", bot, {"repository": found["repository"], "task": task_id})
    return {**found, "task_id": task_id, "created": True}


def _finish(store, bot, wait, how):
    with store.transaction() as c:
        every = waits(c)
        if bot not in every:
            return
        every.pop(bot)
        _save(c, KEY, every)
        task_id = wait.get("task_id")
        row = H.task(c, task_id) if task_id else None
        if row and row["status"] != "closed":
            try:
                H.task_close(c, H.KEEPER, task_id, note=how, strict=False)
            except H.Refused:
                log.warning("Could not close the Needs-you task for %s's repository", bot)
        H.event(c, H.KEEPER, "github.repo_wait_resolved", bot, {"repository": wait.get("repository"), "how": how})


def resume(service):
    """Finish every waiting bot that can be finished now; answers {bot: how} for the ones that were."""
    with service.store.read() as c:
        pending = waits(c)
        row = service.row(c)
    if not pending:
        return {}
    done = {}
    if not row:
        return done
    with _lock:
        # A template repository needs Administration; an empty one in a personal account also the owner's authorization
        # (github_app.can_create_repos), so each waiting bot is checked against what its own repository needs.
        allowed = {"template": service.can_create_repos(refresh=True)}
        allowed["empty"] = service.can_create_repos(empty=True)
        for bot, wait in pending.items():
            try:
                how = _resume_one(service, row, allowed, bot, wait)
            except Exception as exc:
                # One bot's failure (GitHub unreachable, a bad record) never holds up the others.
                log.warning("Checking %s's waiting repository failed: %s", bot, type(exc).__name__)
                continue
            if how:
                done[bot] = how
    return done


def _resume_one(service, row, allowed, bot, wait):
    with service.store.read() as c:
        found = H.bot(c, bot) or {}
        name = H.bot_repository_name(c, bot) if found else ""
    state = found.get("state")
    if state in (None, "archived"):
        _finish(service.store, bot, wait, "The bot was archived; nothing was created.")
        return "archived"
    repository = wait.get("repository") or f"{row['org']}/{name}"
    how = None
    try:
        service.mint([repository], {"metadata": "read"}, diagnose=False)
        how = f"{repository} exists on GitHub now; the bot's computer publishes its history on its next run."
    except (Problem, ValueError, KeyError):
        pass
    template = wait.get("template") or ""
    if how is None and allowed["template" if template else "empty"]:
        try:
            # Empty for a bot built on a computer (its runner publishes the history); from the template
            # when that is what was asked for.
            service.create_repo(bot, template, empty=not template)
            how = f"BotOps created {repository}; the bot's computer publishes its history on its next run."
            with service.store.transaction() as c:
                H.event(
                    c,
                    H.KEEPER,
                    "github.repo_created",
                    repository,
                    {"template": template, "resumed": True} if template else {"empty": True, "resumed": True},
                )
        except Problem as problem:
            if problem.code == "github_repo_exists":
                how = f"{repository} exists on GitHub now; the bot's computer publishes its history on its next run."
            else:
                log.warning("Creating %s for a waiting bot failed: %s", repository, problem.code)
    if how:
        _finish(service.store, bot, wait, how)
    return how


def tick(service, force=False):
    """The scheduler's check: nothing to do unless a bot waits, and GitHub is asked at most every CHECK_EVERY."""
    if service is None:
        return {}
    with service.store.read() as c:
        if not waits(c):
            return {}
    if not force and time.time() - _last["at"] < CHECK_EVERY:
        return {}
    _last["at"] = time.time()
    return resume(service)
