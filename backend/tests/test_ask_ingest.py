"""Ask ingest (backend/ask_ingest.py, spec ask-ingest.md v2): a bot's task for a person is judged before it lands.
With checks On, the budget tightens as the person's plate fills; SENT_BACK or FYI refuses the write
and the error says why and how to fix it, naming the bot's manager. A person's own write is never gated, and any
failure lets the item through as before."""

import importlib
import json
import sqlite3
import threading
import time

import pytest

from backend import ask_ingest as A
from backend import call_slots as CS
from backend import hubdb as H
from backend.tests.test_api import unrelated_task_managers
from backend.tests.test_task_guest import run_on
from backend.tests.test_task_guest import task as private_task
from backend.tests.test_api import setup_attempt  # noqa: F401
from backend.tests.test_file_reviews import publish
from backend.tests.test_tasks_board import api, bot_token, get, headers, post   # noqa: F401


class FakeModel:
    """Answers the set as told: clear/recommends as probabilities, kind, meets (the option whose line holds
    `meets`), covered (the option whose title holds `covered`) and urgency 0-3."""

    def __init__(self, clear=0.95, kind="decision", meets=None, urgency=1.0, recommends=0.1, covered=None):
        self.clear, self.kind, self.meets, self.urgency = clear, kind, meets, urgency
        self.recommends, self.covered = recommends, covered
        self.calls, self.fail, self.hold, self.before = [], None, None, None

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        if self.before:                  # a test changes the world between the judgment and the write
            self.before()
        if self.hold:
            self.hold.wait(5)
        if self.fail:
            raise self.fail

        def pick(qid, needle, default):
            options = questions.get(qid, {}).get("criteria") or {}
            return next((n for n, text in options.items() if needle and needle in str(text)), default)
        answers = {"clear": {"type": "noul", "noul": self.clear},
                   "kind": {"type": "choice", "choice": self.kind, "confidence": 0.9},
                   "urgency": {"type": "score", "score": self.urgency, "confidence": 0.9},
                   "recommends": {"type": "noul", "noul": self.recommends}}
        if "meets" in questions:
            answers["meets"] = {"type": "choice", "choice": pick("meets", self.meets, "none"), "confidence": 0.9}
        if "covered" in questions:
            answers["covered"] = {"type": "choice", "choice": pick("covered", self.covered, "new"), "confidence": 0.9}
        return {"model": "jev-1.14", "usage": {"input_tokens": 400, "output_tokens": 0}, "ms": 120,
                "answers": answers}


@pytest.fixture
def gate(api):
    """Ben's checks On (budget 10, the default criteria), ops reporting to cpo, a fake model."""
    model = FakeModel()
    api.app.state.judge = model
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        A.save(c, "human:ben", "human:ben", {"mode": "on", "budget": 10, "chores_budget": 5,
                                             "criteria": A.DEFAULTS["criteria"]})
    return api, model, bot_token(api, "ops")


