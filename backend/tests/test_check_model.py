"""The company's check model (backend/providers.py `check_choice`): what a review or check subtask runs on when it
names none. It follows the owner's order, then the other enabled providers, and never picks the provider the work
was written on."""

import pytest

from backend import providers as P
from backend.store import H
from backend.tests.test_api import api, get  # noqa: F401  (the fixture)

COMPANY = {"enabled": ["anthropic", "xai", "openai"], "runtime": "claude", "model": "claude-opus-5-5"}


def test_the_owners_order_wins_and_the_authors_provider_is_skipped():
    record = P._record({**COMPANY, "check_models": P.normalize_check_models("grok, codex:gpt-6.1-sol", COMPANY)})
    assert record["check_models"] == ["grok", "codex:gpt-6.1-sol"]
    assert P.normalize_check_models("grok, grok", COMPANY) == ["grok"]
    # The rest of the enabled providers follow the owner's order; a model named twice counts once.
    assert [row["model"] for row in P.check_order(record)] == ["grok-4.6", "gpt-6.1-sol", "claude-opus-5-5", "gpt-6-luna"]
    assert P.check_choice(record, "anthropic")["model"] == "grok-4.6"
    assert P.check_choice(record, "grok-4.6") == {"runtime": "codex", "model": "gpt-6.1-sol", "provider": "openai"}
    # Grok is not signed in on any computer: the next in order.
    assert P.check_choice(record, "claude", available={"codex", "claude"})["model"] == "gpt-6.1-sol"
    # A one-entry order still finds another provider for work written on that entry's provider.
    short = P._record({**COMPANY, "check_models": ["grok"]})
    assert P.check_choice(short, "xai")["provider"] == "anthropic"


def test_every_enabled_provider_counts_even_two_on_one_runtime():
    record = P._record({"enabled": ["moonshot", "deepseek"]})
    assert [row["provider"] for row in P.check_order(record)] == ["moonshot", "deepseek"]
    assert P.check_choice(record, P.check_order(record)[0]["model"])["provider"] == "deepseek"
    # A runtime alone rides the company default on that runtime.
    sonnet = {**COMPANY, "model": "claude-sonnet-5-5", "check_models": ["claude"]}
    assert P.check_order(P._record(sonnet))[0]["model"] == "claude-sonnet-5-5"


def test_stale_entries_are_skipped_and_never_fail_a_save():
    record = P._record({**COMPANY, "check_models": ["grok", "codex:gpt-6-sol"]})
    # gpt-6-sol is retired: skipped, and the runtime-only grok entry is no pin.
    assert [row["model"] for row in P.check_order(record)][:2] == ["grok-4.6", "claude-opus-5-5"]
    # A company default retired since it was saved is no check model; its provider's current one stands in.
    retired = P._record({**COMPANY, "model": "claude-opus-5"})
    assert [row["model"] for row in P.check_order(retired)][0] == "claude-opus-5-5"
    assert [row["model"] for row in P.check_order(P._record({**retired, "check_models": ["claude", "grok"]}))][:2] == \
        ["claude-opus-5-5", "grok-4.6"]
    assert P.normalize_check_models(["claude"], retired) == ["claude"]
    # A current company default leads its provider's place in the order, not the recommended model.
    assert P.check_order(P._record({**COMPANY, "model": "claude-sonnet-5-5"}))[0]["model"] == "claude-sonnet-5-5"
    off = {"enabled": ["anthropic", "openai"], "runtime": "claude", "model": "claude-opus-5-5"}
    assert P.normalize_check_models(["grok", "codex:gpt-6-sol"], off, saved=record["check_models"]) == \
        ["grok", "codex:gpt-6-sol"]
    assert "grok-4.6" not in [row["model"] for row in P.check_order(P._record({**off, "check_models": ["grok"]}))]
    alone = P._record({"enabled": ["anthropic"], "runtime": "claude", "model": "claude-opus-5-5", "check_models": ["grok"]})
    # Another provider is preferred, not required: with only the author's own on, it checks the work.
    assert P.check_choice(alone, "claude")["provider"] == "anthropic"
    with pytest.raises(P.ProviderError) as refused:
        P.check_choice(alone, "claude", available=set())
    assert refused.value.status == 409 and "No check model can run" in refused.value.detail


@pytest.mark.parametrize("choice", ["gemini", "claude-opus-5", "claude:", ":"])
def test_a_new_choice_the_company_cannot_run_is_refused(choice):
    with pytest.raises(P.ProviderError) as refused:
        P.normalize_check_models([choice], COMPANY)
    assert refused.value.code == "check_model" and refused.value.status == 422


def save(api, enabled=COMPANY["enabled"], **extra):
    view = get(api, "providers")
    return api.put("/api/v2/providers", json={"enabled": enabled, "model": COMPANY["model"],
                                               "expected_revision": view["revision"], **extra},
                   headers={"Authorization": "Bearer ana-test", "Idempotency-Key": H.new_id()})


def test_the_owner_saves_the_order_and_turning_a_listed_provider_off_still_saves(api):
    assert save(api).json()["check_models"] == []
    view = save(api, check_models=["grok", "codex"]).json()
    assert view["check_models"] == ["grok", "codex"]
    assert [row["model"] for row in view["check_order"]][:2] == ["grok-4.6", "gpt-6-luna"]
    assert save(api).json()["check_models"] == ["grok", "codex"]
    # xAI off, the saved order sent back as it was: kept, and Grok drops out of the order in effect.
    view = save(api, enabled=["anthropic", "openai"], check_models=["grok", "codex"]).json()
    assert view["check_models"] == ["grok", "codex"] and view["check_order"][0]["runtime"] == "codex"
    refused = save(api, check_models=["gemini"])
    assert refused.status_code == 422
    assert refused.json()["error"]["detail"] == "Check model 'gemini': its provider (google) is not enabled"
    with api.app.state.store.read() as c:
        event = c.execute("SELECT detail FROM events WHERE action='providers.updated' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert '"check_models"' in event[0]
