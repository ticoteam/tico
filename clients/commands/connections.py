"""Connection tools: the team's GitHub App, its repositories and which bot may use each, and whether the company can
work right now. Each is a `tico` command and an MCP tool of the same name (`tico github status` is
`tico_github_status`), registered into the one table like any other module listed in clients/commands/__init__.py.

Every tool composes the ordinary routes with the caller's own token, so it can do nothing the HTTP API refuses: the
GitHub App routes are the Owner's, repository changes the Owner's or an admin's (backend/github_app.py,
backend/repositories.py). Pure stdlib, like hubtools: the runner venv, the cloud venv and the CLI import it.
"""

import html
import json
import os
from pathlib import Path
from urllib.parse import quote

from clients.tico import APIError

from clients.commands._registry import TOOLS, tool
from clients.commands._requester import PEOPLE, REQUESTER, _as_person
from clients.commands._schemas import _key, _s

_FIRST = len(TOOLS)  # the tools below are TOOLS[_FIRST:]

REDIRECTS = (301, 302, 303, 307, 308)
# Owners and admins act; BotOps acts as the person who asked it. Not offered to the Assistant (its listing is near its
# size cliff) nor to ordinary bots: connecting the company is an admin's job.
ADMINS = REQUESTER
OWNER_ONLY = PEOPLE  # the server admits only the Owner; a member or admin is told so by the route


def _bots(api):
    rows = api.get("bots")
    return rows.get("bots", []) if isinstance(rows, dict) else rows or []


def _repo_row(listing, name):
    wanted = str(name or "").strip().lower()
    return next((r for r in listing.get("repositories") or [] if r.get("full_name", "").lower() == wanted), None)


def _path(value):
    return quote(str(value), safe="")


def _not_listed(repository):
    return APIError(
        "not_found",
        f"{repository} is not in the team's repository list; tico repo refresh reads it from GitHub "
        "(the App must be installed on it)",
        404,
    )


def _listed(api, repository):
    """The repository's owner/repo as the team's list spells it; not_found when the list does not have it."""
    row = _repo_row(api.get("repositories"), repository)
    if not row:
        raise _not_listed(repository)
    return row["full_name"]


