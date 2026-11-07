# pyright: strict
"""Product: the built-in task type a product idea is filed as, the board the product work runs on. It lives on the Tasks
page with the rest of the work, like the Marketing type (backend/marketing_tasks.py).

A product idea moves from Idea to Shaping, then Ready for engineering once it is clear enough to build; Building
follows, then Shipped, then Measured once its result is read. Dropped is the other way out. Nothing is required of it
beyond the steps.

The type's name is Product unless a team's own type already has that name; then Product idea, then Product board. Its
id is `product`, and its steps are `product-<key>`. No retire or rename touches it: it is seeded at every start, which
gives a fresh install and an existing one the same type.
"""

import sqlite3

from . import hubdb as H

TYPE_ID = "product"
TYPE_NAMES = ("Product", "Product idea", "Product board")
# The steps in board order: (key, step name, task status). A step's id is `product-<key>`.
STEPS = (
    ("idea", "Idea", "open"),
    ("shaping", "Shaping", "doing"),
    ("ready", "Ready for engineering", "waiting"),
    ("building", "Building", "doing"),
    ("shipped", "Shipped", "done"),
    ("measured", "Measured", "closed"),
    ("dropped", "Dropped", "closed"),
)


def step_id(key: str) -> str:
    return f"{TYPE_ID}-{key}"


def ensure(c: sqlite3.Connection) -> str | None:
    """The Product task type and its steps, under the type's own id. A step missing from an older seed is added; one a
    team renamed keeps its name. Returns the type id, or None when the team's own types already use every name."""
    if not c.execute("SELECT id FROM task_types WHERE id=?", (TYPE_ID,)).fetchone():
        taken = {str(r[0]).lower() for r in c.execute("SELECT name FROM task_types").fetchall()}
        name = next((n for n in TYPE_NAMES if n.lower() not in taken), None)
        if not name:
            return None
        now = H.now()
        c.execute(
            "INSERT INTO task_types (id, name, created_at, updated_at) VALUES (?,?,?,?)", (TYPE_ID, name, now, now)
        )
    names = {str(r[0]).lower() for r in c.execute("SELECT name FROM task_steps WHERE type_id=?", (TYPE_ID,)).fetchall()}
    have = {str(r[0]) for r in c.execute("SELECT id FROM task_steps WHERE type_id=?", (TYPE_ID,)).fetchall()}
    for position, (key, name, status) in enumerate(STEPS):
        sid = step_id(key)
        if sid in have:
            # Only a step that moved is written: a changed row bumps the roster clock (backend/roster_clock.py), which
            # every start would otherwise do for every step.
            c.execute("UPDATE task_steps SET position=? WHERE id=? AND position IS NOT ?", (position, sid, position))
        elif name.lower() not in names:
            c.execute(
                "INSERT INTO task_steps (id, type_id, name, position, status) VALUES (?,?,?,?,?)",
                (sid, TYPE_ID, name, position, status),
            )
    return TYPE_ID
