"""The run header (backend/run_header.py): a card run and a person's Assistant turn start from what is true as the run
starts, read as whoever the turn acts for, and a header that cannot be read in time never holds up the turn."""

import json
import threading

from backend import hubdb as H
from backend import run_header
from backend import task_engine as E
from backend import stage_rules
from backend.auth import Identity
from backend.tests.helpers.api import assign, post, ready, runner
from backend.tests.helpers.assistant import assistant_turn
from backend.tests.helpers.engine import answer, new_task, ok_ask, ran, shown


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


def card_as(api, who, tid, bot="bot:ops"):
    with api.app.state.store.read_transaction() as c:
        return "\n".join(run_header.card(c, api.app.state.auth, who, bot, tid))


def test_changes_since_the_agents_last_run_are_what_others_did_after_it_as_its_history_shows_them(api):
    r = ops_runner(api)
    tid = card_for_ops(api)
    with api.app.state.store.transaction() as c:
        assert ran(c, tid, "bot:ops", at=H.shift(H.now(), minutes=-5), runner=r["runner_id"]) >= 1
        hers = H.task_create(c, "human:ana", "Ana's own errand", "Hers alone.", "human:ana", private=True)["id"]
        H.history_event(c, "task", tid, "human:ben", "title", "Ship it", "Ship the launch note")
        H.history_event(c, "task", tid, "human:ben", "handoff_filed", None, hers)
        H.history_event(c, "task", tid, "bot:ops", "checkpoint", None, "Drafted")
    text = card_as(api, Identity("bot:ops", "bot"), tid)
    changes = text[text.index("Changes since your last run") :]
    assert "Ben: changed title" in changes, text
    assert "handoff filed" not in changes, "a change naming a task it may not open is not shown"
    assert "checkpoint" not in changes, "its own change is not news to it"


def test_a_bound_oks_words_reach_only_its_parties_and_an_approval_names_its_version(api):
    tid = card_for_ops(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET readers=? WHERE id=?", (json.dumps(["human:cara"]), tid))
        page = shown(c, "Launch note", "ops", task_id=tid)
        given = ok_ask(c, tid, "bot:ops", "ana", "OK the launch note", shows=page)
        answer(c, E.task(c, tid), given["requirement_id"], "ok", "human:ana")
        bound = ok_ask(c, tid, "human:ana", "ben", "OK paying Dana's salary advance")
        answer(c, E.task(c, tid), bound["requirement_id"], "no", "human:ben", notes="Dana's figure is wrong")
        c.execute(
            "INSERT INTO ok_grants (requirement_id, asked_by_id, asked_of_id, subject, content_hash, content, filed, "
            "created_at) VALUES (?,?,'human:ben','pay','h','{}',0,?)",
            (bound["requirement_id"], "human:ana", H.now()),
        )
    party = card_as(api, Identity("human:ben", "human", "ben@acme.example"), tid)
    assert "Dana's figure is wrong" in party, party
    agent = card_as(api, Identity("bot:ops", "bot"), tid)
    assert f"- OK the launch note: OK'd (approved {page[0]}@1)" in agent, agent
    for reader in (agent, card_as(api, Identity("human:cara", "human", "cara@acme.example"), tid)):
        assert "Card now: Ship the launch note" in reader and "Dana" not in reader, reader


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


def test_a_claim_is_answered_while_a_stalled_header_is_still_being_built(api, monkeypatch):
    release, started = threading.Event(), threading.Event()

    def stalled(*_):
        started.set()
        release.wait()
        return "too late"

    monkeypatch.setattr(run_header, "_build", stalled)
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 0.05)
    r = ops_runner(api)
    tid = card_for_ops(api)
    try:
        attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
        assert started.is_set() and not release.is_set()
        assert attempt["task"]["id"] == tid and "run_header" not in attempt
    finally:
        release.set()


def test_a_long_header_is_cut_to_its_cap_with_what_was_left_out():
    text = run_header.capped([f"- item {n} " + "x" * 80 for n in range(60)])
    assert len(text) <= run_header.MAX_CHARS
    kept = text.splitlines()
    assert kept[-1] == f"+{60 - (len(kept) - 2)} more", kept[-1]
