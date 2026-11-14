"""Work on a task beside its lifecycle (comments, links, readers, runs, worktrees, caps), tags, feedback, replays, the roadmap and the General Manager.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import argparse
import base64
import json
from urllib.parse import quote
from pathlib import Path
from clients import s3links
from clients.cli_spec import Arg, Command, Group

from clients.commands._registry import tool
from clients.commands._schemas import DEFER_FOR, TASK_ID, _delivery, _key, _s
from clients.commands._shared import ASKED, VERSION, VISUALS, _seen, _uploaded, _visuals, text_file
from clients.commands._requester import _as_person, _asked


def _posted_file(api, path, name, data, key):
    """One file to a multipart route (a task's files): bytes as they are, up to the server's file limit."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="tico-visual-") as folder:
        local = Path(folder) / name
        local.write_bytes(data)
        return api.post_multipart(path, {"file": local}, {"name": name}, key=key)


FEEDBACK_SUBJECT = _s("What was rated", enum=["message", "task"])


@tool(
    "tico_feedback_give",
    "Rate a message or task a bot made, 👍 (up) or 👎 (down), with an optional reason (an ask: tico_task_thumbs). "
    "Rating again replaces your earlier one. tell_bot (a 👎 only) leaves the bot a note for its next run in that "
    "conversation. People only.",
    {
        "subject_type": FEEDBACK_SUBJECT,
        "subject_id": _s("Its id"),
        "rating": _s("up or down", enum=["up", "down"]),
        "reason": _s("Why, in a few words (at most 1000 characters)"),
        "tell_bot": {"type": "boolean", "default": False},
    },
    required=("subject_type", "subject_id", "rating"),
    writes=True,
)
def feedback_give(api, args):
    body = {
        k: args[k] for k in ("subject_type", "subject_id", "rating", "reason", "tell_bot") if args.get(k) is not None
    }
    return api.post("feedback", body, key=_key(args))


@tool(
    "tico_task_thumbs",
    "Was this ask worth asking: 👍 (up) or 👎 (down) on an ask a bot put to you, with an optional note saying what was "
    "wrong. A 👎 sends an open ask back to the bot with your words. For the person asked only.",
    {
        "id": _s("The task id"),
        "requirement": _s("The ask's requirement id"),
        "up": {"type": "boolean", "description": "true for 👍, false for 👎"},
        "note": _s("What was wrong, in a few words (at most 2000 characters)"),
    },
    required=("id", "requirement", "up"),
    writes=True,
)
def task_thumbs(api, args):
    body = {"up": bool(args["up"])}
    if args.get("note"):
        body["note"] = args["note"]
    path = f"tasks/{quote(args['id'], safe='')}/asks/{quote(args['requirement'], safe='')}/thumbs"
    return api.post(path, body, key=_key(args))


@tool(
    "tico_feedback_remove",
    "Remove your feedback; a note it left that no run carried yet is taken back.",
    {"id": _s("The feedback id")},
    required=("id",),
    writes=True,
)
def feedback_remove(api, args):
    return api.call("DELETE", "feedback/" + quote(args["id"], safe=""), None, key=_key(args))


@tool(
    "tico_feedback_markers",
    "Your own feedback on these subjects (at most 200 ids) and everyone's up/down counts, for the ones you may read.",
    {
        "subject_type": _s(
            "What was rated; an ask's id is <visit-id>:<requirement-id>", enum=["message", "task", "ask"]
        ),
        "subject_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 200, "description": "Their ids"},
    },
    required=("subject_type", "subject_ids"),
)
def feedback_markers(api, args):
    ids = args["subject_ids"]
    return api.get(
        "feedback", subject_type=args["subject_type"], subject_ids=ids if isinstance(ids, str) else ",".join(ids)
    )


@tool(
    "tico_feedback_list",
    "Feedback newest first, a page at a time, on what you may read. next_cursor reads the next page.",
    {
        "bot_id": _s("Only feedback on this bot's work (`bot:<slug>`)"),
        "rating": _s("up or down", enum=["up", "down"]),
        "since": _s("An ISO time, or 24h / 7d"),
        "cursor": _s("next_cursor of the page before"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
)
def feedback_list(api, args):
    return api.get("feedback/list", **{k: args.get(k) for k in ("bot_id", "rating", "since", "cursor", "limit")})


@tool("tico_feedback_reasons", "Your five latest distinct feedback reasons, each with how often you used it.", {})
def feedback_reasons(api, args):
    return api.get("feedback/recent-reasons")


@tool(
    "tico_feedback_rule_draft",
    "A one-sentence org rule drafted from your 👎's reason and the rated work. Stores "
    "nothing; drafted is false when no model drafted it and the reason came back as it is.",
    {"id": _s("Your feedback id")},
    required=("id",),
)
def feedback_rule_draft(api, args):
    return api.post("feedback/" + quote(args["id"], safe="") + "/rule-draft", {})


@tool(
    "tico_feedback_rule",
    "Make your 👎 an internal org rule for the bot, or every bot: active when you manage that "
    "Who, otherwise a suggestion.",
    {
        "id": _s("Your feedback id"),
        "text": _s("The rule, one sentence"),
        "all_bots": {"type": "boolean", "default": False, "description": "Every bot, not only the one rated"},
    },
    required=("id", "text"),
    writes=True,
)
def feedback_rule(api, args):
    body = {"text": args["text"], **({"all_bots": True} if args.get("all_bots") else {})}
    return api.post("feedback/" + quote(args["id"], safe="") + "/rule", body, key=_key(args))


# ----------------------------------------------------------------------------- tags
@tool(
    "tico_tag_list",
    "Tags and checklist templates. Task label strings are tag keys.",
    {"is_template": {"type": "boolean", "description": "Only templates (true) or task tags (false)"}},
)
def tag_list(api, args):
    value = args.get("is_template")
    return api.get("tags", is_template=str(value).lower() if value is not None else None)


@tool(
    "tico_tag_show",
    "A tag's metadata, markdown, version and the tasks you may read.",
    {"id": _s("Tag id or key"), "offset": {"type": "integer", "description": "Skip this many of its tasks"}},
    required=("id",),
)
def tag_show(api, args):
    return api.get("tags/" + quote(args["id"], safe=""), offset=args.get("offset"))


@tool(
    "tico_tag_create",
    "Create a tag or template. With template_id, copy its checklist and metadata defaults into an instance. "
    "The owner (default: you) and task movers may edit it; templates cannot go on tasks.",
    {
        "key": _s("Unique key: the label string a task carries"),
        "label": _s("Display label"),
        "metadata": {"type": "object", "description": "Metadata as a JSON object, or overrides of template defaults"},
        "markdown": _s("Markdown checklist or notes; on a command line, --markdown-file names a file that holds it"),
        "is_template": {"type": "boolean", "description": "Make it a template"},
        "template_id": _s("Template id or key"),
        "owner": _s("Owner actor or teammate id"),
    },
    required=("key",),
    writes=True,
)
def tag_create(api, args):
    body = {
        k: args[k] for k in ("key", "label", "metadata", "markdown", "is_template", "owner") if args.get(k) is not None
    }
    path = "tags/" + quote(args["template_id"], safe="") + "/instances" if args.get("template_id") else "tags"
    return _as_person(api).post(path, body, key=_key(args))


@tool(
    "tico_tag_update",
    "Edit a tag's metadata, checklist, label, owner or the bots that may set it. Send its current version; stale "
    "edits are refused.",
    {
        "id": _s("Tag id or key"),
        "version": {"type": "integer", "description": "Its current version, from `tag show`"},
        "label": _s("Display label"),
        "metadata": {"type": "object", "description": "Replace metadata: a JSON object"},
        "markdown": _s("Replace Markdown; on a command line, --markdown-file names a file that holds it"),
        "owner": _s("New owner; empty to clear"),
        "bot_writers": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 50,
            "description": "Bots that may attach and remove this tag on any task they can read; a task mover sets "
            "it, and an empty list revokes every grant",
        },
    },
    required=("id", "version"),
    writes=True,
)
def tag_update(api, args):
    fields = ("version", "label", "metadata", "markdown", "owner", "bot_writers")
    body = {k: args[k] for k in fields if args.get(k) is not None}
    return _as_person(api).post("tags/" + quote(args["id"], safe=""), body, key=_key(args))


@tool(
    "tico_task_worktree_add",
    "Create a task worktree from a repository this bot may write.",
    {"repo": _s("Repository owner/name"), "task": TASK_ID},
    required=("repo",),
    writes=True,
    local=True,
)
def task_worktree_add(api, args):
    from runner_support.worktrees import command

    return command(api, 'add', args['repo'], args.get('task'))


@tool(
    "tico_task_worktree_attach",
    "Attach a Git worktree inside the team workspace to this task.",
    {"path": _s("Path inside the team workspace"), "task": TASK_ID},
    required=("path",),
    writes=True,
    local=True,
)
def task_worktree_attach(api, args):
    from runner_support.worktrees import command

    return command(api, 'attach', args['path'], args.get('task'))


@tool(
    "tico_task_run",
    "Start a bot's task now, as the task (its text in the prompt, not a chat). "
    "For a person, the task's requester, or BotOps's sweep.",
    {"id": TASK_ID},
    required=("id",),
)
def task_run(api, args):
    return api.post(f"tasks/{args['id']}/run-now", {}, key=_key(args))


@tool(
    "tico_task_comment",
    "Leave a comment on a task: progress, a question for the people on it, "
    "a link to what you found. It is on the record with your name; it is not a chat.",
    {
        "id": TASK_ID,
        "text": _s("The comment"),
        "public": {"type": "boolean"},
        "ok": _s("A bot's public reply: the OK a person gave on exactly this text"),
        "attachments": {
            "type": "array",
            "items": {"type": "string"},
            "description": "File versions as file_id@version: on this task, or (a person) a chat file "
            "you can open as its id@1, which the comment shares to the task",
        },
        "visuals": VISUALS,
        "on_behalf_of": ASKED,
        "needs_action": {
            "type": "boolean",
            "default": False,
            "description": "To a bot: wake it now. Without this a bot's message to a bot waits for that "
            "bot's next run, whatever starts it",
        },
        "delivery": {
            "type": "string",
            "enum": ["steer", "queue", "defer", "goal"],
            "description": "How the comment reaches the task's bot. steer: into its running turn now (right "
            "after it if its harness can't steer). queue: right after its current turn on this task, or now. "
            "defer: wakes nobody; its next turn on this task reads it, at the latest after defer_for (24h). "
            "goal: make the text the goal of its task session (no attachments or visuals; not public, not a "
            "question). Omitted, the task's default applies.",
        },
        "defer_for": DEFER_FOR,
    },
    required=("id", "text"),
    writes=True,
)
def task_comment(api, args):
    body = {k: args[k] for k in ("text", "attachments", "public", "needs_action") if k in args}
    if args.get("ok"):
        body["approval_id"] = args["ok"]
    body.update({"delivery": "goal"} if args.get("delivery") == "goal" else _delivery(args))
    local = []
    if args.get("visuals"):
        added, local = _visuals(
            api,
            args["visuals"],
            lambda index, name, data: _uploaded(
                _posted_file(api, f"tasks/{args['id']}/files", name, data, _key(args, ":visual:" + index))
            ),
        )
        body["attachments"] = list(body.get("attachments") or []) + added
    links = s3links.apply(api, body, ("text",))
    result = s3links.annotate(_asked(api, args).post(f"tasks/{args['id']}/comments", body, key=_key(args)), links)
    return {**result, "visuals_local": local} if local and isinstance(result, dict) else result


COMMENT_ID = _s("The comment's id (`id` in the task's `comments`)")


@tool(
    "tico_task_comment_edit",
    "Change the text of a comment you wrote on a task. It wakes nobody and is not sent again; it shows as edited.",
    {
        "id": TASK_ID,
        "comment_id": COMMENT_ID,
        "text": _s("The new text"),
        "ok": _s("The OK a person gave on exactly this public text"),
    },
    required=("id", "comment_id", "text"),
    writes=True,
)
def task_comment_edit(api, args):
    body = {"text": args["text"]}
    if args.get("ok"):
        body["approval_id"] = args["ok"]
    links = s3links.apply(api, body, ("text",))
    return s3links.annotate(api.post(f"tasks/{args['id']}/comments/{args['comment_id']}", body, key=_key(args)), links)


@tool(
    "tico_task_comment_delete",
    "Take back a comment you wrote on a task: it is no longer listed or handed to a bot.",
    {"id": TASK_ID, "comment_id": COMMENT_ID},
    required=("id", "comment_id"),
    writes=True,
)
def task_comment_delete(api, args):
    return api.post(f"tasks/{args['id']}/comments/{args['comment_id']}/delete", {}, key=_key(args))


@tool(
    "tico_task_delete",
    "Delete a task made by mistake, such as a duplicate: it goes to the trash and can be "
    "restored. Only a person signed in as themselves may: the task's requester, or someone who may move any "
    "task. It deletes whatever bots did on it; that work stays in place, hidden until a restore.",
    {"id": TASK_ID},
    required=("id",),
    writes=True,
)
def task_delete(api, args):
    return api.post(f"tasks/{args['id']}/delete", {}, key=_key(args))


@tool("tico_task_deleted", "Deleted tasks you may restore, newest first. A person only.", {})
def task_deleted(api, args):
    return api.get("deleted-tasks")


@tool(
    "tico_task_restore",
    "Put a deleted task back with its conversation, comments, links and number: whoever "
    "deleted it, its requester, or someone who may move any task. A person only.",
    {"id": TASK_ID},
    required=("id",),
    writes=True,
)
def task_restore(api, args):
    return api.post(f"tasks/{args['id']}/restore", {}, key=_key(args))


@tool(
    "tico_task_label",
    "Add or remove labels on a task. A project is a label; so is a kind (bug, front-end). A bot "
    "changes labels on a task it owns or asked for; goal:on/goal:off need a person. It only adds or takes off the "
    "labels named, so the version is optional: given, a task that changed since you were shown it is refused; left "
    "out, the labels are read and changed at the version read, and a change made in between is refused, never lost.",
    {
        "id": TASK_ID,
        "add": {"type": "array", "items": {"type": "string"}, "description": "Labels to add"},
        "remove": {"type": "array", "items": {"type": "string"}, "description": "Labels to remove"},
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id",),
    writes=True,
)
def task_label(api, args):
    current = api.get("tasks/" + args["id"])["task"]  # the labels it adds to or takes from
    # The version the caller saw, when given; else the one just read, which guards these labels against a change
    # made since the read (task_changed: read again and run it again).
    version = args.get("version") if args.get("version") not in (None, "") else current.get("version")
    labels = [x for x in current.get("labels") or []]
    drop = {str(x).strip().lower() for x in args.get("remove") or []}
    labels = [x for x in labels if x not in drop]
    for x in args.get("add") or []:
        x = str(x).strip().lower()
        if x and x not in labels:
            labels.append(x)
    return _asked(api, args).post("tasks/" + args["id"], {"version": version, "labels": labels}, key=_key(args))


@tool(
    "tico_task_link",
    "Attach a link to a task: the pull request you opened, an issue, a document, a page. `kind: session` links "
    "another session working the task (a thread URL, a resume command); it is tracked only, task chat "
    "goes to the owning bot.",
    {
        "id": TASK_ID,
        "url": _s("The URL; for a session also a resume command or a reference"),
        "title": _s("A short name; a pull request needs none"),
        "kind": {"type": "string", "enum": ["session"], "description": "session: another session on this task"},
        "wake_once": {
            "type": "boolean",
            "description": "A pull request: true wakes this task once when its checks "
            "finish (pass or fail), it is approved or gets changes requested, or a release ships it, so "
            "you can end the turn instead of polling. It never changes the task's status. false disarms it",
        },
        "on_behalf_of": ASKED,
    },
    required=("id", "url"),
    writes=True,
)
def task_link(api, args):
    return _asked(api, args).post(
        f"tasks/{args['id']}/links",
        {
            "url": args["url"],
            "title": args.get("title"),
            **({"kind": "session"} if args.get("kind") == "session" else {}),
            **({"wake_once": args["wake_once"]} if args.get("wake_once") is not None else {}),
        },
        key=_key(args),
    )


@tool(
    "tico_task_unlink",
    "Take a link off a task: a pull request closed without merging that another PR replaces, so PR open reads the "
    "one that is left. Name the link by its URL or its id.",
    {"id": TASK_ID, "link": _s("The link's URL, or its id"), "on_behalf_of": ASKED},
    required=("id", "link"),
    writes=True,
)
def task_unlink(api, args):
    link = str(args["link"]).strip()
    if "://" in link:
        shown = api.get(f"tasks/{args['id']}/links")
        found = [
            row.get("id")
            for row in (shown.get("links") if isinstance(shown, dict) else None) or []
            if str(row.get("url") or "").rstrip("/").lower() == link.rstrip("/").lower()
        ]
        if not found:
            raise ValueError(f"No link to {link} on this task. tico task show {args['id']} lists its links.")
        link = str(found[0])
    return _asked(api, args).call("DELETE", f"tasks/{args['id']}/links/{quote(link, safe='')}", {}, key=_key(args))


READERS = {
    "type": "array",
    "items": {"type": "string"},
    "minItems": 1,
    "description": "human:<id>, bot:<slug> or group:<id>",
}


@tool(
    "tico_task_readers_list",
    "Who can see a task: its requester, owner and readers; everyone for a company task.",
    {"id": TASK_ID},
    required=("id",),
)
def task_readers_list(api, args):
    return api.get(f"tasks/{args['id']}/readers")


@tool(
    "tico_task_readers_add",
    "Let people, bots or groups see a private task (a group: whoever is in it now).",
    {"id": TASK_ID, "readers": READERS},
    required=("id", "readers"),
    writes=True,
)
def task_readers_add(api, args):
    return api.post(f"tasks/{args['id']}/readers", {"add": list(args["readers"])}, key=_key(args))


@tool(
    "tico_task_readers_remove",
    "Take readers off a private task.",
    {"id": TASK_ID, "readers": READERS},
    required=("id", "readers"),
    writes=True,
)
def task_readers_remove(api, args):
    return api.post(f"tasks/{args['id']}/readers", {"remove": list(args["readers"])}, key=_key(args))


FOLLOW_LEVEL = {"type": "string", "enum": ["live", "mentions", "digest", "muted"]}


@tool(
    "tico_task_follow",
    "Follow a task, set your level, or unfollow. `who`: someone else, if you may change the task.",
    {"id": TASK_ID, "level": FOLLOW_LEVEL, "who": _s("human:<id> or bot:<slug>"), "unfollow": {"type": "boolean"}},
    required=("id",),
    writes=True,
)
def task_follow(api, args):
    body = {
        "following": not args.get("unfollow"),
        **({"notify": args["level"]} if args.get("level") else {}),
        **({"actor": args["who"]} if args.get("who") else {}),
    }
    return api.call("PUT", f"tasks/{args['id']}/followers", body, key=_key(args))


@tool(
    "tico_task_attach",
    "Attach a deliverable (a report, a draft) to a task so the people on it "
    "can open it in Tico. Returns the link to put in your note; never link a path on your "
    "Mac or an s3:// URI. Send the text as-is, or content_base64 for a binary file. "
    "With req and page instead, attach a page to the stage's page requirement: Jev checks it, then the person it "
    "names OKs that version. Attaching again replaces it. With a new version after a send back, say what changed "
    "(changed: up to 5 one-line bullets) and what you did with each of their points (answered: "
    "\"<point>: done|not done, <why>\"); never quote their note back to them.",
    {
        "id": _s("Task id"),
        "req": _s("The page requirement's name or id, e.g. Spec page"),
        "page": _s("page:<file id> for its newest version, or page:<file id>@<version>"),
        "version": VERSION,
        "name": _s("File name with its extension, e.g. 2026-09-15-draft-review.md"),
        "path": _s("A local file to stream when this server supports multipart uploads"),
        "poster": _s("Optional local PNG or JPEG poster with path"),
        "text": _s("The file's text, for a text or Markdown file"),
        "content_base64": _s("The file's bytes, base64-encoded, for anything that is not text"),
        "note": {"type": "string", "maxLength": 500},
        "changed": {
            "type": "array",
            "items": {"type": "string", "maxLength": 120},
            "maxItems": 5,
            "description": "With a page: what changed in this version, up to 5 one-line bullets",
        },
        "answered": {
            "type": "array",
            "items": {"type": "string", "maxLength": 300},
            "maxItems": 12,
            "description": 'With a page: each point of the last send back, as "<point>: done|not done, <why>"',
        },
    },
    required=("id",),
    writes=True,
)
def task_attach(api, args):
    if args.get("req") or args.get("page"):
        if not (args.get("req") and args.get("page")):
            raise ValueError("A page requirement takes both req and page, e.g. req: Spec page, page: page:file-…")
        body = {"requirement": args["req"], "page": args["page"], "version": _seen(args)}
        for field in ("note", "changed", "answered"):
            if args.get(field):
                body[field] = args[field]
        return _asked(api, args).post(f"tasks/{args['id']}/pages", body, key=_key(args))
    if not args.get("name"):
        raise ValueError("Say the file's name (name), or attach a page with req and page")
    body = {"name": args["name"]}
    if args.get("path"):
        if not hasattr(api, "post_multipart"):
            raise ValueError("Stream local files with tico task attach on your Computer")
        from clients import bot_files as BF

        path, _ = BF.local_file(BF.checkout_root(), args["path"])
        fields = {k: args[k] for k in ("name", "note") if args.get(k) is not None}
        if api.features().get("task_files_multipart"):
            uploads = {"file": path}
            if args.get("poster"):
                uploads["poster"], _ = BF.local_file(BF.checkout_root(), args["poster"])
            return api.post_multipart(f"tasks/{args['id']}/files", uploads, fields, key=_key(args))
        if path.stat().st_size > 10_000_000:
            raise ValueError("This server accepts files up to 10 MB; upgrade it for streaming uploads")
        with path.open("rb") as source:
            data = source.read(10_000_001)
        if len(data) > 10_000_000:
            raise ValueError("This server accepts files up to 10 MB")
        body["content_base64"] = base64.b64encode(data).decode()
    for field in ("text", "content_base64", "note"):
        if args.get(field) is not None:
            body[field] = args[field]
    return api.post(f"tasks/{args['id']}/files", body, key=_key(args))


@tool(
    "tico_task_caps",
    "Read the task caps: how many tasks each teammate may create for another per day, with "
    "today's count, the tasks closed before the receiver acted, and the refused creates. Also the changes bots asked "
    "for, which a person approves or declines as approvals. A cap is off until someone sets one.",
    {},
)
def task_caps_read(api, args):
    return _as_person(api).get("task-caps")


@tool(
    "tico_task_cap_set",
    "Set how many tasks `sender` may create for `receiver` each day (teammate refs: bot:<slug> or "
    "person:<id>). The receiver must be a manager above the sender at any level. per_day 0 blocks it; null removes the cap. "
    "A bot's own change waits as an approval a person decides.",
    {
        "sender": _s("bot:<slug> or person:<id>"),
        "receiver": _s("bot:<slug> or person:<id>, a manager above the sender"),
        "per_day": {"type": ["integer", "null"], "minimum": 0, "description": "Tasks a day; null removes the cap"},
    },
    ("sender", "receiver", "per_day"),
    writes=True,
)
def task_cap_set(api, args):
    body = {"sender": args["sender"], "receiver": args["receiver"], "per_day": args["per_day"]}
    return _as_person(api).call("PUT", "task-caps", body, key=_key(args))


@tool(
    "tico_general_manager",
    "The General Manager: whether the helper bot is on, the hourly report's switch and schedule, its last run "
    "(\"3 departments, 7 items\" or \"Nothing to do\"), and each department's last check and open check task.",
    {},
)
def general_manager_status(api, args):
    return _as_person(api).get("general-manager")


@tool(
    "tico_general_manager_set",
    "The General Manager: switch its hourly report on or off, pick the schedule (business = hourly on "
    "weekdays 08:00 to 18:00, hourly, 4h, daily; `at` is the daily time), or turn the helper bot itself on or off. The owner "
    "or an admin; the bot's own off switch is the owner's.",
    {
        "on": {"type": "boolean", "description": "The hourly report on or off"},
        "preset": {"type": "string", "enum": ["business", "hourly", "4h", "daily"]},
        "at": _s("HH:MM for the daily preset"),
        "bot": {
            "type": "string",
            "enum": ["on", "off"],
            "description": "Turn the General Manager bot itself on or off",
        },
    },
    writes=True,
)
def general_manager_set(api, args):
    api, out = _as_person(api), {}
    if args.get("bot"):
        out["bot"] = api.post("general-manager/turn-" + args["bot"], {}, key=_key(args, ":bot"))
    body = {k: args[k] for k in ("on", "preset", "at") if args.get(k) is not None}
    if body:
        out["job"] = api.call("PUT", "system-routines/task_manager_report", body, _key(args))["job"]
    if not out:
        raise ValueError("name something to change: --on/--off, --preset, --at or --bot")
    return out


@tool(
    "tico_general_manager_run",
    "The General Manager: run the department check once now (the owner or an admin). It opens a check task "
    "per department only when there is something to look at; 409 while the report is off.",
    {},
    writes=True,
)
def general_manager_run(api, args):
    return _as_person(api).call("POST", "system-routines/task_manager_report/run", {}, _key(args))


REPLAY_WINDOW = {
    "from": _s("Start, ISO 8601 with a zone (2026-10-10T00:00:00Z); default 24 hours before `to`"),
    "to": _s("End, ISO 8601 with a zone; default now. At most 31 days after `from`"),
    "bot": _s("Only what involves this bot (a slug you may Read)"),
    "person": _s("Only what involves this person (a person id)"),
    "team": _s("Only what involves this team and the teams under it (a team id)"),
}


def _replay(api, args):
    from datetime import datetime, timedelta, timezone

    end = args.get("to") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = args.get("from")
    if not start:
        at = datetime.fromisoformat(end.replace("Z", "+00:00"))
        start = (at - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return api.get(
        "replay",
        **{"from": start, "to": end, "bot": args.get("bot"), "person": args.get("person"), "team": args.get("team")},
    )


@tool(
    "tico_replay_show",
    "What happened in a window, in time order, as you may see it: tasks made and moved, chat "
    "messages and task comments, runs and what they said, bot status changes, goal colours. The input for a recap. "
    "Returns {from, to, items: [{at, kind, who, task, text, ...}], truncated, next, start: {tasks, bots}, names}; "
    "`names` maps every actor shown to a display name.",
    REPLAY_WINDOW,
)
def replay_show(api, args):
    return _replay(api, args)


@tool(
    "tico_roadmap_tasks",
    "Rank roadmap tasks by requester count, external requests, important requests, organization or size.",
    {
        "sort": _s("Sort", enum=['requester_count', 'external_requesters', 'important', 'organization', 'size']),
        "direction": _s("Direction", enum=['asc', 'desc']),
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
    },
)
def roadmap_tasks(api, args):
    return api.get('roadmap-tasks', **{k: args[k] for k in ('sort', 'direction', 'limit', 'offset') if k in args})


@tool(
    "tico_roadmap_settings",
    "Read or partially update company public roadmap settings. Only owners and admins change them. "
    "Labels must already exist. Empty labels disables public roadmaps. Bot public edits need approval by default. "
    "The read says which bot suggestions go to now (suggestions_receiver).",
    {
        "settings": {
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"type": "string"}},
                "cors_origins": {"type": "array", "items": {"type": "string"}},
                "action_limit": {"type": "integer", "minimum": 1},
                "suggestion_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Visitor suggestions waiting at Suggested, overall",
                },
                "bot_public_approval": {"type": "boolean"},
                "suggestions_bot": {
                    "type": ["string", "null"],
                    "description": "Slug of the active bot that receives visitor suggestions; null for Automatic",
                },
            },
            "additionalProperties": False,
        }
    },
    writes=True,
)
def roadmap_settings(api, args):
    if args.get('settings') is not None:
        return _as_person(api).post('roadmaps/settings', args['settings'], key=_key(args))
    return api.get('roadmaps/settings')


