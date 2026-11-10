"""`POST /api/v2/judge` (`backend/judge.py`): the judge as a hub tool, audited, budgeted, never the state."""

import json

from backend import judge as B
from backend.tests.helpers.api import get, headers, post, setup_attempt  # noqa: F401
from backend.tests.helpers.mcp import call
from backend.tests.helpers.judge import FakeJudge, QUESTIONS, events, fake


def test_a_person_or_a_bot_judges_and_the_audit_keeps_the_answers_not_the_state(api):
    engine = fake(api)
    state = {"subject": "Invoice 4471", "snippet": "the secret body of the mail"}
    out = post(api, "judge", {"state": state, "questions": QUESTIONS, "label": "mail-triage@1"})
    assert out["answers"]["bucket"]["choice"] == "reply" and out["label"] == "mail-triage@1"
    assert out["model"] == "judge-1.13.0" and out["ms"] == 180
    assert engine.calls[-1] == {"state": state, "questions": QUESTIONS, "label": "mail-triage@1"}
    ((target, detail),) = events(api, "human:ana")
    assert target == "mail-triage@1"
    assert detail["answers"] == {
        "bucket": {"type": "choice", "value": "reply", "confidence": 0.8},
        "is_ask": {"type": "noul", "value": 0.9, "confidence": 0.8},
    }
    assert detail["usage"]["input_tokens"] == 90 and detail["questions"] == 2
    assert "secret" not in json.dumps(detail)

    r, msg, attempt = setup_attempt(api)
    err, out = call(api, "tico_decision_ask", {"state": state, "questions": QUESTIONS}, token=attempt["token"])
    assert not err and out["answers"]["is_ask"]["noul"] == 0.9
    assert events(api, "bot:ops")[0][0] == ""


def test_rehearsal_decisions_never_reach_a_configured_provider(api):
    api.app.state.store.settings.rehearsal = True
    engine = fake(api)
    for configured in (engine, None):
        api.app.state.judge = configured
        assert get(api, "judge")["configured"] is False
        result = api.post(
            "/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers()
        )
        assert result.status_code == 503 and result.json()["error"]["code"] == "rehearsal"
    assert engine.calls == [] and events(api, "human:ana") == []


def test_a_demo_never_asks_a_model_with_its_made_up_keys(api):
    store = api.app.state.store
    store.settings.demo = True
    with store.transaction() as c:
        assert B.server_engine(store, c, fake(api)) is None


def test_a_slow_or_down_jev_is_a_quick_honest_503_and_no_other_model_answers(api):
    from clients import judge as J

    jev = fake(api)
    jev.fail = J.JudgeError("unavailable", "TypeSafe did not answer: TimeoutError", retryable=True)
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 503 and result.json()["error"]["code"] == "judge_unavailable"
    # TypeSafe refusing a request that passed the contract here is TypeSafe's problem: Decide is off for the call.
    jev.fail = J.JudgeError("invalid", "TypeSafe refused the request", 422)
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 503 and result.json()["error"]["code"] == "judge_unavailable"
    assert len(jev.calls) == 2 and [d["error"] for _, d in events(api, "human:ana")] == ["unavailable", "invalid"]


def test_the_server_asks_typesafe_once_and_briefly():
    from clients import judge as J

    seen = []

    def opener(request, timeout):
        seen.append(timeout)
        raise TimeoutError()

    engine = J.direct("key", timeout=B.JEV_TIMEOUT, retries=0, opener=opener, sleep=lambda s: None)
    try:
        engine({"team": "Acme"}, QUESTIONS)
    except J.JudgeError as exc:
        assert exc.retryable
    assert seen == [B.JEV_TIMEOUT] and B.JEV_TIMEOUT <= 10


def test_the_gateway_is_asked_once_then_typesafe_and_nothing_after():
    from clients import judge as J

    seen = []

    def gateway(state, questions, label=None):
        seen.append("gateway")
        raise J.JudgeError("unavailable", "Gateway down", retryable=True)

    def typesafe(state, questions, label=None):
        seen.append("typesafe")
        return {"answers": {}}

    gateway.model, typesafe.model = J.GATEWAY_MODEL, J.MODEL
    chain = B.ordered([gateway, typesafe])
    assert chain({"team": "Acme"}, QUESTIONS)["fallback"] == "unavailable"
    assert seen == ["gateway", "typesafe"] and B.step_models(chain.steps) == [J.GATEWAY_MODEL, J.MODEL]


def test_the_daily_cap_is_a_setting_per_role(api):
    engine = fake(api)
    api.app.state.store.settings.decisions_daily_calls_owner = 1
    assert get(api, "judge")["daily_calls"] == 1
    post(api, "decisions", {"state": {"team": "Acme"}, "questions": QUESTIONS})
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 429 and result.json()["error"]["code"] == "budget" and len(engine.calls) == 1


def test_from_env_reads_the_daily_caps(monkeypatch, tmp_path):
    from backend.config import Settings

    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("TICO_DECISIONS_DAILY_CALLS_BOT", "250")
    settings = Settings.from_env()
    assert (settings.decisions_daily_calls_bot, settings.decisions_daily_calls_human) == (250, B.DAILY_CALLS["human"])
    assert B.daily_cap(settings, "bot") == 250


def test_a_caller_records_one_outcome_for_its_own_decision_call(api):
    fake(api)
    r, msg, attempt = setup_attempt(api)
    err, asked = call(
        api,
        "tico_decision_ask",
        {"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS, "label": "mail-triage@1"},
        token=attempt["token"],
    )
    assert not err and asked["ref"]
    said = {"ref": asked["ref"], "verdict": "reply 0.8", "action": "replied"}
    err, first = call(api, "tico_decision_outcome", said, token=attempt["token"])
    assert not err and first["recorded"] and first["label"] == "mail-triage@1"
    err, again = call(api, "tico_decision_outcome", {**said, "action": "archived"}, token=attempt["token"])
    assert not err and not again["recorded"] and again["id"] == first["id"] and again["action"] == "replied"
    # Another caller cannot write an outcome onto the bot's call.
    assert api.post("/api/v2/decisions/outcome", json=said, headers=headers()).status_code == 404
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, target, detail FROM events WHERE action='decision.outcome'").fetchall()
    assert [(a, t, json.loads(d)) for a, t, d in rows] == [
        ("bot:ops", asked["ref"], {"label": "mail-triage@1", "verdict": "reply 0.8", "action": "replied"})
    ]
