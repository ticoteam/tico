"""A usage limit holds the provider login, survives restarts, and lets one probe turn through per hold window."""
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

from backend import health_monitor, login_holds
from backend.store import H
from backend.tests.test_api import api, assign, claim, expire, get, headers, post, ready, runner

CLAUDE = "You've hit your session limit · resets 2:40am (UTC)"


def test_a_reset_in_words_is_honored_and_a_past_or_unreadable_one_falls_back_to_the_backoff():
    hit = datetime(2026, 10, 19, 1, 46, 47, tzinfo=timezone.utc)
    assert login_holds.reset_from_text(CLAUDE, hit) == datetime(2026, 10, 19, 2, 40, tzinfo=timezone.utc)
    assert login_holds.reset_from_text("resets Oct 24, 9am (UTC)", hit) == datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
    # Just past (it names 1:40 at 1:46), an unknown zone, or no time at all: no reset.
    assert login_holds.reset_from_text("resets 1:40am (UTC)", hit) is None
    assert login_holds.reset_from_text("resets 2:40am (Mars/Base)", hit) is None
    assert login_holds.reset_from_text("Quota exceeded. Check your plan and billing details.", hit) is None
    # Never a hold of no time, and a weekly reset days away is kept as it is.
    assert login_holds.believable("2026-10-19T01:00:00Z", hit) is None
    assert login_holds.believable("2026-10-23T09:00:00Z", hit)
    assert login_holds.believable("2026-11-19T09:00:00Z", hit) is None


def limit(api, machine, attempt, errors=(CLAUDE,), **extra):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "limit-" + attempt["id"]}, machine["token"])
    events = [{"seq": n, "kind": "error", "payload": {"error": text, "limit": True}} for n, text in enumerate(errors, 1)]
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    return post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": len(events), "limited": True,
                                                           "text": "", **extra}, machine["token"])


def done(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "ok-" + attempt["id"]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])


def at(monkeypatch, when, api=None):
    """The clock reads `when`; the test computer has been reporting in all along, not waking from a silence."""
    monkeypatch.setattr(H, "now", lambda: when)
    if api is not None:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runners SET last_seen_at=?,awake_since=NULL", (when,))


def test_thirteen_queued_jobs_after_a_limit_start_one_probe_at_the_reset_not_thirteen_turns(api, monkeypatch):
    # The shape of Oct 19 02:25Z: a bot's queued tasks all waited on one spent login and all started together,
    # 15 minutes before the reset the provider had named.
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    assign(api, machine, "finance")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    ready(api, machine, ["ops", "finance"])
    for n in range(12):
        post(api, "tasks", {"owner": "ops", "title": f"Review change {n}", "body": "Please review it."})
    first = claim(api, machine)
    assert limit(api, machine, first)["outcome"] == "failed"
    # Another bot signed in with the same login on this computer queues work too: 13 jobs wait on one login.
    post(api, "tasks", {"owner": "finance", "title": "Close the month", "body": "Please do it."})
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        assert hold["until"].startswith("2026-10-19T02:40:00") and hold["reset"]
        assert "resumes at 02:40 UTC when it resets" in H.status(c, "ops")["focus"]
    at(monkeypatch, "2026-10-19T02:25:15.000000Z", api)
    ready(api, machine, ["ops", "finance"])
    assert claim(api, machine) is None                      # Dev 198's half hour has passed; the login's reset has not
    at(monkeypatch, "2026-10-19T02:40:01.000000Z", api)
    ready(api, machine, ["ops", "finance"])
    probe = claim(api, machine)
    assert probe and probe["bot"] in ("ops", "finance")    # both wait on the login; either may go first
    assert claim(api, machine) is None                      # one probe per login per window; the other jobs wait
    done(api, machine, probe)
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, machine["runner_id"], "", "fake")
    assert claim(api, machine)                              # the login works again: the queue moves


