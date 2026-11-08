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
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from . import hubdb, pipeline_rights, requirement_attachments

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
# History markers a stage move writes for the shipment reading (_shipments): a Close or a Decline that placed a ticket in a
# stage (a cancellation), and a move that finished a ticket into a done or closed stage (a completion).
CLOSED_INTO = "stage_closed"
FINISHED_INTO = "stage_finished"


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
    return _one(conn, "SELECT id, type_id, name, position, status, card_cap FROM task_steps WHERE id = ?", (step_id,))


def stages(conn: sqlite3.Connection, pipeline_id: str) -> list[Row]:
    """A pipeline's stages in order."""
    return _rows(
        conn,
        "SELECT id, type_id, name, position, status, card_cap FROM task_steps WHERE type_id = ? ORDER BY position, id",
        (pipeline_id,),
    )


def first_stage(conn: sqlite3.Connection, pipeline_id: str) -> Row | None:
    """The stage a ticket enters a pipeline at: its first that is not a closing one and not On ice (a stage capped at 0,
    D69): new work arrives past a parking stage, it is never filed on one."""
    return next((s for s in stages(conn, pipeline_id) if s["status"] not in CLOSED and s["card_cap"] != 0), None)


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
    children: dict[str, list[Row]]  # a ticket's subtasks: id, status, type_id, cascaded (closed with its parent)
    spawns: dict[str, set[str]]  # a pipeline id -> the pipelines its spawn links file tickets into
    shipped: set[str] = field(default_factory=set)  # subtask ids that visited their pipeline's output stage


def _facts(conn: sqlite3.Connection, task_ids: list[str], requirement_ids: list[str], kinds: set[str]) -> _Facts:
    tasks: dict[str, Row] = {}
    labels: dict[str, set[str]] = {}
    decisions: dict[tuple[str, str], Row] = {}
    attachments: dict[tuple[str, str], Row] = {}
    links: dict[str, list[Row]] = {}
    for part in _chunks(task_ids):
        marks = _marks(part)
        for record in _rows(
            conn, f"SELECT id, work_revision, owner_id, due_at, type_id FROM tasks WHERE id IN ({marks})", part
        ):
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
                f"SELECT task_id, url, state, checks, detail FROM task_links WHERE kind = 'pr' AND task_id IN ({marks})",
                part,
            ):
                links.setdefault(str(record["task_id"]), []).append(record)
    children: dict[str, list[Row]] = {}
    spawns: dict[str, set[str]] = {}
    if "run" in kinds:
        for part in _chunks(task_ids):
            for record in _rows(
                conn,
                "SELECT r.to_task AS parent, c.id AS id, c.status AS status, c.type_id AS type_id, "
                "EXISTS (SELECT 1 FROM history_events h WHERE h.subject_type = 'task' AND h.subject_id = c.id "
                "AND h.kind = 'closed_with' AND h.new = r.to_task) AS cascaded "
                "FROM task_relations r JOIN tasks c ON c.id = r.from_task "
                f"WHERE r.kind = 'parent' AND r.to_task IN ({_marks(part)}) ORDER BY c.created_at, c.id",
                part,
            ):
                children.setdefault(str(record["parent"]), []).append(record)
        for record in _rows(
            conn,
            "SELECT from_pipeline_id, to_pipeline_id FROM pipeline_links WHERE mode = 'spawn' AND retired_at IS NULL",
        ):
            spawns.setdefault(str(record["from_pipeline_id"]), set()).add(str(record["to_pipeline_id"]))
    shipped = set(_shipments(conn, [str(child['id']) for rows in children.values() for child in rows]))
    return _Facts(tasks, labels, decisions, attachments, links, children, spawns, shipped)


def output_stage(conn: sqlite3.Connection, pipeline_id: str) -> str | None:
    """The stage a pipeline's work is output at: its factory's output stage when set, else its last stage that is not
    declined (the booted Engineering Done stage is closed, and it is the output)."""
    found = _one(
        conn,
        "SELECT f.output_stage_id AS stage FROM task_types t JOIN factories f ON f.id = t.factory_id "
        "JOIN task_steps s ON s.id = f.output_stage_id WHERE t.id = ? AND s.type_id = ?",
        (pipeline_id, pipeline_id),
    )
    if found and found["stage"]:
        return str(found["stage"])
    last = _one(
        conn,
        "SELECT id FROM task_steps WHERE type_id = ? AND status != 'declined' ORDER BY position DESC, id LIMIT 1",
        (pipeline_id,),
    )
    return str(last["id"]) if last else None


def shipped(conn: sqlite3.Connection, task_ids: list[str]) -> set[str]:
    """The tickets among `task_ids` that shipped: each finished, which is a completion and never a cancellation. The
    subtask standing and the factory done count both read this one predicate."""
    return set(shipped_at(conn, task_ids))


def shipped_at(conn: sqlite3.Connection, task_ids: list[str]) -> dict[str, str]:
    """{ticket: when it first shipped} for the tickets among `task_ids` that shipped (see _shipments). The factory done count
    counts a ticket in a period only when this moment is in the period."""
    return _shipments(conn, list(dict.fromkeys(task_ids)))


