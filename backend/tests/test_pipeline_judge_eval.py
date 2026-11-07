"""The pipeline judge matrix (evals/pipeline_judge/) without a model: the labeled cases are well formed and cover what the
plan asks for, the dry run scores a fake oracle at the bar and a sloppy fake below it, and one case goes through the adapter's
judge_once with a fake engine. The real-model run is on demand only and never runs here."""

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
    from clients import judge as J

    def broken(case):
        raise J.JudgeError("unavailable", "down")

    result = run.score("broken", broken, run.load()[:3])
    assert result["false_met"] == 0 and result["unsure_rate"] == 1.0 and not result["passes_bar"]


def test_judge_once_reads_the_probability_and_prices_the_call():
    seen = {}

    def engine(state, questions, label=None):
        seen.update(state=state, label=label)
        return {"answers": {"met": {"noul": 0.9}}, "usage": {"input_tokens": 1_000_000, "output_tokens": 0}, "ms": 12}

    p, cost, ms = adapter.judge_once(engine, run.load()[0], "haiku-5.5")
    assert (p, ms) == (0.9, 12)
    assert cost == adapter.PRICES["haiku-5.5"][0]
    assert seen["state"]["attachment"]["work_version"] == seen["state"]["work"]["work_version"]
