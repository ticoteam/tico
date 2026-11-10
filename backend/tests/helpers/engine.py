"""Rows for tests of the one task engine (backend/task_engine.py): a pipeline with its stages and requirements, a bot,
and a task put into a pipeline's first stage.

Until the cut-over `new_task` makes the row through `H.task_create` (so it has a conversation and a number like any
task) and then calls `task_engine.enter`. After it, `H.task_create` does both.
"""

import json
import re

from backend import bot_settings as BS
from backend import task_engine as E
from backend.store import H

ANA = "human:ana"
GENERAL = (
    {
        "name": "To do",
        "owner": "assignee",
        "expected_minutes": 1440,
        "expected_bots_only": 1,
        "requirements": [{"kind": "owner_done"}],
    },
)


def bot(c, slug):
    """An active bot, as the actor string."""
    if not H.bot(c, slug):
        H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"}}, {"people": []})
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, None, "ana"))
        BS.write(c, slug, {"name": slug, "runtime": "fake", "status": "active"})
    return H.bot_actor(slug)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def requirement(c, stage_id, kind, **fields):
    """A requirement on a stage. Returns its id."""
    rid = H.new_id()
    row = {
        "id": rid,
        "stage_id": stage_id,
        "kind": kind,
        "settings": json.dumps(fields.pop("settings", {})),
        "can_push_past": fields.pop("can_push_past", 1),
        "who_oks": fields.pop("who_oks", "none"),
        "person_id": fields.pop("person", None),
        "line": fields.pop("line", ""),
        "position": fields.pop("position", 0),
        "created_at": H.now(),
    }
    assert not fields, f"unknown requirement fields {sorted(fields)}"
    c.execute(
        f"INSERT INTO requirements ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )
    return rid


def pipeline(c, pid="general", name="General", stages=GENERAL, manager=None):
    """A pipeline with its work stages, then Done and Closed. Returns {"id", "stages": {name: id}, "requirements":
    {stage name: [ids]}}. Stage ids are `<pipeline id>-<stage name as a slug>`."""
    now = H.now()
    c.execute(
        "INSERT INTO pipelines (id, name, manager_id, created_at, updated_at) VALUES (?,?,?,?,?)",
        (pid, name, manager, now, now),
    )
    out = {"id": pid, "stages": {}, "requirements": {}}
    rows = [dict(s, kind="work") for s in stages] + [
        {"name": "Done", "kind": "done"},
        {"name": "Closed", "kind": "closed"},
    ]
    for position, spec in enumerate(rows):
        sid = f"{pid}-{_slug(spec['name'])}"
        c.execute(
            "INSERT INTO stages (id, pipeline_id, position, kind, name, owner, by_hand, task_limit, expected_minutes, "
            "expected_bots_only, on_enter, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                pid,
                position,
                spec["kind"],
                spec["name"],
                spec.get("owner"),
                1 if spec.get("by_hand") else 0,
                spec.get("task_limit"),
                spec.get("expected_minutes"),
                1 if spec.get("expected_bots_only") else 0,
                json.dumps(spec.get("on_enter") or {}),
                now,
                now,
            ),
        )
        out["stages"][spec["name"]] = sid
        out["requirements"][spec["name"]] = [
            requirement(c, sid, **dict(r, position=i)) for i, r in enumerate(spec.get("requirements") or ())
        ]
    return out


def settled(c):
    """Every wake so far has had its run: what is left queued is what a test adds."""
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")


def new_task(c, by, title, pipeline_id="general", assignee=None, parent=None):
    """A task in the first stage of `pipeline_id`, as `task_engine.task()` gives it."""
    row = H.task_create(c, by, title, "What to do and how to tell it is done.", assignee or by, lint=False)
    settled(c)
    return E.enter(c, row["id"], pipeline_id, by, assignee=assignee, parent=parent)


def say_done(c, row, by=None):
    """The owner says the stage's work is finished: one `done` answer on the stage's owner_done requirement. The task is
    not moved; call `task_engine.advance` for that."""
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
        (row["stage_id"],),
    ).fetchone()[0]
    return answer(c, row, rid, "done", by or row["owner_id"])


def answer(c, row, requirement_id, value, by, notes=None):
    aid = H.new_id()
    c.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, notes, created_at) VALUES (?,?,?,?,?,?,?)",
        (aid, row["visit"]["id"], requirement_id, by, value, notes, H.now()),
    )
    return aid


def wakes(c, task_id, actor=None):
    """The engine's wakes for a task, oldest first: (wake_key, recipient)."""
    rows = c.execute(
        "SELECT wake_key, recipient_id FROM messages WHERE wake_key LIKE ? ORDER BY created_at, id", (task_id + ":%",)
    ).fetchall()
    return [(r[0], r[1]) for r in rows if actor is None or r[1] == actor]


def guard_closed(c):
    return c.execute("SELECT open FROM task_move_guards WHERE id=1").fetchone()[0] == 0
