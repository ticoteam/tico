"""Decide on Jev for the team: each call's cost in the audit, the monthly spend cap, and one engine for every server
caller (Jev's one short try, the company's model behind it, and "carry on as if Decide were off" at the cap)."""

import json

import pytest

from backend import hubdb as H
from backend import judge as B
from backend.config import Settings
from backend.tests.test_api import api, get, headers, post   # noqa: F401
from backend.tests.test_judge import QUESTIONS, FakeJudge, events, fake
from backend.tests.test_slack_gateway import gateway, hub   # noqa: F401
from clients import judge as J

DOWN = J.JudgeError("unavailable", "TypeSafe did not answer: TimeoutError", retryable=True)


def spend(api, usd, ts=None, actor="bot:ops"):
    """A past decision call that cost `usd`, this month unless `ts` says otherwise."""
    with api.app.state.store.transaction() as c:
        H.event(c, actor, "judge.call", "mail-triage@1", {"cost_usd": usd})
        if ts:
            c.execute("UPDATE events SET ts=? WHERE id=(SELECT id FROM events WHERE action='judge.call' "
                      "ORDER BY rowid DESC LIMIT 1)", (ts,))


def priced(settings, rate_in=0.5, rate_out=2.0):
    settings.decisions_jev_usd_per_mtok_input, settings.decisions_jev_usd_per_mtok_output = rate_in, rate_out
    return settings


