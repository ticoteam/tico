#!/usr/bin/env python3
"""`tico` (`hub` is its old name): what a bot calls inside a turn to talk to Tico.

Thin on purpose (docs/history/hub-v2.md §5): parse the arguments, hand them to `clients/remotecli.py`,
which makes one HTTP call against the Tico API and prints JSON on stdout. Every rule lives on
the server (`backend/hubdb.py`), never here. A command is its tool's name (clients/hubtools.py) with
`tico_` dropped: `tico message send` is `tico_message_send`, `tico bot go-live` is `tico_bot_go_live`.
The commands are `parser()` below; `tico --help` and `tico <group> --help` list them.

An old spelling (RENAMED below) prints a one-line "renamed to" notice on stderr and runs the new command; a command
that is gone (REMOVED below) prints one line naming what to use instead and exits 1.

Exit codes (docs/cli.md, clients/cli_plumbing.py): 0 fine; 1 may retry (the API could not be reached, 5xx, 429, or an
unexpected error); 2 refused (`{"error": ..., "detail": "..."}`, a non-retryable API error or a task refusal); 3 a
worktree wait; 4 needs sign-in (no credential or profile, 401); 5 not found (404); 64 usage; 124 a waiter's timeout.

Profiles (docs/cli.md): `--profile NAME` anywhere on the line, or `TICO_PROFILE`, uses a saved company and token
(`tico auth add|list|use|remove`); `--fields a,b.c` anywhere on the line prints only those fields of the answer.

Identity: a bot is `HUB_BOT` + `HUB_TOKEN` from the run's environment, set by the runner with `HUB_API_URL` and
`HUB_WORKSPACE`; a person's own script sets `HUB_API_URL` and a personal API token as `HUB_TOKEN` and no `HUB_BOT`
(docs/how-it-works.md, "Calling the API from a script"). Outside a run, `tico sql` and the tool reads fall back to
this Mac's runner credential (`~/.config/tico/runner.json`).
"""

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: python clients/hubcli.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import cli_plumbing, cli_spec

from clients import hubcli_pipelines

# Mirrors of the server's vocabulary, so a typo is refused by the parser before a request is made. The server is the
# authority when they disagree.
# The Assistant, acting on a task the person asked it to in their own Assistant chat (backend/assistant.py `instruction`).
ASKED = (
    "Assistant: the person asked for exactly this in their own Assistant chat: `turn` (their message that started "
    "this run) or that message's id. Leave it out when they did not clearly ask, and propose instead"
)


class CliError(Exception):
    """Anything the arguments or the environment got wrong: exit 1."""


class NoCredential(CliError):
    """No company to talk to, or a profile that cannot be used: the sign-in exit (clients/cli_plumbing.py)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def json_value(value):
    """`--content`: any JSON value, read exactly; a plain word that is not JSON is refused."""
    try:
        return json.loads(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--content is JSON, for example '{\"usd\": \"10.00\"}'") from None


def out(obj):
    print(json.dumps(obj, indent=2, default=str, sort_keys=False))
    return 0


def body_of(args):
    """A new task's first message, as `task create --message` (or --message-file) gave it."""
    if getattr(args, "message_file", None):
        return Path(args.message_file).read_text()
    return getattr(args, "message", None) or ""


# ----------------------------------------------------------------------------- dry run
# The rules are the server's own functions (backend/hubdb.py) run client-side, so a dry run
# reads exactly as the real create would. They are imported only here: the module is pure
# stdlib, opens no database, and the rest of the CLI never touches backend/.
def words_outside_quotes(text):
    """What rule 7 counts: hubdb's own helper, so the number a dry run prints is the number the
    lint would refuse on (quoted `>` lines and URLs do not count)."""
    from backend import hubdb as H

    return len(H._outside_quotes(text).split())


def owner_from_registry(name):
    """The owner as an actor string, read from the registry files the cloud seeds its roster
    from; None when it is in neither. The CLI has no database to resolve against (§3) and
    must not open one."""
    from backend import hubdb as H, people as P
    from clients import registry as REG

    raw = str(name or "").strip()
    if H.actor_kind(raw) in ("bot", "human"):
        return raw
    try:
        emps = {e["name"] for e in REG.load_registry()[1] if e.get("name")}
        roster = P.load(REG.load_people())
    except Exception:  # a CLI that cannot read the registry still answers
        emps, roster = set(), None
    if raw in emps:
        return H.bot_actor(raw)
    if P.person(raw, roster):
        return H.human_actor(raw)
    return None


def task_problems(actor, owner, title, body, pipeline=None):
    """(resolved owner, the problems the server's `task_create` would refuse on, the writing
    warnings it would accept with), writing none.

    The owner's kind comes from the registry files; the lint (`lint_human_item`) is hubdb's own
    function run here. Reach against live state, the
    duplicate check and whether a named pipeline exists are the hub's to decide at the real create.
    The lints shape asks in General only, not another pipeline's tasks.
    """
    from backend import hubdb as H

    problems, warnings = [], []
    title, body = str(title or "").strip(), str(body or "")
    general = str(pipeline or "").strip().lower() in ("", "general")
    target = owner_from_registry(owner)
    # Not in the registry is no refusal here: a template name (`--owner marketing-lead`) still reaches the one live bot
    # built from that template on the server, which this client cannot see. cmd_task_dry_run says so.
    if not title:
        problems.append("give it a title that says what you are asking for")
    elif H.is_human(target) and general and H.STYLE_LINT != "off":
        # writing problems: the create is accepted with these as warnings unless TICO_STYLE_LINT=refuse
        (problems if H.STYLE_LINT == "refuse" else warnings).extend(H.lint_human_item(body, title=title))
    if H.is_bot(actor) and H.is_human(target) and general and title:
        # the ask that opens a person's task is always refused when it is missing or unclear
        problems += list(filter(None, [H.person_ask_refusal(target, H.person_ask_of_body(body, title), "assign")]))
    if H.is_bot(actor) and general:
        # plain-English titles: a warning this week, a refusal once TICO_TITLE_LINT=refuse
        problems += [f"{p} (title lint, {H.TITLE_LINT})" for p in H.lint_title(title)]
    return target, problems, warnings


def private_readers_line(args, body):
    """A dry run into a pipeline prints first, as the create does, who else reads the task if it is private
    (backend/private_readers.py). Only the server knows whether it will be private (a private parent, a bot's default)
    and who holds each stage, so this one read asks its dry run; it files nothing. A notice only: when the server
    cannot be asked, the local answer stands."""
    pipeline = getattr(args, "pipeline", None)
    if not pipeline:
        return
    from clients.tico import APIError, Client

    fields = {
        "title": args.title,
        "body": body,
        "owner": args.owner,
        "pipeline": pipeline,
        "private": True if args.private else None,
        "parent": getattr(args, "parent", None),
        "readers": list(getattr(args, "readers", None) or []) or None,
    }
    try:
        client = Client(os.environ["HUB_API_URL"], os.environ.get("HUB_TOKEN", ""), retries=0)
        said = client.post("tasks/dry-run", {k: v for k, v in fields.items() if v is not None})
    except (APIError, OSError, ValueError):  # a notice: the dry run still answers from its own checks
        return
    if isinstance(said, dict) and said.get("private_readers"):
        print("tico: " + str(said["private_readers"]), file=sys.stderr)


def cmd_task_dry_run(args, who):
    """`tico task create --dry-run`: print what a create would be refused for; write nothing.

    No database is opened; the owner's kind comes from the registry files and the same lint
    runs here, so a bot on the cloud API can size a human item before it spends a turn on a
    refusal. Into a pipeline, one read of the server's dry run says who else reads it if it is
    private (private_readers_line). Prints lines rather
    than JSON: `- <problem>` per problem and exit 1, or one `ok:` line.
    """
    from backend import hubdb as H

    if not os.environ.get("HUB_API_URL"):
        raise CliError(
            "HUB_API_URL is not set: `tico` talks to the Tico API and runs inside a "
            "bot turn, where the runner sets HUB_API_URL, HUB_TOKEN and HUB_BOT"
        )
    if who:
        raise CliError("--human is unavailable with HUB_API_URL; remote identity is the token")
    slug = (os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE") or "").strip()
    actor = H.bot_actor(slug) if slug else None
    body = body_of(args)
    owner, problems, warnings = task_problems(actor, args.owner, args.title, body, getattr(args, "pipeline", None))
    if problems:
        print("\n".join(f"- {p}" for p in problems + [f"{w} (warning)" for w in warnings]))
        return 1
    for w in warnings:
        print(f"- {w} (warning: the create is accepted)")
    private_readers_line(args, body)
    if not args.owner:
        print(
            "ok: no owner named; the server decides at the create (a first stage that picks from several picks one, "
            "any other pipeline needs --owner)"
        )
        return 0
    if not owner:
        print(
            f"ok: {args.owner} is not in registry/employees.yaml or registry/people.yaml; the server decides at the create "
            "(a template name reaches the one live bot built from that template)"
        )
        return 0
    print(
        f'ok: would create "{args.title.strip()}" for {owner} '
        f'({words_outside_quotes(body)} words outside quoted drafts)'
    )
    return 0


# ----------------------------------------------------------------------------- parser
DEFER_FOR = re.compile(r"^(never|[1-9][0-9]{0,5}[mhd])$")


def defer_for(word):
    """A defer's deadline (backend/delivery.py): 30m, 4h, 2d or never. Empty is the default deadline."""
    if word and not DEFER_FOR.match(word):
        raise argparse.ArgumentTypeError("a defer is 30m, 4h, 2d or never")
    return word


def delivery_flags(s, *, steer=True, goal=False, steer_help=None):
    """How a send reaches its bot (backend/delivery.py): --steer, --queue or --defer [time], at most one. --defer sets
    `defer_for` ('' for the default deadline); the others set `delivery`."""
    ways = s.add_mutually_exclusive_group()
    if steer:
        ways.add_argument(
            "--steer",
            dest="delivery",
            action="store_const",
            const="steer",
            help=steer_help or "into the bot's running turn now (right after it if its harness can't steer)",
        )
    ways.add_argument(
        "--queue",
        dest="delivery",
        action="store_const",
        const="queue",
        help="right after the bot's current turn there, or now if it is idle",
    )
    ways.add_argument(
        "--defer",
        dest="defer_for",
        nargs="?",
        const="",
        type=defer_for,
        metavar="30m|4h|2d|never",
        help="wake nobody: it goes in with the bot's next turn there, at the latest after this time",
    )
    if goal:
        ways.add_argument(
            "--goal",
            dest="delivery",
            action="store_const",
            const="goal",
            help="make the text the goal of the task's bot session (plain text, no files)",
        )
    return ways


def delivery_of(args):
    """{delivery, defer_for} for a request body from delivery_flags' arguments; {} when none was given."""
    if getattr(args, "defer_for", None) is not None:
        return {"delivery": "defer", **({"defer_for": args.defer_for} if args.defer_for else {})}
    return {"delivery": args.delivery} if getattr(args, "delivery", None) else {}


def _compact_at(text):
    """`--compact-at` of `tico bot model`: a size in tokens, or `default` for the model's own."""
    if text == "default":
        return text
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text} is not a number of tokens or default") from None


