"""Helpers that other tests share, moved out of test_pipeline_manager.py so they import no test file."""

from datetime import datetime, timedelta, timezone
from backend import hubdb as H


ANA = "human:ana"


STAGES = [
    {"name": "Building", "status": "doing"},
    {"name": "Review", "status": "doing"},
    {"name": "Shipped", "status": "done"},
]


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(at):
    return at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def pipeline(c, manager="coo", every=10, name="Launch"):
    """A pipeline whose manager was assigned a day ago, so the watchdog's clock starts before the test's own times."""
    typ = H.type_create(c, ANA, name, STAGES, mover=True)
    c.execute("UPDATE task_types SET manager=?, manager_every_minutes=? WHERE id=?", (manager, every, typ["id"]))
    c.execute("UPDATE task_types SET manager_since=? WHERE id=?", (stamp(now() - timedelta(days=1)), typ["id"]))
    return typ["id"]


def step(c, type_id, name):
    return c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (type_id, name)).fetchone()[0]


def ticket(c, type_id, stage="Building", created=None):
    row = H.task_create(c, ANA, "Ship the launch page", "", ANA, type=type_id, step=stage, lint=False)
    if created is not None:
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (stamp(created), row["id"]))
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
            (stamp(created), row["id"]),
        )
    return H.task(c, row["id"])


def visit(c, task_id, type_id, stage, entered):
    """The ticket's open visit, started at `entered`: the one the engine opened at creation is moved to that time, else
    one is opened (a ticket has one open visit at a time)."""
    open_visit = c.execute("SELECT id FROM stage_visits WHERE task_id=? AND left_at IS NULL", (task_id,)).fetchone()
    if open_visit:
        c.execute(
            "UPDATE stage_visits SET pipeline_stage_id=?, entered_at=? WHERE id=?",
            (step(c, type_id, stage), stamp(entered), open_visit[0]),
        )
        return
    c.execute(
        "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, direction, entered_by, entered_at) "
        "VALUES (?,?,?,?,'initial',?,?)",
        (f"visit-{task_id}-{entered.timestamp():.0f}", task_id, type_id, step(c, type_id, stage), ANA, stamp(entered)),
    )


def botops_roster(c):
    H.sync_registry(c, {"botops": {"name": "botops", "status": "active"}}, None)
