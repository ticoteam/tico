"""Automation commands: automations, audit, event types, triggers and relays."""

from clients.hubcli_core import (
    on_off,
)


def add_automation(sub):
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
    trigger = sub.add_parser(
        "trigger", help="webhooks that start automations (Automations › Incoming webhooks)"
    ).add_subparsers(dest="sub")
    s = trigger.add_parser("list")
    s.set_defaults(fn="trigger list")
    s = trigger.add_parser("create", help="make a webhook trigger; the address with its secret is shown once")
    s.add_argument("name")
    s.add_argument("--id", help="lowercase letters, digits and dashes; made from the name by default")
    s.set_defaults(fn="trigger create")
    s = trigger.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn="trigger show")
    s = trigger.add_parser("update", help="change a trigger's name or settings")
    s.add_argument("id")
    s.add_argument("--name")
    s.add_argument(
        "--preset",
        choices=["none", "hmac", "github", "standard", "stripe", "slack"],
        help="how calls are verified (needs a signing secret)",
    )
    s.add_argument(
        "--type-from", dest="type_from", help="a header name or a JSON pointer (/type); empty for the preset's default"
    )
    s.add_argument("--hourly", type=int, help="calls an hour, 1-600 (60 by default)")
    s.add_argument(
        "--keep-days",
        dest="keep_days",
        type=int,
        help="days calls stay readable and can run again, 1-30 (7 by default)",
    )
    s.add_argument(
        "--unheard",
        choices=["log", "note"],
        help="note: tell the maker, at most hourly, when no automation or rule acted",
    )
    s.set_defaults(fn="trigger update")
    s = trigger.add_parser("rotate", help="a new secret; the old address stops working")
    s.add_argument("id")
    s.set_defaults(fn="trigger rotate")
    s = trigger.add_parser("sign", help="verify each call with a preset; the signing secret is shown once")
    s.add_argument("id")
    s.add_argument(
        "--preset", choices=["hmac", "github", "standard", "stripe", "slack"], help="the current one, else hmac"
    )
    s.add_argument("--secret", help="the sender's own secret (16+ characters); a new one is made when left out")
    s.add_argument("--off", action="store_true", help="stop checking signatures")
    s.set_defaults(fn="trigger sign")
    s = trigger.add_parser("delete")
    s.add_argument("id")
    s.set_defaults(fn="trigger delete")
    s = trigger.add_parser("deliveries", help="the latest calls to a trigger")
    s.add_argument("id")
    s.set_defaults(fn="trigger deliveries")
    s = trigger.add_parser("delivery", help="one stored call: redacted headers and the body (long bodies in parts)")
    s.add_argument("id")
    s.add_argument("delivery")
    s.add_argument("--part", type=int, help="which 20,000-character part of a long body (from 1)")
    s.set_defaults(fn="trigger delivery")
    s = trigger.add_parser("replay", help="run a stored call again as a new occurrence")
    s.add_argument("id")
    s.add_argument("delivery")
    s.set_defaults(fn="trigger replay")
    s = trigger.add_parser(
        "recover",
        help="run again, under the current secret and preset, every call refused in the last "
        "hours whose body is kept; a sender's later retry of one is a duplicate",
    )
    s.add_argument("id")
    s.add_argument("--since", type=int, default=24, help="hours back, 1-720 (24 by default)")
    s.set_defaults(fn="trigger recover")
    relay = trigger.add_parser(
        "relay", help="a second address through Tico Gateway, so nothing is lost while this server is off"
    ).add_subparsers(dest="subsub")
    for word, text in (
        ("create", "make the address; its secret is shown once"),
        ("rotate", "replace the secret; the old address stops at once"),
        ("delete", "remove the address; the trigger and its own address stay"),
    ):
        s = relay.add_parser(word, help=text)
        s.add_argument("trigger", help="the trigger id from `hub trigger list`")
        s.set_defaults(fn="trigger relay " + word)
    relay_top = sub.add_parser("relay", help="webhooks through Tico Gateway").add_subparsers(dest="sub")
    s = relay_top.add_parser(
        "status", help="the Gateway key, the addresses, the last pull and what waits at the Gateway"
    )
    s.set_defaults(fn="relay status")
    s = relay_top.add_parser("set", help="whether a delivery to a trigger's Gateway address starts a sleeping server")
    s.add_argument("trigger", help="the trigger id from `hub trigger list`")
    s.add_argument(
        "--wake", type=on_off, metavar="on|off", required=True, help="start the server on a delivery (on) or not (off)"
    )
    s.set_defaults(fn="relay set")
    wake_url = relay_top.add_parser(
        "wake-url", help="an address Tico Gateway knocks on to start a server you run when a webhook waits for it"
    ).add_subparsers(dest="subsub")
    wake_url.add_parser("show", help="the address, who set it and when").set_defaults(fn="relay wake-url show")
    wake_url.add_parser("off", help="stop knocking").set_defaults(fn="relay wake-url off")
    s = wake_url.add_parser("set", help="set the address; the signing secret is shown once")
    s.add_argument("url", help="an https address you run")
    s.set_defaults(fn="relay wake-url set")
