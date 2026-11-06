"""The `hub task create` dry run: the server's own rules run client-side, so a dry run reads as the real create."""

import os
from clients.hubcli_core import (
    CliError,
    body_of,
)


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


def task_problems(actor, owner, title, body, type=None):
    """(resolved owner, the problems the server's `task_create` would refuse on, the writing
    warnings it would accept with), writing none.

    The owner's kind comes from the registry files; the lint (`lint_human_item`) is hubdb's own
    function run here. Reach against live state, the
    duplicate check and whether a named type exists are the hub's to decide at the real create.
    A type other than General is a custom type, whose tasks neither lint shapes.
    """
    from backend import hubdb as H

    problems, warnings = [], []
    title, body = str(title or "").strip(), str(body or "")
    general = str(type or "").strip().lower() in ("", H.GENERAL_TYPE)
    target = owner_from_registry(owner)
    if not target:
        problems.append(f"{owner} is not in registry/employees.yaml or registry/people.yaml")
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
    """`hub task create --dry-run`: print what a create would be refused for; write nothing.

    No database is opened and no request is made; the owner's kind comes from the registry
    files and the same lint runs here, so a bot on the cloud API can size a human item before
    it spends a turn on a refusal. Prints lines rather
    than JSON: `- <problem>` per problem and exit 1, or one `ok:` line.
    """
    from backend import hubdb as H

    if not os.environ.get("HUB_API_URL"):
        raise CliError(
            "HUB_API_URL is not set: `hub` talks to the Tico API and runs inside a "
            "bot turn, where the runner sets HUB_API_URL, HUB_TOKEN and HUB_BOT"
        )
    if who:
        raise CliError("--human is unavailable with HUB_API_URL; remote identity is the token")
    slug = (os.environ.get("HUB_BOT") or os.environ.get("HUB_EMPLOYEE") or "").strip()
    actor = H.bot_actor(slug) if slug else None
    body = body_of(args)
    owner, problems, warnings = task_problems(actor, args.owner, args.title, body, getattr(args, "type", None))
    if problems:
        print("\n".join(f"- {p}" for p in problems + [f"{w} (warning)" for w in warnings]))
        return 1
    for w in warnings:
        print(f"- {w} (warning: the create is accepted)")
    print(
        f'ok: would create "{args.title.strip()}" for {owner} '
        f'({words_outside_quotes(body)} words outside quoted drafts)'
    )
    return 0
