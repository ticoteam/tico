"""The requirement judge: an answer passes only when it is a confident yes or no, a failed or capped call is unsure, one
attachment is judged once, the model is called outside any write transaction, and a person is asked when the rule says
so."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import hubdb as H
from backend import judge as backend_judge
from backend import pipeline_judges as P
from backend import pipeline_requirements as R
from backend import requirement_attachments as A
from backend.config import Settings
from backend.store import Store
from clients import judge as J

from .test_pipeline_foundation import ANA, boot, hub, step_id

FIXTURES = Path(__file__).parent / "fixtures"
BEN = "human:ben"
REQ = {"id": "req-1", "name": "Shows the page live", "rubric": "The link opens the page", "pipeline_id": None}
ATTACHMENT = {"id": "att-1", "source": "link", "digest": "d", "url": "https://x.example", "file_version": 0}


def _settings(path):
    return Settings(db_path=path, registry_dir=FIXTURES / "registry")


def _engine(p, *, reason="ok"):
    def ask(model, state, questions, label):
        return {"p": p, "reason": reason, "provider": "t", "model": model}

    return ask


def test_the_bands_decide_met_not_met_or_unsure(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)

    def verdict(p):
        return P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=settings, engine=_engine(p))["verdict"]

    assert verdict(0.8) == "met" and verdict(0.95) == "met"
    assert verdict(0.2) == "not_met" and verdict(0.01) == "not_met"
    assert verdict(0.5) == "unsure" and verdict(0.79) == "unsure" and verdict(0.21) == "unsure"
    assert verdict(float("nan")) == "unsure"  # an answer with no probability is unsure, not a pass


def test_a_failed_model_call_is_unsure_and_no_engine_means_unsure(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)

    def broken(model, state, questions, label):
        raise J.JudgeError("unavailable", "the model did not answer")

    assert P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=settings, engine=broken)["verdict"] == "unsure"
    assert P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=settings, engine=None)["verdict"] == "unsure"


def test_the_spend_cap_stops_the_call_and_the_answer_is_unsure(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    called = []
    monkeypatch.setattr(backend_judge, "over_spend_cap", lambda conn, settings, *a, **k: (12.0, 10.0))

    def ask(model, state, questions, label):
        called.append(model)
        return {"p": 1.0, "reason": "ok"}

    result = P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=_settings(path), engine=ask)

    assert result["verdict"] == "unsure" and called == []


def test_a_pipeline_with_no_choice_is_judged_by_haiku_and_an_unknown_model_is_refused(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    assert P.judge_model_of(c, None) == "haiku-5.5"
    assert P.judge_model_of(c, "no-such-pipeline") == "haiku-5.5"


def test_a_bot_reviewed_attachment_is_judged_once_and_the_decision_is_recorded(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "The link opens the page",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()

    store = Store(_settings(path))
    asked = []

    def ask(model, state, questions, label):
        asked.append(state["requirement"])
        return {"p": 0.95, "reason": "The page opens", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 1
    assert P.tick(store) == 0  # the attachment is answered; the next pass has nothing to judge
    assert [r["name"] for r in asked] == ["Live link"]
    with store.read() as conn:
        rows = [dict(r) for r in conn.execute("SELECT judge, verdict, judge_call_id FROM stage_decisions").fetchall()]
    assert rows == [{"judge": "llm", "verdict": "met", "judge_call_id": rows[0]["judge_call_id"]}]
    assert rows[0]["judge_call_id"]  # the decision points at its audit event


def test_the_model_is_never_called_while_a_write_transaction_is_open(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "Opens",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()

    store = Store(_settings(path))
    real_transaction = store.transaction
    in_call = {"on": False}

    def guarded(*args, **kwargs):
        assert not in_call["on"], "a write transaction opened while the model was being asked"
        return real_transaction(*args, **kwargs)

    def ask(model, state, questions, label):
        in_call["on"] = True
        try:
            return {"p": 0.95, "reason": "ok", "provider": "t", "model": model}
        finally:
            in_call["on"] = False

    monkeypatch.setattr(store, "transaction", guarded)
    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 1


def test_a_bot_then_person_requirement_sends_an_unsure_answer_to_its_person(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot_then_person",
            "reviewer_id": BEN,
            "rubric": "Opens",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()

    store = Store(_settings(path))
    monkeypatch.setattr(P, "ENGINE", _engine(0.5))
    assert P.tick(store) == 1
    with store.read() as conn:
        row = dict(conn.execute("SELECT verdict, route_next_to FROM stage_decisions").fetchone())
    assert row == {"verdict": "unsure", "route_next_to": BEN}
    assert P.tick(store) == 0  # routed to a person: the bot does not judge it again
