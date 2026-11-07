# pyright: strict
"""The pipeline engine: what a stage's requirements ask of a ticket before it leaves the stage, the move gate, and where
each requirement stands on the ticket's card.

A pipeline is a task type and a stage is one of its steps. A stage may have exit requirements (stage_requirements). A
pipeline with none behaves exactly as before: every entry point below returns at once, writes nothing and adds no key to a
task's payload.

- hubdb.task_update calls `check_move` before the review gate when a ticket changes step.
- hubdb._set_status calls `on_step_change` after it writes the step event.
- app.py fills each task's `requirements` from `dots_many`; a task with none has no such key.
- github.py calls `work_changed` when a linked pull request's head moves and `queue_ejected` when the merge queue drops
  one; a body edit reaches `work_changed` through a trigger (migration 20261107173445).

Requirement state, for one requirement on one ticket (`STATES`):

- met: a person, a judge or a rule has passed it for the work as it is now.
- waiting: a reviewer (a person or a judge) has yet to decide.
- out_of_date: it was met, or evidence was attached, but the work changed since.
- failed: a judge or a source (checks, a review) said no for the work as it is now.
- none: nothing has been attached or decided.

The work version is `tasks.work_revision`. It goes up on every work change, so a decision or an attachment made at an
older revision no longer stands (it reads out_of_date) wherever the card is. A decision made under an older version of
the requirement itself stays met ("approved under v1"). A card is held to the requirements of its current stage and of the
stages it has visited; a requirement added later does not reach a card already past its stage.
"""

import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from . import hubdb, pipeline_rights

H: Any = hubdb

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .auth import Auth
    from .store import Store

Row = dict[str, Any]

STATES = ("met", "waiting", "out_of_date", "failed", "none")
CLOSED = ("closed", "declined")  # a stage a card is closed into, not advanced into
DONE = ("done", "closed", "declined")  # a ticket that has finished has no requirements left to show
STATE_WORDS = {"none": "missing", "waiting": "waiting", "out_of_date": "out of date", "failed": "failed"}
CHUNK = 500


# ---------------------------------------------------------------------------------------------- small helpers
def _marks(values: "list[Any] | tuple[Any, ...] | set[str]") -> str:
    return ",".join("?" * len(values))


def _chunks(values: list[str]) -> Iterator[list[str]]:
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


