"""Rule suggestions: the weekly `propose_demotions` job and the Rules page's "make this a note" cards (docs/tasks.md).

A rule that wakes a bot and mostly finds nothing to do costs a run each time. Once a week, in plain code (no model), Tico
reads the last 7 days of runs per rule (`turns.rule_id`, the same figures as `rule_weekly` in hub sql). Any rule with at
least MIN_RUNS runs where more than HALF did nothing becomes one stored suggestion:

- a table rule that wakes or batches: `note`, change its outcome to note (the bot's next run carries it, no run starts);
- a built-in rule that is not protected: `override`, add a team rule for its event that makes it a note. Built-ins are
  read-only, so the way to change one is a scoped row, which is also how it is undone.

Nothing changes by itself. A person applies a suggestion (`apply`) with the same right they would need to edit that rule
(`rules.can_change`: a team rule or a built-in override needs an owner or admin; a bot's rule that bot's managers), and a
bot acts only with the rights of the person it acts for. `dismiss` on an open suggestion declines it, and Tico does not
suggest that rule again for DISMISSED_DAYS; on an applied one it puts the rule back as it was, if nobody has changed it
since. The always-wake events, the rule floor, awaited results and things a person scheduled or assigned (routines,
assignments, due dates) are never suggested.

A suggestion is shown only to people who may change its rule: its figures count every run of the rule, private tasks
included, so they are not for anyone else. Fresh figures replace an open suggestion's each week, and one whose rule no
longer qualifies, has gone or changed is dropped (`expired`)."""

import json

from . import rules as R
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_suggestions(
 id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, kind TEXT NOT NULL, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL DEFAULT '',
 event TEXT NOT NULL, runs INTEGER NOT NULL, no_op INTEGER NOT NULL, revision INTEGER, status TEXT NOT NULL DEFAULT 'open',
 created TEXT NOT NULL, decided_by TEXT, decided TEXT, made_rule_id TEXT, before_json TEXT);
