"""Agent commands: decisions, templates, bots, repos, skills, agents, humans, groups and api."""

import argparse
from clients.hubcli_core import (
    ASKED,
    GO_LIVE_HELP,
)


def add_agents(sub, docs):
    decision = sub.add_parser("decision", help="the decision model: typed questions about a state").add_subparsers(
        dest="sub"
    )
    s = decision.add_parser(
        "ask", help="ask the decision model typed questions about a JSON state (questions/README.md)"
    )
    s.add_argument("--set", dest="question_set", help="a question set: questions/<name>.json in the Tico checkout")
    s.add_argument(
        "--questions-file",
        dest="questions_file",
        help="instead of --set: a JSON file holding the questions map (or an object with `questions`)",
    )
    s.add_argument("--state-file", dest="state_file", help="the JSON state the questions are about; `-` reads stdin")
    s.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="QUESTION=FILE",
        help="complete a dynamic choice with a JSON map of option -> description from FILE",
    )
    s.add_argument("--label", help="how the call is grouped in the audit; the set's id@version by default")
    s.add_argument("--list", action="store_true", help="print the question sets this checkout has")
    s.set_defaults(fn="decision ask")
    s = decision.add_parser("outcome", help="record what you did with a decision answer, once per call (its ref)")
    s.add_argument("--ref", required=True, help="the `ref` the decision answer returned")
    s.add_argument("--verdict", required=True, help="the answer you acted on, in a few words")
    s.add_argument("--action", required=True, help="what you did with it; say `overrode` when you went against it")
    s.add_argument("--label", help="the call's label; the one it was made with by default")
    s.set_defaults(fn="decision outcome")
    s = decision.add_parser(
        "readout",
        help="per question set: the last days' calls, cost, Jev or fallback and answer "
        "spread, and the month's spend against the cap",
    )
    s.add_argument("--days", type=int, help="how many days back from now (1-31, 7 by default)")
    s.set_defaults(fn="decision readout")

    # A new bot: BotOps builds the chosen template in the workspace (`hub bot create`) or only registers it (--record-only).
    # The Librarian (docs/librarian.md): ask a question, and read a linked doc on this computer.
    s = docs.add_parser("ask", help="ask the Librarian about the team's docs and wait for the answer")
    s.add_argument("question")
    s.add_argument("--wait", type=float, default=120, help="seconds to wait for the answer (default 120)")
    s.set_defaults(fn="doc ask")
    s = docs.add_parser("ask-status", help="collect the same Librarian answer without sending another question")
    s.add_argument("conversation_id")
    s.add_argument("message_id")
    s.add_argument("--wait", type=float, default=0)
    s.set_defaults(fn="doc ask-status")
    s = docs.add_parser("fetch", help="read a public link as text; runs on this computer, never on the server")
    s.add_argument("url")
    s.add_argument("--max-chars", dest="max_chars", type=int, default=30000)
    s.set_defaults(fn="doc fetch")
    sub.add_parser("template", help="the bot templates this company can pick from").add_subparsers(
        dest="sub"
    ).add_parser("list", help="the bot templates this company can pick from").set_defaults(fn="template list")
    bot = sub.add_parser(
        "bot", aliases=["bots"], help="build a bot from a template (BotOps), change it, place it, its status"
    ).add_subparsers(dest="sub")
    s = bot.add_parser(
        "show", help="one bot: its profile, and for one you may read also its status, machine, queue and goals"
    )
    s.add_argument("slug")
    s.set_defaults(fn="bot show")
    s = bot.add_parser(
        "create", help="build a bot from a template in the workspace and register it; --record-only only registers"
    )
    s.add_argument("slug", nargs="?", help="leave out: Tico picks one from --name, else the template's name")
    s.add_argument("--template", help="a template from `hub template list`")
    s.add_argument(
        "--record-only",
        dest="record_only",
        action="store_true",
        help="only register the bot with Tico (planned), as the person who asked; build nothing",
    )
    s.add_argument("--name", help="what people call this bot; the template card's name by default")
    s.add_argument("--description")
    s.add_argument("--reports-to", help="with --record-only: a bot slug, or human:<id>; the requester by default")
    s.add_argument(
        "--model",
        help="with --record-only: `hermes` or `openclaw` for a bot run by a Hermes or OpenClaw profile; the company's default otherwise",
    )
    s.set_defaults(fn="bot create")
    s = bot.add_parser("update", help="change a bot's settings with your rights, or the requester's (BotOps)")
    s.add_argument("slug")
    s.add_argument(
        "--on-behalf-of",
        metavar="MESSAGE_ID",
        help="the person's message to BotOps asking for this change; by default the one that started this run",
    )
    s.add_argument("--reports-to", help="a bot slug, or human:<id>")
    s.add_argument("--new-slug", help="rename its slug (a label: its id, history and folder stay)")
    s.add_argument("--display-name")
    s.add_argument("--description")
    s.add_argument("--repo", help="its GitHub repository: <org>/bot-<slug>")
    s.add_argument("--status", choices=("active", "paused", "planned"))
    s.add_argument("--session", choices=("bot", "task"), help="one provider session per bot or per task")
    s.add_argument(
        "--subtask-session",
        choices=("parent", "own"),
        help="parent (default): a subtask continues its parent task's session; own: its own session",
    )
    s.add_argument(
        "--task-goal", choices=("on", "off"), help="on: work each task it owns as a goal; off (default): not"
    )
    s.add_argument(
        "--review-model",
        choices=("check", "own"),
        help="check: every subtask it is handed under another's task runs on the company's check model, "
        "for a reviewer bot; own (default): its own model",
    )
    s.add_argument(
        "--capture-stream",
        choices=("on", "off"),
        help="on (owner only): keep each run's raw provider stream a week for hub run export; off (default): not",
    )
    s.add_argument(
        "--subscribe-updates",
        metavar="templates,tico|none",
        help="Subscribe to updates, as deferred messages for its next run: templates (its template's changes), "
        "tico (Tico releases), both, or none",
    )
    s.add_argument("--shared", action=argparse.BooleanOptionalAction, default=None, help="allow branches")
    s.set_defaults(fn="bot update")
    s = bot.add_parser("check", help="what preflight would still refuse about that repository")
    s.add_argument("slug")
    s.set_defaults(fn="bot check")
    s = bot.add_parser("check-names", help="old column, field and command names left in a bot's files")
    s.add_argument("target", help="the bot's slug, or its checkout folder")
    s.add_argument("--fix", action="store_true", help="rewrite the names that have one new spelling")
    s.set_defaults(fn="bot check-names")
    s = bot.add_parser("access", help="show or set who may see, read and write to a bot")
    s.add_argument("slug")
    for level in ("see", "read", "write"):
        s.add_argument("--" + level, help="everyone, or a comma list: human ids, group:<id>, bot:<slug>")
    s.set_defaults(fn="bot access")
    s = bot.add_parser("owners", help="add or remove the humans who own a bot")
    s.add_argument("slug")
    s.add_argument("--add", nargs="+", default=[], metavar="HUMAN")
    s.add_argument("--remove", nargs="+", default=[], metavar="HUMAN")
    s.set_defaults(fn="bot owners")
    s = bot.add_parser("mail", help="show or set whether a bot sends mail on its own (a person only)")
    s.add_argument("slug")
    s.add_argument(
        "--send",
        dest="outbound_send",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="--send: its sending policy is On their own; --no-send: Ask first",
    )
    s.set_defaults(fn="bot mail")
    s = bot.add_parser("setup-done", help="mark a bot's onboarding complete, as a person above it on the org chart")
    s.add_argument("slug", nargs="?", help="the bot; the one running this command by default")
    s.set_defaults(fn="bot setup-done")
    s = bot.add_parser("ready", help="an onboarding bot says it is ready, with what it learned")
    s.add_argument("--summary", required=True, help="a few lines on what you learned and what you will do first")
    s.set_defaults(fn="bot ready")
    s = bot.add_parser("place", help="put a bot on a computer, as the person who asked (BotOps)")
    s.add_argument("bot")
    s.add_argument("--computer", help="a computer's label or id; the best one that takes it by default")
    s.set_defaults(fn="bot place")
    s = bot.add_parser(
        "go-live",
        help="place it if needed, turn it on and start its setup (BotOps)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=GO_LIVE_HELP,
    )
    s.add_argument("bot")
    s.add_argument("--computer")
    s.add_argument("--no-setup", dest="no_setup", action="store_true", help="do not start its setup chat")
    s.set_defaults(fn="bot go-live")
    s = bot.add_parser("restore", help="bring an archived bot back, as the person who asked (BotOps)")
    s.add_argument("bot")
    s.set_defaults(fn="bot restore")
    s = bot.add_parser("archive", help="archive a bot you manage")
    s.add_argument("bot")
    s.add_argument("--successor")
    s.set_defaults(fn="bot archive")
    s = bot.add_parser("keep-tasks-going", help="read or set automatic task starts: N, off, or unlimited")
    s.add_argument("bot")
    s.add_argument(
        "limit",
        nargs="?",
        type=lambda v: None if v == 'unlimited' else 0 if v == 'off' else int(v),
        default=argparse.SUPPRESS,
    )
    s.set_defaults(fn="bot keep-tasks-going")
    s = bot.add_parser(
        "turns-at-once", help="read or set the most turns the bot runs at once across all computers: N or unlimited"
    )
    s.add_argument("bot")
    s.add_argument("limit", nargs="?", type=lambda v: None if v == 'unlimited' else int(v), default=argparse.SUPPRESS)
    s.set_defaults(fn="bot turns-at-once")
    s = bot.add_parser(
        "browser-exit",
        help="read or set the computer a bot browses the web through: a computer id, or none for its own",
    )
    s.add_argument("bot")
    s.add_argument("computer", nargs="?", type=lambda v: None if v == "none" else v, default=argparse.SUPPRESS)
    s.set_defaults(fn="bot browser-exit")
    s = bot.add_parser("budget", help="read or set a bot's runs per hour and per day, or release it from a hold")
    s.add_argument("bot")
    s.add_argument(
        "action", nargs="?", choices=["release"], help="release the bot from a hold instead of changing a budget"
    )
    for window in ("hour", "day"):
        s.add_argument(
            "--" + window,
            type=lambda v: v if v == "default" else int(v),
            help=f"runs allowed per {window}: a number, 0 for no limit, or default to follow the team",
        )
    s.set_defaults(fn="bot budget")
    s = bot.add_parser("profile", help="read or set a bot's profile: what wakes it and which tasks it takes")
    s.add_argument("bot")
    s.add_argument(
        "profile",
        nargs="?",
        choices=["doer", "reviewer", "lead", "manager", "responder", "monitor", "custom"],
        help="the profile to set; leave out to read the bot's and the choices",
    )
    s.add_argument(
        "--batch-seconds", dest="batch_seconds", type=int, help="how long news waits to be batched (seconds)"
    )
    s.add_argument(
        "--sweep-seconds", dest="sweep_seconds", type=int, help="least seconds between two runs of the same sweep"
    )
    s.add_argument(
        "--digest-hour", dest="digest_hour", type=int, help="the hour (0-23, team time) of a lead's daily digest"
    )
    s.add_argument("--pulls", choices=["all", "review", "none"], help="custom: which open tasks it starts by itself")
    s.add_argument(
        "--sweeps",
        type=lambda v: {"on": True, "off": False}[v],
        metavar="on|off",
        help="custom: whether the task roundup lists it its task sweep lines (late, backlog, assigned, restate, quiet tasks)",
    )
    s.add_argument("--takes", choices=["all", "review", "watcher", "none"], help="custom: which tasks it may be given")
    s.add_argument(
        "--confirm-drop-rules",
        dest="confirm_drop_rules",
        action="store_true",
        default=None,
        help="leaving custom deletes the wake rules it copied into the rules table",
    )
    s.set_defaults(fn="bot profile")
    s = bot.add_parser(
        "release",
        help="release a bot Tico stopped (failed runs, too many runs, or a run it could not start); "
        "its queue starts again",
    )
    s.add_argument("bot")
    s.set_defaults(fn="bot release")
    s = bot.add_parser("model", help="list the models, or change this bot's (BotOps)")
    s.add_argument("bot")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to list")
    s.add_argument("--effort")
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="bot model")
    s = bot.add_parser(
        "task-model",
        help="the model the bot's tasks run on, with its effort and harness; --clear puts "
        "them back on the bot's own model",
    )
    s.add_argument("bot")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to change only --effort or --harness")
    s.add_argument("--effort")
    s.add_argument("--harness")
    s.add_argument("--clear", action="store_true", help="the bot's tasks run on its own model again")
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="bot task-model")
    for verb in ("pause", "resume"):
        s = bot.add_parser(verb, help=f"{verb} a bot (BotOps)")
        s.add_argument("bot")
        s.set_defaults(fn="bot " + verb)
    s = bot.add_parser("scope", help="limit a bot's claims to one task and its subtasks, or lift that (BotOps)")
    s.add_argument("bot")
    s.add_argument("task", nargs="?", help="the task it may still work on")
    s.add_argument("--release", action="store_true", help="lift the limit; its other work becomes claimable again")
    s.set_defaults(fn="bot scope")
    s = bot.add_parser("branch", help="make your branch of a bot that allows branches")
    s.add_argument("bot")
    s.add_argument("--computer", help="your computer's label or id")
    s.set_defaults(fn="bot branch")
    s = bot.add_parser("copy", help="copy a bot into a new one the requester owns (BotOps)")
    s.add_argument("bot", help="the bot to copy")
    s.add_argument("--slug", help="the copy's slug; the original's with the next free number (builder-2) by default")
    s.add_argument("--name", help="what people call the copy")
    s.add_argument(
        "--with-memory",
        dest="with_memory",
        action="store_true",
        help="also copy the original's memory, notes and state",
    )
    s.add_argument("--computer", help="a computer's label or id to put the copy on")
    s.set_defaults(fn="bot copy")
    s = bot.add_parser(
        "update-from-original", help="bring a copy's instructions up to date with the bot it was copied from (BotOps)"
    )
    s.add_argument("bot", help="the copy")
    s.add_argument(
        "--resolved", action="store_true", help="conflicts settled by hand and committed: record the copy as up to date"
    )
    s.set_defaults(fn="bot update-from-original")
    s = bot.add_parser(
        "suggest-to-original", help="suggest a copy's instruction changes to the bot it was copied from (BotOps)"
    )
    s.add_argument("bot", help="the copy")
    s.add_argument(
        "--paths", nargs="+", metavar="PATH", help="only these files or folders, like AGENT.md or skills/triage"
    )
    s.add_argument("--title")
    s.set_defaults(fn="bot suggest-to-original")
    s = bot.add_parser("repos", help="read or set a bot’s repository access")
    s.add_argument("bot")
    modes = s.add_mutually_exclusive_group()
    modes.add_argument("--all", action="store_true")
    modes.add_argument("--own", action="store_true")
    modes.add_argument(
        "--chosen",
        nargs="*",
        metavar="OWNER/REPO[:read|:write[+actions|-actions][+edit|-edit]]",
        help="+actions lets the bot run workflows there, +edit edit workflow files (Owner or admin)",
    )
    s.set_defaults(fn="bot repos")
    repos = sub.add_parser("repo", help="team repositories").add_subparsers(dest="sub")
    s = repos.add_parser("list")
    s.set_defaults(fn="repo list")
    for verb in ("tick", "untick"):
        s = repos.add_parser(verb)
        s.add_argument("full_name")
        s.set_defaults(fn="repo " + verb)
    s = repos.add_parser("review", help="submit a GitHub review at an exact PR head; shared App falls back to COMMENT")
    s.add_argument("repository", metavar="OWNER/REPO")
    s.add_argument("number", type=int)
    s.add_argument("--head", required=True, help="reviewed full 40-character commit SHA")
    s.add_argument("--verdict", required=True, choices=("approve", "changes"))
    text = s.add_mutually_exclusive_group(required=True)
    text.add_argument("--body")
    text.add_argument("--body-file", help="review text file; VERDICT is appended automatically")
    s.add_argument("--operation-id", help="stable retry key; defaults to a hash of the review")
    s.set_defaults(fn="repo review")
    s = repos.add_parser(
        "product-create",
        help="preview, then confirm an exact private empty product repository "
        "(the Owner, or a bot with Create product repositories)",
    )
    s.add_argument("name", help="exact GitHub repository name, without an organization")
    s.add_argument("--confirm", metavar="ORG/NAME", help="the exact preview target, instead of typing it at a terminal")
    s.set_defaults(fn="repo product-create")
    s = bot.add_parser("repo-create", help="owner or BotOps: create <org>/bot-<slug> from a template, or empty")
    s.add_argument("slug")
    s.add_argument("--template", default="ticoteam/botops", metavar="OWNER/REPO")
    s.add_argument(
        "--empty", action="store_true", help="an empty private repository, for a bot whose history is on a computer"
    )
    s.set_defaults(fn="bot repo-create")
    s = bot.add_parser("recent", help="the bots you have been working with lately and where each stands")
    s.add_argument("--days", type=int, help="how far back (default 7)")
    s.add_argument("--limit", type=int, help="at most this many bots (default 10)")
    s.set_defaults(fn="bot recent")
    st = bot.add_parser(
        "status", help="a bot's live status: set yours, list every bot's, read the history"
    ).add_subparsers(dest="subsub")
    s = st.add_parser("set", help="one factual line about what you are doing now")
    s.add_argument("focus")
    s.add_argument("--state")
    s.add_argument("--task")
    s.add_argument("--bot")
    s.set_defaults(fn="bot status set")
    s = st.add_parser("list", help="every bot you may see with its live status")
    s.add_argument("--team")
    s.set_defaults(fn="bot status list")
    s = st.add_parser("history", help="a bot's status history")
    s.add_argument("bot")
    s.add_argument("--since")
    s.set_defaults(fn="bot status history")
    skill = sub.add_parser(
        "skill", help="a bot's skills: copy one to other bots, or propose one to the company's Skills directory"
    ).add_subparsers(dest="sub")
    s = skill.add_parser("copy", help="copy skills/<skill>/ from one bot into others, a commit in each (BotOps)")
    s.add_argument("skill", help="the skill's folder name under skills/")
    s.add_argument("--from", dest="bot", required=True, metavar="BOT", help="the bot that has the skill")
    s.add_argument("--to", nargs="+", required=True, metavar="BOT", help="the bots to copy it to")
    s.add_argument("--replace", action="store_true", help="replace a different skill of the same name")
    s.set_defaults(fn="skill copy")
    s = skill.add_parser(
        "submit", help="propose skills/<skill>/ to the company's Skills directory; an admin approves it after screening"
    )
    s.add_argument("skill", help="the skill's folder name")
    s.add_argument("--path", metavar="DIR", help="the folder, when it is not in the usual place")
    s.add_argument("--note", metavar="TEXT", help="one line for the admin (200 characters)")
    s.add_argument("--base", type=int, metavar="N", help="the version an update is based on (default: the latest)")
    s.set_defaults(fn="skill submit")
    agent = sub.add_parser("agent", help="external agents: connect a Hermes profile to a bot").add_subparsers(
        dest="sub"
    )
    pair = agent.add_parser(
        "pair", help="approve or decline the code a Hermes profile printed when it asked to pair"
    ).add_subparsers(dest="subsub")
    s = pair.add_parser("show", help="preview the profile, computer and expiry without changing a credential")
    s.add_argument("code")
    s.set_defaults(fn="agent pair show")
    s = pair.add_parser(
        "approve", help="connect the profile that printed this code to a bot (BotOps, or its owner or an admin)"
    )
    s.add_argument("code", help="like K7QM-4F2P")
    s.add_argument("--bot", required=True, help="the bot's slug; it must use the hermes harness")
    s.set_defaults(fn="agent pair approve")
    s = pair.add_parser("decline", help="turn down the profile that printed this code")
    s.add_argument("code")
    s.set_defaults(fn="agent pair decline")
    humans = sub.add_parser("human", help="the humans on the team: add one, list who is on it").add_subparsers(
        dest="sub"
    )
    s = humans.add_parser("add", help="add a human to the roster and the sign-in list")
    s.add_argument("email")
    s.add_argument("--name")
    s.add_argument("--title")
    s.add_argument("--reports-to")
    s.set_defaults(fn="human add")
    humans.add_parser("list", help="the humans on the roster").set_defaults(fn="human list")
    s = humans.add_parser("access", help="show or set who may see, read and write to a human")
    s.add_argument("human")
    for level in ("see", "read", "write"):
        s.add_argument("--" + level, help="everyone, or a comma list: human ids, group:<id>, bot:<slug>")
    s.set_defaults(fn="human access")
    groups = sub.add_parser("group", help="the groups: sub-teams that nest, holding humans and bots").add_subparsers(
        dest="sub"
    )
    groups.add_parser("list", help="the groups with their humans and bots").set_defaults(fn="group list")

    s = groups.add_parser(
        "update", help="create (no group given), rename, move, lead or fill a group (an owner or an admin)"
    )
    s.add_argument("group", nargs="?", help="the group's id; leave out to create one from --name")
    s.add_argument("--name")
    s.add_argument("--parent", help="the group it is in; an empty one is the top")
    s.add_argument("--add-human", dest="add_humans", action="append", metavar="HUMAN", help="put a human in it")
    s.add_argument("--add-bot", dest="add_bots", action="append", metavar="BOT", help="put a bot in it")
    s.add_argument("--remove-human", dest="remove_humans", action="append", metavar="HUMAN")
    s.add_argument("--remove-bot", dest="remove_bots", action="append", metavar="BOT")
    s.add_argument("--lead", help="bot:<slug> or person:<id>; an empty one clears it")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="group update")
    s = groups.add_parser("delete", help="delete a group; its teammates and groups move up to its parent")
    s.add_argument("group")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="group delete")
    s = groups.add_parser("access", help="show or set who may see, read and write to a group")
    s.add_argument("group")
    for level in ("see", "read", "write"):
        s.add_argument("--" + level, help="everyone, or a comma list: human ids, group:<id>, bot:<slug>")
    s.set_defaults(fn="group access")
    s = sub.add_parser("api", help="BotOps: any v2 route, as the person who asked")
    s.add_argument("method", type=str.upper, choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    s.add_argument("path", help="/api/v2/... or the part after it")
    s.add_argument("body", nargs="?", help="a JSON body for a write; - reads it from standard input")
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="api")
