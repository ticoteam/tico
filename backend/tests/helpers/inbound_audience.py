"""Helpers that other tests share, moved out of test_inbound_audience.py so they import no test file."""

from backend.tests.helpers.groups import roster_of, set_roster
from backend.tests.helpers.inbound import page, seed


def narrow(api):
    doc = roster_of(api)
    doc["org_groups"] = {
        **(doc.get("org_groups") or {}),
        "sales": {"name": "Sales"},
        "east": {"name": "East", "parent": "sales"},
        "ops-team": {"name": "Operations"},
    }
    for person in doc["people"]:
        if person["id"] == "cara":
            person["team"] = "east"
    set_roster(api, doc)
    svc = seed(api)
    svc.ingest(page(1, inbox="ibx_b", ident="ses:msg:ibx_b", body="Your verification code is 123456."))
    svc.ingest(page(2, inbox="ibx_a", ident="ses:msg:ibx_a", body="Hello from Acme."))
    return svc
