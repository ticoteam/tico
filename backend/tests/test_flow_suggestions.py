"""Flow's suggested rules (backend/flow_suggestions.py): drawn from people's send-back notes on a stage's page check
once five are new, shown only to someone who may change the pipeline, written from the notes and the check's text alone
(never a private task's note, a task's title or a person's name), and, once added, asked by Jev of the next page judged
at that check as one more part, beside a shipped check's own parts and bars, until undone."""

import json
import threading

import pytest

from backend import flow_suggestions as F
from backend import page_verdicts as V
from backend import pipeline_judges as PJ
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_pages
from backend import updates
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post
from backend.tests.helpers.page_requirement import WRITER, _page, _pipeline

ANA = "human:ana"
RULE = {"text": "Lists the tests run and their results.", "notes": 4}
STARTED: list[threading.Thread] = []


class Model:
    """A writer that answers in turn from `answers` (the last one again once they run out) and keeps each prompt."""

    def __init__(self, *answers):
        self.answers, self.prompts, self.systems, self.model = list(answers), [], [], "acme-rules-model"

    def __call__(self, system, user):
        self.systems.append(system)
        self.prompts.append(user)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def threads(monkeypatch):
    """Each call runs on its own thread, as in production; `done` joins them."""
    STARTED.clear()

    def spawn(work):
        STARTED.append(threading.Thread(target=work))
        STARTED[-1].start()

    monkeypatch.setattr(F, "spawn", spawn)


def done():
    for thread in STARTED:
        thread.join(10)


def model(monkeypatch, *answers):
    found = Model(*answers)
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: found)
    return found


def check(api, rubric=PS.SPEC_RUBRIC):
    """Product's Spec stage with one page check (a shipped check by default). Its requirement id."""
    with api.app.state.store.transaction() as c:
        for slug in ("writer", "manager"):
            X.bot(c, slug)
        return _pipeline(c, page_settings={"rubric": rubric})


def _task(c):
    """A task at the check's stage, given to the writer bot; titled "Importer spec <n>"."""
    n = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    return X.new_task(c, ANA, f"Importer spec {n}", "product", assignee=WRITER)


def send_backs(api, rid, n, by=ANA, private=False, notes="No test results are listed."):
    """`n` send backs at the check, each on a task of its own titled "Importer spec"."""
    with api.app.state.store.transaction() as c:
        for i in range(n):
            row = _task(c)
            if private:
                c.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
            X.answer(c, row, rid, "no", by, notes=f"{notes} ({i})")


def listed(api, token="ana-test", expected=200, wait=True):
    found = post(api, "flow/suggestions", {}, token=token, expected=expected)
    if wait:
        done()
    return found


def rows(api, table="flow_suggestions"):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]


def spend(api):
    with api.app.state.store.read() as c:
        return [json.loads(r["detail"]) for r in c.execute("SELECT detail FROM events WHERE action='judge.spend'")]


def ready(api, monkeypatch, rid=None, answer=None):
    """A check with one open suggestion (RULE): (requirement id, the suggestion as the list shows it, the model)."""
    rid = rid or check(api)
    writer = model(monkeypatch, answer or {"rules": [RULE]})
    send_backs(api, rid, 5)
    assert listed(api)["state"] == "pending"
    (one,) = listed(api)["suggestions"]
    return rid, one, writer


# ---- when they are worked out ------------------------------------------------------------------------------------
def test_five_new_send_backs_are_worked_out_once_into_shared_suggestions(api, monkeypatch):
    rid = check(api)
    writer = model(monkeypatch, {"rules": [RULE, {"text": "Names the PR.", "notes": 2}]})
    send_backs(api, rid, 4)
    assert listed(api) == {"state": "ready", "suggestions": []} and writer.prompts == [], "four is not enough"
    send_backs(api, rid, 1)
    assert listed(api)["state"] == "pending"
    got = listed(api)
    assert got["state"] == "ready" and len(writer.prompts) == 1
    assert [(s["text"], s["state"], s["notes_used"], s["notes_of"]) for s in got["suggestions"]] == [
        (RULE["text"], "open", 4, 5),
        ("Names the PR.", "open", 2, 5),
    ]
    first = got["suggestions"][0]
    assert (first["pipeline"], first["stage"], first["requirement_id"], first["pending"]) == (
        "Product",
        "Spec",
        rid,
        False,
    )
    assert listed(api, token="ben-test")["suggestions"] == got["suggestions"], "shared with every editor"
    assert len(writer.prompts) == 1, "never worked out twice for the same send backs"

    writer = model(monkeypatch, {"rules": [{"text": "Links the dashboard.", "notes": 5}, RULE]})
    send_backs(api, rid, 4)
    listed(api)
    assert writer.prompts == [], "only send backs after the last working-out count"
    send_backs(api, rid, 1)
    listed(api)
    got = listed(api)
    assert len(writer.prompts) == 1
    assert f"(open) {RULE['text']}" in writer.prompts[0], "shown what it already suggested"
    assert "Send-back notes (10)" in writer.prompts[0]
    texts = [s["text"] for s in got["suggestions"]]
    assert texts == [RULE["text"], "Names the PR.", "Links the dashboard."], "a repeat is dropped"
    assert got["suggestions"][2]["notes_of"] == 10


