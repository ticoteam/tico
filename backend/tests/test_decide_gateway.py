"""Decide on the Tico AI Gateway (backend/judge.py, clients/judge.py `gateway`): the server asks the Gateway as
tico-decide-1.0 first, then a customer's own TypeSafe key, then the team's own LLM, then nothing, failing open at
every step. All against a fake Gateway, a fake TypeSafe and a fake Anthropic: nothing here leaves the machine."""

import io
import json
import socketserver
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from backend import judge as B
from backend import slack_gateway as G
from backend.config import Settings
from backend.tests.test_api import api, get, headers, post   # noqa: F401
from backend.tests.test_judge import QUESTIONS
from backend.tests.test_slack_gateway import DM, Clock, FakeSlack, envelope, hub, rows   # noqa: F401
from clients import judge as J

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_URL = "https://hq.example/v1/gateway/decisions"
ANTHROPIC = J.LLM_URLS["anthropic"]
SECRET = "the-secret-body-of-the-mail"


def answer_all(questions):
    """A right-shaped answer to every question asked."""
    out = {}
    for qid, q in questions.items():
        if q["type"] == "choice":
            names = list(q["criteria"])
            out[qid] = {"choice": names[-1], "confidence": 0.8,
                        "probabilities": {n: (0.8 if n == names[-1] else 0.2 / (len(names) - 1)) for n in names}}
        elif q["type"] == "score":
            out[qid] = {"score": 0.0, "confidence": 0.9, "probabilities": {"0": 0.9, "1": 0.1}}
        else:
            out[qid] = {"noul": 0.1}
    return out


class Internet:
    """`urllib.request.urlopen` for the three providers. `fail[url] = <status | exception | "garbled" | "shape" |
    ("choice", value) | ("usage", value) | ("extra", text) | ("trickle", seconds)>` makes that provider fail that way ("shape" and a bad choice still come with usage: paid for);
    every request is kept as (url, headers, body, timeout). With a `clock`, a timeout takes all of its seconds."""

    def __init__(self, clock=None):
        self.requests, self.fail, self.clock = [], {}, clock

    def calls(self, url):
        return [r for r in self.requests if r[0] == url]

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = json.loads(request.data)
        self.requests.append((url, dict(request.header_items()), body, timeout))
        how = self.fail.get(url)
        if isinstance(how, int):
            raise urllib.error.HTTPError(url, how, "no", {}, io.BytesIO(json.dumps(
                {"error": "cap_reached" if how == 429 else "revoked_key", "detail": "echo: " + SECRET}).encode()))
        if isinstance(how, BaseException):
            if isinstance(how, TimeoutError) and self.clock:
                self.clock.now += timeout
            raise how
        if how == "garbled":
            return io.BytesIO(b"<html>bad gateway " + SECRET.encode() + b"</html>")
        if url == GATEWAY_URL:
            questions = body["questions"]
            payload = {"model": body["model"], "answers": {} if how == "shape" else answer_all(questions),
                       "usage": {"input_tokens": 120, "output_tokens": 0, "cost_usd": 0.000012}, "ms": 140,
                       "label": body.get("label")}
        elif url == J.URL:
            payload = {"model": "jev-1.14", "answers": {} if how == "shape" else answer_all(body["questions"]),
                       "usage": {"input_tokens": 120, "output_tokens": 0}}
        else:
            questions = json.loads(body["messages"][0]["content"])["questions"]
            reply = {qid: ({"noul": 0.2} if q["type"] == "noul" else
                           {"probabilities": ({n: 1 for n in q["criteria"]} if q["type"] == "choice"
                                              else [1] * len(q["criteria"]))})
                     for qid, q in questions.items()}
            payload = {"content": [{"text": "not JSON" if how == "shape" else json.dumps(reply)}],
                       "usage": {"input_tokens": 300, "output_tokens": 40}}
        if isinstance(how, tuple) and how[0] == "usage":
            payload["usage"] = how[1]
        if isinstance(how, tuple) and how[0] == "extra":         # text riding along an otherwise valid answer
            payload["answers"]["leak"] = {"choice": how[1], "note": how[1]}
            for answer in payload["answers"].values():
                answer["note"] = how[1]
                if isinstance(answer.get("probabilities"), dict):
                    answer["probabilities"][how[1]] = 0.5
        if isinstance(how, tuple) and how[0] == "trickle":
            return Trickle(json.dumps(payload).encode(), self.clock, how[1])
        if isinstance(how, tuple) and how[0] == "choice":
            for answer in payload["answers"].values():
                if "choice" in answer:
                    answer["choice"] = how[1]
        return io.BytesIO(json.dumps(payload).encode())


class Trickle(io.BytesIO):
    """An answer that arrives one byte every `every` seconds on `clock`: read whole, it takes all of that time."""

    def __init__(self, data, clock, every):
        super().__init__(data)
        self.clock, self.every = clock, every

    def read1(self, size=-1):
        self.clock.now += self.every
        return super().read1(1)

    def read(self, size=-1):
        data = super().read(size)
        self.clock.now += self.every * len(data)
        return data


