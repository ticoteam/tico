# pyright: strict
"""A task that depends on another one ("Blocked by #304"): it cannot go on until that task ends.

The dependency is a relation of kind `depends_on` on the waiting task (task_relations: `from_task` depends on
`to_task`), written with `tico task relate <id> depends_on <task>`, the MCP tool or the task page, all through
`task_relations.relate`; the other task reads it as "Blocks #307". Any pipeline, any stage, several at once, and no end
date. It is a named wait, not a state:

- `blockers`: the tasks it depends on that are still open. While there is one, `waiting_on.of` says only "Blocked by
  #304 Growth synced sources part 1, in Build" (and any OK or answer still asked of a person): nobody's turn, so no bot is
  woken and the never-stuck check passes it by. It holds no slot where a stage has a limit, is never Late (the time
  it is blocked does not count, never_stuck._spent), does not move on by itself (task_engine.advance), and leaves its
  owner's Needs you. A reader who may not open the blocker reads "Blocked by a task you can't open".
- `release`: when a task ends, every dependency on it comes off (a dependency holds until its task first ends, so a
  reopened task blocks nothing again), and each task that depended on it and has no other open dependency is read
  again: it moves on if everything its stage asks is met (task_engine.advance), else its owner is woken once, "#304 is
  done" or "#304 was closed without doing it" so they re-plan. A deleted task does the same, "#304 was deleted"
  (`deleted`). No poll and no date. Every dependency written or taken off leaves a history row on the waiting task,
  from which `spans` gives the time it was blocked.
- `check`: the writer must be able to change the waiting task and read the other one (task_relations.relate); a loop,
  or a dependency of or on a task that has ended, is refused.

Until released (`until` `released` on the relation, `tico task relate <id> depends_on <task> --until released`): the
dependency holds past the other task's Done, until production runs a release that holds that task's merged PRs. This is
how a Validate task waits for its work to reach production, with no snooze. It reads "Blocked by #304 ..., done, until
production runs its release". `in_production` asks it of the releases Tico has saved (the release that holds each merge
commit, item_sources.github.release_of, as stage_inputs reads it) and the release this server runs (releases.version,
in the repository of its release manifest), which `on_release` records at every start-up and checks then. While such a
dependency waits on a Done task, the background re-read of releases keeps reading that task's (`held_blockers`, in the
repo its pipeline's In a release reads) and checks again when it finds one holding a merge (`released_now`): the moment
the running release holds them all, the dependency comes off and the owner is woken once, "#304 is in production". No
new poll. When the other task is Done and production already runs its release (or nothing of it merged into the repository
production runs), it comes off at once, as a plain dependency does; closed without doing it, likewise. A dependency on
a Done task is refused only when there is nothing left to wait for: production runs its release already, or none of
its merges is in the repository production's release comes from (another product's work, whose production version
Tico does not see: its owner snoozes to the date they will look again).

Snooze stays for "look again on a date". A PR, main or a release is reached through the task that carries it.
"""

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, cast

from . import read_cache

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger(__name__)

RELATION = "depends_on"
KIND = "depends_on"  # the prefix of the wait's requirement id; never a stored requirement
LINE = "Blocked by {ref} {title}, in {stage}"
UNNAMED = "Blocked by a task you can't open"
DONE = "{ref} is done"
CLOSED = "{ref} was closed without doing it"
DELETED = "{ref} was deleted"
WAKE = "unblocked"
UNTIL = "released"  # task_relations.until: held past the other task's end, until production runs its release
RELEASED_LINE = "Blocked by {ref} {title}, done, until production runs its release"
IN_PRODUCTION = "{ref} is in production: it runs {version}"
# A held dependency whose task merged nothing into the repository production runs (the server first ran with no release
# manifest, or a Release stage's release repo changed): there is no release to wait for, and it is not "in production".
NOTHING_HERE = (
    "{ref} is done, and it merged nothing into {repo}, whose release this server runs: no release to wait for"
)
# registry_metadata: {"version", "repo"} of the release this server runs, as `on_release` saw it at start-up.
RUNNING = "depends-release-running"

# A task, aliased by `%s`, with a dependency still open: for filters beside the open snooze's (hubdb.SNOOZED). Open: its
# task is open, or Done and the dependency holds until production runs its release.
BLOCKED = (
    "EXISTS (SELECT 1 FROM task_relations dep JOIN tasks blocker ON blocker.id = dep.to_task "
    "WHERE dep.from_task = %s.id AND dep.kind = 'depends_on' "
    "AND (blocker.status = 'open' OR blocker.status = 'done' AND dep.until = 'released'))"
)

