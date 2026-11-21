"""Every Tools change from the `tico` CLI and the Tico MCP, so no bot (and no person's own agent) needs the Tools page.

Each tool calls the route the Tools page calls, with the caller's rights: BotOps as the person who asked it
(`_as_person`), the Tools Owner as that person on the Tools changes the server lends it (backend/tools_delegation.py),
everyone else as themselves. Whether a change is allowed is backend/tool_access.py `may_change_access`, the one rule;
a caller without the right gets the route's refusal, which names who can. Nothing here decides a permission.

ROUTES maps each Tools write route to the tool that calls it, and UI_ONLY names the few only a person makes in the app,
each with why. backend/tests/test_tools_parity.py reads the app's route table and fails on a Tools write route in
neither, and checks that each tool really calls its route. The table for people is docs/connect-tools.md ("Every Tools
change from the CLI and MCP").

A value never goes in a tool's arguments: `from_env` names a variable and `from_file` a file on the computer that runs
the command, read there. Those tools are `local` (the server's own MCP endpoint never serves them: it must not read its
own environment or files for a caller). No answer carries a value or its preview.
"""

import argparse
import os
from urllib.parse import quote

from clients.cli_spec import NULL, Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._requester import BOTS, REQUESTER, _as_person, kind_of
from clients.commands._schemas import _key, _s
from clients.commands.tools import _credential_of
from clients.tico import APIError

VALUE_MAX = 100_000  # the vault's own cap on a value (backend/api_tools.py KeyCreate)
LEVEL = {"type": "string", "enum": ["read", "read_write"], "description": "read or read_write"}
KIND = _s("api_key, token, password or connection", enum=["api_key", "token", "password", "connection"])
TOOL_ID = _s("The tool's id as tico_tool_index shows it (api:<id> or <id>)")
WEBSITE_ID = _s("The website tool's id as tico_tool_index shows it (website:<id> or <id>)")
AUDIENCE_PROP = {
    "type": ["object", "null"],
    "description": 'Who can see it: null for everyone, or Only listed {"people": [...], "teams": [...], "bots": [...]}',
}


def _from(what, prefix=""):
    return {
        prefix + "from_env": _s(
            f"The name of a variable in this computer's environment that holds {what}; never the value"
        ),
        prefix + "from_file": _s(
            f"A file on this computer that holds {what} (a trailing newline is dropped); never the value"
        ),
    }


def secret_from(args, prefix=""):
    """The value `<prefix>from_env` or `<prefix>from_file` names, read on this computer, or None when neither is given.
    An error names the variable or the file, never what it holds."""
    env, path = args.get(prefix + "from_env"), args.get(prefix + "from_file")
    if env and path:
        raise ValueError(f"Give {prefix}from_env or {prefix}from_file, not both")
    if env:
        value = os.environ.get(env, "")
        if not value:
            raise ValueError(f"{env} is not set in this command's environment")
        return value
    if not path:
        return None
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            value = handle.read(VALUE_MAX + 3)  # a key and its line ending, and one more to see the file is longer
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"{path} could not be read as text") from None
    value = value[:-2] if value.endswith("\r\n") else value[:-1] if value.endswith("\n") else value
    if not value:
        raise ValueError(f"{path} is empty")
    if len(value) > VALUE_MAX:
        raise ValueError(f"{path} is longer than a key may be ({VALUE_MAX} characters)")
    return value


def _hide(result):
    """The answer with every `preview` (a value's first and last characters) taken out: a transcript keeps none."""
    if isinstance(result, dict):
        return {k: _hide(v) for k, v in result.items() if k != "preview"}
    if isinstance(result, list):
        return [_hide(v) for v in result]
    return result


def _q(value, prefix=""):
    return quote(str(value or "").strip().removeprefix(prefix), safe="")


def _api_tool(args, name="id"):
    return _q(args[name], "api:")


def _website(args, name="id"):
    return _q(args[name], "website:")


def _given(args, names):
    return {k: args[k] for k in names if args.get(k) is not None}


def _a_bot(api):
    return kind_of(api.get("me")) in BOTS


def _own_key(api, args):
    """The caller's bot id when it stores a key for itself: a bot (not BotOps, which stores for the person who asked it)
    naming no other bot in `for_bot`. None otherwise."""
    me = api.get("me")
    if kind_of(me) not in ("bot", "agent"):
        return None
    mine = str(me.get("actor") or "").removeprefix("bot:")
    named = str(args.get("for_bot") or "").removeprefix("bot:")
    return mine if mine and named in ("", mine, str(me.get("slug") or "")) else None


# ----------------------------------------------------------------------------- API and MCP tools
@tool(
    "tico_tool_detail",
    "One API or MCP tool as its Tools page shows it: address, delivery, its keys (name, level, variable; never a "
    "value), who uses it at which level, and its settings.",
    {"id": TOOL_ID},
    required=("id",),
)
def tool_detail(api, args):
    return _hide(_as_person(api).get("api-tools/" + _api_tool(args)))


