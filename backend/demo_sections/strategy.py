"""Strategy: the vision, three bets and a market briefing.

Version 1 is the home business: two bets and the sections for homeowners. Version 2 is published a few days later and
adds the business bet, a new summary and the business sections, so the history has a change to show. The Strategy
Bot's one pending proposal is a bet the team has not written down yet, from this week's briefing, and this week's
briefing is the Librarian's. Every row goes through the API as Ana, or through the domain function a real install runs.
"""

from datetime import timedelta

from .. import goals as G
from .. import market as MK
from .. import market_briefing as MB
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
    # Version 2 changes these four sections for the business branch; "know" stays as it was.
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
    # The Strategy Bot's proposal after the briefing: a bet the team has not written down yet. Waiting for Ana.
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
    # Days ago, most first: when each step happened.
    "days": {"setup": 5.7, "version_1": 5.5, "business": 2.4, "version_2": 2.2, "briefing": 0.3, "proposal": 0.2},
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

    b.at(days=days["business"])
    for spec in CONTENT["bets"]:
        if spec["version"] == 2:
            _link(b, goals, spec, _bet(b, spec))
    b.at(days=days["version_2"])
    _draft(b, summary=CONTENT["summary_v2"], sections=CONTENT["sections_v2"])
    _publish(b, CONTENT["notes"]["v2"])

    b.at(days=days["briefing"])
    _briefing(b)
    b.at(days=days["proposal"])
    _proposal(b)


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
    """The Strategy Bot's proposal: a new bet, from this week's briefing, in the proposed status. The owner confirms or
    rejects it; nothing is published."""
    spec = CONTENT["proposal"]
    bet = {"statement": spec["statement"], "why": spec["why"], "rules_out": spec["rules_out"], "status": "proposed",
           "wrong_if": {"kind": "text", "text": spec["wrong_if"], "check_by": _day(b, spec["check_days"])}}
    payload = {"bets": [bet], "source": "briefing"}

    def work(c):
        G.propose(c, "bot:" + strategy_bot.SLUG, "strategy_draft", payload=payload, reason=spec["reason"])
    b.write(work)