@pytest.fixture
def net(monkeypatch):
    internet = Internet()
    monkeypatch.setattr("clients.judge.urlopen", internet)       # what every decision transport opens with
    monkeypatch.setattr("clients.judge.gateway_open", internet)  # the Gateway's own (no redirect, no loopback proxy)
    return internet


def wire(api, monkeypatch, gateway_key="gw-synthetic", own_key="", team_llm=True):
    """The server's providers as `install_judge` builds them from its settings, on the fake internet."""
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url, settings.typesafe_api_key = gateway_key, GATEWAY_URL, own_key
    api.app.state.judge = B.primary_engine(settings)
    backup = J.llm("anthropic", "an-synthetic", "claude-sonnet-5-5") if team_llm else None
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)


def audits(api):
    with api.app.state.store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT detail FROM events WHERE action='judge.call' ORDER BY rowid")]


def spends(api):
    with api.app.state.store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT detail FROM events WHERE action='judge.spend' ORDER BY rowid")]


def ask(api):
    return api.post("/api/v2/decisions", headers=headers(), json={
        "state": {"subject": "Invoice 4471", "snippet": SECRET}, "questions": QUESTIONS, "label": "mail-triage@1"})


# ----------------------------------------------------------------------------- 1. the Gateway answers
def test_with_a_gateway_key_a_decision_goes_to_the_gateway_as_tico_decide_and_the_audit_names_it(api, monkeypatch, net):
    wire(api, monkeypatch, own_key="ts-synthetic")
    shown = get(api, "judge")
    assert shown["configured"] and shown["provider"] == "Tico AI Gateway" and shown["model"] == "tico-decide-1.0"
    r = ask(api)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "tico-decide-1.0" and r.json()["answers"]["bucket"]["choice"] == "reply"
    (url, sent_headers, body, timeout), = net.requests            # one call, to the Gateway, and nothing else
    assert url == GATEWAY_URL and sent_headers["Authorization"] == "Bearer gw-synthetic" and timeout == 8
    assert body == {"state": {"subject": "Invoice 4471", "snippet": SECRET}, "model": "tico-decide-1.0",
                    "questions": QUESTIONS, "label": "mail-triage@1"}
    detail, = audits(api)
    assert detail["provider"] == "Tico AI Gateway" and detail["model"] == "tico-decide-1.0"
    assert "fallback" not in detail and SECRET not in json.dumps(detail)
    # The Gateway's own price is what the team's monthly cap counts.
    assert spends(api)[-1]["cost_usd"] == 0.000012 and detail["cost_usd"] == 0.000012


