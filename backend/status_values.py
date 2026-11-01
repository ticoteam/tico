"""The values each stored status or state column may hold, and how the database refuses anything else.

docs/status-and-state.md has the table: what each value means, and the transitions the code makes. This module is the
list the database enforces. Two kinds of enforcement, both refusing a value outside the list:

- `trigger`: BEFORE INSERT and BEFORE UPDATE OF <column> triggers that RAISE(ABORT, '<table>.<column>: invalid value').
  Used for the hot and growing tables (tasks, jobs, attempts, automation_runs, webhook_sends), where rebuilding the
  table to add a CHECK would copy every row.
- `check`: a CHECK constraint, installed by rebuilding a small table (`_rebuild_with_check`).

Changing a list is a code change here. `enforce` compares what is installed with what this module says and replaces
it when they differ, so the next start applies a new list without a separate migration. A list that drops a value
still in use is refused by `upgrade`, which is migration 71 (hubdb.migrate): it counts the stored values outside the
list and stops, naming them, before it rewrites anything.

Retired spellings are rewritten by `upgrade` (one spelling per meaning). Each retired value is accepted by the
precheck and mapped to its replacement, never kept as a second spelling.
"""

import re
from contextlib import contextmanager

COLOURS = ("red", "yellow", "green")
BOT_STATES = ("active", "paused", "planned", "quarantined", "archived")
# bot_status.state: what the bot is doing now (hubdb.status_set). "active" is an input to status_set, never stored.
BOT_STATUS_STATES = ("idle", "running", "waiting_human", "waiting_bot", "blocked", "limited", "crashed", "paused",
                     "quarantined")
TASK_STATUSES = ("open", "doing", "waiting", "review", "ready", "done", "closed", "declined")
JOB_STATES = ("queued", "leased", "running", "input", "completed", "failed", "uncertain", "canceled")
ATTEMPT_STATES = ("leased", "running", "input", "completed", "failed", "interrupted", "expired")
RUN_STATUSES = ("queued", "running", "waiting", "ok", "failed", "empty", "skipped", "held")
WEBHOOK_SEND_STATES = ("ready", "sending", "delivered", "failed", "canceled", "withheld")
PAYMENT_STATUSES = ("queued", "sending", "awaiting_bank_approval", "submitted", "settled", "returned", "failed",
                    "canceled", "unknown")
TRANSFER_STATUSES = ("queued", "submitted", "unknown", "failed", "posted", "canceled")
GOOGLE_CONNECT_STATUSES = ("signing_in", "ready", "finishing", "revoking", "disconnecting", "connected",
                           "disconnected", "failed")


class StatusValuesError(RuntimeError):
    """A stored value the list does not allow: the upgrade stops before it changes anything."""


class Column:
    """One stored status or state column. `values` is the list; `retired` maps old spellings to the current one."""

    def __init__(self, table, column, values, *, nullable=False, enforce="trigger", retired=()):
        self.table, self.column, self.values = table, column, tuple(values)
        self.nullable, self.enforce, self.retired = nullable, enforce, tuple(retired)

    @property
    def accepted(self):
        """What the upgrade's precheck lets through: the list plus the retired spellings it maps."""
        return self.values + tuple(old for old, _new in self.retired)

    def in_list(self, values):
        return ", ".join("'" + v.replace("'", "''") + "'" for v in values)

    @property
    def check_name(self):
        return f"status_values_{self.column}"

    def check_expr(self):
        # NULL passes a CHECK, so a nullable column needs no extra term.
        return f"{self.column} IN ({self.in_list(self.values)})"