def plate(api, n, person="human:ben"):
    """Put n open items on the person's plate, filed by a person (not gated)."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            H.task_create(c, "human:ana", f"Load item {i} for {person}", "", person, lint=False)


def file(api, token, title="Pick the launch date", body="Two dates: Nov 3 or Nov 10. Which one?"):
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": title, "body": body}, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()


def items(api):
    with api.app.state.store.read() as c:
        return H._rows(c.execute("SELECT * FROM ask_ingest_items ORDER BY created_at"))


def calls(api):
    with api.app.state.store.read() as c:
        return H._rows(c.execute("SELECT * FROM events WHERE action='judge.call' AND target LIKE 'person-ask-ingest@%'"))


def write_state(api):
    """The domain rows a refused ask must leave intact, including delivery and refusal/quarantine state."""
    with api.app.state.store.read() as c:
        return {table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("tasks", "messages", "jobs", "task_holds", "history_events", "idempotency",
                              "refusals", "quarantine_holds", "org_rule_holds", "org_rule_misses")}


def assert_refused(api, response, before=None):
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "ask_refused" and not error["retryable"]
    if before is not None:
        assert write_state(api) == before, "refusal rolls back every domain row and counts toward no hold"
    [item] = items(api)[-1:]
    assert item["outcome"] in (A.SENT_BACK, A.FYI) and item["ref"] == "" and item["message_id"] is None
    return error["detail"]


# ---- the decision table, plain code ----------------------------------------------------------------------------

def answers(clear=True, kind="decision", meets="none", urgency=1, recommends=False, covered="new"):
    return {"clear": clear, "kind": kind, "meets": meets, "urgency": urgency, "recommends": recommends,
            "covered": covered}


@pytest.mark.parametrize("load,given,expected", [
    (4, answers(), (A.THROUGH, "under_budget")),                                           # criterion 1
    (15, answers(), (A.SENT_BACK, "criteria")),                                            # criterion 2
    (15, answers(meets="line-4"), (A.THROUGH, "criteria")),                                # criterion 3
    (40, answers(meets="line-4", recommends=True, urgency=1), (A.THROUGH, "recommends")),  # criterion 4: no urgency rule
    (40, answers(meets="line-4", recommends=True, urgency=2), (A.THROUGH, "recommends")),
    (40, answers(meets="line-4", recommends=False, urgency=3), (A.THROUGH, "urgent")),
    (40, answers(meets="line-4", recommends=False, urgency=1), (A.SENT_BACK, "recommends")),
    (40, answers(meets="none", recommends=True, urgency=2), (A.SENT_BACK, "recommends")),
    (120, answers(meets="line-4", recommends=True, urgency=2), (A.SENT_BACK, "urgent_only")),  # criterion 5
    (120, answers(urgency=3), (A.THROUGH, "urgent")),
    (0, answers(kind="status"), (A.FYI, "status")),                                         # criterion 7
    (50, answers(kind="chore"), (A.SENT_BACK, "recommends")),                                        # criterion 8
    (0, answers(kind="own_work"), (A.SENT_BACK, "own_work")),                               # criterion 9
    (0, answers(clear=False), (A.SENT_BACK, "unclear")),
    (0, answers(clear=False, urgency=3), (A.THROUGH, "urgent")),
    (0, answers(clear=None), (A.FAILED_OPEN, "below_threshold")),
    (15, answers(meets=None), (A.FAILED_OPEN, "below_threshold")),
    (40, answers(meets="line-1", recommends=None), (A.FAILED_OPEN, "below_threshold")),
    (40, answers(meets="line-1", recommends=True, urgency=None), (A.THROUGH, "recommends")),  # the band reads no urgency
])
def test_the_budget_tightens_as_the_plate_fills(load, given, expected):
    assert A.decide(given, 10, load) == expected


def test_a_recommends_reply_names_the_criterion_and_the_recommendation_only():
    text = A.message("recommends", "Ben", 40, "Priya")
    assert "Ben has 40 open items" in text and "criteria and your recommendation" in text
    assert "date" not in text and "past today" not in text, "no band asks for a date now"


def test_chores_use_the_main_budget():
    assert A.decide(answers(kind="chore"), 10, 9) == (A.THROUGH, "under_budget")
    assert A.decide(answers(kind="chore"), 10, 10) == (A.SENT_BACK, "criteria")
    assert A.decide(answers(kind="chore", meets="line-1"), 10, 10) == (A.THROUGH, "criteria")
    assert A.decide(answers(kind="chore", urgency=3), 10, 100) == (A.THROUGH, "urgent")


def test_answers_under_threshold_read_as_unsure():
    raw = {"clear": {"noul": 0.5}, "kind": {"choice": "decision", "confidence": 0.4},
           "urgency": {"score": 2.6, "confidence": 0.9}, "recommends": {"noul": 0.1},
           "meets": {"choice": "line-1", "confidence": 0.95}}
    got = A.read(raw, {"clear": 0.7, "kind": 0.7, "urgency": 0.6, "recommends": 0.7, "meets": 0.7},
                 {"line-1": "x"}, {})
    assert got == {"clear": None, "kind": None, "recommends": False, "urgency": 3, "meets": "line-1",
                   "covered": "new"}


# ---- through the write -----------------------------------------------------------------------------------------

def test_a_clear_task_under_budget_reaches_the_person(gate):
    api, model, token = gate
    plate(api, 4)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben"
    assert "ingest" not in out, "an item that goes through reads as before"
    assert [(i["outcome"], i["reason"]) for i in items(api)] == [(A.THROUGH, "under_budget")]
    assert len(model.calls) == 1 and model.calls[0]["label"].startswith("person-ask-ingest@")
    assert "Pick the launch date" == model.calls[0]["state"]["item"]["title"]


def test_over_budget_with_no_criterion_refuses_without_creating_anything(gate):
    api, model, token = gate
    plate(api, 15)
    before = write_state(api)
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"},
                 headers=headers(token))
    text = assert_refused(api, r, before)
    assert "15 open items" in text and "cpo" in text
    assert "ask again with the line of their criteria it meets" in text
    [item], [call] = items(api), calls(api)
    assert (item["outcome"], item["reason"], item["call_id"]) == (A.SENT_BACK, "criteria", call["id"])
    assert "Pick the launch date" not in call["detail"]


def test_meeting_a_criterion_gets_it_through_at_that_load(gate):
    api, model, token = gate
    plate(api, 15)
    model.meets = "$200 a month"
    out = file(api, token, "Raise the Gateway cap to $400 a month")
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    assert [(i["outcome"], i["reason"]) for i in items(api)] == [(A.THROUGH, "criteria")]


def test_a_status_line_is_refused_with_a_fix_to_post_an_update(gate):
    api, model, token = gate
    model.kind = "status"
    before = write_state(api)
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Merged; release at 17:00Z",
                                        "body": "Merged; release at 17:00Z."}, headers=headers(token))
    assert "Post it as an update instead." in assert_refused(api, r, before)
    assert [i["outcome"] for i in items(api)] == [A.FYI]


def test_a_stored_rehearsal_reads_and_acts_as_on(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE ask_ingest_settings SET mode='rehearsal' WHERE person='human:ben'")
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["mode"] == "on"
    model.kind = "own_work"
    before = write_state(api)
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Verify the preview cleanup and charges"},
                 headers=headers(token))
    assert "work you can do yourself" in assert_refused(api, r, before)
    [item] = items(api)
    assert (item["mode"], item["outcome"], item["would"]) == ("on", A.SENT_BACK, A.SENT_BACK)


def test_migration_0066_moves_a_stored_rehearsal_to_on_and_leaves_the_rest():
    """backend/migrations/0066_ask_filter_no_rehearsal.py: a person's or the team default's 'rehearsal' becomes 'on'; the
    other modes stay. A database without the table (a new one) is left alone."""
    migration = importlib.import_module("backend.migrations.0066_ask_filter_no_rehearsal")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ask_ingest_settings(person TEXT PRIMARY KEY, mode TEXT NOT NULL)")
    conn.executemany("INSERT INTO ask_ingest_settings VALUES(?,?)",
                     [("*", "rehearsal"), ("human:ben", "rehearsal"), ("human:ana", "off"), ("human:cara", "on")])
    migration.up(conn)
    assert dict(conn.execute("SELECT person, mode FROM ask_ingest_settings").fetchall()) == {
        "*": "on", "human:ben": "on", "human:ana": "off", "human:cara": "on"}
    fresh = sqlite3.connect(":memory:")
    migration.up(fresh)
    assert fresh.execute("SELECT name FROM sqlite_master").fetchall() == []


@pytest.mark.parametrize("how", ["error", "timeout", "cap", "busy"])
def test_it_fails_open_and_records_the_failure(gate, monkeypatch, how):
    api, model, token = gate
    plate(api, 120)
    if how == "error":
        model.fail = RuntimeError("provider down")
    elif how == "timeout":
        monkeypatch.setattr(A, "DEADLINE", 0.2)
        model.hold = threading.Event()
    elif how == "busy":
        monkeypatch.setattr(A, "CONCURRENCY", 1)
        assert A.calls_for(api.app.state.store).slots.acquire(blocking=False)     # the one slot is taken
    else:
        monkeypatch.setattr(A, "daily_cap", lambda settings, role: 0)
    out = file(api, token)
    if model.hold:
        model.hold.set()
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert item["outcome"] == A.FAILED_OPEN and item["ref"] == out["task"]["id"]
    if how == "cap":
        assert item["reason"] == "budget" and not model.calls
    elif how == "busy":
        # Refused at once: nothing was asked, and no judge.call row counts against the day.
        assert item["reason"] == "busy" and not model.calls and not calls(api)
    else:
        [call] = calls(api)
        assert ('"error": "%s"' % how) in call["detail"]


def test_a_full_pool_refuses_at_once_and_a_freed_slot_runs_again(gate, monkeypatch):
    api, model, token = gate
    monkeypatch.setattr(A, "CONCURRENCY", 1)
    model.hold = threading.Event()
    first = threading.Thread(target=file, args=(api, token), kwargs={"title": "First"})
    first.start()
    try:
        for _ in range(500):                  # until the first call holds the slot
            if model.calls:
                break
            time.sleep(0.01)
        started = time.monotonic()
        out = file(api, token, title="Second")
        assert time.monotonic() - started < 2
        assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    finally:
        model.hold.set()
        first.join(10)
    reasons = [i["reason"] for i in items(api)]
    assert len(reasons) == 2 and reasons.count("busy") == 1
    file(api, token, title="Third")           # the first call has ended, so its slot is free
    assert len(model.calls) == 2


def test_an_abandoned_call_keeps_its_slot_until_it_really_ends(gate, monkeypatch):
    api, model, token = gate
    monkeypatch.setattr(A, "CONCURRENCY", 1)
    monkeypatch.setattr(A, "DEADLINE", 0.2)
    model.hold = threading.Event()
    assert file(api, token, title="First")["task"]["owner_id"] == "human:ben"     # the waiter gives up; the call runs on
    started = time.monotonic()
    file(api, token, title="Second")          # so the slot is still taken
    assert time.monotonic() - started < 2 and len(model.calls) == 1
    model.hold.set()
    slots = A.calls_for(api.app.state.store).slots
    for _ in range(500):
        if slots.acquire(blocking=False):
            slots.release()
            break
        time.sleep(0.01)
    else:
        pytest.fail("the abandoned call never gave its slot back")
    file(api, token, title="Third")
    assert len(model.calls) == 2


def test_repeated_failures_pause_ask_ingest_calls(gate):
    api, model, token = gate
    model.fail = RuntimeError("provider down")
    for n in range(CS.BREAKER_FAILURES):
        file(api, token, title=f"Question {n}")
    assert len(model.calls) == CS.BREAKER_FAILURES
    started = time.monotonic()
    file(api, token, title="Next")
    assert time.monotonic() - started < 2 and len(model.calls) == CS.BREAKER_FAILURES
    assert items(api)[-1]["reason"] == "breaker"


def test_ask_ingest_and_rule_checks_have_their_own_slots(gate, monkeypatch):
    from backend import org_rule_check as K
    api, model, token = gate
    monkeypatch.setattr(A, "CONCURRENCY", 1)
    store = api.app.state.store
    assert A.calls_for(store).slots.acquire(blocking=False)        # ask ingest's one slot is taken
    assert K.calls_for(store).slots.acquire(blocking=False)        # a rule check still gets one
    K.calls_for(store).slots.release()


def test_a_persons_own_task_for_someone_is_not_gated_but_counts(gate):
    api, model, token = gate
    out = post(api, "tasks", {"owner": "ben", "title": "Read the contract", "body": "By Friday."})
    assert out["owner_id"] == "human:ben" and not model.calls
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 1


def test_no_decision_model_is_decide_off(gate):
    api, model, token = gate
    api.app.state.judge = None
    api.app.state.store.settings.rehearsal = True
    plate(api, 120)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out and not items(api) and not calls(api)


def test_mode_off_asks_nothing(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out and not model.calls


def test_a_settings_change_applies_to_the_next_item_only(gate):
    api, model, token = gate
    first = file(api, token)["task"]
    plate(api, 15)
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("ben-test"),
                json={"mode": "on", "budget": 5, "criteria": ["Pricing"]})
    assert r.status_code == 200, r.text
    before = write_state(api)
    refused = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Pick the launch venue"}, headers=headers(token))
    assert_refused(api, refused, before)
    with api.app.state.store.read() as c:
        assert H.task(c, first["id"])["owner_id"] == "human:ben"


def test_settings_are_a_persons_own_and_the_team_default_is_an_admins(gate):
    api, model, token = gate
    own = get(api, "ask-ingest/settings", token="priya-test")
    assert own["settings"]["mode"] == "on" and own["settings"]["budget"] == 10, "ships as On"
    assert own["modes"] == ["off", "on"]
    assert len(own["settings"]["criteria"]) == 6 and own["may_edit_team"] is False
    body = {"mode": "on", "budget": 8, "chores_budget": 3, "criteria": ["Pricing"], "team": True}
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("priya-test"), json=body)
    assert r.status_code == 403
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("ana-test"), json=body)
    assert r.status_code == 200, r.text
    assert get(api, "ask-ingest/settings", token="priya-test")["settings"]["budget"] == 8
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["budget"] == 10, "Ben's own stays"
    r = api.get("/api/v2/ask-ingest/settings", headers=headers(token))
    assert r.status_code == 403
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("ben-test"),
                json={"mode": "loud", "budget": 8, "chores_budget": 3, "criteria": []})
    assert r.status_code == 422 and "off or on" in r.text


def test_a_put_of_the_retired_rehearsal_is_saved_as_on(gate):
    """docs/api.md: a request that worked keeps working. A PUT with mode 'rehearsal' (before this release it worked) is
    saved as On and answered as On."""
    api, model, token = gate
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("ben-test"),
                json={"mode": "rehearsal", "budget": 12, "chores_budget": 4, "criteria": []})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["mode"] == "on" and r.json()["settings"]["budget"] == 12
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["mode"] == "on"


# ---- questions and waits ---------------------------------------------------------------------------------------

def own_task(api, token, title="Plan the November launch"):
    r = api.post("/api/v2/tasks", json={"owner": "ops", "title": title, "body": "Dates and venue."},
                 headers=headers(token))
    assert r.status_code == 200 and "ingest" not in r.json(), r.text
    return r.json()["task"]


def on_plate(api, tid, person="human:ben"):
    with api.app.state.store.read() as c:
        plate = H.needs_you(c, person)
    return tid in {t["id"] for t in plate["tasks"] + plate["waiting"]}


def test_a_refused_question_on_a_task_creates_no_comment(gate):
    api, model, token = gate
    plate(api, 15)
    tid = own_task(api, token)["id"]
    question = {"questions": [{"id": "date", "header": "Date", "question": "Which launch date do you want?",
                               "options": [{"label": "Nov 3", "then": "Launch on Nov 3"},
                                           {"label": "Nov 10", "then": "Launch on Nov 10"}]}], "who": "ben"}
    before = write_state(api)
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Which launch date do you want?", "ask": question},
                 headers=headers(token))
    assert_refused(api, r, before)
    assert not on_plate(api, tid)
    assert [(i["kind"], i["outcome"]) for i in items(api)] == [("question", A.SENT_BACK)]


def test_a_refused_task_question_creates_no_task_or_question(gate):
    api, model, token = gate
    plate(api, 15)
    before = write_state(api)
    r = ask_question(api, token)
    assert_refused(api, r, before)
    assert [(i["kind"], i["outcome"]) for i in items(api)] == [("task_question", A.SENT_BACK)]


def test_a_progress_line_as_a_wait_is_refused_without_changing_the_task(gate):
    api, model, token = gate
    model.kind = "status"
    task = own_task(api, token, "Ship the release")
    before = write_state(api)
    r = api.post(f"/api/v2/tasks/{task['id']}", headers=headers(token),
                 json={"version": task["version"], "status": "waiting", "waiting_on": "ben",
                       "note": "Merged; release at 17:00Z"})
    assert "Post it as an update instead." in assert_refused(api, r, before)
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["version"] == task["version"] and A.load(c, "human:ben") == 0
    assert model.calls[-1]["state"]["item"]["text"] == "Merged; release at 17:00Z"


def test_a_clear_wait_under_budget_is_set(gate):
    api, model, token = gate
    task = own_task(api, token, "Pick the venue")
    r = api.post(f"/api/v2/tasks/{task['id']}", headers=headers(token),
                 json={"version": task["version"], "status": "waiting", "waiting_on": "ben",
                       "note": "Venue A or B? I recommend A; I book it Friday if no answer."})
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and [i["outcome"] for i in items(api)] == [A.THROUGH]
    with api.app.state.store.read() as c:
        # A bot's wait on a person is one small subtask they own (hubdb._ask_in_subtask), on their plate.
        assert A.load(c, "human:ben") == 1


def test_a_refused_loose_question_creates_no_message(gate):
    api, model, token = gate
    plate(api, 15)
    before = write_state(api)
    assert_refused(api, ask_loose(api, token), before)
    with api.app.state.store.read() as c:
        assert H.loose_asks_to(c, "human:ben") == []
    assert [(i["kind"], i["outcome"]) for i in items(api)] == [("loose", A.SENT_BACK)]


def test_a_persons_question_is_not_gated(gate):
    api, model, token = gate
    r = api.post("/api/v2/messages", json={"to": "ben", "text": "Lunch at noon?", "kind": "ask"},
                 headers=headers("priya-test"))
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and not model.calls


# ---- Refusal audit --------------------------------------------------------------------------------------------









def test_the_decisions_readout_counts_refusals_and_reconciles_their_calls(gate):
    api, model, token = gate
    plate(api, 15)
    assert_refused(api, api.post("/api/v2/tasks", json={"owner": "ben", "title": "Pick the launch date"},
                                 headers=headers(token)))
    model.meets = "$200 a month"
    file(api, token, "Raise the Gateway cap to $400 a month")
    readout = get(api, "judge/readout")
    [row] = [r for r in readout["sets"] if r["set"] == A.QUESTION_SET]
    assert row["calls"] == 2
    assert row["gate"] == {"outcomes": {A.SENT_BACK: 1, A.THROUGH: 1}, "let_through": 0}
    assert row["reconcile"]["landed"] == 2 and row["reconcile"]["unexplained"] == 0


# ---- the same thing again: merged ------------------------------------------------------------------------------

def test_a_repeat_joins_the_open_item_and_the_answer_there_goes_back_to_the_bot(gate):
    api, model, token = gate
    first = file(api, token, "Pick the launch date for Acme", "Nov 3 or Nov 10?")["task"]
    model.covered = "launch date for Acme"
    second = file(api, token, "Launch date?", "Which day do we launch Acme: Nov 3 or Nov 10?")
    assert second["task"]["owner_id"] == "bot:ops", "the repeat stays with the bot"
    assert second["ingest"]["outcome"] == A.MERGED and "Pick the launch date for Acme" in second["ingest"]["text"]
    [_, merged] = items(api)
    assert (merged["merged_into"], merged["reason"]) == (first["id"], "covered")
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 1, "one item on the plate, not two"
    r = api.post(f"/api/v2/tasks/{first['id']}/comments", json={"text": "Nov 10."}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        passed = [m for m in H.task_comments(c, second["task"]["id"]) if "Nov 10." in (m["body"] or "")]
        assert len(passed) == 1 and H.task(c, second["task"]["id"])["owner_id"] == "bot:ops"
    api.post(f"/api/v2/tasks/{first['id']}/comments", json={"text": "Still Nov 10."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        assert not [m for m in H.task_comments(c, second["task"]["id"]) if "Still" in (m["body"] or "")], "once"


def test_a_repeat_loose_question_hears_the_answer_as_a_message(gate):
    api, model, token = gate
    first = file(api, token, "Pick the launch date for Acme", "Nov 3 or Nov 10?")["task"]
    model.covered = "launch date for Acme"
    r = api.post("/api/v2/messages", json={"to": "ben", "text": "Which day is the Acme launch?", "kind": "ask"},
                 headers=headers(token))
    assert r.json()["ingest"]["outcome"] == A.MERGED
    with api.app.state.store.read() as c:
        assert H.loose_asks_to(c, "human:ben") == []
    api.post(f"/api/v2/tasks/{first['id']}/comments", json={"text": "Nov 10."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        told = c.execute("SELECT body FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%answered this%Nov 10.%'").fetchall()
    assert len(told) == 1


# ---- source access: a repeat joins only an item its bot may read in full (synthetic fixtures only) -----------------

RESTRICTED = {"see": {"everyone": True}, "read": {"people": ["ana"], "teams": [], "bots": []},
              "write": {"everyone": True}}


def add_task(api, actor, title, owner, private):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, actor, title, "", owner, lint=False, private=private)


def restrict(api, slug):
    """The bot's activity is read by Ana alone: its see stays open, so Ops sees the task and cannot read it."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET access=? WHERE bot=?", (json.dumps(RESTRICTED), slug))


