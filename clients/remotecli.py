"""HTTP implementation of the `hub` commands (`clients/hubcli.py` parses them); never opens a database."""

import base64
import csv
import io
import json
import os
import re
import shlex
import sys
import time
import uuid
from pathlib import Path

if __package__ in (None, ""):  # imported by a script run from anywhere
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import s3links
from clients.tico import APIError, Client


def tool_name(fn):
    """The MCP tool a command runs: `message mark-read` is `tico_message_mark_read`."""
    return "tico_" + fn.replace(" ", "_").replace("-", "_")


def visuals_from_args(args):
    """`--visual` values with their `--title`s, in order: a file in the checkout, or ID@VERSION already in Tico."""
    paths, titles = list(getattr(args, "visual", None) or []), list(getattr(args, "visual_title", None) or [])
    if len(titles) > len(paths):
        raise APIError("validation", "Each --title goes with a --visual", 422)
    out = []
    for index, value in enumerate(paths):
        title = titles[index] if index < len(titles) else ""
        known = re.fullmatch(r"[^/@\s]+@[1-9][0-9]*", value) and not os.path.exists(value)
        out.append({"file" if known else "path": value, "title": title})
    return out


def _delivery(args):
    """The request's delivery fields from the command's --steer, --queue or --defer (clients/hubcli.py)."""
    from clients.hubcli import delivery_of

    return delivery_of(args)


def _rules_args(args):
    """`tico rules` flags as the API takes them: the Who and With flags become scope objects, the rest pass as they are."""

    def scope(names, everyone=False, all_bots=False, outside_editions=False):
        out = {}
        if names:
            out["names"] = list(names)
        if everyone:
            out["everyone"] = True
        if all_bots:
            out["all_bots"] = True
        if outside_editions:
            out["outside_editions"] = True
        return out or None

    if args.fn in ("rules add", "rules edit", "rules accept"):
        who = scope(
            getattr(args, "who_names", None),
            getattr(args, "everyone", False),
            getattr(args, "all_bots", False),
            getattr(args, "outside_editions", False),
        )
        if who is not None:
            args.who = who
    if args.fn in ("rules add", "rules edit"):
        with_scope = scope(getattr(args, "with_names", None))
        if with_scope is not None:
            setattr(args, "with", with_scope)
    if args.fn == "rules read" and not getattr(args, "source", None):
        args.fn = "rules get"  # one rule by id; with --from it is a document read (tico_rules_read)
    if args.fn == "rules add" and getattr(args, "file_version", None) and not getattr(args, "quote", None):
        raise SystemExit("tico rules add: --version goes with --file and --quote")
    if args.fn == "rules add" and getattr(args, "quote", None):
        if not getattr(args, "file_id", None):
            raise SystemExit("tico rules add: a quote needs --file (the document it is in)")
        args.cite = {"file_id": args.file_id, "section": getattr(args, "section", None) or "", "quote": args.quote}
        if getattr(args, "file_version", None):
            args.cite["version"] = args.file_version
    for flag in (
        "who_names",
        "with_names",
        "everyone",
        "all_bots",
        "outside_editions",
        "file_id",
        "section",
        "quote",
        "file_version",
    ):
        if hasattr(args, flag):
            delattr(args, flag)
    return args


def _contacts_args(args):
    """`tico contacts` flags as the tools take them: a KEY=VALUE match is an object, --set pairs are the values object, and
    --option pairs are an attribute's options. The pairs are the command line's; the tool takes objects."""
    if getattr(args, "match", None):
        args.match = {args.match[0]: args.match[1]}
    if getattr(args, "set_values", None) is not None:
        args.values = dict(args.set_values)
    args.set_values = None
    if getattr(args, "options", None) is not None:
        args.options = [{"key": key, "label": label} for key, label in args.options]
    return args


def unwrap(result):
    """A write's task as the CLI prints it: the task alone when the answer is only `{"task": ...}`."""
    return result["task"] if isinstance(result, dict) and set(result) == {"task"} else result


def via_tool(client, args, **more):
    """Run the tool of the command's own name with the command's flags as its arguments."""
    from clients import hubtools

    fields = {
        k: v
        for k, v in vars(args).items()
        if k not in ("cmd", "sub", "subsub", "fn", "visual", "visual_title", "as_text") and v is not None
    }
    if getattr(args, "visual", None):
        fields["visuals"] = visuals_from_args(args)
    if args.fn in ("bot turns-at-once", "computer turns-at-once") and hasattr(args, "limit"):
        fields["limit"] = args.limit  # explicit null restores the default; omission only reads
    if args.fn == "bot browser-exit" and hasattr(args, "computer"):
        fields["computer"] = args.computer  # explicit null is the bot's own internet; omission only reads
    if args.fn == "computer browser-exit" and "sharing" in fields:
        fields["sharing"] = fields["sharing"] == "on"
    if args.fn == "thread update" and fields.pop("keep_forever", False):
        fields["retention_days"] = None  # explicit null keeps its messages for good; omission leaves the policy alone
    fields["operation_id"] = fields.get("operation_id") or os.environ.get("HUB_OPERATION_ID")
    fields.update(more)
    return hubtools.BY_NAME[tool_name(args.fn)]["fn"](client, fields)


def automation(client, args):
    """`tico automation ...`: the tico_automation_* tools, with flows read from a file or stdin."""
    from clients import hubtools

    def flow(path):
        try:
            data = json.loads(sys.stdin.read() if path == "-" else Path(path).read_text())
        except ValueError as exc:
            raise APIError("file", f"--file is not JSON ({exc})") from None
        if not isinstance(data, dict):
            raise APIError("file", "--file must hold a JSON object")
        return data

    run = lambda name, fields: hubtools.BY_NAME[name]["fn"](
        client, {**fields, "operation_id": os.environ.get("HUB_OPERATION_ID")}
    )
    if args.sub == "inbox":
        return run("tico_inbox_send", {"inbox": args.inbox, **({"send": args.send == "on"} if args.send else {})})
    fields = {"id": args.id} if hasattr(args, "id") else {}
    if args.sub == "list":
        fields.update({k: getattr(args, k) for k in ("owner", "limit", "cursor") if getattr(args, k) is not None})
    elif args.sub == "create":
        fields.update(flow(args.file))
    elif args.sub == "update":
        fields.update(flow(args.file) if args.file else {})
        fields.update({k: getattr(args, k) for k in ("name", "summary") if getattr(args, k) is not None})
        if args.enable or args.disable:
            fields["enabled"] = bool(args.enable)
    elif args.sub == "run":
        try:
            fields["input"] = json.loads(args.input) if args.input else {}
        except ValueError:
            raise APIError("input", "--input must be JSON") from None
    elif args.sub == "runs":
        fields.update({k: getattr(args, k) for k in ("limit", "before") if getattr(args, k) is not None})
    elif args.sub in ("recent-runs", "stats"):
        fields.update(
            {
                k: getattr(args, k)
                for k in ("range", "status", "trigger", "owner", "limit", "before")
                if getattr(args, k, None) is not None
            }
        )
    elif args.sub == "versions" and args.pending:
        fields["status"] = "pending"
    elif args.sub in ("restore", "discard"):
        fields["n"] = args.n
    elif args.sub == "held":
        fields["action"] = args.action
    elif args.sub == "template":
        fields.update({"owner": args.owner, "inbox": args.inbox})
    return run(tool_name(args.fn), fields)