def test_bot_send_backs_and_notes_without_words_do_not_count(api, monkeypatch):
    rid = check(api)
    writer = model(monkeypatch, {"rules": [RULE]})
    send_backs(api, rid, 5, by="bot:manager")
    with api.app.state.store.transaction() as c:
        for _ in range(5):
            X.answer(c, _task(c), rid, "no", ANA, notes="   ")
    assert listed(api) == {"state": "ready", "suggestions": []} and writer.prompts == []


# ---- who sees them -----------------------------------------------------------------------------------------------
def test_only_someone_who_may_change_the_pipeline_gets_or_acts_on_them(api, monkeypatch):
    _, one, _ = ready(api, monkeypatch)
    assert listed(api, token="cara-test") == {"state": "ready", "suggestions": []}
    for name in ("add", "undo", "dismiss"):
        post(api, f"flow/suggestions/{one['id']}/{name}", {}, token="cara-test", expected=403)
    post(api, f"flow/suggestions/{one['id']}/feedback", {"text": "Shorter"}, token="cara-test", expected=403)
    post(api, "flow/suggestions/rs_nothing/add", {}, expected=404)
    assert rows(api)[0]["state"] == "open", "nothing changed"
    with api.app.state.store.transaction() as c:  # the pipeline's manager is made Cara: she may change it now
        c.execute("UPDATE pipelines SET manager_id='human:cara' WHERE id='product'")
    assert [s["id"] for s in listed(api, token="cara-test")["suggestions"]] == [one["id"]]


def test_a_retired_stage_or_withdrawn_check_hides_its_suggestions(api, monkeypatch):
    rid, one, _ = ready(api, monkeypatch)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE requirements SET withdrawn_at=? WHERE id=?", (H.now(), rid))
    assert listed(api)["suggestions"] == []
    post(api, f"flow/suggestions/{one['id']}/add", {}, expected=404)


# ---- privacy -----------------------------------------------------------------------------------------------------
def test_the_prompt_holds_only_the_check_and_notes_people_may_read(api, monkeypatch):
    rid = check(api)
    writer = model(monkeypatch, {"rules": [RULE]})
    send_backs(api, rid, 5, private=True, notes="SECRET-SALARY-NOTE")
    assert listed(api)["state"] == "ready" and writer.prompts == [], "private tasks' notes never count"
    send_backs(api, rid, 5)
    listed(api)
    (prompt,) = writer.prompts
    assert "SECRET-SALARY-NOTE" not in prompt
    assert PS.SPEC_RUBRIC in prompt and "No test results are listed. (4)" in prompt
    assert "Importer spec" not in prompt, "no task title"
    assert "ana" not in prompt.lower() and "writer" not in prompt.lower(), "no person's or bot's name"
    assert "Send-back notes (5)" in prompt
    (row,) = spend(api)
    assert (row["label"], row["model"]) == ("flow-suggestion", "acme-rules-model") and row["cost_usd"] > 0
    assert "No test results" not in json.dumps(row) and PS.SPEC_RUBRIC not in json.dumps(row), "no prompt in it"


def test_a_private_pipeline_is_never_worked_out(api, monkeypatch):
    rid = check(api)
    writer = model(monkeypatch, {"rules": [RULE]})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE pipelines SET private=1 WHERE id='product'")
    send_backs(api, rid, 6)
    assert listed(api) == {"state": "ready", "suggestions": []} and writer.prompts == []


# ---- what Jev asks -----------------------------------------------------------------------------------------------
def _attach_and_judge(api, monkeypatch, parts):
    """A new page attached at the check, then the Jev step: the questions Jev was asked and the verdict it gave.
    `parts` maps each question to its answer (default 0.9)."""
    store = api.app.state.store
    with store.transaction() as c:
        row = _task(c)
        fid = _page(c, store, "<h1>Spec</h1><p>Goal, scope and criteria.</p>")[0]
        seen = E.task(c, row["id"])["version"]
        attached = task_pages.attach(c, row["id"], "Spec page", "page:" + fid, WRITER, seen_version=seen)
    asked: list[dict] = []

    def raw(state, questions, label):
        asked.append(questions)
        answers = {q: {"type": "noul", "noul": parts.get(q, 0.9)} for q in questions}
        return {"answers": answers, "model": "tico-decide-low", "provider": "gateway", "cost_usd": 0.001, "ms": 3}

    monkeypatch.setattr(PJ, "ASK", raw)
    V.run(store, H.shift(H.now(), seconds=1))
    with store.read() as c:
        verdict = V.get(c, attached["file_id"], attached["version"], attached["rubric_hash"])
    return asked, verdict, attached


