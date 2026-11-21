# pyright: strict
"""A person's snooze of one ask in their own Needs you (#3769): Flow's Snooze on an ask, in place of Skip.

The ask (its visit and requirement, `waiting_on.ask_id`) leaves that person's Needs you, its count, Flow and the Tasks
badge until the time they picked, and comes back then with nothing run: the list leaves out a snooze only while its
time is to come (`waiting_on.ask_snoozes`). It also comes back when the ask is asked again or asks something else: a
snooze holds only while the ask was asked at the moment it was snoozed (`asked_at`, the ask's `since`) and asks the
same (`ask_version`: its words and each page it shows at the same version, as Flow's opinion names it). A
bot's ❓ (backend/question_marks.py) snoozes the same way, keyed on its mark (`qmark:<mark id>`): a new ❓ is a new mark.

Nobody else's list changes, and the task does not: its bot is not told, not woken and not held, and the whole-task
snooze (`POST /api/v2/tasks/{tid}/snooze`) stays what it is. Only a person the ask waits on snoozes it, for themselves
(or their own Assistant, with their rights); no one reads or changes another person's snoozes, and a bot never does.

    POST /api/v2/needs-you/snooze    {id, until}  the ask leaves the caller's list until `until` (at most a year on)
    POST /api/v2/needs-you/unsnooze  {id}         it is back now
    GET  /api/v2/needs-you/snoozed                the caller's snoozed asks, as Needs you lists them, each with its end
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from .store import H, Problem
from .api_contract import api_v2

Item = dict[str, Any]
_is_human = cast(Callable[[object], bool], H.is_human)


def _needs(c: Any, auth: Any, who: Any, **how: Any) -> list[Item]:
    """The caller's Needs you, as it is served (views.needs_shown)."""
    from . import views

    return cast(Callable[..., list[Item]], views.needs_shown)(c, auth, who, **how)


MAX_DAYS = 366  # the latest a snooze may end: a year on (a leap year's day included)
NOT_YOURS = "Only a person this ask waits on snoozes it, for themselves"


def person(who: Any) -> str:
    """The person a snooze is for: the caller, signed in as themselves (a browser, their own API token) or their own
    Assistant acting with their rights. A bot, BotOps or a page chat acting for them, support and a bot's read-only view
    are refused."""
    via = getattr(who, "via", "") or ""
    if (
        who.role not in ("owner", "human")
        or not _is_human(who.actor)
        or getattr(who, "support", "")
        or getattr(who, "view", "")
        or via not in ("", "assistant")
        or (getattr(who, "task_actor", "") and via != "assistant")
    ):
        raise Problem("forbidden", NOT_YOURS, 403)
    return str(who.actor)