def thread_page_flags(s, key="next_cursor"):
    """The paging flags of a thread list: a page's next_cursor (or `key`) reads on, and --limit sets the size."""
    s.add_argument("--cursor", help=f"the page after this cursor: a page's {key}")
    s.add_argument("--limit", type=int, help="items in the page (50 by default, 100 at most)")


def server_release(timeout=3):
    """The release the hub server reports on its unauthenticated /healthz, or "" when it cannot say (no hub set, or it
    does not answer within `timeout` seconds). A turn's runner strips TICO_* from the environment, so a released CLI
    has no version of its own and asks the server it talks to."""
    api = os.environ.get("HUB_API_URL", "")
    if not api:
        return ""
    origin = api.split("/api/", 1)[0].rstrip("/")
    try:
        from clients import gateway

        with gateway.branded_opener().open(origin + "/healthz", timeout=timeout) as reply:
            return str(json.loads(reply.read().decode()).get("release") or "")
    except Exception:
        return ""


class _Version(argparse.Action):
    """`tico --version`: the Tico version this CLI was stamped with, else the release of the server it talks to."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings, dest, nargs=0, default=default, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        from clients.hubtools import release_version

        version = release_version()
        if version == "dev":
            version = server_release() or "unknown (this CLI build is not stamped)"
        print(version)
        parser.exit()


def command_paths(p, prefix=()):
    """Every command under `p` as a tuple of words, e.g. ('task', 'list'). Only commands that parse are listed."""
    paths = []
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen = set()
            for name, sub in action.choices.items():
                if id(sub) in seen:  # an alias: the same command as the name before it
                    continue
                seen.add(id(sub))
                paths.extend(command_paths(sub, prefix + (name,)) or [prefix + (name,)])
    return paths


# The CLI's name in its usage, errors and notices. `hub` (scripts/hub) is its old name, the same CLI.
PROG = "tico"


def did_you_mean(words, names):
    """A one-line hint naming the closest of `names` (commands that exist), or "" when none is close."""
    close = difflib.get_close_matches(" ".join(words), names, n=2, cutoff=0.5)
    return f" Did you mean: {', '.join(PROG + ' ' + name for name in close)}?" if close else ""


def typed_words(argv):
    """The command words on a line: everything before the first flag."""
    words = []
    for a in argv:
        if a.startswith("-"):
            break
        words.append(a)
    return words


def named_command(p, argv):
    """The parser of the command a line names (`tico task relate`), and its words."""
    node, words = p, []
    for a in argv:
        sub = next((act for act in node._actions if isinstance(act, argparse._SubParsersAction)), None)
        if a.startswith("-") or not sub or a not in sub.choices:
            continue
        node = sub.choices[a]
        words.append(a)
    return node, words


def unknown_flag_hint(p, argv, unknown):
    """The closest flag of the command the line names, for a flag it does not take (`--where-status` is `--status`)."""
    node, words = named_command(p, argv)
    flags = []
    for flag in unknown:
        if flag.startswith("--"):
            flags += difflib.get_close_matches(flag, list(node._option_string_actions), n=1, cutoff=0.6)
    if flags:
        return f" Did you mean {', '.join(flags)}?"
    takes = [flag for flag in node._option_string_actions if flag.startswith("--") and flag != "--help"]
    if takes and words:
        return f" It takes: {', '.join(sorted(takes))}"
    return did_you_mean(words, [" ".join(path) for path in command_paths(p)]) if words else ""


def example_line(p):
    """`tico task show <id>`: the command's name with a placeholder for each positional it takes."""
    parts = [p.prog]
    for a in p._actions:
        if a.option_strings or isinstance(a, argparse._SubParsersAction):
            continue
        if a.choices:
            parts.append("{" + "|".join(str(c) for c in a.choices) + "}")
        else:
            parts.append(f"<{a.metavar if isinstance(a.metavar, str) else a.dest}>")
    return " ".join(parts)


def missing_help(p, message):
    """`finding-key: The finding's key: its signature (hm-...) on the finding's task`: the help of each argument the
    required-arguments error names, so the error says where the value is, not only that it is missing."""
    named = message.split(":", 1)[-1].split(", ")
    return [
        f"{argparse._get_action_name(a)}: {a.help}"
        for a in p._actions
        if a.help and a.help != argparse.SUPPRESS and argparse._get_action_name(a) in [n.strip() for n in named]
    ]


def _bad_command_word(p, message):
    """Whether argparse's error is about the word naming a command (`task lst`), not a flag's or argument's value
    (`rules due --status late`), which argparse's own list of choices already answers."""
    return any(
        message.startswith(f"argument {argparse._get_action_name(action)}: invalid choice")
        for action in p._actions
        if isinstance(action, argparse._SubParsersAction)
    )


class HubParser(argparse.ArgumentParser):
    """The parser of the root and of every command: argparse builds each subparser from this class, so one error
    method covers all of them. Each error says what would have worked: the closest command, the closest flag, or the
    example of a command that was missing its id."""

    def error(self, message):
        if message.startswith("the following arguments are required"):
            self.print_usage(sys.stderr)
            print(f"Example: {example_line(self)}", file=sys.stderr)
            for line in missing_help(self, message):
                print(line, file=sys.stderr)
            self.exit(2, f"{self.prog}: error: {message}\n")
        hint = ""
        argv = getattr(self, "hub_argv", None)
        if argv is not None and message.startswith("argument cmd: invalid choice"):
            names = [" ".join(path) for path in command_paths(self)]
            hint = did_you_mean(typed_words(argv), names)
        elif argv is not None and message.startswith("unrecognized arguments: "):
            hint = unknown_flag_hint(self, argv, message[len("unrecognized arguments: ") :].split())
            # The usage and name of the command the line names (`tico task relate`), not of the whole CLI: a flag the
            # command does not take (`--version` on relate) is that command's error.
            node, words = named_command(self, argv)
            if words:
                node.print_usage(sys.stderr)
                self.exit(2, f"{node.prog}: error: {message}{hint}\n")
        elif self.prog != PROG and _bad_command_word(self, message):  # a bad word under a command, e.g. `task lst`
            bad = message.split("invalid choice: '", 1)[-1].split("'", 1)[0]
            under = self.prog.split()[1:]
            names = [" ".join(under + list(path)) for path in command_paths(self)]
            hint = did_you_mean(under + [bad], names)
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}{hint}\n")


def _group_subparsers(sub, name):
    """The subcommands of group `name`, already added by a spec, so the hand parser can add one leaf it still builds."""
    return next(a for a in sub.choices[name]._actions if isinstance(a, argparse._SubParsersAction))