_OPEN = (
    "SELECT d.from_task, d.to_task, d.created_at FROM task_relations d JOIN tasks b ON b.id = d.to_task "
    "WHERE d.from_task IN (SELECT value FROM json_each(?)) AND d.kind = 'depends_on' "
    "AND (b.status = 'open' OR b.status = 'done' AND d.until = 'released') "
    "ORDER BY d.created_at, d.rowid"
)


def _engine() -> Any:
    from . import task_engine

    return task_engine


def ref(row: Row | None) -> str:
    """How a line names a task: `#304`, or its id's first eight characters when it has no number."""
    if not row:
        return "a task"
    number = row.get("number")
    return f"#{number}" if number is not None else str(row["id"])[:8]


def _open_of(conn: "sqlite3.Connection", task_ids: list[str]) -> dict[str, list[tuple[str, str]]]:
    import json

    out: dict[str, list[tuple[str, str]]] = {task_id: [] for task_id in task_ids}
    if task_ids:  # one statement however many
        for found in conn.execute(_OPEN, (json.dumps(task_ids),)).fetchall():
            out[str(found[0])].append((str(found[1]), str(found[2])))
    return out


def blockers(conn: "sqlite3.Connection", task_id: str) -> list[tuple[str, str]]:
    """(task id, since) of each task this one depends on that is still open, oldest dependency first. Kept for the
    snapshot (`prime` reads a list's together)."""
    return read_cache.value(conn, (KIND, task_id), lambda: _open_of(conn, [task_id])[task_id], shared=True)