def test_a_hold_survives_a_runner_restart_and_a_lost_probe_is_retried_as_the_same_job(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=8 WHERE id=?", (machine["runner_id"],))
    ready(api, machine, ["ops"])
    for n in range(3):
        post(api, "tasks", {"owner": "ops", "title": f"Task {n}", "body": "Please do it."})
    first = claim(api, machine)
    limit(api, machine, first, errors=("Quota exceeded. Check your plan and billing details.",))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
    # No reset given: the first limit holds 30 minutes, saved on the server, so a runner that restarts (and forgets
    # everything it knew) still gets nothing on the login before then.
    assert hold["until"].startswith("2026-10-19T02:16:47") and not hold["reset"]
    # The computer's config carries it, so a restarted runner keeps the login out of its fallback choice too.
    assert get(api, "config", machine["token"])["login_holds"] == [{"profile": "", "runtime": "fake", "until": hold["until"]}]
    at(monkeypatch, "2026-10-19T02:16:00.000000Z", api)
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None
    at(monkeypatch, "2026-10-19T02:17:00.000000Z", api)
    ready(api, machine, ["ops"])
    probe = claim(api, machine)
    # The runner restarts before the probe begins: its lease lapses and the job goes back in the queue. Only that job
    # may start on the login again; a second job would be a second turn in the same window.
    expire(api, probe["id"])
    ready(api, machine, ["ops"])
    again = claim(api, machine)
    assert again and again["job_id"] == probe["job_id"]
    assert claim(api, machine) is None
    # It hits the limit again: the second in a row holds an hour.
    limit(api, machine, again, errors=("Quota exceeded. Check your plan and billing details.",))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        assert hold["streak"] == 2 and hold["until"].startswith("2026-10-19T03:17:00")
        assert "API quota or billing limit" in H.status(c, "ops")["focus"]
    # Health names the provider whose login is held, not just the bot's own.
    issue = next(i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i.get("bot") == "ops")
    assert issue["detail"].startswith("Primary fake API quota or billing limit; queued work waits until 2026-10-19 03:17 UTC")


def test_one_hold_covers_every_bot_on_the_login_and_a_persons_message_still_gets_one_try(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    assign(api, machine, "finance")
    ready(api, machine, ["ops", "finance"])
    post(api, "tasks", {"owner": "ops", "title": "First", "body": "Please do it."})
    limit(api, machine, claim(api, machine), errors=("Usage limit",))
    post(api, "tasks", {"owner": "finance", "title": "Close the month", "body": "Please do it."})
    ready(api, machine, ["ops", "finance"])
    assert claim(api, machine) is None                      # finance signs in with the same login on this computer
    at(monkeypatch, "2026-10-19T01:48:00.000000Z", api)
    post(api, "chat/finance", {"text": "I renewed the plan, try again."})
    retry = claim(api, machine)
    assert retry and retry["bot"] == "finance"


def test_try_now_on_a_shared_login_starts_one_probe_across_bots_and_a_second_limit_puts_the_window_back(
        api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    assign(api, machine, "finance")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    ready(api, machine, ["ops", "finance"])
    for n in range(3):
        post(api, "tasks", {"owner": "ops", "title": f"Ops {n}", "body": "Please do it."})
        post(api, "tasks", {"owner": "finance", "title": f"Finance {n}", "body": "Please do it."})
    limit(api, machine, claim(api, machine))
    at(monkeypatch, "2026-10-19T02:00:00.000000Z", api)
    ready(api, machine, ["ops", "finance"])
    assert claim(api, machine) is None
    # Try now, though the plan may still be spent: one queued job across both bots tries the login first.
    post(api, "bots/ops/limit/retry", {})
    probe = claim(api, machine)
    assert probe and claim(api, machine) is None
    post(api, "bots/finance/limit/retry", {}, expected=409)  # the probe is out: a second Try now adds no turn
    # Still spent, and this notice names no reset: the 02:40 window comes back, not a fresh streak's hour.
    limit(api, machine, probe, errors=("Quota exceeded. Check your plan and billing details.",))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
    assert hold["until"].startswith("2026-10-19T02:40:00") and hold["streak"] == 1
    ready(api, machine, ["ops", "finance"])
    assert claim(api, machine) is None
    # Renewed for real this time: the probe works and the whole fleet resumes.
    post(api, "bots/finance/limit/retry", {})
    probe = claim(api, machine)
    assert probe and claim(api, machine) is None
    done(api, machine, probe)
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, machine["runner_id"], "", "fake")
    ready(api, machine, ["ops", "finance"])
    assert {claim(api, machine)["bot"], claim(api, machine)["bot"]} == {"ops", "finance"}


def signed_in(api, machine, bots, runtimes):
    """A heartbeat from a computer whose bots are ready and on which `runtimes` are signed in."""
    report = {"schema_version": 1,
              "runtimes": {name: {"installed": True, "authenticated": "ready"} for name in runtimes},
              "bots": {bot: {"ready": True, "runtime": "fake", "repository_present": True, "configuration_valid": True}
                       for bot in bots}}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": report}, token=machine["token"])


def test_a_held_login_sends_the_turn_to_a_ready_fallback_and_waits_only_when_none_is_free(api, monkeypatch):
    # Dev 137's rule: a bot whose own login is spent runs on its fallback when one is ready. The hold only keeps the
    # turn off the spent login; it waits only when every fallback is held too, or there is none.
    view = get(api, "providers")
    api.put("/api/v2/providers", json={"enabled": ["openai", "anthropic"], "model": view["default"]["model"] or "",
                                       "expected_revision": view["revision"]},
            headers={**headers(), "Idempotency-Key": "held-fallback"}).raise_for_status()
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=8 WHERE id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops"], ["codex"])
    for n in range(3):
        post(api, "tasks", {"owner": "ops", "title": f"Task {n}", "body": "Please do it."})
    first = claim(api, machine)
    assert "primary_held" not in first["config"]
    limit(api, machine, first)
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
    # Codex is signed in here and nothing holds it: the next job starts at once, told the bot's own login is held
    # until its reset, so the runner starts it on the fallback and never tries the spent login.
    at(monkeypatch, "2026-10-19T01:50:00.000000Z", api)
    signed_in(api, machine, ["ops"], ["codex"])
    moved = claim(api, machine)
    assert moved and moved["config"]["primary_held"] == hold["until"]
    post(api, f"attempts/{moved['id']}/started", {"thread_id": "codex-" + moved["id"]}, machine["token"])
    post(api, f"attempts/{moved['id']}/events", {"events": [{"seq": 1, "kind": "diagnostic", "payload": {
        "text": "fake login held (usage limit) until 02:40 UTC; running this turn on codex", "fallback": "codex"}}]},
         machine["token"])
    post(api, f"attempts/{moved['id']}/complete", {"outcome": "completed", "last_seq": 1, "fallback": "codex",
                                                   "text": "Done on Codex."}, machine["token"])
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine["runner_id"], "", "fake") == hold     # the held login was not tried again
    # Codex then meets its own limit on a later turn: both logins are held, so the work waits.
    moved = claim(api, machine)
    assert moved["config"]["primary_held"]
    limit(api, machine, moved, errors=("Quota exceeded. Check your plan and billing details.",), fallback="codex")
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine["runner_id"], "", "codex")["until"].startswith("2026-10-19T02:20:00")
        assert login_holds.get(c, machine["runner_id"], "", "fake")["streak"] == 1
    at(monkeypatch, "2026-10-19T01:51:00.000000Z", api)
    signed_in(api, machine, ["ops"], ["codex"])
    assert claim(api, machine) is None
    # Codex's hold ends first; the bot's own login is still held, so the turn goes to Codex again.
    at(monkeypatch, "2026-10-19T02:21:00.000000Z", api)
    signed_in(api, machine, ["ops"], ["codex"])
    assert claim(api, machine)["config"]["primary_held"] == hold["until"]


