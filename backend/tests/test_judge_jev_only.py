"""Every decision is Jev's (backend/judge.py): no judge call reaches another model, whatever the caller asks for or
the server holds a key to, and when Jev fails the decision is no answer (unsure), never a pass."""

import io
import json

import pytest

from backend import judge as B
from backend import pipeline_judges as P
from backend import providers
from backend.tests.helpers.api import headers
from backend.tests.helpers.decide_gateway import answer_all
from backend.tests.helpers.judge import QUESTIONS
from clients import judge as J

GATEWAY = "https://gateway.example/v1/gateway/decisions"


@pytest.fixture
def wire(api, monkeypatch):
    """The server set up with Jev on the Gateway and on its own TypeSafe key, and a company provider (Anthropic) with a
    key on the server. Every HTTP request the decision transports send is recorded; `answer` is what each gets back
    (None: a timeout)."""
    settings = api.app.state.store.settings
    monkeypatch.setattr(settings, "gateway_key", "gk-synthetic")
    monkeypatch.setattr(settings, "gateway_url", GATEWAY)
    monkeypatch.setattr(settings, "typesafe_api_key", "ts-synthetic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-synthetic")
    loaded = providers.load
    monkeypatch.setattr(
        providers,
        "load",
        lambda c, s=None: {**loaded(c, s), "runtime": "claude", "model": "claude-haiku-5-5", "enabled": ["anthropic"]},
    )
    sent = {"requests": [], "answer": None}

    def opener(request, timeout=None):
        sent["requests"].append({"url": request.full_url, "body": json.loads(request.data)})
        if sent["answer"] is None:
            raise TimeoutError()
        return io.BytesIO(json.dumps(sent["answer"]).encode())

    monkeypatch.setattr(J, "gateway_open", opener)
    monkeypatch.setattr(J, "urlopen", opener)
    api.app.state.judge = B.primary_engine(settings, api.app.state.store)
    return sent


def _jev_only(requests):
    for request in requests:
        if request["url"] == GATEWAY:
            assert request["body"]["model"] == J.GATEWAY_MODEL
        else:
            assert request["url"].startswith(J.URL), request["url"]


def test_no_judge_call_reaches_a_model_other_than_jev(api, wire):
    store = api.app.state.store
    with store.read() as c:
        engine = B.server_engine(store, c, api.app.state.judge)
    assert engine.model == J.GATEWAY_MODEL
    # A caller that names a higher level (backend/pipeline_judges.py names Low) still gets Jev.
    with pytest.raises(J.JudgeError) as failed:
        engine({"team": "Acme"}, QUESTIONS, "mail-triage@1", level="high")
    assert failed.value.code == "unavailable"
    assert [r["url"] for r in wire["requests"]] == [GATEWAY, J.URL]
    _jev_only(wire["requests"])


def test_jev_failing_on_the_decisions_route_is_a_503_and_no_answer(api, wire):
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 503 and result.json()["error"]["code"] == "judge_unavailable"
    assert "answers" not in result.json()
    _jev_only(wire["requests"])
    with api.app.state.store.read() as c:
        (detail,) = [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='judge.call'")]
    assert detail["error"] == "unavailable" and "answers" not in detail


def test_jev_failing_leaves_a_pipeline_requirement_unsure_never_met(api, wire):
    store = api.app.state.store
    requirement = {"id": "req-1", "name": "Shows the page live", "rubric": "The link opens", "pipeline_id": None}
    attachment = {"id": "att-1", "source": "link", "digest": "d", "url": "https://x.example", "file_version": 0}
    attachment["text"] = "The link opens the page"
    with store.read() as c:
        result = P.judge_once(c, requirement, attachment, settings=store.settings, engine=P.live_engine(store))
    assert result["verdict"] == "unsure" and result["p"] == 0.5
    assert wire["requests"]
    _jev_only(wire["requests"])


def test_jev_answering_is_the_answer(api, wire):
    wire["answer"] = {"model": J.GATEWAY_MODEL, "answers": answer_all(QUESTIONS)}
    result = api.post("/api/v2/decisions", json={"state": {"team": "Acme"}, "questions": QUESTIONS}, headers=headers())
    assert result.status_code == 200, result.text
    assert result.json()["model"] == J.GATEWAY_MODEL
    assert [r["url"] for r in wire["requests"]] == [GATEWAY]


class Captured:
    """A provider's chat API as a draft writer sees it: remembers each request, answers with one JSON object."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append({"url": request.full_url, "body": json.loads(request.data)})
        text = json.dumps({"body": "- shipped"})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


def test_a_draft_still_runs_on_the_companys_own_model_and_sends_only_its_two_texts():
    """A person's update draft is writing, not a decision (backend/updates.py `own_model`): it keeps the company's
    provider, through OpenRouter with OpenRouter's id for a pi model, skipping a provider the server cannot call."""
    from backend import updates

    opener = Captured()
    pi = {"runtime": "pi", "model": "deepseek-v4.1-flash", "enabled": ["openrouter"]}
    via, key, model = updates.own_model(pi, env={"OPENROUTER_API_KEY": "synthetic"})
    assert (via, model) == ("openrouter", "deepseek/deepseek-v4.1-flash")
    write = J.llm_writer(via, key, model, opener=opener)
    assert write("Draft it.", "Ana's facts") == {"body": "- shipped"}
    body = opener.requests[-1]["body"]
    assert [m["content"] for m in body["messages"]] == ["Draft it.", "Ana's facts"] and "tools" not in body
    cursor = {"runtime": "cursor", "model": "cursor-auto", "enabled": ["cursor", "moonshot"]}
    assert updates.own_model(cursor, env={"OPENROUTER_API_KEY": "synthetic"})[2] == "moonshotai/kimi-k3"
    assert updates.own_model(cursor, env={}) is None
