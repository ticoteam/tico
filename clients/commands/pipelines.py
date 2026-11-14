"""Pipelines, stages and what it takes to leave them, as tools in the one table (clients/hubtools.py).

What a person's agent reads to work a company from a terminal (docs/terminal.md): the pipelines and their stages, who
holds each stage, what it takes to leave it, the company's shape in one read, a request filed where it belongs, a
person's OK or send back on the task that waits on them, and `tico task wait` (a shell's watcher, never a tool: one
call that blocks for minutes has no place in a bot's turn or an MCP call). Every read and write is an ordinary API
call made as the caller, so the server decides who may do what.

Listed in clients/commands/__init__.py like any other tool module; `_access` merges its AUDIENCE and SHELL_ONLY lines.
CLI is the `tico pipeline`, `stage`, `company` and `requirement` command lines as specs (clients/cli_spec.py); the
`tico task request|approve|send-back|wait` ones are still built by hand in clients/hubcli_pipelines.py.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

from clients import cli_plumbing
from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._requester import PEOPLE
from clients.commands._schemas import TASK_ID, _key, _s
from clients.commands.tasks import task_create
from clients.tico import APIError

PIPELINE = _s("The pipeline: its id or its name, any case (product, Engineering)")
STAGE = _s("The stage's id (product-spec, engineering-review-1), from `pipeline show`")
WHOEVER = "whoever the task is for"  # a stage held by `assignee`: each task's own owner holds it
PICK = "pick"  # a stage whose owner is "One of several": Tico picks one of `pick.bots` as a task arrives (backend/stage_pick.py)


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


def _find(rows, ref):
    """One pipeline's record by id or name (any case). Not found: the ids there are, in the refusal."""
    wanted = str(ref or "").strip().casefold()
    for row in rows:
        if wanted in (str(row.get("id")).casefold(), str(row.get("name") or "").casefold()):
            return row
    ids = ", ".join(str(r.get("id")) for r in rows if not r.get("retired_at"))
    raise APIError("not_found", f"No pipeline {ref!r}; the pipelines are: {ids}", 404)


def _pipeline(api, ref):
    """One pipeline by id or name, with the names map."""
    rows, actors = _pipelines(api)
    return _find(rows, ref), actors


def _picks_from(stage):
    """The bots a stage that picks its owner picks from, or () for a stage with one owner."""
    pick = stage.get("pick") if stage.get("owner") == PICK else None
    return tuple((pick or {}).get("bots") or ()) if isinstance(pick, dict) else ()


def _holder(stage, actors):
    owner = stage.get("owner") or ""
    if owner == "assignee":
        return WHOEVER
    if owner == PICK:
        return "One of several: " + ", ".join(actors.get(b) or b for b in _picks_from(stage))
    return stage.get("owner_name") or actors.get(owner) or owner


def requirement_line(req):
    """One requirement as a person reads it: its line, or what its kind asks, and who gives an OK on it."""
    kind = req.get("kind")
    said = req.get("line") or {
        "owner_done": "its owner says the work is done",
        "linked_done": "the tasks it filed are done",
        "related_input": "the bots Jev picks have commented, or its wait ran out",
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
        **_pick_shown(stage, actors),
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


def _pick_shown(stage, actors):
    """A stage's "One of several" as the API gives it (the bots, Take turns or Random, Limit each, Prefer, whether the
    pick becomes the task's owner, each bot's model family) with the bots' names, and `pick_next`: who gets the next
    task, in one sentence. Nothing for a stage with one owner."""
    if not _picks_from(stage):
        return {}
    pick = dict(stage["pick"])
    pick["bot_names"] = [actors.get(b) or b for b in pick.get("bots") or []]
    return {"pick": pick, "pick_next": stage.get("pick_next") or ""}


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
    "its OK). `record` gives the whole settings record instead, as tico_pipeline_save takes it. Anyone on the team "
    "may read it. Example: tico pipeline show product",
    {
        "pipeline": PIPELINE,
        "record": {"type": "boolean", "description": "The whole settings record, to change and save whole"},
    },
    required=("pipeline",),
)
def pipeline_show(api, args):
    if args.get("record"):
        return {"pipeline": _record(api, args["pipeline"])}
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
            for who in (s.get("pick") or {}).get("bots") or [s["held_by"]]:
                holds.setdefault(who, []).append(s["id"])
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
    "stage (a stage held by whoever the task is for needs `owner`; a stage that picks from several picks one, unless "
    "`owner` names one). Product requests go to product. Prints the task "
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
    if not owner and holder != PICK:
        raise ValueError(f"{row.get('name')}'s first stage is held by {WHOEVER}: add --owner <bot or person>")
    # A first stage that picks from several picks the owner as the task arrives; naming one always wins.
    fields = {"title": args["title"], "body": args.get("body") or args["title"], "pipeline": row["id"]}
    if owner:
        fields["owner"] = owner
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