CREATE INDEX IF NOT EXISTS rule_suggestions_rule ON rule_suggestions(rule_id, status);
"""

NOTE, OVERRIDE = "note", "override"
OPEN, APPLIED, DISMISSED, EXPIRED = "open", "applied", "dismissed", "expired"
MIN_RUNS = 20
NO_OP_OVER = 0.5
DAYS = 7
DISMISSED_DAYS = 28
SHOWN_DAYS = 30                 # an applied or dismissed suggestion stays in the list this long (so Dismiss can undo)
MAX_ROWS = 100
# Events a person scheduled or assigned: a run that finds nothing is the schedule working, not waste.
NEVER = frozenset({"routine", "schedule", "due", "snooze", "next_run", "update", "assignment", "auto_start"})
DEMOTABLE_GROUPS = ("wake", "batch", "pr_checks")


def usage(c, at):
    """{rule id: (runs, no-op runs)} over the last DAYS, every run counted."""
    since = H.shift(at, days=-DAYS)
    return {row["rule"]: (row["runs"], row["idle"] or 0) for row in c.execute(
        "SELECT rule_id AS rule, count(*) AS runs, sum(coalesce(did_nothing,0)=1) AS idle FROM turns "
        "WHERE started>=? AND rule_id IS NOT NULL GROUP BY rule_id HAVING count(*)>=?", (since, MIN_RUNS))}


def _eligible(event, then):
    return then in (R.WAKE, R.BATCH) and event != "*" and event not in R.FLOOR_EVENTS and event not in NEVER


def found(c, at):
    """The rules that qualify now: [{rule_id, kind, scope_kind, scope_id, event, runs, no_op, revision}]."""
    figures = {k: v for k, v in usage(c, at).items() if v[1] / v[0] > NO_OP_OVER}
    out = []
    for rule in R.BUILTINS:
        if rule["id"] in figures and not rule["protected"] and rule["group"] in DEMOTABLE_GROUPS \
                and _eligible(rule["event"], rule["then"]):
            runs, idle = figures[rule["id"]]
            out.append({"rule_id": rule["id"], "kind": OVERRIDE, "scope_kind": "team", "scope_id": "",
                        "event": rule["event"], "runs": runs, "no_op": idle, "revision": None})
    marks = ",".join("?" for _ in figures) or "NULL"
    for row in c.execute(f"SELECT * FROM rules WHERE enabled=1 AND id IN ({marks})", tuple(figures)):
        if _eligible(row["event"], row["then_outcome"]):
            runs, idle = figures[row["id"]]
            out.append({"rule_id": row["id"], "kind": NOTE, "scope_kind": row["scope_kind"],
                        "scope_id": row["scope_id"], "event": row["event"], "runs": runs, "no_op": idle,
                        "revision": row["revision"]})
    return out


def propose_demotions(c, at):
    """The weekly job: one open suggestion per qualifying rule, figures refreshed, stale ones expired. Returns
    {"made": [...], "expired": n}."""
    now, qualifying, made, expired = at, {f["rule_id"]: f for f in found(c, at)}, [], 0
    held = H.shift(now, days=-DISMISSED_DAYS)
    for row in c.execute("SELECT * FROM rule_suggestions WHERE status='open'").fetchall():
        item = qualifying.get(row["rule_id"])
        if item and item["revision"] == row["revision"]:
            c.execute("UPDATE rule_suggestions SET runs=?, no_op=? WHERE id=?", (item["runs"], item["no_op"], row["id"]))
        else:
            c.execute("UPDATE rule_suggestions SET status='expired', decided=? WHERE id=?", (now, row["id"]))
            expired += 1
    for rule_id, item in qualifying.items():
        if c.execute("SELECT 1 FROM rule_suggestions WHERE rule_id=? AND (status='open' OR (status IN ('dismissed','applied') "
                     "AND decided>=?))", (rule_id, held)).fetchone():
            continue
        suggestion = "sug-" + H.new_id()
        c.execute("INSERT INTO rule_suggestions(id,rule_id,kind,scope_kind,scope_id,event,runs,no_op,revision,created) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)", (suggestion, rule_id, item["kind"], item["scope_kind"], item["scope_id"],
                                                  item["event"], item["runs"], item["no_op"], item["revision"], now))
        H.event(c, H.KEEPER, "rule_suggestion.created", suggestion, {"rule": rule_id, "runs": item["runs"], "no_op": item["no_op"]})
        made.append(suggestion)
    c.execute("DELETE FROM rule_suggestions WHERE status IN ('expired','dismissed','applied') AND decided<?",
              (H.shift(now, days=-90),))
    return {"made": made, "expired": expired}


# ----------------------------------------------------------------------------- reading
def view(row, can):
    rate = round(row["no_op"] / row["runs"], 3) if row["runs"] else None
    words = ("Make this rule a note" if row["kind"] == NOTE
             else "Add a team rule that makes this built-in rule a note")
    return {"id": row["id"], "rule_id": row["rule_id"], "kind": row["kind"], "scope_kind": row["scope_kind"],
            "scope_id": row["scope_id"], "event": row["event"], "status": row["status"], "runs_7d": row["runs"],
            "no_op_7d": row["no_op"], "no_op_rate_7d": rate, "suggestion": words, "created": row["created"],
            "decided_by": row["decided_by"], "decided": row["decided"], "made_rule_id": row["made_rule_id"],
            "can_change": can}


def _can(c, settings, auth, who, row):
    return R.can_change(c, settings, auth, who, row["scope_kind"], row["scope_id"])


def listing(c, settings, auth, who, status=OPEN, limit=50, offset=0):
    """The suggestions this viewer may act on, newest first. `status` is open (the default), or all: also the applied
    and dismissed ones of the last SHOWN_DAYS."""
    limit = max(1, min(int(limit or 50), MAX_ROWS))
    if status == "all":
        rows = c.execute("SELECT * FROM rule_suggestions WHERE status='open' OR (status IN ('applied','dismissed') AND "
                         "decided>=?) ORDER BY created DESC, id", (H.shift(H.now(), days=-SHOWN_DAYS),)).fetchall()
    elif status in (OPEN, APPLIED, DISMISSED):
        rows = c.execute("SELECT * FROM rule_suggestions WHERE status=? ORDER BY created DESC, id", (status,)).fetchall()
    else:
        raise Problem("invalid", "status is open, applied, dismissed or all", 422)
    mine = [r for r in rows if _can(c, settings, auth, who, r)]
    return {"suggestions": [view(r, True) for r in mine[offset:offset + limit]], "more": len(mine) > offset + limit}


def _get(c, suggestion_id):
    row = c.execute("SELECT * FROM rule_suggestions WHERE id=?", (suggestion_id,)).fetchone()
    if not row:
        raise Problem("not_found", "No such suggestion", 404)
    return row


# ----------------------------------------------------------------------------- acting
def apply(c, settings, auth, who, suggestion_id):
    """Make the rule a note, as the person who asked. A table rule is edited in place (its old outcome is kept to put
    back); a built-in gets a team override row. Refused unless they could edit that rule by hand."""
    row = _get(c, suggestion_id)
    R._require(c, settings, auth, who, row["scope_kind"], row["scope_id"])
    if row["status"] != OPEN:
        raise Problem("conflict", f"This suggestion is {row['status']}", 409)
    if row["kind"] == NOTE:
        rule = c.execute("SELECT * FROM rules WHERE id=?", (row["rule_id"],)).fetchone()
        if not rule or rule["revision"] != row["revision"]:
            raise Problem("version_conflict", "The rule changed since this was suggested; it will be looked at again next week", 409)
        R.validate(c, settings, rule["scope_kind"], rule["scope_id"], rule["event"], R.NOTE,
                   H._json(rule["when_json"], {}), H._json(rule["params_json"], {}))
        c.execute("UPDATE rules SET then_outcome='note', updated=?, revision=revision+1 WHERE id=?", (H.now(), rule["id"]))
        after = R.get(c, rule["id"])
        H.event(c, who.actor, "rule.updated", rule["id"], {"before": R.view(rule), "after": R.view(after),
                                                           "suggestion": suggestion_id})
        c.execute("UPDATE rule_suggestions SET status='applied', decided_by=?, decided=?, before_json=?, revision=? WHERE id=?",
                  (who.actor, H.now(), json.dumps({"then": rule["then_outcome"]}), after["revision"], suggestion_id))
    else:
        scope_id, when, params = R.validate(c, settings, "team", "", row["event"], R.NOTE, {}, {})
        made = "rule-" + H.new_id()
        at = H.now()
        position = (c.execute("SELECT max(position) FROM rules WHERE scope_kind='team' AND scope_id=''").fetchone()[0] or 0) + 1
        if c.execute("SELECT count(*) FROM rules").fetchone()[0] >= R.MAX_ROWS:
            raise Problem("conflict", "The rule list is full; edit or delete a rule", 409)
        c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,when_json,then_outcome,params_json,enabled,position,note,"
                  "created_by,created,updated,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                  (made, "team", scope_id, row["event"], "{}", R.NOTE, "{}", 1, position,
                   f"Made a note: {row['rule_id']} found nothing to do on {row['no_op']} of {row['runs']} runs in a week",
                   who.actor, at, at))
        H.event(c, who.actor, "rule.created", made, {"after": R.view(R.get(c, made)), "suggestion": suggestion_id})
        c.execute("UPDATE rule_suggestions SET status='applied', decided_by=?, decided=?, made_rule_id=?, revision=1 WHERE id=?",
                  (who.actor, at, made, suggestion_id))
    H.event(c, who.actor, "rule_suggestion.applied", suggestion_id, {"rule": row["rule_id"]})
    return {"suggestion": view(_get(c, suggestion_id), True)}


def dismiss(c, settings, auth, who, suggestion_id):
    """Decline an open suggestion, or take an applied one back: the rule returns to its old outcome (a built-in's
    override row is deleted), unless someone has changed it since (a 409)."""
    row = _get(c, suggestion_id)
    R._require(c, settings, auth, who, row["scope_kind"], row["scope_id"])
    if row["status"] == OPEN:
        status = DISMISSED
    elif row["status"] == APPLIED:
        status = DISMISSED
        target = row["made_rule_id"] or row["rule_id"]
        rule = c.execute("SELECT * FROM rules WHERE id=?", (target,)).fetchone()
        if rule and rule["revision"] != row["revision"]:
            raise Problem("version_conflict", "The rule changed since it was applied; edit it by hand", 409)
        if rule and row["made_rule_id"]:
            c.execute("DELETE FROM rules WHERE id=?", (target,))
            H.event(c, who.actor, "rule.deleted", target, {"before": R.view(rule), "suggestion": suggestion_id})
        elif rule:
            before = (H._json(row["before_json"], {}) or {}).get("then") or R.WAKE
            c.execute("UPDATE rules SET then_outcome=?, updated=?, revision=revision+1 WHERE id=?", (before, H.now(), target))
            H.event(c, who.actor, "rule.updated", target, {"before": R.view(rule), "after": R.view(R.get(c, target)),
                                                          "suggestion": suggestion_id})
    else:
        raise Problem("conflict", f"This suggestion is {row['status']}", 409)
    c.execute("UPDATE rule_suggestions SET status=?, decided_by=?, decided=? WHERE id=?",
              (status, who.actor, H.now(), suggestion_id))
    H.event(c, who.actor, "rule_suggestion.dismissed", suggestion_id, {"rule": row["rule_id"], "was": row["status"]})
    return {"suggestion": view(_get(c, suggestion_id), True)}


def install(app, store, auth, mutate):
    """The routes; registered before backend/rules.py's `/api/v2/rules/{rule_id}`, which would take `suggestions`."""
    from fastapi import Request
    from . import models as M

    @app.get("/api/v2/rules/suggestions")
    def list_suggestions(request: Request, status: str = OPEN, limit: int = 50, offset: int = 0):
        """The rules Tico suggests making a note, with each one's 7-day runs and no-op share: only those the viewer may
        change. `status` open (default) or all (also the applied and dismissed ones of the last 30 days)."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, store.settings, auth, who, status, limit, max(0, offset))

    @app.post("/api/v2/rules/suggestions/{suggestion_id}/apply")
    def apply_suggestion(suggestion_id: str, request: Request, body: M.RuleSuggestionAct):
        who = request.state.identity
        return mutate(request, body, lambda c: apply(c, store.settings, auth, who, suggestion_id))

    @app.post("/api/v2/rules/suggestions/{suggestion_id}/dismiss")
    def dismiss_suggestion(suggestion_id: str, request: Request, body: M.RuleSuggestionAct):
        who = request.state.identity
        return mutate(request, body, lambda c: dismiss(c, store.settings, auth, who, suggestion_id))
