"""Tools (the outside services bots reach through Tico), credentials, repositories and Slack.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import hashlib
import json
import re
from urllib.parse import quote
from clients.tico import APIError

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.commands._shared import _CHANNEL
from clients.commands._requester import _as_person, _for_person, kind_of


# ----------------------------------------------------------------------------- tools
# What a bot uses, as its page shows it (docs/creating-bots.md, "What people see about a bot's tools"). Adding one
# never carries a credential: `env` is a variable's name, and its stored Credential is granted to the bot.
def _scope_of(value):
    """A scope as an object, from an object or from KEY=VALUE strings (a comma makes a list)."""
    if isinstance(value, dict):
        return value
    scope = {}
    for item in value or []:
        key, _, text = str(item).partition("=")
        scope[key.strip()] = [part.strip() for part in text.split(",")] if "," in text else text.strip()
    return scope


def _mcp_of(args):
    """The `mcp:` block the arguments name, or None: `mcp_url`, `transport` and `headers` (an object, or "Name: value"
    strings). A placeholder such as ${JIRA_API_TOKEN} is the variable's name; the server refuses a value."""
    url, transport, headers = args.get("mcp_url"), args.get("transport"), args.get("headers")
    if isinstance(headers, str):
        headers = [headers]
    if isinstance(headers, list):
        pairs = {}
        for item in headers:
            name, colon, value = str(item).partition(":")
            if not colon or not name.strip():
                raise ValueError(f"A header is written Name: value, not {item!r}")
            pairs[name.strip()] = value.strip()
        headers = pairs
    mcp = {k: v for k, v in (("url", url), ("transport", transport), ("headers", headers)) if v}
    return mcp or None


MCP_PROPERTIES = {
    "mcp_url": _s(
        "The address of a remote MCP server the bot uses (https; plain http only for localhost), such as https://mcp.linear.app/mcp"
    ),
    "transport": {
        "type": "string",
        "enum": ["http", "sse"],
        "description": "The MCP server's transport: http (streamable, the default) or sse",
    },
    "headers": {
        "type": ["object", "array", "string"],
        "items": {"type": "string"},
        "description": "Headers for the MCP server, e.g. {\"Authorization\": \"Bearer ${LINEAR_API_KEY}\"}. "
        "${VAR} may only name the entry's own env variable; never write the value (or a list of \"Name: value\")",
    },
}


@tool(
    "tico_tool_report",
    "Report the complete tool list configured in your external profile. Names and scopes only, never credential values.",
    {
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["service", "can"],
                "properties": {
                    "service": _s("Service name"),
                    "identity": _s("Account or project"),
                    "can": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "scope": {"type": "object"},
                    "env": _s("Credential variable name"),
                    "note": _s("Scope note"),
                    "mcp": {"type": "object"},
                },
                "additionalProperties": False,
            },
        }
    },
    required=("tools",),
    writes=True,
)
def tools_report(api, args):
    return api.post("agents/heartbeat", {"tools": args["tools"]}, key=_key(args))


@tool(
    "tico_tool_add",
    "Add a note on a tool for yourself, or (the Tools Owner, BotOps, a credential admin, a person who manages it) for any bot: a `tools:` entry for its bot.yaml. "
    "A tool granted in Tools already reaches the bot's runs and shows on its Tools tab with no entry: an entry that only names one is answered with the grant and "
    "opens no task. Otherwise the server checks it and opens a task with the exact YAML for BotOps, or for an External agent profile to configure itself; it stays "
    "pending until a tool report lists it. Names and verbs only: never a Credential value. To give the bot a vendor's remote MCP server Tools does not hold, add "
    "`mcp_url`, `transport` and `headers` (the credential is a ${VAR} placeholder for `env`).",
    {
        "bot": _s("The bot's slug"),
        "service": _s("A short name such as posthog or google-calendar"),
        **MCP_PROPERTIES,
        "identity": _s("Who it acts as, for a person to read: an account, a project, a role"),
        "can": {
            "type": ["array", "string"],
            "items": {"type": "string"},
            "minItems": 1,
            "description": "read, draft, post, act, use, send, write (or a comma list). Custom verbs describe intent; they add no enforcement",
        },
        "scope": {
            "type": ["object", "array"],
            "items": {"type": "string"},
            "description": "database, channels, project, mailbox, sites, repo and the like",
        },
        "env": _s("The environment variable's name, such as POSTHOG_KEY; never its value"),
        "note": _s("Who authorized it and what is excluded"),
        "title_prefix": _s("Keep this prefix on the generated BotOps task"),
        "dry_run": {"type": "boolean", "description": "Validate only; create no task"},
    },
    required=("bot", "service", "can"),
    writes=True,
)
def tools_add(api, args):
    can = args["can"]
    can = [part.strip() for part in can.split(",")] if isinstance(can, str) else can
    mcp = _mcp_of(args)
    if mcp and not mcp.get("url"):
        raise ValueError("An MCP server needs its address: mcp_url")
    body = {
        "service": args["service"],
        "can": can,
        "scope": _scope_of(args.get("scope")),
        **({"mcp": mcp} if mcp else {}),
        **{k: args[k] for k in ("identity", "env", "note", "title_prefix", "dry_run") if args.get(k)},
    }
    return _as_person(api).post(f"bots/{args['bot']}/tools", body, key=_key(args))


