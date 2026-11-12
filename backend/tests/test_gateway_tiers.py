"""The Tico AI Gateway's picks (backend/gateway_tiers.py): ids, the catalog refresh, a bot's level, the Decisions level
and the lineup an agent reads. Fakes only: no call leaves the test."""

import json
from pathlib import Path

import pytest

from backend import gateway_tiers as T
from backend import providers
from backend.tests.helpers.api import get, headers  # noqa: F401
from clients import gateway as G
from clients import judge as J


@pytest.fixture(autouse=True)
def fresh_catalog():
    T.reset()
    yield
    T.reset()


def test_ids_resolve_to_a_tier_a_version_and_a_price():
    auto = T.resolve("tico-medium")
    pinned = T.resolve("tico-medium-1.0")
    assert (auto["auto"], pinned["auto"]) == (True, False)
    assert auto["version"] == pinned["version"] == "1.0" and auto["tier"] == pinned["tier"] == "tico-medium"
    assert pinned["model"] == "Claude Sonnet 5.5" and pinned["price"] == {
        "input": 2.5,
        "output": 12.5,
        "cached_input": 0.25,
    }
    fast = T.resolve("tico-high-fast-1.0")
    assert fast["fast"] and fast["price"]["input"] == 10 and fast["model"] == "Claude Opus 5.5 (fast mode)"
    assert "department" not in fast and fast["level"] == "high"
    assert T.resolve("tico-medium-9.9") is None and T.resolve("tico-nowhere-low") is None
    assert T.resolve("claude-opus-5-5") is None
    # Any id prices on lookup, though the model list holds only each tier's current version.
    assert providers.PRICES["tico-medium"] == providers.PRICES["tico-medium-1.0"] == (2.5, 0.25, 12.5)
    assert "tico-medium" not in [row["id"] for row in providers.MODEL_CATALOG]
    assert providers.MODEL_BY_ID["tico-medium"]["tier"]["auto"] is True


def _doc(**change):
    doc = json.loads(json.dumps(T.SNAPSHOT))
    doc.update(change)
    return doc


def _opener(body):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, limit=-1):
            return body if isinstance(body, bytes) else json.dumps(body).encode()

    return lambda request, timeout: Response()


def test_a_malformed_refresh_is_ignored_and_a_good_one_is_used_for_an_hour():
    before = T.catalog()
    for bad in (
        b"not json",
        b"[]",
        json.dumps({"schema": 2}).encode(),
        json.dumps(_doc(schema=1)).encode(),
        json.dumps(_doc(models=[{"id": "x"}, 5])).encode(),
        b"x" * (T.MAX_BYTES + 5),
    ):
        assert T.refresh("https://gateway.example.test/v1/gateway", _opener(bad), now=1000.0, force=True) is before
    # One bad tier is dropped, the good ones stay; a version with a text price is dropped with its tier.
    doc = _doc()
    doc["models"][0]["versions"][0]["price"] = {"input": "free", "output": 1, "cached_input": 0}
    kept = T.validate(doc)
    assert kept and len(kept["models"]) == len(T.SNAPSHOT["models"]) - 1
    # A newer version becomes the current one for auto ids, and the old pin stays valid.
    doc = _doc()
    tier = next(row for row in doc["models"] if row["id"] == "tico-medium")
    tier["versions"].append(
        {**tier["versions"][0], "version": "1.1", "id": "tico-medium-1.1", "model": "Claude Sonnet 5.6"}
    )
    tier["current"] = "1.1"
    got = T.refresh("https://gateway.example.test/v1/gateway", _opener(doc), now=2000.0, force=True)
    assert got is not before
    assert T.resolve("tico-medium")["version"] == "1.1" and T.resolve("tico-medium-1.0")["version"] == "1.0"
    assert T.default_model() == "tico-medium-1.1"
    # Within the hour it does not ask again; a failed ask later leaves what it has.
    assert T.refresh("https://gateway.example.test/v1/gateway", _opener(b"nope"), now=2100.0) is got
    assert (
        T.refresh("https://gateway.example.test/v1/gateway", _opener(b"nope"), now=2000.0 + T.REFRESH_SECONDS + 1)
        is got
    )