def until_of(value: object) -> str:
    """`value` as the stored end: a moment to come, at most MAX_DAYS on."""
    from .task_moves_routes import parse_until

    stamp = parse_until(value)
    if not stamp:
        raise Problem("date", "until is a date-time to come", 422)
    latest = (datetime.now(timezone.utc) + timedelta(days=MAX_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    if stamp > latest:
        raise Problem("date", "until is at most a year on", 422)
    return stamp


def _ask(c: Any, auth: Any, who: Any, ask_id: str) -> dict[str, Any]:
    """The ask `ask_id` in the caller's Needs you, snoozed or not: refused when it is not an ask that waits on them
    (another person's, one they may not read, one answered or gone)."""
    for item in _needs(c, auth, who, count_only=True, snoozed="keep"):
        if item["kind"] in ("ask", "question") and str(item["id"]) == ask_id:  # a ❓ too (backend/question_marks.py)
            return item
    raise Problem("not_found", "No such ask waits on you", 404)


def _version(c: Any, item: Item) -> str:
    """What the ask asks as the snooze is made (waiting_on.ask_version): once it asks something else, it is back."""
    from . import task_engine, waiting_on

    if item["kind"] == "question":
        return ""  # a ❓ asks what its message says: a new one is a new mark, so it is back
    row = cast(Callable[..., Item | None], task_engine.task)(c, item["task_id"])
    waits: list[waiting_on.Wait] = waiting_on.of(c, row) if row else []
    wait = next((w for w in waits if w.ask and str(w.requirement_id) == str(item["requirement_id"])), None)
    return waiting_on.ask_version(c, row, wait) if row and wait else ""


def snooze(c: Any, auth: Any, who: Any, ask_id: str, until: str) -> dict[str, Any]:
    """The ask leaves the caller's Needs you until `until`. Snoozing it again moves the end."""
    me = person(who)
    item = _ask(c, auth, who, ask_id)
    visit_id, _, requirement_id = ask_id.partition(":")
    now = H.now()
    c.execute(
        "INSERT INTO ask_snoozes (person_id, visit_id, requirement_id, task_id, asked_at, ask_version, snoozed_until, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(person_id, visit_id, requirement_id) DO UPDATE "
        "SET task_id=excluded.task_id, asked_at=excluded.asked_at, ask_version=excluded.ask_version, "
        "snoozed_until=excluded.snoozed_until, updated_at=excluded.updated_at",
        (
            me,
            visit_id,
            requirement_id,
            str(item["task_id"] or ""),
            str(item.get("since") or ""),
            _version(c, item),
            until,
        )
        + (now, now),
    )
    return {"id": ask_id, "snoozed_until": until}


def unsnooze(c: Any, who: Any, ask_id: str) -> dict[str, Any]:
    """The ask is back in the caller's Needs you now. Only their own snooze is touched; none is no change."""
    me = person(who)
    visit_id, _, requirement_id = ask_id.partition(":")
    c.execute(
        "DELETE FROM ask_snoozes WHERE person_id=? AND visit_id=? AND requirement_id=?", (me, visit_id, requirement_id)
    )
    return {"id": ask_id}


def next_end(c: Any, who: Any) -> str | None:
    """When the caller's next snoozed ask comes back, or None: the page reads Needs you again then."""
    from . import waiting_on

    if not _is_human(getattr(who, "actor", "")):
        return None
    ends = [end for _asked, _version, end in waiting_on.ask_snoozes(c, str(who.actor)).values()]
    return min(ends) if ends else None


def install(app: Any, store: Any, auth: Any, mutate: Any) -> None:
    from fastapi import Request
    from pydantic import Field

    from .models import Contract

    class AskSnooze(Contract):
        id: str = Field(min_length=3, max_length=200)
        # An ISO-8601 date-time (or a date: the start of that day in the team's zone), to come, at most a year on.
        until: str = Field(min_length=10, max_length=40)

    class AskUnsnooze(Contract):
        id: str = Field(min_length=3, max_length=200)

    @app.post(
        "/api/v2/needs-you/snooze",
        **api_v2(
            "Needs you",
            "snoozeNeedsYouAsk",
            "Snooze one ask for yourself (`id`: its Needs-you id, `until`: a date-time to come, at most a year on): it leaves your Needs you, its count and Flow until then, or until it is asked again, and stays in everyone else's. The task and its bot are not changed or told. Only a person the ask waits on, or their own Assistant",
        ),
    )
    def needs_snooze(request: Request, body: AskSnooze) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Snooze one ask for yourself: it leaves your Needs you until `until`, and stays in everyone else's."""
        who = request.state.identity
        person(who)
        until = until_of(body.until)  # a bad date is refused before anything is read

        def run(c: Any) -> dict[str, Any]:
            return snooze(c, auth, who, body.id, until)

        return mutate(request, body, run)

    @app.post(
        "/api/v2/needs-you/unsnooze",
        **api_v2("Needs you", "unsnoozeNeedsYouAsk", "Bring an ask you snoozed (`id`) back into your Needs you now"),
    )
    def needs_unsnooze(request: Request, body: AskUnsnooze) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Bring an ask you snoozed back into your Needs you now."""
        who = request.state.identity
        person(who)

        def run(c: Any) -> dict[str, Any]:
            return unsnooze(c, who, body.id)

        return mutate(request, body, run)

    @app.get(
        "/api/v2/needs-you/snoozed",
        **api_v2(
            "Needs you",
            "getNeedsYouSnoozed",
            "The asks you snoozed for yourself that are still snoozed, as Needs you lists them, each with `snoozed_until`, and `next`, when the first comes back",
        ),
    )
    def needs_snoozed(request: Request) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """The asks you snoozed for yourself that are still snoozed, as Needs you lists them, each with `snoozed_until`,
        and when the first one comes back (`next`)."""
        who = request.state.identity
        person(who)
        with store.read_transaction() as c:
            items = _needs(c, auth, who, snoozed="only")
            return {"items": items, "count": len(items), "next": next_end(c, who)}
