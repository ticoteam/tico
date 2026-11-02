"""Decide on Jev for the team: each call's cost in the audit, the monthly spend cap, and one engine for every server
caller (Jev's one short try, the company's model behind it, and "carry on as if Decide were off" at the cap)."""

import json

import pytest

from backend import hubdb as H
from backend import judge as B
from backend.config import Settings
from backend.store import Store
from backend.tests.test_api import api, get, headers, post   # noqa: F401
from backend.tests.test_judge import QUESTIONS, FakeJudge, events, fake
from backend.tests.test_slack_gateway import gateway, hub   # noqa: F401
from clients import judge as J

DOWN = J.JudgeError("unavailable", "TypeSafe did not answer: TimeoutError", retryable=True)


def spend(api, usd, ts=None, actor="bot:ops"):
    """A past decision call that cost `usd`, this month unless `ts` says otherwise."""
    with api.app.state.store.transaction() as c:
        H.event(c, actor, "judge.spend", "mail-triage@1", {"cost_usd": usd})
        if ts:
            c.execute("UPDATE events SET ts=? WHERE id=(SELECT id FROM events WHERE action='judge.spend' "
                      "ORDER BY rowid DESC LIMIT 1)", (ts,))


def priced(settings, rate_in=0.5, rate_out=2.0):
    settings.decisions_jev_usd_per_mtok_input, settings.decisions_jev_usd_per_mtok_output = rate_in, rate_out
    return settings