@pytest.mark.parametrize("alternate", [False, True])
def test_a_same_login_named_fallback_uses_the_next_company_row_or_waits_without_a_lease(api, monkeypatch, alternate):
    from backend.execution import fallback_choice
    view = get(api, "providers")
    api.put("/api/v2/providers", json={"enabled": ["openai", "anthropic"] if alternate else ["openai"],
                                       "model": "gpt-6-luna", "expected_revision": view["revision"]},
            headers={**headers(), "Idempotency-Key": "same-login-fallback"}).raise_for_status()
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    config = {"runtime": "codex", "harness": "codex", "model": "gpt-6-luna",
              "fallback": {"harness": "codex", "model": "gpt-6-luna"}}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_patch(config,?) WHERE bot='ops'", (json.dumps(config),))
    signed_in(api, machine, ["ops"], ["codex", "claude"])
    for n in range(2):
        post(api, "tasks", {"owner": "ops", "title": f"Same login work {n}", "body": "Please do it."})
    limit(api, machine, claim(api, machine))
    at(monkeypatch, "2026-10-19T01:50:00.000000Z", api)
    with api.app.state.store.read() as c:
        assert fallback_choice(c, api.app.state.store.settings, machine["runner_id"], "ops", config) == (
            ("", "claude") if alternate else None)
    next_turn = claim(api, machine)
    if alternate:
        assert next_turn and next_turn["config"]["primary_held"]
    else:
        assert next_turn is None
        with api.app.state.store.read() as c:
            assert login_holds.get(c, machine["runner_id"], "", "codex")["until"].startswith("2026-10-19T02:40")
            assert H.status(c, "ops")["state"] == "limited"


def test_a_bot_whose_fallback_is_off_or_not_signed_in_waits_out_the_hold(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=8 WHERE id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops"], [])
    for n in range(2):
        post(api, "tasks", {"owner": "ops", "title": f"Task {n}", "body": "Please do it."})
    limit(api, machine, claim(api, machine))
    at(monkeypatch, "2026-10-19T01:50:00.000000Z", api)
    signed_in(api, machine, ["ops"], [])
    assert claim(api, machine) is None                      # no other provider is signed in here
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.fallback',json('{\"off\":true}')) "
                  "WHERE bot='ops'")
    signed_in(api, machine, ["ops"], ["codex"])
    assert claim(api, machine) is None                      # the bot turned its fallback off


def providers_on(api):
    view = get(api, "providers")
    api.put("/api/v2/providers", json={"enabled": ["openai", "anthropic"], "model": view["default"]["model"] or "",
                                       "expected_revision": view["revision"]},
            headers={**headers(), "Idempotency-Key": f"providers-{view['revision']}"}).raise_for_status()


def two_bots(api, monkeypatch, runtimes=("codex",)):
    providers_on(api)
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    for bot in ("ops", "finance"):
        assign(api, machine, bot)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops", "finance"], runtimes)
    return machine


def events(api, machine, attempt, rows):
    post(api, f"attempts/{attempt['id']}/events", {"events": [
        {"seq": n, "kind": kind, "payload": {"error": text, "limit": True} if kind == "error" else {"text": text}}
        for n, (kind, text) in enumerate(rows, 1)]}, machine["token"])
    return len(rows)


def test_a_fallback_that_finishes_the_turn_keeps_the_spent_login_held_for_every_bot(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    post(api, "tasks", {"owner": "ops", "title": "Work 0", "body": "Please do it."})
    first = claim(api, machine)
    for n, bot in enumerate(("ops", "finance"), 1):
        post(api, "tasks", {"owner": bot, "title": f"Work {n}", "body": "Please do it."})
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t-" + first["id"]}, machine["token"])
    seq = events(api, machine, first, [("error", CLAUDE), ("diagnostic", "fake hit its usage limit; running this "
                                        "turn on codex (no fallback set; another provider signed in here)")])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": seq, "fallback": "codex",
                                                   "primary_limited": True, "text": "Done on Codex."}, machine["token"])
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
    assert hold["until"].startswith("2026-10-19T02:40:00")
    # Before the reset, the next job of each bot is sent straight to Codex: neither tries the spent login again.
    at(monkeypatch, "2026-10-19T01:50:00.000000Z", api)
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    moved = [claim(api, machine) for _ in range(2)]
    assert sorted(m["bot"] for m in moved) == ["finance", "ops"]
    assert all(m["config"]["primary_held"] == hold["until"] for m in moved)