@tool(
    "tico_tool_vendor",
    "Everything of one vendor you may see (its API and MCP tools with keys, levels and settings, its website logins).",
    {"vendor": _s("The vendor as tico_tool_index's `vendor` shows it, such as raygun")},
    required=("vendor",),
)
def tool_vendor(api, args):
    return _hide(_as_person(api).get("tool-vendors/" + _q(args["vendor"])))


@tool(
    "tico_tool_create",
    "Add an API tool. A known service fills in its address, auth and check path.",
    {
        "name": _s("What people call it, such as Linear"),
        "service": _s("The service, such as linear; matched from the name when left out"),
        "base_url": _s("The API's https address"),
        "auth_scheme": _s("bearer, basic_user, basic_password, header:<Name> or query:<param>"),
        "check_path": _s("A cheap authenticated GET on the address, such as /v1/me"),
    },
    required=("name",),
    writes=True,
)
def tool_create(api, args):
    body = _given(args, ("name", "service", "base_url", "auth_scheme", "check_path"))
    return _as_person(api).post("api-tools", body, key=_key(args))


@tool(
    "tico_tool_edit",
    "Change an API tool: rename it, its address, auth or check path, its delivery (proxy: Tico holds the key; "
    "computer: runs get the value), Everyone can read, or who can see it. Only what you send changes.",
    {
        "id": TOOL_ID,
        "name": _s("The new name"),
        "service": _s("The service"),
        "base_url": _s("The API's https address"),
        "auth_scheme": _s("bearer, basic_user, basic_password, header:<Name> or query:<param>"),
        "check_path": _s("A cheap authenticated GET on the address"),
        "delivery": _s("proxy or computer", enum=["proxy", "computer"]),
        "everyone_reads": {"type": "boolean", "description": "Every bot reads it with no grant (on or off)"},
        "audience": AUDIENCE_PROP,
    },
    required=("id",),
    writes=True,
)
def tool_edit(api, args):
    body = _given(args, ("name", "service", "base_url", "auth_scheme", "check_path", "delivery", "everyone_reads"))
    if "audience" in args:
        body["audience"] = args["audience"]
    if not body:
        raise ValueError("Say what changes: name, address, auth, check path, delivery, everyone_reads or audience")
    return _hide(_as_person(api).patch("api-tools/" + _api_tool(args), body, key=_key(args)))


@tool(
    "tico_tool_delete",
    "Delete an API tool with its keys and settings. Every bot loses it at once.",
    {"id": TOOL_ID},
    required=("id",),
    writes=True,
)
def tool_delete(api, args):
    return _as_person(api).call("DELETE", "api-tools/" + _api_tool(args), key=_key(args))


@tool(
    "tico_tool_setting_add",
    "Add a setting to a tool: a non-secret variable every run that uses the tool gets (a host, region, project id). "
    "A value that looks like a key is refused: store it with tico_credential_add.",
    {"id": TOOL_ID, "name": _s("The variable, such as POSTHOG_HOST"), "value": _s("Its plain value")},
    required=("id", "name", "value"),
    writes=True,
)
def tool_setting_add(api, args):
    body = {"name": args["name"], "value": args["value"]}
    return _as_person(api).post(f"api-tools/{_api_tool(args)}/settings", body, key=_key(args))


def _setting(person, args):
    """A setting's id from its id or its name, on the tool `args` names."""
    ref = str(args["setting"]).strip()
    for row in person.get("api-tools/" + _api_tool(args)).get("settings") or []:
        if ref in (row.get("id"), row.get("name")):
            return _q(row["id"])
    raise APIError("not_found", f"The tool has no setting {ref}: tico tool detail lists them")


@tool(
    "tico_tool_setting_change",
    "Rename a tool's setting or change its value.",
    {
        "id": TOOL_ID,
        "setting": _s("The setting's name or id"),
        "name": _s("Its new name"),
        "value": _s("Its new value"),
    },
    required=("id", "setting"),
    writes=True,
)
def tool_setting_change(api, args):
    body = _given(args, ("name", "value"))
    if not body:
        raise ValueError("Say what changes: name or value")
    person = _as_person(api)
    return person.patch(f"api-tools/{_api_tool(args)}/settings/{_setting(person, args)}", body, key=_key(args))


@tool(
    "tico_tool_setting_remove",
    "Take a setting off a tool.",
    {"id": TOOL_ID, "setting": _s("The setting's name or id")},
    required=("id", "setting"),
    writes=True,
)
def tool_setting_remove(api, args):
    person = _as_person(api)
    return person.call("DELETE", f"api-tools/{_api_tool(args)}/settings/{_setting(person, args)}", key=_key(args))


@tool(
    "tico_tool_mcp_probe",
    "Ask a remote MCP server how it signs in: a token, an OAuth sign-in, or none.",
    {"url": _s("The server's https address, such as https://mcp.linear.app/mcp")},
    required=("url",),
)
def tool_mcp_probe(api, args):
    return _as_person(api).post("mcp-tools/probe", {"url": args["url"]})


