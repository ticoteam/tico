"""Tasks: create, list, show, move, done, close, asks and answers, OKs.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

from urllib.parse import quote
from clients import s3links

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
    "ask, the body is under 120 words, and its first paragraph is the ask: one or two plain sentences on what you "
    "need and what happens after, at most 40 words, with no ids, hashes, file paths, s3:// links or tool names "
    "(\"PR #1555\" is fine; refused otherwise), e.g. \"Approve the video tool trial for Thursday? If yes, I'll send "
    "the invite.\" Details go below. It starts in the first stage of its pipeline (General when none is named). "
    "`dry_run` reports the checks a create would fail and writes nothing. A person’s asking criteria can refuse "
    "the write as ask_refused, with the reason and fix; no task is created. To ask a person something on your own "
    "task, use tico_task_ask.",
    {
        "owner": _s(
            "Who does it: a bot slug, a person id, or `me`. Leave it out to file into a pipeline whose first stage "
            "picks from several: the stage picks one; naming one always wins"
        ),
        "title": _s("Task title: plain words, no reference numbers or all-caps"),
        "body": _s("The details", default=""),
        "pipeline": _s("The pipeline's id; default general"),
        "visibility": _s(
            "People only: roadmap visibility at creation; public needs public_title. A bot is refused; "
            "it creates the task, then requests a publication",
            enum=["public", "private"],
        ),
        "public_title": _s("People only (a bot is refused): public title; the internal title is never published"),
        "public_summary": _s("People only (a bot is refused): public summary; the internal body is never published"),
        "due": _s("ISO-8601 date-time with timezone"),
        "private": {
            "type": "boolean",
            "description": "Only its requester, owner and readers see it; bot defaults also apply",
        },
        "readers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Private: who else sees it: human:<id>, bot:<slug> or group:<id>",
        },
        "follow": {"type": "boolean"},  # the filer follows it too (docs/tasks.md, Followers)
        "from_private": {
            "type": "boolean",
            "default": False,
            "description": "From a run on your own private task with no person on it: a company-visible "
            "task for a bot, carrying only its own words (no 12 words copied from the private "
            "work, no secret-shaped value, no file or link of it). Parent: that private task, "
            "or none",
        },
        "parent": _s(
            "The task that files it (its id, or 8-character short id): the parent waits until this one ends. "
            "Set once, never changed"
        ),
        "request_id": _s(
            "A person's message this task carries: BotOps continuing its own chat, or another bot handing BotOps "
            "a person's request (their message id, or `turn` for the one that started this run). BotOps then acts "
            "with that person's rights, or puts it to them as a Confirm card"
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
        "after": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tasks it merges after (ids or 8+ leading characters): in Merge its PRs are not queued until "
            "each has merged. Build and review go ahead side by side",
        },
        "roles": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
            "description": "Who is on it by role, {role: [people or bots]}: a named role's people are replaced, [] clears "
            "it, an unnamed role stays. Roles are short names the team picks (developer, reviewer, qa); one person may hold several",
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
        "body": args.get("body") or "",
        "due": args.get("due"),
        "goal_id": args.get("goal_id") or None,
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
        "from_private",
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
    links = s3links.apply(api, body, ("body",))
    return s3links.annotate(api.post("tasks", body, key=_key(args)), links)


@tool(
    "tico_task_child_create",
    "Create a subtask under a parent; you are its requester. With `check_model`, the "
    "subtask is your own and runs on that model in a session of its own, reading only the subtask: name in its "
    "body the PR or files to look at. Mark it done with the verdict as the note; it comes back to the parent.",
    {
        "parent": TASK_ID,
        "owner": _s("Bot slug or human id; `me` for a check_model subtask (the default)"),
        "title": _s("What to do"),
        "body": _s("Details", default=""),
        "check_model": _s(
            "A company-enabled runtime, optionally with a model: `grok`, `codex:gpt-6.1-sol`; `auto` for "
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
    return task_create(api, {**args, "owner": owner, "body": args.get("body") or args["title"]})


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
        if item.get("choices"):
            words.append("choices: " + ", ".join(str(c) for c in item["choices"]))
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
    "One task: its version; pipeline, stage and owner; whose turn it is; what it takes to leave the stage (each "
    "requirement, met or not, with its id and what you may do with it); the next stage; the task that filed it. "
    "`text` says it in lines. Send its version with every change you make to the task. For several tasks, "
    "tico_task_show_many reads them in one call.",
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
    "Several tasks in one call (up to 100): each task's fields, without history or "
    "conversation; `missing` lists the ids that are unknown or not yours to read. Use this instead of one "
    "tico_task_show per task.",
    {"ids": {"type": "array", "items": TASK_ID, "minItems": 1, "maxItems": 100}},
    required=("ids",),
)
def task_show_many(api, args):
    ids = [str(i).strip() for i in args["ids"] if str(i).strip()]
    return api.get("tasks", ids=",".join(ids))


@tool(
    "tico_task_done",
    "Say the work of this stage is finished, with what was done. It never pushes past anything: while something else "
    "is missing it is refused with what is missing and the commands you may run instead. In the last stage the task "
    "is Done.",
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


@tool(
    "tico_task_move",
    "Move a task: `next` (forward; with a why, past what is not met), back to an earlier stage by its name or id, "
    "`done`, `closed` or `reopen`. Forward with something not met and no why is refused with what is missing and "
    "the commands you may run. A bot says why for every move but forward; reopen needs a why from everyone. A bot "
    "does not reopen what a person closed.",
    {
        "id": TASK_ID,
        "to": _s("next, done, closed, reopen, or a stage's name or id"),
        "why": WHY,
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id", "to"),
    writes=True,
)
def task_move(api, args):
    body = {"to": args["to"], "version": _seen(args), **_why(args)}
    links = s3links.apply(api, body, ("why",))
    return s3links.annotate(_asked(api, args).post(f"tasks/{args['id']}/move", body, key=_key(args)), links)


@tool(
    "tico_task_close",
    "Close a task, with why: it is never refused for what is missing, and its open sub-tasks close with it. A bot "
    "cannot close a task a person asked for: it asks that person instead (tico_task_ask). BotOps closes with the "
    "rights of the person who asked it.",
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
            # Fleet work has no person's request behind it: BotOps closes with its own rights.
    return s3links.annotate(api.post(path, body, key=_key(args)), links)


@tool(
    "tico_task_reassign",
    "Give the task to another bot or person for this stage. It raises the version and starts the tries again; the "
    "old owner's queued wakes for it are dropped.",
    {
        "id": TASK_ID,
        "owner": _s("The new owner: a bot slug, a person id, or `me`"),
        "why": WHY,
        "version": VERSION,
        "set_anyway": {
            "type": "boolean",
            "description": "A person gives it all the same when a stage rule refused this owner (a bot is refused)",
        },
        "on_behalf_of": ASKED,
    },
    required=("id", "owner"),
    writes=True,
)
def task_reassign(api, args):
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
    "bot instead. Any move ends a snooze.",
    {
        "id": TASK_ID,
        "until": _s("An ISO-8601 date (the start of that day, Pacific) or date-time with a zone"),
        "why": WHY,
        "version": VERSION,
    },
    required=("id",),
    writes=True,
)
def task_snooze(api, args):
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


@tool(
    "tico_task_ask",
    "Ask a person on this task, in one sentence: a question (with choices, or a typed answer) or an OK. The task "
    "stays in its stage until they answer, it is one line in their Needs you, and their answer wakes you once. "
    "Asking again in this stage replaces your earlier ask. A rule they set may refuse it with the rule's words: act on "
    "the rule instead. `withdraw` takes your open ask back.",
    {
        "id": TASK_ID,
        "to": _s("The person you ask: their id"),
        "line": _s("The one sentence they are asked, readable on its own", maxLength=300),
        "kind": {
            "type": "string",
            "enum": ["answer", "ok"],
            "description": "answer (the default): a question; ok: they approve it or send it back with a note",
        },
        "choices": {
            "type": "array",
            "items": {"type": "string", "maxLength": 60},
            "minItems": 2,
            "maxItems": 6,
            "description": "The answers to pick from; they may also type their own",
        },
        "show": {
            "type": "array",
            "items": _s("page:<id>, file:<id>@<version> or a link"),
            "maxItems": 10,
            "description": "What they look at to answer, shown with the ask",
        },
        "subject": _s(
            "An OK bound to what you will do, as <kind>:<id> (budget_raise:launch-video): the OK covers exactly "
            "`content`, and you use it once with tico_ok_use just before you act"
        ),
        "content": {"description": "With subject: exactly what the OK covers (any JSON)"},
        "withdraw": {"type": "boolean", "description": "Take back your open ask on this task instead"},
        "requirement": _s("With withdraw: the ask's requirement id, when more than one may be withdrawn"),
        "why": _s("With withdraw: why, in a sentence"),
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
    body = {"to": args["to"], "line": args["line"], "version": _seen(args)}
    if args.get("subject"):
        if args.get("choices") or args.get("kind") == "answer" or "content" not in args:
            raise ValueError("An OK bound to a subject is an ok with its content, never a question with choices")
        body.update({"subject": args["subject"], "content": args["content"]})
        if args.get("show"):
            body["show"] = args["show"]
        return api.post(f"tasks/{args['id']}/oks", body, key=_key(args))
    for field in ("kind", "choices", "show"):
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
    "Answer what you are asked on a task (a bot named on an OK or a question): ok, no (send it back, with a note), "
    "again (look again), or your answer. The requirement id is in tico_task_show's To leave. A stage's answer is "
    "named there (confidence): give the name instead, and a whole number in its range, one of its choices, or text.",
    {
        "id": TASK_ID,
        "requirement": _s("The requirement's id, or a stage's answer by its name"),
        "value": _s("ok, no, again, or your answer: one of its choices or your own words"),
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
    "Yours by default, in General unless you name a pipeline. Sub-tasks nest at most 5 deep.",
    {
        "parent": TASK_ID,
        "title": _s("What the new task is for, in plain words"),
        "body": _s("The details; default the title"),
        "owner": _s("Who does it: a bot slug, a person id, or `me` (the default)"),
        "pipeline": _s("The pipeline's id; default general"),
        "due": _s("ISO-8601 date-time with timezone"),
        "labels": {"type": "array", "items": {"type": "string"}, "description": "Lower-case labels"},
        "private": {"type": "boolean", "description": "Only its requester, owner and readers see it"},
        "after": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tasks it merges after (ids or 8+ leading characters): in Merge its PRs are not queued until "
            "each has merged. Build and review go ahead side by side",
        },
    },
    required=("parent", "title"),
    writes=True,
)
def task_file(api, args):
    fields = ("parent", "title", "pipeline", "due", "labels", "private", "after", "operation_id")
    # A task has a body: with none given, its title is its body, as for a sub-task made with tico_task_child_create.
    body = args.get("body") or args["title"]
    return task_create(
        api, {**{k: args[k] for k in fields if k in args}, "body": body, "owner": args.get("owner") or "me"}
    )


@tool(
    "tico_task_list",
    "Tasks you may see, filtered; each has its full `id`, an 8-character `short_id` that every task tool accepts, its "
    "`version` (send it with a change), its `stage_id` and its `turn` (whose turn it is, and the line why). Your own "
    "come back in queue order: the first is what to do next. `all` also returns every bot you may see (`{tasks, "
    "bots}`).",
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
        "brief": {"type": "boolean", "description": "Leave out each task's body"},
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
    page = api.get(
        "tasks",
        owner=_target(api, args.get("owner")) if args.get("owner") else None,
        requester=_target(api, args.get("requester")) if args.get("requester") else None,
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
    "Edit a task's fields: title, body, due, labels, privacy, goal, with a note. Where it is and who has it change "
    "by tico_task_done, tico_task_move, tico_task_close and tico_task_reassign, never here.",
    {
        "id": TASK_ID,
        "title": _s("A new title; it is checked as a new task's title would be"),
        "body": _s("A new body"),
        "due": _s("ISO-8601 date-time with timezone"),
        "labels": {"type": "array", "items": {"type": "string"}, "description": "Replace the labels"},
        "private": {
            "type": "boolean",
            "description": "Tighten visibility; only the direct human requester can publish",
        },
        "goal_id": _s("The goal this task serves; an empty string takes it off"),
        "note": _s("What changed, in a sentence"),
        "version": VERSION,
        "on_behalf_of": ASKED,
    },
    required=("id",),
    writes=True,
)
def task_update(api, args):
    body = {"version": _seen(args)}
    for field in ("title", "body", "due", "labels", "private", "goal_id", "note"):
        if args.get(field) is not None:
            body[field] = args[field]
    links = s3links.apply(api, body, ("note",))
    return s3links.annotate(_asked(api, args).post("tasks/" + args["id"], body, key=_key(args)), links)


RELATION_KINDS = ("related", "duplicate_of", "follow_up")


def _after(args):
    """The tasks a new task merges after: the tool's list, or the CLI's repeated or comma-separated `--after`."""
    values = args.get("after") or []
    if isinstance(values, str):
        values = [values]
    return [part.strip().lstrip("#") for value in values for part in str(value).split(",") if part.strip()]


BLOCKING_GONE = (
    'A task does not wait on another by a relation: file a sub-task (tico task file --parent <id> "title") or ask a '
    "person (tico task ask)."
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
    "Queue again: on the Merge stage, after GitHub took a PR out of the merge queue (or refused it), put it back at "
    "its newest commit. Refused while its checks are running or failing there. Push a fix to the PR branch first when "
    "it needs one; or move the task back instead.",
    {"id": TASK_ID, "version": VERSION, "on_behalf_of": ASKED},
    required=("id",),
    writes=True,
)
def task_queue(api, args):
    return _asked(api, args).post(f"tasks/{args['id']}/queue", {"version": _seen(args)}, key=_key(args))


@tool(
    "tico_task_release",
    "Cut the release a task in Release waits for: Tico runs the repository's release workflow, or creates the tag you "
    "name where it releases from a tag, through its GitHub App. One release covers every task waiting on that "
    "repository. Refused before a person signed the task off, and while an earlier ask still waits for its release.",
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
    "duplicate_of, follow_up (it was split off or followed up from task). A relation is a note: a task waits only on "
    "its requirements and the tasks it filed (tico_task_file), and its parent is set when it is filed. `remove` takes "
    "the relation off. The reply's `readback` says what was stored, both ends: check it before reporting the link as "
    "fixed.",
    {
        "id": TASK_ID,
        "task": {**TASK_ID, "description": "The other task: its id or 8+ leading characters"},
        # No enum: blocks and blocked_by are answered with what to do instead, not with a list of choices.
        "kind": _s("related (the default), duplicate_of or follow_up", default="related"),
        "remove": {"type": "boolean", "default": False},
    },
    required=("id", "task"),
    writes=True,
)
def task_relate(api, args):
    kind = args.get("kind") or "related"
    if kind in ("blocks", "blocked_by"):
        raise ValueError(BLOCKING_GONE)
    if kind not in RELATION_KINDS:
        raise ValueError(f"A relation is {', '.join(RELATION_KINDS)}, not {kind}")
    return api.post(
        f"tasks/{args['id']}/relations",
        {"task": args["task"], "kind": kind, "remove": bool(args.get("remove"))},
        key=_key(args),
    )


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
