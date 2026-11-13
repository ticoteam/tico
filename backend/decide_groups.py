# pyright: strict
"""Asking Decide (Jev) about a list of small items in as few calls as fit: one shared way for every pass over a queue.

Jev charges for what it reads, so one call per small item pays the call's fixed cost again for every item. A pass
hands its items here as `(state, questions)` pairs, all under one question set's label, and gets back, for each item,
only its own answers, in the shape one call about that item alone returns.

How a group is asked: up to MOST items whose states and questions fit one call (`J.fits`: the state within
`J.MAX_STATE_CHARS`, the whole call within `J.MAX_CALL_CHARS` and at most `J.MAX_QUESTIONS`, the Gateway's limit) go in
one call. The state lists the items by name (`item1`, `item2`, ...) under `items`; each item's questions are asked again
under `<name>__<id>`, their instructions opened with the item they are about, and nothing else about them changes. A
group of one is sent exactly as the item alone always was, so a pass kept at one item per call (`size=1`) asks as it
did before grouping.

What comes back:
- every answer is read back under its own item's names only, so an item never gets another item's answer;
- an item whose answers are missing or malformed is asked again on its own, carrying its share of the grouped call's
  cost besides its own call's; the grouped call is made within `J.partial_answers`, so a transport keeps the answers
  that are there rather than refusing the whole map for one missing answer. A grouped answer that cannot be read at
  all (`shape`) asks each item again on its own;
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from clients import judge as J

Row = dict[str, Any]
Item = tuple[Any, Row]  # (state, questions) for one item, as one call about it alone takes them
Engine = Callable[..., Any]
Reply = Row | BaseException

_J: Any = J  # the client is plain, untyped stdlib code shared with the CLI and the runner
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
    result: Row | None = None
    error: BaseException | None = None
    sent: bool = True
    answers: list[Row] = field(default_factory=lambda: [])  # each held item's own answers, in `items` order


def _size(state: Any) -> int:
    try:
        return len(json.dumps(state, default=str))
    except (TypeError, ValueError):
        return int(_J.MAX_STATE_CHARS) + 1


def packs(items: Sequence[Item], size: int = MOST) -> list[list[int]]:
    """The items' places, in order, cut into runs that fit one call each: at most `size` items, their states within
    J.MAX_STATE_CHARS once grouped and the whole grouped call within J.MAX_CALL_CHARS and J.MAX_QUESTIONS (`J.fits`).
    An item too big to share a call goes alone."""
    size = max(1, int(size))
    most_chars = int(_J.MAX_STATE_CHARS)
    out: list[list[int]] = []
    run: list[int] = []
    chars = WRAP
    for place, (state, _questions) in enumerate(items):
        mine = _size(state) + len(NAME.format(len(run) + 1)) + 4
        fits = len(run) < size and chars + mine <= most_chars and _fits_with(items, run, place)
        if run and not fits:
            out.append(run)
            run, chars = [], WRAP
            mine = _size(state) + len(NAME.format(1)) + 4
        run.append(place)
        chars += mine
    if run:
        out.append(run)
    return out


def _fits_with(items: Sequence[Item], run: list[int], place: int) -> bool:
    """Whether the grouped call about `run` and `place` is within the client's size limits, questions included."""
    state, questions, _ = grouped([items[p] for p in [*run, place]])
    return bool(_J.fits(state, questions))


def _about(name: str, text: Any) -> Any:
    lead = f"About {name} only (state.items.{name}):"
    return f"{lead} {text}" if isinstance(text, str) else [lead, text]


def grouped(items: Sequence[Item]) -> tuple[Row, Row, list[dict[str, str]]]:
    """(state, questions, ids) for one call about `items`: `ids[n]` maps the grouped question ids back to item n's
    own."""
    held: Row = {}
    questions: Row = {}
    ids: list[dict[str, str]] = []
    for n, (one, asked) in enumerate(items, start=1):
        name = NAME.format(n)
        held[name] = one
        mine: dict[str, str] = {}
        for qid, q in asked.items():
            key = f"{name}{SEP}{qid}"
            question: Row = dict(q)
            questions[key] = {**question, "instructions": _about(name, question.get("instructions"))}
            mine[key] = qid
        ids.append(mine)
    return {"how": HOW, "items": held}, questions, ids


def _own(answers: Row, ids: dict[str, str]) -> Row:
    """Item answers under its own ids, read only from its own names."""
    return {qid: answers[key] for key, qid in ids.items() if key in answers}


def _alone(engine: Engine, item: Item, label: str | None, place: int, calls: list[Call]) -> Reply:
    state, questions = item
    try:
        result: Row = dict(engine(state, questions, label) or {})
    except Exception as exc:  # noqa: BLE001 - the item's own failure, handled by its pass
        calls.append(Call([place], len(questions), error=exc))
        return exc
    answers: Any = result.get("answers")
    calls.append(Call([place], len(questions), result=result, answers=[answers if isinstance(answers, dict) else {}]))
    return result


def _missing() -> BaseException:
    return _J.JudgeError("shape", "no reply")


