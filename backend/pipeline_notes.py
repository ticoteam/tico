# pyright: strict
"""Pipeline and stage notes: what the team learned on a pipeline or one of its stages, each with what we tried, when and
why, served to the bot doing the work in its first turn there (runner-rs/src/prompt.rs, the Notes parts).

- Two levels: a pipeline's notes (`stage_id` NULL) and a stage's. No line cap: a list is as long as it needs to be.
- Who changes them: a person who may change the pipeline (pipeline_settings.may_change) adds, edits and retires notes
  at any time, and every edit or retire keeps a line on what changed and why (`pipeline_note_changes`). A bot only
  proposes one, with its why and its evidence; the pipeline's manager (or such a person) accepts or declines it with a
  reason the proposer receives, and nobody decides their own proposal. A proposed or retired note reaches no turn.
- When: the claim hands the task's two lists (`for_task`) as text; the runner sends each once per session, again when
  its text changes or the session is new, and never in the system prompt, so the cached prefix stays the same.
- Guards: a note may name a guard (`GUARDS`). That guard's refusal then quotes the note's why (`guard_why`).
"""

import json
import uuid
from datetime import date
from typing import Any

from . import hubdb as _hubdb
from . import pipeline_settings as _pipeline_settings
from .store import Problem

H: Any = _hubdb
pipeline_settings: Any = _pipeline_settings
Row = dict[str, Any]

STATES = ("active", "proposed", "declined", "retired")
NOTE_MAX = 1000
WHY_MAX = 1000
EVIDENCE_MAX = 500
LINE_MAX = 1000
# The guards whose refusal can quote a note: name -> the refusal's own why, said when no note names it.
GUARDS = {
    "approval_first": "one card per person per task; approvers got two cards for one decision.",
}
PROPOSE_HINT = "tico pipeline note propose"


def _id() -> str:
    return "n_" + uuid.uuid4().hex[:8]


def _pipeline(c: Any, pipeline_id: str) -> Row:
    row = c.execute("SELECT * FROM pipelines WHERE id=? AND retired_at IS NULL", (pipeline_id,)).fetchone()
    if not row:
        raise Problem("not_found", f"No such pipeline: {pipeline_id}", 404)
    return dict(row)


def _stage(c: Any, pipeline_id: str, stage_id: str | None) -> Row | None:
    """The stage, by id or name, when it is this pipeline's; None for the whole pipeline."""
    if not stage_id:
        return None
    row = c.execute(
        "SELECT * FROM stages WHERE pipeline_id=? AND retired_at IS NULL AND (id=? OR name=? COLLATE NOCASE) "
        "ORDER BY id=? DESC, position LIMIT 1",
        (pipeline_id, stage_id, stage_id, stage_id),
    ).fetchone()
    if not row:
        raise Problem("invalid", f"Not a stage of {pipeline_id}: {stage_id}", 422)
    return dict(row)


def get(c: Any, note_id: str) -> Row:
    row = c.execute("SELECT * FROM pipeline_notes WHERE id=?", (note_id,)).fetchone()
    if not row:
        raise Problem("not_found", f"No such note: {note_id}", 404)
    return dict(row)


