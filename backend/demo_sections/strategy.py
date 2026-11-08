"""Strategy: the vision, three bets and a market briefing.

Version 1 is the home business: two bets and the sections for homeowners. Version 2 is published a few days later and
adds the business bet, a new summary and the business sections, so the history has a change to show. The Strategy
Bot's one proposal, a bet the team had not written down yet from this week's briefing, Ana has accepted into the
draft (proposed, unpublished), and this week's
briefing is the Librarian's. Every row goes through the API as Ana, or through the domain function a real install runs.
"""

from datetime import timedelta

from .. import goals as G
from .. import market as MK
from .. import market_briefing as MB
from .. import strategy as S
from .. import strategy_bot
from ..auth import Identity

CONTENT = {
    "vision": "Any building can be a light show, set up in ten minutes.",
    "summary_v1": "Acme Lights sells holiday projectors that turn a home's facade into a light show. Home Starter and "
                  "Home Pro are aimed and calibrated in one pass with our app, and the Scene Library adds new shows each "
                  "season. Homes are the volume and the brand.",
    "summary_v2": "Acme Lights sells holiday projectors that turn a building's facade into a light show. Homes are the "
                  "volume and the brand, with the Scene Library for new shows each season. Business installs are the "
                  "margin: storefronts, hotels, towns and HOAs, with a survey, a partner crew and a yearly scene license.",
    "sections": {
        "serve": "Homeowners who want the house to be the light show on the street, set up themselves over one weekend.\n\n"
                 "We do not sell to a house with no outdoor power on the wall the projector faces. That is a job for an "
                 "electrician first.",
        "sell": "Home Starter at $299 for one unit, and Home Pro at $449 for two. Both kits are sold on their own.\n\n"
                "The Scene Library is sold on top: $4.99 a month, or $29 for a season.\n\n"
                "The price that breaks: a kit that needs a second visit to calibrate costs us a return and a trip. A "
                "lower price does not fix that, so we fix the calibration first.",
        "proof": "Owners come back. Most of last season's owners bought a second scene before New Year, and they bought "
                 "it in our store.\n\n"
                 "The kits that came back were nearly all a scene that landed crooked on the first night. 16 in 100 first "
                 "calibrations still fail, and that is the number we watch.",
        "now": "A phone camera can now map a facade in one pass, which took a technician two years ago. LED projectors got "
               "bright enough to light a two-storey house at $449. And people now expect a holiday show that changes each "
               "season, not one string of lights.",
        "know": "A projector is only as good as its first night. A scene that lands crooked gets the kit returned, and "
                "the owner never buys a second scene. Calibration decides both, and it is where other kits ask for a "
                "second visit or a manual setup. We map the facade in one pass, so the first night is the one people keep.",
        "how": "Our online store, the bigmarket.example marketplace and homeworks.example retail shelves. Creators and "
               "social posts bring the first look, and email brings the renewal.\n\n"
               "Only our store sells the Scene Library, so the store is where we see the customer and offer the renewal.",
        "not": "- We won't sell a kit that needs a second calibration visit to be right.\n"
               "- We won't put the Scene Library on a marketplace or a shelf.\n"
               "- We won't cut the kit price below a retail partner's shelf price.\n"
               "- We won't ship a scene that has not run on a real facade.",
    },
    # Version 2 changes these five sections for the business branch; "know" and "now" stay as they were.
    "sections_v2": {
        "serve": "Homeowners who want the house to be the light show on the street, set up themselves over one weekend.\n\n"
                 "Shops and hotels that want a storefront or a lobby lit for the season. Towns and HOAs that want a street "
                 "or a facade done once, after a survey. Business sites renew their scene license each year.\n\n"
                 "We do not sell to a house with no outdoor power on the wall the projector faces. That is a job for an "
                 "electrician first.",
        "sell": "Home Starter at $299 for one unit, and Home Pro at $449 for two. The Scene Library is sold on top: $4.99 "
                "a month, or $29 for a season.\n\n"
                "Storefront at $1,200 with a yearly scene license, for shops and hotels. Facade is a per-site quote after "
                "a survey, with a partner crew, for malls, towns and HOAs. Renewals are the yearly license and the next "
                "season's scene set.\n\n"
                "The price that breaks: a facade quote below the crew's cost loses money on every site. We quote from the "
                "survey, not from the size of the wall.",
        "proof": "Owners come back. Most of last season's owners bought a second scene before New Year, and they bought "
                 "it in our store.\n\n"
                 "Business asked before we sold it: shop owners wrote in after seeing a house on their street, and the "
                 "three downtown storefronts we lit as a test asked for next season's license before this one ended.\n\n"
                 "16 in 100 first calibrations still fail, and that is the number we watch.",
        "how": "Home kits go through the store, the marketplace and retail, as before.\n\n"
               "Business goes through direct sales for shops, hotels and towns, and through installer and event partners "
               "who run the survey and the crew. Towns and HOAs put their holiday contracts out to public bid, and we bid "
               "when the scope is clear. The account owner sells renewals.",
        "not": "- We won't sell a Storefront or Facade job to a site we have not surveyed.\n"
               "- We won't quote a facade below the crew's cost to win a town.\n"
               "- We won't put the Scene Library on a marketplace or a shelf.\n"
               "- We won't cut the kit price below a retail partner's shelf price.",
    },
    "notes": {"v1": "First strategy: two home bets from the setup answers.",
              "v2": "Business installs are a bet now; the summary and the sections name them."},
    # Each bet serves a goal the core seed made, named by its owner: the company's top goal or a department's.
    "bets": [
        {"key": "calibration", "rank": 0, "goal": "bot:product-manager", "version": 1,
         "statement": "Calibration that works first try is the product; everything else follows.",
         "why": "A homeowner gives the kit one night. If the scene lands crooked, the kit goes back, and a second visit "
                "costs us more than the sale. A kit that maps the facade in one pass is what earns the review and the "
                "next season's scene set. The Scene Library and the bigger kits sell to people who had a good first night.",
         "rules_out": "A kit that needs a second visit to calibrate, and a Scene Library launch before first-night "
                      "calibration is reliable.",
         "wrong_if": "Homes that needed a second calibration renew the Scene Library no less often than homes that got it "
                     "right the first time, over two seasons.",
         "check_days": 240},
        {"key": "business", "rank": 1, "goal": "bot:sales-lead", "version": 2,
         "statement": "Business installs are the margin; homes are the volume and the brand.",
         "why": "One storefront or facade job is worth many home kits, and business sites renew their scene license every "
                "year. Our direct sales team and installer partners can run the survey and the crew, so each site adds "
                "margin without a new team.",
         "rules_out": "A quote for a facade we have not surveyed, and a storefront with no yearly scene license attached.",
         "wrong_if": "Business sites renew their scene license at less than one in two after the first season, over two "
                     "seasons running.",
         "check_days": 300},
        {"key": "marketplace", "rank": 2, "goal": "company", "version": 1,
         "statement": "Marketplace and retail for reach; our own store for the Scene Library subscription.",
         "why": "Most first-time buyers find a projector on a marketplace or on a shelf, not on our site, so those channels "
                "bring the volume. The subscription is sold only on our store, where we see the customer and can offer the "
                "renewal. The store is where the margin is.",
         "rules_out": "Selling the Scene Library through a marketplace or a retailer, and a marketplace price below the "
                      "shelf price our retail partners pay.",
         "wrong_if": "Marketplace buyers subscribe to the Scene Library at least as often as store buyers, after one season.",
         "check_days": 210},
    ],
    # The Strategy Bot's proposal after the briefing: a bet the team had not written down yet. Ana has accepted it into
    # the draft.
    "proposal": {
        "statement": "Public bids from towns and HOAs are a motion of their own, with a survey and a fixed date.",
        "why": "A public bid has a published scope and a deadline, so our survey and crew can price it in a week. A town "
               "that renews gives us the next contract without a campaign.",
        "rules_out": "Bidding on a scope we cannot survey before the deadline, and a bid below the crew's cost.",
        "wrong_if": "Fewer than three public bids for holiday lights come up in our market in a season, for two seasons.",
        "check_days": 180,
        "reason": "This week's briefing: two towns put their holiday light contracts out to public bid. Bids have a fixed "
                  "scope and a date, which fits our survey and crew. Proposed a bet for them.",
    },
    # Published briefing for this week, by the Librarian.
    "briefing": {
        "title": "Holiday projector market this week",
        "items": [
            ("change", "Glimmerco cut its two-unit kit to $399 on the bigmarket.example marketplace, $50 under Home Pro."),
            ("theme", "Calibration came up in seven of this week's twelve support tickets, most asking for a second try."),
            ("signal", "Two towns put their holiday light contracts out to public bid, with the scope and the deadline "
                       "published."),
        ],
        "body": "# Holiday projector market this week\n\n"
                "Two things moved: a competitor priced against our two-unit kit, and towns started buying through public "
                "bid. Our bets still hold, with one proposal for Ana to decide.\n\n"
                "## What moved\n\n"
                "- Glimmerco (glimmerco.example) cut its two-unit kit to $399 on bigmarket.example, $50 under Home Pro.\n"
                "- Frostlamp (frostlamp.example) added a visit from a partner crew to its Storefront kit.\n"
                "- Calibration came up in seven of this week's twelve support tickets. Most asked for a second try on the "
                "first night.\n"
                "- Two towns put their holiday light contracts out to public bid, with the scope and the deadline "
                "published.\n\n"
                "## What it means for our bets\n\n"
                "- **Calibration that works first try.** The support tickets point here. The second-visit tickets are how "
                "we will know whether the bet holds.\n"
                "- **Business installs.** The two towns are the first public bids this season. The Strategy Bot proposed "
                "a bet for them, for Ana to decide.\n"
                "- **Marketplace and retail.** Glimmerco's price cut is on the marketplace, so the price gap there is the "
                "risk. The bet says only our store sells the subscription, and that still holds.\n\n"
                "## Not known yet\n\n"
                "- Whether Glimmerco's price cut moves our share of kit sales on bigmarket.example.\n"
                "- Whether the towns' bids stay open to a regional crew, or go to one contractor.\n",
    },
    # The brand book the owner published before the business branch, through the brand API.
    "brand": {
        "note": "First brand book, for the holiday season.",
        "sections": {
            "name": "Acme Lights",
            "positioning": "Holiday projectors that turn a building into a light show, set up in ten minutes.",
            "voice": [
                {"trait": "Plain and warm", "do": "Say what the projector puts on the wall and who it is for.",
                 "dont": "Say immersive experience or transform your space."},
                {"trait": "Exact about setup", "do": "Give the steps and the time each one takes.",
                 "dont": "Promise a setup time we have not timed on a real facade."},
            ],
            "tone": [
                {"situation": "A support ticket about a crooked scene", "guide": "Apologize once, then give the next "
                                                                                "calibration step."},
                {"situation": "A shop or a town asking for a quote", "guide": "Say the survey comes first, then the price."},
            ],
            "words": {
                "use": [{"word": "scene"}, {"word": "first night"}, {"word": "facade"}],
                "avoid": [{"word": "immersive", "why": "Says nothing the customer can check.", "level": "warn"},
                          {"word": "revolutionary", "why": "A claim we cannot prove.", "level": "block"}],
            },
            "naming": [
                {"name": "Home Starter", "rule": "Two words, capitalized, for the one-unit kit.",
                 "wrong": ["starter kit"]},
                {"name": "Home Pro", "rule": "Two words, capitalized, for the two-unit kit.", "wrong": ["Pro kit"]},
                {"name": "Storefront", "rule": "One word, for the shop and hotel kit with a yearly license.",
                 "wrong": ["store front"]},
                {"name": "Scene Library", "rule": "Always both words, for the subscription of new shows.",
                 "wrong": ["scene pack", "show library"]},
            ],
            "visual": {"colors": [{"name": "Ink", "hex": "#1B1F2A"}, {"name": "Lamp", "hex": "#F5B841"},
                                  {"name": "Dusk", "hex": "#4A3F6B"}],
                       "imagery": "Real facades at night, lit by the projector. No stock photos of lights in empty rooms."},
            "claims": {
                "allowed": [
                    {"claim": "Set up in about ten minutes once the kit is on the wall",
                     "proof": "Timed installs in our test homes, logged by our team"},
                    {"claim": "The app maps the facade in one pass",
                     "proof": "The calibration pass in the app, timed on test homes"},
                ],
                "never": ["Every scene lands on the first night", "Works on a wall with no outdoor power"],
            },
            "examples": [
                {"text": "Your whole street will see it from the curb.", "verdict": "on",
                 "why": "Says what the customer gets, in their words."},
                {"text": "Our revolutionary immersive AI light engine.", "verdict": "off",
                 "why": "Hype and a claim we cannot prove."},
            ],
        },
    },
    # The market map, as the owner sets it on Where we play: the root, the core segments under it, one new segment,
    # the Us and Next marks, one accepted estimate per segment, and the companies in each. Marks: ours (Us), next.
    "market": {
        "root": {"root_name": "Holiday light shows",
                 "description": "Projector and light-show installs in the US, for homes, shops, hotels, towns, HOAs and "
                                "event venues.",
                 "geography": "United States", "currency": "USD",
                 "includes": ["Projector kits and installs for homes", "Storefront, hotel and lobby installs",
                              "Public bids for towns, malls and HOAs", "Event venues"],
                 "excludes": ["Light strings and decorations with no projector", "Year-round signage"]},
        "existing": [
            {"id": "segment/home-light-shows", "mark": "ours",
             "players": ["company/lumenhaus", "company/brightyard", "company/stringcraft", "company/yardwise",
                         "company/sparkfield"],
             "size": {"method": "top_down", "spend_usd": 186_000_000, "low_usd": 150_000_000, "high_usd": 215_000_000,
                      "growth_pct": 9,
                      "arithmetic": "US households with a street-facing wall and a seasonal decor habit, times the "
                                    "average spend on a projector kit and a season's scenes.",
                      "evidence": [{"source_url": "https://marketdata.example/holiday-decor-{year}", "source_kind": "site",
                                    "quote": "Projectors were the fastest-growing line of outdoor seasonal decor "
                                             "last year, and most buyers set one up themselves.",
                                    "our_read": "Matches what first-season buyers in our store tell us."}]}},
            {"id": "segment/shops-hotels", "mark": "ours", "players": ["company/glowline", "company/crewline"],
             "size": {"method": "bottom_up", "spend_usd": 64_000_000, "low_usd": 48_000_000, "high_usd": 80_000_000,
                      "growth_pct": 6,
                      "arithmetic": "About 9,000 storefronts and hotel fronts that light for the season, times about "
                                    "$7,000 a site for a scene and a yearly license.",
                      "evidence": [{"source_url": "https://marketdata.example/commercial-seasonal-lighting-{year}",
                                    "source_kind": "site",
                                    "quote": "Storefront and hotel seasonal lighting, counted by site, with the "
                                             "average spend per site.",
                                    "our_read": "The site count is the soft part; the range covers it."}]}},
            {"id": "segment/towns-malls", "mark": "next", "players": ["company/crewline"],
             "size": {"method": "owner", "spend_usd": 41_000_000, "low_usd": 30_000_000, "high_usd": 52_000_000,
                      "growth_pct": 12,
                      "arithmetic": "Set from the public bids we have seen and the budgets of the towns we would bid for.",
                      "evidence": [{"source_url": "https://townsite.example/bids/holiday-lights-{year}",
                                    "source_kind": "filing",
                                    "quote": "Holiday light installation for the main street and the town square: "
                                             "scope, site survey and a fixed completion date.",
                                    "our_read": "One town's bid; the rest of the range is the owner's read."}]}},
        ],
        "added": [
            {"name": "Event venues", "mark": "next", "players": ["company/crewline"],
             "summary": "Venues that light the front of the building for the season: theaters, arenas and event halls.",
             "size": {"method": "top_down", "spend_usd": 22_000_000, "low_usd": 15_000_000, "high_usd": 30_000_000,
                      "growth_pct": 4,
                      "arithmetic": "Event halls and arenas that light the front of the building each season, times the "
                                    "average install.",
                      "evidence": [{"source_url": "https://marketdata.example/event-venue-lighting-{year}",
                                    "source_kind": "site",
                                    "quote": "Venues spent on seasonal front-of-house lighting, grouped by venue type.",
                                    "our_read": "Rough; we have not sized it from our own sales."}]}},
        ],
    },
    # Listening finds the Librarian filed as insights, one per source, under the tags the Related news list shows.
    "news": {
        "news_rival": {"tag": "rival", "kind": "property-change", "ref": "demo-strategy-news-rival",
                       "claim": "Glimmerco cut its two-unit kit to $399 on bigmarket.example, $50 under Home Pro.",
                       "url": "https://glimmerco.example/news/two-unit-kit-price",
                       "quote": "Two-unit kit, now $399, on the marketplace this week.", "confidence": "high"},
        "news_town": {"tag": "market", "kind": "other", "ref": "demo-strategy-news-town",
                      "claim": "A town put its holiday light installs out to public bid, with the scope, a site survey "
                               "and a fixed completion date.",
                      "url": "https://townsite.example/bids/holiday-lights-{year}",
                      "quote": "Holiday light installation for the main street and the town square.",
                      "confidence": "high"},
        "news_shortage": {"tag": "hot", "kind": "other", "ref": "demo-strategy-news-shortage",
                          "claim": "The controller boards for two projector models are six weeks from order at the "
                                   "supplier.",
                          "url": "https://partsdesk.example/notices/controller-boards-{year}",
                          "quote": "Controller boards for the {year} projector range: six weeks from order.",
                          "confidence": "medium"},
    },
    # Days ago, most first: when each step happened.
    "days": {"setup": 5.7, "version_1": 5.5, "brand": 4.2, "business": 2.4, "version_2": 2.2, "market": 2.0,
             "news_rival": 1.9, "briefing": 1.4, "news_town": 1.2, "proposal": 1.0, "accepted": 0.8, "news_shortage": 0.6},
}


