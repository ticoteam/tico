"""held_back and unreviewed batch message privacy for status/operations."""

from backend.tests.test_api import api  # noqa: F401
from backend import execution
from backend import task_privacy as privacy
from backend.auth import Identity


def test_held_back_and_unreviewed_privacy_check_in_one_batch_each(api, monkeypatch):
    calls = []
    real = privacy.readable_messages

    def once(c, actor, messages):
        calls.append(len(list(messages)))
        return real(c, actor, messages)

    monkeypatch.setattr(privacy, "readable_messages", once)
    who = Identity("human:ana", "owner", "ana@acme.example")
    with api.app.state.store.read_transaction() as c:
        assert execution.held_back(c, who, api.app.state.auth, "ops") == (0, [])
        assert execution.unreviewed(c, who, "ops") == (0, 0)
    assert calls == [0, 0]