def _shipments(conn: sqlite3.Connection, task_ids: list[str]) -> dict[str, str]:
    """The tickets that shipped, judged per transition and never by where they sit now, with the moment of their first
    shipment. A transition ships a ticket when it is a completion: a `done` status in its history (which still counts after
    it is closed or archived), or a move that finished it into the output stage of its own pipeline (a `stage_finished`
    marker). Nothing else ships. A Close or a Decline writes a `stage_closed` marker and is never a completion, so a ticket
    whose only entries in the output stage are cancellations never shipped; a later genuine finish still ships a ticket
    that was closed unfinished and reopened, and closing a shipped ticket in place takes nothing back.

    A visit into a stage is not evidence of a finish: a move with no marker (history from before the markers) does not count.
    Markers and visits were released together, so no real hub has visits without markers."""
    cards: dict[str, Row] = {}
    for part in _chunks(task_ids):
        for record in _rows(
            conn,
            f"SELECT id, type_id, step_id FROM tasks WHERE id IN ({_marks(part)})",
            part,
        ):
            cards[str(record["id"])] = record
    if not cards:
        return {}
    outputs: dict[str, str | None] = {}
    for card in cards.values():
        type_id = str(card["type_id"] or "")
        if type_id and type_id not in outputs:
            outputs[type_id] = output_stage(conn, type_id)
    first: dict[str, str] = {}

    def shipped_on(task_id: str, at: object) -> None:
        when = str(at or "")
        if when and (task_id not in first or when < first[task_id]):
            first[task_id] = when

    for part in _chunks(list(cards)):
        marks = _marks(part)
        for record in _rows(
            conn,
            "SELECT subject_id, MIN(created_at) AS at FROM history_events WHERE subject_type = 'task' AND kind = 'status' "
            f"AND new = 'done' AND subject_id IN ({marks}) GROUP BY subject_id",
            part,
        ):
            shipped_on(str(record["subject_id"]), record["at"])
        for record in _rows(
            conn,
            "SELECT subject_id, new, MIN(created_at) AS at FROM history_events WHERE subject_type = 'task' "
            f"AND kind = ? AND subject_id IN ({marks}) GROUP BY subject_id, new",
            [FINISHED_INTO, *part],
        ):
            task_id = str(record["subject_id"])
            output = outputs.get(str(cards[task_id]["type_id"] or ""))
            if output and str(record["new"]) == output:
                shipped_on(task_id, record["at"])
    return first


def _mark_stage(conn: sqlite3.Connection, actor: str, task_id: str, kind: str, stage_id: str, at: str) -> None:
    """A stage marker for the shipment reading: `kind` is CLOSED_INTO (a cancellation) or FINISHED_INTO (a completion)."""
    from .strategy_acts import ACTION

    H.history_event(
        conn,
        "task",
        task_id,
        actor,
        kind,
        None,
        stage_id,
        "",
        action_id=ACTION.get() or None,
        via=H.VIA.get() or None,
        created_at=at,
    )


def _subtasks_state(task_id: str, facts: _Facts, pipeline: str | None = None) -> str:
    """Engineering shipped: the ticket has subtasks in the pipelines its pipeline spawns into, or only in `pipeline` when the
    requirement names one (D64: a subtask in another pipeline neither meets nor blocks it). A subtask shipped when it is
    done or visited its pipeline's output stage (its Done, whatever its status column says). A subtask closed with its
    card or declined is gone: it neither counts as shipped nor blocks. Every live subtask must be finished, and at least
    one must have shipped; a set that is all closed with none shipped is failed (the PM is asked), never passed."""
    card = facts.tasks.get(task_id) or {}
    wanted = {pipeline} if pipeline else facts.spawns.get(str(card.get("type_id") or ""), set())
    kids = [child for child in facts.children.get(task_id, []) if str(child["type_id"]) in wanted]
    if not kids:
        return "none"
    live = [k for k in kids if not (k["status"] == "declined" or k["cascaded"])]
    if not live:
        return "failed"
    if any(str(child["status"]) not in DONE for child in live):
        return "waiting"
    shipped = any(child["status"] == "done" or str(child["id"]) in facts.shipped for child in live)
    return "met" if shipped else "failed"


def _run_state(task_id: str, requirement: Row, facts: _Facts, conn: sqlite3.Connection) -> str:
    """The state a run requirement's source gives the work as it is now."""
    source = requirement["needs"].get("source")
    if source == "subtasks":
        named = requirement["needs"].get("pipeline")
        return _subtasks_state(task_id, facts, str(named) if named else None)
    prs = [link for link in facts.links.get(task_id, []) if link["state"] != "closed"]
    if not prs:
        return "none"
    if source == "github_checks":
        seen = {str(link["checks"] or "pending") for link in prs}
        return "failed" if "failing" in seen else "met" if seen == {"passing"} else "waiting"
    if source == "test_receipt":
        return _receipt_state(conn, task_id, prs)
    if source == "check_pool":
        return _pool_state(conn, task_id, requirement, prs)
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


def _pool_state(conn: sqlite3.Connection, task_id: str, requirement: Row, prs: list[Row]) -> str:
    """The verdict the ticket's review checks hold at each open pull request's head. Only checks filed at the requirement's
    own stage count (a Review 1 approval never meets Review 2), and every open head needs a passing verdict of its own."""
    from . import pr_identity, verdict_moves

    row = cast("Row | None", H.task(conn, task_id))
    if row is None:
        return "none"
    checks = [
        check
        for check in cast("list[Row]", verdict_moves.review_checks(conn, row))  # pyright: ignore[reportUnknownMemberType]
        if cast("Row", check.get("check_step") or {}).get("id") == requirement["pipeline_stage_id"]
    ]
    if not checks:
        return "none"
    states: set[str] = set()
    for link in prs:
        url = str(link.get("url") or "")
        # A check that names a pull request counts only for that one; a check naming none counts for every head.
        mine = [check for check in checks if not pr_identity.references(check) or verdict_moves.for_pr(check, url)]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]
        verdict: str | None = verdict_moves.verdict_at(mine, _head(link).lower())  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportArgumentType]
        if not verdict:
            states.add("waiting")
        else:
            states.add("met" if verdict in ("approve", "pass") else "failed")
    if "failed" in states:
        return "failed"
    return "waiting" if "waiting" in states else "met"


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