@tool(
    "tico_tool_mcp_add",
    "Add a vendor's remote MCP server as a tool: with a token read from this computer (auth token), or ready for its "
    "OAuth sign-in (auth oauth; then tico_tool_sign_in). A token tool is checked at once.",
    {
        "name": _s("What people call it"),
        "url": _s("The server's https address"),
        "auth": _s("token or oauth", enum=["token", "oauth"]),
        "header": _s("The header the token goes in; Authorization (Bearer) by default"),
        **_from("the token", "token_"),
        "client_id": _s("For a sign-in with no dynamic registration: the client id made for Tico"),
        **_from("that client's secret", "client_secret_"),
    },
    required=("name", "url", "auth"),
    writes=True,
    local=True,
)
def tool_mcp_add(api, args):
    token, client_secret = secret_from(args, "token_"), secret_from(args, "client_secret_")
    if args["auth"] == "token" and token is None:
        raise ValueError("A token tool needs token_from_env or token_from_file")
    body = {
        **_given(args, ("name", "url", "auth", "header", "client_id")),
        **({"token": token} if token is not None else {}),
        **({"client_secret": client_secret} if client_secret is not None else {}),
    }
    return _hide(_as_person(api).post("mcp-tools", body, key=_key(args)))


@tool(
    "tico_tool_sign_in",
    "Start (or redo) an MCP tool's OAuth sign-in: the address a person opens to sign in. Send it to them; the tool is "
    "connected when they finish.",
    {"id": TOOL_ID},
    required=("id",),
    writes=True,
)
def tool_sign_in(api, args):
    return _as_person(api).post(f"api-tools/{_api_tool(args)}/sign-in", {}, key=_key(args))


@tool(
    "tico_tool_forget",
    "Delete a learning from a tool's page (the owner only).",
    {"service": _s("Tool name"), "id": _s("The learning's id, from tico_tool_show")},
    required=("service", "id"),
    writes=True,
)
def tool_forget(api, args):
    return _as_person(api).post(f"tools/{_q(args['service'])}/learnings/{_q(args['id'])}/delete", {}, key=_key(args))


# ----------------------------------------------------------------------------- auth templates (backend/tool_templates.py)
@tool(
    "tico_tool_templates",
    "The known auth templates: each vendor account's values (secret, id, setting) and how a call carries them.",
    {},
)
def tool_templates(api, args):
    return _as_person(api).get("tool-templates")


@tool(
    "tico_tool_template_set",
    "Set a tool's auth template: a known one by id (twilio), the tool's own {fields, ways}, or none (null).",
    {
        "id": TOOL_ID,
        "template": {
            "type": ["string", "object", "null"],
            "description": "A known template's id, the tool's own {fields, ways}, or null for none",
        },
    },
    required=("id", "template"),
    writes=True,
)
def tool_template_set(api, args):
    template = args["template"]
    template = {"id": template} if isinstance(template, str) else template
    path = f"api-tools/{_api_tool(args)}/template"
    return _hide(_as_person(api).call("PUT", path, {"template": template}, _key(args)))


@tool(
    "tico_tool_ungroup",
    "Split a tool Tico grouped from several back into the tools it was made of, with their keys and settings.",
    {"id": TOOL_ID},
    required=("id",),
    writes=True,
)
def tool_ungroup(api, args):
    return _hide(_as_person(api).post(f"api-tools/{_api_tool(args)}/ungroup", {}, key=_key(args)))


PASTE_TEMPLATE = _s("A known template's id to match the values to; found from the names when left out")


@tool(
    "tico_tool_classify",
    "What a vendor account's values are (secret, id or setting, by template field), read from a variable or a file on "
    "this computer (lines NAME=value). Nothing is stored and no value is answered.",
    {**_from("the values"), "template": PASTE_TEMPLATE},
    local=True,
)
def tool_classify(api, args):
    text = secret_from(args)
    if text is None:
        raise ValueError("Say where the values are: from_env or from_file, never the values")
    body = {"text": text, **_given(args, ("template",))}
    return _hide(_as_person(api).post("tool-templates/classify", body))


@tool(
    "tico_tool_paste",
    "Store a vendor account's values on a tool in one step, read from a variable or a file on this computer (lines "
    "NAME=value): each secret as a key, each id or setting as a setting, and the template when one matches.",
    {"id": TOOL_ID, **_from("the values"), "template": PASTE_TEMPLATE},
    required=("id",),
    writes=True,
    local=True,
)
def tool_paste(api, args):
    text = secret_from(args)
    if text is None:
        raise ValueError("Say where the values are: from_env or from_file, never the values")
    body = {"text": text, **_given(args, ("template",))}
    return _hide(_as_person(api).post(f"api-tools/{_api_tool(args)}/paste", body, key=_key(args)))


# ----------------------------------------------------------------------------- website tools
SITES = {
    "type": ["array", "string"],
    "items": {"type": "string"},
    "description": "Its sites, such as app.example.com (subdomains count); a list or one per flag",
}


def _sites(value):
    return [value] if isinstance(value, str) else value


