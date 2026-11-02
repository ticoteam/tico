"""HTTP implementation of the `hub` commands (`clients/hubcli.py` parses them); never opens a database."""

import base64
import csv
import io
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

if __package__ in (None, ""):                          # imported by a script run from anywhere
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import s3links  # noqa: E402
from clients.tico import APIError, Client  # noqa: E402


def tool_name(fn):
    """The MCP tool a command runs: `message mark-read` is `hub_message_mark_read`."""
    return "hub_" + fn.replace(" ", "_").replace("-", "_")


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
    """`hub rules` flags as the API takes them: the Who and With flags become scope objects, the rest pass as they are."""
    def scope(names, everyone=False, all_bots=False):
        out = {}
        if names:
            out["names"] = list(names)
        if everyone:
            out["everyone"] = True
        if all_bots:
            out["all_bots"] = True
        return out or None
    if args.fn in ("rules add", "rules edit", "rules accept"):
        who = scope(getattr(args, "who_names", None), getattr(args, "everyone", False), getattr(args, "all_bots", False))
        if who is not None:
            args.who = who
    if args.fn in ("rules add", "rules edit"):
        with_scope = scope(getattr(args, "with_names", None))
        if with_scope is not None:
            setattr(args, "with", with_scope)
    if args.fn == "rules read" and not getattr(args, "source", None):
        args.fn = "rules get"                      # one rule by id; with --from it is a document read (hub_rules_read)
    if args.fn == "rules add" and getattr(args, "file_version", None) and not getattr(args, "quote", None):
        raise SystemExit("hub rules add: --version goes with --file and --quote")
    if args.fn == "rules add" and getattr(args, "quote", None):
        if not getattr(args, "file_id", None):
            raise SystemExit("hub rules add: a quote needs --file (the document it is in)")
        args.cite = {"file_id": args.file_id, "section": getattr(args, "section", None) or "", "quote": args.quote}
        if getattr(args, "file_version", None):
            args.cite["version"] = args.file_version
    for flag in ("who_names", "with_names", "everyone", "all_bots", "file_id", "section", "quote", "file_version"):
        if hasattr(args, flag):
            delattr(args, flag)
    return args


def via_tool(client, args, **more):
    """Run the tool of the command's own name with the command's flags as its arguments."""
    from clients import hubtools
    fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "subsub", "fn", "visual", "visual_title")
              and v is not None}
    if getattr(args, "visual", None):
        fields["visuals"] = visuals_from_args(args)
    if args.fn in ("bot keep-tasks-going", "bot turns-at-once", "computer turns-at-once") and hasattr(args, "limit"):
        fields["limit"] = args.limit  # explicit null restores the default; omission only reads
    if args.fn == "bot browser-exit" and hasattr(args, "computer"):
        fields["computer"] = args.computer  # explicit null is the bot's own internet; omission only reads
    if args.fn == "computer browser-exit" and "sharing" in fields:
        fields["sharing"] = fields["sharing"] == "on"
    fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
    fields.update(more)
    return hubtools.BY_NAME[tool_name(args.fn)]["fn"](client, fields)


def automation(client, args):
    """`hub automation ...`: the hub_automation_* tools, with flows read from a file or stdin."""
    from clients import hubtools

    def flow(path):
        try:
            data = json.loads(sys.stdin.read() if path == "-" else Path(path).read_text())
        except ValueError as exc:
            raise APIError("file", f"--file is not JSON ({exc})") from None
        if not isinstance(data, dict):
            raise APIError("file", "--file must hold a JSON object")
        return data

    run = lambda name, fields: hubtools.BY_NAME[name]["fn"](client, {**fields, "operation_id": os.environ.get("HUB_OPERATION_ID")})
    if args.sub == "inbox":
        return run("hub_inbox_send", {"inbox": args.inbox, **({"send": args.send == "on"} if args.send else {})})
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
        fields.update({k: getattr(args, k) for k in ("range", "status", "trigger", "owner", "limit", "before")
                       if getattr(args, k, None) is not None})
    elif args.sub == "versions" and args.pending:
        fields["status"] = "pending"
    elif args.sub in ("restore", "discard"):
        fields["n"] = args.n
    elif args.sub == "held":
        fields["action"] = args.action
    return run(tool_name(args.fn), fields)


def product_repo_create(client, name, *, confirm=None, stdin=None, stderr=None):
    """Preview the exact target and require the exact org/name before writing: typed at a terminal, or given as
    `confirm` (`--confirm`, for a bot's turn, which has no terminal) and checked against the preview."""
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    request_id = os.environ.get("HUB_OPERATION_ID") or uuid.uuid4().hex
    key = request_id + ":product-repository"
    if len(key) > 200:
        raise APIError("operation_id", "HUB_OPERATION_ID is too long for a product-repository idempotency key; no repository was created", 422)
    preview = client.get("github/product-repos/preview", name=name)
    print(f"Product repository preview: {preview['repository']} · private · empty (no initial commit)", file=stderr)
    print(f"GitHub App capability: {preview['capability']}. {preview['capability_detail']}", file=stderr)
    if preview["capability"] != "available":
        raise APIError("github_capability", preview["capability_detail"], 409)
    if confirm is not None:
        if confirm != preview["repository"]:
            raise APIError("confirmation_required", f"--confirm must be the exact preview target {preview['repository']}; no repository was created", 409)
    elif not stdin.isatty():
        raise APIError("confirmation_required", f"Confirm the exact target: run it again with --confirm {preview['repository']}, or type it at an interactive terminal; no repository was created")
    else:
        print(f"Type {preview['repository']} to create it: ", end="", file=stderr, flush=True)
        if stdin.readline().strip() != preview["repository"]:
            raise APIError("cancelled", "Confirmation did not match; no repository was created")
    return client.post("github/product-repos", {
        "org": preview["org"], "name": preview["name"], "visibility": preview["visibility"],
        "auto_init": preview["auto_init"], "confirmed": True,
    }, key=key)


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
        raise APIError("validation", f"'{value}': use owner/repo, :read, :write, or :write with +actions, -actions, "
                       "+edit or -edit (e.g. :write+actions+edit)")
    return {"full_name": name, "access": access, **flags}