def test_a_fallback_login_whose_hold_ends_gets_one_probe_not_every_waiting_job(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    for n, bot in enumerate(("ops", "ops", "ops", "finance", "finance")):
        post(api, "tasks", {"owner": bot, "title": f"Work {n}", "body": "Please do it."})
    limit(api, machine, claim(api, machine))                                    # Claude (fake) held until 02:40
    with api.app.state.store.transaction() as c:
        login_holds.record(c, machine["runner_id"], "", "codex", "2026-10-19T01:50:00.000000Z")   # Codex to 02:20
    at(monkeypatch, "2026-10-19T02:00:00.000000Z", api)
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    assert claim(api, machine) is None                                          # both logins held: everyone waits
    at(monkeypatch, "2026-10-19T02:21:00.000000Z", api)
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    probe = claim(api, machine)
    assert probe["config"]["primary_held"]
    assert claim(api, machine) is None                                          # the rest wait for Codex's probe
    expire(api, probe["id"])                                                    # the runner restarts before it begins
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    again = claim(api, machine)
    assert again and again["job_id"] == probe["job_id"]
    assert claim(api, machine) is None
    post(api, f"attempts/{again['id']}/started", {"thread_id": "codex-" + again["id"]}, machine["token"])
    seq = events(api, machine, again, [("diagnostic", "fake login held (usage limit) until 02:40 UTC; running this "
                                                      "turn on codex")])
    post(api, f"attempts/{again['id']}/complete", {"outcome": "completed", "last_seq": seq, "fallback": "codex",
                                                   "primary_held": True, "text": "Done."}, machine["token"])
    with api.app.state.store.read() as c:                                       # Codex works: the others move
        assert not login_holds.get(c, machine["runner_id"], "", "codex")
        assert login_holds.get(c, machine["runner_id"], "", "fake")["until"].startswith("2026-10-19T02:40")
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    assert claim(api, machine)["config"]["primary_held"]


class Body:
    def __init__(self, **values):
        self.__dict__.update({"limited": False, "fallback": None, "primary_limited": False, "primary_held": False,
                              "profile_used": None, "limit_resets_at": None, "outcome": "failed", "usage": None,
                              **values})


TURNS = {}


def attempt_with(c, rows):
    """A finished turn's limit notices and fallback notes, as login_holds.limit_texts reads them."""
    aid = H.new_id()
    TURNS[aid] = [("limit" if kind == "error" else "note", text) for kind, text in rows]
    return aid


HIT = "2026-10-19T01:46:47.000000Z"


def test_each_limit_is_held_on_the_login_it_came_from_however_the_turn_ends(api, monkeypatch):
    monkeypatch.setattr(login_holds, "limit_texts", lambda c, aid: TURNS[aid])
    with api.app.state.store.transaction() as c:
        # Claude used tokens and then ran out; Codex refused before any token (so no usage segment names it).
        aid = attempt_with(c, [("error", CLAUDE), ("diagnostic", "fake hit its usage limit; running this turn on codex"),
                               ("error", "Quota exceeded. Check your plan and billing details.")])
        login_holds.after_turn(c, "r1", "ops", "", "fake", Body(limited=True, fallback="codex"), aid, HIT)
        assert login_holds.get(c, "r1", "", "fake")["until"].startswith("2026-10-19T02:40:00")
        codex = login_holds.get(c, "r1", "", "codex")
        assert codex["until"].startswith("2026-10-19T02:16:47") and codex["error"].startswith("Quota exceeded")
        # On a named subscription, the fallback's hold is on the profile the runner says it used.
        aid = attempt_with(c, [("diagnostic", "fake hit its usage limit; running this turn on codex"),
                               ("error", "Quota exceeded.")])
        login_holds.after_turn(c, "r2", "ops", "work", "fake", Body(limited=True, fallback="codex",
                                                                    profile_used="work"), aid, HIT)
        assert login_holds.get(c, "r2", "work", "codex") and login_holds.get(c, "r2", "work", "fake")
        assert not login_holds.get(c, "r2", "", "codex")
        # Three providers: Codex runs out in the middle and Gemini finishes. Codex stays held with its own window.
        aid = attempt_with(c, [("error", CLAUDE), ("diagnostic", "fake hit its usage limit; running this turn on codex"),
                               ("error", "Quota exceeded."),
                               ("diagnostic", "codex hit its usage limit; running this turn on gemini")])
        assert login_holds.after_turn(c, "r3", "ops", "", "fake", Body(outcome="completed", fallback="gemini"),
                                      aid, HIT) == {}
        assert login_holds.get(c, "r3", "", "fake")["reset"]
        assert login_holds.get(c, "r3", "", "codex")["until"].startswith("2026-10-19T02:16:47")
        assert not login_holds.get(c, "r3", "", "gemini")
        # A turn sent away from the held login does not count as a new limit on it.
        held = login_holds.get(c, "r3", "", "fake")
        aid = attempt_with(c, [("diagnostic", "fake login held (usage limit) until 02:40 UTC; running this turn on "
                                              "gemini")])
        login_holds.after_turn(c, "r3", "ops", "", "fake", Body(outcome="completed", fallback="gemini",
                                                                primary_held=True), aid, "2026-10-19T01:55:00.000000Z")
        assert login_holds.get(c, "r3", "", "fake") == held
    # And a restarted runner hears of both held logins.
    at(monkeypatch, "2026-10-19T01:55:00.000000Z")
    machine = runner(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET key=replace(key,'r3|',?) WHERE key LIKE 'login-hold:r3|%'",
                  (machine["runner_id"] + "|",))
    at_config = {h["runtime"] for h in get(api, "config", machine["token"])["login_holds"]}
    assert {"fake", "codex"} <= at_config


def test_a_lane_leased_before_the_limit_cannot_clear_the_login_hold(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:30.000000Z")
    machine = runner(api)
    for bot in ("ops", "finance"):
        assign(api, machine, bot)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    ready(api, machine, ["ops", "finance"])
    post(api, "tasks", {"owner": "ops", "title": "Long work", "body": "Please do it."})
    post(api, "tasks", {"owner": "finance", "title": "Close the month", "body": "Please do it."})
    early, spent = claim(api, machine), claim(api, machine)
    at(monkeypatch, "2026-10-19T01:46:47.000000Z", api)
    limit(api, machine, spent)                                  # the other lane runs the login out
    done(api, machine, early)                                   # the lane leased before it ends well afterwards
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine["runner_id"], "", "fake")["until"].startswith("2026-10-19T02:40:00")
    owner = early["bot"]
    post(api, "tasks", {"owner": owner, "title": "Next", "body": "Please do it."})
    ready(api, machine, ["ops", "finance"])
    assert claim(api, machine) is None                          # no bot starts on the held login before the reset


def test_a_turn_with_no_fallback_ready_after_all_settles_unstarted_and_the_bot_waits(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    for n in range(2):
        post(api, "tasks", {"owner": "ops", "title": f"Work {n}", "body": "Please do it."})
    limit(api, machine, claim(api, machine), errors=(QUOTA,))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
    at(monkeypatch, "2026-10-19T01:50:00.000000Z", api)
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    moved = claim(api, machine)
    assert moved["config"]["primary_held"]
    # The runner finds Codex not ready after all: it never starts the turn and says so, with no /started.
    seq = events(api, machine, moved, [("error", "fake login held (usage limit) until 02:40 UTC, and no fallback is "
                                                 "ready on this computer")])
    post(api, f"attempts/{moved['id']}/complete", {"outcome": "failed", "last_seq": seq, "limited": True,
                                                   "primary_held": True, "text": ""}, machine["token"])
    with api.app.state.store.read() as c:
        after = login_holds.get(c, machine["runner_id"], "", "fake")
        assert c.execute("SELECT state FROM jobs WHERE id=?", (moved["job_id"],)).fetchone()[0] == "queued"
    assert (after["until"], after["streak"], after["stuck"]) == (hold["until"], 1, ["ops"])
    # It waits on the earlier API quota hold: not reported as a fresh plan limit.
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["focus"].startswith("Primary fake API quota or billing limit, held from an earlier "
                                                      "turn; no fallback could take this one; retrying after "
                                                      "2026-10-19 02:16 UTC")
    assert health_detail(api, "ops").startswith("Primary fake API quota or billing limit, held from an earlier turn")
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    assert claim(api, machine) is None                          # not sent to that fallback again this window


def test_the_runners_report_of_a_fallback_that_never_started_requeues_the_job_and_holds_no_other_login(api, monkeypatch):
    # Exactly what the runner sends when a held login's fallback fails before it acknowledges the turn: its own test
    # drives that turn and checks it against the same fixture (runner-rs/src/execute.rs).
    sent = json.loads((Path(__file__).resolve().parents[2] / "runner-rs/tests/fixtures/held_fallback_unstarted.json").read_text())
    machine = two_bots(api, monkeypatch)
    for n in range(2):
        post(api, "tasks", {"owner": "ops", "title": f"Started nowhere {n}", "body": "Please do it."})
    limit(api, machine, claim(api, machine), errors=(QUOTA,))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
    at(monkeypatch, "2026-10-19T01:50:00.000000Z", api)
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    moved = claim(api, machine)
    assert moved["config"]["primary_held"]
    post(api, f"attempts/{moved['id']}/events", {"events": [{"seq": n, **e} for n, e in enumerate(sent["events"], 1)]},
         machine["token"])
    post(api, f"attempts/{moved['id']}/complete", {**sent["completion"], "last_seq": len(sent["events"])}, machine["token"])
    with api.app.state.store.read() as c:
        after = login_holds.get(c, machine["runner_id"], "", "fake")
        assert c.execute("SELECT state FROM jobs WHERE id=?", (moved["job_id"],)).fetchone()[0] == "queued"
        # The fallback never ran, so it met no limit: only the bot's own login is held, as it was.
        assert [r[0] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'login-hold:%'")] == \
            [f"login-hold:{machine['runner_id']}||fake"]
    assert (after["until"], after["streak"], after["stuck"]) == (hold["until"], 1, ["ops"])
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    assert claim(api, machine) is None                          # waits out the hold, not leased again and again


def test_profiles_sharing_the_computers_own_login_share_its_hold_and_probe(api, monkeypatch):
    from backend import execution
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    row = {"installed": True, "authenticated": "ready"}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "bots": {"ops": {"ready": True, "runtime": "fake"}},
        "runtimes": {"fake": {**row, "profiles": {"team-a": {**row, "shared_login": True},
                                                  "team-b": {**row, "shared_login": True}, "solo": row}}}}},
         token=machine["token"])
    rid = machine["runner_id"]
    with api.app.state.store.transaction() as c:
        assert [login_holds.canonical(c, rid, p, "fake") for p in ("", "team-a", "team-b", "solo")] == ["", "", "", "solo"]
        aid = H.new_id()
        monkeypatch.setattr(login_holds, "limit_texts", lambda c, a: [("limit", CLAUDE)])
        login_holds.after_turn(c, rid, "ops", "team-a", "fake", Body(limited=True), aid, HIT)
        # One hold for the computer's own login, whichever alias met the limit, and the runner hears it under "".
        assert login_holds.get(c, rid, "", "fake")["until"].startswith("2026-10-19T02:40") and not \
            login_holds.get(c, rid, "team-a", "fake")
        # A bot runs on the computer's own login (#1259), so its turns wait on that hold.
        key = execution.login_key(c, api.app.state.store.settings, rid, "ops", {"runtime": "fake"})
        assert login_holds.holding(c, *key) == "held"
    assert get(api, "config", machine["token"])["login_holds"][0]["profile"] == ""


