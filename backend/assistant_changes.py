"""What the person's Assistant did for them, and taking one of those changes back (docs/assistant.md, "Undo").

`GET /api/v2/assistant/changes` lists the writes recorded `via: assistant` by the caller, newest first: a task filed,
moved (done, closed, reopened, to another stage) or given to someone else, an automation or an org rule changed, a
message sent. Each says what it did in one line, the words of the person it acted on (the cited message), the object it
touched, and whether it can be undone and, when not, why.

`POST /api/v2/assistant/changes/{id}/restore` undoes one: a task's move is a move back to where it was, with a why, and
a hand-over gives it back (both through the task calls, never a direct write); an automation goes back to the version
before the change, an org rule added is retired. A message sent stays sent. Restoring twice is a no-op
(`already: true`), and every restore is recorded as an event of the person's own.

Only the caller's own changes are listed or restored; anything else is a 404.
"""

import json

from fastapi import Request

from . import automations
from . import hubdb as H
from . import models as M
from . import org_rules
from . import views
from .store import Problem

LIST_DAYS = 30
LIST_DAYS_MAX = 90
PAGE = 50
PAGE_MAX = 100
WORDS = 200
RESTORE_ACTION = "assistant.change.restore"
MOVES = ("move", "reassign")  # the task history kinds an undo puts back
UNDO_WHY = "Undo of the Assistant's change"


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
            return listing(c, auth, who, days, limit, before, settings)

    @app.post("/api/v2/assistant/changes/{cid}/restore")
    def assistant_restore(request: Request, cid: str, body: M.Empty):
        """Take one of your Assistant's changes back, as you. Restoring it again changes nothing."""
        who = request.state.identity
        if who.role not in ("owner", "human"):
            raise Problem("identity", "This frontend endpoint requires a person", 403)

        def work(c):
            return restore(c, auth, who, cid, settings)

        return mutate(request, body, work)


def listing(c, auth, who, days, limit, before, settings):
    """One page of the caller's Assistant changes, newest first. `next` is the `<at>|<id>` of the last row shown; `before`
    takes it back. The cursor is applied in each source's SQL, so every source yields its next `limit + 1` rows after it:
    a page never skips a row, and `next` is null only when nothing is left."""
    since = H.shift(H.now(), days=-days)
    if before:
        at, _, cid = before.partition("|")
        cut, cut_args = "(created_at < ? OR (created_at = ? AND id < ?))", (at, at, cid)
    else:
        cut, cut_args = "created_at <= ?", (H.now(),)
    fetch = limit + 1
    sources = (
        (
            "event",
            "SELECT * FROM events WHERE actor=? AND created_at>=? AND " + cut + " AND action='task.create' "
            "AND json_extract(detail,'$.via')='assistant' ORDER BY created_at DESC, id DESC LIMIT ?",
            (who.actor, since, *cut_args, fetch),
        ),
        (
            "move",
            "SELECT * FROM history_events WHERE subject_type='task' AND actor_id=? AND created_at>=? AND " + cut + " "
            "AND via='assistant' AND kind IN (?,?) AND coalesce(json_extract(data,'$.move'),'') <> 'new' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (who.actor, since, *cut_args, *MOVES, fetch),
        ),
        (
            "audit",
            "SELECT * FROM audit_log WHERE actor=? AND created_at>=? AND " + cut + " AND via='assistant' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (who.actor, since, *cut_args, fetch),
        ),
        (
            "message",
            "SELECT * FROM messages WHERE sender_id=? AND created_at>=? AND " + cut + " "
            "AND refs LIKE '%assistant%' AND json_extract(refs,'$.via')='assistant' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (who.actor, since, *cut_args, fetch),
        ),
        (
            "setting",
            "SELECT * FROM settings_changes WHERE actor=? AND created_at>=? AND " + cut + " AND via='assistant' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (who.actor, since, *cut_args, fetch),
        ),
    )
    rows = []
    for kind, sql, args in sources:
        rows += [(kind, dict(row)) for row in c.execute(sql, args)]
    rows.sort(key=lambda x: (x[1]["created_at"], x[1]["id"]), reverse=True)
    shown = [describe(c, auth, who, kind, row, settings) for kind, row in rows[:limit]]
    last = shown[-1] if len(rows) > limit and shown else None
    return {"actor": who.actor, "days": days, "changes": shown, "next": f"{last['at']}|{last['id']}" if last else None}