def test_a_repeat_from_an_unrelated_bot_never_sees_another_bots_private_item(gate):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Acme pricing secret", "human:ben", private=True)["id"]
    model.covered = "Acme pricing"
    second = file(api, token, "Acme pricing again?", "Is the Acme price still $40?")["task"]
    assert all("covered" not in call["questions"] for call in model.calls), "no covered option was offered"
    assert all("Acme pricing secret" not in json.dumps(call) for call in model.calls)
    assert "Acme pricing secret" not in json.dumps(second)
    assert second["owner_id"] == "human:ben", "judged as new, under budget: it reaches the person"
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Use $40."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        told = c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%Use $40.%'").fetchall()
        assert not told
        assert not [m for m in H.task_comments(c, second["id"]) if "Use $40." in (m["body"] or "")]


def test_a_company_task_whose_bot_hides_its_activity_from_the_asker_is_not_offered(gate):
    api, model, token = gate
    add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)
    restrict(api, "cmo")
    model.covered = "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")["task"]
    assert all("covered" not in call["questions"] for call in model.calls)
    assert "Vendor escrow terms" not in json.dumps(model.calls)
    assert second["owner_id"] == "human:ben" and [i["merged_into"] for i in items(api)] == [None]


@pytest.mark.parametrize("how", ["private", "access", "plate"])
def test_an_item_lost_between_judgment_and_write_names_no_source(gate, how):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)["id"]

    def lose_access():
        if how == "private":
            with api.app.state.store.transaction() as c:
                c.execute("UPDATE tasks SET private=1 WHERE id=?", (first,))
        elif how == "plate":            # still open and readable, but no longer the person's: handed to Ana
            with api.app.state.store.transaction() as c:
                c.execute("UPDATE tasks SET owner_id='human:ana' WHERE id=?", (first,))
        else:
            restrict(api, "cmo")
    model.before, model.covered = lose_access, "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")
    assert model.calls, "the repeat was judged"
    with api.app.state.store.read() as c:
        assert H.task(c, first)["status"] in H.ACTIVE_STATUSES, "the source stays open: only the lost right fails"
    assert "Vendor escrow terms" not in json.dumps(second), "no title in the write's reply"
    assert second["task"]["owner_id"] == "human:ben", "the fallback is the ordinary fail-open path"
    [row] = [i for i in items(api) if i["ref"] == second["task"]["id"]]
    # covered_gone can only follow a merge the model picked: the offered source was the one it chose
    assert (row["outcome"], row["reason"], row["merged_into"]) == (A.FAILED_OPEN, "covered_gone", None)


@pytest.mark.parametrize("how", ["private", "access"])
def test_a_reply_is_not_forwarded_once_the_asker_lost_the_item(gate, how):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)["id"]
    model.covered = "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")["task"]
    assert second["owner_id"] == "bot:ops", "merged: the asker keeps its own task"
    if how == "private":
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET private=1 WHERE id=?", (first,))
    else:
        restrict(api, "cmo")
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Take the 30-day term."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        told = c.execute("SELECT 1 FROM messages WHERE recipient_id='bot:ops' AND body LIKE '%30-day term%'").fetchall()
        assert not told
        assert not [m for m in H.task_comments(c, second["id"]) if "30-day" in (m["body"] or "")]
        assert c.execute("SELECT passed_at FROM ask_ingest_items WHERE merged_into=?", (first,)).fetchone()[0] is None


