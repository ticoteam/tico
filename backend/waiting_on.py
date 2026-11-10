# pyright: strict
"""Whose turn a task is (docs/pipelines-v3.md, "Whose turn it is: the nine states of an open task").

    waiting_on(conn, task_id) -> list[Wait]

One read. Every screen, every bot, the never-stuck check and Health call it; none of them works a task's state out for
itself. The list is in headline order, so the first entry is what a row shows and the task page lists the rest. It is
never empty for an open task, and empty for one that is Done or Closed. It reads tables only: no GitHub, no Jev, no
computer.

The nine states, in headline order:

    snoozed   someone set the task aside
    person    a requirement names them, a bot asked them, the task is theirs, or the ladder reached them
    paused    it is a bot's turn and the bot cannot run
    bot       a requirement is not met and a bot has it
    jev       Jev is checking a page
    github    checks are running, the PR is queued, or it merged and waits for a release
    tasks     tasks it filed are still open
    room      everything is met and the next stage is full
    by_hand   it sits in a stage that tasks leave by hand
"""

import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import chain
from typing import TYPE_CHECKING, Any, TypeVar

from . import hubdb_core, pipeline_rights, read_cache, task_wakes
from . import task_engine as E
from . import task_requirements as R

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
actor_id: Callable[[object], str] = _core.actor_id
human_actor: Callable[[object], str] = _core.human_actor
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

SNOOZED, PERSON, PAUSED, BOT, JEV, GITHUB, TASKS, ROOM, BY_HAND = STATES = (
    "snoozed",
    "person",
    "paused",
    "bot",
    "jev",
    "github",
    "tasks",
    "room",
    "by_hand",
)
_ORDER = {state: at for at, state in enumerate(STATES)}


@dataclass(frozen=True)
class Wait:
    """One thing a task waits on. `who` is an actor for `person`, `paused` and `bot`, a task id for `tasks`, the stage
    id for `room` and `by_hand`, whoever snoozed it for `snoozed`, and None for `jev` and `github`. `line` is one
    sentence. `until` is a snooze's end, where it has one. `ask` is set when a requirement names who it waits on (an
    OK or an answer); without it the wait is the task's own work, which whoever has the task has. `about` is the task
    it filed whose title (or why it was closed) the line says: it never leaves the server (`view`), and a reader who
    may not open that task is not told them (`unnamed`)."""

    state: str
    who: str | None = None
    line: str = ""
    requirement_id: str | None = None
    since: str | None = None
    until: str | None = None
    ask: bool = False
    about: str | None = None


# What a reader who may not open a task this one filed is told of it (`unnamed`).
SUB_TASK = "A sub-task"
SUB_TASK_OPEN = "A sub-task is not finished"
SUB_TASK_CLOSED = "A sub-task was closed"

Said = TypeVar("Said", Wait, R.Standing)


def unnamed(said: Said, shown: Callable[[str], bool]) -> Said:
    """A wait or a requirement's standing as said to a reader who may open only the tasks `shown` says yes to. Its
    line may come from a task this one filed (`about`: its title, or why it was closed). To anyone who may not open
    that task it says only that a sub-task is open or was closed: never its title, its id or the reason. The one rule
    for every read and every wake that says what a task waits on (docs/pipelines-v3.md, "Private tasks. Same
    engine. Who can see them doesn't change.")."""
    if not said.about or shown(said.about):
        return said
    if isinstance(said, Wait):
        if said.state == TASKS:
            return replace(said, who=None, line=SUB_TASK, about=None)
        return replace(said, line=SUB_TASK_CLOSED, about=None)
    if said.state == R.WAITING:
        return replace(said, on=None, line=SUB_TASK_OPEN, about=None)
    return replace(said, line=SUB_TASK_CLOSED, about=None)


def view(wait: Wait, shown: Callable[[str], bool]) -> Row:
    """A wait as JSON for a reader (`unnamed`), without `about`: which task a line came from is the server's."""
    out = asdict(unnamed(wait, shown))
    del out["about"]
    return out


