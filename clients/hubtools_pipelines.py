"""Pipelines, stages and what it takes to leave them, as tools in the one table (clients/hubtools.py).

What a person's agent reads to work a company from a terminal (docs/terminal.md): the pipelines and their stages, who
holds each stage, what it takes to leave it, the company's shape in one read, a request filed where it belongs, a
person's OK or send back on the task that waits on them, and `tico task wait` (a shell's watcher, never a tool: one
call that blocks for minutes has no place in a bot's turn or an MCP call). Every read and write is an ordinary API
call made as the caller, so the server decides who may do what.

A side module of the tool table. How one joins it (clients/hubtools_<area>.py does the same):

  1. define tools here with `hubtools.tool`; they append to `hubtools.TOOLS` like any other;
  2. end with `_register()`: the module's AUDIENCE and SHELL_ONLY lines join hubtools', and BY_NAME is refreshed;
  3. hubtools.py imports the module on its last line, so every client of the table (the CLI, both MCP doors, the
     tests) sees its tools; importing the module first works too;
  4. the command lines live in clients/hubcli_<area>.py (stdlib only, so `tico --help` stays fast), called once from
     hubcli.parser(); remotecli.run sends its COMMANDS through `via_tool`.
"""

import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

from clients import cli_plumbing
from clients import hubtools as T
from clients.hubtools import PEOPLE, TASK_ID, _key, _s, task_create, tool
from clients.tico import APIError

PIPELINE = _s("The pipeline: its id or its name, any case (product, Engineering)")
STAGE = _s("The stage's id (product-spec, engineering-review-1); tico_pipeline_show lists them")
WHOEVER = "whoever the task is for"  # a stage held by `assignee`: each task's own owner holds it


# ----------------------------------------------------------------------------- reading the pipelines
def _bots(api):
    found = api.get("bots")
    return found if isinstance(found, list) else (found or {}).get("bots") or []


def _pipelines(api, bots=None):
    """Every pipeline's settings record, and the names of the people and bots they name: the answer's own map holds
    the stages' holders, the bots list the rest (a manager, a bot named by its slug)."""
    found = api.get("pipelines")
    names = {}
    for b in _bots(api) if bots is None else bots:
        for ref in (b.get("id"), b.get("slug")):
            if ref:
                names[f"bot:{ref}"] = b.get("display_name") or b.get("name") or b.get("slug")
    return (found.get("pipelines") or []), {**names, **(found.get("actors") or {})}


def _pipeline(api, ref):
    """One pipeline by id or name (any case), with the names map. Not found: the ids there are, in the refusal."""
    rows, actors = _pipelines(api)
    wanted = str(ref or "").strip().casefold()
    for row in rows:
        if wanted in (str(row.get("id")).casefold(), str(row.get("name") or "").casefold()):
            return row, actors
    ids = ", ".join(str(r.get("id")) for r in rows if not r.get("retired_at"))
    raise APIError("not_found", f"No pipeline {ref!r}; the pipelines are: {ids}", 404)


def _holder(stage, actors):
    owner = stage.get("owner") or ""
    if owner == "assignee":
        return WHOEVER
    return stage.get("owner_name") or actors.get(owner) or owner


def requirement_line(req):
    """One requirement as a person reads it: its line, or what its kind asks, and who gives an OK on it."""
    kind = req.get("kind")
    said = req.get("line") or {
        "owner_done": "its owner says the work is done",
        "linked_done": "the tasks it filed are done",
        "item": " ".join(str(x) for x in (req.get("source"), req.get("condition")) if x).replace("_", " ")
        or "a linked item is ready",
    }.get(kind, kind or "")
    who = {"people_only": "a person's OK", "people_with_exceptions": "an OK"}.get(req.get("who_oks"))
    return f"{said} ({who})" if who else said