SPEC_PARTS = ["part_criteria", "part_goal", "part_in_scope", "part_out_of_scope"]


def test_add_makes_jev_ask_the_rule_beside_the_shipped_checks_parts_and_undo_takes_it_out(api, monkeypatch):
    rid, one, _ = ready(api, monkeypatch)
    added = post(api, f"flow/suggestions/{one['id']}/add", {})["suggestion"]
    assert (added["state"], added["id"]) == ("added", one["id"])
    with api.app.state.store.read() as c:
        assert (
            PJ.check_of(
                json.loads(c.execute("SELECT settings FROM requirements WHERE id=?", (rid,)).fetchone()[0])["rubric"]
            )
            == "spec"
        ), "the check's own text is untouched"

    # The shipped check's parts read on its own bar (0.5): 0.6 is yes there. The rule reads on the default bar (0.75).
    asked, verdict, attached = _attach_and_judge(api, monkeypatch, {"part_goal": 0.6, "part_rule1": 0.1})
    (questions,) = asked
    assert sorted(questions) == sorted([*SPEC_PARTS, "part_rule1"])
    assert RULE["text"] in questions["part_rule1"]["instructions"]
    assert "one part of `requirement.rubric`" in questions["part_goal"]["instructions"]
    assert json.loads(attached["judged_against"])["rules"] == [RULE["text"]]
    assert (verdict["verdict"], verdict["reasons"]) == ("not_met", f'Doesn\'t meet "{RULE["text"]}".')

    # The rule met: the shipped check's parts decide as before, a part at 0.6 still a yes on its bar.
    asked, verdict, _ = _attach_and_judge(api, monkeypatch, {"part_goal": 0.6, "part_rule1": 0.9})
    assert verdict["verdict"] == "met"

    undone = post(api, f"flow/suggestions/{one['id']}/undo", {})["suggestion"]
    assert undone["state"] == "open"
    asked, verdict, attached = _attach_and_judge(api, monkeypatch, {"part_goal": 0.6})
    (questions,) = asked
    assert sorted(questions) == SPEC_PARTS, "the shipped check is asked exactly as before"
    assert "rules" not in json.loads(attached["judged_against"])
    assert attached["rubric_hash"] == V.rubric_hash(PS.SPEC_RUBRIC, json.loads(attached["judged_against"])["context"])
    assert verdict["verdict"] == "met"


def test_a_rule_on_a_check_asked_whole_is_a_part_beside_it(api, monkeypatch):
    rubric = "Says what done looks like"
    rid = check(api, rubric)
    _, one, _ = ready(api, monkeypatch, rid=rid)
    post(api, f"flow/suggestions/{one['id']}/add", {})
    asked, verdict, _ = _attach_and_judge(api, monkeypatch, {"met": 0.9, "part_rule1": 0.5})
    (questions,) = asked
    assert sorted(questions) == ["met", "part_rule1"]
    assert (verdict["verdict"], verdict["reasons"]) == ("unsure", f'Can\'t tell if it meets "{RULE["text"]}".')
    asked, verdict, _ = _attach_and_judge(api, monkeypatch, {"met": 0.1, "part_rule1": 0.9})
    assert verdict["verdict"] == "not_met"


# ---- acting on one -----------------------------------------------------------------------------------------------
def test_dismiss_takes_it_off_the_list_for_everyone_and_states_hold(api, monkeypatch):
    _, one, _ = ready(api, monkeypatch)
    post(api, f"flow/suggestions/{one['id']}/undo", {}, expected=409)  # not added
    gone = post(api, f"flow/suggestions/{one['id']}/dismiss", {}, token="ben-test")["suggestion"]
    assert gone["state"] == "dismissed"
    assert listed(api)["suggestions"] == []
    post(api, f"flow/suggestions/{one['id']}/add", {}, expected=404)
    assert rows(api)[0]["acted_by"] == "human:ben"


