"""The provider choice (backend/providers.py): stored, revisioned, seeded from the environment,
and the only thing that decides a bot's runtime and model when the bot names none."""

import pytest

from backend import providers as P
from backend.tests.test_api import api  # noqa: F401  (the fixture)

def test_resolution_order_bot_then_company_then_first_provider_then_error():
    none = P._record({})
    company = P._record({"enabled": ["anthropic", "openai"], "runtime": "codex", "model": "gpt-6-sol"})
    assert P.resolve(company, {"runtime": "claude", "model": "claude-opus-5"}) == ("claude", "claude-opus-5")
    assert P.resolve(company, {"model": "claude-opus-5-5"}) == ("claude", "claude-opus-5-5")
    assert P.resolve(company, {"runtime": "default", "model": "default"}) == ("codex", "gpt-6-sol")
    assert P.resolve(company, {"runtime": "claude"}) == ("claude", "claude-opus-5-5")
    first = P._record({"enabled": ["google", "openai"]})
    assert P.resolve(first, {}) == ("gemini", "gemini-3.8-flash")
    with pytest.raises(P.NoProvider) as refused:
        P.resolve(none, {})
    assert "Settings > Computers" in refused.value.detail and refused.value.status == 409


def test_a_claimed_attempt_carries_the_company_default_for_a_bot_that_names_none(api):
    """Found by the Linux rehearsal: onboarding creates bots that follow the company default, and
    the runner got an empty runtime from the claim, so no turn ever started."""
    from backend.store import encode
    from backend.tests.test_api import claim, post, put, runner, assign, ready
    put(api, "providers", {"enabled": ["openai"], "runtime": "codex", "model": "gpt-6-luna", "expected_revision": 0})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (encode({"name": "ops", "status": "active"}),))
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Say hello."})
    config = claim(api, r)["config"]
    assert (config["runtime"], config["model"]) == ("codex", "gpt-6-luna")



def test_a_bot_set_to_haiku_5_5_at_xhigh_runs_that_model_at_that_effort(api):
    """A Settings model change is what the runner's next claim carries: `--model` and `--effort` come from it."""
    from backend.tests.test_api import claim, post, put, runner, assign, ready
    put(api, "providers", {"enabled": ["anthropic"], "runtime": "claude", "model": "claude-opus-5-5",
                           "expected_revision": 0})
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    post(api, "bots/ops/transitions", {"kind": "model", "model": "claude-haiku-5-5", "effort": "xhigh",
                                       "expected_revision": revision})
    post(api, "chat/ops", {"text": "Say hello."})
    config = claim(api, r)["config"]
    assert (config["runtime"], config["model"], config["reasoning_effort"]) == ("claude", "claude-haiku-5-5", "xhigh")
    assert P.price_of("claude-haiku-5-5") == (0.10, 0.01, 0.50)


@pytest.mark.parametrize("model,runtime", [("gpt-6-sol", "codex")])
def test_retired_models_remain_readable_but_cannot_be_new_choices(model, runtime):
    company = P._record({"enabled": ["openai", "anthropic"], "runtime": runtime, "model": model})
    assert P.resolve(company, {}) == (runtime, model)
    assert P.resolve(company, {"runtime": runtime, "model": model}) == (runtime, model)
    with pytest.raises(P.ProviderError) as refused:
        P.complete_choice(company["enabled"], runtime, model)
    assert refused.value.status == 422 and "retired" in refused.value.detail


