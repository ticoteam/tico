# pyright: strict
"""Company › Risk (ui/app/risk.ts): the risks Acme Lights carries and the matters under them.

A risk is a tag of kind risk (backend/risk.py); a matter is a task tagged with it, with its facts (kind, party, amount,
forum). Everything goes through the API as Ana, the way the Legal page owner's `tico risk` calls would: one risk is live
(a dispute arrived two days ago), one is open, two are quiet (one with a closed matter, one with none). The parties are
the Contacts section's organizations (backend/demo_sections/directory.py runs first), and a rule and a document link to
the risks they guard. Dates (rule_due_dates with a basis) sit under standing rules on three risks: a fire board's
order tomorrow, a court's dates on Cedar Venues (one theirs to meet), a privacy reply met with a link, and an insurance
premium that passed with no proof, so Product safety reads Unconfirmed and is live. Bright Homes' dispute carries key
dates of its own (`matter_dates`), which show on the owner's calendar. Nothing names a real company or person.
"""

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

OWNER = "contracts-manager"
RISKS = (
    # (label, its page text)
    ("Customer disputes", "Claims from customers over orders, installs and invoices. Each is a task with its facts."),
    ("Product safety", "A kit that overheats, sparks or fails a fire check, and what a regulator asks about it."),
    ("Data privacy", "Requests to see or delete what we hold about a person, and anything that leaks it."),
    ("Trademarks", "Our name and marks, and anyone using them."),
)
MATTERS = (
    # (days ago, risk, title, party (a Contacts organization), facts, done)
    (
        2,
        "Customer disputes",
        "Bright Homes disputes the October invoice",
        "Bright Homes Retail",
        {
            "kind": "dispute",
            "amount": 1_240_000,
            "currency": "USD",
            "claim": "Says 40 kits arrived dead",
            "forum": "Demand",
        },
        False,
    ),
    (
        24,
        "Customer disputes",
        "Cedar Venues hall damage claim",
        "Cedar Venues",
        {"kind": "dispute", "amount": 480_000, "currency": "USD", "claim": "Ceiling paint", "forum": "Small claims"},
        False,
    ),
    (
        40,
        "Customer disputes",
        "Maple Street late delivery credit",
        "Maple Street Stores",
        {"kind": "dispute", "amount": 95_000, "currency": "USD", "forum": "Demand"},
        True,
    ),
    (
        18,
        "Product safety",
        "Fire board asks about the rig's wiring",
        "Metro Fire Safety Board",
        {"kind": "notice", "forum": "Fire board"},
        False,
    ),
    (
        30,
        "Data privacy",
        "Delete a former customer's account",
        None,
        {"kind": "request"},
        True,
    ),
)
KEY_DATES_MATTER = "Bright Homes disputes the October invoice"
KEY_DATES = (
    # (days from today, what it is for, not before or due by, a time in the company's zone or the whole day)
    (5, "Their delivery records", "due_at", ""),
    (12, "Our photos of the kits", "due_at", ""),
    (19, "Mediation call", "due_at", "T10:00"),
)
RULE = "A fire board letter gets Ana's OK before any reply."
SOURCE = "Customer terms"
DATES = (
    # (standing rule, its risk, ((days from today, title, basis, matter title or None, met with this link or None), ...))
    (
        "Meet every date a regulator sets.",
        "Product safety",
        (
            (1, "Fire board inspection records", "order", "Fire board asks about the rig's wiring", None),
            (-17, "Liability premium", "contract", None, None),
        ),
    ),
    (
        "Meet every date a court sets on a customer dispute.",
        "Customer disputes",
        (
            (13, "Cedar Venues discovery", "theirs", "Cedar Venues hall damage claim", None),
            (20, "Cedar Venues witness list", "order", "Cedar Venues hall damage claim", None),
            (27, "Cedar Venues hearing", "order", "Cedar Venues hall damage claim", None),
            (
                -8,
                "Cedar Venues photos",
                "order",
                "Cedar Venues hall damage claim",
                "https://files.acme.example/cedar-photos",
            ),
        ),
    ),
    (
        "Answer every privacy request within the time the law gives.",
        "Data privacy",
        (
            (
                -25,
                "Deletion reply",
                "law",
                "Delete a former customer's account",
                "https://files.acme.example/deletion-reply",
            ),
        ),
    ),
)


def _key(label: str) -> str:
    return "risk:" + "-".join(label.lower().split())


def seed(b: Any) -> None:  # backend.demo_seed.Builder
    b.at(days=45)
    for label, markdown in RISKS:
        b.call("POST", "risks", {"label": label, "markdown": markdown})
    for days, risk, title, party, facts, done in MATTERS:
        b.at(days=days)
        made = b.call(
            "POST",
            "tasks",
            {"owner": OWNER, "title": title, "body": f"{title}. The facts are on the task.", "labels": [_key(risk)]},
        )
        tid = str(made["task"]["id"])
        b.tasks["risk:" + title] = tid
        body = dict(facts)
        if party:
            found = b.call("GET", "entities/find/organization?" + urlencode({"name": party, "limit": 1}))["rows"]
            body["party_entity_id"] = found[0]["id"]
        b.call("POST", f"tasks/{tid}/matter", body)
        if done:
            b.at(days=days - 3)
            b.call("POST", f"tasks/{tid}/done", {"note": "Settled."})
    b.at(days=1)
    rule = b.call("POST", "rules", {"kind": "internal", "text": RULE, "who": {"everyone": True}})["id"]
    b.call("POST", f"risks/{_key('Product safety')}/links", {"linkable_type": "org_rule", "linkable_id": rule})
    document = b.call("POST", "entities", {"type": "document", "match": {"name": SOURCE}, "name": SOURCE})["id"]
    b.call("POST", f"risks/{_key('Customer disputes')}/links", {"linkable_type": "entity", "linkable_id": document})
    # Dates count from the server's today, so they stay where they are put however late the demo is built.
    today = b.now.date()
    # Bright Homes' dispute has key dates of its own: their documents, ours, then a call with the mediator.
    for offset, label, field, at in KEY_DATES:
        on = (today + timedelta(days=offset)).isoformat() + at
        b.call("POST", f"tasks/{b.tasks['risk:' + KEY_DATES_MATTER]}/matter/dates", {"label": label, field: on})
    for text, risk, dates in DATES:
        b.at(days=30)
        standing = b.call("POST", "rules", {"kind": "internal", "text": text, "who": {"everyone": True}})["id"]
        b.call("POST", f"risks/{_key(risk)}/links", {"linkable_type": "org_rule", "linkable_id": standing})
        for offset, title, basis, matter, link in dates:
            on = (today + timedelta(days=offset)).isoformat()
            body = {
                "first_due": on,
                "repeats": "once",
                "owner": OWNER,
                "timezone": "UTC",
                "title": title,
                "basis": basis,
            }
            if matter:
                body["task_id"] = b.tasks["risk:" + matter]
            b.at(days=30)
            made = b.call("POST", f"rules/{standing}/due", body)["id"]
            if link:
                b.at(days=-offset + 1)
                b.call("POST", f"rule-due/{made}/satisfied", {"due_on": on, "proof": {"kind": "link", "ref": link}})
