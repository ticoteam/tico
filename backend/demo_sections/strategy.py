"""Strategy: the vision, three bets and a market briefing.

Version 1 is published a few days before version 2, which adds a third bet and a new summary, so the history has a
change to show. The Strategy Bot's one pending proposal sharpens a bet's wrong-if, and this week's briefing is the
Librarian's. Every row goes through the API as Ana, or through the domain function a real install runs.
"""

from datetime import timedelta

from .. import goals as G
from .. import market as MK
from .. import market_briefing as MB
from .. import strategy_bot
from ..auth import Identity

CONTENT = {
    "vision": "Every short-term rental host runs their homes from one calendar and one inbox, with cleaners and repairs "
              "sorted before the guest asks.",
    "summary_v1": "Acme runs cleanings, repairs and guest messages for 400 short-term rental hosts, on its own booking "
                  "software. Hosts with several listings are the market, and guest messaging is how we reach them.",
    "summary_v2": "Acme runs cleanings, repairs and guest messages for 400 short-term rental hosts, on its own booking "
                  "software. Hosts with several listings are the market. Guest messaging brings them in, cleanings "
                  "follow, and our booking software keeps one calendar for all their channels.",
    "sections": {
        "serve": "Hosts with five or more short-term rentals. They clean every week, track it in a spreadsheet and text "
                 "a cleaning company by hand. Today they do it themselves on weekends.\n\n"
                 "We turned down single-listing hosts. They book a cleaner a few times a year and leave when the season "
                 "ends.",
        "sell": "Guest messaging and our booking software, billed per listing each month. Cleanings and repairs are "
                "dispatched through Acme and billed per visit.\n\n"
                "The price that breaks: a per-listing fee above what a host pays a cleaner for one turnaround a month "
                "sends multi-listing hosts back to a spreadsheet. Halving it gives away the margin our cleaners need.",
        "know": "A cleaning turnaround is a booking problem, not a cleaning problem. A checkout, a cleaner and the next "
                "check-in land in the same few hours, and the messages between guests and cleaners are where that "
                "gets lost. We see the thread, so we can move the cleaning before the guest asks. Most competitors "
                "sell a calendar or a cleaner. Few sell both in one place.",
        "how": "Self-serve sign-up for a single host. An account manager for hosts with five or more listings, who "
               "walks them through one calendar across every channel on the first call.\n\n"
               "Local partners who already visit hosts' homes, such as key-box and laundry companies, refer them for a "
               "referral fee. Two weeks free on the multi-listing plan.",
        "not": "- We won't sell a sales call to a single-listing host.\n"
               "- We won't resell another company's channel manager or calendar.\n"
               "- We won't take a cleaning booking that no cleaner can do on the day.\n"
               "- We won't cut the per-listing price for a year to win a host.",
    },
    "notes": {"v1": "First strategy: two bets from the setup answers.",
              "v2": "Booking software is a bet now; the summary names the calendar."},
    # Goals are read from the core seed by owner: the company's top goal, support's and sales' goals.
    "bets": [
        {"key": "market", "rank": 0, "goal": "company", "version": 1,
         "statement": "Hosts with 5 or more listings are our market; single-listing hosts churn.",
         "why": "Hosts with five or more listings run cleanings every week, so a missed turnaround costs them money and "
                "they keep paying us. Single-listing hosts book a cleaner a few times a year. Most of our cancellations "
                "come from them.",
         "rules_out": "A free plan for single-listing hosts, and a sales call for any host with fewer than five listings.",
         "wrong_if": "Single-listing hosts churn at or below the rate of five-listing hosts for two quarters running.",
         "check_days": 180},
        {"key": "wedge", "rank": 1, "goal": "support", "version": 1,
         "statement": "Guest messaging is the wedge; cleanings follow.",
         "why": "Hosts open the guest inbox every day, so it is the first thing they let us run. Once a thread shows a "
                "checkout and a check-in on the same day, the cleaning is one tap away. Cleanings sold alone, without "
                "messaging, did not stick.",
         "rules_out": "Selling cleanings alone, and a messaging tool with no calendar behind it.",
         "wrong_if": "Hosts who use guest messaging do not start booking cleanings with us.",
         "check_days": 90},
        {"key": "calendar", "rank": 2, "goal": "sales", "version": 2,
         "statement": "Our own booking software replaces the channel calendar for hosts with 5 or more listings.",
         "why": "Multi-listing hosts keep one calendar per booking channel and double-book when a stay changes. Our "
                "booking software holds one calendar and tells the cleaner the same hour. No channel manager sells that "
                "with a cleaning attached.",
         "rules_out": "Reselling another company's channel manager, and building for hosts with a single channel.",
         "wrong_if": "Hosts who move to our booking software still double-book more than once a month after 60 days.",
         "check_days": 150},
    ],
    # The Strategy Bot's proposal after the briefing: a number for bet 2's wrong-if. Waiting for Ana.
    "proposal": {
        "wrong_if": "Fewer than one in three hosts who message through us book a cleaning within 60 days of their first "
                    "message.",
        "check_days": 120,
        "reason": "This week's briefing: a booking-channel vendor made guest messaging free on its base plan. Bet 2 "
                  "still holds, but its wrong-if has no number. Proposed one.",
    },
    # Published briefing for this week, by the Librarian.
    "briefing": {
        "title": "Rental market this week",
        "items": [
            ("change", "A booking-channel vendor made guest messaging free on its base plan."),
            ("theme", "Five support tickets this week asked for one calendar across channels."),
            ("signal", "Two multi-listing hosts moved a channel after a double booking."),
        ],
        "body": "# Rental market this week\n\n"
                "Guest messaging just got cheaper for hosts. Our bets still hold, with one change proposed.\n\n"
                "## What moved\n\n"
                "- A booking-channel vendor made guest messaging free on its base plan. Some hosts on our plan may ask "
                "why they pay us.\n"
                "- Five support tickets asked for one calendar across channels. Most came from hosts with five or more "
                "listings.\n"
                "- Two multi-listing hosts moved a channel after a double booking.\n\n"
                "## What it means for our bets\n\n"
                "- **Guest messaging is the wedge.** Still right, but free messaging makes it easier to copy. The "
                "Strategy Bot proposed a number for its wrong-if, for Ana to decide.\n"
                "- **Our booking software.** The calendar tickets point at bet 3, which is active now.\n\n"
                "## Not known yet\n\n"
                "- Whether free messaging changes how many hosts book a cleaning with us.\n",
    },
    # Days ago, most first: when each step happened.
    "days": {"setup": 5.7, "version_1": 5.5, "bet_3": 2.4, "version_2": 2.2, "briefing": 0.3, "proposal": 0.2},
}


