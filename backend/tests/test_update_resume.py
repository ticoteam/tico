"""After an update's drain, queued work starts again by itself (Dev 684).

A synthetic update: the server moves to a new release, the computer drains (a running turn finishes or reaches the
drain time limit, claims that had not started are handed back), updates, and comes back current. Nobody resumes or
pokes anything, and every hold that is not the update's stays in force. Each test prints the admission, hold, drain and
runner-version state before and after the update clears.
"""

import json

import pytest

from backend import execution, org_turns
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401

OLD, NEW = "0.4.15", "0.4.16"
CAPPED = "Paused at the computer's drain time limit; work and session saved, queued to continue"
WAITING = "Handed back before starting because the computer is updating or restarting; queued to continue"


def beat(api, machine, bots, release, update):
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test", "release": release,
                "readiness": {b: True for b in bots}, "update": update}, machine["token"])


def capture(api, machine, bots):
    """Admission, holds, drain and runner version, the way the claim and Health read them."""
    with api.app.state.store.read() as c:
        version = dict(c.execute("SELECT release,update_state,update_target FROM runner_versions WHERE runner_id=?",
                                 (machine["runner_id"],)).fetchone() or {})
        return {"at": H.now(), "server": NEW, "runner": version,
                "paused": execution.runner_versions.paused(c, machine["runner_id"]),
                "holding_since": execution.runner_versions.holding_since(c, machine["runner_id"]),
                "draining": sorted(r[0] for r in c.execute("SELECT bot FROM bot_control WHERE draining=1")),
                "bot_state": {b: H.bot(c, b)["state"] for b in bots},
                "status": {b: {k: (H.status(c, b) or {}).get(k) for k in ("state", "focus")} for b in bots},
                "jobs": {b: [(r[0], r[1]) for r in c.execute("SELECT state,created_at FROM jobs WHERE bot=? ORDER BY created_at",
                                                             (b,))] for b in bots},
                "refusal": {b: execution.bot_refusal(c, api.app.state.store.settings, machine["runner_id"], b)
                            for b in bots}}


def receipt(name, before, after, started):
    print(json.dumps({"case": name, "before": before, "after": after,
                      "started": {b: {"job_id": a["job_id"], "at": a["claimed_at"]} for b, a in started.items()}}, indent=1, default=str))


def drain(api, machine, attempt, how):
    """End a turn the way the runner's drain does: `capped` at the time limit, `waiting` handed back unstarted,
    `held` a turn that cannot safely continue, `finished_at` a turn that completed before the drain ended."""
    if how == "finished":
        post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Done.", "last_seq": 0},
             machine["token"])
        return
    events = [{"seq": 1, "kind": "status", "payload": {"yielded": CAPPED if how == "capped" else WAITING}}]
    if how != "held":
        post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "yielded": how != "held",
         "last_seq": 0 if how == "held" else 1}, machine["token"])


def claim_all(api, machine, tries=6):
    started = {}
    for _ in range(tries):
        got = claim(api, machine)
        if got:
            started[got["bot"]] = {**got, "claimed_at": H.now()}
    return started


def pause(api, bot):
    post(api, f"bots/{bot}/control", {"action": "pause", "expected_revision": get(api, f"bots/{bot}")["revision"]})


