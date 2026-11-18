"""A Gateway model's maker is the maker of the model underneath (backend/providers.py `maker`): the Gateway sells
models, it does not make them. So a second opinion "from another maker" never picks Claude on the Gateway for work
Claude wrote (Nov 18: every second review on Tico Team ran on Gateway Sonnet at API prices as the "other maker")."""

from backend import providers


def test_a_gateway_model_counts_as_the_maker_underneath():
    assert providers.maker("tico-medium-1.0") == "anthropic"  # Claude Sonnet
    assert providers.maker("tico-xlow-1.0") == "deepseek"
    assert providers.maker("claude-opus-5-5") == "anthropic"
    assert providers.maker("no-such-model") == ""
    assert providers.author_provider("tico-medium-1.0") == "anthropic"


def test_a_check_of_claude_work_skips_claude_on_the_gateway():
    record = {
        "enabled": ["anthropic", "tico", "openai"],
        "model": None,
        "check_models": ["pi:tico-medium-1.0", "codex:gpt-6.2-sol"],
    }
    assert providers.check_choice(record, "claude-opus-5-5")["model"] == "gpt-6.2-sol"
    assert [row["maker"] for row in providers.check_order(record)][:2] == ["anthropic", "openai"]
