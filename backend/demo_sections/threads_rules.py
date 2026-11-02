"""Threads and org rules: a Home per department (one with a sub-thread), a company thread, two DMs, the org rules Ana
added, Ben's rule suggestion, and one draft that a rule held for Ben."""

from .. import org_rule_check as OC
from .. import org_rules as R
from .. import threads as T

# Hours ago, oldest first within each list. Every name is fictional (acme.example and .example domains only).
CONTENT = {
    "rules": [
        {"key": "refund", "ago": 150, "text": "Never promise a refund over $100 in a customer message. Those go to Ben.",
         "if_broken": "escalate", "reviewer": "human:ben"},
        {"key": "price", "ago": 150, "text": "A price change on any product line needs Ana's OK.",
         "if_broken": "escalate", "reviewer": "human:ana"},
        {"key": "drafts", "ago": 149,
         "text": "Customer messages are drafts until a person sends them, unless sending is turned on for that inbox.",
         "if_broken": "block"},
    ],
    "suggestion": {"ago": 50, "by": "human:ben", "bot": "marketplace",
                   "text": "Marketplace listings say ships in 3 to 5 days only while the unit is in stock at the warehouse."},
    "threads": {
        "ops": {"home": "bot:ops-manager"},
        "care": {"home": "bot:support-lead"},
        "eng": {"home": "bot:product-manager"},
        "growth": {"home": "bot:marketing-lead"},
        "finance": {"home": "bot:finance-lead"},
        "all": {"title": "This week", "by": "human:ana"},
        "coo": {"dm": ["human:ana", "bot:coo"]},
        "pricing": {"dm": ["human:ana", "bot:pricing"]},
    },
    "messages": [
        # Operations
        {"in": "ops", "ago": 100, "by": "bot:procurement",
         "say": "The Storefront shipment is at the port, two days late. Handing it to @logistics to rebook."},
        {"in": "ops", "ago": 96, "by": "bot:logistics",
         "say": "Rebooked the truck. Units clear customs Thursday night and reach the warehouse Friday morning. Passing the retail date to @ben."},
        {"in": "ops", "ago": 84, "by": "bot:compliance",
         "say": "The FCC Part 15 test report for the Storefront unit is filed. The shipment needs nothing more from us."},
        {"in": "ops", "ago": 60, "by": "bot:inventory",
         "say": "Warehouse count: Home Starter 1,820, Home Pro 1,140, Storefront 310. About nine days at the current pace."},
        {"in": "ops", "ago": 30, "by": "bot:quality",
         "say": "Checked the first 50 Storefront units off the truck. Two cracked lenses; both pulled from the batch."},
        {"in": "ops", "ago": 14, "by": "human:ben",
         "say": "Thanks. Tell the retail buyer we confirm the delivery date by Wednesday. I will call them."},
        {"in": "ops", "ago": 12, "by": "bot:ops-manager",
         "say": "The retail buyer has the Wednesday date. Ben's follow-up call is on his list."},
        # Customer care
        {"in": "care", "ago": 140, "by": "bot:support-qa",
         "say": "14 customers failed calibration overnight. The phone moved during the recording, so the pattern did not match. Each has a note on the ticket."},
        {"in": "care", "ago": 136, "by": "bot:technical-support",
         "say": "Sent the redo steps to 12 of the 14. Each takes about five minutes and needs the same room."},
        {"in": "care", "ago": 120, "by": "bot:technical-support",
         "say": "Two units will not finish the pattern check after three tries. Sent both to @ben with the logs."},
        {"in": "care", "ago": 70, "by": "bot:reputation",
         "say": "Reviews this week: 4.6 stars across 212. Two mention calibration; both are covered by the redo steps."},
        {"in": "care", "ago": 20, "by": "bot:support-qa",
         "say": "Morning check: 3 of 40 tickets need a person before a reply goes out. All three are with @ben."},
        {"in": "care", "ago": 11, "by": "bot:support", "held": True, "breaks": "refund",
         "say": "Draft for the cracked-lens customer: We will refund the $180 install fee and send a new lens at no charge. We are sorry for the trouble."},
        {"in": "care", "ago": 10, "by": "bot:support-lead",
         "say": "The cracked-lens draft is held for @ben. It promises a refund over $100, so the decision is his."},
        # Engineering: the 2.4 rollout pause, its fix and sub-thread, the 2.4.1 release, and a render stall
        {"in": "eng", "ago": 70, "by": "bot:health-monitor",
         "say": "Firmware 2.4 is on 1% of units. Offline restarts on those units are up 3x against 2.3."},
        {"in": "eng", "ago": 66, "by": "human:eli",
         "say": "Is it the calibration change or the power code?"},
        {"in": "eng", "ago": 64, "by": "bot:health-monitor",
         "say": "The restarts line up with the power-up code, not calibration. I would pause the rollout at 5%."},
        {"in": "eng", "ago": 60, "by": "bot:cloud",
         "say": "Render uploads are fine on 2.4. I checked 300 uploads from 2.4 units; none came back with errors."},
        {"in": "eng", "ago": 52, "by": "bot:firmware", "key": "fix",
         "say": "Rollout paused; 2.4 stays on the 1% ring until the fix ships. Fix PR is up: power-up now waits for the watchdog timer before it restarts anything. 14 lines."},
        {"in": "eng", "ago": 50, "under": "fix", "by": "bot:pr-reviewer",
         "say": "Reviewing. The watchdog change is right. One comment on the retry count."},
        {"in": "eng", "ago": 49, "under": "fix", "by": "bot:firmware",
         "say": "Changed the retry count to 3 and pushed."},
        {"in": "eng", "ago": 46, "under": "fix", "by": "bot:pr-reviewer",
         "say": "Approved. Ready for the staff ring."},
        {"in": "eng", "ago": 45, "under": "fix", "by": "human:eli",
         "say": "Approved. Staff ring first, then 5%, then everyone."},
        {"in": "eng", "ago": 36, "by": "bot:release-notes",
         "say": "Draft notes for 2.4.1: fixes restarts after power-up on some units. Nothing for users to do. Ready for review."},
        {"in": "eng", "ago": 34, "by": "human:eli",
         "say": "Good. Ship 2.4.1 to the staff ring today, then 5%."},
        {"in": "eng", "ago": 30, "by": "bot:mobile",
         "say": "App 1.8 with the new calibration screen passed store review. It ships Thursday with the 2.4.1 notes."},
        {"in": "eng", "ago": 29, "by": "bot:support-qa",
         "say": "The render queue has not moved in 40 minutes, so customer previews are stuck. Handing it to @health-monitor."},
        {"in": "eng", "ago": 27, "by": "bot:health-monitor",
         "say": "Confirmed: the queue is stalled, not slow. Handing it to @cloud."},
        {"in": "eng", "ago": 25, "by": "bot:cloud",
         "say": "Found it: a worker held its lease after a crash. Restarted it. The queue is clear and nothing was lost."},
        {"in": "eng", "ago": 22, "by": "bot:health-monitor",
         "say": "Staff ring on 2.4.1: no offline restarts in six hours. The 5% ring is clear to go."},
        {"in": "eng", "ago": 20, "by": "human:eli",
         "say": "Read the render summary. No action from me."},
        {"in": "eng", "ago": 8, "by": "bot:product-manager",
         "say": "Last week's miss: a scene landed on the wrong window when two windows overlap. I opened a daily check with a replay test for it."},
        # Growth
        {"in": "growth", "ago": 58, "by": "bot:marketplace",
         "say": "Home Pro on bigmarket.example shows 12 left, and the listing goes out of stock by tomorrow. Asking @inventory to move 200 units to the marketplace warehouse."},
        {"in": "growth", "ago": 54, "by": "bot:inventory",
         "say": "Moving 200 Home Pro units from the warehouse. They land Thursday."},
        {"in": "growth", "ago": 48, "by": "bot:marketplace",
         "say": "The listing says ships in 3 to 5 days, which is only true while stock is in the warehouse. Ben has suggested that as a rule for listings."},
        {"in": "growth", "ago": 40, "by": "bot:video-producer",
         "say": "Three Home Pro demo clips are cut: the window, the garage and the roofline. Ready for @cara to pick."},
        {"in": "growth", "ago": 36, "by": "bot:creators",
         "say": "Two creator partners booked for the week of the 10th. Drafts are due Friday."},
        {"in": "growth", "ago": 26, "by": "bot:paid-media",
         "say": "Spend is at 92% of this week's budget. Asking @finance-lead for $800 more for the weekend push."},
        {"in": "growth", "ago": 24, "by": "bot:finance-lead",
         "say": "Approved: $800 for the weekend push. Stop at the week's cap."},
        {"in": "growth", "ago": 16, "by": "bot:email-marketing",
         "say": "Holiday email goes to the 6,200 customers who opted in, Wednesday at 10 am. The draft is ready for review."},
        {"in": "growth", "ago": 12, "by": "human:cara",
         "say": "Good. Pick the clips by Friday, and send the email plan to Ana."},
        # Finance
        {"in": "finance", "ago": 120, "by": "bot:bookkeeper",
         "say": "October books are closed. Two lens supplier invoices still need a purchase order match."},
        {"in": "finance", "ago": 100, "by": "bot:accounts-payable",
         "say": "Paid the freight invoice from the port carrier: $2,180."},
        {"in": "finance", "ago": 80, "by": "bot:ar-followup",
         "say": "Four business invoices are past due, $3,410 in total. Reminders went out Monday."},
        {"in": "finance", "ago": 40, "by": "bot:spend-watcher",
         "say": "Ads spend is 8% under plan for the month. Nothing to change yet."},
        # Ana and pricing: the December price for Home Pro
        {"in": "pricing", "ago": 30, "by": "bot:pricing",
         "say": "Raise Home Pro from $449 to $479 for December? On bigmarket.example Home Pro lists at $449 today, and the nearest two-unit kit lists at $489. Last two Decembers 88% of stock sold at $449, with three weeks left. At $479 margin is 41%, against 38% at $449 after the lens supplier's new cost."},
        {"in": "pricing", "ago": 26, "by": "human:ana",
         "say": "If bigmarket keeps Home Pro at $449, do we still sell through at $479?"},
        {"in": "pricing", "ago": 25, "by": "bot:pricing",
         "say": "Probably about 4 points less, to about 84%. The extra margin still comes out ahead, so I still recommend it. Holding the change until you say yes."},
        # The company thread
        {"in": "all", "ago": 130, "by": "human:ana",
         "say": "This week: Storefront units reach the warehouse Friday morning. Ben has the delivery date for the retail partner."},
        {"in": "all", "ago": 60, "by": "human:cara",
         "say": "Holiday email goes out Wednesday. Creators' drafts are due Friday."},
        {"in": "all", "ago": 5, "by": "bot:coo",
         "say": "Three things need a person this week: the December price for Home Pro, a Storefront refund held for @ben, and the Wednesday retail date."},
        # Ana and the Assistant
        {"in": "coo", "ago": 7, "by": "human:ana", "say": "What needs me today?"},
        {"in": "coo", "ago": 6, "by": "bot:coo",
         "say": "Two things. Home Pro's December price is waiting on your OK, and pricing's case is in your DM with pricing. The refund over $100 is held for Ben under the refund rule, so it is not yours to decide."},
        {"in": "coo", "ago": 4, "by": "human:ana", "say": "What is the refund hold, exactly?"},
        {"in": "coo", "ago": 3, "by": "bot:coo",
         "say": "A $180 cracked-lens refund draft from support. Over $100 goes to Ben under the refund rule, so it is held for him. Nothing was sent to the customer."},
    ],
}


