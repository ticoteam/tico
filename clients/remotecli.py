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


def via_tool(client, args, **more):
    """Run the tool of the command's own name with the command's flags as its arguments."""
    from clients import hubtools
    fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "subsub", "fn") and v is not None}
    fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
    fields.update(more)
    return hubtools.BY_NAME[tool_name(args.fn)]["fn"](client, fields)


def product_repo_create(client, name, *, stdin=None, stderr=None):
    """Preview the exact target and require an interactive exact-name confirmation before writing."""
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
    if not stdin.isatty():
        raise APIError("confirmation_required", "This command requires an interactive terminal. Type the exact org/name shown to confirm; no repository was created")
    print(f"Type {preview['repository']} to create it: ", end="", file=stderr, flush=True)
    if stdin.readline().strip() != preview["repository"]:
        raise APIError("cancelled", "Confirmation did not match; no repository was created")
    return client.post("github/product-repos", {
        "org": preview["org"], "name": preview["name"], "visibility": preview["visibility"],
        "auto_init": preview["auto_init"], "confirmed": True,
    }, key=key)


def run(args, who=None):
    if who:
        raise APIError("identity", "Remote identity comes from authentication; --human is unavailable")
    # A query may run for 20 s on the server before it is stopped; leave room for that.
    client = Client(os.environ["HUB_API_URL"], os.environ.get("HUB_TOKEN", ""), timeout=30 if args.cmd == "sql" else 120 if args.cmd == "listening" or args.fn == "market import" else 15)
    fn = args.fn
    if fn == "team icon":
        with Path(args.file).open("rb") as source:
            return client.post_bytes("team/icon", source)
    if fn in ("task worktree add", "task worktree attach", "task worktree setup"):
        from runner.worktrees import command
        return command(client, args.worktree_sub, getattr(args, 'repo', None) or getattr(args, 'path', None), args.task)
    if args.cmd == "repo":
        from clients import hubtools
        if args.fn == "repo product-create":
            return product_repo_create(client, args.name)
        if args.sub == "list":
            return hubtools.repo_list(client, {})
        return hubtools.repo_update(client, {"full_name": args.full_name, "enabled": args.sub == "tick"})
    if fn == "bot repos":
        from clients import hubtools
        if not args.all and not args.own and args.chosen is None:
            return hubtools.bot_repos_get(client, {"bot": args.bot})
        body = {"bot": args.bot, "mode": "all" if args.all else "own" if args.own else "chosen"}
        if args.chosen is not None:
            body["chosen"] = [{"full_name": r.removesuffix(":read").removesuffix(":write"),
                               "access": "read" if r.endswith(":read") else "write"} for r in args.chosen]
        return hubtools.bot_repos_set(client, body)
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
    if fn in ("bot repo-create", "human add", "human list", "group list", "group update", "tool list", "tool learn", "tool add", "tool update", "tool remove",
              "update create", "update redo", "update list", "update show", "update mark-read", "update reply", "update settings",
              "changelog list", "changelog mark-read",
              "needs-you start", "needs-you next", "needs-you respond", "needs-you commit", "needs-you abandon",
              "brief", "mcp stats", "calendar list", "calendar status", "routine update", "team show", "run list",
              "message list", "message mark-read", "bot recent", "agent pair show", "agent pair approve", "agent pair decline",
              "slack channel list", "slack channel add", "slack channel remove", "slack channel import"):
        if fn == "routine update":                  # --enable / --disable are the tool's `enabled`; a key or an id names it
            more = {"text": Path(args.text_file).read_text()} if args.text_file else {}
            if args.enable or args.disable:
                more["enabled"] = bool(args.enable)
            return via_tool(client, args, **more)
        return via_tool(client, args)
    if fn in ("task child create", "task tree"):
        return via_tool(client, args)
    if args.cmd == "chat":
        return via_tool(client, args)
    if args.cmd in ("api", "computer", "credential", "support", "health") or fn == "message redact":
        return botops_tools(client, args)
    if args.cmd == "classify":
        from clients import hubtools
        text = Path(args.file).read_text(errors="replace") if args.file else sys.stdin.read()
        return hubtools.BY_NAME["hub_classify"]["fn"](client, {"text": text})
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
    if args.cmd == "doc":
        from clients import hubtools
        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "fn", "body_file") and v is not None}
        fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
        if fn == "doc write":
            fields["body"] = sys.stdin.read() if not args.body_file or args.body_file == "-" else Path(args.body_file).read_text(encoding="utf-8-sig")
        return hubtools.BY_NAME[tool_name(fn)]["fn"](client, fields)
    if args.cmd == "assistant":
        from clients import hubtools
        if args.sub in ("read", "send"):
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
    identity = client.get("me")
    actor = identity["actor"]
    key = os.environ.get("HUB_OPERATION_ID")

    def post(path, body, suffix=""):
        result = client.post(path, body, key=(key + suffix) if key else None)
        return result["task"] if isinstance(result, dict) and set(result) == {"task"} else result

    def target(value):
        return actor if value in ("me", "self") else value

    def tool(client, args):
        """The goal, KPI and proposal commands run the MCP tool of the same name, so both say the same thing."""
        from clients import hubtools
        fields = {k: v for k, v in vars(args).items() if k not in ("cmd", "sub", "fn") and v is not None}
        fields["operation_id"] = key
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
            "refs": args.ref or [], "operation_id": key})
    if fn == "question answer":
        return post(f"messages/{args.message_id}/answer", {"text": args.text or args.unknown,
                    "unknown": bool(args.unknown)})
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
        payload = {"to": target(args.to), "text": text}
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
        if sub == "type":
            if args.type_sub == "delete":
                return post("task-types/" + args.id + "/delete", {})
            body = {}
            if args.name is not None:
                body["name"] = args.name
            if args.steps_file:
                body["steps"] = json.loads(Path(args.steps_file).read_text())
            if args.bots:
                body["bots"] = args.bots
            if args.numbered is not None:
                body["numbered"] = args.numbered
            return post("task-types" + ("/" + args.id if args.type_sub == "update" else ""), body)
        if sub == "types":
            if args.delete:
                if not args.id:
                    raise ValueError("Choose a type to delete")
                return post("task-types/" + args.id + "/delete", {})
            if args.name is not None or args.steps_file:
                body = {}
                if args.name is not None:
                    body["name"] = args.name
                if args.steps_file:
                    body["steps"] = json.loads(Path(args.steps_file).read_text())
                return post("task-types" + ("/" + args.id if args.id else ""), body)
            return client.get("task-types" + ("/" + args.id if args.id else ""))
        if sub == "create":
            body = Path(args.body_file).read_text() if args.body_file else args.body
            payload = {"owner": target(args.owner), "title": args.title, "body": body,
                       "due": args.due, "goal_id": getattr(args, "goal", None) or None}
            if args.parent:
                payload["relations"] = [{"task": args.parent, "kind": "parent"}]
            if getattr(args, "private", None) is not None:
                payload["private"] = args.private
            for field in ("type", "step", "number"):
                if getattr(args, field, None) is not None:
                    payload[field] = getattr(args, field)
            if args.label:
                payload["labels"] = [x.strip() for one in args.label for x in one.split(",") if x.strip()]
            if args.top:
                payload["top"] = True
            if args.link:
                payload["links"] = list(args.link)
            if getattr(args, "next_run", False):
                payload["next_run"] = True
            if getattr(args, "request_id", None):
                payload["request_id"] = args.request_id
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
        if sub == "list":
            return via_tool(client, args)
        if sub == "ask":
            return post(f"tasks/{args.id}/ask", {"text": args.text})
        if sub == "comment":
            from clients.task_review import ask_from_args, attachment
            ask = ask_from_args(args)
            references = []
            for index, path in enumerate(getattr(args, "attach", None) or []):
                made = post(f"tasks/{args.id}/files", attachment(path), suffix=f":attachment:{index}")
                references.append(f"{made['file_id']}@{made['version']}")
            body = {"text": args.text}
            if references:
                body["attachments"] = references
            if ask is not None:
                body["ask"] = ask
            links = s3links.apply(client, body, ("text",))
            return s3links.annotate(post(f"tasks/{args.id}/comments", body), links)
        if sub == "answers":
            return client.get(f"tasks/{args.id}/answers")
        if sub == "comment-edit":
            body = {"text": args.text}
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
            return post(f"tasks/{args.id}/links", {"url": args.url, "title": args.title})
        if sub == "relate":
            return post(f"tasks/{args.id}/relations", {"task": args.task, "kind": args.kind, "remove": args.remove})
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
            return post("tasks/" + args.id, {"version": current["version"], "labels": labels})
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
        if sub in ("update", "close"):
            current = client.get("tasks/" + args.id)["task"]
            body = {"version": current["version"], "note": args.note}
            if getattr(args, "quiet", False):
                body["quiet"] = True
            if sub == "close":
                body["close"] = True
            else:
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
            return s3links.annotate(post("tasks/" + args.id, body), links)
    if cmd == "goal":
        if sub == "create":
            body = Path(args.body_file).read_text() if args.body_file else args.body
            return post("goals", {"owner": target(args.owner), "title": args.title, "parent_id": args.parent,
                        "body": body or "", "top": bool(args.top)})["goal"]
        if sub == "update":
            body = {"title": args.title, "parent_id": args.parent, "owner": target(args.owner) if args.owner else None,
                    "rank": args.rank, "top": bool(args.top)}
            if args.body_file or args.body is not None:
                body["body"] = Path(args.body_file).read_text() if args.body_file else args.body
            return post("goals/" + args.id, body)["goal"]
        return tool(client, args)
    if cmd in ("kpi", "proposal"):
        return tool(client, args)
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
    if cmd == "routine":
        bot = getattr(args, "bot", None) or actor.split(":", 1)[-1]
        if sub == "list":
            return via_tool(client, args)
        if sub == "set":
            text = Path(args.text_file).read_text() if args.text_file else args.text
            return via_tool(client, args, text=text, enabled=not args.disabled)
        if sub in ("delete", "run"):
            return via_tool(client, args)
    if cmd == "approval":
        if sub == "show":
            return client.get("approvals/" + args.id)
        payload = json.loads(Path(args.payload_file).read_text() if args.payload_file else args.payload or "{}")
        return post("approvals", {"kind": args.kind, "payload": payload, "task_id": args.task})
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
    if args.fn == "bot assignment":
        action = args.assignment_action
        operation = os.environ.get("HUB_OPERATION_ID")
        if action == "list":
            return client.get(f"bots/{args.source}/assignment-branches")
        if action == "policy":
            return client.call("PUT", f"bots/{args.source}/assignment-branches/policy",
                               {"enabled": args.enabled, "expected_revision": args.revision}, key=operation)
        if action == "create":
            return client.post(f"bots/{args.source}/assignment-branches",
                               {"assignment_key": args.assignment_key, "generation": args.generation,
                                "task_id": args.task, "display_name": args.display_name}, key=operation)
        if action == "update":
            try:
                checkpoint = json.loads(args.checkpoint_json)
            except json.JSONDecodeError as exc:
                raise APIError("checkpoint", "--checkpoint-json must be a JSON object") from exc
            if not isinstance(checkpoint, dict):
                raise APIError("checkpoint", "--checkpoint-json must be a JSON object")
            reviewed_learning = ""
            if args.reviewed_learning_file:
                try:
                    reviewed_learning = Path(args.reviewed_learning_file).read_text(encoding="utf-8")
                except OSError as exc:
                    raise APIError("learning_note", f"Could not read reviewed learning file: {type(exc).__name__}") from exc
            if reviewed_learning.strip() and not args.confirm_learning_review:
                raise APIError("learning_review", "Review the lesson and pass --confirm-learning-review to publish it")
            if args.confirm_learning_review and not reviewed_learning.strip():
                raise APIError("learning_note", "--confirm-learning-review needs --reviewed-learning-file with content")
            body = {"expected_revision": args.revision, "phase": args.phase,
                    "display_name": args.display_name, "note": args.note, "checkpoint": checkpoint,
                    "deployed_version": args.deployed_version, "acceptance_receipt": args.acceptance_receipt,
                    "learning_receipt": args.learning_receipt, "evidence_receipt": args.evidence_receipt,
                    "handoff_task_id": args.handoff_task,
                    "reviewed_learning_note": reviewed_learning,
                    "confirm_learning_review": args.confirm_learning_review}
            return client.patch(f"assignment-branches/{args.assignment}", body, key=operation)
        if action == "cleanup":
            return client.post(f"assignment-branches/{args.assignment}/cleanup",
                               {"expected_revision": args.revision}, key=operation)
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
            listing = hubtools.computers(client, {})
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
                                    ("on_behalf_of", args.on_behalf_of), ("shared", args.shared), ("session", args.session)) if v is not None}
        try:
            return hubtools.BY_NAME["hub_bot_update"]["fn"](client, fields)
        except ValueError as exc:
            raise APIError("not_found", str(exc)) from None
    if args.fn in ("bot access", "bot mail", "bot owners", "bot setup-done", "bot place", "bot go-live", "bot model", "bot pause",
                   "bot resume", "bot restore", "bot archive") or (args.fn == "bot create" and args.record_only):
        from clients import hubtools
        fields = {k: v for k, v in vars(args).items()
                  if k not in ("cmd", "sub", "subsub", "fn", "no_setup", "record_only") and v not in (None, [])}
        if args.fn == "bot create" and args.record_only:
            fields["build"] = False
        if getattr(args, "no_setup", False):
            fields["setup"] = False
        if getattr(args, "routines_file", None):
            from clients.hubcli import read_routines_file
            fields["routines"] = read_routines_file(args.routines_file)
        fields["operation_id"] = os.environ.get("HUB_OPERATION_ID")
        return hubtools.BY_NAME[tool_name(args.fn)]["fn"](client, fields)
    if args.fn == "bot check":
        from clients.manifest import repo_dir
        problems = catalog.check(repo_dir(workspace, args.slug), args.slug)
        return {"ready": not problems, "problems": problems}
    if not args.template:
        raise APIError("usage", "hub bot create <slug> --template T builds the bot; --record-only only registers it")
    record = onboarding(client)
    chosen = (record.get("selected") or {}).get(args.slug) or {}
    registered = register_for_requester(client, args, chosen)
    path = catalog.materialize(args.template, args.slug, workspace, client.get("config"),
                               record.get("answers") or {},
                               display_name=args.name or chosen.get("display_name"),
                               instructions=chosen.get("instructions"))
    result = {"path": str(path), "template": args.template, "slug": args.slug,
              "committed": catalog.committed(path), "routines": seed_routines(client, args.slug, path),
              **({"registered": registered} if registered else {})}
    result["github"] = github_repository(client, args.slug, path)
    return result


