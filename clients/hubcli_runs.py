"""Run commands: calls, sleep, notices, settings, releases, approvals, webhooks, pipelines, briefs, mcp, origin, needs-you and run."""

import argparse
from clients.hubcli_core import (
    APPROVAL_KINDS,
    ASKED,
    on_off,
    quiet_hours,
)


def add_runs(sub):
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
    sleep = sub.add_parser("sleep", help="Sleep when idle: a hosted server stops after quiet hours").add_subparsers(
        dest="sub"
    )
    s = sleep.add_parser("status", help="hosted or not, the hours, what keeps the server up and Tico's last answer")
    s.set_defaults(fn="sleep status")
    s = sleep.add_parser("set", help="turn it on (1-24 quiet hours) or off (0); an owner or an admin")
    s.add_argument("--hours", type=quiet_hours, metavar="N", help="quiet hours before it sleeps; 0 turns it off")
    s.add_argument(
        "--runners", type=on_off, metavar="on|off", help="stop the bot computers with the server (default on)"
    )
    s.set_defaults(fn="sleep set")

    notice = sub.add_parser("notice", help="notices from Tico Team").add_subparsers(dest="sub")
    s = notice.add_parser("list", help="the notices Tico Team sent, newest first")
    s.set_defaults(fn="notice list")

    catalogue = sub.add_parser(
        "settings", help="find, read and change a control in Settings by its words (docs/permissions.md)"
    ).add_subparsers(dest="sub")
    s = catalogue.add_parser("find", help="settings whose name, place or key hold these words")
    s.add_argument("words")
    s.set_defaults(fn="settings find")
    for leaf, text in (
        ("show", "one setting: its value, who may change it and its card"),
        ("set", "change one setting as yourself, or BotOps or the Assistant as the person who asked"),
        ("ask", "a setting you may not change: ask one person who may, with its Confirm card"),
    ):
        s = catalogue.add_parser(leaf, help=text)
        s.add_argument("key", help="the key `hub settings find` gives, e.g. team.rules.admin_sql")
        if leaf in ("set", "ask"):
            s.add_argument("value", help="on/off, a number, text, a comma-separated list or JSON")
            s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
        if leaf == "ask":
            s.add_argument("--to", help="the person's id to ask; default an admin who may (the owner for the owner's)")
            s.add_argument("--note", help="a line for them from you, shown on the request")
        target = s.add_mutually_exclusive_group()
        target.add_argument("--bot", help="the bot, for a setting set per bot")
        target.add_argument("--computer", help="the computer, for a setting set per computer")
        target.add_argument("--id", help="the one thing it sets: a trigger, webhook, rule, routine, repository...")
        target.add_argument("--person", help="the person's id, for a setting set per person")
        s.set_defaults(fn="settings " + leaf)
    release = sub.add_parser(
        "release", help="the team's releases: ask for one now, or set the daily hour"
    ).add_subparsers(dest="sub")
    s = release.add_parser(
        "now",
        help="ask the Release Manager to release what is at Pending Release now (hotfix, "
        "midday release); one task listing the tickets",
    )
    s.add_argument(
        "--note", help="why now; goes in the task; a person's note asks even with nothing at Pending Release"
    )
    s.set_defaults(fn="release now")
    s = release.add_parser("schedule", help="show the daily release, or change it")
    on = s.add_mutually_exclusive_group()
    on.add_argument("--on", dest="on", action="store_true", default=None, help="turn the daily release on")
    on.add_argument("--off", dest="on", action="store_false", help="turn the daily release off")
    s.add_argument(
        "--time",
        dest="times",
        action="append",
        metavar="HH:MM",
        help="a release time in company time; give it twice for two (replaces the times)",
    )
    s.add_argument("--manager", dest="bot", help="the Release Manager's bot slug; empty finds it by starter template")
    s.add_argument("--writer", help="the Technical Writer's bot slug; empty finds it by starter template")
    s.set_defaults(fn="release schedule")

    appr = sub.add_parser("approval", aliases=["approvals"]).add_subparsers(dest="sub")
    s = appr.add_parser(
        "request", help="ask for a yes; if the owner already said send, mail send --approve <their message id>"
    )
    s.add_argument("--kind", required=True, choices=list(APPROVAL_KINDS))
    s.add_argument("--payload-file", dest="payload_file")
    s.add_argument("--payload")
    s.add_argument("--task")
    s.add_argument(
        "--supersedes", metavar="APPROVAL_ID", help="your older undecided approval this replaces; it is withdrawn"
    )
    s.set_defaults(fn="approval request")
    s = appr.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="approval show")
    s = appr.add_parser(
        "list", help="the approvals addressed to you: pending (what Needs you counts), or approved or declined"
    )
    s.add_argument("--status", choices=["pending", "approved", "declined"], default="pending")
    s.add_argument("--from", dest="from_actor", metavar="ACTOR", help="only the ones this actor asked for")
    s.add_argument("--before", metavar="DATE", help="only those asked before this date (YYYY-MM-DD)")
    s.add_argument("--since", metavar="DATE", help="only those asked on or after this date (YYYY-MM-DD)")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.set_defaults(fn="approval list")
    s = appr.add_parser("read-back", help="the Assistant: list in the person's chat the approvals it will approve")
    s.add_argument("ids", nargs="+")
    s.add_argument(
        "--note",
        metavar="TEXT",
        default="",
        help="one plain line shown above the list: the real decision you found, by name",
    )
    s.set_defaults(fn="approval read-back")
    s = appr.add_parser("approve", help="approve an approval addressed to you")
    s.add_argument("id")
    s.add_argument("--note", default="")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="approval approve")
    s = appr.add_parser("decline", help="decline an approval addressed to you, with a reason")
    s.add_argument("id")
    s.add_argument("--note", default="")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="approval decline")

    webhook = sub.add_parser(
        "webhook", help="outgoing webhooks: Tico posts events to an address you choose (Tools › Webhooks)"
    ).add_subparsers(dest="sub")
    s = webhook.add_parser("list", help="the endpoints you may see, with 7-day delivered and failed counts")
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="webhook list")
    s = webhook.add_parser(
        "create", help="make an endpoint; the signing secret is printed once. A bot files an approval instead"
    )
    s.add_argument("--name", required=True)
    s.add_argument("--url", required=True, help="an https address")
    s.add_argument("--events", nargs="+", help="event names, groups (task.*) or * (default all)")
    s.add_argument("--rate-per-hour", dest="rate_per_hour", type=int, help="most sends an hour (default 600)")
    s.add_argument(
        "--allow-http",
        dest="allow_http",
        action="store_true",
        default=None,
        help="owner only: plain http to localhost, for development",
    )
    s.add_argument("--departments", nargs="+", help="only events about work in these departments (default all)")
    s.add_argument("--bots", nargs="+", help="only events that involve these bots (default all)")
    s.add_argument(
        "--task-types", dest="task_types", nargs="+", help="only events about tasks of these types (default all)"
    )
    s.add_argument(
        "--header", action="append", help='a header to send, "Name: value" (up to 10; every use replaces the list)'
    )
    s.add_argument(
        "--secret-header",
        dest="secret_header",
        action="append",
        help='like --header, the value is sealed and never shown; "Name" alone keeps the value it has',
    )
    s.set_defaults(fn="webhook create")
    s = webhook.add_parser("show", help="one endpoint and its last attempts")
    s.add_argument("id")
    s.set_defaults(fn="webhook show")
    s = webhook.add_parser("update", help="change an endpoint")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument("--url")
    s.add_argument("--events", nargs="+")
    s.add_argument("--rate-per-hour", dest="rate_per_hour", type=int)
    s.add_argument("--allow-http", dest="allow_http", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument(
        "--expected-revision", dest="expected_revision", type=int, help="the revision you read; needed with --url"
    )
    s.add_argument(
        "--credentials",
        choices=("keep", "clear"),
        help="a new address for another destination: keep sends the saved secret headers there, clear removes them",
    )
    s.add_argument("--departments", nargs="+", help="only events about work in these departments (default all)")
    s.add_argument("--bots", nargs="+", help="only events that involve these bots (default all)")
    s.add_argument(
        "--task-types", dest="task_types", nargs="+", help="only events about tasks of these types (default all)"
    )
    s.add_argument(
        "--header", action="append", help='a header to send, "Name: value" (up to 10; every use replaces the list)'
    )
    s.add_argument(
        "--secret-header",
        dest="secret_header",
        action="append",
        help='like --header, the value is sealed and never shown; "Name" alone keeps the value it has',
    )
    s.add_argument(
        "--no-filter", dest="no_filter", action="store_true", help="send every event the endpoint listens for"
    )
    s.add_argument("--no-headers", dest="no_headers", action="store_true", help="remove every custom header")
    s.set_defaults(fn="webhook update")
    s = webhook.add_parser("rotate", help="a new signing secret, printed once; the old one signs for 24 more hours")
    s.add_argument("id")
    s.set_defaults(fn="webhook rotate")
    s = webhook.add_parser("pause", help="stop sending; queued sends wait")
    s.add_argument("id")
    s.add_argument("--reason")
    s.set_defaults(fn="webhook pause")
    s = webhook.add_parser("resume", help="send again")
    s.add_argument("id")
    s.set_defaults(fn="webhook resume")
    s = webhook.add_parser("delete", help="delete the endpoint and cancel its queued sends")
    s.add_argument("id")
    s.set_defaults(fn="webhook delete")
    s = webhook.add_parser("send", help="send a custom event (custom.<name>) to one endpoint")
    s.add_argument("id")
    s.add_argument("--type", required=True, help="custom.<name>")
    s.add_argument("--data", help="JSON, or @file with the JSON")
    s.add_argument("--subject")
    s.set_defaults(fn="webhook send")
    s = webhook.add_parser("sends", help="an endpoint's send log; --send ID shows one send with its attempts")
    s.add_argument("id")
    s.add_argument("--send")
    s.add_argument("--state", choices=["ready", "sending", "delivered", "failed", "canceled", "withheld"])
    s.add_argument("--limit", type=int)
    s.add_argument("--offset", type=int)
    s.set_defaults(fn="webhook sends")
    s = webhook.add_parser("retry", help="send a failed, canceled or delivered send again now")
    s.add_argument("id")
    s.add_argument("send")
    s.set_defaults(fn="webhook retry")
    s = webhook.add_parser("recover", help="send again every send that gave up since a date (same ids and bodies)")
    s.add_argument("id")
    s.add_argument("--since", required=True, help="a date like 2026-10-20 or a time")
    s.set_defaults(fn="webhook recover")
    s = webhook.add_parser(
        "replay", help="send the events since a date that the endpoint should have had and has no send for"
    )
    s.add_argument("id")
    s.add_argument("--since", required=True, help="a date like 2026-10-20 or a time")
    s.add_argument(
        "--after", help="with --since: continue where a round that said `more` stopped (its next_since and next_after)"
    )
    s.set_defaults(fn="webhook replay")
    s = webhook.add_parser(
        "enable",
        help="turn an endpoint back on; after Tico turned it off for failing, it offers to send again what it held back",
    )
    s.add_argument("id")
    s.set_defaults(fn="webhook enable")
    s = webhook.add_parser("example", help="send an event type's example to one endpoint")
    s.add_argument("id")
    s.add_argument("--type", help="an event name (default custom.example)")
    s.set_defaults(fn="webhook example")

    s = sub.add_parser(
        "pipeline",
        help="a task type's steps (count, age, owners, whom they wait on, open PRs), each bot's "
        "running, queued, held and dead jobs, failed runs by cause, and the engineering manager's exceptions",
    )
    s.add_argument("--type", help="task type id or name; default the engineering board")
    s.add_argument("--owner")
    s.add_argument("--label")
    s.add_argument("--updated-since", help="ISO date-time or a window like 7d")
    s.add_argument("--snoozed", choices=["all", "hide", "only"], help="default all")
    s.add_argument("--bot", help="queues and failures for this bot only")
    s.add_argument("--since", help="failure window: 24h (default), up to 7d, or an ISO date-time")
    s.set_defaults(fn="pipeline")
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
    s.add_argument("--decision", choices=["approve", "decline", "done", "close", "answer"])
    s.add_argument("--item", type=int, help="another item's number")
    s.add_argument("--until", help="with later: an ISO date-time")
    s.set_defaults(fn="needs-you respond")
    s = bt.add_parser("commit", help="apply every recorded response")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you commit")
    s = bt.add_parser("abandon", help="drop the batch without applying anything")
    s.add_argument("batch")
    s.set_defaults(fn="needs-you abandon")
    s = bt.add_parser("show", help="what needs you, counted as the board counts it: per kind and per bot")
    s.add_argument(
        "--by", choices=["bot", "kind"], default="bot", help="print the counts per bot (default) or per kind"
    )
    s.add_argument("--limit", type=int, help="the newest items listed under each bot (default 5, at most 10)")
    s.set_defaults(fn="needs-you show")

    runs = sub.add_parser("run", help="a bot's runs").add_subparsers(dest="sub")
    s = runs.add_parser("list", help="a bot's recent runs")
    s.add_argument("bot")
    s.add_argument("--since")
    s.set_defaults(fn="run list")
    s = runs.add_parser("export", help="a run's captured provider stream (owners and engineering bots)")
    s.add_argument("run", help="the run id, from hub run list")
    s.add_argument(
        "--fixture",
        action="store_true",
        help="write input.json, <provider>_transcript.ndjson and output.json to a folder",
    )
    s.add_argument("--out", help="with --fixture: the parent folder (default: the current one)")
    s.set_defaults(fn="run export")
