"""A bot's subtask on a check model (backend/check_models.py): its turns run on the named model in a session of their
own, its provenance names that model, and the verdict comes back to the parent's session."""
import json

import pytest

from backend import check_models
from backend.store import H, encode
from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401  (api is a fixture)

GROK = {"model": "grok-4.6", "runtime": "grok", "harness": "grok", "billing": "api"}


def company(api, *enabled):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='providers'")
        c.execute("INSERT INTO registry_metadata VALUES('providers',?)",
                  (encode({"revision": 1, "enabled": list(enabled), "runtime": "claude", "model": "claude-sonnet-5-5"}),))


def heartbeat(api, machine, grok="ready"):
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": ["task_lanes_v2", "session_roots_v1"],
        "readiness": {"schema_version": 1,
                      "runtimes": {"claude": {"installed": True, "authenticated": "ready"},
                                   "grok": {"installed": True, "authenticated": grok}},
                      "bots": {"ops": {"runtime": "claude", "ready": True}}}}, token=machine["token"])


def parent_turn(api):
    """ops on Claude, working a person's task: its claimed turn."""
    company(api, "anthropic", "xai")
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        config.update(runtime="claude", harness="claude", model="claude-sonnet-5-5",
                      fallback={"harness": "codex", "model": "gpt-6.1-sol"})
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (json.dumps(config),))
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine)
    parent = post(api, "tasks", {"owner_id": "ops", "title": "Ship the import fix", "body": "Open the PR and get it reviewed."})
    turn = claim(api, machine)
    assert turn["task"]["id"] == parent["id"] and turn["config"]["runtime"] == "claude"
    return machine, parent, turn


def finish(api, machine, turn, segment=None, during=None):
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    if during:
        during()
    usage = {**segment, "input_tokens": 30, "output_tokens": 30, "segments": [
        {**segment, "input_tokens": 30, "output_tokens": 30}]} if segment else None
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done.",
                                                  **({"usage": usage} if usage else {})}, machine["token"])


def test_a_review_subtask_runs_on_grok_in_its_own_session_and_its_verdict_wakes_the_parent(api):
    machine, parent, turn = parent_turn(api)
    sub = post(api, "tasks", {"owner_id": "ops", "title": "Review PR 700 with another model",
                              "body": "https://github.com/ticoteam/tico/pull/700", "check_model": "grok",
                              "check_role": "review", "relations": [{"task": parent["id"], "kind": "parent"}]},
               turn["token"])
    finish(api, machine, turn)

    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    assert check["task"]["check"] == {"runtime": "grok", "model": "grok-4.6", "role": "review"}
    config = check["config"]
    assert (config["runtime"], config["model"], config["harness"]) == ("grok", "grok-4.6", "grok")
    assert config["fallback"] == {"off": True}, "a check never hops to another provider"
    assert config["session"] == "task" and config["subtask_session"] == "own"
    assert check["lane"] == "task:" + sub["id"] and check["task"]["session_task_id"] == sub["id"]
    assert all(m.get("conversation_id") == sub["conversation_id"] for m in check["history"])
    finish(api, machine, check, GROK, during=lambda: post(
        api, "tasks/" + sub["id"], {"status": "done", "note": "Verdict: OK to merge; one nit on naming.",
                                         "verdict": "approve", "verdict_head": "a" * 40,
                                         "version": check["task"]["version"]}, check["token"]))
    shown = get(api, "tasks/" + sub["id"])
    assert shown["runs"][0]["ran_on"]["segments"][0]["model"] == "grok-4.6"
    assert shown["check_model"]["model"] == "grok-4.6" and shown["check_model"]["role"] == "review"
    back = claim(api, machine)
    assert back["task"]["id"] == parent["id"]
    assert back["config"]["runtime"] == "claude" and "check" not in back["task"]
    assert "Review done (ran on grok-4.6): Review PR 700 with another model" in back["message"]["body"]
    assert "Verdict: OK to merge" in back["message"]["body"]


def test_a_parents_turn_after_the_check_keeps_the_bots_own_session(api):
    machine, parent, turn = parent_turn(api)
    post(api, "tasks", {"owner_id": "ops", "title": "Check the migration with Grok", "body": "Read the migration plan.",
                        "check_model": "grok:grok-4.6", "relations": [{"task": parent["id"], "kind": "parent"}]},
         turn["token"])
    with api.app.state.store.read() as c:
        assert H.session_root(c, "ops", parent["id"]) == parent["id"]
        sub = c.execute("SELECT task_id,role FROM task_check_models").fetchone()
    assert sub["role"] == "check"
    with api.app.state.store.read() as c:
        assert H.session_root(c, "ops", sub["task_id"]) == sub["task_id"], "a check is never the parent's session"


