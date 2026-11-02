"""Ask ingest (backend/ask_ingest.py, spec ask-ingest.md v2): a bot's task for a person is judged before it lands.
With the filter On, the budget tightens as the person's plate fills; sent back or FYI, the task stays the bot's own
and the reply says why, naming the bot's manager. Rehearsal changes nothing and lists what On would have done. A
person's own write is never gated, and any failure lets the item through as before."""

import json
import threading
import time

import pytest

from backend import ask_ingest as A
from backend import call_slots as CS
from backend import hubdb as H
from backend.tests.test_api import unrelated_task_managers
from backend.tests.test_task_guest import run_on
from backend.tests.test_task_guest import task as private_task
from backend.tests.test_tasks_board import api, bot_token, get, headers, post   # noqa: F401


class FakeModel:
    """Answers the set as told: clear/recommends as probabilities, kind, meets (the option whose line holds
    `meets`), covered (the option whose title holds `covered`) and urgency 0-3."""

    def __init__(self, clear=0.95, kind="decision", meets=None, urgency=1.0, recommends=0.1, covered=None):
        self.clear, self.kind, self.meets, self.urgency = clear, kind, meets, urgency
        self.recommends, self.covered = recommends, covered
        self.calls, self.fail, self.hold = [], None, None

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
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
    """Ben's filter On (budget 10, chores 5, the default criteria), ops reporting to cpo, a fake model."""
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
    (50, answers(kind="chore"), (A.HANDS, "chore")),                                        # criterion 8
    (0, answers(kind="own_work"), (A.SENT_BACK, "own_work")),                               # criterion 9
    (0, answers(clear=False), (A.SENT_BACK, "unclear")),
    (0, answers(clear=False, urgency=3), (A.THROUGH, "urgent")),
    (0, answers(clear=None), (A.FAILED_OPEN, "below_threshold")),
    (15, answers(meets=None), (A.FAILED_OPEN, "below_threshold")),
    (40, answers(meets="line-1", recommends=None), (A.FAILED_OPEN, "below_threshold")),
    (40, answers(meets="line-1", recommends=True, urgency=None), (A.THROUGH, "recommends")),  # the band reads no urgency
])
def test_the_budget_tightens_as_the_plate_fills(load, given, expected):
    assert A.decide(given, 10, 5, load, 0) == expected


def test_a_recommends_reply_names_the_criterion_and_the_recommendation_only():
    text = A.message("recommends", "Ben", 40, 0, "Priya")
    assert "Ben has 40 open items" in text and "criteria and your recommendation" in text
    assert "date" not in text and "past today" not in text, "no band asks for a date now"


def test_chores_have_their_own_budget():
    assert A.decide(answers(kind="chore"), 10, 5, 0, 4) == (A.HANDS, "chore")
    assert A.decide(answers(kind="chore"), 10, 5, 0, 5) == (A.SENT_BACK, "chores_full")
    assert A.decide(answers(kind="chore", urgency=3), 10, 5, 0, 5) == (A.HANDS, "chore")


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


def test_over_budget_with_no_criterion_it_stays_with_the_bot_and_names_its_manager(gate):
    api, model, token = gate
    plate(api, 15)
    with api.app.state.store.read() as c:
        refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]
    out = file(api, token)
    assert out["task"]["owner_id"] == "bot:ops", "sent back: the task stays the bot's own"
    text = out["ingest"]["text"]
    assert out["ingest"]["outcome"] == A.SENT_BACK and text.startswith("Not sent to ")
    assert "15 open items" in text and "cpo" in text and "nothing was refused" in text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals, "never a refusal"
        assert out["task"]["id"] not in {t["id"] for t in H.needs_you(c, "human:ben")["tasks"]}
    listed = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert [(i["task"], i["outcome"], i["reason"]) for i in listed] == [(out["task"]["id"], A.SENT_BACK, "criteria")]
    [call] = calls(api)
    assert "Pick the launch date" not in call["detail"], "the call row keeps no text"


