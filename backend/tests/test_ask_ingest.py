"""Ask ingest (backend/ask_ingest.py, spec ask-ingest.md v1): a bot's task for a person is judged before it lands.
With the filter On, the budget tightens as the person's plate fills; sent back or FYI, the task stays the bot's own
and the reply says why, naming the bot's manager. Rehearsal changes nothing and lists what On would have done. A
person's own write is never gated, and any failure lets the item through as before."""

import threading

import pytest

from backend import ask_ingest as A
from backend import hubdb as H
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
        return H._rows(c.execute("SELECT * FROM ask_ingest_items ORDER BY created"))


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
    (40, answers(meets="line-4", recommends=True, urgency=1), (A.SENT_BACK, "recommends")),  # criterion 4
    (40, answers(meets="line-4", recommends=True, urgency=2), (A.THROUGH, "recommends")),
    (120, answers(meets="line-4", recommends=True, urgency=2), (A.SENT_BACK, "urgent_only")),  # criterion 5
    (120, answers(urgency=3), (A.THROUGH, "urgent")),
    (0, answers(kind="status"), (A.FYI, "status")),                                         # criterion 7
    (50, answers(kind="chore"), (A.HANDS, "chore")),                                        # criterion 8
    (0, answers(kind="own_work"), (A.SENT_BACK, "own_work")),                               # criterion 9
    (0, answers(clear=False), (A.SENT_BACK, "unclear")),
    (0, answers(clear=False, urgency=3), (A.THROUGH, "urgent")),
    (0, answers(clear=None), (A.FAILED_OPEN, "below_threshold")),
    (15, answers(meets=None), (A.FAILED_OPEN, "below_threshold")),
    (40, answers(meets="line-1", recommends=True, urgency=None), (A.FAILED_OPEN, "below_threshold")),
])
def test_the_budget_tightens_as_the_plate_fills(load, given, expected):
    assert A.decide(given, 10, 5, load, 0) == expected


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
    assert out["task"]["owner"] == "human:ben"
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
    assert out["task"]["owner"] == "bot:ops", "sent back: the task stays the bot's own"
    text = out["ingest"]["text"]
    assert out["ingest"]["outcome"] == A.SENT_BACK and text.startswith("Not sent to ")
    assert "15 open items" in text and "cpo" in text and "nothing was refused" in text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM refusals").fetchone()[0] == refusals, "never a refusal"
        assert out["task"]["id"] not in {t["id"] for t in H.needs_you(c, "human:ben")["tasks"]}
    listed = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert [(i["task"], i["outcome"], i["reason"]) for i in listed] == [(out["task"]["id"], A.SENT_BACK, "criteria")]
    [call] = calls(api)
    assert "Pick the launch date" not in call["detail_json"], "the call row keeps no text"


def test_meeting_a_criterion_gets_it_through_at_that_load(gate):
    api, model, token = gate
    plate(api, 15)
    model.meets = "$200 a month"
    out = file(api, token, "Raise the Gateway cap to $400 a month")
    assert out["task"]["owner"] == "human:ben" and "ingest" not in out
    assert [(i["outcome"], i["reason"]) for i in items(api)] == [(A.THROUGH, "criteria")]


def test_a_status_line_is_fyi_and_stays_with_the_bot(gate):
    api, model, token = gate
    model.kind = "status"
    out = file(api, token, "Merged; release at 17:00Z", "Merged; release at 17:00Z.")
    assert out["task"]["owner"] == "bot:ops" and out["ingest"]["outcome"] == A.FYI
    assert [i["outcome"] for i in get(api, "ask-ingest/filtered", token="ben-test")["items"]] == [A.FYI]


