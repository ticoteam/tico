"""Why an item is on someone's list: `GET /api/v2/origins/{ref}` (docs/assistant.md, "Why is this here").

A ref is a task (its id, or its first 8 or more characters), a message (a bot's question) or an approval. The answer says
what made it: who filed it, the cause (the automation that opened it, the org rule that asked for it, the parent task it
came from, or the bot that filed it alone), how many items that cause made in the last seven days and how many of them
are still open, and up to three commands that would stop the source. Every command is a `hub` line the caller can run.
A ref the caller cannot read answers 404, as if it did not exist.
"""

import json
import shlex

from fastapi import Request

from . import hubdb as H
from . import task_privacy as privacy
from . import views
from .store import Problem

WINDOW_DAYS = 7
STOPS_MAX = 3


def install(app, store, auth):
    @app.get("/api/v2/origins/{ref}")
    def origin_show(request: Request, ref: str):
        """Why this task, message or approval is on the caller's list: its maker, its cause, its siblings in the last
        seven days and the commands that would stop the source."""
        who = request.state.identity
        with store.read_transaction() as c:
            return show(c, auth, who, ref)


def show(c, auth, who, ref):
    kind, item = resolve(c, auth, who, ref)
    if kind == "task":
        made_by, created, cause = item["requester_id"], item["created_at"], task_cause(c, item)
        title, siblings, stops = item["title"] or "", *siblings_and_stops(c, item, cause)
        task_id = item["id"]
    elif kind == "message":
        made_by, created, title, task_id = item["sender_id"], item["created_at"], (item["body"] or "")[:80], None
        row = H.task(c, H.message_task_id(item)) if H.message_task_id(item) else None
        cause = task_cause(c, row) if row else {"type": "message", "conversation_id": item["conversation_id"]}
        siblings, stops = {"days": WINDOW_DAYS, "made": 0, "open": 0}, stops_for(c, cause, None)
    else:
        made_by, created, title, task_id = item["requested_by_id"], item["created_at"], item["kind"], item.get("task_id")
        cause = {"type": "approval", "run": approval_run(c, item["id"]), "task": task_id}
        siblings, stops = {"days": WINDOW_DAYS, "made": 0, "open": 0}, stops_for(c, cause, None)
    return {"ref": ref, "kind": kind, "id": item["id"], "title": title, "created_at": created,
            "made_by": {"actor": made_by, "name": H.actor_name(c, made_by) if made_by else ""},
            "task": task_id, "cause": cause, "siblings": siblings, "stop": stops[:STOPS_MAX]}


def resolve(c, auth, who, ref):
    """(kind, row) for the one thing `ref` names that the caller may read; 404 when none does."""
    if len(ref) >= 8:
        rows = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE id=? OR id LIKE ? LIMIT 2", (ref, ref + "%"))]
        if len(rows) == 1:
            visible = auth.task_sql(c, who)
            if views._readable_task_ids(c, visible, [rows[0]["id"]]):
                return "task", rows[0]
    message = H.message(c, ref)
    if message and privacy.message_readable(c, privacy.actor(who), message):
        return "message", message
    approval = H.approval(c, ref)
    if approval and (who.actor in (approval.get("requested_by_id"), approval.get("decision_owner"))
                     or (approval.get("task_id") and views._readable_task_ids(
                         c, auth.task_sql(c, who), [approval["task_id"]]))):
        return "approval", approval
    raise Problem("not_found", f"no task, message or approval {ref} that you can read", 404)


def approval_run(c, approval_id):
    """The run that asked for an approval, as its request was recorded (hubdb `approval.request`)."""
    row = c.execute("SELECT detail FROM events WHERE action='approval.request' AND target=? "
                    "ORDER BY created_at DESC LIMIT 1", (approval_id,)).fetchone()
    return (json.loads(row["detail"] or "{}") or {}).get("attempt_id") if row else None


def notice_refs(c, task):
    """The refs of the notice that told the task's owner about it (the first one that names an automation or a rule)."""
    if not task.get("conversation_id"):
        return {}
    for row in c.execute("SELECT refs FROM messages WHERE conversation_id=? AND refs LIKE '%\"task\"%' "
                         "ORDER BY created_at LIMIT 200", (task["conversation_id"],)):
        refs = json.loads(row["refs"] or "{}") if row["refs"] else {}
        if isinstance(refs, dict) and refs.get("task") == task["id"] and ("automation" in refs or "rule" in refs):
            return refs
    return {}