def test_a_tico_model_is_counted_at_tico_decides_price_not_the_unknown_model_ceiling(tmp_path):
    settings = Settings(db_path=tmp_path / "hub.db")
    for model in J.GATEWAY_MODELS:
        assert B.priced(settings, model)
        cost = B.cost_usd(settings, model, {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        # Low is Jev's price; Medium and High are Claude Sonnet 5.5's ($2.50 / $12.50) and Opus 5.5's ($5 / $25).
        want = 15.0 if "-medium" in model else 30.0 if "-high" in model else 0.06
        assert cost == want
    unknown = B.reservation(settings, ["no-such-model"], {"team": "Acme"}, QUESTIONS)
    assert B.reservation(settings, [J.GATEWAY_MODEL], {"team": "Acme"}, QUESTIONS) < unknown / 100


# ----------------------------------------------------------------------------- 2. falling back, in order
FAILURES = {"timeout": TimeoutError(), "down": 503, "revoked": 401, "gateway cap": 429, "refused": 422,
            "garbled": "garbled", "bad shape": "shape"}


@pytest.mark.parametrize("how", list(FAILURES))
def test_when_the_gateway_fails_the_own_key_answers_then_the_team_llm_then_decide_is_off(api, monkeypatch, net, how):
    wire(api, monkeypatch, own_key="ts-synthetic")
    net.fail[GATEWAY_URL] = FAILURES[how]
    code = {"revoked": "auth", "refused": "http", "bad shape": "shape"}.get(how, "unavailable")

    # The own key answers, after one try at the Gateway.
    r = ask(api)
    assert r.status_code == 200, r.text
    assert len(net.calls(GATEWAY_URL)) == 1 and len(net.calls(J.URL)) == 1 and not net.calls(ANTHROPIC)
    assert r.json()["model"] == "jev-1.14"
    assert audits(api)[-1]["provider"] == "TypeSafe" and audits(api)[-1]["fallback"] == code

    # The own key down too: the team's LLM answers.
    net.fail[J.URL] = 503
    r = ask(api)
    assert r.status_code == 200, r.text
    assert len(net.calls(GATEWAY_URL)) == 2 and len(net.calls(J.URL)) == 2 and len(net.calls(ANTHROPIC)) == 1
    assert r.json()["model"] == "claude-sonnet-5-5"
    assert audits(api)[-1]["provider"] == "Anthropic" and audits(api)[-1]["fallback"] == code

    # Everything down: a quick, honest answer the caller carries on from, with nothing of anyone's words in it.
    net.fail[ANTHROPIC] = 503
    r = ask(api)
    assert r.status_code == 503 and r.json()["error"]["code"] == "judge_unavailable"
    assert SECRET not in r.text and SECRET not in json.dumps(audits(api))
    assert len(net.calls(GATEWAY_URL)) == 3, "the Gateway gets one try per call, never a retry"


@pytest.mark.parametrize("only", ["gateway key, 401", "own TypeSafe key, 403"])
def test_a_revoked_key_with_no_provider_after_it_is_decide_off_and_the_caller_carries_on(api, monkeypatch, net, only):
    gateway_only = only.startswith("gateway")
    wire(api, monkeypatch, gateway_key="gw-synthetic" if gateway_only else "",
         own_key="" if gateway_only else "ts-synthetic", team_llm=False)
    net.fail[GATEWAY_URL if gateway_only else J.URL] = 401 if gateway_only else 403
    r = ask(api)
    assert r.status_code == 503 and r.json()["error"]["code"] == "judge_unavailable", r.text
    assert (len(net.calls(GATEWAY_URL)), len(net.calls(J.URL))) == ((1, 0) if gateway_only else (0, 1))
    assert audits(api)[-1]["error"] == "auth", "the audit still tells an admin the key was refused"
    assert SECRET not in r.text


def test_without_an_own_key_a_failed_gateway_goes_straight_to_the_team_llm(api, monkeypatch, net):
    wire(api, monkeypatch)
    net.fail[GATEWAY_URL] = 401
    r = ask(api)
    assert r.status_code == 200 and r.json()["model"] == "claude-sonnet-5-5"
    assert not net.calls(J.URL), "no TypeSafe call without a TypeSafe key"
    assert audits(api)[-1]["provider"] == "Anthropic" and audits(api)[-1]["fallback"] == "auth"


def no_own_key_settings(monkeypatch, tmp_path):
    """The server's settings with a gateway key and none of the three TypeSafe sources: no `TYPESAFE_API_KEY`, no
    `TICO_TYPESAFE_SECRET_ARN`, no /etc/tico/typesafe-api-key."""
    for name in ("TYPESAFE_API_KEY", "TICO_TYPESAFE_SECRET_ARN", "TICO_REHEARSAL", *J.LLM_KEYS.values()):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_DB", str(tmp_path / "env.db"))
    monkeypatch.setenv("TICO_GATEWAY_KEY", "gw-synthetic")
    monkeypatch.setenv("TICO_GATEWAY_URL", GATEWAY_URL)
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if str(self) == "/etc/tico/typesafe-api-key":
            raise FileNotFoundError(str(self))
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)
    settings = Settings.from_env()
    assert settings.gateway_key == "gw-synthetic" and settings.typesafe_api_key == ""
    return settings


@pytest.mark.parametrize("team_llm", [True, False])
def test_a_gateway_at_its_cap_with_no_own_key_never_reaches_typesafe_directly(api, monkeypatch, net, tmp_path,
                                                                                team_llm):
    """The Gateway's cap is the team's one Jev budget: at 429 cap_reached the team's LLM answers or Decide is off,
    and no call goes to TypeSafe on another key, on the decisions route or on Slack routing."""
    env = no_own_key_settings(monkeypatch, tmp_path)
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url, settings.typesafe_api_key = \
        env.gateway_key, env.gateway_url, env.typesafe_api_key
    api.app.state.judge = B.primary_engine(settings)
    backup = J.llm("anthropic", "an-synthetic", "claude-sonnet-5-5") if team_llm else None
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    net.fail[GATEWAY_URL] = 429

    r = ask(api)
    if team_llm:
        assert r.status_code == 200 and r.json()["model"] == "claude-sonnet-5-5"
        assert audits(api)[-1]["provider"] == "Anthropic" and audits(api)[-1]["fallback"] == "unavailable"
    else:
        assert r.status_code == 503 and r.json()["error"]["code"] == "judge_unavailable"
    assert len(net.calls(GATEWAY_URL)) == 1
    assert net.calls(J.URL) == [], "a capped Gateway never falls through to TypeSafe"


@pytest.mark.parametrize("team_llm", [True, False])
def test_slack_routing_with_a_capped_gateway_and_no_own_key_never_reaches_typesafe_directly(hub, monkeypatch, net,
                                                                                            tmp_path, team_llm):
    env = no_own_key_settings(monkeypatch, tmp_path)
    hub.settings.gateway_key, hub.settings.gateway_url, hub.settings.typesafe_api_key = \
        env.gateway_key, env.gateway_url, env.typesafe_api_key
    backup = J.llm("anthropic", "an-synthetic", "claude-sonnet-5-5") if team_llm else None
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: backup)
    net.fail[GATEWAY_URL] = 429
    gw = G.Gateway(hub, FakeSlack(), clock=Clock())
    gw.verify_app()
    assert gw.receive(envelope("hello there", channel=DM, kind="message")) == "queued"
    gw.tick()
    assert len(net.calls(GATEWAY_URL)) == 1 and net.calls(J.URL) == [], "a capped Gateway never falls through to TypeSafe"
    if team_llm:
        detail = json.loads(rows(hub, "SELECT detail FROM events WHERE action='judge.call'")[-1]["detail"])
        assert detail["provider"] == "Anthropic" and len(net.calls(ANTHROPIC)) == 1