def _stage(stage, actors):
    return {
        "id": stage.get("id"),
        "name": stage.get("name"),
        "held_by": stage.get("owner"),
        "held_by_name": _holder(stage, actors),
        "by_hand": bool(stage.get("by_hand")),
        "task_limit": stage.get("task_limit"),
        "to_leave": [
            {
                "requirement_id": r.get("id"),
                "kind": r.get("kind"),
                "line": requirement_line(r),
                "who_oks": r.get("who_oks"),
                "person": actors.get(r.get("person_id") or "") or r.get("person_id"),
                "can_push_past": r.get("can_push_past"),
            }
            for r in stage.get("requirements") or []
        ],
    }


def _shape(row, actors):
    manager = row.get("manager_id")
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "goal": row.get("goal"),
        "manager": manager,
        "manager_name": actors.get(manager or "") or manager,
        "private": bool(row.get("private")),
        "retired_at": row.get("retired_at"),
        "settings_version": row.get("settings_version"),
        "may_change": row.get("may_change"),
        "stages": [_stage(s, actors) for s in row.get("stages") or []],
    }


@tool(
    "tico_pipeline_list",
    "The company's pipelines, each with its manager and its stages in order (id, name, who holds it). Anyone on the "
    "team may read them; a private pipeline's tasks stay private. Retired pipelines only with `retired`. "
    "Example: tico pipeline list",
    {"retired": {"type": "boolean", "description": "Also list retired pipelines"}},
)
def pipeline_list(api, args):
    rows, actors = _pipelines(api)
    return {
        "pipelines": [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "manager_name": actors.get(row.get("manager_id") or "") or row.get("manager_id"),
                "private": bool(row.get("private")),
                **({"retired_at": row["retired_at"]} if row.get("retired_at") else {}),
                "stages": [
                    {"id": s.get("id"), "name": s.get("name"), "held_by_name": _holder(s, actors)}
                    for s in row.get("stages") or []
                ],
            }
            for row in rows
            if args.get("retired") or not row.get("retired_at")
        ]
    }


@tool(
    "tico_pipeline_show",
    "One pipeline, by id or name: its goal, manager, settings version and every stage in order, with who holds it, "
    "whether tasks leave it by hand, its task limit and what it takes to leave it (each requirement and who gives "
    "its OK). Anyone on the team may read it. Example: tico pipeline show product",
    {"pipeline": PIPELINE},
    required=("pipeline",),
)
def pipeline_show(api, args):
    row, actors = _pipeline(api, args["pipeline"])
    return {"pipeline": _shape(row, actors)}


@tool(
    "tico_pipeline_log",
    "What happened in a pipeline lately: the manager's Log (and Learned), only over the tasks you may read. "
    "Example: tico pipeline log engineering",
    {"pipeline": PIPELINE},
    required=("pipeline",),
)
def pipeline_log(api, args):
    row, _ = _pipeline(api, args["pipeline"])
    return {"pipeline": row.get("id"), **api.get(f"pipelines/{quote(str(row['id']), safe='')}/log")}


@tool(
    "tico_stage_show",
    "One stage, by its id: the pipeline it is in, who holds it, whether tasks leave it by hand, its task limit, what "
    "it takes to leave it, and the stages before and after it. Anyone on the team may read it. To list the tasks in "
    "it: tico_task_list with `stage`. Example: tico stage show product-spec",
    {"stage": STAGE},
    required=("stage",),
)
def stage_show(api, args):
    rows, actors = _pipelines(api)
    wanted = str(args["stage"]).strip()
    for row in rows:
        stages = row.get("stages") or []
        for at, stage in enumerate(stages):
            if stage.get("id") == wanted:
                near = lambda s: {"id": s.get("id"), "name": s.get("name")} if s else None
                return {
                    "stage": _stage(stage, actors),
                    "purpose": stage.get("purpose"),
                    "pipeline": {"id": row.get("id"), "name": row.get("name")},
                    "before": near(stages[at - 1] if at else None),
                    "after": near(stages[at + 1] if at + 1 < len(stages) else row.get("done")),
                }
    raise APIError("not_found", f"No stage {wanted!r}; tico pipeline show <pipeline> lists a pipeline's stages", 404)