# ----------------------------------------------------------------------------- cost
def test_a_calls_cost_is_the_providers_own_figure_else_its_tokens_at_list_price(tmp_path):
    settings = Settings(db_path=tmp_path / "hub.db")
    # OpenRouter (and TypeSafe, if it sends one) says what the call cost: that wins.
    assert B.cost_usd(settings, "jev-1.14", {"input_tokens": 9_000_000, "cost": 0.0123}) == 0.0123
    # Jev at TypeSafe's published price: input $0.042 per 1M tokens, output free.
    assert B.cost_usd(settings, "jev-1.14", {"input_tokens": 1_000_000, "output_tokens": 50_000}) == 0.042
    # A model with no known price never counts $0: its tokens are counted at the dearest list price.
    assert B.CEILING == (10.0, 50.0)
    assert B.cost_usd(priced(settings, 0, 0), "jev-1.14", {"input_tokens": 1_000_000, "output_tokens": 0}) == 10.0
    priced(settings)
    assert B.cost_usd(settings, "jev-1.14", {"input_tokens": 2_000_000, "output_tokens": 500_000}) == 2.0
    # The company's model at providers.PRICES; OpenAI-style counts include the cached tokens.
    assert B.cost_usd(settings, "claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000}) == 3.0
    assert B.tokens({"prompt_tokens": 1000, "completion_tokens": 7,
                     "prompt_tokens_details": {"cached_tokens": 400}}) == (600, 400, 7)
    assert B.cost_usd(settings, "a-model-with-no-price", {"output_tokens": 1_000_000}) == 50.0


def test_the_audit_keeps_model_tokens_latency_and_cost_and_says_when_the_fallback_answered(api, monkeypatch):
    priced(api.app.state.store.settings)
    jev = fake(api)
    jev_result = jev.__call__

    def as_jev(state, questions, label=None):
        return {**jev_result(state, questions, label), "model": "jev-1.14",
                "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}
    api.app.state.judge = as_jev
    out = post(api, "decisions", {"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": "mail-triage@1"})
    assert set(out) == {"model", "answers", "usage", "ms", "label", "ref"}, "the reply's shape is unchanged but for its ref"
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
    spend(api, 19.7, actor="human:ben")                      # every actor's calls count: the cap is the team's
    post(api, "decisions", {"state": {"team": "Acme"}, "questions": QUESTIONS})   # its most (~$0.21) fits
    assert len(engine.calls) == 1
    shown = get(api, "judge")
    assert shown["monthly_usd"] == 50.0 and shown["spent_this_month_usd"] == pytest.approx(49.7, abs=0.001)

    spend(api, 0.2)                                          # under the cap, but a call's most no longer fits
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
    assert (settings.decisions_monthly_usd, settings.decisions_jev_usd_per_mtok_input,
            settings.decisions_jev_usd_per_mtok_output) == (50.0, 0.042, 0)
    monkeypatch.setenv("TICO_DECISIONS_MONTHLY_USD", "12.5")
    monkeypatch.setenv("TICO_DECISIONS_JEV_USD_PER_MTOK_INPUT", "0.4")
    monkeypatch.setenv("TICO_DECISIONS_JEV_USD_PER_MTOK_OUTPUT", "1.6")
    settings = Settings.from_env()
    assert (settings.decisions_monthly_usd, settings.decisions_jev_usd_per_mtok_input,
            settings.decisions_jev_usd_per_mtok_output) == (12.5, 0.4, 1.6)


class Paid:
    """A model whose every answer says it cost `usd`."""

    def __init__(self, usd, inner=None):
        self.usd, self.inner, self.calls = usd, inner or FakeJudge(), []

    def __call__(self, state, questions, label=None):
        self.calls.append(questions)
        return {**self.inner(state, questions, label), "usage": {"input_tokens": 10, "cost": self.usd}}


def test_a_batch_stops_calling_once_its_own_answers_reach_the_cap(api, monkeypatch):
    """One engine serves a whole batch, so the cap is counted call by call, not only when the engine is made."""
    from backend import learnings as L
    from backend.listening import judge_pending
    from backend.tests.test_listening import FakeJudge as Listener, _bots, _post, _token
    store = api.app.state.store
    store.settings.decisions_monthly_usd = 1.0
    spend(api, 0.5)
    jev = Paid(0.6)                                    # the first answer takes the month past $1
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: None)
    with store.read() as c:
        engine = B.server_engine(store, c, jev)
    engine({"team": "Acme"}, QUESTIONS)
    with pytest.raises(J.JudgeError) as refused:
        engine({"team": "Acme"}, QUESTIONS)
    assert refused.value.code == "spend_cap" and len(jev.calls) == 1, "no paid call starts over the cap"

    # Listening: the first post is judged, the rest stay unjudged for after the cap.
    _bots(api)
    listening = _token(api, "listening")
    post(api, "listening/runs", {"source": "x", "query": "q", "status": "ok", "items": [
        _post(str(n), f"Creator post {n} [creator]") for n in range(3)]}, token=listening)
    api.app.state.judge = jev = Paid(0.6, Listener())
    spend(api, -0.6)                                   # back to $0.50, so the batch starts
    out = post(api, "listening/judge", {}, token=listening)
    assert out["judged"] == 1 and [e["error"] for e in out["errors"]] == ["spend_cap"] and len(jev.calls) == 1
    with store.read() as c:
        assert B.spent_this_month(c) == pytest.approx(1.1)

    # Learnings: a night that reaches the cap sends nothing and keeps its cursors, so nothing read is lost.
    items = [{"key": f"chat:{n}", "source": "chat", "created_at": H.now(), "audience": None, "label": "Chat",
              "text": f"Invoices go out on Friday {n}.", "author": "human:ana", "ref": {}} for n in range(3)]
    team = {"ops": {"profile": "Ops runs invoicing."}}
    spend(api, -0.6)                                   # back to $0.50, so the night starts
    night = Paid(0.6, lambda state, questions, label=None: {
        "model": "judge-1.13.0", "ms": 5, "answers": {qid: {"type": "noul", "noul": 0.9} for qid in questions}})
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: night)
    monkeypatch.setattr(L, "WORKERS", 1)
    monkeypatch.setattr(L, "roster", lambda *args, **kwargs: team)
    monkeypatch.setattr(L, "collect", lambda *args, **kwargs: (items, 0))
    with store.transaction() as c:
        run_id = L.claim(c, "2026-10-12")
    L.run(store, run_id)
    with store.read() as c:
        row = c.execute("SELECT status,detail FROM learning_runs WHERE id=?", (run_id,)).fetchone()
        assert row["status"] == "capped" and json.loads(row["detail"])["failed"] == {"spend_cap": 2}
        assert c.execute("SELECT count(*) FROM learning_deliveries").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM learning_cursors").fetchone()[0] == 0, "the cursors did not move"
        audit = json.loads(c.execute("SELECT detail FROM events WHERE target=? ORDER BY rowid DESC LIMIT 1",
                                     (f"{L.QUESTION_SET}@run",)).fetchone()[0])
        assert audit["calls"] == 1 and audit["cost_usd"] == 0.6, "the call it did make is counted"
        assert audit["answers"] == [{"learnable": {"type": "noul", "value": 0.9, "confidence": 0.8},
                                     "rel_ops": {"type": "noul", "value": 0.9, "confidence": 0.8}}], "and what it answered"
    assert len(night.calls) == 1
    with store.transaction() as c:                     # a capped night may be run again
        c.execute("UPDATE learning_runs SET night=? WHERE id=?", (L.night_of(L.datetime.now(L.timezone.utc)), run_id))
    assert api.post("/api/v2/learnings/run", json={}, headers=headers()).status_code == 200


