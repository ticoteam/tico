"""The tasks page's right bar and the Task Manager, its built-in bot (Task checks 2/2).

The right bar shows the task checks and task rules for the task type in view (backend/task_checks.py,
backend/task_rules.py): each with its switch, or one read-only line when the person may not change it, its recent
catches on tasks the person can read, and right / wrong marks on each catch (`task_check_marks`), which feed the weekly
flow report. The collapsed bar shows today's catch count.

The Task Manager is in every company with no setup and no hiring: it is not a bot row, has no runner, no token and no
rights of its own. Each message is one call to Claude Haiku 5.5 through the team's AI setting (`writer_for`): the Tico
AI Gateway's Tico Cheap (Haiku 5.5 underneath) when the team's models choice spends the Gateway and it is connected,
else the team's own Anthropic key. With neither it says so and does nothing. The call carries the person's words, the
recent chat, and what the person can already see of the setup (task types, steps, statuses, the rules with whether
they may change each one, the check catalog, the tabs they may edit). Never a task: the only tasks it ever names are a
Check all's results, read through the API as the person.

What it may do is a fixed list (`ACTIONS`). Every write is a card the chatting person confirms with their own click;
the confirm replays it through the same API route with the credential they came in with (`assistant.Internal`), so
the route checks their rights exactly as if they had clicked in Settings, and a refusal comes back in its words. A
card it may not offer (a rule of another department, a bot's tabs the person does not manage) is declined with who
can. It is never in the create path: no task create calls it. It never deletes, reassigns to another person, spends,
sends outside Tico or publishes, and it does not do the work inside tasks or answer their questions.
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import Request

from . import task_checks as TC
from . import task_rules as TR
from .store import H, Problem, encode

LOG = logging.getLogger("tico.task_manager")

NAME = "Task Manager"
MODEL = "claude-haiku-5-5"
GATEWAY_MODEL = "tico-cheap-1.1"          # the Gateway's Claude Haiku 5.5 (backend/providers.py)
DAILY = 100                               # a person's messages a day
HISTORY = 10                              # turns of the chat the model sees
PAGE = 50                                 # messages the panel reads
BODY = 4000
CARD_TTL_H = 24
CATCHES = 5                               # recent catches a row shows
CATCH_SCAN = 40
REPORT_SUGGESTIONS = 2
WRONG_SHARE = 0.3

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_manager_messages(
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL, card_json TEXT, state TEXT,
 result TEXT, created TEXT NOT NULL, decided_at TEXT);
CREATE INDEX IF NOT EXISTS task_manager_messages_actor ON task_manager_messages(actor, created);
CREATE TABLE IF NOT EXISTS task_check_marks(
 event_id TEXT NOT NULL, actor TEXT NOT NULL, rule TEXT NOT NULL, verdict TEXT NOT NULL CHECK(verdict IN ('right','wrong')),
 at TEXT NOT NULL, PRIMARY KEY(event_id, actor));
CREATE INDEX IF NOT EXISTS task_check_marks_at ON task_check_marks(at);
"""

UNCONFIGURED = ("The Task Manager runs on the team's AI setting: connect the Tico AI Gateway or add the team's "
                "Anthropic key in Settings > Computers.")
LIMITS = ("I can't do that. I propose checks and rule changes, set up tabs, run Check all and offer its fix, and make "
          "tasks through the same checks. I never delete, reassign to another person, spend, send or publish, and I "
          "don't do the work inside tasks.")


def person(who):
    """Only a person chats with the Task Manager and confirms its cards, with their own sign-in."""
    return bool(who) and who.role in ("owner", "human") and not who.support and not who.via


def require_person(who):
    if not person(who):
        raise Problem("forbidden", "Only a person, signed in as themselves, uses the Task Manager", 403)