@tool(
    "tico_tool_update",
    "Change a tool a bot already declares, in place: what it `can` do, its `scope` or its `note` "
    "(BotOps: as the person who asked you). Only what you send changes; a `scope` key with an empty value comes off. "
    "A task for BotOps or the external profile carries the whole changed entry and the tool shows as pending its change. Use this, never "
    "remove and add again: that files a removal nobody wants. The identity and the env name do not change here. "
    "An MCP server's `mcp_url`, `transport` or `headers` change the same way: what you send replaces that part.",
    {
        "bot": _s("The bot's slug"),
        "id": _s("The tool id from tico_tool_list"),
        **MCP_PROPERTIES,
        "can": {
            "type": ["array", "string"],
            "items": {"type": "string"},
            "minItems": 1,
            "description": "The full list of what it may do from now on: read, draft, post, act, use, send, write (or a comma list)",
        },
        "scope": {
            "type": ["object", "array"],
            "items": {"type": "string"},
            "description": "Keys to set: database, channels, project, mailbox and the like; '' takes one off",
        },
        "note": _s("The new note; an empty string clears it"),
        "title_prefix": _s("Keep this prefix on the generated BotOps task"),
    },
    required=("bot", "id"),
    writes=True,
)
def tools_update(api, args):
    body = {}
    if args.get("can") is not None:
        can = args["can"]
        body["can"] = [part.strip() for part in can.split(",") if part.strip()] if isinstance(can, str) else can
    if args.get("scope") is not None:
        body["scope"] = _scope_of(args["scope"])
    if args.get("note") is not None:
        body["note"] = args["note"]
    if _mcp_of(args):
        body["mcp"] = _mcp_of(args)
    if not body:
        raise ValueError("Say what changes: can, scope, note or an MCP server's mcp_url, transport or headers")
    if args.get("title_prefix"):
        body["title_prefix"] = args["title_prefix"]
    return _as_person(api).post(f"bots/{args['bot']}/tools/{args['id']}/update", body, key=_key(args))


@tool(
    "tico_tool_remove",
    "Request removal of a tool from yourself or a bot you may change (its id from `tico_tool_list`), or withdraw "
    "a pending request. BotOps changes bot.yaml; an external profile changes its own tools and reports the list.",
    {"bot": _s("The bot's slug"), "id": _s("The tool id from tico_tool_list")},
    required=("bot", "id"),
    writes=True,
)
def tools_remove(api, args):
    return _as_person(api).post(f"bots/{args['bot']}/tools/{args['id']}/delete", {}, key=_key(args))


def _bot_of(api, args):
    return args.get("bot") or api.get("me")["actor"].split(":", 1)[-1]


@tool(
    "tico_slack_channel_list",
    "The Slack channels bots may read and post in: each one's name and id, which bots read it, "
    "whether bots may post there, and its note. Also whether an old registry/slack-channels.yaml "
    "is still waiting to be imported.",
    {},
)
def slack_channel_list(api, args):
    return api.get("slack/channels")


@tool(
    "tico_slack_channel_add",
    "Let a bot read a Slack channel: add the channel to the team's list with the bots that read it, "
    "or add readers to (or change) one already there, as the person who asked you (an owner or an admin; anyone else is "
    "refused, with who to ask). A reader gets what is new in the channel about once an hour. Posting in the channel "
    "is on unless you set post false. A channel shared outside the workspace is refused by Tico whatever this says. "
    "Safe to repeat. Say what you did in one line.",
    {
        "channel": _CHANNEL,
        "readers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Bots' slugs that read it, added to the ones it has",
        },
        "post": {
            "type": "boolean",
            "description": "May bots post there? On for a new channel; leave out to keep it as is",
        },
        "note": _s("What the channel is for"),
        "digest_hours": {
            "type": "number",
            "description": "Readers get it at most once in this many hours (a noisy channel)",
        },
        "name": _s("Its name, when you give the id"),
    },
    required=("channel",),
    writes=True,
)
def slack_channel_add(api, args):
    body = {
        k: args[k] for k in ("channel", "readers", "post", "note", "digest_hours", "name") if args.get(k) is not None
    }
    return _as_person(api).post("slack/channels", body, key=_key(args))


@tool(
    "tico_slack_channel_remove",
    "Take a Slack channel off the team's list, or with `reader` only that bot off its readers, as "
    "the person who asked you (an owner or an admin). Safe to repeat.",
    {"channel": _CHANNEL, "reader": _s("Only this bot stops reading it; leave out to remove the channel")},
    required=("channel",),
    writes=True,
)
def slack_channel_remove(api, args):
    body = {k: args[k] for k in ("channel", "reader") if args.get(k)}
    return _as_person(api).post("slack/channels/remove", body, key=_key(args))


@tool(
    "tico_slack_channel_import",
    "Store the channels an old registry/slack-channels.yaml lists in the team's list, once, as the "
    "person who asked you (an owner or an admin). The file is ignored afterwards.",
    {},
    writes=True,
)
def slack_channel_import(api, args):
    return _as_person(api).post("slack/channels/import", {}, key=_key(args))