def verdict_binds_attachment(decision: Row, attachment: Row | None) -> bool:
    """The one verdict-binding rule, shared by the engine and the review reader: a verdict on one attachment (an approval or
    a judge's) stands only while that attachment is the one still attached. An override names no attachment, so it stands
    on the work version alone (its freshness is checked by the caller, as for any met)."""
    if decision.get("verdict") == "override" or not decision.get("requirement_attachment_id"):
        return True
    return attachment is not None and str(attachment["id"]) == str(decision["requirement_attachment_id"])


def _rule_source(conn: sqlite3.Connection, task_id: str, requirement: Row, facts: _Facts, task: Row) -> str:
    """The state a field or run requirement's source gives the ticket now, as its rule reads it."""
    if requirement["kind"] == "field":
        return _field_state(task, requirement, facts)
    return _run_state(task_id, requirement, facts, conn)


def _standing(conn: sqlite3.Connection, task_id: str, requirement: Row, facts: _Facts) -> Standing:
    task = facts.tasks.get(task_id) or {"id": task_id, "work_revision": 0}
    revision = int(task.get("work_revision") or 0)
    key = (task_id, str(requirement["id"]))
    decision, attachment = facts.decisions.get(key), facts.attachments.get(key)
    if decision is not None and not verdict_binds_attachment(decision, attachment):
        decision = None  # the evidence it judged was replaced or taken away
    if decision is not None and requirement["kind"] == "approval" and decision["verdict"] == "override":
        decision = None  # no override stands in for a named person's approval (D10, D34)
    if decision is not None and requirement["kind"] in ("field", "run") and decision["verdict"] == "met":
        # A person's approval of a field or run stands only while its source is still true: a date cleared after it was
        # approved, or a run that stopped passing, is not met (the requirement waits on the rule again).
        if _rule_source(conn, task_id, requirement, facts, task) != "met":
            decision = None
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
    if kind == "approval":
        return Standing(requirement, "waiting")  # an approval waits on its person
    source = (
        _field_state(task, requirement, facts) if kind == "field" else _run_state(task_id, requirement, facts, conn)
    )
    # A field or run is a rule (D60): no judge reads it, so only a person's review waits on the source. A bot or
    # bot-then-person review of one (rows from before the API refused it) reads as the rule itself, so no card waits
    # for a reviewer nobody can reach.
    if source == "met" and requirement["reviewed_by"] == "person":
        return Standing(requirement, "waiting")  # the source is ready; the named person still decides
    return Standing(requirement, source)


def applies(requirement: Row, labels: set[str]) -> bool:
    """Whether a requirement applies to a ticket with these label keys (a requirement with applies_to_label only applies to
    tickets carrying that label). The one rule every reader uses: the engine, the queue, the judges and the stage check."""
    label = requirement.get("applies_to_label")
    return not label or str(label) in labels


def _pipelines_of(conn: sqlite3.Connection, stage_ids: list[str]) -> list[str]:
    """The pipelines that own these stages, one query for the whole list."""
    if not stage_ids:
        return []
    return [
        str(r["type_id"])
        for r in _rows(
            conn,
            "SELECT DISTINCT type_id FROM task_steps WHERE id IN (SELECT value FROM json_each(?)) AND type_id IS NOT NULL",
            (json.dumps(sorted(set(stage_ids))),),
        )
    ]


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
            if not applies(requirement, facts.labels.get(task_id, set())):
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
    visited = _visited(conn, _ids(rows))
    seen = [stage_id for left in visited.values() for stage_id in left]
    pipelines = list(dict.fromkeys([str(r["type_id"]) for r in rows] + _pipelines_of(conn, seen)))
    by_stage = _requirements(conn, pipelines)
    if not by_stage:
        return {}
    return _evaluate(conn, _scopes(rows, visited), by_stage)


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
    conn: sqlite3.Connection,
    actor: str,
    row: Row,
    type_id: str | None,
    step_id: str | None,
    ts: str,
    *,
    closing: bool = False,
) -> None:
    """After a ticket's step changed and its step event is written (`row` is the ticket as it was): close its visit
    and open one at the new stage, and log a move back. A ticket in a pipeline with no requirements and no open visit is
    left alone, so such a pipeline writes nothing."""
    task_id = str(row.get("id") or "")
    if not task_id:
        return
    if step_id:
        target = stage(conn, step_id)
        if closing:
            _mark_stage(conn, actor, task_id, CLOSED_INTO, step_id, ts)  # a Close or a Decline cancelled the ticket
        elif target is not None and target["status"] in ("done", "closed"):
            _mark_stage(conn, actor, task_id, FINISHED_INTO, step_id, ts)  # a move finished it into a done stage
    current = open_visit(conn, task_id)
    _leave_spawn_stage(conn, row, type_id, step_id)
    _move_visit(conn, actor, row, type_id, step_id, ts, current)
    target = stage(conn, step_id)
    if target is not None and target["status"] == "declined" and row.get("status") != "declined":
        _decline_children(conn, actor, row, type_id)
    _settle_parent(conn, task_id)


def _move_visit(
    conn: sqlite3.Connection,
    actor: str,
    row: Row,
    type_id: str | None,
    step_id: str | None,
    ts: str,
    current: Row | None,
) -> None:
    """The stage visit side of a step change. Runs before the parent settles, so a subtask that has just reached its
    pipeline's output stage counts as shipped."""
    task_id = str(row.get("id") or "")
    old_type: str | None = row.get("type_id")
    old_step: str | None = row.get("step_id")
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