def test_a_call_in_flight_holds_its_most_against_the_cap_for_every_other_request(api, monkeypatch):
    """Two requests at once: the second sees what the first may cost while it is still out, not only what is written."""
    import threading
    store = api.app.state.store
    store.settings.decisions_monthly_usd = 1.0
    priced(store.settings, 0, 0)                       # Jev unpriced, so a call in flight holds a lot
    spend(api, 0.6)
    entered, release = threading.Event(), threading.Event()

    class Slow(Paid):
        def __call__(self, state, questions, label=None):
            entered.set()
            release.wait(5)
            return super().__call__(state, questions, label)
    slow = Slow(0.05)
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: None)
    with store.read() as c:
        first, second = B.server_engine(store, c, slow), B.server_engine(store, c, Paid(0.05))
    held = B.reservation(store.settings, [J.MODEL], {"team": "Acme"}, QUESTIONS)
    assert 0.2 < held < 0.4, "unpriced: held high, but one call still fits under the cap"
    out = {}
    thread = threading.Thread(target=lambda: out.update(first({"team": "Acme"}, QUESTIONS, "unpriced")))
    thread.start()
    assert entered.wait(5)
    slow.inner = lambda state, questions, label=None: {"model": "an-unpriced-model", "ms": 1, "answers": {}}
    with pytest.raises(J.JudgeError) as refused:       # $0.60 written + the first call's most in flight + its own
        second({"team": "Acme"}, QUESTIONS)
    assert refused.value.code == "spend_cap"
    release.set()
    thread.join(5)
    assert out["cost_usd"] == 0.05 and B._RESERVED[0] == 0
    second({"team": "Acme"}, QUESTIONS)                # $0.65 written, nothing in flight: it fits
    with store.read() as c:
        assert B.spent_this_month(c) == pytest.approx(0.7)
        rows = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='judge.spend' "
                                                    "AND target='unpriced'")]
    assert rows == [{"label": "unpriced", "model": "an-unpriced-model", "cost_usd": 0.05}], \
        "the provider's own cost wins; an unpriced model without one is counted at the ceiling"


