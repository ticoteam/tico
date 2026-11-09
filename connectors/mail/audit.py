"""Every read of a body, every label, every archive lands here.

Two places, one shape: <projects>/runtime/mail/audit.jsonl (append-only, greppable) and the
`audit` table in mail.db (queryable). The jsonl file is the record even if the database is
thrown away.
"""

import json
from pathlib import Path

from . import AUDIT_PATH, stamp
from . import db as _db


def line(employee, action, mailbox="", target="", detail=None, issue="", when=None):
    return {
        "ts": stamp(when),
        "employee": employee,
        "issue": str(issue or ""),
        "mailbox": mailbox or "",
        "action": action,
        "target": target or "",
        "detail": detail or {},
    }


def record(employee, action, mailbox="", target="", detail=None, issue="", conn=None, path=None, when=None):
    """Append one audit line. Returns it, so callers can print it in --json."""
    row = line(employee, action, mailbox, target, detail, issue, when)
    p = Path(path or AUDIT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    if conn is not None:
        _db.record_audit(conn, row)
    return row