@tool(
    "tico_credential_request",
    "Open a card in the conversation for the person to type a secret into: what it is for, the format, "
    "where to get one. The value goes straight to Credentials and is granted to the bot; you never see it. You are woken when "
    "it is saved: then test the connection and report, or open the card again if it fails. Use this whenever a bot needs a "
    "key, token or password. On a task, the card goes in its thread and the task waits for it. Never ask for the value in "
    "words.",
    {
        "env": _s("The variable's name, like JIRA_BASIC_AUTH"),
        "for_bot": _s("The bot that needs it; you, when you leave it out"),
        "label": _s("What it is, for the card's title: 'your Jira login'"),
        "format": _s("The exact shape, as the input's placeholder: you@example.com:API token"),
        "help_url": _s("An https page where they create one, when you know it"),
        "kind": _s("api_key, token, password or connection", enum=["api_key", "token", "password", "connection"]),
    },
    required=("env",),
    writes=True,
)
def credential_request(api, args):
    body = {k: args[k] for k in ("env", "for_bot", "label", "format", "help_url", "kind") if args.get(k)}
    if _as_person(api) is not api:
        body["on_behalf_of"] = "turn"
    return api.post("credential-requests", body, key=_key(args))


@tool(
    "tico_access_request",
    "Ask for a level on a tool in Tools or a GitHub repository that already exists, for yourself (BotOps, the Tools "
    "Owner and people: `for_bot`). Read where Tico can hold it to reads is granted at once; anything else is one card "
    "to each person who can grant it, and the first to act decides. Asking again adds nothing and notifies nobody. You "
    "are woken on your task when it is granted, however it was given: work on something else or end your turn. Use "
    "this for every access need instead of filing a task. A key that is not stored yet is tico_credential_request.",
    {
        "resource": _s(
            "The tool (its name or id, `tool:<name>`) or the repository (`owner/name`, its short name, `repo:<name>`). "
            "When several tools give the same variable, name the one you need"
        ),
        "level": _s("read (the default) or read_write", enum=["read", "read_write"]),
        "for_bot": _s("The bot that needs it; you, when you leave it out"),
        "why": _s("One line for the person: what it is for"),
        "task": _s("The task that waits on it (its full id); the task your turn is on when you leave it out"),
    },
    required=("resource",),
    writes=True,
)
def access_request(api, args):
    body = {k: args[k] for k in ("resource", "level", "for_bot", "why", "task") if args.get(k)}
    return api.post("access-requests", body, key=_key(args))