def _leave_spawn_stage(conn: sqlite3.Connection, row: Row, type_id: str | None, step_id: str | None) -> None:
    """A card that moves forward out of a stage with a spawn link files its spawned ticket (pipeline_links.spawn), once
    per card while that handoff is live. A move back, a move into a closing stage or a move to another pipeline files
    nothing."""
    old_step = row.get("step_id")
    if not old_step or not step_id or not type_id or step_id == old_step:
        return
    from . import pipeline_links

    link = pipeline_links.link_for_stage(conn, str(old_step))
    if link is None or link["mode"] != "spawn":
        return
    before, after = stage(conn, str(old_step)), stage(conn, step_id)
    if before is None or after is None or after["type_id"] != before["type_id"]:
        return
    if after["position"] <= before["position"] or after["status"] in CLOSED:
        return
    pipeline_links.spawn(conn, link, str(row.get("id")))


def _settle_parent(conn: sqlite3.Connection, child_id: str) -> None:
    """A subtask changed stage: a parent whose stage waits on its subtasks (a run from `subtasks`) is checked now. When
    every requirement of that stage is met the parent moves on, as Tico (the rule decides; nobody passes it). A parent
    with a requirement not met stays where it is, and the pipeline manager sees the failed or waiting standing."""
    parent_id = H.TR.parent_of(conn, child_id)
    if not parent_id:
        return
    parent = _one(conn, "SELECT id, type_id, step_id FROM tasks WHERE id = ?", (parent_id,))
    if parent is None or not parent["step_id"]:
        return
    by_stage = _requirements(conn, [str(parent["type_id"])])
    stage_id = str(parent["step_id"])
    waiting = by_stage.get(stage_id, [])
    if not any(r["kind"] == "run" and r["needs"].get("source") == "subtasks" for r in waiting):
        return
    found = _evaluate(conn, {str(parent_id): {stage_id: None}}, by_stage).get(str(parent_id), [])
    if found and all(s.state == "met" for s in found):
        _advance_parent(conn, str(parent_id))


def _advance_parent(conn: sqlite3.Connection, parent_id: str) -> None:
    """Moves a parent on because its subtasks finished, in a savepoint of its own. A refusal (a requirement, or a spawn the
    rules could not file) rolls back only the parent's move: the subtask's change that settled it stands, and the refusal is
    logged on the parent (`auto_move_refused`) with its reason."""
    from .store import Problem

    conn.execute("SAVEPOINT settle_parent")
    try:
        result = advance(conn, H.KEEPER, parent_id)
    except (H.Refused, Problem) as exc:
        conn.execute("ROLLBACK TO settle_parent")
        conn.execute("RELEASE settle_parent")
        reason = str(cast("object", getattr(exc, "detail", None)) or exc)
    else:
        conn.execute("RELEASE settle_parent")
        if result["moved"] or not result["reason"]:
            return
        reason = str(result["reason"])  # advance already undid its own move and says why it stayed
    H._task_event(conn, parent_id, H.KEEPER, "auto_move_refused", None, None, reason)  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------------------------- decline
DECLINE_NOTE = "Parent declined"


def _spawned_open(conn: sqlite3.Connection, card: Row) -> list[Row]:
    """The open subtasks of a card that its pipeline spawned into (the Engineering tickets of a Product card)."""
    targets = [
        str(r["to_pipeline_id"])
        for r in _rows(
            conn,
            "SELECT to_pipeline_id FROM pipeline_links WHERE from_pipeline_id = ? AND mode = 'spawn' AND retired_at IS NULL",
            (str(card.get("type_id") or ""),),
        )
    ]
    if not targets:
        return []
    kids = [H.task(conn, kid_id) for kid_id in H.TR.child_ids(conn, str(card["id"]))]
    return [k for k in kids if k and str(k.get("type_id") or "") in targets and k["status"] in H.ACTIVE_STATUSES]


def _decline_preflight(
    conn: sqlite3.Connection, actor: str, row: Row, type_id: str | None, step_id: str | None
) -> None:
    """A card moving into a declined stage must be closable with its open subtasks by this actor, as a close cascade would
    need. Refuses before anything is written (D66)."""
    target = stage(conn, step_id)
    if target is None or target["status"] != "declined" or row.get("status") == "declined":
        return
    for kid in _spawned_open(conn, {**row, "type_id": type_id or row.get("type_id")}):
        H._check_task_close(conn, actor, kid, DECLINE_NOTE, None)  # pyright: ignore[reportPrivateUsage]


def _decline_children(conn: sqlite3.Connection, actor: str, row: Row, type_id: str | None) -> None:
    """A card was declined: its open spawned subtasks close with it, noted "Parent declined", each with its own open
    descendants. Subtasks already done stay done. Same transaction as the decline (called after its write)."""
    card = {**row, "type_id": type_id or row.get("type_id")}
    for kid in _spawned_open(conn, card):
        H.task_close(conn, actor, str(kid["id"]), note=DECLINE_NOTE, with_parent=str(row["id"]), strict=True)
        H._finish_children(conn, actor, H.task(conn, str(kid["id"])), None, False, strict=True)  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------------------------- the move gate
def _unmet(found: list[Standing]) -> list[Standing]:
    """What stops a forward move: every requirement that is not met, except one the review gate still decides."""
    return [s for s in found if s.state != "met" and not s.requirement.get("enforced_by")]


def _forward_scope(conn: sqlite3.Connection, task_id: str, here: Row, target: Row | None) -> dict[str, str | None]:
    """The stages a forward move to `target` is held to: where the ticket is, the ones it passes through on the way, and
    every stage it visited and left (of any pipeline it passed through) that is not at or beyond the target. A stage it
    was moved back from, at or after the target, is not in the way. Visits are never skipped: a loop back to a stage it has
    been in is held to the same requirements as any other move, so a stale earlier requirement still blocks."""
    scope: dict[str, str | None] = {}
    same = target is not None and target["type_id"] == here["type_id"]
    seen = _visited(conn, [task_id]).get(task_id, {})
    for stage_id, left_at in seen.items():
        placed = stage(conn, stage_id)
        if placed is None:
            continue
        if (
            same
            and target is not None
            and placed["type_id"] == target["type_id"]
            and placed["position"] >= target["position"]
        ):
            continue
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