# ----------------------------------------------------------------------------- the model
def writer_for(c, settings, store=None, vault=None, env=None):
    """`write(system, user)` on Claude Haiku 5.5 through the team's AI setting, or None. The Gateway key is read at
    each call, never held by the writer."""
    if settings.rehearsal:
        return None
    from clients import gateway as G, judge as J
    from . import gateway_connection, models_preset, providers
    record = providers.load(c, settings)
    if store is not None and models_preset.spends_gateway(record) and gateway_connection.connected(c, settings):
        base = G.chat_base(gateway_connection.url(c, settings))

        def key_of():
            from .credentials import Vault
            keys = vault or Vault(store)
            with store.read() as c:
                return gateway_connection.key(c, keys, settings) if keys.configured or gateway_connection.env_key(settings) else ""
        return J.gateway_writer(key_of, base, GATEWAY_MODEL)
    env = os.environ if env is None else env
    key = (env.get(J.LLM_KEYS["anthropic"]) or "").strip()
    if "anthropic" in (record.get("enabled") or []) and key:
        return J.llm_writer("anthropic", key, MODEL)
    return None


SYSTEM = """You are the Task Manager, a built-in helper on a team's Tasks page in Tico. You know task types, steps,
statuses, filters, tabs, views, task rules, task checks and routines. You act only with the rights of the person
chatting, and every change you propose waits for their click.

A task check row reads: WHEN <trigger> FOR <task types | all types> CHECK <condition> THEN <action>. The checks you can
propose are only those in `catalog`; each has one trigger and its own actions.

Answer with one JSON object and nothing else: {"reply": "<one to three plain sentences>", "action": null | {...}}.
The actions, the only ones you have:
- {"kind": "propose_check", "check": "<catalog key>", "name": "<short rule name>", "types": ["<task type id>", ...] (empty
  is all types), "then": ["<action key>", ...], "hours": <whole hours, quiet_wait only>, "department": "<department id,
  optional>"}: turn a complaint into a new check row.
- {"kind": "change_rule", "rule": "<rule key>", "on": true|false, "then": [...], "types": [...], "hours": <n>}: change an
  existing rule; leave out what stays.
- {"kind": "save_tab", "bot": "<bot slug from `tabs`, or empty for the person's own>", "name": "<tab name>",
  "conditions": [{"field": "<field>", "value": ...}]}: a saved filter as a tab. Fields: status, step, type, assigned_to,
  asked_by, waiting_on, created_within, updated_within, done_within, due, label.
- {"kind": "check_all"}: run every check as a dry run over the tasks the person can read, and offer the fix.
- {"kind": "create_task", "title": "...", "body": "...", "owner": "<bot slug or person>", "type": "<task type id>"}: make
  a task the person asked for; it goes through the same checks as any create.
Never offer to delete, reassign a task to another person, spend, send outside Tico or publish; never do the work inside
a task or answer a task's own question. Say so plainly when asked. Never invent a rule key, type id or bot."""


def context(c, settings, auth, who, type_id):
    """What the model is shown: the setup the person can already see, and nothing of any task."""
    types = [{"id": t["id"], "name": t["name"], "steps": [s["name"] for s in t.get("steps") or []]}
             for t in H.type_list(c)][:40]
    rules = []
    for job in TR.listing(c, settings, who, auth):
        rules.append({"key": job["key"], "name": job["name"], "on": job["on"], "department": job["department"]["name"],
                      "for": job.get("for", "all types"), "when": job["when"], "check": job.get("check_words"),
                      "then": job["then"], "you_may_change": job["can_change"]})
    from . import bot_task_tabs as BT
    tabs = [{"bot": "", "name": "your own tabs"}]
    for row in c.execute("SELECT slug FROM bots WHERE coalesce(state,'active')='active' ORDER BY slug LIMIT 200"):
        slug = row[0]
        if slug != settings.assistant_bot and auth.visible_bot(c, who, slug) and BT.may_edit(c, auth, settings, who, slug):
            tabs.append({"bot": slug})
        if len(tabs) > 30:
            break
    return {"type_in_view": type_id, "task_types": types, "statuses": list(H.TASK_STATUSES), "rules": rules,
            "catalog": TC.catalog(), "tabs": tabs,
            "departments": [d["id"] for d in TR.departments(settings)],
            "your_department": TR.department_of(c, settings, who.actor)}