@tool(
    "tico_access_stats",
    "Access requests in numbers: asked, reached a person, share granted at once, open, median minutes to grant.",
    {"days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "How many days back; 7 by default"}},
)
def access_stats(api, args):
    return api.get("access-requests/stats" + (f"?days={int(args['days'])}" if args.get("days") else ""))


ACCESS = (
    Group("access", "ask for a level on a tool or a repository; the numbers on asking"),
    Command(
        "access request",
        "ask for Read or Read and write on a tool or a repository",
        args=(Arg("for_bot", flag="--for", metavar="BOT"),),
        example="tico access request acme/web --level read --why 'review the release notes'",
    ),
    Command("access stats", "the last days' access requests in numbers", example="tico access stats --days 7"),
)


@tool(
    "tico_credential_set",
    "Store a credential a person gave you in chat, for the bot they name, as them: it is kept in Credentials "
    "(named for the variable) and granted to that bot alone, replacing the old value. Only a credential admin, or someone "
    "who manages the bot, may. Then the pasted words are taken out of the chat and this run's record. Never print, log, "
    "commit or copy the value anywhere else, and tell the person in one line that it was saved and removed from the chat, "
    "and that next time the card keeps it off the model entirely. Prefer tico_credential_request.",
    {
        "env": _s("The variable's name, like JIRA_BASIC_AUTH"),
        "for_bot": _s("The bot that needs it"),
        "value": _s("The secret, exactly as given"),
        "name": _s("What to call it in Credentials; the variable's name by default"),
        "kind": _s("api_key, token, password or connection", enum=["api_key", "token", "password", "connection"]),
        "username": _s("The account it belongs to, if any"),
        "redact": {"type": "boolean", "default": True, "description": "Take the pasted value out of the conversation"},
    },
    required=("env", "for_bot", "value"),
    writes=True,
)
def credential_set(api, args):
    body = {
        "env": args["env"],
        "for_bot": args["for_bot"],
        "value": args["value"],
        **{k: args[k] for k in ("name", "kind", "username") if args.get(k)},
        "redact": args.get("redact", True),
    }
    if _as_person(api) is not api:
        body["on_behalf_of"] = "turn"
    return api.post("credential-set", body)


@tool(
    "tico_credential_list",
    "The credentials this person may see: name, variable, kind and which bots have each. Never a value.",
    {},
)
def credential_list(api, args):
    listing = _as_person(api).get("credentials")
    return {
        "configured": listing.get("configured"),
        "can_manage": listing.get("can_manage"),
        "credentials": [
            {
                "name": c.get("name"),
                "env": c.get("env"),
                "kind": c.get("kind"),
                "stored": c.get("stored"),
                "bots": [g.get("subject") for g in c.get("grants", []) if str(g.get("subject")).startswith("bot:")],
            }
            for c in listing.get("credentials", [])
        ],
    }


def _credential_of(listing, ref):
    """The one credential `ref` names (its name, its id or its variable) among the ones this person may see."""
    want = str(ref or "").strip().lower()
    rows = listing.get("credentials", [])
    hits = [r for r in rows if want in (str(r.get("id")).lower(), str(r.get("name")).lower())] or [
        r for r in rows if str(r.get("env") or "").lower() == want
    ]
    if not hits:
        raise APIError(
            "not_found",
            f"No credential named {ref}. `tico credential list` shows the ones you may see; "
            "to move one out of a bot's own secrets file first: `tico credential import <VARIABLE> --from-bot <bot>`",
        )
    if len(hits) > 1:
        raise APIError(
            "ambiguous",
            f"More than one credential matches {ref}: "
            + ", ".join(str(r.get("name")) for r in hits)
            + ". Name one of them.",
        )
    return hits[0]


def _bot_of_credentials(listing, ref):
    """A bot's slug from its slug or its name, among the bots this person may give credentials to."""
    want = str(ref or "").strip().lower()
    for bot in listing.get("bots", []):
        if want in (str(bot.get("id")).lower(), str(bot.get("name")).lower()):
            return bot["id"]
    return str(ref or "").strip()


@tool(
    "tico_credential_grant",
    "Give a bot a stored credential, as the person who asked you (BotOps and the Tools Owner: a credential "
    "administrator, or the person who stored it, for a bot they own or manage): from then "
    "on every run of that bot has it as its variable. A bot never uses a credential that was not granted to it. Safe to repeat. "
    "Never copy a value from one bot to another: if the credential is only in another bot's own secrets file, "
    "tico_credential_import it first. Then run the bot's own read-only check of the connection. Anyone else is "
    "refused, with who to ask.",
    {"credential": _s("The credential's name (or its variable's name)"), "to_bot": _s("The bot's slug or name")},
    required=("credential", "to_bot"),
    writes=True,
)
def credential_grant(api, args):
    person = _as_person(api)
    listing = person.get("credentials")
    row = _credential_of(listing, args["credential"])
    bot = _bot_of_credentials(listing, args["to_bot"])
    given = person.post(f"credentials/{row['id']}/grants", {"subject": "bot:" + bot}, key=_key(args))
    return {
        "credential": row.get("name"),
        "env": row.get("env"),
        "bot": bot,
        **{k: v for k, v in given.items() if k != "subject"},
    }


@tool(
    "tico_credential_revoke",
    "Take a stored credential away from a bot, as the person who asked you (a credential administrator, "
    "or the person who stored it, for a bot they own or manage). "
    "Its next run no longer has it. Safe to repeat.",
    {"credential": _s("The credential's name (or its variable's name)"), "from_bot": _s("The bot's slug or name")},
    required=("credential", "from_bot"),
    writes=True,
)
def credential_revoke(api, args):
    person = _as_person(api)
    listing = person.get("credentials")
    row = _credential_of(listing, args["credential"])
    bot = _bot_of_credentials(listing, args["from_bot"])
    grants = [g for g in row.get("grants", []) if g.get("subject") == "bot:" + bot]
    for grant in grants:
        person.post(f"credentials/{row['id']}/grants/{grant['id']}/revoke", {}, key=_key(args, ":" + str(grant["id"])))
    return {"credential": row.get("name"), "env": row.get("env"), "bot": bot, "revoked": len(grants)}


@tool(
    "tico_credential_delete",
    "Delete a stored Credential and all its grants. Credential administrators only. "
    "The encrypted value is erased; history keeps metadata only.",
    {"credential": _s("Credential name, id or variable name")},
    required=("credential",),
    writes=True,
)
def credential_delete(api, args):
    person = _as_person(api)
    row = _credential_of(person.get("credentials"), args["credential"])
    if row.get("tool_id"):  # a tool's key goes as its tool's page deletes it (backend/api_tools.py delete_key)
        path = f"api-tools/{quote(str(row['tool_id']), safe='')}/keys/{quote(str(row['id']), safe='')}"
        return person.call("DELETE", path, key=_key(args))
    return person.call("DELETE", f"credentials/{row['id']}", key=_key(args))


@tool(
    "tico_credential_import",
    "Move one variable a bot keeps in its own secrets file (secrets/<bot>.env on its computer) into "
    "Credentials, granted to that bot, as the person who asked you (a credential administrator). The bot's computer reads the "
    "value and sends it to the server itself: you never see it, and the bot keeps working as before. Afterwards "
    "tico_credential_grant can give it to other bots. Waits up to `wait` seconds for the computer to answer; run it again to keep waiting.",
    {
        "env": _s("The variable's name in that bot's file, like JIRA_BASIC_AUTH"),
        "from_bot": _s("The bot whose file has it"),
        "name": _s("What to call it in Credentials; the variable's name by default"),
        "kind": _s("api_key, token, password or connection", enum=["api_key", "token", "password", "connection"]),
        "wait": {"type": "integer", "default": 30, "description": "Seconds to wait for the computer (at most 60)"},
    },
    required=("env", "from_bot"),
    writes=True,
)
def credential_import(api, args):
    import time

    person = _as_person(api)
    bot = _bot_of_credentials(person.get("credentials"), args["from_bot"])
    asked = _for_person(api)
    body = {"env": args["env"], "bot": bot, **{k: args[k] for k in ("name", "kind") if args.get(k)}, **asked}
    row = api.post("credential-imports", body, key=_key(args))
    deadline = time.monotonic() + max(0, min(int(args.get("wait") if args.get("wait") is not None else 30), 60))
    while row.get("state") == "requested" and time.monotonic() < deadline:
        time.sleep(2)
        row = api.get(f"credential-imports/{row['id']}", **asked)
    if row.get("state") == "failed":
        raise APIError("import_failed", row.get("message") or "The computer could not read it")
    done = row.get("state") == "done"
    return {
        "state": row.get("state") if done else "waiting",
        "env": args["env"],
        "bot": bot,
        "credential_id": row.get("credential_id"),
        "detail": (
            "Stored in Credentials and granted to " + bot + ". Its own file is unchanged."
            if done
            else "Its computer has not answered yet; run the same command again to keep waiting."
        ),
    }


@tool("tico_repo_list", "List the team’s repositories and new bot default.", {})
def repo_list(api, args):
    return _as_person(api).get("repositories")


@tool(
    "tico_repo_update",
    "Tick a repository or set its setup command (owner or admin).",
    {"full_name": _s("owner/repo"), "enabled": {"type": "boolean"}, "setup_command": _s("Setup command")},
    required=("full_name",),
    writes=True,
)
def repo_update(api, args):
    return _as_person(api).call(
        "PUT",
        "repositories/" + args["full_name"],
        {k: args[k] for k in ("enabled", "setup_command") if k in args},
        key=_key(args),
    )


@tool(
    "tico_repo_review",
    "Submit this bot's GitHub PR review at an explicit current head. The configured reviewer App "
    "uses APPROVE or REQUEST_CHANGES; the shared App uses COMMENT. Requires an existing repository "
    "grant (write for the shared App). VERDICT is appended automatically. A moved head is refused; a lost response is never blindly resubmitted.",
    {
        "repository": _s("GitHub owner/repository"),
        "number": {"type": "integer", "minimum": 1},
        "head": _s("Reviewed full commit SHA", pattern="^[0-9a-f]{40}$"),
        "verdict": _s("Review verdict", enum=["approve", "changes"]),
        "body": _s("Review findings, excluding the automatically appended VERDICT line", maxLength=60000),
    },
    required=("repository", "number", "head", "verdict", "body"),
    writes=True,
)
def repo_review(api, args):
    body = {key: args[key] for key in ("repository", "number", "head", "verdict", "body")}
    key = args.get("operation_id") or "review-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return api.post("github/reviews", body, key=key)


@tool(
    "tico_repo_product_create",
    "The Owner, or a bot with Create product repositories (every bot, unless the Owner "
    "or an admin switched it off in the bot's settings under Repositories). Private only; a bot that creates one gets write to it and the Owner "
    "is told. First call with `name` and a stable `operation_id` to preview the "
    "connected account or organization, exact repository name, private visibility, empty initialization, and live App "
    "capability. If capability is available, review the preview and call again with `confirm_repository` set "
    "to the exact `org/name`. The create request uses the same stable operation id, so retries replay the saved "
    "receipt and never issue a second GitHub create.",
    {
        "name": _s("Exact product repository name, without owner or bot- prefix"),
        "confirm_repository": _s("Exact org/name from the preview; omit on the preview call"),
    },
    required=("name", "operation_id"),
    writes=True,
)
def repo_product_create(api, args):
    operation_id = args.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise APIError("operation_id", "A stable operation_id is required for this create flow", 422)
    key = _key(args, ":product-repository")
    if len(key) > 200:
        raise APIError("operation_id", "operation_id is too long for a product-repository idempotency key", 422)

    preview = api.get("github/product-repos/preview", name=args["name"])
    repository = f"{preview.get('org', '')}/{preview.get('name', '')}"
    if (
        preview.get("name") != args["name"]
        or preview.get("repository") != repository
        or preview.get("visibility") != "private"
        or preview.get("auto_init") is not False
    ):
        raise APIError(
            "product_repository_preview",
            "The server returned an invalid product-repository preview; no repository was created",
            502,
        )

    if preview.get("capability") != "available":
        return {"preview": preview, "created": False, "confirmation_required": False}
    if not args.get("confirm_repository"):
        return {"preview": preview, "created": False, "confirmation_required": True}
    if args["confirm_repository"] != repository:
        raise APIError(
            "confirmation_required",
            f"To create this repository, confirm the exact preview target {repository}; no repository was created",
            409,
        )

    return api.post(
        "github/product-repos",
        {
            "org": preview["org"],
            "name": preview["name"],
            "visibility": preview["visibility"],
            "auto_init": preview["auto_init"],
            "confirmed": True,
        },
        key=key,
    )


@tool(
    "tico_tool_list",
    "Every tool the team uses (each outside system): how you reach it (`access`), its credentials "
    "or env names, how it is declared, writes, and query and learning counts. Read this before "
    "touching an outside system; `tico_tool_show` is the full page. With `bot`, the tools that "
    "bot uses (its model and harness, its repository, each declared tool), with who it acts as, "
    "what it may do, its scope and a status (ready, problem, unknown, or pending while BotOps "
    "adds it).",
    {"bot": _s("A bot's slug, or `me`: that bot's own tools instead of the team's")},
)
def tool_list(api, args):
    if args.get("bot"):
        bot = _bot_of(api, {"bot": None if args["bot"] == "me" else args["bot"]})
        reader = _as_person(api) if kind_of(api.get("me")) == "botops" else api
        return reader.get(f"bots/{bot}/tools")
    return api.get("tools")


@tool(
    "tico_tool_show",
    "One tool's page: how you reach it, what you may do, its "
    "queries and the learnings. Read it before using an outside system.",
    {"service": _s("Tool name")},
    required=("service",),
)
def tool_show(api, args):
    return api.get("tools/" + args["service"])


@tool(
    "tico_tool_query_search",
    "Search a tool's ready-made query catalog, or fetch one by id.",
    {
        "service": _s("Tool name"),
        "term": _s("Words to match against id, title, description, tags and SQL", default=""),
        "id": _s("One query id: returns its SQL and params"),
    },
    required=("service",),
)
def queries(api, args):
    return api.get(f"tools/{args['service']}/queries", term=args.get("term") or None, id=args.get("id"))


@tool(
    "tico_tool_learn",
    "Add a reusable learning about a tool: a limit, a working command, a gotcha.",
    {"service": _s("Tool name"), "text": _s("The learning")},
    required=("service", "text"),
    writes=True,
)
def learn(api, args):
    return api.post(f"tools/{args['service']}/learnings", {"text": args["text"]}, key=_key(args))


# ----------------------------------------------------------------------------- tool index (backend/api_tools.py)
@tool(
    'tico_tool_index',
    'The Tools you may see (API tools, mailboxes, numbers, Slack, GitHub, webhooks...): kind, '
    'status, way, who can use it, needs you. Page with cursor.',
    {'cursor': _s('The previous page\'s next'), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500}},
)
def tool_index(api, args):
    page = dict(limit=args.get('limit'), cursor=args.get('cursor'))
    return _as_person(api).get('tool-index', **{k: v for k, v in page.items() if v is not None})


