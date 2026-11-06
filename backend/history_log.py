"""Settings > History > Log: what every teammate did, asked and was stopped from doing, newest first
(`GET /api/v2/history`), and the same list as CSV for the owner and the Admins (`GET /api/v2/history.csv`).

Nothing new is recorded. Each kind is read from where Tico already keeps it: runs from `attempts`, task status changes
from `history_events`, approvals asked and decided from `approvals`, sends the send switch kept as drafts
(`automation.send_off`), credential reveals and permission changes from `events`, and people's answers to bots'
questions (decisions) from `messages`.

Every read goes through the reader's own SQL connection (backend/sql.py): each table there is the view of it that
`POST /api/v2/sql` gives the same person, so the list never holds a row its reader could not already query. A filter
only narrows it: by teammate or team (`bot=`, `person=`, `team=`, as Replay takes them; a team includes the groups
nested under it), matching who acted or whom the row is about, and by `kind=`. A credential event on a key of a tool
the reader may not see (backend/api_tools.py `visible`) is not in it at all, though the reader made it.
"""

import csv
import io
import json
import sqlite3
import time

from fastapi import Request
from fastapi.responses import Response

from . import replay
from . import sql
from . import sources
from .store import H, Problem

LIMIT = 100
CSV_MAX = 5000
LIST_BYTES = 262_144                     # as the audit trail's pages
TEXT_MAX = 160
KINDS = {"run": "Run", "task": "Task", "approval.asked": "Approval asked", "approval.granted": "Approval granted",
         "approval.denied": "Approval denied", "outbound.blocked": "Outbound blocked",
         "credential.revealed": "Credential revealed", "permission.changed": "Permission change",
         "decision": "Decision"}
REVEALS = {"credential.revealed": "revealed", "credential.sent_to_computer": "sent to its computer"}
# What each permission event changed, in words.
PERMISSIONS = {"person.access_edited": "access", "bot.access_changed": "who sees its work", "bot.owners_changed": "owners",
               "person.access_changed": "who sees them", "group.access_changed": "who sees the group",
               "bot.co_owners_changed": "co-owners", "access.limits_updated": "bot limit per member",
               "access.allow_updated": "who may sign in", "access.rules_updated": "team rules",
               "credential.granted": "granted", "credential.revoked": "revoked"}
SEND_OFF = "automation.send_off"


def clip(value, n=TEXT_MAX):
    value = " ".join(str(value or "").split())
    return value if len(value) <= n else value[: n - 1].rstrip() + "…"


def kinds_of(value):
    """The kinds a `kind=` asks for (comma-separated), every kind when empty."""
    wanted = [k.strip() for k in str(value or "").split(",") if k.strip()]
    unknown = [k for k in wanted if k not in KINDS]
    if unknown:
        raise Problem("kind", "kind is one or more of " + ", ".join(KINDS), 422)
    return wanted or list(KINDS)


def cursor_of(before):
    at, _, rid = str(before or "").partition("|")
    if before and not rid:
        raise Problem("before", "before is the `next` of an earlier page", 422)
    return at, rid


def moment(value):
    """A `from` or `to` as a stored timestamp; "" when not given."""
    if not value:
        return ""
    at = H.parse_ts(value)
    if not at or at.tzinfo is None:
        raise Problem("range", "from and to are ISO-8601 times with a zone", 422)
    try:
        return replay.stamp(at)
    except OverflowError:
        raise Problem("range", "from and to are out of range", 422) from None


