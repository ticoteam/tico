"""What the person's Assistant did for them, and taking one of those changes back (docs/assistant.md, "Undo").

`GET /api/v2/assistant/changes` lists the writes recorded `via: assistant` by the caller, newest first: a task filed,
changed or closed, an approval decided, an automation or an org rule changed, a message sent. Each says what it did in
one line, the words of the person it acted on (the cited message), the object it touched, and whether it can be undone
and, when not, why.

`POST /api/v2/assistant/changes/{id}/restore` undoes one: a task's status or owner goes back to its earlier value, an
approval goes back to waiting for the person, an automation goes back to the version before the change, an org rule
added is retired. A message sent stays sent. Restoring twice is a no-op (`already: true`), and every restore is
recorded as an event of the person's own.

Only the caller's own changes are listed or restored; anything else is a 404.
"""

import json

from fastapi import Request

from . import hubdb as H
from . import models as M
from . import org_rules
from . import automations
from .store import Problem

LIST_DAYS = 30
LIST_DAYS_MAX = 90
PAGE = 50
PAGE_MAX = 100
WORDS = 200
TASK_ACTIONS = ("task.create", "task.update", "task.close")
RESTORE_ACTION = "assistant.change.restore"
SKEW_SECONDS = 5          # a task change's status row is written just before its event: the window that pairs them


def install(app, store, auth, mutate, settings):
    @app.get("/api/v2/assistant/changes")
    def assistant_changes(request: Request, days: int = LIST_DAYS, limit: int = PAGE, before: str | None = None):
        """What your Assistant did for you, newest first: each change's `what`, the `words` it acted on, its `object`,
        and `undoable` with `why_not` when it cannot be taken back. `before` is the `next` of the page before."""
        who = request.state.identity
        if who.role not in ("owner", "human"):
            raise Problem("identity", "This frontend endpoint requires a person", 403)
        days, limit = max(1, min(int(days), LIST_DAYS_MAX)), max(1, min(int(limit), PAGE_MAX))
        with store.read_transaction() as c:
            return listing(c, who, days, limit, before, settings)

    @app.post("/api/v2/assistant/changes/{cid}/restore")
    def assistant_restore(request: Request, cid: str, body: M.Empty):
        """Take one of your Assistant's changes back, as you. Restoring it again changes nothing."""
        who = request.state.identity
        if who.role not in ("owner", "human"):
            raise Problem("identity", "This frontend endpoint requires a person", 403)

        def work(c):
            return restore(c, auth, who, cid, settings)
        return mutate(request, body, work)


def listing(c, who, days, limit, before, settings):
    """One page of the caller's Assistant changes, newest first. `next` is the `<at>|<id>` of the last row shown, and
    `before` takes it back: each source is read from that time on, so a page never skips a row."""
    since = H.shift(H.now(), days=-days)
    top, cut = (before.partition("|")[0], before.partition("|")[2]) if before else (H.now(), "")
    fetch = limit + 1
    sources = (
        ("SELECT * FROM events WHERE actor=? AND created_at>=? AND created_at<=? AND action IN (?,?,?,?) "
         "AND json_extract(detail,'$.via')='assistant' ORDER BY created_at DESC, id DESC LIMIT ?",
         (who.actor, since, top, *TASK_ACTIONS, "approval.decide", fetch), "event"),
        ("SELECT * FROM audit_log WHERE actor=? AND created_at>=? AND created_at<=? AND via='assistant' "
         "ORDER BY created_at DESC, id DESC LIMIT ?", (who.actor, since, top, fetch), "audit"),
        ("SELECT * FROM messages WHERE sender_id=? AND created_at>=? AND created_at<=? "
         "AND refs LIKE '%assistant%' AND json_extract(refs,'$.via')='assistant' "
         "ORDER BY created_at DESC, id DESC LIMIT ?", (who.actor, since, top, fetch), "message"),
        ("SELECT * FROM settings_changes WHERE actor=? AND created_at>=? AND created_at<=? AND via='assistant' "
         "ORDER BY created_at DESC, id DESC LIMIT ?", (who.actor, since, top, fetch), "setting"),
    )
    rows = []
    for sql, args, kind in sources:
        rows += [(kind, dict(row)) for row in c.execute(sql, args)]
    rows.sort(key=lambda x: (x[1]["created_at"], x[1]["id"]), reverse=True)
    if cut:
        rows = [x for x in rows if (x[1]["created_at"], x[1]["id"]) < (top, cut)]
    shown = [describe(c, who, kind, row, settings) for kind, row in rows[:limit]]
    last = shown[-1] if len(rows) > limit and shown else None
    return {"actor": who.actor, "days": days, "changes": shown,
            "next": f"{last['at']}|{last['id']}" if last else None}


def has_column(c, table, column):
    return any(r[1] == column for r in c.execute(f"PRAGMA table_info({table})"))


