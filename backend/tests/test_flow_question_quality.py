"""The question-quality rubric (evals/flow/rubric.json: the Assistant owner's eight checks, any no fails;
evals/flow/quality.py) without a model: plain code gives exactly
the verdicts question_examples.json expects of it, never contradicts the right verdict, and passes every good example;
the judge's question set is one the decision contract accepts for every example; and the live eval's dry runs pass (a
careful splitter, a right judge) and fail a judge that says yes to everything, sending nothing. The live runs are
evals/flow/run.py, on demand only."""

import pytest

from clients import judge as J
from evals.flow import quality as Q
from evals.flow import run

EXAMPLES = run.load_questions()


def test_the_rubric_is_whole():
    ids = [c["id"] for c in Q.RUBRIC["checks"]]
    assert len(ids) == 8 and all(c["rule"] and c["name"] for c in Q.RUBRIC["checks"])  # eight checks, any no fails
    assert len(ids) == len(set(ids)) and {c["layer"] for c in Q.RUBRIC["checks"]} <= {"mechanical", "judge", "both"}
    assert all(c["judge"].strip().endswith("?") for c in Q.RUBRIC["checks"])
    assert set(Q.mechanical({"question": "Is this one?"})) == set(ids)
    J.validate({"question": "", "choices": [], "context": ""}, Q.judge_questions(), run.LABEL)


def test_the_examples_cover_good_and_bad():
    good = [e for e in EXAMPLES if all(e["expect"].values())]
    bad = [e for e in EXAMPLES if not all(e["expect"].values())]
    assert len(good) >= 10 and len(bad) >= 10 and run.problems(EXAMPLES) == []
    assert all(set(e["expect"]) == set(Q.CHECKS) for e in EXAMPLES)
    # Every check that plain code settles is failed by at least one example.
    caught = {k for e in bad for k, v in Q.mechanical(e).items() if v is False}
    assert caught == set(Q.MECHANICAL)


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["id"] for e in EXAMPLES])
def test_plain_code_gives_the_expected_verdicts(example):
    code = Q.mechanical(example)
    assert code == example["mechanical"]
    assert all(v == example["expect"][k] for k, v in code.items() if v is not None)
    if all(example["expect"].values()):
        assert Q.failed(example) == []


def test_flow_questions_are_rubric_items_and_judgeable():
    items = run.flow_questions()
    assert {i["id"] for i in items} == {
        "strategy/" + k for k in ("serve", "sell", "bet", "not", "proof", "now", "know", "how")
    }
    assert run.problems(items) == []
    taken = Q.of_flow({"key": "serve", "title": "Who we serve", "question": "Who is it for?", "suggest": "Landlords."})
    assert taken["choices"] == ["Landlords."] and Q.mechanical(taken)["recommended_first"] is True


def test_the_dry_runs_pass_and_a_yes_judge_fails(capsys):
    assert run.main(["ramble", "--dry-run", "-j", "2"]) == 0
    assert run.main(["judge", "--dry-run"]) == 0
    assert run.main(["judge", "--flow", "--dry-run"]) == 0
    assert run.run_judge(EXAMPLES, run.yes_man) == 1
    assert "bad questions passed" in capsys.readouterr().out


def test_with_no_model_nothing_is_sent_and_the_exit_says_so(capsys):
    assert run.main(["ramble"], env={}) == 2
    assert run.main(["judge"], env={}) == 2
    assert "Nothing was sent" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run.main(["ramble", "--dry-run", "-j", "3"])  # never more than two calls at once
