"""The `tico` CLI's plumbing: profiles (`tico auth`), personal tokens (`tico token`), `tico events tail`, the global
`--profile` and `--fields`, and the exit codes (docs/cli.md).

Kept out of clients/hubcli.py, which every area edits: hubcli registers these parsers with one call and hands the
commands here.
"""

import json
import os
import sys

# Exit codes (docs/cli.md, Exit codes). 0-3 are the ones bots have always read; the rest tell apart what used to be
# 1 or 2. 1 is the only "may retry"; every other non-zero code means the same call will fail the same way.
EXIT_OK = 0
EXIT_FAILED = 1  # could not reach Tico, a 5xx or 429 after retries, or an unexpected error: may retry
EXIT_REFUSED = 2  # the server said no (403, 409, 422, a task refusal): do not retry as is
EXIT_WAIT = 3  # a worktree wait: Tico wakes the task, end the turn (remotecli.WORKTREE_WAIT_EXIT)
EXIT_SIGN_IN = 4  # no credential, or it is invalid, expired or revoked (401, a sign-in redirect), or a bad profile
EXIT_NOT_FOUND = 5  # 404: no such thing, or not one the credential may see
EXIT_TIMEOUT = 124  # a waiter's --timeout passed before the state was reached (as timeout(1))
EXIT_USAGE = 64  # the command line is wrong: unknown command or flag, missing argument (EX_USAGE)


def exit_code(exc):
    """The exit code for an APIError the CLI prints."""
    if exc.retryable:
        return EXIT_FAILED
    if exc.status == 401 or 300 <= exc.status < 400:  # a redirect is a sign-in page (clients/tico.py NoRedirect)
        return EXIT_SIGN_IN
    if exc.status == 404:
        return EXIT_NOT_FOUND
    return EXIT_REFUSED


def split_globals(argv):
    """(argv, profile, fields): the global `--profile NAME` and `--fields a,b` taken off the line, wherever they are."""
    from clients import profiles

    argv, profile = profiles.split_profile(argv)
    argv, fields = profiles.split_fields(argv)
    return argv, profile, fields


def add_parsers(sub):
    """`tico auth ...`, `tico token ...` and `tico events tail` (clients/hubcli.py parser())."""
    auth = sub.add_parser(
        "auth",
        help="profiles: which company and token this shell talks to (--profile NAME on any command)",
        description="Profiles keep a company's URL and token, so one shell talks to several companies: "
        "tico --profile acme task list. Any person may keep profiles; a bot's turn uses its own token.",
    ).add_subparsers(dest="sub")
    s = auth.add_parser(
        "add",
        help="save a profile; the token is checked first and never printed",
        description="Save a profile. Example: tico auth add acme --url https://acme.tico.team --token-file "
        "~/acme/owner_token",
    )
    s.set_defaults(fn="auth add")
    s.add_argument("name", help="the profile's name, e.g. acme")
    s.add_argument("--url", required=True, help="the company's Tico address, e.g. https://acme.tico.team")
    s.add_argument("--token-file", help="a file holding the token; it is read at each call, not copied")
    s.add_argument("--with-token", action="store_true", help="read the token from stdin and store it in the profile")
    s.add_argument("--default", action="store_true", help="use this profile when no other is named")
    s.add_argument("--no-check", action="store_true", help="save without asking Tico who the token is")
    auth.add_parser("list", help="every profile: name, URL, where its token is; never the token").set_defaults(
        fn="auth list"
    )
    s = auth.add_parser("use", help="make a profile the default")
    s.set_defaults(fn="auth use")
    s.add_argument("name")
    s = auth.add_parser("remove", help="forget a profile (the token itself keeps working: tico token revoke)")
    s.set_defaults(fn="auth remove")
    s.add_argument("name")
    auth.add_parser("status", help="the same as tico whoami: profile, company, who you are").set_defaults(fn="whoami")

    token = sub.add_parser(
        "token",
        help="your personal API tokens (list, create, revoke)",
        description="Your personal API tokens. A token lists and revokes its person's own tokens; making one "
        "takes a signed-in browser (Settings > Computers > API tokens), so a token cannot extend its own life.",
    ).add_subparsers(dest="sub")
    s = token.add_parser("list", help="your tokens; never a secret. --all: everyone's (owner, admins; browser)")
    s.set_defaults(fn="token list")
    s.add_argument("--all", action="store_true", help="everyone's tokens, with whose each is")
    s = token.add_parser(
        "create",
        help="a new token for yourself, printed once on stdout (signed-in browser sessions only)",
        description="Example: tico token create --label 'release script' --days 30",
    )
    s.set_defaults(fn="token create")
    s.add_argument("--label", required=True, help="what it is for, as Settings lists it")
    s.add_argument("--days", type=int, dest="expires_in_days", help="how long it lasts: 1-365, 90 by default")
    s = token.add_parser("revoke", help="stop one of your tokens at once: tico token revoke <id>")
    s.set_defaults(fn="token revoke")
    s.add_argument("id", help="the token's id, from tico token list")

    events = sub.add_parser("events", help="the company's live event stream").add_subparsers(dest="sub")
    s = events.add_parser(
        "tail",
        help="follow live events, one JSON line each; reconnects on its own (stripe listen)",
        description="Follow the live event stream (what the web app reads), one JSON line per event, until "
        "Ctrl-C, --count or --timeout. Anyone signed in; you see what you may read. Example: "
        "tico events tail --task 60e069fc --fields seq,kind,task.stage_id,task.status",
    )
    s.set_defaults(fn="events tail")
    s.add_argument("--task", action="append", help="only this task (id, short id or number); repeat for more")
    s.add_argument("--pipeline", action="append", help="only tasks in this pipeline (id or name); repeat for more")
    s.add_argument(
        "--topics",
        help="comma list of tasks, messages, runs, bots, needs, threads, letters (tasks when --task or "
        "--pipeline is given, else every topic)",
    )
    s.add_argument("--after", type=int, help="start after this change number (a line's seq) instead of now")
    s.add_argument("--count", type=int, help="stop after this many lines")
    s.add_argument("--timeout", type=float, help="stop after this many seconds (exit 0)")


