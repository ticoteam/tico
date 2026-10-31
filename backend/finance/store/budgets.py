"""SQL for budgets, their versions, holds, allocations, projections and the threshold events they raise."""
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ..core.budgets import Budget, Version
from .ledger import new_id


def _budget(r: Any) -> Budget:
    return Budget(id=r["id"], book_id=r["book_id"], name=r["name"], period=r["period"], account_id=r["account_id"] or "",
                  catch_all=bool(r["catch_all"]), team=r["team"], teammate=r["teammate"], vendor=r["vendor"],
                  parent_id=r["parent_id"] or "", owner=r["owner"], warn_pct=int(r["warn_pct"]), at_limit=r["at_limit"],
                  rollover=r["rollover"], active=bool(r["active"]), created_at=r["created_at"])


def budgets(c: sqlite3.Connection, book_id: str) -> dict[str, Budget]:
    """Every budget in the book, active or not (a tree is small: dozens of rows)."""
    return {r["id"]: _budget(r) for r in c.execute("SELECT * FROM fin_budgets WHERE book_id=? ORDER BY created_at, id",
                                                    (book_id,))}


def insert_budget(c: sqlite3.Connection, b: Budget, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_budgets(id, book_id, name, owner, period, account_id, catch_all, team, teammate, vendor, "
              "parent_id, warn_pct, at_limit, rollover, active, created_at, created_by, updated_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (b.id, b.book_id, b.name, b.owner, b.period, b.account_id or None, int(b.catch_all), b.team, b.teammate,
               b.vendor, b.parent_id or None, b.warn_pct, b.at_limit, b.rollover, int(b.active), at, by, at))


def update_budget(c: sqlite3.Connection, b: Budget, at: str) -> None:
    c.execute("UPDATE fin_budgets SET name=?, owner=?, warn_pct=?, at_limit=?, rollover=?, active=?, updated_at=? "
              "WHERE id=?", (b.name, b.owner, b.warn_pct, b.at_limit, b.rollover, int(b.active), at, b.id))


def versions(c: sqlite3.Connection, budget_ids: Sequence[str]) -> dict[str, list[Version]]:
    out: dict[str, list[Version]] = {bid: [] for bid in budget_ids}
    if not budget_ids:
        return out
    marks = ",".join("?" * len(budget_ids))
    for r in c.execute(f"SELECT budget_id, version, effective_from, amount FROM fin_budget_versions "
                       f"WHERE budget_id IN ({marks}) ORDER BY version", list(budget_ids)):
        out[r["budget_id"]].append(Version(budget_id=r["budget_id"], version=int(r["version"]),
                                           effective_from=r["effective_from"], amount=int(r["amount"])))
    return out


def insert_version(c: sqlite3.Connection, budget_id: str, effective_from: str, amount: int, currency: str, by: str,
                   at: str) -> int:
    row = c.execute("SELECT COALESCE(MAX(version), 0) FROM fin_budget_versions WHERE budget_id=?",
                    (budget_id,)).fetchone()
    number = int(row[0]) + 1
    c.execute("INSERT INTO fin_budget_versions(id, budget_id, version, effective_from, amount, currency, created_at, "
              "created_by) VALUES(?,?,?,?,?,?,?,?)", (new_id(), budget_id, number, effective_from, amount, currency,
                                                     at, by))
    return number


def seeded_version(c: sqlite3.Connection, budget_id: str) -> bool:
    """Whether the budget's only version is setup's copy of the usage cap: written with the budget itself, by the same
    writer at the same moment, on a budget no `budget_create` action made (a person's new budget with an amount is
    written the same way, and leaves that action). Any version a person set later comes at a later moment."""
    rows = c.execute("SELECT v.created_at = b.created_at AND v.created_by = b.created_by FROM fin_budget_versions v "
                     "JOIN fin_budgets b ON b.id = v.budget_id WHERE v.budget_id=? LIMIT 2", (budget_id,)).fetchall()
    if len(rows) != 1 or not rows[0][0]:
        return False
    return c.execute("SELECT 1 FROM fin_actions WHERE kind='budget_create' "
                     "AND json_extract(result_json, '$.budget_id')=? LIMIT 1", (budget_id,)).fetchone() is None