def parse(out):
    """The model's answer as (reply, action): anything else is a reply with no action."""
    if not isinstance(out, dict):
        return "", None
    reply = str(out.get("reply") or "").strip()[:BODY]
    action = out.get("action")
    return reply, action if isinstance(action, dict) and isinstance(action.get("kind"), str) else None


# ----------------------------------------------------------------------------- cards
class Declined(Exception):
    pass


def _types(c, values):
    out = []
    for value in values or []:
        found = H.type_get(c, str(value)) if value else None
        if not found:
            raise Declined(f"There is no task type '{value}'.")
        out.append(found["id"])
    return list(dict.fromkeys(out))


def only_line(job):
    return f"Only {job['department']['name']} can change this rule."


def row_words(c, check, types, then):
    spec = TC.CHECKS[check["key"]]
    acts = [TC.ACTIONS[a] for a in then]
    return (f"WHEN {TC.TRIGGERS[spec['trigger']]} FOR {TC.for_words(c, types)} CHECK {spec['words'](check)} "
            f"THEN {', then '.join(acts)}")


def propose_check(c, settings, auth, who, action):
    key = action.get("check")
    if key not in TC.CHECKS:
        raise Declined("That isn't a check I can set up; the checks are " + ", ".join(TC.CHECKS) + ".")
    spec = TC.CHECKS[key]
    types = _types(c, action.get("types"))
    then = [a for a in (action.get("then") or [spec["actions"][0]]) if isinstance(a, str)]
    check = {"key": key, **({"hours": action["hours"]} if spec["hours"] and action.get("hours") is not None else {})}
    try:
        TC.validate([{"key": spec["trigger"]}], then, check, types)
    except Problem as exc:
        raise Declined(exc.detail + ".")
    charts = {d["id"]: d["name"] for d in TR.departments(settings)}
    department = action.get("department") or TR.department_of(c, settings, who.actor)
    if not department and (who.role == "owner" or auth.bot_admin(who)):
        department = TC.SEED_DEPARTMENT
    if department not in charts:
        raise Declined("A check belongs to a department, and you are in none. Only a department's members, the owner "
                       "or an admin can add one.")
    if not TR.can_change(c, settings, auth, who, department):
        raise Declined(f"Only {charts[department]} can add a rule for {charts[department]}.")
    name = str(action.get("name") or "").strip()[:80] or spec["words"](check)[:80]
    body = {"name": name, "department": department, "check": check, "types": types,
            "triggers": [{"key": spec["trigger"]}], "actions": then, "on": not (spec["hours"] and not check.get("hours"))}
    words = f"New rule '{name}' ({charts[department]}): {row_words(c, check, types, then)}"
    return {"kind": "propose_check", "summary": words, "method": "POST", "path": "/api/v2/task-rules", "body": body}


def change_rule(c, settings, auth, who, action):
    key = str(action.get("rule") or "")
    rule = c.execute("SELECT * FROM task_rules WHERE id=? OR name=? COLLATE NOCASE", (key, key)).fetchone()
    if not rule:
        raise Declined(f"There is no rule '{key}'.")
    rule = dict(rule)
    job = TR.row(c, settings, rule, who, auth)
    if not job["can_change"]:
        raise Declined(only_line(job))
    body, said = {}, []
    if isinstance(action.get("on"), bool) and action["on"] != job["on"]:
        body["on"] = action["on"]
        said.append("switch it " + ("on" if action["on"] else "off"))
    check = TC.check_of(rule)
    if check:
        types = TC.types_of(rule)
        then = json.loads(rule["actions_json"])
        if action.get("types") is not None:
            types = body["types"] = _types(c, action["types"])
        if action.get("then"):
            then = body["actions"] = [a for a in action["then"] if isinstance(a, str)]
        if action.get("hours") is not None and TC.CHECKS[check["key"]]["hours"]:
            check = body["check"] = {"key": check["key"], "hours": action["hours"]}
        try:
            TC.validate([{"key": TC.CHECKS[check["key"]]["trigger"]}], then, check, types)
        except Problem as exc:
            raise Declined(exc.detail + ".")
        if {"types", "actions", "check"} & set(body):
            said.append("it will read: " + row_words(c, check, types, then))
    elif {"types", "then", "hours"} & {k for k, v in action.items() if v is not None}:
        raise Declined(f"'{rule['name']}' is a close rule; change its wait in Settings > Routines.")
    if not body:
        raise Declined(f"'{rule['name']}' already reads that way.")
    return {"kind": "change_rule", "summary": f"Change '{rule['name']}': " + "; ".join(said), "method": "PUT",
            "path": "/api/v2/task-rules/" + rule["id"], "body": body}