def test_feedback_rewrites_it_in_place_and_it_is_pending_until_then(api, monkeypatch):
    _, one, writer = ready(api, monkeypatch)
    gate = threading.Event()
    rewritten = {"text": "Lists every test run, with its result."}

    def slow(system, user):
        gate.wait(5)
        writer.prompts.append(user)
        return rewritten

    slow.model = "acme-rules-model"
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: slow)
    got = post(api, f"flow/suggestions/{one['id']}/feedback", {"text": "Say every test"})["suggestion"]
    assert got["pending"] is True and got["text"] == RULE["text"]
    seen = listed(api, wait=False)
    assert seen["state"] == "pending" and seen["suggestions"][0]["pending"] is True
    post(api, f"flow/suggestions/{one['id']}/add", {}, expected=409)  # not while it is rewritten
    post(api, f"flow/suggestions/{one['id']}/feedback", {"text": "Again"}, expected=409)
    gate.set()
    done()
    seen = listed(api)
    assert seen["state"] == "ready"
    assert [(s["text"], s["pending"], s["notes_used"]) for s in seen["suggestions"]] == [
        (rewritten["text"], False, RULE["notes"])
    ]
    prompt = writer.prompts[-1]
    assert "Say every test" in prompt and RULE["text"] in prompt and "No test results" not in prompt
    assert [s["label"] for s in spend(api)] == ["flow-suggestion", "flow-suggestion-feedback"]
    post(api, f"flow/suggestions/{one['id']}/feedback", {"text": ""}, expected=422)
    post(api, f"flow/suggestions/{one['id']}/feedback", {"text": "x" * 501}, expected=422)


# ---- failures ----------------------------------------------------------------------------------------------------
def test_a_failed_call_saves_nothing_new_and_is_not_asked_again_at_once(api, monkeypatch):
    rid = check(api)
    writer = model(monkeypatch, RuntimeError("provider down"))
    send_backs(api, rid, 5)
    listed(api)
    assert listed(api) == {"state": "ready", "suggestions": []} and len(writer.prompts) == 1
    assert rows(api, "flow_suggestion_runs")[0]["error"] == "error"
    listed(api)
    assert len(writer.prompts) == 1, "not asked again within RETRY_MINUTES"
    monkeypatch.setattr(F, "RETRY_MINUTES", 0)
    writer = model(monkeypatch, {"rules": "not a list"})
    listed(api)
    assert listed(api) == {"state": "ready", "suggestions": []} and len(writer.prompts) == 1, "asked again; no shape"


def test_a_failed_rewrite_keeps_the_text(api, monkeypatch):
    _, one, _ = ready(api, monkeypatch)
    model(monkeypatch, RuntimeError("provider down"))
    post(api, f"flow/suggestions/{one['id']}/feedback", {"text": "Shorter"})
    done()
    (seen,) = listed(api)["suggestions"]
    assert (seen["text"], seen["pending"]) == (RULE["text"], False)
    model(monkeypatch, {"text": "x" * 300})  # too long: kept as it was
    post(api, f"flow/suggestions/{one['id']}/feedback", {"text": "Longer"})
    done()
    assert listed(api)["suggestions"][0]["text"] == RULE["text"]


def test_no_writer_demo_or_the_cap_answer_ready_with_what_is_saved(api, monkeypatch):
    rid, one, _ = ready(api, monkeypatch)
    monkeypatch.setattr(updates, "writer_for", lambda *a, **k: None)
    send_backs(api, rid, 5)
    assert listed(api) == {"state": "ready", "suggestions": [one]}
    got = post(api, f"flow/suggestions/{one['id']}/feedback", {"text": "Shorter"})["suggestion"]
    assert got["pending"] is False and not STARTED[1:], "no model: nothing started"
    writer = model(monkeypatch, {"rules": [{"text": "Names the owner.", "notes": 3}]})
    with api.app.state.store.transaction() as c:  # the month's decision cap reached
        c.execute(
            "INSERT INTO registry_metadata VALUES('maintenance', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps({"monthly_cap_usd": 0.0001}),),
        )
    assert listed(api) == {"state": "ready", "suggestions": [one]} and writer.prompts == []
    monkeypatch.setattr(api.app.state.store.settings, "demo", True)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='maintenance'")
    assert listed(api) == {"state": "ready", "suggestions": [one]} and writer.prompts == []


def test_the_read_of_a_model_answer_keeps_only_whole_new_rules():
    taken = {"names the pr."}
    answer = {
        "rules": [
            {"text": "  Names   the PR. ", "notes": 2},
            {"text": "Links the build.", "notes": 99},
            {"text": "links the build.", "notes": 1},
            {"text": "x" * (F.TEXT_MAX + 1), "notes": 2},
            {"text": "Says why.", "notes": "many"},
            "not a rule",
        ]
    }
    assert F.read(answer, 6, taken) == [{"text": "Links the build.", "notes": 6}, {"text": "Says why.", "notes": 1}]
    assert F.read(["rules"], 6, taken) == []
