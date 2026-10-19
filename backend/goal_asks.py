"""What the Goals page needs from a person, as tasks the Goal Manager asks (docs/goals-and-kpis.md, "Needs you").

Three things wait on a person: a KPI off track (red) on a goal they own, a KPI they keep whose reading is out of
date (stale or missing), and a proposal to confirm or reject. Each is one task, requested by the Goal Manager and
owned by the person who must act, so it shows in Tasks and Decide like any other ask. One per KPI or proposal,
kind and person (`goal_asks`), never two. `sync` files what is new and closes what is resolved: a fresh reading,
a KPI back on track, a proposal decided, or the thing now someone else's. A task the person closed or declined
while the cause stands is left alone, and asked again only once the cause has cleared and come back.

A company goal or KPI is the environment owner's to act on. A bot's red or stale KPI is the bot's own work (the
Goal Manager's pass asks it), so no task is filed for it; a proposal about a bot's goal or KPI goes to the nearest
person above the bot on the org chart, as only a person decides one.
"""

import logging

from .batch_work import isolated
from . import ceo_bot
from . import goals as G
from . import kpis as K
from . import views
from .store import H

log = logging.getLogger("tico.goal_asks")

KINDS = ("kpi_red", "kpi_stale", "proposal")


def _org(conn):
    archived = {row["slug"] for row in H.bots(conn) if row.get("state") == "archived"}
    return views.roster(conn), views.entries(conn), archived


def _person(conn, actor, owner_id, org):
    """The person who acts for an owner: a person themselves, the environment owner for `company`, and for a bot
    the nearest person above it (else the environment owner). None when nobody on the roster can."""
    if actor == G.COMPANY or not actor:
        return H.human_actor(owner_id) if owner_id else None
    if H.is_human(actor):
        return actor if H.human(conn, H.actor_id(actor)) else None
    for boss in G.above(actor, *org):
        if H.is_human(boss) and H.human(conn, H.actor_id(boss)):
            return boss
    return H.human_actor(owner_id) if owner_id else None


def _acts_for(actor, owner_id):
    """Who acts on a red or stale KPI: the owner, or the environment owner for a company one."""
    if actor == G.COMPANY:
        return H.human_actor(owner_id) if owner_id else None
    return actor