@tool(
    "tico_tool_website_show",
    "One website tool: its sites, and its accounts (username, holder, who uses each; never a password).",
    {"id": WEBSITE_ID},
    required=("id",),
)
def tool_website_show(api, args):
    return _hide(_as_person(api).get("website-tools/" + _website(args)))


@tool(
    "tico_tool_website_add",
    "Add a website tool: the sites bots sign in to. Then add its accounts with tico_credential_login_add.",
    {
        "name": _s("What people call it; the first site by default"),
        "sites": SITES,
        "central_only": {"type": "boolean", "description": "Only the central browser signs in there"},
    },
    required=("sites",),
    writes=True,
)
def tool_website_add(api, args):
    body = {"sites": _sites(args["sites"]), **_given(args, ("name", "central_only"))}
    return _as_person(api).post("website-tools", body, key=_key(args))


@tool(
    "tico_tool_website_edit",
    "Change a website tool: its name, its sites, central only, or who can see it. Only what you send changes.",
    {
        "id": WEBSITE_ID,
        "name": _s("The new name"),
        "sites": SITES,
        "central_only": {"type": "boolean", "description": "Only the central browser signs in there (on or off)"},
        "audience": AUDIENCE_PROP,
    },
    required=("id",),
    writes=True,
)
def tool_website_edit(api, args):
    body = _given(args, ("name", "central_only"))
    if args.get("sites") is not None:
        body["sites"] = _sites(args["sites"])
    if "audience" in args:
        body["audience"] = args["audience"]
    if not body:
        raise ValueError("Say what changes: name, sites, central_only or audience")
    return _hide(_as_person(api).patch("website-tools/" + _website(args), body, key=_key(args)))


@tool(
    "tico_tool_website_delete",
    "Delete a website tool with its accounts.",
    {"id": WEBSITE_ID},
    required=("id",),
    writes=True,
)
def tool_website_delete(api, args):
    return _as_person(api).call("DELETE", "website-tools/" + _website(args), key=_key(args))


# ----------------------------------------------------------------------------- keys
@tool(
    "tico_credential_add",
    "Store a key you already hold (one you made through a vendor's API) without it entering your conversation: name "
    "the variable (from_env) or file (from_file) on this computer that holds it. A bot stores it as its own key: "
    "given to it alone and back in its next run under `env` (default: the from_env name), on a tool of its own under "
    "`tool`'s vendor. A person (or BotOps for one): with `tool`, it becomes that tool's key at `level` (with `for_bot`, "
    "that bot gets the level); with `for_bot` alone, it is granted to that bot; else it is stored for its vendor's "
    "tool. A person's own key comes through tico_credential_request instead.",
    {
        "name": _s("What to call it, such as Linear (Ops)"),
        "env": _s("The variable runs read it under, such as LINEAR_API_KEY"),
        **_from("the value"),
        "tool": _s("The API tool it is a key of, as tico_tool_index shows it"),
        "level": LEVEL,
        "for_bot": _s("The bot that gets it"),
        "username": _s("The account it belongs to, if any"),
        "expires_on": _s("A tool key's last day, YYYY-MM-DD"),
        "kind": KIND,
    },
    required=("name",),
    writes=True,
    local=True,
)
def credential_add(api, args):
    if any(args.get(k) for k in ("value", "secret")):
        raise ValueError(
            "A value never goes in an argument: name the variable (from_env) or file (from_file) holding it"
        )
    value = secret_from(args)
    if value is None:
        raise ValueError("Say where the value is: from_env (a variable's name) or from_file (a file), never the value")
    if (args.get("level") or args.get("expires_on")) and not args.get("tool"):
        raise ValueError("level and expires_on belong to a tool's key: name the tool")
    me = _own_key(api, args)
    if me:
        env = args.get("env") or args.get("from_env")
        if not env:
            raise ValueError("Say which variable your runs read it under: env")
        if args.get("expires_on"):
            raise ValueError("expires_on is set on a tool's key in Tools; your own key has none")
        body = {
            "env": env,
            "for_bot": me,
            "value": value,
            **_given(args, ("name", "kind", "username")),
            **({"tool": _api_tool(args, "tool")} if args.get("tool") else {}),
            **({"level": args["level"]} if args.get("level") else {}),
        }
        return _hide(api.post("credential-set", body, key=_key(args)))
    person = _as_person(api)
    if args.get("tool"):
        tool_id, level = _api_tool(args, "tool"), args.get("level") or "read_write"
        body = {
            "name": args["name"],
            "value": value,
            "access_level": level,
            **_given(args, ("env", "username", "expires_on")),
        }
        out = {"tool": tool_id, "key": _hide(person.post(f"api-tools/{tool_id}/keys", body, key=_key(args)))}
        if args.get("for_bot"):
            subject = _q("bot:" + str(args["for_bot"]).removeprefix("bot:"))
            out["access"] = person.call(
                "PUT", f"api-tools/{tool_id}/access/{subject}", {"level": level}, _key(args, ":level")
            )
        return out
    if args.get("for_bot"):
        if not args.get("env"):
            raise ValueError("for_bot needs env: the variable the bot reads it under")
        body = {
            "env": args["env"],
            "for_bot": args["for_bot"],
            "value": value,
            **_given(args, ("name", "kind", "username")),
        }
        if _a_bot(api):
            body["on_behalf_of"] = "turn"  # a bot stores only with the rights of a person who asked (the route checks)
        return _hide(api.post("credential-set", body, key=_key(args)))
    body = {"name": args["name"], "secret": value, **_given(args, ("env", "kind", "username"))}
    return _hide(person.post("credentials", body, key=_key(args)))