def describe(c, who, kind, row, settings):
    """One change in the list: its id (the restore key), when, what, the words it acted on, its object, and undo."""
    cid = row["id"]
    if kind == "event":
        detail = json.loads(row["detail"] or "{}") or {}
        target = row["target"] or ""
        obj = target if ":" in target else (f"task:{target}" if row["action"].startswith("task.") else
                                            f"approval:{target}" if row["action"].startswith("approval.") else target)
        what = event_line(c, row, detail, obj)
        words = cited_words(c, detail.get("delegation"))
        undo, why = undoable(c, who, kind, row, detail, settings)
    elif kind == "audit":
        obj = row["object"]
        before = json.loads(row["before"]) if row["before"] else None
        after = json.loads(row["after"]) if row["after"] else None
        what = audit_line(obj, before, after)
        words = ""
        undo, why = undoable(c, who, kind, row, {"before": before, "after": after}, settings)
    elif kind == "setting":
        obj = f"setting:{row['bot']}:{row['field']}"
        what = setting_line(c, row)
        words = ""
        undo, why = undoable(c, who, kind, row, {}, settings)
    else:
        obj = f"message:{cid}"
        what = "Answered a question" if row["kind"] == "answer" else "Sent a message"
        words = (row["body"] or "")[:WORDS]
        undo, why = False, "a message sent stays sent"
    out = {"id": cid, "at": row["created_at"], "what": what[:200], "words": words[:WORDS], "object": obj,
           "undoable": bool(undo)}
    if not undo:
        out["why_not"] = why
    return out


def setting_line(c, row):
    """A plain line for a bot setting the Assistant changed for the person: the bot, the setting and its new value."""
    bot = (H.bot(c, row["bot"]) or {}).get("name") or row["bot"]
    return f"Changed {bot}'s {row['field'].replace('_', ' ')}"


def event_line(c, row, detail, obj):
    """A plain line for an event the Assistant wrote: the task's title, the status it set, the approval's decision."""
    action = row["action"]
    if action == "approval.decide":
        ask = H.approval(c, row["target"]) or {}
        return f"{'Approved' if detail.get('decision') == 'approved' else 'Declined'} a {ask.get('kind', 'request')} request"
    task = H.task(c, row["target"]) if obj.startswith("task:") else None
    title = (task or {}).get("title") or "a task"
    if action == "task.create":
        return f"Filed the task '{title}'"
    if action == "task.close":
        return f"Closed '{title}'"
    status = detail.get("status")
    return f"Set '{title}' to {status}" if status else f"Changed '{title}'"


def audit_line(obj, before, after):
    """A plain line for an automation or org rule change in the audit trail."""
    kind, _, _ = obj.partition(":")
    if kind == "automation":
        name = (after or before or {}).get("name") or "an automation"
        return f"Created automation '{name}'" if before is None else (
            f"Deleted automation '{name}'" if after is None else f"Changed automation '{name}'")
    if kind == "org_rule":
        text = ((after or before or {}).get("text") or "a rule")[:120]
        return f"Added the rule: {text}" if before is None else (
            f"Retired the rule: {text}" if after is None or (after or {}).get("status") == "retired"
            else f"Changed the rule: {text}")
    return f"Changed {obj}"


def _json(text):
    return json.loads(text) if text else None


def cited_words(c, delegation):
    """The text of the person's message a change acted on, when the change cited one."""
    cited = delegation.get("cited") if isinstance(delegation, dict) else None
    if not isinstance(cited, str) or not cited:
        return ""
    message = H.message(c, cited)
    return (message or {}).get("body") or ""


def task_field_rows(c, who, row, detail):
    """The task_events rows a change wrote just before its event: (field, old, new) for each one, the same actor, via."""
    at = row["created_at"]
    start = H.shift(at, seconds=-SKEW_SECONDS)
    if not has_column(c, "task_events", "via"):
        return []
    return [dict(r) for r in c.execute(
        "SELECT * FROM task_events WHERE task_id=? AND actor=? AND via='assistant' AND created_at>=? AND created_at<=? "
        "AND field IN ('status','owner') ORDER BY created_at DESC", (row["target"], who.actor, start, at))]


def undoable(c, who, kind, row, detail, settings):
    """(undoable, why_not): whether the change can be taken back as it stands, and the reason when it cannot."""
    if kind == "event":
        action = row["action"]
        if action == "approval.decide":
            ask = H.approval(c, row["target"])
            if not ask or ask.get("decided_by_id") != who.actor or ask.get("decided_via") != "assistant":
                return False, "the decision is no longer your Assistant's"
            if ask.get("consumed_at"):
                return False, "it already ran"
            return True, ""
        if action == "task.create":
            return False, "close the task on its page if it is not wanted"
        rows = task_field_rows(c, who, row, detail)
        if not rows:
            return False, "only its note changed"
        task = H.task(c, row["target"]) or {}
        for field_row in rows:
            if (task.get(field_row["field"]) or None) != (field_row["new"] or None):
                return False, "it changed again since"
        return True, ""
    if kind == "setting":
        if row["undone_at"]:
            return False, "it was undone already"
        if not settings.may_undo(c, who, row):
            return False, "you no longer manage this bot"
        if settings.snapshot(c, row["bot"], row["field"]) != _json(row["after"]):
            return False, "it changed again since"
        return True, ""
    if kind == "audit":
        obj = row["object"]
        before, after = detail.get("before"), detail.get("after")
        if obj.startswith("org_rule:") and before is None and after is not None:
            return (True, "") if after.get("status") == "active" else (False, "the rule is no longer in force")
        if obj.startswith("automation:") and before is not None and after is not None:
            current = c.execute("SELECT flow FROM automations WHERE id=?", (obj.split(":", 1)[1],)).fetchone()
            if not current or json.loads(current["flow"]) != after:
                return False, "it changed again since"
            return True, ""
        return False, "change it on its page"
    return False, "a message sent stays sent"


