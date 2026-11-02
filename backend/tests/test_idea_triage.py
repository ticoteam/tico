"""Idea triage on the public roadmap's suggestion form (backend/idea_triage.py): a confident bug or small change goes
to Head of Engineering, a covering open roadmap item is named and linked, and anything else (off, error, timeout,
below threshold, the spend cap) leaves the idea with product exactly as before. Every call is a `judge.call` row."""

import json
import threading

import pytest

from backend import hubdb as H
from backend import idea_triage as I
from backend.tests.test_roadmaps import ORIGIN, public_setup
from backend.tests.test_tasks_board import api, get, post   # noqa: F401
from clients import judge as J


class FakeModel:
    """Answers `kind` and `covered` as told; `covered` names the option whose description holds `match`."""

    def __init__(self, kind="product", confidence=0.9, match=None, covered=0.85):
        self.kind, self.confidence, self.match, self.covered = kind, confidence, match, covered
        self.calls, self.fail, self.hold, self.extra = [], None, None, {}
        self.entered = threading.Event()

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        self.entered.set()
        if self.hold:
            self.hold.wait(5)
        if self.fail:
            raise self.fail
        answers = {"kind": {"type": "choice", "choice": self.kind, "confidence": self.confidence}}
        if "covered" in questions:
            options = questions["covered"]["criteria"]
            hit = next((name for name, text in options.items() if self.match and self.match in str(text)), "new")
            answers["covered"] = {"type": "choice", "choice": hit, "confidence": self.covered if hit != "new" else 0.9}
        return {"model": "jev-1.14", "usage": {"input_tokens": 400, "output_tokens": 0}, "ms": 120,
                "answers": {**answers, **self.extra}}


@pytest.fixture
def roadmap(api):
    """The public form on, Head of Product (cpo) receiving suggestions, Head of Engineering (ops) active, one open
    roadmap item, and idea triage turned on with a fake model."""
    item = public_setup(api, limit=100)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.template','product-lead') WHERE bot='cpo'")
        c.execute("UPDATE bot_config SET config=json_set(config,'$.template','engineering-lead') WHERE bot='ops'")
    api.app.state.store.settings.decisions_idea_triage = True
    model = FakeModel()
    api.app.state.judge = model
    return api, item, model


def suggest(api, title, summary=""):
    response = api.post("/public/roadmaps/roadmap/suggest", json={"title": title, "summary": summary}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"id", "requester_token"}, "the visitor learns nothing of the routing"
    return get(api, "tasks/" + response.json()["id"])


def audits(api):
    with api.app.state.store.read() as c:
        return [(r[0], r[1], json.loads(r[2])) for r in c.execute(
            "SELECT actor,target,detail FROM events WHERE action='judge.call' ORDER BY rowid")]


def test_a_feature_matching_an_open_roadmap_item_names_the_item_and_product_sees_the_link(roadmap):
    api, item, model = roadmap
    model.match = "Internal title"
    shown = suggest(api, "Let me export reports", "Export to CSV would help a lot")
    task = shown["task"]
    assert task["owner_id"] == "bot:cpo", "a feature stays with Head of Product"
    assert task["body"].startswith("Idea triage (idea-triage, routing only): for product (product 90%).\n"
                                   "Probably covered by the open roadmap item “Internal title” (task "
                                   + item["id"] + ", 85%)")
    assert task["body"].index("Idea triage") < task["body"].index("Customer suggestion (unverified")
    assert [r["id"] for r in task["relations"].get("related", [])] == [item["id"]]
    # The model saw the open item as an option, the idea as state, never the question ids.
    call = model.calls[-1]
    assert call["label"] == "idea-triage@1"
    assert call["state"] == {"idea": {"title": "Let me export reports", "summary": "Export to CSV would help a lot"}}
    assert list(call["questions"]["covered"]["criteria"]) == ["item-1", "new"]
    assert "dynamic" not in call["questions"]["covered"]
    (actor, target, detail), = audits(api)
    assert (actor, target) == (H.KEEPER, "idea-triage@1")
    assert detail["route"] == "product" and detail["covered"] == item["id"] and detail["model"] == "jev-1.14"
    assert detail["answers"]["kind"]["value"] == "product" and "cost_usd" in detail
    assert "export" not in json.dumps(detail).lower(), "the audit keeps the answers, never the idea's text"


def test_a_bug_goes_to_head_of_engineering(roadmap):
    api, _, model = roadmap
    model.kind, model.confidence = "engineering", 0.93
    task = suggest(api, "The save button does nothing", "Clicking save on settings shows no change")["task"]
    assert task["owner_id"] == "bot:ops"
    assert task["body"].startswith("Idea triage (idea-triage, routing only): a bug or small change (93%), "
                                   "sent to engineering.\n\nCustomer suggestion")
    assert not task["relations"].get("related"), "no covering item, no link"
    assert audits(api)[-1][2]["route"] == "engineering"


@pytest.mark.parametrize("case", ["off", "error", "timeout", "below_threshold", "no_engineering_bot", "spend_cap",
                                  "shape", "unconfigured", "budget", "bad_confidence"])
