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


def thumb(value):
    """up or down, as the thumbs route's `up`."""
    if value not in ("up", "down"):
        raise argparse.ArgumentTypeError("up or down")
    return value == "up"


def json_value(value):
    """`--content`: any JSON value, read exactly; a plain word that is not JSON is refused."""
    try:
        return json.loads(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--content is JSON, for example '{\"usd\": \"10.00\"}'") from None


def choice_list(value):
    """`--choices a,b`: the answers a person picks from, in order."""
    return [choice.strip() for choice in value.split(",") if choice.strip()]


GO_LIVE_HELP = """When the computer does not have the bot's repository yet, go-live keeps the placement and answers
waiting_for_repository: the computer clones it, and Tico finishes going live when it reports the repository."""


def out(obj):
    print(json.dumps(obj, indent=2, default=str, sort_keys=False))
    return 0


def body_of(args):
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text()
    return args.body or ""


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


def cmd_task_dry_run(args, who):
    """`tico task create --dry-run`: print what a create would be refused for; write nothing.

    No database is opened and no request is made; the owner's kind comes from the registry
    files and the same lint runs here, so a bot on the cloud API can size a human item before
    it spends a turn on a refusal. Prints lines rather
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


def _pair(text):
    """`KEY=VALUE` on a command line, split at the first `=`: a match, a value or an option of `tico contacts`."""
    key, sep, value = str(text).partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError(f"{text} is not KEY=VALUE")
    return key.strip(), value


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


# A repeated event's edit: this one occurrence, it and the later ones, or the whole series.
CALENDAR_SCOPES = ("this", "following", "all")


def parser():
    p = HubParser(
        prog=PROG,
        description="Tico: tasks, messages, bots and the rest of your team's work",
        epilog="This computer's own commands: tico-computer help",
    )
    p.add_argument("--version", action=_Version, help="print the Tico version")
    sub = p.add_subparsers(dest="cmd")
    from clients.commands import mail as mail_commands, rules as rule_commands, strategy as strategy_commands
    from clients.commands import market as market_commands
    from clients.commands import company_page, finance, listening as listening_commands, threads as thread_commands
    from clients.commands import webhooks
    from clients.commands import computer_upkeep, computers, org as org_commands, pipelines as pipeline_commands
    from clients.commands import gateway as gateway_commands, goals as goal_commands
    from clients.commands import admin as admin_commands, content as content_commands, task_work as task_work_commands

    sub.add_parser("whoami", help="who you are, to which company, and through which profile").set_defaults(fn="whoami")
    cli_plumbing.add_parsers(sub)  # tico auth, tico token, tico events tail (clients/cli_plumbing.py)

    alerts = sub.add_parser('alerts', help='your own phone and paid alert preferences').add_subparsers(dest='sub')
    for action in ('show', 'remove', 'confirm', 'verify', 'test', 'set', 'company'):
        item = alerts.add_parser(action)
        item.set_defaults(fn='alerts ' + action)
        if action == 'confirm':
            item.add_argument('code')
        if action == 'verify':
            item.add_argument('phone')
            item.add_argument('--sms-consent', action='store_true', required=True)
            item.add_argument('--call-consent', action='store_true', default=False)
        if action == 'test':
            item.add_argument('--accept-charge', action='store_true', required=True, help='company pays $1 if accepted')
            item.add_argument('--operation-id', required=True, help='stable id for this paid test and retries')
        if action in ('set', 'company'):
            item.add_argument('--monthly-cap', type=int)
        if action == 'set':
            for flag in ('urgent-needs', 'legal-mail', 'bot-stuck', 'urgent-calls', 'emergency-exception'):
                item.add_argument('--' + flag, action=argparse.BooleanOptionalAction, default=None)
            for flag in ('timezone', 'quiet-start', 'quiet-end'):
                item.add_argument('--' + flag)

    cli_spec.add(sub, listening_commands.INBOUND)  # tico inbound: clients/commands/listening.py
    cli_spec.add(sub, mail_commands.LETTER)  # tico letter and tico mail: clients/commands/mail.py
    cli_spec.add(sub, mail_commands.MAIL)
    cli_spec.add(sub, mail_commands.CLI)  # tico email: clients/commands/mail.py

    cli_spec.add(sub, company_page.CLI)  # tico company-page, audiences, editions, readers

    meeting = sub.add_parser(
        "meeting", help="search meetings, read transcripts, import a transcript from another tool"
    ).add_subparsers(dest="sub")
    granola = meeting.add_parser(
        "granola", help="your Granola account; connect in Meetings in your browser"
    ).add_subparsers(dest="granola_action")
    for action in ("status", "sync"):
        granola.add_parser(action).set_defaults(fn="meeting granola " + action)
    s = meeting.add_parser("link", help="link existing tasks to a saved meeting")
    s.add_argument("id")
    s.add_argument("task_ids", nargs="+")
    s.set_defaults(fn="meeting link")
    s = meeting.add_parser("unlink", help="remove a meeting's task link")
    s.add_argument("id")
    s.add_argument("task")
    s.set_defaults(fn="meeting unlink")
    s = meeting.add_parser("search")
    s.add_argument("q", nargs="?", default="")
    s.add_argument("--person", default="")
    s.add_argument("--since")
    s.add_argument("--until")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.set_defaults(fn="meeting search")
    s = meeting.add_parser("read", help="read a transcript")
    s.add_argument("id")
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--limit", type=int, default=20000)
    s.add_argument("--notes", action="store_true", default=None, help="read only the meeting's notes")
    s.set_defaults(fn="meeting read")
    s = meeting.add_parser("delete", help="delete a Meeting you may edit")
    s.add_argument("id")
    s.set_defaults(fn="meeting delete")
    meeting.add_parser("pending", help="your Pending queue").set_defaults(fn="meeting pending")
    s = meeting.add_parser("approve", help="share your Pending meetings")
    target = s.add_mutually_exclusive_group(required=True)
    target.add_argument("id", nargs="?")
    target.add_argument("--all", action="store_true")
    s.add_argument("--private", action="store_true", default=None)
    s.set_defaults(fn="meeting approve")
    for action in ("dismiss", "restore"):
        s = meeting.add_parser(action)
        s.add_argument("id")
        s.set_defaults(fn="meeting " + action)
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

    feedback = sub.add_parser(
        "feedback", help="a person's 👍/👎 on a bot's work: give, list, make a rule"
    ).add_subparsers(dest="sub")
    s = feedback.add_parser("give", help="rate a message or task a bot made; again replaces it (an ask: task thumbs)")
    s.add_argument("subject_type", choices=("message", "task"))
    s.add_argument("subject_id")
    s.add_argument("rating", choices=("up", "down"))
    s.add_argument("--reason")
    s.add_argument("--tell-bot", dest="tell_bot", action="store_true", help="a 👎 only: a note for the bot's next run")
    s.set_defaults(fn="feedback give")
    s = feedback.add_parser("remove", help="take back your feedback, and its note if no run carried it")
    s.add_argument("id")
    s.set_defaults(fn="feedback remove")
    s = feedback.add_parser("markers", help="your feedback on these subjects and everyone's up/down counts")
    s.add_argument("subject_type", choices=("message", "task", "ask"), help="an ask's id: <visit-id>:<requirement-id>")
    s.add_argument("subject_ids", nargs="+")
    s.set_defaults(fn="feedback markers")
    s = feedback.add_parser("list", help="feedback newest first, a page at a time")
    s.add_argument("--bot", dest="bot_id")
    s.add_argument("--rating", choices=("up", "down"))
    s.add_argument("--since", help="an ISO time, or 24h / 7d")
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="feedback list")
    feedback.add_parser("reasons", help="your five latest distinct reasons").set_defaults(fn="feedback reasons")
    s = feedback.add_parser("rule-draft", help="a one-sentence rule drafted from your 👎; stores nothing")
    s.add_argument("id")
    s.set_defaults(fn="feedback rule-draft")
    s = feedback.add_parser("rule", help="your 👎 as an org rule: active if you manage the bot, else suggested")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.set_defaults(fn="feedback rule")

    files = sub.add_parser(
        "file", help="what you publish for people: reports, documents, imported objects"
    ).add_subparsers(dest="sub")
    s = files.add_parser("archive", help="archive a File with your rights")
    s.add_argument("id")
    s.set_defaults(fn="file archive")
    s = files.add_parser("list", help="the files on your page, newest activity first")
    s.add_argument("--bot")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor")
    s.set_defaults(fn="file list")
    s = files.add_parser("publish", help="upload a file from this checkout (reports/x.md); again adds a version")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--task", help="the task it is for; defaults to the one you are working on")
    s.add_argument("--scope", choices=["task", "bot"])
    s.set_defaults(fn="file publish")
    s = files.add_parser("link", help="list a Google Doc, Notion page, Figma file or any https link")
    s.add_argument("url")
    s.add_argument("--title")
    s.add_argument("--task")
    s.add_argument("--scope", choices=["task", "bot"])
    s.set_defaults(fn="file link")
    s = files.add_parser("touch", help="you edited a linked document again: move it to the top")
    s.add_argument("target", help="a file id or its https link")
    s.set_defaults(fn="file touch")
    s = files.add_parser("import", help="copy an s3:// object with this computer's credentials into Tico")
    s.add_argument("uri")
    s.add_argument("--title")
    s.add_argument("--task")
    s.set_defaults(fn="file import")
    s = files.add_parser("search", help="every file you may read, by title and text")
    s.add_argument("q")
    s.add_argument("--kind", choices=["page", "document", "image", "data", "media", "other"])
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="file search")
    s = files.add_parser("read", help="one version of a file you may read, as text, in full (default: the current one)")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.set_defaults(fn="file read")
    s = files.add_parser("delete", help="the owner deletes a file for good: its bytes leave storage")
    s.add_argument("id")
    s.set_defaults(fn="file delete")
    s = files.add_parser(
        "approved",
        help="exit 0 when a person approved this exact version on a review question, "
        "1 when not (its JSON answer says \"approved\": false), 2 on bad input. "
        "A failed call exits 1 or 2 with an error answer: check the answer, not only "
        "the code",
    )
    s.add_argument(
        "target", help="a path in this checkout (its bytes now), or FILE[@N] (the current version without @N)"
    )
    s.add_argument("--by", help="whose verdicts count: human (default), any, or bot:SLUG")
    s.set_defaults(fn="file approved")
    s = files.add_parser("review", help="approve or ask for changes on another bot's file version, on a task you read")
    s.add_argument(
        "target", help="FILE[@N] (the current version without @N), or a path in this checkout (its bytes now)"
    )
    verdict = s.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--approve", dest="verdict", action="store_const", const="approve")
    verdict.add_argument("--changes", dest="verdict", action="store_const", const="changes")
    s.add_argument("--task", required=True, help="the task the review is for")
    s.add_argument("--note", help="what to change, or why it is approved")
    s.set_defaults(fn="file review")
    cli_spec.add(sub, content_commands.PAGE)  # tico page: clients/commands/content.py
    cli_spec.add(sub, content_commands.CARD)  # tico card
    cli_spec.add(sub, content_commands.PIN)  # tico pin
    docs = sub.add_parser(
        "doc", help="read and search team docs and external sources, write internal docs, ask the Librarian"
    ).add_subparsers(dest="sub")
    s = docs.add_parser("archive", help="archive an internal Doc with your rights")
    s.add_argument("ref")
    s.set_defaults(fn="doc archive")
    s = docs.add_parser("list", help="internal docs by path")
    s.add_argument("--prefix", help="only paths starting with this, e.g. sales/")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor", help="the page after this one: a page's next_cursor")
    s.set_defaults(fn="doc list")
    s = docs.add_parser("read", help="one internal or indexed external doc in full")
    s.add_argument("ref", help="a doc id/path, external:<id>, or manual:<name>")
    s.add_argument("--revision", help="external commit returned by search")
    s.set_defaults(fn="doc read")
    s = docs.add_parser("search", help="search internal, indexed external and linked docs, then the Tico manual")
    s.add_argument("q")
    s.add_argument("--limit", type=int)
    s.add_argument(
        "--manual",
        dest="collection",
        action="store_const",
        const="manual",
        help="only the read-only Tico manual (how to do something in Tico)",
    )
    s.add_argument("--team", dest="collection", action="store_const", const="team", help="only team docs")
    s.add_argument("--market", action="store_true", default=None, help="also the market's notes, entities and evidence")
    s.add_argument("--source-id", help="only this external source")
    s.set_defaults(fn="doc search")
    s = docs.add_parser("write", help="create or replace an internal doc at a path")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--body-file", dest="body_file", help="the Markdown file; standard input when omitted")
    s.add_argument("--note", help="one line on what changed")
    s.set_defaults(fn="doc write")
    s = docs.add_parser("history", help="the versions of a doc")
    s.add_argument("ref", help="a doc id or a path")
    s.set_defaults(fn="doc history")
    s = docs.add_parser("link-list", help="the linked docs: where the company's other docs live")
    s.set_defaults(fn="doc link-list")
    chat = sub.add_parser("chat", help="bot chat goals and commands").add_subparsers(dest="sub")
    s = chat.add_parser("goal", help="read, set, edit, pause, resume or clear a chat goal")
    s.add_argument("conversation_id")
    s.add_argument("action", nargs="?", default="get", choices=["get", "set", "edit", "pause", "resume", "clear"])
    s.add_argument("objective", nargs="?")
    s.set_defaults(fn="chat goal")
    assistant = sub.add_parser("assistant", help="the Assistant's proposals for a person to confirm").add_subparsers(
        dest="sub"
    )
    assistant.add_parser("read", help="read your private Assistant chat").set_defaults(fn="assistant read")
    s = assistant.add_parser("send", help="ask your private Assistant")
    s.add_argument("text")
    s.set_defaults(fn="assistant send")
    s = assistant.add_parser("propose", help="ask the person to confirm one side-effecting operation")
    s.add_argument("--summary", required=True)
    s.add_argument("--path", required=True)
    s.add_argument("--method", default="POST", choices=["POST", "PUT", "PATCH", "DELETE"])
    s.add_argument("--body", default="{}", help="the request's JSON body")
    s.add_argument(
        "--for",
        dest="for_person",
        default=None,
        help="the Assistant's own background run only: the person (human:<id>) whose chat gets the card",
    )
    s.set_defaults(fn="assistant propose")
    s = assistant.add_parser("morning", help="the Assistant's one morning message to a person (background run only)")
    s.add_argument("--for", dest="for_person", required=True, help="the person (human:<id>) whose chat gets it")
    s.add_argument("--body", required=True, help="the message, in Markdown, at most 2000 characters")
    s.set_defaults(fn="assistant morning")
    s = assistant.add_parser("learn", help="save one thing the person taught you (What it has learned)")
    s.add_argument("text", help="one short line, in their words")
    s.add_argument(
        "--company",
        dest="layer",
        action="store_const",
        const="company",
        default="personal",
        help="the Strategy Owner in the owner's chat: about the company, not how they like things",
    )
    s.set_defaults(fn="assistant learn")
    assistant.add_parser(
        "learned", help="what your chat bot has learned: your layer, and the company's for the owner"
    ).set_defaults(fn="assistant learned")
    s = assistant.add_parser("withdraw", help="take back a Confirm card that waits: it never runs (any teammate)")
    s.add_argument("id", help="the card's proposal id (tico assistant proposals)")
    s.add_argument("--reason", default="", help="why, in one short sentence: the person reads it in their chat")
    s.set_defaults(fn="assistant withdraw")
    change = assistant.add_parser(
        "change", help="what your Assistant did for you, and undoing one of those changes"
    ).add_subparsers(dest="subsub")
    s = change.add_parser("list", help="the Assistant's changes for you, newest first, with the words it acted on")
    s.add_argument("--days", type=int, help="how far back (default 30, at most 90)")
    s.add_argument("--limit", type=int, help="changes per page (default 50, at most 100)")
    s.add_argument("--before", help="the `next` of the page before, to read older changes")
    s.set_defaults(fn="assistant change list")
    s = change.add_parser("restore", help="undo one change as you; restoring it again changes nothing")
    s.add_argument("id", help="the change's id (tico assistant change list)")
    s.set_defaults(fn="assistant change restore")
    s = assistant.add_parser("proposals", help="the Confirm cards still waiting for their owner (find one to withdraw)")
    s.add_argument("--proposer", choices=["botops", "assistant"], default=None, help="only the cards one proposed")
    s.add_argument("--owner", default=None, help="only one person's cards, e.g. human:ana")
    s.set_defaults(fn="assistant proposals")
    cli_spec.add(sub, task_work_commands.TAG)  # tico tag: clients/commands/task_work.py

    task = sub.add_parser("task", aliases=["tasks"]).add_subparsers(dest="sub")
    s = task.add_parser("child", help="create a subtask")
    s.add_argument("parent")
    s.add_argument("--owner", help="who does it; a --check-model subtask is your own (`me`)")
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.add_argument(
        "--check-model",
        metavar="RUNTIME[:MODEL]",
        nargs="?",
        const="auto",
        help="run this subtask of yours on another model, in its own session (e.g. grok, codex:gpt-6.1-sol); "
        "with no value (put the flag after the parent id), the company's check model, from a provider other "
        "than the parent's authors when one can run it",
    )
    s.add_argument(
        "--role",
        dest="check_role",
        choices=("review", "check"),
        help="review files a review child; with --check-model, what that model is asked for (default check)",
    )
    s.set_defaults(fn="task child create")
    s = task.add_parser("tree", help="show nested subtasks")
    s.add_argument("id")
    s.set_defaults(fn="task tree")
    worktree = task.add_parser("worktree", help="create or attach this task's worktree").add_subparsers(
        dest="worktree_sub"
    )
    for operation, argument in (("add", "repo"), ("attach", "path"), ("setup", "repo")):
        s = worktree.add_parser(operation)
        s.add_argument(argument)
        s.add_argument("--task", help="task id; defaults to this run's task")
        s.set_defaults(fn="task worktree " + operation)
    s = task.add_parser(
        "caps",
        help="the task caps: today's count per pair, the tasks closed before the "
        "receiver acted, and the changes bots asked for",
    )
    s.set_defaults(fn="task caps")
    cap = task.add_parser(
        "cap", help="set a task cap: how many tasks sender may create for receiver a day"
    ).add_subparsers(dest="cap_sub")
    s = cap.add_parser(
        "set", help="sender receiver --per-day N; 0 blocks the pair, and with no --per-day the cap is removed"
    )
    s.add_argument("sender", help="bot:<slug> or person:<id>")
    s.add_argument("receiver", help="bot:<slug> or person:<id>, a manager above the sender")
    s.add_argument("--per-day", dest="per_day", type=int, help="tasks a day; 0 blocks the pair")
    s.set_defaults(fn="task cap set")
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
        "--from-private",
        dest="from_private",
        action="store_true",
        help="from a run on your private task: a company-visible task for a bot, in your own words (no "
        "12 words copied from the private work, no secret-shaped value, no file or link of it)",
    )
    s.add_argument("--owner", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="")
    s.add_argument("--body-file", dest="body_file")
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
    s = task.add_parser("done", help="the work of this stage is finished; never pushes past what is missing")
    s.add_argument("id")
    s.add_argument("-m", "--note", help="what was done, in a few plain sentences")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task done")
    s = task.add_parser(
        "move", help="move a task: next (with -m, past what is not met), back to a stage, done, closed or reopen"
    )
    s.add_argument("id")
    s.add_argument("to", help="next, done, closed, reopen, or a stage's name or id")
    s.add_argument("-m", "--why", help="why, in one plain sentence; a bot always says why")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task move")
    s = task.add_parser("close", help="close a task with why; its open sub-tasks close with it")
    s.add_argument("id")
    s.add_argument("-m", "--why", help="why it is closed")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task close")
    s = task.add_parser("reassign", help="give the task to another bot or person for this stage")
    s.add_argument("id")
    s.add_argument("owner", help="a bot slug, a person id, or me")
    s.add_argument("-m", "--why", help="why, in one plain sentence")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument(
        "--set-anyway", action="store_true", help="a person gives it all the same when a stage rule refused this owner"
    )
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task reassign")
    s = task.add_parser(
        "ask", help="ask a person on this task, in one line; it waits for their answer, which wakes you once"
    )
    s.add_argument("id")
    s.add_argument("line", nargs="?", help="the one sentence they are asked, readable on its own")
    s.add_argument("--to", help="the person you ask: their id")
    s.add_argument(
        "--kind", choices=["answer", "ok"], help="answer (the default): a question; ok: approve or send back"
    )
    s.add_argument("--choices", type=choice_list, help="the answers to pick from, a,b")
    s.add_argument("--show", action="append", metavar="REF", help="what they look at: page:12, file:ID@N; repeat")
    s.add_argument("--for", dest="subject", metavar="KIND:ID", help="an OK bound to what you will do, with --content")
    s.add_argument("--content", type=json_value, help="with --for: exactly what the OK covers, as JSON")
    s.add_argument("--withdraw", action="store_true", default=None, help="take back your open ask instead")
    s.add_argument("--requirement", help="with --withdraw: the ask's requirement id, when several may be withdrawn")
    s.add_argument("-m", "--why", help="with --withdraw: why")
    s.add_argument("--version", type=int, help=version_help)
    s.set_defaults(fn="task ask")
    s = task.add_parser("thumbs", help="was this ask worth asking: up or down; a down sends an open ask back")
    s.add_argument("id")
    s.add_argument("requirement", help="the ask's requirement id, from tico task show")
    s.add_argument("up", metavar="up|down", type=thumb, help="up: worth asking; down: what was wrong goes in --note")
    s.add_argument("--note", help="what was wrong, in a few words")
    s.set_defaults(fn="task thumbs")
    s = task.add_parser("answer", help="answer what you are asked on a task: ok, no, again or your answer")
    s.add_argument("id")
    s.add_argument("requirement", help="the requirement's id from tico task show, or a stage's answer by its name")
    s.add_argument("value", help="ok, no (send back, with -m), again (look again), or your answer")
    s.add_argument("-m", "--note", help="what they read with it; required with no")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task answer")
    s = task.add_parser("give", help="give what you are asked on a task to another person")
    s.add_argument("id")
    s.add_argument("requirement", help="the requirement's id, from tico task show")
    s.add_argument("to_person", nargs="?", metavar="PERSON", help=argparse.SUPPRESS)  # the older form of --to
    s.add_argument("--to", help="the person it goes to: their id (required)")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task give")
    s = task.add_parser("file", help="file a task this one waits on; yours unless --owner")
    s.add_argument("title")
    s.add_argument("--parent", required=True, help="the task you work on: it waits until this one ends")
    s.add_argument("--pipeline", help="the pipeline's id; default general")
    s.add_argument("--owner", help="who does it; default you")
    s.add_argument("--body", help="the details; default the title")
    s.add_argument("--due")
    s.add_argument("--label", dest="labels", action="append", help="a label; repeat for more")
    s.add_argument("--private", action="store_true", default=None, help="only its requester, owner and readers see it")
    s.add_argument("--after", action="append", help="a task it merges after (repeat for more): Merge waits for it")
    s.set_defaults(fn="task file")
    s = task.add_parser("update", help="edit a task's fields: title, body, due, labels, privacy, goal; with --version")
    visibility = s.add_mutually_exclusive_group()
    visibility.add_argument("--private", dest="private", action="store_true", default=None)
    visibility.add_argument(
        "--company", dest="private", action="store_false", help="human requester publishes the task"
    )
    s.add_argument("id")
    s.add_argument("--title", help="a new title, checked as a new task's title would be")
    s.add_argument("--body")
    s.add_argument("--due")
    s.add_argument("--label", dest="labels", action="append", help="replace the labels with these; repeat for more")
    s.add_argument("--goal", dest="goal_id", help='the goal this task serves; "" takes it off')
    s.add_argument("-m", "--note", help="what changed, in a sentence")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task update")
    s = task.add_parser("comment", help="leave a comment on a task, on the record with your name")
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
    s = task.add_parser("comment-moderate", help="hide, show again or delete a public comment on a task you manage")
    s.add_argument("id")
    s.add_argument("comment_id")
    s.add_argument("action", choices=["hide", "show", "delete"])
    s.set_defaults(fn="task comment-moderate")
    s = task.add_parser("requesters", help="a task's requesters: internal contacts and counts, never tokens")
    s.add_argument("id")
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="task requesters")
    s = task.add_parser(
        "requester-save", help="add a requester to a task, or update one with --requester-id; nothing is sent"
    )
    s.add_argument("id")
    s.add_argument("--requester-id", dest="requester_id", help="the requester to update; omit to add one")
    s.add_argument("--actor", help="a teammate (verified identity); omit for someone outside")
    for field in ("name", "email", "phone", "other", "organization", "note"):
        s.add_argument("--" + field)
    s.add_argument("--source", choices=["public_form", "inbox", "meeting", "manual"])
    s.add_argument("--important", action=argparse.BooleanOptionalAction, default=None)
    s.set_defaults(fn="task requester-save")
    s = task.add_parser("requester-remove", help="take a requester off a task")
    s.add_argument("id")
    s.add_argument("requester_id")
    s.set_defaults(fn="task requester-remove")
    s = task.add_parser("comment-edit", help="change the text of a comment you wrote; it wakes nobody")
    s.add_argument("--ok", metavar="OK_ID", help="the OK a person gave on exactly this public reply")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.add_argument("text")
    s.set_defaults(fn="task comment-edit")
    s = task.add_parser("comment-delete", help="take back a comment you wrote")
    s.add_argument("id")
    s.add_argument("comment_id", help="the comment's id (its id in the task's comments)")
    s.set_defaults(fn="task comment-delete")
    s = task.add_parser("delete", help="delete a task made by mistake: its requester or a mover, never a bot")
    s.add_argument("id")
    s.set_defaults(fn="task delete")
    s = task.add_parser("deleted", help="deleted tasks you may restore, newest first")
    s.set_defaults(fn="task deleted")
    s = task.add_parser("restore", help="put a deleted task back: whoever deleted it, its requester or a mover")
    s.add_argument("id")
    s.set_defaults(fn="task restore")
    s = task.add_parser("link", help="attach a link: the pull request you opened, an issue, a document, a session")
    s.add_argument("id")
    s.add_argument("url", help="the URL; with --session also a resume command or a reference")
    s.add_argument("--title")
    s.add_argument(
        "--session",
        action="store_true",
        help="another session working this task, tracked only: task chat goes to the owning bot",
    )
    s.add_argument(
        "--wake-once",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="a pull request: wake this task once when its checks finish (pass or fail), it is approved or "
        "gets changes requested, or a release ships it; never changes the task's status. "
        "--no-wake-once disarms it",
    )
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task link")
    readers = task.add_parser("readers", help="who can see a private task: list, add, remove").add_subparsers(
        dest="subsub"
    )
    s = readers.add_parser("list", help="its requester, owner and readers")
    s.add_argument("id")
    s.set_defaults(fn="task readers list")
    for change, words in (
        ("add", "let them see it: people, bots or group:<id>s"),
        ("remove", "take them off: they stop seeing it"),
    ):
        s = readers.add_parser(change, help=words)
        s.add_argument("id")
        s.add_argument("readers", nargs="+", metavar="X", help="human:<id>, bot:<slug> or group:<id>")
        s.set_defaults(fn="task readers " + change)
    s = task.add_parser("followers", help="who follows a task, each at their level")
    s.add_argument("id")
    s.set_defaults(fn="task followers")
    for change, words in (("follow", "follow a task, or set your level"), ("unfollow", "stop following a task")):
        s = task.add_parser(change, help=words)
        s.add_argument("id")
        if change == "follow":
            s.add_argument("--level", choices=("live", "mentions", "digest", "muted"))
        s.add_argument(
            "--who", help="someone else who sees it (human:<id> or bot:<slug>), when you may change the task"
        )
        s.set_defaults(
            fn="task follow", **({"unfollow": True} if change == "unfollow" else {})
        )  # one tool: tico_task_follow
    s = task.add_parser("relate", help="relate another task to this one: <id> <kind> <task>")
    s.add_argument("id")
    s.add_argument("task")
    s.add_argument(
        "--kind",
        default="related",
        help="related (default); duplicate_of; follow_up: it was split off or followed up from task. A relation is a "
        "note: a task waits only on its requirements and the tasks it filed",
    )
    s.add_argument("--remove", action="store_true", help="take it off instead")
    s.set_defaults(fn="task relate")
    s = task.add_parser("after", help="this task merges after another: <id> <task>; Merge waits for it")
    s.add_argument("id", help="the task that merges later")
    s.add_argument("task", help="the task it merges after")
    s.add_argument("--remove", action="store_true", help="take the order off instead")
    s.set_defaults(fn="task after")
    s = task.add_parser("label", help="add or remove labels on a task")
    s.add_argument("id")
    s.add_argument("--add", action="append")
    s.add_argument("--remove", action="append")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task label")
    s = task.add_parser(
        "attach",
        help="attach a file to a task; prints the link for your note. With --req, attach a page "
        "(page:<file id>[@<version>]) to the stage's page requirement",
    )
    s.add_argument("id")
    s.add_argument("file", help="a local file, or page:<file id>[@<version>] with --req")
    s.add_argument("--req", help='the page requirement it is for, e.g. "Spec page"')
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--name", help="the name people see; defaults to the file's own")
    s.add_argument("--poster", help="a PNG or JPEG preview")
    s.add_argument("--note", help="short note on this version (at most 500 characters)")
    s.set_defaults(fn="task attach")
    s = task.add_parser("context", help="readable page and meeting references for a task")
    s.add_argument("id")
    s.set_defaults(fn="task context")
    s = task.add_parser("meetings", help="meetings linked to a task")
    s.add_argument("id")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor")
    s.set_defaults(fn="task meetings")
    s = task.add_parser("list")
    s.add_argument("--q", help="title words or #N")
    s.add_argument("--owner")
    s.add_argument("--requester")
    s.add_argument("--status", action="append", choices=["open", "done", "closed"])
    s.add_argument("--label")
    s.add_argument("--sort", choices=["queue", "finished"], help="finished: most recently ended first")
    s.add_argument("--number", type=int, help="only this task number")
    s.add_argument("--updated-since", help="only tasks changed after this ISO-8601 time with a timezone")
    s.add_argument("--member", help="only tasks this person or bot is on in some role (me, a human id, a bot slug)")
    s.add_argument("--role", help="with --member: only tasks they are on in this role")
    s.add_argument("--pipeline", help="only this pipeline's tasks: its id (tico pipeline list)")
    s.add_argument(
        "--stage",
        help="only the tasks in this stage: its id, or its name with --pipeline (tico pipeline show <pipeline>)",
    )
    s.add_argument(
        "--waiting-on", help="only open tasks whose turn is this person's or bot's: me, a human id, a bot slug"
    )
    s.add_argument("--brief", action="store_true", help="leave out bodies")
    s.add_argument(
        "--all", action="store_true", help="every task (snoozed ones too) and every bot you may see, as {tasks, bots}"
    )
    s.add_argument(
        "--snoozed",
        choices=["only", "all"],
        help="snoozed tasks are left out: only lists just them, all lists them with the rest",
    )
    s.add_argument("--limit", type=int, help="tasks in a page (500 at most); the answer is then {tasks, next_offset}")
    s.add_argument("--offset", type=int, help="skip this many: a page's next_offset")
    s.set_defaults(fn="task list")
    s = task.add_parser(
        "show",
        help="one task: its version, stage, owner, turn, what it takes to leave the stage, next; several ids: their "
        "fields in one read",
    )
    s.add_argument("id", nargs="+")
    s.add_argument("--json", action="store_true", help="the task as JSON instead of its lines")
    s.set_defaults(fn="task show")
    s = task.add_parser("run", help="start a bot's task now, as the task")
    s.add_argument("id")
    s.set_defaults(fn="task run")
    s = task.add_parser(
        "model",
        help="the model this task's runs use, with its effort and harness; --clear puts it "
        "back on the bot's task model",
    )
    s.add_argument("id")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to change only --effort or --harness")
    s.add_argument("--effort")
    s.add_argument("--harness")
    s.add_argument(
        "--clear", action="store_true", help="clear the task's own model: its runs use the bot's task model again"
    )
    s.set_defaults(fn="task model")
    s = task.add_parser(
        "snooze",
        help="set a task aside in its stage, with why: off Needs you and quiet until DATE; with no date it is On ice",
    )
    s.add_argument("id")
    s.add_argument("--until", help="ISO-8601 date (the start of that day, Pacific) or date-time with a zone")
    s.add_argument("-m", "--why", help='why, and what you wait for ("vendor replies by Nov 14")')
    s.add_argument("--version", type=int, help=version_help)
    s.set_defaults(fn="task snooze")
    s = task.add_parser("unsnooze", help="a person brings a snoozed task back now; its owner is woken once")
    s.add_argument("id")
    s.add_argument("-m", "--why")
    s.set_defaults(fn="task unsnooze")
    s = task.add_parser("queue", help="queue again: put the PRs GitHub took out of the merge queue back in it")
    s.add_argument("id")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task queue")
    s = task.add_parser("release", help="cut the release a task in Release waits for, the way the repository makes one")
    s.add_argument("id")
    s.add_argument("--tag", help="the release's tag, where the repository releases from a tag")
    s.add_argument("--version", type=int, help=version_help)
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task release")
    s = task.add_parser("unlink", help="take a link off a task: a PR closed without merging, by its URL or id")
    s.add_argument("id")
    s.add_argument("link", help="the link's URL, or its id")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="task unlink")

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
    cli_spec.add(sub, strategy_commands.STRATEGY)  # tico strategy and tico brand: clients/commands/strategy.py
    cli_spec.add(sub, strategy_commands.BRAND)
    cli_spec.add(sub, strategy_commands.CLI)  # tico growth: clients/commands/strategy.py

    cli_spec.add(sub, strategy_commands.LENS)  # tico lens: clients/commands/strategy.py

    cli_spec.add(sub, market_commands.CLI)  # tico market: clients/commands/market.py

    listen = sub.add_parser(
        "listening", help="Listening's sources: the ones due a read, and saving what a read found"
    ).add_subparsers(dest="sub")
    s = listen.add_parser("sources", help="the Search sources due a read")
    s.add_argument("--automation", help="only this listening's sources")
    s.set_defaults(fn="listening sources")
    s = listen.add_parser("save", help="what one read of one source found, from a JSON file")
    s.add_argument("--source", required=True, help="source id, from `tico listening sources`")
    s.add_argument("--file", required=True, help="JSON: {status, error, items: [...]}; - for stdin")
    s.set_defaults(fn="listening save")

    def mcp_flags(s):
        s.add_argument(
            "--mcp-url",
            dest="mcp_url",
            help="the tool is a remote MCP server at this https address (http only for localhost)",
        )
        s.add_argument("--transport", choices=["http", "sse"], help="the MCP server's transport (default http)")
        s.add_argument(
            "--header",
            dest="headers",
            action="append",
            metavar="'NAME: VALUE'",
            help='an MCP header; the value may use ${VAR_NAME} for the tool\'s --env variable, never the value itself; repeatable',
        )

    tools = sub.add_parser(
        "tool",
        help="what the team uses: each tool, what a bot uses, its model, repository and declared tools (docs/creating-bots.md)",
    ).add_subparsers(dest="sub")
    s = tools.add_parser("list", help="the team's tools; with --bot, that bot's tools with their status")
    s.add_argument("--bot", help="a bot's slug, or me: that bot's own tools instead of the team's")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="tool list")
    s = tools.add_parser("show", help="one tool: the page, its queries and the learnings")
    s.add_argument("service")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="tool show")
    s = tools.add_parser("index", help="Tools: every API tool, mailbox, number and connection you may see")
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="tool index")
    s = tools.add_parser("check", help="check an API tool's keys now")
    s.add_argument("id", help="the API tool id (`tico tool index`)")
    s.set_defaults(fn="tool check")
    s = tools.add_parser("call", help="call a proxied API tool from a turn; Tico adds the real key")
    s.add_argument("tool", help="the API tool id, e.g. close-crm (its TICO_TOOL_<ID>_URL)")
    s.add_argument("method", type=str.upper, choices=("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"))
    s.add_argument("path", help="the vendor API path, e.g. /api/v1/me/")
    s.add_argument("--data", help="a JSON body")
    s.add_argument("--query", action="append", default=[], help="a query parameter, key=value (repeatable)")
    s.set_defaults(fn="tool call")
    s = tools.add_parser("query-search", help="search a tool's query catalog")
    s.add_argument("service")
    s.add_argument("term", nargs="?", default="", help="matched against id, title, description, tags and SQL")
    s.add_argument("--id", dest="query_id", help="print one query's SQL and params")
    s.add_argument("--json", action="store_true", help="print the matching queries as JSON")
    s.set_defaults(fn="tool query-search")
    s = tools.add_parser("learn", help='add a learning: tico tool learn <service> "<text>"')
    s.add_argument("service")
    s.add_argument("text")
    s.set_defaults(fn="tool learn")
    s = tools.add_parser("report", help="report the external profile's complete current tool list")
    s.add_argument("tools", help="JSON array of tool entries")
    s.set_defaults(fn="tool report")
    s = tools.add_parser("add", help="register a tool: BotOps adds it to bot.yaml; never a credential value")
    s.add_argument("bot")
    s.add_argument("service", help="a short name such as posthog or google-calendar")
    s.add_argument("--can", required=True, help="read, draft, post, act, use, send or write; comma separated")
    s.add_argument("--identity", help="who it acts as, in words for a person")
    s.add_argument(
        "--scope",
        action="append",
        metavar="KEY=VALUE",
        help="database=warehouse, channels=#a,#b, project=123; repeatable",
    )
    s.add_argument("--env", help="the variable's NAME, such as POSTHOG_KEY; the owner installs the value")
    s.add_argument("--note")
    mcp_flags(s)
    s.add_argument("--title-prefix", dest="title_prefix", help="prefix for the generated BotOps task")
    s.add_argument("--dry-run", dest="dry_run", action="store_true", help="validate without opening a task")
    s.set_defaults(fn="tool add")
    s = tools.add_parser(
        "update", help="change a declared tool's can, scope or note in place; never remove and add again"
    )
    s.add_argument("id", help="the tool id from `tico tool list --bot`")
    s.add_argument("--bot", required=True, help="the bot's slug")
    s.add_argument(
        "--can", help="the full list from now on: read, draft, post, act, use, send or write; comma separated"
    )
    s.add_argument(
        "--scope",
        action="append",
        metavar="KEY=VALUE",
        help="set a key (database=warehouse, channels=#a,#b); KEY= takes it off; repeatable",
    )
    s.add_argument("--note", help='the new note; "" clears it')
    mcp_flags(s)
    s.add_argument("--title-prefix", dest="title_prefix", help="prefix for the generated BotOps task")
    s.set_defaults(fn="tool update")
    s = tools.add_parser("remove", help="ask BotOps to remove a tool, or withdraw a pending request")
    s.add_argument("bot")
    s.add_argument("id", help="the tool id from `tico tool list --bot`")
    s.set_defaults(fn="tool remove")

    auto = sub.add_parser(
        "automation", aliases=["automations"], help="automations: a trigger and steps"
    ).add_subparsers(dest="sub")
    s = auto.add_parser("list")
    s.add_argument("--owner", help="person:<id>, bot:<slug> or group:<id>")
    s.add_argument("--limit", type=int)
    s.add_argument("--cursor", type=int, help="`next` from the page before")
    s.set_defaults(fn="automation list")
    s = auto.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="automation get")
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
    s = auto.add_parser("delete")
    s.add_argument("id")
    s.set_defaults(fn="automation delete")
    s = auto.add_parser("run", help="run it now and print the run")
    s.add_argument("id")
    s.add_argument("--input", help="a JSON object standing in for the trigger's output")
    s.set_defaults(fn="automation run")
    s = auto.add_parser("runs")
    s.add_argument("id")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="`next` from the page before")
    s.set_defaults(fn="automation runs")
    s = auto.add_parser("held", help="end a run-guard hold: release queues its held runs, drop skips them")
    s.add_argument("id")
    s.add_argument("action", choices=["release", "drop"])
    s.set_defaults(fn="automation held")
    s = auto.add_parser("recent-runs", help="runs across every automation you may see, newest first")
    s.add_argument("--range", choices=["5m", "1h", "24h", "7d"])
    s.add_argument("--status", choices=["done", "failed", "waiting", "running", "held"])
    s.add_argument("--trigger", help="message, task_received, schedule, webhook, listening, event, due or system")
    s.add_argument("--owner", help="person:<id>, bot:<slug> or group:<id>")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="`next` from the page before")
    s.set_defaults(fn="automation recent-runs")
    s = auto.add_parser("stats", help="runs, failures and cost per automation over a range")
    s.add_argument("--range", choices=["5m", "1h", "24h", "7d"])
    s.set_defaults(fn="automation stats")
    s = auto.add_parser("versions")
    s.add_argument("id")
    s.add_argument("--pending", action="store_true", help="proposals only")
    s.set_defaults(fn="automation versions")
    s = auto.add_parser("restore")
    s.add_argument("id")
    s.add_argument("n", type=int)
    s.set_defaults(fn="automation restore")
    s = auto.add_parser("discard", help="drop a pending version (a proposal)")
    s.add_argument("id")
    s.add_argument("n", type=int)
    s.set_defaults(fn="automation discard")
    s = auto.add_parser("question-sets", help="the question sets a Decide step may name")
    s.set_defaults(fn="automation question-sets")
    s = auto.add_parser("guide", help="how BotOps builds automations, with the flow format")
    s.set_defaults(fn="automation guide")
    s = auto.add_parser("templates", help="the inbox templates: founder, support, sales, blank")
    s.set_defaults(fn="automation templates")
    s = auto.add_parser("template", help="one inbox template filled in for an owner and an inbox (saves nothing)")
    s.add_argument("id", help="founder, support, sales or blank")
    s.add_argument("--owner", required=True, help="person:<id>, bot:<slug> or group:<id>")
    s.add_argument("--inbox", required=True, help="an email address, slack:dm or slack:#<channel>")
    s.set_defaults(fn="automation template")
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

    s = sub.add_parser("event-types", help="every event Tico names, with a one-line description and an example payload")
    s.add_argument(
        "--group",
        help="only this group: task, approval, needs_you, bot, release, meeting, strategy, market, "
        "inbound, health, webhook, tico, custom",
    )
    s.set_defaults(fn="event-types")
    cli_spec.add(sub, webhooks.CLI)  # tico trigger: clients/commands/webhooks.py
    cli_spec.add(sub, webhooks.RELAY)  # tico relay: clients/commands/webhooks.py
    call = sub.add_parser(
        "call", help="Tico Meet: audio and screen share in a thread, with a transcript posted to it"
    ).add_subparsers(dest="sub")
    s = call.add_parser("list", help="the calls in a thread, newest first")
    s.add_argument("thread_id")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--before", help="the next_before of the page you read, to read the next page")
    s.set_defaults(fn="call list")
    s = call.add_parser("start", help="start a call in a thread you can write to; you become its connector")
    s.add_argument("thread_id")
    s.add_argument("--title", default="")
    s.add_argument(
        "--speak",
        dest="speak_replies",
        action="store_true",
        help="bots speak their replies on this call (refused until the Gateway has text-to-speech)",
    )
    s.set_defaults(fn="call start")
    for action, help_text in (
        ("show", "a call: its state, people, bots and how it ended"),
        ("join", "join a call: the URL and a token good for 5 minutes"),
        ("token", "a fresh token while you are joined"),
        ("leave", "leave a call you joined"),
        ("end", "end a call you started; its transcript is posted to the thread"),
        ("retry", "ask again for action items the Gateway could not make"),
    ):
        s = call.add_parser(action, help=help_text)
        s.add_argument("call_id")
        s.set_defaults(fn="call " + action)
    bot = call.add_parser("bot", help="bots that listen to a call and reply in its thread").add_subparsers(
        dest="subsub"
    )
    s = bot.add_parser("add", help="let bots listen to a call you joined (up to 10)")
    s.add_argument("call_id")
    s.add_argument("bots", nargs="+")
    s.set_defaults(fn="call bot add")
    s = bot.add_parser("remove", help="take a bot out of a call")
    s.add_argument("call_id")
    s.add_argument("bot")
    s.set_defaults(fn="call bot remove")
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

    catalogue = sub.add_parser(
        "settings", help="find, read and change a control in Settings by its words (docs/permissions.md)"
    ).add_subparsers(dest="sub")
    s = catalogue.add_parser("find", help="settings whose name, place or key hold these words")
    s.add_argument("words")
    s.set_defaults(fn="settings find")
    for leaf, text in (
        ("show", "one setting: its value, who may change it and its card"),
        ("set", "change one setting as yourself, or BotOps or the Assistant as the person who asked"),
    ):
        s = catalogue.add_parser(leaf, help=text)
        s.add_argument("key", help="the key `tico settings find` gives, e.g. team.rules.admin_sql")
        if leaf == "set":
            s.add_argument("value", help="on/off, a number, text, a comma-separated list or JSON")
            s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
        target = s.add_mutually_exclusive_group()
        target.add_argument("--bot", help="the bot, for a setting set per bot")
        target.add_argument("--computer", help="the computer, for a setting set per computer")
        target.add_argument("--id", help="the one thing it sets: a trigger, webhook, rule, routine, repository...")
        target.add_argument("--person", help="the person's id, for a setting set per person")
        s.set_defaults(fn="settings " + leaf)
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
    bt = sub.add_parser(
        "needs-you", help="a person's walk through what needs them: respond, commit (backend/batch.py)"
    ).add_subparsers(dest="sub")
    s = bt.add_parser("start", help="start the batch of the bot that most needs you, or resume the one in progress")
    s.add_argument("--bot", help="only this bot's items (a slug)")
    s.add_argument("--all", action="store_true", help="every item from every bot in one batch")
    s.add_argument("--fresh", action="store_true", help="drop the batch in progress and build a new list")
    s.set_defaults(fn="needs-you start")
    s = bt.add_parser("next", help="the next item; at the end, the summary to read back")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you next")
    s = bt.add_parser("respond", help="record a response to the current item; nothing applies until commit")
    s.add_argument("batch")
    s.add_argument("kind", choices=["decide", "needs_info", "instruct", "rule", "skip", "later"])
    s.add_argument("--text", default="")
    s.add_argument("--decision", choices=["ok", "send_back", "done", "close", "answer"])
    s.add_argument("--item", type=int, help="another item's number")
    s.add_argument("--until", help="with later: an ISO date-time")
    s.set_defaults(fn="needs-you respond")
    s = bt.add_parser("commit", help="apply every recorded response")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you commit")
    s = bt.add_parser("abandon", help="drop the batch without applying anything")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you abandon")
    s = bt.add_parser("show", help="what needs you: the count per kind and the items, oldest first")
    s.add_argument("--kind", choices=["task", "ask", "paused", "mention"], help="only this kind")
    s.add_argument("--limit", type=int, help="the items listed (default 20); the counts are of every item")
    s.set_defaults(fn="needs-you show")

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

    team = sub.add_parser("team", help="team chart and icon").add_subparsers(dest="sub")
    s = team.add_parser("icon", help="set the public team logo (owner; PNG/JPEG/WebP up to 1 MB)")
    s.add_argument("file")
    s.set_defaults(fn="team icon")
    s = team.add_parser("show", help="the team chart: humans, Slack, what they own, the bots with reports_to and group")
    s.add_argument("--person", help="that human and everyone under them")
    s.add_argument("--team", help="a group id, like engineering or sales: that group and the groups in it")
    s.set_defaults(fn="team show")
    s = team.add_parser(
        "budget", help="read or set the team's default runs per hour and per day for bots (owner and admins)"
    )
    for window in ("hour", "day"):
        s.add_argument(
            "--" + window,
            type=int,
            help=f"runs allowed per {window} for bots with no budget of their own; 0 for no limit",
        )
    s.set_defaults(fn="team budget")
    cli_spec.add(sub, admin_commands.SEAT)  # tico seat: clients/commands/admin.py
    cli_spec.add(sub, org_commands.ORG)  # tico org: clients/commands/org.py
    cli_spec.add(sub, computers.HEALTH)  # tico health: clients/commands/computers.py
    changelog = sub.add_parser("changelog", help="shipped product changes and your own read state").add_subparsers(
        dest="sub"
    )
    s = changelog.add_parser("list", help="what changed; --unread catches up since your last look")
    s.add_argument("--kind", choices=["product", "activity", "all"], default="product")
    s.add_argument("--unread", action="store_true")
    s.add_argument("--q", help="search change titles and bullets")
    s.add_argument("--offset", type=int)
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="changelog list")
    s = changelog.add_parser("mark-read", help="acknowledge only the changes you have reviewed")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn="changelog mark-read")

    upd = sub.add_parser("update", help="create, read and reply to the bots' daily and weekly updates").add_subparsers(
        dest="sub"
    )
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
    s = upd.add_parser("redo", help="ask the bots again for a past day's update in the current shape (owner)")
    s.add_argument("--day", required=True)
    s.add_argument("--kind", choices=("daily", "weekly"))
    s.add_argument("--bot", dest="bots", action="append", help="only this bot (repeatable)")
    s.add_argument("--with-managers", dest="managers", action="store_true", help="also the bots above them")
    s.set_defaults(fn="update redo")
    s = upd.add_parser("list", help="updates from bots and people, newest first")
    s.add_argument("--kind", choices=("daily", "weekly"))
    s.add_argument("--bot", help="one author: a bot slug or human:<id>")
    s.add_argument("--scope", choices=("all", "direct"), help="direct: only your direct reports")
    s.add_argument("--unread", action="store_true")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="older than this: a page's next_before")
    s.set_defaults(fn="update list")
    s = upd.add_parser("show")
    s.add_argument("update")
    s.set_defaults(fn="update show")
    s = upd.add_parser("mark-read", help="mark updates read (--unread to mark them unread)")
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--unread", dest="read", action="store_false")
    s.set_defaults(fn="update mark-read")
    s = upd.add_parser("reply")
    s.add_argument("update")
    s.add_argument("text")
    s.set_defaults(fn="update reply")
    s = upd.add_parser("settings", help="turn a bot's daily or weekly update on or off")
    s.add_argument("bot")
    s.add_argument("--daily", choices=("on", "off"))
    s.add_argument("--weekly", choices=("on", "off"))
    s.set_defaults(fn="update settings")
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

    calendar = sub.add_parser("calendar", help="events, free times and bookings on calendars").add_subparsers(
        dest="sub"
    )
    s = calendar.add_parser("list", help="events on calendars (--who), or a company calendar's appointments")
    s.add_argument("--calendar", default="", help="roster email; default the company owner's")
    s.add_argument("--who", help="comma-separated people or bots, refs or emails; default you")
    s.add_argument("--from", dest="from_", metavar="ISO", help="window start: ISO time, or YYYY-MM-DD")
    s.add_argument("--to", metavar="ISO", help="window end, exclusive; at most 62 days after --from")
    s.add_argument("--kinds", help="comma-separated: event, run, due, away (default all)")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="calendar list")
    s = calendar.add_parser("free", help="times when people are free, within their working hours")
    s.add_argument("--who", help="comma-separated people or bots; default you")
    s.add_argument("--minutes", type=int, default=30, help="length of the free slot (default 30)")
    s.add_argument("--within", metavar="DURATION", help="how far ahead to look, such as 5d")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="calendar free")
    s = calendar.add_parser("add", help="book an event; a bot or the Assistant may book anyone a rule allows")
    s.add_argument("--title", required=True)
    s.add_argument("--start", required=True, help="ISO-8601 time with zone, or YYYY-MM-DD with --all-day")
    s.add_argument("--end", required=True, help="ISO-8601 time with zone, or YYYY-MM-DD (exclusive) with --all-day")
    s.add_argument("--who", help="comma-separated attendees, refs or emails")
    s.add_argument("--repeat", help='words or an RRULE, such as "weekly on mon,wed" or "daily until 2026-12-31"')
    s.add_argument("--calendar", help="whose calendar: a ref such as human:ana, or an email; default you")
    s.add_argument("--all-day", action="store_true", help="start and end are dates")
    s.add_argument("--notes", help="description for attendees")
    s.add_argument("--no-meet", action="store_true", help="no Google Meet link")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="calendar add")
    s = calendar.add_parser("move", help="move one occurrence, or a repeated event from it on with --scope")
    s.add_argument("occurrence", help="occurrence id from calendar list or search")
    s.add_argument("--start", required=True, help="ISO-8601 time with zone, or YYYY-MM-DD for all day")
    s.add_argument("--end", help="same form as --start")
    s.add_argument("--scope", choices=CALENDAR_SCOPES, help="for a repeated event: this, following or all")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="calendar move")
    s = calendar.add_parser("cancel", help="cancel one occurrence, or a repeated event from it on with --scope")
    s.add_argument("occurrence", help="occurrence id from calendar list or search")
    s.add_argument("--scope", choices=CALENDAR_SCOPES, help="for a repeated event: this, following or all")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="calendar cancel")
    s = calendar.add_parser("search", help="find events by words, with tokens such as with:ben or before:2026-12-01")
    s.add_argument("q", metavar="words")
    s.add_argument("--who", help="comma-separated calendars to search; default you")
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="calendar search")
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
    s = calendar.add_parser("status", help="whether a queued appointment was created")
    s.add_argument("id")
    s.set_defaults(fn="calendar status")

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
        help="per question set: the last days' calls, cost, Jev or fallback and answer "
        "spread, and the month's spend against the cap",
    )
    s.add_argument("--days", type=int, help="how many days back from now (1-31, 7 by default)")
    s.set_defaults(fn="decision readout")

    # A new bot: BotOps builds the chosen template in the workspace (`tico bot create`) or only registers it (--record-only).
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
        "show",
        help="one bot: its profile, and for one you may read also its status, machine, queue, goals and routines",
    )
    s.add_argument("slug")
    s.set_defaults(fn="bot show")
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
        help="on (owner only): keep each run's raw provider stream a week for tico run export; off (default): not",
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
    s = bot.add_parser(
        "app-view", help="read or set whether a bot may view the app read only, for its screenshots: on or off"
    )
    s.add_argument("bot")
    s.add_argument("on", nargs="?", type=lambda v: {"on": True, "off": False}[v], default=argparse.SUPPRESS)
    s.set_defaults(fn="bot app-view")
    s = bot.add_parser(
        "app-shot", help="a screenshot of the app as you see it, read only: --width 390 for a phone, 1280 for a desktop"
    )
    s.add_argument("path", nargs="?", default="/", help="the app address, such as /#/tasks")
    s.add_argument("--out", required=True, help="where to write the PNG")
    s.add_argument("--width", type=int, default=390)
    s.add_argument("--height", type=int, default=argparse.SUPPRESS)
    s.add_argument("--wait-ms", dest="wait_ms", type=int, default=argparse.SUPPRESS)
    s.add_argument("--full", action="store_true", default=argparse.SUPPRESS)
    s.set_defaults(fn="bot app-shot")
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
    s = bot.add_parser("model", help="list the models, or change this bot's model or where it compacts (BotOps)")
    s.add_argument("bot")
    s.add_argument("model", nargs="?", help="a model id or name; leave out to list")
    s.add_argument("--effort")
    s.add_argument(
        "--compact-at",
        type=int,
        help="where its conversation compacts, in tokens: 100000, 150000, 200000, 250000, 300000, 400000, 500000, "
        "600000, 700000, 800000, 900000 or 1000000",
    )
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
        "--paths", nargs="+", metavar="PATH", help="only these files or folders, like AGENTS.md or skills/triage"
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
    cli_spec.add(sub, org_commands.HUMAN)  # tico human: clients/commands/org.py
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
    cred = sub.add_parser("credential", help="ask for a credential in the chat, store one, list them").add_subparsers(
        dest="sub"
    )
    s = cred.add_parser("request", help="open a card in the chat for the person to type the credential into")
    s.add_argument("env", help="the variable's name, like JIRA_BASIC_AUTH")
    s.add_argument("--for-bot", dest="for_bot")
    s.add_argument("--label", help="what it is: 'your Jira login'")
    s.add_argument("--format", help="its shape: you@company.com:API token")
    s.add_argument("--help-url", dest="help_url", help="an https page where they make one")
    s.add_argument("--kind", choices=["api_key", "token", "password", "connection"])
    s.set_defaults(fn="credential request")
    s = cred.add_parser("set", help="store a credential a person gave you, for one bot; the value is read from stdin")
    s.add_argument("env")
    s.add_argument("--for-bot", dest="for_bot", required=True)
    s.add_argument("--name")
    s.add_argument("--kind", choices=["api_key", "token", "password", "connection"])
    s.add_argument("--username")
    s.add_argument("--no-redact", dest="no_redact", action="store_true", help="leave the pasted words in the chat")
    s.set_defaults(fn="credential set")
    cred.add_parser("list", help="names, variables and which bots have each; never a value").set_defaults(
        fn="credential list"
    )
    s = cred.add_parser("grant", help="give a bot a stored credential")
    s.add_argument("credential", help="its name in Credentials (or its variable's name)")
    s.add_argument("--to", dest="to_bot", required=True, metavar="BOT")
    s.set_defaults(fn="credential grant")
    s = cred.add_parser("revoke", help="take a stored credential away from a bot")
    s.add_argument("credential")
    s.add_argument("--from", dest="from_bot", required=True, metavar="BOT")
    s.set_defaults(fn="credential revoke")
    s = cred.add_parser("delete", help="erase a Credential and all its grants (credential administrators)")
    s.add_argument("credential")
    s.set_defaults(fn="credential delete")
    s = cred.add_parser(
        "import", help="move one variable from a bot's own secrets file into Credentials, granted to that bot"
    )
    s.add_argument("env", metavar="VAR", help="the variable's name, like JIRA_BASIC_AUTH")
    s.add_argument("--from-bot", dest="from_bot", required=True, metavar="BOT")
    s.add_argument("--name", help="what to call it in Credentials; the variable's name by default")
    s.add_argument("--kind", choices=["api_key", "token", "password", "connection"])
    s.add_argument("--wait", type=int, help="seconds to wait for the computer to answer (30; at most 60)")
    s.set_defaults(fn="credential import")
    slack = sub.add_parser("slack", help="the Slack channels bots may read and post in").add_subparsers(dest="sub")
    chan = slack.add_parser("channel", help="list, add or remove a channel").add_subparsers(dest="subsub")
    chan.add_parser("list", help="the listed channels, their readers, and whether bots may post").set_defaults(
        fn="slack channel list"
    )
    s = chan.add_parser("add", help="list a channel, or add readers to one already listed (an owner or an admin)")
    s.add_argument("channel", help="#name or id (C0123456789)")
    s.add_argument(
        "--reader", dest="readers", action="append", metavar="BOT", help="a bot that reads it; repeat for several"
    )
    s.add_argument(
        "--post",
        dest="post",
        action="store_true",
        default=None,
        help="bots may post there (the default for a new channel)",
    )
    s.add_argument("--no-post", dest="post", action="store_false", help="bots may only read it")
    s.add_argument("--note", help="what the channel is for")
    s.add_argument(
        "--digest-hours", dest="digest_hours", type=float, help="readers get it at most once in this many hours"
    )
    s.add_argument("--name", help="its name, when you give the id")
    s.set_defaults(fn="slack channel add")
    s = chan.add_parser("remove", help="take a channel off the list, or one reader off it")
    s.add_argument("channel")
    s.add_argument("--reader", help="only this bot stops reading it")
    s.set_defaults(fn="slack channel remove")
    chan.add_parser("import", help="store the channels of the old registry/slack-channels.yaml").set_defaults(
        fn="slack channel import"
    )
    cli_spec.add(sub, computers.SUPPORT)  # tico support: clients/commands/computers.py
    cli_spec.add(sub, finance.CLI)  # tico finance: clients/commands/finance.py
    # Contacts (backend/entities/routes.py): every reader sees what the read rule lets it see; writes name their actor.
    contacts = sub.add_parser(
        "contacts",
        help="contacts: people, organizations and places, their links, values and notes, as you see them",
    ).add_subparsers(dest="sub")
    s = contacts.add_parser("self", help="who we are: our company, where we operate, the obligations that bind us")
    s.add_argument("--text", dest="as_text", action="store_true", help="a short summary, not JSON")
    s.set_defaults(fn="contacts self")
    contacts.add_parser("types", help="the entity types and their descriptions, and who added each").set_defaults(
        fn="contacts types"
    )
    s = contacts.add_parser(
        "describe", help="one type: its attributes (owners, options) and the link types that touch it"
    )
    s.add_argument("type")
    s.set_defaults(fn="contacts describe")
    s = contacts.add_parser("find", help="entities of one type you can see, by name, status or a link to us")
    s.add_argument("type")
    s.add_argument("--name", help="a name prefix")
    s.add_argument("--status")
    s.add_argument("--link", help="a link to us, like customer_of")
    s.add_argument("--after", help="the next the last page gave")
    s.add_argument("--limit", type=int, help="up to 50")
    s.add_argument("--text", dest="as_text", action="store_true", help="one line per entity, not JSON")
    s.set_defaults(fn="contacts find")
    s = contacts.add_parser("get", help="one entity as you see it: values with source, links, records and your notes")
    s.add_argument("ref", metavar="id|email|domain|name")
    s.add_argument("--text", dest="as_text", action="store_true", help="a short card, not JSON")
    s.set_defaults(fn="contacts get")
    s = contacts.add_parser("rules", help="the rules that apply to an entity (none until rules arrive)")
    s.add_argument("ref", metavar="id|email|domain|name")
    s.add_argument("--as", dest="as_bot", metavar="BOT", help="the bot the rules are for")
    s.add_argument("--action", help="what you would do, like email")
    s.set_defaults(fn="contacts rules")
    s = contacts.add_parser("upsert", help="create or update one entity: --match names it, --set sets its values")
    s.add_argument("type")
    s.add_argument("--match", required=True, type=_pair, metavar="KEY=VALUE", help="email, domain, name or id")
    s.add_argument(
        "--set", dest="set_values", action="append", type=_pair, metavar="KEY=VALUE", help="one value, repeatable"
    )
    s.add_argument("--name")
    s.add_argument("--evidence", dest="evidence_id", metavar="EVIDENCE_ID", help="the market evidence it rests on")
    s.add_argument("--confidence", type=float, help="0 to 1; the default is 0.6")
    s.set_defaults(fn="contacts upsert")
    s = contacts.add_parser("link", help="a typed, dated link between two entities: a works_at b")
    s.add_argument("from_ref", metavar="a")
    s.add_argument("link_key", metavar="link")
    s.add_argument("to_ref", metavar="b")
    s.add_argument("--from", dest="valid_from_on", metavar="DATE", help="YYYY-MM-DD the link starts")
    s.add_argument("--until", dest="valid_until_on", metavar="DATE", help="YYYY-MM-DD the link ends")
    s.add_argument("--evidence", dest="evidence_id", metavar="EVIDENCE_ID", help="the market evidence it rests on")
    s.add_argument("--confidence", type=float, help="0 to 1; the default is 0.6")
    s.set_defaults(fn="contacts link")
    s = contacts.add_parser("unlink", help="end a link: it stops being current today; its history stays")
    s.add_argument("link_id")
    s.set_defaults(fn="contacts unlink")
    s = contacts.add_parser("merge", help="merge a duplicate into the entity it copies; unmerge reverses it")
    s.add_argument("keep")
    s.add_argument("drop")
    s.set_defaults(fn="contacts merge")
    s = contacts.add_parser("unmerge", help="reverse the last merge of a dropped entity, named by its own id")
    s.add_argument("drop")
    s.set_defaults(fn="contacts unmerge")
    s = contacts.add_parser("history", help="past values of an entity's fields, newest first")
    s.add_argument("ref", metavar="id|email|domain|name")
    s.add_argument("--attribute", help="one field's history")
    s.add_argument("--after", help="the next the last page gave")
    s.add_argument("--limit", type=int, help="up to 50")
    s.set_defaults(fn="contacts history")
    s = contacts.add_parser("note", help="a note on an entity; --audience me keeps it to you")
    s.add_argument("ref", metavar="id|email|domain|name")
    s.add_argument("body")
    s.add_argument("--audience", choices=["me", "company"])
    s.add_argument("--evidence", dest="evidence_id", metavar="EVIDENCE_ID", help="the market evidence it cites")
    s.set_defaults(fn="contacts note")
    attribute = contacts.add_parser("attribute", help="add an attribute to a type").add_subparsers(dest="subsub")
    s = attribute.add_parser("add", help="add an attribute: name, value type, description, and options for a select")
    s.add_argument("type")
    s.add_argument("key")
    s.add_argument("--name", required=True)
    s.add_argument("--value-type", dest="value_type", required=True, help="text, number, date, select, status ...")
    s.add_argument("--description", required=True, help="what it holds; required")
    s.add_argument(
        "--option", dest="options", action="append", type=_pair, metavar="KEY=LABEL", help="a select's option"
    )
    s.add_argument("--multiple", action="store_true")
    s.add_argument("--unique", dest="unique_value", action="store_true")
    s.add_argument("--required", action="store_true")
    s.add_argument("--sensitive", action="store_true", help="only Finance's owner and the system read it")
    s.add_argument("--owner", help="tico (the default) or connection:<id>")
    s.set_defaults(fn="contacts attribute add")
    kind = contacts.add_parser("type", help="add an entity type").add_subparsers(dest="subsub")
    s = kind.add_parser("add", help="add an entity type, with its description")
    s.add_argument("key")
    s.add_argument("--name", required=True)
    s.add_argument("--description", required=True)
    s.add_argument("--plural", dest="plural_name")
    s.add_argument("--icon")
    s.set_defaults(fn="contacts type add")
    link_type = contacts.add_parser("link-type", help="add a link type").add_subparsers(dest="subsub")
    s = link_type.add_parser("add", help="add a link type: its name both ways, its description, the ends it may join")
    s.add_argument("key")
    s.add_argument("--name", required=True)
    s.add_argument("--inverse", dest="inverse_name", required=True, help="the name from the far end")
    s.add_argument("--description", required=True)
    s.add_argument("--from", dest="from_type", help="the type the link starts at")
    s.add_argument("--to", dest="to_type", help="the type the link ends at")
    s.add_argument(
        "--inherits-rules", dest="inherits_rules", action="store_true", help="a rule toward one end reaches the other"
    )
    s.add_argument("--symmetric", action="store_true", help="stored once, both ways")
    s.set_defaults(fn="contacts link-type add")
    manager = sub.add_parser(
        "general-manager", help="the General Manager's hourly department check (docs/general-manager.md)"
    ).add_subparsers(dest="sub")
    manager.add_parser(
        "status", help="bot, report switch and schedule, last run, each department's last check"
    ).set_defaults(fn="general-manager status")
    manager.add_parser("on", help="switch the hourly report on").set_defaults(fn="general-manager on")
    manager.add_parser("off", help="switch the hourly report off").set_defaults(fn="general-manager off")
    manager.add_parser("run", help="run the department check now").set_defaults(fn="general-manager run")
    s = manager.add_parser("schedule", help="pick when the report runs")
    s.add_argument("preset", choices=["business", "hourly", "4h", "daily"])
    s.add_argument("--at", help="HH:MM for the daily preset")
    s.set_defaults(fn="general-manager schedule")
    s = manager.add_parser("bot", help="turn the General Manager bot itself on or off")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(fn="general-manager bot")
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
    org_commands.add_commands(sub)  # tico bot list|go-live-all|quarantine-clear
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
    'ask a person on your task with: tico task ask <id> --to <person> "<one line>" --version N; '
    'ask a bot with: tico message send <bot> "<question>" --needs-action'
)
ANSWER_INSTEAD = (
    "a person answers in Needs you; a bot named on an ask answers with: "
    "tico task answer <id> <requirement> ok|no|<answer> --version N"
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