def wanted(conn, owner_id, at=None):
    """Everything that waits on someone now: [{kind, ref, owner, ...the item the Goals page shows}]. `owner` is
    the actor who must act (a bot only for its own red or stale KPI). `owner_id` is the environment owner's
    roster id."""
    org = None
    items = []
    live = G.goals(conn, live_only=True)
    for goal_id, rows in K.goal_views(conn, [g["id"] for g in live], at).items():
        goal = next(g for g in live if g["id"] == goal_id)
        who = _acts_for(goal["owner"], owner_id)
        for k in rows:
            if who and k["status"] == "red":
                items.append({"kind": "kpi_red", "ref": k["id"], "owner": who, "goal_id": goal_id,
                              "goal_title": goal["title"], "kpi_id": k["id"], "kpi_name": k["name"],
                              "reason": k["reason"]})
    owned = [dict(r) for r in conn.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created")]
    data = K.effective_many(conn, [r["id"] for r in owned])
    for record in owned:
        who = _acts_for(record["owner"], owner_id)
        if K.auto(record["id"]):
            continue
        state = K.assess(record, None, data.get(record["id"], []), at)
        if who and state["freshness"] != "fresh":
            items.append({"kind": "kpi_stale", "ref": record["id"], "owner": who, "kpi_id": record["id"],
                          "kpi_name": record["name"], "freshness": state["freshness"], "reason": state["reason"]})
    for p in G.proposals(conn, "pending"):
        goal = G.goal(conn, p["goal_id"]) if p["goal_id"] else None
        record = K.kpi(conn, p["kpi_id"]) if p["kpi_id"] else None
        if p["kind"] == "kpi_definition":
            decides = record and record["owner"]
        else:
            decides = (goal and goal["owner"]) or (record and record["owner"])
        org = org or _org(conn)
        who = _person(conn, decides, owner_id, org)
        if who:
            items.append({"kind": "proposal", "ref": p["id"], "owner": who, "proposal": p,
                          "goal_title": goal and goal["title"], "kpi_name": record and record["name"]})
    # A red KPI used by two of one person's goals is one ask.
    seen, out = set(), []
    for item in items:
        key = (item["kind"], item["ref"], item["owner"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _change(p, kpi_name):
    """The proposed change in a few words."""
    payload = p.get("payload") or {}
    if p["kind"] == "goal_wording":
        parts = [f'title "{payload["title"]}"' if payload.get("title") else "", "a new description" if payload.get("body") else ""]
        return "new " + " and ".join(x for x in parts if x)
    if p["kind"] == "kpi_definition":
        return "change " + ", ".join(f"{k.replace('_', ' ')} to {v!r}" for k, v in K.clean(payload).items())
    if p["kind"] == "kpi_target":
        return "target " + (K.target_label({"unit": ""}, payload) or payload.get("kind") or "none")
    if p["kind"] == "goal_kpi":
        name = (payload.get("kpi") or {}).get("name") or kpi_name or "an existing KPI"
        return f"measure it by {name}"
    if p["kind"] == "flag":
        return f"flagged as {payload.get('issue')}"
    return p["kind"]


def _ask(item, manager="Goal Manager"):
    """The task's title and body: the ask first, in plain words. `manager` is the bot's name (backend/ceo_bot.py)."""
    name, goal = item.get("kpi_name") or "", item.get("goal_title") or ""
    closes = "This closes by itself once "
    if item["kind"] == "kpi_stale":
        first = item["reason"].endswith("has no reading yet")
        title = f"Log the first {name} reading" if first else f"Log a new {name} reading"
        body = (f"{title}: {item['reason']}. Open {name} on the Goals page and add the latest number, or tell the "
                f"{manager} on this task where it comes from. {closes}a fresh reading is logged.")
        return title, body
    if item["kind"] == "kpi_red":
        title = f"Check why {name} is off track on {goal}"
        body = (f"{title}: {item['reason']}. Add a check-in on the goal saying what happens next, or propose a "
                f"different target on the Goals page. {closes}{name} is back on track.")
        return title, body
    p = item["proposal"]
    title = {"goal_wording": f"Confirm or reject new wording for {goal}",
             "goal_kpi": f"Confirm or reject a new KPI for {goal}",
             "kpi_definition": f"Confirm or reject a change to how {name} is defined",
             "kpi_target": f"Confirm or reject a new {name} target on {goal}",
             "flag": f"Confirm or reject a flag on {goal}"}.get(p["kind"], "Confirm or reject a goal proposal")
    said = f" because {p['reason'].rstrip('.')}" if p.get("reason") else ""
    body = (f"{title}. {H.actor_id(p['proposed_by'])} proposed it{said}: {_change(p, name)}. Confirm or reject it "
            f"under Needs you on the Goals page. {closes}it is decided.")
    return title, body


def _resolved(conn, kind, ref):
    if kind == "kpi_stale":
        return "Resolved: a fresh reading is logged (or the KPI moved on)."
    if kind == "kpi_red":
        return "Resolved: the KPI is no longer off track on your goal."
    row = G.proposal(conn, ref)
    if row and row["status"] != "pending":
        return f"Resolved: the proposal was {row['status']} by {H.actor_id(row['decided_by'] or '')}.".replace(" by .", ".")
    return "Resolved: nothing is waiting on you here any more."


def _free_title(conn, owner, title):
    """task_create refuses a second live task with the same requester, owner and title; two different proposals
    can word the same, so the second one is numbered instead of refused."""
    taken = {r[0] for r in conn.execute(
        f"SELECT title FROM tasks WHERE requester=? AND owner=? AND status IN ({','.join('?' * len(H.LIVE_STATUSES))})",
        (G.AUTO_ACTOR, owner, *H.LIVE_STATUSES))}
    out, n = title[:290], 2
    while out in taken:
        out, n = f"{title[:290]} ({n})", n + 1
    return out


def manager_ready(conn):
    row = H.bot(conn, G.GOAL_MANAGER)
    return bool(row) and row.get("state") not in ("archived", "quarantined")


def sync(conn, owner_id, at=None):
    """File the asks that are new and close the ones that are resolved. Returns {created, closed}. Never raises
    for one row: a failure is recorded and the rest go on (backend/batch_work.py)."""
    if not manager_ready(conn):
        return {"created": [], "closed": []}
    # Only someone on the roster: a refused ask counts against the Goal Manager (rule 8), every hour.
    want = {(i["kind"], i["ref"], i["owner"]): i for i in wanted(conn, owner_id, at)
            if H.is_human(i["owner"]) and H.human(conn, H.actor_id(i["owner"]))}
    have = {(r["kind"], r["ref"], r["owner"]): dict(r) for r in conn.execute("SELECT * FROM goal_asks")}
    created, closed = [], []
    moved = {(kind, ref): owner for kind, ref, owner in want}
    for key, row in list(have.items()):
        task = H.task(conn, row["task_id"])
        # Marked done while its cause stands: accepted and asked again, so it does not sit done and hidden.
        again = key in want and task and task["status"] == "done"
        if key in want and not again:
            continue
        with isolated(conn, "goal_ask_close", row["task_id"]):
            if task and task["status"] in H.LIVE_STATUSES:
                if again:
                    note = f"Still needed: {want[key].get('reason') or 'nothing has changed yet'}. Asked again in a new task."
                elif moved.get(key[:2]):
                    note = f"Now asked of {H.actor_id(moved[key[:2]])}, who owns it now."
                else:
                    note = _resolved(conn, row["kind"], row["ref"])
                H.task_close(conn, G.AUTO_ACTOR, task["id"], note=note, quiet=True)
                closed.append(task["id"])
            conn.execute("DELETE FROM goal_asks WHERE kind=? AND ref=? AND owner=?", key)
            if again:
                del have[key]
    for key, item in want.items():
        if key in have:
            continue
        with isolated(conn, "goal_ask_file", ":".join(key)):
            title, body = _ask(item, ceo_bot.name(conn))
            if H.TITLE_LINT == "refuse" and H.lint_title(title):
                # A KPI name the title lint refuses would be refused, and counted against the Goal Manager, every hour.
                log.warning("Not asking %s: the title lint refuses its KPI's name", item["owner"])
                continue
            made = H.task_create(conn, G.AUTO_ACTOR, _free_title(conn, item["owner"], title), body, item["owner"],
                                 lint=False, lane="company")
            conn.execute("INSERT INTO goal_asks (kind, ref, owner, task_id, created) VALUES (?,?,?,?,?)",
                         (*key, made["id"], H.now()))
            created.append(made["id"])
    return {"created": created, "closed": closed}


def for_person(conn, actor, items):
    """The Goals page's Needs you for one caller: each item carries the task that asks it (`task`), and an item
    the person already closed or declined while its cause stands is left out, as the task is."""
    rows = {(r["kind"], r["ref"]): r["task_id"] for r in conn.execute("SELECT * FROM goal_asks WHERE owner=?", (actor,))}
    out = []
    for item in items:
        task_id = rows.get((item["kind"], item["ref"]))
        task = H.task(conn, task_id) if task_id else None
        if task_id and not (task and task["status"] in H.ACTIVE_STATUSES):
            continue
        view = {k: v for k, v in item.items() if k not in ("ref", "owner")}
        if task:
            view["task"] = {"id": task["id"], "title": task["title"], "status": task["status"]}
        out.append(view)
    return out