# Hot and growing tables use triggers; small tables use a CHECK rebuilt into the table.
COLUMNS = (
    Column("tasks", "status", TASK_STATUSES, nullable=True),
    Column("tasks", "blocked_from_status", TASK_STATUSES, nullable=True),
    Column("jobs", "state", JOB_STATES, retired=(("cancelled", "canceled"),)),
    Column("attempts", "state", ATTEMPT_STATES),
    Column("automation_runs", "status", RUN_STATUSES),
    Column("webhook_sends", "state", WEBHOOK_SEND_STATES, retired=(("cancelled", "canceled"),)),
    Column("bots", "state", BOT_STATES, nullable=True, enforce="check"),
    # needs_onboarding is the spelling migration 45 rewrote (backend/statuses.py); a stale row maps, it does not abort.
    Column("bot_config", "onboarding_state", ("needs_setup", "onboarded"), nullable=True, enforce="check",
           retired=(("needs_onboarding", "needs_setup"),)),
    Column("bot_status", "state", BOT_STATUS_STATES, nullable=True, enforce="check"),
    Column("automation_versions", "status", ("active", "pending"), enforce="check"),
    Column("goals", "status", COLOURS + ("gray", "done", "dropped"), nullable=True, enforce="check"),
    Column("goals", "status_source", ("person", "auto"), nullable=True, enforce="check"),
    Column("goals", "suggest_status", COLOURS, nullable=True, enforce="check"),
    Column("goal_proposals", "status", ("pending", "confirmed", "rejected"), enforce="check"),
    Column("bot_files", "state", ("ready", "deleted", "not_synced"), enforce="check"),
    Column("approvals", "decision", ("approved", "declined", "withdrawn"), nullable=True, enforce="check"),
    Column("doc_sources", "status", ("link_only", "queued", "preparing", "updating", "ready", "empty", "failed",
                                     "access_needed"), enforce="check"),
    Column("model_subscriptions", "status", ("new", "working", "needs_reconnect"), enforce="check"),
    Column("strategy_actions", "state", ("proposed", "done", "undone"), enforce="check"),
    Column("google_connects", "status", GOOGLE_CONNECT_STATUSES, enforce="check"),
    Column("fin_payments", "status", PAYMENT_STATUSES, enforce="check", retired=(("cancelled", "canceled"),)),
    Column("fin_transfers", "status", TRANSFER_STATUSES, enforce="check", retired=(("cancelled", "canceled"),)),
)


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def present(conn):
    """The specs whose table and column exist in this file (a column a later step adds is not there yet)."""
    return [spec for spec in COLUMNS if spec.column in _columns(conn, spec.table)]


def unknown_values(conn):
    """(table, column, value, rows) for every stored value outside the accepted list, NULL included where refused."""
    found = []
    for spec in present(conn):
        accepted = spec.in_list(spec.accepted)
        rows = conn.execute(
            f"SELECT {spec.column}, COUNT(*) FROM {spec.table} WHERE {spec.column} NOT IN ({accepted}) "
            f"{'AND ' + spec.column + ' IS NOT NULL' if spec.nullable else ''} GROUP BY {spec.column}").fetchall()
        if not spec.nullable:
            nulls = conn.execute(f"SELECT COUNT(*) FROM {spec.table} WHERE {spec.column} IS NULL").fetchone()[0]
            if nulls:
                found.append((spec.table, spec.column, None, nulls))
        found.extend((spec.table, spec.column, value, n) for value, n in rows)
    return found


def upgrade(conn):
    """Migration 71. Refuse stored values outside the lists (naming them), then rewrite retired spellings and
    install the constraints. Runs inside the migration's transaction, with foreign keys already off."""
    problems = unknown_values(conn)
    if problems:
        lines = [f"  {t}.{c} = {v!r} ({n} rows)" if v is not None else f"  {t}.{c} IS NULL ({n} rows)"
                 for t, c, v, n in problems]
        raise StatusValuesError("status values outside their lists; map or fix these rows, then start again:\n"
                                + "\n".join(lines))
    for spec in present(conn):
        # CHECK columns are mapped while their table is copied (_rebuild_with_check), not by an UPDATE the old CHECK
        # would refuse.
        if spec.enforce == "trigger":
            for old, new in spec.retired:
                conn.execute(f"UPDATE {spec.table} SET {spec.column}=? WHERE {spec.column}=?", (new, old))
    _enforce(conn)


def enforce(conn):
    """Install or refresh every constraint in the lists. Idempotent: a file that has them does nothing. Safe on a
    fresh file, where the tables come from later schema steps. A table rebuild needs foreign keys off (see
    _rebuild_with_check); triggers need nothing."""
    _enforce(conn)


def _enforce(conn):
    for spec in present(conn):
        if spec.enforce == "trigger":
            _ensure_triggers(conn, spec)
        else:
            _ensure_check(conn, spec)


def _ensure_triggers(conn, spec):
    lst = spec.in_list(spec.values)
    if spec.nullable:
        when = f"NEW.{spec.column} IS NOT NULL AND NEW.{spec.column} NOT IN ({lst})"
    else:
        when = f"NEW.{spec.column} IS NULL OR NEW.{spec.column} NOT IN ({lst})"
    message = f"{spec.table}.{spec.column}: invalid value"
    for event, name in (("INSERT", "ins"), ("UPDATE OF " + spec.column, "upd")):
        trigger = f"status_values_{spec.table}_{spec.column}_{name}"
        sql = (f"CREATE TRIGGER {trigger} BEFORE {event} ON {spec.table} WHEN {when} "
               f"BEGIN SELECT RAISE(ABORT, '{message}'); END")
        have = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)).fetchone()
        if have is not None and have[0] == sql:
            continue
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute(sql)


