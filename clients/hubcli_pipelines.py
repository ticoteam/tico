"""The `tico` command lines for pipelines, stages, the company's shape and following a task (clients/commands/pipelines.py
holds the tools). Stdlib only: hubcli.parser() calls `add` on every `tico` start, so nothing heavy is imported here.

    tico pipeline list [--retired]          the pipelines, each with its manager and stages
    tico pipeline show <pipeline>           one pipeline: every stage, who holds it, what it takes to leave it
    tico pipeline log <pipeline>            what happened in it lately
    tico stage show <stage>                 one stage, its pipeline and the stages around it
    tico company show [--retired]           the company in one read: you, the pipelines, the bots and what they hold
    tico task request <pipeline> "<title>" [--body "..."] [--owner X] [--private]
                                           file a request into a pipeline's first stage, given to its holder
    tico task approve <id> [-m "note"] [--requirement R] [--version N]   your OK on what waits on you
    tico task send-back <id> -m "what to change" [--requirement R] [--version N]   your no, with a note
    tico pipeline create <id> --name N --stage "Name[:holder]" ... [--manager M] [--goal G] [--prefix P] [--private]
    tico pipeline update <pipeline> [--name N] [--goal G] [--manager M] [--owner O] [--prefix P] [--[no-]private]
    tico pipeline save <id> --file record.json   make or replace a pipeline from its whole record (- for stdin)
    tico pipeline remove <pipeline> --confirm "<its name>"
    tico stage add <pipeline> "<name>" [--after S | --first] [--holder H] [--[no-]by-hand] [--limit N] [--purpose P]
    tico stage update <stage> [--name N] [--holder H] [--[no-]by-hand] [--limit N | --no-limit] [--purpose P]
    tico stage move <stage> (--after S | --first)
    tico stage remove <stage> --confirm "<its name>"
    tico requirement list <stage>
    tico requirement add <stage> --kind page|ok|owner_done|linked_done|item [--line L] [--who-oks W] [--person P] ...
    tico requirement update <requirement> [--line L] [--who-oks W] [--person P] [--[no-]can-push-past] [--rubric R]
    tico requirement remove <requirement> --confirm "<its line>"
                                           every change reads the pipeline's record and saves it whole with the
                                           settings_version read (--version N to pin the one you read)
    tico task wait <id> [--stage S] [--status done] [--turn person] [--needs-me] [--tree] [--timeout 30m] [--every 15s]
                                           one JSON line per change; exit 0 when met, 6 when the task ended
                                           without it, 124 on timeout (a person's shell; a bot's turn never waits)
"""

import argparse
import json
import sys

COMMANDS = {
    "pipeline list",
    "pipeline show",
    "pipeline log",
    "stage show",
    "company show",
    "task request",
    "task approve",
    "task send-back",
    "pipeline create",
    "pipeline update",
    "pipeline save",
    "pipeline remove",
    "stage add",
    "stage update",
    "stage move",
    "stage remove",
    "requirement list",
    "requirement add",
    "requirement update",
    "requirement remove",
}  # each runs its tool through remotecli.via_tool; `task wait` runs in hubcli itself
WAIT = "task wait"
ANYONE = "Anyone on the team may run it."
CHANGERS = "The company's owner and Admins, the pipeline's owner or its manager may run it."
MAKERS = "The company's owner and Admins may run it."


