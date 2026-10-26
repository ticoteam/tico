"""Shared model-family eligibility for check routing and accepted verdicts.

The configured model is useful when choosing a candidate, but it is not proof of what a completed check ran on.
Verdicts use immutable run usage segments for both the builder's ticket and the checker task. Unknown or mixed
families are deliberately not independent.
"""
import json

from . import hubdb as H
from . import usage_records

SCHEMA = """
CREATE TABLE IF NOT EXISTS check_head_provenance(
  check_id TEXT NOT NULL, ticket_id TEXT NOT NULL, head TEXT NOT NULL, turn_id TEXT NOT NULL,
  builder_families TEXT NOT NULL, checker_families TEXT NOT NULL, known INTEGER NOT NULL,
  created TEXT NOT NULL, PRIMARY KEY(check_id,head,turn_id));
CREATE INDEX IF NOT EXISTS check_head_provenance_head ON check_head_provenance(ticket_id,head);
"""


def ensure(c):
    c.executescript(SCHEMA)


def _executed(c, task_id):
    turns = [r[0] for r in c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started,id", (task_id,))]
    if not turns:
        return set(), False
    families, known = set(), True
    for made in usage_records.ran_on(c, turns).values():
        segments = made.get("segments") or []
        if not segments or any(not segment.get("family") for segment in segments):
            known = False
        families.update(segment["family"] for segment in segments if segment.get("family"))
    return families, known and bool(families)


def capture_attempt(c, ticket_id, check_id, head, turn_id):
    """Capture the attempt that judged `head`, plus actual builder usage observed for that current head.

    Part A calls this when an assigned check attempt finishes. Keeping the attempt id makes a configured candidate
    distinct from the model that actually ran; an unknown or mixed segment is recorded as unknown.
    """
    ensure(c)
    builder, builder_known = _executed(c, ticket_id)
    run = usage_records.ran_on(c, [turn_id]).get(turn_id) or {}
    segments = run.get("segments") or []
    checker = {s["family"] for s in segments if s.get("family")}
    checker_known = bool(segments) and all(s.get("family") for s in segments) and bool(checker)
    known = builder_known and checker_known
    c.execute("INSERT OR REPLACE INTO check_head_provenance(check_id,ticket_id,head,turn_id,builder_families,"
              "checker_families,known,created) VALUES(?,?,?,?,?,?,?,?)",
              (check_id, ticket_id, str(head or "").lower(), turn_id, json.dumps(sorted(builder)),
               json.dumps(sorted(checker)), int(known), H.now()))
    return {"eligible": known and bool(builder) and bool(checker) and not builder.intersection(checker),
            "known": known, "head": str(head or "").lower(), "evidence": "executed",
            "builder_families": sorted(builder), "checker_families": sorted(checker),
            "reason": "captured executed model-family provenance" if known else "execution provenance is unknown"}


def assess(c, ticket, checker, *, head=None, check_id=None, attempt_id=None):
    """Return the shared independence decision for a candidate or an executed verdict.

    Pass `check_id` when validating a completed verdict. That path only trusts actual checker and builder usage
    segments. Without it, candidate routing uses Part A's configured/runtime candidate set, but still requires actual
    builder provenance; unknown candidates never qualify.
    """
    ticket = H.task(c, ticket["id"] if isinstance(ticket, dict) else ticket)
    if check_id:
        ensure(c)
        row = c.execute("SELECT builder_families,checker_families,known FROM check_head_provenance "
                        "WHERE ticket_id=? AND check_id=? AND head=? "
                        "AND (? IS NULL OR turn_id=?) ORDER BY created DESC,turn_id DESC LIMIT 1",
                        (ticket["id"], check_id, str(head or "").lower(), attempt_id, attempt_id)).fetchone()
        if row:
            builder = set(H._json(row["builder_families"], []) or [])
            checker_families = set(H._json(row["checker_families"], []) or [])
            builder_known = checker_known = bool(row["known"])
        else:
            builder, checker_families = set(), set()
            builder_known = checker_known = False
        evidence = "captured attempt"
    else:
        builder, builder_known = _executed(c, ticket["id"])
        from .check_pools import makers
        checker_families = makers(c, checker)
        checker_known = bool(checker_families)
        evidence = "configured/runtime"
    overlap = builder & checker_families
    known = builder_known and checker_known
    eligible = known and bool(builder) and bool(checker_families) and not overlap
    if not builder_known:
        reason = "builder model-family provenance is unknown"
    elif not checker_known:
        reason = "checker model-family provenance is unknown"
    elif overlap:
        reason = "checker ran on a builder model family: " + ", ".join(sorted(overlap))
    else:
        reason = "independent model-family provenance confirmed"
    return {"eligible": eligible, "known": known, "head": str(head or ""), "evidence": evidence,
            "builder_families": sorted(builder), "checker_families": sorted(checker_families), "reason": reason}