@tool(
    'tico_tool_check',
    'Check an API tool\'s keys now: Working, Key rejected or Check failed, per key.',
    {'id': _s('Its tico_tool_index row id, without api:')},
    required=('id',),
)
def tool_check(api, args):
    return _as_person(api).post('api-tools/' + quote(str(args['id']).removeprefix('api:'), safe='') + '/check', {})


# A path segment that names one record (an id, a number, a hash), folded to `:id` so one endpoint's calls count together.
# A version (`v2`, `v26.0`) or a dated API version (`2010-04-01`) is part of the endpoint and stays.
_RECORD = re.compile(r'^\d+$|^(?!v\d+(\.\d+)*$)(?!\d{4}-\d{2}-\d{2}$)(?=.*\d)[A-Za-z0-9_.:@=-]{6,}$')


def _endpoint(path):
    """`/v1/customers/cus_8f2k91/charges?limit=` as `/v1/customers/:id/charges?limit=`: the call's shape, never a value
    (the proxy keeps a query's keys, not its values)."""
    base, mark, query = str(path or '').partition('?')
    return '/'.join(':id' if _RECORD.match(part) else part for part in base.split('/')) + mark + query


def activity_summary(rows):
    """The page's calls by caller, method, endpoint, status and result, most frequent first: what a tool is used for and
    which error keeps coming back."""
    groups = {}
    for row in rows:
        caller = row.get('bot') or row.get('actor') or ''
        shape = (caller, row.get('method') or '', _endpoint(row.get('path')), row.get('status_code'), row['result'])
        seen = groups.setdefault(shape, {'count': 0, 'last_at': row['at']})
        seen['count'] += 1
        seen['last_at'] = max(seen['last_at'], row['at'])
    fields = ('caller', 'method', 'endpoint', 'status_code', 'result')
    newest = sorted(groups.items(), key=lambda kv: kv[1]['last_at'], reverse=True)
    return [
        {**dict(zip(fields, shape, strict=True)), **seen}
        for shape, seen in sorted(newest, key=lambda kv: -kv[1]['count'])
    ]