def test_a_readable_repeat_merges_and_its_answer_is_delivered_once(gate):
    api, model, token = gate
    first = add_task(api, "bot:cmo", "Vendor escrow terms", "human:ben", private=False)["id"]
    model.covered = "Vendor escrow terms"
    second = file(api, token, "Escrow terms for the vendor?", "Which escrow term do we take?")
    assert second["ingest"]["outcome"] == A.MERGED
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Take the 30-day term."}, headers=headers("ben-test"))
    api.post(f"/api/v2/tasks/{first}/comments", json={"text": "Still the 30-day term."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        told = [m for m in H.task_comments(c, second["task"]["id"]) if "30-day term" in (m["body"] or "")]
    assert len(told) == 1 and "Vendor escrow terms" in told[0]["body"]


# ---- Shared ask budget and the way back to the team default ---------------------------------------------------------

def test_a_chore_lands_as_an_ordinary_need_and_counts_under_the_main_budget(gate):
    api, model, token = gate
    plate(api, 9)
    model.kind = "chore"
    tid = file(api, token, "Grant ops read access to the billing repo", "One click in GitHub settings.")["task"]["id"]
    assert on_plate(api, tid)
    cards = {it["id"]: it for it in get(api, "needs-you?limit=200", token="ben-test")["items"]}
    assert cards[tid]["need"] != "hands"
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 10
    before = write_state(api)
    assert_refused(api, api.post("/api/v2/tasks", json={"owner": "ben", "title": "Grant docs access"},
                                 headers=headers(token)), before)


def test_a_person_goes_back_to_the_team_default(gate):
    api, model, token = gate
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["own"] is True
    r = api.delete("/api/v2/ask-ingest/settings", headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    assert r.json()["settings"] == {**get(api, "ask-ingest/settings", token="ben-test")["team"], "own": False}
    plate(api, 15)
    assert_refused(api, api.post("/api/v2/tasks", json={"owner": "ben", "title": "Pick the launch date"},
                                 headers=headers(token)))
    assert api.delete("/api/v2/ask-ingest/settings", headers=headers(token)).status_code == 403


# ---- the replay before On --------------------------------------------------------------------------------------

def test_a_replay_judges_a_past_day_as_on_and_moves_nothing(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    plate(api, 15)
    tid = file(api, token)["task"]["id"]         # Off: both items land as filed
    api.post("/api/v2/messages", json={"to": "ben", "text": "Which venue do you want?", "kind": "ask"}, headers=headers(token))
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "on", "budget": 10, "chores_budget": 5, "criteria": []})
    before, calls_before = (on_plate(api, tid), len(items(api))), len(calls(api))
    day = H.now()[:10]
    r = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day, "limit": 1}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["total"] == 2 and first["next_offset"] == 1
    [one] = first["items"]
    assert (one["kind"], one["ref"], one["would"], one["reason"]) == ("task", tid, A.SENT_BACK, "criteria")
    assert one["load"] >= 15
    rest = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day, "offset": 1},
                    headers=headers("ben-test")).json()
    assert [i["kind"] for i in rest["items"]] == ["loose"] and rest["next_offset"] is None
    assert (on_plate(api, tid), len(items(api))) == before, "nothing on the plate moved, no item rows"
    replayed = calls(api)[calls_before:]
    assert len(replayed) == 2 and all('"mode": "replay"' in str(row) for row in replayed)
    assert all("Which venue" not in str(row) and "Pick the launch date" not in str(row) for row in replayed)


def test_a_replay_is_the_persons_own_or_an_admins(gate):
    api, model, token = gate
    day = H.now()[:10]
    assert api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day},
                    headers=headers("priya-test")).status_code == 403
    assert api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day},
                    headers=headers(token)).status_code == 403
    api.app.state.judge = None
    r = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("ben-test"))
    assert r.status_code == 409, r.text


# ---- A bot's handoff to a person is gated --------------------



def test_a_bot_handing_its_task_to_a_person_is_refused_atomically(gate):
    api, model, token = gate
    plate(api, 15)
    task = own_task(api, token, "Pick the launch date")
    before = write_state(api)
    r = api.post(f"/api/v2/tasks/{task['id']}", json={"version": task["version"], "owner": "ben",
                                                    "title": "Changed title"}, headers=headers(token))
    assert_refused(api, r, before)
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["version"] == task["version"]


def test_a_bots_bulk_handoff_to_a_person_records_each_refusal_without_quarantine(gate):
    api, model, token = gate
    plate(api, 15)
    ids = [own_task(api, token, f"Pick launch option {n}")["id"] for n in range(3)]
    r = api.post("/api/v2/task-bulk", json={"filter": {"ids": ids}, "action": "owner", "owner": "ben"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    assert (r.json()["changed"], r.json()["skipped"]) == (0, 3)
    with api.app.state.store.read() as c:
        assert {H.task(c, i)["owner_id"] for i in ids} == {"bot:ops"}
        assert not c.execute("SELECT 1 FROM refusals").fetchone()
        assert not c.execute("SELECT 1 FROM quarantine_holds").fetchone()
    assert [(i["kind"], i["outcome"], i["ref"]) for i in items(api)] == [("task", A.SENT_BACK, "")] * 3
    row = next(r for r in get(api, "judge/readout")["sets"] if r["set"] == A.QUESTION_SET)
    assert row["reconcile"]["landed"] == 3 and row["reconcile"]["unexplained"] == 0


# ---- Review 2: every way a bot asks a person is judged, against the plate before the item ----------------------

def for_ben(api, title="Plan the November launch"):
    """A task Ben asked ops for: ops owns it, Ben is its requester (filed by a person, not gated)."""
    with api.app.state.store.transaction() as c:
        return H.task_create(c, "human:ben", title, "Dates and venue.", "bot:ops", lint=False)


def test_a_bots_task_ask_over_budget_is_refused_without_a_question_or_hold(gate):
    api, model, token = gate
    plate(api, 15)
    task = for_ben(api)
    before = write_state(api)
    r = api.post(f"/api/v2/tasks/{task['id']}/ask", json={"text": "Which launch date do you want?"},
                 headers=headers(token))
    assert "15 open items" in assert_refused(api, r, before)
    assert not on_plate(api, task["id"])
    assert [(i["kind"], i["outcome"]) for i in items(api)] == [("task_ask", A.SENT_BACK)]


def test_a_refused_task_ask_keeps_the_bots_note_without_posting_a_comment(gate):
    api, model, token = gate
    plate(api, 15)
    task = for_ben(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", task["id"], status="doing", note="Drafting the venue shortlist")
    before = write_state(api)
    r = api.post(f"/api/v2/tasks/{task['id']}/ask", json={"text": "Which launch date do you want?"},
                 headers=headers(token))
    assert_refused(api, r, before)
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        assert (row["status"], row["note"]) == ("doing", "Drafting the venue shortlist")


def test_a_persons_task_ask_is_not_gated(gate):
    api, model, token = gate
    plate(api, 15)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ben", "Plan the offsite", "", "human:priya", lint=False)
    r = api.post(f"/api/v2/tasks/{task['id']}/ask", json={"text": "Which week?"}, headers=headers("priya-test"))
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and items(api) == [] and model.calls == []
    assert on_plate(api, task["id"])


def test_a_raw_ask_message_naming_a_task_is_refused_before_sending(gate):
    api, model, token = gate
    plate(api, 15)
    tid = own_task(api, token)["id"]
    before = write_state(api)
    r = api.post("/api/v2/messages", headers=headers(token),
                 json={"to": "ben", "text": "Which launch date do you want?", "kind": "ask", "refs": {"task": tid}})
    assert_refused(api, r, before)
    assert model.calls[-1]["state"]["item"]["title"] == "Plan the November launch"
    assert [(i["kind"], i["message_id"]) for i in items(api)] == [("question", None)]


def test_a_raw_ask_naming_a_task_the_bot_cannot_read_never_shows_its_title(gate):
    api, model, token = gate
    model.meets = "Strategy"
    plate(api, 15)
    with api.app.state.store.transaction() as c:
        hidden = H.task_create(c, "human:ben", "Acme acquisition terms", "", "human:priya", lint=False, private=True)
    r = api.post("/api/v2/messages", headers=headers(token),
                 json={"to": "ben", "text": "Which launch date do you want?", "kind": "ask",
                       "refs": {"task": hidden["id"]}})
    assert r.status_code in (403, 404), r.text
    assert model.calls and "Acme" not in str([call["state"] for call in model.calls])
    assert all(call["state"]["item"]["title"] == "Which launch date do you want?" for call in model.calls)


def test_a_question_sent_as_a_reply_is_judged(gate):
    api, model, token = gate
    first = api.post("/api/v2/messages", json={"to": "ben", "text": "Which venue do you want, A or B?", "kind": "ask"},
                     headers=headers(token))
    assert first.status_code == 200 and "ingest" not in first.json(), first.text      # under budget: through
    answer = api.post("/api/v2/messages", json={"to": "ops", "text": "Venue A.", "in_reply_to": first.json()["id"]},
                      headers=headers("ben-test"))
    assert answer.status_code == 200, answer.text
    with api.app.state.store.transaction() as c:      # the bot's next turn is on Ben's reply, in that thread
        c.execute("INSERT OR IGNORE INTO attempt_conversations(attempt_id,conversation_id) SELECT id, ? FROM attempts",
                  (answer.json()["conversation_id"],))
    plate(api, 15)
    r = api.post("/api/v2/messages", headers=headers(token),
                 json={"to": "ben", "text": "Thanks. Which launch date do you want?", "kind": "ask",
                       "in_reply_to": answer.json()["id"]})
    assert_refused(api, r)
    with api.app.state.store.read() as c:
        assert H.loose_asks_to(c, "human:ben") == []


def ask_loose(api, token):
    return api.post("/api/v2/messages", json={"to": "ben", "text": "Which launch date do you want?", "kind": "ask"},
                    headers=headers(token))


def ask_comment(api, token):
    tid = own_task(api, token)["id"]
    question = {"questions": [{"id": "date", "header": "Date", "question": "Which launch date do you want?",
                               "options": [{"label": "Nov 3", "then": "Launch on Nov 3"},
                                           {"label": "Nov 10", "then": "Launch on Nov 10"}]}], "who": "ben"}
    return api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Which launch date do you want?", "ask": question},
                    headers=headers(token))


def ask_question(api, token):
    return api.post("/api/v2/task-questions", json={"on": "human:ben", "question": "Which launch date do you want?"},
                    headers=headers(token))


def ask_task(api, token):
    return api.post(f"/api/v2/tasks/{for_ben(api)['id']}/ask", json={"text": "Which launch date do you want?"},
                    headers=headers(token))


@pytest.mark.parametrize("ask", [ask_loose, ask_comment, ask_question, ask_task])
@pytest.mark.parametrize("load,meets,expected", [
    (9, None, (A.THROUGH, "under_budget")),       # B-1: under budget, the question itself not counted
    (10, None, (A.SENT_BACK, "criteria")),
    (29, "Strategy", (A.THROUGH, "criteria")),    # 3B-1
    (99, None, (A.SENT_BACK, "recommends")),      # 10B-1
])
def test_a_question_is_judged_against_the_plate_before_it(gate, ask, load, meets, expected):
    api, model, token = gate
    model.meets = meets
    plate(api, load)
    r = ask(api, token)
    if expected[0] == A.SENT_BACK:
        assert_refused(api, r)
    else:
        assert r.status_code == 200, r.text
    [item] = [i for i in items(api) if i["asker"] == "bot:ops"]
    assert (item["outcome"], item["reason"], item["load"]) == (*expected, load)
    if expected[0] == A.SENT_BACK:
        assert f"{load} open items" in r.json()["error"]["detail"]


def test_a_question_on_a_task_already_on_the_plate_counts_it_once(gate):
    api, model, token = gate
    plate(api, 8)
    task = file(api, token)["task"]               # ops's task for Ben: on his plate, the ninth item
    assert task["owner_id"] == "human:ben"
    question = {"questions": [{"id": "date", "header": "Date", "question": "Nov 3 or Nov 10?",
                               "options": [{"label": "Nov 3", "then": "Launch on Nov 3"},
                                           {"label": "Nov 10", "then": "Launch on Nov 10"}]}], "who": "ben"}
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": "Nov 3 or Nov 10?", "ask": question},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    assert [(i["kind"], i["outcome"], i["load"]) for i in items(api)][-1] == ("question", A.THROUGH, 9)


def test_a_refused_wait_rolls_back_other_fields_in_the_same_request(gate):
    api, model, token = gate
    plate(api, 15)
    task = own_task(api, token, "Pick the venue")
    before = write_state(api)
    r = api.post(f"/api/v2/tasks/{task['id']}", headers=headers(token),
                 json={"version": task["version"], "owner": "ops", "status": "waiting", "waiting_on": "ben",
                       "title": "New title", "note": "Venue A or B?"})
    assert_refused(api, r, before)
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["version"] == task["version"]
    assert [(i["kind"], i["outcome"]) for i in items(api)] == [("wait", A.SENT_BACK)]


def test_a_chore_wait_uses_the_main_budget_and_ignores_the_old_chores_budget(gate):
    api, model, token = gate
    model.kind = "chore"
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "on", "budget": 10, "criteria": []})
        c.execute("UPDATE ask_ingest_settings SET chores_budget=1 WHERE person='human:ben'")
    for title in ("Grant ops access", "Grant ops access to the docs"):
        task = own_task(api, token, title)
        r = api.post(f"/api/v2/tasks/{task['id']}", headers=headers(token),
                     json={"version": task["version"], "status": "waiting", "waiting_on": "ben",
                           "note": "Grant ops read access to the repo"})
        assert r.status_code == 200, r.text
    assert [i["outcome"] for i in items(api)] == [A.THROUGH, A.THROUGH]
    cards = {it["id"]: it for it in get(api, "needs-you?limit=200", token="ben-test")["items"]}
    assert all(cards[i["ref"]]["need"] != "hands" for i in items(api))
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 2


