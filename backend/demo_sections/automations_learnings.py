"""Automations with runs (a loop the run guard held and Ben dropped, declined cards, nightly bookkeeping, device and
release checks), a week of Learnings nights, and Tico release updates delivered to two bots.

Everything goes through the code a real install runs: automations are saved through the API, events and mail reach the
automation engine through its own entry points (`hubdb.hook`, `automations.on_mail`), the engine drains the runs, and
the Learnings and update-feed modules write their own rows. Bots work their queue through the runner's own endpoints
(claim, started, memory report, complete), and a person reads a thread through the thread API. The only direct SQL is
the scheduler's bookkeeping and reads of Update delivery receipts and task threads.
"""

import hashlib
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .. import automations, learnings, update_feeds, updates
from ..scheduler import next_due
from ..store import H

FINISH_AFTER = timedelta(minutes=12)    # how long a night's run takes after it starts
MAX_AHEAD_JOBS = 40                     # queued jobs a delivery turn works through before its own
CLAIM_TRIES = 30                        # polls a computer makes for a bot's job before it gives up
CLAIM_POLL = timedelta(seconds=5)       # the gap between two polls on the demo clock (a computer takes work after 120 s)
WORK_WINDOW = timedelta(minutes=5)      # room a delivery turn needs before the build's now
CLOSE_MARGIN = timedelta(minutes=1)     # a task closed "now" is closed this long before the build's now