def test_a_request_the_contract_refuses_is_refused_before_any_provider_is_asked(api, monkeypatch, net):
    wire(api, monkeypatch, own_key="ts-synthetic")
    r = api.post("/api/v2/decisions", headers=headers(), json={"state": {}, "questions": {
        "x": {"type": "choice", "instructions": "Pick", "criteria": {"only": None}}}})
    assert r.status_code == 422 and net.requests == []


# ----------------------------------------------------------------------------- 3. what each failure costs and takes
def test_typesafe_refusing_a_request_that_passed_the_contract_falls_through_to_the_team_llm(api, monkeypatch, net):
    wire(api, monkeypatch, own_key="ts-synthetic")
    net.fail[GATEWAY_URL], net.fail[J.URL] = 503, 422
    r = ask(api)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "claude-sonnet-5-5" and len(net.calls(J.URL)) == 1 and len(net.calls(ANTHROPIC)) == 1
    assert audits(api)[-1]["provider"] == "Anthropic" and audits(api)[-1]["fallback"] == "unavailable"


@pytest.mark.parametrize("bad", [[], {}, 7, None], ids=["list", "map", "number", "none"])
def test_a_choice_that_is_not_an_option_name_is_a_bad_shape_and_the_next_provider_answers(api, monkeypatch, net, bad):
    wire(api, monkeypatch, own_key="ts-synthetic")
    net.fail[GATEWAY_URL] = ("choice", bad)
    r = ask(api)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "jev-1.14" and audits(api)[-1]["fallback"] == "shape"
    with pytest.raises(J.JudgeError) as caught:
        J.check_answers({"bucket": {"choice": bad, "confidence": 0.5}, "is_ask": {"noul": 0.1}}, QUESTIONS)
    assert caught.value.code == "shape"


def test_an_unusable_answer_is_still_paid_for_and_counted_toward_the_cap_with_the_one_after_it(api, monkeypatch, net):
    wire(api, monkeypatch, own_key="ts-synthetic")
    store = api.app.state.store
    store.settings.decisions_monthly_usd = 50.0
    net.fail[GATEWAY_URL], net.fail[J.URL] = "shape", 503
    r = ask(api)
    assert r.status_code == 200 and r.json()["model"] == "claude-sonnet-5-5"
    llm_cost = B.cost_usd(store.settings, "claude-sonnet-5-5", {"input_tokens": 300, "output_tokens": 40})
    unusable, answered = spends(api)
    assert unusable == {"label": "mail-triage@1", "model": "tico-decide-1.0", "cost_usd": 0.000012, "unusable": True}
    assert answered["model"] == "claude-sonnet-5-5" and answered["cost_usd"] == llm_cost and "unusable" not in answered
    assert audits(api)[-1]["cost_usd"] == round(0.000012 + llm_cost, 6) and B._RESERVED[0] == 0

    # Every provider answers unusably: each is still counted, the reservation is released, and once the counted spend
    # reaches the cap the next call asks no one.
    net.fail[ANTHROPIC] = "shape"
    r = ask(api)
    assert r.status_code == 503 and r.json()["error"]["code"] == "judge_unavailable" and B._RESERVED[0] == 0
    assert [(row["model"], row.get("unusable")) for row in spends(api)[2:]] == [
        ("tico-decide-1.0", True), ("claude-sonnet-5-5", True)]
    assert audits(api)[-1]["error"] == "shape" and audits(api)[-1]["cost_usd"] == round(0.000012 + llm_cost, 6)
    store.settings.decisions_monthly_usd = round(sum(row["cost_usd"] for row in spends(api)), 6)
    asked = len(net.requests)
    r = ask(api)
    assert r.status_code == 429 and r.json()["error"]["code"] == "spend_cap" and len(net.requests) == asked


@pytest.mark.parametrize("usage", [{"input_tokens": float("nan"), "cost_usd": float("inf")},
                                   {"input_tokens": -5, "cost_usd": -1}, {}, "lots"], ids=["nan-inf", "negative", "none", "text"])
