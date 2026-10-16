"""The Goal Manager's asks as ordinary tasks (docs/goals-and-kpis.md, "Needs you").

What a goal or a KPI waits on a person or a bot for is a task from the Goal Manager, owned by whoever
must act, so it shows in Tasks and Decide like any other work and the Goals page's Needs you box is
those same tasks:

- `kpi_stale`: a KPI with no fresh reading. Its owner logs one.
- `kpi_red`: a KPI that is off track on a live goal. The goal's owner says what is happening.
- `proposal`: a definition, target or wording change somebody proposed. Its natural decider confirms or
  rejects it: the goal's owner (the KPI's owner for a definition change), and for a bot's goal or KPI the
  nearest person above the bot, since only a person decides.

Each condition has one key: kind and what it is about, then `|` and who acts. `sync` reconciles every key at once:
a key that holds and has no task gets one, a key that no longer holds has its open task marked done by
Tico with a line saying why. Which task a key has is the `goal_manager.task` event (target = the key);
`goal_manager.resolved` closes the key, so the same condition coming back later is a new task. A task
its owner finished while the condition still holds is not filed again until the condition clears:
they have answered it.

`sync` runs inside the transaction of the write that changed things (a reading, a proposal, an archive)
and after every status pass, so it is idempotent and reads in a few batched queries.
"""

from .batch_work import isolated
from . import goals as G
from . import hubdb as H
from . import kpis as K
from . import task_snooze as TS

FILED = "goal_manager.task"
RESOLVED = "goal_manager.resolved"
KINDS = ("kpi_stale", "kpi_red", "proposal")


def company_owner(conn, owner_id=""):
    """Who acts for `company`: the environment's owner (Auth.owner_id), else the roster's default person."""
    pid = owner_id or H.default_human(conn)
    return H.human_actor(pid) if pid and H.human(conn, pid) else None


def _clip(text, n=60):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _ask(text, plain):
    """The ask a person's card leads with (views.person_ask): with the KPI's or goal's name when that passes the
    person-ask lint (a name like `mrr_v2` reads as a field), else the same ask without it."""
    return plain if H.lint_person_ask(text) else text


class _Shape:
    """The org chart, read once and only when a bot's proposal needs the person above it."""

    def __init__(self, conn):
        self.conn, self._shape = conn, None

    def person_above(self, actor):
        if self._shape is None:
            from . import views
            archived = {row["slug"] for row in H.bots(self.conn) if row.get("state") == "archived"}
            self._shape = (views.roster(self.conn), views.entries(self.conn), archived)
        return next((a for a in G.above(actor, *self._shape) if H.is_human(a)), None)


def _actor(conn, owner, boss):
    """The actor who acts for an owner string: `company` is the company owner. (actor, hold): hold is a bot that is
    not active now, whose task waits rather than being filed or resolved."""
    if owner == G.COMPANY:
        return boss, False
    if H.is_bot(owner):
        row = H.bot(conn, H.actor_id(owner))
        return (owner, row["state"] != "active") if row and row["state"] != "archived" else (None, False)
    if H.is_human(owner) and H.human(conn, H.actor_id(owner)):
        return owner, False
    return None, False


def _decider(conn, proposal, goal, record, boss, shape):
    """The person who naturally decides a proposal: whoever owns what changes (decider() in goal_routes.py
    allows anyone above them too). A bot's goal or KPI goes to the nearest person above the bot."""
    if proposal["kind"] == "kpi_definition" or not goal:
        owner = (record or {}).get("owner")
    else:
        owner = goal["owner"]
    if not owner:
        return None
    if owner == G.COMPANY:
        return boss
    if H.is_bot(owner):
        return shape.person_above(owner) or boss
    return owner if H.is_human(owner) and H.human(conn, H.actor_id(owner)) else None


def _proposal_title(p, goal, record):
    kpi_name, goal_title = _clip((record or {}).get("name")), _clip((goal or {}).get("title"))
    if p["kind"] in ("kpi_definition", "kpi_target") and kpi_name:
        return f"Confirm the change to {kpi_name}"
    if p["kind"] == "goal_kpi":
        return f"Confirm the new KPI for {goal_title}"
    if p["kind"] == "flag":
        return f"Review the Goal Manager's flag on {goal_title}"
    return f"Confirm the new wording of {goal_title}"


