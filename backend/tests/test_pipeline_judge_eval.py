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

    result = run.score("broken", broken, run.load()[:3], pause=lambda seconds: None)
    assert result["false_met"] == 0 and result["unsure_rate"] == 1.0 and not result["passes_bar"]


def test_judge_once_goes_through_the_servers_judge_with_the_work_and_the_evidence():
    seen = {}

    def engine(model, state, questions, label):
        seen.update(model=model, state=state, label=label)
        return {"p": 0.9, "reason": "ok", "provider": "t", "model": model, "cost_usd": 0.002, "latency_ms": 12}

    case = next(c for c in run.load() if c.get("trap") == "old_version")
    p, cost, ms = adapter.judge_once(engine, case)
    assert (p, cost, ms) == (0.9, 0.002, 12)
    assert seen["model"] == "jev" and seen["label"] == adapter.QUESTION_SET
    assert seen["state"]["work"]["work_version"] == case["work"]["work_version"]
    assert seen["state"]["attachment"]["work_version"] == case["attachment"]["work_version"]
    assert seen["state"]["attachment"]["work_version"] != seen["state"]["work"]["work_version"]
    assert seen["state"]["attachment"]["text"] == case["attachment"]["text"]


def test_a_model_call_that_fails_is_raised_to_the_caller_and_never_passes():
    def broken(model, state, questions, label):
        raise J.JudgeError("unavailable", "down")

    with pytest.raises(J.JudgeError):
        adapter.judge_once(broken, run.load()[0])


def test_the_only_eval_model_is_jev_and_the_gateways_cost_is_kept_or_priced():
    assert adapter.MODELS == ("jev",)
    assert adapter.cost_usd("jev", {"cost_usd": 0.5, "prompt_tokens": 1_000_000}) == 0.5
    assert adapter.cost_usd("jev", {"prompt_tokens": 1_000_000, "completion_tokens": 0}) == adapter.PRICES["jev"][0]


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


def test_a_run_with_no_key_is_incomplete_and_sends_nothing(monkeypatch, tmp_path, capsys):
    built = []

    def build(name, env=None):
        built.append(name)
        return None

    monkeypatch.setattr(adapter, "build_engine", build)
    assert run.main(["--out", str(tmp_path)], env={}) == 2
    assert "INCOMPLETE RUN" in capsys.readouterr().err and not list(tmp_path.iterdir())
    assert built == ["jev"]


def test_the_default_judges_all_go_through_the_gateway_key_only():
    engines = {name: adapter.build_engine(name, {"TICO_GATEWAY_KEY": "k"}) for name in adapter.MODELS}
    assert all(callable(engine) for engine in engines.values())
    assert adapter.build_engine("jev", {"ANTHROPIC_API_KEY": "direct"}) is None


def test_the_screenshot_cases_are_unsure_because_the_judge_is_shown_no_image_text():
    """Fix L2-7: the server reads no text from an image, so every screenshot case is unsure and the judge sees none of it."""
    shots = [c for c in run.load() if (c.get("attachment") or {}).get("kind") == "image"]
    assert len(shots) == 5
    for case in shots:
        assert case["expected"] == "unsure", case["id"]
        assert adapter.state_for(case)["attachment"]["text"] is None, case["id"]


def test_the_eval_judge_is_shown_the_work_it_is_judging():
    """The eval builds the state through the server's _state, with the case's own work fields."""
    case = next(c for c in run.load() if c["id"] == "dev-tests-other-pr")
    work = adapter.state_for(case)["work"]
    assert work["repo"] == "acme/app" and work["pr"] == 91 and work["author"] == "human:ana"
    assert work["title"] == case["work"]["title"] and work["spec"] == case["work"]["spec"]
    missing = next(c for c in run.load() if c["id"] == "mkt-page-live")
    assert adapter.state_for(missing)["work"]["title"] == "Holiday projectors campaign"


def test_a_missing_evidence_case_is_not_met_and_no_model_is_called():
    case = next(c for c in run.load() if c["id"] == "dev-tests-missing")
    asked = []

    def engine(model, state, questions, label):
        asked.append(model)
        return {"p": 0.9, "reason": "", "provider": "t", "model": model}

    assert adapter.judge_once(engine, case) == (0.0, 0.0, 0)
    assert asked == []


def test_a_failed_call_is_retried_once_after_five_seconds_and_the_retry_is_counted():
    waits, calls = [], {}

    def flaky(case):
        calls[case["id"]] = calls.get(case["id"], 0) + 1
        if calls[case["id"]] == 1:
            raise J.JudgeError("unavailable", "down")
        return 0.95 if case["expected"] == "met" else 0.05, 0.0, 5

    cases = run.load()[:3]
    result = run.score("flaky", flaky, cases, pause=waits.append, clock=lambda: 0.0)
    assert waits == [5] * 3
    assert result["retries"] == 3 and all(r["retries"] == 1 for r in result["rows"])
    assert result["errors"] == []
    # The failed call's time and the five-second pause count toward the case's latency, not only the retry's 5 ms.
    assert all(r["ms"] == 5005 for r in result["rows"])


