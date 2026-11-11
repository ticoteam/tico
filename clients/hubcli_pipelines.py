"""The `tico` command lines for pipelines, stages, the company's shape and following a task (clients/hubtools_pipelines.py
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
    tico task wait <id> [--stage S] [--status done] [--turn person] [--needs-me] [--tree] [--timeout 30m] [--every 15s]
                                           one JSON line per change; exit 0 when met, 5 when the task ended
                                           without it, 124 on timeout (a person's shell; a bot's turn never waits)
"""

import argparse

COMMANDS = {
    "pipeline list",
    "pipeline show",
    "pipeline log",
    "stage show",
    "company show",
    "task request",
    "task approve",
    "task send-back",
}  # each runs its tool through remotecli.via_tool; `task wait` runs in hubcli itself
WAIT = "task wait"
ANYONE = "Anyone on the team may run it."


def current(argv):
    """True for a line of these commands, which an older command of the same words (hubcli.REMOVED) must not catch:
    `tico pipeline list` is new where `tico pipeline --type` is gone, and `tico task wait <id> --on task:<id>` is the
    old wait."""
    words = " ".join(argv[:2])
    return words in COMMANDS or words == WAIT and "--on" not in argv


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
        "A person's shell (a bot's turn never waits). Exit 0 when met, 5 when the task ended without it, "
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
