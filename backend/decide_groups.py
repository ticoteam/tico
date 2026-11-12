"""Asking Decide (Jev) about a list of small items in as few calls as fit: one shared way for every pass over a queue.

Jev charges for what it reads, so one call per small item pays the call's fixed cost again for every item. A pass
hands its items here as `(state, questions)` pairs, all under one question set's label, and gets back, for each item,
only its own answers, in the shape one call about that item alone returns.

How a group is asked: up to MOST items whose states and questions fit one call (`J.MAX_STATE_CHARS`, and
`J.MAX_QUESTIONS` while the client has one) go in one call. The state lists the items by name (`item1`, `item2`, ...)
under `items`; each item's questions are asked again under `<name>__<id>`, their instructions opened with the item they
are about, and nothing else about them changes. A group of one is sent exactly as the item alone always was, so a pass
kept at one item per call (`size=1`) asks as it did before grouping.

What comes back:
- every answer is read back under its own item's names only, so an item never gets another item's answer;
- an item whose answers are missing or malformed is asked again on its own (`retry`); when the grouped call itself
  comes back unreadable (`shape`, which a transport raises when any one answer is missing), each item is asked again
  on its own;
- any other failure of the grouped call (a timeout, the spend cap, a refused key) is each item's failure, as if its
  own call had failed: the pass handles it per item as it always did (a try, a wait, or a person);
- an item the contract refuses on its own (`J.validate`) gets that refusal and is sent in no call.

Each paid call is one `Call`, so a pass writes one `judge.call` audit row per call, not per item, and the readout
counts what was really sent. Each answered item carries its share of the call's cost (`cost_usd`), so a per-item spend
(listening's cap) still adds up to what was paid.

Not for injection screening, skill screening or live meetings: those stay one text per call on purpose (a hostile text
must not sway the verdict on the texts beside it; a skill bundle nearly fills a call alone; meeting windows arrive one
at a time).
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from clients import judge as J

MOST = 10  # items in one call at most: a failed call fails no more than this many
NAME = "item{}"
SEP = "__"
HOW = (
    "Several separate items, each under its own name in `items`. Each question names the one item it is about: "
    "answer it from that item alone, as if the other items were not there."
)
WRAP = len(json.dumps({"how": HOW, "items": {}}))  # the grouped state's own characters, around the items


@dataclass
class Call:
    """One call sent (or one refusal before sending): the items it held, by their place in the pass's list, and what
    came back. `result` is the engine's answer for a call that answered; `error` the failure otherwise. `sent` is
    False for an item the contract refused before any call."""

    items: list[int]
    questions: int
    result: dict | None = None
    error: BaseException | None = None
    sent: bool = True
    answers: list[dict] = field(default_factory=list)  # each held item's own answers, in `items` order


def _size(state):
    try:
        return len(json.dumps(state, default=str))
    except (TypeError, ValueError):
        return J.MAX_STATE_CHARS + 1


def packs(items, size=MOST):
    """The items' places, in order, cut into runs that fit one call each: at most `size` items, their states within
    J.MAX_STATE_CHARS once grouped and, while the client caps it, their questions within J.MAX_QUESTIONS. An item too
    big to share a call goes alone."""
    size = max(1, int(size))
    most_questions = getattr(J, "MAX_QUESTIONS", None)
    out, run, chars, asked = [], [], WRAP, 0
    for place, (state, questions) in enumerate(items):
        mine = _size(state) + len(NAME.format(len(run) + 1)) + 4
        count = len(questions) if isinstance(questions, dict) else 0
        fits = (
            len(run) < size
            and chars + mine <= J.MAX_STATE_CHARS
            and (most_questions is None or asked + count <= most_questions)
        )
        if run and not fits:
            out.append(run)
            run, chars, asked = [], WRAP, 0
            mine = _size(state) + len(NAME.format(1)) + 4
        run.append(place)
        chars += mine
        asked += count
    if run:
        out.append(run)
    return out


def _about(name, text):
    lead = f"About {name} only (state.items.{name}):"
    return f"{lead} {text}" if isinstance(text, str) else [lead, text]


def grouped(items):
    """(state, questions, ids) for one call about `items`: `ids[n]` maps the grouped question ids back to item n's
    own."""
    state, questions, ids = {"how": HOW, "items": {}}, {}, []
    for n, (one, asked) in enumerate(items, start=1):
        name = NAME.format(n)
        state["items"][name] = one
        mine = {}
        for qid, q in asked.items():
            key = f"{name}{SEP}{qid}"
            questions[key] = {**q, "instructions": _about(name, q.get("instructions"))}
            mine[key] = qid
        ids.append(mine)
    return state, questions, ids


def _own(answers, ids):
    """Item answers under its own ids, read only from its own names."""
    return {qid: answers[key] for key, qid in ids.items() if key in answers}


def _alone(engine, state, questions, label, place, calls):
    try:
        result = engine(state, questions, label)
    except Exception as exc:  # noqa: BLE001 - the item's own failure, handled by its pass
        calls.append(Call([place], len(questions), error=exc))
        return exc
    result = dict(result or {})
    calls.append(Call([place], len(questions), result=result, answers=[result.get("answers") or {}]))
    return result


Reply = dict | BaseException


def ask(engine: Callable[..., Any], items, label=None, size=MOST) -> tuple[list[Reply], list[Call]]:
    """Ask `engine` about every `(state, questions)` in `items`, `size` at most per call. Returns each item's reply in
    order (its own result dict, `answers` keyed by its own question ids; or the exception its call ended in) and the
    calls made, one `Call` each. A pass that checks something between calls (time left, a cap) asks one `packs` run at
    a time instead."""
    replies: list[Reply | None] = [None] * len(items)
    calls: list[Call] = []
    for run in packs(items, size):
        for place, reply in zip(run, ask_run(engine, [items[p] for p in run], label, calls, run), strict=True):
            replies[place] = reply
    return [r if r is not None else J.JudgeError("shape", "no reply") for r in replies], calls


def ask_run(engine, items, label=None, calls=None, places=None) -> list[Reply]:
    """One run from `packs`: one call for all of them (or the item's own call when alone), each item's missing answer
    asked again alone. Appends what was sent to `calls`, the items named by `places` (their places in the pass's own
    list; 0.. by default)."""
    calls = calls if calls is not None else []
    places = list(places) if places is not None else list(range(len(items)))
    replies: list[Reply | None] = [None] * len(items)
    fine = []
    for n, (state, questions) in enumerate(items):
        try:
            J.validate(state, questions, label)
        except J.JudgeError as exc:
            calls.append(Call([places[n]], len(questions) if isinstance(questions, dict) else 0, error=exc, sent=False))
            replies[n] = exc
            continue
        fine.append(n)
    if len(fine) == 1:
        n = fine[0]
        replies[n] = _alone(engine, items[n][0], items[n][1], label, places[n], calls)
    elif fine:
        state, questions, ids = grouped([items[n] for n in fine])
        result, error, sent = None, None, True
        try:
            J.validate(state, questions, label)
        except J.JudgeError as exc:
            error, sent = exc, False
        if sent:
            try:
                result = dict(engine(state, questions, label) or {})
            except Exception as exc:  # noqa: BLE001 - the group's failure is each item's, below
                error = exc
        unreadable = isinstance(error, J.JudgeError) and error.code in ("shape", "invalid")
        if error is not None and not unreadable:
            calls.append(Call([places[n] for n in fine], len(questions), error=error))
            for n in fine:
                replies[n] = error
        elif error is not None:
            # A grouped call that could not be sent or read as a whole: each item is asked on its own, as before.
            if sent:
                calls.append(Call([places[n] for n in fine], len(questions), error=error))
            for n in fine:
                replies[n] = _alone(engine, items[n][0], items[n][1], label, places[n], calls)
        else:
            assert result is not None
            answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
            share = round(float(result.get("cost_usd") or 0) / len(fine), 6)
            meta = {k: v for k, v in result.items() if k not in ("answers", "cost_usd", "usage", "paid", "ref")}
            call = Call([], len(questions), result=result)
            calls.append(call)
            again = []
            for n, mine in zip(fine, ids, strict=True):
                own = _own(answers, mine)
                try:
                    own = J.check_answers(own, items[n][1])
                except J.JudgeError:
                    again.append(n)
                    continue
                call.items.append(places[n])
                call.answers.append(own)
                replies[n] = {**meta, "answers": own, "cost_usd": share, "grouped": len(fine)}
            for n in again:
                replies[n] = _alone(engine, items[n][0], items[n][1], label, places[n], calls)
            if not call.items:  # every item asked again: the call still happened and was paid
                call.items = [places[n] for n in fine]
                call.answers = [{} for _ in fine]
    return [r if r is not None else J.JudgeError("shape", "no reply") for r in replies]


def split(state, questions):
    """The items of a grouped call, for a fake engine in a test: [(name, state, {own id: grouped id})], or None when
    the call is about one item alone."""
    if not (isinstance(state, dict) and state.get("how") == HOW and isinstance(state.get("items"), dict)):
        return None
    out = []
    for name, one in state["items"].items():
        mine = {key.split(SEP, 1)[1]: key for key in questions if key.startswith(name + SEP)}
        out.append((name, one, mine))
    return out


def audit_row(call: Call, label=None, **extra):
    """The `judge.call` row's detail for one `Call`: one row per call, so the readout counts calls sent, not items. A
    grouped call keeps each item's answers as a list (the readout reads a list as one map per item) and says how many
    items it held."""
    from .judge import audit_detail

    many = {"items": len(call.items)} if len(call.items) > 1 else {}
    if call.error is not None or call.result is None:
        return {"label": label, **extra, "questions": call.questions, "error": J.error_code(call.error), **many}
    answers = [J.summary(a) for a in call.answers]
    return {
        "label": label,
        **extra,
        "questions": call.questions,
        **audit_detail(call.result),
        "answers": answers if many else (answers[0] if answers else {}),
        **many,
    }