def parser():
    p = HubParser(
        prog=PROG,
        description="Tico: tasks, messages, bots and the rest of your team's work",
        epilog="This computer's own commands: tico-computer help",
    )
    p.add_argument("--version", action=_Version, help="print the Tico version")
    sub = p.add_subparsers(dest="cmd")
    from clients.commands import mail as mail_commands, rules as rule_commands, strategy as strategy_commands
    from clients.commands import risk as risk_commands
    from clients.commands import market as market_commands
    from clients.commands import company_page, finance, listening as listening_commands, threads as thread_commands
    from clients.commands import calls, webhooks
    from clients.commands import contacts as contacts_commands
    from clients.commands import products as products_commands
    from clients.commands import page_history as page_history_commands
    from clients.commands import computer_upkeep, computers, org as org_commands, pipelines as pipeline_commands
    from clients.commands import gateway as gateway_commands, goals as goal_commands
    from clients.commands import admin as admin_commands, content as content_commands, task_work as task_work_commands
    from clients.commands import bots as bot_commands, tasks as tasks_commands
    from clients.commands import assistant as assistant_commands
    from clients.commands import automations as automations_commands
    from clients.commands import browser as browser_commands, logins
    from clients.commands import tools as tools_commands
    from clients.commands import tools_admin

    sub.add_parser("whoami", help="who you are, to which company, and through which profile").set_defaults(fn="whoami")
    cli_plumbing.add_parsers(sub)  # tico auth, tico token, tico events tail (clients/cli_plumbing.py)

    cli_spec.add(sub, admin_commands.ALERTS)  # tico alerts: clients/commands/admin.py (all but `set`)
    item = _group_subparsers(sub, "alerts").add_parser("set")  # hand-built: see ALERTS in clients/commands/admin.py
    item.set_defaults(fn="alerts set")
    item.add_argument("--monthly-cap", type=int)
    for flag in ("urgent-needs", "legal-mail", "bot-stuck", "urgent-calls", "emergency-exception"):
        item.add_argument("--" + flag, action=argparse.BooleanOptionalAction, default=None)
    for flag in ("timezone", "quiet-start", "quiet-end"):
        item.add_argument("--" + flag)

    cli_spec.add(sub, listening_commands.INBOUND)  # tico inbound: clients/commands/listening.py
    cli_spec.add(sub, mail_commands.LETTER)  # tico letter and tico mail: clients/commands/mail.py
    cli_spec.add(sub, mail_commands.MAIL)
    cli_spec.add(sub, mail_commands.CLI)  # tico email: clients/commands/mail.py

    cli_spec.add(sub, company_page.CLI)  # tico company-page, audiences, editions, readers

    cli_spec.add(sub, calls.MEETING)  # tico meeting: clients/commands/calls.py (all but `import`)
    meeting = _group_subparsers(sub, "meeting")  # hand-built: it reads the transcript and notes files on this computer
    s = meeting.add_parser(
        "import", help="file a transcript (text, WebVTT, SRT or JSON segments) as one of your meetings"
    )
    s.add_argument("file", help="the transcript file; - reads standard input")
    s.add_argument("--title", help="default: the file's name")
    s.add_argument(
        "--date", help="when it started: 2026-09-28 or 2026-09-28T16:00 (this Mac's time zone) or with an offset"
    )
    s.add_argument("--participant", dest="participants", action="append", help="an email or a name; repeat for each")
    s.add_argument(
        "--source", default="upload", help="where it came from: zoom, granola, otter, fireflies... (default upload)"
    )
    s.add_argument("--external-id", help="that system's id for it: importing it again updates the meeting")
    s.add_argument("--format", choices=["auto", "text", "vtt", "srt", "json"], default="auto")
    s.add_argument("--notes-file", help="a Markdown file of notes or a summary")
    s.add_argument("--media-url", help="an https link to the recording, if there is one")
    s.add_argument("--send-to", help="a bot to hand the meeting to, as Send does")
    s.add_argument("--private", action="store_true", default=None)
    s.add_argument(
        "--review",
        choices=["pending", "live"],
        help="share immediately with live; otherwise your review setting applies",
    )
    s.set_defaults(fn="meeting import")

    message = sub.add_parser("message", help="send a message, read what waits for you, mark one read").add_subparsers(
        dest="sub"
    )
    s = message.add_parser("send", help="send a message to a bot or a human")
    s.add_argument("to")
    s.add_argument("text")
    s.add_argument(
        "--fyi", action="store_true", help="an fyi: it expects no reply, and takes no conversation or references"
    )
    s.add_argument("--conversation")
    s.add_argument("--ref", action="append")
    s.add_argument(
        "--command", action="store_true", help="send the text verbatim as a harness command, such as /compact"
    )
    ways = delivery_flags(s, steer=False)
    ways.add_argument(
        "--steer",
        action="store_true",
        help="into the bot's running turn now; with --ref task:<id> you requested, into its owner's "
        "running turn on that task",
    )
    s.add_argument(
        "--visual",
        action="append",
        metavar="PATH",
        help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
        "already in Tico); repeat for more",
    )
    s.add_argument(
        "--title",
        action="append",
        dest="visual_title",
        metavar="TITLE",
        help="the caption for each --visual, in the same order",
    )
    s.add_argument(
        "--attach",
        action="append",
        metavar="PATH",
        help="a file in your checkout to attach (a report, letter, spec, HTML page); repeat for more",
    )
    s.add_argument(
        "--needs-action",
        dest="needs_action",
        action="store_true",
        help="a bot's message to a bot waits for that bot's next run; this wakes it now",
    )
    s.set_defaults(fn="message send")
    message.add_parser("list", help="the messages and fyis waiting for you").set_defaults(fn="message list")
    s = message.add_parser(
        "mark-read", help="mark a message from your inbox as read (an external agent's own delivery)"
    )
    s.add_argument("message_id")
    s.set_defaults(fn="message mark-read")
    s = message.add_parser("redact", help="replace a secret (read from stdin) in a message with a mark")
    s.add_argument("message_id")
    s.add_argument("--label", help="what it was saved as")
    s.set_defaults(fn="message redact")

    s = (
        sub.add_parser("conversation", help="what was said in a conversation")
        .add_subparsers(dest="sub")
        .add_parser("show")
    )
    s.add_argument("conversation")
    s.add_argument("--before", help="the page before this message id: a page's next_before")
    s.add_argument("--since", help="only messages sent after this time (ISO 8601)")
    s.set_defaults(fn="conversation show")

    cli_spec.add(sub, thread_commands.CLI)  # tico thread: clients/commands/threads.py

    note = sub.add_parser(
        "note", help="a deferred message for a bot: no run now, its next run reads it"
    ).add_subparsers(dest="sub")
    s = note.add_parser("create", help="leave a bot a deferred message: no run now, its next run reads it")
    s.add_argument("to")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--text-file", dest="text_file")
    s.add_argument(
        "--defer",
        dest="defer_for",
        type=defer_for,
        metavar="30m|4h|2d|never",
        help="start a run by this time if none carried it (never when omitted)",
    )
    s.set_defaults(fn="note create")
    s = note.add_parser("list", help="deferred messages (notes), newest first: left for you and by you")
    s.add_argument("--to")
    s.add_argument("--from", dest="sender")
    s.add_argument("--since", help="an ISO time, or 24h / 3d")
    s.add_argument("--waiting", action="store_true", help="only notes no run has carried yet")
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(fn="note list")
    s = note.add_parser("delete", help="take back a note no run has carried yet")
    s.add_argument("id")
    s.set_defaults(fn="note delete")

    cli_spec.add(sub, task_work_commands.FEEDBACK)  # tico feedback: clients/commands/task_work.py

    cli_spec.add(sub, content_commands.FILE)  # tico file: clients/commands/content.py
    cli_spec.add(sub, content_commands.PAGE)  # tico page: clients/commands/content.py
    cli_spec.add(sub, content_commands.CARD)  # tico card
    cli_spec.add(sub, content_commands.PIN)  # tico pin
    cli_spec.add(sub, content_commands.DOC)  # tico doc: clients/commands/content.py (all but `fetch`, below)
    s = _group_subparsers(sub, "doc").add_parser(
        "fetch", help="read a public link as text; runs on this computer, never on the server"
    )
    s.add_argument("url")
    s.add_argument("--max-chars", dest="max_chars", type=int, default=30000)
    s.set_defaults(fn="doc fetch")
    chat = sub.add_parser("chat", help="bot chat goals and commands").add_subparsers(dest="sub")
    s = chat.add_parser("goal", help="read, set, edit, pause, resume or clear a chat goal")
    s.add_argument("conversation_id")
    s.add_argument("action", nargs="?", default="get", choices=["get", "set", "edit", "pause", "resume", "clear"])
    s.add_argument("objective", nargs="?")
    s.set_defaults(fn="chat goal")
    s = chat.add_parser("ask", help="ask a person in your direct chat, marked ❓: it needs their reply")
    s.add_argument("message", help="what you ask: a short first line that stands on its own")
    s.add_argument("--to", required=True, help="the person: their id")
    s.add_argument(
        "--conversation", dest="conversation_id", help="a chat you are both in; your chat with them if left out"
    )
    s.set_defaults(fn="chat ask")
    cli_spec.add(sub, assistant_commands.ASSISTANT)  # tico assistant: clients/commands/assistant.py
    cli_spec.add(sub, task_work_commands.TAG)  # tico tag: clients/commands/task_work.py

    cli_spec.add(sub, tasks_commands.TASK)  # tico task and tasks: clients/commands/tasks.py
    task = _group_subparsers(sub, "task")  # the leaves still built here are added below
    worktree = task.add_parser("worktree", help="create or attach this task's worktree").add_subparsers(
        dest="worktree_sub"
    )
    for operation, argument in (("add", "repo"), ("attach", "path"), ("setup", "repo")):
        s = worktree.add_parser(operation)
        s.add_argument(argument)
        s.add_argument("--task", help="task id; defaults to this run's task")
        s.set_defaults(fn="task worktree " + operation)
    s = task.add_parser("create")
    s.add_argument("--visibility", choices=["public", "private"])
    s.add_argument("--public-title", dest="public_title", help="public roadmap title (needed with --visibility public)")
    s.add_argument("--public-summary", dest="public_summary", help="public roadmap summary")
    s.add_argument("--private", action="store_true", default=None, help="only its requester, owner and readers see it")
    s.add_argument(
        "--reader",
        dest="readers",
        action="append",
        help="with --private: someone else who sees it, a person, bot or group:<id> (repeat for more)",
    )
    s.add_argument(
        "--follow", action="store_true", default=None, help="follow the task you file (its owner follows anyway)"
    )
    s.add_argument(
        "--public",
        "--from-private",
        dest="private",
        action="store_false",
        default=None,
        help="company-visible, also from a run on a private task (whose tasks are private unless you say so) or under "
        "a private parent; a bot's private-by-default setting still keeps its tasks private",
    )
    s.add_argument(
        "--owner",
        help="who does it: a bot slug, a person id or me; leave out to file into a pipeline whose first stage picks "
        "from several (the stage picks one)",
    )
    s.add_argument(
        "--for",
        dest="for_actor",
        help="a bot filing a person's own ask: the person (human:<id>) it is for, with --from-message",
    )
    s.add_argument(
        "--from-message",
        dest="from_message",
        help="with --for: the id of the message that person asked in (you must be able to read it)",
    )
    s.add_argument("--title", required=True)
    s.add_argument(
        "--message", "--body", dest="message", default="", help="the first message on its chat: what you ask"
    )
    s.add_argument("--message-file", "--body-file", dest="message_file", help="the first message, from a file")
    s.add_argument("--due")
    s.add_argument("--parent", help="the task that files it: it waits until this one ends")
    s.add_argument("--pipeline", help="the pipeline's id; default general")
    s.add_argument("--label", action="append", help="a label (repeat, or comma-separate); a project is a label")
    s.add_argument("--top", action="store_true", help="put it at the top of the owner's queue")
    s.add_argument("--link", action="append", help="a URL to attach (a pull request, an issue, a document)")
    s.add_argument("--goal", help="the goal this task serves (tico goal list); optional")
    s.add_argument("--after", action="append", help="a task it merges after (repeat for more): Merge waits for it")
    s.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="print the checks a create would fail and write nothing"
    )
    s.add_argument(
        "--request-id",
        dest="request_id",
        help="the person's message this carries: handing BotOps something a person asked you for, their "
        "message id or `turn` (BotOps acts with their rights, or puts it to them as a Confirm card)",
    )
    s.add_argument("--number", type=int, help="keep an imported task's number")
    s.add_argument(
        "--model",
        help="the model this bot-owned task's runs use, from `tico bot model`'s list; leave out "
        "for the bot's task model",
    )
    s.add_argument("--effort", help="the reasoning effort that model takes")
    s.add_argument("--harness", help="the harness that runs that model")
    s.set_defaults(fn="task create")
    version_help = "the version you were last shown: tico task show, a list row and every wake print it"
    s = task.add_parser(
        "ask",
        help="ask a person on this task: the message is posted on the task's chat marked ❓ (needs their reply); "
        "with --kind ok and --show, an OK of what it shows",
    )
    s.add_argument("id")
    s.add_argument("line", nargs="?", help="the message: a short first line that reads on its own, then any detail")
    s.add_argument("--to", help="the person you ask: their id")
    s.add_argument(
        "--kind",
        choices=["ok"],
        help="ok: approve or send back what --show names (a question is the default)",
    )
    s.add_argument("--show", action="append", metavar="REF", help="what they look at: page:12, file:ID@N; repeat")
    s.add_argument("--for", dest="subject", metavar="KIND:ID", help="an OK bound to what you will do, with --content")
    s.add_argument("--content", type=json_value, help="with --for: exactly what the OK covers, as JSON")
    s.add_argument("--withdraw", action="store_true", default=None, help="take back your open ask instead")
    s.add_argument("--requirement", help="with --withdraw: the ask's requirement id, when several may be withdrawn")
    s.add_argument(
        "--not-known",
        dest="not_known",
        action="store_true",
        default=None,
        help="send it to the person even though Tico answered it (its answer did not cover your question)",
    )
    s.add_argument("-m", "--why", help="with --withdraw: why")
    s.add_argument("--version", type=int, help=version_help)
    s.set_defaults(fn="task ask")
    s = task.add_parser("give", help="give what you are asked on a task to another person")
    s.add_argument("id")
    s.add_argument("requirement", help="the requirement's id, from tico task show")
    s.add_argument("to_person", nargs="?", metavar="PERSON", help=argparse.SUPPRESS)  # the older form of --to
    s.add_argument("--to", help="the person it goes to: their id (required)")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task give")
    s = task.add_parser(
        "comment", help="leave a comment on a task (not your own task during your turn: your reply is posted there)"
    )
    s.add_argument("--public", action="store_true", default=None)
    s.add_argument("--ok", metavar="OK_ID", help="the OK a person gave on exactly this public reply")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--attach", action="append", help="file to attach; repeat for more files")
    s.add_argument(
        "--file",
        action="append",
        metavar="ID@VERSION",
        help="a file already in Tico: one on this task, or (a person) a chat file you can open, as ID@1",
    )
    s.add_argument(
        "--visual",
        action="append",
        metavar="PATH",
        help="a picture, SVG, HTML page, video or audio to show with it (a file in your checkout, or ID@VERSION "
        "already in Tico); repeat for more",
    )
    s.add_argument(
        "--title",
        action="append",
        dest="visual_title",
        metavar="TITLE",
        help="the caption for each --visual, in the same order",
    )
    delivery_flags(s, goal=True)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.add_argument(
        "--needs-action",
        dest="needs_action",
        action="store_true",
        help="a bot's message to a bot waits for that bot's next run; this wakes it now",
    )
    s.set_defaults(fn="task comment")
    for change, words in (
        ("relate", "relate another task to this one: <id> [<kind>] <task>, e.g. <id> depends_on <task>"),
        ("unrelate", "take a relation off: <id> [<kind>] <task>"),
    ):
        s = task.add_parser(change, help=words)
        s.add_argument("id")
        s.add_argument("task", help="the other task, or the kind when a third word names the task")
        s.add_argument("other", nargs="?", help="the other task, when the second word is the kind")
        s.add_argument(
            "--kind",
            default="related",
            help="related (default); duplicate_of; follow_up: it was split off or followed up from task; depends_on: "
            "this task cannot go on until task ends (Blocked by it, no date; never snooze for that)",
        )
        if change == "relate":
            s.add_argument("--remove", action="store_true", help="take it off instead")
            s.add_argument(
                "--until",
                choices=("released",),
                help="depends_on only: stay blocked after task is Done, until production runs a release that holds "
                "its merged PRs (how a Validate task waits for its work to reach production)",
            )
        s.set_defaults(fn="task relate", **({"remove": True} if change == "unrelate" else {}))
    s = task.add_parser(
        "attach",
        help="attach a file to a task; prints the link for your note. With --req, attach a page "
        "(page:<file id>[@<version>]) to the stage's page requirement",
    )
    s.add_argument("id")
    s.add_argument("file", help="a local file, or page:<file id>[@<version>] with --req")
    s.add_argument(
        "--doc",
        metavar="NAME",
        help="an .html file as the task's document NAME: a page linked to the task with that role, the same as "
        "tico page publish FILE --task ID --as NAME. Spec, QA report, Release report and Invoice are the names a "
        "stage approves; say what changed in a comment (no --note)",
    )
    s.add_argument("--req", help='the page requirement it is for, e.g. "Code review"')
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--name", help="the name people see; defaults to the file's own")
    s.add_argument(
        "--replaces",
        metavar="FILE_ID",
        help="a file on this task this is the next version of, whatever its name (the same name already is)",
    )
    s.add_argument("--poster", help="a PNG or JPEG preview")
    s.add_argument("--note", help="short note on this version (at most 500 characters); not with --doc")
    s.add_argument(
        "--changed",
        action="append",
        metavar="LINE",
        help="with --req: one line on what changed in this version (up to 5, 120 characters each)",
    )
    s.add_argument(
        "--answered",
        action="append",
        metavar="ENTRY",
        help='with --req: "<point>: done|not done, <why>" for each point of the last send back (up to 12). '
        "Never quote their note back to them",
    )
    s.add_argument(
        "--add",
        action="store_true",
        help="with --req on a document approval (QA report, Spec): keep the pages already attached in this stage "
        "beside this one, all judged together. Without it this page replaces them",
    )
    s.set_defaults(fn="task attach")

    replay = sub.add_parser("replay", help="what happened in a window, as you may see it").add_subparsers(dest="sub")
    s = replay.add_parser("show", help="what happened in a window, in time order, as you may see it (JSON)")
    s.add_argument("--from", dest="from", help="start, ISO 8601 with a zone (default 24 hours before --to)")
    s.add_argument("--to", help="end (default now); at most 31 days after --from")
    scope = s.add_mutually_exclusive_group()
    scope.add_argument("--bot", help="only what involves this bot")
    scope.add_argument("--person", help="only what involves this person")
    scope.add_argument("--team", help="only what involves this team")
    s.set_defaults(fn="replay show")
    cli_spec.add(sub, goal_commands.GOAL)  # tico goal, kpi and proposal: clients/commands/goals.py
    cli_spec.add(sub, goal_commands.KPI)
    cli_spec.add(sub, goal_commands.PROPOSAL)

    cli_spec.add(sub, rule_commands.CLI)  # tico rules: clients/commands/rules.py
    cli_spec.add(sub, risk_commands.CLI)  # tico risk: clients/commands/risk.py
    cli_spec.add(sub, strategy_commands.STRATEGY)  # tico strategy and tico brand: clients/commands/strategy.py
    cli_spec.add(sub, strategy_commands.BRAND)
    cli_spec.add(sub, browser_commands.CLI)  # tico browser: clients/commands/browser.py
    cli_spec.add(sub, logins.CLI)  # tico login: clients/commands/logins.py
    cli_spec.add(sub, tools_commands.ACCESS)  # tico access: clients/commands/tools.py
    cli_spec.add(sub, strategy_commands.CLI)  # tico growth: clients/commands/strategy.py

    cli_spec.add(sub, strategy_commands.LENS)  # tico lens: clients/commands/strategy.py

    cli_spec.add(sub, market_commands.CLI)  # tico market: clients/commands/market.py

    # `tico listening` (clients/commands/listening.py): every leaf but save, which reads a file or stdin here.
    cli_spec.add(sub, listening_commands.LISTENING)
    listen = _group_subparsers(sub, "listening")
    s = listen.add_parser("save", help="what one read of one source found, from a JSON file")
    s.add_argument("--source", required=True, help="source id, from `tico listening sources`")
    s.add_argument("--file", required=True, help="JSON: {status, error, items: [...]}; - for stdin")
    s.set_defaults(fn="listening save")

    # `tico tool` (clients/commands/tools.py): every leaf but `call`, which runs the vendor call on this computer.
    cli_spec.add(sub, tools_commands.TOOL)
    tools = _group_subparsers(sub, "tool")
    s = tools.add_parser("call", help="call a proxied API tool from a turn; Tico adds the real key")
    s.add_argument("tool", help="the API tool id, e.g. close-crm (its TICO_TOOL_<ID>_URL)")
    s.add_argument("method", type=str.upper, choices=("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"))
    s.add_argument("path", help="the vendor API path, e.g. /api/v1/me/")
    s.add_argument("--data", help="a JSON body")
    s.add_argument("--query", action="append", default=[], help="a query parameter, key=value (repeatable)")
    s.set_defaults(fn="tool call")
    cli_spec.add(sub, tools_commands.TOOL_AFTER_CALL, parents={("tool",): tools})
    cli_spec.add(sub, tools_admin.TOOL_CLI, parents={("tool",): tools})  # clients/commands/tools_admin.py

    # Automations (clients/commands/automations.py): every leaf but create, update and inbox, which stay here.
    cli_spec.add(sub, automations_commands.AUTOMATION)
    auto = _group_subparsers(sub, "automation")
    s = auto.add_parser("create", help="save a new automation from a JSON flow")
    s.add_argument("--file", required=True, help="a JSON object {name, owner, trigger, steps, ...}; - reads stdin")
    s.set_defaults(fn="automation create")
    s = auto.add_parser("update", help="change one; a new trigger, steps or batch is a new version")
    s.add_argument("id")
    s.add_argument("--file", help="a JSON object with the fields to change; - reads stdin")
    s.add_argument("--name")
    s.add_argument("--summary", help="a few words on the change")
    switch = s.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true")
    switch.add_argument("--disable", action="store_true")
    s.set_defaults(fn="automation update")
    s = auto.add_parser("inbox", help="read or set an inbox's send switch")
    s.add_argument("inbox", help="an email address, slack:dm or slack:#<channel>")
    s.add_argument("--send", choices=["on", "off"], help="leave out to read it")
    s.set_defaults(fn="automation inbox")
    s = sub.add_parser(
        "audit", help="who changed caps, spend, groups, automations and inboxes, newest first (the owner sees all)"
    )
    s.add_argument("--object", help="one object, such as automation:<id> or inbox:<address>")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="`next` from the page before")
    s.set_defaults(fn="audit")

    cli_spec.add(sub, automations_commands.EVENT_TYPES)  # tico event-types: clients/commands/automations.py
    cli_spec.add(sub, webhooks.CLI)  # tico trigger: clients/commands/webhooks.py
    cli_spec.add(sub, webhooks.RELAY)  # tico relay: clients/commands/webhooks.py
    cli_spec.add(sub, calls.CALL)  # tico call: clients/commands/calls.py
    cli_spec.add(sub, webhooks.SLEEP)

    oks = sub.add_parser("ok", help="an OK you asked for with --for: use it once, just before you act").add_subparsers(
        dest="sub"
    )
    s = oks.add_parser("use", help="use an OK once; refused unless it was given for exactly this")
    s.add_argument("requirement", help="the OK's requirement id, as tico task ask --for printed it")
    s.add_argument("--for", dest="subject", required=True, metavar="KIND:ID", help="the subject it was asked for")
    s.add_argument("--content", type=json_value, required=True, help="exactly the content it was asked for, as JSON")
    s.add_argument("-m", "--note", help="what was done, in a sentence")
    s.set_defaults(fn="ok use")
    cli_spec.add(sub, webhooks.NOTICE)

    cli_spec.add(sub, admin_commands.SETTINGS)  # tico settings: clients/commands/admin.py
    cli_spec.add(sub, webhooks.RELEASE)
    cli_spec.add(sub, webhooks.WEBHOOK)

    s = sub.add_parser("brief", help="alerts, who needs the person, what bots said since --since")
    s.add_argument("--since", help="an ISO date-time; default the last 12 hours")
    s.set_defaults(fn="brief")
    mcp = sub.add_parser("mcp", help="how outside assistants' tool calls perform").add_subparsers(dest="sub")
    s = mcp.add_parser("stats", help="per-tool timing and answer size of assistants' calls")
    s.add_argument("--days", type=int)
    s.add_argument("--via", help="one assistant's token label, e.g. grok-bot")
    s.set_defaults(fn="mcp stats")
    origin = sub.add_parser("origin", help="why an item is on a person's list").add_subparsers(dest="sub")
    s = origin.add_parser(
        "show", help="who made a task, message or approval, its cause, its siblings and what stops it"
    )
    s.add_argument("ref", help="a task id (or its first 8 characters), a message id or an approval id")
    s.set_defaults(fn="origin show")
    cli_spec.add(sub, assistant_commands.NEEDS_YOU)  # tico needs-you: clients/commands/assistant.py

    runs = sub.add_parser("run", help="a bot's runs").add_subparsers(dest="sub")
    s = runs.add_parser("list", help="a bot's recent runs")
    s.add_argument("bot")
    s.add_argument("--since")
    s.set_defaults(fn="run list")
    s = runs.add_parser("export", help="a run's captured provider stream (owners and engineering bots)")
    s.add_argument("run", help="the run id, from tico run list")
    s.add_argument(
        "--fixture",
        action="store_true",
        help="write input.json, <provider>_transcript.ndjson and output.json to a folder",
    )
    s.add_argument("--out", help="with --fixture: the parent folder (default: the current one)")
    s.set_defaults(fn="run export")

    # `tico team` (clients/commands/admin.py): every leaf but icon, which sends a file's bytes from this computer.
    cli_spec.add(sub, admin_commands.TEAM)
    team = _group_subparsers(sub, "team")
    s = team.add_parser("icon", help="set the public team logo (owner; PNG/JPEG/WebP up to 1 MB)")
    s.add_argument("file")
    s.set_defaults(fn="team icon")
    cli_spec.add(sub, admin_commands.TEAM_AFTER_ICON, parents={("team",): team})
    cli_spec.add(sub, admin_commands.SEAT)  # tico seat: clients/commands/admin.py
    cli_spec.add(sub, org_commands.ORG)  # tico org: clients/commands/org.py
    cli_spec.add(sub, computers.HEALTH)  # tico health: clients/commands/computers.py
    cli_spec.add(sub, assistant_commands.CHANGELOG)  # tico changelog: clients/commands/assistant.py

    # `tico update` (clients/commands/assistant.py): every leaf but create, whose tool reads --slides-file here.
    cli_spec.add(sub, assistant_commands.UPDATE)
    upd = _group_subparsers(sub, "update")
    s = upd.add_parser(
        "create",
        help="post your update when Tico asks for it: three plain-English bullets "
        "(plus any Blocked: or Suggestion: lines), or a week in review's slides",
    )
    s.add_argument(
        "body", nargs="?", help="a daily: three lines, each starting with '- ', plus any Blocked: or Suggestion: lines"
    )
    s.add_argument("--slides-file", help="a week in review: JSON {goal, kpis, done, focus, blockers}")
    s.add_argument("--kind", choices=("daily", "weekly"))
    s.add_argument("--day", help="YYYY-MM-DD, only when Tico asks you to redo a past day")
    s.add_argument(
        "--learnings-saved", type=int, dest="learnings_saved", help="how many of Tico's learnings you kept (0-15)"
    )
    s.add_argument(
        "--suggestion", action="append", dest="suggestions_seen", help="a suggestion you acted on (repeatable)"
    )
    s.add_argument(
        "--delivery-batch", dest="delivery_batch_id", help="the delivery this post answers, when Tico named one"
    )
    s.add_argument("--skip", action="store_true", help="nothing real to report since your last update: post nothing")
    s.set_defaults(fn="update create")
    cli_spec.add(sub, assistant_commands.UPDATE_AFTER_CREATE, parents={("update",): upd})
    grok = sub.add_parser("grokbot", help="your Grok Bots in Tico (docs/external-agent-sync.md)").add_subparsers(
        dest="sub"
    )
    s = grok.add_parser("sync", help="sync Grok Bots from a JSON file: {\"bots\": [...], \"source\": ...}")
    s.add_argument("--file", required=True, help="the JSON body tico_grokbot_sync takes")
    s.set_defaults(fn="grokbot sync")
    ext = sub.add_parser(
        "external", help="your bots on another platform in Tico (docs/external-agent-sync.md)"
    ).add_subparsers(dest="sub")
    s = ext.add_parser("sync", help="sync Grok Bots or Dots from a JSON file: {\"bots\": [...], \"source\": ...}")
    s.add_argument("--provider", required=True, choices=("grokbot", "dots", "muse"))
    s.add_argument("--file", required=True, help="the JSON body tico_external_sync takes")
    s.set_defaults(fn="external sync")

    # `tico calendar` (clients/commands/calls.py): every leaf but list, whose tool depends on --json, and schedule,
    # which reads --description-file on this computer.
    cli_spec.add(sub, calls.CALENDAR)
    calendar = _group_subparsers(sub, "calendar")
    s = calendar.add_parser("list", help="events on calendars (--who), or a company calendar's appointments")
    s.add_argument("--calendar", default="", help="roster email; default the company owner's")
    s.add_argument("--who", help="comma-separated people or bots, refs or emails; default you")
    s.add_argument("--from", dest="from_", metavar="ISO", help="window start: ISO time, or YYYY-MM-DD")
    s.add_argument("--to", metavar="ISO", help="window end, exclusive; at most 62 days after --from")
    s.add_argument("--kinds", help="comma-separated: event, run, due, away (default all)")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="calendar list")
    cli_spec.add(sub, calls.CALENDAR_AFTER_LIST, parents={("calendar",): calendar})
    s = calendar.add_parser("schedule", help="queue one calendar appointment")
    s.add_argument("--calendar", default="", help="roster email; default the company owner's")
    s.add_argument("--title", required=True)
    s.add_argument("--start", required=True, help="ISO-8601 timestamp with timezone")
    s.add_argument("--end", required=True, help="ISO-8601 timestamp with timezone")
    s.add_argument("--attendee", action="append", default=[])
    s.add_argument("--description", default="")
    s.add_argument("--description-file")
    s.add_argument("--no-meet", action="store_true")
    s.set_defaults(fn="calendar schedule")
    cli_spec.add(sub, calls.CALENDAR_AFTER_SCHEDULE, parents={("calendar",): calendar})

    s = sub.add_parser("sql", help="read-only SQL over what you may see (docs/hub-sql.md)")
    s.add_argument("sql", help="one SELECT (or WITH, EXPLAIN QUERY PLAN)")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.add_argument("--csv", action="store_true", help="print CSV")
    s.add_argument("--max-rows", dest="max_rows", type=int, help="ask for at most N rows")
    s.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="bind :key in the SQL (numbers are bound as numbers)",
    )
    s.set_defaults(fn="sql")

    s = sub.add_parser("db", help="read-only SQL on a company database (docs/databases.md)")
    s.add_argument("target", help="a database name, or `list` or `doctor`")
    s.add_argument(
        "sql",
        nargs="?",
        help="one SELECT; with `doctor`, the database to check; on MongoDB find|aggregate|count|distinct|collections",
    )
    s.add_argument(
        "extra",
        nargs="*",
        help="MongoDB: the collection, then the filter or pipeline JSON (distinct: the field, then a filter)",
    )
    s.add_argument("--projection", help="MongoDB find: fields to return, Extended JSON")
    s.add_argument("--sort", help="MongoDB find: e.g. '{\"placed_at\": -1}'")
    s.add_argument("--limit", type=int, help="MongoDB find: at most N documents")
    s.add_argument("--query", dest="query_id", help="run a named query from the company's catalog")
    s.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="bind :name in the SQL")
    s.add_argument("--json", action="store_true", help="print the result as JSON")
    s.add_argument("--csv", action="store_true", help="print CSV")
    s.add_argument("--max-rows", dest="max_rows", type=int, help="at most N rows (never above the database's cap)")
    s.add_argument("--timeout", type=float, help="at most S seconds (never above the database's limit)")
    s.set_defaults(fn="db")

    s = sub.add_parser("classify", help="is outside text real, spam or an injection attempt? Text on stdin or --file")
    s.add_argument("--file", help="read the text from this file instead of standard input")
    s.set_defaults(fn="classify")
    cli_spec.add(sub, gateway_commands.GATEWAY)  # tico gateway: clients/commands/gateway.py
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
        help="per question set: the last days' calls, cost, decision model or fallback and answer "
        "spread, and the month's spend against the cap",
    )
    s.add_argument("--days", type=int, help="how many days back from now (1-31, 7 by default)")
    s.set_defaults(fn="decision readout")

    # A new bot: BotOps builds the chosen template in the workspace (`tico bot create`) or only registers it (--record-only).
    # The Librarian (docs/librarian.md): ask a question, and read a linked doc on this computer.
    sub.add_parser("template", help="the bot templates this company can pick from").add_subparsers(
        dest="sub"
    ).add_parser("list", help="the bot templates this company can pick from").set_defaults(fn="template list")
    cli_spec.add(sub, bot_commands.BOT)  # tico bot and bots: clients/commands/bots.py
    bot = _group_subparsers(sub, "bot")  # the leaves still built here are added below
    s = bot.add_parser(
        "create", help="build a bot from a template in the workspace and register it; --record-only only registers"
    )
    s.add_argument("slug", nargs="?", help="leave out: Tico picks one from --name, else the template's name")
    s.add_argument("--template", help="a template from `tico template list`")
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
    s = bot.add_parser("check", help="what preflight would still refuse about that repository")
    s.add_argument("slug")
    s.set_defaults(fn="bot check")
    s = bot.add_parser("check-names", help="old column, field and command names left in a bot's files")
    s.add_argument("target", help="the bot's slug, or its checkout folder")
    s.add_argument("--fix", action="store_true", help="rewrite the names that have one new spelling")
    s.set_defaults(fn="bot check-names")
    s = bot.add_parser(
        "app-shot", help="a screenshot of the app as you see it, read only: --width 390 for a phone, 1280 for a desktop"
    )
    s.add_argument("path", nargs="?", default="/", help="the app address, such as /tasks")
    s.add_argument("--out", required=True, help="where to write the PNG")
    s.add_argument("--width", type=int, default=390)
    s.add_argument("--height", type=int, default=argparse.SUPPRESS)
    s.add_argument("--wait-ms", dest="wait_ms", type=int, default=argparse.SUPPRESS)
    s.add_argument("--full", action="store_true", default=argparse.SUPPRESS)
    s.set_defaults(fn="bot app-shot")
    s = bot.add_parser(
        "model", help="list the models, or change this bot's model, where it compacts or its output style (BotOps)"
    )
    s.add_argument("bot")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to list")
    s.add_argument("--effort")
    s.add_argument(
        "--compact-at",
        type=_compact_at,
        help="where its conversation compacts, in tokens: 100000, 150000, 200000, 250000, 300000, 400000, 500000, "
        "600000, 700000, 800000, 900000 or 1000000; default puts it back on its model's default",
    )
    s.add_argument(
        "--output-style",
        dest="output_style",
        choices=("default", "Concise", "Explanatory", "Learning", "Proactive"),
        help="Claude Code's output style for its turns; default is Claude Code's own",
    )
    s.add_argument(
        "--verbosity",
        choices=("default", "low", "medium", "high"),
        help="Codex's verbosity for its turns; default is the model's own",
    )
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="bot model")
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
        "--paths", nargs="+", metavar="PATH", help="only these files or folders, like AGENTS.md or skills/triage"
    )
    s.add_argument("--title")
    s.set_defaults(fn="bot suggest-to-original")
    cli_spec.add(sub, bot_commands.BOT_REPOS, parents={("bot",): bot})  # clients/commands/bots.py
    # `tico repo` (clients/commands/tools.py and connections.py): every leaf but review and product-create.
    cli_spec.add(sub, tools_commands.REPO)
    repos = _group_subparsers(sub, "repo")
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
    skill = sub.add_parser(
        "skill", help="skills: list and search them, copy one to other bots, or propose one to the company's directory"
    ).add_subparsers(dest="sub")
    s = skill.add_parser("list", help="every skill on Library › Skills, by job function and search words")
    s.add_argument("q", nargs="*", metavar="WORD", help="words to match in the name, description or function")
    s.add_argument("--function", metavar="F", help="one job function, like legal or finance; other for the rest")
    s.set_defaults(fn="skill list")
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
    cli_spec.add(sub, bot_commands.AGENT)  # tico agent: clients/commands/bots.py
    cli_spec.add(sub, org_commands.HUMAN)  # tico human: clients/commands/org.py
    cli_spec.add(sub, admin_commands.GROUP)  # tico group: clients/commands/admin.py
    setup = sub.add_parser("setup", help="Finish setup, the welcome wizard (the owner)").add_subparsers(dest="sub")
    setup.add_parser(
        "show", help="where it stands: the wizard, the people the company file names, the checklist"
    ).set_defaults(fn="setup show")
    setup.add_parser("complete", help="the owner finishes the wizard with its saved answers").set_defaults(
        fn="setup complete"
    )
    s = setup.add_parser("invite", help="the owner invites a person the company file from setup names")
    s.add_argument("person_key", help="their id in the company file (tico setup show: company_import.people[].id)")
    s.add_argument("--email", help="their email; leave out to use the one they were added with")
    s.set_defaults(fn="setup invite")
    s = sub.add_parser("api", help="BotOps: any v2 route, as the person who asked")
    s.add_argument("method", type=str.upper, choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    s.add_argument("path", help="/api/v2/... or the part after it")
    s.add_argument("body", nargs="?", help="a JSON body for a write; - reads it from standard input")
    s.add_argument(
        "--file", help="read the JSON body from this file (- is standard input), for a body too long to type"
    )
    s.add_argument("--on-behalf-of", help="the open task a person asked you for, when something else started this run")
    s.set_defaults(fn="api")
    cli_spec.add(sub, computers.COMPUTER)  # tico computer: clients/commands/computers.py
    # `tico credential` (clients/commands/tools.py): every leaf but set, which reads the value from standard input.
    cli_spec.add(sub, tools_commands.CREDENTIAL)
    cred = _group_subparsers(sub, "credential")
    s = cred.add_parser("set", help="store a credential a person gave you, for one bot; the value is read from stdin")
    s.add_argument("env")
    s.add_argument("--for-bot", dest="for_bot", required=True)
    s.add_argument("--name")
    s.add_argument("--kind", choices=["api_key", "token", "password", "connection"])
    s.add_argument("--username")
    s.add_argument("--no-redact", dest="no_redact", action="store_true", help="leave the pasted words in the chat")
    s.set_defaults(fn="credential set")
    cli_spec.add(sub, tools_commands.CREDENTIAL_AFTER_SET, parents={("credential",): cred})
    cli_spec.add(sub, tools_admin.CREDENTIAL_CLI, parents={("credential",): cred})  # clients/commands/tools_admin.py
    cli_spec.add(sub, tools_commands.SLACK)  # tico slack: clients/commands/tools.py
    cli_spec.add(sub, computers.SUPPORT)  # tico support: clients/commands/computers.py
    cli_spec.add(sub, finance.CLI)  # tico finance: clients/commands/finance.py
    # Contacts (backend/entities/routes.py): every reader sees what the read rule lets it see; writes name their actor.
    cli_spec.add(sub, products_commands.PRODUCTS)  # tico products: clients/commands/products.py (backend/products.py)
    cli_spec.add(sub, page_history_commands.PAGE_HISTORY)  # tico page-history: clients/commands/page_history.py
    cli_spec.add(sub, contacts_commands.CONTACTS)  # tico contacts: clients/commands/contacts.py (not attribute add)
    attribute = (
        _group_subparsers(sub, "contacts")
        .add_parser("attribute", help="add an attribute to a type")
        .add_subparsers(dest="subsub")
    )
    s = attribute.add_parser("add", help="add an attribute: name, value type, description, and options for a select")
    s.add_argument("type")
    s.add_argument("key")
    s.add_argument("--name", required=True)
    s.add_argument("--value-type", dest="value_type", required=True, help="text, number, date, select, status ...")
    s.add_argument("--description", required=True, help="what it holds; required")
    s.add_argument(
        "--option", dest="options", action="append", type=cli_spec.pair, metavar="KEY=LABEL", help="a select's option"
    )
    s.add_argument("--multiple", action="store_true")
    s.add_argument("--unique", dest="unique_value", action="store_true")
    s.add_argument("--required", action="store_true")
    s.add_argument("--sensitive", action="store_true", help="only Finance's owner and the system read it")
    s.add_argument("--owner", help="tico (the default) or connection:<id>")
    s.set_defaults(fn="contacts attribute add")
    cli_spec.add(sub, task_work_commands.GENERAL_MANAGER)  # tico general-manager: clients/commands/task_work.py
    cli_spec.add(sub, computers.MAINTENANCE)  # tico maintenance: clients/commands/computers.py
    roadmap = sub.add_parser(
        "roadmap", help="public roadmaps: ranking and company settings (docs/roadmaps.md)"
    ).add_subparsers(dest="sub")
    s = roadmap.add_parser("tasks", help="roadmap tasks ranked by requests, organization or size")
    s.add_argument("--sort", choices=["requester_count", "external_requesters", "important", "organization", "size"])
    s.add_argument("--direction", choices=["asc", "desc"])
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="roadmap tasks")
    s = roadmap.add_parser(
        "settings", help="read the company's roadmap settings; any flag changes those named (owners and admins)"
    )
    s.add_argument(
        "--label", dest="labels", action="append", help="a label shown publicly; repeat for more, '' for none"
    )
    s.add_argument(
        "--cors-origin",
        dest="cors_origins",
        action="append",
        help="an exact website origin; repeat for more, '' for none",
    )
    s.add_argument("--action-limit", dest="action_limit", type=int, help="visitor writes per address per day")
    s.add_argument("--suggestion-limit", dest="suggestion_limit", type=int, help="visitor suggestions waiting at once")
    s.add_argument(
        "--bot-public-approval",
        dest="bot_public_approval",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="bots need an approval to publish",
    )
    who = s.add_mutually_exclusive_group()
    who.add_argument(
        "--suggestions-bot", dest="suggestions_bot", metavar="SLUG", help="the bot that receives visitor suggestions"
    )
    who.add_argument(
        "--automatic",
        action="store_true",
        help="suggestions go to the Automatic bot (the Product Owner, then the Product Lead)",
    )
    s.set_defaults(fn="roadmap settings")
    keys = sub.add_parser(
        "service-key", help="keys another system uses to file tasks, or to update this install (owner and admins)"
    ).add_subparsers(dest="sub")
    s = keys.add_parser("create", help="make a key; it is shown this once")
    s.add_argument("--label", required=True, help="the system that holds it; every task it files says so")
    s.add_argument(
        "--scope",
        choices=["tasks", "update"],
        default="tasks",
        help="tasks (default): files tasks; update (owner only): checks for, starts and follows an update of this install",
    )
    s.set_defaults(fn="service-key create")
    keys.add_parser("list", help="every service key, never its secret").set_defaults(fn="service-key list")
    s = keys.add_parser("revoke", help="stop a key at once")
    s.add_argument("id")
    s.set_defaults(fn="service-key revoke")
    cli_spec.add(sub, org_commands.SPEND)  # tico usage, billing, reach: clients/commands/org.py
    from clients.commands import connections

    connections.add_commands(sub)  # `tico github`, and the connection verbs of `tico repo`
    cli_spec.add(sub, computer_upkeep.UPKEEP)  # tico upgrade, cloud, subscription: clients/commands/computer_upkeep.py
    cli_spec.add(sub, pipeline_commands.CLI)  # tico pipeline, stage, company, requirement: commands/pipelines.py
    hubcli_pipelines.add(task)  # task request|approve|send-back|wait
    return p


