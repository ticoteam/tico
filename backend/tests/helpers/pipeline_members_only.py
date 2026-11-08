"""Helpers that other tests share, moved out of test_pipeline_members_only.py so they import no test file."""

from backend import hubdb as H


STEPS = [{"name": "Doing", "status": "doing"}, {"name": "Done", "status": "done"}]


def make_pipeline(api, name, *, private=False):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, name, STEPS)
        c.execute(
            "UPDATE task_types SET pipeline_owner_id=?, pipeline_visibility=? WHERE id=?",
            ("human:ana", "members" if private else "company", typ["id"]),
        )
    return typ["id"], {s["name"]: s["id"] for s in typ["steps"]}


def ticket(api, typ, title, owner, requester="human:ana"):
    """A ticket on a pipeline, public to the company but for the pipeline's own privacy."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, requester, title, "", owner, type=typ, step="Doing", lint=False)
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (task["id"],))
    return task["id"]