# ----------------------------------------------------------------------------- changing pipelines, stages, requirements
# Each change reads the pipeline's whole record, changes it, and saves it whole with the settings version it read
# (POST pipelines/{id}, backend/pipeline_settings.py): the server checks the record and refuses the first problem in a
# sentence, and a change someone made since is refused, not overwritten. The company's owner and Admins, the
# pipeline's owner and its manager may change a pipeline; only the owner and Admins make one.
VERSION_SEEN = {
    "type": "integer",
    "minimum": 0,
    "description": "The pipeline's settings_version you read (`pipeline show`); default the one read now",
}
CONFIRM = _s("The exact name of what is removed, typed again: nothing is removed without it")
HOLDER = _s("Who holds it: a bot slug, bot:<id>, human:<id>, `me`, or `assignee` (whoever the task is for)")
WHO_OKS = {
    "type": "string",
    "enum": ["people_only", "people_with_exceptions", "none"],
    "description": "Who gives the OK on a page or an OK: people_only (never a bot), people_with_exceptions, none",
}


def _record(api, ref):
    """The pipeline's whole settings record, by id or name (no names map: a write needs none)."""
    return _find(api.get("pipelines").get("pipelines") or [], ref)


def _where(api, stage_id=None, requirement_id=None):
    """The record holding a stage (or a requirement), and the stage's place in it."""
    for row in api.get("pipelines").get("pipelines") or []:
        for at, stage in enumerate(row.get("stages") or []):
            if stage.get("id") == stage_id or any(
                r.get("id") == requirement_id for r in stage.get("requirements") or [] if requirement_id
            ):
                return row, at
    what = f"stage {stage_id!r}" if stage_id else f"requirement {requirement_id!r}"
    raise APIError("not_found", f"No {what}; tico pipeline show <pipeline> lists the stages and requirements", 404)


def _actor(api, value):
    """A holder, manager or person as the record names them."""
    value = str(value or "").strip()
    if value in ("me", "self"):
        return api.get("me")["actor"]
    if value in ("assignee", "whoever"):
        return "assignee"
    return value if ":" in value else f"bot:{value}"


def _save(api, record, args, made=False):
    """Save the whole record; the answer is the record as saved."""
    body = {k: v for k, v in record.items() if k not in ("may_change", "retired_at", "settings_version")}
    body["stages"] = [_stage_sent(s) for s in body.get("stages") or []]
    if not made:
        body["settings_version"] = (
            args.get("version") if args.get("version") is not None else record.get("settings_version")
        )
    return api.post(f"pipelines/{quote(str(record['id']), safe='')}", body, key=_key(args))


def _stage_sent(stage):
    """A stage as a save sends it: without what the server adds to a read (`pick_next`, each bot's model family)."""
    stage = {k: v for k, v in stage.items() if k != "pick_next"}
    if isinstance(stage.get("pick"), dict):
        stage["pick"] = {k: v for k, v in stage["pick"].items() if k not in ("families", "bot_names")}
    return stage


def _taken(api, pipeline_id):
    """Whether a pipeline of this id exists already (retired ones count: their ids are never reused)."""
    return any(r.get("id") == pipeline_id for r in api.get("pipelines").get("pipelines") or [])


