"""Automations with runs (a loop the run guard held and Ben dropped, declined cards, nightly bookkeeping, device and
release checks), a week of Learnings nights, and Tico release updates delivered to two bots.

Everything goes through the code a real install runs: automations are saved through the API, events and mail reach the
automation engine through its own entry points (`hubdb.hook`, `automations.on_mail`), the engine drains the runs, and
the Learnings and update-feed modules write their own rows. The only direct writes are the scheduler's own bookkeeping
(the next due time of a schedule) and reads of the packet tasks a night made.
"""

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .. import automations, learnings, update_feeds
from ..scheduler import next_due
from ..store import H

CONTENT = {
    "mailbox": "ben@acme.example",
    "close_after_hours": 2,
    "created_days": 6,           # the automations are saved this many days back, before their first run
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
         "closer": "ar-followup", "note": "Retry paid.",
         "trigger": {"type": "event", "name": "finance.card.declined"},
         "steps": [{"id": "chase", "type": "action", "do": "create_task", "args": {
             "title": "Card declined for {{event.data.account}}",
             "body": "Retry the charge once. If it fails again, email the account.",
             "owner": "ar-followup"}}]},
        {"key": "nightly", "name": "Nightly orders to bookkeeping", "owner": "person:ana",
         "closer": "bookkeeper", "note": "Posted. Nothing to flag.",
         "trigger": {"type": "schedule", "cron": "0 2 * * *", "tz": "America/Los_Angeles"},
         "steps": [{"id": "post", "type": "action", "do": "create_task", "args": {
             "title": "Post yesterday's orders to the ledger",
             "body": "Check every marketplace and store order is posted, and flag any that do not match.",
             "owner": "bookkeeper"}}]},
        {"key": "rings", "name": "Firmware rings to health monitor", "owner": "person:eli",
         "closer": "health-monitor", "note": "Restarts stayed flat.",
         "trigger": {"type": "event", "name": "custom.firmware.ring_done"},
         "steps": [{"id": "watch", "type": "action", "do": "create_task", "args": {
             "title": "Watch {{event.data.ring}} for offline restarts",
             "body": "Compare offline restarts with the last ring. Report any rise to Eli.",
             "owner": "health-monitor"}}]},
        {"key": "deploys", "name": "Finished deploys to release notes", "owner": "person:eli",
         "closer": "release-notes", "note": "Notes posted.",
         "trigger": {"type": "event", "name": "custom.deploy.finished"},
         "steps": [{"id": "notes", "type": "action", "do": "create_task", "args": {
             "title": "Write the release notes for {{event.data.version}}",
             "body": "Use the merged pull requests since the last release.",
             "owner": "release-notes"}}]},
    ],
    # Events the company's systems report, oldest first within a day. `at` is [days ago, hours before that].
    "events": [
        {"at": [1, 7.0], "name": "custom.calibration.failed", "subject": "unit/U-1042", "actor": "bot:support-qa",
         "data": {"unit": "U-1042", "attempt": 1}, "close": "calibration"},
        {"at": [1, 6.8], "name": "custom.calibration.failed", "subject": "unit/U-1057", "actor": "bot:support-qa",
         "data": {"unit": "U-1057", "attempt": 1}, "close": "calibration"},
        {"at": [1, 6.6], "name": "custom.calibration.failed", "subject": "unit/U-1061", "actor": "bot:support-qa",
         "data": {"unit": "U-1061", "attempt": 1}, "close": "calibration"},
        {"at": [1, 6.4], "name": "custom.calibration.failed", "subject": "unit/U-1073", "actor": "bot:support-qa",
         "data": {"unit": "U-1073", "attempt": 1}, "close": "calibration"},
        {"at": [1, 6.2], "name": "custom.calibration.failed", "subject": "unit/U-1088", "actor": "bot:support-qa",
         "data": {"unit": "U-1088", "attempt": 1}, "close": "calibration"},
        {"at": [1, 6.0], "name": "custom.calibration.failed", "subject": "unit/U-1093", "actor": "bot:support-qa",
         "data": {"unit": "U-1093", "attempt": 2}, "close": "calibration", "note": "Could not fix. Sent to Ben."},
        {"at": [1, 5.8], "name": "custom.calibration.failed", "subject": "unit/U-1104", "actor": "bot:support-qa",
         "data": {"unit": "U-1104", "attempt": 1}, "close": "calibration"},
        {"at": [1, 5.6], "name": "custom.calibration.failed", "subject": "unit/U-1110", "actor": "bot:support-qa",
         "data": {"unit": "U-1110", "attempt": 1}, "close": "calibration"},
        {"at": [1, 5.4], "name": "custom.calibration.failed", "subject": "unit/U-1126", "actor": "bot:support-qa",
         "data": {"unit": "U-1126", "attempt": 1}, "close": "calibration"},
        {"at": [1, 5.2], "name": "custom.calibration.failed", "subject": "unit/U-1131", "actor": "bot:support-qa",
         "data": {"unit": "U-1131", "attempt": 1}, "close": "calibration"},
        {"at": [1, 5.0], "name": "custom.calibration.failed", "subject": "unit/U-1140", "actor": "bot:support-qa",
         "data": {"unit": "U-1140", "attempt": 1}, "close": "calibration"},
        {"at": [1, 4.8], "name": "custom.calibration.failed", "subject": "unit/U-1152", "actor": "bot:support-qa",
         "data": {"unit": "U-1152", "attempt": 1}, "close": "calibration"},
        {"at": [1, 4.6], "name": "custom.calibration.failed", "subject": "unit/U-1158", "actor": "bot:support-qa",
         "data": {"unit": "U-1158", "attempt": 2}, "close": "calibration", "note": "Could not fix. Sent to Ben."},
        {"at": [1, 4.4], "name": "custom.calibration.failed", "subject": "unit/U-1163", "actor": "bot:support-qa",
         "data": {"unit": "U-1163", "attempt": 1}, "close": "calibration"},
        {"at": [4, 8.0], "name": "custom.review.posted", "subject": "review/R-2201", "actor": "bot:marketplace",
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
        {"at": [1, 8.0], "name": "custom.review.posted", "subject": "review/R-2240", "actor": "bot:marketplace",
         "data": {"stars": 3, "product": "Home Pro", "text": "Fine for the price."}},
        {"at": [4, 11.0], "name": "finance.card.declined", "subject": "charge/C-7781", "actor": "bot:bookkeeper",
         "data": {"account": "Harbor Lane Storefront", "amount": "$1,200.00"}, "close": "declined"},
        {"at": [1, 9.0], "name": "finance.card.declined", "subject": "charge/C-7830", "actor": "bot:bookkeeper",
         "data": {"account": "Pinecrest Hotel", "amount": "$449.00"}},
        {"at": [5, 10.0], "name": "custom.firmware.ring_done", "subject": "ring/staff", "actor": "bot:firmware",
         "data": {"ring": "staff units", "version": "2.4"}, "close": "rings"},
        {"at": [2, 10.0], "name": "custom.firmware.ring_done", "subject": "ring/5-percent", "actor": "bot:firmware",
         "data": {"ring": "5% of units", "version": "2.4"}},
        {"at": [5, 15.0], "name": "custom.deploy.finished", "subject": "deploy/cloud-1.8.1", "actor": "bot:devops",
         "data": {"version": "cloud 1.8.1"}, "close": "deploys"},
        {"at": [3, 15.0], "name": "custom.deploy.finished", "subject": "deploy/app-3.1.0", "actor": "bot:devops",
         "data": {"version": "app 3.1.0"}, "close": "deploys"},
        {"at": [1, 15.0], "name": "custom.deploy.finished", "subject": "deploy/cloud-1.8.2", "actor": "bot:devops",
         "data": {"version": "cloud 1.8.2"}},
    ],
    # Mail to Ben's inbox. Orders come from the marketplace; the rest are questions the filter drops.
    "mail": [
        {"at": [5, 9.0], "id": "order-4390", "from": "orders@bigmarket.example",
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
        {"at": [0, 8.0], "id": "order-4478", "from": "orders@bigmarket.example",
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
                {"task": "release-post", "author": "human:ana", "hours": 8,
                 "label": "Ana in Draft the spring release announcement",
                 "text": "Creators get the Storefront demo a week before the launch post.",
                 "learnable": 0.85, "scores": {"creators": 0.9, "marketing-lead": 0.8, "content": 0.6}}]},
            {"days": 2, "items": [
                {"task": "howtos", "author": "human:ben", "hours": 5,
                 "label": "Ben in Outline three how-to posts for the help centre",
                 "text": "Calibration: hold the phone still at the wall, not panning. A moving phone is the usual cause.",
                 "learnable": 0.9, "scores": {"technical-support": 0.9, "support": 0.8, "content": 0.7}}]},
            {"days": 1, "items": [
                {"task": "brightline", "author": "human:ana", "hours": 6,
                 "label": "Ana in Prepare the Brightline renewal brief",
                 "text": "Renewal calls open with usage, not price.",
                 "learnable": 0.8, "scores": {"sales-lead": 0.9, "account-manager": 0.9, "sales": 0.7}}]},
            {"days": 0, "items": [
                {"task": "ticket-replies", "author": "human:ben", "hours": 5,
                 "label": "Ben in Draft replies for the open support tickets",
                 "text": "Send login help in this order: version check, then the reset link, then the call.",
                 "learnable": 0.85, "scores": {"support-lead": 0.8, "support": 0.9}}]},
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
    for spec in CONTENT["events"]:
        _event(b, spec, aids)
    _mail_loop(b, aids)
    _hold(b, aids, TOKENS)
    _nightly(b, aids["nightly"])
    _learn(b)
    _feed(b)
    store.automations.drain()


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


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
        ids[spec["key"]] = b.call("POST", "automations", body)["id"]
    return ids


def _newest_task(b, aid):
    """The task the automation's newest run opened (its first action that made one), or None."""
    for run in b.call("GET", f"automations/{aid}/runs")["runs"]:
        for step in run.get("steps") or []:
            task = (step.get("outputs") or {}).get("task")
            if task:
                return task
    return None


def _close(b, aid, closer, note):
    """The closer bot, which owns the newest task the automation opened, marks it done a couple of hours on."""
    task = _newest_task(b, aid)
    if not task:
        return
    b.clock.at += timedelta(hours=CONTENT["close_after_hours"])
    b.write(lambda c: H.task_update(c, H.bot_actor(_bot(c, closer)), task, status="done", note=note))


def _spec(key):
    return next(a for a in CONTENT["automations"] if a["key"] == key)


def _event(b, spec, aids):
    """One event: the hub's hook (what a real event does), then the runs it queued, drained at that moment."""
    _at(b, *spec["at"])
    b.write(lambda c: H.hook(c, spec["name"], spec["subject"], spec["data"], spec["actor"]))
    b.store.automations.drain()
    if spec.get("close"):
        auto = _spec(spec["close"])
        _close(b, aids[spec["close"]], auto["closer"], spec.get("note") or auto["note"])


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
        _close(b, aids[item["close"]], auto["closer"], auto["note"])


def _mail_loop(b, aids):
    """Each mail in its turn (drained as it goes), then the loop, which the run guard holds in one go, so no run is
    started before the hold is in place."""
    for item in CONTENT["mail"]:
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
        b.clock.at = due
        after = next_due(spec["trigger"]["cron"], due, zone)

        def work(c, due=due, after=after):
            c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_stamp(due), aid))
            automations.fire(c, "schedule", {"schedule": {"at": _stamp(due), "cron": spec["trigger"]["cron"],
                                                           "tz": zone}},
                             f"schedule:{_stamp(due)}", only=aid)
            c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_stamp(after), aid))
        b.write(work)
        b.store.automations.drain()
        if back:
            _close(b, aid, spec["closer"], spec["note"])


