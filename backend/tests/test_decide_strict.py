"""The strict Decide call (backend/judge.py `strict_engine`, skill screening's): the Tico AI Gateway at one level and
nothing else, metered under the monthly cap, audited without the state, and with the evidence it was asked for passed
through checked. A failed Gateway is raised, never answered by another provider."""

import io
import json
import urllib.error

import pytest

from backend import judge as B
from backend.store import H
from backend.tests.test_api import api   # noqa: F401
from backend.tests.test_decide_gateway import GATEWAY_URL, answer_all
from backend.tests.test_judge import QUESTIONS
from clients import judge as J

EVIDENCE = {"summary": "reads a keychain secret", "findings": [
    {"file": "run.py", "line": 4, "issue": "reads a secret", "severity": "high"}]}
SKILL_TEXT = "the-skill-body-that-must-never-be-kept"


class Gateway:
    """The Tico AI Gateway's decisions door as the client reaches it: records each call; `fail` is the HTTP status it
    answers with, `evidence` what it sends beside the answers when asked (None: nothing)."""

    def __init__(self):
        self.sent, self.fail, self.evidence = [], None, EVIDENCE

    def __call__(self, request, timeout=None):
        url, body = request.full_url, json.loads(request.data)
        self.sent.append((url, body, timeout))
        if self.fail:
            raise urllib.error.HTTPError(url, self.fail, "no", {}, io.BytesIO(b'{"error":"unavailable"}'))
        payload = {"model": body["model"], "answers": answer_all(body["questions"]),
                   "usage": {"input_tokens": 900, "output_tokens": 60, "cost_usd": 0.0004}, "ms": 300,
                   "label": body.get("label")}
        if body.get("evidence") and self.evidence is not None:
            payload["evidence"] = self.evidence
        return io.BytesIO(json.dumps(payload).encode())


def nowhere(*args, **kwargs):
    raise AssertionError("no provider but the Gateway may be asked by the strict call")


@pytest.fixture
def gateway(api, monkeypatch):
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url, settings.typesafe_api_key = "gw-synthetic", GATEWAY_URL, "ts-synthetic"
    internet = Gateway()
    monkeypatch.setattr("clients.judge.gateway_open", internet)
    monkeypatch.setattr("clients.judge.urlopen", nowhere)
    monkeypatch.setattr(B, "fallback_engine", nowhere)
    return internet


def strict(api, **kwargs):
    store = api.app.state.store
    with store.read() as c:
        return B.strict_engine(store, c, **kwargs)


