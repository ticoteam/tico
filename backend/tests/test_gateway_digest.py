"""POST /api/v2/gateway/digest and `hub_gateway_digest` (backend/providers_routes.py, clients/hubtools.py): the items and
the focus go to the Gateway with the company's key, the Gateway's groups come back, and a refused request never reaches
it. The Gateway is faked: no call leaves the test."""

import pytest

from backend.tests.helpers.api import headers  # noqa: F401
from backend.tests.test_api import api  # noqa: F401
from backend.tests.helpers.mcp import call
from clients import gateway as G

ITEMS = [
    {"id": "a", "title": "Pro plan price rise", "url": "https://northwind.example/pricing"},
    {"title": "Pro plan price rise again", "text": "Now 39 a seat."},
]
GROUPS = {
    "summary": "Northwind raised its Pro plan.",
    "groups": [{"title": "Pricing", "why": "Our bets cite Northwind.", "item_ids": ["a", "item-2"]}],
    "dropped": [],
    "usage": {"cost_usd": 0.0004},
}


def connect(api):
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url = "gw-synthetic", "https://hq.example/v1/gateway/decisions"


def test_the_digest_route_sends_the_items_and_answers_the_gateways_shape(api, monkeypatch):
    connect(api)
    sent = []

    def digest(base, key, items, focus, style, header):
        sent.append((base, key, items, focus, style, header))
        return 200, dict(GROUPS), ""

    monkeypatch.setattr(G, "digest", digest)
    result = api.post("/api/v2/gateway/digest", json={"items": ITEMS, "focus": "pricing"}, headers=headers("ana-test"))
    assert result.status_code == 200, result.text
    assert result.json() == GROUPS
    base, key, items, focus, style, header = sent[0]
    assert (base, key, focus, style, header) == (
        "https://hq.example/v1/gateway",
        "gw-synthetic",
        "pricing",
        "brief",
        {},
    )
    assert [item["title"] for item in items] == ["Pro plan price rise", "Pro plan price rise again"]


def test_the_digest_route_refuses_a_bad_request_and_maps_the_gateways_refusals(api, monkeypatch):
    connect(api)

    def forbidden(*args, **kwargs):
        raise AssertionError("A refused request must not reach the Gateway")

    monkeypatch.setattr(G, "digest", forbidden)
    for body in ({"items": []}, {"items": ITEMS, "style": "long"}, {"items": ITEMS, "focus": "x" * 501}):
        assert api.post("/api/v2/gateway/digest", json=body, headers=headers("ana-test")).status_code == 422, body
    for status, code, want_status, want in (
        (404, "404", 503, "gateway_digest_unavailable"),
        (422, "invalid", 422, "gateway_digest_failed"),
        (429, "cap_reached", 503, "gateway_digest_failed"),
    ):
        monkeypatch.setattr(G, "digest", lambda *args, status=status, code=code: (status, {}, code))
        refused = api.post("/api/v2/gateway/digest", json={"items": ITEMS}, headers=headers("ana-test"))
        assert (refused.status_code, refused.json()["error"]["code"]) == (want_status, want), refused.text
    store = api.app.state.store
    store.settings.gateway_key = ""
    store.settings.gateway_url = ""
    unconnected = api.post("/api/v2/gateway/digest", json={"items": ITEMS}, headers=headers("ana-test"))
    assert unconnected.status_code == 409


def test_the_digest_tool_sends_the_items_and_returns_the_groups(api, monkeypatch):
    from clients import hubtools

    tool = hubtools.BY_NAME["hub_gateway_digest"]
    assert tool["inputSchema"]["required"] == ["items"]
    assert tool["inputSchema"]["properties"]["items"]["maxItems"] == 300
    connect(api)
    asked = []
    monkeypatch.setattr(
        G,
        "digest",
        lambda base, key, items, focus, style, header: asked.append((items, focus, style)) or (200, dict(GROUPS), ""),
    )
    error, out = call(api, "hub_gateway_digest", {"items": ITEMS, "focus": "pricing", "style": "full"})
    assert not error and out == GROUPS
    assert asked == [(ITEMS, "pricing", "full")]
    error, out = call(api, "hub_gateway_digest", {"items": ITEMS})
    assert not error and asked[-1] == (ITEMS, None, "brief")
    error, out = call(api, "hub_gateway_digest", {"items": []})
    assert error and len(asked) == 2


def test_the_client_keeps_the_documented_shape_and_sends_the_style(monkeypatch):
    sent = {}

    def post(url, body, headers, timeout, opener):
        sent.update(url=url, body=body, timeout=timeout)
        return (
            200,
            {
                "model": "tico-digest-1.0",
                "summary": "S",
                "groups": [{"title": "T", "why": "W", "item_ids": ["a"], "extra": 1}, "junk"],
                "dropped": ["b"],
                "usage": {"cost_usd": True},
            },
            "",
        )

    monkeypatch.setattr(G, "_post", post)
    status, answer, code = G.digest("https://gateway.example.test/v1/gateway", "k", ITEMS, focus="x" * 600, style="odd")
    assert (status, code) == (200, "")
    assert sent["url"].endswith("/digest") and sent["body"]["style"] == "brief" and len(sent["body"]["focus"]) == 500
    assert sent["timeout"] == 130
    assert answer == {
        "summary": "S",
        "groups": [{"title": "T", "why": "W", "item_ids": ["a"]}],
        "dropped": ["b"],
        "usage": {"cost_usd": None},
    }
    monkeypatch.setattr(G, "_post", lambda *a: (422, {}, "invalid"))
    assert G.digest("https://gateway.example.test/v1/gateway", "k", ITEMS) == (422, {}, "invalid")
