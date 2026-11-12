"""Decide on Jev for the team: each call's cost in the audit, the monthly spend cap, and one engine for every server
caller (Jev's one short try, no other model behind it, and "carry on as if Decide were off" at the cap)."""

import json

import pytest

from backend import hubdb as H
from backend import judge as B
from backend import providers
from backend.config import Settings
from backend.store import Store
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.judge import QUESTIONS, FakeJudge, events, fake
from backend.tests.helpers.slack_gateway import gateway, hub  # noqa: F401
from clients import judge as J
from backend.tests.helpers.judge_spend import DOWN, spend

pytestmark = pytest.mark.usefixtures("inside_one_half_hour")


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
    assert B.tokens(
        {"prompt_tokens": 1000, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 400}}
    ) == (600, 400, 7)
    assert B.cost_usd(settings, "a-model-with-no-price", {"output_tokens": 1_000_000}) == 50.0


def test_a_terra_call_at_272000_prompt_tokens_is_priced_at_the_long_tier_as_one_request(tmp_path):
    settings = Settings(db_path=tmp_path / "hub.db")
    # The long-prompt tier starts at 272,000 prompt tokens: 271,999 is still the standard rate.
    assert B.cost_usd(settings, "gpt-6.1-terra", {"input_tokens": 271_999, "output_tokens": 1_000}) == round(
        (271_999 * 1.25 + 1_000 * 6.25) / 1_000_000, 6
    )
    assert B.cost_usd(settings, "gpt-6.1-terra", {"input_tokens": 272_000, "output_tokens": 1_000}) == round(
        (272_000 * 2.5 + 1_000 * 9.375) / 1_000_000, 6
    )
    # Fresh and cached input together make the prompt; each part is billed at the long tier's own rate.
    assert B.cost_usd(
        settings, "gpt-6.1-terra", {"input_tokens": 200_000, "cache_read_input_tokens": 72_000, "output_tokens": 1_000}
    ) == round((200_000 * 2.5 + 72_000 * 0.125 + 1_000 * 9.375) / 1_000_000, 6)


def test_a_metered_haiku_call_keeps_the_standard_rate_over_100k_prompt_tokens(tmp_path):
    """Claude Haiku 5.5's long-prompt rate was for judge calls only (decision 2c1be08a); decisions are Jev's alone now,
    so a metered Haiku call (a person's update draft on the company's model) is priced at the standard rate."""
    assert not providers.per_request_long_tier("claude-haiku-5-5")
    assert providers.per_request_long_tier("gpt-6.1-terra")
    assert not providers.per_request_long_tier("claude-sonnet-5-5")
    settings = Settings(db_path=tmp_path / "hub.db")
    assert B.cost_usd(settings, "claude-haiku-5-5", {"input_tokens": 100_001, "output_tokens": 1_000}) == round(
        (100_001 * 0.10 + 1_000 * 0.50) / 1_000_000, 6
    )
    assert B.cost_usd(settings, "claude-sonnet-5-5", {"input_tokens": 100_001, "output_tokens": 1_000}) == round(
        (100_001 * 2.0 + 1_000 * 10.0) / 1_000_000, 6
    )


def test_the_audit_keeps_model_tokens_latency_and_cost_and_says_when_jev_failed(api, monkeypatch):
    priced(api.app.state.store.settings)
    jev = fake(api)
    jev_result = jev.__call__

    def as_jev(state, questions, label=None):
        return {
            **jev_result(state, questions, label),
            "model": "jev-1.14",
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
        }

    api.app.state.judge = as_jev
    out = post(api, "decisions", {"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": "mail-triage@1"})
    assert set(out) == {"model", "answers", "usage", "ms", "label", "ref"}, (
        "the reply's shape is unchanged but for its ref"
    )
    detail = events(api, "human:ana")[-1][1]
    assert (detail["model"], detail["usage"]["input_tokens"], detail["ms"], detail["cost_usd"]) == (
        "jev-1.14",
        1_000_000,
        180,
        0.5,
    )
    assert "fallback" not in detail

    jev.fail = DOWN
    api.app.state.judge = jev
    result = api.post(
        "/api/v2/decisions",
        json={"state": {"subject": "Invoice"}, "questions": QUESTIONS, "label": "mail-triage@1"},
        headers=headers(),
    )
    assert result.status_code == 503, "no other model answers in Jev's place"
    detail = events(api, "human:ana")[-1][1]
    assert detail["error"] == "unavailable" and "answers" not in detail and "fallback" not in detail