def test_a_named_fallback_that_is_not_ready_here_leaves_the_job_waiting_not_leased(api, monkeypatch):
    from backend import models_preset
    machine = two_bots(api, monkeypatch, runtimes=())
    for n in range(3):
        post(api, "tasks", {"owner": "ops", "title": f"Work {n}", "body": "Please do it."})
    limit(api, machine, claim(api, machine))

    def names(fallback):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bot_config SET config=json_set(config,'$.fallback',json(?)) WHERE bot='ops'",
                      (json.dumps(fallback),))
    names({"harness": "codex", "model": "gpt-5.5"})
    for minute in ("01:50", "01:55", "02:00"):                      # Codex is not signed in here: no lease, ever
        at(monkeypatch, f"2026-10-19T{minute}:00.000000Z", api)
        signed_in(api, machine, ["ops", "finance"], ())
        assert claim(api, machine) is None
    # A Tico model named as the fallback needs a computer where bots run as their own user; this one is not.
    names({"harness": "pi", "model": models_preset.WAITING_MODEL})
    signed_in(api, machine, ["ops", "finance"], ())
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0] == 1
    # Once Codex signs in, the named fallback takes the next job.
    names({"harness": "codex", "model": "gpt-5.5"})
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    assert claim(api, machine)["config"]["primary_held"]


QUOTA = "Quota exceeded. Check your plan and billing details."


def fallback_turn(api, machine, rows, **result):
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, machine["token"])
    seq = events(api, machine, attempt, rows)
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": seq, "limited": True,
                                                     "fallback": "codex", "text": "", **result}, machine["token"])
    return attempt


def health_detail(api, bot):
    return next(i for i in api.get("/api/status", headers=headers()).json()["health_issues"] if i.get("bot") == bot)["detail"]


