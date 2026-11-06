"""Platform commands: computers, credentials, slack, support, finance, task manager, maintenance, roadmap and service keys."""

import argparse
from clients.hubcli_core import (
    _finance_payload,
)


def add_platform(sub):
    comp = sub.add_parser("computer", help="the computers a bot may go on").add_subparsers(dest="sub")
    s = comp.add_parser("list", help="the computers a bot may go on, one short row each; --computer for one in full")
    s.add_argument("--computer", help="one computer's label or id: its full detail, large sections capped")
    s.add_argument(
        "--section",
        choices=["assigned", "repositories", "bots", "runtimes", "harnesses", "recent_errors"],
        help="with --computer: one page of this section only",
    )
    s.add_argument("--offset", type=int, help="with --section: the first item to return")
    s.set_defaults(fn="computer list")
    s = comp.add_parser("performance", help="a computer's CPU, memory and disk over time, overloads and what ran then")
    s.add_argument("computer", help="the computer's label or id")
    s.add_argument("--hours", type=int, default=24)
    s.set_defaults(fn="computer performance")
    s = comp.add_parser(
        "turns-at-once", help="set how many turns a computer runs at once: N, or automatic for its own capacity"
    )
    s.add_argument("computer", help="the computer's id")
    s.add_argument("limit", type=lambda v: None if v == "automatic" else int(v), help="N (1 to 256), or automatic")
    s.set_defaults(fn="computer turns-at-once")
    s = comp.add_parser("browser-exit", help="read or set whether a computer shares its internet with bots: on or off")
    s.add_argument("computer", help="the computer's id")
    s.add_argument("sharing", nargs="?", choices=["on", "off"], default=argparse.SUPPRESS)
    s.add_argument(
        "--bots",
        type=lambda v: [b.strip() for b in v.split(",") if b.strip()],
        help="comma-separated bot slugs it shares with",
    )
    s.add_argument("--cap-gb", dest="daily_cap_gb", type=float, help="the daily cap in GB (default 5)")
    s.add_argument(
        "--usage", action="store_true", default=None, help="also show the last 7 days of what went through it"
    )
    s.set_defaults(fn="computer browser-exit")
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
    support = sub.add_parser("support", help="tell the Tico team about a gap or a fault").add_subparsers(dest="sub")
    s = support.add_parser("file", help="a Confirm card shows the message; nothing is sent until the person confirms")
    s.add_argument("message")
    s.set_defaults(fn="support file")
    s = support.add_parser(
        "report", help="send a Tico bug report (a person approves it first, unless the team lets bots send)"
    )
    text = s.add_mutually_exclusive_group(required=True)
    text.add_argument("--message", help="the whole report: what failed, since when, the exact error, what you tried")
    text.add_argument("--file", help="a file holding the report (UTF-8)")
    s.add_argument(
        "--no-ids", dest="no_ids", action="store_true", help="send only the message: not the version and install ID"
    )
    s.set_defaults(fn="support report")
    s = support.add_parser("send", help="send a report a person approved; filed as the person who approved it")
    s.add_argument("--approval", required=True, help="the approval id `support report` printed")
    s.set_defaults(fn="support send")
    fin = sub.add_parser(
        "finance", help="the books, as you: search, reports, metrics, actions, your work queue (docs/finance-tools.md)"
    ).add_subparsers(dest="sub")
    s = fin.add_parser("search", help="entries, bank and card rows, bills, invoices, contacts and rules you may see")
    s.add_argument("--kind", choices=["entry", "feed", "bill", "invoice", "contact", "rule", "card", "account"])
    s.add_argument("--q")
    for flag in (
        "--status",
        "--contact-id",
        "--vendor",
        "--account-id",
        "--money-account-id",
        "--start",
        "--end",
        "--cursor",
    ):
        s.add_argument(flag)
    s.add_argument("--aging", choices=["current", "1_30", "31_60", "61_90", "over_90"])
    s.add_argument("--contact-kind", choices=["vendor", "customer", "both"])
    s.add_argument("--rule-kind", choices=["gate", "posting"])
    s.add_argument("--active", type=lambda v: v.lower() in ("1", "true", "yes", "on"), help="contacts: true or false")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="finance search")
    s = fin.add_parser("report", help="one report cut to your view; rows past --top-n fold into Other")
    s.add_argument(
        "kind",
        choices=[
            "pnl",
            "balance_sheet",
            "cash_flow",
            "budget_vs_actual",
            "ap_aging",
            "ar_aging",
            "spend_by",
            "close",
            "sales_tax",
            "1099",
        ],
    )
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--year", type=int, help="sales_tax, 1099: the calendar year (this year when omitted)")
    s.add_argument("--period", choices=["day", "week", "month", "quarter", "year"])
    s.add_argument("--basis", choices=["cash", "accrual"])
    s.add_argument("--compare", choices=["prior"])
    s.add_argument("--top-n", type=int)
    s.add_argument("--by", choices=["department", "team", "teammate", "bot", "person", "vendor", "account"])
    s.add_argument("--month", help="close: the month, YYYY-MM")
    s.set_defaults(fn="finance report")
    s = fin.add_parser("metric", help="one number you may see, with when it was worked out")
    s.add_argument(
        "name",
        choices=[
            "cash",
            "burn",
            "runway_months",
            "revenue",
            "spend",
            "budget_remaining",
            "budget_pct_used",
            "ap_due",
            "ar_overdue",
            "uncategorized_count",
            "unreconciled_days",
            "duplicate_charges",
            "price_increases",
        ],
    )
    for flag in ("--budget-id", "--start", "--end", "--account-id", "--team", "--teammate"):
        s.add_argument(flag)
    s.add_argument("--days", type=int)
    s.set_defaults(fn="finance metric")
    s = fin.add_parser("act", help="ask for one Finance action (help lists kinds)")
    s.add_argument("kind")
    s.add_argument("--payload", type=_finance_payload, help="the kind's fields as JSON, or - to read stdin")
    s.add_argument("--dry-run", action="store_true", default=None)
    s.add_argument("--operation-id", help="the action's key (default: HUB_OPERATION_ID)")
    s.set_defaults(fn="finance act")
    s = fin.add_parser("action", help="an action's status and result")
    s.add_argument("action_id")
    s.set_defaults(fn="finance action")
    s = fin.add_parser("inbox", help="your Finance work queue")
    s.add_argument(
        "--kind",
        choices=[
            "uncategorized",
            "bill_to_enter",
            "unmatched_receipt",
            "missing_receipt",
            "bill_due",
            "invoice_overdue",
            "budget_near_limit",
            "sync_failed",
            "period_ready",
            "unreconciled",
            "bill_email",
        ],
        help="only this kind, paged on with --cursor past its first 200",
    )
    s.add_argument("--cursor")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="finance inbox")
    s = fin.add_parser("card-fill", help="fill your card into a checkout (not available yet)")
    s.add_argument("card_id", nargs="?", help="your card")
    s.add_argument("--merchant", help="the checkout's domain")
    s.add_argument("--amount", type=int, help="minor units")
    s.set_defaults(fn="finance card-fill")
    manager = sub.add_parser(
        "task-manager", help="the Task Manager's hourly department check (docs/task-manager.md)"
    ).add_subparsers(dest="sub")
    manager.add_parser(
        "status", help="bot, report switch and schedule, last run, each department's last check"
    ).set_defaults(fn="task-manager status")
    manager.add_parser("on", help="switch the hourly report on").set_defaults(fn="task-manager on")
    manager.add_parser("off", help="switch the hourly report off").set_defaults(fn="task-manager off")
    manager.add_parser("run", help="run the department check now").set_defaults(fn="task-manager run")
    s = manager.add_parser("schedule", help="pick when the report runs")
    s.add_argument("preset", choices=["business", "hourly", "4h", "daily"])
    s.add_argument("--at", help="HH:MM for the daily preset")
    s.set_defaults(fn="task-manager schedule")
    s = manager.add_parser("bot", help="turn the Task Manager bot itself on or off")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(fn="task-manager bot")
    maintenance = sub.add_parser(
        "maintenance", help="Maintenance: the scheduled steps, their switches and runs (docs/maintenance.md)"
    ).add_subparsers(dest="sub")
    maintenance.add_parser(
        "status", help="schedule, pause, monthly judge cap, and each step's switch, frequency and last run"
    ).set_defaults(fn="maintenance")
    STEPS = ["collect", "clean_up", "check_health", "find_stuck", "learn", "suggest", "update", "dream"]
    s = maintenance.add_parser(
        "set", help="change the schedule, the pause, the monthly judge cap, or a step's switch and limits"
    )
    s.add_argument("--schedule", choices=["hourly", "2h", "3h", "6h", "12h", "daily"], help="how often the run starts")
    pause = s.add_mutually_exclusive_group()
    pause.add_argument("--pause", dest="paused", action="store_true", default=None, help="pause the schedule")
    pause.add_argument("--resume", dest="paused", action="store_false", help="resume the schedule")
    s.add_argument("--cap", dest="monthly_cap_usd", type=float, help="monthly judge spend cap, in dollars")
    s.add_argument("--step", choices=STEPS, help="the step the switch, frequency or limits change")
    on = s.add_mutually_exclusive_group()
    on.add_argument("--on", dest="enabled", action="store_true", default=None, help="switch the step on")
    on.add_argument("--off", dest="enabled", action="store_false", help="switch the step off")
    s.add_argument(
        "--frequency", choices=["run", "day", "week", "custom"], help="run = every run; day; week; or custom"
    )
    s.add_argument("--every", dest="custom_every", type=int, help="custom: every N")
    s.add_argument("--unit", dest="custom_unit", choices=["hours", "days"], help="custom: hours or days")
    s.add_argument("--max-bots", dest="max_bots", type=int, help="find_stuck and dream: most bots per run")
    s.set_defaults(fn="maintenance set")
    s = maintenance.add_parser("run", help="run now: all steps, or one with --step (the owner or an admin)")
    s.add_argument("--step", choices=STEPS)
    s.set_defaults(fn="maintenance run")
    s = maintenance.add_parser("runs", help="recent runs with each step's status")
    s.add_argument("--limit", type=int)
    s.add_argument("--before", help="the next cursor from the page before")
    s.set_defaults(fn="maintenance runs")
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
        help="suggestions go to the Automatic bot (Product Manager, then Head of Product)",
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
