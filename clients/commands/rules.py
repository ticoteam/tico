"""Org rules.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together."""

import json

from clients.tico import APIError

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.commands._shared import ASKED
from clients.commands._requester import _as_person, _asked


# ----------------------------------------------------------------------------- org rules (backend/org_rules.py)
def _rule_scope(description):
    names = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "description": description,
        "properties": {
            "everyone": {"type": "boolean"},
            "all_bots": {"type": "boolean"},
            "groups": names,
            "bots": names,
            "people": names,
            "names": {**names, "description": "A group, person or bot, by id or name"},
            "outside_editions": {
                "type": "boolean",
                "description": "What the Company page sends outside (its editions); the owner and admins only",
            },
        },
    }


def _present(values):
    return {k: v for k, v in values.items() if v is not None}


RULE_VERSION = {"type": "integer", "minimum": 1, "description": "The rule's version as you read it"}


RULE_WHERE = {
    "type": "array",
    "maxItems": 20,
    "items": {"type": "string"},
    "description": "Where it applies, any of: "
    "thread:<id> (a conversation), team:<group>, kind:dm (a direct chat), has_outside_members. Empty or left "
    "out: everywhere",
}


STAGE = "sr_"  # a stage rule's id starts with it (backend/stage_rules.py); an org rule's never does
STAGE_FIELDS = {
    "name": _s("Stage rule: its name"),
    "pipeline": _s("Stage rule: the pipeline it is on (its id); left out or empty: the company, every pipeline"),
    "stages": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Stage rule: the stage ids it is on; empty: the whole pipeline",
    },
    "event": {
        "type": "string",
        "description": "Stage rule: When: enters, owner_changes or bot_snoozes (several comma-separated), or timer",
    },
    "timer": {
        "type": "object",
        "description": 'Stage rule: a timer\'s kind and hours, {"kind": "in_stage"|"quiet"|"waiting_person", "hours": N}',
    },
    "cond": {
        "type": "object",
        "description": "Stage rule: If, fixed conditions such as owner_on_team or owner_not_on_team (a team id; "
        "empty: the manager's team), owner_kind, label, requester_person, sent_back, snooze_hours_over, turn",
    },
    "jev": _s("Stage rule: one question Jev answers (one Jev call per event); empty for none"),
    "then": {
        "type": "string",
        "enum": ["block", "tell_manager", "tell_owner", "move_back"],
        "description": "Stage rule: Then",
    },
    "reason": _s("Stage rule: what a Block or Tell says"),
}
STAGE_KEYS = tuple(STAGE_FIELDS)


def _stage(args):
    return str(args.get("id") or "").startswith(STAGE)


def _stage_decide(api, args, action):
    """Retire, accept or skip a stage rule, as the caller itself: a bot is refused, a person decides."""
    body = _present({"revision": args.get("version"), "why": args.get("why")})
    return api.post(f"stage-rules/{args['id']}/{action}", body, key=_key(args))