def test_at_the_monthly_cap_a_call_is_refused_before_any_model_is_asked(api, monkeypatch):
    engine = fake(api)
    priced(api.app.state.store.settings, 0, 0)  # Jev unpriced: each call holds the dearest list price
    api.app.state.store.settings.decisions_monthly_usd = 50.0
    spend(api, 60.0, ts="2000-01-15T00:00:00Z")  # an older month never counts
    spend(api, 30.0)
    spend(api, 19.7, actor="human:ben")  # every actor's calls count: the cap is the team's
    post(api, "decisions", {"state": {"team": "Acme"}, "questions": QUESTIONS})  # its most (~$0.21) fits
    assert len(engine.calls) == 1
    shown = get(api, "judge")
    assert shown["monthly_usd"] == 50.0 and shown["spent_this_month_usd"] == pytest.approx(49.7, abs=0.001)

    spend(api, 0.2)  # under the cap, but a call's most no longer fits
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 429 and result.json()["error"]["code"] == "spend_cap"
    assert len(engine.calls) == 1, "Jev is not asked at the cap"

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
    judged = MJ.judge_message(
        {"id": "m1", "subject": "Invoice", "from": "a@b.example"}, chosen, J.through_hub(Client())
    )
    assert judged["error"] == "spend_cap"

    # A cap of 0 is no cap.
    api.app.state.store.settings.decisions_monthly_usd = 0
    post(api, "decisions", {"state": {"team": "Acme"}, "questions": QUESTIONS})
    assert len(engine.calls) == 2


