"""Strategy commands: strategy, brand, growth and lens."""


def add_strategy(sub):
    strategy = sub.add_parser(
        "strategy", help="the team's strategy: its bets, its sections, the versions"
    ).add_subparsers(dest="sub")
    s = strategy.add_parser(
        "show", help="the published strategy, its bets and their goals; the owner also gets the draft"
    )
    s.add_argument(
        "--section",
        choices=["serve", "sell", "proof", "now", "know", "how", "not"],
        help="only this section of the published strategy, as text (proof: How we know they want it; "
        "now: Why now; how: How we sell)",
    )
    s.add_argument("--json", action="store_true", help="print the API response as JSON")
    s.set_defaults(fn="strategy show")
    s = strategy.add_parser("summary", help="the text every run's context carries")
    s.set_defaults(fn="strategy summary")
    s = strategy.add_parser(
        "draft", help="owner only: change the draft; a bot proposes instead (proposal create --kind strategy_draft)"
    )
    s.add_argument("--summary")
    s.add_argument("--vision")
    s.add_argument(
        "--section",
        action="append",
        default=[],
        metavar="KEY=TEXT",
        help="serve, sell, proof, now, know, how, not, or an extra section's key; KEY= clears it; repeatable",
    )
    s.add_argument("--revision", type=int, help="the draft's revision you read; default the current one")
    s.set_defaults(fn="strategy draft")
    s = strategy.add_parser("publish", help="owner only: the draft becomes the next version")
    s.add_argument("--note", default="")
    s.add_argument("--revision", type=int, help="the draft's revision you read; default the current one")
    s.set_defaults(fn="strategy publish")
    s = strategy.add_parser("versions", help="the published versions, newest first; with N, that version whole")
    s.add_argument("n", nargs="?", type=int)
    s.set_defaults(fn="strategy versions")
    s = strategy.add_parser("diff", help="what changed between two published versions")
    s.add_argument("--from", dest="from_n", type=int, required=True)
    s.add_argument("--to", dest="to_n", type=int, required=True)
    s.set_defaults(fn="strategy diff")
    s = strategy.add_parser(
        "guides", help="deprecated: the Strategy Bot's drafting questions; the exercises are lenses"
    )
    s.set_defaults(fn="strategy guides")
    bet = strategy.add_parser("bet", help="owner only: add, change or retire a bet").add_subparsers(dest="subsub")

    def bet_flags(s):
        s.add_argument("--why")
        s.add_argument("--rules-out", dest="rules_out")
        s.add_argument(
            "--wrong-if-kpi", dest="wrong_if_kpi", metavar="ID", help="wrong if this KPI crosses the threshold"
        )
        s.add_argument("--wrong-if-direction", dest="wrong_if_direction", choices=["above", "below"])
        s.add_argument("--wrong-if-threshold", dest="wrong_if_threshold", type=float, metavar="N")
        s.add_argument(
            "--wrong-if-thesis",
            dest="wrong_if_thesis",
            metavar="MARKET_ID",
            help="wrong if this market entity or page changes",
        )
        s.add_argument(
            "--wrong-if-text",
            dest="wrong_if_text",
            metavar="TEXT",
            help="wrong if this turns out true (needs --check-by)",
        )
        s.add_argument("--check-by", dest="check_by", metavar="DATE", help="YYYY-MM-DD: the day to look again")
        s.add_argument(
            "--cite",
            action="append",
            dest="cites",
            metavar="MARKET_ID",
            help="a market id the bet rests on; repeatable",
        )
        s.add_argument("--status", choices=["proposed", "active", "retired"])
        s.add_argument("--rank", type=int)

    s = bet.add_parser("add")
    s.add_argument("--statement", required=True, help="one sentence that could turn out wrong")
    bet_flags(s)
    s.set_defaults(fn="strategy bet add")
    s = bet.add_parser("update")
    s.add_argument("id")
    s.add_argument("--statement")
    bet_flags(s)
    s.set_defaults(fn="strategy bet update")
    s = bet.add_parser("retire", help="its goals keep their link")
    s.add_argument("id")
    s.set_defaults(fn="strategy bet retire")

    def json_flag(s):
        s.add_argument("--json", action="store_true", help="print the API response as JSON")
        return s

    brand_sections = ("name", "positioning", "voice", "tone", "words", "naming", "visual", "claims", "examples")
    page = sub.add_parser("brand", help="the brand: voice, words, names, claims").add_subparsers(dest="sub")
    s = json_flag(page.add_parser("show", help="the published page; owner and admins also get the draft"))
    s.add_argument("--section", choices=brand_sections, help="only this section")
    s.add_argument("--version", type=int, metavar="N", help="a published version; default the latest")
    s.set_defaults(fn="brand show")
    s = json_flag(page.add_parser("summary", help="the text a growth-facing bot's turn carries"))
    s.set_defaults(fn="brand summary")
    s = json_flag(page.add_parser("check", help="check a draft against the published brand; - reads stdin"))
    s.add_argument("file", help="a file holding the draft, or - for stdin")
    s.set_defaults(fn="brand check")
    s = json_flag(
        page.add_parser(
            "draft",
            help="owner or admin: change one section of the draft; a bot proposes instead "
            "(proposal create --kind brand_draft)",
        )
    )
    s.add_argument("--section", required=True, choices=brand_sections)
    s.add_argument("--body", required=True, help="the section's value as JSON; null removes it")
    s.add_argument("--revision", type=int, help="the draft's revision you read; stale is refused")
    s.set_defaults(fn="brand draft")
    s = json_flag(page.add_parser("publish", help="owner or admin: the draft becomes the next version"))
    s.add_argument("--note", default="")
    s.set_defaults(fn="brand publish")

    growth = sub.add_parser(
        "growth", help="growth: channels, sources, monthly cohorts, unit economics, the numbers"
    ).add_subparsers(dest="sub")
    channel = growth.add_parser("channel", help="the channels customers come from").add_subparsers(dest="subsub")

    def channel_flags(s):
        s.add_argument("--track", choices=["performance", "brand"])
        s.add_argument("--stage", choices=["idea", "testing", "scaling", "paused"])
        s.add_argument("--owner", help='me, a bot slug or a person id; "" clears')
        s.add_argument("--budget", dest="budget_month", type=float, metavar="N", help="spend per month")
        s.add_argument("--source", dest="source_id", metavar="ID", help="the source that holds its spend")
        s.add_argument("--why")
        s.add_argument("--guardrail-kpi", dest="guardrail_kpi_id", metavar="ID")
        s.add_argument("--guardrail-max", dest="guardrail_max", type=float, metavar="N")
        s.add_argument("--sort", type=int)

    s = json_flag(channel.add_parser("list"))
    s.add_argument("--retired", dest="include_retired", action="store_true", default=None, help="also retired ones")
    s.set_defaults(fn="growth channel list")
    s = json_flag(channel.add_parser("show"))
    s.add_argument("slug")
    s.set_defaults(fn="growth channel show")
    s = json_flag(channel.add_parser("add", help="owner or admin"))
    s.add_argument("slug", help="lowercase, digits and dashes: google-search")
    s.add_argument("--name", required=True)
    channel_flags(s)
    s.set_defaults(fn="growth channel add")
    s = json_flag(channel.add_parser("update", help="owner or admin; the owning bot may change why, stage, budget"))
    s.add_argument("slug")
    s.add_argument("--name")
    channel_flags(s)
    s.set_defaults(fn="growth channel update")
    s = json_flag(channel.add_parser("retire", help="owner or admin"))
    s.add_argument("slug")
    s.set_defaults(fn="growth channel retire")

    source = growth.add_parser("source", help="which system holds the truth for each number").add_subparsers(
        dest="subsub"
    )

    def source_flags(s, required):
        s.add_argument("--truth-for", dest="truth_for", required=required, help="the number: bookings, ad spend")
        s.add_argument("--system", required=required, help="Stripe, Google Ads")
        s.add_argument(
            "--reach-kind", dest="reach_kind", required=required, choices=["mcp", "credential", "url", "person"]
        )
        s.add_argument("--reach-ref", dest="reach_ref", help="the MCP server, credential, URL or person")
        s.add_argument("--may", help="what a bot may do there")
        s.add_argument("--owner", help="me, a bot slug or a person id")

    s = json_flag(source.add_parser("list"))
    s.add_argument("--retired", dest="include_retired", action="store_true", default=None)
    s.set_defaults(fn="growth source list")
    s = json_flag(source.add_parser("add", help="owner or admin"))
    source_flags(s, True)
    s.set_defaults(fn="growth source add")
    s = json_flag(source.add_parser("update", help="owner or admin"))
    s.add_argument("id")
    source_flags(s, False)
    s.set_defaults(fn="growth source update")
    s = json_flag(source.add_parser("retire", help="owner or admin"))
    s.add_argument("id")
    s.set_defaults(fn="growth source retire")

    cohort = growth.add_parser("cohort", help="monthly numbers by channel").add_subparsers(dest="subsub")
    s = json_flag(cohort.add_parser("list"))
    s.add_argument("--channel", metavar="SLUG", help="a channel slug, or unknown")
    s.add_argument("--month", metavar="YYYY-MM")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="growth cohort list")
    s = json_flag(cohort.add_parser("set", help="one channel's month, only the fields given; any bot or an admin"))
    s.add_argument("--channel", required=True, metavar="SLUG", help="a channel slug, or unknown")
    s.add_argument("--month", required=True, metavar="YYYY-MM")
    s.add_argument("--spend", type=float)
    s.add_argument("--bot-cost", dest="bot_cost", type=float)
    s.add_argument("--new", dest="new_customers", type=int, metavar="N", help="new customers")
    s.add_argument("--d30", dest="kept_d30", type=float, metavar="PCT", help="%% kept after 30 days")
    s.add_argument("--d90", dest="kept_d90", type=float, metavar="PCT")
    s.add_argument("--d180", dest="kept_d180", type=float, metavar="PCT")
    s.add_argument("--revenue", dest="revenue_month", type=float, metavar="N", help="per customer per month")
    s.add_argument("--margin", dest="margin_pct", type=float, metavar="PCT")
    s.add_argument("--quality", choices=["actual", "estimate"])
    s.add_argument("--source", action="append", metavar="FIELD=TEXT", help="where a number came from; repeatable")
    s.set_defaults(fn="growth cohort set")
    s = json_flag(growth.add_parser("economics", help="CAC, months kept, LTV and LTV:CAC by channel"))
    s.add_argument("--month", metavar="YYYY-MM")
    s.set_defaults(fn="growth economics")
    s = json_flag(
        growth.add_parser("numbers", help="the monthly series, each channel's economics, the KPI tree, markers")
    )
    s.add_argument("--months", type=int, metavar="N", help="1 to 24; default 12")
    s.set_defaults(fn="growth numbers")
    s = json_flag(growth.add_parser("kpis", help="the KPI tree: North Star, inputs, guardrails"))
    s.set_defaults(fn="growth kpis")
    s = json_flag(growth.add_parser("settings", help="unit, revenue, margin; --set: owner or admin"))
    s.add_argument("--set", action="append", metavar="KEY=VALUE", help="repeatable")
    s.set_defaults(fn="growth settings")

    lens = sub.add_parser("lens", help="thought exercises run against the strategy or the market").add_subparsers(
        dest="sub"
    )
    s = lens.add_parser("list", help="the lenses, with their steps")
    s.add_argument("--market", action="store_true", help="only the market lenses")
    s.set_defaults(fn="lens list")
    s = lens.add_parser("run", help="run a lens; prints the run, waiting up to 60 seconds for its summary")
    s.add_argument("id")
    s.add_argument("--answers-file", dest="answers_file", help='JSON {"step key": answer}; a step left out is assumed')
    s.add_argument("--draft", action="store_true", help="owner only: run on the draft instead of the published version")
    s.add_argument("--no-wait", dest="no_wait", action="store_true", help="print the running run and return")
    s.set_defaults(fn="lens run")
    s = lens.add_parser("show", help="one run: answers, result and changes")
    s.add_argument("id")
    s.set_defaults(fn="lens show")
    s = lens.add_parser("runs", help="runs, newest first")
    s.add_argument("--lens", dest="lens")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn="lens runs")
