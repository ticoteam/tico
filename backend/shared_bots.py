"""Branches share their original's definition and repository, with personal work and computers."""

import json
import re
from types import SimpleNamespace

from .store import H, Problem, encode, repo_url
from . import profiles

# What a branch takes from its original at every claim. Everything else (its name, its person,
# its computer) is the branch's own.
FOLLOWED = ("model", "runtime", "harness", "reasoning_effort", "compact_at",
            "session", "subtask_session", "task_goal", "review_model", "fallback",
            "task_model",
            "max_run_minutes", "bot_contact", "private_tasks_default", "stateless")


def _as_followed(original):
    """The original's config as a branch takes it. FOLLOWED copies only what the original states, so an original that is
    stateless by its template (hubdb.stateless) is marked stateless here, and its branches stay stateless too."""
    return {**original, "stateless": True} if H.stateless(original) else original


def _json(value):
    try:
        return json.loads(value) if isinstance(value, str) else (value or {})
    except (TypeError, ValueError):
        return {}


def declared(c, bot):
    if not H._has_table(c, "bot_config"):
        return {}
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    return _json(row[0]) if row else {}


def source_of(config):
    """The bot with branches this one is a branch of, or ""."""
    return str((config or {}).get("shared_from") or "")


def follow(c, bot, config, rows=None):
    """A branch's config with the original's behaviour in it; any other bot's config as it is.
    `rows(slug)` answers from bot_config rows already read (backend/bot_rows.py). A stateless bot's config carries
    `stateless` whether or not its template is in the row (H.with_stateless)."""
    config = H.with_stateless(config)
    source = source_of(config)
    if not source:
        return config
    if rows is not None:
        found = rows(source)
        original = _json(found["config"]) if found else {}
    else:
        original = declared(c, source)
    if not original:
        return config
    original = _as_followed(original)
    merged = dict(config)
    for key in FOLLOWED:
        if key in original:
            merged[key] = original[key]
        else:
            merged.pop(key, None)
    row = rows(source) if rows is not None else c.execute(
        "SELECT repo FROM bot_config WHERE bot=?", (source,)).fetchone()
    if row:
        merged["repo"] = row["repo"] or "emp-" + (H.bot(c, source) or {}).get("slug", source)
    return merged


def copies(c, source):
    """Ids of every copy of `source`, archived ones included."""
    if not H._has_table(c, "bot_config"):
        return []
    return [row["bot"] for row in c.execute("SELECT bot,config FROM bot_config ORDER BY bot")
            if source_of(_json(row["config"])) == source
            # A retired temporary assignment (#420) also names its role in shared_from; it is no branch.
            and not _json(row["config"]).get("assignment_branch")]


def refuse_copy(c, bot):
    """A branch's behaviour is its original's; changing it means changing the original."""
    source = source_of(declared(c, bot))
    if source:
        slug, original = (H.bot(c, bot) or {}).get("slug", bot), (H.bot(c, source) or {}).get("slug", source)
        raise Problem("shared_copy", f"{slug} is a branch of {original} and follows it; "
                      f"change {original} instead", 409)


def model_bot(c, bot):
    """The bot whose model decides who may choose a model its tasks run on: a branch's is its original's, since it
    follows the original's model (FOLLOWED). Its tasks' own choices are still the task's, so only the rights come from
    the original."""
    return source_of(declared(c, bot)) or bot


def copy_slug(source, person):
    slug = re.sub(r"[^a-z0-9]+", "-", f"{source}-{person}".lower()).strip("-")
    if len(slug) > 80:
        raise Problem("slug", "That bot and person make a name longer than 80 characters", 422)
    return slug