def test_a_usage_that_cannot_be_read_is_counted_at_the_calls_most_not_as_nothing(api, monkeypatch, net, usage):
    wire(api, monkeypatch)
    net.fail[GATEWAY_URL] = ("usage", usage)
    r = ask(api)
    assert r.status_code == 200 and r.json()["model"] == "tico-decide-1.0"
    most = B.reservation(api.app.state.store.settings, [J.GATEWAY_MODEL],
                         {"subject": "Invoice 4471", "snippet": SECRET}, QUESTIONS)
    row, = spends(api)
    assert row["cost_usd"] == most > 0 and row["usage"] == "unknown" and B._RESERVED[0] == 0
    assert r.json()["usage"] == audits(api)[-1]["usage"] == {}, "a bad number is dropped where the answer comes in"


def test_a_call_in_flight_reserves_what_every_provider_it_may_ask_could_cost(api, monkeypatch, net):
    settings = api.app.state.store.settings
    chain = [J.GATEWAY_MODEL, J.MODEL, "claude-sonnet-5-5"]
    each = [B.reservation(settings, [model], {"team": "Acme"}, QUESTIONS) for model in chain]
    assert B.reservation(settings, chain, {"team": "Acme"}, QUESTIONS) == pytest.approx(sum(each))
    wire(api, monkeypatch, own_key="ts-synthetic")
    with api.app.state.store.read() as c:
        engine = B.server_engine(api.app.state.store, c, api.app.state.judge)
    seen = []
    real = B.reservation
    monkeypatch.setattr(B, "reservation", lambda s, models, *a: seen.append(list(models)) or real(s, models, *a))
    engine({"team": "Acme"}, QUESTIONS)
    assert seen == [chain]


def test_the_slack_gateway_counts_an_unusable_answer_with_the_one_after_it(hub, monkeypatch, net):
    for name in J.LLM_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    hub.settings.gateway_key, hub.settings.gateway_url, hub.settings.typesafe_api_key = "gw-synthetic", GATEWAY_URL, ""
    monkeypatch.setattr(B, "fallback_engine",
                        lambda *args, **kwargs: J.llm("anthropic", "an-synthetic", "claude-sonnet-5-5"))
    net.fail[GATEWAY_URL] = "shape"
    gw = G.Gateway(hub, FakeSlack(), clock=Clock())
    gw.verify_app()
    assert gw.receive(envelope("hello there", channel=DM, kind="message")) == "queued"
    gw.tick()
    spent = [json.loads(r["detail"]) for r in rows(hub, "SELECT detail FROM events WHERE action='judge.spend'")]
    assert [(row["model"], row.get("unusable")) for row in spent] == [("tico-decide-1.0", True), ("claude-sonnet-5-5", None)]
    detail = json.loads(rows(hub, "SELECT detail FROM events WHERE action='judge.call'")[-1]["detail"])
    assert detail["provider"] == "Anthropic" and detail["cost_usd"] == round(sum(row["cost_usd"] for row in spent), 6)


class Seconds:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_providers_together_answer_within_the_chain_deadline_on_a_controlled_clock(api, monkeypatch, net):
    clock = Seconds()
    monkeypatch.setattr(J, "clock", clock)
    net.clock = clock
    wire(api, monkeypatch, own_key="ts-synthetic")
    net.fail[GATEWAY_URL] = net.fail[J.URL] = TimeoutError()
    r = ask(api)
    assert r.status_code == 200 and r.json()["model"] == "claude-sonnet-5-5"
    # The Gateway and TypeSafe take their full 8 s each; the team's LLM gets what is left of 25 s, not its own 20 s.
    assert [req[3] for req in net.requests] == [8, 8, B.CHAIN_SECONDS - 16]

    net.fail[ANTHROPIC] = TimeoutError()
    start = clock.now
    assert ask(api).status_code == 503 and clock.now - start == B.CHAIN_SECONDS

    # With less than a second left, a provider is not asked at all.
    monkeypatch.setattr(B, "CHAIN_SECONDS", 16.5)
    asked = len(net.requests)
    assert ask(api).status_code == 503 and [r[0] for r in net.requests[asked:]] == [GATEWAY_URL, J.URL]


MARKER = "upstream-echo-of-the-mail"


@pytest.mark.parametrize("provider", ["gateway", "own key"])
def test_only_the_contracts_answer_fields_reach_the_caller_never_extra_text_from_a_provider(api, monkeypatch, net,
                                                                                             provider):
    wire(api, monkeypatch, own_key="ts-synthetic")
    if provider == "own key":
        net.fail[GATEWAY_URL] = 503
    net.fail[J.URL if provider == "own key" else GATEWAY_URL] = ("extra", MARKER)
    r = ask(api)
    assert r.status_code == 200, r.text
    assert MARKER not in r.text and MARKER not in json.dumps(audits(api))
    answers = r.json()["answers"]
    assert set(answers) == set(QUESTIONS)
    assert set(answers["bucket"]) == {"choice", "confidence", "probabilities"}
    assert set(answers["bucket"]["probabilities"]) == {"archive", "reply"} and answers["is_ask"] == {"noul": 0.1}


