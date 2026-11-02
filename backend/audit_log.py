"""One audit trail for the redesign's settings: task caps, spend limits, groups and automations.

Every change writes one row with who made it, the object it changed (`<kind>:<id>`, e.g. `task_cap:<id>`,
`automation:<id>`) and the whole object before and after (null when created or deleted), in the same transaction as
the change, so a change without its row cannot exist. Rows are never edited or pruned.
"""
import json

from .store import H, encode

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log(
 id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, object TEXT NOT NULL,
 before TEXT, after TEXT, via TEXT);
CREATE INDEX IF NOT EXISTS audit_log_object ON audit_log(object, at);
CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log(at);
"""
LIMIT = 50
LIST_BYTES = 262_144                     # as the automations list: a page stays under 256 KB


def write(c, actor, obj, before, after, via=None):
    """Record one change. `via` names who acted for the actor (BotOps for a person, an approval id)."""
    row_id = H.new_id()
    c.execute("INSERT INTO audit_log VALUES(?,?,?,?,?,?,?)",
              (row_id, H.now(), actor, obj,
               None if before is None else encode(before), None if after is None else encode(after), via))
    return row_id


def _row(r):
    return {"id": r["id"], "at": r["at"], "actor": r["actor"], "object": r["object"], "via": r["via"],
            "before": None if r["before"] is None else json.loads(r["before"]),
            "after": None if r["after"] is None else json.loads(r["after"])}


def rows(c, obj=None, actor=None, limit=LIMIT, before=None):
    """Newest first, paged by `before`: the last row's `<at>|<id>`, so rows sharing a time are never skipped. A page
    stops at LIST_BYTES too (a row carries whole objects), and the next page starts at the first row it left out."""
    sql, args = "SELECT * FROM audit_log WHERE 1=1", []
    for column, value in (("object", obj), ("actor", actor)):
        if value:
            sql += f" AND {column}=?"
            args.append(value)
    if before:
        at, _, row_id = before.partition("|")
        sql += " AND (at<? OR (at=? AND id<?))"
        args += [at, at, row_id]
    limit = max(1, min(int(limit or LIMIT), 200))
    found = [_row(r) for r in c.execute(sql + " ORDER BY at DESC, id DESC LIMIT ?", (*args, limit + 1))]
    shown, used = [], 256
    for row in found[:limit]:
        size = len(json.dumps(row, default=str).encode()) + 1
        if shown and used + size > LIST_BYTES:
            break                                    # the first row of a page always fits
        used += size
        shown.append(row)
    last = shown[-1] if len(shown) < len(found) else None
    return {"rows": shown, "next": last and last["at"] + "|" + last["id"]}


def install(app, store, auth):
    from fastapi import Request

    @app.get("/api/v2/audit")
    def audit_view(request: Request, object: str | None = None, limit: int = LIMIT, before: str | None = None):
        """Who changed what. The owner reads every row; anyone else reads only their own changes."""
        who = request.state.identity
        auth.domain(who)
        from .org_rules import redact_audit
        with store.read() as c:
            mine = None if who.role == "owner" else who.actor
            page = rows(c, obj=object, actor=mine, limit=limit, before=before)
            redact_audit(c, auth, who, page["rows"])
            return page