def save_tab(c, settings, auth, who, action):
    from . import bot_task_tabs as BT
    from . import models as M
    bot = str(action.get("bot") or "").strip() or settings.assistant_bot
    if not H.bot(c, bot) or not auth.visible_bot(c, who, bot):
        raise Declined(f"There is no bot '{bot}'.")
    if not BT.may_edit(c, auth, settings, who, bot):
        raise Declined(f"Only {bot} itself or a person who manages it can change its tabs.")
    try:
        tab = M.BotTaskTab(name=str(action.get("name") or "")[:40] or "Saved filter",
                           conditions=[M.BotTaskCondition(**r) for r in action.get("conditions") or []])
        BT.clean(c, [tab])
    except Problem as exc:
        raise Declined(exc.detail + ".")
    except (TypeError, ValueError):
        raise Declined("That filter uses a field or value tabs don't have.")
    if not tab.conditions:
        raise Declined("A saved filter needs at least one row.")
    whose = "your own tabs" if BT.personal(settings, bot) else f"{bot}'s tabs"
    return {"kind": "save_tab", "summary": f"Add the tab '{tab.name}' to {whose}", "method": "PUT",
            "path": f"/api/v2/bots/{bot}/task-tabs", "body": {"tab": tab.model_dump(include={"name", "show_count", "conditions"})}}


def create_task(c, settings, auth, who, action):
    title = str(action.get("title") or "").strip()[:200]
    owner = str(action.get("owner") or "").strip()
    if not title or not owner:
        raise Declined("Say what the task is and who it is for.")
    body = {"title": title, "owner": owner, "body": str(action.get("body") or "")[:BODY]}
    if action.get("type"):
        body["type"] = _types(c, [action["type"]])[0]
    return {"kind": "create_task", "summary": f"Make the task '{title}' for {owner}", "method": "POST",
            "path": "/api/v2/tasks", "body": body}


ACTIONS = {"propose_check": propose_check, "change_rule": change_rule, "save_tab": save_tab, "create_task": create_task}
# What a confirm may replay: the paths the cards above build, and the comment offered after a blocked create.
ROUTES = (("POST", re.compile(r"/api/v2/task-rules")), ("PUT", re.compile(r"/api/v2/task-rules/[\w-]+")),
          ("PUT", re.compile(r"/api/v2/bots/[\w-]+/task-tabs")), ("POST", re.compile(r"/api/v2/tasks")),
          ("POST", re.compile(r"/api/v2/task-rules/[\w-]+/fix")), ("POST", re.compile(r"/api/v2/tasks/[\w-]+/comments")))


def replayable(card):
    return any(card.get("method") == m and p.fullmatch(card.get("path") or "") for m, p in ROUTES)


def fix_cards(results):
    """A Check all's results as words and one fix card per check that found something."""
    lines, cards = [], []
    for r in results:
        found = r.get("found") or []
        if not found:
            continue
        lines.append(f"{r['name']}: {len(found)} task{'s' if len(found) != 1 else ''} ({r['fix']})")
        titles = ", ".join(f"\"{f['task']['title']}\"" for f in found[:3]) + (" and more" if len(found) > 3 else "")
        cards.append({"kind": "fix", "summary": f"{r['fix'][0].upper() + r['fix'][1:]}: {titles}", "method": "POST",
                      "path": f"/api/v2/task-rules/{r['key']}/fix",
                      "body": {"tasks": [{"task": f["task"]["id"], **({"duplicate_of": f["duplicate_of"]["id"]}
                                                                        if f.get("duplicate_of") else {})} for f in found]}})
    if not lines:
        return "Check all found nothing that breaks a check.", []
    return "Check all found " + "; ".join(lines) + ". Confirm a fix to run it with your own rights.", cards