def task_cause(c, task):
    """What made the task: an automation's run (its routine's marker, or the notice that opened it), an org rule, the
    parent task it was filed under, or a bot's own filing."""
    refs = notice_refs(c, task)
    occurrence = (task.get("occurrence") or "").split(":", 1)[0]
    automation = occurrence or refs.get("automation")
    if automation:
        found = automation_info(c, automation)
        return {"type": "automation", **found, "run": refs.get("automation_run")}
    if refs.get("rule"):
        rule = c.execute("SELECT text, version FROM org_rules WHERE id=?", (refs["rule"],)).fetchone()
        return {"type": "rule", "id": refs["rule"], "text": (rule["text"] if rule else "")[:200],
                "version": rule["version"] if rule else 1}
    if task.get("parent_id"):
        parent = H.task(c, task["parent_id"]) or {}
        return {"type": "parent_task", "id": task["parent_id"], "title": (parent.get("title") or "")[:80]}
    if H.is_bot(task.get("requester_id")):
        return {"type": "bot", "bot": task["requester_id"]}
    return {"type": "person", "person": task.get("requester_id")}


def automation_info(c, aid):
    row = c.execute("SELECT name, trigger_type, enabled, flow FROM automations WHERE id=?", (aid,)).fetchone()
    if not row:
        return {"id": aid, "name": "", "trigger": None, "enabled": None}
    trigger = (json.loads(row["flow"] or "{}") or {}).get("trigger") or {}
    keep = {k: trigger[k] for k in ("type", "cron", "every", "tz", "event") if k in trigger}
    return {"id": aid, "name": row["name"], "trigger": keep or {"type": row["trigger_type"]},
            "enabled": bool(row["enabled"])}


def siblings_and_stops(c, task, cause):
    """(siblings, stops) for a task: the same cause's items made in the window, how many of them are open, and stops."""
    since = H.shift(H.now(), days=-WINDOW_DAYS)
    if cause["type"] in ("automation", "rule"):
        made = set()
        key = "automation" if cause["type"] == "automation" else "rule"
        for row in c.execute("SELECT refs FROM messages WHERE created_at>=? AND refs LIKE ? ORDER BY created_at LIMIT 5000",
                             (since, f'%"{key}": "{cause["id"]}"%')):
            refs = json.loads(row["refs"] or "{}") if row["refs"] else {}
            if isinstance(refs, dict) and refs.get(key) == cause["id"] and refs.get("task"):
                made.add(refs["task"])
    elif cause["type"] == "parent_task":
        made = {r["id"] for r in c.execute("SELECT id FROM tasks WHERE parent_id=? AND created_at>=?",
                                           (cause["id"], since))}
    else:
        made = {r["id"] for r in c.execute("SELECT id FROM tasks WHERE requester_id=? AND title=? AND created_at>=?",
                                           (task["requester_id"], task["title"], since))}
    open_now = 0
    for part in H.chunks(sorted(made)):
        marks = ",".join("?" * len(part))
        open_now += c.execute(f"SELECT COUNT(*) FROM tasks WHERE id IN ({marks}) AND status NOT IN ('done','closed','declined')",
                              part).fetchone()[0]
    siblings = {"days": WINDOW_DAYS, "made": len(made), "open": open_now}
    return siblings, stops_for(c, cause, task)


def stops_for(c, cause, task):
    """Up to three `hub` lines that stop the source: each `{how, command, note}`, and every command parses with clients/hubcli."""
    kind = cause.get("type")
    if kind == "automation" and cause.get("id"):
        return [{"how": "Turn the automation off", "command": f"hub automation update {cause['id']} --disable",
                 "note": "its runs stop; `--enable` turns it back on"},
                {"how": "Change what it does", "command": f"hub automation update {cause['id']} --file -",
                 "note": "a JSON object with the fields to change, on stdin; a change to its steps is a new version"}]
    if kind == "rule" and cause.get("id"):
        return [{"how": "Retire the rule", "command": f"hub rules retire {cause['id']} --version {cause.get('version', 1)}",
                 "note": "its due dates come back"}]
    if kind == "bot" and cause.get("bot"):
        slug = (H.bot(c, H.actor_id(cause["bot"])) or {}).get("slug") or H.actor_id(cause["bot"])
        title = (task or {}).get("title") or "its work"
        return [{"how": "Close what it filed in one go (counts first)",
                 "command": f"hub task bulk close --where-requester {shlex.quote(slug)} --dry-run",
                 "note": "drop --dry-run to close them"},
                {"how": "Pause the bot until its source is fixed", "command": f"hub bot pause {shlex.quote(slug)}"},
                {"how": "Have BotOps fix the bot's source",
                 "command": "hub task create --owner botops --title "
                            + shlex.quote(f"Stop {slug} filing: {title[:60]}"),
                 "note": "BotOps reads the cause and files the fix in the bot's code"}]
    if kind == "parent_task" and cause.get("id"):
        return [{"how": "Close the parent task", "command": f"hub task close {cause['id']}",
                 "note": "its open children stay as they are"}]
    return []