def describe(c, auth, who, kind, row, settings):
    """One change in the list: its id (the restore key), when, what, the words it acted on, its object, and undo."""
    cid = row["id"]
    if kind == "event":
        detail = json.loads(row["detail"] or "{}") or {}
        target = row["target"] or ""
        obj = target if ":" in target else f"task:{target}"
        what = event_line(c, auth, who, row, detail, obj)
        words = cited_words(c, detail.get("delegation"))
        undo, why = undoable(c, auth, who, kind, row, detail, settings)
    elif kind == "move":
        detail = json.loads(row["data"] or "{}") or {}
        obj = f"task:{row['subject_id']}"
        what = move_line(c, auth, who, row, detail)
        words = cited_words(c, detail.get("delegation") or move_delegation(c, row))
        undo, why = undoable(c, auth, who, kind, row, detail, settings)
    elif kind == "audit":
        obj = row["object"]
        before = json.loads(row["before"]) if row["before"] else None
        after = json.loads(row["after"]) if row["after"] else None
        what = audit_line(obj, before, after)
        words = ""
        undo, why = undoable(c, auth, who, kind, row, {"before": before, "after": after}, settings)
    elif kind == "setting":
        obj = f"setting:{row['bot']}:{row['field']}"
        what = setting_line(c, row)
        words = ""
        undo, why = undoable(c, auth, who, kind, row, {}, settings)
    else:
        obj = f"message:{cid}"
        what = "Answered a question" if row["kind"] == "answer" else "Sent a message"
        words = (row["body"] or "")[:WORDS]
        undo, why = False, "a message sent stays sent"
    out = {
        "id": cid,
        "at": row["created_at"],
        "what": what[:200],
        "words": words[:WORDS],
        "object": obj,
        "undoable": bool(undo),
    }
    if not undo:
        out["why_not"] = why
    return out


def setting_line(c, row):
    """A plain line for a bot setting the Assistant changed for the person: the bot, the setting and its new value."""
    bot = (H.bot(c, row["bot"]) or {}).get("name") or row["bot"]
    return f"Changed {bot}'s {row['field'].replace('_', ' ')}"


def _label(c, auth, who, task_id):
    """A task as a line names it: its title, or that the person can no longer see it."""
    if can_see(c, auth, who, task_id):
        return f"'{(H.task(c, task_id) or {}).get('title') or 'a task'}'"
    return "a task you can no longer see"


def event_line(c, auth, who, row, detail, obj):
    """A plain line for a task the Assistant filed."""
    return f"Filed {_label(c, auth, who, row['target'])}" if obj.startswith("task:") else f"Changed {obj}"


def move_line(c, auth, who, row, detail):
    """A plain line for a task the Assistant moved or gave to someone: done, closed, reopened, moved to a stage."""
    label = _label(c, auth, who, row["subject_id"])
    if row["kind"] == "reassign":
        return f"Gave {label} to {H.actor_name(c, row['new']) if row['new'] else 'nobody'}"
    move = detail.get("move")
    words = {"done": "Marked {} done", "closed": "Closed {}", "reopen": "Reopened {}", "back": "Moved {} back"}
    if move in words:
        return words[move].format(label)
    stage = c.execute("SELECT name FROM stages WHERE id=?", (row["new"],)).fetchone()
    return f"Moved {label} to {stage['name'] if stage else 'another stage'}"


def audit_line(obj, before, after):
    """A plain line for an automation or org rule change in the audit trail."""
    kind, _, _ = obj.partition(":")
    if kind == "automation":
        name = (after or before or {}).get("name") or "an automation"
        return (
            f"Created automation '{name}'"
            if before is None
            else (f"Deleted automation '{name}'" if after is None else f"Changed automation '{name}'")
        )
    if kind == "org_rule":
        text = ((after or before or {}).get("text") or "a rule")[:120]
        return (
            f"Added the rule: {text}"
            if before is None
            else (
                f"Retired the rule: {text}"
                if after is None or (after or {}).get("status") == "retired"
                else f"Changed the rule: {text}"
            )
        )
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


def move_delegation(c, row):
    """The cited request of a move or hand-over: a task's history row does not keep it, the event its effects write in
    the same write does (`H.event` stamps `delegation` on a write made via the Assistant; backend/task_effects.py)."""
    found = c.execute(
        "SELECT detail FROM events WHERE actor=? AND target=? AND action IN ('task.close','task.update') "
        "AND created_at>=? AND json_extract(detail,'$.via')='assistant' ORDER BY created_at, id LIMIT 1",
        (row["actor_id"], row["subject_id"], row["created_at"]),
    ).fetchone()
    return (json.loads(found["detail"] or "{}") or {}).get("delegation") if found else None


def can_see(c, auth, who, task_id):
    """The task is one the person may read now (the list and the undo both ask this, not what it was)."""
    return bool(task_id) and bool(views._readable_task_ids(c, auth.task_sql(c, who), [task_id]))


def _last_change(c, task_id):
    """The task's newest move or hand-over, as its history has it."""
    return c.execute(
        "SELECT id FROM history_events WHERE subject_type='task' AND subject_id=? AND kind IN (?,?) "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (task_id, *MOVES),
    ).fetchone()


