# pyright: strict
"""Source actions: what Tico owes a source for one item, and carries out on the Heartbeat (docs/pipelines-v3.md
"Sources and items"). GitHub's one action is `queue` (its Merge stage puts a task's PRs in the merge queue).

- A stage acts on a source when entered: `on_enter` `{"act": "<source>.<action>"}` (`enter`; the source says which
  items, `entered`), and leaving it backwards owes the undo (`undo_stage`).
- An action is owed once per key in the `effects` table: `act` (e.g. queue the PR) or `undo` (take it out again), for
  one item (`source`, `item_type`, `item_key`), `pinned` to the item version it was owed for (a PR's head). Key:
  `<kind>:<source>.<action>:<visit>:<item key>[@<pinned>]`; a second owe of the same key is nothing.
- `run` (the Heartbeat's `source_actions` step) carries out owed actions with no transaction held during the source's
  call, and records each in a short write. A failed call is tried again after RETRY_S; after TRIES failed calls, or
  when the source answers no, the action is `failed` and the source module says what that means (`gave_up`).
- `undo` (the task closed or went back): an `act` still owed is moot, as nothing was done yet; one done owes its `undo`.
  An `act` the source carried out after it went moot owes its `undo` then.
- An `undo` belongs to its visit, not to the item: before it is carried out, the source says whether another open
  visit (this task's next one, or another task's) still wants the same item at the same version (`undo_moot`); then the
  undo is moot and no call is made. One that reached the source anyway is reconciled by the source (`recorded`).

States: owed, done, failed, moot. A source module (backend/item_sources) that acts names them in `ACTIONS` and has
`entered(conn, task, by, action)`, `carry_out(client, effect)`
(the call, no transaction held: returns what the source said, `{"ok": bool, "reason"?, "how"?}`), `done_already(conn,
effect)` (True: no call is made; the action is done, unless the source settled it otherwise itself), `recorded(conn,
effect, said, state, version)` and `gave_up(conn, effect, reason)`. It may have `look_first(store, client, effect)`, a
read before `done_already` with no transaction held, so that it decides on what the source says now, and
`undo_moot(conn, effect)` (True: an `undo` takes back what an open visit still wants; it is moot, no call is made).

Every function takes the caller's open write transaction and never commits, except `run`.
"""

import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
ACT, UNDO = "act", "undo"
RETRY_S = (60, 300, 900)  # a failed call is tried again after these; then the action is `failed`
TRIES = len(RETRY_S)  # failed calls before the action is `failed`
BATCH = 50  # actions carried out per pass at most


def _h() -> Any:
    from . import hubdb

    return hubdb


def _source(source_id: str) -> Any:
    from . import item_sources

    return item_sources.source(source_id)


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def act_of(stage: Row | None) -> tuple[str, str] | None:
    """(source, action) of a stage whose on-enter action is a source's (`{"act": "<source>.<action>"}`, e.g.
    `github.queue`), or None."""
    action: Any = (stage or {}).get("on_enter")
    if isinstance(action, str):
        try:
            action = json.loads(action)
        except ValueError:
            return None
    act = cast("Row", action).get("act") if isinstance(action, dict) else None
    if not isinstance(act, str) or not known(act):
        return None
    source, _, name = act.partition(".")
    return source, name


def known(act: str) -> bool:
    """Whether `<source>.<action>` names an action a source has (its module's ACTIONS)."""
    source, _, name = act.partition(".")
    module = _source(source) if source else None
    return module is not None and name in getattr(module, "ACTIONS", ())


