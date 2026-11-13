# pyright: strict
"""Whose turn a task is (docs/pipelines-v3.md, "Whose turn it is: the ten states of an open task").

    waiting_on(conn, task_id) -> list[Wait]

One read. Every screen, every bot, the never-stuck check and Health call it; none of them works a task's state out for
itself. The list is in headline order, so the first entry is what a row shows and the task page lists the rest. It is
never empty for an open task, and empty for one that is Done or Closed. It reads tables only: no GitHub, no Jev, no
computer.

The ten states, in headline order:

    snoozed   someone set the task aside
    person    a requirement names them, a bot asked them, the task is theirs, or the ladder reached them
    paused    it is a bot's turn and the bot cannot run
    bot       a requirement is not met and a bot has it
    jev       Jev is checking a page
    github    checks are running, the PR is queued, or it merged and waits for a release
    tasks     tasks it filed are still open, a task it depends on ("Blocked by #304"), or one it merges after
    room      everything is met and the next stage is full
    pick      its stage picks from several bots and every one is at its limit (backend/stage_pick.py)
    by_hand   it sits in a stage that tasks leave by hand
"""

import json
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from itertools import chain
from typing import TYPE_CHECKING, Any, TypeVar

from . import hubdb_core, idea_checks, merge_order, pipeline_rights, pr_conflicts, read_cache, task_depends, task_wakes
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