def seed(b):
    goals = _goals(b)
    days = CONTENT["days"]
    b.at(days=days["setup"])
    _draft(b, summary=CONTENT["summary_v1"], vision=CONTENT["vision"], sections=CONTENT["sections"])
    for spec in CONTENT["bets"]:
        if spec["version"] == 1:
            _link(b, goals, spec, _bet(b, spec))
    b.at(days=days["version_1"])
    _publish(b, CONTENT["notes"]["v1"])

    b.at(days=days["brand"])
    _brand(b)

    b.at(days=days["business"])
    for spec in CONTENT["bets"]:
        if spec["version"] == 2:
            _link(b, goals, spec, _bet(b, spec))
    b.at(days=days["version_2"])
    _draft(b, summary=CONTENT["summary_v2"], sections=CONTENT["sections_v2"])
    _publish(b, CONTENT["notes"]["v2"])

    b.at(days=days["market"])
    _market(b)

    b.at(days=days["news_rival"])
    _news(b, "news_rival")
    b.at(days=days["briefing"])
    _briefing(b)
    b.at(days=days["news_town"])
    _news(b, "news_town")
    b.at(days=days["proposal"])
    _proposal(b)
    b.at(days=days["news_shortage"])
    _news(b, "news_shortage")


def _goals(b):
    """The goal ids the core seed made, by the owner each bet names: the company's top goal or a department's."""
    rows = sorted(b.call("GET", "goals?all=true")["goals"], key=lambda g: (g["created_at"], g["id"]))
    return {owner: next((g["id"] for g in rows if g["owner_id"] == owner), None)
            for owner in {spec["goal"] for spec in CONTENT["bets"]}}