def enter(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe what the stage the task just entered acts on (`stage_effects.enter`): the source decides which items
    (`entered`). Returns the actions owed now."""
    found = act_of(task.get("stage"))
    if not found or not task.get("visit"):
        return []
    owed: list[Row] = _source(found[0]).entered(conn, task, by, found[1])
    return owed


def undo_stage(conn: "sqlite3.Connection", task: Row) -> list[Row]:
    """Owe back what the task's open visit acted on (`stage_effects.undo`: it closed or went back; `undo`)."""
    found = act_of(task.get("stage"))
    visit = task.get("visit")
    if not found or not visit:
        return []
    return undo(conn, str(visit["id"]), found[0], found[1])


def key(kind: str, source: str, action: str, visit_id: str, item_key: str, pinned: str | None = None) -> str:
    """An action's key: one per visit, item and pinned version."""
    at = f"@{pinned}" if pinned else ""
    return f"{kind}:{source}.{action}:{visit_id}:{item_key.lower()}{at}"


def of_visit(
    conn: "sqlite3.Connection", visit_id: str, source: str, action: str, kind: str = ACT, item_key: str | None = None
) -> list[Row]:
    """A visit's actions of one kind, oldest first; for one item when `item_key` is given."""
    found = _rows(
        conn,
        "SELECT * FROM effects WHERE visit_id=? AND source=? AND action=? AND kind=? ORDER BY created_at, rowid",
        (visit_id, source, action, kind),
    )
    if item_key is None:
        return found
    return [e for e in found if str(e["item_key"]).lower() == item_key.lower()]


def owe(
    conn: "sqlite3.Connection",
    visit_id: str,
    kind: str,
    source: str,
    action: str,
    item_type: str,
    item_key: str,
    pinned: str | None = None,
) -> Row | None:
    """Owe one action, once per key: the row written, or None when that key was owed before."""
    H = _h()
    ts = H.now()
    effect_id = H.new_id()
    written = conn.execute(
        "INSERT OR IGNORE INTO effects (id, visit_id, kind, source, action, item_type, item_key, pinned, key, state, "
        "tries, created_at, updated_at, next_try_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'owed', 0, ?, ?, NULL)",
        (
            effect_id,
            visit_id,
            kind,
            source,
            action,
            item_type,
            item_key,
            pinned,
            key(kind, source, action, visit_id, item_key, pinned),
            ts,
            ts,
        ),
    ).rowcount
    if not written:
        return None
    return _rows(conn, "SELECT * FROM effects WHERE id=?", (effect_id,))[0]


def owe_again(
    conn: "sqlite3.Connection",
    visit_id: str,
    source: str,
    action: str,
    item_type: str,
    item_key: str,
    pinned: str | None,
) -> Row:
    """Owe an `act` again (the stage owner asked to): the visit's other acts for the item are moot, and the one for
    this pinned version is owed afresh."""
    ts = _h().now()
    again = key(ACT, source, action, visit_id, item_key, pinned)
    conn.execute(
        "UPDATE effects SET state='moot', updated_at=? WHERE visit_id=? AND kind=? AND source=? AND action=? "
        "AND key<>? AND item_key=? AND state IN ('owed', 'done', 'failed')",
        (ts, visit_id, ACT, source, action, again, item_key),
    )
    conn.execute(
        "UPDATE effects SET state='owed', tries=0, last_error=NULL, next_try_at=NULL, updated_at=? WHERE key=?",
        (ts, again),
    )
    owe(conn, visit_id, ACT, source, action, item_type, item_key, pinned)
    return _rows(conn, "SELECT * FROM effects WHERE key=?", (again,))[0]


def undo(conn: "sqlite3.Connection", visit_id: str, source: str, action: str) -> list[Row]:
    """Owe an `undo` for each item an `act` of the visit was carried out on (the task closed or went back). An `act`
    still owed is moot instead: nothing was done yet (`run` undoes one the source carried out meanwhile). Idempotent."""
    conn.execute(
        "UPDATE effects SET state='moot', updated_at=? WHERE visit_id=? AND kind=? AND source=? AND action=? "
        "AND state='owed'",
        (_h().now(), visit_id, ACT, source, action),
    )
    owed = [_owe_undo(conn, e) for e in of_visit(conn, visit_id, source, action) if e["state"] == "done"]
    return [e for e in owed if e]


def _owe_undo(conn: "sqlite3.Connection", effect: Row) -> Row | None:
    """The `undo` of one `act`, pinned to the same item version, so the source knows what it takes back (GitHub fails
    its `tico/merge-ok` on that head)."""
    return owe(
        conn,
        str(effect["visit_id"]),
        UNDO,
        str(effect["source"]),
        str(effect["action"]),
        str(effect["item_type"]),
        str(effect["item_key"]),
        effect.get("pinned"),
    )


# ----------------------------------------------------------------------------- the Heartbeat step
def due(conn: "sqlite3.Connection", now: str, limit: int = BATCH) -> list[Row]:
    """Owed actions whose next try is due, oldest first, with the task each is for."""
    return _rows(
        conn,
        "SELECT e.*, v.task_id FROM effects e JOIN visits v ON v.id = e.visit_id WHERE e.state='owed' "
        "AND (e.next_try_at IS NULL OR e.next_try_at <= ?) ORDER BY e.created_at, e.rowid LIMIT ?",
        (now, limit),
    )


def run(store: Any, clients: dict[str, Any], now: str | None = None) -> int:
    """The Heartbeat's step: carry out owed actions with no transaction held during the source's call, and record
    each in a short write. `clients` holds each source's client by source id; an action of a source with none waits.
    Returns how many were carried out (done, or failed for good)."""
    at: str = now or _h().now()
    with store.read() as conn:
        owed = due(conn, at)
    count = 0
    for effect in owed:
        module, client = _source(str(effect["source"])), clients.get(str(effect["source"]))
        if module is None or client is None:
            continue
        if effect["kind"] == ACT:
            look = getattr(module, "look_first", None)
            if look is not None:
                look(store, client, effect)
            with store.transaction() as conn:
                if module.done_already(conn, effect):
                    count += 1 if _done(conn, effect) else 0
                    continue
        else:
            moot = getattr(module, "undo_moot", None)
            if moot is not None:
                with store.transaction() as conn:
                    if moot(conn, effect):
                        _moot(conn, effect)
                        continue
        version: int = module.version_now()
        try:
            said: Row = module.carry_out(client, effect)
        except Exception as exc:  # noqa: BLE001 - the source down or refusing the call: tried again later
            with store.transaction() as conn:
                count += _failed_call(conn, effect, str(exc) or type(exc).__name__, at)
            continue
        with store.transaction() as conn:
            count += _record(conn, module, effect, said, version)
    return count


def _done(conn: "sqlite3.Connection", effect: Row) -> bool:
    return bool(
        conn.execute(
            "UPDATE effects SET state='done', last_error=NULL, next_try_at=NULL, updated_at=? WHERE id=? AND state='owed'",
            (_h().now(), effect["id"]),
        ).rowcount
    )


def _moot(conn: "sqlite3.Connection", effect: Row) -> None:
    conn.execute(
        "UPDATE effects SET state='moot', next_try_at=NULL, updated_at=? WHERE id=? AND state='owed'",
        (_h().now(), effect["id"]),
    )


def _failed_call(conn: "sqlite3.Connection", effect: Row, reason: str, now: str) -> int:
    """A call that failed: tried again after RETRY_S; after TRIES failed calls the action is `failed`."""
    H = _h()
    tries = int(effect["tries"] or 0) + 1
    if tries < TRIES:
        conn.execute(
            "UPDATE effects SET tries=?, last_error=?, next_try_at=?, updated_at=? WHERE id=? AND state='owed'",
            (tries, reason[:500], H.shift(now, seconds=RETRY_S[tries - 1]), H.now(), effect["id"]),
        )
        return 0
    return _give_up(conn, effect, tries, reason)


def _give_up(conn: "sqlite3.Connection", effect: Row, tries: int, reason: str) -> int:
    """The action is `failed`; its source says what that means for the task (`gave_up`)."""
    written = conn.execute(
        "UPDATE effects SET state='failed', tries=?, last_error=?, next_try_at=NULL, updated_at=? "
        "WHERE id=? AND state='owed'",
        (tries, reason[:500], _h().now(), effect["id"]),
    ).rowcount
    if not written:
        return 0
    module = _source(str(effect["source"]))
    if module is not None:
        module.gave_up(conn, effect, reason)
    return 1


def _record(conn: "sqlite3.Connection", module: Any, effect: Row, said: Row, version: int) -> int:
    """Record what the source said to one action; the source saves what it means (`recorded`)."""
    found = conn.execute("SELECT state FROM effects WHERE id=?", (effect["id"],)).fetchone()
    state = str(found[0]) if found else "moot"
    if not said.get("ok"):
        if state != "owed":
            return 0
        return _give_up(conn, effect, int(effect["tries"] or 0) + 1, str(said.get("reason") or "The source said no"))
    conn.execute(
        "UPDATE effects SET state=CASE WHEN state='owed' THEN 'done' ELSE state END, tries=tries+1, last_error=NULL, "
        "next_try_at=NULL, updated_at=? WHERE id=?",
        (_h().now(), effect["id"]),
    )
    if module.recorded(conn, effect, said, state, version) and effect["kind"] == ACT and state == "moot":
        _owe_undo(conn, effect)  # the task left the stage while the source acted: undo it
    return 1