def restore(c, auth, who, cid, settings):
    """Undo one of the caller's Assistant changes. Idempotent: a second restore answers `already`."""
    row, kind = find(c, who, cid)
    if row is None:
        raise Problem("not_found", f"no change {cid} of yours", 404)
    done = c.execute("SELECT id FROM events WHERE action=? AND target=? LIMIT 1", (RESTORE_ACTION, cid)).fetchone()
    if done:
        return {"id": cid, "restored": True, "already": True}
    if kind == "message":
        raise Problem("not_undoable", "a message sent stays sent", 409)
    detail = json.loads(row["detail"] or "{}") if kind == "event" else {
        "before": json.loads(row["before"]) if row["before"] else None,
        "after": json.loads(row["after"]) if row["after"] else None}
    if kind == "setting":
        undo, why = undoable(c, who, kind, row, {}, settings)
        if not undo:
            raise Problem("not_undoable", why, 409)
        config = settings._config(c, row["bot"])
        settings.undo(c, who, row["id"], M.SettingsUndo(expected_revision=config["revision"]))
        obj = f"setting:{row['bot']}:{row['field']}"
    elif kind == "event" and row["action"] in ("approval.decide",):
        undo, why = undoable(c, who, kind, row, detail, settings)
        if not undo:
            raise Problem("not_undoable", why, 409)
        H.approval_undo(c, who.actor, row["target"])
        obj = f"approval:{row['target']}"
    elif kind == "event":
        undo, why = undoable(c, who, kind, row, detail, settings)
        if not undo:
            raise Problem("not_undoable", why, 409)
        restore_task(c, who, row, detail)
        obj = f"task:{row['target']}"
    else:
        undo, why = undoable(c, who, kind, row, detail, settings)
        if not undo:
            raise Problem("not_undoable", why, 409)
        obj = row["object"]
        if obj.startswith("org_rule:"):
            rule = obj.split(":", 1)[1]
            version = c.execute("SELECT version FROM org_rules WHERE id=?", (rule,)).fetchone()
            org_rules.decide_rule(c, auth, who, rule, {"version": version["version"] if version else 1}, "retire")
        else:
            restore_automation(c, who, obj.split(":", 1)[1], detail["before"])
    H.event(c, who.actor, RESTORE_ACTION, cid, {"object": obj, "undid": row["id"]})
    return {"id": cid, "restored": True, "already": False, "object": obj}


def find(c, who, cid):
    """(row, kind) of the caller's own change `cid`, or (None, None): another person's or none."""
    row = c.execute("SELECT * FROM settings_changes WHERE id=? AND actor=? AND via='assistant'", (cid, who.actor)).fetchone()
    if row:
        return dict(row), "setting"
    row = c.execute("SELECT * FROM events WHERE id=? AND actor=? AND json_extract(detail,'$.via')='assistant'",
                    (cid, who.actor)).fetchone()
    if row:
        return dict(row), "event"
    row = c.execute("SELECT * FROM audit_log WHERE id=? AND actor=? AND via='assistant'", (cid, who.actor)).fetchone()
    if row:
        return dict(row), "audit"
    row = c.execute("SELECT * FROM messages WHERE id=? AND sender_id=? AND refs LIKE '%assistant%' "
                    "AND json_extract(refs,'$.via')='assistant'", (cid, who.actor)).fetchone()
    if row:
        return dict(row), "message"
    return None, None


def restore_task(c, who, row, detail):
    """Put back each status and owner the change set, to the value the task had before it."""
    for field_row in task_field_rows(c, who, row, detail):
        if field_row["field"] == "status":
            H.task_update(c, who.actor, row["target"], status=field_row["old"] or "open")
        elif field_row["field"] == "owner" and field_row["old"]:
            H.task_update(c, who.actor, row["target"], owner=field_row["old"])


def restore_automation(c, who, aid, before):
    """Save the automation's earlier flow as a new version, the way a restore of an old version does."""
    current = c.execute("SELECT flow FROM automations WHERE id=?", (aid,)).fetchone()
    if not current or not automations.may_change(c, who, before):
        raise Problem("forbidden", "you may not change this automation", 403)
    automations._save(c, who.actor, aid, before, json.loads(current["flow"]), new_version=True)
