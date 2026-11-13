# pyright: strict
"""A stage picks its owner from several bots: "One of several" (spec: "A stage picks its owner from several").

A stage whose owner is `pick` names the bots to pick from in `stages.pick`, and Tico picks one the moment a task
enters the stage (a hand-off, a task a bot files into it, a kickback), with no model call:

1. It had someone here before (a holder of an earlier visit to this stage) who is still on the list: that one, even
   over its limit. Where the pick becomes the task's owner, a task that has an owner keeps it (the engine never asks).
2. Prefer narrows the list: Anyone, New to the task, or A different model family from everyone who worked on it. If
   that would leave nobody free, it is skipped, and the line says so.
3. Take turns or Random among those not paused, who can read the task, and under their Limit each.
4. Everyone is full: the task waits ("Waiting for a free Reviewer") and nobody is woken; the never-stuck pass gives
   the oldest waiting task to the first bot with room (task_engine.pick_again).
5. Nobody can take it (all paused, or none can read a private task): the manager is woken, told why.

`owns_task` says whether the pick becomes the task's owner for life (Build: its assignee, so Fixes and kickbacks go back
to the same Builder) or holds only this stage (Review: `visits.holder_id`). Every pick is written on the task's
history: "Build given to Builder 2 · took turns".
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

PICK = "pick"  # a stage's owner when it is "One of several"
TURNS, RANDOM = "turns", "random"
HOWS = (TURNS, RANDOM)
ANYONE, NEW, FAMILY = "anyone", "new", "family"
PREFERS = (ANYONE, NEW, FAMILY)
FULL, NOBODY = "full", "nobody"  # visits.pick_state

_HOW_LINE = {TURNS: "took turns", RANDOM: "picked at random"}
_PREFER_LINE = {NEW: "new to this task", FAMILY: "a different model family"}
_SKIPPED_LINE = {NEW: "nobody new to this task was free", FAMILY: "nobody of a different model family was free"}
_FAMILY_NAMES = {"anthropic": "Claude", "openai": "GPT", "google": "Gemini", "xai": "Grok", "mistral": "Mistral"}

# Random picks; tests seed it.
chance = random.Random()


def _h() -> Any:
    from . import hubdb

    return hubdb


@dataclass(frozen=True)
class Pick:
    """A stage's "One of several"."""

    bots: tuple[str, ...]
    how: str = TURNS
    limit_each: int | None = None
    prefer: str = ANYONE
    owns_task: bool = False


@dataclass(frozen=True)
class Choice:
    """What a pick came to: the bot and why (`line`'s reason), or no bot and the visit's `state` (FULL or NOBODY)."""

    bot: str | None
    why: str
    state: str | None = None
    back: bool = False  # it had it here before


def _decoded(value: object) -> Row | None:
    if isinstance(value, dict):
        return dict(value)  # type: ignore[arg-type]
    if isinstance(value, str) and value.strip():
        try:
            found: object = json.loads(value)
        except ValueError:
            return None
        return dict(found) if isinstance(found, dict) else None  # type: ignore[arg-type]
    return None


def config(stage: Row | None) -> Pick | None:
    """The stage's pick, or None when its owner is not "One of several"."""
    if not stage or stage.get("kind", "work") != "work" or stage.get("owner") != PICK:
        return None
    found = _decoded(stage.get("pick"))
    if not found:
        return None
    raw_bots: list[Any] = list(found.get("bots") or [])
    bots = tuple(dict.fromkeys(str(bot) for bot in raw_bots if bot))
    if not bots:
        return None
    limit = found.get("limit_each")
    return Pick(
        bots=bots,
        how=str(found.get("how") or TURNS) if found.get("how") in HOWS else TURNS,
        limit_each=int(limit) if isinstance(limit, int) and limit > 0 else None,
        prefer=str(found.get("prefer")) if found.get("prefer") in PREFERS else ANYONE,
        owns_task=bool(found.get("owns_task")),
    )


def stored(pick: Pick) -> str:
    """`stages.pick` for a pick."""
    return json.dumps(view(pick), sort_keys=True)


def view(pick: Pick) -> Row:
    return {
        "bots": list(pick.bots),
        "how": pick.how,
        "limit_each": pick.limit_each,
        "prefer": pick.prefer,
        "owns_task": pick.owns_task,
    }