@tool(
    "tico_rules_list",
    "The org rules in force, newest first, paged: internal (between teammates) and external "
    "(what someone outside requires). mine=true reads your bot's rules, and its settings as sentences. kind=stage "
    "lists stage rules: with pipeline (and stage), those that apply there, most specific first, each with its cost; "
    "with mine, the ones you wrote or suggested.",
    {
        "kind": {"type": "string", "enum": ["internal", "external", "stage"]},
        "pipeline": _s("kind=stage: the pipeline's id"),
        "stage": _s("kind=stage: a stage id of that pipeline"),
        "status": {"type": "string", "enum": ["suggested", "active", "retired", "any"]},
        "who": _s("A group, person or bot the rule's Who names"),
        "with": _s("A group, person or bot the rule's With names"),
        "source": _s("A From, by name or id"),
        "bot": _s("The rules that apply to this bot"),
        "mine": {"type": "boolean", "description": "The rules that apply to your bot, and its settings"},
        "cursor": _s("next_cursor from the last page"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
)
def rules_list(api, args):
    if args.get("kind") == "stage" or args.get("pipeline") or args.get("stage"):
        query = _present(
            {
                "pipeline": args.get("pipeline"),
                "stage": args.get("stage"),
                "status": args.get("status"),
                "mine": "true" if args.get("mine") else None,
            }
        )
        # As the caller itself: `mine` is the caller's own rules and suggestions.
        return api.get("stage-rules", **query)
    query = _present(
        {
            "kind": args.get("kind"),
            "status": args.get("status"),
            "who": args.get("who"),
            "with": args.get("with"),
            "source": args.get("source"),
            "bot": args.get("bot"),
            "mine": "true" if args.get("mine") else None,
            "cursor": args.get("cursor"),
            "limit": args.get("limit"),
        }
    )
    # `mine` is the caller's own bot: BotOps reads its own rules and settings, not its requester's. Anything else reads
    # as the person (BotOps's requester), like the rest of the rules.
    return (api if args.get("mine") else _as_person(api)).get("rules", **query)


@tool(
    "tico_rules_suggested",
    "The org rule suggestions waiting for a person to accept or skip; stage_rules: the stage rules suggested.",
    {"cursor": _s("next_cursor from the last page"), "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
)
def rules_suggested(api, args):
    found = _as_person(api).get("rules", status="suggested", cursor=args.get("cursor"), limit=args.get("limit"))
    if not args.get("cursor"):
        found = {**found, "stage_rules": (api.get("stage-rules", status="suggested") or {}).get("items", [])}
    return found


@tool(
    "tico_rules_get",
    "One org rule: its sentence, Who and With, From, the due dates, and the quote it cites where "
    "you can read that file. A stage rule (an id starting sr_): On, When, If, Then and its cost.",
    {"id": _s("The rule's id")},
    required=("id",),
)
def rules_get(api, args):
    if _stage(args):
        return api.get(f"stage-rules/{args['id']}")
    return _as_person(api).get(f"rules/{args['id']}")


@tool(
    "tico_rules_read",
    "Add a document for the Rules Owner to read: a file you can read, or a public link (fetched now, saved as a "
    "file version). The Rules Owner suggests the obligations in it as rules, each with its quote, for a person to add or skip. "
    "The Assistant adds it as the person, citing their words (on_behalf_of).",
    {
        "id": _s("A file id, or a public link"),
        "source": _s("The From, by name or id"),
        "kind": {
            "type": "string",
            "enum": ["vendor", "customer", "government", "other"],
            "description": "The kind, when the From is new",
        },
        "note": _s("What the document is for"),
        "on_behalf_of": ASKED,
    },
    required=("id", "source"),
    writes=True,
)
def rules_read(api, args):
    link = args["id"].startswith(("http://", "https://"))
    body = _present(
        {
            "url" if link else "file_id": args["id"],
            "source_id": args["source"],
            "kind": args.get("kind"),
            "note": args.get("note"),
        }
    )
    who = _asked(api, args) if args.get("on_behalf_of") else _as_person(api)
    return who.post("rules/read", body, key=_key(args))


@tool(
    "tico_rules_ask",
    "A can-I question about the company's rules: the active rules that share words with it, cited, "
    "and the Rules Owner's answer on a task. The Rules Owner answers from the rules only, and says it is not legal advice. The "
    "Assistant asks as the person, citing their words (on_behalf_of).",
    {"question": _s("The question, in one sentence"), "on_behalf_of": ASKED},
    required=("question",),
    writes=True,
)
def rules_ask(api, args):
    who = _asked(api, args) if args.get("on_behalf_of") else _as_person(api)
    return who.post("rules/ask", {"question": args["question"]}, key=_key(args))


@tool(
    "tico_rules_add",
    "Add an org rule. Internal: Who (and With, the teammates it is about). External: Who and From "
    "(source_id, a name or id). A bot's add is a suggestion; only the owner and admins add an Everyone rule. The "
    "Assistant adds one as the person, citing their words (on_behalf_of). kind=stage adds a stage rule: name, "
    "pipeline, stages, event (When), cond and jev (If), then and reason; a bot's is a suggestion a person accepts.",
    {
        "kind": {"type": "string", "enum": ["internal", "external", "stage"]},
        "text": _s("The rule in one sentence"),
        "description": _s("Optional, longer"),
        "who": _rule_scope(
            "Who follows it. A bot may leave it out when no department is named: the owner sets it on accept"
        ),
        "with": _rule_scope("Internal: who it is about"),
        "source_id": _s("External: the From, by name or id"),
        "cite": {
            "type": "object",
            "additionalProperties": False,
            "description": "External: the words the rule rests on, "
            "which must be in that file's version. A quote that is not there is dropped (the answer says so).",
            "properties": {
                "file_id": _s("The document's file id"),
                "version": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The file's version the quote is in (the Rules Owner passes the one its task names)",
                },
                "section": _s("The section it is in"),
                "quote": _s("The exact words, which must be in that version of the file"),
            },
            "required": ["file_id", "quote"],
        },
        "if_broken": {"type": "string", "enum": ["block", "escalate", "warn"]},
        "where": RULE_WHERE,
        "reviewer": _s("A person who reviews outputs under it"),
        "on_behalf_of": ASKED,
        **STAGE_FIELDS,
    },
    required=("kind",),
    writes=True,
)
def rules_add(api, args):
    if args.get("kind") == "stage":
        # As the caller itself: a bot's stage rule is a suggestion, never its requester's rule.
        return api.post("stage-rules", _present({k: args.get(k) for k in STAGE_KEYS}), key=_key(args))
    body = _present(
        {
            k: args.get(k)
            for k in (
                "kind",
                "text",
                "description",
                "who",
                "with",
                "where",
                "source_id",
                "cite",
                "if_broken",
                "reviewer",
            )
        }
    )
    who = _asked(api, args) if args.get("on_behalf_of") else _as_person(api)
    return who.post("rules", body, key=_key(args))


@tool(
    "tico_rules_edit",
    "Edit an org rule: only the fields sent, against the version you read. A bot cannot edit. A stage rule (an id "
    "starting sr_) takes its own fields, against its revision as `version`; a bot's change is a suggestion.",
    {
        "id": _s("The rule's id"),
        "version": RULE_VERSION,
        "text": _s("The rule in one sentence"),
        "description": _s("Longer text"),
        "who": _rule_scope("Who follows it"),
        "with": _rule_scope("Internal: with whom"),
        "source_id": _s("External: the From, by name or id"),
        "where": RULE_WHERE,
        "if_broken": {"type": "string", "enum": ["block", "escalate", "warn"]},
        "reviewer": _s("A reviewer"),
        **STAGE_FIELDS,
    },
    required=("id", "version"),
    writes=True,
)
def rules_edit(api, args):
    if _stage(args):
        body = _present({"revision": args.get("version"), **{k: args.get(k) for k in STAGE_KEYS}})
        return api.post(f"stage-rules/{args['id']}", body, key=_key(args))
    body = _present(
        {
            k: args.get(k)
            for k in ("version", "text", "description", "who", "with", "where", "source_id", "if_broken", "reviewer")
        }
    )
    return _as_person(api).post(f"rules/{args['id']}", body, key=_key(args))


@tool(
    "tico_rules_accept",
    "Accept a suggested org rule (optionally changing its Who first). A person does this.",
    {"id": _s("The rule's id"), "version": RULE_VERSION, "who": _rule_scope("Who, if it changes")},
    required=("id",),
    writes=True,
)
def rules_accept(api, args):
    if _stage(args):
        return _stage_decide(api, args, "accept")
    body = _present({"version": args.get("version"), "who": args.get("who")})
    return _as_person(api).post(f"rules/{args['id']}/accept", body, key=_key(args))


@tool(
    "tico_rules_skip",
    "Skip a suggested org rule: it is set aside and is not in force.",
    {"id": _s("The rule's id"), "version": RULE_VERSION},
    required=("id",),
    writes=True,
)
def rules_skip(api, args):
    if _stage(args):
        return _stage_decide(api, args, "skip")
    return _as_person(api).post(f"rules/{args['id']}/skip", _present({"version": args.get("version")}), key=_key(args))


@tool(
    "tico_rules_retire",
    "Retire an org rule in force. Its due dates come back so you can remove them too.",
    {"id": _s("The rule's id"), "version": RULE_VERSION},
    required=("id",),
    writes=True,
)
def rules_retire(api, args):
    if _stage(args):
        return _stage_decide(api, args, "retire")
    return _as_person(api).post(
        f"rules/{args['id']}/retire", _present({"version": args.get("version")}), key=_key(args)
    )


@tool(
    "tico_rules_due",
    "The due dates of rules in force: overdue always, then those falling due within next (default "
    "90d). status narrows to overdue, due_soon, upcoming or satisfied.",
    {
        "next": _s("A number of days, such as 90d"),
        "status": {"type": "string", "enum": ["overdue", "due_soon", "upcoming", "satisfied"]},
        "cursor": _s("next_cursor from the last page"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
)
def rules_due(api, args):
    return _as_person(api).get(
        "rules/due",
        **_present(
            {
                "next": args.get("next"),
                "status": args.get("status"),
                "cursor": args.get("cursor"),
                "limit": args.get("limit"),
            }
        ),
    )


TASK_DAYS = {
    "type": "integer",
    "minimum": 0,
    "maximum": 366,
    "description": "A routine opens the owner's task this many days before each due day",
}


@tool(
    "tico_rules_due_set",
    "Add a due date to an org rule: a first day, a repeat, an owner (a bot or a person), and the "
    "routine's lead time in days.",
    {
        "id": _s("The rule's id"),
        "first_due": _s("YYYY-MM-DD"),
        "repeats": {"type": "string", "enum": ["once", "weekly", "monthly", "quarterly", "yearly"]},
        "owner": _s("The bot or person who owns it, by name or id"),
        "timezone": _s("An IANA zone, such as America/Chicago"),
        "task_days": TASK_DAYS,
    },
    required=("id", "first_due", "repeats", "owner"),
    writes=True,
)
def rules_due_set(api, args):
    body = _present({k: args.get(k) for k in ("first_due", "repeats", "owner", "timezone", "task_days")})
    return _as_person(api).post(f"rules/{args['id']}/due", body, key=_key(args))


@tool(
    "tico_rules_due_edit",
    "Change a rule's due date. A new first day or repeat starts a new due date and keeps the old "
    "one in its history; an owner or zone changes it in place. task_days sets the routine's lead time; no_routine "
    "turns the routine off.",
    {
        "id": _s("The due date's id"),
        "first_due": _s("YYYY-MM-DD"),
        "repeats": {"type": "string", "enum": ["once", "weekly", "monthly", "quarterly", "yearly"]},
        "owner": _s("The bot or person who owns it, by name or id"),
        "timezone": _s("An IANA zone, such as America/Chicago"),
        "task_days": TASK_DAYS,
        "no_routine": {"type": "boolean", "description": "Turn the routine off"},
    },
    required=("id",),
    writes=True,
)
def rules_due_edit(api, args):
    body = _present({k: args.get(k) for k in ("first_due", "repeats", "owner", "timezone", "task_days")})
    if args.get("no_routine"):
        if "task_days" in body:
            raise APIError("validation", "Give task_days or no_routine, not both", 422)
        body["task_days"] = None
    return _as_person(api).post(f"rule-due/{args['id']}", body, key=_key(args))


@tool(
    "tico_rules_due_satisfied",
    "Mark a rule's due date satisfied for its current occurrence. With more than one open, "
    "due_on says which. The id is a due date's, or the rule's when it has one due date.",
    {"id": _s("A due date's id, or its rule's"), "due_on": _s("YYYY-MM-DD, when more than one is open")},
    required=("id",),
    writes=True,
)
def rules_due_satisfied(api, args):
    return _as_person(api).post(
        f"rule-due/{args['id']}/satisfied", _present({"due_on": args.get("due_on")}), key=_key(args)
    )


@tool(
    "tico_rules_check",
    "Check a draft against the org rules that apply to you, as the write it will be (as: message, "
    "comment, task, assign). Answers pass, broken (with the rules) or unchecked. Sending that exact draft to the same "
    "teammate then costs no second check.",
    {
        "text": _s("The draft, whole"),
        "to": _s("The teammate it goes to"),
        "as": {"type": "string", "enum": ["message", "comment", "task", "assign"]},
    },
    required=("text", "to"),
    writes=True,
)
def rules_check(api, args):
    body = _present({"text": args.get("text"), "to": args.get("to"), "as": args.get("as")})
    return api.post("rules/check", body, key=_key(args))


@tool(
    "tico_rules_appeal",
    "Ask the reviewer to look at your held output: they get a review task with your reason and "
    "may send it anyway. Never checked by a rule.",
    {"id": _s("The hold's id, from the held answer"), "why": _s("Why the rule does not apply")},
    required=("id",),
    writes=True,
)
def rules_appeal(api, args):
    return api.post(f"rule-holds/{args['id']}/appeal", {"why": args.get("why") or ""}, key=_key(args))


@tool(
    "tico_rules_withdraw",
    "Take back your held write so you can edit it and send it again: the hold is kept and "
    "nothing is sent. Only the author of the held write may withdraw it.",
    {"id": _s("The hold's id, from the held answer")},
    required=("id",),
    writes=True,
)
def rules_withdraw(api, args):
    return api.post(f"rule-holds/{args['id']}/withdraw", {}, key=_key(args))


# `tico rules ...` (clients/cli_spec.py): each leaf's flags come from its tool's schema.
def _scope(names=None, **switches):
    """A Who or With scope object from the command line's names and switches; None when none was given."""
    out = ({"names": list(names)} if names else {}) | {k: True for k, on in switches.items() if on}
    return out or None


def _who(fields):
    who = _scope(
        fields.get("who_names"),
        everyone=fields.get("everyone"),
        all_bots=fields.get("all_bots"),
        outside_editions=fields.get("outside_editions"),
    )
    return fields | ({"who": who} if who else {})


def _who_with(fields):
    fields = _who(fields)
    return fields | ({"with": {"names": list(fields["with_names"])}} if fields.get("with_names") else {})


def _add(fields):
    """`rules add`: the scopes, and --file, --section, --quote and --version as the quote the rule cites."""
    fields = _who_with(fields)
    if fields.get("file_version") and not fields.get("quote"):
        raise SystemExit("tico rules add: --version goes with --file and --quote")
    if fields.get("quote"):
        if not fields.get("file_id"):
            raise SystemExit("tico rules add: a quote needs --file (the document it is in)")
        cite = {"file_id": fields["file_id"], "section": fields.get("section") or "", "quote": fields["quote"]}
        fields["cite"] = cite | ({"version": fields["file_version"]} if fields.get("file_version") else {})
    return fields


WHO = (
    Arg("who_names", "--who", action="append", metavar="NAME", into="who", part="names"),
    Arg("everyone", "--everyone", action="store_true", into="who"),
    Arg("all_bots", "--all-bots", action="store_true", into="who"),
    Arg("outside_editions", "--outside-editions", action="store_true", into="who"),
)
WITH = (Arg("with_names", "--with", action="append", metavar="NAME", into="with", part="names"),)
CITE = (
    Arg("file_id", "--file", metavar="FILE_ID", into="cite"),
    Arg("section", "--section", into="cite"),
    Arg("quote", "--quote", into="cite"),
    Arg("file_version", "--version", type=int, metavar="N", into="cite", part="version"),
)
FROM = Arg("source_id", "--from", metavar="SOURCE")
STAGE_JSON = (Arg("timer", type=json.loads, metavar="JSON"), Arg("cond", "--if", type=json.loads, metavar="JSON"))
DAY = "YYYY-MM-DD"

CLI = (
    Group(
        "rules",
        "org rules: what teammates follow (internal) and what someone outside requires (external), and their due dates",
        aliases=("rule",),
    ),
    Command(
        "rules list",
        "the rules in force; --mine: your bot's rules and its settings as sentences; --kind stage: stage rules, "
        "--pipeline and --stage for those that apply there",
        example="tico rules list --kind stage --pipeline engineering --stage engineering-build",
    ),
    Command(
        "rules read",
        "one rule, with its due dates and the quote it cites; with --from, add a document for the Rules Owner to read "
        "(a file id or a link)",
        tool="tico_rules_get",
        tools=("tico_rules_read",),
        args=(Arg("source", "--from", metavar="SOURCE"),),
        tool_for=lambda fields: "tico_rules_read" if fields.get("source") else "tico_rules_get",
        example="tico rules read https://acme.example/terms --from Acme --kind vendor",
    ),
    Command(
        "rules ask",
        "a can-I question: the rules that apply, cited, and the Rules Owner's answer on a task",
        example="tico rules ask 'Can I share the deck with Acme?'",
    ),
    Command(
        "rules suggested",
        "the suggestions waiting for a person to accept or skip",
        example="tico rules suggested",
    ),
    Command(
        "rules add",
        "add a rule: a bot's is a suggestion; only owners and admins add Everyone",
        args=(Arg("kind", positional=False), Arg("text", positional=False), FROM, *WHO, *WITH, *CITE, *STAGE_JSON),
        convert=_add,
        example="tico rules add --kind stage --name 'Owner on the team' --pipeline engineering --stages engineering-build "
        "--event owner_changes --then block",
    ),
    Command(
        "rules edit",
        "change a rule, only the fields given, against the version you read",
        args=(Arg("version", positional=False), FROM, *WHO, *WITH, *STAGE_JSON),
        convert=_who_with,
        example="tico rules edit r1 --version 2 --text 'Reply within four hours'",
    ),
    Command(
        "rules accept",
        "a person accepts a suggestion, optionally changing its Who",
        args=WHO,
        convert=_who,
        example="tico rules accept r1 --who Support",
    ),
    Command("rules skip", "set a suggestion aside", example="tico rules skip r1"),
    Command(
        "rules retire", "retire a rule in force; its due dates come back", example="tico rules retire r1 --version 2"
    ),
    Group("rules due", "due dates on rules: the next ones, add one, mark one satisfied", default="rules due list"),
    Command(
        "rules due list",
        "the due dates of rules in force: overdue first, then those due within --next days",
        tool="tico_rules_due",
        example="tico rules due list --next 30d",
    ),
    Command(
        "rules due add",
        "a due date on a rule: first day, repeat, owner",
        tool="tico_rules_due_set",
        args=(
            Arg("first_due", "--first", metavar=DAY),
            Arg("repeats", positional=False),
            Arg("owner", positional=False),
        ),
        example="tico rules due add r1 --first 2026-12-01 --repeats monthly --owner Ada",
    ),
    Command(
        "rules due edit",
        "change a due date; a new first day or repeat starts a new one, history kept",
        args=(Arg("first_due", "--first", metavar=DAY), Arg("no_routine", default=None)),
        exclusive=(("task_days", "no_routine"),),
        example="tico rules due edit d1 --task-days 3",
    ),
    Command(
        "rules due satisfied",
        "mark the current occurrence satisfied",
        args=(Arg("due_on", "--on", metavar=DAY),),
        example="tico rules due satisfied d1",
    ),
    Command(
        "rules check",
        "check a draft against the rules that apply to you, as the write it will be",
        args=(Arg("to", positional=False), Arg("as", default="message")),
        example="tico rules check 'Sending the deck now' --to Ada",
    ),
    Command(
        "rules appeal",
        "ask the reviewer to look at your held output",
        args=(Arg("why", positional=True, nargs="?", default=""),),
        example="tico rules appeal h1 'The rule is about invoices, not decks'",
    ),
    Command(
        "rules withdraw",
        "take back your held write to edit it and send it again (nothing is sent)",
        example="tico rules withdraw h1",
    ),
)