@tool(
    'tico_tool_activity',
    'An API tool\'s recent activity, newest first: each call through the proxy (who, method, path, status, result) and, '
    'for keys on computers, each caller\'s last key use; a summary of repeated calls and errors; who added its keys. '
    'Never a value. The Tools page\'s owner reads every caller; others their own and their bots\'. Page with cursor.',
    {
        'id': _s('Its tico_tool_index row id, without api:'),
        'since': _s('Only rows from this window on: 24h, 7d or an ISO time'),
        'result': {
            'type': 'string',
            'enum': ['sent', 'refused', 'failed', 'use'],
            'description': 'sent: reached the vendor (any status); refused: stopped by Tico; failed: vendor unreachable',
        },
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200},
        'cursor': _s('The previous page\'s next'),
    },
    required=('id',),
)
def tool_activity(api, args):
    reader = _as_person(api)
    tool_id = quote(str(args['id']).removeprefix('api:'), safe='')
    query = {k: args[k] for k in ('since', 'result', 'limit', 'cursor') if args.get(k) not in (None, '')}
    page = reader.get(f'api-tools/{tool_id}/activity', **query)
    detail = reader.get(f'api-tools/{tool_id}')
    shown = ('id', 'name', 'vendor', 'service', 'base_url', 'auth_scheme', 'check_path', 'delivery')
    return {
        'tool': {k: (detail.get('tool') or {}).get(k) for k in shown},
        'status': detail.get('status'),
        'added_by': sorted({k['created_by'] for k in detail.get('keys') or [] if k.get('created_by')}),
        'summary': activity_summary(page.get('rows') or []),
        'rows': page.get('rows') or [],
        'next': page.get('next'),
    }