def test_a_bot_takes_its_bot_yaml_gateway_block_and_medium_when_it_has_none():
    assert T.DEFAULT_TIER == "tico-medium" and T.default_model() == "tico-medium-1.0"
    assert T.bot_model() == "tico-medium-1.0"
    assert T.bot_model({"level": "low", "fast": True}) == "tico-low-fast-1.0"
    assert T.bot_model({"level": "xhigh"}) == "tico-xhigh-1.0" and T.bot_model({"level": "xlow"}) == "tico-xlow-1.0"
    assert T.bot_model({"level": "extreme", "fast": "yes"}) == "tico-medium-1.0"
    assert T.bot_model("not a block") == "tico-medium-1.0"
    assert not hasattr(T, "department_of") and "departments" not in T.SNAPSHOT


def test_there_is_no_decisions_level_to_pick(api):
    """Every decision is Jev's (Decide Low): the providers view offers no level, and a save naming one is refused."""
    view = get(api, "providers")
    assert "decide_level" not in view and "decide_levels" not in view
    body = {
        "enabled": view["enabled"],
        "model": view["default"]["model"],
        "expected_revision": view["revision"],
        "decide_level": "high",
    }
    assert api.put("/api/v2/providers", json=body, headers=headers("ana-test")).status_code == 422


def test_an_agent_reads_the_lineup_and_asks_for_a_search_in_the_gateways_shape(api, monkeypatch):
    high = get(api, "gateway/catalog?query=high")["models"]
    assert {row["id"] for row in high} == {"tico-high", "tico-high-fast", "tico-xhigh", "tico-xhigh-fast"}
    assert all(row["pinned"].endswith("-1.0") and row["when"] for row in high)
    assert [row["id"] for row in get(api, "gateway/catalog")["models"]][:2] == ["tico-xlow", "tico-xlow-fast"]
    assert not get(api, "gateway/catalog?query=classify")["services"]
    assert {row["id"] for row in get(api, "gateway/catalog?query=search")["services"]} == {"search"}
    assert get(api, "gateway/catalog")["note"].startswith("A model id without a version")
    sent = {}

    def post(url, body, headers, timeout, opener):
        sent.update(url=url, body=body, headers=headers)
        return (
            200,
            {
                "results": [{"title": "T", "url": "https://example.test", "snippet": "S", "extra": 1}],
                "answer": "A",
                "usage": {"cost_usd": 0.01},
                "model": "tico-search-1.0",
            },
            "",
        )

    monkeypatch.setattr(G, "_post", post)
    status, answer, code = G.search(
        "https://gateway.example.test/v1/gateway", "tgw-key", "q" * 600, 50, {"X-Tico-Bot": "ops"}
    )
    assert (status, code) == (200, "")
    assert sent["url"].endswith("/search") and sent["body"] == {"query": "q" * 500, "max_results": 10}
    assert (
        "filters" not in sent["body"]
        and sent["headers"]["Authorization"] == "Bearer tgw-key"
        and sent["headers"]["X-Tico-Bot"] == "ops"
    )
    assert answer == {
        "results": [{"title": "T", "url": "https://example.test", "snippet": "S"}],
        "answer": "A",
        "usage": {"cost_usd": 0.01},
    }
    # Not connected: the route says so rather than calling anyone.
    refused = api.post("/api/v2/gateway/search", json={"query": "x"}, headers=headers("ana-test"))
    assert refused.status_code == 409


