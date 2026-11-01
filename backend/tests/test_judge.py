"""`POST /api/v2/judge` (`backend/judge.py`): the judge as a hub tool, audited, budgeted, never the state."""

import json

from backend import judge as B
from backend.tests.test_api import api, get, headers, post, setup_attempt   # noqa: F401
from backend.tests.test_mcp import call

QUESTIONS = {
    "bucket": {"type": "choice", "instructions": "Where does it go?",
               "criteria": {"archive": "noise", "reply": "answer it"}},
    "is_ask": {"type": "noul", "instructions": "Is someone asking for something?"},
}


class FakeJudge:
    def __init__(self):
        self.calls, self.fail = [], None

    def __call__(self, state, questions, label=None):
        self.calls.append({"state": state, "questions": questions, "label": label})
        if self.fail:
            raise self.fail
        return {"model": "judge-1.13.0", "usage": {"input_tokens": 90, "output_tokens": 0}, "ms": 180,
                "answers": {"bucket": {"type": "choice", "choice": "reply", "confidence": 0.8,
                                       "probabilities": {"archive": 0.2, "reply": 0.8}},
                            "is_ask": {"type": "noul", "noul": 0.9}}}


def fake(api):
    engine = FakeJudge()
    api.app.state.judge = engine
    return engine


def events(api, actor):
    with api.app.state.store.read() as c:
        return [(r["target"], json.loads(r["detail"])) for r in c.execute(
            "SELECT target, detail FROM events WHERE action='judge.call' AND actor=? ORDER BY created_at", (actor,))]


def test_a_person_or_a_bot_judges_and_the_audit_keeps_the_answers_not_the_state(api):
    engine = fake(api)
    state = {"subject": "Invoice 4471", "snippet": "the secret body of the mail"}
    out = post(api, "judge", {"state": state, "questions": QUESTIONS, "label": "mail-triage@1"})
    assert out["answers"]["bucket"]["choice"] == "reply" and out["label"] == "mail-triage@1"
    assert out["model"] == "judge-1.13.0" and out["ms"] == 180
    assert engine.calls[-1] == {"state": state, "questions": QUESTIONS, "label": "mail-triage@1"}
    (target, detail), = events(api, "human:ana")
    assert target == "mail-triage@1"
    assert detail["answers"] == {"bucket": {"type": "choice", "value": "reply", "confidence": 0.8},
                                 "is_ask": {"type": "noul", "value": 0.9, "confidence": 0.8}}
    assert detail["usage"]["input_tokens"] == 90 and detail["questions"] == 2
    assert "secret" not in json.dumps(detail)

    r, msg, attempt = setup_attempt(api)
    err, out = call(api, "hub_decision_ask", {"state": state, "questions": QUESTIONS}, token=attempt["token"])
    assert not err and out["answers"]["is_ask"]["noul"] == 0.9
    assert events(api, "bot:ops")[0][0] == ""


def test_rehearsal_decisions_never_reach_a_configured_or_fallback_provider(api, monkeypatch):
    api.app.state.store.settings.rehearsal = True
    engine = fake(api)
    def forbidden(*args, **kwargs):
        raise AssertionError("A rehearsal must not resolve a provider")
    monkeypatch.setattr(B, "fallback_engine", forbidden)
    for configured in (engine, None):
        api.app.state.judge = configured
        assert get(api, "judge")["configured"] is False
        result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
        assert result.status_code == 503 and result.json()["error"]["code"] == "rehearsal"
    assert engine.calls == [] and events(api, "human:ana") == []


