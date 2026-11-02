"""SQL for `fin_actions`: every requested write, its status, its approval and its result."""
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any


def by_key(c: sqlite3.Connection, key: str) -> Any:
    return c.execute("SELECT * FROM fin_actions WHERE idempotency_key=?", (key,)).fetchone()


def by_id(c: sqlite3.Connection, action_id: str) -> Any:
    return c.execute("SELECT * FROM fin_actions WHERE id=?", (action_id,)).fetchone()


def by_approval(c: sqlite3.Connection, approval_id: str) -> Any:
    return c.execute("SELECT * FROM fin_actions WHERE approval_id=?", (approval_id,)).fetchone()


def waiting(c: sqlite3.Connection, kinds: Sequence[str]) -> list[Any]:
    """Actions of these kinds still waiting on an approval."""
    marks = ",".join("?" * len(kinds))
    return c.execute(f"SELECT * FROM fin_actions WHERE status='needs_approval' AND kind IN ({marks}) "
                     "ORDER BY created_at, id", list(kinds)).fetchall()


def insert(c: sqlite3.Connection, *, action_id: str, kind: str, payload: Mapping[str, object], actor: str,
           on_behalf_of: str, rule_id: str, key: str, rules: Sequence[Mapping[str, object]], status: str,
           result: Mapping[str, object], code: str, reason: str, by: str, at: str) -> None:
    c.execute("INSERT INTO fin_actions(id, kind, payload_json, actor, on_behalf_of, rule_id, idempotency_key, "
              "rule_results_json, status, result_json, code, reason, created_at, created_by, updated_at) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (action_id, kind, _dump(payload), actor, on_behalf_of, rule_id, key, _dump(list(rules)), status,
               _dump(result), code, reason, at, by, at))


def finish(c: sqlite3.Connection, action_id: str, *, status: str, result: Mapping[str, object], code: str,
           reason: str, at: str) -> None:
    c.execute("UPDATE fin_actions SET status=?, result_json=?, code=?, reason=?, updated_at=? WHERE id=?",
              (status, _dump(result), code, reason, at, action_id))


def asked(c: sqlite3.Connection, action_id: str, *, approval_id: str, approval_hash: str, requester: str, to: str,
          rule_id: str, payload: Mapping[str, object], at: str,
          rules: Sequence[Mapping[str, object]] | None = None) -> None:
    """The approval an action waits on: its id, the hash of the payload it was asked for, who asked and who decides. The
    stored payload becomes that one, so the decision checks the payload that was asked. `rules`, when given, replaces
    the rules the action records: every rule its approval names (a re-ask after the rules changed)."""
    c.execute("UPDATE fin_actions SET status='needs_approval', approval_id=?, approval_hash=?, approval_by=?, "
              "approval_to=?, rule_id=?, payload_json=?, updated_at=? WHERE id=?",
              (approval_id, approval_hash, requester, to, rule_id, _dump(payload), at, action_id))
    if rules is not None:
        c.execute("UPDATE fin_actions SET rule_results_json=? WHERE id=?", (_dump(list(rules)), action_id))


def settled_waits(c: sqlite3.Connection, most: int) -> list[str]:
    """Approval ids of actions still waiting whose approval was decided, withdrawn or is gone: the sweep's work."""
    return [str(r[0]) for r in c.execute(
        "SELECT f.approval_id FROM fin_actions f LEFT JOIN approvals a ON a.id=f.approval_id "
        "WHERE f.status='needs_approval' AND (a.id IS NULL OR a.decision IS NOT NULL) ORDER BY f.created_at LIMIT ?",
        (most,))]


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def drop_waiting(c: sqlite3.Connection) -> int:
    """Delete every action still waiting on its approval (a dev copy's scrub). How many went."""
    return c.execute("DELETE FROM fin_actions WHERE status='needs_approval'").rowcount