# ----------------------------------------------------------------------------- messages
def add(c, actor, role, body, card=None):
    mid = H.new_id()
    c.execute("INSERT INTO task_manager_messages(id,actor,role,body,card_json,state,created) VALUES(?,?,?,?,?,?,?)",
              (mid, actor, role, str(body or "")[:BODY], encode(card) if card else None, "pending" if card else None,
               H.now()))
    return mid


def view(row):
    card = json.loads(row["card_json"]) if row["card_json"] else None
    return {"id": row["id"], "role": row["role"], "body": row["body"], "created": row["created"],
            **({"card": {"kind": card["kind"], "summary": card["summary"], "state": row["state"],
                         "result": row["result"]}} if card else {})}


def messages(c, actor, ids=None):
    if ids:
        rows = c.execute(f"SELECT * FROM task_manager_messages WHERE actor=? AND id IN ({','.join('?' * len(ids))}) "
                         "ORDER BY created,rowid", (actor, *ids)).fetchall()
    else:
        rows = c.execute("SELECT * FROM (SELECT *,rowid AS r FROM task_manager_messages WHERE actor=? "
                         "ORDER BY created DESC,rowid DESC LIMIT ?) ORDER BY created,r", (actor, PAGE)).fetchall()
    return [view(r) for r in rows]


def history(c, actor):
    rows = c.execute("SELECT role,body FROM task_manager_messages WHERE actor=? ORDER BY created DESC,rowid DESC LIMIT ?",
                     (actor, HISTORY)).fetchall()
    return [{"from": "person" if r["role"] == "person" else "you", "text": r["body"][:1000]} for r in reversed(rows)]


# ----------------------------------------------------------------------------- the right bar
def catches(c, auth, who, rule_id):
    """The rule's recent catches on tasks the person can read, with their own mark on each."""
    out = []
    for e in c.execute("SELECT id,ts,detail_json FROM events WHERE target=? AND action='task_check.caught' "
                       "ORDER BY ts DESC LIMIT ?", (rule_id, CATCH_SCAN)):
        detail = json.loads(e["detail_json"] or "{}")
        task_id = detail.get("task")
        try:
            task = auth.task(c, who, task_id) if task_id else None
        except Problem:
            task = None
        if not task:
            continue
        mark = c.execute("SELECT verdict FROM task_check_marks WHERE event_id=? AND actor=?", (e["id"], who.actor)).fetchone()
        out.append({"id": e["id"], "at": e["ts"], "task": {"id": task["id"], "title": task["title"], "status": task["status"]},
                    "warned": bool(detail.get("warn")), "action": detail.get("action"),
                    "mark": mark[0] if mark else None})
        if len(out) >= CATCHES:
            break
    return out


def day_start(now):
    return now.strftime("%Y-%m-%dT00:00:00")


def bar(c, settings, auth, who, type_id, now=None):
    now = now or datetime.now(timezone.utc)
    type_id = type_id or H.GENERAL_TYPE
    rows = []
    for raw in c.execute("SELECT * FROM task_rules ORDER BY created,id"):
        rule = dict(raw)
        if TC.check_of(rule) and not TC.covers(rule, type_id):
            continue
        job = TR.row(c, settings, rule, who, auth, now)
        rows.append({k: job.get(k) for k in ("key", "name", "on", "when", "then", "for", "check_words", "what", "stats",
                                              "checkable", "can_change", "department")}
                    | {"read_only": None if job["can_change"] else only_line(job),
                       "catches": catches(c, auth, who, rule["id"])})
    today = c.execute("SELECT count(*) FROM events WHERE action='task_check.caught' AND ts>=?",
                      (day_start(now),)).fetchone()[0]
    typ = H.type_get(c, type_id) or {}
    return {"type": {"id": type_id, "name": typ.get("name") or type_id}, "today": today, "rules": rows,
            "report": report(c, now), "chat": person(who)}