def _learn(b):
    """One night per local day, each claimed and dispatched as the nightly run does. The packets a night's bots got
    are finished by those bots before the next night, so none is left unread behind a newer one."""
    today = learnings.local(b.now).date()
    previous = None
    for spec in CONTENT["learnings"]["nights"]:
        day = today - timedelta(days=spec["days"])
        run_at = datetime.combine(day, time(learnings.HOUR), tzinfo=learnings.ZONE).astimezone(timezone.utc)
        if previous:
            b.clock.at = run_at - timedelta(hours=2)
            _finish_packets(b, previous)
        b.clock.at = run_at
        previous = _night(b, spec, day.isoformat(), run_at)


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
        chosen = learnings.packets(items, qset)
        team = {slug: {"name": (H.bot(c, slug) or {}).get("display_name") or slug} for slug in chosen}
        detail = {}
        _, routed = learnings.dispatch(c, run_id, night, items, chosen, team, detail)
        learnings.advance(c, items, learnings.utc(run_at))
        box.update(run=run_id, read=len(items), routed=routed, detail=detail,
                   deliveries=[(r[0], r[1]) for r in c.execute(
                       "SELECT recipient, task_id FROM learning_deliveries WHERE run_id=?", (run_id,))])
    b.write(work)
    b.clock.at = run_at + timedelta(minutes=12)
    learnings.finish(b.store, box["run"], "done" if box["routed"] else "quiet", box["detail"],
                     read=box["read"], routed=box["routed"])
    return box


def _finish_packets(b, night):
    note = CONTENT["learnings"]["read"]

    def work(c):
        for recipient, task in night["deliveries"]:
            H.task_update(c, H.bot_actor(recipient), task, status="done", note=note)
    b.write(work)


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