CONTENT = {
    "mailbox": "ben@acme.example",
    "close_after_hours": 2,
    "created_days": 7,           # the automations are saved this many days back, before their first run
    # Automations: who owns each, what starts it, and what it does. `closer` is the bot that finishes the task a run
    # opens (with `note`), when an event or mail says it was done.
    "automations": [
        {"key": "calibration", "name": "Calibration failures to technical support", "owner": "person:ben",
         "closer": "technical-support", "note": "Redo done. The unit calibrated on the next try.",
         "trigger": {"type": "event", "name": "custom.calibration.failed"},
         "steps": [{"id": "redo", "type": "action", "do": "create_task", "args": {
             "title": "Redo calibration for {{event.data.unit}}",
             "body": "The phone moved during calibration. Walk the customer through a redo.",
             "owner": "technical-support"}}]},
        {"key": "calibration-twice", "name": "Calibration failed twice to Ben", "owner": "person:ben",
         "trigger": {"type": "event", "name": "custom.calibration.failed",
                     "filters": [{"field": "event.data.attempt", "op": ">=", "value": 2}]},
         "steps": [{"id": "ben", "type": "action", "do": "create_task", "args": {
             "title": "Calibration failed twice on {{event.data.unit}}",
             "body": "Technical support could not fix it. Decide: call the customer, or send a replacement unit.",
             "owner": "person:ben"}}]},
        {"key": "orders", "name": "Marketplace orders to inventory", "owner": "person:ben",
         "closer": "inventory", "note": "Packed and shipped.",
         "trigger": {"type": "message", "inbox": "ben@acme.example",
                     "filters": [{"field": "message.from", "op": "contains", "value": "bigmarket.example"}]},
         "steps": [{"id": "pick", "type": "action", "do": "create_task", "args": {
             "title": "Pick and ship {{message.subject}}",
             "body": "Marketplace order. Check stock, pack it and mark it shipped.",
             "owner": "inventory"}}]},
        # Ben's inbox as a board (ui/app/automations-flow.js auBoardShape): rules, a question, then Sort into the
        # destinations. Off, so the week's mail runs only the automations above and below.
        {"key": "inbox-board", "name": "Ben's inbox", "owner": "person:ben", "enabled": False,
         "trigger": {"type": "message", "inbox": "ben@acme.example"},
         "destinations": [
             {"id": "me", "name": "Me", "color": "amber", "then": [{"do": "leave_for_you", "args": {}}]},
             {"id": "orders", "name": "Orders", "color": "green", "then": [{"do": "create_task", "args": {
                 "title": "Pick and ship {{message.subject}}", "owner": "inventory"}}]},
             {"id": "support", "name": "Support", "color": "blue", "then": [{"do": "create_task", "args": {
                 "title": "Answer {{message.subject}}", "owner": "technical-support"}}]},
             {"id": "archive", "name": "Archive", "color": "gray", "then": [{"do": "archive", "args": {}}]}],
         "steps": [
             {"id": "rules", "type": "filter", "rules": [
                 {"when": [{"field": "message.from", "op": "contains", "value": "bigmarket.example"}], "match": "all",
                  "then": [], "to": "orders", "next": "end", "stop": True},
                 {"when": [{"field": "message.from", "op": "contains", "value": "acme.example"}], "match": "all",
                  "then": [{"do": "label", "args": {"name": "Team"}}], "next": "rule", "stop": False},
                 {"when": [{"field": "message.subject", "op": "matches", "value": "receipt|invoice paid"}], "match": "all",
                  "then": [{"do": "label", "args": {"name": "Receipts"}}], "to": "archive", "next": "end", "stop": True}]},
             {"id": "ask", "type": "decide", "out": "ask", "level": "medium", "questions": {
                 "review": {"type": "noul", "instructions": "Is it about a store review or a broken unit?"}}},
             {"id": "asked", "type": "filter", "rules": [
                 {"when": [{"field": "ask.review", "op": "is", "value": "yes", "min": 0.75}], "match": "all",
                  "then": [], "to": "support", "next": "end", "stop": True}]},
             {"id": "sort", "type": "decide", "out": "sort", "level": "high", "questions": {
                 "to": {"type": "choice", "instructions": "Pick where this goes. Unsure: Me.",
                        "criteria": {"me": "Me", "orders": "Orders", "support": "Support", "archive": "Archive"}}}},
             {"id": "route", "type": "filter", "rules": [
                 {"when": [{"field": "sort.to", "op": "is", "value": "me", "min": 0.5}], "match": "all", "then": [],
                  "to": "me", "next": "end", "stop": True},
                 {"when": [{"field": "sort.to", "op": "is", "value": "orders", "min": 0.5}], "match": "all", "then": [],
                  "to": "orders", "next": "end", "stop": True},
                 {"when": [{"field": "sort.to", "op": "is", "value": "support", "min": 0.5}], "match": "all", "then": [],
                  "to": "support", "next": "end", "stop": True},
                 {"when": [{"field": "sort.to", "op": "is", "value": "archive", "min": 0.5}], "match": "all", "then": [],
                  "to": "archive", "next": "end", "stop": True},
                 {"when": [], "match": "all", "then": [], "to": "me", "next": "end", "stop": True}]}]},
        {"key": "reviews", "name": "1-star reviews to reputation", "owner": "person:ben",
         "closer": "reputation", "note": "Reply posted.",
         "trigger": {"type": "event", "name": "custom.review.posted",
                     "filters": [{"field": "event.data.stars", "op": "<=", "value": 1}]},
         "steps": [{"id": "reply", "type": "action", "do": "create_task", "args": {
             "title": "Reply to a 1-star review of {{event.data.product}}",
             "body": "Draft a reply in our voice. Do not promise a refund in a public reply.",
             "owner": "reputation"}},
                   {"id": "read", "type": "action", "do": "create_task", "args": {
             "title": "Read the 1-star review of {{event.data.product}}",
             "body": "{{event.data.text}}",
             "owner": "person:ben"}}]},
        {"key": "declined", "name": "Declined cards to accounts receivable", "owner": "person:ana",
         "closer": "ar-followup", "note": "Retry paid.", "read_by": "human:ana",
         "trigger": {"type": "event", "name": "finance.card.declined"},
         "steps": [{"id": "chase", "type": "action", "do": "create_task", "args": {
             "title": "Card declined for {{event.data.account}}",
             "body": "Retry the charge once. If it fails again, email the account.",
             "owner": "ar-followup"}}]},
        {"key": "nightly", "name": "Nightly orders to bookkeeping", "owner": "person:ana",
         "closer": "bookkeeper", "note": "Posted. Nothing to flag.", "read_by": "human:ana",
         "trigger": {"type": "schedule", "cron": "0 2 * * *", "tz": "America/Los_Angeles"},
         "steps": [{"id": "post", "type": "action", "do": "create_task", "args": {
             "title": "Post yesterday's orders to the ledger",
             "body": "Check every marketplace and store order is posted, and flag any that do not match.",
             "owner": "bookkeeper"}}]},
        {"key": "rings", "name": "Firmware rings to BotOps", "owner": "person:eli",
         "closer": "botops", "note": "Restarts stayed flat.",
         "trigger": {"type": "event", "name": "custom.firmware.ring_done"},
         "steps": [{"id": "watch", "type": "action", "do": "create_task", "args": {
             "title": "Watch {{event.data.ring}} for offline restarts",
             "body": "Compare offline restarts with the last ring. Report any rise to Eli.",
             "owner": "botops"}}]},
        {"key": "deploys", "name": "Finished deploys to release notes", "owner": "person:eli",
         "closer": "release-notes", "note": "Notes posted.",
         "trigger": {"type": "event", "name": "custom.deploy.finished"},
         "steps": [{"id": "notes", "type": "action", "do": "create_task", "args": {
             "title": "Write the release notes for {{event.data.version}}",
             "body": "Use the merged pull requests since the last release.",
             "owner": "release-notes"}}]},
    ],
    # Events the company's systems report, in any order: the seed plays them oldest first. `at` is [days ago, hours before
    # that].
    "events": [
        {"at": [6, 6.0], "name": "custom.calibration.failed", "subject": "unit/U-1042", "actor": "bot:support-qa",
         "data": {"unit": "U-1042", "attempt": 1}, "close": "calibration"},
        {"at": [6, 2.0], "name": "custom.calibration.failed", "subject": "unit/U-1057", "actor": "bot:support-qa",
         "data": {"unit": "U-1057", "attempt": 1}, "close": "calibration"},
        {"at": [5, 7.0], "name": "custom.calibration.failed", "subject": "unit/U-1061", "actor": "bot:support-qa",
         "data": {"unit": "U-1061", "attempt": 1}, "close": "calibration"},
        {"at": [5, 1.0], "name": "custom.calibration.failed", "subject": "unit/U-1073", "actor": "bot:support-qa",
         "data": {"unit": "U-1073", "attempt": 1}, "close": "calibration"},
        {"at": [4, 4.0], "name": "custom.calibration.failed", "subject": "unit/U-1088", "actor": "bot:support-qa",
         "data": {"unit": "U-1088", "attempt": 1}, "close": "calibration"},
        {"at": [4, 0.7], "name": "custom.calibration.failed", "subject": "unit/U-1093", "actor": "bot:support-qa",
         "data": {"unit": "U-1093", "attempt": 2}, "close": "calibration", "note": "Could not fix. Sent to Ben."},
        {"at": [3, 6.0], "name": "custom.calibration.failed", "subject": "unit/U-1104", "actor": "bot:support-qa",
         "data": {"unit": "U-1104", "attempt": 1}, "close": "calibration"},
        {"at": [3, 3.0], "name": "custom.calibration.failed", "subject": "unit/U-1110", "actor": "bot:support-qa",
         "data": {"unit": "U-1110", "attempt": 1}, "close": "calibration"},
        {"at": [2, 5.0], "name": "custom.calibration.failed", "subject": "unit/U-1126", "actor": "bot:support-qa",
         "data": {"unit": "U-1126", "attempt": 1}, "close": "calibration"},
        {"at": [2, 0.4], "name": "custom.calibration.failed", "subject": "unit/U-1131", "actor": "bot:support-qa",
         "data": {"unit": "U-1131", "attempt": 1}, "close": "calibration"},
        {"at": [1, 8.0], "name": "custom.calibration.failed", "subject": "unit/U-1140", "actor": "bot:support-qa",
         "data": {"unit": "U-1140", "attempt": 1}, "close": "calibration"},
        {"at": [1, 4.0], "name": "custom.calibration.failed", "subject": "unit/U-1152", "actor": "bot:support-qa",
         "data": {"unit": "U-1152", "attempt": 1}, "close": "calibration"},
        {"at": [0, 0.6], "name": "custom.calibration.failed", "subject": "unit/U-1158", "actor": "bot:support-qa",
         "data": {"unit": "U-1158", "attempt": 2}, "close": "calibration", "note": "Could not fix. Sent to Ben."},
        {"at": [0, 0.2], "name": "custom.calibration.failed", "subject": "unit/U-1163", "actor": "bot:support-qa",
         "data": {"unit": "U-1163", "attempt": 1}, "close": "calibration"},
        {"at": [6, 8.0], "name": "custom.review.posted", "subject": "review/R-2201", "actor": "bot:marketplace",
         "data": {"stars": 5, "product": "Home Pro", "text": "The show ran all night."}},
        {"at": [3, 9.0], "name": "custom.review.posted", "subject": "review/R-2214", "actor": "bot:marketplace",
         "data": {"stars": 1, "product": "Home Pro", "text": "The phone app lost the schedule twice."},
         "close": "reviews"},
        {"at": [3, 14.0], "name": "custom.review.posted", "subject": "review/R-2215", "actor": "bot:marketplace",
         "data": {"stars": 4, "product": "Home Pro", "text": "Good, but set-up took a while."}},
        {"at": [2, 10.0], "name": "custom.review.posted", "subject": "review/R-2230", "actor": "bot:marketplace",
         "data": {"stars": 5, "product": "Home Pro", "text": "Easy to calibrate."}},
        {"at": [2, 16.0], "name": "custom.review.posted", "subject": "review/R-2233", "actor": "bot:marketplace",
         "data": {"stars": 1, "product": "Storefront", "text": "Unit stopped at dusk on the second night."}},
        {"at": [0, 0.9], "name": "custom.review.posted", "subject": "review/R-2240", "actor": "bot:marketplace",
         "data": {"stars": 3, "product": "Home Pro", "text": "Fine for the price."}},
        {"at": [4, 11.0], "name": "finance.card.declined", "subject": "charge/C-7781", "actor": "bot:bookkeeper",
         "data": {"account": "Harbor Lane Storefront", "amount": "$1,200.00"}, "close": "declined"},
        {"at": [1, 9.0], "name": "finance.card.declined", "subject": "charge/C-7830", "actor": "bot:bookkeeper",
         "data": {"account": "Pinecrest Hotel", "amount": "$449.00"}, "close": "declined"},
        {"at": [5, 10.0], "name": "custom.firmware.ring_done", "subject": "ring/staff", "actor": "bot:firmware",
         "data": {"ring": "staff units", "version": "2.4"}, "close": "rings"},
        {"at": [0, 0.8], "name": "custom.firmware.ring_done", "subject": "ring/5-percent", "actor": "bot:firmware",
         "data": {"ring": "5% of units", "version": "2.4"}},
        {"at": [5, 15.0], "name": "custom.deploy.finished", "subject": "deploy/cloud-1.8.1", "actor": "bot:devops",
         "data": {"version": "cloud 1.8.1"}, "close": "deploys"},
        {"at": [6, 15.0], "name": "custom.deploy.finished", "subject": "deploy/app-3.1.0", "actor": "bot:devops",
         "data": {"version": "app 3.1.0"}, "close": "deploys"},
        {"at": [1, 15.0], "name": "custom.deploy.finished", "subject": "deploy/cloud-1.8.2", "actor": "bot:devops",
         "data": {"version": "cloud 1.8.2"}},
    ],
    # Mail to Ben's inbox. Orders come from the marketplace; the rest are questions the filter drops.
    "mail": [
        {"at": [6, 9.0], "id": "order-4390", "from": "orders@bigmarket.example",
         "subject": "Order 4390: Home Pro x1", "close": "orders"},
        {"at": [5, 15.0], "id": "order-4391", "from": "orders@bigmarket.example",
         "subject": "Order 4391: Storefront x1", "close": "orders"},
        {"at": [4, 10.0], "id": "order-4402", "from": "orders@bigmarket.example",
         "subject": "Order 4402: Home Pro x2", "close": "orders"},
        {"at": [4, 16.0], "id": "question-771", "from": "buyer@shoppers.example",
         "subject": "Do the projectors need Wi-Fi?"},
        {"at": [3, 8.0], "id": "order-4409", "from": "orders@bigmarket.example",
         "subject": "Order 4409: Home Pro x1", "close": "orders"},
        {"at": [3, 12.0], "id": "question-774", "from": "buyer@shoppers.example",
         "subject": "Can I move the unit to the garage?"},
        {"at": [2, 12.0], "id": "order-4455", "from": "orders@bigmarket.example",
         "subject": "Order 4455: Home Pro x1", "close": "orders"},
        {"at": [1, 9.0], "id": "order-4470", "from": "orders@bigmarket.example",
         "subject": "Order 4470: Storefront x1"},
        {"at": [0, 0.4], "id": "order-4478", "from": "orders@bigmarket.example",
         "subject": "Order 4478: Home Pro x1"},
    ],
    # A forwarder that kept re-sending one order: 121 runs in 18 minutes, which the run guard holds.
    "loop": {"at": [3, 10.0], "id": "loop", "from": "orders@bigmarket.example",
             "subject": "Order 4466: Home Pro x1 (sent again)", "count": 121, "seconds": 9},
    # Ben looks at the held runs the morning after and drops them, once the forwarder is fixed.
    "hold": {"at": [2, 9.0], "automation": "orders", "action": "drop", "by": "human:ben"},
    # The schedule fires every night at 02:00 Pacific, for this many nights back from the day it is seeded.
    "nightly_days": 6,
    # Learnings: one night's run per local day at 03:00. Items are what people said and decided that day, as sourced
    # from a task. `hours` is how long before the run it was said. Scores are per bot: relevance, 0 to 1.
    "learnings": {
        "read": "Read. Kept the rule for the next task.",
        # Update delivers the items after Learn finishes; the bot saves what it learned in that turn.
        "work_after_minutes": 25,
        "nights": [
            {"days": 6, "items": []},
            {"days": 5, "items": [
                {"task": "refund-policy", "author": "human:ana", "hours": 6,
                 "label": "Ana in Update the Support instructions",
                 "text": "Keep the refund wording short. Customers read it on their phones.",
                 "learnable": 0.8, "scores": {"support": 0.8, "support-lead": 0.7, "sales-lead": 0.2}}]},
            {"days": 4, "items": [
                {"task": "tag-refunds", "author": "human:ben", "hours": 7,
                 "label": "Ben in Tag last week's refund requests",
                 "text": "Until Ana sets the refund limit, do not promise a refund over $100 in a reply. "
                         "Send anything bigger to me first.",
                 "learnable": 0.95, "scores": {"support": 0.95, "support-lead": 0.9, "escalations": 0.7,
                                                "reputation": 0.3}}]},
            {"days": 3, "items": [
                {"task": "ticket-replies", "author": "human:ben", "hours": 6,
                 "label": "Ben in Draft replies for the open support tickets",
                 "text": "Login problems: check the app version before you reply. Most are one old version.",
                 "learnable": 0.9, "scores": {"support": 0.9, "technical-support": 0.8, "support-qa": 0.7}},
                {"task": "creator-contracts", "author": "human:ana", "hours": 8,
                 "label": "Ana in Get the twelve creator contracts signed",
                 "text": "Creators get the Storefront demo a week before the launch post.",
                 "learnable": 0.85, "scores": {"creators": 0.9, "marketing-lead": 0.8, "content": 0.6}}]},
            {"days": 2, "items": [
                {"task": "cal-callbacks", "author": "human:ben", "hours": 5,
                 "label": "Ben in Call back owners whose calibration failed twice",
                 "text": "Calibration: hold the phone still at the wall, not panning. A moving phone is the usual cause.",
                 "learnable": 0.9, "scores": {"technical-support": 0.9, "support": 0.8, "content": 0.7}}],
             # The technical-support bot works its packet and saves one line (a smaller change).
             "learned": [{"bot": "technical-support", "subject": "Hold the phone still at the wall while calibrating",
                          "rule": "Calibration: hold the phone still at the wall, not panning.",
                          "source": 'task Ben in Call back owners whose calibration failed twice: "Calibration: hold the '
                                    'phone still at the wall, not panning."',
                          "summary": "Read the packet. Saved the calibration callback rule."}]},
            {"days": 1, "items": [
                {"task": "cal-units", "author": "human:ben", "hours": 6,
                 "label": "Ben in Decide whether owners get a unit swap after two failed calibrations",
                 "text": "After two failed calibrations, offer the owner a unit swap. Do not book a third redo.",
                 "learnable": 0.8, "scores": {"technical-support": 0.8, "support-lead": 0.6, "escalations": 0.5}}]},
            {"days": 0, "items": [
                {"task": "cal-callbacks", "author": "human:ben", "hours": 3,
                 "label": "Ben in Call back owners whose calibration failed twice",
                 "text": "Correction: ask customers to set the phone on a flat surface before calibrating.",
                 "learnable": 0.95, "scores": {"technical-support": 0.95}},
                {"task": "ticket-replies", "author": "human:ben", "hours": 5,
                 "label": "Ben in Draft replies for the open support tickets",
                 "text": "Send login help in this order: version check, then the reset link, then the call.",
                 "learnable": 0.85, "scores": {"support-lead": 0.8, "support": 0.9}}],
             # The technical-support bot works the correction in its packet and saves it as a working rule.
             "learned": [{"bot": "technical-support",
                          "subject": "Learned from Ben: set the phone on a flat surface before calibrating",
                          "rule": "Before calibrating, ask the customer to set the phone on a flat surface.",
                          "source": 'task Ben in Call back owners whose calibration failed twice: "Correction: ask '
                                    'customers to set the phone on a flat surface before calibrating."',
                          "summary": "Read the packet. Saved the flat-surface correction as a working rule."}]},
        ],
    },
    # Tico release updates: two bots subscribe to the Tico feed, and hear about each release after its first.
    "feed": {
        "subscribed_days": 6,
        "subscribers": ["product-manager", "botops"],
        "releases": [
            {"days": 5.5, "tag": "v0.5.52", "name": "Tico 0.5.52",
             "body": "### Added\n- Updates: a subscribed bot hears about new Tico releases as quiet notes.\n"
                     "### Fixed\n- A run that was cut off resumes from its last save."},
            {"days": 3, "tag": "v0.5.53", "name": "Tico 0.5.53",
             "body": "### Added\n- Run guard: a loop of automation runs is held for a person to release or drop.\n"
                     "### Changed\n- Learnings packets name the source of each item."},
            {"days": 1, "tag": "v0.5.54", "name": "Tico 0.5.54",
             "body": "### Fixed\n- A scheduled run absorbed by an open task is counted as dropped.\n"
                     "### Changed\n- Automation runs show each step's outputs."},
        ],
    },
}