def test_a_retry_cannot_hide_a_slow_case_from_the_p95_bar():
    """Codex round 3, P3: a failed call, the pause and a 29 s retry take over 30 s, so the case fails the p95 bar."""
    calls, ticks = {}, iter([0.0, 1.0, 1.0] * 3)  # per case: the start, the failed call's end, the retry's start

    def flaky(case):
        calls[case["id"]] = calls.get(case["id"], 0) + 1
        if calls[case["id"]] == 1:
            raise J.JudgeError("unavailable", "down")
        return 0.95 if case["expected"] == "met" else 0.05, 0.0, 29000

    result = run.score("slow", flaky, run.load()[:3], pause=lambda s: None, clock=lambda: next(ticks))
    assert result["p95_ms"] == 35000
    assert any("p95" in f for f in result["bar_failures"])


def test_a_call_that_fails_twice_is_still_an_error_and_fails_the_bar():
    waits = []

    def down(case):
        raise J.JudgeError("unavailable", "down")

    cases = run.load()[:3]
    ticks = iter([0.0, 2.0, 2.0, 3.0] * 3)  # per case: the start, the first call fails 2 s in, the retry fails 1 s in
    result = run.score("down", down, cases, pause=waits.append, clock=lambda: next(ticks))
    assert waits == [5] * 3
    assert result["retries"] == 3 and len(result["errors"]) == 3
    # Codex round 6, P3: both failed calls and the pause count in the latency, so the case is not left out of the p95.
    assert all(r["ms"] == 8000 for r in result["rows"]) and result["p95_ms"] == 8000
    assert not result["passes_bar"] and any("failed calls" in f for f in result["bar_failures"])


def test_the_cases_the_rubric_says_do_not_count_are_not_met():
    """Readable evidence that fails the rubric is not met, not unsure (Codex round 9, P3: a lead with no named person)."""
    by_id = {c["id"]: c["expected"] for c in run.load()}
    for case_id in (
        "dev-tests-truncated",
        "dev-review-partial",
        "mkt-brief-comment",
        "mkt-page-timeout",
        "mkt-lead-generic-email",
    ):
        assert by_id[case_id] == "not_met", case_id


def test_the_eval_author_reaches_the_judge_only_through_the_pr_links_detail(monkeypatch):
    """Codex P1: production knows a work's author only as the GitHub login on its PR link (detail.author_login), so the eval
    passes the case's author the same way, through the server's own reader of a PR link."""
    import json

    from backend import pipeline_judges as P

    seen = []
    real = P.pr_work
    monkeypatch.setattr(P, "pr_work", lambda link: seen.append(link) or real(link))
    case = next(c for c in run.load() if c["id"] == "dev-review-self")
    work = adapter.state_for(case)["work"]
    assert work["author"] == "human:ana" and work["pr"] == 91
    assert seen and json.loads(seen[-1]["detail"])["author_login"] == "human:ana"
    no_pr = {**case, "work": {k: v for k, v in case["work"].items() if k not in ("repo", "pr")}}
    with pytest.raises(ValueError, match="PR"):
        adapter.state_for(no_pr)


def test_the_acme_review_pages_are_judged_as_the_page_step_asks_with_the_shipped_rubrics():
    """The three pages Jev was unsure of on Acme (Nov 11) and the two it passed are cases, built by the page step's own
    builder (page_verdicts.job_of): the page's text, the stage's shipped rubric, the PR and its newest commit."""
    from backend.pipeline_settings import FIXES_2_RUBRIC, REVIEW_RUBRIC

    cases = {c["id"]: c for c in run.chosen(run.load(), "acme-*")}
    assert set(cases) == {"acme-review-1", "acme-review-2", "acme-fixes-2", "acme-spec", "acme-validate"}
    assert all(c["expected"] == "met" for c in cases.values())
    state = adapter.state_for(cases["acme-review-1"])
    assert state["requirement"]["rubric"] == REVIEW_RUBRIC and state["requirement"]["older_evidence_ok"] is True
    assert (state["work"]["repo"], state["work"]["pr"]) == ("ticoteam/pipeline-sandbox", 38)
    head = state["work"]["context"]["heads"]["ticoteam/pipeline-sandbox#38"]
    assert state["attachment"]["text"].startswith(f"Reviewed commit {head}")
    assert adapter.state_for(cases["acme-validate"])["work"]["context"]["release"] == ["v6"]
    assert adapter.state_for(cases["acme-fixes-2"])["requirement"]["rubric"] == FIXES_2_RUBRIC
    # Its two cuts are traps: a pre-release page without what the reviewers found, or without the checks, is not met.
    for trap in ("dev-prerelease-no-reviews", "dev-prerelease-unchecked"):
        cut = next(c for c in run.load() if c["id"] == trap)
        assert cut["expected"] == "not_met" and cut["requirement"]["rubric"] == FIXES_2_RUBRIC
        assert len(cut["attachment"]["text"]) < len(cases["acme-fixes-2"]["attachment"]["text"])
    assert run.chosen(run.load(), "acme-spec,dev-review-done") and len(run.chosen(run.load(), "acme-spec")) == 1
