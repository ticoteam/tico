"""The run header (backend/run_header.py): a card run and a person's Assistant turn start from what is true as the run
starts, read as whoever the turn acts for, and a header that cannot be read in time never holds up the turn."""

import json
import threading

from backend import files
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
        # A newer run that started and has not ended does not hide the last one that did.
        job = c.execute("SELECT job_id, message_id FROM attempts WHERE task_id=?", (tid,)).fetchone()
        c.execute(
            "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, "
            "created_at, started_at, message_id, task_id) VALUES (?,?,'ops',?,1,'h','running',?,?,?,?,?)",
            (H.new_id(), job[0], r["runner_id"], H.now(), H.now(), H.now(), job[1], tid),
        )
    text = card_as(api, Identity("bot:ops", "bot"), tid)
    changes = text[text.index("Changes since your last run") :]
    assert "Ben: changed title" in changes, text
    assert "handoff filed" not in changes, "a change naming a task it may not open is not shown"
    assert "checkpoint" not in changes, "its own change is not news to it"


def test_the_header_describes_the_task_the_run_was_recorded_for(api, monkeypatch):
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 30.0)
    r = ops_runner(api)
    carrier = card_for_ops(api, "Card A")
    attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    assert attempt["task"]["id"] == carrier
    with api.app.state.store.transaction() as c:
        other = new_task(c, "human:ana", "Card B", assignee="bot:ops")["id"]
        # A carrier in A's room starts a run for B: the run is recorded for B.
        c.execute("UPDATE attempts SET task_id=? WHERE id=?", (other, attempt["id"]))
    with api.app.state.store.read_transaction() as c:
        lines, _ = run_header.for_turn(c, api.app.state.auth, attempt)
    assert lines[0].startswith("Card now: Card B is in"), lines


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
            venue = H.task_create(c, "human:cara", "Book the venue", "For the offsite.", "bot:ops", pipeline="general")
            H.task_create(c, "human:ana", "Ana's private handoff", "Hers alone.", "bot:ops", private=True)
            # Not hers to track: one filed for Ben, and one she keeps herself.
            his = H.task_create(c, "human:cara", "Ben's onboarding", "For him.", "bot:ops")["id"]
            H.task_create(c, "human:cara", "Cara's own notes", "Mine.", "human:cara")
            nobodys = H.task_create(c, "human:cara", "Nobody's errand", "Unassigned.", "bot:ops")["id"]
        finally:
            H.VIA.reset(token)
        c.execute("UPDATE task_move_guards SET open=1, task_id=? WHERE id=1", (nobodys,))  # as a move opens it
        c.execute("UPDATE tasks SET owner_id=NULL WHERE id=?", (nobodys,))  # unassigned
        c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
        c.execute("UPDATE tasks SET requester_id='human:ben' WHERE id=?", (his,))  # filed --for Ben
        held = ok_ask(c, venue["id"], "human:ana", "ben", "OK Dana's catering deposit")
        bind(c, held["requirement_id"], "human:ana", "human:ben")
    _, attempt = assistant_turn(api, "cara-test", "What needs me today?")
    header = attempt["run_header"]
    assert "Needs you (" in header and "Ship the launch note" in header, header
    assert "Open handoffs you filed through me:\n- Book the venue" in header, header
    assert "Payroll" not in header and "payroll" not in header and "Ana's private handoff" not in header, header
    assert "Dana" not in header, "a handoff's turn says a bound OK only to its parties"
    assert "- OK the launch note (Ship the launch note)" in header, "Needs you reads as the list they are served"
    assert "Ben's onboarding" not in header and "Cara's own notes" not in header, header
    assert "Nobody's errand" not in header, "an unassigned task is not a handoff, as in the Tracker"
    with api.app.state.store.read_transaction() as c:
        cara = Identity("human:cara", "human", "cara@acme.example")
        asked = []
        c.set_trace_callback(asked.append)
        try:
            run_header.snapshot(c, api.app.state.auth, cara)
        finally:
            c.set_trace_callback(None)
        assert sum("json_extract(detail,'$.via')='assistant'" in sql for sql in asked) == 1, "one handoff query"
        full = ["Card now: " + "y" * 90] * 19  # what the card took already: no room left for a handoff
        lines, unseen = run_header.snapshot(c, api.app.state.auth, cara, full)
    assert unseen == 1 and not any("Book the venue" in line for line in lines), "past its room a handoff is not read"


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