def test_a_loose_chore_counts_as_an_ordinary_ask(gate):
    api, model, token = gate
    model.kind = "chore"
    r = api.post("/api/v2/messages", json={"to": "ben", "text": "Please click Approve on the GitHub invite.",
                                           "kind": "ask"}, headers=headers(token))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == 1
    cards = {it["id"]: it for it in get(api, "needs-you?limit=200", token="ben-test")["items"]}
    assert cards[r.json()["id"]]["need"] != "hands"


def test_a_bots_bulk_handoff_tells_it_the_refusal_and_fix_for_each_task(gate):
    api, model, token = gate
    plate(api, 15)
    ids = [own_task(api, token, f"Pick launch option {n}")["id"] for n in range(3)]
    r = api.post("/api/v2/task-bulk", json={"filter": {"ids": ids}, "action": "owner", "owner": "ben"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    lines = {line["id"]: line for line in r.json()["tasks"]["skipped"]}
    assert set(lines) == set(ids)
    for line in lines.values():
        assert line["code"] == "ask_refused"
        assert "cpo" in line["reason"] and "ask again" in line["reason"]


# ---- Review 2: the replay sees the plate just before each item, and an admin only what they may read ----------

def test_a_replay_never_offers_an_item_as_its_own_repeat(gate):
    api, model, token = gate
    plate(api, 9)
    model.covered = "launch"
    first = file(api, token)["task"]["id"]
    second = file(api, token, "Choose the Acme launch day", "Nov 3 or Nov 10?")["task"]["id"]    # a true repeat
    live = {i["ref"]: i for i in items(api)}
    assert (live[first]["would"], live[first]["load"]) == (A.THROUGH, 9)
    r = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": H.now()[:10]}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    got = {i["ref"]: i for i in r.json()["items"]}
    assert (got[first]["would"], got[first]["load"]) == (A.THROUGH, 9), "agrees with the gate at B-1"
    assert got[second]["would"] == A.MERGED
    offered = [call["questions"].get("covered", {}).get("criteria", {}) for call in model.calls[-2:]]
    assert not [t for t in offered[0].values() if "launch" in t]
    assert "Pick the launch date" in offered[1].values()


def test_a_refused_question_on_a_task_already_on_the_plate_counts_that_task(gate):
    api, model, token = gate
    plate(api, 9)
    task = file(api, token)["task"]
    question = {"questions": [{"id": "date", "header": "Date", "question": "Nov 3 or Nov 10?",
                               "options": [{"label": "Nov 3", "then": "Launch on Nov 3"},
                                           {"label": "Nov 10", "then": "Launch on Nov 10"}]}], "who": "ben"}
    before = write_state(api)
    r = api.post(f"/api/v2/tasks/{task['id']}/comments", json={"text": "Nov 3 or Nov 10?", "ask": question},
                 headers=headers(token))
    assert_refused(api, r, before)
    live = [i for i in items(api) if i["kind"] == "question"][-1]
    assert live["load"] == 10
    r = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": H.now()[:10]}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    assert not [i for i in r.json()["items"] if i["kind"] == "question"], "no item text survives a refusal to replay"


def test_a_replay_shows_an_admin_only_the_titles_they_may_read(gate, monkeypatch):
    api, model, token = gate
    auth = api.app.state.auth
    monkeypatch.setattr(auth, "bot_admins", {*auth.bot_admins, "priya@acme.example"})    # an admin, not a party
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Ben's private salary review", "body": "x",
                                        "private": True}, headers=headers(token))
    assert r.status_code == 200, r.text
    file(api, token, "Pick the launch date")
    api.post("/api/v2/messages", json={"to": "ben", "text": "Which doctor do you see?", "kind": "ask"},
             headers=headers(token))
    day = H.now()[:10]
    admin = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("priya-test"))
    assert admin.status_code == 200, admin.text
    shown = sorted(i["title"] for i in admin.json()["items"])
    assert shown == ["", "", "Pick the launch date"]
    assert "salary" not in admin.text and "doctor" not in admin.text
    own = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("ben-test"))
    assert {"Ben's private salary review", "Which doctor do you see?"} <= {i["title"] for i in own.json()["items"]}


def test_a_replay_hides_a_private_task_the_filter_kept_with_its_bot(gate):
    """A private task sent back stays the bot's: the person is no party to it, so their own replay shows no title. Their
    own task, and a question to them outside any task, they always see."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, "bot:ops", "Bot-only vendor margin", "", "bot:ops", lint=False, private=True)
        mine = H.task_create(c, "bot:ops", "Pick the launch date", "", "human:ben", lint=False, private=True)
        assert not A._shown(c, None, "human:ben", {"kind": "task", "ref": kept["id"]})
        assert not A._shown(c, "human:ben", "human:ben", {"kind": "task", "ref": kept["id"]})
        assert A._shown(c, None, "human:ben", {"kind": "task", "ref": mine["id"]})
        assert A._shown(c, None, "human:ben", {"kind": "loose", "ref": "m1"})
        assert not A._shown(c, "human:priya", "human:ben", {"kind": "loose", "ref": "m1"})


def test_a_replay_shows_a_bot_task_only_where_the_viewer_may_read_it(gate):
    """A company-visible task the filter kept with its bot shows its title only where the full task-read rule allows it
    (Read on the bot): privacy alone is not enough."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, "bot:ops", "Vendor margin review", "", "bot:ops", lint=False)
        it = {"kind": "task", "ref": kept["id"]}
        assert A._shown(c, "human:priya", "human:ben", it)                       # privacy alone: shown
        assert not A._shown(c, "human:priya", "human:ben", it, lambda c, task: False)
        assert A._shown(c, "human:priya", "human:ben", it, lambda c, task: True)


