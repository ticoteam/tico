"""Helpers that other tests share, moved out of test_hermes_pairing.py so they import no test file."""

from backend.tests.helpers.api import headers


def pair(api, profile="scout", ip=None, harness="hermes"):
    extra = {"cf-connecting-ip": ip} if ip else {}
    r = api.post(
        "/api/v2/agents/pairings",
        json={"profile": profile, "harness": harness, "host": "mac-mini", "version": "1.0"},
        headers=extra,
    )
    return r


def approve(api, code, bot="scout", token="ana-test"):
    return api.post("/api/v2/agents/pairings/approve", json={"code": code, "bot": bot}, headers=headers(token))