# ----------------------------------------------------------------------------- GitHub App
def _github_view(app, repos=None):
    """The App's status in plain words: what is connected, to which account, on which repositories, what is missing."""
    if not app.get("connected"):
        return {
            "connected": False,
            "installed": False,
            "summary": "GitHub is not connected.",
            "missing": [
                {
                    "key": "not_connected",
                    "detail": "No GitHub App is connected, so bots' files and memory are only on their computers.",
                    "fix": "tico github connect <account or organization> --html github.html",
                }
            ],
        }
    org, kind = app.get("org") or "", app.get("account_type") or ""
    missing = []
    if not app.get("installed"):
        missing.append(
            {
                "key": "not_installed",
                "detail": f"The App {app.get('slug')} is created but not installed on {org}.",
                "fix": "Open <company URL>/api/v2/github/app/install as the Owner, install it (All repositories is "
                "recommended), then run tico github installed",
            }
        )
    if app.get("checks_withheld"):
        missing.append(
            {
                "key": "checks_withheld",
                "detail": app["checks_withheld"],
                "fix": f"Set Checks to Read and write at {app.get('permissions_url') or 'the App settings'}, accept it "
                "on the installation, then run tico github check",
            }
        )
    if app.get("administration") is False:
        missing.append(
            {
                "key": "no_administration",
                "detail": "The App may not create repositories: a person creates every new bot's repository.",
                "fix": f"Set Administration to Read and write at {app.get('permissions_url') or 'the App settings'}, "
                "accept it on the installation, then run tico github check",
            }
        )
    if kind == "User" and not app.get("authorized"):
        missing.append(
            {
                "key": "not_authorized",
                "detail": "Tico is not authorized to create repositories in this personal account.",
                "fix": "Tools > GitHub > Authorize, as the Owner",
            }
        )
    if app.get("callback_url"):
        missing.append(
            {
                "key": "callback_url",
                "detail": "This App was made before authorization was part of setup and has no callback URL.",
                "fix": f"Add {app['callback_url']} as the App's Callback URL, then authorize again",
            }
        )
    view = {
        "connected": True,
        "installed": bool(app.get("installed")),
        "app": app.get("slug"),
        "org": org,
        "account_type": kind,
        "app_id": app.get("app_id"),
        "creates_repositories": bool(app.get("administration")),
        "missing": missing,
        "links": {
            k: app.get(k)
            for k in ("settings_url", "permissions_url", "install_url", "uninstall_url", "create_url")
            if app.get(k)
        },
    }
    if repos is not None:
        rows = repos.get("repositories") or []
        ticked = [r["full_name"] for r in rows if r.get("enabled") and not r.get("bot_repo")]
        unreachable = [r["full_name"] for r in rows if r.get("enabled") and not r.get("reachable")]
        bot_unreachable = [r["full_name"] for r in rows if r.get("bot_repo") and not r.get("reachable")]
        view["repositories"] = {
            "ticked": ticked,
            "seen": len(rows),
            "reachable": sum(1 for r in rows if r.get("reachable")),
            "new_bot_default": repos.get("new_bot_default"),
        }
        if unreachable:
            missing.append(
                {
                    "key": "ticked_unreachable",
                    "detail": "Ticked but the App cannot reach: " + ", ".join(unreachable[:10]),
                    "fix": "Add them to the App's installation (All repositories is recommended), then tico repo refresh",
                }
            )
        if bot_unreachable:
            missing.append(
                {
                    "key": "bot_repositories_unreachable",
                    "detail": f"{len(bot_unreachable)} bot repositories the App cannot reach, so their history is not "
                    "on GitHub: " + ", ".join(bot_unreachable[:5]) + (" ..." if len(bot_unreachable) > 5 else ""),
                    "fix": "Install the App on All repositories, then tico repo refresh",
                }
            )
    words = [f"Connected to {org}" + (f" ({kind})" if kind else "") + f" as the App {app.get('slug')}"]
    words.append("installed" if view["installed"] else "not installed")
    if repos is not None:
        ticked = view["repositories"]["ticked"]
        words.append(
            f"{len(ticked)} repositories ticked" + (": " + ", ".join(ticked[:5]) if ticked else "")
            if len(ticked) != 1
            else "1 repository ticked: " + ticked[0]
        )
    summary = "; ".join(words) + "."
    if missing:
        summary += " Missing: " + ", ".join(m["key"] for m in missing) + "."
    return {"summary": summary, **view}


def _status(api):
    app = api.get("github/app")
    try:
        repos = api.get("repositories")
    except APIError:
        repos = None
    return _github_view(app, repos)


@tool(
    "tico_github_status",
    "The team's GitHub App, in plain words (the Owner): connected or not, to which account or organization, installed "
    "or not, which repositories are ticked, and what is missing with its fix (checks_withheld: the App lacks Checks "
    "write; not_installed; bot repositories the App cannot reach...). Example: tico github status",
    {},
)
def github_status(api, args):
    return _status(api)


def manifest_page(action, manifest):
    """The page an agent opens in a browser: it posts the App manifest to GitHub, where the Owner clicks Create.
    It submits itself; the button is there when scripts are off."""
    value = html.escape(json.dumps(manifest), quote=True)
    return (
        "<!doctype html>\n<meta charset=utf-8>\n<title>Connect GitHub to Tico</title>\n"
        f'<form id=f method=post action="{html.escape(action, quote=True)}">\n'
        f'<input type=hidden name=manifest value="{value}">\n'
        "<p>GitHub asks you to create the App: check the name, then click Create GitHub App.</p>\n"
        "<button type=submit>Continue to GitHub</button>\n</form>\n"
        "<script>document.getElementById('f').submit()</script>\n"
    )


