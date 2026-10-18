"""Decide on the Tico AI Gateway (backend/judge.py, clients/judge.py `gateway`): the server asks the Gateway as
tico-decide-1.0 first, then a customer's own TypeSafe key, then the team's own LLM, then nothing, failing open at
every step. All against a fake Gateway, a fake TypeSafe and a fake Anthropic: nothing here leaves the machine."""

import io
import json
import urllib.error
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
    """`urllib.request.urlopen` for the three providers. `fail[host] = <status | exception | "garbled" | "shape">`
    makes that provider fail that way; every request is kept as (url, headers, body, timeout)."""

    def __init__(self):
        self.requests, self.fail = [], {}

    def calls(self, url):
        return [r for r in self.requests if r[0] == url]

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = json.loads(request.data)
        self.requests.append((url, dict(request.header_items()), body, timeout))
        how = self.fail.get(url)
        if isinstance(how, int):
            raise urllib.error.HTTPError(url, how, "no", {}, io.BytesIO(json.dumps(
                {"error": "revoked_key", "detail": "echo: " + SECRET}).encode()))
        if isinstance(how, BaseException):
            raise how
        if how == "garbled":
            return io.BytesIO(b"<html>bad gateway " + SECRET.encode() + b"</html>")
        if url == GATEWAY_URL:
            questions = body["questions"]
            payload = {"model": body["model"], "answers": {} if how == "shape" else answer_all(questions),
                       "usage": {"input_tokens": 120, "output_tokens": 0, "cost_usd": 0.000012}, "ms": 140,
                       "label": body.get("label")}
        elif url == J.URL:
            payload = {"model": "jev-1.14", "answers": answer_all(body["questions"]),
                       "usage": {"input_tokens": 120, "output_tokens": 0}}
        else:
            questions = json.loads(body["messages"][0]["content"])["questions"]
            reply = {qid: ({"noul": 0.2} if q["type"] == "noul" else
                           {"probabilities": ({n: 1 for n in q["criteria"]} if q["type"] == "choice"
                                              else [1] * len(q["criteria"]))})
                     for qid, q in questions.items()}
            payload = {"content": [{"text": json.dumps(reply)}], "usage": {"input_tokens": 300, "output_tokens": 40}}
        return io.BytesIO(json.dumps(payload).encode())


@pytest.fixture
def net(monkeypatch):
    internet = Internet()
    monkeypatch.setattr("urllib.request.urlopen", internet)
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
            "SELECT detail_json FROM events WHERE action='judge.call' ORDER BY rowid")]


def spends(api):
    with api.app.state.store.read() as c:
        return [json.loads(r[0]) for r in c.execute(
            "SELECT detail_json FROM events WHERE action='judge.spend' ORDER BY rowid")]


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
        assert B.cost_usd(settings, model, {"input_tokens": 1_000_000, "output_tokens": 1_000_000}) == 0.06
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


def test_without_an_own_key_a_failed_gateway_goes_straight_to_the_team_llm(api, monkeypatch, net):
    wire(api, monkeypatch)
    net.fail[GATEWAY_URL] = 401
    r = ask(api)
    assert r.status_code == 200 and r.json()["model"] == "claude-sonnet-5-5"
    assert not net.calls(J.URL), "no TypeSafe call without a TypeSafe key"
    assert audits(api)[-1]["provider"] == "Anthropic" and audits(api)[-1]["fallback"] == "auth"


def test_a_request_the_contract_refuses_is_refused_before_any_provider_is_asked(api, monkeypatch, net):
    wire(api, monkeypatch, own_key="ts-synthetic")
    r = api.post("/api/v2/decisions", headers=headers(), json={"state": {}, "questions": {
        "x": {"type": "choice", "instructions": "Pick", "criteria": {"only": None}}}})
    assert r.status_code == 422 and net.requests == []


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
    detail = json.loads(rows(hub, "SELECT detail_json FROM events WHERE action='judge.call'")[-1]["detail_json"])
    assert detail["provider"] == "Tico AI Gateway" and detail["model"] == "tico-decide-1.0"

    net.fail[GATEWAY_URL] = 503
    assert gw.receive(envelope("and again", channel=DM, kind="message")) == "queued"
    gw.tick()
    detail = json.loads(rows(hub, "SELECT detail_json FROM events WHERE action='judge.call'")[-1]["detail_json"])
    assert detail["provider"] == "TypeSafe" and len(net.calls(J.URL)) >= 1