RUNNER_CONFIG = Path.home() / ".config" / "tico" / "runner.json"
# Read-only commands a person or a script on the Mac may run outside a turn with the runner credential.
# The commands that work in this computer's workspace of bot repositories.
WORKSPACE_COMMANDS = (
    "bot check",
    "bot copy",
    "bot update-from-original",
    "bot suggest-to-original",
    "skill copy",
    "skill submit",
)
READ_OUTSIDE_A_TURN = ("sql", "tool list", "tool show", "tool query-search")


# ----------------------------------------------------------------------------- the last release's spellings
def _goal_auto(rest):  # tico goal auto <id>  ->  tico goal status <id> auto
    return rest[:1] + ["auto"] + rest[1:]


def _context_search(rest):  # --source all|docs|market  ->  --market (docs are always searched)
    out, market, i = [], True, 0
    while i < len(rest):
        if rest[i] == "--source" and i + 1 < len(rest):
            market, i = rest[i + 1] != "docs", i + 2
        elif rest[i].startswith("--source="):
            market, i = rest[i].split("=", 1)[1] != "docs", i + 1
        else:
            out.append(rest[i])
            i += 1
    return out + (["--market"] if market else [])


def _gtm_how(rest):
    """`tico gtm show|summary` is the How we sell section now: the old page's flags are gone, `--json` stays."""
    return [a for a in rest if a == "--json"]