@tool(
    "tico_github_connect",
    "Start connecting a GitHub App (the Owner), from an agent: answers the App manifest, GitHub's create address and a "
    "single-use `state`, and `page_html`, the page to open in a browser signed in to GitHub as an owner of the account "
    "or organization; it posts the manifest and the person clicks Create GitHub App. Then tico_github_finish with the "
    "state. With no `org`, GitHub makes the App in the personal account signed in there. Refused when GitHub is already "
    "connected. Example: tico github connect --html github.html (a personal account), tico github connect acme-co",
    {
        "org": _s("The GitHub organization the App is made under; leave out for your own GitHub account"),
        "name": _s("The App's name; default '<company> Tico'"),
        "administration": {
            "type": "boolean",
            "description": "Let BotOps create bot repositories (Administration: write). Default true",
        },
    },
)
def github_connect(api, args):
    org = (args.get("org") or "").strip()
    query = {"org": org or None, "name": args.get("name") or None}
    if args.get("administration") is False:
        query["administration"] = "false"
    made = api.get("github/app/manifest", **query)
    return {
        "state": made["state"],
        "action": made["action"],
        "manifest": made["manifest"],
        "page_html": manifest_page(made["action"], made["manifest"]),
        "next": [
            f"Open the page in a browser signed in to GitHub as an owner of {org}, and click Create GitHub App"
            if org
            else "Open the page in a browser signed in to GitHub as the account the App is for, and click Create GitHub App",
            f"tico github finish {made['state']}: answers install_url once GitHub has sent the App back",
            "Open install_url, choose All repositories (recommended), click Install",
            "tico github installed: records the install; then tico github status",
        ],
    }


@tool(
    "tico_github_finish",
    "Finish the App GitHub created (the Owner): Tico exchanges the code GitHub sent back for this `state` once, keeps "
    "the App's secrets and answers `install_url`, the App's install page. 409 github_not_returned: the Create click "
    "has not happened yet. Example: tico github finish <state>",
    {"state": _s("The state tico_github_connect answered")},
    required=("state",),
    writes=True,
)
def github_finish(api, args):
    done = api.post("github/app/finish", {"state": args["state"]}, key=_key(args))
    return {
        **done,
        "next": [
            "Open install_url in the browser, choose All repositories (recommended), click Install",
            "tico github installed: records the install; then tico github status",
        ],
    }


@tool(
    "tico_github_installed",
    "Record the App's install after the Install click on GitHub (the Owner): Tico reads the installation again and "
    "syncs its repositories, then answers the App's status. Example: tico github installed",
    {},
)
def github_installed(api, args):
    try:
        api.get("github/app/installed")
    except APIError as exc:
        if getattr(exc, "status", 0) not in REDIRECTS:  # the route answers a browser with a redirect to Tools
            raise
    return {"recorded": True, **_status(api)}


@tool(
    "tico_github_check",
    "Read the App's permissions from GitHub again, after the Owner accepted new ones on the installation (Checks or "
    "Administration write), and answer the status. Example: tico github check",
    {},
    writes=True,
)
def github_check(api, args):
    app = api.post("github/app/check-permissions", {}, key=_key(args))
    try:
        repos = api.get("repositories")
    except APIError:
        repos = None
    return _github_view(app, repos)


@tool(
    "tico_github_disconnect",
    "Forget the GitHub App here (the Owner): bots stop getting GitHub tokens. The App stays on GitHub until someone "
    "uninstalls or deletes it there. `confirm` must be the connected account or organization. "
    "Example: tico github disconnect --confirm acme",
    {"confirm": _s("The connected account or organization's exact name")},
    required=("confirm",),
    writes=True,
)
def github_disconnect(api, args):
    app = api.get("github/app")
    if not app.get("connected"):
        return {"ok": True, "note": "GitHub is not connected."}
    if args["confirm"] != app.get("org"):
        raise APIError(
            "confirmation_required",
            f"To disconnect, confirm the connected account exactly: --confirm {app.get('org')}; nothing changed",
            409,
        )
    return api.post("github/app/disconnect", {}, key=_key(args))


# ----------------------------------------------------------------------------- repositories
def _own(bot, name):
    """Whether `name` is the bot's own repository (its `repo`, bare or owner/repo)."""
    own = str(bot.get("repo") or "").strip().lower()
    return bool(own) and (name.lower() == own or name.lower().split("/")[-1] == own.split("/")[-1])