@tool(
    "tico_company_show",
    "The company in one read: its name, address and release; who you are in it; every pipeline with its manager and "
    "stages (who holds each, what it takes to leave it); and every bot with the stages it holds, whether it is on and "
    "its open tasks. Anyone on the team may read it. Example: tico company show",
    {"retired": {"type": "boolean", "description": "Also show retired pipelines"}},
)
def company_show(api, args):
    config, me, bots = api.get("config"), api.get("me"), _bots(api)
    rows, actors = _pipelines(api, bots)
    pipelines = [_shape(row, actors) for row in rows if args.get("retired") or not row.get("retired_at")]
    holds = {}
    for p in pipelines:
        for s in p["stages"]:
            holds.setdefault(s["held_by"], []).append(s["id"])
        holds.setdefault(p["manager"], []).append(f"{p['id']} (manager)")
    return {
        "company": {
            "name": config.get("company_name"),
            "url": config.get("public_url"),
            "release": config.get("release"),
        },
        "me": {"actor": me.get("actor"), "name": me.get("actor_name"), "role": me.get("role"), "kind": me.get("kind")},
        "pipelines": pipelines,
        "bots": [
            {
                "id": b.get("id"),
                "slug": b.get("slug"),
                "name": b.get("display_name"),
                "state": b.get("state"),
                "online": b.get("online"),
                "open_tasks": (b.get("status") or {}).get("open_tasks"),
                "reports_to": b.get("reports_to"),
                "holds": list(
                    dict.fromkeys([*holds.get(f"bot:{b.get('id')}", []), *holds.get(f"bot:{b.get('slug')}", [])])
                ),
            }
            for b in bots
        ],
    }


# ----------------------------------------------------------------------------- filing a request, a person's answer
@tool(
    "tico_task_request",
    "File a request into a pipeline: a task that starts in the pipeline's first stage, given to whoever holds that "
    "stage (a stage held by whoever the task is for needs `owner`). Product requests go to product. Prints the task "
    "with its id and version; watch it with `tico task wait <id>`. A person or a bot may file one. "
    'Example: tico task request product "Add a negate(x) function" --body "Why and what done looks like"',
    {
        "pipeline": PIPELINE,
        "title": _s("What is asked, in plain words"),
        "body": _s("The details: why, and what done looks like; default the title"),
        "owner": _s("Who works it first instead of the stage's holder: a bot slug, a person id, or `me`"),
        "private": {"type": "boolean", "description": "Only its requester, owner and readers see it"},
    },
    required=("pipeline", "title"),
    writes=True,
)
def task_request(api, args):
    row, _ = _pipeline(api, args["pipeline"])
    stages = row.get("stages") or []
    holder = (stages[0].get("owner") if stages else None) or ""
    owner = args.get("owner") or (holder if ":" in holder else None)
    if not owner:
        raise ValueError(f"{row.get('name')}'s first stage is held by {WHOEVER}: add --owner <bot or person>")
    fields = {"owner": owner, "title": args["title"], "body": args.get("body") or args["title"], "pipeline": row["id"]}
    for name in ("private", "operation_id"):
        if args.get(name) is not None:
            fields[name] = args[name]
    return task_create(api, fields)


def _my_ask(api, args, value):
    """The task and the one unmet requirement on it the caller may answer with `value` (ok or no), or a refusal that
    lists what the task waits on."""
    shown = api.get(f"tasks/{args['id']}")
    task = shown.get("task") if isinstance(shown.get("task"), dict) else shown
    open_ = [e for e in task.get("to_leave") or [] if not e.get("met")]
    if args.get("requirement"):
        open_ = [e for e in open_ if args["requirement"] in (e.get("requirement_id"), e.get("line"))]
    mine = [e for e in open_ if value in (e.get("may") or [])]
    if len(mine) == 1:
        return task, mine[0]
    listed = "; ".join(
        f"{e.get('requirement_id')} {e.get('line') or e.get('kind')} (you may: {', '.join(e.get('may') or []) or 'nothing'})"
        for e in open_
    )
    if not mine:
        raise ValueError(f"Nothing on this task waits for your {value}" + (f": {listed}" if listed else ""))
    raise ValueError(f"More than one requirement takes your {value}; add --requirement <id>: {listed}")