@pytest.mark.parametrize("ending", ["capped", "finished"])
def test_queued_work_starts_after_the_update_clears_and_an_operator_pause_stays(api, monkeypatch, ending):
    """A time-limit drain (`capped`) and an ordinary one (`finished_at`): with no resume, message or poke, the queued work
    starts as soon as the computer is back current, while the bot a person paused stays paused."""
    monkeypatch.setenv("TICO_VERSION", NEW)
    machine = runner(api)
    bots = ("ops", "finance", "inbox", "doc-updater")
    for bot in bots:
        assign(api, machine, bot)
    beat(api, machine, bots, OLD, {"state": "idle"})
    for bot in bots:
        post(api, "tasks", {"owner": bot, "title": "Check the synthetic report", "body": "Fixture data."})
    pause(api, "doc-updater")
    running = claim(api, machine, "ops")
    post(api, f"attempts/{running['id']}/started", {"thread_id": "midturn"}, machine["token"])
    unstarted = claim(api, machine, "finance")
    beat(api, machine, bots, OLD, {"state": "waiting", "target": NEW, "running": 1, "handed_back": 0})
    drain(api, machine, unstarted, "waiting")
    drain(api, machine, running, ending)
    beat(api, machine, bots, OLD, {"state": "updating", "target": NEW, "running": 0, "handed_back": 1})
    before = capture(api, machine, bots)
    beat(api, machine, bots, NEW, {"state": "idle", "target": NEW})
    after = capture(api, machine, bots)
    started = claim_all(api, machine)
    receipt(f"{ending} drain", before, after, started)
    expected = {"finance", "inbox"} | ({"ops"} if ending == "capped" else set())
    assert set(started) == expected, (before, after, started)
    assert before["holding_since"] and not after["holding_since"] and not after["paused"]
    # The person's pause is theirs: clearing the update leaves it, and its work waits for their resume.
    assert after["draining"] == ["doc-updater"] and after["bot_state"]["doc-updater"] == "paused"
    assert after["refusal"]["doc-updater"] == "draining"
    if ending == "capped":
        assert before["status"]["ops"]["focus"] == CAPPED


def test_a_capped_turn_continues_after_it_finished_its_own_task(api, monkeypatch):
    """The live v0.4.16 cutover: a turn marked its task done, then reached the drain time limit. The claim's sweep of
    notices for finished tasks cancelled the continuation, leaving the bot "queued to continue" with nothing queued."""
    monkeypatch.setenv("TICO_VERSION", NEW)
    machine = runner(api)
    bots = ("ops", "finance")
    for bot in bots:
        assign(api, machine, bot)
    beat(api, machine, bots, OLD, {"state": "idle"})
    post(api, "chat/finance", {"text": "Please ask Ops to check the synthetic report."})
    filer = claim(api, machine, "finance")
    post(api, f"attempts/{filer['id']}/started", {"thread_id": "filer"}, machine["token"])
    task = post(api, "tasks", {"owner": "ops", "title": "Check the synthetic report", "body": "Fixture data."},
                token=filer["token"])
    post(api, f"attempts/{filer['id']}/complete", {"outcome": "completed", "text": "Filed.", "last_seq": 0},
         machine["token"])
    turn = claim(api, machine, "ops")
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "midturn"}, machine["token"])
    current = get(api, "tasks/" + task["id"], token=turn["token"])
    post(api, "tasks/" + task["id"], {"version": current.get("task", current)["version"], "status": "done", "note": "Checked."},
         token=turn["token"])
    beat(api, machine, bots, OLD, {"state": "waiting", "target": NEW, "running": 1, "handed_back": 0})
    drain(api, machine, turn, "capped")
    beat(api, machine, bots, OLD, {"state": "updating", "target": NEW, "running": 0, "handed_back": 0})
    before = capture(api, machine, bots)
    beat(api, machine, bots, NEW, {"state": "idle", "target": NEW})
    after = capture(api, machine, bots)
    started = claim_all(api, machine, tries=2)
    receipt("capped turn after its task finished", before, after, started)
    assert "ops" in started, (before, after, started)
    assert started["ops"]["job_id"] == turn["job_id"]


