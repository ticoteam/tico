"""Helpers that other tests share, moved out of test_strategy.py so they import no test file."""

from backend.tests.helpers.api import get, post, put


WRONG = {"kind": "text", "text": "Three landlords in a row say no", "check_by": "2099-01-01"}


def draft(api, token="ana-test"):
    return get(api, "strategy", token=token)["draft"]


def write(api, token="ana-test", expected=200, **fields):
    revision = fields.pop("revision", None)
    if revision is None:
        revision = draft(api)["revision"]
    return put(api, "strategy/draft", {"revision": revision, **fields}, token=token, expected=expected)


def add_bet(api, statement="Small landlords pay for bots", expected=200, token="ana-test", **more):
    body = {
        "statement": statement,
        "why": "They have no staff.",
        "rules_out": "Enterprise sales",
        "wrong_if": WRONG,
        "status": "active",
        **more,
    }
    return post(api, "strategy/bets", body, token=token, expected=expected)


def publish(api, note="First", expected=200):
    return post(api, "strategy/publish", {"note": note, "revision": draft(api)["revision"]}, expected=expected)


def ready(api, **bet):
    """A draft that may be published: a won't-do and one active bet."""
    write(api, summary="Acme runs rentals with bots.", sections={"not": "No enterprise deals."})
    return add_bet(api, **bet)["bet"]
