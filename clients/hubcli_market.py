"""Market commands: market, listening and tools."""


def add_market(sub):
    market = sub.add_parser(
        "market", help="the shared market graph: read it, report into it, curate it"
    ).add_subparsers(dest="sub")
    s = market.add_parser("show", help="one entity, its edges both ways, the evidence, the last ten events")
    s.add_argument("id")
    s.set_defaults(fn="market show")
    s = market.add_parser("find", help="names, aliases, summaries, and evidence")
    s.add_argument("text")
    s.set_defaults(fn="market find")
    s = market.add_parser("edges", help="graph rows; symmetric relations are returned from either end")
    s.add_argument("--from", dest="src")
    s.add_argument("--to", dest="dst")
    s.add_argument("--rel")
    s.add_argument("--as-of", dest="as_of")
    s.set_defaults(fn="market edges")
    s = market.add_parser("delta", help="what changed, grouped by entity")
    s.add_argument("--since", default="7d")
    s.set_defaults(fn="market delta")
    s = market.add_parser("ask", help="a question answered from the graph, with citations")
    s.add_argument("question")
    s.set_defaults(fn="market ask")
    s = market.add_parser("report", help="prose only; this does not change the graph")
    s.add_argument(
        "--kind", required=True, choices=["new-entity", "edge", "property-change", "correction", "question", "other"]
    )
    s.add_argument("--about", default="")
    s.add_argument("--claim", required=True)
    s.add_argument("--source", default="")
    s.add_argument("--quote", default="")
    s.add_argument("--confidence", default="medium", choices=["high", "medium", "low"])
    s.add_argument("--urgent", action="store_true")
    s.add_argument("--source-ref", dest="source_ref", help="where it came from, e.g. the intake item id")
    s.set_defaults(fn="market report")
    s = market.add_parser("resolve", help="close an insight: applied, merged, rejected, or needs-human")
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=["applied", "merged", "rejected", "needs-human"])
    s.add_argument("--resolution", default="")
    s.add_argument("--event", action="append", dest="events", default=[])
    s.set_defaults(fn="market resolve")
    s = market.add_parser("apply", help="write evidence first, then the entity or edge, and mark the insight applied")
    s.add_argument("id")
    s.add_argument("--source", default="")
    s.add_argument("--source-kind", dest="source_kind", default="other")
    s.add_argument("--quote", default="")
    s.add_argument("--our-read", dest="our_read", default="")
    s.add_argument("--entity-type", dest="entity_type")
    s.add_argument("--entity-name", dest="entity_name")
    s.add_argument("--entity-id", dest="entity_id")
    s.add_argument("--summary")
    s.add_argument("--tier", choices=["core", "lookalike", "phrase-stealer", "secondary"], help="for a new company")
    s.add_argument(
        "--new-id",
        dest="new_id",
        help="the id for a new entity when the default type/name-slug is wrong, e.g. company/self",
    )
    s.add_argument(
        "--last-verified-at",
        dest="last_verified_at",
        metavar="YYYY-MM-DD",
        help="the date the entity was last checked: set on --entity-id, or on a new entity; YYYY-MM-DD only, a timestamp is refused",
    )
    s.add_argument("--edge-src", dest="edge_src")
    s.add_argument("--edge-rel", dest="edge_rel")
    s.add_argument("--edge-dst", dest="edge_dst")
    s.set_defaults(fn="market apply")
    s = market.add_parser(
        "import", help="apply a verified batch: one JSON entity or edge per line, each with its evidence"
    )
    s.add_argument("file", help="the JSONL file, at most 2000 non-blank lines (split larger ones); - reads stdin")
    s.add_argument("--dry-run", dest="preview", action="store_true", help="report what would change and keep nothing")
    s.add_argument("--note", default="", help="one sentence on where the batch came from")
    s.set_defaults(fn="market import")
    s = market.add_parser("sweep", help="one owner task for needs-human, listening tasks for unverified stale entities")
    s.add_argument("--today")
    s.add_argument("--unverified", action="append", default=[], help="entity-id=what to look for")
    s.set_defaults(fn="market sweep")
    s = market.add_parser("refresh", help="rewrite the weekly delta from market events; only a Monday changes it")
    s.add_argument("--today")
    s.set_defaults(fn="market refresh")
    s = market.add_parser(
        "page", help="read one market page (overview, coverage-universe, ...), or rewrite it from a Markdown file"
    )
    s.add_argument(
        "name",
        help="overview, structure-and-size, coverage-universe, people-who-matter, channels, "
        "regulation-and-catalysts, theses, open-questions or customer-themes",
    )
    s.add_argument(
        "--body-file", dest="body_file", help="the whole page in Markdown; - reads stdin; omit to read the page"
    )
    s.set_defaults(fn="market page")
    watch = market.add_parser(
        "watch", help="watch a competitor's public page; the server reads it daily and files an insight on a change"
    ).add_subparsers(dest="subsub")
    s = watch.add_parser("add", help="watch one page of an entity")
    s.add_argument("entity_id")
    s.add_argument("url")
    s.add_argument("--kind", default="other", choices=["pricing", "careers", "changelog", "docs", "home", "other"])
    s.set_defaults(fn="market watch add")
    s = watch.add_parser("list", help="the watched pages and when each was last read")
    s.add_argument("--entity", dest="entity_id")
    s.set_defaults(fn="market watch list")
    s = watch.add_parser("stop", help="stop watching a page (the row is kept)")
    s.add_argument("id")
    s.set_defaults(fn="market watch stop")
    s = watch.add_parser("check", help="read the watched pages now (the longest unchecked first, at most 50)")
    s.set_defaults(fn="market watch check")
    briefing = market.add_parser("briefing", help="the weekly market briefing").add_subparsers(dest="subsub")
    s = briefing.add_parser("post", help="post the week's briefing; the same week again replaces it")
    s.add_argument("--week", required=True, help="ISO week, like 2026-W44")
    s.add_argument("--title", required=True)
    s.add_argument("--body-file", dest="body_file", required=True, help="the briefing in Markdown; - reads stdin")
    s.add_argument(
        "--items-file", dest="items_file", help="JSON list of {text, kind: change|signal|theme|size, entity_ids}"
    )
    s.add_argument(
        "--answers-file",
        dest="answers_file",
        help="JSON object of one-line answers: {changed, money, players, reach, saying}",
    )
    s.set_defaults(fn="market briefing post")
    s = briefing.add_parser("latest", help="the newest briefing, its items and the bets it touches")
    s.set_defaults(fn="market briefing latest")
    s = market.add_parser(
        "define", help="Librarian or owner: define the market (what it is, what is in and out, how to count)"
    )
    s.add_argument("--description")
    s.add_argument("--root-name", dest="root_name", help="the root segment's name, the first time")
    s.add_argument(
        "--include", action="append", dest="includes", metavar="TEXT", help="what is in; repeatable, replaces the list"
    )
    s.add_argument(
        "--exclude", action="append", dest="excludes", metavar="TEXT", help="what is out; repeatable, replaces the list"
    )
    s.add_argument(
        "--rule",
        action="append",
        dest="counting_rules",
        metavar="TEXT",
        help="a counting rule; repeatable, replaces the list",
    )
    s.add_argument("--year", type=int)
    s.add_argument("--geography")
    s.add_argument(
        "--param",
        action="append",
        dest="params",
        metavar="NAME=NUMBER",
        help="a shared parameter formulas read as $NAME; repeatable",
    )
    s.set_defaults(fn="market define")
    s = market.add_parser("segment", help="the market's segments: add one, or print the tree").add_subparsers(
        dest="subsub"
    )
    s2 = s.add_parser("add", help="Librarian or owner: a segment under the root, or under --parent")
    s2.add_argument("name")
    s2.add_argument("--parent", dest="parent_id", metavar="SEGMENT_ID")
    s2.add_argument("--summary")
    s2.set_defaults(fn="market segment add")
    s2 = s.add_parser("tree", help="the segments, indented, with their sizes")
    s2.set_defaults(fn="market segment tree")
    s = market.add_parser(
        "size", help="a segment's yearly spend: add an estimate, accept or reject one, list them"
    ).add_subparsers(dest="subsub")
    s2 = s.add_parser("add", help="Librarian or owner: file an estimate, proposed unless --accept")
    s2.add_argument("--segment", dest="segment_id", required=True, metavar="SEGMENT_ID")
    s2.add_argument("--method", required=True, choices=["top_down", "bottom_up", "player_sum", "owner"])
    s2.add_argument("--spend", dest="spend_usd", type=float, metavar="USD", help="omit when --formula gives it")
    s2.add_argument("--low", dest="low_usd", type=float, required=True, metavar="USD")
    s2.add_argument("--high", dest="high_usd", type=float, required=True, metavar="USD")
    s2.add_argument("--formula", help="arithmetic over numbers, --param names and shared $names")
    s2.add_argument("--param", action="append", dest="params", metavar="NAME=NUMBER")
    s2.add_argument("--arithmetic", help="the working, in words")
    s2.add_argument("--growth", dest="growth_pct", type=float, metavar="PERCENT")
    s2.add_argument("--year", type=int)
    s2.add_argument("--dimension", choices=["offering", "buyer"])
    s2.add_argument("--basis", choices=["product", "platform"])
    s2.add_argument("--confidence", choices=["high", "medium", "low"])
    s2.add_argument("--overlaps")
    s2.add_argument("--spread", help="analyst headline figures, as text")
    s2.add_argument("--evidence", action="append", dest="evidence_ids", metavar="EVIDENCE_ID")
    s2.add_argument("--evidence-url", dest="evidence_url")
    s2.add_argument("--quote")
    s2.add_argument("--source-kind", dest="source_kind")
    s2.add_argument("--our-read", dest="our_read")
    s2.add_argument("--accept", action="store_true", default=None)
    s2.set_defaults(fn="market size add")
    for word, help_text in (("accept", "the estimate becomes the segment's size"), ("reject", "the estimate is out")):
        s2 = s.add_parser(word, help=help_text)
        s2.add_argument("id")
        s2.set_defaults(fn="market size decide", decision=word)
    s2 = s.add_parser("params", help="owner only: change an estimate's parameter; files a new accepted owner estimate")
    s2.add_argument("id")
    s2.add_argument("--param", action="append", dest="params", required=True, metavar="NAME=NUMBER")
    s2.set_defaults(fn="market size decide", decision="params")
    s2 = s.add_parser("list", help="the estimates, optionally one segment's")
    s2.add_argument("--segment", metavar="SEGMENT_ID")
    s2.set_defaults(fn="market size list")
    s = market.add_parser("figure", help="a player's revenue, ARR, customers or seats in a segment").add_subparsers(
        dest="subsub"
    )
    s2 = s.add_parser("add", help="Librarian or owner: one dated figure with its evidence")
    s2.add_argument("--entity", dest="entity_id", required=True, metavar="ENTITY_ID")
    s2.add_argument("--segment", dest="segment_id", required=True, metavar="SEGMENT_ID")
    s2.add_argument("--metric", required=True, choices=["revenue", "arr", "customers", "seats"])
    s2.add_argument("--value", type=float)
    s2.add_argument("--text", help="when there is no number")
    s2.add_argument("--kind", required=True, choices=["reported", "estimated", "claimed"])
    s2.add_argument("--as-of", dest="as_of", required=True, metavar="YYYY-MM[-DD]")
    s2.add_argument("--evidence", dest="evidence_id", metavar="EVIDENCE_ID")
    s2.add_argument("--evidence-url", dest="evidence_url")
    s2.add_argument("--quote")
    s2.add_argument("--source-kind", dest="source_kind")
    s2.add_argument("--our-read", dest="our_read")
    s2.set_defaults(fn="market figure add")
    s = market.add_parser("sources", help="where to look when sizing the market: the catalog and the team's additions")
    s.add_argument("--add-file", dest="add_file", help="a YAML file of sources to add (a list, or one)")
    s.set_defaults(fn="market sources")
    s = market.add_parser("spend", help="the market by spend: the tree with sizes (JSON has every estimate)")
    s.add_argument("--dimension", choices=["offering", "buyer"])
    s.add_argument("--basis", choices=["product", "platform"])
    s.add_argument("--year", type=int)
    s.set_defaults(fn="market spend")
    s = market.add_parser("research", help="owner only: ask the Librarian to define and size the market")
    s.add_argument("description")
    s.set_defaults(fn="market research")

    listen = sub.add_parser(
        "listening", help="Listening's saved posts: save a sweep, decide where each post goes, trace a post"
    ).add_subparsers(dest="sub")
    s = listen.add_parser("save", help="one sweep of one query and the posts it saw, from a JSON file")
    s.add_argument(
        "--file", required=True, help="JSON: {source, query, status, note, pages_read, items: [...]}; - for stdin"
    )
    s.set_defaults(fn="listening save")
    s = listen.add_parser(
        "decide", help="put the listening-item decisions to new posts and route them to the message bots"
    )
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--item", action="append", dest="item_ids", default=[])
    s.set_defaults(fn="listening decide")
    s = listen.add_parser("show", help="a post, its run, its decisions, and where it went")
    s.add_argument("id")
    s.set_defaults(fn="listening show")
    s = listen.add_parser("runs", help="sweeps, newest first, with ok/blocked/rate_limited/error")
    s.add_argument("--since")
    s.add_argument("--source")
    s.set_defaults(fn="listening runs")
    s = listen.add_parser("stats", help="coverage by source and precision by message bot")
    s.add_argument("--since")
    s.set_defaults(fn="listening stats")
    item = listen.add_parser(
        "item", help="posts Listening routed to you: list them, then accept or reject each"
    ).add_subparsers(dest="subsub")
    s = item.add_parser("list", help="your posts, oldest first")
    s.add_argument("--destination")
    s.add_argument("--status", default="new", choices=["new", "accepted", "rejected", "duplicate"])
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(fn="listening item list")
    s = item.add_parser(
        "resolve", help="accepted --ref <your record>, duplicate --ref <existing>, or rejected --reason"
    )
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=["accepted", "rejected", "duplicate"])
    s.add_argument("--ref", dest="receiver_ref", default="")
    s.add_argument("--reason", default="")
    s.set_defaults(fn="listening item resolve")

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
    s.add_argument("id", help="the API tool id (`hub tool index`)")
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
    s = tools.add_parser("learn", help='add a learning: hub tool learn <service> "<text>"')
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
    s.add_argument("id", help="the tool id from `hub tool list --bot`")
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
    s.add_argument("id", help="the tool id from `hub tool list --bot`")
    s.set_defaults(fn="tool remove")
