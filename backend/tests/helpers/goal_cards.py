"""Helpers that other tests share, moved out of test_goal_cards.py so they import no test file."""

from backend.tests.helpers.api import post


def propose(api, kind, payload, goal_id=None, kpi_id=None, token="ana-test"):
    return post(
        api,
        "goal-proposals",
        {"kind": kind, "goal_id": goal_id, "kpi_id": kpi_id, "payload": payload, "reason": "No measure yet"},
        token=token,
    )["proposal"]


# A member may not read the inbox bot (the base fixture: it is Ana's alone), so nothing of its goals or measures reaches
# her through a suggestion: not its name, what it counts, its source, its unit, nor the target it has now.
SECRETS = ("NDAs waiting", "Unsigned NDAs in the mail", "Mailbox count", "Close all NDAs")


def inbox_measure(api):
    hidden = post(api, "goals", {"title": "Close all NDAs by Q4", "owner": "bot:inbox", "top": True})["goal"]
    kpi = post(
        api,
        "kpis",
        {
            "name": "NDAs waiting",
            "unit": "docs",
            "direction": "down",
            "cadence": "daily",
            "definition": "Unsigned NDAs in the mail",
            "source_note": "Mailbox count",
            "goal_id": hidden["id"],
        },
    )["kpi"]
    return hidden, kpi


def leaks(response):
    return [s for s in SECRETS if s in str(response)]