def test_rehearsal_changes_nothing_and_lists_what_on_would_do(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
    model.kind = "own_work"
    out = file(api, token, "Verify the preview cleanup and charges")
    assert out["task"]["owner"] == "human:ben"
    assert out["ingest"]["outcome"] == A.THROUGH and out["ingest"]["would"] == A.SENT_BACK
    assert out["ingest"]["text"].startswith("Rehearsal, nothing changed.")
    assert "work you can do yourself" in out["ingest"]["text"]
    [item] = get(api, "ask-ingest/filtered", token="ben-test")["items"]
    assert (item["mode"], item["outcome"], item["would"]) == ("rehearsal", A.THROUGH, A.SENT_BACK)


@pytest.mark.parametrize("how", ["error", "timeout", "cap"])
def test_it_fails_open_and_records_the_failure(gate, monkeypatch, how):
    api, model, token = gate
    plate(api, 120)
    if how == "error":
        model.fail = RuntimeError("provider down")
    elif how == "timeout":
        monkeypatch.setattr(A, "DEADLINE", 0.2)
        model.hold = threading.Event()
    else:
        monkeypatch.setattr(A, "daily_cap", lambda settings, role: 0)
    out = file(api, token)
    if model.hold:
        model.hold.set()
    assert out["task"]["owner"] == "human:ben" and "ingest" not in out
    [item] = items(api)
    assert item["outcome"] == A.FAILED_OPEN and item["ref"] == out["task"]["id"]
    if how == "cap":
        assert item["reason"] == "budget" and not model.calls
    else:
        [call] = calls(api)
        assert ('"error": "%s"' % how) in call["detail_json"]


def test_a_persons_own_task_for_someone_is_not_gated_but_counts(gate):
    api, model, token = gate
    out = post(api, "tasks", {"owner": "ben", "title": "Read the contract", "body": "By Friday."})
    assert out["owner"] == "human:ben" and not model.calls
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == (1, 0)


def test_no_decision_model_is_decide_off(gate):
    api, model, token = gate
    api.app.state.judge = None
    api.app.state.store.settings.rehearsal = True
    plate(api, 120)
    out = file(api, token)
    assert out["task"]["owner"] == "human:ben" and "ingest" not in out and not items(api) and not calls(api)


def test_mode_off_asks_nothing(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "off", "budget": 10, "chores_budget": 5, "criteria": []})
    out = file(api, token)
    assert out["task"]["owner"] == "human:ben" and "ingest" not in out and not model.calls


def test_a_settings_change_applies_to_the_next_item_only(gate):
    api, model, token = gate
    plate(api, 15)
    first = file(api, token, "Pick the launch date")
    assert first["task"]["owner"] == "bot:ops"
    r = api.put("/api/v2/ask-ingest/settings", headers=headers("ben-test"),
                json={"mode": "on", "budget": 50, "chores_budget": 5, "criteria": ["Pricing"]})
    assert r.status_code == 200, r.text
    second = file(api, token, "Pick the launch venue")
    assert second["task"]["owner"] == "human:ben"
    with api.app.state.store.read() as c:
        assert H.task(c, first["task"]["id"])["owner"] == "bot:ops", "nothing already decided moves"


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
        assert not [a for a in H.open_task_asks(c, H.task(c, tid)) if a["to_actor"] == "human:ben"]
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
        assert H.task(c, tid)["owner"] == "human:ben" and on_plate(api, tid)
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
    assert second["task"]["owner"] == "bot:ops", "the repeat stays with the bot"
    assert second["ingest"]["outcome"] == A.MERGED and "Pick the launch date for Acme" in second["ingest"]["text"]
    [_, merged] = items(api)
    assert (merged["merged_into"], merged["reason"]) == (first["id"], "covered")
    with api.app.state.store.read() as c:
        assert A.load(c, "human:ben") == (1, 0), "one item on the plate, not two"
    r = api.post(f"/api/v2/tasks/{first['id']}/comments", json={"text": "Nov 10."}, headers=headers("ben-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        passed = [m for m in H.task_comments(c, second["task"]["id"]) if "Nov 10." in (m["body"] or "")]
        assert len(passed) == 1 and H.task(c, second["task"]["id"])["owner"] == "bot:ops"
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
        told = c.execute("SELECT body FROM messages WHERE to_actor='bot:ops' AND body LIKE '%answered this%Nov 10.%'").fetchall()
    assert len(told) == 1


def test_in_rehearsal_a_repeat_still_reaches_the_person(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        A.save(c, "human:ben", "human:ben", {"mode": "rehearsal", "budget": 10, "chores_budget": 5, "criteria": []})
    first = file(api, token, "Pick the launch date for Acme")["task"]
    model.covered = "launch date for Acme"
    second = file(api, token, "Launch date?")
    assert second["task"]["owner"] == "human:ben" and second["ingest"]["would"] == A.MERGED
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
    assert file(api, token)["task"]["owner"] == "human:ben", "the team default ships as Rehearsal"
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