def seed(b):
    from ..demo_seed import TOKENS   # the demo's people sign in with these, as the API expects
    store = b.store
    aids = _automations(b)
    for spec in _oldest_first(CONTENT["events"]):
        _event(b, spec, aids)
    _mail_loop(b, aids)
    _hold(b, aids, TOKENS)
    _nightly(b, aids["nightly"])
    _learn(b)
    _feed(b)
    store.automations.drain()


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _oldest_first(items):
    """The items in the order they happened, so each run is the newest one when its own trigger is processed."""
    return sorted(items, key=lambda i: -(i["at"][0] * 24 + i["at"][1]))


def _at(b, days, hours):
    b.clock.at = b.ago(days=days, hours=hours)


def _bot(c, slug):
    return (H.resolve_bot(c, slug) or {}).get("id", slug)


def _automations(b):
    """Each automation saved through the API, as its owner's manager (Ana), at the start of the week."""
    _at(b, CONTENT["created_days"], 0)
    ids = {}
    for spec in CONTENT["automations"]:
        trigger = dict(spec["trigger"])
        body = {"name": spec["name"], "owner": spec["owner"], "trigger": trigger, "steps": spec["steps"]}
        body.update({k: spec[k] for k in ("destinations", "enabled") if k in spec})
        ids[spec["key"]] = b.call("POST", "automations", body)["id"]
    return ids


