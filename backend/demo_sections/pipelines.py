# pyright: strict
"""The Dev tickets pipeline as the pipeline page shows it: stages with requirements (dots), what waits on Ana (the
play button), the manager's rail (a clean run, a failed one, what it did) and a ticket past its stage limit (Late).

Only the tables and routes that exist before the pipeline engine are used: the type and its tickets go through the API,
the requirements, decisions, visits and manager rows are written as the engine would leave them."""

import json
from datetime import datetime, timedelta
from typing import Any

from ..store import H

NAME = "Dev tickets"
MANAGER = "product-manager"
OWNER = "human:eli"
ME = "human:ana"
STAGES = (  # name, status, limit in minutes
    ("Spec", "open", 24 * 60),
    ("Build", "doing", 48 * 60),
    ("Review 1", "review", 24 * 60),
    ("Done", "done", None),
)
# stage, requirement, kind, reviewed_by, reviewer
REQUIREMENTS = (
    ("Spec", "Spec page", "attachment", "person", ME),
    ("Build", "Tests pass", "run", "nobody", None),
    ("Build", "Screenshots", "attachment", "bot_then_person", ME),
    ("Review 1", "Reviewer approval", "approval", "person", OWNER),
)
# key, title, stage, hours in stage, owner, (requirement, state) for each requirement of its stage
TICKETS = (
    ("D61", "Pipeline stages and the manager rail", "Spec", 3, "product-manager", (("Spec page", "waiting"),)),
    (
        "D52",
        "Assistant can approve a spec",
        "Build",
        20,
        "devops",
        (("Tests pass", "met"), ("Screenshots", "waiting")),
    ),
    (
        "D55",
        "Phone board shows one stage",
        "Build",
        70,
        "devops",
        (("Tests pass", "met"), ("Screenshots", "out_of_date")),
    ),
    ("D17", "Calibration export retries", "Review 1", 30, "devops", (("Reviewer approval", "waiting"),)),
)


# A run requirement reads its source as it is now (R6): Tests pass reads the GitHub checks on the ticket's pull request.
NEEDS: dict[str, dict[str, str]] = {"Tests pass": {"source": "github_checks"}}


def _id() -> str:
    return H.new_id()


