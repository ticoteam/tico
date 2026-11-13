"""The one list of named events: routines (`on:`), rules (WHEN), outgoing webhook subscriptions, `tico event-types` and
the docs all read it.

An event is a dot name in a group (`task.done`) with a one-line description and an `example`: the `data` object of a
typical payload, in Acme's words. "Send example" posts it and the docs show it. Every payload carries its event's
`version` (starts at 1); a change that breaks a receiver adds a new version, never edits one.

`aliases` are spellings that came before the catalog and still work (`recording.ready`). `sourceless` marks events
that are not a message to a bot and have no task, so a rule judges them with `rules.judge_event` (scopes team,
department and bot only). `outbound=False` marks those only rules see: an outgoing webhook cannot subscribe to them.
`webhook.<id>` (one incoming webhook) and `custom.<x>` (anything a bot or rule sends) are not listed one by one:
`canonical` lets them through.

This module imports nothing from Tico: runners and clients validate routines with it too.
"""

import re
from dataclasses import dataclass

CUSTOM = "custom."
# One incoming webhook's own event, `webhook.<id>` (backend/triggers.py): a routine may listen for it.
# `_tico` is the built-in trigger for Tico Team's notices: its leading underscore is why no one's trigger can take it.
_WEBHOOK_ID = re.compile(r"webhook\.(?:_tico|[a-z0-9][a-z0-9-]{0,39})")
_CUSTOM = re.compile(r"custom\.[a-z0-9][a-z0-9_.-]{0,59}")


@dataclass(frozen=True)
class Event:
    name: str  # "task.done"
    group: str  # "task"
    description: str  # one short line, plain words
    label: str  # the event in a few words, for pickers and summaries: "Task created"
    example: dict  # the `data` object of a typical payload; what "Send example" posts
    version: int = 1
    aliases: tuple = ()  # old spellings still accepted: ("recording.ready",)
    sourceless: bool = False  # no task and no message: rules judge it with judge_event
    outbound: bool = True  # an outgoing webhook may subscribe to it; False for events only rules see


# Ids in the examples are made up; they look like the real ones. Each example has the keys its producer sends.
_TASK = "5b1d2c0e-7f34-4a9e-8c1b-2f6a9d3e4b10"


def _task(**more):
    """A task event's facts (backend/task_effects.py): where the task is, who has it and who acted."""
    return {
        "id": _TASK,
        "title": "Renew the acme.example domain",
        "status": "open",
        "pipeline": "general",
        "stage": "general-todo",
        "owner_id": "bot:ops",
        "requester_id": "human:ana",
        "actor": "bot:ops",
        **more,
    }


def _change(field, old, new, move=None, **more):
    change = {"field": field, "from": old, "to": new, **({"move": move} if move else {})}
    return _task(change=change, **more)


