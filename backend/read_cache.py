"""Reuse neutral rows only within a borrowed connection's current snapshot.

No permission result survives a request. Writes invalidate even inside a transaction;
ending a snapshot or returning a connection to the pool discards its inputs too.
"""

from copy import deepcopy


def cache(c):
    values = getattr(c, 'read_cache', None)
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
    if values is None:
        return tuple(c.execute(statement, args))
    if key not in values:
        values[key] = tuple(c.execute(statement, args))
    return values[key]


def configs(c, slugs=None):
    statement = 'SELECT * FROM bot_config'
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
        part = ordered[start:start + 500]
        out.extend(rows(c, statement + ' WHERE bot IN (' + ','.join('?' * len(part)) + ')', part))
    return tuple(out)


def metadata(c, key):
    found = rows(c, 'SELECT value_json FROM registry_metadata WHERE key=?', (key,))
    return found[0] if found else None


def value(c, key, build):
    values = cache(c)
    if values is None:
        return build()
    if key not in values:
        values[key] = build()
    # Callers enrich roster/config dictionaries; their changes must not become cached policy.
    return deepcopy(values[key])


def one(c, statement, args=()):
    """One neutral stored row; never cache a mutation or an answer outside its snapshot."""
    values = cache(c) if statement.lstrip().upper().startswith("SELECT ") else None
    key = ("one", statement, tuple(args))
    if values is None:
        return c.execute(statement, args).fetchone()
    if key not in values:
        values[key] = c.execute(statement, args).fetchone()
    return values[key]