def drop_versions(c: sqlite3.Connection, budget_id: str) -> None:
    """Remove a budget's versions and the warn/cutoff events they raised, so its next amount starts at v1, armed."""
    c.execute("DELETE FROM fin_budget_versions WHERE budget_id=?", (budget_id,))
    c.execute("DELETE FROM fin_events WHERE subject=? AND name LIKE 'finance.budget.%'", (budget_id,))


# ------------------------------------------------------------------------------------------------ holds
# The period sums read only the period's live and settled holds through fin_holds_budget (budget_id, status, held_on),
# never a budget's whole history of released ones; INDEXED BY keeps the planner off the target index as targets grow.
LIVE = "status IN ('held','settled')"


def held(c: sqlite3.Connection, budget_ids: Sequence[str], start: str, end: str, target: str = "") -> int:
    """What holds placed in the window reserve on these budgets (for one `target` when given): active holds at their
    amount, and settled ones no entry has absorbed yet at what they settled for (a run's priced tokens before its
    daily posting, or a run on the company's own keys, whose expense arrives in a card feed)."""
    if not budget_ids:
        return 0
    marks = ",".join("?" * len(budget_ids))
    sql = (f"SELECT COALESCE(SUM(CASE WHEN status='held' THEN amount WHEN status='settled' AND entry_id IS NULL "
           f"THEN settled_amount ELSE 0 END), 0) FROM fin_holds INDEXED BY fin_holds_budget "
           f"WHERE budget_id IN ({marks}) AND {LIVE} AND held_on>=? AND held_on<=?")
    args: list[object] = [*budget_ids, start, end]
    if target:
        sql += " AND target=?"
        args.append(target)
    return int(c.execute(sql, args).fetchone()[0])


def target_used(c: sqlite3.Connection, budget_ids: Sequence[str], start: str, end: str, target: str,
                absorbed: bool) -> int:
    """What a target's holds placed in the window used on these budgets: live holds at their amount, settled ones at
    what they settled for (with `absorbed`, also those an entry has taken in, for targets no line is tagged with)."""
    if not budget_ids:
        return 0
    marks = ",".join("?" * len(budget_ids))
    settled = "status='settled'" if absorbed else "status='settled' AND entry_id IS NULL"
    return int(c.execute(f"SELECT COALESCE(SUM(CASE WHEN status='held' THEN amount WHEN {settled} THEN settled_amount "
                         f"ELSE 0 END), 0) FROM fin_holds INDEXED BY fin_holds_budget WHERE budget_id IN ({marks}) "
                         f"AND {LIVE} AND target=? AND held_on>=? AND held_on<=?",
                         [*budget_ids, target, start, end]).fetchone()[0])


def resize_hold(c: sqlite3.Connection, hold_id: str, amount: int) -> int:
    return c.execute("UPDATE fin_holds SET amount=? WHERE id=? AND status='held'", (amount, hold_id)).rowcount


def insert_hold(c: sqlite3.Connection, *, budget_id: str, amount: int, currency: str, source: str, source_id: str,
                target: str, held_on: str, expires_at: str | None, by: str, at: str) -> str:
    hid = new_id()
    c.execute("INSERT INTO fin_holds(id, budget_id, amount, currency, source, source_id, target, held_on, status, "
              "expires_at, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,'held',?,?,?)",
              (hid, budget_id, amount, currency, source, source_id, target, held_on, expires_at, at, by))
    return hid


def hold(c: sqlite3.Connection, hold_id: str) -> Any:
    return c.execute("SELECT * FROM fin_holds WHERE id=?", (hold_id,)).fetchone()


def hold_for_source(c: sqlite3.Connection, source: str, source_id: str) -> Any:
    """The live hold an earlier call placed for the same source, so a retried claim never holds twice."""
    return c.execute("SELECT * FROM fin_holds WHERE source=? AND source_id=? AND status='held' ORDER BY created_at "
                     "LIMIT 1", (source, source_id)).fetchone()


def holds_for_source(c: sqlite3.Connection, source: str, source_id: str) -> list[str]:
    return [str(r[0]) for r in c.execute("SELECT id FROM fin_holds WHERE source=? AND source_id=? AND status='held'",
                                         (source, source_id))]


def settle(c: sqlite3.Connection, hold_id: str, amount: int, entry_id: str | None, at: str) -> int:
    """Any amount, over the hold or the budget included: a provider has already charged it."""
    return c.execute("UPDATE fin_holds SET status='settled', settled_amount=?, entry_id=?, settled_at=? "
                     "WHERE id=? AND status='held'", (amount, entry_id, at, hold_id)).rowcount