# ----------------------------------------------------------------------------- commands that need no company
def local_command(args):
    """`tico auth add|list|use|remove`: answered here. Returns the exit code, or None for any other command."""
    from clients import profiles
    from clients.tico import APIError

    if args.fn not in ("auth add", "auth list", "auth use", "auth remove"):
        return None
    try:
        if args.fn == "auth add":
            result = profiles.add(
                args.name,
                args.url,
                token_file=args.token_file,
                with_token=args.with_token,
                make_default=args.default,
                check=None if args.no_check else check_identity,
            )
        elif args.fn == "auth list":
            result = profiles.listing()
        elif args.fn == "auth use":
            result = profiles.use(args.name)
        else:
            result = profiles.remove(args.name)
    except profiles.ProfileError as exc:
        print(json.dumps({"error": "profile", "detail": str(exc)}, indent=2))
        return EXIT_SIGN_IN
    except APIError as exc:  # the check: a wrong address or token is not saved
        print(json.dumps({"error": exc.code, "detail": exc.detail, "retryable": exc.retryable}, indent=2))
        return exit_code(exc)
    except ValueError as exc:  # the URL rules of clients/tico.py Client
        print(json.dumps({"error": "usage", "detail": str(exc)}, indent=2))
        return EXIT_USAGE
    print(json.dumps(result, indent=2))
    return EXIT_OK


def check_identity(url, token):
    """Who a new profile's token is, before it is saved; an APIError (wrong URL or token) stops the save."""
    from clients.tico import Client

    return identity(Client(url, token, retries=1))


def identity(client, me=None):
    """`tico whoami`: the API's /me, plus the profile in use, the company's name and its address."""
    from clients import profiles
    from clients.tico import APIError

    me = me if me is not None else client.get("me")
    if not isinstance(me, dict):
        return me
    extra = {"url": client.url}
    try:
        name = (client.get("config") or {}).get("company_name")
        if name:
            extra["company"] = name
    except APIError:
        pass
    if profiles.ACTIVE.get("source"):
        extra["profile"] = profiles.ACTIVE.get("profile")
        extra["credential_from"] = profiles.ACTIVE["source"]
    return {**me, **extra}


def events_tail(client, args, fields=None):
    """`tico events tail`: runs until stopped; returns the exit code. `fields`: the global --fields, per line."""
    from clients import events_tail as tail
    from clients.tico import APIError

    def emit(line):
        if fields:
            from clients import profiles

            line = json.dumps(profiles.pick(json.loads(line), fields), separators=(",", ":"), default=str)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    topics = [t.strip() for t in (args.topics or "").split(",") if t.strip()] or None
    try:
        tail.tail(
            client,
            topics=topics,
            tasks=args.task,
            pipelines=args.pipeline,
            after=args.after,
            count=args.count,
            timeout=args.timeout,
            emit=emit,
        )
    except KeyboardInterrupt:
        return EXIT_OK
    except tail.SignInExpired as exc:
        print(json.dumps({"error": "expired", "detail": str(exc), "retryable": False}, indent=2))
        return EXIT_SIGN_IN
    except APIError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail, "retryable": exc.retryable}, indent=2))
        return exit_code(exc)
    return EXIT_OK


def credential_hint():
    """The words after "HUB_API_URL is not set" for a person's shell."""
    if os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE"):
        return ""
    return "; from your own shell add a profile: tico auth add <name> --url <https://...> --token-file <path>"