# ----------------------------------------------------------------------------- cost
def test_a_calls_cost_is_the_providers_own_figure_else_its_tokens_at_list_price(tmp_path):
    settings = Settings(db_path=tmp_path / "hub.db")
    # OpenRouter (and TypeSafe, if it sends one) says what the call cost: that wins.
    assert B.cost_usd(settings, "jev-1.14", {"input_tokens": 9_000_000, "cost": 0.0123}) == 0.0123
    # Jev without a price is unknown: tokens kept, nothing counted.
    assert B.cost_usd(settings, "jev-1.14", {"input_tokens": 1000, "output_tokens": 10}) is None
    priced(settings)
    assert B.cost_usd(settings, "jev-1.14", {"input_tokens": 2_000_000, "output_tokens": 500_000}) == 2.0
    # The company's model at providers.PRICES; OpenAI-style counts include the cached tokens.
    assert B.cost_usd(settings, "claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000}) == 3.0
    assert B.tokens({"prompt_tokens": 1000, "completion_tokens": 7,
                     "prompt_tokens_details": {"cached_tokens": 400}}) == (600, 400, 7)
    assert B.cost_usd(settings, "a-model-with-no-price", {"input_tokens": 10}) is None


def test_the_audit_keeps_model_tokens_latency_and_cost_and_says_when_the_fallback_answered(api, monkeypatch):
    priced(api.app.state.store.settings)
    jev = fake(api)
    jev_result = jev.__call__

    def as_jev(state, questions, label=None):
        return {**jev_result(state, questions, label), "model": "jev-1.14",
                "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
    api.app.state.judge = as_jev
    out = post(api, "decisions", {"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": "mail-triage@1"})
    assert set(out) == {"model", "answers", "usage", "ms", "label"}, "the reply's shape is unchanged"
    detail = events(api, "human:ana")[-1][1]
    assert (detail["model"], detail["usage"]["input_tokens"], detail["ms"], detail["cost_usd"]) == \
        ("jev-1.14", 1_000_000, 180, 0.5)
    assert "fallback" not in detail

    backup = FakeJudge()
    jev.fail = DOWN
    api.app.state.judge = jev
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    post(api, "decisions", {"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": "mail-triage@1"})
    detail = events(api, "human:ana")[-1][1]
    assert detail["fallback"] == "unavailable" and detail["model"] == "judge-1.13.0" and "cost_usd" in detail


# ----------------------------------------------------------------------------- the monthly cap
def test_at_the_monthly_cap_a_call_is_refused_before_any_model_is_asked(api, monkeypatch):
    engine = fake(api)
    backup = FakeJudge()
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    api.app.state.store.settings.decisions_monthly_usd = 50.0
    spend(api, 60.0, ts="2000-01-15T00:00:00Z")            # an older month never counts
    spend(api, 30.0)
    spend(api, 19.99, actor="human:ben")                     # every actor's calls count: the cap is the team's
    post(api, "decisions", {"state": {"team": "Acme"}, "questions": QUESTIONS})
    assert len(engine.calls) == 1
    shown = get(api, "judge")
    assert shown["monthly_usd"] == 50.0 and shown["spent_this_month_usd"] == pytest.approx(49.99)

    spend(api, 0.01)
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 429 and result.json()["error"]["code"] == "spend_cap"
    assert len(engine.calls) == 1 and backup.calls == [], "neither Jev nor the fallback is asked at the cap"

    # The callers that ask through the hub (mail triage, "already covered?") carry on as if Decide were off.
    from connectors.mail import judge as MJ

    from clients.tico import APIError

    class Client:
        def post(self, path, body):
            response = api.post("/api/v2/" + path, json=body, headers=headers())
            if response.status_code != 200:
                error = response.json()["error"]
                raise APIError(error["code"], error.get("detail") or error.get("message") or "", response.status_code)
            return response.json()
    chosen = J.load_set("mail-triage")
    judged = MJ.judge_message({"id": "m1", "subject": "Invoice", "from": "a@b.example"}, chosen, J.through_hub(Client()))
    assert judged["error"] == "spend_cap"

    # A cap of 0 is no cap.
    api.app.state.store.settings.decisions_monthly_usd = 0
    post(api, "decisions", {"state": {"team": "Acme"}, "questions": QUESTIONS})
    assert len(engine.calls) == 2


def test_from_env_reads_the_monthly_cap_and_jevs_price(monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    settings = Settings.from_env()
    assert settings.decisions_monthly_usd == 50.0 and settings.decisions_jev_usd_per_mtok_input == 0
    monkeypatch.setenv("TICO_DECISIONS_MONTHLY_USD", "12.5")
    monkeypatch.setenv("TICO_DECISIONS_JEV_USD_PER_MTOK_INPUT", "0.4")
    monkeypatch.setenv("TICO_DECISIONS_JEV_USD_PER_MTOK_OUTPUT", "1.6")
    settings = Settings.from_env()
    assert (settings.decisions_monthly_usd, settings.decisions_jev_usd_per_mtok_input,
            settings.decisions_jev_usd_per_mtok_output) == (12.5, 0.4, 1.6)


# ----------------------------------------------------------------------------- one engine for every caller
def test_the_server_engine_falls_back_when_jev_is_down_and_is_off_at_the_cap(api, monkeypatch):
    store = api.app.state.store
    jev, backup = FakeJudge(), FakeJudge()
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    with store.read() as c:
        engine = B.server_engine(c, store.settings, jev)
    assert engine.model == J.MODEL
    assert "cost_usd" in engine({"team": "Acme"}, QUESTIONS) and len(jev.calls) == 1 and backup.calls == []
    jev.fail = DOWN
    assert engine({"team": "Acme"}, QUESTIONS)["fallback"] == "unavailable" and len(backup.calls) == 1
    jev.fail = J.JudgeError("invalid", "TypeSafe refused the request", 422)
    with pytest.raises(J.JudgeError):
        engine({"team": "Acme"}, QUESTIONS)                  # a refused request is not retried elsewhere
    assert len(backup.calls) == 1
    # No key: the company's model alone. Neither: off.
    with store.read() as c:
        assert B.server_engine(c, store.settings, None)({"team": "Acme"}, QUESTIONS)["model"] == "judge-1.13.0"
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: None)
    with store.read() as c:
        assert B.server_engine(c, store.settings, None) is None
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    with store.read() as c:
        assert B.server_engine(c, store.settings, jev) is None


def test_the_nightly_learnings_ask_jev_once_and_briefly_with_the_fallback_behind_and_tally_the_cost(api, monkeypatch):
    from backend import learnings as L
    store = api.app.state.store
    seen = []

    def direct(key, timeout=None, retries=None, **kwargs):
        seen.append((key, timeout, retries))
        return FakeJudge()
    monkeypatch.setattr(B.J, "direct", direct)
    store.settings.typesafe_api_key = "synthetic"
    with store.read() as c:
        engine = L.engine_for(c, store.settings)
    assert seen == [("synthetic", B.JEV_TIMEOUT, 0)], "not the direct client's 20 s and two retries"
    tally = L.Tally(engine)
    tally({"team": "Acme"}, QUESTIONS)
    tally({"team": "Acme"}, QUESTIONS)
    assert tally.detail() == {"calls": 2, "fallbacks": 0, "models": {"judge-1.13.0": 2}, "model": "judge-1.13.0",
                              "usage": {"input_tokens": 180, "output_tokens": 0}, "cost_usd": 0.0}
    store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    with store.read() as c:
        assert L.engine_for(c, store.settings) is None


def test_the_listening_judge_gets_the_fallback_when_jev_is_down_and_waits_at_the_cap(api, monkeypatch):
    from backend.tests.test_listening import FakeJudge as Listener, _bots, _post, _token
    _bots(api)
    listening = _token(api, "listening")
    jev, backup = FakeJudge(), Listener()
    jev.fail = DOWN
    api.app.state.judge = jev
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post("1", "Creator asks how teams handle standups [creator] [content]")]}, token=listening)
    out = post(api, "listening/judge", {}, token=listening)
    assert out["judged"] == 1 and out["errors"] == [] and len(jev.calls) == 1
    detail = events(api, "bot:listening")[-1][1]
    assert detail["fallback"] == "unavailable" and "cost_usd" in detail

    api.app.state.store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post("2", "Another creator post [creator]")]}, token=listening)
    result = api.post("/api/v2/listening/judge", json={}, headers=headers(listening))
    assert result.status_code == 429 and result.json()["error"]["code"] == "spend_cap" and len(jev.calls) == 1