def _revision(b):
    return b.call("GET", "strategy")["draft"]["revision"]


def _draft(b, **fields):
    b.call("PUT", "strategy/draft", {"revision": _revision(b), **fields})


def _bet(b, spec):
    wrong = {"kind": "text", "text": spec["wrong_if"], "check_by": _day(b, spec["check_days"])}
    reply = b.call("POST", "strategy/bets", {"statement": spec["statement"], "why": spec["why"],
                                             "rules_out": spec["rules_out"], "wrong_if": wrong, "status": "active",
                                             "rank": spec["rank"], "revision": _revision(b)})
    return reply["bet"]["id"]


def _link(b, goals, spec, bet_id):
    """The goal the bet serves, when the core seed has one for that owner."""
    if goals.get(spec["goal"]):
        b.call("POST", "goals/" + goals[spec["goal"]], {"bet_id": bet_id})


def _publish(b, note):
    b.call("POST", "strategy/publish", {"note": note, "revision": _revision(b)})


def _day(b, days):
    return (b.now + timedelta(days=days)).date().isoformat()


def _briefing(b):
    """This week's briefing, written as the Librarian: a market page and a row that lists its items."""
    items = [MB.BriefingItem(text=text, kind=kind) for kind, text in CONTENT["briefing"]["items"]]
    week = MB.this_week(b.now)

    def work(c):
        MB.post(c, Identity(MK.CURATOR, "bot"), week, CONTENT["briefing"]["title"], CONTENT["briefing"]["body"], items)
    b.write(work)