@pytest.mark.parametrize("written", [0.009999, 0.0099])
def test_two_processes_admitted_at_once_pass_the_cap_by_at_most_the_larger_reservation(gateway, hub, monkeypatch,
                                                                                       written):
    """QA's repro on the v0.3.58 candidate: cap $0.01 and $0.009999 written; a server_engine call and a Slack
    Gateway.ask in the gateway's own process (its own meter) both checked before either answer is written; each
    answers 1000 tokens in, 0 out, on Jev. Each admits a call only if it fits with its own most, so the month ends
    at most the larger reservation over the cap. Before the fix both were let in at $0.009999: $0.010083 written."""
    from backend.tests import test_slack_gateway as S
    hub.settings.decisions_monthly_usd = 0.01
    with hub.transaction() as c:
        H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": written})
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: None)
    jev_usage = {"input_tokens": 1000, "output_tokens": 0}
    state, held, slack_answered = {"team": "Acme"}, [], []
    reserve = B.reservation
    monkeypatch.setattr(B, "reservation", lambda *args: held.append(reserve(*args)) or held[-1])

    class SlackJev(S.CappedJudge):
        def __call__(self, state, questions, label=None):
            slack_answered.append(questions)
            return {**super().__call__(state, questions, label), "model": J.MODEL, "usage": jev_usage}
    gateway._judge = SlackJev({"asks": 0.9, "bot:legal": 0.9})

    def slack_process():
        """The gateway's container: the same audit, its own in-flight reservations."""
        real = B._RESERVED
        monkeypatch.setattr(B, "_RESERVED", [0.0])
        try:
            return S.send(gateway, "please review the vendor terms", channel=S.DM, kind="message", user="U2")
        finally:
            monkeypatch.setattr(B, "_RESERVED", real)

    def server_jev(state, questions, label=None):
        out["slack"] = slack_process()                 # the Slack call is checked while this one is out
        return {"model": J.MODEL, "answers": FakeJudge()(state, questions, label)["answers"], "usage": jev_usage, "ms": 1}
    out = {}
    with hub.read() as c:
        engine = B.server_engine(hub, c, server_jev)
    try:
        engine(state, QUESTIONS, "server")
    except J.JudgeError as exc:
        assert exc.code == "spend_cap"
        out["slack"] = slack_process()                 # the server call never went out; Slack is checked alone
    with hub.read() as c:
        spent = B.spent_this_month(c)
    assert len(held) == 2 and max(held) < 0.0001       # Jev at $0.042 per 1M in: a few hundredths of a cent each
    assert spent <= 0.01 + max(held), f"${spent} written against a $0.01 cap"
    if written == 0.009999:                            # neither fits with its own most: nothing more is spent
        assert spent == pytest.approx(written) and slack_answered == []
        assert out["slack"]["decision"]["routed_by"] == "spend_cap"
    else:                                              # both fit: both answer, and the month stays under the cap
        assert len(slack_answered) == 1 and spent == pytest.approx(written + 2 * 0.000042) and spent <= 0.01
    assert B._RESERVED[0] == 0


def test_slack_without_a_jev_key_holds_each_call_at_the_company_models_price(gateway, hub, monkeypatch):
    """No TypeSafe key: the company's own model answers Slack, so a call is held at its price, not Jev's."""
    from backend.tests import test_slack_gateway as S

    class Company(S.CappedJudge):
        model = "a-model-with-no-list-price"           # held at the ceiling, about $0.21 a call
    company = Company({"asks": 0.9, "bot:legal": 0.9})
    gateway._judge = None
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: company)
    hub.settings.decisions_monthly_usd = 1.0
    with hub.transaction() as c:
        H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": 0.9})
    result = S.send(gateway, "please review the vendor terms", channel=S.DM, kind="message")
    assert company.calls == [], "at Jev's price the call would have fit; at the company model's it does not"
    assert result["decision"]["routed_by"] == "spend_cap"
    with hub.transaction() as c:
        c.execute("DELETE FROM events WHERE action='judge.spend'")
    assert S.send(gateway, "and the NDA", channel=S.DM, kind="message")["state"] == "routed"
    assert len(company.calls) == 1


# The company's own model on each transport: (transport, a model with a list price), as the fallback asks it.
COMPANY_MODELS = [("anthropic", "claude-opus-5-5"), ("openai", "gpt-6-sol"), ("google", "gemini-3.8-flash"),
                  ("xai", "grok-4.6"), ("openrouter", "moonshotai/kimi-k3")]


class WholeAllowance:
    """A provider that answers with the longest answer the request allows, and bills all of it."""

    def __init__(self, provider):
        self.provider, self.limits = provider, []

    def __call__(self, request, timeout=None):
        import io
        body = json.loads(request.data)
        limit = (body.get("generationConfig") or {}).get("maxOutputTokens") or body.get("max_tokens") \
            or body.get("max_completion_tokens")
        self.limits.append(limit)
        billed_in, billed_out = len(request.data) // 3, limit or 64_000   # no limit: a long reasoning answer
        text = json.dumps({"bucket": {"probabilities": {"archive": 0.2, "reply": 0.8}}, "is_ask": {"noul": 0.9}})
        if self.provider == "anthropic":
            payload = {"content": [{"type": "text", "text": text}],
                       "usage": {"input_tokens": billed_in, "output_tokens": billed_out}}
        elif self.provider == "google":
            payload = {"candidates": [{"content": {"parts": [{"text": text}]}}],
                       "usageMetadata": {"promptTokenCount": billed_in, "candidatesTokenCount": billed_out}}
        else:
            payload = {"choices": [{"message": {"content": text}}],
                       "usage": {"prompt_tokens": billed_in, "completion_tokens": billed_out}}
        return io.BytesIO(json.dumps(payload).encode())