def _answer(api, args, value, note):
    task, entry = _my_ask(api, args, value)
    body = {"value": value, "version": args.get("version") or task.get("version")}
    if note:
        body["note"] = note
    page = entry.get("page") or {}
    if page.get("file_id") and page.get("version"):
        body["page_version"] = f"{page['file_id']}@{page['version']}"  # the OK is for the version you were shown
    path = f"tasks/{task.get('id') or args['id']}/asks/{quote(str(entry['requirement_id']), safe='')}/answer"
    done = api.post(path, body, key=_key(args))
    shown = {"requirement_id": entry["requirement_id"], "line": entry.get("line"), "page": page.get("ref")}
    return {**(done if isinstance(done, dict) else {"result": done}), "answered": shown, "value": value}


ANSWER_FIELDS = {
    "id": TASK_ID,
    "requirement": _s("The requirement's id or line, when more than one waits on you (tico task show: To leave)"),
    "version": {"type": "integer", "minimum": 0, "description": "The task's version you read; default the latest"},
}


@tool(
    "tico_task_approve",
    "Give your OK on a task that waits on you: the one requirement in its To leave you may OK (a page sign-off is "
    "for the page version attached now). The person named on it, or someone who may answer for them; an OK a "
    "requirement keeps for people is refused to a personal token (give it in Tico). "
    'Example: tico task approve 3508775e -m "Looks right"',
    {**ANSWER_FIELDS, "note": _s("What they read with it")},
    required=("id",),
    writes=True,
)
def task_approve(api, args):
    return _answer(api, args, "ok", args.get("note"))


@tool(
    "tico_task_send_back",
    "Send a task back from what waits on you: a no on the one requirement you may answer, with a note saying what to "
    "change. The person named on it. "
    'Example: tico task send-back 3508775e -m "Say what happens to negative zero"',
    {**ANSWER_FIELDS, "note": _s("What to change, word for word; required")},
    required=("id", "note"),
    writes=True,
)
def task_send_back(api, args):
    if not str(args.get("note") or "").strip():
        raise ValueError('Say what to change: -m "..."')
    return _answer(api, args, "no", args["note"])


# ----------------------------------------------------------------------------- tico task wait (a shell's, not a tool)
TURN_STATES = ("snoozed", "person", "paused", "bot", "jev", "source", "tasks", "room", "by_hand")
# The CLI's exit codes (clients/cli_plumbing.py, docs/cli.md), and one of the waiter's own: the task ended without it.
EXIT_MET, EXIT_ENDED, EXIT_TIMEOUT = cli_plumbing.EXIT_OK, 6, cli_plumbing.EXIT_TIMEOUT
TREE_MAX = 20  # with --tree, at most this many tasks are watched