@pytest.mark.parametrize("body, status, words", [
    ({"check_model": "cursor"}, 422, "enabled"),                       # the company has not enabled that provider
    ({"check_model": "grok", "owner": "coo"}, 422, "subtask it owns"),  # another bot's subtask
    ({"check_model": "grok", "relations": []}, 422, "under a task you own"),
])
def test_a_check_model_names_only_what_the_company_enabled_for_the_bots_own_subtask(api, body, status, words):
    machine, parent, turn = parent_turn(api)
    ask = {"owner_id": "ops", "title": "Review the change", "body": "Read it.",
           "relations": [{"task": parent["id"], "kind": "parent"}], **body}
    refused = post(api, "tasks", ask, turn["token"], expected=status)
    assert words in json.dumps(refused)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_check_models").fetchone()[0] == 0


def test_a_person_cannot_file_one_and_a_signed_out_runtime_is_refused_and_not_handed_the_turn(api):
    machine, parent, turn = parent_turn(api)
    post(api, "tasks", {"owner_id": "ops", "title": "Review it", "body": "Read it.", "check_model": "grok",
                        "relations": [{"task": parent["id"], "kind": "parent"}]}, expected=422)
    heartbeat(api, machine, grok="missing")
    refused = post(api, "tasks", {"owner_id": "ops", "title": "Review it on Grok", "body": "Read it.",
                                  "check_model": "grok", "relations": [{"task": parent["id"], "kind": "parent"}]},
                   turn["token"], expected=409)
    assert refused["code"] == "runner_not_ready" if "code" in refused else "runner_not_ready" in json.dumps(refused)
    heartbeat(api, machine)
    sub = post(api, "tasks", {"owner_id": "ops", "title": "Review it on Grok", "body": "Read it.", "check_model": "grok",
                              "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    finish(api, machine, turn)
    heartbeat(api, machine, grok="missing")
    assert claim(api, machine) is None, "a computer signed out of Grok is not handed the check"
    heartbeat(api, machine)
    assert claim(api, machine)["task"]["id"] == sub["id"]


def check_filed(api, title="Review PR 701 on Grok"):
    machine, parent, turn = parent_turn(api)
    sub = post(api, "tasks", {"owner_id": "ops", "title": title, "body": "Read it.", "check_model": "grok",
                              "check_role": "review", "relations": [{"task": parent["id"], "kind": "parent"}]},
               turn["token"])
    finish(api, machine, turn)
    return machine, parent, sub


def test_a_limit_on_the_check_model_holds_the_check_alone_and_the_bot_keeps_working(api, monkeypatch):
    from backend.execution import cooling_off, last_limit, limit_streak
    machine, parent, sub = check_filed(api)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    now = H.now()
    reset = H.shift(now, hours=2)[:19] + "Z"
    post(api, f"attempts/{check['id']}/started", {"thread_id": "grok-limit"}, machine["token"])
    post(api, f"attempts/{check['id']}/events", {"events": [{"seq": 1, "kind": "error", "payload": {
        "error": f"Grok quota exhausted. Your usage window resets at {reset}.", "limit": True}}]}, machine["token"])
    post(api, f"attempts/{check['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
         machine["token"])
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
        assert status["state"] != "limited" and not cooling_off(c, "ops"), "Grok's limit is not the bot's"
        assert "grok usage limit on a check" in status["focus"] and "claude" not in status["focus"]
        assert limit_streak(c, "ops") == 0 and last_limit(c, "ops") is None, "a check's limits are not the bot's"
    person = post(api, "tasks", {"owner_id": "ops", "title": "Answer the customer", "body": "Reply to the import question."})
    other = claim(api, machine)
    assert other["task"]["id"] == person["id"] and other["config"]["runtime"] == "claude"
    finish(api, machine, other)
    assert claim(api, machine) is None, "the check waits for Grok's reset"
    monkeypatch.setattr(H, "now", lambda: H.shift(reset, seconds=2))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen_at=NULL,awake_since=NULL")   # the computer stayed in touch meanwhile
    heartbeat(api, machine)
    again = claim(api, machine)
    assert again["task"]["id"] == sub["id"] and again["config"]["runtime"] == "grok"


def test_a_check_handed_to_another_bot_is_that_bots_ordinary_task(api):
    machine, parent, turn = parent_turn(api)
    sub = post(api, "tasks", {"owner_id": "ops", "title": "Review it on Grok", "body": "Read it.", "check_model": "grok",
                              "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    post(api, "tasks/" + sub["id"], {"owner_id": "coo", "version": sub["version"]}, turn["token"])
    with api.app.state.store.read() as c:
        assert H.check_model(c, sub["id"], "coo") is None and H.check_model(c, sub["id"], "ops") is None
        assert H.check_model(c, sub["id"])["model"] == "grok-4.6", "the record itself stays"


def test_work_under_a_check_continues_the_checks_session_not_the_parents(api):
    machine, parent, sub = check_filed(api)
    # A person's subtask under the check (a bot's own subtask is its own root anyway).
    below = post(api, "tasks", {"owner_id": "ops", "title": "Read the migration file", "body": "Line by line.",
                                "relations": [{"task": sub["id"], "kind": "parent"}]})
    with api.app.state.store.read() as c:
        assert H.session_root(c, "ops", below["id"]) == sub["id"]


def test_a_check_handed_to_another_bot_mid_turn_still_completes_as_the_check(api):
    from backend.execution import cooling_off
    machine, parent, sub = check_filed(api)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    post(api, f"attempts/{check['id']}/started", {"thread_id": "grok-handoff"}, machine["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='bot:coo' WHERE id=?", (sub["id"],))
    post(api, f"attempts/{check['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True, "text": ""},
         machine["token"])
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] != "limited" and not cooling_off(c, "ops")
        assert "grok usage limit on a check" in H.status(c, "ops")["focus"]
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key LIKE 'check-attempt:%'").fetchone()


def all_ready(api, machine, signed_out=(), extra=()):
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": ["task_lanes_v2", "session_roots_v1"],
        "readiness": {"schema_version": 1, "runtimes": {
            r: {"installed": True, "authenticated": "missing" if r in signed_out else "ready"}
            for r in ("claude", "codex", "grok", *extra)},
            "bots": {"ops": {"runtime": "claude", "ready": True}}}}, token=machine["token"])


def codex_check(api):
    """ops (Claude) with a review on Codex under its own task, the company also on Grok, the check claimed."""
    machine, parent, turn = parent_turn(api)
    company(api, "anthropic", "openai", "xai")
    all_ready(api, machine)
    sub = post(api, "tasks", {"owner_id": "ops", "title": "Review PR 702 on Codex", "body": "Read it.",
                              "check_model": "codex", "check_role": "review",
                              "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    finish(api, machine, turn)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"] and check["config"]["runtime"] == "codex"
    return machine, sub, check


def out_of_quota(api, machine, turn, resets_at=None):
    """The Oct 17-18 case: the computer's default Codex login refuses the turn at once."""
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "codex-" + turn["id"][:8]}, machine["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True, "text": "",
                                                  "limit_resets_at": resets_at}, machine["token"])


def later(api, monkeypatch, machine, at):
    monkeypatch.setattr(H, "now", lambda: at)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET last_seen_at=NULL,awake_since=NULL")   # the computer stayed in touch meanwhile
    all_ready(api, machine)


def test_a_check_moves_to_its_next_model_once_its_default_login_is_out_of_quota(api, monkeypatch):
    machine, sub, check = codex_check(api)
    start = H.now()
    out_of_quota(api, machine, check)
    assert claim(api, machine) is None, "one limit is not yet out of quota: the check waits as before"
    later(api, monkeypatch, machine, H.shift(start, minutes=31))
    again = claim(api, machine)
    assert again["task"]["id"] == sub["id"] and again["config"]["runtime"] == "codex"
    out_of_quota(api, machine, again)
    # Two limits in a row with no reset time: Health marks the login out of quota, so the next turn runs on Grok
    # at once instead of retrying Codex in half an hour.
    moved = claim(api, machine)
    assert moved["task"]["id"] == sub["id"]
    assert (moved["config"]["runtime"], moved["config"]["model"]) == ("grok", "grok-4.6")
    assert moved["task"]["check"] == {"runtime": "grok", "model": "grok-4.6", "role": "review"}
    assert moved["config"]["fallback"] == {"off": True} and moved["config"]["subtask_session"] == "own"
    with api.app.state.store.read() as c:
        assert "the check moved to grok" in H.status(c, "ops")["focus"]
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.check_moved'").fetchone()[0] == 1
        event = c.execute("SELECT old,new,note FROM task_events WHERE task_id=? AND field='check_model' "
                          "AND old IS NOT NULL", (sub["id"],)).fetchone()
        assert (event["old"], event["new"]) == ("codex:gpt-6-luna", "grok:grok-4.6"), dict(event)
    shown = get(api, "tasks/" + sub["id"])
    assert shown["check_model"]["model"] == "gpt-6-luna", "the check asked for stays on record"


def test_with_no_other_provider_left_the_check_moves_to_the_authors_own(api, monkeypatch):
    machine, sub, check = codex_check(api)
    company(api, "anthropic", "openai")                     # Claude wrote the work; Codex is the only other model
    start = H.now()
    out_of_quota(api, machine, check)
    later(api, monkeypatch, machine, H.shift(start, minutes=31))
    again = claim(api, machine)
    out_of_quota(api, machine, again)
    moved = claim(api, machine)
    assert moved["task"]["id"] == sub["id"], "another provider is preferred, not required: the review still runs"
    assert moved["config"]["runtime"] == "claude"


def test_with_no_next_check_model_the_check_keeps_its_hold(api, monkeypatch):
    machine, sub, check = codex_check(api)
    company(api, "anthropic", "openai")                     # Claude wrote the work; Codex is the only other model
    start = H.now()
    out_of_quota(api, machine, check)
    later(api, monkeypatch, machine, H.shift(start, minutes=31))
    again = claim(api, machine)
    all_ready(api, machine, signed_out=("claude",))         # and the author's own cannot run on this computer
    out_of_quota(api, machine, again)
    assert claim(api, machine) is None, "nowhere to move: the check waits on Codex as before"
    with api.app.state.store.read() as c:
        assert "codex usage limit on a check" in H.status(c, "ops")["focus"]
    later(api, monkeypatch, machine, H.shift(start, minutes=63))
    assert claim(api, machine)["config"]["runtime"] == "codex"


@pytest.mark.parametrize("recovery", ["completed", "reset"])
def test_a_moved_check_goes_back_to_its_first_model_once_that_login_works(api, monkeypatch, recovery):
    from backend.tests.test_health_monitor import turn as finished_turn
    machine, sub, check = codex_check(api)
    start = H.now()
    reset = H.shift(start, hours=3) if recovery == "reset" else None
    # An earlier turn on that login already stopped on its limit (another bot's check, say).
    finished_turn(api, machine["runner_id"], {"outcome": "failed", "limited": True, "limit_resets_at": reset,
                                              "profile_used": None, "check": "codex"}, 5)
    out_of_quota(api, machine, check, reset)
    moved = claim(api, machine)
    assert moved["config"]["runtime"] == "grok"
    out_of_quota(api, machine, moved)                       # Grok is busy too: the check waits on Grok, not Codex
    assert claim(api, machine) is None
    if recovery == "completed":
        # A turn on the computer's default Codex login completes: it works again.
        finished_turn(api, machine["runner_id"], {"outcome": "completed", "profile_used": None,
                                                  "usage": {"runtime": "codex"}}, -1, bot="coo")
        later(api, monkeypatch, machine, H.shift(start, minutes=31))
    else:
        later(api, monkeypatch, machine, H.shift(reset, seconds=2))
    back = claim(api, machine)
    assert back["task"]["id"] == sub["id"]
    assert (back["config"]["runtime"], back["config"]["model"]) == ("codex", "gpt-6-luna")
    assert back["task"]["check"]["runtime"] == "codex"


def test_a_check_on_a_subscription_profile_does_not_move(api, monkeypatch):
    from backend.tests.test_health_monitor import turn as finished_turn
    machine, sub, check = codex_check(api)
    finished_turn(api, machine["runner_id"], {"outcome": "failed", "limited": True, "limit_resets_at": None,
                                              "profile_used": None, "check": "codex"}, 10)
    finished_turn(api, machine["runner_id"], {"outcome": "failed", "limited": True, "limit_resets_at": None,
                                              "profile_used": None, "check": "codex"}, 5)
    # The computer's default Codex login is out of quota, but this turn ran on a subscription profile's own login.
    post(api, f"attempts/{check['id']}/started", {"thread_id": "codex-profile"}, machine["token"])
    post(api, f"attempts/{check['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True, "text": "",
                                                   "profile_used": "work"}, machine["token"])
    assert claim(api, machine) is None, "a profile's limit holds the check as before"