def _confirmed(args, name):
    if str(args.get("confirm") or "").strip() != str(name or "").strip():
        raise APIError("usage", f"Nothing removed: add --confirm {json.dumps(name)} (its exact name) to remove it")


def _stage_fields(api, args, stage):
    """The stage fields a command line sets, onto `stage`."""
    for arg, field in (("name", "name"), ("purpose", "purpose")):
        if args.get(arg) is not None:
            stage[field] = args[arg]
    _pick_fields(api, args, stage)
    if args.get("by_hand") is not None:
        stage["by_hand"] = bool(args["by_hand"])
    if args.get("no_limit"):
        stage["task_limit"] = None
    elif args.get("limit") is not None:
        stage["task_limit"] = args["limit"]
    if args.get("expected_minutes") is not None:
        stage["expected_minutes"] = args["expected_minutes"] or None
    return stage


PICK_ARGS = ("pick", "limit_each", "no_limit_each", "prefer", "owns_task")


def _pick_fields(api, args, stage):
    """Who holds the stage: one holder, or "One of several" (`pick_from` and its settings, the API's `pick`). A holder
    takes the stage back to one owner; the settings alone change the pick a stage has."""
    bots = [b for b in args.get("pick_from") or [] if str(b or "").strip()]
    settings = [a for a in PICK_ARGS if args.get(a) not in (None, False)] + (
        ["owns_task"] if args.get("owns_task") is False else []
    )
    if args.get("holder") and (bots or settings):
        raise APIError("usage", "A stage has one holder or picks from several: give --holder or --pick-from, not both")
    if args.get("holder"):
        stage["owner"] = _actor(api, args["holder"])
        stage.pop("pick", None)
        stage.pop("pick_next", None)
        return
    if not bots and not settings:
        return
    if not bots and stage.get("owner") != PICK:
        raise APIError(
            "usage",
            "--pick, --limit-each, --prefer and --owns-task are for a stage that picks from "
            "several: add --pick-from <bot> for each bot it picks from",
        )
    had = stage.get("pick") if stage.get("owner") == PICK and isinstance(stage.get("pick"), dict) else {}
    pick = {
        "bots": [_actor(api, b) for b in bots] if bots else list(had.get("bots") or []),
        "how": had.get("how") or "turns",
        "limit_each": had.get("limit_each"),
        "prefer": had.get("prefer") or "anyone",
        "owns_task": bool(had.get("owns_task")),
    }
    if args.get("pick"):
        pick["how"] = args["pick"]
    if args.get("no_limit_each"):
        pick["limit_each"] = None
    elif args.get("limit_each") is not None:
        pick["limit_each"] = args["limit_each"]
    if args.get("prefer"):
        pick["prefer"] = args["prefer"]
    if args.get("owns_task") is not None:
        pick["owns_task"] = bool(args["owns_task"])
    stage["owner"], stage["pick"] = PICK, pick


def _place(stages, stage, args):
    """Put `stage` where --after or --first says, else last (a new one) or where it is (a moved one)."""
    if args.get("first"):
        return [stage, *stages]
    after = args.get("after")
    if after:
        at = next((i for i, s in enumerate(stages) if after in (s.get("id"), s.get("name"))), None)
        if at is None:
            raise APIError("usage", f"No stage {after!r} in this pipeline to go after")
        return [*stages[: at + 1], stage, *stages[at + 1 :]]
    return [*stages, stage]