SNOOZED, PERSON, PAUSED, BOT, JEV, SOURCE, TASKS, ROOM, PICK, BY_HAND = STATES = (
    "snoozed",
    "person",
    "paused",
    "bot",
    "jev",
    "source",
    "tasks",
    "room",
    "pick",  # a stage's "One of several" has nobody free yet: "Waiting for a free Reviewer" (backend/stage_pick.py)
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
    may not open that task is not told them (`unnamed`). `fix` is set on a Paused wait a person fixes before any bot
    can act (a requirement's `Standing.fix`, such as Connect GitHub): where they fix it. Nobody is woken for it. `href`
    is the page that shows a Paused bot's cause (task_wakes.cause_href: Settings > Computers for a bot with no computer,
    Billing when out of credit), for a link beside the line; it changes nothing about who is woken. `share` is who a
    private task waits on to be let in (the next stage's owner, or someone it asks, who can't see it): the person who
    has the task lets exactly them see it from their Needs you card (task #392; task_calls.task_let_see)."""

    state: str
    who: str | None = None
    line: str = ""
    requirement_id: str | None = None
    since: str | None = None
    until: str | None = None
    ask: bool = False
    about: str | None = None
    fix: str | None = None
    href: str | None = None
    share: str | None = None


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
        if said.state == TASKS and merge_order.is_order_wait(said.requirement_id):
            return replace(said, who=None, line=merge_order.UNNAMED, about=None)
        if said.state == TASKS and task_depends.is_wait(said.requirement_id):
            return replace(said, who=None, line=task_depends.UNNAMED, about=None)
        if said.state == TASKS:
            return replace(said, who=None, line=SUB_TASK, about=None)
        return replace(said, line=SUB_TASK_CLOSED, about=None)
    if said.state == R.WAITING and merge_order.is_order(said.requirement):
        return replace(said, on=None, line=merge_order.UNNAMED, about=None)
    if said.state == R.WAITING:
        return replace(said, on=None, line=SUB_TASK_OPEN, about=None)
    return replace(said, line=SUB_TASK_CLOSED, about=None)


def view(wait: Wait, shown: Callable[[str], bool]) -> Row:
    """A wait as JSON for a reader (`unnamed`), without `about`: which task a line came from is the server's. `href` only
    when the line has a page to fix it on."""
    out = asdict(unnamed(wait, shown))
    del out["about"], out["fix"]
    if out["href"] is None:
        del out["href"]
    if out["share"] is None:
        del out["share"]
    return out


def person_for(conn: "sqlite3.Connection", row: Row) -> str | None:
    """The person a task falls to when no bot can take it further: the one the ladder already chose, else the task's
    owner where that is a person, else the pipeline's owner or manager where that is a person, else the first person
    above its manager bot on the org chart (C16), else the company owner, else the person who has been here longest.
    Anyone on the way who left, was removed or cannot sign in is skipped, and on a private task anyone who cannot read
    it. A private task then falls to whoever asked for it, else a person on its reader list, a group's by its members
    now (`H.reader_actors`). None only when nobody can sign in, or nobody who can reads it. The never-stuck
    check hands a task to this same person (never_stuck._person).
    One person's Needs you reads only `candidates`: a new way this names a person must be added there."""
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
    the company has a person.
    One person's Needs you reads only `candidates`: a new way this names a person must be added there."""
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
        return Wait(
            PAUSED,
            who,
            task_wakes.cause_line(conn, who, cause),
            requirement_id,
            since,
            ask=ask,
            href=task_wakes.cause_href(cause),
        )
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
            and not R.outside(named)
            and standing.requirement["kind"] in ("ok", "answer", "page")
            and not H.task_private_readable(conn, named, row)
        ):
            return named
    return None


def _share_line(conn: "sqlite3.Connection", who: str) -> str:
    """Why a private task waits on its person, in their words (task #392): `who` (asked, or the next step's) can't see
    it. Their card's button lets `who` see it (`Wait.share`)."""
    from . import hubdb

    H: Any = hubdb
    return f"{H.actor_name(conn, who)} can't see this task"


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
    caller has them.
    One person's Needs you reads only `candidates`: a new way this names a person must be added there."""
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
    if not row.get("owner_id") and visit.get("pick_state") == "full":
        # Everyone the stage picks from is at their limit: the task waits for the first with room, and until then it
        # is nobody's turn (task_engine.pick_again, on the never-stuck pass).
        from . import stage_pick

        out.append(Wait(PICK, stage["id"], stage_pick.waiting_line(conn, stage), None, entered))
        return out
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

    def on_filed(requirement_id: str, since: str | None) -> bool:
        """Whether a requirement waits on the first open task the bot filed this visit (one line per such task)."""
        nonlocal filed
        if filed is None:
            filed = _filed_open(conn, visit["id"]) if theirs else []
        if not filed:
            return False
        first, title = filed[0]
        if first not in on_tasks:
            on_tasks.add(first)
            out.append(Wait(TASKS, first, title, requirement_id, since, about=first))
        return True

    # While it waits on a task it merges after, GitHub's "Not queued until #40 merges" is that same wait (merge_order).
    ordered = any(merge_order.is_order(s.requirement) for s in unmet)
    shut = unreadable(conn, row, unmet)  # asked, and may not open it
    for standing in unmet:
        requirement = standing.requirement
        # An ask's wait counts from when it was asked (a page's version, once Jev looked), else when the stage began.
        since = standing.since or (requirement["created_at"] if requirement.get("visit_id") else entered)
        line = standing.line or standing.name
        if requirement["kind"] == "ok" and requirement.get("visit_id") and _bound(conn, requirement["id"]):
            # A bound OK's line may name what it is for: what the task waits on goes to everyone who reads the task,
            # its lists and briefs, so it only says that it waits on a person (task_asks.PRIVATE_LINE).
            line = _private_line()
        if requirement["kind"] == pr_conflicts.KIND:
            # A PR that conflicts before Merge is the bot's that built it, whoever has the stage (pr_conflicts). While
            # the stage waits on a person's answer it is held for them: nobody's turn, and their ask says so (`needs`).
            if not pr_conflicts.held(standing):
                out.append(_turn(conn, row, standing.on or has, line, None, since, False, seen))
            continue
        if standing.state == R.WAITING and merge_order.is_order(requirement):
            # It goes after a task that has not merged: that task's, like a filed task; nobody is woken (merge_order).
            out.append(Wait(TASKS, standing.on, line, merge_order.wait_id(row["id"]), since, about=standing.about))
            continue
        if standing.state == R.WAITING and requirement["kind"] == "linked_done":
            if standing.on not in on_tasks:
                on_tasks.add(standing.on or "")
                out.append(Wait(TASKS, standing.on, line, requirement["id"], since, about=standing.about))
        elif standing.state == R.WAITING and standing.on == R.JEV:
            out.append(Wait(JEV, None, line, requirement["id"], since))
        elif standing.state == R.WAITING and R.outside(standing.on):
            if ordered and requirement.get("condition") == "pr_merged":
                continue
            out.append(Wait(SOURCE, standing.on, line, requirement["id"], since))  # who: the source, e.g. github
        elif standing.state == R.WAITING:
            # An OK or an answer waits on whoever it names: a person, or a bot that was named to approve. One that names
            # nobody, or a person who left (`person_of` skips them), is the task's person's.
            named = standing.on or person_for(conn, row)
            if not requirement.get("visit_id") and not is_bot(named) and on_filed(requirement["id"], since):
                # The stage's own OK or answer waits on a task its bot filed this visit (a review of what the person
                # is to OK): the person is asked once that has ended, not on what the review may still change.
                continue
            if shut and named and E.not_shared(conn, row, named):
                # Someone who may not open it: the task's person shares it first. Work of theirs, not an ask.
                line = _share_line(conn, named)
                out.append(Wait(PERSON, person_for(conn, row), line, requirement["id"], since, share=named))
                continue
            out.append(_turn(conn, row, named, line, requirement["id"], since, True, seen))
        elif by_hand or asked:
            continue  # in a by-hand stage nobody is chased for what is not met
        elif standing.fix and is_bot(has):
            # Nobody can meet it until a person fixes its cause (Connect GitHub): the bot is Paused on it, without a
            # turn, and the person who fixes it has one row for every task it holds up (`needs_by_person`).
            out.append(Wait(PAUSED, has, line, requirement["id"], since, fix=standing.fix))
        else:
            if on_filed(requirement["id"], since):
                continue
            # Not met: whoever has the task's work has this too.
            out.append(_turn(conn, row, has, line, requirement["id"], since, False, seen, standing.about))
    if by_hand and not unmet and E.not_held_by_hand(conn, row):
        # A person's request is not held by hand: it waits for room in the next stage (never_stuck._requests).
        ahead = E.next_stage(conn, row)
        name = ahead["name"] if ahead else "the next stage"
        full = bool(ahead) and not E.has_room(conn, ahead, task_row=row)
        line = f"No room in {name}" if full else f"Moving to {name}"
        out.append(Wait(ROOM, ahead["id"] if ahead else None, line, None, entered))
    elif by_hand:
        out.append(Wait(BY_HAND, stage["id"], f"Leaves {stage['name']} by hand", None, entered))
    elif not unmet and idea_checks.holds(conn, row):
        # An idea in Product's Ideas: Jev's duplicate check comes first, for two minutes at most (idea_checks).
        out.append(Wait(JEV, None, "Jev is checking it against open ideas", None, entered))
    elif not unmet:
        # Everything is met. It waits for a slot in the next stage; with room, the engine takes it on its next pass.
        ahead = E.next_stage(conn, row)
        full = bool(ahead) and not E.has_room(conn, ahead, task_row=row)
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
            line = f"Waits on {by}: {held['cause']}"
            shut = shut or str(held["who"])
        out.append(Wait(ROOM, ahead["id"] if ahead else None, line, None, entered, share=held["who"] if held else None))
    if visit.get("level") == "person" and not by_hand:
        # The ladder or Late brought the task to a person. It is in their list whatever else it waits on, and the
        # first thing it says is why.
        late = visit.get("late_person_at")
        line = f"Late in {stage['name']}" if late else f"Stuck in {stage['name']}"
        if shut:
            line = f"{line}: {_share_line(conn, shut)}"
        since = late or visit.get("last_change_at") or entered
        out.insert(0, Wait(PERSON, person_for(conn, row), line, None, since, share=shut))
    blockers = task_depends.blockers(conn, str(row["id"]))
    if blockers:
        # It depends on a task still open: that is all it waits on, nobody's turn, until the task ends (task_depends).
        # An OK or an answer asked of a person, and a cause a person fixes first, stay theirs, as under a bot's snooze.
        out = [wait for wait in out if wait.state == SNOOZED or (wait.state == PERSON and wait.ask) or wait.fix] + [
            Wait(
                TASKS,
                blocker,
                task_depends.line(conn, blocker),
                task_depends.wait_id(str(row["id"]), blocker),
                since,
                about=blocker,
            )
            for blocker, since in blockers
        ]
    # Two requirements missing for the same reason ("No PR linked" for PR open and Checks pass) are one line.
    once: dict[tuple[str, str | None, str], Wait] = {}
    for wait in out:
        once.setdefault((wait.state, wait.who, wait.line), wait)
    return sorted(once.values(), key=lambda wait: _ORDER[wait.state])


def turns(conn: "sqlite3.Connection", row: Row, waits: Sequence[Wait] | None = None) -> set[str]:
    """The bots whose turn it is on this task: each bot it waits on (Paused ones too: their wake waits until they can
    run), and the pipeline's manager bot while the ladder has the task at its level. Empty at level `person`: nothing
    runs until a person acts. A bot Paused on a cause a person fixes first (`Wait.fix`) has no turn: nothing it could
    do would help, so it is not woken, and it is woken once when the cause goes (never_stuck's ladder)."""
    visit: Row = row.get("visit") or {}
    level = visit.get("level") or "owner"
    if level == "person" or row["stage"]["kind"] != "work":
        return set()
    found = of(conn, row) if waits is None else waits
    out = {str(wait.who) for wait in found if wait.state in (BOT, PAUSED) and wait.who and not wait.fix}
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
    bots = sorted(
        (w for w in found if w.state in states and w.who and not w.fix), key=lambda w: (w.state != BOT, w.ask)
    )
    if not bots:
        return None
    pipe: Row = row.get("pipeline") or {}
    manager: str | None = pipe.get("manager_id")
    if level == "manager" and is_bot(manager):
        return str(manager)
    return bots[0].who


def on_person(
    conn: "sqlite3.Connection", row: Row, waits: Sequence[Wait], seen: dict[str, str | None] | None = None
) -> bool:
    """Whether the task waits only on a person's OK or answer, so it gives up its slot in a stage with a limit
    (docs/pipelines-v3.md, "Limits"): every wait on it is an ask of a person, and none is a bot's, Jev's, GitHub's or a
    sub-task's. A task the ladder brought to a person (level `person`) is stuck work, not review, and so is one whose
    owner bot cannot run (Paused): both keep their slot, or freeing it would hide them."""
    visit: Row = row.get("visit") or {}
    if (visit.get("level") or "owner") == "person" or row.get("snooze"):
        return False
    if not waits or any(wait.state != PERSON or not wait.ask for wait in waits):
        return False
    owner = row.get("owner_id")
    return not (is_bot(owner) and task_wakes.cannot_run(conn, owner, _seen(conn) if seen is None else seen))


def on_person_many(conn: "sqlite3.Connection", waits: Mapping[str, Sequence[Wait]]) -> set[str]:
    """`on_person` for each task of a list, from its waits (`waiting_on_many`): the ids of those that wait only on a
    person's OK or answer. One read of the tasks whose every wait is an ask of a person (their owner, open visit's level
    and open snooze, all `on_person` reads of a task), and "can this bot run" once per owner bot: never a read per row,
    with or without a snapshot's cache (list rows, task_reads.brief)."""
    asked = [task_id for task_id, found in waits.items() if found and all(w.state == PERSON and w.ask for w in found)]
    if not asked:
        return set()
    seen = _seen(conn)
    out: set[str] = set()
    for task_id, owner, level, snoozed in conn.execute(
        "SELECT t.id, t.owner_id, v.level, EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id = t.id "
        "AND s.ended_at IS NULL) FROM tasks t LEFT JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "WHERE t.id IN (SELECT value FROM json_each(?))",
        (json.dumps(asked),),
    ).fetchall():
        row: Row = {"owner_id": owner, "visit": {"level": level} if level else None, "snooze": snoozed or None}
        if on_person(conn, row, waits[str(task_id)], seen):
            out.add(str(task_id))
    return out


@dataclass(frozen=True)
class Slots:
    """A limited stage's tasks as its limit counts them: `held`, the open tasks that are not snoozed or blocked
    (task_depends) and hold a slot,
    and `on_person`, (task id, since) for each that waits only on a person's OK or answer (`on_person`), `since` being
    when the newest of its asks began."""

    held: int
    on_person: tuple[tuple[str, str], ...]


_IN_STAGE = (
    "SELECT t.id FROM tasks t WHERE t.pipeline_id IS NOT NULL AND t.stage_id=? AND t.status='open' "
    "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id = t.id AND s.ended_at IS NULL) "
    f"AND NOT {task_depends.BLOCKED % 't'}"
)


def slots(conn: "sqlite3.Connection", stage_id: str) -> Slots:
    """How a stage's open, unsnoozed tasks stand against its limit (`Slots`). One read of the tasks, then each one's
    requirements; a task with nothing missing holds its slot without reading its waits (it waits for room ahead, which
    would ask the next stage the same). Kept for the snapshot: a write discards it."""

    def build() -> Slots:
        ids = [str(found[0]) for found in conn.execute(_IN_STAGE, (stage_id,)).fetchall()]
        read = prime(conn, ids)
        seen = _seen(conn)
        held, person = 0, list[tuple[str, str]]()
        for task_id in ids:
            row = read[task_id] if task_id in read else E.task(conn, task_id)
            if not row:
                continue
            standings = R.standings(conn, row)
            waits = of(conn, row, seen, standings) if any(not s.met for s in standings) else []
            if waits and on_person(conn, row, waits, seen):
                # When its turn became a person's: the newest ask, else the visit's start.
                visit: dict[str, Any] = row.get("visit") or {}
                since = max(str(wait.since or "") for wait in waits) or str(visit.get("entered_at") or "")
                person.append((task_id, since))
            else:
                held += 1
        return Slots(held, tuple(person))

    return read_cache.value(conn, ("stage_slots", stage_id), build, shared=True)


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
    task_depends.prime(conn, ids)  # the tasks each depends on that are still open
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


def _visit_id(row: Row) -> str | None:
    visit: Row = row.get("visit") or {}
    return str(visit["id"]) if visit.get("id") else None


def ask_id(entry: Row) -> str:
    """The id of one ask in Needs you: its task's visit and the requirement. A stage's requirement is the same on every
    task in the stage, so the requirement alone names no one ask; `requirement_id` is still what answers it."""
    return f"{entry['visit_id']}:{entry['requirement_id']}"


# The open tasks Needs you reads: on the engine, open, and not set aside by a person (a bot's snooze hides no ask,
# `listed`). `%s` is what to select; the predicate is the partial index tasks_open_pipeline's, word for word.
_OPEN_SQL = (
    "SELECT %s FROM tasks t WHERE t.pipeline_id IS NOT NULL AND t.status='open' "
    "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id = t.id AND s.ended_at IS NULL "
    "AND s.by_id NOT LIKE 'bot:%%') ORDER BY t.id"
)
# What `candidates` reads of every open task when the person may be on its ladder (`_ladder_people`).
_OPEN_ROWS = (
    "t.id, t.owner_id, t.requester_id, t.private, t.readers, t.pipeline_id, t.stage_id, "
    "v.id AS visit_id, v.level, v.person_id"
)
_OPEN_JOIN = "tasks t LEFT JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL"
# Of the requirements of every stage and of every open visit, what says whether a task may fall to its ladder.
_STAGE_ASKS = (
    "SELECT stage_id, kind, person_id, condition FROM requirements WHERE stage_id IS NOT NULL AND withdrawn_at IS NULL"
)
_VISIT_ASKS = (
    "SELECT r.visit_id, r.kind, r.person_id, r.condition FROM visits v JOIN requirements r ON r.visit_id = v.id "
    "WHERE v.left_at IS NULL AND r.withdrawn_at IS NULL"
)
# GitHub conditions whose standing names the task's owner, GitHub or nobody's turn, never a person by the ladder.
_NAMED_CONDITIONS = ("pr_open", "checks_pass", "pr_merged")
_ASK_KINDS = ("ok", "answer", "page")


def _listed(values: Iterable[str]) -> tuple[str]:
    return (json.dumps(sorted({str(value) for value in values if value})),)


def _ladder_ask(conn: "sqlite3.Connection", requirement: Row, private: bool = False) -> bool:
    """Whether a requirement may wait on nobody it names, so the task's person (`person_for`) has it: an OK, a
    question or a page naming nobody, or a person who left; GitHub's release condition when only a person can act. On
    a private task, any ask: whoever it names may not read the task (`unreadable`)."""
    kind, named = requirement["kind"], requirement["person_id"]
    if kind in _ASK_KINDS:
        return private or not named or (is_human(named) and not R.active_person(conn, named))
    return kind == "item" and requirement["condition"] not in _NAMED_CONDITIONS


def _ladder_people(conn: "sqlite3.Connection", person: str) -> tuple[bool, set[str]]:
    """(whether `person` is the company's person, the pipelines whose ladder reaches them): the pipelines they own or
    manage, or whose manager bot they are the first person above. A task falls to someone else's ladder only through
    them, a private task's requester and readers aside."""
    company = company_person(conn) == person
    pipelines: set[str] = set()
    above: dict[str, str | None] = {}
    for found in conn.execute("SELECT id, owner_id, manager_id FROM pipelines").fetchall():
        pid, owner, manager = str(found[0]), found[1], found[2]
        if person in (owner, manager):
            pipelines.add(pid)
        elif is_bot(manager):
            if manager not in above:
                above[manager] = person_above(conn, manager)
            if above[manager] == person:
                pipelines.add(pid)
    return company, pipelines


# Every bot the company has, and every bot an open task (owner or assignee: a conflicting PR's builder), a stage, a
# pipeline or an ask names, as bot ids.
_NAMED_BOTS = (
    "SELECT id FROM bots UNION SELECT substr(owner_id, 5) FROM tasks WHERE owner_id LIKE 'bot:%' AND status='open' "
    "UNION SELECT substr(assignee_id, 5) FROM tasks WHERE assignee_id LIKE 'bot:%' AND status='open' "
    "UNION SELECT substr(person_id, 5) FROM requirements WHERE person_id LIKE 'bot:%' AND withdrawn_at IS NULL "
    "UNION SELECT substr(manager_id, 5) FROM pipelines WHERE manager_id LIKE 'bot:%' "
    "UNION SELECT substr(owner, 5) FROM stages WHERE owner LIKE 'bot:%' AND retired_at IS NULL "
    "UNION SELECT substr(notes, 5) FROM answers WHERE answer='give' AND notes LIKE 'bot:%' AND undone_at IS NULL"
)


def _blocked_bots(conn: "sqlite3.Connection", person: str, company: bool) -> set[str]:
    """The bots that cannot run whose fixer (`_fixer`) is `person`: the bots they own, and for the company's person
    every bot (a cause that stops them all, an owner who left), and every bot a task or a stage still names that has
    no `bots` row (task_wakes: "was removed", which only the company's person fixes)."""
    if company:
        bots = [str(found[0]) for found in conn.execute(_NAMED_BOTS).fetchall()]
    else:
        bots = [
            str(found[0])
            for found in conn.execute(
                "SELECT bot FROM bot_config WHERE owner_id IN (?, ?)", (person, actor_id(person))
            ).fetchall()
        ]
    actors = [f"bot:{bot}" for bot in bots]
    task_wakes.prime_bots(conn, actors)
    seen = _seen(conn)
    out: set[str] = set()
    for bot in actors:
        cause = task_wakes.cannot_run(conn, bot, seen)
        if cause and _fixer(conn, bot, cause) == person:
            out.add(bot)
    return out


def _held_tasks(conn: "sqlite3.Connection") -> set[str]:
    """The tasks one of whose bots is held on them (task_wakes.task_cause): a held run, runs that can't start, or a
    worktree wait. Few at any time."""
    from . import start_holds

    SH: Any = start_holds
    TW: Any = task_wakes  # its held-runs read for every task, as task_wakes.prime_holds reads it
    out = {str(found[0]) for found in conn.execute(TW._held_runs_sql(many=True)).fetchall()}
    out |= {str(found[0]) for found in conn.execute(SH.held_sql(many=True), (_core.KEEPER, SH.EVENT)).fetchall()}
    return out | set(task_wakes.worktree_holds(conn))


def candidates(conn: "sqlite3.Connection", person: str) -> list[str]:
    """The open tasks that can put something in `person`'s Needs you, in id order: every task `needs_by_person` would
    list for them, and others it reads to be sure. A task reaches a person in one of these ways (`of`,
    `needs_by_person`):

    - it is theirs (its owner), the ladder already brought it to them (its visit's person), or a requirement on its
      stage or visit names them, or was given to them;
    - a page on it that names nobody asks again whoever sent it back or whose OK lapsed (an answer of theirs);
    - a bot they fix cannot run (`_fixer`) and the task is that bot's turn: its owner, its pipeline's manager, a
      requirement naming or given to it, a page's reviewer, a conflicting PR's builder;
    - a bot of it is held on the task itself (task_wakes.task_cause);
    - nobody it names can act, so it falls to its person (`person_for`), and they are on its ladder: they own or manage
      its pipeline, are the first person above its manager bot, are the company's person, or ask for or read it;
    - for the company's person, a cause they fix first (`Wait.fix`, Connect GitHub).

    Most people own or manage no pipeline, so only their own rows are read, by index. Someone whose ladder reaches a
    pipeline reads a few columns of each of its open tasks, and works out in full only those that may fall to them."""
    company, pipelines = _ladder_people(conn, person)
    blocked = _blocked_bots(conn, person, company)
    named = {person, *blocked}
    found: set[str] = set(_held_tasks(conn))
    visits: set[str] = set()
    stages: set[str] = set()
    found |= {
        str(row[0])
        for row in conn.execute(
            "SELECT id FROM tasks WHERE owner_id IN (SELECT value FROM json_each(?)) AND status='open'", _listed(named)
        ).fetchall()
    }
    visits |= {
        str(row[0])
        for row in conn.execute("SELECT id FROM visits WHERE person_id=? AND left_at IS NULL", (person,)).fetchall()
    }
    for stage_id, visit_id in conn.execute(
        "SELECT stage_id, visit_id FROM requirements WHERE person_id IN (SELECT value FROM json_each(?)) "
        "AND withdrawn_at IS NULL",
        _listed(named),
    ).fetchall():
        if stage_id:
            stages.add(str(stage_id))
        if visit_id:
            visits.add(str(visit_id))
    visits |= {
        str(row[0])
        for row in conn.execute(
            "SELECT visit_id FROM answers WHERE answer='give' AND notes IN (SELECT value FROM json_each(?)) "
            "AND undone_at IS NULL",
            _listed(named),
        ).fetchall()
    }
    stage_asks = conn.execute(_STAGE_ASKS).fetchall()
    # A page that names nobody asks whoever sent it back, or whose OK lapsed: on any visit of the task.
    pages = {str(row[0]) for row in stage_asks if row[1] == "page" and not row[2]}
    paged = [
        str(row[0])
        for row in conn.execute(
            "SELECT v.task_id FROM visits v JOIN requirements r ON r.visit_id = v.id WHERE v.left_at IS NULL "
            "AND r.kind='page' AND r.person_id IS NULL AND r.withdrawn_at IS NULL "
            "UNION SELECT task_id FROM visits WHERE left_at IS NULL AND stage_id IN (SELECT value FROM json_each(?))",
            _listed(pages),
        ).fetchall()
    ]
    if paged:
        found |= {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT v.task_id FROM visits v JOIN answers a ON a.visit_id = v.id "
                "WHERE v.task_id IN (SELECT value FROM json_each(?)) AND a.by_id=?",
                (json.dumps(paged), person),
            ).fetchall()
        }
    if blocked:
        # A blocked bot may be asked as a page's reviewer (task_requirements.delta_reviewer) or as the builder of a PR
        # that conflicts (pr_conflicts.builder): every task with a page, and every task with a conflicting PR.
        stages |= {str(row[0]) for row in stage_asks if row[1] == "page"}
        found |= {
            str(row[0])
            for row in conn.execute(
                "SELECT v.task_id FROM visits v JOIN requirements r ON r.visit_id = v.id WHERE v.left_at IS NULL "
                "AND r.kind='page' AND r.withdrawn_at IS NULL"
            ).fetchall()
        }
        found |= set(paged)
        found |= {
            str(row[0])
            for row in conn.execute(
                "SELECT task_id FROM task_links WHERE kind='pr' AND state IN ('open', 'draft') AND mergeable='conflict'"
            ).fetchall()
        }
        managed = {
            str(row[0])
            for row in conn.execute(
                "SELECT id FROM pipelines WHERE manager_id IN (SELECT value FROM json_each(?))", _listed(blocked)
            ).fetchall()
        }
        if managed:
            found |= {
                str(row[0])
                for row in conn.execute(
                    "SELECT id FROM tasks WHERE pipeline_id IN (SELECT value FROM json_each(?)) AND status='open'",
                    _listed(managed),
                ).fetchall()
            }
    if company:
        from .item_sources import github as GS

        if GS.first_fix(conn):
            stages |= {str(row[0]) for row in stage_asks if row[1] == "item"}
            found |= {
                str(row[0])
                for row in conn.execute(
                    "SELECT v.task_id FROM visits v JOIN requirements r ON r.visit_id = v.id WHERE v.left_at IS NULL "
                    "AND r.kind='item' AND r.withdrawn_at IS NULL"
                ).fetchall()
            }
    if company or pipelines:
        found |= _ladder_tasks(conn, person, company, pipelines, stage_asks)
    else:
        found |= _private_ladder_tasks(conn, person, stage_asks)
    if stages:
        visits |= {
            str(row[0])
            for row in conn.execute(
                "SELECT id FROM visits WHERE stage_id IN (SELECT value FROM json_each(?)) AND left_at IS NULL",
                _listed(stages),
            ).fetchall()
        }
    if visits:
        found |= {
            str(row[0])
            for row in conn.execute(
                "SELECT task_id FROM visits WHERE id IN (SELECT value FROM json_each(?)) AND left_at IS NULL",
                _listed(visits),
            ).fetchall()
        }
    if not found:
        return []
    return [
        str(row[0])
        for row in conn.execute(
            _OPEN_SQL.replace("ORDER BY", "AND t.id IN (SELECT value FROM json_each(?)) ORDER BY") % "t.id",
            _listed(found),
        ).fetchall()
    ]


