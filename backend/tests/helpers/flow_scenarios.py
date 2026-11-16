"""Flow question scenarios (docs/flow-scenarios.md): each area Flow asks setup questions about is one `Area`, a small
registration of how to build its state, read its questions, answer one through the routes the page uses, and what
"done" means; its `scenarios` are realistic company states with what Flow should ask in each. The invariants in
backend/tests/test_flow_scenarios.py run on every scenario of every area in AREAS. Only Strategy exists today
(backend/flow_questions.py). A scenario with `pending` is what its owner expects once a trigger Flow lacks is built:
its expected question and choices are written down and its test is a strict xfail, which fails the day it passes so
the mark comes off. Areas with no backend yet are data only, in backend/tests/helpers/flow_pending.py. Invented
companies only: Acme (acme.example)."""

from dataclasses import dataclass, field
from typing import Any, Callable

from backend import flow_questions as FQ
from backend import recruit
from backend import strategy as S
from backend import strategy_setup as SS
from backend.auth import Identity
from backend.tests.helpers.api import get, post, put
from backend.tests.helpers.company import bot
from backend.tests.helpers.strategy import draft, publish, write

OWNER = "ana-test"
SITE = "acme.example"  # where setup drafted the Unconfirmed text from
LONG = 200  # a typed answer over this goes to POST /flow/ramble first (ui/app/decide-setup.ts DEC_Q_RAMBLE)


@dataclass(frozen=True)
class Drafted:
    """Text setup drafted from the website, still Unconfirmed (strategy_setup.unconfirmed)."""

    text: str


@dataclass(frozen=True)
class Ask:
    """One question Flow should ask: its key, and where its suggested answer comes from (None: no suggestion)."""

    key: str
    source: str | None = None  # "draft" (the draft's unconfirmed text) or "proposed_bet" (the top proposed bet)
    suggest: str = ""  # the exact suggestion, when the scenario pins it
    question: str = ""  # the owner's expected wording: properties are checked on what Flow asks, never these words
    choices: tuple[str, ...] = ()  # the choices the owner expects, in order (a pending scenario checks them)


@dataclass(frozen=True)
class Scenario:
    """A company state and what Flow asks the owner in it: `asks` in order, then `more` (Keep going). `never` are keys
    Flow must not ask here at all; `note` says why the scenario exists."""

    name: str
    note: str
    state: dict[str, Any]
    asks: tuple[Ask, ...] = ()
    more: tuple[Ask, ...] = ()
    never: tuple[str, ...] = ()
    pending: str = ""  # the trigger Flow lacks for this scenario; empty when it runs today


@dataclass(frozen=True)
class Area:
    """One Flow area. Every callable takes the test client first.

    prepare(api) -> {viewer name: token} for everyone who must get no questions (and whose reads Flow must not change
      before Publish); build(api, state) writes a scenario's state as the owner; read(api, token) is the questions read
      ({"questions": [...], "more": [...]}, each {key, question, suggest, ...}); answer(api, question) answers one the
      way the page does and returns the text saved; source(api, question) is (kind, stored text) the suggestion must
      come from; stored(api) is every stored text a suggestion may quote; confirmed(api) the keys whose part needs no
      question; untouched(api) what Flow must never write (Strategy: the summary and the vision); done(api) whether
      the area may be published; publish(api) publishes it; seen(api, viewers) everything those viewers read.
    `wording` names question keys whose words break the one-sentence rule today, with why: a strict xfail each."""

    name: str
    scenarios: tuple[Scenario, ...]
    prepare: Callable[[Any], dict[str, str]]
    build: Callable[[Any, dict[str, Any]], None]
    read: Callable[[Any, str], dict[str, Any]]
    answer: Callable[[Any, dict[str, Any]], str]
    source: Callable[[Any, dict[str, Any]], tuple[str, str] | None]
    stored: Callable[[Any], list[str]]
    confirmed: Callable[[Any], set[str]]
    untouched: Callable[[Any], dict[str, Any]]
    done: Callable[[Any], bool]
    publish: Callable[[Any], None]
    seen: Callable[[Any, dict[str, str]], dict[str, Any]]
    wording: dict[str, str] = field(default_factory=dict)


