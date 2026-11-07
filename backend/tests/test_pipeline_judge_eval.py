"""The pipeline judge matrix (evals/pipeline_judge/) without a model: the labeled cases are well formed and cover what the
plan asks for, the dry run scores a fake oracle at the bar and a sloppy fake below it, and one case goes through the server's
judge_once with a fake engine. The real-model run is on demand only and never runs here."""

import pytest

from clients import judge as J
from evals.pipeline_judge import adapter, run


def test_the_cases_are_usable_and_cover_both_domains_and_the_traps():
    cases = run.load()
    assert run.problems(cases) == []
    assert len(cases) >= 30
    traps = {c.get("trap") for c in cases}
    assert {"missing_file", "old_version", "wrong_screen", "no_email"} <= traps
    assert {c["expected"] for c in cases} == {"met", "not_met", "unsure"}


def test_the_dry_run_passes_the_oracle_and_fails_the_sloppy_judge_on_false_met(tmp_path, capsys):
    assert run.main(["--dry-run", "--out", str(tmp_path)]) == 0
    cases = run.load()
    judges = run.fake_judges(cases)
    oracle = run.score("oracle", judges["fake-oracle"], cases)
    sloppy = run.score("sloppy", judges["fake-sloppy"], cases)
    assert oracle["passes_bar"] and oracle["accuracy"] == 1.0 and oracle["false_met"] == 0
    assert not sloppy["passes_bar"] and sloppy["false_met"] > 0
    assert not list(tmp_path.iterdir()), "a dry run writes nothing"
    assert "fake-sloppy" in capsys.readouterr().out


def test_a_failed_call_counts_as_unsure_and_never_as_met():
    def broken(case):
        raise J.JudgeError("unavailable", "down")

    result = run.score("broken", broken, run.load()[:3])
    assert result["false_met"] == 0 and result["unsure_rate"] == 1.0 and not result["passes_bar"]


def test_judge_once_goes_through_the_servers_judge_with_the_work_and_the_evidence():
    seen = {}

    def engine(model, state, questions, label):
        seen.update(model=model, state=state, label=label)
        return {"p": 0.9, "reason": "ok", "provider": "t", "model": model, "cost_usd": 0.002, "latency_ms": 12}

    case = next(c for c in run.load() if c.get("trap") == "old_version")
    p, cost, ms = adapter.judge_once(engine, case, "haiku-5.5")
    assert (p, cost, ms) == (0.9, 0.002, 12)
    assert seen["model"] == "haiku-5.5" and seen["label"] == adapter.QUESTION_SET
    assert seen["state"]["work"]["work_version"] == case["work"]["work_version"]
    assert seen["state"]["attachment"]["work_version"] == case["attachment"]["work_version"]
    assert seen["state"]["attachment"]["work_version"] != seen["state"]["work"]["work_version"]
    assert seen["state"]["attachment"]["text"] == case["attachment"]["text"]


def test_a_model_call_that_fails_is_raised_to_the_caller_and_never_passes():
    def broken(model, state, questions, label):
        raise J.JudgeError("unavailable", "down")

    with pytest.raises(J.JudgeError):
        adapter.judge_once(broken, run.load()[0], "jev")


def test_the_gateways_cost_is_kept_and_tokens_are_priced_when_it_gives_none():
    assert adapter.cost_usd("haiku-5.5", {"cost_usd": 0.5, "prompt_tokens": 1_000_000}) == 0.5
    assert (
        adapter.cost_usd("haiku-5.5", {"prompt_tokens": 1_000_000, "completion_tokens": 0})
        == (adapter.PRICES["haiku-5.5"][0])
    )


def test_no_gateway_key_means_no_engine_for_any_model_and_nothing_is_sent():
    for name in adapter.MODELS:
        assert adapter.build_engine(name, {}) is None
    with pytest.raises(ValueError):
        adapter.build_engine("anthropic-direct", {"TICO_GATEWAY_KEY": "x"})
