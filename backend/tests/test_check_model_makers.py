"""A review's check model skips the makers who built the work, not the reviewer, and a Tico AI Gateway tier counts as
the maker of the model it runs underneath (#633): an Opus build reviewed by a GPT bot gets the company's GPT check
model, never Tico Medium (Claude Sonnet underneath)."""

from backend import bot_settings as BS, gateway_tiers, providers, usage_records
from backend.store import encode
from backend.tests.helpers.api import post
from backend.tests.helpers.check_models import check_row, computer, parent_turn
from backend.tests.usage_rows import segment


def test_a_gateway_tier_is_the_maker_of_the_model_underneath():
    assert gateway_tiers.maker_of("Claude Sonnet 5.5") == "anthropic"
    assert gateway_tiers.maker_of("Inception Mercury 3") == "inception"
    assert gateway_tiers.maker_of("DeepSeek V4 Flash") == "deepseek"
    assert gateway_tiers.maker_of("Something New 1") == ""
    assert providers.maker("tico-medium-1.0") == "anthropic"
    assert providers.maker("tico-medium") == "anthropic"
    assert providers.maker("gpt-6.2-sol") == "openai"
    assert providers.author_provider("tico-medium-1.0") == "anthropic"
    # What a run reports as its maker (ran_on's family) is the same.
    assert usage_records.family({"model": "tico-medium-1.0"}) == "anthropic"


def test_check_choice_skips_a_gateway_tier_on_the_authors_own_maker():
    record = {
        "enabled": ["anthropic", "openai", "tico"],
        "runtime": "claude",
        "model": "claude-opus-5-5",
        "check_models": ["pi:tico-medium-1.0", "codex:gpt-6.2-sol"],
    }
    order = providers.check_order(record)
    assert [r["maker"] for r in order if r["model"] == "tico-medium-1.0"] == ["anthropic"]
    assert providers.check_choice(record, "claude-opus-5-5")["model"] == "gpt-6.2-sol"


def run(api, task_id, run_id, bot, model, provider, runtime):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempts(id,bot_id,created_at,settled_at,task_id) VALUES(?,?,'2026-01-01T00:00:00Z',"
            "'2026-01-01T00:05:00Z',?)",
            (run_id, bot, task_id),
        )
        segment(
            c,
            run_id,
            input_tokens=10,
            output_tokens=10,
            model=model,
            provider=provider,
            runtime=runtime,
            harness=runtime,
        )


def test_a_gpt_reviewer_of_an_opus_build_gets_the_companys_gpt_check_model(api):
    machine, parent, turn = parent_turn(api)
    # The reviewer (ops) runs on GPT; the work it reviews was built on Opus by another bot.
    with api.app.state.store.transaction() as c:
        config = BS.read(c, "ops")
        config.update(runtime="codex", harness="codex", model="gpt-6.1-terra", fallback={"off": True})
        BS.write(c, "ops", config)
        c.execute(
            "UPDATE registry_metadata SET value=? WHERE key='providers'",
            (
                encode(
                    {
                        "revision": 1,
                        "enabled": ["anthropic", "openai", "xai"],
                        "runtime": "claude",
                        "model": "claude-opus-5-5",
                        "check_models": ["codex:gpt-6.2-sol"],
                    }
                ),
            ),
        )
    run(api, parent["id"], "built", "cpo", "claude-opus-5-5", "anthropic", "claude")
    run(api, parent["id"], "read", "ops", "gpt-6.1-terra", "openai", "codex")
    computer(api, machine)

    def file(role, title):
        return post(
            api,
            "tasks",
            {
                "owner": "ops",
                "title": title,
                "body": "Read the PR.",
                "check_model": "auto",
                "check_role": role,
                "parent": parent["id"],
            },
            turn["token"],
        )

    review = file("review", "Second review: PR 700")
    assert tuple(check_row(api, review["id"])) == ("codex", "gpt-6.2-sol", "review")
    # A check of the bot's own work still counts its own runs: GPT and Claude both wrote it, so Grok checks it.
    check = file("check", "Check my own part")
    assert tuple(check_row(api, check["id"]))[:2] == ("grok", "grok-4.6")