def _grant_view(bot, access_row, name):
    """How `bot` (its row in GET bots) reaches `name` now: access, via own, all or chosen, and its workflow flags;
    a chosen grant on a repository that is not ticked has applies false. None when it does not reach it."""
    mode = access_row.get("mode")
    effective = next(
        (r for r in access_row.get("effective") or [] if r.get("full_name", "").lower() == name.lower()), None
    )
    chosen = next((r for r in access_row.get("chosen") or [] if r.get("full_name", "").lower() == name.lower()), None)
    if effective:
        via = "own" if _own(bot, name) else "chosen" if mode == "chosen" and chosen else mode
        row = {"access": effective.get("access"), "via": via}
        source = chosen if via == "chosen" else effective
        row.update({flag: True for flag in ("run_workflows", "edit_workflows") if (source or {}).get(flag)})
        return row
    if chosen and mode == "chosen":
        return {"access": chosen.get("access"), "via": "chosen", "applies": False}
    return None


@tool(
    "tico_repo_show",
    "One repository (a teammate reads it): whether it is ticked and reachable, its setup command, and every bot that may "
    "read or write it, how (own repository, All ticked repos or chosen) and whether it may run workflows or edit "
    "workflow files. A chosen grant on a repository that is not ticked is listed with applies false. "
    "Example: tico repo show acme/web",
    {"repository": _s("owner/repo")},
    required=("repository",),
)
def repo_show(api, args):
    person = _as_person(api)
    listing = person.get("repositories")
    row = _repo_row(listing, args["repository"])
    if not row:
        raise _not_listed(args["repository"])
    name = row["full_name"]
    bots = []
    for bot in _bots(person):
        slug = bot.get("slug") or bot.get("id")
        if not slug or bot.get("state") == "archived":
            continue
        try:
            access_row = person.get(f"bots/{_path(slug)}/repositories")
        except APIError as exc:
            if exc.status in (403, 404):
                continue
            raise
        found = _grant_view(bot, access_row, name)
        if found:
            bots.append({"bot": slug, **found})
    return {
        "repository": row,
        "bots": bots,
        "writers": [b["bot"] for b in bots if b["access"] == "write" and b.get("applies", True)],
        "readers": [b["bot"] for b in bots if b["access"] == "read" and b.get("applies", True)],
    }


GRANT_FLAGS = {
    "run_workflows": {
        "type": "boolean",
        "description": "Write only, Owner or admin: may start, rerun and cancel workflow runs. Left out: unchanged",
    },
    "edit_workflows": {
        "type": "boolean",
        "description": "Write only, Owner or admin: may change files under .github/workflows. Left out: unchanged",
    },
}


@tool(
    "tico_repo_grant",
    "Let one bot read or write one repository (the Owner or an admin; BotOps as the person who asked), keeping the "
    "bot's other chosen repositories: a bot on its own repository only switches to chosen with this one. A bot on All "
    "ticked repos is refused unless that already covers it (choose its repositories with tico bot repos). `tick` "
    "ticks the repository first when it is not, since a grant applies only to a ticked one. "
    "Example: tico repo grant acme/web builder write --tick",
    {
        "repository": _s("owner/repo"),
        "bot": _s("The bot's slug"),
        "access": _s("read or write (default write)", enum=["read", "write"]),
        "tick": {"type": "boolean", "description": "Tick the repository too when it is not ticked yet"},
        **GRANT_FLAGS,
    },
    required=("repository", "bot"),
    writes=True,
)
def repo_grant(api, args):
    person = _as_person(api)
    bot, access = args["bot"], args.get("access") or "write"
    flags = {k: args[k] for k in GRANT_FLAGS if args.get(k) is not None}
    if access == "read" and any(flags.values()):
        raise ValueError("Running workflows and editing workflow files need write access")
    row = _repo_row(person.get("repositories"), args["repository"])
    if not row:
        raise _not_listed(args["repository"])
    name = row["full_name"]
    current = person.get(f"bots/{_path(bot)}/repositories")
    out = {"bot": bot, "repository": name, "access": access, "ticked": bool(row.get("enabled")), "was": current["mode"]}
    if not row.get("enabled") and args.get("tick"):
        person.call("PUT", "repositories/" + name, {"enabled": True}, key=_key(args, ":tick"))
        out["ticked"] = out["ticked_now"] = True
    if current["mode"] == "all":
        if out["ticked"] and (current.get("all_access") == "write" or access == "read") and not flags:
            return {**out, "changed": False, "detail": f"{bot} has All ticked repos ({current.get('all_access')})"}
        raise APIError(
            "repository_access_all",
            f"{bot} has All ticked repos ({current.get('all_access')}); to give it {access} on {name} alone, choose "
            f"its repositories instead: tico bot repos {bot} --chosen {name}:{access} ...",
            409,
        )
    chosen = [
        {"full_name": r["full_name"], "access": r["access"]}
        for r in (current.get("chosen") or [] if current["mode"] == "chosen" else [])
        if r["full_name"].lower() != name.lower()
    ]
    chosen.append({"full_name": name, "access": access, **flags})
    result = person.call("PUT", f"bots/{_path(bot)}/repositories", {"mode": "chosen", "chosen": chosen}, key=_key(args))
    out.update(changed=True, effective=(result or {}).get("effective"))
    if not out["ticked"]:
        out["warning"] = f"{name} is not ticked: the grant applies once it is (tico repo tick {name}, or --tick)"
    return out


