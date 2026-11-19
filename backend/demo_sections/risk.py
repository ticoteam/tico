# pyright: strict
"""Company › Risk (ui/app/risk.ts): the risks Acme Lights carries and the matters under them.

A risk is a tag of kind risk (backend/risk.py); a matter is a task tagged with it, with its facts (kind, party, amount,
forum). Everything goes through the API as Ana, the way the Legal page owner's `tico risk` calls would: one risk is live
(a dispute arrived two days ago), one is open, two are quiet (one with a closed matter, one with none). The parties are
the Contacts section's organizations (backend/demo_sections/directory.py runs first), and a rule and a document link to
the risks they guard. Nothing names a real company or person.
"""

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
RULE = "A fire board letter gets Ana's OK before any reply."
SOURCE = "Customer terms"


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
