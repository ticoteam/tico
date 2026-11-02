"""Marketing: the built-in task type a marketing idea is filed as. It lives on the Tasks page with the rest of the work.

A marketing idea is a plain task of this type. Its steps run Idea, Testing, Scaling, with Paused to one side, and it
finishes by moving to Won, Lost, Inconclusive or Dropped. Channels are `channel:<slug>` labels, and the department is
the task's team. Nothing is required of it: no prediction, hypothesis, score or bet.

The type keeps the id `idea` it was seeded with, so the tasks filed under it and the step ids other code reads
(`idea-doing`, `idea-won` and the rest) need no rewrite. Its name is Marketing unless a team's own type already has
that name; then the names it had before, Idea or Growth idea, are the fallbacks.
"""
from . import hubdb as H

TYPE_ID = "idea"
TYPE_NAMES = ("Marketing", "Idea", "Growth idea")
# The steps in board order: (key, step name, task status). A step's id is `idea-<key>`; the first two keep the ids an
# earlier seed gave them.
STEPS = (("idea", "Idea", "open"), ("testing", "Testing", "doing"), ("scaling", "Scaling", "doing"),
         ("paused", "Paused", "waiting"), ("review", "Review", "review"), ("ready", "Ready", "ready"),
         ("done", "Done", "done"), ("closed", "Closed", "closed"), ("won", "Won", "closed"), ("lost", "Lost", "closed"),
         ("inconclusive", "Inconclusive", "closed"), ("dropped", "Dropped", "closed"))
STEP_IDS = {"idea": f"{TYPE_ID}-open", "testing": f"{TYPE_ID}-doing"}
TERMINAL = ("won", "lost", "inconclusive", "dropped")


def step_id(key):
    return STEP_IDS.get(key, f"{TYPE_ID}-{key}")


def started_step_id():
    """The step a marketing task moves to when its test starts (`idea-doing`)."""
    return step_id("testing")


def finished_step_ids():
    """{step id: outcome} for the steps a marketing task finishes on (`idea-won` ... `idea-dropped`)."""
    return {step_id(key): key for key in TERMINAL}


def ensure(c):
    """The Marketing task type and its steps, under the type's own id. A step missing from an older seed is added; one a
    team renamed keeps its name. Returns the type id, or None when the team's own types already use every name."""
    if not H._one(c, "SELECT id FROM task_types WHERE id=?", (TYPE_ID,)):
        taken = {r[0].lower() for r in c.execute("SELECT name FROM task_types")}
        name = next((n for n in TYPE_NAMES if n.lower() not in taken), None)
        if not name:
            return None
        now = H.now()
        c.execute("INSERT INTO task_types (id, name, created, updated) VALUES (?,?,?,?)", (TYPE_ID, name, now, now))
    names = {r[0].lower() for r in c.execute("SELECT name FROM task_steps WHERE type_id=?", (TYPE_ID,))}
    have = {r[0] for r in c.execute("SELECT id FROM task_steps WHERE type_id=?", (TYPE_ID,))}
    for position, (key, name, status) in enumerate(STEPS):
        sid = step_id(key)
        if sid in have:
            c.execute("UPDATE task_steps SET position=? WHERE id=?", (position, sid))
        elif name.lower() not in names:
            c.execute("INSERT INTO task_steps (id, type_id, name, position, status) VALUES (?,?,?,?,?)",
                      (sid, TYPE_ID, name, position, status))
    return TYPE_ID