def test_meeting_a_criterion_gets_it_through_at_that_load(gate):
    api, model, token = gate
    plate(api, 15)
    model.meets = "$200 a month"
    out = file(api, token, "Raise the Gateway cap to $400 a month")
    assert out["task"]["owner_id"] == "human:ben" and "ingest" not in out
    assert [(i["outcome"], i["reason"]) for i in items(api)] == [(A.THROUGH, "criteria")]


def test_a_status_line_is_fyi_and_stays_with_the_bot(gate):
    api, model, token = gate
    model.kind = "status"
    out = file(api, token, "Merged; release at 17:00Z", "Merged; release at 17:00Z.")
    assert out["task"]["owner_id"] == "bot:ops" and out["ingest"]["outcome"] == A.FYI
    assert [i["outcome"] for i in get(api, "ask-ingest/filtered", token="ben-test")["items"]] == [A.FYI]


def test_rehearsal_changes_nothing_and_lists_what_on_would_do(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
    model.kind = "own_work"
    out = file(api, token, "Verify the preview cleanup and charges")
    assert out["task"]["owner_id"] == "human:ben"
    assert out["ingest"]["outcome"] == A.THROUGH and out["ingest"]["would"] == A.SENT_BACK
    assert out["ingest"]["text"].startswith("Rehearsal, nothing changed.")
    assert "work you can do yourself" in out["ingest"]["text"]
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert (item["mode"], item["outcome"], item["would"]) == ("rehearsal", A.THROUGH, A.SENT_BACK)


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
        assert A.load(c, "human:ben") == (1, 0)


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
    plate(api, 15)
    first = file(api, token, "Pick the launch date")
    assert first["task"]["owner_id"] == "bot:ops"
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("ben-test"),
                json={"mode": "on", "budget": 50, "chores_budget": 5, "criteria": ["Pricing"]})
    assert r.status_code == 200, r.text
    second = file(api, token, "Pick the launch venue")
    assert second["task"]["owner_id"] == "human:ben"
    with api.app.state.store.read() as c:
        assert H.task(c, first["task"]["id"])["owner_id"] == "bot:ops", "nothing already decided moves"


def test_settings_are_a_persons_own_and_the_team_default_is_an_admins(gate):
    api, model, token = gate
    own = get(api, "ask-ingest/settings", token="priya-test")
    assert own["settings"]["mode"] == "rehearsal" and own["settings"]["budget"] == 10, "ships as Rehearsal"
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
    assert r.status_code == 422


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


def test_a_sent_back_question_on_a_task_is_withdrawn_at_once(gate):
    api, model, token = gate
    plate(api, 15)
    tid = own_task(api, token)["id"]
    question = {"questions": [{"id": "date", "header": "Date", "question": "Which launch date do you want?",
                               "options": [{"label": "Nov 3", "then": "Launch on Nov 3"},
                                           {"label": "Nov 10", "then": "Launch on Nov 10"}]}], "who": "ben"}
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Which launch date do you want?", "ask": question},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK
    assert not on_plate(api, tid)
    with api.app.state.store.read() as c:
        assert not [a for a in H.open_task_asks(c, H.task(c, tid)) if a["recipient_id"] == "human:ben"]
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert (item["kind"], item["task"], item["outcome"]) == ("question", tid, A.SENT_BACK) and item["message"]