def _page(goal_id=None):
    return "Open it on the Goals page: #/goals" + (f"/{goal_id}" if goal_id else "")


def wanted(conn, boss, at=None):
    """{key: item} for every condition that waits on someone now. `boss` acts for company goals and KPIs."""
    out, shape = {}, _Shape(conn)
    live = G.goals(conn, live_only=True)
    by_id = {g["id"]: g for g in live}
    for goal_id, views in K.goal_views(conn, list(by_id), at).items():
        goal = by_id[goal_id]
        owner, hold = _actor(conn, goal["owner"], boss)
        for k in views:
            if k["status"] != "red" or not owner:
                continue
            name, title = _clip(k["name"]), _clip(goal["title"])
            out[f"kpi_red:{goal_id}:{k['id']}|{owner}"] = {
                "kind": "kpi_red", "owner": owner, "hold": hold, "goal_id": goal_id, "goal_title": goal["title"],
                "goal_owner": goal["owner"], "kpi_id": k["id"], "kpi_name": k["name"], "reason": k["reason"],
                "title": f"{name} is off track on {title}: say what's happening",
                "ask": _ask(f"Say in a check-in on {title} what is happening with {name} and what you will do about it.",
                            "Say in a check-in on this goal what is happening with this KPI and what you will do about it.")}
    stored = H._rows(conn.execute("SELECT * FROM kpis WHERE archived_at IS NULL ORDER BY created"))
    data = K.effective_many(conn, [r["id"] for r in stored])
    for record in stored:
        state = K.assess(record, None, data.get(record["id"], []), at)
        if state["freshness"] == "fresh":
            continue
        owner, hold = _actor(conn, record.get("owner"), boss)
        if not owner:
            continue
        name = _clip(record["name"])
        out[f"kpi_stale:{record['id']}|{owner}"] = {
            "kind": "kpi_stale", "owner": owner, "hold": hold, "kpi_id": record["id"], "kpi_name": record["name"],
            "freshness": state["freshness"], "reason": state["reason"],
            "title": f"Log a new reading for {name}",
            "ask": _ask(f"Log a new reading for {name}, or say why there is none.",
                        "Log a new reading for this KPI, or say why there is none.")}
    for p in G.proposals(conn, "pending"):
        goal = G.goal(conn, p["goal_id"]) if p["goal_id"] else None
        record = K.kpi(conn, p["kpi_id"]) if p["kpi_id"] else None
        owner = _decider(conn, p, goal, record, boss, shape)
        if not owner:
            continue
        title = _proposal_title(p, goal, record)
        out[f"proposal:{p['id']}|{owner}"] = {
            "kind": "proposal", "owner": owner, "hold": False, "proposal_id": p["id"], "goal_id": p["goal_id"],
            "goal_title": goal and goal["title"], "kpi_id": p["kpi_id"], "kpi_name": record and record["name"],
            "reason": p["reason"], "title": title,
            "ask": "Confirm or reject the proposed change on the Goals page."}
    return out


def _body(item):
    reason = str(item.get("reason") or "").strip()
    parts = [item["ask"], reason and ("Why: " + reason if item["kind"] == "proposal" else reason),
             _page(item.get("goal_id"))]
    return "\n\n".join(p for p in parts if p)


def filed(conn):
    """{key: (action, detail)}: the newest filing or resolution of every key."""
    rows = conn.execute("SELECT target, action, detail_json, max(rowid) FROM events WHERE actor=? AND action IN (?,?) "
                        "GROUP BY target", (G.AUTO_ACTOR, FILED, RESOLVED))
    return {r["target"]: (r["action"], H._json(r["detail_json"], {}) or {}) for r in rows}


def _why(conn, key, detail, now_with):
    """The line a task gets when Tico marks it done. `now_with` is who the same condition waits on now, if anyone."""
    kind, name = key.split(":", 1)[0], detail.get("kpi_name") or "The KPI"
    if now_with:
        return f"This is now {H.actor_id(now_with)}'s to do."
    if kind == "proposal":
        p = G.proposal(conn, detail.get("proposal_id"))
        return f"The proposal was {p['status']}." if p and p["status"] != "pending" else "The proposal was withdrawn."
    record = K.kpi(conn, detail["kpi_id"]) if detail.get("kpi_id") else None
    if not record or record.get("archived_at"):
        return f"{name} was archived."
    goal = G.goal(conn, detail["goal_id"]) if detail.get("goal_id") else None
    if kind == "kpi_red" and (not goal or goal["status"] in ("done", "dropped")):
        return "The goal is no longer live."
    return f"{name} is no longer off track." if kind == "kpi_red" else f"{name} has a fresh reading."