def _ensure_check(conn, spec):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (spec.table,)).fetchone()[0]
    wanted = f"CONSTRAINT {spec.check_name} CHECK ({spec.check_expr()})"
    if wanted in sql and not _inline_check(spec, sql):
        return
    _rebuild_with_check(conn, spec, wanted)


def _named_check(spec):
    """The table-level constraint this module wrote for the column, whatever list it carries."""
    return re.compile(rf",?\s*CONSTRAINT {re.escape(spec.check_name)} CHECK \((?:[^()]|\([^()]*\))*\)")


def _inline_check(spec, sql):
    """True when the table still has an inline CHECK on this column from an older definition (fin_payments)."""
    return re.search(rf"CHECK \(\s*{re.escape(spec.column)} IN \(", _named_check(spec).sub("", sql)) is not None


@contextmanager
def _foreign_keys_off(conn):
    """A table rebuild drops the old table; with foreign keys on, that would cascade into the child rows. Turn them off
    for the rebuild (only possible outside a transaction, so this is a no-op inside one: the caller turned them off)."""
    prior = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if prior:
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        yield
    finally:
        if prior:
            conn.execute("PRAGMA foreign_keys=ON")


def _rebuild_with_check(conn, spec, constraint):
    """Rebuild `spec.table` with `constraint` added, copying every row with retired spellings mapped. The table is
    renamed aside rather than dropped first, with legacy ALTER TABLE rules, so tables that refer to it (foreign keys,
    triggers) keep their text and nothing cascades; the new table takes the old one's SQL, columns, indexes and
    triggers. The row count must match before the old table goes."""
    if conn.in_transaction and conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        # Dropping the old table inside a transaction with foreign keys on would cascade into child rows.
        raise RuntimeError(f"{spec.table}: a rebuild for {spec.check_name} needs foreign keys off outside a transaction")
    with _foreign_keys_off(conn):
        own = not conn.in_transaction
        if own:
            conn.execute("BEGIN IMMEDIATE")
        legacy = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
        conn.execute("PRAGMA legacy_alter_table=ON")
        try:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                               (spec.table,)).fetchone()[0]
            body = _named_check(spec).sub("", sql)
            body = re.sub(rf"\s*CHECK \(\s*{re.escape(spec.column)} IN \([^)]*\)\s*\)", "", body).rstrip()
            if not body.endswith(")"):
                raise RuntimeError(f"{spec.table}: unexpected table definition, cannot add {spec.check_name}")
            new_sql = re.sub(rf"^CREATE TABLE\s+\"?{spec.table}\"?\s*\(", f"CREATE TABLE {spec.table} (",
                             body[:-1].rstrip(), count=1) + f",\n {constraint}\n)"
            if not new_sql.startswith(f"CREATE TABLE {spec.table} ("):
                raise RuntimeError(f"{spec.table}: cannot read the table definition for rebuild")
            side = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') "
                                "AND sql IS NOT NULL", (spec.table,)).fetchall()
            cols = _columns(conn, spec.table)
            select, params = [], []
            for c in cols:
                if c == spec.column and spec.retired:
                    cases = " ".join("WHEN ? THEN ?" for _ in spec.retired)
                    select.append(f"CASE {c} {cases} ELSE {c} END")
                    for old, new in spec.retired:
                        params.extend((old, new))
                else:
                    select.append(c)
            before = conn.execute(f"SELECT COUNT(*) FROM {spec.table}").fetchone()[0]
            old = f"{spec.table}__status_old"
            conn.execute(f"ALTER TABLE {spec.table} RENAME TO {old}")
            conn.execute(new_sql)
            conn.execute(f"INSERT INTO {spec.table} ({', '.join(cols)}) SELECT {', '.join(select)} FROM {old}", params)
            after = conn.execute(f"SELECT COUNT(*) FROM {spec.table}").fetchone()[0]
            if after != before:
                raise RuntimeError(f"{spec.table}: rebuild copied {after} of {before} rows")
            conn.execute(f"DROP TABLE {old}")
            for (statement,) in side:
                conn.execute(statement)
        except Exception:
            if own:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute(f"PRAGMA legacy_alter_table={'ON' if legacy else 'OFF'}")
        if own:
            conn.execute("COMMIT")