def test_a_sent_back_task_question_does_not_wait_on_the_person(gate):
    api, model, token = gate
    plate(api, 15)
    r = api.post("/api/v2/task-questions", json={"on": "human:ben", "question": "Which launch date do you want?"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
    assert row["status"] != "waiting" and row["waiting_on"] is None
    assert not on_plate(api, task["id"])


def test_in_rehearsal_a_task_question_still_reaches_the_person(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
    plate(api, 15)
    r = api.post("/api/v2/task-questions", json={"on": "human:ben", "question": "Which launch date do you want?"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        assert H.task(c, r.json()["task"]["id"])["waiting_on"] == "human:ben"
    assert on_plate(api, r.json()["task"]["id"])
    assert (r.json()["ingest"]["outcome"], r.json()["ingest"]["would"]) == (A.THROUGH, A.SENT_BACK)


def test_a_progress_line_as_a_wait_is_fyi_and_no_wait_is_set(gate):
    api, model, token = gate
    model.kind = "status"
    task = own_task(api, token, "Ship the release")
    r = api.post(f"/api/v2/tasks/{task['id']}", headers=headers(token),
                 json={"version": task["version"], "status": "waiting", "waiting_on": "ben",
                       "note": "Merged; release at 17:00Z"})
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.FYI
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["waiting_on"] is None and A.load(c, "human:ben") == (0, 0), "nothing on the plate"
    assert model.calls[-1]["state"]["item"]["text"] == "Merged; release at 17:00Z"
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert (item["kind"], item["outcome"]) == ("wait", A.FYI)


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
        assert A.load(c, "human:ben") == (1, 0)


def test_a_sent_back_loose_question_leaves_needs_you(gate):
    api, model, token = gate
    plate(api, 15)
    r = api.post("/api/v2/messages", json={"to": "ben", "text": "Which launch date do you want?", "kind": "ask"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK
    with api.app.state.store.read() as c:
        assert H.loose_asks_to(c, "human:ben") == []
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert (item["kind"], item["title"], item["task"]) == ("loose", "Which launch date do you want?", None)


def test_a_persons_question_is_not_gated(gate):
    api, model, token = gate
    r = api.post("/api/v2/messages", json={"to": "ben", "text": "Lunch at noon?", "kind": "ask"},
                 headers=headers("priya-test"))
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and not model.calls


# ---- Let it through --------------------------------------------------------------------------------------------

def let_through(api, item_id, token="ben-test", expected=200):
    r = api.post(f"/api/v2/ask-ingest/items/{item_id}/let-through", headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_let_it_through_puts_a_sent_back_task_on_the_plate_and_tells_the_asker_once(gate):
    api, model, token = gate
    plate(api, 15)
    tid = file(api, token)["task"]["id"]
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert let_through(api, item["id"])["item"]["let_through_by"] == "human:ben"
    with api.app.state.store.read() as c:
        assert H.task(c, tid)["owner_id"] == "human:ben" and on_plate(api, tid)
        told = [m for m in H.task_comments(c, tid) if "let this through" in (m["body"] or "")]
    assert len(told) == 1
    let_through(api, item["id"])
    with api.app.state.store.read() as c:
        assert len([m for m in H.task_comments(c, tid) if "let this through" in (m["body"] or "")]) == 1, "once"
    assert get(api, "ask-ingest/filtered", token="ben-test")["items"][0]["let_through_at"]


def test_let_it_through_asks_a_withdrawn_question_again(gate):
    api, model, token = gate
    plate(api, 15)
    r = api.post("/api/v2/messages", json={"to": "ben", "text": "Which launch date do you want?", "kind": "ask"},
                 headers=headers(token))
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    let_through(api, item["id"])
    with api.app.state.store.read() as c:
        assert [m["id"] for m in H.loose_asks_to(c, "human:ben")] == [r.json()["id"]]


def test_only_the_person_lets_their_items_through_and_rehearsal_has_nothing_to_let_through(gate):
    api, model, token = gate
    plate(api, 15)
    file(api, token)
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    let_through(api, item["id"], token="priya-test", expected=404)
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
    file(api, token, "Pick the launch venue")
    rehearsed = next(i for i in get(api, "ask-ingest/filtered", token="ben-test")["items"] if i["mode"] == "rehearsal")
    let_through(api, rehearsed["id"], expected=409)


def test_the_decisions_readout_shows_calls_outcomes_and_let_throughs(gate):
    api, model, token = gate
    plate(api, 15)
    file(api, token)                                   # sent back
    model.meets = "$200 a month"
    file(api, token, "Raise the Gateway cap to $400 a month")    # through
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    let_through(api, item["id"])
    readout = get(api, "judge/readout")
    [row] = [r for r in readout["sets"] if r["set"] == A.QUESTION_SET]
    assert row["calls"] == 2
    assert row["gate"] == {"outcomes": {A.SENT_BACK: 1, A.THROUGH: 1}, "rehearsal": {}, "let_through": 1}
    assert "outcomes: sent_back 1, through 1; let through 1" in readout["text"]


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
        assert A.load(c, "human:ben") == (1, 0), "one item on the plate, not two"
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


def test_in_rehearsal_a_repeat_still_reaches_the_person(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
    first = file(api, token, "Pick the launch date for Acme")["task"]
    model.covered = "launch date for Acme"
    second = file(api, token, "Launch date?")
    assert second["task"]["owner_id"] == "human:ben" and second["ingest"]["would"] == A.MERGED
    api.post(f"/api/v2/tasks/{first['id']}/comments", json={"text": "Nov 10."}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        assert not [m for m in H.task_comments(c, second["task"]["id"]) if "Nov 10." in (m["body"] or "")]


# ---- Hands needed and the way back to the team default ---------------------------------------------------------

def test_a_chore_lands_in_hands_needed_on_its_own_budget(gate):
    api, model, token = gate
    plate(api, 30)
    model.kind = "chore"
    tid = file(api, token, "Grant ops read access to the billing repo", "One click in GitHub settings.")["task"]["id"]
    assert on_plate(api, tid), "a chore reaches the person even over the decision budget"
    cards = {it["id"]: it for it in get(api, "needs-you?limit=200", token="ben-test")["items"]}
    assert cards[tid]["need"] == "hands"
    assert not [k for k, it in cards.items() if k != tid and it["need"] == "hands"], "only the gate's chores group there"
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben")[1] == 1


def test_a_person_goes_back_to_the_team_default(gate):
    api, model, token = gate
    assert get(api, "ask-ingest/settings", token="ben-test")["settings"]["own"] is True
    r = api.delete("/api/v2/ask-ingest/settings", headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    assert r.json()["settings"] == {**get(api, "ask-ingest/settings", token="ben-test")["team"], "own": False}
    plate(api, 15)
    assert file(api, token)["task"]["owner_id"] == "human:ben", "the team default ships as Rehearsal"
    assert api.delete("/api/v2/ask-ingest/settings", headers=headers(token)).status_code == 403


# ---- the replay before On --------------------------------------------------------------------------------------

def test_a_replay_judges_a_past_day_as_on_and_moves_nothing(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
    plate(api, 15)
    tid = file(api, token)["task"]["id"]
    api.post("/api/v2/messages", json={"to": "ben", "text": "Which venue do you want?", "kind": "ask"}, headers=headers(token))
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


# ---- Review 1: a task question let through waits again; a bot's handoff to a person is gated --------------------

def test_let_it_through_a_task_question_asks_again_and_waits_on_the_person(gate):
    api, model, token = gate
    plate(api, 15)
    r = api.post("/api/v2/task-questions", json={"on": "human:ben", "question": "Which launch date do you want?"},
                 headers=headers(token))
    tid = r.json()["task"]["id"]
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert item["kind"] == "task_question"
    let_through(api, item["id"])
    with api.app.state.store.read() as c:
        row = H.task(c, tid)
        assert (row["status"], row["waiting_on"]) == ("waiting", "human:ben"), "the wait is back as first filed"
        assert [a for a in H.open_task_asks(c, row) if a["recipient_id"] == "human:ben"]
    assert on_plate(api, tid)


def test_a_bot_handing_its_task_to_a_person_is_gated(gate):
    api, model, token = gate
    plate(api, 15)
    task = own_task(api, token, "Pick the launch date")
    r = api.post(f"/api/v2/tasks/{task['id']}", json={"version": task["version"], "owner": "ben"}, headers=headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK and r.json()["task"]["owner_id"] == "bot:ops"
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    let_through(api, item["id"])
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["owner_id"] == "human:ben"


def test_a_bots_bulk_handoff_to_a_person_is_gated_task_by_task(gate):
    api, model, token = gate
    plate(api, 15)
    ids = [own_task(api, token, f"Pick launch option {n}")["id"] for n in range(3)]
    r = api.post("/api/v2/task-bulk", json={"filter": {"ids": ids}, "action": "owner", "owner": "ben"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        assert {H.task(c, i)["owner_id"] for i in ids} == {"bot:ops"}
    assert sorted(i["ref"] for i in items(api) if i["kind"] == "task" and i["ref"] in ids) == sorted(ids)


# ---- Review 2: every way a bot asks a person is judged, against the plate before the item ----------------------

def for_ben(api, title="Plan the November launch"):
    """A task Ben asked ops for: ops owns it, Ben is its requester (filed by a person, not gated)."""
    with api.app.state.store.transaction() as c:
        return H.task_create(c, "human:ben", title, "Dates and venue.", "bot:ops", lint=False)


def test_a_bots_task_ask_over_budget_is_sent_back_withdrawn_and_listed(gate):
    api, model, token = gate
    plate(api, 15)
    task = for_ben(api)
    with api.app.state.store.read() as c:
        refusals = c.execute("SELECT count(*) FROM refusals").fetchone()[0]
    r = api.post(f"/api/v2/tasks/{task['id']}/ask", json={"text": "Which launch date do you want?"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK and "15 open items" in r.json()["ingest"]["text"]
    assert not on_plate(api, task["id"])
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        assert row["status"] != "waiting", "not parked on a question that was withdrawn"
        assert not [a for a in H.open_task_asks(c, row) if a["recipient_id"] == "human:ben"]
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert (item["kind"], item["task"], item["outcome"]) == ("task_ask", task["id"], A.SENT_BACK)
    let_through(api, item["id"])
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "waiting"
    assert on_plate(api, task["id"])


def test_a_sent_back_task_ask_keeps_the_bots_note_and_says_why_in_a_quiet_comment(gate):
    api, model, token = gate
    plate(api, 15)
    task = for_ben(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", task["id"], status="doing", note="Drafting the venue shortlist")
    r = api.post(f"/api/v2/tasks/{task['id']}/ask", json={"text": "Which launch date do you want?"},
                 headers=headers(token))
    assert r.status_code == 200 and r.json()["ingest"]["outcome"] == A.SENT_BACK, r.text
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        assert (row["status"], row["note"]) == ("doing", "Drafting the venue shortlist")
        assert not c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='status' AND new='waiting'",
                             (task["id"],)).fetchone(), "never parked, so nothing to put back"
        said = [m for m in H.task_comments(c, task["id"]) if m["sender_id"] == H.KEEPER]
    assert [m["body"] for m in said] == [r.json()["ingest"]["text"]]
    assert said[0]["refs"].get("quiet") is True


def test_a_persons_task_ask_is_not_gated(gate):
    api, model, token = gate
    plate(api, 15)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ben", "Plan the offsite", "", "human:priya", lint=False)
    r = api.post(f"/api/v2/tasks/{task['id']}/ask", json={"text": "Which week?"}, headers=headers("priya-test"))
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and items(api) == [] and model.calls == []
    assert on_plate(api, task["id"])


def test_a_raw_ask_message_naming_a_task_is_judged(gate):
    api, model, token = gate
    plate(api, 15)
    tid = own_task(api, token)["id"]
    r = api.post("/api/v2/messages", headers=headers(token),
                 json={"to": "ben", "text": "Which launch date do you want?", "kind": "ask", "refs": {"task": tid}})
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK
    assert model.calls[-1]["state"]["item"]["title"] == "Plan the November launch"
    [item] = items(api)
    assert (item["kind"], item["message_id"]) == ("question", r.json()["id"])
    with api.app.state.store.read() as c:
        assert not on_plate(api, tid) and H.loose_asks_to(c, "human:ben") == []


def test_a_raw_ask_naming_a_task_the_bot_cannot_read_never_shows_its_title(gate):
    api, model, token = gate
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
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK
    with api.app.state.store.read() as c:
        assert r.json()["id"] not in {m["id"] for m in H.loose_asks_to(c, "human:ben")}


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
    assert r.status_code == 200, r.text
    [item] = [i for i in items(api) if i["asker"] == "bot:ops"]
    assert (item["outcome"], item["reason"], item["load"]) == (*expected, load)
    if expected[0] == A.SENT_BACK:
        assert f"{load} open items" in r.json()["ingest"]["text"]


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


def test_an_owner_and_a_wait_in_one_request_are_each_judged(gate):
    api, model, token = gate
    plate(api, 15)
    task = own_task(api, token, "Pick the venue")
    r = api.post(f"/api/v2/tasks/{task['id']}", headers=headers(token),
                 json={"version": task["version"], "owner": "ops", "status": "waiting", "waiting_on": "ben",
                       "note": "Venue A or B?"})
    assert r.status_code == 200, r.text
    assert r.json()["ingest"]["outcome"] == A.SENT_BACK
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["waiting_on"] is None
    assert [(i["kind"], i["outcome"]) for i in items(api)] == [("wait", A.SENT_BACK)]


def test_a_chore_wait_lands_its_card_in_hands_needed_and_fills_the_chores_budget(gate):
    api, model, token = gate
    model.kind = "chore"
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "on", "budget": 10, "chores_budget": 1, "criteria": []})
    task = own_task(api, token, "Grant ops access")
    r = api.post(f"/api/v2/tasks/{task['id']}", headers=headers(token),
                 json={"version": task["version"], "status": "waiting", "waiting_on": "ben",
                       "note": "Grant ops read access to the billing repo"})
    assert r.status_code == 200, r.text
    [item] = items(api)
    assert item["outcome"] == A.HANDS and item["ref"] != task["id"], "the gate's row names the card"
    cards = {it["id"]: it for it in get(api, "needs-you?limit=200", token="ben-test")["items"]}
    assert cards[item["ref"]]["need"] == "hands"
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == (0, 1)
    again = own_task(api, token, "Grant ops access to the docs")
    r = api.post(f"/api/v2/tasks/{again['id']}", headers=headers(token),
                 json={"version": again["version"], "status": "waiting", "waiting_on": "ben",
                       "note": "Grant ops read access to the docs repo"})
    assert (r.json()["ingest"]["outcome"], r.json()["ingest"]["reason"]) == (A.SENT_BACK, "chores_full")


def test_a_loose_chore_counts_as_a_chore(gate):
    api, model, token = gate
    model.kind = "chore"
    r = api.post("/api/v2/messages", json={"to": "ben", "text": "Please click Approve on the GitHub invite.",
                                           "kind": "ask"}, headers=headers(token))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == (0, 1)
    cards = {it["id"]: it for it in get(api, "needs-you?limit=200", token="ben-test")["items"]}
    assert cards[r.json()["id"]]["need"] == "hands"


def test_a_bots_bulk_handoff_tells_it_why_for_each_task(gate):
    api, model, token = gate
    plate(api, 15)
    ids = [own_task(api, token, f"Pick launch option {n}")["id"] for n in range(3)]
    r = api.post("/api/v2/task-bulk", json={"filter": {"ids": ids}, "action": "owner", "owner": "ben"},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    lines = {line["id"]: line for line in r.json()["tasks"]["changed"]}
    assert set(lines) == set(ids)
    for line in lines.values():
        said = line["ingest"]
        assert (said["outcome"], said["reason"]) == (A.SENT_BACK, "criteria")
        assert "cpo" in said["text"] and "nothing was refused" in said["text"]


# ---- Review 2: the replay sees the plate just before each item, and an admin only what they may read ----------

def test_a_replay_never_offers_an_item_as_its_own_repeat(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
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


@pytest.mark.parametrize("mode", ["on", "rehearsal"])
@pytest.mark.parametrize("where,reason", [("load_set", "not_found"), ("open_items", "error"),
                                        ("server_engine", "unavailable")])
def test_preflight_exception_keeps_failed_open_record(gate, monkeypatch, mode, where, reason):
    from clients.judge import JudgeError
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {**A.DEFAULTS, "mode": mode})
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


def test_a_plain_bot_task_to_a_person_in_rehearsal_writes_one_row_and_one_judge_call(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {**A.DEFAULTS, "mode": "rehearsal"})
    out = file(api, token)
    assert out["task"]["owner_id"] == "human:ben"
    [item], [call] = items(api), calls(api)
    assert (item["call_id"], item["mode"], item["outcome"]) == (call["id"], A.REHEARSAL, A.THROUGH)
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
    file(api, token)                                                      # sent back: a row
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