def _proposal(b):
    """The Strategy Bot's proposal: a new bet, from this week's briefing, in the proposed status. Ana has already said
    yes, through the same decide call her card makes, so the bet sits in the strategy draft as proposed and nothing
    waits on Goals; nothing is published."""
    spec = CONTENT["proposal"]
    bet = {"statement": spec["statement"], "why": spec["why"], "rules_out": spec["rules_out"], "status": "proposed",
           "wrong_if": {"kind": "text", "text": spec["wrong_if"], "check_by": _day(b, spec["check_days"])}}
    payload = {"bets": [bet], "source": "briefing"}

    def work(c):
        proposal = G.propose(c, "bot:" + strategy_bot.SLUG, "strategy_draft", payload=payload, reason=spec["reason"])
        # The owner's card is filed now. Left to the next read, goal_asks.sync files it at that read's clock, which
        # can be an earlier build day, so the task would be dated before the proposal it asks about.
        S.file_card(c, "human:ana", proposal)
        return proposal["id"]
    pid = b.write(work)
    b.at(days=CONTENT["days"]["accepted"])
    b.call("POST", f"goal-proposals/{pid}/decide", {"decision": "confirm"})


def _brand(b):
    """The brand book, written as the owner in the brand draft and published as version 1."""
    b.call("PUT", "brand/draft", {"sections": CONTENT["brand"]["sections"]})
    b.call("POST", "brand/publish", {"note": CONTENT["brand"]["note"]})