def _run_task(b, aid, trigger_ref=None):
    """The task an automation's run opened (its first action that made one), or None. The newest run's, or with a
    trigger_ref, the run that trigger started."""
    for run in b.call("GET", f"automations/{aid}/runs")["runs"]:
        if trigger_ref is not None and run.get("trigger_ref") != trigger_ref:
            continue
        for step in run.get("steps") or []:
            task = (step.get("outputs") or {}).get("task")
            if task:
                return task
    return None


def _close(b, aid, closer, note, trigger_ref=None, read_by=None):
    """The closer bot, which owns the task the automation opened, marks it done a couple of hours on. Never past now: a
    close made at this moment still falls a minute before it, so nothing the demo writes is dated after the build.
    The person `read_by`, if any, then reads the task's thread to its last message."""
    task = _run_task(b, aid, trigger_ref)
    if not task:
        return
    b.clock.at = min(b.clock.at + timedelta(hours=CONTENT["close_after_hours"]), b.now - CLOSE_MARGIN)
    b.write(lambda c: H.task_update(c, H.bot_actor(_bot(c, closer)), task, status="done", note=note))
    if read_by:
        _read(b, task, read_by)


def _read(b, task, person):
    """The person reads the task's thread through the thread API, as their thread view does: up to its last message."""
    from ..demo_seed import TOKENS
    with b.store.read() as c:
        thread = H.task(c, task)["conversation_id"]
        last = c.execute("SELECT last_seq FROM conversations WHERE id=?", (thread,)).fetchone()[0]
    b.call("POST", f"threads/{thread}/read", {"seq": last}, token=TOKENS[person])