def moved_to_grok(api):
    """Two limits in a row on the computer's default Codex login: the check has moved to Grok."""
    from backend.tests.test_health_monitor import turn as finished_turn
    machine, sub, check = codex_check(api)
    finished_turn(api, machine["runner_id"], {"outcome": "failed", "limited": True, "limit_resets_at": None,
                                              "profile_used": None, "check": "codex"}, 5)
    out_of_quota(api, machine, check)
    with api.app.state.store.read() as c:
        assert check_models.of(c, sub["id"], "ops")["runtime"] == "grok"
    return machine, sub


def test_a_moved_check_tries_its_first_model_again_on_another_computer(api):
    from backend.health_monitor import out_of_quota_logins
    machine, sub = moved_to_grok(api)
    # The bot moves to a computer where Codex works and Grok is signed out; the first computer's login stays out.
    other = runner(api, label="Other Mac")
    with api.app.state.store.read() as c:
        generation = c.execute("SELECT generation FROM assignments WHERE bot='ops'").fetchone()[0]
    assign(api, other, "ops", generation=generation)
    all_ready(api, other, signed_out=("grok",))
    again = claim(api, other)
    assert again["task"]["id"] == sub["id"]
    assert (again["config"]["runtime"], again["config"]["model"]) == ("codex", "gpt-6-luna")
    with api.app.state.store.read() as c:
        assert [x["runner_id"] for x in out_of_quota_logins(c)] == [machine["runner_id"]]