def github_repository(client, slug, path):
    """Give the new bot its empty private `<org>/bot-<slug>` on GitHub; its runner publishes the local history into
    it. No GitHub connected (or a rehearsal) leaves the bot on its computer, as before. When the GitHub App may not
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
            return {"repository": "bot-" + slug, "existing": True, "note": exc.detail}
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
        return client.post("bots/register", {"slug": args.slug, "template": args.template, "on_behalf_of": "turn",
                                             "display_name": args.name or chosen.get("display_name") or ""})
    except APIError as exc:
        if exc.code == "on_behalf_of":
            return None
        raise


def seed_routines(client, slug, path):
    """The template's `routines:` (older: `schedules:`) become the new bot's first routines in the hub. The manifest
    is read once, here, with its playbooks already rendered; from now on the hub's rows are the
    routines and `hub routine set` changes them (docs/routines.md)."""
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
        seeded.append(client.post(f"bots/{slug}/routines", {
            "key": entry["id"], "title": entry["title"], "text": entry["instructions"],
            "cron": entry["cron"], "on": entry["on"], "timezone": entry["timezone"],
            "enabled": entry["enabled"]})["routine"]["id"])
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
    out += [f"  {n['created'][:10]}  {n['actor']}: {n['text']}" for n in notes] or ["  none yet"]
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
        elif args.fn == "bot go-live" and isinstance(result, dict) and result.get("state") == "waiting_for_repository":
            # Not an error and nothing to retry: Tico finishes it when the computer reports the repository.
            print(f"{result.get('bot')} is placed on {result.get('computer')} and waiting for it to get the repository. "
                  "Tico finishes going live (turns it on and starts its setup) when "
                  f"{result.get('computer')} has the repository; nothing needs to run again.")
        else:
            print(json.dumps(result, indent=2))
        if isinstance(result, dict) and result.get("warning"):     # saved, but a person should know why it waits
            print("hub: " + str(result["warning"]), file=sys.stderr)
        return 0
    except APIError as exc:
        if exc.code == "repository_waiting_for_permission":
            # Nothing to retry and nothing for the caller to build: the owner's Needs you task holds the fix.
            print(f"{exc.detail}.")
            return 2
        print(json.dumps({"error": exc.code, "detail": exc.detail, "retryable": exc.retryable,
                          "operation_id": exc.operation_id}, indent=2))
        return 1 if exc.retryable else 2