def _spec(key):
    return next(a for a in CONTENT["automations"] if a["key"] == key)


def _event(b, spec, aids):
    """One event: the hub's hook (what a real event does), then the runs it queued, drained at that moment."""
    _at(b, *spec["at"])
    b.write(lambda c: H.hook(c, spec["name"], spec["subject"], spec["data"], spec["actor"]))
    b.store.automations.drain()
    if spec.get("close"):
        auto = _spec(spec["close"])
        _close(b, aids[spec["close"]], auto["closer"], spec.get("note") or auto["note"],
               read_by=auto.get("read_by"))


def _message(item):
    """A mail as the mail connector hands it to `automations.on_mail`."""
    return SimpleNamespace(msg_id=item["id"], thread_id=item["id"], from_addr=item["from"], from_header="",
                           to=[CONTENT["mailbox"]], cc=[], subject=item["subject"], snippet=item["subject"],
                           body=item["subject"], labels=["INBOX"], list_id=None, has_unsubscribe=False,
                           attachments=[], is_internal=False, date=_stamp(item["at"]))


def _mail_item(b, item, aids):
    _at(b, *item["at"])
    item = {**item, "at": b.clock.at}
    b.write(lambda c: automations.on_mail(c, CONTENT["mailbox"], _message(item)))
    b.store.automations.drain()
    if item.get("close"):
        auto = _spec(item["close"])
        _close(b, aids[item["close"]], auto["closer"], auto["note"],
               trigger_ref=f"mail:{CONTENT['mailbox']}:{item['id']}", read_by=auto.get("read_by"))