# ----------------------------------------------------------------------------- the monthly cap
def test_from_env_reads_the_monthly_cap_and_jevs_price(monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    settings = Settings.from_env()
    assert (
        settings.decisions_monthly_usd,
        settings.decisions_jev_usd_per_mtok_input,
        settings.decisions_jev_usd_per_mtok_output,
    ) == (50.0, 0.042, 0)
    monkeypatch.setenv("TICO_DECISIONS_MONTHLY_USD", "12.5")
    monkeypatch.setenv("TICO_DECISIONS_JEV_USD_PER_MTOK_INPUT", "0.4")
    monkeypatch.setenv("TICO_DECISIONS_JEV_USD_PER_MTOK_OUTPUT", "1.6")
    settings = Settings.from_env()
    assert (
        settings.decisions_monthly_usd,
        settings.decisions_jev_usd_per_mtok_input,
        settings.decisions_jev_usd_per_mtok_output,
    ) == (12.5, 0.4, 1.6)


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
    from backend.listening import run_pass
    from backend.tests.helpers.automation_runs import sql
    from backend.tests.helpers.listening import FakeJudge as Listener, item, listening, save, source_id

    store = api.app.state.store
    store.settings.decisions_monthly_usd = 1.0
    spend(api, 0.5)
    jev = Paid(0.6)  # the first answer takes the month past $1
    with store.read() as c:
        engine = B.server_engine(store, c, jev)
    engine({"team": "Acme"}, QUESTIONS)
    with pytest.raises(J.JudgeError) as refused:
        engine({"team": "Acme"}, QUESTIONS)
    assert refused.value.code == "spend_cap" and len(jev.calls) == 1, "no paid call starts over the cap"

    # Listening: the first call's posts (ten at most) are scored, the rest stay new for after the cap.
    made = listening(api, rules=[{"do": "keep", "tag": "leads", "text": "A lead"}])
    save(api, source_id(api, made["id"]), [item(n, f"A post about standups, number {n} [r0]") for n in range(12)])
    jev = Paid(0.6, Listener())
    spend(api, -0.6)  # back to $0.50, so the pass starts
    run_pass(store, None, jev)
    rows = sql(api, "SELECT status, count(*) AS n FROM listening_items GROUP BY status")
    assert {r["status"]: r["n"] for r in rows} == {"kept": 10, "new": 2} and len(jev.calls) == 1
    with store.read() as c:
        assert B.spent_this_month(c) == pytest.approx(1.1)

    # Learnings: a night that reaches the cap sends nothing and keeps its cursors, so nothing read is lost.
    items = [
        {
            "key": f"chat:{n}",
            "source": "chat",
            "created_at": H.now(),
            "audience": None,
            "label": "Chat",
            "text": f"Invoices go out on Friday {n}.",
            "author": "human:ana",
            "ref": {},
        }
        for n in range(3)
    ]
    team = {"ops": {"profile": "Ops runs invoicing."}}
    spend(api, -0.6)  # back to $0.50, so the night starts
    night = Paid(
        0.6,
        lambda state, questions, label=None: {
            "model": "judge-1.13.0",
            "ms": 5,
            "answers": {qid: {"type": "noul", "noul": 0.9} for qid in questions},
        },
    )
    monkeypatch.setattr(L, "primary_engine", lambda *args, **kwargs: night)
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
        audit = json.loads(
            c.execute(
                "SELECT detail FROM events WHERE target=? ORDER BY rowid DESC LIMIT 1", (f"{L.QUESTION_SET}@run",)
            ).fetchone()[0]
        )
        assert audit["calls"] == 1 and audit["cost_usd"] == 0.6, "the call it did make is counted"
        assert audit["answers"] == [
            {
                "learnable": {"type": "noul", "value": 0.9, "confidence": 0.8},
                "rel_ops": {"type": "noul", "value": 0.9, "confidence": 0.8},
            }
        ], "and what it answered"
    assert len(night.calls) == 1
    with store.transaction() as c:  # a capped night may be run again
        c.execute("UPDATE learning_runs SET night=? WHERE id=?", (L.night_of(L.datetime.now(L.timezone.utc)), run_id))
    assert api.post("/api/v2/learnings/run", json={}, headers=headers()).status_code == 200


def test_a_call_in_flight_holds_its_most_against_the_cap_for_every_other_request(api, monkeypatch):
    """Two requests at once: the second sees what the first may cost while it is still out, not only what is written."""
    import threading

    store = api.app.state.store
    store.settings.decisions_monthly_usd = 1.0
    priced(store.settings, 0, 0)  # Jev unpriced, so a call in flight holds a lot
    spend(api, 0.6)
    entered, release = threading.Event(), threading.Event()

    class Slow(Paid):
        def __call__(self, state, questions, label=None):
            entered.set()
            release.wait(5)
            return super().__call__(state, questions, label)

    slow = Slow(0.05)
    with store.read() as c:
        first, second = B.server_engine(store, c, slow), B.server_engine(store, c, Paid(0.05))
    held = B.reservation(store.settings, [J.MODEL], {"team": "Acme"}, QUESTIONS)
    assert 0.2 < held < 0.4, "unpriced: held high, but one call still fits under the cap"
    out = {}
    thread = threading.Thread(target=lambda: out.update(first({"team": "Acme"}, QUESTIONS, "unpriced")))
    thread.start()
    assert entered.wait(5)
    slow.inner = lambda state, questions, label=None: {"model": "an-unpriced-model", "ms": 1, "answers": {}}
    with pytest.raises(J.JudgeError) as refused:  # $0.60 written + the first call's most in flight + its own
        second({"team": "Acme"}, QUESTIONS)
    assert refused.value.code == "spend_cap"
    release.set()
    thread.join(5)
    assert out["cost_usd"] == 0.05 and B._RESERVED[0] == 0
    second({"team": "Acme"}, QUESTIONS)  # $0.65 written, nothing in flight: it fits
    with store.read() as c:
        assert B.spent_this_month(c) == pytest.approx(0.7)
        rows = [
            json.loads(r[0])
            for r in c.execute("SELECT detail FROM events WHERE action='judge.spend' AND target='unpriced'")
        ]
    assert rows == [{"label": "unpriced", "model": "an-unpriced-model", "cost_usd": 0.05}], (
        "the provider's own cost wins; an unpriced model without one is counted at the ceiling"
    )


@pytest.mark.parametrize("written", [0.009999, 0.0099])
def test_two_processes_admitted_at_once_pass_the_cap_by_at_most_the_larger_reservation(
    gateway, hub, monkeypatch, written
):
    """QA's repro on the v0.3.58 candidate: cap $0.01 and $0.009999 written; a server_engine call and a Slack
    Gateway.ask in the gateway's own process (its own meter) both checked before either answer is written; each
    answers 1000 tokens in, 0 out, on Jev. Each admits a call only if it fits with its own most, so the month ends
    at most the larger reservation over the cap. Before the fix both were let in at $0.009999: $0.010083 written."""
    from backend.tests.helpers import slack_gateway as S

    hub.settings.decisions_monthly_usd = 0.01
    with hub.transaction() as c:
        H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": written})
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
        out["slack"] = slack_process()  # the Slack call is checked while this one is out
        return {
            "model": J.MODEL,
            "answers": FakeJudge()(state, questions, label)["answers"],
            "usage": jev_usage,
            "ms": 1,
        }

    out = {}
    with hub.read() as c:
        engine = B.server_engine(hub, c, server_jev)
    try:
        engine(state, QUESTIONS, "server")
    except J.JudgeError as exc:
        assert exc.code == "spend_cap"
        out["slack"] = slack_process()  # the server call never went out; Slack is checked alone
    with hub.read() as c:
        spent = B.spent_this_month(c)
    assert len(held) == 2 and max(held) < 0.0001  # Jev at $0.042 per 1M in: a few hundredths of a cent each
    assert spent <= 0.01 + max(held), f"${spent} written against a $0.01 cap"
    if written == 0.009999:  # neither fits with its own most: nothing more is spent
        assert spent == pytest.approx(written) and slack_answered == []
        assert out["slack"]["decision"]["routed_by"] == "spend_cap"
    else:  # both fit: both answer, and the month stays under the cap
        assert len(slack_answered) == 1 and spent == pytest.approx(written + 2 * 0.000042) and spent <= 0.01
    assert B._RESERVED[0] == 0


class WholeAllowance:
    """A provider that answers with the longest answer the request allows, and bills all of it."""

    def __init__(self, provider):
        self.provider, self.limits = provider, []

    def __call__(self, request, timeout=None):
        import io

        body = json.loads(request.data)
        limit = (
            (body.get("generationConfig") or {}).get("maxOutputTokens")
            or body.get("max_tokens")
            or body.get("max_completion_tokens")
        )
        self.limits.append(limit)
        billed_in, billed_out = len(request.data) // 3, limit or 64_000  # no limit: a long reasoning answer
        text = json.dumps({"body": "- shipped"})
        if self.provider == "anthropic":
            payload = {
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": billed_in, "output_tokens": billed_out},
            }
        elif self.provider == "google":
            payload = {
                "candidates": [{"content": {"parts": [{"text": text}]}}],
                "usageMetadata": {"promptTokenCount": billed_in, "candidatesTokenCount": billed_out},
            }
        else:
            payload = {
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": billed_in, "completion_tokens": billed_out},
            }
        return io.BytesIO(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "provider,model",
    [
        ("anthropic", "claude-opus-5-5"),
        ("openai", "gpt-6-sol"),
        ("google", "gemini-3.8-flash"),
        ("xai", "grok-4.6"),
        ("openrouter", "moonshotai/kimi-k3"),
    ],
)
def test_every_transport_caps_a_metered_drafts_answer_at_what_the_cap_reserves(provider, model):
    """Review 1 on #864: an update draft on the company's model is metered, so on every transport it asks for at most
    MAX_OUTPUT_TOKENS, in that API's own field, and is never billed past its reservation."""
    seen = []
    J.llm_writer(
        provider,
        "synthetic",
        model,
        opener=lambda request, timeout=None: seen.append(json.loads(request.data)) or WholeAllowance(provider)(request),
    )("Draft it.", "Ana's facts")
    body = seen[-1]
    assert (
        {
            "anthropic": body.get("max_tokens"),
            "google": (body.get("generationConfig") or {}).get("maxOutputTokens"),
            "openai": body.get("max_completion_tokens"),
            "xai": body.get("max_tokens"),
            "openrouter": body.get("max_tokens"),
        }[provider]
        == J.MAX_OUTPUT_TOKENS
        == B.MAX_OUTPUT_TOKENS
    )
    if provider == "openai":
        assert "max_tokens" not in body, "OpenAI's reasoning models refuse max_tokens"


def test_the_server_engine_asks_jev_alone_and_is_off_at_the_cap(api, monkeypatch):
    store = api.app.state.store
    jev = FakeJudge()
    with store.read() as c:
        engine = B.server_engine(store, c, jev)
    assert engine.model == J.MODEL
    assert "cost_usd" in engine({"team": "Acme"}, QUESTIONS) and len(jev.calls) == 1
    # Jev down or refusing: the caller gets the error, and no other model is asked.
    for failure in (DOWN, J.JudgeError("invalid", "TypeSafe refused the request", 422)):
        jev.fail = failure
        with pytest.raises(J.JudgeError) as failed:
            engine({"team": "Acme"}, QUESTIONS)
        assert failed.value.code == failure.code
    assert len(jev.calls) == 3
    # No Jev: off, even with a company model's key on the server.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic")
    with store.read() as c:
        assert B.server_engine(store, c, None) is None
    store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    with store.read() as c:
        assert B.server_engine(store, c, jev) is None


# ----------------------------------------------------------------------------- one engine for every caller
def test_listening_leaves_a_post_unscored_when_jev_is_down_and_waits_at_the_cap(api):
    from backend.listening import run_pass
    from backend.tests.helpers.automation_runs import sql
    from backend.tests.helpers.listening import item, listening, save, source_id

    store = api.app.state.store
    jev = FakeJudge()
    jev.fail = DOWN
    made = listening(api, rules=[{"do": "keep", "tag": "leads", "text": "A lead"}])
    source = source_id(api, made["id"])
    save(api, source, [item(1, "Someone asks how teams handle standups")])
    run_pass(store, None, jev)
    assert sql(api, "SELECT status, tries FROM listening_items") == [{"status": "new", "tries": 1}]
    assert len(jev.calls) == 1

    store.settings.decisions_monthly_usd = 1.0
    spend(api, 1.0)
    save(api, source, [item(2, "Another post about how teams handle standups")])
    run_pass(store, None, jev)  # at the cap nothing is asked, and waiting is not a try
    rows = sql(api, "SELECT status, tries FROM listening_items ORDER BY native_id")
    assert rows == [{"status": "new", "tries": 1}, {"status": "new", "tries": 0}] and len(jev.calls) == 1


# ----------------------------------------------------------------------------- the running month total
def test_the_month_total_is_a_running_sum_that_stays_exact_per_database_and_month(tmp_path):
    def open_store(name):
        s = Store(Settings(db_path=tmp_path / name))
        s.initialize(seed_market=False)
        return s

    def record(s, usd, ts):
        with s.transaction() as c:
            H.event(c, "bot:ops", "judge.spend", "mail-triage@1", {"cost_usd": usd})
            c.execute(
                "UPDATE events SET created_at=? WHERE id=(SELECT id FROM events WHERE action='judge.spend' "
                "ORDER BY rowid DESC LIMIT 1)",
                (ts,),
            )

    def full(s, month):
        with s.read() as c:
            return round(
                float(
                    c.execute(
                        "SELECT coalesce(sum(json_extract(detail,'$.cost_usd')),0) FROM events "
                        "WHERE created_at>=? AND action='judge.spend'",
                        (month + "-01T00:00:00",),
                    ).fetchone()[0]
                ),
                6,
            )

    def running(s, at):
        with s.read() as c:
            return B.spent_this_month(c, at)

    oct_, nov = "2026-10-30T12:00:00.000000Z", "2026-11-02T08:00:00.000000Z"
    a, b = open_store("a.sqlite"), open_store("b.sqlite")
    record(a, 0.1, "2026-10-01T00:00:00.000000Z")
    assert running(a, oct_) == full(a, "2026-10") == 0.1
    record(a, 0.2, "2026-10-15T10:00:00.000000Z")
    record(a, 9.0, "2026-09-30T23:59:59.000000Z")  # last month: not this month's
    assert running(a, oct_) == full(a, "2026-10") == 0.3
    record(b, 2.0, "2026-10-20T10:00:00.000000Z")
    assert running(b, oct_) == full(b, "2026-10") == 2.0
    record(a, 0.25, "2026-10-29T10:00:00.000000Z")
    assert running(a, oct_) == full(a, "2026-10") == 0.55
    with a.transaction() as c:  # a deleted newest row is counted out, not kept
        c.execute(
            "DELETE FROM events WHERE id=(SELECT id FROM events WHERE action='judge.spend' ORDER BY rowid DESC LIMIT 1)"
        )
    assert running(a, oct_) == full(a, "2026-10") == 0.3
    assert running(a, nov) == full(a, "2026-11") == 0.0  # a new month starts its own total
    record(a, 0.4, "2026-11-02T09:00:00.000000Z")
    assert running(a, nov) == full(a, "2026-11") == 0.4
    assert running(b, oct_) == full(b, "2026-10") == 2.0  # the other database keeps its own


# ----------------------------------------------------------------------------- what the cost is counted as
def test_a_gateway_calls_cached_input_is_billed_at_the_cached_rate_not_the_input_rate(tmp_path):
    # Tico Decide Medium is Claude Sonnet 5.5: $2.50 input, $0.25 cached input, $12.50 output per 1M tokens (the catalog).
    settings = Settings(db_path=tmp_path / "hub.db")
    medium = "tico-decide-medium-1.0"
    cached = {"input_tokens": 0, "cache_read_input_tokens": 1_000_000, "output_tokens": 0}
    assert B.cost_usd(settings, medium, cached) == 0.25
    both = {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000, "output_tokens": 100_000}
    assert B.cost_usd(settings, medium, both) == 4.0  # 2.50 + 0.25 + 1.25


def test_an_old_rules_spend_row_still_counts_under_its_set_in_the_readout(api):
    # Org rule checks once metered under a budget of their own and wrote `rules.spend` rows; they write `judge.spend`
    # now. The rows already written stay, and the readout's cost per set still counts them as past spend.
    with api.app.state.store.transaction() as c:
        H.event(
            c,
            H.KEEPER,
            "judge.call",
            "rule-check@1",
            {
                "label": "rule-check@1",
                "model": "tico-decide-medium-1.0",
                "provider": "Tico AI Gateway",
                "cost_usd": 0.00875,
                "usage": {},
                "answers": {},
            },
        )
        H.event(
            c,
            H.KEEPER,
            "rules.spend",
            "rule-check@1",
            {"label": "rule-check@1", "model": "tico-decide-medium-1.0", "cost_usd": 0.00875},
        )
    sets = {row["set"]: row for row in get(api, "decisions/readout")["sets"]}
    assert sets["rule-check"]["cost_usd"] == 0.00875
