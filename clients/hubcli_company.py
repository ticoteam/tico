"""Company commands: team, seats, org, health, changelog, update, external, calendar, sql, db, classify and gateway."""

from clients.hubcli_core import (
    ASKED,
)


def add_company(sub):
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
    seat = sub.add_parser("seat", help="Recruiting seats: roles on the chart with no bot yet").add_subparsers(
        dest="sub"
    )
    seat.add_parser("list", help="the Recruiting seats").set_defaults(fn="seat list")
    s = seat.add_parser("create", help="add a seat under you or someone below you (a person)")
    s.add_argument("title")
    s.add_argument("--description", help="its job description")
    s.add_argument("--reports-to", dest="reports_to", help="a bot's slug or human:<id>; you by default")
    s.add_argument("--team", help="a group id")
    s.set_defaults(fn="seat create")
    s = seat.add_parser("update", help="change a seat's title, job description, reporting line or group (a person)")
    s.add_argument("seat")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--reports-to", dest="reports_to")
    s.add_argument("--team")
    s.set_defaults(fn="seat update")
    s = seat.add_parser("close", help="take a seat off the chart (a person)")
    s.add_argument("seat")
    s.set_defaults(fn="seat close")
    org = sub.add_parser("org", help="the order of the team chart").add_subparsers(dest="sub")
    s = org.add_parser("order", help="a manager's direct reports in the chart's order; --set puts them in an order")
    s.add_argument("manager", help="a bot's slug or a human's id (human:<id> when one could be either)")
    s.add_argument(
        "--set",
        type=lambda text: [x.strip() for x in text.split(",") if x.strip()],
        metavar="A,B,C",
        help="its reports in the new order, comma-separated; the ones left out keep their places",
    )
    s.set_defaults(fn="org order")
    s = org.add_parser("reports-to", help="change who a teammate reports to, as the person who asked")
    s.add_argument("teammate", help="bot:<slug> or human:<id>")
    s.add_argument("reports_to", metavar="manager", help="a bot's slug or human:<id>")
    s.add_argument("--on-behalf-of", metavar="MESSAGE_ID", help=ASKED)
    s.set_defaults(fn="org reports-to")
    org.add_parser("models", help="the published starter org chart models that recommendations come from").set_defaults(
        fn="org models"
    )
    health = sub.add_parser("health", help="what is wrong, and where").add_subparsers(dest="sub")
    s = health.add_parser("check", help="what is wrong with the bots, most urgent first, each with its fix")
    s.set_defaults(fn="health check")
    s = health.add_parser("metrics", help="the server's flight recorder: requests, CPU, loop lag, SQL, database size")
    s.add_argument("--minutes", type=int)
    s.add_argument("--section", help="requests, slow, process, sql, db or events; comma-separated")
    s.set_defaults(fn="health metrics")
    health.add_parser(
        "findings", help="what Check health found on this install, each problem with its report"
    ).set_defaults(fn="health findings")
    s = health.add_parser(
        "report", help="report a finding as a Tico bug: one ticket per problem, with the redacted diagnostics"
    )
    s.add_argument("key", nargs="?", help="the finding's key or signature, from its task")
    s.set_defaults(fn="health report")
    changelog = sub.add_parser("changelog", help="shipped product changes and your own read state").add_subparsers(
        dest="sub"
    )
    s = changelog.add_parser("list", help="what changed; --unread catches up since your last look")
    s.add_argument("--kind", choices=["product", "activity", "all"], default="product")
    s.add_argument("--unread", action="store_true")
    s.add_argument("--since-version", dest="since_version")
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
    s.add_argument("--file", required=True, help="the JSON body hub_grokbot_sync takes")
    s.set_defaults(fn="grokbot sync")
    ext = sub.add_parser(
        "external", help="your bots on another platform in Tico (docs/external-agent-sync.md)"
    ).add_subparsers(dest="sub")
    s = ext.add_parser("sync", help="sync Grok Bots or Dots from a JSON file: {\"bots\": [...], \"source\": ...}")
    s.add_argument("--provider", required=True, choices=("grokbot", "dots", "muse"))
    s.add_argument("--file", required=True, help="the JSON body hub_external_sync takes")
    s.set_defaults(fn="external sync")

    calendar = sub.add_parser("calendar", help="read or schedule company appointments").add_subparsers(dest="sub")
    s = calendar.add_parser("list", help="appointments on a company calendar")
    s.add_argument("--calendar", default="", help="roster email; default the company owner's")
    s.set_defaults(fn="calendar list")
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
    gateway = sub.add_parser(
        "gateway", help="the Tico AI Gateway: its lineup, web search and page reading"
    ).add_subparsers(dest="sub")
    s = gateway.add_parser("catalog", help="models, decision levels and services, with when to use each")
    s.add_argument("query", nargs="?", help="words to match, e.g. 'engineering high' or 'search'")
    s.set_defaults(fn="gateway catalog")
    s = gateway.add_parser("search", help="search the web through the Gateway")
    s.add_argument("query")
    s.add_argument("--max", dest="max_results", type=int, help="how many results (5 by default)")
    s.set_defaults(fn="gateway search")
    s = gateway.add_parser("fetch", help="read a web page or PDF through the Gateway")
    s.add_argument("url")
    s.add_argument(
        "--max-tokens", dest="max_tokens", type=int, help="how much to return, 1,000 to 50,000 (10,000 by default)"
    )
    s.set_defaults(fn="gateway fetch")
    s = gateway.add_parser("image", help="make an image through the Gateway and save it to a file")
    s.add_argument("prompt")
    s.add_argument(
        "--category",
        required=True,
        choices=("storyboard", "photo", "character", "graphic"),
        help="storyboard (fast, keeps references), photo, character (keeps people from --ref), graphic (text and layout)",
    )
    s.add_argument(
        "--ref",
        dest="refs",
        action="append",
        default=[],
        metavar="FILE",
        help="a reference image (PNG, JPEG or WebP, 2 MiB at most); up to 4",
    )
    s.add_argument("--size", help="WxH, for example 1920x1080 (the nearest aspect ratio is asked for)")
    s.add_argument("--count", type=int, help="how many images, 1 to 4 (1 by default)")
    s.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="the file for the first image; more are saved as name-2.png, name-3.png...",
    )
    s.set_defaults(fn="gateway image")