def test_a_score_answer_keeps_its_levels_probabilities_and_the_questions_own_legend():
    questions = {"urgency": {"type": "score", "instructions": "How urgent?", "criteria": ["low", "high"]}}
    for given in ({"0": 0.9, "1": 0.1, "2": 0.5, "x": MARKER}, [0.9, 0.1]):
        got = J.check_answers({"urgency": {"score": 0.1, "confidence": 0.9, "probabilities": given, "why": MARKER},
                               "other": {"noul": 0.5}}, questions)
        assert got == {"urgency": {"score": 0.1, "confidence": 0.9, "probabilities": {0: 0.9, 1: 0.1},
                                   "legend": ["low", "high"]}}


def test_an_answer_that_trickles_in_is_cut_off_at_its_deadline_and_the_next_provider_answers(api, monkeypatch, net):
    clock = Seconds()
    monkeypatch.setattr(J, "clock", clock)
    net.clock = clock
    wire(api, monkeypatch, own_key="ts-synthetic")
    net.fail[GATEWAY_URL] = ("trickle", 2)                     # a byte every 2 s: each wait is under the 8 s timeout
    start = clock.now
    r = ask(api)
    assert r.status_code == 200 and r.json()["model"] == "jev-1.14", "the Gateway's slow answer is not waited for"
    assert audits(api)[-1]["fallback"] == "unavailable" and clock.now - start <= J.GATEWAY_TIMEOUT + 2
    # Every provider trickling: the whole chain still ends by its deadline.
    net.fail[J.URL] = net.fail[ANTHROPIC] = ("trickle", 2)
    start = clock.now
    assert ask(api).status_code == 503 and clock.now - start <= B.CHAIN_SECONDS + 2
    # One transport on its own, outside any chain, is held to its own timeout from start to last byte.
    engine = J.gateway("gw-synthetic", GATEWAY_URL, opener=net)
    start = clock.now
    with pytest.raises(J.JudgeError) as caught:
        engine({"team": "Acme"}, QUESTIONS)
    assert caught.value.code == "unavailable" and clock.now - start <= J.GATEWAY_TIMEOUT + 2


def self_signed(tmp_path):
    """A certificate for 127.0.0.1 that only this test trusts (SSL_CERT_FILE), and its key."""
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, True, False, False, False), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
            .sign(key, hashes.SHA256()))
    (tmp_path / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                         serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
    return tmp_path / "cert.pem", tmp_path / "key.pem"


class Dribble(socketserver.BaseRequestHandler):
    """A peer that never finishes its headers: a status line, then a header byte every 0.1 s, over TLS when the
    server has a `tls` context. Each wait is far under any socket timeout."""

    def handle(self):
        conn = self.request
        try:
            if self.server.tls:
                conn = self.server.tls.wrap_socket(conn, server_side=True)
            conn.settimeout(0.5)
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            for _ in range(100):
                time.sleep(0.1)
                conn.sendall(b"a")
        except (OSError, ssl.SSLError):
            pass                                    # the client shut the connection: what the test wants


@pytest.mark.slow
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_peer_that_dribbles_its_headers_is_cut_at_the_deadline_and_the_next_provider_answers(monkeypatch, tmp_path,
                                                                                               scheme):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Dribble)
    server.daemon_threads, server.tls = True, None
    if scheme == "https":
        cert, key = self_signed(tmp_path)
        server.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.tls.load_cert_chain(cert, key)
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))
        # The opener's TLS context reads the trusted certificates when it is built.
        monkeypatch.setattr(J, "_OPENER", urllib.request.build_opener(J._HTTPHandler, J._HTTPSHandler))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"{scheme}://127.0.0.1:{server.server_address[1]}/v1/decisions"

    def answers(state, questions, label=None):
        return {"answers": answer_all(questions), "model": "next-provider"}
    try:
        monkeypatch.setattr(B, "CHAIN_SECONDS", 2.5)
        start = time.monotonic()
        result = B.ordered([J.gateway("gw-synthetic", url, timeout=1.2), answers])({"team": "Acme"}, QUESTIONS)
        assert result["model"] == "next-provider" and result["fallback"] == "unavailable"
        assert time.monotonic() - start < 1.2 + 1, "the attempt is cut at its own deadline"
        # Every provider dribbling: the whole chain still ends by its deadline.
        start = time.monotonic()
        with pytest.raises(J.JudgeError) as caught:
            B.ordered([J.gateway("gw-synthetic", url, timeout=1.2), J.gateway("gw-synthetic", url)])(
                {"team": "Acme"}, QUESTIONS)
        assert caught.value.code == "unavailable" and time.monotonic() - start < B.CHAIN_SECONDS + 1
    finally:
        server.shutdown()
        server.server_close()


