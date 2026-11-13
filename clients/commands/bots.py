"""Bots, agents, skills and templates.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from clients.tico import APIError

from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.commands._shared import BUDGET_VALUE, _model_id, _teammate_access
from clients.commands._requester import ON_BEHALF_OF, _as_person, _for_person


@tool(
    "tico_bot_archive",
    "Archive a bot you manage. Routines and placement are removed; its External agent Credential "
    "is revoked by default. Restore does not recover those. Built-in bots cannot be archived.",
    {"bot": _s("Bot slug"), "successor": _s("Bot taking over its open work")},
    required=("bot",),
    writes=True,
)
def bot_archive(api, args):
    person = _as_person(api)
    definition = person.get(f"bots/{args['bot']}/access")
    return person.post(
        f"bots/{args['bot']}/archive",
        {"successor": args.get("successor") or None, "expected_revision": definition["revision"]},
        key=_key(args),
    )


@tool(
    "tico_bot_update",
    "Change a bot's settings (reports to, name, description, status) with your own rights. "
    "BotOps acts as the person who asked, citing their message. "
    "A manager bot changes only its direct reports: their model, task model, name and description. Creating, archiving "
    "or restoring a report goes through BotOps the same way, within the weekly limit.",
    {
        "slug": _s("The bot to change (its slug or id)"),
        "on_behalf_of": _s(
            "Id of the person's message to BotOps asking for it (default: the message that started this turn)"
        ),
        "new_slug": _s("Rename its slug (a label: its id, history and folder stay); not a built-in bot's"),
        "reports_to": _s("A bot slug, or human:<id>"),
        "display_name": _s("New display name"),
        "description": _s("New description"),
        "template": _s("Correct the template metadata; use tico_template_list"),
        "repo": _s("Its GitHub repository: <org>/bot-<slug>"),
        "shared": {"type": "boolean", "description": "Allow branches"},
        "session": {"type": "string", "enum": ["bot", "task"]},
        "subtask_session": {
            "type": "string",
            "enum": ["parent", "own"],
            "description": "parent (default): a subtask continues its parent task's session; own: its own session",
        },
        "task_goal": {
            "type": "string",
            "enum": ["on", "off"],
            "description": "on: work each task it owns as a goal; off (default): not",
        },
        "review_model": {
            "type": "string",
            "enum": ["check", "own"],
            "description": "check: a subtask it is handed under another's task runs on the company's "
            "check model; own (default): its own model",
        },
        "capture_stream": {
            "type": "string",
            "enum": ["on", "off"],
            "description": "on (owner only): keep each run's raw provider stream a week; off (default): not",
        },
        "update_subscriptions": {
            "type": "array",
            "items": {"type": "string", "enum": ["templates", "tico"]},
            "description": "Subscribe to updates, as deferred messages for its next run: templates "
            "(its template's changes), tico (Tico releases); [] for none",
        },
        "status": {"type": "string", "enum": ["active", "paused", "planned"]},
        "app_delivery": _s(
            "A Grok Bot only: the id of your own computer whose Grok app delivers its messages, "
            "or `off` for the daily sync alone"
        ),
    },
    required=("slug",),
    writes=True,
)
def bot_set(api, args):
    delegated = _for_person(api)
    on_behalf = args.get("on_behalf_of") or "turn" if delegated else None
    # The bot's own revision, read as the person: BotOps may not see a bot they own but it does not.
    row = next((b for b in api.get("bots") if args["slug"] in (b.get("id"), b.get("slug") or b.get("name"))), None)
    if not row or row.get("revision") is None:
        row = api.get(f"bots/{args['slug']}", **({"on_behalf_of": on_behalf} if delegated else {}))
        if not row:
            raise ValueError("No bot " + args["slug"])
    change = {
        k: args[k]
        for k in (
            "reports_to",
            "display_name",
            "description",
            "template",
            "repo",
            "status",
            "shared",
            "session",
            "subtask_session",
            "task_goal",
            "review_model",
            "capture_stream",
            "update_subscriptions",
        )
        if args.get(k) is not None
    }
    if args.get("new_slug"):
        change["slug"] = args["new_slug"]
    if args.get("app_delivery"):
        change["app_delivery"] = (
            None if args["app_delivery"] == "off" else {"computer": args["app_delivery"], "app": "grok"}
        )
    return api.post(
        f"bots/{args['slug']}/definition",
        {**change, "expected_revision": row["revision"], **({"on_behalf_of": on_behalf} if delegated else {})},
        key=_key(args),
    )


@tool(
    "tico_bot_create",
    "Register a new bot with the server, planned, as the person who asked you (BotOps): the "
    "record its repository is then built for. They become its owner. Needs their create_bots (on by default) and "
    "stays within their limit of active bots. Safe to repeat for a bot they already own. `tico bot create --template T` "
    "also builds its repository on this computer. Starters build on their assigned Computer; custom templates get "
    "a BotOps setup_task_id to watch with tico_task_show. Set build false for only the record; BotOps defaults to only the record.",
    {
        "slug": _s("Leave out: Tico picks it from the name. Only to keep a slug already chosen"),
        "name": _s("What people call it; the template's name by default"),
        "description": _s("What it does"),
        "reports_to": _s("A bot slug, or human:<id>; the requester by default"),
        "template": _s("A template from tico_template_list, if it is built from one"),
        "build": {"type": "boolean", "description": "Build from the template (human default: true; BotOps: false)"},
        "instructions": _s(
            "Reviewed Instructions overriding the template; requested description and limits still apply"
        ),
        "title_prefix": _s("Prefix for a generated setup task's title"),
        "model": _s(
            "`hermes` for a bot run by a Hermes profile (it gets a credential, not a computer); leave out for the Team default"
        ),
    },
    writes=True,
)
def bot_register(api, args):
    body = {
        **({"slug": args["slug"]} if args.get("slug") else {}),
        "display_name": args.get("name") or "",
        "description": args.get("description") or "",
        "reports_to": args.get("reports_to") or None,
        "template": args.get("template") or "",
        **{k: args[k] for k in ("instructions", "title_prefix") if k in args},
        **({"model": args["model"]} if args.get("model") else {}),
        **_for_person(api),
    }
    body["build"] = args.get("build", bool(body["template"]) and "on_behalf_of" not in body)
    return api.post("bots/register", body, key=_key(args))


@tool(
    "tico_bot_access",
    "Show, or set, who may see, read and write to a bot, as the person who asked you (they must "
    "own the bot). Each of see, read and write is `everyone`, or a comma list of person ids, `group:<id>` and "
    "`bot:<slug>`. Anyone who may read or write can also see it. Levels you leave out stay as they are.",
    {
        "slug": _s("The bot"),
        "see": _s("everyone, or ben,group:legal,bot:analyst"),
        "read": _s("Who may read its work: tasks, updates, files, status, automations"),
        "write": _s("Who may send it messages and tasks"),
    },
    required=("slug",),
    writes=True,
)
def bot_access(api, args):
    return _teammate_access(api, f"bots/{args['slug']}", args)


@tool(
    "tico_bot_mail",
    "Show or set whether a bot sends mail on its own or asks first. Only a person who manages the "
    "bot sets it; bots are refused. Full policy: tico_email_policy.",
    {"slug": _s("The bot"), "outbound_send": {"type": "boolean", "description": "On their own: on or off"}},
    required=("slug",),
    writes=True,
)
def bot_mail(api, args):
    body = {k: args[k] for k in ("outbound_send",) if args.get(k) is not None}
    if not body:
        return api.get(f"bots/{args['slug']}/mail-settings")
    return api.post(f"bots/{args['slug']}/mail-settings", body, key=_key(args))


@tool(
    "tico_bot_owners",
    "Add or remove people who own a bot, as the person who asked you (any owner may). The creator "
    "is the first; whoever it reports up to and the admins are owners without being listed.",
    {
        "slug": _s("The bot"),
        "add": {"type": "array", "items": {"type": "string"}, "description": "Person ids to add"},
        "remove": {"type": "array", "items": {"type": "string"}, "description": "Person ids to remove"},
    },
    required=("slug",),
    writes=True,
)
def bot_owners(api, args):
    body = {"add": list(args.get("add") or []), "remove": list(args.get("remove") or []), **_for_person(api)}
    return api.post(f"bots/{args['slug']}/co-owners", body, key=_key(args))


@tool(
    "tico_bot_setup_done",
    "Mark a bot's onboarding complete, as a person above it on the org chart (its manager, their "
    "managers or the owner): it becomes Active, its automations run and it takes tasks. Nobody else may. A bot calling it "
    "on itself says it is ready instead, like tico_bot_ready, and stays Onboarding, unless its template goes live after "
    "setup (the growth channel packs): then its own call makes it Active. Repeating it changes nothing. A "
    "member's bot counts toward their limit of active bots from here on, so this can answer `bot_limit`: tell the "
    "person to archive a bot or ask an admin.",
    {"slug": _s("The bot; you, when you leave it out")},
    writes=True,
)
def bot_onboarded(api, args):
    slug = args.get("slug") or str(api.get("me").get("actor", "")).removeprefix("bot:")
    if not slug:
        raise ValueError("Say which bot: tico bot setup-done <slug>")
    return api.post(f"bots/{slug}/onboarded", {}, key=_key(args))


@tool(
    "tico_bot_ready",
    "Say you are ready to start work, while you are onboarding, with a short summary of what you "
    "learned. It is shown to the people above you on the org chart; one of them marks your onboarding complete. You "
    "stay Onboarding until then.",
    {"summary": _s("A few lines on what you learned and what you will do first")},
    required=("summary",),
    writes=True,
)
def bot_ready(api, args):
    slug = str(api.get("me").get("actor", "")).removeprefix("bot:")
    return api.post(f"bots/{slug}/onboarded", {"summary": args.get("summary") or ""}, key=_key(args))


@tool(
    "tico_bot_place",
    "Put a bot on a computer, as the person who asked you: the one named (label or id), or the best one that "
    "takes it (the only computer, else the least busy that accepts it). Safe to repeat. A computer that does not take "
    "members' bots is a Confirm card for an admin.",
    {"bot": _s("The bot's slug"), "computer": _s("A computer's label or id; leave out to pick one")},
    required=("bot",),
    writes=True,
)
def bot_place(api, args):
    return _as_person(api).post(f"bots/{args['bot']}/place", {"computer": args.get("computer") or ""}, key=_key(args))


@tool(
    "tico_bot_go_live",
    "Take a built bot to working, as the person who asked you: place it if it has no computer, turn it on and "
    "start its setup with the person. Then send it one small task to test it and report what happened. When its computer "
    "does not have its repository yet the answer is state waiting_for_repository: the placement is kept and Tico "
    "finishes going live by itself when the computer reports the repository, so do not call it again or wait.",
    {
        "bot": _s("The bot's slug"),
        "computer": _s("A computer's label or id; leave out to pick one"),
        "setup": {"type": "boolean", "default": True, "description": "Start its setup chat when it is a starter bot"},
    },
    required=("bot",),
    writes=True,
)
def bot_go_live(api, args):
    return _as_person(api).post(
        f"bots/{args['bot']}/go-live",
        {"computer": args.get("computer") or "", "setup": args.get("setup", True)},
        key=_key(args),
    )


@tool(
    "tico_bot_restore",
    "Bring an archived bot back, as the person who asked you (its owner or an admin): to the status it had "
    "when it was archived, else planned. Its routines are not restored. For a Hermes bot the answer says whether its agent "
    "credential still works.",
    {"bot": _s("The archived bot's slug")},
    required=("bot",),
    writes=True,
)
def bot_restore(api, args):
    return _as_person(api).post(f"bots/{args['bot']}/restore", {}, key=_key(args))


# ----------------------------------------------------------------------------- branches
@tool(
    "tico_bot_branch",
    "Make your branch of a bot that allows branches, on your own computer. "
    "It follows the original's instructions and model with personal tasks and chats. Asking again returns your branch. "
    "BotOps acts as the person who asked.",
    {"bot": _s("The original bot"), "runner_id": _s("Your computer's id; omit to create a planned branch")},
    required=("bot",),
    writes=True,
)
def bot_branch(api, args):
    body = {"runner_id": args["runner_id"]} if args.get("runner_id") else {}
    return _as_person(api).post(f"bots/{args['bot']}/copies", body, key=_key(args))


# Copying a bot or a skill moves files in the workspace on this computer, so these run here, never on the server
# (docs/creating-bots.md, "Copy a bot"); the server checks the requester's rights on each bot they name.
@tool(
    "tico_bot_copy",
    "Copy a bot into a new one, as the person who asked you (they need read on the original and room in their limit of bots): an "
    "ordinary, independent bot they own, from the original's repository at its current commit (instructions, skills, playbooks, the `tools:` "
    "list; one fresh commit, not its history). Its notes and memory are left out unless `with_memory`. No credential, .env or secret is "
    "ever copied: the credentials the original's tools need are granted to the copy when the person is a credential administrator, and "
    "the rest come back as `needs credential X` for you to ask for. Nothing stays linked. Runs on this computer, in its workspace.",
    {
        "bot": _s("The bot to copy"),
        "slug": _s("The copy's slug; the original's with the next free number (builder-2) by default"),
        "name": _s("What people call the copy"),
        "with_memory": {
            "type": "boolean",
            "default": False,
            "description": "Also copy the original's memory, notes and state",
        },
        "computer": _s("A computer's label or id to put the copy on; leave out to place it later"),
    },
    required=("bot",),
    writes=True,
    local=True,
)
def bot_copy(api, args):
    from clients import botcopy

    return botcopy.run_copy(api, _as_person(api), args)


@tool(
    "tico_bot_update_from_original",
    "Bring a copy's instructions (AGENTS.md, skills, playbooks) up to date with the bot it was copied from, as the "
    "person who asked you (they manage the copy): a file-level three-way merge, in one commit on the copy's repository. Answers the "
    "original's diff since the copy, and either `updated`, `current`, or `conflicts` (nothing changed; settle each with the person, "
    "edit the copy, commit, then call again with `resolved`). Runs on this computer.",
    {
        "bot": _s("The copy"),
        "resolved": {
            "type": "boolean",
            "default": False,
            "description": "You settled the conflicts by hand and committed: record that the copy is up to date",
        },
    },
    required=("bot",),
    writes=True,
    local=True,
)
def bot_update_from_original(api, args):
    from clients import botcopy

    return botcopy.run_update(_as_person(api), args)


@tool(
    "tico_bot_suggest_to_original",
    "Suggest a copy's changes to its instructions to the bot it was copied from, as the person who asked you: a "
    "pull request on the original's repository when they may write to it and GitHub is connected, else a task for the original's owner "
    "with the diff. A file the original changed too is held back. Runs on this computer.",
    {
        "bot": _s("The copy"),
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only these files or folders (AGENTS.md, skills/x)",
        },
        "title": _s("The pull request's or task's title"),
    },
    required=("bot",),
    writes=True,
    local=True,
)
def bot_suggest_to_original(api, args):
    from clients import botcopy

    return botcopy.run_suggest(_as_person(api), args)


@tool(
    "tico_skill_copy",
    "Copy one skill (skills/<skill>/) from a bot's repository into other bots' repositories, a commit in each, as the person "
    "who asked you (read on the source; they manage each target). A target that already has a different skill of that name is left "
    "alone unless `replace`. Runs on this computer.",
    {
        "skill": _s("The skill's folder name under skills/"),
        "bot": _s("The bot that has the skill"),
        "to": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "The bots to copy it to"},
        "replace": {"type": "boolean", "default": False, "description": "Replace a different skill of the same name"},
    },
    required=("skill", "bot", "to"),
    writes=True,
    local=True,
)
def skill_copy(api, args):
    from clients import botcopy

    return botcopy.run_skill(_as_person(api), args)


@tool(
    "tico_skill_submit",
    "Propose a skill folder to the company's Skills directory, as the person who asked you (a bot or a person may propose; an "
    "admin approves it after screening). The folder is read on this computer: `skills/<skill>` or `.claude/skills/<skill>` in its repository, "
    "else `~/.claude/skills/<skill>`. A symlink, a binary file, or anything over the limits is refused here with the reason, never truncated; "
    "the whole folder is sent. An update of a skill the directory has is based on its latest version unless `base` names another.",
    {
        "skill": _s("The skill's folder name, lowercase letters, digits, dots and hyphens"),
        "path": _s("The folder, when it is not in one of the usual places"),
        "note": _s("One line for the admin, up to 200 characters"),
        "base": {
            "type": "integer",
            "minimum": 1,
            "description": "The version an update is based on (default: the latest)",
        },
    },
    required=("skill",),
    writes=True,
    local=True,
)
def skill_submit(api, args):
    from clients import skills

    return skills.run_submit(_as_person(api), args)


@tool(
    "tico_agent_pair_show",
    "Preview a pairing code: profile, host, harness and expiry, without changing a credential.",
    {"code": _s("The pairing code the profile printed")},
    required=("code",),
)
def agent_pair_show(api, args):
    return _as_person(api).get("agents/pairing-preview", code=args["code"])


@tool(
    "tico_agent_pair_approve",
    "Connect a Hermes profile to a bot, as the person who asked you (the bot's owner or an admin): "
    "the profile printed a code like K7QM-4F2P when it asked to pair. The bot must use the matching Hermes or OpenClaw harness (planned or active); pairing activates a planned bot. "
    "The bot's agent credential is made and goes to the profile itself; it is never shown here. Answers the profile name and "
    "host. Use tico_agent_pair_show first to check them against the request before replacing a credential.",
    {"code": _s("The pairing code the profile printed, like K7QM-4F2P"), "bot": _s("The bot's slug")},
    required=("code", "bot"),
    writes=True,
)
def agent_pair_approve(api, args):
    return _as_person(api).post("agents/pairings/approve", {"code": args["code"], "bot": args["bot"]}, key=_key(args))


@tool(
    "tico_agent_pair_decline",
    "Turn down a Hermes profile's pairing code, as the person who asked you. The profile is told.",
    {"code": _s("The pairing code the profile printed")},
    required=("code",),
    writes=True,
)
def agent_pair_decline(api, args):
    return _as_person(api).post("agents/pairings/decline", {"code": args["code"]}, key=_key(args))


@tool(
    "tico_bot_turns_at_once",
    "Read or set the most turns a bot you manage runs at once across all computers. "
    "Default no limit (null); N caps its turns on every computer together. Work over the limit waits and says so.",
    {
        "bot": _s("Bot slug"),
        "limit": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 256,
            "description": "Omit to read, null for no limit, N for a cap",
        },
    },
    ("bot",),
    writes=True,
)
def bot_turns_at_once(api, args):
    who = _as_person(api)
    bot = who.get(f"bots/{args['bot']}")
    if 'limit' not in args:
        return {"bot": args['bot'], "turns_at_once": bot.get('turns_at_once')}
    return who.post(
        f"bots/{args['bot']}/turns-at-once",
        {"turns_at_once": args['limit'], "expected_revision": bot['revision']},
        key=_key(args),
    )


@tool(
    "tico_bot_browser_exit",
    "Read or set the computer a bot you manage browses the web through: one whose owner "
    "shares its internet with that bot, or null for the bot's own. A computer that is offline or stops sharing "
    "makes the bot's browser fail with its name, never go direct.",
    {
        "bot": _s("Bot slug"),
        "computer": {
            "type": ["string", "null"],
            "description": "Omit to read, a computer id from `choices`, or null for the bot's own internet",
        },
    },
    ("bot",),
    writes=True,
)
def bot_browser_exit(api, args):
    who = _as_person(api)
    if "computer" in args:
        who.post(f"bots/{args['bot']}/browser-exit", {"runner_id": args["computer"]}, key=_key(args))
    return who.get(f"bots/{args['bot']}/browser-exit")


@tool(
    "tico_bot_app_view",
    "Read or set “Can view the app (read only)” for a bot: whether it may open the app signed in as itself, read only, "
    "to take screenshots for its own proof pages. Only an owner or an admin turns it on or off; every view is in the "
    "audit log.",
    {
        "bot": _s("Bot slug"),
        "on": {"type": "boolean", "description": "Omit to read; true to turn it on, false to turn it off"},
    },
    ("bot",),
    writes=True,
)
def bot_app_view(api, args):
    who = _as_person(api)
    if "on" in args:
        return who.post(f"bots/{args['bot']}/app-view", {"on": bool(args["on"])}, key=_key(args))
    return who.get(f"bots/{args['bot']}/app-view")


@tool(
    "tico_bot_app_shot",
    "A screenshot of the app as you see it, read only, signed in as yourself: for the screenshots on a spec or "
    "Checked in production page. Opens a read-only view (a person turned on “Can view the app” for you; nothing can "
    "be changed from it), opens `path` (an app address such as /#/tasks or /#/task/<id>) at `width` (390 for a "
    "phone, 1280 for a desktop) and writes a PNG to `out`. Runs Chrome on this computer.",
    {
        "path": _s("The app address to open, such as /#/tasks; / for home"),
        "out": _s("Where to write the PNG"),
        "width": {
            "type": "integer",
            "minimum": 320,
            "maximum": 2560,
            "description": "Window width in px: 390 phone, 1280 desktop",
        },
        "height": {"type": "integer", "minimum": 320, "maximum": 4000, "description": "Window height in px"},
        "wait_ms": {
            "type": "integer",
            "minimum": 0,
            "maximum": 30000,
            "description": "How long the page reads its data after it loads (default 2500)",
        },
        "full": {"type": "boolean", "description": "The whole page, not only the window"},
    },
    ("out",),
    writes=True,
    local=True,
)
def bot_app_shot(api, args):
    import json
    import os
    import subprocess
    from pathlib import Path

    path = str(args.get("path") or "/")
    if not path.startswith("/"):
        raise APIError("validation", "path is an app address that starts with /, such as /#/tasks")
    view = api.post("app-view", {}, key=_key(args))
    out = str(Path(args["out"]).expanduser().resolve())
    width = int(args.get("width") or 390)
    command = [
        "node",
        str(Path(__file__).resolve().parent.parent / "app_shot.mjs"),
        api.url + path,
        out,
        str(width),
        str(int(args.get("height") or 0) or ""),
        str(int(args.get("wait_ms") if args.get("wait_ms") is not None else 2500)),
        "full" if args.get("full") else "",
    ]
    # The token goes in the environment, never on the command line: it would show in the process list.
    done = subprocess.run(
        command,
        env={**os.environ, "TICO_VIEW_TOKEN": view["token"]},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise APIError("app_shot", (done.stderr or done.stdout or "the screenshot failed").strip()[-500:])
    shot = json.loads(done.stdout.strip().splitlines()[-1])
    return {**shot, "view": view["id"], "read_only": True}


@tool(
    "tico_bot_budget",
    "Read or set how many runs a bot may start per hour and per day, as the person who asked you. "
    "Tico holds a bot that goes over (a runaway loop, not normal work): its queued work is kept and a person's "
    "messages still run. With neither hour nor day it reads the budget, the team default, the runs so far and any "
    "hold. Raising a budget above the runs that held the bot releases it; release does that without a change.",
    {
        "bot": _s("Bot slug"),
        "hour": BUDGET_VALUE,
        "day": BUDGET_VALUE,
        "release": {
            "type": "boolean",
            "description": "Release the bot from a hold (failed runs or run budget) instead",
        },
    },
    ("bot",),
    writes=True,
)
def bot_budget(api, args):
    who = _as_person(api)
    path = f"bots/{args['bot']}/budget"
    if args.get("release") or args.get("action") == "release":
        return who.post(f"bots/{args['bot']}/release", {}, key=_key(args))
    body = {}
    for field, name in (("turns_per_hour", "hour"), ("turns_per_day", "day")):
        if name in args:
            body[field] = None if args[name] in (None, "default") else int(args[name])
    current = who.get(path)
    if not body:
        return current
    return who.post(path, {**body, "expected_revision": current["revision"]}, key=_key(args))


_PROFILES = ["doer", "reviewer", "lead", "manager", "responder", "monitor", "custom"]


@tool(
    "tico_bot_profile",
    "Read or set a bot's profile, as the person who asked you: what wakes the bot and which tasks it takes. "
    "doer works its tasks (news batched); reviewer takes review and QA requests only; lead hears the rest once a day; "
    "manager runs on its schedule over its exceptions list and hears ticket news only then; responder answers people in chat and takes no tasks; monitor takes its watcher's tasks; custom keeps its own wake "
    "rules on the Rules page. With no profile it reads the bot's and the choices. Leaving custom deletes the wake rules "
    "it copied into the rules table, so say confirm_drop_rules.",
    {
        "bot": _s("Bot slug"),
        "profile": {"type": "string", "enum": _PROFILES, "description": "The profile to set; leave out to read"},
        "digest_hour": {
            "type": "integer",
            "minimum": 0,
            "maximum": 23,
            "description": "Hour of a lead's daily digest, team time",
        },
        "pulls": {
            "type": "string",
            "enum": ["all", "review", "none"],
            "description": "Custom: which open tasks it starts by itself",
        },
        "sweeps": {
            "type": "boolean",
            "description": "Custom: whether the task roundup lists it its task sweep lines (late, backlog, assigned, restate, quiet tasks)",
        },
        "takes": {
            "type": "string",
            "enum": ["all", "review", "watcher", "none"],
            "description": "Custom: which tasks it may be given",
        },
        "confirm_drop_rules": {"type": "boolean", "description": "Leaving custom: delete the rules it copied"},
    },
    ("bot",),
    writes=True,
)
def bot_profile(api, args):
    who = _as_person(api)
    path = f"bots/{args['bot']}/profile"
    current = who.get(path)
    if not args.get("profile"):
        return {**current, "choices": who.get("profiles")["profiles"]}
    body = {
        k: args[k] for k in ("digest_hour", "pulls", "sweeps", "takes", "confirm_drop_rules") if args.get(k) is not None
    }
    return who.post(
        path, {**body, "profile": args["profile"], "expected_revision": current["revision"]}, key=_key(args)
    )


@tool(
    "tico_bot_release",
    "Release a bot Tico stopped, as the person who asked you: after five failed runs in a row, "
    "for starting too many runs, or for queued runs it could not start. Check its last runs first. Its queued work, "
    "kept while it was held, starts again.",
    {"bot": _s("Bot slug")},
    ("bot",),
    writes=True,
)
def bot_release(api, args):
    return _as_person(api).post(f"bots/{args['bot']}/release", {}, key=_key(args))


# Where a bot's conversation compacts, in tokens: the sizes the server takes (backend/providers.py COMPACT_SIZES).
COMPACT_SIZES = (
    100_000,
    150_000,
    200_000,
    250_000,
    300_000,
    400_000,
    500_000,
    600_000,
    700_000,
    800_000,
    900_000,
    1_000_000,
)


@tool(
    "tico_bot_model",
    "Show the models a bot may run on, or change its model or where its conversation compacts (`compact_at`), as "
    "the person who asked you. A model change waits for a run in progress to end. A manager bot may change its "
    "direct report's model.",
    {
        "bot": _s("The bot's slug"),
        "model": _s("A model id or name from the list; leave out to list them"),
        "effort": _s("Reasoning effort that model supports"),
        "harness": _s("A harness from that model's harnesses list"),
        "compact_at": {
            "type": "integer",
            "enum": list(COMPACT_SIZES),
            "description": "Where the bot's conversation compacts, in tokens; leave out to keep it",
        },
        "on_behalf_of": ON_BEHALF_OF,
    },
    required=("bot",),
    writes=True,
)
def bot_model(api, args):
    who = _as_person(api, args.get("on_behalf_of"))
    catalog = who.get("models")
    current = who.get(f"bots/{args['bot']}")
    if not any(args.get(k) for k in ("model", "harness", "effort", "compact_at")):
        enabled = set(catalog.get("enabled_providers") or [])
        return {
            "models": [
                {k: m.get(k) for k in ("id", "label", "provider", "efforts", "default_effort", "harnesses")}
                for m in catalog.get("models", [])
                if not m.get("deprecated") and (not enabled or m.get("provider") in enabled)
            ],
            **{k: v for k, v in current.items() if k in ("harness", "effort", "compact_at")},
            "current": current.get("model"),
            "compact_sizes": list(COMPACT_SIZES),
        }
    out = None
    if any(args.get(k) for k in ("model", "harness", "effort")):
        out = _set_bot_model(who, catalog, current, args)
    if args.get("compact_at"):
        revision = (out or {}).get("revision") or who.get(f"bots/{args['bot']}")["revision"]
        saved = who.post(
            f"bots/{args['bot']}/compact-at",
            {"compact_at": args["compact_at"], "expected_revision": revision},
            key=_key(args, ":compact"),
        )
        out = {**(out or {}), "compact_at": saved["compact_at"], "revision": saved["revision"]}
    return out


def _set_bot_model(who, catalog, current, args):
    model = _model_id(
        catalog, args.get("model") or current.get("model") or (catalog.get("default") or {}).get("model") or ""
    )
    revision = current["revision"]
    body = {"model": model, "expected_revision": revision, **({"effort": args["effort"]} if args.get("effort") else {})}
    if args.get("harness"):
        body["harness"] = args["harness"]
    return who.post(f"bots/{args['bot']}/model", body, key=_key(args))


@tool(
    "tico_bot_task_model",
    "Show, or set, the model a bot's tasks run on, as the person who asked you: a model id or "
    "name from the list, with its effort and harness. Leave out `model` to change only the effort or harness on the "
    "bot's own model; `clear` puts the bot's tasks back on its own model. A task's own model (tico_task_model) comes "
    "before this. Whoever may change the bot's model may change this, and so may a manager bot for its direct "
    "report.",
    {
        "bot": _s("The bot's slug"),
        "model": _s("A model id or name from tico_bot_model's list; leave out to keep the bot's own"),
        "effort": _s("Reasoning effort that model takes"),
        "harness": _s("A harness from that model's harnesses list"),
        "clear": {"type": "boolean", "default": False, "description": "Clear the bot's task model"},
        "on_behalf_of": ON_BEHALF_OF,
    },
    required=("bot",),
    writes=True,
)
def bot_task_model(api, args):
    who = _as_person(api, args.get("on_behalf_of"))
    if not any(args.get(k) for k in ("model", "harness", "effort", "clear")):
        return {"task_model": who.get(f"bots/{args['bot']}").get("task_model")}
    # The bot row, not its access card: a manager bot reads its report's row but not who may reach it.
    revision = who.get(f"bots/{args['bot']}")["revision"]
    if args.get("clear"):
        body = {"task_model": None, "expected_revision": revision}
    else:
        choice = {}
        if args.get("model"):
            choice["model"] = _model_id(who.get("models"), args["model"])
        for field in ("effort", "harness"):
            if args.get(field):
                choice[field] = args[field]
        body = {"task_model": choice, "expected_revision": revision}
    return who.post(f"bots/{args['bot']}/task-model", body, key=_key(args))


def _control(action):
    def run(api, args):
        who = _as_person(api)
        revision = who.get(f"bots/{args['bot']}/access")["revision"]
        return who.post(
            f"bots/{args['bot']}/control", {"action": action, "expected_revision": revision}, key=_key(args)
        )

    return run


tool(
    "tico_bot_pause",
    "Pause a bot, as the person who asked you: it takes no new work until resumed.",
    {"bot": _s("The bot's slug")},
    required=("bot",),
    writes=True,
)(_control("pause"))


tool(
    "tico_bot_resume",
    "Resume a paused bot, as the person who asked you; one with no computer is placed on one.",
    {"bot": _s("The bot's slug")},
    required=("bot",),
    writes=True,
)(_control("resume"))


@tool(
    "tico_bot_scope",
    "Limit a bot's claims to one task and its direct subtasks, as the person who asked you, or lift "
    "that limit with release: its other queued and new work stays queued, untouched, and nothing is paused, resumed "
    "or started.",
    {
        "bot": _s("The bot's slug"),
        "task": _s("The task it may still work on"),
        "release": {"type": "boolean", "description": "Lift the limit instead"},
    },
    required=("bot",),
    writes=True,
)
def _bot_scope(api, args):
    if bool(args.get("task")) == bool(args.get("release")):
        raise ValueError("Give a task to limit the bot to, or release to lift the limit")
    who = _as_person(api)
    revision = who.get(f"bots/{args['bot']}/access")["revision"]
    body = {"action": "unscope"} if args.get("release") else {"action": "scope", "task_id": args["task"]}
    return who.post(f"bots/{args['bot']}/control", {**body, "expected_revision": revision}, key=_key(args))


# ----------------------------------------------------------------------------- status, activity
@tool(
    "tico_bot_status_set",
    "One factual line about what you are doing now.",
    {
        "focus": _s("What you are working on"),
        "state": _s("idle, running, waiting_human, waiting_bot, blocked"),
        "task_id": _s("The task this is about"),
        "bot": _s("Another bot you operate; default is yourself"),
    },
    required=("focus",),
    writes=True,
)
def status_set(api, args):
    bot = args.get("bot") or api.get("me")["actor"].split(":", 1)[-1]
    return api.post(
        f"bots/{bot}/status",
        {"state": args.get("state"), "focus": args["focus"], "task_id": args.get("task_id")},
        key=_key(args),
    )


@tool(
    "tico_bot_recent",
    "The bots you (a person) have been working with lately, most recent first: each "
    "with its live status and what it is working on, the last thing you said and the last thing it "
    "said, the conversation id to read on with tico_conversation_show, your open tasks together, and what it "
    "needs from you. Start here to pick up where you left off.",
    {
        "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "How far back (default 7)"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "At most this many bots (default 10)"},
    },
)
def recent(api, args):
    return api.get("me/recent", days=args.get("days") or 7, limit=args.get("limit") or 10)


# ----------------------------------------------------------------------------- external agent sync
_SYNCED_MESSAGE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["role", "text"],
    "properties": {
        "role": _s("user (the person) or bot (the agent)", enum=["user", "bot"]),
        "text": _s("The message, verbatim"),
        "at": _s("When it was sent, ISO 8601"),
        "id": _s("The platform's id for the message, if you have one"),
        "images": {
            "type": "array",
            "maxItems": 10,
            "description": "Images shown in this message: each a public "
            "https `url` Tico can download, or for a file on your computer `content_base64` (under 5 MB) "
            "with a `name`. Tico stores them and shows them inline.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "url": _s("The image's https address"),
                    "name": _s("A file name, e.g. poster.png"),
                    "content_base64": _s("The image itself, base64"),
                },
            },
        },
    },
}


_SYNCED_BOT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {
        "id": _s(
            "The bot's id on its platform: for a Grok Bot the uuid in grok.com/bot/<id>. Dots and Muse may leave it out"
        ),
        "grok_id": _s("Older name for id, for a Grok Bot"),
        "name": _s("Its name there"),
        "description": _s("Its short description"),
        "instructions": _s("Its full instructions, verbatim"),
        "section": _s(
            "The section it sits in on its platform's own list (Grok: Pinned, or a section you made); "
            "empty when it is in none"
        ),
        "messages": {
            "type": "array",
            "maxItems": 500,
            "items": _SYNCED_MESSAGE,
            "description": "Transcript messages newer than the synced_through the last sync returned "
            "(all of them the first time), oldest first",
        },
    },
}


_SYNC_DOC = (
    "A bot Tico has not seen becomes a bot under you on the team chart; its name, description, section and "
    "full instructions are kept, and its transcript is copied into your chat with it. Idempotent: "
    "resending messages adds nothing. Each bot in the reply has `synced_through`, the newest message "
    "Tico has: next time send only newer ones."
)


_INBOX_DOC = (
    " Each bot in the reply also has `inbox`, what you wrote to it in Tico since the last sync, oldest "
    "first: give those to that bot. Tico counts them as delivered once they are in a reply."
)


def _sync_body(args, provider):
    return {
        k: v
        for k, v in (("provider", provider), ("bots", args["bots"]), ("source", args.get("source")))
        if v is not None
    }


def _sync_with_inbox(api, body, key):
    """Sync and take the inbox. A server from before `inbox` refuses the field; it hands the inbox to every sync."""
    try:
        return api.post("external/sync", {**body, "inbox": True}, key=key)
    except APIError as exc:
        if exc.status == 422 and exc.code == "validation" and "inbox" in str(exc.detail):
            return api.post("external/sync", body, key=key)
        raise


@tool(
    "tico_external_sync",
    "Sync your (a person's) bots on another platform into Tico: your Grok Bots "
    "(provider grokbot, every Bot on the account), your Dots (provider dots) or your Muse (provider muse), one bot each. "
    + _SYNC_DOC
    + _INBOX_DOC,
    {
        "provider": _s("grokbot, dots or muse", enum=["grokbot", "dots", "muse"]),
        "bots": {"type": "array", "minItems": 1, "maxItems": 50, "items": _SYNCED_BOT},
        "source": _s("Which of your bots ran this sync"),
    },
    required=("provider", "bots"),
    writes=True,
)
def external_sync(api, args):
    return _sync_with_inbox(api, _sync_body(args, args["provider"]), _key(args))


@tool(
    "tico_grokbot_sync",
    "Sync your Grok Bots into Tico: tico_external_sync with provider grokbot, for routines set "
    "up before the inbox, so it leaves what you wrote to them waiting. " + _SYNC_DOC,
    {
        "bots": {"type": "array", "minItems": 1, "maxItems": 50, "items": _SYNCED_BOT},
        "source": _s("Which of your Bots ran this sync"),
    },
    required=("bots",),
    writes=True,
)
def grokbot_sync(api, args):
    return api.post("external/sync", _sync_body(args, "grokbot"), key=_key(args))


@tool(
    "tico_bot_show",
    "One bot: its profile (name, role, who runs it, who it reports to, who it works for), and for "
    "one who may read it also its status, machine, queue, goals and which routines are on.",
    {"slug": _s("The bot's slug")},
    required=("slug",),
)
def bot_show(api, args):
    return _as_person(api).get(f"bots/{args['slug']}")


@tool("tico_bot_status_list", "Every bot you may see with its live status.", {"team": _s("Only this team")})
def status_list(api, args):
    return [b for b in api.get("bots") if not args.get("team") or b.get("team") == args["team"]]


@tool(
    "tico_bot_status_history",
    "A bot's status history.",
    {"bot": _s("Bot slug"), "since": _s("Window like `7d` or an ISO timestamp")},
    required=("bot",),
)
def status_history(api, args):
    return api.get(f"bots/{args['bot']}/history", since=args.get("since"))


@tool(
    "tico_bot_repos_get",
    "Read a bot’s own, all or chosen repository access.",
    {"bot": _s("Bot slug")},
    required=("bot",),
)
def bot_repos_get(api, args):
    return _as_person(api).get(f"bots/{args['bot']}/repositories")


@tool(
    "tico_bot_repos_set",
    "Set a bot’s repository access (owner or admin).",
    {
        "bot": _s("Bot slug"),
        "mode": _s("Access mode", enum=["own", "all", "chosen"]),
        "all_access": _s("Default access for all repos", enum=["read", "write"]),
        "chosen": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "full_name": _s("owner/repo"),
                    "access": _s("Access", enum=["read", "write"]),
                    "run_workflows": {
                        "type": "boolean",
                        "description": "Write grants only, Owner or admin: may start, rerun "
                        "and cancel workflow runs (GitHub Actions: write). Leave out to keep the current setting",
                    },
                    "edit_workflows": {
                        "type": "boolean",
                        "description": "Write grants only, Owner or admin: may create and "
                        "change workflow files under .github/workflows (GitHub Workflows: write). Leave out to "
                        "keep the current setting",
                    },
                },
                "required": ["full_name", "access"],
                "additionalProperties": False,
            },
        },
    },
    required=("bot", "mode"),
    writes=True,
)
def bot_repos_set(api, args):
    return _as_person(api).call(
        "PUT",
        f"bots/{args['bot']}/repositories",
        {k: args[k] for k in ("mode", "all_access", "chosen") if k in args},
        key=_key(args),
    )


@tool(
    "tico_bot_repo_create",
    "Owner or the BotOps bot: create the private repository bot-<slug> in the "
    "connected GitHub account or organization from a template (default ticoteam/botops), or empty when the bot's "
    "repository already exists on a computer. Answers with how to create it by hand when the team's "
    "GitHub App was not given permission to.",
    {
        "slug": _s("The bot's name, e.g. inbound-sales-owner for bot-inbound-sales-owner"),
        "template": _s("Template repository as owner/name; default ticoteam/botops"),
        "empty": {
            "type": "boolean",
            "description": "Create an empty private repository (no template) to push an existing history into",
        },
    },
    required=("slug",),
    writes=True,
)
def github_create_bot_repo(api, args):
    body = (
        {"slug": args["slug"], "empty": True}
        if args.get("empty")
        else {"slug": args["slug"], "template": args.get("template") or "ticoteam/botops"}
    )
    return api.post("github/repos", body, key=_key(args))


@tool(
    "tico_template_list",
    "The bot templates this Team can pick from, with Setup questions, the first Routine, an example result and Instructions.",
    {},
)
def catalog(api, args):
    return api.get("templates")["cards"]
