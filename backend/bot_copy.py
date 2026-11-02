"""Copy a bot, bring a copy up to date with its original, suggest a copy's changes back, and copy a skill between bots
(docs/creating-bots.md, "Copy a bot").

A copy is an ordinary bot that belongs to whoever asked. Nothing ties it to the original but `copied_from: {bot, sha}` on its
record, which two explicit requests read: update my copy from the original, and suggest this to the original.

The server holds no bot repository (backend/bot_tools.py), so these routes decide and record, and BotOps's computer moves the
files in the workspace (clients/botcopy.py): the route checks the requester's rights, registers the new bot within their
limit, copies the model settings, grants the credentials the requester may grant and answers with what is left to do. A
suggestion to the original is the exception that needs no repository here: the requester's files come in the request and the
GitHub App opens a pull request with them, or, when the requester may not write to the original, a task carries the diff to its
owner. Every route is one BotOps may call as the requester (backend/botops_act.py): the server's own checks are the gate.
"""
import base64
import json
import secrets
from urllib.parse import quote

from fastapi import Request

from clients import botcopy
from . import models as M
from . import built_on, providers
from .credentials import administrator, ask_admin_detail, effective_grant
from .github_app import TURN_PERMISSIONS, repo_of
from .harnesses import EXTERNAL_HARNESSES, resolve_harness
from .store import H, Problem, encode

READ_PERMISSIONS = {"contents": "read", "metadata": "read"}