# (old words, new words, words added at the end, rewrite of what follows). The old spellings are not in the parser, so
# they are not in `tico --help`; `rename_argv` maps a command line onto the new one and says so on stderr. They are removed
# one release after the rename. The longest old spelling that matches wins.
RENAMED = [
    (("say",), ("message", "send"), (), None),
    (("notice",), ("message", "send"), ("--fyi",), None),
    (("inbox",), ("message", "list"), (), None),
    (("ack",), ("message", "mark-read"), (), None),
    (("history",), ("conversation", "show"), (), None),
    (("notes",), ("note", "list"), (), None),
    (("unnote",), ("note", "delete"), (), None),
    (("board",), ("task", "list"), ("--all",), None),
    (("goals",), ("goal", "list"), (), None),
    (("goal", "auto"), ("goal", "status"), (), _goal_auto),
    (("goal", "checkins"), ("goal", "checkin-list"), (), None),
    (("goal", "checkin", "list"), ("goal", "checkin-list"), (), None),
    (("kpi", "add"), ("kpi", "create"), (), None),
    (("kpi", "readings"), ("kpi", "show"), (), None),
    (("context", "search"), ("doc", "search"), (), _context_search),
    (("context", "show"), ("doc", "read"), (), None),
    (("docs", "links"), ("doc", "link-list"), (), None),
    (("docs",), ("doc",), (), None),
    (("files", "add-link"), ("file", "link"), (), None),
    (("files",), ("file",), (), None),
    (("meetings", "transcript"), ("meeting", "read"), (), None),
    (("meetings",), ("meeting",), (), None),
    (("listen",), ("listening",), (), None),
    (("tools",), ("tool",), (), None),
    (("integrations",), ("tool", "list"), (), None),
    (("integration",), ("tool", "show"), (), None),
    (("queries",), ("tool", "query-search"), (), None),
    (("learn",), ("tool", "learn"), (), None),
    (("status",), ("bot", "status"), (), None),
    (("recent",), ("bot", "recent"), (), None),
    (("turns",), ("run", "list"), (), None),
    (("org",), ("team", "show"), (), None),
    (("chat", "send"), ("message", "send"), (), None),
    (("fleet",), ("health", "check"), (), None),
    (("fleet-check",), ("health", "check"), (), None),
    (("computers",), ("computer", "list"), (), None),
    (("catalog",), ("template", "list"), (), None),
    (("bot", "register"), ("bot", "create"), ("--record-only",), None),
    (("bot", "set"), ("bot", "update"), (), None),
    (("bot", "onboarded"), ("bot", "setup-done"), (), None),
    (("github", "create-bot-repo"), ("bot", "repo-create"), (), None),
    (("people",), ("human",), (), None),
    (("person",), ("human",), (), None),
    (("update", "post"), ("update", "create"), (), None),
    (("update", "read"), ("update", "mark-read"), (), None),
    (("updates",), ("update", "list"), (), None),
    (("batch",), ("needs-you",), (), None),
    (("live", "brief"), ("brief",), (), None),
    (("live", "stats"), ("mcp", "stats"), (), None),
    (("calendar", "upcoming"), ("calendar", "list"), (), None),
    (("decisions",), ("decision", "ask"), (), None),
    (("judge",), ("decision", "ask"), (), None),
    (("gtm", "show"), ("strategy", "show", "--section", "how"), (), _gtm_how),
    (("gtm", "summary"), ("strategy", "show", "--section", "how"), (), _gtm_how),
    (("gtm", "sources"), ("growth", "source", "list"), (), None),
]
RENAMED.sort(key=lambda rule: -len(rule[0]))

