# pyright: strict
"""The stuck count and the stuck alarm for engineering factories (roadmap 886, under 854).

A card is stuck when pipeline_exceptions.build lists it with kind "stuck": the one rule, with its STUCK_MINUTES (45)
threshold. Nothing here adds a second threshold. Each stuck entry has a `since`: the time the list shows the card stuck
from (its last run, or when it entered the step, or when its finished check went quiet). The alarm's age is that `since`.

- count(c, factory_id): the cards of the factory's pipelines stuck right now. Zero for a factory with no pipelines.
  The Overview's fifth number reads it (the block lives with the scoreboard, PR 3180).
- sweep(c, at): one task per card stuck for more than ALARM_AFTER_HOURS (4), filed to the factory's manager bot when the
  factory has one (factories.manager_id), else to the Engineering Pipeline Manager seat. One open alarm per card (the
  stuck_alarms row): no second task while the condition holds. When the card is no longer stuck for that long, the
  alarm clears itself: its row gets `cleared_at` and its task is closed.

The alarm ships switched off. It runs only when TICO_STUCK_ALARM is on (`enabled`); the Head of Engineering turns it on
after checking the live count. Plain SQL, no model. The heartbeat step is `stuck_alarm` (backend/scheduler.py).
"""

import os
from typing import TYPE_CHECKING, Any

from . import hubdb
from . import pipeline_exceptions

if TYPE_CHECKING:
    import sqlite3

# hubdb and pipeline_exceptions are untyped: their functions are Any here (as in factory_manager.py),
# not partially unknown.
H: Any = hubdb
PE: Any = pipeline_exceptions
Row = dict[str, Any]

ALARM_AFTER_HOURS = 4
ENV = "TICO_STUCK_ALARM"
ENGINEERING_MANAGER = "engineering-pipeline-manager"
STEP = "stuck_alarm"
TITLE = "Stuck alarm: "
SUBJECT = "ticket:"
ON = ("on", "1", "true", "yes")


def enabled() -> bool:
    """Whether the alarm runs. Off unless TICO_STUCK_ALARM is set to on."""
    return os.environ.get(ENV, "").strip().lower() in ON


def _pipelines(c: "sqlite3.Connection", factory_id: str) -> list[Row]:
    return H._rows(
        c.execute(
            "SELECT id, name, manager, factory_id FROM task_types WHERE factory_id=? ORDER BY name", (factory_id,)
        )
    )


def _factories(c: "sqlite3.Connection") -> list[str]:
    return [
        str(row[0])
        for row in c.execute(
            "SELECT DISTINCT factory_id FROM task_types WHERE factory_id IS NOT NULL ORDER BY factory_id"
        )
    ]


def _stuck_entries(c: "sqlite3.Connection", typ: Row, at: str) -> list[Row]:
    """The `stuck` entries of one pipeline, as pipeline_exceptions.build lists them for the type's manager (its default
    reader), so the count and the alarm read the same list."""
    return [entry for entry in PE.build(c, typ, at=at) if entry["kind"] == "stuck"]


def _card(entry: Row) -> str:
    """The card a stuck entry is about: its key is 'ticket:<card id>:stuck' (the subject is 'ticket:<card id>')."""
    return str(entry["key"])[len(SUBJECT) :].rsplit(":", 1)[0]


def _recipient(c: "sqlite3.Connection", factory_id: str) -> str | None:
    """The factory's manager bot when the factory has one (factories.manager_id), else the Engineering Pipeline Manager
    seat. None when that bot is not on the roster: the alarm waits for it, and files nothing."""
    slug = None
    if "manager_id" in {row[1] for row in c.execute("PRAGMA table_info(factories)")}:
        found = c.execute("SELECT manager_id FROM factories WHERE id=?", (factory_id,)).fetchone()
        if found and found[0]:
            slug = str(found[0])
    actor = H.bot_actor(slug or ENGINEERING_MANAGER)
    return actor if H.bot(c, actor) else None


def count(c: "sqlite3.Connection", factory_id: str, at: str | None = None) -> int:
    """The cards of this factory stuck right now: one per card on pipeline_exceptions.build's stuck rule, read as the
    pipeline's manager reads its list (build's default reader)."""
    now = at or H.now()
    return sum(len(_stuck_entries(c, typ, now)) for typ in _pipelines(c, factory_id))


def sweep(c: "sqlite3.Connection", at: str) -> dict[str, int]:
    """One pass of the alarm, inside the caller's transaction. {'raised': n, 'cleared': n}; nothing when it is off."""
    if not enabled():
        return {"raised": 0, "cleared": 0}
    cutoff = H.shift(at, hours=-ALARM_AFTER_HOURS)
    open_alarms = {
        row["task_id"]: row
        for row in H._rows(c.execute("SELECT * FROM stuck_alarms WHERE cleared_at IS NULL ORDER BY raised_at, id"))
    }
    overdue: dict[str, tuple[str, str, Row]] = {}  # card id -> (factory id, recipient, its stuck entry)
    waiting: set[str] = set()  # factories with no recipient this pass: their open alarms stand
    for factory_id in _factories(c):
        recipient = _recipient(c, factory_id)
        if recipient is None:
            waiting.add(factory_id)
            continue
        for typ in _pipelines(c, factory_id):
            for entry in _stuck_entries(c, typ, at):
                if entry["since"] < cutoff:  # stuck from before the cutoff: more than ALARM_AFTER_HOURS
                    overdue[_card(entry)] = (factory_id, recipient, entry)
    raised = 0
    for card, (factory_id, recipient, entry) in sorted(overdue.items(), key=lambda item: item[1][2]["since"]):
        if card in open_alarms:
            continue  # one task per card: the open alarm stands while the condition holds
        found = H.task(c, card)
        if not found:
            continue
        made = H.task_create(
            c,
            H.KEEPER,
            TITLE + str(found["title"] or "")[:150],
            f"Card task:{card} has been stuck for more than {ALARM_AFTER_HOURS} hours, since {entry['since']}"
            f" ({entry['cause']}). Unblock it, reassign it, split it or close it, and leave one line saying which. "
            "This alarm clears itself when the card moves.",
            recipient,
            deduplicate=False,
            lint=False,
            private=False,
            wake_refs={"wake": STEP},
            trigger="schedule",
            trigger_source=STEP,
        )
        c.execute(
            "INSERT INTO stuck_alarms (id, task_id, factory_id, recipient, alarm_task_id, stuck_since, raised_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (H.new_id(), card, factory_id, recipient, made["id"], entry["since"], at),
        )
        raised += 1
    cleared = 0
    for card, row in open_alarms.items():
        if card in overdue or row["factory_id"] in waiting:
            continue  # still stuck for that long, or its factory had no recipient this pass: the alarm stands
        c.execute("UPDATE stuck_alarms SET cleared_at=?, clear_reason=? WHERE id=?", (at, "no longer stuck", row["id"]))
        alarm = H.task(c, row["alarm_task_id"]) if row["alarm_task_id"] else None
        if alarm and alarm["status"] in H.ACTIVE_STATUSES:
            H.task_close(
                c, H.KEEPER, alarm["id"], note="The card is no longer stuck for that long: the alarm clears itself."
            )
        cleared += 1
    return {"raised": raised, "cleared": cleared}