def test_anything_short_of_a_confident_answer_goes_to_product_as_today(roadmap, monkeypatch, case):
    api, _, model = roadmap
    model.kind, model.confidence = "engineering", 0.95
    model.match = "Internal title"
    settings = api.app.state.store.settings
    if case == "off":
        settings.decisions_idea_triage = False
    elif case == "error":
        model.fail = J.JudgeError("unavailable", "TypeSafe did not answer", retryable=False)
    elif case == "timeout":
        model.hold = threading.Event()
        monkeypatch.setattr(I, "DEADLINE", 0.05)
    elif case == "below_threshold":
        model.confidence = 0.6
    elif case == "no_engineering_bot":
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    elif case == "spend_cap":
        with api.app.state.store.transaction() as c:
            H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": settings.decisions_monthly_usd})
    elif case == "shape":
        model.kind = "neither"
    elif case == "bad_confidence":
        model.confidence = "high"
    elif case == "budget":
        settings.decisions_daily_calls_bot = 0
    elif case == "unconfigured":
        api.app.state.judge = None
        monkeypatch.setattr("backend.judge.fallback_engine", lambda *args, **kwargs: None)
    task = suggest(api, "The save button does nothing", "Clicking save shows no change")["task"]
    if model.hold:
        model.hold.set()
    assert task["owner_id"] == "bot:cpo"
    calls = audits(api)
    if case in ("off", "spend_cap", "unconfigured", "budget"):
        assert not calls and not model.calls, "no call is made, so nothing is audited"
        assert task["body"].startswith("Customer suggestion (unverified"), "exactly as before"
        return
    (_, _, detail), = calls
    assert detail["route"] == "product"
    if case in ("error", "timeout", "shape", "bad_confidence"):
        assert detail["reason"] == {"error": "unavailable", "bad_confidence": "shape"}.get(case, case)
        assert task["body"].startswith("Customer suggestion (unverified")
        assert not task["relations"].get("related")
    else:
        assert detail["reason"] == case


def test_no_call_for_a_suggestion_the_form_refuses_and_none_without_open_items(roadmap):
    api, item, model = roadmap
    post(api, "roadmaps/settings", {"action_limit": 1})
    suggest(api, "First idea")
    calls = len(model.calls)
    refused = api.post("/public/roadmaps/roadmap/suggest", json={"title": "Second idea"}, headers=ORIGIN)
    assert refused.status_code == 429 and len(model.calls) == calls, "a refused suggestion costs no Decide call"
    # With no open roadmap item to be covered by, the call asks only the kind.
    post(api, "roadmaps/settings", {"action_limit": 100})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE type_id=(SELECT id FROM task_types WHERE role='product-roadmap')")
    suggest(api, "Third idea")
    assert set(model.calls[-1]["questions"]) == {"kind"}


def test_a_covered_match_under_its_threshold_is_neither_named_nor_linked(roadmap):
    api, _, model = roadmap
    model.match, model.covered = "Internal title", 0.6
    task = suggest(api, "Let me export reports")["task"]
    assert task["owner_id"] == "bot:cpo" and "Probably covered" not in task["body"]
    assert not task["relations"].get("related")
    assert audits(api)[-1][2]["covered"] is None


def test_a_malformed_extra_answer_neither_breaks_the_suggestion_nor_enters_the_audit(roadmap):
    api, _, model = roadmap
    model.extra = {"extra": {"noul": "invalid"}}
    task = suggest(api, "Let me export reports")["task"]
    assert task["owner_id"] == "bot:cpo"
    (_, _, detail), = audits(api)
    assert set(detail["answers"]) == {"kind", "covered"}


@pytest.mark.parametrize("last", ["allowance", "queue"])
def test_a_suggestion_refused_while_another_waits_on_triage_costs_no_call(roadmap, last):
    """Two suggestions racing for the visitor's last action or the queue's last place: the one in triage holds it,
    so the other is refused without a Decide call, and a refused one gives its place and action back."""
    api, _, model = roadmap
    post(api, "roadmaps/settings", {"action_limit": 1} if last == "allowance" else {"suggestion_limit": 1})
    model.hold = threading.Event()
    first = {}
    worker = threading.Thread(target=lambda: first.update(response=api.post(
        "/public/roadmaps/roadmap/suggest", json={"title": "First idea"}, headers=ORIGIN)))
    worker.start()
    assert model.entered.wait(5)
    second = api.post("/public/roadmaps/roadmap/suggest", json={"title": "Second idea"}, headers=ORIGIN)
    assert second.status_code == 429 and len(model.calls) == 1
    model.hold.set()
    worker.join(10)
    assert first["response"].status_code == 200, first["response"].text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT value FROM registry_metadata WHERE key='roadmap_suggest_reservations'"
                         ).fetchone()[0] == "{}", "the recorded suggestion's place is released"



def test_a_suggestion_refused_after_triage_gives_back_the_visitors_action_and_its_place(roadmap):
    api, _, model = roadmap
    post(api, "roadmaps/settings", {"action_limit": 1})

    def receiver_leaves(state, questions, label=None):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bots SET state='archived' WHERE slug IN ('cpo','cmo')")
        return FakeModel()(state, questions, label)
    api.app.state.judge = receiver_leaves
    refused = api.post("/public/roadmaps/roadmap/suggest", json={"title": "First idea"}, headers=ORIGIN)
    assert refused.status_code == 409
    with api.app.state.store.read() as c:
        assert c.execute("SELECT coalesce(sum(actions),0) FROM roadmap_actions").fetchone()[0] == 0
        assert c.execute("SELECT value FROM registry_metadata WHERE key='roadmap_suggest_reservations'"
                         ).fetchone()[0] == "{}"