# Commands taken away (asks in Tico 0.5, the old task engine in 0.6): each prints one line naming what to use instead
# and exits 1, so a bot told an old command does not loop on it. The longest old spelling that matches wins.
ASK_INSTEAD = (
    'ask a person on your task with: tico task ask <id> --to <person> "<question>" (a message marked ❓); '
    'ask a bot with: tico message send <bot> "<question>" --needs-action'
)
ANSWER_INSTEAD = (
    "a person replies in Needs you; a bot named on an OK answers with: "
    "tico task answer <id> <requirement> ok|no --version N"
)
WAIT_INSTEAD = (
    "a task waits only on a person it asks (tico task ask) or a task it files (tico task file --parent <id>); "
    'else tico task snooze <id> --until DATE -m "why" --version N'
)
MOVE_INSTEAD = (
    'move a task with: tico task done <id> -m "what was done" --version N, or tico task move <id> next|<stage> '
    '-m "why" --version N; tico task show <id> says what it takes to leave its stage'
)
PIPELINE_INSTEAD = "a pipeline's settings are a person's, on its page; tico task show <id> says where a task is"
REMOVED = [
    (("question", "ask"), ASK_INSTEAD),
    (("question", "answer"), ANSWER_INSTEAD),
    (("question",), ASK_INSTEAD),
    (("ask",), ASK_INSTEAD),
    (("answer",), ANSWER_INSTEAD),
    (("task", "question"), ASK_INSTEAD),
    (("task", "ask-resolve"), ANSWER_INSTEAD),
    (("task", "answers"), "the answers are on the task: tico task show <id>, under To leave"),
    (("approval", "request"), ASK_INSTEAD),
    (("approval",), ANSWER_INSTEAD),
    (("approvals",), ANSWER_INSTEAD),
    (("task", "check-back"), WAIT_INSTEAD),
    (("task", "later"), WAIT_INSTEAD),
    (("task", "defer"), WAIT_INSTEAD),
    (("task", "stuck"), "Tico's own check finds a task that is stuck; tico task list lists tasks"),
    (("task", "bulk"), "a person changes many tasks at once on the Tasks page; a bot changes one task at a time"),
    (("task", "waive-qa"), MOVE_INSTEAD),
    (("task", "thumbs"), "asks no longer take a thumbs up or down; answer the ask: tico task answer"),
    (("task", "type"), PIPELINE_INSTEAD),
    (("task", "types"), PIPELINE_INSTEAD),
    (
        ("growth", "idea"),
        'a growth idea is a task: tico task create --owner <bot> --title "..." --label channel:<slug>',
    ),
]
REMOVED.sort(key=lambda rule: -len(rule[0]))
NOTICE_COMMANDS = ("list",)  # the subcommands `tico notice` has now