def test_a_fallbacks_quota_names_the_fallback_and_never_holds_the_primary(api, monkeypatch):
    # Oct 19 20:05Z: the Codex fallback said "Quota exceeded" while Claude was not limited, and the bot read
    # "claude API quota or billing limit" (Dev 279).
    machine = two_bots(api, monkeypatch)
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    first = fallback_turn(api, machine, [("diagnostic", "fake unavailable; running this turn on codex"),
                                         ("error", QUOTA)])
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, machine["runner_id"], "", "fake")
        assert login_holds.get(c, machine["runner_id"], "", "codex")["until"].startswith("2026-10-19T02:16:47")
        focus = H.status(c, "ops")["focus"]
    assert focus == ("Fallback codex API quota or billing limit at 01:46; primary fake not limited; "
                     "retrying after 2026-10-19 02:16 UTC")
    assert health_detail(api, "ops").startswith("Fallback codex API quota or billing limit; primary fake not limited; "
                                                "queued work waits until 2026-10-19 02:16 UTC")
    log = api.get(f"/api/runs/{first['id']}/log", headers=headers()).text
    assert "Limit: Fallback codex API quota or billing limit; primary fake not limited" in log
    # Another bot's turn on the same Codex login replaces its hold: Health still names the fallback, not the bot's
    # own runtime.
    with api.app.state.store.transaction() as c:
        login_holds.record(c, machine["runner_id"], "", "codex", "2026-10-19T01:47:00.000000Z", attempt_id="other",
                           bot="finance", error=QUOTA)
        assert not login_holds.for_attempt(c, first["id"])
    assert health_detail(api, "ops").startswith("Fallback codex API quota or billing limit; primary fake not limited")


def test_an_older_runners_fallback_quota_with_no_hop_note_is_still_the_fallbacks(api, monkeypatch):
    monkeypatch.setattr(login_holds, "limit_texts", lambda c, aid: TURNS[aid])
    with api.app.state.store.transaction() as c:
        aid = attempt_with(c, [("error", QUOTA)])
        login_holds.after_turn(c, "r1", "ops", "", "fake", Body(limited=True, fallback="codex"), aid, HIT)
        assert not login_holds.get(c, "r1", "", "fake")
        assert login_holds.get(c, "r1", "", "codex")["error"] == QUOTA
        # Unless the runner says the primary was limited: then its notice is the primary's, as before.
        aid = attempt_with(c, [("error", CLAUDE)])
        login_holds.after_turn(c, "r2", "ops", "", "fake", Body(limited=True, fallback="codex", primary_limited=True),
                               aid, HIT)
        assert login_holds.get(c, "r2", "", "fake")["reset"] and login_holds.get(c, "r2", "", "codex")


PAUSED = ("Tico models are paused: this company is out of credit. Your bots use their other models until credit is "
          "added (out_of_credit)")


def test_a_company_out_of_credit_holds_the_tico_login_a_minute_and_a_fallbacks_own_limit_as_before(api, monkeypatch):
    """The Tico AI Gateway's out_of_credit is the company's wait, not a spent login: the Tico login is held for the
    Gateway's minute, never the backoff, so the next turn tries Tico models again within about a minute."""
    monkeypatch.setattr(login_holds, "limit_texts", lambda c, aid: TURNS[aid])
    hop = ("diagnostic", "pi hit its usage limit; running this turn on codex")
    with api.app.state.store.transaction() as c:
        aid = attempt_with(c, [("error", PAUSED), hop])
        kept = login_holds.after_turn(c, "r1", "ops", "", "pi", Body(outcome="completed", fallback="codex",
                                                                     primary_limited=True), aid, HIT)
        assert kept == {} and login_holds.get(c, "r1", "", "pi")["until"].startswith("2026-10-19T01:47:47")
        # Its fallback ran out too: that login is held as before, with its own wait.
        aid = attempt_with(c, [("error", PAUSED), hop, ("error", QUOTA)])
        kept = login_holds.after_turn(c, "r2", "ops", "", "pi", Body(limited=True, fallback="codex",
                                                                     primary_limited=True), aid, HIT)
        assert kept["runtime"] == "codex" and kept["until"].startswith("2026-10-19T02:16:47")
        src = login_holds.source(c, aid, "pi", Body(limited=True, fallback="codex", primary_limited=True))
        assert login_holds.label(src) == ("Fallback codex API quota or billing limit; primary pi also limited "
                                          "(Tico models paused, out of credit)")


RATED = "Tico AI Gateway: rate limited; retrying after the Gateway's requested wait (rate_limited)"


def test_a_tico_rate_limit_another_model_finished_holds_the_tico_login_a_minute_not_the_backoff(api, monkeypatch):
    """The Tico AI Gateway's rate_limited is the key's minute window, not a spent login: when the bot's fallback
    finishes the turn, its Tico login is held for a minute (no Retry-After reaches the server then), never 30 minutes,
    and a second one in a row is a minute again, not an hour."""
    monkeypatch.setattr(login_holds, "limit_texts", lambda c, aid: TURNS[aid])
    hop = ("diagnostic", "pi hit its usage limit; running this turn on codex")
    done = Body(outcome="completed", fallback="codex", primary_limited=True)
    with api.app.state.store.transaction() as c:
        aid = attempt_with(c, [("error", RATED), hop])
        assert login_holds.after_turn(c, "r1", "ops", "", "pi", done, aid, HIT) == {}
        assert login_holds.get(c, "r1", "", "pi")["until"].startswith("2026-10-19T01:47:47")
        later = "2026-10-19T01:48:00.000000Z"
        aid = attempt_with(c, [("error", RATED), hop])
        login_holds.after_turn(c, "r1", "ops", "", "pi", done, aid, later)
        assert login_holds.get(c, "r1", "", "pi")["until"].startswith("2026-10-19T01:49:00")
        # Its fallback ran out too: that login is held as before, with its own wait, and named as the Gateway's.
        aid = attempt_with(c, [("error", RATED), hop, ("error", QUOTA)])
        body = Body(limited=True, fallback="codex", primary_limited=True)
        kept = login_holds.after_turn(c, "r2", "ops", "", "pi", body, aid, HIT)
        assert kept["runtime"] == "codex" and kept["until"].startswith("2026-10-19T02:16:47")
        assert login_holds.get(c, "r2", "", "pi")["until"].startswith("2026-10-19T01:47:47")
        assert login_holds.label(login_holds.source(c, aid, "pi", body)) == (
            "Fallback codex API quota or billing limit; primary pi also limited (Tico AI Gateway rate limit)")


