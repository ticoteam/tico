"""The Late list the board's cards read (GET /api/v2/pipelines/{id}/late): every late live ticket of the pipeline, with no
cap and with or without a manager, a held ticket left out, and a pipeline the reader may not see answered as missing."""

import uuid

from backend import hubdb as H
from backend import task_holds
from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)
from backend.tests.test_pipeline_members_only import make_pipeline, ticket


def late_ticket(api, typ, steps, title, minutes_ago, owner="human:ben"):
    """A ticket at the stage with a one-hour Warn after, whose clock started `minutes_ago` minutes ago."""
    tid = ticket(api, typ, title, owner)
    when = H.shift(H.now(), minutes=-minutes_ago)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (steps["Doing"],))
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (when, tid))
        c.execute("UPDATE history_events SET created_at=? WHERE subject_id=?", (when, tid))
    return tid


def test_the_late_list_names_each_late_ticket_and_leaves_out_one_within_its_limit_or_on_hold(api):
    tag = uuid.uuid4().hex[:8]
    typ, steps = make_pipeline(api, "Late" + tag)  # no manager bot: the list does not need one
    late = late_ticket(api, typ, steps, "Late " + tag, minutes_ago=180)
    fresh = late_ticket(api, typ, steps, "Fresh " + tag, minutes_ago=5)
    held = late_ticket(api, typ, steps, "Held " + tag, minutes_ago=180)
    with api.app.state.store.transaction() as c:
        task_holds.put(c, held, "blocked", H.shift(H.now(), minutes=-120))

    body = get(api, f"pipelines/{typ}/late")
    assert set(body["late"]) == {late}, body
    entry = body["late"][late]
    assert set(entry) == {"since", "hours", "limit_hours"}
    assert entry["limit_hours"] == 1
    assert entry["hours"] >= 2.9
    assert fresh not in body["late"] and held not in body["late"]


def test_the_late_list_is_not_capped_at_the_rail_cap(api):
    """More than the rail's 100 late tickets: every one of them gets its badge."""
    tag = uuid.uuid4().hex[:8]
    typ, steps = make_pipeline(api, "Busy" + tag)
    template = late_ticket(api, typ, steps, "Busy " + tag, minutes_ago=180)
    old = H.shift(H.now(), minutes=-180)
    ids = []
    with api.app.state.store.transaction() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(tasks)")]
        base = dict(zip(cols, c.execute("SELECT * FROM tasks WHERE id=?", (template,)).fetchone(), strict=True))
        for i in range(120):
            row = {**base, "id": f"busy-{tag}-{i:03d}", "created_at": old, "updated_at": old}
            if "number" in row:
                row["number"] = None
            ids.append(row["id"])
            c.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    body = get(api, f"pipelines/{typ}/late")
    assert set(ids) <= set(body["late"]), len(body["late"])
    assert len(ids) == 120 and len(body["late"]) >= 120


def test_the_late_list_is_missing_for_a_pipeline_the_reader_may_not_see(api):
    tag = uuid.uuid4().hex[:8]
    typ, steps = make_pipeline(api, "Hush" + tag, private=True)
    late_ticket(api, typ, steps, "Hushed " + tag, minutes_ago=180)
    get(api, f"pipelines/{typ}/late", "cara-test", expected=404)
    assert get(api, f"pipelines/{typ}/late", "ana-test")["late"]