def _dicts(cursor: "sqlite3.Cursor") -> list[Row]:
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def _asks_by(rows: Iterable[Any]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for found in rows:
        out.setdefault(str(found[0]), []).append({"kind": found[1], "person_id": found[2], "condition": found[3]})
    return out


def _falls(conn: "sqlite3.Connection", row: Row, asks: dict[str, list[Row]], visit_asks: dict[str, list[Row]]) -> bool:
    """Whether an open task may fall to its person (`person_for`) in `of`: the ladder brought it to a person, or to
    its manager; nobody owns it, or its owner left (`holder`); or a requirement on it waits on nobody it names, or on
    someone who may not read this private task (`_ladder_ask`)."""
    owner = row["owner_id"]
    if row["level"] in ("person", "manager") or not owner:
        return True
    if is_human(owner) and not R.active_person(conn, owner):
        return True
    private = row["private"] is None or bool(row["private"])
    requirements = chain(asks.get(str(row["stage_id"]), ()), visit_asks.get(str(row["visit_id"]), ()))
    return any(_ladder_ask(conn, requirement, private) for requirement in requirements)


def _ladder_tasks(
    conn: "sqlite3.Connection", person: str, company: bool, pipelines: set[str], stage_asks: list[Any]
) -> set[str]:
    """The open tasks that may fall to `person` by the ladder (`_falls`), reading a few columns of each open task of
    the pipelines whose ladder reaches them (every pipeline, for the company's person), and of private tasks they ask
    for or read."""
    asks = _asks_by(stage_asks)
    visit_asks = _asks_by(conn.execute(_VISIT_ASKS).fetchall())
    out: set[str] = set()
    for row in _dicts(conn.execute(_OPEN_SQL.replace("FROM tasks t", "FROM " + _OPEN_JOIN) % _OPEN_ROWS)):
        if not (company or row["pipeline_id"] in pipelines or _reads(conn, person, row)):
            continue
        if _falls(conn, row, asks, visit_asks):
            out.add(str(row["id"]))
    return out


def _reads(conn: "sqlite3.Connection", person: str, row: Row) -> bool:
    """Whether a private task falls to `person` last on its ladder: they asked for it, or are on its reader list."""
    from . import hubdb

    H: Any = hubdb
    if not (row["private"] is None or row["private"]):
        return False
    return row["requester_id"] == person or person in H.reader_actors(conn, row)


def _private_ladder_tasks(conn: "sqlite3.Connection", person: str, stage_asks: list[Any]) -> set[str]:
    """`_ladder_tasks` for someone whose ladder reaches no pipeline: only private tasks they ask for or read."""
    rows = [
        row
        for row in _dicts(
            conn.execute(
                _OPEN_SQL.replace("FROM tasks t", "FROM " + _OPEN_JOIN).replace(
                    "WHERE ", "WHERE (t.private IS NOT 0) AND (t.requester_id=? OR t.readers IS NOT NULL) AND ", 1
                )
                % _OPEN_ROWS,
                (person,),
            )
        )
        if _reads(conn, person, row)
    ]
    if not rows:
        return set()
    asks = _asks_by(stage_asks)
    visit_asks = _asks_by(
        conn.execute(
            "SELECT visit_id, kind, person_id, condition FROM requirements WHERE withdrawn_at IS NULL "
            "AND visit_id IN (SELECT value FROM json_each(?))",
            _listed(str(row["visit_id"]) for row in rows),
        ).fetchall()
    )
    return {str(row["id"]) for row in rows if _falls(conn, row, asks, visit_asks)}


def needs(conn: "sqlite3.Connection", person: str) -> list[Row]:
    """Needs you, for one person, oldest first. A task whose work is theirs is listed once (`kind` "task"), however many
    requirements are missing. Each OK or answer that names them is its own entry (`kind` "ask"). A bot that cannot run
    is one entry per cause (`kind` "paused") for the person who can fix it, with the tasks it holds up, not one per
    task; a cause the company's Owner fixes first (Connect GitHub, `Wait.fix`) is theirs, with `href` to where. A read:
    nothing is stored, so nothing can fall out of step with the tasks. A task a person snoozed is set aside and is not
    listed. A bot's snooze hides no ask: each OK or answer still asked of a person stays in their list (Acme rc.5, Nov
    11: a manager bot's snooze took a people-only OK out of the person's list for two days); the rest of the task is
    set aside. An ask the stage's merge conflict waits on says so in one more sentence
    (pr_conflicts.held).

    Read from the tasks that can put something in this person's list (`candidates`), each worked out as
    `needs_by_person` works out every task, so the list is the one it gives this person, item for item."""
    if not is_human(person):
        return []
    return _lists(conn, candidates(conn, person), person).get(person, [])


def needs_by_person(conn: "sqlite3.Connection") -> dict[str, list[Row]]:
    """Every person's Needs you (`needs`), by person, from one pass over the open tasks: each task and its waits are
    read once, whoever they are for, where asking `needs` for each person reads every task again for each. A person
    with nothing to act on has no key. "Can this bot run" is kept for the snapshot (`_seen`)."""
    return _lists(conn, [str(found[0]) for found in conn.execute(_OPEN_SQL % "t.id").fetchall()])


class _Everyone:
    """`only` for `_lists` when every person's list is wanted."""

    def __contains__(self, person: object) -> bool:
        return True


def _lists(conn: "sqlite3.Connection", task_ids: list[str], *only_people: str) -> dict[str, list[Row]]:
    """Needs you from these open tasks (in id order), for every person, or only for `only_people`: each task's waits
    are worked out whoever they are for, and only the chosen people's entries are kept."""
    only: Any = set(only_people) if only_people else _Everyone()
    out: dict[str, list[Row]] = {}
    paused: dict[str, dict[str, Row]] = {}
    seen = _seen(conn)
    fixers: dict[str, str | None] = {}
    read = prime(conn, task_ids)
    for task_id in task_ids:
        row = read[str(task_id)] if str(task_id) in read else E.task(conn, task_id)
        if row is None:  # the query lists only tasks on the engine
            raise LookupError(f"task {task_id} is not on the engine")
        standings = R.standings(conn, row) if row.get("visit") else []
        waits = listed(of(conn, row, seen, standings), row)
        held = next((s for s in standings if pr_conflicts.held(s)), None)
        kinds = {str(s.requirement["id"]): str(s.requirement.get("kind")) for s in standings}
        people = sorted({str(wait.who) for wait in waits if wait.state == PERSON and wait.who and wait.who in only})
        for person in people:
            mine = [wait for wait in waits if wait.state == PERSON and wait.who == person]
            if any(wait.about for wait in mine):  # said to them: a sub-task they may not open is not named
                shown = _shown(conn, row, person)
                mine = [unnamed(wait, shown) for wait in mine]
            work = [wait for wait in mine if not wait.ask]
            for wait in [wait for wait in mine if wait.ask] + work[:1]:
                line = wait.line
                if held and wait.ask:
                    line = R.sentences(line, pr_conflicts.ask_line(conn, held, kinds.get(str(wait.requirement_id), "")))
                out.setdefault(person, []).append(
                    {
                        "kind": "ask" if wait.ask else "task",
                        "task_id": task_id,
                        "number": row.get("number"),
                        "title": row.get("title"),
                        "pipeline_id": row["pipeline_id"],
                        "stage_id": row["stage_id"],
                        "requirement_id": wait.requirement_id if wait.ask else None,
                        "visit_id": _visit_id(row) if wait.ask else None,
                        "line": line,
                        "missing": [each.line for each in (work if not wait.ask else [wait])],
                        "since": min((each.since or "" for each in work), default="") if not wait.ask else wait.since,
                    }
                )
        for wait in waits:
            if wait.state != PAUSED or not wait.who:
                continue
            if wait.line.endswith(task_wakes.BEING_SET_UP) and _botops_reviews(conn, wait.who):
                continue  # BotOps is setting it up, and its task starts when the bot is live: no person row
            if wait.line.endswith(task_wakes.RUN_HELD) and _botops_reviews(conn, wait.who):
                continue  # BotOps has the held run's review task (execution._hand_to_maintainer): no person row
            if wait.line.endswith(task_wakes.CANT_START):
                continue  # BotOps has the task to find why its runs can't start (start_holds.file): no person row
            if task_wakes.cannot_run(conn, wait.who, seen) == task_wakes.CLOUD_STARTING:
                continue  # its cloud machine is on its way, and its work starts once it is up: no person row
            if wait.fix:
                # A cause of the company's (Connect GitHub): its Owner fixes it, once for every task it holds up.
                fixer = company_person(conn)
            else:
                if wait.who not in fixers:
                    fixers[wait.who] = _fixer(conn, wait.who, seen.get(actor_id(wait.who)))
                fixer = fixers[wait.who]
            if not fixer or fixer not in only:
                continue
            fresh: Row = {"kind": "paused", "line": wait.line, "bots": [], "tasks": [], "since": wait.since or ""}
            if wait.fix or wait.href:
                fresh["href"] = wait.fix or wait.href
            entry = paused.setdefault(fixer, {}).setdefault(wait.line, fresh)
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


def listed(waits: list[Wait], row: Row) -> list[Wait]:
    """The waits of a task that go in Needs you. A task a bot snoozed lists only what is asked of a person, the OKs and
    answers and a cause they fix first (`Wait.fix`): a bot's snooze never hides an ask, and sets the rest aside. Any
    other task, all of them (a task a person snoozed is not listed)."""
    snooze: Row = row.get("snooze") or {}
    if not snooze:
        return waits
    if not is_bot(snooze.get("by_id")):
        return []
    return [wait for wait in waits if (wait.state == PERSON and wait.ask) or wait.fix]


def _botops_reviews(conn: "sqlite3.Connection", bot: str) -> bool:
    """Whether BotOps reviews this bot's held runs: it is in the company and the bot is not BotOps itself."""
    from . import hubdb

    maintainer = str(hubdb.FLEET_MAINTAINER)
    return actor_id(bot) != maintainer and bool(read_cache.one(conn, "SELECT 1 FROM bots WHERE id=?", (maintainer,)))


def _fixer(conn: "sqlite3.Connection", bot: str, cause: str | None) -> str | None:
    """The person who can get a bot running again: the company's owner for a cause that stops every bot, else the
    bot's owner while they are still here, else the company's owner. An owner who cannot act gives the person the
    company falls back on (`company_person`).
    One person's Needs you reads only `candidates`: a new way this names a person must be added there."""
    if cause not in task_wakes.COMPANY_WIDE:
        owner = _bot_owner(conn, bot)
        if owner:
            return owner
    return company_person(conn)
