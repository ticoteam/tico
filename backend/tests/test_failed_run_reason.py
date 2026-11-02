"""A run that failed on a limit says why in the run list: the kind, the runtime, primary or fallback, the login and the
model. Before, the reason sat only in the bot's live status line, and the run read "Attempt failed before completion"
(the limit source was kept in the attempt's result but never read by the run list)."""
from backend import login_holds
from backend.tests.test_api import api, claim, get, post  # noqa: F401
from backend.tests.test_login_holds import CLAUDE, QUOTA, events, two_bots


def failed_run(api, bot="ops"):
    return next(t for t in get(api, f"bots/{bot}/turns") if t.get("exit") == "failed")


def finish(api, machine, rows, **result):
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, machine["token"])
    seq = events(api, machine, attempt, rows)
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": seq, "limited": True, "text": "",
                                                     **result}, machine["token"])
    return attempt


def test_a_fallback_quota_failure_names_kind_fallback_and_model(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    finish(api, machine, [("diagnostic", "fake unavailable; running this turn on codex"), ("error", QUOTA)],
           fallback="codex", usage={"segments": [{"model": "gpt-5.5", "runtime": "codex"}]})
    reason = failed_run(api)["failure_reason"]
    assert reason.startswith("failed: quota (Codex fallback") and "model gpt-5.5" in reason


def test_a_fallback_failing_at_once_does_not_borrow_the_primarys_model(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    finish(api, machine, [("diagnostic", "fake unavailable; running this turn on codex"), ("error", QUOTA)],
           fallback="codex", usage={"segments": [{"model": "fake-model", "runtime": "fake", "input_tokens": 5}]})
    reason = failed_run(api)["failure_reason"]
    assert reason.startswith("failed: quota (Codex fallback") and "model" not in reason


def test_a_primary_session_limit_failure_says_usage_limit_and_primary(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    finish(api, machine, [("error", CLAUDE)])
    assert failed_run(api)["failure_reason"] == "failed: usage limit (Fake, primary)"


def test_failure_line_is_none_without_a_limit():
    assert login_holds.failure_line({"error": "boom"}) is None
