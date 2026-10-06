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
    assert "Settings > AI providers" in refused.value.detail and refused.value.status == 409


def test_a_claimed_attempt_carries_the_company_default_for_a_bot_that_names_none(api):
    """Found by the Linux rehearsal: onboarding creates bots that follow the company default, and
    the runner got an empty runtime from the claim, so no turn ever started."""
    from backend.store import encode
    from backend.tests.test_api import claim, post, put, runner, assign, ready
    put(api, "providers", {"enabled": ["openai"], "runtime": "codex", "model": "gpt-6-luna", "expected_revision": 0})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (encode({"name": "ops", "status": "active"}),))
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "chat/ops", {"text": "Say hello."})
    config = claim(api, r)["config"]
    assert (config["runtime"], config["model"]) == ("codex", "gpt-6-luna")



@pytest.mark.parametrize("model,runtime", [("gpt-6-sol", "codex")])
def test_retired_models_remain_readable_but_cannot_be_new_choices(model, runtime):
    company = P._record({"enabled": ["openai", "anthropic"], "runtime": runtime, "model": model})
    assert P.resolve(company, {}) == (runtime, model)
    assert P.resolve(company, {"runtime": runtime, "model": model}) == (runtime, model)
    with pytest.raises(P.ProviderError) as refused:
        P.complete_choice(company["enabled"], runtime, model)
    assert refused.value.status == 422 and "retired" in refused.value.detail
