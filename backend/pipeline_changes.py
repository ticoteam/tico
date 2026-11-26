# pyright: strict
"""What a pipeline save changed, in words a person reads: the board's Log line and the notice its owner and manager get
when someone else changes it. `pipeline.save` events keep every change as {path: [old, new]}
(pipeline_settings._changes); this module only words them, one line per change, top-level facts first, then each
stage in its new order."""

import json
import sqlite3
from typing import Any, cast

from . import hubdb

H: Any = hubdb  # untyped: its helpers are Any here

Row = dict[str, Any]

TOP = (
    ("name", "name"),
    ("goal", "goal"),
    ("prefix", "prefix"),
    ("manager_id", "manager"),
    ("owner_id", "owner"),
    ("private", "private"),
    ("factory_id", "factory"),
)
STAGE = (
    ("purpose", "goal"),
    ("owner", "holder"),
    ("task_limit", "limit"),
    ("side", "Side"),
    ("by_hand", "By hand"),
    ("requests_move_on", "Asks to move on"),
    ("on_enter", "action on entering"),
    ("pick", "who it picks from"),
)
REQUIREMENT = (
    ("kind", "kind"),
    ("source", "source"),
    ("condition", "condition"),
    ("settings", "settings"),
    ("can_push_past", "Can be pushed past"),
    ("who_oks", "who OKs"),
    ("person_id", "person"),
    ("person_ids", "people"),
    ("line", "wording"),
)
WHO_OKS = {"none": "nobody", "people_only": "people only", "people_with_exceptions": "people, with exceptions"}
ACTOR_KEYS = {"manager_id", "owner_id", "owner", "person_id", "person_ids"}
LONGEST = 60


def _word(c: sqlite3.Connection, key: str, value: object) -> str:
    """One value as a person reads it: a name for a teammate, a factory's name, "none" for nothing."""
    if value is None or value == "" or value == [] or value == {}:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(_word(c, key, one) for one in cast("list[object]", value))
    if key == "who_oks":
        return WHO_OKS.get(str(value), str(value))
    text = json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)
    if key in ACTOR_KEYS:
        if text == "assignee":
            return "the task's assignee"
        if text == "pick":
            return "one of several"
        return str(H.actor_name(c, text))
    if key == "factory_id":
        found = c.execute("SELECT name FROM factories WHERE id=?", (text,)).fetchone()
        return str(found[0]) if found else "a retired factory"
    return text if len(text) <= LONGEST else text[: LONGEST - 1].rstrip() + "…"


def _top(c: sqlite3.Connection, key: str, label: str, old: object, new: object) -> str:
    if key == "owner_id":
        return f"made {_word(c, key, new)} the owner" if new else f"removed {_word(c, key, old)} as the owner"
    if key == "private":
        return "made the pipeline private" if new else "made the pipeline company-wide"
    if key == "name":
        return f"renamed the pipeline {old} to {new}"
    return f"changed the {label} from {_word(c, key, old)} to {_word(c, key, new)}"


def _stage(c: sqlite3.Connection, name: str, key: str, label: str, old: object, new: object) -> str:
    if isinstance(old, bool) or isinstance(new, bool):
        return f"turned {label} {'on' if new else 'off'} for {name}"
    return f"changed {name}'s {label} from {_word(c, key, old)} to {_word(c, key, new)}"


def lines(c: sqlite3.Connection, pipeline_id: str, changes: Row) -> list[str]:
    """Every change one save made, one line each, without who made it ("renamed the stage Ideas to Bot ideas")."""
    out = [_top(c, key, label, *changes[key]) for key, label in TOP if key in changes]
    stages: dict[str, Row] = {}
    for path, pair in changes.items():
        parts = path.split(".")
        if parts[0] != "stages" or len(parts) < 3:
            continue
        stage = stages.setdefault(parts[1], {"fields": {}, "requirements": {}})
        if parts[2] == "requirements" and len(parts) >= 5:
            stage["requirements"].setdefault(parts[3], {})[parts[4]] = pair
        else:
            stage["fields"][parts[2]] = pair
    names = {str(r[0]): str(r[1]) for r in c.execute("SELECT id, name FROM stages WHERE pipeline_id=?", (pipeline_id,))}

    def at(sid: str) -> tuple[int, int]:
        old, new = stages[sid]["fields"].get("position", [None, None])
        return (0, new) if new is not None else (1, old if old is not None else 0)

    moved = [sid for sid, s in stages.items() if None not in s["fields"].get("position", [None, None])]
    if [m for m in sorted(moved, key=lambda m: stages[m]["fields"]["position"][0])] != sorted(
        moved, key=lambda m: stages[m]["fields"]["position"][1]
    ):
        # Each stage that moved, from its place to its new one (1 is where a new task starts).
        steps = [
            f"{stages[m]['fields'].get('name', [names.get(m)] * 2)[1] or 'a stage'} from "
            f"{stages[m]['fields']['position'][0] + 1} to {stages[m]['fields']['position'][1] + 1}"
            for m in sorted(moved, key=lambda m: stages[m]["fields"]["position"][1])
        ]
        out.append(f"changed the order of the stages: {', '.join(steps)}")
    for sid in sorted(stages, key=at):
        fields: Row = stages[sid]["fields"]
        old_name, new_name = fields.get("name", [names.get(sid), names.get(sid)])
        name = str(new_name or old_name or "a stage")
        if old_name is None and new_name is not None:
            out.append(f"added the stage {new_name}")
            continue
        if new_name is None and old_name is not None:
            out.append(f"removed the stage {old_name}")
            continue
        if "name" in fields:
            out.append(f"renamed the stage {old_name} to {new_name}")
        out += [_stage(c, name, key, label, *fields[key]) for key, label in STAGE if key in fields]
        for req in stages[sid]["requirements"].values():
            kind = req.get("kind", [None, None])
            words = (req.get("line") or [None, None])[1] or (req.get("line") or [None, None])[0]
            what = f'"{words}"' if words else "a requirement"
            if kind[0] is None and kind[1] is not None:
                out.append(f"added {what} to {name}")
            elif kind[1] is None and kind[0] is not None:
                out.append(f"removed {what} from {name}")
            else:
                out += [
                    f"changed {what} on {name}: {label} from {_word(c, key, req[key][0])} to "
                    f"{_word(c, key, req[key][1])}"
                    for key, label in REQUIREMENT
                    if key in req and key != "line"
                ]
                if "line" in req:
                    out.append(f"changed the wording on {name} from {_word(c, 'line', req['line'][0])} to {words}")
                if "position" in req:
                    out.append(f"moved {what} on {name} from {req['position'][0] + 1} to {req['position'][1] + 1}")
    return out