def person_for(conn: "sqlite3.Connection", row: Row) -> str | None:
    """The person a task falls to when no bot can take it further: the one the ladder already chose, else the task's
    owner where that is a person, else the pipeline's owner or manager where that is a person, else the first person
    above its manager bot on the org chart (C16), else the company owner, else the person who has been here longest.
    Anyone on the way who left, was removed or cannot sign in is skipped, and on a private task anyone who cannot read
    it. A private task then falls to whoever asked for it, else a person on its reader list, a group's by its members
    now (`H.reader_actors`). None only when nobody can sign in, or nobody who can reads it. The never-stuck
    check hands a task to this same person (never_stuck._person)."""
    from . import hubdb

    H: Any = hubdb
    if not H.task_private(conn, row):
        return next(_people_for(conn, row), None)
    # A private task falls only to a person who may read it already: the engine never adds a reader. None when no
    # person on the way reads it (never_stuck: held, and counted in Health without naming it).
    found = (
        who for who in chain(_people_for(conn, row), _readers(conn, row)) if H.task_private_readable(conn, who, row)
    )
    return next(found, None)


def _readers(conn: "sqlite3.Connection", row: Row) -> Iterator[str]:
    """The people on a private task's reader list who can act, each group by its members now, in a stable order. Read
    only once the ladder's own people are passed over."""
    from . import hubdb

    H: Any = hubdb
    if "readers" not in row:
        found = conn.execute("SELECT readers FROM tasks WHERE id=?", (row["id"],)).fetchone()
        row = {**row, "readers": found[0] if found else None}
    for person in sorted(who for who in H.reader_actors(conn, row) if is_human(who)):
        if R.active_person(conn, person):
            yield str(person)


def _people_for(conn: "sqlite3.Connection", row: Row) -> Iterator[str]:
    """`person_for`'s people in order, each one that can act; read lazily, so the first one costs only its own read."""
    visit: Row = row.get("visit") or {}
    pipe: Row = row.get("pipeline") or {}
    for found in (visit.get("person_id"), row.get("owner_id"), pipe.get("owner_id"), pipe.get("manager_id")):
        if R.active_person(conn, found):
            yield str(found)
    # C16: the top of the ladder is the first person above the pipeline's manager bot on the org chart, now; with none,
    # the company's owner. Not the person whose computer a bot runs on (that person fixes a bot that cannot run).
    above = person_above(conn, pipe.get("manager_id"))
    if above:
        yield above
    fallback = company_person(conn)
    if fallback:
        yield fallback
    # Only a private task gets this far (person_for skips who cannot read it): whoever asked for it reads it.
    if R.active_person(conn, row.get("requester_id")):
        yield row["requester_id"]


def person_above(conn: "sqlite3.Connection", bot: object) -> str | None:
    """The first person above a bot on the current org chart (people.org_parent: its `reports_to`, else its team's
    group or lead, else the founder) who can act; one who cannot is passed over for the next one up. None for anything
    that is not a bot, or when nobody above it can act."""
    if not is_bot(bot):
        return None
    from . import hubdb, people, views

    # Untyped modules: their reads get their types here.
    H: Any = hubdb
    P: Any = people
    V: Any = views
    roster: Row = V.roster(conn, shared=True)
    entries: Row = V.entries(conn, shared=True)
    archived = {str(found["id"]) for found in H.bots(conn) if found.get("state") == "archived"}
    groups: Row = roster.get("org_groups") or {}
    node: str = P.org_parent("bot", actor_id(bot), roster, entries, archived, True)
    seen: set[str] = set()
    while node and node not in seen:
        seen.add(node)
        kind, _, ident = node.partition(":")
        if kind == "p":
            if R.active_person(conn, human_actor(ident)):
                return human_actor(ident)
            node = P.org_parent("person", ident, roster, entries, archived, True)
        elif kind == "g":
            group: Row = groups.get(ident) or {}
            node = f"p:{group['reports_to']}" if group.get("reports_to") else ""
        else:
            node = P.org_parent("bot", ident, roster, entries, archived, True)
    return None


def company_person(conn: "sqlite3.Connection") -> str | None:
    """The person the company falls back on: its owner while they can act, else the person who has been here longest
    (first on the roster, then anyone else in `humans` by id), so the choice is stable. None only when nobody can
    sign in."""
    owner = pipeline_rights.company_owner(conn)
    if R.active_person(conn, owner):
        return owner
    roster = [
        str(found[0])
        for found in conn.execute(
            "SELECT json_extract(p.value, '$.id') FROM registry_metadata m, "
            "json_each(CASE WHEN json_valid(m.value) THEN m.value ELSE '{}' END, '$.people') p "
            "WHERE m.key='people' AND json_extract(p.value, '$.id') IS NOT NULL ORDER BY CAST(p.key AS INTEGER)"
        ).fetchall()
    ]
    rest = [str(found[0]) for found in conn.execute("SELECT id FROM humans ORDER BY id").fetchall()]
    for person in [*roster, *(p for p in rest if p not in roster)]:
        if R.active_person(conn, human_actor(person)):
            return human_actor(person)
    return None