def normalize(value: object, label: str, problems: list[str]) -> Row | None:
    """A pick as a pipeline record gives it, checked: the bots (ids as `bot:<id>`; that they exist is the caller's),
    Take turns or Random, Limit each, Prefer and whether it becomes the task's owner. None, with a problem added, when
    it is not one."""
    found = _decoded(value)
    if found is None:
        problems.append(f"Say which bots {label} picks from.")
        return None
    raw: list[Any] = found.get("bots") if isinstance(found.get("bots"), list) else []  # type: ignore[assignment]
    bots = list(dict.fromkeys(str(bot).strip() for bot in raw if str(bot or "").strip()))
    if not bots:
        problems.append(f"Name at least one bot for {label} to pick from.")
    how = found.get("how") or TURNS
    if how not in HOWS:
        problems.append(f"{label} picks by taking turns or at random, not {how}.")
    prefer = found.get("prefer") or ANYONE
    if prefer not in PREFERS:
        problems.append(f"{label} prefers anyone, new to the task or a different model family, not {prefer}.")
    limit = found.get("limit_each")
    if limit in ("", 0):
        limit = None
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        problems.append(f"Limit each on {label} is a whole number of tasks, 1 or more, or none.")
        limit = None
    owns = found.get("owns_task", False)
    if not isinstance(owns, bool):
        problems.append(f"Whether the pick on {label} becomes the task's owner is yes or no.")
        owns = False
    return {"bots": bots, "how": how, "limit_each": limit, "prefer": prefer, "owns_task": owns}


# ----------------------------------------------------------------------------- reads
def family(conn: "sqlite3.Connection", bot: str) -> str:
    """The model maker a bot runs on (`anthropic`, `openai`, ...), from its configured model in Tico's catalog; "" when
    Tico cannot name it (a model the catalog does not list, or a reseller that picks the model). Unknown is never
    different from anything."""
    from . import bot_settings as _bot_settings, providers as _providers, shared_bots as _shared_bots
    from .usage_records import RESELLERS

    bot_settings: Any = _bot_settings
    providers: Any = _providers
    shared_bots: Any = _shared_bots
    slug = str(_h().actor_id(bot))
    config_row: Row = shared_bots.follow(conn, slug, bot_settings.read(conn, slug) or {})
    runtime, model = providers.bot_choice(conn, None, config_row)
    model = model or config_row.get("model") or ""
    runtime = runtime or config_row.get("runtime") or ""
    listed: Row = providers.MODEL_BY_ID.get(model) or {}
    maker = listed.get("provider") if model else providers.runtime_provider(runtime)
    return "" if not maker or maker in RESELLERS else str(maker)


def family_name(maker: str) -> str:
    """How a person reads a model family: Claude, GPT, ...; "" when unknown."""
    return _FAMILY_NAMES.get(maker, maker.capitalize() if maker else "")


def role(conn: "sqlite3.Connection", bots: tuple[str, ...] | list[str]) -> str:
    """What the bots on a list are, for "Waiting for a free …": their shared name without its number (Reviewer 1 and
    Reviewer 2 are a Reviewer), else "bot"."""
    H = _h()
    names = {re.sub(r"\s*\d+$", "", str(H.actor_name(conn, bot))).strip() for bot in bots}
    return names.pop() if len(names) == 1 and "" not in names else "bot"


def waiting_line(conn: "sqlite3.Connection", stage: Row) -> str:
    pick = config(stage)
    return f"Waiting for a free {role(conn, pick.bots if pick else ())}"


def held(conn: "sqlite3.Connection", stage_id: str, bot: str) -> int:
    """How many open tasks the bot has in the stage now: what its Limit each counts. A snoozed one is set aside."""
    found = conn.execute(
        "SELECT COUNT(*) FROM tasks t WHERE t.stage_id=? AND t.owner_id=? AND t.status='open' "
        "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL)",
        (stage_id, bot),
    ).fetchone()
    return int(found[0]) if found else 0


def had_here(conn: "sqlite3.Connection", task_id: str, stage_id: str) -> str | None:
    """Who a pick gave the task the last time it was in this stage."""
    found = conn.execute(
        "SELECT holder_id FROM visits WHERE task_id=? AND stage_id=? AND holder_id IS NOT NULL "
        "ORDER BY entered_at DESC, rowid DESC LIMIT 1",
        (task_id, stage_id),
    ).fetchone()
    return str(found[0]) if found else None