def sync(conn, boss, at=None):
    """File, keep or finish the Goal Manager's tasks so they match `wanted`. Returns {filed, resolved}."""
    want, have = wanted(conn, boss, at), filed(conn)
    gm = H.bot(conn, G.GOAL_MANAGER)
    requester = G.AUTO_ACTOR if gm and gm["state"] not in ("archived", "quarantined") else H.KEEPER
    made, done = [], []
    waits_on = {key.split("|")[0]: item["owner"] for key, item in want.items()}
    for key, (action, detail) in have.items():
        if action != FILED or key in want:
            continue
        with isolated(conn, "goal_task", key):
            row = H.task(conn, detail.get("task"))
            if row and row["status"] in H.ACTIVE_STATUSES:
                why = _why(conn, key, detail, waits_on.get(key.split("|")[0]))
                H.task_update(conn, H.KEEPER, row["id"], status="done", note=why, quiet=True)
                done.append(row["id"])
            H.event(conn, G.AUTO_ACTOR, RESOLVED, key, {"task": detail.get("task")})
    for key, item in want.items():
        if item["hold"] or have.get(key, (None,))[0] == FILED:
            continue
        with isolated(conn, "goal_task", key):
            # The same ask already open (filed before this key existed, or by hand) is adopted, not repeated.
            same = H._one(conn, f"SELECT id FROM tasks WHERE requester=? AND owner=? AND title=? AND status IN "
                                f"({','.join('?' * len(H.ACTIVE_STATUSES))})",
                          (requester, item["owner"], item["title"], *H.ACTIVE_STATUSES))
            task_id = same["id"] if same else H.task_create(
                conn, requester, item["title"], _body(item), item["owner"],
                goal_id=item.get("goal_id") if item["kind"] != "kpi_stale" else None, lint=False)["id"]
            H.event(conn, G.AUTO_ACTOR, FILED, key, {
                "task": task_id, "kind": item["kind"], "owner": item["owner"],
                **{k: item.get(k) for k in ("goal_id", "kpi_id", "proposal_id", "kpi_name", "goal_title")}})
            made.append(task_id)
    return {"filed": made, "resolved": done}


def safely(conn, boss, at=None):
    """`sync` where a failure must not undo the write that called it: rolled back alone and logged."""
    with isolated(conn, "goal_tasks", "sync"):
        return sync(conn, boss, at)


def open_for(conn, actor, boss, at=None):
    """The caller's open Goal Manager tasks as the Goals page's Needs you rows: each task with what it is about,
    its words worked out now (a stale KPI's age moves on) or, for a condition the next sync will close, as filed."""
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    rows = conn.execute(f"SELECT e.target, e.detail_json, t.* FROM events e JOIN tasks t "
                        f"ON t.id=json_extract(e.detail_json,'$.task') WHERE e.actor=? AND e.action=? AND t.owner=? "
                        f"AND t.status IN ({marks}) ORDER BY t.created", (G.AUTO_ACTOR, FILED, actor, *H.ACTIVE_STATUSES))
    found = [(r["target"], H._json(r["detail_json"], {}) or {}, dict(r)) for r in rows]
    found = [(key, detail, row) for key, detail, row in found if not TS.snoozed(row)]
    if not found:
        return []
    want, items, seen = wanted(conn, boss, at), [], set()
    for key, detail, row in found:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        now = want.get(key) or {}
        item = {"kind": detail.get("kind") or key.split(":", 1)[0], "task_id": row["id"], "title": row["title"],
                "status": row["status"],
                **{k: now.get(k, detail.get(k)) for k in ("goal_id", "goal_title", "kpi_id", "kpi_name", "proposal_id")},
                "reason": now.get("reason") or "", "freshness": now.get("freshness"),
                "goal_owner": now.get("goal_owner"), "owner": row["owner"]}
        items.append(item)
    return items