def changes(c: Any, note_id: str) -> list[Row]:
    rows = c.execute(
        "SELECT by_id, kind, line, created_at FROM pipeline_note_changes WHERE note_id=? ORDER BY id", (note_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def may_change(c: Any, actor: str, pipeline: Row) -> bool:
    """A person who may change the pipeline adds, edits and retires its notes. A bot never does: it proposes."""
    return bool(H.is_human(actor)) and bool(pipeline_settings.may_change(c, actor, pipeline))


def may_decide(c: Any, actor: str, pipeline: Row, note: Row) -> bool:
    """Who accepts or declines a proposal: a person who may change the pipeline, or the pipeline's manager bot; never
    whoever proposed it."""
    if actor == note["created_by"]:
        return False
    if H.is_human(actor):
        return may_change(c, actor, pipeline)
    return bool(pipeline.get("manager_id")) and _same(c, actor, pipeline["manager_id"])


def _same(c: Any, a: str, b: str) -> bool:
    from . import pipeline_rights

    one = pipeline_rights.canonical(c, a)
    return bool(one) and one == pipeline_rights.canonical(c, b)


def _text(value: Any, name: str, limit: int, *, required: bool) -> str:
    out = " ".join(str(value or "").split())
    if required and not out:
        raise Problem("invalid", f"A note needs {name}.", 422)
    if len(out) > limit:
        raise Problem("invalid", f"{name.capitalize()} is over {limit} characters.", 422)
    return out


def _since(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return H.now()[:10]
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        raise Problem("invalid", "Since is a date: YYYY-MM-DD.", 422) from None


def _guard(value: Any) -> str:
    name = str(value or "").strip()
    if name and name not in GUARDS:
        raise Problem("invalid", f"No guard named {name}; one of: {', '.join(sorted(GUARDS))}.", 422)
    return name


def _fields(fields: Row, *, base: Row | None = None) -> Row:
    """The note's own fields, checked; with `base`, only those sent change it."""
    base = base or {}
    pick = {k: fields[k] if fields.get(k) is not None else base.get(k) for k in ("note", "why", "since", "evidence")}
    return {
        "note": _text(pick["note"], "a note", NOTE_MAX, required=True),
        "why": _text(pick["why"], "a why", WHY_MAX, required=True),
        "since": _since(pick["since"]),
        "evidence": _text(pick["evidence"], "evidence", EVIDENCE_MAX, required=False),
        "guard": _guard(fields["guard"] if fields.get("guard") is not None else base.get("guard")),
    }


def _next_position(c: Any, pipeline_id: str, stage_id: str | None) -> int:
    row = c.execute(
        "SELECT COALESCE(MAX(position), 0) FROM pipeline_notes WHERE pipeline_id=? AND stage_id IS ?",
        (pipeline_id, stage_id),
    ).fetchone()
    return int(row[0]) + 1


def _log(c: Any, note_id: str, by: str, kind: str, line: str, before: Row | None = None) -> None:
    c.execute(
        "INSERT INTO pipeline_note_changes (note_id, by_id, kind, line, before, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (note_id, by, kind, line[:LINE_MAX], json.dumps(before) if before else None, H.now()),
    )


def _line(value: Any, what: str) -> str:
    line = " ".join(str(value or "").split())
    if not line:
        raise Problem("invalid", f"Say in one line what changed and why ({what}).", 422)
    return line[:LINE_MAX]


def _insert(c: Any, by: str, pipeline: Row, stage: Row | None, data: Row, state: str) -> Row:
    ts, nid = H.now(), _id()
    stage_id = stage["id"] if stage else None
    c.execute(
        "INSERT INTO pipeline_notes (id, pipeline_id, stage_id, note, why, since, evidence, guard, state, position, "
        "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            nid,
            pipeline["id"],
            stage_id,
            data["note"],
            data["why"],
            data["since"],
            data["evidence"],
            data["guard"],
            state,
            _next_position(c, pipeline["id"], stage_id) if state == "active" else 0,
            by,
            ts,
            ts,
        ),
    )
    return get(c, nid)


def add(c: Any, by: str, pipeline_id: str, stage_id: str | None, fields: Row) -> Row:
    """A note in force, written by a person who may change the pipeline. A bot is told to propose instead."""
    pipeline = _pipeline(c, pipeline_id)
    stage = _stage(c, pipeline["id"], stage_id)
    _refuse_writer(c, by, pipeline)
    data = _fields(fields)
    note = _insert(c, by, pipeline, stage, data, "active")
    _log(c, note["id"], by, "added", str(fields.get("line") or "").strip() or f"Added: {data['why']}")
    return note


def propose(c: Any, by: str, pipeline_id: str, stage_id: str | None, fields: Row) -> Row:
    """A bot's proposed note: it needs its why and its evidence, reaches no turn, and wakes the pipeline's manager to
    accept or decline it."""
    pipeline = _pipeline(c, pipeline_id)
    stage = _stage(c, pipeline["id"], stage_id)
    missing = [name for name in ("why", "evidence") if not str(fields.get(name) or "").strip()]
    if missing:
        raise Problem(
            "invalid",
            f"A proposed note needs {' and '.join(missing)}: say why the rule should exist and what showed it "
            "(a task, a pull request, a run).",
            422,
        )
    data = _fields(fields)
    note = _insert(c, by, pipeline, stage, data, "proposed")
    _log(c, note["id"], by, "proposed", f"Proposed: {data['why']} Evidence: {data['evidence']}")
    _tell_decider(c, by, pipeline, stage, note)
    return note


def _refuse_writer(c: Any, by: str, pipeline: Row) -> None:
    if H.is_human(by) and may_change(c, by, pipeline):
        return
    if not H.is_human(by):
        raise Problem(
            "forbidden",
            f"A bot proposes a note; a person or the pipeline's manager accepts it: {PROPOSE_HINT} "
            f"{pipeline['id']} --note ... --why ... --evidence ...",
            403,
        )
    raise Problem("forbidden", "Only the pipeline's owner, its manager and Admins change its notes.", 403)


def edit(c: Any, by: str, note_id: str, fields: Row, line: str, revision: int | None = None) -> Row:
    """Change a note in force against the revision read; the line on what changed and why is required."""
    note = get(c, note_id)
    pipeline = _pipeline(c, note["pipeline_id"])
    _refuse_writer(c, by, pipeline)
    if note["state"] != "active":
        raise Problem("conflict", f"Only a note in force is edited; this one is {note['state']}.", 409)
    if revision is not None and revision != note["revision"]:
        raise Problem("conflict", "The note changed since you read it. Read it again.", 409)
    said = _line(line, "a required line under the note")
    data = _fields(fields, base=note)
    before = {k: note[k] for k in ("note", "why", "since", "evidence", "guard")}
    if data == before:
        raise Problem("invalid", "Nothing changed.", 422)
    c.execute(
        "UPDATE pipeline_notes SET note=?, why=?, since=?, evidence=?, guard=?, revision=revision+1, updated_at=? "
        "WHERE id=?",
        (data["note"], data["why"], data["since"], data["evidence"], data["guard"], H.now(), note_id),
    )
    _log(c, note_id, by, "edited", said, before)
    return get(c, note_id)


def retire(c: Any, by: str, note_id: str, line: str) -> Row:
    """Take a note out of force; it stays readable with its lines. The line on why is required."""
    note = get(c, note_id)
    _refuse_writer(c, by, _pipeline(c, note["pipeline_id"]))
    if note["state"] != "active":
        raise Problem("conflict", f"Only a note in force is retired; this one is {note['state']}.", 409)
    said = _line(line, "why it is retired")
    c.execute("UPDATE pipeline_notes SET state='retired', updated_at=? WHERE id=?", (H.now(), note_id))
    _log(c, note_id, by, "retired", said)
    return get(c, note_id)


def decide(c: Any, by: str, note_id: str, accept: bool, reason: str) -> Row:
    """Accept or decline a proposed note, with a reason the proposer receives."""
    note = get(c, note_id)
    pipeline = _pipeline(c, note["pipeline_id"])
    if note["state"] != "proposed":
        raise Problem("conflict", f"Only a proposed note is accepted or declined; this one is {note['state']}.", 409)
    if not may_decide(c, by, pipeline, note):
        if by == note["created_by"]:
            raise Problem(
                "forbidden", "Nobody decides their own proposal: the pipeline's manager or a person does.", 403
            )
        raise Problem(
            "forbidden",
            "Only the pipeline's manager and the people who may change the pipeline accept or decline its notes; "
            f"to change one, propose it ({PROPOSE_HINT}).",
            403,
        )
    said = _line(reason, "the reason the proposer receives")
    if accept:
        c.execute(
            "UPDATE pipeline_notes SET state='active', position=?, updated_at=? WHERE id=?",
            (_next_position(c, note["pipeline_id"], note["stage_id"]), H.now(), note_id),
        )
    else:
        c.execute("UPDATE pipeline_notes SET state='declined', updated_at=? WHERE id=?", (H.now(), note_id))
    _log(c, note_id, by, "accepted" if accept else "declined", said)
    _tell_proposer(c, by, pipeline, note, accept, said)
    return get(c, note_id)


def _where(c: Any, pipeline: Row, stage: Row | None) -> str:
    return f"{pipeline['name']} / {stage['name']}" if stage else str(pipeline["name"])


def _post(c: Any, sender: str, to: str, text: str, *, wake: bool) -> None:
    from . import threads as _threads

    threads: Any = _threads
    room = threads.dm(c, sender, [to])
    threads.post(c, sender, room["id"], text, refs={"needs_action": True} if wake and H.is_bot(sender) else None)


def _tell_decider(c: Any, by: str, pipeline: Row, stage: Row | None, note: Row) -> None:
    """Wake the pipeline's manager about a proposal, or its owner when the manager proposed it; neither: it waits in
    the pipeline's editor."""
    manager = str(pipeline.get("manager_id") or "")
    to = manager if manager and not _same(c, by, manager) else str(pipeline.get("owner_id") or "")
    if not to or _same(c, by, to):
        return
    text = (
        f"Proposed note for {_where(c, pipeline, stage)} ({note['id']}): {note['note']}\n"
        f"Why: {note['why']}\nEvidence: {note['evidence']}\n"
        f"Accept or decline it with a reason: tico pipeline note accept {note['id']} -m \"<why>\" "
        f"(or decline)."
    )
    _post(c, by, to, text, wake=True)


def _tell_proposer(c: Any, by: str, pipeline: Row, note: Row, accept: bool, reason: str) -> None:
    to = str(note["created_by"])
    if not to or _same(c, by, to):
        return
    verb = "Accepted" if accept else "Declined"
    _post(c, by, to, f"{verb} your note for {pipeline['name']} ({note['id']}): {note['note']}\n{reason}", wake=False)


def listing(c: Any, pipeline_id: str, *, states: tuple[str, ...] = ("active",)) -> Row:
    """A pipeline's notes in `states`: its own and each stage's, in order, with each note's change lines."""
    pipeline = _pipeline(c, pipeline_id)
    marks = ",".join("?" * len(states))
    rows = [
        dict(r)
        for r in c.execute(
            f"SELECT * FROM pipeline_notes WHERE pipeline_id=? AND state IN ({marks}) ORDER BY position, created_at",
            (pipeline["id"], *states),
        ).fetchall()
    ]
    for row in rows:
        row["changes"] = changes(c, row["id"])
    stages = c.execute(
        "SELECT id, name FROM stages WHERE pipeline_id=? AND retired_at IS NULL ORDER BY position, id",
        (pipeline["id"],),
    ).fetchall()
    return {
        "pipeline": {"id": pipeline["id"], "name": pipeline["name"]},
        "notes": [r for r in rows if not r["stage_id"]],
        "stages": [
            {"id": s["id"], "name": s["name"], "notes": [r for r in rows if r["stage_id"] == s["id"]]} for s in stages
        ],
    }


def _active(c: Any, pipeline_id: str, stage_id: str | None) -> list[Row]:
    return [
        dict(r)
        for r in c.execute(
            "SELECT * FROM pipeline_notes WHERE pipeline_id=? AND stage_id IS ? AND state='active' "
            "ORDER BY position, created_at",
            (pipeline_id, stage_id),
        ).fetchall()
    ]


def _bullet(note: Row) -> str:
    evidence = f" ({note['evidence']})" if note["evidence"] else ""
    out = f"- {note['note']} Why: {note['why'].rstrip('.')}. Since {note['since']}{evidence}."
    return out + " A guard refuses it." if note["guard"] else out


def render(title: str, notes: list[Row], propose: str) -> str:
    """One list as the turn reads it: a heading, a line per note with its why, since and evidence, and how to suggest
    a change."""
    if not notes:
        return ""
    lines = [f"Notes for {title} (what we learned; follow them):", *(_bullet(n) for n in notes)]
    lines.append(f"To suggest a change: {propose}")
    return "\n".join(lines)


def for_task(c: Any, task: Row) -> Row:
    """The task's two lists as the runner sends them: {"pipeline": text, "stage": text}, each left out when it has no
    note in force. Empty when the task is in no pipeline or neither has notes, so the turn reads as before."""
    pipeline_id = task.get("pipeline_id")
    if not pipeline_id:
        return {}
    pipeline = c.execute("SELECT id, name FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
    if not pipeline:
        return {}
    out: Row = {}
    hint = f"{PROPOSE_HINT} {pipeline['id']}"
    listed = render(
        str(pipeline["name"]),
        _active(c, pipeline_id, None),
        f'{hint} --note "<note>" --why "<why>" --evidence "<task or PR>"',
    )
    if listed:
        out["pipeline"] = listed
    stage_id = task.get("stage_id")
    if stage_id:
        stage = c.execute("SELECT id, name FROM stages WHERE id=?", (stage_id,)).fetchone()
        if stage:
            listed = render(
                str(stage["name"]),
                _active(c, pipeline_id, stage_id),
                f'{hint} --stage "{stage["name"]}" --note "<note>" --why "<why>" --evidence "<task or PR>"',
            )
            if listed:
                out["stage"] = listed
    return out


def guard_why(c: Any, task: Row | None, guard: str) -> str:
    """The why a guard's refusal adds: the active note that names it in the task's stage or pipeline (the stage's
    first), else the guard's own why. Empty for a guard with neither."""
    default = GUARDS.get(guard, "")
    if task and task.get("pipeline_id"):
        row = c.execute(
            "SELECT n.*, s.name AS stage_name, p.name AS pipeline_name FROM pipeline_notes n "
            "JOIN pipelines p ON p.id = n.pipeline_id LEFT JOIN stages s ON s.id = n.stage_id "
            "WHERE n.pipeline_id=? AND n.guard=? AND n.state='active' AND (n.stage_id IS NULL OR n.stage_id=?) "
            "ORDER BY n.stage_id IS NULL, n.position LIMIT 1",
            (task["pipeline_id"], guard, task.get("stage_id")),
        ).fetchone()
        if row:
            where = row["stage_name"] or row["pipeline_name"]
            evidence = f", {row['evidence']}" if row["evidence"] else ""
            return f"Why: {row['why'].rstrip('.')}. {where} note {row['id']} ({row['since']}{evidence})."
    return f"Why: {default}" if default else ""
