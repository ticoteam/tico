# pyright: strict
"""What a pipeline save changed, in words a person reads: the board's Log line and the notice its owner and manager get
when someone else changes it. `pipeline.save` events keep every change as {path: [old, new]}
(pipeline_settings._changes); this module only words them, one line per change, top-level facts first, then each
stage in its new order."""

import sqlite3
from typing import Any

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
ACTOR_KEYS = {"manager_id", "owner_id", "owner"}
LONGEST = 60


def _word(c: sqlite3.Connection, key: str, value: object) -> str:
    """One value as a person reads it: a name for a teammate, a factory's name, "none" for nothing."""
    if value is None or value == "":
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    text = str(value)
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
    if isinstance(old, dict | list) or isinstance(new, dict | list):
        return f"changed {name}'s {label}"
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
        out.append("changed the order of the stages")
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
                out.append(f"changed {what} on {name}")
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
