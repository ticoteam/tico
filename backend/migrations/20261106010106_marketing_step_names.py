"""The Marketing task type's steps take the names the growth board uses (backend/marketing_tasks.py): Idea becomes On
deck, Testing becomes Live, Scaling becomes Working, Won becomes Succeeded and Lost becomes Failed. Only a step still
under its old default name is renamed, so a team's own name stays, and a name another step of the type already has is
left alone. The step ids do not change, so the code that reads them (idea-doing, idea-won ...) is unaffected.

A rule or "What wakes" rule that names a renamed step by its old name names the new one, so it stays on the same step.
The In development step is not made here: marketing_tasks.ensure adds it at the next start, in its board position.

Plain SQL and JSON, not the app's own code: a migration runs once, on every file, whatever the code becomes later.
Running it again changes nothing, because a step already renamed no longer has its old name.
"""
import json
from datetime import datetime, timezone

TYPE_ID = "idea"
# (step id, old default name, new name)
RENAMES = (("idea-open", "Idea", "On deck"), ("idea-doing", "Testing", "Live"), ("idea-scaling", "Scaling", "Working"),
           ("idea-won", "Won", "Succeeded"), ("idea-lost", "Lost", "Failed"))
WAKES_PREFIX = "auto_wakes_"


def _moved(value, names):
    """The value with each step name it holds renamed. `names` maps a lowercased old name to its new one, as a rule
    matches a step by name ignoring case."""
    if isinstance(value, list):
        return [names.get(str(v).lower(), v) for v in value]
    if isinstance(value, str):
        return names.get(value.lower(), value)
    return value


def _dicts(value):
    """The dict items of a list, so a malformed flow is skipped rather than failing the start."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _rename_wake_flow(flow, names):
    """Renames the step conditions of a What wakes flow in place: a rule's `task.step is` condition, and a rule that
    another rule's action carries (its `args.rule.when.step`)."""
    for step in _dicts(flow.get("steps")):
        for rule in _dicts(step.get("rules")):
            for cond in _dicts(rule.get("when")):
                if cond.get("field") == "task.step" and cond.get("op") == "is":
                    cond["value"] = _moved(cond.get("value"), names)
            for then in _dicts(rule.get("then")):
                source = (then.get("args") or {}).get("rule") if isinstance(then.get("args"), dict) else None
                if isinstance(source, dict) and isinstance(source.get("when"), dict) and "step" in source["when"]:
                    source["when"]["step"] = _moved(source["when"]["step"], names)


def _rules(conn, names, stamp):
    """The type's own rules that name a step (rules.condition's `step`)."""
    for ident, raw in conn.execute("SELECT id, condition FROM rules WHERE scope_kind='type' AND scope_id=?",
                                   (TYPE_ID,)).fetchall():
        try:
            when = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(when, dict) or "step" not in when:
            continue
        moved = _moved(when["step"], names)
        if moved != when["step"]:
            when["step"] = moved
            conn.execute("UPDATE rules SET condition=?, updated_at=?, revision=revision+1 WHERE id=?",
                         (json.dumps(when, sort_keys=True), stamp, ident))


def _wakes(conn, names, stamp):
    """The type's "What wakes" automations. Only their flow text changes: the automation is not saved again."""
    for ident, raw in conn.execute("SELECT id, flow FROM automations WHERE id>=? AND id<? AND deleted_at IS NULL",
                                   (WAKES_PREFIX, WAKES_PREFIX[:-1] + "`")).fetchall():
        try:
            flow = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(flow, dict) or flow.get("wakes") != {"scope_kind": "type", "scope_id": TYPE_ID}:
            continue
        renamed = json.loads(raw)
        _rename_wake_flow(renamed, names)
        if renamed != flow:
            conn.execute("UPDATE automations SET flow=?, updated_at=? WHERE id=?",
                         (json.dumps(renamed, sort_keys=True), stamp, ident))


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # A new file makes the module's tables after the migrations: nothing to rename yet, and ensure seeds the new names.
    if "task_steps" not in tables:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_steps)")}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    renamed = {}
    for ident, old, new in RENAMES:
        held = conn.execute("SELECT 1 FROM task_steps WHERE id=? AND type_id=? AND name=?", (ident, TYPE_ID, old))
        if not held.fetchone():
            continue
        # A name another step of the type holds (ignoring case, as a team's step names are read) is not taken from it.
        clash = conn.execute("SELECT 1 FROM task_steps WHERE type_id=? AND lower(name)=lower(?)", (TYPE_ID, new))
        if clash.fetchone():
            continue
        # public_name follows the name only where it was the old name too (a public type's steps are named publicly).
        if "public_name" in columns:
            conn.execute("UPDATE task_steps SET name=?, public_name=CASE WHEN public_name=? THEN ? ELSE public_name END"
                         " WHERE id=?", (new, old, new, ident))
        else:
            conn.execute("UPDATE task_steps SET name=? WHERE id=?", (new, ident))
        renamed[old.lower()] = new
    if not renamed:
        return
    if "rules" in tables:
        _rules(conn, renamed, stamp)
    if "automations" in tables:
        _wakes(conn, renamed, stamp)
