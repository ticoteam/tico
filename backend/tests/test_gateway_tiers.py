"""The Tico AI Gateway's picks (backend/gateway_tiers.py): ids, the catalog refresh, a bot's department, the Decisions level
and the lineup an agent reads. Fakes only: no call leaves the test."""
import json

import pytest

from backend import gateway_tiers as T
from backend import providers
from backend.tests.test_api import api, get, headers  # noqa: F401
from clients import gateway as G
from clients import judge as J


@pytest.fixture(autouse=True)
def fresh_catalog():
    T.reset()
    yield
    T.reset()


def test_ids_resolve_to_a_tier_a_version_and_a_price():
    auto = T.resolve("tico-sales-medium")
    pinned = T.resolve("tico-sales-medium-1.0")
    assert (auto["auto"], pinned["auto"]) == (True, False)
    assert auto["version"] == pinned["version"] == "1.0" and auto["tier"] == pinned["tier"] == "tico-sales-medium"
    assert pinned["model"] == "Claude Sonnet 5.5" and pinned["price"] == {"input": 2.5, "output": 12.5, "cached_input": 0.25}
    fast = T.resolve("tico-general-high-fast-1.0")
    assert fast["fast"] and fast["price"]["input"] == 10 and fast["model"] == "Claude Opus 5.5 (fast mode)"
    assert T.resolve("tico-sales-medium-9.9") is None and T.resolve("tico-nowhere-low") is None
    assert T.resolve("claude-opus-5-5") is None
    # Any id prices on lookup, though the model list holds only each tier's current version.
    assert providers.PRICES["tico-sales-medium"] == providers.PRICES["tico-sales-medium-1.0"] == (2.5, 0.25, 12.5)
    assert "tico-sales-medium" not in [row["id"] for row in providers.MODEL_CATALOG]
    assert providers.MODEL_BY_ID["tico-sales-medium"]["tier"]["auto"] is True


def test_the_old_lineup_still_resolves_at_its_old_price_and_is_never_offered():
    for old, level in (("tico-cheap-1.0", "low"), ("tico-cheap-1.1", "low"), ("tico-mid-1.0", "medium"), ("tico-power-1.0", "high")):
        found = T.resolve(old)
        assert found["old"] and found["level"] == level and found["name"].endswith("(old lineup)")
        row = providers.MODEL_BY_ID[old]
        assert row["deprecated"] and row["lineup"] == "old"
    assert providers.PRICES.get("tico-mid-1.0") == (2.5, 0.25, 12.5)
    assert T.resolve("tico-decide-1.0")["old"] and T.resolve("tico-classify-1.0")["old"]
    assert not any(row["id"] in T.OLD and not row.get("deprecated") for row in providers.MODEL_CATALOG)
    assert len([row for row in providers.MODEL_CATALOG if row["id"].startswith("tico-") and not row.get("deprecated")]) == 60


def _doc(**change):
    doc = json.loads(json.dumps({**T.SNAPSHOT, "schema": 1}))
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
    for bad in (b"not json", b"[]", json.dumps({"schema": 2}).encode(), json.dumps(_doc(models=[{"id": "x"}, 5])).encode(),
                b"x" * (T.MAX_BYTES + 5)):
        assert T.refresh("https://gateway.example.test/v1/gateway", _opener(bad), now=1000.0, force=True) is before
    # One bad tier is dropped, the good ones stay; a version with a text price is dropped with its tier.
    doc = _doc()
    doc["models"][0]["versions"][0]["price"] = {"input": "free", "output": 1, "cached_input": 0}
    kept = T.validate(doc)
    assert kept and len(kept["models"]) == len(T.SNAPSHOT["models"]) - 1
    # A newer version becomes the current one for auto ids, and the old pin stays valid.
    doc = _doc()
    tier = next(row for row in doc["models"] if row["id"] == "tico-sales-medium")
    tier["versions"].append({**tier["versions"][0], "version": "1.1", "id": "tico-sales-medium-1.1", "model": "Claude Sonnet 5.6"})
    tier["current"] = "1.1"
    got = T.refresh("https://gateway.example.test/v1/gateway", _opener(doc), now=2000.0, force=True)
    assert got is not before
    assert T.resolve("tico-sales-medium")["version"] == "1.1" and T.resolve("tico-sales-medium-1.0")["version"] == "1.0"
    assert T.default_model("sales") == "tico-sales-medium-1.1"
    # Within the hour it does not ask again; a failed ask later leaves what it has.
    assert T.refresh("https://gateway.example.test/v1/gateway", _opener(b"nope"), now=2100.0) is got
    assert T.refresh("https://gateway.example.test/v1/gateway", _opener(b"nope"), now=2000.0 + T.REFRESH_SECONDS + 1) is got


