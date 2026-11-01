"""Removing a person added by mistake (a typo'd address, a test person) for good.

Mark as left keeps a person and their history forever. Remove is for a left person who has none: no row anywhere
names them except their own roster and `humans` rows and what goes with those (a never-used API token, a sent
invite, their address on the allow list). Anything else that names them, as `human:<id>` or their bare id, is
history and keeps them as left: something they did (an event they are the actor of, a message, a task, an
approval, a meeting, a token used once), or something someone filed for them. A directory-synced person stays too:
the next sync would only bring them back.

`history` reads every table, so it runs in a read snapshot before the write; the write rechecks the indexed
kinds (`recent_history`) that could have appeared in between, and SQLite's foreign keys refuse the rest. The
People tab shows Remove only when the indexed check passes, so the full read is paid only on the click.
"""

import json

from . import access as Access
from . import hubdb as H
from .store import Problem

KEEPS = "Has history; stays as left"
# Where a row naming them is not history: the rows `remove` deletes, the request cache, and their principal (the
# `human:<id>` identity that the humans trigger tombstones on delete). Events about them (person.added,
# person.updated) name their bare id as the target, so `history` reads a target only as `human:<id>`.
SKIP_TABLES = ("humans", "idempotency", "principals")
OWN_COLUMNS = {("invite_sends", "person"), ("human_tokens", "human")}
# Settings that name them and are rewritten by `remove`.
OWN_SETTINGS = (Access.ACCESS, "people", "invites")


def _ids(pid):
    return pid, "human:" + pid


def recent_history(c, pid):
    """The indexed kinds of history: what they did (events, tasks, messages), a token they used, a sign-in."""
    ids = _ids(pid)
    checks = ("SELECT 1 FROM events WHERE actor IN (?,?)",
              "SELECT 1 FROM tasks WHERE owner_id IN (?,?)", "SELECT 1 FROM tasks WHERE requester_id IN (?,?)",
              "SELECT 1 FROM messages WHERE recipient_id IN (?,?)")
    if any(c.execute(sql + " LIMIT 1", ids).fetchone() for sql in checks):
        return True
    return bool(c.execute("SELECT 1 FROM human_tokens WHERE human=? AND last_used_at IS NOT NULL", (pid,)).fetchone()
                or c.execute("SELECT 1 FROM oidc_sessions WHERE human=?", (pid,)).fetchone())


def history(c, pid):
    """The first `table.column` that names `pid` outside what `remove` deletes, or ''. Every column is read for
    their bare id or `human:<id>`, and for `"human:<id>"` inside JSON text."""
    from .devcopy import data_tables, quote
    ids = _ids(pid)
    quoted = '"' + ids[1] + '"'
    if c.execute("SELECT 1 FROM human_tokens WHERE human=? AND last_used_at IS NOT NULL", (pid,)).fetchone():
        return "human_tokens.last_used_at"
    for table in sorted(set(data_tables(c)) - set(SKIP_TABLES)):
        columns = [row[1] for row in c.execute(f"PRAGMA table_info({quote(table)})")
                   if (table, row[1]) not in OWN_COLUMNS]
        clauses, args = [], []
        for col in columns:
            if (table, col) == ("events", "target"):
                clauses.append(f"{quote(col)} = ?"); args.append(ids[1])
            else:
                clauses.append(f"{quote(col)} IN (?,?) OR instr({quote(col)}, ?) > 0"); args += [*ids, quoted]
        if not clauses:
            continue
        sql = f"SELECT * FROM {quote(table)} WHERE ({' OR '.join(clauses)})"
        if table == "registry_metadata":
            sql += f" AND key NOT IN ({','.join('?' * len(OWN_SETTINGS))})"
            args += OWN_SETTINGS
        row = c.execute(sql + " LIMIT 1", args).fetchone()
        if row:
            hit = next((col for col in columns if row[col] in ids or quoted in str(row[col] or "")), columns[0])
            return f"{table}.{hit}"
    return ""


def check(person):
    """Why `person` (a roster row) cannot be removed whatever their history, or ''."""
    if not person.get("hidden"):
        return "Mark them as left first"
    if person.get("directory") or person.get("external_id"):
        return "Synced from your directory; stays as left"
    return ""


def removable(c, person):
    """Whether the People tab offers Remove: the cheap checks; the click runs `history` as well."""
    return not check(person) and not recent_history(c, person["id"])


def remove(c, actor, roster, person):
    """Delete `person` and what goes with them, in the caller's transaction. Refuses when history appeared since
    `history` was read. The audit event keeps their name and address, since nothing else does."""
    pid = person["id"]
    if check(person):
        raise Problem("not_removable", check(person), 409)
    if recent_history(c, pid):
        raise Problem("has_history", KEEPS, 409)
    people = [p for p in roster["people"] if p["id"] != pid]
    people = [{**p, "reports_to": person.get("reports_to") or ""} if p.get("reports_to") == pid else p for p in people]
    Access.save_roster(c, {**roster, "people": people})
    c.execute("DELETE FROM human_tokens WHERE human=?", (pid,))
    c.execute("DELETE FROM invite_sends WHERE person=?", (pid,))
    c.execute("DELETE FROM humans WHERE id=?", (pid,))
    from . import invites
    sent = invites.sent(c)
    if pid in sent:
        sent.pop(pid)
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(sent), invites.KEY))
    email = person.get("email") or ""
    stored = Access._load_json(c, Access.ACCESS)
    if email and stored and (email in Access.emails(stored.get("allowed")) or email in Access.emails(stored.get("admins"))):
        # A removed address no longer signs in on its own; the revision moves so an open People tab reloads first.
        Access._store(c, Access.ACCESS, {**stored, "allowed": [e for e in Access.emails(stored.get("allowed")) if e != email],
                                         "admins": [e for e in Access.emails(stored.get("admins")) if e != email],
                                         "revision": int(stored.get("revision") or 0) + 1, "updated_at": H.now(),
                                         "updated_by": str(actor)})
    H.event(c, actor, "person.removed", pid, {"name": person.get("name") or "", "email": email})
