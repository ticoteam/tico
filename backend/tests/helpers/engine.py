"""Rows for tests of the one task engine (backend/task_engine.py): a pipeline with its stages and requirements, a bot,
and a task put into a pipeline's first stage.

`new_task` makes the task through `H.task_create`, as everything does: it has a conversation, and the engine has put
it into the pipeline's first stage.
"""

import json
import re

from backend import bot_settings as BS
from backend import pipeline_settings as PS
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


def bot(c, slug, computer=True):
    """An active bot on a computer, as the actor string. `computer=False` leaves it with nowhere to run."""
    if not H.bot(c, slug):
        H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"}}, {"people": []})
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES(?,?,?)", (slug, None, "ana"))
        BS.write(c, slug, {"name": slug, "runtime": "fake", "status": "active"})
        if computer:
            place(c, slug)
    return H.bot_actor(slug)


def place(c, slug):
    """Put a bot on the one test computer."""
    c.execute(
        "INSERT OR IGNORE INTO runners(id,label,owner_id,token_hash,created_at) VALUES('r1','box','ana','h',?)",
        (H.now(),),
    )
    c.execute("INSERT OR REPLACE INTO assignments VALUES(?,?,1,?,'human:ana')", (slug, "r1", H.now()))


def ran(c, task_id, actor, state="completed", at=None, runner="r1", comments=False):
    """The bot's queued wakes for a task have had their run: each job gets one run that ended in `state` at `at`, each
    started in order (task_wakes.started), on the computer `runner`. With `comments`, so has each queued message on
    the task a person wrote to it."""
    from backend import task_wakes

    at = at or H.now()
    rows = c.execute(
        "SELECT j.id, j.message_id FROM jobs j JOIN messages m ON m.id = j.message_id "
        "WHERE j.state='queued' AND j.bot_id=? AND (m.wake_key LIKE ? OR (? AND json_extract(m.refs, '$.task') = ?))",
        (H.actor_id(actor), task_id + ":%", comments, task_id),
    ).fetchall()
    for job_id, message_id in rows:
        run = H.new_id()
        c.execute(
            "INSERT INTO attempts (id, job_id, bot_id, runner_id, generation, token_hash, state, lease_until, "
            "created_at, started_at, finished_at, message_id, task_id) VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?)",
            (run, job_id, H.actor_id(actor), runner, run, state, at, at, at, at, message_id, task_id),
        )
        task_wakes.started(c, run)
        c.execute(
            "UPDATE jobs SET state=?, attempt_id=? WHERE id=?",
            ("completed" if state == "completed" else "uncertain", run, job_id),
        )
    return len(rows)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def requirement(c, stage_id, kind, **fields):
    """A requirement on a stage. Returns its id."""
    rid = H.new_id()
    row = {
        "id": rid,
        "stage_id": stage_id,
        "kind": kind,
        "source": fields.pop("source", None),
        "condition": fields.pop("condition", None),
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


def github(condition, **fields):
    """A requirement spec of kind `item` on the task's GitHub PRs (`pipeline`'s `requirements`)."""
    return {"kind": "item", "source": "github", "condition": condition, **fields}


def github_installed(c, repository="acme/app"):
    """The company's GitHub App, connected and installed, with `repository` In use (None: none is): a task with no PR
    linked is its bot's to open one, not Paused on Connect GitHub or Choose a repository (item_sources.github
    first_fix)."""
    from backend.github_app import SCHEMA
    from backend.repositories_schema import SCHEMA as REPOSITORIES

    for statement in (SCHEMA + ";" + REPOSITORIES).split(";"):
        if statement.strip():
            c.execute(statement)
    c.execute(
        "INSERT OR REPLACE INTO github_app(id,app_id,slug,client_id,org,administration,installation_id,html_url,"
        "ciphertext,nonce,created_at,created_by) VALUES('app',1,'tico','client','acme',0,1,'',x'00',x'00',?,?)",
        (H.now(), ANA),
    )
    if repository:
        c.execute(
            "INSERT INTO repositories(id,full_name,enabled,updated_at) VALUES(?,?,1,?) "
            "ON CONFLICT(full_name) DO UPDATE SET enabled=1",
            (H.new_id(), repository, H.now()),
        )


def _general(c, manager):
    """General from its preset (backend/pipeline_settings.py), with its fixed ids, managed by `manager`."""
    record = PS.preset(c, "general")
    record["manager_id"] = manager
    saved = PS.save(c, E.SYSTEM, record)
    stages = [*saved["stages"], saved["done"], saved["closed"]]
    return {
        "id": saved["id"],
        "stages": {s["name"]: s["id"] for s in stages},
        "requirements": {s["name"]: [r["id"] for r in s.get("requirements") or ()] for s in stages},
    }


def pipeline(c, pid="general", name="General", stages=GENERAL, manager=None):
    """A pipeline with its work stages, then Done and Closed. Returns {"id", "stages": {name: id}, "requirements":
    {stage name: [ids]}}. Stage ids are `<pipeline id>-<stage name as a slug>`. The default is General, built from its
    preset: its ids are fixed (`general-todo`, `general-done`, `general-closed`)."""
    if (pid, name, stages) == ("general", "General", GENERAL):
        return _general(c, manager)
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
            "INSERT INTO stages (id, pipeline_id, position, kind, name, owner, by_hand, requests_move_on, task_limit, "
            "expected_minutes, expected_bots_only, on_enter, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                pid,
                position,
                spec["kind"],
                spec["name"],
                spec.get("owner"),
                1 if spec.get("by_hand") else 0,
                1 if spec.get("requests_move_on") else 0,
                spec.get("task_limit"),
                spec.get("expected_minutes"),
                1 if spec.get("expected_bots_only") else 0,
                json.dumps(spec.get("on_enter") or {}),
                now,
                now,
            ),
        )
        if spec.get("pick"):
            c.execute("UPDATE stages SET pick=? WHERE id=?", (json.dumps(spec["pick"]), sid))
        out["stages"][spec["name"]] = sid
        out["requirements"][spec["name"]] = [
            requirement(c, sid, **dict(r, position=i)) for i, r in enumerate(spec.get("requirements") or ())
        ]
    return out


