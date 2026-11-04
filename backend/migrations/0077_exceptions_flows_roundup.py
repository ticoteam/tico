"""A saved rule on the retired "exceptions" event moves to the task_roundup event, in its table row and in its automation.

The engineering exceptions are lines on the task roundup now (backend/task_roundup.py), and no event named "exceptions"
is left to wake on. Migration 0076 moved the rules table's rows. A rule the redesign cutover already made into an
automation keeps its event in the automation's flow: the step's `when` condition and the rule saved in its action's args.
Those move too, in the current flow and in each saved version (a restored version must not bring the retired event back).
Every other rule and flow keeps its event. Running it again changes nothing.
"""
import json

OLD, NEW = "exceptions", "task_roundup"


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _moved(node):
    """The JSON value with each event condition or rule event named OLD renamed to NEW, and whether any was renamed."""
    if isinstance(node, list):
        found = [_moved(item) for item in node]
        return [value for value, _ in found], any(hit for _, hit in found)
    if not isinstance(node, dict):
        return node, False
    out, hit = {}, False
    for key, value in node.items():
        if value == OLD and (key == "event" or (key == "value" and node.get("field") == "event")):
            out[key], hit = NEW, True
            continue
        out[key], below = _moved(value)
        hit = hit or below
    return out, hit


def up(conn):
    if _table(conn, "rules"):
        conn.execute("UPDATE rules SET event=? WHERE event=?", (NEW, OLD))
    if _table(conn, "automations"):
        for aid, flow in conn.execute("SELECT id, flow FROM automations WHERE flow LIKE ?", (f"%{OLD}%",)).fetchall():
            moved, hit = _moved(json.loads(flow))
            if hit:
                conn.execute("UPDATE automations SET flow=? WHERE id=?", (json.dumps(moved), aid))
    if _table(conn, "automation_versions"):
        for aid, n, flow in conn.execute("SELECT automation_id, n, flow FROM automation_versions WHERE flow LIKE ?",
                                         (f"%{OLD}%",)).fetchall():
            moved, hit = _moved(json.loads(flow))
            if hit:
                conn.execute("UPDATE automation_versions SET flow=? WHERE automation_id=? AND n=?",
                             (json.dumps(moved), aid, n))
