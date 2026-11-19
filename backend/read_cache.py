"""Reuse neutral rows only within a borrowed connection's current snapshot.

No permission result survives a request. Writes invalidate even inside a transaction;
ending a snapshot or returning a connection to the pool discards its inputs too. The whole
roster tables and the task steps are shared between snapshots, at an unchanged count (backend/roster_clock.py).
"""

import sqlite3
from collections.abc import Callable, Hashable, Iterable, Mapping
from copy import deepcopy
from typing import Any, TypeVar

from . import bot_settings as BS
from . import roster_clock

T = TypeVar("T")


def cache(c: Any) -> dict[Any, Any] | None:
    values: dict[Any, Any] | None = getattr(c, 'read_cache', None)
    if values is None:
        return None
    if not c.in_transaction:
        values.clear()
        return None
    if values.get('changes') != c.total_changes:
        values.clear()
        values['changes'] = c.total_changes
    return values


def rows(c, statement, args=()):
    values = cache(c)
    key = (statement, tuple(args))
    if not args and statement in roster_clock.STATEMENTS:
        # Shared across snapshots, so a read outside a transaction (most request reads) shares them too.
        if values is None:
            return roster_clock.rows(c, statement)
        if key not in values:
            values[key] = roster_clock.rows(c, statement)
        return values[key]
    if values is None:
        return tuple(c.execute(statement, args))
    if key not in values:
        values[key] = tuple(c.execute(statement, args))
    return values[key]


def configs(c, slugs=None):
    statement = BS.ROW_SQL
    values = cache(c)
    full = values.get((statement, ())) if values is not None else None
    if slugs is None:
        return rows(c, statement)
    wanted = set(slugs)
    if full is not None:
        return tuple(row for row in full if row['bot'] in wanted)
    out = []
    ordered = sorted(wanted)
    for start in range(0, len(ordered), 500):
        part = ordered[start : start + 500]
        out.extend(rows(c, statement + ' WHERE bc.bot IN (' + ','.join('?' * len(part)) + ')', part))
    return tuple(out)


def metadata(c, key):
    found = rows(c, 'SELECT value FROM registry_metadata WHERE key=?', (key,))
    return found[0] if found else None


def value(c: Any, key: Hashable, build: Callable[[], T], shared: bool = False) -> T:
    """The snapshot's value for `key`. A caller that enriches the result gets its own copy; one that only reads it
    (`shared=True`) gets the snapshot's object, so it must never change it: a copy of a roster and every bot's
    configuration costs more than the read it saves, and a request asks for them several times."""
    values = cache(c)
    if values is None:
        return build()
    if key not in values:
        values[key] = build()
    # Callers enrich roster/config dictionaries; their changes must not become cached policy.
    return values[key] if shared else deepcopy(values[key])


def one(c: Any, statement: str, args: Iterable[Any] = ()) -> Any:
    """One neutral stored row; never cache a mutation or an answer outside its snapshot."""
    values = cache(c) if statement.lstrip()[:7].upper() == "SELECT " else None
    key = ("one", statement, tuple(args))
    if values is None:
        return c.execute(statement, args).fetchone()
    if key not in values:
        values[key] = c.execute(statement, args).fetchone()
    return values[key]


def one_dict(c: Any, statement: str, args: Iterable[Any] = ()) -> dict[str, Any] | None:
    """`one` as a dict of the caller's own, or None for no row (hubdb._one). A stored row (sqlite3.Row) is turned into
    a dict once a snapshot and each caller gets a copy of that: a Row's values are plain (text, numbers, bytes, None),
    so a shallow copy is a whole one, and copying a dict costs a fraction of building one from a Row (a task's 43
    columns: 6 µs a Row, 0.1 µs a dict). A list asks for each task, its visit, stage and pipeline several times. A row a
    page primed as a dict is copied as before."""
    args = tuple(args)
    row = one(c, statement, args)
    if row is None:
        return None
    if not isinstance(row, sqlite3.Row):
        return dict(row)
    values = cache(c)
    if values is None:
        return dict(row)
    key = ("one_dict", statement, args)
    kept = values.get(key)
    if kept is None or kept[0] is not row:  # the row this dict was made from, not one read since
        kept = values[key] = (row, dict(row))
    return dict(kept[1])


def prime(c: Any, statement: str, found: Mapping[tuple[Any, ...], Any]) -> None:
    """Seed `one` with rows read together in this snapshot, {args: row or None}, so a loop of one-row lookups that
    follows reads none of them again. A row already cached stays as it is."""
    values = cache(c)
    if values is None:
        return
    for args, row in found.items():
        values.setdefault(("one", statement, tuple(args)), row)


def prime_rows(c: Any, statement: str, found: Mapping[tuple[Any, ...], Iterable[Any]]) -> None:
    """Seed `rows` as `prime` seeds `one`: {args: the rows that statement returns for them}."""
    values = cache(c)
    if values is None:
        return
    for args, value in found.items():
        values.setdefault((statement, tuple(args)), tuple(value))
