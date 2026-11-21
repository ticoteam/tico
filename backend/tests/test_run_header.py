"""The run header (backend/run_header.py): a card run and a person's Assistant turn start from what is true as the run
starts, read as whoever the turn acts for, and a header that cannot be read in time never holds up the turn."""

import json

from backend import hubdb as H
from backend import run_header
from backend import stage_rules
from backend.auth import Identity
from backend.tests.helpers.api import assign, post, ready, runner
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.engine import new_task, ok_ask, ran


def ops_runner(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    return r


def card_for_ops(api, title="Ship the launch note"):
    """A General task owned by ops, with a stage rule on its stage. Returns its id."""
    with api.app.state.store.transaction() as c:
        task = new_task(c, "human:ana", title, assignee="bot:ops")
        stage_rules.add(
            c,
            "human:ana",
            {
                "name": "Bots own the work",
                "pipeline_id": task["pipeline_id"],
                "stages": [task["stage_id"]],
                "event": "owner_changes",
                "then": "block",
                "reason": "Only an agent takes this stage's work",
            },
        )
        return task["id"]


def test_a_card_run_starts_from_where_the_card_stands_now(api, monkeypatch):
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 30.0)
    r = ops_runner(api)
    tid = card_for_ops(api)
    attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    assert attempt["task"]["id"] == tid
    header = attempt["run_header"]
    assert header.startswith(run_header.HEAD), header
    assert "Card now: Ship the launch note is in To do (General)" in header
    assert "Whose turn: " in header and "Missing to leave this stage:" in header
    assert "Rules on this stage:\n- Bots own the work: Only an agent takes this stage's work" in header
    assert len(header) <= run_header.MAX_CHARS


def test_changes_since_the_agents_last_run_are_what_others_did_after_it(api):
    r = ops_runner(api)
    tid = card_for_ops(api)
    with api.app.state.store.transaction() as c:
        assert ran(c, tid, "bot:ops", at=H.shift(H.now(), minutes=-5), runner=r["runner_id"]) >= 1
        H.history_event(c, "task", tid, "human:ben", "title", "Ship it", "Ship the launch note")
        H.history_event(c, "task", tid, "bot:ops", "checkpoint", None, "Drafted")
    with api.app.state.store.read_transaction() as c:
        lines = run_header.card(c, api.app.state.auth, Identity("bot:ops", "bot"), "bot:ops", tid)
    changes = lines[next(i for i, line in enumerate(lines) if line.startswith("Changes since your last run")) :]
    assert any(line.endswith(": changed title") for line in changes), lines
    assert not any("checkpoint" in line for line in changes), "its own change is not news to it"


def test_the_assistant_snapshot_holds_only_its_persons_own_items(api, monkeypatch):
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 30.0)
    with api.app.state.store.transaction() as c:
        mine = new_task(c, "human:ana", "Ship the launch note", assignee="bot:ops")
        c.execute("UPDATE tasks SET readers=? WHERE id=?", (json.dumps(["human:cara"]), mine["id"]))
        ok_ask(c, mine["id"], "bot:ops", "cara", "OK the launch note")
        theirs = new_task(c, "human:ana", "Payroll change for March", assignee="bot:ops")
        ok_ask(c, theirs["id"], "bot:ops", "ana", "OK the payroll change")
        token = H.VIA.set("assistant")
        try:
            H.task_create(c, "human:cara", "Book the venue", "For the offsite.", "bot:ops")
            H.task_create(c, "human:ana", "Ana's private handoff", "Hers alone.", "bot:ops", private=True)
        finally:
            H.VIA.reset(token)
    _, attempt = assistant_turn(api, "cara-test", "What needs me today?")
    header = attempt["run_header"]
    assert "Needs you (" in header and "Ship the launch note" in header, header
    assert "Open handoffs you filed through me:\n- Book the venue" in header, header
    assert "Payroll" not in header and "payroll" not in header and "Ana's private handoff" not in header, header


def test_a_header_past_its_budget_is_left_out_and_the_turn_still_runs(api, monkeypatch):
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 0.0)
    r = ops_runner(api)
    tid = card_for_ops(api)
    attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    assert attempt["task"]["id"] == tid and "run_header" not in attempt


def test_a_long_header_is_cut_to_its_cap_with_what_was_left_out():
    text = run_header.capped([f"- item {n} " + "x" * 80 for n in range(60)])
    assert len(text) <= run_header.MAX_CHARS
    kept = text.splitlines()
    assert kept[-1] == f"+{60 - (len(kept) - 2)} more", kept[-1]