def test_a_gateway_reset_already_past_is_its_minute_not_the_backoff(api, monkeypatch):
    """A runner whose clock is behind reports the Gateway's Retry-After as a reset already past: the Tico login is
    held the Gateway's minute, not 30 minutes."""
    monkeypatch.setattr(login_holds, "limit_texts", lambda c, aid: TURNS[aid])
    with api.app.state.store.transaction() as c:
        aid = attempt_with(c, [("error", PAUSED)])
        past = Body(limited=True, limit_resets_at="2026-10-19T01:46:00.000000Z")
        login_holds.after_turn(c, "r1", "ops", "", "pi", past, aid, HIT)
        assert login_holds.get(c, "r1", "", "pi")["until"].startswith("2026-10-19T01:47:47")
        # A rate limit the turn ended on with no Retry-After keeps the safe hour: early tries can't reach the Gateway.
        aid = attempt_with(c, [("error", RATED)])
        login_holds.after_turn(c, "r2", "ops", "", "pi", Body(limited=True), aid, HIT)
        assert login_holds.get(c, "r2", "", "pi")["until"].startswith("2026-10-19T02:46:47")


def test_a_primary_held_for_hours_with_its_tico_fallback_out_of_credit_waits_only_the_gateways_minute(
        api, monkeypatch):
    """A subscription bot whose last fallback is a Tico model: its own login stays held to its reset, but the turn
    ended on the company's credit, so the bot asks again after the Gateway's minute and runs on Tico models."""
    from backend.execution import cooling_off, limit_cooldown
    from clients import gateway
    at(monkeypatch, HIT)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    hop = "fake hit its usage limit; running this turn on pi"
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, machine["token"])
    seq = events(api, machine, attempt, [("error", CLAUDE), ("diagnostic", hop), ("error", PAUSED)])
    post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "failed", "last_seq": seq, "limited": True, "fallback": "pi", "primary_limited": True, "text": "",
        "limit_resets_at": "2026-10-19T01:47:47.000000Z"}, machine["token"])
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine["runner_id"], "", "fake")["until"].startswith("2026-10-19T02:40:00")
        assert login_holds.get(c, machine["runner_id"], "", "pi")["until"].startswith("2026-10-19T01:47:47")
        assert limit_cooldown(c, "ops") <= gateway.OUT_OF_CREDIT_WAIT + 1
        assert H.status(c, "ops")["focus"].startswith(gateway.PAUSED_MESSAGE + " at 01:46")
    # After the minute the bot is free again while its own login is still held: the next turn goes to its fallback.
    at(monkeypatch, "2026-10-19T01:48:00.000000Z", api)
    with api.app.state.store.read() as c:
        assert not cooling_off(c, "ops")
        assert login_holds.get(c, machine["runner_id"], "", "fake")["until"] > H.now()