# ---- Strategy ---------------------------------------------------------------------------------------------------
SERVE = "Landlords with one to twenty units who still run everything from a spreadsheet."
SELL = "Bots that answer tenants and chase late rent, at $29 a unit a month."
NOT = "No enterprise property managers, even when they ask."
PROOF = "Forty landlords pay us and thirty-one have stayed past six months."
NOW = "Tenants text instead of calling, and bots can now answer them well."
KNOW = "Small landlords hate apps but answer texts within minutes."
HOW = "Landlord meetups and referrals, a free month for each landlord they bring."
BET_1 = "Small landlords will trust a bot with tenant messages"
BET_2 = "Landlords will pay more for same-day repairs"
BET_3 = "Landlords will switch for a bot that files their repair tickets"
STALE = "2026-01-15"  # a bet's check date already passed
CONFIRMED = {"serve": SERVE, "sell": SELL, "not": NOT, "proof": PROOF, "now": NOW, "know": KNOW, "how": HOW}
# What the owner types for each part with no suggestion. What we sell is long, so it goes through the ramble route.
TYPED = {
    "serve": "Landlords with one to twenty units and a day job.",
    "sell": (
        "We sell a bot that answers tenant messages at any hour and chases late rent for the landlord, at twenty-nine "
        "dollars a unit a month, and the landlord reads every message it sends and can step in whenever they want to."
    ),
    "bet": "Landlords will let a bot answer tenants if they can read every message",
    "not": "No commercial property and no custom software.",
    "proof": "Forty landlords pay us today.",
    "now": "Tenants text now instead of calling.",
    "know": "Small landlords answer texts, not apps.",
    "how": "Meetups and referrals.",
}


def _section(value: Any) -> str:
    return SS.unconfirmed(value.text, SITE) if isinstance(value, Drafted) else str(value)


def strategy_prepare(api: Any) -> dict[str, str]:
    """The people and bots who must get no Strategy questions: an admin (Ben), a member (Cara), a bot with its own
    credential, and the owner through their Assistant, both as its turn and as their tap on its proposal."""
    identities = api.app.state.store.settings.test_identities
    identities["ana-assistant"] = Identity("human:ana", "owner", "ana@acme.example", via="assistant")
    identities["ana-assistant-tap"] = Identity(
        "human:ana", "owner", "ana@acme.example", via="assistant", confirmed=True
    )
    return {
        "admin": "ben-test",
        "member": "cara-test",
        "bot": bot(api, "ops"),
        "assistant": "ana-assistant",
        "assistant tap": "ana-assistant-tap",
    }


def _add_bet(api: Any, statement: str, status: str) -> None:
    post(api, "strategy/bets", {"statement": statement, "status": status, "revision": draft(api)["revision"]})


def _add_wrong_bet(api: Any, statement: str, wrong: dict[str, Any]) -> None:
    post(
        api,
        "strategy/bets",
        {"statement": statement, "status": "active", "wrong_if": wrong, "revision": draft(api)["revision"]},
    )


def _kpi_bet(api: Any, statement: str, direction: str, threshold: float, reading: float) -> None:
    """An active bet that is wrong if a KPI goes past a line, and a reading past it."""
    from backend.tests.helpers.kpis import goal_with_kpi, log

    _, kpi = goal_with_kpi(api)
    _add_wrong_bet(api, statement, {"kind": "kpi", "kpi_id": kpi["id"], "direction": direction, "threshold": threshold})
    log(api, kpi["id"], reading)


def strategy_build(api: Any, state: dict[str, Any]) -> None:
    """A Strategy state as the owner makes it: `summary`, `vision`, `sections` ({key: text or Drafted}), `bets`
    ([(statement, status)]), `stale_bet` (an active bet whose words-wrong-if check date passed), `kpi_bet`
    ((statement, direction, threshold, reading): wrong if a KPI crosses a line, and a reading past it), `published`
    (publish it then), and `after` / `after_bets`, written to the draft after."""
    if state.get("summary") or state.get("vision"):
        write(api, summary=state.get("summary", ""), vision=state.get("vision", ""))
    if state.get("sections"):
        write(api, sections={k: _section(v) for k, v in state["sections"].items()})
    for statement, status in state.get("bets", ()):
        _add_bet(api, statement, status)
    if state.get("stale_bet"):
        wrong = {"kind": "text", "text": "fewer than ten landlords let it answer alone", "check_by": STALE}
        _add_wrong_bet(api, state["stale_bet"], wrong)
    if state.get("kpi_bet"):
        _kpi_bet(api, *state["kpi_bet"])
    if state.get("published"):
        publish(api)
    if state.get("after"):
        write(api, sections={k: _section(v) for k, v in state["after"].items()})
    for statement, status in state.get("after_bets", ()):
        _add_bet(api, statement, status)