def seed(b: Any) -> None:
    b.at(days=3)
    with b.store.read() as c:
        row = c.execute("SELECT id FROM task_types WHERE name=?", (NAME,)).fetchone()
    if row:
        type_id = row[0]
    else:
        steps = [{"name": name, "status": status} for name, status, _ in STAGES]
        made = b.call("POST", "task-types", {"name": NAME, "steps": steps, "numbered": True})
        type_id = made["type"]["id"]
    with b.store.read() as c:
        stage: dict[str, str] = {
            r["name"]: r["id"] for r in c.execute("SELECT id, name FROM task_steps WHERE type_id=?", (type_id,))
        }
    ids: dict[str, str] = {}
    for key, title, stage_name, _, owner, _ in TICKETS:
        b.at(days=3)
        ids[key] = b.call(
            "POST",
            "tasks",
            {"title": title, "body": title + ".", "owner": owner, "type": type_id, "step": stage[stage_name]},
        )["task"]["id"]

    def write(c: Any) -> None:
        ts = H.now()
        # Every ticket is at its stage's current work revision (1). Waiting evidence is attached at that revision; the
        # stale one at the revision before it, so the attach-time revision is what makes it out of date (D33).
        for task in ids.values():
            c.execute("UPDATE tasks SET work_revision=1 WHERE id=?", (task,))
        c.execute("UPDATE task_types SET manager=? WHERE id=?", (MANAGER, type_id))
        c.execute("UPDATE task_types SET pipeline_owner_id=?, manager_every_minutes=60 WHERE id=?", (OWNER, type_id))
        for name, _, limit in STAGES:
            c.execute("UPDATE task_steps SET limit_minutes=? WHERE id=?", (limit, stage[name]))
        req: dict[str, str] = {}
        for position, (stage_name, name, kind, reviewed_by, reviewer) in enumerate(REQUIREMENTS):
            req[name] = _id()
            c.execute(
                "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, "
                "reviewer_id, position, version, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
                (
                    req[name],
                    type_id,
                    stage[stage_name],
                    name,
                    kind,
                    json.dumps(NEEDS.get(name, {})),
                    reviewed_by,
                    reviewer,
                    position,
                    OWNER,
                    ts,
                    ts,
                ),
            )
            definition = json.dumps({"name": name, "kind": kind, "reviewed_by": reviewed_by, "reviewer_id": reviewer})
            c.execute(
                "INSERT INTO stage_requirement_versions (stage_requirement_id, version, definition, changed_by, created_at) "
                "VALUES (?,1,?,?,?)",
                (req[name], definition, OWNER, ts),
            )
        for key, _, stage_name, hours, _, states in TICKETS:
            entered = b.ago(hours=hours).strftime("%Y-%m-%dT%H:%M:%SZ")
            task = ids[key]
            c.execute("UPDATE stage_visits SET left_at=? WHERE task_id=? AND left_at IS NULL", (entered, task))
            # Worked an hour ago: a ticket's Late clock is its stage visit, and a demo ticket is not stuck work.
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (b.ago(hours=1).strftime("%Y-%m-%dT%H:%M:%SZ"), task))
            c.execute(
                "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, direction, entered_by, entered_at) "
                "VALUES (?,?,?,?,'initial',?,?)",
                (_id(), task, type_id, stage[stage_name], OWNER, entered),
            )
            for name, state in states:
                if state == "met":
                    c.execute(
                        "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, "
                        "requirement_version, work_version, judge, judge_actor, verdict, reason, created_at) "
                        "VALUES (?,?,?,?,1,1,'rule','system','met','Passed on 3f2a9c1',?)",
                        (_id(), task, stage[stage_name], req[name], entered),
                    )
                    if name in NEEDS:  # the passing checks that rule judged
                        c.execute(
                            "INSERT INTO task_links (id, task_id, kind, url, title, state, checks, added_by, created_at, "
                            "updated_at) VALUES (?,?,'pr',?,?,'open','passing','bot:devops',?,?)",
                            (_id(), task, "https://github.com/acme/app/pull/" + key[1:], key, entered, entered),
                        )
                elif state in ("waiting", "out_of_date"):
                    attach_id = _id()
                    link = "https://acme.example/shots/" + key.lower()
                    version = 1 if state == "waiting" else 0  # the work revision the evidence was attached at
                    c.execute(
                        "INSERT INTO requirement_attachments (id, task_id, stage_requirement_id, source, url, digest, "
                        "work_version, attached_by, created_at) VALUES (?,?,?,'link',?,?,?,?,?)",
                        (attach_id, task, req[name], link, "sha256:" + key.lower(), version, "bot:devops", entered),
                    )
        # The manager's rail: a clean run, a failed one and a quiet one, with what it did.
        runs: list[tuple[str, datetime]] = []
        for minutes, status, error, actions in (
            (10, "ok", None, 1),
            (70, "failed", "The model was unreachable", 0),
            (130, "ok", None, 0),
        ):
            started = b.now - timedelta(minutes=minutes)
            run = _id()
            runs.append((run, started))
            c.execute(
                "INSERT INTO pipeline_manager_runs (id, pipeline_id, started_at, finished_at, status, exceptions_count, "
                "actions_count, model_called, error) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    type_id,
                    started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    (started + timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    status,
                    actions,
                    actions,
                    int(status == "ok"),
                    error,
                ),
            )
        first = runs[0]
        for action, task, target, note in (
            ("ask", ids["D52"], ME, "Screenshots are waiting on you."),
            ("propose_rule", None, OWNER, "Add a Tests pass requirement to Review 1?"),
        ):
            c.execute(
                "INSERT INTO pipeline_manager_actions (id, pipeline_id, pipeline_manager_run_id, task_id, action, "
                "target_actor, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (_id(), type_id, first[0], task, action, target, note, first[1].strftime("%Y-%m-%dT%H:%M:%SZ")),
            )

    b.write(write)