OVERRIDE_REASON = "Moved on without it"
# stage_decisions.reason takes at most 500 bytes (a CHECK in the table), and a note is capped at 500 characters.
NOTE_MAX_CHARS = 500
REASON_MAX_BYTES = 500


def override_reason(note: str | None) -> str:
    """The reason an override is logged with: the fixed text, then the person's note when they gave one. The whole reason
    is cut to the bytes the table takes, so a long note with accents or emoji cannot fail the write."""
    text = str(note or "").strip()[:NOTE_MAX_CHARS]
    if not text:
        return OVERRIDE_REASON
    return f"{OVERRIDE_REASON}: {text}".encode()[:REASON_MAX_BYTES].decode(errors="ignore")


# The actor whose decision prescribes the move now being made (a named reviewer's "Not validated"). Only that actor's backward
# move skips the rule-editor test; the reviewer is still recorded as the actor of the move.
_DECIDED_MOVE: ContextVar[str | None] = ContextVar("pipeline_engine_decided_move", default=None)


@contextmanager
def decided_move(actor: str) -> "Generator[None, None, None]":
    """Runs the moves inside it as a decision the actor already made (its authority is checked by the caller)."""
    token = _DECIDED_MOVE.set(actor)
    try:
        yield
    finally:
        _DECIDED_MOVE.reset(token)


def is_cancellation(status: str | None, *, explicit_step: bool = False) -> bool:
    """The one test for "a cancellation, not progress", decided by intent and not by the stage's status. A decline (status
    declined) is a cancellation, and so is a Close (status closed with no step of its own). A step move into a stage whose
    status is closed is forward progress: Engineering's Done is such a stage, so it meets every requirement. Both gates ask
    this (hubdb.task_update's move gate and hubdb._set_status's status gate), and the close marker that shipment accounting
    reads is written from the same answer. Who may cancel is still checked by the close and decline paths."""
    if status == "declined":
        return True
    return status == "closed" and not explicit_step


def completion_target(
    conn: sqlite3.Connection,
    row: Row,
    type_id: str | None,
    step_id: str | None,
    effective: str | None,
    *,
    explicit_step: bool,
) -> str | None:
    """The stage a write is held to as progress, or None when it is not progress. A move to a stage is held to that stage.
    A status done with no step of its own (booted Engineering's Done is closed, so `status=done` resolves to none) is a
    completion, and it is held as a move into the pipeline's completion stage (pipeline_setup.completion_stage). Cancellations
    (a Close with no step, a Decline) and every other status write are not held here."""
    if is_cancellation(effective, explicit_step=explicit_step):
        return None
    if step_id:
        return step_id
    if effective != "done" or row.get("status") == "done" or not type_id:
        return None
    from .pipeline_setup import completion_stage

    return completion_stage(conn, type_id)


def held_count(conn: sqlite3.Connection, step_id: str, task_id: str = "") -> int:
    """How many open cards a stage holds, not counting the ticket that asks (a finished card holds no place)."""
    row = _one(
        conn,
        "SELECT COUNT(*) AS n FROM tasks WHERE step_id = ? AND id <> ? AND status NOT IN ('done', 'closed', 'declined')",
        (step_id, task_id),
    )
    return int(row["n"]) if row else 0


def held_by_stage(conn: sqlite3.Connection, type_id: str) -> dict[str, int]:
    """Every stage of a pipeline with the open cards it holds, as held_count counts them: a snoozed or hidden card holds its
    place too. The board shows a capped stage's count from this (D68), not from the cards it has loaded."""
    return {
        str(r["step_id"]): int(r["n"])
        for r in _rows(
            conn,
            "SELECT step_id, COUNT(*) AS n FROM tasks WHERE step_id IN (SELECT id FROM task_steps WHERE type_id = ?) "
            "AND status NOT IN ('done', 'closed', 'declined') GROUP BY step_id",
            (type_id,),
        )
    }


def add_held(conn: sqlite3.Connection, typ: dict[str, Any]) -> None:
    """Each step of a pipeline view gets `held`: its open cards (held_by_stage), so a capped stage's count is the one caps are
    enforced against."""
    held = held_by_stage(conn, str(typ["id"]))
    steps: list[dict[str, Any]] = typ.get("steps") or []
    for step in steps:
        step["held"] = held.get(str(step["id"]), 0)


def full_stage(conn: sqlite3.Connection, step_id: str | None, task_id: str = "") -> Row | None:
    """The stage with its cap and its count (`held`) when it can take no more cards (D68), else None. A stage with no cap is
    never full; a cap of 0 (On ice, D69) is always full."""
    target = stage(conn, step_id)
    if target is None or target["card_cap"] is None:
        return None
    held = held_count(conn, str(target["id"]), task_id)
    return {**target, "held": held} if held >= int(target["card_cap"]) else None


def default_step(conn: sqlite3.Connection, steps: list[Row], status: str, task_id: str = "") -> Row | None:
    """The stage a ticket lands in when a write names only its status (D69): the first stage of that status with room that is
    not On ice (cap 0), else the first such stage even when it is full (the cap then refuses a bot as for any move), else the
    first stage of that status. A stage with no cap always has room, so an uncapped pipeline keeps its first stage."""
    of_status = [s for s in steps if s["status"] == status]
    ordinary = [s for s in of_status if s.get("card_cap") != 0]
    roomy = [
        s for s in ordinary if s.get("card_cap") is None or held_count(conn, str(s["id"]), task_id) < int(s["card_cap"])
    ]
    return next(iter(roomy or ordinary or of_status), None)


