"""A nightly memory tidy pass's check subtask (backend/check_models.py file_pass, spec nightly-memory-tidy v1.3): its named
model at low effort and never Haiku-tier, skipped rather than moved or held when that model cannot run, and a free skip
that ignores the pass's own commits and the wake turn it caused. Synthetic fixtures only."""
import json

import pytest

from backend import check_models, memory_history
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401  (api is a fixture)
from backend.tests.test_check_models import all_ready, company, finish, out_of_quota, parent_turn


def pass_filed(api, spec="codex:gpt-6.1-sol"):
    """ops (Claude) with a person's task claimed, a plain subtask of it, and that subtask named as a memory pass on `spec`."""
    machine, parent, turn = parent_turn(api)
    company(api, "anthropic", "openai", "xai")
    all_ready(api, machine)
    sub = post(api, "tasks", {"owner": "ops", "title": "Nightly memory tidy: 2026-11-02", "body": "Read memory.",
                              "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    finish(api, machine, turn)
    with api.app.state.store.transaction() as c:
        check_models.file_pass(c, api.app.state.store.settings, sub["id"], spec)
    return machine, parent, sub


@pytest.mark.parametrize("spec, model, runtime", [
    ("codex:gpt-6.1-sol", "gpt-6.1-sol", "codex"),
    ("claude:claude-sonnet-5-5", "claude-sonnet-5-5", "claude"),
])
def test_a_pass_runs_at_low_effort_on_each_pilot_entry_and_the_ordinary_check_keeps_its_default(api, spec, model, runtime):
    machine, parent, turn = parent_turn(api)
    company(api, "anthropic", "openai", "xai")
    all_ready(api, machine)
    settings = api.app.state.store.settings
    with api.app.state.store.read() as c:
        ordinary = check_models.choose(c, settings, "ops", spec, "check")
        passed = check_models.choose(c, settings, "ops", spec, "check", memory_pass=True)
    assert ordinary["effort"] != "low" and "memory_pass" not in ordinary
    assert (passed["runtime"], passed["model"], passed["effort"], passed["memory_pass"]) == (runtime, model, "low", True)


def test_a_pass_never_runs_on_a_haiku_tier_model(api):
    machine, parent, turn = parent_turn(api)
    company(api, "anthropic", "openai", "xai")
    all_ready(api, machine)
    with api.app.state.store.read() as c, pytest.raises(check_models.CheckModelError, match="Haiku"):
        check_models.choose(c, api.app.state.store.settings, "ops", "claude:claude-haiku-5-5", "check", memory_pass=True)


def test_a_pass_is_filed_on_its_own_subtask_at_low_effort_with_no_fallback_and_the_pass_flag(api):
    machine, parent, sub = pass_filed(api)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    assert (check["config"]["runtime"], check["config"]["model"]) == ("codex", "gpt-6.1-sol")
    assert check["config"]["reasoning_effort"] == "low" and check["config"]["memory_pass"] is True
    assert check["config"]["fallback"] == {"off": True} and check["config"]["subtask_session"] == "own"
    with api.app.state.store.read() as c:
        assert check_models.memory_pass(c, sub["id"])
        assert not check_models.memory_pass(c, parent["id"])


def test_a_pass_whose_named_model_is_out_of_quota_is_skipped_with_the_reason_and_never_moves_or_holds(api):
    machine, parent, sub = pass_filed(api)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"] and check["config"]["runtime"] == "codex"
    out_of_quota(api, machine, check)          # one limit is enough: the pass cannot run that night
    with api.app.state.store.read() as c:
        shown = c.execute("SELECT status,note FROM tasks WHERE id=?", (sub["id"],)).fetchone()
        assert (shown["status"], shown["note"]) == ("declined", "skipped: model unavailable"), dict(shown)
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.check_skipped'").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.check_moved'").fetchone()[0] == 0
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key LIKE 'check-moved:%' OR key LIKE 'check-limited:%'").fetchone()
        assert "skipped" in H.status(c, "ops")["focus"] and "grok" not in H.status(c, "ops")["focus"]
    # The pass is never claimed again, and nothing runs on another model for it: the only turn left is the parent's
    # own, on the bot's Claude model, which was queued when the subtask was filed.
    leftover = claim(api, machine)
    assert leftover is None or (leftover["task"]["id"] == parent["id"] and leftover["config"]["runtime"] == "claude"
                                and not leftover["config"].get("memory_pass")), leftover
    assert get(api, "tasks/" + sub["id"])["check_model"]["model"] == "gpt-6.1-sol"


def test_a_pass_with_no_memory_commit_since_its_last_pass_is_a_free_skip(api):
    machine, parent, turn = parent_turn(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_memory_updates(bot,sha,subject,author,committed_at,files,attempt_id,runner_id,reported_at)"
                  " VALUES('ops',?,?,?,?,?,?,?,?)",
                  ("a" * 40, "Dreamed the pass's own wake turn", "ops", "2026-11-01T20:00:00Z", "[]", turn["id"],
                   machine["runner_id"], "2026-11-01T20:00:00Z"))
    since = "2026-11-01T19:00:00Z"
    with api.app.state.store.read() as c:
        assert memory_history.changed_since(c, "ops", since, [parent["id"]]) is False, "only the pass's wake turn wrote"
        assert memory_history.changed_since(c, "ops", since, []) is True, "without the pass tasks it is a change"
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_memory_updates(bot,sha,subject,author,committed_at,files,attempt_id,runner_id,reported_at)"
                  " VALUES('ops',?,?,?,?,?,?,?,?)",
                  ("b" * 40, "A rule from an ordinary turn", "ops", "2026-11-01T21:00:00Z", "[]", "", machine["runner_id"],
                   "2026-11-01T21:00:00Z"))
    with api.app.state.store.read() as c:
        assert memory_history.changed_since(c, "ops", since, [parent["id"]]) is True, "an ordinary turn's commit counts"


def queued_for(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot_id=? AND state='queued'", (bot,)).fetchone()[0]


def test_a_skipped_pass_wakes_nothing_on_its_parent_or_its_requester(api):
    machine, parent, sub = pass_filed(api)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    out_of_quota(api, machine, check)
    with api.app.state.store.read() as c:
        queued = [dict(r) for r in c.execute(
            "SELECT m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.state='queued'").fetchall()]
    news = [json.loads(r["refs"] or "{}").get("news") for r in queued]
    assert "task_declined" not in news, "the decline is not sent to the requester"
    assert "subtask_finished" not in [json.loads(r["refs"] or "{}").get("wake") for r in queued], "the parent is not woken"
    leftover = claim(api, machine)   # a requeued notice for the pass is refused at claim: it is never claimed again
    assert leftover is None or leftover["task"]["id"] != sub["id"], leftover
