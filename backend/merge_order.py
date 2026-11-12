# pyright: strict
"""A task that merges after another one ("after #40"): docs/pipelines-v3.md, "Whose turn it is" and the Engineering
pipeline's Merge stage.

When two tasks change the same lines, one must merge after the other. The order is a relation of kind `after` on the
later task (task_relations: `from_task` merges after `to_task`), written with `tico task after`, `--after` when filing
or creating, or the MCP tools; the earlier task reads it as "Before #42". It changes nothing before the stage that
queues PRs (Merge): both tasks build and are reviewed side by side.

- `held` is the one check. A task is held while it is open in a stage whose on-enter action queues its PRs, none of its
  PRs was queued in this visit (a person may queue it anyway), not all of them merged, and a task it goes after is
  still open and has not merged its PRs. Entering Merge (`item_sources.github.queue`), a PR whose metadata comes in late
  (`metadata_changed`) and `tico task queue` (`requeue`) all ask it, and nothing else holds a queue.
- `standing`: while held, one more thing the task needs to leave its stage, put first (task_requirements.standings):
  waiting on the earlier task (waiting_on's `tasks` state), "After #40: waits for it to merge". Nobody's turn and
  nobody is woken, and it is not Late (never_stuck). A reader who may not open #40 reads "After a task you can't open".
- `release`: when a task ends or its PR merges, every task held on it is read again. One no longer held is queued now
  (its `queue` actions owed, as on entering Merge; GitHub's `mergeable` is read when they are carried out, and a PR that
  now conflicts goes to the owner with the send-back to Fixes 2 naming the task it is to be rebased on,
  `conflict_line`). A task before it that was closed without merging ends the order: the later task's owner is told
  once, "#40 closed without merging. Queue it, or send it back.", and it is their turn (`closed_line`); Tico does not
  queue it by itself.

An earlier task "has merged" when it is Done, or when it has PRs and every one that was not closed without merging has
merged (a Product task with no PR: when it is Done). A task never goes after itself, and an order that makes a loop is
refused (`check`).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

    from . import task_requirements as R

Row = dict[str, Any]

KIND = "merge_order"  # the standing's requirement kind; never a stored requirement
RELATION = "after"
LINE = "After {ref}: waits for it to merge"
UNNAMED = "After a task you can't open: waits for it to merge"
CLOSED = "{ref} closed without merging. Queue it, or send it back."
WAKE_CLOSED = "after_closed"
QUEUED_ANYWAY = "Queued before {ref} merged"


def _engine() -> Any:
    from . import task_engine

    return task_engine


def _github() -> Any:
    from .item_sources import github

    return github


def ref(row: Row | None) -> str:
    """How a line names a task: `#40`, or its id's first eight characters when it has no number."""
    if not row:
        return "a task"
    number = row.get("number")
    return f"#{number}" if number is not None else str(row["id"])[:8]


def earlier_ids(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    """The tasks this one goes after, oldest order first."""
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT to_task FROM task_relations WHERE from_task=? AND kind=? ORDER BY created_at, rowid",
            (task_id, RELATION),
        )
    ]


def later_ids(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    """The tasks that go after this one."""
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT from_task FROM task_relations WHERE to_task=? AND kind=? ORDER BY created_at, rowid",
            (task_id, RELATION),
        )
    ]


def merged(conn: "sqlite3.Connection", row: Row) -> bool:
    """Whether a task has merged, for the tasks that go after it: Done, or every PR of it that was not closed without
    merging has merged (and it has one)."""
    if row.get("status") == "done":
        return True
    states = [
        str(link["metadata"]["state"]) if link["metadata"] else "unknown"
        for link in _github().prs_of(conn, str(row["id"]))
    ]
    kept = [state for state in states if state != "closed"]
    return bool(kept) and all(state == "merged" for state in kept)


def waits_for(conn: "sqlite3.Connection", task_id: str) -> Row | None:
    """The first task this one goes after that is still open and has not merged, or None."""
    E = _engine()
    for earlier_id in earlier_ids(conn, task_id):
        earlier: Row | None = E.task(conn, earlier_id)
        if earlier and earlier["status"] == "open" and not merged(conn, earlier):
            return earlier
    return None