def _mail_loop(b, aids):
    """Each mail in its turn (drained as it goes), oldest first, then the loop, which the run guard holds in one go, so
    no run is started before the hold is in place."""
    for item in _oldest_first(CONTENT["mail"]):
        _mail_item(b, item, aids)
    loop = CONTENT["loop"]
    _at(b, *loop["at"])
    start = b.clock.at

    def work(c):
        for n in range(loop["count"]):
            b.clock.at = start + timedelta(seconds=loop["seconds"] * n)
            item = {"id": f"{loop['id']}-{n:03d}", "from": loop["from"], "subject": loop["subject"]}
            automations.on_mail(c, CONTENT["mailbox"], _message({**item, "at": b.clock.at}))
    b.write(work)
    b.store.automations.drain()


def _hold(b, aids, tokens):
    """Ben drops the held runs through the API, as the hold's Needs-you task asks."""
    spec = CONTENT["hold"]
    _at(b, *spec["at"])
    b.call("POST", f"automations/{aids[spec['automation']]}/held", {"action": spec["action"]},
           token=tokens[spec["by"]])


def _nightly(b, aid):
    """The schedule's own bookkeeping, then each occurrence through `fire`, as the scheduler's tick does it. The
    previous night's ledger task is finished before the next night, so no occurrence is absorbed."""
    spec = _spec("nightly")
    zone = spec["trigger"]["tz"]
    tz = ZoneInfo(zone)
    today = b.now.astimezone(tz).date()
    for back in range(CONTENT["nightly_days"], -1, -1):
        due = datetime.combine(today - timedelta(days=back), time(2), tzinfo=tz).astimezone(timezone.utc)
        after = next_due(spec["trigger"]["cron"], due, zone)
        if due > b.now:
            # Tonight's occurrence has not happened yet: the schedule is simply due at that time, with no run.
            def upcoming(c, due=due):
                c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_stamp(due), aid))
            b.write(upcoming)
            continue
        b.clock.at = due

        def work(c, due=due, after=after):
            c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_stamp(due), aid))
            automations.fire(c, "schedule", {"schedule": {"at": _stamp(due), "cron": spec["trigger"]["cron"],
                                                           "tz": zone}},
                             f"schedule:{_stamp(due)}", only=aid)
            c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_stamp(after), aid))
        b.write(work)
        b.store.automations.drain()
        _close(b, aid, spec["closer"], spec["note"], read_by=spec.get("read_by"))