def undoable(c, auth, who, kind, row, detail, settings):
    """(undoable, why_not): whether the change can be taken back as it stands, and the reason when it cannot."""
    if kind == "event":
        return False, "close the task on its page if it is not wanted"
    if kind == "move":
        task_id = row["subject_id"]
        if not can_see(c, auth, who, task_id):
            return False, "a task you can no longer see"
        latest = _last_change(c, task_id)
        if not latest or latest["id"] != row["id"]:
            return False, "it changed again since"
        task = H.task(c, task_id) or {}
        if row["kind"] == "reassign":
            if not row["old"]:
                return False, "it had no owner before"
            if task.get("owner_id") != row["new"]:
                return False, "it changed again since"
            return True, ""
        if not row["old"] or task.get("stage_id") != row["new"]:
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
            now_flow = json.loads(current["flow"])
            if not (automations.may_change(c, who, now_flow) and automations.may_change(c, who, before)):
                return False, "you no longer manage this automation"
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
    detail = (
        json.loads(row["detail"] or "{}")
        if kind == "event"
        else json.loads(row["data"] or "{}") or {}
        if kind == "move"
        else {
            "before": json.loads(row["before"]) if row["before"] else None,
            "after": json.loads(row["after"]) if row["after"] else None,
        }
    )
    if kind == "setting":
        undo, why = undoable(c, auth, who, kind, row, {}, settings)
        if not undo:
            raise Problem("not_undoable", why, 409)
        config = settings._config(c, row["bot"])
        settings.undo(c, who, row["id"], M.SettingsUndo(expected_revision=config["revision"]))
        obj = f"setting:{row['bot']}:{row['field']}"
    elif kind in ("event", "move"):
        undo, why = undoable(c, auth, who, kind, row, detail, settings)
        if not undo:
            raise Problem("not_undoable", why, 409)
        restore_task(c, who, row)
        obj = f"task:{row['subject_id']}"
    else:
        undo, why = undoable(c, auth, who, kind, row, detail, settings)
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
    row = c.execute(
        "SELECT * FROM settings_changes WHERE id=? AND actor=? AND via='assistant'", (cid, who.actor)
    ).fetchone()
    if row:
        return dict(row), "setting"
    row = c.execute(
        "SELECT * FROM events WHERE id=? AND actor=? AND json_extract(detail,'$.via')='assistant'", (cid, who.actor)
    ).fetchone()
    if row:
        return dict(row), "event"
    row = c.execute(
        "SELECT * FROM history_events WHERE id=? AND subject_type='task' AND actor_id=? AND via='assistant' "
        "AND kind IN (?,?)",
        (cid, who.actor, *MOVES),
    ).fetchone()
    if row:
        return dict(row), "move"
    row = c.execute("SELECT * FROM audit_log WHERE id=? AND actor=? AND via='assistant'", (cid, who.actor)).fetchone()
    if row:
        return dict(row), "audit"
    row = c.execute(
        "SELECT * FROM messages WHERE id=? AND sender_id=? AND refs LIKE '%assistant%' "
        "AND json_extract(refs,'$.via')='assistant'",
        (cid, who.actor),
    ).fetchone()
    if row:
        return dict(row), "message"
    return None, None


def restore_task(c, who, row):
    """Take a move or a hand-over back, through the task calls: a move back to the stage the task was in, a reopen
    undone by finishing or closing it again, a hand-over undone by giving it back. Always with a why."""
    task_id = row["subject_id"]
    if row["kind"] == "reassign":
        H.task_reassign(c, who.actor, task_id, row["old"], UNDO_WHY)
        return
    stage = c.execute("SELECT kind FROM stages WHERE id=?", (row["old"],)).fetchone()
    to = {"done": H.TASK_DONE, "closed": H.TASK_CLOSED}.get(stage["kind"] if stage else "", row["old"])
    if (H.task(c, task_id) or {}).get("status") != "open" and to not in (H.TASK_DONE, H.TASK_CLOSED):
        to = H.TASK_REOPEN  # a done or closed task goes back to work by a reopen, which lands where it left
    H.task_move(c, who.actor, task_id, to, UNDO_WHY)


def restore_automation(c, who, aid, before):
    """Save the automation's earlier flow as a new version, the way a restore of an old version does. The person must
    manage it now (its current flow) and may write the flow they restore."""
    current = c.execute("SELECT flow FROM automations WHERE id=?", (aid,)).fetchone()
    if not current:
        raise Problem("not_found", "that automation no longer exists", 404)
    now_flow = json.loads(current["flow"])
    from . import assistant_routines

    if (
        who.via == "assistant"
        and not who.confirmed
        and before.get("enabled")
        and (assistant_routines.message(before) or assistant_routines.message(now_flow))
    ):
        raise Problem(
            "confirm_required",
            "Restoring an active inbox routine needs a Confirm card; propose its "
            "earlier flow with PATCH /api/v2/automations/" + aid,
            403,
        )
    if not (automations.may_change(c, who, now_flow) and automations.may_change(c, who, before)):
        raise Problem("forbidden", "you may not change this automation", 403)
    automations._save(c, who.actor, aid, before, now_flow, new_version=True)