@tool(
    "tico_repo_revoke",
    "Take one repository away from one bot (the Owner or an admin), keeping its other chosen repositories. A bot always "
    "keeps its own repository; a bot on All ticked repos is refused (choose its repositories with tico bot repos). "
    "Example: tico repo revoke acme/web reviewer-1",
    {"repository": _s("owner/repo"), "bot": _s("The bot's slug")},
    required=("repository", "bot"),
    writes=True,
)
def repo_revoke(api, args):
    person = _as_person(api)
    bot, name = args["bot"], str(args["repository"]).strip()
    current = person.get(f"bots/{_path(bot)}/repositories")
    if current["mode"] == "all":
        raise APIError(
            "repository_access_all",
            f"{bot} has All ticked repos; untick {name} for everyone (tico repo untick {name}) or choose its "
            f"repositories: tico bot repos {bot} --chosen ...",
            409,
        )
    chosen = current.get("chosen") or [] if current["mode"] == "chosen" else []
    kept = [
        {"full_name": r["full_name"], "access": r["access"]} for r in chosen if r["full_name"].lower() != name.lower()
    ]
    if len(kept) == len(chosen):
        own = any(r.get("full_name", "").lower() == name.lower() for r in current.get("effective") or [])
        return {
            "bot": bot,
            "repository": name,
            "changed": False,
            "detail": f"{name} is {bot}'s own repository; a bot always keeps it"
            if own
            else f"{bot} has no grant on {name}",
        }
    result = person.call("PUT", f"bots/{_path(bot)}/repositories", {"mode": "chosen", "chosen": kept}, key=_key(args))
    return {"bot": bot, "repository": name, "changed": True, "effective": (result or {}).get("effective")}


@tool(
    "tico_repo_refresh",
    "Read the repository list from GitHub again (the Owner or an admin): what the App's installation reaches now. "
    "Example: tico repo refresh",
    {},
    writes=True,
)
def repo_refresh(api, args):
    listing = _as_person(api).post("repositories/refresh", {}, key=_key(args))
    rows = listing.get("repositories") or []
    return {
        "seen": len(rows),
        "reachable": sum(1 for r in rows if r.get("reachable")),
        "ticked": [r["full_name"] for r in rows if r.get("enabled") and not r.get("bot_repo")],
        "github_installed": listing.get("github_installed"),
    }


@tool(
    "tico_repo_default",
    "The repository access a new bot starts with: own (its own repository only) or all (All ticked repos). Without "
    "`mode` it reads it; setting it is the Owner's or an admin's. Example: tico repo default own",
    {"mode": _s("own or all", enum=["own", "all"])},
    writes=True,
)
def repo_default(api, args):
    person = _as_person(api)
    if not args.get("mode"):
        return {"new_bot_default": person.get("repositories").get("new_bot_default")}
    return person.call("PUT", "repositories/settings", {"new_bot_default": args["mode"]}, key=_key(args))