def sentence(c: sqlite3.Connection, by: str, pipeline_id: str, detail: Row) -> tuple[str, list[str]]:
    """The Log line for one save and its full list: who, then the first change from and to, and "and N more changes".
    A save that changed nothing says it was saved; a new pipeline says it was made."""
    who = H.actor_name(c, by)
    if detail.get("made"):
        return f"{who} made the pipeline", []
    every = lines(c, pipeline_id, dict(detail.get("changes") or {}))
    if not every:
        return f"{who} saved the pipeline", []
    more = len(every) - 1
    tail = f" and {more} more change{'s' if more > 1 else ''}" if more else ""
    return f"{who} {every[0]}{tail}", every


RULE = (
    ("name", "name"),
    ("stages", "stages"),
    ("event", "when"),
    ("cond", "only when"),
    ("jev", "Jev's question"),
    ("then", "then"),
    ("then_params", "then's settings"),
    ("reason", "reason"),
)


def _hours(timer: object) -> str:
    """A rule's timer as a person reads it: "24 h", else as stored."""
    found: dict[str, object] = cast("dict[str, object]", timer) if isinstance(timer, dict) else {}
    hours = found.get("hours")
    return f"{hours:g} h" if isinstance(hours, int | float) else _word_plain(found or None)


def _word_plain(value: object) -> str:
    if value is None or value == "" or value == {}:
        return "none"
    text = json.dumps(value, sort_keys=True) if isinstance(value, dict | list) else str(value)
    return text if len(text) <= LONGEST else text[: LONGEST - 1].rstrip() + "…"


def rule_lines(c: sqlite3.Connection, pipeline_id: str, detail: Row) -> list[str]:
    """Every change one rule change made (`pipeline.rule`, pipeline_settings.rule_changed), one line each, without who
    made it ("changed the Late time on Spec from 24 h to 4 h")."""
    names = {str(r[0]): str(r[1]) for r in c.execute("SELECT id, name FROM stages WHERE pipeline_id=?", (pipeline_id,))}

    def where(ids: object) -> str:
        listed = (
            [names.get(str(i), "a removed stage") for i in cast("list[object]", ids)] if isinstance(ids, list) else []
        )
        return ", ".join(listed) or "the pipeline"

    changes: Row = dict(detail.get("changes") or {})
    rule, on = str(detail.get("name") or "a rule"), where(detail.get("stages"))
    if "added" in changes:
        return [f"added the rule {rule} on {on}"]
    if "retired" in changes:
        return [f"retired the rule {rule} on {on}"]
    out: list[str] = []
    if "timer" in changes:
        old, new = changes["timer"]
        out.append(f"changed the {rule} time on {on} from {_hours(old)} to {_hours(new)}")
    for key, label in RULE:
        if key not in changes:
            continue
        old, new = changes[key]
        if key == "stages":
            old, new = where(old), where(new)
        out.append(f"changed the rule {rule} on {on}: {label} from {_word_plain(old)} to {_word_plain(new)}")
    return out


def rule_sentence(c: sqlite3.Connection, by: str, pipeline_id: str, detail: Row) -> tuple[str, list[str]]:
    """The Log line for one rule change and its full list, as `sentence` words a save."""
    every = rule_lines(c, pipeline_id, detail)
    more = len(every) - 1
    tail = f" and {more} more change{'s' if more > 1 else ''}" if more > 0 else ""
    return f"{H.actor_name(c, by)} {every[0] if every else 'changed a rule'}{tail}", every