@tool(
    'tico_tool_level',
    'Set a bot\'s level on an API tool: read, read_write or none. It grants the one key that level uses and takes the '
    'others away. A credential administrator; the Tools Owner as the person who asked it. Never yourself.',
    {
        'id': _s('Its tico_tool_index row id, without api:'),
        'bot': _s('The bot\'s slug'),
        'level': {'type': 'string', 'enum': ['read', 'read_write', 'none']},
    },
    required=('id', 'bot', 'level'),
    writes=True,
)
def tool_level(api, args):
    tool_id = quote(str(args['id']).removeprefix('api:'), safe='')
    subject = quote('bot:' + str(args['bot']).removeprefix('bot:'), safe='')
    return _as_person(api).call('PUT', f'api-tools/{tool_id}/access/{subject}', {'level': args['level']}, _key(args))


@tool(
    'tico_tools_propose_grant',
    'Tools owner only: propose which tool and bots each key a person dropped in your Tools chat goes to. The server '
    'builds a card of rows (key, tool, level, bots) in that chat; only the person\'s Grant on it grants, then the tool '
    'check runs. Keys are named as the message showed them ([saved NAME]); never the owner, every bot or every computer.',
    {
        'thread_id': _s('Your chat with the person (the thread the [saved NAME] message is in)'),
        'rows': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'key': _s('The key\'s name, e.g. JIRA_LOGIN'),
                    'tool': _s('The tool\'s id or name, as tico_tool_index shows it'),
                    'level': {'type': 'string', 'enum': ['read', 'read_write']},
                    'bots': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Bot slugs'},
                },
                'required': ['key', 'tool', 'level', 'bots'],
            },
        },
        'note': _s('One line above the card (optional)'),
    },
    required=('thread_id', 'rows'),
    writes=True,
)
def tools_propose_grant(api, args):
    return api.post(
        'tools-chat/proposals',
        {'thread_id': args['thread_id'], 'rows': args['rows'], 'note': args.get('note') or ''},
        key=_key(args),
    )


@tool(
    'tico_tool_call',
    'Call a proxied API tool: METHOD and vendor API path, through the Tico proxy (it adds the key '
    'and logs the call). tool: the id in TICO_TOOL_<ID>_URL. Returns {status, content_type, body}.',
    {
        'tool': _s('The API tool id, e.g. close-crm'),
        'method': {'type': 'string', 'enum': ['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE']},
        'path': _s('The vendor API path, e.g. /api/v1/me/'),
        'data': {'description': 'A JSON body'},
        'query': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Query parameters, each key=value'},
    },
    required=('tool', 'method', 'path'),
    local=True,
)
def tool_call(api, args):
    from clients import tool_call as T

    try:
        return T.call(args['tool'], args['method'], args['path'], args.get('data'), args.get('query') or ())
    except T.CallError as exc:
        return {'error': str(exc)}


def query_search(queries, term):
    """Every query whose id, title, description, category, tags or SQL carries every word of `term`."""
    words = str(term or "").lower().split()

    def text(q):
        return " ".join(
            [
                q["id"],
                q["title"],
                q.get("description", ""),
                q.get("category", ""),
                " ".join(q.get("tags") or []),
                q.get("sql") or json.dumps(q.get("mongo")),
            ]
        ).lower()

    return [q for q in queries if all(w in text(q) for w in words)]


# ----------------------------------------------------------------------------- `tico tool` and `tico repo` (cli_spec)
# The command lines of the tools above (clients/cli_spec.py). `tool call` is still built in clients/hubcli.py: it calls
# the vendor through this computer's proxy. So are `repo review`, which reads --body-file here, and `repo
# product-create`, which asks for the exact name at this terminal.
def integrations_text(rows):
    blocks = []
    for row in rows:
        head = (
            f"{row['service']:<24} {row['kind']:<8} {row['writes']:<9} {row['summary']}"
            f"  [{row['query_count']} queries, {row['learning_count']} learnings]"
        )
        extra = [f"  credentials: {c}" for c in (row.get("credentials") or [])]
        if row.get("access"):
            extra.append(f"  access: {row['access']}")
        blocks.append("\n".join([head, *extra]))
    return "\n\n".join(blocks) or "no integrations"


def integration_text(page):
    out = [
        f"# {page['title']} ({page['service']}, {page['kind']}, writes: {page['writes']}, owner: {page['owner']})",
        "",
        page["summary"],
        "",
        f"Access: {page['access']}",
        "Credentials:",
    ]
    out += [f"  - {c}" for c in page["credentials"]]
    out += ["Declared in bot.yaml as:"] + ["  " + line for line in page["declared_as"].rstrip().splitlines()]
    out += ["", page["body"].rstrip()]
    if page["queries"]:
        out += [
            "",
            f"## Queries ({len(page['queries'])}; `tico tool query-search {page['service']} <term>` searches, `--id <id>` prints one)",
        ]
        out += [f"  {q['id']:<44} {q['title']}" for q in page["queries"]]
    notes = page.get("learnings") or []
    out += ["", f"## Learnings ({len(notes)}; add one with `tico tool learn {page['service']} \"...\"`)"]
    out += [f"  {n['created_at'][:10]}  {n['actor']}: {n['text']}" for n in notes] or ["  none yet"]
    return "\n".join(out)