def _leaf(group, name, fn, help_, who, example):
    s = group.add_parser(
        name,
        help=help_,
        description=f"{help_[0].upper()}{help_[1:]}. {who}",
        epilog=f"Example: {example}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    s.set_defaults(fn=fn)
    return s


def add(sub, task):
    """The parsers: `sub` is the root's command group, `task` is `tico task`'s."""
    pipeline = sub.add_parser("pipeline", help="the company's pipelines and their stages").add_subparsers(dest="sub")
    s = _leaf(
        pipeline,
        "list",
        "pipeline list",
        "the pipelines, each with its manager and stages",
        ANYONE,
        "tico pipeline list",
    )
    s.add_argument("--retired", action="store_true", default=None, help="also list retired pipelines")
    s = _leaf(
        pipeline,
        "show",
        "pipeline show",
        "one pipeline: every stage in order, who holds it and what it takes to leave it",
        ANYONE,
        "tico pipeline show product",
    )
    s.add_argument("pipeline", help="its id or name")
    s.add_argument(
        "--record", action="store_true", default=None, help="the whole settings record, as tico pipeline save takes it"
    )
    s = _leaf(
        pipeline,
        "log",
        "pipeline log",
        "what happened in a pipeline lately (the manager's Log)",
        "Anyone on the team may run it; it covers the tasks they may read.",
        "tico pipeline log engineering",
    )
    s.add_argument("pipeline", help="its id or name")

    stage = sub.add_parser("stage", help="one stage of a pipeline").add_subparsers(dest="sub")
    s = _leaf(
        stage,
        "show",
        "stage show",
        "one stage: its pipeline, who holds it, what it takes to leave it",
        ANYONE,
        "tico stage show product-spec",
    )
    s.add_argument("stage", help="the stage's id (tico pipeline show lists them)")

    company = sub.add_parser("company", help="the company's shape").add_subparsers(dest="sub")
    s = _leaf(
        company,
        "show",
        "company show",
        "the company in one read: you, every pipeline and stage, every bot and what it holds",
        ANYONE,
        "tico company show",
    )
    s.add_argument("--retired", action="store_true", default=None, help="also show retired pipelines")

    s = _leaf(
        task,
        "request",
        "task request",
        "file a request into a pipeline's first stage, given to whoever holds that stage",
        "A person or a bot may run it.",
        'tico task request product "Add a negate(x) function" --body "Why"',
    )
    s.add_argument("pipeline", help="its id or name")
    s.add_argument("title")
    s.add_argument("--body", help="why, and what done looks like; default the title")
    s.add_argument("--owner", help="who works it first instead of the stage's holder: a bot slug, a person id, me")
    s.add_argument("--private", action="store_true", default=None, help="only its requester, owner and readers see it")
    for verb, help_, example in (
        ("approve", "give your OK on what waits on you on a task", 'tico task approve 3508775e -m "Looks right"'),
        (
            "send-back",
            "send a task back from what waits on you, with what to change",
            'tico task send-back 3508775e -m "Cover negative zero"',
        ),
    ):
        s = _leaf(
            task,
            verb,
            "task " + verb,
            help_,
            "The person it waits on. A personal token may OK a page; an OK a stage keeps for people is given in Tico.",
            example,
        )
        s.add_argument("id")
        s.add_argument("-m", "--note", required=verb == "send-back", help="what they read with it")
        s.add_argument("--requirement", help="its id or line, when more than one waits on you (tico task show)")
        s.add_argument("--version", type=int, help="the version you read; default the latest")

    s = _leaf(
        task,
        "wait",
        WAIT,
        "wait until a task reaches (or passes) a stage, a status or a turn, or needs you; one JSON line per change",
        "A person's shell (a bot's turn never waits). Exit 0 when met, 6 when the task ended without it, "
        "124 on timeout. With no condition it waits until the task ends.",
        "tico task wait 3508775e --tree --stage engineering-release --timeout 2h",
    )
    s.add_argument("id")
    s.add_argument(
        "--stage",
        action="append",
        help="until it reaches this stage or one after it (Done counts): a stage id; repeat or comma-separate for any",
    )
    s.add_argument("--status", action="append", choices=["open", "done", "closed"])
    s.add_argument(
        "--turn", action="append", help="whose turn: snoozed, person, paused, bot, jev, source, tasks, room, by_hand"
    )
    s.add_argument("--needs-me", action="store_true", help="until something on it waits on you")
    s.add_argument("--tree", action="store_true", help="also watch the tasks it files (up to 20)")
    s.add_argument("--timeout", default="30m", help="how long at most: 90s, 30m, 2h (default 30m)")
    s.add_argument("--every", default="15s", help="how often to read it (default 15s)")

    _changes(sub, pipeline, stage)


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


def _version(s):
    s.add_argument("--version", type=int, help="the settings_version you read; default the one read now")


def _flag(s, name, help_):
    s.add_argument(f"--{name}", action=argparse.BooleanOptionalAction, default=None, help=help_)


def _stage_flags(s):
    s.add_argument("--purpose", help="what the stage is for, in a sentence")
    s.add_argument("--holder", help="who holds it: a bot slug, bot:<id>, human:<id>, me, or assignee")
    _flag(s, "by-hand", "tasks leave it only when someone moves them")
    s.add_argument("--limit", type=int, help="at most this many open tasks in it (not the first stage)")
    s.add_argument("--expected-minutes", type=int, help="how long a task should take here; 0 clears")


def _requirement_flags(s):
    s.add_argument("--line", help="what people read: Spec page, Checked in production")
    s.add_argument("--who-oks", choices=["people_only", "people_with_exceptions", "none"], help="who gives the OK")
    s.add_argument("--person", help="who gives the OK (an OK or a page): human:<id>, a bot slug, or me")
    _flag(s, "can-push-past", "whether a task may be moved on without it")
    s.add_argument("--rubric", help="a page: what Jev checks it against")


def _changes(sub, pipeline, stage):
    """The commands that change pipelines, stages and requirements."""
    s = _leaf(
        pipeline,
        "create",
        "pipeline create",
        "make a pipeline with its stages in order, each held by whoever the task is for unless a holder is given",
        MAKERS,
        'tico pipeline create support --name Support --manager success-team-manager --stage Triage --stage "Doing:success-team-manager"',
    )
    s.add_argument("id", help="lowercase words joined by dashes")
    s.add_argument("--name", required=True)
    s.add_argument(
        "--stage", dest="stages", action="append", required=True, help="Name or Name:holder; repeat in order"
    )
    s.add_argument("--manager", help="a bot slug, bot:<id> or human:<id>")
    s.add_argument("--goal")
    s.add_argument("--prefix", help="one or two letters its task numbers start with")
    s.add_argument("--private", action="store_true", default=None, help="its tasks are read only by its manager")
    s = _leaf(
        pipeline,
        "update",
        "pipeline update",
        "change a pipeline's name, goal, manager, owner, prefix or privacy (its stages change with tico stage)",
        CHANGERS,
        'tico pipeline update product --goal "Specs a builder can build from"',
    )
    s.add_argument("pipeline", help="its id or name")
    s.add_argument("--name")
    s.add_argument("--goal")
    s.add_argument("--manager", help="a bot slug, bot:<id> or human:<id>")
    s.add_argument("--owner", help="human:<id>, or a bot")
    s.add_argument("--prefix")
    _flag(s, "private", "its tasks are read only by its manager (only while it holds no task)")
    _version(s)
    s = _leaf(
        pipeline,
        "save",
        "pipeline save",
        "make or replace a pipeline from its whole record: stages and requirements with an id change in place, "
        "without one are made, left out are retired",
        CHANGERS + " Only the owner and Admins make one.",
        "tico pipeline show support --record > support.json; (edit it); tico pipeline save support --file support.json",
    )
    s.add_argument("id")
    s.add_argument("--file", dest="record", type=record_file, required=True, help="the record as JSON; - for stdin")
    _version(s)
    s = _leaf(
        pipeline,
        "remove",
        "pipeline remove",
        "retire a pipeline (refused while it holds an open task); --confirm is its exact name",
        CHANGERS,
        "tico pipeline remove support --confirm Support",
    )
    s.add_argument("pipeline", help="its id or name")
    s.add_argument("--confirm", required=True, help="its exact name")
    _version(s)

    s = _leaf(
        stage,
        "add",
        "stage add",
        "add a stage to a pipeline: last, first, or after a stage",
        CHANGERS,
        'tico stage add engineering "Security review" --after engineering-review-2 --holder reviewer-2',
    )
    s.add_argument("pipeline", help="its id or name")
    s.add_argument("name")
    s.add_argument("--after", help="the stage it comes after: its id or name")
    s.add_argument("--first", action="store_true", default=None)
    _stage_flags(s)
    _version(s)
    s = _leaf(
        stage,
        "update",
        "stage update",
        "change a stage: name, purpose, holder, by hand, task limit, expected time",
        CHANGERS,
        "tico stage update product-ideas --no-by-hand",
    )
    s.add_argument("stage", help="the stage's id")
    s.add_argument("--name")
    _stage_flags(s)
    s.add_argument("--no-limit", action="store_true", default=None, help="take its task limit off")
    _version(s)
    s = _leaf(
        stage,
        "move",
        "stage move",
        "put a stage first, or after another stage of its pipeline",
        CHANGERS,
        "tico stage move engineering-review-2 --after engineering-review-1",
    )
    s.add_argument("stage", help="the stage's id")
    s.add_argument("--after", help="the stage it comes after: its id or name")
    s.add_argument("--first", action="store_true", default=None)
    _version(s)
    s = _leaf(
        stage,
        "remove",
        "stage remove",
        "take a stage out of its pipeline (refused while it holds open tasks); --confirm is its exact name",
        CHANGERS,
        'tico stage remove engineering-fixes-2 --confirm "Fixes 2"',
    )
    s.add_argument("stage", help="the stage's id")
    s.add_argument("--confirm", required=True, help="its exact name")
    _version(s)

    req = sub.add_parser("requirement", help="what it takes to leave a stage").add_subparsers(dest="sub")
    s = _leaf(
        req,
        "list",
        "requirement list",
        "what it takes to leave a stage: each requirement's id, kind, line and who gives its OK",
        ANYONE,
        "tico requirement list product-spec",
    )
    s.add_argument("stage", help="the stage's id")
    s = _leaf(
        req,
        "add",
        "requirement add",
        "add a requirement to a stage: page, ok, owner_done, linked_done, or item (a source's condition)",
        CHANGERS,
        'tico requirement add product-spec --kind page --line "Spec page" --who-oks people_only --person me',
    )
    s.add_argument("stage", help="the stage's id")
    s.add_argument("--kind", required=True, choices=["page", "ok", "owner_done", "linked_done", "item"])
    s.add_argument("--source", help="an item: its source (github)")
    s.add_argument("--condition", help="an item: the source's condition (pr_open, checks_pass, pr_merged)")
    _requirement_flags(s)
    _version(s)
    s = _leaf(
        req,
        "update",
        "requirement update",
        "change a requirement: line, who OKs it, the person, push past, rubric (never its kind)",
        CHANGERS,
        "tico requirement update 01e01f8d --who-oks people_only",
    )
    s.add_argument("requirement", help="its id (tico requirement list <stage>)")
    _requirement_flags(s)
    _version(s)
    s = _leaf(
        req,
        "remove",
        "requirement remove",
        "take a requirement off a stage; --confirm is its line (or its kind when it has none)",
        CHANGERS,
        'tico requirement remove 01e01f8d --confirm "Spec page"',
    )
    s.add_argument("requirement", help="its id")
    s.add_argument("--confirm", required=True, help="its line, or its kind when it has none")
    _version(s)