STAGE_FIELDS = {
    "purpose": _s("What the stage is for, in a sentence"),
    "holder": HOLDER,
    "pick_from": {
        "type": "array",
        "items": {"type": "string"},
        "description": "One of several: the bots it picks the owner from as a task arrives (slugs or bot:<id>); "
        "replaces its holder",
    },
    "pick": {"type": "string", "enum": ["turns", "random"], "description": "One of several: take turns or random"},
    "limit_each": {
        "type": "integer",
        "minimum": 1,
        "description": "One of several: at most this many of its tasks each; when all are full a task waits",
    },
    "no_limit_each": {"type": "boolean", "description": "One of several: take Limit each off"},
    "prefer": {
        "type": "string",
        "enum": ["anyone", "new", "family"],
        "description": "One of several: anyone, one new to the task, or one of a different model family from "
        "everyone who worked on it",
    },
    "owns_task": {
        "type": "boolean",
        "description": "One of several: the pick becomes the task's owner (Build); off, it holds only this stage "
        "(Review)",
    },
    "by_hand": {"type": "boolean", "description": "Tasks leave it only when someone moves them"},
    "limit": {
        "type": "integer",
        "minimum": 1,
        "description": "At most this many open tasks in it (not the first stage)",
    },
    "expected_minutes": {"type": "integer", "minimum": 0, "description": "How long a task should take here; 0 clears"},
}


@tool(
    "tico_pipeline_create",
    "Make a pipeline: its id (lowercase words joined by dashes), name, manager and stages in order, each held by "
    "`assignee` (whoever the task is for) unless a holder is given. The company's owner and Admins. Add requirements "
    'after with tico_requirement_add. Example: tico pipeline create support --name Support --manager success-manager '
    '--stage Triage --stage "Doing:success-manager"',
    {
        "id": _s("The new pipeline's id: lowercase words joined by dashes (support, field-ops)"),
        "name": _s("Its name, as people read it"),
        "goal": _s("What it is for, in a sentence"),
        "manager": _s("Its manager: a bot slug, bot:<id> or human:<id>"),
        "prefix": _s("One or two letters its task numbers start with"),
        "private": {"type": "boolean", "description": "Its tasks are read only by its manager and the company's owner"},
        "stages": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string"},
            "description": "The stages in order, each `Name` or `Name:holder`",
        },
    },
    required=("id", "name", "stages"),
    writes=True,
)
def pipeline_create(api, args):
    if _taken(api, args["id"]):
        raise APIError("usage", f"A pipeline {args['id']!r} is there already: change it with tico pipeline update")
    stages = []
    for text in args["stages"]:
        name, _, holder = str(text).partition(":")
        stages.append({"name": name.strip(), "owner": _actor(api, holder) if holder.strip() else "assignee"})
    record = {"id": args["id"], "name": args["name"], "goal": args.get("goal") or "", "stages": stages}
    if args.get("manager"):
        record["manager_id"] = _actor(api, args["manager"])
    for field in ("prefix", "private"):
        if args.get(field) is not None:
            record[field] = args[field]
    return _save(api, record, args, made=True)


@tool(
    "tico_pipeline_update",
    "Change a pipeline's own fields: name, goal, manager, owner, prefix, private (only while it holds no task). Its "
    "stages change with tico_stage_*. The company's owner and Admins, the pipeline's owner or its manager. "
    'Example: tico pipeline update product --goal "Specs a builder can build from"',
    {
        "pipeline": PIPELINE,
        "name": _s("A new name"),
        "goal": _s("What it is for, in a sentence"),
        "manager": _s("Its manager: a bot slug, bot:<id> or human:<id>"),
        "owner": _s("Its owner: human:<id>, or a bot"),
        "prefix": _s("One or two letters its task numbers start with"),
        "private": {"type": "boolean", "description": "Its tasks are read only by its manager and the company's owner"},
        "version": VERSION_SEEN,
    },
    required=("pipeline",),
    writes=True,
)
def pipeline_update(api, args):
    record = _record(api, args["pipeline"])
    for arg, field in (("name", "name"), ("goal", "goal"), ("prefix", "prefix"), ("private", "private")):
        if args.get(arg) is not None:
            record[field] = args[arg]
    for arg, field in (("manager", "manager_id"), ("owner", "owner_id")):
        if args.get(arg):
            record[field] = _actor(api, args[arg])
    return _save(api, record, args)