def _queued(conn: "sqlite3.Connection", row: Row) -> bool:
    """Whether a `queue` was owed for any PR of the task in this visit (and is not moot)."""
    GS = _github()
    return any(e["state"] != "moot" for e in GS.effects(conn, str(row["visit"]["id"])))


def _all_merged(conn: "sqlite3.Connection", row: Row) -> bool:
    links = _github().prs_of(conn, str(row["id"]))
    return bool(links) and all(link["metadata"] and link["metadata"]["state"] == "merged" for link in links)


def held(conn: "sqlite3.Connection", row: Row | None) -> Row | None:
    """The one check (module docstring): the task `row` (task_engine.task's shape) waits for this earlier task before
    its PRs are queued, or None when nothing holds it."""
    if not row or row.get("status") != "open" or not row.get("visit"):
        return None
    if not _github().queues_prs(row.get("stage")):
        return None
    if not earlier_ids(conn, str(row["id"])):
        return None
    if _queued(conn, row) or _all_merged(conn, row):
        return None
    return waits_for(conn, str(row["id"]))


def standing(conn: "sqlite3.Connection", row: Row) -> "R.Standing | None":
    """While the task is held (`held`): waiting on the earlier task, "After #40: waits for it to merge"."""
    from . import task_requirements as R

    earlier = held(conn, row)
    if not earlier:
        return None
    requirement: Row = {
        "id": f"{KIND}:{row['id']}",
        "kind": KIND,
        "line": "",
        "stage_id": None,
        "visit_id": None,
        "person_id": None,
        "added_by_id": None,
        "can_push_past": 0,
        "who_oks": "none",
        "settings": {},
        "shows": None,
        "choices": None,
        "created_at": row["visit"]["entered_at"],
    }
    line = LINE.format(ref=ref(earlier))
    return R.Standing(requirement, R.WAITING, str(earlier["id"]), line, about=str(earlier["id"]))


def is_order(requirement: Row) -> bool:
    return requirement.get("kind") == KIND


def is_order_wait(requirement_id: object) -> bool:
    """Whether a wait (waiting_on.Wait.requirement_id) is this module's."""
    return str(requirement_id or "").startswith(KIND + ":")


def wait_id(task_id: str) -> str:
    return f"{KIND}:{task_id}"


def closed_before(conn: "sqlite3.Connection", row: Row) -> Row | None:
    """A task the open task goes after that was closed without merging, while nothing else still holds it and none of
    its PRs was queued in this visit: the owner's to queue or send back (`CLOSED`). None otherwise."""
    if not row or row.get("status") != "open" or not row.get("visit"):
        return None
    if not _github().queues_prs(row.get("stage")) or _queued(conn, row) or waits_for(conn, str(row["id"])):
        return None
    E = _engine()
    for earlier_id in earlier_ids(conn, str(row["id"])):
        earlier: Row | None = E.task(conn, earlier_id)
        if earlier and earlier["status"] == "closed" and not merged(conn, earlier):
            return earlier
    return None


def stops_queue(conn: "sqlite3.Connection", row: Row) -> Row | None:
    """Whether Tico must not queue the task's PRs by itself now: it is held (`held`), or a task it goes after closed
    without merging, which is its owner's to judge (`closed_before`). The earlier task, or None."""
    return held(conn, row) or closed_before(conn, row)


def closed_line(earlier: Row) -> str:
    return CLOSED.format(ref=ref(earlier))


def just_merged(conn: "sqlite3.Connection", row: Row) -> Row | None:
    """The task the open task went after that merged last, for the line a conflict's send-back adds: the Builder
    rebases onto it. None when the task goes after nothing that merged."""
    E = _engine()
    found: Row | None = None
    for earlier_id in earlier_ids(conn, str(row["id"])):
        earlier: Row | None = E.task(conn, earlier_id)
        if earlier and merged(conn, earlier):
            if not found or str(earlier.get("updated_at") or "") >= str(found.get("updated_at") or ""):
                found = earlier
    return found