def test_the_bot_list_says_the_effort_a_bot_runs_at_and_nothing_when_it_has_none(api):
    """The runtime mark's hover reads harness, model and effort ("Codex GPT 6.1 Sol High"): the bot's
    reasoning_effort, else an older config `effort`, else its stored row; none, or no runtime, sends none."""
    from backend.store import encode
    from backend.tests.test_api import put
    put(api, "providers", {"enabled": ["openai", "anthropic"], "runtime": "claude", "model": "claude-opus-5-5", "expected_revision": 0})
    configs = {"ops": ({"runtime": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "high", "effort": "low"}, "max"),
               "cpo": ({}, ""), "coo": ({"effort": "xhigh"}, "low"), "inbox": ({}, "medium")}
    with api.app.state.store.transaction() as c:
        for bot, (config, stored) in configs.items():
            c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode({"name": bot, "status": "active", **config}), bot))
            c.execute("UPDATE bots SET effort=? WHERE slug=?", (stored, bot))
    rows = {row["name"]: row for row in api.get("/api/employees", headers={"Authorization": "Bearer ana-test"}).json()}
    said = {bot: (rows[bot]["resolved_runtime"], rows[bot]["resolved_model"], rows[bot]["resolved_effort"]) for bot in configs}
    assert said == {"ops": ("codex", "gpt-6.1-sol", "high"), "cpo": ("claude", "claude-opus-5-5", ""),
                    "coo": ("claude", "claude-opus-5-5", "xhigh"), "inbox": ("claude", "claude-opus-5-5", "medium")}
    # A team with no provider: nothing resolves, so no effort is said either.
    put(api, "providers", {"enabled": [], "runtime": "", "model": "", "expected_revision": 1})
    rows = {row["name"]: row for row in api.get("/api/employees", headers={"Authorization": "Bearer ana-test"}).json()}
    assert (rows["cpo"]["resolved_runtime"], rows["cpo"]["resolved_effort"], rows["inbox"]["resolved_effort"]) == ("", "", "")


def test_gpt_6_1_terra_is_priced_at_the_approved_rate_and_held_from_every_pick(api):
    """Gated (Dev 693): the row is priced at Tico's rate, held, and refused by every pick and listing."""
    from backend.store import Problem
    from backend import task_models
    row = P.MODEL_BY_ID["gpt-6.1-terra"]
    assert row["held"] and row["deprecated"]
    assert P.price_of("gpt-6.1-terra") == (1.25, 0.062, 6.25)
    assert "gpt-6.1-terra" not in [r["id"] for r in P.provider_models("openai")]
    assert "gpt-6.1-terra" not in [r["id"] for r in P.provider_models("openai", current=False)]
    with pytest.raises(P.ProviderError):
        P.complete_choice(["openai"], model="gpt-6.1-terra")
    with pytest.raises(P.ProviderError):
        P.complete_choice(["openai"], model="gpt-6.1-terra", kept="gpt-6.1-terra")
    with pytest.raises(P.ProviderError):
        P.meeting_choice(P._record({"enabled": ["openai"]}), "gpt-6.1-terra")
    with pytest.raises(Problem):
        task_models.refuse_retired(row)
    listed = api.get("/api/v2/models", headers={"Authorization": "Bearer ana-test"}).json()["models"]
    assert "gpt-6.1-terra" not in [m["id"] for m in listed]


def test_a_live_gateway_tier_for_the_held_id_does_not_expose_it(monkeypatch):
    """Once Tico HQ publishes a Terra tier, the gateway's lookups must not make the held id pickable."""
    live = {"id": "gpt-6.1-terra", "label": "live", "runtime": "pi", "provider": "tico", "efforts": ("low",),
            "default_effort": "low", "tier": {"department": "x", "level": "low", "fast": False, "version": 1, "auto": False}}
    monkeypatch.setattr(P.gateway_tiers, "row_for", lambda model: live if model == "gpt-6.1-terra" else None)
    assert P.MODEL_BY_ID["gpt-6.1-terra"]["provider"] == "openai"
    with pytest.raises(P.ProviderError):
        P.complete_choice(["tico", "openai"], model="gpt-6.1-terra")
    assert "gpt-6.1-terra" not in [r["id"] for r in P.provider_models("tico")]


def test_mercury_on_openrouter_is_offered_only_as_a_meeting_model_and_priced():
    from clients import openrouter
    assert P.OPENROUTER_MEETING_MODEL == "mercury-3"
    assert "mercury-3" in [row["id"] for row in P.provider_models("openrouter", meeting=True)]
    assert "mercury-3" not in [row["id"] for row in P.provider_models("openrouter")]
    row = P.MODEL_BY_ID["mercury-3"]
    assert (row["runtime"], row["default_effort"], row["efforts"][0]) == ("pi", "low", "low")
    assert P.PRICES["mercury-3"] == (0.20, 0.02, 0.70)
    assert openrouter.MODELS["mercury-3"] == "inception/mercury-3"