def test_a_bot_takes_its_departments_tier_and_its_bot_yaml_gateway_block():
    assert [T.department_of(g) for g in ("sales", "support", "hr", "leadership", "engineering", "", None, "weird")] == [
        "sales", "operations", "operations", "general", "engineering", "general", "general", "general"]
    assert T.default_model("sales") == "tico-sales-medium-1.0"
    assert T.default_model("nowhere") == "tico-general-medium-1.0"
    assert T.bot_model("operations", {"level": "low", "fast": True}) == "tico-operations-low-fast-1.0"
    assert T.bot_model("engineering", {"level": "high"}) == "tico-engineering-high-1.0"
    assert T.bot_model("sales", {"level": "extreme", "fast": "yes"}) == "tico-sales-medium-1.0"
    assert T.bot_model("sales", "not a block") == "tico-sales-medium-1.0"


def test_a_bot_with_no_model_runs_on_its_groups_tier(api):
    from backend import models_preset
    with api.app.state.store.read() as c:
        # No group: General. The config's own gateway block still picks the level.
        assert models_preset.model_for_bot(c, api.app.state.store.settings, "ops", {}) == "tico-general-medium-1.0"
        assert models_preset.model_for_bot(c, api.app.state.store.settings, "ops", {"gateway": {"level": "high"}}) == "tico-general-high-1.0"
    assert models_preset.pick("tico-sales-low-1.0") == "tico-sales-low-1.0" and models_preset.pick(lambda: "tico-x") == "tico-x"


def test_the_decisions_level_picks_the_decision_model_and_how_long_it_may_take():
    assert [J.decide_model(level) for level in ("low", "medium", "high")] == [
        "tico-decide-1.0", "tico-decide-medium-1.0", "tico-decide-high-1.0"]
    assert [J.decide_timeout(level) for level in ("low", "medium", "high")] == [8, 30, 30]
    # Anything else is Low, exactly as before the levels.
    assert (J.normalize_level(None), J.normalize_level("MEDIUM "), J.normalize_level("extreme")) == ("low", "medium", "low")
    assert J.decide_model("extreme") == "tico-decide-1.0" and J.decide_timeout(None) == 8


def test_the_company_saves_a_decisions_level_and_old_settings_read_as_low(api):
    view = get(api, "providers")
    assert view["decide_level"] == "low" and view["decide_levels"] == ["low", "medium", "high"]
    body = {"enabled": view["enabled"], "model": view["default"]["model"], "expected_revision": view["revision"], "decide_level": "high"}
    assert api.put("/api/v2/providers", json=body, headers=headers("ana-test")).status_code == 200
    assert get(api, "providers")["decide_level"] == "high"
    bad = api.put("/api/v2/providers", json={**body, "expected_revision": get(api, "providers")["revision"], "decide_level": "extreme"},
                  headers=headers("ana-test"))
    assert bad.status_code == 422


def test_an_agent_reads_the_lineup_and_asks_for_a_search_in_the_gateways_shape(api, monkeypatch):
    marketing = get(api, "gateway/catalog?query=marketing")["models"]
    assert len(marketing) == 6 and {"tico-marketing-low-fast", "tico-marketing-high-fast"} <= {row["id"] for row in marketing}
    low = {"models": marketing}
    assert all(row["pinned"].endswith("-1.0") and row["when"] for row in low["models"])
    assert {row["id"] for row in get(api, "gateway/catalog?query=search")["services"]} == {"search"}
    assert get(api, "gateway/catalog")["note"].startswith("A model id without a version")
    sent = {}

    def post(url, body, headers, timeout, opener):
        sent.update(url=url, body=body, headers=headers)
        return 200, {"results": [{"title": "T", "url": "https://example.test", "snippet": "S", "extra": 1}], "answer": "A",
                     "usage": {"cost_usd": 0.01}, "model": "tico-search-1.0"}, ""
    monkeypatch.setattr(G, "_post", post)
    status, answer, code = G.search("https://gateway.example.test/v1/gateway", "tgw-key", "q" * 600, 50, {"X-Tico-Bot": "ops"})
    assert (status, code) == (200, "")
    assert sent["url"].endswith("/search") and sent["body"] == {"query": "q" * 500, "max_results": 10}
    assert "filters" not in sent["body"] and sent["headers"]["Authorization"] == "Bearer tgw-key" and sent["headers"]["X-Tico-Bot"] == "ops"
    assert answer == {"results": [{"title": "T", "url": "https://example.test", "snippet": "S"}], "answer": "A", "usage": {"cost_usd": 0.01}}
    # Not connected: the route says so rather than calling anyone.
    refused = api.post("/api/v2/gateway/search", json={"query": "x"}, headers=headers("ana-test"))
    assert refused.status_code == 409