def bind(c, requirement_id, asked_by, asked_of):
    """Make an OK a bound one (task_asks.ask_for): its words are its parties' alone."""
    c.execute(
        "INSERT INTO ok_grants (requirement_id, asked_by_id, asked_of_id, subject, content_hash, content, filed, "
        "created_at) VALUES (?,?,?,'pay','h','{}',0,?)",
        (requirement_id, asked_by, asked_of, H.now()),
    )


def test_whose_turn_says_a_bound_ok_only_as_far_as_its_reader_may_read_it(api):
    tid = card_for_ops(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET readers=? WHERE id=?", (json.dumps(["human:cara"]), tid))
        bound = ok_ask(c, tid, "human:ana", "ben", "OK paying Dana's salary advance")
        bind(c, bound["requirement_id"], "human:ana", "human:ben")
    party = card_as(api, Identity("human:ben", "human", "ben@acme.example"), tid)
    assert "Whose turn: Ben: " in party and "Dana" in party, party
    other = card_as(api, Identity("human:cara", "human", "cara@acme.example"), tid)
    assert "Whose turn: Ben: " in other and "Dana" not in other, other


def test_a_requirement_whose_page_is_gone_is_left_out_and_whose_turn_says_only_its_label(api):
    tid = card_for_ops(api)
    with api.app.state.store.transaction() as c:
        page = shown(c, "Zebra plan", "ops", task_id=tid)
        ok_ask(c, tid, "bot:ops", "ben", "OK the Zebra budget", shows=page)
        c.execute("UPDATE bot_files SET state='deleted' WHERE id=?", (page[0][len("page:") :],))
    text = card_as(api, Identity("bot:ops", "bot"), tid)
    turn = next(line for line in text.splitlines() if line.startswith("Whose turn: Ben: "))
    assert turn[len("Whose turn: Ben: ") :] in files.REMOVED_LABELS, text
    assert "Zebra" not in text and sum(text.count(label) for label in files.REMOVED_LABELS) == 1, text


def test_an_ended_card_still_says_where_it_stands(api):
    tid = card_for_ops(api)
    with api.app.state.store.transaction() as c:
        E.move(c, tid, E.CLOSED, "human:ana", "No longer needed")
    text = card_as(api, Identity("bot:ops", "bot"), tid)
    assert text.startswith("Card now: Ship the launch note is closed in Closed; version"), text


def test_a_make_page_turn_counts_changes_from_its_last_page_for_the_same_person(api):
    r = ops_runner(api)
    tid = card_for_ops(api)
    ana = Identity("human:ana", "owner", "ana@acme.example")
    with api.app.state.store.transaction() as c:
        # The page was asked for in the person's own chat, not on the task.
        other = new_task(c, "human:ana", "Another errand", assignee="bot:ops")["id"]
        msg = c.execute(
            "SELECT m.id, j.id AS job_id FROM messages m JOIN jobs j ON j.message_id=m.id WHERE m.wake_key LIKE ?",
            (other + ":%",),
        ).fetchone()
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps({"make_page": {"task": tid}}), msg["id"]))
        earlier = H.shift(H.now(), minutes=-5)
        c.execute(
            "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, created_at, "
            "started_at, finished_at, message_id, acting_person) VALUES (?,?,'assistant',?,1,'h','failed',?,?,?,?,?,?)",
            (H.new_id(), msg["job_id"], r["runner_id"], earlier, earlier, earlier, earlier, msg["id"], ana.actor),
        )
        H.history_event(c, "task", tid, "human:ben", "title", "Ship it", "Ship the launch note")
    with api.app.state.store.read_transaction() as c:
        made = run_header.card(c, api.app.state.auth, ana, "bot:assistant", tid, making=True)
        chat = run_header.card(c, api.app.state.auth, ana, "bot:assistant", tid)
    assert "- Ben: changed title" in made, made
    assert not any(line.startswith("Changes since") for line in chat), chat