def _bot_owner(conn: "sqlite3.Connection", bot: object) -> str | None:
    """The person who owns a bot, as an actor, while they are still here. None for a bot with no owner, one whose owner
    left, or anything that is not a bot."""
    if not is_bot(bot):
        return None
    found = conn.execute("SELECT owner_id FROM bot_config WHERE bot=?", (actor_id(bot),)).fetchone()
    if not found or not found[0]:
        return None
    owner = str(found[0]) if is_human(found[0]) else human_actor(found[0])
    return owner if R.active_person(conn, owner) else None


def holder(conn: "sqlite3.Connection", row: Row) -> str | None:
    """Who has the task's own work now, as an actor: the owner, or whoever the ladder handed it to (the manager bot,
    then a person). A person's own task stays theirs whatever the ladder did. A task with no owner is its pipeline
    manager's to hand out. A person who left or was removed has nothing: it falls to `person_for`. Never None while
    the company has a person."""
    visit: Row = row.get("visit") or {}
    level = visit.get("level") or "owner"
    pipe: Row = row.get("pipeline") or {}
    manager = pipe.get("manager_id")
    if is_human(row.get("owner_id")):
        return row["owner_id"] if R.active_person(conn, row["owner_id"]) else person_for(conn, row)
    if level == "person":
        return person_for(conn, row)
    if level == "manager" or not row.get("owner_id"):
        return manager if is_bot(manager) or R.active_person(conn, manager) else person_for(conn, row)
    return row["owner_id"]


def _bound(conn: "sqlite3.Connection", requirement_id: str) -> bool:
    from . import task_asks

    return task_asks.bound(conn, requirement_id)


def _shown(conn: "sqlite3.Connection", row: Row, viewer: str) -> Callable[[str], bool]:
    """Whether `viewer` may open a task `row` filed (task_calls.hidden_filed), for `unnamed`."""
    from . import task_calls

    hidden = set(task_calls.hidden_filed(conn, row, viewer))
    return lambda task_id: task_id not in hidden


def _private_line() -> str:
    from . import task_asks

    return task_asks.PRIVATE_LINE


def _turn(
    conn: "sqlite3.Connection",
    row: Row,
    who: str | None,
    line: str,
    requirement_id: str | None = None,
    since: str | None = None,
    ask: bool = False,
    seen: dict[str, str | None] | None = None,
    about: str | None = None,
) -> Wait:
    """The wait for something `who` has to do: a person's turn, a bot's turn, or Paused when that bot cannot run. A
    Paused wait's line is the cause as a sentence: "Builder has no computer"."""
    if not who or is_human(who):
        return Wait(PERSON, who, line, requirement_id, since, ask=ask, about=about)
    cause = task_wakes.cannot_run(conn, who, seen) or task_wakes.task_cause(conn, row["id"], who)
    if cause:
        return Wait(PAUSED, who, task_wakes.cause_line(conn, who, cause), requirement_id, since, ask=ask)
    return Wait(BOT, who, line, requirement_id, since, ask=ask, about=about)


def unreadable(conn: "sqlite3.Connection", row: Row, standings: Iterable[R.Standing]) -> str | None:
    """Who an OK or an answer this private task waits on names, when they may not open it: a stage edited to ask them,
    or the task's readers narrowed since. Nobody is made a reader, and they are told nothing: the ask falls to the
    task's person (`person_for`), and the never-stuck check hands the task to that person at once."""
    from . import hubdb

    H: Any = hubdb
    if not H.task_private(conn, row):
        return None
    for standing in standings:
        named = standing.on if standing.state == R.WAITING and not standing.met else None
        if (
            named
            and named not in (R.JEV, R.GITHUB)
            and standing.requirement["kind"] in ("ok", "answer")
            and not H.task_private_readable(conn, named, row)
        ):
            return named
    return None


