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


def stage_dev_type(c):
    """The Engineering board after the stage map: the stage names and statuses the setup gives it."""
    return H.type_create(
        c,
        H.KEEPER,
        "Dev tickets",
        [
            {"name": "Build", "status": "open"},
            {"name": "Review 1", "status": "review"},
            {"name": "Review 2", "status": "review"},
            {"name": "Merge queue", "status": "review"},
            {"name": "Release", "status": "review"},
            {"name": "Watch 24h", "status": "review"},
            {"name": "Closed", "status": "closed"},
        ],
        bots="work",
    )