@tool(
    "tico_pipeline_save",
    "Make or replace a pipeline from its whole settings record, as tico_pipeline_show's `pipeline` or GET "
    "pipelines/{id} gives it: stages and requirements with an id are changed in place, without one are made, left "
    "out are retired. The company's owner and Admins, the pipeline's owner or its manager. "
    "Example: tico pipeline save support --file support.json",
    {
        "id": _s("The pipeline's id"),
        "record": {"type": "object", "description": "The whole record: name, goal, manager_id, stages, ..."},
        "version": VERSION_SEEN,
    },
    required=("id", "record"),
    writes=True,
)
def pipeline_save(api, args):
    record = {**args["record"], "id": args["id"]}
    seen = args.get("version", record.get("settings_version"))
    if seen is None and _taken(api, args["id"]):
        raise APIError("usage", "Add --version N (its settings_version, tico pipeline show): it is there already")
    return _save(api, record, {**args, "version": seen}, made=seen is None)


@tool(
    "tico_pipeline_remove",
    "Retire a pipeline: it takes no new task, and the tasks that ended in it stay. Refused while it holds an open "
    "task or another pipeline files into it. `confirm` is its exact name. The company's owner and Admins, the "
    "pipeline's owner or its manager. Example: tico pipeline remove support --confirm Support",
    {"pipeline": PIPELINE, "confirm": CONFIRM, "version": VERSION_SEEN},
    required=("pipeline", "confirm"),
    writes=True,
)
def pipeline_remove(api, args):
    record = _record(api, args["pipeline"])
    _confirmed(args, record.get("name"))
    seen = args.get("version") if args.get("version") is not None else record.get("settings_version")
    return api.post(f"pipelines/{quote(str(record['id']), safe='')}/retire", {"settings_version": seen}, key=_key(args))


@tool(
    "tico_stage_add",
    "Add a stage to a pipeline: last, first, or after a stage; held by `assignee` (whoever the task is for) unless "
    "a holder is given. Who may change the pipeline. "
    'Example: tico stage add engineering "Security review" --after engineering-review-2 --holder reviewer-2',
    {
        "pipeline": PIPELINE,
        "name": _s("The stage's name"),
        "after": _s("The stage it comes after: its id or name"),
        "first": {"type": "boolean", "description": "Make it the first stage"},
        **STAGE_FIELDS,
        "version": VERSION_SEEN,
    },
    required=("pipeline", "name"),
    writes=True,
)
def stage_add(api, args):
    record = _record(api, args["pipeline"])
    # Whoever the task is for, unless it picks from several, or a pick setting asks for --pick-from (_pick_fields says so).
    picking = (
        args.get("pick_from")
        or any(args.get(a) not in (None, False) for a in PICK_ARGS)
        or args.get("owns_task") is False
    )
    holder = args.get("holder") or (None if picking else "assignee")
    stage = _stage_fields(api, {**args, "holder": holder}, {"requirements": []})
    record["stages"] = _place(record.get("stages") or [], stage, args)
    return _save(api, record, args)


@tool(
    "tico_stage_update",
    "Change a stage: its name, purpose, who holds it (one holder, or One of several with `pick_from`, `pick`, "
    "`limit_each`, `prefer` and `owns_task`; only a person changes who a stage picks from), whether tasks leave it "
    "by hand, its task limit, its expected time. Who may change the pipeline. "
    "Example: tico stage update engineering-review-1 --pick-from reviewer-1 --pick-from reviewer-2 --prefer new",
    {
        "stage": STAGE,
        "name": _s("A new name"),
        **STAGE_FIELDS,
        "no_limit": {"type": "boolean", "description": "Take its task limit off"},
        "version": VERSION_SEEN,
    },
    required=("stage",),
    writes=True,
)
def stage_update(api, args):
    record, at = _where(api, stage_id=args["stage"])
    _stage_fields(api, args, record["stages"][at])
    return _save(api, record, args)