def _rows(conn: sqlite3.Connection, sql: str, args: "list[Any] | tuple[Any, ...]" = ()) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [str(d[0]) for d in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


def _one(conn: sqlite3.Connection, sql: str, args: "list[Any] | tuple[Any, ...]" = ()) -> Row | None:
    found = _rows(conn, sql, args)
    return found[0] if found else None


def _json(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return cast("dict[str, Any]", raw)
    try:
        value: object = json.loads(str(raw)) if raw else {}
    except (TypeError, ValueError):
        return {}
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _ids(rows: list[Row], key: str = "id") -> list[str]:
    return list(dict.fromkeys(str(r[key]) for r in rows if r.get(key)))


def has_requirements(conn: sqlite3.Connection, pipeline_id: str | None) -> bool:
    """Whether the pipeline has any live requirement: the engine's off switch."""
    if not pipeline_id:
        return False
    found = conn.execute(
        "SELECT 1 FROM stage_requirements WHERE pipeline_id = ? AND retired_at IS NULL LIMIT 1", (pipeline_id,)
    ).fetchone()
    return found is not None


# ---------------------------------------------------------------------------------------------- work version
def work_changed(conn: sqlite3.Connection, task_id: str) -> None:
    """The ticket's work changed (a new pull request head, an attachment that counts as work): evidence attached or
    judged before now reads out of date."""
    conn.execute("UPDATE tasks SET work_revision = work_revision + 1 WHERE id = ?", (task_id,))


def work_version(conn: sqlite3.Connection, task_id: str) -> int:
    found = conn.execute("SELECT work_revision FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return int(found[0] or 0) if found else 0


# ---------------------------------------------------------------------------------------------- stages
def stage(conn: sqlite3.Connection, step_id: str | None) -> Row | None:
    """A stage (task step) as a row, or None."""
    if not step_id:
        return None
    return _one(conn, "SELECT id, type_id, name, position, status FROM task_steps WHERE id = ?", (step_id,))


def stages(conn: sqlite3.Connection, pipeline_id: str) -> list[Row]:
    """A pipeline's stages in order."""
    return _rows(
        conn,
        "SELECT id, type_id, name, position, status FROM task_steps WHERE type_id = ? ORDER BY position, id",
        (pipeline_id,),
    )


def first_stage(conn: sqlite3.Connection, pipeline_id: str) -> Row | None:
    """The stage a ticket enters a pipeline at: its first that is not a closing one."""
    return next((s for s in stages(conn, pipeline_id) if s["status"] not in CLOSED), None)


def next_stage(conn: sqlite3.Connection, step_id: str | None) -> Row | None:
    """The stage after this one, in the pipeline's order, skipping a closing stage; None at the end."""
    here = stage(conn, step_id)
    if here is None:
        return None
    return next(
        (
            s
            for s in stages(conn, str(here["type_id"]))
            if s["position"] > here["position"] and s["status"] not in CLOSED
        ),
        None,
    )


def previous_stage(conn: sqlite3.Connection, step_id: str | None) -> Row | None:
    """The stage before this one, skipping a closing stage; None at the start."""
    here = stage(conn, step_id)
    if here is None:
        return None
    before = [
        s for s in stages(conn, str(here["type_id"])) if s["position"] < here["position"] and s["status"] not in CLOSED
    ]
    return before[-1] if before else None


# ---------------------------------------------------------------------------------------------- standing
@dataclass
class Standing:
    """Where one requirement stands on one ticket."""

    requirement: Row
    state: str
    decision: Row | None = None
    attachment: Row | None = None
    why: str = ""

    @property
    def id(self) -> str:
        return str(self.requirement["id"])

    @property
    def stage_id(self) -> str:
        return str(self.requirement["pipeline_stage_id"])


def _requirements(conn: sqlite3.Connection, pipeline_ids: list[str]) -> dict[str, list[Row]]:
    """{stage id: its live requirements in order} for these pipelines, from one query."""
    if not pipeline_ids:
        return {}
    found = _rows(
        conn,
        "SELECT id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, reviewer_id, backup_reviewer_id, "
        "applies_to_label, position, version, enforced_by, created_at FROM stage_requirements "
        f"WHERE retired_at IS NULL AND pipeline_id IN ({_marks(pipeline_ids)}) "
        "ORDER BY pipeline_stage_id, position, created_at, id",
        pipeline_ids,
    )
    out: dict[str, list[Row]] = {}
    for record in found:
        record["needs"] = _json(record["needs"])
        out.setdefault(str(record["pipeline_stage_id"]), []).append(record)
    return out


def _visited(conn: sqlite3.Connection, task_ids: list[str]) -> dict[str, dict[str, str]]:
    """{task id: {stage id: when it last left it}} for the stages each ticket has visited and left."""
    out: dict[str, dict[str, str]] = {}
    for part in _chunks(task_ids):
        for record in _rows(
            conn,
            "SELECT task_id, pipeline_stage_id, max(left_at) AS left_at FROM stage_visits "
            f"WHERE left_at IS NOT NULL AND task_id IN ({_marks(part)}) GROUP BY task_id, pipeline_stage_id",
            part,
        ):
            out.setdefault(str(record["task_id"]), {})[str(record["pipeline_stage_id"])] = str(record["left_at"])
    return out


def _fresh_needed(requirement: Row, verdict: str = "") -> bool:
    """Whether the work changing after the requirement was met makes it out of date."""
    kind, needs = requirement["kind"], requirement["needs"]
    if verdict == "override" or kind == "approval":
        return True
    if kind == "attachment":
        return needs.get("newer_than_change", True) is not False
    if kind == "run":
        return needs.get("at_head", True) is not False
    return False


@dataclass
class _Facts:
    """What the standings read about a page of tickets, each from one query."""

    tasks: dict[str, Row]
    labels: dict[str, set[str]]
    decisions: dict[tuple[str, str], Row]
    attachments: dict[tuple[str, str], Row]
    links: dict[str, list[Row]]


def _facts(conn: sqlite3.Connection, task_ids: list[str], requirement_ids: list[str], kinds: set[str]) -> _Facts:
    tasks: dict[str, Row] = {}
    labels: dict[str, set[str]] = {}
    decisions: dict[tuple[str, str], Row] = {}
    attachments: dict[tuple[str, str], Row] = {}
    links: dict[str, list[Row]] = {}
    for part in _chunks(task_ids):
        marks = _marks(part)
        for record in _rows(conn, f"SELECT id, work_revision, owner_id, due_at FROM tasks WHERE id IN ({marks})", part):
            tasks[str(record["id"])] = record
        for record in _rows(
            conn,
            "SELECT tt.task_id AS task_id, g.key AS key FROM task_tags tt JOIN tags g ON g.id = tt.tag_id "
            f"WHERE tt.task_id IN ({marks})",
            part,
        ):
            labels.setdefault(str(record["task_id"]), set()).add(str(record["key"]))
        if requirement_ids:
            wanted = _marks(requirement_ids)
            for record in _rows(
                conn,
                "SELECT * FROM stage_decisions WHERE undone_at IS NULL "
                f"AND task_id IN ({marks}) AND stage_requirement_id IN ({wanted}) ORDER BY created_at, rowid",
                [*part, *requirement_ids],
            ):
                key = (str(record["task_id"]), str(record["stage_requirement_id"]))
                if record["verdict"] == "withdrawn":
                    decisions.pop(key, None)
                else:
                    decisions[key] = record
            for record in _rows(
                conn,
                "SELECT * FROM requirement_attachments WHERE removed_at IS NULL "
                f"AND task_id IN ({marks}) AND stage_requirement_id IN ({wanted}) ORDER BY created_at, rowid",
                [*part, *requirement_ids],
            ):
                attachments[(str(record["task_id"]), str(record["stage_requirement_id"]))] = record
        if kinds & {"run", "field"}:
            for record in _rows(
                conn,
                f"SELECT task_id, state, checks, detail FROM task_links WHERE kind = 'pr' AND task_id IN ({marks})",
                part,
            ):
                links.setdefault(str(record["task_id"]), []).append(record)
    return _Facts(tasks, labels, decisions, attachments, links)


def _run_state(task_id: str, requirement: Row, facts: _Facts, conn: sqlite3.Connection) -> str:
    """The state a run requirement's source gives the work as it is now."""
    source = requirement["needs"].get("source")
    prs = [link for link in facts.links.get(task_id, []) if link["state"] != "closed"]
    if not prs:
        return "none"
    if source == "github_checks":
        seen = {str(link["checks"] or "pending") for link in prs}
        return "failed" if "failing" in seen else "met" if seen == {"passing"} else "waiting"
    if source == "test_receipt":
        return _receipt_state(conn, task_id, prs)
    if source == "check_pool":
        return _pool_state(conn, task_id, prs)
    return "none"


def _head(link: Row) -> str:
    return str(_json(link.get("detail")).get("head_sha") or "")


def _receipt_state(conn: sqlite3.Connection, task_id: str, prs: list[Row]) -> str:
    """A passing test receipt on the ticket at each open pull request's head (the review gate's own reading)."""
    from . import review_gate, test_receipts

    row = cast("Row | None", H.task(conn, task_id))
    if row is None:
        return "none"
    facts = review_gate.evidence(conn, row, H.KEEPER, None)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    states = {str(test_receipts.at_head(facts, _head(link))[0]) for link in prs}  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return "failed" if "failed" in states else "met" if states == {"passed"} else "none"


def _pool_state(conn: sqlite3.Connection, task_id: str, prs: list[Row]) -> str:
    """The verdict the ticket's review checks hold at the pull request's head."""
    from . import verdict_moves

    row = cast("Row | None", H.task(conn, task_id))
    if row is None:
        return "none"
    checks = cast("list[Row]", verdict_moves.review_checks(conn, row))  # pyright: ignore[reportUnknownMemberType]
    if not checks:
        return "none"
    verdict: str | None = verdict_moves.verdict_at(checks, _head(prs[0]).lower())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not verdict:
        return "waiting"
    return "met" if verdict in ("approve", "pass") else "failed"


def _field_state(task: Row, requirement: Row, facts: _Facts) -> str:
    needs = requirement["needs"]
    field = needs.get("field")
    task_id = str(task["id"])
    if field == "pr_linked":
        have = any(link["state"] != "closed" for link in facts.links.get(task_id, []))
    elif field == "label":
        keys = facts.labels.get(task_id, set())
        have = str(needs.get("label")) in keys if needs.get("label") else bool(keys)
    elif field == "assignee":
        have = bool(task.get("owner_id"))
    elif field == "due":
        have = bool(task.get("due_at"))
    else:
        have = False
    return "met" if have else "none"


def _standing(conn: sqlite3.Connection, task_id: str, requirement: Row, facts: _Facts) -> Standing:
    task = facts.tasks.get(task_id) or {"id": task_id, "work_revision": 0}
    revision = int(task.get("work_revision") or 0)
    key = (task_id, str(requirement["id"]))
    decision, attachment = facts.decisions.get(key), facts.attachments.get(key)
    if decision is not None and decision.get("requirement_attachment_id"):
        if attachment is None or attachment["id"] != decision["requirement_attachment_id"]:
            decision = None  # the evidence it judged was replaced or taken away
    if decision is not None:
        verdict = str(decision["verdict"])
        same = decision.get("work_version") is None or int(decision["work_version"]) == revision
        if verdict in ("met", "override"):
            if same or not _fresh_needed(requirement, verdict):
                return Standing(requirement, "met", decision, attachment)
            return Standing(requirement, "out_of_date", decision, attachment, "The work changed after it was met")
        if same and verdict == "failed":
            return Standing(requirement, "failed", decision, attachment, str(decision.get("reason") or ""))
        if same and verdict in ("unsure", "sent_back"):
            return Standing(requirement, "waiting", decision, attachment)
        decision = None  # a verdict on work that has changed since says nothing about it
    kind = str(requirement["kind"])
    if kind == "attachment":
        if attachment is None:
            return Standing(requirement, "none")
        if int(attachment["work_version"]) != revision and _fresh_needed(requirement):
            return Standing(requirement, "out_of_date", None, attachment, "Attached before the last change")
        return Standing(requirement, "met" if requirement["reviewed_by"] == "nobody" else "waiting", None, attachment)
    if kind == "field":
        return Standing(requirement, _field_state(task, requirement, facts))
    if kind == "run":
        return Standing(requirement, _run_state(task_id, requirement, facts, conn))
    return Standing(requirement, "waiting")  # an approval waits on its person


def _evaluate(
    conn: sqlite3.Connection, scopes: dict[str, dict[str, str | None]], by_stage: dict[str, list[Row]]
) -> dict[str, list[Standing]]:
    """The standings of the requirements in each ticket's scope ({task id: {stage id: when it was left, or None for
    the stage it is at}}), set-based: the number of queries does not depend on the number of tickets."""
    chosen: dict[str, list[Row]] = {}
    for task_id, wanted in scopes.items():
        for stage_id, left_at in wanted.items():
            for requirement in by_stage.get(stage_id, []):
                if left_at is None or str(requirement["created_at"]) <= left_at:
                    chosen.setdefault(task_id, []).append(requirement)
    if not chosen:
        return {}
    every = [r for rows in chosen.values() for r in rows]
    facts = _facts(
        conn, list(chosen), list(dict.fromkeys(str(r["id"]) for r in every)), {str(r["kind"]) for r in every}
    )
    out: dict[str, list[Standing]] = {}
    for task_id, rows in chosen.items():
        for requirement in rows:
            label = requirement.get("applies_to_label")
            if label and label not in facts.labels.get(task_id, set()):
                continue
            out.setdefault(task_id, []).append(_standing(conn, task_id, requirement, facts))
    return out


def _scopes(rows: list[Row], visited: dict[str, dict[str, str]]) -> dict[str, dict[str, str | None]]:
    """Each ticket's stages by the default rule: the one it is at, and the ones it visited and left."""
    scopes: dict[str, dict[str, str | None]] = {}
    for row in rows:
        task_id, here = str(row["id"]), row.get("step_id")
        scope: dict[str, str | None] = dict(visited.get(task_id, {}))
        if here:
            scope[str(here)] = None
        scopes[task_id] = scope
    return scopes


def standings(conn: sqlite3.Connection, rows: list[Row]) -> dict[str, list[Standing]]:
    """{task id: where each requirement that counts stands}, for the tickets among `rows` (id, type_id, step_id) that
    are in a pipeline with requirements. A ticket with none is not in the answer."""
    rows = [r for r in rows if r.get("type_id") and r.get("step_id") and r.get("status") not in DONE]
    by_stage = _requirements(conn, list(dict.fromkeys(str(r["type_id"]) for r in rows)))
    if not by_stage:
        return {}
    return _evaluate(conn, _scopes(rows, _visited(conn, _ids(rows))), by_stage)


def dots_many(c: sqlite3.Connection, rows: list[Row]) -> dict[str, list[dict[str, Any]]]:
    """{task id: [requirement dot, ...]} for the tickets among `rows` whose stage has requirements, from a fixed number
    of queries for the page. A dot is `{"id", "name", "state"}`. Those of the stage the ticket is at come first; a
    requirement of a stage it has left shows only while it is not met. A ticket with no dot is not in the answer."""
    out: dict[str, list[dict[str, Any]]] = {}
    here = {str(r["id"]): str(r.get("step_id") or "") for r in rows}
    for task_id, found in standings(c, rows).items():
        dots = [
            {"id": s.id, "name": str(s.requirement["name"]), "state": s.state}
            for s in sorted(found, key=lambda s: s.stage_id != here.get(task_id))
            if s.stage_id == here.get(task_id) or s.state != "met"
        ]
        if dots:
            out[task_id] = dots
    return out


# ---------------------------------------------------------------------------------------------- visits
def open_visit(conn: sqlite3.Connection, task_id: str) -> Row | None:
    return _one(conn, "SELECT * FROM stage_visits WHERE task_id = ? AND left_at IS NULL", (task_id,))


def _open_visit_at(
    conn: sqlite3.Connection,
    task_id: str,
    pipeline_id: str,
    step_id: str,
    entered_at: str,
    *,
    direction: str,
    came_from: str | None,
    actor: str | None,
) -> str:
    visit_id = cast(str, H.new_id())
    conn.execute(
        "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, entered_from_stage_id, direction, "
        "entered_by, entered_at, left_at) VALUES (?,?,?,?,?,?,?,?,NULL)",
        (visit_id, task_id, pipeline_id, step_id, came_from, direction, actor, entered_at),
    )
    return visit_id


def _entered(conn: sqlite3.Connection, task_id: str, fallback: object, before: str | None = None) -> str:
    """When the ticket reached its stage: its last step event, else when it was made."""
    sql = "SELECT max(created_at) AS at FROM history_events WHERE subject_type = 'task' AND subject_id = ? AND kind = 'step'"
    args: list[Any] = [task_id]
    if before:
        sql += " AND created_at < ?"
        args.append(before)
    found = _one(conn, sql, args)
    return str((found or {}).get("at") or fallback or H.now())


def ensure_visit(conn: sqlite3.Connection, task_id: str) -> Row | None:
    """The ticket's open visit, opened now at its stage when it has none (a ticket made before the pipeline had
    requirements). A visit left open at a stage the ticket is no longer at is closed first."""
    task = _one(conn, "SELECT id, type_id, step_id, created_at FROM tasks WHERE id = ?", (task_id,))
    if task is None or not task["type_id"] or not task["step_id"]:
        return None
    found = open_visit(conn, task_id)
    if found is not None and found["pipeline_stage_id"] == task["step_id"]:
        return found
    if found is not None:
        conn.execute("UPDATE stage_visits SET left_at = ? WHERE id = ?", (H.now(), found["id"]))
    _open_visit_at(
        conn,
        task_id,
        str(task["type_id"]),
        str(task["step_id"]),
        _entered(conn, task_id, task["created_at"]),
        direction="initial",
        came_from=None,
        actor=None,
    )
    return open_visit(conn, task_id)


def on_step_change(
    conn: sqlite3.Connection, actor: str, row: Row, type_id: str | None, step_id: str | None, ts: str
) -> None:
    """After a ticket's step changed and its step event is written (`row` is the ticket as it was): close its visit
    and open one at the new stage, and log a move back. A ticket in a pipeline with no requirements and no open visit is
    left alone, so such a pipeline writes nothing."""
    task_id = str(row.get("id") or "")
    if not task_id:
        return
    old_type: str | None = row.get("type_id")
    old_step: str | None = row.get("step_id")
    current = open_visit(conn, task_id)
    if current is None and not (has_requirements(conn, old_type) or has_requirements(conn, type_id)):
        return
    if current is None and old_type and old_step:
        # Opened late: the ticket was at its old stage before the pipeline had requirements.
        _open_visit_at(
            conn,
            task_id,
            old_type,
            old_step,
            _entered(conn, task_id, row.get("created_at") or ts, ts),
            direction="initial",
            came_from=None,
            actor=None,
        )
    conn.execute("UPDATE stage_visits SET left_at = ? WHERE task_id = ? AND left_at IS NULL", (ts, task_id))
    if not type_id or not step_id:
        return
    before, after = stage(conn, old_step), stage(conn, step_id)
    direction = "forward" if old_step else "initial"
    if old_step and old_type != type_id:
        direction = "handoff"
    elif before is not None and after is not None and after["position"] < before["position"]:
        direction = "back"
    _open_visit_at(conn, task_id, type_id, step_id, ts, direction=direction, came_from=old_step, actor=actor)
    if direction == "back" and has_requirements(conn, type_id):
        H._task_event(conn, task_id, actor, "stage_move_back", old_step, step_id, "")  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------------------------- the move gate
def _unmet(found: list[Standing]) -> list[Standing]:
    """What stops a forward move: every requirement that is not met, except one the review gate still decides."""
    return [s for s in found if s.state != "met" and not s.requirement.get("enforced_by")]


def _forward_scope(conn: sqlite3.Connection, task_id: str, here: Row, target: Row | None) -> dict[str, str | None]:
    """The stages a forward move to `target` is held to: where the ticket is, the ones it passes through on the way, and
    the ones it visited before the target (a stage it was moved back from, beyond the target, is not in the way). A move
    to a stage it has been in before is a loop (review sends it back to rework): only the stage it leaves counts."""
    scope: dict[str, str | None] = {}
    same = target is not None and target["type_id"] == here["type_id"]
    seen = _visited(conn, [task_id]).get(task_id, {})
    if target is not None and str(target["id"]) in seen:
        return {str(here["id"]): None}  # a loop back to a stage it has been in (rework): only what it leaves counts
    for stage_id, left_at in seen.items():
        placed = stage(conn, stage_id)
        if placed is None or placed["type_id"] != here["type_id"]:
            continue
        if not same or target is None or placed["position"] < target["position"]:
            scope[stage_id] = left_at
    if same and target is not None:
        for passed in stages(conn, str(here["type_id"])):
            if here["position"] < passed["position"] < target["position"]:
                scope.setdefault(str(passed["id"]), None)
    scope[str(here["id"])] = None
    return scope


def _names(unmet: list[Standing]) -> str:
    named = [f"{s.requirement['name']} ({STATE_WORDS.get(s.state, s.state)})" for s in unmet[:3]]
    return ", ".join(named) + (f" and {len(unmet) - 3} more" if len(unmet) > 3 else "")


def check_move(conn: sqlite3.Connection, actor: str, row: Row, step_id: str | None, override: bool = False) -> None:
    """Before a ticket changes to `step_id`: refuse the move (rule "requirement") when the stage's requirements do not
    allow it, unless a rule editor overrides them (logged). A pipeline with no requirements allows every move.

    Forward, every requirement that counts must be met: the stage the ticket is at, the stages it passes through and the
    ones it visited. A rule editor may move it past them with `override`; that writes one `override` decision for each
    requirement it passed. Backward, only a rule editor and Tico itself (the legacy flows move as Tico) may move a
    ticket; the move is logged as `stage_move_back` by `on_step_change`."""
    task_id = str(row.get("id") or "")
    if not step_id or not task_id:
        return
    now = _one(conn, "SELECT type_id, step_id FROM tasks WHERE id = ?", (task_id,))
    if now is None or not now["step_id"] or step_id == now["step_id"]:
        return
    pipeline_id = str(now["type_id"])
    if not has_requirements(conn, pipeline_id):
        return
    here, target = stage(conn, str(now["step_id"])), stage(conn, step_id)
    if here is None:
        return
    editor = pipeline_rights.may_edit_rules(conn, actor, pipeline_id)
    if target is not None and target["type_id"] == here["type_id"] and target["position"] < here["position"]:
        if actor != H.KEEPER and not editor:
            H.refuse(
                conn,
                actor,
                "requirement",
                "Only the pipeline's owner or an Admin moves a ticket back a stage",
                task=task_id,
            )
        return
    scope = _forward_scope(conn, task_id, here, target)
    unmet = _unmet(_evaluate(conn, {task_id: scope}, _requirements(conn, [pipeline_id])).get(task_id, []))
    if not unmet:
        return
    if override and editor:
        _record_override(conn, actor, task_id, unmet)
        return
    hint = "" if editor else " A pipeline editor can move it on anyway."
    H.refuse(conn, actor, "requirement", f"Can't leave {here['name']} yet: {_names(unmet)}.{hint}", task=task_id)


def _record_override(conn: sqlite3.Connection, actor: str, task_id: str, unmet: list[Standing]) -> None:
    """A rule editor moved a ticket past requirements that were not met: one `override` decision for each, bound to the work
    as it is now, and one line in the ticket's history."""
    visit = ensure_visit(conn, task_id)
    revision = work_version(conn, task_id)
    stamp = H.now()
    for s in unmet:
        conn.execute(
            "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_visit_id, stage_requirement_id, "
            "requirement_version, requirement_attachment_id, attachment_digest, work_version, judge, judge_actor, "
            "verdict, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,'person',?,'override',?,?)",
            (
                H.new_id(),
                task_id,
                s.stage_id,
                visit["id"] if visit else None,
                s.id,
                s.requirement["version"],
                s.attachment["id"] if s.attachment else None,
                s.attachment["digest"] if s.attachment else None,
                revision,
                actor,
                "Moved on without it",
                stamp,
            ),
        )
    H._task_event(  # pyright: ignore[reportPrivateUsage]
        conn, task_id, actor, "stage_override", None, ", ".join(str(s.requirement["name"]) for s in unmet), ""
    )


# ---------------------------------------------------------------------------------------------- advance and ejection
def advance(conn: sqlite3.Connection, actor: str, task_id: str) -> dict[str, Any]:
    """Move the ticket on from its stage: through the stage's `move` link into the other pipeline's first stage, else to
    the next stage. The requirements still gate it (`check_move`), and a refusal undoes only this move.

    {"moved": bool, "to": step id | None, "pipeline": id | None, "handoff": link id | None, "reason": str}."""
    from . import pipeline_links

    row = _one(conn, "SELECT id, type_id, step_id FROM tasks WHERE id = ?", (task_id,))
    none: dict[str, Any] = {"moved": False, "to": None, "pipeline": None, "handoff": None, "reason": ""}
    if row is None or not row["step_id"]:
        return {**none, "reason": "It is in no stage"}
    link = pipeline_links.link_for_stage(conn, str(row["step_id"]))
    target: Row | None
    if link is not None and link["mode"] == "move":
        target = first_stage(conn, str(link["to_pipeline_id"]))
    else:
        link, target = None, next_stage(conn, str(row["step_id"]))
    if target is None:
        return {**none, "reason": "There is no next stage"}
    conn.execute("SAVEPOINT pipeline_advance")
    try:
        if link is not None:
            H.task_update(conn, actor, task_id, type=target["type_id"], step=target["id"])
            pipeline_links.record_handoff(conn, str(link["id"]), task_id, task_id)
        else:
            H.task_update(conn, actor, task_id, step=target["id"])
    except H.Refused as exc:
        conn.execute("ROLLBACK TO pipeline_advance")
        conn.execute("RELEASE pipeline_advance")
        return {**none, "reason": str(exc.detail or exc)}
    conn.execute("RELEASE pipeline_advance")
    return {
        "moved": True,
        "to": target["id"],
        "pipeline": target["type_id"],
        "handoff": link["id"] if link else None,
        "reason": "",
    }


def queue_ejected(conn: sqlite3.Connection, task_id: str, reason: str) -> bool:
    """The merge queue dropped the ticket's pull request: the card goes back a stage, with the reason in its history.
    Tico moves it (a back move needs no editor). Only a ticket in a pipeline with requirements moves; the answer says
    whether it did."""
    row = _one(conn, "SELECT id, type_id, step_id FROM tasks WHERE id = ?", (task_id,))
    if row is None or not row["step_id"] or not has_requirements(conn, str(row["type_id"])):
        return False
    before = previous_stage(conn, str(row["step_id"]))
    if before is None:
        return False
    H.task_update(conn, H.KEEPER, task_id, step=before["id"])
    H._task_event(  # pyright: ignore[reportPrivateUsage]
        conn, task_id, H.KEEPER, "queue_ejected", row["step_id"], before["id"], reason or "Removed from the merge queue"
    )
    return True


# ---------------------------------------------------------------------------------------------- detail for one ticket
def detail(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    """Each requirement that counts on one ticket, with who decided and what is attached: the ticket page's list."""
    row = _one(conn, "SELECT id, type_id, step_id, status FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        return []
    out: list[dict[str, Any]] = []
    names = {str(s["id"]): str(s["name"]) for s in stages(conn, str(row["type_id"] or ""))}
    for s in standings(conn, [row]).get(task_id, []):
        decision, attachment = s.decision, s.attachment
        out.append(
            {
                "id": s.id,
                "name": s.requirement["name"],
                "kind": s.requirement["kind"],
                "stage_id": s.stage_id,
                "stage": names.get(s.stage_id, ""),
                "state": s.state,
                "why": s.why,
                "reviewed_by": s.requirement["reviewed_by"],
                "reviewer_id": s.requirement["reviewer_id"],
                "version": s.requirement["version"],
                "decision": None
                if decision is None
                else {
                    "id": decision["id"],
                    "judge": decision["judge"],
                    "judge_actor": decision["judge_actor"],
                    "verdict": decision["verdict"],
                    "requirement_version": decision["requirement_version"],
                    "at": decision["created_at"],
                },
                "attachment": None
                if attachment is None
                else {
                    "id": attachment["id"],
                    "source": attachment["source"],
                    "digest": attachment["digest"],
                    "by": attachment["attached_by"],
                    "at": attachment["created_at"],
                },
            }
        )
    return out


# ---------------------------------------------------------------------------------------------- boot
SEEDED = "pipeline_engine.seeded"
LATE_MINUTES = 240


def boot(c: sqlite3.Connection) -> None:
    """Idempotent, at every start and after the Dev and Product types exist: give the Dev tickets type the prefix D and
    the Product roadmap P while nobody holds them, give the steps of a type not seen before their default Warn after, and
    open a stage visit for each active ticket without one. The default limits are given once for each type (an event
    records it), so a limit a person clears stays cleared."""
    types = {r[1] for r in c.execute("PRAGMA table_info(task_types)")}
    steps = {r[1] for r in c.execute("PRAGMA table_info(task_steps)")}
    if not {"prefix", "role"} <= types or "limit_minutes" not in steps:
        return
    for prefix, where in (
        ("D", "name = 'Dev tickets' COLLATE NOCASE OR role = 'engineering'"),
        ("P", "role = 'product-roadmap'"),
    ):
        c.execute(
            f"UPDATE task_types SET prefix = ? WHERE id = (SELECT id FROM task_types WHERE ({where}) "
            "AND prefix IS NULL ORDER BY name = 'Dev tickets' COLLATE NOCASE DESC, created_at, id LIMIT 1) "
            "AND NOT EXISTS (SELECT 1 FROM task_types WHERE prefix = ?)",
            (prefix, prefix),
        )
    for type_id, role in c.execute("SELECT id, role FROM task_types").fetchall():
        if c.execute("SELECT 1 FROM events WHERE action = ? AND target = ? LIMIT 1", (SEEDED, type_id)).fetchone():
            continue
        for step_id, status, flow in c.execute(
            "SELECT id, status, flow FROM task_steps WHERE type_id = ? AND limit_minutes IS NULL", (type_id,)
        ).fetchall():
            hours = _json(flow).get("late_hours")
            if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
                minutes: int | None = round(hours * 60)
            else:
                minutes = LATE_MINUTES if role == "engineering" and status == "doing" else None
            if minutes:
                c.execute("UPDATE task_steps SET limit_minutes = ? WHERE id = ?", (minutes, step_id))
        H.event(c, H.KEEPER, SEEDED, type_id, {})
    c.execute(
        "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, entered_from_stage_id, direction, "
        "entered_by, entered_at, left_at) "
        "SELECT lower(hex(randomblob(16))), t.id, t.type_id, t.step_id, NULL, 'initial', NULL, "
        "COALESCE((SELECT max(h.created_at) FROM history_events h WHERE h.subject_type = 'task' AND h.subject_id = t.id "
        "AND h.kind = 'step' AND h.new = t.step_id), t.created_at), NULL "
        "FROM tasks t WHERE t.type_id IS NOT NULL AND t.step_id IS NOT NULL "
        "AND t.status IN ('open','doing','waiting','review','ready') "
        "AND NOT EXISTS (SELECT 1 FROM stage_visits v WHERE v.task_id = t.id AND v.left_at IS NULL)"
    )


# ---------------------------------------------------------------------------------------------- routes
def install(app: "FastAPI", store: "Store", mutate: Callable[..., Any], auth: "Auth") -> None:
    """The ticket's requirement detail, the Tico Team setup."""
    from fastapi import Request

    from . import pipeline_setup

    @app.get("/api/v2/tasks/{task_id}/requirements")  # pyright: ignore[reportUntypedFunctionDecorator]
    def requirements(request: Request, task_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        who = request.state.identity
        guard: Any = auth
        reader: Any = store
        with reader.read() as read:
            c = cast("sqlite3.Connection", read)
            ident = cast("str", guard.resolve_task(c, who, task_id))
            guard.task(c, who, ident)
            return {"requirements": detail(c, ident)}

    pipeline_setup.install(app, store, mutate, auth)