def strategy_read(api: Any, token: str) -> dict[str, Any]:
    return get(api, "flow/questions", token=token)


def _merge(body: str, line: str) -> str:
    """ui/app/decide-setup.ts decideQMerge: a line after what is there, or in place of drafted Unconfirmed text."""
    return line if not body.strip() or FQ.MARKER.match(body) else f"{body.rstrip()}\n\n{line}"


def _save_lines(api: Any, lines: list[dict[str, Any]]) -> None:
    """ui/app/decide-setup.ts decideQSave: the sections in one draft write, then each bet, made active."""
    sections = [x for x in lines if x["section"] != FQ.BET]
    if sections:
        body = draft(api)
        now: dict[str, str] = {}
        for x in sections:
            now[x["section"]] = _merge(now.get(x["section"], body["sections"].get(x["section"]) or ""), x["text"])
        put(api, "strategy/draft", {"revision": body["revision"], "sections": now})
    for x in lines:
        if x["section"] == FQ.BET:
            sent = {"statement": x["text"], "status": "active", "revision": draft(api)["revision"]}
            post(api, "strategy/bets", {**sent, **({"why": x["why"]} if x.get("why") else {})})


def strategy_answer(api: Any, q: dict[str, Any]) -> str:
    """One question answered as the page does: the suggestion tapped when there is one (a proposed bet made active),
    else the owner's typed answer, a long one sent to POST /flow/ramble first and its lines saved."""
    if q["suggest"]:
        if q["key"] == FQ.BET and q["bet_id"]:
            post(api, "strategy/bets/" + q["bet_id"], {"status": "active", "revision": draft(api)["revision"]})
        else:
            _save_lines(api, [{"section": q["key"], "text": q["suggest"]}])
        return q["suggest"]
    text = TYPED[q["key"]]
    if len(text) > LONG:
        split = post(api, "flow/ramble", {"text": text, "section": q["key"]})
        _save_lines(api, split["lines"])
    else:
        _save_lines(api, FQ.whole(text, q["key"]))
    return text


def strategy_source(api: Any, q: dict[str, Any]) -> tuple[str, str] | None:
    """Where a question's suggestion must come from: the section's own drafted text, or the proposed bet it names."""
    with api.app.state.store.read() as c:
        if q["key"] == FQ.BET:
            found = S.bet(c, q["bet_id"]) if q.get("bet_id") else None
            return ("proposed_bet", found["statement"]) if found and found["status"] == "proposed" else None
        body = str(S.draft(c)["sections"].get(q["key"]) or "")
    return ("draft", body) if FQ.unconfirmed(body) is not None else None


def strategy_stored(api: Any) -> list[str]:
    with api.app.state.store.read() as c:
        sections = S.draft(c)["sections"]
        texts = [str(v) for k, v in sections.items() if k != "extra" and v]
        return texts + [b["statement"] for b in S.bets(c)]


def strategy_confirmed(api: Any) -> set[str]:
    with api.app.state.store.read() as c:
        sections = S.draft(c)["sections"]
        out = {k for k in FQ.KEYS if k != FQ.BET and not FQ.open_section(str(sections.get(k) or ""))}
        return out | ({FQ.BET} if S.bets(c, "active") else set())


def strategy_untouched(api: Any) -> dict[str, Any]:
    row = draft(api)
    return {"summary": row["summary"], "vision": row["vision"], "guide": row["guide"]}


def strategy_done(api: Any) -> bool:
    """What Publish needs besides who and what (strategy.publish): one active bet and a What we won't do."""
    with api.app.state.store.read() as c:
        return bool(S.bets(c, "active")) and bool(S.draft(c)["sections"]["not"].strip())


