"""SQL for who may do and see what: rights, view defaults, grants, private lines and the view version counter."""
import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from ..core.view import Defaults, Grant, PrivateLine
from .ledger import new_id


# ------------------------------------------------------------------------------------------------ rights
def rights_of(c: sqlite3.Connection, teammate: str, at: str) -> frozenset[str]:
    return frozenset(str(r[0]) for r in c.execute(
        "SELECT right_name FROM fin_rights WHERE teammate=? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at>?)", (teammate, at)))


def grant_right(c: sqlite3.Connection, teammate: str, right: str, granted_by: str, expires_at: str | None,
                at: str) -> str:
    """A right row; an existing live one for the same pair is revoked first, so there is one row in force."""
    revoke_right(c, teammate, right, granted_by, at)
    rid = new_id()
    c.execute("INSERT INTO fin_rights(id, teammate, right_name, granted_by, expires_at, created_at, created_by) "
              "VALUES(?,?,?,?,?,?,?)", (rid, teammate, right, granted_by, expires_at, at, granted_by))
    return rid


def revoke_right(c: sqlite3.Connection, teammate: str, right: str, by: str, at: str) -> int:
    return c.execute("UPDATE fin_rights SET revoked_at=?, revoked_by=? WHERE teammate=? AND right_name=? "
                     "AND revoked_at IS NULL", (at, by, teammate, right)).rowcount


# ------------------------------------------------------------------------------------------------ defaults
def defaults(c: sqlite3.Connection) -> Defaults:
    stored = {str(r[0]): str(r[1]) for r in c.execute("SELECT role, policy FROM fin_view_defaults")}
    base = Defaults()
    return Defaults(manager=stored.get("manager", base.manager), member=stored.get("member", base.member),
                    manager_payroll=stored.get("manager_payroll", base.manager_payroll))


def set_default(c: sqlite3.Connection, role: str, policy: str, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_view_defaults(role, policy, created_at, created_by, updated_at) VALUES(?,?,?,?,?) "
              "ON CONFLICT(role) DO UPDATE SET policy=excluded.policy, updated_at=excluded.updated_at",
              (role, policy, at, by, at))


def seed_defaults(c: sqlite3.Connection, by: str, at: str) -> None:
    base = Defaults()
    for role, policy in (("owner", "everything"), ("manager", base.manager), ("member", base.member),
                         ("manager_payroll", base.manager_payroll)):
        c.execute("INSERT INTO fin_view_defaults(role, policy, created_at, created_by, updated_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(role) DO NOTHING", (role, policy, at, by, at))


# ------------------------------------------------------------------------------------------------ grants
def _grant(r: Any) -> Grant:
    return Grant(id=r["id"], subject_kind=r["subject_kind"], subject=r["subject"], what=r["what"], whose=r["whose"],
                 whose_team=r["whose_team"], account_ids=frozenset(json.loads(r["account_ids"] or "[]")),
                 new_members=bool(r["new_members"]), members=frozenset(json.loads(r["members"] or "[]")),
                 granted_by=r["granted_by"], expires_at=r["expires_at"] or "", status=r["status"])


def grants(c: sqlite3.Connection, status: str = "active") -> list[Grant]:
    return [_grant(r) for r in c.execute("SELECT * FROM fin_view_grants WHERE status=? ORDER BY created_at, id",
                                         (status,))]


def grant(c: sqlite3.Connection, grant_id: str) -> Grant | None:
    row = c.execute("SELECT * FROM fin_view_grants WHERE id=?", (grant_id,)).fetchone()
    return _grant(row) if row else None


def insert_grant(c: sqlite3.Connection, g: Grant, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_view_grants(id, subject_kind, subject, new_members, members, what, account_ids, "
              "whose, whose_team, granted_by, expires_at, status, created_at, created_by) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',?,?)",
              (g.id, g.subject_kind, g.subject, int(g.new_members), json.dumps(sorted(g.members)), g.what,
               json.dumps(sorted(g.account_ids)), g.whose, g.whose_team, g.granted_by, g.expires_at or None, at, by))


def set_grant_status(c: sqlite3.Connection, grant_id: str, status: str, by: str, at: str, reason: str = "") -> int:
    if status == "revoked":
        return c.execute("UPDATE fin_view_grants SET status='revoked', revoked_at=?, revoked_by=? WHERE id=? "
                         "AND status IN ('active','suspended')", (at, by, grant_id)).rowcount
    return c.execute("UPDATE fin_view_grants SET status=?, suspended_reason=? WHERE id=? AND status='active'",
                     (status, reason or None, grant_id)).rowcount


# ------------------------------------------------------------------------------------------------ private lines
def private_lines(c: sqlite3.Connection) -> dict[str, PrivateLine]:
    return {r["account_id"]: PrivateLine(account_id=r["account_id"], others_see=r["others_see"],
                                         full_detail=frozenset(json.loads(r["full_detail"] or "[]")))
            for r in c.execute("SELECT * FROM fin_private_lines")}


def set_private(c: sqlite3.Connection, account_id: str, others_see: str, full_detail: Iterable[str], by: str,
                at: str) -> None:
    c.execute("INSERT INTO fin_private_lines(account_id, others_see, full_detail, created_at, created_by, "
              "updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET others_see=excluded.others_see, "
              "full_detail=excluded.full_detail, updated_at=excluded.updated_at",
              (account_id, others_see, json.dumps(sorted(set(full_detail))), at, by, at))


def clear_private(c: sqlite3.Connection, account_id: str) -> int:
    return c.execute("DELETE FROM fin_private_lines WHERE account_id=?", (account_id,)).rowcount


def mark_private(c: sqlite3.Connection, account_id: str, by: str, at: str) -> bool:
    """Make an account private with the default (totals only, no full detail) unless it already is. True when it
    was not private before, so the caller knows to move the view version."""
    return c.execute("INSERT OR IGNORE INTO fin_private_lines(account_id, others_see, full_detail, created_at, "
                     "created_by, updated_at) VALUES(?,?,?,?,?,?)", (account_id, "totals", "[]", at, by, at)).rowcount == 1


# ------------------------------------------------------------------------------------------------ view version
def view_version(c: sqlite3.Connection) -> int:
    row = c.execute("SELECT version FROM fin_view_versions WHERE id=1").fetchone()
    return int(row[0]) if row else 0


def bump_view(c: sqlite3.Connection, reason: str, at: str) -> int:
    """Move the counter caches and open sessions key on; the new value."""
    c.execute("INSERT INTO fin_view_versions(id, version, reason, updated_at) VALUES(1, 1, ?, ?) "
              "ON CONFLICT(id) DO UPDATE SET version=fin_view_versions.version+1, reason=excluded.reason, "
              "updated_at=excluded.updated_at", (reason, at))
    return view_version(c)