def test_every_client_that_asks_for_a_decision_waits_longer_than_the_chain_takes(monkeypatch):
    from clients import remotecli
    waits = []

    class Stop(Exception):
        pass

    def client(url, token, timeout=15, retries=3):
        waits.append(timeout)
        raise Stop()
    monkeypatch.setenv("HUB_API_URL", "https://hub.example")
    monkeypatch.setattr(remotecli, "Client", client)
    with pytest.raises(Stop):
        remotecli.run(type("Args", (), {"cmd": "decision", "fn": "decision ask"})())
    assert waits == [45]
    assert J.HUB_WAIT > B.SLOW_CHAIN_SECONDS > B.CHAIN_SECONDS      # `J.from_env` through the Hub, Decisions at any level
    assert B.SLOW_CHAIN_SECONDS + 1 < min(waits)
    # The MCP tools' client keeps its ordinary deadline but waits past the chain on the routes that run one.
    from clients import tico
    from clients.tico import Client, SLOW_WAIT
    opened = []

    class Opener:
        def open(self, request, timeout):
            opened.append((request.full_url.rsplit("/api/v2/", 1)[1], timeout))
            return io.BytesIO(b"{}")
    monkeypatch.setattr(tico.urllib.request, "build_opener", lambda *args: Opener())
    hub = Client("https://hub.example", "lease", timeout=30)
    for path in ("decisions", "gateway/search", "gateway/fetch", "tasks"):
        hub.request("POST", "/api/v2/" + path, {})
    assert opened == [("decisions", SLOW_WAIT), ("gateway/search", SLOW_WAIT), ("gateway/fetch", SLOW_WAIT), ("tasks", 30)]
    assert SLOW_WAIT > B.SLOW_CHAIN_SECONDS and "timeout=30)" in (ROOT / "clients" / "hubmcp.py").read_text()


# ----------------------------------------------------------------------------- 4. no provider at all
def test_with_no_gateway_key_and_no_other_provider_decide_is_off_and_callers_carry_on(api, monkeypatch, net):
    wire(api, monkeypatch, gateway_key="", team_llm=False)
    assert api.app.state.judge is None
    assert get(api, "judge")["configured"] is False
    r = ask(api)
    assert r.status_code == 503 and r.json()["error"]["code"] == "judge_unconfigured"
    with api.app.state.store.read() as c:
        assert B.server_engine(api.app.state.store, c, api.app.state.judge) is None   # listening, triage: Decide off
    assert net.requests == []