def settled(c):
    """Every wake so far has had its run: what is left queued is what a test adds."""
    c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")


def new_task(c, by, title, pipeline_id="general", assignee=None, parent=None):
    """A task in the first stage of `pipeline_id`, as `task_engine.task()` gives it: made the way every task is
    (`H.task_create`), given to `assignee`, or to whoever files it when nobody is named."""
    row = H.task_create(
        c,
        by,
        title,
        "What to do and how to tell it is done.",
        assignee or by,
        parent_id=parent,
        pipeline=pipeline_id,
        lint=False,
    )
    return E.task(c, row["id"])


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


def ask_key(c, requirement_id):
    """A batch item's key for an ask on a task's own visit: "ask:" + waiting_on.ask_id (its visit, the requirement)."""
    visit = c.execute("SELECT visit_id FROM requirements WHERE id=?", (requirement_id,)).fetchone()[0]
    return f"ask:{visit}:{requirement_id}"


def wakes(c, task_id, actor=None):
    """The engine's wakes for a task, oldest first: (wake_key, recipient)."""
    rows = c.execute(
        "SELECT wake_key, recipient_id FROM messages WHERE wake_key LIKE ? ORDER BY created_at, id", (task_id + ":%",)
    ).fetchall()
    return [(r[0], r[1]) for r in rows if actor is None or r[1] == actor]


def events(c, task_id):
    """The kinds of a task's history events, oldest first."""
    rows = c.execute(
        "SELECT kind FROM history_events WHERE subject_type='task' AND subject_id=? ORDER BY rowid", (task_id,)
    ).fetchall()
    return [row[0] for row in rows]


def left(c, actor):
    """Mark a person as left, as the People tab does (backend/access.py `leave`): their roster row is hidden."""
    from backend import access as Access
    from backend.views import roster

    whole = roster(c)
    people = whole["people"]
    at = next(i for i, p in enumerate(people) if p["id"] == H.actor_id(actor))
    people[at] = Access.leave(c, people, people[at])
    Access.save_roster(c, {**whole, "people": people})


def no_sign_in(c, actor):
    """Turn a person's sign-in off, as the People tab's switch does (backend/access.py): `sign_in: false` on their
    roster row. They are still on the chart, and cannot act."""
    from backend import access as Access
    from backend.views import roster

    whole = roster(c)
    people = whole["people"]
    at = next(i for i, p in enumerate(people) if p["id"] == H.actor_id(actor))
    people[at] = {**people[at], "sign_in": False}
    Access.save_roster(c, {**whole, "people": people})


def guard_closed(c):
    return c.execute("SELECT open FROM task_move_guards WHERE id=1").fetchone()[0] == 0