@tool(
    "tico_stage_move",
    "Put a stage in another place in its pipeline: first, or after another stage. Who may change the pipeline. "
    "Example: tico stage move engineering-review-2 --after engineering-review-1",
    {
        "stage": STAGE,
        "after": _s("The stage it comes after: its id or name"),
        "first": {"type": "boolean", "description": "Make it the first stage"},
        "version": VERSION_SEEN,
    },
    required=("stage",),
    writes=True,
)
def stage_move(api, args):
    if not args.get("after") and not args.get("first"):
        raise APIError("usage", "Say where it goes: --after <stage> or --first")
    record, at = _where(api, stage_id=args["stage"])
    stages = list(record["stages"])
    stage = stages.pop(at)
    record["stages"] = _place(stages, stage, args)
    return _save(api, record, args)


@tool(
    "tico_stage_remove",
    "Take a stage out of its pipeline (it is retired; its history stays). Refused while it holds open tasks. "
    "`confirm` is its exact name. Who may change the pipeline. "
    'Example: tico stage remove engineering-fixes-2 --confirm "Fixes 2"',
    {"stage": STAGE, "confirm": CONFIRM, "version": VERSION_SEEN},
    required=("stage", "confirm"),
    writes=True,
)
def stage_remove(api, args):
    record, at = _where(api, stage_id=args["stage"])
    _confirmed(args, record["stages"][at].get("name"))
    record["stages"] = [s for i, s in enumerate(record["stages"]) if i != at]
    return _save(api, record, args)


REQUIREMENT_FIELDS = {
    "line": _s("What people read: Spec page, Checked in production"),
    "who_oks": WHO_OKS,
    "person": _s("Who gives the OK (an OK or a page): human:<id>, a bot slug, or `me`"),
    "can_push_past": {"type": "boolean", "description": "Whether a task may be moved on without it"},
    "rubric": _s("A page: what Jev checks it against"),
}


def _requirement_fields(api, args, req):
    if args.get("line") is not None:
        req["line"] = args["line"]
    if args.get("who_oks"):
        req["who_oks"] = args["who_oks"]
    if args.get("person"):
        req["person_id"] = _actor(api, args["person"])
    if args.get("can_push_past") is not None:
        req["can_push_past"] = bool(args["can_push_past"])
    if args.get("rubric") is not None:
        req["settings"] = {**(req.get("settings") or {}), "rubric": args["rubric"]}
    return req


def _requirement_label(req):
    return req.get("line") or req.get("kind")


@tool(
    "tico_requirement_list",
    "What it takes to leave a stage: each requirement with its id, kind, line, who gives its OK and whether a task "
    "may be moved on without it. Anyone on the team. Example: tico requirement list product-spec",
    {"stage": STAGE},
    required=("stage",),
)
def requirement_list(api, args):
    record, at = _where(api, stage_id=args["stage"])
    stage = record["stages"][at]
    return {
        "stage": stage.get("id"),
        "pipeline": record.get("id"),
        "settings_version": record.get("settings_version"),
        "requirements": stage.get("requirements") or [],
    }


@tool(
    "tico_requirement_add",
    "Add a requirement to a stage. Kinds: page (a page someone OKs), ok (an OK from a named person), owner_done (its "
    "holder says done), linked_done (the tasks it filed are done), related_input (Jev picks 1-2 related bots to comment "
    "once; settings bar, most, wait_hours), item (a linked item's condition, with source and condition, e.g. github "
    "pr_merged). `who_oks people_only` keeps the OK for people. Who may change the pipeline. "
    'Example: tico requirement add product-spec --kind page --line "Spec page" --who-oks people_only --person me',
    {
        "stage": STAGE,
        "kind": {
            "type": "string",
            "enum": ["page", "ok", "owner_done", "linked_done", "related_input", "item"],
            "description": "What it asks for: a page, an OK, the owner done, the linked tasks done, or a source's item",
        },
        "source": _s("An item: its source (github)"),
        "condition": _s("An item: its source's condition (pr_open, checks_pass, pr_merged)"),
        **REQUIREMENT_FIELDS,
        "version": VERSION_SEEN,
    },
    required=("stage", "kind"),
    writes=True,
)
def requirement_add(api, args):
    record, at = _where(api, stage_id=args["stage"])
    req = {"kind": args["kind"], "source": args.get("source"), "condition": args.get("condition")}
    stage = record["stages"][at]
    stage["requirements"] = [*(stage.get("requirements") or []), _requirement_fields(api, args, req)]
    return _save(api, record, args)