def test_a_primary_usage_limit_names_the_primary_and_its_window(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    limit(api, machine, claim(api, machine))
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["focus"] == ("Primary fake plan usage limit (5-hour window) at 01:46; resumes at "
                                               "02:40 UTC when it resets")
        assert not login_holds.get(c, machine["runner_id"], "", "codex")
    assert health_detail(api, "ops").startswith("Primary fake plan usage limit (5-hour window); resumes at 02:40 UTC")


def test_both_limits_in_one_window_keep_two_holds_each_with_its_own_retry(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    first = fallback_turn(api, machine, [("error", CLAUDE), ("diagnostic", "fake hit its usage limit; running this "
                                                                          "turn on codex"), ("error", QUOTA)])
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine["runner_id"], "", "fake")["until"].startswith("2026-10-19T02:40:00")
        assert login_holds.get(c, machine["runner_id"], "", "codex")["until"].startswith("2026-10-19T02:16:47")
        # The bot waits on the login its turn ended on, whichever hold row is read first.
        assert login_holds.for_attempt(c, first["id"])["runtime"] == "codex"
        focus = H.status(c, "ops")["focus"]
    assert focus == ("Fallback codex API quota or billing limit at 01:46; primary fake also limited (plan usage limit "
                     "(5-hour window)) until 02:40 UTC; retrying after 2026-10-19 02:16 UTC")


def test_a_limit_keeps_the_provider_and_login_it_came_from_after_settings_and_holds_change(api, monkeypatch):
    at(monkeypatch, "2026-10-19T01:46:47.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    first = claim(api, machine)
    limit(api, machine, first, errors=(QUOTA,), profile_used="work")
    said = "Primary fake (the work login) API quota or billing limit"
    with api.app.state.store.transaction() as c:
        assert H.status(c, "ops")["focus"].startswith(said + " at 01:46")
        # The bot moves to Codex and another turn replaces the login's hold: the run still names what refused it.
        assert c.execute("UPDATE bot_config SET config=? WHERE bot='ops'",
                         (json.dumps({"runtime": "codex"}),)).rowcount == 1
        assert login_holds.get(c, machine["runner_id"], "work", "fake"), "an in-flight turn keeps its reported home"
        login_holds.record(c, machine["runner_id"], "work", "fake", "2026-10-19T01:47:00.000000Z", attempt_id="other",
                           bot="finance", error=CLAUDE)
        assert not login_holds.for_attempt(c, first["id"])
        assert json.loads(c.execute("SELECT detail FROM events WHERE action='attempt.limited' AND target=?",
                                    (first["id"],)).fetchone()[0])["source"]["profile"] == "work"
    assert f"Limit: {said}" in api.get(f"/api/runs/{first['id']}/log", headers=headers()).text
    assert health_detail(api, "ops").startswith(said)


def test_a_late_limit_from_a_turn_started_before_the_hold_keeps_the_live_try_now_probe(api, monkeypatch):
    at(monkeypatch, '2026-10-19T01:00:00.000000Z')
    machine = runner(api)
    for bot in ('ops', 'finance'):
        assign(api, machine, bot)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET capacity=16 WHERE id=?', (machine['runner_id'],))
    ready(api, machine, ['ops', 'finance'])
    for n in range(3):
        for bot in ('ops', 'finance'):
            post(api, 'tasks', {'owner': bot, 'title': f'{bot} task {n}', 'body': 'Please review it.'})
    first, late = claim(api, machine), claim(api, machine)
    post(api, f"attempts/{late['id']}/started", {'thread_id': 'late-original'}, machine['token'])
    at(monkeypatch, '2026-10-19T01:01:00.000000Z', api)
    limit(api, machine, first)
    at(monkeypatch, '2026-10-19T01:02:00.000000Z', api)
    ready(api, machine, ['ops', 'finance'])
    post(api, f"bots/{first['bot']}/limit/retry", {})
    probe = claim(api, machine)
    assert probe and claim(api, machine) is None
    post(api, f"attempts/{probe['id']}/started", {'thread_id': 'live-probe'}, machine['token'])
    # The turn started before the hold now ends on the limit too, while the probe is still running.
    at(monkeypatch, '2026-10-19T01:03:00.000000Z', api)
    post(api, f"attempts/{late['id']}/events", {'events': [
        {'seq': 1, 'kind': 'error', 'payload': {'error': 'Quota exceeded.', 'limit': True}}]}, machine['token'])
    post(api, f"attempts/{late['id']}/complete", {'outcome': 'failed', 'last_seq': 1, 'limited': True, 'text': ''},
         machine['token'])
    ready(api, machine, ['ops', 'finance'])
    response = api.post(f"/api/v2/bots/{late['bot']}/limit/retry", json={}, headers=headers())
    assert response.status_code == 409, response.text
    assert claim(api, machine) is None
    # The probe's own result still decides the login: its success frees the waiting work.
    post(api, f"attempts/{probe['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Done.'},
         machine['token'])
    ready(api, machine, ['ops', 'finance'])
    assert claim(api, machine)


def test_a_model_at_capacity_holds_the_login_minutes_and_a_later_real_quota_is_still_a_quota_hold(api, monkeypatch):
    # Oct 23 03:04Z: "Selected model is at capacity" held the shared Codex login for an hour as spent quota; the probe
    # after it met "Quota exceeded", which is the plan's and must stay a full hold.
    at(monkeypatch, "2026-10-23T03:04:45.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Work", "body": "Please do it."})
    post(api, "tasks", {"owner": "ops", "title": "More work", "body": "Please do it."})
    # The runner's plan-window reset hours away says nothing about a busy model.
    limit(api, machine, claim(api, machine), errors=("Selected model is at capacity. Please try a different model.",),
          limit_resets_at="2026-10-23T08:00:00Z")
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        assert hold["until"].startswith("2026-10-23T03:06:45") and hold["capacity"] and hold["streak"] == 0
        assert H.status(c, "ops")["focus"] == ("Primary fake temporary model capacity at 03:04; retrying after "
                                               "2026-10-23 03:06 UTC")
        # A busy moment is not a spent plan: Health does not say the bot stopped on a subscription limit for it.
        assert not [f for f in health_monitor._bot_checks(c, api.app.state.store.settings)
                    if f["check_id"] == "limited_no_fallback"]
    at(monkeypatch, "2026-10-23T03:06:46.000000Z", api)
    ready(api, machine, ["ops"])
    limit(api, machine, claim(api, machine), errors=("Quota exceeded. Check your plan and billing details.",))
    with api.app.state.store.read() as c:
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        # The first quota limit in a row: the 30-minute backoff, not a streak the capacity hold grew.
        assert hold["until"].startswith("2026-10-23T03:36:46") and not hold["capacity"] and hold["streak"] == 1
        assert "API quota or billing limit" in H.status(c, "ops")["focus"]


def test_a_capacity_notice_during_a_quota_hold_leaves_the_providers_reset_where_it_was(api):
    with api.app.state.store.transaction() as c:
        quota = login_holds.record(c, "r1", "", "codex", "2026-10-23T04:30:00.000000Z", reset="2026-10-23T05:00:30Z",
                                   error="Quota exceeded. Check your plan and billing details.")
        busy = login_holds.record(c, "r1", "", "codex", "2026-10-23T05:00:00.000000Z",
                                  error="Selected model is at capacity. Please try a different model.")
    assert (busy["until"], busy["streak"], busy["reset"], busy["capacity"]) == (quota["until"], 1, True, False)


def test_canonical_parses_the_readiness_once_per_transaction(api, monkeypatch):
    # A claim scan asks for the same login per bot and per job; the readiness document is parsed once per login.
    from backend import store as S
    machine = runner(api)
    row = {"installed": True, "authenticated": "ready"}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
        "schema_version": 1, "bots": {}, "runtimes": {"fake": {**row, "profiles": {
            "team-a": {**row, "shared_login": True}, "solo": row}}}}}, token=machine["token"])
    rid = machine["runner_id"]
    parsed, real = [], S.readiness_document
    monkeypatch.setattr(S, "readiness_document", lambda value: parsed.append(1) or real(value))
    with api.app.state.store.transaction() as c:
        assert [login_holds.canonical(c, rid, p, "fake") for p in ("team-a", "solo", "team-a")] == ["", "solo", ""]
        assert len(parsed) == 2                 # one parse per login asked for: team-a and solo
        assert [login_holds.canonical(c, rid, "team-a", "fake") for _ in range(3)] == [""] * 3 and len(parsed) == 2
        # A write in the same transaction is seen: the runner now shares its login, and its answer changes.
        c.execute("UPDATE runners SET readiness=? WHERE id=?", (json.dumps({"schema_version": 1, "bots": {}, "runtimes": {
            "fake": {**row, "profiles": {"solo": {**row, "shared_login": True}}}}}), rid))
        assert login_holds.canonical(c, rid, "solo", "fake") == ""