def blockers_of(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> dict[str, list[tuple[str, str]]]:
    """`blockers` of many tasks in one statement, with or without the snapshot's cache (a list read outside a
    transaction keeps nothing between reads)."""
    return _open_of(conn, sorted({str(t) for t in task_ids if t}))


def prime(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> None:
    """Read the open dependencies of many tasks in one statement, for the `blockers` reads that follow."""
    values = read_cache.cache(conn)
    if values is None:
        return
    unread = sorted({str(t) for t in task_ids if t and (KIND, str(t)) not in values})
    for task_id, found in _open_of(conn, unread).items():
        values[(KIND, task_id)] = found


def blocked(conn: "sqlite3.Connection", task_id: str) -> bool:
    return bool(blockers(conn, task_id))


def wait_id(task_id: str, blocker_id: str) -> str:
    return f"{KIND}:{task_id}:{blocker_id}"


def is_wait(requirement_id: object) -> bool:
    """Whether a wait (waiting_on.Wait.requirement_id) is a dependency's."""
    return str(requirement_id or "").startswith(KIND + ":")


_NAMED = (
    "SELECT t.id, t.number, t.title, s.name, t.status FROM tasks t LEFT JOIN stages s ON s.id = t.stage_id WHERE t.id=?"
)


def line(conn: "sqlite3.Connection", blocker_id: str) -> str:
    """ "Blocked by #304 Growth synced sources part 1, in Build", or once it is Done and the dependency holds until its
    release, "Blocked by #304 Growth synced sources part 1, done, until production runs its release"."""
    found = read_cache.one(conn, _NAMED, (blocker_id,))
    if not found:
        return UNNAMED
    if found[4] != "open":
        return RELEASED_LINE.format(ref=ref({"id": found[0], "number": found[1]}), title=str(found[2] or "").strip())
    said = LINE.format(ref=ref({"id": found[0], "number": found[1]}), title=str(found[2] or "").strip(), stage="")
    return said + str(found[3]) if found[3] else said.removesuffix(", in ")


_BLOCKING = (
    "SELECT d.to_task, d.from_task, w.number, d.created_at FROM task_relations d JOIN tasks w ON w.id = d.from_task "
    "WHERE d.to_task IN (SELECT value FROM json_each(?)) AND d.kind = 'depends_on' AND w.status = 'open' "
    "ORDER BY d.created_at, d.rowid"
)


def blocking(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> dict[str, list[tuple[str, object, str]]]:
    """{task id: [(dependent id, its number, since)]}: the open tasks each of these blocks ("Blocks #307, #308"), oldest
    dependency first, in one statement however many."""
    import json

    ids = sorted({str(t) for t in task_ids if t})
    out: dict[str, list[tuple[str, object, str]]] = {task_id: [] for task_id in ids}
    if ids:
        for found in conn.execute(_BLOCKING, (json.dumps(ids),)):
            out[str(found[0])].append((str(found[1]), found[2], str(found[3])))
    return out


def blocks_line(named: list[str], hidden: int = 0) -> str:
    """ "Blocks #307, #308", "Blocks #307 and 1 more", or "Blocks 2 tasks" when none may be named."""
    if not named:
        return f"Blocks {hidden} task{'' if hidden == 1 else 's'}"
    return f"Blocks {', '.join(named)}" + (f" and {hidden} more" if hidden else "")


def dependents(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    """The tasks that depend on this one."""
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT from_task FROM task_relations WHERE to_task=? AND kind=? ORDER BY created_at, rowid",
            (task_id, RELATION),
        )
    ]


def _depends_on(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT to_task FROM task_relations WHERE from_task=? AND kind=? ORDER BY created_at, rowid",
            (task_id, RELATION),
        )
    ]


def spans(conn: "sqlite3.Connection", task_id: str, since: str) -> list[tuple[str, str | None]]:
    """(from, to) of each time the task was blocked, from `since` on, for Late (never_stuck._spent): from when a
    dependency was written to when it came off (its task ended, it was taken off, or its task was deleted), None while
    it is on. Read from the task's history, where every write and removal of a dependency leaves a row, so a span
    outlives its relation row; a relation row with no history (a trash restore) counts from its own date."""
    opened: dict[str, str] = {}
    out: list[tuple[str, str | None]] = []
    for old, new, at in conn.execute(
        "SELECT old, new, created_at FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? "
        "ORDER BY created_at, rowid",
        (task_id, RELATION),
    ).fetchall():
        if new:
            opened.setdefault(str(new), str(at))
        elif old and str(old) in opened:
            out.append((opened.pop(str(old)), str(at)))
    for blocker, created in conn.execute(
        "SELECT to_task, created_at FROM task_relations WHERE from_task=? AND kind=?", (task_id, RELATION)
    ).fetchall():
        out.append((opened.pop(str(blocker), str(created)), None))
    out += [(at, None) for at in opened.values()]
    return [(max(start, since), end) for start, end in out if end is None or end > since]


# ----------------------------------------------------------------------------- writes
def check(conn: "sqlite3.Connection", actor: str, later: Row, blocker: Row, until: str | None = None) -> None:
    """Refuse a dependency that cannot hold: of a task that has ended, on one that has ended, or a loop (#304 already
    depends on #307, however far round). Until released, a Done task may be depended on while production does not run
    its release yet. The writer's rights are checked by the caller (task_relations.relate)."""
    from . import hubdb

    H: Any = hubdb
    if until == UNTIL and blocker["status"] == "done":
        shipped = in_production(conn, str(blocker["id"]))
        if shipped == "":
            where = running(conn)[1] or "the repository"
            H.refuse(
                conn,
                actor,
                "task_ended",
                f"{ref(blocker)} merged nothing into {where}, whose release this server runs: until released cannot "
                "wait for it. Another product's release is not seen here: snooze to the date you will look again.",
            )
        if shipped is not None:
            H.refuse(
                conn, actor, "task_ended", f"{ref(blocker)} is in production already: there is nothing to wait for"
            )
        blocker = {**blocker, "status": "open"}
    for row, what in ((later, "it"), (blocker, ref(blocker))):
        if row["status"] != "open":
            who = "The task" if what == "it" else what
            H.refuse(conn, actor, "task_ended", f"{who} has ended: there is nothing to wait for")
    path = _path(conn, str(blocker["id"]), str(later["id"]))
    if path:
        E = _engine()
        names = " → ".join(ref(E.task(conn, tid) or {"id": tid}) for tid in [str(later["id"]), *path])
        H.refuse(
            conn,
            actor,
            "kind",
            f"That would make a loop ({names}): {ref(blocker)} already depends on {ref(later)}.",
        )


def _path(conn: "sqlite3.Connection", start: str, goal: str) -> list[str]:
    """The tasks from `start` to `goal` along "depends on", both ends included, or [] when there is no such path."""
    back: dict[str, str | None] = {start: None}
    frontier = [start]
    while frontier:
        nxt: list[str] = []
        for tid in frontier:
            for other in _depends_on(conn, tid):
                if other in back:
                    continue
                back[other] = tid
                if other == goal:
                    out = [goal]
                    cursor = back[goal]
                    while cursor is not None:
                        out.append(cursor)
                        cursor = back[cursor]
                    return list(reversed(out))
                nxt.append(other)
        frontier = nxt
    return []


def release(conn: "sqlite3.Connection", ended_id: str, by: str) -> int:
    """`ended_id` ended: each dependency on it comes off, with a history row on both tasks, so a dependency holds only
    until its task first ends (reopening that task blocks nothing again). A dependency until released on a Done task
    stays while production does not run its release (`in_production`; `on_release` takes it off then). Then each task
    that depended on it and has no open dependency left moves on, or its owner is woken once (`release_one`). Returns
    how many were released."""
    found: Any = conn.execute("SELECT number, status FROM tasks WHERE id=?", (ended_id,)).fetchone()
    done = bool(found and found[1] == "done")
    named = ref({"id": ended_id, "number": found[0] if found else None})
    said = (DONE if done else CLOSED).format(ref=named)
    waiting = dependents(conn, ended_id)
    held: set[str] = set(_until_released(conn, ended_id)) if done else set()
    shipped = in_production(conn, ended_id) if held else None
    if shipped is None:  # they stay blocked until production runs its release (on_release)
        waiting = [task_id for task_id in waiting if task_id not in held]
    _off(conn, waiting, ended_id, by, "it ended")
    live = IN_PRODUCTION.format(ref=named, version=shipped) if shipped else said
    return sum(release_one(conn, task_id, ended_id, live if task_id in held else said) for task_id in waiting)


def _off(conn: "sqlite3.Connection", waiting: list[str], ended_id: str, by: str, note: str) -> None:
    from . import task_relations

    TR: Any = task_relations  # untyped module
    for task_id in waiting:
        conn.execute(
            "DELETE FROM task_relations WHERE from_task=? AND to_task=? AND kind=?", (task_id, ended_id, RELATION)
        )
        TR._history(conn, by, task_id, ended_id, RELATION, False, note)


def _until_released(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT from_task FROM task_relations WHERE to_task=? AND kind=? AND until=? ORDER BY created_at, rowid",
            (task_id, RELATION, UNTIL),
        )
    ]


