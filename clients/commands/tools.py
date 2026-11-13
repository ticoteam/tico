"""Tools (the outside services bots reach through Tico), credentials, repositories and Slack.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import hashlib
import json
from urllib.parse import quote
from clients.tico import APIError

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
    "Register a tool for a bot you manage (BotOps: one the person who asked you manages): a `tools:` entry for its bot.yaml. The server checks "
    "it and opens a task with the exact YAML for BotOps, or for an External agent profile to configure itself. The tool stays pending until a tool report lists "
    "it. Names and verbs only: never a Credential value. `env` names a stored Credential granted to the bot. To give the bot a vendor's remote MCP server, add `mcp_url`, `transport` and `headers` (the "
    "credential is a ${VAR} placeholder for `env`).",
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
    "Request removal of a tool from a bot you manage (its id from `tico_tool_list`), or withdraw "
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
    "whether bots may post there, and its note. Also whether an old registry/slack-channels.yaml is still waiting to be imported.",
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
    "Give a bot a stored credential, as the person who asked you (a credential administrator, or the "
    "person who stored it, for a bot they own or manage): from then "
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
    "Every tool the team uses (each outside system), and what you need to use it: how you reach it "
    "(`access`), the credentials or env names, how it is declared, writes, and query/learning counts. Read this "
    "list before touching an outside system; `tico_tool_show` is the full page. With `bot`, the tools that bot uses, "
    "as its page shows them: its model and harness, its repository and each declared tool, with who it acts as, what "
    "it may do, its scope and a status (ready, problem, unknown, or pending while BotOps is adding it).",
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
)
def tools_propose_grant(api, args):
    return api.post(
        'tools-chat/proposals', {'thread_id': args['thread_id'], 'rows': args['rows'], 'note': args.get('note') or ''}
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