def parse(p, argv):
    """`p.parse_args(argv)`, except that a `--json` no command on the line takes is ignored, so a bot that adds it out of
    habit gets the command's normal answer instead of a usage error. One parse: a value read from standard input is read
    once, a `--json` after `--` stays a value, and a command with its own `--json` (`tico tool list`) still gets it."""
    p.hub_argv = list(argv)  # the errors below name the closest command the line could have meant
    args, extra = p.parse_known_args(argv)
    left = [a for a in extra if a != "--json"]
    if left:
        p.error("unrecognized arguments: " + " ".join(left))
    return args


def removed_notice(argv):
    """The one line for a command that is gone (REMOVED), or None."""
    for old, instead in REMOVED:
        if tuple(argv[: len(old)]) == old:
            return f'{PROG}: "{" ".join(old)}" is gone; {instead}'
    return None


def rename_argv(argv):
    """`argv` for the new spelling of an old command line, and the notice to print (or None when nothing was renamed)."""
    argv = list(argv)
    if (
        argv[:1] == ["note"]
        and len(argv) > 1
        and argv[1] not in ("create", "list", "delete")
        and not argv[1].startswith("-")
    ):
        return ["note", "create"] + argv[1:], '"tico note <bot> <text>" is now "tico note create <bot> <text>"'
    if argv[:1] == ["notice"] and len(argv) > 1 and (argv[1] in NOTICE_COMMANDS or argv[1].startswith("-")):
        return argv, None  # `tico notice list` is new; only `tico notice <text>` is the old send
    from clients.commands.org import ORG_COMMANDS

    if argv[:1] == ["org"] and argv[1:2] and argv[1] in ORG_COMMANDS:
        return argv, None  # `tico org` alone is the old `tico team show`; its subcommands are new
    for old, new, tail, rewrite in RENAMED:
        if tuple(argv[: len(old)]) == old:
            rest = argv[len(old) :]
            rest = (rewrite(rest) if rewrite else rest) + list(tail)
            return list(new) + rest, f'"{PROG} {" ".join(old)}" is now "{PROG} {" ".join(new)}"'
    return argv, None