def _json(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        value = None
    return value if isinstance(value, dict) else {}


def folder(c, bot):
    """The folder the bot's checkout has on a computer, as recorded (H.bot_folder): the client finds its files by this,
    never by its slug, which a rename changes while the folder stays."""
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return H.bot_folder(bot, _json(row["config"] if row else None))


def free_slug(c, original):
    base = original[:70] + "-copy"
    for n in range(1, 100):
        slug = base if n == 1 else f"{base}-{n}"
        if not H.resolve_bot(c, slug):
            return slug
    raise Problem("duplicate", "Give the copy a name of its own (slug)", 409)


def model_settings(c, settings_admin, settings, bot, config):
    """(choice, effort, harness) for the copy: the original's where it is a model the company still offers and runs, else the company's
    default. A bot run by an external agent (a Hermes profile) has no model of its own to copy."""
    company = providers.load(c, settings)
    choice = settings_admin.models.get(config.get("model"))
    harness = resolve_harness(config, None)
    effort = bot.get("effort") or config.get("reasoning_effort")
    if not choice or choice.get("deprecated") or harness in EXTERNAL_HARNESSES or (
            company["enabled"] and not providers.runtime_enabled(choice["runtime"], company["enabled"], choice)):
        choice = settings_admin.models.get(company.get("model")) or next(
            (m for m in settings_admin.models.values() if not m.get("deprecated")), None)
        effort = harness = None
        if not choice:
            raise Problem("model", "Choose the company's AI provider first (Settings > Providers)", 422)
    return choice, settings_admin._effort(choice, None, effort), settings_admin._harness(choice, None, harness)


def credentials_for(c, vault, who, original, slug, tools, needed):
    """What the original's tools need, as `{granted, needs}`: a credential the original holds from the vault is granted to the copy when
    the requester is a credential administrator; anything else is reported as "needs credential X" for BotOps to ask for."""
    wanted = {}
    for entry in [*tools, *needed]:
        if entry.get("env"):
            wanted.setdefault(entry["env"], entry.get("service") or "")
    granted, needs = [], []
    admin = administrator(c, who, vault.admins)
    for env, service in sorted(wanted.items()):
        held = [row for row in c.execute("SELECT * FROM credentials WHERE env=? AND ciphertext IS NOT NULL ORDER BY id", (env,))
                if effective_grant(c, row["id"], "bot:" + original)]
        item = {"env": env, "service": service}
        if not held:
            needs.append({**item, "needs": f"needs credential {env}", "detail": "The original has no stored credential for it."})
        elif not admin:
            needs.append({**item, "needs": f"needs credential {env}", "detail": ask_admin_detail(c, vault.admins, "give a bot a credential")})
        else:
            c.execute("SAVEPOINT copy_grant")
            try:
                vault.grant(c, who, held[0]["id"], "bot:" + slug)
            except Problem as exc:
                c.execute("ROLLBACK TO copy_grant")
                needs.append({**item, "needs": f"needs credential {env}", "detail": exc.detail})
            else:
                granted.append({**item, "credential": held[0]["name"]})
            c.execute("RELEASE copy_grant")
    return {"granted": granted, "needs": needs}


def install(app, store, auth, mutate, settings_admin, place_now):
    settings = store.settings

    def person(request):
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "Only people copy bots; BotOps does it as the person who asked", 403)
        return who

    def origin(c, who, bot):
        """The copy's record and what it was copied from, for a person who manages the copy and may read the original."""
        settings_admin._manager(c, who, bot)
        config = settings_admin._config(c, bot)
        source = _json(config["config"]).get("copied_from") or {}
        if not source.get("bot"):
            raise Problem("not_a_copy", "That bot was not made by copying another bot", 409)
        if not H.bot(c, source["bot"]):
            raise Problem("not_found", "The bot it was copied from no longer exists", 404)
        auth.require_read(c, who, source["bot"], "The bot it was copied from is not one you may read")
        return config, source

    def _declared(c, bot):
        """The original's tools as its computer last reported them: the variable each needs."""
        from .bot_tools import _state
        return [{"service": e.get("service") or "", "env": e.get("env") or ""} for e in _state(c, settings, bot)["raw"]
                if str(e.get("env") or "").isupper()]

    @app.post("/api/v2/bots/{bot}/copy")
    def copy_bot(request: Request, bot: str, body: M.BotCopy):
        """A new bot, planned, owned by the requester and reporting to them, from the original's model settings. Needs read on
        the original and counts toward the requester's bot limit like any bot they add."""
        who = person(request)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)

        def work(c):
            original = H.bot(c, bot)
            if not original:
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            if auth.system_bot(bot):
                raise Problem("system_bot", "Built-in bots cannot be copied; add one from the catalog instead", 409)
            row = settings_admin._config(c, bot)
            config = _json(row["config"])
            slug = body.slug or free_slug(c, original["slug"])
            display = body.display_name.strip() or ("Copy of " + original["display_name"])[:100]
            choice, effort, harness = model_settings(c, settings_admin, settings, original, config)
            created = settings_admin.create_bot(c, who, M.BotDefinitionCreate(
                slug=slug, display_name=display, description=row["description"] or "",
                reports_to="human:" + H.actor_id(who.actor), status="planned", repo="", model=choice["id"], effort=effort,
                harness=harness, owners=[]))
            copy_id = H.resolve_bot(c, slug)["id"]
            made = settings_admin._config(c, copy_id)
            declared = _json(made["config"])
            declared["copied_from"] = {"bot": bot, "sha": body.sha}
            c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(declared), copy_id))
            credentials = credentials_for(c, app.state.vault, who, bot, copy_id, [t.model_dump() for t in body.tools],
                                          _declared(c, bot))
            # The copy's history is made after this, in BotOps's workspace, so only the computer BotOps's turn runs on (its
            # assignment, for a card the person clicked) holds it until GitHub does: recorded, its first placement goes there
            # and another computer is refused, with GitHub or without (backend/built_on.py).
            if who.via == "botops":
                botops = c.execute("SELECT runner_id FROM assignments WHERE bot='botops'").fetchone()
                built_on.remember(c, copy_id, who.runner_id or (botops["runner_id"] if botops else ""))
            placed = None
            if body.computer:                            # an older BotOps; today's `hub bot copy` places after the build
                c.execute("SAVEPOINT copy_place")
                try:
                    placed = place_now(c, who, copy_id, body.computer, False)
                except Problem as exc:
                    c.execute("ROLLBACK TO copy_place")
                    placed = {"placed": False, "problem": exc.detail}
                c.execute("RELEASE copy_place")
            H.event(c, who.actor, "bot.copied", copy_id, {"from": bot, "sha": body.sha, "with_memory": body.with_memory,
                                                           "granted": [g["env"] for g in credentials["granted"]]})
            return {**created, "copied_from": {"bot": bot, "sha": body.sha}, "with_memory": body.with_memory,
                    "original_repo": row["repo"] or "emp-" + original["slug"], "original_folder": folder(c, bot),
                    "folder": folder(c, copy_id), "credentials": credentials,
                    **({"placement": placed} if placed else {})}
        return mutate(request, body, work)

    @app.post("/api/v2/bots/{bot}/repository-read-token")
    def repository_read_token(request: Request, bot: str, body: M.Empty):
        """A short-lived, read-only GitHub token for one bot's repository, for BotOps to clone it when that bot runs on another
        computer (a copy, an update or a skill is made from its files). Only BotOps acting for a person who may read the bot gets one."""
        who = person(request)
        if who.via != "botops":
            raise Problem("forbidden", "Only BotOps, acting for the person who asked, is given a token to read a bot's repository", 403)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            app_row = app.state.github_app.row(c)
            repo = repo_of(settings_admin._config(c, bot)["repo"] or "emp-" + H.bot(c, bot)["slug"], app_row["org"]) if app_row else None
        if not repo or repo.split("/")[0].lower() != app_row["org"].lower():
            return {"configured": False}
        token, expires = app.state.github_app.mint([repo], READ_PERMISSIONS)
        with store.transaction() as c:
            H.event(c, who.actor, "bot.repository_read", bot, {"repository": repo})
        return {"configured": True, "token": token, "expires_at": expires, "repository": repo}

    @app.post("/api/v2/bots/{bot}/update-from-original")
    def update_from_original(request: Request, bot: str, body: M.BotUpdateFromOriginal):
        """Without `applied_sha`: what this copy was copied from and at which commit, for the computer that holds both
        repositories to merge. With it: the commit the copy is now up to date with, once that merge is committed."""
        who = person(request)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        if not body.applied_sha:
            with store.read() as c:
                config, source = origin(c, who, bot)
                original = H.bot(c, source["bot"])["slug"]       # the client names its folder and words by slug
                return {"bot": bot, "original": original, "base_sha": source.get("sha") or "",
                        "folder": folder(c, bot), "original_folder": folder(c, source["bot"]),
                        "repo": config["repo"] or "bot-" + H.bot(c, bot)["slug"],
                        "original_repo": (settings_admin._config(c, source["bot"])["repo"]
                                          or "emp-" + H.bot(c, source["bot"])["slug"])}

        def work(c):
            config, source = origin(c, who, bot)
            declared = _json(config["config"])
            declared["copied_from"] = {"bot": source["bot"], "sha": body.applied_sha}
            c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(declared), bot))
            H.event(c, who.actor, "bot.copy_updated", bot, {"from": source["bot"], "before": source.get("sha") or "",
                                                            "after": body.applied_sha})
            return {"bot": bot, "original": H.bot(c, source["bot"])["slug"], "base_sha": body.applied_sha}
        return mutate(request, body, work)

    def pull_request(service, repo, title, text, files, branch):
        """A branch off the original's default branch with the files, and a pull request from it: the GitHub App's token for that one
        repository. The url of the pull request."""
        token, _ = service.mint([repo], TURN_PERMISSIONS)
        head = {"Authorization": "Bearer " + token}

        def call(method, path, expect, **kw):
            done = service._call(method, path, headers=head, **kw)
            if done.status_code not in expect:
                raise Problem("github_suggest", f"GitHub answered {done.status_code} to {method} {path.split('?')[0]}", 502)
            return done.json() if done.content else {}

        base = call("GET", f"/repos/{repo}", (200,))["default_branch"]
        start = call("GET", f"/repos/{repo}/git/ref/heads/{quote(base)}", (200,))["object"]["sha"]
        call("POST", f"/repos/{repo}/git/refs", (201,), json={"ref": "refs/heads/" + branch, "sha": start})
        for file in files:
            path = f"/repos/{repo}/contents/{quote(file.path)}"
            found = service._call("GET", path, headers=head, params={"ref": base})
            sha = found.json().get("sha") if found.status_code == 200 else None
            if file.content is None:
                if sha:
                    call("DELETE", path, (200,), json={"message": f"Remove {file.path}", "sha": sha, "branch": branch})
                continue
            call("PUT", path, (200, 201), json={"message": f"Update {file.path}", "branch": branch,
                                                "content": base64.b64encode(file.content.encode()).decode(),
                                                **({"sha": sha} if sha else {})})
        return call("POST", f"/repos/{repo}/pulls", (201,), json={"title": title, "head": branch, "base": base, "body": text})["html_url"]

    @app.post("/api/v2/bots/{bot}/suggest-to-original")
    def suggest_to_original(request: Request, bot: str, body: M.BotSuggest):
        """A copy's changes to its instructions, offered to the original: a pull request on the original's repository when the
        requester may write to it and GitHub is connected, else a task for the original's owner with the diff."""
        who = person(request)
        if sum(len(f.content or "") for f in body.files) > 1_500_000:
            raise Problem("too_large", "Suggest fewer or smaller files at once", 413)
        for file in body.files:
            if not (botcopy.safe_path(file.path) and botcopy.in_scope(file.path)):
                raise Problem("path", f"{file.path} is not part of a bot's instructions (AGENT.md, skills/, playbooks/)", 422)
        service = app.state.github_app
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            config, source = origin(c, who, bot)
            original = source["bot"]
            theirs = settings_admin._config(c, original)
            writes = auth.bot_access(c, who, original)["write"]
            app_row = service.row(c)
            repo = repo_of(theirs["repo"] or "emp-" + H.bot(c, original)["slug"], app_row["org"] if app_row else "") if app_row else None
            owner = theirs["operator"] if theirs["operator"] and H.human(c, theirs["operator"]) else ""
            names = {"copy": H.bot(c, bot)["display_name"], "original": H.bot(c, original)["display_name"],
                     "person": (H.human(c, H.actor_id(who.actor)) or {}).get("name") or H.actor_id(who.actor),
                     "copy_slug": H.bot(c, bot)["slug"], "original_slug": H.bot(c, original)["slug"]}
        paths = [f.path for f in body.files]
        text = "\n".join([
            f"{names['person']} suggests these changes to {names['original']}'s instructions, made in their copy {names['copy']} ({names['copy_slug']}).",
            "", "Files: " + ", ".join(paths),
            *(["Not included, because both changed them: " + ", ".join(body.held_back)] if body.held_back else []),
            "", "```diff", body.diff[:30_000], "```"])
        url, why = None, ""
        if writes and repo and repo.split("/")[0].lower() == app_row["org"].lower():
            try:
                url = pull_request(service, repo, body.title.strip() or f"Suggestion from {names['copy']}", text, body.files,
                                   f"tico/suggest-{names['copy_slug']}-{secrets.token_hex(3)}")
            except Problem as exc:
                why = exc.detail
        with store.transaction() as c:
            if url:
                H.event(c, who.actor, "bot.suggestion_made", bot, {"original": original, "how": "pull_request", "url": url, "files": paths})
                return {"suggested": True, "how": "pull_request", "original": original, "url": url, "files": paths}
            title = f"Review a suggestion from {names['copy']} for {names['original']}"
            task = H.task_create(c, who.actor, title, text + "\n\nTo take it, apply these changes to " + names["original_slug"]
                                 + "'s instructions (BotOps can do it).", "human:" + owner if owner else "bot:" + original,
                                 allow_planned=True, lint=False)
            H.event(c, who.actor, "bot.suggestion_made", bot, {"original": original, "how": "task", "task": task["id"], "files": paths})
            return {"suggested": True, "how": "task", "original": original, "task_id": task["id"], "files": paths,
                    "detail": ("You may not write to " + names["original"] if not writes else
                               "GitHub is not connected for that repository" if not repo else why or "GitHub would not take it")
                    + ", so its owner has a task with the diff."}

    @app.post("/api/v2/bots/{bot}/skills/copy")
    def copy_skill(request: Request, bot: str, body: M.SkillCopy):
        """Copy `skills/<skill>/` from this bot into others. Needs read on this one and the right to change each target: a skill
        is part of a bot's instructions, so only someone who manages the target may add one. The computer that holds the
        repositories commits it."""
        who = person(request)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)

        def work(c):
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            targets, ids = [], []
            for slug in dict.fromkeys(body.to):
                slug = (H.resolve_bot(c, slug) or {}).get("id", slug)
                row = H.bot(c, slug)
                if slug == bot:
                    raise Problem("same_bot", "Name a different bot to copy the skill to", 422)
                if not row or row["state"] == "archived":
                    raise Problem("not_found", f"No bot {slug} to copy to", 404)
                auth.require_write(c, who, slug)
                settings_admin._manager(c, who, slug)
                # The answer names each target by slug and by the folder its checkout has (a rename keeps the folder):
                # the client finds its files there. The audit names ids.
                targets.append({"bot": row["slug"], "folder": folder(c, slug),
                                "repo": settings_admin._config(c, slug)["repo"] or "emp-" + row["slug"]})
                ids.append(slug)
            H.event(c, who.actor, "bot.skill_copy_requested", bot, {"skill": body.skill, "to": ids})
            return {"skill": body.skill, "from": bot, "from_folder": folder(c, bot), "to": targets}
        return mutate(request, body, work)