def test_a_check_skips_a_next_model_its_computer_cannot_run(api):
    machine, parent, turn = parent_turn(api)
    company(api, "anthropic", "openai", "xai", "cursor")
    all_ready(api, machine, signed_out=("grok",), extra=("cursor",))
    sub = post(api, "tasks", {"owner_id": "ops", "title": "Review PR 703 on Codex", "body": "Read it.",
                              "check_model": "codex", "check_role": "review",
                              "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    finish(api, machine, turn)
    from backend.tests.test_health_monitor import turn as finished_turn
    finished_turn(api, machine["runner_id"], {"outcome": "failed", "limited": True, "limit_resets_at": None,
                                              "profile_used": None, "check": "codex"}, 5)
    check = claim(api, machine)
    out_of_quota(api, machine, check)
    moved = claim(api, machine)
    assert moved["task"]["id"] == sub["id"] and moved["config"]["runtime"] == "cursor", "Grok is signed out here"


def limited_check(api):
    """A Grok review whose first turn stopped on Grok's usage limit: its job back in the queue, held."""
    machine, parent, sub = check_filed(api)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    post(api, f"attempts/{check['id']}/started", {"thread_id": "grok-" + check["id"][:8]}, machine["token"])
    post(api, f"attempts/{check['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True, "text": ""},
         machine["token"])
    return machine, sub, check


def check_held(api, check, hours=None):
    """The Health Monitor's check_limited findings, with the check's limited turn `hours` old."""
    from backend import health_monitor
    store = api.app.state.store
    with store.transaction() as c:
        if hours is not None:
            c.execute("UPDATE attempts SET finished_at=? WHERE id=?", (H.shift(H.now(), hours=-hours), check["id"]))
        return [f for f in health_monitor.findings(c, store.settings) if f["check_id"] == "check_limited"]


def queue_on(api, sub, sender, to="bot:ops", hours=0):
    """A message to `to` on the check task, its job `hours` old: its id."""
    with api.app.state.store.transaction() as c:
        conv = H.task(c, sub["id"])["conversation_id"]
        msg = H.say(c, sender, to, "One more thing on this review.", conversation_id=conv,
                    refs={"task": sub["id"], **({"needs_action": True} if H.is_bot(sender) else {})})
        c.execute("UPDATE jobs SET created_at=? WHERE message_id=?", (H.shift(H.now(), hours=-hours), msg["id"]))
        return msg["id"]


def test_a_check_its_limit_held_twelve_hours_is_one_setup_finding_until_a_turn_gets_past_it(api, monkeypatch):
    machine, sub, check = limited_check(api)
    # An older notice the claim has not handed out yet waits beside the job that keeps stopping.
    queue_on(api, sub, H.KEEPER, hours=20)
    assert check_held(api, check, hours=11) == []
    [found] = check_held(api, check, hours=13)
    with api.app.state.store.read() as c:
        name = H.bot(c, "ops")["display_name"]
        since = c.execute("SELECT finished_at FROM attempts WHERE id=?", (check["id"],)).fetchone()[0]
    assert (found["key"], found["kind"]) == ("check_limited:" + sub["id"], "setup")
    assert found["detail"].startswith(f"{name}'s check on Grok Build (grok-4.6) has been held by its usage limit for "
                                      f"13 h (since {since[:16].replace('T', ' ')} UTC); Review PR 701 on Grok.")
    assert found["fix"].startswith("BotOps: wait for Grok Build (grok-4.6)'s limit to reset; or switch the check model")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (sub["id"],))
    assert "; a private task." in check_held(api, check)[0]["detail"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (sub["id"],))
    # Past the reset the claim hands it out again; a turn that ends any other way than the limit clears it, even
    # with the job back in the queue.
    later(api, monkeypatch, machine, H.shift(H.now(), minutes=31))
    again = claim(api, machine)
    assert again["task"]["id"] == sub["id"]
    post(api, f"attempts/{again['id']}/started", {"thread_id": "grok-" + again["id"][:8]}, machine["token"])
    post(api, f"attempts/{again['id']}/complete", {"outcome": "failed", "last_seq": 0, "retryable": True, "text": ""},
         machine["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (again["job_id"],)).fetchone()[0] == "queued"
    assert check_held(api, check) == []


@pytest.mark.parametrize("status", ["declined", "closed", "done"])
def test_a_held_check_that_leaves_the_board_lets_its_queued_turn_go(api, monkeypatch, status):
    machine, sub, check = limited_check(api)
    execution = api.app.state.execution
    person = queue_on(api, sub, "human:ana")
    with api.app.state.store.transaction() as c:
        assert check["job_id"] not in execution.hold_unrunnable(c), "an open check keeps its turn"
        # The task left the board while held, or before this server started: the next tick lets the turn go.
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status, sub["id"]))
        assert check["job_id"] in execution.hold_unrunnable(c)
        assert c.execute("SELECT state FROM jobs WHERE id=?", (check["job_id"],)).fetchone()[0] == "canceled"
        event = c.execute("SELECT detail FROM events WHERE action='job.suppress' AND target=?",
                          (check["job_id"],)).fetchone()
        assert json.loads(event[0])["task_id"] == sub["id"]
        assert check["job_id"] not in execution.hold_unrunnable(c), "once"
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (person,)).fetchone()[0] == "queued"
    assert check_held(api, check, hours=13) == []
    later(api, monkeypatch, machine, H.shift(H.now(), minutes=31))
    assert claim(api, machine)["job_id"] == person, "only the person's message is handed out"