def conflict_line(conn: "sqlite3.Connection", row: Row, ref_: str) -> str:
    """The send-back to Fixes 2 for a PR that conflicts with its base branch (item_sources.github
    SENT_BACK_FOR_CONFLICT), naming the task it goes after that merged, for the Builder to rebase onto."""
    GS = _github()
    earlier = just_merged(conn, row)
    if not earlier:
        return GS.SENT_BACK_FOR_CONFLICT.format(ref=ref_, task=row["id"])
    return GS.SENT_BACK_AFTER.format(ref=ref_, task=row["id"], earlier=ref(earlier))


# ----------------------------------------------------------------------------- writes
def check(conn: "sqlite3.Connection", actor: str, later: Row, earlier: Row) -> None:
    """Refuse an order that cannot hold: on a task that has ended, after one that has ended, or a loop (#40 already goes
    after #42, however far round). The writer's right to change the later task and read the earlier one is checked by
    the caller (task_relations.relate)."""
    from . import hubdb

    H: Any = hubdb
    for row, what in ((later, "it"), (earlier, ref(earlier))):
        if row["status"] != "open":
            who = "The task" if what == "it" else what
            H.refuse(conn, actor, "task_ended", f"{who} has ended: there is no merge to wait for")
    path = _path(conn, str(earlier["id"]), str(later["id"]))
    if path:
        names = " → ".join(ref(_engine().task(conn, tid)) for tid in [str(later["id"]), *path])
        H.refuse(
            conn,
            actor,
            "kind",
            f"That would make a loop ({names}): {ref(earlier)} already goes after {ref(later)}.",
        )


def _path(conn: "sqlite3.Connection", start: str, goal: str) -> list[str]:
    """The tasks from `start` to `goal` along "goes after", both ends included, or [] when there is no such path."""
    back: dict[str, str | None] = {start: None}
    frontier = [start]
    while frontier:
        nxt: list[str] = []
        for tid in frontier:
            for earlier_id in earlier_ids(conn, tid):
                if earlier_id in back:
                    continue
                back[earlier_id] = tid
                if earlier_id == goal:
                    out = [goal]
                    cursor = back[goal]
                    while cursor is not None:
                        out.append(cursor)
                        cursor = back[cursor]
                    return list(reversed(out))
                nxt.append(earlier_id)
        frontier = nxt
    return []


def release(conn: "sqlite3.Connection", earlier_id: str, by: str) -> int:
    """`earlier_id` moved, ended or merged a PR: every task that goes after it is read again (`release_one`). Returns
    how many were queued."""
    return sum(release_one(conn, later_id, by) for later_id in later_ids(conn, earlier_id))


def release_one(conn: "sqlite3.Connection", later_id: str, by: str) -> int:
    """Read one task that goes after others again: open in a stage that queues PRs and no longer held, its PRs are
    queued now (`item_sources.github.queue`), unless a task it goes after was closed without merging: its owner is
    told once and it is their turn (`closed_before`). 1 when it was queued. Idempotent: a task whose PRs were queued in
    this visit is not queued again."""
    E = _engine()
    GS = _github()
    row: Row | None = E.task(conn, later_id)
    if not row or row["status"] != "open" or not row.get("visit") or not GS.queues_prs(row.get("stage")):
        return 0
    if held(conn, row) or _queued(conn, row) or _all_merged(conn, row):
        return 0
    closed = closed_before(conn, row)
    if closed:
        E.changed(conn, later_id, E.SYSTEM)
        E.tell_owner(conn, later_id, closed_line(closed), f"{WAKE_CLOSED}:{closed['id']}")
        return 0
    if not GS.queue(conn, row, by):
        return 0
    E.changed(conn, later_id, E.SYSTEM)
    return 1


def released_on_merge(conn: "sqlite3.Connection", task_ids: list[str], by: str) -> int:
    """A PR merged: release the tasks that go after each task that links it (`release`)."""
    return sum(release(conn, task_id, by) for task_id in task_ids)