# ----------------------------------------------------------------------------- 5. config: no TypeSafe key by default
def test_the_server_reads_the_gateway_key_and_url_and_no_typesafe_key_by_default(monkeypatch, tmp_path):
    for name in ("TYPESAFE_API_KEY", "TICO_TYPESAFE_SECRET_ARN", "TICO_GATEWAY_KEY", "TICO_GATEWAY_SECRET_ARN",
                 "TICO_GATEWAY_URL", "TICO_HQ_URL", "TICO_REHEARSAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    read = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: read.append(str(self)) or real(self, *a, **k))
    settings = Settings.from_env()
    assert settings.typesafe_api_key == "" and settings.gateway_key == ""
    assert settings.gateway_url == "https://updates.tico.team/v1/gateway/decisions"
    assert B.primary_engine(settings) is None and B.jev(settings) is None

    monkeypatch.setenv("TICO_GATEWAY_KEY", "gw-synthetic")
    monkeypatch.setenv("TICO_HQ_URL", "https://hq.example/")
    settings = Settings.from_env()
    assert settings.gateway_key == "gw-synthetic" and settings.gateway_url == GATEWAY_URL
    engine = B.primary_engine(settings)
    assert engine.provider == "Tico AI Gateway" and engine.model == "tico-decide-1.0" and not hasattr(engine, "steps")
    assert "gw-synthetic" not in repr(settings), "the key is kept out of the settings' repr"
    monkeypatch.setenv("TICO_GATEWAY_URL", "https://gateway.example/decide")
    assert Settings.from_env().gateway_url == "https://gateway.example/decide"
    # A rehearsal holds no gateway key, the same as it holds no TypeSafe key.
    monkeypatch.setenv("TICO_REHEARSAL", "1")
    assert Settings.from_env().gateway_key == ""


def test_no_default_setup_or_example_config_holds_a_typesafe_key():
    example = (ROOT / ".env.example").read_text().splitlines()
    assert not [line for line in example if line.strip().startswith(("TYPESAFE_API_KEY", "TICO_TYPESAFE_SECRET_ARN"))]
    assert not [line for line in example if "TYPESAFE_API_KEY=" in line and line.split("=", 1)[1].strip()]
    assert "# TICO_GATEWAY_KEY=" in example
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    for name in ("server", "slack"):
        env = services[name]["environment"]
        assert {"TICO_GATEWAY_KEY", "TICO_GATEWAY_SECRET_ARN", "TICO_GATEWAY_URL"} <= set(env)
        assert all(env[key] is None for key in ("TICO_GATEWAY_KEY", "TICO_GATEWAY_SECRET_ARN", "TICO_GATEWAY_URL",
                                                "TYPESAFE_API_KEY", "TICO_TYPESAFE_SECRET_ARN")), \
            "keys pass through the operator's own environment; none is set in the file"


# ----------------------------------------------------------------------------- the Slack gateway routes the same way
def test_slack_routing_asks_the_gateway_first_and_audits_it(hub, monkeypatch, net):
    for name in J.LLM_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    hub.settings.gateway_key, hub.settings.gateway_url, hub.settings.typesafe_api_key = \
        "gw-synthetic", GATEWAY_URL, "ts-synthetic"
    gw = G.Gateway(hub, FakeSlack(), clock=Clock())
    gw.verify_app()
    assert [step.provider for step in gw.judge.steps] == ["Tico AI Gateway", "TypeSafe"]
    assert gw.receive(envelope("hello there", channel=DM, kind="message")) == "queued"
    gw.tick()
    assert net.requests and {r[0] for r in net.requests} == {GATEWAY_URL}
    detail = json.loads(rows(hub, "SELECT detail FROM events WHERE action='judge.call'")[-1]["detail"])
    assert detail["provider"] == "Tico AI Gateway" and detail["model"] == "tico-decide-1.0"

    net.fail[GATEWAY_URL] = 503
    assert gw.receive(envelope("and again", channel=DM, kind="message")) == "queued"
    gw.tick()
    detail = json.loads(rows(hub, "SELECT detail FROM events WHERE action='judge.call'")[-1]["detail"])
    assert detail["provider"] == "TypeSafe" and len(net.calls(J.URL)) >= 1


class _Recorder:
    """A loopback HTTP server that keeps each request's (path, Authorization) and answers with `reply(handler)`."""

    def __init__(self, reply):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        calls = self.calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _any(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                calls.append((self.path, self.headers.get("Authorization", "")))
                reply(self)
            do_GET = do_POST = do_CONNECT = _any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _json(handler, status, payload):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _answers(handler):
    _json(handler, 200, {"answers": answer_all(QUESTIONS), "model": "tico-decide-1.0"})


def test_decide_sends_the_gateway_key_only_where_it_is_set_never_through_a_redirect_or_a_loopback_proxy(monkeypatch):
    """Decide's Gateway transport holds the same line as the key check (clients/gateway.py): the key never follows a
    redirect, a plain-http loopback Gateway is reached without the environment's proxy, and a URL that is neither
    https nor this computer gets nothing (the next provider answers)."""
    other = _Recorder(_answers)
    moved = _Recorder(lambda h: (h.send_response(302), h.send_header("Location", other.url + "/v1/decisions"),
                                 h.send_header("Content-Length", "0"), h.end_headers()))
    gateway = _Recorder(_answers)
    proxy = _Recorder(lambda h: _json(h, 502, {}))
    try:
        request = lambda url: urllib.request.Request(url, data=b"{}", method="POST",
                                                     headers={"Authorization": "Bearer gw-synthetic"})
        # Control: urllib's default opener follows the redirect and hands the key to the other server.
        with J.urlopen(request(moved.url + "/v1/decisions"), timeout=5) as response:
            response.read()
        assert other.calls == [("/v1/decisions", "Bearer gw-synthetic")]
        other.calls.clear()
        moved.calls.clear()
        with pytest.raises(J.JudgeError) as caught:
            J.gateway("gw-synthetic", moved.url + "/v1/decisions")({"team": "Acme"}, QUESTIONS)
        assert (caught.value.code, caught.value.status) == ("http", 302)
        assert len(moved.calls) == 1 and other.calls == []

        for name in ("no_proxy", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
        for name in ("http_proxy", "HTTP_PROXY"):
            monkeypatch.setenv(name, proxy.url)
        # Control: with a proxy set and no loopback bypass, urllib sends a loopback request, key and all, to the proxy.
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.build_opener().open(request(gateway.url + "/v1/decisions"), timeout=5)
        assert proxy.calls and gateway.calls == []
        proxy.calls.clear()
        result = J.gateway("gw-synthetic", gateway.url + "/v1/decisions")({"team": "Acme"}, QUESTIONS)
        assert result["provider"] == J.GATEWAY_PROVIDER
        assert gateway.calls == [("/v1/decisions", "Bearer gw-synthetic")] and proxy.calls == []

        seen = []

        def spy(request, timeout=None):
            seen.append(request.full_url)
            raise AssertionError("nothing may be sent")

        def answers(state, questions, label=None):
            return {"answers": answer_all(questions), "model": "next-provider"}
        for url in ("http://localhost.acme.example/v1/decisions", "http://127.0.0.1.acme.example/v1/decisions",
                    "http://hq.example/v1/decisions", "https://user@hq.example/v1/decisions",
                    "https://hq.example/v1/decisions?x=1"):
            result = B.ordered([J.gateway("gw-synthetic", url, opener=spy), answers])({"team": "Acme"}, QUESTIONS)
            assert result["model"] == "next-provider" and result["fallback"] == "unconfigured", url
        assert seen == []
    finally:
        for server in (other, moved, gateway, proxy):
            server.close()