def runner_credential(path=None):
    """This Mac's runner credential as (url, token), or None when it is not registered.

    `tico sql` and the integration reads use it; the API answers a runner's SQL as the person
    who registered the machine. That is how a script or an agent on the Mac reads without a
    browser session.
    """
    try:
        config = json.loads(Path(path or RUNNER_CONFIG).read_text())
        return str(config["url"]), str(config["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def split_human(argv):
    """Pull `--human <id>` out of anywhere in the line, so it works before or after the command."""
    argv, who = list(argv), None
    for i, a in enumerate(argv):
        if a == "--human" and i + 1 < len(argv):
            who = argv[i + 1]
            return argv[:i] + argv[i + 2 :], who
        if a.startswith("--human="):
            return argv[:i] + argv[i + 1 :], a.split("=", 1)[1]
    return argv, who


# Words that were this computer's commands under `tico` before it became the teamwork CLI (scripts/tico): a person who
# types one is told where the computer's command is now. A bot turn is not, so it does not reach for tico-computer.
COMPUTER_WORDS = {("status",): "status", ("update",): "update"}


def main(argv):
    argv, who = split_human(argv)
    argv, profile, fields = cli_plumbing.split_globals(argv)
    if argv == ["help"]:  # `tico help` reads like `tico --help`
        argv = ["--help"]
    word = COMPUTER_WORDS.get(tuple(argv))
    if word and not (os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE")):
        print(f"tico: this computer's own {word} is `tico-computer {word}`", file=sys.stderr)
    gone = removed_notice(argv)
    if gone:
        print(gone, file=sys.stderr)
        return 1
    argv, renamed = rename_argv(argv)
    if renamed:
        print(f"{PROG}: {renamed}; the old name stops working in the next release", file=sys.stderr)
    extras = {k: v for k, v in (("profile", profile), ("fields", fields)) if v}
    return run(argv, who, **extras)


def run(argv, who, profile=None, fields=None):
    """One command line in its current spelling: parsed here, then answered here or sent to the hub. `profile` and
    `fields` are the global --profile and --fields main() took off the line (clients/cli_plumbing.py)."""
    p = parser()
    try:
        args = parse(p, argv)
    except SystemExit as e:  # argparse already said what was wrong, or printed help
        return 0 if not e.code else cli_plumbing.EXIT_USAGE
    if not getattr(args, "fn", None):
        p.print_help(sys.stderr)
        return cli_plumbing.EXIT_USAGE
    local = cli_plumbing.local_command(args)  # tico auth add|list|use|remove: no company needed
    if local is not None:
        return local
    try:
        if args.fn == "task create" and args.dry_run:  # never reaches remotecli: nothing to send
            return cmd_task_dry_run(args, who)
        if args.fn == "decision ask" and args.list:  # the sets are files in this checkout, no API needed
            from clients import judge

            print(json.dumps(judge.list_sets(), indent=2))
            return 0
        if args.fn == "bot check-names":  # reads the bot's own files here: no hub, no credential
            from clients import bot_names

            return bot_names.command(args.target, args.fix, os.environ.get("HUB_WORKSPACE"))
        if args.fn == "doc fetch":  # runs here, beside the bot: no hub, no credential
            from clients import doc_fetch

            try:
                print(json.dumps(doc_fetch.fetch(args.url, args.max_chars), indent=2))
                return 0
            except doc_fetch.FetchError as e:
                print(json.dumps({"error": e.code, "detail": e.message}, indent=2))
                return 1
        if args.fn == "tool call":  # runs here, through the tool proxy: no hub API, the run's token
            from clients import tool_call

            try:
                data = json.loads(args.data) if args.data is not None else None
                print(json.dumps(tool_call.call(args.tool, args.method, args.path, data, args.query), indent=2))
                return 0
            except (tool_call.CallError, ValueError) as e:
                print(json.dumps({"error": str(e)}, indent=2))
                return 1
        from clients import profiles

        try:
            profiles.resolve(profile)  # --profile, TICO_PROFILE, else HUB_API_URL, else the default profile
        except profiles.ProfileError as e:
            raise NoCredential(str(e), "profile") from None
        if not os.environ.get("HUB_API_URL"):
            local = args.fn in READ_OUTSIDE_A_TURN and not (args.fn == "tool list" and args.bot)
            credential = runner_credential(os.environ.get("HUB_RUNNER_CONFIG")) if local else None
            if not credential:
                raise NoCredential(
                    "HUB_API_URL is not set: `tico` talks to the Tico API and runs inside a "
                    "bot turn, where the runner sets HUB_API_URL, HUB_TOKEN and HUB_BOT"
                    + (
                        f"; outside a turn `tico {args.fn}` needs this Mac's runner credential (scripts/setup-runner.sh)"
                        if local
                        else ""
                    )
                    + cli_plumbing.credential_hint()
                )
            os.environ["HUB_API_URL"], os.environ["HUB_TOKEN"] = credential
            profiles.ACTIVE.update(profile=None, source="runner")
        if (args.fn in WORKSPACE_COMMANDS or args.fn == "bot create" and not args.record_only) and not os.environ.get(
            "HUB_WORKSPACE"
        ):
            raise CliError(
                "HUB_WORKSPACE is not set: bot repositories live in the workspace this "
                "machine was enrolled with, which the runner gives every turn"
            )
        if args.fn == hubcli_pipelines.WAIT:
            if os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE"):
                print(f'{PROG}: "task wait" is for a person\'s shell; {WAIT_INSTEAD}', file=sys.stderr)
                return 1
            from clients.commands import pipelines as hubtools_pipelines

            return hubtools_pipelines.command(args)
        from clients import remotecli

        if args.fn == "events tail":
            from clients.tico import Client

            return cli_plumbing.events_tail(
                Client(os.environ["HUB_API_URL"], os.environ.get("HUB_TOKEN", "")), args, fields
            )
        return remotecli.main(args, who, **({"fields": fields} if fields else {}))
    except NoCredential as e:
        print(json.dumps({"error": e.code, "detail": str(e)} if e.code else {"error": str(e)}, indent=2))
        return cli_plumbing.EXIT_SIGN_IN
    except CliError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