def _share_line(conn: "sqlite3.Connection", who: str, verb: str = "Add") -> str:
    """What the person who has a private task does for someone it asks who may not open it (`unreadable`)."""
    from . import hubdb

    H: Any = hubdb
    return f"{verb} {H.actor_name(conn, who)} under Who can see"


_FILED_OPEN_SQL = (
    "SELECT l.visit_id, t.id, t.title FROM links l JOIN tasks t ON t.id = l.to_task_id "
    "WHERE l.visit_id IN (%s) AND t.status='open' ORDER BY l.created_at, l.id"
)


def _filed_open_of(conn: "sqlite3.Connection", visit_ids: list[str]) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {visit_id: [] for visit_id in visit_ids}
    if visit_ids:  # one statement however many (the ids go in as one JSON list)
        listed = (json.dumps(visit_ids),)
        for found in conn.execute(_FILED_OPEN_SQL % "SELECT value FROM json_each(?)", listed).fetchall():
            out[str(found[0])].append((str(found[1]), str(found[2] or "")))
    return out


def _filed_open(conn: "sqlite3.Connection", visit_id: str) -> list[tuple[str, str]]:
    """(id, title) of the tasks filed during a visit that are still open, oldest first."""
    return read_cache.value(conn, ("filed_open", visit_id), lambda: _filed_open_of(conn, [visit_id])[visit_id])


def _seen(conn: "sqlite3.Connection") -> dict[str, str | None]:
    """ "Can this bot run" for every read in this snapshot: a page asks it of the same bots from several lists. A write
    discards it (read_cache)."""
    if read_cache.cache(conn) is None:
        return {}
    return read_cache.value(conn, ("bots_can_run",), dict[str, str | None], shared=True)


def of(
    conn: "sqlite3.Connection",
    row: Row,
    seen: dict[str, str | None] | None = None,
    standings: Sequence[R.Standing] | None = None,
) -> list[Wait]:
    """`waiting_on` for a task already read with `task_engine.task()`. `seen` keeps "can this bot run" for one pass
    over many tasks; without it, the snapshot keeps them (`_seen`). `standings` saves reading them again when the
    caller has them."""
    seen = _seen(conn) if seen is None else seen
    stage = row["stage"]
    if stage["kind"] != "work":
        return []
    visit = row["visit"]
    entered = visit["entered_at"]
    out: list[Wait] = []
    snooze = row.get("snooze")
    if snooze:
        out.append(
            Wait(SNOOZED, snooze["by_id"], snooze.get("why") or "", None, snooze["created_at"], snooze["until_at"])
        )
    by_hand = bool(stage.get("by_hand"))
    unmet = [s for s in (R.standings(conn, row) if standings is None else standings) if not s.met]
    # A bot that has the task may have left it waiting on something it started: a question or an OK it added, or a
    # task it filed. Until that ends, the rest of what is missing is nobody's turn. A person's own task stays theirs.
    has = holder(conn, row) if unmet and not by_hand else None
    theirs = is_bot(has)
    asked = theirs and any(
        s.state == R.WAITING and s.requirement.get("visit_id") and s.requirement.get("added_by_id") == has
        for s in unmet
    )
    filed: list[tuple[str, str]] | None = None
    on_tasks: set[str] = set()
    shut = unreadable(conn, row, unmet)  # asked, and may not open it
    for standing in unmet:
        requirement = standing.requirement
        since = requirement["created_at"] if requirement.get("visit_id") else entered
        line = standing.line or standing.name
        if requirement["kind"] == "ok" and requirement.get("visit_id") and _bound(conn, requirement["id"]):
            # A bound OK's line may name what it is for: what the task waits on goes to everyone who reads the task,
            # its lists and briefs, so it only says that it waits on a person (task_asks.PRIVATE_LINE).
            line = _private_line()
        if standing.state == R.WAITING and requirement["kind"] == "linked_done":
            if standing.on not in on_tasks:
                on_tasks.add(standing.on or "")
                out.append(Wait(TASKS, standing.on, line, requirement["id"], since, about=standing.about))
        elif standing.state == R.WAITING and standing.on in (R.JEV, R.GITHUB):
            out.append(Wait(JEV if standing.on == R.JEV else GITHUB, None, line, requirement["id"], since))
        elif standing.state == R.WAITING:
            # An OK or an answer waits on whoever it names: a person, or a bot that was named to approve. One that names
            # nobody, or a person who left (`person_of` skips them), is the task's person's.
            named = standing.on or person_for(conn, row)
            if shut and named and E.not_shared(conn, row, named):
                # Someone who may not open it: the task's person shares it first. Work of theirs, not an ask.
                out.append(Wait(PERSON, person_for(conn, row), _share_line(conn, named), requirement["id"], since))
                continue
            out.append(_turn(conn, row, named, line, requirement["id"], since, True, seen))
        elif by_hand or asked:
            continue  # in a by-hand stage nobody is chased for what is not met
        else:
            if filed is None:
                filed = _filed_open(conn, visit["id"]) if theirs else []
            if filed:
                first, title = filed[0]
                if first not in on_tasks:
                    on_tasks.add(first)
                    out.append(Wait(TASKS, first, title, requirement["id"], since, about=first))
                continue
            # Not met: whoever has the task's work has this too.
            out.append(_turn(conn, row, has, line, requirement["id"], since, False, seen, standing.about))
    if by_hand:
        out.append(Wait(BY_HAND, stage["id"], f"Leaves {stage['name']} by hand", None, entered))
    elif not unmet:
        # Everything is met. It waits for a slot in the next stage; with room, the engine takes it on its next pass.
        ahead = E.next_stage(conn, row)
        full = bool(ahead) and not E.has_room(conn, ahead)
        name = ahead["name"] if ahead else "the next stage"
        line = f"No room in {name}" if full else f"Moving to {name}"
        held = E.shut_out(conn, row, ahead) if ahead and not full else None
        if held:
            # The next stage's owner, or someone it asks, cannot read this private task: it waits until someone who
            # can see it shares it (the engine never adds a reader). Who that is, and why.
            from . import hubdb

            H: Any = hubdb
            sharer = person_for(conn, row)
            by = str(H.actor_name(conn, sharer)) if sharer else "someone who can see it"
            line = f"Waits on {by} to share it: {held['cause']}"
        out.append(Wait(ROOM, ahead["id"] if ahead else None, line, None, entered))
    if visit.get("level") == "person" and not by_hand:
        # The ladder or Late brought the task to a person. It is in their list whatever else it waits on, and the
        # first thing it says is why.
        late = visit.get("late_person_at")
        line = f"Late in {stage['name']}" if late else f"Stuck in {stage['name']}"
        if shut:
            line = f"{line}: {_share_line(conn, shut, 'add')}"
        since = late or visit.get("last_change_at") or entered
        out.insert(0, Wait(PERSON, person_for(conn, row), line, None, since))
    return sorted(out, key=lambda wait: _ORDER[wait.state])