def worked_on(conn: "sqlite3.Connection", row: Row) -> set[str]:
    """Every bot that worked on the task: its assignee, each owner it had in any stage, each holder a pick gave it, and
    the owners of the tasks it filed."""
    out: set[str] = {str(a) for a in (row.get("assignee_id"), row.get("owner_id")) if a}
    task_id = str(row["id"])
    for (owner,) in conn.execute(
        "SELECT json_extract(data, '$.owner') FROM history_events WHERE subject_type='task' AND subject_id=? "
        "AND kind='move' AND data IS NOT NULL AND json_valid(data) "
        "UNION SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='reassign' "
        "UNION SELECT holder_id FROM visits WHERE task_id=? "
        "UNION SELECT t.owner_id FROM links l JOIN tasks t ON t.id = l.to_task_id WHERE l.from_task_id=?",
        (task_id, task_id, task_id, task_id),
    ).fetchall():
        if owner:
            out.add(str(owner))
    return {who for who in out if _h().is_bot(who)}


def _last_turn(conn: "sqlite3.Connection", stage_id: str) -> str | None:
    found = conn.execute("SELECT bot_id FROM stage_pick_turns WHERE stage_id=?", (stage_id,)).fetchone()
    return str(found[0]) if found else None


def _next_in_turn(bots: tuple[str, ...], last: str | None, free: list[str]) -> str:
    start = bots.index(last) + 1 if last in bots else 0
    for at in range(len(bots)):
        bot = bots[(start + at) % len(bots)]
        if bot in free:
            return bot
    return free[0]


@dataclass(frozen=True)
class _Screen:
    can: list[str]  # not paused and may read the task
    free: list[str]  # of those, under their limit
    paused: list[str]
    unreadable: list[str]
    refused: list[str]  # a Block rule of the stage refuses it as an owner: each rule's name, once


def refused_by(conn: "sqlite3.Connection", row: Row | None, stage: Row, bot: str) -> str | None:
    """The name of the stage's Block rule that refuses `bot` as the task's owner as it enters (stage_rules.owner_check,
    the one place that rule is enforced: a picked bot is held to it as a named one is), or None. With no task (the
    next-task sentence) no rule is read: a rule's condition may be about the task."""
    if row is None:
        return None
    from . import stage_rules as _stage_rules

    stage_rules: Any = _stage_rules
    found = stage_rules.owner_check(conn, row, stage, bot, "enters")
    return str(found[0]["name"]) if found else None


def _screen(conn: "sqlite3.Connection", row: Row | None, stage: Row, pick: Pick) -> _Screen:
    from . import task_wakes as _task_wakes

    task_wakes: Any = _task_wakes
    H = _h()
    stage_id = str(stage["id"])
    seen: dict[str, str | None] = {}
    can: list[str] = []
    paused: list[str] = []
    unreadable: list[str] = []
    refused: list[str] = []
    private = bool(row) and bool(H.task_private(conn, row))
    for bot in pick.bots:
        if task_wakes.cannot_run(conn, bot, seen):
            paused.append(bot)
        elif private and not H.task_private_readable(conn, bot, row):
            unreadable.append(bot)
        elif rule := refused_by(conn, row, stage, bot):
            if rule not in refused:
                refused.append(rule)
        else:
            can.append(bot)
    free = [bot for bot in can if pick.limit_each is None or held(conn, stage_id, bot) < pick.limit_each]
    return _Screen(can, free, paused, unreadable, refused)


def _nobody_why(screen: _Screen) -> str:
    causes = [bool(screen.paused), bool(screen.unreadable), bool(screen.refused)]
    if causes == [True, False, False]:
        return "every bot it picks from is paused"
    if causes == [False, True, False]:
        return "none of the bots it picks from can see this private task"
    if causes == [False, False, True]:
        return f"rule {' and '.join(screen.refused)} refuses every bot it picks from"
    return "each bot it picks from is paused, cannot see this private task or is refused by a stage rule"


def _preferred(conn: "sqlite3.Connection", row: Row | None, prefer: str, free: list[str]) -> list[str]:
    if prefer == ANYONE or not row:
        return free
    worked = worked_on(conn, row)
    if prefer == NEW:
        return [bot for bot in free if bot not in worked]
    families = {family(conn, bot) for bot in worked} - {""}
    out: list[str] = []
    for bot in free:
        mine = family(conn, bot)
        if mine and mine not in families:
            out.append(bot)
    return out