@tool(
    "tico_credential_update",
    "Change a stored key: its name, username, level or end date (a tool's key), or replace its value from a variable "
    "or file on this computer. Only what you send changes.",
    {
        "credential": _s("The key's name, id or variable"),
        "name": _s("Its new name"),
        "username": _s("The account it belongs to"),
        "level": LEVEL,
        "expires_on": _s("A tool key's last day, YYYY-MM-DD; empty clears it"),
        **_from("the new value"),
    },
    required=("credential",),
    writes=True,
    local=True,
)
def credential_update(api, args):
    person = _as_person(api)
    row = _credential_of(person.get("credentials"), args["credential"])
    value = secret_from(args)
    if row.get("tool_id"):
        body = _given(args, ("name", "username", "expires_on"))
        if args.get("level"):
            body["access_level"] = args["level"]
        if value is not None:
            body["value"] = value
        if not body:
            raise ValueError("Say what changes: name, username, level, expires_on or a new value")
        path = f"api-tools/{_q(row['tool_id'])}/keys/{_q(row['id'])}"
        return _hide(person.patch(path, body, key=_key(args)))
    if args.get("level") or args.get("expires_on") is not None:
        raise ValueError(f"{row.get('name')} is on no tool, so it has no level or end date")
    if not (_given(args, ("name", "username")) or value is not None):
        raise ValueError("Say what changes: name, username or a new value")
    body = {
        "name": args.get("name") or row.get("name"),
        "username": args["username"] if args.get("username") is not None else row.get("username") or "",
        "kind": row.get("kind") or "api_key",
        "env": row.get("env") or "",
        "source": row.get("source") or "",
        "expected_revision": row.get("revision"),
        **({"secret": value} if value is not None else {}),
    }
    return _hide(person.post("credentials/" + _q(row["id"]), body, key=_key(args)))


@tool(
    "tico_credential_cancel",
    "Withdraw a key card nobody has filled in (the person it asks).",
    {"id": _s("The card's id")},
    required=("id",),
    writes=True,
)
def credential_cancel(api, args):
    return _as_person(api).post(f"credential-requests/{_q(args['id'])}/cancel", {}, key=_key(args))


# ----------------------------------------------------------------------------- website accounts (logins)
ACCOUNT = _s("The account's id, from tico_tool_website_show")


@tool(
    "tico_credential_login_add",
    "Add an account to a website tool. Its password and 2FA key are read from this computer (a variable or a file); "
    "leave both out for a site a person signs in to by hand. A shared account is used by nobody until shared; all_bots "
    "shares it with every bot.",
    {
        "tool": WEBSITE_ID,
        "username": _s("The sign-in name"),
        **_from("the password", "password_"),
        **_from("the 2FA (TOTP) key", "totp_"),
        "holder": _s("Whose own account it is (human:<id> or bot:<slug>); a shared account has none"),
        "all_bots": {"type": "boolean", "description": "Every bot uses it (off by default)"},
    },
    required=("tool",),
    writes=True,
    local=True,
)
def credential_login_add(api, args):
    password, totp = secret_from(args, "password_"), secret_from(args, "totp_")
    body = {
        **_given(args, ("username", "holder", "all_bots")),
        **({"password": password} if password is not None else {}),
        **({"totp": totp} if totp is not None else {}),
    }
    return _hide(_as_person(api).post(f"website-tools/{_website(args, 'tool')}/accounts", body, key=_key(args)))


@tool(
    "tico_credential_login_edit",
    "Change a website account: its username, its password or 2FA key (read from this computer), or remove the 2FA key.",
    {
        "tool": WEBSITE_ID,
        "account": ACCOUNT,
        "username": _s("The new sign-in name"),
        **_from("the new password", "password_"),
        **_from("the new 2FA (TOTP) key", "totp_"),
        "totp_off": {"type": "boolean", "description": "Remove the 2FA key"},
    },
    required=("tool", "account"),
    writes=True,
    local=True,
)
def credential_login_edit(api, args):
    password, totp = secret_from(args, "password_"), secret_from(args, "totp_")
    body = _given(args, ("username",))
    if password is not None:
        body["password"] = password
    if totp is not None or args.get("totp_off"):
        body["totp"] = totp
    if not body:
        raise ValueError("Say what changes: username, a new password or 2FA key, or totp_off")
    path = f"website-tools/{_website(args, 'tool')}/accounts/{_q(args['account'])}"
    return _hide(_as_person(api).patch(path, body, key=_key(args)))