def mark(c, auth, who, event_id, verdict):
    e = c.execute("SELECT * FROM events WHERE id=? AND action='task_check.caught'", (event_id,)).fetchone()
    task_id = json.loads(e["detail_json"] or "{}").get("task") if e else None
    try:
        if not task_id or not auth.task(c, who, task_id):
            raise Problem("not_found", "Catch not found", 404)
    except Problem:
        raise Problem("not_found", "Catch not found", 404)
    if verdict is None:
        c.execute("DELETE FROM task_check_marks WHERE event_id=? AND actor=?", (event_id, who.actor))
    else:
        c.execute("INSERT INTO task_check_marks(event_id,actor,rule,verdict,at) VALUES(?,?,?,?,?) ON CONFLICT(event_id,actor) "
                  "DO UPDATE SET verdict=excluded.verdict,at=excluded.at", (event_id, who.actor, e["target"], verdict, H.now()))
    return {"id": event_id, "mark": verdict}


def report(c, now):
    """The weekly flow report for the last full week (Monday to Monday, UTC), read from the rules' records: catches,
    overrides, wrong marks, quiet waits, and one or two suggestions, at least one when over 30% of marks are wrong."""
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = monday - timedelta(days=7), monday
    since, until = start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")
    names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM task_rules")}
    per = {}
    for target, action, n in c.execute(
            "SELECT target,action,count(*) FROM events WHERE action IN ('task_check.caught','task_check.override') "
            "AND ts>=? AND ts<? GROUP BY target,action", (since, until)):
        if target in names:
            per.setdefault(target, {})[action] = n
    marks = {}
    for rule, verdict, n in c.execute("SELECT rule,verdict,count(*) FROM task_check_marks WHERE at>=? AND at<? "
                                      "GROUP BY rule,verdict", (since, until)):
        marks.setdefault(rule, {})[verdict] = n
    catches_n = sum(v.get("task_check.caught", 0) for v in per.values())
    overrides = sum(v.get("task_check.override", 0) for v in per.values())
    wrong = sum(v.get("wrong", 0) for v in marks.values())
    marked = wrong + sum(v.get("right", 0) for v in marks.values())
    quiet_rules = {r["id"]: dict(r) for r in c.execute("SELECT * FROM task_rules WHERE check_json IS NOT NULL")
                   if (TC.check_of(dict(r)) or {}).get("key") == "quiet_wait"}
    quiet = sum(per.get(k, {}).get("task_check.caught", 0) for k in quiet_rules)
    suggestions = []
    if marked and wrong / marked > WRONG_SHARE:
        worst = max(marks, key=lambda k: marks[k].get("wrong", 0))
        w, m = marks[worst].get("wrong", 0), sum(marks[worst].values())
        suggestions.append(f"'{names.get(worst, worst)}' was marked wrong on {w} of {m} catches: narrow its task types "
                           "or switch it to warn.")
    over = max(per, key=lambda k: per[k].get("task_check.override", 0), default=None)
    if over and per[over].get("task_check.override", 0) >= 3:
        suggestions.append(f"'{names[over]}' was overridden {per[over]['task_check.override']} times: read the reasons "
                           "on its row and narrow it if they agree.")
    if quiet_rules and not any(r["enabled"] for r in quiet_rules.values()):
        suggestions.append("Quiet waits are off: set a quiet time for a department or task type to nudge waiting tasks.")
    return {"week": start.strftime("%Y-%m-%d"), "catches": catches_n, "overrides": overrides, "wrong": wrong,
            "marked": marked, "quiet_waits": quiet, "suggestions": suggestions[:REPORT_SUGGESTIONS]}