@tool(
    "tico_repo_setup_command",
    "Set the command a computer runs after it clones a repository (the Owner or an admin), or clear it to hand the "
    "repository back to its own tico.json or conductor.json. Example: tico repo setup-command acme/web 'npm ci'",
    {"repository": _s("owner/repo"), "command": _s("The setup command; empty clears it", maxLength=4096)},
    required=("repository", "command"),
    writes=True,
)
def repo_setup_command(api, args):
    person = _as_person(api)
    name = _listed(person, args["repository"])
    return person.call("PUT", "repositories/" + name, {"setup_command": args["command"]}, key=_key(args))


# ----------------------------------------------------------------------------- is the company able to work
CHECKS_COVERED = {"computers", "github"}  # Health checks the readiness below says in its own words


def _bot_line(bot):
    return {"bot": bot.get("slug"), "name": bot.get("display_name") or bot.get("slug")}


@tool(
    "tico_health_ready",
    "Is this company able to work right now (a person, or BotOps for them): ready true or false, each blocker with its "
    "fix, then warnings. Blockers: no computer online; GitHub not connected or not installed (unless github false); a "
    "bot still planned or waiting on setup (fix: tico bot go-live); a quarantined bot; any Health check that is bad. "
    "`bots` limits the bot checks to those bots (the others' problems become warnings). The CLI exits 1 when not "
    "ready. Example: tico health ready --bot builder --bot reviewer-1",
    {
        "bots": {"type": "array", "items": {"type": "string"}, "description": "Only these bots must be ready (slugs)"},
        "github": {"type": "boolean", "description": "Whether GitHub must be connected and installed (default true)"},
    },
)
def health_ready(api, args):
    person = _as_person(api)
    health = person.get("health")
    bots = [b for b in _bots(person) if b.get("state") != "archived"]
    only = {str(b).strip().lower() for b in args.get("bots") or []}
    unknown = sorted(only - {str(b.get("slug") or "").lower() for b in bots})
    blockers, warnings = [], []
    if unknown:
        blockers.append(
            {"check": "unknown_bots", "detail": "No such bot: " + ", ".join(unknown), "fix": "tico bot list"}
        )

    def bucket(slug):
        return blockers if not only or str(slug).lower() in only else warnings

    checks = {c.get("id"): c for c in health.get("checks") or []}
    computers = health.get("computers") or []
    online = [c.get("label") or c.get("id") for c in computers if c.get("online")]
    offline = [c.get("label") or c.get("id") for c in computers if not c.get("online")]
    if computers and not online or not computers and checks.get("computers", {}).get("status") == "bad":
        blockers.append(
            {
                "check": "computers",
                "detail": checks.get("computers", {}).get("summary") or "No computer is online.",
                "fix": "Turn the cloud machines on, or start a computer (tico computer list)",
            }
        )

    github = None
    try:
        github = _status(person)
    except APIError as exc:
        if exc.status != 403:
            raise
    if github is None:  # only the Owner reads the App itself; Health says enough for everyone else
        check = checks.get("github") or {}
        github = {"connected": check.get("status") == "ok", "summary": check.get("summary") or "Not reported."}
        github_ok = check.get("status") == "ok"
    else:
        github_ok = github.get("connected") and github.get("installed")
    if not github_ok:
        (blockers if args.get("github", True) else warnings).append(
            {"check": "github", "detail": github.get("summary"), "fix": "tico github status"}
        )

    planned, setup, quarantined, paused = [], [], [], []
    for bot in bots:
        slug = bot.get("slug")
        status = bot.get("status") if isinstance(bot.get("status"), dict) else {}
        if bot.get("state") == "planned":
            planned.append(slug)
            bucket(slug).append(
                {
                    "check": "bot_planned",
                    "bot": slug,
                    "detail": f"{bot.get('display_name') or slug} is planned: it takes no work until it goes live",
                    "fix": f"tico bot go-live {slug}",
                }
            )
        elif bot.get("onboarding_state") in ("needs_setup", "needs_onboarding"):
            setup.append(slug)
            bucket(slug).append(
                {
                    "check": "bot_needs_setup",
                    "bot": slug,
                    "detail": f"{bot.get('display_name') or slug} is still waiting on its setup",
                    "fix": f"tico bot go-live {slug}",
                }
            )
        if bot.get("state") == "quarantined" or status.get("state") == "quarantined":
            quarantined.append(slug)
            bucket(slug).append(
                {
                    "check": "bot_quarantined",
                    "bot": slug,
                    "detail": f"{bot.get('display_name') or slug} is quarantined: its queued work waits",
                    "fix": "tico health check (BotOps reviews it); it resumes after the cooldown",
                }
            )
        if bot.get("state") == "paused":
            paused.append(slug)
            (blockers if only and str(slug).lower() in only else warnings).append(
                {
                    "check": "bot_paused",
                    "bot": slug,
                    "detail": f"{bot.get('display_name') or slug} is paused",
                    "fix": f"tico bot resume {slug}",
                }
            )

    for key, check in checks.items():
        said = key in CHECKS_COVERED or key == "quarantine" and quarantined  # already itemized above
        if said or check.get("status") not in ("bad", "warn"):
            continue
        line = {"check": key, "detail": check.get("summary"), "status": check.get("status")}
        (blockers if check["status"] == "bad" else warnings).append(line)

    ready = not blockers
    return {
        "ready": ready,
        "summary": ("Ready" if ready else f"Not ready: {len(blockers)} blocker" + ("s" if len(blockers) != 1 else ""))
        + (f", {len(warnings)} warnings." if warnings else "."),
        "blockers": blockers,
        "warnings": warnings,
        "github": {k: github.get(k) for k in ("connected", "installed", "org", "app", "summary") if k in github},
        "computers": {"online": online, "offline": offline},
        "bots": {
            "total": len(bots),
            "active": sum(1 for b in bots if b.get("state") == "active"),
            "planned": planned,
            "needs_setup": setup,
            "quarantined": quarantined,
            "paused": paused,
        },
        "checked": health.get("checked"),
    }