@tool(
    "tico_requirement_update",
    "Change a requirement on a stage: its line, who gives its OK (people_only keeps it for people), the person named, "
    "whether a task may be moved on without it, a page's rubric. Its kind never changes: remove it and add another. "
    "Who may change the pipeline. Example: tico requirement update 01e01f8d --who-oks people_only",
    {
        "requirement": _s("The requirement's id (tico requirement list <stage>)"),
        **REQUIREMENT_FIELDS,
        "version": VERSION_SEEN,
    },
    required=("requirement",),
    writes=True,
)
def requirement_update(api, args):
    record, at = _where(api, requirement_id=args["requirement"])
    for req in record["stages"][at].get("requirements") or []:
        if req.get("id") == args["requirement"]:
            _requirement_fields(api, args, req)
    return _save(api, record, args)


@tool(
    "tico_requirement_remove",
    "Take a requirement off a stage (withdrawn; the answers given stay readable). `confirm` is its line (or its kind "
    'when it has none). Who may change the pipeline. Example: tico requirement remove 01e01f8d --confirm "Spec page"',
    {"requirement": _s("The requirement's id"), "confirm": CONFIRM, "version": VERSION_SEEN},
    required=("requirement", "confirm"),
    writes=True,
)
def requirement_remove(api, args):
    record, at = _where(api, requirement_id=args["requirement"])
    stage = record["stages"][at]
    gone = next(r for r in stage.get("requirements") or [] if r.get("id") == args["requirement"])
    _confirmed(args, _requirement_label(gone))
    stage["requirements"] = [r for r in stage["requirements"] if r is not gone]
    return _save(api, record, args)


# ----------------------------------------------------------------------------- tico task wait (a shell's, not a tool)
TURN_STATES = ("snoozed", "person", "paused", "bot", "jev", "source", "tasks", "room", "pick", "by_hand")
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
        "tico_pipeline_create",
        "tico_pipeline_update",
        "tico_pipeline_save",
        "tico_pipeline_remove",
        "tico_stage_add",
        "tico_stage_update",
        "tico_stage_move",
        "tico_stage_remove",
        "tico_requirement_list",
        "tico_requirement_add",
        "tico_requirement_update",
        "tico_requirement_remove",
    )
}
SHELL_ONLY = {"tico_task_wait"}


# ----------------------------------------------------------------------------- the `tico` commands
ANYONE = " (anyone)"
CHANGERS = " (the owner and Admins, the pipeline's owner or its manager)"
MAKERS = " (the owner and Admins)"


def record_file(path):
    """`--file`: a pipeline's whole record as JSON, from a file or `-` for stdin (read here, never by the tool)."""
    try:
        with sys.stdin if path == "-" else open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"cannot read a JSON record from {path}: {exc}") from None
    if isinstance(data, dict) and isinstance(data.get("pipeline"), dict):
        data = data["pipeline"]  # as `tico pipeline show` or GET pipelines/{id} prints it
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError(f"{path} must hold a JSON object: the pipeline's record")
    return data


def _either(name):
    """A boolean given as --name or --no-name, and left out when neither is."""
    return Arg(name, action=argparse.BooleanOptionalAction, default=None)


_CONFIRM = Arg("confirm", "--confirm")
_FIRST = Arg("first", default=None)

