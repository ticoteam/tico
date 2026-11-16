"""The areas Flow will ask about but does not yet (backend/tests/helpers/flow_pending.py): each owner's states are
written down whole (who is asked, the question, where its suggestion comes from, where the answer saves), none names
an area Flow already asks about, and each expected question has the card's properties and passes the rubric's
mechanical checks, so an area's build starts from questions that already fit. Words its owner proposed that break a
rule are a strict xfail with why, as are the Strategy owner's per-part words for the questions being rewritten. Data
only: no backend, no model."""

import pytest

from backend.tests.helpers import flow_pending as P
from backend.tests.helpers import flow_scenarios as F
from evals.flow import quality as Q

# Names that must never reach the repo's fixtures: real products the owners' notes used.
REAL = ("figma", "loom", "claude", "northwind", "tico team", "chris")


def item(state):
    """A pending state's question as a rubric item: a box beside at most one suggestion, else its choices."""
    return {"question": state.question, "choices": list(state.choices), "open": len(state.choices) <= 1}


def test_every_pending_area_is_written_down_whole():
    names = [a.name for a in P.PENDING]
    assert len(names) == len(set(names)) and not set(names) & {a.name for a in F.AREAS}
    for area in P.PENDING:
        assert area.saves in ("draft", "at once") and area.states, area.name
        assert len({s.name for s in area.states}) == len(area.states), area.name
        for s in area.states:
            assert s.who, (area.name, s.name)
            if s.question:  # a state with no question is one where Flow must ask nothing
                assert s.suggest_from and s.saves_to, (area.name, s.name)
            else:
                assert s.who.startswith("nobody"), (area.name, s.name)
            text = " ".join([s.question, s.suggest_from, s.saves_to, *s.choices]).lower()
            assert not [w for w in REAL if w in text], (area.name, s.name)


def _questions():
    for area in P.PENDING:
        for s in area.states:
            if s.question:
                marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason=s.breaks)] if s.breaks else []
                yield pytest.param(s, marks=marks, id=f"{area.name}/{s.name}")


@pytest.mark.parametrize("state", list(_questions()))
def test_an_expected_question_fits_the_card_and_the_rubric(state):
    assert Q.card(state.question) + Q.failed(item(state)) == [], state.question


def _wording():
    for key, (question, breaks) in P.STRATEGY_WORDING.items():
        marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason=breaks)] if breaks else []
        yield pytest.param(question, marks=marks, id=key)


@pytest.mark.parametrize("question", list(_wording()))
def test_the_strategy_owners_wording_fits_the_card(question):
    assert Q.card(question) + Q.failed({"question": question}) == [], question
