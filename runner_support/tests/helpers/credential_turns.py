"""A bot turn that uses credentials, and where its values must never show: shared by the credential delivery suite
(runner_support/tests/test_credential_delivery.py, a stub vendor) and the live auth matrix
(runner_support/tests/test_credential_live.py, public test services)."""

import json

from backend.tests.helpers.api import get, post
from runner_support.tests.helpers.conformance import reply_to, until


def turn(api, computer, *markers, bot="ops"):
    """One chat turn whose message is the markers, one per line; (the bot's reply, the uses its harness logged)."""
    done = len([a for a in computer.attempts() if a["result"]])
    logged = len(computer.calls())
    message = post(api, f"chat/{bot}", {"text": "Please do this:\n" + "\n".join(markers)})
    until(lambda: len([a for a in computer.attempts() if a["result"]]) > done, f"{bot}'s turn", timeout=60)
    attempt = computer.attempts()[-1]
    assert attempt["result"]["outcome"] == "completed", attempt
    answer = until(lambda: reply_to(api, message["conversation_id"], bot=bot), f"{bot}'s reply")
    uses = [c for c in computer.calls()[logged:] if "use" in c]
    return answer, uses, message["conversation_id"]


def one(uses, kind):
    found = [u for u in uses if u["use"] == kind]
    assert len(found) == 1, uses
    return found[0]


def nowhere(api, computer, conversation, *values):
    """None of `values` in the conversation, any of this computer's turns' events, the runner's log or any table."""
    shown = json.dumps(get(api, f"conversations/{conversation}/messages"))
    events = json.dumps([computer.events(a["id"]) for a in computer.attempts()])
    log = computer.output.read_text() if computer.output.exists() else ""
    with api.app.state.store.read() as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        rows = {t: repr([tuple(r) for r in c.execute(f'SELECT * FROM "{t}"')]) for t in tables}
    for value in values:
        assert value not in shown, "the transcript holds the value"
        assert value not in events, "a turn's events hold the value"
        assert value not in log, "the runner's log holds the value"
        assert not [t for t, text in rows.items() if value in text], "a table holds the value"