CLI = (
    Group("pipeline", "the company's pipelines and their stages"),
    Command(
        "pipeline list",
        "the pipelines, each with its manager and stages" + ANYONE,
        args=(Arg("retired", default=None),),
        example="tico pipeline list",
    ),
    Command(
        "pipeline show",
        "one pipeline: every stage in order, who holds it and what it takes to leave it" + ANYONE,
        args=(Arg("record", default=None),),
        example="tico pipeline show product",
    ),
    Command(
        "pipeline log",
        "what happened in a pipeline lately, in the tasks you may read (the manager's Log)" + ANYONE,
        example="tico pipeline log engineering",
    ),
    Command(
        "pipeline create",
        "make a pipeline with its stages in order, each held by whoever the task is for unless a holder is given"
        + MAKERS,
        args=(Arg("name", "--name"), Arg("stages", "--stage"), Arg("private", default=None)),
        example='tico pipeline create support --name Support --manager success-manager --stage Triage --stage "Doing:success-manager"',
    ),
    Command(
        "pipeline update",
        "change a pipeline's name, goal, manager, owner, prefix or privacy (its stages change with tico stage)"
        + CHANGERS,
        args=(_either("private"),),
        example='tico pipeline update product --goal "Specs a builder can build from"',
    ),
    Command(
        "pipeline save",
        "make or replace a pipeline from its whole record: stages and requirements with an id change in place, "
        "without one are made, left out are retired" + CHANGERS,
        args=(Arg("record", "--file", type=record_file, metavar="FILE"),),
        example="tico pipeline save support --file support.json",
    ),
    Command(
        "pipeline remove",
        "retire a pipeline (refused while it holds an open task); --confirm is its exact name" + CHANGERS,
        args=(_CONFIRM,),
        example="tico pipeline remove support --confirm Support",
    ),
    Group("stage", "one stage of a pipeline"),
    Command(
        "stage show",
        "one stage: its pipeline, who holds it, what it takes to leave it" + ANYONE,
        example="tico stage show product-spec",
    ),
    Command(
        "stage add",
        "add a stage to a pipeline: last, first, or after a stage" + CHANGERS,
        args=(_FIRST, _either("by_hand"), _either("owns_task"), Arg("no_limit_each", default=None)),
        example='tico stage add engineering "Security review" --after engineering-review-2 --holder reviewer-2',
    ),
    Command(
        "stage update",
        "change a stage: name, purpose, holder or the bots it picks from, by hand, task limit, expected time"
        + CHANGERS,
        args=(
            _either("by_hand"),
            Arg("no_limit", default=None),
            _either("owns_task"),
            Arg("no_limit_each", default=None),
        ),
        example="tico stage update engineering-review-1 --pick-from reviewer-1 --pick-from reviewer-2 --prefer new",
    ),
    Command(
        "stage move",
        "put a stage first, or after another stage of its pipeline" + CHANGERS,
        args=(_FIRST,),
        example="tico stage move engineering-review-2 --after engineering-review-1",
    ),
    Command(
        "stage remove",
        "take a stage out of its pipeline (refused while it holds open tasks); --confirm is its exact name" + CHANGERS,
        args=(_CONFIRM,),
        example='tico stage remove engineering-fixes-2 --confirm "Fixes 2"',
    ),
    Group("company", "the company's shape"),
    Command(
        "company show",
        "the company in one read: you, every pipeline and stage, every bot and what it holds" + ANYONE,
        args=(Arg("retired", default=None),),
        example="tico company show",
    ),
    Group("requirement", "what it takes to leave a stage"),
    Command(
        "requirement list",
        "what it takes to leave a stage: each requirement's id, kind, line and who gives its OK" + ANYONE,
        example="tico requirement list product-spec",
    ),
    Command(
        "requirement add",
        "add a requirement to a stage: page, ok, owner_done, linked_done, related_input, or item (a source's condition)"
        + CHANGERS,
        args=(Arg("kind", "--kind"), _either("can_push_past")),
        example='tico requirement add product-spec --kind page --line "Spec page" --who-oks people_only --person me',
    ),
    Command(
        "requirement update",
        "change a requirement: line, who OKs it, the person, push past, rubric (never its kind)" + CHANGERS,
        args=(_either("can_push_past"),),
        example="tico requirement update 01e01f8d --who-oks people_only",
    ),
    Command(
        "requirement remove",
        "take a requirement off a stage; --confirm is its line (or its kind when it has none)" + CHANGERS,
        args=(_CONFIRM,),
        example='tico requirement remove 01e01f8d --confirm "Spec page"',
    ),
)