@tool(
    "tico_credential_login_delete",
    "Delete a website account.",
    {"tool": WEBSITE_ID, "account": ACCOUNT},
    required=("tool", "account"),
    writes=True,
)
def credential_login_delete(api, args):
    path = f"website-tools/{_website(args, 'tool')}/accounts/{_q(args['account'])}"
    return _as_person(api).call("DELETE", path, key=_key(args))


@tool(
    "tico_credential_login_share",
    "Let a bot, person or group use a shared website account, or stop it (on false).",
    {
        "tool": WEBSITE_ID,
        "account": ACCOUNT,
        "subject": _s("bot:<slug>, human:<id>, group:<id>, or bots for every bot"),
        "on": {"type": "boolean", "description": "Uses it (on) or not (off)"},
    },
    required=("tool", "account", "subject", "on"),
    writes=True,
)
def credential_login_share(api, args):
    subject = str(args["subject"])
    subject = subject if ":" in subject or subject == "bots" else "bot:" + subject
    path = f"website-tools/{_website(args, 'tool')}/accounts/{_q(args['account'])}/access/{_q(subject)}"
    return _as_person(api).call("PUT", path, {"on": bool(args["on"])}, _key(args))


# ----------------------------------------------------------------------------- who is offered them
# People, BotOps (as the person who asked) and bots: the Tools Owner is a bot, and any other bot is refused by the
# route with who can. Never the Assistant (it hands Tools changes to the Tools Owner; its listing is near its cap).
AUDIENCE = {
    name: REQUESTER + ("bot",)
    for name in (
        "tico_tool_detail",
        "tico_tool_vendor",
        "tico_tool_create",
        "tico_tool_edit",
        "tico_tool_delete",
        "tico_tool_setting_add",
        "tico_tool_setting_change",
        "tico_tool_setting_remove",
        "tico_tool_mcp_probe",
        "tico_tool_mcp_add",
        "tico_tool_sign_in",
        "tico_tool_forget",
        "tico_tool_templates",
        "tico_tool_template_set",
        "tico_tool_ungroup",
        "tico_tool_classify",
        "tico_tool_paste",
        "tico_tool_website_show",
        "tico_tool_website_add",
        "tico_tool_website_edit",
        "tico_tool_website_delete",
        "tico_credential_add",
        "tico_credential_update",
        "tico_credential_cancel",
        "tico_credential_login_add",
        "tico_credential_login_edit",
        "tico_credential_login_delete",
        "tico_credential_login_share",
    )
}


# ----------------------------------------------------------------------------- the parity contract
# Every Tools write route, "METHOD /api/v2/<path>" as the app's route table spells it, and the tool that calls it.
ROUTES = {
    "POST /api/v2/api-tools": "tico_tool_create",
    "PATCH /api/v2/api-tools/{tool_id}": "tico_tool_edit",
    "DELETE /api/v2/api-tools/{tool_id}": "tico_tool_delete",
    "POST /api/v2/api-tools/{tool_id}/keys": "tico_credential_add",
    "PATCH /api/v2/api-tools/{tool_id}/keys/{key_id}": "tico_credential_update",
    "DELETE /api/v2/api-tools/{tool_id}/keys/{key_id}": "tico_credential_delete",
    "PUT /api/v2/api-tools/{tool_id}/access/{subject}": "tico_tool_level",
    "POST /api/v2/api-tools/{tool_id}/check": "tico_tool_check",
    "POST /api/v2/api-tools/{tool_id}/settings": "tico_tool_setting_add",
    "PATCH /api/v2/api-tools/{tool_id}/settings/{setting_id}": "tico_tool_setting_change",
    "DELETE /api/v2/api-tools/{tool_id}/settings/{setting_id}": "tico_tool_setting_remove",
    "POST /api/v2/api-tools/{tool_id}/sign-in": "tico_tool_sign_in",
    "PUT /api/v2/api-tools/{tool_id}/template": "tico_tool_template_set",
    "POST /api/v2/api-tools/{tool_id}/ungroup": "tico_tool_ungroup",
    "POST /api/v2/api-tools/{tool_id}/paste": "tico_tool_paste",
    "POST /api/v2/tool-templates/classify": "tico_tool_classify",
    "POST /api/v2/mcp-tools/probe": "tico_tool_mcp_probe",
    "POST /api/v2/mcp-tools": "tico_tool_mcp_add",
    "POST /api/v2/website-tools": "tico_tool_website_add",
    "PATCH /api/v2/website-tools/{tool_id}": "tico_tool_website_edit",
    "DELETE /api/v2/website-tools/{tool_id}": "tico_tool_website_delete",
    "POST /api/v2/website-tools/{tool_id}/accounts": "tico_credential_login_add",
    "PATCH /api/v2/website-tools/{tool_id}/accounts/{account_id}": "tico_credential_login_edit",
    "DELETE /api/v2/website-tools/{tool_id}/accounts/{account_id}": "tico_credential_login_delete",
    "PUT /api/v2/website-tools/{tool_id}/accounts/{account_id}/access/{subject}": "tico_credential_login_share",
    "POST /api/v2/credentials": "tico_credential_add",
    "POST /api/v2/credentials/{cid}": "tico_credential_update",
    "POST /api/v2/credentials/{cid}/grants": "tico_credential_grant",
    "POST /api/v2/credentials/{cid}/grants/{gid}/revoke": "tico_credential_revoke",
    "DELETE /api/v2/credentials/{cid}": "tico_credential_delete",
    "POST /api/v2/credential-requests": "tico_credential_request",
    "POST /api/v2/credential-requests/{rid}/cancel": "tico_credential_cancel",
    "POST /api/v2/credential-set": "tico_credential_add",
    "POST /api/v2/credential-imports": "tico_credential_import",
    "POST /api/v2/tools-chat/proposals": "tico_tools_propose_grant",
    "POST /api/v2/bots/{bot}/tools": "tico_tool_add",
    "POST /api/v2/bots/{bot}/tools/{tool_id}/update": "tico_tool_update",
    "POST /api/v2/bots/{bot}/tools/{tool_id}/delete": "tico_tool_remove",
    "POST /api/v2/tools/{service}/learnings": "tico_tool_learn",
    "POST /api/v2/tools/{service}/learnings/{lid}/delete": "tico_tool_forget",
}

