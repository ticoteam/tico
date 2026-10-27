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
