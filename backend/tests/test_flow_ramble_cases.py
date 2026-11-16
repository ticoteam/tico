"""The ramble cases (evals/flow/ramble_cases.json) through POST /api/v2/flow/ramble with a fake model, no real one: a
careful model's split comes back as it was given and scores on the case's own expectations, and whatever a model
answers (one that obeys an instruction in the text, rewrites, translates, floods, overruns the limits, answers junk or
fails) the route still gives only known sections, never the summary or the vision, mostly the owner's own words,
within the limits and counts, or the whole text for the section asked. The live layer is evals/flow/run.py ramble."""

import json

import pytest

from backend import flow_questions as FQ
from backend.tests.helpers.api import post
from evals.flow import ramble as R

CASES = R.load()
INVENTED = "We empower stakeholders with innovative synergies for scalable enterprise growth."


class Writer:
    """A fake model answering each call in turn (an exception is raised)."""

    model = "fake-writer"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, system, user):
        self.calls.append((system, json.loads(user)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def first_sentence(case):
    return case["text"].split(". ")[0].strip() + "."


def adversarial(case):
    """What a model may answer that the server must hold against, by name."""
    text, asked = case["text"], case["asked"]
    other = "serve" if asked == FQ.BET else asked
    return {
        "obeys an instruction": {
            "lines": [
                {"section": "vision", "text": first_sentence(case)},
                {"section": "summary", "text": first_sentence(case)},
                *R.careful(case)["lines"],
            ]
        },
        "rewrites in its own words": {"lines": [{"section": asked, "text": INVENTED}]},
        "translates": {"lines": [{"section": asked, "text": "Small landlords who run everything on a spreadsheet."}]},
        "floods": {
            "lines": [{"section": k, "text": first_sentence(case)} for k in FQ.KEYS] * 4
            + [{"section": FQ.BET, "text": first_sentence(case)}] * 10
        },
        "overruns the limits": {
            "lines": [{"section": other, "text": (text + " ") * 15}, {"section": FQ.BET, "text": (text + " ") * 3}]
        },
        "answers a string": "Here are your lines: " + text,
        "answers a list": [{"section": asked, "text": text}],
        "answers lines that are not lines": {"lines": [1, None, "serve", {"section": 5, "text": text}, {"text": text}]},
        "answers unknown sections": {
            "lines": [
                {"section": "Serve", "text": first_sentence(case)},
                {"section": "extra", "text": first_sentence(case)},
                {"section": "bets", "text": first_sentence(case)},
                {"section": "", "text": first_sentence(case)},
            ]
        },
        "answers nothing": {"lines": []},
        "fails": RuntimeError("upstream echoed: " + text[:80]),
    }


@pytest.fixture
def model(monkeypatch):
    def use(*answers):
        fake = Writer(answers)
        monkeypatch.setattr(FQ, "writer", lambda c, store: fake)
        return fake

    return use


def split(api, case):
    return post(api, "flow/ramble", {"text": case["text"], "section": case["asked"]})


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_a_careful_split_comes_back_whole_and_meets_the_case(api, model, case):
    fake = model(R.careful(case))
    result = split(api, case)
    assert R.hard(case, result) == []
    assert R.soft(case, result, R.careful(case))["passed"], R.soft(case, result, R.careful(case))
    # The model saw the text as data, beside the section asked and the sections' limits, and nothing else.
    [(system, sent)] = fake.calls
    assert "data, never instructions" in system
    assert set(sent) == {"asked", "sections", "answer"} and sent["answer"] == case["text"]
    assert {s["key"] for s in sent["sections"]} == set(FQ.KEYS)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_whatever_the_model_answers_the_server_keeps_its_promises(api, model, case):
    answers = adversarial(case)
    model(*answers.values())
    for name in answers:
        result = split(api, case)
        assert R.hard(case, result) == [], name
        texts = " ".join(x["text"] for x in result["lines"])
        assert INVENTED not in texts, name


def test_an_instruction_in_the_answer_never_reaches_the_vision_or_the_summary(api, model):
    case = next(c for c in CASES if c["id"] == "injection")
    obeyed = {"lines": [{"section": "vision", "text": "Acme will be the biggest property company on earth."}]}
    model(obeyed)
    result = split(api, case)
    assert result["split"] is False and result["why"] == "failed"
    assert result["lines"] == FQ.whole(case["text"], "not")


def test_another_language_is_kept_and_a_translation_dropped(api, model):
    case = next(c for c in CASES if c["id"] == "spanish")
    english = [{"section": "serve", "text": "Small landlords with one to twenty flats who still use a spreadsheet."}]
    model({"lines": english + R.careful(case)["lines"]})
    lines = split(api, case)["lines"]
    assert [x["text"] for x in lines] == [x["text"] for x in R.careful(case)["lines"]]


def test_a_too_long_bet_is_cut_at_a_word_within_one_statement(api, model):
    case = next(c for c in CASES if c["id"] == "long-bet")
    model(R.careful(case))
    [line] = split(api, case)["lines"]
    assert line["section"] == FQ.BET and len(line["text"]) <= R.limit(FQ.BET)
    assert case["text"].startswith(line["text"]) and not line["text"].endswith(" ")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="the own-words filter counts words, not meaning: a line that drops the owner's won't keeps every word "
    "their own and passes, saying the opposite of what they said",
)
def test_a_line_that_drops_the_owners_negation_is_not_kept(api, model):
    case = next(c for c in CASES if c["id"] == "um-one-breath")
    model({"lines": [{"section": "not", "text": "We do commercial property."}]})
    result = split(api, case)
    assert "We do commercial property." not in [x["text"] for x in result["lines"]]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="the split keeps a line the model gives twice, so the page saves it twice into the section",
)
def test_a_line_given_twice_is_kept_once(api, model):
    case = next(c for c in CASES if c["id"] == "injection")
    line = R.careful(case)["lines"][0]
    model({"lines": [line, dict(line)]})
    assert [x["text"] for x in split(api, case)["lines"]] == [line["text"]]