def _build():
    found = []

    def add(name, description, label, example, aliases=(), sourceless=False, outbound=True):
        found.append(
            Event(name, name.split(".", 1)[0], description, label, example, 1, tuple(aliases), sourceless, outbound)
        )

    add("task.created", "A task was filed.", "Task created", _task(actor="human:ana", parent_id=None))
    add(
        "task.assigned",
        "A task was given to someone else.",
        "Task reassigned",
        _change("owner", "bot:ops", "bot:finance", owner_id="bot:finance", actor="human:ana"),
    )
    add(
        "task.step_changed",
        "A task moved to another stage of its pipeline, Done and Closed included.",
        "Task moved",
        _change("stage", "general-todo", "general-done", "done", status="done", stage="general-done"),
    )
    add("task.commented", "Someone commented on a task.", "Task commented", _task(actor="human:ana", comment_id="c1"))
    add(
        "task.done",
        "A task was finished.",
        "Task done",
        _change("status", "open", "done", "done", status="done", stage="general-done"),
    )
    add(
        "task.closed",
        "A task was closed without finishing.",
        "Task closed",
        _change("status", "open", "closed", "closed", status="closed", stage="general-closed", actor="human:ana"),
    )
    add(
        "task.reopened",
        "A finished or closed task was opened again.",
        "Task reopened",
        _change("status", "done", "open", "reopen", actor="human:ana"),
    )

    # Threads (docs/threads.md). Payloads are thin: ids and facts, never a message's words. An endpoint hears a thread
    # only if its owner can read the thread, message.held included. message.created is for outgoing
    # webhooks alone: a routine or rule does not hear every message.
    thread = {
        "id": "t1",
        "title": "Refund policy",
        "audience": "members",
        "owner_id": "human:ana",
        "root_id": "t1",
        "parent_id": None,
        "parent_message_id": None,
    }
    add("thread.created", "A thread was started.", "Thread started", thread)
    add(
        "message.created",
        "A message was posted to a thread (outgoing webhooks only).",
        "Message posted",
        {
            "id": "m1",
            "thread_id": "t1",
            "root_id": "t1",
            "parent_message_id": None,
            "seq": 4411,
            "sender_id": "human:ana",
            "recipient_id": None,
            "kind": "say",
        },
    )
    add(
        "message.held",
        "The org rules held a message before it reached its thread.",
        "Message held",
        {"id": "m2", "thread_id": "t1", "root_id": "t1", "sender_id": "bot:ops", "rule": "refund_terms", "hold": "h1"},
    )

    add(
        "bot.run_failed",
        "A bot's run failed.",
        "Bot run failed",
        {"bot": "ops", "run_id": "r1", "error_class": "login"},
    )
    add(
        "bot.held",
        "A bot was held: it hit a limit or kept failing.",
        "Bot held",
        {"bot": "ops", "reason": "budget", "window": "1h", "runs": 40, "budget": 30, "task": _TASK},
    )
    add(
        "bot.released",
        "A held bot was released.",
        "Bot released",
        {"bot": "ops", "reason": "budget", "released_by": "human:ana"},
    )
    add(
        "org_rule.retired",
        "A rule was retired and no longer applies to anyone.",
        "Rule retired",
        {
            "id": "r1",
            "kind": "internal",
            "text": "Send a status note by Friday.",
            "source_id": None,
            "retired_by": "human:ana",
        },
    )
    checked = {"hold": "h1", "operation": "message", "rules": ["r1"], "cached": False, "reviewer": "human:ana"}
    add("rules.blocked", "A bot's output broke a rule and was held.", "Output blocked", checked)
    add(
        "rules.escalated",
        "A bot's output broke a rule and was held for a person to review.",
        "Held for review",
        {**checked, "task": _TASK},
    )
    add(
        "rules.warned",
        "A bot's output broke a rule and went through with a warning.",
        "Output warned",
        {"operation": "message", "rules": ["r1"], "cached": False},
    )
    add(
        "rules.unchecked",
        "A bot's output went through without a rule check.",
        "Output not checked",
        {"operation": "message", "reason": "deadline", "rules": ["r1"]},
    )

    add(
        "release.published",
        "A release of the server's release repository was published.",
        "Release published",
        {"release": "v1.4.0", "repository": "acme/app", "url": "https://github.com/acme/app/releases/tag/v1.4.0"},
        aliases=("release_published",),
    )
    add(
        "meeting.ready",
        "A team meeting with a transcript was imported.",
        "Meeting ready",
        {"id": "m1", "title": "Acme weekly sync", "source": "recorder", "ended": "2026-03-02T17:30:00Z"},
        aliases=("recording.ready",),
    )
    add(
        "strategy.published",
        "A new version of the strategy was published.",
        "Strategy published",
        {"version": 3, "note": "Focus on Acme's renewals.", "published_by": "human:ana"},
    )
    add(
        "brand.published",
        "A new version of the brand page was published.",
        "Brand published",
        {"version": 2, "note": "Warmer voice for Acme's home visits.", "published_by": "human:ana"},
    )
    add(
        "market.insight.urgent",
        "An urgent market insight was reported.",
        "Urgent insight",
        {"id": "i1", "about": "Rival Co", "reported_by": "bot:market-analyst"},
    )
    add(
        "market.briefing",
        "The weekly market briefing is ready.",
        "Market briefing",
        {"week": "2026-W10", "bets": ["Renewals", "Self-serve onboarding"]},
    )
    add(
        "inbound.received",
        "New mail arrived for a person's inbox bot.",
        "Mail received",
        {"message_id": "g1", "inbox_id": "ib1", "kind": "email"},
    )
    add(
        "health.finding",
        "Tico found a problem on this install.",
        "Health finding",
        {
            "key": "computer-silent:mac",
            "check": "computer-silent",
            "kind": "computer",
            "severity": "warn",
            "title": "A computer has not reported for an hour",
        },
    )
    # Rules take these two as they happen (backend/triggers.py, backend/tico_notices.py); no endpoint hears them.
    add(
        "webhook.received",
        "An incoming webhook was accepted.",
        "Webhook received",
        {
            "trigger": {"id": "shop", "name": "Acme shop"},
            "delivery": "d1",
            "type": "order.created",
            "payload": {"order": 42, "total": "120.00"},
            "via": "direct",
        },
        sourceless=True,
        outbound=False,
    )
    add(
        "tico.notice",
        "A notice from the Tico team reached this install.",
        "Tico notice",
        {
            "id": "tn1",
            "kind": "info",
            "body": "Version 1.4.1 fixes a mail sync bug.",
            "url": "https://tico.team/notices/1",
        },
        sourceless=True,
        outbound=False,
    )
    # Finance's budget crossings (backend/finance/jobs.py): ids only, so whoever acts reads the budget in their own view.
    add(
        "finance.budget.warn",
        "A Finance budget reached its warning level this period.",
        "Budget warning",
        {"budget_id": "b1", "event_id": "e1", "period": "2026-10-01"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.budget.cutoff",
        "A Finance budget reached its limit this period.",
        "Budget limit reached",
        {"budget_id": "b1", "event_id": "e2", "period": "2026-10-01"},
        sourceless=True,
        outbound=False,
    )
    # Finance's bank feed, payments, cards and sync (backend/finance/banking.py, payments.py, sync.py): ids only too.
    add(
        "finance.feed.new",
        "A new bank or card transaction arrived in Finance.",
        "New transaction",
        {"feed_id": "f1", "event_id": "e3"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.feed.needs_category",
        "A bank transaction needs a category before it can be posted.",
        "Transaction needs a category",
        {"feed_id": "f1", "event_id": "e4"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.payment.sent",
        "A Finance payment settled at the bank.",
        "Payment sent",
        {"payment_id": "p1", "event_id": "e5"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.payment.failed",
        "A Finance payment failed, was returned or was rejected at the bank.",
        "Payment failed",
        {"payment_id": "p1", "why": "returned", "event_id": "e6"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.card.declined",
        "A card charge was declined.",
        "Card declined",
        {"card_id": "c1", "feed_id": "f2", "event_id": "e7"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.sync.failed",
        "Finance could not sync a bank connection.",
        "Bank sync failed",
        {"connection_id": "n1", "event_id": "e8"},
        sourceless=True,
        outbound=False,
    )
    # Finance's payables, receivables, cash alert and close (backend/finance/documents.py, jobs.py): ids only.
    add(
        "finance.bill.received",
        "A bill was recorded in Finance (by hand, upload or the bills address).",
        "Bill received",
        {"bill_id": "bl1", "origin": "email", "event_id": "e9"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.bill.due_soon",
        "An unpaid Finance bill is due in 7 days, in 1 day or today.",
        "Bill due soon",
        {"bill_id": "bl1", "stage": "due_in_7", "due_date": "2026-11-07", "event_id": "e10"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.invoice.overdue",
        "A Finance invoice is 1, 7, 30, 60 or 90 days overdue.",
        "Invoice overdue",
        {"invoice_id": "in1", "stage": "overdue_7", "due_date": "2026-10-24", "event_id": "e11"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.cash.below",
        "The company's cash dropped under the owner's alert amount (read the cash metric for amounts).",
        "Cash below alert",
        {"metric": "cash", "crossing": 1, "event_id": "e12"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.period.ready",
        "A month is ready to close in Finance.",
        "Month ready to close",
        {"month": "2026-10", "event_id": "e13"},
        sourceless=True,
        outbound=False,
    )
    add(
        "finance.vendor.renewal_due",
        "A vendor's renewal is 30 or 7 days away (its date is on the vendor).",
        "Vendor renewal due",
        {"contact_id": "c1", "stage": "renewal_in_7", "renewal_on": "2026-11-07", "event_id": "e14"},
        sourceless=True,
        outbound=False,
    )
    add(
        "custom.*",
        "Anything a bot or rule sends: custom.<name>.",
        "Custom events",
        {"note": "custom.<name> carries whatever its sender puts in data."},
    )
    return found


EVENTS: dict = {e.name: e for e in _build()}
_ALIASES = {alias: e.name for e in EVENTS.values() for alias in e.aliases}


def canonical(name):
    """The catalog name for `name` (an alias becomes its event), or None when it is not an event. `webhook.<id>` and
    `custom.<x>` pass through."""
    if not isinstance(name, str):
        return None
    if name in EVENTS:
        return name
    if name in _ALIASES:
        return _ALIASES[name]
    if _WEBHOOK_ID.fullmatch(name) or _CUSTOM.fullmatch(name):
        return name
    return None


def matches(pattern, name):
    """Whether `name` is `pattern`: exactly, or by prefix when the pattern ends with `*` (`task.*`, `*`)."""
    if not isinstance(pattern, str) or not isinstance(name, str):
        return False
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return pattern == name


def spellings(name):
    """Every spelling of the event `name` is: its catalog name and its aliases. A name that is no event is itself."""
    found = canonical(name) or name
    return (found,) + tuple(EVENTS[found].aliases if found in EVENTS else ())


def listing(group=None):
    """The catalog as the API shows it, optionally one group."""
    return [
        {
            "name": e.name,
            "group": e.group,
            "description": e.description,
            "label": e.label,
            "version": e.version,
            "example": e.example,
            "aliases": list(e.aliases),
            "sourceless": e.sourceless,
            "outbound": e.outbound,
        }
        for e in EVENTS.values()
        if group in (None, e.group)
    ]


def sourceless():
    """The names of the events rules judge without a task."""
    return tuple(e.name for e in EVENTS.values() if e.sourceless)
