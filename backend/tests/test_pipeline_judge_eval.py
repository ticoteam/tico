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


def _synthetic(n, expected="met"):
    return [{"id": f"c{i}", "domain": "dev" if i % 2 else "marketing", "expected": expected} for i in range(n)]


def _answering(answers, ms=5):
    """A fake judge that answers each case id with a verdict (met, not_met or unsure) at the probability the bar maps back."""
    p_of = {"met": 0.95, "not_met": 0.05, "unsure": 0.5}
    return lambda case: (p_of[answers[case["id"]]], 0.0, ms)


def test_the_bar_accuracy_gate_is_exactly_ninety_percent():
    cases = _synthetic(10)
    nine = {c["id"]: "met" for c in cases} | {"c9": "not_met"}
    eight = nine | {"c8": "not_met"}
    assert run.score("nine", _answering(nine), cases)["passes_bar"]
    eight_result = run.score("eight", _answering(eight), cases)
    assert not eight_result["passes_bar"]
    assert any(f.startswith("accuracy 80%") for f in eight_result["bar_failures"])


def test_the_bar_refuses_any_false_met_even_at_high_accuracy():
    cases = _synthetic(10, expected="not_met")
    answers = {c["id"]: "not_met" for c in cases} | {"c0": "met"}
    result = run.score("one-false-met", _answering(answers), cases)
    assert result["accuracy"] == 0.9 and result["false_met"] == 1 and not result["passes_bar"]
    assert any("false met" in f for f in result["bar_failures"])


def test_the_bar_p95_gate_is_under_thirty_seconds():
    cases = _synthetic(10)
    answers = {c["id"]: "met" for c in cases}
    assert run.score("just-under", _answering(answers, ms=29_999), cases)["passes_bar"]
    slow = run.score("at-limit", _answering(answers, ms=30_000), cases)
    assert not slow["passes_bar"] and any("p95" in f for f in slow["bar_failures"])


def test_the_bar_refuses_an_empty_case_set():
    result = run.score("none", _answering({}), [])
    assert not result["passes_bar"] and "no cases" in result["bar_failures"]


def test_an_always_unsure_judge_fails_the_real_cases_on_accuracy():
    cases = run.load()
    result = run.score("always-unsure", lambda case: (0.5, 0.0, 5), cases)
    assert not result["passes_bar"] and result["unsure_rate"] == 1.0
    assert result["accuracy"] < run.ACCURACY_BAR


def test_a_perfect_judge_that_takes_sixty_seconds_fails_the_real_cases():
    cases = run.load()
    perfect = {c["id"]: c["expected"] for c in cases}
    assert run.score("perfect", _answering(perfect), cases)["passes_bar"]
    slow = run.score("perfect-slow", _answering(perfect, ms=60_000), cases)
    assert not slow["passes_bar"] and slow["accuracy"] == 1.0


def test_a_run_with_a_skipped_model_is_incomplete_and_sends_nothing(monkeypatch, tmp_path, capsys):
    built = []

    def build(name, env=None):
        built.append(name)
        return None if name == "sonnet" else (lambda *a: (0.95, 0.0, 5))

    monkeypatch.setattr(adapter, "build_engine", build)
    assert run.main(["--models", "jev,sonnet", "--out", str(tmp_path)], env={}) == 2
    assert "INCOMPLETE RUN" in capsys.readouterr().err and not list(tmp_path.iterdir())
    assert built == ["jev", "sonnet"]


def test_the_default_judges_all_go_through_the_gateway_key_only():
    engines = {name: adapter.build_engine(name, {"TICO_GATEWAY_KEY": "k"}) for name in adapter.MODELS}
    assert all(callable(engine) for engine in engines.values())
    assert adapter.build_engine("jev", {"ANTHROPIC_API_KEY": "direct"}) is None