def choose(conn: "sqlite3.Connection", row: Row | None, stage: Row, pick: Pick, *, take: bool = True) -> Choice:
    """Who the task gets in `stage`, as the steps in this module's docstring go. `row` is the task (None for the next
    new task, `next_line`). With `take`, a pick by turns is remembered for the next one."""
    stage_id = str(stage["id"])
    if row is not None and not pick.owns_task:
        before = had_here(conn, str(row["id"]), stage_id)
        # A rule added since it was picked holds for a returning bot too: refused, it goes to another.
        if before and before in pick.bots and not refused_by(conn, row, stage, before):
            return Choice(before, "had it here before", back=True)
    screen = _screen(conn, row, stage, pick)
    if not screen.can:
        return Choice(None, _nobody_why(screen), NOBODY)
    if not screen.free:
        return Choice(None, f"Waiting for a free {role(conn, pick.bots)}", FULL)
    free = screen.free
    reasons: list[str] = []
    if pick.prefer != ANYONE and row is not None:
        narrowed = _preferred(conn, row, pick.prefer, free)
        if narrowed:
            free = narrowed
            reasons.append(_PREFER_LINE[pick.prefer])
        else:
            reasons.append(_SKIPPED_LINE[pick.prefer])
    if pick.how == RANDOM:
        bot = chance.choice(sorted(free))
    else:
        bot = _next_in_turn(pick.bots, _last_turn(conn, stage_id), free)
    if not reasons or reasons[0] not in _PREFER_LINE.values():
        reasons.insert(0, _HOW_LINE[pick.how])
    if take:
        conn.execute(
            "INSERT INTO stage_pick_turns (stage_id, bot_id, at) VALUES (?,?,?) "
            "ON CONFLICT(stage_id) DO UPDATE SET bot_id=excluded.bot_id, at=excluded.at",
            (stage_id, bot, _h().now()),
        )
    return Choice(bot, ", ".join(reasons))


def line(conn: "sqlite3.Connection", stage: Row, choice: Choice) -> str:
    """The history line of a pick: "Build given to Builder 2 · took turns", "Review 1 back to Reviewer 2 · had it here
    before", or why nobody was."""
    H = _h()
    if choice.bot:
        verb = "back to" if choice.back else "given to"
        return f"{stage['name']} {verb} {H.actor_name(conn, choice.bot)} · {choice.why}"
    if choice.state == FULL:
        return f"{stage['name']}: {choice.why}"
    return f"{stage['name']}: nobody picked, {choice.why}"


def next_line(conn: "sqlite3.Connection", stage: Row) -> str:
    """Who gets the next new task, in one sentence, so a person can check the setting reads right. It is `choose` for
    a task with no history, and remembers nothing."""
    pick = config(stage)
    if not pick:
        return ""
    H = _h()
    choice = choose(conn, None, stage, pick, take=False)
    if choice.state == NOBODY:
        return f"The next task wakes the manager to pick someone: {choice.why}."
    if choice.state == FULL:
        return f"The next task waits for a free {role(conn, pick.bots)}: each has {pick.limit_each} already."
    name = H.actor_name(conn, choice.bot)
    if pick.how == RANDOM:
        names = [str(H.actor_name(conn, bot)) for bot in pick.bots]
        said = f"The next task goes to {name}"
        if len(names) > 1:
            said = f"The next task goes to one of {', '.join(names[:-1])} or {names[-1]} at random"
    else:
        last = _last_turn(conn, str(stage["id"]))
        after = f", after {H.actor_name(conn, last)} had the last one" if last and last in pick.bots else ""
        said = f"The next task goes to {name}{after}"
    if pick.prefer == NEW:
        said += f", or to one new to the task if {name} worked on it"
    elif pick.prefer == FAMILY:
        said += ", or to one of a different model family from everyone who worked on it"
    return said + "."


def first_picks(conn: "sqlite3.Connection", pipeline_id: str | None) -> bool:
    """Whether a pipeline's first stage picks its owner from several: a task filed into it may name no owner, and the
    stage picks one as it arrives (a bot filing into Build). Naming an owner always wins."""
    from . import task_engine

    first = next((s for s in task_engine.stages(conn, str(pipeline_id or "general")) if s["kind"] == "work"), None)
    return config(first) is not None


def no_owner_refusal(conn: "sqlite3.Connection", pipeline_id: str | None) -> str:
    """Why a task with no owner is refused in a pipeline, or "" when its first stage picks one."""
    if first_picks(conn, pipeline_id):
        return ""
    return (
        "Say who does it (owner): only a pipeline whose first stage picks from several bots takes a task with no "
        "owner, and the first stage of " + str(pipeline_id or "general") + " does not"
    )