# ----------------------------------------------------------------------------- registration
MINE = TOOLS[_FIRST:]
AUDIENCE = {
    **{t["name"]: ADMINS for t in MINE},
    **{
        name: OWNER_ONLY
        for name in (
            "tico_github_status",
            "tico_github_connect",
            "tico_github_finish",
            "tico_github_installed",
            "tico_github_check",
            "tico_github_disconnect",
        )
    },
}


# ----------------------------------------------------------------------------- the `tico` commands
def _group(sub, name):
    """The subcommands of an existing `tico <name>` group (built in clients/hubcli.py)."""
    import argparse

    return next(a for a in sub.choices[name]._actions if isinstance(a, argparse._SubParsersAction))


def _command(group, words, help_, example, fn):
    s = group.add_parser(words, help=help_, description=help_ + ". Example: " + example)
    s.set_defaults(fn=fn)
    return s


def add_commands(sub):
    """Add `tico github ...` and the connection verbs under `tico repo` to the CLI's parser. `tico health ready` is
    in HEALTH (clients/commands/computers.py)."""
    gh = sub.add_parser("github", help="the team's GitHub App: connect it from a terminal, see what it reaches")
    github = gh.add_subparsers(dest="sub")
    _command(
        github,
        "status",
        "the App: connected, to which account, installed, ticked repositories, what is missing (the Owner)",
        "tico github status",
        "github status",
    )
    s = _command(
        github,
        "connect",
        "start connecting an App: the manifest page to open in a browser and the state to finish with (the Owner)",
        "tico github connect --html github.html",
        "github connect",
    )
    s.add_argument(
        "org",
        nargs="?",
        default=None,
        help="the GitHub organization the App is made under; leave out for your own account",
    )
    s.add_argument("--name", help="the App's name; default '<company> Tico'")
    s.add_argument(
        "--no-administration",
        dest="administration",
        action="store_false",
        default=None,
        help="do not let BotOps create bot repositories",
    )
    s.add_argument("--html", dest="html_file", metavar="PATH", help="write the page to open in a browser here")
    s = _command(
        github,
        "finish",
        "after the Create click: keep the App's secrets and print its install page (the Owner)",
        "tico github finish <state>",
        "github finish",
    )
    s.add_argument("state", help="the state tico github connect printed")
    _command(
        github,
        "installed",
        "after the Install click: record the install, sync repositories, print the status (the Owner)",
        "tico github installed",
        "github installed",
    )
    _command(
        github,
        "check",
        "read the App's permissions again after accepting new ones on GitHub (the Owner)",
        "tico github check",
        "github check",
    )
    s = _command(
        github,
        "disconnect",
        "forget the App here; it stays on GitHub until uninstalled there (the Owner)",
        "tico github disconnect --confirm acme",
        "github disconnect",
    )
    s.add_argument("--confirm", required=True, metavar="ACCOUNT", help="the connected account's exact name")

    repo = _group(sub, "repo")
    s = _command(
        repo,
        "show",
        "one repository and every bot that may read or write it, and how (a teammate)",
        "tico repo show acme/web",
        "repo show",
    )
    s.add_argument("repository", metavar="OWNER/REPO")
    s = _command(
        repo,
        "grant",
        "let a bot read or write one repository, keeping its other chosen ones (the Owner or an admin)",
        "tico repo grant acme/web builder write --tick",
        "repo grant",
    )
    s.add_argument("repository", metavar="OWNER/REPO")
    s.add_argument("bot")
    s.add_argument("access", nargs="?", choices=["read", "write"], default="write")
    s.add_argument("--tick", action="store_true", default=None, help="tick the repository first if it is not")
    for flag, dest, words in (
        ("actions", "run_workflows", "run workflows there"),
        ("edit", "edit_workflows", "edit workflow files there"),
    ):
        on_off = s.add_mutually_exclusive_group()
        on_off.add_argument(f"--{flag}", dest=dest, action="store_true", default=None, help=f"may {words}")
        on_off.add_argument(f"--no-{flag}", dest=dest, action="store_false", default=None, help=f"may not {words}")
    s = _command(
        repo,
        "revoke",
        "take one repository away from a bot, keeping its other chosen ones (the Owner or an admin)",
        "tico repo revoke acme/web reviewer-1",
        "repo revoke",
    )
    s.add_argument("repository", metavar="OWNER/REPO")
    s.add_argument("bot")
    _command(
        repo,
        "refresh",
        "read the repository list from GitHub again (the Owner or an admin)",
        "tico repo refresh",
        "repo refresh",
    )
    s = _command(
        repo,
        "default",
        "the repository access a new bot starts with: own or all; without a mode, read it",
        "tico repo default own",
        "repo default",
    )
    s.add_argument("mode", nargs="?", choices=["own", "all"])
    s = _command(
        repo,
        "setup-command",
        "the command a computer runs after cloning the repository; '' clears it (the Owner or an admin)",
        "tico repo setup-command acme/web 'npm ci'",
        "repo setup-command",
    )
    s.add_argument("repository", metavar="OWNER/REPO")
    s.add_argument("command")


