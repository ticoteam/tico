"""store.readiness_document parses a stored text once (a runner's text changes only on its heartbeat); each caller
still gets a document of its own, so one caller's changes never reach the next."""

import orjson
from backend import store
from backend.store import bot_readiness, readiness_document


def _text(bots=3):
    return orjson.dumps(
        {
            "schema_version": 1,
            "worktrees": True,
            "harnesses": {"claude-code": {"name": "claude-code", "installed": True}},
            "mail_key": "exposed",
            "runtimes": {
                "claude": {"installed": True, "authenticated": "ready", "account": {"email": "a@example.test"}},
                "codex": {"installed": True, "authenticated": "missing", "weekly": {"used_percent": 4}},
            },
            "bots": {
                f"bot-{i}": {"ready": True, "runtime": "claude", "problems": [], "tools": [{"service": "mail"}]}
                for i in range(bots)
            },
            "recent_errors": ["WARN one", "WARN two"],
        }
    ).decode()


def _fresh(value):
    """The answer before memoizing: parse the text every time."""
    return store._readiness_shape(orjson.loads(value))


def test_a_changed_document_does_not_reach_the_next_call():
    text = _text()
    first = readiness_document(text)
    assert first == _fresh(text)
    # What callers do: execution.heartbeat/forget_report/mark_rejected, views.computer_details, without_accounts.
    first.pop("worktrees")
    first.pop("harnesses")
    first["mail_key"] = None
    del first["bots"]["bot-0"]
    first["bots"]["bot-1"]["ready"] = False
    first["bots"]["bot-1"]["problems"] = ["Sign-in rejected"]
    first["runtimes"]["claude"].update(authenticated="rejected")
    first["runtimes"]["claude"].pop("account")
    first["runtimes"]["codex"].pop("weekly")
    first["runtimes"]["gemini"] = {"installed": False}
    first["recent_errors"].append("WARN three")
    first["bots"] = {}
    again = readiness_document(text)
    assert again == _fresh(text)
    assert again is not first
    # A row read through bot_readiness is the caller's own too.
    row = bot_readiness(text, "bot-2")
    row["ready"] = False
    assert bot_readiness(text, "bot-2")["ready"] is True


def test_same_answer_as_parsing_for_every_input_kind():
    legacy = orjson.dumps({"bot-a": True, "bot-b": False, "note": "x"}).decode()
    for value in (_text(), _text().encode(), legacy, legacy.encode(), "", "not json", "[1, 2]", b"null"):
        for _ in range(2):  # the miss and the hit
            try:
                expected = _fresh(value)
            except ValueError:
                expected = store._readiness_shape({})
            assert readiness_document(value) == expected
    assert readiness_document(None) == store._readiness_shape({})
    assert readiness_document({"bot-a": True})["bots"]["bot-a"]["ready"] is True
    shaped = {"schema_version": 1, "bots": {}}
    assert readiness_document(shaped) is shaped  # a document already parsed is returned as before


def test_the_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(store, "_READINESS_MOST", 4)
    for i in range(10):
        readiness_document(_text(bots=i))
    assert len(store._READINESS_PARSED) <= 4
    assert store._readiness_chars == sum(len(k) for k in store._READINESS_PARSED)
    monkeypatch.setattr(store, "_READINESS_MOST_CHARS", len(_text(bots=50)) - 1)
    readiness_document(_text(bots=50))  # larger than the whole budget: parsed, not kept
    assert _text(bots=50) not in store._READINESS_PARSED
    assert store._readiness_chars <= store._READINESS_MOST_CHARS