def test_a_drained_turn_that_cannot_continue_is_held_with_its_reason_and_other_work_starts(api, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", NEW)
    machine = runner(api)
    bots = ("ops", "finance")
    for bot in bots:
        assign(api, machine, bot)
    beat(api, machine, bots, OLD, {"state": "idle"})
    for bot in bots:
        post(api, "tasks", {"owner": bot, "title": "Check the synthetic report", "body": "Fixture data."})
    turn = claim(api, machine, "ops")
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "midturn"}, machine["token"])
    beat(api, machine, bots, OLD, {"state": "waiting", "target": NEW, "running": 1, "handed_back": 0})
    drain(api, machine, turn, "held")
    beat(api, machine, bots, OLD, {"state": "updating", "target": NEW, "running": 0, "handed_back": 0})
    before = capture(api, machine, bots)
    beat(api, machine, bots, NEW, {"state": "idle", "target": NEW})
    after = capture(api, machine, bots)
    started = claim_all(api, machine)
    receipt("held turn", before, after, started)
    assert set(started) == {"finance"}, (before, after, started)
    # Never replayed blind: the job waits for a review, and says why it stopped.
    assert after["jobs"]["ops"][0][0] == "uncertain"
    with api.app.state.store.read() as c:
        attempt = c.execute("SELECT * FROM attempts WHERE id=?", (turn["id"],)).fetchone()
        assert execution.STOP_WHY[execution.stop_cause(attempt)] == "Interrupted on its computer"
    assert "queued to continue" not in (after["status"]["ops"]["focus"] or "")


def test_holds_that_are_not_the_updates_stay_in_force_when_it_clears(api, monkeypatch):
    """A computer on a release below the minimum, a usage limit and the company's turn cap each keep holding their
    work after another computer's update clears, and a computer still updating keeps reporting its hold. (An updating
    runner stops claiming on its own side, runner-rs follow.rs `blocks_claims`; the server does not refuse it.)"""
    monkeypatch.setenv("TICO_VERSION", NEW)
    current, updating, old = runner(api, label="Current"), runner(api, label="Updating"), runner(api, label="Old")
    places = [(current, ("ops", "finance")), (updating, ("inbox",)), (old, ("doc-updater",))]
    for machine, bots in places:
        for bot in bots:
            assign(api, machine, bot)
        beat(api, machine, bots, OLD, {"state": "idle"})
        for bot in bots:
            post(api, "tasks", {"owner": bot, "title": "Check the synthetic report", "body": "Fixture data."})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_status(bot,state,since,updated_at) VALUES('finance','limited',?,?) "
                  "ON CONFLICT(bot) DO UPDATE SET state='limited',since=excluded.since", (H.now(), H.now()))
    for machine, bots in places:
        beat(api, machine, bots, OLD, {"state": "updating", "target": NEW, "running": 0, "handed_back": 0})
    before = {m["runner_id"]: capture(api, m, bots) for m, bots in places}
    beat(api, current, places[0][1], NEW, {"state": "idle", "target": NEW})
    beat(api, old, places[2][1], "0.0.9", {"state": "idle"})
    after = {m["runner_id"]: capture(api, m, bots) for m, bots in places}
    started = {m["runner_id"]: claim_all(api, m, tries=3) for m in (current, old)}
    print(json.dumps({"case": "independent holds", "before": before, "after": after,
                      "started": {r: sorted(s) for r, s in started.items()}}, indent=1, default=str))
    assert set(started[current["runner_id"]]) == {"ops"}
    assert after[current["runner_id"]]["refusal"]["finance"] == "cooling_off"
    assert after[updating["runner_id"]]["holding_since"]
    assert not started[old["runner_id"]] and after[old["runner_id"]]["paused"]
    # The company's turn cap holds everything, whatever the computers' updates did.
    real = org_turns.observe
    monkeypatch.setattr(org_turns, "observe", lambda *a, **k: {**real(*a, **k), "at_stop": True})
    ops = started[current["runner_id"]]["ops"]
    post(api, f"attempts/{ops['id']}/started", {"thread_id": "ops"}, current["token"])
    post(api, f"attempts/{ops['id']}/complete", {"outcome": "completed", "text": "Done.", "last_seq": 0},
         current["token"])
    post(api, "tasks", {"owner": "ops", "title": "Check the second synthetic report", "body": "Fixture data."})
    got = post(api, "jobs/claim", {"bot": None, "start_places": True}, token=current["token"])
    assert got["attempt"] is None and got.get("waiting") == org_turns.REASON