def _sources(kinds):
    """(kind, SELECT, its time column, its actor and its about expressions) for each kind asked for. Each SELECT names
    `rid` (unique across kinds, the cursor's tie-break), `at`, `actor` and `about`; `read` adds the window, the cursor
    and the scope over its own columns."""
    out = []

    def add(kind, select, at, actor, about):
        out.append((kind, select, at, actor, about))

    if "run" in kinds:
        add("run", "SELECT 'run:'||t.id AS rid, t.created_at AS at, 'bot:'||t.bot_id AS actor, NULL AS about, t.id AS ref, "
                   "substr(t.exit,1,:text_n) AS exit, t.task_id AS task_id, substr(k.title,1,:text_n) AS title FROM attempts t "
                   "LEFT JOIN tasks k ON k.id=t.task_id WHERE (t.job_id IS NULL OR t.trigger IS NOT NULL)",
            "t.created_at", "'bot:'||t.bot_id", None)
    if "task" in kinds:
        add("task", "SELECT 'task:'||e.id AS rid, e.created_at AS at, e.actor_id AS actor, k.owner_id AS about, k.id AS task_id, "
                    "substr(k.title,1,:text_n) AS title, substr(e.old,1,:text_n) AS old, substr(e.new,1,:text_n) AS new "
                    "FROM history_events e JOIN tasks k ON k.id=e.subject_id "
                    "WHERE e.subject_type='task' AND e.kind='status' AND e.created_at IS NOT NULL",
            "e.created_at", "e.actor_id", "k.owner_id")
    if "approval.asked" in kinds:
        add("approval.asked", "SELECT 'approval.asked:'||a.id AS rid, a.created_at AS at, a.requested_by_id AS actor, "
                              "a.decision_owner AS about, substr(a.kind,1,:text_n) AS what, "
                              "substr(a.decision,1,:text_n) AS decision, a.task_id AS task_id, "
                              "substr(k.title,1,:text_n) AS title FROM approvals a LEFT JOIN tasks k ON k.id=a.task_id "
                              "WHERE a.created_at IS NOT NULL", "a.created_at", "a.requested_by_id", "a.decision_owner")
    for kind, decision in (("approval.granted", "approved"), ("approval.denied", "declined")):
        if kind in kinds:
            add(kind, f"SELECT '{kind}:'||a.id AS rid, a.decided_at AS at, a.decided_by_id AS actor, "
                      "a.requested_by_id AS about, substr(a.kind,1,:text_n) AS what, "
                      "substr(a.decision,1,:text_n) AS decision, a.task_id AS task_id, "
                      "substr(k.title,1,:text_n) AS title FROM approvals a LEFT JOIN tasks k ON k.id=a.task_id "
                      f"WHERE a.decision='{decision}' AND a.decided_at IS NOT NULL",
                "a.decided_at", "a.decided_by_id", "a.requested_by_id")
    # A key of a tool the reader may not see (backend/api_tools.py `visible`) has no rows here at all: `:hidden`.
    unseen = "coalesce(e.target,'') NOT IN (SELECT value FROM json_each(:hidden))"
    events = {"outbound.blocked": f"e.action='{SEND_OFF}'",
              "credential.revealed": "e.action IN (" + ",".join(f"'{a}'" for a in REVEALS) + f") AND {unseen}",
              "permission.changed": "e.action IN (" + ",".join(f"'{a}'" for a in PERMISSIONS) + ") AND "
                                    f"(e.action NOT IN ('credential.granted','credential.revoked') OR {unseen})"}
    for kind, which in events.items():
        if kind in kinds:
            about = ("CASE WHEN e.action IN ('credential.granted','credential.revoked') "
                     "THEN json_extract(e.detail,'$.subject') "
                     "WHEN e.action LIKE 'bot.%' THEN 'bot:'||e.target "
                     "WHEN e.action IN ('person.access_edited','person.access_changed') THEN 'human:'||e.target "
                     "WHEN e.action='group.access_changed' THEN 'group:'||e.target "
                     "WHEN json_extract(e.detail,'$.bot') IS NOT NULL THEN 'bot:'||json_extract(e.detail,'$.bot') END")
            # Read by action (the store's events_action_target index), never by scanning events since a time.
            add(kind, f"SELECT '{kind}:'||e.id AS rid, e.created_at AS at, e.actor AS actor, {about} AS about, "
                      "e.action AS action, e.target AS target, substr(json_extract(e.detail,'$.subject'),1,:text_n) AS subject_actor, "
                      "CASE WHEN e.action='person.access_edited' THEN (SELECT substr(group_concat(key, ', '),1,:text_n) "
                      "FROM json_each(e.detail) WHERE key NOT IN ('via','delegation')) END AS changed FROM events e "
                      f"WHERE {which}", "e.created_at", "e.actor", about)
    if "decision" in kinds:
        # A person's answer to a question: the answers of people only, never a bot's reply to a person's ask.
        add("decision", "SELECT 'decision:'||m.id AS rid, m.created_at AS at, m.sender_id AS actor, m.recipient_id AS about, "
                        "substr(m.body,1,:text_n) AS answer, substr(q.body,1,:text_n) AS asked, k.id AS task_id, "
                        "substr(k.title,1,:text_n) AS title FROM messages m "
                        "LEFT JOIN messages q ON q.id=m.in_reply_to "
                        "LEFT JOIN tasks k ON k.id=coalesce(json_extract(m.refs,'$.task'),json_extract(q.refs,'$.task')) "
                        "WHERE m.kind='answer' AND m.created_at IS NOT NULL "
                        "AND m.sender_id IN (SELECT value FROM json_each(:people))",
            "m.created_at", "m.sender_id", "m.recipient_id")
    return out