@pytest.mark.parametrize("provider,model", COMPANY_MODELS)
def test_every_transport_caps_a_decisions_answer_at_what_the_cap_reserves(provider, model):
    """Review 1 on #864: only Anthropic carried an output limit, so a Google or OpenAI-style answer could be billed
    far past the call's reservation. Each now asks for at most MAX_OUTPUT_TOKENS, in that API's own field."""
    seen = []
    J.llm(provider, "synthetic", model, opener=lambda request, timeout=None: seen.append(json.loads(request.data))
          or WholeAllowance(provider)(request))({"team": "Acme"}, QUESTIONS)
    body = seen[-1]
    field = {"anthropic": body.get("max_tokens"), "google": (body.get("generationConfig") or {}).get("maxOutputTokens"),
             "openai": body.get("max_completion_tokens"), "xai": body.get("max_tokens"),
             "openrouter": body.get("max_tokens")}[provider]
    assert field == J.MAX_OUTPUT_TOKENS == B.MAX_OUTPUT_TOKENS
    if provider == "openai":
        assert "max_tokens" not in body, "OpenAI's reasoning models refuse max_tokens"
    # The draft writer is not a decision call and keeps its own length: only Anthropic, which requires one, is capped.
    J.llm_writer(provider, "synthetic", model, opener=lambda request, timeout=None: seen.append(
        json.loads(request.data)) or WholeAllowance(provider)(request))("Draft it.", "Ana's facts")
    assert provider == "anthropic" or not ({"max_tokens", "max_completion_tokens"} & set(seen[-1])
                                           or "maxOutputTokens" in (seen[-1].get("generationConfig") or {}))


@pytest.mark.parametrize("provider,model", COMPANY_MODELS)
def test_a_company_model_call_billed_its_whole_allowance_stays_within_its_reservation(api, monkeypatch, provider,
                                                                                      model):
    """The bound holds only if no call costs more than was held for it. Through the meter, on the company's model,
    with the provider billing the longest answer the request allows: the recorded cost is at most the reservation."""
    store = api.app.state.store
    store.settings.decisions_monthly_usd = 50.0
    provider_api = WholeAllowance(provider)
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: J.llm(provider, "synthetic", model,
                                                                             opener=provider_api))
    held, reserve = [], B.reservation
    monkeypatch.setattr(B, "reservation", lambda *args: held.append(reserve(*args)) or held[-1])
    with store.read() as c:
        engine = B.server_engine(store, c, None)
    result = engine({"team": "Acme"}, QUESTIONS, "company")
    assert provider_api.limits == [J.MAX_OUTPUT_TOKENS]
    with store.read() as c:
        recorded = json.loads(c.execute("SELECT detail FROM events WHERE action='judge.spend'").fetchone()[0])
    assert recorded["model"] == model and recorded["cost_usd"] == result["cost_usd"] > 0
    assert recorded["cost_usd"] <= held[0], f"{provider}: billed ${recorded['cost_usd']}, held ${held[0]}"


