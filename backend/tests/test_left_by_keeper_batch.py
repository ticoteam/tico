"""left_by_keeper batches message privacy instead of message_readable per row."""

from backend.tests.test_api import api  # noqa: F401
from backend import execution
from backend import task_privacy as privacy
from backend.auth import Identity
from backend.store import H


def test_left_by_keeper_privacy_checks_in_one_batch(api, monkeypatch):
    # No keeper-left rows: still one empty readable_messages pass (not a generator of per-row checks).
    calls = []
    real = privacy.readable_messages

    def once(c, actor, messages):
        calls.append(len(list(messages)))
        return real(c, actor, messages)

    monkeypatch.setattr(privacy, "readable_messages", once)
    who = Identity("human:ana", "owner", "ana@acme.example")
    with api.app.state.store.read_transaction() as c:
        assert execution.left_by_keeper(c, who, "ops") == 0
    assert calls == [0]