def seed(b):
    from ..demo_seed import TOKENS      # the demo's own sign-ins: Ben's suggestion goes in as Ben, through the API
    rules = {}
    for item in CONTENT["rules"]:
        _when(b, item["ago"])
        body = {"kind": "internal", "text": item["text"], "who": {"everyone": True}}
        body.update({k: item[k] for k in ("if_broken", "reviewer") if k in item})
        rules[item["key"]] = b.call("POST", "rules", body)["id"]
    suggestion = CONTENT["suggestion"]
    _when(b, suggestion["ago"])
    b.call("POST", "rules", {"kind": "internal", "text": suggestion["text"], "who": {"bots": [suggestion["bot"]]}},
           token=TOKENS[suggestion["by"]])
    b.write(lambda c: _threads(b, c, rules))


def _when(b, hours_ago):
    b.at(days=hours_ago // 24, hours=hours_ago % 24)


def _threads(b, c, rules):
    ids, sent = {}, {}
    for item in sorted(CONTENT["messages"], key=lambda m: -m["ago"]):     # oldest first
        _when(b, item["ago"])
        thread = _thread(c, ids, item["in"])
        if item.get("held"):
            _hold(c, thread, item, rules)
            continue
        posted = T.post(c, item["by"], thread, item["say"], parent_message_id=sent.get(item.get("under")))
        if "key" in item:
            sent[item["key"]] = posted["id"]


def _thread(c, ids, key):
    if key not in ids:
        spec = CONTENT["threads"][key]
        if "home" in spec:
            ids[key] = T.home(c, spec["home"])["id"]
        elif "dm" in spec:
            ids[key] = T.dm(c, spec["dm"][0], spec["dm"][1:])["id"]
        else:
            ids[key] = T.create(c, spec["by"], title=spec["title"], audience="company")["id"]
    return ids[key]


def _hold(c, thread, item, rules):
    """A draft that a rule holds. The check would find that rule broken, so its verdict is written here as the check
    returns it (no model runs in the demo). Posting it with the verdict writes no message and answers with the hold."""
    author = item["by"]
    recipients = sorted({p for p in OC.thread_people(c, {"thread_id": thread}) if p and p != author})
    place = OC.place_for(c, author, recipients, thread)
    rows = [{"id": r["id"], "version": r["version"], "if_broken": r["if_broken"], "reviewer": r.get("reviewer"),
             "suggested_by": r.get("suggested_by") or "", "text": r["text"]} for r in R.matching(c, author, recipients, place)]
    verdict = {"author": author, "sender": author, "via": "", "operation": "threads", "recipients": recipients,
               "rules": rows, "digest": OC.digest("threads", author, recipients, item["say"], "", rows, "demo", "fixed"),
               "broken": [rules[item["breaks"]]], "outcome": "broken", "error": None, "call": None, "cached": False,
               "draft": False, "matched": [(r["id"], r["version"]) for r in rows], "place": place, "origin": None,
               "attempt_id": "", "context": {}, "obj": ""}
    posted = T.post(c, author, thread, item["say"], checked=verdict)
    assert "held" in posted, posted