# ----------------------------------------------------------------------------- one engine for every caller
def test_the_server_engine_falls_back_when_jev_is_down_and_is_off_at_the_cap(api, monkeypatch):
    store = api.app.state.store
    jev, backup = FakeJudge(), FakeJudge()
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    with store.read() as c:
        engine = B.server_engine(store, c, jev)
    assert engine.model == J.MODEL
    assert "cost_usd" in engine({"team": "Acme"}, QUESTIONS) and len(jev.calls) == 1 and backup.calls == []
    jev.fail = DOWN
    assert engine({"team": "Acme"}, QUESTIONS)["fallback"] == "unavailable" and len(backup.calls) == 1
    # A provider's own 422 is its problem, once the request passed the contract here: the next one answers.
    jev.fail = J.JudgeError("invalid", "TypeSafe refused the request", 422)
    assert engine({"team": "Acme"}, QUESTIONS)["fallback"] == "invalid" and len(backup.calls) == 2
    with pytest.raises(J.JudgeError) as refused:
        engine({"team": "Acme"}, {"bucket": {"type": "choice"}})     # the contract refuses it: no provider is asked
    assert refused.value.code == "invalid" and len(jev.calls) == 3 and len(backup.calls) == 2
    # No key: the company's model alone. Neither: off.
    with store.read() as c:
        assert B.server_engine(store, c, None)({"team": "Acme"}, QUESTIONS)["model"] == "judge-1.13.0"
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: None)
    with store.read() as c:
        assert B.server_engine(store, c, None) is None
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    with store.read() as c:
        assert B.server_engine(store, c, jev) is None


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
        engine = L.engine_for(store, c)
    assert seen == [("synthetic", B.JEV_TIMEOUT, 0)], "not the direct client's 20 s and two retries"
    tally = L.Tally(engine)
    tally({"team": "Acme"}, QUESTIONS)
    tally({"team": "Acme"}, QUESTIONS)
    detail = tally.detail()
    assert detail.pop("answers") == [{"bucket": {"type": "choice", "value": "reply", "confidence": 0.8},
                                      "is_ask": {"type": "noul", "value": 0.9, "confidence": 0.8}}] * 2, "one per call"
    assert detail.pop("cost_usd") == pytest.approx(2 * B.cost_usd(store.settings, "judge-1.13.0", {"input_tokens": 90}))
    assert detail == {"calls": 2, "fallbacks": 0, "models": {"judge-1.13.0": 2}, "model": "judge-1.13.0",
                      "usage": {"input_tokens": 180, "output_tokens": 0}}
    store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    with store.read() as c:
        assert L.engine_for(store, c) is None


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
    audit = S.rows(hub, "SELECT detail FROM events WHERE action='judge.call'")
    assert json.loads(audit[-1]["detail"])["cost_usd"] == 0.005   # no list price: 500 tokens at the ceiling
    hub.settings.decisions_monthly_usd = 1.0
    with hub.transaction() as c:
        H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": 1.0})
    calls = len(gateway.engine.calls)
    second = S.send(gateway, "and the NDA too", channel=S.DM, kind="message", user="U2")
    assert len(gateway.engine.calls) == calls, "at the cap the decision model is not asked"
    assert second["decision"]["routed_by"] == "spend_cap" and "monthly decision spend" in second["decision"]["reason"]
    assert [r["bot"] for r in second["decision"]["recipients"]] == ["coo"]


def test_slack_keeps_the_cost_of_answered_batches_when_a_later_batch_fails(gateway, hub):
    """A big fleet is asked in several calls. Each answered call is counted as it returns (a `judge.spend` row, not
    one of the sender's daily calls), so a later failure, its retries and the final failure lose none and add none."""
    from backend.tests import test_slack_gateway as S
    S.add_bots(hub, 50)                                # 56 bots: two calls a message
    down = J.JudgeError("unavailable", "the decision model answered 503", 503, retryable=True)

    class Batches(S.CappedJudge):
        def __init__(self, outcomes):
            super().__init__({"asks": 0.9, "bot:legal": 0.9})
            self.outcomes = outcomes

        def __call__(self, state, questions, label=None):
            result = super().__call__(state, questions, label)
            if self.outcomes.pop(0) == "down":
                raise down
            return {**result, "usage": {"cost": 0.25}}
    gateway._judge = Batches(["ok", "down", "ok", "ok"])
    assert S.send(gateway, "please review the vendor terms", channel=S.DM, kind="message")["state"] == "received"
    gateway.clock.advance(S.G.RETRY_SECONDS)
    assert gateway.tick()["events"][-1]["state"] == "routed"
    with hub.read() as c:
        rows = [(r["action"], json.loads(r["detail"])) for r in
                c.execute("SELECT action,detail FROM events WHERE action IN ('judge.call','judge.spend') ORDER BY rowid")]
        # Each answered call is written down as it returns: the first try's one, then the retry's two.
        assert [(a, d["cost_usd"]) for a, d in rows] == [("judge.spend", 0.25)] * 3 + [("judge.call", 0.5)]
        assert B.spent_this_month(c) == 0.75
        actor = rows[1][1] and c.execute("SELECT actor FROM events WHERE action='judge.call'").fetchone()[0]
        assert B.used_today(c, actor) == 1, "the retried try is not one of the sender's calls"

    gateway._judge = Batches(["ok", "down"] * 20)
    S.send(gateway, "and the NDA", channel=S.DM, kind="message")
    gateway.clock.advance(S.G.ROUTE_GIVE_UP_SECONDS + 1)
    assert gateway.tick()["events"][-1]["state"] == "failed"
    with hub.read() as c:
        last = json.loads(c.execute("SELECT detail FROM events WHERE action='judge.call' ORDER BY rowid DESC").fetchone()[0])
        assert last["error"] == "unavailable" and last["cost_usd"] == 0.25
        assert B.spent_this_month(c) == 1.25


