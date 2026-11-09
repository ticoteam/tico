"""Helpers that other tests share, moved out of test_decide_strict.py so they import no test file."""

import io
import json
import urllib.error
import pytest
from backend import judge as B
from backend.tests.helpers.decide_gateway import GATEWAY_URL, answer_all


EVIDENCE = {
    "summary": "reads a keychain secret",
    "findings": [{"file": "run.py", "line": 4, "issue": "reads a secret", "severity": "high"}],
}


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
        payload = {
            "model": body["model"],
            "answers": answer_all(body["questions"]),
            "usage": {"input_tokens": 900, "output_tokens": 60, "cost_usd": 0.0004},
            "ms": 300,
            "label": body.get("label"),
        }
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