def judge_calls(api):
    with api.app.state.store.read() as c:
        return [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='judge.call' ORDER BY rowid")]


def spend_rows(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM events WHERE action='judge.spend'").fetchone()[0]


def test_the_strict_call_asks_the_gateway_at_high_once_and_nothing_else(api, gateway):
    engine = strict(api)
    result = engine({"skill": {"name": "triage"}}, QUESTIONS, "skill-screen@1", evidence=True)
    (url, body, timeout), = gateway.sent                    # one call, to the Gateway, and no other provider
    assert url == GATEWAY_URL and body["model"] == "tico-decide-high-1.0" and body["label"] == "skill-screen@1"
    assert timeout == B.STRICT_SECONDS == 90
    assert result["model"] == "tico-decide-high-1.0" and result["provider"] == "Tico AI Gateway"
    assert result["answers"]["bucket"]["choice"] == "reply"


def test_the_evidence_comes_back_checked_and_the_audit_keeps_neither_the_state_nor_the_evidence(api, gateway):
    engine = strict(api)
    result = engine({"skill": {"text": SKILL_TEXT}}, QUESTIONS, "skill-screen@1", evidence=True)
    assert result["evidence"] == EVIDENCE
    (detail,) = judge_calls(api)
    assert detail["model"] == "tico-decide-high-1.0" and detail["answers"]["is_ask"]["type"] == "noul"
    assert detail["questions"] == 2 and detail["usage"]["input_tokens"] == 900
    assert SKILL_TEXT not in json.dumps(detail) and "keychain" not in json.dumps(detail)
    assert gateway.sent[0][1]["evidence"] is True


def test_without_the_flag_the_request_and_the_result_have_no_evidence(api, gateway):
    result = strict(api)({"skill": {"text": SKILL_TEXT}}, QUESTIONS, "skill-screen@1")
    assert "evidence" not in result and "evidence" not in gateway.sent[0][1]


def test_a_gateway_failure_is_raised_with_its_code_once_and_no_other_provider_is_asked(api, gateway):
    gateway.fail = 503
    with pytest.raises(J.JudgeError) as failed:
        strict(api)({"skill": {"text": SKILL_TEXT}}, QUESTIONS, "skill-screen@1", evidence=True)
    assert failed.value.code == "unavailable"
    assert len(gateway.sent) == 1                           # no retry, and no other provider
    (detail,) = judge_calls(api)
    assert detail["error"] == "unavailable" and "answers" not in detail
    assert spend_rows(api) == 0                             # nothing was answered, so nothing was charged


def test_an_answer_with_malformed_evidence_fails_the_screen_and_its_cost_is_still_counted(api, gateway):
    gateway.evidence = {"summary": "ok", "findings": [{"file": "a.py", "line": 1, "issue": "x", "severity": "severe"}]}
    with pytest.raises(J.JudgeError) as failed:
        strict(api)({"skill": {"text": SKILL_TEXT}}, QUESTIONS, "skill-screen@1", evidence=True)
    assert failed.value.code == "shape"
    (detail,) = judge_calls(api)
    assert detail["error"] == "shape" and detail["cost_usd"] == 0.0004
    assert spend_rows(api) == 1


def test_the_month_at_its_cap_means_no_engine_at_all(api, gateway):
    settings = api.app.state.store.settings
    settings.decisions_monthly_usd = 1.0
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "judge.spend", "earlier", {"cost_usd": 1.5})
    assert strict(api) is None and gateway.sent == []


def test_a_call_that_would_pass_the_cap_is_refused_before_the_gateway_is_asked(api, gateway):
    api.app.state.store.settings.decisions_monthly_usd = 0.001     # the reservation of one High call is far more
    with pytest.raises(J.JudgeError) as refused:
        strict(api)({"skill": {"text": SKILL_TEXT}}, QUESTIONS, "skill-screen@1", evidence=True)
    assert refused.value.code == "spend_cap" and gateway.sent == []
    assert judge_calls(api)[-1]["error"] == "spend_cap"


def test_without_a_gateway_key_or_url_there_is_no_strict_engine(api, gateway):
    api.app.state.store.settings.gateway_key = ""
    assert strict(api) is None and gateway.sent == []


def test_the_client_checks_and_clamps_evidence_the_way_the_gateway_does():
    long = {"summary": "s" * 900, "findings": [{"file": "f" * 300, "line": -2, "issue": "i" * 400, "severity": "low"}]
            + [{"file": "a", "line": n, "issue": "b", "severity": "high"} for n in range(30)]}
    got = J.check_evidence(long)
    assert len(got["summary"]) == J.EVIDENCE_SUMMARY and len(got["findings"]) == J.EVIDENCE_FINDINGS
    assert got["findings"][0] == {"file": "f" * J.EVIDENCE_FILE, "line": 0, "issue": "i" * J.EVIDENCE_ISSUE,
                                  "severity": "low"}
    for bad in (None, {"summary": "x"}, {"summary": "x", "findings": [{"file": "a", "line": 1.0, "issue": "x",
                                                                      "severity": "low"}]}):
        with pytest.raises(J.JudgeError) as shape:
            J.check_evidence(bad)
        assert shape.value.code == "shape"


def test_a_question_set_may_declare_evidence_as_a_flag_and_a_set_without_it_is_as_before():
    data = {"id": "skill-screen", "version": 1, "summary": "s", "questions": QUESTIONS, "evidence": True}
    assert J.check_set(data, "skill-screen")["evidence"] is True
    assert "evidence" not in J.check_set({k: v for k, v in data.items() if k != "evidence"}, "skill-screen")
    with pytest.raises(J.JudgeError) as bad:
        J.check_set({**data, "evidence": "yes"}, "skill-screen")
    assert bad.value.code == "invalid"