def test_slack_stops_a_big_fleet_at_the_cap_partway_and_routes_to_the_assistant(gateway, hub):
    from backend.tests import test_slack_gateway as S
    S.add_bots(hub, 50)                                # 56 bots: two calls a message
    hub.settings.decisions_monthly_usd = 1.0
    with hub.transaction() as c:
        H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": 0.9})

    class Paying(S.CappedJudge):
        def __call__(self, state, questions, label=None):
            return {**super().__call__(state, questions, label), "usage": {"cost": 0.25}}
    gateway._judge = engine = Paying({"asks": 0.9, "bot:legal": 0.9})
    result = S.send(gateway, "please review the vendor terms", channel=S.DM, kind="message")
    assert len(engine.calls) == 1, "the second call would start over the cap"
    assert result["state"] == "routed" and result["decision"]["routed_by"] == "spend_cap"
    assert [r["bot"] for r in result["decision"]["recipients"]] == ["coo"]
    with hub.read() as c:
        audit = json.loads(c.execute("SELECT detail FROM events WHERE action='judge.call'").fetchone()[0])
        assert audit["error"] == "spend_cap" and audit["cost_usd"] == 0.25
        assert B.spent_this_month(c) == pytest.approx(1.15)


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


# ----------------------------------------------------------------------------- the running month total
def test_the_month_total_is_a_running_sum_that_stays_exact_per_database_and_month(tmp_path):
    def open_store(name):
        s = Store(Settings(db_path=tmp_path / name))
        s.initialize(seed_market=False)
        return s

    def record(s, usd, ts):
        with s.transaction() as c:
            H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": usd})
            c.execute("UPDATE events SET ts=? WHERE id=(SELECT id FROM events WHERE action='judge.spend' "
                      "ORDER BY rowid DESC LIMIT 1)", (ts,))

    def full(s, month):
        with s.read() as c:
            return round(float(c.execute("SELECT coalesce(sum(json_extract(detail,'$.cost_usd')),0) FROM events "
                                         "WHERE ts>=? AND action='judge.spend'", (month + "-01T00:00:00",)).fetchone()[0]), 6)

    def running(s, at):
        with s.read() as c:
            return B.spent_this_month(c, at)
    oct_, nov = "2026-10-30T12:00:00.000000Z", "2026-11-02T08:00:00.000000Z"
    a, b = open_store("a.sqlite"), open_store("b.sqlite")
    record(a, 0.1, "2026-10-01T00:00:00.000000Z")
    assert running(a, oct_) == full(a, "2026-10") == 0.1
    record(a, 0.2, "2026-10-15T10:00:00.000000Z")
    record(a, 9.0, "2026-09-30T23:59:59.000000Z")          # last month: not this month's
    assert running(a, oct_) == full(a, "2026-10") == 0.3
    record(b, 2.0, "2026-10-20T10:00:00.000000Z")
    assert running(b, oct_) == full(b, "2026-10") == 2.0
    record(a, 0.25, "2026-10-29T10:00:00.000000Z")
    assert running(a, oct_) == full(a, "2026-10") == 0.55
    with a.transaction() as c:                              # a deleted newest row is counted out, not kept
        c.execute("DELETE FROM events WHERE id=(SELECT id FROM events WHERE action='judge.spend' ORDER BY rowid DESC LIMIT 1)")
    assert running(a, oct_) == full(a, "2026-10") == 0.3
    assert running(a, nov) == full(a, "2026-11") == 0.0     # a new month starts its own total
    record(a, 0.4, "2026-11-02T09:00:00.000000Z")
    assert running(a, nov) == full(a, "2026-11") == 0.4
    assert running(b, oct_) == full(b, "2026-10") == 2.0    # the other database keeps its own