@pytest.mark.parametrize("owner_event_at", ["2026-10-06T09:00:00", "2026-10-06T09:00:00.200"])
def test_a_handoff_replays_on_the_day_it_was_put_on_them(gate, owner_event_at):
    """A task the filter acted on counts on the day its ask row was written, not the day the bot created the task."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        kept = H.task_create(c, "bot:ops", "Renew the vendor contract", "", "bot:ops", lint=False)
        c.execute("UPDATE tasks SET created_at='2026-10-05T10:00:00' WHERE id=?", (kept["id"],))
        c.execute("INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
                  "VALUES('ai1','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,'2026-10-06T09:00:00')",
                  (kept["id"],))
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    assert kept["id"] not in [i["ref"] for i in monday]
    assert [(i["ref"], i["created_at"]) for i in tuesday if i["kind"] == "task"] == [(kept["id"], "2026-10-06T09:00:00")]

    with api.app.state.store.transaction() as c:
        # Let it through makes it theirs: it still counts on the day it was put on them, under the bot that asked.
        # The handoff is an owner change, recorded in the history as the hub writes it, then the task's owner.
        H.history_event(c, "task", kept["id"], "bot:ops", "owner", "bot:ops", "human:ben",
                        created_at=owner_event_at)
        c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (kept["id"],))
        human = H.task_create(c, "human:priya", "Priya's task the bot handed on", "", "bot:ops", lint=False)
        c.execute("UPDATE tasks SET created_at='2026-10-05T11:00:00' WHERE id=?", (human["id"],))
        c.execute("INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
                  "VALUES('ai2','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,'2026-10-06T09:30:00')",
                  (human["id"],))
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    assert not [i for i in monday if i["kind"] == "task"]
    assert [(i["ref"], i["asker"]) for i in tuesday if i["kind"] == "task"] == [(kept["id"], "bot:ops"), (human["id"], "bot:ops")]


def test_a_bot_handoff_while_the_filter_is_off_replays_but_a_human_handoff_does_not(gate):
    """Owner history supplies an Off handoff with the same task fields as an ask-row handoff, but only for bot actors."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        bot_task = H.task_create(c, "bot:ops", "Renew the vendor contract", "Check the renewal date", "bot:ops",
                                 lint=False)
        human_task = H.task_create(c, "human:priya", "Review the launch plan", "Confirm the milestones",
                                   "human:priya", lint=False)
        for task, actor in ((bot_task, "bot:ops"), (human_task, "human:priya")):
            c.execute("UPDATE tasks SET created_at='2026-10-05T10:00:00' WHERE id=?", (task["id"],))
            H.history_event(c, "task", task["id"], actor, "owner", actor, "human:ben",
                            created_at="2026-10-06T09:00:00")
            c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (task["id"],))
        listed = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")

    tasks = [item for item in listed if item["kind"] == "task"]
    assert tasks == [{"kind": "task", "ref": bot_task["id"], "asker": "bot:ops",
                      "title": "Renew the vendor contract", "text": "Check the renewal date",
                      "asked": None, "created_at": "2026-10-06T09:00:00"}]