def seconds(text, default):
    """`90`, `90s`, `15m`, `2h` or `1d` as seconds."""
    text = str(text or "").strip().lower()
    if not text:
        return default
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(text[-1])
    try:
        return float(text[:-1]) * unit if unit else float(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a time: use 90s, 15m, 2h or 1d") from None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark(task, stage):
    """What a change is: the stage, status, version, and whose turn it is."""
    turn = task.get("turn") or {}
    return stage, task.get("status"), task.get("version"), turn.get("state"), turn.get("who")


class Watch:
    """`tico task wait`: one task (and with --tree, the tasks it filed) read every few seconds; a JSON line for each
    change; done when a condition is met, the task ends, or time is up."""

    def __init__(self, api, args, out=None, sleep=time.sleep, clock=time.monotonic):
        self.api, self.args = api, args
        self.out = out or (lambda line: print(line, flush=True))
        self.sleep, self.clock = sleep, clock
        self.tasks = {}  # id -> the task as tasks/{id} gives it
        self.names = {}
        self.me = None
        self.order = {}  # stage id -> (pipeline id, its place in the pipeline); Done comes after the last stage

    def emit(self, event, task=None, **more):
        line = {"event": event, "at": _now()}
        if task:
            turn = task.get("turn") or {}
            stage = task.get("stage") or {}
            to_leave = task.get("to_leave") or []
            line.update(
                task=task.get("id"),
                number=task.get("number"),
                title=task.get("title"),
                pipeline=(task.get("pipeline") or {}).get("id"),
                stage=stage.get("id"),
                stage_name=stage.get("name"),
                status=task.get("status"),
                version=task.get("version"),
                turn=turn.get("state"),
                who=self.names.get(turn.get("who") or "") or turn.get("who"),
                line=turn.get("line"),
                missing=[e.get("line") or e.get("kind") for e in to_leave if not e.get("met")],
            )
            yours = self.yours(task)
            if yours:
                line["yours"] = yours
        line.update(more)
        self.out(json.dumps({k: v for k, v in line.items() if v not in (None, [], "")}, default=str))

    def yours(self, task):
        """What on this task waits on the caller: each requirement named on them they may answer, and its id."""
        if not self.me:
            return []
        return [
            {"requirement_id": e.get("requirement_id"), "line": e.get("line"), "may": e.get("may")}
            for e in task.get("to_leave") or []
            if not e.get("met") and e.get("who") == self.me and e.get("may")
        ]

    def read(self, task_id):
        shown = self.api.get(f"tasks/{task_id}")
        task = shown.get("task") if isinstance(shown.get("task"), dict) else shown
        self.names.update(shown.get("actors") or {})
        self.tasks[task["id"]] = task
        return task

    def follow_filed(self):
        """With --tree: start watching each task a watched task filed, up to TREE_MAX."""
        if not self.args.get("tree"):
            return
        for task in list(self.tasks.values()):
            for filed in task.get("filed") or []:
                fid = filed.get("id") if isinstance(filed, dict) else filed
                if fid and fid not in self.tasks and len(self.tasks) < TREE_MAX:
                    self.emit("filed", self.read(fid), filed_by=task.get("id"))

    def stages(self):
        """Where each stage sits in its pipeline, so a task that went past the stage asked for has reached it."""
        for row in self.api.get("pipelines").get("pipelines") or []:
            listed = [*(row.get("stages") or []), *([row["done"]] if row.get("done") else [])]
            for at, stage in enumerate(listed):
                self.order[stage.get("id")] = (row.get("id"), at)
        unknown = [s for s in self.args["stage"] if s not in self.order]
        if unknown:
            raise ValueError(f"No stage {', '.join(unknown)}: --stage takes a stage id (tico pipeline show <pipeline>)")

    def met(self, task):
        """Why this task meets what was asked, or None."""
        stages, statuses, turns = (self.args.get(k) or [] for k in ("stage", "status", "turn"))
        stage = (task.get("stage") or {}).get("id")
        here = self.order.get(stage)
        for wanted in stages:
            there = self.order[wanted]
            if here and here[0] == there[0] and here[1] >= there[1]:
                return f"stage {wanted}" + (f" (now {stage})" if stage != wanted else "")
        if task.get("status") in statuses:
            return f"status {task['status']}"
        if task.get("status") == "open" and (task.get("turn") or {}).get("state") in turns:
            return f"turn {task['turn']['state']}"
        if self.args.get("needs_me") and (
            self.yours(task)
            or (task.get("turn") or {}).get("who") == self.me
            and (task.get("turn") or {}).get("state") == "person"
        ):
            return "needs you"
        return None

    def asked(self):
        return any(self.args.get(k) for k in ("stage", "status", "turn", "needs_me"))

    def poll(self):
        """One list read of every watched task; a task whose stage, status, version or turn moved is read whole and
        printed. The whole read (tasks/{id}) is the heavy one, so it is made only on a change."""
        rows = self.api.get("tasks", ids=",".join(self.tasks)).get("tasks") or []
        for row in rows:
            task = self.tasks.get(row.get("id"))
            if task and mark(row, row.get("stage_id")) != mark(task, (task.get("stage") or {}).get("id")):
                self.emit("change", self.read(row["id"]))
        self.follow_filed()

    def run(self):
        timeout = seconds(self.args.get("timeout"), 1800)
        every = max(seconds(self.args.get("every"), 15), 2)
        deadline = self.clock() + timeout
        if self.args.get("needs_me"):
            self.me = self.api.get("me").get("actor")
        if self.args.get("stage"):
            self.stages()
        root = self.read(self.args["id"])
        self.root = root["id"]
        self.emit("start", root)
        self.follow_filed()
        while True:
            for task in self.tasks.values():
                why = self.met(task)
                if why:
                    self.emit("met", task, reason=why)
                    return EXIT_MET
            root = self.tasks[self.root]
            if root.get("status") != "open":
                if not self.asked():
                    self.emit("met", root, reason=f"status {root.get('status')}")
                    return EXIT_MET
                if all(t.get("status") != "open" for t in self.tasks.values()):
                    self.emit("ended", root, reason="it ended without meeting what you waited for")
                    return EXIT_ENDED
            if self.clock() >= deadline:
                self.emit("timeout", root, reason=f"not met after {self.args.get('timeout') or '30m'}")
                return EXIT_TIMEOUT
            self.sleep(min(every, max(deadline - self.clock(), 0)))
            try:
                self.poll()
            except APIError as exc:
                self.emit("error", error=exc.code, detail=exc.detail, retryable=exc.retryable)
                if not exc.retryable:
                    return cli_plumbing.exit_code(exc)


def task_wait(api, args, **hooks):
    """`tico task wait`: the exit code. A bot in its turn is refused before this (hubcli)."""
    for name in ("stage", "status", "turn"):
        args[name] = [v for one in args.get(name) or [] for v in str(one).split(",") if v.strip()]
    bad = [t for t in args["turn"] if t not in TURN_STATES]
    if bad:
        raise ValueError(f"--turn takes {', '.join(TURN_STATES)}; not {', '.join(bad)}")
    return Watch(api, args, **hooks).run()


def command(args):
    """The shell side of `tico task wait`: the run's credential, one line per change on stdout, its exit code."""
    from clients.tico import Client

    api = Client(os.environ["HUB_API_URL"], os.environ.get("HUB_TOKEN", ""), timeout=30)
    fields = {
        k: getattr(args, k, None) for k in ("id", "stage", "status", "turn", "needs_me", "tree", "timeout", "every")
    }
    try:
        return task_wait(api, fields)
    except ValueError as exc:
        print(json.dumps({"event": "error", "error": "usage", "detail": str(exc)}), flush=True)
        return cli_plumbing.EXIT_USAGE
    except APIError as exc:
        print(
            json.dumps({"event": "error", "error": exc.code, "detail": exc.detail, "retryable": exc.retryable}),
            flush=True,
        )
        return cli_plumbing.exit_code(exc)


# ----------------------------------------------------------------------------- joining the table
AUDIENCE = {
    name: PEOPLE  # what a person's agent runs; a bot reads its own task with tico_task_show
    for name in (
        "tico_pipeline_list",
        "tico_pipeline_show",
        "tico_pipeline_log",
        "tico_stage_show",
        "tico_company_show",
        "tico_task_request",
        "tico_task_approve",
        "tico_task_send_back",
    )
}
SHELL_ONLY = {"tico_task_wait"}


def _register():
    T.AUDIENCE.update(AUDIENCE)
    T.SHELL_ONLY.update(SHELL_ONLY)
    T.BY_NAME.update({t["name"]: t for t in T.TOOLS})


_register()