def _dated(b, value):
    """Text and URLs that name a year carry the build's year, so a build in another year names that year."""
    return value.replace("{year}", str(b.now.year)) if isinstance(value, str) else value


def _market(b):
    """The market map, set up the way Where we play sets it, as Ana: the definition and its root, the core segments
    placed under the root, one new segment, the Us and Next marks, an accepted estimate for each segment, and the
    companies in each. Every write is a market API call. The year is the build's own."""
    spec = CONTENT["market"]
    root = b.call("PUT", "market/definition", {**spec["root"], "year": b.now.year})["root"]
    for seg in spec["existing"] + spec["added"]:
        size = dict(seg["size"])
        evidence = [b.call("POST", "market/evidence", {k: _dated(b, v) for k, v in item.items()})["evidence"]["id"]
                    for item in size.pop("evidence")]
        if "id" in seg:         # a core segment: placed under the root now
            seg_id = seg["id"]
            b.call("POST", "market/edges", {"src": seg_id, "rel": "member_of", "dst": root, "evidence_ids": evidence})
        else:                   # a new segment: made under the root
            made = b.call("POST", "market/segments", {"name": seg["name"], "parent_id": root, "summary": seg["summary"]})
            seg_id = made["segment"]["id"]
        b.call("POST", "market/segments/target", {"id": seg_id, "target": seg["mark"]})
        b.call("POST", "market/sizes", {**size, "segment_id": seg_id, "evidence_ids": evidence, "accept": True})
        for company in seg["players"]:
            b.call("POST", "market/edges", {"src": company, "rel": "operates_in", "dst": seg_id,
                                            "evidence_ids": evidence})


def _news(b, key):
    """A listening find the Librarian filed as an insight. It is prose for the Related news list; no entity changes."""
    item = {k: _dated(b, v) for k, v in CONTENT["news"][key].items()}

    def work(c):
        MK.report(c, MK.CURATOR, kind=item["kind"], about="Listening: " + item["tag"], claim=item["claim"],
                  source_url=item["url"], quote=item["quote"], confidence=item["confidence"], source_ref=item["ref"])
    b.write(work)