def room_refusal(rule: str, detail: str, task_id: str, step_id: str) -> Exception:
    """The refusal a stage cap raises (rule "stage_full" or "on_ice"). Its context names the stage, so the pipeline manager
    can list the ticket as waiting for room (pipeline_manager). Raised, not recorded: the write that asked is rolled back,
    and whoever catches it records it (store.refused for a request, advance for a link's move)."""
    exc: Any = H.Refused(rule, detail, task=task_id)
    exc.context = {"stage": step_id}
    return cast("Exception", exc)


def full_detail(full: Row) -> str:
    """What a full stage says: "Specifying is full (3 of 3)"; On ice takes no card from a bot."""
    if int(full["card_cap"]) == 0:
        return f"{full['name']} takes no cards from a bot or automation"
    return f"{full['name']} is full ({full['held']} of {full['card_cap']})"


def _room(conn: sqlite3.Connection, actor: str, task_id: str, here: Row | None, target: Row | None) -> bool:
    """The stage caps (D68, D69) on one move: a bot or automation may not move a card out of On ice (cap 0), nor into a
    full stage. A person may move a card into a full stage; the answer is True, so the move is an over-cap one and the app
    says so. Returns False for a stage with room."""
    if target is None:
        return False
    # Delegated writes are automated too: BotOps or the Assistant writing for a person (H.VIA) gets no person's override.
    automated = not H.is_human(actor) or bool(H.VIA.get())
    if automated and here is not None and here["card_cap"] == 0:
        raise room_refusal("on_ice", f"Only a person moves a card out of {here['name']}", task_id, str(here["id"]))
    full = full_stage(conn, str(target["id"]), task_id)
    if full is None:
        return False
    if automated:
        raise room_refusal("stage_full", full_detail(full), task_id, str(target["id"]))
    return True


def check_move(
    conn: sqlite3.Connection,
    actor: str,
    row: Row,
    step_id: str | None,
    override: bool = False,
    override_note: str | None = None,
) -> bool:
    """Before a ticket changes to `step_id`: refuse the move (rule "requirement") when the requirements that count do not
    allow it, unless a rule editor overrides them (logged). A pipeline with no requirements allows every move. A stage's
    cap is checked first (_room): a bot or automation cannot move into a full stage, and a person's move into one stands
    and returns True (`over_cap`).

    Forward, every requirement that counts must be met: the stage the ticket is at, the stages it passes through and the
    ones it visited (in any pipeline it has been in). A rule editor may move it past them with `override`, which writes one
    `override` decision for each requirement it passed, its reason the editor's `override_note` (override_reason). A
    protected approval (D10, D34) is never overridden: its named person has to approve it. Backward, only a rule editor and
    Tico itself (the legacy flows move as Tico) may move a ticket; the move is logged as `stage_move_back` by
    `on_step_change`."""
    task_id = str(row.get("id") or "")
    if not step_id or not task_id:
        return False
    now = _one(conn, "SELECT type_id, step_id FROM tasks WHERE id = ?", (task_id,))
    if now is None or step_id == now["step_id"]:
        return False
    here, target = stage(conn, str(now["step_id"]) if now["step_id"] else None), stage(conn, step_id)
    over = _room(conn, actor, task_id, here, target)
    if here is None:
        return over
    pipeline_id = str(now["type_id"])
    editor = pipeline_rights.may_edit_rules(conn, actor, pipeline_id)
    if target is not None and target["type_id"] == here["type_id"] and target["position"] < here["position"]:
        if (
            has_requirements(conn, pipeline_id) and actor != H.KEEPER and not editor and _DECIDED_MOVE.get() != actor
        ):  # the off switch: no rules, no gate
            H.refuse(
                conn,
                actor,
                "requirement",
                "Only the pipeline's owner or an Admin moves a ticket back a stage",
                task=task_id,
            )
        return over
    scope = _forward_scope(conn, task_id, here, target)
    _hold(conn, actor, task_id, pipeline_id, f"Can't leave {here['name']} yet", scope, override, override_note)
    return over


def may_clear_stage(conn: sqlite3.Connection, actor: str, row: Row) -> None:
    """Taking a ticket out of its stage without putting it in another (step "") is a person's move (D69): a bot or
    automation may not do it, so a card On ice cannot slip out of it, nor a stage's requirements be skipped. Tico itself
    (the keeper's legacy flows) and a person may."""
    if not row.get("step_id") or actor == H.KEEPER or (H.is_human(actor) and not H.VIA.get()):
        return
    H.refuse(
        conn,
        actor,
        "stage_required",
        "A ticket leaves its stage only for another stage; a person takes it out",
        task=str(row.get("id") or ""),
    )


def is_completion(row: Row, effective: str | None) -> bool:
    """Whether a write marks the ticket done now: a `done` status event, which ships it (_shipments)."""
    return effective == "done" and row.get("status") != "done"


def check_completion(
    conn: sqlite3.Connection, actor: str, row: Row, override: bool = False, override_note: str | None = None
) -> None:
    """A ticket marked done where it stands (no move: it already sits in its Done stage, for instance after a Close put it
    there, or its pipeline has no Done stage) is held to everything a finish is held to: its current stage and every stage
    it visited and left, as check_move holds a forward move. Without this, Close into Done then `status=done` shipped work
    whose approval was still waiting."""
    task_id = str(row.get("id") or "")
    now = _one(conn, "SELECT type_id, step_id FROM tasks WHERE id = ?", (task_id,)) if task_id else None
    if now is None:
        return
    here = stage(conn, str(now["step_id"])) if now["step_id"] else None
    scope: dict[str, str | None]
    if here is not None:
        scope = _forward_scope(conn, task_id, here, None)
    else:
        # No stage now (a Close on a pipeline with no Done stage clears it): the stages it visited still hold it.
        scope = dict(_visited(conn, [task_id]).get(task_id, {}))
    if not scope:
        return
    _hold(conn, actor, task_id, str(now["type_id"] or ""), "Can't mark it done yet", scope, override, override_note)


