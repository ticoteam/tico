"""recent_bots finds its bots with one batched privacy pass and still shows each bot's newest readable line."""

from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend import views
from backend import task_privacy as privacy


def test_recent_bots_discovers_in_one_pass_and_skips_deleted_newest_lines(api, monkeypatch):
    for n in range(42):
        post(api, "chat/ops", {"text": f"line {n}"})
    with api.app.state.store.transaction() as c:
        newest = [r[0] for r in c.execute("SELECT id FROM messages WHERE from_actor='human:ana' AND to_actor='bot:ops' "
                                          "ORDER BY created_at DESC")][:41]
        c.executemany("UPDATE messages SET deleted_at=? WHERE id=?", [(H.now(), mid) for mid in newest])
    calls = []
    real = privacy.readable_messages

    def counted(c, actor, messages):
        rows = list(messages)
        calls.append(len(rows))
        return real(c, actor, rows)

    monkeypatch.setattr(privacy, "readable_messages", counted)
    who = api.app.state.store.settings.test_identities["ana-test"]
    with api.app.state.store.read_transaction() as c:
        bots = views.recent_bots(c, api.app.state.auth, who, since="2000-01-01T00:00:00Z", limit=5, needs=[])
    ops = next(b for b in bots if b["bot"] == "ops")
    # The 41 newest lines are deleted: the card still shows the one readable line and its conversation.
    assert ops["you_said"]["text"] == "line 0"
    assert ops["conversation_id"]
    # Discovery checks the whole window in one batched call.
    assert len(calls) == 1 and calls[0] >= 42
    assert any(b["bot"] == "ops" for b in get(api, "me/recent")["bots"])