def test_a_templates_gateway_block_holds_only_a_level_and_fast():
    import yaml
    from pathlib import Path
    seen = 0
    for path in sorted(Path("templates/catalog").glob("*/bot.yaml")):
        block = (yaml.safe_load(path.read_text()) or {}).get("gateway")
        if block is not None:
            seen += 1
            assert T.clean_block(block) == block, path
            assert T.bot_model("sales", block).startswith("tico-sales-" + block["level"])
    assert seen >= 5
    assert T.clean_block({"level": "extreme"}) is None and T.clean_block({"fast": False}) is None and T.clean_block("low") is None


def test_the_shipped_snapshot_matches_a_copy_of_the_gateways_own_catalog():
    """Prices and models cannot drift from what Tico HQ publishes: tiers (with long-prompt rates) and decision levels."""
    from pathlib import Path
    hq = json.loads((Path(__file__).parent / "fixtures" / "gateway_catalog_hq.json").read_text())
    ours = T.SNAPSHOT

    def versions(cat, kind):
        return {v["id"]: (v["model"], v["price"], v.get("long_prompt")) for row in cat[kind] for v in row["versions"]}
    assert versions(ours, "models") == versions(hq, "models") and len(versions(hq, "models")) == 60
    assert versions(ours, "decisions") == versions(hq, "decisions")
    assert {row["id"]: row["current"] for row in ours["models"]} == {row["id"]: row["current"] for row in hq["models"]}
    # Medium and High Decide are priced like their model, so no Decide call is counted at the ceiling.
    assert T.resolve("tico-decide-high-1.0")["price"] == {"input": 5.0, "output": 25.0, "cached_input": 0.5}


def test_every_tool_a_service_names_is_a_hub_tool():
    from clients import hubtools
    known = {entry["name"] for entry in hubtools.TOOLS}
    named = {name for service in T.SNAPSHOT["services"] for name in service["tools"]}
    assert {"hub_inbound_wait", "hub_gateway_search", "hub_decision_ask"} <= named and named <= known, named - known


def test_a_medium_or_high_decision_reserves_the_spend_of_the_model_that_will_run(api, monkeypatch):
    from backend import judge as B
    store = api.app.state.store
    store.settings.gateway_key, store.settings.gateway_url = "gw-synthetic", "https://hq.example/v1/gateway/decisions"
    view = get(api, "providers")
    body = {"enabled": view["enabled"], "model": view["default"]["model"], "decide_level": "low"}
    step = B.gateway(store.settings, store)
    seen = []
    real = B.reservation
    monkeypatch.setattr(B, "reservation", lambda s, models, *a: seen.append(list(models)) or real(s, models, *a))
    monkeypatch.setattr(B, "fallback_engine", lambda *args, **kwargs: None)
    for level, want in (("low", "tico-decide-1.0"), ("high", "tico-decide-high-1.0")):
        view = get(api, "providers")
        put = api.put("/api/v2/providers", json={**body, "decide_level": level, "expected_revision": view["revision"]},
                      headers=headers("ana-test"))
        assert put.status_code == 200, put.text
        assert B.step_models([step]) == [want]
        with store.read() as c:
            engine = B.server_engine(store, c, step)
        store.settings.decisions_monthly_usd = 50
        with pytest.raises(J.JudgeError):           # the fake host answers nothing; only the reservation matters
            engine({"team": "Acme"}, {"q": {"type": "choice", "question": "?", "criteria": {"a": "a", "b": "b"}}})
        assert seen[-1] == [want], seen


def test_a_rehearsal_server_searches_nothing_even_with_a_gateway_key(api, monkeypatch):
    settings = api.app.state.store.settings
    settings.gateway_key, settings.gateway_url, settings.rehearsal = "gw-synthetic", "https://hq.example/v1/gateway/decisions", True

    def forbidden(*args, **kwargs):
        raise AssertionError("A rehearsal must not call the Gateway")
    monkeypatch.setattr(G, "_post", forbidden)
    monkeypatch.setattr(T, "refresh", forbidden)
    result = api.post("/api/v2/gateway/search", json={"query": "acme pricing"}, headers=headers("ana-test"))
    assert result.status_code == 503 and result.json()["error"]["code"] == "rehearsal"
    assert get(api, "gateway/catalog")["models"]


def test_a_saved_task_model_on_the_old_lineup_still_runs_but_a_retired_model_does_not(api):
    from backend import task_models
    store = api.app.state.store
    retired = next(row["id"] for row in providers.MODEL_CATALOG if row.get("deprecated") and row.get("lineup") != "old")
    with store.read() as c:
        for model, is_retired in (("tico-mid-1.0", False), ("tico-cheap-1.1", False), ("tico-power-1.0", False), (retired, True)):
            choice = {"runtime": providers.MODEL_BY_ID[model]["runtime"], "model": model}
            why = task_models.why_not(c, store.settings, "ops", choice, "runner-1")
            assert ("is retired" in why) is is_retired, (model, why)