# ----------------------------------------------------------------------------- until released
def running(conn: "sqlite3.Connection") -> tuple[str, str]:
    """(version, repository) of the release this server runs, as the last start-up saw it ("", "" before any)."""
    import json

    found = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (RUNNING,)).fetchone()
    try:
        value: Any = json.loads(found[0]) if found else {}
    except ValueError:
        value = {}
    if not isinstance(value, dict):
        return "", ""
    saved: dict[str, Any] = cast("dict[str, Any]", value)
    return str(saved.get("version") or ""), str(saved.get("repo") or "")


def in_production(conn: "sqlite3.Connection", task_id: str) -> str | None:
    """Whether production runs a release that holds every merged PR of the task: the version it runs when it does, ""
    when nothing of the task merged into the repository production runs (nothing to wait for), None while a merge is in
    no release yet or in one newer than the running release. Each merge is looked for in the repo its pipeline's In a
    release reads (item_sources.github.release_reads: the `release_repo` setting, else the PR's own). A release holds a
    merge when its saved history holds the merge commit (item_sources.github.release_of, pre-releases counted, as
    stage_inputs reads it); it runs in production when its tag is the running version or older (releases.parse: rc.10
    after rc.9)."""
    from . import releases
    from .item_sources import github as G

    GS: Any = G
    RL: Any = releases  # untyped module
    version, repo = running(conn)
    have = RL.parse(version)
    held: list[str] = []
    for wait in GS.release_reads(conn, task_id):
        if repo and str(wait["repo"]).lower() != repo.lower():
            continue  # another product's code: this server's release never holds it
        release: Row | None = GS.release_of(conn, str(wait["repo"]), str(wait["sha"]), prereleases=True)
        tag = RL.parse(release["tag"]) if release else None
        if not (have and tag and tag <= have):
            return None
        held.append(str(release["tag"]) if release else "")
    return version if held else ""


def on_release(conn: "sqlite3.Connection", version: str, repo: str) -> list[str]:
    """Start-up: record the release this server runs (`version`, of `repo`, from releases.version and the release
    manifest), then take off each dependency until released whose task is Done and whose merged PRs that release holds
    (`in_production`), and release each task that waited on it (`release_one`: it moves on, or its owner is woken once,
    "#304 is in production: it runs 0.6.0-rc.10"). Every start-up checks, and so does the background re-read when it
    finds a release holding a merge of the Done task (`released_now`); a dependency that came off is not there to wake
    anyone twice. Returns the tasks released."""
    import json

    conn.execute(
        "INSERT OR REPLACE INTO registry_metadata VALUES(?,?)",
        (RUNNING, json.dumps({"version": version, "repo": repo})),
    )
    return _ship(conn, None, version)


def held_blockers(conn: "sqlite3.Connection") -> list[str]:
    """The Done tasks an open task still depends on until released: whose releases the background re-read keeps
    reading after Done (item_sources.github._release_waits), so a task that reached Done before its release was read
    does not keep its dependent blocked for good."""
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT d.to_task FROM task_relations d JOIN tasks b ON b.id = d.to_task "
            "JOIN tasks w ON w.id = d.from_task WHERE d.kind = ? AND d.until = ? AND b.status = 'done' "
            "AND w.status = 'open' ORDER BY d.to_task",
            (RELATION, UNTIL),
        )
    ]


