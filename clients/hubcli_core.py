"""What more than one hub CLI module needs: the error type, the argument helpers and the parser pieces (clients/hubcli_*.py)."""

import argparse
import json
import re
import sys
from pathlib import Path


# Mirrors of the server's vocabulary (backend/hubdb.py), so a typo is refused by the parser
# before a request is made. The server is the authority when they disagree.
TASK_STATUSES = ("open", "doing", "waiting", "review", "ready", "done", "closed", "declined")


TASK_UPDATE_STATUSES = tuple(status for status in TASK_STATUSES if status != "closed")


# task_cap and spend_limit: proposals (backend/proposals.py).
APPROVAL_KINDS = ("send", "spend", "publish", "merge", "support", "task_cap", "spend_limit")


GOAL_STATUSES = ("red", "yellow", "green", "done", "dropped")  # set by hand; gray is only ever automatic


# The Assistant, acting on a task the person asked it to in their own Assistant chat (backend/assistant.py `instruction`).
ASKED = (
    "Assistant: the person asked for exactly this in their own Assistant chat: `turn` (their message that started "
    "this run) or that message's id. Leave it out when they did not clearly ask, and propose instead"
)


class CliError(Exception):
    """Anything the arguments or the environment got wrong: exit 1."""


def task_update_status(value):
    if value == "closed":
        raise argparse.ArgumentTypeError("use `hub task close <id>` to close a task")
    return value


GO_LIVE_HELP = """When the computer does not have the bot's repository yet, go-live keeps the placement and answers
waiting_for_repository: the computer clones it, and Tico finishes going live when it reports the repository."""


def body_of(args):
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text()
    return args.body or ""


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


def quiet_hours(word):
    """0 to 24, whole hours."""
    try:
        hours = int(word)
    except ValueError:
        hours = -1
    if not 0 <= hours <= 24:
        raise argparse.ArgumentTypeError("use a whole number of hours from 0 to 24")
    return hours


def on_off(word):
    """`on` or `off` as the boolean the tool takes."""
    if word not in ("on", "off"):
        raise argparse.ArgumentTypeError("use on or off")
    return word == "on"


def _finance_payload(text):
    """`hub finance act --payload`: a JSON object, or `-` to read it from stdin."""
    try:
        value = json.loads(sys.stdin.read() if text == "-" else text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--payload is not JSON ({exc})") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("--payload must be a JSON object")
    return value


def thread_page_flags(s):
    """The paging flags of a thread list: a page's next_cursor reads on, and --limit sets the size."""
    s.add_argument("--cursor", help="the page after this cursor: a page's next_cursor")
    s.add_argument("--limit", type=int, help="items in the page (50 by default, 100 at most)")