# ----------------------------------------------------------------------------- routes
def install(app, store, auth, mutate):
    from . import models as M
    from .assistant import Internal, outcome

    @app.get("/api/v2/task-checks/bar")
    def task_checks_bar(request: Request, type: str = ""):
        """The right bar for the task type in view: its checks and rules with recent catches the caller can read,
        today's catch count and the weekly flow report."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return bar(c, store.settings, auth, who, type[:64])

    @app.post("/api/v2/task-checks/catches/{event_id}/mark")
    def task_check_mark(event_id: str, request: Request, body: M.TaskCheckMark):
        """A person's right / wrong on one catch of a task they can read (null takes it back)."""
        who = request.state.identity
        require_person(who)
        return mutate(request, body, lambda c: mark(c, auth, who, event_id, body.verdict))

    @app.get("/api/v2/task-manager")
    def task_manager_read(request: Request):
        """The caller's own Task Manager chat, newest PAGE messages."""
        who = request.state.identity
        require_person(who)
        with store.read() as c:
            return {"name": NAME, "messages": messages(c, who.actor)}

    @app.post("/api/v2/task-manager")
    async def task_manager_say(request: Request, body: M.TaskManagerSay):
        """One message to the Task Manager: its answer, and a card to confirm when it proposes a change."""
        who = request.state.identity
        require_person(who)
        settings = store.settings
        text = body.text.strip()
        if not text:
            raise Problem("invalid", "Say something", 422)

        def begin():
            with store.transaction() as c:
                sent = c.execute("SELECT count(*) FROM task_manager_messages WHERE actor=? AND role='person' AND created>=?",
                                 (who.actor, day_start(datetime.now(timezone.utc)))).fetchone()[0]
                if sent >= DAILY:
                    raise Problem("rate_limited", f"The Task Manager takes {DAILY} messages a day; ask again tomorrow", 429)
                past = history(c, who.actor)
                mid = add(c, who.actor, "person", text)
            with store.read() as c:
                writer = getattr(app.state, "task_manager_writer", None) or writer_for(c, settings, store)
                return mid, writer, {"message": text, "chat": past, **context(c, settings, auth, who, body.type)}
        mid, writer, state = await asyncio.to_thread(begin)
        ids = [mid]
        if writer is None:
            reply, action = UNCONFIGURED, None
        else:
            try:
                reply, action = parse(await asyncio.to_thread(writer, SYSTEM, json.dumps(state, default=str)))
            except Exception as exc:
                LOG.warning("Task Manager model failed: %s", type(exc).__name__)
                reply, action = "The model behind the Task Manager did not answer; try again shortly.", None
        cards = []
        if action:
            kind = action["kind"]
            if kind == "check_all":
                response = await Internal(request).call("POST", "task-rules/check-all", body={})
                if response.status_code == 200:
                    words, cards = fix_cards(response.json().get("checks") or [])
                else:
                    words = "Check all did not run: " + str(outcome(response)["error"])
                reply = (reply + "\n\n" if reply else "") + words
            elif kind in ACTIONS:
                def plan():
                    with store.read() as c:
                        return ACTIONS[kind](c, settings, auth, who, action)
                try:
                    cards = [await asyncio.to_thread(plan)]
                except Declined as exc:
                    reply = str(exc)
            else:
                reply = LIMITS

        def finish():
            with store.transaction() as c:
                ids.append(add(c, who.actor, "bot", reply or "Done."))
                for card in cards:
                    ids.append(add(c, who.actor, "bot", card["summary"], card))
                H.event(c, who.actor, "task_manager.said", mid, {"action": (action or {}).get("kind"), "cards": len(cards)})
                return {"messages": messages(c, who.actor, ids)}
        return await asyncio.to_thread(finish)

    @app.post("/api/v2/task-manager/cards/{mid}/dismiss")
    def task_manager_dismiss(mid: str, request: Request, body: M.Empty):
        who = request.state.identity
        require_person(who)

        def work(c):
            row = c.execute("SELECT * FROM task_manager_messages WHERE id=? AND actor=? AND card_json IS NOT NULL",
                            (mid, who.actor)).fetchone()
            if not row:
                raise Problem("not_found", "Card not found", 404)
            if row["state"] != "pending":
                raise Problem("state", "This card is already " + row["state"], 409)
            c.execute("UPDATE task_manager_messages SET state='dismissed',decided_at=? WHERE id=?", (H.now(), mid))
            return {"messages": messages(c, who.actor, [mid])}
        return mutate(request, body, work)

    @app.post("/api/v2/task-manager/cards/{mid}/confirm")
    async def task_manager_confirm(mid: str, request: Request):
        """The person's own click on a card: replayed through its route with their credential, so the route checks
        their rights. Only the person the card was made for confirms it, once, within CARD_TTL_H hours."""
        who = request.state.identity
        require_person(who)

        def claim():
            with store.transaction() as c:
                row = c.execute("SELECT * FROM task_manager_messages WHERE id=? AND actor=? AND card_json IS NOT NULL",
                                (mid, who.actor)).fetchone()
                if not row:
                    raise Problem("not_found", "Card not found", 404)
                if row["state"] != "pending":
                    raise Problem("state", "This card is already " + row["state"], 409)
                if row["created"] < H.shift(H.now(), hours=-CARD_TTL_H):
                    c.execute("UPDATE task_manager_messages SET state='expired',decided_at=? WHERE id=?", (H.now(), mid))
                    return None
                c.execute("UPDATE task_manager_messages SET state='running',decided_at=? WHERE id=?", (H.now(), mid))
                card = json.loads(row["card_json"])
                H.event(c, who.actor, "task_manager.confirmed", mid, {"kind": card["kind"], "path": card["path"]})
                return card
        card = await asyncio.to_thread(claim)
        if card is None:
            raise Problem("expired", "This card is more than a day old; ask again", 409)
        api = Internal(request)
        follow, ok, said = [], False, ""
        try:
            if not replayable(card):
                raise Problem("operation", "This card is not one the Task Manager may run", 422)
            sent = card["body"]
            if card["kind"] == "save_tab":
                # Read the tabs as they are now and add this one, so a change made since is kept.
                now = await api.call("GET", card["path"])
                if now.status_code != 200:
                    raise Problem("forbidden", outcome(now)["error"], now.status_code)
                tabs = [{k: t[k] for k in ("builtin", "name", "show_count", "conditions") if k in t}
                        for t in now.json()["tabs"]]
                sent = {"tabs": tabs + [sent["tab"]]}
            response = await api.call(card["method"], card["path"], body=sent, key="task-manager-" + mid)
            result = outcome(response)
            ok = result["error"] is None
            said = "Done." if ok else "That did not go through: " + str(result["error"])
            if card["kind"] == "fix" and ok:
                done = response.json()
                refused = [r for r in done.get("results") or [] if not r.get("ok")]
                said = f"Fixed {done.get('fixed', 0)}." + (f" {len(refused)} refused: {refused[0]['detail']}" if refused else "")
            if card["kind"] == "create_task" and not ok:
                error = (response.json().get("error") or {}) if response.headers.get("content-type", "").startswith("application/json") else {}
                match = ((error.get("task_check") or {}).get("match") or {}).get("id")
                if error.get("code") == "task_check" and match:
                    # A check blocked it: offer to comment on the open task instead, as the person.
                    said = "Not created: " + str(error.get("detail") or "a task check blocked it")
                    text = ("Also asked for: " + card["body"]["title"] + ("\n\n" + card["body"]["body"] if card["body"].get("body") else ""))
                    follow.append({"kind": "comment", "summary": f"Comment on the open task instead: \"{text[:200]}\"",
                                   "method": "POST", "path": f"/api/v2/tasks/{match}/comments", "body": {"text": text[:BODY]}})
        except Problem as exc:
            said = "That did not go through: " + exc.detail

        def settle():
            with store.transaction() as c:
                c.execute("UPDATE task_manager_messages SET state=?,result=? WHERE id=?",
                          ("done" if ok else "failed", said[:BODY], mid))
                ids = [mid] + [add(c, who.actor, "bot", f["summary"], f) for f in follow]
                return {"ok": ok, "messages": messages(c, who.actor, ids)}
        return await asyncio.to_thread(settle)