def turns(conn: "sqlite3.Connection", row: Row, waits: Sequence[Wait] | None = None) -> set[str]:
    """The bots whose turn it is on this task: each bot it waits on (Paused ones too: their wake waits until they can
    run), and the pipeline's manager bot while the ladder has the task at its level. Empty at level `person`: nothing
    runs until a person acts."""
    visit: Row = row.get("visit") or {}
    level = visit.get("level") or "owner"
    if level == "person" or row["stage"]["kind"] != "work":
        return set()
    found = of(conn, row) if waits is None else waits
    out = {str(wait.who) for wait in found if wait.state in (BOT, PAUSED) and wait.who}
    pipe: Row = row.get("pipeline") or {}
    manager: str | None = pipe.get("manager_id")
    if level == "manager" and is_bot(manager) and out:
        out.add(str(manager))
    return out


def turn(
    conn: "sqlite3.Connection", row: Row, waits: Sequence[Wait] | None = None, *, paused: bool = False
) -> str | None:
    """The one bot to wake about this task now, as the never-stuck ladder picks it: the bot with the task's own work,
    before a bot that was only asked for an OK or an answer; the manager bot at level `manager`; nobody at level
    `person`. With `paused`, a bot that cannot run yet is picked when no other can: its wake waits for it."""
    visit: Row = row.get("visit") or {}
    level = visit.get("level") or "owner"
    if level == "person" or row["stage"]["kind"] != "work":
        return None
    found = of(conn, row) if waits is None else waits
    states = (BOT, PAUSED) if paused else (BOT,)
    bots = sorted((w for w in found if w.state in states and w.who), key=lambda w: (w.state != BOT, w.ask))
    if not bots:
        return None
    pipe: Row = row.get("pipeline") or {}
    manager: str | None = pipe.get("manager_id")
    if level == "manager" and is_bot(manager):
        return str(manager)
    return bots[0].who