def strategy_publish(api: Any) -> None:
    publish(api, note="")


def _peek(api: Any, path: str, token: str) -> tuple[int, Any]:
    from backend.tests.helpers.api import headers

    r = api.get("/api/v2/" + path, headers=headers(token))
    return r.status_code, r.json()


def strategy_seen(api: Any, viewers: dict[str, str]) -> dict[str, Any]:
    """Everything the viewers read of the strategy: the Strategy page, the questions, and what a bot's run gets (the
    published version, the bets a bot administrator is given, the team's vision and summary)."""
    out: dict[str, Any] = {}
    for name, token in viewers.items():
        out[name] = {"strategy": _peek(api, "strategy", token), "questions": _peek(api, "flow/questions", token)}
    with api.app.state.store.read() as c:
        published = S.current(c)
        out["bots"] = {
            "published": {k: published[k] for k in ("n", "summary", "vision", "sections", "bets")}
            if published
            else None,
            "bets": recruit.strategy_bets(c, Identity("human:ben", "human", "ben@acme.example")),
            "vision": S.vision(c),
            "summary": S.summary_text(c),
        }
    return out


STRATEGY = Area(
    name="strategy",
    prepare=strategy_prepare,
    build=strategy_build,
    read=strategy_read,
    answer=strategy_answer,
    source=strategy_source,
    stored=strategy_stored,
    confirmed=strategy_confirmed,
    untouched=strategy_untouched,
    done=strategy_done,
    publish=strategy_publish,
    seen=strategy_seen,
    wording={
        "proof": "the guide's proof ask is two sentences (How do you know people want it? Who uses it now, ...?)",
        "now": "the guide's now ask is two sentences (Why now? What changed recently ...?)",
        "know": "the first know ask in guide order is why-you, two sentences (Why are you the ones ...? What have ...?)",
        "how": "the guide's how ask is two sentences and ends in a period (... come from? Name the channel, or the people.)",
    },
    scenarios=(
        Scenario(
            "empty",
            "A brand-new company: nothing written, no bets. Every basic is asked, with nothing to suggest.",
            {},
            asks=(Ask("serve"), Ask("sell"), Ask("bet"), Ask("not")),
            more=(Ask("proof"), Ask("now"), Ask("know"), Ask("how")),
        ),
        Scenario(
            "site-drafted",
            "Setup drafted every part from the website, all still Unconfirmed, and proposed two bets: each question "
            "offers the drafted text, the bet the top proposed one, never the guide's claim.",
            {
                "sections": {k: Drafted(v) for k, v in CONFIRMED.items()},
                "bets": [(BET_1, "proposed"), (BET_2, "proposed")],
            },
            asks=(
                Ask("serve", "draft", SERVE),
                Ask("sell", "draft", SELL),
                Ask("bet", "proposed_bet", BET_1),
                Ask("not", "draft", NOT),
            ),
            more=(
                Ask("proof", "draft", PROOF),
                Ask("now", "draft", NOW),
                Ask("know", "draft", KNOW),
                Ask("how", "draft", HOW),
            ),
        ),
        Scenario(
            "half-done",
            "Who and what confirmed, proof still drafted, no bet but a retired one, no What we won't do: the bet and "
            "the won't-do are asked with nothing to suggest (a retired bet is never offered).",
            {
                "sections": {"serve": SERVE, "sell": SELL, "proof": Drafted(PROOF)},
                "bets": [(BET_2, "retired")],
            },
            asks=(Ask("bet"), Ask("not")),
            more=(Ask("proof", "draft", PROOF), Ask("now"), Ask("know"), Ask("how")),
            never=("serve", "sell"),
        ),
        Scenario(
            "proposed-bets-only",
            "Nothing written, two proposed bets: the bet question offers the first, by rank.",
            {"bets": [(BET_1, "proposed"), (BET_2, "proposed")]},
            asks=(Ask("serve"), Ask("sell"), Ask("bet", "proposed_bet", BET_1), Ask("not")),
            more=(Ask("proof"), Ask("now"), Ask("know"), Ask("how")),
        ),
        Scenario(
            "basics-done",
            "Who, what, won't-do confirmed and a bet active beside a proposed one: no basics; Keep going asks the "
            "empty optional parts.",
            {
                "sections": {"serve": SERVE, "sell": SELL, "not": NOT},
                "bets": [(BET_1, "active"), (BET_2, "proposed")],
            },
            more=(Ask("proof"), Ask("now"), Ask("know"), Ask("how")),
            never=("serve", "sell", "bet", "not"),
        ),
        Scenario(
            "published-nothing-open",
            "Everything confirmed and published: Flow asks nothing.",
            {"sections": dict(CONFIRMED), "bets": [(BET_1, "active")], "published": True},
            never=FQ.KEYS,
        ),
        Scenario(
            "published-then-edited",
            "Published, then setup drafted who we serve again from the site and the owner cleared How we win: Flow "
            "asks from the draft, not the published version, which the team still reads.",
            {
                "sections": dict(CONFIRMED),
                "bets": [(BET_1, "active")],
                "published": True,
                "after": {"serve": Drafted("Landlords with up to fifty units."), "how": ""},
            },
            asks=(Ask("serve", "draft", "Landlords with up to fifty units."),),
            more=(Ask("how"),),
            never=("sell", "bet", "not", "proof", "now", "know"),
        ),
        # ---- What the Strategy owner expects once Flow has the trigger (round 2, owner states 4 to 6), and the
        # bet question's choices it asked for. Each is a strict xfail until built.
        Scenario(
            "three-drafted-bets",
            "Setup proposed three bets: the bet question offers all three to pick from, not only the top one.",
            {"bets": [(BET_1, "proposed"), (BET_2, "proposed"), (BET_3, "proposed")]},
            asks=(
                Ask("serve"),
                Ask("sell"),
                Ask("bet", "proposed_bet", BET_1, choices=(BET_1, BET_2, BET_3)),
                Ask("not"),
            ),
            pending="Flow offers only the top proposed bet; the owner asked for up to three as choices",
        ),
        Scenario(
            "published-stale-bet",
            "Published, and the active bet's check date has passed: Flow asks whether it still holds.",
            {"sections": dict(CONFIRMED), "stale_bet": BET_1, "published": True},
            asks=(
                Ask(
                    "bet",
                    question=f"Is your bet '{BET_1}' still holding?",
                    choices=("Still holding, check again in 90 days", "Rewrite it", "Retire it"),
                ),
            ),
            pending="nothing checks a bet's check date for Flow",
        ),
        Scenario(
            "bet-proved-wrong",
            "Published, and the number in the active bet's wrong-if crossed its line: Flow asks what the owner does. "
            "(The owner's own wording, \"Your bet '...' looks wrong: [number] is past [line]. What do you do?\", opens "
            "with a statement, which the rubric's plain-sentence check fails: kept as one question here.)",
            {"sections": dict(CONFIRMED), "kpi_bet": (BET_1, "below", 30.0, 22.0), "published": True},
            asks=(
                Ask(
                    "bet",
                    question="Activation is 22%, below the 30% your bet needs: what do you do with the bet?",
                    choices=("Retire it and name a new one", "Keep it, change the test", "Keep it as is"),
                ),
            ),
            pending="a crossed wrong-if goes to the Strategy bot as a task, not to the owner's Flow",
        ),
        Scenario(
            "new-competitor",
            "Published, and a market watch saw a competitor launch: Flow asks whether it changes the bet.",
            {"sections": dict(CONFIRMED), "bets": [(BET_1, "active")], "published": True},
            asks=(
                Ask(
                    "bet",
                    question="Does Globex's new tenant bot change your bet?",
                    choices=("No, add why to What we know", "Yes, rewrite the bet", "Yes, add to What we won't do"),
                ),
            ),
            pending="there is no market watch to see a competitor's launch",
        ),
    ),
)

AREAS = (STRATEGY,)


def cases() -> list[tuple[Area, Scenario]]:
    """Every scenario that runs today."""
    return [(a, s) for a in AREAS for s in a.scenarios if not s.pending]


def pending_cases() -> list[tuple[Area, Scenario]]:
    """Every scenario waiting on a trigger Flow lacks."""
    return [(a, s) for a in AREAS for s in a.scenarios if s.pending]


def case_id(case: tuple[Area, Scenario]) -> str:
    return f"{case[0].name}/{case[1].name}"