def absorb(c: sqlite3.Connection, hold_id: str, entry_id: str) -> int:
    """A settled hold's entry arrives: from now the posted line counts instead of the hold."""
    return c.execute("UPDATE fin_holds SET entry_id=? WHERE id=? AND status='settled' AND entry_id IS NULL",
                     (entry_id, hold_id)).rowcount


def release(c: sqlite3.Connection, hold_id: str, at: str) -> int:
    return c.execute("UPDATE fin_holds SET status='released', released_at=? WHERE id=? AND status='held'",
                     (at, hold_id)).rowcount


def expired(c: sqlite3.Connection, at: str, most: int) -> list[str]:
    return [str(r[0]) for r in c.execute("SELECT id FROM fin_holds WHERE status='held' AND expires_at IS NOT NULL "
                                         "AND expires_at<=? ORDER BY expires_at LIMIT ?", (at, most))]


# ------------------------------------------------------------------------------------------------ allocations
def allocation(c: sqlite3.Connection, budget_id: str, target_kind: str, target_id: str) -> Any:
    return c.execute("SELECT * FROM fin_allocations WHERE budget_id=? AND target_kind=? AND target_id=?",
                     (budget_id, target_kind, target_id)).fetchone()


def allocations(c: sqlite3.Connection, budget_ids: Sequence[str]) -> list[Any]:
    if not budget_ids:
        return []
    marks = ",".join("?" * len(budget_ids))
    return c.execute(f"SELECT * FROM fin_allocations WHERE budget_id IN ({marks}) AND amount>0 ORDER BY created_at",
                     list(budget_ids)).fetchall()


def upsert_allocation(c: sqlite3.Connection, *, budget_id: str, target_kind: str, target_id: str, amount: int,
                      currency: str, by: str, at: str) -> str:
    row = allocation(c, budget_id, target_kind, target_id)
    if row:
        c.execute("UPDATE fin_allocations SET amount=?, updated_at=? WHERE id=?", (amount, at, row["id"]))
        return str(row["id"])
    aid = new_id()
    c.execute("INSERT INTO fin_allocations(id, budget_id, target_kind, target_id, amount, currency, created_at, "
              "created_by, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (aid, budget_id, target_kind, target_id, amount, currency, at, by, at))
    return aid


# ------------------------------------------------------------------------------------------------ projections
def upsert_projection(c: sqlite3.Connection, *, book_id: str, account_id: str, month: str, team: str, teammate: str,
                      amount: int, currency: str, method: str, by: str, at: str) -> str:
    row = c.execute("SELECT id FROM fin_projections WHERE book_id=? AND account_id=? AND month=? AND team=? "
                    "AND teammate=?", (book_id, account_id, month, team, teammate)).fetchone()
    if row:
        c.execute("UPDATE fin_projections SET amount=?, method=?, set_by=?, as_of=? WHERE id=?",
                  (amount, method, by, at, row["id"]))
        return str(row["id"])
    pid = new_id()
    c.execute("INSERT INTO fin_projections(id, book_id, account_id, month, team, teammate, amount, currency, method, "
              "set_by, as_of, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (pid, book_id, account_id, month, team, teammate, amount, currency, method, by, at, at, by))
    return pid


def projections(c: sqlite3.Connection, book_id: str, first: str, last: str) -> list[Any]:
    return c.execute("SELECT account_id, month, team, teammate, amount, method FROM fin_projections WHERE book_id=? "
                     "AND month>=? AND month<=?", (book_id, first, last)).fetchall()


# ------------------------------------------------------------------------------------------------ events
def event(c: sqlite3.Connection, name: str, subject: str, period_key: str, payload: Mapping[str, object], by: str,
          at: str) -> bool:
    """A `finance.*` event for Tico's triggers, once per (name, subject, period). Whether it was new."""
    return c.execute("INSERT INTO fin_events(id, name, subject, period_key, payload_json, created_at, created_by) "
                     "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name, subject, period_key) DO NOTHING",
                     (new_id(), name, subject, period_key, json.dumps(dict(payload), sort_keys=True, default=str),
                      at, by)).rowcount > 0


def unlink_allocation_holds(c: sqlite3.Connection) -> int:
    """Clear every allocation's hold link (a dev copy's scrub, which empties the holds)."""
    return c.execute("UPDATE fin_allocations SET hold_id=NULL WHERE hold_id IS NOT NULL").rowcount