def add_copy(c, who, source, runner_id, settings_admin, execution, github_owner):
    """The caller's own copy of `source`, created if they have none, on `runner_id` if given."""
    if who.role not in ("human", "owner"):
        raise Problem("forbidden", "Only a person may make a branch", 403)
    person = H.actor_id(who.actor)
    if not H.human(c, person):
        raise Problem("not_found", "You are not on the roster", 404)
    original_row = c.execute("SELECT * FROM bot_config WHERE bot=?", (source,)).fetchone()
    original_bot = H.bot(c, source)
    if not original_row or not original_bot or original_bot["state"] == "archived":
        raise Problem("not_found", "Bot not found", 404)
    original = _json(original_row["config"])
    if source_of(original):
        parent = H.bot(c, source_of(original)) or {}
        raise Problem("shared_copy", f"{original_bot['slug']} is itself a branch; add {parent.get('slug', source_of(original))}", 422)
    if not original.get("shared"):
        raise Problem("not_shared", f"{original_bot['slug']} does not allow branches", 409)
    if original_row["operator"] == person:
        raise Problem("own_bot", f"{original_bot['slug']} is already yours", 409)
    settings_admin.auth.require_read(c, who, source)
    runner = None
    if runner_id:
        runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()
        if not runner:
            raise Problem("not_found", "Computer is not registered", 404)
        if runner["operator"] != person:
            raise Problem("operator", "That computer belongs to someone else", 403)

    slug = copy_slug(original_bot["slug"], person)
    existing = c.execute("SELECT bc.* FROM bot_config bc JOIN bots b ON b.id=bc.bot WHERE b.slug=?", (slug,)).fetchone()
    bot_id = existing["bot"] if existing else H.new_bot_id(slug)
    if existing and (existing["operator"] != person or source_of(_json(existing["config"])) != source):
        raise Problem("duplicate", f"The name {slug} is taken by another bot", 409)
    created = not existing
    if existing and H.bot(c, bot_id)["state"] == "archived":
        raise Problem("archived", "Restore your branch before using it", 409)
    if runner:
        check_runner(c, bot_id, runner["id"], source)
    if created:
        repo = original_row["repo"] or ("emp-" + original_bot["slug"])
        status = "active" if runner else "planned"
        config = {"name": slug, "display_name": original_bot["display_name"],
                  "description": original_row["description"] or "", "reports_to": "human:" + person,
                  "status": status, "repo": repo, "repo_url": repo_url(repo, github_owner),
                  "host": "keeper", "tasks": "hub", "thread_mode": "personal",
                  "shared_from": source, "model_managed_by": "cloud"}
        profiles.apply(config, profiles.DOER, {})
        config.update({key: value for key, value in _as_followed(original).items() if key in FOLLOWED})
        team = settings_admin._team(c, bot_id, config)
        now = H.now()
        c.execute("INSERT INTO bots(id,slug,display_name,effort,state,created_at) VALUES(?,?,?,?,?,?)",
                  (bot_id, slug, original_bot["display_name"], original_bot["effort"], status, now))
        c.execute(
            "INSERT INTO bot_config(bot,config,team,operator,owner_ids,description,reports_to,"
            "repo,thread_mode,definition_updated_at,definition_updated_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (bot_id, encode(config), team, person, encode([person]), config["description"],
             config["reports_to"], repo, "personal", now, who.actor))
        from .report_order import arrive
        arrive(c, "bot:" + bot_id, config["reports_to"])     # a new bot goes last among its manager's reports
        c.execute("UPDATE bot_config SET bot_owners=? WHERE bot=?", (encode([person]), bot_id))
        H.event(c, who.actor, "bot.shared_copy_added", bot_id, {"source": source, "runner": runner_id})
    assignment = c.execute("SELECT * FROM assignments WHERE bot=?", (bot_id,)).fetchone()
    if runner and (not assignment or assignment["runner_id"] != runner["id"]):
        execution.assign(c, who, bot_id, SimpleNamespace(
            runner_id=runner["id"], expected_generation=assignment["generation"] if assignment else 0))
        if H.bot(c, bot_id)["state"] == "planned":
            c.execute("UPDATE bots SET state='active' WHERE id=?", (bot_id,))
            c.execute("UPDATE bot_config SET config=json_set(config,'$.status','active') WHERE bot=?", (bot_id,))
    return {**settings_admin.definition(c, bot_id), "created": created,
            "assignment": dict(c.execute("SELECT * FROM assignments WHERE bot=?", (bot_id,)).fetchone() or {})
            or None}


def check_runner(c, bot, runner_id, source=None):
    """One checkout has one bot on each computer, including future transfers."""
    config = declared(c, bot)
    original = source_of(config)
    source = source or original or bot
    for row in c.execute("SELECT a.bot,bc.config FROM assignments a JOIN bot_config bc ON bc.bot=a.bot "
                         "WHERE a.runner_id=? AND a.bot<>?", (runner_id, bot)):
        other_config = _json(row["config"])
        if row["bot"] == source or source_of(other_config) == source:
            raise Problem("shared_runner", "That computer already runs the original or a branch of it. Choose another computer.", 409)

    if original:
        runner = c.execute("SELECT operator FROM runners WHERE id=?", (runner_id,)).fetchone()
        person = H.actor_id(config.get("reports_to") or "")
        if runner and runner["operator"] != person:
            raise Problem("operator", "A branch runs only on its person's computers", 403)


def place_pending(c, operator, runner_id, settings_admin, execution, github_owner):
    """A planned branch starts on the next computer its person enrolls, with the usual checks."""
    from .auth import Identity
    who = (settings_admin.auth.owner_identity(c) if operator == settings_admin.auth.owner_id(c)
           else Identity("human:" + operator, "human"))
    placed = []
    for row in c.execute("SELECT bc.bot,bc.config FROM bot_config bc JOIN bots b ON b.id=bc.bot "
                         "LEFT JOIN assignments a ON a.bot=bc.bot "
                         "WHERE bc.operator=? AND b.state='planned' AND a.bot IS NULL ORDER BY bc.bot",
                         (operator,)).fetchall():
        source = source_of(_json(row["config"]))
        if not source:
            continue
        try:
            add_copy(c, who, source, runner_id, settings_admin, execution, github_owner)
        except Problem:
            continue                      # enrollment still works if access or branching has since changed
        placed.append(row["bot"])
    return placed