def test_a_slow_or_down_typesafe_falls_back_to_the_companys_model_instead_of_a_503(api, monkeypatch):
    from clients import judge as J
    jev, backup = fake(api), FakeJudge()
    jev.fail = J.JudgeError("unavailable", "TypeSafe did not answer: TimeoutError", retryable=True)
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    out = post(api, "decisions", {"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS, "label": "mail-triage@1"})
    assert out["answers"]["bucket"]["choice"] == "reply" and len(jev.calls) == 1 and len(backup.calls) == 1
    assert events(api, "human:ana")[-1][1]["fallback"] == "unavailable"
    # With nothing to fall back to, the caller still gets a quick, honest 503; TypeSafe refusing a request that passed
    # the contract here is TypeSafe's problem, so the company's model answers it.
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: None)
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 503 and result.json()["error"]["code"] == "judge_unavailable"
    jev.fail = J.JudgeError("invalid", "TypeSafe refused the request", 422)
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 200 and len(backup.calls) == 2
    assert events(api, "human:ana")[-1][1]["fallback"] == "invalid"


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


def test_a_callers_level_reaches_the_steps_that_take_one_and_prices_their_model():
    from clients import judge as J
    seen = []

    def gateway(state, questions, label=None, level=None):
        seen.append(("gateway", level))
        raise J.JudgeError("down", "Gateway down", retryable=True)
    gateway.current_model, gateway.level = (lambda: J.decide_model("low")), (lambda: "low")

    def company(state, questions, label=None):   # takes no level: asked as before
        seen.append(("company", None))
        return {"answers": {}}

    chain = B.ordered([gateway, company])
    chain({"team": "Acme"}, QUESTIONS, level="high")
    assert seen == [("gateway", "high"), ("company", None)]
    assert B.step_models([gateway, company], level="high")[0] == J.decide_model("high")
    assert B.step_models([gateway, company])[0] == J.decide_model("low")


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


class Captured:
    """A provider's HTTP API as the fallback sees it: remembers each request, answers with probabilities."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout=None):
        import io
        self.requests.append({"url": request.full_url, "body": json.loads(request.data)})
        text = json.dumps({"bucket": {"probabilities": {"archive": 0.2, "reply": 0.8}}, "is_ask": {"noul": 0.9}})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


def test_the_openrouter_fallback_sends_the_openrouter_model_id_and_skips_providers_without_a_key(monkeypatch, caplog):
    opener = Captured()
    monkeypatch.setattr("clients.judge.urlopen", opener)
    pi = {"runtime": "pi", "model": "deepseek-v4.1-flash", "enabled": ["openrouter"]}
    engine = B.fallback_engine(pi, env={"OPENROUTER_API_KEY": "synthetic"})
    engine({"team": "Acme"}, QUESTIONS)
    assert opener.requests[-1]["url"].startswith("https://openrouter.ai/")
    assert opener.requests[-1]["body"]["model"] == "deepseek/deepseek-v4.1-flash"
    # Cursor has no API the server can call: the next enabled provider with a key answers, and the log says why.
    cursor = {"runtime": "cursor", "model": "cursor-auto", "enabled": ["cursor", "moonshot"]}
    with caplog.at_level("INFO", logger="tico.judge"):
        engine = B.fallback_engine(cursor, env={"OPENROUTER_API_KEY": "synthetic"})
    assert engine is not None and "cursor" in caplog.text
    engine({"team": "Acme"}, QUESTIONS)
    assert opener.requests[-1]["body"]["model"] == "moonshotai/kimi-k3"
    assert B.fallback_engine(cursor, env={}) is None


def test_the_draft_writer_sends_only_its_two_texts_to_the_companys_model():
    """A person's update draft (backend/updates.write_draft): one system prompt and one user turn, no tools."""
    from clients import judge as J
    opener = Captured()
    via, key, model = B.fallback_model({"runtime": "pi", "model": "deepseek-v4.1-flash", "enabled": ["openrouter"]},
                                       env={"OPENROUTER_API_KEY": "synthetic"})
    write = J.llm_writer(via, key, model, opener=opener)
    assert write("Draft it.", "Ana's facts")["bucket"]
    body = opener.requests[-1]["body"]
    assert [m["content"] for m in body["messages"]] == ["Draft it.", "Ana's facts"] and "tools" not in body


def test_a_caller_records_one_outcome_for_its_own_decision_call(api):
    fake(api)
    r, msg, attempt = setup_attempt(api)
    err, asked = call(api, "hub_decision_ask", {"state": {"subject": "Invoice 4471"}, "questions": QUESTIONS,
                                               "label": "mail-triage@1"}, token=attempt["token"])
    assert not err and asked["ref"]
    said = {"ref": asked["ref"], "verdict": "reply 0.8", "action": "replied"}
    err, first = call(api, "hub_decision_outcome", said, token=attempt["token"])
    assert not err and first["recorded"] and first["label"] == "mail-triage@1"
    err, again = call(api, "hub_decision_outcome", {**said, "action": "archived"}, token=attempt["token"])
    assert not err and not again["recorded"] and again["id"] == first["id"] and again["action"] == "replied"
    # Another caller cannot write an outcome onto the bot's call.
    assert api.post("/api/v2/decisions/outcome", json=said, headers=headers()).status_code == 404
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, target, detail FROM events WHERE action='decision.outcome'").fetchall()
    assert [(a, t, json.loads(d)) for a, t, d in rows] == [
        ("bot:ops", asked["ref"], {"label": "mail-triage@1", "verdict": "reply 0.8", "action": "replied"})]
