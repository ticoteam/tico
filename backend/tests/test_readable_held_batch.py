"""_readable_held batches privacy and reuses one list per snapshot for status/operations callers."""

from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_held_runs import held
from backend import execution
from backend import task_privacy as privacy
from backend.auth import Identity


def test_readable_held_reuses_the_list_and_batches_message_privacy(api, monkeypatch):
    machine, message, attempt = held(api)
    calls = []
    real = privacy.readable_messages

    def once(c, actor, messages):
        calls.append(len(list(messages)))
        return real(c, actor, messages)

    monkeypatch.setattr(privacy, "readable_messages", once)
    who = Identity("human:ana", "owner", "ana@acme.example")
    with api.app.state.store.read_transaction() as c:
        first = list(execution._readable_held(c, who, api.app.state.auth, "ops"))
        second = list(execution._readable_held(c, who, api.app.state.auth, "ops"))
        # held_runs + isolation_refused + stopped_rows used to walk three times; one cached list.
        assert first == second
        assert first and first[0][2] is True
    assert calls.count(1) == 1