MARKET_SPEND = ("market define", "market segment add", "market segment tree", "market size add", "market size decide",
                "market size list", "market figure add", "market sources", "market spend", "market research")


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


def run(args, who=None):
    if who:
        raise APIError("identity", "Remote identity comes from authentication; --human is unavailable")
    counsel_held(args)
    # A query may run for 20 s on the server before it is stopped, a decision's providers 25 s; leave room for that.
    client = Client(os.environ["HUB_API_URL"], os.environ.get("HUB_TOKEN", ""), timeout=45 if args.cmd in ("decision", "gateway") else 30 if args.cmd == "sql" else 120 if args.cmd == "listening" or args.fn == "market import" else 130 if args.cmd == "inbound" else 15)
    fn = args.fn
    if args.cmd == "inbound":
        return via_tool(client,args)
    if args.cmd == "rules":
        return via_tool(client, _rules_args(args))
    if args.cmd == "mail":
        return via_tool(client, args)
    if args.cmd == "finance":
        return via_tool(client, args, **({"operation_id": args.operation_id} if getattr(args, "operation_id", None) else {}))
    if args.cmd == "settings":
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
        from runner.worktrees import command
        return command(client, args.worktree_sub, getattr(args, 'repo', None) or getattr(args, 'path', None), args.task)
    if args.cmd == "repo":
        from clients import hubtools
        if args.fn == "repo review":
            body = Path(args.body_file).read_text() if args.body_file else args.body
            return hubtools.repo_review(client, {"repository": args.repository, "number": args.number,
                                                 "head": args.head, "verdict": args.verdict, "body": body,
                                                 **({"operation_id": args.operation_id} if args.operation_id else {})})
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
    if fn in ("bot copy", "bot update-from-original", "bot suggest-to-original", "skill copy"):
        return via_tool(client, args)              # they work in this computer's workspace (clients/botcopy.py)
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
    if fn in ("bot repo-create", "human add", "human list", "group list", "group update", "group delete", "tool list", "tool learn", "tool add", "tool update", "tool remove",
              "update create", "update redo", "update list", "update show", "update mark-read", "update reply", "update settings",
              "changelog list", "changelog mark-read",
              "needs-you start", "needs-you next", "needs-you respond", "needs-you commit", "needs-you abandon",
              "brief", "pipeline", "mcp stats", "calendar list", "calendar status", "team show", "team budget", "org order", "org reports-to", "run list",
              "message list", "message mark-read", "bot recent", "agent pair show", "agent pair approve", "agent pair decline",
              "slack channel list", "slack channel add", "slack channel remove", "slack channel import",
              "seat list", "seat create", "seat update", "seat close"):
        return via_tool(client, args)
    if fn == "run export":
        from clients import hubtools
        if not args.fixture:
            return hubtools.BY_NAME["hub_run_export"]["fn"](client, {"run": args.run})
        found = hubtools.run_export_full(client, args.run)
        return {"written": hubtools.write_fixture(found, Path(args.out or "."))}
    if fn in ("task child create", "task tree", "task bulk", "task model"):
        return via_tool(client, args)
    if fn in ("task requesters", "task requester-save", "task requester-remove", "task publication-preview",
              "task comment-moderate", "roadmap tasks"):
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
        return hubtools.roadmap_settings(client, {"settings": changes or None,
                                                  "operation_id": os.environ.get("HUB_OPERATION_ID")})
    if args.cmd in ("chat", "replay"):
        return via_tool(client, args)
    if fn in ("stall-triage", "audit"):
        return via_tool(client, args)
    if fn and fn.startswith("task-manager "):
        from clients import hubtools
        op = os.environ.get("HUB_OPERATION_ID")
        if fn == "task-manager status":
            return hubtools.task_manager_status(client, {})
        if fn == "task-manager run":
            return hubtools.task_manager_run(client, {"operation_id": op})
        change = {"task-manager on": {"on": True}, "task-manager off": {"on": False},
                  "task-manager schedule": {"preset": getattr(args, "preset", None), "at": getattr(args, "at", None)},
                  "task-manager bot": {"bot": getattr(args, "state", None)}}[fn]
        return hubtools.task_manager_set(client, {**change, "operation_id": op})
    if fn in ("health-monitor findings", "health-monitor report"):
        from clients import hubtools
        if fn == "health-monitor findings":
            return hubtools.health_monitor_findings(client, {})
        return hubtools.health_monitor_report(client, {"key": args.key, "process": args.process, "cpu": args.cpu,
                                                       "mb": args.mb, "operation_id": os.environ.get("HUB_OPERATION_ID")})
    if fn in ("support report", "support send"):
        from clients import hubtools
        key = os.environ.get("HUB_OPERATION_ID")
        if fn == "support send":
            return hubtools.support_send(client, {"approval": args.approval, "operation_id": key})
        text = Path(args.file).read_text(encoding="utf-8") if args.file else args.message
        return hubtools.support_report(client, {"message": text, "include_ids": not args.no_ids, "operation_id": key})
    if args.cmd in ("api", "computer", "credential", "support", "health") or fn == "message redact":
        return botops_tools(client, args)
    if args.cmd == "classify":
        from clients import hubtools
        text = Path(args.file).read_text(errors="replace") if args.file else sys.stdin.read()
        return hubtools.BY_NAME["hub_classify"]["fn"](client, {"text": text})
    if fn in ("decision readout", "decision outcome", "gateway catalog", "gateway search", "gateway fetch", "gateway image"):
        return via_tool(client, args)
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
        return docs_ask.status(client, args.conversation_id, args.message_id, args.wait,
                               key=os.environ.get("HUB_OPERATION_ID"))
    if fn == "doc ask":                 # `doc fetch` never gets here: it runs locally (clients/hubcli.py)
        from clients import docs_ask
        return docs_ask.ask(client, args.question, args.wait, key=os.environ.get("HUB_OPERATION_ID"))
    if args.cmd == "grokbot":
        from clients import hubtools
        body = json.loads(Path(args.file).read_text())
        return hubtools.BY_NAME["hub_grokbot_sync"]["fn"](client, body)
    if args.cmd == "external":
        from clients import hubtools
        body = json.loads(Path(args.file).read_text())
        return hubtools.BY_NAME["hub_external_sync"]["fn"](client, {**body, "provider": args.provider})
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
            fields["body"] = sys.stdin.read() if not args.body_file or args.body_file == "-" else Path(args.body_file).read_text(encoding="utf-8-sig")
        return hubtools.BY_NAME[tool_name(fn)]["fn"](client, fields)
    if args.cmd == "assistant":
        from clients import hubtools
        if args.sub in ("read", "send", "learn", "learned"):
            return via_tool(client, args)
        try:
            body = json.loads(args.body or "{}")
        except ValueError:
            raise APIError("body", "--body must be JSON") from None
        return hubtools.BY_NAME["hub_assistant_propose"]["fn"](client, {
            "summary": args.summary, "path": args.path, "method": args.method, "body": body,
            "operation_id": os.environ.get("HUB_OPERATION_ID")})
    if args.cmd == "db":
        # Runs here, beside the credential; the hub only checks who is asking and keeps the audit.
        from clients import dbquery
        return dbquery.run(client, args)
    if args.cmd == "service-key":
        # A person's own shell (hubtools.SHELL_ONLY): a new key is shown to them, never to an agent's context.
        if fn == "service-key create":
            return client.post("service-keys", {"label": args.label, "scope": getattr(args, "scope", "tasks")},
                               key=os.environ.get("HUB_OPERATION_ID"))
        if fn == "service-key revoke":
            return client.post("service-keys/" + args.id + "/revoke", {}, key=os.environ.get("HUB_OPERATION_ID"))
        return client.get("service-keys")["keys"]
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
        result = (client.call("POST", path, body, (key + suffix) if key else None, delegate=delegate) if delegate
                  else client.post(path, body, key=(key + suffix) if key else None))
        return result["task"] if isinstance(result, dict) and set(result) == {"task"} else result

    def target(value):
        return actor if value in ("me", "self") else value

    def tool(client, args):
        """The goal, KPI and proposal commands run the MCP tool of the same name, so both say the same thing."""
        from clients import hubtools
        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "subsub", "fn", "json") and v is not None}
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
        return identity
    if fn == "message send":
        from clients import hubtools
        return hubtools.BY_NAME["hub_message_send"]["fn"](client, {
            "to": args.to, "text": args.text, "fyi": args.fyi, "conversation_id": args.conversation,
            "refs": args.ref or [], "steer": getattr(args, "steer", False),
            "needs_action": getattr(args, "needs_action", False), "operation_id": key, **_delivery(args),
            **({"visuals": visuals_from_args(args)} if getattr(args, "visual", None) else {})})
    if fn == "question answer":
        return post(f"messages/{args.message_id}/answer", {"text": args.text or args.unknown,
                    "unknown": bool(args.unknown)}, delegate=getattr(args, "on_behalf_of", None) or False)
    if fn == "question ask":
        if len(args.words) < 2:
            raise APIError("usage", 'hub question ask <bot> [<bot>...] "question"')
        from clients import hubtools
        wait = max(0, min(int(args.wait), hubtools.ASK_WAIT_MAX))
        fields = {"operation_id": key}
        pending, failed = hubtools.ask_create(client, args.words[:-1], args.words[-1], wait, fields)
        # Said before the wait: a shell that kills a long wait still leaves the bot holding each id.
        for mid, bot in pending.items():
            print(f"hub: asked {bot}, message {mid}; waiting up to {wait} s for the answer", file=sys.stderr, flush=True)
        return {**failed, **hubtools.ask_wait(client, pending, wait, fields)}
    if fn == "note create":
        text = Path(args.text_file).read_text() if args.text_file else args.text
        payload = {"to": target(args.to), "text": text,
                   **({"defer_for": args.defer_for} if getattr(args, "defer_for", None) else {})}
        links = s3links.apply(client, payload, ("text",))
        return s3links.annotate(post("notes", payload)["note"], links)
    if fn == "note list":
        since = args.since
        m = re.fullmatch(r"(\d+)([hd])", since or "")
        if m:
            hours = int(m.group(1)) * (24 if m.group(2) == "d" else 1)
            since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))
        return client.get("notes", to=target(args.to) if args.to else None,
                          sender=target(args.sender) if args.sender else None, since=since,
                          waiting="true" if args.waiting else None, limit=args.limit)
    if fn == "note delete":
        return post(f"notes/{args.id}/cancel", {})["note"]
    if cmd == "task":
        asked = getattr(args, "on_behalf_of", None) or False
        if sub in ("type", "types"):
            # The task type tools: BotOps changes a type as the person who asked it (clients/hubtools.py _as_person).
            from clients import hubtools
            name, steps_file = getattr(args, "name", None), getattr(args, "steps_file", None)
            edits = name is not None or bool(steps_file)
            if sub == "type":
                change = args.type_sub
            else:                       # `task types`: the older form, which lists unless told to change something
                change = "delete" if args.delete else ("update" if args.id else "create") if edits else None
            if change is None:
                return client.get("task-types" + ("/" + args.id if args.id else ""))
            if change != "create" and not args.id:
                raise ValueError("Choose a type to " + change)
            if change == "create" and name is None:
                raise ValueError("Name the new type with --name")
            fields = {"operation_id": key, **({"id": args.id} if change != "create" else {})}
            if change != "delete":
                for name in ("name", "bots", "numbered", "steps_only", "default_visibility"):
                    if getattr(args, name, None) is not None:
                        fields[name] = getattr(args, name)
                if steps_file:
                    fields["steps"] = json.loads(Path(steps_file).read_text())
            return hubtools.BY_NAME["hub_task_type_" + change]["fn"](client, fields)
        if sub == "create":
            body = Path(args.body_file).read_text() if args.body_file else args.body
            payload = {"owner": target(args.owner), "title": args.title, "body": body,
                       "due": args.due, "goal_id": getattr(args, "goal", None) or None}
            if args.parent:
                payload["relations"] = [{"task": args.parent, "kind": "parent"}]
            if getattr(args, "private", None) is not None:
                payload["private"] = args.private
            if getattr(args, "from_private", False):
                payload["from_private"] = True
            for field in ("type", "step", "number", "qa", "change_paths", "qa_not_checkable_live"):
                if getattr(args, field, None) is not None:
                    payload[field] = (getattr(args, field) == "yes") if field == "qa" else getattr(args, field)
            if args.label:
                payload["labels"] = [x.strip() for one in args.label for x in one.split(",") if x.strip()]
            if getattr(args, "blocker", None):
                repo, status, endpoint = args.blocker
                if not status.isdigit():
                    raise SystemExit("hub: --blocker STATUS is an HTTP status number, e.g. 403")
                payload["blocker"] = {"repo": repo, "status": int(status), "endpoint": endpoint}
            if args.top:
                payload["top"] = True
            if args.link:
                payload["links"] = list(args.link)
            if getattr(args, "next_run", False):
                payload["next_run"] = True
            payload.update(_delivery(args))
            if getattr(args, "request_id", None):
                payload["request_id"] = args.request_id
            for field in ("model", "effort", "harness"):
                if getattr(args, field, None):
                    payload[field] = getattr(args, field)
            links = s3links.apply(client, payload, ("body",))
            return s3links.annotate(post("tasks", payload), links)
        if sub == "show":
            ids = args.id if isinstance(args.id, list) else [args.id]
            if len(ids) > 1:                       # one read for several (hub_task_show_many), not one GET each
                from clients import hubtools
                return hubtools.BY_NAME["hub_task_show_many"]["fn"](client, {"ids": ids})
            return client.get("tasks/" + ids[0])
        if sub == "run":
            return post(f"tasks/{args.id}/run-now", {})
        if sub in ("later", "defer"):           # `defer` is the older name
            return post(f"tasks/{args.id}/later", {"after": args.after, "at": args.at,
                                                   "next_run": bool(args.next_run), "handled": bool(args.handled)})
        if sub == "waive-qa":
            return post(f"tasks/{args.id}/qa-waiver", {"head": args.head, "reason": args.reason})
        if sub in ("list", "snooze", "unsnooze"):
            return via_tool(client, args)
        if sub == "ask":
            return post(f"tasks/{args.id}/ask", {"text": args.text})
        if sub == "question":
            return via_tool(client, args)
        if sub == "comment":
            from clients.task_review import ask_from_args, attachment
            ask = ask_from_args(args)
            references = list(getattr(args, "file", None) or [])
            for index, path in enumerate(getattr(args, "attach", None) or []):
                made = post(f"tasks/{args.id}/files", attachment(path), suffix=f":attachment:{index}")
                references.append(f"{made['file_id']}@{made['version']}")
            local = []
            if getattr(args, "visual", None):
                from clients import hubtools
                visuals, local = hubtools._visuals(client, visuals_from_args(args), lambda index, name, data: hubtools._uploaded(
                    hubtools._posted_file(client, f"tasks/{args.id}/files", name, data,
                                          (key + ":visual:" + index) if key else None)))
                references.extend(visuals)
            body = {"text": args.text}
            if references:
                body["attachments"] = references
            if ask is not None:
                body["ask"] = ask
            if getattr(args, "needs_action", False):
                body["needs_action"] = True
            body.update(_delivery(args))
            if getattr(args, "public", None):
                body["public"] = True
            if getattr(args, "approval_id", None):
                body["approval_id"] = args.approval_id
            links = s3links.apply(client, body, ("text",))
            result = s3links.annotate(post(f"tasks/{args.id}/comments", body, delegate=asked), links)
            return {**result, "visuals_local": local} if local and isinstance(result, dict) else result
        if sub == "answers":
            return client.get(f"tasks/{args.id}/answers")
        if sub == "comment-edit":
            body = {"text": args.text}
            if getattr(args, "approval_id", None):
                body["approval_id"] = args.approval_id
            links = s3links.apply(client, body, ("text",))
            return s3links.annotate(post(f"tasks/{args.id}/comments/{args.comment_id}", body), links)
        if sub == "comment-delete":
            return post(f"tasks/{args.id}/comments/{args.comment_id}/delete", {})
        if sub == "ask-resolve":
            return post(f"tasks/{args.id}/comments/{args.comment_id}/resolve",
                        {"quote": args.quote} if args.quote is not None else {})
        if sub == "delete":
            return post(f"tasks/{args.id}/delete", {})
        if sub == "deleted":
            return client.get("deleted-tasks")
        if sub == "restore":
            return post(f"tasks/{args.id}/restore", {})
        if sub == "link":
            return post(f"tasks/{args.id}/links", {"url": args.url, "title": args.title,
                                                   **({"kind": "session"} if args.session else {}),
                                                   **({"wake_once": args.wake_once} if args.wake_once is not None else {})},
                        delegate=asked)
        if sub == "relate":
            return post(f"tasks/{args.id}/relations", {"task": args.task, "kind": args.kind, "remove": args.remove})
        if sub == "wait":
            return post(f"tasks/{args.id}/waits", {"on": args.wait_on, "remove": args.remove,
                                                   **({"note": args.note} if getattr(args, "note", None) else {})})
        if sub == "label":
            current = client.get("tasks/" + args.id)["task"]
            labels = [x for x in current.get("labels") or []]
            for one in (args.remove or []):
                for x in one.split(","):
                    labels = [y for y in labels if y != x.strip().lower()]
            for one in (args.add or []):
                for x in one.split(","):
                    if x.strip() and x.strip().lower() not in labels:
                        labels.append(x.strip().lower())
            body = {"version": current["version"], "labels": labels}
            if getattr(args, "approval_id", None):
                body["approval_id"] = args.approval_id
            return post("tasks/" + args.id, body, delegate=asked)
        if sub == "attach":
            path = Path(args.file)
            from clients.task_review import ask_from_args
            body = {"name": args.name or path.name}
            ask = ask_from_args(args)
            if ask is not None:
                body["ask"] = ask
            if getattr(args, "note", None) is not None:
                body["note"] = args.note
            if client.features().get("task_files_multipart"):
                uploads = {"file": path}
                if getattr(args, "poster", None):
                    uploads["poster"] = Path(args.poster)
                return client.post_multipart(f"tasks/{args.id}/files", uploads, body, key=key)
            if path.stat().st_size > 10_000_000:
                raise APIError("too_large", "This server accepts files up to 10 MB; upgrade it for streaming uploads", 413)
            with path.open("rb") as source:
                data = source.read(10_000_001)
            if len(data) > 10_000_000:
                raise APIError("too_large", "This server accepts files up to 10 MB", 413)
            try:
                body["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                body["content_base64"] = base64.b64encode(data).decode("ascii")
            return post(f"tasks/{args.id}/files", body)
        if sub in ("update", "close", "done"):
            current = client.get("tasks/" + args.id)["task"]
            body = {"version": current["version"], "note": args.note}
            if sub == "done":
                body["status"] = "done"
            for field in ("verdict", "verdict_head"):
                if getattr(args, field, None) is not None:
                    body[field] = getattr(args, field)
            if getattr(args, "brief", False):
                body["quiet"] = True
            if sub == "close":
                body["close"] = True
            elif sub == "update":
                body.update({"status": args.status, "owner": args.owner, "due": args.due,
                             "goal_id": getattr(args, "goal", None)})
                if getattr(args, "private", None) is not None:
                    body["private"] = args.private
                for field in ("title", "type", "step", "step_rank", "number"):
                    if getattr(args, field, None) is not None:
                        body[field] = getattr(args, field)
                if getattr(args, "waiting_on", None) is not None:
                    body["waiting_on"] = args.waiting_on
            links = s3links.apply(client, body, ("note",))
            return s3links.annotate(post("tasks/" + args.id, body, delegate=asked), links)
    if cmd == "goal":
        if sub in ("create", "update"):
            args.parent_id = args.parent
            if args.body_file:
                args.body = Path(args.body_file).read_text()
        return tool(client, args)
    if cmd in ("kpi", "proposal", "strategy", "brand", "gtm", "growth"):
        return tool(client, args)
    if cmd == "lens":
        from clients import hubtools
        if sub == "list":
            return hubtools.lens_list(client, {"applies_to": "market" if args.market else None})
        if sub == "run":
            answers = json.loads(Path(args.answers_file).read_text(encoding="utf-8-sig")) if args.answers_file else {}
            if not isinstance(answers, dict):
                raise APIError("answers", "--answers-file is a JSON object of {step key: answer}")
            return hubtools.lens_run(client, {"lens": args.id, "answers": answers, "draft": args.draft,
                                              "wait": 0 if args.no_wait else hubtools.LENS_WAIT_MAX, "operation_id": key})
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
        return via_tool(client, args, body=body, items=items)
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
            return post("market/insights", {"kind": args.kind, "about": args.about or "", "claim": args.claim,
                        "source_url": args.source or "", "quote": args.quote or "",
                        "confidence": args.confidence or "medium", "urgent": bool(args.urgent),
                        "source_ref": getattr(args, "source_ref", None) or None})
        if sub == "resolve":
            return post(f"market/insights/{args.id}/resolve", {"status": args.status,
                        "resolution": args.resolution or "", "applied_events": args.events or []})
        if sub == "apply":
            body = {"evidence": {"source_url": args.source or "", "source_kind": args.source_kind or "other",
                                 "quote": args.quote or "", "our_read": args.our_read or ""}}
            if args.entity_type and args.entity_name:
                body["entity"] = {"type": args.entity_type, "name": args.entity_name,
                                  "summary": args.summary or ""}
                if args.tier:
                    body["entity"]["tier"] = args.tier
                if args.new_id:
                    body["entity"]["id"] = args.new_id
            if args.entity_id:
                body["entity_id"] = args.entity_id
                if args.summary is not None:
                    body["summary"] = args.summary
            if args.edge_src and args.edge_rel and args.edge_dst:
                body["edge"] = {"src": args.edge_src, "rel": args.edge_rel, "dst": args.edge_dst}
            return post(f"market/insights/{args.id}/apply", body)
        if sub == "import":
            text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8-sig")
            return post("market/import", {"name": "" if args.file == "-" else Path(args.file).name, "lines": text,
                                          "note": args.note or "", "dry_run": bool(args.preview)})
        if sub == "refresh":
            return post("market/delta/refresh", {"today": args.today})
        if sub == "page":
            if not args.body_file:
                return client.get("market/pages/" + args.name)
            text = sys.stdin.read() if args.body_file == "-" else open(args.body_file).read()
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
            return post(f"intake/{args.id}/resolve", {"status": args.status, "receiver_ref": args.receiver_ref or "",
                        "reason": args.reason or ""})
        return client.get("listening/stats", since=args.since)
    if fn == "conversation show":
        page = client.get(f"conversations/{args.conversation}/messages", before=args.before, since=args.since)
        return {"conversation": page.get("conversation"), "messages": page.get("messages", []),
                "has_more": bool(page.get("has_more")), "next_before": page.get("next_before")}
    if cmd == "automation":
        return automation(client, args)
    if cmd == "release":
        return via_tool(client, args)
    if cmd == "approval":
        if sub == "show":
            return client.get("approvals/" + args.id)
        if sub == "read-back":
            return post("assistant/read-back", {"approvals": args.ids})
        if sub in ("approve", "decline"):
            return post("approvals/" + args.id, {"decision": sub + "d", "note": args.note},
                        delegate=getattr(args, "on_behalf_of", None) or False)
        payload = json.loads(Path(args.payload_file).read_text() if args.payload_file else args.payload or "{}")
        return post("approvals", {"kind": args.kind, "payload": payload, "task_id": args.task,
                                  "supersedes_approval": getattr(args, "supersedes", None)})
    if fn.startswith("bot status"):
        if fn == "bot status set":
            bot = args.bot or actor.split(":", 1)[-1]
            return post(f"bots/{bot}/status", {"state": args.state, "focus": args.focus, "task_id": args.task})
        if fn == "bot status history":
            return client.get(f"bots/{args.bot}/history", since=args.since)
        return [b for b in client.get("bots") if not args.team or b["team"] == args.team]
    if cmd == "calendar":
        from clients import hubtools
        description = (Path(args.description_file).read_text()
                       if args.description_file else args.description)
        return hubtools.BY_NAME["hub_calendar_schedule"]["fn"](client, {
            "calendar": args.calendar, "title": args.title, "start": args.start, "end": args.end,
            "attendees": args.attendee, "description": description,
            "add_meet": not args.no_meet, "operation_id": key,
        })
    if cmd == "sql":
        body = {"sql": args.sql, "params": sql_params(args.param)}
        if args.max_rows:
            body["max_rows"] = args.max_rows
        return post("sql", body)
    raise APIError("unsupported", "This command is not supported by the remote API")


def botops_tools(client, args):
    """`hub api`, `hub credential ...`, `hub computer list`, `hub health check`, `hub message redact`, `hub support file`: the
    tool of the same name. A secret is read from standard input, never from the command line."""
    from clients import hubtools
    key = os.environ.get("HUB_OPERATION_ID")
    if args.fn == "api":
        text = sys.stdin.read() if args.body == "-" else args.body
        try:
            body = json.loads(text) if text else None
        except ValueError:
            raise APIError("body", "The body must be JSON") from None
        return hubtools.BY_NAME["hub_api"]["fn"](client, {"method": args.method, "path": args.path, "body": body, "operation_id": key,
                                                         **({"on_behalf_of": args.on_behalf_of} if getattr(args, "on_behalf_of", None) else {})})
    name = tool_name(args.fn)
    fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "subsub", "fn", "what", "no_redact") and v is not None}
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
    """`hub template list`, `hub bot create|check` and the bot commands that act as the requester: how BotOps sets the
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
            matches = [row for row in rows if not row.get("revoked_at") and row.get("operator") == person
                       and args.computer in (row.get("id"), row.get("label"))]
            if len(matches) != 1:
                raise APIError("computer", "Choose one of your computers by its id or unique label")
            fields["runner_id"] = matches[0]["id"]
        return hubtools.bot_branch(client, fields)
    if args.fn == "bot update":
        # The server checks the change as the person who sent the cited message (backend/app.py).
        from clients import hubtools
        fields = {k: v for k, v in (("slug", args.slug), ("reports_to", args.reports_to), ("display_name", args.display_name),
                                    ("description", args.description), ("status", args.status), ("repo", args.repo),
                                    ("on_behalf_of", args.on_behalf_of), ("shared", args.shared), ("session", args.session),
                                    ("subtask_session", getattr(args, "subtask_session", None)),
                                    ("task_goal", getattr(args, "task_goal", None)),
                                    ("review_model", getattr(args, "review_model", None)),
                                    ("capture_stream", getattr(args, "capture_stream", None)),
                                    ("new_slug", getattr(args, "new_slug", None))) if v is not None}
        if getattr(args, "subscribe_updates", None) is not None:
            picked = [p.strip() for p in args.subscribe_updates.split(",") if p.strip() and p.strip() != "none"]
            fields["update_subscriptions"] = picked
        try:
            return hubtools.BY_NAME["hub_bot_update"]["fn"](client, fields)
        except ValueError as exc:
            raise APIError("not_found", str(exc)) from None
    if args.fn in ("bot access", "bot mail", "bot owners", "bot setup-done", "bot ready", "bot place", "bot go-live", "bot model", "bot pause",
                   "bot resume", "bot scope", "bot restore", "bot archive", "bot budget", "bot release", "bot profile",
                   "bot task-model") or (args.fn == "bot create" and args.record_only):
        from clients import hubtools
        fields = {k: v for k, v in vars(args).items()
                  if k not in ("cmd", "sub", "subsub", "fn", "no_setup", "record_only") and v not in (None, [])}
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
        raise APIError("usage", "hub bot create --template T builds the bot; --record-only only registers it")
    record = onboarding(client)
    chosen = (record.get("selected") or {}).get(args.slug) or {} if args.slug else {}
    registered = register_for_requester(client, args, chosen)
    slug = args.slug or (registered or {}).get("slug") or local_slug(client, args, workspace)
    path = catalog.materialize(args.template, slug, workspace, client.get("config"),
                               record.get("answers") or {},
                               display_name=args.name or chosen.get("display_name"),
                               instructions=chosen.get("instructions"),
                               folder=(registered or {}).get("folder"))
    result = {"path": str(path), "template": args.template, "slug": slug,
              "committed": catalog.committed(path),
              "automations": seed_automations(client if registered is None else _person(client), slug, path),
              **({"registered": registered} if registered else {})}
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
    reserved = ("assistant", "botops", "librarian", "goal-manager", "coo", "task-manager", "health-monitor", "bookkeeper")
    return catalog.free_slug(args.name or card.get("name") or args.template, lambda slug: (
        slug in known or slug in reserved
        or any((workspace / (prefix + slug)).exists() for prefix in (REPO_PREFIX, OLD_REPO_PREFIX))))


def github_repository(client, slug, path):
    """Give the new bot its empty private `<org>/bot-<slug>` on GitHub. The server records this computer as the one
    holding the build, so the bot's first placement is here, and this computer's runner publishes the history into it. No GitHub connected (or a rehearsal) leaves the bot on its computer, as before. When the GitHub App may not
    create repositories, the build is not finished: the command fails (non-zero) and says a person must act. The
    server has put one Needs-you task in front of the owner and finishes the repository on its own once they do; the
    bot stays planned with its files here."""
    try:
        return client.post("github/repos", {"slug": slug, "empty": True})
    except APIError as exc:
        if exc.code in ("github_not_connected", "rehearsal"):
            return {"skipped": exc.detail}
        if exc.code in ("forbidden", "not_found"):
            # A bot the server has not registered stays on this computer, as before.
            return {"skipped": exc.detail, "note": f"register the bot, then hub bot repo-create {slug} --empty"}
        if exc.code == "github_repo_exists":
            return {"repository": path.name, "existing": True, "note": exc.detail}
        if exc.code == "github_permission_missing":
            raise APIError(exc.code, f"Not finished: {exc.detail} The bot stays planned and its files are in {path}; "
                           "finish and commit them there as usual.", exc.status, False, exc.operation_id) from None
        raise


def register_for_requester(client, args, chosen):
    """`hub bot create` in a turn a person's chat message started also registers the bot with the server as
    them (planned, they own it), so `hub bot update` and its routines have a bot to act on. A refusal for what that
    person may not do (no create_bots, the limit) stops the build; a turn no person started (onboarding, a
    routine) registers nothing here, as before."""
    try:
        return client.post("bots/register", {**({"slug": args.slug} if args.slug else {}),
                                             "template": args.template, "on_behalf_of": "turn",
                                             "display_name": args.name or chosen.get("display_name") or ""})
    except APIError as exc:
        if exc.code == "on_behalf_of":
            return None
        raise


def _person(client):
    from clients import hubtools
    return hubtools._as_person(client)


def seed_automations(client, slug, path):
    """The template's `routines:` (older: `schedules:`) become the new bot's first automations: each a schedule (or an
    event, or a webhook trigger) and one bot turn with the routine's text. The manifest is read once, here, with its
    playbooks already rendered; from now on they are edited in Automations."""
    from clients.manifest import manifest_path, routines_of
    from clients.routines import validate_schedules
    import yaml
    try:
        declared = routines_of(yaml.safe_load(manifest_path(path).read_text()) or {})
        entries = validate_schedules(declared, lambda rel: (path / rel).read_text())
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        return {"error": f"bot.yaml routines: {exc}"}
    seeded = []
    for entry in entries:
        on = entry["on"] or ""
        trigger = ({"type": "webhook", "id": on[len("webhook."):]} if on.startswith("webhook.") else
                   {"type": "event", "name": on} if on else
                   {"type": "schedule", "cron": entry["cron"], "tz": entry["timezone"]})
        made = client.post("automations", {
            "name": entry["title"][:120], "owner": "bot:" + slug, "enabled": entry["enabled"], "trigger": trigger,
            "steps": [{"id": "turn", "type": "bot_turn", "bot": "bot:" + slug,
                       "prompt": entry["instructions"] or "Do this routine's work."}]})
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


from clients.hubtools import query_search  # noqa: E402  (one search, shared with the hub_tool_query_search tool)


# ----------------------------------------------------------------------------- decisions
def judge(client, args):
    """`hub decision ask`: load the questions on this side, send them inline, print the answers.

    The hub knows nothing about question sets (questions/README.md): the set is read from this
    checkout, a dynamic choice is completed from `--option`, and the label is the set's
    `id@version` unless one is given."""
    from clients import judge as J
    if args.list:
        return J.list_sets()
    if bool(args.question_set) == bool(args.questions_file):
        raise APIError("usage", "hub decision ask takes --set <name> or --questions-file <file>, and --state-file")
    if not args.state_file:
        raise APIError("usage", "hub decision ask needs --state-file <json> (`-` for stdin)")
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
        head = (f"{row['service']:<24} {row['kind']:<8} {row['writes']:<9} {row['summary']}"
                f"  [{row['query_count']} queries, {row['learning_count']} learnings]")
        extra = [f"  credentials: {c}" for c in (row.get("credentials") or [])]
        if row.get("access"):
            extra.append(f"  access: {row['access']}")
        blocks.append("\n".join([head, *extra]))
    return "\n\n".join(blocks) or "no integrations"


def integration_text(page):
    out = [f"# {page['title']} ({page['service']}, {page['kind']}, writes: {page['writes']}, owner: {page['owner']})",
           "", page["summary"], "", f"Access: {page['access']}", "Credentials:"]
    out += [f"  - {c}" for c in page["credentials"]]
    out += ["Declared in bot.yaml as:"] + ["  " + line for line in page["declared_as"].rstrip().splitlines()]
    out += ["", page["body"].rstrip()]
    if page["queries"]:
        out += ["", f"## Queries ({len(page['queries'])}; `hub tool query-search {page['service']} <term>` searches, `--id <id>` prints one)"]
        out += [f"  {q['id']:<44} {q['title']}" for q in page["queries"]]
    notes = page.get("learnings") or []
    out += ["", f"## Learnings ({len(notes)}; add one with `hub tool learn {page['service']} \"...\"`)"]
    out += [f"  {n['created_at'][:10]}  {n['actor']}: {n['text']}" for n in notes] or ["  none yet"]
    return "\n".join(out)


def query_text(query):
    out = [f"{query['id']}: {query['title']}", query.get("description", ""),
           f"database: {query.get('database', '')}  category: {query.get('category', '')}  tags: {', '.join(query.get('tags') or [])}"]
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
    cell = lambda value: ("" if value is None else " ".join(str(value).split()))[:width]  # noqa: E731
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


# `hub task worktree add` at the bot's worktree limit: Tico recorded a wait and wakes the task; end the turn.
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
    cells = [[h for h, _ in cols]] + [["" if (v := (f(r) if callable(f) else r.get(f))) is None else str(v)
                                       for _, f in cols] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(cols))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in cells)


def growth_text(fn, result):
    """`hub brand|gtm|growth ...` for a person: the summary as text, lists as tables, the rest as indented lines.
    `--json` prints the API response instead."""
    if not isinstance(result, dict):
        return "\n".join(_plain(result))
    if fn in ("brand summary", "gtm summary"):
        return result.get("text") or "Nothing published yet."
    if fn == "brand check":
        hits = result.get("hits") or []
        if not hits:
            return f"No hits against brand v{result.get('n', 0)}." if result.get("n") else "No brand published yet."
        return "\n".join(f"{h['level']}: {h['kind']} \"{h['match']}\" at {h['index']}" + (f" ({h['rule']})" if h.get("rule") else "")
                         for h in hits)
    if fn == "growth segment list":
        return _table(result.get("segments") or [], [("id", "id"), ("name", "name"), ("status", "status"),
                      ("motion", "motion"), ("buyer", "who_buys"), ("deal", "deal_size"),
                      ("channels", lambda r: ", ".join(r.get("channels") or [])), ("ideas", "ideas")])
    if fn in ("growth channel list",):
        return _table(result.get("channels") or [], [("slug", "slug"), ("name", "name"), ("stage", "stage"),
                      ("track", "track"), ("owner", "owner_ref"), ("budget/mo", "budget_month")])
    if fn in ("gtm sources", "growth source list"):
        text = _table(result.get("sources") or [], [("id", "id"), ("truth for", "truth_for"), ("system", "system"),
                      ("reach", lambda r: f"{r.get('reach_kind')}:{r.get('reach_ref') or ''}".rstrip(":")), ("may", "may")])
        conflicts = result.get("conflicts") or []
        return text + ("\n\nConflicts:\n" + "\n".join(_plain(conflicts)) if conflicts else "")
    if fn == "growth idea list":
        return _table(result.get("ideas") or [], [("id", "task_id"), ("status", "status"), ("ice", "ice"),
                      ("title", "title"), ("assignee", "assignee")])
    if fn == "growth economics":
        rows = list(result.get("rows") or []) + ([result["total"]] if result.get("total") else [])
        head = f"Month: {result.get('month') or 'none yet'}"
        head += f"  Segment: {result['segment']}" if result.get("segment") else ""
        return head + "\n" + _table(rows, [
            ("channel", "name"), ("spend", "spend"), ("bot", "bot_cost"), ("new", "new_customers"), ("CAC", "cac"),
            ("months", "expected_months"), ("LTV", "ltv"), ("LTV:CAC", "ltv_cac"), ("payback", "payback_months")])
    if fn == "growth cohort list":
        return _table(result.get("cohorts") or [], [("month", "month"), ("channel", lambda r: r.get("channel") or "unknown"),
                      ("spend", "spend"), ("bot", "bot_cost"), ("new", "new_customers"), ("d30", "kept_d30"),
                      ("d90", "kept_d90"), ("d180", "kept_d180"), ("quality", "quality")])
    return "\n".join(_plain(result)) or "Nothing yet."


def main(args, who=None):
    try:
        result = run(args, who)
        if args.cmd == "sql" and not args.json:
            print(sql_csv(result) if args.csv else sql_table(result))
        elif args.cmd == "db" and not args.json:
            from clients import dbquery
            print(dbquery.render(result, args))
            return 0 if result.get("ok", True) else 2
        elif args.fn == "tool list" and not args.bot and not args.json:
            print(integrations_text(result))
        elif args.fn == "tool show" and not args.json:
            print(integration_text(result))
        elif args.fn == "tool query-search" and not args.json:
            print(query_text(result) if args.query_id else queries_text(result))
        elif args.cmd in ("brand", "gtm", "growth") and not getattr(args, "json", False):
            print(growth_text(args.fn, result))
        elif args.fn == "automation guide" and isinstance(result, dict) and "text" in result:
            print(result["text"])
        elif args.fn == "bot go-live" and isinstance(result, dict) and result.get("state") == "waiting_for_repository":
            # Not an error and nothing to retry: Tico finishes it when the computer reports the repository.
            print(f"{args.bot} is placed on {result.get('computer')} and waiting for it to get the repository. "
                  "Tico finishes going live (turns it on and starts its setup) when "
                  f"{result.get('computer')} has the repository; nothing needs to run again.")
        else:
            print(json.dumps(result, indent=2))
        if args.fn == "file approved":
            return 0 if isinstance(result, dict) and result.get("approved") else 1
        if isinstance(result, dict) and result.get("readback"):    # what a relation write stored, direction and all
            for line in result["readback"]:
                print("hub: stored: " + str(line), file=sys.stderr)
        if isinstance(result, dict) and result.get("warning"):     # saved, but a person should know why it waits
            print("hub: " + str(result["warning"]), file=sys.stderr)
        if isinstance(result, dict) and result.get("held"):        # a bot's message to a bot waits for its next run
            print("hub: " + str(result["held"]), file=sys.stderr)
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
        print(json.dumps({"error": exc.code, "detail": exc.detail, "retryable": exc.retryable,
                          "operation_id": exc.operation_id}, indent=2))
        return 1 if exc.retryable else 2