def test_an_agent_reads_a_page_in_the_gateways_shape(api, monkeypatch):
    assert {row["id"] for row in get(api, "gateway/catalog?query=fetch")["services"]} == {"fetch"}
    sent = {}

    def post(url, body, headers, timeout, opener):
        sent.update(url=url, body=body, headers=headers, timeout=timeout)
        return (
            200,
            {
                "model": "tico-fetch-1.0",
                "url": "https://example.test/final",
                "title": "T",
                "content": "# Page\n" + "x" * 300000,
                "usage": {"cost_usd": 0.002},
                "provider": "parallel",
            },
            "",
        )

    monkeypatch.setattr(G, "_post", post)
    status, answer, code = G.fetch(
        "https://gateway.example.test/v1/gateway",
        "tgw-key",
        "https://example.test/" + "a" * 2100,
        99999,
        {"X-Tico-Bot": "ops"},
    )
    assert (status, code) == (200, "")
    assert sent["url"] == "https://gateway.example.test/v1/gateway/fetch" and sent["timeout"] == 35
    assert sent["body"] == {"url": ("https://example.test/" + "a" * 2100)[:2000], "max_tokens": 50000}
    assert (
        "filters" not in sent["body"]
        and sent["headers"]["Authorization"] == "Bearer tgw-key"
        and sent["headers"]["X-Tico-Bot"] == "ops"
    )
    assert set(answer) == {"url", "title", "content", "usage"} and answer["url"] == "https://example.test/final"
    assert answer["title"] == "T" and answer["content"].startswith("# Page\n") and len(answer["content"]) == 250000
    assert answer["usage"] == {"cost_usd": 0.002}
    G.fetch("https://gateway.example.test/v1/gateway", "k", "https://example.test", 5)
    assert sent["body"]["max_tokens"] == 1000
    monkeypatch.setattr(G, "_post", lambda *a: (422, {}, "invalid"))
    assert G.fetch("https://gateway.example.test/v1/gateway", "k", "https://example.test") == (422, {}, "invalid")


def test_the_fetch_route_forwards_the_url_and_answers_the_gateways_shape(api, monkeypatch):
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url = "gw-synthetic", "https://hq.example/v1/gateway/decisions"
    calls = []

    def fetch(base, key, url, max_tokens, header):
        calls.append((base, key, url, max_tokens, header))
        return 200, {"url": url, "title": "Pricing", "content": "text", "usage": {"cost_usd": 0.00125}}, ""

    monkeypatch.setattr(G, "fetch", fetch)
    result = api.post(
        "/api/v2/gateway/fetch",
        json={"url": "https://example.test/pricing", "max_tokens": 2000},
        headers=headers("ana-test"),
    )
    assert result.status_code == 200, result.text
    assert result.json() == {
        "url": "https://example.test/pricing",
        "title": "Pricing",
        "content": "text",
        "usage": {"cost_usd": 0.00125},
    }
    assert calls[0][2:4] == ("https://example.test/pricing", 2000) and calls[0][1] == "gw-synthetic"
    api.post("/api/v2/gateway/fetch", json={"url": "http://example.test/"}, headers=headers("ana-test"))
    assert calls[1][2:4] == ("http://example.test/", 10000)
    # The Gateway's refusals come back as plain problems.
    for status, code, want_status, want in (
        (404, "404", 503, "gateway_fetch_unavailable"),
        (422, "invalid", 422, "gateway_fetch_failed"),
        (429, "cap_reached", 503, "gateway_fetch_failed"),
        (0, "unreachable", 503, "gateway_fetch_failed"),
    ):
        monkeypatch.setattr(G, "fetch", lambda *args, status=status, code=code: (status, {}, code))
        refused = api.post("/api/v2/gateway/fetch", json={"url": "https://example.test/"}, headers=headers("ana-test"))
        assert (refused.status_code, refused.json()["error"]["code"]) == (want_status, want), refused.text


