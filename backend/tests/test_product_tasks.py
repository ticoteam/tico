"""Product: the built-in task type product ideas are filed as (backend/product_tasks.py). A fresh install gets it at its
first start, an existing one at the next start, and a team's own type named Product keeps its name while the built-in
one takes the next name free. Examples use Acme."""

from backend import product_tasks as PT
from backend.tests.test_api import api  # noqa: F401

STEPS = [
    ("product-idea", "Idea", "open"),
    ("product-shaping", "Shaping", "doing"),
    ("product-ready", "Ready for engineering", "waiting"),
    ("product-building", "Building", "doing"),
    ("product-shipped", "Shipped", "done"),
    ("product-measured", "Measured", "closed"),
    ("product-dropped", "Dropped", "closed"),
]


def type_and_steps(store, type_id=PT.TYPE_ID):
    with store.read() as c:
        name = c.execute("SELECT name FROM task_types WHERE id=?", (type_id,)).fetchone()
        steps = c.execute(
            "SELECT id, name, status FROM task_steps WHERE type_id=? ORDER BY position", (type_id,)
        ).fetchall()
    return (name[0] if name else None), [tuple(row) for row in steps]


def test_a_fresh_install_has_the_product_type_with_its_steps_in_board_order(api):
    name, steps = type_and_steps(api.app.state.store)
    assert name == "Product"
    assert steps == STEPS


def test_a_team_type_already_named_product_keeps_its_name_and_the_built_in_takes_the_next(api):
    store = api.app.state.store
    stamp = "2026-11-01T00:00:00.000000Z"
    with store.transaction() as c:
        c.execute("DELETE FROM task_steps WHERE type_id=?", (PT.TYPE_ID,))
        c.execute("DELETE FROM task_types WHERE id=?", (PT.TYPE_ID,))
        c.execute(
            "INSERT INTO task_types (id, name, created_at, updated_at) VALUES ('team-product', 'Product', ?, ?)",
            (stamp, stamp),
        )
        assert PT.ensure(c) == PT.TYPE_ID
    name, steps = type_and_steps(store)
    assert name == "Product idea"
    assert [step[0] for step in steps] == [step[0] for step in STEPS]
    assert type_and_steps(store, "team-product")[0] == "Product"
    # Starting again adds nothing and renames nothing.
    with store.transaction() as c:
        PT.ensure(c)
    assert type_and_steps(store) == (name, steps)