def seed(b):
    goals = _goals(b)
    days = CONTENT["days"]
    bets = {}
    b.at(days=days["setup"])
    _draft(b, summary=CONTENT["summary_v1"], vision=CONTENT["vision"], sections=CONTENT["sections"])
    for spec in CONTENT["bets"]:
        if spec["version"] == 1:
            bets[spec["key"]] = _bet(b, spec)
            _link(b, goals, spec, bets[spec["key"]])
    b.at(days=days["version_1"])
    _publish(b, CONTENT["notes"]["v1"])

    b.at(days=days["bet_3"])
    for spec in CONTENT["bets"]:
        if spec["version"] == 2:
            bets[spec["key"]] = _bet(b, spec)
            _link(b, goals, spec, bets[spec["key"]])
    b.at(days=days["version_2"])
    _draft(b, summary=CONTENT["summary_v2"])
    _publish(b, CONTENT["notes"]["v2"])

    b.at(days=days["briefing"])
    _briefing(b)
    b.at(days=days["proposal"])
    _proposal(b, bets["wedge"])


def _goals(b):
    """The goal ids the core seed made, found by their owner: the company's top goal and the support and sales goals."""
    rows = sorted(b.call("GET", "goals?all=true")["goals"], key=lambda g: (g["created_at"], g["id"]))

    def first(match):
        return next((g["id"] for g in rows if match(g)), None)
    return {"company": first(lambda g: g["owner_id"] == G.COMPANY and not g["parent_id"]),
            "support": first(lambda g: g["owner_id"] == "bot:support"),
            "sales": first(lambda g: g["owner_id"] == "bot:sales")}


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


def _proposal(b, bet_id):
    """The Strategy Bot's proposal to sharpen a bet's wrong-if. The owner confirms or rejects it; nothing is published."""
    wrong = {"kind": "text", "text": CONTENT["proposal"]["wrong_if"], "check_by": _day(b, CONTENT["proposal"]["check_days"])}
    payload = {"bets": [{"id": bet_id, "wrong_if": wrong}]}

    def work(c):
        G.propose(c, "bot:" + strategy_bot.SLUG, "strategy_draft", payload=payload, reason=CONTENT["proposal"]["reason"])
    b.write(work)