def test_a_declined_checks_notice_is_cancelled_on_claim_without_waiting_for_the_tick(api, monkeypatch):
    machine, sub, check = limited_check(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='declined' WHERE id=?", (sub["id"],))
    later(api, monkeypatch, machine, H.shift(H.now(), minutes=31))
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (check["job_id"],)).fetchone()[0] == "canceled"


def test_a_held_check_handed_to_another_bot_is_its_ordinary_task_when_it_closes(api):
    machine, sub, check = limited_check(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='bot:finance',status='done' WHERE id=?", (sub["id"],))
    follow_up = queue_on(api, sub, "bot:ops", to="bot:finance")
    with api.app.state.store.transaction() as c:
        assert api.app.state.execution.hold_unrunnable(c) == []
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (follow_up,)).fetchone()[0] == "queued"


def test_a_check_model_at_capacity_is_not_its_default_login_out_of_quota(api, monkeypatch):
    # Two "at capacity" refusals on the computer's default Codex login: a busy provider, so the check waits its two
    # minutes on Codex and Health never calls that login out of quota.
    from backend import health_monitor
    machine, sub, check = codex_check(api)
    start = H.now()
    notice = "Selected model is at capacity. Please try a different model."
    busy = {"seq": 1, "kind": "error", "payload": {"error": notice, "limit": True}}
    for n in range(2):
        post(api, f"attempts/{check['id']}/started", {"thread_id": f"busy-{n}"}, machine["token"])
        post(api, f"attempts/{check['id']}/events", {"events": [busy]}, machine["token"])
        post(api, f"attempts/{check['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
             machine["token"])
        later(api, monkeypatch, machine, H.shift(start, minutes=3 * (n + 1)))
        check = claim(api, machine)
        assert check["task"]["id"] == sub["id"] and check["config"]["runtime"] == "codex"
    with api.app.state.store.read() as c:
        assert health_monitor.out_of_quota_logins(c, api.app.state.store.settings) == []
        assert "temporary model capacity on a check" in H.status(c, "ops")["focus"]