def released_now(conn: "sqlite3.Connection", blocker_id: str) -> list[str]:
    """The re-read just found a release holding a merge of `blocker_id` (Done, depended on until released): if the
    release production runs (as the last start-up saw it) now holds all its merges, release what waits on it, as
    `on_release` does. Returns the tasks released."""
    return _ship(conn, blocker_id, running(conn)[0])


def _ship(conn: "sqlite3.Connection", blocker: str | None, version: str) -> list[str]:
    found = conn.execute(
        "SELECT d.from_task, d.to_task, b.number FROM task_relations d JOIN tasks b ON b.id = d.to_task "
        "WHERE d.kind = ? AND d.until = ? AND b.status = 'done' AND (? IS NULL OR d.to_task = ?) "
        "ORDER BY d.created_at, d.rowid",
        (RELATION, UNTIL, blocker, blocker),
    ).fetchall()
    shipped: dict[str, str | None] = {}
    out: list[str] = []
    for task_id, blocker_id, number in found:
        blocker_id = str(blocker_id)
        if blocker_id not in shipped:
            shipped[blocker_id] = in_production(conn, blocker_id)
        if shipped[blocker_id] is None:
            continue
        named = ref({"id": blocker_id, "number": number})
        if shipped[blocker_id]:
            note, said = "its release runs in production", IN_PRODUCTION.format(ref=named, version=version)
        else:
            repo = running(conn)[1] or "the repository"
            note, said = "it merged nothing to release here", NOTHING_HERE.format(ref=named, repo=repo)
        _off(conn, [str(task_id)], blocker_id, str(_engine().SYSTEM), note)
        if release_one(conn, str(task_id), blocker_id, said):
            out.append(str(task_id))
    return out


def deleted(conn: "sqlite3.Connection", task_id: str, number: object, by: str) -> int:
    """`task_id` is being deleted: each dependency on it comes off (a history row on the task that waited), and each
    task left with no open dependency moves on, or its owner is woken once, "#304 was deleted". Call it before the
    relation rows go."""
    from . import hubdb

    H: Any = hubdb
    waiting = dependents(conn, task_id)
    for waiter in waiting:
        conn.execute(
            "DELETE FROM task_relations WHERE from_task=? AND to_task=? AND kind=?", (waiter, task_id, RELATION)
        )
        H._task_event(conn, waiter, by, RELATION, task_id, None, "it was deleted")
    said = DELETED.format(ref=ref({"id": task_id, "number": number}))
    return sum(release_one(conn, waiter, task_id, said) for waiter in waiting)


def restores(conn: "sqlite3.Connection", row: Row, restored_id: str) -> bool:
    """Whether a trashed dependency goes back with the task restored from the trash. A dependency on that task does not:
    the tasks that waited on it were released when it was deleted (`deleted`), and their owners may have moved on.
    One of that task on another goes back only while the other is still open: a dependency holds until its task first
    ends."""
    if str(row["to_task"]) == restored_id:
        return False
    found = conn.execute("SELECT status FROM tasks WHERE id=?", (row["to_task"],)).fetchone()
    return bool(found) and found[0] == "open"


def release_one(conn: "sqlite3.Connection", task_id: str, ended_id: str, said: str) -> int:
    """One task whose dependency `ended_id` came off. Still blocked by another: nothing, and nobody is woken. Else it
    moves on if everything is met and there is room; if not, its owner is woken once with `said`. A move the engine
    refuses is logged and the owner is told instead: a dependent never stops its blocker from ending. 1 when it was
    released."""
    E = _engine()
    row: Row | None = E.task(conn, task_id)
    if not row or row["status"] != "open" or not row.get("visit"):
        return 0
    if _open_of(conn, [task_id])[task_id]:
        return 0
    E.changed(conn, task_id, E.SYSTEM)
    from . import hubdb

    H: Any = hubdb
    try:
        conn.execute("SAVEPOINT depends_release")
        moved = E.advance(conn, task_id)
        conn.execute("RELEASE depends_release")
    except H.Refused as refused:  # advance logged it (task_engine.log_refused_move)
        conn.execute("ROLLBACK TO depends_release")
        conn.execute("RELEASE depends_release")
        LOG.info("dependency released %s but its move was refused: %s", task_id, refused)
        moved = None
    if moved:
        return 1
    E.tell_owner(conn, task_id, said, f"{WAKE}:{ended_id}")
    return 1
