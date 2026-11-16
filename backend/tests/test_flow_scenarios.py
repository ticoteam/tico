"""Flow setup questions, scenario by scenario (backend/tests/helpers/flow_scenarios.py, docs/flow-scenarios.md): every
registered area's scenarios through the real questions read, with no model and no browser. Each scenario asks what it
says, in order, with suggestions from where it says; and on every scenario of every area: each question has the Flow
card's properties (one sentence ending in "?", two lines at most, no ids) and passes the rubric's mechanical checks, a
suggestion quotes stored data, a confirmed part is never asked, nobody but the owner gets a question or may split an
answer, and answering every question in turn through the routes the page uses ends publishable with nothing left to
ask, never writes what Flow must not, and changes nothing anyone else reads, nor the owner's Needs you count, before
Publish. Properties, not words: the questions' wording is being rewritten per part. A pending scenario (a trigger Flow
lacks) is a strict xfail on its expected question and choices. Invented companies only."""

import pytest

from backend import flow_questions as FQ
from backend.tests.helpers import flow_scenarios as F
from backend.tests.helpers.api import get, post
from evals.flow import quality as Q

NOTHING = {"questions": [], "more": [], "revision": None, "titles": {}}
WALK_MAX = 20  # questions a walk may answer before it is called stuck


@pytest.fixture
def no_model(monkeypatch):
    """No writer, as on a server with no model: a long answer comes back whole."""
    monkeypatch.setattr(FQ, "writer", lambda c, store: None)


def every(found):
    return list(found["questions"]) + list(found["more"])


def check(area, api, found):
    """The invariants on one read of the owner's questions."""
    confirmed, stored = area.confirmed(api), area.stored(api)
    keys = [q["key"] for q in every(found)]
    assert len(keys) == len(set(keys)), f"a part asked twice: {keys}"
    for q in every(found):
        assert q["key"] not in confirmed, f"{q['key']} is asked though it is confirmed"
        if q["key"] not in area.wording:
            assert Q.card(q["question"]) + Q.failed(Q.of_flow(q)) == [], f"{q['key']}: {q['question']!r}"
        if q["suggest"]:
            assert any(q["suggest"] in text for text in stored), f"{q['key']} suggests {q['suggest']!r}: not stored"
            assert "_Unconfirmed" not in q["suggest"]


@pytest.mark.parametrize("case", F.cases(), ids=F.case_id)
def test_flow_asks_what_the_scenario_says(api, case):
    area, scenario = case
    area.prepare(api)
    area.build(api, scenario.state)
    found = area.read(api, F.OWNER)
    assert [q["key"] for q in found["questions"]] == [a.key for a in scenario.asks]
    assert [q["key"] for q in found["more"]] == [a.key for a in scenario.more]
    assert not {q["key"] for q in every(found)} & set(scenario.never)
    expected = {a.key: a for a in scenario.asks + scenario.more}
    for q in every(found):
        want = expected[q["key"]]
        source = area.source(api, q) if q["suggest"] else None
        assert (source or (None,))[0] == want.source, f"{q['key']}: suggestion from {source}, not {want.source}"
        if want.suggest:
            assert q["suggest"] == want.suggest
        if source:
            assert q["suggest"] in source[1]
    check(area, api, found)


@pytest.mark.parametrize("case", F.cases(), ids=F.case_id)
def test_nobody_but_the_owner_gets_a_question_or_may_split_an_answer(api, case, no_model):
    area, scenario = case
    viewers = area.prepare(api)
    area.build(api, scenario.state)
    for name, token in viewers.items():
        assert area.read(api, token) == NOTHING, name
        post(api, "flow/ramble", {"text": "x" * 300, "section": "serve"}, token=token, expected=403)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM events WHERE action=?", (FQ.ACTION,)).fetchone(), "a refused split counted"


@pytest.mark.parametrize("case", F.cases(), ids=F.case_id)
def test_answering_every_question_ends_publishable_and_changes_nothing_others_read(api, case, no_model):
    area, scenario = case
    viewers = area.prepare(api)
    area.build(api, scenario.state)
    before, kept, needs = area.seen(api, viewers), area.untouched(api), get(api, "needs-you")["count"]
    saved = []
    for _ in range(WALK_MAX):
        found = area.read(api, F.OWNER)
        check(area, api, found)
        if not every(found):
            break
        q = every(found)[0]
        saved.append(area.answer(api, q))
        after = area.read(api, F.OWNER)
        assert q["key"] not in [x["key"] for x in every(after)], f"{q['key']} is asked again after its answer"
        assert area.seen(api, viewers) == before, f"answering {q['key']} changed what others read"
    assert every(area.read(api, F.OWNER)) == [], "questions left after the walk"
    assert area.done(api), "every question answered, and still not publishable"
    assert area.untouched(api) == kept, "Flow wrote what it never writes"
    assert get(api, "needs-you")["count"] == needs, "the questions changed the Needs you count"
    if not saved:
        return
    # Publish is what lets the team read it: the reads change then, so the check above was not vacuous.
    area.publish(api)
    assert area.seen(api, viewers) != before
    assert every(area.read(api, F.OWNER)) == []


def _wording_cases():
    for area in F.AREAS:
        empty = next(s for s in area.scenarios if s.name == "empty")
        for ask in empty.asks + empty.more:
            known = area.wording.get(ask.key)
            marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason=known)] if known else []
            yield pytest.param(area, empty, ask.key, marks=marks, id=f"{area.name}/{ask.key}")


@pytest.mark.parametrize("area, empty, key", list(_wording_cases()))
def test_each_question_is_one_short_plain_sentence(api, area, empty, key):
    """Each question on the card's properties and the rubric's mechanical checks. A key in the area's `wording` breaks
    them today: a strict xfail, which fails once the words are fixed so its entry is taken out."""
    area.prepare(api)
    area.build(api, empty.state)
    [q] = [q for q in every(area.read(api, F.OWNER)) if q["key"] == key]
    assert Q.card(q["question"]) + Q.failed(Q.of_flow(q)) == [], q["question"]


def _pending():
    for area, scenario in F.pending_cases():
        mark = pytest.mark.xfail(strict=True, raises=AssertionError, reason="not built: " + scenario.pending)
        yield pytest.param(area, scenario, marks=[mark], id=F.case_id((area, scenario)))


@pytest.mark.parametrize("area, scenario", list(_pending()))
def test_a_pending_scenario_asks_what_its_owner_expects(api, area, scenario):
    """What the area's owner expects once Flow has the trigger: each expected question by its key, with the expected
    choices in order, on the card's properties. Not built yet, so a strict xfail: it fails the day the trigger lands
    and passes, and the scenario's `pending` comes off."""
    area.prepare(api)
    area.build(api, scenario.state)
    found = every(area.read(api, F.OWNER))
    for want in scenario.asks + scenario.more:
        got = [q for q in found if q["key"] == want.key and tuple(q.get("choices") or ()) == want.choices]
        assert got, f"no {want.key} question with the choices {want.choices}"
        assert Q.card(got[0]["question"]) == []
        check(area, api, {"questions": got, "more": []})


def test_pending_scenarios_are_written_down_whole():
    """Each pending scenario names what it waits on and the choices its owner expects; its expected words are only a
    guide, but they are kept as plain questions."""
    for area, scenario in F.pending_cases():
        assert scenario.pending and any(a.choices for a in scenario.asks), scenario.name
        for want in scenario.asks:
            if want.question:
                assert want.question.endswith("?") and not Q.failed(
                    {"question": want.question, "choices": list(want.choices)}
                )