COMMANDS = frozenset(
    {
        "github status",
        "github connect",
        "github finish",
        "github installed",
        "github check",
        "github disconnect",
        "repo show",
        "repo grant",
        "repo revoke",
        "repo refresh",
        "repo default",
        "repo setup-command",
    }
)


def run_command(client, args):
    """One of COMMANDS: its tool, with the command's flags as the tool's arguments."""
    fields = {
        k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "subsub", "fn", "html_file") and v is not None
    }
    fields["operation_id"] = fields.get("operation_id") or os.environ.get("HUB_OPERATION_ID")
    if not fields["operation_id"]:
        del fields["operation_id"]
    name = "tico_" + args.fn.replace(" ", "_").replace("-", "_")
    from clients import hubtools

    result = hubtools.BY_NAME[name]["fn"](client, fields)
    if args.fn == "github connect" and isinstance(result, dict):
        page = result.pop("page_html", "")
        if getattr(args, "html_file", None):
            path = Path(args.html_file).expanduser().resolve()
            path.write_text(page)
            result["page"] = str(path)
            result["next"][0] = (
                f"Open {path} in a browser signed in to GitHub"
                + result["next"][0].split("a browser signed in to GitHub", 1)[-1]
            )
        else:
            result["page"] = "run again with --html PATH to write the page to open"
    return result


def exit_code(fn, result):
    """A command whose answer is a yes or no: 1 for no, so a script can wait on it. None for the rest."""
    if fn == "health ready":
        return 0 if isinstance(result, dict) and result.get("ready") else 1
    return None