def test_the_slack_gateway_routes_to_the_assistant_at_the_cap_and_audits_the_cost(gateway, hub):
    from backend.tests import test_slack_gateway as S
    first = S.send(gateway, "please review the vendor terms", judge_answer=S.answers(legal=0.9), channel=S.DM,
                   kind="message", user="U2")
    assert first["state"] == "routed"
    audit = S.rows(hub, "SELECT detail_json FROM events WHERE action='judge.call'")
    assert json.loads(audit[-1]["detail_json"])["cost_usd"] == 0.0     # the fake model has no list price
    hub.settings.decisions_monthly_usd = 1.0
    with hub.transaction() as c:
        H.event(c, "bot:ops", "judge.call", "mail-triage@1", {"cost_usd": 1.0})
    calls = len(gateway.engine.calls)
    second = S.send(gateway, "and the NDA too", channel=S.DM, kind="message", user="U2")
    assert len(gateway.engine.calls) == calls, "at the cap the decision model is not asked"
    assert second["decision"]["routed_by"] == "spend_cap" and "monthly decision spend" in second["decision"]["reason"]
    assert [r["bot"] for r in second["decision"]["recipients"]] == ["coo"]


def test_a_live_meeting_passes_at_the_cap_and_its_audit_keeps_the_tokens_and_cost(api, monkeypatch):
    from backend import live_meetings as LM

    class Room:
        def __call__(self, state, questions, label=None):
            return {"model": "jev-1.14", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}, "ms": 90,
                    "answers": {qid: {"type": "noul", "noul": 0.9} for qid in questions}}
    priced(api.app.state.store.settings)
    api.app.state.judge = Room()
    bots = [{"slug": "ops", "name": "Ops"}]
    routed = LM._decision(api.app, {"source": {"kind": "chat"}}, bots, 0.5, "human:ana")
    assert routed["targets"] == ["ops"]
    assert routed["trace"]["audit"] == {"ms": 90, "model": "jev-1.14", "usage": {"input_tokens": 1_000_000,
                                                                                  "output_tokens": 0}, "cost_usd": 0.5}
    api.app.state.store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    capped = LM._decision(api.app, {"source": {"kind": "chat"}}, bots, 0.5, "human:ana")
    assert capped["outcome"] == "pass" and capped["trace"]["reason"] == "monthly_decision_spend_cap"
