"""Tasks: create, list, show, move, done, close, asks and answers, OKs.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import os
from urllib.parse import quote
from clients import s3links
from clients.cli_spec import NULL, Arg, Command, Group

from clients.commands._registry import tool
from clients.commands._schemas import STATUSES, TASK_ID, _key, _s, _target
from clients.commands._shared import ASKED, VERSION, _model_id, _seen
from clients.commands._requester import ON_BEHALF_OF, _as_person, _asked


@tool(
    "tico_task_meetings",
    "Linked meetings you may open, newest first. Follow next_cursor for more.",
    {"id": TASK_ID, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "cursor": _s("Previous next_cursor")},
    required=("id",),
)
def task_meetings(api, args):
    return _as_person(api).get(f"tasks/{args['id']}/meetings", **{k: args[k] for k in ('limit', 'cursor') if k in args})


@tool(
    "tico_task_context",
    "Task page and meeting references, up to 10 each. Fetch content with tico_file_read or tico_meeting_read.",
    {"id": TASK_ID},
    required=("id",),
)
def task_context(api, args):
    return api.get(f"tasks/{args['id']}/context")


# ----------------------------------------------------------------------------- tasks
@tool(
    "tico_task_create",
    "File a task for a bot or a person. A task for a person is a decision or a review: the title says what you "
    "ask, the message is under 120 words, and its first paragraph is the ask: one or two plain sentences on what "
    "you need and what happens after, at most 40 words, with no ids, hashes, file paths, s3:// links or tool names "
    "(\"PR #1555\" is fine; refused otherwise), e.g. \"Approve the video tool trial for Thursday? If yes, I'll send "
    "the invite.\" Details go below. It starts in the first stage of its pipeline (General when none is named). "
    "`dry_run` reports the checks a create would fail and writes nothing. A person’s asking criteria can refuse "
    "the write as ask_refused, with the reason and fix; no task is created. To ask a person on your own "
    "task, use tico_task_ask. Access: tico_access_request.",
    {
        "owner": _s(
            "Who does it: a bot slug, a person id, or `me`. Left out, a pipeline's first stage that picks from several "
            "picks one"
        ),
        "title": _s("Task title: plain words, no reference numbers or all-caps"),
        "message": _s("The first message on its chat: what you ask, if the title doesn't say it all", default=""),
        "pipeline": _s("The pipeline's id; default general"),
        "visibility": _s(
            "People only: roadmap visibility at creation; public needs public_title. A bot is refused; "
            "it creates the task, then requests a publication",
            enum=["public", "private"],
        ),
        "public_title": _s("People only (a bot is refused): public title; the internal title is never published"),
        "public_summary": _s("People only (a bot is refused): public summary; the task's chat is never published"),
        "due": _s("ISO-8601 date-time with timezone"),
        "private": {
            "type": "boolean",
            "description": "true: only its requester, owner and readers see it. false: company-visible, also from a "
            "run on a private task or under a private parent. Left out: a private task's run files it private; "
            "bot defaults also apply",
        },
        "readers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Private: who else sees it: human:<id>, bot:<slug> or group:<id>",
        },
        "follow": {"type": "boolean"},  # the filer follows it too (docs/tasks.md, Followers)
        "for_actor": _s(
            "A bot filing a person's own ask: the person (human:<id>) it is for, who becomes its requester. "
            "With from_message"
        ),
        "from_message": _s("With for_actor: the id of the message that person asked in; you must be able to read it"),
        "parent": _s(
            "The task that files it (its id, or 8-character short id): the parent waits until this one ends. "
            "Set once, never changed"
        ),
        "request_id": _s(
            "A person's message this task carries: Agent Ops continuing its own chat, or another bot "
            "handing Agent Ops a person's request (their message id, or `turn` for the one that "
            "started this run). Agent Ops then acts with that person's rights, or puts it to them as "
            "a Confirm card"
        ),
        "number": {
            "type": "integer",
            "minimum": 1,
            "description": "Movers: keep an imported ticket's number (unique across the team)",
        },
        "labels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Labels: a project name, a kind (bug, front-end). Lower-case words.",
        },
        "top": {
            "type": "boolean",
            "default": False,
            "description": "Put it at the top of the owner's queue instead of the bottom",
        },
        "links": {
            "type": "array",
            "items": {"type": "string"},
            "description": "URLs to attach: a pull request, an issue, a document",
        },
        "goal_id": _s("The goal this task serves (tico_goal_list); optional"),
        "human": _s("Its Human: a person or `me`"),
        "after": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tasks it merges after (ids or 8+ leading characters): its PRs queue in Merge only once "
            "each has merged; build and review go ahead",
        },
        "roles": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
            "description": "Who is on it by role, {role: [people or bots]}, e.g. reviewer: each role named is "
            "replaced ([] clears it), others stay",
        },
        "model": _s(
            "A bot-owned task's own model, from tico_bot_model's list: its runs use it, not the bot's task model"
        ),
        "effort": _s("Reasoning effort that model takes (with model)"),
        "harness": _s("A harness from that model's harnesses list (with model)"),
        "dry_run": {"type": "boolean", "default": False},
    },
    required=("title",),
    writes=True,
)
def task_create(api, args):
    body = {
        **({"owner": _target(api, args["owner"])} if args.get("owner") else {}),
        "title": args["title"],
        **({"message": said} if (said := args.get("message") or args.get("body")) else {}),
        "due": args.get("due"),
        "goal_id": args.get("goal_id") or None,
        **({"human": _target(api, args["human"])} if args.get("human") else {}),
    }
    for field in (
        "pipeline",
        "parent",
        "labels",
        "top",
        "links",
        "request_id",
        "number",
        "check_model",
        "check_role",
        "visibility",
        "public_title",
        "public_summary",
        "for_actor",
        "from_message",
        "model",
        "effort",
        "harness",
        "readers",
        "follow",
    ):
        if args.get(field) not in (None, "", [], False):
            body[field] = args[field]
    if args.get("private") is not None:
        body["private"] = args["private"]
    after = _after(args)
    if after:
        body["relations"] = [{"task": other, "kind": "after"} for other in after]
    roles = _roles(api, args)
    if roles:
        body["roles"] = roles
    if args.get("dry_run"):
        return api.post("tasks/dry-run", body)
    links = s3links.apply(api, body, ("message",))
    return s3links.annotate(api.post("tasks", body, key=_key(args)), links)


@tool(
    "tico_task_child_create",
    "Create a subtask under a parent; you are its requester. With `check_model`, the subtask is "
    "your own and runs on that model in a session of its own, reading only the subtask: name in "
    "its message the PR or files to look at. Mark it done with the verdict as the note; it comes "
    "back to the parent.",
    {
        "parent": TASK_ID,
        "owner": _s("Bot slug or human id; `me` for a check_model subtask (the default)"),
        "title": _s("What to do"),
        "message": _s("The first message on its chat", default=""),
        "check_model": _s(
            "A company-enabled runtime, optionally with a model: `grok`, `codex:gpt-6.2-sol`; `auto` for "
            "the company's check model, from a provider other than the parent's author when one can run it"
        ),
        "check_role": {
            "type": "string",
            "enum": ["review", "check"],
            "description": "review files a normal review child; with check_model, review work or check a claim",
        },
    },
    required=("parent", "title"),
    writes=True,
)
def task_child_create(api, args):
    owner = args.get("owner") or ("me" if args.get("check_model") else None)
    if not owner:
        raise ValueError("owner is required (or check_model, which files the subtask for you)")
    return task_create(api, {**args, "owner": owner})


@tool("tico_task_tree", "The nested subtasks with status, owner and worst PR state.", {"id": TASK_ID}, required=("id",))
def task_tree(api, args):
    return api.get(f"tasks/{args['id']}/tree")


WHY = _s("Why, in one plain sentence. A bot always says why")


def _why(args, field="why"):
    return {field: args[field]} if args.get(field) not in (None, "") else {}


def _who(actor, actors=None):
    """An actor as people read it: its name when the server sends one (in the actor, or in the answer's `actors`
    map, backend/names.py), else its id."""
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("id") or "")
    return str((actors or {}).get(actor) or actor or "")


def _ref(task):
    return f"#{task['number']}" if task.get("number") else str(task.get("id") or "")


def turn_line(turn, actors=None):
    """The headline wait in one line: who has the turn and why, "Builder (stopped early, try 2 of 3)". A wait on a
    task this one filed names that task, and its id last, for `tico task show`."""
    if not turn:
        return ""
    who, line = turn.get("who"), str(turn.get("line") or "")
    if turn.get("state") == "pick":  # a stage that picks from several has nobody free: "Waiting for a free Reviewer"
        return line or "Waiting for a free bot"
    if str(turn.get("requirement_id") or "").startswith("merge_order:"):  # it merges after a task: the line names it
        return f"{line} ({who})" if who else line
    if isinstance(who, str) and who and ":" not in who:  # a task's id: the line is its title
        return f"waits on {line or 'a filed task'} ({who})" + (f", until {turn['until']}" if turn.get("until") else "")
    head = _who(who, actors) or str(turn.get("state") or "")
    text = f"{head} ({line})" if head and line else head or line
    return text + (f", until {turn['until']}" if turn.get("until") else "")


def task_text(task, actors=None, events=()):
    """`tico task show` as a bot reads it: the version; pipeline, stage and owner; whose turn it is; what it takes to
    leave the stage, each requirement met or not with its id; the next stage; the task that filed it; who each stage
    that picks from several gave it to (`events` of field `pick`). People are named by `actors` (the answer's
    {id: name}, backend/names.py) where it names them."""
    stage = task.get("stage") or {}
    lines = [f"{_ref(task)} {task.get('title') or ''}   version {task.get('version')}"]
    owner = task.get("owner_name") or _who(task.get("owner_id") or task.get("owner"), actors)
    place = [(task.get("pipeline") or {}).get("name"), stage.get("name"), f"owner {owner}" if owner else None]
    lines.append(" · ".join(p for p in place if p))
    asked = task.get("request") if isinstance(task.get("request"), dict) else None
    if asked and asked.get("text"):
        # What it asks: its first chat message (a task has no description); the rest of its chat says more.
        said = " ".join(str(asked["text"]).split())
        lines.append(
            f"Asked by {_who(asked.get('from'), actors) or 'someone'}: {said[:600]}" + ("…" if len(said) > 600 else "")
        )
    if task.get("status") not in (None, "open"):
        lines.append(f"Status: {task['status']}")
    if task.get("turn"):
        lines.append("Turn: " + turn_line(task["turn"], actors))
    repos = [str(r) for r in task.get("repositories") or []]
    if repos:
        # Where the work is (backend/stage_inputs.repositories): `tico task worktree add <one>` makes a worktree of it.
        lines.append(("Repository: " if len(repos) == 1 else "Repositories: ") + ", ".join(repos))
    late = task.get("late")
    if late:
        lines.append(f"Late since {late.get('since')}" + (", with a person" if late.get("level") == "person" else ""))
    to_leave = task.get("to_leave") or []
    lines.append(f"To leave {stage.get('name') or 'this stage'}:" + ("" if to_leave else " nothing"))
    for item in to_leave:
        words = [f"  {'met' if item.get('met') else 'not met':<8} {item.get('line') or item.get('kind') or ''}"]
        if not item.get("met") and item.get("standing"):
            words.append(str(item["standing"]))  # how it stands: "Sent back: ...", "No task filed yet"
        if not item.get("met") and _who(item.get("who"), actors):
            words.append(f"(waits on {_who(item['who'], actors)})")
        answer = item.get("answer")
        if answer:
            words.append(f"answer: {answer.get('value')}" + (f", {answer['note']}" if answer.get("note") else ""))
        words.append(f"id {item.get('requirement_id')}")
        if item.get("may"):
            words.append("you may: " + ", ".join(item["may"]))
        lines.append("  ".join(words))
    inputs = [i for i in task.get("inputs") or [] if isinstance(i, dict)]
    if inputs:
        # What earlier stages handed on (backend/stage_inputs.py): the PR, each earlier page, what was said at done.
        lines.append("From earlier stages:")
        for entry in inputs:
            who = _who(entry.get("by"), actors)
            line = entry.get("line") or entry.get("label") or ""
            lines.append(f"  {line}" + (f" ({who})" if who else ""))
    following = task.get("next")
    lines.append("Next: " + ((following or {}).get("name") or "none"))
    if task.get("filed_by"):
        lines.append(f"Filed by: {task['filed_by']}")
    for filed in task.get("filed") or []:
        title = filed.get("title") or "(not yours to read)"
        lines.append(f"Filed: {filed.get('id')} {title} ({filed.get('status')})")
    lines += order_lines(task)
    # Every pick is written on the task's history: "Build given to Builder 2 · took turns".
    lines += [
        f"Picked: {e['note']}"
        for e in events or ()
        if isinstance(e, dict) and e.get("field") == "pick" and e.get("note")
    ]
    return "\n".join(lines)


def order_lines(task):
    """The tasks it merges after and before (relations of kind `after`): "After #40 add the B6 line · Merge"."""
    out = []
    for entry in (task.get("relations") or {}).get("after") or []:
        word = "After" if entry.get("direction") == "out" else "Before"
        where = entry.get("stage") if entry.get("status") == "open" else entry.get("status")
        ref = f"#{entry['number']}" if entry.get("number") else str(entry.get("id") or "")[:8]
        out.append(
            f"{word} {ref} {entry.get('title') or ''}" + (f" · {where}" if where else "") + f" ({entry.get('id')})"
        )
    return out


@tool(
    "tico_task_show",
    "One task: its version; pipeline, stage and owner; whose turn it is; what it takes to leave "
    "the stage (each requirement, met or not, with its id and what you may do with it); the next "
    "stage; the task that filed it. `text` says it in lines. Send its version with every change "
    "you make. For several tasks, tico_task_show_many reads them in one call.",
    {"id": TASK_ID},
    required=("id",),
)
def task_show(api, args):
    shown = api.get("tasks/" + args["id"])
    if not isinstance(shown, dict):
        return shown
    task = shown.get("task") if isinstance(shown.get("task"), dict) else shown
    return {**shown, "text": task_text(task, shown.get("actors"), shown.get("events") or ())}


@tool(
    "tico_task_show_many",
    "Several tasks in one call (up to 100): each task's fields, without history or conversation; "
    "`missing` lists the ids that are unknown or not yours to read. Use this instead of one "
    "tico_task_show per task.",
    {"ids": {"type": "array", "items": TASK_ID, "minItems": 1, "maxItems": 100}},
    required=("ids",),
)
def task_show_many(api, args):
    ids = [str(i).strip() for i in args["ids"] if str(i).strip()]
    return api.get("tasks", ids=",".join(ids))


@tool(
    "tico_task_done",
    "Say the work of this stage is finished, with what was done. It never pushes past anything: "
    "while something else is missing it is refused with what is missing and the commands you may "
    "run instead. In the last stage the task is Done.",
    {
        "id": TASK_ID,
        "note": _s("What was done, in a few plain sentences"),
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id",),
    writes=True,
)
def task_done(api, args):
    body = {"version": _seen(args), **_why(args, "note")}
    links = s3links.apply(api, body, ("note",))
    return s3links.annotate(_asked(api, args).post(f"tasks/{args['id']}/done", body, key=_key(args)), links)


def _from_stage(args, what):
    """`{"from": {pipeline, stage}}` when the tool names a stage's open tasks (from_pipeline + from_stage) in place of
    one task's id; None when it names one task. `what` says the write ("Moving") in a refusal."""
    if not (args.get("from_pipeline") or args.get("from_stage")):
        if not args.get("id"):
            raise ValueError("Say which task: id (or from_pipeline and from_stage for a whole stage)")
        return None
    if not (args.get("from_pipeline") and args.get("from_stage")):
        raise ValueError(f"{what} a stage's tasks needs from_pipeline and from_stage")
    if args.get("id"):
        raise ValueError("Give id, or from_pipeline and from_stage, not both")
    return {"from": {"pipeline": args["from_pipeline"], "stage": args["from_stage"]}}