# Tools write routes no tool calls, and why.
UI_ONLY = {
    "POST /api/v2/credentials/{cid}/reveal": "Shows a value: a person reads it in Tools, and a bot never sees one",
    "POST /api/v2/credential-requests/{rid}/save": "The person types the value into the card; it never passes a bot",
    "POST /api/v2/tools-chat/proposals/{message_id}/grant": "Grant on a key card is the person's own tap by design",
    "DELETE /api/v2/bots/{bot}/tools/{tool_id}": "The same removal as POST .../delete, which tico tool remove calls",
    "DELETE /api/v2/tool-proxy/{tool_id}/{path:path}": "The proxy itself: tico tool call sends calls through it",
    "PATCH /api/v2/tool-proxy/{tool_id}/{path:path}": "The proxy itself: tico tool call sends calls through it",
    "POST /api/v2/tool-proxy/{tool_id}/{path:path}": "The proxy itself: tico tool call sends calls through it",
    "PUT /api/v2/tool-proxy/{tool_id}/{path:path}": "The proxy itself: tico tool call sends calls through it",
    "POST /api/v2/website-tools/{tool_id}/accounts/{account_id}/browser": "A person's live view of a sign-in in Tico",
    "POST /api/v2/website-tools/{tool_id}/accounts/{account_id}/browser/control": "A person's live sign-in view",
    "POST /api/v2/website-tools/{tool_id}/accounts/{account_id}/browser/input": "A person's live sign-in view",
}


# ------------------------------------------------------------------ `tico tool ...` and `tico credential ...`
def _on_off(value):
    word = str(value).strip().lower()
    if word not in ("on", "off", "true", "false", "yes", "no"):
        raise ValueError("on or off")
    return word in ("on", "true", "yes")


def _json(value):
    import json

    return json.loads(value)


def _template(value):
    """A template on the command line: the tool's own as JSON, else a known template's id."""
    return _json(value) if str(value).lstrip().startswith("{") else value


_AUDIENCE_ARGS = (
    Arg("audience", type=_json, metavar="JSON", action="store"),
    Arg("audience", "--audience-everyone", action="store_const", const=NULL),
)
_FROM = (Arg("from_env", metavar="VAR"), Arg("from_file", metavar="PATH"))


def _from_args(prefix):
    return (Arg(prefix + "from_env", metavar="VAR"), Arg(prefix + "from_file", metavar="PATH"))