def test_a_task_filed_while_the_filter_was_off_counts_on_its_day_and_again_on_a_later_handoff(gate):
    """Filed for Ben on Oct 5 with the filter Off (no ask row), moved to the bot that evening, and handed back to Ben on
    Oct 6 with the filter On (an ask row). The filing still counts on Oct 5 and the handoff on Oct 6: the handoff does
    not hide the filing."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    tid = file(api, token, "Renew the vendor contract")["task"]["id"]
    assert not [i for i in items(api) if i["ref"] == tid], "Off asks nothing, so the filing has no ask row"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at='2026-10-05T10:00:00.000000Z' WHERE id=?", (tid,))
        H.history_event(c, "task", tid, "human:ben", "owner", "human:ben", "bot:ops",
                        created_at="2026-10-05T18:00:00.000000Z")
        c.execute("UPDATE tasks SET owner_id='bot:ops' WHERE id=?", (tid,))
        H.history_event(c, "task", tid, "bot:ops", "owner", "bot:ops", "human:ben",
                        created_at="2026-10-06T09:00:00.000000Z")
        c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (tid,))
        c.execute("INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
                  "VALUES('ai3','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,"
                  "'2026-10-06T09:00:00.000000Z')", (tid,))
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    shown = [(i["ref"], i["created_at"]) for i in monday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-05T10:00:00.000000Z")]
    shown = [(i["ref"], i["created_at"]) for i in tuesday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-06T09:00:00.000000Z")]


def test_a_task_filed_with_the_filter_on_is_listed_once(gate):
    """The gate judges a task in the request that files it, so its ask row comes before any owner change: its day lists
    it once, from the ask row, not again from the task."""
    api, model, token = gate
    tid = file(api, token)["task"]["id"]
    assert len([i for i in items(api) if i["ref"] == tid]) == 1
    with api.app.state.store.transaction() as c:
        day = H.task(c, tid)["created_at"][:10]
        listed = A.replay_items(c, "human:ben", f"{day}T00:00:00", A._next_day(day))
    assert [i["ref"] for i in listed if i["kind"] == "task"] == [tid]


def test_a_live_on_handoff_stays_single_when_its_history_event_is_later_than_the_ask(gate):
    """The history link deduplicates a real On handoff without relying on their timestamps staying within one second."""
    api, model, token = gate
    task = own_task(api, token, "Renew the vendor contract")
    r = api.post(f"/api/v2/tasks/{task['id']}", json={"version": task["version"], "owner": "ben"},
                 headers=headers(token))
    assert r.status_code == 200 and r.json()["task"]["owner_id"] == "human:ben", r.text
    [item] = [row for row in items(api) if row["ref"] == task["id"]]
    assert item["outcome"] == A.THROUGH
    with api.app.state.store.transaction() as c:
        event = c.execute("SELECT id, data FROM history_events WHERE subject_type='task' AND subject_id=? "
                          "AND kind='owner' AND actor_id='bot:ops' AND new='human:ben' ORDER BY rowid DESC LIMIT 1",
                          (task["id"],)).fetchone()
        assert event and json.loads(event["data"])["ask_ingest_item"] == item["id"]
        ask_at, event_at = "2026-10-06T09:00:00.000000Z", "2026-10-06T09:00:02.000000Z"
        c.execute("UPDATE ask_ingest_items SET created_at=? WHERE id=?", (ask_at, item["id"]))
        c.execute("UPDATE history_events SET created_at=? WHERE id=?", (event_at, event["id"]))
        day = ask_at[:10]
        listed = A.replay_items(c, "human:ben", f"{day}T00:00:00", A._next_day(day))

    assert [(i["ref"], i["created_at"]) for i in listed if i["kind"] == "task"] == [(task["id"], ask_at)]


def test_a_filing_the_gate_judged_and_a_later_handoff_each_list_once(gate):
    """Filed for Ben on Oct 5 with the filter On (its ask row at creation), moved to the bot that evening, and handed
    back on Oct 6 with a new ask row. Oct 5 lists the filing once, from its ask row; Oct 6 lists the handoff once."""
    api, model, token = gate
    tid = file(api, token)["task"]["id"]
    [filing] = [i for i in items(api) if i["ref"] == tid]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at='2026-10-05T10:00:00.000000Z' WHERE id=?", (tid,))
        c.execute("UPDATE ask_ingest_items SET created_at='2026-10-05T10:00:00.200000Z' WHERE id=?", (filing["id"],))
        H.history_event(c, "task", tid, "human:ben", "owner", "human:ben", "bot:ops",
                        created_at="2026-10-05T18:00:00.000000Z")
        c.execute("UPDATE tasks SET owner_id='bot:ops' WHERE id=?", (tid,))
        H.history_event(c, "task", tid, "bot:ops", "owner", "bot:ops", "human:ben",
                        created_at="2026-10-06T09:00:00.000000Z")
        c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (tid,))
        c.execute("INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
                  "VALUES('ai-handoff','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,"
                  "'2026-10-06T09:00:00.000000Z')", (tid,))
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    shown = [(i["ref"], i["created_at"]) for i in monday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-05T10:00:00.200000Z")]
    shown = [(i["ref"], i["created_at"]) for i in tuesday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-06T09:00:00.000000Z")]


def test_a_handoff_just_after_midnight_is_not_the_filing_from_just_before(gate):
    """Filed for Ben at 23:59:40 on Oct 5 with the filter Off (no ask row), moved to the bot at 23:59:50, and handed
    back on Oct 6 at 00:00:10 with its ask row. Thirty seconds apart across midnight is two placements, not one: Oct 5
    lists the filing, Oct 6 the handoff."""
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    tid = file(api, token, "Renew the vendor contract")["task"]["id"]
    assert not [i for i in items(api) if i["ref"] == tid], "Off asks nothing, so the filing has no ask row"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created_at='2026-10-05T23:59:40.000000Z' WHERE id=?", (tid,))
        H.history_event(c, "task", tid, "human:ben", "owner", "human:ben", "bot:ops",
                        created_at="2026-10-05T23:59:50.000000Z")
        c.execute("UPDATE tasks SET owner_id='bot:ops' WHERE id=?", (tid,))
        H.history_event(c, "task", tid, "bot:ops", "owner", "bot:ops", "human:ben",
                        created_at="2026-10-06T00:00:10.000000Z")
        c.execute("UPDATE tasks SET owner_id='human:ben' WHERE id=?", (tid,))
        c.execute("INSERT INTO ask_ingest_items(id,person,asker,kind,ref,mode,outcome,would,reason,load,created_at) "
                  "VALUES('ai-midnight','human:ben','bot:ops','task',?,'on','sent_back','sent_back','',0,"
                  "'2026-10-06T00:00:10.000000Z')", (tid,))
        monday = A.replay_items(c, "human:ben", "2026-10-05T00:00:00", "2026-10-06T00:00:00")
        tuesday = A.replay_items(c, "human:ben", "2026-10-06T00:00:00", "2026-10-07T00:00:00")
    shown = [(i["ref"], i["created_at"]) for i in monday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-05T23:59:40.000000Z")]
    shown = [(i["ref"], i["created_at"]) for i in tuesday if i["kind"] == "task"]
    assert shown == [(tid, "2026-10-06T00:00:10.000000Z")]


@pytest.mark.parametrize("actor,role,via,expected", [
    ("bot:ops", "bot", "botops", True),
    ("bot:ops", "bot", "assistant", False),
    ("human:ben", "human", "", False),
    ("human:ben", "human", "botops", False),
    ("human:ben", "human", "assistant", False),
    ("bot:assistant", "bot", "", False),
    ("bot:assistant", "bot", "botops", False),
])
def test_gate_preserves_person_and_assistant_exclusions(actor, role, via, expected):
    from backend.auth import Identity
    assert A.gated(Identity(actor, role, via=via), "assistant") is expected


@pytest.mark.parametrize("where,reason", [("load_set", "not_found"), ("open_items", "error"),
                                        ("server_engine", "unavailable")])
def test_preflight_exception_keeps_failed_open_record(gate, monkeypatch, where, reason):
    from clients.judge import JudgeError
    api, model, token = gate
    def fail(*args, **kwargs):
        if reason == "error":
            raise RuntimeError("private item text must not be recorded")
        raise JudgeError(reason, "private item text must not be recorded")
    monkeypatch.setattr(A.J if where == "load_set" else A, where, fail)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert (item["outcome"], item["would"], item["reason"]) == (A.FAILED_OPEN, A.FAILED_OPEN, reason)
    assert item["ref"] == out["task"]["id"] and item["call_id"] is None
    assert not calls(api) and not model.calls
    assert "private item text" not in str(item)


def test_exception_after_judging_keeps_call_link_and_fails_open(gate, monkeypatch):
    api, model, token = gate
    original = A._criteria
    seen = []
    def criteria(own):
        seen.append(True)
        if len(seen) == 2:
            raise RuntimeError("could not assemble verdict")
        return original(own)
    monkeypatch.setattr(A, "_criteria", criteria)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item], [call] = items(api), calls(api)
    assert (item["outcome"], item["reason"], item["call_id"]) == (A.FAILED_OPEN, "error", call["id"])
    assert len(model.calls) == 1


# ---- every judged call ends in a row or a named reason (Dev 702) ------------------------------------------------------

def reasons(api):
    """{call id: reason} for the unrecorded events the gate named (ask_ingest.unrecorded)."""
    with api.app.state.store.read() as c:
        return {r["target"]: json.loads(r["detail"])["why"]
                for r in H._rows(c.execute("SELECT target, detail FROM events WHERE action=?", (A.UNRECORDED,)))}


def test_a_plain_bot_task_to_a_person_writes_one_row_and_one_judge_call(gate):
    api, model, token = gate
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben"
    [item], [call] = items(api), calls(api)
    assert (item["call_id"], item["mode"], item["outcome"]) == (call["id"], A.ON, A.THROUGH)
    assert len(model.calls) == 1 and not reasons(api)


def test_a_saved_key_retry_makes_no_new_judge_call(gate):
    api, model, token = gate
    key = "retry-the-same-create"
    body = {"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"}
    first = api.post("/api/v2/tasks", json=body, headers={**headers(token), "Idempotency-Key": key})
    assert first.status_code == 200, first.text
    again = api.post("/api/v2/tasks", json=body, headers={**headers(token), "Idempotency-Key": key})
    assert again.status_code == 200 and again.json()["task"]["id"] == first.json()["task"]["id"]
    assert len(model.calls) == 1 and len(calls(api)) == 1 and len(items(api)) == 1


def test_a_joined_blocker_write_ends_in_a_named_reason_never_a_silent_gap(gate):
    api, model, token = gate
    blocker = {"repo": "ticoteam/tico", "status": 403, "endpoint": "workflow_dispatch"}
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {**A.DEFAULTS, "mode": "off"})
    first = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Blocked first", "body": "b", "blocker": blocker},
                     headers=headers(token))
    assert first.status_code == 200, first.text
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {**A.DEFAULTS, "mode": "on"})
    second = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Blocked second", "body": "b", "blocker": blocker},
                      headers=headers(token))
    assert second.status_code == 200 and second.json().get("joined")
    [call] = calls(api)
    assert reasons(api) == {call["id"]: "joined"} and not items(api)


def test_a_refused_create_writes_no_row_and_its_call_is_labelled_refused(gate):
    api, model, token = gate
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Goal gone", "body": "x", "goal_id": "no-such-goal"},
                 headers=headers(token))
    assert r.status_code == 404, r.text
    [call] = calls(api)
    assert len(model.calls) == 1 and not items(api) and reasons(api) == {call["id"]: "refused"}


def test_a_privacy_refused_create_writes_no_row_and_its_call_is_labelled_refused(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
    private_task(api, "bot:cpo", "bot:cmo", "Zip triage")
    attempt = run_on(api, "cmo")                # the run carries a private task: a company-visible create is refused
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?",
                                        "private": False}, headers=headers(attempt["token"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "privacy", r.text
    [call] = calls(api)
    assert len(model.calls) == 1 and not items(api) and reasons(api) == {call["id"]: "refused"}


def test_the_readout_reconciles_every_call_to_a_row_or_a_named_reason(gate):
    api, model, token = gate
    plate(api, 15)
    assert_refused(api, api.post("/api/v2/tasks", json={"owner": "ben", "title": "Pick the launch date"},
                                 headers=headers(token)))
    model.meets = "Strategy"
    api.post("/api/v2/tasks", json={"owner": "ben", "title": "Goal gone", "body": "x", "goal_id": "no-such-goal"},
             headers=headers(token))                                      # refused: a named reason
    row = next(r for r in get(api, "judge/readout")["sets"] if r["set"] == A.QUESTION_SET)
    assert row["reconcile"] == {"calls": 2, "landed": 1, "reasons": {"refused": 1}, "unexplained": 0, "no_call": 0}
    assert row["versions"] == ["2"], "the readout groups answers by the set's version, now 2"
    assert "calls reconciled: 2 = 1 with an item row, refused 1; unexplained 0; rows with no call 0" \
        in get(api, "judge/readout")["text"]


def test_a_call_with_neither_a_row_nor_a_reason_is_shown_as_unexplained(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "judge.call", "person-ask-ingest@2", {"label": "person-ask-ingest@2", "mode": "on"})
    row = next(r for r in get(api, "judge/readout")["sets"] if r["set"] == A.QUESTION_SET)
    assert row["reconcile"]["unexplained"] == 1 and row["reconcile"]["calls"] == 1


def test_a_settings_failure_before_the_verdict_keeps_the_failed_open_record(gate, monkeypatch):
    """The settings read is the first thing preflight does after the person is known: a failure there still writes
    the failed_open row, and the create still succeeds (Dev 702 QA)."""
    api, model, token = gate
    real, seen = A.settings, []

    def fails_once(c, person):
        if not seen:
            seen.append(person)
            raise RuntimeError("settings unreadable")
        return real(c, person)
    monkeypatch.setattr(A, "settings", fails_once)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert (item["outcome"], item["would"], item["reason"]) == (A.FAILED_OPEN, A.FAILED_OPEN, "error")
    assert item["ref"] == out["task"]["id"] and item["call_id"] is None
    assert not calls(api) and not model.calls


def test_a_settings_read_that_always_fails_still_lands_a_failed_open_row(gate, monkeypatch):
    """The settings read fails on every call, in preflight and again inside the write: the create still succeeds,
    and its one row is a failed open with no judge call (Dev 702 QA retest)."""
    api, model, token = gate

    def always_fails(c, person):
        raise RuntimeError("settings unreadable")
    monkeypatch.setattr(A, "settings", always_fails)
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert (item["outcome"], item["would"], item["reason"]) == (A.FAILED_OPEN, A.FAILED_OPEN, "error")
    assert item["ref"] == out["task"]["id"] and item["call_id"] is None
    assert not calls(api) and not model.calls


# ---- what the model reads for a structured question (Dev 724) --------------------------------------------------

DATES = [{"id": "date", "header": "Date", "question": "Which launch date do you want?",
          "why": "It sets the venue deposit.",
          "options": [{"label": "Nov 3", "then": "Book the venue for Nov 3", "description": "The earlier date"},
                      {"label": "Nov 10", "then": "Book the venue for Nov 10", "recommended": True}]}]


def model_text(model):
    return model.calls[-1]["state"]["item"]["text"]


def test_a_structured_task_question_carries_its_lines_and_the_task_body(gate):
    api, model, token = gate
    r = api.post("/api/v2/task-questions", headers=headers(token), json={
        "on": "human:ben", "question": "Which launch date do you want?", "why": "It sets the venue deposit.",
        "body": "Dates and venue.",
        "options": [{"label": "Nov 3", "then": "Book Nov 3"}, {"label": "Nov 10", "then": "Book Nov 10", "recommended": True}]})
    assert r.status_code == 200, r.text
    assert model_text(model) == (
        "Question: Which launch date do you want?\n"
        "Why: It sets the venue deposit.\n"
        "Option: Nov 3 (then: Book Nov 3)\n"
        "Option: Nov 10 (then: Book Nov 10)\n"
        "Recommended: Nov 10\n\n"
        "Dates and venue.")
    assert model.calls[-1]["state"]["item"]["title"] == "Which launch date do you want?"


def test_a_structured_comment_ask_carries_its_lines(gate):
    api, model, token = gate
    tid = own_task(api, token)["id"]
    r = api.post(f"/api/v2/tasks/{tid}/comments", headers=headers(token),
                 json={"text": "Dates and venue.", "ask": {"questions": DATES, "who": "ben"}})
    assert r.status_code == 200, r.text
    assert model_text(model).startswith("Question: Which launch date do you want?\nWhy: It sets the venue deposit.\n")
    assert model_text(model).endswith("Recommended: Nov 10\n\nDates and venue.")


def test_a_long_task_body_is_cut_and_the_lines_and_options_survive(gate):
    api, model, token = gate
    r = api.post("/api/v2/task-questions", headers=headers(token), json={
        "on": "human:ben", "question": "Which launch date do you want?", "why": "It sets the venue deposit.",
        "body": "Long context. " * 300,
        "options": [{"label": "Nov 3", "then": "Book Nov 3"}, {"label": "Nov 10", "then": "Book Nov 10", "recommended": True}]})
    assert r.status_code == 200, r.text
    text = model_text(model)
    assert len(text) == A.MAX_TEXT
    assert text.startswith("Question: Which launch date do you want?\nWhy: It sets the venue deposit.\n"
                           "Option: Nov 3 (then: Book Nov 3)\nOption: Nov 10 (then: Book Nov 10)\n"
                           "Recommended: Nov 10\n\nLong context.")


def test_an_item_without_structured_questions_is_judged_as_before(gate):
    api, model, token = gate
    text = "Which venue do you want? " * 100
    r = api.post("/api/v2/messages", headers=headers(token), json={"to": "ben", "text": text, "kind": "ask"})
    assert r.status_code == 200, r.text
    assert model.calls[-1]["state"]["item"]["text"] == text[:A.MAX_TEXT]
    assert A.structured_text(None, "plain") == "plain" and A.structured_text([], "plain") == "plain"


def test_the_largest_question_set_keeps_every_question_option_and_recommendation(gate):
    """The schema's largest set: four questions of 300 characters, six options of 60 each, each with a why and a then.
    The lines are never cut, so every question, option and recommended option reads, in order, with no body."""
    qs = []
    for q in range(1, 5):
        options = [{"label": f"Q{q} option {o} " + "L" * 45, "then": f"Q{q} then {o} " + "T" * 100,
                    "recommended": o == 6} for o in range(1, 7)]
        qs.append({"question": f"Q{q} " + "Q" * 296, "why": f"Q{q} why " + "W" * 190, "options": options})
    text = A.structured_text(qs, "Body")
    cursor = 0
    for q in qs:
        for needle in [q["question"][:300], "Why: " + q["why"][:200]]:
            cursor = text.index(needle, cursor) + len(needle)
        for option in q["options"]:
            cursor = text.index(f"Option: {option['label'][:60]}", cursor) + 1
        cursor = text.index(f"Recommended: {q['options'][5]['label'][:60]}", cursor) + 1
    assert len(text) > A.MAX_TEXT and text.count("Recommended:") == 4
    assert "Body" not in text, "the lines are never cut to make room for a body"


def ana_gate(api):
    """Ana, asked by a bot's file review, with the filter On and a fake model."""
    model = FakeModel()
    api.app.state.judge = model
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ana", "human:ana", {"mode": "on", "budget": 10, "chores_budget": 5,
                                             "criteria": A.DEFAULTS["criteria"]})
    return model