# A stage's open tasks in place of one task (the board's ⋯ on a column): its pipeline and the stage.
FROM_STAGE = {"from_pipeline": _s("Its pipeline"), "from_stage": _s("A stage")}


@tool(
    "tico_task_move",
    "Move a task: `next`, back to a stage, `done`, `closed` or `reopen`. A bot gives a why for all but `next`; reopen "
    "needs one from everyone; a bot never reopens what a person closed. `to_pipeline` moves it to another pipeline, "
    "number and history kept. `from_pipeline` + `from_stage` instead of `id`: all that stage's open tasks, each moved "
    "as one move is (`to` alone keeps them in their pipeline: `closed` closes them all).",
    {
        "id": TASK_ID,
        "to": _s("next, done, closed, reopen, or a stage's name or id"),
        "to_pipeline": _s("Another pipeline's name or id"),
        **FROM_STAGE,
        "why": WHY,
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    writes=True,
)
def task_move(api, args):
    stage = _from_stage(args, "Moving")
    if not args.get("to") and not args.get("to_pipeline"):
        raise ValueError("Say where it goes: to, or to_pipeline")
    if stage:
        body = {
            **stage,
            **({"to_pipeline": args["to_pipeline"]} if args.get("to_pipeline") else {}),
            **({"to": args["to"]} if args.get("to") else {}),
            **_why(args),
        }
        return _asked(api, args).post("tasks/move", body, key=_key(args))
    body = {
        **({"to": args["to"]} if args.get("to") else {}),
        **({"to_pipeline": args["to_pipeline"]} if args.get("to_pipeline") else {}),
        "version": _seen(args),
        **_why(args),
    }
    links = s3links.apply(api, body, ("why",))
    return s3links.annotate(_asked(api, args).post(f"tasks/{args['id']}/move", body, key=_key(args)), links)


@tool(
    "tico_task_close",
    "Close a task, with why: it is never refused for what is missing, and its open sub-tasks "
    "close with it. A bot cannot close a task a person asked for: it asks that person instead "
    "(tico_task_ask). Agent Ops closes with the rights of the person who asked it.",
    {"id": TASK_ID, "why": WHY, "version": VERSION, "on_behalf_of": ASKED},
    required=("id",),
    writes=True,
)
def task_close(api, args):
    body = {"to": "closed", "version": _seen(args), **_why(args)}
    path = f"tasks/{args['id']}/move"
    links = s3links.apply(api, body, ("why",))
    if args.get("on_behalf_of"):
        return s3links.annotate(_asked(api, args).post(path, body, key=_key(args)), links)
    requester = _as_person(api)
    if requester is not api:
        try:
            return s3links.annotate(requester.post(path, body, key=_key(args)), links)
        except Exception as exc:
            if getattr(exc, "code", None) != "on_behalf_of":
                raise
            # Fleet work has no person's request behind it: Agent Ops closes with its own rights.
    return s3links.annotate(api.post(path, body, key=_key(args)), links)


@tool(
    "tico_task_reassign",
    "Give the task to another bot or person for this stage. It raises the version and starts the tries again; the "
    "old owner's queued wakes for it are dropped. `from_pipeline` + `from_stage` instead of `id`: all that stage's "
    "open tasks (a person's change, or one made for a person with on_behalf_of).",
    {
        "id": TASK_ID,
        **FROM_STAGE,
        "owner": _s("The new owner: a bot slug, a person id, or `me`"),
        "why": WHY,
        "version": VERSION,
        "set_anyway": {
            "type": "boolean",
            "description": "A person gives it all the same when a stage rule refused this owner (a bot is refused)",
        },
        "on_behalf_of": ASKED,
    },
    required=("owner",),
    writes=True,
)
def task_reassign(api, args):
    stage = _from_stage(args, "Reassigning")
    if stage:
        body = {**stage, "owner": _target(api, args["owner"]), **_why(args)}
        return _asked(api, args).post("tasks/owner", body, key=_key(args))
    version = _seen(args)
    body = {"owner": _target(api, args["owner"]), "version": version, **_why(args)}
    if args.get("set_anyway"):
        body["set_anyway"] = True
    return _asked(api, args).post(f"tasks/{args['id']}/owner", body, key=_key(args))


@tool(
    "tico_task_snooze",
    "Set a task aside in its stage, with why: no wakes, no Late, and it leaves Needs you until its date or until "
    "someone unsnoozes it; its owner is then woken once. Give a date wherever there is one (\"vendor replies by Nov "
    "14\"); with none it is On ice and the manager bot is told. A bot's fourth snooze in one stage goes to the manager "
    "bot instead. Any move ends a snooze. `from_pipeline` + `from_stage` instead of `id`: all that stage's open tasks "
    "(a person's change only).",
    {
        "id": TASK_ID,
        **FROM_STAGE,
        "until": _s("An ISO-8601 date (the start of that day, Pacific) or date-time with a zone"),
        "why": WHY,
        "version": VERSION,
    },
    writes=True,
)
def task_snooze(api, args):
    stage = _from_stage(args, "Snoozing")
    if stage:
        body = {**stage, **_why(args, "until"), **_why(args)}
        return api.post("tasks/snooze", body, key=_key(args))
    body = {"version": _seen(args), **_why(args, "until"), **_why(args)}
    return api.post(f"tasks/{args['id']}/snooze", body, key=_key(args))


@tool(
    "tico_task_unsnooze",
    "A person brings a snoozed task back now; its owner is woken once.",
    {"id": TASK_ID, "why": _s("Why, in a sentence; optional")},
    required=("id",),
    writes=True,
)
def task_unsnooze(api, args):
    return api.post(f"tasks/{args['id']}/unsnooze", _why(args), key=_key(args))


def _own_ask(api, task_id, requirement=None):
    """The requirement id of the ask to withdraw: the one named, else the one ask on the task the caller may withdraw.
    The read finds which ask; the withdraw carries no version."""
    if requirement:
        return requirement
    shown = api.get("tasks/" + task_id)
    task = shown.get("task", shown) if isinstance(shown, dict) else {}
    mine = [r for r in task.get("to_leave") or [] if "withdraw" in (r.get("may") or [])]
    if len(mine) == 1:
        return mine[0]["requirement_id"]
    if not mine:
        raise ValueError(f"No ask of yours to withdraw on {task_id}")
    raise ValueError(
        f"{len(mine)} asks on {task_id} may be withdrawn: name one with --requirement "
        + ", ".join(r["requirement_id"] for r in mine)
    )


# What makes tico_task_ask a stage ask rather than a ❓: any of these.
# A question is a message marked ❓ (backend/question_marks.py); its options and why are words in it.
QUESTION_INSTEAD = (
    'A question is a message: tico task ask <id> --to <person> "<question>", with no --kind or --show. Say the '
    "options, your pick and why in the message."
)
_STAGE_ASK = ("kind", "show", "subject", "why")


@tool(
    "tico_task_ask",
    "Ask a person on this task. `to` and `line`: a ❓ message on its chat, in their Needs you until they reply. A new ❓ "
    "replaces yours, so put every question in one, numbered when there are several; options and why go in it. While it "
    "is open you can't mark the task done or close it. An OK: `kind` ok with `show`. `withdraw` takes an OK back.",
    {
        "id": TASK_ID,
        "to": _s("The person's id"),
        "line": _s("The message: a short first line that reads on its own, then any detail", maxLength=4000),
        "kind": {
            "type": "string",
            "enum": ["ok"],
            "description": "ok: approve or send back what `show` names",
        },
        "show": {
            "type": "array",
            "items": _s("page:<id>, file:<id>@<version> or a link"),
            "maxItems": 10,
            "description": "What an OK approves, shown with it",
        },
        "subject": _s(
            "An OK bound to what you will do, <kind>:<id>, covering exactly `content`; "
            "use it once with tico_ok_use just before acting"
        ),
        "content": {"description": "With subject: exactly what the OK covers (any JSON)"},
        "withdraw": {"type": "boolean", "description": "Take back your open OK instead"},
        "not_known": {"type": "boolean", "description": "Ask even if Tico answered"},
        "requirement": _s("With withdraw: the OK's requirement id, if several"),
        "why": _s("With withdraw: why"),
        "version": VERSION,
    },
    required=("id",),
    writes=True,
)
def task_ask(api, args):
    if args.get("withdraw"):
        requirement = _own_ask(api, args["id"], args.get("requirement"))
        path = f"tasks/{args['id']}/asks/{quote(requirement, safe='')}"
        return api.call("DELETE", path, _why(args), key=_key(args))
    missing = [name for name in ("to", "line") if not args.get(name)]
    if missing:
        raise ValueError(f"An ask names who it is for and the one line they are asked: give {' and '.join(missing)}")
    if not any(args.get(field) for field in _STAGE_ASK):
        # A ❓: the message on the task's chat, needing their reply (backend/question_marks.py).
        question = {"to": args["to"], "text": args["line"]}
        if args.get("not_known"):
            question["not_known"] = True  # past Tico's own answer (backend/ask_known.py)
        return api.post(f"tasks/{args['id']}/questions", question, key=_key(args))
    if args.get("kind") != "ok" and not args.get("subject"):
        raise ValueError(QUESTION_INSTEAD)
    body = {"to": args["to"], "line": args["line"], "version": _seen(args)}
    if args.get("subject"):
        if "content" not in args:
            raise ValueError("An OK bound to a subject is an ok with its content")
        body.update({"subject": args["subject"], "content": args["content"]})
        if args.get("show"):
            body["show"] = args["show"]
        return api.post(f"tasks/{args['id']}/oks", body, key=_key(args))
    for field in ("kind", "show"):
        if args.get(field):
            body[field] = args[field]
    return api.post(f"tasks/{args['id']}/asks", body, key=_key(args))


@tool(
    "tico_ok_use",
    "Use an OK you asked for with a subject (tico_task_ask `subject`), once, just before you act: Tico checks it was "
    "given by the person's own tap, for the same subject and exactly the same content, and not used before. Refused "
    "otherwise; then do not act.",
    {
        "requirement": _s("The OK's requirement id, as tico_task_ask returned it"),
        "subject": _s("The same subject the OK was asked for"),
        "content": {"description": "Exactly the content the OK was asked for"},
        "note": _s("What was done, in a sentence", maxLength=300),
    },
    required=("requirement", "subject", "content"),
    writes=True,
)
def ok_use(api, args):
    body = {"subject": args["subject"], "content": args["content"]}
    if args.get("note"):
        body["note"] = args["note"]
    return api.post(f"oks/{quote(args['requirement'], safe='')}/use", body, key=_key(args))


@tool(
    "tico_task_answer",
    "Answer what you are asked on a task (a bot named on an OK or a question): ok, no (send it "
    "back, with a note). The requirement id is in tico_task_show's To leave.",
    {
        "id": TASK_ID,
        "requirement": _s("The requirement's id"),
        "value": _s("ok or no", enum=["ok", "no"]),
        "note": _s("What they read with it, word for word; required with no"),
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id", "requirement", "value"),
    writes=True,
)
def task_answer(api, args):
    body = {"value": args["value"], "version": _seen(args), **_why(args, "note")}
    path = f"tasks/{args['id']}/asks/{quote(args['requirement'], safe='')}/answer"
    return _asked(api, args).post(path, body, key=_key(args))


@tool(
    "tico_task_give",
    "Give what you are asked on a task to another person: the ask leaves your list and goes to theirs. The "
    "requirement id is in tico_task_show's To leave.",
    {
        "id": TASK_ID,
        "requirement": _s("The requirement's id"),
        "to": _s("The person it goes to: their id"),
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id", "requirement", "to"),
    writes=True,
)
def task_give(api, args):
    if not args.get("to"):
        raise ValueError("Say who it goes to: --to <person>")
    body = {"to": args["to"], "version": _seen(args)}
    path = f"tasks/{args['id']}/asks/{quote(args['requirement'], safe='')}/give"
    return _asked(api, args).post(path, body, key=_key(args))


@tool(
    "tico_task_file",
    "File a task from the task you work on: that task waits until this one ends, and its owner is woken once then. "
    "Yours by default, in General unless you name a pipeline; in a pipeline whose first stage picks from several, the "
    "stage picks its owner unless you name one. Sub-tasks nest at most 5 deep.",
    {
        "parent": TASK_ID,
        "title": _s("What the new task is for, in plain words"),
        "message": _s("The first message on its chat: anything the title doesn't say"),
        "owner": _s(
            "Who does it: a bot slug, a person id, or `me` (the default; in a pipeline whose first stage picks, the "
            "stage picks)"
        ),
        "pipeline": _s("The pipeline's id; default general"),
        "due": _s("ISO-8601 date-time with timezone"),
        "labels": {"type": "array", "items": {"type": "string"}, "description": "Lower-case labels"},
        "private": {"type": "boolean", "description": "Only its requester, owner and readers see it"},
        "after": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tasks it merges after (ids or 8+ leading characters): its PRs queue in Merge only once "
            "each has merged; build and review go ahead",
        },
    },
    required=("parent", "title"),
    writes=True,
)
def task_file(api, args):
    fields = ("parent", "title", "message", "body", "pipeline", "due", "labels", "private", "after", "operation_id")
    owner = args.get("owner") or (None if _first_stage_picks(api, args.get("pipeline")) else "me")
    return task_create(api, {**{k: args[k] for k in fields if k in args}, "owner": owner})


def _first_stage_picks(api, pipeline):
    """Whether a named pipeline's first stage picks its owner from several (owner `pick`, backend/stage_pick.py): a task
    filed into it with no owner named is left for the stage to pick, not given to the filer. General never picks."""
    if not pipeline or str(pipeline).strip().lower() == "general":
        return False
    for row in (api.get("pipelines") or {}).get("pipelines") or []:
        if str(pipeline).strip().lower() in (str(row.get("id") or "").lower(), str(row.get("name") or "").lower()):
            stages = row.get("stages") or []
            return bool(stages) and stages[0].get("owner") == "pick"
    return False


@tool(
    "tico_task_list",
    "Tasks you may see, filtered; each has its full `id`, an 8-character `short_id` that every "
    "task tool accepts, its `version` (send it with a change), its `stage_id` and its `turn` "
    "(whose turn it is, and why). Your own come back in queue order: the first is what to do "
    "next. `all` also returns every bot you may see (`{tasks, bots}`).",
    {
        "owner": _s("Owner: a bot slug, a human id, or `me`"),
        "requester": _s("Requester: a bot slug, a human id, or `me`"),
        "q": _s("Title words or #N"),
        "status": {
            "type": "array",
            "items": {"type": "string", "enum": list(STATUSES)},
            "description": "Only these statuses",
        },
        "label": _s("Only tasks carrying this label"),
        "sort": {
            "type": "string",
            "enum": ["queue", "finished"],
            "description": "queue (default) or finished (most recently ended first)",
        },
        "number": {"type": "integer", "minimum": 1, "maximum": 999999999, "description": "Only this task number"},
        "updated_since": _s("Only tasks changed after this ISO-8601 time with a timezone"),
        "member": _s("Only tasks this person or bot is on in some role (`me`, a human id or a bot slug)"),
        "role": _s("With member: only tasks they are on in this role"),
        "brief": {"type": "boolean", "description": "Leave out each task's longer fields"},
        "all": {
            "type": "boolean",
            "default": False,
            "description": "Every task (snoozed ones too) and every bot you may see, as `{tasks, bots}`",
        },
        "snoozed": {
            "type": "string",
            "enum": ["hide", "only", "all"],
            "description": "Snoozed tasks: hide (default), only them, or all",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
            "description": "Tasks in this page (500 at most); with limit or offset the answer is `{tasks, next_offset}`",
        },
        "offset": {"type": "integer", "minimum": 0, "description": "Skip this many: a page's next_offset"},
        "pipeline": _s("Only this pipeline's tasks: its id"),
        "stage": _s("Only the tasks in this stage: its id (engineering-build), or its name with pipeline"),
        "waiting_on": _s("Only open tasks waiting on this person or bot: `me`, a human id or a bot slug"),
        "exact": {
            "type": "boolean",
            "default": False,
            "description": "Refuse an owner or requester that names nobody, instead of listing without that filter",
        },
    },
)
def task_list(api, args):
    more = {
        name: args[name]
        for name in ("sort", "number", "updated_since", "snoozed", "q", "pipeline", "stage")
        if args.get(name)
    }
    if args.get("all") and not args.get("snoozed"):
        more["snoozed"] = "all"  # every task you may see: the snoozed ones too
    if args.get("waiting_on"):
        more["waiting_on"] = _target(api, args["waiting_on"])
    paged = args.get("limit") is not None or args.get("offset") is not None
    if paged:
        more.update({name: args[name] for name in ("limit", "offset") if args.get(name) is not None})
    if args.get("member"):
        more["member"] = _target(api, args["member"])
        if args.get("role"):
            more["role"] = args["role"]
    if args.get("brief"):
        more["brief"] = "true"
    if args.get("exact"):
        more["exact"] = "true"

    def named(field):
        # Under exact an empty name still goes, so the server refuses it instead of listing without the filter.
        value = args.get(field)
        if value:
            return _target(api, value)
        return "" if args.get("exact") and value is not None else None

    page = api.get(
        "tasks",
        owner=named("owner"),
        requester=named("requester"),
        status=",".join(args["status"]) if args.get("status") else None,
        label=args.get("label") or None,
        **more,
    )
    rows = page["tasks"]
    if paged:  # a page: its next_offset reads on (null on the last page)
        return {
            "tasks": rows,
            "next_offset": page.get("next_offset"),
            **({"bots": api.get("bots")} if args.get("all") else {}),
        }
    return {"tasks": rows, "bots": api.get("bots")} if args.get("all") else rows


@tool(
    "tico_task_update",
    "Edit a task's fields: title, due, labels, privacy, goal, with a note. Where it is and who has it change "
    "by tico_task_done, tico_task_move, tico_task_close and tico_task_reassign, never here.",
    {
        "id": TASK_ID,
        "title": _s("A new title; it is checked as a new task's title would be"),
        "due": _s("ISO-8601 date-time with timezone"),
        "labels": {"type": "array", "items": {"type": "string"}, "description": "Replace the labels"},
        "private": {
            "type": "boolean",
            "description": "Tighten visibility; only the direct human requester can publish",
        },
        "goal_id": _s("The goal this task serves; an empty string takes it off"),
        "human": _s("Its Human: a person, `me`, or \"\" for none"),
        "note": _s("What changed, in a sentence"),
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id",),
    writes=True,
)
def task_update(api, args):
    body = {"version": _seen(args)}
    for field in ("title", "due", "labels", "private", "goal_id", "note"):
        if args.get(field) is not None:
            body[field] = args[field]
    if args.get("human") is not None:
        body["human"] = _target(api, args["human"]) if args["human"] else ""
    links = s3links.apply(api, body, ("note",))
    return s3links.annotate(_asked(api, args).post("tasks/" + args["id"], body, key=_key(args)), links)


RELATION_KINDS = ("related", "duplicate_of", "follow_up", "depends_on")


def _after(args):
    """The tasks a new task merges after: the tool's list, or the CLI's repeated or comma-separated `--after`."""
    values = args.get("after") or []
    if isinstance(values, str):
        values = [values]
    return [part.strip().lstrip("#") for value in values for part in str(value).split(",") if part.strip()]


BLOCKING_GONE = (
    "A task that cannot go on until another ends depends on it: tico task relate <id> depends_on <task>. It shows "
    "Blocked by that task, with no date, and you are woken when it ends."
)


def _roles(api, args):
    """{role: [actors]} from `roles` (the tool's object) and `role` (the CLI's `--role name=a,b`, repeatable;
    `name=` clears the role), each person or bot resolved as an owner would be."""
    out = {}
    for role, people in (args.get("roles") or {}).items():
        if isinstance(people, str):
            people = people.split(",")
        out[str(role).strip().lower()] = [_target(api, str(p).strip()) for p in (people or []) if str(p).strip()]
    for item in args.get("role") or []:
        name, _, people = str(item).partition("=")
        out[name.strip().lower()] = [_target(api, p.strip()) for p in people.split(",") if p.strip()]
    return out


@tool(
    "tico_task_queue",
    "Queue again: on the Merge stage, after GitHub took a PR out of the merge queue (or refused "
    "it), put it back at its newest commit. Refused while its checks are running or failing "
    "there. Push a fix to the PR branch first when it needs one; or move the task back instead.",
    {"id": TASK_ID, "version": VERSION, "on_behalf_of": ASKED},
    required=("id",),
    writes=True,
)
def task_queue(api, args):
    return _asked(api, args).post(f"tasks/{args['id']}/queue", {"version": _seen(args)}, key=_key(args))


@tool(
    "tico_task_release",
    "Cut the release a task in Release waits for: Tico runs the repository's release workflow, or "
    "creates the tag you name where it releases from a tag, through its GitHub App. One release "
    "covers every task waiting on that repository. Refused before a person signed the task off, "
    "and while an earlier ask still waits for its release.",
    {
        "id": TASK_ID,
        "tag": {"type": "string", "description": "The release's tag, where the repository releases from a tag"},
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id",),
    writes=True,
)
def task_release(api, args):
    body = {"version": _seen(args), **({"tag": args["tag"]} if args.get("tag") else {})}
    return _asked(api, args).post(f"tasks/{args['id']}/release", body, key=_key(args))


@tool(
    "tico_task_after",
    "Say this task merges after another one (`id` after `task`): when two tasks change the same lines. Build and "
    "review go ahead side by side; in Merge its PRs are not queued until `task` has merged, then Tico queues them (or "
    "says to rebase onto it when they conflict). `remove` takes the order off. Refused for a loop, or a task that has "
    "ended. The reply's `readback` says what was stored, from both ends.",
    {
        "id": TASK_ID,
        "task": {**TASK_ID, "description": "The task it merges after: its id or 8+ leading characters"},
        "remove": {"type": "boolean", "default": False},
    },
    required=("id", "task"),
    writes=True,
)
def task_after(api, args):
    return api.post(
        f"tasks/{args['id']}/relations",
        {"task": str(args["task"]).lstrip("#"), "kind": "after", "remove": bool(args.get("remove"))},
        key=_key(args),
    )


@tool("tico_task_followers", "Who follows a task, at what level.", {"id": TASK_ID}, required=("id",))
def task_followers(api, args):
    return api.get(f"tasks/{args['id']}/followers")


@tool(
    "tico_task_relate",
    "Relate another task to this one, read as `id kind task`: related (they belong together, the default), "
    "duplicate_of, follow_up (it was split off or followed up from task), or depends_on (this task cannot go on until "
    "task ends: it shows Blocked by it, with no date, nobody is woken for it, and its owner is woken once when task "
    "ends; use it, never a snooze, when you wait on another task, its PR, a fix for main or a release). With until "
    "released, a depends_on holds after task is Done, until production runs a release that holds its merged PRs: how a "
    "Validate task waits for its work to reach production. The other "
    "relations are notes, and a task's parent is set when it is filed. `remove` takes the relation off. The reply's "
    "`readback` says what was stored, both ends: check it before reporting the link as fixed.",
    {
        "id": TASK_ID,
        "task": {**TASK_ID, "description": "The other task: its id or 8+ leading characters"},
        # No enum: blocks and blocked_by are answered with what to do instead, not with a list of choices.
        "kind": _s("related (the default), duplicate_of, follow_up or depends_on", default="related"),
        "remove": {"type": "boolean", "default": False},
        "until": {
            "type": "string",
            "enum": ["released"],
            "description": "depends_on only: stay blocked after task is Done, until production runs its release",
        },
    },
    required=("id", "task"),
    writes=True,
)
def task_relate(api, args):
    kind = args.get("kind") or "related"
    if args.get("other"):  # the CLI's three words, `tico task relate <id> depends_on <task>`
        named = str(args.get("kind") or "related").strip().lower().replace("-", "_")
        if named not in ("related", str(args["task"]).strip().lower().replace("-", "_")):
            raise ValueError(f"Two kinds given: {args['task']} and --kind {args['kind']}. Give one.")
        kind, args = str(args["task"]), {**args, "task": args["other"]}
    kind = kind.strip().lower().replace("-", "_")
    if kind in ("blocks", "blocked_by", "blocked", "block"):
        raise ValueError(BLOCKING_GONE)
    if kind not in RELATION_KINDS:
        raise ValueError(f"A relation is {', '.join(RELATION_KINDS)}, not {kind}")
    body = {"task": str(args["task"]).lstrip("#"), "kind": kind, "remove": bool(args.get("remove"))}
    if args.get("until"):
        if kind != "depends_on" or args.get("remove"):
            raise ValueError(
                "--until released goes with depends_on: tico task relate <id> depends_on <task> --until released"
            )
        body["until"] = args["until"]
    return api.post(f"tasks/{args['id']}/relations", body, key=_key(args))


@tool(
    "tico_task_model",
    "Set the model one task's runs use, as the person who asked you: a model id or name from the "
    "list, with its effort and harness. Leave out `model` to change only the effort or harness of the task's own "
    "model; `clear` puts the task back on the bot's task model. Whoever may change the bot's model may change this; "
    "a bot never does.",
    {
        "id": TASK_ID,
        "model": _s("A model id or name from tico_bot_model's list; leave out to keep the task's own"),
        "effort": _s("Reasoning effort that model takes"),
        "harness": _s("A harness from that model's harnesses list"),
        "clear": {"type": "boolean", "default": False, "description": "Clear the task's own model"},
        "on_behalf_of": ON_BEHALF_OF,
    },
    required=("id",),
    writes=True,
)
def task_model(api, args):
    who = _as_person(api, args.get("on_behalf_of"))
    if args.get("clear"):
        body = {"model": None}
    else:
        body = {}
        if args.get("model"):
            body["model"] = _model_id(who.get("models"), args["model"])
        for field in ("effort", "harness"):
            if args.get(field):
                body[field] = args[field]
        if not body:
            raise ValueError("Name a model, an effort or a harness, or clear the task's model")
    return who.post(f"tasks/{args['id']}/model", body, key=_key(args))


@tool(
    "tico_task_requesters",
    "Internal requester contacts and counts, paged. Tokens are never listed.",
    {"id": TASK_ID, "limit": {"type": "integer"}, "offset": {"type": "integer"}},
    required=("id",),
)
def task_requesters(api, args):
    return api.get(f"tasks/{args['id']}/requesters", **{k: args[k] for k in ('limit', 'offset') if k in args})


@tool(
    "tico_task_requester_save",
    "Record or partially update a requester on a task. Contact details are unverified and internal only. "
    "Omit actor for an external requester; a verified teammate identity determines teammate status. Nothing sends.",
    {
        "id": TASK_ID,
        "requester_id": _s("Existing requester id to update; omit to add"),
        "actor": _s("Verified teammate actor; omit for external"),
        "name": _s("Name"),
        "email": _s("Email"),
        "phone": _s("Phone"),
        "other": _s("Other contact"),
        "organization": _s("Organization"),
        "note": _s("Quote or note"),
        "source": _s("Source", enum=['public_form', 'inbox', 'meeting', 'manual']),
        "important": {"type": "boolean"},
    },
    required=("id",),
    writes=True,
)
def task_requester_save(api, args):
    path = f"tasks/{args['id']}/requesters" + ('/' + args['requester_id'] if args.get('requester_id') else '')
    return api.post(
        path,
        {
            k: v
            for k, v in args.items()
            if k in ('actor', 'name', 'email', 'phone', 'other', 'organization', 'note', 'source', 'important')
        },
        key=_key(args),
    )


@tool(
    "tico_task_requester_remove",
    "Remove a requester from a task (task managers).",
    {"id": TASK_ID, "requester_id": _s("Requester id")},
    required=("id", "requester_id"),
    writes=True,
)
def task_requester_remove(api, args):
    return api.post(f"tasks/{args['id']}/requesters/{args['requester_id']}/delete", {}, key=_key(args))


@tool(
    "tico_task_comment_moderate",
    "Hide, restore or delete a public comment on a task you manage.",
    {"id": TASK_ID, "comment_id": _s("Public comment id"), "action": _s("Action", enum=['hide', 'show', 'delete'])},
    required=("id", "comment_id", "action"),
    writes=True,
)
def task_comment_moderate(api, args):
    return api.post(
        f"tasks/{args['id']}/comments/{args['comment_id']}/moderate", {'action': args['action']}, key=_key(args)
    )


def _call(client, name, fields, unwrapped=False):
    """remotecli.call_tool: the tool with the request's operation id, sent only when HUB_OPERATION_ID is set. A write that
    answers only `{"task": ...}` prints the task alone (remotecli.unwrap) when `unwrapped`."""
    from clients import hubtools
    from clients.remotecli import unwrap

    key = os.environ.get("HUB_OPERATION_ID")
    answer = hubtools.BY_NAME[name]["fn"](client, {**fields, "operation_id": key} if key else fields)
    return unwrap(answer) if unwrapped else answer


def _via(name):
    """remotecli.via_tool for a write that answers `{"task": ...}` alone: the spec's arguments, the task printed."""

    def run(client, fields):
        return _call(client, name, fields, unwrapped=True)

    return run


def _place(text):
    """ "Backlog/On ice" as (pipeline, stage); "Backlog" alone is (pipeline, None)."""
    pipeline, _, stage = str(text).partition("/")
    return pipeline.strip(), stage.strip() or None


def _from_place(fields, from_place, shift):
    """--from "Pipeline/Stage" in place of a task: the tool's from_pipeline and from_stage. With it the first positional
    is the next one's (`shift`: `tico task move --from Dev/Ideas closed`, `tico task reassign --from Dev/Ideas ana`)."""
    from clients.tico import APIError

    if not from_place:
        return
    fields["from_pipeline"], fields["from_stage"] = _place(from_place)
    if not fields["from_stage"]:
        raise APIError("validation", '--from names a stage: "Pipeline/Stage"', 422)
    if shift and fields.get("id") and not fields.get(shift):
        fields[shift] = fields.pop("id")
    if fields.get("id"):
        raise APIError("validation", "Give a task, or --from, not both", 422)


def _move(client, fields, to_place=None, from_place=None):
    """`tico task move` with --to "Pipeline/Stage" (another pipeline) or --from "Pipeline/Stage" (a stage's open
    tasks): the tool's to_pipeline, to, from_pipeline and from_stage."""
    from clients.tico import APIError

    if to_place:
        if fields.get("to"):
            raise APIError("validation", 'Give the stage in --to "Pipeline/Stage", not as well after the task', 422)
        fields["to_pipeline"], to = _place(to_place)
        if to:
            fields["to"] = to
    _from_place(fields, from_place, None if to_place else "to")
    if not from_place and not fields.get("id"):
        raise APIError("validation", 'Say which task, or --from "Pipeline/Stage"', 422)
    if from_place and not fields.get("to") and not fields.get("to_pipeline"):
        raise APIError("validation", 'Say where they go: a stage, done or closed, or --to "Pipeline/Stage"', 422)
    return _call(client, "tico_task_move", {k: v for k, v in fields.items() if v is not None}, unwrapped=True)


def _from_stage_via(name, shift):
    """A task write that also takes --from "Pipeline/Stage": every open task there in place of one."""

    def run(client, fields, from_place=None):
        _from_place(fields, from_place, shift)
        return _call(client, name, {k: v for k, v in fields.items() if v is not None}, unwrapped=True)

    return run


FROM_ARG = Arg(
    "from_place",
    "--from",
    local=True,
    metavar="PIPELINE/STAGE",
    help="every open task in this stage, in place of an id",
)


def _show(client, fields, **_local):
    """`tico task show ID ...`: one task, or several in one read (tico_task_show_many). `--json` is local: it shapes
    what prints (remotecli), so it is taken here and never sent."""
    from clients import hubtools

    ids = fields["id"] if isinstance(fields["id"], list) else [fields["id"]]
    if len(ids) > 1:
        return hubtools.BY_NAME["tico_task_show_many"]["fn"](client, {"ids": ids})
    return hubtools.task_show(client, {"id": ids[0]})


def _label(client, fields):
    """`tico task label`: --add and --remove take comma-separated lists, and both are always sent."""

    def split(values):
        return [x.strip() for one in values or [] for x in one.split(",") if x.strip()]

    return _via("tico_task_label")(
        client, {**fields, "add": split(fields.get("add")), "remove": split(fields.get("remove"))}
    )


def _link(client, fields):
    """`tico task link`: the URL and title, `kind` session with --session, and the wake switch when it was given."""
    sent = {"id": fields["id"], "url": fields["url"], "title": fields.get("title")}
    if fields.get("kind"):
        sent["kind"] = fields["kind"]
    if fields.get("wake_once") is not None:  # --no-wake-once sends false, as the hand parser did
        sent["wake_once"] = fields["wake_once"]
    if fields.get("on_behalf_of"):
        sent["on_behalf_of"] = fields["on_behalf_of"]
    return _call(client, "tico_task_link", sent, unwrapped=True)


# `tico task ...` (clients/cli_spec.py): the flags come from each tool's schema. A flag the schema does not name is left
# out with `omit`. `-m` and `--why` (or `--note`) are one flag spelled twice. A write that answers `{"task": ...}` alone
# prints the task (`_via`). The hand parser keeps the leaves clients/hubcli.py names: their arguments are not the tool's.
TASK = (
    Group("task", aliases=("tasks",)),
    Command("task tree", "show nested subtasks", example="tico task tree T1"),
    Command(
        "task caps",
        "the task caps: today's count per pair, the tasks closed before the receiver acted, and the changes bots asked for",
        example="tico task caps",
    ),
    Group("task cap", "set a task cap: how many tasks sender may create for receiver a day"),
    Command(
        "task cap set",
        "sender receiver --per-day N; 0 blocks the pair, and with no --per-day the cap is removed",
        args=(Arg("per_day", "--per-day", default=NULL, required=False),),
        example="tico task cap set bot:ana bot:ben --per-day 5",
    ),
    Command(
        "task requesters",
        "a task's requesters: internal contacts and counts, never tokens",
        example="tico task requesters T1 --limit 5",
    ),
    Command(
        "task requester-save",
        "save a requester on a task: a person or organization, its contact details and a note",
        args=(
            Arg("important", "--important", action="store_const", const=True),
            Arg("important", "--no-important", action="store_const", const=False),
        ),
        example="tico task requester-save T1 --name 'Ana Lopes' --email ana@example.com",
    ),
    Command("task requester-remove", "take a requester off a task", example="tico task requester-remove T1 R1"),
    Command(
        "task comment-moderate",
        "hide, show again or delete a public comment on a task you manage",
        example="tico task comment-moderate T1 C1 hide",
    ),
    Command(
        "task comment-edit",
        "change the text of a comment you wrote; it wakes nobody",
        example="tico task comment-edit T1 C1 'Fixed wording'",
        run=lambda client, f: _call(
            client,
            "tico_task_comment_edit",
            {
                "id": f["id"],
                "comment_id": f["comment_id"],
                "text": f["text"],
                **({"ok": f["ok"]} if f.get("ok") else {}),
            },
        ),
    ),
    Command(
        "task comment-delete",
        "take back a comment you wrote",
        example="tico task comment-delete T1 C1",
        run=lambda client, f: _call(client, "tico_task_comment_delete", {"id": f["id"], "comment_id": f["comment_id"]}),
    ),
    Command(
        "task delete",
        "delete a task made by mistake: its requester or a mover, never a bot",
        example="tico task delete T1",
        run=lambda client, f: _call(client, "tico_task_delete", {"id": f["id"]}, unwrapped=True),
    ),
    Command(
        "task deleted",
        "deleted tasks you may restore, newest first",
        example="tico task deleted",
        run=lambda client, f: _call(client, "tico_task_deleted", {}),
    ),
    Command(
        "task restore",
        "put a deleted task back: whoever deleted it, its requester or a mover",
        example="tico task restore T1",
        run=lambda client, f: _call(client, "tico_task_restore", {"id": f["id"]}, unwrapped=True),
    ),
    Group("task readers", "who can see a private task: list, add, remove"),
    Command("task readers list", "its requester, owner and readers", example="tico task readers list T1"),
    Command(
        "task readers add",
        "let them see it: people, bots or group:<id>s",
        args=(Arg("readers", action="store", nargs="+"),),
        example="tico task readers add T1 human:ana",
    ),
    Command(
        "task readers remove",
        "take them off: they stop seeing it",
        args=(Arg("readers", action="store", nargs="+"),),
        example="tico task readers remove T1 human:ana",
    ),
    Command("task followers", "who follows a task, each at their level", example="tico task followers T1"),
    Command(
        "task follow",
        "follow a task, or set your level",
        args=(Arg("who", "--who"),),
        omit=("unfollow",),
        example="tico task follow T1 --level digest",
    ),
    Command(
        "task unfollow",
        "stop following a task",
        tool="tico_task_follow",
        fixed=(("unfollow", True),),
        omit=("level",),
        args=(Arg("who", "--who"),),
        example="tico task unfollow T1",
    ),
    Command("task context", "readable page and meeting references for a task", example="tico task context T1"),
    Command("task meetings", "meetings linked to a task", example="tico task meetings T1 --limit 5"),
    Command(
        "task list",
        "the tasks you may see, newest first",
        args=(
            Arg("status", "--status", choices=("open", "done", "closed")),
            Arg("snoozed", "--snoozed", choices=("only", "all")),
        ),
        example="tico task list --status open --limit 10",
    ),
    Command(
        "task show",
        "a task: its state, its asks and the next steps (--json for the whole record)",
        args=(
            Arg("id", nargs="+"),
            Arg("json", "--json", action="store_true", local=True, help="print the whole task as JSON, not text"),
        ),
        example="tico task show T1",
        run=_show,
    ),
    Command(
        "task run",
        "start a bot's task now, as the task",
        example="tico task run T1",
        run=lambda client, f: _call(client, "tico_task_run", {"id": f["id"]}, unwrapped=True),
    ),
    Command(
        "task model",
        "the model a bot-owned task's runs use; no model shows it",
        args=(Arg("model", positional=True, nargs="?"),),
        omit=("on_behalf_of",),
        example="tico task model T1 gpt-6.2-sol --effort high",
    ),
    Command(
        "task child",
        "create a subtask",
        tool="tico_task_child_create",
        args=(
            Arg("title", "--title"),
            Arg("message", default=""),
            Arg("message", "--body"),
            Arg("check_model", "--check-model", nargs="?", const="auto", metavar="RUNTIME[:MODEL]"),
            Arg("check_role", "--role"),
        ),
        example="tico task child T1 --title 'Check the copy'",
    ),
    Command(
        "task snooze",
        "snooze a task until a time, with why; --from \"Pipeline/Stage\" snoozes every open task there",
        args=(Arg("id", positional=True, nargs="?"), FROM_ARG, Arg("why", "-m"), Arg("why", "--why")),
        omit=("from_pipeline", "from_stage"),
        example="tico task snooze T1 --until 2026-12-01 -m 'waiting on the vendor'",
        run=_from_stage_via("tico_task_snooze", None),
    ),
    Command(
        "task unsnooze",
        "a person brings a snoozed task back now; its owner is woken once",
        args=(Arg("why", "-m"), Arg("why", "--why")),
        example="tico task unsnooze T1",
        run=_via("tico_task_unsnooze"),
    ),
    Command(
        "task queue",
        "queue again: put the PRs GitHub took out of the merge queue back in it",
        example="tico task queue T1 --version 3",
        run=_via("tico_task_queue"),
    ),
    Command(
        "task release",
        "cut the release a task in Release waits for, the way the repository makes one",
        example="tico task release T1",
        run=_via("tico_task_release"),
    ),
    Command(
        "task unlink",
        "take a link off a task: a PR closed without merging, by its URL or id",
        example="tico task unlink T1 https://github.com/acme/app/pull/12",
        run=_via("tico_task_unlink"),
    ),
    Command(
        "task done",
        "the work of this stage is finished; never pushes past what is missing",
        args=(Arg("note", "-m"), Arg("note", "--note")),
        example="tico task done T1 -m 'Shipped the fix'",
        run=_via("tico_task_done"),
    ),
    Command(
        "task move",
        "move a task: next (with -m, past what is not met), back to a stage, done, closed or reopen; "
        '--to "Pipeline/Stage" moves it to another pipeline, --from "Pipeline/Stage" moves (or closes) every open task there',
        args=(
            Arg("id", positional=True, nargs="?"),
            Arg("to", positional=True, nargs="?"),
            Arg(
                "to_place",
                "--to",
                local=True,
                metavar="PIPELINE/STAGE",
                help="another pipeline, and its stage after a / (its first when left out): the task keeps its label",
            ),
            FROM_ARG,
            Arg("why", "-m"),
            Arg("why", "--why"),
        ),
        omit=("to_pipeline", "from_pipeline", "from_stage"),
        example="tico task move T1 review -m 'Ready for review'",
        run=_move,
    ),
    Command(
        "task close",
        "close a task with why; its open sub-tasks close with it",
        args=(Arg("why", "-m"), Arg("why", "--why")),
        example="tico task close T1 -m 'Duplicate of T2'",
        run=_via("tico_task_close"),
    ),
    Command(
        "task reassign",
        "give the task to another bot or person for this stage; --from \"Pipeline/Stage\" gives every open task there",
        args=(
            Arg("id", positional=True, nargs="?"),
            Arg("owner", positional=True, nargs="?"),
            FROM_ARG,
            Arg("why", "-m"),
            Arg("why", "--why"),
        ),
        omit=("from_pipeline", "from_stage"),
        example="tico task reassign T1 bot:ana -m 'Ana has the data'",
        run=_from_stage_via("tico_task_reassign", "owner"),
    ),
    Command(
        "task answer",
        "answer what you are asked on a task: ok, no, again or your answer",
        args=(Arg("note", "-m"), Arg("note", "--note")),
        example="tico task answer T1 R1 ok",
        run=_via("tico_task_answer"),
    ),
    Command(
        "task file",
        "file a task under a parent: it waits until that one ends",
        args=(
            Arg("parent", "--parent"),
            Arg("labels", "--label", action="append"),
            Arg("private", "--private", action="store_const", const=True),
        ),
        example="tico task file 'Write the summary' --parent T1",
        run=_via("tico_task_file"),
    ),
    Command(
        "task update",
        "edit a task's fields: title, due, labels, privacy, goal; with --version",
        args=(
            Arg("note", "-m"),
            Arg("note", "--note"),
            Arg("labels", "--label", action="append"),
            Arg("goal_id", "--goal"),
            Arg("private", "--private", action="store_const", const=True),
            Arg("private", "--company", action="store_const", const=False),
        ),
        omit=("human",),
        example="tico task update T1 --title 'Launch the beta' --version 2",
        run=_via("tico_task_update"),
    ),
    Command(
        "task label",
        "add or remove labels on a task",
        args=(Arg("add", "--add", action="append"), Arg("remove", "--remove", action="append")),
        example="tico task label T1 --add launch",
        run=_label,
    ),
    Command(
        "task after",
        "this task merges after another: <id> <task>; Merge waits for it",
        example="tico task after T1 T2",
        run=_via("tico_task_after"),
    ),
    Command(
        "task link",
        "attach a link: the pull request you opened, an issue, a document, a session",
        args=(
            Arg("kind", "--session", action="store_const", const="session"),
            Arg("wake_once", "--wake-once", action="store_const", const=True),
            Arg("wake_once", "--no-wake-once", action="store_const", const=False),
        ),
        example="tico task link T1 https://github.com/acme/app/pull/12 --title 'Fix PR'",
        run=_link,
    ),
)