def _hold(
    conn: sqlite3.Connection,
    actor: str,
    task_id: str,
    pipeline_id: str,
    blocked: str,
    scope: dict[str, str | None],
    override: bool,
    override_note: str | None,
) -> None:
    """Refuse (rule "requirement") unless every requirement of `scope` is met, or a rule editor overrides the ones that
    are not (never a protected approval)."""
    by_stage = _requirements(conn, _pipelines_of(conn, list(scope)) or [pipeline_id])
    if not any(by_stage.get(stage_id) for stage_id in scope):
        return  # the off switch: nothing this move is held to has requirements
    unmet = _unmet(_evaluate(conn, {task_id: scope}, by_stage).get(task_id, []))
    if not unmet:
        return
    # An override passes each unmet requirement on its own pipeline's rules, so the actor must edit the rules of every
    # pipeline that owns one of them (a destination's owner cannot override the source's requirements).
    owners = {str(s.requirement["pipeline_id"]) for s in unmet}
    editable = all(pipeline_rights.may_edit_rules(conn, actor, owner) for owner in sorted(owners))
    if override and editable:
        protected = [s for s in unmet if s.requirement["kind"] == "approval"]
        if protected:
            H.refuse(
                conn,
                actor,
                "requirement",
                f"{blocked}: {_names(protected)}. A spec or release approval needs its named "
                "person, so an override can't stand in for it.",
                task=task_id,
            )
        _record_override(conn, actor, task_id, unmet, override_reason(override_note))
        return
    hint = "" if editable else " A pipeline editor can move it on anyway."
    H.refuse(conn, actor, "requirement", f"{blocked}: {_names(unmet)}.{hint}", task=task_id)


def check_forward(conn: sqlite3.Connection, actor: str, row: Row, type_id: str | None, step_id: str | None) -> None:
    """Every forward status write (hubdb._set_status, not only task_update) is held to the same gate: a move that goes
    forward a stage must pass `check_move`. A backward move is left to the legacy flows as before."""
    if not step_id or step_id == row.get("step_id"):
        return
    before, after = stage(conn, row.get("step_id")), stage(conn, step_id)
    if before is not None and after is not None and after["type_id"] == before["type_id"]:
        if after["position"] < before["position"]:
            # A backward move is left to the legacy flows, but not the caps: nothing automatic moves into a full stage or out of
            # On ice (_room), whichever way the card goes.
            _room(conn, actor, str(row.get("id") or ""), before, after)
            return
    _decline_preflight(conn, actor, row, type_id, step_id)
    check_move(conn, actor, {**row, "type_id": type_id}, step_id)


def counts_on_card(conn: sqlite3.Connection, task_id: str, requirement: Row) -> bool:
    """Whether a requirement is one the ticket is held to where it stands: one of its current stage, or one of a stage it
    has left, as long as the requirement existed when it left. Such a stale requirement can be attached to, judged and
    reviewed in place, without moving the ticket (D33)."""
    task = _one(conn, "SELECT step_id FROM tasks WHERE id = ?", (task_id,))
    if task is None:
        return False
    stage_id = str(requirement["pipeline_stage_id"])
    if task["step_id"] == stage_id:
        return True
    left = _one(
        conn,
        "SELECT max(left_at) AS left_at FROM stage_visits WHERE task_id = ? AND pipeline_stage_id = ? AND left_at IS NOT NULL",
        (task_id, stage_id),
    )
    return bool(left and left["left_at"] and str(requirement["created_at"]) <= str(left["left_at"]))


def approval_problem(
    conn: sqlite3.Connection, task_id: str, requirement: Row, attachment: Row | None
) -> tuple[str, str] | None:
    """What stops a person's approval of this requirement from standing now, as (code, message), or None. The rule's own
    prerequisites are checked in the same write as the approval: an attachment must be current (attached at the work's
    current version, when freshness is on) and in a format the rule takes; a field must be set; a run's source must have
    passed. A person approves what is true now, never what the queue showed before it changed."""
    task = _one(conn, "SELECT id, work_revision FROM tasks WHERE id = ?", (task_id,))
    if task is None:
        return ("not_found", "No such ticket")
    requirement = {**requirement, "needs": _json(requirement.get("needs"))}
    kind = str(requirement["kind"])
    if kind == "attachment":
        if attachment is None:
            return ("nothing_to_approve", "Nothing is attached to approve")
        if _fresh_needed(requirement) and int(attachment["work_version"]) != int(task["work_revision"] or 0):
            return ("stale", "The attachment was attached before the last change")
        formats: list[str] = requirement["needs"].get("formats") or []
        if formats:
            shown = requirement_attachments.views(conn, [attachment])[0]["format"]
            if shown not in formats:
                return ("not_ready", f"This requirement takes {', '.join(formats)}, not {shown}")
        return None
    if kind in ("field", "run"):
        facts = _facts(conn, [task_id], [], {kind})
        state = (
            _field_state(facts.tasks[task_id], requirement, facts)
            if kind == "field"
            else _run_state(task_id, requirement, facts, conn)
        )
        if state != "met":
            return ("not_ready", f"It is {STATE_WORDS.get(state, state)}: the source has not passed yet")
    return None