def _learn(b):
    """One Learn run per day, carried by Update turns before the next day."""
    today = learnings.local(b.now).date()
    previous = None
    work_after = timedelta(minutes=CONTENT["learnings"]["work_after_minutes"])
    for spec in CONTENT["learnings"]["nights"]:
        day = today - timedelta(days=spec["days"])
        run_at = datetime.combine(day, time(learnings.HOUR), tzinfo=learnings.ZONE).astimezone(timezone.utc)
        if run_at + work_after + WORK_WINDOW > b.now:
            break   # this run and its delivery turns have not finished yet
        if previous:
            b.clock.at = run_at - timedelta(hours=2)
            _finish_deliveries(b, previous)
        b.clock.at = run_at
        previous = _night(b, spec, day.isoformat(), run_at)
        for learned in spec.get("learned", []):
            b.clock.at = run_at + work_after
            _run(b, learned["bot"], dict(previous["deliveries"])[learned["bot"]], learned["summary"], learned)


def _night(b, spec, night, run_at):
    box = {}

    def work(c):
        run_id = learnings.claim(c, night)
        items = []
        for n, row in enumerate(spec["items"], 1):
            ref = {"task": b.tasks[row["task"]]} if row["task"] in b.tasks else {}
            items.append({"key": f"{night}-{n}", "source": "task", "ref": ref, "author": row["author"],
                          "label": row["label"], "text": row["text"],
                          "created_at": learnings.utc(run_at - timedelta(hours=row["hours"])),
                          "learnable": row["learnable"],
                          "scores": {_bot(c, slug): score for slug, score in row["scores"].items()}})
        qset = {"thresholds": {"relevant": 0.6, "noise": 0.2, "per_recipient": learnings.PER_RECIPIENT}}
        chosen = learnings.packets(items, qset, limit=15)
        team = {slug: {"name": (H.bot(c, slug) or {}).get("display_name") or slug} for slug in chosen}
        detail = {}
        _, routed = learnings.dispatch(c, run_id, night, items, chosen, team, detail)
        learnings.advance(c, items, learnings.utc(run_at))
        deliveries = []
        if chosen:
            updates.build_queue(c, night, updates.kind_for(night))
            for recipient in chosen:
                row = c.execute("SELECT * FROM update_queue WHERE bot=? AND day=?", (recipient, night)).fetchone()
                updates.send_request(c, row, delivery=True)
                deliveries.append((recipient, row["id"]))
        box.update(run=run_id, read=len(items), routed=routed, detail=detail, deliveries=deliveries)
    b.write(work)
    b.clock.at = run_at + FINISH_AFTER
    learnings.finish(b.store, box["run"], "done" if box["routed"] else "quiet", box["detail"],
                     read=box["read"], routed=box["routed"])
    b.write(lambda c: learnings.refresh_details(c, box["run"]))
    box["run_at"] = run_at
    return box