def product_repo_create(client, name, *, confirm=None, stdin=None, stderr=None):
    """Preview the exact target and require the exact org/name before writing: typed at a terminal, or given as
    `confirm` (`--confirm`, for a bot's turn, which has no terminal) and checked against the preview."""
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    request_id = os.environ.get("HUB_OPERATION_ID") or uuid.uuid4().hex
    key = request_id + ":product-repository"
    if len(key) > 200:
        raise APIError(
            "operation_id",
            "HUB_OPERATION_ID is too long for a product-repository idempotency key; no repository was created",
            422,
        )
    preview = client.get("github/product-repos/preview", name=name)
    print(f"Product repository preview: {preview['repository']} · private · empty (no initial commit)", file=stderr)
    print(f"GitHub App capability: {preview['capability']}. {preview['capability_detail']}", file=stderr)
    if preview["capability"] != "available":
        raise APIError("github_capability", preview["capability_detail"], 409)
    if confirm is not None:
        if confirm != preview["repository"]:
            raise APIError(
                "confirmation_required",
                f"--confirm must be the exact preview target {preview['repository']}; no repository was created",
                409,
            )
    elif not stdin.isatty():
        raise APIError(
            "confirmation_required",
            f"Confirm the exact target: run it again with --confirm {preview['repository']}, or type it at an interactive terminal; no repository was created",
        )
    else:
        print(f"Type {preview['repository']} to create it: ", end="", file=stderr, flush=True)
        if stdin.readline().strip() != preview["repository"]:
            raise APIError("canceled", "Confirmation did not match; no repository was created")
    return client.post(
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


def chosen_grant(value):
    """`owner/repo`, `owner/repo:read`, `owner/repo:write`, or a write grant with options: `+actions` (may run
    workflows) and `+edit` (may edit workflow files), as in `owner/repo:write+actions+edit`. Left out, an option keeps
    whatever setting the grant has; `-actions` or `-edit` turns it off."""
    name, _, level = value.rpartition(":") if ":" in value else (value, "", "write")
    access, *options = re.split(r"(?=[+-])", level)
    flags = {}
    for option in options:
        flag = {"actions": "run_workflows", "edit": "edit_workflows"}.get(option[1:])
        if not flag or flag in flags:
            access = ""
            break
        flags[flag] = option[0] == "+"
    if access not in ("read", "write") or access == "read" and flags:
        raise APIError(
            "validation",
            f"'{value}': use owner/repo, :read, :write, or :write with +actions, -actions, "
            "+edit or -edit (e.g. :write+actions+edit)",
        )
    return {"full_name": name, "access": access, **flags}


MARKET_SPEND = (
    "market define",
    "market segment add",
    "market segment tree",
    "market size add",
    "market size decide",
    "market size list",
    "market figure add",
    "market sources",
    "market spend",
    "market research",
)


def market_spend(client, args, tool):
    """The spend commands run the MCP tool of the same name; `sources --add-file` reads its YAML here."""
    fn = args.fn
    if fn == "market sources" and args.add_file:
        import yaml

        loaded = yaml.safe_load(Path(args.add_file).read_text())
        args.add = loaded if isinstance(loaded, list) else [loaded]
    return tool(client, args)


def counsel_held(args):
    """Counsel runs a shell, so its CLI is held to the tools it is offered as its MCP is (hubtools.COUNSEL_TOOLS): a web
    search or fetch, a message, and every other command outside that list are refused before anything is sent."""
    from clients import hubtools

    if (os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE") or "").strip() != hubtools.COUNSEL_BOT:
        return
    fn = getattr(args, "fn", None)
    entry = hubtools.BY_NAME.get(tool_name(fn)) if fn else None
    if entry and hubtools.COUNSEL not in hubtools.offered_to(entry):
        raise SystemExit(f"{fn} is not available to Counsel: it has no credentials and sends nothing")


# The aliases hubcli.parser() declares. Argparse keeps the spelling typed (`tico tasks list` sets cmd to "tasks"), and the
# dispatch below tests the canonical names, so each alias is renamed once, here, before anything is dispatched on it.
COMMAND_ALIASES = {
    "tasks": "task",
    "automations": "automation",
    "bots": "bot",
    "rule": "rules",
}


def canonical_names(args):
    """Rename an aliased command on a parsed line to the name it stands for. `fn` is already the canonical name, set by
    each leaf of the parser."""
    args.cmd = COMMAND_ALIASES.get(args.cmd, args.cmd)
    return args


def run(args, who=None):
    if who:
        raise APIError("identity", "Remote identity comes from authentication; --human is unavailable")
    canonical_names(args)
    counsel_held(args)
    # A query may run for 20 s on the server before it is stopped, a decision's providers 25 s; leave room for that.
    client = Client(
        os.environ["HUB_API_URL"],
        os.environ.get("HUB_TOKEN", ""),
        timeout=45
        if args.cmd in ("decision", "gateway")
        else 30
        if args.cmd == "sql"
        else 120
        if args.cmd == "listening" or args.fn == "market import"
        else 130
        if args.cmd == "inbound"
        else 15,
    )
    fn = args.fn
    from clients.hubtools_org import COMMANDS as ORG_COMMANDS

    if fn in ORG_COMMANDS:  # people, bots, the org chart, usage, billing and reach (clients/hubtools_org.py)
        return via_tool(client, args)

    from clients import hubtools_computers, hubtools_connections

    if fn in hubtools_connections.COMMANDS or fn in hubtools_computers.COMMANDS:
        return hubtools_connections.run_command(client, args)
    if args.cmd == "inbound":
        return via_tool(client, args)
    if args.cmd == "rules":
        return via_tool(client, _rules_args(args))
    if args.cmd in ("mail", "email", "letter", "alerts") or fn in ("tool index", "tool check"):
        return via_tool(client, args)
    if args.cmd == "maintenance":
        return via_tool(client, args)
    if args.cmd == "thread":
        return via_tool(client, args)
    if args.cmd == "contacts":
        return via_tool(client, _contacts_args(args))
    if args.cmd == "finance":
        return via_tool(
            client, args, **({"operation_id": args.operation_id} if getattr(args, "operation_id", None) else {})
        )
    if args.cmd == "settings":
        return via_tool(client, args)
    if fn in ("token list", "token create", "token revoke"):  # clients/cli_plumbing.py
        return via_tool(client, args)
    if args.cmd == "feedback":
        return via_tool(client, args)
    if args.cmd == "call":
        return via_tool(client, args)
    if args.cmd in ("card", "pin"):
        return via_tool(client, args)
    if fn.startswith(("trigger relay ", "relay ", "sleep ")):
        return via_tool(client, args)
    # The other webhook leaves of docs/webhooks.md: each runs the tool of its own name, as on the server's own shell.
    if fn.startswith(("trigger ", "notice ")) or fn == "event-types":
        return via_tool(client, args)
    if fn == "team icon":
        with Path(args.file).open("rb") as source:
            return client.post_bytes("team/icon", source)
    if fn in ("task worktree add", "task worktree attach", "task worktree setup"):
        from runner_support.worktrees import command

        return command(client, args.worktree_sub, getattr(args, 'repo', None) or getattr(args, 'path', None), args.task)
    if args.cmd == "repo":
        from clients import hubtools

        if args.fn == "repo review":
            body = Path(args.body_file).read_text() if args.body_file else args.body
            return hubtools.repo_review(
                client,
                {
                    "repository": args.repository,
                    "number": args.number,
                    "head": args.head,
                    "verdict": args.verdict,
                    "body": body,
                    **({"operation_id": args.operation_id} if args.operation_id else {}),
                },
            )
        if args.fn == "repo product-create":
            return product_repo_create(client, args.name, confirm=args.confirm)
        if args.sub == "list":
            return hubtools.repo_list(client, {})
        return hubtools.repo_update(client, {"full_name": args.full_name, "enabled": args.sub == "tick"})
    if fn == "bot repos":
        from clients import hubtools

        if not args.all and not args.own and args.chosen is None:
            return hubtools.bot_repos_get(client, {"bot": args.bot})
        body = {"bot": args.bot, "mode": "all" if args.all else "own" if args.own else "chosen"}
        if args.chosen is not None:
            body["chosen"] = [chosen_grant(r) for r in args.chosen]
        result = hubtools.bot_repos_set(client, body)
        # Say what the change took away: a switch to own keeps the chosen rows, but they stop applying.
        lost = [r["full_name"] + " (" + r["access"] + ")" for r in (result or {}).get("stopped") or []]
        lost += [name + " (run workflows)" for name in (result or {}).get("workflows_stopped") or []]
        lost += [name + " (edit workflow files)" for name in (result or {}).get("workflow_edits_stopped") or []]
        lost += ["repository creation"] if (result or {}).get("creation_stopped") else []
        lost += ["product repository creation"] if (result or {}).get("product_creation_stopped") else []
        if lost:
            print(f"{args.bot} no longer has: " + ", ".join(lost), file=sys.stderr)
        return result
    if fn in ("tool list", "tool show", "tool query-search") and not (fn == "tool list" and args.bot):
        # Reads a runner credential may make outside a turn; /me would refuse a runner.
        return integrations(client, args)
    if fn in ("bot copy", "bot update-from-original", "bot suggest-to-original", "skill copy", "skill submit"):
        return via_tool(client, args)  # they work in this computer's workspace (clients/botcopy.py)
    if fn == "tool report":
        return via_tool(client, args, tools=json.loads(args.tools))
    if args.cmd == "tag":
        more = {}
        if getattr(args, "markdown_file", None):
            more["markdown"] = Path(args.markdown_file).read_text()
        if getattr(args, "metadata", None) is not None:
            more["metadata"] = json.loads(args.metadata)
            if not isinstance(more["metadata"], dict):
                raise APIError("kind", "Tag metadata must be a JSON object")
        fields = vars(args).copy()
        fields.pop("markdown_file", None)
        from argparse import Namespace

        return via_tool(client, Namespace(**fields), **more)
    if args.cmd == "template" or (args.cmd == "bot" and args.sub not in ("status", "recent", "repo-create")):
        return bots(client, args)
    if fn in (
        "bot repo-create",
        "human add",
        "human list",
        "human access",
        "group list",
        "group update",
        "group delete",
        "group access",
        "tool list",
        "tool learn",
        "tool add",
        "tool update",
        "tool remove",
        "update create",
        "update redo",
        "update list",
        "update show",
        "update mark-read",
        "update reply",
        "update settings",
        "changelog list",
        "changelog mark-read",
        "needs-you start",
        "needs-you next",
        "needs-you respond",
        "needs-you commit",
        "needs-you abandon",
        "needs-you show",
        "origin show",
        "brief",
        "mcp stats",
        "calendar list",
        "calendar status",
        "team show",
        "team budget",
        "org order",
        "org reports-to",
        "org models",
        "run list",
        "message list",
        "message mark-read",
        "bot recent",
        "agent pair show",
        "agent pair approve",
        "agent pair decline",
        "slack channel list",
        "slack channel add",
        "slack channel remove",
        "slack channel import",
        "seat list",
        "seat create",
        "seat update",
        "seat close",
        "setup show",
        "setup complete",
        "setup invite",
    ):
        return via_tool(client, args)
    if fn == "run export":
        from clients import hubtools

        if not args.fixture:
            return hubtools.BY_NAME["tico_run_export"]["fn"](client, {"run": args.run})
        found = hubtools.run_export_full(client, args.run)
        return {"written": hubtools.write_fixture(found, Path(args.out or "."))}
    if fn in ("task child create", "task tree", "task model", "task meetings", "task context"):
        return via_tool(client, args)
    if fn in (
        "task requesters",
        "task requester-save",
        "task requester-remove",
        "task comment-moderate",
        "roadmap tasks",
    ):
        return via_tool(client, args)
    if fn == "roadmap settings":
        # Any flag is a partial update of those settings; none reads them. An empty value clears a list.
        names = ("labels", "cors_origins", "action_limit", "suggestion_limit", "bot_public_approval")
        changes = {k: getattr(args, k) for k in names if getattr(args, k) is not None}
        for k in ("labels", "cors_origins"):
            if k in changes:
                changes[k] = [v for v in changes[k] if v]
        if args.suggestions_bot is not None or args.automatic:
            changes["suggestions_bot"] = args.suggestions_bot
        from clients import hubtools

        return hubtools.roadmap_settings(
            client, {"settings": changes or None, "operation_id": os.environ.get("HUB_OPERATION_ID")}
        )
    if args.cmd in ("chat", "replay"):
        return via_tool(client, args)
    if fn == "audit":
        return via_tool(client, args)
    if fn and fn.startswith("task-manager "):
        from clients import hubtools

        op = os.environ.get("HUB_OPERATION_ID")
        if fn == "task-manager status":
            return hubtools.task_manager_status(client, {})
        if fn == "task-manager run":
            return hubtools.task_manager_run(client, {"operation_id": op})
        change = {
            "task-manager on": {"on": True},
            "task-manager off": {"on": False},
            "task-manager schedule": {"preset": getattr(args, "preset", None), "at": getattr(args, "at", None)},
            "task-manager bot": {"bot": getattr(args, "state", None)},
        }[fn]
        return hubtools.task_manager_set(client, {**change, "operation_id": op})
    if fn in ("support report", "support send"):
        from clients import hubtools

        key = os.environ.get("HUB_OPERATION_ID")
        if fn == "support send":
            return hubtools.support_send(client, {"ok": args.ok, "operation_id": key})
        text = Path(args.file).read_text(encoding="utf-8") if args.file else args.message
        report = {"message": text, "include_ids": not args.no_ids, "task": args.task, "operation_id": key}
        return hubtools.support_report(client, report)
    if args.cmd in ("api", "computer", "credential", "support", "health") or fn == "message redact":
        return botops_tools(client, args)
    if args.cmd == "classify":
        from clients import hubtools

        text = Path(args.file).read_text(errors="replace") if args.file else sys.stdin.read()
        return hubtools.BY_NAME["tico_classify"]["fn"](client, {"text": text})
    if fn in (
        "decision readout",
        "decision outcome",
        "gateway catalog",
        "gateway search",
        "gateway fetch",
        "gateway image",
        "gateway video",
        "gateway video-status",
    ):
        return via_tool(client, args)
    if fn == "gateway digest":
        items = json.loads(Path(args.items_file).read_text(encoding="utf-8"))
        return via_tool(client, args, items=items)
    if args.cmd == "decision":
        return judge(client, args)
    if args.cmd == "meeting":
        from clients import hubtools

        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "fn") and v is not None}
        fields.pop("granola_action", None)
        if fn == "meeting import":
            return hubtools.meetings_import_file(client, fields)
        return hubtools.BY_NAME[tool_name(fn)]["fn"](client, fields)
    if fn == "doc ask-status":
        from clients import docs_ask

        return docs_ask.status(
            client, args.conversation_id, args.message_id, args.wait, key=os.environ.get("HUB_OPERATION_ID")
        )
    if fn == "doc ask":  # `doc fetch` never gets here: it runs locally (clients/hubcli.py)
        from clients import docs_ask

        return docs_ask.ask(client, args.question, args.wait, key=os.environ.get("HUB_OPERATION_ID"))
    if args.cmd == "grokbot":
        from clients import hubtools

        body = json.loads(Path(args.file).read_text())
        return hubtools.BY_NAME["tico_grokbot_sync"]["fn"](client, body)
    if args.cmd == "external":
        from clients import hubtools

        body = json.loads(Path(args.file).read_text())
        return hubtools.BY_NAME["tico_external_sync"]["fn"](client, {**body, "provider": args.provider})
    if args.cmd == "file":
        from clients import hubtools

        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "fn") and v is not None}
        fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
        if fn == "file publish":
            return hubtools.files_publish_path(client, fields)
        return hubtools.BY_NAME[tool_name(fn)]["fn"](client, fields)
    if args.cmd == "page":
        from clients import hubtools

        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "fn") and v is not None}
        fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
        if fn == "page publish":
            return hubtools.pages_publish_path(client, fields)
        return hubtools.BY_NAME[tool_name(fn)]["fn"](client, fields)
    if args.cmd == "doc":
        from clients import hubtools

        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "fn", "body_file") and v is not None}
        fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
        if fn == "doc write":
            fields["body"] = (
                sys.stdin.read()
                if not args.body_file or args.body_file == "-"
                else Path(args.body_file).read_text(encoding="utf-8-sig")
            )
        return hubtools.BY_NAME[tool_name(fn)]["fn"](client, fields)
    if args.cmd == "assistant":
        from clients import hubtools

        if args.sub in ("read", "send", "learn", "learned", "withdraw", "proposals", "change", "morning"):
            return via_tool(client, args)
        try:
            body = json.loads(args.body or "{}")
        except ValueError:
            raise APIError("body", "--body must be JSON") from None
        return hubtools.BY_NAME["tico_assistant_propose"]["fn"](
            client,
            {
                "summary": args.summary,
                "path": args.path,
                "method": args.method,
                "body": body,
                "for_person": getattr(args, "for_person", None),
                "operation_id": os.environ.get("HUB_OPERATION_ID"),
            },
        )
    if args.cmd == "db":
        # Runs here, beside the credential; the hub only checks who is asking and keeps the audit.
        from clients import dbquery

        return dbquery.run(client, args)
    if args.cmd == "service-key":
        # A person's own shell (hubtools.SHELL_ONLY): a new key is shown to them, never to an agent's context.
        if fn == "service-key create":
            return client.post(
                "service-keys",
                {"label": args.label, "scope": getattr(args, "scope", "tasks")},
                key=os.environ.get("HUB_OPERATION_ID"),
            )
        if fn == "service-key revoke":
            return client.post("service-keys/" + args.id + "/revoke", {}, key=os.environ.get("HUB_OPERATION_ID"))
        return client.get("service-keys")["keys"]
    if args.cmd == "ok":
        return unwrap(via_tool(client, args))
    if args.cmd == "webhook":
        from clients import hubtools

        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "fn") and v is not None}
        fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
        if fn in ("webhook create", "webhook update"):
            try:
                hubtools.webhook_options(fields)
            except ValueError as exc:
                raise APIError("header", str(exc), 422) from None
        if fn == "webhook send":
            raw = fields.pop("data", None) or "{}"
            try:
                fields["data"] = json.loads(Path(raw[1:]).read_text() if raw.startswith("@") else raw)
            except (OSError, ValueError):
                raise APIError("data", "--data must be JSON, or @file holding JSON") from None
        return hubtools.BY_NAME[tool_name(fn)]["fn"](client, fields)
    identity = client.get("me")
    actor = identity["actor"]
    key = os.environ.get("HUB_OPERATION_ID")

    def post(path, body, suffix="", delegate=False):
        # `delegate`: the person's message the Assistant cites for a task action they asked for (`--on-behalf-of`).
        result = (
            client.call("POST", path, body, (key + suffix) if key else None, delegate=delegate)
            if delegate
            else client.post(path, body, key=(key + suffix) if key else None)
        )
        return result["task"] if isinstance(result, dict) and set(result) == {"task"} else result

    def target(value):
        return actor if value in ("me", "self") else value

    def tool(client, args):
        """The goal, KPI and proposal commands run the MCP tool of the same name, so both say the same thing."""
        from clients import hubtools

        fields = {
            k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "subsub", "fn", "json") and v is not None
        }
        fields["operation_id"] = key
        if args.fn == "brand check":
            file = fields.pop("file")
            fields["text"] = sys.stdin.read() if file == "-" else Path(file).read_text()
        if args.fn == "kpi log":
            if fields.pop("estimate", False) and "quality" not in fields:
                fields["quality"] = "estimate"
        if args.fn == "proposal create":
            file = fields.pop("payload_file", None)
            text = Path(file).read_text() if file else fields.pop("payload", None)
            fields.pop("payload", None)
            try:
                fields["payload"] = json.loads(text) if text else {}
            except ValueError:
                raise APIError("payload", "--payload must be JSON") from None
        return hubtools.BY_NAME[tool_name(args.fn)]["fn"](client, fields)

    def refs(values):
        out = {}
        for value in values or []:
            kind, _, ident = value.partition(":")
            out.setdefault(kind, []).append(ident)
        return out

    cmd, sub = args.cmd, getattr(args, "sub", None)
    if cmd == "whoami":
        from clients import cli_plumbing

        return cli_plumbing.identity(client, identity)  # plus the company, its address and the profile in use
    if fn == "message send":
        from clients import hubtools

        return hubtools.BY_NAME["tico_message_send"]["fn"](
            client,
            {
                "to": args.to,
                "text": args.text,
                "fyi": args.fyi,
                "conversation_id": args.conversation,
                "refs": args.ref or [],
                "command": getattr(args, "command", False),
                "steer": getattr(args, "steer", False),
                "needs_action": getattr(args, "needs_action", False),
                "operation_id": key,
                **_delivery(args),
                **({"visuals": visuals_from_args(args)} if getattr(args, "visual", None) else {}),
            },
        )
    if fn == "note create":
        text = Path(args.text_file).read_text() if args.text_file else args.text
        payload = {
            "to": target(args.to),
            "text": text,
            **({"defer_for": args.defer_for} if getattr(args, "defer_for", None) else {}),
        }
        links = s3links.apply(client, payload, ("text",))
        return s3links.annotate(post("notes", payload)["note"], links)
    if fn == "note list":
        since = args.since
        m = re.fullmatch(r"(\d+)([hd])", since or "")
        if m:
            hours = int(m.group(1)) * (24 if m.group(2) == "d" else 1)
            since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))
        return client.get(
            "notes",
            to=target(args.to) if args.to else None,
            sender=target(args.sender) if args.sender else None,
            since=since,
            waiting="true" if args.waiting else None,
            limit=args.limit,
        )
    if fn == "note delete":
        return post(f"notes/{args.id}/cancel", {})["note"]
    if cmd == "task":
        asked = getattr(args, "on_behalf_of", None) or False
        if sub == "create":
            from clients import hubtools

            fields = {
                "owner": args.owner,
                "title": args.title,
                "body": Path(args.body_file).read_text() if args.body_file else args.body,
                "due": args.due,
                "goal_id": getattr(args, "goal", None) or None,
                "labels": [x.strip() for one in args.label or [] for x in one.split(",") if x.strip()] or None,
                "links": list(args.link) if args.link else None,
                "readers": list(args.readers) if getattr(args, "readers", None) else None,
                "operation_id": key,
            }
            for field in (
                "parent",
                "pipeline",
                "private",
                "follow",
                "from_private",
                "top",
                "request_id",
                "number",
                "model",
                "effort",
                "harness",
                "visibility",
                "public_title",
                "public_summary",
            ):
                fields[field] = getattr(args, field, None)
            return unwrap(hubtools.task_create(client, {k: v for k, v in fields.items() if v is not None}))
        if sub == "show":
            from clients import hubtools

            ids = args.id if isinstance(args.id, list) else [args.id]
            if len(ids) > 1:  # one read for several (tico_task_show_many), not one GET each
                return hubtools.BY_NAME["tico_task_show_many"]["fn"](client, {"ids": ids})
            return hubtools.task_show(client, {"id": ids[0]})
        if sub == "run":
            return post(f"tasks/{args.id}/run-now", {})
        if sub in ("list", "readers", "followers", "follow", "unfollow"):
            return via_tool(client, args)
        if sub in (
            "done",
            "move",
            "close",
            "reassign",
            "ask",
            "answer",
            "thumbs",
            "file",
            "snooze",
            "unsnooze",
            "update",
            "queue",
            "unlink",
        ):
            # The engine's commands (docs/pipelines-v3.md S16): each sends the version the bot passed, never one read now.
            return unwrap(via_tool(client, args))
        if sub == "give":  # `--to <person>`, or the person as a third word
            args.to = args.to or args.to_person
            del args.to_person
            return unwrap(via_tool(client, args))
        if sub == "label":
            split = lambda values: [x.strip() for one in values or [] for x in one.split(",") if x.strip()]
            args.add, args.remove = split(args.add), split(args.remove)
            return unwrap(via_tool(client, args))
        if sub == "comment":
            from clients.task_review import attachment

            references = list(getattr(args, "file", None) or [])
            for index, path in enumerate(getattr(args, "attach", None) or []):
                made = post(f"tasks/{args.id}/files", attachment(path), suffix=f":attachment:{index}")
                references.append(f"{made['file_id']}@{made['version']}")
            local = []
            if getattr(args, "visual", None):
                from clients import hubtools

                visuals, local = hubtools._visuals(
                    client,
                    visuals_from_args(args),
                    lambda index, name, data: hubtools._uploaded(
                        hubtools._posted_file(
                            client, f"tasks/{args.id}/files", name, data, (key + ":visual:" + index) if key else None
                        )
                    ),
                )
                references.extend(visuals)
            body = {"text": args.text}
            if references:
                body["attachments"] = references
            if getattr(args, "needs_action", False):
                body["needs_action"] = True
            body.update(_delivery(args))
            if getattr(args, "public", None):
                body["public"] = True
            if getattr(args, "ok", None):
                body["approval_id"] = args.ok
            links = s3links.apply(client, body, ("text",))
            result = s3links.annotate(post(f"tasks/{args.id}/comments", body, delegate=asked), links)
            return {**result, "visuals_local": local} if local and isinstance(result, dict) else result
        if sub == "comment-edit":
            body = {"text": args.text}
            if getattr(args, "ok", None):
                body["approval_id"] = args.ok
            links = s3links.apply(client, body, ("text",))
            return s3links.annotate(post(f"tasks/{args.id}/comments/{args.comment_id}", body), links)
        if sub == "comment-delete":
            return post(f"tasks/{args.id}/comments/{args.comment_id}/delete", {})
        if sub == "delete":
            return post(f"tasks/{args.id}/delete", {})
        if sub == "deleted":
            return client.get("deleted-tasks")
        if sub == "restore":
            return post(f"tasks/{args.id}/restore", {})
        if sub == "link":
            return post(
                f"tasks/{args.id}/links",
                {
                    "url": args.url,
                    "title": args.title,
                    **({"kind": "session"} if args.session else {}),
                    **({"wake_once": args.wake_once} if args.wake_once is not None else {}),
                },
                delegate=asked,
            )
        if sub == "relate":
            return via_tool(client, args)  # the tool says what to do instead of blocks and blocked_by
        if sub == "attach":
            if getattr(args, "req", None):
                return via_tool(client, args, page=args.file)
            path = Path(args.file)
            body = {"name": args.name or path.name}
            if getattr(args, "note", None) is not None:
                body["note"] = args.note
            if client.features().get("task_files_multipart"):
                uploads = {"file": path}
                if getattr(args, "poster", None):
                    uploads["poster"] = Path(args.poster)
                return client.post_multipart(f"tasks/{args.id}/files", uploads, body, key=key)
            if path.stat().st_size > 10_000_000:
                raise APIError(
                    "too_large", "This server accepts files up to 10 MB; upgrade it for streaming uploads", 413
                )
            with path.open("rb") as source:
                data = source.read(10_000_001)
            if len(data) > 10_000_000:
                raise APIError("too_large", "This server accepts files up to 10 MB", 413)
            try:
                body["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                body["content_base64"] = base64.b64encode(data).decode("ascii")
            return post(f"tasks/{args.id}/files", body)
    if cmd == "goal":
        if sub in ("create", "update"):
            args.parent_id = args.parent
            if args.body_file:
                args.body = Path(args.body_file).read_text()
        return tool(client, args)
    if cmd in ("kpi", "proposal", "strategy", "brand", "growth"):
        return tool(client, args)
    if cmd == "lens":
        from clients import hubtools

        if sub == "list":
            return hubtools.lens_list(client, {"applies_to": "market" if args.market else None})
        if sub == "run":
            answers = json.loads(Path(args.answers_file).read_text(encoding="utf-8-sig")) if args.answers_file else {}
            if not isinstance(answers, dict):
                raise APIError("answers", "--answers-file is a JSON object of {step key: answer}")
            return hubtools.lens_run(
                client,
                {
                    "lens": args.id,
                    "answers": answers,
                    "draft": args.draft,
                    "wait": 0 if args.no_wait else hubtools.LENS_WAIT_MAX,
                    "operation_id": key,
                },
            )
        if sub == "show":
            return hubtools.lens_show(client, {"id": args.id})
        return hubtools.lens_runs(client, {"lens": args.lens, "limit": args.limit})
    if fn.startswith("market watch "):
        return via_tool(client, args)
    if fn == "market briefing latest":
        return via_tool(client, args)
    if fn == "market briefing post":
        body = sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text()
        items = json.loads(Path(args.items_file).read_text()) if args.items_file else []
        answers = json.loads(Path(args.answers_file).read_text()) if args.answers_file else None
        return via_tool(client, args, body=body, items=items, answers=answers)
    if cmd == "market" and fn in MARKET_SPEND:
        return market_spend(client, args, tool)
    if cmd == "market":
        if sub == "show":
            return client.get("market/entities/" + args.id)
        if sub == "find":
            return client.get("market/entities", q=args.text)
        if sub == "edges":
            return client.get("market/edges", src=args.src, dst=args.dst, rel=args.rel, as_of=args.as_of)
        if sub == "delta":
            return client.get("market/delta", since=args.since)
        if sub == "ask":
            return post("market/ask", {"question": args.question})
        if sub == "report":
            return post(
                "market/insights",
                {
                    "kind": args.kind,
                    "about": args.about or "",
                    "claim": args.claim,
                    "source_url": args.source or "",
                    "quote": args.quote or "",
                    "confidence": args.confidence or "medium",
                    "urgent": bool(args.urgent),
                    "source_ref": getattr(args, "source_ref", None) or None,
                },
            )
        if sub == "resolve":
            return post(
                f"market/insights/{args.id}/resolve",
                {"status": args.status, "resolution": args.resolution or "", "applied_events": args.events or []},
            )
        if sub == "apply":
            body = {
                "evidence": {
                    "source_url": args.source or "",
                    "source_kind": args.source_kind or "other",
                    "quote": args.quote or "",
                    "our_read": args.our_read or "",
                }
            }
            verified = None
            if args.last_verified_at:
                from clients import hubtools

                try:
                    verified = hubtools.market_day(args.last_verified_at)
                except ValueError as exc:
                    raise APIError("date", str(exc), 422) from None
            if args.entity_type and args.entity_name:
                body["entity"] = {"type": args.entity_type, "name": args.entity_name, "summary": args.summary or ""}
                if args.tier:
                    body["entity"]["tier"] = args.tier
                if args.new_id:
                    body["entity"]["id"] = args.new_id
                if verified:
                    body["entity"]["last_verified_at"] = verified
            if args.entity_id:
                body["entity_id"] = args.entity_id
                if args.summary is not None:
                    body["summary"] = args.summary
                if verified:
                    body["last_verified_at"] = verified
            if verified and "entity" not in body and "entity_id" not in body:
                raise APIError(
                    "entity",
                    "--last-verified-at needs --entity-id, or --entity-type and --entity-name for a new entity",
                )
            if args.edge_src and args.edge_rel and args.edge_dst:
                body["edge"] = {"src": args.edge_src, "rel": args.edge_rel, "dst": args.edge_dst}
            return post(f"market/insights/{args.id}/apply", body)
        if sub == "import":
            text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8-sig")
            return post(
                "market/import",
                {
                    "name": "" if args.file == "-" else Path(args.file).name,
                    "lines": text,
                    "note": args.note or "",
                    "dry_run": bool(args.preview),
                },
            )
        if sub == "refresh":
            return post("market/delta/refresh", {"today": args.today})
        if sub == "page":
            if not args.body_file:
                return client.get("market/pages/" + args.name)
            if args.body_file == "-":
                text = sys.stdin.read()
            else:
                with open(args.body_file) as f:
                    text = f.read()
            return post("market/pages/" + args.name, {"body": text})
        unverified = []
        for item in args.unverified or []:
            entity_id, _, look = str(item).partition("=")
            unverified.append({"id": entity_id, "look_for": look})
        return post("market/curator/sweep", {"today": args.today, "unverified": unverified})
    if cmd == "listening":
        if fn == "listening save":
            text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
            return post("listening/runs", json.loads(text))
        if fn == "listening decide":
            return post("listening/decide", {"limit": args.limit, "item_ids": args.item_ids or []})
        if fn == "listening show":
            return client.get("listening/items/" + args.id)
        if fn == "listening runs":
            return client.get("listening/runs", since=args.since, source=args.source)
        if fn == "listening item list":
            return client.get("intake", destination=args.destination, status=args.status, limit=args.limit)
        if fn == "listening item resolve":
            return post(
                f"intake/{args.id}/resolve",
                {"status": args.status, "receiver_ref": args.receiver_ref or "", "reason": args.reason or ""},
            )
        return client.get("listening/stats", since=args.since)
    if fn == "conversation show":
        page = client.get(f"conversations/{args.conversation}/messages", before=args.before, since=args.since)
        return {
            "conversation": page.get("conversation"),
            "messages": page.get("messages", []),
            "has_more": bool(page.get("has_more")),
            "next_before": page.get("next_before"),
        }
    if fn == "thread link-slack":  # the MCP tool of the same name: the CLI and MCP run one function
        from clients import hubtools

        return hubtools.BY_NAME["tico_thread_link_slack"]["fn"](
            client,
            {
                "thread": args.thread,
                "channel": args.channel,
                "history": args.history,
                "off": args.off,
                "operation_id": key,
            },
        )
    if cmd == "automation":
        return automation(client, args)
    if cmd == "release":
        return via_tool(client, args)
    if fn.startswith("bot status"):
        if fn == "bot status set":
            bot = args.bot or actor.split(":", 1)[-1]
            return post(f"bots/{bot}/status", {"state": args.state, "focus": args.focus, "task_id": args.task})
        if fn == "bot status history":
            return client.get(f"bots/{args.bot}/history", since=args.since)
        return [b for b in client.get("bots") if not args.team or b["team"] == args.team]
    if cmd == "calendar":
        from clients import hubtools

        description = Path(args.description_file).read_text() if args.description_file else args.description
        return hubtools.BY_NAME["tico_calendar_schedule"]["fn"](
            client,
            {
                "calendar": args.calendar,
                "title": args.title,
                "start": args.start,
                "end": args.end,
                "attendees": args.attendee,
                "description": description,
                "add_meet": not args.no_meet,
                "operation_id": key,
            },
        )
    if cmd == "sql":
        body = {"sql": args.sql, "params": sql_params(args.param)}
        if args.max_rows:
            body["max_rows"] = args.max_rows
        return post("sql", body)
    raise APIError("unsupported", "This command is not supported by the remote API")


def botops_tools(client, args):
    """`tico api`, `tico credential ...`, `tico computer list`, `tico health check`, `tico message redact`, `tico support file`: the
    tool of the same name. A secret is read from standard input, never from the command line."""
    from clients import hubtools

    key = os.environ.get("HUB_OPERATION_ID")
    if args.fn == "api":
        source = getattr(args, "file", None)
        if source and args.body:
            raise APIError("body", "Give the body one way: inline, or --file PATH")
        if "-" in (args.body, source):
            text = sys.stdin.read()
        elif source:
            try:
                text = Path(source).expanduser().read_text()
            except OSError as exc:
                raise APIError("body", f"Cannot read {source}: {exc.strerror}") from None
        else:
            text = args.body
        try:
            body = json.loads(text) if text else None
        except ValueError:
            raise APIError("body", "The body must be JSON") from None
        return hubtools.BY_NAME["tico_api"]["fn"](
            client,
            {
                "method": args.method,
                "path": args.path,
                "body": body,
                "operation_id": key,
                **({"on_behalf_of": args.on_behalf_of} if getattr(args, "on_behalf_of", None) else {}),
            },
        )
    name = tool_name(args.fn)
    fields = {
        k: v
        for k, v in vars(args).items()
        if k not in ("cmd", "sub", "subsub", "fn", "what", "no_redact") and v is not None
    }
    if args.fn in ("credential set", "message redact"):
        value = sys.stdin.read().rstrip("\n")
        if not value:
            raise APIError("value", "Give the secret on standard input: printf '%s' \"$VALUE\" | hub " + args.fn)
        fields["value"] = value
        if args.fn == "credential set" and getattr(args, "no_redact", False):
            fields["redact"] = False
    fields["operation_id"] = key
    return hubtools.BY_NAME[name]["fn"](client, fields)


# ----------------------------------------------------------------------------- bots
def bots(client, args):
    """`tico template list`, `tico bot create|check` and the bot commands that act as the requester: how BotOps sets the
    chosen bots up.

    The materialization is `clients/catalog.py`, which needs no network. What comes from the
    server is this company's names, the onboarding answers everybody's `knowledge/company.md` is
    written from, and the instructions the person wrote for this particular bot.
    """
    from clients import catalog

    workspace = Path(os.environ.get("HUB_WORKSPACE") or "")
    if args.fn == "template list":
        try:
            # The server's cards carry the instructions onboarding filled in; a server without
            # the endpoint still lets a bot read this checkout's own templates.
            return client.get("templates")["cards"]
        except (APIError, KeyError, TypeError):
            return catalog.cards()
    if args.fn == "bot branch":
        from clients import hubtools

        fields = {"bot": args.bot}
        if args.computer:
            listing = hubtools._as_person(client).get("computers")
            rows = listing.get("computers", []) if isinstance(listing, dict) else listing
            me = hubtools._as_person(client).get("me")
            person = str(me.get("actor") or me.get("id") or "").removeprefix("human:")
            matches = [
                row
                for row in rows
                if not row.get("revoked_at")
                and row.get("owner_id") == person
                and args.computer in (row.get("id"), row.get("label"))
            ]
            if len(matches) != 1:
                raise APIError("computer", "Choose one of your computers by its id or unique label")
            fields["runner_id"] = matches[0]["id"]
        return hubtools.bot_branch(client, fields)
    if args.fn == "bot update":
        # The server checks the change as the person who sent the cited message (backend/app.py).
        from clients import hubtools

        fields = {
            k: v
            for k, v in (
                ("slug", args.slug),
                ("reports_to", args.reports_to),
                ("display_name", args.display_name),
                ("description", args.description),
                ("status", args.status),
                ("repo", args.repo),
                ("on_behalf_of", args.on_behalf_of),
                ("shared", args.shared),
                ("session", args.session),
                ("subtask_session", getattr(args, "subtask_session", None)),
                ("task_goal", getattr(args, "task_goal", None)),
                ("review_model", getattr(args, "review_model", None)),
                ("capture_stream", getattr(args, "capture_stream", None)),
                ("new_slug", getattr(args, "new_slug", None)),
            )
            if v is not None
        }
        if getattr(args, "subscribe_updates", None) is not None:
            picked = [p.strip() for p in args.subscribe_updates.split(",") if p.strip() and p.strip() != "none"]
            fields["update_subscriptions"] = picked
        try:
            return hubtools.BY_NAME["tico_bot_update"]["fn"](client, fields)
        except ValueError as exc:
            raise APIError("not_found", str(exc)) from None
    if args.fn in (
        "bot show",
        "bot access",
        "bot mail",
        "bot owners",
        "bot setup-done",
        "bot ready",
        "bot place",
        "bot go-live",
        "bot model",
        "bot pause",
        "bot resume",
        "bot scope",
        "bot restore",
        "bot archive",
        "bot budget",
        "bot release",
        "bot profile",
        "bot task-model",
    ) or (args.fn == "bot create" and args.record_only):
        from clients import hubtools

        fields = {
            k: v
            for k, v in vars(args).items()
            if k not in ("cmd", "sub", "subsub", "fn", "no_setup", "record_only") and v not in (None, [])
        }
        if args.fn == "bot create" and args.record_only:
            fields["build"] = False
        if getattr(args, "no_setup", False):
            fields["setup"] = False
        fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
        return hubtools.BY_NAME[tool_name(args.fn)]["fn"](client, fields)
    if args.fn == "bot check":
        from clients.botcopy import folder_of
        from clients.manifest import folder_dir

        problems = catalog.check(folder_dir(workspace, folder_of(client, args.slug), args.slug), args.slug)
        return {"ready": not problems, "problems": problems}
    if not args.template:
        raise APIError("usage", "tico bot create --template T builds the bot; --record-only only registers it")
    record = onboarding(client)
    chosen = (record.get("selected") or {}).get(args.slug) or {} if args.slug else {}
    registered = register_for_requester(client, args, chosen)
    slug = args.slug or (registered or {}).get("slug") or local_slug(client, args, workspace)
    path = catalog.materialize(
        args.template,
        slug,
        workspace,
        client.get("config"),
        record.get("answers") or {},
        display_name=args.name or chosen.get("display_name"),
        instructions=chosen.get("instructions"),
        folder=(registered or {}).get("folder"),
    )
    result = {
        "path": str(path),
        "template": args.template,
        "slug": slug,
        "committed": catalog.committed(path),
        "automations": seed_automations(
            client if registered is None else _person(client), slug, path, (registered or {}).get("id")
        ),
        **({"registered": registered} if registered else {}),
    }
    result["github"] = github_repository(client, slug, path)
    return result


def local_slug(client, args, workspace):
    """The slug for a bot built in a turn no person started, where the server did not pick one: from its name, else
    the template's, skipping the server's bots and any repository already in the workspace."""
    from clients import catalog
    from clients.manifest import OLD_REPO_PREFIX, REPO_PREFIX

    card = catalog.card(args.template) or {}
    try:
        known = {b.get("slug") or b.get("name") for b in client.get("bots", include_archived="1")}
    except (APIError, AttributeError, TypeError):
        known = set()
    reserved = ("assistant", "botops", "librarian", "goal-manager", "coo", "task-manager", "bookkeeper")
    return catalog.free_slug(
        args.name or card.get("name") or args.template,
        lambda slug: (
            slug in known
            or slug in reserved
            or any((workspace / (prefix + slug)).exists() for prefix in (REPO_PREFIX, OLD_REPO_PREFIX))
        ),
    )


def github_repository(client, slug, path):
    """Give the new bot its empty private `<org>/bot-<slug>` on GitHub. The server records this computer as the one
    holding the build, so the bot's first placement is here, and this computer's runner publishes the history into it. No GitHub connected (or a rehearsal) leaves the bot on its computer, as before. When the GitHub App may not
    create repositories, the build is not finished: the command fails (non-zero) and says a person must act. The
    server has put one Needs-you task in front of the owner and finishes the repository on its own once they do; the
    bot stays planned with its files here. When the App is installed on chosen repositories only, or the name is
    already taken on GitHub by a repository this company did not make, no repository is made or adopted: the bot
    works from this computer and its history is not backed up to GitHub; nobody needs to act."""
    try:
        return client.post("github/repos", {"slug": slug, "empty": True})
    except APIError as exc:
        if exc.code in ("github_not_connected", "rehearsal"):
            return {"skipped": exc.detail}
        if exc.code in ("forbidden", "not_found"):
            # A bot the server has not registered stays on this computer, as before.
            return {"skipped": exc.detail, "note": f"register the bot, then tico bot repo-create {slug} --empty"}
        if exc.code in ("github_selected_repos", "github_repo_taken", "github_repo_exists"):
            return {"skipped": exc.detail}  # never adopt a repository this company may not have made
        if exc.code == "github_permission_missing":
            raise APIError(
                exc.code,
                f"Not finished: {exc.detail} The bot stays planned and its files are in {path}; "
                "finish and commit them there as usual.",
                exc.status,
                False,
                exc.operation_id,
            ) from None
        raise


def register_for_requester(client, args, chosen):
    """`tico bot create` in a turn a person's chat message started also registers the bot with the server as
    them (planned, they own it), so `tico bot update` and its routines have a bot to act on. A refusal for what that
    person may not do (no create_bots, the limit) stops the build; a turn no person started (onboarding, a
    routine) registers nothing here, as before."""
    try:
        return client.post(
            "bots/register",
            {
                **({"slug": args.slug} if args.slug else {}),
                "template": args.template,
                "on_behalf_of": "turn",
                "display_name": args.name or chosen.get("display_name") or "",
            },
        )
    except APIError as exc:
        if exc.code == "on_behalf_of":
            return None
        raise


def _person(client):
    from clients import hubtools

    return hubtools._as_person(client)


def seed_automations(client, slug, path, bot_id=None):
    """The template's `routines:` (older: `schedules:`) become the new bot's first automations: each a schedule (or an
    event, or a webhook trigger) and one bot turn with the routine's text. The manifest is read once, here, with its
    playbooks already rendered; from now on they are edited in Automations. The actor is `bot:<id>` (a bot's id is
    its primary key; its slug is only a label), looked up by slug when the registration did not return it."""
    from clients.manifest import manifest_path, routines_of
    from clients.routines import validate_schedules
    import yaml

    try:
        declared = routines_of(yaml.safe_load(manifest_path(path).read_text()) or {})
        entries = validate_schedules(declared, lambda rel: (path / rel).read_text())
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        return {"error": f"bot.yaml routines: {exc}"}
    if not entries:
        return []
    try:
        actor = "bot:" + (bot_id or client.get("bots/" + slug)["id"])
    except (APIError, KeyError, TypeError) as exc:
        return {"error": f"the bot's id for its routines: {exc}"}
    seeded = []
    for entry in entries:
        on = entry["on"] or ""
        trigger = (
            {"type": "webhook", "id": on[len("webhook.") :]}
            if on.startswith("webhook.")
            else {"type": "event", "name": on}
            if on
            else {"type": "schedule", "cron": entry["cron"], "tz": entry["timezone"]}
        )
        made = client.post(
            "automations",
            {
                "name": entry["title"][:120],
                "owner": actor,
                "enabled": entry["enabled"],
                "trigger": trigger,
                "steps": [
                    {
                        "id": "turn",
                        "type": "bot_turn",
                        "bot": actor,
                        "prompt": entry["instructions"] or "Do this routine's work.",
                    }
                ],
            },
        )
        seeded.append(made.get("id") or (made.get("automation") or {}).get("id"))
    return seeded


def onboarding(client):
    """What the person answered while picking their bots; {} on a server that has no onboarding."""
    try:
        record = client.get("setup")
    except APIError as exc:
        if exc.status in (403, 404):
            return {}
        raise
    return record if isinstance(record, dict) else {}


# ----------------------------------------------------------------------------- integrations
def integrations(client, args):
    if args.fn == "tool list":
        return client.get("tools")["integrations"]
    page = client.get("tools/" + args.service)
    if args.fn == "tool show":
        return page
    if args.query_id:
        for query in page["queries"]:
            if query["id"] == args.query_id:
                return query
        raise APIError("not_found", f"No query {args.query_id} under {page['service']}", 404)
    return query_search(page["queries"], args.term)


from clients.hubtools import query_search  # (one search, shared with the tico_tool_query_search tool)


# ----------------------------------------------------------------------------- decisions
def judge(client, args):
    """`tico decision ask`: load the questions on this side, send them inline, print the answers.

    The hub knows nothing about question sets (questions/README.md): the set is read from this
    checkout, a dynamic choice is completed from `--option`, and the label is the set's
    `id@version` unless one is given."""
    from clients import judge as J

    if args.list:
        return J.list_sets()
    if bool(args.question_set) == bool(args.questions_file):
        raise APIError("usage", "tico decision ask takes --set <name> or --questions-file <file>, and --state-file")
    if not args.state_file:
        raise APIError("usage", "tico decision ask needs --state-file <json> (`-` for stdin)")
    try:
        if args.question_set:
            chosen = J.load_set(args.question_set)
            questions, label = dict(chosen["questions"]), chosen["label"]
        else:
            data = json.loads(Path(args.questions_file).read_text())
            questions = data.get("questions") if isinstance(data, dict) and "questions" in data else data
            label = data.get("label") if isinstance(data, dict) else None
        for spec in args.option or []:
            qid, _, path = spec.partition("=")
            if qid not in questions or not path:
                raise APIError("usage", f"--option names a question of the set and a file: {spec!r}")
            questions[qid] = J.with_options(questions[qid], json.loads(Path(path).read_text()))
        for qid, q in questions.items():
            if isinstance(q, dict) and q.get("dynamic"):
                raise APIError("usage", f"{qid} is a dynamic choice: complete it with --option {qid}=<file>")
        state = json.loads(sys.stdin.read() if args.state_file == "-" else Path(args.state_file).read_text())
    except J.JudgeError as exc:
        raise APIError(exc.code, exc.detail)
    except OSError as exc:
        raise APIError("usage", f"cannot read {exc.filename}: {exc.strerror}")
    except ValueError as exc:
        raise APIError("usage", f"not JSON: {exc}")
    try:
        return J.through_hub(client)(state, questions, args.label or label)
    except J.JudgeError as exc:
        raise APIError(exc.code, exc.detail, exc.status, exc.retryable)


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


def sql_params(pairs):
    """`--param key=value` pairs as the named bindings the API takes; numbers bind as numbers."""
    params = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise APIError("usage", "--param takes key=value")
        if re.fullmatch(r"-?\d+", value):
            params[key] = int(value)
        elif re.fullmatch(r"-?\d+\.\d+", value):
            params[key] = float(value)
        else:
            params[key] = value
    return params


def sql_table(result, width=60):
    """A compact aligned table, one value per cell, with a trailing count line."""
    columns, rows = result["columns"], result["rows"]
    cell = lambda value: ("" if value is None else " ".join(str(value).split()))[:width]
    grid = [[cell(v) for v in row] for row in rows]
    widths = [max([len(name)] + [len(row[i]) for row in grid]) for i, name in enumerate(columns)]
    lines = []
    if columns:
        lines.append("  ".join(name.ljust(widths[i]) for i, name in enumerate(columns)).rstrip())
        lines.append("  ".join("-" * w for w in widths))
        lines += ["  ".join(row[i].ljust(widths[i]) for i in range(len(columns))).rstrip() for row in grid]
    count = f"{result['row_count']} row{'s' if result['row_count'] != 1 else ''}"
    if result.get("truncated"):
        count += " shown (more available; add a LIMIT or raise --max-rows)"
    lines.append(f"{count} ({result['ms']} ms)" + (f"; {result['note']}" if result.get("note") else ""))
    return "\n".join(lines)


def sql_csv(result):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(result["columns"])
    writer.writerows(result["rows"])
    return out.getvalue().rstrip("\n")


# `tico task worktree add` at the bot's worktree limit: Tico recorded a wait and wakes the task; end the turn.
WORKTREE_WAIT_EXIT = 3


def _plain(value, indent=""):
    """Any JSON value as indented `key: value` lines, empty fields left out."""
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                lines += [f"{indent}{k}:", *_plain(v, indent + "  ")]
            else:
                lines.append(f"{indent}{k}: {v}")
        return lines
    if isinstance(value, list):
        lines = []
        for v in value:
            if isinstance(v, (dict, list)):
                inner = _plain(v, indent + "  ")
                lines += [indent + "- " + inner[0].lstrip(), *inner[1:]] if inner else []
            else:
                lines.append(f"{indent}- {v}")
        return lines
    return [f"{indent}{value}"]


def _table(rows, cols):
    """Rows as aligned columns: cols are (header, key or function)."""
    cells = [[h for h, _ in cols]] + [
        ["" if (v := (f(r) if callable(f) else r.get(f))) is None else str(v) for _, f in cols] for r in rows
    ]
    widths = [max(len(row[i]) for row in cells) for i in range(len(cols))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in cells)


def growth_text(fn, result):
    """`tico brand|growth ...` for a person: the summary as text, lists as tables, the rest as indented lines.
    `--json` prints the API response instead."""
    if not isinstance(result, dict):
        return "\n".join(_plain(result))
    if fn == "brand summary":
        return result.get("text") or "Nothing published yet."
    if fn == "brand check":
        hits = result.get("hits") or []
        if not hits:
            return f"No hits against brand v{result.get('n', 0)}." if result.get("n") else "No brand published yet."
        return "\n".join(
            f"{h['level']}: {h['kind']} \"{h['match']}\" at {h['index']}" + (f" ({h['rule']})" if h.get("rule") else "")
            for h in hits
        )
    if fn in ("growth channel list",):
        return _table(
            result.get("channels") or [],
            [
                ("slug", "slug"),
                ("name", "name"),
                ("stage", "stage"),
                ("track", "track"),
                ("owner", "owner_ref"),
                ("budget/mo", "budget_month"),
            ],
        )
    if fn == "growth source list":
        text = _table(
            result.get("sources") or [],
            [
                ("id", "id"),
                ("truth for", "truth_for"),
                ("system", "system"),
                ("reach", lambda r: f"{r.get('reach_kind')}:{r.get('reach_ref') or ''}".rstrip(":")),
                ("may", "may"),
            ],
        )
        conflicts = result.get("conflicts") or []
        return text + ("\n\nConflicts:\n" + "\n".join(_plain(conflicts)) if conflicts else "")
    if fn == "growth economics":
        rows = list(result.get("rows") or []) + ([result["total"]] if result.get("total") else [])
        head = f"Month: {result.get('month') or 'none yet'}"
        return (
            head
            + "\n"
            + _table(
                rows,
                [
                    ("channel", "name"),
                    ("spend", "spend"),
                    ("bot", "bot_cost"),
                    ("new", "new_customers"),
                    ("CAC", "cac"),
                    ("months", "expected_months"),
                    ("LTV", "ltv"),
                    ("LTV:CAC", "ltv_cac"),
                    ("payback", "payback_months"),
                ],
            )
        )
    if fn == "growth cohort list":
        return _table(
            result.get("cohorts") or [],
            [
                ("month", "month"),
                ("channel", lambda r: r.get("channel") or "unknown"),
                ("spend", "spend"),
                ("bot", "bot_cost"),
                ("new", "new_customers"),
                ("d30", "kept_d30"),
                ("d90", "kept_d90"),
                ("d180", "kept_d180"),
                ("quality", "quality"),
            ],
        )
    return "\n".join(_plain(result)) or "Nothing yet."


def strategy_section_text(result, key):
    """One section of the published strategy as text, for `tico strategy show --section`."""
    current = result.get("current") if isinstance(result, dict) else None
    if not current:
        return "Nothing published yet."
    return (current.get("sections") or {}).get(key) or ""


def email_text(result, action):
    if action in ("archive", "unarchive", "mark-read", "mark-unread", "label", "unlabel"):
        verb = {
            "archive": "Archived",
            "unarchive": "Unarchived",
            "mark-read": "Marked read",
            "mark-unread": "Marked unread",
            "label": "Labeled",
            "unlabel": "Unlabeled",
        }[action]
        text = f"{verb} {result['count']} messages in {result['mailbox']}."
        if not result['count']:
            return text
        undo = result['undo']
        words = ['tico', 'email', undo['action'].replace('_', '-'), result['mailbox']]
        if 'label' in undo:
            words.append(undo['label'])
        command = shlex.join(words + undo['ids'])
        return text + (
            " Undo: " + command if len(command) <= 1000 else " Undo: use the inverse action and ids from --json."
        )
    if action == "mailboxes":
        boxes = result.get("mailboxes") or []
        return "# Email mailboxes\n\n" + ("\n".join("- " + box["address"] for box in boxes) or "No readable mailboxes.")
    messages = [result] if action == "message" else result.get("messages") or []
    lines = ["# Email", ""]
    if not messages:
        lines.append("No messages.")
    for message in messages:
        lines.extend(
            [
                "## " + (message.get("subject") or "(no subject)"),
                f"Mailbox: {message['mailbox']}",
                f"Message: {message['msg_id']} · Thread: {message['thread_id']}",
                "From: " + (message.get("from_header") or message.get("from_addr") or ""),
                "To: " + ", ".join(message.get("to") or []),
                "Date: " + (message.get("date") or ""),
                "Labels: " + ", ".join(message.get("labels") or []),
                "",
                message.get("body", message.get("snippet") or ""),
                "",
            ]
        )
        for attachment in message.get("attachments") or []:
            lines.append(f"Attachment: {attachment['name']} ({attachment['type']}, {attachment['size']} bytes)")
    if result.get("next_cursor"):
        lines.extend(["Next cursor: " + result["next_cursor"], ""])
    return "\n".join(lines)


def _entity_line(row):
    """One entity in a list: its id, name and status, and the link keys it has to us (customer_of ...)."""
    keys = " ".join(row.get("links_to_us") or [])
    return f"{row['id']}  {row['name']}  {row['status']}" + (f"  [{keys}]" if keys else "")


def contacts_text(fn, result):
    """`--text` for Contacts reads: a short summary, one line per entity, or a card. Any other command prints JSON."""
    if fn == "contacts self":
        lines = [result["name"] + (f" ({result['primary_domain']})" if result.get("primary_domain") else "")]
        if result.get("operates_in"):
            lines.append("operates in: " + ", ".join(place["name"] for place in result["operates_in"]))
        lines.append(
            "obligations: " + ("none yet" if not result.get("obligations") else str(len(result["obligations"])))
        )
        return "\n".join(lines)
    if fn == "contacts find":
        lines = [_entity_line(row) for row in result.get("rows", [])] or ["(none you can see)"]
        if result.get("next"):
            lines.append(f"more: --after {result['next']}")
        return "\n".join(lines)
    if fn == "contacts get":
        if result.get("ambiguous"):
            return f"{result['ref']} matches {len(result['matches'])} entities; name one by id:\n" + "\n".join(
                _entity_line(row) for row in result["matches"]
            )
        lines = [f"{result['name']} ({result['type']}) {result['id']}"]
        for key, value in sorted(result.get("values", {}).items()):
            for item in value if isinstance(value, list) else [value]:
                lines.append(f"  {key}: {item['value']} (from {item['source_type']}, {str(item['updated_at'])[:10]})")
        for link in result.get("links", []):
            lines.append(f"  {link['link_key']}: {link['other_name']} ({link['other_type']})")
        for ref in result.get("refs", []):
            lines.append(f"  in {ref['system']}: {ref['external_type']} {ref['external_id']}")
        for note in result.get("notes", []):
            lines.append(f"  note ({note['audience']}, {note['author_key']}): {note['body']}")
        return "\n".join(lines)
    return json.dumps(result, indent=2)


def main(args, who=None, fields=None):
    """Run one parsed command and print its answer. `fields`: the global --fields (clients/profiles.py pick), which
    prints only those fields as JSON, in place of any text rendering. Returns the exit code (clients/cli_plumbing.py)."""
    from clients import cli_plumbing, hubtools, hubtools_connections

    try:
        result = run(args, who)
        if fields:
            from clients import profiles

            print(json.dumps(profiles.pick(result, fields), indent=2, default=str))
        elif (
            args.cmd == "email"
            and args.sub
            in (
                "mailboxes",
                "inbox",
                "search",
                "thread",
                "message",
                "archive",
                "unarchive",
                "mark-read",
                "mark-unread",
                "label",
                "unlabel",
            )
            and not args.json
        ):
            print(email_text(result, args.sub))
        elif args.cmd == "sql" and not args.json:
            print(sql_csv(result) if args.csv else sql_table(result))
        elif args.cmd == "db" and not args.json:
            from clients import dbquery

            print(dbquery.render(result, args))
            return 0 if result.get("ok", True) else 2
        elif args.cmd == "contacts" and getattr(args, "as_text", False):
            print(contacts_text(args.fn, result))
        elif args.fn == "tool list" and not args.bot and not args.json:
            print(integrations_text(result))
        elif args.fn == "tool show" and not args.json:
            print(integration_text(result))
        elif args.fn == "strategy show" and getattr(args, "section", None) and not args.json:
            print(strategy_section_text(result, args.section))
        elif args.fn == "tool query-search" and not args.json:
            print(query_text(result) if args.query_id else queries_text(result))
        elif args.cmd in ("brand", "growth") and not getattr(args, "json", False) and args.fn != "growth numbers":
            print(growth_text(args.fn, result))
        elif args.fn == "automation guide" and isinstance(result, dict) and "text" in result:
            print(result["text"])
        elif args.fn == "meeting read" and getattr(args, "notes", False):
            print(result["text"])
        elif (
            args.fn == "task show"
            and not getattr(args, "json", False)
            and isinstance(result, dict)
            and "text" in result
        ):
            print(result["text"])  # the lines a bot reads (docs/pipelines-v3.md S16); --json prints the task
        elif args.fn == "bot go-live" and isinstance(result, dict) and result.get("state") == "waiting_for_repository":
            # Not an error and nothing to retry: Tico finishes it when the computer reports the repository.
            print(
                f"{args.bot} is placed on {result.get('computer')} and waiting for it to get the repository. "
                "Tico finishes going live (turns it on and starts its setup) when "
                f"{result.get('computer')} has the repository; nothing needs to run again."
            )
        else:
            print(json.dumps(result, indent=2))
        if args.fn == "file approved":
            return 0 if isinstance(result, dict) and result.get("approved") else 1
        if (code := hubtools_connections.exit_code(args.fn, result)) is not None:
            return code
        if isinstance(result, dict) and result.get("readback"):  # what a relation write stored, direction and all
            for line in result["readback"]:
                print("tico: stored: " + str(line), file=sys.stderr)
        if isinstance(result, dict) and result.get("warning"):  # saved, but a person should know why it waits
            print("tico: " + str(result["warning"]), file=sys.stderr)
        if isinstance(result, dict) and result.get("held"):  # a bot's message to a bot waits for its next run
            print("tico: " + str(result["held"]), file=sys.stderr)
        return 0
    except APIError as exc:
        if exc.code == "worktree_limit" and isinstance(exc.extra.get("wait"), dict):
            # Not a failure to retry: Tico holds this task's place and wakes it when the worktree is ready. The
            # status is its own so a script or harness can tell "end the turn" from an error.
            print(f"{exc.detail}")
            return WORKTREE_WAIT_EXIT
        if exc.code == "repository_waiting_for_permission":
            # Nothing to retry and nothing for the caller to build: the owner's Needs you task holds the fix.
            print(f"{exc.detail}.")
            return 2
        if args.cmd == "task" and any(k in exc.extra for k in hubtools.REFUSAL_FIELDS):
            # A task refusal: the line, what is missing and the commands allowed next, so the bot acts instead of
            # retrying the same command.
            print(hubtools.refusal_text(hubtools.error_payload(exc), getattr(args, "id", None)))
            return 2
        print(
            json.dumps(
                {"error": exc.code, "detail": exc.detail, "retryable": exc.retryable, "operation_id": exc.operation_id},
                indent=2,
            )
        )
        return cli_plumbing.exit_code(exc)
    except ValueError as exc:
        if args.cmd != "task":
            raise
        # What the command needs first, in its own words ("Add --version N: ..."), as the MCP tool says it.
        print(json.dumps({"error": "usage", "detail": str(exc), "retryable": False}, indent=2))
        return cli_plumbing.EXIT_USAGE
