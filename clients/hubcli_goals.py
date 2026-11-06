"""Goal commands: goals, KPIs, proposals and rules."""

import argparse
from clients.hubcli_core import (
    GOAL_STATUSES,
)


def add_goals(sub):
    goal = sub.add_parser(
        "goal",
        help="goals: list, read-back, create and edit",
        description="Read back goal changes in the owner's Strategy Bot chat. "
        "Saves nothing without a plain yes; "
        "cite that yes with --on-behalf-of turn (or its message id) on each save.",
    ).add_subparsers(dest="sub")
    s = goal.add_parser(
        "read-back",
        help="Tico writes the draft; saves nothing without a plain yes",
        description="Post 1 to 20 changes as Tico's read-back. Saves nothing without a plain yes. "
        "Returns message_id, text and exact method/path/body steps. After yes, use goal save "
        "for each agreed step; replace {new} with the first save's goal id using --created-goal. "
        "On not_agreed read back again; never retry the save.",
    )
    s.add_argument(
        "changes",
        help="JSON array: kind goal_create, goal_update, measure_add, target_set, measure_remove or suggestion, "
        "and that change's fields",
    )
    s.set_defaults(fn="goal read-back")
    s = goal.add_parser(
        "save",
        help="save one exact read-back step after the owner's plain yes, or record the owner's "
        "\"no\" to a suggestion (goal-proposals/turned-down)",
    )
    s.add_argument("method", choices=["POST"])
    s.add_argument("path", help="the step's /api/v2 path")
    s.add_argument("body", help="the step's exact body string, unchanged")
    s.add_argument("--created-goal", dest="created_goal", help="first save's goal id, to replace {new} in later paths")
    s.add_argument("--on-behalf-of", required=True, help="turn or the message id of the owner's plain yes")
    s.set_defaults(fn="goal save")
    s = goal.add_parser("list", help="what you are for: your goals, the chain above them, your reports' goals")
    s.add_argument("--owner", help="someone else's: a bot slug or a person id")
    s.add_argument("--all", action="store_true", help="every live goal in the company")
    s.add_argument("--status", help="with --all: only these, comma-separated (red,yellow,green,gray,done,dropped)")
    s.set_defaults(fn="goal list")
    s = goal.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="goal show")
    s = goal.add_parser("create", help="set a goal; a parent is optional, and it starts with no colour")
    s.add_argument(
        "--owner", required=True, help="me, a bot slug, or a person id (company: a company goal, the owner only)"
    )
    s.add_argument("--title", required=True)
    s.add_argument("--parent", help="the goal this one supports, if any; not needed")
    s.add_argument("--body", default="")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--top", action="store_true", help="put it first in the owner's order")
    s.add_argument("--bet", dest="bet_id", help="the strategy bet it serves (hub strategy show lists them)")
    s.add_argument(
        "--on-behalf-of",
        metavar="MESSAGE_ID",
        help="turn or the message id of the owner's plain yes to a goal read-back",
    )
    s.set_defaults(fn="goal create")
    s = goal.add_parser(
        "status",
        help="set the colour by hand: red, yellow or green with one sentence, done or dropped "
        "when it ends; it sticks until a person hands it back (status auto)",
    )
    s.add_argument("id")
    s.add_argument("status", choices=list(GOAL_STATUSES) + ["auto"])
    s.add_argument("note", nargs="?", default="")
    s.set_defaults(fn="goal status")
    s = goal.add_parser("update")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--body")
    s.add_argument("--body-file", dest="body_file")
    s.add_argument("--parent", help='the goal it supports; "" unlinks it')
    s.add_argument("--owner")
    s.add_argument("--rank", type=int)
    s.add_argument("--top", action="store_true")
    s.add_argument("--bet", dest="bet_id", help='the strategy bet it serves; "" unlinks it')
    s.add_argument(
        "--on-behalf-of",
        metavar="MESSAGE_ID",
        help="turn or the message id of the owner's plain yes to a goal read-back",
    )
    s.set_defaults(fn="goal update")
    s = goal.add_parser("refresh", help="the Strategy Bot's status pass: work automatic colours out again")
    s.add_argument("--goal", action="append", dest="goal_ids", help="only this goal; repeatable")
    s.set_defaults(fn="goal refresh")
    s = goal.add_parser("checkin", help="how the owner says the goal is going, in their words")
    s.add_argument("id")
    s.add_argument("body")
    s.add_argument("--signal", choices=["on_track", "at_risk", "off_track"])
    s.add_argument("--from", dest="from_actor", help="whose words these are, when you record them for someone")
    s.add_argument("--kpi", dest="kpi_id", help="the KPI that prompted it")
    s.set_defaults(fn="goal checkin")
    s = goal.add_parser("checkin-list", help="a goal's check-ins, newest first")
    s.add_argument("id")
    s.set_defaults(fn="goal checkin-list")
    s = goal.add_parser("needs-you", help="red KPIs on your goals, stale KPIs you own, proposals to confirm")
    s.set_defaults(fn="goal needs-you")
    s = goal.add_parser("company", help="the company's Vision and Description (read-only)")
    s.set_defaults(fn="goal company")
    s = goal.add_parser(
        "strategy-bot", help="the Strategy Bot: its level, regular checks and today's Needs you count per person"
    )
    s.set_defaults(fn="goal strategy-bot")
    s = goal.add_parser(
        "strategy-bot-merge", help="the Strategy Bot at level 2+: one item for several bots' same ask of a person"
    )
    s.add_argument("person", help="the person they all wait on (a person id)")
    s.add_argument("title")
    s.add_argument("question", help="the one question that answers them all")
    s.add_argument("tasks", nargs="+", help="2 to 10 task ids waiting on that person")
    s.set_defaults(fn="goal strategy-bot-merge")
    s = goal.add_parser(
        "strategy-bot-level3-offer", help="the Strategy Bot: record the level-3 offer its Monday note made"
    )
    s.set_defaults(fn="goal strategy-bot-level3-offer")
    s = goal.add_parser(
        "strategy-bot-decide", help="the Strategy Bot at level 3: one decision alone on bot tasks, with Undo"
    )
    s.add_argument("reason", help="why, in one sentence the owner reads in the log")
    s.add_argument(
        "changes",
        help='a JSON list of 1 to 10 changes: {"do": "create", "owner": "<bot>", "title": "..."}, '
        '{"do": "reorder"|"close", "task_id": "..."}, {"do": "reassign", "task_id": "...", '
        '"owner": "<bot>"}',
    )
    s.add_argument("--goal-id", dest="goal_id", help="the goal this serves")
    s.set_defaults(fn="goal strategy-bot-decide")
    s = goal.add_parser(
        "strategy-bot-log",
        help="the Strategy Bot log (the owner only): what it decided alone and what it proposed instead",
    )
    s.add_argument("--before", help="next_before from the previous page")
    s.set_defaults(fn="goal strategy-bot-log")

    def target_flags(s):
        s.add_argument("--baseline", type=float, help="improvement: where it starts (default the latest reading)")
        s.add_argument("--target", type=float, help="improvement: the value to reach; needs --deadline")
        s.add_argument("--deadline", help="improvement: when the target is due, YYYY-MM-DD")
        s.add_argument("--min", type=float, help="range: the lowest acceptable value")
        s.add_argument("--max", type=float, help="range: the highest acceptable value")

    def kpi_flags(s):
        s.add_argument("--definition", help="what exactly is counted, in a sentence")
        s.add_argument("--unit")
        s.add_argument("--direction", choices=["up", "down", "range"], help="which way is good")
        s.add_argument("--cadence", choices=["daily", "weekly", "monthly"], help="how often it is read")
        s.add_argument("--source-note", dest="source_note", help="where the number comes from")

    kpi = sub.add_parser("kpi", help="a measure on its own that goals link to, and its readings").add_subparsers(
        dest="sub"
    )
    s = kpi.add_parser("list", help="KPIs with latest reading and colour")
    s.add_argument("--goal", dest="goal_id", help="only the KPIs this goal uses")
    s.add_argument("--owner", help="only this owner's: me, a bot slug or a person id")
    s.add_argument("--unlinked", action="store_true", help="only the KPIs no goal uses")
    s.add_argument("--bot", help="a bot slug: its five automatic KPIs")
    s.add_argument(
        "--archived", dest="include_archived", action="store_true", help="include archived KPIs for historical review"
    )
    s.set_defaults(fn="kpi list")
    s = kpi.add_parser("show", help="definition and versions, the goals using it, every reading, check-ins")
    s.add_argument("id")
    s.add_argument("--effective", action="store_true", help="leave out readings a correction replaced")
    s.set_defaults(fn="kpi show")
    for action in ("archive", "restore"):
        s = kpi.add_parser(action, help=f"{action} a KPI while keeping its definitions, links and readings")
        s.add_argument("id")
        s.set_defaults(fn="kpi " + action)
    s = kpi.add_parser("create", help="make a KPI; with --goal it is linked, the target on the link")
    s.add_argument("name", help="what is counted, per what: 'booked demos per two weeks'")
    s.add_argument("--goal", dest="goal_id", help="link it to this goal")
    s.add_argument("--owner", help="me (default), company, a bot slug or a person id")
    kpi_flags(s)
    target_flags(s)
    s.add_argument(
        "--on-behalf-of",
        metavar="MESSAGE_ID",
        help="turn or the message id of the owner's plain yes to a goal read-back",
    )
    s.set_defaults(fn="kpi create")
    s = kpi.add_parser("update", help="a change to what it measures is a new definition version")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--owner")
    kpi_flags(s)
    s.add_argument(
        "--parent", dest="parent_kpi_id", metavar="ID", help='the KPI it feeds in the growth tree; "" clears'
    )
    s.add_argument(
        "--role",
        dest="growth_role",
        choices=["north_star", "input", "guardrail", ""],
        help="one North Star at a time: naming a new one moves the role",
    )
    s.add_argument("--stage", choices=["reach", "acquire", "activate", "retain", "refer", ""])
    s.add_argument("--loop", metavar="NAME:STEP", help='its place in a growth loop, referral:2; "" clears')
    s.set_defaults(fn="kpi update")
    s = kpi.add_parser("link", help="link a goal to a KPI, or change the target on the link")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
    target_flags(s)
    s.add_argument(
        "--on-behalf-of",
        metavar="MESSAGE_ID",
        help="turn or the message id of the owner's plain yes to a goal read-back",
    )
    s.set_defaults(fn="kpi link")
    s = kpi.add_parser("target", help="set or remove the target on a goal's measure")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
    target_flags(s)
    s.add_argument("--on-behalf-of", help="turn or the message id of the owner's plain yes")
    s.set_defaults(fn="kpi target")
    s = kpi.add_parser("unlink", help="take a KPI off a goal")
    s.add_argument("goal_id")
    s.add_argument("kpi_id")
    s.add_argument(
        "--on-behalf-of",
        metavar="MESSAGE_ID",
        help="turn or the message id of the owner's plain yes to a goal read-back",
    )
    s.set_defaults(fn="kpi unlink")
    s = kpi.add_parser("log", help="a reading: a fact with its period; to correct one, supersede it")
    s.add_argument("kpi_id")
    s.add_argument("value", type=float)
    s.add_argument("note", nargs="?", default="")
    s.add_argument("--period-start", dest="period_start", help="start of the period it describes")
    s.add_argument("--period-end", dest="period_end", help="end of the period it describes; default now")
    s.add_argument("--at", help="the old name of --period-end")
    s.add_argument("--collected-at", dest="collected_at", help="when it was collected; default now")
    s.add_argument("--evidence", help="a link or a note that shows where the value came from")
    s.add_argument("--quality", choices=["measured", "estimate", "partial"])
    s.add_argument("--estimate", action="store_true", help="the same as --quality estimate")
    s.add_argument("--source", help="a connector or system name (posthog, close)")
    s.add_argument("--definition-version", dest="definition_version", type=int)
    s.add_argument("--supersedes", help="the id of the reading this one corrects")
    s.set_defaults(fn="kpi log")

    proposal = sub.add_parser(
        "proposal", help="a change you may not make yourself, for the owner to confirm"
    ).add_subparsers(dest="sub")
    s = proposal.add_parser("create")
    s.add_argument(
        "--kind",
        required=True,
        choices=[
            "goal_wording",
            "goal_kpi",
            "kpi_definition",
            "kpi_target",
            "flag",
            "goal_why",
            "recovery",
            "strategy_draft",
            "brand_draft",
        ],
    )
    s.add_argument("--goal", dest="goal_id")
    s.add_argument("--kpi", dest="kpi_id")
    s.add_argument("--payload", help="the proposed change, as JSON")
    s.add_argument("--payload-file", dest="payload_file")
    s.add_argument("--reason", default="")
    s.set_defaults(fn="proposal create")
    s = proposal.add_parser("list")
    s.add_argument("--status", choices=["pending", "confirmed", "rejected", "all"], default="pending")
    s.add_argument("--goal", dest="goal_id")
    s.add_argument("--kpi", dest="kpi_id")
    s.set_defaults(fn="proposal list")
    s = proposal.add_parser("decide", help="a person's own decision; a bot is refused")
    s.add_argument("id")
    s.add_argument("decision", choices=["confirm", "reject"])
    s.add_argument("--note", default="")
    s.add_argument("--option", type=int, help="a recovery: approve this option (0 is the first) instead of the pick")
    s.add_argument(
        "--on-behalf-of",
        metavar="MESSAGE_ID",
        help="turn or the message id of the owner's plain yes to a goal read-back",
    )
    s.set_defaults(fn="proposal decide")

    rules = sub.add_parser(
        "rules",
        aliases=["rule"],
        help="org rules: what teammates follow (internal) and what someone outside requires "
        "(external), and their due dates",
    ).add_subparsers(dest="sub")
    s = rules.add_parser("list", help="the rules in force; --mine: your bot's rules and its settings as sentences")
    s.add_argument("--kind", choices=["internal", "external"])
    s.add_argument("--status", choices=["suggested", "active", "retired", "any"])
    s.add_argument("--who", help="a group, person or bot the rule's Who names")
    s.add_argument("--with", dest="with", help="a group, person or bot the rule's With names")
    s.add_argument("--source", help="a From, by name or id")
    s.add_argument("--bot", help="the rules that apply to this bot")
    s.add_argument("--mine", action="store_true", help="the rules that apply to your bot, and its settings")
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="rules list")
    s = rules.add_parser(
        "read",
        help="one rule, with its due dates and the quote it cites; with --from, add a document "
        "for Counsel to read (a file id or a link)",
    )
    s.add_argument("id", help="a rule id, or with --from a file id or a link")
    s.add_argument("--from", dest="source", metavar="SOURCE", help="document: the From, by name or id")
    s.add_argument(
        "--kind", choices=["vendor", "customer", "government", "other"], help="document: the kind, for a new From"
    )
    s.add_argument("--note", help="document: what the document is for")
    s.set_defaults(fn="rules read")
    s = rules.add_parser("ask", help="a can-I question: the rules that apply, cited, and Counsel's answer on a task")
    s.add_argument("question")
    s.set_defaults(fn="rules ask")
    s = rules.add_parser("suggested", help="the suggestions waiting for a person to accept or skip")
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="rules suggested")
    s = rules.add_parser("add", help="add a rule: a bot's is a suggestion; only owners and admins add Everyone")
    s.add_argument("--kind", required=True, choices=["internal", "external"])
    s.add_argument("--text", required=True, help="the rule in one sentence")
    s.add_argument("--description")
    s.add_argument(
        "--who",
        dest="who_names",
        action="append",
        default=[],
        metavar="NAME",
        help="a group, person or bot by id or name; repeatable",
    )
    s.add_argument("--everyone", action="store_true")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.add_argument(
        "--outside-editions",
        dest="outside_editions",
        action="store_true",
        help="what the Company page sends outside (owner and admins only)",
    )
    s.add_argument(
        "--with",
        dest="with_names",
        action="append",
        default=[],
        metavar="NAME",
        help="internal: a group, person or bot the rule is about; repeatable",
    )
    s.add_argument(
        "--where",
        action="append",
        default=None,
        metavar="TERM",
        help="where it applies: thread:ID, team:GROUP, kind:dm or has_outside_members; repeatable",
    )
    s.add_argument("--from", dest="source_id", metavar="SOURCE", help="external: the From, by name or id")
    s.add_argument("--file", dest="file_id", metavar="FILE_ID", help="external: the document the rule rests on")
    s.add_argument("--section", help="external: the section of the document")
    s.add_argument("--quote", help="external: the words of the document the rule rests on (must be in the file)")
    s.add_argument(
        "--version",
        dest="file_version",
        type=int,
        metavar="N",
        help="external: the version of --file the quote is in (Counsel passes the one its task names)",
    )
    s.add_argument("--if-broken", dest="if_broken", choices=["block", "escalate", "warn"])
    s.add_argument("--reviewer")
    s.set_defaults(fn="rules add")
    s = rules.add_parser("edit", help="change a rule, only the fields given, against the version you read")
    s.add_argument("id")
    s.add_argument("--version", type=int, required=True)
    s.add_argument("--text")
    s.add_argument("--description")
    s.add_argument("--who", dest="who_names", action="append", default=[], metavar="NAME")
    s.add_argument("--everyone", action="store_true")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.add_argument("--outside-editions", dest="outside_editions", action="store_true")
    s.add_argument("--with", dest="with_names", action="append", default=[], metavar="NAME")
    s.add_argument(
        "--where",
        action="append",
        default=None,
        metavar="TERM",
        help="where it applies (replaces the list): thread:ID, team:GROUP, kind:dm or has_outside_members",
    )
    s.add_argument("--from", dest="source_id", metavar="SOURCE")
    s.add_argument("--if-broken", dest="if_broken", choices=["block", "escalate", "warn"])
    s.add_argument("--reviewer")
    s.set_defaults(fn="rules edit")
    s = rules.add_parser("accept", help="a person accepts a suggestion, optionally changing its Who")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.add_argument("--who", dest="who_names", action="append", default=[], metavar="NAME")
    s.add_argument("--everyone", action="store_true")
    s.add_argument("--all-bots", dest="all_bots", action="store_true")
    s.add_argument("--outside-editions", dest="outside_editions", action="store_true")
    s.set_defaults(fn="rules accept")
    s = rules.add_parser("skip", help="set a suggestion aside")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.set_defaults(fn="rules skip")
    s = rules.add_parser("retire", help="retire a rule in force; its due dates come back")
    s.add_argument("id")
    s.add_argument("--version", type=int)
    s.set_defaults(fn="rules retire")
    due = rules.add_parser("due", help="due dates on rules: the next ones, add one, mark one satisfied")
    # The list's flags work on `due` itself and on `list`, which is the same command. SUPPRESS keeps a flag given on
    # one from being reset by the other's default.
    due.add_argument("--next", default=argparse.SUPPRESS, help="days ahead to list, such as 90d (default 90d)")
    due.add_argument("--status", default=argparse.SUPPRESS, choices=["overdue", "due_soon", "upcoming", "satisfied"])
    due.add_argument("--cursor", default=argparse.SUPPRESS)
    due.add_argument("--limit", type=int, default=argparse.SUPPRESS)
    due.set_defaults(fn="rules due")
    due_sub = due.add_subparsers(dest="subsub")
    s = due_sub.add_parser(
        "list", help="the due dates of rules in force: overdue first, then those due within --next days"
    )
    s.add_argument("--next", default=argparse.SUPPRESS)
    s.add_argument("--status", default=argparse.SUPPRESS, choices=["overdue", "due_soon", "upcoming", "satisfied"])
    s.add_argument("--cursor", default=argparse.SUPPRESS)
    s.add_argument("--limit", type=int, default=argparse.SUPPRESS)
    s.set_defaults(fn="rules due")
    s = due_sub.add_parser("add", help="a due date on a rule: first day, repeat, owner")
    s.add_argument("id", help="the rule's id")
    s.add_argument("--first", dest="first_due", required=True, metavar="YYYY-MM-DD")
    s.add_argument("--repeats", required=True, choices=["once", "weekly", "monthly", "quarterly", "yearly"])
    s.add_argument("--owner", required=True, help="a bot or person, by name or id")
    s.add_argument("--timezone")
    s.add_argument("--task-days", type=int, metavar="N", help="a routine opens the owner's task N days before each one")
    s.set_defaults(fn="rules due set")
    s = due_sub.add_parser("edit", help="change a due date; a new first day or repeat starts a new one, history kept")
    s.add_argument("id", help="the due date's id")
    s.add_argument("--first", dest="first_due", metavar="YYYY-MM-DD")
    s.add_argument("--repeats", choices=["once", "weekly", "monthly", "quarterly", "yearly"])
    s.add_argument("--owner", help="a bot or person, by name or id")
    s.add_argument("--timezone")
    routine = s.add_mutually_exclusive_group()
    routine.add_argument("--task-days", type=int, metavar="N", help="a routine opens the owner's task N days before")
    routine.add_argument("--no-routine", action="store_true", default=None, help="turn the routine off")
    s.set_defaults(fn="rules due edit")
    s = due_sub.add_parser("satisfied", help="mark the current occurrence satisfied")
    s.add_argument("id", help="a due date's id, or its rule's when it has one due date")
    s.add_argument("--on", dest="due_on", metavar="YYYY-MM-DD", help="which occurrence, when two are open")
    s.set_defaults(fn="rules due satisfied")
    s = rules.add_parser("check", help="check a draft against the rules that apply to you, as the write it will be")
    s.add_argument("text", help="the draft, whole")
    s.add_argument("--to", required=True, help="the teammate it goes to")
    s.add_argument("--as", dest="as_", default="message", choices=["message", "comment", "task", "assign"])
    s.set_defaults(fn="rules check")
    s = rules.add_parser("appeal", help="ask the reviewer to look at your held output")
    s.add_argument("id", help="the hold's id, from the held answer")
    s.add_argument("why", nargs="?", default="", help="why you think the rule does not apply")
    s.set_defaults(fn="rules appeal")
    s = rules.add_parser("withdraw", help="take back your held write to edit it and send it again (nothing is sent)")
    s.add_argument("id", help="the hold's id, from the held answer")
    s.set_defaults(fn="rules withdraw")
