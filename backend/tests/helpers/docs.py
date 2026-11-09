"""Helpers that other tests share, moved out of test_docs.py so they import no test file."""

from backend.tests.helpers.api import headers


ANA, BEN, CARA = "ana-test", "ben-test", "cara-test"  # owner, bot administrator, everyone else


def call(api, method, path, body=None, who=ANA, expected=200, **kw):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(who), **kw)
    assert r.status_code == expected, (path, r.status_code, r.text)
    return r.json()


def make(api, title="Refund policy", body="Refund within 30 days.", who=ANA, **fields):
    return call(api, "POST", "docs", {"title": title, "body": body, **fields}, who)["doc"]
