"""recent_bots bounds the mine/theirs message scan and batches privacy."""

from backend.tests.test_api import api, get, post  # noqa: F401
from backend import views
from backend import task_privacy as privacy


def test_recent_bots_uses_a_bounded_readable_messages_pass(api, monkeypatch):
    post(api, "chat/ops", {"text": "Hello ops"})
    post(api, "chat/ops", {"text": "Second note"})
    calls = []
    real = privacy.readable_messages

    def once(c, actor, messages):
        rows = list(messages)
        calls.append(len(rows))
        assert len(rows) <= 40
        return real(c, actor, rows)

    monkeypatch.setattr(privacy, "readable_messages", once)
    who = api.app.state.store.settings.test_identities["ana-test"]
    with api.app.state.store.read_transaction() as c:
        bots = views.recent_bots(c, api.app.state.auth, who, since="2000-01-01T00:00:00Z", limit=5, needs=[])
    assert any(b["bot"] == "ops" for b in bots)
    assert calls and all(n <= 40 for n in calls)
    page = get(api, "me/recent")
    assert any(b["bot"] == "ops" for b in page["bots"])