def waiting_on(conn: "sqlite3.Connection", task_id: str) -> list[Wait]:
    """What the task waits on, headline first. Never empty for an open task; empty for a Done or Closed one, and for a
    task that is not on the engine."""
    row = E.task(conn, task_id)
    return of(conn, row) if row else []


# task_engine.task()'s reads, word for word, so `prime` can seed them.
_TASK_READS = (
    ("SELECT * FROM tasks WHERE id=?", "SELECT * FROM tasks WHERE id IN (%s)", "id"),
    (
        "SELECT * FROM visits WHERE task_id=? AND left_at IS NULL",
        "SELECT * FROM visits WHERE task_id IN (%s) AND left_at IS NULL ORDER BY rowid",
        "task_id",
    ),
    (
        "SELECT * FROM snoozes WHERE task_id=? AND ended_at IS NULL",
        "SELECT * FROM snoozes WHERE task_id IN (%s) AND ended_at IS NULL ORDER BY rowid",
        "task_id",
    ),
)


def prime(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> dict[str, Row]:
    """Read many tasks as `task_engine.task()` and `task_requirements.standings` read each one, a few statements in
    all, for the reads of each task that follow in this snapshot: a list of waits costs what one task's does. Gives
    the tasks it read, by id (none where nothing is kept for the snapshot)."""
    import sqlite3

    values = read_cache.cache(conn)
    if values is None or conn.row_factory is not sqlite3.Row:
        return {}
    ids = sorted({str(task_id) for task_id in task_ids if task_id})
    for one_sql, many_sql, column in _TASK_READS:
        unread = [task_id for task_id in ids if ("one", one_sql, (task_id,)) not in values]
        if unread:  # one statement however many (the ids go in as one JSON list)
            found: dict[str, Any] = {}
            listed = (json.dumps(unread),)
            for row in conn.execute(many_sql % "SELECT value FROM json_each(?)", listed).fetchall():
                found.setdefault(str(row[column]), row)
            read_cache.prime(conn, one_sql, {(task_id,): found.get(task_id) for task_id in unread})
    rows = [row for row in (E.task(conn, task_id) for task_id in ids) if row]
    R.prime(conn, rows)
    task_wakes.prime_holds(conn, ids)  # each task's worktree wait and held run: a bot's turn may be Paused on it
    # "Can this bot run" for each bot whose turn a task may be: its owner, or its pipeline's manager.
    bots: list[str] = []
    for row in rows:
        pipeline: dict[str, Any] = row.get("pipeline") or {}
        bots += [str(a) for a in (row.get("owner_id"), pipeline.get("manager_id")) if a]
    task_wakes.prime_bots(conn, bots)
    visits = sorted({str(row["visit"]["id"]) for row in rows if row.get("visit")})
    unread = [visit_id for visit_id in visits if ("filed_open", visit_id) not in values]
    for visit_id, filed in _filed_open_of(conn, unread).items():
        values.setdefault(("filed_open", visit_id), filed)
    return {str(row["id"]): row for row in rows}


def waiting_on_many(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> dict[str, list[Wait]]:
    """`waiting_on` for each task of a list, by task id."""
    ids = list(task_ids)
    read = prime(conn, ids)
    seen = _seen(conn)  # "can this bot run", once for the whole list
    out: dict[str, list[Wait]] = {}
    for task_id in ids:
        row = read[task_id] if task_id in read else E.task(conn, task_id)
        out[task_id] = of(conn, row, seen) if row else []
    return out


def headline(conn: "sqlite3.Connection", task_id: str) -> Wait | None:
    """The first wait: what a row shows. None for an ended task."""
    waits = waiting_on(conn, task_id)
    return waits[0] if waits else None


def all_met(waits: list[Wait]) -> bool:
    """Whether a task's waits say it is ready to move on: nothing is missing and it only waits for a slot."""
    return bool(waits) and all(wait.state == ROOM for wait in waits)


def needs(conn: "sqlite3.Connection", person: str) -> list[Row]:
    """Needs you, for one person, oldest first. A task whose work is theirs is listed once (`kind` "task"), however many
    requirements are missing. Each OK or answer that names them is its own entry (`kind` "ask"). A bot that cannot run
    is one entry per cause (`kind` "paused") for the person who can fix it, with the tasks it holds up, not one per
    task. A read: nothing is stored, so nothing can fall out of step with the tasks. A snoozed task is set aside and
    is not listed."""
    return needs_by_person(conn).get(person, [])


def needs_by_person(conn: "sqlite3.Connection") -> dict[str, list[Row]]:
    """Every person's Needs you (`needs`), by person, from one pass over the open tasks: each task and its waits are
    read once, whoever they are for, where asking `needs` for each person reads every task again for each. A person
    with nothing to act on has no key. "Can this bot run" is kept for the snapshot (`_seen`)."""
    out: dict[str, list[Row]] = {}
    paused: dict[str, dict[str, Row]] = {}
    seen = _seen(conn)
    fixers: dict[str, str | None] = {}
    rows = conn.execute(
        "SELECT t.id FROM tasks t WHERE t.pipeline_id IS NOT NULL AND t.status='open' "
        "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id = t.id AND s.ended_at IS NULL) ORDER BY t.id"
    ).fetchall()
    read = prime(conn, [str(found[0]) for found in rows])
    for (task_id,) in rows:
        row = read[str(task_id)] if str(task_id) in read else E.task(conn, task_id)
        if row is None:  # the query lists only tasks on the engine
            raise LookupError(f"task {task_id} is not on the engine")
        waits = of(conn, row, seen)
        people = sorted({str(wait.who) for wait in waits if wait.state == PERSON and wait.who})
        for person in people:
            mine = [wait for wait in waits if wait.state == PERSON and wait.who == person]
            if any(wait.about for wait in mine):  # said to them: a sub-task they may not open is not named
                shown = _shown(conn, row, person)
                mine = [unnamed(wait, shown) for wait in mine]
            work = [wait for wait in mine if not wait.ask]
            for wait in [wait for wait in mine if wait.ask] + work[:1]:
                out.setdefault(person, []).append(
                    {
                        "kind": "ask" if wait.ask else "task",
                        "task_id": task_id,
                        "number": row.get("number"),
                        "title": row.get("title"),
                        "pipeline_id": row["pipeline_id"],
                        "stage_id": row["stage_id"],
                        "requirement_id": wait.requirement_id if wait.ask else None,
                        "line": wait.line,
                        "missing": [each.line for each in (work if not wait.ask else [wait])],
                        "since": min((each.since or "" for each in work), default="") if not wait.ask else wait.since,
                    }
                )
        for wait in waits:
            if wait.state != PAUSED or not wait.who:
                continue
            if wait.line.endswith(task_wakes.RUN_HELD) and _botops_reviews(conn, wait.who):
                continue  # BotOps has the held run's review task (execution._hand_to_maintainer): no person row
            if wait.who not in fixers:
                fixers[wait.who] = _fixer(conn, wait.who, seen.get(actor_id(wait.who)))
            fixer = fixers[wait.who]
            if not fixer:
                continue
            entry = paused.setdefault(fixer, {}).setdefault(
                wait.line,
                {"kind": "paused", "line": wait.line, "bots": [], "tasks": [], "since": wait.since or ""},
            )
            if wait.who not in entry["bots"]:
                entry["bots"].append(wait.who)
            if task_id not in entry["tasks"]:
                entry["tasks"].append(task_id)
            entry["since"] = min(entry["since"], wait.since or "")
    for fixer, entries in paused.items():
        out.setdefault(fixer, []).extend(entries.values())
    return {
        person: sorted(items, key=lambda item: (item["since"] or "", item.get("task_id") or "", item["line"]))
        for person, items in out.items()
    }


def _botops_reviews(conn: "sqlite3.Connection", bot: str) -> bool:
    """Whether BotOps reviews this bot's held runs: it is in the company and the bot is not BotOps itself."""
    from . import hubdb

    maintainer = str(hubdb.FLEET_MAINTAINER)
    return actor_id(bot) != maintainer and bool(read_cache.one(conn, "SELECT 1 FROM bots WHERE id=?", (maintainer,)))


def _fixer(conn: "sqlite3.Connection", bot: str, cause: str | None) -> str | None:
    """The person who can get a bot running again: the company's owner for a cause that stops every bot, else the
    bot's owner while they are still here, else the company's owner. An owner who cannot act gives the person the
    company falls back on (`company_person`)."""
    if cause not in task_wakes.COMPANY_WIDE:
        owner = _bot_owner(conn, bot)
        if owner:
            return owner
    return company_person(conn)
