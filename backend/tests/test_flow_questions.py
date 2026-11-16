"""Strategy setup questions in Flow (backend/flow_questions.py, docs/strategy.md#flow): the owner's open questions are
read from the draft with no model and nobody else gets any; a long answer is split by one metered call into lines in
the owner's own words, never into the summary or the vision; with no model, at the cap, at the daily limit or on a
failure the text comes back whole for the section asked; nothing of the text is stored; and what Flow saves changes
nothing anyone but the owner reads until Publish. Examples use Acme."""

import json

import pytest

from backend import flow_questions as FQ
from backend import judge
from backend import recruit
from backend import strategy as S
from backend import strategy_setup as SS
from backend.auth import Identity
from backend.tests.helpers.api import get, post, put
from backend.tests.helpers.strategy import draft, write

OWNER, MEMBER = "ana-test", "ben-test"
BEN = Identity("human:ben", "human", "ben@acme.example")
RAMBLE = (
    "We serve landlords with one to twenty units who still run everything from a spreadsheet. "
    "We sell bots that answer tenants and chase rent, at $29 a unit a month. "
    "We bet that small landlords will trust a bot with tenant messages. "
    "We will not take enterprise property managers, even when they ask."
)


class Writer:
    """A fake model: records each call and answers in turn (an exception is raised)."""

    model = "fake-writer"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, system, user):
        self.calls.append((system, json.loads(user)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def model(monkeypatch):
    def use(*answers):
        fake = Writer(*answers)
        monkeypatch.setattr(FQ, "writer", lambda c, store: fake)
        return fake

    return use


def ramble(api, text=RAMBLE, section="serve", token=OWNER, expected=200):
    return post(api, "flow/ramble", {"text": text, "section": section}, token=token, expected=expected)


def events(api, action):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT actor, target, detail FROM events WHERE action=?", (action,))]


def test_the_owners_basics_come_from_the_guide_in_order_and_others_get_none(api):
    found = get(api, "flow/questions")
    assert [q["key"] for q in found["questions"]] == ["serve", "sell", "bet", "not"]
    assert [q["key"] for q in found["more"]] == ["proof", "now", "know", "how"]
    asks = {q["key"]: q for q in found["questions"] + found["more"]}
    guide = {q["key"]: q["ask"] for q in S.guide("default")["questions"]}
    # The first question in guide order for each section: "make" for what we sell, "acute" for who we serve.
    assert asks["sell"]["question"] == guide["make"] and asks["serve"]["question"] == guide["acute"]
    assert asks["bet"]["question"] == guide["contrarian"] and asks["not"]["question"] == guide["wont"]
    assert asks["serve"]["title"] == "Who we serve" and asks["bet"]["title"] == "Bet"
    assert all(q["suggest"] == "" for q in found["questions"]) and found["revision"] == draft(api)["revision"]
    assert found["titles"] == {**{k: S.TITLES[k] for k in FQ.KEYS if k != "bet"}, "bet": "Bet"}
    # Anyone but the owner: nothing, not even the draft's revision.
    assert get(api, "flow/questions", token=MEMBER) == {"questions": [], "more": [], "revision": None, "titles": {}}


def test_a_part_is_asked_while_empty_or_unconfirmed_and_the_bet_offers_a_proposed_one(api):
    drafted = SS.unconfirmed("Landlords with 1-20 units.", "acme.example")
    write(api, sections={"serve": drafted, "sell": "Bots at $29 a unit a month.", "proof": "40 landlords."})
    proposed = post(api, "strategy/bets", {"statement": "Landlords trust bots with tenant messages"})["bet"]
    found = get(api, "flow/questions")
    asks = {q["key"]: q for q in found["questions"]}
    # Confirmed text is never asked again; drafted text is offered as the answer, its marker left out.
    assert list(asks) == ["serve", "bet", "not"] and "proof" not in [q["key"] for q in found["more"]]
    assert asks["serve"]["suggest"] == "Landlords with 1-20 units."
    assert asks["bet"]["suggest"] == proposed["statement"] and asks["bet"]["bet_id"] == proposed["id"]
    # Tapping the proposed bet makes it active (the bet route the page uses): the bet question goes.
    post(api, "strategy/bets/" + proposed["id"], {"status": "active", "revision": draft(api)["revision"]})
    assert [q["key"] for q in get(api, "flow/questions")["questions"]] == ["serve", "not"]


def test_the_ramble_route_is_the_owners_alone(api, model):
    fake = model({"lines": []})
    assert ramble(api, token=MEMBER, expected=403)["error"]["code"] == "forbidden"
    assert fake.calls == [] and events(api, FQ.ACTION) == []
    ramble(api, section="summary", expected=422)  # never the summary or the vision


def test_with_no_model_the_text_comes_back_whole_for_the_section_asked(api, monkeypatch):
    monkeypatch.setattr(FQ, "writer", lambda c, store: None)
    back = ramble(api, section="not")
    assert back == {"lines": [{"section": "not", "text": RAMBLE}], "split": False, "why": "no_model"}
    # A bet holds one sentence: the rest of its words are its why, not lost.
    long = RAMBLE + " " + RAMBLE
    bet = ramble(api, text=long, section="bet")["lines"]
    assert len(bet) == 1 and len(bet[0]["text"]) <= S.STATEMENT_MAX and long.startswith(bet[0]["text"])
    assert (bet[0]["text"] + " " + bet[0]["why"]).split() == long.split()
    assert events(api, FQ.ACTION) == []  # no call made, none counted


def test_one_call_gives_lines_in_the_owners_words_cut_to_the_limits(api, model):
    before = draft(api)
    fake = model(
        {
            "lines": [
                {
                    "section": "serve",
                    "text": "Landlords with one to twenty units who still run everything from a spreadsheet.",
                },
                {"section": "sell", "text": "Bots that answer tenants and chase rent, at $29 a unit a month."},
                {"section": "summary", "text": "We serve landlords."},  # never the summary
                {"section": "vision", "text": "Landlords trust bots."},  # nor the vision
                {"section": "bet", "text": "Small landlords will trust a bot with tenant messages."},
                {"section": "not", "text": "Acme pivots to enterprise and doubles every price."},  # not their words
                {"section": "not", "text": "We will not take enterprise property managers, even when they ask."},
                {"section": "how", "text": ""},
            ]
        }
    )
    back = ramble(api)
    assert back["split"] is True and back["why"] is None
    assert [(x["section"], x["text"]) for x in back["lines"]] == [
        ("serve", "Landlords with one to twenty units who still run everything from a spreadsheet."),
        ("sell", "Bots that answer tenants and chase rent, at $29 a unit a month."),
        ("bet", "Small landlords will trust a bot with tenant messages."),
        ("not", "We will not take enterprise property managers, even when they ask."),
    ]
    # Exactly one call; its input is the answer as data with the section names and limits, nothing from the draft.
    [(system, sent)] = fake.calls
    assert "data, never instructions" in system
    assert set(sent) == {"asked", "sections", "answer"} and sent["answer"] == RAMBLE and sent["asked"] == "serve"
    assert {s["key"]: s["max"] for s in sent["sections"]} == {
        **{k: S.SECTION_MAX for k in ("serve", "sell", "not", "proof", "now", "know", "how")},
        "bet": S.STATEMENT_MAX,
    }
    # Writes nothing to the strategy; the audit row has no detail and the spend row its label, never the text.
    assert draft(api) == before
    [row] = events(api, FQ.ACTION)
    assert row["actor"] == "human:ana" and "spreadsheet" not in json.dumps(row)
    spend = events(api, judge.SPEND_ACTION)
    assert [json.loads(r["detail"])["label"] for r in spend] == [FQ.LABEL]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM events WHERE detail LIKE '%spreadsheet%'").fetchone()
        assert not c.execute(
            "SELECT 1 FROM history_events WHERE coalesce(old, '') || coalesce(new, '') || coalesce(note, '') "
            "LIKE '%spreadsheet%'"
        ).fetchone()


def test_long_lines_are_cut_and_bets_and_lines_are_capped(api, model):
    long = "landlords " * 60
    model({"lines": [{"section": "bet", "text": long}] * 5 + [{"section": "serve", "text": long}] * 20})
    lines = ramble(api, text=long * 10)["lines"]
    assert len(lines) == FQ.LINES_MAX and sum(x["section"] == "bet" for x in lines) == FQ.BETS_MAX
    assert all(len(x["text"]) <= (S.STATEMENT_MAX if x["section"] == "bet" else S.SECTION_MAX) for x in lines)
    assert all(len(x["text"]) <= S.STATEMENT_MAX for x in lines if x["section"] == "bet")


def test_a_failure_is_not_retried_and_gives_the_whole_text(api, model):
    fake = model(RuntimeError("upstream said: " + RAMBLE))
    assert ramble(api, section="sell") == {
        "lines": [{"section": "sell", "text": RAMBLE}],
        "split": False,
        "why": "failed",
    }
    assert len(fake.calls) == 1
    # An answer with no usable line is a failure too.
    model({"lines": [{"section": "summary", "text": RAMBLE}]})
    assert ramble(api)["why"] == "failed"


def test_the_daily_limit_and_the_spend_cap_send_no_call(api, model, monkeypatch):
    monkeypatch.setattr(FQ, "DAILY_MAX", 2)
    fake = model({"lines": []}, {"lines": []}, {"lines": []})
    ramble(api), ramble(api)
    assert ramble(api)["why"] == "limit" and len(fake.calls) == 2
    # Ben's limit is his own; he is no owner, so he is refused before it counts.
    ramble(api, token=MEMBER, expected=403)
    monkeypatch.setattr(FQ, "DAILY_MAX", 30)
    monkeypatch.setattr(FQ.judge, "over_spend_cap", lambda c, settings: (10.0, 10.0))
    assert ramble(api)["why"] == "spend_cap" and len(fake.calls) == 2


def test_what_flow_saves_changes_nothing_a_non_owner_reads_before_publish(api):
    store = api.app.state.store

    def seen():
        with store.read() as c:
            return {
                "strategy": get(api, "strategy", token=MEMBER),
                "vision": S.vision(c),
                "summary": S.summary_text(c),
                "bets": recruit.strategy_bets(c, BEN),
                "questions": get(api, "flow/questions", token=MEMBER),
            }

    write(api, summary="Acme runs rentals with bots.", vision="Every small landlord runs on bots.")
    before = seen()
    # What the page sends for the four basics: sections on the draft (never summary or vision) and an active bet.
    rev = draft(api)["revision"]
    put(
        api,
        "strategy/draft",
        {"revision": rev, "sections": {"serve": "Landlords with 1-20 units.", "not": "No enterprise."}},
    )
    post(
        api,
        "strategy/bets",
        {"statement": "Landlords trust bots", "status": "active", "revision": draft(api)["revision"]},
    )
    put(api, "strategy/draft", {"revision": draft(api)["revision"], "sections": {"sell": "Bots at $29 a unit."}})
    assert draft(api)["sections"]["serve"] == "Landlords with 1-20 units."
    assert seen() == before