def _record_override(conn: sqlite3.Connection, actor: str, task_id: str, unmet: list[Standing], reason: str) -> None:
    """A rule editor moved a ticket past requirements that were not met: one `override` decision for each, bound to the work
    as it is now, its reason as the editor gave it, and one line in the ticket's history."""
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
                reason,
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
        if exc.rule == "stage_full":
            record_waiting(conn, actor, task_id, exc)
        return {**none, "reason": str(exc.detail or exc)}
    conn.execute("RELEASE pipeline_advance")
    return {
        "moved": True,
        "to": target["id"],
        "pipeline": target["type_id"],
        "handoff": link["id"] if link else None,
        "reason": "",
    }


def record_waiting(conn: sqlite3.Connection, actor: str, task_id: str, exc: hubdb.Refused) -> None:
    """A move that waited for room (a link's, or an automatic one: hubdb.try_auto_move) is recorded after its own write is
    undone, so the refusal outlives the rollback: the pipeline manager lists the ticket as waiting for room
    (pipeline_manager). Nothing is raised."""
    try:
        H.refuse(conn, actor, exc.rule, exc.detail, task=task_id, context=getattr(exc, "context", None))
    except H.Refused:
        pass


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
    conn.execute("SAVEPOINT queue_ejected")
    try:
        H.task_update(conn, H.KEEPER, task_id, step=before["id"])
    except H.Refused:  # the stage it goes back to is full, or the card is On ice (_room): it stays, and says so
        conn.execute("ROLLBACK TO queue_ejected")
        conn.execute("RELEASE queue_ejected")
        return False
    conn.execute("RELEASE queue_ejected")
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
    found = standings(conn, [row]).get(task_id, [])
    shown = _attachments_by_requirement(conn, task_id)
    for s in found:
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
                "attachments": shown.get(s.id, []),
            }
        )
    return out


def _attachments_by_requirement(conn: sqlite3.Connection, task_id: str) -> dict[str, list[dict[str, Any]]]:
    """The newest attachments of each requirement on one ticket, capped per requirement, as read views. One query and
    one media lookup for the whole ticket, so the ticket page needs no request per requirement."""
    rows = _rows(
        conn,
        "SELECT id, stage_requirement_id, source, blob_id, file_id, file_version, url, digest, work_version, attached_by, "
        "created_at FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY stage_requirement_id "
        "ORDER BY created_at DESC, id DESC) AS n FROM requirement_attachments WHERE task_id = ? AND removed_at IS NULL) "
        "WHERE n <= ? ORDER BY created_at DESC, id DESC",
        (task_id, requirement_attachments.ATTACH_LIMIT),
    )
    kept: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        kept.setdefault(str(row["stage_requirement_id"]), []).append(row)
    shown = requirement_attachments.views(conn, [r for bucket in kept.values() for r in bucket])
    by_id = {str(r["id"]): r for r in shown}
    return {req: [by_id[str(r["id"])] for r in bucket] for req, bucket in kept.items()}


# ---------------------------------------------------------------------------------------------- boot
SEEDED = "pipeline_engine.seeded"
LATE_MINUTES = 240


def seed_type(c: sqlite3.Connection, type_id: str) -> None:
    """Give one type's steps their default Warn after, once. Boot does it for every type; a type created later (its role
    set) does it when it is made, so an engineering type never waits for a restart to be late by the default. An event
    records it, so a limit a person clears stays cleared."""
    if c.execute("SELECT 1 FROM events WHERE action = ? AND target = ? LIMIT 1", (SEEDED, type_id)).fetchone():
        return
    role = c.execute("SELECT role FROM task_types WHERE id = ?", (type_id,)).fetchone()
    role = role[0] if role else None
    has_flow = any(r[1] == "flow" for r in c.execute("PRAGMA table_info(task_steps)"))  # step_flow adds it at boot
    for step_id, status, flow in c.execute(
        f"SELECT id, status, {'flow' if has_flow else 'NULL'} FROM task_steps WHERE type_id = ? AND limit_minutes IS NULL",
        (type_id,),
    ).fetchall():
        hours = _json(flow).get("late_hours")
        if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
            minutes: int | None = round(hours * 60)
        else:
            minutes = LATE_MINUTES if role == "engineering" and status == "doing" else None
        if minutes:
            c.execute("UPDATE task_steps SET limit_minutes = ? WHERE id = ?", (minutes, step_id))
    H.event(c, H.KEEPER, SEEDED, type_id, {})


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
    for (type_id,) in c.execute("SELECT id FROM task_types").fetchall():
        seed_type(c, type_id)
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


def task_standings(conn: sqlite3.Connection, task_ids: list[str]) -> dict[str, dict[str, str]]:
    """{task id: {requirement id: the engine's state}} for every requirement that counts on each ticket: those of its current
    stage and of the stages it has left. The review queue, Approve, Mark met and the GitHub checks all read a requirement's
    standing from here. Unlike `standings`, a closed ticket is answered too, so its checks keep the engine's state."""
    ids = list(dict.fromkeys(str(t) for t in task_ids if t))
    rows: list[Row] = []
    for part in _chunks(ids):
        rows.extend(_rows(conn, f"SELECT id, type_id, step_id, status FROM tasks WHERE id IN ({_marks(part)})", part))
    visited = _visited(conn, [str(r["id"]) for r in rows])
    seen = [stage_id for left in visited.values() for stage_id in left]
    pipelines = list(dict.fromkeys([str(r["type_id"]) for r in rows if r["type_id"]] + _pipelines_of(conn, seen)))
    by_stage = _requirements(conn, pipelines)
    found = _evaluate(conn, _scopes(rows, visited), by_stage) if by_stage else {}
    return {task_id: {standing.id: standing.state for standing in found.get(task_id, [])} for task_id in ids}