def _query(source, actors, first, last, before, limit, people, hidden=()):
    """Order a source by its indexed time and id; the prefixed id only breaks ties across sources. `hidden` is the keys
    whose credential events the reader never gets."""
    _, select, column, actor, about = source
    at, rid = cursor_of(before)
    where = []
    if first:
        where.append(f"{column}>=:first")
    if last:
        where.append(f"{column}<:last")
    if at:
        # The separate upper bound gives SQLite a range seek even across kinds with different id prefixes.
        where.append(f"{column}<=:at AND ({column}<:at OR rid<:rid)")
    if actors is not None:
        match = f"{actor} IN (SELECT value FROM json_each(:actors))"
        if about:
            match = f"({match} OR {about} IN (SELECT value FROM json_each(:actors)))"
        where.append(match)
    ident = column.rsplit(".", 1)[0] + ".id"
    statement = select + "".join(" AND " + w for w in where) + f" ORDER BY {column} DESC, {ident} DESC LIMIT :n"
    return statement, {"first": first, "last": last, "at": at, "rid": rid, "n": limit + 1, "text_n": TEXT_MAX + 1,
                       "actors": json.dumps(sorted(actors or ())), "people": json.dumps(people),
                       "hidden": json.dumps(sorted(hidden))}


def install(app, store, auth):
    def readable_credentials(c, who, ids):
        """The names of the credentials among `ids` this reader's Credentials list shows them (backend/credentials.py)."""
        from . import credentials as V
        vault = getattr(app.state, "vault", None)
        if not ids or vault is None or who.role not in ("human", "owner"):
            return {}
        from .api_tools import hidden_keys
        own = V.owned(c, who) - hidden_keys(c, who, vault.admins)     # never a key of a tool they may not see
        return {row["id"]: row["name"] for row in c.execute(
            f"SELECT id,substr(name,1,{TEXT_MAX + 1}) AS name FROM credentials WHERE id IN (%s)" % ",".join("?" * len(ids)), list(ids))
            if row["id"] in own or V.permitted(c, who, row["id"], vault.admins)}

    def read(c, who, kinds, actors, first, last, before, limit):
        """Up to `limit` rows newest first, and whether more were left (each source reads `limit + 1`)."""
        cursor_of(before)
        _, seconds = sql.LIMITS[who.role if who.role in sql.LIMITS else "human"]
        deadline = time.monotonic() + seconds
        people = ["human:" + row["id"] for row in c.execute("SELECT id FROM humans")]
        from .api_tools import hidden_keys
        vault = getattr(app.state, "vault", None)
        hidden = hidden_keys(c, who, vault.admins if vault is not None else (), auth)
        conn = sql.connect(store.settings.db_path, c, auth, who, seconds=seconds)
        rows = []
        try:
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
            for source in _sources(kinds):
                statement, args = _query(source, actors, first, last, before, limit, people, hidden)
                rows += [{**dict(row), "kind": source[0]} for row in conn.execute(statement, args)]
        except sqlite3.Error as exc:
            if time.monotonic() > deadline or "interrupted" in str(exc):
                raise Problem("timeout", f"History stopped after {seconds:g} s; filter it or page with before", 422) from exc
            raise
        finally:
            conn.close()
        rows.sort(key=lambda r: (r["at"], r["rid"]), reverse=True)
        return rows[:limit], len(rows) > limit

    def view(c, who, rows):
        """Each source row in the Log's one shape, and the names of everyone in them."""
        from . import automations
        creds = readable_credentials(c, who, {r["target"] for r in rows if r["kind"] in ("credential.revealed",)
                                              or r.get("action") in ("credential.granted", "credential.revoked")})
        autos = {r["target"] for r in rows if r["kind"] == "outbound.blocked"}
        autos = {row["id"]: row["name"] for row in c.execute(
            f"SELECT id,substr(name,1,{TEXT_MAX + 1}) AS name,owner_id,group_id FROM automations "
            "WHERE deleted_at IS NULL AND id IN (%s)" % ",".join("?" * len(autos)), list(autos))
            if automations.may_see(c, who, {"owner": row["owner_id"], "group": row["group_id"]})} if autos else {}
        bots = {row["id"]: row["display_name"] or row["slug"] for row in c.execute(
            f"SELECT id,slug,substr(display_name,1,{TEXT_MAX + 1}) AS display_name FROM bots")}
        people = {row["id"]: row["name"] or row["id"] for row in c.execute(
            f"SELECT id,substr(name,1,{TEXT_MAX + 1}) AS name FROM humans")}

        def name(actor):
            ident = H.actor_id(actor)
            if str(actor).startswith("bot:"):
                return bots.get(ident) or ident
            if str(actor).startswith("human:"):
                return people.get(ident) or ident
            return "Tico" if actor in (H.KEEPER, "system") else str(actor or "")

        out = []
        for r in rows:
            kind, task = r["kind"], ({"id": r["task_id"], "title": r["title"]} if r.get("task_id") and r.get("title") else None)
            item = {"id": r["rid"], "at": r["at"], "kind": kind, "actor": r["actor"], "about": r["about"] or None}
            link = "#/task/" + task["id"] if task else ""
            if kind == "run":
                subject = task["title"] if task else "No task"
                # attempts.exit is the outcome's word ("completed", "interrupted", …); older rows hold a process code.
                done = r["exit"] in ("completed", "0", 0)
                result = "running" if r["exit"] is None else "done" if done else str(r["exit"])
                link = "/api/runs/" + r["ref"] + "/log"
            elif kind == "task":
                subject = task["title"] if task else ""
                result = "created" if not r["old"] else f"{r['old']} → {r['new']}"
            elif kind.startswith("approval."):
                subject = f"{r['what']} · {task['title']}" if task else str(r["what"] or "")
                result = r["decision"] or "pending"
            elif kind == "outbound.blocked":
                subject = autos.get(r["target"]) or "An automation"
                result = "kept as a draft"
                link = "#/automations/" + r["target"]
            elif kind == "credential.revealed":
                subject = creds.get(r["target"]) or "A credential"
                result = REVEALS.get(r["action"], r["action"])
                link = "#/tools/keys?show=credentials"
            elif kind == "permission.changed":
                action, target = r["action"], r["target"] or ""
                if action in ("credential.granted", "credential.revoked"):
                    subject = creds.get(target) or "A credential"
                    result = PERMISSIONS[action] + (" to " if action == "credential.granted" else " from ") + name(r["subject_actor"])
                    link = "#/tools/keys?show=credentials"
                elif action.startswith("bot."):
                    subject, result, link = name("bot:" + target), PERMISSIONS[action], "#/bot/" + target
                elif action == "person.access_edited":
                    changed = r["changed"]
                    subject, result, link = name("human:" + target), PERMISSIONS[action] + (f": {changed}" if changed else ""), "#/person/" + target
                elif action == "person.access_changed":
                    subject, result, link = name("human:" + target), PERMISSIONS[action], "#/person/" + target
                elif action == "group.access_changed":
                    subject, result, link = target, PERMISSIONS[action], "#/team/" + target
                else:
                    subject, result, link = "Org", PERMISSIONS.get(action, action), "#/settings/people"
            else:   # decision
                subject, result = clip(r["asked"]) or "A question", clip(r["answer"])
                if not task and str(r["about"] or "").startswith("bot:"):
                    link = "#/bot/" + H.actor_id(r["about"])
            item.update(subject=clip(subject), result=clip(result), link=link)
            if task:
                item["task"] = task
            out.append(item)
        names = {a: name(a) for item in out for a in (item["actor"], item["about"]) if a}
        lines = sources.lookup(c, sources.actors_of(out))
        for item in out:
            item["source"] = sources.source_of(lines, item)
        return out, names

    def params(request):
        q = request.query_params
        kinds = kinds_of(q.get("kind"))
        kind, ident = replay.scope_of(q.get("bot"), q.get("person"), q.get("team"))
        return kinds, kind, ident, moment(q.get("from")), moment(q.get("to"))

    def actors_for(c, who, kind, ident):
        if kind == "bot":
            ident = (H.resolve_bot(c, ident) or {}).get("id", ident)
        return replay.members(c, auth, who, kind, ident)

    @app.get("/api/v2/history")
    def history(request: Request, before: str | None = None, limit: int = LIMIT):
        """The Log, newest first: `limit` rows (at most 200) and `next`, the `before` that continues it, under 256 KB a
        page. Filters: `kind` (comma-separated), one of `bot`, `person` or `team`, and `from`/`to`."""
        who = request.state.identity
        auth.domain(who)
        kinds, kind, ident, first, last = params(request)
        limit = max(1, min(int(limit or LIMIT), 200))
        with store.read() as c:
            actors = actors_for(c, who, kind, ident)
            rows, more = read(c, who, kinds, actors, first, last, before, limit)
            items, names = view(c, who, rows)
        shown, used = [], 256
        for item in items:
            size = len(json.dumps(item, default=str).encode()) + 1
            if shown and used + size > LIST_BYTES:
                more = True
                break
            used += size
            shown.append(item)
        last_row = shown[-1] if more and shown else None
        named = {a for item in shown for a in (item["actor"], item["about"]) if a}
        return {"items": shown, "names": {a: n for a, n in names.items() if a in named},
                "next": last_row and last_row["at"] + "|" + last_row["id"], "kinds": KINDS}

    @app.get("/api/v2/history.csv")
    def history_csv(request: Request, before: str | None = None):
        """The same rows as CSV, up to CSV_MAX; `X-Next` is the `before` that continues it. The owner and the Admins."""
        who = request.state.identity
        if who.role not in ("owner", "human") or not auth.bot_admin(who):
            raise Problem("forbidden", "The owner and the Admins export History", 403)
        kinds, kind, ident, first, last = params(request)
        with store.read() as c:
            actors = actors_for(c, who, kind, ident)
            rows, more = read(c, who, kinds, actors, first, last, before, CSV_MAX)
            items, names = view(c, who, rows)
        base = store.settings.public_url.rstrip("/")
        buffer = io.StringIO()
        out = csv.writer(buffer)
        # A cell a spreadsheet would run as a formula starts with a quote mark instead.
        safe = lambda v: "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v  # noqa: E731
        out.writerow(["time", "actor", "kind", "subject", "result", "link"])
        for item in items:
            link = item["link"] and (base + ("/" if item["link"].startswith("#") else "") + item["link"])
            out.writerow([item["at"], safe(names.get(item["actor"], item["actor"] or "")), KINDS[item["kind"]],
                          safe(item["subject"]), safe(item["result"]), link])
        headers = {"X-Content-Type-Options": "nosniff",
                   "Content-Disposition": 'attachment; filename="tico-history.csv"'}
        if more and items:
            headers["X-Next"] = items[-1]["at"] + "|" + items[-1]["id"]
        return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