def _metadata(text):
    """A flag's type: tag metadata, a JSON object, or a usage error."""
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not JSON ({exc})") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("Tag metadata must be a JSON object")
    return value


_TAG_TEXT = (
    Arg("metadata", type=_metadata, metavar="JSON"),
    Arg("markdown", "--markdown"),
    Arg("markdown", "--markdown-file", type=text_file),
)

# Tags keep the task --label key contract: a task's label string is a tag's key.
TAG = (
    Group("tag", "tags and checklist templates"),
    Command(
        "tag list",
        "tags and checklist templates",
        args=(
            Arg("is_template", "--templates", action="store_const", const=True),
            Arg("is_template", "--instances", action="store_const", const=False),
        ),
        example="tico tag list --templates",
    ),
    Command("tag show", "a tag with the tasks that carry it", example="tico tag show launch"),
    Command(
        "tag create",
        "a tag, a checklist template, or an instance of a template",
        args=(
            *_TAG_TEXT,
            Arg("is_template", "--template", action="store_const", const=True),
            Arg("template_id", "--from-template"),
        ),
        example="tico tag create launch-q4 --from-template launch",
    ),
    Command(
        "tag update",
        "edit a tag's label, metadata, checklist or owner",
        args=(Arg("version", "--version"), *_TAG_TEXT),
        example="tico tag update launch --version 2 --label Launch",
    ),
)
