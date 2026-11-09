"""Helpers that other tests share, moved out of test_dev_ticket_rules.py so they import no test file."""

from backend.store import H


def dev_type(c):
    return H.type_create(
        c,
        H.KEEPER,
        "Dev tickets",
        [
            {"name": "In Progress", "status": "doing"},
            {"name": "In QA", "status": "review"},
            {"name": "QA Fixes", "status": "doing"},
            {"name": "Pending Release", "status": "review"},
            {"name": "Done", "status": "done"},
        ],
        bots="work",
    )
