"""A saved rule on the retired "exceptions" event moves to the task_roundup event, in its table row and in its automation.

The engineering exceptions are lines on the task roundup now (backend/task_roundup.py), and no event named "exceptions"
is left to wake on. Migration 0076 moved the rules table's rows. A rule the redesign cutover already made into an
automation keeps its event in the automation's flow, in two places: a filter step's rule `when` condition
(`event is exceptions`) and the rule saved in that rule's action args (`args.rule.event`, backend/redesign_migration.py
_rule_step). Both move, in the current flow and in each saved version (a restored version must not bring the retired
event back). Nothing else in a flow is renamed: an action's own data, such as a webhook payload that has an `event`
key, keeps its value. Every other rule and flow keeps its event. Running it again changes nothing.
"""
import json

OLD, NEW = "exceptions", "task_roundup"


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _list(value):
    return value if isinstance(value, list) else []


def _steps(steps):
    """Each step of a flow's list, then the steps of its fork paths and of its otherwise branch (a filter can sit in either)."""
    for step in _list(steps):
        if not isinstance(step, dict):
            continue
        yield step
        for path in _list(step.get("paths")):
            if isinstance(path, dict):
                yield from _steps(path.get("steps"))
        if isinstance(step.get("otherwise"), dict):
            yield from _steps(step["otherwise"].get("steps"))


def _moved(flow):
    """Renames the retired event in the flow where the cutover wrote it, and returns whether it did: in a filter step's
    rule `when` (`field` event, `op` is) and in the rule saved in an action's args (`args.rule.event`)."""
    hit = False
    for step in _steps(flow.get("steps")):
        if step.get("type") != "filter":
            continue
        for rule in _list(step.get("rules")):
            if not isinstance(rule, dict):
                continue
            for cond in _list(rule.get("when")):
                if isinstance(cond, dict) and cond.get("field") == "event" and cond.get("op") == "is" and cond.get("value") == OLD:
                    cond["value"] = NEW
                    hit = True
            for action in _list(rule.get("then")):
                args = action.get("args") if isinstance(action, dict) else None
                saved = args.get("rule") if isinstance(args, dict) else None
                if isinstance(saved, dict) and saved.get("event") == OLD:
                    saved["event"] = NEW
                    hit = True
    return hit


def up(conn):
    if _table(conn, "rules"):
        conn.execute("UPDATE rules SET event=? WHERE event=?", (NEW, OLD))
    if _table(conn, "automations"):
        for aid, text in conn.execute("SELECT id, flow FROM automations WHERE flow LIKE ?", (f"%{OLD}%",)).fetchall():
            flow = json.loads(text)
            if _moved(flow):
                conn.execute("UPDATE automations SET flow=? WHERE id=?", (json.dumps(flow), aid))
    if _table(conn, "automation_versions"):
        for aid, n, text in conn.execute("SELECT automation_id, n, flow FROM automation_versions WHERE flow LIKE ?",
                                         (f"%{OLD}%",)).fetchall():
            flow = json.loads(text)
            if _moved(flow):
                conn.execute("UPDATE automation_versions SET flow=? WHERE automation_id=? AND n=?",
                             (json.dumps(flow), aid, n))