def test_a_replayed_claim_whose_lease_is_no_longer_live_gets_no_header(api, monkeypatch):
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 30.0)
    r = ops_runner(api)
    card_for_ops(api)
    first = post(api, "jobs/claim", {}, token=r["token"], key="claim:once")["attempt"]
    assert "run_header" in first
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), first["id"]))
    again = post(api, "jobs/claim", {}, token=r["token"], key="claim:once")["attempt"]
    assert again["id"] == first["id"] and "run_header" not in again


def test_a_turn_that_moved_on_while_its_header_was_written_gets_none(api, monkeypatch):
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 30.0)
    store = api.app.state.store

    def started_meanwhile(c, auth, attempt, deadline):
        with store.transaction() as w:
            w.execute("UPDATE attempts SET state='running' WHERE id=?", (attempt["id"],))
        return ["Card now: written before it started."], 0

    monkeypatch.setattr(run_header, "for_turn", started_meanwhile)
    r = ops_runner(api)
    card_for_ops(api)
    assert "run_header" not in post(api, "jobs/claim", {}, token=r["token"])["attempt"]


def test_a_turn_for_a_person_who_cannot_be_resolved_has_no_header_and_never_the_agents(api, monkeypatch):
    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 30.0)
    r = ops_runner(api)
    card_for_ops(api)
    attempt = post(api, "jobs/claim", {}, token=r["token"])["attempt"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET acting_person='human:ana' WHERE id=?", (attempt["id"],))
    with api.app.state.store.read_transaction() as c:
        assert run_header.for_turn(c, api.app.state.auth, attempt) == ([], 0)


def test_a_native_command_or_a_turn_taken_whole_by_the_page_filter_says_no_more():
    command = {"id": "a1", "message": {"refs": {"command": True}}}
    assert run_header.build(None, None, command) is None, "a native command reads no store"
    taken = {"removed": True, "label": "A deleted page"}
    assert run_header._turn_line({"to_leave": []}, taken) == "A deleted page"


def test_no_header_is_built_when_every_worker_is_busy(api):
    held = [run_header._SLOTS.acquire(blocking=False) for _ in range(run_header.WORKERS)]
    try:
        assert all(held)
        assert run_header.build(api.app.state.store, api.app.state.auth, {"id": "nope"}) is None
    finally:
        for _ in held:
            run_header._SLOTS.release()


def test_a_long_header_keeps_whole_entries_with_their_heading_and_counts_only_entries_left_out():
    long = "Card now: " + "y" * 700
    bullets = [f"- item {n} " + "x" * 80 for n in range(30)]
    text = run_header.capped(
        [long, "Missing to leave this stage:", *bullets, "Rules on this stage:", "- " + "z" * 1500]
    )
    assert len(text) <= run_header.MAX_CHARS
    kept = text.splitlines()
    assert kept[1] == long, "an entry is kept whole, never cut"
    assert kept[2] == "Missing to leave this stage:" and kept[3] == bullets[0]
    assert "Rules on this stage:" not in kept, "a heading goes only with a bullet of its own"
    shown = len([line for line in kept if line.startswith("- item")])
    assert kept[-1] == f"+{30 - shown + 1} more", kept[-1]


def test_each_section_is_cut_on_its_own_and_no_bullet_moves_under_another_heading():
    text = run_header.capped(
        ["Met:", "- " + "m" * 1950, "Missing to leave this stage:", "- Review: not yet.", "Rules:"]
    )
    assert text.splitlines()[1:] == ["Missing to leave this stage:", "- Review: not yet.", "+1 more"], text