def test_the_fetch_route_checks_its_input_and_needs_a_connected_gateway(api, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A refused request must not reach the Gateway")

    monkeypatch.setattr(G, "_post", forbidden)
    for body in (
        {},
        {"url": ""},
        {"url": "ftp://example.test/"},
        {"url": "example.test"},
        {"url": "https://" + "a" * 2000},
        {"url": "https://example.test/", "max_tokens": 999},
        {"url": "https://example.test/", "max_tokens": 50001},
        {"url": "https://example.test/", "filters": {}},
    ):
        assert api.post("/api/v2/gateway/fetch", json=body, headers=headers("ana-test")).status_code == 422, body
    refused = api.post("/api/v2/gateway/fetch", json={"url": "https://example.test/"}, headers=headers("ana-test"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "gateway_not_connected"


def test_the_fetch_tool_asks_the_route_with_the_url_and_a_default_size(api, monkeypatch):
    from backend.tests.helpers.mcp import call
    from clients import hubtools

    tool = hubtools.BY_NAME["tico_gateway_fetch"]
    assert (
        tool["inputSchema"]["required"] == ["url"]
        and tool["inputSchema"]["properties"]["max_tokens"]["maximum"] == 50000
    )
    assert "login" in tool["description"] and "other" in tool["description"]
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url = "gw-synthetic", "https://hq.example/v1/gateway/decisions"
    asked = []
    monkeypatch.setattr(
        G,
        "fetch",
        lambda base, key, url, max_tokens, header: (
            asked.append((url, max_tokens))
            or (200, {"url": url, "title": "T", "content": "C", "usage": {"cost_usd": 0.00125}}, "")
        ),
    )
    error, out = call(api, "tico_gateway_fetch", {"url": "https://example.test/a"})
    assert not error and out == {
        "url": "https://example.test/a",
        "title": "T",
        "content": "C",
        "usage": {"cost_usd": 0.00125},
    }
    error, out = call(api, "tico_gateway_fetch", {"url": "https://example.test/b", "max_tokens": 3000})
    assert not error and asked == [("https://example.test/a", 10000), ("https://example.test/b", 3000)]
    error, out = call(api, "tico_gateway_fetch", {"url": "file:///etc/passwd"})
    assert error and len(asked) == 2


def test_what_a_model_reads_comes_from_the_catalog_and_is_never_guessed():
    assert T.resolve("tico-medium")["inputs"] == T.resolve("tico-medium-1.0")["inputs"] == ["text", "image", "pdf"]
    assert T.resolve("tico-low-fast-1.0")["inputs"] == T.resolve("tico-xlow-1.0")["inputs"] == ["text"]
    row = next(r for r in T.SNAPSHOT["models"] if r["id"] == "tico-medium")
    cleaned = T.validate(
        {
            **T.SNAPSHOT,
            "models": [{**row, "versions": [{**row["versions"][0], "inputs": ["image", "image", "video", "text", 3]}]}],
        }
    )
    assert cleaned["models"][0]["versions"][0]["inputs"] == ["image", "text"]
    # None, or none that are known: not claimed at all, and the id reads as text only.
    for odd in (None, [], ["video"], "image"):
        bare = {k: v for k, v in row["versions"][0].items() if k != "inputs"}
        cleaned = T.validate(
            {**T.SNAPSHOT, "models": [{**row, "versions": [{**bare, **({"inputs": odd} if odd is not None else {})}]}]}
        )
        assert "inputs" not in cleaned["models"][0]["versions"][0]
        assert T.resolve("tico-medium-1.0", cleaned)["inputs"] == ["text"]


def test_a_rehearsal_server_searches_nothing_even_with_a_gateway_key(api, monkeypatch):
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url, settings.rehearsal = (
        "gw-synthetic",
        "https://hq.example/v1/gateway/decisions",
        True,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("A rehearsal must not call the Gateway")

    monkeypatch.setattr(G, "_post", forbidden)
    monkeypatch.setattr(T, "refresh", forbidden)
    result = api.post("/api/v2/gateway/search", json={"query": "acme pricing"}, headers=headers("ana-test"))
    assert result.status_code == 503 and result.json()["error"]["code"] == "rehearsal"
    result = api.post("/api/v2/gateway/fetch", json={"url": "https://example.test/"}, headers=headers("ana-test"))
    assert result.status_code == 503 and result.json()["error"]["code"] == "rehearsal"
    assert get(api, "gateway/catalog")["models"]


def test_a_saved_task_model_on_an_old_id_still_runs_but_a_retired_model_does_not(api):
    from backend import task_models

    store = api.app.state.store
    retired = next(row["id"] for row in providers.MODEL_CATALOG if row.get("deprecated"))
    with store.read() as c:
        for model, is_retired in (
            ("tico-medium-1.0", False),
            ("tico-mid-1.0", False),
            ("tico-sales-low-fast-1.0", False),
            (retired, True),
        ):
            choice = {"runtime": providers.MODEL_BY_ID[model]["runtime"], "model": model}
            why = task_models.why_not(c, store.settings, "ops", choice, "runner-1")
            assert ("is retired" in why) is is_retired, (model, why)


def _meeting_row(**change):
    """HQ's meeting tier as its `/catalog` answers it (gateway/catalog.py), with `change` applied."""
    row = {
        "id": "tico-meeting",
        "level": None,
        "fast": False,
        "name": "Meeting",
        "current": "1.0",
        "versions": [
            {
                "version": "1.0",
                "id": "tico-meeting-1.0",
                "model": "Inception Mercury 3",
                "inputs": ["text"],
                "price": {"input": 0.30, "output": 1.05, "cached_input": 0.03},
                "long_prompt": None,
                "context": 1000000,
                "max_output": 128000,
                "released": "2026-10-29",
            }
        ],
    }
    row.update(change)
    return row


def test_the_meeting_tier_as_hq_serves_it_is_kept_beside_the_level_tiers():
    doc = {
        **T.SNAPSHOT,
        "models": [row for row in T.SNAPSHOT["models"] if row["id"] != "tico-meeting"] + [_meeting_row()],
    }
    cat = T.validate(json.loads(json.dumps(doc)))
    meeting = next(row for row in cat["models"] if row["id"] == "tico-meeting")
    assert meeting["level"] is None and meeting["fast"] is False and "department" not in meeting
    assert meeting["versions"][0]["context"] == 1000000 and meeting["versions"][0]["inputs"] == ["text"]
    found = T.resolve("tico-meeting-1.0", cat)
    assert found["kind"] == "chat" and found["inputs"] == ["text"] and found["level"] is None
    assert found["price"] == {"input": 0.30, "output": 1.05, "cached_input": 0.03}
    assert T.row_for("tico-meeting-1.0")["efforts"][0] == "low" and T.row_for("tico-meeting")["default_effort"] == "low"


@pytest.mark.parametrize("change", [{"level": "low"}, {"fast": True}, {"fast": None}, {"id": "tico-meeting-x"}])
def test_a_malformed_meeting_row_is_left_out_and_the_release_copy_stands(change):
    doc = {
        **T.SNAPSHOT,
        "models": [row for row in T.SNAPSHOT["models"] if row["id"] != "tico-meeting"] + [_meeting_row(**change)],
    }
    cat = T.validate(json.loads(json.dumps(doc)))
    meetings = [row for row in cat["models"] if row["id"] == "tico-meeting"]
    assert len(meetings) == 1 and meetings[0]["level"] is None and meetings[0]["fast"] is False


def test_the_meeting_tier_is_a_meeting_model_not_a_bots_pick():
    assert [row["id"] for row in T.meeting_rows()] == ["tico-meeting-1.0"] and T.meeting_rows()[0]["meeting"] is True
    assert not any(row["id"].startswith("tico-meeting") for row in T.listed_rows())
    assert "tico-meeting-1.0" in {row["id"] for row in providers.MODEL_CATALOG}
    assert "tico-meeting-1.0" not in {row["id"] for row in providers.provider_models("tico")}
    assert "tico-meeting-1.0" in {row["id"] for row in providers.provider_models("tico", meeting=True)}
    assert "tico-meeting" not in T.view()["tiers"]


def _tier_running_a_held_model(current_held=True):
    """HQ's first level tier with one more version that runs the held Terra model (synthetic; no HQ data)."""
    doc = json.loads(json.dumps(T.SNAPSHOT))
    row = next(r for r in doc["models"] if r.get("level"))
    held = dict(row["versions"][-1], version="2.0", id=f"{row['id']}-2.0", model="GPT-6.1 Terra")
    row["versions"].append(held)
    if current_held:
        row["current"] = "2.0"
    return T.validate(doc), row


def test_a_live_tier_running_the_held_model_is_left_out_of_every_gateway_view():
    """Dev 693: once HQ publishes a tier on the held model, the picker, the lineup and the resolver never offer it."""
    cat, row = _tier_running_a_held_model(current_held=True)
    assert row["id"] not in T.view(cat)["tiers"]
    assert row["id"] not in [m["id"] for m in T.lineup(cat=cat)["models"]]
    assert T.resolve(row["id"], cat) is None and T.resolve(f"{row['id']}-2.0", cat) is None
    assert T.row_for(f"{row['id']}-2.0") is None


def test_a_held_pinned_version_is_left_out_but_the_tier_stays_on_its_current_version():
    cat, row = _tier_running_a_held_model(current_held=False)
    assert T.resolve(f"{row['id']}-2.0", cat) is None
    assert T.resolve(row["id"], cat)["version"] == row["current"]
    assert "2.0" not in [v["version"] for v in T.view(cat)["tiers"][row["id"]]["versions"]]
    assert [m["pinned"] for m in T.lineup(cat=cat)["models"] if m["id"] == row["id"]] == [
        f"{row['id']}-{row['current']}"
    ]


def test_a_held_pinned_id_is_refused_by_the_model_check(monkeypatch):
    cat, row = _tier_running_a_held_model(current_held=True)
    monkeypatch.setattr(T, "catalog", lambda: cat)
    with pytest.raises(providers.ProviderError):
        providers.complete_choice(["tico"], model=f"{row['id']}-2.0")
    assert providers.complete_choice(["tico"], model=f"{row['id']}-{T.FIRST_VERSION}") == (
        "pi",
        f"{row['id']}-{T.FIRST_VERSION}",
    )


def test_a_services_details_come_through_and_an_older_hqs_rows_have_none():
    """The Gateway card shows each service's details (label and value pairs); a row from an older HQ has none."""
    hq = {
        **T.SNAPSHOT,
        "services": json.loads((Path(__file__).parent / "fixtures" / "gateway_catalog_hq.json").read_text())[
            "services"
        ],
    }
    rows = {row["id"]: row for row in T.validate(hq)["services"]}
    assert rows["search"]["details"][-1] == {"label": "Engines", "value": "Exa, then Parallel"}
    assert rows["email.send"]["details"] == []
    old = [{k: v for k, v in row.items() if k != "details"} for row in hq["services"]]
    old[0]["details"] = [{"label": "Low", "value": "Jev"}, {"label": 3, "value": "x"}, "odd", {"label": "No value"}]
    cleaned = T.validate({**hq, "services": old})["services"]
    assert cleaned[0]["details"] == [{"label": "Low", "value": "Jev"}] and all(
        row["details"] == [] for row in cleaned[1:]
    )
    # The release's copy has the same shape, with this app's own tool names.
    assert all(isinstance(row["details"], list) and row["for"] for row in T.SNAPSHOT["services"])
    assert next(row for row in T.SNAPSHOT["services"] if row["id"] == "mail.physical")["tools"] == [
        "tico_mail_list",
        "tico_mail_read",
    ]


def test_settings_gateway_models_leave_out_a_tier_that_runs_the_held_model():
    from backend import gateway_page

    cat, _ = _tier_running_a_held_model(current_held=True)
    assert "GPT-6.1 Terra" not in json.dumps(gateway_page.models(cat))


def test_a_held_medium_does_not_leave_a_new_bot_without_a_model(monkeypatch):
    """Medium is the company's fallback model; if HQ puts a held model on it, the fallback is another live tier."""
    doc = json.loads(json.dumps(T.SNAPSHOT))
    medium = T.DEFAULT_TIER
    row = next(r for r in doc["models"] if r["id"] == medium)
    row["versions"].append(dict(row["versions"][-1], version="2.0", id=f"{medium}-2.0", model="GPT-6.1 Terra"))
    row["current"] = "2.0"
    cat = T.validate(doc)
    monkeypatch.setattr(T, "catalog", lambda: cat)
    fallback = T.default_model()
    assert fallback != f"{medium}-{T.FIRST_VERSION}" and T.resolve(fallback, cat) is not None
    assert T.resolve(fallback)["tier"] != medium


def test_a_held_high_keeps_the_medium_fallback_for_a_new_bot(monkeypatch):
    """Only a held Medium moves the fallback; a held High keeps the company's Medium default."""
    doc = json.loads(json.dumps(T.SNAPSHOT))
    high = next(r for r in doc["models"] if r["id"] == T.tier_id("high"))
    high["versions"].append(dict(high["versions"][-1], version="2.0", id=f"{high['id']}-2.0", model="GPT-6.1 Terra"))
    high["current"] = "2.0"
    cat = T.validate(doc)
    monkeypatch.setattr(T, "catalog", lambda: cat)
    assert T.default_model("high") == f"{T.DEFAULT_TIER}-{T.FIRST_VERSION}"