def _run(b, slug, batch_id, summary, learned):
    """A bot takes its Update delivery, reports a memory commit and completes its turn."""
    box = {}
    def request(c):
        box["message"] = c.execute("SELECT message_id FROM update_queue WHERE id=?", (batch_id,)).fetchone()[0]
    b.write(request)
    for _ in range(MAX_AHEAD_JOBS):
        claim = _claim(b, slug)
        if claim is None:
            raise RuntimeError(f"{slug} was handed no job for Update {batch_id}")
        aid = claim["id"]
        mine = (claim.get("message") or {}).get("id") == box["message"]
        b.call("POST", f"attempts/{aid}/started", {"thread_id": claim["conversation"]["id"]}, token=b.mac["token"])
        if mine and learned:
            b.call("POST", f"bots/{slug}/memory/report", {"commits": [_commit(slug, learned, aid, b.clock.at)]},
                   token=b.mac["token"])
        text = summary + f"\nlearnings_saved: {int(bool(learned))}" if mine else ""
        b.call("POST", f"attempts/{aid}/complete", {"outcome": "completed", "text": text, "last_seq": 0},
               token=b.mac["token"])
        if mine:
            def settle(c):
                row = c.execute("SELECT * FROM update_queue WHERE id=?", (batch_id,)).fetchone()
                updates.settle_sent(c, row)
            b.write(settle)
            _keep_focus(b, slug)
            return
    raise RuntimeError(f"{slug} has more than {MAX_AHEAD_JOBS} jobs queued ahead of Update {batch_id}")


def _keep_focus(b, slug):
    """A finished run leaves its bot idle with no focus, as the product does. The company's story gives each bot its
    focus, so after the run the bot has the focus it had: the run came and went, and the story's status stays."""
    from ..demo_content import FOCUS
    if FOCUS.get(slug):
        b.write(lambda c: H.status_set(c, H.KEEPER, slug, focus=FOCUS[slug]))


def _claim(b, slug):
    """The computer's next job for the bot, or None. The computer polls as its runner does: an empty claim is a pass,
    and the next poll comes CLAIM_POLL later on the demo clock. It takes work only after two minutes of unbroken
    contact, so a single claim at one instant would never be handed a job."""
    for _ in range(CLAIM_TRIES):
        claim = b.call("POST", "jobs/claim", {"bot": slug}, token=b.mac["token"])["attempt"]
        if claim:
            return claim
        b.clock.at += CLAIM_POLL
    return None


def _commit(slug, learned, aid, at):
    """The memory commit a bot's run makes: one working rule in memory/learnings.md, sourced to the item it came from."""
    diff = ("--- a/memory/learnings.md\n+++ b/memory/learnings.md\n@@ -1 +1,2 @@\n## Working rules\n"
            f"+- {learned['rule']} [source: {learned['source']}]\n")
    return {"sha": hashlib.sha1(f"{slug}|{learned['subject']}|{at.isoformat()}".encode()).hexdigest(),
            "subject": learned["subject"], "author": slug, "committed": _stamp(at),
            "files": ["memory/learnings.md"], "diff": diff, "attempt": aid}


def _finish_deliveries(b, night):
    for recipient, batch_id in night["deliveries"]:
        box = {}
        def state(c, box=box, batch_id=batch_id):
            box["state"] = c.execute("SELECT state FROM update_queue WHERE id=?", (batch_id,)).fetchone()[0]
        b.write(state)
        if box["state"] == "sent":
            _run(b, recipient, batch_id, CONTENT["learnings"]["read"], None)


def _release(b, release):
    return {"tag_name": release["tag"], "name": release["name"], "body": release["body"],
            "published_at": _stamp(b.clock.at), "draft": False, "prerelease": False}


def _feed(b):
    """Two bots subscribe to the Tico feed through the bot definition API. Each release is stored as the feed fetched
    it, then delivered; the first release is only the starting point, so the bots hear the next two."""
    feed = CONTENT["feed"]
    _at(b, feed["subscribed_days"], 0)
    for slug in feed["subscribers"]:
        revision = b.call("GET", f"bots/{slug}")["revision"]
        b.call("POST", f"bots/{slug}/definition", {"update_subscriptions": ["tico"], "expected_revision": revision})
    for release in feed["releases"]:
        _at(b, release["days"], 0)
        item = update_feeds.tico_item(_release(b, release))
        b.write(lambda c, item=item: update_feeds.store_items(c, "tico", [item]))
        b.write(lambda c: update_feeds.deliver(c))