def test_a_file_review_question_is_judged_with_the_choices_it_shows(api):
    _, _, attempt = setup_attempt(api)
    model = ana_gate(api)
    v1 = publish(api, attempt["token"], "# Cut one")
    r = api.post("/api/v2/task-questions", headers=headers(attempt["token"]),
                 json={"on": "ana", "question": "Is this cut ready?", "review": [{"file": v1["id"]}]})
    assert r.status_code == 200, r.text
    assert model_text(model) == ("Question: Is this cut ready?\n"
                                 "Option: Approve (then: This version is approved)\n"
                                 "Option: Request changes (then: Your note on what to change goes back)")


def test_a_picked_file_review_judges_one_choice_per_file(api):
    _, _, attempt = setup_attempt(api)
    model = ana_gate(api)
    v1, v2 = publish(api, attempt["token"], "# Cut one"), publish(api, attempt["token"], "# Cut two", name="cut-2.md")
    r = api.post("/api/v2/task-questions", headers=headers(attempt["token"]),
                 json={"on": "ana", "question": "Which cut ships?", "pick": True,
                       "review": [{"file": v1["id"]}, {"file": v2["id"]}]})
    assert r.status_code == 200, r.text
    lines = model_text(model).split("\n")
    assert lines[0] == "Question: Which cut ships?"
    assert lines[-1].startswith("Option: Request changes")
    assert sum(line.startswith("Option: ") and "then: This one is approved" in line for line in lines) == 2


def test_a_task_question_replays_with_the_body_it_was_judged_with(gate):
    """A task question's body is judged live and lives on its task, which is the bot's: Replay reads it from there."""
    api, model, token = gate
    body = ("Dates and venue, with the context the person needs. " * 4).strip()
    r = api.post("/api/v2/task-questions", headers=headers(token), json={
        "on": "human:ben", "question": "Which launch date do you want?", "why": "It sets the venue deposit.",
        "body": body,
        "options": [{"label": "Nov 3", "then": "Book Nov 3"}, {"label": "Nov 10", "then": "Book Nov 10", "recommended": True}]})
    assert r.status_code == 200, r.text
    live = model_text(model)
    head, rest = live.split("\n\n", 1)
    assert head.endswith("Recommended: Nov 10") and rest == body
    out = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": H.now()[:10]},
                   headers=headers("ben-test"))
    assert out.status_code == 200, out.text
    assert model_text(model) == live
    assert model.calls[-1]["state"]["item"]["title"] == "Which launch date do you want?"


def test_a_comment_ask_that_names_its_question_decision_replays_its_comment_not_the_task_body(gate):
    """Only a task question is replayed with its task's body. A client can name its question `decision` on a comment,
    which must not make Replay read the task's body in place of the comment (QA, Dev 724)."""
    api, model, token = gate
    tid = own_task(api, token)["id"]
    decision = [dict(DATES[0], id="decision")]
    r = api.post(f"/api/v2/tasks/{tid}/comments", headers=headers(token),
                 json={"text": "Tell me which date.", "ask": {"questions": decision, "who": "ben"}})
    assert r.status_code == 200, r.text
    live = model_text(model)
    assert live.endswith("Recommended: Nov 10\n\nTell me which date.")
    out = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": H.now()[:10]}, headers=headers("ben-test"))
    assert out.status_code == 200, out.text
    assert model_text(model) == live
    assert "Plan the November launch" not in model_text(model)


def test_a_replayed_structured_question_judges_the_same_lines(gate):
    api, model, token = gate
    tid = own_task(api, token)["id"]
    r = api.post(f"/api/v2/tasks/{tid}/comments", headers=headers(token),
                 json={"text": "Tell me which date.", "ask": {"questions": DATES, "who": "ben"}})
    assert r.status_code == 200, r.text
    day = H.now()[:10]
    out = api.post("/api/v2/ask-ingest/replay", json={"person": "ben", "date": day}, headers=headers("ben-test"))
    assert out.status_code == 200, out.text
    assert model_text(model).endswith("Recommended: Nov 10\n\nTell me which date.")
    assert model.calls[-1]["label"] and '"mode": "replay"' in str(calls(api)[-1])
    assert all("Recommended" not in str(row) and "Tell me" not in str(row) for row in calls(api))


@pytest.mark.parametrize("kind", ["status", "own_work"])
@pytest.mark.parametrize("path", ["create", "handoff", "wait", "comment", "question", "task_ask", "message"])
def test_refusals_preserve_all_domain_rows_across_write_paths(gate, kind, path):
    api, model, token = gate
    model.kind = kind
    if path in ("handoff", "wait", "comment"):
        task = own_task(api, token)
    elif path == "task_ask":
        task = for_ben(api)
    if path == "create":
        endpoint, body = "tasks", {"owner": "ben", "title": "Pick the launch date"}
    elif path == "handoff":
        endpoint, body = f"tasks/{task['id']}", {"version": task["version"], "owner": "ben"}
    elif path == "wait":
        endpoint, body = f"tasks/{task['id']}", {"version": task["version"], "status": "waiting", "waiting_on": "ben",
                                                  "note": "Which launch date do you want?"}
    elif path == "comment":
        endpoint, body = f"tasks/{task['id']}/comments", {"text": "Which launch date?", "ask": {"who": "ben",
            "questions": [{"id": "date", "question": "Which launch date?", "options": [
                {"label": "Nov 3", "then": "Launch on Nov 3"}, {"label": "Nov 10", "then": "Launch on Nov 10"}]}]}}
    elif path == "question":
        endpoint, body = "task-questions", {"on": "ben", "question": "Which launch date?"}
    elif path == "task_ask":
        endpoint, body = f"tasks/{task['id']}/ask", {"text": "Which launch date?"}
    else:
        endpoint, body = "messages", {"to": "ben", "text": "Which launch date?", "kind": "ask"}
    before = write_state(api)
    r = api.post("/api/v2/" + endpoint, json=body, headers=headers(token))
    assert_refused(api, r, before)
    [item], [call] = items(api), calls(api)
    assert item["call_id"] == call["id"]
    assert item["outcome"] == (A.FYI if kind == "status" else A.SENT_BACK)


def test_a_refusal_can_be_fixed_and_retried_with_the_same_key(gate):
    api, model, token = gate
    plate(api, 15)
    body = {"owner": "ben", "title": "Pick the launch date", "body": "Nov 3 or Nov 10?"}
    keyed = {**headers(token), "Idempotency-Key": "fix-a-refused-ask"}
    before = write_state(api)
    assert_refused(api, api.post("/api/v2/tasks", json=body, headers=keyed), before)
    model.meets = "Strategy"
    body["body"] = "Strategy and positioning: Nov 3 or Nov 10? I recommend Nov 10."
    fixed = api.post("/api/v2/tasks", json=body, headers=keyed)
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["task"]["owner_id"] == "human:ben"
    assert len(items(api)) == 2 and len(calls(api)) == 2


def test_repeated_ask_refusals_never_quarantine_or_hold_the_bot(gate):
    api, model, token = gate
    model.kind = "own_work"
    before = write_state(api)
    for _ in range(6):
        assert_refused(api, api.post("/api/v2/tasks", json={"owner": "ben", "title": "Verify the deployment"},
                                     headers=headers(token)), before)
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"
    assert len(items(api)) == 6


def test_off_at_write_time_does_not_refuse_an_ask_already_judged(gate):
    api, model, token = gate
    model.kind = "own_work"

    def turn_off():
        with api.app.state.store.transaction() as c:
            A.save(c, "human:ben", "human:ben", {**A.DEFAULTS, "mode": "off"})

    model.before = turn_off
    assert file(api, token)["task"]["owner_id"] == "human:ben"
    assert items(api)[0]["outcome"] == A.THROUGH