def ask(
    engine: Engine, items: Sequence[Item], label: str | None = None, size: int = MOST
) -> tuple[list[Reply], list[Call]]:
    """Ask `engine` about every `(state, questions)` in `items`, `size` at most per call. Returns each item's reply in
    order (its own result dict, `answers` keyed by its own question ids; or the exception its call ended in) and the
    calls made, one `Call` each. A pass that checks something between calls (time left, a cap) asks one `packs` run at
    a time instead."""
    replies: list[Reply | None] = [None] * len(items)
    calls: list[Call] = []
    for run in packs(items, size):
        for place, reply in zip(run, ask_run(engine, [items[p] for p in run], label, calls, run), strict=True):
            replies[place] = reply
    return [r if r is not None else _missing() for r in replies], calls


def ask_run(
    engine: Engine,
    items: Sequence[Item],
    label: str | None = None,
    calls: list[Call] | None = None,
    places: Sequence[int] | None = None,
) -> list[Reply]:
    """One run from `packs`: one call for all of them (or the item's own call when alone), each item's missing answer
    asked again alone. Appends what was sent to `calls`, the items named by `places` (their places in the pass's own
    list; 0.. by default)."""
    calls = calls if calls is not None else []
    where = list(places) if places is not None else list(range(len(items)))
    replies: list[Reply | None] = [None] * len(items)
    fine: list[int] = []
    for n, (state, questions) in enumerate(items):
        try:
            _J.validate(state, questions, label)
        except J.JudgeError as exc:
            calls.append(Call([where[n]], len(questions), error=exc, sent=False))
            replies[n] = exc
            continue
        fine.append(n)
    if len(fine) == 1:
        n = fine[0]
        replies[n] = _alone(engine, items[n], label, where[n], calls)
    elif fine:
        state, questions, ids = grouped([items[n] for n in fine])
        result: Row | None = None
        error: BaseException | None = None
        sent = True
        try:
            _J.validate(state, questions, label)
        except J.JudgeError as exc:
            error, sent = exc, False
        if sent:
            try:
                # Each item's answers are checked below, on their own: one missing answer is not the whole call's.
                with _J.partial_answers():
                    result = dict(engine(state, questions, label) or {})
            except Exception as exc:  # noqa: BLE001 - the group's failure is each item's, below
                error = exc
        code: Any = getattr(error, "code", None)
        unreadable = isinstance(error, J.JudgeError) and code in ("shape", "invalid")
        if error is not None and not unreadable:
            calls.append(Call([where[n] for n in fine], len(questions), error=error))
            for n in fine:
                replies[n] = error
        elif error is not None:
            # A grouped call that could not be sent or read as a whole: each item is asked on its own, as before.
            if sent:
                calls.append(Call([where[n] for n in fine], len(questions), error=error))
            for n in fine:
                replies[n] = _alone(engine, items[n], label, where[n], calls)
        elif result is not None:
            given: Any = result.get("answers")
            answers: Row = cast(Row, given) if isinstance(given, dict) else {}
            share = round(float(result.get("cost_usd") or 0) / len(fine), 6)
            meta = {k: v for k, v in result.items() if k not in ("answers", "cost_usd", "usage", "paid", "ref")}
            call = Call([], len(questions), result=result)
            calls.append(call)
            again: list[int] = []
            for n, mine in zip(fine, ids, strict=True):
                try:
                    own: Row = _J.check_answers(_own(answers, mine), items[n][1])
                except J.JudgeError:
                    again.append(n)
                    continue
                call.items.append(where[n])
                call.answers.append(own)
                replies[n] = {**meta, "answers": own, "cost_usd": share, "grouped": len(fine)}
            for n in again:
                reply = _alone(engine, items[n], label, where[n], calls)
                if isinstance(reply, dict):  # its share of the grouped call was paid too
                    reply = {**reply, "cost_usd": round(float(reply.get("cost_usd") or 0) + share, 6)}
                replies[n] = reply
            if not call.items:  # every item asked again: the call still happened and was paid
                call.items = [where[n] for n in fine]
                call.answers = [{} for _ in fine]
    return [r if r is not None else _missing() for r in replies]


def split(state: Any, questions: Row) -> list[tuple[str, Any, dict[str, str]]] | None:
    """The items of a grouped call, for a fake engine in a test: [(name, state, {own id: grouped id})], or None when
    the call is about one item alone."""
    if not isinstance(state, dict):
        return None
    grouped_state = cast(Row, state)
    held: Any = grouped_state.get("items")
    if grouped_state.get("how") != HOW or not isinstance(held, dict):
        return None
    items = cast(Row, held)
    out: list[tuple[str, Any, dict[str, str]]] = []
    for name, one in items.items():
        mine = {key.split(SEP, 1)[1]: key for key in questions if key.startswith(name + SEP)}
        out.append((name, one, mine))
    return out


def audit_row(call: Call, label: str | None = None, **extra: Any) -> Row:
    """The `judge.call` row's detail for one `Call`: one row per call, so the readout counts calls sent, not items. A
    grouped call keeps each item's answers as a list (the readout reads a list as one map per item) and says how many
    items it held."""
    from . import judge as decide

    server: Any = decide

    many = {"items": len(call.items)} if len(call.items) > 1 else {}
    if call.error is not None or call.result is None:
        return {"label": label, **extra, "questions": call.questions, "error": _J.error_code(call.error), **many}
    answers: list[Any] = [_J.summary(a) for a in call.answers]
    detail = cast(Row, server.audit_detail(call.result))
    return {
        "label": label,
        **extra,
        "questions": call.questions,
        **detail,
        "answers": answers if many else (answers[0] if answers else {}),
        **many,
    }