def query_text(query):
    out = [
        f"{query['id']}: {query['title']}",
        query.get("description", ""),
        f"database: {query.get('database', '')}  category: {query.get('category', '')}  tags: {', '.join(query.get('tags') or [])}",
    ]
    if query.get("params"):
        out.append("params:")
        for p in query["params"]:
            extra = (" required" if p.get("required") else "") + (f" default={p['default']}" if "default" in p else "")
            out.append(f"  {p['name']} ({p.get('type', 'text')}): {p.get('label', p['name'])}{extra}")
    out += ["", query["sql"].rstrip() if "sql" in query else json.dumps(query.get("mongo"), indent=2)]
    return "\n".join(out)


def queries_text(rows):
    return "\n".join(f"{q['id']:<44} {q['title']}  — {q.get('description', '')}" for q in rows) or "no matching queries"


def _tool_list(client, fields):
    """With a bot, its tools (the tool). Without, the team's rows from GET /tools, which a runner credential may read
    outside a turn (/me, which the tool asks first for a bot, would refuse a runner)."""
    from clients import hubtools

    if fields.get("bot"):
        return hubtools.BY_NAME["tico_tool_list"]["fn"](client, fields)
    return client.get("tools")["integrations"]


def _query_search(client, fields):
    """The catalog searched here, from the tool's page (GET /tools/<service>), which a runner may read outside a turn."""
    page = client.get("tools/" + fields["service"])
    if fields.get("id"):
        for query in page["queries"]:
            if query["id"] == fields["id"]:
                return query
        raise APIError("not_found", f"No query {fields['id']} under {page['service']}", 404)
    return query_search(page["queries"], fields.get("term"))


def _no_key(fields):
    """The arguments without an operation id: `repo tick` and `repo untick` never sent one."""
    return {k: v for k, v in fields.items() if k != "operation_id"}


_MCP_ARGS = (Arg("headers", flag="--header", action="append", metavar="'NAME: VALUE'"),)
_SCOPE = Arg("scope", action="append", metavar="KEY=VALUE")

TOOL = (
    Group(
        "tool",
        "what the team uses: each tool, what a bot uses, its model, repository and declared tools (docs/creating-bots.md)",
    ),
    Command(
        "tool list",
        "the team's tools; with --bot, that bot's tools with their status",
        run=_tool_list,
        render=lambda args, result: json.dumps(result, indent=2) if args.bot else integrations_text(result),
        example="tico tool list --bot me",
    ),
    Command(
        "tool show",
        "one tool: the page, its queries and the learnings",
        render=lambda args, result: integration_text(result),
        example="tico tool show posthog",
    ),
    Command(
        "tool index", "Tools: every API tool, mailbox, number and connection you may see", example="tico tool index"
    ),
    Command("tool check", "check an API tool's keys now", example="tico tool check linear"),
    Command(
        "tool activity",
        "an API tool's recent calls and key uses, with repeated calls and errors summed up; never a value",
        args=(Arg("id", metavar="tool"),),
        example="tico tool activity linear --since 24h",
    ),
    Command(
        "tool level",
        "set a bot's level on an API tool: read, read_write or none",
        example="tico tool level linear ops read",
    ),
)
# After the hand-built `tool call`, so `tico tool` lists its leaves in the order it always has.
TOOL_AFTER_CALL = (
    Command(
        "tool query-search",
        "search a tool's query catalog",
        args=(Arg("term", positional=True, nargs="?", default=""),),
        run=_query_search,
        render=lambda args, result: query_text(result) if args.id else queries_text(result),
        example="tico tool query-search posthog signups",
    ),
    Command(
        "tool learn", 'add a learning: tico tool learn <service> "<text>"', example='tico tool learn posthog "..."'
    ),
    Command(
        "tool report",
        "report the external profile's complete current tool list",
        args=(Arg("tools", action="store"),),
        convert=lambda fields: {**fields, "tools": json.loads(fields["tools"])},
        example="tico tool report '[...]'",
    ),
    Command(
        "tool add",
        "register a tool: BotOps adds it to bot.yaml; never a credential value",
        args=(Arg("can", flag="--can", action="store"), _SCOPE, *_MCP_ARGS),
        example="tico tool add analyst posthog --can read --env POSTHOG_KEY",
    ),
    Command(
        "tool update",
        "change a declared tool's can, scope or note in place; never remove and add again",
        args=(Arg("bot", flag="--bot"), Arg("can", flag="--can", action="store"), _SCOPE, *_MCP_ARGS),
        example="tico tool update posthog --bot analyst --can read,write",
    ),
    Command(
        "tool remove",
        "ask BotOps to remove a tool, or withdraw a pending request",
        example="tico tool remove analyst posthog",
    ),
)

REPO = (
    Group("repo", "team repositories"),
    Command("repo list", example="tico repo list"),
    Command(
        "repo tick",
        tool="tico_repo_update",
        fixed=(("enabled", True),),
        omit=("setup_command",),
        convert=_no_key,
        example="tico repo tick acme/web",
    ),
    Command(
        "repo untick",
        tool="tico_repo_update",
        fixed=(("enabled", False),),
        omit=("setup_command",),
        convert=_no_key,
        example="tico repo untick acme/web",
    ),
)
