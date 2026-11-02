"""Model-family provenance for check routing preferences and verdict records.

The configured model is useful when choosing a candidate, but it is not proof of what a completed check ran on.
Verdicts use immutable run usage segments for both the builder's ticket and the checker task. Unknown or mixed
families inform routing only; they never invalidate a verdict.
"""
import json

from . import hubdb as H
from . import usage_records

SCHEMA = """
CREATE TABLE IF NOT EXISTS check_head_provenance(
  check_id TEXT NOT NULL, ticket_id TEXT NOT NULL, head TEXT NOT NULL, turn_id TEXT NOT NULL,
  builder_families TEXT NOT NULL, configured_families TEXT NOT NULL DEFAULT '[]',
  checker_families TEXT NOT NULL, known INTEGER NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(check_id,head,turn_id));
CREATE INDEX IF NOT EXISTS check_head_provenance_head ON check_head_provenance(ticket_id,head);
"""


def ensure(c):
    # executescript() commits any open transaction, which would drop the request's outer savepoint when
    # a check finishes inside a runner completion write.
    for statement in SCHEMA.split(";"):
        if statement.strip():
            c.execute(statement)
    H.add_column(c, "check_head_provenance", "configured_families", "TEXT NOT NULL DEFAULT '[]'")


def configured(c, check_id, owner):
    """Configured provider for this check task, distinct from its captured execution family."""
    from . import providers
    selected = H.check_model(c, check_id, H.actor_id(owner)) if owner and H.is_bot(owner) else None
    if selected:
        family = providers.author_provider(selected["model"])
    else:
        from .check_pools import maker
        family = maker(c, H.actor_id(owner)) if owner and H.is_bot(owner) else ""
    return [family] if family else []


def _executed(c, task_id):
    turns = [r[0] for r in c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started_at,id", (task_id,))]
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
    """Capture the attempt that judged `head`, plus actual builder usage across every turn the ticket ran.

    Builder usage is not attributed to a head, so the whole ticket history counts: a builder that used two families,
    or a turn with no usage segments, narrows or blocks independence rather than letting an overlap through.

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
    check = H.task(c, check_id)
    configured_families = configured(c, check_id, check.get("owner") if check else None)
    c.execute("INSERT OR REPLACE INTO check_head_provenance(check_id,ticket_id,head,turn_id,builder_families,"
              "configured_families,checker_families,known,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (check_id, ticket_id, str(head or "").lower(), turn_id, json.dumps(sorted(builder)),
               json.dumps(configured_families), json.dumps(sorted(checker)), int(known), H.now()))
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
        row = c.execute("SELECT builder_families,configured_families,checker_families,known FROM check_head_provenance "
                        "WHERE ticket_id=? AND check_id=? AND head=? "
                        "AND (? IS NULL OR turn_id=?) ORDER BY created_at DESC,turn_id DESC LIMIT 1",
                        (ticket["id"], check_id, str(head or "").lower(), attempt_id, attempt_id)).fetchone()
        if row:
            builder = set(H._json(row["builder_families"], []) or [])
            configured_families = set(H._json(row["configured_families"], []) or [])
            checker_families = set(H._json(row["checker_families"], []) or [])
            builder_known = checker_known = bool(row["known"])
        else:
            builder, configured_families, checker_families = set(), set(), set()
            builder_known = checker_known = False
        evidence = "captured attempt"
    else:
        builder, builder_known = _executed(c, ticket["id"])
        from .check_pools import maker, makers
        checker_families = makers(c, checker, whole=True)    # a model of unknown company may be the builder's
        configured_families = {maker(c, checker)} - {""}
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
            "builder_families": sorted(builder), "configured_families": sorted(configured_families),
            "checker_families": sorted(checker_families), "reason": reason}