TOOL_CLI = (
    Command(
        "tool detail", "one API or MCP tool: keys, levels, settings; never a value", example="tico tool detail linear"
    ),
    Command("tool vendor", "everything of one vendor you may see", example="tico tool vendor raygun"),
    Command(
        "tool create",
        "add an API tool",
        example="tico tool create Linear --base-url https://api.linear.app --check-path /graphql",
    ),
    Command(
        "tool edit",
        "rename an API tool or change its address, auth, check path, delivery, Everyone can read or who can see it",
        args=(Arg("everyone_reads", type=_on_off, metavar="on|off", action="store"), *_AUDIENCE_ARGS),
        exclusive=(("audience",),),
        example="tico tool edit linear --delivery proxy --everyone-reads off",
    ),
    Command("tool delete", "delete an API tool, its keys and settings", example="tico tool delete linear"),
    Group("tool setting", "a tool's non-secret settings (host, region, project id)"),
    Command(
        "tool setting add", "add a setting", example="tico tool setting add posthog POSTHOG_HOST https://eu.posthog.com"
    ),
    Command(
        "tool setting change",
        "rename a setting or change its value",
        example="tico tool setting change posthog POSTHOG_HOST --value https://us.posthog.com",
    ),
    Command("tool setting remove", "take a setting off", example="tico tool setting remove posthog POSTHOG_HOST"),
    Group("tool mcp", "a vendor's remote MCP server as a tool"),
    Command(
        "tool mcp probe",
        "how an MCP server signs in",
        tool="tico_tool_mcp_probe",
        example="tico tool mcp probe https://mcp.linear.app/mcp",
    ),
    Command(
        "tool mcp add",
        "add an MCP server: a token read from this computer, or OAuth",
        tool="tico_tool_mcp_add",
        args=_from_args("token_") + _from_args("client_secret_"),
        exclusive=(("token_from_env", "token_from_file"), ("client_secret_from_env", "client_secret_from_file")),
        example="tico tool mcp add Linear https://mcp.linear.app/mcp oauth",
    ),
    Command(
        "tool sign-in",
        "start an MCP tool's OAuth sign-in: the address a person opens",
        example="tico tool sign-in linear",
    ),
    Command("tool forget", "delete a learning from a tool (the owner)", example="tico tool forget posthog l1"),
    Command(
        "tool propose-grant",
        "the Tools Owner: propose where keys dropped in its chat go; the person grants on the card",
        tool="tico_tools_propose_grant",
        args=(Arg("rows", "--rows", type=_json, metavar="JSON", action="store"),),
        example='tico tool propose-grant t1 --rows \'[{"key": "LINEAR_API_KEY", "tool": "linear", '
        '"level": "read", "bots": ["ops"]}]\'',
    ),
    Command("tool templates", "the known auth templates", example="tico tool templates"),
    Command(
        "tool template",
        "set a tool's auth template: a known one's id, the tool's own JSON, or --none",
        tool="tico_tool_template_set",
        args=(
            Arg("template", positional=True, nargs="?", type=_template, metavar="ID|JSON", default=argparse.SUPPRESS),
            Arg("template", "--none", action="store_const", const=NULL),
        ),
        one_of=(("template",),),
        example="tico tool template twilio twilio",
    ),
    Command("tool ungroup", "split a grouped tool back into its parts", example="tico tool ungroup twilio"),
    Command(
        "tool classify",
        "what a vendor account's values are, read from a variable or a file; nothing is stored",
        args=_FROM,
        one_of=(("from_env", "from_file"),),
        example="tico tool classify --from-file ./twilio.env",
    ),
    Command(
        "tool paste",
        "store a vendor account's values on a tool, read from a variable or a file",
        args=_FROM,
        one_of=(("from_env", "from_file"),),
        example="tico tool paste twilio --from-file ./twilio.env",
    ),
    Group("tool website", "website tools: the sites bots sign in to"),
    Command("tool website show", "one website tool and its accounts", example="tico tool website show slack"),
    Command(
        "tool website add",
        "add a website tool",
        args=(Arg("sites", "--site", required=True, metavar="SITE"),),
        example="tico tool website add --site app.example.com --name Example",
    ),
    Command(
        "tool website edit",
        "change a website tool's name, sites, central only or who can see it",
        args=(
            Arg("sites", "--site", metavar="SITE"),
            Arg("central_only", type=_on_off, metavar="on|off", action="store"),
            *_AUDIENCE_ARGS,
        ),
        exclusive=(("audience",),),
        example="tico tool website edit slack --central-only on",
    ),
    Command("tool website delete", "delete a website tool and its accounts", example="tico tool website delete slack"),
)

CREDENTIAL_CLI = (
    Command(
        "credential add",
        "store a key you hold, read from a variable or a file on this computer; never type the value",
        args=_FROM,
        one_of=(("from_env", "from_file"),),
        example="tico credential add 'Linear (Ops)' --env LINEAR_API_KEY --from-env NEW_KEY --tool linear --level read",
    ),
    Command(
        "credential update",
        "rename a key, change its level or end date, or replace its value from a variable or file",
        args=_FROM,
        exclusive=(("from_env", "from_file"),),
        example="tico credential update LINEAR_API_KEY --from-file ./new-key",
    ),
    Command("credential cancel", "withdraw a key card nobody filled in", example="tico credential cancel c1"),
    Group("credential login", "a website tool's accounts; passwords are read from this computer"),
    Command(
        "credential login add",
        "add an account to a website tool",
        args=(
            *_from_args("password_"),
            *_from_args("totp_"),
            Arg("all_bots", type=_on_off, metavar="on|off", action="store"),
        ),
        exclusive=(("password_from_env", "password_from_file"), ("totp_from_env", "totp_from_file")),
        example="tico credential login add slack --username ops@acme.example --password-from-env SLACK_PASSWORD",
    ),
    Command(
        "credential login edit",
        "change an account's username, password or 2FA key",
        args=(*_from_args("password_"), *_from_args("totp_")),
        exclusive=(("password_from_env", "password_from_file"), ("totp_from_env", "totp_from_file", "totp_off")),
        example="tico credential login edit slack a1 --password-from-file ./password",
    ),
    Command("credential login delete", "delete a website account", example="tico credential login delete slack a1"),
    Command(
        "credential login share",
        "let a bot, person or group use a shared account, or stop",
        args=(Arg("on", positional=True, choices=("on", "off"), action="store"),),
        convert=lambda fields: {**fields, "on": _on_off(fields["on"])},
        example="tico credential login share slack a1 bot:ops on",
    ),
)