def route(c, actor, target):
    """Route a person's new work to their active branch while branches are allowed."""
    resolved = H.resolve_actor(c, target)
    if not str(actor).startswith("human:") or not str(resolved).startswith("bot:"):
        return resolved or target
    source = H.actor_id(resolved)
    if not declared(c, source).get("shared"):
        return resolved
    for slug in copies(c, source):
        row = c.execute("SELECT operator FROM bot_config WHERE bot=?", (slug,)).fetchone()
        if row["operator"] == H.actor_id(actor) and H.bot(c, slug)["state"] == "active":
            return "bot:" + slug
    return resolved


def cascade_archive(c, actor, source):
    """Keep branches' assignments and work intact so restoring the original resumes them."""
    for slug in copies(c, source):
        state = H.bot(c, slug)["state"]
        if state == "archived":
            continue
        config = declared(c, slug)
        config["branch_previous_status"] = state
        config["status"] = "archived"
        c.execute("UPDATE bots SET state='archived' WHERE id=?", (slug,))
        c.execute("UPDATE bot_config SET config=?,revision=revision+1 WHERE bot=?", (encode(config), slug))
        H.event(c, actor, "bot.branch_archived", slug, {"source": source, "previous": state})
        H.setup_finished(c, slug, archived=True)


def cascade_restore(c, actor, source):
    for slug in copies(c, source):
        config = declared(c, slug)
        previous = config.pop("branch_previous_status", None)
        if not previous:
            continue
        config["status"] = previous
        c.execute("UPDATE bots SET state=? WHERE id=?", (previous, slug))
        c.execute("UPDATE bot_config SET config=?,revision=revision+1 WHERE bot=?", (encode(config), slug))
        H.event(c, actor, "bot.branch_restored", slug, {"source": source, "status": previous})


def install(app, store, auth, mutate, settings_admin, execution):
    from fastapi import Request
    from .models import BotBranch

    @app.post("/api/v2/bots/{bot}/branches")
    @app.post("/api/v2/bots/{bot}/copies")
    def branch(request: Request, bot: str, body: BotBranch):
        who = request.state.identity
        auth.domain(who)
        return mutate(request, body, lambda c: add_copy(c, who, (H.resolve_bot(c, bot) or {}).get("id", bot),
                                                       body.runner_id, settings_admin, execution,
                                                       store.settings.github_owner))

    @app.get("/api/v2/bots/{bot}/branches")
    def branches(request: Request, bot: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            auth.require_read(c, who, bot)
            source = source_of(declared(c, bot)) or bot
            auth.require_read(c, who, source)
            return {"original": source, "shared": bool(declared(c, source).get("shared")),
                    "branches": [settings_admin.definition(c, slug) for slug in copies(c, source)
                                 if auth.bot_access(c, who, slug)["read"]]}


def retire_assignments(c):
    """Temporary task assignments are retired (#420); task sessions replaced them. Their actors are
    archived and taken off their computers, queued turns cancelled, and open tasks go back to the role
    they came from. The rows and the assignment_branch* tables stay as history. Idempotent."""
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    for row in c.execute("SELECT bc.bot,bc.config,b.state FROM bot_config bc JOIN bots b ON b.id=bc.bot "
                         "WHERE bc.config LIKE '%assignment_branch%'").fetchall():
        config = _json(row["config"])
        if not config.get("assignment_branch"):
            continue
        bot = row["bot"]
        # A turn still leased or running could not hand in its result once the actor is archived and off
        # its computer; leave this one as it is and retire it at a later startup, once the turn settles.
        if c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running') LIMIT 1",
                     (bot,)).fetchone():
            continue
        if row["state"] != "archived" or config.get("status") != "archived":
            config["status"] = "archived"
            c.execute("UPDATE bots SET state='archived' WHERE id=?", (bot,))
            c.execute("UPDATE bot_config SET config=?,revision=revision+1 WHERE bot=?", (encode(config), bot))
        H.setup_finished(c, bot, archived=True)
        c.execute("DELETE FROM assignments WHERE bot=?", (bot,))
        c.execute("UPDATE jobs SET state='canceled' WHERE bot=? AND state='queued'", (bot,))
        source = H.bot(c, str(config.get("shared_from") or ""))
        if not source or source["state"] == "archived":
            continue
        owner = "bot:" + source["id"]
        for task_id, title in c.execute(f"SELECT id,title FROM tasks WHERE owner=? AND status IN ({marks})",
                                        ("bot:" + bot, *H.ACTIVE_STATUSES)).fetchall():
            # The normal handoff queues the role's turn and notice, so a `doing` task does not sit idle.
            # Only an active role can be handed a task; otherwise (or if refused) the owner is set
            # directly and the role told the same way, to pick up when it runs again.
            if source["state"] == "active":
                try:
                    H.task_update(c, H.KEEPER, task_id, owner=owner, note="Back from a retired temporary assignment.")
                    continue
                except H.Refused:
                    pass
            c.execute("UPDATE tasks SET owner=?,updated_at=? WHERE id=?", (owner, H.now(), task_id))
            from . import task_run_models
            task_run_models.moved(c, H.KEEPER, task_id)
            H._wake(c, H.task(c, task_id), owner, f"Open: {title}", {"news": "assignment"})
